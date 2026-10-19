"""Coordinator-observed receipts for writer-side generation steps; provenance only.

The coordinator runs a declared step itself in the ordinary no-network sandbox and
hashes the tool, inputs, outputs and sandbox profile. Writer files and statements
are never receipts. A receipt never makes a check pass: acceptance still comes only
from fresh sealed checks of the captured proposal.
"""

import hashlib
import json
import math
import os
import re
import shutil
import stat
import time
import uuid
from pathlib import Path, PurePosixPath

from .executor import execute
from .storage import digest, identity, read_json, write_json

STEP_SCHEMA = 'agent-ee.stage-step/1'
RECEIPT_SCHEMA = 'agent-ee.stage-receipt/1'
INDEX_SCHEMA = 'agent-ee.stage-receipt-index/1'
STAGES = ('scaffold', 'parts', 'schematic', 'placement', 'routing', 'verification', 'fab')
ADOPTABLE = ('completed', 'partial')
STATUSES = ('completed', 'partial', 'failed', 'timeout', 'cancelled', 'unavailable', 'refused')
STEP_NAME = re.compile(r'[a-z0-9][a-z0-9._-]{0,63}\Z')
SYSTEM_PREFIXES = ('/usr/', '/bin/', '/sbin/', '/lib/', '/lib64/')
RESERVED_MOUNTS = ('/candidate', '/work', '/app', '/usr', '/bin', '/sbin', '/lib', '/lib64',
                   '/etc', '/proc', '/dev', '/tmp', '/home', '/run')
EXECUTOR_FILES = ('stdout.log', 'stderr.log', 'console.log', 'execution.json', 'home')
RECORD_FILES = ('execution.json', 'stdout.log', 'stderr.log', 'console.log')
REPORT_LIMIT = 1_000_000
REPORT_DEPTH = 64
TREE_DEPTH = 128
# Every lookup below a run root opens one component at a time and never follows a link.
NOFOLLOW = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
VERSION_TIMEOUT_S = 30
STEP_KEYS = {'schema', 'stage', 'step', 'argv', 'mounts', 'version_argv', 'source',
             'backend_report', 'writes', 'artifacts', 'adoptable_exits', 'timeout_s', 'inputs'}
RECEIPT_KEYS = {'schema', 'receipt_id', 'binding', 'spec', 'spec_sha256', 'argv', 'tool', 'tool_after',
                'inputs', 'outputs', 'changed', 'violations', 'artifacts', 'execution',
                'version_execution', 'exit_code', 'status', 'started_unix', 'duration_s',
                'sandbox_profile_sha256', 'identity'}
INDEX_KEYS = {'schema', 'implementation', 'source_candidate_id', 'receipts', 'delivered', 'identity'}


def _sandbox_path(value, label):
    if not isinstance(value, str) or not value.startswith('/'):
        raise ValueError(f'{label} must be an absolute sandbox path')
    path = PurePosixPath(value)
    if '..' in path.parts or str(path) != value or value == '/':
        raise ValueError(f'{label} must be a normalized absolute sandbox path')
    return value


def _relative(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f'{label} must be a relative path')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '.' in path.parts or str(path) != value:
        raise ValueError(f'{label} must be a normalized relative path: {value!r}')
    return value


def _under(path, root):
    return path == root or path.startswith(root.rstrip('/') + '/')


def _argv(value, label):
    if not isinstance(value, list) or not value or any(not isinstance(a, str) for a in value):
        raise ValueError(f'{label} must be a non-empty string list')
    return list(value)


def validate_step(spec, *, host=True):
    """Return the normalized step declaration; host=True also checks mount sources."""
    if not isinstance(spec, dict) or spec.get('schema') != STEP_SCHEMA:
        raise ValueError(f'stage step must be {STEP_SCHEMA}')
    if not set(spec) <= STEP_KEYS or not {'schema', 'stage', 'step', 'argv', 'writes'} <= set(spec):
        raise ValueError('stage step has unknown or missing fields')
    if spec['stage'] not in STAGES:
        raise ValueError('stage step names an unknown stage')
    if not isinstance(spec['step'], str) or not STEP_NAME.match(spec['step']):
        raise ValueError('stage step name must match [a-z0-9][a-z0-9._-]{0,63}')
    argv = _argv(spec['argv'], 'stage step argv')
    mounts = spec.get('mounts', [])
    if not isinstance(mounts, list):
        raise ValueError('stage step mounts must be a list')
    normalized_mounts, seen = [], []
    for mount in mounts:
        if not isinstance(mount, dict) or set(mount) != {'source', 'at'}:
            raise ValueError('stage step mount needs exactly source and at')
        at = _sandbox_path(mount['at'], 'mount at')
        if any(_under(at, reserved) or _under(reserved, at) for reserved in RESERVED_MOUNTS):
            raise ValueError(f'mount at reserved sandbox path: {at}')
        if any(_under(at, other) or _under(other, at) for other in seen):
            raise ValueError('stage step mounts overlap')
        seen.append(at)
        source = mount['source']
        if not isinstance(source, str) or not Path(source).is_absolute():
            raise ValueError('mount source must be an absolute host path')
        if host and not Path(source).exists():
            raise ValueError(f'mount source does not exist: {source}')
        normalized_mounts.append({'source': source, 'at': at})

    def tool_path(value, label):
        _sandbox_path(value, label)
        if not (value.startswith(SYSTEM_PREFIXES) or any(_under(value, m['at']) and value != m['at']
                                                         for m in normalized_mounts)):
            raise ValueError(f'{label} must come from a read-only system path or declared mount')
        return value

    tool_path(argv[0], 'stage step executable')
    version_argv = spec.get('version_argv')
    if version_argv is not None:
        tool_path(_argv(version_argv, 'version_argv')[0], 'version executable')
    source = spec.get('source')
    if source is not None:
        _sandbox_path(source, 'source')
        if not any(_under(source, m['at']) for m in normalized_mounts):
            raise ValueError('Python source tree must be under a declared mount')
    writes = spec['writes']
    if not isinstance(writes, dict):
        raise ValueError('stage step writes must be a map')
    for target, produced in writes.items():
        _relative(target, 'write target')
        _relative(produced, 'write source')
        if PurePosixPath(produced).parts[0] in EXECUTOR_FILES:
            raise ValueError('write source collides with executor records')
        if PurePosixPath(produced).parts[0] == 'design' and produced != 'design/' + target:
            raise ValueError('an in-place write must keep its candidate path')
    artifacts = spec.get('artifacts', [])
    if not isinstance(artifacts, list) or len(set(artifacts)) != len(artifacts):
        raise ValueError('stage step artifacts must be a unique list')
    for artifact in artifacts:
        _relative(artifact, 'artifact')
        if PurePosixPath(artifact).parts[0] in (*EXECUTOR_FILES, 'design'):
            raise ValueError('artifact collides with executor records or the design copy')
    if not writes and not artifacts:
        raise ValueError('stage step must declare at least one write or artifact')
    report = spec.get('backend_report')
    if report is not None:
        if (not isinstance(report, dict) or set(report) != {'artifact', 'key'} or
                not isinstance(report['key'], str) or not report['key'] or
                report.get('artifact') not in artifacts):
            raise ValueError('backend_report needs a declared JSON artifact and key')
    exits = spec.get('adoptable_exits', [0])
    if (not isinstance(exits, list) or 0 not in exits or len(set(exits)) != len(exits) or
            any(isinstance(e, bool) or not isinstance(e, int) or not 0 <= e <= 255 for e in exits)):
        raise ValueError('adoptable_exits must be unique exit codes including 0')
    timeout = spec.get('timeout_s', 120)
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
            not math.isfinite(timeout) or not 1 <= timeout <= 86400):
        raise ValueError('timeout_s must be between 1 and 86400 seconds')
    inputs = spec.get('inputs')
    if 'inputs' in spec:
        if not isinstance(inputs, list) or not inputs or any(not isinstance(p, str) for p in inputs):
            raise ValueError('stage step inputs must be a nonempty list of candidate paths')
        for path in inputs:
            _relative(path, 'step input')
        if len(set(inputs)) != len(inputs) or set(inputs) & writes.keys():
            raise ValueError('stage step inputs must be unique and separate from writes')
    return {**({'inputs': inputs} if 'inputs' in spec else {}),
            'schema': STEP_SCHEMA, 'stage': spec['stage'], 'step': spec['step'], 'argv': argv,
            'mounts': normalized_mounts, 'version_argv': version_argv, 'source': source,
            'backend_report': report, 'writes': dict(sorted(writes.items())), 'artifacts': list(artifacts),
            'adoptable_exits': sorted(exits), 'timeout_s': timeout}


def _digest_fd(descriptor):
    h = hashlib.sha256()
    with os.fdopen(descriptor, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _lookup(root, rel):
    """('ok', fd), ('missing', None) or ('unsafe', None) for root/rel.

    Each component is opened relative to its parent without following links, so a link
    or special entry anywhere below root is never traversed. root itself is coordinator-owned.
    """
    try:
        current = os.open(root, NOFOLLOW | os.O_DIRECTORY)
    except FileNotFoundError:
        return 'missing', None
    except OSError:
        return 'unsafe', None
    try:
        parts = PurePosixPath(rel).parts
        for part in parts[:-1]:
            child = os.open(part, NOFOLLOW | os.O_DIRECTORY, dir_fd=current)
            os.close(current)
            current = child
        return 'ok', os.open(parts[-1], NOFOLLOW, dir_fd=current)
    except FileNotFoundError:
        return 'missing', None
    except OSError:
        return 'unsafe', None
    finally:
        os.close(current)


def _exists(root, rel):
    """Whether any entry, of any type, is at root/rel; links are not followed."""
    state, descriptor = _lookup(root, rel)
    if descriptor is not None:
        os.close(descriptor)
    return state != 'missing'


def _regular(root, rel, *, content=False, limit=None):
    """('file', sha256), ('missing', None) or ('unsafe', None).

    content=True returns the bytes instead, at most limit + 1 of them when limit is given.
    """
    state, descriptor = _lookup(root, rel)
    if state != 'ok':
        return state, None
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        return 'unsafe', None
    if not content:
        return 'file', _digest_fd(descriptor)
    with os.fdopen(descriptor, 'rb') as stream:
        return 'file', stream.read(-1 if limit is None else limit + 1)


def _scan(descriptor, files, other):
    """Walk an open directory without following links; consumes descriptor."""
    try:
        stack = [(descriptor, '', iter(sorted(os.listdir(descriptor))))]
    except OSError as exc:
        os.close(descriptor)
        raise ValueError('directory cannot be listed') from exc
    try:
        while stack:
            current, prefix, names = stack[-1]
            name = next(names, None)
            if name is None:
                os.close(current)
                stack.pop()
                continue
            rel = prefix + name
            try:
                child = os.open(name, NOFOLLOW, dir_fd=current)
            except OSError:
                other.append(rel)
                continue
            mode = os.fstat(child).st_mode
            if stat.S_ISREG(mode):
                files[rel] = _digest_fd(child)
            elif stat.S_ISDIR(mode) and len(stack) < TREE_DEPTH:
                try:
                    entries = iter(sorted(os.listdir(child)))
                except OSError:
                    os.close(child)
                    other.append(rel)
                    continue
                stack.append((child, rel + '/', entries))
            else:
                os.close(child)
                other.append(rel)
    finally:
        for current, _, _ in stack:
            os.close(current)


def file_tree(root):
    """Regular files by relative path, plus every link or special entry."""
    try:
        descriptor = os.open(root, NOFOLLOW | os.O_DIRECTORY)
    except OSError as exc:
        raise ValueError(f'tree root is not a directory: {root}') from exc
    files, other = {}, []
    _scan(descriptor, files, other)
    return dict(sorted(files.items())), sorted(other)


def _strict_tree(root):
    files, other = file_tree(root)
    if other:
        raise ValueError(f'stage step input contains links or special files: {other}')
    return files


def _identity_tree(source):
    """Identity of a read-only tool mount: file bytes and link targets."""
    source = Path(source)
    if source.is_file() and not source.is_symlink():
        entries = {source.name: digest(source)}
    else:
        entries = {}
        for current, dirs, names in os.walk(source, followlinks=False):
            base = Path(current)
            for name in [*dirs, *names]:
                path = base / name
                rel = path.relative_to(source).as_posix()
                if path.is_symlink():
                    entries[rel] = 'link:' + os.readlink(path)
                elif path.is_file():
                    entries[rel] = digest(path)
    return {'sha256': identity(entries), 'files': len(entries)}


def tree_identity(source):
    """Identity of a retained tool tree in the form a receipt records it."""
    return _identity_tree(source)


def _host_path(spec, sandbox_path):
    for mount in spec['mounts']:
        if _under(sandbox_path, mount['at']):
            rel = PurePosixPath(sandbox_path).relative_to(mount['at'])
            return Path(mount['source']) / rel
    return Path(sandbox_path)


def observe_tool(spec):
    """Coordinator-side tool identity; missing pieces are recorded as null."""
    host = _host_path(spec, spec['argv'][0])
    resolved = host.resolve() if host.exists() else None
    executable = {'path': spec['argv'][0], 'host_path': str(resolved) if resolved else str(host),
                  'sha256': digest(resolved) if resolved and resolved.is_file() else None}
    mounts = [{'at': m['at'], **_identity_tree(m['source'])} if Path(m['source']).exists()
              else {'at': m['at'], 'sha256': None, 'files': 0} for m in spec['mounts']]
    source = None
    if spec['source'] is not None:
        root = _host_path(spec, spec['source'])
        source = {'path': spec['source'], **(_identity_tree(root) if root.exists()
                                             else {'sha256': None, 'files': 0})}
    return {'executable': executable, 'mounts': mounts, 'source_tree': source}


def sandbox_profile(sandbox_argv):
    """Sandbox options before the tool argv, with per-run host bind sources normalized."""
    if not isinstance(sandbox_argv, list) or '--' not in sandbox_argv:
        raise ValueError('malformed sandbox argv')
    options = sandbox_argv[:sandbox_argv.index('--')]
    profile, index = [], 0
    while index < len(options):
        token = options[index]
        if token in ('--bind', '--ro-bind') and index + 2 < len(options):
            source, dest = options[index + 1], options[index + 2]
            profile += [token, source if source == dest else '<host>', dest]
            index += 3
        else:
            profile.append(token)
            index += 1
    return profile


def _reported_version(spec, record, work):
    if spec['version_argv'] is None:
        return {'status': 'unknown', 'value': None, 'reason': 'no version probe declared'}
    if record is None:
        return {'status': 'unknown', 'value': None, 'reason': 'version probe not run'}
    if record['execution'] != 'ok' or record['exit_code'] != 0:
        return {'status': 'unknown', 'value': None,
                'reason': f"version probe {record['execution']} exit {record['exit_code']}"}
    state, data = _regular(work, 'stdout.log', content=True, limit=REPORT_LIMIT)
    if state != 'file':
        return {'status': 'unknown', 'value': None, 'reason': 'version probe output is not a regular file'}
    if len(data) > REPORT_LIMIT:
        return {'status': 'unknown', 'value': None, 'reason': 'version probe output too large'}
    try:
        lines = data.decode('utf-8').splitlines()
    except UnicodeDecodeError:
        return {'status': 'unknown', 'value': None, 'reason': 'version probe output is not UTF-8'}
    value = lines[0].strip() if lines else ''
    if not value:
        return {'status': 'unknown', 'value': None, 'reason': 'version probe printed no version'}
    return {'status': 'reported', 'value': value, 'reason': None}


class _Unreported(ValueError):
    pass


def _unique(pairs):
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _Unreported('has duplicate keys')
    return dict(pairs)


def _nonfinite(token):
    raise _Unreported('has a non-finite number')


def _finite(token):
    value = float(token)
    if not math.isfinite(value):
        raise _Unreported('has a non-finite number')
    return value


def _strict_json(data):
    """UTF-8 JSON with unique keys, finite numbers and bounded nesting, or _Unreported."""
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise _Unreported('is not UTF-8') from exc
    try:
        value = json.loads(text, object_pairs_hook=_unique, parse_constant=_nonfinite, parse_float=_finite)
    except RecursionError as exc:
        raise _Unreported(f'nesting exceeds {REPORT_DEPTH}') from exc
    except _Unreported:
        raise
    except json.JSONDecodeError as exc:
        raise _Unreported('is not JSON') from exc
    except ValueError as exc:
        # For example an integer beyond the interpreter's digit limit.
        raise _Unreported('has an out-of-range number') from exc
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (dict, list)):
            if depth > REPORT_DEPTH:
                raise _Unreported(f'nesting exceeds {REPORT_DEPTH}')
            stack += [(child, depth + 1) for child in (item.values() if isinstance(item, dict) else item)]
    return value


def _backend(spec, run):
    report = spec['backend_report']
    if report is None:
        return {'status': 'not_declared', 'value': None, 'reason': None}
    state, data = _regular(run, report['artifact'], content=True, limit=REPORT_LIMIT)
    if state == 'missing':
        return {'status': 'unknown', 'value': None, 'reason': 'backend report artifact missing'}
    if state != 'file':
        return {'status': 'unknown', 'value': None, 'reason': 'backend report artifact is a link or special file'}
    if len(data) > REPORT_LIMIT:
        return {'status': 'unknown', 'value': None, 'reason': 'backend report artifact too large'}
    try:
        value = _strict_json(data)
    except _Unreported as exc:
        return {'status': 'unknown', 'value': None, 'reason': f'backend report artifact {exc}'}
    if not isinstance(value, dict) or report['key'] not in value:
        return {'status': 'unknown', 'value': None, 'reason': 'backend report key missing'}
    # Copied verbatim: a null version stays null rather than being filled in.
    return {'status': 'reported', 'value': value[report['key']], 'reason': None}


def _artifacts(spec, run):
    found = {}
    for declared in spec['artifacts']:
        state, descriptor = _lookup(run, declared)
        mode = os.fstat(descriptor).st_mode if state == 'ok' else None
        if mode is not None and stat.S_ISREG(mode):
            found[declared] = _digest_fd(descriptor)
        elif mode is not None and stat.S_ISDIR(mode):
            files, other = {}, []
            try:
                _scan(descriptor, files, other)
            except ValueError:
                found[declared] = None
                continue
            found.update({f'{declared}/{rel}': sha for rel, sha in files.items()})
            found.update({f'{declared}/{rel}': None for rel in other})
            if not files and not other:
                found[declared + '/'] = None
        else:
            # Missing, linked (at any component) or special: recorded, never followed.
            if descriptor is not None:
                os.close(descriptor)
            found[declared] = None
    return dict(sorted(found.items()))


def _writes(spec, run):
    """Observed write sources: target -> ('file'|'missing'|'unsafe', sha256 or None)."""
    return {target: _regular(run, produced) for target, produced in spec['writes'].items()}


def _outputs(spec, run):
    return {target: sha for target, (_, sha) in _writes(spec, run).items()}


def _records(work):
    """Executor record files: name -> ('file'|'missing'|'unsafe', sha256 or None)."""
    return {name: _regular(work, name) for name in RECORD_FILES}


def _violations(spec, inputs, receipt_dir, tool, tool_after, executions):
    violations = []
    violations += ['missing_input:' + rel for rel in spec.get('inputs', []) if rel not in inputs]
    run = Path(receipt_dir) / 'run'
    try:
        files, other = file_tree(run / 'design')
    except ValueError:
        files, other = {}, ['.']
    in_place = {t for t, p in spec['writes'].items() if p == 'design/' + t}
    for rel in sorted(set(files) ^ set(inputs) | set(other)):
        if rel not in in_place:
            violations.append('inventory_changed:' + rel)
    for rel in sorted(set(files) & set(inputs)):
        if files[rel] != inputs[rel] and rel not in in_place:
            violations.append('write_outside_scope:' + rel)
    writes = _writes(spec, run)
    violations += ['missing_output:' + rel for rel, (state, _) in writes.items() if state == 'missing']
    violations += ['unsafe_output:' + rel for rel, (state, _) in writes.items() if state == 'unsafe']
    if not spec['writes']:
        # Artifacts are the only promised output of a write-free step. Write-bearing
        # steps retain the existing allowance for unhashable artifact links (e.g. latest).
        violations += ['missing_artifact:' + rel for rel, sha in _artifacts(spec, run).items() if sha is None]
    # A tool can plant an entry where the executor later writes its own records, which the
    # executor moves aside and reports, or leave /work unwritable so that it cannot write them.
    for name in ('version', 'run'):
        work = Path(receipt_dir) / name
        records = _records(work)
        displaced = executions[name].get('displaced_records', {}) if executions[name] else {}
        for record, (state, _) in records.items():
            if (state == 'unsafe' or record in displaced or
                    (state == 'missing' and executions[name] is not None)):
                violations.append(f'executor_record_unsafe:{name}/{record}')
        # The executor creates home/ before launch, so its presence shows the step was entered.
        entered = _exists(work, 'home') or any(st != 'missing' for st, _ in records.values())
        if executions[name] is None and entered:
            violations.append(f'executor_record_missing:{name}')
    if tool['executable'] != tool_after['executable'] or tool['executable']['sha256'] is None:
        violations.append('tool_changed')
    for before, after in zip(tool['mounts'], tool_after['mounts']):
        if before != after or before['sha256'] is None:
            violations.append('mount_changed:' + before['at'])
    if tool['source_tree'] != tool_after['source_tree'] or (
            tool['source_tree'] is not None and tool['source_tree']['sha256'] is None):
        violations.append('source_changed')
    return violations


def _status(spec, execution, violations):
    if execution is None:
        # No trusted executor record: an interrupt before one existed, or records the executor could not keep.
        return 'refused' if any(v.startswith('executor_record_') for v in violations) else 'cancelled'
    if execution['execution'] == 'interrupted':
        return 'cancelled'
    if execution['execution'] in ('timeout', 'unavailable'):
        return execution['execution']
    if execution['exit_code'] not in spec['adoptable_exits']:
        return 'failed'
    if violations:
        return 'refused'
    return 'completed' if execution['exit_code'] == 0 else 'partial'


def run_step(receipt_dir, design, spec, binding, *, deadline=None, cancellation=None):
    """Execute one declared step on a copy of design; never modifies design itself."""
    spec = validate_step(spec)
    if not isinstance(binding, dict):
        raise ValueError('stage receipt binding must be an object')
    timeout = spec['timeout_s']
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise ValueError('stage step deadline exhausted before launch')
    _strict_tree(design)
    receipt_dir = Path(receipt_dir)
    receipt_dir.mkdir(parents=True, exist_ok=False)
    started_unix, start = time.time(), time.monotonic()
    receipt = {'schema': RECEIPT_SCHEMA, 'receipt_id': str(uuid.uuid4()), 'binding': binding,
               'spec': spec, 'spec_sha256': identity(spec), 'argv': spec['argv']}
    interrupted, failure = False, None
    execution = version_record = None
    run = receipt_dir / 'run'
    try:
        shutil.copytree(design, receipt_dir / 'inputs')
        inputs = _strict_tree(receipt_dir / 'inputs')
        shutil.copytree(receipt_dir / 'inputs', run / 'design')
        tool = observe_tool(spec)
    except BaseException:
        # Nothing was executed or observed; a partial copy is not evidence.
        shutil.rmtree(receipt_dir, ignore_errors=True)
        raise
    mounts = [(m['source'], m['at']) for m in spec['mounts']]
    try:
        if spec['version_argv'] is not None:
            version_record = execute(receipt_dir / 'inputs', receipt_dir / 'version', spec['version_argv'],
                                     timeout=max(.001, min(VERSION_TIMEOUT_S, timeout)), network=False,
                                     extra_ro=mounts, cancellation=cancellation)
        # An exhausted budget is recorded as the step's own observed timeout.
        execution = execute(receipt_dir / 'inputs', run, spec['argv'],
                            timeout=max(.001, timeout - (time.monotonic() - start)), network=False,
                            extra_ro=mounts, cancellation=cancellation)
    except KeyboardInterrupt:
        # The executor writes its record before re-raising an interrupt. Anything else on disk may
        # have been planted by the tool, and at most it yields a cancelled, unadoptable receipt.
        interrupted = True
        execution = _interrupted_record(run, spec['argv'])
        if version_record is None and spec['version_argv'] is not None:
            version_record = _interrupted_record(receipt_dir / 'version', spec['version_argv'])
    except Exception as exc:
        # The executor could not keep its records, for example because the tool left /work
        # unwritable. No record on disk is trusted; the receipt is kept and the error re-raised.
        failure = exc
    tool_after = observe_tool(spec)
    outputs = _outputs(spec, run)
    violations = _violations(spec, inputs, receipt_dir, tool, tool_after,
                             {'run': execution, 'version': version_record})
    receipt.update(
        tool={**tool, 'reported_version': _reported_version(spec, version_record, receipt_dir / 'version'),
              'backend': _backend(spec, run)},
        tool_after=tool_after, inputs=inputs, outputs=outputs,
        changed=sorted(rel for rel, sha in outputs.items() if sha != inputs.get(rel)),
        violations=violations, artifacts=_artifacts(spec, run), execution=execution,
        version_execution=version_record, exit_code=execution['exit_code'] if execution else None,
        status=_status(spec, execution, violations), started_unix=started_unix,
        duration_s=round(time.monotonic() - start, 3),
        sandbox_profile_sha256=identity(sandbox_profile(execution['sandbox_argv'])) if execution else None)
    receipt['identity'] = identity(receipt)
    write_json(receipt_dir / 'receipt.json', receipt)
    if interrupted:
        raise KeyboardInterrupt
    if failure is not None:
        raise failure
    return receipt


def _retained_record(work):
    """The executor's record when it is a regular file reached without links, else None."""
    state, data = _regular(work, 'execution.json', content=True, limit=REPORT_LIMIT)
    if state != 'file' or len(data) > REPORT_LIMIT:
        return None
    try:
        return _strict_json(data)
    except _Unreported:
        return None


def _interrupted_record(work, argv):
    record = _retained_record(work)
    if not isinstance(record, dict) or record.get('execution') != 'interrupted':
        return None
    try:
        _execution_record(record, work, argv)
    except ValueError:
        return None
    return record


def _execution_record(record, work, argv):
    if record is None:
        return
    records = _records(work)
    # A linked or special record file is a recomputed violation; it is never read through.
    if (not isinstance(record, dict) or record.get('argv') != argv or
            (records['execution.json'][0] != 'unsafe' and record != _retained_record(work))):
        raise ValueError('stage receipt execution record differs from retained execution')
    displaced = record.get('displaced_records', {})
    if not isinstance(displaced, dict) or not set(displaced) <= set(RECORD_FILES):
        raise ValueError('malformed stage receipt displaced executor records')
    sandbox = record.get('sandbox_argv')
    if (not isinstance(sandbox, list) or '--' not in sandbox or sandbox[sandbox.index('--') + 1:] != argv or
            '--unshare-net' not in sandbox[:sandbox.index('--')]):
        raise ValueError('stage receipt sandbox lacks the exact argv or network isolation')
    options = sandbox[:sandbox.index('--')]
    pairs = {tuple(options[i:i + 3]) for i in range(len(options) - 2)}
    if (not any(p[0] == '--ro-bind' and p[2] == '/candidate' for p in pairs) or
            not any(p[0] == '--bind' and p[2] == '/work' for p in pairs)):
        raise ValueError('stage receipt sandbox lacks read-only candidate or scratch binding')
    for name, key in (('stdout.log', 'stdout_sha256'), ('stderr.log', 'stderr_sha256'),
                      ('console.log', 'console_sha256')):
        state, sha = records[name]
        if state != 'unsafe' and sha != record.get(key):
            raise ValueError('stage receipt execution artifacts changed')


def validate_receipt(receipt_dir, *, binding=None):
    """Recompute every derivable receipt field from retained coordinator records."""
    receipt_dir = Path(receipt_dir)
    receipt = read_json(receipt_dir / 'receipt.json')
    if not isinstance(receipt, dict) or receipt.get('schema') != RECEIPT_SCHEMA or set(receipt) != RECEIPT_KEYS:
        raise ValueError('malformed stage receipt')
    if identity({k: v for k, v in receipt.items() if k != 'identity'}) != receipt['identity']:
        raise ValueError('stage receipt identity mismatch')
    try:
        uuid.UUID(receipt['receipt_id'])
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('malformed stage receipt id') from exc
    if binding is not None and receipt['binding'] != binding:
        raise ValueError('foreign stage receipt binding')
    spec = validate_step(receipt['spec'], host=False)
    if spec != receipt['spec'] or receipt['spec_sha256'] != identity(spec) or receipt['argv'] != spec['argv']:
        raise ValueError('stage receipt step declaration mismatch')
    if receipt['inputs'] != _strict_tree(receipt_dir / 'inputs'):
        raise ValueError('stage receipt inputs changed')
    run = receipt_dir / 'run'
    execution = receipt['execution']
    _execution_record(execution, run, spec['argv'])
    version_record = receipt['version_execution']
    if spec['version_argv'] is None and version_record is not None:
        raise ValueError('stage receipt has an undeclared version probe')
    _execution_record(version_record, receipt_dir / 'version', spec['version_argv'])
    if receipt['exit_code'] != (execution['exit_code'] if execution else None):
        raise ValueError('stage receipt exit code differs from execution')
    if receipt['sandbox_profile_sha256'] != (identity(sandbox_profile(execution['sandbox_argv']))
                                            if execution else None):
        raise ValueError('stage receipt sandbox profile mismatch')
    tool, after = receipt['tool'], receipt['tool_after']
    if not isinstance(tool, dict) or set(tool) != {'executable', 'mounts', 'source_tree', 'reported_version', 'backend'}:
        raise ValueError('malformed stage receipt tool identity')
    observed = {k: tool[k] for k in ('executable', 'mounts', 'source_tree')}
    for value in (observed, after):
        if (not isinstance(value, dict) or set(value) != {'executable', 'mounts', 'source_tree'} or
                not isinstance(value['executable'], dict) or
                set(value['executable']) != {'path', 'host_path', 'sha256'} or
                value['executable']['path'] != spec['argv'][0] or not isinstance(value['mounts'], list) or
                any(not isinstance(m, dict) or set(m) != {'at', 'sha256', 'files'} for m in value['mounts']) or
                not (value['source_tree'] is None or isinstance(value['source_tree'], dict)) or
                [m.get('at') for m in value['mounts']] != [m['at'] for m in spec['mounts']] or
                (value['source_tree'] is None) != (spec['source'] is None)):
            raise ValueError('malformed stage receipt tool identity')
    if tool['reported_version'] != _reported_version(spec, version_record, receipt_dir / 'version'):
        raise ValueError('stage receipt reported version differs from retained probe')
    if tool['backend'] != _backend(spec, run):
        raise ValueError('stage receipt backend identity differs from retained report')
    outputs = _outputs(spec, run)
    if receipt['outputs'] != outputs:
        raise ValueError('stage receipt outputs changed')
    if receipt['artifacts'] != _artifacts(spec, run):
        raise ValueError('stage receipt artifacts changed')
    if receipt['changed'] != sorted(rel for rel, sha in outputs.items() if sha != receipt['inputs'].get(rel)):
        raise ValueError('stage receipt changed paths mismatch')
    violations = _violations(spec, receipt['inputs'], receipt_dir, observed, after,
                             {'run': execution, 'version': version_record})
    if receipt['violations'] != violations:
        raise ValueError('stage receipt violations mismatch')
    if receipt['status'] != _status(spec, execution, violations):
        raise ValueError('stage receipt status mismatch')
    for key in ('started_unix', 'duration_s'):
        value = receipt[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('malformed stage receipt timing')
    return receipt


def after_tree(receipt):
    tree = dict(receipt['inputs'])
    tree.update(receipt['outputs'])
    return dict(sorted(tree.items()))


def applicability(receipts, delivered):
    """Exact relation of each receipt to the delivered tree, latest first."""
    results = [None] * len(receipts)
    for index in reversed(range(len(receipts))):
        receipt = receipts[index]
        if receipt['status'] not in ADOPTABLE:
            results[index] = {'applicability': 'not_applicable', 'differences': ['status:' + receipt['status']]}
            continue
        produced = after_tree(receipt)
        if produced == delivered:
            results[index] = {'applicability': 'applicable', 'differences': []}
        elif any(results[later]['applicability'] in ('applicable', 'superseded') and
                 receipts[later]['inputs'] == produced for later in range(index + 1, len(receipts))):
            results[index] = {'applicability': 'superseded', 'differences': []}
        else:
            results[index] = {'applicability': 'not_applicable', 'differences': sorted(
                rel for rel in produced.keys() | delivered.keys() if produced.get(rel) != delivered.get(rel))}
    return results


def _write_beneath(root, rel, data):
    """Write data at root/rel, creating directories, without following any link below root."""
    current = os.open(root, NOFOLLOW | os.O_DIRECTORY)
    try:
        parts = PurePosixPath(rel).parts
        for part in parts[:-1]:
            try:
                child = os.open(part, NOFOLLOW | os.O_DIRECTORY, dir_fd=current)
            except FileNotFoundError:
                os.mkdir(part, dir_fd=current)
                child = os.open(part, NOFOLLOW | os.O_DIRECTORY, dir_fd=current)
            os.close(current)
            current = child
        target = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o666, dir_fd=current)
    finally:
        os.close(current)
    with os.fdopen(target, 'wb') as stream:
        stream.write(data)


def adopt(receipt_dir, receipt, design):
    """Copy an adoptable receipt's exact outputs into an unchanged design."""
    spec = receipt['spec']
    if receipt['status'] not in ADOPTABLE:
        raise ValueError(f"stage step {spec['step']} {receipt['status']}; no output adopted or substituted")
    # Only the retained coordinator receipt, revalidated now, authorizes adoption.
    if validate_receipt(receipt_dir) != receipt:
        raise ValueError('stage receipt differs from the retained coordinator receipt')
    design = Path(design)
    if _strict_tree(design) != receipt['inputs']:
        raise ValueError(f"design changed during stage step {spec['step']}")
    produced = {}
    for target, source in spec['writes'].items():
        state, data = _regular(Path(receipt_dir) / 'run', source, content=True)
        if state != 'file' or hashlib.sha256(data).hexdigest() != receipt['outputs'][target]:
            raise ValueError('stage step output changed before adoption')
        produced[target] = data
    # The bytes written are the bytes hashed above, whatever happens to the run tree meanwhile.
    for target, data in produced.items():
        _write_beneath(design, target, data)
    if _strict_tree(design) != after_tree(receipt):
        raise ValueError('adopted design differs from stage receipt outputs')


def _receipt_dirs(work):
    root = Path(work) / 'receipts'
    if not root.exists():
        return []
    entries = []
    for path in root.iterdir():
        index, _, step = path.name.partition('-')
        if path.is_symlink() or not path.is_dir() or not index.isdigit() or not STEP_NAME.match(step):
            raise ValueError(f'unexpected stage receipt entry: {path.name}')
        entries.append((int(index), path))
    entries.sort()
    if [i for i, _ in entries] != list(range(1, len(entries) + 1)):
        raise ValueError('stage receipt directories are not one contiguous sequence')
    return [p for _, p in entries]


def build_index(work, delivered_dir, implementation, source_candidate_id):
    binding = {'implementation': implementation, 'source_candidate_id': source_candidate_id}
    receipts, links = [], []
    for k, path in enumerate(_receipt_dirs(work), 1):
        receipt = validate_receipt(path, binding={**binding, 'receipt_index': k})
        if path.name != f"{k}-{receipt['spec']['step']}":
            raise ValueError('stage receipt directory name differs from its step')
        receipts.append(receipt)
        links.append({'path': (path / 'receipt.json').relative_to(work).as_posix(),
                      'sha256': digest(path / 'receipt.json'), 'receipt_id': receipt['receipt_id']})
    delivered = _strict_tree(delivered_dir) if isinstance(delivered_dir, (str, Path)) else dict(delivered_dir)
    for link, relation in zip(links, applicability(receipts, delivered)):
        link.update(relation)
    index = {'schema': INDEX_SCHEMA, **binding, 'receipts': links, 'delivered': delivered}
    return {**index, 'identity': identity(index)}


def bind_implementation(work, design, implementation, source_candidate_id):
    """Write and return the digest of the coordinator's receipt index for one repair."""
    work = Path(work)
    write_json(work / 'stage-receipts.json', build_index(work, design, implementation, source_candidate_id))
    return digest(work / 'stage-receipts.json')


def verify_implementation(work, anchor, implementation, source_candidate_id, *, captured=None, steps=None,
                          complete=False):
    """Verify one anchored index; captured is the next run's candidate file map, if any.

    steps is the ordered expected step list; retained receipts must be a prefix of it, or all
    of it when complete is true (a delivered proposal implies every planned step ran)."""
    work = Path(work)
    path = work / 'stage-receipts.json'
    if not path.is_file() or digest(path) != anchor:
        raise ValueError('stage receipt index changed or missing')
    stored = read_json(path)
    if (not isinstance(stored, dict) or stored.get('schema') != INDEX_SCHEMA or set(stored) != INDEX_KEYS or
            identity({k: v for k, v in stored.items() if k != 'identity'}) != stored['identity']):
        raise ValueError('malformed stage receipt index')
    if not isinstance(stored['delivered'], dict):
        raise ValueError('malformed stage receipt index')
    rebuilt = build_index(work, stored['delivered'], implementation, source_candidate_id)
    if rebuilt != stored:
        raise ValueError('stage receipt index differs from retained receipts')
    if steps is not None:
        specs = [read_json(work / link['path'])['spec'] for link in stored['receipts']]
        if len(specs) > len(steps) or specs != [validate_step(s, host=False) for s in steps[:len(specs)]]:
            raise ValueError('stage receipt step differs from the campaign declaration')
        if complete and len(specs) != len(steps):
            raise ValueError('delivered proposal lacks a receipt for a planned stage step')
    if captured is not None and captured != stored['delivered']:
        for link in stored['receipts']:
            if link['applicability'] == 'applicable':
                raise ValueError('claimed applicable stage receipt output differs from captured proposal')
        raise ValueError('captured proposal differs from the delivered design')
    return stored


def validate_pre_receipts(declarations):
    """Validate the campaign's explicit receipt hashes and expected coordinator bindings."""
    if not isinstance(declarations, list):
        raise ValueError('pre_receipts must be a list')
    for link in declarations:
        if not isinstance(link, dict) or set(link) != {'path', 'sha256', 'binding'}:
            raise ValueError('pre_receipt needs exactly path, sha256 and binding')
        if (not isinstance(link['path'], str) or not link['path'] or
                Path(link['path']).name != 'receipt.json'):
            raise ValueError('pre_receipt path must name receipt.json')
        if not isinstance(link['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', link['sha256']):
            raise ValueError('pre_receipt sha256 must be an exact digest')
        if not isinstance(link['binding'], dict) or not link['binding']:
            raise ValueError('pre_receipt binding must be a nonempty object')
    return declarations


def validate_pre_receipt(base, link):
    path = Path(base) / link['path']
    state, sha = _regular(path.parent, path.name)
    if state != 'file' or sha != link['sha256']:
        raise ValueError('pre_receipt changed, unsafe or missing')
    receipt = validate_receipt(path.parent, binding=link['binding'])
    if identity(receipt['binding']) != identity(link['binding']):
        raise ValueError('foreign stage receipt binding')
    if receipt['status'] not in ADOPTABLE:
        raise ValueError('pre_receipt is not adoptable: ' + receipt['status'])
    return receipt


def validate_pre_receipt_chain(base, declarations):
    """Declared order is custody order: each receipt consumes its predecessor's exact after-tree."""
    receipts = [validate_pre_receipt(base, link) for link in validate_pre_receipts(declarations)]
    for number in range(1, len(receipts)):
        if receipts[number]['inputs'] != after_tree(receipts[number - 1]):
            raise ValueError(f'pre_receipt {number + 1} does not consume the after-tree of pre_receipt '
                             f'{number}; declared pre_receipts must form one ordered chain')
    return receipts


def import_pre_receipts(root, declarations, source):
    """Copy retained runs, preserving artifact links without following them, then revalidate."""
    imported = []
    for index, link in enumerate(validate_pre_receipts(declarations), 1):
        validate_pre_receipt(source, link)
        receipt_dir = (Path(source) / link['path']).parent
        target = Path(root) / 'pre-receipts' / str(index)
        if target.resolve().is_relative_to(receipt_dir.resolve()):
            raise ValueError('pre_receipt import destination is inside its source')
        shutil.copytree(receipt_dir, target, symlinks=True)
        retained = {**link, 'path': f'pre-receipts/{index}/receipt.json'}
        validate_pre_receipt(root, retained)
        imported.append(retained)
    return imported


def pre_receipt_binding(receipt, captured, *, folders=None):
    """Exact first-capture relation in every top-level folder of the receipt's after-tree."""
    produced = after_tree(receipt)
    if folders is None:
        folders = {rel.split('/', 1)[0] for rel in produced}
    selected = {rel: sha for rel, sha in captured.items() if rel.split('/', 1)[0] in folders}
    differences = sorted(rel for rel in produced.keys() | selected.keys()
                         if produced.get(rel) != selected.get(rel))
    return {'status': receipt['status'], 'bound': not differences, 'differences': differences}


def verify_pre_receipts(root, state, declarations):
    """Revalidate imported runs and bind them to run 1, independently of declared stage steps."""
    root = Path(root)
    declarations = validate_pre_receipts(declarations)
    expected = {f'pre-receipts/{k}/receipt.json': link['sha256']
                for k, link in enumerate(declarations, 1)}
    if any(link['path'] != f'pre-receipts/{k}/receipt.json' for k, link in enumerate(declarations, 1)):
        raise ValueError('pre_receipt path is not campaign-local')
    retained = root / 'pre-receipts'
    if retained.is_symlink() or (retained.exists() and (
            not retained.is_dir() or {p.name for p in retained.iterdir()} !=
            {str(k) for k in range(1, len(declarations) + 1)})):
        raise ValueError('pre_receipt inventory differs from campaign declarations')
    if state.get('pre_receipts', {}) != expected:
        raise ValueError('pre_receipt anchors differ from campaign declarations')
    captured = None
    if declarations:
        if not state['history']:
            raise ValueError('pre_receipt has no first capture binding')
        first = root / 'runs' / '1'
        if Path(state['history'][0]['run']) != first:
            raise ValueError('pre_receipt requires campaign run 1')
        captured = read_json(first / 'candidate.json')['files']
    receipts = validate_pre_receipt_chain(root, declarations)
    if captured is not None:
        # Earlier receipts bind run 1 through the chain; the last one's after-tree is the capture.
        relation = pre_receipt_binding(receipts[-1], captured)
        if not relation['bound']:
            raise ValueError('pre_receipt output differs from first capture: ' +
                             ', '.join(relation['differences']))


def verify_campaign_receipts(root, state, steps=None, plans=None):
    """Campaign-level anchors; legacy campaigns without receipts keep verifying.

    plans, when given, maps each implementation name to its own expected ordered step list,
    as re-derived by the campaign verifier; otherwise every implementation shares steps."""
    root = Path(root)
    config = read_json(root / 'config.json')
    verify_pre_receipts(root, state, config.get('pre_receipts', []))
    anchors = state.get('stage_receipts')
    implementations = sorted((root / 'implementations').glob('*'), key=lambda p: p.name)
    indexed = {p.name for p in implementations if (p / 'stage-receipts.json').exists() or (p / 'receipts').exists()}
    if anchors is None:
        if indexed:
            raise ValueError('stage receipts exist without campaign anchors')
        return
    if not isinstance(anchors, dict) or set(anchors) != indexed | {
            p.name for p in implementations if (p / 'proposal.scope.json').exists()}:
        raise ValueError('stage receipt anchors differ from implementation records')
    history = state['history']
    for name, anchor in anchors.items():
        number = int(name)
        work = root / 'implementations' / name
        if not 1 <= number <= len(history) or str(number) != name:
            raise ValueError('stage receipt anchor names no campaign implementation')
        source = read_json(Path(history[number - 1]['run']) / 'candidate.json')
        captured = None
        if number < len(history):
            proposal = read_json(Path(history[number]['run']) / 'candidate.json')
            if proposal.get('scope_path') == str(work / 'proposal.scope.json'):
                captured = proposal['files']
        expected = steps if plans is None else plans[name]
        verify_implementation(work, anchor, number, source['candidate_id'], captured=captured, steps=expected,
                              complete=plans is not None and (work / 'proposal.scope.json').exists())

"""Coordinator-observed receipts for writer-side generation steps; provenance only.

The coordinator runs a declared step itself in the ordinary no-network sandbox and
hashes the tool, inputs, outputs and sandbox profile. Writer files and statements
are never receipts. A receipt never makes a check pass: acceptance still comes only
from fresh sealed checks of the captured proposal.
"""

import contextlib
import contextvars
import errno
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

from . import native_run
from .storage import digest, digest_fd, identity, read_json, write_json

STEP_SCHEMA = 'agent-ee.stage-step/1'
# /2 receipts are produced only by strict source observation (AGENTEE-357). A /1 receipt still
# replays unless its recorded source identity could be a link-only one (AGENTEE-374).
RECEIPT_SCHEMA = 'agent-ee.stage-receipt/2'
LEGACY_RECEIPT_SCHEMA = 'agent-ee.stage-receipt/1'
RECEIPT_SCHEMAS = (LEGACY_RECEIPT_SCHEMA, RECEIPT_SCHEMA)
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
# routing_adapter._step mounts these standalone helpers, and the pin data file, individually.
QUALIFIED_LIVE_HELPERS = frozenset({'kicad_route_attempt.py', 'net_binding.py', 'symbol_library.py',
                                    'data/kicad-route-pin.json'})
VERSION_TIMEOUT_S = 30
# Linux CLOCK_REALTIME_COARSE, the clock that stamps file status changes; not exported by time.
CLOCK_REALTIME_COARSE = 5
# Longest wait for the coarse clock to pass every live status time before a launch.
STATUS_FENCE_S = 2.5
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


class LivePackageSourceError(ValueError):
    """A declared mount exposes the running package's mutable source."""

    def __init__(self, at):
        self.violation = 'live_package_source:' + at
        super().__init__(self.violation)


class MountSourceChangedError(ValueError):
    """A validated mount source disappeared or changed its identity."""

    def __init__(self, at):
        self.violation = 'mount_source_changed:' + at
        super().__init__(self.violation)


class EscapingSourceError(ValueError):
    """A declared source tree resolves through a link that leaves its mount."""

    def __init__(self, at):
        self.violation = 'source_link_escape:' + at
        super().__init__(self.violation)


class LegacySourceIdentityError(ValueError):
    """A /1 receipt records a source identity replay cannot tell from an escaped link-only one."""

    def __init__(self, source):
        self.violation = 'legacy_source_identity:' + source
        super().__init__(self.violation)


class ReceiptCleanupError(ValueError):
    """A refused run's receipt directory could not be removed; ``refusal`` is why it was refused."""

    def __init__(self, path, refusal):
        self.violation = 'cleanup_failed:' + str(path)
        self.path, self.refusal = str(path), refusal
        super().__init__(f'{self.violation} (after {refusal!r})')


def _reopened(descriptor, flags=0):
    """A readable descriptor for the object an O_PATH descriptor holds, never a new lookup of its name."""
    return os.open(f'/proc/self/fd/{descriptor}', os.O_RDONLY | os.O_CLOEXEC | flags)


def _held_walk(directory, *, vanished=False):
    """Yield (relative path, directory descriptor, name, status) for every entry below a directory.

    Consumes the readable ``directory`` descriptor. Every entry is looked up as one component
    of the directory object just listed, without following a link, and a subdirectory is
    entered only through the object opened from that listing, which must be the entry
    observed, so a component swapped for a link or another directory can never redirect the
    walk. The yielded directory descriptor stays valid until the walk resumes. Lookup errors
    propagate; vanished=True skips an entry removed after listing.
    """
    stack = []
    try:
        stack.append(('', directory, iter(sorted(os.listdir(directory)))))
    finally:
        if not stack:
            os.close(directory)
    try:
        while stack:
            prefix, current, names = stack[-1]
            name = next(names, None)
            if name is None:
                os.close(current)
                stack.pop()
                continue
            try:
                observed = os.stat(name, dir_fd=current, follow_symlinks=False)
            except FileNotFoundError:
                if vanished:
                    continue
                raise
            yield prefix + name, current, name, observed
            if stat.S_ISDIR(observed.st_mode):
                try:
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=current)
                except FileNotFoundError:
                    if vanished:
                        continue
                    raise
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino):
                        raise OSError(errno.ESTALE, 'directory replaced during walk', prefix + name)
                    names = iter(sorted(os.listdir(child)))
                except BaseException:
                    os.close(child)
                    raise
                stack.append((prefix + name + '/', child, names))
    finally:
        for _, current, _ in stack:
            os.close(current)


def _entry_digest(directory, name):
    """Digest of the regular file ``name`` in a held directory, opened without following a link."""
    entry = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(entry).st_mode):
            raise OSError(errno.ESTALE, 'entry replaced during walk', name)
        # The held object's magic link reaches that same file, never a new lookup of its name.
        return digest(f'/proc/self/fd/{entry}')
    finally:
        os.close(entry)


def _live_root():
    package_source = Path(__file__).resolve(strict=True).parents[1]
    return os.open(package_source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)


def _live_source_identities():
    directory = _live_root()
    observed = os.fstat(directory)
    identities = {(observed.st_dev, observed.st_ino)}
    for _, _, _, observed in _held_walk(directory):
        identities.add((observed.st_dev, observed.st_ino))
    return identities


def _live_source_state():
    """Status-change time of every live non-directory entry, by device and inode.

    Only files can gain hard links. An entry that vanishes during the walk, such as a
    replaced bytecode file, is absent here; a later comparison refuses a baseline entry
    that is absent, never treating it as unchanged."""
    state = {}
    for _, _, _, observed in _held_walk(_live_root(), vanished=True):
        if not stat.S_ISDIR(observed.st_mode):
            state[observed.st_dev, observed.st_ino] = observed.st_ctime_ns
    return state


def _await_status_fence(state, at):
    """Wait until the stamping clock is in a later whole second than every baseline status time.

    Any later status change, such as a link or unlink, is then stamped strictly after its
    baseline time on every filesystem whose timestamp granularity divides one second, so a
    link operation in the baseline's own tick cannot leave the time unchanged."""
    second = 1_000_000_000
    fence = (max(state.values(), default=0) // second + 1) * second
    limit = time.monotonic() + STATUS_FENCE_S
    while time.clock_gettime_ns(CLOCK_REALTIME_COARSE) < fence:
        if time.monotonic() > limit:
            # A status time in the future cannot be fenced; refuse rather than compare it.
            raise LivePackageSourceError(at)
        time.sleep(.005)


def _mount_identity(path):
    observed = path.stat()
    return observed.st_dev, observed.st_ino


def _validated_mount_source(source, at, *, source_identity=None):
    resolved = Path(source).resolve(strict=True)
    package_source = Path(__file__).resolve(strict=True).parents[1]
    package = package_source / 'agent_ee'
    # Only the canonical, individually mounted routing helpers are qualified.
    if resolved.is_relative_to(package) and resolved.relative_to(package).as_posix() in QUALIFIED_LIVE_HELPERS:
        observed = resolved.stat()
        if (stat.S_ISREG(observed.st_mode) and
                (source_identity is None or (observed.st_dev, observed.st_ino) == source_identity)):
            return str(resolved)
    live_ancestors = {package_source, *package_source.parents}
    if resolved in live_ancestors or package_source in resolved.parents:
        raise LivePackageSourceError(at)
    source_identity = source_identity if source_identity is not None else _mount_identity(resolved)
    if source_identity in {_mount_identity(p) for p in live_ancestors}:
        raise LivePackageSourceError(at)
    # Source ancestors catch paths below a bind alias. Compare only with the
    # live tree here: common ancestors such as / must not reject unrelated tools.
    source_identities = {source_identity, *(_mount_identity(p) for p in resolved.parents)}

    live_identities = _live_source_identities()
    if source_identities & live_identities:
        raise LivePackageSourceError(at)
    if resolved.is_dir():
        _refuse_submounts(resolved, at)
    return str(resolved)


def _refuse_submounts(source, at):
    # Builders need plain tool trees, never recursive host mount hierarchies.
    # mountinfo catches same-device bind mounts which st_dev cannot distinguish.
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        if len(fields) < 10 or '-' not in fields[6:]:
            raise ValueError('malformed host mountinfo')
        mountpoint = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
        if mountpoint != str(source) and _under(mountpoint, str(source)):
            raise ValueError('mount_source_submount:' + at)


def _resolved_mounts(spec):
    mounts, identities = [], []
    for mount in spec['mounts']:
        try:
            resolved = Path(mount['source']).resolve(strict=True)
            observed = resolved.lstat()
            expected = observed.st_dev, observed.st_ino
            source = _validated_mount_source(str(resolved), mount['at'], source_identity=expected)
            if source != str(resolved) or not (stat.S_ISDIR(observed.st_mode) or
                                               stat.S_ISREG(observed.st_mode)):
                raise MountSourceChangedError(mount['at'])
        except FileNotFoundError as exc:
            raise ValueError('mount source does not exist: ' + mount['source']) from exc
        except OSError as exc:
            raise MountSourceChangedError(mount['at']) from exc
        mounts.append({'source': source, 'at': mount['at']})
        identities.append(expected)
    return {**spec, 'mounts': mounts}, identities


def _fd_mount_id(descriptor):
    values = [line.partition(':')[2].strip()
              for line in Path(f'/proc/self/fdinfo/{descriptor}').read_text().splitlines()
              if line.startswith('mnt_id:')]
    if len(values) != 1 or not values[0].isdecimal():
        raise ValueError('missing or malformed host mount id')
    return int(values[0])


def _refuse_fd_submounts(descriptor, at):
    # Anchor the inventory to the opened object, not a spelling which a rename
    # can redirect while mountinfo is read. Mount IDs distinguish same-device binds.
    expected = _fd_mount_id(descriptor)
    live_identities = _live_source_identities()
    for _, directory, name, _ in _held_walk(_reopened(descriptor, os.O_DIRECTORY)):
        child = os.open(name, os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory)
        try:
            if _fd_mount_id(child) != expected:
                raise ValueError('mount_source_submount:' + at)
            observed = os.fstat(child)
            if (observed.st_dev, observed.st_ino) in live_identities:
                raise LivePackageSourceError(at)
        finally:
            os.close(child)


@contextlib.contextmanager
def _opened_mounts(spec, identities):
    with contextlib.ExitStack() as stack:
        mounts = []
        for mount, expected in zip(spec['mounts'], identities):
            try:
                flags = os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW
                if Path(mount['source']).is_dir():
                    flags |= os.O_DIRECTORY
                descriptor = os.open(mount['source'], flags)
                stack.callback(os.close, descriptor)
                observed = os.fstat(descriptor)
                if ((observed.st_dev, observed.st_ino) != expected or
                        not (stat.S_ISDIR(observed.st_mode) or stat.S_ISREG(observed.st_mode))):
                    raise MountSourceChangedError(mount['at'])
                if stat.S_ISDIR(observed.st_mode):
                    _refuse_fd_submounts(descriptor, mount['at'])
            except OSError as exc:
                raise MountSourceChangedError(mount['at']) from exc
            mounts.append((descriptor, mount['at']))
        yield mounts


def _directory_mounts(spec):
    return [m['at'] for m in spec['mounts']
            if 'descriptor' in m and stat.S_ISDIR(os.fstat(m['descriptor']).st_mode)]


def _live_baseline(spec):
    """Live source status before launch, or None when no directory mount could carry a link."""
    directories = _directory_mounts(spec)
    if not directories:
        return None
    state = _live_source_state()
    _await_status_fence(state, ','.join(directories))
    return state


def _recheck_mounts(spec, identities, live=None):
    for mount, expected in zip(spec['mounts'], identities):
        source = Path(mount['source'])
        try:
            if (str(source.resolve(strict=True)) != mount['source'] or
                    _mount_identity(source) != expected):
                raise MountSourceChangedError(mount['at'])
            _validated_mount_source(mount['source'], mount['at'], source_identity=expected)
            # Entries remain mutable in place: re-inventory the held object for live links.
            if 'descriptor' in mount and stat.S_ISDIR(os.fstat(mount['descriptor']).st_mode):
                _refuse_fd_submounts(mount['descriptor'], mount['at'])
        except OSError as exc:
            raise MountSourceChangedError(mount['at']) from exc
    # Linking or unlinking a name for a live file always updates its ctime, which an
    # unprivileged process cannot restore, so a link planted and removed mid-run is seen.
    # The baseline fence keeps that update out of the baseline's own tick, and a baseline
    # entry no longer found (for example unlinked and replaced) is never unchanged.
    if live is not None:
        at = ','.join(_directory_mounts(spec))
        try:
            current = _live_source_state()
        except OSError as exc:
            raise LivePackageSourceError(at) from exc
        if any(current.get(key) != ctime for key, ctime in live.items()):
            raise LivePackageSourceError(at)
    source = spec.get('source')
    if source is not None:
        with _mount_observation(spec, source):
            if _escaping_mount_link(spec, source) is not None:
                raise EscapingSourceError(source)


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
        if host:
            if not Path(source).exists():
                raise ValueError(f'mount source does not exist: {source}')
            _validated_mount_source(source, at)
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
    return digest_fd(descriptor)


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


def _identity_tree(source, *, descriptor=None):
    """Identity of a read-only tool mount: file bytes and link targets.

    The tree is walked relative to the held root object (``descriptor``, or the named
    root opened once), never by a pathname below it."""
    source = Path(source)
    name = source.name
    if descriptor is not None:
        held = os.dup(descriptor)
        top = True
    else:
        held = os.open(source, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
        top = not stat.S_ISLNK(os.fstat(held).st_mode)
        if not top:
            # A named root may itself be a link to a directory; nothing below it is followed.
            os.close(held)
            held = os.open(source, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        mode = os.fstat(held).st_mode
        if stat.S_ISREG(mode) and top:
            entries = {name: digest(f'/proc/self/fd/{held}')}
        elif stat.S_ISDIR(mode):
            entries = {}
            for rel, directory, entry, observed in _held_walk(_reopened(held, os.O_DIRECTORY)):
                if stat.S_ISLNK(observed.st_mode):
                    entries[rel] = 'link:' + os.readlink(entry, dir_fd=directory)
                elif stat.S_ISREG(observed.st_mode):
                    entries[rel] = _entry_digest(directory, entry)
        else:
            raise NotADirectoryError(errno.ENOTDIR, 'tool tree is not a directory', str(source))
    finally:
        os.close(held)
    return {'sha256': identity(entries), 'files': len(entries)}


def tree_identity(source):
    """Identity of a retained tool tree in the form a receipt records it."""
    return _identity_tree(source)


def _host_path(spec, sandbox_path, *, held=True):
    for mount in spec['mounts']:
        if _under(sandbox_path, mount['at']):
            rel = PurePosixPath(sandbox_path).relative_to(mount['at'])
            source = (f"/proc/self/fd/{mount['descriptor']}"
                      if held and 'descriptor' in mount else mount['source'])
            return Path(source) / rel
    return Path(sandbox_path)


def _beneath(mount, sandbox_path):
    """('ok', O_PATH fd), ('missing', None) or ('escaped', link target) for a mount path.

    Each component is opened relative to its parent without following a link; a link is
    expanded only from the object just opened, so a component swapped for a link after
    any check is never followed out of the mount. The caller closes the descriptor.
    """
    flags = os.O_PATH | os.O_CLOEXEC
    try:
        root = (os.dup(mount['descriptor']) if 'descriptor' in mount else os.open(mount['source'], flags))
    except FileNotFoundError:
        return 'missing', None
    stack = [root]
    pending = [(part, None) for part in PurePosixPath(sandbox_path).relative_to(mount['at']).parts]
    links = 0
    try:
        while pending:
            part, origin = pending.pop(0)
            if part == '..':
                if len(stack) == 1:
                    return 'escaped', origin
                os.close(stack.pop())
                continue
            if part == '.':
                continue
            try:
                child = os.open(part, flags | os.O_NOFOLLOW, dir_fd=stack[-1])
            except (FileNotFoundError, NotADirectoryError):
                return 'missing', None
            if not stat.S_ISLNK(os.fstat(child).st_mode):
                stack.append(child)
                continue
            try:
                target = os.readlink('', dir_fd=child)
            finally:
                os.close(child)
            if PurePosixPath(target).is_absolute():
                return 'escaped', target
            links += 1
            if links > 40:
                raise OSError(errno.ELOOP, 'too many tool symlinks', sandbox_path)
            pending = [(part, target) for part in PurePosixPath(target).parts] + pending
        return 'ok', stack.pop()
    finally:
        for descriptor in stack:
            os.close(descriptor)


def _mount_of(spec, sandbox_path):
    return next((m for m in spec['mounts'] if _under(sandbox_path, m['at'])), None)


def _escaping_mount_link(spec, sandbox_path):
    """Return a link target if resolving this path would leave its mount namespace."""
    mount = _mount_of(spec, sandbox_path)
    if mount is None:
        return None
    state, value = _beneath(mount, sandbox_path)
    if state == 'ok':
        os.close(value)
    return value if state == 'escaped' else None


def _held_digest(descriptor):
    """Digest of the regular file behind an O_PATH descriptor, else None; consumes descriptor."""
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        # Reopening the descriptor's own magic link reaches the same object, never a new name.
        return _digest_fd(os.open(f'/proc/self/fd/{descriptor}', os.O_RDONLY | os.O_CLOEXEC))
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def _mount_observation(spec, sandbox_path):
    try:
        yield
    except OSError as exc:
        mount = next((m for m in spec['mounts'] if _under(sandbox_path, m['at'])), None)
        if mount is None:
            raise
        raise MountSourceChangedError(mount['at']) from exc


# A run observes its source strictly: the resolution that would record an escaped link-only
# identity refuses instead, so no new receipt can carry one. Standalone observation records it.
_STRICT_SOURCE = contextvars.ContextVar('strict_source', default=False)


@contextlib.contextmanager
def _strict_source():
    token = _STRICT_SOURCE.set(True)
    try:
        yield
    finally:
        _STRICT_SOURCE.reset(token)


def observe_tool(spec):
    """Coordinator-side tool identity; missing pieces are recorded as null."""
    host = _host_path(spec, spec['argv'][0])
    display = _host_path(spec, spec['argv'][0], held=False)
    mount = _mount_of(spec, spec['argv'][0])
    # Resolving an fd path back to its name would reintroduce the rename race.
    with _mount_observation(spec, spec['argv'][0]):
        if mount is None:
            resolved = display.resolve() if display.exists() else display
            sha = digest(host) if host.is_file() else None
        else:
            # The bytes hashed are the object resolution reached, not a later lookup of its name.
            state, value = _beneath(mount, spec['argv'][0])
            sha = _held_digest(value) if state == 'ok' else None
            resolved = (display.resolve() if host == display and state == 'ok' and display.exists()
                        else display)
        executable = {'path': spec['argv'][0], 'host_path': str(resolved), 'sha256': sha}
    mounts = []
    for mount in spec['mounts']:
        with _mount_observation(spec, mount['at']):
            observed = (_identity_tree(mount['source'], descriptor=mount.get('descriptor'))
                        if 'descriptor' in mount or Path(mount['source']).exists()
                        else {'sha256': None, 'files': 0})
            mounts.append({'at': mount['at'], **observed})
    source = None
    if spec['source'] is not None:
        with _mount_observation(spec, spec['source']):
            root = _host_path(spec, spec['source'])
            mount = next((m for m in spec['mounts'] if m['at'] == spec['source']), None)
            if mount is not None:
                observation = (_identity_tree(mount['source'], descriptor=mount.get('descriptor'))
                               if root.exists() else {'sha256': None, 'files': 0})
            else:
                state, value = _beneath(_mount_of(spec, spec['source']), spec['source'])
                if state == 'escaped':
                    if _STRICT_SOURCE.get():
                        raise EscapingSourceError(spec['source'])
                    # Recorded, never digested; a run's strict observation refuses it above.
                    observation = {'sha256': identity({root.name: 'link:' + value}), 'files': 1}
                elif state == 'missing':
                    observation = {'sha256': None, 'files': 0}
                else:
                    try:
                        mode = os.fstat(value).st_mode
                        if not (stat.S_ISDIR(mode) or (stat.S_ISREG(mode) and not root.is_symlink())):
                            raise NotADirectoryError(errno.ENOTDIR, 'source tree is not a directory',
                                                     spec['source'])
                        observation = _identity_tree(root, descriptor=value)
                    finally:
                        os.close(value)
            source = {'path': spec['source'], **observation}
    return {'executable': executable, 'mounts': mounts, 'source_tree': source}


def sandbox_profile(sandbox_argv):
    """Sandbox options before the tool argv, with per-run host bind sources normalized."""
    return native_run.parse_profile(sandbox_argv).normalized()


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
        if executions.get(name) is not None and executions[name].get('mount_guard') is False:
            violations.append('mount_guard_failed:' + name)
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
    for mount, observed in zip(spec['mounts'], tool['mounts']):
        expected = Path(mount['source']).name
        # A content-addressed mount declares its digest in its directory name.
        # Use the retained observation on replay, never the later live cache.
        if re.fullmatch(r'[0-9a-f]{64}', expected):
            source = tool['source_tree']
            if (observed['sha256'] != expected or
                    (spec['source'] == mount['at'] and source['sha256'] != expected)):
                violations.append('snapshot_hash_mismatch:' + mount['at'])
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


def _held_directory(parent, name):
    """Open directory ``name`` below ``parent`` for reading, first giving the owner rwx on it.

    The directory is acquired without following links before its mode is touched, and the mode is
    changed on that held object, never by name, so a name swapped for a link never reaches a host
    directory. Raises OSError for anything that is not, or is no longer, that directory.
    """
    path = os.open(name, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try:
        held = os.fstat(path)
        if held.st_mode & stat.S_IRWXU != stat.S_IRWXU:
            # An O_PATH descriptor cannot be fchmod'ed; its /proc magic link names the held object.
            os.chmod(f'/proc/self/fd/{path}', stat.S_IMODE(held.st_mode) | stat.S_IRWXU)
        fd = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=path)
    finally:
        os.close(path)
    opened = os.fstat(fd)
    if (opened.st_dev, opened.st_ino) != (held.st_dev, held.st_ino):
        os.close(fd)
        raise OSError(errno.ESTALE, 'directory replaced during cleanup', name)
    return fd


def _empty_directory(fd):
    """Unlink every non-directory entry of the held directory ``fd``; return its subdirectory names."""
    with os.scandir(fd) as entries:
        listed = [(entry.name, entry.is_dir(follow_symlinks=False)) for entry in entries]
    for name, is_dir in listed:
        if not is_dir:
            os.unlink(name, dir_fd=fd)
    return [name for name, is_dir in listed if is_dir]


def _remove_tree(parent, name):
    """Remove directory ``name`` below ``parent`` and everything beneath it, without following links.

    The walk is iterative and holds one directory at a time, so tool-created nesting is bounded
    by neither the interpreter's recursion limit nor the descriptor limit. Each return to a parent
    goes through ``..`` of the held child and must reach the parent object held before.
    """
    fd = _held_directory(parent, name)
    try:
        held = os.fstat(fd)
        frames = [(name, (held.st_dev, held.st_ino), _empty_directory(fd))]
        while True:
            pending = frames[-1][2]
            if pending:
                child = pending.pop()
                child_fd = _held_directory(fd, child)
                os.close(fd)
                fd = child_fd
                held = os.fstat(fd)
                frames.append((child, (held.st_dev, held.st_ino), _empty_directory(fd)))
                continue
            child = frames.pop()[0]
            if not frames:
                break
            up = os.open('..', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = up
            held = os.fstat(fd)
            if (held.st_dev, held.st_ino) != frames[-1][1]:
                raise OSError(errno.ESTALE, 'directory moved during cleanup', child)
            os.rmdir(child, dir_fd=fd)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent)


def _discard(receipt_dir, refusal):
    """Remove a refused run's receipt directory, or raise ReceiptCleanupError naming it.

    The tool may have left directories unwritable or unsearchable, so owner access is restored
    on each held directory before it is read. A directory that cannot be removed is reported,
    never silently left unlabelled.
    """
    receipt_dir = Path(receipt_dir)
    try:
        parent = os.open(receipt_dir.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            _remove_tree(parent, receipt_dir.name)
        finally:
            os.close(parent)
    except OSError as exc:
        # Only an already absent receipt directory is a completed cleanup.
        if not isinstance(exc, FileNotFoundError) or os.path.lexists(receipt_dir):
            raise ReceiptCleanupError(receipt_dir, refusal) from exc
    if os.path.lexists(receipt_dir):
        raise ReceiptCleanupError(receipt_dir, refusal)


def run_step(receipt_dir, design, spec, binding, *, deadline=None, cancellation=None, skip_visuals=None):
    """Execute one declared step on a copy of design; never modifies design itself.

    skip_visuals is the executor's typed candidate-imaging policy for the step sandbox
    (AGENTEE-458); None leaves the sandbox argv unchanged."""
    from .executor import visual_policy
    from .tool_source import hold_snapshots
    visual_policy(skip_visuals)
    # Acquire before host validation/observation so pruning cannot interleave.
    validated = validate_step(spec, host=False)
    with hold_snapshots(validated['mounts']):
        return _run_step(receipt_dir, design, validated, binding,
                         deadline=deadline, cancellation=cancellation, skip_visuals=skip_visuals)


def _run_step(receipt_dir, design, spec, binding, *, deadline=None, cancellation=None, skip_visuals=None):
    spec = validate_step(spec, host=False)
    runtime_spec, mount_identities = _resolved_mounts(spec)
    with _opened_mounts(runtime_spec, mount_identities) as mounts:
        runtime_spec = {**runtime_spec, 'mounts': [
            {**mount, 'descriptor': descriptor}
            for mount, (descriptor, _) in zip(runtime_spec['mounts'], mounts)]}
        return _run_bound_step(receipt_dir, design, spec, binding, runtime_spec,
                               mount_identities, mounts, deadline=deadline, cancellation=cancellation,
                               skip_visuals=skip_visuals)


def _run_bound_step(receipt_dir, design, spec, binding, runtime_spec, mount_identities, mounts,
                    *, deadline=None, cancellation=None, skip_visuals=None):
    if not isinstance(binding, dict):
        raise ValueError('stage receipt binding must be an object')
    timeout = spec['timeout_s']
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise ValueError('stage step deadline exhausted before launch')
    _strict_tree(design)
    # A routing input's owning context is lost once design is copied (AGENTEE-442 review H2).
    from .routing_adapter import input_refusal
    refusal = input_refusal(spec, design)
    if refusal is not None:
        raise ValueError(refusal)
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
        live = _live_baseline(runtime_spec)
        with _strict_source():
            tool = observe_tool(runtime_spec)
        _recheck_mounts(runtime_spec, mount_identities, live)
    except BaseException as exc:
        # Nothing was executed or observed; a partial copy is not evidence.
        _discard(receipt_dir, exc)
        raise
    try:
        if spec['version_argv'] is not None:
            version_record = dict(native_run.run(
                native_run.Intent('version', spec['version_argv'], timeout=max(.001, min(VERSION_TIMEOUT_S, timeout))),
                inputs=receipt_dir / 'inputs', work=receipt_dir / 'version', mounts=mounts, cancel=cancellation))
        _recheck_mounts(runtime_spec, mount_identities, live)
        # An exhausted budget is recorded as the step's own observed timeout.
        execution = dict(native_run.run(
            native_run.Intent('stage', spec['argv'], timeout=max(.001, timeout - (time.monotonic() - start)),
                              skip_visuals=skip_visuals),
            inputs=receipt_dir / 'inputs', work=run, mounts=mounts, cancel=cancellation))
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
    try:
        _recheck_mounts(runtime_spec, mount_identities, live)
        with _strict_source():
            tool_after = observe_tool(runtime_spec)
    except EscapingSourceError as exc:
        # An escaped source never yields a receipt; what the tool left behind is not evidence.
        _discard(receipt_dir, exc)
        raise
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
    if not isinstance(sandbox, list) or '--' not in sandbox:
        raise ValueError('stage receipt sandbox lacks the exact argv or network isolation')
    profile = native_run.parse_profile(sandbox)
    if list(profile.tool_argv) != argv or profile.network:
        raise ValueError('stage receipt sandbox lacks the exact argv or network isolation')
    if profile.guarded and type(record.get('mount_guard')) is not bool:
        raise ValueError('stage receipt lacks a coordinator mount guard observation')
    if (not any(bind.option == '--ro-bind' and bind.dest == '/candidate' for bind in profile.binds) or
            not any(bind.option == '--bind' and bind.dest == '/work' for bind in profile.binds)):
        raise ValueError('stage receipt sandbox lacks read-only candidate or scratch binding')
    for name, key in (('stdout.log', 'stdout_sha256'), ('stderr.log', 'stderr_sha256'),
                      ('console.log', 'console_sha256')):
        state, sha = records[name]
        if state != 'unsafe' and sha != record.get(key):
            raise ValueError('stage receipt execution artifacts changed')


def legacy_source_refusal(receipt):
    """Refuse a /1 receipt whose source identity replay cannot distinguish from a link-only one.

    Before AGENTEE-357 a run recorded an escaped declared source as a one-entry link
    identity whose target is not retained, which is indistinguishable from a one-file
    source tree. A /1 receipt with a nested source (not a mount root) and a one-entry
    source identity in either observation is refused with a typed reason; it is never
    rewritten or reinterpreted. A missing or malformed count (not a non-negative int) in
    either observation cannot show it is outside that class and refuses the same way.
    /2 receipts come only from strict source observation."""
    if not isinstance(receipt, dict) or receipt.get('schema') != LEGACY_RECEIPT_SCHEMA:
        return
    spec = receipt.get('spec')
    if not isinstance(spec, dict) or not isinstance(spec.get('source'), str):
        return
    mounts = spec.get('mounts')
    if isinstance(mounts, list) and any(isinstance(m, dict) and m.get('at') == spec['source'] for m in mounts):
        return
    observed = [value.get('source_tree') for value in (receipt.get('tool'), receipt.get('tool_after'))
                if isinstance(value, dict)]
    if len(observed) != 2 or any(not isinstance(tree, dict) or type(tree.get('files')) is not int or
                                 tree['files'] < 0 or tree['files'] == 1 for tree in observed):
        raise LegacySourceIdentityError(spec['source'])


def validate_receipt(receipt_dir, *, binding=None):
    """Recompute every derivable receipt field from retained coordinator records."""
    receipt_dir = Path(receipt_dir)
    receipt = read_json(receipt_dir / 'receipt.json')
    if not isinstance(receipt, dict) or receipt.get('schema') not in RECEIPT_SCHEMAS or set(receipt) != RECEIPT_KEYS:
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
    legacy_source_refusal(receipt)
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
    if 'via_budget_pilot' in receipt['binding']:
        raise ValueError('a diagnostic via-budget pilot attempt is never adoptable')
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
                from .rating_lineage import delivered_view
                # The coordinator rating capture is verified against its lineage, not the delivered design.
                delivered = read_json(work / 'stage-receipts.json')['delivered']
                captured = delivered_view(config, proposal['files'], delivered)
                from .layer_policy_lineage import delivered_view as layer_policy_view
                # So is the coordinator capture of a pre-routing layer policy derivation (AGENTEE-260).
                captured = layer_policy_view(config, captured, delivered)
                from .placement_defects import delivered_view as placement_view
                captured = placement_view(captured, delivered)
        expected = steps if plans is None else plans[name]
        verify_implementation(work, anchor, number, source['candidate_id'], captured=captured, steps=expected,
                              complete=plans is not None and (work / 'proposal.scope.json').exists())

"""Frozen build testimony for adjudication, separate from the reviewed candidate."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import time

from . import native_run, qualification, requirement_map, retained_run
from .catalog import strict_json
from .storage import digest, json_bytes, read_json

DIRECTORY = 'adjudication-judgments'
SCHEMA = 'agent-ee.judgment-context/1'
DIAGNOSTIC_SCHEMA = 'agent-ee.diagnostic-judgment-context/1'
MAX_FILES = 256
MAX_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 2 * MAX_FILES


@dataclass(frozen=True)
class Source:
    root: Path
    stage: Path
    candidate: Path
    diagnostic: bool = False


checkpoint = native_run.checkpoint


def _bytes(path, deadline=None, limit=MAX_BYTES):
    checkpoint(deadline)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('judgment context requires bounded regular files')
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('judgment context file exceeds byte limit')
    checkpoint(deadline)
    return raw


def _files(root, deadline=None, limit=MAX_BYTES):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('judgment context requires an unaliased directory')
    files, total, entries, pending = {}, 0, 0, [root]
    while pending:
        with os.scandir(pending.pop()) as children:
            for entry in children:
                checkpoint(deadline)
                entries += 1
                if entries > MAX_ENTRIES:
                    raise ValueError('judgment context exceeds directory entry limit')
                path = Path(entry.path)
                if entry.is_symlink():
                    raise ValueError('judgment context contains an alias')
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                    continue
                if len(files) >= MAX_FILES:
                    raise ValueError('judgment context exceeds file limit')
                raw = _bytes(path, deadline, limit - total)
                total += len(raw)
                files[path.relative_to(root).as_posix()] = raw
    return {name: files[name] for name in sorted(files)}


def _pins(files):
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}


def capture(run, source, manifest, run_id, deadline):
    """Select the build's sealed receipt and freeze exact bytes within its review budget."""
    checkpoint(deadline)
    root = Path(source.root).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError('judgment build source contains an alias')
    stage = retained_run.retained_path(root, Path(source.stage).relative_to(root).as_posix())
    candidate = retained_run.retained_path(root, Path(source.candidate).relative_to(root).as_posix())
    poll = lambda: checkpoint(deadline)
    report = retained_run.snapshot(root, checkpoint=poll)
    plan = retained_run.read_plan(root, report)
    if plan is None or plan['schema'] != qualification.TYPED_SCHEMA:
        raise ValueError('judgment context requires a typed retained build')
    packages = [row for row in [*report['completed'], *report['attempts']]
                if row['stage'] == 'package' and root / row['directory'] == stage]
    if not packages or candidate != stage / 'candidate':
        raise ValueError('judgment context is not the selected package candidate')
    selected = retained_run.scope(root, report)
    diagnostic_pin = None
    if source.diagnostic:
        last = report['completed'][-1]
        if last['stage'] != 'package' or root / last['directory'] != stage or root / last['candidate'] != candidate:
            raise ValueError('diagnostic judgment context requires the selected completed package')
        expected = json_bytes(retained_run.review_selection(root, report, selected))
        if _bytes(Path(run) / 'inputs' / retained_run.REVIEW_SOURCE, deadline) != expected:
            raise ValueError('diagnostic judgment provenance differs from the retained build selection')
        diagnostic_pin = hashlib.sha256(expected).hexdigest()
    scaffold = retained_run.completed_stage(root, 'scaffold', report)
    accounting = retained_run.completed_stage(root, 'requirements', report)
    paths = {'scope.json': selected.path, 'requirements.json': scaffold.directory / 'requirements.json',
             'accounting.json': accounting.directory / 'accounting.json', 'plan.json': root / 'build-plan.json',
             'applicability-inventory.json': stage / 'applicability-inventory.json',
             'judgment-inventory.json': stage / 'judgment-inventory.json'}
    files, total = {}, 0
    for name, path in paths.items():
        raw = _bytes(path, deadline, MAX_BYTES - total)
        files[name] = raw
        total += len(raw)
    files.update({'judgments/' + name: raw for name, raw in
                  _files(stage / 'judgments', deadline, MAX_BYTES - total).items()})
    record = dict(schema=SCHEMA, candidate_id=manifest['candidate_id'], run_id=run_id,
                  build_identity=report['identity'], files=_pins(files))
    if diagnostic_pin is not None:
        record.update(schema=DIAGNOSTIC_SCHEMA, diagnostic_source_sha256=diagnostic_pin)
    record_raw = json_bytes(record)
    if len(files) >= MAX_FILES or sum(map(len, files.values())) + len(record_raw) > MAX_BYTES:
        raise ValueError('judgment context exceeds capture limits')
    receipt = strict_json(files['requirements.json'])
    qualification._bind_receipt(root, receipt, plan, provisional=False,
        accounting=strict_json(files['accounting.json'])['requirements'])
    expected_files = requirement_map.input_inventory(candidate, checkpoint=poll)
    if diagnostic_pin is not None:
        if any(name == 'build-review' or name.startswith('build-review/') for name in expected_files):
            raise ValueError('package occupies reserved diagnostic provenance namespace')
        expected_files[retained_run.REVIEW_SOURCE] = diagnostic_pin
    if expected_files != manifest['files']:
        raise ValueError('judgment package differs from review candidate files')
    directory = Path(run) / DIRECTORY
    directory.mkdir(exist_ok=False)
    for name, raw in files.items():
        checkpoint(deadline)
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    (directory / 'manifest.json').write_bytes(record_raw)
    link = dict(path=DIRECTORY + '/manifest.json', sha256=digest(directory / 'manifest.json'))
    _read(Path(run), link, manifest, run_id, deadline)
    if any(_bytes(path, deadline) != files[name] for name, path in paths.items()):
        raise ValueError('judgment source changed during capture')
    if _pins(_files(stage / 'judgments', deadline)) != strict_json(files['judgment-inventory.json']):
        raise ValueError('frozen judgment source changed during capture')
    return link


def _read(run, link, manifest, run_id, deadline=None):
    if (not isinstance(link, dict) or set(link) != {'path', 'sha256'}
            or link['path'] != DIRECTORY + '/manifest.json'):
        raise ValueError('invalid judgment context binding')
    directory = run / DIRECTORY
    files = _files(directory, deadline)
    raw = files.pop('manifest.json', None)
    if raw is None or hashlib.sha256(raw).hexdigest() != link['sha256']:
        raise ValueError('judgment context manifest changed or disappeared')
    context = strict_json(raw)
    diagnostic = context.get('schema') == DIAGNOSTIC_SCHEMA
    fields = {'schema', 'candidate_id', 'run_id', 'build_identity', 'files'}
    if diagnostic:
        fields.add('diagnostic_source_sha256')
    if (set(context) != fields
            or context['schema'] not in (SCHEMA, DIAGNOSTIC_SCHEMA) or context['candidate_id'] != manifest['candidate_id']
            or context['run_id'] != run_id or context['files'] != _pins(files)):
        raise ValueError('judgment context identity or file inventory changed')
    plan = strict_json(files['plan.json'])
    receipt = strict_json(files['requirements.json'])
    original = strict_json(files['scope.json'])
    scope = manifest['scope']
    without_inputs = lambda value: {k: v for k, v in value.items() if k not in ('inputs', 'review_profile')}
    if without_inputs(original) != without_inputs(scope):
        raise ValueError('judgment authored scope differs from reviewed criteria')
    if not diagnostic and 'review_profile' in plan and scope.get('review_profile') != read_json(run / 'inputs' / plan['review_profile']):
        raise ValueError('judgment context differs from captured review profile')
    inputs = run / 'inputs'
    diagnostic_pin = context.get('diagnostic_source_sha256')
    if diagnostic:
        source = strict_json(_bytes(inputs / retained_run.REVIEW_SOURCE, deadline))
        if (manifest['files'].get(retained_run.REVIEW_SOURCE) != diagnostic_pin
                or source.get('schema') != 'agent-ee.build-review-source/1'
                or source.get('qualification_mode') != 'typed'
                or source.get('build_identity') != context['build_identity']
                or source.get('scope_sha256') != context['files']['scope.json']
                or source.get('selected', {}).get('stage') != 'package'):
            raise ValueError('diagnostic judgment source identity differs from captured provenance')
    requirement_map.bind_receipt(receipt, (inputs / plan['requirements']['spec']).read_bytes(),
        source_hashes={field: manifest['files'][plan['requirements'][name]] for name, field in
            [('map', 'map_sha256'), ('record', 'spec_record_sha256'), ('spec', 'spec_sha256'),
             ('obligations', 'obligations_sha256')]},
        accounting=strict_json(files['accounting.json'])['requirements'])
    binding = dict(board_sha256=manifest['files'][scope['board']], scope_sha256=context['files']['scope.json'])
    checkpoint(deadline)
    judgments = qualification._judgments(run, directory, dict(receipt, candidate='inputs'), binding, plan,
        checkpoint=lambda: checkpoint(deadline), diagnostic_source_sha256=diagnostic_pin)
    checkpoint(deadline)
    return dict(schema=context['schema'], binding=binding, judgments=judgments, context=link)


def read(run, *, result=None, deadline=None):
    """Replay retained admission only; never consult or repair the original build."""
    run = Path(run)
    if (result is None and not (run / 'result.json').exists()
            and not (run / DIRECTORY).exists() and not (run / DIRECTORY).is_symlink()):
        return None
    result = read_json(run / 'result.json') if result is None else result
    link = result.get('judgment_context')
    if link is None:
        if (run / DIRECTORY).exists() or (run / DIRECTORY).is_symlink():
            raise ValueError('unbound judgment context')
        return None
    from .candidate import verify_snapshot
    return _read(run, link, verify_snapshot(run, checkpoint=lambda: checkpoint(deadline)), result['run_id'], deadline)


def citation_paths(run):
    """Only an admitted retained context supplies adjudicator citation membership."""
    if read(run) is None:
        return ()
    return tuple(DIRECTORY + '/' + name for name in _files(Path(run) / DIRECTORY))


def prompt(run, *, tools, deadline):
    context = read(run, deadline=deadline)
    if context is None:
        return None
    if not tools:
        return context
    return dict(context=context['context'], binding=context['binding'],
        access='/evidence/' + DIRECTORY + '/',
        judgments=[{**{key: row[key] for key in ('id', 'span_id', 'status', 'witness')},
                    'path': DIRECTORY + '/judgments/' + row['path'],
                    **{key: row[key] for key in ('sha256', 'reason') if key in row}}
                   for row in context['judgments']],
        authority='Recorded testimony and concurrence are not passing checks, a vote, physical qualification, '
                  'or closure of an independent concern. Read the bound records and original sources; '
                  'retain unresolved uncertainty under the ordinary adjudication rules.')

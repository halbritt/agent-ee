"""Custody and access for a build's retained run and stage records."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import sys
import uuid

from . import build_plan, requirement_map
from .catalog import strict_json
from .stage_receipts import _relative, tree_identity
from .storage import digest, identity, publish, read_json, write_json
from .run_record import run_review, verify

REPORT_SCHEMA = 'agent-ee.build-report/1'
TYPED_SCHEMA = build_plan.TYPED_SCHEMA
STAGES = ('requirements', 'parts', 'scaffold', 'schematic', 'placement-routing', 'artwork', 'package')
_inventory = requirement_map.input_inventory


class QualificationError(ValueError):
    """A current claim cannot be reconstructed from the retained evidence."""
    def __init__(self, message, *, integrity=False):
        self.integrity = integrity
        super().__init__(message)


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('build declaration fields differ from ' + ', '.join(required))


def retained_path(root, relative):
    path = root / _relative(relative, 'retained build path')
    if path == root or not path.resolve().is_relative_to(root):
        raise ValueError('retained build path escapes its run')
    for component in [path, *path.parents]:
        if component == root:
            break
        if component.is_symlink():
            raise ValueError('retained build path contains a link')
    return path


def snapshot(root):
    report = strict_json(retained_path(root, 'build-report.json').read_bytes())
    if report.get('schema') != REPORT_SCHEMA or report.get('identity') != identity(report['inputs']):
        raise QualificationError('build identity differs from captured inputs', integrity=True)
    if _inventory(retained_path(root, 'source')) != report['inputs']['source']:
        raise QualificationError('retained build source identity changed', integrity=True)
    completed = report['completed']
    if len(completed) > len(STAGES):
        raise QualificationError('completed stage inventory is invalid')
    for index, row in enumerate(completed):
        directory = retained_path(root, row['directory'])
        candidate = retained_path(root, row['candidate'])
        if (row['stage'] != STAGES[index] or candidate == directory or not candidate.is_relative_to(directory)
                or tree_identity(directory) != row['tree']):
            raise QualificationError('completed stage identity changed: ' + row['stage'], integrity=True)
    if completed and report.get('candidate') != completed[-1]['candidate']:
        raise QualificationError('candidate differs from the last completed stage')
    return report


def read_plan(root, report):
    path = retained_path(root, 'build-plan.json')
    if not path.exists():
        return None
    if digest(path) != report['inputs']['plan_sha256']:
        raise QualificationError('captured build plan identity changed', integrity=True)
    return build_plan.validate_plan(strict_json(path.read_bytes()))


def verify_retained(out):
    """Verify retained custody independently of a proposed resume invocation."""
    root = Path(out)
    try:
        report = snapshot(root)
        plan = read_plan(root, report)
        if plan is None and 'qualification' in report:
            raise ValueError('captured build plan is missing from a qualified run')
        if report.get('accepted') and len(report['completed']) != len(STAGES):
            raise ValueError('accepted build has no complete stage inventory')
        if 'judgment_inventory' in report:
            frozen = report['judgment_inventory']
            path = retained_path(root, frozen['path'])
            if digest(path) != frozen['sha256'] or _inventory(path.parent / 'judgments') != read_json(path):
                raise ValueError('retained judgment inventory changed after freeze')
        return plan
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise QualificationError(str(exc), integrity=True) from exc


def checked_review(root, report, *, host=None):
    candidates = list(reversed(report['completed']))
    if len(report['completed']) == len(STAGES) - 1:
        for attempt in reversed(report['attempts']):
            if (attempt['stage'] == 'package' and attempt['status'] in ('failed', 'interrupted')
                    and attempt.get('identity') == report['identity']):
                candidates.insert(0, dict(stage='package', directory=attempt['directory'],
                                         candidate=attempt['directory'] + '/candidate'))
                break
    for stage in candidates:
        directory = retained_path(root, stage['directory'])
        review = directory / 'review'
        if not (review / 'result.json').exists():
            continue
        result_sha256 = digest(review / 'result.json')
        result = verify_stage_review(directory, **({} if host is None else {'host': host}))
        if digest(review / 'result.json') != result_sha256:
            raise QualificationError('Ordinary review changed during verification', integrity=True)
        manifest = read_json(review / 'candidate.json')
        review_scope = manifest['scope']
        candidate = retained_path(root, stage['candidate'])
        board_hash = manifest['files'].get(review_scope.get('board'))
        if review_scope.get('board') and board_hash != digest(retained_path(candidate, review_scope['board'])):
            raise QualificationError('review board differs from its sealed stage candidate')
        selected_stage = CompletedStage(stage['stage'], directory, candidate, stage)
        scope_path = scope(root, report, review_stage=selected_stage).path
        binding = dict(board_sha256=board_hash, scope_sha256=digest(scope_path), stage=stage['stage'])
        return CheckedReview(directory, result, review_scope, binding, result_sha256)
    return None


def review_candidate(scope, directory, timeout, profile=None):
    """Use the generated acceptance scope against this actual native candidate.

    Without a profile this is the checks-only review; with one, the ordinary
    isolated reviewers and adjudicator also run."""
    path = scope_path(directory)
    write_scope(directory, scope)
    run_review(path, directory / 'review', timeout, checks_only=profile is None, profile=profile,
               output_stream=sys.stderr)
    return verify_stage_review(directory)



def verify_stage_review(directory, *, host=None):
    return verify(directory / 'review', **({} if host is None else {'host': host}))


def verify_review(review, *, host=None):
    """Recheck the selected review before publishing an assessment."""
    if (verify_stage_review(review.directory, **({} if host is None else {'host': host})) != review.result
            or digest(review.directory / 'review/result.json') != review.result_sha256):
        raise QualificationError('Ordinary review evidence changed during assessment', integrity=True)


def diagnostic_review(root, result, selection):
    manifest = read_json(root / 'review/candidate.json')
    binding = dict(board_sha256=manifest['files'].get(manifest['scope'].get('board')),
        scope_sha256=selection['scope_sha256'], stage=selection['selected']['stage'])
    return CheckedReview(root, result, manifest['scope'], binding)


def requirements(root, report, typed):
    scaffold_row = next((row for row in report['completed'] if row['stage'] == 'scaffold'), None)
    if scaffold_row:
        return read_json(retained_path(root, scaffold_row['directory']) / 'requirements.json')
    rows = accounting(root, report)['requirements']
    return dict(schema='agent-ee.requirement-map-receipt/' + ('2' if typed else '1'), requirements=rows,
                **({'method_binding': 'typed'} if typed else {}))


def review_source(run):
    """Only receipted completed stages may supply a diagnostic candidate."""
    path = retained_path(run, 'build-report.json')
    report = strict_json(path.read_bytes())
    if not isinstance(report, dict) or report.get('schema') != REPORT_SCHEMA:
        raise ValueError('expected a retained agent-ee build report')
    inputs = report.get('inputs')
    _keys(inputs, ('plan_sha256', 'source', 'runtime'))
    if report.get('identity') != identity(inputs) or _inventory(retained_path(run, 'source')) != inputs['source']:
        raise ValueError('retained build source identity changed')
    completed = report.get('completed')
    if not isinstance(completed, list) or not 4 <= len(completed) <= len(STAGES):
        raise ValueError('build review requires completed native schematic generation')
    for index, row in enumerate(completed):
        _keys(row, ('stage', 'directory', 'candidate', 'tree'))
        directory = retained_path(run, row['directory'])
        candidate = retained_path(run, row['candidate'])
        if candidate == directory or not candidate.is_relative_to(directory):
            raise ValueError('build candidate is outside its completed stage')
        if row['stage'] != STAGES[index] or tree_identity(directory) != row['tree']:
            raise ValueError('completed build stage identity changed: ' + str(row['stage']))
    if report.get('candidate') != completed[-1]['candidate']:
        raise ValueError('build candidate differs from its last completed stage')
    candidate = retained_path(run, completed[-1]['candidate'])
    selected_scope = scope(run, report)
    scope_path = selected_scope.path
    scope_document = strict_json(scope_path.read_bytes())
    selection = dict(schema='agent-ee.build-review-source/1', build_run=str(run),
        build_report_sha256=digest(path), build_identity=report['identity'],
        build_runtime=inputs['runtime'], selected=completed[-1],
        scope_sha256=digest(scope_path), scaffold=selected_scope.stage.record)
    plan_path = retained_path(run, 'build-plan.json')
    if plan_path.exists():
        if digest(plan_path) != inputs['plan_sha256']:
            raise ValueError('captured build plan identity changed')
        selection['qualification_mode'] = 'typed' if strict_json(plan_path.read_bytes())['schema'] == TYPED_SCHEMA else 'legacy'
    return ReviewSource(candidate, scope_document, selection)


@dataclass(frozen=True)
class CompletedStage:
    name: str
    directory: Path
    candidate: Path
    record: dict


@dataclass(frozen=True)
class Scope:
    path: Path
    stage: CompletedStage

    @property
    def document(self):
        return read_json(self.path)


@dataclass(frozen=True)
class CheckedReview:
    directory: Path
    result: dict
    scope: dict
    binding: dict
    result_sha256: str | None = None


@dataclass(frozen=True)
class ReviewSource:
    candidate: Path
    scope: dict
    selection: dict


def load_report(root):
    return read_json(retained_path(root, 'build-report.json'))


def write_report(root, report):
    write_json(retained_path(root, 'build-report.json'), report)


def initialize(root, pin):
    report = dict(schema=REPORT_SCHEMA, identity=identity(pin), inputs=pin, status='running', accepted=False,
                  completed=[], attempts=[], candidate=None)
    write_report(root, report)
    return report


def completed(root, report=None):
    if report is None:
        report = load_report(root)
    return [CompletedStage(row['stage'], retained_path(root, row['directory']),
                           retained_path(root, row['candidate']), row) for row in report['completed']]


def completed_stage(root, name, report=None):
    return next((row for row in completed(root, report) if row.name == name), None)


def scope(root, report=None, *, review_stage=None):
    """Locate the current scope once, including the pre-scaffold review case."""
    stages = completed(root, report)
    scaffold = next((row for row in stages if row.name == 'scaffold'), None)
    if scaffold is not None:
        selected = scaffold
        source = selected.directory / 'product/scope.json'
    elif review_stage is not None:
        selected = review_stage
        source = selected.directory / 'scope.json'
    else:
        selected = next((row for row in stages if row.name == 'requirements'), None)
        if selected is None:
            return None
        source = selected.directory / 'scope.json'
    return Scope(source, selected)


def accounting(root, report=None):
    requirements_stage = completed_stage(root, 'requirements', report)
    return (read_json(requirements_stage.directory / 'accounting.json') if requirements_stage is not None
            else dict(requirements=[]))


def qualification(root):
    return read_json(retained_path(root, 'qualification.json'))


def write_qualification(root, record):
    target = retained_path(root, 'qualification.json')
    temporary = root / ('.qualification-' + uuid.uuid4().hex)
    try:
        write_json(temporary, record)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return record


def write_scope(directory, document):
    write_json(scope_path(directory), document)


def write_planning_scope(directory, document):
    path = directory / 'planning.scope.json'
    write_json(path, document)
    return path


def write_accounting(directory, document):
    write_json(directory / 'accounting.json', document)


def write_requirements(directory, document):
    write_json(directory / 'requirements.json', document)


def record_completion(root, report, attempt, candidate):
    report['candidate'] = str(candidate.relative_to(root))
    report['completed'].append(dict(stage=attempt.name, directory=str(attempt.directory.relative_to(root)),
        candidate=report['candidate'], tree=tree_identity(attempt.directory)))


@dataclass(frozen=True)
class StageAttempt:
    root: Path
    name: str
    directory: Path

    def capture(self, previous):
        candidate = self.directory / 'candidate'
        shutil.copytree(previous, candidate)
        return candidate

    def execute(self, plan, previous, *, diagnostic=False):
        # Stage producers depend on this record interface, never on the build controller.
        from . import build_stages
        return build_stages.execute(self, plan, previous, diagnostic=diagnostic)


def begin_stage(root, name, *, index=None):
    if name not in STAGES:
        raise ValueError('unknown build stage')
    if index is None:
        index = STAGES.index(name)
    directory = root / 'stages' / f'{index + 1:02d}-{name}-{uuid.uuid4().hex}'
    directory.mkdir(parents=True)
    return StageAttempt(root, name, directory)


def capture_source(root, source, plan, source_files):
    """Capture the preflight inventory, pruning late-bound judgments before reading."""
    if (root / 'source').exists():
        return
    capturing = root / ('.capture-' + uuid.uuid4().hex)
    prefix = plan.get('judgments')
    omitted = source / prefix if prefix else None
    def ignore(directory, names):
        return {omitted.name} if omitted is not None and Path(directory) == omitted.parent else set()
    shutil.copytree(source, capturing, ignore=ignore)
    if _inventory(capturing) != source_files:
        raise ValueError('build input changed during capture')
    capturing.rename(root / 'source')


def read_scope(directory, *, product=False):
    return read_json(scope_path(directory, product=product))


def qualification_digest(root):
    return digest(retained_path(root, 'qualification.json'))


def archive_records(out, reason):
    """Retain the exact previous root records before superseding their claims."""
    directory = retained_path(out, 'superseded') / uuid.uuid4().hex
    directory.mkdir(parents=True)
    previous = {}
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in ('build-report.json', 'qualification.json'):
            path = retained_path(out, name)
            if path.exists():
                raw = path.read_bytes()
                publish(descriptor, name, raw)
                previous[name] = hashlib.sha256(raw).hexdigest()
    finally:
        os.close(descriptor)
    supersession = dict(path=str(directory.relative_to(out)), previous=previous, reason=reason)
    write_json(directory / 'supersession.json', dict(schema='agent-ee.build-supersession/1', **supersession))
    descriptor = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return supersession


def record_digests(root):
    return {name: digest(retained_path(root, name)) for name in ('build-report.json', 'qualification.json')
            if (root / name).exists()}


def scope_path(directory, *, product=False):
    return directory / ('product/scope.json' if product else 'scope.json')

"""Resumable composition of the existing engineering and native producers.

Engineering decisions remain captured inputs. Every stage consumes the preceding
stage's real files; final acceptance comes from independently sealed checks.
"""
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from . import native_run
from . import build_plan, build_stages, placement_intent, qualification, requirement_map, retained_run
from .retained_run import retained_path as _retained_path
from .catalog import strict_json
from .stage_receipts import tree_identity
from .storage import digest, identity, read_json, write_json
from .run_record import policy_files
from . import run_record
from .placement_job import EnvironmentFailure

SCHEMA = build_plan.SCHEMA
TYPED_SCHEMA = build_plan.TYPED_SCHEMA
REPORT_SCHEMA = retained_run.REPORT_SCHEMA
STAGES = retained_run.STAGES


validate_plan = build_plan.validate_plan
_inventory = requirement_map.input_inventory


def _runtime():
    cli = shutil.which('kicad-cli')
    if cli is None:
        raise ValueError('kicad-cli is unavailable')
    execution = native_run.run(
        native_run.Intent('probe', [cli, 'version'], timeout=30, isolation='host'), work=None
    )
    version = native_run.completed(execution, text=True)
    version.check_returncode()
    extension, diagnostic = run_record.pcbnew_extension()
    if extension is None:
        raise ValueError('pcbnew native extension unavailable: ' + str(diagnostic))
    probe = native_run.run(
        native_run.Intent('probe', [sys.executable, '-c', 'import importlib; print(importlib.import_module("pcbnew").Version())'],
                          timeout=15, isolation='host'), work=None
    )
    pcbnew_result = native_run.completed(probe, text=True)
    if pcbnew_result.returncode != 0:
        raise ValueError('pcbnew version probe failed: ' + pcbnew_result.stderr.strip())
    return dict(source=policy_files(), python=digest(sys.executable), kicad_cli=digest(cli),
                kicad_version=version.stdout.strip(), pcbnew_version=pcbnew_result.stdout.strip(), pcbnew_sha256=digest(extension))


def _native(runtime):
    """The runtime a build is bound to. agent-ee's own source digests are recorded
    provenance: using the tool must not depend on the tool staying unedited."""
    return {key: value for key, value in runtime.items() if key != 'source'}


def _bound(pin):
    return identity(dict(pin, runtime=_native(pin['runtime'])))


def _record_tool_change(report, pin):
    captured, current = report['inputs']['runtime'].get('source'), pin['runtime'].get('source')
    if captured == current:
        return
    change = dict(reason='agent-ee source changed since capture', captured_sha256=identity(captured),
                  current_sha256=identity(current),
                  files=sorted(name for name in (captured or {}).keys() | (current or {}).keys()
                               if (captured or {}).get(name) != (current or {}).get(name)))
    if change not in report.setdefault('tool_changes', []):
        report['tool_changes'].append(change)


def review_build(run, out, *, max_seconds=900, profile=None):
    """Fresh checks, and with a profile the ordinary full review, without adopting
    a candidate or accepting a package."""
    run, out = Path(run).resolve(), Path(out).resolve()
    if profile is not None:
        profile = Path(profile).resolve()
        if not profile.is_file():
            raise ValueError('build review profile must be an existing agent-ee.profile/1 file')
    if out == run or run in out.parents:
        raise ValueError('build review output must be outside the retained build')
    if type(max_seconds) is not int or not 1 <= max_seconds <= 86400:
        raise ValueError('max-seconds must be an integer from 1 to 86400')
    # Reuse the build writer's exact lease; do not create a new lock for a
    # missing or incomplete run. The review itself owns its ordinary lease.
    with _retained_path(run, '.lock').open('rb') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('another writer owns the build') from exc
        selected = retained_run.review_source(run)
        candidate, scope, selection = selected.candidate, selected.scope, selected.selection
        if (candidate / 'build-review').exists():
            raise ValueError('candidate occupies reserved build-review provenance namespace')
        out.mkdir(parents=True, exist_ok=False)
        # The source selection is an immutable captured input to the ordinary
        # review. It adds provenance, never a passing acceptance predicate.
        source = out / 'source.json'
        write_json(source, selection)
        bound_scope = build_stages.candidate_scope(scope, candidate)
        bound_scope['inputs'].append(dict(path=str(source), at=retained_run.REVIEW_SOURCE))
        context = {}
        if (profile is not None and selection.get('qualification_mode') == 'typed'
                and selection['selected']['stage'] == 'package'):
            from .judgment_context import Source
            context['judgment_context'] = Source(run, run / selection['selected']['directory'], candidate,
                                                diagnostic=True)
        result = retained_run.review_candidate(bound_scope, out, max_seconds,
                                               profile=None if profile is None else str(profile), **context)
        if retained_run.review_source(run).selection != selection:
            raise ValueError('retained build changed during diagnostic verification')
        summary = dict(schema='agent-ee.build-review/1', accepted=False,
            outcome=result['outcome'], candidate_id=result['candidate_id'],
            source=selection, review=str(out / 'review'),
            checks=[{key: row[key] for key in ('id', 'execution', 'outcome')} for row in result['checks']],
            limitations=result['limitations'])
        if profile is not None:
            summary['review_profile'] = str(profile)
        qualified = qualification.assess_review(out, result, selection, engineering=profile is not None)
        summary['qualification'] = qualification.summary(out, qualified)
        write_json(out / 'report.json', summary)
        return summary


def preflight_build(plan_path, out):
    """Validate plan schema, pruned source inventory, placement intent, and map binding before any stage runs."""
    plan_path, out, plan_raw, plan, source, source_files = build_plan.admit_source(plan_path, out)
    placement_intent.admit_source(
        strict_json((source / plan['placement']['intent']).read_bytes()),
        strict_json((source / plan['placement']['groups']).read_bytes()))
    if 'block_source' in plan['placement']:
        from .block_source_stage import admit
        admit(source, plan, source_files)
    requirement_map.admit_source(
        (source / plan['requirements']['map']).read_bytes(),
        typed=plan['schema'] == TYPED_SCHEMA, source_files=source_files)
    return plan_path, out, plan_raw, plan, source, source_files


def run(plan_path, out, *, resume=False, through='package'):
    if through not in STAGES:
        raise ValueError('unknown build stage')
    plan_path, out, plan_raw, plan, source, source_files = preflight_build(plan_path, out)
    pin = dict(plan_sha256=hashlib.sha256(plan_raw).hexdigest(), source=source_files, runtime=_runtime())
    binding = identity(pin)
    if resume:
        report = retained_run.load_report(out)
        if report.get('schema') != REPORT_SCHEMA:
            raise ValueError('expected retained build report')
    else:
        out.mkdir(parents=True, exist_ok=False)
        report = retained_run.initialize(out, pin)
    with (out / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if resume:
            report = retained_run.load_report(out)
            try:
                retained_plan = retained_run.verify_retained(out)
            except qualification.QualificationError as exc:
                _invalidate_retained(out, report, str(exc), plan)
            if retained_plan is None:
                _refuse_resume(out, report, 'historical build has no captured plan; resume migration is unsupported',
                               pin, kind='historical_migration')
            if _bound(report['inputs']) != _bound(pin):
                _refuse_resume(out, report, 'build resume identity differs from captured inputs or runtime', pin)
            # The build keeps its captured identity; a later tool is recorded beside it.
            binding = report['identity']
            _record_tool_change(report, pin)
        captured_plan = out / 'build-plan.json'
        if not captured_plan.exists():
            captured_plan.write_bytes(plan_raw)
        if digest(captured_plan) != pin['plan_sha256']:
            _invalidate_retained(out, report, 'captured build plan identity changed', plan)
        retained_run.capture_source(out, source, plan, source_files)
        if _inventory(out / 'source') != source_files:
            _invalidate_retained(out, report, 'retained build source identity changed', plan)
        for index, row in enumerate(report['completed']):
            if row['stage'] != STAGES[index] or tree_identity(out / row['directory']) != row['tree']:
                _invalidate_retained(out, report, 'completed build stage identity changed: ' + row['stage'], plan)
        package = next((row for row in report['completed'] if row['stage'] == 'package'), None)
        if 'judgment_inventory' in report:
            frozen = report['judgment_inventory']
            path = _retained_path(out, frozen['path'])
            if not path.is_file() or digest(path) != frozen['sha256']:
                _invalidate_retained(out, report, 'judgment inventory changed after freeze', plan)
            try:
                qualification.verify_judgment_source(source, plan, path.parent)
            except ValueError as exc:
                _refuse_resume(out, report, str(exc), pin, kind='judgment_inputs',
                               judgments=identity(qualification.judgment_inventory(source, plan)))
        if package is not None and plan['schema'] == TYPED_SCHEMA:
            try:
                qualification.verify_judgment_source(source, plan, out / package['directory'])
            except ValueError as exc:
                _refuse_resume(out, report, str(exc), pin, kind='judgment_inputs',
                               judgments=identity(qualification.judgment_inventory(source, plan)))
        if report['accepted']:
            try:
                retained_run.verify_stage_review(retained_run.completed_stage(out, 'package', report).directory)
            except (ValueError, OSError) as exc:
                # Completed-tree custody was verified independently above. This
                # verifier also gates current tools/rules; its refusal does not
                # establish that historical evidence bytes were corrupted.
                _refuse_resume(out, report, str(exc), pin, kind='verification_unavailable')
            return _assess_report(out, report, typed=plan['schema'] == TYPED_SCHEMA)
        previous = out / report['candidate'] if report['candidate'] else out / 'source'
        # These describe the current run; failed attempts retain their own class.
        report.pop('failure_kind', None)
        report.pop('failing_stage', None)
        try:
            for stage in STAGES[len(report['completed']):STAGES.index(through) + 1]:
                if (out / 'CANCEL').exists():
                    raise KeyboardInterrupt('build CANCEL requested')
                index = len(report['completed'])
                attempt = retained_run.begin_stage(out, stage, index=index)
                directory = attempt.directory
                report['attempts'].append(dict(stage=stage, directory=str(directory.relative_to(out)),
                                              status='running', identity=binding))
                retained_run.write_report(out, report)
                before = tree_identity(previous)
                if stage == 'package':
                    from . import block_package
                    block_package.verify(out, previous, retained_run.scope(out).document, plan)
                if stage == 'package' and plan['schema'] == TYPED_SCHEMA:
                    qualification.freeze_judgments(source, previous, plan, directory)
                    path = directory / 'judgment-inventory.json'
                    report['judgment_inventory'] = dict(path=str(path.relative_to(out)), sha256=digest(path))
                    retained_run.write_report(out, report)
                candidate = attempt.execute(plan, previous, diagnostic=through != 'package')
                if (tree_identity(previous) != before or build_plan.source_inventory(source, plan) != source_files
                        or digest(plan_path) != pin['plan_sha256'] or _native(_runtime()) != _native(pin['runtime'])):
                    raise ValueError('build input or runtime changed during stage execution')
                if stage == 'package' and plan['schema'] == TYPED_SCHEMA:
                    qualification.verify_judgment_source(source, plan, directory)
                retained_run.record_completion(out, report, attempt, candidate)
                if stage == 'placement-routing' and 'local_decoupling' in plan.get('placement', {}):
                    job = read_json(directory / 'placement/job-report.json')
                    report['warnings'] = job.get('final', {}).get('decoupling_loops', {}).get('warnings', [])
                report['attempts'][-1]['status'] = 'completed'
                previous = candidate
                retained_run.write_report(out, report)
            report['accepted'] = False
            report['status'] = 'assessing' if len(report['completed']) == len(STAGES) else 'checkpoint'
            report.pop('reason', None)
        except (ValueError, OSError, subprocess.SubprocessError, KeyboardInterrupt, EnvironmentFailure) as exc:
            report['status'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
            report['reason'] = f'{type(exc).__name__}: {exc}'
            if report['attempts'] and report['attempts'][-1]['status'] == 'running':
                report['attempts'][-1].update(status=report['status'], reason=report['reason'])
            if isinstance(exc, EnvironmentFailure):
                failure = dict(failure_kind='environment', failing_stage=exc.stage)
                report.update(failure)
                report['attempts'][-1].update(failure)
        return _assess_report(out, report, typed=plan['schema'] == TYPED_SCHEMA)


def _assess_report(out, report, *, typed=False):
    if report['accepted']:
        report['supersession'] = retained_run.archive_records(out, 'Reassessment of a previously accepted run')
    report['accepted'] = False
    report.pop('qualification', None)
    retained_run.write_report(out, report)
    try:
        record = qualification.assess(out)
    except qualification.QualificationError as exc:
        report.update(status='failed', accepted=False, reason='QualificationError: ' + str(exc))
        record = qualification.failed_assessment(out, report, str(exc),
            typed=typed, integrity=exc.integrity)
        report['qualification'] = qualification.summary(out, record)
    else:
        report['qualification'] = qualification.summary(out, record)
        report['accepted'] = record['accepted']
        if len(report['completed']) == len(STAGES):
            file_release = record['dimensions']['file_release']['status']
            pending = file_release == 'pending'
            report['status'] = 'accepted' if report['accepted'] else 'pending' if pending else 'failed'
            if not report['accepted']:
                report['reason'] = ('Required file-release evidence is pending' if pending
                                    else 'QualificationError: required file-release evidence failed' if file_release != 'pass'
                                    else 'Requested engineering review is '
                                         + record['dimensions']['engineering_review']['status'])
    retained_run.write_report(out, report)
    return report


def _refuse_resume(out, report, reason, pin, *, kind='invocation_identity', judgments=None):
    """Record a refused invocation without changing the build it tried to resume."""
    directory = _retained_path(out, 'resume-refusals')
    directory.mkdir(exist_ok=True)
    retained = retained_run.record_digests(out)
    receipt = dict(schema='agent-ee.resume-refusal/1', kind=kind, reason=reason,
        attempted_identity=identity(pin) if judgments is None else identity(dict(build=pin, judgments=judgments)),
        retained_identity=report['identity'], retained=retained,
        attempted=dict(plan_sha256=pin['plan_sha256'], source_sha256=identity(pin['source']),
                       runtime_sha256=identity(pin['runtime'])))
    if judgments is not None:
        receipt['attempted']['judgments_sha256'] = judgments
    write_json(directory / (uuid.uuid4().hex + '.json'), receipt)
    raise ValueError(reason)


def _invalidate_retained(out, report, reason, plan):
    """Preserve superseded root bytes, then withdraw a claim on corrupted evidence."""
    supersession = retained_run.archive_records(out, reason)
    report.update(status='failed', accepted=False, reason='QualificationError: ' + reason,
                  supersession=supersession)
    report.pop('qualification', None)
    retained_run.write_report(out, report)
    record = qualification.failed_assessment(out, report, reason, typed=plan['schema'] == TYPED_SCHEMA, integrity=True)
    record['supersession'] = supersession
    retained_run.write_qualification(out, record)
    report['qualification'] = qualification.summary(out, record)
    retained_run.write_report(out, report)
    raise ValueError(reason)


def configure_parser(parser):
    sub = parser.add_subparsers(dest='build_command', required=True)
    command = sub.add_parser('run', help='run or resume the captured engineering build')
    command.add_argument('plan', type=Path)
    command.add_argument('--out', type=Path, required=True)
    command.add_argument('--resume', action='store_true')
    command.add_argument('--through', choices=STAGES, default='package', help='diagnostic checkpoint; never acceptance')
    command = sub.add_parser('review', help='run fresh native checks on the last completed build candidate')
    command.add_argument('run', type=Path)
    command.add_argument('--out', type=Path, required=True)
    command.add_argument('--max-seconds', type=int, default=900)
    command.add_argument('--profile', type=Path,
                         help='explicit agent-ee.profile/1; also run its isolated reviewers and adjudicator')


def execute(args):
    if args.build_command == 'review':
        report = review_build(args.run, args.out, max_seconds=args.max_seconds, profile=args.profile)
        shown = {key: report[key] for key in ('accepted', 'outcome', 'candidate_id', 'review')}
        if args.profile is not None:
            shown['engineering_review'] = report['qualification']['dimensions']['engineering_review']
        print(json.dumps(shown, indent=2))
        return 0 if report['outcome'] == 'criteria_satisfied' else 1
    report = run(args.plan, args.out, resume=args.resume, through=args.through)
    print(json.dumps({key: report[key] for key in ('status', 'accepted', 'candidate')} |
                     {'reason': report.get('reason')}, indent=2))
    return 0 if report['status'] in ('accepted', 'checkpoint') else 1

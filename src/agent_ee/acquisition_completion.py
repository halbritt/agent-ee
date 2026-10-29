"""Trusted acquisition completion inside a generation case; no writer-selected commands."""
import argparse
import math
from pathlib import Path
import sys
import time

from . import native_run, qualification, retained_run
from .candidate import safe_relative
from .catalog import exact_fields
from .stage_receipts import tree_identity
from .storage import digest, read_json, write_json

CONTEXT = 'agent-ee.acquisition-completion/1'
PINS = 'agent-ee.acquisition-context-pins/1'
REQUEST = 'agent-ee.acquisition-request/1'
RECEIPT = 'agent-ee.acquisition-completion-record/1'


class CompletionUnavailable(ValueError):
    """A required production integration is absent; never substitute testimony."""


def checkpoint(deadline, cancel=None):
    if (not isinstance(deadline, (int, float)) or not math.isfinite(deadline)
            or time.monotonic() >= deadline or (cancel is not None and cancel.cancelled.is_set())):
        raise native_run.DeadlineExhausted('acquisition completion deadline/cancellation')


def context(root, expect_pins):
    from .generation_benchmark import tree_files
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('public context traverses a link')
    files = tree_files(root)
    if not isinstance(expect_pins, str) or files.get('pins.json') != expect_pins:
        raise ValueError('external public context pins mismatch')
    pins = read_json(root / 'pins.json')
    exact_fields(pins, ('schema', 'files'))
    if pins['schema'] != PINS or pins['files'] != {k: v for k, v in files.items() if k != 'pins.json'}:
        raise ValueError('public context inventory mismatch')
    config = read_json(root / 'context.json')
    exact_fields(config, ('schema', 'plan', 'added_sources', 'review_profile'))
    if config['schema'] != CONTEXT or not isinstance(config['added_sources'], dict):
        raise ValueError('invalid acquisition public context')
    if set(config['added_sources']) - {'cpl', 'manufacturer-text', 'operating-supplement',
                                      'source-supplement', 'power-repair'}:
        raise ValueError('unknown public source addition')
    for rel in [config['plan'], config['review_profile'], *config['added_sources'].values()]:
        if str(safe_relative(rel)) != rel:
            raise ValueError('noncanonical public context path')
        path = root / rel
        if not path.exists() or not path.resolve().is_relative_to(root):
            raise ValueError('missing public context input')
    if not (root / config['plan']).is_file() or not (root / config['review_profile']).is_file():
        raise ValueError('public plan/profile must be files')
    source = (root / config['plan']).parent / read_json(root / config['plan'])['source']
    if not source.resolve().is_relative_to(root):
        raise ValueError('public preparation source escapes context')
    if any(not (root / rel).is_dir() for rel in config['added_sources'].values()):
        raise ValueError('public source additions must be directories')
    return config, files


def package_binding(root, deadline, cancel=None, *, environment=None):
    """Replay the production verifier read-only, including complete stage/native custody."""
    checkpoint(deadline, cancel)
    root = Path(root).resolve()
    plan = retained_run.verify_retained(root)
    report = retained_run.snapshot(root)
    if (report.get('accepted') is not True or report.get('status') != 'accepted'
            or plan is None or plan['schema'] != qualification.TYPED_SCHEMA or 'review_profile' not in plan
            or [r['stage'] for r in report['completed']] != list(retained_run.STAGES)):
        raise ValueError('completion requires seven-stage typed package and requested full review')
    from .run_record import policy_files, runtime_files
    if environment is None:
        environment = dict(policy_files=policy_files(), runtime_files=runtime_files(
            checkpoint=lambda: checkpoint(deadline, cancel)))
    elif environment.get('policy_files') != policy_files():
        raise ValueError('package verifier source differs from frozen environment')
    record = qualification.assess(root, persist=False, host=environment)
    checkpoint(deadline, cancel)
    if not record['accepted'] or read_json(root / 'qualification.json') != record:
        raise ValueError('production package qualification is not accepted/current')
    package = retained_run.completed_stage(root, 'package', report)
    review = retained_run.checked_review(root, report, host=environment)
    if review is None or review.directory != package.directory:
        raise ValueError('package review does not bind final candidate')
    manifest = read_json(package.directory / 'review/candidate.json')
    binding = dict(root=str(root), tree=tree_identity(root), build_identity=report['identity'],
        candidate=package.record['candidate'], candidate_id=manifest['candidate_id'],
        verifier_environment=environment,
        board_sha256=review.binding['board_sha256'], scope_sha256=review.binding['scope_sha256'])
    checkpoint(deadline, cancel)
    return binding


def complete(public, work, production, context_root, expect_pins, *, attempt, deadline, cancel):
    """Launch one fixed trusted child; paths are chosen by run_task, never by the writer."""
    checkpoint(deadline, cancel)
    context(context_root, expect_pins)
    argv = [sys.executable, '-B', '-m', __name__, '--context', str(context_root),
            '--expect-context', expect_pins, '--brief', str(Path(public) / 'prompt.md'),
            '--request', str(Path(work) / 'output'), '--production', str(production),
            '--attempt', str(attempt), '--deadline', repr(deadline)]
    return native_run.run(native_run.Intent('acquisition_completion', argv, timeout=None, isolation='host'),
                          work=Path(work).parent / f'completion-{attempt}', deadline=deadline, cancel=cancel)


def retain(root, context_root, expect_pins, production, *, brief, attempt, productive_deadline, finished, deadline, cancel=None):
    """Parent-owned receipt: execution failure never supplies native/refusal outputs."""
    from .generation_benchmark import executor_outcome, tree_files
    root, production = Path(root), Path(production)
    checkpoint(deadline, cancel)
    context(context_root, expect_pins)
    record = dict(schema=RECEIPT, context=str(Path(context_root).resolve()), context_pins=expect_pins,
                  production=str(production.resolve()), brief_sha256=digest(brief), attempt=attempt,
                  productive_deadline=productive_deadline, finished=finished,
                  execution=executor_outcome(root),
                  outputs=None, package=None, error=None)
    if finished >= productive_deadline:
        record.update(execution='failed', error='productive completion cutoff exhausted')
    if read_json(root / 'execution.json').get('cleanup_error'):
        record.update(execution='failed', error='trusted child cleanup incomplete')
    if record['execution'] == 'ok':
        try:
            terminal = read_json(production / 'completion.json')
            exact_fields(terminal, ('kind',))
            if terminal['kind'] == 'refusal':
                outputs = production / 'refusal'
                if set(tree_files(outputs)) != {'refusal.json'}:
                    raise ValueError('invalid trusted refusal output')
            elif terminal['kind'] == 'package':
                record['package'] = package_binding(production / 'build', deadline, cancel)
                outputs = production / 'build' / record['package']['candidate']
            else:
                raise ValueError('missing terminal production outcome')
            record['outputs'] = str(outputs.resolve())
        except (ValueError, OSError, KeyError, TypeError) as exc:
            record.update(execution='failed', outputs=None, package=None, error=f'{type(exc).__name__}: {exc}')
    write_json(root / 'receipt.json', record)
    checkpoint(deadline, cancel)
    return root


def verify(root, *, deadline, cancel=None, replay=False):
    from .generation_benchmark import executor_outcome, tree_files
    checkpoint(deadline, cancel)
    root = Path(root)
    record = read_json(root / 'receipt.json')
    exact_fields(record, ('schema', 'context', 'context_pins', 'production', 'brief_sha256', 'attempt', 'productive_deadline', 'finished', 'execution', 'outputs', 'package', 'error'))
    if record['schema'] != RECEIPT:
        raise ValueError('unknown completion receipt')
    context(record['context'], record['context_pins'])
    actual = executor_outcome(root)
    if record['execution'] == 'ok':
        if (actual != 'ok' or record['error'] is not None or record['finished'] >= record['productive_deadline']
                or read_json(root / 'execution.json').get('cleanup_error')):
            raise ValueError('completion contradicts actual execution')
        production = Path(record['production'])
        terminal = read_json(production / 'completion.json')
        if terminal == {'kind': 'package'}:
            expected = package_binding(production / 'build', deadline, cancel,
                environment=record['package']['verifier_environment'] if replay else None)
            if record['package'] != expected or Path(record['outputs']) != production / 'build' / expected['candidate']:
                raise ValueError('package completion provenance changed')
        elif terminal == {'kind': 'refusal'}:
            if (record['package'] is not None or Path(record['outputs']) != production / 'refusal'
                    or set(tree_files(record['outputs'])) != {'refusal.json'}):
                raise ValueError('trusted refusal provenance changed')
        else:
            raise ValueError('unknown completion terminal record')
    elif record['execution'] != 'failed' or record['outputs'] is not None or record['package'] is not None:
        raise ValueError('invalid failed completion')
    checkpoint(deadline, cancel)
    return record


def produce(context_root, expect_pins, brief, request, production, *, attempt, deadline):
    """The fixed child currently supports genuine preparation refusals only.

    Supported builds fail closed until genuine judgment/repair adapters are wired.
    There is no profile flag or user-supplied command that can bypass this edge.
    """
    from . import acquisition_prepare
    from .generation_benchmark import tree_files
    checkpoint(deadline)
    config, _ = context(context_root, expect_pins)
    request, production, context_root = Path(request), Path(production), Path(context_root)
    if (set(tree_files(request)) != {'request.json'} or (request / 'request.json').stat().st_size > 4096):
        raise ValueError('generator may emit only bounded request.json')
    document = read_json(request / 'request.json')
    if document != dict(schema=REQUEST, brief_sha256=digest(brief), action='prepare' if attempt == 1 else 'repair'):
        raise ValueError('request differs from captured brief/attempt')
    production.mkdir(parents=True, exist_ok=False)
    if attempt != 1:
        raise CompletionUnavailable('candidate-bound campaign repair connector is not wired')
    try:
        acquisition_prepare.prepare(brief, context_root / config['plan'], production / 'prepared',
            added_sources={k: context_root / v for k, v in config['added_sources'].items()},
            review_profile=context_root / config['review_profile'])
    except acquisition_prepare.PreparationRefused as exc:
        checkpoint(deadline)
        outputs = production / 'refusal'; outputs.mkdir()
        write_json(outputs / 'refusal.json', exc.report)
        write_json(production / 'completion.json', dict(kind='refusal'))
        return
    checkpoint(deadline)
    raise CompletionUnavailable('independent judgment author/verifier adapter is not wired; no build/package claimed')


def complete_prepared(prepared, build_run, out, *, author, verifier, private_evaluator_pair,
                      deadline, cancellation, launch_environment, host=None):
    """Parent-side composition for the fixed forthcoming public judgment producer.

    The caller retains the SAME case cancellation object. This function is not
    exposed as a generator command and accepts no producer/module/shell override.
    The default child refuses supported requests until this edge is wired.
    """
    import importlib
    import importlib.util
    import shutil
    from . import build_workflow
    from .requirement_map import input_inventory
    checkpoint(deadline, cancellation)
    name = 'agent_ee.judgment_seats'
    if importlib.util.find_spec(name) is None:
        raise CompletionUnavailable('independent judgment author/verifier adapter is not wired')
    producer = importlib.import_module(name)
    prepared, build_run, out = Path(prepared).resolve(), Path(build_run).resolve(), Path(out).resolve()
    plan_path = prepared / 'build-plan.json'
    plan = read_json(plan_path)
    with native_run.using(deadline=deadline, cancel=cancellation):
        checkpoint(deadline, cancellation)
        report = build_workflow.run(plan_path, build_run, through='artwork')
        if [r['stage'] for r in report['completed']] != list(retained_run.STAGES[:-1]):
            raise ValueError('judgments require actual completed artwork')
        checkpoint(deadline, cancellation)
        result = producer.produce(build_run, out, author=author, verifier=verifier,
            private_evaluator_pair=private_evaluator_pair, deadline=deadline,
            cancellation=cancellation, launch_environment=launch_environment, host=host)
        checkpoint(deadline, cancellation)
        if result['execution'] != 'ok' or result['outcome'] != 'criteria_satisfied':
            raise ValueError('judgment author/verifier did not complete successfully')
        records = Path(result['judgment_directory']).absolute()
        receipt = Path(result['producer_receipt']).absolute()
        if (not records.is_relative_to(out) or not receipt.is_relative_to(out)
                or records == out or records.resolve() != records or receipt.resolve() != receipt):
            raise ValueError('judgment producer paths escape owned output')
        for path in (records, receipt):
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError('judgment producer paths contain a link')
        if (not receipt.is_file() or receipt.stat().st_nlink != 1
                or digest(receipt) != result['producer_receipt_sha256']
                or input_inventory(records) != result['judgment_inventory']):
            raise ValueError('judgment producer receipt/directory custody differs')
        source = (plan_path.parent / plan['source']).resolve()
        target = retained_run.retained_path(source, plan['judgments'])
        if target.exists() or target.is_symlink():
            raise ValueError('judgment source already exists; never overwrite prior records')
        shutil.copytree(records, target)
        if input_inventory(target) != result['judgment_inventory']:
            raise ValueError('judgment inventory changed during adoption')
        checkpoint(deadline, cancellation)
        report = build_workflow.run(plan_path, build_run, resume=True)
        checkpoint(deadline, cancellation)
        if report['status'] != 'accepted':
            raise ValueError('resumed package/full review did not accept')
        return package_binding(build_run, deadline, cancellation)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('context', 'expect-context', 'brief', 'request', 'production'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--attempt', type=int, choices=(1, 2), required=True)
    parser.add_argument('--deadline', type=float, required=True)
    args = parser.parse_args()
    with native_run.using(deadline=args.deadline):
        produce(args.context, args.expect_context, args.brief, args.request, args.production,
                attempt=args.attempt, deadline=args.deadline)


if __name__ == '__main__':
    main()

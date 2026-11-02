"""Typed build completion: seat policy loading, package binding verification, and prepared build execution."""
import importlib
import importlib.util
from pathlib import Path
import shutil
import time

from . import build_workflow, native_run, qualification, retained_run
from .catalog import exact_fields
from .judgment_seats import seat_policy as validate_seats
from .profiles import validate_definition
from .requirement_map import input_inventory
from .stage_receipts import tree_identity
from .storage import digest, read_json


class CompletionUnavailable(ValueError):
    """A required production integration is absent; never substitute testimony."""


def load_seat_policy(path, expected=None):
    """A separately pinned trusted-parent policy, never part of writer output."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"seat policy path not found: {path}")
    if (path.is_symlink() or not path.is_file() or path.stat().st_size > 1048576
            or (expected is not None and digest(path) != expected)):
        raise ValueError('trusted judgment seat policy pin mismatch')
    data = read_json(path)
    exact_fields(data, ('author', 'verifier', 'private_evaluator_pair', 'launch_environment',
                        *(['implementation'] if 'implementation' in data else [])))
    if 'implementation' in data:
        validate_definition(data['implementation'])
    validate_seats(data['author'], data['verifier'], data['private_evaluator_pair'])
    exact_fields(data['launch_environment'], ('run_id', 'policy_files', 'runtime_files'))
    return data


def verify_package_binding(root, deadline=None, cancel=None, *, environment=None):
    """Replay the production verifier read-only, including complete stage/native custody."""
    native_run.checkpoint(deadline, cancel)
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
            checkpoint=lambda: native_run.checkpoint(deadline, cancel)))
    elif environment.get('policy_files') != policy_files():
        raise ValueError('package verifier source differs from frozen environment')
    record = qualification.assess(root, persist=False, host=environment)
    native_run.checkpoint(deadline, cancel)
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
    native_run.checkpoint(deadline, cancel)
    return binding


def complete_typed_build(prepared, build_run, out, *, author, verifier, private_evaluator_pair,
                         deadline, cancellation, launch_environment, host=None):
    """Parent-side composition for the fixed public judgment producer.

    The caller retains the SAME case cancellation object. This function is not
    exposed as a generator command and accepts no producer/module/shell override.
    Supervised preparation is intermediate until this composition finishes.
    """
    native_run.checkpoint(deadline, cancellation)
    name = 'agent_ee.judgment_seats'
    if importlib.util.find_spec(name) is None:
        raise CompletionUnavailable('independent judgment author/verifier adapter is not wired')
    producer = importlib.import_module(name)
    prepared, build_run, out = Path(prepared).resolve(), Path(build_run).resolve(), Path(out).resolve()
    plan_path = prepared / 'build-plan.json'
    plan = read_json(plan_path)
    with native_run.using(deadline=deadline, cancel=cancellation):
        native_run.checkpoint(deadline, cancellation)
        report = build_workflow.run(plan_path, build_run, through='artwork')
        if [r['stage'] for r in report['completed']] != list(retained_run.STAGES[:-1]):
            raise ValueError('judgments require actual completed artwork')
        native_run.checkpoint(deadline, cancellation)
        native_checks = out.with_name(out.name + '-native-checks')
        build_workflow.review_build(build_run, native_checks,
            max_seconds=max(1, min(86400, int(deadline - time.monotonic()))))
        native_run.checkpoint(deadline, cancellation)
        result = producer.produce(build_run, out, author=author, verifier=verifier,
            private_evaluator_pair=private_evaluator_pair, deadline=deadline,
            cancellation=cancellation, launch_environment=launch_environment, host=host,
            native_checks=native_checks)
        native_run.checkpoint(deadline, cancellation)
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
        producer.verify(out, result)
        source = (plan_path.parent / plan['source']).resolve()
        target = retained_run.retained_path(source, plan['judgments'])
        if target.exists() or target.is_symlink():
            raise ValueError('judgment source already exists; never overwrite prior records')
        shutil.copytree(records, target)
        if input_inventory(target) != result['judgment_inventory']:
            raise ValueError('judgment inventory changed during adoption')
        native_run.checkpoint(deadline, cancellation)
        report = build_workflow.run(plan_path, build_run, resume=True)
        native_run.checkpoint(deadline, cancellation)
        if report['status'] != 'accepted':
            raise ValueError('resumed package/full review did not accept')
        return verify_package_binding(build_run, deadline, cancellation)


# Backwards compatibility aliases
seat_policy = load_seat_policy
package_binding = verify_package_binding
complete_prepared = complete_typed_build

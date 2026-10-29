"""Trusted acquisition completion inside a generation case; no writer-selected commands."""
import argparse
import math
import re
import traceback
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
    if (root / 'context.json').stat().st_size > 65536:
        raise ValueError('public context options exceed bound')
    exact_fields(config, ('schema', 'plan', 'added_sources', 'review_profile',
                          *(['prepare_options'] if 'prepare_options' in config else [])))
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
    preparation_options(root, config)
    return config, files


def preparation_options(root, config):
    """Only existing prepare arguments, from the pinned public tree; defaults stay unchanged."""
    options = dict(config.get('prepare_options', {}))
    path_fields = {'alternative_context', 'alternative_source', 'evaluation_context',
                   'expected_intent', 'expected_parts_receipt', 'role_context', 'role_source'}
    hash_fields = {key + '_sha256' for key in ('alternative_context', 'evaluation_context',
                                              'expected_intent', 'expected_parts_receipt', 'role_context')}
    if set(options) - path_fields - hash_fields - {'final_groups', 'addr_tie', 'verification_timeout_s'}:
        raise ValueError('unsupported public preparation option')
    for key in hash_fields & options.keys():
        if not isinstance(options[key], str) or re.fullmatch('[0-9a-f]{64}', options[key]) is None:
            raise ValueError('preparation option needs exact SHA-256: ' + key)
    for key in path_fields & options.keys():
        rel = options[key]
        if not isinstance(rel, str) or str(safe_relative(rel)) != rel:
            raise ValueError('noncanonical preparation option: ' + key)
        path = Path(root) / rel
        if not path.exists() or not path.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError('missing public preparation option: ' + key)
        if key.endswith('_source'):
            if not path.is_dir(): raise ValueError('preparation source must be a directory')
        elif not path.is_file():
            raise ValueError('preparation input must be a file')
        if key + '_sha256' in options and digest(path) != options[key + '_sha256']:
            raise ValueError('preparation option pin mismatch: ' + key)
        options[key] = path
    for names in [('alternative_context', 'alternative_source', 'alternative_context_sha256'),
                  ('role_context', 'role_source', 'role_context_sha256'),
                  ('evaluation_context', 'evaluation_context_sha256'),
                  ('expected_intent', 'expected_intent_sha256', 'expected_parts_receipt', 'expected_parts_receipt_sha256')]:
        if any(k in options for k in names) and not all(k in options for k in names):
            raise ValueError('incomplete public preparation option group')
    if 'final_groups' in options and type(options['final_groups']) is not bool:
        raise ValueError('final_groups must be boolean')
    if 'addr_tie' in options and options['addr_tie'] not in ('GND', 'V3V3'):
        raise ValueError('unsupported address tie')
    if 'verification_timeout_s' in options and (type(options['verification_timeout_s']) is not int or not 1 <= options['verification_timeout_s'] <= 86400):
        raise ValueError('invalid verification timeout')
    return options


def seat_policy(path, expected):
    """A separately pinned trusted-parent policy, never part of writer output."""
    from .judgment_seats import seat_policy as validate_seats
    path = Path(path)
    if (path.is_symlink() or not path.is_file() or path.stat().st_size > 1048576
            or digest(path) != expected):
        raise ValueError('trusted judgment seat policy pin mismatch')
    data = read_json(path)
    exact_fields(data, ('author', 'verifier', 'private_evaluator_pair', 'launch_environment'))
    validate_seats(data['author'], data['verifier'], data['private_evaluator_pair'])
    exact_fields(data['launch_environment'], ('run_id', 'policy_files', 'runtime_files'))
    return data


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


def complete(public, work, production, context_root, expect_pins, *, attempt, deadline, cancel,
             policy=None, expect_policy=None, public_context=None, public_context_sha256=None):
    """Supervised preparation, then fixed trusted-parent completion on the SAME case clock/stop."""
    checkpoint(deadline, cancel)
    context(context_root, expect_pins)
    production = Path(production)
    owned = Path(work).parent / f'completion-{attempt}'
    argv = [sys.executable, '-B', '-m', __name__, '--context', str(context_root),
            '--expect-context', expect_pins, '--brief', str(Path(public) / 'prompt.md'),
            '--request', str(Path(work) / 'output'), '--production', str(production),
            '--attempt', str(attempt), '--deadline', repr(deadline)]
    if public_context is not None or public_context_sha256 is not None:
        if public_context is None or public_context_sha256 is None:
            raise ValueError('public case context requires path and external SHA-256')
        argv += ['--public-context', str(public_context), '--expect-public-context', public_context_sha256]
    child = native_run.run(native_run.Intent('acquisition_completion', argv, timeout=None, isolation='host'),
                           work=owned, deadline=deadline, cancel=cancel)
    if child['execution'] != 'ok' or read_json(owned/'execution.json').get('cleanup_error'):
        return child
    terminal = production/'completion.json'
    parent = dict(execution='not_run', child_terminal_sha256=None,
                  deadline=deadline, started=time.monotonic(), finished=None, package=None, error=None)
    try:
        parent['child_terminal_sha256'] = digest(terminal)
        if read_json(terminal) != {'kind': 'prepared'}:
            return child  # Genuine refusal is terminal, never an accepted package.
        checkpoint(deadline, cancel)
        if policy is None or expect_policy is None:
            raise CompletionUnavailable('trusted judgment seat policy is not supplied')
        data = seat_policy(policy, expect_policy)
        parent['policy_sha256'] = expect_policy
        binding = complete_prepared(production/'prepared', production/'build', production/'seats',
            **data, deadline=deadline, cancellation=cancel)
        checkpoint(deadline, cancel)
        parent.update(execution='ok', package=binding)
    except Exception as exc:
        parent.update(execution='failed', error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
    except KeyboardInterrupt:
        parent.update(execution='interrupted', error='parent completion cancelled')
        raise
    finally:
        parent['finished'] = time.monotonic()
        write_json(production/'parent-completion.json', parent)
        write_json(owned/'parent-completion.json', parent)
    return child


def terminal(production, productive_deadline):
    """Prepared is intermediate; only a successful bound parent can supply a package."""
    production = Path(production)
    child = read_json(production/'completion.json')
    if child == {'kind': 'refusal'}:
        return child, None
    if child != {'kind': 'prepared'}:
        raise ValueError('child may supply only preparation/refusal, never accepted package')
    parent = read_json(production/'parent-completion.json')
    if (parent['child_terminal_sha256'] != digest(production/'completion.json')
            or parent['deadline'] != productive_deadline
            or any(type(parent[k]) not in (int, float) or not math.isfinite(parent[k]) for k in ('started', 'finished'))
            or not parent['started'] <= parent['finished'] < productive_deadline
            or parent['execution'] != 'ok' or parent['error'] is not None or parent['package'] is None):
        raise ValueError('trusted parent completion failed: ' + str(parent.get('error')))
    return {'kind': 'package'}, parent


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
            final, parent = terminal(production, productive_deadline)
            exact_fields(final, ('kind',))
            if final['kind'] == 'refusal':
                outputs = production / 'refusal'
                if set(tree_files(outputs)) != {'refusal.json'}:
                    raise ValueError('invalid trusted refusal output')
            elif final['kind'] == 'package':
                record['package'] = package_binding(production / 'build', deadline, cancel,
                    environment=parent['package']['verifier_environment'] if parent else None)
                if parent is not None and record['package'] != parent['package']:
                    raise ValueError('parent package changed before receipt')
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
        final, parent = terminal(production, record['productive_deadline'])
        if final == {'kind': 'package'}:
            expected = package_binding(production / 'build', deadline, cancel,
                environment=record['package']['verifier_environment'] if replay else None)
            if record['package'] != expected or Path(record['outputs']) != production / 'build' / expected['candidate']:
                raise ValueError('package completion provenance changed')
        elif final == {'kind': 'refusal'}:
            if (record['package'] is not None or Path(record['outputs']) != production / 'refusal'
                    or set(tree_files(record['outputs'])) != {'refusal.json'}):
                raise ValueError('trusted refusal provenance changed')
        else:
            raise ValueError('unknown completion terminal record')
    elif record['execution'] != 'failed' or record['outputs'] is not None or record['package'] is not None:
        raise ValueError('invalid failed completion')
    checkpoint(deadline, cancel)
    return record


def produce(context_root, expect_pins, brief, request, production, *, attempt, deadline,
            public_context=None, public_context_sha256=None):
    """Fixed preparation child: emit only genuine refusal or intermediate prepared.

    Accepted packages are established only by the supervised trusted parent.
    The campaign repair and per-case catalog selector remain separate boundaries.
    """
    from . import acquisition_prepare
    from .generation_benchmark import tree_files
    checkpoint(deadline)
    config, _ = context(context_root, expect_pins)
    if public_context is not None or public_context_sha256 is not None:
        if (public_context is None or public_context_sha256 is None
                or Path(public_context).is_symlink() or not Path(public_context).is_file()
                or Path(public_context).stat().st_size > 65536 or digest(public_context) != public_context_sha256):
            raise ValueError('public case-context pin mismatch')
        raise CompletionUnavailable('public case-context catalog selector is not wired')
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
            review_profile=context_root / config['review_profile'],
            **preparation_options(context_root, config))
    except acquisition_prepare.PreparationRefused as exc:
        checkpoint(deadline)
        outputs = production / 'refusal'; outputs.mkdir()
        write_json(outputs / 'refusal.json', exc.report)
        write_json(production / 'completion.json', dict(kind='refusal'))
        return
    checkpoint(deadline)
    write_json(production / 'completion.json', dict(kind='prepared'))


def complete_prepared(prepared, build_run, out, *, author, verifier, private_evaluator_pair,
                      deadline, cancellation, launch_environment, host=None):
    """Parent-side composition for the fixed public judgment producer.

    The caller retains the SAME case cancellation object. This function is not
    exposed as a generator command and accepts no producer/module/shell override.
    Supervised preparation is intermediate until this composition finishes.
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
        producer.verify(out, result)
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
    parser.add_argument('--public-context')
    parser.add_argument('--expect-public-context')
    args = parser.parse_args()
    with native_run.using(deadline=args.deadline):
        produce(args.context, args.expect_context, args.brief, args.request, args.production,
                attempt=args.attempt, deadline=args.deadline, public_context=args.public_context,
                public_context_sha256=args.expect_public_context)


if __name__ == '__main__':
    main()

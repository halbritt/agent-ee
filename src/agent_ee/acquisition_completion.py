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
                   'expected_intent', 'expected_parts_receipt', 'role_context', 'role_source',
                   'resistor_context', 'resistor_source', 'public_context'}
    hash_fields = {key + '_sha256' for key in ('alternative_context', 'evaluation_context',
                                              'expected_intent', 'expected_parts_receipt', 'role_context',
                                              'resistor_context', 'public_context')}
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
                  ('resistor_context', 'resistor_source', 'resistor_context_sha256'),
                  ('public_context', 'public_context_sha256'),
                  ('evaluation_context', 'evaluation_context_sha256'),
                  ('expected_intent', 'expected_intent_sha256', 'expected_parts_receipt', 'expected_parts_receipt_sha256')]:
        if any(k in options for k in names) and not all(k in options for k in names):
            raise ValueError('incomplete public preparation option group')
    if 'resistor_context' in options and ('alternative_context' not in options or 'role_context' in options):
        raise ValueError('common resistor context requires ADC context and owns role selection')
    if 'final_groups' in options and type(options['final_groups']) is not bool:
        raise ValueError('final_groups must be boolean')
    if 'addr_tie' in options and options['addr_tie'] not in ('GND', 'V3V3'):
        raise ValueError('unsupported address tie')
    if 'verification_timeout_s' in options and (type(options['verification_timeout_s']) is not int or not 1 <= options['verification_timeout_s'] <= 86400):
        raise ValueError('invalid verification timeout')
    return options


def admit_public_context(context_root, expect_pins, brief, public_context, expected, *, deadline, cancel=None):
    """Admit exact catalog identities before an attempt using the producer's readers."""
    checkpoint(deadline, cancel)
    from . import acquisition_prepare, acquisition_resistor, acquisition_alternative_context
    from . import acquisition_alternative, acquisition_power_repair, parts_manifest
    from .catalog import strict_json
    root = Path(context_root)
    config, _ = context(root, expect_pins)
    options = preparation_options(root, config)
    public = Path(public_context)
    if public.is_symlink() or digest(public) != expected:
        raise ValueError('public case context pin mismatch before attempt')
    raw = public.read_bytes()
    # Null has no catalog entries to match. The same production parser still owns it.
    if strict_json(raw).get('catalog_policy') is None:
        result = acquisition_resistor.policy(raw, [])
    else:
        selected = acquisition_prepare.parse_brief(Path(brief).read_bytes())
        plan_path = root / config['plan']
        base = (plan_path.parent / read_json(plan_path)['source']).resolve()
        check = acquisition_alternative._assembly_check(read_json(base / 'requirement-map.json'))
        catalogs = [strict_json(parts_manifest.read_captured(base, check['params']['catalog']))]
        if 'alternative_context' in options:
            doc, captures = acquisition_alternative_context.read(options['alternative_source'],
                                                                 options['alternative_context'].read_bytes())
            catalogs.append(strict_json(captures[doc['catalog']['path']]))
        if 'power-repair' in config['added_sources']:
            captures = acquisition_power_repair.read(root / config['added_sources']['power-repair'])
            catalogs.append(strict_json(captures['catalog/catalog.json']))
        if 'resistor_context' in options:
            _, captures, _ = acquisition_resistor.read(options['resistor_source'],
                options['resistor_context'].read_bytes(), selected['value'])
            catalogs.append(strict_json(captures['catalog/catalog.json']))
        result = acquisition_resistor.policy(raw, catalogs)
    checkpoint(deadline, cancel)
    if digest(public) != expected:
        raise ValueError('public case context changed during admission')
    return result


def seat_policy(path, expected):
    """A separately pinned trusted-parent policy, never part of writer output."""
    from .judgment_seats import seat_policy as validate_seats
    path = Path(path)
    if (path.is_symlink() or not path.is_file() or path.stat().st_size > 1048576
            or digest(path) != expected):
        raise ValueError('trusted judgment seat policy pin mismatch')
    data = read_json(path)
    exact_fields(data, ('author', 'verifier', 'private_evaluator_pair', 'launch_environment',
                        *(['implementation'] if 'implementation' in data else [])))
    if 'implementation' in data:
        from .profiles import validate_definition
        validate_definition(data['implementation'])
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
    repair = None
    if attempt == 2:
        try:
            validate_request(request=Path(work) / 'output', brief=Path(public) / 'prompt.md', attempt=attempt)
            if policy is None or expect_policy is None:
                raise CompletionUnavailable('ADDR repair requires trusted implementation policy')
            data = seat_policy(policy, expect_policy)
            if 'implementation' not in data:
                raise CompletionUnavailable('ADDR implementation seat is not supplied')
            with native_run.using(deadline=deadline, cancel=cancel):
                repair = begin_addr_repair(production, context_root, expect_pins, data, expect_policy, deadline, cancel)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            # Preserve the failed productive setup. The unchanged fixed child must
            # refuse its absent authenticated selector, retaining a real execution.
            owned.mkdir(parents=True, exist_ok=True)
            write_json(owned/'repair-refusal.json', dict(error=f'{type(exc).__name__}: {exc}'))
        checkpoint(deadline, cancel)
    argv = [sys.executable, '-B', '-m', __name__, '--context', str(context_root),
            '--expect-context', expect_pins, '--brief', str(Path(public) / 'prompt.md'),
            '--request', str(Path(work) / 'output'), '--production', str(production),
            '--attempt', str(attempt), '--deadline', repr(deadline)]
    if public_context is not None or public_context_sha256 is not None:
        if public_context is None or public_context_sha256 is None:
            raise ValueError('public case context requires path and external SHA-256')
        argv += ['--public-context', str(public_context), '--expect-public-context', public_context_sha256]
    if repair is not None:
        argv += ['--repair-selection', str(repair), '--expect-repair-selection', digest(repair)]
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
        data.pop('implementation', None)
        parent['policy_sha256'] = expect_policy
        try:
            with retained_run.review_resubmissions(0):
                binding = complete_prepared(production/'prepared', production/'build', production/'seats',
                    **data, deadline=deadline, cancellation=cancel)
        except Exception:
            if attempt == 1:
                # Diagnostic work is still productive, inside the SAME D/C. The original failure survives.
                try:
                    parent['diagnostic'] = failed_public_review(production, context_root, expect_pins, deadline, cancel)
                except Exception as diagnostic_error:
                    parent['diagnostic_error'] = f'{type(diagnostic_error).__name__}: {diagnostic_error}'
            raise
        if attempt == 2:
            parent['repair'] = finish_addr_repair(production, context_root, expect_pins, binding, deadline, cancel)
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


def retain(root, context_root, expect_pins, production, *, brief, attempt, productive_deadline, finished, deadline, cancel=None, public_context=None, public_context_sha256=None, completion_policy_sha256=None):
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
    if completion_policy_sha256 is not None:
        record['completion_policy_sha256'] = completion_policy_sha256
    if public_context is not None or public_context_sha256 is not None:
        if (public_context is None or public_context_sha256 is None or
                digest(public_context) != public_context_sha256):
            raise ValueError('public case context pin mismatch at retention')
        record.update(public_context=str(Path(public_context).resolve()), public_context_sha256=public_context_sha256)
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
    exact_fields(record, ('schema', 'context', 'context_pins', 'production', 'brief_sha256', 'attempt', 'productive_deadline', 'finished', 'execution', 'outputs', 'package', 'error',
                        *(['public_context', 'public_context_sha256'] if 'public_context' in record else []),
                        *(['completion_policy_sha256'] if 'completion_policy_sha256' in record else [])))
    if record['schema'] != RECEIPT:
        raise ValueError('unknown completion receipt')
    context(record['context'], record['context_pins'])
    if 'public_context' in record:
        from .generation_benchmark import case_context
        path = Path(record['public_context'])
        if path.is_symlink() or digest(path) != record['public_context_sha256']:
            raise ValueError('public case context changed at completion replay')
        case_context(path)
    actual = executor_outcome(root)
    if record['execution'] == 'ok':
        if (actual != 'ok' or record['error'] is not None or record['finished'] >= record['productive_deadline']
                or read_json(root / 'execution.json').get('cleanup_error')):
            raise ValueError('completion contradicts actual execution')
        production = Path(record['production'])
        final, parent = terminal(production, record['productive_deadline'])
        if final == {'kind': 'package'}:
            if ('completion_policy_sha256' in record and
                    record['completion_policy_sha256'] != parent.get('policy_sha256')):
                raise ValueError('parent seat policy differs from original requested policy')
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
        if record['attempt'] == 2:
            lineage = verify_addr_lineage(production, record['context'], record['context_pins'], parent['package'], deadline, cancel)
            if (read_json(production/'repair/convergence.json') != lineage or parent.get('repair') !=
                    dict(path=str(production/'repair'), tree=tree_identity(production/'repair'))):
                raise ValueError('generated child campaign lineage changed')
    elif record['execution'] != 'failed' or record['outputs'] is not None or record['package'] is not None:
        raise ValueError('invalid failed completion')
    checkpoint(deadline, cancel)
    return record


def produce(context_root, expect_pins, brief, request, production, *, attempt, deadline,
            public_context=None, public_context_sha256=None, repair_selection=None, expect_repair_selection=None):
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
        # Selector and qualification remain owned by the existing production producer.
        from .generation_benchmark import case_context
        case_context(public_context)
    request, production, context_root = Path(request), Path(production), Path(context_root)
    validate_request(request, brief, attempt)
    options = preparation_options(context_root, config)
    if public_context is not None:
        if 'public_context_sha256' in options and options['public_context_sha256'] != public_context_sha256:
            raise ValueError('task/public preparation context conflicts')
        options.update(public_context=Path(public_context), public_context_sha256=public_context_sha256)
    if attempt == 1:
        if repair_selection is not None or expect_repair_selection is not None:
            raise ValueError('unexpected repair selection on initial attempt')
        production.mkdir(parents=True, exist_ok=False)
    else:
        from .campaign import verify_generated_addr
        selection = production / 'repair' / 'selection.json'
        if (repair_selection is None or expect_repair_selection is None
                or Path(repair_selection) != selection or digest(selection) != expect_repair_selection
                or read_json(selection.parent/'config.json')['deadline'] != deadline
                or verify_generated_addr(selection.parent)['addr_tie'] != 'GND'
                or set(tree_files(production)) != {'repair/' + k for k in tree_files(selection.parent)}):
            raise ValueError('missing or foreign generated repair selection')
        options['addr_tie'] = 'GND'
    try:
        acquisition_prepare.prepare(brief, context_root / config['plan'], production / 'prepared',
            added_sources={k: context_root / v for k, v in config['added_sources'].items()},
            review_profile=context_root / config['review_profile'],
            **options)
    except acquisition_prepare.PreparationRefused as exc:
        if attempt != 1:
            raise  # A repair cannot replace the required successor package with a refusal.
        checkpoint(deadline)
        outputs = production / 'refusal'; outputs.mkdir()
        write_json(outputs / 'refusal.json', exc.report)
        write_json(production / 'completion.json', dict(kind='refusal'))
        return
    checkpoint(deadline)
    write_json(production / 'completion.json', dict(kind='prepared'))


def validate_request(request, brief, attempt):
    from .generation_benchmark import tree_files
    request = Path(request)
    if set(tree_files(request)) != {'request.json'} or (request / 'request.json').stat().st_size > 4096:
        raise ValueError('generator may emit only bounded request.json')
    if read_json(request / 'request.json') != dict(schema=REQUEST, brief_sha256=digest(brief),
                                                   action='prepare' if attempt == 1 else 'repair'):
        raise ValueError('request differs from captured brief/attempt')


def failed_public_review(production, context_root, expect_pins, deadline, cancel):
    """Diagnostic only: never a package or release, never a detached post-case review."""
    from . import build_workflow
    checkpoint(deadline, cancel)
    config, _ = context(context_root, expect_pins)
    selected = retained_run.review_source(Path(production) / 'build')
    remaining = int(deadline - time.monotonic())
    if remaining < 1:
        raise native_run.DeadlineExhausted('no productive time for diagnostic review')
    with native_run.using(deadline=deadline, cancel=cancel), retained_run.review_resubmissions(0):
        summary = build_workflow.review_build(Path(production) / 'build', Path(production) / 'failed-public-review',
            max_seconds=min(86400, remaining), profile=Path(context_root) / config['review_profile'])
    checkpoint(deadline, cancel)
    if summary['accepted'] is not False or summary['source'] != selected.selection:
        raise ValueError('diagnostic review source/acceptance changed')
    return dict(root=str(Path(production) / 'failed-public-review'), source=selected.selection,
                tree=tree_identity(Path(production) / 'failed-public-review'))


def repair_original(production, context_root, expect_pins):
    """Previous source is fixed by the admitted case, never by a writer pathname."""
    production = Path(production).absolute()
    if production.name != 'production-2':
        raise ValueError('ADDR successor must be the second complete case attempt')
    previous = production.parent / 'production-1'
    receipt = read_json(production.parent / 'completion-1/receipt.json')
    parent = read_json(previous / 'parent-completion.json')
    if (receipt['attempt'] != 1 or receipt['production'] != str(previous) or receipt['execution'] != 'failed'
            or receipt['context_pins'] != expect_pins or Path(receipt['context']) != Path(context_root).resolve()
            or parent['execution'] != 'failed' or parent.get('diagnostic_error') is not None
            or parent['deadline'] != receipt['productive_deadline']):
        raise ValueError('ADDR source is not the actual failed first completion')
    prepared = read_json(previous / 'prepared/preparation.json')
    if prepared['recipe'].get('addr_tie') != 'V3V3':
        raise ValueError('ADDR source is not the finite wrong-tie preparation')
    config, _ = context(context_root, expect_pins)
    options = preparation_options(context_root, config)
    required = {'evaluation_context', 'evaluation_context_sha256'}
    if not required <= options.keys() or options.get('addr_tie') != 'V3V3':
        raise CompletionUnavailable('ADDR needs unchanged captured evaluation clock and wrong-tie source context')
    diagnostic = previous / 'failed-public-review'
    if (parent.get('diagnostic', {}).get('tree') != tree_identity(diagnostic)
            or read_json(diagnostic / 'source.json') != retained_run.review_source(previous / 'build').selection
            or read_json(diagnostic / 'report.json')['accepted'] is not False):
        raise ValueError('failed PUBLIC diagnostic source/custody changed')
    return previous, diagnostic / 'review'


def begin_addr_repair(production, context_root, expect_pins, policy, policy_sha256, deadline, cancel):
    from .campaign import select_generated_addr
    checkpoint(deadline, cancel)
    previous, first = repair_original(production, context_root, expect_pins)
    if read_json(previous / 'parent-completion.json')['deadline'] != deadline:
        raise ValueError('ADDR repair cannot reset the original case deadline')
    Path(production).mkdir(parents=True, exist_ok=False)
    select_generated_addr(Path(production) / 'repair', first, definition=policy['implementation'],
        launch_environment=policy['launch_environment'], deadline=deadline, cancellation=cancel)
    write_json(Path(production) / 'repair/source.json', dict(previous=str(previous),
        previous_tree=tree_identity(previous), context_pins=expect_pins, completion_policy_sha256=policy_sha256))
    checkpoint(deadline, cancel)
    return Path(production) / 'repair/selection.json'


def verify_addr_lineage(production, context_root, expect_pins, package, deadline, cancel=None):
    from .campaign import verify_generated_addr
    checkpoint(deadline, cancel)
    production = Path(production)
    previous, first = repair_original(production, context_root, expect_pins)
    source = read_json(production / 'repair/source.json')
    policy_pin = source.get('completion_policy_sha256')
    policy = seat_policy(production.parent / 'completion-policy.json', policy_pin)
    if source != dict(previous=str(previous), previous_tree=tree_identity(previous), context_pins=expect_pins,
                      completion_policy_sha256=policy_pin):
        raise ValueError('generated child lost complete immutable first-attempt lineage')
    config = read_json(production / 'repair/config.json')
    if (config['definition'] != {**policy['implementation'], 'tool_mode': 'fixed_packet'} or
            config['launch_environment'] != policy['launch_environment'] or
            config['deadline'] != read_json(previous / 'parent-completion.json')['deadline']):
        raise ValueError('generated writer differs from original requested policy/clock')
    if Path(config['first']) != first:
        raise ValueError('generated child uses a foreign public finding')
    # Recipes, including all original source/control/role/clock pins, may differ ONLY in the finite tie.
    old = read_json(previous / 'prepared/preparation.json')['recipe']
    new = read_json(production / 'prepared/preparation.json')['recipe']
    if old.get('addr_tie') != 'V3V3' or new.get('addr_tie') != 'GND' or {
            k:v for k,v in old.items() if k != 'addr_tie'} != {k:v for k,v in new.items() if k != 'addr_tie'}:
        raise ValueError('generated child changed original preparation recipe/context')
    successor = production / 'build' / package['candidate']
    # Final native package candidate and its public review must be the same existing checked review.
    checked = retained_run.checked_review(production / 'build', retained_run.snapshot(production / 'build'),
                                          host=package['verifier_environment'])
    completed = retained_run.completed_stage(production / 'build', 'package')
    if checked is None or completed.candidate != successor or checked.directory != completed.directory:
        raise ValueError('generated successor final package/review identity differs')
    result = verify_generated_addr(production / 'repair', successor=checked.directory / 'review',
                                   host=package['verifier_environment'])
    checkpoint(deadline, cancel)
    return result


def finish_addr_repair(production, context_root, expect_pins, package, deadline, cancel):
    result = verify_addr_lineage(production, context_root, expect_pins, package, deadline, cancel)
    write_json(Path(production) / 'repair/convergence.json', result)
    return dict(path=str(Path(production) / 'repair'), tree=tree_identity(Path(production) / 'repair'))


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
    parser.add_argument('--repair-selection')
    parser.add_argument('--expect-repair-selection')
    args = parser.parse_args()
    with native_run.using(deadline=args.deadline):
        produce(args.context, args.expect_context, args.brief, args.request, args.production,
                attempt=args.attempt, deadline=args.deadline, public_context=args.public_context,
                public_context_sha256=args.expect_public_context, repair_selection=args.repair_selection,
                expect_repair_selection=args.expect_repair_selection)


if __name__ == '__main__':
    main()

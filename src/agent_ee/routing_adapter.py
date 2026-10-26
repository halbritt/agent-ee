"""Coordinator half of the pinned kicad-route routing adapter (AGENTEE-93).

`step()` declares one routing attempt in the AGENTEE-95 `agent-ee.stage-step/1`
form: the coordinator, never the writer, runs it in the networkless sandbox
with the kicad-route checkout, its state directory and the attempt script
mounted read-only. `verify()` re-derives the attempt's outcome from the retained
bytes alone, so a report the attempt wrote is never taken on its word.

Routing receipts and reports are provenance. A routed proposal still needs
agent-ee's own fresh checks after capture (DRC with schematic parity and zero
unconnected items, copper-width/1, compiled routing rules) before anything is
accepted.
"""

import contextlib
import contextvars
import json
import os
import re
from pathlib import Path, PurePosixPath

from . import kicad_route_attempt as attempt
from .storage import digest

ADAPTER_AT = '/opt/agent-ee-routing/kicad_route_attempt.py'
ADAPTER_SOURCE = Path(attempt.__file__).resolve()
BINDING_MOUNTS = [{'source': str(ADAPTER_SOURCE.with_name(name)),
                   'at': '/opt/agent-ee-routing/' + name}
                  for name in ('net_binding.py', 'symbol_library.py')]
# Hashing the adapter, code and state mounts, and retaining diagnostics, happen
# after the router's own timeout inside the same step budget.
STEP_MARGIN_S = 300
COMPATIBILITY = re.compile(r'kicad-route@([0-9a-f]{40})\Z')


class StaleRoutingEvidence(ValueError):
    """Routing evidence produced by another kicad-route pin (AGENTEE-450).

    Results do not transfer between pins: the report is refused for every new
    comparison, adoption or baseline, whatever else it says. Its retained bytes
    stay as they are; this only names why they no longer qualify."""

    def __init__(self, pin, expected, what='routing attempt'):
        super().__init__(f'stale routing evidence: {what} claims a different kicad-route pin {pin}, '
                         f'not the pinned {expected}; rerun it on the current pin')
        self.pin, self.expected = pin, expected
ARTIFACTS = ('routing/adapter-report.json', 'routing/kicad-route.stdout', 'routing/kicad-route.stderr',
             'routing/attempt/result.json', 'routing/attempt/attempt.json', 'routing/attempt/RESULT.md',
             'routing/attempt/snapshot.json', 'routing/attempt/validate', 'routing/attempt/facts',
             'routing/attempt/work', 'routing/' + attempt.ORIGINAL_STAGE)


def pinned_layout(pin_file=None):
    """The host layout of a pinned router run: the pin record and the file it is read from.

    The production pin is the package's data file; a test passes its fake checkout's
    pin file instead of changing module globals. The file is mounted beside the
    sandbox script, which reads the same bytes."""
    path = Path(pin_file).resolve() if pin_file is not None else attempt.PIN_FILE.resolve()
    record = attempt.load_pin(path)
    return {'pin': record['pin'], 'code': record['code'], 'pin_file': str(path)}


# A layout a caller qualifies for the runs it starts in this context (see qualified_layout).
_LAYOUT = contextvars.ContextVar('agent_ee_router_layout', default=None)


@contextlib.contextmanager
def qualified_layout(layout):
    """Qualify layout (pinned_layout of a fake checkout's pin file) for every pinned router run,
    preflight, step and binding started in this context, instead of the package pin."""
    token = _LAYOUT.set(layout)
    try:
        yield layout
    finally:
        _LAYOUT.reset(token)


def current_layout():
    """The qualified layout: a context's qualified_layout, else the package pin."""
    return _LAYOUT.get() or pinned_layout()


def preflight(checkout, state=None, backend='builtin', *, layout=None):
    """Host-side identity without running git, routing or installation.

    Running `git status` could rewrite the checkout's index, so HEAD is read
    from the git files and code is compared with the pinned blob hashes, by the
    same attempt.preflight the sandbox runs."""
    layout = layout or current_layout()
    checkout = Path(checkout).resolve()
    state = Path(state).resolve() if state is not None else checkout
    commit, _, problems = attempt.preflight(checkout, state, backend, layout, mounted_at=attempt.SANDBOX[
        'checkout' if state == checkout else 'state'])
    return {'checkout': str(checkout), 'state': str(state), 'commit': commit, 'pin': layout['pin'],
            'executable_sha256': digest(checkout / 'bin/kicad-route') if (checkout / 'bin/kicad-route').is_file() else None,
            'adapter_sha256': digest(ADAPTER_SOURCE), 'problems': problems}


def step(*, project, board, schematic, name, label, checkout, state=None, budget=300, timeout=None,
         backend='builtin', diff_pairs=None, layer_policy=None, experiment_policy=None, layout=None):
    """The AGENTEE-95 step declaration for one attempt. Raises ValueError if the tool does not qualify.

    layer_policy (AGENTEE-231) is the declared allowed signal layers, optional KRT
    layer costs, pour area-loss bound and optional RF reference plane.
    experiment_policy (AGENTEE-442) is a five-field routability-policy/1 declaration the
    router receives as --experiment-policy; its experiment evidence is retained or refused.
    layout (pinned_layout) is the qualified pin; its pin file is mounted beside the script."""
    layout = layout or current_layout()
    check = preflight(checkout, state, backend, layout=layout)
    if check['problems']:
        raise ValueError('kicad-route does not qualify: ' + '; '.join(check['problems']))
    timeout = timeout if timeout is not None else 2 * budget + 300
    separate = check['state'] != check['checkout']
    args = ['--project', project, '--board', board, '--schematic', schematic, '--name', name,
            '--label', label, '--budget', str(budget), '--timeout', str(timeout), '--backend', backend,
            '--state', 'separate' if separate else 'checkout']
    if diff_pairs is not None:
        args += ['--diff-pairs', diff_pairs]
    if layer_policy is not None:
        args += ['--layer-policy', attempt.canonical_json(layer_policy)]
    if experiment_policy is not None:
        args += ['--experiment-policy', attempt.canonical_json(experiment_policy)]
    request = attempt.parse_request(args)  # the same validation the sandbox applies
    return _step(request, check['checkout'], check['state'] if separate else None, layout)


def _step(request, checkout, state, layout=None):
    """Pure declaration construction, also used for retained campaign replay."""
    layout = layout or current_layout()
    mounts = [{'source': str(ADAPTER_SOURCE), 'at': ADAPTER_AT},
              {'source': checkout, 'at': attempt.SANDBOX['checkout']}, *BINDING_MOUNTS,
              {'source': layout['pin_file'], 'at': attempt.PIN_AT}]
    if state is not None:
        mounts.append({'source': state, 'at': attempt.SANDBOX['state']})
    target = f'{request["project"]}/{request["board"]}'
    return {'schema': 'agent-ee.stage-step/1', 'stage': 'routing', 'step': f'kicad-route-{request["backend"]}',
            'argv': [attempt.PYTHON, '-I', '-B', ADAPTER_AT, *_request_argv(request)],
            'mounts': mounts, 'source': attempt.SANDBOX['checkout'] + '/src',
            'backend_report': {'artifact': 'routing/attempt/result.json', 'key': 'backend'},
            'writes': {target: f'routing/candidate/{target}'}, 'artifacts': list(ARTIFACTS),
            'adoptable_exits': [0, 2], 'timeout_s': request['timeout_s'] + STEP_MARGIN_S}


def validate_declaration(value):
    """Strict campaign opt-in; command, mounts, pin and environment are not configurable."""
    required = {'schema', 'project', 'board', 'schematic', 'name', 'label', 'checkout'}
    optional = {'state', 'budget', 'timeout', 'backend', 'diff_pairs', 'layer_policy', 'experiment_policy'}
    if (not isinstance(value, dict) or value.get('schema') != 'agent-ee.routing-adapter/1' or
            not required <= set(value) or not set(value) <= required | optional):
        raise ValueError('expected agent-ee.routing-adapter/1 declaration')
    budget = value.get('budget', 300)
    timeout = value.get('timeout', 2 * budget + 300) if isinstance(budget, int) else None
    state = value.get('state')
    checkout = value['checkout']
    for path in (checkout, state):
        if path is not None and (not isinstance(path, str) or not Path(path).is_absolute() or
                                 '..' in Path(path).parts or str(Path(path)) != path):
            raise ValueError('routing checkout/state must be normalized absolute host paths')
    if checkout is None:
        raise ValueError('routing checkout is required')
    separate = state is not None and state != checkout
    request = attempt.parse_request(_request_argv({
        **{k: value[k] for k in ('project', 'board', 'schematic', 'name', 'label')},
        'backend': value.get('backend', 'builtin'), 'budget_s': budget, 'timeout_s': timeout,
        'state': 'separate' if separate else 'checkout',
        **({'diff_pairs': value['diff_pairs']} if 'diff_pairs' in value else {}),
        **({'layer_policy': value['layer_policy']} if 'layer_policy' in value else {}),
        **({'experiment_policy': value['experiment_policy']} if 'experiment_policy' in value else {})}))
    return {'schema': value['schema'], **{k: request[k] for k in ('project', 'board', 'schematic', 'name', 'label')},
            'checkout': checkout, 'state': state if separate else None, 'budget': request['budget_s'],
            'timeout': request['timeout_s'], 'backend': request['backend'],
            **({'diff_pairs': request['diff_pairs']} if 'diff_pairs' in request else {}),
            **({'layer_policy': request['layer_policy']} if 'layer_policy' in request else {}),
            **({'experiment_policy': request['experiment_policy']} if 'experiment_policy' in request else {})}


def campaign_spec(declaration, manifest, mutable):
    value = validate_declaration(declaration)
    scope = manifest['scope']
    stages = scope.get('stages')
    if not isinstance(stages, dict) or 'routing' not in stages.get('order', []):
        raise ValueError('routing_adapter requires a staged scope with a routing stage')
    board = f'{value["project"]}/{value["board"]}'
    schematic = f'{value["project"]}/{value["schematic"]}'
    if board != scope['board'] or schematic != scope['schematic'] or board not in mutable:
        raise ValueError('routing adapter must select the scope board/schematic and a mutable board')
    return declaration_spec(value)


def declaration_spec(value):
    """The step a validated declaration runs, without the campaign scope checks."""
    from .stage_receipts import validate_step
    request = attempt.parse_request(_request_argv({
        **{k: value[k] for k in ('project', 'board', 'schematic', 'name', 'label', 'backend')},
        'budget_s': value['budget'], 'timeout_s': value['timeout'],
        'state': 'separate' if value['state'] else 'checkout',
        **({'diff_pairs': value['diff_pairs']} if 'diff_pairs' in value else {}),
        **({'layer_policy': value['layer_policy']} if 'layer_policy' in value else {}),
        **({'experiment_policy': value['experiment_policy']} if 'experiment_policy' in value else {})}))
    return validate_step(_step(request, value['checkout'], value['state']), host=False)


def campaign_binding(declaration, manifest, mutable):
    from .stage_receipts import observe_tool
    spec = campaign_spec(declaration, manifest, mutable)
    validate_campaign_step(spec)
    return {'spec': spec, 'tool': observe_tool(spec), 'pin': current_layout()['pin'],
            'adapter_sha256': digest(ADAPTER_SOURCE)}


def validate_binding(binding, declaration, manifest, mutable, policy_files):
    pin = current_layout()['pin']
    if (isinstance(binding, dict) and isinstance(binding.get('pin'), str) and
            re.fullmatch(r'[0-9a-f]{40}', binding['pin']) and binding['pin'] != pin):
        raise StaleRoutingEvidence(binding['pin'], pin, 'campaign routing binding')
    expected = campaign_spec(declaration, manifest, mutable)
    if (not isinstance(binding, dict) or set(binding) != {'spec', 'tool', 'pin', 'adapter_sha256'} or
            binding['spec'] != expected or binding['pin'] != pin or
            binding['adapter_sha256'] != policy_files['kicad_route_attempt.py']):
        raise ValueError('campaign routing binding differs from its declaration or captured policy')
    return expected


def validate_live_binding(binding):
    from .stage_receipts import observe_tool
    spec = validate_campaign_step(binding['spec'])
    if digest(ADAPTER_SOURCE) != binding['adapter_sha256'] or observe_tool(spec) != binding['tool']:
        raise ValueError('routing adapter, checkout or backend state changed since campaign binding')


def validate_campaign_step(spec, *, host=True):
    """Bind the complete coordinator command, mounts and timeout to this adapter."""
    from .stage_receipts import validate_step

    spec = validate_step(spec, host=host)
    request = attempt.parse_request(spec['argv'][4:])
    mounts = {m['at']: m['source'] for m in spec['mounts']}
    checkout = mounts.get(attempt.SANDBOX['checkout'])
    separate = request['state'] == 'separate'
    state = mounts.get(attempt.SANDBOX['state']) if separate else None
    expected_mounts = {ADAPTER_AT, attempt.SANDBOX['checkout'], attempt.PIN_AT, *(m['at'] for m in BINDING_MOUNTS)}
    if separate:
        expected_mounts.add(attempt.SANDBOX['state'])
    if (set(mounts) != expected_mounts or spec['argv'][:4] != [attempt.PYTHON, '-I', '-B', ADAPTER_AT] or
            (host and mounts[ADAPTER_AT] != str(ADAPTER_SOURCE))):
        raise ValueError('campaign routing step differs from the pinned adapter command or mounts')
    if host:
        if any(mounts[m['at']] != m['source'] for m in BINDING_MOUNTS):
            raise ValueError('routing binding helper mounts differ from captured policy')
        layout = current_layout()
        if mounts[attempt.PIN_AT] != layout['pin_file']:
            raise ValueError('routing pin mount differs from the qualified pin file')
        check = preflight(checkout, state, request['backend'], layout=layout)
        if check['problems']:
            raise ValueError('kicad-route does not qualify: ' + '; '.join(check['problems']))
        if checkout != check['checkout'] or (state is not None and state != check['state']):
            raise ValueError('routing mount sources must use resolved host paths')
    _receipt_step({'spec': spec}, request)
    if spec['timeout_s'] != request['timeout_s'] + STEP_MARGIN_S:
        raise ValueError('campaign routing step differs from the pinned adapter timeout')
    return spec


def verify_campaign_receipt(receipt_dir, *, expected=None, binding=None, location=None):
    """Verify coordinator execution and routing semantics before adoption and on replay.

    location is the directory the receipt was executed in, when it was copied afterwards
    (an imported via-budget pilot); the sandbox command still names that directory."""
    from . import native_run
    from .stage_receipts import ADOPTABLE, file_tree, validate_receipt

    receipt_dir = Path(receipt_dir)
    receipt = validate_receipt(receipt_dir)
    spec = validate_campaign_step(receipt['spec'], host=False)
    if expected is not None and receipt['binding'] != expected:
        raise ValueError('foreign routing receipt binding')
    if binding is not None:
        observed = {k: receipt['tool'][k] for k in ('executable', 'mounts', 'source_tree')}
        if (spec != binding['spec'] or observed != binding['tool'] or receipt['tool_after'] != binding['tool']):
            raise ValueError('routing receipt differs from the frozen campaign tool binding')
    execution = receipt['execution']
    if execution is not None:
        origin = Path(location) if location is not None else receipt_dir
        actual = execution['sandbox_argv']
        profile = native_run.profile(execution)
        mount_identities = profile.mount_identities
        mounts = [(m['source'], m['at']) for m in spec['mounts']]
        fd_mounts = []
        for bind in profile.binds:
            if bind.option == '--ro-bind-fd':
                if not bind.source.isdecimal() or int(bind.source) < 3:
                    raise ValueError('campaign routing sandbox has an invalid mount descriptor')
                fd_mounts.append((int(bind.source), bind.dest))
        if fd_mounts:
            if ([dest for _, dest in fd_mounts] != [dest for _, dest in mounts] or
                    len({fd for fd, _ in fd_mounts}) != len(fd_mounts) or
                    mount_identities is None or set(mount_identities) != {dest for _, dest in mounts}):
                raise ValueError('campaign routing sandbox differs from the declared read-only mounts')
            mounts = fd_mounts
        expected = native_run.declared_sandbox_argv(origin / 'inputs', origin / 'run', spec['argv'], mounts=mounts,
                                                    mount_identities=mount_identities)
        if actual != expected:
            raise ValueError('campaign routing sandbox differs from the declared read-only mounts')
    # A killed or crashed adapter can leave no report. Its failed receipt remains provenance,
    # but supplies no routing coverage and can never authorize an output.
    if receipt['artifacts'].get('routing/adapter-report.json') is None:
        if receipt['status'] not in ADOPTABLE:
            return None
        raise ValueError('adoptable routing receipt lacks a safe adapter report')
    for name in ('attempt', 'candidate'):
        path = receipt_dir / 'run/routing' / name
        if os.path.lexists(path):
            _, other = file_tree(path)
            if other:
                raise ValueError('retained routing evidence contains links or special files')
    return verify(receipt_dir / 'run', receipt=receipt)


def _report(run):
    path = Path(run) / 'routing/adapter-report.json'
    if path.is_symlink() or not path.is_file():
        raise ValueError('routing adapter report missing')
    report = json.loads(path.read_text())
    if not isinstance(report, dict) or report.get('schema') != attempt.REPORT_SCHEMA:
        raise ValueError(f'routing adapter report must be {attempt.REPORT_SCHEMA}')
    if set(report) != attempt.REPORT_KEYS:
        raise ValueError('routing adapter report has unknown or missing fields')
    body = {k: v for k, v in report.items() if k != 'identity'}
    if report.get('identity') != attempt.canonical_identity(body):
        raise ValueError('routing adapter report identity mismatch')
    return report


def verify(run, inputs=None, *, receipt=None, layout=None):
    """Re-derive one attempt's outcome from its retained bytes; raise on any disagreement.

    run is the attempt's /work directory. inputs, when given, is the pre-step
    candidate file map (relative path -> sha256) the coordinator captured.
    receipt, when given, is the step's AGENTEE-95 receipt, already checked by
    stage_receipts.validate_receipt; it supplies the inputs, and the report must
    agree with its exit, outputs, artifacts and backend. The report's own
    identity and hashes only show that the bytes agree with each other: the
    receipt, anchored by the campaign, is what authenticates them, and this
    function never replaces it.

    layout overrides the production sandbox layout and pin (the fixture
    router's tests supply theirs). Tool claims are compared with it, never
    taken from the report."""
    run = Path(run)
    report = _report(run)
    try:
        return _verify(run, report, inputs, receipt, layout)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError(f'malformed routing adapter report: {type(exc).__name__}: {exc}') from exc


def _verify(run, report, inputs, receipt, layout):
    routing = run / 'routing'
    expected_pin = (layout or {}).get('pin', current_layout()['pin'])
    claimed = COMPATIBILITY.match(report['adapter'].get('compatibility') or '') if isinstance(
        report['adapter'], dict) else None
    if claimed and claimed.group(1) != expected_pin:
        # Checked first: an older pin's report may not even parse under today's request rules.
        raise StaleRoutingEvidence(claimed.group(1), expected_pin)
    request = attempt.parse_request(_request_argv(report['request']))
    if request != report['request']:
        raise ValueError('routing adapter request is not a valid adapter request')
    qualified = current_layout()
    expected = {**attempt.default_layout(request), 'pin': qualified['pin'], 'code': qualified['code'],
                **(layout or {})}
    if receipt is not None:
        _receipt_step(receipt, request)
        if inputs is not None and inputs != receipt['inputs']:
            raise ValueError('captured inputs differ from the stage receipt inputs')
        inputs = receipt['inputs']
    if inputs is not None and report['inputs']['files'] != inputs:
        raise ValueError('routing attempt inputs differ from the captured pre-step candidate')
    retained = {}
    if (routing / 'attempt').is_dir():
        entries = attempt.tree(routing / 'attempt')
        if attempt.links_of(entries):
            raise ValueError('retained routing diagnostics contain links')
        retained.update({f'attempt/{k}': v for k, v in attempt.files_of(entries).items()})
    for name in ('kicad-route.stdout', 'kicad-route.stderr'):
        if (routing / name).is_file() and not (routing / name).is_symlink():
            retained[name] = digest(routing / name)
    if retained != report['retained']:
        raise ValueError('retained routing artifacts differ from the adapter report')
    sem = report['semantics']
    classification = sem['classification']
    if classification not in attempt.CLASSES:
        raise ValueError('unknown routing classification')
    proposal, coverage, code = attempt.CLASSES[classification]
    if (sem.get('proposal'), sem.get('coverage'), sem.get('adapter_exit')) != (proposal, coverage, code):
        raise ValueError('routing classification does not match its proposal and coverage')
    if not isinstance(sem.get('reasons'), list) or (classification == 'not_run' and not sem['reasons']):
        raise ValueError('routing classification without its reasons')
    process = report['process']
    if sem['exit_code'] != (process['exit_code'] if process is not None else None):
        raise ValueError('reported router exit differs from the observed process exit')
    _tool_claims(report, request, expected, ran=process is not None)
    _staged(routing, report, request, expected, ran=process is not None)
    if report['tool_version'] != attempt.TOOL_VERSION:
        raise ValueError('routing tool version is not the pinned unknown')
    if report['reproducibility'] != attempt.REPRODUCIBILITY[request['backend']]:
        raise ValueError(f'routing reproducibility label is not the {request["backend"]} label')
    candidate = routing / 'candidate' / request['project'] / request['board']
    if proposal == 'none':
        if os.path.lexists(routing / 'candidate') or report['candidate'] is not None:
            raise ValueError(f'a {classification} attempt delivered a routed board')
        if classification != 'contradiction' and (report['candidate_files'] or report['candidate_editor_state']):
            raise ValueError(f'a {classification} attempt records a bound candidate')
    else:
        if (candidate.is_symlink() or not candidate.is_file() or report['candidate'] is None or
                report['candidate'] != {'path': f'candidate/{request["project"]}/{request["board"]}',
                                        'sha256': digest(candidate)} or
                attempt.files_of(attempt.tree(routing / 'candidate')) !=
                {f'{request["project"]}/{request["board"]}': report['candidate']['sha256']}):
            raise ValueError('routed candidate differs from the adapter report')
    if (classification not in ('validation_failed', 'policy_failed') and
            report['rejected_candidate_sha256'] is not None):
        raise ValueError(f'a {classification} attempt records a rejected candidate')
    result = record = None
    if classification == 'not_run':
        if (process is not None or retained or report['discovery'] is not None or
                report['identity_after'] is not None or report['missing_artifacts'] != []):
            raise ValueError('a not-run attempt has router output')
    else:
        if report['missing_artifacts'] != attempt.missing_reports(
                {k[len('attempt/'):] for k in retained if k.startswith('attempt/')}):
            raise ValueError('missing routing artifacts differ from the retained artifacts')
        discovery = report['discovery']
        found = discovery['attempt_dir']
        if found is not None and not _attempt_dir(found, request):
            raise ValueError('discovered attempt directory is not a fresh OUT stamp directory')
        result_found, result = _retained_json(routing, 'result.json', retained)
        record_found, record = _retained_json(routing, 'attempt.json', retained)
        malformed = (result_found and result is None) or (record_found and record is None)
        if classification in ('timeout', 'cancelled'):
            if not process['timed_out' if classification == 'timeout' else 'cancelled']:
                raise ValueError(f'{classification} classification without that observation')
        else:
            if process['timed_out'] or process['cancelled']:
                raise ValueError(f'a {classification} attempt was timed out or cancelled')
            stdout = (routing / 'kicad-route.stdout').read_bytes().decode('utf-8', 'replace')
            refused = attempt.refused_output(stdout)
            derived, _ = attempt.semantics(process['exit_code'], result, record, refused, found is not None,
                                           request)
            if 'layer_policy' in request:
                grade = attempt.layer_policy_grade(request['layer_policy'], routing / 'attempt', result, derived,
                                                  request['board'])
                if derived in ('complete', 'incomplete') and grade['status'] != 'pass':
                    derived = 'policy_failed'
            # A contradiction may sit over reports that alone classify otherwise, because some
            # contradictions come from the file system. It never delivers a board.
            if classification != 'contradiction':
                if derived != classification:
                    raise ValueError(f'retained reports classify as {derived}, not {classification}')
                if malformed or discovery['problems'] or report['identity_after'] != report['identity_before']:
                    raise ValueError(f'a {classification} attempt with discovery, report or tool-state '
                                     'violations is a contradiction')
                generation = attempt.generation_of_components(request['backend'], report['identity_before']['state'])
                if attempt.runtime_problems(request, expected, generation, result):
                    raise ValueError(f'a {classification} attempt ran an engine other than the verified one')
        if proposal != 'none':
            _bind(routing, retained, report, request, expected, result, record)
    backend = attempt.backend_of(result)
    if report['backend'] != backend:
        raise ValueError('routing backend identity differs from the retained result.json')
    if receipt is not None:
        _receipt_agrees(receipt, report, request, routing, backend)
    pairs = attempt.pair_coverage(request, result, record, classification)
    if 'pair_coverage' in sem and sem['pair_coverage'] != pairs:
        raise ValueError('pair coverage differs from retained routing evidence')
    policy = _policy_grade(routing, report, request, result, classification)
    experiment = _experiment_grade(routing, report, request, result, classification, retained, expected)
    return {'classification': classification, 'proposal': proposal, 'coverage': coverage,
            'pair_coverage': pairs,
            'router_exit': sem['exit_code'], 'candidate_sha256': (report['candidate'] or {}).get('sha256'),
            'backend': backend, 'tool_version': dict(attempt.TOOL_VERSION),
            'reproducibility': attempt.REPRODUCIBILITY[request['backend']],
            **({'layer_policy': policy} if policy is not None else {}),
            **({'experiment': experiment} if experiment is not None else {})}


def _experiment_grade(routing, report, request, result, classification, retained, layout):
    """Re-derive the K91 experiment record (AGENTEE-442) from the retained bytes; None when not involved.

    A refused record is honest only on an attempt that delivers no board."""
    sem = report['semantics']
    key = f'attempt/{attempt.EXPERIMENT_POLICY_FILE}'
    if 'experiment_policy' not in request:
        if key in retained:
            raise ValueError('experiment policy file without a declared experiment policy')
    elif report['process'] is not None and classification not in ('contradiction', 'timeout', 'cancelled'):
        if (key not in retained or (routing / key).read_bytes() !=
                attempt.canonical_json(request['experiment_policy']).encode()):
            raise ValueError('experiment policy file differs from the declared experiment policy')
    found = (report.get('discovery') or {}).get('attempt_dir')
    grade = attempt.experiment_grade(request, result, routing / 'attempt',
                                     attempt.experiment_run(request, layout, found,
                                                            report['omitted_design_files']))
    if ('experiment' in sem) != (grade is not None) or sem.get('experiment') != grade:
        raise ValueError('experiment evidence record differs from the retained routing evidence')
    if (grade is not None and grade['status'] == 'refused' and
            classification not in ('contradiction', 'timeout', 'cancelled')):
        raise ValueError(f'a {classification} attempt with refused experiment evidence')
    return grade


def _policy_grade(routing, report, request, result, classification):
    """Re-grade a declared layer policy from the retained reports; None when undeclared."""
    sem = report['semantics']
    if 'layer_policy' not in request:
        if 'layer_policy' in sem:
            raise ValueError('layer policy grade without a declared layer policy')
        return None
    if classification == 'policy_failed':
        result_sha = result['validation'].get('validated_board_sha256')
        if report['rejected_candidate_sha256'] != result_sha:
            raise ValueError('policy-failed board is not the board the router validated')
    graded = classification
    if classification == 'policy_failed':
        graded = 'complete' if result['routing']['status'] == 'COMPLETE' else 'incomplete'
    grade = attempt.layer_policy_grade(request['layer_policy'], routing / 'attempt', result, graded,
                                      request['board'])
    if sem.get('layer_policy') != grade:
        raise ValueError('layer policy grade differs from the retained routing evidence')
    if attempt.CLASSES[classification][0] != 'none' and grade['status'] != 'pass':
        raise ValueError(f'a {classification} attempt delivered a board whose layer policy is {grade["status"]}')
    return grade


def _tool_claims(report, request, layout, *, ran):
    """The tool identity the report claims, against the pin and layout rather than itself."""
    tool = report['tool']
    executable = str(Path(layout['checkout']) / 'bin' / 'kicad-route')
    if (report['adapter'] != {'compatibility': f'kicad-route@{layout["pin"]}'} or tool['pin'] != layout['pin'] or
            tool['kicad_route']['path'] != executable):
        raise ValueError('routing attempt claims a different kicad-route pin or location')
    if not ran:
        return
    # The router starts only after these preconditions held, so an attempt that ran must show them.
    if tool['commit'] != layout['pin'] or tool['kicad_route']['sha256'] != layout['code'].get('bin/kicad-route'):
        raise ValueError('routing attempt ran a kicad-route that is not the pinned code')
    probes = report['state_write_probe']
    if layout['probe'] is not None:
        targets = [str(t) for t in layout['probe']]
    else:
        state, checkout = Path(layout['state']), Path(layout['checkout'])
        components = attempt.state_components(
            request['backend'], attempt.generation_of_components(request['backend'], report['identity_before']['state']))
        targets = [str(state)] + [str(state / c) for c in components] + (
            [str(checkout)] if checkout != state else [])
    if (not isinstance(probes, list) or [p.get('directory') for p in probes] != targets or
            any(p.get('refused') is not True or not p.get('errno') for p in probes)):
        raise ValueError('routing attempt ran without an OS-refused write probe of each state directory')
    process = report['process']
    routing = Path(layout['work']) / 'routing'
    if (process['argv'] != attempt.tool_argv(request, layout) or process['env'] != attempt.tool_env(layout) or
            process['cwd'] != str(routing) or process['timeout_s'] != request['timeout_s']):
        raise ValueError('router argv, environment or timeout differ from the fixed adapter values')
    if report['identity_after'] is None or report['discovery'] is None:
        raise ValueError('routing attempt ran without its after-identities or discovery')


def _staged(routing, report, request, layout, *, ran):
    """The staged original the router read as --dir (AGENTEE-442): at the adapter's
    staging path, its per-file manifest and tree identity the declared input's, and the
    retained staged tree exactly the one the attempt observed after the router ran
    (inputs.staged_after), for every classification. The staged tree is required
    replay evidence: an absent, partial or stale tree refuses (review H1, H3)."""
    inputs = report['inputs']
    if not isinstance(inputs, dict) or set(inputs) != {'files', 'sha256', 'staged', 'staged_after'}:
        raise ValueError('routing attempt inputs have unknown or missing fields')
    staged, after = inputs['staged'], inputs['staged_after']
    path = routing / attempt.ORIGINAL_STAGE
    if staged is None:
        if ran:
            raise ValueError('routing attempt ran without a staged original')
        if after is not None or os.path.lexists(path):
            raise ValueError('an attempt that staged nothing has a staged original')
        return
    if (not isinstance(staged, dict) or set(staged) != {'path', 'root', 'files', 'sha256'} or
            staged['path'] != str(attempt.staged_dir(request, layout)) or
            staged['root'] != str(attempt.staged_root(layout))):
        raise ValueError('staged original is not at the adapter staging path')
    # Staging runs only after every other precondition held, so a staged attempt that
    # did not run is one whose copy differed from the input.
    if (staged['files'] == inputs['files'] and staged['sha256'] == inputs['sha256']) != ran:
        raise ValueError('the staged original differs from the declared input' if ran else
                         'a not-run attempt records a staged original equal to its input')
    if not ran:
        if after is not None:
            raise ValueError('a not-run attempt records a post-run staged original')
        expected = {'status': 'observed', 'files': staged['files'], 'sha256': staged['sha256']}
    else:
        if (not isinstance(after, dict) or set(after) != {'status', 'files', 'sha256'} or
                after['status'] not in ('observed', 'missing', 'unreadable') or
                (after['status'] == 'observed') != (after['files'] is not None and after['sha256'] is not None)):
            raise ValueError('routing attempt has no sealed post-run staged original')
        expected = after
        sem = report['semantics']
        changed = after != {'status': 'observed', 'files': staged['files'], 'sha256': staged['sha256']}
        # A router that wrote its input is an honest contradiction, never adoptable.
        if changed != (sem['classification'] == 'contradiction' and
                       'the staged original changed during the attempt' in sem['reasons']):
            raise ValueError('the post-run staged original and the classification disagree')
    observed = attempt.staged_observation({'work': routing.parent})
    if observed != expected:
        raise ValueError(f'staged original bytes differ from the adapter report (retained tree '
                         f'{observed["status"]}, sealed {expected["status"]})')


def predecessor_input(design, project, experiment=True):
    """Why the host input design/project is not an original project, or None (review H2).

    The sandbox mounts a copy of design and the adapter stages it at a neutral path, which
    kicad-route reads as an original at depth 0. So the input's owning context is judged
    here, on the host path, before anything is copied, by kicad-route's own rule
    (bd1e633 cli.previous_step and experiment._origin): an input that is the candidate
    of the run whose attempt.json lies two levels up is a genuine predecessor, and one
    laid out as a run candidate, or beside a run record, without that owner is a missing
    predecessor. The adapter retains no predecessor receipts, so both are refused.

    A genuine owner or a run record beside the input refuses every step. A parent that
    is only named 'candidate', with no run record, is kicad-route's missing predecessor
    only for its experiment origin, so it refuses only a step that declares an
    experiment policy (experiment): no other step consumes origin evidence."""
    src = (Path(design) / project).resolve()
    prev = src.parent.parent
    try:
        owner = json.loads((prev / 'attempt.json').read_text())
    except (OSError, ValueError):
        owner = None
    if (isinstance(owner, dict) and isinstance(owner.get('candidate_dir'), str) and
            (prev / owner['candidate_dir']).resolve() == src):
        return (f'routing input {src} is the candidate of the kicad-route run {prev}: a predecessor '
                'input, whose ancestry staging would erase; predecessor chains are unsupported')
    if (any(os.path.lexists(prev / n) for n in ('attempt.json', 'result.json')) or
            (experiment and src.parent.name == 'candidate')):
        return (f'routing input {src} is laid out as a kicad-route run candidate without a readable '
                'owning run that owns it: a missing predecessor, not an original project')
    return None


def input_refusal(spec, design):
    """stage_receipts.run_step calls this before it copies design: the refusal of a
    kicad-route adapter step's input, or None for any other step."""
    if spec.get('stage') != 'routing' or not str(spec.get('step', '')).startswith('kicad-route-'):
        return None
    argv = spec['argv']
    return predecessor_input(design, argv[argv.index('--project') + 1], '--experiment-policy' in argv)


def _attempt_dir(found, request):
    parts = PurePosixPath(found).parts
    return (str(PurePosixPath(found)) == found and len(parts) == 5 and
            parts[:4] == ('out', 'runs', request['name'], request['label']) and attempt.STAMP.match(parts[4]))


def _bind(routing, retained, report, request, layout, result, record):
    """Re-check every binding of an eligible candidate the retained evidence supports."""
    if report['missing_artifacts']:
        raise ValueError(f'eligible attempt lacks {", ".join(report["missing_artifacts"])}')
    # The adapter's sandbox origin, not the host path: report paths are resolved lexically.
    origin = Path(layout['work']) / 'routing'
    run_dir = origin / report['discovery']['attempt_dir']
    cand_dir = run_dir / record['candidate_dir']
    _, snapshot = _retained_json(routing, 'snapshot.json', retained)
    files = snapshot.get('files') if isinstance(snapshot, dict) else None
    problems = attempt.candidate_binding_problems(
        request, origin, run_dir, cand_dir, result, report['inputs']['files'], files,
        report['candidate_files'], report['candidate_editor_state'], report['candidate']['sha256'],
        retained.get('attempt/' + attempt.COPPER_OWNERSHIP_RECORD), retained=True)
    if problems:
        raise ValueError(problems[0])


def _receipt_step(receipt, request):
    """The receipt must be this adapter's routing step for the same request."""
    spec = receipt['spec']
    target = f'{request["project"]}/{request["board"]}'
    mounts = {m['at'] for m in spec['mounts']}
    if (spec['stage'] != 'routing' or spec['step'] != f'kicad-route-{request["backend"]}' or
            spec['writes'] != {target: f'routing/candidate/{target}'} or spec['adoptable_exits'] != [0, 2] or
            spec['backend_report'] != {'artifact': 'routing/attempt/result.json', 'key': 'backend'} or
            spec['artifacts'] != list(ARTIFACTS) or spec['source'] != attempt.SANDBOX['checkout'] + '/src' or
            spec['version_argv'] is not None or
            ADAPTER_AT not in mounts or attempt.SANDBOX['checkout'] not in mounts or
            (attempt.SANDBOX['state'] in mounts) != (request['state'] == 'separate') or
            attempt.parse_request(spec['argv'][4:]) != request):
        raise ValueError('stage receipt is not this routing adapter step')


def _receipt_agrees(receipt, report, request, routing, backend):
    target = f'{request["project"]}/{request["board"]}'
    if receipt['exit_code'] != report['semantics']['adapter_exit']:
        raise ValueError('stage receipt exit differs from the routing adapter exit')
    if receipt['outputs'] != {target: (report['candidate'] or {}).get('sha256')}:
        raise ValueError('stage receipt output differs from the routed candidate')
    if receipt['artifacts'].get('routing/adapter-report.json') != digest(routing / 'adapter-report.json'):
        raise ValueError('stage receipt does not anchor this routing adapter report')
    # The receipt seals the retained staged tree's files; they must be the report's.
    prefix = f'routing/{attempt.ORIGINAL_STAGE}'
    sealed = {k[len(prefix) + 1:]: v for k, v in receipt['artifacts'].items()
              if k.startswith(prefix + '/') and v is not None}
    after = report['inputs']['staged_after'] or (
        {'files': report['inputs']['staged']['files']} if report['inputs']['staged'] else None)
    if after is not None and after.get('files') is not None and sealed != after['files']:
        raise ValueError('stage receipt staged-original artifacts differ from the routing adapter report')
    # The receipt's own strict parser reports what it read; a reported backend must be the same one.
    if backend['status'] == 'reported' and receipt['tool']['backend'] != backend:
        raise ValueError('stage receipt backend differs from the retained result.json')


def _retained_json(routing, name, retained):
    """(retained, value): value is None when the retained file is not JSON or exceeds the limit."""
    key = f'attempt/{name}'
    if key not in retained:
        return False, None
    return True, attempt.load_json(routing / key, [], name)


def _request_argv(request):
    if not isinstance(request, dict):
        raise ValueError('routing adapter request must be an object')
    flags = {'project': '--project', 'board': '--board', 'schematic': '--schematic', 'name': '--name',
             'label': '--label', 'budget_s': '--budget', 'timeout_s': '--timeout', 'backend': '--backend',
             'state': '--state'}
    if 'diff_pairs' in request:
        flags['diff_pairs'] = '--diff-pairs'
    if 'layer_policy' in request:
        flags['layer_policy'] = '--layer-policy'
    if 'experiment_policy' in request:
        flags['experiment_policy'] = '--experiment-policy'
    if set(request) != set(flags):
        raise ValueError('routing adapter request has unknown or missing fields')
    argv = []
    for key, flag in flags.items():
        try:
            value = (attempt.canonical_json(request[key]) if key in ('layer_policy', 'experiment_policy')
                     else str(request[key]))
        except (TypeError, ValueError):
            raise ValueError(f'routing adapter {key.replace("_", " ")} is not JSON') from None
        argv += [flag, value]
    return argv


def validate_router_options(router_config):
    """Validate and normalize board-level kicad-route options."""
    import math
    ac_match = router_config.get('ac_match')
    if 'ac_match' in router_config:
        if not isinstance(ac_match, dict) or set(ac_match) != {'chains', 'tolerance_mm', 'amplitude_mm'}:
            raise ValueError('router.ac_match requires chains, tolerance_mm and amplitude_mm')
        chains = ac_match['chains']
        if (not isinstance(chains, list) or not chains
                or any(not isinstance(refs, list) or len(refs) < 2
                       or not all(isinstance(ref, str) and ref for ref in refs) for refs in chains)):
            raise ValueError('router.ac_match needs explicit series-part reference chains')
        refs = [ref for chain in chains for ref in chain]
        if len(set(refs)) != len(refs):
            raise ValueError('router.ac_match series-part references must not repeat')
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < .000001
               for key, value in ac_match.items() if key != 'chains'):
            raise ValueError('router.ac_match controls must be finite and positive')
    length_match = router_config.get('length_match')
    if 'length_match' in router_config:
        unit = 'ps' if isinstance(length_match, dict) and 'tolerance_ps' in length_match else 'mm'
        required = {'groups', 'tolerance_' + unit, 'amplitude_mm'}
        if (not isinstance(length_match, dict) or not required <= set(length_match)
                or set(length_match) - required - {'spacing_widths'}):
            raise ValueError('router.length_match requires groups, exactly one of tolerance_mm or tolerance_ps, and amplitude_mm; spacing_widths is optional')
        length_match = {'spacing_widths': 2, **length_match}
        members = length_match['groups']
        if (not isinstance(members, list) or not members
                or any(not isinstance(group, list) or len(group) < 2
                       or not all(isinstance(name, str) and name for name in group)
                       or len(set(group)) != len(group) for group in members)):
            raise ValueError('router.length_match groups need at least two distinct exact net names')
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < .000001
               for key, value in length_match.items() if key != 'groups'):
            raise ValueError('router.length_match controls must be finite and positive')
        if length_match['spacing_widths'] < 1:
            raise ValueError('router.length_match spacing must be at least one track width')
    bus = router_config.get('bus')
    if 'bus' in router_config:
        fields = {'detection_radius_mm', 'attraction_radius_mm', 'attraction_bonus', 'min_nets'}
        if not isinstance(bus, dict) or set(bus) - fields:
            raise ValueError('router.bus accepts detection_radius_mm, attraction_radius_mm, attraction_bonus and min_nets only')
        for key, value in bus.items():
            if key.endswith('_mm'):
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0.000001:
                    raise ValueError('bus radii must be finite and at least 0.000001 mm')
            elif type(value) is not int or value < (2 if key == 'min_nets' else 0):
                raise ValueError('bus minimum nets must be an integer at least two and attraction bonus a nonnegative integer')
    pair_layers = router_config.get('pair_layers')
    if 'pair_layers' in router_config:
        if (not isinstance(pair_layers, list) or not pair_layers
                or any(not isinstance(layer, str) or not layer for layer in pair_layers)
                or len(set(pair_layers)) != len(pair_layers)):
            raise ValueError('router.pair_layers requires a nonempty list of distinct copper layer names')
    impedance = router_config.get('pair_impedance')
    if 'pair_impedance' in router_config:
        fields = {'target_ohm', 'tolerance_percent', 'reference_net', 'max_uncoupled_mm'}
        if not isinstance(impedance, dict) or set(impedance) != fields:
            raise ValueError('router.pair_impedance requires target_ohm, tolerance_percent, reference_net and max_uncoupled_mm')
        if (not isinstance(impedance['reference_net'], str) or not impedance['reference_net'].strip()
                or any(type(impedance[key]) not in (int, float) or not math.isfinite(impedance[key])
                       or impedance[key] <= 0 for key in fields - {'reference_net'})
                or impedance['tolerance_percent'] >= 100):
            raise ValueError('pair impedance needs positive finite numbers, tolerance below 100 percent and a reference net')
        if not pair_layers or len(pair_layers) != 1:
            raise ValueError('qualified pair impedance requires exactly one explicit pair layer')
    matching = router_config.get('intra_pair_match')
    if 'intra_pair_match' in router_config:
        if not isinstance(matching, dict) or set(matching) != {'tolerance_mm', 'amplitude_mm'}:
            raise ValueError('router.intra_pair_match requires tolerance_mm and amplitude_mm only')
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0.000001
               for value in matching.values()):
            raise ValueError('intra-pair matching values must be finite and at least 0.000001 mm')
    return {'ac_match': ac_match, 'length_match': length_match, 'bus': bus,
            'pair_layers': pair_layers, 'pair_impedance': impedance, 'intra_pair_match': matching}


def board_route_argv(checkout, staged_dir, out_dir, *, design_name='board', owned_copper=False, budget_s,
                     grid_step_mm=None, pair_layers=None, pair_impedance=None, intra_pair_match=None,
                     bus=None, length_match=None, ac_match=None):
    """Build the pinned kicad-route CLI command for board-only / schematic-optional invocations."""
    argv = [attempt.PYTHON, '-B', str(Path(checkout) / 'bin' / 'kicad-route'), 'run', '--out', str(out_dir),
            '--json', '--backend', attempt.PIN_BACKEND_NAMES['builtin'], '--dir', str(staged_dir),
            '--board', 'board.kicad_pcb', '--schematic', 'board.kicad_sch', '--name', design_name,
            '--label', 'placement-job', '--protect', 'locked' if owned_copper else 'existing',
            '--budget', str(int(budget_s)), '--diff-pairs', 'auto']
    if grid_step_mm is not None:
        argv += ['--grid-step', str(grid_step_mm)]
    if pair_layers is not None:
        argv += ['--pair-layers', *pair_layers]
    if pair_impedance is not None:
        argv += ['--pair-impedance', str(pair_impedance['target_ohm']),
                 '--impedance-tolerance', str(pair_impedance['tolerance_percent']),
                 '--impedance-reference', pair_impedance['reference_net'],
                 '--impedance-uncoupled', str(pair_impedance['max_uncoupled_mm'])]
    if intra_pair_match is not None:
        argv += ['--diff-pair-intra-match', '--length-match-tolerance', str(intra_pair_match['tolerance_mm']),
                 '--meander-amplitude', str(intra_pair_match['amplitude_mm'])]
    if bus is not None:
        argv += ['--bus']
        for key, value in bus.items():
            argv += ['--bus-' + key.removesuffix('_mm').replace('_', '-'), str(value)]
    if length_match is not None:
        for group in length_match['groups']:
            argv += ['--length-match-group-json', json.dumps(group)]
        unit = 'ps' if 'tolerance_ps' in length_match else 'mm'
        flag = '--group-delay-tolerance' if unit == 'ps' else '--group-match-tolerance'
        argv += [flag, str(length_match['tolerance_' + unit]),
                 '--group-meander-amplitude', str(length_match['amplitude_mm']),
                 '--group-meander-spacing', str(length_match['spacing_widths'])]
    if ac_match is not None:
        for refs in ac_match['chains']:
            argv += ['--ac-couple-chain-json', json.dumps(refs)]
        argv += ['--ac-match-tolerance', str(ac_match['tolerance_mm']),
                 '--ac-meander-amplitude', str(ac_match['amplitude_mm'])]
    return argv


def board_route_env(checkout, home):
    """Build the isolated subprocess environment for a board-level kicad-route run."""
    Path(home).mkdir(parents=True, exist_ok=True)
    return {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'TMPDIR': str(home), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
            'KICAD_ROUTE_STATE': str(checkout), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_OPTIONAL_LOCKS': '0'}


def board_retry_argv(argv, retry_dir, budget_s, feedback, pads, board_path):
    """Build the bounded repair retry CLI command for a board-level kicad-route run."""
    retry = list(argv)
    retry[retry.index('--out') + 1] = str(Path(retry_dir) / 'out')
    retry[retry.index('--budget') + 1] = str(int(budget_s))
    if feedback['kind'] == 'thermal_corridors':
        pads = feedback['pads']
    else:
        retry[retry.index('--dir') + 1] = str(Path(board_path).parent)
        del retry[retry.index('--diff-pairs'):retry.index('--diff-pairs') + 2]
        retry += ['--nets', *feedback['nets'], '--backend-arg=--force-reroute']
    for pad in pads:
        retry += ['--reserve-spoke-pad', pad]
    return retry, pads


def spokes_argv(checkout, run_dir):
    """Build the pinned kicad-route spokes diagnostic command."""
    return [attempt.PYTHON, '-B', str(Path(checkout) / 'bin/kicad-route'), 'spokes', str(run_dir)]


def bind_board_attempt(route_dir, argv, exit_code):
    """Bind one board-level kicad-route attempt output directory and verify its candidate digest."""
    route_dir = Path(route_dir)
    record = {'argv': argv, 'exit_code': exit_code}
    results = sorted((route_dir / 'out').rglob('result.json'))
    if len(results) != 1:
        return {**record, 'status': 'missing_output'}
    data = results[0].read_bytes()
    # The same result rule as the sandboxed route: schema, keys and statuses first.
    outcome, reasons = attempt.classify(data, exit_code, paired='--diff-pairs' in argv)
    record.update(result_path=str(results[0]), result_sha256=digest(results[0]),
                  classification={'outcome': outcome, 'reasons': reasons})
    if outcome == 'refused':
        return {**record, 'status': 'refused_result'}
    result = json.loads(data)
    record.update(router_exit=result.get('exit_code'), router_status=result.get('status') or result.get('outcome'))
    candidate = result.get('candidate')
    if not candidate:
        return {**record, 'status': 'no_candidate'}
    board = route_dir / candidate['board']
    sha = digest(board)
    if sha != result.get('validation', {}).get('candidate_board_sha256'):
        return {**record, 'status': 'stale_candidate'}
    return {**record, 'status': 'routed', 'board': str(board), 'board_sha256': sha,
            'router_accepted': outcome == 'accepted',
            **({'impedance_plan': result['settings']['pair_impedance']}
               if 'pair_impedance' in result.get('settings', {}) else {})}


def preflight_stackup_argv(checkout, board_path, layer, *, python=None):
    """Build the kicad-route pair-impedance stackup preflight command."""
    import sys
    return [python or sys.executable, '-B', '-c',
            'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); '
            'from kicad_route.pair_impedance import model; model(Path(sys.argv[2]), sys.argv[3])',
            str(Path(checkout) / 'src'), str(board_path), layer]


def preflight_delay_argv(checkout, board_path, nets, *, python=None):
    """Build the kicad-route native delay model preflight command."""
    import sys
    return [python or sys.executable, '-B', '-c',
            'import json,sys; sys.path.insert(0, sys.argv[1]); '
            'from native_delay import profiles; '
            'profiles(sys.argv[2], json.loads(sys.argv[3]), required=True)',
            str(Path(checkout) / 'src/kicad_route/router/py_router'),
            str(board_path), json.dumps(sorted(nets))]


def post_route_checker_argv(checkout, kind, board_path, request_path, result_path, *, python=None):
    """Build the kicad-route post-route checker command (pair_impedance, length_groups, ac_matching)."""
    import sys
    scripts = {'pair_impedance': 'pair_impedance.py', 'length_groups': 'length_groups.py',
               'ac_matching': 'ac_matching.py'}
    checker = Path(checkout) / 'src/kicad_route' / scripts[kind]
    argv = [python or sys.executable, '-B', str(checker), str(board_path), str(request_path), str(result_path)]
    if kind == 'pair_impedance':
        argv.append('--check')
    return argv

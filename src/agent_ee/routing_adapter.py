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

import json
import os
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
ARTIFACTS = ('routing/adapter-report.json', 'routing/kicad-route.stdout', 'routing/kicad-route.stderr',
             'routing/attempt/result.json', 'routing/attempt/attempt.json', 'routing/attempt/RESULT.md',
             'routing/attempt/snapshot.json', 'routing/attempt/validate', 'routing/attempt/facts',
             'routing/attempt/work')


def preflight(checkout, state=None, backend='krt'):
    """Host-side identity without running git, routing or installation.

    Running `git status` could rewrite the checkout's index, so HEAD is read
    from the git files and code is compared with the pinned blob hashes."""
    checkout = Path(checkout).resolve()
    state = Path(state).resolve() if state is not None else checkout
    problems = []
    try:
        commit = attempt.checkout_commit(checkout)
    except OSError as exc:
        commit, problems = None, [f'cannot read the checkout HEAD: {exc}']
    if commit != attempt.PIN:
        problems.append(f'checkout HEAD {commit} is not the pinned {attempt.PIN}; no automatic update')
    code_problems = attempt.verify_code(checkout)
    problems += code_problems
    if not code_problems:
        problems += attempt.verify_state(checkout, state, backend)
    for rel in attempt.STATE_REQUIRED[backend]:
        if not os.path.lexists(state / rel):
            problems.append(f'missing installed backend dependency: KICAD_ROUTE_STATE/{rel}')
    return {'checkout': str(checkout), 'state': str(state), 'commit': commit, 'pin': attempt.PIN,
            'executable_sha256': digest(checkout / 'bin/kicad-route') if (checkout / 'bin/kicad-route').is_file() else None,
            'adapter_sha256': digest(ADAPTER_SOURCE), 'problems': problems}


def step(*, project, board, schematic, name, label, checkout, state=None, budget=300, timeout=None,
         backend='krt', diff_pairs=None, layer_policy=None, experiment_policy=None):
    """The AGENTEE-95 step declaration for one attempt. Raises ValueError if the tool does not qualify.

    layer_policy (AGENTEE-231) is the declared allowed signal layers, optional KRT
    layer costs, pour area-loss bound and optional RF reference plane.
    experiment_policy (AGENTEE-442) is a five-field routability-policy/1 declaration the
    router receives as --experiment-policy; its experiment evidence is retained or refused."""
    check = preflight(checkout, state, backend)
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
    return _step(request, check['checkout'], check['state'] if separate else None)


def _step(request, checkout, state):
    """Pure declaration construction, also used for retained campaign replay."""
    mounts = [{'source': str(ADAPTER_SOURCE), 'at': ADAPTER_AT},
              {'source': checkout, 'at': attempt.SANDBOX['checkout']}, *BINDING_MOUNTS]
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
        'backend': value.get('backend', 'krt'), 'budget_s': budget, 'timeout_s': timeout,
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
    return {'spec': spec, 'tool': observe_tool(spec), 'pin': attempt.PIN,
            'adapter_sha256': digest(ADAPTER_SOURCE)}


def validate_binding(binding, declaration, manifest, mutable, policy_files):
    expected = campaign_spec(declaration, manifest, mutable)
    if (not isinstance(binding, dict) or set(binding) != {'spec', 'tool', 'pin', 'adapter_sha256'} or
            binding['spec'] != expected or binding['pin'] != attempt.PIN or
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
    expected_mounts = {ADAPTER_AT, attempt.SANDBOX['checkout'], *(m['at'] for m in BINDING_MOUNTS)}
    if separate:
        expected_mounts.add(attempt.SANDBOX['state'])
    if (set(mounts) != expected_mounts or spec['argv'][:4] != [attempt.PYTHON, '-I', '-B', ADAPTER_AT] or
            (host and mounts[ADAPTER_AT] != str(ADAPTER_SOURCE))):
        raise ValueError('campaign routing step differs from the pinned adapter command or mounts')
    if host:
        if any(mounts[m['at']] != m['source'] for m in BINDING_MOUNTS):
            raise ValueError('routing binding helper mounts differ from captured policy')
        check = preflight(checkout, state, request['backend'])
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
    from .executor import sandbox_argv, sandbox_invocation
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
        _, mount_identities = sandbox_invocation(actual)
        mounts = [(m['source'], m['at']) for m in spec['mounts']]
        options = actual[:actual.index('--')]
        fd_mounts = []
        for index, token in enumerate(options):
            if token == '--ro-bind-fd':
                if (index + 2 >= len(options) or not options[index + 1].isdecimal() or
                        int(options[index + 1]) < 3):
                    raise ValueError('campaign routing sandbox has an invalid mount descriptor')
                fd_mounts.append((int(options[index + 1]), options[index + 2]))
        if fd_mounts:
            if ([dest for _, dest in fd_mounts] != [dest for _, dest in mounts] or
                    len({fd for fd, _ in fd_mounts}) != len(fd_mounts) or
                    mount_identities is None or set(mount_identities) != {dest for _, dest in mounts}):
                raise ValueError('campaign routing sandbox differs from the declared read-only mounts')
            mounts = fd_mounts
        expected = sandbox_argv(origin / 'inputs', origin / 'run', spec['argv'], network=False,
                                extra_ro=mounts, mount_identities=mount_identities)
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
    request = attempt.parse_request(_request_argv(report['request']))
    if request != report['request']:
        raise ValueError('routing adapter request is not a valid adapter request')
    expected = {**attempt.default_layout(request), **(layout or {})}
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
    experiment = _experiment_grade(routing, report, request, result, classification, retained)
    return {'classification': classification, 'proposal': proposal, 'coverage': coverage,
            'pair_coverage': pairs,
            'router_exit': sem['exit_code'], 'candidate_sha256': (report['candidate'] or {}).get('sha256'),
            'backend': backend, 'tool_version': dict(attempt.TOOL_VERSION),
            'reproducibility': attempt.REPRODUCIBILITY[request['backend']],
            **({'layer_policy': policy} if policy is not None else {}),
            **({'experiment': experiment} if experiment is not None else {})}


def _experiment_grade(routing, report, request, result, classification, retained):
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
                                     PurePosixPath(found).name if isinstance(found, str) else None)
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
        targets = [str(state)] + [str(state / c) for c in attempt.STATE_COMPONENTS[request['backend']]] + (
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


def _attempt_dir(found, request):
    parts = PurePosixPath(found).parts
    return (str(PurePosixPath(found)) == found and len(parts) == 5 and
            parts[:4] == ('out', 'runs', request['name'], request['label']) and attempt.STAMP.match(parts[4]))


def _bind(routing, retained, report, request, layout, result, record):
    """Re-check every binding of an eligible candidate the retained evidence supports."""
    sha = report['candidate']['sha256']
    if report['missing_artifacts']:
        raise ValueError(f'eligible attempt lacks {", ".join(report["missing_artifacts"])}')
    validation = result['validation']
    if not attempt.validated_candidate(validation, sha):
        raise ValueError('routed candidate is not the board the router validated')
    if validation.get('restored_project_files') != []:
        raise ValueError('the router restored project files of an eligible attempt')
    # The adapter's sandbox origin, not the host path: report paths are resolved lexically.
    origin = Path(layout['work']) / 'routing'
    run_dir = origin / report['discovery']['attempt_dir']
    cand_dir = run_dir / record['candidate_dir']
    if (attempt.resolve_reported(result['run_dir'], origin, origin) != run_dir or
            attempt.resolve_reported(result['candidate']['dir'], origin, origin) != cand_dir or
            attempt.resolve_reported(result['candidate']['board'], origin, origin) != cand_dir / request['board']):
        raise ValueError('result paths are not inside the discovered attempt')
    found, snapshot = _retained_json(routing, 'snapshot.json', retained)
    files = snapshot.get('files') if isinstance(snapshot, dict) else None
    if (not isinstance(files, dict) or any(not isinstance(v, str) for v in files.values()) or
            request['board'] not in files or request['schematic'] not in files):
        raise ValueError('routing reference snapshot lacks the board or schematic')
    prefix = request['project'] + '/'
    if any(report['inputs']['files'].get(prefix + rel) != sha_ for rel, sha_ in files.items()):
        raise ValueError('routing reference snapshot differs from the attempt inputs')
    if attempt.fingerprint(files) != result['input']['fingerprint']:
        raise ValueError('routing input fingerprint is not the retained snapshot fingerprint')
    inventory, editor = report['candidate_files'], report['candidate_editor_state']
    state = PurePosixPath(request['board']).stem + '.kicad_prl'
    if not (editor == {} or (list(editor) == [state] and state not in files)):
        raise ValueError('candidate editor state is not the board editor-state file')
    if inventory != {**files, **editor, request['board']: sha}:
        raise ValueError('candidate inventory is not the snapshot with only the board routed')


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

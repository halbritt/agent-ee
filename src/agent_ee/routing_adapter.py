"""Coordinator half of the kicad-route routing adapter (AGENTEE-93).

`step()` declares one routing attempt in the AGENTEE-95 `agent-ee.stage-step/1`
form: the coordinator, never the writer, runs it in the networkless sandbox
with the kicad-route checkout, its state directory and the attempt script
mounted read-only. `verify()` re-derives the attempt's outcome from the retained
bytes alone, so a report the attempt wrote is never taken on its word.

kicad-route is used at the commit its checkout holds; each attempt records that
commit, the clean-tree state and a code digest (kicad_route_attempt.checkout_record).
A finished attempt is verified against what it recorded, not against this host's
checkout. Work that continues a campaign must run on the checkout its binding
recorded (validate_live_binding).

Routing receipts and reports are provenance. A routed proposal still needs
agent-ee's own fresh checks after capture (DRC with schematic parity and zero
unconnected items, copper-width/1, compiled routing rules) before anything is
accepted.
"""

import json
import os
import re
from pathlib import Path, PurePosixPath

from . import kicad_route_attempt as attempt
from .storage import digest

resolve_kicad_route_checkout = attempt.resolve_kicad_route_checkout
resolve_kicad_route_engine = attempt.resolve_kicad_route_engine
resolve_native_bridge = attempt.resolve_native_bridge
is_rust_capable_checkout = attempt.is_rust_capable_checkout
RE01OwnershipError = attempt.RE01OwnershipError
check_re01_command = attempt.check_re01_command
re01_enforced = attempt.re01_enforced

ADAPTER_AT = '/opt/agent-ee-routing/kicad_route_attempt.py'
ADAPTER_SOURCE = Path(attempt.__file__).resolve()
BINDING_MOUNTS = [{'source': str(ADAPTER_SOURCE.with_name(name)),
                   'at': '/opt/agent-ee-routing/' + name}
                  for name in ('net_binding.py', 'symbol_library.py')]
# Hashing the adapter, code and state mounts, and retaining diagnostics, happen
# after the router's own timeout inside the same step budget.
STEP_MARGIN_S = 300


class StaleRoutingEvidence(ValueError):
    """Work would continue on routing evidence recorded with another kicad-route commit.

    A finished attempt verifies against the commit it recorded. Continuing a campaign
    (another routing turn on its frozen binding) needs this host's checkout to be the
    one the binding recorded; otherwise the campaign is rerun on the current checkout.
    Retained bytes stay as they are; this only names why they cannot be continued."""

    def __init__(self, recorded, current, what='campaign routing binding'):
        super().__init__(f'stale routing evidence: {what} recorded kicad-route {recorded}, but the checkout '
                         f'is now {current}; rerun it on the current checkout')
        self.recorded, self.current = recorded, current
ARTIFACTS = ('routing/adapter-report.json', 'routing/kicad-route.stdout', 'routing/kicad-route.stderr',
             'routing/attempt/result.json', 'routing/attempt/attempt.json', 'routing/attempt/RESULT.md',
             'routing/attempt/snapshot.json', 'routing/attempt/validate', 'routing/attempt/facts',
             'routing/attempt/work', 'routing/attempt/' + attempt.NATIVE_OPERATION_FILE,
             'routing/attempt/' + attempt.NATIVE_REQUEST_FILE, 'routing/' + attempt.ORIGINAL_STAGE)


# The image's kicad-route install record (docker/kicad-route/install.sh); a test names another file.
INSTALL_RECORD_ENV = 'AGENT_EE_KICAD_ROUTE_INSTALL_RECORD'
INSTALL_RECORD = '/opt/kicad-route.install.json'
INSTALL_RECORD_SCHEMA = 'agent-ee.kicad-route-install/2'


def read_install_record(path=None):
    """{'path', 'sha256', 'record'} for the image's kicad-route install record, or None when there is none.

    A host install without the image has no record; every routing attempt still records the checkout
    it runs. A record that is not one install.sh writes (schema, a 40-hex commit recorded clean, the
    installed checkout path) raises ValueError."""
    path = Path(path) if path is not None else Path(os.environ.get(INSTALL_RECORD_ENV) or INSTALL_RECORD)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        record = json.loads(raw.decode('utf-8'))
    except ValueError as exc:
        raise ValueError(f'kicad-route install record {path} is not JSON') from exc
    route = record.get('kicad_route') if isinstance(record, dict) else None
    if (not isinstance(record, dict) or record.get('schema') != INSTALL_RECORD_SCHEMA or not isinstance(route, dict)
            or not isinstance(route.get('commit'), str) or not attempt.COMMIT.match(route['commit'])
            or route.get('clean') is not True or not isinstance(route.get('path'), str)):
        raise ValueError(f'kicad-route install record {path} is not one docker/kicad-route/install.sh writes')
    return {'path': str(path), 'sha256': digest(path), 'record': record}


def legacy_claim(report):
    """The commit a report written before 2026-11-03 recorded as its tool.pin, else None.

    Those reports compared the checkout with a committed table of one commit's file
    digests (retired); they keep verifying against the commit they recorded."""
    tool = report.get('tool') if isinstance(report, dict) else None
    return tool.get('pin') if isinstance(tool, dict) and 'pin' in tool else None


def preflight(checkout=None, state=None, backend='builtin'):
    """Host-side identity without running routing or installation.

    The checkout is recorded and qualified by the same attempt.preflight the sandbox
    runs: HEAD from the git files, the clean-tree state from git with optional locks
    off, and the code digest. Any clean commit qualifies; the result names it."""
    checkout = attempt.resolve_kicad_route_checkout(checkout).resolve()
    state = Path(state).resolve() if state is not None else checkout
    record, _, problems = attempt.preflight(checkout, state, backend, mounted_at=attempt.SANDBOX[
        'checkout' if state == checkout else 'state'])
    rust_capable = attempt.is_rust_capable_checkout(checkout)
    engine_bin = attempt.resolve_kicad_route_engine(checkout) if rust_capable else None
    exec_sha = (digest(engine_bin) if engine_bin is not None and engine_bin.is_file()
                else (digest(checkout / 'bin/kicad-route') if (checkout / 'bin/kicad-route').is_file() else None))
    result = {'checkout': str(checkout), 'state': str(state), **record,
              'executable_sha256': exec_sha,
              'adapter_sha256': digest(ADAPTER_SOURCE), 'problems': problems}
    if rust_capable:
        bridge = checkout / 'tools' / 'rust_engine_kicad.py'
        cargo_lock = checkout / 'rust-engine' / 'Cargo.lock'
        license_file = checkout / 'LICENSE'
        result.update(
            rust_capable=True,
            engine=str(engine_bin) if engine_bin is not None else None,
            engine_sha256=digest(engine_bin) if engine_bin is not None and engine_bin.is_file() else None,
            native_bridge_sha256=digest(bridge) if bridge.is_file() else None,
            cargo_lock_sha256=digest(cargo_lock) if cargo_lock.is_file() else None,
            license_sha256=digest(license_file) if license_file.is_file() else None,
        )
    return result


def step(*, project, board, schematic, name, label, checkout=None, state=None, budget=300, timeout=None,
         backend='builtin', diff_pairs=None, layer_policy=None, experiment_policy=None):
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
    if attempt.is_native_request(request, check['checkout']):
        unsupported = attempt.native_unsupported_problems(request)
        if unsupported:
            raise ValueError('route-native refuses unsupported request options: ' + '; '.join(unsupported))
    return _step(request, check['checkout'], check['state'] if separate else None)


def _step(request, checkout, state, legacy_table=None):
    """Pure declaration construction, also used for retained campaign replay.

    legacy_table is the host path a retained step (2026-10-26 to 2026-11-03) mounted
    the retired commit table from; new steps mount no table."""
    mounts = [{'source': str(ADAPTER_SOURCE), 'at': ADAPTER_AT},
              {'source': checkout, 'at': attempt.SANDBOX['checkout']}, *BINDING_MOUNTS,
              *([{'source': legacy_table, 'at': attempt.LEGACY_TABLE_AT}] if legacy_table is not None else [])]
    if state is not None:
        mounts.append({'source': state, 'at': attempt.SANDBOX['state']})
    native = attempt.is_native_request(request, checkout)
    if native:
        engine_bin = attempt.resolve_kicad_route_engine(checkout)
        if engine_bin is not None and engine_bin.is_file():
            real_engine = Path(engine_bin).resolve()
            if not real_engine.is_relative_to(Path(checkout).resolve()):
                mounts.append({'source': str(real_engine), 'at': attempt.SANDBOX['engine']})
    target = f'{request["project"]}/{request["board"]}'
    backend_report = ({'artifact': f'routing/attempt/{attempt.NATIVE_OPERATION_FILE}', 'key': 'binary'}
                      if native else {'artifact': 'routing/attempt/result.json', 'key': 'backend'})
    return {'schema': 'agent-ee.stage-step/1', 'stage': 'routing', 'step': f'kicad-route-{request["backend"]}',
            'argv': [attempt.PYTHON, '-I', '-B', ADAPTER_AT, *_request_argv(request)],
            'mounts': mounts, 'source': attempt.SANDBOX['checkout'] + '/src',
            'backend_report': backend_report,
            'writes': {target: f'routing/candidate/{target}'}, 'artifacts': list(ARTIFACTS),
            'adoptable_exits': [0, 2], 'timeout_s': request['timeout_s'] + STEP_MARGIN_S}


def validate_declaration(value):
    """Strict campaign opt-in; command, mounts and environment are not configurable."""
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


def campaign_spec(declaration, manifest, mutable, legacy_table=None):
    value = validate_declaration(declaration)
    scope = manifest['scope']
    stages = scope.get('stages')
    if not isinstance(stages, dict) or 'routing' not in stages.get('order', []):
        raise ValueError('routing_adapter requires a staged scope with a routing stage')
    board = f'{value["project"]}/{value["board"]}'
    schematic = f'{value["project"]}/{value["schematic"]}'
    if board != scope['board'] or schematic != scope['schematic'] or board not in mutable:
        raise ValueError('routing adapter must select the scope board/schematic and a mutable board')
    return declaration_spec(value, legacy_table)


def declaration_spec(value, legacy_table=None):
    """The step a validated declaration runs, without the campaign scope checks."""
    from .stage_receipts import validate_step
    request = attempt.parse_request(_request_argv({
        **{k: value[k] for k in ('project', 'board', 'schematic', 'name', 'label', 'backend')},
        'budget_s': value['budget'], 'timeout_s': value['timeout'],
        'state': 'separate' if value['state'] else 'checkout',
        **({'diff_pairs': value['diff_pairs']} if 'diff_pairs' in value else {}),
        **({'layer_policy': value['layer_policy']} if 'layer_policy' in value else {}),
        **({'experiment_policy': value['experiment_policy']} if 'experiment_policy' in value else {})}))
    return validate_step(_step(request, value['checkout'], value['state'], legacy_table), host=False)


def checkout_of(spec):
    """The host checkout a routing step mounts."""
    return Path({m['at']: m['source'] for m in spec['mounts']}[attempt.SANDBOX['checkout']])


def campaign_binding(declaration, manifest, mutable):
    """A campaign's frozen routing step: the step, the observed tool mounts, the adapter digest
    and the kicad-route commit the (clean) checkout held when the campaign bound it."""
    from .stage_receipts import observe_tool
    spec = campaign_spec(declaration, manifest, mutable)
    validate_campaign_step(spec)
    return {'spec': spec, 'tool': observe_tool(spec), 'commit': attempt.checkout_commit(checkout_of(spec)),
            'adapter_sha256': digest(ADAPTER_SOURCE)}


def validate_binding(binding, declaration, manifest, mutable, policy_files):
    """A retained campaign's routing binding against what it recorded: its declaration's step,
    the adapter digest of the captured policy and a recorded kicad-route commit. This host's
    checkout is not compared; validate_live_binding does that before a campaign continues.

    A binding written before 2026-11-03 records its commit as 'pin', and one written from
    2026-10-26 also mounted the retired commit table: its step is rebuilt with that mount."""
    key = 'pin' if isinstance(binding, dict) and 'pin' in binding else 'commit'
    if (not isinstance(binding, dict) or set(binding) != {'spec', 'tool', key, 'adapter_sha256'} or
            not isinstance(binding[key], str) or not attempt.COMMIT.match(binding[key]) or
            not isinstance(binding['spec'], dict) or not isinstance(binding['spec'].get('mounts'), list)):
        raise ValueError('campaign routing binding differs from its declaration or captured policy')
    legacy_table = next((m['source'] for m in binding['spec']['mounts'] if key == 'pin' and isinstance(m, dict)
                         and m.get('at') == attempt.LEGACY_TABLE_AT and isinstance(m.get('source'), str)), None)
    expected = campaign_spec(declaration, manifest, mutable, legacy_table)
    if binding['spec'] != expected or binding['adapter_sha256'] != policy_files['kicad_route_attempt.py']:
        raise ValueError('campaign routing binding differs from its declaration or captured policy')
    return expected


def validate_live_binding(binding):
    """Before a campaign continues: this host's checkout holds the commit the binding recorded,
    and the adapter, checkout and backend state are unchanged since binding. A binding that
    recorded another commit, or the retired commit table, is stale for continuation."""
    from .stage_receipts import observe_tool
    try:
        current = attempt.checkout_commit(checkout_of(binding['spec']))
    except (OSError, UnicodeDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f'cannot read the routing checkout commit: {exc}') from exc
    if 'pin' in binding:
        raise StaleRoutingEvidence(binding['pin'], current, 'campaign routing binding (retired commit table)')
    if not isinstance(binding.get('commit'), str):
        raise ValueError('campaign routing binding records no kicad-route commit')
    if binding['commit'] != current:
        raise StaleRoutingEvidence(binding['commit'], current)
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
    native_spec = (attempt.canonical_backend(request['backend']) == 'builtin' and
                   spec.get('backend_report') == {'artifact': f'routing/attempt/{attempt.NATIVE_OPERATION_FILE}',
                                                  'key': 'binary'})
    is_native = (bool(checkout) and attempt.is_native_request(request, checkout)) if host else native_spec
    expected_engine = None
    if host and is_native:
        engine_bin = attempt.resolve_kicad_route_engine(checkout)
        if engine_bin is not None and engine_bin.is_file():
            real_engine = Path(engine_bin).resolve()
            if not real_engine.is_relative_to(Path(checkout).resolve()):
                expected_engine = str(real_engine)
    if is_native and ((host and expected_engine is not None) or (not host and attempt.SANDBOX['engine'] in mounts)):
        expected_mounts.add(attempt.SANDBOX['engine'])
    if not host and attempt.LEGACY_TABLE_AT in mounts:
        # Retained steps of 2026-10-26 to 2026-11-03 also mounted the retired commit table.
        expected_mounts.add(attempt.LEGACY_TABLE_AT)
    if (set(mounts) != expected_mounts or spec['argv'][:4] != [attempt.PYTHON, '-I', '-B', ADAPTER_AT] or
            (host and mounts[ADAPTER_AT] != str(ADAPTER_SOURCE))):
        raise ValueError('campaign routing step differs from the adapter command or mounts')
    if host:
        if native_spec != is_native:
            raise ValueError('campaign routing step differs from the adapter command or mounts')
        if any(mounts[m['at']] != m['source'] for m in BINDING_MOUNTS):
            raise ValueError('routing binding helper mounts differ from captured policy')
        check = preflight(checkout, state, request['backend'])
        if check['problems']:
            raise ValueError('kicad-route does not qualify: ' + '; '.join(check['problems']))
        if (checkout != check['checkout'] or (state is not None and state != check['state']) or
                (expected_engine is not None and mounts.get(attempt.SANDBOX['engine']) != expected_engine)):
            raise ValueError('routing mount sources must use resolved host paths')
    _receipt_step({'spec': spec}, request)
    if spec['timeout_s'] != request['timeout_s'] + STEP_MARGIN_S:
        raise ValueError('campaign routing step differs from the adapter timeout')
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

    layout overrides the production sandbox layout (the fixture router's tests
    supply theirs). Tool claims are compared with it and with the checkout the
    report recorded, never with this host's checkout: a finished attempt verifies
    on a host whose kicad-route has moved on."""
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
    native_run = (f'attempt/{attempt.NATIVE_OPERATION_FILE}' in retained
                  or 'kicad_route_engine' in (report.get('tool') or {}))
    req_found, op_req = _retained_json(routing, attempt.NATIVE_REQUEST_FILE, retained) if native_run else (False, None)
    op_found, op_result = (_retained_json(routing, attempt.NATIVE_OPERATION_FILE, retained, strict=True)
                           if native_run else (False, None))
    _tool_claims(report, request, expected, ran=process is not None,
                 native_request=attempt.native_exported_request(op_result, op_req))
    _staged(routing, report, request, expected, ran=process is not None)
    if report['tool_version'] != (attempt.TOOL_VERSION if legacy_claim(report) is None else
                                  attempt.LEGACY_TOOL_VERSION):
        raise ValueError('routing tool version is not the recorded unknown')
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
    backend = attempt.native_backend_of(None) if native_run else attempt.backend_of(None)
    if classification == 'not_run':
        if (process is not None or retained or report['discovery'] is not None or
                report['identity_after'] is not None or report['missing_artifacts'] != []):
            raise ValueError('a not-run attempt has router output')
        if native_run and report['backend'] != backend:
            raise ValueError('routing backend identity differs from the retained operation-result.json')
    elif native_run:
        malformed = (op_found and op_result is None) or (req_found and op_req is None)
        expected_missing = []
        if not op_found:
            expected_missing.append(attempt.NATIVE_OPERATION_FILE)
        if ((process['exit_code'] == 0 or (isinstance(op_result, dict) and op_result.get('routing') is not None))
                and not req_found):
            expected_missing.append(attempt.NATIVE_REQUEST_FILE)
        if report['missing_artifacts'] != expected_missing:
            raise ValueError('missing routing artifacts differ from the retained artifacts')
        adapter_killed = (
            classification in ('timeout', 'cancelled')
            and process['timed_out' if classification == 'timeout' else 'cancelled']
        )
        if not adapter_killed:
            if process['timed_out'] or process['cancelled']:
                raise ValueError(f'a {classification} attempt was timed out or cancelled')
            if classification in ('timeout', 'cancelled'):
                native_status = op_result.get('status') if isinstance(op_result, dict) else None
                expected_status = 'operation_timeout' if classification == 'timeout' else 'cancelled'
                if native_status != expected_status:
                    raise ValueError(f'{classification} classification without that observation')
            op_req_path = routing / 'attempt' / attempt.NATIVE_REQUEST_FILE
            derived, _ = attempt.native_semantics(
                process['exit_code'], op_result, op_req_path, None,
                candidate if proposal != 'none' else None)
            if classification != 'contradiction':
                if derived != classification:
                    raise ValueError(f'retained reports classify as {derived}, not {classification}')
                if malformed or report['identity_after'] != report['identity_before']:
                    raise ValueError(f'a {classification} attempt with report or tool-state violations is a contradiction')
        backend = attempt.native_backend_of(op_result)
        if report['backend'] != backend:
            raise ValueError('routing backend identity differs from the retained operation-result.json')
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
    if not native_run:
        backend = attempt.backend_of(result)
        if report['backend'] != backend:
            raise ValueError('routing backend identity differs from the retained result.json')
    if receipt is not None:
        _receipt_agrees(receipt, report, request, routing, backend)
    if native_run and op_result is not None:
        pairs = attempt.pair_coverage(request, op_result, op_req, classification)
    else:
        pairs = attempt.pair_coverage(request, result, record, classification)
    if 'pair_coverage' in sem and sem['pair_coverage'] != pairs:
        # Older native adapters had no per-pair result and reported no coverage.
        historical = (native_run and isinstance(op_result, dict)
                      and (not isinstance(op_result.get('routing'), dict) or 'diff_pairs' not in op_result['routing'])
                      and sem['pair_coverage'] == attempt.pair_coverage(request, None, None, classification))
        if not historical:
            raise ValueError('pair coverage differs from retained routing evidence')
    policy = _policy_grade(routing, report, request, result, classification)
    experiment = _experiment_grade(routing, report, request, result, classification, retained, expected)
    return {'classification': classification, 'proposal': proposal, 'coverage': coverage,
            'pair_coverage': pairs,
            'router_exit': sem['exit_code'], 'candidate_sha256': (report['candidate'] or {}).get('sha256'),
            'backend': backend, 'tool_version': dict(report['tool_version']),
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


def _tool_claims(report, request, layout, *, ran, native_request=None):
    """The tool identity the report claims, against its own checkout record and the layout.

    A report records the commit it ran (tool.commit, named by adapter.compatibility), its
    clean-tree state and code digest. A report written before 2026-11-03 records the
    commit of the retired commit table as tool.pin instead, and ran only at that commit."""
    tool = report['tool']
    executable = str(Path(layout['checkout']) / 'bin' / 'kicad-route')
    legacy = legacy_claim(report)
    if legacy is not None:
        claim_ok = isinstance(legacy, str) and attempt.COMMIT.match(legacy) is not None and \
            report['adapter'] == {'compatibility': f'kicad-route@{legacy}'}
    else:
        claim_ok = all(k in tool for k in attempt.CHECKOUT_KEYS) and \
            report['adapter'] == attempt.adapter_claim(tool['commit'])
    if not claim_ok or tool['kicad_route']['path'] != executable:
        raise ValueError('routing attempt claims a different kicad-route commit or location')
    if not ran:
        return
    # The router starts only after these preconditions held, so an attempt that ran must show them.
    if legacy is not None:
        if tool['commit'] != legacy:
            raise ValueError('routing attempt ran a kicad-route commit other than the one it recorded')
    elif not (isinstance(tool['commit'], str) and attempt.COMMIT.match(tool['commit']) and tool['clean'] is True and
              isinstance(tool['code_sha256'], str) and attempt.SHA256.match(tool['code_sha256']) and
              type(tool['code_files']) is int and tool['code_files'] > 0):
        raise ValueError('routing attempt ran without a recorded clean kicad-route commit and code digest')
    probes = report['state_write_probe']
    if layout['probe'] is not None:
        targets = [str(t) for t in layout['probe']]
    else:
        state, checkout = Path(layout['state']), Path(layout['checkout'])
        components = attempt.state_components(
            request['backend'], attempt.generation_of_components(request['backend'], report['identity_before']['state']))
        targets = [str(state)] + [str(state / c) for c in components
                                  if report['identity_before']['state'].get(c) is not None] + (
            [str(checkout)] if checkout != state else [])
    if (not isinstance(probes, list) or [p.get('directory') for p in probes] != targets or
            any(p.get('refused') is not True or not p.get('errno') for p in probes)):
        raise ValueError('routing attempt ran without an OS-refused write probe of each state directory')
    process = report['process']
    routing = Path(layout['work']) / 'routing'
    if 'kicad_route_engine' in tool:
        eng = tool['kicad_route_engine']
        eng_path = eng.get('path') if isinstance(eng, dict) else None
        checkout_prefix = str(Path(layout['checkout'])) + '/'
        if not isinstance(eng_path, str) or not (
                eng_path == attempt.SANDBOX['engine'] or eng_path.startswith(checkout_prefix)):
            raise ValueError('routing attempt claims an engine outside the verified checkout or engine mount')
        expected_argv = attempt.native_tool_argv(request, {**layout, 'engine': eng_path})
        options = native_request.get('routing_options', {}) if isinstance(native_request, dict) else {}
        protect_mode = options.get('protect_mode') if isinstance(options, dict) else None
        if '--protect' not in process['argv']:
            # Earlier native invocations omitted the option. Only their exact argv
            # and an exported request without a protection override can replay.
            if protect_mode is not None:
                raise ValueError('native copper protection differs between the executed argv and saved request')
            offset = expected_argv.index('--protect')
            del expected_argv[offset:offset + 2]
        elif report['semantics']['classification'] == 'complete' and protect_mode != 'existing':
            raise ValueError('saved native request did not protect existing copper')
    else:
        expected_argv = attempt.tool_argv(request, layout)
    if (process['argv'] != expected_argv or process['env'] != attempt.tool_env(layout) or
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
    expected_backend_reports = (
        {'artifact': 'routing/attempt/result.json', 'key': 'backend'},
        {'artifact': f'routing/attempt/{attempt.NATIVE_OPERATION_FILE}', 'key': 'binary'},
    )
    if (spec['stage'] != 'routing' or spec['step'] != f'kicad-route-{request["backend"]}' or
            spec['writes'] != {target: f'routing/candidate/{target}'} or spec['adoptable_exits'] != [0, 2] or
            spec['backend_report'] not in expected_backend_reports or
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


def _retained_json(routing, name, retained, *, strict=False):
    """(retained, value): value is None when the retained file is not JSON or exceeds the limit."""
    key = f'attempt/{name}'
    if key not in retained:
        return False, None
    if strict:
        try:
            return True, attempt.strict_json_file(routing / key, name)
        except attempt.RequestError:
            return True, None
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
    grid = router_config.get('grid_step_mm')
    if grid is not None and (type(grid) not in (int, float) or not math.isfinite(grid) or grid < .000001):
        raise ValueError('router.grid_step_mm must be finite and at least 0.000001 mm')
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
    # Pair handling is declared, never assumed: kicad-route's --diff-pairs auto treats every
    # net named *_P/*_N (or +/-) as a pair member and refuses an unpaired one.
    diff_pairs = router_config.get('diff_pairs')
    if 'diff_pairs' in router_config and diff_pairs != 'auto':
        raise ValueError("router.diff_pairs must be 'auto' (route declared pairs); omit it to route without pair handling")
    dependent = [key for key in ('pair_layers', 'pair_impedance', 'intra_pair_match', 'ac_match') if key in router_config]
    if dependent and diff_pairs != 'auto':
        raise ValueError(', '.join('router.' + key for key in dependent)
                         + " route differential pairs; declare them with router.diff_pairs 'auto'")
    return {'ac_match': ac_match, 'length_match': length_match, 'bus': bus,
            'pair_layers': pair_layers, 'pair_impedance': impedance, 'intra_pair_match': matching,
            'diff_pairs': diff_pairs}


def router_python(*args, python=None):
    """The command for Python code from a kicad-route checkout: the interpreter (attempt.PYTHON unless
    python is given), attempt.ROUTER_PYTHON_FLAGS, then args. Every router command this module builds
    starts here, so bytecode cached in the checkout never runs whatever environment runs the command."""
    return [python or attempt.PYTHON, *attempt.ROUTER_PYTHON_FLAGS, *args]


def is_router_command(argv):
    """Whether argv runs Python with attempt.ROUTER_PYTHON_FLAGS: a router_python command, or one that
    puts -I before the flags (the pose solver's, from pose_milp or acquisition.placement)."""
    flags = list(attempt.ROUTER_PYTHON_FLAGS)
    head = [str(arg) for arg in argv[1:2 + len(flags)]]
    return head[:len(flags)] == flags or head == ['-I', *flags]


def router_env(env=None):
    """The environment of a router command. The flags keep the interpreter the command starts off
    cached bytecode; the Python processes it starts in turn (the delay helper starts KiCad's
    interpreter on native_delay.py, the board route its workers) inherit the environment but not -X
    options, so the environment names a private prefix too. With env None the result is os.environ with
    PYTHONPYCACHEPREFIX replaced by one that holds no bytecode, since the caller's own prefix may be
    shared; an explicit env keeps the private prefix it names (board_route_env) and gets that one
    otherwise. PYTHONDONTWRITEBYTECODE is always set, so those processes do not try to create cache
    directories under the prefix either. placement_job._run_child applies this to every command
    is_router_command accepts."""
    result = dict(os.environ if env is None else env)
    if env is None or not result.get('PYTHONPYCACHEPREFIX'):
        result['PYTHONPYCACHEPREFIX'] = attempt.NO_PYCACHE
    result['PYTHONDONTWRITEBYTECODE'] = '1'
    return result


def stop_grace_s(budget_s):
    """The longest kicad-route lets its engine run past ``--budget`` before it grades the result.

    At the budget it asks the engine to stop and waits max(30, budget // 5) s, then terminates it
    (10 s) and kills it (5 s): kicad-route ``cli.run_budgeted`` and ``campaign.stop_grace``.
    The passes the router runs after the engine, its closing rounds and its pour repair, take
    their time from the budget and from what the engine left of that wait, and no more
    (kicad-route 25a58a5; before it a pour repair could add ten minutes)."""
    return max(30, int(budget_s) // 5) + 15


def search_budget_s(share_s, checks_s):
    """The largest whole ``--budget`` whose search, stop grace and ``checks_s`` all fit in ``share_s``.

    ``checks_s`` is the time kept for what follows the search: the router's own grading and
    the caller's checks of the result. A value below 1 means ``share_s`` has no room for a search."""
    room = share_s - checks_s
    budget = int((room - 15) * 5 / 6)
    if budget < 150:                        # up to 150 s the grace is its 30 s minimum
        return min(int(room - 45), 149)
    while budget + 1 + stop_grace_s(budget + 1) <= room:
        budget += 1
    return budget


def first_search_budget_s(share_s, checks_s):
    """Reserve checks when possible without giving a larger first share less search.

    A short first trial can finish before the conservative stop/check allowance.
    Keep its original search up to that allowance, then hold that search floor until
    the fully reserved budget catches up. The outer job deadline still applies.
    """
    floor = checks_s + stop_grace_s(1)
    return max(search_budget_s(share_s, checks_s), min(int(share_s), floor))


def _native_board_route_argv(checkout, staged_dir, out_dir, *, budget_s, owned_copper=False,
                             grid_step_mm=None, pair_layers=None, pair_impedance=None,
                             intra_pair_match=None, bus=None, length_match=None,
                             ac_match=None, diff_pairs=None, board_name='board.kicad_pcb',
                             output_board=None, operation_result=None, save_request=None,
                             project_file=None):
    diff_pair_args = []
    if diff_pairs is not None:
        if diff_pairs == 'auto':
            diff_pair_args = ['--diff-pairs', 'auto']
        elif isinstance(diff_pairs, (list, tuple)) and diff_pairs:
            seen_pairs = set()
            seen_nets = set()
            for pair in diff_pairs:
                if isinstance(pair, str):
                    if attempt._re01_has_nul_or_surrogate(pair):
                        raise ValueError("diff_pairs must not contain NUL or surrogate characters")
                    parts = pair.split(',')
                    if len(parts) != 2:
                        raise ValueError("diff_pairs must be 'auto' or non-empty '<POS>,<NEG>' specs")
                    pos, neg = parts
                elif isinstance(pair, (list, tuple)) and len(pair) == 2:
                    pos, neg = pair
                    if not isinstance(pos, str) or not isinstance(neg, str) or ',' in pos or ',' in neg:
                        raise ValueError("diff_pairs must be 'auto' or non-empty '<POS>,<NEG>' specs")
                    if attempt._re01_has_nul_or_surrogate(pos) or attempt._re01_has_nul_or_surrogate(neg):
                        raise ValueError("diff_pairs must not contain NUL or surrogate characters")
                else:
                    raise ValueError("diff_pairs must be 'auto' or non-empty '<POS>,<NEG>' specs")
                if (not pos or not neg or pos != pos.strip() or neg != neg.strip()
                        or pos.startswith('-') or neg.startswith('-') or pos == neg):
                    raise ValueError("diff_pairs must be 'auto' or non-empty '<POS>,<NEG>' specs")
                spec = f'{pos},{neg}'
                if spec in seen_pairs or pos in seen_nets or neg in seen_nets:
                    raise ValueError(f"diff_pairs contains duplicate or overlapping net in pair: {spec}")
                seen_pairs.add(spec)
                seen_nets.update((pos, neg))
                diff_pair_args += ['--diff-pairs', spec]
        else:
            raise ValueError("diff_pairs must be 'auto' or None")
    engine_bin = attempt.resolve_kicad_route_engine(checkout)
    if engine_bin is None or not engine_bin.is_file() or not os.access(engine_bin, os.X_OK):
        raise ValueError('kicad-route-engine binary missing for Rust-capable checkout')
    bridge = attempt.resolve_native_bridge(checkout)
    if bridge is None:
        raise ValueError('tools/rust_engine_kicad.py missing for Rust-capable checkout')
    staged_path = Path(staged_dir)
    out_base = Path(out_dir)
    source_board = staged_path / board_name
    out_board = Path(output_board) if output_board is not None else (out_base / 'native' / board_name)
    op_result = Path(operation_result) if operation_result is not None else (out_base / attempt.NATIVE_OPERATION_FILE)
    op_req = Path(save_request) if save_request is not None else (out_base / attempt.NATIVE_REQUEST_FILE)
    argv = [
        str(engine_bin), 'route-native', str(source_board), str(out_board),
        '--operation-result', str(op_result),
        '--save-request', str(op_req),
        '--native-bridge', str(bridge),
        '--python', attempt.PYTHON,
        '--timeout-ms', str(int(budget_s) * 1000),
        '--protect', 'locked' if owned_copper else 'existing',
        *diff_pair_args,
    ]
    proj = Path(project_file) if project_file is not None else (staged_path / (Path(board_name).stem + '.kicad_pro'))
    if proj.is_file() and not proj.is_symlink():
        argv += ['--project', str(proj)]
    return argv + _board_option_args(grid_step_mm, pair_layers, pair_impedance, intra_pair_match,
                                   bus, length_match, ac_match)


def board_route_argv(checkout, staged_dir, out_dir, *, design_name='board', owned_copper=False, budget_s,
                     grid_step_mm=None, pair_layers=None, pair_impedance=None, intra_pair_match=None,
                     bus=None, length_match=None, ac_match=None, diff_pairs=None):
    """Build the kicad-route CLI command for board-only / schematic-optional invocations.

    diff_pairs is 'auto' only for declared pairs; otherwise the router has no pair handling."""
    if attempt.is_rust_capable_checkout(checkout):
        return _native_board_route_argv(
            checkout, staged_dir, out_dir, budget_s=budget_s, owned_copper=owned_copper,
            grid_step_mm=grid_step_mm, pair_layers=pair_layers, pair_impedance=pair_impedance,
            intra_pair_match=intra_pair_match, bus=bus, length_match=length_match,
            ac_match=ac_match, diff_pairs=diff_pairs)
    argv = router_python(str(Path(checkout) / 'bin' / 'kicad-route'), 'run', '--out', str(out_dir),
                         '--json', '--backend', attempt.CLI_BACKEND_NAMES['builtin'], '--dir', str(staged_dir),
                         '--board', 'board.kicad_pcb', '--schematic', 'board.kicad_sch', '--name', design_name,
                         '--label', 'placement-job', '--protect', 'locked' if owned_copper else 'existing',
                         '--budget', str(int(budget_s)))
    if diff_pairs is not None:
        if diff_pairs != 'auto':
            raise ValueError("diff_pairs must be 'auto' or None")
        argv += ['--diff-pairs', 'auto']
    return argv + _board_option_args(grid_step_mm, pair_layers, pair_impedance, intra_pair_match,
                                   bus, length_match, ac_match)


def _board_option_args(grid_step_mm, pair_layers, pair_impedance, intra_pair_match,
                      bus, length_match, ac_match):
    """Encode the board constraints accepted by both router CLIs."""
    argv = []
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


ROUTER_RECIPE = 'auto'


def router_recipe(checkout):
    """The search-cost recipe a job may try on its router, or None when that router has none.

    route-native does not invoke legacy bin/kicad-route CLI recipes (RE-01)."""
    if attempt.is_rust_capable_checkout(checkout):
        return None
    try:
        declared = '"--recipe"' in (Path(checkout) / 'src/kicad_route/cli.py').read_text(errors='replace')
    except OSError:
        return None
    return ROUTER_RECIPE if declared else None


def copper_layer_count(board_path):
    """Copper layers a board file declares in its layer table, or None when it cannot be read."""
    try:
        text = Path(board_path).read_text(errors='replace')
    except OSError:
        return None
    table = re.search(r'\(layers\b(.*?)\n\t\)', text, re.S)
    return len(re.findall(r'\(\d+ "[^"]+\.Cu" ', table.group(1))) if table else None


def board_recipe_argv(argv, out_dir, budget_s, recipe):
    """The command of a board route again from the same input, at a kicad-route cost recipe."""
    if 'route-native' in [str(a) for a in argv] or attempt.is_re01_enforced():
        raise attempt.RE01OwnershipError('RE-01 forbids legacy board_recipe_argv on route-native invocations')
    again = list(argv)
    again[again.index('--out') + 1] = str(out_dir)
    again[again.index('--budget') + 1] = str(int(budget_s))
    return again + ['--recipe', recipe]


def recorded_recipe(record):
    """The cost recipe a bound attempt's run recorded (settings.recipe of its result), or None.

    The router records one only when it is not the engine's own costs: asked for, or chosen by
    the router itself for the board (kicad-route's default `auto` on a two-layer board)."""
    try:
        recipe = json.loads(Path(record['result_path']).read_bytes())['settings'].get('recipe')
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return None
    return recipe if isinstance(recipe, str) else None


def new_via_count(record):
    """Vias the router added in a bound attempt (routing.stats.new_vias of its result), or None."""
    try:
        return int(json.loads(Path(record['result_path']).read_bytes())['routing']['stats']['new_vias'])
    except (KeyError, OSError, TypeError, ValueError):
        return None


def board_route_env(checkout, home):
    """Build the isolated subprocess environment for a board-level kicad-route run.

    PYTHONPYCACHEPREFIX names a private, empty directory under home, as the sandbox attempt's
    does: -B only stops writing caches, so without it Python would read bytecode cached in the
    checkout, which the recorded code digest (kicad_route_attempt.code_files) excludes."""
    Path(home).mkdir(parents=True, exist_ok=True)
    return {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'TMPDIR': str(home), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
            'PYTHONPYCACHEPREFIX': str(Path(home) / 'pycache'),
            'KICAD_ROUTE_STATE': str(checkout), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_OPTIONAL_LOCKS': '0'}


def board_retry_argv(argv, retry_dir, budget_s, feedback, pads, board_path):
    """Build the bounded repair retry CLI command for a board-level kicad-route run."""
    if 'route-native' in [str(a) for a in argv] or attempt.is_re01_enforced():
        raise attempt.RE01OwnershipError('RE-01 forbids legacy board_retry_argv on route-native invocations')
    retry = list(argv)
    retry[retry.index('--out') + 1] = str(Path(retry_dir) / 'out')
    retry[retry.index('--budget') + 1] = str(int(budget_s))
    if feedback['kind'] == 'thermal_corridors':
        pads = feedback['pads']
    else:
        retry[retry.index('--dir') + 1] = str(Path(board_path).parent)
        if '--diff-pairs' in retry:
            del retry[retry.index('--diff-pairs'):retry.index('--diff-pairs') + 2]
        retry += ['--nets', *feedback['nets'], '--backend-arg=--force-reroute']
    for pad in pads:
        retry += ['--reserve-spoke-pad', pad]
    return retry, pads


def spokes_argv(checkout, run_dir, *, route_native=None):
    """Build the kicad-route spokes diagnostic command."""
    if route_native is True or attempt.is_re01_enforced() or (
            route_native is None and attempt.is_rust_capable_checkout(checkout)):
        raise attempt.RE01OwnershipError('RE-01 forbids executing legacy bin/kicad-route spokes on route-native')
    return router_python(str(Path(checkout) / 'bin/kicad-route'), 'spokes', str(run_dir))


REFUSAL_SCHEMA = 'kicad-route/refusal/1'
REFUSAL_CLASSES = ('usage', 'input', 'ledger', 'canary', 'state')


def board_refusal(route_dir, argv, exit_code):
    """The router's own refusal record for this invocation, or None.

    With --json, kicad-route refuses before any backend runs by exiting 4 with
    exactly one kicad-route/refusal/1 object on stdout (its docs/interface.md).
    The record must name this command line: the router records its own arguments, everything
    after the script in argv. Its message is kept verbatim."""
    script = next((i for i, arg in enumerate(argv) if str(arg).endswith('/bin/kicad-route')), None)
    if exit_code != 4 or script is None:
        return None
    try:
        value = json.loads((Path(route_dir) / 'kicad-route.stdout').read_bytes())
    except (OSError, ValueError):
        return None
    if (not isinstance(value, dict) or value.get('schema') != REFUSAL_SCHEMA or value.get('kind') != 'refusal'
            or value.get('exit_code') != 4 or value.get('argv') != [str(arg) for arg in argv[script + 1:]]
            or value.get('reason_class') not in REFUSAL_CLASSES
            or not isinstance(value.get('message'), str) or not value['message']):
        return None
    return {'reason_class': value['reason_class'], 'message': value['message'], 'record': value.get('record')}


VIA_IN_PAD_LINE = re.compile(r'^VIA_IN_PAD_JSON:\s*(.+)$', re.MULTILINE)


def parse_via_in_pad_record(text_or_dict):
    """Extract structured via-in-pad data from a router log, stdout, or summary.json dict."""
    if isinstance(text_or_dict, dict):
        raw = text_or_dict.get('via_in_pad')
        if isinstance(raw, dict):
            return raw
    if isinstance(text_or_dict, (str, bytes)):
        text = text_or_dict.decode('utf-8', errors='replace') if isinstance(text_or_dict, bytes) else text_or_dict
        match = VIA_IN_PAD_LINE.search(text)
        if match:
            try:
                return json.loads(match.group(1))
            except ValueError:
                pass
    return {'count': 0, 'sites': [], 'stamped': 0, 'restored': 0,
            'protected': 0, 'unstampable': 0, 'site_created': 0,
            'unprotected': [], 'note': ''}


def _bind_native_board_attempt(route_dir, argv, exit_code):
    argv_list = [str(a) for a in argv]
    record = {'argv': argv_list, 'exit_code': exit_code}
    idx = argv_list.index('route-native')
    source_board = Path(argv_list[idx + 1])
    output_board = Path(argv_list[idx + 2])
    op_result_path = Path(argv_list[argv_list.index('--operation-result') + 1])
    op_request_path = (Path(argv_list[argv_list.index('--save-request') + 1])
                       if '--save-request' in argv_list else None)
    if op_result_path.is_symlink() or not op_result_path.is_file():
        return {**record, 'status': 'missing_output', 'failure_kind': 'environment',
                'failing_stage': 'routing',
                'reason': f'route-native exit {exit_code}: missing {op_result_path.name}'}
    try:
        op_result = attempt.strict_json_file(op_result_path, op_result_path.name)
    except (OSError, ValueError, RecursionError) as exc:
        return {**record, 'status': 'refused_result', 'failure_kind': 'environment',
                'failing_stage': 'routing',
                'reason': f'unreadable {op_result_path.name}: {exc}'}
    outcome_class, reasons = attempt.native_semantics(
        exit_code, op_result, op_request_path, source_board, output_board)
    record.update(
        result_path=str(op_result_path),
        result_sha256=digest(op_result_path),
        native_operation_schema=attempt.NATIVE_OPERATION_SCHEMA,
        router_exit=op_result.get('exit_code') if isinstance(op_result, dict) else None,
        router_status=op_result.get('status') if isinstance(op_result, dict) else None,
        classification={'outcome': outcome_class, 'reasons': reasons},
    )
    if outcome_class == 'complete':
        sha = digest(output_board)
        return {**record, 'status': 'routed', 'board': str(output_board), 'board_sha256': sha,
                'router_accepted': True, 'via_in_pad': parse_via_in_pad_record({})}
    if outcome_class == 'refused':
        out = {**record, 'status': 'router_refused',
               'refusal': {'reason_class': op_result.get('status'), 'message': '; '.join(reasons)},
               'reason': '; '.join(reasons)}
        if exit_code == 8:
            out.update(failure_kind='environment', failing_stage='routing')
        return out
    if (outcome_class == 'backend_failure' and exit_code in (3, 4, 5)) or (
            outcome_class == 'timeout' and exit_code == 6):
        return {**record, 'status': 'no_candidate', 'reason': '; '.join(reasons)}
    return {**record, 'status': 'backend_failure', 'failure_kind': 'environment',
            'failing_stage': 'routing', 'reason': '; '.join(reasons)}


def bind_board_attempt(route_dir, argv, exit_code):
    """Bind one board-level kicad-route attempt output directory and verify its candidate digest."""
    if 'route-native' in [str(a) for a in argv]:
        return _bind_native_board_attempt(route_dir, argv, exit_code)
    route_dir = Path(route_dir)
    record = {'argv': argv, 'exit_code': exit_code}
    results = sorted((route_dir / 'out').rglob('result.json'))
    if len(results) != 1:
        refusal = board_refusal(route_dir, argv, exit_code) if not results else None
        if refusal is not None:
            record.update(status='router_refused', refusal=refusal)
            if refusal['reason_class'] in ('state', 'canary', 'usage'):
                stage = 'routing' if refusal['reason_class'] == 'usage' else 'routing-preflight'
                record.update(failure_kind='environment', failing_stage=stage, reason=refusal['message'])
            return record
        if exit_code == 4 and not results:
            try:
                json.loads((route_dir / 'kicad-route.stdout').read_bytes())
            except (OSError, ValueError):
                pass  # Unreadable refusal evidence is a tool failure below.
            else:
                # A readable record that cannot bind keeps the existing route-failure path.
                return {**record, 'status': 'missing_output'}
        return {**record, 'status': 'missing_output', 'failure_kind': 'environment',
                'failing_stage': 'routing', 'reason': f'router exit {exit_code}: expected one result.json, found {len(results)}'}
    data = results[0].read_bytes()
    # The same result rule as the sandboxed route: schema, keys and statuses first.
    outcome, reasons = attempt.classify(data, exit_code, paired='--diff-pairs' in argv)
    record.update(result_path=str(results[0]), result_sha256=digest(results[0]),
                  classification={'outcome': outcome, 'reasons': reasons})
    if outcome == 'refused':
        return {**record, 'status': 'refused_result', 'failure_kind': 'environment',
                'failing_stage': 'routing', 'reason': '; '.join(reasons)}
    result = json.loads(data)
    record.update(router_exit=result.get('exit_code'), router_status=result.get('status') or result.get('outcome'))
    # A board-dependent search stop also reports process FAIL without a board.
    if result['process']['status'] == 'FAIL':
        commands = result['process'].get('commands')
        if any(command.get('killed') is True and
               (command.get('budget_reached') is True or command.get('stalled') is True
                or command.get('stop_requested') is True)
               for command in (commands if isinstance(commands, list) else [])
               if isinstance(command, dict)):
            return {**record, 'status': 'no_candidate'}
        return {**record, 'status': 'backend_failure', 'failure_kind': 'environment',
                'failing_stage': 'routing', 'reason': '; '.join(reasons)}
    candidate = result.get('candidate')
    if not candidate:
        return {**record, 'status': 'no_candidate'}
    board = route_dir / candidate['board']
    sha = digest(board)
    if sha != result.get('validation', {}).get('candidate_board_sha256'):
        return {**record, 'status': 'stale_candidate', 'failure_kind': 'environment',
                'failing_stage': 'routing', 'reason': 'router candidate digest differs from validation'}
    vip_record = None
    summaries = sorted((route_dir / 'out').rglob('summary.json'))
    if summaries and summaries[0].is_file():
        try:
            vip_record = parse_via_in_pad_record(json.loads(summaries[0].read_bytes()))
        except (OSError, ValueError):
            pass
    if vip_record is None or vip_record.get('count') == 0:
        for log_name in ('work/krt.log', 'krt.log', 'kicad-route.stdout'):
            candidates = sorted(route_dir.rglob(Path(log_name).name))
            if candidates and candidates[0].is_file():
                try:
                    parsed = parse_via_in_pad_record(candidates[0].read_text(encoding='utf-8', errors='replace'))
                    if parsed.get('count', 0) > 0 or vip_record is None:
                        vip_record = parsed
                        if parsed.get('count', 0) > 0:
                            break
                except (OSError, ValueError):
                    pass
    if vip_record is None:
        vip_record = parse_via_in_pad_record({})
    return {**record, 'status': 'routed', 'board': str(board), 'board_sha256': sha,
            'router_accepted': outcome == 'accepted',
            'via_in_pad': vip_record,
            **({'impedance_plan': result['settings']['pair_impedance']}
               if 'pair_impedance' in result.get('settings', {}) else {})}


def _require_legacy_helper_allowed(checkout, what, route_native):
    if route_native is True or attempt.is_re01_enforced() or (
            route_native is None and attempt.is_rust_capable_checkout(checkout)):
        raise attempt.RE01OwnershipError(f'RE-01 forbids executing legacy Python {what} in route-native flows')


def preflight_stackup_argv(checkout, board_path, layer, *, python=None, route_native=None):
    """Build the kicad-route pair-impedance stackup preflight command."""
    import sys
    _require_legacy_helper_allowed(checkout, 'src/kicad_route/pair_impedance.py', route_native)
    return router_python('-c',
                         'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); '
                         'from kicad_route.pair_impedance import model; model(Path(sys.argv[2]), sys.argv[3])',
                         str(Path(checkout) / 'src'), str(board_path), layer, python=python or sys.executable)


def preflight_delay_argv(checkout, board_path, nets, *, python=None, route_native=None):
    """Build the kicad-route native delay model preflight command."""
    import sys
    _require_legacy_helper_allowed(checkout, 'src/kicad_route/router/py_router/native_delay.py', route_native)
    return router_python('-c',
                         'import json,sys; sys.path.insert(0, sys.argv[1]); '
                         'from native_delay import profiles; '
                         'profiles(sys.argv[2], json.loads(sys.argv[3]), required=True)',
                         str(Path(checkout) / 'src/kicad_route/router/py_router'),
                         str(board_path), json.dumps(sorted(nets)), python=python or sys.executable)


def post_route_checker_argv(checkout, kind, board_path, request_path, result_path, *, python=None, route_native=None):
    """Build the kicad-route post-route checker command (pair_impedance, length_groups, ac_matching)."""
    import sys
    scripts = {'pair_impedance': 'pair_impedance.py', 'length_groups': 'length_groups.py',
               'ac_matching': 'ac_matching.py'}
    _require_legacy_helper_allowed(checkout, f'src/kicad_route/{scripts[kind]}', route_native)
    checker = Path(checkout) / 'src/kicad_route' / scripts[kind]
    argv = router_python(str(checker), str(board_path), str(request_path), str(result_path),
                         python=python or sys.executable)
    if kind == 'pair_impedance':
        argv.append('--check')
    return argv

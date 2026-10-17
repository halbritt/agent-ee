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
from pathlib import Path

from . import kicad_route_attempt as attempt
from .storage import digest

ADAPTER_AT = '/opt/agent-ee-routing/kicad_route_attempt.py'
ADAPTER_SOURCE = Path(attempt.__file__).resolve()
# Hashing the adapter, code and state mounts, and retaining diagnostics, happen
# after the router's own timeout inside the same step budget.
STEP_MARGIN_S = 300
ARTIFACTS = ('routing/adapter-report.json', 'routing/kicad-route.stdout', 'routing/kicad-route.stderr',
             'routing/attempt/result.json', 'routing/attempt/attempt.json', 'routing/attempt/RESULT.md',
             'routing/attempt/snapshot.json', 'routing/attempt/validate', 'routing/attempt/facts',
             'routing/attempt/work')


def preflight(checkout, state=None, backend='krt'):
    """Host-side identity of the external tool, without running git or the tool.

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
    problems += attempt.verify_code(checkout)
    for rel in attempt.STATE_REQUIRED[backend]:
        if not os.path.lexists(state / rel):
            problems.append(f'missing installed backend dependency: KICAD_ROUTE_STATE/{rel}')
    return {'checkout': str(checkout), 'state': str(state), 'commit': commit, 'pin': attempt.PIN,
            'executable_sha256': digest(checkout / 'bin/kicad-route') if (checkout / 'bin/kicad-route').is_file() else None,
            'adapter_sha256': digest(ADAPTER_SOURCE), 'problems': problems}


def step(*, project, board, schematic, name, label, checkout, state=None, budget=300, timeout=None,
         backend='krt'):
    """The AGENTEE-95 step declaration for one attempt. Raises ValueError if the tool does not qualify."""
    check = preflight(checkout, state, backend)
    if check['problems']:
        raise ValueError('kicad-route does not qualify: ' + '; '.join(check['problems']))
    timeout = timeout if timeout is not None else 2 * budget + 300
    separate = check['state'] != check['checkout']
    args = ['--project', project, '--board', board, '--schematic', schematic, '--name', name,
            '--label', label, '--budget', str(budget), '--timeout', str(timeout), '--backend', backend,
            '--state', 'separate' if separate else 'checkout']
    attempt.parse_request(args)  # the same validation the sandbox applies
    mounts = [{'source': str(ADAPTER_SOURCE), 'at': ADAPTER_AT},
              {'source': check['checkout'], 'at': attempt.SANDBOX['checkout']}]
    if separate:
        mounts.append({'source': check['state'], 'at': attempt.SANDBOX['state']})
    target = f'{project}/{board}'
    return {'schema': 'agent-ee.stage-step/1', 'stage': 'routing', 'step': f'kicad-route-{backend}',
            'argv': [attempt.PYTHON, '-I', '-B', ADAPTER_AT, *args],
            'mounts': mounts, 'source': attempt.SANDBOX['checkout'] + '/src',
            'backend_report': {'artifact': 'routing/attempt/result.json', 'key': 'backend'},
            'writes': {target: f'routing/candidate/{target}'}, 'artifacts': list(ARTIFACTS),
            'adoptable_exits': [0, 2], 'timeout_s': timeout + STEP_MARGIN_S}


def _report(run):
    path = Path(run) / 'routing/adapter-report.json'
    if path.is_symlink() or not path.is_file():
        raise ValueError('routing adapter report missing')
    report = json.loads(path.read_text())
    if not isinstance(report, dict) or report.get('schema') != attempt.REPORT_SCHEMA:
        raise ValueError(f'routing adapter report must be {attempt.REPORT_SCHEMA}')
    body = {k: v for k, v in report.items() if k != 'identity'}
    if report.get('identity') != attempt.canonical_identity(body):
        raise ValueError('routing adapter report identity mismatch')
    return report


def verify(run, inputs=None):
    """Re-derive one attempt's outcome from its retained bytes; raise on any disagreement.

    run is the attempt's /work directory. inputs, when given, is the pre-step
    candidate file map (relative path -> sha256) the coordinator captured."""
    run = Path(run)
    routing = run / 'routing'
    report = _report(run)
    request = attempt.parse_request(_request_argv(report['request']))
    if request != report['request']:
        raise ValueError('routing adapter request is not a valid adapter request')
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
    candidate = routing / 'candidate' / request['project'] / request['board']
    if proposal == 'none':
        if os.path.lexists(routing / 'candidate') or report['candidate'] is not None:
            raise ValueError(f'a {classification} attempt delivered a routed board')
    else:
        if (candidate.is_symlink() or not candidate.is_file() or report['candidate'] is None or
                digest(candidate) != report['candidate']['sha256'] or
                attempt.files_of(attempt.tree(routing / 'candidate')) !=
                {f'{request["project"]}/{request["board"]}': report['candidate']['sha256']}):
            raise ValueError('routed candidate differs from the adapter report')
    process = report['process']
    if classification == 'not_run':
        if process is not None or retained.keys() - {'kicad-route.stdout', 'kicad-route.stderr'}:
            raise ValueError('a not-run attempt has router output')
    elif classification in ('timeout', 'cancelled'):
        if not process or not process['timed_out' if classification == 'timeout' else 'cancelled']:
            raise ValueError(f'{classification} classification without that observation')
    else:
        result = _retained_json(routing, 'result.json', retained)
        record = _retained_json(routing, 'attempt.json', retained)
        stdout = (routing / 'kicad-route.stdout').read_bytes().decode('utf-8', 'replace')
        refused = any(line.startswith('[route] refused: ') for line in stdout.splitlines())
        derived, _ = attempt.semantics(process['exit_code'], result, record, refused,
                                       report['discovery']['attempt_dir'] is not None, request)
        if derived != classification and not (classification == 'contradiction' and derived in (
                'complete', 'incomplete', 'backend_failure', 'validation_failed')):
            raise ValueError(f'retained reports classify as {derived}, not {classification}')
        if proposal != 'none':
            if result['validation'].get('delivered_board_sha256') != report['candidate']['sha256']:
                raise ValueError('routed candidate is not the board the router validated')
            prefix = request['project'] + '/'
            snapshot = _retained_json(routing, 'snapshot.json', retained)
            files = snapshot.get('files') if isinstance(snapshot, dict) else None
            if not isinstance(files, dict) or any(report['inputs']['files'].get(prefix + rel) != sha
                                                  for rel, sha in files.items()):
                raise ValueError('routing reference snapshot differs from the attempt inputs')
    return {'classification': classification, 'proposal': proposal, 'coverage': coverage,
            'router_exit': sem['exit_code'], 'candidate_sha256': (report['candidate'] or {}).get('sha256'),
            'backend': report['backend'], 'tool_version': report['tool_version'],
            'reproducibility': report['reproducibility']}


def _retained_json(routing, name, retained):
    key = f'attempt/{name}'
    if key not in retained:
        return None
    try:
        return json.loads((routing / key).read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _request_argv(request):
    if not isinstance(request, dict):
        raise ValueError('routing adapter request must be an object')
    flags = {'project': '--project', 'board': '--board', 'schematic': '--schematic', 'name': '--name',
             'label': '--label', 'budget_s': '--budget', 'timeout_s': '--timeout', 'backend': '--backend',
             'state': '--state'}
    if set(request) != set(flags):
        raise ValueError('routing adapter request has unknown or missing fields')
    argv = []
    for key, flag in flags.items():
        argv += [flag, str(request[key])]
    return argv


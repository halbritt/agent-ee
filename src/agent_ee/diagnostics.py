"""Advisory retained-evidence diagnosis; never recover or write historical data."""

import fcntl
import subprocess
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .storage import read_json


@contextmanager
def _snapshot_lease(root):
    paths = {root}
    paths.update(p for p in root.parents if (p / '.campaign-root').is_file())
    with ExitStack() as stack:
        missing = []
        for path in sorted(paths):
            lock = path.parent / f'.{path.name}.writer.lock'
            try:
                stream = stack.enter_context(lock.open('rb'))
            except FileNotFoundError:
                missing.append(str(lock))
                continue
            except OSError as exc:
                yield {'status': 'unverified', 'reason': f'{type(exc).__name__}: {exc}'}
                return
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                yield {'status': 'busy', 'reason': f'writer lease held: {lock}'}
                return
        yield ({'status': 'unverified', 'reason': 'existing lease unavailable', 'missing': missing}
               if missing else {'status': 'coherent', 'reason': 'existing shared flock leases held'})


def _record(path, unavailable):
    try:
        value = read_json(path)
        if not isinstance(value, dict):
            raise ValueError('record must be an object')
        return value
    except (OSError, ValueError) as exc:
        unavailable.append({'path': str(path), 'error': f'{type(exc).__name__}: {exc}'})
        return {}


def _diagnose(root, campaign, coherence, current_source):
    from .cli import verify
    from .campaign import verify_campaign_records

    report = {'schema': 'agent-ee.diagnosis/1', 'path': str(root),
              'kind': 'campaign' if campaign else 'run', 'coherence': coherence,
              'identities': {}, 'verified_facts': [], 'failed_invariants': [],
              'unavailable_facts': [], 'timeline': [], 'ambiguous_transitions': [],
              'operator_actions': []}
    unavailable = report['unavailable_facts']
    record = _record(root / ('state.json' if campaign else 'result.json'), unavailable)
    manifest = {} if campaign else _record(root / 'candidate.json', unavailable)
    report['identities'] = {key: record.get(key, manifest.get(key))
                            for key in ('run_id', 'candidate_id', 'baseline_id')}
    report['identities']['campaign_path'] = str(root) if campaign else next(
        (str(p) for p in root.parents if (p / '.campaign-root').is_file()), None)
    baseline = record.get('baseline') if campaign else (
        _record(root / 'baseline.json', unavailable) if (root / 'baseline.json').exists() else None)
    report['identities']['baseline_id'] = baseline.get('baseline_id') if isinstance(baseline, dict) else None
    report['recorded_state'] = record.get('state')
    try:
        verified = verify_campaign_records(root) if campaign else verify(root, current_source=current_source)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, subprocess.SubprocessError) as exc:
        error = {'type': type(exc).__name__, 'message': str(exc)}
        report['verification'] = {'status': 'failed' if record else 'unavailable', 'error': error}
        report['failed_invariants'].append(error)
    else:
        report['verification'] = {'status': 'passed', 'recorded_state': verified.get('state')}
        if coherence['status'] == 'coherent':
            report['verified_facts'].append('ordinary integrity verification passed under existing leases')
    report['verification']['coherent'] = coherence['status'] == 'coherent'
    groups = [('capture', ['candidate.json', 'baseline.json']),
              ('checks', ['checks/*.json', 'work/*/execution.json']),
              ('review', ['reviews/*.json', 'review-executions/*.json']),
              ('adjudication', ['adjudicator-execution.json', 'events/*adjudic*.json']),
              ('empirical', ['events/*bench*.json', 'events/*feedback*.json']),
              ('campaign', ['state.json', 'history/*.json'])]
    for boundary, patterns in groups:
        paths = sorted({p for pattern in patterns for p in root.glob(pattern) if p.is_file()})
        if not paths:
            unavailable.append({'boundary': boundary, 'path': str(root),
                                'error': 'no retained records observed; execution is unknown'})
        for path in paths:
            value = _record(path, unavailable)
            report['timeline'].append({'boundary': boundary, 'path': str(path.relative_to(root)),
                'fact': 'retained record observed; ordering and completion are not inferred',
                'recorded': {k: value[k] for k in ('candidate_id', 'run_id', 'execution', 'state', 'at', 'started_utc', 'finished_utc') if k in value}})
    temps = sorted(str(p.relative_to(root)) for p in root.rglob('*.tmp'))
    if temps or record.get('state') in ('running', 'incomplete', 'interrupted') or not record or any(
            event['boundary'] == 'empirical' for event in report['timeline']):
        report['ambiguous_transitions'].append({'artifacts': temps,
            'candidates': ['active writer', 'interrupted transition', 'uncommitted or unrelated temporary data'],
            'reason': 'retained artifacts do not establish a unique phase or safe retry'})
    if campaign:
        report['runs'] = [diagnose(p, current_source=False) for p in sorted((root / 'runs').glob('*')) if p.is_dir()]
    if coherence['status'] == 'busy':
        report['operator_actions'].append({'action': 'diagnose again after the writer exits',
                                          'preconditions': ['writer has released its lease']})
    else:
        report['operator_actions'].append({'action': 'inspect retained records and verifier error before choosing recovery',
            'preconditions': ['preserve original evidence', 'separate authorization for any mutation']})
    return report


def diagnose(path, *, current_source=True):
    root = Path(path).resolve()
    campaign = (root / '.campaign-root').is_file() or (root / 'state.json').exists()
    with _snapshot_lease(root) as coherence:
        if campaign and not (root / '.campaign-root').is_file() and coherence['status'] == 'coherent':
            coherence = {'status': 'unverified', 'reason': 'campaign ancestor lease marker missing'}
        return _diagnose(root, campaign, coherence, current_source)


def render_diagnosis(report):
    lines = [f"{report['kind']} diagnosis: {report['path']}",
             f"Snapshot: {report['coherence']['status']}; verification: {report['verification']['status']}",
             f"Recorded state: {report['recorded_state']}"]
    for name, value in report['identities'].items():
        lines.append(f"{name}: {value if value is not None else 'unavailable'}")
    for event in report['timeline']:
        lines.append(f"{event['boundary']}: {event['path']} — {event['fact']}")
    for error in report['failed_invariants']:
        lines.append(f"{error['type']}: {error['message']}")
    for unknown in report['unavailable_facts']:
        lines.append(f"Unavailable {unknown['path']}: {unknown['error']}")
    for transition in report['ambiguous_transitions']:
        lines.append('Ambiguous: ' + transition['reason'])
    for action in report['operator_actions']:
        lines.append(action['action'] + '; requires: ' + '; '.join(action['preconditions']))
    for run in report.get('runs', []):
        lines.append(render_diagnosis(run))
    return '\n'.join(lines)

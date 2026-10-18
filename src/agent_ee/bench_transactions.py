"""Opt-in, bounded bench publication. Recovery never invokes a provider."""

import copy
import os
import re
import shutil
import sys
from pathlib import Path

from .locking import writer_lock
from .storage import digest, identity, json_digest, read_json, write_json

SCHEMA = 'agent-ee.bench-transaction/1'
PHASES = ('prepared', 'supersession_committed', 'review_started', 'completed')


class TransactionError(ValueError):
    def __init__(self, disposition, reason, *, out=None, actions=()):
        self.diagnostic = {'schema': 'agent-ee.bench-recovery/1', 'disposition': disposition,
                           'reason': reason, 'transaction': str(out) if out else None,
                           'operator_actions': list(actions)}
        super().__init__(reason)


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sync_tree(root):
    root = Path(root)
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise TransactionError('conflict', 'transaction contains a live link', out=root)
        if path.is_file():
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
    for path in sorted((p for p in root.rglob('*') if p.is_dir()), reverse=True):
        fsync_directory(path)
    fsync_directory(root)
    fsync_directory(root.parent)


def inventory(root):
    found = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink():
            raise TransactionError('conflict', 'transaction contains a live link', out=root)
        if path.is_file():
            found[path.relative_to(root).as_posix()] = digest(path)
    return found


def put(path, value):
    """An immutable deterministic write; foreign bytes are never overwritten."""
    path = Path(path)
    if path.exists():
        if digest(path) != json_digest(value):
            raise TransactionError('conflict', f'conflicting durable record: {path}')
    else:
        write_json(path, value)
    fsync_directory(path.parent)
    fsync_directory(path.parent.parent)


def receipt(out, phase, payload):
    previous = None
    for name in PHASES[:PHASES.index(phase)]:
        previous = digest(out / (name + '.json'))
    value = {'schema': SCHEMA, 'phase': phase, 'sequence': PHASES.index(phase) + 1,
             'request_sha256': digest(out / 'request.json'), 'previous': previous, **payload}
    put(out / (phase + '.json'), value)
    return value


def load(out):
    out = Path(out).resolve()
    request = read_json(out / 'request.json')
    if not isinstance(request, dict) or request.get('schema') != SCHEMA or request.get('out') != str(out):
        raise TransactionError('conflict', 'invalid transaction identity', out=out)
    records, previous, gap = {}, None, False
    for number, phase in enumerate(PHASES, 1):
        path = out / (phase + '.json')
        if not path.exists():
            gap = True
            continue
        value = read_json(path)
        if (not isinstance(value, dict) or gap or value.get('schema') != SCHEMA or value.get('phase') != phase or
                value.get('sequence') != number or value.get('previous') != previous or
                value.get('request_sha256') != digest(out / 'request.json')):
            raise TransactionError('conflict', 'broken transaction receipt chain', out=out)
        records[phase] = value
        previous = digest(path)
    return request, records


def validate_prepared(out, request, records):
    from .applicability import validate_bundle
    from .baseline import load_baseline
    from .candidate import verify_snapshot

    prepared = records.get('prepared')
    if prepared is None:
        raise TransactionError('ambiguous', 'preparation has no complete durable receipt', out=out,
                               actions=['preserve partial preparation; start a separately identified operation after inspection'])
    if inventory(out / 'prepared') != prepared['files']:
        raise TransactionError('conflict', 'prepared evidence changed or is missing', out=out)
    run = Path(request['run'])
    if request.get('campaign') and not (Path(request['campaign']['root']) / '.campaign-root').is_file():
        raise TransactionError('conflict', 'campaign ancestry lease marker is missing', out=out)
    manifest = verify_snapshot(run)
    if (digest(run / 'candidate.json') != request['manifest_sha256'] or
            digest(run / 'baseline.json') != request['baseline_sha256']):
        raise TransactionError('conflict', 'source candidate changed', out=out)
    bundle = read_json(out / 'prepared/empirical/calibration.json')
    validate_bundle(run / 'inputs', bundle, load_baseline(run), empirical_root=out / 'prepared/empirical')
    original = read_json(out / 'prepared/source-result.json')
    if (identity(original) != request['source_result_id'] or original['candidate_id'] != manifest['candidate_id'] or
            digest(out / 'prepared/source-result.json') != request['source_result_sha256']):
        raise TransactionError('conflict', 'source prediction identity changed', out=out)
    return original


def event_for(out, request):
    bundle = out / 'prepared/empirical'
    # Keep the existing event contract for campaign projection verification.
    return {'schema': 'agent-ee.empirical-feedback/1', 'bundle': str(bundle),
            'bundle_sha256': digest(bundle / 'calibration.json'), 'review': request['review_out']}


def publication_path(run, operation_id):
    return Path(run) / 'bench-publications' / (operation_id + '.json')


def verify_publications(run, result):
    """Ordinary readers refuse a commit intent with a stale acceptance projection."""
    for path in sorted((Path(run) / 'bench-publications').glob('*.json')):
        marker = read_json(path)
        if not isinstance(marker, dict) or marker.get('schema') != SCHEMA:
            raise TransactionError('conflict', 'invalid bench publication marker', out=run)
        out = Path(marker['out'])
        request, records = load(out)
        validate_prepared(out, request, records)
        if marker['request_sha256'] != digest(out / 'request.json') or request['run'] != str(Path(run)):
            raise TransactionError('conflict', 'publication request identity differs', out=out)
        committed = records.get('supersession_committed')
        if (committed is None or committed['publication_sha256'] != digest(path) or
                digest(Path(run) / 'REPORT.md') != committed['report_sha256']):
            raise TransactionError('pending_publication', 'bench publication is partial; explicit recovery required', out=out)
        if result != marker['projection']:
            raise TransactionError('pending_publication', 'bench supersession projection requires explicit recovery',
                                   out=marker['out'], actions=['bench-recover'])
        if read_json(Path(run) / marker['event_path']) != marker['event']:
            raise TransactionError('conflict', 'bench publication event differs from commit intent', out=marker['out'])
        if digest(Path(marker['event']['bundle']) / 'calibration.json') != marker['event']['bundle_sha256']:
            raise TransactionError('conflict', 'published bench bundle changed', out=marker['out'])


def publish(out, request, records):
    from .cli import artifacts, outcome, render, verify_records

    run = Path(request['run'])
    original = validate_prepared(out, request, records)
    event = event_for(out, request)
    event_rel = 'events/bench-' + identity({'operation_id': request['operation_id']}) + '.json'
    actual = artifacts(run)
    if event_rel in actual:
        if digest(run / event_rel) != json_digest(event):
            raise TransactionError('conflict', 'foreign feedback event', out=out)
        del actual[event_rel]
    if actual != original['artifact_hashes']:
        raise TransactionError('conflict', 'source evidence changed during publication', out=out)
    # Recheck all original candidate/check/review/seal contracts without blessing
    # any new artifact. The sole permitted inventory delta is the exact event.
    checked = copy.deepcopy(original)
    checked['artifact_hashes'] = artifacts(run)
    verify_records(run, checked, current_source=False)
    projection = copy.deepcopy(original)
    projection.setdefault('empirical_feedback', []).append(event)
    projection['outcome'] = outcome(projection)
    # Compute the exact bytes write_json will publish, without adding acceptance.
    projection['artifact_hashes'][event_rel] = json_digest(event)
    marker = {'schema': SCHEMA, 'out': str(out), 'request_sha256': digest(out / 'request.json'),
              'event_path': event_rel, 'event': event, 'projection': projection}
    current = read_json(run / 'result.json')
    expected_result_hash = request['source_result_sha256'] if current == original else json_digest(projection)
    if current not in (original, projection) or digest(run / 'result.json') != expected_result_hash:
        raise TransactionError('conflict', 'source result is neither original nor intended projection', out=out)
    put(publication_path(run, request['operation_id']), marker)
    put(run / event_rel, event)
    if current != projection:
        write_json(run / 'result.json', projection)
    render(run, projection)
    with (run / 'REPORT.md').open('rb') as stream:
        os.fsync(stream.fileno())
    fsync_directory(run)
    receipt(out, 'supersession_committed', {'publication_sha256': digest(publication_path(run, request['operation_id'])),
                                          'report_sha256': digest(run / 'REPORT.md')})
    from .cli import verify
    verify(run, current_source=False)


def prepare(run, report, out, request):
    from .empirical import ingest, calibration_for
    from .cli import verify

    original = verify(run, current_source=False)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / 'request.json', request)
    prepared = out / 'prepared'
    prepared.mkdir()
    write_json(prepared / 'source-result.json', original)
    shutil.copyfile(report, prepared / 'report-original.json')
    ingest(run, report, prepared / 'empirical')
    if (digest(prepared / 'report-original.json') != request['report_sha256'] or
            digest(report) != request['report_sha256'] or
            any(digest(report.parent / rel) != sha for rel, sha in request['raw'].items())):
        raise TransactionError('conflict', 'report or raw artifacts changed during preparation', out=out)
    if identity(read_json(run / 'result.json')) != request['source_result_id']:
        raise TransactionError('conflict', 'source changed during preparation', out=out)
    manifest = read_json(run / 'candidate.json')
    staged = prepared / 'candidate-inputs'
    shutil.copytree(run / 'inputs', staged)
    previous = calibration_for(run, manifest['scope'], manifest['files'], provenance_only=True)
    owned = set(previous['artifacts']) | {manifest['scope']['empirical']} if previous else set()
    for rel in owned:
        (staged / rel).unlink()
    for source in (prepared / 'empirical').rglob('*'):
        if source.is_file():
            target = staged / 'empirical' / source.relative_to(prepared / 'empirical')
            if target.exists():
                raise TransactionError('conflict', 'new empirical evidence overlaps a design dependency', out=out)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    scope = copy.deepcopy(manifest['scope'])
    scope['inputs'] = [{'path': str(p), 'at': p.name} for p in sorted(staged.iterdir())]
    scope['empirical'] = 'empirical/calibration.json'
    write_json(prepared / 'scope.json', scope)
    sync_tree(out)
    receipt(out, 'prepared', {'files': inventory(prepared)})


def request_for(run, report, out, review_out, seconds, operation_id):
    from .candidate import safe_relative
    from .measurements import finite
    seconds = finite(seconds, 'max_seconds')
    if not 1 <= seconds <= 86400:
        raise ValueError('max_seconds must be between 1 and 86400')
    if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', operation_id):
        raise ValueError('operation_id must contain 1..80 letters, digits, underscores or hyphens')
    raw = {}
    for item in read_json(report)['artifacts']:
        rel = str(safe_relative(item['path']))
        source = report.parent / rel
        if source.is_symlink() or not source.resolve().is_relative_to(report.parent):
            raise ValueError('bench artifact escapes report directory')
        raw[rel] = digest(source)
        if raw[rel] != item['sha256']:
            raise ValueError('bench raw artifact checksum mismatch')
    manifest = read_json(run / 'candidate.json')
    return {'schema': SCHEMA, 'operation_id': operation_id, 'run': str(run), 'report': str(report),
            'report_sha256': digest(report), 'raw': raw, 'out': str(out), 'review_out': str(review_out),
            'max_seconds': seconds, 'manifest_sha256': digest(run / 'candidate.json'),
            'candidate_id': manifest['candidate_id'], 'scope_id': identity(manifest['scope']),
            'baseline_sha256': digest(run / 'baseline.json')}


def status_value(out, request, records, disposition=None):
    phase = next(reversed(records), None)
    return {'schema': 'agent-ee.bench-recovery/1', 'operation_id': request['operation_id'],
            'transaction': str(out), 'phase': phase, 'disposition': disposition or phase,
            'attempt': latest_attempt(out, records) if 'review_started' in records else None,
            'review': records.get('completed', {}).get('review')}


def settle(out, request, records):
    """Reconcile known local writes and adopt only fully verified terminal evidence."""
    from .cli import verify
    validate_prepared(out, request, records)
    if 'completed' in records:
        verify(request['run'], current_source=False)
        current = verify_terminal(out, records['completed']['review'])
        if request.get('campaign'):
            from .campaign import verify_campaign
            verify_campaign(request['campaign']['root'])
        value = status_value(out, request, records)
        value['current_review'] = current_review(current)
        return value
    publish(out, request, records)
    request, records = load(out)
    if 'review_started' not in records:
        return status_value(out, request, records, 'ready_for_explicit_review')
    attempt = latest_attempt(out, records)
    target = Path(attempt['out'])
    if not (target / 'result.json').is_file():
        return status_value(out, request, records, 'uncertain_execution')
    try:
        result = verify(target, current_source=False)
        manifest = read_json(target / 'candidate.json')
        expected = read_json(out / 'prepared/scope.json')
        if (manifest['scope'] != expected or
                manifest['files'] != inventory(out / 'prepared/candidate-inputs')):
            raise ValueError('review scope differs from prepared scope')
        if (result.get('previous_run') != request['run'] or
                result['state'] not in ('complete', 'incomplete', 'interrupted') or not result.get('finished_utc')):
            raise ValueError('review has no verified terminal completion')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        value = status_value(out, request, records, 'uncertain_execution')
        value['failed_checks'] = [str(exc)]
        return value
    terminal = {'attempt_id': attempt['id'], 'out': str(target), 'run_id': result['run_id'],
                'candidate_id': result['candidate_id'], 'result_sha256': digest(target / 'result.json'),
                'state': result['state'], 'outcome': result['outcome']}
    put(out / 'terminal-result.json', result)
    sync_tree(target)
    finish_campaign(out, request, terminal)
    receipt(out, 'completed', {'review': terminal})
    value = status_value(out, request, load(out)[1])
    value['current_review'] = current_review(result)
    return value


def launch(out, request, records):
    from .cli import run_review
    if 'review_started' in records:
        return settle(out, request, records)
    target = Path(request['review_out'])
    if target.exists():
        raise TransactionError('ambiguous', 'review target already exists', out=out)
    receipt(out, 'review_started', {'attempt': {'id': request['operation_id'] + '-1', 'out': str(target)}})
    run_review(out / 'prepared/scope.json', target, request['max_seconds'],
               previous=request['run'], output_stream=sys.stderr,
               max_schema_resubmissions=read_json(out / 'prepared/source-result.json').get('max_schema_resubmissions', 0))
    return settle(out, request, load(out)[1])


def transact(run, report, out, max_seconds, *, operation_id, review_out=None):
    run, report, out = Path(run).resolve(), Path(report).resolve(), Path(out).resolve()
    review_out = Path(review_out).resolve() if review_out else out / 'review'
    if (out.is_relative_to(run) or run.is_relative_to(out) or review_out.is_relative_to(run) or
            run.is_relative_to(review_out) or (review_out.is_relative_to(out) and review_out != out / 'review')):
        raise TransactionError('conflict', 'transaction paths overlap protected source or preparation', out=out)
    preexisting_output = out.exists()
    prior_target = review_out
    if (out / 'review_started.json').is_file():
        prior_records = load(out)[1]
        prior_target = latest_attempt(out, prior_records)['out']
    with writer_lock(run, out, review_out, prior_target):
        request = request_for(run, report, out, review_out, max_seconds, operation_id)
        reservations = run / 'bench-operations'
        for path in sorted(reservations.glob('*.json')):
            existing = read_json(path)
            same_id = existing['operation_id'] == operation_id
            equivalent = all(existing.get(k) == request[k] for k in
                             ('run', 'candidate_id', 'manifest_sha256', 'scope_id', 'report_sha256', 'raw', 'max_seconds'))
            if same_id:
                if any(existing.get(k) != v for k, v in request.items()):
                    raise TransactionError('conflict', 'operation ID has a different request', out=out)
                request = existing
                if not (out / 'request.json').is_file():
                    raise TransactionError('ambiguous', 'reserved operation has incomplete preparation', out=out)
                retained = load(out)[1]
                if 'review_started' in retained and latest_attempt(out, retained)['out'] != str(prior_target):
                    raise TransactionError('conflict', 'attempt changed while acquiring leases; retry diagnosis', out=out)
                return settle(out, request, retained)
            if equivalent:
                return {'schema': 'agent-ee.bench-recovery/1', 'disposition': 'existing_operation',
                        'operation_id': existing['operation_id'], 'transaction': existing['out']}
        from .cli import verify
        original = verify(run, current_source=False)
        if original.get('empirical_feedback'):
            raise TransactionError('conflict', 'source is superseded; recover its existing transaction', out=out)
        lease_file = review_out.parent / ('.' + review_out.name + '.writer.lock')
        if preexisting_output or (out.exists() and any(p != lease_file for p in out.iterdir())) or review_out.exists():
            raise TransactionError('conflict', 'transaction or review target already exists', out=out)
        request['source_result_id'] = identity(original)
        request['source_result_sha256'] = digest(run / 'result.json')
        campaign = campaign_context(run, review_out)
        if campaign is not None:
            request['campaign'] = campaign
        put(reservations / (operation_id + '.json'), request)
        prepare(run, report, out, request)
        records = load(out)[1]
        publish(out, request, records)
        return launch(out, request, load(out)[1])


def recover(out, *, launch_review=False):
    out = Path(out).resolve()
    try:
        request, records = load(out)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise TransactionError('ambiguous', f'no valid opt-in transaction: {exc}', out=out,
                               actions=['preserve partial or legacy files; no guessed conversion']) from exc
    prior_target = latest_attempt(out, records)['out'] if 'review_started' in records else request['review_out']
    with writer_lock(request['run'], out, request['review_out'], prior_target):
        request, records = load(out)
        if 'review_started' in records and latest_attempt(out, records)['out'] != prior_target:
            raise TransactionError('conflict', 'attempt changed while acquiring leases; retry diagnosis', out=out)
        value = settle(out, request, records)
        if launch_review and value['disposition'] == 'ready_for_explicit_review':
            return launch(out, request, load(out)[1])
        return value


def review_attempts(out, records):
    attempt = records['review_started']['attempt']
    request = read_json(out / 'request.json')
    if attempt != {'id': request['operation_id'] + '-1', 'out': request['review_out']}:
        raise TransactionError('conflict', 'initial review attempt differs from pinned request', out=out)
    attempts = [attempt]
    previous = digest(out / 'review_started.json')
    for index, path in enumerate(sorted((out / 'attempts').glob('*.json')), 2):
        record = read_json(path)
        if (record.get('schema') != SCHEMA or path.name != f'{index:06}.json' or
                record.get('sequence') != index or record.get('previous') != previous):
            raise TransactionError('conflict', 'broken review attempt chain', out=out)
        previous = digest(path)
        attempts.append(record['attempt'])
    return attempts


def latest_attempt(out, records):
    return review_attempts(out, records)[-1]


def new_attempt(out, attempt_id, target):
    """Explicit operator inference authorization; retain every uncertain attempt."""
    out, target = Path(out).resolve(), Path(target).resolve()
    # Validate the complete retained history while supported writers are excluded.
    # Target leases create parent directories/files, so acquire them only after
    # proving that the new path is disjoint from every retained evidence tree.
    with writer_lock(out):
        request, records = load(out)
        if 'review_started' not in records or 'completed' in records:
            raise TransactionError('conflict', 'new attempt requires an unsettled prior review start', out=out)
        retained = review_attempts(out, records)
        protected = [out, Path(request['run']).resolve(), *(Path(a['out']).resolve() for a in retained)]
        if any(target.is_relative_to(path) or path.is_relative_to(target) for path in protected):
            raise TransactionError('conflict', 'attempt target overlaps retained evidence', out=out)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', attempt_id):
            raise ValueError('invalid attempt ID')
        if target.exists():
            raise TransactionError('conflict', 'new attempt requires a fresh run directory', out=out)
        if any(attempt['id'] == attempt_id for attempt in retained):
            raise TransactionError('conflict', 'attempt ID already used', out=out)
        with writer_lock(request['run'], request['review_out'], retained[-1]['out'], target):
            if target.exists():
                raise TransactionError('conflict', 'new attempt requires a fresh run directory', out=out)
            value = settle(out, request, records)
            if value['disposition'] != 'uncertain_execution':
                raise TransactionError('conflict', 'prior attempt already completed', out=out)
            attempts = sorted((out / 'attempts').glob('*.json'))
            previous = attempts[-1] if attempts else out / 'review_started.json'
            put(out / 'attempts' / f'{len(attempts) + 2:06}.json', {
                'schema': SCHEMA, 'sequence': len(attempts) + 2, 'previous': digest(previous),
                'attempt': {'id': attempt_id, 'out': str(target)}})
            from .cli import run_review
            run_review(out / 'prepared/scope.json', target, request['max_seconds'],
                       previous=request['run'], output_stream=sys.stderr,
                       max_schema_resubmissions=read_json(out / 'prepared/source-result.json').get('max_schema_resubmissions', 0))
            return settle(out, request, load(out)[1])


def campaign_context(run, target):
    root = next((p for p in run.parents if (p / '.campaign-root').is_file()), None)
    if root is None:
        return None
    from .campaign import verify_campaign
    state = read_json(root / 'state.json')
    checked = verify_campaign(root)
    from .applicability import pending_recalibration
    if (not state['history'] or state['history'][-1]['run'] != str(run) or
            (checked['state'] not in ('criteria_satisfied', 'empirical_superseded') and
             not (checked['state'] == 'incomplete' and pending_recalibration(run)))):
        raise TransactionError('conflict', 'campaign source is not eligible for bench feedback', out=root)
    if target != root / 'runs' / str(len(state['history']) + 1):
        raise TransactionError('conflict', 'campaign feedback requires its declared next run', out=root)
    config = read_json(root / 'config.json')
    if len(state['history']) >= config['max_iterations']:
        raise TransactionError('conflict', 'campaign iteration budget exhausted', out=root)
    return {'root': str(root), 'state': state, 'config_sha256': digest(root / 'config.json'),
            'profile_sha256': digest(root / 'profile.json')}


def finish_campaign(out, request, terminal):
    context = request.get('campaign')
    if context is None:
        return
    from .baseline import load_baseline
    from .convergence import advance
    from .campaign import verify_campaign
    root, target = Path(context['root']), Path(terminal['out'])
    if (digest(root / 'config.json') != context['config_sha256'] or
            digest(root / 'profile.json') != context['profile_sha256']):
        raise TransactionError('conflict', 'campaign configuration changed', out=out)
    state = copy.deepcopy(context['state'])
    if load_baseline(target) != state['baseline']:
        raise TransactionError('conflict', 'feedback review changed immutable campaign baseline', out=out)
    verify_campaign(root)
    config = read_json(root / 'config.json')
    result, manifest = read_json(target / 'result.json'), read_json(target / 'candidate.json')
    epoch = state.get('epoch', 0) + 1
    record, diagnostic = advance([], result, manifest, config['max_iterations'] - len(state['history']))
    snapshot = root / 'history' / f"{len(state['history']) + 1}.json"
    record.update(run=str(target), result_sha256=terminal['result_sha256'], result_snapshot=str(snapshot),
                  epoch=epoch, calibration_id=manifest['files'][manifest['scope']['empirical']])
    if str(target) != request['review_out']:
        record.update(bench_transaction=str(out), bench_attempt_id=terminal['attempt_id'])
    state.update(epoch=epoch)
    state.pop('error', None)
    state['history'].append(record)
    state.update({k: v for k, v in diagnostic.items() if k not in ('schema', 'history')})
    state['diagnostic'] = diagnostic
    current = read_json(root / 'state.json')
    if current not in (context['state'], state):
        raise TransactionError('conflict', 'campaign history differs from expected predecessor or projection', out=out)
    put(out / 'campaign-intent.json', {'state': state, 'snapshot': str(snapshot), 'result': result})
    put(snapshot, result)
    if state['state'] == 'escalated':
        write_json(root / 'tradeoff.json', diagnostic)
    write_json(root / 'state.json', state)
    fsync_directory(root)
    verify_campaign(root)


def status(out):
    """Read-only coherent diagnosis, including legacy/partial transaction refusal."""
    from .locking import reader_lock
    out = Path(out).resolve()
    try:
        request, preliminary = load(out)
        target = latest_attempt(out, preliminary)['out'] if 'review_started' in preliminary else request['review_out']
        with reader_lock(request['run'], out, request['review_out'], target, require_existing=True):
            request, records = load(out)
            if 'review_started' in records and latest_attempt(out, records)['out'] != target:
                raise TransactionError('conflict', 'attempt changed while acquiring leases; retry diagnosis', out=out)
            validate_prepared(out, request, records)
            value = status_value(out, request, records)
            value['coherence'] = 'coherent'
            from .cli import verify
            try:
                verify(request['run'], current_source=False)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                value['disposition'] = 'pending_or_invalid_publication'
                value['failed_checks'] = [str(exc)]
            if 'completed' in records:
                current = verify_terminal(out, records['completed']['review'])
                value['current_review'] = current_review(current)
                if request.get('campaign'):
                    from .campaign import verify_campaign_records
                    verify_campaign_records(Path(request['campaign']['root']))
            if 'review_started' in records and 'completed' not in records:
                value['disposition'] = 'uncertain_execution'
            return value
    except TransactionError as exc:
        return exc.diagnostic
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return {'schema': 'agent-ee.bench-recovery/1', 'disposition': 'unverified',
                'transaction': str(out), 'reason': str(exc),
                'operator_actions': ['preserve retained files; no automatic legacy conversion']}


def campaign_transaction(root, report, out, max_seconds, *, operation_id):
    root, out = Path(root).resolve(), Path(out).resolve()
    # Read identity to declare the complete ordered lease set, then revalidate
    # under that lease. An advanced campaign cannot redirect an existing ID.
    existing = out / 'request.json'
    state = read_json(root / 'state.json')
    if existing.is_file() and read_json(existing).get('campaign', {}).get('root') != str(root):
        raise TransactionError('conflict', 'operation belongs to a different campaign', out=out)
    if not existing.is_file() and not state.get('history'):
        raise TransactionError('conflict', 'campaign has no feedback source candidate', out=out)
    run = Path(read_json(existing)['run']) if existing.is_file() else Path(state['history'][-1]['run'])
    target = Path(read_json(existing)['review_out']) if existing.is_file() else root / 'runs' / str(len(state['history']) + 1)
    return transact(run, report, out, max_seconds, operation_id=operation_id, review_out=target)


def verify_campaign_attempt(root, stored, previous):
    """Narrow exception for an explicitly identified replacement inference attempt."""
    out = Path(stored['bench_transaction'])
    request, records = load(out)
    validate_prepared(out, request, records)
    attempt = latest_attempt(out, records)
    if (request.get('campaign', {}).get('root') != str(root) or request['run'] != previous['run'] or
            attempt['id'] != stored['bench_attempt_id'] or attempt['out'] != stored['run'] or
            digest(out / 'prepared/empirical/calibration.json') != stored['calibration_id']):
        raise TransactionError('conflict', 'campaign review attempt identity differs', out=out)
    return request['review_out']


def verify_terminal(out, terminal):
    from .cli import verify
    snapshot_path = out / 'terminal-result.json'
    if digest(snapshot_path) != terminal['result_sha256']:
        raise TransactionError('conflict', 'terminal review snapshot changed', out=out)
    snapshot = read_json(snapshot_path)
    request, records = load(out)
    attempt = latest_attempt(out, records)
    if (terminal['attempt_id'] != attempt['id'] or terminal['out'] != attempt['out'] or
            any(terminal[key] != snapshot.get(key) for key in ('run_id', 'candidate_id', 'state', 'outcome'))):
        raise TransactionError('conflict', 'terminal receipt contradicts retained review', out=out)
    current = verify(terminal['out'], current_source=False)
    if current != snapshot:
        allowed = {'empirical_feedback', 'artifact_hashes', 'outcome'}
        if (not current.get('empirical_feedback') or
                {k: v for k, v in current.items() if k not in allowed} !=
                {k: v for k, v in snapshot.items() if k not in allowed} or
                any(current['artifact_hashes'].get(k) != v for k, v in snapshot['artifact_hashes'].items())):
            raise TransactionError('conflict', 'completed review changed beyond empirical supersession', out=out)

    return current


def current_review(result):
    return {**{key: result[key] for key in ('run_id', 'candidate_id', 'state', 'outcome')},
            'empirical_superseded': bool(result.get('empirical_feedback'))}

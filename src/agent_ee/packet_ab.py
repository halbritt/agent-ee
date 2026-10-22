"""Capped matched-snapshot reviewer A/B for factual packet navigation (AGENTEE-12).

Both arms review one captured run: the same candidate, checks, run_id and launch
expectation. The indexed arm gets the packet as built; the baseline arm gets the
historical packet, without the navigation extension or its files. Every provider
call is first reserved in a durable ledger, which refuses calls beyond the cap.
"""

import datetime
import fcntl
import json
import os
import re
import shutil
import statistics
import time
from pathlib import Path

from .storage import canonical, digest, identity, read_json, write_json

CAP = 12
LEDGER_SCHEMA = 'agent-ee.call-ledger/1'
PLAN_SCHEMA = 'agent-ee.packet-index-ab-plan/1'
CELL_SCHEMA = 'agent-ee.packet-index-ab-cell/1'
REPORT_SCHEMA = 'agent-ee.packet-index-ab-report/1'
ARMS = ('baseline', 'indexed')
FIXTURES = {'seeded': 'fixtures/rc/seeded.scope.json', 'control': 'fixtures/rc/control.scope.json'}
PROFILE = 'profiles/claude-baseline.json'
ROLE = 'circuit'
RESUBMISSIONS = 1
TERMINAL = ('unavailable', 'failed', 'timeout', 'interrupted')
# Seed facts from docs/evaluation-plan.md: R1 saved as 100k, 1 ms response about 0.095 V.
SEED_ENTITY = re.compile(r'\bR1\b')
SEED_FACT = re.compile(r'100\s*k|time[ -]constant|\btau\b|τ|0\.09\d*', re.IGNORECASE)
FINDING_TEXT = ('title', 'location', 'condition', 'mechanism', 'reasoning')


class CallCapReached(RuntimeError):
    pass


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class CallLedger:
    """Append-only JSONL. A reservation is fsynced before its call and is never undone.

    Retries and resubmissions are separate reservations. A line that does not parse
    (for example one torn by a crash) counts as a real call, so damage never frees budget.
    """

    def __init__(self, path, cap=CAP):
        if not isinstance(cap, int) or not 0 < cap <= CAP:
            raise ValueError(f'ledger cap must be 1..{CAP}')
        self.path = Path(path)
        self.cap = cap

    def _open(self):
        created = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, 'a+', encoding='utf-8')
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        if created:
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return handle

    @staticmethod
    def _append(handle, record):
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(record, sort_keys=True) + '\n')
        handle.flush()
        os.fsync(handle.fileno())

    def _entries(self, handle):
        handle.seek(0)
        entries = []
        for line in handle.read().splitlines():
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    raise ValueError
            except ValueError:
                entry = {'event': 'malformed'}
            entries.append(entry)
        if not entries:
            self._append(handle, {'schema': LEDGER_SCHEMA, 'event': 'header', 'cap': self.cap, 'created_utc': _now()})
            return self._entries(handle)
        header = entries[0]
        if header.get('schema') != LEDGER_SCHEMA or header.get('event') != 'header':
            raise ValueError('call ledger header missing or malformed')
        if header.get('cap') != self.cap:
            raise ValueError(f'call ledger cap {header.get("cap")!r} differs from requested cap {self.cap}')
        return entries

    @staticmethod
    def _summary(entries):
        reserved = [e for e in entries if e.get('event') == 'reserve']
        malformed = sum(1 for e in entries if e.get('event') == 'malformed')
        return {'real_calls': sum(1 for e in reserved if e.get('kind') == 'real') + malformed,
                'fake_calls': sum(1 for e in reserved if e.get('kind') == 'fake'),
                'refused': sum(1 for e in entries if e.get('event') == 'refused'),
                'malformed_lines': malformed,
                'unsettled': sorted({e['seq'] for e in reserved} -
                                    {e.get('seq') for e in entries if e.get('event') == 'settle'})}

    def state(self):
        with self._open() as handle:
            entries = self._entries(handle)
            return {'path': str(self.path), 'cap': self.cap, **self._summary(entries)}

    def remaining(self):
        return self.cap - self.state()['real_calls']

    def reserve(self, kind, cell, label, plan_id):
        if kind not in ('real', 'fake'):
            raise ValueError('ledger kind must be real or fake')
        with self._open() as handle:
            entries = self._entries(handle)
            summary = self._summary(entries)
            if kind == 'real' and summary['real_calls'] >= self.cap:
                self._append(handle, {'event': 'refused', 'kind': kind, 'cell': cell, 'label': label,
                                      'plan_id': plan_id, 'at_utc': _now(), 'real_calls': summary['real_calls']})
                raise CallCapReached(f'subscription call cap {self.cap} reached; call refused before launch')
            seq = len(entries)
            self._append(handle, {'event': 'reserve', 'seq': seq, 'kind': kind, 'cell': cell, 'label': label,
                                  'plan_id': plan_id, 'at_utc': _now(),
                                  'real_calls_after': summary['real_calls'] + (kind == 'real')})
            return seq

    def settle(self, seq, outcome):
        with self._open() as handle:
            self._entries(handle)
            self._append(handle, {'event': 'settle', 'seq': seq, 'at_utc': _now(), **outcome})


def plan():
    """Preregistered matrix: sequential, arm order alternated (ABBA) across matched pairs."""
    order = [('seeded', 1, ARMS), ('control', 1, ARMS[::-1]), ('seeded', 2, ARMS[::-1]), ('control', 2, ARMS)]
    cells = [{'id': f'{fixture}-r{replicate}-{arm}', 'fixture': fixture, 'replicate': replicate, 'arm': arm}
             for fixture, replicate, arms in order for arm in arms]
    return {'schema': PLAN_SCHEMA, 'item': 'AGENTEE-12', 'cap': CAP, 'profile': PROFILE, 'role': ROLE,
        'fixtures': FIXTURES, 'arms': {'baseline': 'packet without navigation or work/.navigation files',
                                       'indexed': 'packet as built, with navigation and work/.navigation files'},
        'cells': cells, 'execution': 'sequential, one initial reviewer per cell, no adjudicator',
        'max_schema_resubmissions': RESUBMISSIONS,
        'stop_rules': ['a cell starts only while the ledger has budget; a refused reservation launches nothing',
                       'stop after an unavailable execution or two consecutive failed/timeout/interrupted cells',
                       'no fallback provider, model or profile; a failed cell is retained, not replaced'],
        'scoring': {'seeded_detected': 'a finding (defect or concern) whose title/location/condition/mechanism/'
                    'reasoning names R1 and matches ' + SEED_FACT.pattern,
                    'seeded_missed': 'completed seeded review without a detecting finding',
                    'control_false_alarm': 'findings with assessment defect on the control',
                    'not_scored': 'cells without an accepted review are process failures, not misses'},
        'metrics': ['provider modelUsage tokens per attempt', 'terminal usage and num_turns',
                    'executor duration_s and provider duration_api_ms', 'packet prompt bytes']}


def historical_packet(packet):
    """The packet a reviewer received before the navigation extension, for the same run."""
    result = {k: v for k, v in packet.items() if k not in ('navigation', 'packet_sha256')}
    result['evidence_access'] = {k: v for k, v in packet['evidence_access'].items()
                                 if k not in ('navigation', 'binding')}
    result['citation_files'] = [c for c in packet['citation_files'] if not c['path'].startswith('work/.navigation/')]
    result['packet_sha256'] = identity(result)
    return result


def prepare(root, *, repository=Path('.')):
    """Checks-only capture of each fixture and both arm packets. No provider call."""
    from .cli import run_review
    from .reviewers import build_packet
    root = Path(root).resolve()
    repository = Path(repository).resolve()
    summary = {}
    for fixture, scope in FIXTURES.items():
        run = root / 'prepared' / fixture
        run_review(repository / scope, run, 1200, checks_only=True, profile=str(repository / PROFILE))
        result = read_json(run / 'result.json')
        if not result['checks'] or any(c.get('execution') != 'ok' for c in result['checks']):
            raise ValueError(f'{fixture}: prepared checks did not all execute')
        summary[fixture] = {'run_id': result['run_id'], 'candidate_id': result['candidate_id'], 'arms': {}}
        for arm in ARMS:
            target = root / 'arms' / fixture / arm
            shutil.copytree(run, target, symlinks=True)
            packet = build_packet(target, result['checks'], result['run_id'])
            if arm == 'baseline':
                shutil.rmtree(target / 'work' / '.navigation')
                packet = historical_packet(packet)
                write_json(target / 'packet.json', packet)
            summary[fixture]['arms'][arm] = {'packet_sha256': packet['packet_sha256'],
                'prompt_packet_bytes': len(json.dumps(packet).encode('utf-8')),
                'canonical_packet_bytes': len(canonical(packet))}
    write_json(root / 'prepared.json', summary)
    return summary


def fake_dispatch(run_dir, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
    """Dry-run provider: a scripted review, never a model call or engineering evidence."""
    from .findings import REVIEW_VERSION
    run_dir = Path(run_dir)
    packet = read_json(run_dir / 'packet.json')
    findings = []
    check = read_json(run_dir / 'checks' / 'rc-step.json')
    if check.get('outcome') == 'defect':
        findings.append({'id': 'dry-run-seed', 'title': 'DRY RUN scripted finding: R1 100k',
            'location': 'R1', 'condition': 'dry run', 'mechanism': 'scripted', 'severity': 'info',
            'assessment': 'concern', 'reasoning': 'Fake provider output for plumbing only.',
            'evidence': [{'path': 'checks/rc-step.json', 'locator': 'check outcome', 'pointer': '/outcome'}]})
    value = {'schema': REVIEW_VERSION, 'candidate_id': packet['candidate_id'], 'run_id': packet['run_id'],
             'packet_sha256': packet['packet_sha256'], 'role': label,
             'coverage': ['DRY RUN: fake provider, no model call'], 'limitations': ['DRY RUN'], 'findings': findings}
    return {'backend': 'fake-dry-run', 'execution': 'ok', 'billing_route': 'none',
            'model_requested': definition['model'], 'duration_s': 0.0}, value


def metered(ledger, mode, plan_id, provider):
    """Wrap a reviewer dispatch so each call is reserved (fsynced) before it launches."""
    kind = {'live': 'real', 'dry-run': 'fake'}[mode]

    def dispatch(run_dir, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
        cell = Path(run_dir).name
        try:
            seq = ledger.reserve(kind, cell, label, plan_id)
        except CallCapReached as exc:
            return {'execution': 'not_run', 'backend': definition['harness'], 'error': str(exc)}, None
        try:
            record, value = provider(run_dir, label, definition, prompt, schema, timeout,
                                     cancellation=cancellation, executable=executable)
        except BaseException as exc:
            ledger.settle(seq, {'execution': 'raised', 'error': type(exc).__name__})
            raise
        attempt = Path(run_dir) / 'ab-attempts' / f'{seq:03d}'
        attempt.mkdir(parents=True)
        for name in ('stdout.log', 'stderr.log'):
            source = Path(run_dir) / 'review-work' / label / name
            if source.is_file():
                shutil.copyfile(source, attempt / name)
        write_json(attempt / 'record.json', {'seq': seq, 'kind': kind, 'prompt_bytes': len(prompt.encode('utf-8')),
                                             'record': record})
        ledger.settle(seq, {'execution': record.get('execution'), 'duration_s': record.get('duration_s')})
        return record, value
    return dispatch


def console_usage(path):
    """Terminal Claude stream-json usage, exactly as reported; None when absent or ambiguous."""
    if not Path(path).is_file():
        return None
    terminal = []
    for line in Path(path).read_text(errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get('type') == 'result':
            terminal.append(event)
    if len(terminal) != 1:
        return None
    t = terminal[0]
    return {k: t.get(k) for k in ('usage', 'modelUsage', 'total_cost_usd', 'num_turns', 'duration_ms',
                                  'duration_api_ms', 'subtype', 'is_error')}


def tokens(model_usage):
    if not isinstance(model_usage, dict) or not model_usage:
        return None
    keys = ('inputTokens', 'outputTokens', 'cacheReadInputTokens', 'cacheCreationInputTokens', 'webSearchRequests')
    total = {k: sum(int(m.get(k) or 0) for m in model_usage.values()) for k in keys}
    total['costUSD_estimate'] = sum(float(m.get('costUSD') or 0) for m in model_usage.values())
    total['prompt_tokens_total'] = (total['inputTokens'] + total['cacheReadInputTokens'] +
                                    total['cacheCreationInputTokens'])
    total['models'] = sorted(model_usage)
    return total


def score(fixture, review):
    if review is None:
        return {'scored': False, 'reason': 'no accepted review; process failure, not a miss'}
    findings = review['findings']
    hits = [f['id'] for f in findings if SEED_ENTITY.search(text := ' '.join(str(f.get(k, '')) for k in FINDING_TEXT))
            and SEED_FACT.search(text)]
    defects = [f['id'] for f in findings if f['assessment'] == 'defect']
    navigation = sorted({e['path'] for f in findings for e in f['evidence'] if e['path'].startswith('work/.navigation/')})
    result = {'scored': True, 'findings': len(findings), 'defects': len(defects), 'navigation_citations': navigation}
    if fixture == 'seeded':
        result.update(seed_detected=bool(hits), seed_missed=not hits, detecting_findings=hits,
                      seed_detected_as_defect=any(f['id'] in hits and f['assessment'] == 'defect' for f in findings))
    else:
        result.update(false_alarm_defects=len(defects))
    return result


def run_cell(root, cell, ledger, mode, plan_id, provider):
    from . import reviewers
    root = Path(root).resolve()
    target = root / 'cells' / cell['id']
    if target.exists():
        raise ValueError(f'cell {cell["id"]} already exists; a cell is never rerun')
    shutil.copytree(root / 'arms' / cell['fixture'] / cell['arm'], target, symlinks=True)
    result = read_json(target / 'result.json')
    packet = read_json(target / 'packet.json')
    original = reviewers.dispatch
    reviewers.dispatch = metered(ledger, mode, plan_id, provider)
    started = time.monotonic()
    try:
        reviews, executions = reviewers.initial_reviews(
            target, packet, time.monotonic() + 1000, roles=[ROLE],
            launch_context={'expected': result['launch_expectation'], 'max_schema_resubmissions': RESUBMISSIONS})
    finally:
        reviewers.dispatch = original
    wall = time.monotonic() - started
    review = next((r for r in reviews if r['role'] == ROLE), None)
    execution = next(e for e in executions if e['role'] == ROLE)
    attempts = []
    for directory in sorted((target / 'ab-attempts').glob('*')):
        record = read_json(directory / 'record.json')
        attempts.append({'seq': record['seq'], 'kind': record['kind'], 'prompt_bytes': record['prompt_bytes'],
            'execution': record['record'].get('execution'), 'duration_s': record['record'].get('duration_s'),
            'duration_api_ms': record['record'].get('duration_api_ms'),
            'model_requested': record['record'].get('model_requested'),
            'model_observed': record['record'].get('model_observed'),
            'tokens': tokens(record['record'].get('model_usage')), 'terminal': console_usage(directory / 'stdout.log')})
    record = {'schema': CELL_SCHEMA, 'mode': mode, 'plan_id': plan_id, **cell,
              'candidate_id': packet['candidate_id'], 'run_id': packet['run_id'],
              'packet_sha256': packet['packet_sha256'], 'packet_has_navigation': 'navigation' in packet,
              'prompt_packet_bytes': len(json.dumps(packet).encode('utf-8')),
              'execution': execution.get('execution'), 'error': execution.get('error'),
              'cell_wall_s': round(wall, 3), 'attempts': attempts, 'score': score(cell['fixture'], review)}
    write_json(target / 'ab-cell.json', record)
    return record


def run(root, ledger_path, mode, *, provider=None):
    if mode not in ('live', 'dry-run'):
        raise ValueError('mode must be live or dry-run')
    root = Path(root).resolve()
    ledger = CallLedger(ledger_path)
    if mode == 'live':
        if Path(ledger_path).resolve().is_relative_to('/tmp'):
            raise ValueError('a live call ledger must be durable, outside /tmp')
        from .reviewers import dispatch as real
        provider = provider or real
    else:
        provider = provider or fake_dispatch
    current = plan()
    plan_id = identity(current)
    write_json(root / f'plan-{mode}.json', {**current, 'plan_id': plan_id})
    cells = []
    failures = 0
    stopped = None
    for cell in current['cells']:
        if stopped is None and mode == 'live' and ledger.remaining() <= 0:
            stopped = 'call cap reached'
        if stopped is not None:
            cells.append({'schema': CELL_SCHEMA, 'mode': mode, **cell, 'execution': 'not_run', 'error': stopped})
            continue
        record = run_cell(root, cell, ledger, mode, plan_id, provider)
        cells.append(record)
        failures = failures + 1 if record['execution'] in TERMINAL else 0
        if record['execution'] == 'unavailable' or failures >= 2:
            stopped = f'stop rule after {cell["id"]}: {record["execution"]}'
    outcome = {'schema': 'agent-ee.packet-index-ab-run/1', 'mode': mode, 'plan_id': plan_id,
               'ledger': ledger.state(), 'cells': cells}
    write_json(root / f'run-{mode}.json', outcome)
    return outcome


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _cell_total(cell, key):
    """Sum over every attempt, or None when any attempt lacks provider usage."""
    attempts = cell.get('attempts', [])
    values = [a['tokens'][key] for a in attempts if a.get('tokens')]
    return sum(values) if attempts and len(values) == len(attempts) else None


def _cell_seconds(cell, key):
    values = [a.get(key) for a in cell.get('attempts', [])]
    return sum(values) if values and None not in values else None


def report(run_record):
    """Descriptive per-arm measurements; never a savings claim from this sample size.

    Medians cover cells with an accepted review; ``spent`` covers every call made.
    A pair is comparable only when both of its cells have accepted reviews.
    """
    arms = {}
    for arm in ARMS:
        cells = [c for c in run_record['cells'] if c['arm'] == arm]
        accepted = [c for c in cells if c.get('score', {}).get('scored')]
        seeded = [c['score'] for c in accepted if c['fixture'] == 'seeded']
        control = [c['score'] for c in accepted if c['fixture'] == 'control']
        spent = [_cell_total(c, 'prompt_tokens_total') for c in cells if c.get('attempts')]
        arms[arm] = {'cells': len(cells), 'calls': sum(len(c.get('attempts', [])) for c in cells),
            'accepted_reviews': len(accepted), 'executions': {c['id']: c['execution'] for c in cells},
            'median_prompt_tokens_total': _median([_cell_total(c, 'prompt_tokens_total') for c in accepted]),
            'median_cache_creation_tokens': _median([_cell_total(c, 'cacheCreationInputTokens') for c in accepted]),
            'median_output_tokens': _median([_cell_total(c, 'outputTokens') for c in accepted]),
            'median_reviewer_seconds': _median([_cell_seconds(c, 'duration_s') for c in accepted]),
            'median_api_ms': _median([_cell_seconds(c, 'duration_api_ms') for c in accepted]),
            'spent_prompt_tokens_total': sum(spent) if spent and None not in spent else None,
            'spent_output_tokens': sum(_cell_total(c, 'outputTokens') or 0 for c in cells),
            'seeded_scored': len(seeded), 'seeded_detected': sum(s['seed_detected'] for s in seeded),
            'seeded_missed': sum(s['seed_missed'] for s in seeded),
            'control_scored': len(control), 'control_false_alarm_defects': sum(s['false_alarm_defects'] for s in control),
            'navigation_citations': sum(len(s['navigation_citations']) for s in seeded + control)}
    pairs = []
    for cell in run_record['cells']:
        if cell['arm'] != 'indexed':
            continue
        peer = next(c for c in run_record['cells'] if c['arm'] == 'baseline' and c['fixture'] == cell['fixture']
                    and c['replicate'] == cell['replicate'])
        pair = {'fixture': cell['fixture'], 'replicate': cell['replicate'],
                'comparable': all(c.get('score', {}).get('scored') for c in (cell, peer))}
        for name, c in (('baseline', peer), ('indexed', cell)):
            pair[name] = {'execution': c['execution'], 'calls': len(c.get('attempts', [])),
                          'prompt_tokens_total': _cell_total(c, 'prompt_tokens_total'),
                          'output_tokens': _cell_total(c, 'outputTokens'),
                          'reviewer_seconds': _cell_seconds(c, 'duration_s')}
        pairs.append(pair)
    return {'schema': REPORT_SCHEMA, 'mode': run_record['mode'], 'plan_id': run_record['plan_id'],
            'ledger': run_record['ledger'], 'arms': arms, 'pairs': pairs,
            'claim': 'none: descriptive measurements of at most two matched pairs per fixture on one small fixture; '
                     'no savings, latency or detection improvement is claimed or excluded',
            'limits': ['One initial circuit reviewer per cell; no adjudication.',
                       'The rc-step check already reports the seed; detection measures reviewer reporting, '
                       'not independent discovery.',
                       'prompt_tokens_total sums uncached input, cache reads and cache writes over every agent turn.',
                       'costUSD is the harness estimate, not subscription billing.',
                       'Scoring is the mechanical preregistered rule in the plan, not engineering adjudication.']}


def manifest(root):
    root = Path(root).resolve()
    files = {p.relative_to(root).as_posix(): {'sha256': digest(p), 'bytes': p.stat().st_size}
             for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()}
    return {'schema': 'agent-ee.evidence-manifest/1', 'files': files}

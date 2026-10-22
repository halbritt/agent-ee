"""Opt-in bounded timing and Stage-A replay trace of the placement pipeline (AGENTEE-430, G0).

Tracing is off unless a caller enters ``recording()`` (the ``pack`` and ``overhead``
commands here, or ``placer_runner --trace``). While off, every hook is a context
variable read that returns None; placement decisions, plans and saved boards are the
same with tracing on and off. The record is a ``pcb-trace-envelope/1`` (see
``trace_envelope``) whose ``payload`` is ``agent-ee.placement-trace/1``:

* per-stage monotonic durations: preparation, Stage-A search (candidate geometry,
  wire-cost lower bounds, exact wire cost, lazy ranking, predicates), Stage-A
  complete-proposal judging, Stage-B block arrangement, native checks and export;
* counters that reconcile: every predicate step is doomed, admitted or rejected,
  and every generated candidate is exact-resolved or left unresolved;
* a capped replay sample of Stage-A scoring: per scan, the part, its at-origin
  signal pads per quarter turn and its same-net peers at the scan's placement
  generation; per resolved candidate, its pose, bound and exact cost.

Limits bound what is retained while recording, not only at export. ``replay``
recomputes every sampled bound and exact cost with the placer's own functions. It
refuses (typed, parity none) an unsupported or unknown objective or generation and a
malformed, stale, duplicated or unreconciled payload; it never takes completeness
from the coverage label. A capped sample set, an incomplete run or an unknown board
or rules identity is ``partial`` and never qualifies complete parity. The trace
overhead gate (provisional <= 5% median, measured to export) is an experiment gate,
not a physical constraint; see docs/placement-trace.md.
"""
import argparse
import contextlib
import contextvars
import copy
from fractions import Fraction
import functools
import json
import re
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from . import trace_envelope

PAYLOAD = 'agent-ee.placement-trace/1'
REPLAY = 'agent-ee.placement-trace-replay/1'
CONSUMER = {'id': 'agent-ee.placement', 'version': '1'}
OBJECTIVE = {'id': 'agent-ee.placer.wire-cost', 'version': '1',
             'definition': 'per nonground signal pad, integer Manhattan nm to the nearest placed same-net pad'}
GENERATION = 'agent-ee.placer.search-stack/1'
LIMITS = {'requests': 256, 'samples': 8192, 'bytes': 8 * 1024 * 1024}
ROTATIONS = (0, 90, 180, 270)
# A complete replay is bound to the board and rules it scored; unknown ones leave it partial.
QUALIFYING_IDENTITIES = ('pcb', 'rules')
LOCAL_COST = re.compile(r'^-?[0-9]+/[0-9]+$')
SAMPLE_FIELDS = ('request', 'generation', 'x_nm', 'y_nm', 'rotation_deg', 'local_cost', 'bound_nm', 'cost_nm')
# name, parent, boundary, overlap, within: the static duration tree every trace reports.
DURATIONS = (
    ('pipeline', None, 'inclusive', 'disjoint', ()),
    ('prep', 'pipeline', 'exclusive', 'nested', ('stage_b.arrange',)),
    ('stage_a.search', 'pipeline', 'inclusive', 'nested', ('stage_b.arrange',)),
    ('stage_a.geometry', 'stage_a.search', 'exclusive', 'disjoint', ()),
    ('stage_a.bounds', 'stage_a.search', 'exclusive', 'disjoint', ()),
    ('stage_a.exact', 'stage_a.search', 'exclusive', 'disjoint', ()),
    ('stage_a.rank', 'stage_a.search', 'exclusive', 'disjoint', ()),
    ('stage_a.predicates', 'stage_a.search', 'exclusive', 'disjoint', ()),
    ('stage_a.judge', 'pipeline', 'exclusive', 'nested', ('stage_b.arrange',)),
    ('stage_b.arrange', 'pipeline', 'inclusive', 'disjoint', ()),
    ('stage_b.search', 'stage_b.arrange', 'exclusive', 'disjoint', ()),
    ('native_checks', 'pipeline', 'exclusive', 'disjoint', ()),
    ('export', 'pipeline', 'exclusive', 'disjoint', ()),
)
COUNTERS = ('stage_a.searches', 'stage_a.scans', 'stage_a.candidates_generated', 'stage_a.exact_resolved',
            'stage_a.steps', 'stage_a.doomed', 'stage_a.admitted', 'stage_a.rejected', 'stage_a.generations',
            'stage_a.stale_samples', 'stage_b.arrangements', 'stage_b.steps')
SOURCES = ('placer.py', 'block_arrangement.py', 'placer_runner.py', 'placement_trace.py', 'trace_envelope.py')

now = time.perf_counter_ns  # Monotonic, nanoseconds.
_ACTIVE = contextvars.ContextVar('agent_ee_placement_trace', default=None)


def active():
    """The current recorder, or None when tracing is off (the default)."""
    return _ACTIVE.get()


def phase(name):
    """Switch the runner's current exclusive phase (None stops it); a no-op when tracing is off."""
    recorder = _ACTIVE.get()
    if recorder is not None:
        recorder.phase(name)


def exact_number(value):
    """An int as itself; an exact non-integral Fraction (locality cost) as the string 'p/q'."""
    if isinstance(value, int):
        return value
    if isinstance(value, Fraction):
        return value.numerator if value.denominator == 1 else f'{value.numerator}/{value.denominator}'
    raise TypeError(f'trace sample values must be exact, not {value.__class__.__name__}')


# The serialized payload sample set with nothing recorded: the minimum legal byte limit.
FRAME_BYTES = len('{"requests":[],"samples":[]}')
# Sized in place of a request's generated-candidate count, which its scan sets after
# recording it: the byte account is an upper bound of the serialized size.
_GENERATED_RESERVE = 10 ** 19


def _compact(value):
    return json.dumps(value, separators=(',', ':'))


class Recorder:
    def __init__(self, limits=None):
        self.limits = {**LIMITS, **(limits or {})}
        if set(self.limits) != set(LIMITS) or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
                                                      for v in self.limits.values()):
            raise ValueError(f'trace limits must be non-negative integers for {", ".join(LIMITS)}')
        if self.limits['bytes'] < FRAME_BYTES:
            raise ValueError(f'trace byte limit must be at least {FRAME_BYTES}, the empty sample set')
        self.durations = {name: [0, 0] for name, *_ in DURATIONS}
        self.counters = dict.fromkeys(COUNTERS, 0)
        self.searches, self.requests, self.samples = [], [], []
        self.requests_dropped = self.samples_dropped = 0
        self.byte_dropped_requests = self.byte_dropped_samples = 0
        # Bytes retained so far, an upper bound of the serialized sample set; never above the limit.
        self.stored = FRAME_BYTES
        self.started = self.stopped = None
        self._phase = None

    def add(self, name, ns, count=1):
        row = self.durations[name]
        row[0] += ns
        row[1] += count

    def phase(self, name):
        t = now()
        if self._phase is not None:
            self.add(self._phase[0], t - self._phase[1])
        self._phase = None if name is None else (name, t)

    def search_started(self, search, *, joint, copper_axes):
        self.counters['stage_a.searches'] += 1
        self.searches.append((search, {'index': len(self.searches), 'joint': bool(joint),
                                       'copper_axes': bool(copper_axes)}))
        return len(self.searches) - 1

    def scan_request(self, search_index, generation, fp, grounds, peers, pad_nets):
        """Record one scan's Stage-A scoring inputs; None once a request or byte limit is reached.

        The request is sized before its peers are copied: nothing over the byte limit is retained.
        """
        self.counters['stage_a.scans'] += 1
        if len(self.requests) >= self.limits['requests']:
            self.requests_dropped += 1
            return None
        pads = {str(r): [list(p) for p in pad_nets(fp, (0, 0), r, grounds)] for r in ROTATIONS}
        nets = sorted({p[0] for rows in pads.values() for p in rows if p[0] in peers})
        request = {'id': len(self.requests), 'search': search_index, 'generation': generation,
                   'ref': fp['ref'], 'uuid': fp.get('uuid'), 'generated': _GENERATED_RESERVE,
                   'inputs_sha256': '0' * 64}
        # Merged with its inputs object and followed by a separator, the request is the two sizes summed.
        meta = len(_compact(request))
        remaining = self.limits['bytes'] - self.stored - meta
        # Every peer point serializes to at least 6 bytes ('[0,0],', one fewer for a net's last):
        # a request whose points cannot fit is dropped from its point count, before any is read.
        if 6 * sum(len(peers[net]) for net in nets) - len(nets) > remaining:
            self.byte_dropped_requests += 1
            return None
        subset = {net: peers[net] for net in nets}
        inputs = json.dumps({'pads': pads, 'peers': subset}, sort_keys=True, separators=(',', ':'))
        if len(inputs) > remaining:
            self.byte_dropped_requests += 1
            return None
        self.stored += meta + len(inputs)
        # A private snapshot, hashed as trace_envelope.canonical_sha256 hashes it.
        request.update(pads=pads, peers={net: [list(p) for p in points] for net, points in subset.items()},
                       generated=0, inputs_sha256=trace_envelope.sha256_bytes(inputs.encode()))
        self.requests.append(request)
        return request

    def sample(self, request, scan_generation, generation, key, cost):
        """One exact evaluation; ``generation`` is the search's at evaluation, ``scan_generation`` at scan start."""
        if generation != scan_generation:
            self.counters['stage_a.stale_samples'] += 1
        if request is None or len(self.samples) >= self.limits['samples']:
            self.samples_dropped += 1
            return
        local_cost, bound, x, y, rotation, _ = key
        row = [request['id'], generation, x, y, rotation, exact_number(local_cost), bound, cost]
        # Integers and one optional 'p/q' string: the compact JSON size is repr's less its 7 ', ' spaces,
        # plus the separator.
        size = len(repr(row)) - 6
        if self.stored + size > self.limits['bytes']:
            self.byte_dropped_samples += 1
            return
        self.stored += size
        self.samples.append(row)

    def timed_rank(self, ranked):
        """``ranked`` re-yielded, timing the heap work exclusive of exact cost resolution."""
        exact = self.durations['stage_a.exact']
        while True:
            t, before = now(), exact[0]
            try:
                key = next(ranked)
            except StopIteration:
                self.add('stage_a.rank', now() - t - (exact[0] - before))
                return
            self.add('stage_a.rank', now() - t - (exact[0] - before))
            yield key

    def finish(self):
        self.phase(None)
        for search, record in self.searches:
            record['steps'] = search.steps
            record['generations'] = search.generations_allocated
            self.counters['stage_a.steps'] += search.steps
            self.counters['stage_a.generations'] += search.generations_allocated
        self.counters['stage_a.exact_resolved'] = self.durations['stage_a.exact'][1]
        self.stopped = now()
        self.add('pipeline', self.stopped - self.started)

    def bounded(self):
        """Recorded requests and samples, what was dropped and why, and their serialized size.

        Recording kept them within the byte limit; ``stored`` is their exact canonical size.
        """
        stored = len(json.dumps({'requests': self.requests, 'samples': self.samples}, sort_keys=True,
                                separators=(',', ':')))
        reasons = []
        if self.requests_dropped:
            reasons.append(f'request limit {self.limits["requests"]} reached: {self.requests_dropped} scans unsampled')
        if self.samples_dropped:
            reasons.append(f'sample limit {self.limits["samples"]} or unsampled scan: '
                           f'{self.samples_dropped} exact evaluations unsampled')
        if self.byte_dropped_requests or self.byte_dropped_samples:
            reasons.append(f'byte limit {self.limits["bytes"]} reached: {self.byte_dropped_requests} requests and '
                           f'{self.byte_dropped_samples} samples dropped')
        dropped = (self.requests_dropped + self.samples_dropped + self.byte_dropped_requests
                   + self.byte_dropped_samples)
        return self.requests, self.samples, dropped, reasons, stored


@contextlib.contextmanager
def recording(limits=None):
    """Turn tracing on for the enclosed placement call; yields the Recorder."""
    if _ACTIVE.get() is not None:
        raise ValueError('a placement trace is already recording in this context')
    recorder = Recorder(limits)
    token = _ACTIVE.set(recorder)
    recorder.started = now()
    try:
        yield recorder
    finally:
        _ACTIVE.reset(token)
        recorder.finish()


@functools.cache
def _source_hashes():
    root = Path(__file__).resolve().parent
    return tuple(trace_envelope.file_hash('agent_ee/' + name, root / name) for name in SOURCES)


@functools.cache
def _binary_hashes():
    return (trace_envelope.file_hash('python', sys.executable) if sys.executable else
            {'name': 'python', 'status': 'unknown', 'reason': 'interpreter path not reported'},)


def native_tool_hashes():
    """kicad-cli and the pcbnew module as loaded, each hashed or an explicit unknown."""
    rows = []
    cli = shutil.which('kicad-cli')
    rows.append(trace_envelope.file_hash('kicad-cli', cli) if cli else
                {'name': 'kicad-cli', 'status': 'unknown', 'reason': 'kicad-cli not on PATH'})
    module = sys.modules.get('pcbnew')
    path = getattr(module, '__file__', None)
    rows.append(trace_envelope.file_hash('pcbnew', path) if path else
                {'name': 'pcbnew', 'status': 'unknown', 'reason': 'pcbnew module not loaded'})
    native = sys.modules.get('_pcbnew')
    path = getattr(native, '__file__', None)
    if path:
        rows.append(trace_envelope.file_hash('_pcbnew', path))
    return rows


def envelope(recorder, *, stage, identity, state, native_tools=(), outcome=None):
    """The ``pcb-trace-envelope/1`` record for a finished recorder."""
    requests, samples, dropped, reasons, stored = recorder.bounded()
    if state['status'] != 'completed':
        reasons = reasons + ['traced run did not complete']
    durations = [{'name': name, 'unit': 'ns', 'value': recorder.durations[name][0],
                  'count': recorder.durations[name][1], 'boundary': boundary, 'overlap': overlap,
                  'parent': parent, **({'within': list(within)} if within else {})}
                 for name, parent, boundary, overlap, within in DURATIONS]
    counters = dict(recorder.counters)
    counters['stage_a.unresolved'] = counters['stage_a.candidates_generated'] - counters['stage_a.exact_resolved']
    document = {
        'schema': trace_envelope.SCHEMA, 'consumer': dict(CONSUMER), 'stage': {'id': stage},
        'identity': identity,
        'hashes': {'source': [dict(row) for row in _source_hashes()], 'binary': [dict(row) for row in _binary_hashes()],
                   'native_tools': list(native_tools)},
        'state': {**state, **({'outcome': outcome} if outcome is not None else {})},
        'generation': {'id': GENERATION, 'allocated': counters['stage_a.generations'],
                       'searches': [record for _, record in recorder.searches]},
        'objective': dict(OBJECTIVE),
        'clock': {'source': 'monotonic', 'unit': 'ns', 'function': 'time.perf_counter_ns'},
        'durations': durations, 'counters': counters,
        'sampling': {'limits': dict(recorder.limits), 'recorded': len(requests) + len(samples), 'bytes': stored,
                     'dropped': dropped, 'coverage': 'partial' if reasons else 'complete',
                     'partial_reasons': reasons},
        'payload': {'schema': PAYLOAD, 'sample_fields': list(SAMPLE_FIELDS), 'requests': requests,
                    'samples': samples,
                    'reconciliation': reconcile(counters)}}
    return trace_envelope.check(document)


def reconcile(counters):
    """Counter identities every complete Stage-A trace satisfies."""
    return {'steps': counters['stage_a.steps'] == counters['stage_a.doomed'] + counters['stage_a.admitted']
            + counters['stage_a.rejected'],
            'candidates': counters['stage_a.candidates_generated'] >= counters['stage_a.exact_resolved']}


def identity_of(value, reason):
    return trace_envelope.qualified(value) if isinstance(value, str) and trace_envelope.SHA256.match(value) \
        else trace_envelope.unknown(reason)


def pack_identity(capture, policy, plan, experiment=None):
    return {'pcb': identity_of(capture.get('board_sha256'), 'capture board_sha256 is not a sha256'),
            'project': trace_envelope.unknown('standalone placement capture carries no project file'),
            'rules': trace_envelope.qualified(trace_envelope.canonical_sha256(policy), kind=policy.get('schema')),
            'experiment': (trace_envelope.qualified(id=experiment) if experiment
                           else trace_envelope.unknown('no experiment declared')),
            'capture': trace_envelope.qualified(trace_envelope.canonical_sha256(capture)),
            'candidate': (trace_envelope.qualified(trace_envelope.canonical_sha256(plan), kind='plan')
                          if plan.get('status') == 'planned'
                          else trace_envelope.unknown(f'no planned candidate: {plan.get("status")}'))}


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _rows(value, width, first=_integer):
    """Whether ``value`` is a list of ``width``-item rows: ``first`` then integers."""
    return isinstance(value, list) and all(
        isinstance(row, list) and len(row) == width and first(row[0]) and all(_integer(v) for v in row[1:])
        for row in value)


def contract_problems(document):
    """Why this reader cannot replay a (valid) envelope at all: an unsupported consumer, payload,
    objective or generation contract. Unknown is never assumed to be the supported contract."""
    problems = []
    consumer, payload = document['consumer'], document['payload']
    if (consumer.get('id'), consumer.get('version')) != (CONSUMER['id'], CONSUMER['version']):
        problems.append(f'consumer {consumer.get("id")}/{consumer.get("version")} is not '
                        f'{CONSUMER["id"]}/{CONSUMER["version"]}')
    if payload.get('schema') != PAYLOAD or payload.get('sample_fields') != list(SAMPLE_FIELDS):
        problems.append(f'not an {PAYLOAD} payload')
    for key, supported in (('objective', (OBJECTIVE['id'], OBJECTIVE['version'])), ('generation', (GENERATION,))):
        value = document[key]
        declared = (value.get('id'), value.get('version')) if key == 'objective' else (value.get('id'),)
        if value.get('status') == 'unknown':
            problems.append(f'{key} is unknown ({value.get("reason")}); replay needs the declared '
                            f'{"/".join(supported)} contract')
        elif declared != supported:
            problems.append(f'{key} {"/".join(map(str, declared))} is not the supported {"/".join(supported)}')
    return problems


def payload_problems(document):
    """Malformed or unreconciled ``agent-ee.placement-trace/1`` data in a valid envelope.

    Requests and samples must be well formed, unique and bound to recorded searches and
    generations; the sampling record must count and size exactly what the payload holds;
    and every scan and exact evaluation the counters report must be either recorded or
    accounted as dropped. A coverage label alone never certifies completeness.
    """
    payload, sampling, counters = document['payload'], document['sampling'], document['counters']
    requests, samples, searches = payload.get('requests'), payload.get('samples'), document['generation'].get('searches')
    if not isinstance(requests, list) or not isinstance(samples, list):
        return ['payload requests and samples must be lists']
    if not isinstance(searches, list) or not all(isinstance(s, dict) and _integer(s.get('generations'))
                                                 and s['generations'] >= 0 for s in searches):
        return ['generation.searches must list each search with its allocated generations']
    missing = [name for name in COUNTERS + ('stage_a.unresolved',) if name not in counters]
    if missing:
        return [f'counters missing {", ".join(missing)}']
    problems = []
    for i, request in enumerate(requests):
        label = f'request {i}'
        if not isinstance(request, dict):
            problems.append(f'{label}: not an object')
            continue
        search, generation = request.get('search'), request.get('generation')
        if not _integer(request.get('id')) or request['id'] != i:
            problems.append(f'{label}: id must be its recording position {i}')
        if not (_integer(search) and 0 <= search < len(searches)):
            problems.append(f'{label}: search {search!r} is not a recorded search')
        elif not (_integer(generation) and 0 <= generation <= searches[search]['generations']):
            problems.append(f'{label}: generation {generation!r} was never allocated by search {search}')
        if not (isinstance(request.get('ref'), str) and request['ref']) or not (
                request.get('uuid') is None or isinstance(request['uuid'], str)):
            problems.append(f'{label}: ref and uuid must be strings')
        if not _integer(request.get('generated')) or request['generated'] < 0:
            problems.append(f'{label}: generated must be a non-negative integer')
        pads, peers = request.get('pads'), request.get('peers')
        if not (isinstance(pads, dict) and set(pads) == {str(r) for r in ROTATIONS}
                and all(_rows(rows, 3, lambda net: isinstance(net, str)) for rows in pads.values())):
            problems.append(f'{label}: pads must give [net, x, y] rows for each of {", ".join(map(str, ROTATIONS))}')
            continue
        nets = {row[0] for rows in pads.values() for row in rows}
        if not (isinstance(peers, dict) and set(peers) <= nets and all(points and _rows(points, 2)
                                                                       for points in peers.values())):
            problems.append(f'{label}: peers must map its pad nets to [x, y] points')
            continue
        if request.get('inputs_sha256') != trace_envelope.canonical_sha256({'pads': pads, 'peers': peers}):
            problems.append(f'trace request {i} inputs differ from their recorded hash')
    if problems:
        return problems
    seen, per_request = set(), [0] * len(requests)
    for i, sample in enumerate(samples):
        if not (isinstance(sample, list) and len(sample) == len(SAMPLE_FIELDS)
                and all(_integer(v) for v in sample[:5] + sample[6:])
                and (_integer(sample[5]) or isinstance(sample[5], str) and LOCAL_COST.match(sample[5]))):
            problems.append(f'sample {i}: must be integer {", ".join(SAMPLE_FIELDS)} (local_cost may be "p/q")')
            continue
        rid, generation, x, y, rotation, _, bound, cost = sample
        if rotation not in ROTATIONS:
            problems.append(f'sample {i}: rotation {rotation} is not one of {", ".join(map(str, ROTATIONS))}')
        if bound < 0 or cost < 0:
            problems.append(f'sample {i}: bound and cost must be non-negative')
        if not 0 <= rid < len(requests):
            problems.append(f'sample {i} names unrecorded request {rid}')
            continue
        if generation != requests[rid]['generation']:
            problems.append(f'sample {i} generation {generation} is stale for request {rid} '
                            f'at generation {requests[rid]["generation"]}')
        if (rid, x, y, rotation) in seen:
            problems.append(f'sample {i}: duplicates request {rid} pose {[x, y, rotation]}')
        seen.add((rid, x, y, rotation))
        per_request[rid] += 1
    problems += [f'request {rid}: {n} samples exceed its {requests[rid]["generated"]} generated candidates'
                 for rid, n in enumerate(per_request) if n > requests[rid]['generated']]
    limits = sampling['limits']
    if set(limits) != set(LIMITS):
        problems.append(f'sampling.limits must name exactly {", ".join(LIMITS)}')
    elif len(requests) > limits['requests'] or len(samples) > limits['samples']:
        problems.append('payload holds more requests or samples than its limits')
    if sampling['recorded'] != len(requests) + len(samples):
        problems.append(f'sampling.recorded {sampling["recorded"]} is not the {len(requests) + len(samples)} '
                        f'recorded requests and samples')
    if sampling['bytes'] != len(_canonical({'requests': requests, 'samples': samples})):
        problems.append('sampling.bytes is not the serialized size of the recorded requests and samples')
    unsampled = (counters['stage_a.scans'] - len(requests)) + (counters['stage_a.exact_resolved'] - len(samples))
    if len(requests) > counters['stage_a.scans'] or len(samples) > counters['stage_a.exact_resolved'] \
            or sum(r['generated'] for r in requests) > counters['stage_a.candidates_generated']:
        problems.append('payload records more scans, samples or candidates than the counters')
    elif sampling['dropped'] != unsampled:
        problems.append(f'sampling.dropped {sampling["dropped"]} does not account for the {unsampled} scans and '
                        f'exact evaluations the counters report but the payload does not hold')
    if counters['stage_a.unresolved'] != counters['stage_a.candidates_generated'] - counters['stage_a.exact_resolved']:
        problems.append('counters: unresolved is not generated minus exact-resolved')
    exact = next((row for row in document['durations'] if row.get('name') == 'stage_a.exact'), None)
    if exact is None or exact.get('count') != counters['stage_a.exact_resolved']:
        problems.append('durations: stage_a.exact count is not the exact-resolved counter')
    if len(searches) != counters['stage_a.searches'] or not (
            sum(s['generations'] for s in searches) == document['generation'].get('allocated')
            == counters['stage_a.generations']):
        problems.append('generation searches and allocations do not match the counters')
    if payload.get('reconciliation') != reconcile(counters) or not all(reconcile(counters).values()):
        problems.append('counters do not reconcile')
    return problems


def qualification_gaps(document):
    """Why a replay that agrees cannot be complete-parity evidence: an incomplete run, or an
    unknown identity the comparison is bound to. The trace stays retained and replayable."""
    gaps = []
    state = document['state']
    if state['status'] != 'completed':
        gaps.append(f'traced run {state["status"]}: {state.get("reason")}')
    for key in QUALIFYING_IDENTITIES:
        value = document['identity'][key]
        if value['status'] != 'qualified':
            gaps.append(f'identity.{key} unknown: {value.get("reason")}')
    return gaps


def stage_a_inputs(document):
    """The Stage-A scoring inputs of a trace, per sampled scan, for an independent scorer (G1).

    Raises ValueError on an invalid envelope, an unsupported or unknown contract, or a
    malformed, tampered, stale, duplicated or unreconciled payload.
    """
    trace_envelope.check(document)
    problems = contract_problems(document)
    problems = problems or payload_problems(document)
    if problems:
        raise ValueError('; '.join(problems[:10]) + (f'; and {len(problems) - 10} more' if len(problems) > 10 else ''))
    payload = document['payload']
    out = [{'request': request['id'], 'ref': request['ref'], 'generation': request['generation'],
            'objective': document['objective'], 'pads': request['pads'], 'peers': request['peers'], 'queries': [],
            'expected_bound_nm': [], 'expected_cost_nm': []} for request in payload['requests']]
    for rid, _, x, y, rotation, _, bound, cost in payload['samples']:
        out[rid]['queries'].append([x, y, rotation])
        out[rid]['expected_bound_nm'].append(bound)
        out[rid]['expected_cost_nm'].append(cost)
    return out


def replay(document):
    """Recompute every sampled bound and exact cost with the placer; a typed report.

    Invalid, unsupported, unknown or unreconciled evidence is ``refused`` with parity
    ``none``; agreement is ``complete`` only for a completed, fully sampled run bound to
    qualified board and rules identities, and ``partial`` otherwise.
    """
    from .placer import _wire_bounds, _wire_cost
    try:
        inputs = stage_a_inputs(document)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return {'schema': REPLAY, 'status': 'refused', 'parity': 'none', 'reason': str(exc)}
    checked, mismatches = 0, []
    for entry in inputs:
        peers = {net: [tuple(p) for p in points] for net, points in entry['peers'].items()}
        for (x, y, rotation), bound, cost in zip(entry['queries'], entry['expected_bound_nm'],
                                                 entry['expected_cost_nm']):
            signal = [tuple(p) for p in entry['pads'][str(rotation)]]
            replayed = _wire_cost([(net, ox + x, oy + y) for net, ox, oy in signal], peers)
            bx, by = _wire_bounds(signal, peers, [x], [y])
            checked += 1
            if replayed != cost or bx[x] + by[y] != bound or bound > cost:
                mismatches.append({'request': entry['request'], 'pose': [x, y, rotation], 'cost_nm': cost,
                                   'replayed_cost_nm': replayed, 'bound_nm': bound,
                                   'replayed_bound_nm': bx[x] + by[y]})
    reasons = document['sampling']['partial_reasons'] + qualification_gaps(document)
    complete = trace_envelope.complete_parity_allowed(document) and not reasons
    return {'schema': REPLAY, 'status': 'mismatch' if mismatches else 'exact',
            'parity': 'none' if mismatches else ('complete' if complete else 'partial'),
            'requests': len(inputs), 'samples_checked': checked, 'mismatches': mismatches[:20],
            'mismatch_count': len(mismatches), 'partial_reasons': reasons}


def traced_pack(capture, policy, *, limits=None, experiment=None):
    """``placer.pack`` under a trace: (plan, envelope)."""
    from .placer import pack
    with recording(limits) as recorder:
        plan = pack(capture, policy)
    return plan, envelope(recorder, stage='pack', identity=pack_identity(capture, policy, plan, experiment),
                          state={'status': 'completed'}, outcome=plan.get('status'))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def export(document, path):
    """Write a trace to a fresh ``path`` as canonical JSON; the number of bytes written."""
    data = (_canonical(document) + '\n').encode()
    with Path(path).open('xb') as stream:
        stream.write(data)
    return len(data)


def overhead(capture, policy, *, runs=5, warmups=1, limits=None):
    """Matched off/on ``pack`` timings: alternating order, median overhead and plan identity.

    An enabled run ends when its trace is on disk: recording, envelope assembly and
    export to a fresh file are all inside the measured boundary.
    """
    from .placer import pack
    if runs < 1 or warmups < 0:
        raise ValueError('runs must be positive and warmups non-negative')
    reference = _canonical(pack(copy.deepcopy(capture), policy))
    off, on, recorded, exported, identical, trace_bytes = [], [], [], [], True, []
    with tempfile.TemporaryDirectory(prefix='agent-ee-trace-overhead-') as tmp:
        for i in range(warmups):
            pack(copy.deepcopy(capture), policy)
            _, document = traced_pack(copy.deepcopy(capture), policy, limits=limits)
            export(document, Path(tmp) / f'warmup-{i}.json')
        for i in range(runs):
            order = ('off', 'on') if i % 2 == 0 else ('on', 'off')
            for mode in order:
                work = copy.deepcopy(capture)
                t = now()
                if mode == 'off':
                    plan = pack(work, policy)
                    off.append(now() - t)
                else:
                    with recording(limits) as recorder:
                        plan = pack(work, policy)
                    t1 = now()
                    document = envelope(recorder, stage='pack', identity=pack_identity(capture, policy, plan),
                                        state={'status': 'completed'}, outcome=plan.get('status'))
                    written = export(document, Path(tmp) / f'trace-{i}.json')
                    t2 = now()
                    on.append(t2 - t)
                    recorded.append(t1 - t)
                    exported.append(t2 - t1)
                    trace_bytes.append(written)
                identical = identical and _canonical(plan) == reference
    median_off, median_on, median_recorded = statistics.median(off), statistics.median(on), statistics.median(recorded)
    overhead_ratio = (median_on - median_off) / median_off if median_off else None
    return {'schema': 'agent-ee.placement-trace-overhead/2', 'runs': runs, 'warmups': warmups,
            'unit': 'ns', 'off_ns': off, 'on_ns': on, 'recording_ns': recorded, 'export_ns': exported,
            'median_off_ns': median_off, 'median_on_ns': median_on, 'median_recording_ns': median_recorded,
            'median_export_ns': statistics.median(exported),
            'median_overhead': overhead_ratio,
            'median_recording_overhead': (median_recorded - median_off) / median_off if median_off else None,
            'outputs_identical': identical, 'trace_bytes_max': max(trace_bytes),
            'gate': {'kind': 'experiment', 'provisional_max_median_overhead': 0.05,
                     'met': overhead_ratio is not None and overhead_ratio <= 0.05,
                     'measures': 'median_overhead: placement with the trace recorded, assembled and exported '
                                 'to a fresh file, versus tracing off, alternating matched runs; '
                                 'median_recording_overhead is the recording alone'}}


def _read(path):
    return json.loads(Path(path).read_text())


def _write_fresh(path, value):
    with Path(path).open('x') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + '\n')


def _limits(args):
    return {'requests': args.max_requests, 'samples': args.max_samples, 'bytes': args.max_bytes}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('pack', 'overhead'):
        command = commands.add_parser(name)
        command.add_argument('--capture', required=True, type=Path, help='placement-probe capture JSON')
        command.add_argument('--policy', required=True, type=Path, help='agent-ee.packing-policy/1 JSON')
        command.add_argument('--max-requests', type=int, default=LIMITS['requests'])
        command.add_argument('--max-samples', type=int, default=LIMITS['samples'])
        command.add_argument('--max-bytes', type=int, default=LIMITS['bytes'])
    traced = commands.choices['pack']
    traced.add_argument('--trace', required=True, type=Path, help='fresh trace output path')
    traced.add_argument('--plan', type=Path, help='fresh plan output path')
    traced.add_argument('--experiment', help='declared experiment identifier')
    measured = commands.choices['overhead']
    measured.add_argument('--runs', type=int, default=5)
    measured.add_argument('--warmups', type=int, default=1)
    replayed = commands.add_parser('replay')
    replayed.add_argument('trace', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'replay':
        report = replay(_read(args.trace))
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report['status'] == 'exact' else 1
    capture, policy = _read(args.capture), _read(args.policy)
    if args.command == 'overhead':
        print(json.dumps(overhead(capture, policy, runs=args.runs, warmups=args.warmups, limits=_limits(args)),
                         indent=2, sort_keys=True))
        return 0
    plan, document = traced_pack(capture, policy, limits=_limits(args), experiment=args.experiment)
    export(document, args.trace)
    if args.plan:
        _write_fresh(args.plan, plan)
    return 0 if plan['status'] == 'planned' else 1


if __name__ == '__main__':
    # Run as the imported module: the placer reads that module's context variable, not __main__'s.
    from agent_ee import placement_trace
    raise SystemExit(placement_trace.main())

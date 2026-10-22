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

``replay`` recomputes every sampled bound and exact cost with the placer's own
functions and rejects samples whose generation is not their scan's. A capped or
byte-limited sample set is ``partial`` and never qualifies complete parity. The
trace overhead gate (provisional <= 5% median) is an experiment gate, not a
physical constraint; see docs/placement-trace.md.
"""
import argparse
import contextlib
import contextvars
import copy
from fractions import Fraction
import functools
import json
import shutil
import statistics
import sys
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


class Recorder:
    def __init__(self, limits=None):
        self.limits = {**LIMITS, **(limits or {})}
        if set(self.limits) != set(LIMITS) or not all(isinstance(v, int) and v >= 0 for v in self.limits.values()):
            raise ValueError(f'trace limits must be non-negative integers for {", ".join(LIMITS)}')
        self.durations = {name: [0, 0] for name, *_ in DURATIONS}
        self.counters = dict.fromkeys(COUNTERS, 0)
        self.searches, self.requests, self.samples = [], [], []
        self.requests_dropped = self.samples_dropped = 0
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
        """Record one scan's Stage-A scoring inputs; None once the request limit is reached."""
        self.counters['stage_a.scans'] += 1
        if len(self.requests) >= self.limits['requests']:
            self.requests_dropped += 1
            return None
        pads = {str(r): [list(p) for p in pad_nets(fp, (0, 0), r, grounds)] for r in (0, 90, 180, 270)}
        nets = sorted({p[0] for rows in pads.values() for p in rows if p[0] in peers})
        request = {'id': len(self.requests), 'search': search_index, 'generation': generation,
                   'ref': fp['ref'], 'uuid': fp.get('uuid'), 'pads': pads,
                   'peers': {net: [list(p) for p in peers[net]] for net in nets}, 'generated': 0}
        self.requests.append(request)  # A private snapshot; its inputs hash is computed by bounded().
        return request

    def sample(self, request, scan_generation, generation, key, cost):
        """One exact evaluation; ``generation`` is the search's at evaluation, ``scan_generation`` at scan start."""
        if generation != scan_generation:
            self.counters['stage_a.stale_samples'] += 1
        if request is None or len(self.samples) >= self.limits['samples']:
            self.samples_dropped += 1
            return
        local_cost, bound, x, y, rotation, _ = key
        self.samples.append([request['id'], generation, x, y, rotation, exact_number(local_cost), bound, cost])

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
        """Requests and samples kept within the byte limit, in recording order, and what was dropped."""
        for request in self.requests:
            if 'inputs_sha256' not in request:
                request['inputs_sha256'] = trace_envelope.canonical_sha256(
                    {'pads': request['pads'], 'peers': request['peers']})
        limit, reasons = self.limits['bytes'], []
        stored = len(json.dumps({'requests': self.requests, 'samples': self.samples}, sort_keys=True,
                                separators=(',', ':')))
        if stored <= limit:
            requests, samples, byte_dropped_requests, byte_dropped_samples = self.requests, self.samples, 0, 0
        else:
            requests, samples, byte_dropped_requests, byte_dropped_samples = self._within(limit)
            stored = len(json.dumps({'requests': requests, 'samples': samples}, sort_keys=True,
                                    separators=(',', ':')))
        if self.requests_dropped:
            reasons.append(f'request limit {self.limits["requests"]} reached: {self.requests_dropped} scans unsampled')
        if self.samples_dropped:
            reasons.append(f'sample limit {self.limits["samples"]} or unsampled scan: '
                           f'{self.samples_dropped} exact evaluations unsampled')
        if byte_dropped_requests or byte_dropped_samples:
            reasons.append(f'byte limit {limit} reached: {byte_dropped_requests} requests and '
                           f'{byte_dropped_samples} samples dropped')
        dropped = self.requests_dropped + self.samples_dropped + byte_dropped_requests + byte_dropped_samples
        return requests, samples, dropped, reasons, stored

    def _within(self, limit):
        """Requests, then their samples, in recording order while the serialized total stays within ``limit``."""
        used, kept, samples, requests = len('{"requests":[],"samples":[]}'), set(), [], []
        for request in self.requests:
            size = len(json.dumps(request, sort_keys=True, separators=(',', ':'))) + 1
            if used + size <= limit:
                used += size
                requests.append(request)
                kept.add(request['id'])
        byte_dropped_samples = 0
        for sample in self.samples:
            size = len(json.dumps(sample, separators=(',', ':'))) + 1
            if sample[0] in kept and used + size <= limit:
                used += size
                samples.append(sample)
            else:
                byte_dropped_samples += 1
        return requests, samples, len(self.requests) - len(requests), byte_dropped_samples


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
        'hashes': {'source': copy.deepcopy(list(_source_hashes())), 'binary': copy.deepcopy(list(_binary_hashes())),
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


def stage_a_inputs(document):
    """The Stage-A scoring inputs of a trace, per sampled scan, for an independent scorer (G1).

    Raises on an invalid envelope, a tampered request or a stale sample generation.
    """
    trace_envelope.check(document)
    payload = document['payload']
    if payload.get('schema') != PAYLOAD or payload.get('sample_fields') != list(SAMPLE_FIELDS):
        raise ValueError(f'not an {PAYLOAD} payload')
    out = {}
    for request in payload['requests']:
        if trace_envelope.canonical_sha256({'pads': request['pads'], 'peers': request['peers']}) \
                != request['inputs_sha256']:
            raise ValueError(f'trace request {request["id"]} inputs differ from their recorded hash')
        out[request['id']] = {'request': request['id'], 'ref': request['ref'], 'generation': request['generation'],
                              'objective': document['objective'], 'pads': request['pads'],
                              'peers': request['peers'], 'queries': [], 'expected_bound_nm': [],
                              'expected_cost_nm': []}
    for i, (rid, generation, x, y, rotation, _, bound, cost) in enumerate(payload['samples']):
        if rid not in out:
            raise ValueError(f'sample {i} names unrecorded request {rid}')
        if generation != out[rid]['generation']:
            raise ValueError(f'sample {i} generation {generation} is stale for request {rid} '
                             f'at generation {out[rid]["generation"]}')
        out[rid]['queries'].append([x, y, rotation])
        out[rid]['expected_bound_nm'].append(bound)
        out[rid]['expected_cost_nm'].append(cost)
    return list(out.values())


def replay(document):
    """Recompute every sampled bound and exact cost with the placer; a typed report."""
    from .placer import _wire_bounds, _wire_cost
    try:
        inputs = stage_a_inputs(document)
    except (ValueError, KeyError, TypeError) as exc:
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
    complete = trace_envelope.complete_parity_allowed(document)
    return {'schema': REPLAY, 'status': 'mismatch' if mismatches else 'exact',
            'parity': 'none' if mismatches else ('complete' if complete else 'partial'),
            'requests': len(inputs), 'samples_checked': checked, 'mismatches': mismatches[:20],
            'mismatch_count': len(mismatches),
            'partial_reasons': document['sampling']['partial_reasons']}


def traced_pack(capture, policy, *, limits=None, experiment=None):
    """``placer.pack`` under a trace: (plan, envelope)."""
    from .placer import pack
    with recording(limits) as recorder:
        plan = pack(capture, policy)
    return plan, envelope(recorder, stage='pack', identity=pack_identity(capture, policy, plan, experiment),
                          state={'status': 'completed'}, outcome=plan.get('status'))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def overhead(capture, policy, *, runs=5, warmups=1, limits=None):
    """Matched off/on ``pack`` timings: alternating order, median overhead and plan identity."""
    from .placer import pack
    if runs < 1 or warmups < 0:
        raise ValueError('runs must be positive and warmups non-negative')
    reference = _canonical(pack(copy.deepcopy(capture), policy))
    for _ in range(warmups):
        pack(copy.deepcopy(capture), policy)
        traced_pack(copy.deepcopy(capture), policy, limits=limits)
    off, on, assembly, identical, envelope_bytes = [], [], [], True, []
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
                on.append(t1 - t)
                document = envelope(recorder, stage='pack', identity=pack_identity(capture, policy, plan),
                                    state={'status': 'completed'}, outcome=plan.get('status'))
                assembly.append(now() - t1)
                envelope_bytes.append(len(_canonical(document)))
            identical = identical and _canonical(plan) == reference
    median_off, median_on = statistics.median(off), statistics.median(on)
    median_assembly = statistics.median(assembly)
    return {'schema': 'agent-ee.placement-trace-overhead/1', 'runs': runs, 'warmups': warmups,
            'unit': 'ns', 'off_ns': off, 'on_ns': on, 'envelope_ns': assembly,
            'median_off_ns': median_off, 'median_on_ns': median_on, 'median_envelope_ns': median_assembly,
            'median_overhead': (median_on - median_off) / median_off if median_off else None,
            'median_overhead_with_envelope': ((median_on + median_assembly - median_off) / median_off
                                              if median_off else None),
            'outputs_identical': identical, 'envelope_bytes_max': max(envelope_bytes),
            'gate': {'kind': 'experiment', 'provisional_max_median_overhead': 0.05,
                     'measures': 'median_overhead: placement with recording on versus off, alternating '
                                 'matched runs; envelope assembly after the run is reported separately'}}


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
    _write_fresh(args.trace, document)
    if args.plan:
        _write_fresh(args.plan, plan)
    return 0 if plan['status'] == 'planned' else 1


if __name__ == '__main__':
    # Run as the imported module: the placer reads that module's context variable, not __main__'s.
    from agent_ee import placement_trace
    raise SystemExit(placement_trace.main())

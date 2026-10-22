#!/usr/bin/env python3
"""Matched Rust/Python scoring and placement measurements; no promotion or board acceptance.

Retains exact inputs, validated traces, raw alternating timings and parity results.
Replay timings include preparation and Python input/output work, not just a kernel.
"""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT)]
from agent_ee import placement_scoring as scoring, placement_trace, placer
from scripts.placement_trace_overhead import unit_cases, pod_cases

MAX_RUNS = 100
BOUNDARIES = {
    'reference_score': 'Python oracle over pre-captured snapshots; result lists included',
    'rust_batch_score': 'Rust preparation, batch conversion/call, result lists and release; library preloaded',
    'rust_scalar_score': 'Rust preparation, checked-generation scalar wrapper calls, result lists and release; library preloaded',
    'checked_score': 'Rust preparation, batch wrapper, full Python oracle comparison and release; library preloaded',
    'reference_placement': 'complete untraced Python pack()',
    'rust_placement': 'complete untraced Rust pack(), including context and library loading',
    'checked_placement': 'complete untraced checked Rust pack(), including full Python oracle and library loading',
}


def digest(contents):
    return hashlib.sha256(contents).hexdigest()


def write_json(path, contents):
    path.write_text(json.dumps(contents, indent=2, sort_keys=True) + '\n')


def source_identity():
    paths = ['src/agent_ee/placement_scoring.py', 'src/agent_ee/data/placement_score.rs',
             'src/agent_ee/placer.py', 'src/agent_ee/placement_trace.py',
             'scripts/placement_scoring_benchmark.py', 'scripts/placement_trace_overhead.py',
             'scripts/qualify_pod_placement.py', 'tests/test_placer_clusters.py', 'tests/test_placer_search.py']
    revision = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    return {'git_head': revision,
            'files_sha256': {name: digest((ROOT / name).read_bytes()) for name in paths}}


def environment():
    cpu = next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                if line.startswith('model name')), platform.processor())
    return {'host': platform.node(), 'platform': platform.platform(), 'machine': platform.machine(),
            'cpu_model': cpu, 'cpus': os.cpu_count(), 'cpu_affinity': sorted(os.sched_getaffinity(0)),
            'python': sys.version, 'load_average': list(os.getloadavg()),
            'unix_time_ns': time.time_ns(),
            'gc': {'enabled': gc.isenabled(), 'thresholds': list(gc.get_threshold()), 'forced_collections': False}}


def measured(call):
    started = time.perf_counter_ns()
    returned = call()
    return returned, time.perf_counter_ns() - started


def replay_arms(snapshots, library):
    native = scoring.Native(library)
    fast, checked = scoring.Session(library), scoring.Session(library, check=True)
    if fast.events or checked.events:
        raise ValueError(f'Rust replay library unavailable: {fast.events + checked.events}')

    def reference():
        return [scoring.reference(snapshot, poses) for snapshot, poses in snapshots], []

    def batch():
        results = []
        for snapshot, poses in snapshots:
            packed = native.prepare(snapshot)
            try:
                results.append(native.score(packed, poses))
            finally:
                del packed
        return results, []

    def wrapped(session, scalar):
        results = []
        for snapshot, poses in snapshots:
            scorer = session.scorer(snapshot)
            try:
                results.append([scorer.score(*pose, generation=snapshot.generation) for pose in poses]
                               if scalar else scorer.score_batch(poses, generation=snapshot.generation))
            finally:
                del scorer
        return results, list(session.events)

    return {'reference_score': reference, 'rust_batch_score': batch,
            'rust_scalar_score': lambda: wrapped(fast, True), 'checked_score': lambda: wrapped(checked, False)}


def placement_arms(source, policy, library):
    def native(check):
        with scoring.experimental(library, check=check) as session:
            plan = placer.pack(source, policy)
        return plan, list(session.events)
    return {'reference_placement': lambda: (placer.pack(source, policy), []),
            'rust_placement': lambda: native(False), 'checked_placement': lambda: native(True)}


def verify(label, returned, expected, case):
    actual, events = returned
    if events:
        raise ValueError(f'unexpected scorer rollback: {case}/{label}: {events}')
    if actual != expected:
        raise ValueError(f'parity failure: {case}/{label}')


def run_arms(name, arms, expected, out, runs):
    times, orders = {label: [] for label in arms}, []
    phase, label = 'warmup', None
    try:
        for label, call in arms.items():
            verify(label, call(), expected[label], name)
        phase = 'measurement'
        for repetition in range(runs):
            labels = list(arms) if repetition % 2 == 0 else list(reversed(arms))
            orders.append(labels)
            for label in labels:
                returned, duration = measured(arms[label])
                times[label].append(duration)
                verify(label, returned, expected[label], name)
    except Exception as exc:
        write_json(out / f'{name}.failure.json',
                   {'case': name, 'phase': phase, 'arm': label, 'raw_ns': times, 'arm_order': orders,
                    'failure': {'type': type(exc).__name__, 'message': str(exc)}})
        raise
    return times, orders


def benchmark(name, source, policy, library, out, runs=5, *, workload_kind='fixture'):
    if not 3 <= runs <= MAX_RUNS:
        raise ValueError(f'runs must be between 3 and {MAX_RUNS}')
    started = environment()
    plan, trace = placement_trace.traced_pack(source, policy)
    placement_trace.export(trace, out / f'{name}.trace.json')
    write_json(out / f'{name}.capture.json', source)
    write_json(out / f'{name}.policy.json', policy)
    rows = placement_trace.stage_a_inputs(trace)
    snapshots = [(scoring.Snapshot.capture(row['generation'], row['pads'], row['peers']), row['queries'])
                 for row in rows]
    expected_scores = [row['expected_cost_nm'] for row in rows]
    arms = {**replay_arms(snapshots, library), **placement_arms(source, policy, library)}
    expected = {label: plan if label.endswith('placement') else expected_scores for label in arms}
    times, orders = run_arms(name, arms, expected, out, runs)
    medians = {label: statistics.median(samples) for label, samples in times.items()}
    gains = {label: medians['reference_placement' if label.endswith('placement') else 'reference_score'] / ns
             for label, ns in medians.items() if not label.startswith('reference')}
    regressions = {label: [native / reference - 1 for native, reference in
                           zip(times[label], times['reference_placement'])]
                   for label in ('rust_placement', 'checked_placement')}
    return {'case': name, 'workload_kind': workload_kind, 'status': plan['status'], 'all_outputs_exact': True,
            'footprints': len(source['footprints']), 'steps_used': plan.get('steps_used'),
            'trace_coverage': trace['sampling']['coverage'], 'requests': len(rows),
            'sampled_queries': sum(len(poses) for _, poses in snapshots),
            'input_sha256': {kind: digest((out / f'{name}.{kind}.json').read_bytes())
                             for kind in ('capture', 'policy', 'trace')},
            'raw_ns': times, 'median_ns': medians, 'speedup': gains, 'arm_order': orders,
            'full_placement_regression_each': regressions,
            'environment': {'before': started, 'after': environment()},
            'scope': 'sampled trace requests for replay; complete untraced pack for placement; no physical acceptance',
            'gate': {'kind': 'measurement', 'promotion_authorized': False, 'met': False}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--library', type=Path, default=os.environ.get('AGENT_EE_PLACEMENT_SCORE_LIBRARY'))
    parser.add_argument('--out', type=Path, required=True, help='fresh output directory')
    parser.add_argument('--runs', type=int, default=5, help=f'matched repetitions, 3 to {MAX_RUNS}')
    parser.add_argument('--no-native', action='store_true', help='fixture mode: omit native-generated pod fixtures')
    parser.add_argument('--capture', type=Path, help='existing capture JSON; replaces generated fixtures')
    parser.add_argument('--policy', type=Path, help='existing packing policy JSON, required with --capture')
    args = parser.parse_args(argv)
    if not 3 <= args.runs <= MAX_RUNS:
        parser.error(f'--runs must be between 3 and {MAX_RUNS}')
    if (args.capture is None) != (args.policy is None):
        parser.error('--capture and --policy are required together')
    args.out.mkdir(parents=True, exist_ok=False)
    library = Path(args.library).resolve() if args.library else scoring.build(args.out / 'placement_score.so')
    report = {'schema': 'agent-ee.placement-scoring-benchmark/1', 'status': 'running',
              'runs': args.runs, 'warmups': 1, 'boundaries': BOUNDARIES,
              'replay_setup_excluded': 'trace generation/validation and immutable snapshot capture; library loading',
              'source': source_identity(), 'library': {'path': str(library), 'sha256': digest(library.read_bytes())},
              'environment_before': environment(), 'cases': [],
              'gate': {'kind': 'measurement', 'promotion_authorized': False, 'met': False,
                       'g0_workloads_complete': not args.no_native and args.capture is None},
              'exit_status': 'zero means all measured scores and plans matched with no rollback; no promotion'}
    try:
        with tempfile.TemporaryDirectory(prefix='agent-ee-rust-benchmark-') as scratch:
            if args.capture:
                cases = [('supplied-capture', json.loads(args.capture.read_bytes()), json.loads(args.policy.read_bytes()))]
                report['supplied_inputs'] = {kind: {'path': str(path.resolve()), 'sha256': digest(path.read_bytes())}
                                             for kind, path in (('capture', args.capture), ('policy', args.policy))}
            else:
                cases = unit_cases() + ([] if args.no_native else pod_cases(Path(scratch)))
            for name, source, policy in cases:
                report['cases'].append(benchmark(name, source, policy, library, args.out, args.runs,
                                                 workload_kind='supplied-capture' if args.capture else 'fixture'))
                write_json(args.out / 'report.json', report)
        if source_identity() != report['source'] or digest(library.read_bytes()) != report['library']['sha256']:
            raise ValueError('source or scorer library changed during benchmark')
        report['status'] = 'passed-parity'
    except Exception as exc:
        # Retain incomplete/failing evidence and still fail the command; never replace it with timings.
        report['status'] = 'failed'
        report['failure'] = {'type': type(exc).__name__, 'message': str(exc)}
        raise
    finally:
        report['environment_after'] = environment()
        write_json(args.out / 'report.json', report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

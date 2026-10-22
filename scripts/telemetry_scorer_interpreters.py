#!/usr/bin/env python3
"""Compare CPython and PyPy on identical captured-geometry scorer queries.

Each worker owns a whole workload. No per-score IPC or native KiCad runs occur.
Native workload admission is external. These are generated queries, not traces
of a placement search. The original Python reference is unchanged.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

import telemetry_scorer_replay as replay

ROOT = Path(__file__).resolve().parents[1]


def save(path, document):
    path.write_text(json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n')


def identity(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': replay.sha256(path)}


def worker(config_path):
    config = json.loads(config_path.read_bytes())
    output = Path(config['output'])
    report = {'schema': 'agent-ee.scorer-interpreter-worker/1', 'status': 'starting', 'samples': []}
    try:
        _worker(config, output, report)
    except BaseException as exc:
        report.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)})
        save(output, report)
        raise


def _worker(config, output, report):
    source_before = replay.source_identity()
    actual_implementation = platform.python_implementation()
    if actual_implementation != config['implementation']:
        raise ValueError(f'expected {config["implementation"]}, observed {actual_implementation}')
    inputs = {name: identity(config[name]) for name in ('capture', 'policy')}
    started = time.perf_counter_ns()
    capture = json.loads(Path(config['capture']).read_bytes())
    policy = json.loads(Path(config['policy']).read_bytes())
    load_ns = time.perf_counter_ns() - started
    started = time.perf_counter_ns()
    tasks, generation = replay.workloads(capture, policy)
    workload_build_ns = time.perf_counter_ns() - started
    _, snapshots, poses, counts = next(row for row in tasks if row[0] == config['state'])
    native = None
    if config['engine'] == 'rust_batch':
        if actual_implementation != 'CPython':
            raise ValueError('optional native comparator requires CPython handle-release semantics')
        inputs['library'] = identity(config['library'])
        native = replay.scoring.Native(config['library'])

    def evaluate():
        if native is None:
            return [replay.scoring.reference(snapshot, poses) for snapshot in snapshots]
        result = []
        for snapshot in snapshots:
            packed = native.prepare(snapshot)
            try:
                result.append(native.score(packed, poses))
            finally:
                del packed
        return result

    expected = None
    if config.get('expected'):
        expected = json.loads(Path(config['expected']).read_bytes())
    report.update({'schema': 'agent-ee.scorer-interpreter-worker/1', 'state': config['state'],
              'phase': config['phase'], 'engine': config['engine'],
              'environment_before': replay.environment(), 'executable': identity(sys.executable),
              'inputs_before': inputs, 'source_before': source_before,
              'capture_policy_load_ns': load_ns, 'workload_build_both_states_ns': workload_build_ns,
              'generation': generation, 'counts': counts, 'samples': [],
              'gc_policy': 'interpreter default; no forced collections or disabled GC',
              'first_pass_prior_scoring_calls': 0, 'driver': identity(Path(__file__))})

    def sample(phase):
        before_load = list(os.getloadavg())
        started = time.perf_counter_ns()
        scores = evaluate()
        elapsed = time.perf_counter_ns() - started
        sample_record = {'index': len(report['samples']), 'phase': phase, 'elapsed_ns': elapsed,
                         'scores_sha256': replay.digest(scores),
                         'checked_against_external_cpython_oracle': False,
                         'load_before': before_load, 'load_after': list(os.getloadavg())}
        report['samples'].append(sample_record)
        if expected is not None:
            try:
                replay.equal(scores, expected, config['state'], config['engine'])
            except BaseException:
                mismatch = output.with_suffix('.mismatch.scores.json')
                save(mismatch, scores)
                sample_record['mismatching_scores_file'] = identity(mismatch)
                sample_record['expected_scores_sha256'] = replay.digest(expected)
                raise
            sample_record['checked_against_external_cpython_oracle'] = True
        return scores, elapsed

    scores, _ = sample('first')
    if config['phase'] == 'warm':
        warmup_elapsed, passes = 0, 0
        minimum_ns = int(config['warmup_seconds'] * 1_000_000_000)
        while passes < config['warmup_max_passes'] and (
                passes < config['warmups'] or warmup_elapsed < minimum_ns):
            scores, elapsed = sample('warmup')
            warmup_elapsed += elapsed
            passes += 1
        reached = passes >= config['warmups'] and warmup_elapsed >= minimum_ns
        report['warmup'] = {'passes': passes, 'score_time_ns': warmup_elapsed,
                            'minimum_passes': config['warmups'], 'minimum_score_time_ns': minimum_ns,
                            'maximum_passes': config['warmup_max_passes'],
                            'stop_reason': 'minimum_passes_and_duration_reached' if reached else 'pass_cap_reached'}
        if not reached:
            save(output, report)
            raise RuntimeError('bounded warmup did not reach declared minimum; no warm measurements taken')
        for _ in range(config['runs']):
            scores, _ = sample('measurement')
    save(output.with_suffix('.scores.json'), scores)
    report['scores_file'] = identity(output.with_suffix('.scores.json'))
    report['source_after'] = replay.source_identity()
    report['inputs_after'] = {name: identity(value['path']) for name, value in inputs.items()}
    if source_before != report['source_after'] or inputs != report['inputs_after']:
        raise RuntimeError('source or input identity changed during worker execution')
    report['environment_after'] = replay.environment()
    report['status'] = 'complete'
    save(output, report)


def run_worker(config, interpreter, output, timeout):
    specification = output.with_suffix('.input.json')
    save(specification, {**config, 'output': str(output)})
    argv = [str(interpreter), '-B', str(Path(__file__).resolve()), '--worker', str(specification)]
    started = time.perf_counter_ns()
    process_record = {'argv': argv, 'timeout_s': timeout, 'exit_code': None, 'exception': None}
    try:
        # A pipe lets communicate() wait for EOF rather than polling waitpid at
        # timeout intervals, which otherwise distorts short cold-process samples.
        with output.with_suffix('.stderr.log').open('wb') as stderr:
            result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=stderr, timeout=timeout, check=False)
        process_record['process_wall_ns'] = time.perf_counter_ns() - started
        output.with_suffix('.stdout.log').write_bytes(result.stdout)
        process_record['exit_code'] = result.returncode
    except BaseException as exc:
        process_record['exception'] = {'type': type(exc).__name__, 'message': str(exc)}
        if isinstance(exc, subprocess.TimeoutExpired):
            output.with_suffix('.stdout.log').write_bytes(exc.stdout or b'')
        raise
    finally:
        if 'process_wall_ns' not in process_record:
            process_record['process_wall_ns'] = time.perf_counter_ns() - started
        save(output.with_suffix('.process.json'), process_record)
    if process_record['exit_code']:
        raise RuntimeError(f'worker exited {process_record["exit_code"]}; inspect {output.with_suffix(".stderr.log")}')
    report = json.loads(output.read_bytes())
    if report['status'] != 'complete':
        raise RuntimeError('worker did not complete')
    return {**process_record, 'process_metadata': identity(output.with_suffix('.process.json')),
            'worker_report': identity(output), 'worker': report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--pypy', type=Path, required=True)
    parser.add_argument('--cpython', type=Path, default=Path(sys.executable))
    parser.add_argument('--library', type=Path, help='optional CPython/Rust prepared-batch comparator')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--runs', type=int, choices=range(1, 6), default=5)
    parser.add_argument('--warmups', type=int, choices=range(1, 6), default=5)
    parser.add_argument('--warmup-seconds', type=float, default=2.0,
                        help='minimum cumulative scorer time, in addition to minimum warmup passes')
    parser.add_argument('--warmup-max-passes', type=int, default=1000)
    parser.add_argument('--timeout', type=int, default=300, help='finite timeout per worker in seconds')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    if not 0 <= args.warmup_seconds <= 60 or not args.warmups <= args.warmup_max_passes <= 1000:
        parser.error('warmup seconds must be 0..60 and maximum passes between minimum passes and 1000')
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    paths = {name: getattr(args, name).resolve() for name in ('capture', 'policy', 'pypy', 'cpython')}
    if args.library:
        paths['library'] = args.library.resolve()
    arms = {'cpython_reference': (paths['cpython'], 'CPython', 'reference'),
            'pypy_reference': (paths['pypy'], 'PyPy', 'reference')}
    if args.library:
        arms['cpython_rust_batch'] = (paths['cpython'], 'CPython', 'rust_batch')
    report = {'schema': 'agent-ee.scorer-interpreter-comparison/1', 'status': 'running',
              'scope': 'Generated queries on captured geometry; original Python reference under each interpreter; '
                       'no placement, admissibility, routing, DRC, or per-score IPC.',
              'inputs_before': {name: identity(path) for name, path in paths.items()},
              'driver': identity(Path(__file__)), 'source_before': replay.source_identity(),
              'environment_before': replay.environment(), 'runs': args.runs, 'warmups': args.warmups,
              'warmup_minimum_seconds': args.warmup_seconds, 'warmup_maximum_passes': args.warmup_max_passes,
              'states': [], 'timing_boundary': {
                  'cold_process': 'Fresh interpreter launch through exit, including imports, source verification, '
                                  'capture/policy load, both-state snapshot construction, first scorer pass, '
                                  'exact checking, score/report serialization and interpreter teardown.',
                  'first_pass': 'First scorer invocation in a fresh worker after imports and snapshot construction; '
                                'no oracle evaluation or earlier scoring in that process.',
                  'warm': 'At least five warmup passes and two cumulative scorer seconds by default, '
                          'bounded at 1000 passes; refusal if the minimum is not reached. '
                          'Then measured passes in one worker. '
                          'Warmup is reported, not assumed to establish a steady state.',
                  'score_pass': 'Score evaluation and materialized integer results; native comparator additionally '
                                'includes per-source preparation, pose packing and immediate CPython handle release. '
                                'Capture/loading, snapshots, exact checking and hashing excluded.'},
              'limitations': ['Interpreter/default GC effects remain included.',
                              'A separate unmeasured CPython process establishes each state oracle; its cost is retained.',
                              'Speedups describe these inputs and host only; no backend promotion is implied.']}
    try:
        for state_index, state in enumerate(('fixed_peers_initial', 'all_other_peers_hypothetical')):
            directory = output / state
            directory.mkdir()
            row = {'state': state, 'cold': {name: [] for name in arms}, 'warm': {}, 'cold_orders': []}
            report['states'].append(row)
            common = {'capture': str(paths['capture']), 'policy': str(paths['policy']), 'state': state,
                      'runs': args.runs, 'warmups': args.warmups,
                      'warmup_seconds': args.warmup_seconds, 'warmup_max_passes': args.warmup_max_passes}
            if args.library:
                common['library'] = str(paths['library'])
            oracle_config = {**common, 'phase': 'oracle', 'implementation': 'CPython', 'engine': 'reference'}
            row['oracle'] = run_worker(oracle_config, paths['cpython'], directory / 'oracle.json', args.timeout)
            expected_path = Path(row['oracle']['worker']['scores_file']['path'])
            expected = json.loads(expected_path.read_bytes())
            for repetition in range(args.runs):
                order = list(arms) if (repetition + state_index) % 2 == 0 else list(reversed(arms))
                row['cold_orders'].append(order)
                for name in order:
                    executable, implementation, engine = arms[name]
                    config = {**common, 'phase': 'cold', 'implementation': implementation, 'engine': engine,
                              'expected': str(expected_path)}
                    record = run_worker(config, executable, directory / f'cold-{repetition}-{name}.json', args.timeout)
                    scores_path = Path(record['worker']['scores_file']['path'])
                    scores = json.loads(scores_path.read_bytes())
                    replay.equal(scores, expected, state, name)
                    record['external_cpython_oracle_exact'] = True
                    if record['worker']['driver'] != report['driver']:
                        raise RuntimeError('worker driver identity differs from orchestrator')
                    row['cold'][name].append(record)
            order = list(arms) if state_index % 2 == 0 else list(reversed(arms))
            row['warm_order'] = order
            for name in order:
                executable, implementation, engine = arms[name]
                config = {**common, 'phase': 'warm', 'implementation': implementation, 'engine': engine,
                          'expected': str(expected_path)}
                row['warm'][name] = run_worker(config, executable, directory / f'warm-{name}.json', args.timeout)
                if row['warm'][name]['worker']['driver'] != report['driver']:
                    raise RuntimeError('worker driver identity differs from orchestrator')
            row['expected_scores_sha256'] = replay.digest(expected)
            row['medians_ns'] = {
                name: {'cold_process': statistics.median(r['process_wall_ns'] for r in row['cold'][name]),
                       'cold_first_score_pass': statistics.median(r['worker']['samples'][0]['elapsed_ns'] for r in row['cold'][name]),
                       'warm_score_pass': statistics.median(s['elapsed_ns'] for s in row['warm'][name]['worker']['samples']
                                                            if s['phase'] == 'measurement')}
                for name in arms}
            row['speedup_vs_cpython'] = {name: {metric: row['medians_ns']['cpython_reference'][metric] / value
                                               for metric, value in metrics.items()}
                                         for name, metrics in row['medians_ns'].items()}
            print(json.dumps({'state': state, 'medians_ns': row['medians_ns'],
                              'speedup_vs_cpython': row['speedup_vs_cpython']}), flush=True)
            save(output / 'report.json', report)
        report['inputs_after'] = {name: identity(path) for name, path in paths.items()}
        report['source_after'] = replay.source_identity()
        if (report['inputs_before'] != report['inputs_after'] or report['source_before'] != report['source_after']
                or report['driver'] != identity(Path(__file__))):
            raise RuntimeError('input/source identity changed during comparison')
        report.update(status='complete', all_scores_exact=True)
    except BaseException as exc:
        report.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)})
        raise
    finally:
        report['environment_after'] = replay.environment()
        save(output / 'report.json', report)
    return 0


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        worker(Path(sys.argv[2]))
    else:
        raise SystemExit(main())

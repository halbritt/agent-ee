#!/usr/bin/env python3
"""One matched CPython/C/Rust/PyPy full-telemetry comparison on frozen inputs.

Four fresh workers per round, four balanced Williams rounds, no backend-specific
warmup. Every retained artifact is compared against round-one CPython. Native
workload admission and disposable host provisioning are external to this driver.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import statistics
import sys
import time
import traceback

import telemetry_scorer_compare as pair

ROOT = Path(__file__).resolve().parents[1]
BACKENDS = ('python', 'c', 'rust', 'pypy')
ORDERS = (('python', 'c', 'pypy', 'rust'), ('c', 'rust', 'python', 'pypy'),
          ('rust', 'pypy', 'c', 'python'), ('pypy', 'python', 'rust', 'c'))
OVERALL_SECONDS = 80 * 60
BENCHMARK_FILES = ('scripts/telemetry_scorer_four_way.py', 'scripts/telemetry_scorer_compare.py',
                   'scripts/placement_scoring_c.py', 'scripts/data/placement_score_original.c',
                   'scripts/data/placement_score_original.json')


def validate_orders():
    for order in ORDERS:
        if set(order) != set(BACKENDS) or len(order) != len(BACKENDS):
            raise ValueError('each round must contain every backend exactly once')
    for column in zip(*ORDERS):
        if set(column) != set(BACKENDS):
            raise ValueError('each backend must occupy each position once')
    carryovers = Counter((left, right) for order in ORDERS for left, right in zip(order, order[1:]))
    if carryovers != Counter({(left, right): 1 for left in BACKENDS for right in BACKENDS if left != right}):
        raise ValueError('every directed within-round carryover must occur once')


def file_identity(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'sha256': pair.sha(path.read_bytes())}


def tree_identity(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f'identity root must be a directory: {root}')
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            target = path.resolve(strict=True)
            if not target.is_relative_to(root) or target.is_dir():
                raise ValueError(f'unsealed external/directory symlink in identity tree: {path}')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = pair.sha(path.read_bytes())
    if not files:
        raise ValueError(f'identity tree is empty: {root}')
    return {'path': str(root), 'file_count': len(files), 'files_sha256': files,
            'files_manifest_sha256': pair.sha(pair.canonical(files))}


def source_identity():
    source = pair.source_identity()
    for relative in BENCHMARK_FILES:
        digest = pair.sha((ROOT / relative).read_bytes())
        if source['provenance'] == 'archive-manifest-verified-files':
            if source['files_sha256'].get(relative) != digest:
                raise ValueError(f'archive manifest does not seal benchmark file: {relative}')
        else:
            source['files_sha256'][relative] = digest
    return source


def common_identity(config):
    inputs = pair.input_identity(config)
    inputs['c_library'] = file_identity(config['c_library'])
    return {'inputs': inputs, 'source': source_identity(),
            'cpython': file_identity(sys.executable), 'kicad_cli': file_identity(config['kicad_cli']),
            'pypy_distribution': tree_identity(config['pypy_root']), 'models': tree_identity(config['models']),
            'model_environment': os.environ.get('KICAD10_3DMODEL_DIR')}


def worker(config_path):
    config = json.loads(config_path.read_bytes())
    arm = Path(config['arm_root'])
    if os.environ.get('KICAD10_3DMODEL_DIR') != config['models']:
        raise ValueError('model runtime environment differs from the frozen model root')
    before = pair.sha(pair.canonical(common_identity(config)))
    if before != config['expected_identity_sha256']:
        raise ValueError('worker source/input/runtime/model identities differ before execution')
    result = pair.worker(config_path)
    after = pair.sha(pair.canonical(common_identity(config)))
    metadata_path = arm / 'worker-metadata.json'
    metadata = json.loads(metadata_path.read_bytes())
    metadata['matched_identity'] = {'before_sha256': before, 'after_sha256': after,
                                    'expected_sha256': config['expected_identity_sha256'],
                                    'unchanged': before == after}
    pair.save(metadata_path, metadata)
    if before != after:
        raise ValueError('worker source/input/runtime/model identities changed during execution')
    return result


def require_success(record, expected_identity):
    backend, metadata = record['backend'], record.get('worker', {})
    if record['exit_code'] != 0 or metadata.get('status') != 'placed' or metadata.get('exception'):
        raise ValueError(f'{backend} failed, timed out or refused; retain artifacts and stop without rerun')
    if metadata.get('scorer_events'):
        raise ValueError(f'{backend} fell back; no performance result is accepted')
    identity = metadata.get('matched_identity', {})
    if (identity.get('before_sha256') != expected_identity or identity.get('after_sha256') != expected_identity
            or identity.get('unchanged') is not True):
        raise ValueError(f'{backend} lacks stable matched source/input/runtime/model identity')
    rust_calls = metadata.get('rust_planning_calls', [])
    if backend == 'rust' and rust_calls:
        if any(call.get('status') != 'ok' or call.get('exit_code') != 0 or not call.get('executable_sha256')
               or not call.get('input_sha256') for call in rust_calls):
            raise ValueError('Rust lacks successful whole-planning runtime evidence')
    elif backend in ('c', 'rust') and (metadata.get('first_native_invocation') or {}).get('returned') is not True:
        raise ValueError(f'{backend} lacks an actual successful native scorer invocation')
    if backend == 'pypy':
        calls = metadata.get('pypy_planning_calls', [])
        if not calls or any(call.get('status') != 'ok' or call.get('exit_code') != 0 or call.get('error')
                            or call.get('runtime', {}).get('implementation') != 'PyPy' for call in calls):
            raise ValueError('PyPy lacks successful planning runtime evidence')
        if not any((call.get('first_wire_cost') or {}).get('returned') is True for call in calls):
            raise ValueError('PyPy lacks an actual successful Python scorer invocation')


def compare_to_reference(reference, arm):
    expected, expected_fields, expected_links = pair.comparison_artifacts(reference)
    actual, actual_fields, actual_links = pair.comparison_artifacts(arm)
    missing = sorted(set(expected) ^ set(actual))
    changed = sorted(name for name in set(expected) & set(actual) if expected[name] != actual[name])
    fields_equal = set(expected_fields) == set(actual_fields)
    board_equal = ('candidate.kicad_pcb' in expected and
                   expected['candidate.kicad_pcb'] == actual.get('candidate.kicad_pcb'))
    equal = not missing and not changed and fields_equal and board_equal
    return {'status': 'equal' if equal else 'different', 'reference': str(reference),
            'candidate_board_bytes_equal': board_equal, 'file_set_difference': missing,
            'changed_files': changed, 'same_normalization_fields': fields_equal,
            'normalizations': {'reference': expected_fields, 'candidate': actual_fields},
            'verified_links': {'reference': expected_links, 'candidate': actual_links},
            'comparison_sha256': {name: pair.sha(raw) for name, raw in actual.items()},
            'scope': 'Every artifact compared to round-one CPython; only existing comparator normalization; raw evidence unchanged.'}


def summarize(rounds):
    if len(rounds) != len(ORDERS) or any(len(row['arms']) != len(BACKENDS) for row in rounds):
        raise ValueError('all four complete rounds are required before reporting performance')
    samples = {backend: [] for backend in BACKENDS}
    paired = {backend: [] for backend in BACKENDS}
    for row in rounds:
        records = {record['backend']: record for record in row['arms']}
        if (set(records) != set(BACKENDS) or set(row['comparisons']) != set(BACKENDS)
                or any(value['status'] != 'equal' for value in row['comparisons'].values())):
            raise ValueError('every backend must pass parity before reporting performance')
        baseline = records['python']['worker']['runner_wall_ns']
        for backend, record in records.items():
            elapsed = record['worker']['runner_wall_ns']
            samples[backend].append(elapsed)
            paired[backend].append(baseline / elapsed)
    means = {backend: statistics.mean(values) for backend, values in samples.items()}
    return {'runner_wall_ns': samples, 'mean_runner_ns': means,
            'median_runner_ns': {backend: statistics.median(values) for backend, values in samples.items()},
            'per_round_speedup_vs_cpython': paired,
            'ratio_of_mean_cpython_to_backend': {backend: means['python'] / value for backend, value in means.items()},
            'mean_wall_reduction_percent': {backend: 100 * (1 - value / means['python']) for backend, value in means.items()},
            'interpretation': 'Four matched observations per backend on one host/input; no earlier timings pooled, no confidence interval or automatic promotion.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('board', 'netlist', 'request', 'models', 'c-library', 'pypy', 'pypy-root', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    rust = parser.add_mutually_exclusive_group(required=True)
    rust.add_argument('--rust-library', type=Path)
    rust.add_argument('--rust-planner', type=Path)
    args = parser.parse_args()
    validate_orders()
    if shutil.which('timeout') is None or shutil.which('kicad-cli') is None:
        parser.error('GNU timeout and kicad-cli are required')
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    config = {name: str(getattr(args, name).resolve(strict=True))
              for name in ('board', 'netlist', 'request', 'models', 'c_library', 'pypy', 'pypy_root')}
    if args.rust_planner is not None:
        config['rust_planner'] = str(args.rust_planner.resolve(strict=True))
    else:
        config['library'] = str(args.rust_library.resolve(strict=True))
    config['kicad_cli'] = str(Path(shutil.which('kicad-cli')).resolve())
    if any(Path(value).is_relative_to(root) for value in config.values()):
        raise ValueError('all frozen inputs/runtimes must be outside the writable result root')
    if root.is_relative_to(Path(config['pypy_root'])) or root.is_relative_to(Path(config['models'])):
        raise ValueError('results cannot be written inside the frozen runtime or model trees')
    if not Path(config['pypy']).is_relative_to(Path(config['pypy_root'])):
        raise ValueError('PyPy executable must belong to the pinned distribution tree')
    os.environ['KICAD10_3DMODEL_DIR'] = config['models']
    manifest = {'schema': 'agent-ee.telemetry-scorer-four-way/1', 'status': 'running',
                'orders': ORDERS, 'rounds': [], 'environment_before': pair.environment(),
                'timeout_s_per_arm': pair.TIMEOUT_SECONDS, 'overall_budget_s': OVERALL_SECONDS,
                'termination_grace_s': 5, 'changes': ['authorization.writable_root per arm', 'skip_visuals=True for every arm'],
                'backend_definitions': {'python': 'Unchanged CPython reference placement',
                                        'c': 'Original unmodified C kernel from 50e586818acf6265039a3509acc910eb0158f357 with benchmark fast adapter; no full oracle inside timer',
                                        'rust': ('Greenfield complete Rust placement planner; native KiCad capture, apply and independent validation remain in CPython'
                                                 if args.rust_planner is not None else 'Opt-in prepared Rust scalar scorer under CPython'),
                                        'pypy': 'Original Python scorer and complete pure planner in PyPy; native KiCad remains in CPython'},
                'boundary': 'Same complete place_candidate worker for all arms; context/library/startup/IPC included; imports and external report serialization excluded. One fresh worker per arm, no warmup or persistent worker.',
                'correctness': 'All sixteen outputs must match round-one CPython; no fallback, missing witness, mismatch or rerun accepted.',
                'memory_boundary': 'Separate self/children high-water RSS, not an additive simultaneous peak.'}
    deadline = time.monotonic() + OVERALL_SECONDS
    try:
        identity = common_identity(config)
        expected = pair.sha(pair.canonical(identity))
        config['expected_identity_sha256'] = expected
        manifest.update(identity_before=identity, identity_sha256=expected)
        pair.save(root / 'manifest.json', manifest)
        reference = root / 'round-1' / 'python'
        for number, order in enumerate(ORDERS, 1):
            directory = root / f'round-{number}'
            directory.mkdir()
            row = {'round': number, 'order': order, 'arms': [], 'comparisons': {}}
            manifest['rounds'].append(row)
            for backend in order:
                if pair.sha(pair.canonical(common_identity(config))) != expected:
                    raise ValueError('source/input/runtime/model identity changed between arms')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('overall comparison budget expired; no next worker launched')
                timeout = min(pair.TIMEOUT_SECONDS, remaining)
                record = pair.run_arm(config, backend, directory, worker_script=Path(__file__), timeout_seconds=timeout)
                row['arms'].append(record)
                pair.save(root / 'manifest.json', manifest)
                require_success(record, expected)
                comparison = compare_to_reference(reference, directory / backend)
                row['comparisons'][backend] = comparison
                pair.save(root / 'manifest.json', manifest)
                if comparison['status'] != 'equal':
                    raise ValueError(f'round {number} {backend} differs from round-one CPython; stop without rerun')
            elapsed = {record['backend']: record['worker']['runner_wall_ns'] for record in row['arms']}
            row['speedup_vs_cpython'] = {backend: elapsed['python'] / value for backend, value in elapsed.items()}
            print(json.dumps({'round': number, 'runner_wall_ns': elapsed,
                              'speedup_vs_cpython': row['speedup_vs_cpython']}), flush=True)
        manifest['identity_after'] = common_identity(config)
        if manifest['identity_after'] != identity:
            raise ValueError('source/input/runtime/model identities changed across comparison')
        manifest['summary'] = summarize(manifest['rounds'])
        manifest['status'] = 'completed'
    except BaseException as exc:
        manifest.update(status='failed', failure={'type': type(exc).__name__, 'message': str(exc)})
        (root / 'driver-exception.txt').write_text(traceback.format_exc())
    finally:
        manifest['environment_after'] = pair.environment()
        pair.save(root / 'manifest.json', manifest)
    print(json.dumps({'status': manifest['status'], 'manifest': str(root / 'manifest.json')}), flush=True)
    return 0 if manifest['status'] == 'completed' else 1


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        raise SystemExit(worker(Path(sys.argv[2])))
    raise SystemExit(main())

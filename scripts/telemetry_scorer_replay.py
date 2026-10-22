#!/usr/bin/env python3
"""Generated scorer queries on captured pod geometry, not actual search traces.

Native workload admission is external to this driver. Five matched scorer-only
trials do not run placement, test admissibility,
reconstruct actual search traces, or establish native board/DRC improvement.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from agent_ee import placement_scoring as scoring, placer

RUNS = 5
GRID = 9
BUILD = ROOT / 'build' / 'rust-scoring'
ARM_NAMES = ('python_prepared_reference', 'rust_prepared_batch', 'rust_scalar_wrapper', 'checked_batch')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_identity():
    modules = sorted((ROOT / 'src' / 'agent_ee').rglob('*.py'))
    modules.append(ROOT / 'src/agent_ee/data/placement_score.rs')
    module_names = {path.relative_to(ROOT).as_posix() for path in modules}
    manifest_path = ROOT / 'SOURCE.json'
    if manifest_path.exists() or manifest_path.is_symlink():
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
        valid_hex = lambda value, length: isinstance(value, str) and len(value) == length and all(
            c in '0123456789abcdef' for c in value)
        if (manifest.get('schema') != 'agent-ee.source-archive/1'
                or not valid_hex(manifest.get('revision'), 40)
                or not valid_hex(manifest.get('archive_sha256'), 64)
                or not isinstance(manifest.get('files_sha256'), dict)):
            raise ValueError('invalid explicit SOURCE.json archive manifest')
        expected = manifest['files_sha256']
        missing = sorted(module_names - expected.keys())
        if missing:
            raise ValueError(f'archive manifest omits present source modules: {missing}')
        observed = {}
        for relative, expected_hash in expected.items():
            path = ROOT / relative
            if (not isinstance(relative, str) or Path(relative).is_absolute()
                    or '..' in Path(relative).parts or not path.resolve().is_relative_to(ROOT)
                    or not valid_hex(expected_hash, 64)):
                raise ValueError(f'invalid archive manifest file entry: {relative!r}')
            observed[relative] = sha256(path)
            if observed[relative] != expected_hash:
                raise ValueError(f'archive source file hash mismatch: {relative}')
        identity = {'provenance': 'archive-manifest-verified-files', 'revision': manifest['revision'],
                    'archive_sha256': manifest['archive_sha256'],
                    'manifest_sha256': hashlib.sha256(raw).hexdigest(),
                    'archive_identity_scope': 'revision/export hash supplied by SOURCE.json; '
                    'every listed file and coverage of present Python/Rust modules verified here',
                    'files_sha256': observed}
    else:
        identity = {'provenance': 'observed-git-checkout',
                    'git_head': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
                    'files_sha256': {str(path.relative_to(ROOT)): sha256(path) for path in modules}}
    identity['scratch_driver'] = {'path': str(Path(__file__).resolve().relative_to(ROOT)),
                                  'sha256': sha256(Path(__file__))}
    return identity


def environment():
    return {'utc': stamp(), 'host': platform.node(), 'platform': platform.platform(),
            'machine': platform.machine(), 'python': sys.version,
            'python_implementation': platform.python_implementation(),
            'cpu_count': os.cpu_count(), 'affinity': sorted(os.sched_getaffinity(0)),
            'load_average': list(os.getloadavg())}


def peer_points(footprints, source_ref):
    peers = {}
    for fp in footprints:
        if fp['ref'] == source_ref:
            continue
        for pad in fp['pads']:
            if pad['net']:
                peers.setdefault(pad['net'], []).append(tuple(pad['position_nm']))
    return peers


def workloads(capture, policy):
    footprints = sorted(capture['footprints'], key=lambda f: f['ref'])
    refs = {fp['ref'] for fp in footprints}
    if len(refs) != len(footprints):
        raise ValueError('duplicate footprint references in retained capture')
    fixed_refs = placer._fixed_refs(capture, policy)
    if fixed_refs - refs:
        raise ValueError(f'fixed references absent from capture: {sorted(fixed_refs - refs)}')
    fixed = [fp for fp in footprints if fp['ref'] in fixed_refs]
    movable = [fp for fp in footprints if fp['ref'] not in fixed_refs]
    if not movable:
        raise ValueError('retained capture has no movable source footprints')
    points = [point for polygon in capture['outline']['polygons'] for point in polygon]
    x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
    y0, y1 = min(p[1] for p in points), max(p[1] for p in points)
    xs = [x0 + (x1 - x0) * i // (GRID - 1) for i in range(GRID)]
    ys = [y0 + (y1 - y0) * i // (GRID - 1) for i in range(GRID)]
    poses = tuple((x, y, rotation) for x in xs for y in ys for rotation in scoring.ROTATIONS)
    if len(set(poses)) != GRID * GRID * 4:
        raise ValueError('board bounding box cannot produce the complete distinct 9x9 grid')
    grounds = policy['ground_nets']
    result = []
    for name, peers_from, interpretation in (
            ('fixed_peers_initial', fixed,
             'Each movable source scored against fixed/locked footprints at captured coordinates.'),
            ('all_other_peers_hypothetical', footprints,
             'Each movable source scored against every other footprint at captured coordinates; '
             'this is a hypothetical dense peer state, not a realized late placement.')):
        snapshots = []
        rows = []
        for fp in movable:
            pads = {str(r): placer._pad_nets(fp, (0, 0), r, grounds) for r in scoring.ROTATIONS}
            peers = peer_points(peers_from, fp['ref'])
            snapshot = scoring.Snapshot.capture((name, fp['ref']), pads, peers)
            snapshots.append(snapshot)
            relevant = dict(snapshot.peers)
            rows.append({'ref': fp['ref'], 'queries': len(poses),
                         'signal_pad_terms_per_rotation': len(snapshot.pads[0]),
                         'matched_pad_terms_per_rotation': sum(net in relevant for net, _, _ in snapshot.pads[0]),
                         'relevant_peer_entries': sum(len(v) for v in relevant.values()),
                         'packed_peer_terms_all_rotations': sum(len(relevant.get(net, ()))
                             for rotation in snapshot.pads for net, _, _ in rotation)})
        result.append((name, snapshots, poses, {
            'interpretation': interpretation, 'source_count': len(snapshots),
            'queries_per_source': len(poses), 'queries_total': len(snapshots) * len(poses),
            'peer_footprint_pool_count': len(peers_from),
            'peer_pool_footprints_with_origins_outside_board_bbox': sum(
                not (x0 <= fp['position_nm'][0] <= x1 and y0 <= fp['position_nm'][1] <= y1)
                for fp in peers_from),
            'source_counts': rows,
        }))
    return result, {'footprints': len(footprints), 'fixed': len(fixed), 'movable': len(movable),
                    'fixed_refs': sorted(fixed_refs), 'movable_refs': [fp['ref'] for fp in movable],
                    'ground_nets': grounds, 'board_bbox_nm': [x0, y0, x1, y1],
                    'grid': {'size': [GRID, GRID], 'x_nm': xs, 'y_nm': ys,
                             'rotations_deg': list(scoring.ROTATIONS),
                             'ordering': 'x then y then rotation', 'pose_sha256': digest(poses)}}


def arms_for(snapshots, poses, library):
    native = scoring.Native(library)
    fast = scoring.Session(library, check=False)
    checked = scoring.Session(library, check=True)
    if fast.native is None or checked.native is None or fast.events or checked.events:
        raise RuntimeError(f'cannot load requested Rust scorer: {fast.events + checked.events}')

    def reference():
        return [scoring.reference(snapshot, poses) for snapshot in snapshots]

    def batch():
        results = []
        for snapshot in snapshots:
            packed = native.prepare(snapshot)
            try:
                results.append(native.score(packed, poses))
            finally:
                del packed  # CPython releases the owned native handle inside timing.
        return results

    def wrapped(session, scalar):
        results = []
        for snapshot in snapshots:
            scorer = session.scorer(snapshot)
            try:
                if session.events:
                    raise RuntimeError(f'Rust preparation rolled back: {session.events}')
                results.append([scorer.score(*pose, generation=snapshot.generation) for pose in poses]
                               if scalar else scorer.score_batch(poses, generation=snapshot.generation))
                if session.events:
                    raise RuntimeError(f'Rust scoring rolled back: {session.events}')
            finally:
                del scorer  # CPython releases the owned native handle inside timing.
        return results

    return dict(zip(ARM_NAMES, (reference, batch, lambda: wrapped(fast, True), lambda: wrapped(checked, False))))


def equal(actual, expected, state, arm):
    if actual != expected:
        for i, (got, wanted) in enumerate(zip(actual, expected)):
            if got != wanted:
                raise ValueError(f'exact score mismatch in {state}/{arm}, source index {i}')
        raise ValueError(f'exact score mismatch or output length in {state}/{arm}')


def measure(name, snapshots, poses, counts, library, partial):
    arms = arms_for(snapshots, poses, library)
    # The unmeasured oracle establishes expected scores independently of Rust.
    expected = arms['python_prepared_reference']()
    row = {'state': name, **counts, 'raw_ns': {label: [] for label in arms},
           'warmup_ns': {}, 'arm_orders': [], 'samples': [], 'expected_scores_sha256': digest(expected)}
    partial['states'].append(row)
    for label, call in arms.items():
        partial['active'] = {'state': name, 'phase': 'warmup', 'arm': label}
        started = time.perf_counter_ns()
        actual = call()
        row['warmup_ns'][label] = time.perf_counter_ns() - started
        equal(actual, expected, name, label)
        del actual
    for repetition in range(RUNS):
        labels = list(arms) if repetition % 2 == 0 else list(reversed(arms))
        row['arm_orders'].append(labels)
        for label in labels:
            partial['active'] = {'state': name, 'phase': 'measurement', 'arm': label, 'repetition': repetition}
            sample = {'repetition': repetition, 'arm': label, 'utc': stamp(),
                      'load_before': list(os.getloadavg())}
            started = time.perf_counter_ns()
            actual = arms[label]()
            elapsed = time.perf_counter_ns() - started
            sample.update(elapsed_ns=elapsed, load_after=list(os.getloadavg()))
            row['samples'].append(sample)
            row['raw_ns'][label].append(elapsed)
            equal(actual, expected, name, label)  # Comparison and digest are outside timing.
            del actual
    row['median_ns'] = {label: statistics.median(values) for label, values in row['raw_ns'].items()}
    baseline = row['median_ns']['python_prepared_reference']
    row['speedup_vs_python_reference'] = {label: baseline / duration for label, duration in row['median_ns'].items()}
    row['all_scores_exact'] = True
    print(json.dumps({'state': name, 'queries': row['queries_total'], 'median_ns': row['median_ns'],
                      'speedup_vs_python_reference': row['speedup_vs_python_reference']}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, default=BUILD / 'historical-pod' / 'capture.json')
    parser.add_argument('--policy', type=Path, default=BUILD / 'historical-pod' / 'policy.json')
    parser.add_argument('--library', type=Path, default=BUILD / 'placement_score-native.so')
    parser.add_argument('--out', type=Path, default=BUILD / 'capture-replay')
    parser.add_argument('--runs', type=int, choices=[RUNS], default=RUNS)
    args = parser.parse_args(argv)
    if platform.python_implementation() != 'CPython':
        raise RuntimeError('CPython is required to include immediate native-handle release in timing')
    source_before = source_identity()
    paths = {'capture': args.capture.resolve(), 'policy': args.policy.resolve(), 'library': args.library.resolve(),
             'script': Path(__file__).resolve(), 'python_wrapper': ROOT / 'src/agent_ee/placement_scoring.py',
             'rust_source': ROOT / 'src/agent_ee/data/placement_score.rs', 'placer_source': ROOT / 'src/agent_ee/placer.py'}
    hashes = {name: {'path': str(path), 'sha256': sha256(path)} for name, path in paths.items()}
    capture = json.loads(paths['capture'].read_text())
    policy = json.loads(paths['policy'].read_text())
    tasks, generation = workloads(capture, policy)
    args.out.mkdir(parents=True, exist_ok=False)
    report = {
        'schema': 'agent-ee.generated-historical-geometry-score-benchmark/1', 'status': 'running',
        'scope': 'Generated scorer queries on retained historical geometry; not actual search traces or full placement.',
        'runs': RUNS, 'warmups_per_arm': 1, 'inputs': hashes,
        'source_before': source_before,
        'environment_start': environment(), 'generation': generation, 'states': [],
        'timing_boundary': {
            'included': ['native state preparation for every source on every arm invocation',
                         'pose packing and validation used by each native API',
                         'score evaluation and Python result materialization',
                         'native handle release after each source',
                         'complete Python oracle computation within checked_batch'],
            'excluded': ['capture/policy loading', 'snapshot creation', 'shared library loading',
                         'compilation', 'comparison with expected results', 'report hashing/serialization'],
            'reference': 'Python reference consumes the same immutable snapshots; native preparation does not apply.',
        },
        'limitations': [
            'Grid candidates have not passed geometry, locality, keepout, or board admission checks.',
            'All-other peer state preserves captured positions, including off-board unplaced footprints.',
            'No candidate search, backjumping, lazy rank order, full-placement runtime, routing, or DRC is measured.',
            'Shared-host load is recorded; speedups describe this measurement session only.',
            'Current source hashes and loaded binary hash are recorded separately; a source hash alone is not build attestation.',
            'No speed threshold or automatic production/default promotion is defined for this generated workload.',
        ],
    }
    try:
        for name, snapshots, poses, counts in tasks:
            measure(name, snapshots, poses, counts, paths['library'], report)
        changed = [name for name, path in paths.items() if sha256(path) != hashes[name]['sha256']]
        if changed:
            raise RuntimeError(f'benchmark inputs/source changed during measurement: {changed}')
        report['source_after'] = source_identity()
        if report['source_after'] != source_before:
            raise RuntimeError('source identity changed during measurement')
        report.update(status='complete', all_scores_exact=True, environment_end=environment())
        report.pop('active', None)
    except BaseException as exc:
        report.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)},
                      environment_end=environment())
        (args.out / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        raise
    (args.out / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(str((args.out / 'report.json').resolve()), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

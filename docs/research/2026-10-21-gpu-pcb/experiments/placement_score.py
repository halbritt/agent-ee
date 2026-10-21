"""Exact int64 pad-to-nearest-peer scoring; no placement legality or search.

Run beside placement_score.cpp after compiling it to placement_score.so with
  g++ -O3 -std=c++17 -shared -fPIC placement_score.cpp -o placement_score.so
Requires numpy and cupy-cuda12x[ctk]. No CPU fallback for a failed GPU execution.
"""
import argparse
import ctypes
import hashlib
import json
import platform
import time
from pathlib import Path

import numpy as np
import cupy as cp

KERNEL = r'''
extern "C" __global__ void score_gpu(const long long* offsets, const long long* peers,
    const int* starts, int pads, const long long* poses, int count, long long* out) {
    int i = blockDim.x * blockIdx.x + threadIdx.x;
    if (i >= count) return;
    long long total = 0;
    for (int p = 0; p < pads; ++p) {
        long long x = offsets[2*p], y = offsets[2*p+1];
        for (int r = 0; r < poses[3*i+2]; ++r) {
            long long old = x; x = y; y = -old;
        }
        x += poses[3*i]; y += poses[3*i+1];
        long long best = 0x7fffffffffffffffLL;
        for (int j = starts[p]; j < starts[p+1]; ++j) {
            long long dx = x - peers[2*j], dy = y - peers[2*j+1];
            long long d = (dx < 0 ? -dx : dx) + (dy < 0 ? -dy : dy);
            if (d < best) best = d;
        }
        total += best;
    }
    out[i] = total;
}
'''


def prepare(data):
    refs = {q['ref'] for q in data['queries']}
    assert len(refs) == 1
    ref = next(iter(refs))
    target = next(f for f in data['footprints'] if f['ref'] == ref)
    ground = set(data['ground_nets'])
    offsets, peers, starts = [], [], [0]
    for pad in target['pads']:
        net = pad['net']
        matches = [p['position_nm'] for f in data['footprints'] if f['ref'] != ref
                   for p in f['pads'] if p['net'] == net]
        if not net or net in ground or not matches:
            continue
        offsets.append(pad['offset_nm']); peers.extend(matches); starts.append(len(peers))
    poses = [[*q['position_nm'], ((q['rotation_deg']-target['rotation_deg'])//90) % 4]
             for q in data['queries']]
    return (np.asarray(offsets, dtype=np.int64), np.asarray(peers, dtype=np.int64),
            np.asarray(starts, dtype=np.int32), np.asarray(poses, dtype=np.int64))


def score_numpy(offsets, peers, starts, poses):
    out = np.zeros(len(poses), dtype=np.int64)
    for p, (ox, oy) in enumerate(offsets):
        xy = np.asarray([[ox, oy], [oy, -ox], [-ox, -oy], [-oy, ox]], dtype=np.int64)
        points = xy[poses[:, 2]] + poses[:, :2]
        others = peers[starts[p]:starts[p+1]]
        out += np.abs(points[:, None, :] - others[None, :, :]).sum(axis=2).min(axis=1)
    return out


def timed(fn):
    start = time.perf_counter_ns(); value = fn()
    return (time.perf_counter_ns()-start)/1e6, value


def stats(values):
    return {'samples_ms': values, 'median_ms': float(np.median(values)),
            'min_ms': min(values), 'max_ms': max(values)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    prep_ms, arrays = timed(lambda: prepare(data))
    offsets, peers, starts, base = arrays
    library = ctypes.CDLL(str(Path(__file__).with_suffix('.so').resolve()))
    fn = library.score_cpu
    ptr64 = np.ctypeslib.ndpointer(dtype=np.int64, flags='C_CONTIGUOUS')
    ptr32 = np.ctypeslib.ndpointer(dtype=np.int32, flags='C_CONTIGUOUS')
    fn.argtypes = [ptr64, ptr64, ptr32, ctypes.c_int, ptr64, ctypes.c_int, ptr64]
    fn.restype = None
    oracle = np.asarray(data['expected_score_nm'], dtype=np.int64)
    cpu = np.empty(len(base), dtype=np.int64)
    fn(offsets, peers, starts, len(offsets), base, len(base), cpu)
    np.testing.assert_array_equal(cpu, oracle)
    np.testing.assert_array_equal(score_numpy(offsets, peers, starts, base), oracle)
    init = time.perf_counter()
    device = cp.cuda.Device(0); device.use()
    kernel = cp.RawKernel(KERNEL, 'score_gpu'); kernel.compile()
    cp.cuda.Stream.null.synchronize()
    init_ms = (time.perf_counter()-init)*1000
    transfer = time.perf_counter()
    d_offsets, d_peers, d_starts = map(cp.asarray, (offsets, peers, starts))
    cp.cuda.Stream.null.synchronize()
    static_upload_ms = (time.perf_counter()-transfer)*1000
    rows = []
    for count in [1, 32, 256, len(base), 16384, 65536]:
        poses = np.ascontiguousarray(base[np.arange(count) % len(base)])
        expected = oracle[np.arange(count) % len(base)]
        output = np.empty(count, dtype=np.int64)
        d_poses = cp.asarray(poses); d_output = cp.empty(count, dtype=cp.int64)
        def cpu_run():
            fn(offsets, peers, starts, len(offsets), poses, count, output)
            return output
        def launch():
            kernel(((count+127)//128,), (128,),
                   (d_offsets, d_peers, d_starts, np.int32(len(offsets)),
                    d_poses, np.int32(count), d_output))
        def resident():
            launch(); cp.cuda.Stream.null.synchronize()
        def transferred():
            d_poses.set(poses); launch(); return d_output.get()
        for _ in range(2):
            cpu_run(); transferred()
        timings = {k: [] for k in ('cpp', 'numpy', 'gpu_resident_wall', 'gpu_transfer_wall', 'gpu_event')}
        for repeat in range(7):
            order = ['cpp', 'numpy', 'gpu_transfer_wall', 'gpu_resident_wall']
            if repeat % 2: order.reverse()
            for name in order:
                call = {'cpp': cpu_run, 'numpy': lambda: score_numpy(offsets, peers, starts, poses),
                        'gpu_transfer_wall': transferred, 'gpu_resident_wall': resident}[name]
                ms, value = timed(call); timings[name].append(ms)
                if value is not None: np.testing.assert_array_equal(value, expected)
            begin, end = cp.cuda.Event(), cp.cuda.Event()
            begin.record(); launch(); end.record(); end.synchronize()
            timings['gpu_event'].append(float(cp.cuda.get_elapsed_time(begin, end)))
            np.testing.assert_array_equal(d_output.get(), expected)
        rows.append({'count': count, 'unique_queries': min(count, len(base)),
                     'best_index': int(np.argmin(expected)), 'all_scores_exact': True,
                     'timings': {k: stats(v) for k,v in timings.items()}})
    props = cp.cuda.runtime.getDeviceProperties(0)
    report = {'schema': 'agent510.gpu-placement-experiment/1',
              'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
              'python': platform.python_version(), 'numpy': np.__version__, 'cupy': cp.__version__,
              'gpu': props['name'].decode(), 'cuda_runtime': cp.cuda.runtime.runtimeGetVersion(),
              'cuda_driver': cp.cuda.runtime.driverGetVersion(),
              'source_pads': len(offsets), 'peer_entries': len(peers),
              'host_prepare_ms': prep_ms, 'cuda_context_compile_ms': init_ms,
              'static_upload_ms': static_upload_ms, 'results': rows,
              'limits': 'Scores only; candidate lattice unqualified for legality. Counts above1156 repeat queries for throughput, not additional board opportunities. Resident timing excludes transfers; transfer wall includes pose upload and all score download. Static peer upload and host packing separate.'}
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({r['count']: {k: v['median_ms'] for k,v in r['timings'].items()} for r in rows}, indent=2))

if __name__ == '__main__': main()

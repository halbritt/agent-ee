#!/usr/bin/env python3
"""Research-only CPU/CUDA pattern benchmark, no board writes.

Usage: python route_patterns.py mask.npz --output results.json
       python route_patterns.py --self-test
Requires NumPy, g++, and CuPy for the normal benchmark. NPZ keys: blocked uint8
HxW (0/1), costs int32 HxW (positive final cost, e.g. 10+history), queries int32
Nx4 (sx,sy,tx,ty). An optional same-stem .json sidecar is retained as metadata.
Eight candidates per query: two L and six Z using quarter/half/three-quarter
bbox splits. Every traversed cell costs its input value, including source once;
segment junctions count once. Revisits, if any, count again. Blocked => INF.
Dijkstra is a 4-neighbor immutable-map coverage oracle, not a speed baseline.
"""
import argparse
import ctypes
import hashlib
import heapq
import json
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np

INF = 0x3FFFFFFFFFFFFFFF
HERE = Path(__file__).resolve().parent


def patterns(queries):
    result = []
    for sx, sy, tx, ty in queries.tolist():
        s, t = (sx, sy), (tx, ty)
        row = [[s, (tx, sy), t, t], [s, (sx, ty), t, t]]
        for k in (1, 2, 3):
            x = sx + (tx - sx) * k // 4
            y = sy + (ty - sy) * k // 4
            row.extend([[s, (x, sy), (x, ty), t],
                        [s, (sx, y), (tx, y), t]])
        result.append(row)
    return np.ascontiguousarray(result, dtype=np.int32).reshape(-1, 8, 4, 2)


def load_data(path):
    with np.load(path, allow_pickle=False) as z:
        b, c, q = (z[k] for k in ('blocked', 'costs', 'queries'))
    if b.dtype != np.uint8 or c.dtype != np.int32 or q.dtype != np.int32:
        raise ValueError('expected blocked uint8, costs int32, queries int32')
    if b.ndim != 2 or c.shape != b.shape or q.ndim != 2 or q.shape[1] != 4 or len(q) == 0:
        raise ValueError('expected nonempty HxW maps and Nx4 queries')
    h, w = b.shape
    if h == 0 or w == 0 or h*w > 2**31-1 or np.any(b > 1) or np.any(c <= 0):
        raise ValueError('invalid grid dimensions, occupancy or nonpositive costs')
    if np.any(q < 0) or np.any(q[:, (0, 2)] >= w) or np.any(q[:, (1, 3)] >= h):
        raise ValueError('query outside map')
    # At most three Manhattan segments plus the source are scored.
    if (3*(h+w)+1)*int(c.max()) >= INF:
        raise ValueError('cost bound exceeds int64 sentinel')
    return tuple(np.ascontiguousarray(a) for a in (b, c, q))


def compile_cpu(directory):
    libpath = Path(directory) / 'patterns.so'
    subprocess.run(['g++', '-O3', '-std=c++17', '-shared', '-fPIC',
                    str(HERE/'route_patterns.cpp'), '-o', str(libpath)], check=True)
    lib = ctypes.CDLL(str(libpath))
    fn = lib.score_patterns
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                   ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    fn.restype = None

    def score(b, c, p, out=None):
        if out is None:
            out = np.empty(p.shape[:2], dtype=np.int64)
        fn(b.ctypes.data, c.ctypes.data, *b.shape, p.ctypes.data,
           out.size, out.ctypes.data)
        return out
    return score


def reference(b, c, paths):
    out = []
    for p in paths.reshape(-1, 4, 2):
        x, y = map(int, p[0])
        value = int(c[y, x]) if not b[y, x] else INF
        for tx, ty in p[1:].tolist():
            dx, dy = int(np.sign(tx-x)), int(np.sign(ty-y))
            assert not (dx and dy)
            while x != tx or y != ty:
                x, y = x+dx, y+dy
                if b[y, x]:
                    value = INF
                elif value != INF:
                    value += int(c[y, x])
        out.append(value)
    return np.array(out, dtype=np.int64).reshape(paths.shape[:2])


def dijkstra(b, c, query, max_visits):
    sx, sy, tx, ty = map(int, query)
    if b[sy, sx] or b[ty, tx]:
        return {'status': 'unreachable', 'cost': None, 'visited': 0}
    initial = int(c[sy, sx])
    queue, distances, visited = [(initial, sx, sy)], {(sx, sy): initial}, 0
    while queue:
        cost, x, y = heapq.heappop(queue)
        if cost != distances[(x, y)]:
            continue
        visited += 1
        if (x, y) == (tx, ty):
            return {'status': 'found', 'cost': cost, 'visited': visited}
        if visited >= max_visits:
            return {'status': 'visit_limit', 'cost': None, 'visited': visited}
        for nx, ny in ((x-1,y), (x+1,y), (x,y-1), (x,y+1)):
            if 0 <= ny < b.shape[0] and 0 <= nx < b.shape[1] and not b[ny,nx]:
                nc = cost + int(c[ny,nx])
                if nc < distances.get((nx,ny), INF):
                    distances[(nx,ny)] = nc
                    heapq.heappush(queue, (nc,nx,ny))
    return {'status': 'unreachable', 'cost': None, 'visited': visited}


def self_test(score):
    rng = np.random.default_rng(508)
    for h, w in ((1,1), (3,7), (12,9)):
        b = (rng.random((h,w)) < .2).astype(np.uint8)
        c = rng.integers(10,50,(h,w), dtype=np.int32)
        q = np.column_stack([rng.integers(w,size=50), rng.integers(h,size=50),
                             rng.integers(w,size=50), rng.integers(h,size=50)]).astype(np.int32)
        p = patterns(q)
        np.testing.assert_array_equal(score(b,c,p), reference(b,c,p))
    # Every bbox pattern crosses the wall, but a path around its upper end exists.
    b = np.zeros((9,9),np.uint8); b[2:7,4] = 1
    c = np.full(b.shape,10,np.int32)
    q = np.array([[1,4,7,4]],np.int32)
    assert np.all(score(b,c,patterns(q)) == INF)
    assert dijkstra(b,c,q[0],1000)['status'] == 'found'
    b[:,4] = 1
    assert dijkstra(b,c,q[0],1000)['status'] == 'unreachable'
    b[:] = 0
    q = np.array([[2,3,2,3],[0,0,8,8]],np.int32)
    assert score(b,c,patterns(q))[0,0] == 10
    assert score(b,c,patterns(q))[1,0] == 170
    for _, b, c, q in correctness_cases():
        p = patterns(q)
        np.testing.assert_array_equal(score(b,c,p), reference(b,c,p))


def correctness_cases():
    """Small adversarial inputs independent of the exported board's costs."""
    queries = np.array([[0,0,6,4], [6,4,0,0], [2,3,2,3], [0,2,6,2]],np.int32)
    blocked = np.zeros((5,7),np.uint8)
    costs = (10 + np.arange(35,dtype=np.int32).reshape(5,7)*3)
    yield 'nonuniform_weighted_reverse_degenerate', blocked, costs, queries
    costs = np.full((5,7),10,np.int32)
    yield 'uniform_deterministic_ties', blocked, costs, queries
    yield 'all_blocked', np.ones_like(blocked), costs, queries
    wall = blocked.copy(); wall[:,3] = 1
    yield 'free_endpoints_unreachable_wall', wall, costs, queries[:2]
    partial_wall = blocked.copy(); partial_wall[1:4,3] = 1
    yield 'no_pattern_but_detour_exists', partial_wall, costs, queries[3:]


def gpu_self_test(cp, kernel, score):
    """Fail before timings on any candidate-cost or deterministic-choice mismatch."""
    summaries = []
    for name, b, c, q in correctness_cases():
        p = patterns(q)
        expected = reference(b,c,p)
        np.testing.assert_array_equal(score(b,c,p),expected)
        db, dc, dp = cp.asarray(b), cp.asarray(c), cp.asarray(p)
        dout = cp.empty(expected.shape,np.int64)
        kernel(((expected.size+127)//128,), (128,),
               (db,dc,np.int32(b.shape[0]),np.int32(b.shape[1]),dp,
                np.int32(expected.size),dout))
        cp.cuda.get_current_stream().synchronize()
        actual = cp.asnumpy(dout)
        np.testing.assert_array_equal(actual,expected)
        np.testing.assert_array_equal(actual.argmin(axis=1),expected.argmin(axis=1))
        if name == 'nonuniform_weighted_reverse_degenerate':
            assert np.unique(expected[0]).size > 1
            assert actual[2,0] == c[3,2]
        elif name == 'uniform_deterministic_ties':
            assert np.all(actual == actual[:, :1])
            assert np.all(actual.argmin(axis=1) == 0)
        elif name in ('all_blocked', 'free_endpoints_unreachable_wall'):
            assert np.all(actual == INF)
            assert dijkstra(b,c,q[0],1000)['status'] == 'unreachable'
        elif name == 'no_pattern_but_detour_exists':
            assert np.all(actual == INF)
            assert dijkstra(b,c,q[0],1000)['status'] == 'found'
        summaries.append({'case': name, 'queries': len(q), 'patterns': expected.size,
                          'all_costs_equal_python_and_cpu': True,
                          'deterministic_best_indices': actual.argmin(axis=1).tolist()})
    return {'passed': True, 'cases': summaries,
            'scope': 'Synthetic CUDA scorer correctness; excluded from benchmark timing'}


def benchmark(path, score, oracle_limit):
    import cupy as cp
    b, c, queries = load_data(path)
    meta_path = path.with_suffix('.json')
    report = {'schema': 'pcb-pattern-benchmark/1', 'input': str(path),
              'input_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'map_sha256': hashlib.sha256(b.tobytes()+c.tobytes()).hexdigest(),
              'shape': list(b.shape), 'source_query_count': len(queries),
              'metadata': json.loads(meta_path.read_text()) if meta_path.exists() else None,
              'gpu': cp.cuda.runtime.getDeviceProperties(cp.cuda.Device().id)['name'].decode(),
              'cupy_version': cp.__version__, 'batches': []}
    report['cpu_compiler'] = subprocess.run(['g++','--version'],check=True,
        capture_output=True,text=True).stdout.splitlines()[0]
    report['cpu_flags'] = '-O3 -std=c++17 -shared -fPIC; single thread'
    report['timing_contract'] = ('Two warmups then seven repetitions; arm order alternates. '
        'CPU/GPU outputs and device path buffer preallocated; CPU wall includes ctypes; '
        'GPU total includes synchronized path H2D, launch/event setup, kernel and output D2H. '
        'Map H2D and compilation reported separately. Candidate packing excluded from both. '
        'No GPU reduction: best cost/index and equality checks run on host outside timing.')
    t = time.perf_counter()
    db, dc = cp.asarray(b), cp.asarray(c)
    cp.cuda.get_current_stream().synchronize()
    report['map_h2d_ms'] = (time.perf_counter()-t)*1000
    kernel = cp.RawKernel((HERE/'route_patterns.cpp').read_text(), 'score_patterns',
                          options=('-std=c++17',))
    # Explicit compilation to report cold compilation outside steady-state timing.
    t = time.perf_counter(); kernel.compile()
    report['kernel_compile_ms'] = (time.perf_counter()-t)*1000
    report['cuda_correctness'] = gpu_self_test(cp,kernel,score)

    for size in (1,32,256,1024):
        t = time.perf_counter()
        chosen = queries[np.arange(size) % len(queries)]
        p = patterns(chosen)
        packing_ms = (time.perf_counter()-t)*1000
        expected = score(b,c,p)
        cpu_out = np.empty_like(expected)
        # Independent scalar scorer checks cell sampling and collision semantics.
        np.testing.assert_array_equal(expected, reference(b,c,p))
        dp, dout = cp.empty(p.shape,np.int32), cp.empty(expected.shape,np.int64)
        records = []

        def gpu_run():
            start = time.perf_counter()
            dp.set(p); cp.cuda.get_current_stream().synchronize()
            transferred = time.perf_counter()
            e0, e1 = cp.cuda.Event(), cp.cuda.Event()
            e0.record()
            kernel(((expected.size+127)//128,), (128,),
                   (db,dc,np.int32(b.shape[0]),np.int32(b.shape[1]),dp,np.int32(expected.size),dout))
            e1.record(); e1.synchronize()
            finished = time.perf_counter()
            got = cp.asnumpy(dout)
            cp.cuda.get_current_stream().synchronize()
            ended = time.perf_counter()
            np.testing.assert_array_equal(got, expected)
            # argmin returns first occurrence, so deterministic ties use candidate index.
            np.testing.assert_array_equal(got.argmin(axis=1), expected.argmin(axis=1))
            return {'h2d_ms': (transferred-start)*1000,
                    'kernel_event_ms': cp.cuda.get_elapsed_time(e0,e1),
                    'kernel_wall_ms': (finished-transferred)*1000,
                    'd2h_ms': (ended-finished)*1000,
                    'total_ms': (ended-start)*1000}

        for rep in range(9):
            results = {}
            for arm in (('cpu','gpu') if rep%2 == 0 else ('gpu','cpu')):
                if arm == 'cpu':
                    start = time.perf_counter(); got = score(b,c,p,cpu_out)
                    elapsed = (time.perf_counter()-start)*1000
                    np.testing.assert_array_equal(got,expected)
                    results['cpu_ms'] = elapsed
                else:
                    results['gpu'] = gpu_run()
            if rep >= 2:
                records.append(results)
        best = expected.min(axis=1)
        report['batches'].append({'query_count': size, 'pattern_count': expected.size,
            'unique_queries': len(np.unique(chosen,axis=0)), 'packing_ms': packing_ms,
            'reachable_by_pattern': int(np.sum(best < INF)),
            'candidate_costs_sha256': hashlib.sha256(expected.tobytes()).hexdigest(),
            'best_candidate_indices': expected.argmin(axis=1).tolist(),
            'best_costs': [None if int(v) == INF else int(v) for v in best],
            'all_candidate_costs_equal': True, 'records': records,
            'median_cpu_ms': float(np.median([r['cpu_ms'] for r in records])),
            'median_gpu_total_ms': float(np.median([r['gpu']['total_ms'] for r in records])),
            'median_gpu_kernel_ms': float(np.median([r['gpu']['kernel_event_ms'] for r in records]))})
    oracle = []
    for idx in np.linspace(0,len(queries)-1,min(10,len(queries)),dtype=int):
        q = queries[idx]
        result = dijkstra(b,c,q,oracle_limit)
        best = int(score(b,c,patterns(q[None,:])).min())
        result.update(query_index=int(idx), query=q.tolist(),
                      pattern_cost=None if best == INF else best)
        if result['status'] == 'found' and best < INF and best < result['cost']:
            raise AssertionError('pattern beat exact same-graph Dijkstra')
        oracle.append(result)
    report['oracle'] = oracle
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--oracle-max-visits',type=int,default=2_000_000)
    args = parser.parse_args()
    if args.oracle_max_visits <= 0:
        parser.error('--oracle-max-visits must be positive')
    if not args.self_test and (args.input is None or args.output is None):
        parser.error('input and --output are required unless --self-test')
    with tempfile.TemporaryDirectory(prefix='pcb-pattern-build-') as build:
        start = time.perf_counter(); score = compile_cpu(build)
        compile_ms = (time.perf_counter()-start)*1000
        self_test(score)
        if args.self_test:
            print('CPU scorer/reference and detour coverage tests passed')
            return
        report = benchmark(args.input,score,args.oracle_max_visits)
        report['cpu_compile_ms'] = compile_ms
        args.output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({'output':str(args.output),'batches':len(report['batches'])}))


if __name__ == '__main__':
    main()

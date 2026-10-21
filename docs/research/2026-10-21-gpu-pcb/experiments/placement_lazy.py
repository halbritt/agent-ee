"""Current lazy rank oracle on the exported artificial U23 lattice.
This is NOT _Search.scan: feasibility/locality admission and geometry costs absent.
Bound construction is timed; input loading and eager expected-order setup are not.
"""
import json
import statistics
import sys
import time
from placement_reference import load_workload, _pad_nets, _wire_cost, _wire_bounds, _ranked


def run(doc, fp, peers, grounds, count):
    calls = 0
    bounds = []
    for rotation in sorted({q['rotation_deg'] for q in doc['queries']}):
        candidates = [q for q in doc['queries'] if q['rotation_deg'] == rotation]
        xs = sorted({q['position_nm'][0] for q in candidates})
        ys = sorted({q['position_nm'][1] for q in candidates})
        bx, by = _wire_bounds(_pad_nets(fp, (0, 0), rotation, grounds), peers, xs, ys)
        bounds.extend((bx[q['position_nm'][0]] + by[q['position_nm'][1]],
                       *q['position_nm'], rotation) for q in candidates)
    def exact(key):
        nonlocal calls
        calls += 1
        _, x, y, rotation = key
        return (_wire_cost(_pad_nets(fp, (x, y), rotation, grounds), peers), x, y, rotation)
    ranks = _ranked(bounds, exact)
    result = [next(ranks) for _ in range(count)]
    return result, calls


def main():
    doc, fp, peers, grounds = load_workload(sys.argv[1])
    expected = sorted((score, *q['position_nm'], q['rotation_deg'])
                      for q, score in zip(doc['queries'], doc['expected_score_nm']))
    rows = []
    for count in [1, 10, 100]:
        for _ in range(2):
            result, calls = run(doc, fp, peers, grounds, count)
            assert result == expected[:count]
        times = []
        call_counts = []
        for _ in range(7):
            start = time.perf_counter()
            result, calls = run(doc, fp, peers, grounds, count)
            times.append(time.perf_counter() - start)
            assert result == expected[:count]
            call_counts.append(calls)
        rows.append(dict(emitted=count, exact_calls=call_counts, times_s=times,
                         median_s=statistics.median(times), matches_eager_order=True))
    print(json.dumps(dict(mode='current-python-lazy-lattice', warmups=2, reps=7,
                         bound_construction_included=True, rows=rows)))


if __name__ == '__main__':
    main()

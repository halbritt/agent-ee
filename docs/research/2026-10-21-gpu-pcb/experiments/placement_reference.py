"""Extracted current agent-ee placement score oracle; no geometry admission implied.
Source: src/agent_ee/placer.py at 53e4d10f6c9a8a94867fd6e329bc466e85d9ea97
Whole source SHA256: a2b37ba8b4f38319bf3216005fd268217ebf5e7c6a075ee6341924f64e8292c2
The extracted functions below are unchanged, including their docstrings.
"""
import bisect
import heapq
import json
import sys
import time
import statistics
def rotate(point, quarter_turns):
    x, y = point
    for _ in range(quarter_turns % 4):
        x, y = y, -x  # KiCad positive rotation, in board coordinates.
    return [x, y]

def _pad_nets(fp, position, rotation, grounds):
    """Signal pad positions exactly as transform() moves them."""
    origin, turns = fp['position_nm'], int((rotation-fp['rotation_deg']) / 90)
    out = []
    for pad in fp['pads']:
        if pad['net'] and pad['net'] not in grounds:
            x, y = rotate([pad['position_nm'][0]-origin[0], pad['position_nm'][1]-origin[1]], turns)
            out.append((pad['net'], x+position[0], y+position[1]))
    return out

def _wire_cost(pads, peers):
    # Integer Manhattan pad-to-nearest-same-net-pad cost: deterministic ties.
    cost = 0
    for net, x, y in pads:
        if net in peers:
            cost += min(abs(x-a) + abs(y-b) for a, b in peers[net])
    return cost

def _wire_bounds(signal, peers, xs, ys):
    """Lower bounds on _wire_cost split by axis, one value per candidate axis value.

    ``signal`` holds the at-origin signal pads of one rotation. A pad's nearest
    same-net peer is at least as far as the nearest peer x coordinate plus the
    nearest peer y coordinate (each a minimum over all peers), so the sum over
    pads bounds the cost from below and separates into a function of x and a
    function of y, found by bisection in the sorted peer coordinates (AGENTEE-332).
    """
    along_x = [(ox, sorted({a for a, _ in peers[net]})) for net, ox, _ in signal if net in peers]
    along_y = [(oy, sorted({b for _, b in peers[net]})) for net, _, oy in signal if net in peers]
    def nearest(value, coordinates):
        i = bisect.bisect_left(coordinates, value)
        return min(abs(value-c) for c in coordinates[max(0, i-1):i+1])
    def bound(values, spans):
        return {v: sum(nearest(o+v, coordinates) for o, coordinates in spans) for v in values}
    return bound(xs, along_x), bound(ys, along_y)

def _ranked(bounds, exact):
    """Keys in exact order, resolving each exact key only when its entry may be next.

    Every bound compares at most equal to its exact key and no two entries share
    a key (candidate x, y and rotation are part of it), so a popped exact key
    precedes every remaining entry, whose exact key is at least its own bound.
    The sequence equals sorted(map(exact, bounds)).
    """
    heap = [(key, False) for key in bounds]
    heapq.heapify(heap)
    while heap:
        key, resolved = heapq.heappop(heap)
        if resolved:
            yield key
        else:
            heapq.heappush(heap, (exact(key), True))

def load_workload(path):
    doc = json.load(open(path))
    target = doc['queries'][0]['ref']
    fp = next(f for f in doc['footprints'] if f['ref'] == target)
    grounds = set(doc['ground_nets'])
    peers = {}
    for other in doc['footprints']:
        if other['ref'] == target:
            continue
        for pad in other['pads']:
            if pad['net'] and pad['net'] not in grounds:
                peers.setdefault(pad['net'], []).append(pad['position_nm'])
    return doc, fp, peers, grounds


def eager(doc, fp, peers, grounds):
    return [_wire_cost(_pad_nets(fp, q['position_nm'], q['rotation_deg'], grounds), peers)
            for q in doc['queries']]


def main():
    doc, fp, peers, grounds = load_workload(sys.argv[1])
    for _ in range(2):
        assert eager(doc, fp, peers, grounds) == doc['expected_score_nm']
    times = []
    for _ in range(7):
        start = time.perf_counter()
        actual = eager(doc, fp, peers, grounds)
        times.append(time.perf_counter() - start)
        assert actual == doc['expected_score_nm']
    print(json.dumps({'mode': 'current-python-eager', 'queries': len(actual),
                      'warmups': 2, 'reps': 7, 'times_s': times,
                      'median_s': statistics.median(times), 'all_scores_exact': True}))


if __name__ == '__main__':
    main()

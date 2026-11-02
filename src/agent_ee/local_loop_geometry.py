"""Centreline paths with explicit filled-plane travel; no distance-only passes."""
import heapq
import math


def shortest(adjacency, start, end, blocked=()):
    best, previous, queue = {start: 0.0}, {}, [(0.0, start)]
    blocked = set(blocked)
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != best[node]:
            continue
        if node == end:
            path = [end]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return distance, list(reversed(path))
        for other, length in adjacency.get(node, {}).items():
            candidate = distance + length
            if other not in blocked and candidate < best.get(other, math.inf):
                best[other], previous[other] = candidate, node
                heapq.heappush(queue, (candidate, other))
    return None


def project(p, a, b):
    delta = [y - x for x, y in zip(a, b)]
    square = sum(d * d for d in delta)
    t = max(0, min(1, sum((x - y) * d for x, y, d in zip(p, a, delta)) / square)) if square else 0
    return tuple(x + t * d for x, d in zip(a, delta))


def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def minus(a, b):
    return a[0] - b[0], a[1] - b[1]


def intersections(a, b, c, d):
    u, v = minus(b, a), minus(d, c)
    denominator = cross(u, v)
    if denominator:
        t, s = cross(minus(c, a), v) / denominator, cross(minus(c, a), u) / denominator
        if 0 <= t <= 1 and 0 <= s <= 1:
            return [t]
    else:
        square = sum(x * x for x in u)
        if square and abs(cross(minus(c, a), u)) < 1:
            return [max(0, min(1, sum(x * y for x, y in zip(minus(p, a), u)) / square)) for p in (c, d)]
    return []


def closest(first, second):
    a, b = first
    c, d = second
    ts = intersections(a, b, c, d)
    if ts:
        t = ts[0]
        p = tuple(x + t * (y - x) for x, y in zip(a, b))
        if math.dist(p, project(p, c, d)) < 1:
            return p, p
    candidates = [(a, project(a, c, d)), (b, project(b, c, d)),
                  (project(c, a, b), c), (project(d, a, b), d)]
    return min(candidates, key=lambda pair: math.dist(*pair))


def edges(ring):
    return zip(ring, ring[1:] + ring[:1])


def contains(p, ring):
    inside = False
    for a, b in edges(ring):
        if math.dist(p, project(p, a, b)) < .01:
            return True
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def in_copper(point, polygon):
    if not contains(point, polygon['outline']):
        return False
    for hole in polygon['holes']:
        if contains(point, hole) and not any(math.dist(point, project(point, a, b)) < .01 for a, b in edges(hole)):
            return False
    return True


def inside_segment(a, b, polygon):
    """Every interval between boundary intersections must lie in filled copper."""
    if not in_copper(a, polygon) or not in_copper(b, polygon):
        return False
    cuts = {0., 1.}
    for ring in [polygon['outline'], *polygon['holes']]:
        for c, d in edges(ring):
            if max(c[0], d[0]) < min(a[0], b[0]) or min(c[0], d[0]) > max(a[0], b[0]):
                continue
            if max(c[1], d[1]) < min(a[1], b[1]) or min(c[1], d[1]) > max(a[1], b[1]):
                continue
            cuts.update(intersections(a, b, c, d))
    cuts = sorted(cuts)
    return all(in_copper(tuple(x + (u + v) / 2 * (y - x) for x, y in zip(a, b)), polygon)
               for u, v in zip(cuts, cuts[1:]))


def plane_path(a, b, polygon):
    if inside_segment(a, b, polygon):
        return [a, b]
    # Exact visibility along the retained fill's polygon boundaries. A finite
    # population refusal is unknown, never a straight-line substitute across a void.
    points = sorted({tuple(a), tuple(b), *(tuple(p) for ring in [polygon['outline'], *polygon['holes']] for p in ring)})
    if len(points) > 512:
        raise ValueError('filled-plane visibility exceeds 512 vertices')
    graph = {i: {} for i in range(len(points))}
    for i, p in enumerate(points):
        for j in range(i):
            q = points[j]
            if inside_segment(p, q, polygon):
                graph[i][j] = graph[j][i] = math.dist(p, q)
    found = shortest(graph, points.index(tuple(a)), points.index(tuple(b)))
    return [points[i] for i in found[1]] if found else None

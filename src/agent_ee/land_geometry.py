"""Exact rational polygon Booleans and certified land-pattern area bounds.

Pure computation over already registered records. No parser, tool execution,
source authentication, or acceptance/evidence authority lives here.
"""
from dataclasses import dataclass
from fractions import Fraction
import re

ALGORITHM = 'agent-ee.land-geometry/1'
_DECIMAL = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z')


class GeometryError(ValueError):
    def __init__(self, mode, message, execution='unsupported'):
        super().__init__(message)
        self.mode = mode
        self.execution = execution


def _malformed(message):
    raise GeometryError('malformed_geometry', message, 'malformed')


@dataclass(frozen=True)
class Limits:
    max_segments: int = 2048
    max_pair_checks: int = 5_000_000
    max_slabs: int = 20000
    max_match_states: int = 100000
    max_pads: int = 128
    max_numeric_bits: int = 4096


class _Budget:
    def __init__(self, limits):
        self.limits = limits if limits is not None else Limits()
        if not isinstance(self.limits, Limits) or any(
                type(v) is not int or v < 1 for v in vars(self.limits).values()):
            _malformed('limits must contain positive integers')
        self.counts = {}

    def spend(self, name, count=1):
        self.counts[name] = self.counts.get(name, 0) + count
        if self.counts[name] > getattr(self.limits, name):
            raise GeometryError('work_limit', f'{name} exhausted')

    def number(self, value, *, rational=False):
        if rational and isinstance(value, Fraction):
            result = value
        elif type(value) is int:
            if value.bit_length() > self.limits.max_numeric_bits:
                raise GeometryError('work_limit', 'numeric input too large')
            result = Fraction(value)
        elif isinstance(value, str) and len(value) <= self.limits.max_numeric_bits:
            if not _DECIMAL.fullmatch(value):
                _malformed('numbers must be finite decimal strings or integers')
            result = Fraction(value)
        else:
            _malformed('numbers must be finite decimal strings or integers')
        return self.checked(result)

    def checked(self, value):
        if max(value.numerator.bit_length(), value.denominator.bit_length()) > self.limits.max_numeric_bits:
            raise GeometryError('work_limit', 'rational arithmetic size exhausted')
        return value


def _edges(poly):
    return zip(poly, poly[1:] + poly[:1])


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def _intersection_x(a, b, c, d, budget):
    rx, ry = b[0]-a[0], b[1]-a[1]
    sx, sy = d[0]-c[0], d[1]-c[1]
    det = rx*sy-ry*sx
    if det == 0:
        return None  # Collinear endpoints already split the sweep.
    qx, qy = c[0]-a[0], c[1]-a[1]
    t, u = (qx*sy-qy*sx)/det, (qx*ry-qy*rx)/det
    if 0 <= t <= 1 and 0 <= u <= 1:
        return budget.checked(a[0]+t*rx)
    return None


def _boolean_area(groups, predicate, budget):
    """Each group is a union; predicate selects their Boolean combination."""
    polygons, group_ids = [], []
    for group_id, group in enumerate(groups):
        for poly in sorted(set(tuple(p) for p in group)):
            polygons.append(poly)
            group_ids.append(group_id)
    segments = [(a, b, i) for i, p in enumerate(polygons) for a, b in _edges(p)]
    if len(segments) > budget.limits.max_segments:
        raise GeometryError('work_limit', 'max_segments exhausted')
    xs = {p[0] for poly in polygons for p in poly}
    for i, (a, b, _) in enumerate(segments):
        for c, d, _ in segments[i+1:]:
            budget.spend('max_pair_checks')
            x = _intersection_x(a, b, c, d, budget)
            if x is not None:
                xs.add(x)
    xs = sorted(xs)
    area = Fraction(0)
    for xl, xr in zip(xs, xs[1:]):
        budget.spend('max_slabs')
        mid = (xl+xr)/2
        crossings = []
        for a, b, i in segments:
            budget.spend('max_pair_checks')
            if min(a[0], b[0]) < mid < max(a[0], b[0]):
                slope = (b[1]-a[1])/(b[0]-a[0])
                intercept = a[1]-slope*a[0]
                crossings.append((budget.checked(slope*mid+intercept), slope, intercept, i))
        crossings.sort()
        inside, counts = set(), [0]*len(groups)
        lower = None
        j = 0
        while j < len(crossings):
            y, slope, intercept, _ = crossings[j]
            if lower is not None and predicate([n > 0 for n in counts]):
                ls, li = lower
                area += ((slope-ls)*(xl+xr)+2*(intercept-li))*(xr-xl)/2
                budget.checked(area)
            while j < len(crossings) and crossings[j][0] == y:
                i = crossings[j][3]
                group = group_ids[i]
                if i in inside:
                    inside.remove(i)
                    counts[group] -= 1
                else:
                    inside.add(i)
                    counts[group] += 1
                j += 1
            lower = slope, intercept
    return area


def _polygons(raw, budget):
    if not isinstance(raw, (list, tuple)):
        _malformed('polygon collection must be a sequence')
    result = []
    count = 0
    for poly in raw:
        if not isinstance(poly, (list, tuple)) or len(poly) < 3:
            _malformed('polygon needs at least three vertices')
        count += len(poly)
        if count > budget.limits.max_segments:
            raise GeometryError('work_limit', 'max_segments exhausted')
        points = []
        for point in poly:
            if not isinstance(point, (tuple, list)) or len(point) != 2:
                _malformed('polygon vertex must be a coordinate pair')
            points.append(tuple(budget.number(v, rational=True) for v in point))
        if len(set(points)) != len(points):
            _malformed('repeated polygon vertex')
        if sum(a[0]*b[1]-a[1]*b[0] for a, b in _edges(points)) == 0:
            _malformed('zero polygon area')
        segments = list(_edges(points))
        for i, (a, b) in enumerate(segments):
            c = points[(i+2) % len(points)]
            if _cross(a, b, c) == 0 and (b[0]-a[0])*(c[0]-b[0])+(b[1]-a[1])*(c[1]-b[1]) < 0:
                _malformed('polygon edge reverses')
            for j in range(i+2, len(segments)):
                if i == 0 and j == len(segments)-1:
                    continue
                budget.spend('max_pair_checks')
                c, d = segments[j]
                turns = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
                if turns[0]*turns[1] <= 0 and turns[2]*turns[3] <= 0 and all(
                        max(min(a[k], b[k]), min(c[k], d[k])) <= min(max(a[k], b[k]), max(c[k], d[k])) for k in (0, 1)):
                    _malformed('self-intersecting polygon')
        result.append(tuple(points))
    return result


def polygon_area(polygons, *, subtract=(), limits=None):
    """Exact area of union(polygons) minus union(subtract), simple polygons."""
    budget = _Budget(limits)
    groups = _polygons(polygons, budget), _polygons(subtract, budget)
    return _boolean_area(groups, lambda s: s[0] and not s[1], budget)


def polygon_intersection_area(left, right, *, limits=None):
    """Exact area of intersection(union(left), union(right))."""
    budget = _Budget(limits)
    groups = _polygons(left, budget), _polygons(right, budget)
    return _boolean_area(groups, lambda s: s[0] and s[1], budget)


def _record(raw, required, optional=()):
    if not isinstance(raw, dict) or not set(required) <= raw.keys() or raw.keys() - set(required) - set(optional):
        _malformed(f'record requires {required}; optional {optional}')


def _pair(raw, budget):
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        _malformed('expected coordinate/size pair')
    return tuple(budget.number(v) for v in raw)


def _primitive(raw, budget):
    _record(raw, ('shape', 'center', 'size'), ('angle', 'radius'))
    shape = raw['shape']
    if shape not in ('rect', 'circle', 'oval', 'roundrect'):
        raise GeometryError('unsupported_shape', 'unsupported primitive shape')
    center, size = _pair(raw['center'], budget), _pair(raw['size'], budget)
    if min(size) <= 0:
        _malformed('primitive sizes must be positive')
    angle = budget.number(raw.get('angle', 0))
    if angle % 90:
        raise GeometryError('unsupported_rotation', 'only exact quarter turns are qualified')
    if shape == 'circle' and size[0] != size[1]:
        raise GeometryError('unsupported_shape', 'unequal circle axes are an unsupported ellipse')
    if shape == 'roundrect':
        if 'radius' not in raw:
            _malformed('roundrect requires radius')
        radius = budget.number(raw['radius'])
        if not 0 <= radius <= min(size)/2:
            _malformed('invalid roundrect radius')
    else:
        if 'radius' in raw:
            _malformed('radius is only allowed for roundrect')
        radius = 0 if shape == 'rect' else min(size)/2
    if (angle // 90) % 2:
        size = size[::-1]
    return center, size, radius


def _pattern(raw, budget):
    _record(raw, ('pads', 'holes'))
    if not isinstance(raw['pads'], list) or not isinstance(raw['holes'], list):
        _malformed('pads and holes must be lists')
    if len(raw['pads']) + len(raw['holes']) > budget.limits.max_pads:
        raise GeometryError('work_limit', 'max_pads exhausted')
    pads = []
    for pad in raw['pads']:
        _record(pad, ('number', 'layers', 'primitive'))
        if not isinstance(pad['number'], str) or not pad['number']:
            _malformed('conductive pads require explicit nonempty number strings')
        layers = pad['layers']
        if isinstance(layers, list) and len(layers) > budget.limits.max_segments:
            raise GeometryError('work_limit', 'layer inventory exceeds max_segments')
        if not isinstance(layers, list) or not layers or any(not isinstance(s, str) or not s for s in layers):
            _malformed('pad layers must be nonempty strings')
        if len(set(layers)) != len(layers):
            _malformed('duplicate pad layer')
        pads.append((pad['number'], tuple(sorted(layers)), _primitive(pad['primitive'], budget)))
    return tuple(pads), tuple(_primitive(h, budget) for h in raw['holes'])


def _hull(points):
    points = sorted(set(points))
    halves = []
    for sequence in (points, list(reversed(points))):
        half = []
        for p in sequence:
            while len(half) >= 2 and _cross(half[-2], half[-1], p) <= 0:
                half.pop()
            half.append(p)
        halves.append(half[:-1])
    return tuple(halves[0] + halves[1])


def _circle_envelopes(n, budget):
    if type(n) is not int or n < 1:
        _malformed('refinement must be a positive integer')
    if 4*n > budget.limits.max_segments:
        raise GeometryError('work_limit', 'refinement exceeds max_segments')
    inner = []
    for quarter in range(4):
        for j in range(n):
            t = Fraction(j, n)
            x, y = (1-t*t)/(1+t*t), 2*t/(1+t*t)
            for _ in range(quarter):
                x, y = -y, x
            inner.append((x, y))
    outer = []
    for a, b in _edges(inner):
        det = a[0]*b[1]-a[1]*b[0]
        outer.append((budget.checked((b[1]-a[1])/det), budget.checked((a[0]-b[0])/det)))
    return tuple(inner), tuple(outer)


def _envelope(primitive, circles, budget):
    (cx, cy), (w, h), radius = primitive
    hx, hy = w/2-radius, h/2-radius
    corners = [(sx*hx, sy*hy) for sx in (-1, 1) for sy in (-1, 1)]
    if radius == 0:
        poly = _hull([(cx+x, cy+y) for x, y in corners])
        return poly, poly
    result = []
    for circle in circles:
        poly = _hull([(budget.checked(cx+x+radius*u), budget.checked(cy+y+radius*v))
                      for x, y in corners for u, v in circle])
        result.append(poly)
    return tuple(result)


def _envelopes(pattern, n, budget):
    circles = _circle_envelopes(n, budget)
    pads, holes = pattern
    layers = sorted({layer for _, ls, _ in pads for layer in ls})
    copper = {layer: ([], []) for layer in layers}
    for _, ls, primitive in pads:
        inner, outer = _envelope(primitive, circles, budget)
        for layer in ls:
            copper[layer][0].append(inner)
            copper[layer][1].append(outer)
    apertures = ([], [])
    for primitive in holes:
        inner, outer = _envelope(primitive, circles, budget)
        apertures[0].append(inner)
        apertures[1].append(outer)
    return copper, apertures


def _rational(value):
    return {'numerator': value.numerator, 'denominator': value.denominator}


def _bounds(lo, hi):
    return {'lower': _rational(lo), 'upper': _rational(hi)}


def _net_area(land, holes, budget):
    return _boolean_area((land, holes), lambda s: s[0] and not s[1], budget)


def area_bounds(pattern, *, refinement=4, limits=None):
    """Certified net copper per layer and gross drilled-aperture union bounds."""
    budget = _Budget(limits)
    copper, holes = _envelopes(_pattern(pattern, budget), refinement, budget)
    result = {}
    for layer, (inner, outer) in copper.items():
        result[layer] = _bounds(_net_area(inner, holes[1], budget), _net_area(outer, holes[0], budget))
    return {'algorithm': ALGORITHM, 'refinement': refinement, 'copper': result,
            'holes': _bounds(_net_area(holes[0], [], budget), _net_area(holes[1], [], budget))}


def _perfect_matching(graph, budget, forced=None):
    n = len(graph)
    assigned = {}
    if forced is not None:
        assigned[forced[1]] = forced[0]

    def augment(left, seen):
        budget.spend('max_match_states')
        for right in graph[left]:
            budget.spend('max_match_states')
            if right in seen or (forced is not None and right == forced[1]):
                continue
            seen.add(right)
            if right not in assigned or augment(assigned[right], seen):
                assigned[right] = left
                return True
        return False

    for left in range(n):
        if forced is not None and left == forced[0]:
            continue
        if not augment(left, set()):
            return None
    return sorted((left, right) for right, left in assigned.items())


def _match_pins(left, right, tolerance, aliases, budget):
    lp, rp = left[0], right[0]
    if aliases is not None:
        if not isinstance(aliases, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in aliases.items()):
            _malformed('aliases must map nonempty pin strings to strings')
        # Also catch collisions with implicit identity mappings of unmapped pins.
        names = {pad[0] for pad in lp} | set(aliases)
        values = [aliases.get(name, name) for name in sorted(names)]
        if len(set(values)) != len(values):
            return {'mode': 'alias_conflict', 'pairs': []}
    aliases = aliases or {}
    if len(lp) != len(rp) or not lp:
        return {'mode': 'pin_mapping_unavailable', 'pairs': []}
    graph = []
    for _, layers, (center, _, _) in lp:
        neighbors = []
        for j, (_, other_layers, (other_center, _, _)) in enumerate(rp):
            budget.spend('max_match_states')
            if layers == other_layers and sum((a-b)**2 for a, b in zip(center, other_center)) <= tolerance**2:
                neighbors.append(j)
        graph.append(neighbors)
    pairs = _perfect_matching(graph, budget)
    if pairs is None:
        return {'mode': 'pin_mapping_unavailable', 'pairs': []}
    agrees = lambda i, j: aliases.get(lp[i][0], lp[i][0]) == rp[j][0]
    good = [[j for j in neighbors if agrees(i, j)] for i, neighbors in enumerate(graph)]
    conflict = _perfect_matching(good, budget) is None
    ambiguous = False
    for i, neighbors in enumerate(graph):
        for j in neighbors:
            if (i, j) in pairs:
                continue
            if _perfect_matching(graph, budget, (i, j)) is not None:
                ambiguous = True
                if conflict:
                    return {'mode': 'pin_conflict', 'pairs': []}
                if not agrees(i, j):
                    return {'mode': 'pin_mapping_unavailable', 'pairs': []}
    if conflict:
        return {'mode': 'pin_conflict', 'pairs': [list(p) for p in pairs]}
    # The chosen perfect matching may itself disagree even when another agrees.
    if any(not agrees(i, j) for i, j in pairs):
        return {'mode': 'pin_mapping_unavailable', 'pairs': []}
    return {'mode': 'pin_agreement', 'pairs': [] if ambiguous else [list(p) for p in pairs]}


def match_pins(left, right, *, tolerance='0', aliases=None, limits=None):
    """All admissible bijections under fixed registration, independent of numbers.

    A disagreement is proved only if no label-preserving bijection exists.
    Mixed agreeing/disagreeing admissible bijections leave identity unknown.
    """
    budget = _Budget(limits)
    left, right = _pattern(left, budget), _pattern(right, budget)
    tolerance = budget.number(tolerance)
    if tolerance < 0:
        _malformed('pin tolerance must be nonnegative')
    return _match_pins(left, right, tolerance, aliases, budget)


def _metric(left, right, threshold, budget, *, holes=False):
    # Each operand is (inner land, outer land, inner holes, outer holes).
    li, lo, lhi, lho = left
    ri, ro, rhi, rho = right
    il = _net_area(li, lho, budget)
    ir = _net_area(ri, rho, budget)
    ol = _net_area(lo, lhi, budget)
    or_ = _net_area(ro, rhi, budget)
    if ol == 0 and or_ == 0:
        if holes:
            return {'lower': None, 'upper': None, 'decision': 'not_applicable'}
        raise GeometryError('empty_copper', 'both net copper sets are empty on a represented layer')
    canonical = lambda group: sorted(set(tuple(p) for p in group))
    if all(canonical(a) == canonical(b) for a, b in zip(left, right)) and il > 0:
        lower = upper = Fraction(1)
    else:
        low_groups = li, lho, ri, rho
        high_groups = lo, lhi, ro, rhi
        both = lambda s: s[0] and not s[1] and s[2] and not s[3]
        either = lambda s: (s[0] and not s[1]) or (s[2] and not s[3])
        i_low = _boolean_area(low_groups, both, budget)
        i_high = _boolean_area(high_groups, both, budget)
        u_low = _boolean_area(low_groups, either, budget)
        u_high = _boolean_area(high_groups, either, budget)
        lower = i_low/u_high if u_high else Fraction(0)
        upper = min(Fraction(1), i_high/u_low) if u_low else Fraction(1)
        # Empty versus certified nonempty is exactly zero, even if the other
        # outer approximation overlaps the hole envelope in an uncertain way.
        if (ol == 0 and ir > 0) or (or_ == 0 and il > 0):
            lower = upper = Fraction(0)
    budget.checked(lower)
    budget.checked(upper)
    decision = 'pass' if lower > threshold else 'defect' if upper <= threshold else 'unknown'
    return dict(_bounds(lower, upper), decision=decision)


def compare_patterns(left, right, *, threshold='0.98', pin_tolerance='0',
                     levels=(2, 4, 8, 16), limits=None):
    """Compare all layers and apertures with strict certified IoU thresholds.

    Refinements must be nested (each is an integer multiple of its predecessor).
    Work counters persist across refinements. GeometryError never means pass.
    """
    budget = _Budget(limits)
    left, right = _pattern(left, budget), _pattern(right, budget)
    threshold, tolerance = budget.number(threshold), budget.number(pin_tolerance)
    if not 0 <= threshold <= 1 or tolerance < 0:
        _malformed('threshold must be in [0,1], pin tolerance nonnegative')
    if not isinstance(levels, (list, tuple)) or not levels or len(levels) > 32 or any(type(n) is not int or n < 1 for n in levels):
        _malformed('levels must be a nonempty bounded sequence of positive integers')
    if any(b <= a or b % a for a, b in zip(levels, levels[1:])):
        _malformed('refinement levels must be strictly increasing integer multiples')
    pins = _match_pins(left, right, tolerance, None, budget)
    for n in levels:
        lc, lh = _envelopes(left, n, budget)
        rc, rh = _envelopes(right, n, budget)
        layers = sorted(lc.keys() | rc.keys())
        if not layers:
            raise GeometryError('empty_copper', 'no conductive pads')
        copper = {}
        for layer in layers:
            li, lo = lc.get(layer, ([], []))
            ri, ro = rc.get(layer, ([], []))
            copper[layer] = _metric((li, lo, *lh), (ri, ro, *rh), threshold, budget)
        holes = _metric((*lh, [], []), (*rh, [], []), threshold, budget, holes=True)
        metrics = list(copper.values()) + [holes]
        if not any(m['decision'] == 'unknown' for m in metrics):
            break
    if pins['mode'] in ('pin_conflict', 'alias_conflict'):
        mode = pins['mode']
    elif any(m['decision'] == 'defect' for m in metrics):
        mode = 'low_accuracy'
    elif any(m['decision'] == 'unknown' for m in metrics):
        mode = 'numeric_uncertainty'
    else:
        mode = pins['mode']
    return {'algorithm': ALGORITHM, 'refinement': n, 'copper': copper, 'holes': holes,
            'pins': pins, 'mode': mode}

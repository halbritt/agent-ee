"""Exact straight bounds for circular courtyard primitives (AGENTEE-212).

A saved circle or arc never becomes a straight polygon by assertion. It is
replaced by two integer-nanometre chains whose relation to the true curve is
proved with exact rationals: an *outside* chain whose every segment stays at
or beyond the radius, and an *inside* chain whose every vertex stays at or
within it. Each chain also stays within the declared tolerance of the curve.
The courtyard region is then sandwiched between an inner (subset) and an outer
(superset) polygon. Floating point only proposes vertices; it never decides.
"""
from fractions import Fraction
import math

from .placement_predicates import point_segment2

# Declared maximum distance between either bound and the true curve. It equals
# pcbnew's ARC_HIGH_DEF (0.005 mm), the error KiCad itself allows when it turns
# arcs into polygons.
ARC_TOLERANCE_NM = 5000
MIN_FULL_CIRCLE_STEPS = 8


class CurveError(ValueError):
    pass


def _sqrt_bounds(square):
    """Rationals lo <= sqrt(square) <= hi, within 1/denominator of each other."""
    square = Fraction(square)
    p, q = square.numerator, square.denominator
    root = math.isqrt(p * q)
    lo = Fraction(root, q)
    return lo, lo if root * root == p * q else Fraction(root + 1, q)


def _dist2(p, c):
    return (p[0]-c[0])**2 + (p[1]-c[1])**2


def _repeats(chain):
    return any(a == b for a, b in zip(chain, chain[1:]))


def _radius(r2, tolerance):
    """The float radius of an exact squared radius proved larger than twice the tolerance.

    Runs before any step count: below that radius no chain fits within the
    tolerance and the step formula leaves its domain."""
    _, hi = _sqrt_bounds(r2)
    if hi <= 2 * tolerance:
        raise CurveError('curve radius not larger than twice the declared tolerance')
    return math.sqrt(r2)


def _steps(sweep, radius, tolerance, minimum):
    # Half-angle h with both r(1/cos h - 1) and r(1 - cos h) at most half the tolerance.
    h = min(math.acos(radius / (radius + tolerance / 2)), math.acos(1 - tolerance / (2 * radius)))
    return max(minimum, math.ceil(sweep / (2 * h)))


def _point(c, radius, angle):
    return (round(float(c[0]) + radius * math.cos(angle)), round(float(c[1]) + radius * math.sin(angle)))


def _outside(c, r2, angles, half_step, tolerance, ends=()):
    """Vertices at the given angles whose segments never enter the disk."""
    radius = math.sqrt(r2)
    lo, _ = _sqrt_bounds(r2)
    bump = 2
    while bump <= tolerance / 4:
        points = [_point(c, radius / math.cos(half_step) + bump, a) for a in angles]
        chain = [ends[0], *points, ends[1]] if ends else points + points[:1]
        if _repeats(chain):
            break
        if (all(point_segment2(c, a, b) >= r2 for a, b in zip(chain, chain[1:])) and
                all(_dist2(p, c) <= (lo + tolerance)**2 for p in points)):
            return points
        bump *= 2
    raise CurveError('no exact outside bound within the declared tolerance')


def _inside(c, r2, angles, tolerance, ends=()):
    """Vertices at the given angles on or inside the circle, chords within tolerance of it."""
    radius = _radius(r2, tolerance)
    _, hi = _sqrt_bounds(r2)
    bump = 2
    while bump <= tolerance / 4:
        points = [_point(c, radius - bump, a) for a in angles]
        chain = [ends[0], *points, ends[1]] if ends else points + points[:1]
        if _repeats(chain):
            break
        if (all(_dist2(p, c) <= r2 for p in points) and
                all(point_segment2(c, a, b) >= (hi - tolerance)**2 for a, b in zip(chain, chain[1:]))):
            return points
        bump *= 2
    raise CurveError('no exact inside bound within the declared tolerance')


def circle(center, end, tolerance=ARC_TOLERANCE_NM):
    """Outer and inner closed rings for a saved circle (centre and one point on it)."""
    c = tuple(Fraction(v) for v in center)
    r2 = _dist2(end, c)
    if r2 == 0:
        raise CurveError('zero-radius circle')
    radius = _radius(r2, tolerance)
    n = _steps(2 * math.pi, radius, tolerance, MIN_FULL_CIRCLE_STEPS)
    n += -n % 4
    step = 2 * math.pi / n
    angles = [k * step for k in range(n)]
    return ([list(p) for p in _outside(c, r2, angles, step / 2, tolerance)],
            [list(p) for p in _inside(c, r2, angles, tolerance)])


def _circumcentre(a, b, m):
    d = 2 * (a[0]*(m[1]-b[1]) + m[0]*(b[1]-a[1]) + b[0]*(a[1]-m[1]))
    if d == 0:
        raise CurveError('degenerate arc: start, mid and end are collinear')
    sa, sm, sb = (p[0]**2 + p[1]**2 for p in (a, m, b))
    return (Fraction(sa*(m[1]-b[1]) + sm*(b[1]-a[1]) + sb*(a[1]-m[1]), d),
            Fraction(sa*(b[0]-m[0]) + sm*(a[0]-b[0]) + sb*(m[0]-a[0]), d))


def arc(start, mid, end, tolerance=ARC_TOLERANCE_NM):
    """Interior chain vertices from start to end, outside and inside the arc's circle."""
    start, mid, end = tuple(start), tuple(mid), tuple(end)
    if start == end:
        raise CurveError('arc start equals end')
    c = _circumcentre(start, end, mid)
    r2 = _dist2(start, c)
    radius = _radius(r2, tolerance)
    turn = 2 * math.pi
    base = math.atan2(float(start[1]-c[1]), float(start[0]-c[0]))
    # Start, mid and end lie on the arc in order: its turning sense is the exact
    # orientation of that triangle (never zero once the circumcentre exists).
    direction = 1 if (mid[0]-start[0])*(end[1]-start[1]) - (mid[1]-start[1])*(end[0]-start[0]) > 0 else -1
    sweep = (direction * (math.atan2(float(end[1]-c[1]), float(end[0]-c[0])) - base)) % turn
    n = _steps(sweep, radius, tolerance, 1)
    step = sweep / n
    # Circumscribed vertices between the tangents at start, at each step and at end.
    outside = _outside(c, r2, [base + direction*(k+0.5)*step for k in range(n)], step / 2, tolerance, (start, end))
    inside = _inside(c, r2, [base + direction*k*step for k in range(1, n)], tolerance, (start, end))
    return [list(p) for p in outside], [list(p) for p in inside]

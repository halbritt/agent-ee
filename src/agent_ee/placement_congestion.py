"""Pre-route placement issues and congestion regions over a saved-board probe (AGENTEE-57).

Pure computation over an ``agent-ee.placement-probe/1`` capture and the saved
board's pad-net assignment, which is the forward-annotated netlist. The report
is advisory repair evidence. It never passes a board: proved pad, courtyard or
outline geometry is a defect, everything else is ``info``. Routing acceptance
stays with the router and native DRC.

Issue kinds come from agent-ee's own placement vocabulary (``placement.judge``
and ``placement_predicates``) plus pad copper clearance and ratsnest rotation.
The routing-difficulty mechanism (failure probability per region, 5/2/1 %
bands, deterministic ties, free space per direction) follows tscircuit
circuit-json-routing-analysis (MIT) as a reference only; no code was ported.
Demand is a rectangular uniform wire density (RUDY) spread of the pad-centre
Manhattan MST; capacity is declared track pitch over unblocked cell area; a
cell fails when Poisson demand exceeds its integer capacity. This estimate is
not calibrated against routed outcomes.
"""

from .check_registry import register as _register_check
import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import re

from .placement import clipped_area, intersection_area, judge, qualified_polygons, triangles, validate_capture
from .placement_predicates import exact_nm, ratsnest

SCHEMA = 'agent-ee.placement-congestion/1'
# Severity bands are derived from probability on every read; records never store them.
BANDS = (('high', 0.05), ('medium', 0.02), ('low', 0.01))
# KiCad board y grows downward, so +y is "down".
DIRECTIONS = (('up', (0, -1)), ('down', (0, 1)), ('left', (-1, 0)), ('right', (1, 0)))
KIND_ORDER = ('pad_overlap', 'courtyard_overlap', 'outside_outline', 'routing_congestion',
              'pad_clearance', 'ratsnest_rotation')
DEFECT_KINDS = frozenset(('pad_overlap', 'courtyard_overlap', 'outside_outline', 'routing_congestion'))
# pcbnew PAD_SHAPE values (KiCad 10.0.7).
CIRCLE, RECT, OVAL, TRAPEZOID, ROUNDRECT, CHAMFERED_RECT, CUSTOM = range(7)
COPPER = re.compile(r'(F|B|In[1-9][0-9]?)\.Cu\Z')
REQUIRED = ('clearance_mm', 'track_width_mm', 'cell_mm', 'routing_layers')
OPTIONAL = ('max_failure_probability', 'min_rotation_gain_mm')
QUARTER = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}
MIN_CELL_TRACKS = 4


def severity(probability):
    """Band for a failure probability, or None below the lowest band."""
    return next((name for name, floor in BANDS if probability >= floor), None)


def parameters(params):
    """Validate the declared analysis parameters; raises ValueError."""
    if not isinstance(params, dict):
        raise ValueError('placement_congestion params must be an object')
    unknown, missing = set(params) - set(REQUIRED) - set(OPTIONAL), set(REQUIRED) - set(params)
    if unknown or missing:
        raise ValueError(f'placement_congestion params: unknown {sorted(unknown)}, missing {sorted(missing)}')
    layers = params['routing_layers']
    if (not isinstance(layers, list) or not layers or len(set(layers)) != len(layers)
            or any(not isinstance(v, str) or not COPPER.fullmatch(v) for v in layers)):
        raise ValueError('routing_layers must list distinct copper layer names such as F.Cu')
    out = {'clearance_nm': exact_nm(params['clearance_mm'], 'clearance_mm'),
           'track_width_nm': exact_nm(params['track_width_mm'], 'track_width_mm'),
           'cell_nm': exact_nm(params['cell_mm'], 'cell_mm'), 'routing_layers': sorted(layers),
           'max_failure_probability': None, 'min_rotation_gain_nm': None}
    # Below a few tracks per cell, pad blockage becomes all-or-nothing and the tail meaningless.
    if out['cell_nm'] < MIN_CELL_TRACKS * (out['track_width_nm'] + out['clearance_nm']):
        raise ValueError(f'cell_mm must hold at least {MIN_CELL_TRACKS} track pitches (track_width_mm + clearance_mm)')
    if 'max_failure_probability' in params:
        value = params['max_failure_probability']
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1:
            raise ValueError('max_failure_probability must be a number strictly between 0 and 1')
        out['max_failure_probability'] = float(value)
    if 'min_rotation_gain_mm' in params:
        out['min_rotation_gain_nm'] = exact_nm(params['min_rotation_gain_mm'], 'min_rotation_gain_mm')
    return out


def capability(params):
    """Advisory: no acceptance predicate, so it can never discharge a requirement."""
    parameters(params)
    return []


def mm(nm):
    return round(nm / 10**6, 6)


def _text_mm(nm):
    value = f'{nm / 10**6:.3f}'.rstrip('0').rstrip('.')
    return value or '0'


# ------------------------------------------------------------------ geometry

def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def _turn(vector, degrees):
    """Board-frame rotation used by pcbnew (y down): exact at quarter turns."""
    x, y = vector
    quarter = degrees % 360
    if quarter % 90 == 0:
        cos, sin = QUARTER[int(quarter)]
    else:
        cos, sin = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return (x*cos + y*sin, -x*sin + y*cos)


def _pad_record(fp, pad):
    position, size, rotation = pad.get('position_nm'), pad.get('size_nm'), pad.get('rotation_deg')
    if (not isinstance(position, list) or len(position) != 2 or any(type(v) is not int for v in position)
            or not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v <= 0 for v in size)
            or isinstance(rotation, bool) or not isinstance(rotation, (int, float)) or not math.isfinite(rotation)
            or type(pad.get('shape')) is not int or not isinstance(pad.get('net'), str)
            or not isinstance(pad.get('layers'), list) or any(not isinstance(v, str) for v in pad['layers'])):
        raise ValueError(f"pad record of {fp['ref']} lacks integer position/size, rotation, shape, net or layers")
    return pad


def pad_shapes(pad, offset=(0, 0), turn=0, about=None):
    """(outer, inner) convex cores with radii, each None when the native shape is not modelled.

    ``outer`` contains the copper and ``inner`` lies inside it, so their signed
    distances bound the true clearance. A rounded rectangle is bounded by its
    rectangle and by the stadium of its maximum corner radius.
    """
    (cx, cy), (w, h), rotation = pad['position_nm'], pad['size_nm'], pad['rotation_deg'] + turn
    if turn:
        dx, dy = _turn((cx - about[0], cy - about[1]), turn)
        cx, cy = about[0] + dx, about[1] + dy
    cx, cy = cx + offset[0], cy + offset[1]

    def place(points):
        return tuple((cx + x, cy + y) for x, y in (_turn(p, rotation) for p in points))
    rect = (place([(-w/2, -h/2), (w/2, -h/2), (w/2, h/2), (-w/2, h/2)]), 0)
    half = abs(w - h) / 2
    stadium = (place([(-half, 0), (half, 0)] if w >= h else [(0, -half), (0, half)]), min(w, h) / 2)
    kind = pad['shape']
    if kind == CIRCLE:
        circle = (place([(0, 0)]), w / 2)
        return circle, circle
    if kind == RECT:
        return rect, rect
    if kind == OVAL:
        return stadium, stadium
    if kind == ROUNDRECT:
        return rect, stadium
    if kind == CHAMFERED_RECT:
        return rect, None
    return None, None


def _hull(points):
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def chain(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and _cross(out[-2], out[-1], p) <= 0:
                out.pop()
            out.append(p)
        return out
    lower, upper = chain(pts), chain(list(reversed(pts)))
    return lower[:-1] + upper[:-1]


def _hull_edges(hull):
    if len(hull) == 1:
        return [(hull[0], hull[0])]
    if len(hull) == 2:
        return [(hull[0], hull[1])]
    return list(zip(hull, hull[1:] + hull[:1]))


def _segment_distance(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    length2 = dx*dx + dy*dy
    t = 0 if length2 == 0 else max(0, min(1, ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / length2))
    return math.hypot(a[0] + t*dx - p[0], a[1] + t*dy - p[1])


def _signed(point, hull):
    """Signed distance from a point to a convex hull, negative inside."""
    edges = _hull_edges(hull)
    distance = min(_segment_distance(point, a, b) for a, b in edges)
    if len(hull) > 2 and all(_cross(a, b, point) >= 0 for a, b in edges):
        return -distance
    return distance


def _difference(left, right):
    return _hull([(p[0]-q[0], p[1]-q[1]) for p in left for q in right])


def clearance(left, right):
    """Signed distance between two cores-with-radius; negative is penetration depth."""
    return _signed((0, 0), _difference(left[0], right[0])) - left[1] - right[1]


def _line_interval(hull, reach):
    """Open interval of s with dist((s, 0), hull) < reach, or None.

    The intersection is convex, so its ends lie on the hull boundary offset by
    ``reach``: the union of edge capsules. With zero reach only the hull
    interior blocks; touching never does.
    """
    lows, highs = [], []
    if reach > 0:
        for a, b in _hull_edges(hull):
            for e in (a, b):
                if abs(e[1]) < reach:
                    half = math.sqrt(reach*reach - e[1]*e[1])
                    lows.append(e[0] - half)
                    highs.append(e[0] + half)
            ux, uy = b[0]-a[0], b[1]-a[1]
            length = math.hypot(ux, uy)
            if length == 0:
                continue
            beta, alpha = -uy / length, (uy*a[0] - ux*a[1]) / length
            if beta == 0:
                if abs(alpha) >= reach:
                    continue
                perp = (-math.inf, math.inf)
            else:
                perp = tuple(sorted(((-reach - alpha) / beta, (reach - alpha) / beta)))
            if ux == 0:
                s = -a[1]*uy / length**2
                if not 0 <= s <= 1:
                    continue
                along = (-math.inf, math.inf)
            else:
                along = tuple(sorted((a[0] + a[1]*uy/ux, a[0] + (length**2 + a[1]*uy)/ux)))
            lo, hi = max(perp[0], along[0]), min(perp[1], along[1])
            if lo < hi:
                lows.append(lo)
                highs.append(hi)
    if len(hull) > 2:
        crossings = []
        for a, b in _hull_edges(hull):
            if a[1] == b[1] == 0:
                crossings += [a[0], b[0]]
            elif (a[1] <= 0 <= b[1]) or (b[1] <= 0 <= a[1]):
                crossings.append(a[0] + (b[0]-a[0]) * (0 - a[1]) / (b[1] - a[1]))
        if crossings:
            lo, hi = min(crossings), max(crossings)
            if lo < hi and _signed(((lo + hi) / 2, 0), hull) < 0:
                lows.append(lo)
                highs.append(hi)
    return (min(lows), max(highs)) if lows else None


def _blocked(left, right, margin, direction):
    """Open interval of t at which ``left`` moved by t*direction is closer than margin to ``right``."""
    hull = _difference(left[0], right[0])
    vertical = direction[0] == 0
    if vertical:
        # Swapping axes reverses orientation; re-hull so interior tests stay valid.
        hull = _hull([(y, x) for x, y in hull])
    interval = _line_interval(hull, left[1] + right[1] + margin)
    if interval is None:
        return None
    lo, hi = interval
    # The moved difference contains the origin when the point -t*direction is inside.
    return (-hi, -lo) if (direction[1] if vertical else direction[0]) > 0 else (lo, hi)


def first_free(intervals):
    """Smallest t >= 0 outside every open interval."""
    t = 0.0
    for lo, hi in sorted(intervals):
        if lo < t < hi:
            t = hi
    return t


def free_distance(intervals):
    """Largest t >= 0 reachable before entering an interval."""
    starts = [max(lo, 0.0) for lo, hi in intervals if hi > 0]
    return min(starts) if starts else None


def _box(points, radius=0):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs) - radius, min(ys) - radius, max(xs) + radius, max(ys) + radius)


def _union(boxes):
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _gap(a, b):
    dx = max(b[0] - a[2], a[0] - b[2], 0)
    dy = max(b[1] - a[3], a[1] - b[3], 0)
    return math.hypot(dx, dy)


def _area(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _copper(pad):
    return {layer for layer in pad['layers'] if COPPER.fullmatch(layer)}


def _translate(polygon, vector):
    return [(x + vector[0], y + vector[1]) for x, y in polygon]


def _rotate_polygon(polygon, turn, about):
    out = []
    for x, y in polygon:
        dx, dy = _turn((x - about[0], y - about[1]), turn)
        out.append((about[0] + int(dx), about[1] + int(dy)))
    return out


def _inside(polygon, outline):
    area = Fraction(abs(sum(a[0]*b[1] - a[1]*b[0] for a, b in zip(polygon, polygon[1:] + polygon[:1]))), 2)
    return intersection_area(polygon, outline) == area


# ---------------------------------------------------------------- the board

class Board:
    """Normalized probe geometry: pads, courtyards, outline and free space."""

    def __init__(self, probe, settings):
        validate_capture(probe)
        self.probe, self.settings = probe, settings
        self.footprints = sorted(probe['footprints'], key=lambda f: f['ref'])
        self.unknown = []
        try:
            outline = qualified_polygons(probe.get('outline'))
            if len(outline) != 1:
                raise ValueError('multiple outline contours unsupported')
            self.outline = outline[0]
        except ValueError as exc:
            self.outline = None
            self.unknown.append({'kind': 'outline', 'reason': str(exc)})
        self.pads, self.courtyards = {}, {}
        for fp in self.footprints:
            if fp.get('side') not in ('F', 'B') or not isinstance(fp.get('pads'), list):
                raise ValueError(f"footprint {fp['ref']} lacks a supported side or pad inventory")
            entries = []
            for pad in fp['pads']:
                _pad_record(fp, pad)
                outer, inner = pad_shapes(pad)
                copper = _copper(pad)
                if copper and pad.get('attribute') != 'NPTH' and outer is None:
                    self.unknown.append({'kind': 'pad_shape_unsupported', 'ref': fp['ref'], 'pad': pad['number'],
                                         'shape': pad['shape'],
                                         'reason': 'native pad shape not modelled; its clearance is unknown'})
                entries.append({'pad': pad, 'outer': outer, 'inner': inner,
                                'copper': copper if pad.get('attribute') != 'NPTH' else set()})
            self.pads[fp['ref']] = entries
            record = (fp.get('courtyards') or {}).get(fp['side'])
            try:
                if record is None:
                    raise ValueError('courtyard missing on mounting side')
                self.courtyards[fp['ref']] = qualified_polygons(record)
            except ValueError as exc:
                self.courtyards[fp['ref']] = None
                self.unknown.append({'kind': 'courtyard', 'ref': fp['ref'], 'reason': str(exc)})
        self.by_ref = {fp['ref']: fp for fp in self.footprints}
        self.span = self._span()
        self._free = {}

    def _span(self):
        boxes = [self.extent(fp['ref']) for fp in self.footprints]
        if self.outline is not None:
            boxes.append(_box(self.outline))
        box = _union(boxes)
        return math.hypot(box[2] - box[0], box[3] - box[1]) + 1 if box else 1

    def extent(self, ref, offset=(0, 0)):
        if self.courtyards.get(ref):
            box = _union([_box(p) for p in self.courtyards[ref]])
        else:
            box = _union([_box(e['outer'][0], e['outer'][1]) if e['outer'] else
                          _box([e['pad']['position_nm']]) for e in self.pads[ref]])
        if box is None:
            x, y = self.by_ref[ref]['position_nm']
            box = (x, y, x, y)
        return (box[0] + offset[0], box[1] + offset[1], box[2] + offset[0], box[3] + offset[1])

    def margin(self, left, right):
        a, b = left['pad']['net'], right['pad']['net']
        return 0 if a and a == b else self.settings['clearance_nm']

    def hull_shapes(self, ref):
        return [(tuple(_hull(p)), 0) for p in self.courtyards[ref]]

    def free_space(self, ref):
        """Courtyard travel per direction before touching another courtyard or leaving the outline.

        Convex hulls are used, so the distance is conservative for concave
        courtyards. Pairs that already overlap are reported as issues and do
        not bound travel. Status unknown when any needed contour is unproved.
        """
        if ref in self._free:
            return self._free[ref]
        side = self.by_ref[ref]['side']
        others = [f['ref'] for f in self.footprints if f['ref'] != ref and f['side'] == side]
        reason = ('outline unproved' if self.outline is None else 'courtyard unproved' if not self.courtyards[ref]
                  else next((f'courtyard of {o} unproved' for o in others if not self.courtyards[o]), None))
        if reason:
            self._free[ref] = {'status': 'unknown', 'reason': reason}
            return self._free[ref]
        mine = self.hull_shapes(ref)
        obstacles = []
        for other in others:
            if any(intersection_area(a, b) > 0 for a in self.courtyards[ref] for b in self.courtyards[other]):
                continue
            obstacles.append(self.hull_shapes(other))
        edges = [((a, b), 0) for a, b in zip(self.outline, self.outline[1:] + self.outline[:1])]
        space = {}
        for name, direction in DIRECTIONS:
            intervals = [i for m in mine for group in obstacles for o in group
                         if (i := _blocked(m, o, 0, direction)) is not None]
            intervals += [i for m in mine for e in edges if (i := _blocked(m, e, 0, direction)) is not None]
            distance = free_distance(intervals)
            space[name] = mm(int(math.floor(min(distance, self.span) if distance is not None else self.span)))
        self._free[ref] = {'status': 'proven', **space}
        return self._free[ref]

    def part(self, ref):
        return {'ref': ref, 'locked': bool(self.by_ref[ref].get('locked')), 'free_space_mm': self.free_space(ref)}

    def nearby(self, refs):
        """Other parts within one cell of the given parts' extents, nearest first."""
        box = _union([self.extent(r) for r in refs])
        out = []
        for fp in self.footprints:
            if fp['ref'] in refs:
                continue
            gap = _gap(box, self.extent(fp['ref']))
            if gap <= self.settings['cell_nm']:
                out.append((round(gap), fp['ref']))
        return [{**self.part(ref), 'gap_mm': mm(gap)} for gap, ref in sorted(out)]

    # ------------------------------------------------------------ pad pairs

    def pad_pairs(self):
        """Every cross-footprint copper pad pair whose clearance may be below its margin."""
        entries = [(fp['ref'], e) for fp in self.footprints for e in self.pads[fp['ref']] if e['copper']]
        bounds = {}
        for ref, e in entries:
            if e['outer'] is not None:
                cx, cy = e['pad']['position_nm']
                bounds[id(e)] = max(math.hypot(x - cx, y - cy) for x, y in e['outer'][0]) + e['outer'][1]
        pairs = []
        for i, (ref, left) in enumerate(entries):
            for other, right in entries[i+1:]:
                if other == ref or not left['copper'] & right['copper']:
                    continue
                if left['outer'] is None or right['outer'] is None:
                    continue
                margin = self.margin(left, right)
                (ax, ay), (bx, by) = left['pad']['position_nm'], right['pad']['position_nm']
                if math.hypot(ax - bx, ay - by) > bounds[id(left)] + bounds[id(right)] + margin:
                    continue
                low = clearance(left['outer'], right['outer'])
                if low >= margin:
                    continue
                high = clearance(left['inner'], right['inner']) if left['inner'] and right['inner'] else None
                pairs.append({'refs': (ref, other), 'pads': (left, right), 'margin': margin,
                              'low': low, 'high': high})
        return pairs

    # ------------------------------------------------------------ moves

    def move(self, ref, reason_partner=None):
        """Smallest verified axis move of one part that clears pads, courtyards and the outline."""
        fp = self.by_ref[ref]
        if fp.get('locked'):
            return None, f'{ref} is locked'
        if self.outline is None:
            return None, 'outline unproved'
        if not self.courtyards[ref]:
            return None, f'courtyard of {ref} unproved'
        side = fp['side']
        others = [f['ref'] for f in self.footprints if f['ref'] != ref]
        blind = next((o for o in others if self.by_ref[o]['side'] == side and not self.courtyards[o]), None)
        if blind:
            return None, f'courtyard of {blind} unproved'
        mine = [e for e in self.pads[ref] if e['copper']]
        theirs = [e for o in others for e in self.pads[o] if e['copper']]
        if any(e['outer'] is None for e in mine + theirs):
            return None, 'a copper pad shape is not modelled'
        shells = self.hull_shapes(ref)
        edges = [((a, b), 0) for a, b in zip(self.outline, self.outline[1:] + self.outline[:1])]
        best = None
        for name, direction in DIRECTIONS:
            intervals = []
            for m in mine:
                for o in theirs:
                    if m['copper'] & o['copper']:
                        blocked = _blocked(m['outer'], o['outer'], self.margin(m, o), direction)
                        if blocked:
                            intervals.append(blocked)
            for o in others:
                if self.by_ref[o]['side'] == side:
                    intervals += [b for s in shells for h in self.hull_shapes(o)
                                  if (b := _blocked(s, h, 0, direction))]
            intervals += [b for s in shells for e in edges if (b := _blocked(s, e, 0, direction))]
            t = first_free(intervals)
            if t <= 0 or t > self.span:
                continue
            # Up to whole micrometres; one more micrometre only if float noise fails verification.
            distance = -(-round(t) // 1000) * 1000
            for distance in (distance, distance + 1000):
                vector = (direction[0] * distance, direction[1] * distance)
                if self.verify(ref, vector):
                    break
            else:
                continue
            if best is None or distance < best[0]:
                best = (distance, name, vector)
        if best is None:
            return None, 'no verified axis move inside the outline'
        distance, name, vector = best
        x, y = fp['position_nm']
        return {'op': 'move', 'reference': ref, 'direction': name, 'distance_mm': mm(distance),
                'dx_mm': mm(vector[0]), 'dy_mm': mm(vector[1]), 'x_mm': mm(x + vector[0]), 'y_mm': mm(y + vector[1]),
                'text': f'move {ref} {_text_mm(distance)} mm {name}'}, None

    def verify(self, ref, vector, turn=0):
        """Exact courtyard checks and modelled pad clearance after an integer move or quarter turn."""
        about = tuple(self.by_ref[ref]['position_nm'])
        side = self.by_ref[ref]['side']
        if any(e['outer'] is None for o in self.footprints for e in self.pads[o['ref']] if e['copper']):
            return False
        moved = [_translate(_rotate_polygon(p, turn, about) if turn else p, vector) for p in self.courtyards[ref]]
        if any(not _inside(p, self.outline) for p in moved):
            return False
        for other in self.footprints:
            if other['ref'] == ref:
                continue
            if other['side'] == side and any(intersection_area(a, b) > 0 for a in moved
                                             for b in self.courtyards[other['ref']]):
                return False
            for m in self.pads[ref]:
                if not m['copper']:
                    continue
                shape = pad_shapes(m['pad'], vector, turn, about)[0]
                for o in self.pads[other['ref']]:
                    if m['copper'] & o['copper'] and clearance(shape, o['outer']) < self.margin(m, o):
                        return False
        return True

    def suggestion(self, refs):
        """Move for the cheapest movable part among refs; ties by fewer pads, then reference."""
        options, reasons = [], []
        for ref in sorted(refs, key=lambda r: (len(self.pads[r]), r)):
            move, reason = self.move(ref)
            if move is None:
                reasons.append(reason)
            else:
                options.append(move)
        if not options:
            return None, '; '.join(reasons)
        return min(options, key=lambda m: m['distance_mm']), None


# ------------------------------------------------------------------ issues

def _issue(board, kind, refs, measure, magnitude, extra=None, move=True):
    record = {'kind': kind, 'class': 'defect' if kind in DEFECT_KINDS else 'advisory', 'refs': list(refs),
              **measure, **(extra or {}), 'parts': [board.part(r) for r in refs], 'nearby': board.nearby(refs)}
    if move:
        suggestion, reason = board.suggestion(refs)
        record['suggested_move'] = suggestion
        if suggestion is None:
            record['suggestion_unavailable'] = reason
    return record, magnitude


def pad_issues(board):
    issues, unknown, grouped = [], [], {}
    for pair in board.pad_pairs():
        grouped.setdefault(pair['refs'], []).append(pair)
    for refs, pairs in sorted(grouped.items()):
        overlaps, deficits = [], []
        for pair in pairs:
            left, right = pair['pads']
            record = {'pads': [[refs[0], left['pad']['number']], [refs[1], right['pad']['number']]],
                      'nets': [left['pad']['net'], right['pad']['net']],
                      'layers': sorted(left['copper'] & right['copper']),
                      'clearance_bounds_mm': [mm(pair['low']), None if pair['high'] is None else mm(pair['high'])],
                      'required_mm': mm(pair['margin'])}
            high = pair['high']
            if high is not None and high < 0:
                overlaps.append((high, record))
            elif high is not None and high < pair['margin']:
                deficits.append((high, record))
            else:
                unknown.append({'kind': 'pad_clearance_unresolved', **record,
                                'reason': 'modelled bounds straddle the required clearance'})
        if overlaps:
            worst = min(v for v, _ in overlaps)
            issues.append(_issue(board, 'pad_overlap', refs,
                                 {'clearance_mm': mm(worst), 'pad_pairs': [r for _, r in sorted(
                                     overlaps, key=lambda o: (o[0], o[1]['pads']))]}, -worst))
        if deficits:
            worst = min(v for v, _ in deficits)
            deficit = max(r['required_mm'] - mm(v) for v, r in deficits)
            issues.append(_issue(board, 'pad_clearance', refs,
                                 {'clearance_mm': mm(worst), 'pad_pairs': [r for _, r in sorted(
                                     deficits, key=lambda o: (o[0], o[1]['pads']))]}, deficit * 10**6))
    return issues, unknown


def courtyard_issues(board):
    """The exact AGENTEE-53 courtyard judge, with moves and free space added."""
    issues = []
    for failure in judge(board.probe)['hard_failures']:
        if failure['kind'] == 'courtyard_overlap':
            issues.append(_issue(board, 'courtyard_overlap', failure['refs'],
                                 {'side': failure['side'], 'overlap_area_mm2': failure['overlap_area_mm2']},
                                 failure['overlap_area_mm2']))
        elif failure['kind'] == 'outside_outline':
            issues.append(_issue(board, 'outside_outline', [failure['ref']],
                                 {'side': failure['side'], 'outside_area_mm2': failure['outside_area_mm2']},
                                 failure['outside_area_mm2']))
    return issues


def _manhattan(probe, nets, replace=None):
    footprints = []
    for fp in probe['footprints']:
        source = replace if replace and fp['ref'] == replace['ref'] else fp
        footprints.append({**source, 'pads': [p for p in source['pads'] if p['net'] in nets]})
    totals = ratsnest({'board_sha256': probe['board_sha256'], 'footprints': footprints})['totals']
    return totals['manhattan_nm']


def rotation_issues(board):
    """A verified quarter turn of an unlocked part that shortens its nets' Manhattan MST."""
    minimum = board.settings['min_rotation_gain_nm']
    if minimum is None or board.outline is None:
        return []
    issues = []
    for fp in board.footprints:
        nets = {p['net'] for p in fp['pads'] if p['net']}
        if fp.get('locked') or not nets or not board.courtyards[fp['ref']]:
            continue
        if any(not board.courtyards[o['ref']] for o in board.footprints if o['side'] == fp['side']):
            continue
        if any(e['outer'] is None for o in board.footprints for e in board.pads[o['ref']] if e['copper']):
            continue
        base = _manhattan(board.probe, nets)
        best = None
        about = fp['position_nm']
        for turn in (90, 180, 270):
            pads = []
            for pad in fp['pads']:
                dx, dy = _turn((pad['position_nm'][0] - about[0], pad['position_nm'][1] - about[1]), turn)
                pads.append({**pad, 'position_nm': [about[0] + int(dx), about[1] + int(dy)]})
            gain = base - _manhattan(board.probe, nets, {**fp, 'pads': pads})
            if gain >= minimum and (best is None or gain > best[0]) and board.verify(fp['ref'], (0, 0), turn):
                best = (gain, turn)
        if best:
            gain, turn = best
            target = (Fraction(fp['rotation_deg']).limit_denominator(10**6) + turn) % 360
            rotation = int(target) if target.denominator == 1 else float(target)
            issues.append(_issue(board, 'ratsnest_rotation', [fp['ref']],
                                 {'manhattan_gain_mm': mm(gain), 'manhattan_before_mm': mm(base)},
                                 gain / 10**6, {'suggested_move': {
                                     'op': 'rotate', 'reference': fp['ref'], 'delta_deg': turn,
                                     'rotation_deg': rotation, 'text': f"rotate {fp['ref']} {turn}°"}}, move=False))
    return issues


# ---------------------------------------------------------------- congestion

def _tail(mean, capacity):
    """P(Poisson(mean) > capacity)."""
    if mean <= 0:
        return 0.0
    term = math.exp(-mean)
    total = term
    for k in range(1, capacity + 1):
        term *= mean / k
        total += term
    return max(0.0, 1.0 - total)


def congestion_cells(board, moved=None):
    """Per-cell capacity, RUDY demand and failure probability over the outline box."""
    settings = board.settings
    cell, pitch = settings['cell_nm'], settings['track_width_nm'] + settings['clearance_nm']
    x0, y0, x1, y1 = _box(board.outline)
    nx, ny = max(1, math.ceil((x1 - x0) / cell)), max(1, math.ceil((y1 - y0) / cell))
    pieces = list(triangles(board.outline))
    offset = {moved[0]: moved[1]} if moved else {}
    inside = {}
    for j in range(ny):
        for i in range(nx):
            box = (x0 + i*cell, y0 + j*cell, x0 + (i+1)*cell, y0 + (j+1)*cell)
            square = [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]
            fraction = sum(clipped_area(t, square) for t in pieces) / (cell * cell)
            if fraction > 0:
                inside[(i, j)] = (box, float(fraction))
    blocked = {key: dict.fromkeys(settings['routing_layers'], 0.0) for key in inside}
    for fp in board.footprints:
        vector = offset.get(fp['ref'], (0, 0))
        for e in board.pads[fp['ref']]:
            layers = e['copper'] & set(settings['routing_layers'])
            if not layers:
                continue
            if e['outer'] is not None:
                shape = pad_shapes(e['pad'], vector)[0]
                box = _box(shape[0], shape[1])
            else:
                (px, py), (w, h) = e['pad']['position_nm'], e['pad']['size_nm']
                half = math.hypot(w, h) / 2
                box = (px + vector[0] - half, py + vector[1] - half, px + vector[0] + half, py + vector[1] + half)
            for key in _cells(box, x0, y0, cell, nx, ny):
                if key in inside:
                    for layer in layers:
                        blocked[key][layer] += _area(box, inside[key][0])
    layers = settings['routing_layers']
    free = {key: sum(max(0.0, fraction - blocked[key][layer] / (cell * cell)) for layer in layers)
            for key, (_, fraction) in inside.items()}
    demand, unplaced = dict.fromkeys(inside, 0.0), 0
    probe = board.probe
    if moved:
        probe = {**probe, 'footprints': [{**fp, 'pads': [{**p, 'position_nm': [p['position_nm'][0] + moved[1][0],
                 p['position_nm'][1] + moved[1][1]]} for p in fp['pads']]} if fp['ref'] == moved[0] else fp
                 for fp in probe['footprints']]}
    proxy = ratsnest(probe)
    positions = {}
    for fp in probe['footprints']:
        for pad in fp['pads']:
            positions[(fp['ref'], fp['uuid'], pad['number'], pad['uuid'])] = pad['position_nm']
    for net in sorted(proxy['nets']):
        record = proxy['nets'][net]
        for i, j in record['manhattan_edges']:
            p, q = positions[tuple(record['terminals'][i])], positions[tuple(record['terminals'][j])]
            length = abs(p[0] - q[0]) + abs(p[1] - q[1])
            if not length:
                continue
            cx, cy = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
            w, h = max(abs(p[0] - q[0]), pitch), max(abs(p[1] - q[1]), pitch)
            box = (cx - w/2, cy - h/2, cx + w/2, cy + h/2)
            # Wire length spreads over the unblocked part of its box: routes pass around pads.
            weights = [(key, free[key] * _area(box, inside[key][0]))
                       for key in _cells(box, x0, y0, cell, nx, ny) if key in inside]
            total = sum(w for _, w in weights)
            if total <= 0:
                unplaced += length
                continue
            for key, weight in weights:
                demand[key] += length * weight / total
    cells = {}
    for key, (box, fraction) in inside.items():
        capacity = int(math.floor(free[key] * cell / pitch + 1e-9))
        mean = demand[key] / cell
        cells[key] = {'box': box, 'capacity_tracks': capacity, 'demand_tracks': round(mean, 6),
                      'probability_of_failure': round(_tail(mean, capacity), 9)}
    return cells, proxy, unplaced


def _cells(box, x0, y0, cell, nx, ny):
    i0, i1 = max(0, math.floor((box[0] - x0) / cell)), min(nx - 1, math.floor((box[2] - x0) / cell))
    j0, j1 = max(0, math.floor((box[1] - y0) / cell)), min(ny - 1, math.floor((box[3] - y0) / cell))
    return [(i, j) for j in range(j0, j1 + 1) for i in range(i0, i1 + 1)]


def _region_probability(cells, keys):
    survive = 1.0
    for key in keys:
        survive *= 1.0 - cells[key]['probability_of_failure']
    return round(1.0 - survive, 9)


def congestion(board):
    """Connected cells at or above the lowest band, ranked by failure probability."""
    if board.outline is None:
        return None, [{'kind': 'congestion', 'reason': 'outline unproved; congestion not evaluated'}]
    cells, proxy, unplaced = congestion_cells(board)
    unknown = []
    if unplaced:
        unknown.append({'kind': 'unplaced_demand', 'manhattan_mm': mm(unplaced),
                        'reason': 'ratsnest connections whose box has no unblocked routing area'})
    if not proxy['totals']['terminals']:
        unknown.append({'kind': 'no_net_terminals', 'reason': 'no netted pads; routing demand unknown'})
    floor = BANDS[-1][1]
    hot = {key for key, c in cells.items() if c['probability_of_failure'] >= floor}
    regions, seen = [], set()
    for start in sorted(hot, key=lambda k: (k[1], k[0])):
        if start in seen:
            continue
        group, stack = [], [start]
        seen.add(start)
        while stack:
            i, j = stack.pop()
            group.append((i, j))
            for key in ((i+1, j), (i-1, j), (i, j+1), (i, j-1)):
                if key in hot and key not in seen:
                    seen.add(key)
                    stack.append(key)
        group.sort(key=lambda k: (k[1], k[0]))
        box = _union([cells[k]['box'] for k in group])
        regions.append(_region(board, cells, group, box))
    regions.sort(key=lambda r: (-r['probability_of_failure'], r['bbox_mm'][1], r['bbox_mm'][0]))
    summary = {'cell_mm': mm(board.settings['cell_nm']), 'cells_evaluated': len(cells),
               'board_probability_of_failure': _region_probability(cells, sorted(cells)),
               'nets': proxy['totals']['nets'], 'terminals': proxy['totals']['terminals'],
               'regions': regions}
    return summary, unknown


def _region(board, cells, group, box):
    cell = board.settings['cell_nm']
    reach = (box[0] - cell, box[1] - cell, box[2] + cell, box[3] + cell)
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    near = []
    for fp in board.footprints:
        if _gap(board.extent(fp['ref']), reach) == 0:
            x, y = fp['position_nm']
            near.append((round(math.hypot(x - centre[0], y - centre[1])), fp['ref']))
    near.sort()
    probability = _region_probability(cells, group)
    record = {'id': f'region@{mm(box[0])},{mm(box[1])}', 'bbox_mm': [mm(v) for v in box],
              'cells': [list(k) for k in group], 'probability_of_failure': probability,
              'max_cell_probability': max(cells[k]['probability_of_failure'] for k in group),
              'demand_tracks': round(sum(cells[k]['demand_tracks'] for k in group), 6),
              'capacity_tracks': sum(cells[k]['capacity_tracks'] for k in group),
              'nearby': [{**board.part(ref), 'distance_mm': mm(d)} for d, ref in near]}
    move, reason = _relief(board, cells, group, box, centre, [ref for _, ref in near])
    record['suggested_move'] = move
    if move is None:
        record['suggestion_unavailable'] = reason
    return record


def _relief(board, cells, group, box, centre, refs):
    """Move the nearest movable part away from the region centre within its free space."""
    reasons = []
    for ref in refs:
        fp = board.by_ref[ref]
        space = board.free_space(ref)
        if fp.get('locked') or space['status'] != 'proven':
            reasons.append(f'{ref} locked' if fp.get('locked') else f"{ref}: {space['reason']}")
            continue
        x, y = fp['position_nm']
        extent = board.extent(ref)
        choices = []
        for name, (dx, dy) in DIRECTIONS:
            away = dx * (x - centre[0]) + dy * (y - centre[1])
            if away < 0 or (away == 0 and (x, y) != centre):
                continue
            clear = {'right': box[2] - extent[0], 'left': extent[2] - box[0],
                     'down': box[3] - extent[1], 'up': extent[3] - box[1]}[name]
            limit = int(space[name] * 10**6)
            distance = min(max(int(math.ceil(clear / 1000)) * 1000, 0), limit // 1000 * 1000)
            if distance > 0:
                choices.append((-space[name], name, distance, (dx * distance, dy * distance)))
        for _, name, distance, vector in sorted(choices):
            if not board.verify(ref, vector):
                continue
            after, _, _ = congestion_cells(board, (ref, vector))
            keys = [k for k in group if k in after]
            return {'op': 'move', 'reference': ref, 'direction': name, 'distance_mm': mm(distance),
                    'dx_mm': mm(vector[0]), 'dy_mm': mm(vector[1]), 'x_mm': mm(x + vector[0]),
                    'y_mm': mm(y + vector[1]), 'text': f'move {ref} {_text_mm(distance)} mm {name}',
                    'region_probability_after': _region_probability(after, keys),
                    'basis': 'heuristic relief within courtyard free space; graded only by fresh checks'}, None
        reasons.append(f'{ref}: no verified move away from the region')
    return None, '; '.join(reasons) or 'no nearby part'


# ------------------------------------------------------------------ report

def analyze(probe, params):
    """Typed issues and congestion regions; ValueError for an unusable capture or declaration."""
    settings = parameters(params)
    board = Board(probe, settings)
    ranked, unknown = pad_issues(board)
    ranked += courtyard_issues(board)
    ranked += rotation_issues(board)
    regions, missing = congestion(board)
    unknown += missing
    if regions is not None and settings['max_failure_probability'] is not None:
        for region in regions['regions']:
            if region['probability_of_failure'] > settings['max_failure_probability']:
                ranked.append(({'kind': 'routing_congestion', 'class': 'defect', 'refs': [region['id']],
                                'region': region['id'], 'probability_of_failure': region['probability_of_failure'],
                                'max_failure_probability': settings['max_failure_probability'],
                                'suggested_move': region['suggested_move']}, region['probability_of_failure']))
    ranked.sort(key=lambda item: (KIND_ORDER.index(item[0]['kind']), -round(item[1], 6), item[0]['refs']))
    issues = [{'rank': n, **issue} for n, (issue, _) in enumerate(ranked, 1)]
    defects = [i for i in issues if i['class'] == 'defect']
    unknown = board.unknown + unknown
    return {'schema': SCHEMA, 'board_sha256': probe['board_sha256'], 'pcbnew_version': probe.get('pcbnew_version'),
            'status': 'defect' if defects else 'info', 'authority': 'advisory',
            'parameters': {k: v for k, v in params.items()},
            'netlist': {'source': 'saved board pad nets (forward-annotated netlist)'},
            'method': {'severity_bands': {name: floor for name, floor in BANDS},
                       'probability': 'P(Poisson(RUDY demand of pad-centre Manhattan MST, spread over '
                                      'unblocked area) > integer track capacity of unblocked cell area); '
                                      'region = 1 - product(1 - cell); uncalibrated estimate',
                       'clearance': 'signed distance; rounded rectangles bounded by rectangle and stadium',
                       'moves': 'axis moves verified against exact courtyards, outline and modelled pads'},
            'issues': issues, 'congestion': regions, 'unknown': unknown}


def unavailable(reason):
    """A missing or unusable probe gives unknown coverage, never an empty pass."""
    return {'schema': SCHEMA, 'status': 'unknown', 'authority': 'advisory', 'issues': [], 'congestion': None,
            'unknown': [{'kind': 'probe', 'reason': reason}]}


def from_file(path, params):
    try:
        probe = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        return unavailable(f'placement probe output unreadable: {exc}')
    try:
        return analyze(probe, params)
    except (ValueError, KeyError, TypeError) as exc:
        return unavailable(f'placement probe output unusable: {exc}')


def summary(report):
    """Human-readable lines; bands are computed here from the stored probabilities."""
    lines = [f"{report['status']}: {len(report['issues'])} issue(s), {len(report['unknown'])} unknown item(s)"]
    for issue in report['issues']:
        move = (issue.get('suggested_move') or {}).get('text') or issue.get('suggestion_unavailable')
        lines.append(f"{issue['rank']}. {issue['kind']} {'+'.join(issue['refs'])}: {move}")
    for region in (report.get('congestion') or {}).get('regions', []):
        lines.append(f"{region['id']} p={region['probability_of_failure']} band={severity(region['probability_of_failure'])}"
                     f" near {', '.join(n['ref'] for n in region['nearby'])}")
    return lines


# ------------------------------------------------------------- coordinator

def check_placement_congestion(ctx):
    """Advisory coordinator check: defect for proved geometry, otherwise info; never pass."""
    from .checks import CheckError
    from .stages import project_findings
    from .storage import write_json
    try:
        parameters(ctx.params)
    except ValueError as exc:
        raise CheckError('unsupported', str(exc)) from exc
    board = ctx.candidate_path(ctx.scope['board'])
    ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.placement_probe', board,
                 '--out', '/work/placement-probe.json'], timeout=600)
    ctx.evidence('placement-probe.json')
    facts = ctx.load_json('placement-probe.json', 'placement probe')
    if facts.get('board_sha256') != ctx.manifest['files'].get(ctx.scope['board']):
        raise CheckError('malformed', 'placement probe does not bind the captured board')
    try:
        report = analyze(facts, ctx.params)
    except (ValueError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'placement probe unusable for congestion analysis: {exc}') from exc
    write_json(ctx.work / 'congestion.json', report)
    ctx.evidence('congestion.json')
    regions = (report['congestion'] or {}).get('regions', [])
    ctx.result['details'].update({
        'board_sha256': report['board_sha256'], 'pcbnew_version': report['pcbnew_version'],
        'authority': 'advisory', 'issues': report['issues'], 'unknown': report['unknown'],
        'congestion': None if report['congestion'] is None else {
            k: v for k, v in report['congestion'].items() if k != 'regions'},
        'regions': [{k: v for k, v in r.items() if k != 'cells'} for r in regions]})
    ctx.result['limitations'].append(
        'advisory pre-route estimate over saved placement geometry and pad nets; never a pass; '
        'congestion probability is uncalibrated; suggested moves are proposals graded only by fresh checks')
    defects = [i for i in report['issues'] if i['class'] == 'defect']
    if defects:
        findings = project_findings(ctx.cid, [('+'.join(i['refs']), i['kind'], _fix(i.get('suggested_move')))
                                              for i in defects])
        if findings:
            ctx.result['details']['gate_findings'] = findings
    bands = [severity(r['probability_of_failure']) for r in regions]
    ctx.result.update(execution='ok', outcome='defect' if defects else 'info',
                      summary=f"placement_congestion: {len(defects)} proved defect(s), "
                              f"{len(report['issues']) - len(defects)} advisory issue(s), {len(regions)} congested "
                              f"region(s) ({', '.join(f'{bands.count(b)} {b}' for b, _ in BANDS)}), "
                              f"{len(report['unknown'])} unknown item(s)")


def _fix(move):
    if not move:
        return None
    if move['op'] == 'rotate':
        return {'op': 'rotate', 'params': {'reference': move['reference'], 'rotation_deg': move['rotation_deg']}}
    return {'op': 'move', 'params': {k: move[k] for k in ('reference', 'x_mm', 'y_mm', 'dx_mm', 'dy_mm')}}


def main(argv=None):
    parser = argparse.ArgumentParser(description='advisory pre-route congestion report from a placement probe')
    parser.add_argument('probe')
    parser.add_argument('--params', required=True, help='JSON object of declared parameters')
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    report = from_file(args.probe, json.loads(args.params))
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return 0 if report['status'] != 'unknown' else 2


if __name__ == '__main__':
    raise SystemExit(main())


def describe_placement_congestion(ctx):
    from .placement_congestion import capability as congestion_capability
    ctx.captured(ctx.scope.get('board'))
    ctx.result['predicates'] = congestion_capability(ctx.params)


if __name__ != '__main__':
    _register_check('placement_congestion', describe_placement_congestion, check_placement_congestion)

"""DFM geometry rules over the saved-board probe and a captured threshold profile.

The rule list and thresholds follow AI-PCB-Generator src/pcb/dfm.py (MIT):
DFM-002 annular ring, DFM-003 acid trap, DFM-006 component spacing, DFM-008 via
aspect ratio, DFM-009 copper balance, DFM-010 SMD bridge gap and DFM-012
differential-pair length mismatch. The geometry is re-implemented on exact
pcbnew facts (`geometry_probe --dfm`): pad copper polygons, track and arc
endpoints, via drill and size, courtyards, the saved board thickness and united
per-layer copper area. There are no built-in thresholds and no aggregate score;
each rule yields coded findings with coordinates and references, and any item a
rule cannot evaluate makes that rule `unsupported`, never `pass`.
The additional owned DFM-013 rule measures SMD-pad to via copper clearance.

Out of scope: copper width (copper-width/1) and clearance (DRC), etch
compensation, thermal relief, paste, panel and board-edge rules.
"""

from .check_registry import register as _register_check
from . import check_prerequisites as _requires
import math
import re
from fractions import Fraction

from .storage import digest, write_json

SCHEMA = 'agent-ee.dfm-profile/1'
REPORT = 'agent-ee.dfm-report/1'
ID = re.compile(r'[a-z0-9][a-z0-9_.-]{0,63}\Z')
NAME_PAIRS = 'name-suffix/1'
SUITE_LIMITATION = (
    'saved-board geometry; zone fill as saved; no etch, thermal relief, paste, panel or '
    'board-edge DFM; copper width and clearance belong to copper-width/1 and DRC'
)
# rule: (finding code, required threshold fields, false-negative / unmodelled classes)
RULES = {
    'annular_ring': (
        'DFM-002',
        ('min_ring_mm',),
        (
            'via annular ring uses all-layers padstack size only; zone and teardrop copper are not measured',
            'per-layer padstack modes, custom/trapezoid/chamfered PTH pads, non-circular/non-oblong drills and offset drills in non-rectangular pads are unsupported',
        ),
    ),
    'acid_trap': (
        'DFM-003',
        ('min_angle_deg',),
        (
            'exact shared track/arc endpoints only; mid-track T-junction acid traps are not detected',
            'junctions inside pad copper or a via disk count as covered (pad polygons use pcbnew ERROR_OUTSIDE arc approximations); zone copper wedges are not measured',
            'degenerate or zero-length tracks/arcs and non-track copper primitives are unsupported',
        ),
    ),
    'component_spacing': (
        'DFM-006',
        ('min_gap_mm',),
        (
            'machine-placed footprint courtyard polygons only (with pcbnew ERROR_OUTSIDE arc approximations); 3D body bounds, pad copper and non-placed footprints are not compared',
            'machine-placed footprints without a courtyard polygon on their placement side are unsupported',
        ),
    ),
    'via_aspect_ratio': (
        'DFM-008',
        ('max_ratio',),
        (
            'saved board thickness is parsed from the first root-level general node only; PTH pad holes and plating thickness are not measured',
            'missing saved board thickness and blind, buried or microvias without per-layer stackup thickness are unsupported',
        ),
    ),
    'copper_balance': (
        'DFM-009',
        ('max_layer_share',),
        (
            'united copper area only (with pcbnew ERROR_OUTSIDE arc/zone approximations); spatial copper distribution, warp and odd-layer centre copper are not modelled',
            'unfilled zones and missing layer copper-area records are unsupported',
        ),
    ),
    'smd_bridge_gap': (
        'DFM-010',
        ('min_gap_mm',),
        (
            'curved SMD pad polygons use pcbnew ERROR_OUTSIDE arc approximations; same-footprint pads, same-net pads, PTH pads, tracks, vias, zones and solder-mask openings are not compared',
            'SMD pads without native copper polygons on a declared layer are unsupported',
        ),
    ),
    'diff_pair_skew': (
        'DFM-012',
        ('max_skew_mm', 'pairs'),
        (
            'whole-net track and arc length only; per-segment pairing, via barrel lengths, zones, pad/package delays and KiCad net classes are not measured',
            'unrouted or absent pair nets and degenerate or non-track/arc segments are unsupported',
        ),
    ),
    'smd_via_clearance': (
        'DFM-013',
        ('min_gap_mm',),
        (
            'outer-layer SMD pad copper to complete via copper disk, including same-net pairs; PTH pads, solder-mask and paste openings are not measured',
            'curved pad polygons use pcbnew ERROR_OUTSIDE approximations; custom pads and per-layer padstacks are unsupported',
        ),
    ),
}
FALSE_NEGATIVE_CLASSES = {kind: classes for kind, (_, _, classes) in RULES.items()}


def rule_limitation(spec):
    code, _, classes = RULES[spec['rule']]
    return f"{spec['id']} ({spec['rule']}, {code}): {'; '.join(classes)}"



# ------------------------------------------------------------------ profile

def positive(value, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or value <= 0):
        raise ValueError(f'{label} must be finite positive')
    return value


def compile_profile(document):
    """Validate one explicit agent-ee.dfm-profile/1; no defaults are supplied."""
    if (not isinstance(document, dict) or set(document) - {'schema', 'name', 'basis', 'rules'} or
            document.get('schema') != SCHEMA or not isinstance(document.get('name'), str) or
            not ID.fullmatch(document['name']) or not isinstance(document.get('rules'), list) or
            not document['rules']):
        raise ValueError('named nonempty agent-ee.dfm-profile/1 required; no default thresholds')
    if 'basis' in document and (not isinstance(document['basis'], str) or not document['basis'].strip()):
        raise ValueError('profile basis must be nonempty text')
    rules, ids = [], set()
    for rule in document['rules']:
        if not isinstance(rule, dict) or not isinstance(rule.get('id'), str) or not ID.fullmatch(rule['id']):
            raise ValueError('DFM rule requires a safe id')
        if rule['id'] in ids:
            raise ValueError(f'duplicate DFM rule id {rule["id"]}')
        ids.add(rule['id'])
        kind = rule.get('rule')
        if kind not in RULES:
            raise ValueError(f'unsupported DFM rule {kind!r}')
        fields = RULES[kind][1]
        if set(rule) != {'id', 'rule', *fields} and set(rule) != {'id', 'rule', 'basis', *fields}:
            raise ValueError(f'{rule["id"]}: {kind} requires exactly {", ".join(fields)} (optional basis)')
        if 'basis' in rule and (not isinstance(rule['basis'], str) or not rule['basis'].strip()):
            raise ValueError(f'{rule["id"]}: basis must be nonempty text')
        for field in fields:
            if field != 'pairs':
                positive(rule[field], f'{rule["id"]} {field}')
        if kind == 'acid_trap' and rule['min_angle_deg'] >= 180:
            raise ValueError('acid_trap min_angle_deg must be below 180')
        if kind == 'copper_balance' and not 0.5 < rule['max_layer_share'] < 1:
            raise ValueError('copper_balance max_layer_share must lie in (0.5, 1)')
        if kind == 'diff_pair_skew':
            pairs = rule['pairs']
            if pairs != NAME_PAIRS:
                if (not isinstance(pairs, list) or not pairs or
                        any(not isinstance(p, list) or len(p) != 2 or
                            any(not isinstance(n, str) or not n for n in p) or p[0] == p[1]
                            for p in pairs)):
                    raise ValueError(f'diff_pair_skew pairs must be {NAME_PAIRS!r} or [[p, n], ...] net names')
                names = [n for p in pairs for n in p]
                if len(set(names)) != len(names):
                    raise ValueError('declared differential pairs must not share nets')
        rules.append(dict(rule))
    return {'schema': SCHEMA, 'name': document['name'], 'rules': sorted(rules, key=lambda r: r['id'])}


def capability(params, scope, files, inputs):
    from .routing_rules import captured_file, load_document
    if not isinstance(params, dict) or set(params) != {'dfm_profile'}:
        raise ValueError('dfm_geometry requires only its captured dfm_profile path')
    captured_file(inputs, files, scope['board'])
    profile = compile_profile(load_document(captured_file(inputs, files, params['dfm_profile'])))
    return {'profile': profile, 'immutable_inputs': [params['dfm_profile']],
            'predicates': ['dfm:' + r['id'] for r in profile['rules']]}


# ----------------------------------------------------------------- geometry

def mm(value):
    return round(value / 1e6, 6)


def point_mm(p):
    return [mm(p[0]), mm(p[1])]


def cross(o, a, b):
    return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])


def segments_cross(p, q, r, s):
    """Exact integer test: closed segments pq and rs share at least one point."""
    def within(a, b, c):
        return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])
    d1, d2, d3, d4 = cross(r, s, p), cross(r, s, q), cross(p, q, r), cross(p, q, s)
    if ((d1 > 0) != (d2 > 0) and d1 and d2) and ((d3 > 0) != (d4 > 0) and d3 and d4):
        return True
    return ((not d1 and within(r, s, p)) or (not d2 and within(r, s, q)) or
            (not d3 and within(p, q, r)) or (not d4 and within(p, q, s)))


def closest_on_segment(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    length = dx*dx + dy*dy
    t = 0.0 if not length else max(0.0, min(1.0, ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / length))
    return (a[0] + t*dx, a[1] + t*dy)


def segment_gap(p, q, r, s):
    """(distance, point on pq, point on rs) between closed segments."""
    if segments_cross(p, q, r, s):
        return 0.0, p, p
    best = None
    for point, a, b, first in ((p, r, s, True), (q, r, s, True), (r, p, q, False), (s, p, q, False)):
        other = closest_on_segment(point, a, b)
        d = math.hypot(point[0]-other[0], point[1]-other[1])
        if best is None or d < best[0]:
            best = (d, point, other) if first else (d, other, point)
    return best


def edges(contour):
    return zip(contour, contour[1:] + contour[:1])


def inside(point, contour):
    """Even-odd containment; boundary points count as inside."""
    hit = False
    for a, b in edges(contour):
        if not cross(a, b, point) and min(a[0], b[0]) <= point[0] <= max(a[0], b[0]) \
                and min(a[1], b[1]) <= point[1] <= max(a[1], b[1]):
            return True
        if (a[1] > point[1]) != (b[1] > point[1]):
            x = a[0] + (point[1]-a[1]) * (b[0]-a[0]) / (b[1]-a[1])
            if x > point[0]:
                hit = not hit
    return hit


def bbox(contours):
    xs = [p[0] for c in contours for p in c]
    ys = [p[1] for c in contours for p in c]
    return min(xs), min(ys), max(xs), max(ys)


def box_gap(a, b):
    dx = max(0, b[0]-a[2], a[0]-b[2])
    dy = max(0, b[1]-a[3], a[1]-b[3])
    return math.hypot(dx, dy)


def polygon_gap(first, second):
    """Minimum distance between two polygon sets; 0 when they touch or overlap."""
    for a in first:
        for b in second:
            if inside(a[0], b) or inside(b[0], a):
                return 0.0, tuple(a[0]), tuple(a[0])
    best = None
    for a in first:
        for p, q in edges(a):
            for b in second:
                for r, s in edges(b):
                    found = segment_gap(tuple(p), tuple(q), tuple(r), tuple(s))
                    if best is None or found[0] < best[0]:
                        best = found
                        if not best[0]:
                            return best
    return best


def valid_contours(value):
    return (isinstance(value, list) and value and
            all(isinstance(c, list) and len(c) >= 3 and
                all(isinstance(p, list) and len(p) == 2 and
                    all(isinstance(v, int) and not isinstance(v, bool) for v in p) for p in c)
                for c in value))


def nm_point(value):
    if (not isinstance(value, list) or len(value) != 2 or
            any(isinstance(v, bool) or not isinstance(v, int) for v in value)):
        raise ValueError('malformed nm point')
    return tuple(value)


def circle_center(s, m, e):
    d = 2 * (s[0]*(m[1]-e[1]) + m[0]*(e[1]-s[1]) + e[0]*(s[1]-m[1]))
    if not d:
        raise ValueError('degenerate arc')
    ss, mm2, ee = s[0]**2 + s[1]**2, m[0]**2 + m[1]**2, e[0]**2 + e[1]**2
    return ((ss*(m[1]-e[1]) + mm2*(e[1]-s[1]) + ee*(s[1]-m[1])) / d,
            (ss*(e[0]-m[0]) + mm2*(s[0]-e[0]) + ee*(m[0]-s[0])) / d)


def arc_length(s, m, e):
    c = circle_center(s, m, e)
    radius = math.hypot(s[0]-c[0], s[1]-c[1])
    a1, a3 = (math.atan2(p[1]-c[1], p[0]-c[0]) for p in (s, e))
    turn = (a3 - a1) % (2*math.pi) if cross(s, m, e) > 0 else (a1 - a3) % (2*math.pi)
    return radius * turn


def arm_direction(track, vertex):
    """Unit direction of copper leaving `vertex` along a track or arc endpoint tangent."""
    start, end = nm_point(track['start_nm']), nm_point(track['end_nm'])
    other = end if vertex == start else start
    if track['native_class'] == 'PCB_ARC':
        mid = nm_point(track['mid_nm'])
        c = circle_center(start, mid, end)
        radial = (vertex[0]-c[0], vertex[1]-c[1])
        tangent = (-radial[1], radial[0])
        if tangent[0]*(mid[0]-vertex[0]) + tangent[1]*(mid[1]-vertex[1]) < 0:
            tangent = (-tangent[0], -tangent[1])
    else:
        tangent = (other[0]-vertex[0], other[1]-vertex[1])
    length = math.hypot(*tangent)
    if not length:
        raise ValueError('zero-length track')
    return (tangent[0]/length, tangent[1]/length)


# -------------------------------------------------------------------- rules

class Rule:
    def __init__(self, spec):
        self.spec = spec
        self.code, _, classes = RULES[spec['rule']]
        self.false_negative_classes = list(classes)
        self.findings, self.unsupported, self.checked = [], [], 0
        self.notes = {}

    def refuse(self, entity, reason):
        self.unsupported.append({'entity': entity, 'reason': reason})

    def find(self, entity, at, message, **facts):
        self.findings.append({'code': self.code, 'rule': self.spec['rule'], 'rule_id': self.spec['id'],
                              'entity': entity, 'at_mm': point_mm(at), 'message': message, **facts})

    def record(self, problem=None):
        thresholds = {k: v for k, v in self.spec.items() if k not in ('id', 'rule', 'basis')}
        base = {'id': self.spec['id'], 'rule': self.spec['rule'], 'code': self.code,
                'thresholds': thresholds, 'checked': self.checked,
                'false_negative_classes': list(self.false_negative_classes),
                'findings': sorted(self.findings, key=lambda f: f['entity']),
                'unsupported': sorted(self.unsupported, key=lambda u: u['entity']), **self.notes}
        if problem or self.unsupported:
            return {**base, 'execution': 'unsupported', 'status': 'not_evaluated',
                    'reason': problem or f'{len(self.unsupported)} item(s) could not be evaluated'}
        status = 'defect' if self.findings else 'pass' if self.checked else 'not_applicable'
        return {**base, 'execution': 'ok', 'status': status}


def annular_ring(rule, facts):
    limit = round(rule.spec['min_ring_mm'] * 1e6)
    for via in facts['vias']:
        entity = 'via:' + via['uuid']
        if via['padstack_mode'] != 'NORMAL':
            rule.refuse(entity, f'via padstack mode {via["padstack_mode"]} has per-layer sizes')
            continue
        rule.checked += 1
        if via['width_nm'] - via['drill_nm'] < 2 * limit:
            ring = (via['width_nm'] - via['drill_nm']) / 2
            rule.find(entity, nm_point(via['position_nm']),
                      f'via annular ring {mm(ring)} mm below {rule.spec["min_ring_mm"]} mm',
                      items=[via['uuid']], refs=[], nets=[via['net']], layers=via['layers'],
                      measured_mm=mm(ring), size_mm=mm(via['width_nm']), drill_mm=mm(via['drill_nm']))
    for pad in facts['pads']:
        if pad['attribute'] != 'PTH':
            continue
        entity = 'pad:' + pad['uuid']
        (sx, sy), (dx, dy), (ox, oy) = pad['size_nm'], pad['drill_nm'], pad['offset_nm']
        if pad['padstack_mode'] != 'NORMAL':
            rule.refuse(entity, f'pad padstack mode {pad["padstack_mode"]} has per-layer sizes')
            continue
        if pad['drill_shape'] == 'CIRCLE':
            dy = dx
        elif pad['drill_shape'] != 'OBLONG':
            rule.refuse(entity, f'drill shape {pad["drill_shape"]} unsupported')
            continue
        shape = pad['shape']
        # Exact for these convex pads around an aligned drill; anything else is refused.
        if shape == 'RECTANGLE':
            ring2 = min(sx - dx - 2*abs(ox), sy - dy - 2*abs(oy))
        elif shape in ('CIRCLE', 'OVAL') or (shape == 'ROUNDRECT' and pad['drill_shape'] == 'CIRCLE'):
            if ox or oy:
                rule.refuse(entity, f'{shape} pad with drill offset unsupported')
                continue
            ring2 = min(sx - dx, sy - dy)
        else:
            rule.refuse(entity, f'{shape} pad with {pad["drill_shape"]} drill unsupported')
            continue
        rule.checked += 1
        if ring2 < 2 * limit:
            rule.find(entity, nm_point(pad['position_nm']),
                      f'pad {pad["ref"]}:{pad["pad"]} annular ring {mm(ring2/2)} mm below '
                      f'{rule.spec["min_ring_mm"]} mm', items=[pad['uuid']], refs=[pad['ref']],
                      pads=[f'{pad["ref"]}:{pad["pad"]}'], nets=[pad['net']], measured_mm=mm(ring2/2))


def via_aspect_ratio(rule, facts):
    thickness = facts.get('board_thickness_nm')
    if isinstance(thickness, bool) or not isinstance(thickness, int) or thickness <= 0:
        return facts.get('board_thickness_problem', 'probe output carries no saved board thickness')
    rule.notes['board_thickness_mm'] = mm(thickness)
    for via in facts['vias']:
        entity = 'via:' + via['uuid']
        if via['via_type'] != 'THROUGH':
            rule.refuse(entity, f'{via["via_type"]} via depth needs per-layer stackup thickness')
            continue
        if via['drill_nm'] <= 0:
            rule.refuse(entity, 'via has no drill')
            continue
        rule.checked += 1
        ratio = thickness / via['drill_nm']
        if ratio > rule.spec['max_ratio']:
            rule.find(entity, nm_point(via['position_nm']),
                      f'via aspect ratio {ratio:.3g}:1 above {rule.spec["max_ratio"]}:1',
                      items=[via['uuid']], refs=[], nets=[via['net']], measured_ratio=round(ratio, 6),
                      drill_mm=mm(via['drill_nm']), board_thickness_mm=mm(thickness))
    return None


def acid_trap(rule, facts):
    limit = rule.spec['min_angle_deg']
    vertices = {}
    for track in facts['tracks']:
        if track['native_class'] not in ('PCB_TRACK', 'PCB_ARC'):
            rule.refuse('track:' + track['uuid'], f'{track["native_class"]} unsupported')
            continue
        for key in ('start_nm', 'end_nm'):
            vertices.setdefault((track['layer'], nm_point(track[key])), []).append(track)
    copper = {}
    for pad in facts['pads']:
        for layer, contours in pad['polygons_nm'].items():
            copper.setdefault(layer, []).extend((bbox([c]), c) for c in contours)
    covered = 0
    for (layer, vertex), arms in sorted(vertices.items()):
        if len(arms) < 2:
            continue
        # A junction inside pad or via copper is not an open etchant wedge at the vertex.
        if any(box[0] <= vertex[0] <= box[2] and box[1] <= vertex[1] <= box[3] and inside(vertex, c)
               for box, c in copper.get(layer, [])) or any(
                layer in v['copper_layers'] and
                math.hypot(vertex[0]-v['position_nm'][0], vertex[1]-v['position_nm'][1]) <= v['width_nm'] / 2
                for v in facts['vias']):
            covered += 1
            continue
        try:
            directions = sorted((math.atan2(d[1], d[0]) % (2*math.pi), t['uuid'])
                                for t in arms for d in [arm_direction(t, vertex)])
        except (ValueError, KeyError) as exc:
            for t in arms:
                rule.refuse('track:' + t['uuid'], f'junction arm unreadable: {exc}')
            continue
        rule.checked += 1
        for index, (angle, uuid) in enumerate(directions):
            following, other = directions[(index + 1) % len(directions)]
            wedge = math.degrees((following - angle) % (2*math.pi))
            if 1e-9 < wedge < limit:
                pair = sorted((uuid, other))
                rule.find(f'junction:{layer}:{vertex[0]},{vertex[1]}:{pair[0]}+{pair[1]}', vertex,
                          f'{wedge:.4g} degree copper junction on {layer} below {limit} degrees',
                          items=pair, refs=[], layer=layer, measured_deg=round(wedge, 6),
                          nets=sorted({t['net'] for t in arms}))
    rule.notes['pad_or_via_covered_junctions'] = covered


def pairwise_gap(items, limit_nm, eligible, find):
    """Call find(a, b, gap, midpoint) for item pairs of different groups closer than the limit."""
    boxes = [bbox(item['contours']) for item in items]
    for i, first in enumerate(items):
        for j in range(i + 1, len(items)):
            second = items[j]
            if first['group'] == second['group'] or not eligible(first, second):
                continue
            if box_gap(boxes[i], boxes[j]) >= limit_nm:
                continue
            gap, a, b = polygon_gap(first['contours'], second['contours'])
            if gap < limit_nm:
                find(first, second, gap, ((a[0]+b[0])/2, (a[1]+b[1])/2))


def smd_bridge_gap(rule, facts):
    limit = rule.spec['min_gap_mm'] * 1e6
    layers = {}
    for pad in facts['pads']:
        if pad['attribute'] != 'SMD':
            continue
        for layer in pad['copper_layers']:
            contours = pad['polygons_nm'].get(layer)
            if not valid_contours(contours):
                rule.refuse('pad:' + pad['uuid'], f'no native copper polygon on {layer}')
                continue
            rule.checked += 1
            layers.setdefault(layer, []).append({'pad': pad, 'contours': contours,
                                                 'group': pad['footprint_uuid']})

    def eligible(a, b):
        return not a['pad']['net'] or a['pad']['net'] != b['pad']['net']

    for layer, items in sorted(layers.items()):
        def find(a, b, gap, at, layer=layer):
            first, second = sorted((a['pad'], b['pad']), key=lambda p: (p['ref'], p['pad'], p['uuid']))
            names = [f'{p["ref"]}:{p["pad"]}' for p in (first, second)]
            rule.find(f'pads:{layer}:{first["uuid"]}+{second["uuid"]}', at,
                      f'SMD pads {names[0]} and {names[1]} on different nets are {mm(gap)} mm apart '
                      f'on {layer}, below {rule.spec["min_gap_mm"]} mm', items=[first['uuid'], second['uuid']],
                      refs=sorted({first['ref'], second['ref']}), pads=names, layer=layer,
                      nets=[first['net'], second['net']], measured_mm=mm(gap))
        pairwise_gap(items, limit, eligible, find)


def smd_via_clearance(rule, facts):
    """Independent assembly clearance, including same-net copper ignored by DRC."""
    from .board_envelope import distance2, inside as contains
    limit = Fraction(str(rule.spec['min_gap_mm'])) * 1_000_000
    pads = []
    for pad in facts['pads']:
        if pad['attribute'] != 'SMD':
            continue
        if pad['padstack_mode'] != 'NORMAL' or pad['shape'] not in (
                'RECTANGLE', 'ROUNDRECT', 'CIRCLE', 'OVAL', 'TRAPEZOID', 'CHAMFERED_RECT'):
            rule.refuse('pad:' + pad['uuid'], 'unsupported SMD shape or per-layer padstack')
            continue
        if not pad['copper_layers'] or not set(pad['copper_layers']) <= set(facts['copper_layers']):
            rule.refuse('pad:' + pad['uuid'], 'missing or invalid pad copper layers')
            continue
        for layer in sorted(set(pad['copper_layers']) & {'F.Cu', 'B.Cu'}):
            contours = pad['polygons_nm'].get(layer)
            if not valid_contours(contours):
                rule.refuse('pad:' + pad['uuid'], f'no native copper polygon on {layer}')
            else:
                pads.append((pad, layer, contours))
    for via in facts['vias']:
        if not via['copper_layers'] or not set(via['copper_layers']) <= set(facts['copper_layers']):
            rule.refuse('via:' + via['uuid'], 'missing or invalid via copper layers')
            continue
        if via['padstack_mode'] != 'NORMAL':
            rule.refuse('via:' + via['uuid'], 'via per-layer sizes are unsupported')
            continue
        centre = nm_point(via['position_nm'])
        width = via['width_nm']
        if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
            raise ValueError('via width must be positive integer nm')
        radius = Fraction(width, 2)
        for pad, layer, contours in pads:
            if layer not in via['copper_layers']:
                continue
            rule.checked += 1
            squared = (Fraction(0) if any(contains(centre, c) for c in contours) else
                       min(distance2(centre, a, b) for c in contours for a, b in edges(c) if a != b))
            if squared < (radius + limit) ** 2:
                gap = max(0.0, math.sqrt(squared) - float(radius))
                rule.find(f'via-pad:{layer}:{via["uuid"]}:{pad["uuid"]}', centre,
                          f'via copper is {mm(gap)} mm from SMD pad {pad["ref"]}:{pad["pad"]} '
                          f'on {layer}, below {rule.spec["min_gap_mm"]} mm',
                          items=[via['uuid'], pad['uuid']], refs=[pad['ref']], pad=pad['pad'],
                          layer=layer, nets=[via['net'], pad['net']], measured_mm=mm(gap))


def component_spacing(rule, facts):
    limit = rule.spec['min_gap_mm'] * 1e6
    sides = {}
    for fp in facts['courtyards']:
        # Only machine-placed parts: pads present, not board-only, in the position file.
        if not fp['pad_count'] or fp['board_only'] or fp['exclude_from_pos_files']:
            continue
        if not valid_contours(fp['polygons_nm']):
            rule.refuse('footprint:' + fp['footprint_uuid'], f'{fp["ref"]} has no courtyard on {fp["side"]}')
            continue
        rule.checked += 1
        sides.setdefault(fp['side'], []).append({'fp': fp, 'contours': fp['polygons_nm'],
                                                'group': fp['footprint_uuid']})
    for side, items in sorted(sides.items()):
        def find(a, b, gap, at, side=side):
            first, second = sorted((a['fp'], b['fp']), key=lambda f: (f['ref'], f['footprint_uuid']))
            rule.find(f'footprints:{first["footprint_uuid"]}+{second["footprint_uuid"]}', at,
                      f'{first["ref"]} and {second["ref"]} courtyards are {mm(gap)} mm apart on {side}, '
                      f'below {rule.spec["min_gap_mm"]} mm',
                      items=[first['footprint_uuid'], second['footprint_uuid']],
                      refs=[first['ref'], second['ref']], side=side, measured_mm=mm(gap))
        pairwise_gap(items, limit, lambda a, b: True, find)


def name_pairs(nets):
    pairs = []
    for name in sorted(nets):
        if name.endswith('+') and name[:-1] + '-' in nets:
            pairs.append([name, name[:-1] + '-'])
        elif name[-2:] in ('_P', '_p') and name[:-1] + ('N' if name[-1] == 'P' else 'n') in nets:
            pairs.append([name, name[:-1] + ('N' if name[-1] == 'P' else 'n')])
    return pairs


def diff_pair_skew(rule, facts):
    lengths, points, items = {}, {}, {}
    for track in facts['tracks']:
        net = track['net']
        try:
            start, end = nm_point(track['start_nm']), nm_point(track['end_nm'])
            if track['native_class'] == 'PCB_ARC':
                length = arc_length(start, nm_point(track['mid_nm']), end)
            elif track['native_class'] == 'PCB_TRACK':
                length = math.hypot(end[0]-start[0], end[1]-start[1])
            else:
                raise ValueError(f'{track["native_class"]} length unsupported')
        except (ValueError, KeyError) as exc:
            lengths[net] = None
            items.setdefault(net, []).append(('unreadable', track['uuid'], str(exc)))
            continue
        if lengths.get(net, 0) is not None:
            lengths[net] = lengths.get(net, 0) + length
        points.setdefault(net, []).extend([start, end])
        items.setdefault(net, []).append(('ok', track['uuid'], track['native_class']))
    nets = {p['net'] for p in facts['pads'] if p['net']} | {t['net'] for t in facts['tracks'] if t['net']}
    declared = rule.spec['pairs']
    pairs = name_pairs(nets) if declared == NAME_PAIRS else declared
    rule.notes['pairs'] = []
    for positive_net, negative_net in pairs:
        entity = f'pair:{positive_net}|{negative_net}'
        missing = [n for n in (positive_net, negative_net) if not lengths.get(n)]
        if missing:
            bad = [i for n in (positive_net, negative_net) for i in items.get(n, []) if i[0] != 'ok']
            rule.refuse(entity, f'unroutable or unreadable length for {", ".join(missing)}'
                        + (f': {bad[0][2]}' if bad else ''))
            continue
        rule.checked += 1
        lp, ln = lengths[positive_net], lengths[negative_net]
        record = {'nets': [positive_net, negative_net], 'lengths_mm': [mm(lp), mm(ln)],
                  'arcs': sum(i[2] == 'PCB_ARC' for n in (positive_net, negative_net) for i in items[n])}
        rule.notes['pairs'].append(record)
        skew = abs(lp - ln)
        if skew > rule.spec['max_skew_mm'] * 1e6:
            box = bbox([points[positive_net] + points[negative_net]])
            rule.find(entity, ((box[0]+box[2])/2, (box[1]+box[3])/2),
                      f'{positive_net}/{negative_net} routed lengths differ by {mm(skew)} mm, '
                      f'above {rule.spec["max_skew_mm"]} mm', refs=[],
                      items=sorted(i[1] for n in (positive_net, negative_net) for i in items[n]),
                      measured_mm=mm(skew), **record)


def copper_balance(rule, facts):
    layers, areas = facts['copper_layers'], facts['copper_area']
    for index in range(len(layers) // 2):
        top, bottom = layers[index], layers[-1 - index]
        entity = f'layers:{top}|{bottom}'
        records = [areas.get(top), areas.get(bottom)]
        if any(not isinstance(r, dict) or isinstance(r.get('area_nm2'), bool) or
               not isinstance(r.get('area_nm2'), int) for r in records):
            rule.refuse(entity, 'copper area missing from probe')
            continue
        unfilled = sorted({z for r in records for z in r.get('unfilled_zones', [])})
        if unfilled:
            rule.refuse(entity, f'{len(unfilled)} unfilled zone(s): copper as built is unknown')
            continue
        total = records[0]['area_nm2'] + records[1]['area_nm2']
        if not total:
            continue
        rule.checked += 1
        heavy = 0 if records[0]['area_nm2'] >= records[1]['area_nm2'] else 1
        share = records[heavy]['area_nm2'] / total
        if share > rule.spec['max_layer_share']:
            box = records[heavy]['bbox_nm']
            rule.find(entity, ((box[0]+box[2])/2, (box[1]+box[3])/2),
                      f'{(top, bottom)[heavy]} holds {share:.1%} of the {top}/{bottom} copper, above '
                      f'{rule.spec["max_layer_share"]:.1%}', refs=[], items=[], layers=[top, bottom],
                      areas_mm2=[round(r['area_nm2'] / 1e12, 6) for r in records],
                      measured_share=round(share, 6))


EVALUATORS = {'annular_ring': annular_ring, 'acid_trap': acid_trap,
              'component_spacing': component_spacing, 'via_aspect_ratio': via_aspect_ratio,
              'copper_balance': copper_balance, 'smd_bridge_gap': smd_bridge_gap,
              'diff_pair_skew': diff_pair_skew, 'smd_via_clearance': smd_via_clearance}
FACT_KEYS = ('copper_layers', 'pads', 'courtyards', 'tracks', 'vias', 'copper_area')


def evaluate(facts, profile):
    """Pure function of the probe's `dfm` section and a compiled profile."""
    if not isinstance(facts, dict) or any(k not in facts for k in FACT_KEYS):
        raise ValueError('probe output lacks the dfm fact section')
    records = []
    for spec in profile['rules']:
        rule = Rule(spec)
        try:
            problem = EVALUATORS[spec['rule']](rule, facts)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            problem = f'malformed probe facts: {type(exc).__name__}: {exc}'
            rule.findings = []
        records.append(rule.record(problem))
    limitations = [SUITE_LIMITATION, *(rule_limitation(spec) for spec in profile['rules'])]
    return {'schema': REPORT, 'profile': profile['name'], 'rules': records, 'limitations': limitations}


# -------------------------------------------------------------------- check

def check_dfm_geometry(ctx):
    from .checks import CheckError
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs)
        source = {'path': ctx.params['dfm_profile'], 'sha256': digest(ctx.inputs / ctx.params['dfm_profile'])}
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.geometry_probe',
                 ctx.candidate_path(ctx.scope['board']), '-o', '/work/geometry.json', '--dfm'], timeout=900)
    ctx.evidence('geometry.json')
    geo = ctx.load_json('geometry.json', 'geometry probe')
    try:
        report = evaluate(geo.get('dfm'), descriptor['profile'])
    except ValueError as exc:
        raise CheckError('malformed', str(exc)) from exc
    report.update(candidate_id=ctx.manifest['candidate_id'], profile_source=source,
                  board_sha256=ctx.manifest['files'].get(ctx.scope['board']),
                  pcbnew_version=geo.get('pcbnew_version'))
    write_json(ctx.work / 'dfm-report.json', report)
    ctx.evidence('dfm-report.json')
    rules = report['rules']
    findings = [f for r in rules for f in r['findings']]
    complete = all(r['execution'] == 'ok' for r in rules)
    ctx.result['limitations'].extend(report['limitations'])
    ctx.result['details'].update(
        profile=source, board_sha256=report['board_sha256'], pcbnew_version=report['pcbnew_version'],
        rules=[{k: r[k] for k in ('id', 'rule', 'code', 'execution', 'status', 'checked',
                                  'false_negative_classes')} |
               ({'reason': r['reason']} if 'reason' in r else {}) for r in rules],
        findings=findings,
        gate_findings=[{'check_id': ctx.cid, 'entity': f['entity'], 'kind': 'dfm/' + f['rule_id']}
                       for f in findings])
    unsupported = [r['id'] for r in rules if r['execution'] != 'ok']
    ctx.result.update(
        execution='ok' if complete else 'unsupported',
        outcome=('defect' if findings else 'pass') if complete else 'unknown',
        summary=(f'{len(findings)} DFM finding(s) over {len(rules)} rule(s)' if complete else
                 f'DFM rule(s) not evaluated: {", ".join(unsupported)}; {len(findings)} finding(s) recorded'))


def describe_dfm_geometry(ctx):
    ctx.captured(ctx.scope.get('board'))
    descriptor = capability(ctx.params, ctx.scope, ctx.files, ctx.inputs)
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('dfm_geometry', describe_dfm_geometry, check_dfm_geometry,
        early_prerequisites=(_requires.placement_clear,), prerequisites=(_requires.stage_progress,))

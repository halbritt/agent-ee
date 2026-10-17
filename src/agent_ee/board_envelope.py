"""Offline board envelope record (agent-ee.envelope/1) and derived obligations.

Geometry is exact integer nanometres in KiCad board axes (y grows downward).
A structural pass is not fabrication or placement acceptance: obligations are
only satisfied later by mapped coordinator checks. Fabricator support is never
inferred here; it stays unresolved until a captured profile contract exists.
"""
import argparse
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

from .candidate import safe_relative
from .storage import canonical
from .symbol_library import LibraryError, children, nanometres, parse_sexpr

SCHEMA = 'agent-ee.envelope/1'
ENVELOPE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}\Z')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
SIDES = {'front': 'F.Cu', 'back': 'B.Cu'}
INSERTIONS = ('through_edge', 'from_top', 'from_bottom')
FORBID = ('footprints', 'tracks', 'vias', 'pads', 'copper_pours')
MAX_COPPER = 32
# Bounds keep exact O(n^2) polygon checks small; real envelopes are far below them.
MAX_BYTES, MAX_VERTICES, MAX_ITEMS = 1024 * 1024, 1024, 1024
# Provisional mapping from the ticket graph (AGENTEE-92 placement, AGENTEE-75
# fab profile, AGENTEE-94 fab package); none of these kinds is registered yet.
CHECK_KINDS = {'outline': ['placement'], 'layer_count': ['fab_profile'],
               'stackup': ['fab_package'], 'fab_profile': ['fab_profile'],
               'mounting_holes': ['placement'], 'keepouts': ['placement'],
               'fixed_footprints': ['placement']}
PROFILE_PENDING = ('AGENTEE-75 fab-profile contract is not committed on main; '
                   'profile name and layer-count support are not interpreted')


def load(raw):
    """Strict JSON: duplicate keys, NaN/Infinity and non-UTF-8 are refused."""
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f'duplicate JSON key: {key}')
            value[key] = item
        return value

    def constant(name):
        raise ValueError(f'invalid JSON number: {name}')
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError(f'envelope must be bytes, at most {MAX_BYTES}')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                          parse_float=Decimal, parse_constant=constant)
    except UnicodeError as exc:
        raise ValueError('envelope is not UTF-8') from exc


def fields(value, label, required, optional=()):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    missing, unknown = set(required) - value.keys(), value.keys() - set(required) - set(optional)
    if missing or unknown:
        raise ValueError(f'{label} fields: missing {sorted(missing)}, unknown {sorted(unknown)}')
    return value


def name(value, label, pattern=NAME):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f'invalid {label}: {value!r}')
    return value


def decimal(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValueError(f'{label} must be a JSON number')
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError(f'{label} must be finite')
    return number


def length(value, label, *, positive=False):
    number = decimal(value, label)
    try:
        nm = nanometres(str(number))
    except LibraryError as exc:
        raise ValueError(f'{label} must be exact to 1 nm: {exc}') from exc
    if nm < 0 or (positive and nm == 0):
        raise ValueError(f'{label} must be {"positive" if positive else "nonnegative"}')
    return nm


def point(value, label):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f'{label} must be [x, y] mm')
    x, y = (decimal(v, label) for v in value)
    try:
        return nanometres(str(x)), nanometres(str(y))
    except LibraryError as exc:
        raise ValueError(f'{label} must be exact to 1 nm: {exc}') from exc


def text(number):
    """Exact positional decimal text without exponent or trailing zeros."""
    out = format(Decimal(number), 'f')
    if '.' in out:
        out = out.rstrip('0').rstrip('.')
    return '0' if out in ('-0', '') else out


def degrees(value):
    """Exact angle text normalized to [0, 360); KiCad may save -90 for 270."""
    if not value.is_finite():
        raise ValueError('rotation must be finite')
    return text((value % 360 + 360) % 360)


def mm(nm):
    return text(Decimal(nm).scaleb(-6))


def mm_point(p):
    return [mm(p[0]), mm(p[1])]


def cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def on_segment(p, a, b):
    return (cross(a, b, p) == 0 and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def intersects(a, b, c, d):
    d1, d2, d3, d4 = cross(c, d, a), cross(c, d, b), cross(a, b, c), cross(a, b, d)
    if ((d1 > 0) != (d2 > 0) and d1 and d2) and ((d3 > 0) != (d4 > 0) and d3 and d4):
        return True
    return on_segment(a, c, d) or on_segment(b, c, d) or on_segment(c, a, b) or on_segment(d, a, b)


def simple_polygon(vertices, label):
    """Refuse degenerate, overlapping or self-intersecting closed polygons."""
    n = len(vertices)
    if n < 3:
        raise ValueError(f'{label} needs at least three edges')
    edges = [(vertices[i], vertices[(i + 1) % n]) for i in range(n)]
    for i, (a, b) in enumerate(edges):
        if a == b:
            raise ValueError(f'{label} has a zero-length edge')
    for i in range(n):
        for j in range(i + 1, n):
            (a, b), (c, d) = edges[i], edges[j]
            if j == i + 1 or (i == 0 and j == n - 1):
                shared, p, q = (b, a, d) if j == i + 1 else (a, b, c)
                # Adjacent edges may share only their common vertex.
                if cross(shared, p, q) == 0 and (
                        (p[0] - shared[0]) * (q[0] - shared[0]) + (p[1] - shared[1]) * (q[1] - shared[1])) > 0:
                    raise ValueError(f'{label} is self-intersecting (overlapping adjacent edges)')
                if n == 3:
                    continue
                if j == i + 1 and (on_segment(a, c, d) or on_segment(d, a, b)):
                    raise ValueError(f'{label} is self-intersecting')
                if i == 0 and j == n - 1 and (on_segment(b, c, d) or on_segment(c, a, b)):
                    raise ValueError(f'{label} is self-intersecting')
            elif intersects(a, b, c, d):
                raise ValueError(f'{label} is self-intersecting')
    area2 = sum(cross((0, 0), a, b) for a, b in edges)
    if area2 == 0:
        raise ValueError(f'{label} has zero area')
    return area2


def inside(p, vertices):
    """Exact closed-polygon membership: boundary points are inside."""
    n, result = len(vertices), False
    for i in range(n):
        a, b = vertices[i], vertices[(i + 1) % n]
        if on_segment(p, a, b):
            return True
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + Fraction(p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if p[0] < x:
                result = not result
    return result


def strictly_inside(p, vertices):
    n = len(vertices)
    return inside(p, vertices) and not any(on_segment(p, vertices[i], vertices[(i + 1) % n]) for i in range(n))


def segment_inside(a, b, vertices):
    """Split a-b at every boundary contact and test each piece's midpoint."""
    params, n = {Fraction(0), Fraction(1)}, len(vertices)
    for i in range(n):
        c, d = vertices[i], vertices[(i + 1) % n]
        denominator = cross((0, 0), (b[0] - a[0], b[1] - a[1]), (d[0] - c[0], d[1] - c[1]))
        if denominator:
            t = Fraction(cross((0, 0), (c[0] - a[0], c[1] - a[1]), (d[0] - c[0], d[1] - c[1])), denominator)
            u = Fraction(cross((0, 0), (c[0] - a[0], c[1] - a[1]), (b[0] - a[0], b[1] - a[1])), denominator)
            if 0 <= t <= 1 and 0 <= u <= 1:
                params.add(t)
        elif cross(a, b, c) == 0:
            span = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
            for q in (c, d):
                t = Fraction((q[0] - a[0]) * (b[0] - a[0]) + (q[1] - a[1]) * (b[1] - a[1]), span)
                if 0 <= t <= 1:
                    params.add(t)
    ordered = sorted(params)
    for t0, t1 in zip(ordered, ordered[1:]):
        t = (t0 + t1) / 2
        if not inside((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])), vertices):
            return False
    return True


def distance2(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = Fraction((p[0] - a[0]) * dx + (p[1] - a[1]) * dy, dx * dx + dy * dy)
    t = min(max(t, Fraction(0)), Fraction(1))
    x, y = a[0] + t * dx - p[0], a[1] + t * dy - p[1]
    return x * x + y * y


def axis(direction):
    return {(1, 0): 'x+', (-1, 0): 'x-', (0, 1): 'y+', (0, -1): 'y-'}[direction]


def sign(value):
    return (value > 0) - (value < 0)


def validate_outline(value):
    fields(value, 'outline', ['edges'])
    edges = value['edges']
    if not isinstance(edges, list) or not 3 <= len(edges) <= MAX_VERTICES:
        raise ValueError(f'outline needs 3 to {MAX_VERTICES} named edges')
    names, parsed = set(), []
    for index, edge in enumerate(edges):
        fields(edge, f'outline edge {index}', ['name', 'start', 'end'])
        label = name(edge['name'], 'edge name')
        if label in names:
            raise ValueError(f'duplicate edge name: {label}')
        names.add(label)
        parsed.append((label, point(edge['start'], f'edge {label} start'), point(edge['end'], f'edge {label} end')))
    for (label, _, end), (following, start, _) in zip(parsed, parsed[1:] + parsed[:1]):
        if end != start:
            raise ValueError(f'outline is open: edge {label} ends at {mm_point(end)} '
                             f'but edge {following} starts at {mm_point(start)}')
    vertices = [start for _, start, _ in parsed]
    area2 = simple_polygon(vertices, 'outline')
    records = {}
    for label, start, end in parsed:
        dx, dy = end[0] - start[0], end[1] - start[1]
        record = {'name': label, 'start_mm': mm_point(start), 'end_mm': mm_point(end),
                  'axis_aligned': dx == 0 or dy == 0, 'length_mm': None, 'outward': None}
        if record['axis_aligned']:
            unit = (sign(dx), sign(dy))
            # Positive shoelace area: interior lies left of each directed edge.
            inward = (-unit[1], unit[0]) if area2 > 0 else (unit[1], -unit[0])
            record.update(length_mm=mm(abs(dx) + abs(dy)), outward=axis((-inward[0], -inward[1])),
                          _unit=unit, _inward=inward, _start=start, _length=abs(dx) + abs(dy))
        records[label] = record
    xs, ys = [v[0] for v in vertices], [v[1] for v in vertices]
    rectangle = (len(vertices) == 4 and all(r['axis_aligned'] for r in records.values())
                 and len(set(xs)) == 2 and len(set(ys)) == 2)
    return vertices, records, {
        'edges': [{k: v for k, v in r.items() if not k.startswith('_')} for r in records.values()],
        'area_mm2': text(Decimal(abs(area2)).scaleb(-12) / 2),
        'winding': 'positive_shoelace' if area2 > 0 else 'negative_shoelace',
        'axis_aligned_rectangle': rectangle,
        'bounds_mm': {'min': mm_point((min(xs), min(ys))), 'max': mm_point((max(xs), max(ys)))}}


def copper_names(count):
    return ['F.Cu'] + [f'In{i}.Cu' for i in range(1, count - 1)] + ['B.Cu']


def validate_stackup(value, count):
    if isinstance(count, bool) or not isinstance(count, int) or not 2 <= count <= MAX_COPPER:
        raise ValueError(f'layer_count must be an integer from 2 to {MAX_COPPER}')
    fields(value, 'stackup', ['thickness_mm', 'layers'])
    total = length(value['thickness_mm'], 'stackup thickness_mm', positive=True)
    layers = value['layers']
    if not isinstance(layers, list) or not layers:
        raise ValueError('stackup layers must be a nonempty list')
    records, copper, names, summed = [], [], set(), 0
    for index, layer in enumerate(layers):
        if not isinstance(layer, dict) or layer.get('kind') not in ('copper', 'dielectric'):
            raise ValueError(f'stackup layer {index} kind must be copper or dielectric')
        expected = 'copper' if index % 2 == 0 else 'dielectric'
        if layer['kind'] != expected:
            raise ValueError('stackup must alternate copper and dielectric, starting and ending with copper')
        if layer['kind'] == 'copper':
            fields(layer, f'stackup layer {index}', ['name', 'kind', 'thickness_mm', 'copper_weight_oz'])
            weight = decimal(layer['copper_weight_oz'], 'copper_weight_oz')
            if weight <= 0:
                raise ValueError('copper_weight_oz must be positive')
            copper.append(layer['name'])
        else:
            fields(layer, f'stackup layer {index}', ['name', 'kind', 'thickness_mm'])
            weight = None
        label = layer['name']
        if not isinstance(label, str) or not label.strip() or len(label) > 64 or label in names:
            raise ValueError(f'invalid or duplicate stackup layer name: {label!r}')
        names.add(label)
        thickness = length(layer['thickness_mm'], f'stackup layer {label} thickness_mm', positive=True)
        summed += thickness
        records.append({'name': label, 'kind': layer['kind'], 'thickness_mm': mm(thickness),
                        **({'copper_weight_oz': text(weight)} if weight is not None else {})})
    if len(layers) % 2 == 0:
        raise ValueError('stackup must alternate copper and dielectric, starting and ending with copper')
    if copper != copper_names(count):
        raise ValueError(f'layer_count {count} requires copper layers {copper_names(count)}, got {copper}')
    if summed != total:
        raise ValueError(f'stackup layers sum to {mm(summed)} mm, not thickness_mm {mm(total)}')
    return {'thickness_mm': mm(total), 'layers': records}


def validate_profile(value, profile):
    if value is None:
        if profile is not None:
            raise ValueError('profile bytes supplied but the envelope references no fab profile')
        return {'status': 'unselected'}, [{'predicate': 'fab_profile_selected',
                                            'reason': 'no fabricator capability profile is referenced'}]
    fields(value, 'fab_profile', ['name', 'sha256'])
    if not isinstance(value['name'], str) or not value['name'].strip() or len(value['name']) > 128:
        raise ValueError('fab_profile name must be nonempty text')
    if not isinstance(value['sha256'], str) or not SHA256.fullmatch(value['sha256']):
        raise ValueError('fab_profile sha256 must be 64 lowercase hex digits')
    record = {'status': 'referenced', 'name': value['name'], 'sha256': value['sha256']}
    unresolved = [{'predicate': 'fab_profile_supports_layer_count', 'reason': PROFILE_PENDING}]
    if profile is None:
        record['bytes'] = 'not_supplied'
        unresolved.insert(0, {'predicate': 'fab_profile_bytes', 'reason': 'profile bytes not supplied'})
    elif hashlib.sha256(profile).hexdigest() != value['sha256']:
        raise ValueError('fab profile bytes do not match the referenced sha256')
    else:
        record['bytes'] = 'sha256_match'
    return record, unresolved


def unique_names(items, key, label, namespace):
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError(f'{label} must be a list of at most {MAX_ITEMS}')
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f'{label} entries must be objects')
        value = name(item.get(key), f'{label} {key}')
        if value in namespace:
            raise ValueError(f'duplicate reference or identifier: {value}')
        namespace.add(value)


def validate_holes(holes, vertices):
    records = []
    for hole in holes:
        fields(hole, f"mounting hole {hole['id']}", ['id', 'at', 'drill_mm', 'plated'])
        at = point(hole['at'], f"mounting hole {hole['id']} at")
        drill = length(hole['drill_mm'], f"mounting hole {hole['id']} drill_mm", positive=True)
        if not isinstance(hole['plated'], bool):
            raise ValueError('plated must be boolean')
        n = len(vertices)
        if not strictly_inside(at, vertices) or any(
                4 * distance2(at, vertices[i], vertices[(i + 1) % n]) < drill * drill for i in range(n)):
            raise ValueError(f"mounting hole {hole['id']} is not inside the outline")
        records.append({'id': hole['id'], 'at_mm': mm_point(at), 'drill_mm': mm(drill), 'plated': hole['plated']})
    return records


def validate_keepouts(keepouts, vertices, copper):
    records = []
    for keepout in keepouts:
        kid = keepout['id']
        fields(keepout, f'keepout {kid}', ['id', 'polygon', 'layers', 'forbid'])
        polygon = keepout['polygon']
        if not isinstance(polygon, list) or not 3 <= len(polygon) <= MAX_VERTICES:
            raise ValueError(f'keepout {kid} polygon needs 3 to {MAX_VERTICES} vertices')
        points = [point(p, f'keepout {kid} vertex') for p in polygon]
        simple_polygon(points, f'keepout {kid}')
        if not all(segment_inside(points[i], points[(i + 1) % len(points)], vertices) for i in range(len(points))):
            raise ValueError(f'keepout {kid} is not contained in the outline')
        layers, forbid = keepout['layers'], keepout['forbid']
        if (not isinstance(layers, list) or not layers or len(set(map(str, layers))) != len(layers)
                or any(layer not in copper for layer in layers)):
            raise ValueError(f'keepout {kid} layers must be unique stackup copper layer names')
        if (not isinstance(forbid, list) or not forbid or len(set(map(str, forbid))) != len(forbid)
                or any(item not in FORBID for item in forbid)):
            raise ValueError(f'keepout {kid} forbid must be a unique nonempty subset of {list(FORBID)}')
        records.append({'id': kid, 'polygon_mm': [mm_point(p) for p in points],
                        'layers': [c for c in copper if c in layers],
                        'forbid': [f for f in FORBID if f in forbid]})
    return records


def validate_fixed(footprints, vertices, edges):
    records = []
    for footprint in footprints:
        ref = footprint['reference']
        fields(footprint, f'fixed footprint {ref}', ['reference', 'side', 'edge', 'position_mm', 'inset_mm',
                                                     'rotation_deg', 'insertion', 'tolerance_mm'])
        if footprint['side'] not in SIDES:
            raise ValueError(f'fixed footprint {ref} side must be front or back')
        if not isinstance(footprint['edge'], str) or footprint['edge'] not in edges:
            raise ValueError(f"fixed footprint {ref} edge {footprint['edge']!r} is not an outline edge name")
        edge = edges[footprint['edge']]
        if not edge['axis_aligned']:
            raise ValueError(f"fixed footprint {ref} edge {edge['name']} is oblique; v1 supports axis-aligned edges")
        position = length(footprint['position_mm'], f'fixed footprint {ref} position_mm')
        if position > edge['_length']:
            raise ValueError(f"fixed footprint {ref} position {mm(position)} mm is outside edge "
                             f"{edge['name']} of length {edge['length_mm']} mm")
        inset = length(footprint['inset_mm'], f'fixed footprint {ref} inset_mm')
        tolerance = length(footprint['tolerance_mm'], f'fixed footprint {ref} tolerance_mm')
        rotation = decimal(footprint['rotation_deg'], f'fixed footprint {ref} rotation_deg')
        if not 0 <= rotation < 360:
            raise ValueError(f'fixed footprint {ref} rotation_deg must be in [0, 360)')
        insertion = footprint['insertion']
        if insertion not in INSERTIONS:
            raise ValueError(f'fixed footprint {ref} insertion must be one of {list(INSERTIONS)}')
        if (footprint['side'], insertion) in (('front', 'from_bottom'), ('back', 'from_top')):
            raise ValueError(f'fixed footprint {ref} on the {footprint["side"]} side cannot be mated {insertion}')
        (sx, sy), (ux, uy), (ix, iy) = edge['_start'], edge['_unit'], edge['_inward']
        anchor = (sx + ux * position + ix * inset, sy + uy * position + iy * inset)
        if not inside(anchor, vertices):
            raise ValueError(f'fixed footprint {ref} anchor {mm_point(anchor)} is outside the outline')
        records.append({'reference': ref, 'side': footprint['side'], 'layer': SIDES[footprint['side']],
                        'edge': edge['name'], 'position_mm': mm(position), 'inset_mm': mm(inset),
                        'anchor_mm': mm_point(anchor), 'rotation_deg': degrees(rotation), 'insertion': insertion,
                        'mating_axis': edge['outward'] if insertion == 'through_edge' else
                        ('z-' if insertion == 'from_top' else 'z+'),
                        'tolerance_mm': mm(tolerance)})
    return records


def validate(raw, *, profile=None):
    """Return the validator record or raise ValueError; never a partial pass."""
    envelope = load(raw)
    fields(envelope, 'envelope', ['schema', 'id', 'outline', 'layer_count', 'stackup', 'fab_profile',
                                  'mounting_holes', 'keepouts', 'fixed_footprints'])
    if envelope['schema'] != SCHEMA:
        raise ValueError(f'schema must be {SCHEMA}')
    eid = name(envelope['id'], 'envelope id', ENVELOPE_ID)
    if profile is not None and not isinstance(profile, bytes):
        raise ValueError('profile must be bytes')
    vertices, edges, outline = validate_outline(envelope['outline'])
    stackup = validate_stackup(envelope['stackup'], envelope['layer_count'])
    copper = copper_names(envelope['layer_count'])
    fab, unresolved = validate_profile(envelope['fab_profile'], profile)
    namespace = set()
    unique_names(envelope['fixed_footprints'], 'reference', 'fixed footprint', namespace)
    unique_names(envelope['mounting_holes'], 'id', 'mounting hole', namespace)
    unique_names(envelope['keepouts'], 'id', 'keepout', namespace)
    return {'schema': 'agent-ee.envelope-validation/1', 'envelope_id': eid,
            'envelope_sha256': hashlib.sha256(raw).hexdigest(),
            'structural': 'pass', 'outcome': 'unknown' if unresolved else 'pass',
            'unresolved': unresolved, 'acceptance_ready': False,
            'outline': outline, 'layer_count': envelope['layer_count'], 'copper_layers': copper,
            'stackup': stackup, 'fab_profile': fab,
            'mounting_holes': validate_holes(envelope['mounting_holes'], vertices),
            'keepouts': validate_keepouts(envelope['keepouts'], vertices, copper),
            'fixed_footprints': validate_fixed(envelope['fixed_footprints'], vertices, edges)}


def obligation(eid, suffix, field, value, statement, source_path, kinds):
    return {'id': f'R-envelope-{eid}-{suffix}',
            'statement': f'Envelope {eid} field {field}: {statement}',
            'source': {'path': source_path, 'locator': field}, 'intent': 'acceptance',
            'field_sha256': hashlib.sha256(canonical(value)).hexdigest(), 'check_kinds': kinds}


def point_text(p):
    return f'({p[0]}, {p[1]})'


def derive_requirements(record, source_path):
    """Deterministic obligations, each citing the exact envelope field."""
    source_path = str(safe_relative(source_path))
    eid = record['envelope_id']
    out = []
    edges = '; '.join(f"{e['name']} {point_text(e['start_mm'])} to {point_text(e['end_mm'])}"
                      for e in record['outline']['edges'])
    out.append(obligation(eid, 'outline', '/outline', record['outline'],
                          f'the Edge.Cuts board outline is exactly the closed polygon {edges} mm, '
                          'with no other outline geometry.', source_path, CHECK_KINDS['outline']))
    out.append(obligation(eid, 'layer-count', '/layer_count', record['copper_layers'],
                          f"the board has exactly {record['layer_count']} copper layers: "
                          f"{', '.join(record['copper_layers'])}.", source_path, CHECK_KINDS['layer_count']))
    layers = '; '.join(f"{l['name']} {l['kind']} {l['thickness_mm']} mm" +
                       (f" {l['copper_weight_oz']} oz" if 'copper_weight_oz' in l else '')
                       for l in record['stackup']['layers'])
    out.append(obligation(eid, 'stackup', '/stackup', record['stackup'],
                          f"the fabricated board is {record['stackup']['thickness_mm']} mm thick with stackup, "
                          f'top to bottom: {layers}.', source_path, CHECK_KINDS['stackup']))
    if record['fab_profile']['status'] == 'referenced':
        profile = {k: record['fab_profile'][k] for k in ('name', 'sha256')}
        out.append(obligation(eid, 'fab-profile', '/fab_profile', profile,
                              f"the board satisfies captured fabricator capability profile {profile['name']} "
                              f"(sha256 {profile['sha256']}).", source_path, CHECK_KINDS['fab_profile']))
    for index, hole in enumerate(record['mounting_holes']):
        out.append(obligation(eid, f"hole-{hole['id']}", f'/mounting_holes/{index}', hole,
                              f"mounting hole {hole['id']} is a {'plated' if hole['plated'] else 'non-plated'} "
                              f"{hole['drill_mm']} mm hole centred at {point_text(hole['at_mm'])} mm.",
                              source_path, CHECK_KINDS['mounting_holes']))
    for index, keepout in enumerate(record['keepouts']):
        polygon = ', '.join(point_text(p) for p in keepout['polygon_mm'])
        out.append(obligation(eid, f"keepout-{keepout['id']}", f'/keepouts/{index}', keepout,
                              f"keepout {keepout['id']} forbids {', '.join(keepout['forbid'])} on "
                              f"{', '.join(keepout['layers'])} inside polygon {polygon} mm.",
                              source_path, CHECK_KINDS['keepouts']))
    for index, fixed in enumerate(record['fixed_footprints']):
        out.append(obligation(eid, f"fixed-{fixed['reference']}", f'/fixed_footprints/{index}', fixed,
                              f"footprint {fixed['reference']} is fixed on the {fixed['side']} side "
                              f"({fixed['layer']}) with its origin {fixed['position_mm']} mm along edge "
                              f"{fixed['edge']} and {fixed['inset_mm']} mm inward, at "
                              f"{point_text(fixed['anchor_mm'])} mm, rotation {fixed['rotation_deg']} deg, "
                              f"within {fixed['tolerance_mm']} mm; it is mated {fixed['insertion']} "
                              f"(mating axis {fixed['mating_axis']}).", source_path, CHECK_KINDS['fixed_footprints']))
    return {'schema': 'agent-ee.envelope-requirements/1', 'envelope_id': eid,
            'envelope_sha256': record['envelope_sha256'], 'source_path': source_path,
            'complete': not record['unresolved'], 'unresolved': record['unresolved'],
            'requirements': out}


def derive(raw, *, source_path, profile=None):
    record = validate(raw, profile=profile)
    requirements = derive_requirements(record, source_path)
    dialog = {'schema': 'agent-ee.requirements/1', 'requirements': [
        {'id': r['id'], 'text': f"{r['statement']} [source {r['source']['path']}#{r['source']['locator']}, "
                                f"envelope sha256 {record['envelope_sha256']}]",
         'source_question_ids': []} for r in requirements['requirements']]}
    markdown = ''.join(f"{r['id']}: {r['statement']}\n  Source: {r['source']['path']} {r['source']['locator']} "
                       f"(field sha256 {r['field_sha256']}); check kinds: {', '.join(r['check_kinds'])}\n\n"
                       for r in requirements['requirements'])
    markdown += ''.join(f"Unresolved: {u['predicate']}: {u['reason']}\n" for u in record['unresolved'])
    return {'envelope.json': raw, 'envelope-validation.json': canonical(record) + b'\n',
            'envelope-requirements.json': canonical(requirements) + b'\n',
            'requirements.md': markdown.encode('utf-8'),
            'dialog-requirements.json': canonical(dialog) + b'\n'}


def require_obligations(scope, requirements):
    """Refuse a scope/3 baseline that drops, alters or vacuously maps an obligation."""
    if not isinstance(scope, dict) or scope.get('schema') != 'agent-ee.scope/3':
        raise ValueError('envelope obligations require an agent-ee.scope/3 baseline')
    if not isinstance(requirements, dict) or requirements.get('schema') != 'agent-ee.envelope-requirements/1':
        raise ValueError('expected agent-ee.envelope-requirements/1')
    baseline = {r.get('id'): r for r in scope.get('baseline', {}).get('requirements', []) if isinstance(r, dict)}
    kinds = {c.get('id'): c.get('kind') for c in scope.get('checks', []) if isinstance(c, dict)}
    for item in requirements['requirements']:
        rid, mapped = item['id'], baseline.get(item['id'])
        if mapped is None or not mapped.get('checks'):
            raise ValueError(f'envelope obligation {rid} has no mapped check')
        if (mapped.get('statement') != item['statement'] or mapped.get('source') != item['source']
                or mapped.get('intent') != 'acceptance'):
            raise ValueError(f'envelope obligation {rid} statement, source or acceptance intent differs')
        wrong = sorted(c for c in mapped['checks'] if kinds.get(c) not in item['check_kinds'])
        if wrong:
            raise ValueError(f"envelope obligation {rid} maps checks {wrong} outside kinds {item['check_kinds']}")
    if not requirements['complete']:
        raise ValueError('envelope requirements are incomplete: unresolved ' +
                         ', '.join(u['predicate'] for u in requirements['unresolved']))


def atoms(node):
    return [x for x in node[1:] if isinstance(x, str)]


def board_facts(pcb):
    """Static facts from saved .kicad_pcb bytes; unsupported outline items are listed."""
    try:
        return saved_facts(parse_sexpr(pcb))
    except (TypeError, IndexError, AttributeError) as exc:
        raise ValueError(f'malformed board item: {exc}') from exc


def copper_order(layer):
    return (0, 0) if layer == 'F.Cu' else (2, 0) if layer == 'B.Cu' else (1, int(layer[2:-3]))


def saved_facts(root):
    if root[0] != 'kicad_pcb':
        raise ValueError('expected kicad_pcb')

    def one(node, key):
        found = children(node, key)
        return found[0] if len(found) == 1 else None

    layer_table = one(root, 'layers')
    copper = sorted((n[1] for n in (layer_table or [])[1:] if isinstance(n, list) and len(n) >= 3
                     and isinstance(n[1], str) and re.fullmatch(r'F\.Cu|B\.Cu|In[1-9][0-9]?\.Cu', n[1])),
                    key=copper_order)
    general = one(root, 'general')
    thickness = one(general, 'thickness') if general else None
    stackup = None
    setup = one(root, 'setup')
    native = one(setup, 'stackup') if setup else None
    if native is not None:
        stackup = []
        for layer in children(native, 'layer'):
            kind, value = one(layer, 'type'), one(layer, 'thickness')
            if kind is None:
                continue
            stackup.append({'name': layer[1], 'type': atoms(kind)[0] if atoms(kind) else None,
                            'thickness_nm': nanometres(atoms(value)[0]) if value and atoms(value) else None})
    segments, other = [], []
    for item in root[1:]:
        if not isinstance(item, list) or not item or not str(item[0]).startswith('gr_'):
            continue
        layer = one(item, 'layer')
        if not layer or atoms(layer) != ['Edge.Cuts']:
            continue
        if item[0] == 'gr_line':
            start, end = (tuple(nanometres(v) for v in atoms(one(item, k))) for k in ('start', 'end'))
            segments.append((start, end))
        elif item[0] == 'gr_rect':
            (x0, y0), (x1, y1) = (tuple(nanometres(v) for v in atoms(one(item, k))) for k in ('start', 'end'))
            segments += [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
        else:
            other.append(item[0])
    footprints = {}
    for footprint in children(root, 'footprint'):
        reference = [atoms(p)[1] for p in children(footprint, 'property') if atoms(p)[:1] == ['Reference']]
        at, layer = one(footprint, 'at'), one(footprint, 'layer')
        for p in children(footprint, 'fp_line') + children(footprint, 'fp_rect') + children(footprint, 'fp_arc') \
                + children(footprint, 'fp_circle') + children(footprint, 'fp_poly'):
            if one(p, 'layer') and atoms(one(p, 'layer')) == ['Edge.Cuts']:
                other.append('footprint ' + p[0])
        if len(reference) != 1 or not at or not layer:
            raise ValueError('footprint without one Reference, at and layer')
        values = atoms(at)
        # Duplicate references stay listed; a fixed footprint needs exactly one match.
        footprints.setdefault(reference[0], []).append({
            'layer': atoms(layer)[0], 'at': (nanometres(values[0]), nanometres(values[1])),
            'rotation_deg': degrees(Decimal(values[2]) if len(values) > 2 else Decimal(0))})
    return {'copper_layers': copper,
            'thickness_nm': nanometres(atoms(thickness)[0]) if thickness and atoms(thickness) else None,
            'stackup': stackup, 'edge_segments': segments, 'other_edge_cuts': sorted(other),
            'footprints': footprints}


def round_trip(raw, pcb):
    """Compare board-derivable envelope fields with a saved board, field by field."""
    envelope, record, facts = load(raw), validate(raw), board_facts(pcb)
    results = []

    def add(field, status, detail):
        results.append({'field': field, 'status': status, 'detail': detail})
    declared = {frozenset((point(e['start'], 'start'), point(e['end'], 'end'))) for e in envelope['outline']['edges']}
    saved = {frozenset(s) for s in facts['edge_segments']}
    add('/outline', 'match' if declared == saved and len(saved) == len(facts['edge_segments'])
        and not facts['other_edge_cuts'] else 'mismatch',
        {'missing': len(declared - saved), 'extra': len(saved - declared), 'other': facts['other_edge_cuts']})
    add('/layer_count', 'match' if facts['copper_layers'] == record['copper_layers'] else 'mismatch',
        {'board': facts['copper_layers']})
    total = length(envelope['stackup']['thickness_mm'], 'thickness')
    add('/stackup/thickness_mm', 'match' if facts['thickness_nm'] == total else 'mismatch',
        {'board_mm': None if facts['thickness_nm'] is None else mm(facts['thickness_nm'])})
    for index, layer in enumerate(record['stackup']['layers']):
        pointer = f'/stackup/layers/{index}'
        if facts['stackup'] is None:
            add(pointer + '/thickness_mm', 'not_in_board', {'reason': 'board saves no stackup section'})
        else:
            types = ('copper',) if layer['kind'] == 'copper' else ('core', 'prepreg')
            native = [l for l in facts['stackup'] if l['type'] in types]
            position = index // 2
            found = native[position] if position < len(native) else None
            add(pointer + '/thickness_mm', 'match' if found and found['thickness_nm'] is not None and
                mm(found['thickness_nm']) == layer['thickness_mm'] else 'mismatch', {'board': found and found['name']})
        if layer['kind'] == 'copper':
            add(pointer + '/copper_weight_oz', 'not_in_board', {'reason': 'KiCad boards save copper thickness only'})
    for index, fixed in enumerate(record['fixed_footprints']):
        matches = facts['footprints'].get(fixed['reference'], [])
        saved = matches[0] if len(matches) == 1 else None
        anchor = point([Decimal(v) for v in fixed['anchor_mm']], 'anchor')
        tolerance = length(Decimal(fixed['tolerance_mm']), 'tolerance')
        ok = (saved is not None and saved['layer'] == fixed['layer'] and saved['rotation_deg'] == fixed['rotation_deg']
              and (saved['at'][0] - anchor[0]) ** 2 + (saved['at'][1] - anchor[1]) ** 2 <= tolerance ** 2)
        add(f'/fixed_footprints/{index}', 'match' if ok else 'mismatch',
            {'board_matches': len(matches),
             'board': None if saved is None else {'layer': saved['layer'], 'at_mm': mm_point(saved['at']),
                                                  'rotation_deg': saved['rotation_deg']}})
    for key in ('mounting_holes', 'keepouts'):
        for index, _ in enumerate(envelope[key]):
            add(f'/{key}/{index}', 'not_compared', {'reason': 'v1 round trip compares outline, layers, stackup '
                                                              'and fixed footprints only'})
    return {'schema': 'agent-ee.envelope-round-trip/1', 'envelope_sha256': record['envelope_sha256'],
            'board_sha256': hashlib.sha256(pcb).hexdigest(), 'fields': results,
            'outcome': 'mismatch' if any(r['status'] == 'mismatch' for r in results) else 'match'}


def write_artifacts(artifacts, output):
    """Write only into a new directory; existing outputs stay intact."""
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    for filename, data in artifacts.items():
        (output / filename).write_bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('envelope', type=Path)
    parser.add_argument('--source-path', required=True, help='capture-relative path cited by derived requirements')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--profile', type=Path, help='captured fab profile bytes referenced by sha256')
    parser.add_argument('--board', type=Path, help='saved .kicad_pcb to compare field by field')
    args = parser.parse_args()
    try:
        raw = args.envelope.read_bytes()
        artifacts = derive(raw, source_path=args.source_path,
                           profile=args.profile.read_bytes() if args.profile else None)
        trip = None
        if args.board:
            trip = round_trip(raw, args.board.read_bytes())
            artifacts['round-trip.json'] = canonical(trip) + b'\n'
        write_artifacts(artifacts, args.out)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'envelope refused or unavailable: {exc}\n')
    unresolved = json.loads(artifacts['envelope-validation.json'])['unresolved']
    return 1 if unresolved or (trip and trip['outcome'] != 'match') else 0


if __name__ == '__main__':
    raise SystemExit(main())

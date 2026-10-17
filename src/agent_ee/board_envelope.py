"""Offline board envelope record (agent-ee.envelope/1) and derived obligations.

Geometry is exact integer nanometres in KiCad board axes (y grows downward).
A structural pass is not fabrication or placement acceptance: obligations are
only satisfied later by mapped coordinator checks. Fabricator support is read
only through the AGENTEE-75 profile compiler, from sourced minimum-tier limits;
it is never defaulted, and stays unresolved while a source is unverified.
"""
import argparse
from decimal import Decimal, DecimalException
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re

from .candidate import safe_relative
from .storage import canonical, digest
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
# Rotations and copper weights stay exact decimals. Values needing more than this
# many digits before or after the point are refused, never rounded.
MAX_PLACES = 32
# Check kinds that may evaluate each field. A kind name alone never covers an
# obligation: only a (field, kind) entry in BINDINGS, whose validator proves the
# mapped check evaluates that exact field, does. fab_package (AGENTEE-94) is
# proposed and unregistered; fab_profile max_layers bounds only a maximum, so it
# cannot establish an exact layer count.
CHECK_KINDS = {'outline': ['placement', 'board_envelope'],
               'layer_count': ['fab_package', 'board_envelope'],
               'stackup': ['fab_package', 'board_envelope'], 'fab_profile': ['fab_profile'],
               'mounting_holes': ['placement'], 'keepouts': ['placement'],
               'fixed_footprints': ['placement']}
# Why a field without a binding has unknown coverage; reported, never assumed.
UNBOUND = {
    'outline': 'needs an exact native board_envelope outline/1 check',
    'layer_count': 'needs an exact native board_envelope layers/1 check',
    'stackup': 'needs native stackup facts and qualified captured copper_weight_oz authority',
    'fab_profile': 'needs one fab_profile check over the referenced profile bytes',
    'mounting_holes': 'no registered predicate checks declared mounting holes',
    'keepouts': 'no registered predicate binds a declared keepout to a saved rule area '
                '(placement footprint-keepout/1 grades only rule areas already saved)',
    'fixed_footprints': 'needs one placement connector-edge/1 check over these envelope bytes, '
                        'and an envelope with exactly one fixed footprint'}


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


def fixed_point(n, places):
    """Exact text of integer n / 10**places, without exponent or trailing zeros."""
    whole, fraction = divmod(abs(n), 10 ** places)
    fraction = str(fraction).rjust(places, '0').rstrip('0')
    return ('-' if n < 0 else '') + str(whole) + ('.' + fraction if fraction else '')


def scaled(value, label):
    """Integer n with value == n / 10**MAX_PLACES exactly; no Decimal context is used."""
    try:
        number = Decimal(value) if not isinstance(value, str) or len(value) <= 128 else None
    except DecimalException:
        number = None
    if number is None or not number.is_finite():
        raise ValueError(f'{label} must be a finite decimal token')
    sign, digits, exponent = number.as_tuple()
    coefficient = ''.join(map(str, digits)).rstrip('0')
    if not coefficient:
        return 0
    exponent += len(digits) - len(coefficient)
    if exponent < -MAX_PLACES or len(coefficient) + exponent > MAX_PLACES:
        raise ValueError(f'{label} is not exactly representable with at most '
                         f'{MAX_PLACES} digits before and after the point')
    n = int(coefficient) * 10 ** (exponent + MAX_PLACES)
    return -n if sign else n


def degrees(value, label):
    """Exact angle text in [0, 360); KiCad may save -90 for 270. Never rounds."""
    return fixed_point(scaled(value, label) % (360 * 10 ** MAX_PLACES), MAX_PLACES)


def mm(nm):
    return fixed_point(nm, 6)


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
                  'axis_aligned': dx == 0 or dy == 0, 'length_mm': None, 'outward': None,
                  '_start': start, '_end': end}
        if record['axis_aligned']:
            unit = (sign(dx), sign(dy))
            # Positive shoelace area: interior lies left of each directed edge.
            inward = (-unit[1], unit[0]) if area2 > 0 else (unit[1], -unit[0])
            record.update(length_mm=mm(abs(dx) + abs(dy)), outward=axis((-inward[0], -inward[1])),
                          _unit=unit, _inward=inward, _length=abs(dx) + abs(dy))
        records[label] = record
    xs, ys = [v[0] for v in vertices], [v[1] for v in vertices]
    rectangle = (len(vertices) == 4 and all(r['axis_aligned'] for r in records.values())
                 and len(set(xs)) == 2 and len(set(ys)) == 2)
    return vertices, records, {
        'edges': [{k: v for k, v in r.items() if not k.startswith('_')} for r in records.values()],
        # Twice the area in nm^2: area_mm2 = |area2| / (2 * 10**12) = 5 |area2| / 10**13.
        'area_mm2': fixed_point(5 * abs(area2), 13),
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
            weight = scaled(decimal(layer['copper_weight_oz'], 'copper_weight_oz'), 'copper_weight_oz')
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
                        **({'copper_weight_oz': fixed_point(weight, MAX_PLACES)} if weight is not None else {})})
    if len(layers) % 2 == 0:
        raise ValueError('stackup must alternate copper and dielectric, starting and ending with copper')
    if copper != copper_names(count):
        raise ValueError(f'layer_count {count} requires copper layers {copper_names(count)}, got {copper}')
    if summed != total:
        raise ValueError(f'stackup layers sum to {mm(summed)} mm, not thickness_mm {mm(total)}')
    return {'thickness_mm': mm(total), 'layers': records}


def profile_document(profile):
    """Profile bytes as the AGENTEE-75 compiler reads them; duplicate keys are refused."""
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f'duplicate JSON key in fab profile: {key}')
            value[key] = item
        return value
    try:
        return json.loads(profile.decode('utf-8'), object_pairs_hook=unique)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError(f'fab profile is not UTF-8 JSON: {exc}') from exc


def profile_support(document, captured, inventory):
    """Evaluate envelope support through fab_profile's own compiler and geometry.

    Only a ready (source-verified) minimum-tier limit can resolve a predicate;
    preferred tiers classify surcharges and never decide support. Board size is
    compared on fixed saved axes, x to x and y to y, exactly as fab-profile/1.
    """
    from .fab_profile import compile_profile, evaluate_geometry
    files, inputs = captured if captured is not None else ({}, Path(os.devnull))
    try:
        compiled = compile_profile(document, files, inputs)
    except (TypeError, KeyError, AttributeError) as exc:
        raise ValueError(f'malformed fab profile: {exc}') from exc
    minima = {c['expected']['key']: c for c in compiled['constraints'] if c['expected']['tier'] == 'minimum'}
    support, unresolved = [], []
    for key, predicate in (('max_layers', 'fab_profile_supports_layer_count'),
                           ('max_board_mm', 'fab_profile_supports_board_size')):
        constraint = minima.get(key)
        if constraint is None:
            if key == 'max_layers':
                unresolved.append({'predicate': predicate,
                                   'reason': 'profile states no minimum-tier max_layers limit'})
            continue
        if constraint['execution'] != 'ready':
            unresolved.append({'predicate': predicate, 'limit_id': constraint['id'],
                               'reason': 'limit source not verified: ' + constraint['reason']})
            continue
        result = evaluate_geometry(constraint, inventory)
        if result['status'] == 'violation':
            raise ValueError(f"fab profile {document['name']} limit {constraint['id']} ({key} "
                             f"{constraint['expected']['value']}) does not support the envelope: {result['actual']}"
                             + (' on fixed saved axes; no rotation is granted' if key == 'max_board_mm' else ''))
        if result['status'] != 'satisfied':
            unresolved.append({'predicate': predicate, 'limit_id': constraint['id'],
                               'reason': result.get('reason', 'not evaluated')})
            continue
        support.append({'predicate': predicate, 'limit_id': constraint['id'], 'key': key,
                        'value': constraint['expected']['value'], 'actual': result['actual'],
                        'status': 'satisfied'})
    return compiled, support, unresolved


def validate_profile(value, profile, captured, inventory):
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
    if profile is None:
        record['bytes'] = 'not_supplied'
        return record, [{'predicate': 'fab_profile_bytes', 'reason': 'profile bytes not supplied'}]
    if hashlib.sha256(profile).hexdigest() != value['sha256']:
        raise ValueError('fab profile bytes do not match the referenced sha256')
    record['bytes'] = 'sha256_match'
    document = profile_document(profile)
    compiled, support, unresolved = profile_support(document, captured, inventory)
    if compiled['name'] != value['name']:
        raise ValueError(f"fab profile is named {compiled['name']!r}, not the referenced {value['name']!r}")
    # source_paths lists only cited sources found in the capture inventory.
    record.update(as_of=compiled['as_of'], support=support, sources_verified=captured is not None,
                  source_paths=compiled['immutable_inputs'])
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
                        'anchor_mm': mm_point(anchor),
                        'rotation_deg': degrees(rotation, f'fixed footprint {ref} rotation_deg'),
                        'insertion': insertion,
                        'mating_axis': edge['outward'] if insertion == 'through_edge' else
                        ('z-' if insertion == 'from_top' else 'z+'),
                        'tolerance_mm': mm(tolerance)})
    return records


def declared_inventory(count, copper, edges):
    """The envelope's own declared facts in fab_profile's inventory shape.

    The outline is qualified by fab_profile's polygon routine, with edge names as
    locators, so profile limits read the declaration exactly as they read a board.
    """
    from .fab_profile import simple_polygon
    segments = sorted((e['name'], tuple(e['_start']), tuple(e['_end'])) for e in edges.values())
    extents, problem = simple_polygon(segments)
    inventory = {'copper_layers': count, 'copper_layer_names': copper, 'outline_xy_mm': extents,
                 'outline_ids': sorted(edges), 'smt_ids': []}
    if problem:
        inventory['outline_problem'] = {'reason': problem[0], 'locator_ids': problem[1]}
    return inventory


def validate(raw, *, profile=None, captured=None):
    """Return the validator record or raise ValueError; never a partial pass.

    captured=(files, inputs) is a capture inventory against which the profile's
    cited sources are verified; without it no limit is ready, so profile support
    stays unresolved rather than assumed.
    """
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
    fab, unresolved = validate_profile(envelope['fab_profile'], profile, captured,
                                       declared_inventory(envelope['layer_count'], copper, edges))
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
                          f"the saved design and manufacturing request specify {record['stackup']['thickness_mm']} mm thickness and stackup, "
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


def dialog_requirements(requirements):
    """Obligations as stage-one original requirements (agent-ee.requirements/1, AGENTEE-25)."""
    return {'schema': 'agent-ee.requirements/1', 'requirements': [
        {'id': r['id'], 'text': f"{r['statement']} [source {r['source']['path']}#{r['source']['locator']}, "
                                f"envelope sha256 {requirements['envelope_sha256']}]",
         'source_question_ids': []} for r in requirements['requirements']]}


def derive(raw, *, source_path, profile=None, captured=None):
    record = validate(raw, profile=profile, captured=captured)
    requirements = derive_requirements(record, source_path)
    markdown = ''.join(f"{r['id']}: {r['statement']}\n  Source: {r['source']['path']} {r['source']['locator']} "
                       f"(field sha256 {r['field_sha256']}); check kinds: {', '.join(r['check_kinds'])}\n\n"
                       for r in requirements['requirements'])
    markdown += ''.join(f"Unresolved: {u['predicate']}: {u['reason']}\n" for u in record['unresolved'])
    return {'envelope.json': raw, 'envelope-validation.json': canonical(record) + b'\n',
            'envelope-requirements.json': canonical(requirements) + b'\n',
            'requirements.md': markdown.encode('utf-8'),
            'dialog-requirements.json': canonical(dialog_requirements(requirements)) + b'\n'}


def stage_one_requirements(original, envelope_dialog):
    """Append envelope obligations to stage-one original requirements, unchanged.

    Both inputs are agent-ee.requirements/1 bytes accepted by the AGENTEE-25
    dialog validator: the original set (for example an AGENTEE-26 intent import)
    and an envelope's dialog-requirements.json. The result is the dialog's
    --requirements input, so every obligation enters the owner's decision record
    with its ID, text and envelope hash. Collisions are refused, never merged.
    """
    from .design_dialog import validate_requirements
    first, second = validate_requirements(original), validate_requirements(envelope_dialog)
    if not second or any(not r['id'].startswith('R-envelope-') or r['source_question_ids'] for r in second):
        raise ValueError('envelope dialog requirements must be nonempty R-envelope- obligations without questions')
    if any(r['id'].startswith('R-envelope-') for r in first):
        raise ValueError('original requirements already contain envelope obligations; derive a new set')
    merged = {'schema': 'agent-ee.requirements/1', 'requirements': first + second}
    raw = canonical(merged) + b'\n'
    validate_requirements(raw)
    return raw


def bind_fab_profile(item, spec, record, files, others):
    """A fab_profile check covers the profile obligation only for the exact referenced bytes.

    AGENTEE-75 itself refuses a fab_profile check mapped by several requirements.
    """
    params = spec.get('params') if isinstance(spec.get('params'), dict) else {}
    if files.get(params.get('fab_profile')) != record['fab_profile'].get('sha256'):
        raise ValueError(f"envelope obligation {item['id']} maps fab_profile check {spec['id']} whose "
                         'captured profile is not the referenced sha256')


def bind_connector_edge(item, spec, record, files, others):
    """A placement connector-edge/1 check (AGENTEE-92) over these exact envelope bytes.

    The check grades every fixed footprint of its envelope as one verdict, so it
    can witness this obligation only when the envelope fixes exactly this one
    footprint and no other requirement maps the check. Returns an unknown reason
    when the verdict cannot be attributed; raises on a mapping that cannot apply.
    """
    params = spec.get('params') if isinstance(spec.get('params'), dict) else {}
    if params.get('criterion') != 'connector-edge/1':
        raise ValueError(f"envelope obligation {item['id']} maps placement check {spec['id']} with criterion "
                         f"{params.get('criterion')!r}; only connector-edge/1 evaluates a fixed footprint")
    if files.get(params.get('envelope')) != record['envelope_sha256']:
        raise ValueError(f"envelope obligation {item['id']} maps placement check {spec['id']} whose "
                         'envelope is not these captured bytes')
    if others:
        raise ValueError(f"placement check {spec['id']} for envelope obligation {item['id']} is also mapped by "
                         f'{others}; one connector verdict cannot witness several requirements')
    if len(record['fixed_footprints']) != 1:
        return (f"connector-edge/1 grades all {len(record['fixed_footprints'])} fixed footprints as one "
                'verdict, so a defect cannot be attributed to this one')
    return None


def bind_native_envelope(item, spec, record, files, others):
    """One field, one criterion, these exact immutable envelope bytes."""
    from .envelope_check import CRITERIA, parameters
    params = spec.get('params')
    parameters(params)
    field = field_of(item)
    if CRITERIA[params['criterion']] != field:
        raise ValueError(f"envelope obligation {item['id']} needs its exact criterion, not {params['criterion']}")
    if files.get(params['envelope']) != record['envelope_sha256']:
        raise ValueError('native envelope check does not bind these captured envelope bytes')
    if others:
        raise ValueError(f"native envelope check {spec['id']} also mapped by {others}; one field per witness")
    if field == 'stackup':
        return 'copper_weight_oz needs qualified captured manufacturing authority; no inferred conversion'
    return None


# (field, check kind) -> validator. Coverage for another field needs the owning
# item to add a validator here; registering a kind or criterion is not enough.
BINDINGS = {('fab_profile', 'fab_profile'): bind_fab_profile,
            ('fixed_footprints', 'placement'): bind_connector_edge,
            **{(field, 'board_envelope'): bind_native_envelope
               for field in ('outline', 'layer_count', 'stackup')}}


def field_of(item):
    return item['source']['locator'].split('/')[1]


def obligation_coverage(scope, requirements, record, files):
    """Per-obligation coverage: bound, unmapped or unknown; vacuous maps are refused.

    An altered statement, source locator or intent, or a mapped check that cannot
    evaluate the obligation's exact field, raises. A missing or unmapped
    obligation is reported, so the caller refuses it rather than dropping it.
    """
    if not isinstance(scope, dict) or scope.get('schema') != 'agent-ee.scope/3':
        raise ValueError('envelope obligations require an agent-ee.scope/3 baseline')
    if not isinstance(requirements, dict) or requirements.get('schema') != 'agent-ee.envelope-requirements/1':
        raise ValueError('expected agent-ee.envelope-requirements/1')
    if record.get('envelope_sha256') != requirements['envelope_sha256']:
        raise ValueError('envelope validation and requirements describe different bytes')
    baseline = {r.get('id'): r for r in scope.get('baseline', {}).get('requirements', []) if isinstance(r, dict)}
    specs = {c.get('id'): c for c in scope.get('checks', []) if isinstance(c, dict)}
    derived = {r['id'] for r in requirements['requirements']}
    stale = sorted(i for i in baseline if isinstance(i, str) and i.startswith('R-envelope-') and i not in derived)
    if stale:
        raise ValueError(f'baseline envelope requirements not derived from the captured envelope: {stale}')
    mappers = {}
    for rid, requirement in baseline.items():
        for cid in requirement.get('checks', []) if isinstance(requirement.get('checks'), list) else []:
            mappers.setdefault(cid, set()).add(rid)
    report = []
    for item in requirements['requirements']:
        rid, mapped, field = item['id'], baseline.get(item['id']), field_of(item)
        kinds = item['check_kinds']
        entry = {'id': rid, 'locator': item['source']['locator'], 'check_kinds': kinds, 'checks': []}
        if mapped is None or not mapped.get('checks'):
            report.append({**entry, 'status': 'unmapped', 'reason': f'no mapped check; {UNBOUND[field]}'})
            continue
        if (mapped.get('statement') != item['statement'] or mapped.get('source') != item['source']
                or mapped.get('intent') != 'acceptance'):
            raise ValueError(f'envelope obligation {rid} statement, source or acceptance intent differs')
        unknown = []
        for cid in mapped['checks']:
            spec = specs.get(cid)
            kind = spec.get('kind') if spec else None
            if kind not in kinds:
                raise ValueError(f"envelope obligation {rid} maps check {cid} of kind {kind!r} outside kinds {kinds}")
            binding = BINDINGS.get((field, kind))
            reason = UNBOUND[field] if binding is None else binding(
                item, spec, record, files, sorted(mappers.get(cid, set()) - {rid}))
            if reason:
                unknown.append(f'{cid}: {reason}')
        entry['checks'] = list(mapped['checks'])
        if unknown:
            report.append({**entry, 'status': 'unknown', 'reason': '; '.join(unknown)})
        else:
            report.append({**entry, 'status': 'bound'})
    return report


def require_obligations(scope, requirements, record, files):
    """Refuse a scope/3 baseline that drops, alters, vacuously maps or leaves unknown an obligation."""
    report = obligation_coverage(scope, requirements, record, files)
    missing = [f"{r['id']} ({r['status']}: {r['reason']})" for r in report if r['status'] != 'bound']
    if missing:
        raise ValueError('envelope obligations lack executable coverage and are refused, not dropped: '
                         + '; '.join(missing))
    if not requirements['complete']:
        raise ValueError('envelope requirements are incomplete: unresolved ' +
                         ', '.join(u['predicate'] for u in requirements['unresolved']))
    return report


def json_object_start(path):
    """True when the first non-whitespace byte is '{'; reads no further than that."""
    with path.open('rb') as stream:
        while chunk := stream.read(4096):
            stripped = chunk.lstrip(b' \t\r\n')
            if stripped:
                return stripped.startswith(b'{')
    return False


# Envelope-derived documents a scope can consume, besides the envelope itself.
DERIVED = ('agent-ee.envelope-validation/1', 'agent-ee.envelope-requirements/1')


def consumed_paths(scope):
    """Paths the scope uses as authority: {path: [use, ...]} and the check-envelope paths.

    These are check `envelope` parameters, baseline requirement sources and
    targets, and packet requirement documents. Reference architectures and other
    captured inputs are informative: their content is never read for envelope
    authority, so a reference example is not promoted into a constraint.
    """
    uses, checked = {}, set()

    def use(rel, label):
        # Normalized like compile_baseline's paths; a malformed value is kept
        # only as a label, and its own validator refuses it later.
        try:
            rel = str(safe_relative(rel)) if isinstance(rel, str) else repr(rel)
        except ValueError:
            pass
        uses.setdefault(rel, []).append(label)
        return rel
    listed = lambda value: value if isinstance(value, list) else []
    for spec in listed(scope.get('checks')):
        params = spec.get('params') if isinstance(spec, dict) and isinstance(spec.get('params'), dict) else {}
        if 'envelope' in params:
            checked.add(use(params['envelope'], f"check {spec.get('id')} envelope"))
    baseline = scope.get('baseline') if isinstance(scope.get('baseline'), dict) else {}
    for req in listed(baseline.get('requirements')):
        source = req.get('source') if isinstance(req, dict) and isinstance(req.get('source'), dict) else {}
        if 'path' in source:
            use(source['path'], f"requirement {req.get('id')} source")
    for rel in listed(baseline.get('targets')):
        use(rel, 'baseline target')
    for rel in listed(scope.get('requirements')):
        use(rel, 'packet requirement')
    return uses, checked


def envelope_document(files, inputs, rel):
    """(kind, value) for a consumed captured file carrying envelope authority, else None.

    kind is 'envelope' (agent-ee.envelope/1), 'derived' (its validation or
    requirements record) or 'stage_one' (agent-ee.requirements/1 holding
    R-envelope- obligations). Only the scope's own consumed paths are read.
    """
    if inputs is None or not isinstance(rel, str) or rel not in files:
        return None
    from .routing_rules import captured_file
    path = captured_file(inputs, files, rel)
    try:
        if not json_object_start(path):
            return None
        value = json.loads(path.read_bytes().decode('utf-8'))
    except (UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get('schema') == SCHEMA:
        return 'envelope', value
    if value.get('schema') in DERIVED:
        return 'derived', value
    if value.get('schema') == 'agent-ee.requirements/1' and isinstance(value.get('requirements'), list) and any(
            isinstance(r, dict) and isinstance(r.get('id'), str) and r['id'].startswith('R-envelope-')
            for r in value['requirements']):
        return 'stage_one', value
    return None


def captured_bytes(files, inputs, rel, label):
    from .routing_rules import captured_file
    try:
        return captured_file(inputs, files, rel).read_bytes()
    except (ValueError, OSError) as exc:
        raise ValueError(f'envelope {label}: {exc}') from exc


def baseline_envelope(scope, files, inputs):
    """Capture-time envelope integration for compile_baseline; None without one.

    Authority comes from the declaration and the scope's consumed paths only; an
    uncited or reference-only envelope is never promoted. A consumed envelope,
    envelope record or stage-one obligation set without a declaration, or one
    that disagrees with it, is refused. Re-derives obligations and profile
    support from captured bytes, so no caller document or flag is trusted.
    Returns the immutable paths and the record that becomes part of the baseline
    identity.
    """
    declared = scope.get('envelope')
    ids = [r.get('id') for r in scope.get('baseline', {}).get('requirements', []) if isinstance(r, dict)]
    uses, checked = consumed_paths(scope)
    consumed = {}
    for rel in sorted(uses):
        if rel in checked:
            consumed[rel] = ('envelope', None, uses[rel])
        elif (document := envelope_document(files, inputs, rel)) is not None:
            consumed[rel] = (*document, uses[rel])
    if declared is None:
        named = sorted(i for i in ids if isinstance(i, str) and i.startswith('R-envelope-'))
        if consumed or named:
            raise ValueError('consumed envelope or envelope requirement without a scope envelope declaration: '
                             + '; '.join([f"{rel} ({kind}; {', '.join(uses)})"
                                          for rel, (kind, _, uses) in consumed.items()] + named))
        return None
    if scope.get('schema') != 'agent-ee.scope/3':
        raise ValueError('a scope envelope requires agent-ee.scope/3 executable coverage')
    if inputs is None:
        raise ValueError('envelope integration requires captured inputs')
    fields(declared, 'scope envelope', ['path', 'profile', 'dialog'])
    path = declared['path']
    if not isinstance(path, str):
        raise ValueError('scope envelope path must be a captured input path')
    raw = captured_bytes(files, inputs, path, 'path')
    other = sorted(rel for rel, (kind, _, _) in consumed.items() if kind == 'envelope' and rel != path)
    if other:
        raise ValueError(f'scope consumes envelopes other than the declared {path}: {other}')
    sha256 = hashlib.sha256(raw).hexdigest()
    stale = sorted(rel for rel, (kind, value, _) in consumed.items()
                   if kind == 'derived' and value.get('envelope_sha256') != sha256)
    if stale:
        raise ValueError(f'consumed envelope records describe bytes other than the declared envelope: {stale}')
    profile = None
    if declared['profile'] is not None:
        profile = captured_bytes(files, inputs, declared['profile'], 'profile')
    record = validate(raw, profile=profile, captured=(files, inputs))
    if record['fab_profile'].get('bytes') == 'not_supplied':
        raise ValueError('envelope references a fab profile; declare its captured path')
    requirements = derive_requirements(record, path)
    derived = {r['id'] for r in requirements['requirements']}
    generic = sorted(r['id'] for r in scope['baseline']['requirements']
                     if str(safe_relative(r['source']['path'])) == path and r['id'] not in derived)
    if generic:
        raise ValueError(f'requirements cite the declared envelope but are not its derived obligations: {generic}')
    dependencies = {path} | ({declared['profile']} if profile is not None else set())
    dependencies.update(record['fab_profile'].get('source_paths', []))
    from .design_dialog import validate_requirements
    expected = dialog_requirements(requirements)['requirements']
    stage_one = sorted(rel for rel, (kind, _, _) in consumed.items() if kind == 'stage_one')
    if declared['dialog'] is not None:
        dependencies.add(declared['dialog'])
        stage_one = sorted(set(stage_one) | {declared['dialog']})
    for rel in stage_one:
        captured = validate_requirements(captured_bytes(files, inputs, rel, 'stage-one requirements'))
        by_id = {r['id']: r for r in captured}
        extra = sorted(i for i in by_id if i.startswith('R-envelope-') and i not in derived)
        if extra or any(by_id.get(r['id']) != r for r in expected):
            raise ValueError(f'captured stage-one requirements {rel} do not carry exactly these envelope '
                             'obligations and envelope hash' + (f'; extra {extra}' if extra else ''))
    coverage = require_obligations(scope, requirements, record, files)
    return {'inputs': sorted(dependencies), 'record': {
        'schema': 'agent-ee.envelope-baseline/1', **declared,
        'validation': record, 'requirements': requirements, 'coverage': coverage}}


def atoms(node):
    return [x for x in node[1:] if isinstance(x, str)]


def edge_cuts_items(root):
    """Every saved item with Edge.Cuts in its own layer or layers list, at any depth.

    Yields (item, label path). KiCad joins footprint shapes on Edge.Cuts
    to the board outline, and a footprint field (PCB_FIELD) or text can be put
    there too, so nothing is skipped by tag.
    """
    stack = [(item, []) for item in reversed(root[1:]) if isinstance(item, list)]
    while stack:
        node, path = stack.pop()
        if not node or not isinstance(node[0], str):
            continue
        label = node[0]
        if label == 'footprint':
            reference = [atoms(p)[1] for p in children(node, 'property')
                         if atoms(p)[:1] == ['Reference'] and len(atoms(p)) > 1]
            label += f' {reference[0]}' if len(reference) == 1 else ''
        elif label == 'property' and atoms(node):
            label += f' {atoms(node)[0]}'
        here = path + [label]
        if any(isinstance(c, list) and c and c[0] in ('layer', 'layers') and 'Edge.Cuts' in atoms(c)
               for c in node[1:]):
            yield node, here
        stack.extend((c, here) for c in reversed(node[1:]) if isinstance(c, list) and c and c[0] not in
                     ('layer', 'layers'))


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
    for item, path in edge_cuts_items(root):
        # Only a board-level straight line or rectangle solely on Edge.Cuts is compared.
        supported = (len(path) == 1 and item[0] in ('gr_line', 'gr_rect') and not children(item, 'layers')
                     and len(children(item, 'layer')) == 1 and atoms(one(item, 'layer')) == ['Edge.Cuts'])
        if not supported:
            other.append(' '.join(path))
        elif item[0] == 'gr_line':
            start, end = (tuple(nanometres(v) for v in atoms(one(item, k))) for k in ('start', 'end'))
            segments.append((start, end))
        else:
            (x0, y0), (x1, y1) = (tuple(nanometres(v) for v in atoms(one(item, k))) for k in ('start', 'end'))
            segments += [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    footprints = {}
    for footprint in children(root, 'footprint'):
        reference = [atoms(p)[1] for p in children(footprint, 'property') if atoms(p)[:1] == ['Reference']]
        at, layer = one(footprint, 'at'), one(footprint, 'layer')
        if len(reference) != 1 or not at or not layer:
            raise ValueError('footprint without one Reference, at and layer')
        values = atoms(at)
        # Duplicate references stay listed; a fixed footprint needs exactly one match.
        footprints.setdefault(reference[0], []).append({
            'layer': atoms(layer)[0], 'at': (nanometres(values[0]), nanometres(values[1])),
            'rotation_deg': degrees(values[2] if len(values) > 2 else '0', f'footprint {reference[0]} rotation')})
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
            'outcome': ('mismatch' if any(r['status'] == 'mismatch' for r in results) else
                        'unknown' if any(r['status'] != 'match' for r in results) else 'match')}


def write_artifacts(artifacts, output):
    """Write only into a new directory; existing outputs stay intact."""
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    for filename, data in artifacts.items():
        (output / filename).write_bytes(data)


def source_inventory(root):
    """Hash regular files under root now, as capture would; symlinks are never followed."""
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f'profile source directory not found: {root}')
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob('*'))
            if p.is_file() and not p.is_symlink()}, root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('envelope', type=Path)
    parser.add_argument('--source-path', required=True, help='capture-relative path cited by derived requirements')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--profile', type=Path, help='captured fab profile bytes referenced by sha256')
    parser.add_argument('--sources', type=Path,
                        help='directory the profile\'s cited source paths resolve in; without it no limit is ready')
    parser.add_argument('--original-requirements', type=Path,
                        help='agent-ee.requirements/1 to extend into stage-one-requirements.json')
    parser.add_argument('--board', type=Path, help='saved .kicad_pcb to compare field by field')
    args = parser.parse_args()
    try:
        if args.sources and not args.profile:
            raise ValueError('--sources needs --profile')
        raw = args.envelope.read_bytes()
        artifacts = derive(raw, source_path=args.source_path,
                           profile=args.profile.read_bytes() if args.profile else None,
                           captured=source_inventory(args.sources) if args.sources else None)
        if args.original_requirements:
            artifacts['stage-one-requirements.json'] = stage_one_requirements(
                args.original_requirements.read_bytes(), artifacts['dialog-requirements.json'])
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

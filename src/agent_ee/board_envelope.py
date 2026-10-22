"""Offline board envelope records (agent-ee.envelope/1 and /2) and obligations.

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
SCHEMAS = (SCHEMA, 'agent-ee.envelope/2')
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
# registered but has no envelope binding yet; fab_profile max_layers bounds only a maximum, so it
# cannot establish an exact layer count.
CHECK_KINDS = {'outline': ['placement', 'board_envelope'],
               'layer_count': ['fab_package', 'board_envelope'],
               'stackup': ['fab_package', 'board_envelope'], 'fab_profile': ['fab_profile'],
               'mounting_holes': ['placement', 'board_envelope'], 'keepouts': ['placement', 'board_envelope'],
               'fixed_footprints': ['placement'], 'obligations': ['board_envelope'],
               'max_component_height_mm': ['board_envelope']}
# Why a field without a binding has unknown coverage; reported, never assumed.
UNBOUND = {
    'max_component_height_mm': 'needs a native component-height/1 check with sourced per-footprint heights',
    'obligations': 'generator mechanics require qualified checks from AGENTEE-177/179',
    'outline': 'needs an exact native board_envelope outline/1 check',
    'layer_count': 'needs an exact native board_envelope layers/1 check',
    'stackup': 'needs native stackup facts and qualified captured copper_weight_oz authority',
    'fab_profile': 'needs one fab_profile check over the referenced profile bytes',
    'mounting_holes': 'needs an id-selected native mounting-holes/1 check',
    'keepouts': 'needs an id-selected native keepouts/1 check',
    'fixed_footprints': 'needs one placement connector-edge/1 check over these envelope bytes '
                        'selecting this fixed reference'}
# A generic /obligations entry is never bound by a check. Its optional coverage
# record stages it: candidate generation is admitted when every witness, a
# concrete envelope field whose own obligation is bound (or a containment check
# over these bytes), is present; its open items stay reported, and refuse final
# physical qualification, until AGENTEE-177/179 evidence closes them. The record
# carries locators and text only, so it cannot introduce a numeric limit.
WITNESS = re.compile(r'/(mounting_holes|keepouts|fixed_footprints)/(0|[1-9][0-9]*)\Z')
CONTAINMENT, PART_IDENTITY = 'outline-containment/1', 'part-identity/1'
WITNESS_CRITERIA = (CONTAINMENT, PART_IDENTITY)
QUALIFICATION_STAGES = ('candidate_assessment', 'physical_qualification')
# Reviewed, source-bound minimum contracts for staging generic obligations (AGENTEE-197
# review H1/H2). Staging is admitted only for an obligation whose id, requirement IDs,
# owner items and statement equal a contract entry verbatim; the contract, never the
# caller's coverage list, fixes the generation witnesses and the open qualification items
# that must stay listed (coverage may add, never drop). It carries roles, references and
# identities only: no numeric limit. A changed source statement refuses staging until a
# reviewed commit updates the entry. `envelope_ids` names source envelopes that must carry
# every contracted obligation even before staging.
PHYSICAL, CANDIDATE = QUALIFICATION_STAGES[1], QUALIFICATION_STAGES[0]
OBLIGATION_CONTRACTS = {
    # fome-mc telemetry-pod production capture composition/envelope.json (envelope id
    # expanded-pod-microsd-production, sha256 96615a7932b83959cd3898cee1febe1a4737c7ebd25fdc4fde40a4951e471e84),
    # obligations as staged by the agent-509 decision of 2026-10-22; J1 identity from its
    # composition parts.json connector/J1 (D-PCB-10: right-angle 35-position AMPSEAL 1-776163-1).
    'telemetry-pod-expanded/1': {
        'envelope_ids': ('expanded-pod-microsd-production',),
        'obligations': {
            'corner-holes': {
                'requirement_ids': ['POD-ME-1', 'POD-AC-7'], 'owner_items': ['AGENTEE-177', 'AGENTEE-179'],
                'statement': 'Provide four corner mounting holes; generator selects drill, plating, inset and '
                             'coordinates.',
                # The reviewed source witnesses H1-H4, one per outline corner; another hole role
                # needs a reviewed contract change. Geometry stays generator-selected.
                'corner_holes': ('H1', 'H2', 'H3', 'H4'),
                'open': {'hole-authority': PHYSICAL, 'fabricated-holes': PHYSICAL}},
            'height': {
                'requirement_ids': ['POD-ME-1'], 'owner_items': ['AGENTEE-177', 'AGENTEE-179'],
                'statement': 'No 12 mm height limit is adopted. Actual cowl, underside, connector, mating-plug and '
                             'harness clearances require enclosure validation for the expanded pod.',
                'open': {'candidate-heights': CANDIDATE, 'cowl-fit': PHYSICAL, 'underside-fit': PHYSICAL,
                         'connector-fit': PHYSICAL, 'mating-plug-fit': PHYSICAL, 'harness-fit': PHYSICAL}},
            'harness-edge': {
                'requirement_ids': ['POD-CD-HARNESS-CONNECTOR', 'POD-ME-2'],
                'owner_items': ['AGENTEE-177', 'AGENTEE-179'],
                'statement': 'Place selected TE 1-776163-1 35-position harness connector at the board edge using '
                             'its captured K1 drawing and exact footprint. Panel cutout, mating plug, retention, '
                             'board thickness tolerance and harness clearance remain unqualified.',
                'fixed': {'reference': 'J1', 'identity': {
                    'manufacturer': 'TE Connectivity', 'mpn': '1-776163-1',
                    'footprint': 'PodConnector:AMPSEAL_35_1-776163-1'}},
                'open': {'panel-cutout': PHYSICAL, 'mating-plug': PHYSICAL, 'retention': PHYSICAL,
                         'thickness-tolerance': PHYSICAL, 'harness-clearance': PHYSICAL}},
            'antenna': {
                'requirement_ids': ['POD-CD-MCU-AND-RADIO', 'POD-AC-7'],
                'owner_items': ['AGENTEE-177', 'AGENTEE-179'],
                'statement': 'ESP32-S3-WROOM-1 antenna overhangs the top edge (D-PCB-16, owner 2026-10-21: '
                             'outside-board placement case, Espressif hardware design guidelines Section 1.4.7, '
                             'page 33). The module-owned keepout rule area is retained from the captured '
                             'footprint; datasheet Figure 11-1 module dimensions define no base-board clearance '
                             '(AGENTEE-211). Feed-point side, housing clearance and RF performance remain '
                             'unqualified.',
                'fixed': {'reference': 'U1'}, 'antenna_keepout': 'U1',
                'open': {'feed-point-side': PHYSICAL, 'housing-pocket': PHYSICAL, 'nearby-metal': PHYSICAL,
                         'support': PHYSICAL, 'rf-performance': PHYSICAL}},
            'imu-separation': {
                'requirement_ids': ['POD-ME-4'], 'owner_items': ['AGENTEE-177', 'AGENTEE-179'],
                'statement': 'Keep tall or heavy parts, including harness connector, SMA and electrolytic '
                             'capacitors, away from the IMU.',
                'open': {'placement-assessment': CANDIDATE, 'vibration-coupling': PHYSICAL}}}}}
CONTRACT_FIELDS = ('requirement_ids', 'owner_items', 'statement')


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


def validate_stackup(value, count, *, schema=SCHEMA):
    if isinstance(count, bool) or not isinstance(count, int) or not 2 <= count <= MAX_COPPER:
        raise ValueError(f'layer_count must be an integer from 2 to {MAX_COPPER}')
    sourced = schema == SCHEMAS[1]
    fields(value, 'stackup', ['nominal_thickness_mm', 'physical_thickness_mm', 'layers']
           if sourced else ['thickness_mm', 'layers'])
    total = length(value['physical_thickness_mm' if sourced else 'thickness_mm'],
                   'stackup physical thickness_mm' if sourced else 'stackup thickness_mm', positive=True)
    nominal = length(value['nominal_thickness_mm'], 'nominal_thickness_mm', positive=True) if sourced else total
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
            fields(layer, f'stackup layer {index}', ['name', 'kind', 'thickness_mm', 'copper_weight_oz']
                   + (['source'] if sourced else []), ['role'])
            if 'role' in layer and layer['role'] not in ('plane', 'signal'):
                raise ValueError('copper layer role must be plane or signal')
            weight = scaled(decimal(layer['copper_weight_oz'], 'copper_weight_oz'), 'copper_weight_oz')
            if weight <= 0:
                raise ValueError('copper_weight_oz must be positive')
            copper.append(layer['name'])
        else:
            fields(layer, f'stackup layer {index}', ['name', 'kind', 'thickness_mm']
                   + (['source'] if sourced else []))
            weight = None
        label = layer['name']
        if not isinstance(label, str) or not label.strip() or len(label) > 64 or label in names:
            raise ValueError(f'invalid or duplicate stackup layer name: {label!r}')
        names.add(label)
        thickness = length(layer['thickness_mm'], f'stackup layer {label} thickness_mm', positive=True)
        summed += thickness
        records.append({'name': label, 'kind': layer['kind'], 'thickness_mm': mm(thickness),
                        **({'role': layer['role']} if 'role' in layer else {}),
                        **({'copper_weight_oz': fixed_point(weight, MAX_PLACES)} if weight is not None else {})})
    if len(layers) % 2 == 0:
        raise ValueError('stackup must alternate copper and dielectric, starting and ending with copper')
    if copper != copper_names(count):
        raise ValueError(f'layer_count {count} requires copper layers {copper_names(count)}, got {copper}')
    if summed != total:
        raise ValueError(f'stackup layers sum to {mm(summed)} mm, not thickness_mm {mm(total)}')
    return {'thickness_mm': mm(nominal), 'layers': records,
            **({'nominal_thickness_mm': mm(nominal), 'physical_thickness_mm': mm(total),
                'sources': [layer['source'] for layer in layers]} if sourced else {})}


def stackup_support(stackup, fab, captured):
    """Verify physical layer citations and the compiler's finished-thickness bound."""
    from .fab_profile import verify_source
    unresolved, paths = [], set()
    files, inputs = captured if captured is not None else ({}, Path(os.devnull))
    for index, source in enumerate(stackup['sources']):
        try:
            paths.add(verify_source(source, inputs, files, fab.get('as_of')))
        except (ValueError, KeyError, OSError, UnicodeError) as exc:
            unresolved.append({'predicate': f'stackup_layer_source_{index}', 'reason': str(exc)})
    tolerance = fab.get('finished_thickness_tolerance', {})
    if tolerance.get('execution') != 'ready':
        unresolved.append({'predicate': 'finished_thickness_tolerance',
                           'reason': tolerance.get('reason', 'no source-verified finished-thickness tolerance')})
    else:
        percent = Fraction(str(tolerance['expected']['percent']))
        nominal, physical = (Fraction(stackup[k]) for k in ('nominal_thickness_mm', 'physical_thickness_mm'))
        if nominal < Fraction(str(tolerance['expected']['min_nominal_thickness_mm'])):
            raise ValueError('nominal thickness is below captured tolerance applicability')
        if abs(physical - nominal) * 100 > nominal * percent:
            raise ValueError('physical stackup is outside captured finished-thickness tolerance of nominal')
        stackup['finished_thickness_tolerance'] = tolerance['expected']
    stackup['source_paths'] = sorted(paths)
    return unresolved


def validate_coverage(item, envelope):
    """A staged obligation's witnesses and open qualification items; never a pass claim."""
    label = f"obligation {item['id']} coverage"
    coverage = fields(item['coverage'], label, ['generation', 'open'])
    witnesses, seen = coverage['generation'], set()
    if not isinstance(witnesses, list) or len(witnesses) > MAX_ITEMS:
        raise ValueError(f'{label} generation must be a list of witnesses')
    for witness in witnesses:
        fields(witness, f'{label} witness', ['locator'], ['criterion'])
        match = WITNESS.fullmatch(witness['locator']) if isinstance(witness['locator'], str) else None
        if match is None:
            raise ValueError(f"{label} witness {witness['locator']!r} must point at a mounting hole, keepout "
                             'or fixed footprint; other fields carry no generation witness')
        field, index = match[1], int(match[2])
        if index >= len(envelope[field]):
            raise ValueError(f"{label} witness {witness['locator']} is not declared in this envelope")
        criterion = witness.get('criterion')
        if criterion is not None and (criterion not in WITNESS_CRITERIA or field != 'fixed_footprints' or (
                criterion == CONTAINMENT and 'courtyard_overhangs' not in envelope[field][index])):
            raise ValueError(f"{label} witness criterion {criterion!r} needs a fixed footprint (with declared "
                             f'courtyard_overhangs for {CONTAINMENT}) and one of {list(WITNESS_CRITERIA)}')
        if (witness['locator'], criterion) in seen:
            raise ValueError(f"{label} repeats witness {witness['locator']}")
        seen.add((witness['locator'], criterion))
    opened = coverage['open']
    # Nothing is qualified at generation: an obligation without open items is a pass claim.
    if not isinstance(opened, list) or not 1 <= len(opened) <= MAX_ITEMS:
        raise ValueError(f'{label} open must list the qualification evidence still required; '
                         'final qualification is never claimed by the envelope')
    unique_names(opened, 'id', f'{label} open item', set())
    for entry in opened:
        fields(entry, f'{label} open item', ['id', 'stage', 'statement'])
        if entry['stage'] not in QUALIFICATION_STAGES:
            raise ValueError(f'{label} open item stage must be one of {list(QUALIFICATION_STAGES)}')
        if not isinstance(entry['statement'], str) or not entry['statement'].strip():
            raise ValueError(f'{label} open item statement required')
    return coverage


def corner_holes(envelope, vertices):
    """Declared hole locators, each with the outline vertex it is uniquely nearest (None on a tie)."""
    out = []
    for index, hole in enumerate(envelope['mounting_holes']):
        at = point(hole['at'], f"mounting hole {hole['id']} at")
        reach = sorted(((at[0] - v[0]) ** 2 + (at[1] - v[1]) ** 2, i) for i, v in enumerate(vertices))
        out.append((f'/mounting_holes/{index}', reach[0][1] if reach[0][0] != reach[1][0] else None))
    return out


def contract_witnesses(oid, expected, envelope, vertices):
    """(locator, criterion) witnesses a contract entry requires, resolved against this envelope."""
    label = f'obligation {oid} source contract'
    required = []
    if 'corner_holes' in expected:
        holes = dict(zip((hole['id'] for hole in envelope['mounting_holes']), corner_holes(envelope, vertices)))
        bound = expected['corner_holes']
        missing, extra = [h for h in bound if h not in holes], sorted(h for h in holes if h not in bound)
        if missing or extra:
            raise ValueError(f'{label} binds exactly the source mounting-hole witnesses {list(bound)}'
                             + (f'; missing {missing}' if missing else '') + (f'; undeclared {extra}' if extra else '')
                             + '; another hole role needs a reviewed contract change')
        corners = [holes[h][1] for h in bound]
        if None in corners or len(set(corners)) != len(bound):
            raise ValueError(f'{label} requires source witnesses {list(bound)} each uniquely nearest a distinct '
                             f'outline corner; nearest corners are {dict(zip(bound, corners))}')
        # Each source witness by its own locator, independent of declaration order.
        required += [(holes[h][0], None) for h in bound]
    if 'fixed' in expected:
        reference = expected['fixed']['reference']
        index = next((i for i, f in enumerate(envelope['fixed_footprints']) if f['reference'] == reference), None)
        if index is None:
            raise ValueError(f'{label} requires fixed footprint {reference}')
        locator = f'/fixed_footprints/{index}'
        required += [(locator, None), (locator, CONTAINMENT)]
        if 'identity' in expected['fixed']:
            required.append((locator, PART_IDENTITY))
    if 'antenna_keepout' in expected:
        keepouts = [f'/keepouts/{i}' for i, k in enumerate(envelope['keepouts'])
                    if isinstance(k, dict) and isinstance(k.get('antenna'), dict) and k['antenna'].get('reference') == expected['antenna_keepout']]
        if not keepouts:
            raise ValueError(f"{label} requires the captured antenna keepout of {expected['antenna_keepout']}")
        required += [(locator, None) for locator in keepouts]
    return required


def obligation_contract(envelope, obligations, vertices):
    """The reviewed source contract staging these obligations, or None when nothing is staged.

    Refuses staging outside a contract, a dropped or altered contracted obligation,
    and coverage that omits a contracted witness or open qualification item.
    """
    staged = [item for item in obligations if 'coverage' in item]
    claimed = sorted(cid for cid, c in OBLIGATION_CONTRACTS.items() if envelope['id'] in c['envelope_ids']
                     or any(item['id'] in c['obligations'] for item in staged))
    if not claimed:
        if staged:
            raise ValueError('staged obligations match no reviewed source contract: '
                             f"{[item['id'] for item in staged]}; only contracted obligations may be staged")
        return None
    if len(claimed) > 1:
        raise ValueError(f'obligations claim more than one source contract: {claimed}')
    cid = claimed[0]
    contract = OBLIGATION_CONTRACTS[cid]['obligations']
    by_id = {item['id']: item for item in obligations}
    for oid, expected in contract.items():
        if oid not in by_id:
            raise ValueError(f'source contract {cid} obligation {oid} is missing; it cannot be dropped')
        differs = [f for f in CONTRACT_FIELDS if by_id[oid][f] != expected[f]]
        if differs:
            raise ValueError(f'obligation {oid} differs from reviewed source contract {cid} in {differs}; a '
                             'changed source needs a reviewed contract update')
    outside = sorted(item['id'] for item in staged if item['id'] not in contract)
    if outside:
        raise ValueError(f'staged obligations outside source contract {cid}: {outside}')
    required = {}
    for item in staged:
        oid, expected, coverage = item['id'], contract[item['id']], item['coverage']
        opened = {e['id']: e['stage'] for e in coverage['open']}
        dropped = sorted(k for k in expected['open'] if k not in opened)
        restaged = sorted(k for k, stage in expected['open'].items() if k in opened and opened[k] != stage)
        if dropped or restaged:
            raise ValueError(f'obligation {oid} coverage must keep source contract {cid} open qualification items'
                             + (f'; dropped {dropped}' if dropped else '') + (f'; restaged {restaged}' if restaged else ''))
        witnesses = contract_witnesses(oid, expected, envelope, vertices)
        listed = {(w['locator'], w.get('criterion')) for w in coverage['generation']}
        missing = [locator + (f' ({criterion})' if criterion else '') for locator, criterion in witnesses
                   if (locator, criterion) not in listed]
        if missing:
            raise ValueError(f'obligation {oid} coverage omits source contract {cid} generation witnesses: '
                             + ', '.join(missing))
        required[oid] = [[locator, criterion] for locator, criterion in witnesses]
    return {'id': cid, 'required': required}


def validate_obligations(items, envelope):
    """Unbound generator decisions are obligations, never caller-asserted passes."""
    namespace = set()
    unique_names(items, 'id', 'obligation', namespace)
    for item in items:
        fields(item, 'obligation', ['id', 'requirement_ids', 'statement', 'owner_items'], ['coverage'])
        if not isinstance(item['statement'], str) or not item['statement'].strip():
            raise ValueError('obligation statement required')
        for field in ('requirement_ids', 'owner_items'):
            values = item[field]
            if (not isinstance(values, list) or not 1 <= len(values) <= MAX_ITEMS or
                    any(not isinstance(v, str) or not ENVELOPE_ID.fullmatch(v) for v in values) or
                    len(set(values)) != len(values)):
                raise ValueError(f'obligation {field} needs unique nonempty identifiers')
        if 'coverage' in item:
            validate_coverage(item, envelope)
    return items


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
    if 'stackup' in compiled:
        record['stackup'] = compiled['stackup']
    if 'finished_thickness_tolerance' in compiled:
        record['finished_thickness_tolerance'] = compiled['finished_thickness_tolerance']
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
        fields(hole, f"mounting hole {hole['id']}", ['id', 'at', 'drill_mm', 'plated'], ['tolerance_mm'])
        at = point(hole['at'], f"mounting hole {hole['id']} at")
        drill = length(hole['drill_mm'], f"mounting hole {hole['id']} drill_mm", positive=True)
        if not isinstance(hole['plated'], bool):
            raise ValueError('plated must be boolean')
        n = len(vertices)
        if not strictly_inside(at, vertices) or any(
                4 * distance2(at, vertices[i], vertices[(i + 1) % n]) < drill * drill for i in range(n)):
            raise ValueError(f"mounting hole {hole['id']} is not inside the outline")
        tolerance = length(hole.get('tolerance_mm', 0), 'hole tolerance_mm')
        records.append({'id': hole['id'], 'at_mm': mm_point(at), 'drill_mm': mm(drill),
                        'plated': hole['plated'], 'tolerance_mm': mm(tolerance)})
    return records


def validate_keepouts(keepouts, vertices, copper):
    records = []
    for keepout in keepouts:
        kid = keepout['id']
        fields(keepout, f'keepout {kid}', ['id', 'layers', 'forbid'], ['polygon', 'antenna'])
        if ('polygon' in keepout) == ('antenna' in keepout):
            raise ValueError(f'keepout {kid} needs exactly one of polygon or antenna')
        geometry = {}
        if 'antenna' in keepout:
            antenna = fields(keepout['antenna'], 'antenna', ['reference', 'facts_check'])
            for key in antenna:
                name(antenna[key], 'antenna ' + key)
            geometry['antenna'] = dict(antenna)
        else:
            polygon = keepout['polygon']
            if not isinstance(polygon, list) or not 3 <= len(polygon) <= MAX_VERTICES:
                raise ValueError(f'keepout {kid} polygon needs 3 to {MAX_VERTICES} vertices')
            points = [point(p, f'keepout {kid} vertex') for p in polygon]
            simple_polygon(points, f'keepout {kid}')
            if not all(segment_inside(points[i], points[(i + 1) % len(points)], vertices) for i in range(len(points))):
                raise ValueError(f'keepout {kid} is not contained in the outline')
            geometry['polygon_mm'] = [mm_point(p) for p in points]
        layers, forbid = keepout['layers'], keepout['forbid']
        if (not isinstance(layers, list) or not layers or len(set(map(str, layers))) != len(layers)
                or any(layer not in copper for layer in layers)):
            raise ValueError(f'keepout {kid} layers must be unique stackup copper layer names')
        if (not isinstance(forbid, list) or not forbid or len(set(map(str, forbid))) != len(forbid)
                or any(item not in FORBID for item in forbid)):
            raise ValueError(f'keepout {kid} forbid must be a unique nonempty subset of {list(FORBID)}')
        records.append({'id': kid, **geometry,
                        'layers': [c for c in copper if c in layers],
                        'forbid': [f for f in FORBID if f in forbid]})
    return records


def validate_fixed(footprints, vertices, edges):
    records = []
    for footprint in footprints:
        ref = footprint['reference']
        fields(footprint, f'fixed footprint {ref}', ['reference', 'side', 'edge', 'position_mm', 'inset_mm',
                                                     'rotation_deg', 'insertion', 'tolerance_mm'],
               ['courtyard_overhangs'])
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
        record = {'reference': ref, 'side': footprint['side'], 'layer': SIDES[footprint['side']],
                  'edge': edge['name'], 'position_mm': mm(position), 'inset_mm': mm(inset),
                  'anchor_mm': mm_point(anchor),
                  'rotation_deg': degrees(rotation, f'fixed footprint {ref} rotation_deg'),
                  'insertion': insertion,
                  'mating_axis': edge['outward'] if insertion == 'through_edge' else
                  ('z-' if insertion == 'from_top' else 'z+'),
                  'tolerance_mm': mm(tolerance)}
        if 'courtyard_overhangs' in footprint:
            record['courtyard_overhangs'] = courtyard_overhangs(footprint['courtyard_overhangs'], ref, edges)
        records.append(record)
    return records


def courtyard_overhangs(value, ref, edges):
    """Declared allowances for one fixed courtyard to leave the outline (AGENTEE-212).

    A nonempty list of distinct axis-aligned outline edges, each with a positive
    maximum distance beyond it. An allowance is never implied by a fixed
    reference, a name or a footprint id."""
    label = f'fixed footprint {ref} courtyard_overhangs'
    if not isinstance(value, list) or not value:
        raise ValueError(f'{label} must be a nonempty list')
    records, seen = [], set()
    for item in value:
        fields(item, label + ' item', ['edge', 'max_mm'])
        if not isinstance(item['edge'], str) or item['edge'] not in edges or item['edge'] in seen:
            raise ValueError(f"{label} edge {item['edge']!r} is not a distinct outline edge name")
        seen.add(item['edge'])
        edge = edges[item['edge']]
        if not edge['axis_aligned']:
            raise ValueError(f"{label} edge {edge['name']} is oblique")
        distance = length(item['max_mm'], f"{label} {edge['name']} max_mm", positive=True)
        records.append({'edge': edge['name'], 'max_mm': mm(distance)})
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


# Constraint provenance (AGENTEE-266). Every constrained field may name who fixed it:
# the owner (quoted, with a hashed source), a derivation from owner-stated facts, or an
# agent-proposed assumption. Assumed and owner-weakened limits are soft: reported for
# owner confirmation, never a hard ceiling. A field without provenance keeps today's
# enforcement and is reported as unknown provenance, never as owner-stated.
PROVENANCE_CLASSES = ('owner_stated', 'derived', 'assumed')
# Wording that says the owner did not fix the limit, or fixed a minimum rather than a
# maximum; it never compiles into a hard limit (AGENTEE-266, widened by AGENTEE-271).
WEAKENING = re.compile(
    r"\b(?:not (?:tight|fixed|final|firm|critical|strict|set in stone|a hard)|isn'?t (?:tight|fixed|final|critical)|"
    r"soft limit|(?<!no )room (?:in|for|to)|there(?:'s| is| are) (?:plenty of |lots of |enough )?room|"
    r"(?:plenty|lots|loads|enough|ample) of (?:room|space)|roomy|spacious|"
    r"ideally|prefer(?:ably|red|ence)?|if possible|flexib\w*|negotiable|or so|"
    r"targets?|targeted|targeting|aim(?:ing)? (?:for|at)|goal|preliminar\w*|tentative\w*|provisional\w*|"
    r"placeholder|tbd|tbc|to be (?:confirmed|determined|decided|agreed)|draft|initial(?:ly)?|"
    r"for now|currently|at the moment|subject to change|(?:can|could|may|might) change|"
    r"estimat\w*|guess\w*|ballpark|rough(?:ly)?|approximate(?:ly)?|maybe|perhaps|probably|could|might|"
    r"something like|more or less|give or take|nice to have|would like|up to you|your call|"
    r"doesn'?t (?:matter|have to)|at least|minimum|or (?:larger|bigger|more|wider|taller))\b|"
    r"\d\s*-?ish\b|(?:\b(?:about|around|approx\.?|circa)|[~\u2248])\s*\d", re.IGNORECASE)
# A number states a limit only in its dimensional context: an outline as one W x H mm
# (or W mm square) pair, a layer count next to "layer", a height in mm beside a height
# word. A matching number elsewhere in the quote (a part count, another dimension) is stray.
_N = r'(?<![\d.])(\d+(?:\.\d+)?)'
OUTLINE_PAIR = re.compile(_N + r'\s*(?:mm\s*)?(?:x|\u00d7|by)\s*' + _N + r'\s*mm\b', re.IGNORECASE)
OUTLINE_SQUARE = re.compile(_N + r'\s*mm\s+square\b', re.IGNORECASE)
LAYERS = re.compile(r'(?<![\d.])(\d+)\s*-?\s*(?:copper\s+)?layers?\b', re.IGNORECASE)
MILLIMETRES = re.compile(_N + r'\s*mm\b', re.IGNORECASE)
HEIGHT_WORD = re.compile(r'\b(?:tall|height|high)\b', re.IGNORECASE)
MAX_TEXT = 4096


def provenance_locators(envelope):
    """JSON pointers of the constrained fields; the same locators the obligations cite."""
    out = ['/outline', '/layer_count', '/stackup', '/fab_profile']
    if 'max_component_height_mm' in envelope:
        out.append('/max_component_height_mm')
    for field in ('mounting_holes', 'keepouts', 'fixed_footprints', 'obligations'):
        out += [f'/{field}/{i}' for i in range(len(envelope.get(field) or []))]
    return out


def stated_numbers(envelope, vertices):
    """Numbers a quote must state for the owner to have fixed this limit, in mm or a count."""
    xs, ys = [v[0] for v in vertices], [v[1] for v in vertices]
    numbers = {'/outline': [Decimal(mm(max(xs) - min(xs))), Decimal(mm(max(ys) - min(ys)))],
               '/layer_count': [Decimal(envelope['layer_count'])]}
    if 'max_component_height_mm' in envelope:
        numbers['/max_component_height_mm'] = [Decimal(str(envelope['max_component_height_mm']))]
    return numbers


def unstated(locator, text, numbers, unique=True):
    """Why text does not state this field's numbers in their dimensional context, else None.

    An owner quote must state exactly one candidate value (unique); a derivation states its
    inputs as well as its result, so only the result's presence is required there."""
    if locator not in numbers:
        return None
    wanted = numbers[locator]
    if locator == '/outline':
        pairs = {tuple(sorted(Decimal(n) for n in m)) for m in OUTLINE_PAIR.findall(text)}
        pairs |= {(Decimal(n), Decimal(n)) for n in OUTLINE_SQUARE.findall(text)}
        stated, label = pairs, f'{wanted[0]} x {wanted[1]} mm outline'
        wanted = tuple(sorted(wanted))
    elif locator == '/layer_count':
        stated, label, wanted = {Decimal(n) for n in LAYERS.findall(text)}, f'{wanted[0]} layers', wanted[0]
    else:
        stated = {Decimal(n) for n in MILLIMETRES.findall(text)} if HEIGHT_WORD.search(text) else set()
        label, wanted = f'a {wanted[0]} mm height', wanted[0]
    if wanted not in stated:
        return f'it does not state the {label}'
    if unique and len(stated) > 1:
        return f'it states several candidate values for the {label}, so the limit is ambiguous'
    return None


def prose(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise ValueError(f'{label} must be nonempty text of at most {MAX_TEXT} characters')
    return value


def owner_quote(value, label, captured):
    """{quote, source: {path, sha256}}, checked against the captured source when supplied.

    The quote must appear in the source text (whitespace runs compared as one space).
    A captured source whose bytes or text disagree is refused; an uncaptured one stays
    unverified, never assumed verified."""
    fields(value, label, ['quote', 'source'])
    quote = prose(value['quote'], f'{label} quote')
    fields(value['source'], f'{label} source', ['path', 'sha256'])
    path = str(safe_relative(value['source']['path']))
    if path != value['source']['path']:
        raise ValueError(f'{label} source path must be canonical candidate-relative')
    sha256 = value['source']['sha256']
    if not isinstance(sha256, str) or not SHA256.fullmatch(sha256):
        raise ValueError(f'{label} source sha256 must be 64 lowercase hex digits')
    out = {'quote': quote, 'source': {'path': path, 'sha256': sha256}, 'verification': 'not_captured'}
    if captured is None:
        return out
    files, inputs = captured
    if path not in files:
        # A capture inventory is present, so a quote it cannot verify is refused (AGENTEE-271).
        raise ValueError(f'{label} source {path} is not in the captured inventory; an owner quote must cite '
                         'a captured source')
    data = captured_bytes(files, inputs, path, f'{label} source')
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError(f'{label} source {path} is not the cited sha256')
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise ValueError(f'{label} source {path} is not UTF-8 text') from exc
    if ' '.join(quote.split()) not in ' '.join(text.split()):
        raise ValueError(f'{label} quote does not appear in {path}')
    out['verification'] = 'quote_in_source'
    return out


def weakened(quotes):
    for quote in quotes:
        found = WEAKENING.search(quote)
        if found:
            return f'the quote weakens the limit ({found.group(0).strip()!r})'
    return None


def validate_provenance(envelope, vertices, captured):
    """Per-constraint provenance class and enforcement; see docs/board-envelope.md."""
    declared = envelope.get('constraint_provenance', {})
    if not isinstance(declared, dict) or ('constraint_provenance' in envelope and not declared):
        raise ValueError('constraint_provenance must be a nonempty object keyed by field pointer')
    locators = provenance_locators(envelope)
    unknown = sorted(set(declared) - set(locators))
    if unknown:
        raise ValueError(f'constraint_provenance names no constrained field: {unknown}')
    numbers = stated_numbers(envelope, vertices)
    entries, done = {}, {}

    def resolve(locator, trail=()):
        if locator in done:
            return done[locator]
        if locator in trail:
            raise ValueError(f"constraint_provenance derivation cycle: {' -> '.join(trail + (locator,))}")
        item, label = declared[locator], f'constraint_provenance {locator}'
        if not isinstance(item, dict) or item.get('class') not in PROVENANCE_CLASSES:
            raise ValueError(f'{label} class must be one of {PROVENANCE_CLASSES}')
        kind = item['class']
        entry = {'locator': locator, 'class': kind}
        if kind == 'owner_stated':
            fields(item, label, ['class', 'quote', 'source'])
            entry.update(owner_quote({k: item[k] for k in ('quote', 'source')}, label, captured))
            reason = weakened([entry['quote']])
            missing = unstated(locator, entry['quote'], numbers)
            if reason is None and missing:
                reason = f'the quote does not fix the limit: {missing}'
        elif kind == 'derived':
            fields(item, label, ['class', 'derivation'], ['from', 'facts'])
            entry['derivation'] = prose(item['derivation'], f'{label} derivation')
            cited, facts = item.get('from', []), item.get('facts', [])
            if (not isinstance(cited, list) or not isinstance(facts, list) or not cited + facts
                    or len(cited) > MAX_ITEMS or len(facts) > MAX_ITEMS):
                raise ValueError(f'{label} must cite owner-stated fields (from) or quoted owner facts')
            if any(c not in declared for c in cited) or len(set(cited)) != len(cited):
                raise ValueError(f'{label} cites fields without provenance or twice: {cited}')
            entry['facts'] = [owner_quote(f, f'{label} fact {i}', captured) for i, f in enumerate(facts)]
            entry['from'] = list(cited)
            reason = None
            for parent in cited:
                if resolve(parent, trail + (locator,))['class'] == 'assumed':
                    raise ValueError(f'{label} derives from assumed {parent}; a limit derived from an '
                                     'assumption is assumed')
                if done[parent]['enforcement'] == 'soft':
                    reason = reason or f'it derives from soft {parent}'
            # The derivation and every fact are checked for weakening, and the derivation must
            # state the derived field's own numbers in context (AGENTEE-271).
            reason = reason or weakened([entry['derivation']] + [f['quote'] for f in entry['facts']])
            missing = unstated(locator, entry['derivation'], numbers, unique=False)
            if reason is None and missing:
                reason = f'the derivation does not fix the limit: {missing}'
        else:
            fields(item, label, ['class', 'rationale'])
            entry['rationale'] = prose(item['rationale'], f'{label} rationale')
            reason = 'agent-proposed default awaiting owner confirmation'
        entry['enforcement'] = 'soft' if reason else 'hard'
        if reason:
            entry['soft_reason'] = reason
        done[locator] = entry
        return entry
    for locator in locators:
        if locator in declared:
            entries[locator] = resolve(locator)
        else:
            entries[locator] = {'locator': locator, 'class': 'unknown', 'enforcement': 'hard',
                                'reason': 'no provenance declared; enforced as before and never '
                                          'treated as owner-stated'}
    constraints = [entries[l] for l in locators]
    sources = sorted({q['source']['path'] for e in constraints for q in [e] + e.get('facts', [])
                      if 'source' in q and q['verification'] == 'quote_in_source'})
    return {'status': 'unknown' if any(e['class'] == 'unknown' for e in constraints) else 'declared',
            'constraints': constraints, 'source_paths': sources,
            'owner_confirmation': [e['locator'] for e in constraints if e['enforcement'] == 'soft']}


def validate(raw, *, profile=None, captured=None):
    """Return the validator record or raise ValueError; never a partial pass.

    captured=(files, inputs) is a capture inventory against which the profile's
    cited sources are verified; without it no limit is ready, so profile support
    stays unresolved rather than assumed.
    """
    envelope = load(raw)
    schema = envelope.get('schema') if isinstance(envelope, dict) else None
    fields(envelope, 'envelope', ['schema', 'id', 'outline', 'layer_count', 'stackup', 'fab_profile',
                                  'mounting_holes', 'keepouts', 'fixed_footprints']
           + (['obligations'] if schema == SCHEMAS[1] else []), ['max_component_height_mm', 'constraint_provenance'])
    if schema not in SCHEMAS:
        raise ValueError(f'schema must be one of {SCHEMAS}')
    eid = name(envelope['id'], 'envelope id', ENVELOPE_ID)
    if profile is not None and not isinstance(profile, bytes):
        raise ValueError('profile must be bytes')
    vertices, edges, outline = validate_outline(envelope['outline'])
    stackup = validate_stackup(envelope['stackup'], envelope['layer_count'], schema=schema)
    height = ({'max_component_height_mm': mm(length(envelope['max_component_height_mm'],
                                                 'max_component_height_mm', positive=True))}
              if 'max_component_height_mm' in envelope else {})
    copper = copper_names(envelope['layer_count'])
    fab, unresolved = validate_profile(envelope['fab_profile'], profile, captured,
                                       declared_inventory(envelope['layer_count'], copper, edges))
    obligations, staged, contract = [], [], None
    namespace = set()
    unique_names(envelope['fixed_footprints'], 'reference', 'fixed footprint', namespace)
    unique_names(envelope['mounting_holes'], 'id', 'mounting hole', namespace)
    unique_names(envelope['keepouts'], 'id', 'keepout', namespace)
    if schema == SCHEMAS[1]:
        unresolved.extend(stackup_support(stackup, fab, captured))
        obligations = validate_obligations(envelope['obligations'], envelope)
        contract = obligation_contract(envelope, obligations, vertices)
        unresolved.extend({'predicate': f"mechanical_{item['id']}",
                           'reason': f"{item['statement']} Unbound until {', '.join(item['owner_items'])}."}
                          for item in obligations if 'coverage' not in item)
        # Staged obligations admit generation but stay open for final physical qualification.
        staged.extend({'predicate': f"mechanical_{item['id']}_{entry['id']}", 'obligation': item['id'],
                       'stage': entry['stage'], 'reason': f"{entry['statement']} Open until "
                                                          f"{', '.join(item['owner_items'])}."}
                      for item in obligations if 'coverage' in item for entry in item['coverage']['open'])
    provenance = validate_provenance(envelope, vertices, captured)
    return {'schema': 'agent-ee.envelope-validation/1', 'envelope_id': eid,
            'envelope_sha256': hashlib.sha256(raw).hexdigest(),
            'structural': 'pass', 'outcome': 'unknown' if unresolved or staged else 'pass',
            'unresolved': unresolved, **({'qualification_open': staged} if staged else {}),
            'acceptance_ready': False,
            'outline': outline, 'layer_count': envelope['layer_count'], 'copper_layers': copper,
            'stackup': stackup, 'fab_profile': fab,
            **height,
            **({'obligations': obligations} if schema == SCHEMAS[1] else {}),
            # Recorded only while staging: an unstaged contracted envelope's record is unchanged.
            **({'obligation_contract': contract} if contract and contract['required'] else {}),
            'mounting_holes': validate_holes(envelope['mounting_holes'], vertices),
            'keepouts': validate_keepouts(envelope['keepouts'], vertices, copper),
            'fixed_footprints': validate_fixed(envelope['fixed_footprints'], vertices, edges),
            'constraint_provenance': provenance}


SOFT_PREFIX = {'assumed': 'ASSUMED, not a hard limit until the owner confirms it',
               'owner_stated': 'SOFT owner statement, not a hard limit until the owner states it',
               'derived': 'SOFT derivation, not a hard limit until the owner confirms it'}


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
    if 'max_component_height_mm' in record:
        height = record['max_component_height_mm']
        out.append(obligation(eid, 'component-height', '/max_component_height_mm', height,
                              f'every footprint has sourced installed height at most {height} mm.',
                              source_path, CHECK_KINDS['max_component_height_mm']))
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
                              f"{hole['drill_mm']} mm hole centred at {point_text(hole['at_mm'])} mm "
                              f"within {hole['tolerance_mm']} mm.",
                              source_path, CHECK_KINDS['mounting_holes']))
    for index, keepout in enumerate(record['keepouts']):
        polygon = (', '.join(point_text(p) for p in keepout['polygon_mm']) + ' mm'
                   if 'polygon_mm' in keepout else
                   f"a source-qualified ESP32-S3-WROOM-1 placement-case polygon (currently unresolved) owned by "
                   f"{keepout['antenna']['reference']} requiring qualification from {keepout['antenna']['facts_check']} facts")
        out.append(obligation(eid, f"keepout-{keepout['id']}", f'/keepouts/{index}', keepout,
                              f"keepout {keepout['id']} forbids {', '.join(keepout['forbid'])} on "
                              f"{', '.join(keepout['layers'])} inside polygon {polygon}.",
                              source_path, CHECK_KINDS['keepouts']))
    for index, fixed in enumerate(record['fixed_footprints']):
        out.append(obligation(eid, f"fixed-{fixed['reference']}", f'/fixed_footprints/{index}', fixed,
                              f"footprint {fixed['reference']} is fixed on the {fixed['side']} side "
                              f"({fixed['layer']}) with its origin {fixed['position_mm']} mm along edge "
                              f"{fixed['edge']} and {fixed['inset_mm']} mm inward, at "
                              f"{point_text(fixed['anchor_mm'])} mm, rotation {fixed['rotation_deg']} deg, "
                              f"within {fixed['tolerance_mm']} mm; it is mated {fixed['insertion']} "
                              f"(mating axis {fixed['mating_axis']})." + (
                                  ' Its courtyard may extend beyond the outline only by at most ' +
                                  '; '.join(f"{o['max_mm']} mm across edge {o['edge']}"
                                            for o in fixed['courtyard_overhangs']) +
                                  (', and into the corner between two adjacent declared edges.'
                                   if len(fixed['courtyard_overhangs']) > 1 else '.')
                                  if 'courtyard_overhangs' in fixed else ''),
                              source_path, CHECK_KINDS['fixed_footprints']))
    for index, item in enumerate(record.get('obligations', [])):
        staged = ''
        if 'coverage' in item:
            witnesses = [w['locator'] + (f" ({w['criterion']})" if 'criterion' in w else '')
                         for w in item['coverage']['generation']]
            staged = (' Staged: candidate generation witnessed by ' + (', '.join(witnesses) or 'no concrete field')
                      + '; open for final qualification: '
                      + '; '.join(f"{e['id']} ({e['stage']})" for e in item['coverage']['open']) + '.')
        out.append(obligation(eid, f"obligation-{item['id']}", f'/obligations/{index}', item,
                              item['statement'] + f" Requirement IDs: {', '.join(item['requirement_ids'])}; "
                              f"checks pending {', '.join(item['owner_items'])}." + staged,
                              source_path, CHECK_KINDS['obligations']))
    by_locator = {e['locator']: e for e in record.get('constraint_provenance', {}).get('constraints', [])}
    provenance = {}
    for item in out:
        entry = by_locator.get(item['source']['locator'], {'class': 'unknown', 'enforcement': 'hard'})
        provenance[item['id']] = {k: entry[k] for k in ('class', 'enforcement', 'soft_reason') if k in entry}
        if entry['enforcement'] == 'soft':
            # Soft limits are surfaced for owner confirmation, never acceptance ceilings.
            item['intent'] = 'informational'
            item['statement'] = (f"{SOFT_PREFIX[entry['class']]} ({entry['soft_reason']}): "
                                 + item['statement'])
    return {'schema': 'agent-ee.envelope-requirements/1', 'envelope_id': eid,
            'envelope_sha256': record['envelope_sha256'], 'source_path': source_path,
            'complete': not record['unresolved'], 'unresolved': record['unresolved'],
            **({'qualification_open': record['qualification_open']} if 'qualification_open' in record else {}),
            'requirements': out, 'provenance': provenance}


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
                       f"(field sha256 {r['field_sha256']}); check kinds: {', '.join(r['check_kinds'])}\n"
                       f"  Provenance: {requirements['provenance'][r['id']]['class']}, "
                       f"{requirements['provenance'][r['id']]['enforcement']} limit\n\n"
                       for r in requirements['requirements'])
    markdown += ''.join(f"Owner confirmation needed: {locator}\n"
                        for locator in record['constraint_provenance']['owner_confirmation'])
    markdown += ''.join(f"Unresolved: {u['predicate']}: {u['reason']}\n" for u in record['unresolved'])
    markdown += ''.join(f"Open for final qualification ({u['stage']}): {u['predicate']}: {u['reason']}\n"
                        for u in record.get('qualification_open', []))
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
    """One selected fixed reference, one witness over these exact envelope bytes."""
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
    fixed = record['fixed_footprints'][int(item['source']['locator'].rsplit('/', 1)[1])]
    if 'reference' not in params:
        if len(record['fixed_footprints']) != 1:
            return 'connector-edge/1 needs an explicit reference for each of several fixed footprints'
    elif params['reference'] != fixed['reference']:
        raise ValueError(f"connector-edge/1 reference {params['reference']!r} does not cover {fixed['reference']}")
    return None


def stackup_authority_reason(record):
    if record['fab_profile'].get('stackup', {}).get('execution') != 'ready':
        return 'copper_weight_oz needs qualified captured manufacturing authority; no inferred conversion'
    missing = [u['reason'] for u in record['unresolved']
               if u['predicate'].startswith('stackup_layer_source_') or
               u['predicate'] == 'finished_thickness_tolerance']
    return '; '.join(missing) if missing else None


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
    if field in ('mounting_holes', 'keepouts'):
        selected = record[field][int(item['source']['locator'].rsplit('/', 1)[1])]
        if params['id'] != selected['id']:
            raise ValueError(f"native envelope selector {params['id']} does not cover {selected['id']}")
    if field == 'stackup':
        return stackup_authority_reason(record)
    return None


# (field, check kind) -> validator. Coverage for another field needs the owning
# item to add a validator here; registering a kind or criterion is not enough.
BINDINGS = {('fab_profile', 'fab_profile'): bind_fab_profile,
            ('fixed_footprints', 'placement'): bind_connector_edge,
            **{(field, 'board_envelope'): bind_native_envelope
               for field in ('outline', 'layer_count', 'stackup', 'mounting_holes', 'keepouts',
                             'max_component_height_mm')}}


def field_of(item):
    return item['source']['locator'].split('/')[1]


def obligation_coverage(scope, requirements, record, files, inputs=None):
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
    report, staged = [], []
    for item in requirements['requirements']:
        rid, mapped, field = item['id'], baseline.get(item['id']), field_of(item)
        kinds = item['check_kinds']
        entry = {'id': rid, 'locator': item['source']['locator'], 'check_kinds': kinds, 'checks': []}
        provenance = requirements.get('provenance', {}).get(rid, {'class': 'unknown', 'enforcement': 'hard'})
        entry['provenance'] = provenance
        if field == 'obligations' and 'coverage' in (declared := record['obligations'][index_of(item)]):
            # Staged before the soft branch: soft provenance cannot hide open items. Never bound: no check evaluates a generic obligation, so a mapping claims too much.
            if mapped is not None and (mapped.get('checks') or mapped.get('statement') != item['statement']
                                       or mapped.get('source') != item['source']):
                raise ValueError(f'envelope obligation {rid} is staged: a baseline mapping cannot claim its '
                                 'final qualification or alter its statement or source')
            staged.append((entry, declared))
            continue
        intent = item.get('intent', 'acceptance')
        if intent == 'informational':
            # A soft limit (AGENTEE-266) is reported for owner confirmation; it is not acceptance
            # and needs no check, but a mapping must still carry its exact statement and intent.
            if mapped is not None and (mapped.get('statement') != item['statement']
                                       or mapped.get('source') != item['source'] or mapped.get('intent') != intent):
                raise ValueError(f'envelope obligation {rid} statement, source or informational intent differs')
            report.append({**entry, 'checks': list(mapped.get('checks', [])) if mapped else [], 'status': 'soft',
                           'reason': f"{provenance['class']} limit is soft: {provenance['soft_reason']}"})
            continue
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
    by_locator = {r['locator']: r for r in report}
    for entry, declared in staged:
        report.insert(next(i for i, r in enumerate(requirements['requirements']) if r['id'] == entry['id']),
                      staged_coverage(entry, declared, by_locator, specs, record, files, inputs))
    return report


def index_of(item):
    return int(item['source']['locator'].rsplit('/', 1)[1])


def identity_witness(reference, expected, specs, files, inputs):
    """Contracted part identity from the existing parts enforcement, never from geometry.

    Every parts_subset check (the scaffold's native membership check, AGENTEE-86) must read
    a captured agent-ee.parts/1 manifest declaring one required purchased root-sheet part
    for this reference with the contracted manufacturer, MPN and footprint, the footprint
    an exact captured member of the manifest's CAD closure (the member the scaffold loads).
    The complete manifest must then pass the authoritative parts_manifest.qualify_manifest
    (canonical record/catalog replay, complete native CAD coverage) with the part's datasheet
    provenance captured, so a self-consistent substitution behind the contracted strings refuses.
    The check then compares the native instance with that declaration; DRC schematic parity
    and connector-edge/1 bind the board footprint of the same reference.
    """
    from .catalog import strict_json
    from .copper_features import is_copper
    from .footprint_library import _members
    from .parts_manifest import _read_blob, load_manifest, qualify_manifest
    from .probe_features import is_probe
    checks = sorted(cid for cid, spec in specs.items() if spec.get('kind') == 'parts_subset')
    if not checks:
        return [], f'{reference} needs a parts_subset check over the captured parts manifest declaring {expected}'
    if inputs is None:
        return [], f'{reference} part identity needs the captured inputs'
    problems = []
    for cid in checks:
        params = specs[cid].get('params')
        try:
            manifest = params.get('manifest') if isinstance(params, dict) else None
            if not isinstance(manifest, str) or manifest not in files or digest(Path(inputs) / manifest) != files[manifest]:
                raise ValueError(f'parts manifest {manifest!r} is not captured')
            document = load_manifest(inputs, manifest)
            parts = [p for p in document['parts'] if {'sheet': '/', 'reference': reference} in p['instances']]
            if len(parts) != 1:
                raise ValueError(f'{manifest} declares no root-sheet part for {reference}')
            part = parts[0]
            if is_probe(part) or is_copper(part) or part.get('required') is not True:
                raise ValueError(f'{manifest} {reference} is not a required purchased part')
            wrong = {field: part[field] for field, value in expected.items() if part[field] != value}
            if wrong:
                raise ValueError(f'{manifest} declares {reference} as {wrong}, not the contracted {expected}')
            cad = document['cad']['manifest']
            if files.get(cad['path']) != cad['sha256']:
                raise ValueError(f"CAD manifest {cad['path']} is not captured")
            members = {lib + ':' + Path(m['path']).stem: m for lib, m in _members(strict_json(_read_blob(inputs, cad)))}
            member = members.get(part['footprint'])
            if member is None or files.get(member['path']) != member['sha256']:
                raise ValueError(f"footprint {part['footprint']} is not a captured member of {cad['path']}")
            # Declared strings are not the producer result: the existing authoritative qualifier
            # replays the canonical part record or catalog row and the native CAD assignments.
            qualified = qualify_manifest(document, inputs)
            if qualified['cad']['coverage'] != 'complete':
                raise ValueError(f'{manifest} captured CAD coverage is incomplete')
            if (part['datasheet']['status'] != 'captured'
                    or part['id'] in {m['part_id'] for m in qualified['missing_provenance']}):
                raise ValueError(f'{manifest} {reference} has no captured datasheet provenance')
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
            problems.append(f'{cid}: {exc}')
    if problems:
        return [], f'{reference} part identity unestablished: ' + '; '.join(problems)
    return checks, None


def staged_coverage(entry, declared, by_locator, specs, record, files, inputs=None):
    """Generation admission for one generic obligation; its open items are never closed here."""
    missing, witnesses = [], []
    contract = record.get('obligation_contract')
    expected = OBLIGATION_CONTRACTS[contract['id']]['obligations'][declared['id']] if contract else None
    listed = {(w['locator'], w.get('criterion')) for w in declared['coverage']['generation']}
    # The validated record's contract fixes the minimum; a coverage list cannot shrink it.
    missing += [f'{locator} ({criterion or "bound"}) is required by source contract {contract["id"]}'
                for locator, criterion in (contract or {}).get('required', {}).get(declared['id'], [])
                if (locator, criterion) not in listed]
    if contract is None or declared['id'] not in contract['required']:
        missing.append('staged without a reviewed source contract')
    for witness in declared['coverage']['generation']:
        locator, criterion = witness['locator'], witness.get('criterion')
        if criterion == PART_IDENTITY:
            reference = record['fixed_footprints'][int(locator.rsplit('/', 1)[1])]['reference']
            identity = (expected or {}).get('fixed', {}).get('identity')
            if identity is None or expected['fixed']['reference'] != reference:
                checks, problem = [], f'{locator} {reference} has no contracted part identity'
            else:
                checks, problem = identity_witness(reference, identity, specs, files, inputs)
            if problem:
                missing.append(problem)
            witnesses.append({'locator': locator, 'criterion': criterion, 'checks': checks,
                              'status': 'bound' if not problem else 'absent'})
            continue
        if criterion is None:
            concrete = by_locator.get(locator)
            status = concrete['status'] if concrete else 'absent'
            # A soft (informational) concrete limit is not acceptance, so it cannot witness.
            if status != 'bound':
                missing.append(f"{locator} is {status}" + (f": {concrete['reason']}" if concrete and 'reason'
                                                             in concrete else ''))
            witnesses.append({'locator': locator, 'requirement': concrete['id'] if concrete else None,
                              'checks': concrete['checks'] if concrete else [], 'status': status})
            continue
        reference = record['fixed_footprints'][int(locator.rsplit('/', 1)[1])]['reference']
        checks = sorted(cid for cid, spec in specs.items()
                        if spec.get('kind') == 'placement' and isinstance(spec.get('params'), dict)
                        and spec['params'].get('criterion') == criterion
                        and files.get(spec['params'].get('envelope')) == record['envelope_sha256'])
        if not checks:
            missing.append(f'{locator} {reference} needs a placement {criterion} check over these captured '
                           'envelope bytes')
        witnesses.append({'locator': locator, 'criterion': criterion, 'checks': checks,
                          'status': 'bound' if checks else 'absent'})
    result = {**entry, 'witnesses': witnesses, 'open': declared['coverage']['open']}
    if missing:
        return {**result, 'status': 'unknown', 'reason': 'staged generation witnesses missing: ' + '; '.join(missing)}
    return {**result, 'status': 'staged', 'reason': 'candidate generation admitted; final physical qualification '
                                                    'open: ' + ', '.join(e['id'] for e in declared['coverage']['open'])}


def require_obligations(scope, requirements, record, files, inputs=None):
    """Refuse a scope/3 baseline that drops, alters, vacuously maps or leaves unknown an obligation."""
    report = obligation_coverage(scope, requirements, record, files, inputs)
    missing = [f"{r['id']} ({r['status']}: {r['reason']})" for r in report
               if r['status'] not in ('bound', 'soft', 'staged')]
    if missing:
        raise ValueError('envelope obligations lack executable coverage and are refused, not dropped: '
                         + '; '.join(missing))
    if not requirements['complete']:
        raise ValueError('envelope requirements are incomplete: unresolved ' +
                         ', '.join(u['predicate'] for u in requirements['unresolved']))
    return report


def require_qualified(coverage):
    """Final physical qualification: refuse while any staged obligation has open evidence.

    Admission for candidate generation (require_obligations) never implies this;
    no envelope or scope field can close an open item.
    """
    staged = [f"{r['id']} ({', '.join(e['id'] for e in r['open'])})" for r in coverage
              if r['status'] == 'staged']
    if staged:
        raise ValueError('final physical qualification refused: staged envelope obligations remain open: '
                         + '; '.join(staged))
    unbound = [r['id'] for r in coverage if r['status'] not in ('bound', 'soft')]
    if unbound:
        raise ValueError(f'final physical qualification refused: obligations not bound: {unbound}')
    return coverage


QUALIFICATION_SCHEMA = 'agent-ee.final-qualification/1'


def qualification_blockers(record):
    """What holds final acceptance in a captured agent-ee.envelope-baseline/1 record.

    Read from the record's own coverage, never from an unresolved list alone: a
    staged obligation has no unresolved predicate yet stays open. Each blocker is
    {id, status, open: [item or predicate IDs]}; an empty list is qualification.
    """
    requirements, coverage = record['requirements'], record['coverage']
    blockers = []
    if not requirements['complete'] or requirements.get('unresolved'):
        blockers.append({'id': 'envelope-requirements', 'status': 'incomplete',
                         'open': [u['predicate'] for u in requirements.get('unresolved', [])]})
    by_id = {r['id']: r for r in coverage}
    for item in requirements['requirements']:
        entry = by_id.get(item['id'])
        if entry is None:
            blockers.append({'id': item['id'], 'status': 'absent', 'open': []})
        elif entry['status'] == 'staged':
            blockers.append({'id': entry['id'], 'status': 'staged', 'open': [e['id'] for e in entry['open']]})
        elif entry['status'] not in ('bound', 'soft'):
            blockers.append({'id': entry['id'], 'status': entry['status'], 'open': []})
    blockers += [{'id': r['id'], 'status': 'unlisted', 'open': []} for r in coverage
                 if r['id'] not in {item['id'] for item in requirements['requirements']}]
    return blockers


def final_qualification(run):
    """The final-acceptance guard over a run's CAPTURED baseline envelope; None without an envelope.

    The candidate identity and immutable baseline are verified first, so None
    means a verified envelope-less capture, never a removed declaration.

    The one shared guard for final review outcome, fab release and campaign
    acceptance/replay (AGENTEE-197). It re-verifies the immutable baseline.json
    (load_baseline) and reads its envelope coverage; the candidate's current
    envelope file, scope fields and stored results are never consulted, so
    nothing a writer or reviewer changes can close an open item.
    """
    from .baseline import load_baseline
    from .storage import identity, read_json
    run = Path(run)
    manifest = read_json(run / 'candidate.json')
    # Authenticate the retained candidate and its baseline before trusting the absence of an
    # envelope: a removed or edited scope declaration is refused, never read as "no envelope".
    if identity({'scope': manifest['scope'], 'files': manifest['files']}) != manifest.get('candidate_id'):
        raise ValueError('candidate metadata differs from its captured identity; final qualification refused')
    baseline = load_baseline(run)
    record = (baseline or {}).get('envelope')
    if 'envelope' not in manifest['scope']:
        if record is not None:
            raise ValueError('captured baseline carries envelope coverage the candidate no longer declares')
        return None
    if not isinstance(record, dict) or record.get('schema') != 'agent-ee.envelope-baseline/1':
        raise ValueError('scope declares an envelope but its captured baseline carries no envelope coverage')
    return {'schema': QUALIFICATION_SCHEMA, 'baseline_id': baseline['baseline_id'],
            'blocking': qualification_blockers(record)}


def describe_blockers(blockers):
    return '; '.join(f"{b['id']} {b['status']}" + (f" ({', '.join(b['open'])})" if b['open'] else '')
                     for b in blockers)


def require_final(run):
    """Refuse final acceptance or sealed release while the captured envelope qualification is open."""
    qualification = final_qualification(run)
    if qualification is not None and qualification['blocking']:
        raise ValueError('final acceptance refused: captured envelope qualification open: '
                         + describe_blockers(qualification['blocking']))
    return qualification


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

    kind is 'envelope' (agent-ee.envelope/1 or /2), 'derived' (its validation or
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
    if value.get('schema') in SCHEMAS:
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
    dependencies.update(record['stackup'].get('source_paths', []))
    dependencies.update(record['constraint_provenance']['source_paths'])
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
    coverage = require_obligations(scope, requirements, record, files, inputs)
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
            'copper_types': {n[1]: n[2] for n in (layer_table or [])[1:]
                             if isinstance(n, list) and len(n) >= 3 and n[1] in copper},
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
    total = length(Decimal(record['stackup']['thickness_mm']), 'thickness')
    thickness_field = 'nominal_thickness_mm' if envelope['schema'] == SCHEMAS[1] else 'thickness_mm'
    add('/stackup/' + thickness_field, 'match' if facts['thickness_nm'] == total else 'mismatch',
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
            if 'role' in layer:
                actual = facts['copper_types'].get(layer['name'])
                expected = 'power' if layer['role'] == 'plane' else 'signal'
                add(pointer + '/role', 'match' if actual == expected else 'mismatch', {'board': actual})
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
    for index, _ in enumerate(record.get('obligations', [])):
        add(f'/obligations/{index}', 'not_compared', {'reason': UNBOUND['obligations']})
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
        raw = args.envelope.read_bytes()
        declares = isinstance(load(raw), dict) and 'constraint_provenance' in load(raw)
        if args.sources and not args.profile and not declares:
            raise ValueError('--sources needs --profile or a constraint_provenance declaration')
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
    validation = json.loads(artifacts['envelope-validation.json'])
    return 1 if (validation['unresolved'] or validation.get('qualification_open')
                 or (trip and trip['outcome'] != 'match')) else 0


if __name__ == '__main__':
    raise SystemExit(main())

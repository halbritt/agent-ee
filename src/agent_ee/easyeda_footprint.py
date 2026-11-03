"""Strict reader for one captured EasyEDA standard component's package footprint.

The input is the raw component object exactly as captured (sha256-pinned by the
caller). Nothing is fetched. Every package record is either converted into a
land-geometry pad/hole, proven non-electrical by its declared layer, or refused:
unknown record types (for example ``PT~M``), unequal ellipse pads and conductive
non-pad records make the footprint unsupported rather than dropped. Standard
through vias are represented only with unambiguous rectangular SMD-pad ownership.
Coordinates are exact decimals; one canvas unit is 10 mil = 254000 nm, +Y down.
"""
from decimal import Decimal
from fractions import Fraction
from itertools import permutations
import json
import re


FORMAT = 'easyeda-std-component/1'
UNIT_NM = Fraction(254000)
MAX_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 20000
MAX_CONTAINED_VIAS = 64
# EasyEDA standard layer ids. Inner copper (21..52) is conductive and not qualified.
SURFACE = {'1': ('F.Cu',), '2': ('B.Cu',), '11': ('B.Cu', 'F.Cu')}
NONCOPPER = {'3': 'TopSilk', '4': 'BottomSilk', '5': 'TopPaste', '6': 'BottomPaste',
             '7': 'TopSolderMask', '8': 'BottomSolderMask', '10': 'BoardOutline',
             '12': 'Document', '13': 'TopAssembly', '14': 'BottomAssembly', '15': 'Mechanical',
             '19': '3DModel', '99': 'ComponentShape', '100': 'LeadShape', '101': 'ComponentMarking'}
# Field index of the layer id in each graphic record (after the type token).
GRAPHIC_LAYER = {'TRACK': 2, 'SOLIDREGION': 1, 'CIRCLE': 5, 'ARC': 2, 'RECT': 5, 'TEXT': 7}
_DECIMAL = re.compile(r'-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z')


class SupplierError(ValueError):
    """Refusal with a land-pattern mode and coordinator execution status."""

    def __init__(self, mode, message, execution):
        super().__init__(message)
        self.mode = mode
        self.execution = execution


def _malformed(message):
    raise SupplierError('supplier_malformed', message, 'malformed')


def _unsupported(message, mode='unsupported_shape'):
    raise SupplierError(mode, message, 'unsupported')


def decimal(value, label):
    if not isinstance(value, str) or not _DECIMAL.match(value):
        _malformed(f'{label} is not an exact decimal: {value!r}')
    return Fraction(value)


def text(value):
    """Exact finite decimal text for a rational with a 2/5-only denominator."""
    value = Fraction(value)
    sign, value = ('-' if value < 0 else ''), abs(value)
    whole, rest = divmod(value.numerator, value.denominator)
    digits = []
    for _ in range(64):
        if not rest:
            break
        whole_digit, rest = divmod(rest * 10, value.denominator)
        digits.append(str(whole_digit))
    if rest:
        raise ValueError('value has no finite decimal representation')
    return sign + str(whole) + ('.' + ''.join(digits) if digits else '')


def strict_json(data):
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        _malformed('supplier footprint must be bytes, at most 16 MiB')
    def pairs(entries):
        record = {}
        for key, value in entries:
            if key in record:
                _malformed('duplicate JSON key: ' + key)
            record[key] = value
        return record
    def constant(token):
        _malformed('nonfinite JSON number')
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs,
                          parse_float=Decimal, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        _malformed('supplier footprint is not strict UTF-8 JSON: ' + str(exc))


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)) or (
            isinstance(value, Decimal) and not value.is_finite()):
        _malformed(f'{label} must be a finite JSON number')
    return Fraction(value)


def _dict(value, label, keys=()):
    if not isinstance(value, dict) or any(k not in value for k in keys):
        _malformed(f'{label} requires fields: ' + ', '.join(keys))
    return value


def identity(component, lcsc):
    """Validate the wrapper, its declared LCSC association and the single package."""
    _dict(component, 'component', ('uuid', 'docType', 'lcsc', 'dataStr', 'packageDetail'))
    if 'result' in component or 'success' in component:
        _malformed('API envelopes are not captured component objects')
    if component['docType'] != 2:
        _malformed('component docType must be 2 (schematic component with package)')
    declared = _dict(component['lcsc'], 'lcsc', ('number',))['number']
    if declared != lcsc:
        raise SupplierError('supplier_identity', f'captured component is {declared!r}, binding is {lcsc!r}',
                            'malformed')
    if 'szlcsc' in component and _dict(component['szlcsc'], 'szlcsc', ('number',))['number'] != lcsc:
        raise SupplierError('supplier_identity', 'szlcsc number differs from bound LCSC id', 'malformed')
    head = _dict(_dict(component['dataStr'], 'dataStr', ('head',))['head'], 'symbol head', ('c_para', 'puuid'))
    para = _dict(head['c_para'], 'symbol c_para')
    if para.get('Supplier Part', lcsc) != lcsc:
        raise SupplierError('supplier_identity', 'symbol Supplier Part differs from bound LCSC id', 'malformed')
    package = _dict(component['packageDetail'], 'packageDetail', ('uuid', 'title', 'docType', 'dataStr'))
    if package['docType'] != 4:
        _malformed('exactly one package with docType 4 is required')
    if head['puuid'] != package['uuid']:
        raise SupplierError('supplier_identity', 'symbol puuid does not name the captured package', 'malformed')
    data = _dict(package['dataStr'], 'package dataStr', ('head', 'shape'))
    phead = _dict(data['head'], 'package head', ('docType', 'uuid', 'x', 'y'))
    if phead['docType'] != '4' or phead['uuid'] != package['uuid']:
        _malformed('package head does not match packageDetail identity')
    if not isinstance(package['title'], str) or not package['title']:
        _malformed('package title required')
    if not isinstance(data['shape'], list) or len(data['shape']) > MAX_RECORDS:
        _malformed('package shape list required (bounded)')
    origin = (_number(phead['x'], 'package origin x'), _number(phead['y'], 'package origin y'))
    record = dict(lcsc=lcsc, component_uuid=component['uuid'], package_uuid=package['uuid'],
                  package=package['title'], symbol_package=para.get('package'),
                  manufacturer_part=para.get('Manufacturer Part'), origin=[text(v) for v in origin])
    return record, data['shape'], origin


def _points(raw, label, count):
    values = raw.split()
    if len(values) != 2 * count:
        _malformed(f'{label} requires {count} points')
    return [(decimal(values[i], label), decimal(values[i + 1], label)) for i in range(0, len(values), 2)]


def _quarter(rotation, label):
    turn = rotation % 360
    if turn % 90:
        _unsupported(f'{label}: rotation {text(rotation)} is not a quarter turn; EasyEDA '
                     'rotation direction is unqualified for general angles', 'unsupported_rotation')
    return turn


def _rect_corners(cx, cy, w, h, turn):
    hw, hh = (w / 2, h / 2) if turn in (0, 180) else (h / 2, w / 2)
    return sorted([(cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh), (cx - hw, cy + hh)])


def _near(a, b, tol):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _pad(fields, tol):
    if len(fields) != 20:
        _malformed(f'PAD requires 20 fields, found {len(fields)}')
    shape, number, ident = fields[1], fields[8], fields[12]
    label = f'PAD {ident or "?"} number {number!r}'
    cx, cy, w, h = (decimal(fields[i], label) for i in (2, 3, 4, 5))
    if w <= 0 or h <= 0:
        _malformed(label + ': nonpositive size')
    if fields[6] not in SURFACE:
        _unsupported(f'{label}: layer {fields[6]!r} is not a qualified copper surface', 'unsupported_layer')
    if not number:
        _unsupported(label + ': unnumbered copper pad is not qualified')
    rotation = decimal(fields[11], label + ' rotation')
    turn = _quarter(rotation, label)
    hole_r = decimal(fields[9], label + ' hole radius')
    if shape == 'RECT':
        corners = _points(fields[10], label + ' outline', 4)
        expected = _rect_corners(cx, cy, w, h, turn)
        # Roundoff can reverse lexicographic order along a nominally vertical edge.
        if not any(all(_near(a, b, tol) for a, b in zip(order, expected))
                   for order in permutations(corners)):
            raise SupplierError('source_inconsistent', label + ': outline differs from declared rectangle',
                                'malformed')
        primitive = dict(shape='rect')
    elif shape == 'OVAL':
        ends = _points(fields[10], label + ' centreline', 2)
        span = abs(w - h) / 2
        axis = (w > h) == (turn in (0, 180))
        expected = sorted([(cx - span, cy), (cx + span, cy)] if axis else [(cx, cy - span), (cx, cy + span)])
        if any(not _near(a, b, tol) for a, b in zip(sorted(ends), expected)):
            raise SupplierError('source_inconsistent', label + ': centreline differs from declared oval',
                                'malformed')
        primitive = dict(shape='circle' if w == h else 'oval')
    elif shape == 'ELLIPSE':
        if w != h:
            _unsupported(label + ': unequal ELLIPSE axes are an unqualified ellipse')
        primitive = dict(shape='circle')
    elif shape == 'POLYGON':
        values = fields[10].split()
        if len(values) < 6 or len(values) % 2:
            _malformed(label + ': polygon requires at least three coordinate pairs')
        points = _points(fields[10], label + ' outline', len(values) // 2)
        # The source may repeat each edge endpoint and close the outline.
        # Remove only zero-length edges; never snap, simplify or take a hull.
        outline = []
        for point in points:
            if not outline or point != outline[-1]:
                outline.append(point)
        if len(outline) > 1 and outline[-1] == outline[0]:
            outline.pop()
        from .land_geometry import GeometryError, polygon_area
        try:
            polygon_area([outline])
        except GeometryError as exc:
            raise SupplierError(exc.mode, label + ': ' + str(exc), exc.execution) from exc
        xs, ys = zip(*outline)
        box_center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        box_size = (max(xs) - min(xs), max(ys) - min(ys))
        expected_size = (w, h) if turn in (0, 180) else (h, w)
        if not _near((cx, cy), box_center, tol) or not _near(expected_size, box_size, tol):
            raise SupplierError('source_inconsistent', label + ': centre/size differ from polygon outline box',
                                'malformed')
        # Points are already in canvas coordinates, including source rotation.
        primitive = dict(shape='polygon', points=outline)
    else:
        _malformed(f'{label}: unknown pad shape {shape!r}')
    primitive.update(center=[cx, cy], size=[w, h], angle=0 if shape == 'POLYGON' else turn)
    hole = None
    length_field, ends_field, plated = fields[13], fields[14], fields[15]
    if hole_r < 0:
        _malformed(label + ': negative hole radius')
    if hole_r == 0:
        if fields[6] == '11' or ends_field or (length_field and decimal(length_field, label) != 0):
            raise SupplierError('source_inconsistent', label + ': SMD/through-hole fields disagree',
                                'malformed')
    else:
        if fields[6] != '11':
            raise SupplierError('source_inconsistent', label + ': drilled pad is not multilayer', 'malformed')
        if plated != 'Y':
            _unsupported(label + ': non-plated drilled copper pad is not qualified')
        length = decimal(length_field, label + ' hole length') if length_field else Fraction(0)
        diameter = 2 * hole_r
        if length > diameter + tol:
            if not ends_field:
                _malformed(label + ': slot is missing its endpoints')
            (ax, ay), (bx, by) = _points(ends_field, label + ' slot', 2)
            dx, dy = abs(bx - ax), abs(by - ay)
            if min(dx, dy) > tol:
                _unsupported(label + ': slot is not axis-aligned', 'unsupported_rotation')
            if abs(max(dx, dy) - (length - diameter)) > tol:
                raise SupplierError('source_inconsistent', label + ': slot endpoints disagree with length',
                                    'malformed')
            if not _near(((ax + bx) / 2, (ay + by) / 2), (cx, cy), tol):
                _unsupported(label + ': slot offset from pad centre is not qualified')
            size = [length, diameter] if dx > dy else [diameter, length]
            hole = dict(shape='oval', center=[cx, cy], size=size, angle=0)
        elif length and abs(length - diameter) > tol:
            raise SupplierError('source_inconsistent', label + ': hole length below diameter', 'malformed')
        else:
            if ends_field:
                (ax, ay), (bx, by) = _points(ends_field, label + ' slot', 2)
                if not (_near((ax, ay), (cx, cy), tol) and _near((bx, by), (cx, cy), tol)):
                    raise SupplierError('source_inconsistent', label + ': round hole has offset endpoints',
                                        'malformed')
            hole = dict(shape='circle', center=[cx, cy], size=[diameter, diameter], angle=0)
    locator = dict(record='PAD', id=ident, number=number, shape=shape, layer=fields[6], raw=fields)
    return dict(number=number, layers=list(SURFACE[fields[6]]), primitive=primitive), hole, locator


def _layer_of(kind, fields):
    if kind == 'SVGNODE':
        try:
            node = json.loads('~'.join(fields[1:]))
        except (json.JSONDecodeError, RecursionError):
            _malformed('SVGNODE payload is not JSON')
        return str(node.get('layerid')) if isinstance(node, dict) else None
    index = GRAPHIC_LAYER[kind]
    return fields[index] if index < len(fields) else None


def _bounds(primitive):
    if primitive['shape'] == 'polygon':
        xs, ys = zip(*primitive['points'])
        return min(xs), min(ys), max(xs), max(ys)
    cx, cy = primitive['center']
    w, h = primitive['size']
    if primitive['angle'] % 180:
        w, h = h, w
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def _touches(a, b):
    """Conservative closed-box intersection; possible contact must not be ignored."""
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _contained_vias(records, pads, holes, locators):
    """Append complete through-via copper/holes to locally owned parser lists.

    EasyEDA Standard documents VIA diameter, hole radius and all-layer through
    plating. A blank-net VIA has no pin number. Derive that number only when its
    whole annulus lies in exactly one isolated rectangular SMD pad. Record this
    inference separately from source fields; no geometric tolerance changes it.
    Bounding-box exclusion is conservative for other shapes. This qualifies
    copper/drill representation, not mask, paste, via filling or assembly.
    """
    if not records:
        return
    if len(records) > MAX_CONTAINED_VIAS:
        _unsupported('more than 64 footprint vias exceeds the bounded qualification')
    source_pads, source_holes = list(pads), list(holes)
    boxes = [_bounds(p['primitive']) for p in source_pads]
    ids = {p['id'] for p in locators} | {h['id'] for h in holes if h.get('id')}
    via_boxes = []
    for index, fields in records:
        if len(fields) != 8 or fields[4] or fields[7] not in ('', '0', '1'):
            _unsupported('VIA requires eight fields, a blank net and a known lock flag')
        cx, cy, diameter, radius = (decimal(fields[i], 'VIA geometry') for i in (1, 2, 3, 5))
        if not fields[6] or not 0 < 2 * radius < diameter:
            _malformed('VIA requires an id and positive drill smaller than copper diameter')
        if fields[6] in ids:
            _unsupported('VIA id duplicates a conductive feature or hole')
        ids.add(fields[6])
        primitive = dict(shape='circle', center=[cx, cy], size=[diameter, diameter], angle=0)
        box = _bounds(primitive)
        owners = [i for i, (pad, bounds) in enumerate(zip(source_pads, boxes))
                  if pad['primitive']['shape'] == 'rect' and len(pad['layers']) == 1
                  and bounds[0] <= box[0] and bounds[1] <= box[1]
                  and bounds[2] >= box[2] and bounds[3] >= box[3]]
        if len(owners) != 1:
            _unsupported('VIA annulus is not contained by exactly one rectangular SMD pad')
        owner = owners[0]
        owning = source_pads[owner]
        for i, (pad, bounds) in enumerate(zip(source_pads, boxes)):
            if i != owner and (_touches(box, bounds) or
                    (set(pad['layers']) & set(owning['layers']) and _touches(boxes[owner], bounds))):
                _unsupported('VIA or its owning pad may contact other source copper')
        if any(_touches(boxes[owner], _bounds(hole)) for hole in source_holes):
            _unsupported('VIA owning pad may be interrupted by another source hole')
        if any(_touches(box, other) for other in via_boxes):
            _unsupported('VIA annuli may overlap; separate contained vias are required')
        via_boxes.append(box)
        holes.append(dict(shape='circle', center=[cx, cy], size=[2 * radius, 2 * radius], angle=0,
                          owner=len(pads), plated=True, id=fields[6]))
        pads.append(dict(number=owning['number'], layers=['B.Cu', 'F.Cu'], primitive=primitive))
        locators.append(dict(record='VIA', id=fields[6], number=owning['number'], index=index, raw=fields,
            pin_assignment=dict(method='unique_rectangular_smd_containment', pad_index=owner,
                                pad_id=locators[owner]['id'])))


def parse_package(data, lcsc, tolerance_nm):
    """Return the supplier land pattern in footprint-origin canvas units plus inventory.

    ``tolerance_nm`` bounds disagreement between redundant source fields (the
    declared rectangle and its outline, slot length and endpoints). It never
    moves geometry: declared centre/size/radius values are used verbatim.
    """
    tol = Fraction(tolerance_nm) / UNIT_NM
    record, shapes, origin = identity(strict_json(data), lcsc)
    pads, holes, locators, ignored, vias = [], [], [], {}, []
    for index, raw in enumerate(shapes):
        if not isinstance(raw, str) or '\x00' in raw:
            _malformed(f'shape #{index} is not a text record')
        fields = raw.split('~')
        kind = fields[0]
        if kind == 'PAD':
            pad, hole, locator = _pad(fields, tol)
            locator['index'] = index
            pads.append(pad)
            locators.append(locator)
            if hole is not None:
                holes.append(dict(hole, owner=len(pads) - 1, plated=True))
        elif kind == 'HOLE':
            if len(fields) != 6:
                _malformed('HOLE requires 6 fields')
            cx, cy, radius = (decimal(fields[i], 'HOLE') for i in (1, 2, 3))
            if radius <= 0:
                _malformed('HOLE requires positive radius')
            holes.append(dict(shape='circle', center=[cx, cy], size=[2 * radius, 2 * radius], angle=0,
                              owner=None, plated=False, id=fields[4]))
        elif kind == 'VIA':
            vias.append((index, fields))
        elif kind in GRAPHIC_LAYER or kind == 'SVGNODE':
            layer = _layer_of(kind, fields)
            if kind == 'SVGNODE' and layer != '19':
                _unsupported(f'SVGNODE on layer {layer!r} is not a proven 3D-model record')
            if layer in SURFACE or (layer is not None and layer.isdigit() and 21 <= int(layer) <= 52):
                _unsupported(f'{kind} record #{index} is conductive copper outside a pad')
            if layer not in NONCOPPER:
                _unsupported(f'{kind} record #{index} has unqualified layer {layer!r}', 'unsupported_layer')
            key = kind + '@' + NONCOPPER[layer]
            ignored[key] = ignored.get(key, 0) + 1
        else:
            _unsupported(f'record #{index} has unsupported shape type {kind!r}: {raw[:80]!r}')
    if not pads:
        _unsupported('package has no conductive pads', 'empty_copper')
    _contained_vias(vias, pads, holes, locators)

    def local(point):
        return [text((point[0] - origin[0]) * UNIT_NM), text((point[1] - origin[1]) * UNIT_NM)]

    def primitive(p):
        out = dict(shape=p['shape'], center=local(p['center']),
                   size=[text(v * UNIT_NM) for v in p['size']], angle=str(p['angle']))
        if p['shape'] == 'polygon':
            out['points'] = [local(point) for point in p['points']]
        return out
    pattern = dict(pads=[dict(number=p['number'], layers=p['layers'], primitive=primitive(p['primitive']))
                         for p in pads],
                   holes=[primitive(h) for h in holes])
    hole_locators = [dict(owner=h['owner'], plated=h['plated'], id=h.get('id')) for h in holes]
    inventory = dict(records=len(shapes), pads=len(pads), holes=len(holes),
                     ignored_noncopper=dict(sorted(ignored.items())))
    if vias:
        inventory['vias'] = len(vias)
    return dict(identity=record, pattern=pattern, pad_locators=locators, hole_locators=hole_locators,
                inventory=inventory, tolerance_nm=text(tolerance_nm), units='nm', frame='easyeda-origin/y-down')


def symbol_pins(data, lcsc):
    """Symbol pin records of the same captured component: number, displayed number and name.

    A ``P`` record is seven ``^^`` segments; the settings segment carries the
    pin number (field 3), the name segment its text (field 4) and the number
    segment the displayed number (field 4). Records are returned verbatim, not
    reconciled: a caller that relies on one pin must require its number and
    displayed number to agree. Other symbol records are not interpreted.
    """
    component = strict_json(data)
    record, _, _ = identity(component, lcsc)
    shapes = component['dataStr'].get('shape', [])
    if not isinstance(shapes, list) or len(shapes) > MAX_RECORDS:
        _malformed('symbol shape list required (bounded)')
    pins = []
    for index, raw in enumerate(shapes):
        if not isinstance(raw, str):
            _malformed(f'symbol shape #{index} is not a text record')
        if not raw.startswith('P~'):
            continue
        segments = raw.split('^^')
        if len(segments) != 7:
            _malformed(f'symbol pin #{index} has {len(segments)} segments, not 7')
        settings, name, shown = (segments[i].split('~') for i in (0, 3, 4))
        if len(settings) != 9 or len(name) < 5 or len(shown) < 5 or not settings[3]:
            _malformed(f'symbol pin #{index} lacks its number, name or displayed number')
        pins.append(dict(index=index, number=settings[3], displayed=shown[4], name=name[4]))
    return dict(identity=record, pins=pins)

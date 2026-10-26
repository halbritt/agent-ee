"""Supplier land pattern versus KiCad footprint: copper/hole IoU and pin agreement.

Pure offline evaluation over captured bytes. The supplier side is a captured
EasyEDA component (``easyeda_footprint``); the native side is either an exact
member of a complete captured footprint-library manifest or the footprint
instance of one reference on the candidate board. Geometry comparison is
delegated to ``land_geometry`` (exact rational polygons with certified curve
bounds). Registration, thresholds, tolerances and the reference list are
immutable criteria; no rotation, scale or alias search is performed.
"""

from .check_registry import register as _register_check
import argparse
from fractions import Fraction
import hashlib
import re
from pathlib import Path, PurePosixPath

from .easyeda_footprint import FORMAT, SupplierError, decimal, parse_package, text
from .footprint_library import (FootprintError, _members, _object, load_document, read_captures)
from .storage import canonical, identity as value_digest


SCHEMA = 'agent-ee.land-pattern-criteria/1'
REPORT = 'agent-ee.land-pattern/1'
PROVENANCE = 'agent-ee.supplier-footprint/1'
VERSION = 'agent-ee.land-pattern-adapter/1'
PREDICATE = 'land_pattern:copper-hole-iou-pin-agreement/1'
TRANSLATIONS = ('declared_origin', 'copper_bbox_center')
MM = Fraction(1000000)
# Pad children that cannot change copper or drill geometry. Anything else refuses.
PAD_CHILDREN = frozenset(('at', 'size', 'drill', 'layers', 'roundrect_rratio', 'uuid', 'tstamp', 'net',
                          'pinfunction', 'pintype', 'solder_mask_margin', 'solder_paste_margin',
                          'solder_paste_margin_ratio', 'clearance', 'zone_connect', 'thermal_width',
                          'thermal_gap', 'thermal_bridge_width', 'thermal_bridge_angle', 'die_length',
                          'property', 'remove_unused_layers', 'keep_end_layers', 'teardrops', 'locked'))
NONCOPPER_LAYERS = frozenset(('F.Mask', 'B.Mask', '*.Mask', 'F.Paste', 'B.Paste', '*.Paste'))
_HEX = re.compile('[0-9a-f]{64}\\Z')


class PatternError(ValueError):
    def __init__(self, mode, message, execution):
        super().__init__(message)
        self.mode = mode
        self.execution = execution


def _refuse(mode, message, execution='unsupported'):
    raise PatternError(mode, message, execution)


def _sha(value):
    return isinstance(value, str) and bool(_HEX.match(value))


def _path(value, label):
    p = PurePosixPath(value) if isinstance(value, str) and value else None
    if p is None or p.is_absolute() or '..' in p.parts or str(p) != value or '\\' in value:
        raise ValueError(f'{label} requires a safe candidate-relative path')
    return value


def _decimal_text(value, label, positive=False, nonnegative=False):
    if not isinstance(value, str):
        raise ValueError(f'{label} must be decimal text')
    try:
        number = decimal(value, label)
    except SupplierError as exc:
        raise ValueError(str(exc)) from exc
    if (positive and number <= 0) or (nonnegative and number < 0):
        raise ValueError(f'{label} out of range')
    return number


def validate_criteria(criteria):
    """Every field is required; unknown fields refuse. Returns referenced paths."""
    _object(criteria, ('schema', 'threshold', 'pin_tolerance_nm', 'source_tolerance_nm',
                       'refinement_levels', 'library_manifest', 'requirements'))
    if criteria['schema'] != SCHEMA:
        raise ValueError('unsupported land-pattern criteria schema')
    threshold = _decimal_text(criteria['threshold'], 'threshold', positive=True)
    if threshold >= 1:
        raise ValueError('threshold must be below 1')
    _decimal_text(criteria['pin_tolerance_nm'], 'pin_tolerance_nm', nonnegative=True)
    _decimal_text(criteria['source_tolerance_nm'], 'source_tolerance_nm', nonnegative=True)
    levels = criteria['refinement_levels']
    if (not isinstance(levels, list) or not levels or any(type(v) is not int or v < 1 for v in levels)
            or any(b <= a or b % a for a, b in zip(levels, levels[1:])) or len(levels) > 32):
        raise ValueError('refinement_levels must be increasing nested positive integers')
    manifest = criteria['library_manifest']
    if manifest is not None:
        _path(manifest, 'library_manifest')
    requirements = criteria['requirements']
    if not isinstance(requirements, list) or not requirements:
        raise ValueError('nonempty requirement list required; zero references cannot pass')
    refs, supplier_paths, sources = set(), set(), set()
    for item in requirements:
        _object(item, ('reference', 'lcsc', 'supplier', 'native', 'registration', 'pin_aliases'))
        ref = item['reference']
        if not isinstance(ref, str) or not ref or ref in refs:
            raise ValueError('unique nonempty references required')
        refs.add(ref)
        if not isinstance(item['lcsc'], str) or not re.fullmatch('C[0-9]+', item['lcsc']):
            raise ValueError(f'{ref}: exact LCSC id required')
        if item['supplier'] is not None:
            _object(item['supplier'], ('provenance', 'sha256'))
            supplier_paths.add(_path(item['supplier']['provenance'], f'{ref} supplier provenance'))
            if not _sha(item['supplier']['sha256']):
                raise ValueError(f'{ref}: supplier provenance sha256 required')
        native = item['native']
        if not isinstance(native, dict) or native.get('source') not in ('library', 'board'):
            raise ValueError(f'{ref}: native source must be library or board')
        if native['source'] == 'library':
            _object(native, ('source', 'lib_id'))
            if not isinstance(native['lib_id'], str) or native['lib_id'].count(':') != 1:
                raise ValueError(f'{ref}: exact library:footprint identity required')
            if manifest is None:
                raise ValueError(f'{ref}: library source requires library_manifest')
        else:
            _object(native, ('source',))
        sources.add(native['source'])
        reg = item['registration']
        _object(reg, ('translation', 'rotation', 'mirror'))
        if reg['translation'] not in TRANSLATIONS or reg['mirror'] is not False:
            raise ValueError(f'{ref}: registration needs a declared translation rule and mirror false')
        if not isinstance(reg['rotation'], str) or reg['rotation'] not in ('0', '90', '180', '270'):
            raise ValueError(f'{ref}: registration rotation must be a declared quarter turn')
        aliases = item['pin_aliases']
        if aliases is not None:
            if (not isinstance(aliases, dict) or not aliases or
                    any(not isinstance(k, str) or not isinstance(v, str) or not k or not v
                        for k, v in aliases.items())
                    or len(set(aliases.values())) != len(aliases)):
                raise ValueError(f'{ref}: pin aliases must be an explicit injective string map')
    return dict(supplier_provenance=sorted(supplier_paths), sources=sorted(sources), manifest=manifest)


def validate_provenance(record, lcsc):
    _object(record, ('schema', 'lcsc', 'format', 'path', 'sha256', 'size_bytes', 'source_url',
                     'retrieved_at', 'retrieval'))
    if record['schema'] != PROVENANCE or record['format'] != FORMAT:
        raise ValueError('unsupported supplier provenance schema/format')
    if record['lcsc'] != lcsc:
        raise ValueError('provenance LCSC id differs from binding')
    _path(record['path'], 'supplier raw path')
    if not _sha(record['sha256']) or type(record['size_bytes']) is not int or record['size_bytes'] <= 0:
        raise ValueError('supplier raw sha256/size required')
    for key in ('source_url', 'retrieved_at', 'retrieval'):
        if not isinstance(record[key], str) or not record[key]:
            raise ValueError(f'provenance {key} required')
    return record


def dependencies(criteria, captures_or_reader):
    """Immutable input paths named by valid criteria and their provenance records."""
    meta = validate_criteria(criteria)
    paths = set(meta['supplier_provenance'])
    for item in criteria['requirements']:
        if item['supplier'] is not None:
            data = captures_or_reader(item['supplier']['provenance'])
            if hashlib.sha256(data).hexdigest() != item['supplier']['sha256']:
                raise ValueError(f"{item['reference']}: supplier provenance hash mismatch")
            paths.add(validate_provenance(_json(data), item['lcsc'])['path'])
    if meta['manifest'] is not None:
        manifest = _json(captures_or_reader(meta['manifest']))
        paths.add(meta['manifest'])
        paths.update(m['path'] for _, m in _members(manifest))
    return sorted(paths), meta


def _json(data):
    import json
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    def constant(token):
        raise ValueError('nonfinite JSON')
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=unique, parse_constant=constant)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError('invalid JSON: ' + str(exc)) from exc


# Native KiCad extraction ------------------------------------------------------

def _reader():
    from .symbol_library import parse_sexpr, children, LibraryError
    return parse_sexpr, children, LibraryError


def _mm(value, label):
    if not isinstance(value, str):
        _refuse('native_malformed', f'{label} must be a number token', 'malformed')
    try:
        return decimal(value, label) * MM
    except SupplierError as exc:
        _refuse('native_malformed', str(exc), 'malformed')


def _degrees(value, label):
    try:
        return decimal(value, label)
    except SupplierError as exc:
        _refuse('native_malformed', str(exc), 'malformed')


def _rotate(point, angle):
    """KiCad convention in +Y-down coordinates: (x, y) -> (x cos + y sin, -x sin + y cos)."""
    turn = angle % 360
    if turn % 90:
        _refuse('unsupported_rotation', f'exact transform for {text(angle)} degrees is not qualified')
    x, y = point
    return {0: (x, y), 90: (y, -x), 180: (-x, -y), 270: (-y, x)}[int(turn)]


def _one(node, key, children):
    found = children(node, key)
    if len(found) > 1:
        _refuse('native_malformed', f'duplicate {key}', 'malformed')
    return found[0] if found else None


def _copper(layers, label):
    surfaces = set()
    for layer in layers:
        if layer == '*.Cu':
            surfaces.update(('F.Cu', 'B.Cu'))
        elif layer in ('F.Cu', 'B.Cu'):
            surfaces.add(layer)
        elif layer not in NONCOPPER_LAYERS:
            _refuse('unsupported_layer', f'{label}: layer {layer!r} is not qualified')
    return sorted(surfaces)


def _native_pad(pad, children, label):
    if len(pad) < 4 or any(not isinstance(x, str) for x in pad[:4]):
        _refuse('native_malformed', label + ': invalid pad header', 'malformed')
    number, kind, shape = pad[1:4]
    label = f'{label} pad {number!r}'
    for item in pad[4:]:
        key = item[0] if isinstance(item, list) and item and isinstance(item[0], str) else item
        if isinstance(item, str) and item == 'locked':
            continue
        if not isinstance(item, list) or key not in PAD_CHILDREN:
            _refuse('unsupported_shape', f'{label}: geometry-affecting or unknown pad field {key!r}')
    if kind not in ('smd', 'thru_hole', 'np_thru_hole'):
        _refuse('unsupported_shape', f'{label}: pad type {kind!r} is not qualified')
    if shape not in ('rect', 'circle', 'oval', 'roundrect'):
        _refuse('unsupported_shape', f'{label}: pad shape {shape!r} is not qualified')
    at = _one(pad, 'at', children)
    size = _one(pad, 'size', children)
    layers = _one(pad, 'layers', children)
    if at is None or len(at) not in (3, 4) or size is None or len(size) != 3 or layers is None or len(layers) < 2:
        _refuse('native_malformed', label + ': at/size/layers required', 'malformed')
    if any(not isinstance(x, str) for x in layers[1:]) or len(set(layers[1:])) != len(layers) - 1:
        _refuse('native_malformed', label + ': invalid layer list', 'malformed')
    pos = (_mm(at[1], label), _mm(at[2], label))
    angle = _degrees(at[3], label) if len(at) == 4 else Fraction(0)
    w, h = _mm(size[1], label), _mm(size[2], label)
    if w <= 0 or h <= 0:
        _refuse('native_malformed', label + ': nonpositive size', 'malformed')
    if shape == 'circle' and w != h:
        _refuse('native_malformed', label + ': circle with unequal size', 'malformed')
    ratio = _one(pad, 'roundrect_rratio', children)
    radius = None
    if shape == 'roundrect':
        if ratio is None or len(ratio) != 2:
            _refuse('native_malformed', label + ': roundrect requires roundrect_rratio', 'malformed')
        r = _degrees(ratio[1], label + ' roundrect_rratio')
        if not 0 <= r <= Fraction(1, 2):
            _refuse('native_malformed', label + ': roundrect_rratio outside [0, 0.5]', 'malformed')
        radius = r * min(w, h)
    elif ratio is not None:
        _refuse('unsupported_shape', label + ': roundrect_rratio on a non-roundrect pad')
    drill = _one(pad, 'drill', children)
    offset, hole = (Fraction(0), Fraction(0)), None
    if drill is not None:
        rest = drill[1:]
        found = [x for x in rest if isinstance(x, list)]
        values = [x for x in rest if not isinstance(x, list)]
        if any(x[0] != 'offset' or len(x) != 3 for x in found) or len(found) > 1:
            _refuse('unsupported_shape', label + ': unqualified drill field')
        if found:
            offset = (_mm(found[0][1], label), _mm(found[0][2], label))
        if values and values[0] == 'oval':
            if len(values) != 3:
                _refuse('native_malformed', label + ': oval drill needs two sizes', 'malformed')
            hole = ('oval', _mm(values[1], label), _mm(values[2], label))
        elif len(values) == 1:
            d = _mm(values[0], label)
            hole = ('circle', d, d)
        elif values:
            _refuse('native_malformed', label + ': unsupported drill form', 'malformed')
        if hole is not None and (hole[1] <= 0 or hole[2] <= 0):
            _refuse('native_malformed', label + ': nonpositive drill', 'malformed')
        if hole is not None and hole[0] == 'oval' and hole[1] == hole[2]:
            hole = ('circle', hole[1], hole[2])
    surfaces = _copper(layers[1:], label)
    if kind == 'smd':
        if hole is not None:
            _refuse('native_malformed', label + ': SMD pad with a drill', 'malformed')
        if len(surfaces) > 1:
            _refuse('unsupported_layer', label + ': SMD pad on both surfaces is not qualified')
    elif hole is None:
        _refuse('native_malformed', label + ': through pad requires a drill', 'malformed')
    elif kind == 'thru_hole' and surfaces != ['B.Cu', 'F.Cu']:
        _refuse('unsupported_layer', label + ': plated hole must carry both surface copper layers')
    return dict(number=number, kind=kind, shape=shape, position=pos, angle=angle, size=(w, h),
                radius=radius, offset=offset, hole=hole, surfaces=surfaces,
                uuid=(_one(pad, 'uuid', children) or [None, None])[1])


def _copper_graphic(node, children):
    for key in ('layer', 'layers'):
        for found in children(node, key):
            for layer in found[1:]:
                if isinstance(layer, str) and (layer.endswith('.Cu') or layer == '*.Cu'):
                    return True
    return False


def extract_footprint(node, *, side, frame_angle, label):
    """Library-frame pads/holes of one native footprint node (library member or board instance).

    For board instances ``frame_angle`` is the footprint orientation: KiCad
    stores pad positions in the footprint frame and pad angles as absolute
    angles. A back-side instance stores its pads mirrored in local Y with
    negated angles and swapped layers (pcbnew 10.0.7 save/reload control);
    that mirror is undone so the result is the top-view library frame.
    """
    parse_sexpr, children, LibraryError = _reader()
    pads, holes, locators, ignored = [], [], [], {}
    for item in node[2:]:
        if not isinstance(item, list) or not item:
            continue
        key = item[0]
        if key == 'pad':
            continue
        if key in ('zone', 'jumper_pad_groups', 'net_tie_pad_groups') or _copper_graphic(item, children):
            _refuse('unsupported_shape', f'{label}: conductive or pin-joining footprint item {key!r}')
        if key.startswith('fp_') or key in ('dimension', 'image', 'barcode'):
            ignored[key] = ignored.get(key, 0) + 1
    for pad in children(node, 'pad'):
        raw = _native_pad(pad, children, label)
        angle = raw['angle'] - frame_angle
        position, offset = raw['position'], raw['offset']
        copper_center = position
        if offset != (0, 0):
            dx, dy = _rotate(offset, angle)
            copper_center = (position[0] + dx, position[1] + dy)
        surfaces = raw['surfaces']
        if side == 'B.Cu':
            mirror = {'F.Cu': 'B.Cu', 'B.Cu': 'F.Cu'}
            position, copper_center = (position[0], -position[1]), (copper_center[0], -copper_center[1])
            angle = -angle
            surfaces = sorted(mirror[s] for s in surfaces)
        angle %= 360
        locator = dict(number=raw['number'], kind=raw['kind'], shape=raw['shape'], uuid=raw['uuid'])
        if raw['hole'] is not None:
            hshape, hw, hh = raw['hole']
            holes.append(dict(shape=hshape, center=[text(v) for v in position], size=[text(hw), text(hh)],
                              angle=text(angle)))
        if raw['kind'] == 'np_thru_hole':
            hshape, hw, hh = raw['hole']
            same = (raw['shape'] == hshape or (raw['shape'] == 'oval' and hshape == 'circle' and
                                                raw['size'][0] == raw['size'][1]))
            if raw['number'] or not same or raw['size'] != (hw, hh) or offset != (0, 0):
                _refuse('unsupported_shape', f'{label}: NPTH pad with copper annulus or number is not qualified')
            ignored['npth_without_copper'] = ignored.get('npth_without_copper', 0) + 1
            continue
        if not surfaces:
            if raw['kind'] != 'smd':
                _refuse('native_malformed', f'{label}: through pad without copper', 'malformed')
            ignored['paste_or_mask_only_pad'] = ignored.get('paste_or_mask_only_pad', 0) + 1
            continue
        if not raw['number']:
            _refuse('unsupported_shape', f'{label}: unnumbered copper pad is not qualified')
        primitive = dict(shape=raw['shape'], center=[text(v) for v in copper_center],
                         size=[text(v) for v in raw['size']], angle=text(angle))
        if raw['shape'] == 'roundrect':
            primitive['radius'] = text(raw['radius'])
        pads.append(dict(number=raw['number'], layers=surfaces, primitive=primitive))
        locators.append(locator)
    if not pads:
        _refuse('empty_copper', f'{label}: footprint has no conductive pads')
    return dict(pattern=dict(pads=pads, holes=holes), pad_locators=locators,
                inventory=dict(pads=len(pads), holes=len(holes), ignored=dict(sorted(ignored.items()))))


def library_footprint(manifest, captures, lib_id):
    """Exact member of a complete captured manifest, verified by hash and native name.

    The shared manifest validator owns closure/path rules. The selected member is
    then read with the shared S-expression reader and this module's strict pad
    extraction (the shared inventory index does not qualify drill offsets).
    """
    library, name = lib_id.split(':')
    try:
        members = [m for lib, m in _members(manifest) if lib == library and PurePosixPath(m['path']).stem == name]
    except (FootprintError, TypeError) as exc:
        _refuse('native_missing', 'footprint manifest: ' + str(exc))
    if len(members) != 1:
        _refuse('native_missing', f'library footprint {lib_id!r} is not exactly one captured manifest member')
    member = members[0]
    data = captures.get(member['path'])
    if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != member['sha256']:
        _refuse('native_missing', f'{member["path"]} is missing or differs from its manifest hash')
    parse_sexpr, children, LibraryError = _reader()
    try:
        if b'\x00' in data:
            raise LibraryError('NUL byte in native footprint')
        node = parse_sexpr(data)
    except LibraryError as exc:
        _refuse('native_malformed', f'{lib_id}: {exc}', 'malformed')
    if node[0] != 'footprint' or len(node) < 2 or node[1] != name:
        _refuse('native_malformed', f'{lib_id}: native footprint name differs from manifest member', 'malformed')
    layer = children(node, 'layer')
    if len(layer) != 1 or layer[0][1:] != ['F.Cu']:
        _refuse('unsupported_layer', f'{lib_id}: only front-side library footprints are qualified')
    result = extract_footprint(node, side='F.Cu', frame_angle=Fraction(0), label=lib_id)
    result['source'] = dict(kind='library', lib_id=lib_id, path=member['path'], sha256=member['sha256'],
                            manifest_sha256=value_digest(manifest))
    return result


def board_footprints(board_bytes):
    parse_sexpr, children, LibraryError = _reader()
    try:
        if b'\x00' in board_bytes:
            raise LibraryError('NUL byte in board')
        root = parse_sexpr(board_bytes)
    except LibraryError as exc:
        _refuse('native_malformed', 'board: ' + str(exc), 'malformed')
    if root[0] != 'kicad_pcb':
        _refuse('native_malformed', 'board root is not kicad_pcb', 'malformed')
    found = {}
    for fp in children(root, 'footprint'):
        refs = [p for p in children(fp, 'property') if len(p) >= 3 and p[1] == 'Reference']
        if len(refs) != 1 or not isinstance(refs[0][2], str):
            _refuse('native_malformed', 'board footprint without one Reference property', 'malformed')
        found.setdefault(refs[0][2], []).append(fp)
    return found


def board_footprint(found, reference, board_sha):
    parse_sexpr, children, LibraryError = _reader()
    nodes = found.get(reference, [])
    if len(nodes) != 1:
        _refuse('native_missing', f'board has {len(nodes)} footprints with reference {reference!r}')
    node = nodes[0]
    layer = children(node, 'layer')
    at = children(node, 'at')
    if len(layer) != 1 or len(layer[0]) != 2 or layer[0][1] not in ('F.Cu', 'B.Cu'):
        _refuse('unsupported_layer', f'{reference}: board footprint side is not qualified')
    if len(at) != 1 or len(at[0]) not in (3, 4):
        _refuse('native_malformed', f'{reference}: footprint placement required', 'malformed')
    angle = _degrees(at[0][3], reference) if len(at[0]) == 4 else Fraction(0)
    result = extract_footprint(node, side=layer[0][1], frame_angle=angle, label=reference)
    uuid = children(node, 'uuid')
    result['source'] = dict(kind='board', fpid=node[1] if len(node) > 1 and isinstance(node[1], str) else None,
                            side=layer[0][1], orientation=text(angle), sha256=board_sha,
                            uuid=uuid[0][1] if uuid and len(uuid[0]) == 2 else None)
    return result


# Registration and comparison ---------------------------------------------------

def _bbox_center(pattern):
    xs, ys = [], []
    for pad in pattern['pads']:
        p = pad['primitive']
        turn = Fraction(p['angle']) % 360
        if turn % 90:
            _refuse('unsupported_rotation', 'copper bbox centring needs quarter-turn primitives')
        w, h = (Fraction(v) for v in p['size'])
        if turn in (90, 270):
            w, h = h, w
        cx, cy = (Fraction(v) for v in p['center'])
        xs += [cx - w / 2, cx + w / 2]
        ys += [cy - h / 2, cy + h / 2]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def _transform(pattern, shift, rotation):
    def point(raw):
        x, y = _rotate((Fraction(raw[0]) - shift[0], Fraction(raw[1]) - shift[1]), rotation)
        return [text(x), text(y)]
    def primitive(p):
        out = dict(p, center=point(p['center']), angle=text((Fraction(p['angle']) + rotation) % 360))
        return out
    return dict(pads=[dict(pad, primitive=primitive(pad['primitive'])) for pad in pattern['pads']],
                holes=[primitive(h) for h in pattern['holes']])


def register(native, supplier, registration):
    """Apply the declared rule: supplier rotation, then the translation datum. No search."""
    rotation = Fraction(registration['rotation'])
    supplier = _transform(supplier, (0, 0), rotation)
    if registration['translation'] == 'copper_bbox_center':
        native = _transform(native, _bbox_center(native), Fraction(0))
        supplier = _transform(supplier, _bbox_center(supplier), Fraction(0))
    return native, supplier


def rational(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    value = Fraction(value)
    return dict(numerator=value.numerator, denominator=value.denominator)


def _present(bound):
    """Lower rounded down / upper rounded up to 6 decimals; presentation only."""
    if bound is None or bound.get('lower') is None:
        return None
    lo = Fraction(bound['lower']['numerator'], bound['lower']['denominator'])
    hi = Fraction(bound['upper']['numerator'], bound['upper']['denominator'])
    scale = 10 ** 6
    down = (lo.numerator * scale) // lo.denominator
    up = -((-hi.numerator * scale) // hi.denominator)
    return dict(lower=text(Fraction(down, scale)), upper=text(Fraction(up, scale)))


def compare(native, supplier, item, criteria):
    from . import land_geometry as kernel
    left, right = register(native['pattern'], supplier['pattern'], item['registration'])
    kwargs = dict(threshold=criteria['threshold'], pin_tolerance=criteria['pin_tolerance_nm'],
                  levels=tuple(criteria['refinement_levels']))
    try:
        result = kernel.compare_patterns(left, right, **kwargs)
        if item['pin_aliases'] is not None:
            result['pins'] = kernel.match_pins(left, right, tolerance=criteria['pin_tolerance_nm'],
                                               aliases=item['pin_aliases'])
    except kernel.GeometryError as exc:
        _refuse(exc.mode, str(exc), exc.execution)
    copper = {layer: dict(metric, presented=_present(metric)) for layer, metric in sorted(result['copper'].items())}
    holes = dict(result['holes'], presented=_present(result['holes']))
    decisions = [m['decision'] for m in copper.values()]
    if holes['decision'] != 'not_applicable':
        decisions.append(holes['decision'])
    pins = result['pins']
    numbered = dict(pins, pairs=[dict(native=native['pattern']['pads'][a]['number'],
                                      supplier=supplier['pattern']['pads'][b]['number'],
                                      native_index=a, supplier_index=b) for a, b in pins.get('pairs', [])])
    if any(d not in ('pass', 'defect', 'unknown') for d in decisions) or pins['mode'] not in (
            'pin_agreement', 'pin_conflict', 'pin_mapping_unavailable', 'alias_conflict'):
        _refuse('kernel_malformed', 'unrecognized kernel decision or pin mode', 'failed')
    modes = ['low_accuracy'] if 'defect' in decisions else []
    if pins['mode'] != 'pin_agreement':
        modes.append(pins['mode'])
    if 'unknown' in decisions:
        modes.append('numeric_uncertainty')
    if 'low_accuracy' in modes or pins['mode'] in ('pin_conflict', 'alias_conflict'):
        outcome = 'defect'
    elif modes:
        outcome = 'unknown'
    else:
        outcome, modes = 'pass', ['match']
    return dict(outcome=outcome, mode=modes[0], modes=modes, copper=copper, holes=holes, pins=numbered,
                refinement=result['refinement'], algorithm=result['algorithm'],
                registered=dict(native=left, supplier=right))


def evaluate(criteria, captures, board_path=None):
    """Return the sealed report. ``captures`` maps candidate-relative paths to bytes."""
    report = dict(schema=REPORT, adapter=VERSION, execution='ok', outcome='pass', coverage='complete',
                  criteria_sha256=value_digest(criteria), references=[],
                  counts=dict(eligible=0, evaluated=0, passed=0, defect=0, unknown=0, unsupported=0, malformed=0,
                              failed=0))
    try:
        meta = validate_criteria(criteria)
    except (ValueError, TypeError) as exc:
        report.update(execution='malformed', outcome='unknown', coverage='missing', message=str(exc))
        return _seal(report)
    board = None
    for item in criteria['requirements']:
        record = dict(reference=item['reference'], lcsc=item['lcsc'], native_request=item['native'],
                      registration=item['registration'], pin_aliases=item['pin_aliases'])
        report['references'].append(record)
        report['counts']['eligible'] += 1
        try:
            if item['supplier'] is None:
                _refuse('supplier_missing', 'no captured supplier footprint is bound to this reference')
            prov_bytes = _capture(captures, item['supplier']['provenance'])
            if hashlib.sha256(prov_bytes).hexdigest() != item['supplier']['sha256']:
                _refuse('supplier_identity', 'supplier provenance hash mismatch', 'malformed')
            try:
                provenance = validate_provenance(_json(prov_bytes), item['lcsc'])
            except (ValueError, TypeError) as exc:
                _refuse('supplier_identity', str(exc), 'malformed')
            raw = _capture(captures, provenance['path'])
            if hashlib.sha256(raw).hexdigest() != provenance['sha256'] or len(raw) != provenance['size_bytes']:
                _refuse('supplier_identity', 'supplier raw bytes differ from provenance pin', 'malformed')
            supplier = parse_package(raw, item['lcsc'], Fraction(criteria['source_tolerance_nm']))
            record['supplier'] = dict(provenance=item['supplier']['provenance'],
                                      provenance_sha256=item['supplier']['sha256'], raw_path=provenance['path'],
                                      raw_sha256=provenance['sha256'], source_url=provenance['source_url'],
                                      retrieved_at=provenance['retrieved_at'], identity=supplier['identity'],
                                      inventory=supplier['inventory'], pads=supplier['pad_locators'],
                                      holes=supplier['hole_locators'])
            if item['native']['source'] == 'library':
                try:
                    manifest = _json(_capture(captures, meta['manifest']))
                    native = library_footprint(manifest, captures, item['native']['lib_id'])
                except (FootprintError, ValueError, TypeError) as exc:
                    if isinstance(exc, PatternError):
                        raise
                    _refuse('native_missing', str(exc))
            else:
                if board_path is None:
                    _refuse('native_missing', 'board-instance comparison requires the candidate board')
                if board is None:
                    data = _capture(captures, board_path)
                    board = (board_footprints(data), hashlib.sha256(data).hexdigest())
                native = board_footprint(board[0], item['reference'], board[1])
                native['source']['path'] = board_path
            record['native'] = dict(native['source'], inventory=native['inventory'], pads=native['pad_locators'])
            record.update(compare(native, supplier, item, criteria))
            record['execution'] = 'ok'
            report['counts']['evaluated'] += 1
        except SupplierError as exc:
            record.update(execution=exc.execution, outcome='unknown', mode=exc.mode, message=str(exc))
        except PatternError as exc:
            record.update(execution=exc.execution, outcome='unknown', mode=exc.mode, message=str(exc))
        except ImportError as exc:
            record.update(execution='failed', outcome='unknown', mode='kernel_unavailable', message=str(exc))
        except (ValueError, TypeError, KeyError) as exc:
            record.update(execution='failed', outcome='unknown', mode='adapter_error',
                          message=f'{type(exc).__name__}: {exc}')
        bucket = {'pass': 'passed', 'defect': 'defect', 'unknown': 'unknown'}[record['outcome']]
        if record['execution'] != 'ok':
            bucket = record['execution']
        report['counts'][bucket] += 1
        if record['outcome'] == 'defect':
            record['witness'] = _witness(record, criteria)
    executions = {r['execution'] for r in report['references']}
    outcomes = {r['outcome'] for r in report['references']}
    if executions != {'ok'}:
        report['coverage'] = 'incomplete'
    if 'defect' in outcomes:
        # A proven per-reference defect stands even when another reference lacks coverage.
        report.update(execution='ok', outcome='defect')
    elif executions != {'ok'}:
        worst = next(e for e in ('failed', 'malformed', 'unsupported') if e in executions)
        report.update(execution=worst, outcome='unknown')
    elif 'unknown' in outcomes:
        report['outcome'] = 'unknown'
    return _seal(report)


def _capture(captures, path):
    data = captures.get(path)
    if not isinstance(data, bytes):
        _refuse('input_missing', f'{path} is not captured', 'failed')
    return data


def _witness(record, criteria):
    inputs = [dict(path=record['supplier']['provenance'], sha256=record['supplier']['provenance_sha256']),
              dict(path=record['supplier']['raw_path'], sha256=record['supplier']['raw_sha256']),
              dict(path=record['native']['path'], sha256=record['native']['sha256'])]
    body = {k: record[k] for k in ('reference', 'lcsc', 'modes', 'copper', 'holes', 'pins')}
    return dict(criteria_sha256=value_digest(criteria), inputs=inputs, result_sha256=value_digest(body))


def _seal(report):
    report['canonical_sha256'] = value_digest(report)
    return report


def replay(report, criteria, captures, board_path=None):
    """Public verification: recomputing from the same captured bytes must give the same seal."""
    if not isinstance(report, dict) or report.get('schema') != REPORT:
        return False
    body = {k: v for k, v in report.items() if k != 'canonical_sha256'}
    if value_digest(body) != report.get('canonical_sha256'):
        return False
    return evaluate(criteria, captures, board_path)['canonical_sha256'] == report['canonical_sha256']


# Coordinator adapter -----------------------------------------------------------

def capability(params, scope, files, inputs, cid):
    _object(params, ('criteria',))
    if inputs is None:
        raise ValueError('captured inputs required for land-pattern capability')
    criteria_path = _path(params['criteria'], 'criteria')
    if criteria_path not in files:
        raise ValueError('land-pattern criteria must be captured')
    criteria = load_document(Path(inputs) / criteria_path)
    def reader(path):
        if path not in files:
            raise ValueError(f'{path} must be captured')
        return (Path(inputs) / path).read_bytes()
    paths, meta = dependencies(criteria, reader)
    if any(p not in files for p in paths):
        raise ValueError('supplier footprints, provenance and library closure must be captured')
    requirements = scope.get('baseline', {}).get('requirements', [])
    mapped = [r for r in requirements if cid in r['checks']]
    if len(mapped) != 1 or mapped[0]['source']['path'] != criteria_path:
        raise ValueError('land-pattern check must map exactly one baseline requirement sourced from its criteria')
    if 'board' in meta['sources']:
        board = scope.get('board')
        if not isinstance(board, str) or board not in files:
            raise ValueError('board-instance comparison requires the captured candidate board')
    return dict(immutable_inputs=sorted({criteria_path, *paths}), predicates=[PREDICATE])


def check_land_pattern(ctx):
    from .checks import CheckError
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    argv = ['/usr/bin/python3', '-m', 'agent_ee.land_pattern', '/candidate/' + ctx.params['criteria'],
            '--captures', '/candidate', '--out', '/work/land-pattern.json']
    if isinstance(ctx.scope.get('board'), str):
        argv += ['--board', ctx.scope['board']]
    try:
        ctx.execute(argv, 120)
    finally:
        ctx.evidence('land-pattern.json')
    report = ctx.load_json('land-pattern.json', 'land pattern')
    if report.get('schema') != REPORT or report.get('execution') not in ('ok', 'unsupported', 'malformed', 'failed'):
        raise CheckError('malformed', 'land-pattern report has unexpected schema/execution')
    ctx.result.update(execution=report['execution'], outcome=report['outcome'],
                      summary='Captured supplier land pattern versus KiCad footprint copper/hole IoU and pin agreement')
    ctx.result['details'].update(report=report, immutable_inputs=descriptor['immutable_inputs'])
    ctx.result['location'] = ', '.join(r['reference'] + ':' + ','.join(r['modes'])
                                       for r in report['references'] if r['outcome'] == 'defect')
    ctx.result['limitations'].append(
        'Copper on outer surfaces and drilled apertures only; no mask/paste/courtyard/inner-layer comparison, '
        'datasheet conformance or assembly approval. Curved areas are certified bounds, not exact areas.')


class _Captures(dict):
    """Read explicit safe candidate paths on first use; unreadable paths stay absent."""

    def __init__(self, root):
        super().__init__()
        self.root = root

    def get(self, path, default=None):
        if path not in self:
            try:
                self[path] = read_captures(self.root, [path])[path]
            except (OSError, ValueError):
                return default
        return self[path]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('criteria')
    parser.add_argument('--captures', required=True)
    parser.add_argument('--board', help='candidate-relative board path for board-instance references')
    parser.add_argument('--out', required=True, help='new output JSON file')
    args = parser.parse_args(argv)
    try:
        report = evaluate(load_document(args.criteria), _Captures(args.captures), args.board)
    except (OSError, ValueError) as exc:
        report = _seal(dict(schema=REPORT, adapter=VERSION, execution='malformed', outcome='unknown',
                            coverage='missing', references=[], message=str(exc)))
    with Path(args.out).open('xb') as stream:
        stream.write(canonical(report) + b'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


def describe_land_pattern(ctx):
    descriptor = capability(ctx.params, ctx.scope, ctx.files, ctx.inputs, ctx.spec['id'])
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('land_pattern', describe_land_pattern, check_land_pattern)

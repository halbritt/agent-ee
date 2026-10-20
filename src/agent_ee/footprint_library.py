"""Offline footprint inventory and assignment over explicit captured bytes.

The complete manifest is an owner declaration of library closure, not discovery
of the host's KiCad installation. This module never opens a library path.
"""
import hashlib
import json
from pathlib import PurePosixPath


class FootprintError(ValueError):
    pass


def load_document(path):
    from pathlib import Path
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise FootprintError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_bytes(), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(FootprintError('non-finite JSON')))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _seal(result):
    result['canonical_sha256'] = digest(result)
    return result


def _object(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise FootprintError('expected fields: ' + ', '.join(keys))


def _text(value, empty=False):
    if not isinstance(value, str) or (not empty and not value) or '\x00' in value:
        raise FootprintError('expected string')
    return value


def _members(manifest):
    _object(manifest, ('schema', 'complete', 'libraries'))
    if manifest['schema'] != 'agent-ee.footprint-libraries/1' or manifest['complete'] is not True:
        raise FootprintError('complete versioned footprint manifest required')
    if not isinstance(manifest['libraries'], list) or not manifest['libraries']:
        raise FootprintError('nonempty libraries required')
    names, paths = set(), set()
    entries = []
    for lib in manifest['libraries']:
        _object(lib, ('name', 'members'))
        name = _text(lib['name'])
        if ':' in name or name in names:
            raise FootprintError('duplicate/invalid library name')
        names.add(name)
        if not isinstance(lib['members'], list):
            raise FootprintError('explicit member list required')
        for member in lib['members']:
            _object(member, ('path', 'sha256'))
            path = PurePosixPath(_text(member['path']))
            if (path.is_absolute() or '..' in path.parts or str(path) != member['path']
                    or '\\' in str(path) or path.suffix != '.kicad_mod' or str(path) in paths):
                raise FootprintError('duplicate/unsafe footprint path')
            sha = _text(member['sha256'])
            if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
                raise FootprintError('invalid sha256')
            paths.add(str(path))
            entries.append((name, member))
    if len(entries) > 10000:
        raise FootprintError('footprint member limit exceeded')
    return sorted(entries, key=lambda x: (x[0], x[1]['path']))


DECLARATIONS_SCHEMA = 'agent-ee.footprint-declarations/1'
DECLARED_SHAPES = ('custom', 'trapezoid')
# Native pad fabrication properties KiCad 10.0.7 leaves out of a footprint's likely
# assembly type (DRC footprint_type_mismatch); a plated via pad needs one to stay SMD.
THERMAL_VIA_PROPERTIES = ('pad_prop_heatsink', 'pad_prop_mechanical')


def thermal_via_property(pad):
    """The native thermal-via property of one parsed ``(pad ...)`` node, or None.

    The pad must carry exactly one ``(property ...)`` child naming one of
    THERMAL_VIA_PROPERTIES; none, another value or several are None.
    """
    props = [x[1:] for x in pad[4:] if isinstance(x, list) and x and x[0] == 'property']
    return props[0][0] if props in ([[p]] for p in THERMAL_VIA_PROPERTIES) else None


def _numbers(value):
    if not isinstance(value, list) or len(set(map(str, value))) != len(value):
        raise FootprintError('declared pad numbers must be a list of unique strings')
    return [_text(x, empty=True) for x in value]


def validate_declarations(declarations):
    """Owner-captured pad/assembly/pinless declarations; nothing is guessed from names.

    Returns ``(footprints, pinless)`` keyed by exact footprint and symbol identity.
    """
    _object(declarations, ('schema', 'footprints', 'pinless_symbols'))
    if declarations['schema'] != DECLARATIONS_SCHEMA:
        raise FootprintError('unsupported footprint declaration schema')
    if not isinstance(declarations['footprints'], list) or not isinstance(declarations['pinless_symbols'], list):
        raise FootprintError('footprint and pinless declaration lists required')
    footprints, pinless = {}, {}
    for entry in declarations['footprints']:
        _object(entry, ('footprint', 'mechanical_pads', 'thermal_via_pads', 'custom_pads', 'assembly', 'rationale'))
        lib_id = _text(entry['footprint'])
        if lib_id.count(':') != 1 or not all(lib_id.split(':')) or lib_id in footprints:
            raise FootprintError('unique exact library:footprint declaration required')
        mechanical, thermal, custom = (_numbers(entry[k]) for k in ('mechanical_pads', 'thermal_via_pads', 'custom_pads'))
        if set(mechanical) & set(thermal) or '' in thermal:
            raise FootprintError('thermal-via pads must be numbered and not declared mechanical')
        if entry['assembly'] not in (None, 'smd', 'through_hole'):
            raise FootprintError('declared assembly must be null, smd or through_hole')
        if not (mechanical or thermal or custom or entry['assembly']):
            raise FootprintError('footprint declaration declares nothing')
        _text(entry['rationale'])
        footprints[lib_id] = entry
    for entry in declarations['pinless_symbols']:
        _object(entry, ('lib_id', 'rationale'))
        lib_id = _text(entry['lib_id'])
        if lib_id.count(':') != 1 or not all(lib_id.split(':')) or lib_id in pinless:
            raise FootprintError('unique exact library:symbol pinless declaration required')
        _text(entry['rationale'])
        pinless[lib_id] = entry
    return footprints, pinless


def _apply_declaration(pads, attributes, entry):
    """Every declared number must change a pad's handling; a stale declaration is unknown."""
    if entry is None:
        return
    for number in entry['mechanical_pads']:
        if not any(p['number'] == number and p.get('declared') == 'mechanical' for p in pads):
            raise FootprintError(f'declared mechanical pad {number!r} matches no copper or numbered NPTH pad')
    for number in entry['thermal_via_pads']:
        types = {p['type'] for p in pads if p['number'] == number and p['role'] == 'electrical'}
        if types != {'smd', 'thru_hole'}:
            raise FootprintError(f'declared thermal-via pad {number!r} needs SMD and through-hole copper pads')
        for pad in pads:
            if pad['number'] == number and pad['type'] == 'thru_hole':
                if thermal_via_property(pad['native']) is None:
                    raise FootprintError(f'declared thermal-via pad {number!r} through-hole copper lacks native '
                                         'pad_prop_heatsink; KiCad then types the footprint through-hole '
                                         '(DRC footprint_type_mismatch)')
                pad['thermal_via'] = True
    for number in entry['custom_pads']:
        if not any(p['number'] == number and p.get('geometry') == 'declared_custom' for p in pads):
            raise FootprintError(f'declared custom pad {number!r} matches no custom/trapezoid pad')
    if entry['assembly'] is not None and set(attributes) & {'smd', 'through_hole'}:
        raise FootprintError('declared assembly contradicts or duplicates a native assembly attribute')

RULE_AREA_COPPER_LAYERS = ('F.Cu', 'B.Cu') + tuple(f'In{i}.Cu' for i in range(1, 31))
KEEPOUT_KEYS = ('tracks', 'vias', 'pads', 'copperpour', 'footprints')
FORBID_MAP = (
    ('footprints', 'footprints'),
    ('tracks', 'tracks'),
    ('vias', 'vias'),
    ('pads', 'pads'),
    ('copper_pours', 'copperpour'),
)


def _rule_area(zone):
    """Parse and qualify a footprint-owned keepout rule area ``(zone ...)`` node."""
    from .board_envelope import mm, simple_polygon
    from .symbol_library import LibraryError, children, field, nanometres
    if children(zone, 'net') or children(zone, 'net_name'):
        raise LibraryError('footprint copper zone unsupported; expected keepout rule area')
    layers_nodes = children(zone, 'layers')
    layer_nodes = children(zone, 'layer')
    if len(layers_nodes) + len(layer_nodes) != 1:
        raise LibraryError('footprint rule area requires explicit layers')
    raw_layers = (layers_nodes[0] if layers_nodes else layer_nodes[0])[1:]
    if not raw_layers or any(not isinstance(x, str) for x in raw_layers):
        raise LibraryError('footprint rule area requires explicit layers')
    if any(layer not in RULE_AREA_COPPER_LAYERS for layer in raw_layers) or len(set(raw_layers)) != len(raw_layers):
        raise LibraryError('unsupported/duplicate rule-area layer')
    keepouts = children(zone, 'keepout')
    if len(keepouts) != 1:
        raise LibraryError('footprint zone requires keepout rules')
    if any(not isinstance(c, list) or not c or c[0] not in KEEPOUT_KEYS for c in keepouts[0][1:]):
        raise LibraryError('unsupported keepout rule')
    keepout_map = {}
    for key in KEEPOUT_KEYS:
        val = field(keepouts[0], key, 1)[0]
        if val not in ('allowed', 'not_allowed'):
            raise LibraryError('invalid keepout rule value')
        keepout_map[key] = val
    forbid = [label for label, native_key in FORBID_MAP if keepout_map[native_key] == 'not_allowed']
    if not forbid:
        raise LibraryError('keepout rule area forbids nothing')
    polygons = children(zone, 'polygon')
    if len(polygons) != 1:
        raise LibraryError('footprint rule area requires exactly one polygon')
    pts = children(polygons[0], 'pts')
    if len(pts) != 1 or len(pts[0]) < 4:
        raise LibraryError('footprint rule area polygon requires at least 3 vertices')
    points_nm = []
    for item in pts[0][1:]:
        if not isinstance(item, list) or len(item) != 3 or item[0] != 'xy' or any(not isinstance(v, str) for v in item[1:]):
            raise LibraryError('unsupported rule-area polygon vertex')
        points_nm.append((nanometres(item[1]), nanometres(item[2])))
    try:
        simple_polygon(points_nm, 'footprint rule area')
    except ValueError as exc:
        raise LibraryError(str(exc)) from exc
    xs = [x for x, _ in points_nm]
    ys = [y for _, y in points_nm]
    return dict(
        uuid=field(zone, 'uuid', 1)[0] if children(zone, 'uuid') else '',
        name=field(zone, 'name', 1)[0] if children(zone, 'name') else '',
        layers=list(raw_layers),
        forbid=forbid,
        keepout=keepout_map,
        polygon_nm=[[x, y] for x, y in points_nm],
        polygon_mm=[[mm(x), mm(y)] for x, y in points_nm],
        bounds_mm=dict(min=[mm(min(xs)), mm(min(ys))], max=[mm(max(xs)), mm(max(ys))]),
    )


def _custom_pad(pad):
    """Qualify bounded custom-pad syntax before native loading."""
    from .symbol_library import children, field, nanometres, LibraryError
    from .board_envelope import simple_polygon
    from math import isqrt
    # Native geometry can silently drop copper at large coordinates. Bound
    # both local extents and rotation-invariant reach before native loading.
    limit_nm = 1_000_000_000

    def ceil_hypot(point):
        squared = sum(v * v for v in point)
        root = isqrt(squared)
        return root + (root * root < squared)

    allowed = {'at', 'size', 'layers', 'uuid', 'options', 'primitives',
               'zone_connect', 'thermal_bridge_angle'}
    nodes = pad[4:]
    if any(not isinstance(n, list) or not n or n[0] not in allowed for n in nodes):
        raise LibraryError('unsupported custom-pad option')
    if len({n[0] for n in nodes}) != len(nodes):
        raise LibraryError('duplicate custom-pad option')
    if pad[2] != 'smd' or field(pad, 'zone_connect', 1) != ['2']:
        raise LibraryError('custom pads require explicit solid zone connection; thermal spokes unsupported')
    layers = children(pad, 'layers')
    copper = [layer for layer in layers[0][1:] if isinstance(layer, str) and layer.endswith('.Cu')] if len(layers) == 1 else []
    if copper not in (['F.Cu'], ['B.Cu']):
        raise LibraryError('custom pads require one explicit front/back copper layer')
    options = children(pad, 'options')
    if (len(options) != 1 or len(options[0]) != 3
            or sorted(n[0] for n in options[0][1:] if isinstance(n, list) and n) != ['anchor', 'clearance']):
        raise LibraryError('unsupported custom-pad options')
    if field(options[0], 'clearance', 1) != ['outline'] or field(options[0], 'anchor', 1)[0] not in ('rect', 'circle'):
        raise LibraryError('unsupported custom-pad clearance/anchor')
    if children(pad, 'thermal_bridge_angle'):
        nanometres(field(pad, 'thermal_bridge_angle', 1)[0])
    at = children(pad, 'at')
    if len(at) != 1 or len(at[0]) not in (3, 4):
        raise LibraryError('pad requires x/y/optional rotation')
    origin_reach = ceil_hypot(tuple(map(nanometres, at[0][1:3])))
    primitives = children(pad, 'primitives')
    if len(primitives) != 1 or not 1 <= len(primitives[0][1:]) <= 128:
        raise LibraryError('custom pad requires 1..128 supported primitives')
    for primitive in primitives[0][1:]:
        if not isinstance(primitive, list) or not primitive:
            raise LibraryError('invalid custom-pad primitive')
        kind = primitive[0]
        keys = {'gr_poly': {'pts', 'width', 'fill'},
                'gr_rect': {'start', 'end', 'width', 'fill'},
                'gr_circle': {'center', 'end', 'width', 'fill'},
                'gr_line': {'start', 'end', 'width'}}.get(kind)
        fields = primitive[1:]
        if (keys is None or any(not isinstance(n, list) or not n for n in fields)
                or len(fields) != len(keys) or {n[0] for n in fields} != keys):
            raise LibraryError('unsupported custom-pad primitive/options')
        width = nanometres(field(primitive, 'width', 1)[0])
        if width > limit_nm:
            raise LibraryError('custom-pad primitive width exceeds 1000 mm')
        fill = field(primitive, 'fill', 1)[0] if kind != 'gr_line' else 'no'
        if fill not in ('yes', 'no') or width < 0 or (fill == 'no' and width == 0):
            raise LibraryError('unsupported custom-pad primitive fill/width')
        if kind == 'gr_poly':
            pts = children(primitive, 'pts')[0][1:]
            if not 3 <= len(pts) <= 1024:
                raise LibraryError('custom polygon requires 3..1024 vertices')
            points = []
            for point in pts:
                if not isinstance(point, list) or len(point) != 3 or point[0] != 'xy':
                    raise LibraryError('unsupported custom polygon vertex')
                points.append(tuple(map(nanometres, point[1:])))
            try:
                simple_polygon(points, 'custom pad')
            except ValueError as exc:
                raise LibraryError(str(exc)) from exc
        else:
            start = tuple(map(nanometres, field(primitive, 'center' if kind == 'gr_circle' else 'start', 2)))
            end = tuple(map(nanometres, field(primitive, 'end', 2)))
            if start == end or (kind == 'gr_rect' and (start[0] == end[0] or start[1] == end[1])):
                raise LibraryError('degenerate custom-pad primitive')
            points = [start, end]
        if any(abs(v) > limit_nm for point in points for v in point):
            raise LibraryError('custom-pad primitive coordinate exceeds +/-1000 mm')
        if kind == 'gr_circle':
            radius = ceil_hypot(tuple(a - b for a, b in zip(start, end)))
            extent = max(abs(v) + radius for v in start)
            reach = ceil_hypot(start) + radius
        else:
            extent = max(abs(v) for point in points for v in point)
            corners = [(x, y) for x in (start[0], end[0]) for y in (start[1], end[1])] if kind == 'gr_rect' else points
            reach = max(map(ceil_hypot, corners))
        if 2 * extent + width > 2 * limit_nm:
            raise LibraryError('custom-pad primitive copper extent exceeds +/-1000 mm')
        # Double the comparison to preserve half-nanometre stroke expansion.
        if 2 * (origin_reach + reach) + width > 2 * limit_nm:
            raise LibraryError('custom-pad copper reach exceeds 1000 mm from footprint origin')


def canonical_custom_pad(pad):
    """Qualify AGENTEE-300 syntax and normalize native rectangle corner ordering.

    Board electrical fields are retained but do not participate in library
    geometry qualification. Other pad fields and primitive ordering stay exact.
    """
    import copy
    from decimal import Decimal
    from .symbol_library import children
    syntax = pad[:4] + [n for n in pad[4:] if n[0] not in ('net', 'pinfunction', 'pintype')]
    _custom_pad(syntax)
    result = copy.deepcopy(pad)
    for primitive in children(result, 'primitives')[0][1:]:
        if primitive[0] == 'gr_rect':
            start = children(primitive, 'start')[0]
            end = children(primitive, 'end')[0]
            for axis in (1, 2):
                start[axis], end[axis] = sorted((start[axis], end[axis]), key=Decimal)
    return result


def _native_custom_geometry(data, footprint):
    """Load exact captured bytes through the ordinary networkless executor."""
    import tempfile
    from pathlib import Path
    from collections import Counter
    from .executor import execute
    from .symbol_library import children, field
    def identity(pad):
        return (pad['number'], pad['type'], pad['shape'], pad['x_nm'], pad['y_nm'],
                pad['orientation_microdegrees'], tuple(pad['size_nm']))
    try:
        with tempfile.TemporaryDirectory(prefix='agent-ee-custom-pad-') as tmp:
            root = Path(tmp)
            inputs = root / 'inputs'
            inputs.mkdir()
            (inputs / (footprint['name'] + '.kicad_mod')).write_bytes(data)
            execution = execute(inputs, root / 'work', ['/usr/bin/python3', '-B', '-m',
                'agent_ee.footprint_probe', footprint['name']], timeout=60)
            if execution['execution'] != 'ok':
                console = (root / 'work' / 'console.log').read_text(errors='replace')[-2000:]
                raise FootprintError('native custom-pad load failed: ' + console)
            report = json.loads((root / 'work' / 'footprint.json').read_bytes())
            native = report['pads']
            if Counter(map(identity, native)) != Counter(map(identity, footprint['pads'])):
                raise FootprintError('native pad identity differs from captured footprint')
            for pad in footprint['pads']:
                if pad.get('geometry') != 'native_custom':
                    continue
                matches = [p for p in native if identity(p) == identity(pad)]
                if len(matches) != 1:
                    raise FootprintError('ambiguous native custom-pad identity')
                found = matches[0]
                anchor = field(children(pad['native'], 'options')[0], 'anchor', 1)[0]
                if found['anchor'] != anchor:
                    raise FootprintError('native custom-pad anchor differs from captured footprint')
                outlines = found['outline_count']
                if type(outlines) is not int or outlines != 1:
                    raise FootprintError(f'native custom pad {pad["number"]!r} has {outlines!r} outlines; '
                                         'exactly one merged copper outline required')
                bbox = found['bbox_nm']
                if (len(bbox) != 4 or any(type(v) is not int for v in bbox)
                        or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]):
                    raise FootprintError('native custom-pad copper extent missing/empty')
                pad['number'] = found['number']
                pad['native_geometry'] = dict(pcbnew_version=report['pcbnew_version'],
                                              number=found['number'], anchor=found['anchor'], bbox_nm=bbox)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FootprintError('native custom-pad evidence unavailable: ' + str(exc)) from exc


def _footprint(data, library, source, declared=None):
    # Shared reader owned by AGENTEE-39; no competing S-expression parser.
    try:
        from .symbol_library import parse_sexpr, children, field, nanometres, LibraryError
    except ImportError as exc:
        raise FootprintError("shared generic S-expression reader unavailable: " + str(exc)) from exc
    try:
        if b'\x00' in data:
            raise LibraryError('NUL byte in native footprint')
        node = parse_sexpr(data)
        if len(node) < 2 or node[0] != 'footprint' or not isinstance(node[1], str):
            raise LibraryError('expected native footprint')
        if node[1] != PurePosixPath(source['path']).stem:
            raise LibraryError('footprint name differs from manifest filename')
        attrs = children(node, 'attr')
        if len(attrs) > 1 or (attrs and any(not isinstance(x, str) for x in attrs[0])):
            raise LibraryError('invalid footprint attributes')
        if field(node, 'layer', 1)[0] not in ('F.Cu', 'B.Cu'):
            raise LibraryError('unqualified footprint side')
        attr = attrs[0][1:] if attrs else []
        entry = (declared or {}).get(library + ':' + node[1])
        mechanical = set(entry['mechanical_pads']) if entry else set()
        custom = set(entry['custom_pads']) if entry else set()
        metadata = {}
        for key in ('descr', 'tags'):
            metadata[key] = field(node, key, 1)[0] if children(node, key) else ''
        pads = []
        for pad in children(node, 'pad'):
            if len(pad) < 4 or any(not isinstance(x, str) for x in pad[:4]):
                raise LibraryError('invalid pad header')
            number, kind, shape = pad[1:4]
            # Declared custom geometry is accepted for numbering only, never qualified.
            geometry = 'declared_custom' if number in custom and (
                shape in DECLARED_SHAPES or any(children(pad, x) for x in ('primitives', 'options'))) else None
            if shape == 'custom' and geometry is None:
                _custom_pad(pad)
                geometry = 'native_custom'
            if kind not in ('smd', 'thru_hole', 'np_thru_hole') or (
                    shape not in ('rect', 'circle', 'oval', 'roundrect') and geometry is None):
                raise LibraryError('unsupported native pad type/geometry')
            if children(pad, 'padstack') or (geometry is None and any(children(pad, x) for x in ('primitives', 'options'))):
                raise LibraryError('unsupported custom padstack')
            layers = children(pad, 'layers')
            if len(layers) != 1 or len(layers[0]) < 2 or any(not isinstance(x, str) for x in layers[0][1:]):
                raise LibraryError('pad requires explicit layers')
            layers = sorted(layers[0][1:])
            known_layers = {'F.Cu', 'B.Cu', '*.Cu', 'F.Paste', 'B.Paste', 'F.Mask', 'B.Mask', '*.Mask'} | {f'In{i}.Cu' for i in range(1, 31)}
            if any(layer not in known_layers for layer in layers) or len(set(layers)) != len(layers):
                raise LibraryError('unsupported/duplicate pad layer')
            copper = any(x.endswith('.Cu') for x in layers)
            at = children(pad, 'at')
            if len(at) != 1 or len(at[0]) not in (3, 4):
                raise LibraryError('pad requires x/y/optional rotation')
            x, y = map(nanometres, at[0][1:3])
            angle = nanometres(at[0][3]) if len(at[0]) == 4 else 0
            size = list(map(nanometres, field(pad, 'size', 2)))
            if any(v <= 0 for v in size):
                raise LibraryError('nonpositive pad size')
            drill = children(pad, 'drill')
            if kind in ('thru_hole', 'np_thru_hole'):
                if len(drill) != 1 or len(drill[0]) not in (2, 4):
                    raise LibraryError('through pad requires qualified drill')
                values = drill[0][1:]
                if values[0] == 'oval':
                    values = values[1:]
                    if len(values) != 2:
                        raise LibraryError('oval drill requires two dimensions')
                elif len(values) != 1:
                    raise LibraryError('unsupported drill form')
                if any(nanometres(v) <= 0 for v in values):
                    raise LibraryError('nonpositive drill')
            elif drill:
                raise LibraryError('SMD drill unsupported')
            extra = {}
            if kind == 'np_thru_hole':
                if number and number not in mechanical:
                    raise LibraryError('numbered mechanical pad unsupported')
                role = 'mechanical'
                if number:
                    extra['declared'] = 'mechanical'
            elif not copper:
                if kind != 'smd' or not layers or any(not x.endswith('.Paste') for x in layers):
                    raise LibraryError('noncopper pad is not qualified paste-only')
                role = 'paste'
            elif number in mechanical:
                role, extra['declared'] = 'mechanical', 'mechanical'
            else:
                if not number:
                    raise LibraryError('unnumbered copper pad unsupported')
                role = 'electrical'
            if geometry:
                extra['geometry'] = geometry
            pads.append(dict(number=number, type=kind, shape=shape, layers=layers,
                             x_nm=x, y_nm=y, orientation_microdegrees=angle,
                             size_nm=size, drill=drill[0][1:] if drill else None, role=role, native=pad, **extra))
        if not pads:
            raise LibraryError('footprint contains no pads')
        rule_areas = [_rule_area(zone) for zone in children(node, 'zone')]
        _apply_declaration(pads, attr, entry)
        result = dict(lib_id=library + ':' + node[1], name=node[1], description=metadata['descr'],
                      tags=metadata['tags'], attributes=sorted(attr), pads=sorted(pads, key=canonical),
                      electrical_numbers=sorted({p['number'] for p in pads if p['role'] == 'electrical'}),
                      source=dict(source))
        if rule_areas:
            result['rule_areas'] = rule_areas
        if entry is not None:
            result['declaration'] = entry
        if any(p.get('geometry') == 'native_custom' for p in pads):
            _native_custom_geometry(data, result)
            result['electrical_numbers'] = sorted({p['number'] for p in pads if p['role'] == 'electrical'})
        return result
    except (LibraryError, TypeError) as exc:
        raise FootprintError(str(exc)) from exc


def index_footprints(manifest, captured_files, declarations=None):
    """Validate every declared member before any selected-name absence claim.

    Optional ``declarations`` are captured owner pad/assembly declarations
    (``validate_declarations``); these never qualify geometry. Explicitly solid
    custom SMD pads in the supported subset require a sandboxed native load.
    """
    result = dict(schema='agent-ee.footprint-index/1', execution='ok', outcome='info',
                  coverage='complete', footprints=[], findings=[], manifest_sha256=None)
    if declarations is not None:
        result['declarations_sha256'] = digest(declarations)
    code = 'manifest'
    try:
        entries = _members(manifest)
        result['manifest_sha256'] = digest(manifest)
        code = 'declaration'
        declared = validate_declarations(declarations)[0] if declarations is not None else {}
    except (FootprintError, TypeError, ValueError) as exc:
        result.update(execution='malformed', outcome='unknown', coverage='missing')
        result['findings'].append(dict(code=code, message=str(exc)))
        return _seal(result)
    total_bytes = 0
    for library, member in entries:
        try:
            data = captured_files.get(member['path'])
            if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != member['sha256']:
                raise FootprintError('missing/unreadable/hash-mismatched captured footprint')
            total_bytes += len(data)
            if total_bytes > 64 * 1024 * 1024:
                raise FootprintError('footprint inventory exceeds 64 MiB byte limit')
            footprint = _footprint(data, library, member, declared)
            if any(x['lib_id'] == footprint['lib_id'] for x in result['footprints']):
                raise FootprintError('duplicate exact footprint identity')
            result['footprints'].append(footprint)
        except FootprintError as exc:
            result.update(execution='failed', outcome='unknown', coverage='missing')
            result['findings'].append(dict(code='library_coverage', path=member['path'], message=str(exc)))
    names = {library + ':' + PurePosixPath(member['path']).stem for library, member in entries}
    for lib_id in sorted(set(declared) - names):
        result.update(execution='failed', outcome='unknown', coverage='missing')
        result['findings'].append(dict(code='declaration', footprint=lib_id,
                                       message='declared footprint is not a manifest member'))
    result['footprints'].sort(key=lambda x: x['lib_id'])
    return _seal(result)


def captured_symbol_libraries(libraries, captured_files, cache):
    """Yield each declared ``(name, path, sha256, symbols)`` after its safety and hash checks.

    Declarations are validated in order, so a consumer that stops early sees the
    same first error as one reading the whole list. Library bytes are parsed only
    through the shared reader's content-addressed cache.
    """
    from .symbol_library import LibraryError
    if not isinstance(libraries, list):
        raise FootprintError('explicit captured symbol libraries required')
    names, paths = set(), set()
    for lib in libraries:
        _object(lib, ('name', 'path', 'sha256'))
        name, path, sha = (lib[x] for x in ('name', 'path', 'sha256'))
        _text(name)
        _text(path)
        _text(sha)
        p = PurePosixPath(path)
        if (name in names or path in paths or p.is_absolute() or '..' in p.parts
                or str(p) != path or '\\' in path or p.suffix != '.kicad_sym'):
            raise FootprintError('ambiguous/unsafe symbol library declaration')
        names.add(name)
        paths.add(path)
        data = captured_files.get(path)
        if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != sha:
            raise FootprintError('missing/unreadable/hash-mismatched captured symbol library')
        try:
            if b'\x00' in data:
                raise LibraryError('NUL byte in native symbol library')
            symbols = cache.symbols(data)
        except LibraryError as exc:
            raise FootprintError(str(exc)) from exc
        yield name, path, sha, symbols


def _symbol_inventory(lib_id, libraries, captured_files, *, _library_cache=None):
    from .symbol_library import ParsedLibraries, resolve_symbol, LibraryError
    cache = _library_cache if _library_cache is not None else ParsedLibraries()
    if not isinstance(lib_id, str) or lib_id.count(':') != 1:
        raise FootprintError('exact library:symbol identity required')
    library, symbol = lib_id.split(':')
    if not library or not symbol or not isinstance(libraries, list):
        raise FootprintError('explicit captured symbol libraries required')
    selected = None
    for name, path, sha, symbols in captured_symbol_libraries(libraries, captured_files, cache):
        if name == library:
            if symbol not in symbols:
                raise FootprintError('selected symbol absent from captured library: ' + lib_id)
            try:
                selected = resolve_symbol(symbols, symbol, path, sha)
            except LibraryError as exc:
                raise FootprintError(str(exc)) from exc
    if selected is None:
        raise FootprintError('selected symbol library is not captured')
    return selected


def board_membership(sources, captured_files):
    from .symbol_library import parse_library, children, field, LibraryError
    on_board = True  # KiCad's default when no declaration is present.
    try:
        for source in sources:
            node = parse_library(captured_files[source['path']])[source['symbol']]
            if children(node, 'on_board'):
                value = field(node, 'on_board', 1)[0]
                if value not in ('yes', 'no'):
                    raise LibraryError('invalid native board-membership flag')
                on_board = value == 'yes'
    except LibraryError as exc:
        raise FootprintError(str(exc)) from exc
    return on_board


def validate_declaration(declaration):
    _object(declaration, ('schema', 'libraries', 'components'))
    if declaration['schema'] != 'agent-ee.footprint-assignments-input/1':
        raise FootprintError('unsupported assignment schema')
    if not isinstance(declaration['components'], list) or not declaration['components']:
        raise FootprintError('nonempty component declarations required')
    if not isinstance(declaration['libraries'], list) or not declaration['libraries']:
        raise FootprintError('nonempty symbol library list required')
    for lib in declaration['libraries']:
        _object(lib, ('name', 'path', 'sha256'))
        for value in lib.values():
            _text(value)
    refs = set()
    for comp in declaration['components']:
        _object(comp, ('reference', 'lib_id', 'footprint', 'assembly', 'connector'))
        ref = _text(comp['reference'])
        _text(comp['footprint'], empty=True)
        _text(comp['lib_id'])
        if comp['lib_id'].count(':') != 1 or not all(comp['lib_id'].split(':')):
            raise FootprintError('exact library:symbol identity required')
        if ref in refs or comp['assembly'] not in ('smd', 'through_hole', 'mixed'):
            raise FootprintError('unique references and explicit assembly intent required')
        refs.add(ref)
        conn = comp['connector']
        if conn is not None:
            _object(conn, ('rows', 'columns'))
            if any(type(v) is not int or v <= 0 for v in conn.values()):
                raise FootprintError('connector requires positive integer rows/columns')


def check_assignments(manifest, captured_files, declaration, native_membership=False, declarations=None):
    """Check declared physical components against full shared-resolver pin inventory.

    Declaration bytes must be captured by the coordinator before using this as
    evidence. All logical device units are compared, including unconnected pins.
    No schematic/intent reference-name inference or package choice is performed.
    With native_membership the caller has already established native board
    membership; captured power/on_board flags are recorded but exclude nothing.
    Optional captured ``declarations`` declare mechanical copper pads, thermal
    vias, custom pads, attr-less assembly and pinless symbols; anything
    undeclared keeps the default defect/unknown handling.
    """
    index = index_footprints(manifest, captured_files, declarations)
    result = dict(schema='agent-ee.footprint-assignments/1', execution=index['execution'],
                  outcome='pass' if index['execution'] == 'ok' else 'unknown',
                  coverage=index['coverage'], index=index, index_sha256=index['canonical_sha256'],
                  declaration_sha256=None, assignments=[], findings=list(index['findings']))
    if declarations is not None:
        result['declarations_sha256'] = digest(declarations)
    try:
        result['declaration_sha256'] = digest(declaration)
        validate_declaration(declaration)
        pinless = validate_declarations(declarations)[1] if declarations is not None else {}
    except (ValueError, TypeError) as exc:
        result.update(execution='malformed', outcome='unknown', coverage='missing')
        result['findings'].append(dict(code='declaration', message=str(exc)))
        return _seal(result)
    footprints = {x['lib_id']: x for x in index['footprints']}
    for comp in sorted(declaration['components'], key=lambda x: x['reference']):
        assignment = dict(reference=comp['reference'], lib_id=comp['lib_id'], footprint=comp['footprint'],
                          status='unknown', findings=[])
        result['assignments'].append(assignment)
        try:
            if comp['assembly'] == 'mixed':
                raise FootprintError('mixed assembly intent is not qualified')
            pins, sources, power = _symbol_inventory(comp['lib_id'], declaration['libraries'], captured_files)
            assignment['symbol_sources'] = sources
            assignment['pin_numbers'] = sorted({p['number'] for p in pins})
            on_board = board_membership(sources, captured_files)
            if native_membership:
                assignment.update(library_power=power, library_on_board=on_board)
                power, on_board = False, True
            assignment['on_board'] = on_board
            if power or not on_board:
                assignment.update(status='excluded', exclusion='pinned native power symbol' if power else 'pinned native on_board=no')
                continue
            if pins and comp['lib_id'] in pinless:
                raise FootprintError('pinless declaration contradicts the symbol pin inventory')
            if not pins and comp['lib_id'] not in pinless:
                raise FootprintError('physical symbol has no qualified pins')
            if not pins:
                assignment['pinless'] = pinless[comp['lib_id']]
            if index['coverage'] != 'complete':
                raise FootprintError('footprint index incomplete; absence/parity unavailable')
            if not comp['footprint']:
                assignment['findings'].append(dict(code='empty_footprint'))
            elif comp['footprint'] not in footprints:
                assignment['findings'].append(dict(code='absent_footprint'))
            else:
                fp = footprints[comp['footprint']]
                assignment['footprint_source'] = fp['source']
                if 'rule_areas' in fp:
                    assignment['rule_areas'] = fp['rule_areas']
                if 'declaration' in fp:
                    assignment['footprint_declaration'] = fp['declaration']
                    assignment['mechanical_pads'] = sorted({p['number'] for p in fp['pads'] if p.get('declared')})
                numbers = set(fp['electrical_numbers'])
                missing = sorted(set(assignment['pin_numbers']) - numbers)
                extra = sorted(numbers - set(assignment['pin_numbers']))
                if missing or extra:
                    assignment['findings'].append(dict(code='pad_pin_parity', missing_pads=missing, extra_pads=extra))
                if comp['connector'] is not None:
                    expected = comp['connector']['rows'] * comp['connector']['columns']
                    if len(numbers) != expected:
                        assignment['findings'].append(dict(code='connector_count', expected=expected, actual=len(numbers)))
                # Declared thermal vias are electrical but do not make an SMD part through-hole.
                types = {p['type'] for p in fp['pads'] if p['role'] == 'electrical' and not p.get('thermal_via')}
                attrs = set(fp['attributes']) & {'smd', 'through_hole'}
                declared_assembly = (fp.get('declaration') or {}).get('assembly')
                if declared_assembly is not None:
                    attrs = {declared_assembly}  # The index refuses one beside a native attribute.
                technology = {frozenset({'smd'}): 'smd', frozenset({'thru_hole'}): 'through_hole',
                              frozenset({'smd', 'thru_hole'}): 'mixed'}.get(frozenset(types))
                if not types and not pins and len(attrs) == 1:
                    technology = next(iter(attrs))  # Declared pinless: no electrical pads to compare.
                if technology is None or not attrs or (technology == 'smd' and attrs != {'smd'}) or (technology == 'through_hole' and attrs != {'through_hole'}):
                    raise FootprintError('ambiguous/missing native assembly attributes')
                if technology == 'mixed':
                    raise FootprintError('mixed assembly semantics are not qualified')
                if comp['assembly'] != technology:
                    assignment['findings'].append(dict(code='assembly_mismatch', expected=comp['assembly'], actual=technology))
            assignment['status'] = 'defect' if assignment['findings'] else 'pass'
        except FootprintError as exc:
            result.update(execution='failed', outcome='unknown', coverage='missing')
            assignment['findings'].append(dict(code='coverage', message=str(exc)))
        result['findings'].extend(dict(finding, reference=comp['reference'], footprint=comp['footprint'],
                                       lib_id=comp['lib_id']) for finding in assignment['findings'])
    if result['coverage'] == 'complete' and result['findings']:
        result['outcome'] = 'defect'
    if result['coverage'] == 'complete' and all(a['status'] == 'excluded' for a in result['assignments']):
        result['outcome'] = 'info'
    return _seal(result)


ESP32_FOOTPRINT_ID = 'RF_Module:ESP32-S3-WROOM-1'
ESP32_FOOTPRINT_PATH = 'RF_Module.pretty/ESP32-S3-WROOM-1.kicad_mod'
ESP32_FOOTPRINT_SHA256 = 'd0cfbb31fef0c47396c0d061ec5a3bff60f0567d8d4ea8dd4769c84f52bd656f'
RF_MODULE_SYMBOL_LIB_SHA256 = '5caaa848e888e9f5491edc765fef0e2eee53c4fe5aa4fe1520c01e4b0f840f2b'
ESP32_SYMBOL_SUBSET_SHA256 = 'ad427c46f8d046f47595edd16128520b75d9b386cc36a14e4d8f75ae036ab23d'
ESP32_DATASHEET_URL = 'https://www.espressif.com/sites/default/files/documentation/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf'
ESP32_DATASHEET_PATH = 'datasheets/dbdfd4ddc180acc1e28909cf69518d22af59605031728a9765a38ff746a1d9b8.pdf'
ESP32_DATASHEET_SHA256 = '27d71971da07c280c6068d08c74720d1a25b8f20cf8494dc1765bdd28d40d435'
ESP32_KEEPOUT_FACT_IDS = (
    'esp32_s3_wroom_1_body_width_mm',
    'esp32_s3_wroom_1_body_length_mm',
    'esp32_s3_wroom_1_antenna_depth_mm',
)


def probe_rule_areas(data, *, library='RF_Module', path=ESP32_FOOTPRINT_PATH, declared=None):
    """List footprint-owned rule area polygons, bounds, forbid flags and layers from captured bytes."""
    if not isinstance(data, bytes):
        raise FootprintError('captured footprint bytes required')
    lib_name = _text(library)
    rel_path = PurePosixPath(_text(path))
    if rel_path.is_absolute() or '..' in rel_path.parts or str(rel_path) != path or '\\' in path or rel_path.suffix != '.kicad_mod':
        raise FootprintError('unsafe footprint path')
    source = dict(path=path, sha256=hashlib.sha256(data).hexdigest())
    fp = _footprint(data, lib_name, source, declared)
    return _seal(dict(
        schema='agent-ee.footprint-rule-areas/1',
        lib_id=fp['lib_id'],
        name=fp['name'],
        source=fp['source'],
        rule_areas=list(fp.get('rule_areas', [])),
    ))


def esp32_s3_wroom_1_manifest():
    return dict(
        schema='agent-ee.footprint-libraries/1',
        complete=True,
        libraries=[dict(
            name='RF_Module',
            members=[dict(path=ESP32_FOOTPRINT_PATH, sha256=ESP32_FOOTPRINT_SHA256)],
        )],
    )


def esp32_s3_wroom_1_declarations():
    return dict(
        schema=DECLARATIONS_SCHEMA,
        footprints=[dict(
            footprint=ESP32_FOOTPRINT_ID,
            mechanical_pads=[],
            thermal_via_pads=['41'],
            custom_pads=[],
            assembly=None,
            rationale=('ESP32-S3-WROOM-1 exposed ground pad 41 has one F.Cu SMD pad and twelve 0.2 mm '
                       'thru-hole thermal vias sharing pad number 41; nine unnumbered F.Paste apertures '
                       'are paste-only.'),
        )],
        pinless_symbols=[],
    )


def esp32_s3_wroom_1_assignments(reference='U1', symbol_path='symbols/RF_Module.kicad_sym',
                                 symbol_sha256=ESP32_SYMBOL_SUBSET_SHA256):
    return dict(
        schema='agent-ee.footprint-assignments-input/1',
        libraries=[dict(name='RF_Module', path=symbol_path, sha256=symbol_sha256)],
        components=[dict(
            reference=reference,
            lib_id=ESP32_FOOTPRINT_ID,
            footprint=ESP32_FOOTPRINT_ID,
            assembly='smd',
            connector=None,
        )],
    )


def esp32_s3_wroom_1_datasheet_facts(*, pdf_path=ESP32_DATASHEET_PATH, pdf_sha256=ESP32_DATASHEET_SHA256):
    """Authentic module dimensions; Figure 11-1 gives no base-board clearance."""
    figure = 'Section 11.1 PCB Land Pattern, Figure 11-1. ESP32-S3-WROOM-1 Recommended PCB Land Pattern'
    return dict(
        schema='agent-ee.datasheet-facts/1',
        facts=[
            dict(
                id='esp32_s3_wroom_1_body_width_mm',
                part_number='ESP32-S3-WROOM-1',
                kind='keepout_dimension',
                value='18',
                unit='mm',
                conditions=f'{figure}: module body width 18 mm (x -9..9 mm relative to footprint origin)',
                source=dict(path=pdf_path, sha256=pdf_sha256, page=45,
                            locator=f'{figure} (module body width)', quote='18'),
            ),
            dict(
                id='esp32_s3_wroom_1_body_length_mm',
                part_number='ESP32-S3-WROOM-1',
                kind='keepout_dimension',
                value='25.5',
                unit='mm',
                conditions=f'{figure}: module body length 25.5 mm (y -12.75..12.75 mm relative to footprint origin)',
                source=dict(path=pdf_path, sha256=pdf_sha256, page=45,
                            locator=f'{figure} (module body length)', quote='25.5'),
            ),
            dict(
                id='esp32_s3_wroom_1_antenna_depth_mm',
                part_number='ESP32-S3-WROOM-1',
                kind='keepout_dimension',
                value='6',
                unit='mm',
                conditions=f'{figure}: on-board PCB Antenna Area depth 6 mm from top module edge (y -12.75..-6.75 mm)',
                source=dict(path=pdf_path, sha256=pdf_sha256, page=45,
                            locator=f'{figure} (Antenna Area depth)', quote='6'),
            ),

        ],
    )


def derive_antenna_keepout(facts, *, part_number='ESP32-S3-WROOM-1'):
    """Refuse the withdrawn blanket-clearance derivation, including old verified records."""
    entries = facts.get('facts') if isinstance(facts, dict) else facts
    if not isinstance(entries, list):
        raise FootprintError('datasheet facts list required')
    for entry in entries:
        fact = entry.get('fact', entry) if isinstance(entry, dict) else None
        if not isinstance(fact, dict):
            raise FootprintError('invalid datasheet fact entry')
        if fact.get('id') == 'esp32_s3_wroom_1_keepout_clearance_mm':
            raise FootprintError('withdrawn ESP32 clearance: Figure 11-1 labels pin 15, not a 15 mm '
                                 'base-board clearance; select a separately source-bound placement case')
    raise FootprintError('ESP32 antenna placement qualification unresolved: module dimensions do not '
                         'define a base-board minimum; outside-board and conditional cutout policies '
                         'need separate physical qualification; captured CAD geometry is not that proof')


ESP32_GUIDELINES_SHA256 = '057f6df63e10a2dfc155cf19d942adf5780127d839626e2beb4c1a61efeaf3a5'
ESP32_GUIDELINES_PATH = 'datasheets/esp32-s3-hardware-design-guidelines.pdf'
_ESP32_PLACEMENT_CASES = {
    'outside_board': (33, 'Section 1.4.7 RF: module antenna outside the base board',
        'Place the module PCB antenna beyond the base board with its feed point close to the edge; '
        'corner selection depends on feed-point side.'),
    'board_cutout': (34, 'Figure 26: conditional cutout, antenna feed point on the right',
        'When the antenna cannot extend beyond the edge, use the applicable base-board cutout case. '
        'Figure 26 has asymmetric Min15, Max2 and Max1 constraints; it is not a symmetric rectangle.'),
}


def esp32_s3_wroom_1_placement_policy(placement_case, *, pdf_path=ESP32_GUIDELINES_PATH):
    """Declare a case and exact reviewed source snapshot; this does not qualify placement."""
    placement_case = _text(placement_case)
    if placement_case not in _ESP32_PLACEMENT_CASES:
        raise FootprintError('unsupported ESP32 antenna placement case')
    page, locator, _ = _ESP32_PLACEMENT_CASES[placement_case]
    return dict(schema='agent-ee.antenna-placement-policy/1', part_number='ESP32-S3-WROOM-1',
        placement_case=placement_case,
        source=dict(path=pdf_path, sha256=ESP32_GUIDELINES_SHA256, page=page, locator=locator))


def antenna_placement_context(policy, captured_files):
    """Bind the selected policy source while explicitly withholding geometric qualification."""
    _object(policy, ('schema', 'part_number', 'placement_case', 'source'))
    source = policy['source']
    _object(source, ('path', 'sha256', 'page', 'locator'))
    path = PurePosixPath(_text(source['path']))
    if path.is_absolute() or '..' in path.parts or str(path) != source['path'] or '\\' in source['path']:
        raise FootprintError('unsafe antenna policy source path')
    expected = esp32_s3_wroom_1_placement_policy(policy['placement_case'], pdf_path=source['path'])
    if policy != expected:
        raise FootprintError('antenna placement case/source differs from the reviewed guideline snapshot')
    raw = captured_files.get(source['path'])
    if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != source['sha256']:
        raise FootprintError('antenna policy source capture missing or hash mismatch')
    return _seal(dict(schema='agent-ee.antenna-placement-context/1', execution='ok', outcome='unknown',
        coverage='missing', part_number=policy['part_number'], placement_case=policy['placement_case'],
        source=dict(source), policy=_ESP32_PLACEMENT_CASES[policy['placement_case']][2],
        source_derived_minimum=None,
        housing_clearance=dict(scope='inside end-product housing; not base-board copper clearance',
            minimum_mm='15', source=dict(path=source['path'], sha256=source['sha256'], page=33,
                locator='Section 1.4.7 RF: end-product housing clearance')),
        limitations=['Source applicability is bound to the declared case and captured snapshot only.',
            'Module pose, feed-point side, board edge/cutout and housing geometry remain unqualified.',
            'Retained footprint rule-area geometry is CAD evidence, not a manufacturer-derived minimum.',
            'Final RF range and throughput testing remain required.']))


def capture_esp32_s3_wroom_1(out_dir=None, *, stock_root='/usr/share/kicad', reference='U1'):
    """Capture the pinned KiCad 10.0.7 ESP32-S3-WROOM-1 footprint and symbol subset at runtime."""
    from pathlib import Path
    from .symbol_capture import capture_symbols
    root = Path(stock_root)
    fp_file = root / 'footprints' / ESP32_FOOTPRINT_PATH
    sym_file = root / 'symbols/RF_Module.kicad_sym'
    if not fp_file.is_file() or not sym_file.is_file():
        raise FootprintError(f'stock KiCad 10.0.7 RF_Module library files missing under {root}')
    fp_bytes = fp_file.read_bytes()
    if hashlib.sha256(fp_bytes).hexdigest() != ESP32_FOOTPRINT_SHA256:
        raise FootprintError('stock ESP32-S3-WROOM-1.kicad_mod sha256 differs from pinned KiCad 10.0.7')
    sym_source = sym_file.read_bytes()
    if hashlib.sha256(sym_source).hexdigest() != RF_MODULE_SYMBOL_LIB_SHA256:
        raise FootprintError('stock RF_Module.kicad_sym sha256 differs from pinned KiCad 10.0.7')
    sym_subset, sym_receipt = capture_symbols(
        sym_source, ['ESP32-S3-WROOM-1'], library='RF_Module', source_file='RF_Module.kicad_sym')
    if sym_receipt['output']['sha256'] != ESP32_SYMBOL_SUBSET_SHA256:
        raise FootprintError('captured RF_Module symbol subset sha256 differs from pinned KiCad 10.0.7')
    manifest = esp32_s3_wroom_1_manifest()
    declarations = esp32_s3_wroom_1_declarations()
    assignments = esp32_s3_wroom_1_assignments(reference=reference)
    captured_files = {
        ESP32_FOOTPRINT_PATH: fp_bytes,
        'symbols/RF_Module.kicad_sym': sym_subset,
    }
    if out_dir is not None:
        target = Path(out_dir)
        if target.exists():
            raise FootprintError(f'capture output directory already exists: {target}')
        (target / 'RF_Module.pretty').mkdir(parents=True)
        (target / 'symbols').mkdir(parents=True)
        (target / ESP32_FOOTPRINT_PATH).write_bytes(fp_bytes)
        (target / 'symbols/RF_Module.kicad_sym').write_bytes(sym_subset)
        (target / 'symbols/capture.json').write_bytes(canonical(sym_receipt) + b'\n')
        (target / 'footprints.manifest.json').write_bytes(canonical(manifest) + b'\n')
        (target / 'footprint-declarations.json').write_bytes(canonical(declarations) + b'\n')
        (target / 'assignments.json').write_bytes(canonical(assignments) + b'\n')
        (target / 'datasheet-facts.json').write_bytes(canonical(esp32_s3_wroom_1_datasheet_facts()) + b'\n')
        (target / 'rule-area.json').write_bytes(
            canonical(probe_rule_areas(fp_bytes, declared=validate_declarations(declarations)[0])) + b'\n')
    return dict(
        manifest=manifest,
        declarations=declarations,
        assignments=assignments,
        datasheet_facts=esp32_s3_wroom_1_datasheet_facts(),
        rule_area_probe=probe_rule_areas(fp_bytes, declared=validate_declarations(declarations)[0]),
        captured_files=captured_files,
        symbol_receipt=sym_receipt,
    )


def resolve_package(table, family, package, manifest, captured_files):
    """Exact captured alias rows; ambiguity never chooses the first row."""
    result = dict(schema='agent-ee.package-resolution/1', execution='failed', outcome='unknown',
                  table_sha256=digest(table), footprint=None, source=None)
    try:
        _object(table, ('schema', 'convention', 'rows'))
        if table['schema'] != 'agent-ee.package-aliases/1' or table['convention'] != 'explicit-imperial-to-metric':
            raise FootprintError('versioned explicit alias convention required')
        if not isinstance(table['rows'], list):
            raise FootprintError('alias rows required')
        for row in table['rows']:
            _object(row, ('family', 'package', 'footprint'))
            for v in row.values():
                _text(v)
        matches = [r for r in table['rows'] if (r['family'], r['package']) == (family, package)]
        if len(matches) != 1:
            raise FootprintError('ambiguous alias' if matches else 'unresolved alias')
        index = index_footprints(manifest, captured_files)
        if index['coverage'] != 'complete':
            raise FootprintError('incomplete footprint index')
        target = matches[0]['footprint']
        matches = [fp for fp in index['footprints'] if fp['lib_id'] == target]
        if len(matches) != 1:
            raise FootprintError('alias target unavailable')
        result.update(execution='ok', outcome='info', footprint=matches[0]['lib_id'], source=matches[0]['source'])
    except (ValueError, TypeError) as exc:
        result['message'] = str(exc)
    return _seal(result)


def read_captures(root, paths):
    """Read only explicit safe paths, including containment after symlink resolution."""
    from pathlib import Path
    root = Path(root).resolve(strict=True)
    captures = {}
    for path in paths:
        p = PurePosixPath(path)
        if p.is_absolute() or '..' in p.parts or str(p) != path or '\\' in path:
            raise FootprintError('unsafe captured path')
        resolved = (root / path).resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise FootprintError('captured path escapes root')
        captures[path] = resolved.read_bytes()
    return captures


def main(argv=None):
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--captures', required=True)
    parser.add_argument('--assignments')
    parser.add_argument('--declarations', help='captured agent-ee.footprint-declarations/1 file')
    parser.add_argument('--query', help='literal exact footprint identity; never shell syntax')
    parser.add_argument('--out', required=True, help='new output JSON file')
    args = parser.parse_args(argv)
    try:
        manifest = load_document(args.manifest)
        declaration = load_document(args.assignments) if args.assignments else None
        declarations = load_document(args.declarations) if args.declarations else None
        members = _members(manifest)
        paths = [member['path'] for _, member in members]
        if declaration is not None:
            paths += [lib['path'] for lib in declaration['libraries']]
        captures = read_captures(args.captures, paths)
        result = (check_assignments(manifest, captures, declaration, declarations=declarations)
                  if declaration is not None else index_footprints(manifest, captures, declarations))
        if args.query is not None:
            result = dict(schema='agent-ee.footprint-query/1', query=args.query,
                          index_sha256=result['canonical_sha256'], execution=result['execution'],
                          outcome='info' if result['coverage'] == 'complete' else 'unknown',
                          matches=[x for x in result.get('footprints', []) if x['lib_id'] == args.query])
            result = _seal(result)
        with Path(args.out).open('xb') as stream:
            stream.write(canonical(result) + b'\n')
        return 0 if result['execution'] == 'ok' else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())

"""Native RF geometry on the routed board: grounded-CPW width/gap/reference, stitching pitch, branch lengths.

A captured `agent-ee.rf-geometry/1` declaration names one RF net, its coplanar
ground net, the signal and reference layers and any of three sections:

- `cpw`: track width and the gap to coplanar ground copper on the signal layer
  match declared targets within declared tolerances, and the declared reference
  layer carries ground copper under the trace and both gaps. Targets may be read
  by JSON pointer from a captured calculation record instead of being restated
  (`source.unit` is `mm` by default or `mil`, converted exactly by 0.0254).
  A missing coplanar ground is reported as an absent structure, never graded as
  microstrip.
- `stitching`: ground vias spanning the signal and reference layers lie inside a
  declared band on each side of every declared branch, with no along-path gap
  larger than the declared pitch (path ends included).
- `topology`: an explicit trunk between two terminals plus named stubs; the
  routed copper graph must realise exactly that tree, and each branch's routed
  track length is graded against its declared bound.

An optional `electrical_length` section is reported only. Its effective
permittivity is either a declared number or read by JSON pointer from a captured
solver record bound by SHA-256: a hash mismatch refuses, and a value absent at the
pointer leaves every branch's electrical length unknown (null).

The sandboxed pcbnew probe (`python3 -m agent_ee.rf_geometry`) emits judgment-free
native facts: the RF net's tracks, pads and vias, ground vias and united copper
polygons (ground on the signal and reference layers, other copper on the signal
layer) clipped to the RF corridor. The coordinator evaluates them in pure Python.
Absent evidence is unknown: a null tolerance, pitch or length bound, unfilled
zones, missing RF copper or an unrouted terminal never passes. None of this is RF
qualification: no field solver, TDR, VNA or proxy such as guard width is used.
"""
import argparse
import heapq
import json
import math
import re
from pathlib import Path, PurePosixPath

from .storage import digest, write_json

SCHEMA = 'agent-ee.rf-geometry/1'
NATIVE_SCHEMA = 'agent-ee.rf-geometry-native/1'
RESULT_SCHEMA = 'agent-ee.rf-geometry-result/1'
TOP_KEYS = {'schema', 'net', 'ground_net', 'signal_layer', 'reference_layer'}
TOP_OPTIONAL = {'source', 'cpw', 'stitching', 'topology', 'electrical_length', 'basis'}
SOURCE_KEYS = {'path', 'width_pointer', 'gap_pointer'}
SOURCE_OPTIONAL = {'unit'}
SOURCE_UNITS = {'mm': 1.0, 'mil': 0.0254}
CPW_KEYS = {'width_tolerance_mm', 'gap_tolerance_mm'}
CPW_OPTIONAL = {'width_mm', 'gap_mm', 'sample_step_mm', 'search_mm'}
STITCH_KEYS = {'max_pitch_mm', 'band_mm'}
STITCH_OPTIONAL = {'branches'}
TRUNK_KEYS = {'from', 'to', 'max_length_mm'}
STUB_KEYS = {'name', 'terminal', 'max_length_mm'}
STUB_OPTIONAL = {'from'}
ELECTRICAL_KEYS = {'frequency_hz', 'basis'}
ELECTRICAL_OPTIONAL = {'effective_permittivity', 'effective_permittivity_source'}
PERMITTIVITY_SOURCE_KEYS = {'path', 'sha256', 'pointer'}
SHA256 = re.compile(r'[0-9a-f]{64}')
DEFAULT_STEP_MM = 0.05
DEFAULT_SEARCH_MM = 1.0
ARC_SAGITTA_NM = 100.0
LIGHT_M_PER_S = 299_792_458
NM = 1_000_000
COPPER_SETS = ('ground_signal', 'ground_signal_pour', 'other_signal', 'ground_reference')


# ------------------------------------------------------------------ declaration

def _number(value, label, *, nullable=False, positive=True):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number' + (' or null' if nullable else ''))
    if positive and value <= 0:
        raise ValueError(f'{label} must be positive')
    return value


def _text(value, label):
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f'{label} must be nonempty text')
    return value


def _keys(obj, required, optional, label):
    if not isinstance(obj, dict) or not required <= set(obj) or not set(obj) <= required | optional:
        raise ValueError(f'{label} requires {sorted(required)}'
                         + (f', optionally {sorted(optional)}' if optional else ''))


def _terminal(value, label):
    _text(value, label)
    ref, sep, pad = value.partition('.')
    if not sep or not ref or not pad:
        raise ValueError(f'{label} must be REF.PAD')
    return value


def pointer(document, path):
    """RFC 6901 JSON pointer resolution."""
    if not isinstance(path, str) or (path and not path.startswith('/')):
        raise ValueError('JSON pointer must be empty or start with /')
    node = document
    for token in path.split('/')[1:]:
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            raise ValueError(f'JSON pointer {path!r} does not resolve')
    return node


def _layer(value, label):
    _text(value, label)
    if value not in ('F.Cu', 'B.Cu') and not (value.startswith('In') and value.endswith('.Cu') and
                                              value[2:-3].isdigit()):
        raise ValueError(f'{label} must be a KiCad copper layer')
    return value


def parse_declaration(raw, load_source=None):
    """Validate a declaration and resolve its CPW targets; returns (declaration, resolved)."""
    try:
        doc = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'rf-geometry declaration is not JSON: {exc}') from exc
    _keys(doc, TOP_KEYS, TOP_OPTIONAL, 'rf-geometry declaration')
    if doc['schema'] != SCHEMA:
        raise ValueError(f'rf-geometry declaration schema must be {SCHEMA}')
    for key in ('net', 'ground_net'):
        _text(doc[key], key)
    if doc['net'] == doc['ground_net']:
        raise ValueError('RF net and ground net must differ')
    _layer(doc['signal_layer'], 'signal_layer')
    _layer(doc['reference_layer'], 'reference_layer')
    if doc['signal_layer'] == doc['reference_layer']:
        raise ValueError('signal and reference layers must differ')
    if not {'cpw', 'stitching', 'topology'} & set(doc):
        raise ValueError('rf-geometry declaration needs cpw, stitching or topology')
    if 'basis' in doc:
        _text(doc['basis'], 'basis')
    resolved = {'net': doc['net'], 'ground_net': doc['ground_net'],
                'signal_layer': doc['signal_layer'], 'reference_layer': doc['reference_layer']}
    if 'source' in doc:
        source = doc['source']
        _keys(source, SOURCE_KEYS, SOURCE_OPTIONAL, 'source')
        if source.get('unit', 'mm') not in SOURCE_UNITS:
            raise ValueError(f'source unit must be one of {sorted(SOURCE_UNITS)}')
        if 'cpw' not in doc:
            raise ValueError('source binds CPW targets and requires cpw')
        if load_source is None:
            raise ValueError('source requires captured inputs')
        captured = load_source(source['path'])
        resolved['source'] = {'path': source['path'], 'sha256': captured['sha256'],
                              'width_pointer': source['width_pointer'],
                              'gap_pointer': source['gap_pointer'], 'unit': source.get('unit', 'mm')}
    if 'cpw' in doc:
        cpw = doc['cpw']
        _keys(cpw, CPW_KEYS, CPW_OPTIONAL, 'cpw')
        if 'source' in doc:
            if {'width_mm', 'gap_mm'} & set(cpw):
                raise ValueError('cpw targets come from source; do not restate width_mm or gap_mm')
            scale = SOURCE_UNITS[source.get('unit', 'mm')]
            width = _number(pointer(captured['document'], source['width_pointer']), 'source width') * scale
            gap = _number(pointer(captured['document'], source['gap_pointer']), 'source gap') * scale
        else:
            if not {'width_mm', 'gap_mm'} <= set(cpw):
                raise ValueError('cpw requires width_mm and gap_mm, or a source')
            width, gap = cpw['width_mm'], cpw['gap_mm']
        resolved['cpw'] = {
            'width_mm': _number(width, 'cpw width'), 'gap_mm': _number(gap, 'cpw gap'),
            'width_tolerance_mm': _number(cpw['width_tolerance_mm'], 'width_tolerance_mm', nullable=True),
            'gap_tolerance_mm': _number(cpw['gap_tolerance_mm'], 'gap_tolerance_mm', nullable=True),
            'sample_step_mm': _number(cpw.get('sample_step_mm', DEFAULT_STEP_MM), 'sample_step_mm'),
            'search_mm': _number(cpw.get('search_mm', DEFAULT_SEARCH_MM), 'search_mm')}
    names = []
    if 'topology' in doc:
        topology = doc['topology']
        _keys(topology, {'trunk', 'stubs'}, set(), 'topology')
        trunk = topology['trunk']
        _keys(trunk, TRUNK_KEYS, set(), 'trunk')
        branches = [{'name': 'trunk', 'from_terminal': _terminal(trunk['from'], 'trunk from'),
                     'terminal': _terminal(trunk['to'], 'trunk to'),
                     'max_length_mm': _number(trunk['max_length_mm'], 'trunk max_length_mm', nullable=True)}]
        if branches[0]['from_terminal'] == branches[0]['terminal']:
            raise ValueError('trunk terminals must differ')
        if not isinstance(topology['stubs'], list):
            raise ValueError('stubs must be a list')
        terminals = {branches[0]['from_terminal'], branches[0]['terminal']}
        names = ['trunk']
        for stub in topology['stubs']:
            _keys(stub, STUB_KEYS, STUB_OPTIONAL, 'stub')
            name = _text(stub['name'], 'stub name')
            parent = stub.get('from', 'trunk')
            if name in names or parent not in names:
                raise ValueError(f'stub {name!r} must be unique and attach to an earlier branch')
            terminal = _terminal(stub['terminal'], f'stub {name} terminal')
            if terminal in terminals:
                raise ValueError(f'terminal {terminal} is declared twice')
            terminals.add(terminal)
            names.append(name)
            branches.append({'name': name, 'parent': parent, 'terminal': terminal,
                             'max_length_mm': _number(stub['max_length_mm'], f'{name} max_length_mm',
                                                      nullable=True)})
        resolved['topology'] = branches
    if 'stitching' in doc:
        stitching = doc['stitching']
        _keys(stitching, STITCH_KEYS, STITCH_OPTIONAL, 'stitching')
        if 'topology' not in doc:
            raise ValueError('stitching is measured along declared branches and requires topology')
        band = stitching['band_mm']
        if (not isinstance(band, list) or len(band) != 2 or
                _number(band[0], 'band inner', positive=False) < 0 or _number(band[1], 'band outer') <= band[0]):
            raise ValueError('band_mm must be [inner, outer] offsets from the centreline, 0 <= inner < outer')
        selected = stitching.get('branches', names)
        if (not isinstance(selected, list) or not selected or len(set(selected)) != len(selected) or
                any(b not in names for b in selected)):
            raise ValueError('stitching branches must name declared branches')
        resolved['stitching'] = {'max_pitch_mm': _number(stitching['max_pitch_mm'], 'max_pitch_mm', nullable=True),
                                 'band_mm': list(band), 'branches': list(selected)}
    if 'electrical_length' in doc:
        electrical = doc['electrical_length']
        _keys(electrical, ELECTRICAL_KEYS, ELECTRICAL_OPTIONAL, 'electrical_length')
        if 'topology' not in doc:
            raise ValueError('electrical_length reports branch lengths and requires topology')
        if len(ELECTRICAL_OPTIONAL & set(electrical)) != 1:
            raise ValueError('electrical_length requires exactly one of effective_permittivity or '
                             'effective_permittivity_source')
        resolved['electrical_length'] = {
            'frequency_hz': _number(electrical['frequency_hz'], 'frequency_hz'),
            'basis': _text(electrical['basis'], 'electrical_length basis')}
        if 'effective_permittivity' in electrical:
            resolved['electrical_length']['effective_permittivity'] = _number(
                electrical['effective_permittivity'], 'effective_permittivity')
        else:
            resolved['electrical_length'].update(_bound_permittivity(
                electrical['effective_permittivity_source'], load_source))
    return doc, resolved


def _bound_permittivity(binding, load_source):
    """Effective permittivity read by JSON pointer from a captured record bound by SHA-256.

    A hash mismatch refuses; a pointer that does not resolve, or resolves to null, is unknown."""
    _keys(binding, PERMITTIVITY_SOURCE_KEYS, set(), 'effective_permittivity_source')
    if not isinstance(binding['sha256'], str) or not SHA256.fullmatch(binding['sha256']):
        raise ValueError('effective_permittivity_source sha256 must be 64 lowercase hex digits')
    path = binding['pointer']
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('effective_permittivity_source pointer must start with /')
    if load_source is None:
        raise ValueError('effective_permittivity_source requires captured inputs')
    captured = load_source(binding['path'])
    if captured['sha256'] != binding['sha256']:
        raise ValueError(f"effective_permittivity_source {binding['path']} has sha256 {captured['sha256']}, "
                         f"not the declared {binding['sha256']}")
    source = {'path': binding['path'], 'sha256': captured['sha256'], 'pointer': path}
    try:
        value = pointer(captured['document'], path)
    except ValueError:
        value = None
    if value is None:
        return {'effective_permittivity': None, 'source': source, 'status': 'unknown',
                'reason': 'effective permittivity is absent from the captured record at the pointer'}
    return {'effective_permittivity': _number(value, 'source effective_permittivity'), 'source': source}


def predicates(resolved):
    """Bounded predicates only: a null tolerance, pitch or length bound is not evaluable."""
    found = []
    cpw = resolved.get('cpw')
    if cpw:
        found += ['rf-geometry:cpw-structure', 'rf-geometry:cpw-reference']
        if cpw['width_tolerance_mm'] is not None:
            found.append('rf-geometry:cpw-width')
        if cpw['gap_tolerance_mm'] is not None:
            found.append('rf-geometry:cpw-gap')
    if resolved.get('stitching', {}).get('max_pitch_mm') is not None:
        found.append('rf-geometry:stitching-pitch')
    if 'topology' in resolved:
        found.append('rf-geometry:branch-topology')
        found += [f"rf-geometry:branch-length:{b['name']}" for b in resolved['topology']
                  if b['max_length_mm'] is not None]
    return found


def _captured(rel, files, label):
    if not isinstance(rel, str) or str(PurePosixPath(rel)) != rel or PurePosixPath(rel).is_absolute() \
            or '..' in PurePosixPath(rel).parts or rel not in files:
        raise ValueError(f'{label} must be a captured input: {rel!r}')
    return rel


def capability(params, scope, files, inputs):
    if not isinstance(params, dict) or set(params) != {'declaration'}:
        raise ValueError('rf_geometry requires exactly params.declaration')
    board = scope.get('board')
    _captured(board, files, 'board')
    declaration = _captured(params['declaration'], files, 'declaration')
    if inputs is None:
        raise ValueError('rf_geometry requires captured inputs')
    root = Path(inputs)
    immutable = [declaration]

    def load_source(rel):
        _captured(rel, files, 'source')
        if rel in (board, declaration):
            raise ValueError('source must be a distinct captured record')
        immutable.append(rel)
        data = (root / rel).read_bytes()
        try:
            document = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f'source is not JSON: {exc}') from exc
        return {'document': document, 'sha256': digest(root / rel)}

    doc, resolved = parse_declaration((root / declaration).read_bytes(), load_source)
    return {'immutable_inputs': sorted(set(immutable)), 'predicates': predicates(resolved),
            'declaration': doc, 'resolved': resolved}


# ------------------------------------------------------------------ native probe (sandbox)

def _polyset(poly):
    result = []
    for i in range(poly.OutlineCount()):
        chain = poly.Outline(i)
        result.append({'outline': [[int(chain.CPoint(j).x), int(chain.CPoint(j).y)]
                                   for j in range(chain.PointCount())],
                       'holes': [[[int(poly.Hole(i, h).CPoint(j).x), int(poly.Hole(i, h).CPoint(j).y)]
                                  for j in range(poly.Hole(i, h).PointCount())]
                                 for h in range(poly.HoleCount(i))]})
    return result


def extract(board_path, request):
    """Judgment-free native facts for one declared RF net (runs inside the sandbox)."""
    import pcbnew
    board = pcbnew.LoadBoard(str(board_path))
    if board is None:
        raise ValueError('native board load failed')
    rf, gnd = request['net'], request['ground_net']
    copper = list(board.GetEnabledLayers().CuStack())
    names = [board.GetLayerName(layer) for layer in copper]
    ids = dict(zip(names, copper))
    max_error = int(board.GetDesignSettings().m_MaxError)
    nets = {str(n.GetNetname()) for n in board.GetNetInfo().NetsByNetcode().values()}
    report = {'schema': NATIVE_SCHEMA, 'pcbnew_version': pcbnew.Version(), 'board_sha256': digest(board_path),
              'max_error_nm': max_error, 'copper_layers': names,
              'nets_present': {'net': rf in nets, 'ground_net': gnd in nets},
              'arc_sagitta_nm': ARC_SAGITTA_NM}

    def point(vec):
        return [int(vec.x), int(vec.y)]

    tracks, vias, pads, rf_zones, xs, ys = [], [], [], [], [], []
    for item in board.GetTracks():
        cls = item.GetClass()
        net = str(item.GetNetname())
        if cls == 'PCB_VIA':
            if net in (rf, gnd):
                vias.append({'uuid': item.m_Uuid.AsString(), 'net': net, 'position_nm': point(item.GetPosition()),
                             'width_nm': int(item.GetWidth(pcbnew.PADSTACK.ALL_LAYERS)),
                             'drill_nm': int(item.GetDrillValue()),
                             'copper_layers': [board.GetLayerName(l) for l in copper if item.IsOnLayer(l)]})
                if net == rf:
                    xs += [item.GetPosition().x]
                    ys += [item.GetPosition().y]
            continue
        if net != rf:
            continue
        if cls not in ('PCB_TRACK', 'PCB_ARC'):
            raise ValueError(f'unsupported routed primitive: {cls}')
        entry = {'uuid': item.m_Uuid.AsString(), 'kind': 'arc' if cls == 'PCB_ARC' else 'segment',
                 'layer': board.GetLayerName(item.GetLayer()), 'width_nm': int(item.GetWidth()),
                 'start_nm': point(item.GetStart()), 'end_nm': point(item.GetEnd())}
        if cls == 'PCB_ARC':
            entry['mid_nm'] = point(item.GetMid())
        tracks.append(entry)
        box = item.GetBoundingBox()
        xs += [box.GetX(), box.GetRight()]
        ys += [box.GetY(), box.GetBottom()]
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if str(pad.GetNetname()) != rf:
                continue
            polygons = {}
            for layer in copper:
                if pad.IsOnLayer(layer) and pad.FlashLayer(layer):
                    poly = pcbnew.SHAPE_POLY_SET()
                    pad.TransformShapeToPolygon(poly, layer, 0, max_error, pcbnew.ERROR_OUTSIDE)
                    poly.Simplify()
                    polygons[board.GetLayerName(layer)] = _polyset(poly)
            pads.append({'ref': str(fp.GetReference()), 'pad': str(pad.GetNumber()), 'uuid': pad.m_Uuid.AsString(),
                         'position_nm': point(pad.GetPosition()), 'polygons_nm': polygons})
            box = pad.GetBoundingBox()
            xs += [box.GetX(), box.GetRight()]
            ys += [box.GetY(), box.GetBottom()]
    zones = [*board.Zones(), *(z for fp in board.GetFootprints() for z in fp.Zones())]
    rf_zones = sorted(z.m_Uuid.AsString() for z in zones if not z.GetIsRuleArea() and str(z.GetNetname()) == rf)
    report.update(tracks=tracks, vias=vias, pads=pads, rf_zones=rf_zones)
    report['all_rf_net_pads'] = sorted({f"{p['ref']}.{p['pad']}" for p in pads})

    signal, reference = request['signal_layer'], request['reference_layer']
    report['absent_layers'] = [n for n in (signal, reference) if n not in ids]
    copper_sets = {key: None for key in COPPER_SETS}
    unfilled = {key: [] for key in copper_sets}
    if xs and not report['absent_layers']:
        margin = int(request['margin_nm'])
        clip_box = [min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin]
        report['clip_nm'] = clip_box
        clip = pcbnew.SHAPE_POLY_SET()
        clip.NewOutline()
        for x, y in ((clip_box[0], clip_box[1]), (clip_box[2], clip_box[1]),
                     (clip_box[2], clip_box[3]), (clip_box[0], clip_box[3])):
            clip.Append(x, y)
        # ground_signal_pour excludes pads and vias: it decides whether a coplanar ground conductor
        # runs beside the trace at all, so isolated stitching vias or ground pads cannot stand in for it.
        plan = (('ground_signal', signal, lambda net: net == gnd, pcbnew.ERROR_OUTSIDE, True),
                ('ground_signal_pour', signal, lambda net: net == gnd, pcbnew.ERROR_OUTSIDE, False),
                ('other_signal', signal, lambda net: net not in (rf, gnd), pcbnew.ERROR_OUTSIDE, True),
                ('ground_reference', reference, lambda net: net == gnd, pcbnew.ERROR_INSIDE, True))
        shapes = [*board.GetDrawings(), *(g for fp in board.GetFootprints() for g in fp.GraphicalItems())]
        all_pads = [pad for fp in board.GetFootprints() for pad in fp.Pads()]
        for key, layer_name, wanted, error, flashed in plan:
            layer = ids[layer_name]
            poly = pcbnew.SHAPE_POLY_SET()
            for item in board.GetTracks():
                is_via = item.GetClass() == 'PCB_VIA'
                on = (flashed and item.FlashLayer(layer)) if is_via else item.GetLayer() == layer
                if on and wanted(str(item.GetNetname())):
                    item.TransformShapeToPolygon(poly, layer, 0, max_error, error)
            for pad in all_pads if flashed else ():
                if pad.IsOnLayer(layer) and pad.FlashLayer(layer) and wanted(str(pad.GetNetname())):
                    pad.TransformShapeToPolygon(poly, layer, 0, max_error, error)
            for shape in shapes:
                net = str(shape.GetNetname()) if isinstance(shape, pcbnew.BOARD_CONNECTED_ITEM) else ''
                if shape.IsOnLayer(layer) and wanted(net):
                    shape.TransformShapeToPolygon(poly, layer, 0, max_error, error)
            for zone in zones:
                if zone.GetIsRuleArea() or not zone.IsOnLayer(layer) or not wanted(str(zone.GetNetname())):
                    continue
                if zone.IsFilled() and zone.HasFilledPolysForLayer(layer):
                    poly.Append(zone.GetFilledPolysList(layer))
                else:
                    unfilled[key].append(zone.m_Uuid.AsString())
            poly.Simplify()
            poly.BooleanIntersection(clip)
            poly.Simplify()
            copper_sets[key] = _polyset(poly)
    report['copper'] = copper_sets
    report['unfilled_zones'] = {k: sorted(v) for k, v in unfilled.items()}
    return report


# ------------------------------------------------------------------ planar geometry (coordinator)

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def point_segment(p, a, b):
    """Distance from p to segment ab and the clamped parameter t."""
    ab = _sub(b, a)
    length2 = _dot(ab, ab)
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, _dot(_sub(p, a), ab) / length2))
    q = (a[0] + t * ab[0], a[1] + t * ab[1])
    return math.hypot(p[0] - q[0], p[1] - q[1]), t


def _orient(a, b, c):
    value = _cross(_sub(b, a), _sub(c, a))
    return (value > 0) - (value < 0)


def segments_cross(a, b, c, d):
    """True when segments ab and cd intersect (touching included)."""
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    if o1 != o2 and o3 != o4:
        return True

    def on(p, q, r):
        return min(p[0], q[0]) <= r[0] <= max(p[0], q[0]) and min(p[1], q[1]) <= r[1] <= max(p[1], q[1])
    return ((o1 == 0 and on(a, b, c)) or (o2 == 0 and on(a, b, d)) or
            (o3 == 0 and on(c, d, a)) or (o4 == 0 and on(c, d, b)))


def segment_segment(a, b, c, d):
    if segments_cross(a, b, c, d):
        return 0.0
    return min(point_segment(a, c, d)[0], point_segment(b, c, d)[0],
               point_segment(c, a, b)[0], point_segment(d, a, b)[0])


def arc_polyline(start, mid, end, sagitta=ARC_SAGITTA_NM):
    """Chord approximation of a three-point arc with bounded sagitta."""
    ax, ay = start
    bx, by = mid
    cx, cy = end
    det = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if det == 0:
        return [tuple(start), tuple(end)], math.dist(start, end)
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / det
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / det
    radius = math.hypot(ax - ux, ay - uy)
    a0, am, a1 = (math.atan2(p[1] - uy, p[0] - ux) for p in (start, mid, end))
    sweep = (a1 - a0) % (2 * math.pi)
    if (am - a0) % (2 * math.pi) > sweep:
        sweep -= 2 * math.pi
    step = 2 * math.acos(max(-1.0, 1 - sagitta / radius)) if radius > sagitta else math.pi / 2
    count = max(2, math.ceil(abs(sweep) / step))
    points = [tuple(start)]
    points += [(ux + radius * math.cos(a0 + sweep * i / count), uy + radius * math.sin(a0 + sweep * i / count))
               for i in range(1, count)]
    points.append(tuple(end))
    return points, radius * abs(sweep)


def track_polyline(track):
    if track['kind'] == 'arc':
        return arc_polyline(track['start_nm'], track['mid_nm'], track['end_nm'])
    return [tuple(track['start_nm']), tuple(track['end_nm'])], math.dist(track['start_nm'], track['end_nm'])


def polyline_length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


class EdgeSet:
    """Edges of a polygon set with a uniform grid index; even-odd containment."""

    def __init__(self, polysets, cell=500_000):
        self.rings, self.edges, self.cell, self.grid = [], [], cell, {}
        for polyset in polysets:
            for poly in polyset or []:
                for ring in [poly['outline'], *poly['holes']]:
                    if len(ring) < 3:
                        continue
                    ring = [tuple(p) for p in ring]
                    self.rings.append(ring)
                    for a, b in zip(ring, ring[1:] + ring[:1]):
                        index = len(self.edges)
                        self.edges.append((a, b))
                        for key in self._cells(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])):
                            self.grid.setdefault(key, []).append(index)

    def __bool__(self):
        return bool(self.edges)

    def _cells(self, x0, y0, x1, y1):
        c = self.cell
        for i in range(math.floor(x0 / c), math.floor(x1 / c) + 1):
            for j in range(math.floor(y0 / c), math.floor(y1 / c) + 1):
                yield (i, j)

    def near(self, x0, y0, x1, y1):
        found = set()
        for key in self._cells(x0, y0, x1, y1):
            found.update(self.grid.get(key, ()))
        return [self.edges[i] for i in sorted(found)]

    def contains(self, p):
        inside = False
        for ring in self.rings:
            for a, b in zip(ring, ring[1:] + ring[:1]):
                if (a[1] > p[1]) != (b[1] > p[1]):
                    x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                    if x > p[0]:
                        inside = not inside
        return inside

    def distance(self, p, radius):
        """0 inside; nearest edge within radius; None when nothing is within radius."""
        if self.contains(p):
            return 0.0
        best = None
        for a, b in self.near(p[0] - radius, p[1] - radius, p[0] + radius, p[1] + radius):
            d = point_segment(p, a, b)[0]
            if d <= radius and (best is None or d < best):
                best = d
        return best

    def polyline_distance(self, points, radius):
        """Minimum distance from a polyline to the set (0 when it touches or enters)."""
        if any(self.contains(p) for p in points[:1]):
            return 0.0
        best = None
        for a, b in zip(points, points[1:]):
            for c, d in self.near(min(a[0], b[0]) - radius, min(a[1], b[1]) - radius,
                                  max(a[0], b[0]) + radius, max(a[1], b[1]) + radius):
                value = segment_segment(a, b, c, d)
                if best is None or value < best:
                    best = value
        return best

    def contains_convex(self, quad):
        """Exact containment of a convex polygon: vertices inside, no boundary crossing or vertex inside."""
        if not all(self.contains(p) for p in quad):
            return False
        xs, ys = [p[0] for p in quad], [p[1] for p in quad]
        edges = list(zip(quad, quad[1:] + quad[:1]))
        for c, d in self.near(min(xs), min(ys), max(xs), max(ys)):
            if any(_proper(a, b, c, d) for a, b in edges):
                return False
            for v in (c, d):
                if all(_orient(a, b, v) > 0 for a, b in edges) or all(_orient(a, b, v) < 0 for a, b in edges):
                    return False
        return True


def _proper(a, b, c, d):
    """Proper crossing (collinear touching is not a crossing)."""
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    return o1 * o2 < 0 and o3 * o4 < 0


def _mm(nm):
    return None if nm is None else round(nm / NM, 6)


def _status(statuses):
    statuses = set(statuses)
    return 'defect' if 'defect' in statuses else 'unknown' if 'unknown' in statuses or not statuses else 'pass'


# ------------------------------------------------------------------ evaluation

def _validate_native(native, resolved):
    if not isinstance(native, dict) or native.get('schema') != NATIVE_SCHEMA:
        raise ValueError('native rf-geometry report schema mismatch')
    for key in ('tracks', 'vias', 'pads', 'rf_zones', 'copper_layers', 'absent_layers'):
        if not isinstance(native.get(key), list):
            raise ValueError(f'native {key} must be a list')
    if type(native.get('max_error_nm')) is not int or native['max_error_nm'] < 0:
        raise ValueError('native max_error_nm must be a nonnegative integer')
    present = native.get('nets_present')
    if (not isinstance(present, dict) or set(present) != {'net', 'ground_net'} or
            not all(isinstance(v, bool) for v in present.values())):
        raise ValueError('native nets_present is malformed')
    if (not isinstance(native.get('all_rf_net_pads'), list) or
            sorted({f"{p.get('ref')}.{p.get('pad')}" for p in native['pads'] if isinstance(p, dict)})
            != native['all_rf_net_pads']):
        raise ValueError('native RF pad inventory is inconsistent')

    def pt(value):
        return isinstance(value, list) and len(value) == 2 and all(type(v) is int for v in value)
    for track in native['tracks']:
        if (not isinstance(track, dict) or track.get('kind') not in ('segment', 'arc') or
                not isinstance(track.get('uuid'), str) or not isinstance(track.get('layer'), str) or
                type(track.get('width_nm')) is not int or track['width_nm'] <= 0 or
                not pt(track.get('start_nm')) or not pt(track.get('end_nm')) or
                (track['kind'] == 'arc' and not pt(track.get('mid_nm')))):
            raise ValueError('native RF track geometry is malformed')
    for via in native['vias']:
        if (not isinstance(via, dict) or not isinstance(via.get('uuid'), str) or
                via.get('net') not in (resolved['net'], resolved['ground_net']) or
                not pt(via.get('position_nm')) or type(via.get('width_nm')) is not int or
                not isinstance(via.get('copper_layers'), list)):
            raise ValueError('native via geometry is malformed')
    for pad in native['pads']:
        if (not isinstance(pad, dict) or not isinstance(pad.get('ref'), str) or not isinstance(pad.get('pad'), str)
                or not pt(pad.get('position_nm')) or not isinstance(pad.get('polygons_nm'), dict)):
            raise ValueError('native RF pad geometry is malformed')
    copper = native.get('copper')
    if not isinstance(copper, dict) or set(copper) != set(COPPER_SETS):
        raise ValueError('native copper sets are malformed')
    for polyset in copper.values():
        if polyset is None:
            continue
        if not isinstance(polyset, list) or any(
                not isinstance(p, dict) or not isinstance(p.get('outline'), list) or
                not isinstance(p.get('holes'), list) or not all(pt(v) for v in p['outline']) or
                not all(isinstance(h, list) and all(pt(v) for v in h) for h in p['holes']) for p in polyset):
            raise ValueError('native copper polygons are malformed')
    if native['tracks'] and not native['absent_layers'] and any(v is None for v in copper.values()):
        raise ValueError('native copper sets are missing for a routed RF net')
    unfilled = native.get('unfilled_zones')
    if not isinstance(unfilled, dict) or set(unfilled) != set(copper):
        raise ValueError('native unfilled-zone inventory is malformed')


def _samples(points, step):
    """(s, point, unit normal to the left of travel) along a polyline, ends included."""
    result, base = [], 0.0
    pieces = list(zip(points, points[1:]))
    for index, (a, b) in enumerate(pieces):
        length = math.dist(a, b)
        if length == 0:
            continue
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        count = max(1, math.ceil(length / step))
        last = index == len(pieces) - 1
        for i in range(count + (1 if last else 0)):
            t = min(1.0, i / count)
            result.append((base + t * length, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])), (-uy, ux)))
        base += length
    return result


def _runs(violations):
    """Collapse consecutive violating samples into intervals per track, side and reason."""
    runs = []
    for v in violations:
        last = runs[-1] if runs else None
        if (last and last['track'] == v['track'] and last['side'] == v['side'] and last['reason'] == v['reason']
                and v['index'] == last['_index'] + 1):
            last.update(_index=v['index'], to_mm=v['s_mm'], samples=last['samples'] + 1)
            if v.get('gap_mm') is not None and (last['worst_gap_mm'] is None or
                                                abs(v['gap_mm'] - last['_target']) > abs(last['worst_gap_mm'] - last['_target'])):
                last['worst_gap_mm'] = v['gap_mm']
            continue
        runs.append({'track': v['track'], 'side': v['side'], 'reason': v['reason'], 'from_mm': v['s_mm'],
                     'to_mm': v['s_mm'], 'samples': 1, 'worst_gap_mm': v.get('gap_mm'),
                     '_index': v['index'], '_target': v['target']})
    for run in runs:
        run.pop('_index')
        run.pop('_target')
    return runs


def evaluate_cpw(resolved, native):
    spec = resolved['cpw']
    signal, ref_layer = resolved['signal_layer'], resolved['reference_layer']
    width_nm, gap_nm = spec['width_mm'] * NM, spec['gap_mm'] * NM
    wtol, gtol = spec['width_tolerance_mm'], spec['gap_tolerance_mm']
    max_error = native['max_error_nm']
    items = {}
    tracks = native['tracks']
    on_signal = [t for t in tracks if t['layer'] == signal]
    off_signal = [t for t in tracks if t['layer'] != signal]
    if native['absent_layers']:
        reason = f"declared layer(s) absent from board: {native['absent_layers']}; grounded CPW structure absent"
        items['structure'] = {'status': 'defect', 'reason': reason}
        return {'status': 'defect', 'items': items}
    if not on_signal:
        reason = ('declared RF net absent from board' if not native.get('nets_present', {}).get('net')
                  else f'no routed RF copper on {signal}')
        return {'status': 'unknown', 'items': {'structure': {'status': 'unknown', 'reason': reason}}}

    # Width: exact native track widths.
    width_bad = [] if wtol is None else [
        {'track': t['uuid'], 'width_mm': _mm(t['width_nm'])}
        for t in on_signal if abs(t['width_nm'] - width_nm) > wtol * NM + 1e-6]
    items['width'] = ({'status': 'unknown', 'reason': 'width tolerance not declared',
                       'measured_mm': sorted({_mm(t['width_nm']) for t in on_signal})} if wtol is None else
                      {'status': 'defect' if width_bad else 'pass', 'target_mm': spec['width_mm'],
                       'tolerance_mm': wtol, 'violations': width_bad,
                       'measured_mm': sorted({_mm(t['width_nm']) for t in on_signal})})
    items['layer'] = {'status': 'defect' if off_signal else 'pass',
                      'off_signal_layer': [{'track': t['uuid'], 'layer': t['layer']} for t in off_signal]}

    copper = native['copper']
    unfilled = native['unfilled_zones']
    ground = EdgeSet([copper['ground_signal']])
    pour = EdgeSet([copper['ground_signal_pour']])
    other = EdgeSet([copper['other_signal']])
    reference = EdgeSet([copper['ground_reference']])
    search = spec['search_mm'] * NM
    polylines = {t['uuid']: track_polyline(t)[0] for t in on_signal}
    signal_unfilled = sorted(set(unfilled['ground_signal'] + unfilled['ground_signal_pour'] +
                                 unfilled['other_signal']))
    graded_gap = gtol is not None and gtol * NM > max_error

    if signal_unfilled:
        reason = {'reason': 'zones on the signal layer are unfilled', 'zones': signal_unfilled}
        items['structure'] = {'status': 'unknown', **reason}
        items['gap'] = {'status': 'unknown', **reason}
    else:
        # Edge samples: a point on each trace edge, every sample_step along each track. Samples within
        # gap(+tolerance) of other RF-net copper are transitions (bends, junctions, launches).
        high, low = gap_nm + (gtol or 0) * NM, gap_nm - (gtol or 0) * NM
        own_pads = [EdgeSet([p['polygons_nm'][signal]]) for p in native['pads'] if signal in p['polygons_nm']]
        own_vias = [v for v in native['vias'] if v['net'] == resolved['net'] and signal in v['copper_layers']]
        minimum, violations, graded, transitions, index, sides = [], [], 0, 0, 0, {}
        for track in on_signal:
            points, half = polylines[track['uuid']], track['width_nm'] / 2
            if graded_gap:
                # Exact minimum over the whole track, transitions included.
                nearest = ground.polyline_distance(points, high + half)
                gap_min = None if nearest is None else nearest - half
                foreign = other.polyline_distance(points, high + half)
                minimum.append({'track': track['uuid'], 'min_gap_mm': _mm(gap_min),
                                'min_foreign_mm': _mm(None if foreign is None else foreign - half)})
                if gap_min is not None and gap_min < low:
                    violations.append({'track': track['uuid'], 'side': 'either', 'reason': 'gap_below_tolerance',
                                       's_mm': 0.0, 'gap_mm': _mm(gap_min), 'index': -1,
                                       'target': spec['gap_mm']})
            others = [(polylines[t['uuid']], t['width_nm'] / 2) for t in on_signal if t['uuid'] != track['uuid']]
            for s, p, normal in _samples(points, spec['sample_step_mm'] * NM):
                for side, sign in (('left', 1), ('right', -1)):
                    index += 1
                    edge = (p[0] + sign * normal[0] * half, p[1] + sign * normal[1] * half)
                    pad_distances = [es.distance(edge, high) for es in own_pads]
                    own = min([point_segment(edge, a, b)[0] - w for pts, w in others for a, b in zip(pts, pts[1:])] +
                              [math.inf if d is None else d for d in pad_distances] +
                              [math.dist(edge, v['position_nm']) - v['width_nm'] / 2 for v in own_vias] + [math.inf])
                    if own < high:
                        transitions += 1
                        continue
                    graded += 1
                    tally = sides.setdefault((track['uuid'], side), [0, 0])
                    tally[0] += 1
                    if pour.distance(edge, search) is not None:
                        tally[1] += 1
                    if not graded_gap:
                        continue
                    dg = ground.distance(edge, search)
                    do = other.distance(edge, high)
                    entry = {'track': track['uuid'], 'side': side, 's_mm': _mm(s), 'index': index,
                             'target': spec['gap_mm']}
                    if do is not None and (dg is None or do <= dg):
                        violations.append({**entry, 'reason': 'foreign_copper_in_gap', 'gap_mm': _mm(do)})
                    elif dg is None:
                        violations.append({**entry, 'reason': 'coplanar_ground_missing', 'gap_mm': None})
                    elif dg > high:
                        violations.append({**entry, 'reason': 'gap_above_tolerance', 'gap_mm': _mm(dg)})
                    elif dg < low:
                        violations.append({**entry, 'reason': 'gap_below_tolerance', 'gap_mm': _mm(dg)})

        # Structure: a ground pour/track conductor (not just vias or pads) beside every track side.
        missing = [{'track': t, 'side': side, 'graded_edge_samples': n}
                   for (t, side), (n, present) in sorted(sides.items()) if n and not present]
        if missing:
            items['structure'] = {
                'status': 'defect', 'reason': 'coplanar_ground_absent', 'track_sides': missing,
                'statement': f"no {resolved['ground_net']} pour or track on {signal} within "
                             f"{spec['search_mm']} mm of any graded edge sample of these track sides: the routed "
                             "structure is not grounded CPW there and is not graded as microstrip"}
        elif graded:
            items['structure'] = {'status': 'pass', 'search_mm': spec['search_mm'],
                                  'track_sides': len(sides)}
        else:
            items['structure'] = {'status': 'unknown', 'reason': 'no edge sample away from other RF-net copper'}

        # Gap: exact minimum plus sampled maximum at both trace edges.
        if gtol is None:
            items['gap'] = {'status': 'unknown', 'reason': 'gap tolerance not declared'}
        elif not graded_gap:
            items['gap'] = {'status': 'unknown', 'reason': f'gap tolerance does not exceed the native polygon '
                                                           f'error ({max_error} nm)'}
        else:
            items['gap'] = {'status': 'defect' if violations else 'pass' if graded else 'unknown',
                            'target_mm': spec['gap_mm'], 'tolerance_mm': gtol,
                            'sample_step_mm': spec['sample_step_mm'], 'graded_edge_samples': graded,
                            'transition_edge_samples': transitions, 'minimum': minimum,
                            'violations': _runs(violations)}
            if not graded and not violations:
                items['gap']['reason'] = 'no edge sample away from other RF-net copper'

    # Reference: ground copper on the reference layer under the trace and both declared gaps.
    if unfilled['ground_reference']:
        items['reference'] = {'status': 'unknown', 'reason': 'ground zones on the reference layer are unfilled',
                              'zones': unfilled['ground_reference']}
    else:
        voids, covered, quads, half_strip = [], 0, 0, width_nm / 2 + gap_nm
        for track in on_signal:
            points = polylines[track['uuid']]
            for a, b in zip(points, points[1:]):
                length = math.dist(a, b)
                if length == 0:
                    continue
                quads += 1
                nx, ny = -(b[1] - a[1]) / length * half_strip, (b[0] - a[0]) / length * half_strip
                quad = [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]
                if reference and reference.contains((a[0] / 2 + b[0] / 2, a[1] / 2 + b[1] / 2)):
                    covered += 1
                if not reference or not reference.contains_convex(quad):
                    voids.append({'track': track['uuid'], 'from_nm': [round(v) for v in a],
                                  'to_nm': [round(v) for v in b]})
        absent = bool(voids) and not covered and len(voids) == quads
        items['reference'] = {'status': 'defect' if voids else 'pass', 'strip_half_width_mm': _mm(half_strip),
                              'reason': 'reference_absent' if absent else 'reference_void' if voids else None,
                              'voids': voids}
        if absent:
            items['reference']['statement'] = (f"no {resolved['ground_net']} copper on {ref_layer} under any RF "
                                               "track strip: the grounded-CPW reference is absent")
    return {'status': _status(i['status'] for i in items.values()), 'items': items}


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, key):
        self.parent.setdefault(key, key)
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def _pieces(tracks):
    """Split tracks at T-junctions (an endpoint on another same-layer track's copper)."""
    lines = {t['uuid']: track_polyline(t) for t in tracks}
    cuts = {t['uuid']: set() for t in tracks}
    for t1 in tracks:
        for end in (tuple(t1['start_nm']), tuple(t1['end_nm'])):
            for t2 in tracks:
                if t2 is t1 or t2['layer'] != t1['layer'] or end in (tuple(t2['start_nm']), tuple(t2['end_nm'])):
                    continue
                points = lines[t2['uuid']][0]
                best = min(((point_segment(end, a, b), i) for i, (a, b) in enumerate(zip(points, points[1:]))),
                           key=lambda x: x[0][0])
                (distance, t), i = best
                if distance <= t2['width_nm'] / 2 and 0 < i + t < len(points) - 1:
                    cuts[t2['uuid']].add((i, t))
    pieces = []
    for track in tracks:
        points = lines[track['uuid']][0]
        params = sorted(cuts[track['uuid']])
        boundaries = [(0, 0.0), *params, (len(points) - 2, 1.0)]
        for (i0, t0), (i1, t1) in zip(boundaries, boundaries[1:]):
            def at(i, t):
                a, b = points[i], points[i + 1]
                return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
            chain = [at(i0, t0), *points[i0 + 1:i1 + 1], at(i1, t1)]
            pieces.append({'track': track['uuid'], 'layer': track['layer'], 'width_nm': track['width_nm'],
                           'points': chain, 'length_nm': polyline_length(chain)})
    return pieces


def _dijkstra(adjacency, sources):
    dist, prev, heap = {}, {}, [(0.0, s) for s in sources]
    for s in sources:
        dist[s] = 0.0
    heapq.heapify(heap)
    while heap:
        d, node = heapq.heappop(heap)
        if d > dist.get(node, math.inf):
            continue
        for other, edge, length in adjacency.get(node, ()):
            nd = d + length
            if nd < dist.get(other, math.inf):
                dist[other], prev[other] = nd, (node, edge)
                heapq.heappush(heap, (nd, other))
    return dist, prev


def evaluate_topology(resolved, native):
    branches = resolved['topology']
    tracks = native['tracks']
    declared = [b['terminal'] for b in branches] + [branches[0]['from_terminal']]
    present = set(native.get('all_rf_net_pads', []))
    findings = []
    undeclared = sorted(present - set(declared))
    absent = sorted(set(declared) - present)
    if undeclared:
        findings.append({'status': 'defect', 'reason': 'undeclared_terminal', 'terminals': undeclared})
    if absent:
        findings.append({'status': 'defect', 'reason': 'terminal_absent_from_net', 'terminals': absent})
    if native['rf_zones']:
        findings.append({'status': 'unknown', 'reason': 'RF-net zone copper is not modelled',
                         'zones': native['rf_zones']})
    if not tracks:
        findings.append({'status': 'unknown', 'reason': 'no routed RF copper'})
        branches = [{'name': b['name'], 'terminal': b['terminal'], 'max_length_mm': b['max_length_mm'],
                     'status': 'unknown', 'reason': 'no routed RF copper'} for b in branches]
        return {'status': _status([f['status'] for f in findings] + ['unknown']), 'findings': findings,
                'branches': branches, 'paths': {}}

    pieces = _pieces(tracks)
    uf = _UnionFind()
    ends = []
    for index, piece in enumerate(pieces):
        for which in (0, -1):
            key = ('end', index, which)
            uf.find(key)
            ends.append((key, piece['layer'], piece['points'][which], piece['width_nm']))
    for i, (k1, l1, p1, w1) in enumerate(ends):
        for k2, l2, p2, w2 in ends[i + 1:]:
            if l1 == l2 and math.dist(p1, p2) <= min(w1, w2) / 2:
                uf.union(k1, k2)
    for pad in native['pads']:
        key = ('pad', f"{pad['ref']}.{pad['pad']}")
        uf.find(key)
        for layer, polyset in pad['polygons_nm'].items():
            shape = EdgeSet([polyset])
            for k, l, p, _ in ends:
                if l == layer and shape.contains(p):
                    uf.union(k, key)
    rf_vias = [v for v in native['vias'] if v['net'] == resolved['net']]
    for via in rf_vias:
        key = ('via', via['uuid'])
        uf.find(key)
        for k, l, p, _ in ends:
            if l in via['copper_layers'] and math.dist(p, via['position_nm']) <= via['width_nm'] / 2:
                uf.union(k, key)
        for pad in native['pads']:
            if any(layer in via['copper_layers'] and EdgeSet([ps]).contains(tuple(via['position_nm']))
                   for layer, ps in pad['polygons_nm'].items()):
                uf.union(key, ('pad', f"{pad['ref']}.{pad['pad']}"))
    adjacency, self_loops = {}, []
    for index, piece in enumerate(pieces):
        a, b = uf.find(('end', index, 0)), uf.find(('end', index, -1))
        if a == b:
            self_loops.append(index)
            continue
        adjacency.setdefault(a, []).append((b, index, piece['length_nm']))
        adjacency.setdefault(b, []).append((a, index, piece['length_nm']))
    via_roots = {uf.find(('via', v['uuid'])) for v in rf_vias}

    def path(prev, node):
        nodes, edges = [node], []
        while node in prev:
            node, edge = prev[node]
            nodes.append(node)
            edges.append(edge)
        return nodes[::-1], edges[::-1]

    results, used, unrouted, paths = [], {}, [], {}
    electrical = resolved.get('electrical_length')
    for branch in branches:
        target = ('pad', branch['terminal'])
        record = {'name': branch['name'], 'terminal': branch['terminal'],
                  'max_length_mm': branch['max_length_mm']}
        if branch['name'] == 'trunk':
            record['from'] = branch['from_terminal']
            source = ('pad', branch['from_terminal'])
            sources = [uf.find(source)] if source[1] in present else []
        else:
            record['from'] = branch['parent']
            sources = paths.get(branch['parent'], {}).get('nodes', [])
        if target[1] not in present or not sources:
            record.update(status='unknown', reason='terminal or parent branch is not routed')
            unrouted.append(branch['name'])
            results.append(record)
            continue
        dist, prev = _dijkstra(adjacency, sources)
        end = uf.find(target)
        if end not in dist:
            record.update(status='unknown', reason='terminal is not connected by routed copper')
            unrouted.append(branch['name'])
            results.append(record)
            continue
        nodes, edges = path(prev, end)
        chain = []
        for node, edge in zip(nodes, edges):
            points = pieces[edge]['points']
            first, last = uf.find(('end', edge, 0)), uf.find(('end', edge, -1))
            oriented = points if first == node else points[::-1]
            chain += oriented if not chain else oriented[1:]
        paths[branch['name']] = {'nodes': nodes, 'edges': edges, 'points': chain,
                                 'tracks': sorted({pieces[e]['track'] for e in edges})}
        for edge in edges:
            used.setdefault(edge, []).append(branch['name'])
        length = dist[end]
        record.update(length_mm=_mm(length), junction=None if branch['name'] == 'trunk' else
                      {'at_nm': [round(v) for v in chain[0]] if chain else None},
                      via_transitions=sum(1 for n in nodes if n in via_roots),
                      tracks=sorted({pieces[e]['track'] for e in edges}))
        if electrical and electrical['effective_permittivity'] is None:
            record['electrical_length_deg'] = None
        elif electrical:
            record['electrical_length_deg'] = round(
                360 * (length / 1e9) * electrical['frequency_hz'] * math.sqrt(electrical['effective_permittivity'])
                / LIGHT_M_PER_S, 4)
        if branch['max_length_mm'] is None:
            record.update(status='unknown', reason='no design length bound declared')
        else:
            record['status'] = 'defect' if length > branch['max_length_mm'] * NM else 'pass'
        results.append(record)
    overlap = sorted({e for e, names in used.items() if len(names) > 1})
    if overlap:
        findings.append({'status': 'defect', 'reason': 'branch_overlap',
                         'statement': 'routed copper is shared by more than one declared branch; declare the '
                                      'nested stub with from',
                         'tracks': sorted({pieces[e]['track'] for e in overlap})})
    on_paths = {n for p in paths.values() for n in p['nodes']}
    extra = [i for i in range(len(pieces)) if i not in used and not
             (i in self_loops and uf.find(('end', i, 0)) in on_paths)]
    if extra:
        entry = {'reason': 'undeclared_copper', 'tracks': sorted({pieces[i]['track'] for i in extra}),
                 'statement': 'RF-net copper outside every declared branch (dangling stub, loop or island)'}
        findings.append({**entry, 'status': 'unknown' if unrouted else 'defect',
                         **({'qualifier': 'some branches are unrouted'} if unrouted else {})})
    status = _status([f['status'] for f in findings] + [r['status'] for r in results])
    return {'status': status, 'findings': findings, 'branches': results, 'paths': paths, 'pieces': pieces}


def evaluate_stitching(resolved, native, topology):
    spec = resolved['stitching']
    signal, reference = resolved['signal_layer'], resolved['reference_layer']
    inner, outer = (v * NM for v in spec['band_mm'])
    vias = [v for v in native['vias'] if v['net'] == resolved['ground_net'] and
            signal in v['copper_layers'] and reference in v['copper_layers']]
    branches = []
    for name in spec['branches']:
        path = topology['paths'].get(name)
        if path is None or len(path['points']) < 2:
            branches.append({'branch': name, 'status': 'unknown', 'reason': 'branch path is not routed'})
            continue
        points = path['points']
        cumulative = [0.0]
        for a, b in zip(points, points[1:]):
            cumulative.append(cumulative[-1] + math.dist(a, b))
        total = cumulative[-1]
        sides = {'left': [], 'right': []}
        for via in vias:
            p = tuple(via['position_nm'])
            best = None
            for i, (a, b) in enumerate(zip(points, points[1:])):
                d, t = point_segment(p, a, b)
                if best is None or d < best[0]:
                    best = (d, t, i)
            d, t, i = best
            s = cumulative[i] + t * (cumulative[i + 1] - cumulative[i])
            if s <= 0 or s >= total or not inner <= d <= outer:
                continue
            a, b = points[i], points[i + 1]
            direction = _sub(b, a)
            if t >= 1.0 and i + 1 < len(points) - 1:
                direction = (direction[0] + points[i + 2][0] - b[0], direction[1] + points[i + 2][1] - b[1])
            elif t <= 0.0 and i > 0:
                direction = (direction[0] + a[0] - points[i - 1][0], direction[1] + a[1] - points[i - 1][1])
            side = 'left' if _cross(direction, _sub(p, a if t < 1.0 else b)) > 0 else 'right'
            sides[side].append((s, via['uuid']))
        record = {'branch': name, 'length_mm': _mm(total), 'max_pitch_mm': spec['max_pitch_mm'], 'sides': {}}
        statuses = []
        for side, found in sides.items():
            found.sort()
            stops = [0.0, *[s for s, _ in found], total]
            gaps = [(b - a, a, b) for a, b in zip(stops, stops[1:])]
            worst = max(gaps)
            entry = {'vias': [u for _, u in found], 'max_gap_mm': _mm(worst[0])}
            if spec['max_pitch_mm'] is None:
                entry['status'] = 'unknown'
            else:
                over = [{'from_mm': _mm(a), 'to_mm': _mm(b), 'gap_mm': _mm(g)}
                        for g, a, b in gaps if g > spec['max_pitch_mm'] * NM]
                entry.update(status='defect' if over else 'pass', violations=over)
            statuses.append(entry['status'])
            record['sides'][side] = entry
        record['status'] = _status(statuses)
        if spec['max_pitch_mm'] is None:
            record['reason'] = 'stitching pitch not declared'
        branches.append(record)
    return {'status': _status(b['status'] for b in branches), 'band_mm': spec['band_mm'],
            'stitching_vias_considered': len(vias), 'branches': branches}


def evaluate(resolved, native):
    _validate_native(native, resolved)
    sections = {}
    if 'cpw' in resolved:
        sections['cpw'] = evaluate_cpw(resolved, native)
    if 'topology' in resolved:
        topology = evaluate_topology(resolved, native)
        if 'stitching' in resolved:
            sections['stitching'] = evaluate_stitching(resolved, native, topology)
        topology.pop('pieces', None)
        topology['paths'] = {name: {'tracks': sorted({t for t in p.get('tracks', [])}), 'points': len(p['points'])}
                             for name, p in topology.get('paths', {}).items()}
        sections['topology'] = topology
    if not native['nets_present'].get('ground_net') and ('cpw' in sections or 'stitching' in sections):
        sections['binding'] = {'status': 'unknown', 'reason': 'declared ground net absent from board'}
    return {'status': _status(s['status'] for s in sections.values()), 'sections': sections}


def margin_nm(resolved):
    values = [1.0]
    if 'cpw' in resolved:
        cpw = resolved['cpw']
        values.append(cpw['width_mm'] / 2 + cpw['gap_mm'] + (cpw['gap_tolerance_mm'] or 0) + cpw['search_mm'])
    if 'stitching' in resolved:
        values.append(resolved['stitching']['band_mm'][1])
    return math.ceil((max(values) + 1.0) * NM)


def gate_findings(resolved, evaluation):
    """(entity, kind) identities of determinate defects; unknowns are not findings."""
    net, found = resolved['net'], []
    sections = evaluation['sections']
    for name, item in sections.get('cpw', {}).get('items', {}).items():
        if item['status'] == 'defect':
            found.append((f'{net}:cpw', f'rf-geometry/cpw-{name}'))
    for branch in sections.get('stitching', {}).get('branches', []):
        if branch['status'] == 'defect':
            found.append((f"{net}:{branch['branch']}", 'rf-geometry/stitching-pitch'))
    topology = sections.get('topology', {})
    for finding in topology.get('findings', []):
        if finding['status'] == 'defect':
            found.append((f'{net}:topology', f"rf-geometry/{finding['reason'].replace('_', '-')}"))
    for branch in topology.get('branches', []):
        if branch['status'] == 'defect':
            found.append((f"{net}:{branch['name']}", 'rf-geometry/branch-length'))
    return found


# ------------------------------------------------------------------ check

LIMITATIONS = ('Planar saved-board copper geometry against a declared structure only; this is not RF '
               'qualification. No field solver, TDR, VNA or installed measurement is claimed, and no proxy '
               '(guard width, image-current fraction) is used. Maximum gap is sampled at the declared step; '
               'minimum gap and reference-strip containment are exact on pcbnew polygons within max_error. '
               'Edge samples within gap+tolerance of other RF-net copper (bends, junctions, launches) are '
               'transitions exempt from the maximum-gap test. Branch length is routed track length; via '
               'barrels are counted, not measured. Stitching vias are graded by net, layer span and position, '
               'not by zone connection.')


def check_rf_geometry(ctx):
    from .checks import CheckError
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    resolved = descriptor['resolved']
    board = ctx.candidate_path(ctx.scope['board'])
    request = {'net': resolved['net'], 'ground_net': resolved['ground_net'],
               'signal_layer': resolved['signal_layer'], 'reference_layer': resolved['reference_layer'],
               'margin_nm': margin_nm(resolved)}
    write_json(ctx.work / 'rf-request.json', request)
    ctx.evidence('rf-request.json')
    ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.rf_geometry', board,
                 '--request', '/work/rf-request.json', '--out', '/work/rf-native.json'], timeout=600)
    ctx.evidence('rf-native.json')
    native = ctx.load_json('rf-native.json', 'native RF geometry')
    try:
        if native.get('board_sha256') != ctx.manifest['files'][ctx.scope['board']]:
            raise ValueError('native RF geometry does not bind the candidate board')
        evaluation = evaluate(resolved, native)
    except (ValueError, TypeError, KeyError) as exc:
        raise CheckError('malformed', str(exc)) from exc
    report = {'schema': RESULT_SCHEMA, 'candidate_id': ctx.manifest['candidate_id'],
              'board_sha256': native['board_sha256'],
              'declaration_sha256': digest(ctx.inputs / ctx.params['declaration']),
              'resolved': resolved, **evaluation}
    write_json(ctx.work / 'rf-geometry.json', report)
    ctx.evidence('rf-geometry.json')
    ctx.result['details'].update(report)
    ctx.result['limitations'].append(LIMITATIONS)
    ctx.result['details']['gate_findings'] = [{'check_id': ctx.cid, 'entity': entity, 'kind': kind}
                                              for entity, kind in gate_findings(resolved, evaluation)]
    counts = {s: sum(1 for v in evaluation['sections'].values() if v['status'] == s)
              for s in ('defect', 'unknown', 'pass')}
    ctx.result.update(execution='unsupported' if evaluation['status'] == 'unknown' else 'ok',
                      outcome=evaluation['status'],
                      summary=f"RF geometry {evaluation['status']}: {counts['defect']} defect, "
                              f"{counts['unknown']} unknown, {counts['pass']} pass section(s)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board')
    parser.add_argument('--request', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    request = json.loads(Path(args.request).read_text())
    write_json(args.out, extract(args.board, request))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""ESP32-S3-WROOM-1 outside-board antenna placement: a bare-board fabrication gate.

AGENTEE-453. The selected case is Espressif ESP32-S3 Hardware Design Guidelines
section 1.4.7, physical page 33 (printed 30), Fig. 24 (feed point on the right)
and page 34 Fig. 25 (feed point on the left). Espressif's text: "It is suggested
to place the module's on-board PCB antenna outside the base board, and the feed
point of the antenna close to the edge of the base board. In the following
example figures, positions with mark ✓ are strongly recommended, while positions
without a mark are not recommended."

The saved board is measured natively (pcbnew, in the networkless sandbox) and
graded here against module-frame geometry from the hash-pinned module datasheet
(Figure 11-1 dimensions, Figure 10-1 feed-point side) and the captured KiCad
footprint. Every criterion is exact polygon arithmetic on saved geometry.
Espressif prints no corner distance for the Fig. 24/25 positions, so
manufacturer corner compliance is always unknown; the feed-side corner is
graded only under a declared, agent-derived class-default policy (CORNER_POLICY)
and every verdict that depends on it carries that authority class, never the
manufacturer's. No clearance is derived from the guidelines and the Figure
11-1 pin label 15 is never a clearance (AGENTEE-211). A missing vendor source,
footprint or edge declaration is unknown, never pass. Housing pocket, nearby
metal, mechanical support and installed RF performance stay physical/bench
obligations outside this gate.
"""

from .check_registry import register as _register_check
from . import check_prerequisites as _requires
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path, PurePosixPath

from .catalog import exact_fields, nonempty, strict_json
from . import footprint_library as fl
from .footprint_library import (ESP32_FOOTPRINT_ID, FootprintError, antenna_placement_context,
                                esp32_s3_wroom_1_datasheet_facts, esp32_s3_wroom_1_placement_policy)
from .storage import digest, write_json
from .symbol_library import nanometres

DECLARATION_SCHEMA = 'agent-ee.antenna-placement-declaration/1'
RESULT_SCHEMA = 'agent-ee.antenna-placement/1'
SECTIONS = ('sources', 'footprint', 'antenna_outside_board', 'feed_point_edge', 'feed_side_corner',
            'keepout_retained', 'keepout_clear')
FORBID = ('footprints', 'pads', 'tracks', 'vias', 'zone_fills')
SCOPE = ('Bare-board fabrication gate for the declared ESP32-S3-WROOM-1 outside-board placement case only; '
         'not installed, enclosure or RF qualification.')
LIMITATIONS = (SCOPE + ' Housing pocket and the guidelines\' separate 15 mm in-housing clearance, nearby metal, '
               'mechanical support and installed throughput/range testing remain open physical and bench '
               'obligations. Espressif prints no corner distance: manufacturer corner compliance is unknown, '
               'and the feed-side corner is graded only by the declared agent-derived class-default slot '
               '(module body width), which is neither an Espressif acceptance distance nor a clearance.')
# The guidelines snapshot locates the whole case; quotes are Espressif's text, verified by page.
GUIDANCE = dict(
    section='1.4.7 General Principles of PCB Layout for Modules (Positioning a Module on a Base Board)',
    page=33, printed_page=30, figures=[
        dict(page=33, printed_page=30, figure='Fig. 24: Placement of ESP32-S3 Modules on Base Board '
                                              '(antenna feed point on the right)', marked=[3, 4]),
        dict(page=34, printed_page=31, figure='Fig. 25: Placement of ESP32-S3 Modules on Base Board '
                                              '(antenna feed point on the left)', marked=[1, 5])],
    quote=('It is suggested to place the module’s on-board PCB antenna outside the base board, and the '
           'feed point of the antenna close to the edge of the base board. In the following example figures, '
           'positions with mark ✓ are strongly recommended, while positions without a mark are not '
           'recommended.'))
# Module datasheet v1.8 Figure 10-1 top view draws the antenna trace joining the module body at its
# pin-40 (+x) end, the same drawing as guidelines Fig. 24 "feed point on the right".
FEED_POINT = dict(side='+x', meaning='right in top view, pin 40 side', sources=[
    dict(document='module_datasheet', page=42, printed_page=42,
         locator='Figure 10-1. ESP32-S3-WROOM-1 Physical Dimensions, Top View: antenna trace feed leg at '
                 'the right end of the 6 mm antenna area'),
    dict(document='guidance', page=33, printed_page=30,
         locator='Fig. 24 (antenna feed point on the right): same antenna drawing, "Feed Point" label')])
# Agent-derived class default (AGENTEE-453 review H1): the 18 mm slot is the module body width from
# datasheet Figure 11-1, chosen by the agent to separate corner from mid-edge positions. Fig. 24/25
# dimension no corner gap, so this is not manufacturer authority and cannot yield a manufacturer verdict.
CORNER_POLICY = dict(
    id='agent-ee.esp32-outside-board-corner-slot/1', authority='agent-derived-class-default',
    slot='module body width (datasheet Figure 11-1, 18 mm)',
    rule=('pass when the feed-side gap along the crossed edge is below the slot and no larger than the other '
          'side; defect when it is at least the slot'),
    basis=('Agent-derived from the module width. Espressif Fig. 24/25 mark corner positions but print no corner '
           'distance; this is not a manufacturer acceptance distance.'))
MANUFACTURER = 'manufacturer-sourced'
CLASS_DEFAULT = CORNER_POLICY['authority']
# Authority of each section's verdict. Geometry sections grade sourced figures and captured files.
AUTHORITY = {name: MANUFACTURER for name in SECTIONS}
OBLIGATIONS = {'housing-pocket': 'physical', 'housing-clearance': 'physical', 'nearby-metal': 'physical',
               'support': 'physical', 'rf-performance': 'bench'}


def _rel(value, label):
    rel = nonempty(value)
    path = PurePosixPath(rel)
    if path.is_absolute() or '..' in path.parts or str(path) != rel or '\\' in rel:
        raise ValueError(f'{label} must be a safe candidate-relative path')
    return rel


def _nm(value):
    return nanometres(repr(value) if isinstance(value, float) else str(value))


def _blob(value, label, pin):
    exact_fields(value, ('path', 'sha256'))
    _rel(value['path'], label)
    if value['sha256'] != pin:
        raise ValueError(f'{label} sha256 differs from the reviewed pin')
    return value


def module_geometry():
    """Module-frame body and antenna rectangles (nm) from the pinned Figure 11-1 facts."""
    facts = {f['id']: f for f in esp32_s3_wroom_1_datasheet_facts()['facts']}
    width = nanometres(facts['esp32_s3_wroom_1_body_width_mm']['value'])
    length = nanometres(facts['esp32_s3_wroom_1_body_length_mm']['value'])
    depth = nanometres(facts['esp32_s3_wroom_1_antenna_depth_mm']['value'])
    x, top = width // 2, -(length // 2)
    return dict(width_nm=width, antenna=[-x, top, x, top + depth], body=[-x, top + depth, x, length // 2],
                facts=sorted(facts))


def parse_declaration(raw):
    """Exact declaration; the policy must be the reviewed outside-board snapshot."""
    doc = strict_json(raw)
    exact_fields(doc, ('schema', 'reference', 'policy', 'module_datasheet', 'footprint', 'edge', 'corner_policy'))
    if doc['schema'] != DECLARATION_SCHEMA:
        raise ValueError('antenna placement declaration schema required')
    nonempty(doc['reference'])
    policy = doc['policy']
    if not isinstance(policy, dict) or not isinstance(policy.get('source'), dict):
        raise ValueError('antenna placement policy required')
    path = _rel(policy['source'].get('path'), 'guidance source')
    try:
        expected = esp32_s3_wroom_1_placement_policy('outside_board', pdf_path=path)
    except FootprintError as exc:
        raise ValueError(str(exc)) from exc
    if policy != expected:
        raise ValueError('antenna placement policy must be the reviewed outside_board case and snapshot')
    _blob(doc['module_datasheet'], 'module datasheet', fl.ESP32_DATASHEET_SHA256)
    _blob(doc['footprint'], 'footprint', fl.ESP32_FOOTPRINT_SHA256)
    footprint = PurePosixPath(doc['footprint']['path'])
    if footprint.name != ESP32_FOOTPRINT_ID.split(':')[1] + '.kicad_mod' or footprint.parent.suffix != '.pretty':
        raise ValueError('footprint path must be the captured <lib>.pretty/ESP32-S3-WROOM-1.kicad_mod')
    edge = doc['edge']
    if edge is not None:
        exact_fields(edge, ('start_mm', 'end_mm'))
        points = [edge['start_mm'], edge['end_mm']]
        if any(not isinstance(p, list) or len(p) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                                              for v in p) for p in points):
            raise ValueError('edge endpoints must be [x, y] millimetres')
        nm = [[_nm(v) for v in p] for p in points]
        if nm[0] == nm[1]:
            raise ValueError('edge endpoints must differ')
    if doc['corner_policy'] is not None and doc['corner_policy'] != CORNER_POLICY:
        raise ValueError('corner_policy must be null or exactly the agent-derived class-default corner slot')
    paths = [path, doc['module_datasheet']['path'], doc['footprint']['path']]
    if len(set(paths)) != 3:
        raise ValueError('declared sources must be distinct files')
    return doc


def predicates(doc):
    """Decidable predicates; the corner one exists only under the declared class default and says so."""
    names = []
    for name in SECTIONS:
        label = f"antenna-placement:{doc['reference']}:{name.replace('_', '-')}"
        if name == 'feed_side_corner':
            if doc['corner_policy'] is None:
                continue
            label += ':' + CLASS_DEFAULT
        names.append(label)
    return names


def capability(params, scope, files, inputs):
    """Capture-time contract: present sources must match their pins; absent ones become unknown."""
    if not isinstance(params, dict) or set(params) != {'declaration'}:
        raise ValueError('antenna_placement requires exactly params.declaration')
    board = scope.get('board')
    for rel, label in ((board, 'board'), (params['declaration'], 'declaration')):
        if not isinstance(rel, str) or rel not in files:
            raise ValueError(f'{label} must be a captured input: {rel!r}')
    if inputs is None:
        raise ValueError('antenna_placement requires captured inputs')
    root = Path(inputs)
    doc = parse_declaration((root / params['declaration']).read_bytes())
    sources = dict(guidance=doc['policy']['source'], module_datasheet=doc['module_datasheet'],
                   footprint=doc['footprint'])
    present = {}
    for name, source in sources.items():
        rel = source['path']
        if rel in (board, params['declaration']):
            raise ValueError('declared sources must be distinct captured files')
        present[name] = rel in files
        if present[name] and digest(root / rel) != source['sha256']:
            raise ValueError(f'captured {name} differs from its declared sha256: {rel}')
    immutable = [params['declaration'], *(s['path'] for n, s in sources.items() if present[n])]
    return {'immutable_inputs': sorted(immutable), 'predicates': predicates(doc), 'declaration': doc,
            'present': present}


# ------------------------------------------------------------------ pure frame transforms

def quarter_turns(orientation_deg):
    turns = Decimal(str(orientation_deg)) / 90
    if turns != turns.to_integral_value():
        return None
    return int(turns) % 4


def to_board(point, anchor, turns):
    """KiCad footprint rotation: +90 degrees maps local (x, y) to (y, -x) (y axis down)."""
    x, y = point
    for _ in range(turns):
        x, y = y, -x
    return [anchor[0] + x, anchor[1] + y]


def to_local(point, anchor, turns):
    x, y = point[0] - anchor[0], point[1] - anchor[1]
    for _ in range(turns):
        x, y = -y, x
    return [x, y]


def rect_points(rect, anchor, turns):
    x0, y0, x1, y1 = rect
    return [to_board(p, anchor, turns) for p in ([x0, y0], [x1, y0], [x1, y1], [x0, y1])]


# ------------------------------------------------------------------ native probe (sandbox)

def _poly(pcbnew, points):
    poly = pcbnew.SHAPE_POLY_SET()
    poly.NewOutline()
    for x, y in points:
        poly.Append(int(x), int(y))
    return poly


def _area(pcbnew, a, b, op):
    result = pcbnew.SHAPE_POLY_SET(a)
    getattr(result, op)(b)
    return 0 if result.IsEmpty() else int(round(result.Area()))


def _pad_key(pad):
    import pcbnew
    return [pad.GetNumber(), [int(pad.GetPosition().x), int(pad.GetPosition().y)],
            [int(pad.GetSize().x), int(pad.GetSize().y)], int(pad.GetShape()),
            sorted(pcbnew.LayerName(layer) for layer in pad.GetLayerSet().Seq())]


def _pads(fp):
    return sorted(_pad_key(p) for p in fp.Pads())


def pad_geometry_mismatches(fp, expected, layers, max_error):
    """Pads whose native copper polygon differs from the captured footprint's at the same pose.

    The canonical pad record omits orientation and shape detail, so each pad's copper is compared
    natively on every enabled copper layer it occupies (AGENTEE-453 review H3).
    """
    import pcbnew
    actual, captured = (sorted(f.Pads(), key=lambda p: json.dumps(_pad_key(p))) for f in (fp, expected))
    if len(actual) != len(captured):
        return ['pad count']
    found = []
    for a, b in zip(actual, captured):
        for layer in layers:
            on = (a.IsOnLayer(layer), b.IsOnLayer(layer))
            if on == (False, False):
                continue
            polys = []
            for pad in (a, b):
                poly = pcbnew.SHAPE_POLY_SET()
                if pad.IsOnLayer(layer):
                    pad.TransformShapeToPolygon(poly, layer, 0, max_error, pcbnew.ERROR_OUTSIDE)
                polys.append(poly)
            if _area(pcbnew, polys[0], polys[1], 'BooleanSubtract') or _area(
                    pcbnew, polys[1], polys[0], 'BooleanSubtract') or polys[0].IsEmpty() != polys[1].IsEmpty():
                found.append(dict(pad=a.GetNumber(), layer=pcbnew.LayerName(layer)))
    return found


def extract(board_path, request):
    """Measure the saved board; graded later by evaluate(). Never writes the board."""
    import pcbnew
    from .placement_probe import rule_area
    raw = Path(board_path).read_bytes()
    board = pcbnew.LoadBoard(str(board_path))
    reference, geometry = request['reference'], request['geometry']
    enabled = sorted(pcbnew.LayerName(layer) for layer in board.GetEnabledLayers().CuStack())
    out = dict(board_sha256=hashlib.sha256(raw).hexdigest(), kicad_version=pcbnew.Version(),
               copper_layers=enabled, reference=reference)
    outline = pcbnew.SHAPE_POLY_SET()
    if board.GetBoardPolygonOutlines(outline, False) and outline.OutlineCount() == 1:
        chain = outline.COutline(0)
        out['outline'] = dict(points=[[int(chain.CPoint(i).x), int(chain.CPoint(i).y)]
                                      for i in range(chain.PointCount())], holes=outline.HoleCount(0))
    else:
        out['outline'] = None
    matches = [fp for fp in board.GetFootprints() if fp.GetReference() == reference]
    out['footprint_count'] = len(matches)
    if len(matches) != 1:
        return out
    fp = matches[0]
    anchor = [int(fp.GetPosition().x), int(fp.GetPosition().y)]
    pose = dict(fpid=fp.GetFPIDAsString(), anchor_nm=anchor, orientation_deg=fp.GetOrientationDegrees(),
                flipped=bool(fp.IsFlipped()))
    out['pose'] = pose
    turns = quarter_turns(pose['orientation_deg'])
    out['quarter_turns'] = turns
    footprint = Path(request['footprint'])
    expected = pcbnew.FootprintLoad(str(footprint.parent), footprint.stem)
    if expected is None:
        raise ValueError('captured footprint failed native load')
    expected.SetPosition(pcbnew.VECTOR2I(*anchor))
    expected.SetOrientationDegrees(pose['orientation_deg'])

    def zones(item):
        result = []
        for zone in item.Zones():
            area = rule_area(zone, reference) if zone.GetIsRuleArea() else dict(
                owner=reference, rule_area=False, layers=[])
            area.pop('uuid', None)
            result.append(area)
        return result
    expected_zones = zones(expected)
    for area in expected_zones:
        # KiCad saves a library all-copper zone onto the enabled board layers only.
        area['layers'] = sorted(set(area['layers']) & set(enabled))
    copper = list(board.GetEnabledLayers().CuStack())
    geometry_mismatches = pad_geometry_mismatches(fp, expected, copper, int(board.GetDesignSettings().m_MaxError))
    out['identity'] = dict(pads_equal=_pads(fp) == _pads(expected) and not geometry_mismatches,
                           pad_geometry_mismatches=geometry_mismatches, actual_zones=zones(fp),
                           expected_zones=expected_zones)
    if pose['flipped'] or turns is None:
        return out
    # The pure transform must reproduce pcbnew's own pad placement before it grades anything.
    for pad in expected.Pads():
        local = pad.GetFPRelativePosition()
        if to_board([int(local.x), int(local.y)], anchor, turns) != [int(pad.GetPosition().x),
                                                                      int(pad.GetPosition().y)]:
            raise ValueError('module-frame transform disagrees with native pad placement')
    board_poly = pcbnew.SHAPE_POLY_SET(outline) if out['outline'] else None
    antenna = _poly(pcbnew, rect_points(geometry['antenna'], anchor, turns))
    body = _poly(pcbnew, rect_points(geometry['body'], anchor, turns))
    if board_poly is not None:
        out['areas'] = dict(antenna_on_board_nm2=_area(pcbnew, antenna, board_poly, 'BooleanIntersection'),
                            body_off_board_nm2=_area(pcbnew, body, board_poly, 'BooleanSubtract'))
    keepout = [z for z in expected.Zones() if z.GetIsRuleArea()]
    if len(keepout) == 1:
        zone = pcbnew.SHAPE_POLY_SET(keepout[0].Outline())
        out['antenna_outside_keepout_nm2'] = _area(pcbnew, antenna, zone, 'BooleanSubtract')
        out['intrusions'] = intrusions(board, zone, keepout[0])
    return out


def intrusions(board, zone_poly, zone):
    """Saved copper (fills, tracks, vias, pads, graphics) inside the captured keepout polygon."""
    import pcbnew
    layers = [layer for layer in board.GetEnabledLayers().CuStack() if zone.IsOnLayer(layer)]
    items = [*board.GetTracks(), *board.Zones(), *board.GetDrawings()]
    for fp in board.GetFootprints():
        items.extend([*fp.Pads(), *fp.Zones(), *fp.GraphicalItems()])
    found = []
    for item in items:
        kind = item.GetClass()
        if kind == 'ZONE' and item.GetIsRuleArea():
            continue
        hit = []
        for layer in layers:
            if not item.IsOnLayer(layer):
                continue
            if kind in ('PAD', 'PCB_VIA') and not item.FlashLayer(layer):
                continue
            copper = pcbnew.SHAPE_POLY_SET()
            if kind == 'ZONE':
                if item.HasFilledPolysForLayer(layer):
                    copper.Append(item.GetFilledPolysList(layer))
            else:
                item.TransformShapeToPolygon(copper, layer, 0, int(board.GetDesignSettings().m_MaxError),
                                             pcbnew.ERROR_OUTSIDE)
            if not copper.IsEmpty():
                copper.BooleanIntersection(zone_poly)
                if not copper.IsEmpty():
                    hit.append(pcbnew.LayerName(layer))
        if hit:
            parent = item.GetParentFootprint() if hasattr(item, 'GetParentFootprint') else None
            found.append(dict(uuid=item.m_Uuid.AsString(), native_class=kind, layers=hit,
                              owner=parent.GetReference() if parent else None))
    return sorted(found, key=lambda f: f['uuid'])


# ------------------------------------------------------------------ coordinator evaluation

def _section(status, reason, **measured):
    return dict(status=status, reason=reason, **measured)


def _edge_segments(points, anchor, turns):
    """Outline edges in the module frame, collinear runs merged into one straight edge."""
    ring = []
    for p in (to_local(p, anchor, turns) for p in points):
        if not ring or p != ring[-1]:
            ring.append(p)
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    changed = True
    while changed and len(ring) > 3:
        changed = False
        for i, b in enumerate(ring):
            a, c = ring[i - 1], ring[(i + 1) % len(ring)]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            dot = (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1])
            if cross == 0 and dot > 0:
                del ring[i]
                changed = True
                break
    return [(a, b) for a, b in zip(ring, ring[1:] + ring[:1])]


def _covering(segments, y, x0, x1):
    """Straight outline segments on module-frame line y spanning [x0, x1]."""
    return [(min(a[0], b[0]), max(a[0], b[0]), a, b) for a, b in segments
            if a[1] == b[1] == y and min(a[0], b[0]) <= x0 and max(a[0], b[0]) >= x1]


def evaluate(doc, native, present, geometry):
    """Grade the native measurement; defects only from exact saved geometry."""
    sections = {name: _section('unknown', 'not evaluated') for name in SECTIONS}
    missing = [name for name in ('guidance', 'module_datasheet', 'footprint') if not present[name]]
    sections['sources'] = (_section('unknown', 'source not captured: ' + ', '.join(missing), missing=missing)
                           if missing else _section('pass', 'guidance, module datasheet and footprint '
                                                    'captured at their reviewed sha256 pins'))
    fp_count = native.get('footprint_count')
    if fp_count != 1:
        sections['footprint'] = _section('defect', f"expected exactly one {doc['reference']}, found {fp_count}")
        return _overall(sections)
    pose = native['pose']
    identity = native.get('identity')
    if pose['fpid'] != ESP32_FOOTPRINT_ID:
        sections['footprint'] = _section('defect', 'footprint identity differs: ' + pose['fpid'], pose=pose)
    elif identity is None:
        sections['footprint'] = _section('unknown', 'captured footprint not measured', pose=pose)
    elif pose['flipped'] or native.get('quarter_turns') is None:
        sections['footprint'] = _section('unknown', 'only front-side quarter-turn poses are graded', pose=pose)
    elif not identity['pads_equal']:
        sections['footprint'] = _section('defect', 'saved pads differ from the captured footprint at this pose',
                                         pose=pose, pad_geometry_mismatches=identity['pad_geometry_mismatches'])
    else:
        sections['footprint'] = _section('pass', 'saved pads and their native copper equal the captured footprint '
                                         'at the saved pose', pose=pose)
    if identity is not None and sections['footprint']['status'] != 'unknown':
        sections['keepout_retained'] = _retained(identity, native['copper_layers'])
    if sections['footprint']['status'] != 'pass':
        return _overall(sections)
    if 'antenna_outside_keepout_nm2' in native:
        intruders = native['intrusions']
        sections['keepout_clear'] = (
            _section('defect', 'saved copper inside the captured module keepout', items=intruders)
            if intruders else _section('pass', 'no saved copper inside the captured module keepout'))
        if native['antenna_outside_keepout_nm2']:
            sections['keepout_retained'] = _section('defect', 'captured keepout does not cover the antenna area')
    else:
        sections['keepout_clear'] = _section('unknown', 'captured footprint has no single keepout rule area')
    areas = native.get('areas')
    if native.get('outline') is None or areas is None:
        sections['antenna_outside_board'] = _section('unknown', 'board outline is not one closed contour')
        return _overall(sections)
    anchor, turns = pose['anchor_nm'], native['quarter_turns']
    if areas['antenna_on_board_nm2']:
        sections['antenna_outside_board'] = _section(
            'defect', 'antenna area overlaps board material', antenna_on_board_nm2=areas['antenna_on_board_nm2'])
        return _overall(sections)
    sections['antenna_outside_board'] = _section('pass', 'antenna area lies entirely beyond the board outline')
    ax0, ay0, ax1, ay1 = geometry['antenna']
    segments = _edge_segments(native['outline']['points'], anchor, turns)
    feed_line = ay1  # antenna/body boundary; the feed point lies on it
    if areas['body_off_board_nm2']:
        beyond = sorted({a[1] for a, b in segments if a[1] == b[1] and a[1] > feed_line
                         and _covering([(a, b)], a[1], ax0, ax1)})
        found = _covering(segments, beyond[0], ax0, ax1) if beyond else []
        sections['feed_point_edge'] = _section(
            'unknown', 'module body extends beyond the board, so the feed point is off the edge; the guidance '
                       'gives no numeric bound for "close to the edge"', body_off_board_nm2=areas['body_off_board_nm2'],
            feed_point_offset_nm=(beyond[0] - feed_line) if beyond else None)
    else:
        found = _covering(segments, feed_line, ax0, ax1)
        sections['feed_point_edge'] = (
            _section('pass', 'antenna/body boundary, and with it the feed point, lies on the board edge',
                     feed_point_offset_nm=0) if len(found) == 1 else
            _section('unknown', 'board edge at the antenna is not one straight outline segment'))
    if len(found) != 1:
        return _overall(sections)
    x0, x1, a, b = found[0]
    edge = doc['edge']
    if edge is None:
        sections['feed_side_corner'] = _section('unknown', 'no overhang edge declared')
        return _overall(sections)
    declared = sorted(to_local([_nm(v) for v in p], anchor, turns)
                      for p in (edge['start_mm'], edge['end_mm']))
    if declared != sorted([a, b]):
        if any(sorted([p, q]) == declared for p, q in segments):
            sections['feed_side_corner'] = _section('defect', 'antenna overhangs a different board edge than declared')
        else:
            sections['feed_side_corner'] = _section('unknown', 'declared edge is not a segment of the saved outline')
        return _overall(sections)
    width = geometry['width_nm']
    feed_gap, other_gap = x1 - ax1, ax0 - x0  # FEED_POINT side is +x in the module frame
    measured = dict(feed_side_gap_nm=feed_gap, other_side_gap_nm=other_gap)
    if doc['corner_policy'] is None:
        sections['feed_side_corner'] = _section(
            'unknown', 'Espressif prints no corner distance for Fig. 24/25; manufacturer corner compliance is '
                       'unknown and no class-default corner policy is declared', **measured)
        return _overall(sections)
    graded = dict(authority=CLASS_DEFAULT, policy=CORNER_POLICY['id'], slot_nm=width, **measured)
    if feed_gap < width and feed_gap <= other_gap:
        sections['feed_side_corner'] = _section('pass', 'feed-side gap below the agent-derived class-default slot '
                                                '(module body width) and no larger than the other side; not an '
                                                'Espressif-printed distance', **graded)
    elif feed_gap >= width:
        sections['feed_side_corner'] = _section('defect', 'feed-side gap at or beyond the agent-derived '
                                                'class-default slot (module body width); not an Espressif-printed '
                                                'distance', **graded)
    else:
        sections['feed_side_corner'] = _section('unknown', 'both corners within the class-default slot and the '
                                                'non-feed corner is nearer', **graded)
    return _overall(sections)


def _retained(identity, copper_layers):
    actual, expected = identity['actual_zones'], identity['expected_zones']
    if len(expected) != 1 or expected[0].get('outline', {}).get('status') != 'proven':
        return _section('unknown', 'captured footprint has no single straight keepout rule area')
    if not actual:
        return _section('defect', 'module keepout rule area removed from the saved footprint')
    if actual != expected:
        return _section('defect', 'saved module keepout differs from the captured footprint rule area',
                        actual=actual, expected=expected)
    area = actual[0]
    if sorted(area['layers']) != sorted(copper_layers) or not all(area['forbid'][k] for k in FORBID):
        return _section('defect', 'saved module keepout does not forbid copper and placement on every copper layer')
    return _section('pass', 'saved module keepout equals the captured rule area on every copper layer')


def _status(statuses):
    return 'defect' if 'defect' in statuses else 'unknown' if 'unknown' in statuses else 'pass'


def _overall(sections):
    """Overall status and its authority: a verdict that a class-default section decides is not manufacturer's."""
    status = _status([s['status'] for s in sections.values()])
    sourced = _status([s['status'] if s.get('authority', MANUFACTURER) == MANUFACTURER else 'unknown'
                       for s in sections.values()])
    authorities = {name: s.get('authority', AUTHORITY[name]) for name, s in sections.items()}
    return dict(status=status, verdict_authority=MANUFACTURER if sourced == status else CLASS_DEFAULT,
                authorities=authorities, manufacturer_corner_compliance='unknown', sections=sections)


def gate_findings(doc, evaluation):
    return [(f"{doc['reference']}:{name.replace('_', '-')}", f"antenna-placement/{name.replace('_', '-')}")
            for name, section in evaluation['sections'].items() if section['status'] == 'defect']


def _context(descriptor, inputs):
    doc = descriptor['declaration']
    if not descriptor['present']['guidance']:
        return None
    rel = doc['policy']['source']['path']
    return antenna_placement_context(doc['policy'], {rel: (Path(inputs) / rel).read_bytes()})


def build_report(manifest, params, descriptor, context, native):
    """The candidate-bound report, a pure function of the captured declaration/sources and the native record."""
    doc, present = descriptor['declaration'], descriptor['present']
    board_sha = manifest['files'][manifest['scope']['board']]
    geometry = module_geometry()
    if native is not None and native.get('board_sha256') != board_sha:
        raise ValueError('native antenna measurement does not bind the candidate board')
    evaluation = (evaluate(doc, native, present, geometry) if native is not None else
                  _overall({**{n: _section('unknown', 'captured footprint missing') for n in SECTIONS},
                            'sources': _section('unknown', 'source not captured: footprint',
                                                missing=[n for n, p in present.items() if not p])}))
    if not present['guidance'] or not present['module_datasheet']:
        # Without the guidance and the feed-point source no section can pass.
        for section in evaluation['sections'].values():
            if section['status'] == 'pass':
                section.update(status='unknown', reason='source not captured; measured: ' + section['reason'])
        evaluation = _overall(evaluation['sections'])
    return dict(schema=RESULT_SCHEMA, candidate_id=manifest['candidate_id'], board_sha256=board_sha,
                declaration_sha256=manifest['files'][params['declaration']],
                reference=doc['reference'], placement_case='outside_board', scope=SCOPE,
                guidance=dict(source=doc['policy']['source'], **GUIDANCE), context=context,
                feed_point=FEED_POINT, module_datasheet=doc['module_datasheet'], footprint=doc['footprint'],
                corner_policy=doc['corner_policy'], geometry=geometry, obligations=OBLIGATIONS, **evaluation)


def check_antenna_placement(ctx):
    from .checks import CheckError
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    doc, present = descriptor['declaration'], descriptor['present']
    try:
        context = _context(descriptor, ctx.inputs)
    except FootprintError as exc:
        raise CheckError('malformed', str(exc)) from exc
    geometry = module_geometry()
    board = ctx.candidate_path(ctx.scope['board'])
    if present['footprint']:
        request = dict(reference=doc['reference'], geometry=geometry,
                       footprint=ctx.candidate_path(doc['footprint']['path']))
        write_json(ctx.work / 'antenna-request.json', request)
        ctx.evidence('antenna-request.json')
        ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.antenna_placement', board,
                     '--request', '/work/antenna-request.json', '--out', '/work/antenna-native.json'], timeout=300)
        ctx.evidence('antenna-native.json')
        native = ctx.load_json('antenna-native.json', 'native antenna placement')
    else:
        native = None
    try:
        report = build_report(ctx.manifest, ctx.params, descriptor, context, native)
    except (ValueError, TypeError, KeyError) as exc:
        raise CheckError('malformed', str(exc)) from exc
    write_json(ctx.work / 'antenna-placement.json', report)
    ctx.evidence('antenna-placement.json')
    ctx.result['details'].update(report)
    ctx.result['limitations'].append(LIMITATIONS)
    ctx.result['details']['gate_findings'] = [dict(check_id=ctx.cid, entity=e, kind=k)
                                              for e, k in gate_findings(doc, report)]
    counts = {s: sum(1 for v in report['sections'].values() if v['status'] == s)
              for s in ('defect', 'unknown', 'pass')}
    ctx.result.update(execution='unsupported' if report['status'] == 'unknown' else 'ok',
                      outcome=report['status'],
                      summary=f"ESP32 outside-board antenna placement {report['status']}: "
                              f"{counts['defect']} defect, {counts['unknown']} unknown, {counts['pass']} pass "
                              f"(verdict authority {report['verdict_authority']})")


def bare_board_claim(run_dir, checks, check_id):
    """Composition point for a board-fab attestation (AGENTEE-449).

    ``checks`` is the complete retained check set of the run. The claim needs an
    unchanged candidate snapshot (every captured source re-hashed against the
    manifest and candidate identity), the immutable baseline, and a required
    evidence seal matching every retained result and artifact. The sealed report
    must equal the report re-derived from the sealed native measurement and the
    captured declaration and sources, including the full obligation ledger. It
    returns only the bare-board placement claim with its authority class and
    open physical/bench obligations; a pass here never closes installed or
    product qualification, and never states manufacturer corner compliance.
    """
    from .baseline import load_baseline, verify_evidence
    from .candidate import verify_snapshot
    run_dir = Path(run_dir)
    manifest = verify_snapshot(run_dir)
    if load_baseline(run_dir) is None:
        raise ValueError('antenna placement claim requires an immutable baseline')
    if not (run_dir / 'work/verification/evidence.json').is_file():
        raise ValueError('antenna placement claim requires a sealed evidence record')
    verify_evidence(run_dir, checks, required=True)
    matches = [c for c in checks if c.get('id') == check_id]
    if len(matches) != 1:
        raise ValueError(f'antenna placement check {check_id!r} is not in the retained check set')
    result = matches[0]
    if result.get('kind') != 'antenna_placement' or result.get('candidate_id') != manifest['candidate_id']:
        raise ValueError('antenna placement result is not bound to this candidate')
    if result.get('execution') != 'ok' or result.get('outcome') != 'pass':
        raise ValueError(f'antenna placement {check_id!r} did not pass')
    scope = manifest['scope']
    spec = [c for c in scope.get('checks', []) if c.get('id') == check_id]
    if len(spec) != 1 or spec[0].get('kind') != 'antenna_placement':
        raise ValueError('antenna placement check is not declared by this scope')
    evidence = {e['path'] if isinstance(e, dict) else e for e in result.get('evidence', [])}
    rel, native_rel = (f'work/{check_id}/{name}' for name in ('antenna-placement.json', 'antenna-native.json'))
    if not {rel, native_rel} <= evidence:
        raise ValueError('antenna placement report or native measurement is not sealed evidence')
    params = spec[0]['params']
    descriptor = capability(params, scope, manifest['files'], run_dir / 'inputs')
    native = json.loads((run_dir / native_rel).read_text())
    expected = build_report(manifest, params, descriptor, _context(descriptor, run_dir / 'inputs'), native)
    report = json.loads((run_dir / rel).read_text())
    if report != expected or any(result['details'].get(k) != v for k, v in expected.items()):
        raise ValueError('antenna placement report differs from its re-derived candidate evaluation')
    if report['status'] != 'pass' or report['obligations'] != OBLIGATIONS:
        raise ValueError('antenna placement report does not carry a pass with the full obligation ledger')
    return dict(claim='bare-board-antenna-placement', reference=report['reference'],
                placement_case=report['placement_case'], board_sha256=report['board_sha256'],
                guidance_sha256=report['guidance']['source']['sha256'], scope=report['scope'],
                authority_class=report['verdict_authority'], authorities=dict(report['authorities']),
                class_default_policy=(report['corner_policy'] or {}).get('id'),
                manufacturer_corner_compliance='unknown', open_obligations=dict(OBLIGATIONS))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board')
    parser.add_argument('--request', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    write_json(args.out, extract(args.board, json.loads(Path(args.request).read_text())))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


def describe_antenna_placement(ctx):
    ctx.captured(ctx.scope.get('board'))
    descriptor = capability(ctx.params, ctx.scope, ctx.files, ctx.inputs)
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('antenna_placement', describe_antenna_placement, check_antenna_placement,
        early_prerequisites=(_requires.staged_placement_clear,), prerequisites=(_requires.stage_progress,))

"""Read actual saved KiCad placement geometry without modifying the board.

Run under the existing networkless tool sandbox. Separate from electrical and
copper geometry adapters: unsupported curves/holes never become polygon hulls.
Courtyard circles and arcs are bounded by exact inner and outer polygons within
the declared courtyard_curves.ARC_TOLERANCE_NM (AGENTEE-212); placement
outlines and rule areas still refuse every curve. Silk containment reuses the
same bounds for its separate outline-with-holes model (AGENTEE-368).
"""
import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from . import courtyard_curves as curves
from .geometry_probe import box_nm, pad_attribute, pad_vec
from .placement import area2, cross, intersection_area, qualified_polygons


def vector_nm(point):
    return [int(point.x), int(point.y)]


def primitive(shape):
    import pcbnew
    record = {'uuid': shape.m_Uuid.AsString(), 'native_class': shape.GetClass()}
    if shape.GetClass() != 'PCB_SHAPE':
        return {**record, 'kind': 'unsupported'}
    kind = shape.GetShape()
    if shape.GetCornerRadius():
        return {**record, 'kind': 'unsupported', 'native_shape': int(kind),
                'corner_radius_nm': int(shape.GetCornerRadius())}
    if kind == pcbnew.SHAPE_T_SEGMENT:
        return {**record, 'kind': 'segment', 'start': vector_nm(shape.GetStart()), 'end': vector_nm(shape.GetEnd())}
    if kind == pcbnew.SHAPE_T_CIRCLE:
        # Saved as centre and one point on the circle, so the squared radius is exact.
        return {**record, 'kind': 'circle', 'center': vector_nm(shape.GetCenter()), 'end': vector_nm(shape.GetEnd())}
    if kind == pcbnew.SHAPE_T_ARC:
        return {**record, 'kind': 'arc', 'start': vector_nm(shape.GetStart()), 'mid': vector_nm(shape.GetArcMid()),
                'end': vector_nm(shape.GetEnd())}
    if kind == pcbnew.SHAPE_T_RECT:
        return {**record, 'kind': 'polygon', 'points': [vector_nm(p) for p in shape.GetRectCorners()]}
    if kind == pcbnew.SHAPE_T_POLY:
        poly = shape.GetPolyShape()
        if (poly.OutlineCount() == 1 and not poly.HasHoles() and poly.COutline(0).IsClosed()
                and poly.COutline(0).ArcCount() == 0):
            return {**record, 'kind': 'polygon', 'points': [vector_nm(p) for p in shape.GetPolyPoints()]}
    return {**record, 'kind': 'unsupported', 'native_shape': int(kind)}


def marking(item, owner_ref=None):
    """Retain native identity even for a graphic whose content we cannot grade."""
    import pcbnew
    record = {'uuid': item.m_Uuid.AsString(), 'owner_ref': owner_ref,
              'native_class': item.GetClass(), 'layer': {pcbnew.F_SilkS: 'F.SilkS',
                pcbnew.B_SilkS: 'B.SilkS'}.get(item.GetLayer(), pcbnew.LayerName(item.GetLayer()))}
    if item.GetClass() not in ('PCB_TEXT', 'FP_TEXT', 'PCB_FIELD'):
        return record
    return {**record, 'text': item.GetText(), 'position_nm': vector_nm(item.GetPosition()),
            'rotation_deg': item.GetTextAngle().AsDegrees(), 'mirrored': bool(item.IsMirrored()),
            'visible': bool(item.IsVisible()), 'size_nm': vector_nm(item.GetTextSize()),
            'thickness_nm': int(item.GetTextThickness()), 'knockout': bool(item.IsKnockout()),
            'font': item.GetFontName() or None}


def prove_contours(primitives, missing, unproven, curves_allowed=False, *, separate_contours=False):
    """Every primitive must belong to an exact-endpoint degree-two closed ring.

    ``separate_contours`` validates each ring independently; callers must then
    qualify their mutual topology before using them as an outline with holes.

    With ``curves_allowed`` (courtyards and silk containment only), circles are
    whole contours and arcs are ring edges; the record then carries outer
    ``polygons`` (a superset of the curved region) and ``inner_polygons`` (a
    subset), both proven straight."""
    record = {'status': missing if not primitives else unproven, 'primitives': primitives}
    kinds = ('segment', 'polygon') + (('circle', 'arc') if curves_allowed else ())
    if not primitives or any(p['kind'] not in kinds for p in primitives):
        unsupported = sorted({p.get('native_shape', p['kind']) for p in primitives if p['kind'] not in kinds},
                             key=str)
        return {**record, 'reason': f'unsupported primitive {unsupported}'} if primitives else record
    outer = [p['points'] for p in primitives if p['kind'] == 'polygon']
    inner = [list(p) for p in outer]
    try:
        for p in primitives:
            if p['kind'] == 'circle':
                ring_out, ring_in = curves.circle(p['center'], p['end'])
                outer.append(ring_out)
                inner.append(ring_in)
    except curves.CurveError as exc:
        return {**record, 'reason': str(exc)}
    edges = []
    segments = set()
    for p in primitives:
        if p['kind'] not in ('segment', 'arc'):
            continue
        a, b = tuple(p['start']), tuple(p['end'])
        if p['kind'] == 'segment':
            edge = tuple(sorted((a, b)))
            if a == b or edge in segments:
                return record
            segments.add(edge)
            edges.append((a, b, None))
            continue
        try:
            chains = curves.arc(p['start'], p['mid'], p['end'])
        except curves.CurveError as exc:
            return {**record, 'reason': str(exc)}
        edges.append((a, b, (tuple(p['mid']), *chains)))
    incident = {}
    for index, (a, b, _) in enumerate(edges):
        incident.setdefault(a, []).append(index)
        incident.setdefault(b, []).append(index)
    if any(len(adjacent) != 2 for adjacent in incident.values()):
        return record
    remaining = set(incident)
    while remaining:
        start = min(remaining)
        ring, previous, current = [], None, start
        while True:
            index = next(i for i in incident[current] if i != previous)
            a, b, curve = edges[index]
            following = b if current == a else a
            if curve is not None and current != a:
                mid, outside, inside = curve
                curve = (mid, outside[::-1], inside[::-1])
            ring.append((current, following, curve))
            remaining.discard(current)
            previous, current = index, following
            if current == start:
                break
        # Orientation from the ring through arc midpoints; an arc whose midpoint
        # lies on the exterior side is convex, its outside chain bounds from outside.
        sense = area2([q for u, _, c in ring for q in ((u,) if c is None else (u, c[0]))])
        if sense == 0:
            return {**record, 'reason': 'zero polygon area'}
        ring_out, ring_in = [], []
        for u, v, curve in ring:
            ring_out.append(list(u))
            ring_in.append(list(u))
            if curve is None:
                continue
            mid, outside, inside = curve
            convex = (sense > 0) == (cross(u, v, mid) < 0)
            ring_out.extend(outside if convex else inside)
            ring_in.extend(inside if convex else outside)
        outer.append(ring_out)
        inner.append(ring_in)
    try:
        if separate_contours and len(outer) > 64:
            raise ValueError('excessive polygons')
        groups = [[p] for p in outer] if separate_contours else [outer]
        for group in groups:
            qualified_polygons({'status': 'proven', 'polygons': group})
        if inner != outer and separate_contours:
            # Independent contours may nest, so each ring's bounds pair alone. A
            # simple ring whose every edge lies in the closed outer bound is inside it.
            from .board_envelope import segment_inside
            for i, o in zip(inner, outer):
                within, = qualified_polygons({'status': 'proven', 'polygons': [i]})
                if not all(segment_inside(a, b, o) for a, b in zip(within, within[1:] + within[:1])):
                    raise ValueError('inner bound not inside outer bound')
        elif inner != outer:
            within = qualified_polygons({'status': 'proven', 'polygons': inner})
            bounds = qualified_polygons({'status': 'proven', 'polygons': outer})
            if any(intersection_area(i, o) != Fraction(area2(i), 2) for i, o in zip(within, bounds)):
                raise ValueError('inner bound not inside outer bound')
    except ValueError as exc:
        return {**record, 'reason': str(exc)}
    proven = {**record, 'status': 'proven', 'polygons': outer}
    if inner != outer:
        proven.update(inner_polygons=inner, approximation={
            'tolerance_nm': curves.ARC_TOLERANCE_NM, 'polygons': 'outer bound (superset of the curved region)',
            'inner_polygons': 'inner bound (subset of the curved region)'})
    return proven


def rule_area(zone, owner):
    """Every rule area is inventoried; only one straight hole-free contour qualifies."""
    import pcbnew
    poly = zone.Outline()
    record = {'uuid': zone.m_Uuid.AsString(), 'name': zone.GetZoneName(), 'owner': owner,
              'layers': sorted(pcbnew.LayerName(layer) for layer in zone.GetLayerSet().Seq()),
              'forbid': {'footprints': bool(zone.GetDoNotAllowFootprints()),
                         'tracks': bool(zone.GetDoNotAllowTracks()), 'vias': bool(zone.GetDoNotAllowVias()),
                         'pads': bool(zone.GetDoNotAllowPads()),
                         'zone_fills': bool(zone.GetDoNotAllowZoneFills())},
              'placement_area': bool(zone.GetPlacementAreaEnabled())}
    if poly.OutlineCount() != 1 or poly.HoleCount(0) or poly.COutline(0).ArcCount():
        return {**record, 'outline': {'status': 'unsupported', 'outlines': poly.OutlineCount(),
                                      'reason': 'multiple contours, holes or arcs'}}
    chain = poly.COutline(0)
    points = [vector_nm(chain.CPoint(i)) for i in range(chain.PointCount())]
    return {**record, 'outline': {'status': 'proven', 'polygons': [points]}}


def custom_pad_geometry(pad):
    """Native anchor and complete pad-local merged copper contours."""
    import pcbnew
    layer = pcbnew.PADSTACK.ALL_LAYERS
    def contours(poly):
        return [[[int(poly.COutline(i).CPoint(j).x), int(poly.COutline(i).CPoint(j).y)]
                 for j in range(poly.COutline(i).PointCount())] for i in range(poly.OutlineCount())]
    merged = pcbnew.SHAPE_POLY_SET()
    pad.MergePrimitivesAsPolygon(layer, merged)
    # The merged set is fractured (holes become slits), so test holes on a copy.
    unfractured = pcbnew.SHAPE_POLY_SET(merged)
    unfractured.Unfracture()
    if not merged.OutlineCount() or unfractured.HasHoles():
        raise ValueError('custom pad merged copper missing or has unsupported holes')
    return {'anchor': int(pad.GetAnchorPadShape(layer)),
            'merged_polygons_nm': contours(merged)}


def _custom_pad_nodes(data):
    """Use the shared bounded reader and AGENTEE-300 qualifier on board pads."""
    import pcbnew
    from .symbol_library import parse_sexpr, children, field
    nodes, custom_keys = {}, set()
    for fp in children(parse_sexpr(data), 'footprint'):
        for pad in children(fp, 'pad'):
            if not children(pad, 'uuid') and not (len(pad) > 3 and pad[3] == 'custom'):
                # Legacy stock pads may receive their UUID only on native load.
                continue
            # Key by the native spelling: KiCad folds case, braces and legacy forms.
            key = pcbnew.KIID(field(pad, 'uuid', 1)[0]).AsString()
            if len(pad) > 3 and pad[3] == 'custom':
                custom_keys.add(key)
            # Count all pads: a custom pad can also collide with a stock pad.
            nodes[key] = None if key in nodes else pad
    return {key: nodes[key] for key in custom_keys}


def _qualify_custom_pad(pad, node):
    from .footprint_library import canonical_custom_pad
    from .symbol_library import children, field
    # Canonical UUID lookup detects duplicates without admitting new unique-pad spellings.
    if field(node, 'uuid', 1)[0] != pad.m_Uuid.AsString():
        raise ValueError('saved pad UUID spelling not qualified')
    node = canonical_custom_pad(node)
    geometry = custom_pad_geometry(pad)
    from decimal import Decimal, InvalidOperation
    def normalized(value):
        if isinstance(value, list):
            return [normalized(v) for v in value]
        try:
            return str(Decimal(value).normalize())
        except InvalidOperation:
            return value
    geometry['primitives'] = normalized(children(node, 'primitives')[0][1:])
    return {'status': 'proven', **geometry, 'bbox_nm': box_nm(pad.GetBoundingBox())}


def _packing_pad_geometry(board, fp, own_clearances=None):
    """Native outward bounds; drill/copper reservations are not courtyards."""
    import pcbnew
    settings = board.GetDesignSettings()
    clearance = max(settings.m_MinClearance, settings.m_HoleClearance, settings.m_HoleToHoleMin)
    copper, holes = [], []

    def polygons(poly):
        if poly.HasHoles():
            raise ValueError('packing pad contour contains unsupported holes')
        contours = [[[int(poly.COutline(i).CPoint(j).x), int(poly.COutline(i).CPoint(j).y)]
                 for j in range(poly.COutline(i).PointCount())] for i in range(poly.OutlineCount())]
        return [[list(point) for point in outline] for outline in
                qualified_polygons({'status': 'proven', 'polygons': contours})] if contours else []

    for pad in fp.Pads():
        drilled = pad.HasHole() or pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)
        if pad.Padstack().Mode() != pcbnew.PADSTACK.MODE_NORMAL:
            raise ValueError('per-layer packing padstack not qualified')
        for layer in board.GetEnabledLayers().CuStack():
            if not pad.IsOnLayer(layer):
                continue
            poly = pcbnew.SHAPE_POLY_SET()
            own_clearance = (pad.GetOwnClearance(layer) if own_clearances is None
                             else own_clearances[pad.m_Uuid.AsString()])
            # The drill reservation already contributes the board minimum. Peer
            # copper supplies only its excess, so their Minkowski margins cover
            # the peer's larger native netclass/local clearance without doubling
            # the board minimum. SWIG exposes no pairwise custom-rule evaluator;
            # the unchanged-rule native publication gate remains mandatory.
            margin = max(clearance, own_clearance) if drilled else max(0, own_clearance - clearance)
            pad.TransformShapeToPolygon(poly, layer, margin, 1000, pcbnew.ERROR_OUTSIDE)
            contours = polygons(poly)
            if contours:
                entry = {'pad_uuid': pad.m_Uuid.AsString(), 'reservation_kind': 'copper',
                         'layers': [pcbnew.LayerName(layer)], 'polygons': contours}
                copper.append(entry)
                if drilled:
                    holes.append({'pad_uuid': pad.m_Uuid.AsString(), 'reservation_kind': 'copper',
                                  'layers': entry['layers'][:],
                                  'polygons': [[p[:] for p in c] for c in contours]})
        if drilled:
            poly = pcbnew.SHAPE_POLY_SET()
            if not pad.TransformHoleToPolygon(poly, clearance, 1000, pcbnew.ERROR_OUTSIDE):
                raise ValueError('native packing drill contour unavailable')
            contours = polygons(poly)
            if not contours:
                raise ValueError('native packing drill contour empty')
            holes.append({'pad_uuid': pad.m_Uuid.AsString(), 'reservation_kind': 'drill',
                          'layers': [pcbnew.LayerName(l) for l in board.GetEnabledLayers().CuStack()],
                          'polygons': contours})
    return {'copper': copper, 'holes': holes, 'clearance_nm': clearance,
            'error_nm': 1000, 'bound': 'outside'}


def copper_centroid(pad, custom):
    """Exact native copper-land centroid relative to its electrical anchor.

    Symmetric stock shapes use ShapePos; straight native polygons use signed
    area moments. Curved asymmetric copper has no qualified exact centroid.
    Drill voids are not subtracted from the pad's copper-land silhouette.
    """
    import pcbnew
    from .symbol_library import field
    layer = pcbnew.PADSTACK.ALL_LAYERS
    if pad.Padstack().Mode() != pcbnew.PADSTACK.MODE_NORMAL:
        raise ValueError('per-layer copper centroid not qualified')
    if not any(pcbnew.LayerName(l).endswith('.Cu') for l in pad.GetLayerSet().Seq()):
        raise ValueError('pad has no copper layer')
    shape = pad.GetShape(layer)
    if any(v <= 0 for v in vector_nm(pad.GetSize(layer))):
        raise ValueError('native copper centroid needs a nonempty pad land')
    if shape in (pcbnew.PAD_SHAPE_CIRCLE, pcbnew.PAD_SHAPE_RECT,
                 pcbnew.PAD_SHAPE_OVAL, pcbnew.PAD_SHAPE_ROUNDRECT):
        center = vector_nm(pad.ShapePos(layer))
    else:
        if shape == pcbnew.PAD_SHAPE_CUSTOM:
            if custom.get('status') != 'proven' or custom['anchor'] != pcbnew.PAD_SHAPE_RECT:
                raise ValueError('custom copper centroid needs qualified straight copper and rectangular anchor')
            for primitive in custom['primitives']:
                if (primitive[0] not in ('gr_poly', 'gr_rect') or field(primitive, 'width', 1) != ['0']
                        or field(primitive, 'fill', 1) != ['yes']):
                    raise ValueError('curved or stroked custom copper centroid not qualified')
        elif shape == pcbnew.PAD_SHAPE_CHAMFERED_RECT:
            if pad.GetRoundRectRadiusRatio(layer) != 0:
                raise ValueError('curved asymmetric copper centroid not qualified')
        elif shape != pcbnew.PAD_SHAPE_TRAPEZOID:
            raise ValueError('native copper shape not qualified for centroid')
        poly = pcbnew.SHAPE_POLY_SET()
        # With straight copper and zero clearance, ERROR_INSIDE returns the
        # native contour; ERROR_OUTSIDE adds a 1 nm safety rim even to polygons.
        pad.TransformShapeToPolygon(poly, layer, 0, 1, pcbnew.ERROR_INSIDE)
        if not poly.OutlineCount() or poly.HasHoles():
            raise ValueError('native copper centroid polygon empty or has holes')
        total, moments = 0, [0, 0]
        for i in range(poly.OutlineCount()):
            chain = poly.COutline(i)
            if chain.ArcCount():
                raise ValueError('native copper centroid polygon has arcs')
            points = [vector_nm(chain.CPoint(j)) for j in range(chain.PointCount())]
            signed = area2(points)
            if not signed:
                raise ValueError('native copper centroid polygon has zero area')
            sense = 1 if signed > 0 else -1
            total += abs(signed)
            for a, b in zip(points, points[1:] + points[:1]):
                cross_product = a[0]*b[1] - b[0]*a[1]
                for axis in (0, 1):
                    moments[axis] += sense * (a[axis]+b[axis]) * cross_product
        center = [Fraction(m, 3*total) for m in moments]
    offset = [Fraction(c-p) for c, p in zip(center, vector_nm(pad.GetPosition()))]
    return [[v.numerator, v.denominator] for v in offset]


def copper_land_offset(pad):
    """Native land-box centre relative to the electrical anchor, for a land whose centroid is unqualified.

    A rounded chamfered rectangle is its size rectangle with corners cut or
    rounded inward, so the size box (pad-turned) about ShapePos contains its
    copper whether or not an exact area centroid exists (AGENTEE-366). Any
    other land without a qualified centroid has no proven extent here.
    """
    import pcbnew
    layer = pcbnew.PADSTACK.ALL_LAYERS
    if pad.Padstack().Mode() != pcbnew.PADSTACK.MODE_NORMAL:
        raise ValueError('per-layer copper land not qualified')
    if (pad.GetShape(layer) != pcbnew.PAD_SHAPE_CHAMFERED_RECT
            or any(v <= 0 for v in vector_nm(pad.GetSize(layer)))
            or not any(pcbnew.LayerName(l).endswith('.Cu') for l in pad.GetLayerSet().Seq())):
        raise ValueError('native copper land extent not qualified')
    return [c - p for c, p in zip(vector_nm(pad.ShapePos(layer)), vector_nm(pad.GetPosition()))]


def extract(board_path, *, board=None, before=None):
    import pcbnew
    path = Path(board_path)
    if before is None:
        before = path.read_bytes()
    if board is None:
        board = pcbnew.LoadBoard(str(path))
    project = path.with_suffix('.kicad_pro')
    from .design_rule_provenance import PROJECT_KEY, finding_sources, packing_clearances
    from .catalog import strict_json
    project = strict_json(project.read_bytes()) if project.is_file() else {}
    own_clearances = (packing_clearances(board, finding_sources(path), project)
                      if PROJECT_KEY in project else None)
    has_holes = any(p.HasHole() or p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)
                    for f in board.GetFootprints() for p in f.Pads())
    outline_shapes = [s for s in board.GetDrawings() if s.GetLayer() == pcbnew.Edge_Cuts]
    custom_nodes = _custom_pad_nodes(before) if any(
        p.GetShape() == pcbnew.PAD_SHAPE_CUSTOM for f in board.GetFootprints() for p in f.Pads()) else {}
    native_uuids = Counter(p.m_Uuid.AsString() for f in board.GetFootprints() for p in f.Pads())
    footprints = []
    markings = [marking(item) for item in board.GetDrawings()]
    areas = [rule_area(z, 'board') for z in board.Zones() if z.GetIsRuleArea()]
    for fp in board.GetFootprints():
        areas.extend(rule_area(z, fp.m_Uuid.AsString()) for z in fp.Zones() if z.GetIsRuleArea())
        graphics = list(fp.GraphicalItems()) + list(fp.GetFields())
        markings.extend(marking(item, fp.GetReference()) for item in graphics)
        outline_shapes.extend(s for s in graphics if s.GetLayer() == pcbnew.Edge_Cuts)
        courtyards = {}
        for side, layer in (('F', pcbnew.F_CrtYd), ('B', pcbnew.B_CrtYd)):
            shapes = [primitive(s) for s in graphics if s.GetLayer() == layer]
            if shapes:
                courtyards[side] = prove_contours(shapes, 'courtyard_missing', 'courtyard_unproven', True)
        pads = []
        for pad in fp.Pads():
            pads.append({'uuid': pad.m_Uuid.AsString(), 'number': pad.GetNumber(), 'net': pad.GetNetname(),
                         'position_nm': vector_nm(pad.GetPosition()), 'shape': int(pad.GetShape()),
                         'size_nm': vector_nm(pad.GetSize(pcbnew.PADSTACK.ALL_LAYERS)),
                         'drill_size_nm': vector_nm(pad.GetDrillSize()),
                         'rotation_deg': pad.GetOrientationDegrees(), 'attribute': pad_attribute(pad),
                         'layers': sorted(pcbnew.LayerName(layer) for layer in pad.GetLayerSet().Seq())})
            if pad.m_Uuid.AsString() in custom_nodes and (custom_nodes[pad.m_Uuid.AsString()] is None
                                                          or native_uuids[pad.m_Uuid.AsString()] > 1):
                pads[-1]['custom_geometry'] = {'status': 'unsupported',
                                               'reason': 'duplicate pad UUID: ' + pad.m_Uuid.AsString()}
            elif pad.GetShape() == pcbnew.PAD_SHAPE_CUSTOM:
                try:
                    node = custom_nodes[pad.m_Uuid.AsString()]
                    pads[-1]['custom_geometry'] = _qualify_custom_pad(pad, node)
                except (ValueError, KeyError) as exc:
                    pads[-1]['custom_geometry'] = {'status': 'unsupported', 'reason': str(exc)}
            try:
                pads[-1]['copper_centroid_offset_nm'] = copper_centroid(pad, pads[-1].get('custom_geometry', {}))
            except ValueError as exc:
                pads[-1]['copper_centroid_reason'] = str(exc)
                try:
                    pads[-1]['copper_land_offset_nm'] = copper_land_offset(pad)
                except ValueError:
                    pass
        footprints.append({'ref': fp.GetReference(), 'uuid': fp.m_Uuid.AsString(), 'value': fp.GetValue(),
                           'fpid': fp.GetFPIDAsString(),
                           'side': {pcbnew.B_Cu: 'B', pcbnew.F_Cu: 'F'}.get(fp.GetLayer(), 'unsupported'),
                           'locked': bool(fp.IsLocked()), 'position_nm': vector_nm(fp.GetPosition()),
                           'rotation_deg': fp.GetOrientationDegrees(), 'courtyards': courtyards,
                           'pads': sorted(pads, key=lambda p: (p['number'], p['uuid']))})
        from .access_direction import DECLARATION
        declarations = [f.GetText() for f in fp.GetFields() if f.GetName() == DECLARATION]
        if declarations:
            # Keep invalid bytes visible so independent judgment reports unknown.
            footprints[-1]['access_direction'] = declarations[0] if len(declarations) == 1 else None
        if has_holes:
            try:
                footprints[-1]['packing_pad_geometry'] = {
                    'status': 'proven', **_packing_pad_geometry(board, fp, own_clearances)}
            except ValueError as exc:
                footprints[-1]['packing_pad_geometry'] = {'status': 'unsupported', 'reason': str(exc)}
    if path.read_bytes() != before:
        raise ValueError('saved board changed during extraction')
    outline = prove_contours([primitive(s) for s in outline_shapes], 'outline_missing', 'outline_unproven')
    if outline['status'] == 'proven' and len(outline['polygons']) != 1:
        outline.update(status='outline_unproven', reason='multiple board contours unsupported')
    return {'schema': 'agent-ee.placement-probe/1', 'board_sha256': hashlib.sha256(before).hexdigest(),
            'pcbnew_version': pcbnew.Version(), 'outline': outline,
            # ERROR_OUTSIDE arc inflation of polygonised pads; the placer's copper setback (AGENTEE-366e).
            'max_error_nm': int(board.GetDesignSettings().m_MaxError),
            'markings': sorted(markings, key=lambda m: (m['uuid'], m['owner_ref'] or '')),
            'rule_areas': sorted(areas, key=lambda a: (a['owner'], a['uuid'])),
            'footprints': sorted(footprints, key=lambda f: (f['ref'], f['uuid']))}


ROUTING_GEOMETRY = 'agent-ee.routing-geometry/1'


def _outer_contours(poly):
    """Outer contours of a native polygon set; a contour hole is dropped, so the set still contains the shape."""
    return [[vector_nm(poly.COutline(i).CPoint(j)) for j in range(poly.COutline(i).PointCount())]
            for i in range(poly.OutlineCount())]


def _routing_pad(board, fp, pad, error):
    """Raw copper per layer (outer and inner native bounds), drill and native own clearance of one pad."""
    import pcbnew
    record = {'uuid': pad.m_Uuid.AsString(), 'ref': fp.GetReference(), 'footprint_uuid': fp.m_Uuid.AsString(),
              'number': pad.GetNumber(), 'net': pad.GetNetname(), 'attribute': pad_attribute(pad),
              'shape': int(pad.GetShape()), 'position_nm': vector_nm(pad.GetPosition()),
              'rotation_deg': pad.GetOrientationDegrees(), 'copper': {}, 'own_clearance_nm': {}, 'hole': None}
    reasons = []
    if pad.Padstack().Mode() != pcbnew.PADSTACK.MODE_NORMAL:
        reasons.append('per-layer padstack not qualified')
    if pad.GetShape() == pcbnew.PAD_SHAPE_CUSTOM:
        reasons.append('custom pad shape not qualified for access')
    for layer in board.GetEnabledLayers().CuStack():
        if not pad.IsOnLayer(layer) or not pad.IsOnCopperLayer():
            continue
        bounds = {}
        for name, bound in (('outer', pcbnew.ERROR_OUTSIDE), ('inner', pcbnew.ERROR_INSIDE)):
            poly = pcbnew.SHAPE_POLY_SET()
            pad.TransformShapeToPolygon(poly, layer, 0, error, bound)
            if poly.HasHoles():
                reasons.append('pad copper contour has holes')
            bounds[name] = _outer_contours(poly)
        if not bounds['outer']:
            continue
        name = pcbnew.LayerName(layer)
        record['copper'][name] = bounds
        record['own_clearance_nm'][name] = int(pad.GetOwnClearance(layer))
    if pad.HasHole() or pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
        poly = pcbnew.SHAPE_POLY_SET()
        if not pad.TransformHoleToPolygon(poly, 0, error, pcbnew.ERROR_OUTSIDE) or not poly.OutlineCount():
            reasons.append('native drill contour unavailable')
        record['hole'] = {'plated': pad.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH,
                          'drill_nm': vector_nm(pad.GetDrillSize()), 'outer': _outer_contours(poly)}
    record['status'] = 'unsupported' if reasons else 'supported'
    if reasons:
        record['reasons'] = sorted(set(reasons))
    return record


def _custom_rules(path):
    """Native custom rules are captured by identity; their pairwise evaluation is not modelled."""
    from .net_constraints import rule_forms
    dru = path.with_suffix('.kicad_dru')
    if not dru.is_file():
        return {'status': 'absent'}
    raw = dru.read_bytes()
    names = sorted(str(node[1]) for node in rule_forms(raw, native=True)
                   if isinstance(node, list) and len(node) > 1 and node[0] == 'rule')
    return {'status': 'present' if names else 'empty', 'sha256': hashlib.sha256(raw).hexdigest(), 'rules': names}


def routing_geometry(board_path):
    """Opt-in ``agent-ee.routing-geometry/1`` capture for local access analysis (AGENTEE-386).

    Raw pad copper (native outer and inner polygon bounds per copper layer),
    drills and slots, native own clearances under the saved project's rules,
    existing tracks and vias, outline, rule areas, copper zones and the board
    rule floor. Separate from ``extract``: packing reservations are enlarged
    and are never read as raw copper. Custom rule files are identified, not
    evaluated; the analysis reports their presence as unknown coverage.
    """
    import pcbnew
    path = Path(board_path)
    before = path.read_bytes()
    board = pcbnew.LoadBoard(str(path))
    settings = board.GetDesignSettings()
    error = int(settings.m_MaxError)
    layers = [pcbnew.LayerName(layer) for layer in board.GetEnabledLayers().CuStack()]
    pads, footprints, outline_shapes = [], [], [s for s in board.GetDrawings() if s.GetLayer() == pcbnew.Edge_Cuts]
    areas = [rule_area(z, 'board') for z in board.Zones() if z.GetIsRuleArea()]
    for fp in board.GetFootprints():
        areas.extend(rule_area(z, fp.m_Uuid.AsString()) for z in fp.Zones() if z.GetIsRuleArea())
        outline_shapes.extend(s for s in fp.GraphicalItems() if s.GetLayer() == pcbnew.Edge_Cuts)
        footprints.append({'ref': fp.GetReference(), 'uuid': fp.m_Uuid.AsString(),
                           'side': {pcbnew.B_Cu: 'B', pcbnew.F_Cu: 'F'}.get(fp.GetLayer(), 'unsupported'),
                           'position_nm': vector_nm(fp.GetPosition()), 'rotation_deg': fp.GetOrientationDegrees()})
        pads.extend(_routing_pad(board, fp, pad, error) for pad in fp.Pads())
    nets = sorted({p['net'] for p in pads} | {t.GetNetname() for t in board.GetTracks()})
    clearances = {}
    for net in nets:
        # A trial track's own clearance under the native rule resolution, without touching the board.
        probe = pcbnew.PCB_TRACK(board)
        found = board.FindNet(net)
        if found is not None:
            probe.SetNet(found)
        clearances[net] = {}
        for layer in board.GetEnabledLayers().CuStack():
            probe.SetLayer(layer)
            clearances[net][pcbnew.LayerName(layer)] = int(probe.GetOwnClearance(layer))
    tracks, vias = [], []
    for item in board.GetTracks():
        record = {'uuid': item.m_Uuid.AsString(), 'net': item.GetNetname()}
        if item.GetClass() == 'PCB_VIA':
            top, bottom = item.TopLayer(), item.BottomLayer()
            vias.append({**record, 'position_nm': vector_nm(item.GetPosition()), 'diameter_nm': int(item.GetWidth(top)),
                         'drill_nm': int(item.GetDrillValue()), 'via_type': int(item.GetViaType()),
                         'layers': [n for n in layers if item.IsOnLayer(board.GetLayerID(n))],
                         'span': [pcbnew.LayerName(top), pcbnew.LayerName(bottom)]})
        elif item.GetClass() == 'PCB_TRACK':
            tracks.append({**record, 'kind': 'segment', 'layer': pcbnew.LayerName(item.GetLayer()),
                           'start_nm': vector_nm(item.GetStart()), 'end_nm': vector_nm(item.GetEnd()),
                           'width_nm': int(item.GetWidth())})
        else:
            tracks.append({**record, 'kind': 'unsupported', 'native_class': item.GetClass()})
    zones = [{'uuid': z.m_Uuid.AsString(), 'net': z.GetNetname(), 'filled': bool(z.IsFilled()),
              'layers': sorted(pcbnew.LayerName(layer) for layer in z.GetLayerSet().Seq())}
             for z in board.Zones() if not z.GetIsRuleArea()]
    if path.read_bytes() != before:
        raise ValueError('saved board changed during routing geometry capture')
    outline = prove_contours([primitive(s) for s in outline_shapes], 'outline_missing', 'outline_unproven')
    if outline['status'] == 'proven' and len(outline['polygons']) != 1:
        outline.update(status='outline_unproven', reason='multiple board contours unsupported')
    rules = {'min_clearance_nm': int(settings.m_MinClearance), 'hole_clearance_nm': int(settings.m_HoleClearance),
             'hole_to_hole_nm': int(settings.m_HoleToHoleMin),
             'copper_edge_clearance_nm': int(settings.m_CopperEdgeClearance),
             'min_track_width_nm': int(settings.m_TrackMinWidth), 'min_via_diameter_nm': int(settings.m_ViasMinSize),
             'min_via_annular_nm': int(settings.m_ViasMinAnnularWidth),
             'min_through_drill_nm': int(settings.m_MinThroughDrill), 'custom_rules': _custom_rules(path)}
    return {'schema': ROUTING_GEOMETRY, 'board_sha256': hashlib.sha256(before).hexdigest(),
            'pcbnew_version': pcbnew.Version(), 'max_error_nm': error, 'copper_layers': layers, 'rules': rules,
            'track_clearance_nm': clearances, 'outline': outline,
            'rule_areas': sorted(areas, key=lambda a: (a['owner'], a['uuid'])),
            'footprints': sorted(footprints, key=lambda f: (f['ref'], f['uuid'])),
            'pads': sorted(pads, key=lambda p: (p['ref'], p['number'], p['uuid'])),
            'tracks': sorted(tracks, key=lambda t: t['uuid']), 'vias': sorted(vias, key=lambda v: v['uuid']),
            'zones': sorted(zones, key=lambda z: z['uuid'])}


def envelope_extract(path):
    """Read-only native load corroboration for exact saved envelope facts in one LoadBoard pass."""
    import pcbnew
    path = Path(path)
    before = path.read_bytes()
    board = pcbnew.LoadBoard(str(path))
    placement = extract(path, board=board, before=before)
    footprints = list(board.GetFootprints())
    if len({fp.m_Uuid.AsString() for fp in footprints}) != len(footprints):
        raise ValueError('duplicate native footprint UUID makes rule-area ownership ambiguous')
    drills = {}
    for fp in footprints:
        for pad in fp.Pads():
            uid = pad.m_Uuid.AsString()
            if uid in drills:
                raise ValueError('duplicate native pad UUID makes drill inventory ambiguous')
            drills[uid] = {'drill_nm': vector_nm(pad_vec(pad, pad.GetDrillSize)),
                           'offset_nm': vector_nm(pad_vec(pad, pad.GetOffset))}
    for fp in placement['footprints']:
        for pad in fp['pads']:
            pad.update(drills[pad['uuid']])
    copper = [{'id': int(layer), 'name': board.GetLayerName(layer)}
              for layer in board.GetEnabledLayers().CuStack()]
    if path.read_bytes() != before or placement['board_sha256'] != hashlib.sha256(before).hexdigest():
        raise ValueError('board changed during native envelope probe')
    return {'schema': 'agent-ee.envelope-probe/1', 'board_sha256': placement['board_sha256'],
            'pcbnew_version': pcbnew.Version(), 'copper': copper,
            'has_stackup': bool(board.GetDesignSettings().m_HasStackup),
            'thickness_nm': int(board.GetDesignSettings().GetBoardThickness()),
            'outline': placement['outline'], 'footprints': placement['footprints'],
            'rule_areas': placement['rule_areas']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board')
    parser.add_argument('--out', required=True)
    parser.add_argument('--routing-geometry', action='store_true',
                        help='write the opt-in agent-ee.routing-geometry/1 capture instead (AGENTEE-386)')
    parser.add_argument('--envelope', action='store_true',
                        help='write the agent-ee.envelope-probe/1 capture in one native load')
    args = parser.parse_args(argv)
    if Path(args.out).resolve() == Path(args.board).resolve():
        raise ValueError('placement probe cannot overwrite its source board')
    if args.routing_geometry:
        report = routing_geometry(args.board)
    elif args.envelope:
        report = envelope_extract(args.board)
    else:
        report = extract(args.board)
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Read actual saved KiCad placement geometry without modifying the board.

Run under the existing networkless tool sandbox. Separate from electrical and
copper geometry adapters: unsupported curves/holes never become polygon hulls.
Courtyard circles and arcs are bounded by exact inner and outer polygons within
the declared courtyard_curves.ARC_TOLERANCE_NM (AGENTEE-212); outlines and
rule areas still refuse every curve.
"""
import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from . import courtyard_curves as curves
from .geometry_probe import pad_attribute, box_nm
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


def prove_contours(primitives, missing, unproven, curves_allowed=False):
    """Every primitive must belong to an exact-endpoint degree-two closed ring.

    With ``curves_allowed`` (courtyards only), circles are whole contours and arcs
    are ring edges; the record then carries outer ``polygons`` (a superset of the
    curved region) and ``inner_polygons`` (a subset), both proven straight."""
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
        qualified_polygons({'status': 'proven', 'polygons': outer})
        if inner != outer:
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


def extract(board_path):
    import pcbnew
    path = Path(board_path)
    before = path.read_bytes()
    board = pcbnew.LoadBoard(str(path))
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
        footprints.append({'ref': fp.GetReference(), 'uuid': fp.m_Uuid.AsString(), 'value': fp.GetValue(),
                           'fpid': fp.GetFPIDAsString(),
                           'side': {pcbnew.B_Cu: 'B', pcbnew.F_Cu: 'F'}.get(fp.GetLayer(), 'unsupported'),
                           'locked': bool(fp.IsLocked()), 'position_nm': vector_nm(fp.GetPosition()),
                           'rotation_deg': fp.GetOrientationDegrees(), 'courtyards': courtyards,
                           'pads': sorted(pads, key=lambda p: (p['number'], p['uuid']))})
    if path.read_bytes() != before:
        raise ValueError('saved board changed during extraction')
    outline = prove_contours([primitive(s) for s in outline_shapes], 'outline_missing', 'outline_unproven')
    if outline['status'] == 'proven' and len(outline['polygons']) != 1:
        outline.update(status='outline_unproven', reason='multiple board contours unsupported')
    return {'schema': 'agent-ee.placement-probe/1', 'board_sha256': hashlib.sha256(before).hexdigest(),
            'pcbnew_version': pcbnew.Version(), 'outline': outline,
            'markings': sorted(markings, key=lambda m: (m['uuid'], m['owner_ref'] or '')),
            'rule_areas': sorted(areas, key=lambda a: (a['owner'], a['uuid'])),
            'footprints': sorted(footprints, key=lambda f: (f['ref'], f['uuid']))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board')
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    if Path(args.out).resolve() == Path(args.board).resolve():
        raise ValueError('placement probe cannot overwrite its source board')
    report = extract(args.board)
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

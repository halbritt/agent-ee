"""Read actual saved KiCad placement geometry without modifying the board.

Run under the existing networkless tool sandbox. Separate from electrical and
copper geometry adapters: unsupported curves/holes never become polygon hulls.
"""
import argparse
import hashlib
import json
from pathlib import Path

from .geometry_probe import pad_attribute
from .placement import qualified_polygons


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
    if kind == pcbnew.SHAPE_T_RECT:
        return {**record, 'kind': 'polygon', 'points': [vector_nm(p) for p in shape.GetRectCorners()]}
    if kind == pcbnew.SHAPE_T_POLY:
        poly = shape.GetPolyShape()
        if (poly.OutlineCount() == 1 and not poly.HasHoles() and poly.COutline(0).IsClosed()
                and poly.COutline(0).ArcCount() == 0):
            return {**record, 'kind': 'polygon', 'points': [vector_nm(p) for p in shape.GetPolyPoints()]}
    return {**record, 'kind': 'unsupported', 'native_shape': int(kind)}


def prove_contours(primitives, missing, unproven):
    """Every primitive must belong to an exact-endpoint degree-two closed ring."""
    record = {'status': missing if not primitives else unproven, 'primitives': primitives}
    if not primitives or any(p['kind'] not in ('segment', 'polygon') for p in primitives):
        return record
    polygons = [p['points'] for p in primitives if p['kind'] == 'polygon']
    neighbours = {}
    segments = set()
    for p in primitives:
        if p['kind'] != 'segment':
            continue
        a, b = tuple(p['start']), tuple(p['end'])
        edge = tuple(sorted((a, b)))
        if a == b or edge in segments:
            return record
        segments.add(edge)
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)
    if any(len(adjacent) != 2 for adjacent in neighbours.values()):
        return record
    remaining = set(neighbours)
    while remaining:
        start = min(remaining)
        ring, previous, current = [], None, start
        while current not in ring:
            ring.append(current)
            adjacent = neighbours[current]
            following = adjacent[0] if adjacent[0] != previous else adjacent[1]
            previous, current = current, following
        if current != start:
            return record
        remaining.difference_update(ring)
        polygons.append([list(p) for p in ring])
    try:
        qualified_polygons({'status': 'proven', 'polygons': polygons})
    except ValueError as exc:
        return {**record, 'reason': str(exc)}
    return {**record, 'status': 'proven', 'polygons': polygons}


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


def extract(board_path):
    import pcbnew
    path = Path(board_path)
    before = path.read_bytes()
    board = pcbnew.LoadBoard(str(path))
    outline_shapes = [s for s in board.GetDrawings() if s.GetLayer() == pcbnew.Edge_Cuts]
    footprints = []
    areas = [rule_area(z, 'board') for z in board.Zones() if z.GetIsRuleArea()]
    for fp in board.GetFootprints():
        areas.extend(rule_area(z, fp.m_Uuid.AsString()) for z in fp.Zones() if z.GetIsRuleArea())
        graphics = list(fp.GraphicalItems()) + list(fp.GetFields())
        outline_shapes.extend(s for s in graphics if s.GetLayer() == pcbnew.Edge_Cuts)
        courtyards = {}
        for side, layer in (('F', pcbnew.F_CrtYd), ('B', pcbnew.B_CrtYd)):
            shapes = [primitive(s) for s in graphics if s.GetLayer() == layer]
            if shapes:
                courtyards[side] = prove_contours(shapes, 'courtyard_missing', 'courtyard_unproven')
        pads = []
        for pad in fp.Pads():
            pads.append({'uuid': pad.m_Uuid.AsString(), 'number': pad.GetNumber(), 'net': pad.GetNetname(),
                         'position_nm': vector_nm(pad.GetPosition()), 'shape': int(pad.GetShape()),
                         'size_nm': vector_nm(pad.GetSize(pcbnew.PADSTACK.ALL_LAYERS)),
                         'rotation_deg': pad.GetOrientationDegrees(), 'attribute': pad_attribute(pad),
                         'layers': sorted(pcbnew.LayerName(layer) for layer in pad.GetLayerSet().Seq())})
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

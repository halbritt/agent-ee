"""Native In1 reference coverage for the captured AGENTEE-480 GNSS corridor.

Subset of fome-mc b389b47a, agentee-480-gnss-mech/check_constraints.py:
one saved GND component must cover the reserved corridor AND actual RF copper.
The full RF/return-terminal/impedance qualification remains a separate check.
"""
from .storage import digest, read_json

BOUND_NETS = {("U3", "11"): "/POD_GNSS_RF", ("L3", "2"): "/POD_GNSS_RF",
              ("D3", "1"): "/POD_GNSS_RF", ("J2", "1"): "/POD_GNSS_RF",
              ("L3", "1"): "/ANT_BIAS", ("D3", "2"): "/GND",
              ("C42", "1"): "/ANT_BIAS", ("C42", "2"): "/GND"}


def _geometry(value):
    import pcbnew as k
    if value['type'] == 'Polygon':
        polygons = [value['coordinates']]
    elif value['type'] == 'MultiPolygon':
        polygons = value['coordinates']
    else:
        raise ValueError('GNSS reference mask must be Polygon or MultiPolygon')
    result = k.SHAPE_POLY_SET()
    for rings in polygons:
        index = result.NewOutline()
        for ring_index, ring in enumerate(rings):
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError('GNSS polygon ring must be explicitly closed')
            hole = -1 if ring_index == 0 else result.NewHole(index)
            for x, y in ring[:-1]:
                result.Append(k.FromMM(x), k.FromMM(y), index, hole)
    if result.IsEmpty() or result.IsSelfIntersecting():
        raise ValueError('invalid or empty GNSS reference mask')
    return result



def _polygons(poly):
    def ring(chain):
        return [[chain.CPoint(i).x/1e6, chain.CPoint(i).y/1e6] for i in range(chain.PointCount())]
    return [dict(shell=ring(poly.COutline(i)),
                 holes=[ring(poly.CHole(i, h)) for h in range(poly.HoleCount(i))])
            for i in range(poly.OutlineCount())]


def check_reference(board, inputs, board_path):
    import pcbnew as k
    paths = sorted(inputs.rglob('gnss-corridor.json'))
    rf = board.FindNet('/POD_GNSS_RF')
    if rf is None and not paths:
        return {'status': 'not_applicable'}
    if rf is None or len(paths) != 1:
        raise ValueError('GNSS reference requires exact RF net and one captured gnss-corridor.json')
    definition = read_json(paths[0])
    if (definition.get('schema') != 'agentee-480.gnss-reference/1' or
            definition.get('ground_net') != '/GND' or definition.get('ground_reference_layer') != 'In1.Cu'):
        raise ValueError('unsupported GNSS reference definition')
    # Routing changes the board hash; component, pad and stackup bindings must not change.
    binding = definition['binding']
    if (len(binding['pads']) != len(BOUND_NETS) or
            {(p['reference'], p['number']): p['pad']['net'] for p in binding['pads']} != BOUND_NETS):
        raise ValueError('GNSS reference requires the complete AGENTEE-480 terminal binding')
    if digest(board_path.with_suffix('.kicad_pro')) != binding['project_sha256']:
        raise ValueError('stale GNSS project binding')
    edges = [dict(start_mm=[e.GetStart().x/1e6, e.GetStart().y/1e6],
                  end_mm=[e.GetEnd().x/1e6, e.GetEnd().y/1e6], shape=int(e.GetShape()))
             for e in board.GetDrawings() if e.GetLayer() == k.Edge_Cuts]
    if edges != binding['edges']:
        raise ValueError('stale GNSS board edge binding')
    if (binding['copper_layers'] != board.GetCopperLayerCount() or
            binding['thickness_mm'] != board.GetDesignSettings().GetBoardThickness()/1e6):
        raise ValueError('stale GNSS stackup binding')
    native_fps = list(board.GetFootprints())
    for ref in {r for r, _ in BOUND_NETS}:
        if sum(f.GetReference() == ref for f in native_fps) != 1:
            raise ValueError('GNSS component missing or ambiguous: ' + ref)
    fps = {f.GetReference(): f for f in native_fps}
    for saved in binding['pads']:
        fp = fps[saved['reference']]
        pads = [p for p in fp.Pads() if p.GetNumber() == saved['number']]
        if len(pads) != 1:
            raise ValueError('GNSS bound pad missing or ambiguous')
        pad = pads[0]
        pos, pp = fp.GetPosition(), pad.GetPosition()
        copper = {board.GetLayerName(l): _polygons(pad.GetEffectivePolygon(l))
                  for l in [k.F_Cu, k.In1_Cu, k.In2_Cu, k.B_Cu] if pad.IsOnLayer(l)}
        if (copper != saved['pad']['copper_polygons'] or fp.GetLayerName() != 'F.Cu' or
                pad.GetOrientationDegrees() != saved['pad']['orientation_deg'] or
                [pad.GetDrillSize().x/1e6, pad.GetDrillSize().y/1e6] != saved['pad']['drill_mm'] or
                int(pad.GetAttribute()) != saved['pad']['attribute'] or
                [board.GetLayerName(l) for l in pad.GetLayerSet().Seq()] != saved['pad']['layers'] or
                fp.m_Uuid.AsString() != saved['fp_uuid'] or
                fp.GetFPID().GetUniStringLibId() != saved['lib_id'] or
                [pos.x/1e6, pos.y/1e6, fp.GetOrientationDegrees()] != saved['pose_mm_deg'] or
                pad.m_Uuid.AsString() != saved['pad']['uuid'] or
                pad.GetNetname() != saved['pad']['net'] or
                [pp.x/1e6, pp.y/1e6] != saved['pad']['at_mm'] or
                [pad.GetSize().x/1e6, pad.GetSize().y/1e6] != saved['pad']['size_mm'] or
                int(pad.GetShape()) != saved['pad']['shape']):
            raise ValueError('stale GNSS component/pad binding: ' + saved['reference'] + '.' + saved['number'])
    protected = _geometry(definition['reference_exclusion'])
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname() == '/POD_GNSS_RF':
                protected.Append(pad.GetEffectivePolygon(k.F_Cu))
    tracks = [t for t in board.GetTracks() if t.GetNetname() == '/POD_GNSS_RF']
    if not tracks:
        raise ValueError('GNSS RF route is absent')
    for track in tracks:
        if track.GetClass() == 'PCB_VIA' or track.GetLayer() != k.F_Cu:
            raise ValueError('GNSS RF must remain on F.Cu')
        track.TransformShapeToPolygon(protected, k.F_Cu, 0, 1, k.ERROR_OUTSIDE)
    for zone in board.Zones():
        if zone.GetIsRuleArea() or zone.GetNetname() != '/GND' or not zone.IsOnLayer(k.In1_Cu):
            continue
        fill = zone.GetFilledPolysList(k.In1_Cu)
        for index in range(fill.OutlineCount()):
            component = k.SHAPE_POLY_SET()
            component.AddOutline(fill.COutline(index))
            for hole in range(fill.HoleCount(index)):
                component.AddHole(fill.CHole(index, hole), 0)
            uncovered = k.SHAPE_POLY_SET(protected)
            uncovered.BooleanSubtract(component)
            if uncovered.IsEmpty():
                return {'status': 'PASS', 'zone': zone.m_Uuid.AsString(), 'polygon': index}
    raise ValueError('no single saved In1 /GND component covers GNSS corridor and RF copper')

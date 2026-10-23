"""Native In1 reference coverage for the captured AGENTEE-480 GNSS corridor.

Subset of fome-mc b389b47a, agentee-480-gnss-mech/check_constraints.py:
one saved GND component must cover the reserved corridor AND actual RF copper.
The full RF/return-terminal/impedance qualification remains a separate check.
"""
from .storage import read_json


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
            hole = -1 if ring_index == 0 else result.NewHole(index)
            for x, y in ring[:-1]:
                result.Append(k.FromMM(x), k.FromMM(y), index, hole)
    if result.IsEmpty() or result.IsSelfIntersecting():
        raise ValueError('invalid or empty GNSS reference mask')
    return result


def check_reference(board, inputs):
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
    if (binding['copper_layers'] != board.GetCopperLayerCount() or
            binding['thickness_mm'] != board.GetDesignSettings().GetBoardThickness()/1e6):
        raise ValueError('stale GNSS stackup binding')
    fps = {f.GetReference(): f for f in board.GetFootprints()}
    for saved in binding['pads']:
        fp = fps[saved['reference']]
        pads = [p for p in fp.Pads() if p.GetNumber() == saved['number']]
        if len(pads) != 1:
            raise ValueError('GNSS bound pad missing or ambiguous')
        pad = pads[0]
        pos, pp = fp.GetPosition(), pad.GetPosition()
        if (fp.m_Uuid.AsString() != saved['fp_uuid'] or
                fp.GetFPID().GetUniStringLibId() != saved['lib_id'] or
                [pos.x/1e6, pos.y/1e6, fp.GetOrientationDegrees()] != saved['pose_mm_deg'] or
                pad.m_Uuid.AsString() != saved['pad']['uuid'] or
                pad.GetNetname() != saved['pad']['net'] or
                [pp.x/1e6, pp.y/1e6] != saved['pad']['at_mm'] or
                [pad.GetSize().x/1e6, pad.GetSize().y/1e6] != saved['pad']['size_mm'] or
                int(pad.GetShape()) != saved['pad']['shape']):
            raise ValueError('stale GNSS component/pad binding')
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

"""Exact native DC geometry facts; runs in the networkless tool sandbox."""
import argparse

from .storage import write_json


def point(v):
    return [int(v.x), int(v.y)]


def extract(board_path):
    import pcbnew
    from .geometry_probe import load_readonly_board, pad_attribute, verify_unchanged
    board, before = load_readonly_board(board_path)
    tracks, pads = [], []
    for track in board.GetTracks():
        cls = track.GetClass()
        entry = {'uuid': track.m_Uuid.AsString(), 'native_class': cls,
                 'net': track.GetNetname(), 'layer': board.GetLayerName(track.GetLayer()),
                 'start_nm': point(track.GetStart()), 'end_nm': point(track.GetEnd())}
        if cls in ('PCB_TRACK', 'PCB_ARC'):
            entry.update(width_nm=int(track.GetWidth()), native_length_nm=float(track.GetLength()))
            if cls == 'PCB_ARC':
                entry.update(mid_nm=point(track.GetMid()), center_nm=point(track.GetCenter()),
                             radius_nm=float(track.GetRadius()), sweep_rad=track.GetAngle().AsRadians())
            bbox = track.GetBoundingBox()
            entry['bbox_nm'] = [bbox.GetLeft(), bbox.GetTop(), bbox.GetRight(), bbox.GetBottom()]
        tracks.append(entry)
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            pads.append({'uuid': pad.m_Uuid.AsString(), 'footprint_uuid': fp.m_Uuid.AsString(),
                'ref': fp.GetReference(), 'pad': pad.GetNumber(), 'net': pad.GetNetname(),
                'position_nm': point(pad.GetPosition()), 'size_nm': point(pad.GetSize(pcbnew.F_Cu)),
                'drill_nm': point(pad.GetDrillSize()),
                'offset_nm': point(pad.GetOffset(pcbnew.F_Cu)), 'attribute': pad_attribute(pad),
                'shape': int(pad.GetShape(pcbnew.F_Cu)),
                'copper_layers': [board.GetLayerName(l) for l in pad.GetLayerSet().Seq()
                                  if pcbnew.IsCopperLayer(l)]})
    graphics = list(board.GetDrawings()) + [g for fp in board.GetFootprints() for g in list(fp.GraphicalItems()) + list(fp.GetFields())]
    boundaries = []
    for g in graphics:
        if g.GetLayer() == pcbnew.Edge_Cuts:
            entry = {'uuid': g.m_Uuid.AsString(), 'native_class': g.GetClass()}
            if g.GetClass() == 'PCB_SHAPE':
                entry.update(shape=int(g.GetShape()), start_nm=point(g.GetStart()), end_nm=point(g.GetEnd()))
            boundaries.append(entry)
    verify_unchanged(board_path, before, 'DC geometry probe')
    return {'schema': 'agent-ee.dc-geometry/2', 'pcbnew_version': pcbnew.Version(),
            'tracks': tracks, 'pads': pads,
            'substrate': {'boundaries': boundaries},
            'footprint_refs': [fp.GetReference() for fp in board.GetFootprints()],
            'zones': [z.m_Uuid.AsString() for z in list(board.Zones()) +
                      [z for fp in board.GetFootprints() for z in fp.Zones()]],
            'copper_graphics': [{'uuid': g.m_Uuid.AsString(), 'native_class': g.GetClass(),
                                 'layer': board.GetLayerName(g.GetLayer())}
                                for g in graphics if pcbnew.IsCopperLayer(g.GetLayer())]}


def extract_rail(board_path):
    """Native whole-board conductor inventory for the qualified DC rail bound."""
    import pcbnew as p
    from .geometry_probe import VIA_TYPES, enum_name, load_readonly_board, pad_attribute, verify_unchanged
    b, before = load_readonly_board(board_path)
    copper = [l for l in b.GetEnabledLayers().Seq() if p.IsCopperLayer(l)]
    tracks, pads = [], []
    for t in b.GetTracks():
        cls = t.GetClass()
        item = {'uuid': t.m_Uuid.AsString(), 'native_class': cls, 'net': t.GetNetname()}
        if cls in ('PCB_TRACK', 'PCB_ARC'):
            item.update(layer=b.GetLayerName(t.GetLayer()), width_nm=int(t.GetWidth()), length_nm=float(t.GetLength()))
        elif cls == 'PCB_VIA':
            item.update(via_type=enum_name(t.GetViaType(), 'VIATYPE_', VIA_TYPES),
                        drill_nm=int(t.GetDrillValue()),
                        copper_layers=[b.GetLayerName(l) for l in copper if t.IsOnLayer(l)])
        tracks.append(item)
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            d = pad.GetDrillSize()
            pads.append({'uuid': pad.m_Uuid.AsString(), 'ref': fp.GetReference(),
                'pad': pad.GetNumber(), 'net': pad.GetNetname(), 'attribute': pad_attribute(pad),
                'drill_nm': [int(d.x), int(d.y)],
                'copper_layers': [b.GetLayerName(l) for l in copper if pad.IsOnLayer(l)]})
    graphics = list(b.GetDrawings()) + [g for fp in b.GetFootprints()
        for g in list(fp.GraphicalItems()) + list(fp.GetFields())]
    verify_unchanged(board_path, before, 'DC rail geometry probe')
    return {'schema': 'agent-ee.dc-rail-geometry/1', 'pcbnew_version': p.Version(),
        'enabled_copper_layers': [b.GetLayerName(l) for l in copper], 'tracks': tracks, 'pads': pads,
        'zones': [z.m_Uuid.AsString() for z in list(b.Zones()) + [z for fp in b.GetFootprints() for z in fp.Zones()]],
        'copper_graphics': [g.m_Uuid.AsString() for g in graphics if p.IsCopperLayer(g.GetLayer())]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board')
    parser.add_argument('--out', required=True)
    parser.add_argument('--rail', action='store_true',
                        help='write the agent-ee.dc-rail-geometry/1 whole-net conductor inventory')
    args = parser.parse_args()
    write_json(args.out, extract_rail(args.board) if args.rail else extract(args.board))


if __name__ == '__main__':
    main()

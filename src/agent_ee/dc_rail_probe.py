"""Native whole-board conductor inventory for the qualified DC rail bound."""
import argparse

from .storage import write_json


def extract(path):
    import pcbnew as p
    from .geometry_probe import pad_attribute, enum_name, VIA_TYPES
    b = p.LoadBoard(path)
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
    return {'schema': 'agent-ee.dc-rail-geometry/1', 'pcbnew_version': p.Version(),
        'enabled_copper_layers': [b.GetLayerName(l) for l in copper], 'tracks': tracks, 'pads': pads,
        'zones': [z.m_Uuid.AsString() for z in list(b.Zones()) + [z for fp in b.GetFootprints() for z in fp.Zones()]],
        'copper_graphics': [g.m_Uuid.AsString() for g in graphics if p.IsCopperLayer(g.GetLayer())]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    write_json(args.out, extract(args.board))


if __name__ == '__main__':
    main()

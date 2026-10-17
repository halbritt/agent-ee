import pcbnew, json
b = pcbnew.LoadBoard('/candidate/project/rc_filter.kicad_pcb')
tracks = []
for t in b.GetTracks():
    cls = t.GetClass()
    tracks.append(dict(cls=cls, net=t.GetNetname(),
                       width_mm=pcbnew.ToMM(t.GetWidth()),
                       start=(pcbnew.ToMM(t.GetStart().x), pcbnew.ToMM(t.GetStart().y)),
                       end=(pcbnew.ToMM(t.GetEnd().x), pcbnew.ToMM(t.GetEnd().y)),
                       layer=t.GetLayerName() if cls=='PCB_TRACK' else 'F.Cu+B.Cu'))
conn = b.GetConnectivity()
unconnected = conn.GetUnconnectedCount(True)
zones = [dict(net=z.GetNetname(), filled=z.IsFilled(),
              min_thick_mm=pcbnew.ToMM(z.GetMinThickness()),
              layers=[b.GetLayerName(l) for l in z.GetLayerSet().Seq()]) for z in b.Zones()]
segs=[t for t in tracks if t['cls']=='PCB_TRACK']
print(json.dumps(dict(track_segments=len(segs),
                      min_seg_width_mm=min(t['width_mm'] for t in segs),
                      unconnected_items=unconnected, zones=zones, tracks=tracks), indent=1))

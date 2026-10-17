import pcbnew, json
b = pcbnew.LoadBoard('/candidate/project/rc_filter.kicad_pcb')
rows = []
for t in b.GetTracks():
    kind = t.GetClass()
    rows.append(dict(cls=kind, net=t.GetNetname(),
                     width_mm=pcbnew.ToMM(t.GetWidth()),
                     uuid=t.m_Uuid.AsString()))
print("pcbnew version:", pcbnew.GetBuildVersion())
print(json.dumps(rows, indent=1))
segs = [r for r in rows if r['cls']=='PCB_TRACK']
print("track segments:", len(segs), "min width:", min(r['width_mm'] for r in segs))
ds = b.GetDesignSettings()
print("board min track width setting (mm):", pcbnew.ToMM(ds.m_TrackMinWidth))

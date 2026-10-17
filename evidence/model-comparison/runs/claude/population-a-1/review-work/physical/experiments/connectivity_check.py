import pcbnew, json
b = pcbnew.LoadBoard('/candidate/project/rc_filter.kicad_pcb')
b.BuildConnectivity()
conn = b.GetConnectivity()
out = {}
out['pcbnew_version'] = pcbnew.GetBuildVersion()
out['unrouted_ratsnest'] = conn.GetUnconnectedCount(True)
# pads and their nets
pads = []
for fp in b.GetFootprints():
    for p in fp.Pads():
        pads.append(dict(ref=fp.GetReference(), pad=p.GetNumber(),
                         net=p.GetNetname(),
                         pos=[p.GetPosition().x/1e6, p.GetPosition().y/1e6]))
    pads[-1]  # noop
out['footprint_attrs'] = {fp.GetReference(): {'dnp': fp.IsDNP(),
                                              'excl_bom': bool(fp.GetAttributes() & pcbnew.FP_EXCLUDE_FROM_BOM),
                                              'excl_pos': bool(fp.GetAttributes() & pcbnew.FP_EXCLUDE_FROM_POS_FILES)}
                          for fp in b.GetFootprints()}
out['pads'] = pads
# zone fill state
zones = []
for z in b.Zones():
    zones.append(dict(net=z.GetNetname(), filled=z.IsFilled(),
                      area_filled_mm2=sum(z.GetFilledPolysList(l).Area()/1e12 for l in z.GetLayerSet().Seq() if z.HasFilledPolysForLayer(l))))
out['zones'] = zones
print(json.dumps(out, indent=1))

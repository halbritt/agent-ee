import pcbnew, shutil, json
shutil.copy('/candidate/project/rc_filter.kicad_pcb', '/work/experiments/scratch_refill.kicad_pcb')
b = pcbnew.LoadBoard('/work/experiments/scratch_refill.kicad_pcb')
z = list(b.Zones())[0]
saved = z.GetFilledPolysList(pcbnew.B_Cu)
saved_area = saved.Area()
filler = pcbnew.ZONE_FILLER(b)
filler.Fill(b.Zones())
fresh = z.GetFilledPolysList(pcbnew.B_Cu)
fresh_area = fresh.Area()
print(json.dumps({
 "saved_area_mm2": pcbnew.ToMM(pcbnew.ToMM(saved_area)),
 "fresh_area_mm2": pcbnew.ToMM(pcbnew.ToMM(fresh_area)),
 "delta_mm2": pcbnew.ToMM(pcbnew.ToMM(fresh_area - saved_area)),
 "saved_outlines": saved.OutlineCount(),
 "fresh_outlines": fresh.OutlineCount(),
}, indent=1))

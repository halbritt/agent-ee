# Staleness check: refill zones on a SCRATCH COPY and compare fill area/vertex
# count with the saved fill. Candidate itself is untouched.
import pcbnew, shutil, json
shutil.copy("/candidate/project/rc_filter.kicad_pcb", "/work/experiments/scratch_board.kicad_pcb")
b = pcbnew.LoadBoard("/work/experiments/scratch_board.kicad_pcb")
z = list(b.Zones())[0]
saved = z.GetFilledPolysList(pcbnew.B_Cu)
saved_area, saved_pts = saved.Area()/1e12, saved.FullPointCount()
filler = pcbnew.ZONE_FILLER(b)
filler.Fill(b.Zones())
new = z.GetFilledPolysList(pcbnew.B_Cu)
print(json.dumps({
 "saved_area_mm2": saved_area, "saved_vertices": saved_pts,
 "refill_area_mm2": new.Area()/1e12, "refill_vertices": new.FullPointCount(),
 "area_delta_mm2": new.Area()/1e12 - saved_area,
 "unconnected_after_refill": b.GetConnectivity().GetUnconnectedCount(True)
}, indent=1))

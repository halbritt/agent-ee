import pcbnew, json
b = pcbnew.LoadBoard('/candidate/project/rc_filter.kicad_pcb')
z = list(b.Zones())[0]
fill = z.GetFilledPolysList(pcbnew.B_Cu)
def contains(x_mm, y_mm):
    return fill.Contains(pcbnew.VECTOR2I(pcbnew.FromMM(x_mm), pcbnew.FromMM(y_mm)))
report = {}
# via at (113,100) on GND, B.Cu: point containment at via center and ring edge
report['via_center_in_fill'] = contains(113,100)
report['via_ring_pts_in_fill'] = [contains(113+dx,100+dy) for dx,dy in ((0.25,0),(-0.25,0),(0,0.25),(0,-0.25))]
# J1 pad3 at (100,105.08) GND PTH: thermal spokes - sample points just outside the 1.0mm drill (r=0.5), inside pad (r<=0.85)
import math
spoke_hits = []
for ang in range(0,360,5):
    x = 100 + 0.7*math.cos(math.radians(ang))
    y = 105.08 + 0.7*math.sin(math.radians(ang))
    if contains(x,y): spoke_hits.append(ang)
report['j1_pad3_spoke_angles_deg'] = spoke_hits
# connectivity: nets of each pad and whether connectivity says everything connected
conn = b.GetConnectivity()
report['unconnected_count'] = conn.GetUnconnectedCount(True)
# track endpoint vs pad center registration
pads = {}
for fp in b.GetFootprints():
    for p in fp.Pads():
        pads[f"{fp.GetReference()}.{p.GetNumber()}"] = (pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y), p.GetNetname())
report['pads'] = pads
print(json.dumps(report, indent=1))

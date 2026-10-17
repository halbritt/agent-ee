# Independent connectivity probe of the saved board (no refill).
# Verifies pad->net mapping, track endpoints, and whether the saved B.Cu zone
# fill geometrically contains/overlaps the GND via and J1.3 PTH pad.
import pcbnew, json

b = pcbnew.LoadBoard("/candidate/project/rc_filter.kicad_pcb")
out = {"pads": [], "tracks": [], "zone": {}}
for fp in b.GetFootprints():
    for p in fp.Pads():
        out["pads"].append({
            "ref": fp.GetReference(), "pad": p.GetNumber(),
            "net": p.GetNetname(),
            "pos_mm": [p.GetPosition().x/1e6, p.GetPosition().y/1e6]})
for t in b.GetTracks():
    d = {"type": t.GetClass(), "net": t.GetNetname(),
         "start_mm": [t.GetStart().x/1e6, t.GetStart().y/1e6],
         "end_mm": [t.GetEnd().x/1e6, t.GetEnd().y/1e6],
         "width_mm": t.GetWidth()/1e6}
    out["tracks"].append(d)

# zone fill as saved: test overlap with via and J1.3 pad on B.Cu
zones = list(b.Zones())
z = zones[0]
out["zone"] = {"net": z.GetNetname(), "is_filled": z.IsFilled(),
               "layers": [b.GetLayerName(l) for l in z.GetLayerSet().Seq()]}
fill = z.GetFilledPolysList(pcbnew.B_Cu)

def covered(x_mm, y_mm):
    return fill.Contains(pcbnew.VECTOR2I(int(x_mm*1e6), int(y_mm*1e6)))

# via barrel center and points on its annulus
out["zone"]["contains_via_center_113_100"] = covered(113, 100)
# J1.3 pad at (100,105.08), drill 1.0, pad 1.7 -> check ring points at r=0.86mm
import math
pts = []
for ang in range(0, 360, 15):
    x = 100 + 0.86*math.cos(math.radians(ang))
    y = 105.08 + 0.86*math.sin(math.radians(ang))
    pts.append((round(ang), bool(covered(x, y))))
out["zone"]["ring_points_J1_3_r0.86"] = pts
out["zone"]["spoke_hits"] = sum(1 for _, c in pts if c)

# connectivity: run KiCad's own connectivity algo on loaded board
b.BuildConnectivity()
conn = b.GetConnectivity()
nets = {}
for code in range(b.GetNetCount()):
    ni = b.FindNet(code)
    if ni is None: continue
    name = ni.GetNetname()
    if not name: continue
    nets[name] = conn.GetUnconnectedCount(True)
out["unconnected_count"] = conn.GetUnconnectedCount(True)
print(json.dumps(out, indent=1))

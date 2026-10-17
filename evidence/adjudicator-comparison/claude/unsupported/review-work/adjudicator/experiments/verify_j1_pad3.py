"""Adjudication experiment: is J1 pad 3 connected to GND on the saved board?
Loads /candidate/project/rc_filter.kicad_pcb read-only with pcbnew 10.0.7.
Checks: (1) pad net assignments, (2) saved B.Cu GND zone fill polygon
geometrically touching/overlapping J1 pad 3 copper, (3) connectivity
unconnected count using the saved fill (no refill performed).
"""
import pcbnew, json

b = pcbnew.LoadBoard("/candidate/project/rc_filter.kicad_pcb")
out = {}

pads = {}
for fp in b.GetFootprints():
    for p in fp.Pads():
        pads[f"{fp.GetReference()}.{p.GetNumber()}"] = {
            "net": p.GetNetname(),
            "pos_mm": [pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)],
        }
out["pad_nets"] = pads

j1_3 = None
for fp in b.GetFootprints():
    if fp.GetReference() == "J1":
        for p in fp.Pads():
            if p.GetNumber() == "3":
                j1_3 = p
out["j1_pad3_net"] = j1_3.GetNetname()

# saved zone fill vs pad 3 copper overlap on B.Cu
zone = b.Zones()[0]
out["zone_net"] = zone.GetNetname()
out["zone_is_filled"] = zone.IsFilled()
fill = zone.GetFilledPolysList(pcbnew.B_Cu)
pad_shape = j1_3.GetEffectivePolygon(pcbnew.B_Cu)
inter = pcbnew.SHAPE_POLY_SET(fill)
inter.BooleanIntersection(pad_shape)
out["pad3_zone_overlap_area_mm2"] = round(inter.Area() / 1e12, 6)

# spot checks: points along 45-degree thermal spoke directions, 1.0 mm from pad center
import math
cx, cy = 100.0, 105.08
pts = {}
for ang in (45, 135, 225, 315):
    x = cx + 1.0 * math.cos(math.radians(ang))
    y = cy + 1.0 * math.sin(math.radians(ang))
    v = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
    pts[f"{ang}deg@1.0mm"] = {"xy_mm": [round(x,4), round(y,4)], "in_zone_fill": fill.Contains(v)}
out["spoke_probe_points"] = pts

# connectivity with the saved fill (board not refilled)
conn = b.GetConnectivity()
conn.RecalculateRatsnest()
gnd = b.GetNetcodeFromNetname("GND")
out["gnd_unconnected_count"] = conn.GetUnconnectedCount(True)
out["gnd_pads_count"] = "n/a (API signature)"
print(json.dumps(out, indent=2))

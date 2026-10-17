#!/usr/bin/env python3
"""Connectivity cross-check for rc_filter.kicad_pcb:
1. Each track endpoint that should land on a pad does (pad center match).
2. Via at (113,100) lies inside the B.Cu GND filled polygon (point-in-polygon).
3. J1 pad 3 (GND, PTH at 100,105.08) connects to the zone (its center inside or spokes touch).
"""
import re, json

text = open("/candidate/project/rc_filter.kicad_pcb").read()

# Pads from geometry probe (verified against native file): net -> pad centers
pads = {
 ("J1","1","/IN"): (100.0,100.0), ("J1","2","/OUT"): (100.0,102.54), ("J1","3","GND"): (100.0,105.08),
 ("R1","1","/IN"): (104.0875,100.0), ("R1","2","/OUT"): (105.9125,100.0),
 ("C1","1","/OUT"): (109.05,100.0), ("C1","2","GND"): (110.95,100.0),
}
# track endpoints per net (from native segments)
endpoints = {
 "/IN": [(100.0,100.0),(104.0875,100.0)],
 "/OUT": [(105.9125,100.0),(109.05,100.0),(100.0,102.54),(107.5,102.54),(107.5,100.0)],
 "GND": [(110.95,100.0),(113.0,100.0)],
}
results = {"pad_hits": {}, "notes": []}
for (ref,pad,net),pos in pads.items():
    hit = pos in endpoints.get(net,[]) or net=="GND" and ref=="J1"  # J1.3 via zone
    results["pad_hits"][f"{ref}.{pad} ({net})"] = {"center": pos, "track_endpoint_at_center": pos in endpoints.get(net,[])}

# T-junction: vertical /OUT seg ends at (107.5,100), which lies on horizontal seg 105.9125..109.05 at y=100
results["out_tee_on_segment"] = 105.9125 <= 107.5 <= 109.05

# filled polygon on B.Cu
poly_txt = re.search(r'\(filled_polygon.*?\(pts(.*?)\)\s*\)\s*\)', text, re.S).group(1)
pts = [(float(a),float(b)) for a,b in re.findall(r'\(xy ([\d.]+) ([\d.]+)\)', poly_txt)]
def inside(p, poly):
    x,y=p; n=len(poly); c=False; j=n-1
    for i in range(n):
        xi,yi=poly[i]; xj,yj=poly[j]
        if ((yi>y)!=(yj>y)) and (x < (xj-xi)*(y-yi)/(yj-yi)+xi): c=not c
        j=i
    return c
results["polygon_vertices"] = len(pts)
results["via_113_100_inside_zone"] = inside((113.0,100.0), pts)
results["j1_pad3_center_inside_zone"] = inside((100.0,105.08), pts)
# check spoke points slightly outside the pad annulus toward zone (thermal relief)
for name,p in {"j1_pad3_up":(100.0,104.2),"j1_pad3_down":(100.0,105.96),"j1_pad3_left":(99.12,105.08),"j1_pad3_right":(100.88,105.08)}.items():
    results[f"{name}_inside_zone"] = inside(p, pts)
print(json.dumps(results, indent=1))

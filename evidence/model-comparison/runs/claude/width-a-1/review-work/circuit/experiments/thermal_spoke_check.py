#!/usr/bin/env python3
"""Probe diagonal thermal-spoke locations around J1 pad 3 (100,105.08) in B.Cu GND zone.
Pad: 1.7mm circle (r=0.85), thermal_gap 0.5 -> zone copper resumes r>=1.35 except spokes."""
import re, json, math

text = open("/candidate/project/rc_filter.kicad_pcb").read()
poly_txt = re.search(r'\(filled_polygon.*?\(pts(.*?)\)\s*\)\s*\)', text, re.S).group(1)
pts = [(float(a),float(b)) for a,b in re.findall(r'\(xy ([\d.]+) ([\d.]+)\)', poly_txt)]
def inside(p, poly):
    x,y=p; n=len(poly); c=False; j=n-1
    for i in range(n):
        xi,yi=poly[i]; xj,yj=poly[j]
        if ((yi>y)!=(yj>y)) and (x < (xj-xi)*(y-yi)/(yj-yi)+xi): c=not c
        j=i
    return c
cx,cy=100.0,105.08
out={}
# probe along 8 directions at radius 1.0 (inside gap band 0.85..1.35: copper only on spokes)
for deg in range(0,360,45):
    r=1.0
    p=(cx+r*math.cos(math.radians(deg)), cy+r*math.sin(math.radians(deg)))
    out[f"r1.0_deg{deg}"]=inside(p,pts)
# also far field just beyond gap, radius 1.5, to ensure surrounding zone copper exists
for deg in (0,90,180,270):
    r=1.5
    p=(cx+r*math.cos(math.radians(deg)), cy+r*math.sin(math.radians(deg)))
    out[f"r1.5_deg{deg}"]=inside(p,pts)
# and same probes for J1 pad1 (/IN, should have NO connection to GND zone) sanity
out["pad1_r1.0_deg45"]=inside((100+0.707,100+0.707),pts)
print(json.dumps(out, indent=1))

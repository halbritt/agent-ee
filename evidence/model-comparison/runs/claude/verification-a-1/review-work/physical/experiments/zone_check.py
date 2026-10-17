# Parse the single saved filled_polygon from the candidate board and test
# point-in-polygon for the GND via and for points on J1.3's annular copper
# where thermal spokes must land.
import re, sys

text = open("/candidate/project/rc_filter.kicad_pcb").read()
m = re.search(r"\(filled_polygon.*?\(pts(.*?)\)\s*\)\s*\)", text, re.S)
pts = [(float(a),float(b)) for a,b in re.findall(r"\(xy ([\-\d.]+) ([\-\d.]+)\)", m.group(1))]
print("filled_polygon vertices:", len(pts))

def inside(p, poly):
    x,y=p; n=len(poly); c=False
    for i in range(n):
        x1,y1=poly[i]; x2,y2=poly[(i+1)%n]
        if ((y1>y)!=(y2>y)) and x < (x2-x1)*(y-y1)/(y2-y1)+x1:
            c = not c
    return c

tests = {
 "via (113,100)": (113.0,100.0),
 "far corner sample (114,106)": (114.0,106.0),
 "J1.3 pad right edge+spoke (100.80,105.08)": (100.80,105.08),
 "J1.3 pad left edge (99.20,105.08)": (99.20,105.08),
 "J1.3 pad top (100,104.28)": (100.0,104.28),
 "J1.3 pad bottom (100,105.88)": (100.0,105.88),
 "J1.3 just outside clearance ring right (101.6,105.08)": (101.6,105.08),
 "inside J1.2 clearance (should be False) (100.9,102.54)": (100.9,102.54),
 "between J1.2 and J1.3 copper web (100,103.8)": (100.0,103.8),
}
for name,p in tests.items():
    print(f"  {name}: inside={inside(p,pts)}")

# spoke count around J1.3: sample ring of points on the pad copper boundary radius 0.8
import math
hits=[]
for deg in range(0,360,5):
    p=(100+0.8*math.cos(math.radians(deg)), 105.08+0.8*math.sin(math.radians(deg)))
    if inside(p,pts): hits.append(deg)
print("angles (deg) on r=0.8 ring around J1.3 covered by fill:", hits)

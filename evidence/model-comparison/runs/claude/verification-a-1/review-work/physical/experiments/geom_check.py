# Independent connectivity/geometry arithmetic from the saved board file values
# (inputs/project/rc_filter.kicad_pcb). Coordinates in mm.
import math

pads = {
 ("C1","1","/OUT"): (109.05,100.0),
 ("C1","2","GND"): (110.95,100.0),
 ("R1","1","/IN"): (104.0875,100.0),
 ("R1","2","/OUT"): (105.9125,100.0),
 ("J1","1","/IN"): (100.0,100.0),
 ("J1","2","/OUT"): (100.0,102.54),
 ("J1","3","GND"): (100.0,105.08),
}
tracks = [
 ("/IN",(100,100),(104.0875,100)),
 ("/OUT",(105.9125,100),(109.05,100)),
 ("/OUT",(100,102.54),(107.5,102.54)),
 ("/OUT",(107.5,102.54),(107.5,100)),
 ("GND",(110.95,100),(113,100)),
]
via = ("GND",(113.0,100.0))

def on_seg(p,a,b,tol=1e-6):
    ax,ay=a; bx,by=b; px,py=p
    cross = (bx-ax)*(py-ay)-(by-ay)*(px-ax)
    if abs(cross)>tol: return False
    dot = (px-ax)*(bx-ax)+(py-ay)*(by-ay)
    return -tol <= dot <= (bx-ax)**2+(by-ay)**2+tol

print("Pad-to-track endpoint coincidence:")
for (ref,pad,net),pos in pads.items():
    hits=[t for t in tracks if t[0]==net and (t[1]==pos or t[2]==pos or on_seg(pos,t[1],t[2]))]
    print(f"  {ref}.{pad} {net} @ {pos}: {'OK' if hits or (ref,pad)==('J1','3') else 'NO TRACK'} ({len(hits)} track touch)")

print("T-junction: J1.2 vertical track end (107.5,100) lies on R1.2->C1.1 track:",
      on_seg((107.5,100),(105.9125,100),(109.05,100)))
print("Via at (113,100) touches GND track end:", tracks[4][2]==(113,100))

# Board edge rectangle 96..116 x, 96..108 y; zone outline 96.5..107.5
edge=(96,116,96,108)
for (ref,pad,net),(x,y) in pads.items():
    assert edge[0]<x<edge[1] and edge[2]<y<edge[3], (ref,pad)
print("All pads inside board outline: OK")
print("Via inside zone outline rect (96.5..115.5, 96.5..107.5):", 96.5<113<115.5 and 96.5<100<107.5)

# RC analytic
import math
v=1*(1-math.exp(-1e-3/(10e3*100e-9)))
print(f"Analytic v(1ms) = {v:.5f} V; window 0.62..0.65 -> {'PASS' if 0.62<=v<=0.65 else 'FAIL'}")
# window in tau terms
print("tau window:", -1e-3/math.log(1-0.65), "..", -1e-3/math.log(1-0.62))

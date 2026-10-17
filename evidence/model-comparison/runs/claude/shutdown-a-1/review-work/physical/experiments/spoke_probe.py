# Finer probe: does the saved B.Cu GND fill include thermal spokes that
# overlap J1 pad 3 copper (annulus r=0.5..0.85 around (100,105.08))?
import pcbnew, math, json
board = pcbnew.LoadBoard("/candidate/project/rc_filter.kicad_pcb")
z = list(board.Zones())[0]
fill = z.GetFilledPolysList(pcbnew.B_Cu)
def inside(x, y):
    return fill.Contains(pcbnew.VECTOR2I(int(round(x*1e6)), int(round(y*1e6))))
cx, cy = 100.0, 105.08
res = {}
for deg in range(0, 360, 15):
    a = math.radians(deg)
    # radius 0.7: inside pad annulus; radius 1.1: in clearance gap/spoke zone
    res[f"r0.70_a{deg:03d}"] = inside(cx+0.7*math.cos(a), cy+0.7*math.sin(a))
    res[f"r1.10_a{deg:03d}"] = inside(cx+1.1*math.cos(a), cy+1.1*math.sin(a))
hits = {k: v for k, v in res.items() if v}
print(json.dumps({"hits_true": sorted(hits), "n_true": len(hits), "n_total": len(res)}, indent=1))

# Also: same probe for via (net GND) and pad2 (net /OUT, expect NO spokes)
cx2, cy2 = 100.0, 102.54
res2 = [inside(cx2+1.1*math.cos(math.radians(d)), cy2+1.1*math.sin(math.radians(d))) for d in range(0,360,15)]
print("pad2 (/OUT) r1.1 ring any-true:", any(res2))

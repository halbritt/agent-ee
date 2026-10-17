import pcbnew, shutil
b = pcbnew.LoadBoard('/candidate/project/rc_filter.kicad_pcb')
z = list(b.Zones())[0]
poly = z.GetFilledPolysList(pcbnew.B_Cu)
def contains(x_mm, y_mm):
    return poly.Contains(pcbnew.VECTOR2I(int(x_mm*1e6), int(y_mm*1e6)))
print('saved fill area mm2: %.3f' % (poly.Area()/1e12))
print('via (113,100) inside saved fill:', contains(113,100))
# J1.3 pad at (100,105.08): annulus r=0.5..0.85 -> check 4 spoke directions at r=0.7
import math
pts = [(100+0.7*math.cos(a), 105.08+0.7*math.sin(a)) for a in [0, math.pi/2, math.pi, 3*math.pi/2, math.pi/4]]
print('spoke probes near J1.3 (any True => zone touches pad copper):',
      [contains(x,y) for x,y in pts])
# /IN and /OUT pads must NOT be shorted by the zone: probe just outside their copper
print('zone at J1.1 edge+0.1 (100.95,100):', contains(100.95,100))  # should be False (clearance)
print('zone at J1.2 edge+0.1 (100.95,102.54):', contains(100.95,102.54))
# Refill comparison on scratch copy
shutil.copytree('/candidate/project', '/work/experiments/scratch_project', dirs_exist_ok=True)
b2 = pcbnew.LoadBoard('/work/experiments/scratch_project/rc_filter.kicad_pcb')
filler = pcbnew.ZONE_FILLER(b2)
filler.Fill(b2.Zones())
z2 = list(b2.Zones())[0]
poly2 = z2.GetFilledPolysList(pcbnew.B_Cu)
print('refilled area mm2: %.3f' % (poly2.Area()/1e12))
print('area delta mm2: %.4f' % abs(poly.Area()/1e12 - poly2.Area()/1e12))
b2.BuildConnectivity()
print('scratch unrouted after refill:', b2.GetConnectivity().GetUnconnectedCount(True))

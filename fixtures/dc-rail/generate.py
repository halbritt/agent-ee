"""Create the owned multilayer analytic case from the native RC regression."""
from pathlib import Path
import pcbnew as k

root = Path(__file__).resolve().parent
b = k.LoadBoard(str(root.parent / 'rc/control/rc_filter.kicad_pcb'))
# The analytic model excludes pours. Replace this fixture's ground pour with
# explicit copper; never alter the source RC regression or its native rules.
for zone in list(b.Zones()):
    b.Remove(zone)
ground = b.FindNet('GND')
for a, z in (((113000000, 100000000), (113000000, 105080000)),
             ((113000000, 105080000), (100000000, 105080000))):
    t = k.PCB_TRACK(b)
    t.SetStart(k.VECTOR2I(*a))
    t.SetEnd(k.VECTOR2I(*z))
    t.SetWidth(250000)
    t.SetLayer(k.B_Cu)
    t.SetNet(ground)
    b.Add(t)
net = b.FindNet('/IN')
for track in list(b.GetTracks()):
    if track.GetNetname() == '/IN':
        b.Remove(track)
for a, z, layer in ((100000000, 101500000, k.F_Cu),
                     (101500000, 102500000, k.B_Cu),
                     (102500000, 104087500, k.F_Cu)):
    t = k.PCB_TRACK(b)
    t.SetStart(k.VECTOR2I(a, 100000000))
    t.SetEnd(k.VECTOR2I(z, 100000000))
    t.SetWidth(250000)
    t.SetLayer(layer)
    t.SetNet(net)
    b.Add(t)
for x in (101500000, 102500000):
    v = k.PCB_VIA(b)
    v.SetPosition(k.VECTOR2I(x, 100000000))
    v.SetWidth(600000)
    v.SetDrill(300000)
    v.SetViaType(k.VIATYPE_THROUGH)
    v.SetLayerPair(k.F_Cu, k.B_Cu)
    v.SetNet(net)
    b.Add(v)
k.SaveBoard(str(root / 'rc_filter.kicad_pcb'), b, True)

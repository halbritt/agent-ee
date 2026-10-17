# Independent connectivity probe of the saved board (no refill; fill as saved).
import pcbnew
b = pcbnew.LoadBoard("/candidate/project/rc_filter.kicad_pcb")
conn = b.GetConnectivity()
print("unconnected (ratsnest) count:", conn.GetUnconnectedCount(True))
for fp in b.GetFootprints():
    for pad in fp.Pads():
        print(f"{fp.GetReference()}.{pad.GetNumber()} net={pad.GetNetname()} "
              f"pos={pad.GetPosition()}")
# zone fill state
for z in b.Zones():
    print("zone net:", z.GetNetname(), "filled:", z.IsFilled(),
          "layer:", z.GetLayerName(),
          "fill area mm^2:", z.GetFilledArea()/1e12)
# check pad-to-zone and via-to-zone geometric overlap on B.Cu
from pcbnew import B_Cu
z = list(b.Zones())[0]
fill = z.GetFilledPolysList(B_Cu)
def overlaps(item, shape):
    return fill.Collide(shape, 0)
for fp in b.GetFootprints():
    for pad in fp.Pads():
        if pad.GetNetname() == "GND" and pad.HasHole():
            eff = pad.GetEffectiveShape(B_Cu)
            print(f"GND PTH pad {fp.GetReference()}.{pad.GetNumber()} "
                  f"touches B.Cu fill: {overlaps(pad, eff)}")
for t in b.GetTracks():
    if t.GetClass() == "PCB_VIA":
        eff = t.GetEffectiveShape(B_Cu)
        print(f"via at {t.GetPosition()} net={t.GetNetname()} "
              f"touches B.Cu fill: {fill.Collide(eff, 0)}")

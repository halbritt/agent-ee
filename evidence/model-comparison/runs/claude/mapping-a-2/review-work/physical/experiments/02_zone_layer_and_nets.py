#!/usr/bin/env python3
"""Clarify zone layer set, pad-3 annulus fill, and per-net connected clusters."""
import pcbnew

b = pcbnew.LoadBoard("/candidate/project/rc_filter.kicad_pcb")

for z in b.Zones():
    ls = z.GetLayerSet()
    layers = [pcbnew.BOARD.GetStandardLayerName(l) for l in ls.Seq()]
    print("zone net:", z.GetNetname())
    print("  GetLayer():", z.GetLayer(), "GetLayerSet:", layers)
    print("  IsOnLayer(B_Cu):", z.IsOnLayer(pcbnew.B_Cu),
          "IsOnLayer(F_Cu):", z.IsOnLayer(pcbnew.F_Cu))
    fill_b = z.GetFilledPolysList(pcbnew.B_Cu)
    print("  B.Cu fill outlines:", fill_b.OutlineCount())
    f = fill_b

    def contains(xmm, ymm):
        return f.Contains(pcbnew.VECTOR2I(pcbnew.FromMM(xmm), pcbnew.FromMM(ymm)))
    # J1 pad3 annulus: hole r=0.5, pad r=0.85 -> test r=0.7 at 4 points
    import math
    cx, cy = 100.0, 105.08
    pts = [(cx + 0.7*math.cos(a), cy + 0.7*math.sin(a))
           for a in [0, math.pi/2, math.pi, 3*math.pi/2]]
    print("  annulus r=0.7 samples in fill:", [contains(x, y) for x, y in pts])

conn = b.GetConnectivity()
print("\nratsnest edges (missing connections):")
for e in conn.GetRatsnestForItems([]):
    print(" edge", e)

# per-net: list items via board traversal and KiCad's IsConnected tests
print("\ntracks/vias:")
for t in b.GetTracks():
    cls = t.GetClass()
    s, e = t.GetStart(), t.GetEnd()
    print(" ", cls, t.GetNetname(),
          (pcbnew.ToMM(s.x), pcbnew.ToMM(s.y)), (pcbnew.ToMM(e.x), pcbnew.ToMM(e.y)),
          "layer", t.GetLayerName() if cls != "PCB_VIA" else "F-B")

print("\nKiCad unconnected count:", conn.GetUnconnectedCount(False))

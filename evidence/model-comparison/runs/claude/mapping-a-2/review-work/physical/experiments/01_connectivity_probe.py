#!/usr/bin/env python3
"""Independent saved-board connectivity probe for rc_filter.kicad_pcb.

Loads the read-only candidate board with pcbnew 10.0.7 and:
 1. Dumps nets/pads/tracks/vias/zones.
 2. Uses KiCad's connectivity algorithm on the SAVED zone fill to list
    unconnected items per net.
 3. Geometrically tests the saved B.Cu GND zone fill polygon:
    - does it contain the via position (113,100)?
    - does copper cross the thermal gap ring around J1 pad 3 (100,105.08)
      (i.e. do thermal spokes exist, and how many)?
    - is the fill a single connected outline?
"""
import math
import pcbnew

BOARD = "/candidate/project/rc_filter.kicad_pcb"
b = pcbnew.LoadBoard(BOARD)

print("== nets ==")
for code, net in b.GetNetsByNetcode().items():
    print(code, repr(net.GetNetname()))

print("\n== pads ==")
for fp in b.GetFootprints():
    for pad in fp.Pads():
        p = pad.GetPosition()
        print(fp.GetReference(), pad.GetNumber(), pad.GetNetname(),
              pcbnew.ToMM(p.x), pcbnew.ToMM(p.y),
              "drill", pcbnew.ToMM(pad.GetDrillSizeX()))

print("\n== connectivity (saved fill) ==")
conn = b.GetConnectivity()
# unconnected count per KiCad ratsnest on saved state
try:
    print("GetUnconnectedCount(visible-only=False):", conn.GetUnconnectedCount(False))
except TypeError:
    print("GetUnconnectedCount():", conn.GetUnconnectedCount())

zones = b.Zones()
print("\n== zones ==")
for z in zones:
    print("zone net", z.GetNetname(), "layer", z.GetLayerName(),
          "IsFilled", z.IsFilled())
    fill = z.GetFilledPolysList(pcbnew.B_Cu)
    print("outlines:", fill.OutlineCount(), "holes in outline 0:", fill.HoleCount(0))
    print("fill area mm^2:", pcbnew.ToMM(pcbnew.ToMM(int(fill.Area()))))

    # point-in-polygon tests on saved fill
    def contains(xmm, ymm):
        return fill.Contains(pcbnew.VECTOR2I(pcbnew.FromMM(xmm), pcbnew.FromMM(ymm)))

    print("via (113,100) in fill:", contains(113, 100))
    print("point under J1.3 annulus (100,104.70):", contains(100, 104.70))
    print("point in J1.3 thermal gap N (100,104.00):", contains(100, 104.00))
    print("mid-board point (108,105) in fill:", contains(108, 105))

    # count thermal spokes: sample ring at r=1.0mm around J1 pad3 center
    cx, cy = 100.0, 105.08
    r = 1.0  # between pad radius 0.85 and gap outer 1.35
    states = []
    N = 720
    for i in range(N):
        a = 2 * math.pi * i / N
        states.append(contains(cx + r * math.cos(a), cy + r * math.sin(a)))
    # count contiguous True runs (wrap-around)
    runs = 0
    for i in range(N):
        if states[i] and not states[i - 1]:
            runs += 1
    if all(states):
        runs = 1
    print("J1.3 spoke crossings on r=1.0mm ring:", runs,
          "coverage:", sum(states), "/", N)

    # same ring test around the via (should be solid)
    cx, cy = 113.0, 100.0
    statesv = [contains(cx + 0.4 * math.cos(2*math.pi*i/N), cy + 0.4*math.sin(2*math.pi*i/N)) for i in range(N)]
    print("via ring r=0.4mm coverage:", sum(statesv), "/", N)

print("\n== per-net item connectivity (KiCad algo) ==")
for code, net in b.GetNetsByNetcode().items():
    if code == 0:
        continue
    items = conn.GetNetItems(code, [pcbnew.PCB_PAD_T, pcbnew.PCB_TRACE_T,
                                    pcbnew.PCB_VIA_T, pcbnew.PCB_ZONE_T])
    print(net.GetNetname(), "items:", len(items))

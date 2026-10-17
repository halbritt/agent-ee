import pcbnew
b = pcbnew.LoadBoard("/candidate/project/rc_filter.kicad_pcb")
b.BuildConnectivity()
for z in b.Zones():
    ls = z.GetLayerSet()
    layers = [pcbnew.BOARD.GetStandardLayerName(l) for l in ls.Seq()]
    print("zone declared layers:", layers, "| GetFirstLayer:", pcbnew.BOARD.GetStandardLayerName(z.GetFirstLayer()))
    poly = z.GetFilledPolysList(pcbnew.B_Cu)
    # points just outside J1 pad3 copper (pad radius 0.85mm) along diagonals (thermal spokes are at 45 deg for circle pads)
    import math
    cx, cy = 100.0, 105.08
    for ang in (45,135,225,315,0,90,180,270):
        r = 0.86
        x = cx + r*math.cos(math.radians(ang)); y = cy + r*math.sin(math.radians(ang))
        pt = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
        print(f"  fill present at {ang:3d}deg just outside pad3 edge: {poly.Contains(pt)}")
# copper clusters per net via connectivity: pads connected to zone?
conn = b.GetConnectivity()
for fp in b.GetFootprints():
    for p in fp.Pads():
        items = conn.GetConnectedItems(p)
        kinds = sorted({type(i).__name__ for i in items})
        print(f"{fp.GetReference()}.{p.GetNumber()} ({p.GetNetname()}): connected to {kinds}")

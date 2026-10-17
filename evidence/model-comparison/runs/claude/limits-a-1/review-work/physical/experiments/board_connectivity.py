# Independent pcbnew check: pad nets, track widths, zone fill connectivity as saved.
import pcbnew
b = pcbnew.LoadBoard("/candidate/project/rc_filter.kicad_pcb")
print("pcbnew:", pcbnew.GetBuildVersion())
for fp in b.GetFootprints():
    for p in fp.Pads():
        print(f"{fp.GetReference()}.{p.GetNumber()} net={p.GetNetname()} pos={p.GetPosition()}")
for t in b.GetTracks():
    print(type(t).__name__, t.GetNetname(), "w=", t.GetWidth()/1e6, "mm")
# connectivity
b.BuildConnectivity()
conn = b.GetConnectivity()
for n in range(1, b.GetNetCount()):
    ni = b.FindNet(n)
    if ni:
        print("net", ni.GetNetname())
print("unconnected count:", conn.GetUnconnectedCount(True))

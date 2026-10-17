import pcbnew
b = pcbnew.LoadBoard("/candidate/project/rc_filter.kicad_pcb")
b.BuildConnectivity()
conn = b.GetConnectivity()

print("pads and nets:")
pads = {}
for fp in b.GetFootprints():
    for p in fp.Pads():
        key = f"{fp.GetReference()}.{p.GetNumber()}"
        pads[key] = p
        print(f"  {key}: net={p.GetNetname()} pos={p.GetPosition()}")

print("\nunconnected (ratsnest) count:", conn.GetUnconnectedCount(True))

# Pairwise copper-path check between pads on the same net using connectivity clusters
from itertools import combinations
import collections
bynet = collections.defaultdict(list)
for k,p in pads.items(): bynet[p.GetNetname()].append(k)
for net, ks in sorted(bynet.items()):
    for a,bk in combinations(ks,2):
        # Use ratsnest: if no missing connections for the net, pads are copper-connected
        pass
    print(f"net {net}: pads {ks}")

# zone fill state
for z in b.Zones():
    print(f"\nzone net={z.GetNetname()} layer={z.GetLayerName()} IsFilled={z.IsFilled()}")
    poly = z.GetFilledPolysList(pcbnew.B_Cu)
    print("  filled outline count:", poly.OutlineCount())
    # check key points inside filled copper
    for name,(x,y) in {"via(113,100)":(113.0,100.0)}.items():
        pt = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
        print(f"  contains {name}:", poly.Contains(pt))

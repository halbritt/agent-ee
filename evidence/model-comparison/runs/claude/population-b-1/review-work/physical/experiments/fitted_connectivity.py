#!/usr/bin/env python3
"""Fitted-assembly DC connectivity check for /candidate/project/rc_filter.kicad_pcb.

Builds a copper-contact graph from the saved board (tracks, vias, pads, filled
zone) but models component bodies as conductors ONLY for footprints that are
fitted (not DNP). Per the exercise criterion, an unpopulated footprint must not
be assumed to conduct, so a DNP footprint's pads remain board copper but its
pads are NOT bridged to each other. The check then asks whether any conductive
path joins J1 pad 1 (/IN) to J1 pad 2 (/OUT).

Assumptions:
- Pads of a fitted 2-terminal passive (R, C) are bridged by the part body.
- Connector J1 pins are electrically distinct (no internal bridges).
- Copper contact is tested geometrically: segment endpoints inside/touching a
  pad or via annulus, collinear overlap on shared net not needed (small board,
  endpoint coincidence is exact here).
- Zone (B.Cu, GND) connects only to items with copper on B.Cu whose net is GND
  (KiCad zone fills never connect foreign nets; also verified by DRC clearance
  pass). It is irrelevant to /IN--/OUT anyway but included for completeness.
"""
import pcbnew, math, json

board = pcbnew.LoadBoard("/candidate/project/rc_filter.kicad_pcb")

nodes = []   # (kind, label, net, layerset, x_mm, y_mm, halfsize or radius)
def mm(v): return v / 1e6

pads = []
for fp in board.GetFootprints():
    ref = fp.GetReference()
    dnp = fp.IsDNP()
    for pad in fp.Pads():
        p = pad.GetPosition()
        sz = pad.GetSize(pcbnew.PADSTACK.ALL_LAYERS)
        pads.append(dict(ref=ref, pad=pad.GetNumber(), dnp=dnp,
                         net=pad.GetNetname(),
                         x=mm(p.x), y=mm(p.y),
                         hx=mm(sz.x)/2, hy=mm(sz.y)/2,
                         tht=pad.GetAttribute()==pcbnew.PAD_ATTRIB_PTH))

tracks = []
vias = []
for t in board.GetTracks():
    if t.GetClass() == "PCB_VIA":
        p = t.GetPosition()
        vias.append(dict(net=t.GetNetname(), x=mm(p.x), y=mm(p.y),
                         r=mm(t.GetWidth())/2))
    else:
        s, e = t.GetStart(), t.GetEnd()
        tracks.append(dict(net=t.GetNetname(), layer=t.GetLayerName(),
                           sx=mm(s.x), sy=mm(s.y), ex=mm(e.x), ey=mm(e.y),
                           w=mm(t.GetWidth())))

# --- union-find over conductor elements ---
elems = []  # label strings
def add(label):
    elems.append(label); return len(elems)-1
parent = {}
def find(a):
    while parent.setdefault(a, a) != a:
        parent[a] = parent[parent[a]]; a = parent[a]
    return a
def union(a, b):
    parent[find(a)] = find(b)

pad_id = {}
for pd in pads:
    pad_id[(pd['ref'], pd['pad'])] = add(f"{pd['ref']}.{pd['pad']}({pd['net']})")
trk_id = [add(f"track{i}({t['net']})") for i, t in enumerate(tracks)]
via_id = [add(f"via{i}({v['net']})") for i, v in enumerate(vias)]

def pt_on_pad(x, y, pd, tol=1e-6):
    return (abs(x - pd['x']) <= pd['hx'] + tol and
            abs(y - pd['y']) <= pd['hy'] + tol)

# track endpoint touching pad copper (SMD pads front layer only; all tracks F.Cu here)
for i, t in enumerate(tracks):
    for pd in pads:
        if pt_on_pad(t['sx'], t['sy'], pd) or pt_on_pad(t['ex'], t['ey'], pd):
            union(trk_id[i], pad_id[(pd['ref'], pd['pad'])])
    for j, v in enumerate(vias):
        for (x, y) in ((t['sx'], t['sy']), (t['ex'], t['ey'])):
            if math.hypot(x - v['x'], y - v['y']) <= v['r'] + 1e-6:
                union(trk_id[i], via_id[j])
# track-to-track contact: endpoint of one within half-width of the other's
# segment (covers both coincident endpoints and mid-segment T-junctions)
def pt_seg_dist(px, py, t):
    dx, dy = t['ex'] - t['sx'], t['ey'] - t['sy']
    L2 = dx*dx + dy*dy
    u = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px-t['sx'])*dx + (py-t['sy'])*dy)/L2))
    return math.hypot(px - (t['sx'] + u*dx), py - (t['sy'] + u*dy))

for i, a in enumerate(tracks):
    for j, b in enumerate(tracks):
        if j <= i: continue
        if a['layer'] != b['layer']: continue
        touch = any(pt_seg_dist(x, y, b) <= (a['w'] + b['w'])/2 + 1e-6
                    for (x, y) in ((a['sx'], a['sy']), (a['ex'], a['ey'])))
        touch = touch or any(pt_seg_dist(x, y, a) <= (a['w'] + b['w'])/2 + 1e-6
                             for (x, y) in ((b['sx'], b['sy']), (b['ex'], b['ey'])))
        if touch:
            union(trk_id[i], trk_id[j])

# zone: B.Cu GND fill -> connects GND THT pads and GND vias (conservative, by net+layer reach)
zone_node = add("zone(GND,B.Cu)")
for pd in pads:
    if pd['net'] == 'GND' and pd['tht']:
        union(zone_node, pad_id[(pd['ref'], pd['pad'])])
for j, v in enumerate(vias):
    if v['net'] == 'GND':
        union(zone_node, via_id[j])

# component bodies at DC: bridge pads only of FITTED resistive parts (R*).
# A fitted capacitor is open at DC; a DNP footprint conducts nothing.
bridged = []
for fp in board.GetFootprints():
    ref = fp.GetReference()
    if ref.startswith('R') and not fp.IsDNP():
        ps = [pad_id[(ref, p.GetNumber())] for p in fp.Pads()]
        for k in range(1, len(ps)):
            union(ps[0], ps[k])
        bridged.append(ref)

src = pad_id[('J1', '1')]   # /IN at connector
dst = pad_id[('J1', '2')]   # /OUT at connector
connected = find(src) == find(dst)

groups = {}
for idx, lbl in enumerate(elems):
    groups.setdefault(find(idx), []).append(lbl)

result = dict(
    pcbnew_version=pcbnew.GetBuildVersion(),
    dnp_footprints=[fp.GetReference() for fp in board.GetFootprints() if fp.IsDNP()],
    fitted_bodies_bridged=bridged,
    fitted_path_IN_to_OUT=connected,
    islands=sorted(groups.values(), key=len, reverse=True),
    r1_pad_gap_mm=round((105.9125 - 1.025/2) - (104.0875 + 1.025/2), 4),
)
print(json.dumps(result, indent=2))
with open("/work/experiments/fitted_connectivity.json", "w") as f:
    json.dump(result, f, indent=2)

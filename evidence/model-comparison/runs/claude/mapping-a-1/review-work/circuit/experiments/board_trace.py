#!/usr/bin/env python3
"""Independent physical connectivity trace of /candidate/project/rc_filter.kicad_pcb.

Parses the saved board file directly (sexp text) and builds a copper graph:
 - pads (with footprint placement applied; all footprints at rotation 0 here)
 - track segments (F.Cu)
 - via (F.Cu<->B.Cu)
 - saved B.Cu zone filled_polygon (point-in-polygon test, no refill)
Reports which pads end up electrically common, and compares against the
declared nets, to confirm the saved copper implements the J1 contract:
pin1=IN, pin2=OUT(filtered node = R1.2/C1.1), pin3=GND.
"""
import math, re, sys

src = open('/candidate/project/rc_filter.kicad_pcb').read()

# ---- minimal s-expression tokenizer/parser ----
def parse(s):
    toks = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', s)
    def build(it):
        out = []
        for t in it:
            if t == '(':
                out.append(build(it))
            elif t == ')':
                return out
            else:
                out.append(t.strip('"'))
        return out
    it = iter(toks)
    assert next(it) == '('
    return build(it)

tree = parse(src)

def find_all(node, key):
    res = []
    if isinstance(node, list):
        if node and node[0] == key:
            res.append(node)
        for c in node:
            res.extend(find_all(c, key))
    return res

def get(node, key):
    for c in node:
        if isinstance(c, list) and c and c[0] == key:
            return c
    return None

pads = []   # (name, net, x, y, size, shape, layers, through_hole)
for fp in find_all(tree, 'footprint'):
    ref = None
    for prop in find_all(fp, 'property'):
        if prop[1] == 'Reference':
            ref = prop[2]
    at = get(fp, 'at')
    fx, fy = float(at[1]), float(at[2])
    rot = float(at[3]) if len(at) > 3 else 0.0
    assert rot == 0.0, 'unexpected footprint rotation'
    for pad in find_all(fp, 'pad'):
        pat = get(pad, 'at')
        px, py = fx + float(pat[1]), fy + float(pat[2])
        net = get(pad, 'net')
        netname = net[1] if net else None
        size = get(pad, 'size')
        s = max(float(size[1]), float(size[2])) / 2.0
        tht = pad[2] == 'thru_hole'
        pads.append({'id': f'{ref}.{pad[1]}', 'net': netname, 'x': px, 'y': py,
                     'r': s, 'tht': tht})

segs = []
for seg in find_all(tree, 'segment'):
    st, en = get(seg, 'start'), get(seg, 'end')
    net = get(seg, 'net')[1]
    segs.append({'a': (float(st[1]), float(st[2])), 'b': (float(en[1]), float(en[2])),
                 'w': float(get(seg, 'width')[1]), 'net': net, 'layer': get(seg, 'layer')[1]})

vias = []
for v in find_all(tree, 'via'):
    at = get(v, 'at')
    vias.append({'x': float(at[1]), 'y': float(at[2]), 'r': float(get(v, 'size')[1])/2,
                 'net': get(v, 'net')[1]})

zones = []
for z in find_all(tree, 'zone'):
    net = get(z, 'net')[1]
    layer = get(z, 'layer')[1]
    polys = []
    for fpoly in find_all(z, 'filled_polygon'):
        pts = get(fpoly, 'pts')
        poly = [(float(p[1]), float(p[2])) for p in pts[1:]]
        polys.append(poly)
    zones.append({'net': net, 'layer': layer, 'polys': polys})

def pip(pt, poly):
    # ray casting point-in-polygon
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i+1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xi > x:
                inside = not inside
    return inside

def seg_pt_dist(a, b, p):
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx-ax, by-ay
    L2 = dx*dx + dy*dy
    t = 0 if L2 == 0 else max(0, min(1, ((px-ax)*dx + (py-ay)*dy)/L2))
    cx, cy = ax + t*dx, ay + t*dy
    return math.hypot(px-cx, py-cy)

def seg_seg_dist(s1, s2):
    cands = [seg_pt_dist(s1['a'], s1['b'], s2['a']), seg_pt_dist(s1['a'], s1['b'], s2['b']),
             seg_pt_dist(s2['a'], s2['b'], s1['a']), seg_pt_dist(s2['a'], s2['b'], s1['b'])]
    return min(cands)

# ---- union-find over copper items ----
items = []
for p in pads: items.append(('pad', p))
for s in segs: items.append(('seg', s))
for v in vias: items.append(('via', v))
for z in zones: items.append(('zone', z))

parent = list(range(len(items)))
def find(i):
    while parent[i] != i:
        parent[i] = parent[parent[i]]; i = parent[i]
    return i
def union(i, j):
    parent[find(i)] = find(j)

def layers_of(kind, obj):
    if kind == 'pad':
        return {'F.Cu', 'B.Cu'} if obj['tht'] else {'F.Cu'}
    if kind == 'seg':
        return {obj['layer']}
    if kind == 'via':
        return {'F.Cu', 'B.Cu'}
    if kind == 'zone':
        return {obj['layer']}

def touch(ki, oi, kj, oj):
    if not (layers_of(ki, oi) & layers_of(kj, oj)):
        return False
    if ki == 'zone' or kj == 'zone':
        if kj == 'zone':
            ki, oi, kj, oj = kj, oj, ki, oi
        # zone(oi) vs other(oj): sample center point(s) of other inside fill
        shared = layers_of(ki, oi) & layers_of(kj, oj)
        if not shared:
            return False
        if kj == 'pad' or kj == 'via':
            # check pad/via disc overlaps fill: sample center and ring points
            pts = [(oj['x'], oj['y'])]
            for ang in range(0, 360, 15):
                rr = oj['r'] * 0.99
                pts.append((oj['x'] + rr*math.cos(math.radians(ang)),
                            oj['y'] + rr*math.sin(math.radians(ang))))
            return any(pip(pt, poly) for poly in oi['polys'] for pt in pts)
        if kj == 'seg':
            # sample along segment
            n = 50
            ax, ay = oj['a']; bx, by = oj['b']
            pts = [(ax + (bx-ax)*t/n, ay + (by-ay)*t/n) for t in range(n+1)]
            return any(pip(pt, poly) for poly in oi['polys'] for pt in pts)
    if ki == 'seg' and kj == 'seg':
        return seg_seg_dist(oi, oj) <= (oi['w'] + oj['w'])/2 + 1e-6
    if 'seg' in (ki, kj):
        if kj == 'seg':
            ki, oi, kj, oj = kj, oj, ki, oi
        # seg oi vs disc oj
        return seg_pt_dist(oi['a'], oi['b'], (oj['x'], oj['y'])) <= oi['w']/2 + oj['r'] + 1e-6
    # disc vs disc
    return math.hypot(oi['x']-oj['x'], oi['y']-oj['y']) <= oi['r'] + oj['r'] + 1e-6

for i in range(len(items)):
    for j in range(i+1, len(items)):
        ki, oi = items[i]; kj, oj = items[j]
        if touch(ki, oi, kj, oj):
            union(i, j)

groups = {}
for idx, (k, o) in enumerate(items):
    label = o['id'] if k == 'pad' else f"{k}:{o['net']}"
    groups.setdefault(find(idx), []).append((label, o['net']))

print('Physically connected copper groups (saved fill, no refill):')
contract_ok = True
for g in groups.values():
    labels = sorted(l for l, n in g)
    nets = sorted(set(n for l, n in g))
    print(' ', labels, 'nets:', nets)
    if len(nets) != 1:
        print('   !! MIXED NETS IN ONE COPPER GROUP')
        contract_ok = False

pads_by_group = {}
for idx, (k, o) in enumerate(items):
    if k == 'pad':
        pads_by_group.setdefault(find(idx), set()).add(o['id'])

expected = [{'J1.1', 'R1.1'}, {'J1.2', 'R1.2', 'C1.1'}, {'J1.3', 'C1.2'}]
actual = sorted(pads_by_group.values(), key=lambda s: sorted(s))
print()
print('Expected pad groups per contract:', expected)
print('Actual pad groups from copper   :', actual)
ok = all(e in actual for e in expected) and len(actual) == 3
print('CONTRACT MATCH:', ok and contract_ok)

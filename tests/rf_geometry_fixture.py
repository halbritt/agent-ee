"""Original generated grounded-CPW boards for rf_geometry tests (no library or vendor assets).

A four-layer board carries a branched RF net with the pod's terminal identities
(J2.1 SMA launch, U3.11 receiver input, D3.1 ESD and L3.2 bias feed): a trunk
with a 45 degree bend and two T-junction stubs on F.Cu, a GND pour on F.Cu
whose clearance sets the coplanar gap, a GND plane on In1.Cu and GND stitching
vias on both sides of every branch. Options produce violating variants.
"""
import math

TRUNK = [(3.0, 8.0), (13.0, 8.0), (15.0, 10.0), (27.0, 10.0)]
STUBS = {'D3': [(8.0, 8.0), (8.0, 5.0)], 'L3': [(20.0, 10.0), (20.0, 13.0)]}
TERMINALS = {('J2', '1'): (3.0, 8.0), ('U3', '11'): (27.0, 10.0), ('D3', '1'): (8.0, 5.0), ('L3', '2'): (20.0, 13.0)}
OUTLINE = (30.0, 17.0)
RF_NET, GND_NET = '/POD_GNSS_RF', 'GND'
VIA_OFFSET, VIA_STEP = 0.75, 1.0


def _mm(value):
    return round(value * 1e6)


def via_sites(paths, keepout=0.6, step=VIA_STEP, offset=VIA_OFFSET):
    """Stitching via centres beside each polyline, clear of every other RF centreline and pad."""
    lines = [seg for path in paths for seg in zip(path, path[1:])]

    def distance(p, a, b):
        ab = (b[0] - a[0], b[1] - a[1])
        t = max(0.0, min(1.0, ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / (ab[0] ** 2 + ab[1] ** 2)))
        return math.dist(p, (a[0] + t * ab[0], a[1] + t * ab[1]))
    sites = []
    for path in paths:
        for a, b in zip(path, path[1:]):
            length = math.dist(a, b)
            ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
            for i in range(1, math.ceil(length / step)):
                s = i * step
                for sign in (1, -1):
                    p = (a[0] + ux * s - sign * uy * offset, a[1] + uy * s + sign * ux * offset)
                    if min(distance(p, c, d) for c, d in lines) < offset - 1e-9 or \
                            any(math.dist(p, t) < offset + keepout for t in TERMINALS.values()):
                        continue
                    sites.append(p)
    return sites


def make_board(path, *, width=0.335, gap=0.2032, ground_signal=True, reference=True, reference_slot=False,
               fill=True, drop_vias=(), d3_stub=None, dangling=False, undeclared_pad=False, foreign=False,
               unrouted_l3=False, vias=True, b_cu_hop=False, arc_bend=False):
    """arc_bend replaces the 45 degree bend with a quarter arc (centre (13, 10), radius 2)."""
    import pcbnew as k
    board = k.BOARD()
    board.SetCopperLayerCount(4)
    # The default netclass clearance would otherwise floor every zone clearance (and so the gap) at 0.2 mm.
    board.GetDesignSettings().m_NetSettings.GetDefaultNetclass().SetClearance(_mm(0.1))
    nets = {}
    for name in (RF_NET, GND_NET, '/BIAS', '/OTHER'):
        nets[name] = k.NETINFO_ITEM(board, name)
        board.Add(nets[name])

    def vec(p):
        return k.VECTOR2I(_mm(p[0]), _mm(p[1]))

    def pad(ref, number, position, net, size=0.6):
        fp = next((f for f in board.GetFootprints() if f.GetReference() == ref), None)
        if fp is None:
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            board.Add(fp)
        item = k.PAD(fp)
        item.SetNumber(number)
        item.SetAttribute(k.PAD_ATTRIB_SMD)
        item.SetSize(k.VECTOR2I(_mm(size), _mm(size)))
        layers = k.LSET()
        layers.AddLayer(k.F_Cu)
        item.SetLayerSet(layers)
        item.SetPosition(vec(position))
        item.SetNet(nets[net])
        fp.Add(item)

    def track(points, net=RF_NET, layer=None, w=width):
        for a, b in zip(points, points[1:]):
            t = k.PCB_TRACK(board)
            t.SetStart(vec(a))
            t.SetEnd(vec(b))
            t.SetLayer(k.F_Cu if layer is None else layer)
            t.SetWidth(_mm(w))
            t.SetNet(nets[net])
            board.Add(t)

    def via(position, net):
        v = k.PCB_VIA(board)
        v.SetPosition(vec(position))
        v.SetWidth(_mm(0.45))
        v.SetDrill(_mm(0.25))
        v.SetViaType(k.VIATYPE_THROUGH)
        v.SetLayerPair(k.F_Cu, k.B_Cu)
        v.SetNet(nets[net])
        board.Add(v)

    for (ref, number), position in TERMINALS.items():
        pad(ref, number, position, RF_NET)
    pad('D3', '2', (8.0, 3.8), GND_NET)
    pad('L3', '1', (20.0, 14.2), '/BIAS')
    stubs = dict(STUBS)
    if d3_stub is not None:
        stubs['D3'] = d3_stub
    if b_cu_hop:
        # Trunk detours through B.Cu between x=17 and x=19 (two RF vias).
        track([(15.0, 10.0), (17.0, 10.0)])
        track([(17.0, 10.0), (19.0, 10.0)], layer=k.B_Cu)
        track([(19.0, 10.0), (27.0, 10.0)])
        track(TRUNK[:3])
        via((17.0, 10.0), RF_NET)
        via((19.0, 10.0), RF_NET)
    elif arc_bend:
        track(TRUNK[:2])
        arc = k.PCB_ARC(board)
        arc.SetStart(vec((13.0, 8.0)))
        arc.SetMid(vec((13.0 + math.sqrt(2), 10.0 - math.sqrt(2))))
        arc.SetEnd(vec((15.0, 10.0)))
        arc.SetLayer(k.F_Cu)
        arc.SetWidth(_mm(width))
        arc.SetNet(nets[RF_NET])
        board.Add(arc)
        track(TRUNK[2:])
    else:
        track(TRUNK)
    track(stubs['D3'])
    if not unrouted_l3:
        track(stubs['L3'])
    if dangling:
        track([(24.0, 10.0), (24.0, 7.5)])
    if undeclared_pad:
        pad('C9', '1', (11.0, 6.0), RF_NET)
        track([(11.0, 8.0), (11.0, 6.0)])
    if foreign:
        offset = width / 2 + gap / 2
        track([(4.5, 8.0 + offset + 0.05), (6.5, 8.0 + offset + 0.05)], net='/OTHER', w=0.1)
    if vias:
        paths = [TRUNK, *stubs.values()] + ([[(24.0, 10.0), (24.0, 7.5)]] if dangling else [])
        for index, site in enumerate(via_sites(paths)):
            if index not in drop_vias:
                via(site, GND_NET)

    def zone(layer, clearance, slot=None):
        z = k.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(nets[GND_NET])
        z.SetLocalClearance(_mm(clearance))
        z.SetMinThickness(_mm(0.1))
        z.SetPadConnection(k.ZONE_CONNECTION_FULL)
        outline = z.Outline()
        outline.NewOutline()
        for x, y in ((0.5, 0.5), (OUTLINE[0] - 0.5, 0.5), (OUTLINE[0] - 0.5, OUTLINE[1] - 0.5),
                     (0.5, OUTLINE[1] - 0.5)):
            outline.Append(_mm(x), _mm(y))
        if slot:
            outline.NewHole()
            for x, y in slot:
                outline.Append(_mm(x), _mm(y), -1, 0)
        board.Add(z)

    if ground_signal:
        zone(k.F_Cu, gap)
    if reference:
        zone(k.In1_Cu, 0.2, slot=[(20.5, 9.0), (21.5, 9.0), (21.5, 11.0), (20.5, 11.0)] if reference_slot else None)
    corners = [(0, 0), (OUTLINE[0], 0), OUTLINE, (0, OUTLINE[1])]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        edge = k.PCB_SHAPE(board)
        edge.SetShape(k.SHAPE_T_SEGMENT)
        edge.SetLayer(k.Edge_Cuts)
        edge.SetStart(vec(a))
        edge.SetEnd(vec(b))
        edge.SetWidth(_mm(0.05))
        board.Add(edge)
    if fill:
        k.ZONE_FILLER(board).Fill(board.Zones())
    k.SaveBoard(str(path), board)


def declaration(**overrides):
    """Owned control declaration: targets from the captured W1/D1 values, owned control tolerances."""
    doc = {'schema': 'agent-ee.rf-geometry/1', 'net': RF_NET, 'ground_net': GND_NET,
           'signal_layer': 'F.Cu', 'reference_layer': 'In1.Cu',
           'cpw': {'width_mm': 0.334416796875, 'gap_mm': 0.2032, 'width_tolerance_mm': 0.0127,
                   'gap_tolerance_mm': 0.0254},
           'stitching': {'max_pitch_mm': 2.5, 'band_mm': [0.5, 1.5]},
           'topology': {'trunk': {'from': 'J2.1', 'to': 'U3.11', 'max_length_mm': 30.0},
                        'stubs': [{'name': 'esd', 'terminal': 'D3.1', 'max_length_mm': 3.5},
                                  {'name': 'bias', 'terminal': 'L3.2', 'max_length_mm': 3.5}]},
           'basis': 'Owned test control values; not a design capture.'}
    doc.update(overrides)
    return doc


# Owned stand-in for the captured RF calculation record: the same shape and values as the
# AGENTEE-186 status-0 back-calculation (W1 13.166015625 mil, D1 8 mil), authored here.
CALCULATION = {'schema': 'agent-ee.test-rf-calculation/1', 'result_valid': 1,
               'geometry_mil': {'W1': 13.166015625, 'W2': 12.666015625, 'D1': 8}}


def sourced_declaration(path='project/rf-calculation.json', **overrides):
    """Declaration whose CPW targets are read from the captured calculation by JSON pointer."""
    doc = declaration(**overrides)
    doc['source'] = {'path': path, 'width_pointer': '/geometry_mil/W1', 'gap_pointer': '/geometry_mil/D1',
                     'unit': 'mil'}
    doc['cpw'] = {k: v for k, v in doc['cpw'].items() if k not in ('width_mm', 'gap_mm')}
    return doc

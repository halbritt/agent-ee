"""Original, generated DFM geometry boards (no library assets).

Every seeded feature has a compliant twin so each rule is exercised both ways.
Coordinates are millimetres on a 40 x 30 mm two-layer board.
"""
import math
import re

PROFILE = {'schema': 'agent-ee.dfm-profile/1', 'name': 'owned-dfm-fixture',
           'basis': 'thresholds ported from AI-PCB-Generator src/pcb/dfm.py (MIT)',
           'rules': [
               {'id': 'annular-ring', 'rule': 'annular_ring', 'min_ring_mm': 0.1},
               {'id': 'via-aspect', 'rule': 'via_aspect_ratio', 'max_ratio': 8},
               {'id': 'acid-trap', 'rule': 'acid_trap', 'min_angle_deg': 45},
               {'id': 'smd-bridge', 'rule': 'smd_bridge_gap', 'min_gap_mm': 0.2},
               {'id': 'component-spacing', 'rule': 'component_spacing', 'min_gap_mm': 0.1},
               {'id': 'diff-skew', 'rule': 'diff_pair_skew', 'max_skew_mm': 0.5, 'pairs': 'name-suffix/1'},
               {'id': 'copper-balance', 'rule': 'copper_balance', 'max_layer_share': 0.99}]}


def v(x, y):
    import pcbnew as k
    return k.VECTOR2I(round(x * 1e6), round(y * 1e6))


def make_board(path, *, via_size=0.4, bridge_gap=0.15, acid_angle=30.0, arc=True,
               thickness=True, courtyards=True, bottom_copper=False):
    """Seeded defects by default; each keyword selects the compliant twin."""
    import pcbnew as k
    b = k.BOARD()
    b.SetCopperLayerCount(2)
    nets = {}

    def net(name):
        if name not in nets:
            nets[name] = k.NETINFO_ITEM(b, name)
            b.Add(nets[name])
        return nets[name]

    def footprint(ref, x, pads):
        fp = k.FOOTPRINT(b); fp.SetReference(ref); fp.SetPosition(v(x, 10)); b.Add(fp)
        for number, px, name in pads:
            pad = k.PAD(fp); pad.SetNumber(number); pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetShape(k.PAD_SHAPE_RECTANGLE); pad.SetSize(v(0.6, 0.8))
            layers = k.LSET(); layers.AddLayer(k.F_Cu); pad.SetLayerSet(layers)
            fp.Add(pad); pad.SetPosition(v(px, 10)); pad.SetNet(net(name))
        if courtyards:
            xs = [p[1] for p in pads]
            corners = [(min(xs) - 0.35, 9.55), (max(xs) + 0.35, 9.55),
                       (max(xs) + 0.35, 10.45), (min(xs) - 0.35, 10.45)]
            for start, end in zip(corners, corners[1:] + corners[:1]):
                line = k.PCB_SHAPE(fp); line.SetShape(k.SHAPE_T_SEGMENT); line.SetLayer(k.F_CrtYd)
                line.SetStart(v(*start)); line.SetEnd(v(*end)); line.SetWidth(50000); fp.Add(line)

    # R1:2 (NET_A) and R2:1 (NET_B) face each other across `bridge_gap` mm.
    footprint('R1', 10.5, [('1', 10.0, 'NET_A'), ('2', 11.0, 'NET_A')])
    left = 11.3 + bridge_gap + 0.3
    footprint('R2', left + 0.5, [('1', left, 'NET_B'), ('2', left + 1.0, 'NET_B')])

    for x, size in ((20, 0.6), (22, via_size)):
        via = k.PCB_VIA(b); via.SetPosition(v(x, 5)); via.SetWidth(round(size * 1e6))
        via.SetDrill(300000); via.SetViaType(k.VIATYPE_THROUGH)
        via.SetLayerPair(k.F_Cu, k.B_Cu); via.SetNet(net('NET_V')); b.Add(via)

    def track(start, end, name, layer=None):
        t = k.PCB_TRACK(b); t.SetStart(v(*start)); t.SetEnd(v(*end)); t.SetWidth(200000)
        t.SetLayer(layer if layer is not None else k.F_Cu); t.SetNet(net(name)); b.Add(t)

    angle = math.radians(acid_angle)
    track((5, 20), (10, 20), 'NET_C')
    track((10, 20), (10 - 5 * math.cos(angle), 20 - 5 * math.sin(angle)), 'NET_C')
    track((15, 20), (20, 20), 'NET_D')
    track((20, 20), (20, 25), 'NET_D')

    # USB_D+ ends in a semicircular arc (length pi mm); USB_D- ends in its 2 mm chord.
    track((5, 25), (15, 25), 'USB_D+')
    if arc:
        a = k.PCB_ARC(b); a.SetStart(v(15, 25)); a.SetMid(v(16, 24)); a.SetEnd(v(17, 25))
        a.SetWidth(200000); a.SetLayer(k.F_Cu); a.SetNet(net('USB_D+')); b.Add(a)
    else:
        track((15, 25), (17, 25), 'USB_D+')
    track((5, 27), (15, 27), 'USB_D-')
    track((15, 27), (17, 27), 'USB_D-')
    if bottom_copper:
        for y in (3, 4, 6, 7):
            track((2, y), (38, y), 'NET_BOTTOM', k.B_Cu)

    corners = [(0, 0), (40, 0), (40, 30), (0, 30)]
    for start, end in zip(corners, corners[1:] + corners[:1]):
        edge = k.PCB_SHAPE(b); edge.SetShape(k.SHAPE_T_SEGMENT); edge.SetLayer(k.Edge_Cuts)
        edge.SetStart(v(*start)); edge.SetEnd(v(*end)); edge.SetWidth(50000); b.Add(edge)
    k.SaveBoard(str(path), b)
    if not thickness:
        text = path.read_text()
        stripped = re.sub(r'\(thickness [0-9.]+\)', '', text, count=1)
        if stripped == text:
            raise AssertionError('generated board saved no general thickness to strip')
        path.write_text(stripped)

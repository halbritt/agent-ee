"""Original owned fab-export fixture: one schematic, a 2-layer and two 4-layer boards.

pod4.kicad_pcb is the 4-layer pod probe for fab_roundtrip (AGENTEE-195): the same
parts and nets with the pad shapes KiCad plots through aperture macros (rounded,
rotated, chamfered, custom and trapezoid pads, a rotated oval), arc tracks and
saved zone fills on inner and outer copper.

Every footprint, symbol, part-binding value and coordinate here is synthetic and
written for agent-ee; nothing is copied from FOME-MC, a donor, a supplier or a
third-party library. The LCSC-style numbers (C9000xx) and manufacturer/part-number
strings are placeholders that exercise BOM grouping; they make no sourcing
claim. Boards require the qualified native pcbnew installation:

    /usr/bin/python3 fixtures/fab-export/generate.py fixtures/fab-export
"""
import argparse
import uuid
from pathlib import Path

NAMESPACE = uuid.UUID('6f1d7e0a-3c55-4a8e-9a43-0f3b1a2b5c11')
ROOT = 'fab'
QUALIFIED_PCBNEW = '10.0.7'

# ref: (symbol, value, footprint, LCSC, Manufacturer, Partnumber, dnp, side, x, y, nets by pin)
PARTS = {
    'J1': ('Conn3', 'Header 3', 'agent_fab:Header_1x3_owned', 'C900030', 'Owned Fixture Works',
           'OFW-HDR3', False, 'F', 6.0, 10.0, {'1': 'IN', '2': 'OUT', '3': 'GND'}),
    'R1': ('R', '10k', 'agent_fab:R_1608_owned', 'C900010', 'Owned Fixture Works',
           'OFW-R-10K', False, 'F', 16.0, 6.0, {'1': 'IN', '2': 'MID'}),
    'R2': ('R', '4.7kΩ', 'agent_fab:R_1608_owned', 'C900047', 'Owned Fixture Works',
           'OFW-R-4K7', False, 'B', 16.0, 14.0, {'1': 'MID', '2': 'OUT'}),
    'C1': ('C', '1µF', 'agent_fab:C_1608_owned', 'C900001', 'Owned Fixture Works',
           'OFW-C-1U', False, 'F', 24.0, 6.0, {'1': 'MID', '2': 'GND'}),
    'C2': ('C', '1µF', 'agent_fab:C_1608_owned', 'C900001', 'Owned Fixture Works',
           'OFW-C-1U', False, 'F', 24.0, 10.0, {'1': 'OUT', '2': 'GND'}),
    'C10': ('C', '1µF', 'agent_fab:C_1608_owned', 'C900001', 'Owned Fixture Works',
            'OFW-C-1U', False, 'F', 24.0, 14.0, {'1': 'OUT', '2': 'GND'}),
    'C3': ('C', '100n', 'agent_fab:C_1608_owned', 'C900100', 'Owned Fixture Works',
           'OFW-C-100N', True, 'F', 30.0, 10.0, {'1': 'MID', '2': 'GND'}),
}
PINS = {'R': ['1', '2'], 'C': ['1', '2'], 'Conn3': ['1', '2', '3']}


def uid(*parts):
    return str(uuid.uuid5(NAMESPACE, '/'.join(parts)))


def q(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def prop(name, value, hide=True):
    return (f'(property {q(name)} {q(value)} (at 0 0 0) (effects (font (size 1.27 1.27))'
            + (' (hide yes)' if hide else '') + '))')


def lib_symbol(name, pins):
    body = ''.join(
        f'(pin passive line (at 0 {-2.54 * i:.2f} 0) (length 2.54) (name {q(pin)} '
        f'(effects (font (size 1.27 1.27)))) (number {q(pin)} (effects (font (size 1.27 1.27)))))'
        for i, pin in enumerate(pins))
    return (f'(symbol {q("agent_fab:" + name)} (pin_names (offset 0)) (in_bom yes) (on_board yes) '
            f'{prop("Reference", name[0])} {prop("Value", name)} '
            f'(symbol {q(name + "_1_1")} {body}))')


def schematic():
    sheet = uid('sheet')
    out = [f'(kicad_sch (version 20250114) (generator "eeschema") (uuid {q(sheet)}) (paper "A4") (lib_symbols '
           + ''.join(lib_symbol(name, pins) for name, pins in PINS.items()) + ')']
    for index, (ref, part) in enumerate(PARTS.items()):
        symbol, value, footprint, lcsc, maker, mpn, dnp, _, _, _, nets = part
        x, y = 40 + 25 * index, 50
        pins = ''.join(f'(pin {q(pin)} (uuid {q(uid(ref, "pin", pin))}))' for pin in PINS[symbol])
        out.append(
            f'(symbol (lib_id {q("agent_fab:" + symbol)}) (at {x} {y} 0) (unit 1) (in_bom yes) '
            f'(on_board yes) (dnp {"yes" if dnp else "no"}) (uuid {q(uid(ref))}) '
            f'{prop("Reference", ref)} {prop("Value", value)} {prop("Footprint", footprint)} '
            f'{prop("LCSC", lcsc)} {prop("Manufacturer", maker)} {prop("Partnumber", mpn)}{pins}'
            f'(instances (project {q(ROOT)} (path {q("/" + sheet)} (reference {q(ref)}) (unit 1)))))')
        for i, pin in enumerate(PINS[symbol]):
            out.append(f'(label {q(nets[pin])} (at {x} {y + 2.54 * i:.2f} 0) (effects (font (size 1.27 1.27)) '
                       f'(justify left bottom)) (uuid {q(uid(ref, "label", pin))}))')
    return '\n'.join(out) + '\n)\n'


def pod_pad(p, ref, pin, pad):
    """Pod probe pad shapes; each one is plotted by KiCad 10 through an aperture macro."""
    if ref in ('R1', 'R2'):
        pad.SetShape(p.PAD_SHAPE_ROUNDRECT)
        pad.SetRoundRectRadiusRatio(0.25)
    elif ref == 'C1':
        pad.SetOrientationDegrees(30)
    elif ref == 'C2':
        pad.SetShape(p.PAD_SHAPE_CUSTOM)
        pad.SetAnchorPadShape(p.F_Cu, p.PAD_SHAPE_CIRCLE)
        pad.SetSize(p.VECTOR2I(400000, 400000))
        outline = p.VECTOR_VECTOR2I()
        for x, y in ((-450000, -475000), (450000, -475000), (300000, 475000), (-450000, 475000)):
            outline.append(p.VECTOR2I(x, y))
        pad.AddPrimitivePoly(p.F_Cu, outline, 0, True)
    elif ref == 'C10':
        pad.SetShape(p.PAD_SHAPE_CHAMFERED_RECT)
        pad.SetChamferRectRatio(p.F_Cu, 0.2)
        pad.SetChamferPositions(p.F_Cu, 1)  # top-left corner
        pad.SetOrientationDegrees(45)
    elif ref == 'C3':
        pad.SetShape(p.PAD_SHAPE_TRAPEZOID)
        pad.SetDelta(p.F_Cu, p.VECTOR2I(200000, 0))
        pad.SetOrientationDegrees(10)
    elif ref == 'J1' and pin == '2':
        pad.SetShape(p.PAD_SHAPE_OVAL)
        pad.SetSize(p.VECTOR2I(1700000, 2200000))
        pad.SetOrientationDegrees(15)


def pod_copper(p, board, nets, vec):
    """Arc tracks on both outer layers and filled zones on three copper layers."""
    def arc(start, mid, end, layer, net):
        item = p.PCB_ARC(board)
        item.SetStart(vec(*start))
        item.SetMid(vec(*mid))
        item.SetEnd(vec(*end))
        item.SetWidth(250000)
        item.SetLayer(layer)
        item.SetNet(nets[net])
        board.Add(item)

    def zone(layer, net, corners):
        item = p.ZONE(board)
        item.SetLayer(layer)
        item.SetNet(nets[net])
        outline = item.Outline()
        outline.NewOutline()
        for x, y in corners:
            point = vec(x, y)
            outline.Append(point.x, point.y)
        board.Add(item)

    arc((23.2, 10.0), (21.6, 12.0), (23.2, 14.0), p.F_Cu, 'OUT')
    arc((6.0, 12.54), (8.5, 15.5), (12.0, 16.5), p.B_Cu, 'GND')
    zone(p.In1_Cu, 'GND', ((1, 1), (35, 1), (35, 19), (1, 19)))
    zone(p.In2_Cu, 'MID', ((1, 1), (17, 1), (17, 19), (1, 19)))
    zone(p.In2_Cu, 'OUT', ((19, 1), (35, 1), (35, 19), (19, 19)))
    zone(p.B_Cu, 'GND', ((26, 2), (34, 2), (34, 18), (26, 18)))
    board.BuildConnectivity()
    p.ZONE_FILLER(board).Fill(board.Zones())


def qualified_pcbnew():
    """Import pcbnew, refusing any version but the one the committed boards were made with."""
    import pcbnew as p
    try:
        version = p.Version()
    except Exception as exc:
        raise RuntimeError(f'pcbnew version unknown ({exc!r}); fixture boards need {QUALIFIED_PCBNEW}') from exc
    if version != QUALIFIED_PCBNEW:
        raise RuntimeError(f'pcbnew {version!r} is not the qualified {QUALIFIED_PCBNEW}; refusing to generate boards')
    return p


def seed_kiids(p, label):
    """Seed pcbnew's KIID generator so every saved board is byte-reproducible.

    A seeded board's KIIDs differ depending on whether another board was built
    earlier in the process, so a warm-up board is built before seeding: each
    board's bytes are then independent of generation order and earlier boards.
    """
    p.BOARD()
    p.KIID.SeedGenerator(uuid.uuid5(NAMESPACE, 'board/' + label).int % 2 ** 32)


def make_board(path, copper_layers, pod=False):
    p = qualified_pcbnew()
    seed_kiids(p, f'{copper_layers}{"-pod" if pod else ""}')
    board = p.BOARD()
    board.SetCopperLayerCount(copper_layers)
    nets = {}
    for name in ('IN', 'MID', 'OUT', 'GND'):
        nets[name] = p.NETINFO_ITEM(board, '/' + name)  # root-sheet label net names
        board.Add(nets[name])

    def vec(x, y):
        return p.VECTOR2I(round((x + 100) * 1000000), round((y + 100) * 1000000))

    def layers(*ids):
        out = p.LSET()
        for layer in ids:
            out.AddLayer(layer)
        return out

    for ref, part in PARTS.items():
        symbol, value, footprint, lcsc, maker, mpn, dnp, side, x, y, pin_nets = part
        fp = p.FOOTPRINT(board)
        fp.SetFPID(p.LIB_ID('agent_fab', footprint.split(':', 1)[1]))
        fp.SetReference(ref)
        fp.SetValue(value)
        fp.SetPosition(vec(x, y))
        fp.SetPath(p.KIID_PATH('/' + uid(ref)))
        if symbol == 'Conn3':
            fp.SetAttributes(p.FP_THROUGH_HOLE)
            for i, pin in enumerate(PINS[symbol]):
                pad = p.PAD(fp)
                pad.SetNumber(pin)
                pad.SetAttribute(p.PAD_ATTRIB_PTH)
                pad.SetShape(p.PAD_SHAPE_CIRCLE)
                pad.SetSize(p.VECTOR2I(1700000, 1700000))
                pad.SetDrillSize(p.VECTOR2I(1000000, 1000000))
                pad.SetLayerSet(p.PAD.PTHMask())
                pad.SetPosition(vec(x, y - 2.54 + 2.54 * i))
                pad.SetNet(nets[pin_nets[pin]])
                if pod:
                    pod_pad(p, ref, pin, pad)
                fp.Add(pad)
            peg = p.PAD(fp)
            peg.SetNumber('')
            peg.SetAttribute(p.PAD_ATTRIB_NPTH)
            peg.SetShape(p.PAD_SHAPE_CIRCLE)
            peg.SetSize(p.VECTOR2I(1500000, 1500000))
            peg.SetDrillSize(p.VECTOR2I(1500000, 1500000))
            peg.SetLayerSet(p.PAD.UnplatedHoleMask())
            peg.SetPosition(vec(x - 3.0, y))
            fp.Add(peg)
        else:
            fp.SetAttributes(p.FP_SMD)
            for i, pin in enumerate(PINS[symbol]):
                pad = p.PAD(fp)
                pad.SetNumber(pin)
                pad.SetAttribute(p.PAD_ATTRIB_SMD)
                pad.SetShape(p.PAD_SHAPE_RECTANGLE)
                pad.SetSize(p.VECTOR2I(900000, 950000))
                pad.SetLayerSet(layers(p.F_Cu, p.F_Paste, p.F_Mask))
                pad.SetPosition(vec(x - 0.8 + 1.6 * i, y))
                pad.SetNet(nets[pin_nets[pin]])
                if pod:
                    pod_pad(p, ref, pin, pad)
                fp.Add(pad)
        fp.SetDNP(dnp)  # after SetAttributes, which replaces every attribute flag
        board.Add(fp)
        if side == 'B':
            fp.Flip(vec(x, y), p.FLIP_DIRECTION_TOP_BOTTOM)
        # Release controls keep their reference text clear of pads and neighbouring rows.
        if not pod:
            fp.Reference().SetFPRelativePosition(p.VECTOR2I(0, round((-6 if ref == 'J1' else -2) * 1000000)))

    def track(a, b, layer, net, width=0.25):
        segment = p.PCB_TRACK(board)
        segment.SetStart(vec(*a))
        segment.SetEnd(vec(*b))
        segment.SetWidth(round(width * 1000000))
        segment.SetLayer(layer)
        segment.SetNet(nets[net])
        board.Add(segment)

    track((6.0, 7.46), (15.2, 6.0), p.F_Cu, 'IN')
    track((16.8, 6.0), (23.2, 6.0), p.F_Cu, 'MID')
    via = p.PCB_VIA(board)
    via.SetPosition(vec(20.0, 8.0))
    via.SetWidth(600000)
    via.SetDrill(300000)
    via.SetNet(nets['MID'])
    board.Add(via)
    track((20.0, 6.0), (20.0, 8.0), p.F_Cu, 'MID')
    track((20.0, 8.0), (15.2, 14.0), p.B_Cu, 'MID')
    corners = ((0, 0), (36, 0), (36, 20), (0, 20))
    for a, b in zip(corners, corners[1:] + corners[:1]):
        edge = p.PCB_SHAPE(board)
        edge.SetShape(p.SHAPE_T_SEGMENT)
        edge.SetLayer(p.Edge_Cuts)
        edge.SetStart(vec(*a))
        edge.SetEnd(vec(*b))
        edge.SetWidth(50000)
        board.Add(edge)
    if pod:
        pod_copper(p, board, nets, vec)
    p.SaveBoard(str(path), board)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    qualified_pcbnew()  # refuse before writing anything
    args.directory.mkdir(parents=True, exist_ok=True)
    (args.directory / 'fab.kicad_sch').write_text(schematic())
    make_board(args.directory / 'fab.kicad_pcb', 2)
    make_board(args.directory / 'fab4.kicad_pcb', 4)
    make_board(args.directory / 'pod4.kicad_pcb', 4, pod=True)

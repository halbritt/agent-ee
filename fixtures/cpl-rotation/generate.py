"""Original owned CPL-rotation fixture: schematic, board and synthetic supplier footprints.

Every footprint, symbol, supplier record and coordinate here is synthetic and
written for agent-ee. Nothing is copied from KiCad's libraries, Konnect,
tscircuit, EasyEDA/LCSC or any donor design. Footprint *names* reuse KiCad
naming facts (for example `SOIC-8_3.9x4.9mm_P1.27mm`) so that prefix policy
rules are exercised; the pad geometry under each name is authored here. The
LCSC-style numbers (C9100xx) are placeholders that make no sourcing claim.

The header instances reproduce the pad-box midpoint oracle: a 2.54 mm 1x02
header with a 1.7 mm square pin 1 and a 1.7 mm round pin 2 placed at (20,20)
0 deg, (30,20) 90 deg, (40,20) 180 deg, (50,20) 270 deg and (60,20) 45 deg. U1
sits at 90 deg like the SOIC-8 that JLCPCB rendered a quarter turn off.

Boards require the qualified native pcbnew installation:

    /usr/bin/python3 fixtures/cpl-rotation/generate.py fixtures/cpl-rotation
"""
import argparse
import hashlib
import json
import uuid
from pathlib import Path

NAMESPACE = uuid.UUID('2b8f6c1e-7d4a-4f0e-9c35-5a1e0b7d2c64')
ROOT = 'cpl'
HEADER = 'agent_cpl:PinHeader_1x02_P2.54mm_owned'
BIND = ('Owned Fixture Works',)
# ref: (symbol, value, footprint, LCSC, part number, side, x, y, angle)
PARTS = {
    'JF0': ('Conn2', 'Header 2', HEADER, 'C910002', 'OFW-HDR2', 'F', 20, 20, 0),
    'JF1': ('Conn2', 'Header 2', HEADER, 'C910002', 'OFW-HDR2', 'F', 30, 20, 90),
    'JF2': ('Conn2', 'Header 2', HEADER, 'C910002', 'OFW-HDR2', 'F', 40, 20, 180),
    'JF3': ('Conn2', 'Header 2', HEADER, 'C910002', 'OFW-HDR2', 'F', 50, 20, 270),
    'JF4': ('Conn2', 'Header 2', HEADER, 'C910002', 'OFW-HDR2', 'F', 60, 20, 45),
    'JB0': ('Conn2', 'Header 2', HEADER, 'C910002', 'OFW-HDR2', 'B', 20, 45, 0),
    'JB1': ('Conn2', 'Header 2', HEADER, 'C910002', 'OFW-HDR2', 'B', 30, 45, 90),
    'R1': ('R', '10k', 'agent_cpl:R_0805_owned', 'C910010', 'OFW-R-10K', 'F', 80, 20, 0),
    'U1': ('SOIC8', 'Timer', 'agent_cpl:SOIC-8_3.9x4.9mm_P1.27mm', 'C910008', 'OFW-TMR8', 'F', 47, 35, 90),
    'D1': ('D', 'Diode', 'agent_cpl:D_SOD-123_owned', 'C910020', 'OFW-D1', 'F', 70, 40, 0),
}
PINS = {'Conn2': ['1', '2'], 'R': ['1', '2'], 'D': ['1', '2'],
        'SOIC8': ['1', '2', '3', '4', '5', '6', '7', '8']}
PIN_NAMES = {'D': {'1': 'K', '2': 'A'}}
# Footprint-local pads in mm (+Y down): number, x, y, w, h, shape, through-hole.
SOIC = ([(str(i + 1), -2.475, -1.905 + 1.27 * i, 1.95, 0.6, 'rect', False) for i in range(4)]
        + [(str(i + 5), 2.475, 1.905 - 1.27 * i, 1.95, 0.6, 'rect', False) for i in range(4)])
PADS = {
    HEADER: [('1', 0, 0, 1.7, 1.7, 'rect', True), ('2', 0, 2.54, 1.7, 1.7, 'circle', True)],
    'agent_cpl:R_0805_owned': [('1', -1.0, 0, 1.0, 1.45, 'rect', False), ('2', 1.0, 0, 1.0, 1.45, 'rect', False)],
    'agent_cpl:SOIC-8_3.9x4.9mm_P1.27mm': SOIC,
    'agent_cpl:D_SOD-123_owned': [('1', -1.6, 0, 0.9, 1.2, 'rect', False), ('2', 1.6, 0, 0.9, 1.2, 'rect', False)],
}


def uid(*parts):
    return str(uuid.uuid5(NAMESPACE, '/'.join(parts)))


def q(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def prop(name, value, hide=True):
    return (f'(property {q(name)} {q(value)} (at 0 0 0) (effects (font (size 1.27 1.27))'
            + (' (hide yes)' if hide else '') + '))')


def lib_symbol(name, pins):
    names = PIN_NAMES.get(name, {})
    body = ''.join(
        f'(pin passive line (at 0 {-2.54 * i:.2f} 0) (length 2.54) (name {q(names.get(pin, pin))} '
        f'(effects (font (size 1.27 1.27)))) (number {q(pin)} (effects (font (size 1.27 1.27)))))'
        for i, pin in enumerate(pins))
    return (f'(symbol {q("agent_cpl:" + name)} (pin_names (offset 0)) (in_bom yes) (on_board yes) '
            f'{prop("Reference", name[0])} {prop("Value", name)} '
            f'(symbol {q(name + "_1_1")} {body}))')


def schematic():
    sheet = uid('sheet')
    out = [f'(kicad_sch (version 20250114) (generator "eeschema") (uuid {q(sheet)}) (paper "A3") (lib_symbols '
           + ''.join(lib_symbol(name, pins) for name, pins in PINS.items()) + ')']
    for index, (ref, part) in enumerate(PARTS.items()):
        symbol, value, footprint, lcsc, mpn = part[:5]
        x, y = 40 + 25 * (index % 8), 50 + 40 * (index // 8)
        pins = ''.join(f'(pin {q(pin)} (uuid {q(uid(ref, "pin", pin))}))' for pin in PINS[symbol])
        out.append(
            f'(symbol (lib_id {q("agent_cpl:" + symbol)}) (at {x} {y} 0) (unit 1) (in_bom yes) '
            f'(on_board yes) (dnp no) (uuid {q(uid(ref))}) '
            f'{prop("Reference", ref)} {prop("Value", value)} {prop("Footprint", footprint)} '
            f'{prop("LCSC", lcsc)} {prop("Manufacturer", BIND[0])} {prop("Partnumber", mpn)}{pins}'
            f'(instances (project {q(ROOT)} (path {q("/" + sheet)} (reference {q(ref)}) (unit 1)))))')
        for i, pin in enumerate(PINS[symbol]):
            out.append(f'(label {q(ref + "_" + pin)} (at {x} {y + 2.54 * i:.2f} 0) (effects (font (size 1.27 1.27)) '
                       f'(justify left bottom)) (uuid {q(uid(ref, "label", pin))}))')
    return '\n'.join(out) + '\n)\n'


def make_board(path):
    import pcbnew as p
    board = p.BOARD()
    board.SetCopperLayerCount(2)

    def nm(value):
        return round(value * 1000000)

    for ref, part in PARTS.items():
        symbol, value, footprint, _, _, side, x, y, angle = part
        fp = p.FOOTPRINT(board)
        fp.SetFPID(p.LIB_ID('agent_cpl', footprint.split(':', 1)[1]))
        fp.SetReference(ref)
        fp.SetValue(value)
        fp.SetPosition(p.VECTOR2I(nm(x), nm(y)))
        fp.SetPath(p.KIID_PATH('/' + uid(ref)))
        through = PADS[footprint][0][6]
        fp.SetAttributes(p.FP_THROUGH_HOLE if through else p.FP_SMD)
        for number, px, py, w, h, shape, hole in PADS[footprint]:
            pad = p.PAD(fp)
            pad.SetNumber(number)
            pad.SetShape(p.PAD_SHAPE_RECTANGLE if shape == 'rect' else p.PAD_SHAPE_CIRCLE)
            pad.SetSize(p.VECTOR2I(nm(w), nm(h)))
            if hole:
                pad.SetAttribute(p.PAD_ATTRIB_PTH)
                pad.SetDrillSize(p.VECTOR2I(nm(1.0), nm(1.0)))
                pad.SetLayerSet(p.PAD.PTHMask())
            else:
                pad.SetAttribute(p.PAD_ATTRIB_SMD)
                layers = p.LSET()
                for layer in (p.F_Cu, p.F_Paste, p.F_Mask):
                    layers.AddLayer(layer)
                pad.SetLayerSet(layers)
            pad.SetPosition(p.VECTOR2I(nm(x + px), nm(y + py)))
            if symbol in PIN_NAMES:
                pad.SetPinFunction(PIN_NAMES[symbol][number])
            fp.Add(pad)
        board.Add(fp)
        fp.SetOrientationDegrees(angle)
        if side == 'B':
            fp.Flip(fp.GetPosition(), p.FLIP_DIRECTION_TOP_BOTTOM)
    corners = ((0, 0), (100, 0), (100, 60), (0, 60))
    for a, b in zip(corners, corners[1:] + corners[:1]):
        edge = p.PCB_SHAPE(board)
        edge.SetShape(p.SHAPE_T_SEGMENT)
        edge.SetLayer(p.Edge_Cuts)
        edge.SetStart(p.VECTOR2I(nm(a[0]), nm(a[1])))
        edge.SetEnd(p.VECTOR2I(nm(b[0]), nm(b[1])))
        edge.SetWidth(50000)
        board.Add(edge)
    p.SaveBoard(str(path), board)


# ---------------------------------------------------------- owned supplier data
# EasyEDA standard-format records written from scratch: canvas unit 10 mil, +Y
# down, package origin (4000, 3000). Only pad centres, pad numbers and pin names
# carry meaning for the rotation check; sizes are nominal.

def easy_pad(shape, cx, cy, w, h, number, ident, *, layer='1', hole_r='0'):
    points = ''
    if shape == 'RECT':
        points = ' '.join(f'{v:g}' for v in (cx - w / 2, cy - h / 2, cx + w / 2, cy - h / 2,
                                              cx + w / 2, cy + h / 2, cx - w / 2, cy + h / 2))
    return '~'.join(['PAD', shape, f'{cx:g}', f'{cy:g}', f'{w:g}', f'{h:g}', layer, '', number, hole_r,
                     points, '0', ident, '0', '', 'Y', '0', '0', '0.2', f'{cx:g},{cy:g}'])


def easy_pin(number, name, x):
    return (f'P~show~0~{number}~{x}~0~0~gpin{number}~0^^{x}~0^^M {x} 0 h 10~#880000^^'
            f'1~{x}~4~0~{name}~start~~~#0000FF^^1~{x}~-4~0~{number}~end~~~#0000FF^^0~{x}~0^^0~M {x} 0')


def easy_component(lcsc, package, pads, pins=()):
    puuid = uid('package', lcsc).replace('-', '')
    head = {'docType': '2', 'c_para': {'package': package, 'Supplier Part': lcsc, 'Manufacturer Part': 'OWNED'},
            'x': 0, 'y': 0, 'puuid': puuid, 'uuid': uid('symbol', lcsc).replace('-', '')}
    value = {'uuid': uid('component', lcsc).replace('-', ''), 'title': 'OWNED ' + lcsc, 'docType': 2,
             'lcsc': {'id': 1, 'number': lcsc}, 'szlcsc': {'id': 1, 'number': lcsc},
             'dataStr': {'head': head, 'shape': list(pins)},
             'packageDetail': {'uuid': puuid, 'title': package, 'docType': 4,
                               'dataStr': {'head': {'docType': '4', 'uuid': puuid, 'x': 4000, 'y': 3000},
                                           'shape': list(pads)}}}
    return (json.dumps(value, indent=1, sort_keys=True) + '\n').encode()


def suppliers():
    header = [easy_pad('RECT', 4000, 3000, 6.6, 6.6, '1', 'h1', layer='11', hole_r='1.9685'),
              easy_pad('ELLIPSE', 4000, 3010, 6.6, 6.6, '2', 'h2', layer='11', hole_r='1.9685')]
    chip = [easy_pad('RECT', 3996, 3000, 4, 5.7, '1', 'r1'), easy_pad('RECT', 4004, 3000, 4, 5.7, '2', 'r2')]
    # Pins 1-4 along the bottom edge left to right, 5-8 back along the top.
    soic = ([easy_pad('RECT', 3992.5 + 5 * i, 3010.6, 2.4, 7.7, str(i + 1), f's{i + 1}') for i in range(4)]
            + [easy_pad('RECT', 4007.5 - 5 * i, 2989.4, 2.4, 7.7, str(i + 5), f's{i + 5}') for i in range(4)])
    diode = [easy_pad('RECT', 3993.7, 3000, 3.5, 4.7, '1', 'd1'), easy_pad('RECT', 4006.3, 3000, 3.5, 4.7, '2', 'd2')]
    return {'C910002': easy_component('C910002', 'OWNED-HDR-2P-P2.54-V', header),
            'C910010': easy_component('C910010', 'OWNED-R0805', chip),
            'C910008': easy_component('C910008', 'OWNED-SOIC-8-P1.27', soic),
            'C910020': easy_component('C910020', 'OWNED-SOD-123', diode,
                                      [easy_pin('1', 'K', -20), easy_pin('2', 'A', 20)])}


def provenance(lcsc, rel, data):
    record = {'schema': 'agent-ee.supplier-footprint/1', 'lcsc': lcsc, 'format': 'easyeda-std-component/1',
              'path': rel, 'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data),
              'source_url': 'https://example.invalid/agent-ee-owned-fixture',
              'retrieved_at': '2026-10-17T00:00:00Z', 'retrieval': 'owned synthetic fixture; never fetched'}
    return (json.dumps(record, indent=1, sort_keys=True) + '\n').encode()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--supplier-only', action='store_true', help='rewrite supplier records only (no pcbnew)')
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    (args.directory / 'supplier').mkdir(exist_ok=True)
    for lcsc, data in suppliers().items():
        rel = f'supplier/{lcsc}.raweasy.json'
        (args.directory / rel).write_bytes(data)
        (args.directory / f'supplier/{lcsc}.provenance.json').write_bytes(provenance(lcsc, 'design/' + rel, data))
    if not args.supplier_only:
        (args.directory / 'cpl.kicad_sch').write_text(schematic())
        make_board(args.directory / 'cpl.kicad_pcb')

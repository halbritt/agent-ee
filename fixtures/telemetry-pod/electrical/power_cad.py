"""Original pod CAD geometry from captured manufacturer pin/land tables.

No vendor graphics or CAD are embedded. Source hashes and drawing locators
are supplied by the electrical bundle. These objects require native checks
and independent review; they do not establish fabrication suitability.
"""
import json
from pathlib import Path

from agent_ee.symbol_capture import capture_symbols


PINS = {
    'LMR36520ADDAR': [('PGND', 'power_in'), ('VIN', 'power_in'), ('EN', 'input'),
        ('PG', 'open_collector'), ('FB', 'input'), ('VCC', 'power_out'),
        ('BOOT', 'passive'), ('SW', 'power_out'), ('EP', 'passive')],
    'TPS7B4253QDDARQ1': [('OUT', 'power_out'), ('NC', 'no_connect'),
        ('GND', 'power_in'), ('FB', 'input'), ('ADJ', 'input'),
        ('GND', 'power_in'), ('NC', 'no_connect'), ('IN', 'power_in'), ('EP', 'passive')],
    'ADA4096-4ARUZ-R7': [('OUTA', 'output'), ('-INA', 'input'), ('+INA', 'input'),
        ('V+', 'power_in'), ('+INB', 'input'), ('-INB', 'input'), ('OUTB', 'output'),
        ('OUTC', 'output'), ('-INC', 'input'), ('+INC', 'input'), ('V-', 'power_in'),
        ('+IND', 'input'), ('-IND', 'input'), ('OUTD', 'output')],
    'OPA320AIDBVR': [('OUT', 'output'), ('V-', 'power_in'), ('+IN', 'input'),
        ('-IN', 'input'), ('V+', 'power_in')],
    # Same physical device, explicit role as the sole ADC reference supply.
    # This classification does not establish load stability or reference accuracy.
    'OPA320AIDBVR_VREF': [('OUT', 'power_out'), ('V-', 'power_in'), ('+IN', 'input'),
        ('-IN', 'input'), ('V+', 'power_in')],
    'TPS2553QDBVRQ1': [('IN', 'power_in'), ('GND', 'power_in'), ('EN', 'input'),
        ('FAULT', 'open_collector'), ('ILIM', 'passive'), ('OUT', 'power_out')],
}


def symbol_library():
    symbols = []
    for name, pins in PINS.items():
        half = (len(pins) + 1) // 2
        height = (half + 1) * 2.54 / 2
        fields = []
        for key, value, y in [('Reference', 'U', height + 2.54), ('Value', name, height + 5.08)]:
            fields.append(f'(property {json.dumps(key)} {json.dumps(value)} (at 0 {y:g} 0) (effects (font (size 1.27 1.27))))')
        body = [f'(rectangle (start -7.62 {height:g}) (end 7.62 {-height:g}) (stroke (width 0.254) (type default)) (fill (type background)))']
        for index, (pin_name, kind) in enumerate(pins):
            left = index < half
            row = index if left else index - half
            x, angle = (-10.16, 0) if left else (10.16, 180)
            y = height - 2.54 * (row + 1)
            body.append(f'(pin {kind} line (at {x:g} {y:g} {angle}) (length 2.54) '
                        f'(name {json.dumps(pin_name)} (effects (font (size 1.016 1.016)))) '
                        f'(number "{index + 1}" (effects (font (size 1.016 1.016)))))')
        symbols.append(f'(symbol {json.dumps(name)} (pin_names (offset 0.254)) (in_bom yes) (on_board yes) '
                       + ' '.join(fields) + f' (symbol "{name}_1_1" ' + ' '.join(body) + '))')
    return ('(kicad_symbol_lib (version 20231120) (generator "agent_ee_pod_cad")\n'
            + '\n'.join(symbols) + '\n)\n').encode()


def pad(number, x, y, width, height, layers='"F.Cu" "F.Paste" "F.Mask"'):
    return f'(pad "{number}" smd rect (at {x:g} {y:g}) (size {width:g} {height:g}) (layers {layers}))'


def mask_opening(x, y, width, height):
    return (f'(fp_rect (start {x-width/2:g} {y-height/2:g}) '
            f'(end {x+width/2:g} {y+height/2:g}) '
            '(stroke (width 0) (type default)) (fill solid) (layer "F.Mask"))')


def pin_one_mark(x, y):
    return (f'(fp_circle (center {x:g} {y:g}) (end {x+0.2:g} {y:g}) '
            '(stroke (width 0.1) (type default)) (fill solid) (layer "F.Fab"))')


def footprint(name, body, pads, courtyard):
    bw, bh = body
    cw, ch = courtyard
    return (f'(footprint "{name}" (version 20240108) (generator "agent_ee_pod_cad") (layer "F.Cu") (attr smd)\n'
            f'(property "Reference" "REF**" (at 0 {-ch / 2 - 1} 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))\n'
            f'(property "Value" "{name}" (at 0 {ch / 2 + 1} 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))\n'
            f'(fp_rect (start {-bw/2} {-bh/2}) (end {bw/2} {bh/2}) (stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))\n'
            f'(fp_rect (start {-cw/2} {-ch/2}) (end {cw/2} {ch/2}) (stroke (width 0.05) (type default)) (fill none) (layer "F.CrtYd"))\n'
            + '\n'.join(pads) + '\n)\n').encode()


def power_footprints():
    result = {}
    for name, mask in [('DDA0008B', (2.71, 3.4)), ('DDA0008J_C02', (2.6, 3.2))]:
        pads = [pad(n, -2.7 if n <= 4 else 2.7,
                    (n - 2.5) * 1.27 if n <= 4 else (6.5 - n) * 1.27, 1.55, 0.6)
                for n in range(1, 9)]
        pads += [pad(9, 0, 0, 2.95, 4.9, '"F.Cu"'),
                 mask_opening(0, 0, *mask)]
        # Eight native copper-connected thermal holes, same physical pad9.
        # Fabricator must qualify filling/tenting and paste loss.
        for x in (-0.65, 0.65):
            for y in (-1.95, -0.65, 0.65, 1.95):
                pads.append(f'(pad "9" thru_hole circle (at {x} {y}) (size 0.4 0.4) (drill 0.2) (layers "*.Cu"))')
        # Original four-window stencil candidate: 65% of mask opening area.
        # This is an explicit process choice, not claimed as vendor approval.
        for x in (-mask[0]/4, mask[0]/4):
            for y in (-mask[1]/4, mask[1]/4):
                pads.append(pad('', x, y, mask[0] * 0.4031, mask[1] * 0.4031, '"F.Paste"'))
        pads.append(pin_one_mark(-1.5, -2.05))
        result[name] = footprint(name, (3.9, 4.9), pads, (7.45, 5.4))
    # Nexperia Figure11: copper1.3 overall/.3 gap/.7 high; separate mask and paste.
    pads = []
    for n, side in [(1, -1), (2, 1)]:
        pads += [pad(n, side * .4, 0, .5, .7, '"F.Cu"'),
                 mask_opening(side * .4, 0, .6, .8),
                 pad('', side * .35, 0, .3, .6, '"F.Paste"')]
    result['SOD882BD'] = footprint('SOD882BD', (1, .6), pads, (1.8, 1.2))
    # Diodes DMP6023LE page6: lead pitch2.3, row centers6.4, tab3.3x1.6.
    pads = [pad(n, (n - 2) * 2.3, 3.2, 1.2, 1.6) for n in (1, 2, 3)]
    pads.append(pad(2, 0, -3.2, 3.3, 1.6))
    pads.append(pin_one_mark(-2.3, 1.2))
    result['DMP6023LE_SOT223'] = footprint('DMP6023LE_SOT223', (6.5, 3.5), pads, (7.1, 8.5))
    for name, body, size, center, courtyard in [
        ('SRP7050TA', (7.3, 6.6), (2.95, 3.5), 2.725, (8.9, 7.4)),
        ('SRP7050WA', (7.3, 7.3), (3.0, 3.5), 3.0, (9.5, 8.1)),
    ]:
        result[name] = footprint(name, body,
            [pad(1, -center, 0, *size), pad(2, center, 0, *size)], courtyard)
    return result


def write_cad(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    raw = symbol_library()
    captured, receipt = capture_symbols(raw, sorted(PINS), library='PodPower', source_file='PodPower.kicad_sym')
    (output / 'PodPower.kicad_sym').write_bytes(captured)
    (output / 'symbol-capture.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    library = output / 'PodPower.pretty'
    library.mkdir()
    for name, raw in power_footprints().items():
        (library / (name + '.kicad_mod')).write_bytes(raw)

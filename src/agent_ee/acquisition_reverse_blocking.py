"""Finite ADP7104 development recipe; no production or engineering acceptance.

This derives new intent and CAD inputs, never edits a native candidate. The
existing unchanged-reference preparer is deliberately not an entry point for it.
Supplier footprint generation is separate from the complete land-pattern gate.
Contained vias have a strict geometry path; assembly qualification is still separate.
"""
import copy
from fractions import Fraction
from hashlib import sha256
from pathlib import Path

from . import easyeda_footprint
from .catalog import strict_json
from .storage import json_bytes

REFERENCE = 'acquisition-control-reverse-blocking-r1'
DATA = Path(__file__).resolve().parents[2] / 'fixtures/acquisition-reverse-blocking'
LIBRARY = 'AgentEE_Power'
SYMBOL = 'ADP7104ARDZ-3.3'
FOOTPRINT = 'ADP7104ARDZ_SOIC8_EP'
DIODE_FOOTPRINT = 'D_B0520LW_C155367'
BASE_SHA256 = '15b8339e27832824914412e274e2efa4c46150502864418c8e0e604510f21da5'
SUPPLIER_SHA256 = 'a5bb40921bc42f87fc1545b620f6c2d2572d60b1b7deb21898e99e2d23431ca6'
DIODE_SUPPLIER_SHA256 = 'f7cde338c6cdafdb6406d0aaf6b219edb7eea222bfd86ee59a142cdf83930e9d'
# Native footprint coordinates, mm. Keep paste off the four open thermal vias.
# See docs/acquisition-reverse-blocking.md for source guidance and process limits.
EP_PASTE_WINDOWS = (
    ('-1.18', '-1.45', '-0.82', '1.45'),
    ('-0.18', '-1.45', '0.18', '1.45'),
    ('0.82', '-1.45', '1.18', '1.45'),
    *((left, bottom, right, top)
      for left, right in (('-0.71', '-0.29'), ('0.29', '0.71'))
      for bottom, top in (('-1.45', '-0.8'), ('-0.21', '0.21'), ('0.8', '1.45'))),
)
# ADI ADP7104 Rev. L, Table 5 p6; EPAD is represented by native pad/pin 9.
PINS = (
    ('1', 'VOUT', 'power_out', '10.16', '5.08', '180'),
    ('2', 'SENSE', 'input', '10.16', '0', '180'),
    ('3', 'GND', 'power_in', '-2.54', '-12.7', '90'),
    ('4', 'NC', 'no_connect', '-10.16', '-5.08', '0'),
    ('5', 'EN', 'input', '-10.16', '0', '0'),
    ('6', 'GND', 'power_in', '2.54', '-12.7', '90'),
    ('7', 'PG', 'open_collector', '10.16', '-5.08', '180'),
    ('8', 'VIN', 'power_in', '-10.16', '5.08', '0'),
    ('9', 'EP', 'power_in', '0', '-12.7', '90'),
)
SELECTIONS = {
    'U1': dict(mpn='ADP7104ARDZ-3.3-R7', lcsc='C2903961', manufacturer='Analog Devices',
               value='ADP7104ARDZ-3.3-R7', lib_id=LIBRARY + ':' + SYMBOL,
               footprint=LIBRARY + ':' + FOOTPRINT),
    'D1': dict(mpn='B0520LW-7-F', lcsc='C155367', manufacturer='Diodes Incorporated',
               value='B0520LW-7-F', lib_id='Device:D_Schottky', footprint=LIBRARY + ':' + DIODE_FOOTPRINT),
    'R5': dict(mpn='RC0603FR-071KL', lcsc='C22548', manufacturer='YAGEO',
               value='1k', lib_id='Device:R', footprint='Resistor_SMD:R_0603_1608Metric'),
    'R6': dict(mpn='RC0603FR-072K2L', lcsc='C114662', manufacturer='YAGEO',
               value='2.2k', lib_id='Device:R', footprint='Resistor_SMD:R_0603_1608Metric'),
}


def symbol_bytes():
    """Generate the exact fixed-output symbol from the attributed pin table."""
    rows = ['(kicad_symbol_lib (version 20251024) (generator "agent-ee")',
        f'(symbol "{SYMBOL}" (in_bom yes) (on_board yes)',
        '(property "Reference" "U" (at 0 11.43 0) (effects (font (size 1.27 1.27))))',
        f'(property "Value" "{SYMBOL}" (at 0 13.97 0) (effects (font (size 1.27 1.27))))',
        f'(property "Footprint" "{LIBRARY}:{FOOTPRINT}" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))',
        f'(symbol "{SYMBOL}_0_1" (rectangle (start -7.62 10.16) (end 7.62 -10.16)',
        '(stroke (width 0.254) (type default)) (fill (type background))))',
        f'(symbol "{SYMBOL}_1_1"']
    for number, name, kind, x, y, angle in PINS:
        rows.append(f'(pin {kind} line (at {x} {y} {angle}) (length 2.54) '
            f'(name "{name}" (effects (font (size 1.016 1.016)))) '
            f'(number "{number}" (effects (font (size 1.016 1.016)))))')
    return ('\n'.join(rows) + '\n)))\n').encode()


def circuit_bytes(original):
    """Derive from the exact captured original circuit, preserving other nets."""
    if sha256(original).hexdigest() != BASE_SHA256:
        raise ValueError('reverse-blocking recipe requires the exact original acquisition circuit')
    circuit = strict_json(original)
    for component in circuit['components']:
        selected = SELECTIONS.get(component['reference'])
        if selected is not None:
            for key in ('value', 'lib_id', 'footprint'):
                component[key] = selected[key]
    connections = {'GND': ('3', '6', '9'), 'VIN_PROTECTED': ('5', '8'), 'V3V3': ('1', '2')}
    for net in circuit['nets']:
        net['terminals'] = [t for t in net['terminals'] if t['component'] != 'U1']
        net['terminals'] += [dict(component='U1', pin=dict(number=n)) for n in connections.get(net['id'], ())]
    circuit['no_connects'] = [t for t in circuit['no_connects'] if t['component'] != 'U1']
    circuit['no_connects'] += [dict(component='U1', pin=dict(number=n)) for n in ('4', '7')]
    circuit['libraries'] = [lib for lib in circuit['libraries'] if lib['name'] != 'Regulator_Linear']
    circuit['libraries'].append(dict(name=LIBRARY, path=LIBRARY + '.kicad_sym',
                                      sha256=sha256(symbol_bytes()).hexdigest()))
    return json_bytes(circuit)


def locality(document):
    """Retain every original relationship/limit, selecting the new local GND pin."""
    result = copy.deepcopy(document)
    changed = set()
    for row in result['obligations']:
        for leg_name, leg in row.items():
            if not isinstance(leg, dict) or 'from' not in leg or 'to' not in leg:
                continue
            for end in ('from', 'to'):
                endpoint = leg[end]
                if endpoint['reference'] != 'U1':
                    continue
                old = endpoint['pad']
                if (old, endpoint['net']) == ('1', '/VIN_PROTECTED'):
                    endpoint['pad'] = '8'
                elif (old, endpoint['net']) == ('5', '/V3V3'):
                    endpoint['pad'] = '1'
                elif (old, endpoint['net']) == ('2', 'GND') and row['id'] in ('C1', 'C2'):
                    endpoint['pad'] = '6' if row['id'] == 'C1' else '3'
                else:
                    raise ValueError('unrecognized original U1 locality endpoint')
                changed.add((row['id'], leg_name, end))
    required = {(ref, leg, 'from') for ref in ('C1', 'C2') for leg in ('supply', 'return')}
    if not required <= changed:
        raise ValueError('missing original U1 capacitor locality relationships')
    return result


def footprint_bytes(supplier):
    """Generate all copper/holes of one pinned supplier package, with native pin 9 vias.

    This is a finite generation declaration, not a new general supplier parser
    or a successful land-pattern assessment. Four vias are electrically contained
    by exposed pad 9. Coordinates rotate the supplier's bottom-left pin 1 to the
    native top-left position. KiCad rounds decimal coordinates to its nm grid.
    """
    if sha256(supplier).hexdigest() != SUPPLIER_SHA256:
        raise ValueError('reverse-blocking package requires the exact C2903961 source')
    record, shapes, origin = easyeda_footprint.identity(strict_json(supplier), 'C2903961')
    if record['manufacturer_part'] != SELECTIONS['U1']['mpn']:
        raise ValueError('supplier regulator identity differs')
    # Primary EasyEDA format: canvas unit10mil; VIA field3 diameter, field5 hole radius.
    # https://docs.easyeda.com/en/DocumentFormat/3-EasyEDA-PCB-File-Format/
    mm = lambda n: easyeda_footprint.text(Fraction(n) * Fraction('0.254'))
    rotate = lambda x, y: (mm(-(Fraction(y) - origin[1])), mm(Fraction(x) - origin[0]))
    rows = [f'(footprint "{FOOTPRINT}" (version 20241229) (generator "agent-ee")',
        '(layer "F.Cu") (attr smd)',
        '(fp_text reference "REF**" (at 0 -3.2) (layer "F.SilkS") (effects (font (size 0.8 0.8) (thickness 0.12))))',
        f'(fp_text value "{SYMBOL}" (at 0 3.2) (layer "F.Fab") (effects (font (size 0.8 0.8) (thickness 0.12))))',
        '(fp_rect (start -1.95 -2.45) (end 1.95 2.45) (stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
        '(fp_rect (start -3.95 -2.75) (end 3.95 2.75) (stroke (width 0.05) (type default)) (fill none) (layer "F.CrtYd"))',
        '(fp_circle (center -3.0 -2.55) (end -2.85 -2.55) (stroke (width 0.12) (type default)) (fill none) (layer "F.SilkS"))']
    pads, vias = [], []
    for raw in shapes:
        fields = raw.split('~')
        if fields[0] == 'PAD':
            pad, hole, _ = easyeda_footprint._pad(fields, Fraction(200) / easyeda_footprint.UNIT_NM)
            if hole is not None or pad['layers'] != ['F.Cu'] or pad['primitive']['angle'] != 0:
                raise ValueError('selected supplier pad geometry changed')
            pads.append(pad)
            p = pad['primitive']
            x, y = rotate(*p['center'])
            w, h = mm(p['size'][1]), mm(p['size'][0])
            layers = '"F.Cu" "F.Mask"' if pad['number'] == '9' else '"F.Cu" "F.Paste" "F.Mask"'
            rows.append(f'(pad "{pad["number"]}" smd {p["shape"]} (at {x} {y}) (size {w} {h}) '
                        f'(layers {layers}))')
        elif fields[0] == 'VIA':
            if len(fields) != 8 or fields[4] or fields[7] not in ('', '0', '1'):
                raise ValueError('selected supplier via declaration changed')
            vias.append(fields)
        else:
            layer = easyeda_footprint._layer_of(fields[0], fields)
            if layer not in easyeda_footprint.NONCOPPER or (fields[0] == 'SVGNODE' and layer != '19'):
                raise ValueError('unmodeled conductive supplier feature')
    if sorted(p['number'] for p in pads) != list('123456789') or len(vias) != 4:
        raise ValueError('selected supplier pad/via inventory changed')
    ep = next(p['primitive'] for p in pads if p['number'] == '9')
    for fields in vias:
        cx, cy, diameter, radius = (Fraction(fields[i]) for i in (1, 2, 3, 5))
        if not 0 < 2 * radius < diameter or any(abs(c - center) + diameter / 2 > size / 2
                for c, center, size in zip((cx, cy), ep['center'], ep['size'])):
            raise ValueError('supplier thermal via is not contained by EP9')
        x, y = rotate(cx, cy)
        rows.append(f'(pad "9" thru_hole circle (at {x} {y}) (size {mm(diameter)} {mm(diameter)}) '
                    f'(drill {mm(2 * radius)}) (layers "*.Cu" "*.Mask") (property pad_prop_heatsink))')
    for left, bottom, right, top in EP_PASTE_WINDOWS:
        rows.append(f'(fp_rect (start {left} {bottom}) (end {right} {top}) '
                    '(stroke (width 0) (type default)) (fill yes) (layer "F.Paste"))')
    return ('\n'.join(rows) + '\n)\n').encode()


def diode_footprint_bytes(supplier):
    """Preserve the complete pinned C155367 copper in its original orientation.

    The supplier symbol's cathode and footprint marking are at pin 1, left of
    the origin, matching Device:D_Schottky. The maximum body envelope comes
    from Diodes DS30051 Rev.18-2 p4. This is not CPL/assembly qualification.
    """
    if sha256(supplier).hexdigest() != DIODE_SUPPLIER_SHA256:
        raise ValueError('reverse-blocking diode requires the exact C155367 source')
    parsed = easyeda_footprint.parse_package(supplier, 'C155367', 200)
    pattern = parsed['pattern']
    if (parsed['identity']['manufacturer_part'] != SELECTIONS['D1']['mpn'] or pattern['holes']
            or sorted(p['number'] for p in pattern['pads']) != ['1', '2']):
        raise ValueError('selected supplier diode identity or inventory changed')
    mm = lambda n: easyeda_footprint.text(Fraction(n) / 1000000)
    rows = [f'(footprint "{DIODE_FOOTPRINT}" (version 20241229) (generator "agent-ee")',
        '(layer "F.Cu") (attr smd)',
        '(fp_text reference "REF**" (at 0 -1.6) (layer "F.SilkS") (effects (font (size 0.8 0.8) (thickness 0.12))))',
        '(fp_text value "B0520LW-7-F" (at 0 1.6) (layer "F.Fab") (effects (font (size 0.8 0.8) (thickness 0.12))))',
        '(fp_rect (start -1.425 -0.85) (end 1.425 0.85) (stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
        '(fp_rect (start -2.5 -1.1) (end 2.5 1.1) (stroke (width 0.05) (type default)) (fill none) (layer "F.CrtYd"))',
        '(fp_line (start -0.7 -0.8) (end -0.7 0.8) (stroke (width 0.12) (type default)) (layer "F.SilkS"))']
    for pad in pattern['pads']:
        p = pad['primitive']
        if pad['layers'] != ['F.Cu'] or p['shape'] != 'rect' or Fraction(p['angle']) != 0:
            raise ValueError('selected supplier diode pad geometry changed')
        x, y = map(mm, p['center'])
        w, h = map(mm, p['size'])
        rows.append(f'(pad "{pad["number"]}" smd rect (at {x} {y}) (size {w} {h}) '
                    '(layers "F.Cu" "F.Paste" "F.Mask"))')
    return ('\n'.join(rows) + '\n)\n').encode()


def assembly_design():
    """Declare the selected U1 stencil design; this is not process acceptance."""
    return dict(schema='agent-ee.acquisition-power-assembly/1', reference=REFERENCE,
        component='U1', footprint=LIBRARY + ':' + FOOTPRINT,
        via_finish='open_unfilled', stencil_thickness_mm='0.15',
        paste_rectangles_mm=[list(row) for row in EP_PASTE_WINDOWS],
        minimum_drawn_drill_clearance_mm='0.1', minimum_drawn_aperture_gap_mm='0.1',
        ep_paste_coverage_range=['0.5', '0.7'], minimum_aperture_area_ratio='0.66',
        cam_requirement='Preserve the supplied EP apertures; default CAM optimization is not covered.',
        limits=['U1 design only; final board stencil must also cover all other populated parts.',
                'Open vias are not tented, filled or capped; solder joint/void fraction is unmeasured.',
                'No assembler process acceptance, placed order or physical validation.'])


def inputs(original, regulator_supplier, diode_supplier):
    """Return deterministic new source files and explicit remaining release gates."""
    files = {'circuit.json': circuit_bytes(original), LIBRARY + '.kicad_sym': symbol_bytes(),
             LIBRARY + '.pretty/' + FOOTPRINT + '.kicad_mod': footprint_bytes(regulator_supplier),
             LIBRARY + '.pretty/' + DIODE_FOOTPRINT + '.kicad_mod': diode_footprint_bytes(diode_supplier),
             'power-assembly.json': json_bytes(assembly_design())}
    report = dict(schema='agent-ee.acquisition-reverse-blocking/1', reference=REFERENCE,
        original_circuit_sha256=BASE_SHA256, supplier_sha256=SUPPLIER_SHA256,
        diode_supplier_sha256=DIODE_SUPPLIER_SHA256,
        files={name: sha256(raw).hexdigest() for name, raw in files.items()}, selections=SELECTIONS,
        accepted=False, file_release='not_assessed',
        pending=['Complete source-bound production preparation and model integration',
                 'Manufacturer raw-document custody and source-role qualification',
                 'Final-board stencil and assembler process qualification against power-assembly.json',
                 'Generated final placement, return paths, rail drops, thermal and capacitor applicability',
                 'Fresh native checks, independent judgments/full review and manufacturing package'])
    return files, report

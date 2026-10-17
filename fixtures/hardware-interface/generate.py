"""Original synthetic fixture. No donor design or third-party symbol graphics."""
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parent


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'agent-ee-original-interface/' + name))


def property_(key, value):
    return f'(property "{key}" "{value}" (at 0 0 0) (effects (font (size 1 1)) (hide yes)))'


def schematic(variant='control', resistor='10k'):
    root_uuid = uid('root')
    definitions = []
    for name, count in [('R', 2), ('C', 2), ('Port', 3), ('Probe', 1)]:
        pins = ''.join(f'(pin passive line (at 0 {-2.54*i} 0) (length 2.54) '
            f'(name "{i+1}" (effects (font (size 1 1)))) (number "{i+1}" (effects (font (size 1 1)))))'
            for i in range(count))
        definitions.append(f'(symbol "Owned:{name}" (pin_names (offset 0)) (in_bom yes) (on_board yes) '
            + property_('Reference', name) + property_('Value', name) + f'(symbol "{name}_1_1" {pins}))')
    # Device:R/C identity is needed only by the existing passive simulator.
    definitions = [d.replace('Owned:R', 'Device:R').replace('Owned:C', 'Device:C') for d in definitions]
    specs = [('X1', 'Owned:Port', 'port', ['IN', 'OUT', 'GND']),
             ('R1', 'Device:R', resistor, ['IN', 'OUT']),
             ('C1', 'Device:C', '100n', ['OUT', 'GND']),
             ('LOAD', 'Device:R', '1Meg', ['OUT', 'GND']),
             ('PROBE', 'Owned:Probe', 'probe', ['OUT']),
             ('R2', 'Device:R', '0', ['AUX', None]),
             ('R3', 'Device:R', '1k', [None, 'GND'])]
    symbols = []
    for index, (ref, lib, value, labels) in enumerate(specs):
        if variant == 'delete-load' and ref == 'LOAD' or variant == 'delete-probe' and ref == 'PROBE':
            continue
        if variant == 'swapped' and ref == 'X1':
            labels = ['OUT', 'IN', 'GND']
        x, y = 40 + index*20, 50
        # R2.2 -- R3.1 is an anonymous direct conductor, independent of generated net names.
        if ref == 'R3':
            y = 52.54
        dnp = 'yes' if variant == 'dnp-load' and ref == 'LOAD' else 'no'
        pins = ''.join(f'(pin "{i+1}" (uuid "{uid(ref+str(i))}"))' for i in range(len(labels)))
        symbols.append(f'(symbol (lib_id "{lib}") (at {x} {y} 0) (unit 1) '
            f'(in_bom yes) (on_board yes) (dnp {dnp}) (uuid "{uid(ref)}")'
            + property_('Reference', ref) + property_('Value', value) + pins
            + f'(instances (project "interface" (path "/{root_uuid}" (reference "{ref}") (unit 1)))))')
        for i, label in enumerate(labels):
            if label is None or variant == 'disconnect' and ref == 'PROBE':
                continue
            if variant == 'renamed' and label == 'OUT':
                label = 'RENAMED'
            symbols.append(f'(label "{label}" (at {x} {y+2.54*i} 0) '
                f'(effects (font (size 1 1)) (justify left bottom)) (uuid "{uid(ref+str(i)+label)}"))')
    symbols.append(f'(wire (pts (xy 140 52.54) (xy 160 52.54)) (stroke (width 0) (type default)) (uuid "{uid("wire")}"))')
    return f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{root_uuid}") (paper "A4") (lib_symbols {"".join(definitions)}) {"".join(symbols)})\n'


if __name__ == '__main__':
    (ROOT / 'interface.kicad_sch').write_text(schematic())

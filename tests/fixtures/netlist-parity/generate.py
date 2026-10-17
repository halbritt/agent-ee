"""Owned synthetic KiCad design; fixture expectations are authored separately."""
from uuid import NAMESPACE_URL, uuid5


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'agent-ee-owned-netlist-parity/' + name))


def prop(name, value):
    return f'(property "{name}" "{value}" (at 0 0 0) (effects (font (size 1 1)) (hide yes)))'


def schematic(variant='control'):
    definitions = []
    for name, count in [('Port', 3), ('R', 2)]:
        pins = ''.join(f'(pin passive line (at 0 {-2.54*i} 0) (length 2.54) '
            f'(name "P{i+1}" (effects (font (size 1 1)))) '
            f'(number "{i+1}" (effects (font (size 1 1)))))' for i in range(count))
        definitions.append(f'(symbol "Owned:{name}" (pin_names (offset 0)) (in_bom yes) (on_board yes) '
            + prop('Reference', name) + prop('Value', name) + f'(symbol "{name}_1_1" {pins}))')
    parts = [('X9', 'Port', 'port', ['VBUS', 'VCC', 'GND']),
             ('R80', 'R', '10k', ['VBUS', 'VCC']),
             ('R90', 'R', '10k', ['VCC', 'GND'])]
    if variant == 'extra':
        parts.append(('R100', 'R', '10k', ['VBUS', 'GND']))
    symbols = []
    for index, (identity, symbol, value, labels) in enumerate(parts):
        if variant == 'missing' and identity == 'R90':
            continue
        ref = identity
        if variant == 'renamed':
            ref = ['A1', 'A2', 'A3'][index]
        if variant == 'exchange-refs':
            ref = {'R80': 'R90', 'R90': 'R80'}.get(ref, ref)
        if variant == 'ic-swap' and identity == 'X9':
            labels = ['VCC', 'VBUS', 'GND']
        if variant in ('no-connects', 'unconnected'):
            if identity == 'X9':
                labels = ['VBUS', 'VCC', None]
            if identity == 'R90':
                labels = ['VCC', None]
        if variant == 'symmetric' and identity == 'R90':
            labels = ['VBUS', 'VCC']
        if variant == 'identical-pin-swap':
            if identity == 'R80':
                labels = ['VBUS', 'GND']
            if identity == 'R90':
                labels = ['VCC', 'VCC']
        if variant == 'same-part-mutation' and identity == 'R90':
            labels = ['VBUS', 'GND']
        x, y = 40 + index * 30, 50
        pin_instances = ''.join(f'(pin "{i+1}" (uuid "{uid(identity+str(i))}"))'
                                for i in range(len(labels)))
        symbols.append(f'(symbol (lib_id "Owned:{symbol}") (at {x} {y} 0) (unit 1) '
            f'(in_bom yes) (on_board yes) (dnp no) (uuid "{uid(identity)}")'
            + prop('Reference', ref) + prop('Value', value) + pin_instances
            + f'(instances (project "parity" (path "/{uid("root")}" (reference "{ref}") (unit 1)))))')
        for i, label in enumerate(labels):
            if label is None:
                if variant == 'no-connects':
                    symbols.append(f'(no_connect (at {x} {y+2.54*i}) (uuid "{uid(identity+str(i)+"NC")}"))')
                continue
            if variant == 'merge' and label == 'VBUS':
                label = 'VCC'
            if variant == 'renamed':
                label = {'VBUS': 'ALPHA', 'VCC': 'BETA', 'GND': 'GAMMA'}[label]
            dx = 1 if variant == 'off-pin' and identity == 'R80' and i == 0 else 0
            symbols.append(f'(label "{label}" (at {x+dx} {y+2.54*i} 0) '
                f'(effects (font (size 1 1)) (justify left bottom)) (uuid "{uid(identity+str(i)+label)}"))')
    return (f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{uid("root")}") '
        f'(paper "A4") (lib_symbols {"".join(definitions)}) {"".join(symbols)})\n')

"""Original topology-oracle fixtures: an I2C pull-up hub and a gate-driver stage.

Both designs, their symbol geometry, UUIDs, values and rule files are authored
here for agent-ee tests; nothing is copied from any benchmark or upstream
design. Every pin is joined to its net by a local label at the pin end, so the
native KiCad netlist export alone defines connectivity.
"""
import copy
from uuid import NAMESPACE_URL, uuid5

SYMBOLS = {
    'Owned:R': [('1', '~'), ('2', '~')],
    'Owned:C': [('1', '~'), ('2', '~')],
    'Owned:LED': [('1', 'K'), ('2', 'A')],
    'Owned:D': [('1', 'K'), ('2', 'A')],
    'Owned:Conn_4': [('1', 'P1'), ('2', 'P2'), ('3', 'P3'), ('4', 'P4')],
    'Owned:Driver': [('1', 'VCC'), ('2', 'VB'), ('3', 'OUTH'), ('4', 'EN'), ('5', 'GND')],
    'Owned:TP': [('1', '~')],
}


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'agent-ee/topology-rules/' + name))


def prop(name, value):
    return f'(property "{name}" "{value}" (at 0 0 0) (effects (font (size 1 1)) (hide yes)))'


def hub(variant='control'):
    parts = [('J1', 'Owned:Conn_4', 'UP', ['VCC', 'GND', 'SDA', 'SCL']),
             ('J2', 'Owned:Conn_4', 'DOWN', ['VCC', 'GND', 'SDA', 'SCL']),
             ('J3', 'Owned:Conn_4', 'DOWN', ['VCC', 'GND', 'SDA', 'SCL']),
             ('R1', 'Owned:R', '4.7k', ['SDA', 'VCC']),
             ('R2', 'Owned:R', '4k7', ['SCL', 'VCC']),
             ('R3', 'Owned:R', '1k', ['VCC', 'LED_A']),
             ('D1', 'Owned:LED', 'green', ['GND', 'LED_A']),
             ('C1', 'Owned:C', '100nF', ['VCC', 'GND'])]
    out = []
    for ref, symbol, value, labels in parts:
        if variant == 'resistor-swap' and ref == 'R1':
            labels = labels[::-1]
        if variant == 'led-swap' and ref == 'D1':
            labels = labels[::-1]
        if variant == 'missing-value' and ref == 'R1':
            value = ''
        out.append((ref, symbol, value, labels))
    if variant == 'extra-resistor':
        out.append(('R4', 'Owned:R', '10k', ['SDA', 'GND']))
    if variant == 'test-point':
        out.append(('TP1', 'Owned:TP', 'TP', ['SDA']))
    return out


def driver(variant='control'):
    caps = 7 if variant == 'seven-caps' else 8
    parts = [('U1', 'Owned:Driver', 'DRV', ['VCC', 'VB', 'OUTH' if variant != 'direct-gate' else 'GATE', 'NC', 'PGND']),
             ('J1', 'Owned:Conn_4', 'OUT', ['GATE', 'CANH', 'CANL', 'PGND'])]
    if variant != 'direct-gate':
        parts.append(('RG1', 'Owned:R', '10R', ['OUTH', 'GATE']))
    parts += [(f'C{i}', 'Owned:C', '10uF', ['VBUS+', 'PGND']) for i in range(1, caps + 1)]
    parts += [('C20', 'Owned:C', '100nF', ['VB', 'PGND']),
              ('C21', 'Owned:C', '1uF', ['VCC', 'PGND']),
              ('RT1', 'Owned:R', '120R', ['CANH', 'CANL']),
              ('R11', 'Owned:R', '10k', ['VBUS+', 'FB1']), ('R12', 'Owned:R', '2.2k', ['FB1', 'PGND']),
              ('R13', 'Owned:R', '10 kΩ' if variant != 'unequal-divider' else '12k', ['VBUS+', 'FB2']),
              ('R14', 'Owned:R', '2k2', ['FB2', 'PGND'])]
    if variant == 'bootstrap-diode':
        parts.append(('D1', 'Owned:D', 'BAT54', ['VB', 'VCC']))
    return parts


def schematic(parts, project='topology'):
    root = uid('root')
    definitions = []
    for symbol, pins in SYMBOLS.items():
        name = symbol.split(':')[1]
        body = ''.join(f'(pin passive line (at 0 {-2.54 * i} 0) (length 2.54) '
                       f'(name "{pname}" (effects (font (size 1 1)))) (number "{num}" (effects (font (size 1 1)))))'
                       for i, (num, pname) in enumerate(pins))
        definitions.append(f'(symbol "{symbol}" (pin_names (offset 0)) (in_bom yes) (on_board yes) '
                           + prop('Reference', 'X') + prop('Value', name) + f'(symbol "{name}_1_1" {body}))')
    placed = []
    for index, (ref, symbol, value, labels) in enumerate(parts):
        x, y = 30 + 20 * (index % 10), 40 + 30 * (index // 10)
        pins = ''.join(f'(pin "{num}" (uuid "{uid(ref + "/pin/" + num)}"))' for num, _ in SYMBOLS[symbol])
        placed.append(f'(symbol (lib_id "{symbol}") (at {x} {y} 0) (unit 1) (in_bom yes) (on_board yes) '
                      f'(dnp no) (uuid "{uid(ref)}")' + prop('Reference', ref) + prop('Value', value) + pins
                      + f'(instances (project "{project}" (path "/{root}" (reference "{ref}") (unit 1)))))')
        for i, label in enumerate(labels):
            at = f'(at {x} {y + 2.54 * i}'
            if label == 'NC':
                placed.append(f'(no_connect {at}) (uuid "{uid(ref + "/nc/" + str(i))}"))')
            else:
                placed.append(f'(label "{label}" {at} 0) (effects (font (size 1 1)) (justify left bottom)) '
                              f'(uuid "{uid(ref + "/label/" + str(i))}"))')
    return (f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{root}") (paper "A3") '
            f'(lib_symbols {"".join(definitions)}) {"".join(placed)})\n')


CLASSES = {'Owned:R': {'class': 'resistor', 'terminals': {'1': '1', '2': '2'}},
           'Owned:C': {'class': 'capacitor', 'terminals': {'1': '1', '2': '2'}},
           'Owned:LED': {'class': 'led', 'terminals': {'K': '1', 'A': '2'}},
           'Owned:D': {'class': 'diode', 'terminals': {'K': '1', 'A': '2'}},
           'Owned:Conn_4': {'class': 'connector', 'terminals': {'1': '1', '2': '2', '3': '3', '4': '4'}},
           'Owned:Driver': {'class': 'ic', 'terminals': {'VCC': '1', 'VB': '2', 'OUTH': '3', 'EN': '4', 'GND': '5'}}}

# Recorded mapping of the control hub, produced by the matcher and reviewed by hand.
HUB_MAPPING = {'roles': {'C_BYP': 'C1', 'D_PWR': 'D1', 'J_DN1': 'J1', 'J_DN2': 'J2', 'J_UP': 'J3',
                         'R_LED': 'R3', 'R_SCL': 'R2', 'R_SDA': 'R1'},
               'swapped': [],
               'nets': {'GND': '/GND', 'LED_A': '/LED_A', 'SCL': '/SCL', 'SDA': '/SDA', 'VCC': '/VCC'}}


def hub_rules():
    bus = {'1': 'VCC', '2': 'GND', '3': 'SDA', '4': 'SCL'}
    components = [{'role': 'J_UP', 'class': 'connector', 'terminals': bus},
                  {'role': 'J_DN1', 'class': 'connector', 'terminals': dict(bus)},
                  {'role': 'J_DN2', 'class': 'connector', 'terminals': dict(bus)},
                  {'role': 'R_SDA', 'class': 'resistor', 'value': '4.7k', 'terminals': {'1': 'SDA', '2': 'VCC'}},
                  {'role': 'R_SCL', 'class': 'resistor', 'value': '4.7k', 'terminals': {'1': 'SCL', '2': 'VCC'}},
                  {'role': 'R_LED', 'class': 'resistor', 'value': '1k', 'terminals': {'1': 'VCC', '2': 'LED_A'}},
                  {'role': 'D_PWR', 'class': 'led', 'terminals': {'A': 'LED_A', 'K': 'GND'}},
                  {'role': 'C_BYP', 'class': 'capacitor', 'terminals': {'1': 'VCC', '2': 'GND'}}]
    return {'schema': 'agent-ee.topology-rules/1', 'classes': copy.deepcopy(CLASSES),
            'rules': [{'id': 'hub-shape', 'requirement': 'HUB-1', 'type': 'expected_components',
                       'match': 'exact', 'components': components},
                      {'id': 'sda-pullup', 'requirement': 'HUB-2', 'type': 'element_between',
                       'class': 'resistor', 'nets': ['SDA', 'VCC'], 'value': '4.7k'}]}


def driver_rules():
    return {'schema': 'agent-ee.topology-rules/1', 'classes': copy.deepcopy(CLASSES), 'rules': [
        {'id': 'bulk-caps', 'requirement': 'DRV-1', 'type': 'count_between', 'class': 'capacitor',
         'nets': ['VBUS+', 'PGND'], 'min': 8},
        {'id': 'no-bootstrap-diode', 'requirement': 'DRV-2', 'type': 'forbidden_between', 'class': 'diode',
         'nets': ['VCC', 'VB']},
        {'id': 'gate-resistor', 'requirement': 'DRV-3', 'type': 'series_element',
         'terminal': {'reference': 'U1', 'pin': '3'}, 'net': 'GATE', 'class': 'resistor'},
        {'id': 'en-unconnected', 'requirement': 'DRV-4', 'type': 'pin_no_connect',
         'terminal': {'reference': 'U1', 'pin': '4'}},
        {'id': 'can-termination', 'requirement': 'DRV-5', 'type': 'element_between', 'class': 'resistor',
         'nets': ['CANH', 'CANL'], 'value': '120'},
        {'id': 'divider-top', 'requirement': 'DRV-6', 'type': 'value_equal', 'references': ['R11', 'R13']},
        {'id': 'divider-bottom', 'requirement': 'DRV-6', 'type': 'value_equal', 'references': ['R12', 'R14']},
        {'id': 'vcc-pin', 'requirement': 'DRV-7', 'type': 'pin_net',
         'terminal': {'reference': 'U1', 'pin': '1'}, 'net': '/VCC'}]}

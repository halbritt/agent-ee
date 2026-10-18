"""Original single-sheet design-rule fixture; symbols, values and part numbers are fictional.

Each pin is joined to its net by a local label placed on the pin end. The
variants differ only in which passives are fitted, their values and one strap.
"""
from uuid import NAMESPACE_URL, uuid5

# Fictional owned symbols: pin names in order, first pin at the symbol origin.
SYMBOLS = {'Owned:R': ['1', '2'], 'Owned:C': ['1', '2'], 'Owned:R_UNDECLARED': ['1', '2'],
           'Owned:XTAL4': ['X1', 'GND', 'X2', 'GND'],
           'Owned:SENSOR': ['VDD', 'GND', 'SDA', 'SCL', 'ADDR'],
           'Owned:MCU': ['VDD', 'GND', 'SDA', 'SCL', 'EN', 'XIN', 'XOUT'],
           'Owned:HEADER3': ['EN', 'VDD', 'GND']}


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'agent-ee/design-rules/' + name))


def prop(name, value):
    return f'(property "{name}" "{value}" (at 0 0 0) (effects (font (size 1 1)) (hide yes)))'


def parts(variant='control'):
    """``(reference, symbol, value, nets by pin order)``; variants remove or change passives."""
    pull = {'strong': '470'}.get(variant, '4.7k')
    design = [('U1', 'Owned:MCU', 'OWN-MCU1', ['+3V3', 'GND', 'SDA', 'SCL', 'EN', 'XIN', 'XOUT']),
              ('U2', 'Owned:SENSOR', 'OWN-TS1', ['+3V3', 'GND', 'SDA', 'SCL', 'GND']),
              ('U3', 'Owned:SENSOR', 'OWN-TS1', ['+3V3', 'GND', 'SDA', 'SCL',
                                                 'GND' if variant == 'same-address' else '+3V3']),
              ('Y1', 'Owned:XTAL4', '20MHz', ['XIN', 'GND', 'XOUT', 'GND']),
              ('J1', 'Owned:HEADER3', 'DEBUG', ['EN', '+3V3', 'GND']),
              ('C1', 'Owned:C', '22p', ['XIN', 'GND']), ('C2', 'Owned:C', '22p', ['XOUT', 'GND']),
              ('C4', 'Owned:C', '100n', ['+3V3', 'GND']),
              ('R5', 'Owned:R', '10k', ['EN', '+3V3']), ('C3', 'Owned:C', '1u', ['EN', 'GND'])]
    if variant != 'no-pullups':
        design += [('R1', 'Owned:R', pull, ['SDA', '+3V3']), ('R2', 'Owned:R', pull, ['SCL', '+3V3'])]
        if variant != 'strong':
            design += [('R3', 'Owned:R', pull, ['SDA', '+3V3']), ('R4', 'Owned:R', pull, ['SCL', '+3V3'])]
    if variant == 'no-load-cap':
        design = [p for p in design if p[0] != 'C2']
    if variant == 'bare-en':
        design = [p for p in design if p[0] not in ('R5', 'C3')]
    if variant == 'undeclared-pullup':
        design = [p for p in design if p[0] not in ('R1', 'R2', 'R3', 'R4')]
        design += [('R6', 'Owned:R_UNDECLARED', '4.7k', ['SDA', '+3V3']),
                   ('R7', 'Owned:R_UNDECLARED', '4.7k', ['SCL', '+3V3'])]
    return design


def schematic(variant='control', project='rules'):
    root = uid('root')
    definitions = []
    for symbol, names in SYMBOLS.items():
        pins = ''.join(f'(pin passive line (at 0 {-2.54*i:.2f} 0) (length 2.54) '
                       f'(name "{name}" (effects (font (size 1 1)))) (number "{i+1}" (effects (font (size 1 1)))))'
                       for i, name in enumerate(names))
        name = symbol.split(':')[1]
        definitions.append(f'(symbol "{symbol}" (pin_names (offset 0)) (in_bom yes) (on_board yes) '
                           + prop('Reference', 'X') + prop('Value', name) + f'(symbol "{name}_1_1" {pins}))')
    placed = []
    for index, (ref, symbol, value, nets) in enumerate(parts(variant)):
        x, y = 30 + 20 * (index % 8), 40 + 30 * (index // 8)
        pins = ''.join(f'(pin "{i+1}" (uuid "{uid(ref + str(i))}"))' for i in range(len(nets)))
        placed.append(f'(symbol (lib_id "{symbol}") (at {x} {y} 0) (unit 1) (in_bom yes) (on_board yes) '
                      f'(dnp no) (uuid "{uid(ref)}")' + prop('Reference', ref) + prop('Value', value) + pins
                      + f'(instances (project "{project}" (path "/{root}" (reference "{ref}") (unit 1)))))')
        for i, net in enumerate(nets):
            placed.append(f'(label "{net}" (at {x} {y + 2.54*i:.2f} 0) '
                          f'(effects (font (size 1 1)) (justify left bottom)) (uuid "{uid(ref + net + str(i))}"))')
    return (f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{root}") (paper "A4") '
            f'(lib_symbols {"".join(definitions)}) {"".join(placed)})\n')


def bindings():
    """Owner role bindings for the fixture; terminals are exported (reference, pin number)."""
    def t(ref, pin):
        return {'reference': ref, 'pin': pin}
    return {'schema': 'agent-ee.design-rule-bindings/1',
            'classes': {'Owned:R': {'class': 'resistor', 'pins': ['1', '2']},
                        'Owned:C': {'class': 'capacitor', 'pins': ['1', '2']}},
            'roles': {
                'i2c_buses': [{'id': 'I2C0', 'sda': t('U1', '3'), 'scl': t('U1', '4'), 'rail': t('U1', '1'),
                               'devices': [
                                   {'reference': 'U2', 'part_number': 'OWN-TS1', 'address': {'strap': {
                                       'pin': '5', 'options': [{'terminal': t('U2', '2'), 'fact': 'ts1-addr-gnd'},
                                                               {'terminal': t('U2', '1'), 'fact': 'ts1-addr-vdd'}]}}},
                                   {'reference': 'U3', 'part_number': 'OWN-TS1', 'address': {'strap': {
                                       'pin': '5', 'options': [{'terminal': t('U3', '2'), 'fact': 'ts1-addr-gnd'},
                                                               {'terminal': t('U3', '1'), 'fact': 'ts1-addr-vdd'}]}}}]}],
                'crystals': [{'reference': 'Y1', 'pins': ['1', '3'], 'ground': t('U1', '2')}],
                'resets': [{'id': 'U1-EN', 'terminal': t('U1', '5'), 'part_number': 'OWN-MCU1',
                            'rail': t('U1', '1'), 'ground': t('U1', '2'),
                            'applicability': {'fact': 'mcu1-en-rc', 'requires': True}}],
                'power_pins': [{'terminal': t('U1', '1'), 'ground': t('U1', '2')},
                               {'terminal': t('U2', '1'), 'ground': t('U2', '2')}]}}


# Fictional one-line datasheet statements for the owned parts, written into an original PDF by the tests.
FACT_LINES = {'ts1-addr-gnd': ('OWN-TS1', 'i2c_address', '0x48', 'ADDR tied to GND selects address 0x48'),
              'ts1-addr-vdd': ('OWN-TS1', 'i2c_address', '0x49', 'ADDR tied to VDD selects address 0x49'),
              'mcu1-en-rc': ('OWN-MCU1', 'reset_external_rc', 'required',
                             'An external RC delay on EN is required')}

"""Owned synthetic native-binding design, libraries and footprints.

Expectations are authored separately in EXPECTATIONS.md. Variants mutate only
the schematic; declarations are built by the tests.
"""
import hashlib
from uuid import NAMESPACE_URL, uuid5


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'agent-ee-owned-native-binding/' + name))


def prop(name, value):
    return f'(property "{name}" "{value}" (at 0 0 0) (effects (font (size 1 1)) (hide yes)))'


def pin(number, y, kind='passive', name=None):
    return (f'(pin {kind} line (at 0 {y} 0) (length 2.54) (name "{name or "P" + number}" '
            f'(effects (font (size 1 1)))) (number "{number}" (effects (font (size 1 1)))))')


def _units(name, units):
    return ''.join(f'(symbol "{name}_{unit}_1" ' + ''.join(pin(*p) for p in pins) + ')'
                   for unit, pins in units)


SYMBOLS = {
    'R': [(1, [('1', 0), ('2', -2.54)])],
    'Tri': [(1, [('1', 0), ('2', -2.54), ('3', -5.08)])],
    'Quad': [(1, [('1', 0), ('2', -2.54), ('3', -5.08), ('4', -7.62)])],
    'Dual': [(0, [('5', -10.16, 'power_in', 'V')]), (1, [('1', 0), ('2', -2.54)]),
             (2, [('3', 0), ('4', -2.54, 'no_connect', '~')])],
    'Conn4': [(1, [('1', 0), ('2', -2.54), ('3', -5.08), ('4', -7.62)])],
    'GND': [(1, [('1', 0, 'power_in', 'GND')])],
}


def _definition(name, units, prefix='', power=False, extends=None):
    body = f'(extends "{extends}") ' if extends else '(pin_names (offset 0)) (in_bom yes) (on_board yes) '
    return (f'(symbol "{prefix}{name}" ' + ('(power) ' if power else '') + body +
            prop('Reference', '#PWR' if power else 'U') + prop('Value', name) +
            ('' if extends else _units(name, units)) + ')')


def library():
    """Captured library: Child is a genuine derived symbol."""
    parts = [_definition(n, u, power=n == 'GND') for n, u in SYMBOLS.items()]
    parts.append(_definition('Child', None, extends='Tri'))
    return ('(kicad_symbol_lib (version 20241209) (generator "owned") ' + ' '.join(parts) + ')\n').encode()


def footprint(name, pads, attr='smd'):
    return (f'(footprint "{name}" (version 20241229) (generator "owned") (layer "F.Cu") '
            f'(descr "Owned {name}") (tags "owned") (attr {attr}) ' + ' '.join(pads) + ')\n').encode()


def smd(number, x):
    return f'(pad "{number}" smd rect (at {x} 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))'


def tht(number, x):
    return f'(pad "{number}" thru_hole circle (at {x} 0) (size 1.7 1.7) (drill 1) (layers "*.Cu" "*.Mask"))'


def footprints():
    sot5 = [smd(n, i) for i, n in enumerate('12345')] + [
        '(pad "5" smd rect (at 0 3) (size 2 2) (layers "F.Cu"))',
        '(pad "" smd rect (at 0 3) (size 1 1) (layers "F.Paste"))',
        '(pad "" np_thru_hole circle (at 0 6) (size 1 1) (drill 1) (layers "*.Cu" "*.Mask"))']
    return {
        'Owned.pretty/R2.kicad_mod': footprint('R2', [smd(n, i) for i, n in enumerate('12')]),
        'Owned.pretty/SOT3.kicad_mod': footprint('SOT3', [smd(n, i) for i, n in enumerate('123')]),
        'Owned.pretty/SOT4.kicad_mod': footprint('SOT4', [smd(n, i) for i, n in enumerate('1234')]),
        'Owned.pretty/SOT5EP.kicad_mod': footprint('SOT5EP', sot5),
        'Owned.pretty/PinHeader_1x04.kicad_mod': footprint(
            'PinHeader_1x04', [tht(n, 2.54 * i) for i, n in enumerate('1234')], 'through_hole'),
        'Owned.pretty/PinHeader_1x05.kicad_mod': footprint(
            'PinHeader_1x05', [tht(n, 2.54 * i) for i, n in enumerate('12345')], 'through_hole'),
    }


def manifest(files):
    return {'schema': 'agent-ee.footprint-libraries/1', 'complete': True, 'libraries': [
        {'name': 'Owned', 'members': [{'path': p, 'sha256': hashlib.sha256(d).hexdigest()}
                                      for p, d in sorted(files.items())]}]}


# reference, symbol, footprint, (in_bom, on_board, dnp), placed unit
CONTROL = [
    ('R1', 'R', 'Owned:R2', ('yes', 'yes', 'no'), 1),
    ('U1', 'Tri', 'Owned:SOT3', ('yes', 'yes', 'no'), 1),
    ('U2', 'Dual', 'Owned:SOT5EP', ('yes', 'yes', 'no'), 1),
    ('U3', 'Child', 'Owned:SOT3', ('yes', 'yes', 'no'), 1),
    ('J1', 'Conn4', 'Owned:PinHeader_1x04', ('yes', 'yes', 'no'), 1),
    ('R2', 'R', 'Owned:R2', ('yes', 'yes', 'yes'), 1),
    ('R3', 'R', '', ('yes', 'no', 'no'), 1),
    ('R4', 'R', 'Owned:R2', ('no', 'yes', 'no'), 1),
    ('#PWR01', 'GND', '', ('yes', 'yes', 'no'), 1),
]


def parts(variant='control'):
    rows = [list(r) for r in CONTROL]
    row = {r[0]: r for r in rows}
    if variant in ('pin4', 'pin4-corrected'):
        row['U1'][1] = 'Quad'
        if variant == 'pin4-corrected':
            row['U1'][2] = 'Owned:SOT4'
    elif variant == 'extra-native':
        rows.append(['R6', 'R', 'Owned:R2', ('yes', 'yes', 'no'), 1])
    elif variant == 'footprint-edit':
        row['U1'][2] = 'Owned:SOT4'
    elif variant == 'shared-bad':
        row['R1'][2] = row['R2'][2] = 'Owned:SOT3'
    elif variant == 'empty-footprint':
        row['R1'][2] = ''
    elif variant == 'absent-footprint':
        row['R1'][2] = 'Owned:Nope'
    elif variant == 'virtual-nonpower':
        rows.append(['#X1', 'R', 'Owned:R2', ('yes', 'yes', 'no'), 1])
    elif variant == 'power-exported':
        rows.append(['PW1', 'GND', '', ('yes', 'yes', 'no'), 1])
    elif variant == 'connector-1x5':
        row['J1'][2] = 'Owned:PinHeader_1x05'
    return rows


def _embedded(variant):
    out = []
    for name, units in SYMBOLS.items():
        if name == 'Tri' and variant == 'embedded-retyped':
            units = [(1, [('1', 0), ('2', -2.54), ('3', -5.08, 'power_in')])]
        if name == 'Tri' and variant == 'embedded-extra-pin':
            units = [(1, [('1', 0), ('2', -2.54), ('3', -5.08), ('4', -7.62)])]
        out.append(_definition(name, units, 'Owned:', power=name == 'GND'))
    # KiCad embeds derived symbols flattened under their own identity.
    out.append(_definition('Child', SYMBOLS['Tri'], 'Owned:'))
    return ''.join(out)


def schematic(variant='control'):
    pin_numbers = {n: sorted({p[0] for _, pins in u for p in pins}) for n, u in SYMBOLS.items()}
    pin_numbers['Child'] = pin_numbers['Tri']
    symbols = []
    for index, (ref, symbol, fp, (in_bom, on_board, dnp), unit) in enumerate(parts(variant)):
        x = 20 + index * 20
        numbers = pin_numbers[symbol]
        if symbol == 'Dual':
            numbers = ['1', '2', '5']
        pins = ''.join(f'(pin "{n}" (uuid "{uid(ref + n)}"))' for n in numbers)
        symbols.append(
            f'(symbol (lib_id "Owned:{symbol}") (at {x} 50 0) (unit {unit}) (in_bom {in_bom}) '
            f'(on_board {on_board}) (dnp {dnp}) (uuid "{uid(ref)}")' + prop('Reference', ref) +
            prop('Value', symbol) + prop('Footprint', fp) + pins +
            f'(instances (project "binding" (path "/{uid("root")}" (reference "{ref}") (unit {unit})))))')
    return (f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{uid("root")}") '
            f'(paper "A4") (lib_symbols {_embedded(variant)}) {"".join(symbols)})\n')


VARIANTS = ('control', 'pin4', 'pin4-corrected', 'extra-native', 'footprint-edit', 'shared-bad',
            'empty-footprint', 'absent-footprint', 'embedded-retyped', 'embedded-extra-pin',
            'virtual-nonpower', 'power-exported', 'connector-1x5')

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


# A sheet reused from another project keeps that project's occurrence paths.
ARCHIVED_ROOT = uid('archived-root')
SHEETS = (('a', uid('sheet-a')), ('b', uid('sheet-b')))
CHILD = 'sheets/child.kicad_sch'


def _instances(entries):
    return '(instances ' + ''.join(
        f'(project "{project}" ' + ''.join(f'(path "{path}" (reference "{ref}") (unit {unit}))'
                                           for path, ref, unit in paths) + ')'
        for project, paths in entries) + ')'


def _root_entries(ref, unit, variant):
    """Instance data of a root-sheet symbol: the current project, plus archived aliases."""
    current = ('binding', [(f'/{uid("root")}', ref, unit)])
    if variant == 'archived-alias':
        alias = '#GHOST' if ref.startswith('#') else 'Z' + ref
        return [current, ('archived', [(f'/{ARCHIVED_ROOT}', alias, unit)])]
    if ref == '#PWR01' and variant.startswith('archived-same-path'):
        alias = 'PW9' if variant == 'archived-same-path-exported' else '#GHOST'
        archived = ('archived', [(f'/{uid("root")}', alias, unit)])
        return [archived, current] if variant == 'archived-same-path-first' else [current, archived]
    return [current]


def _symbol(ref, symbol, fp, flags, unit, x, entries, shown=None):
    in_bom, on_board, dnp = flags
    numbers = {'Dual': ['1', '2', '5']}.get(symbol) or sorted(
        {p[0] for _, pins in SYMBOLS['Tri' if symbol == 'Child' else symbol] for p in pins})
    pins = ''.join(f'(pin "{n}" (uuid "{uid(ref + n)}"))' for n in numbers)
    return (f'(symbol (lib_id "Owned:{symbol}") (at {x} 50 0) (unit {unit}) (in_bom {in_bom}) '
            f'(on_board {on_board}) (dnp {dnp}) (uuid "{uid(ref)}")' + prop('Reference', shown or ref) +
            prop('Value', symbol) + prop('Footprint', fp) + pins + _instances(entries) + ')')


def _sheet_file(uuid, body, variant='control'):
    return (f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{uuid}") '
            f'(paper "A4") (lib_symbols {_embedded(variant)}) {body})\n')


def schematic(variant='control'):
    # reference-property-alias: the Reference property disagrees with the instance entry.
    shown = {'#PWR01': '#GHOST'} if variant == 'reference-property-alias' else {}
    symbols = [_symbol(ref, symbol, fp, flags, unit, 20 + index * 20, _root_entries(ref, unit, variant),
                       shown.get(ref))
               for index, (ref, symbol, fp, flags, unit) in enumerate(parts(variant))]
    return _sheet_file(uid('root'), ''.join(symbols), variant)


def hierarchy(variant='hierarchy'):
    """Root control sheet with CHILD instantiated twice, as sheets a and b.

    The child holds R (R11/R12) and a GND power symbol (#PWR11/#PWR12).
    """
    root = schematic().rstrip()[:-1]
    for index, (name, sheet) in enumerate(SHEETS):
        root += (f'(sheet (at {50 + 60 * index} 100) (size 30 20) (stroke (width 0) (type default)) '
                 f'(fill (color 0 0 0 0)) (uuid "{sheet}")' + prop('Sheetname', name) +
                 prop('Sheetfile', CHILD) +
                 f'(instances (project "binding" (path "/{uid("root")}" (page "{index + 2}")))))')
    paths = [f'/{uid("root")}/{sheet}' for _, sheet in SHEETS]
    power = {'hierarchy-duplicate': ['#PWR11', '#PWR11'], 'hierarchy-missing': ['#PWR11']}.get(
        variant, ['#PWR11', '#PWR12'])
    resistor = [('binding', [(p, r, 1) for p, r in zip(paths, ['R11', 'R12'])])]
    ground = [('binding', [(p, r, 1) for p, r in zip(paths, power)])]
    if variant == 'hierarchy-archived':
        archived = [f'/{ARCHIVED_ROOT}/{sheet}' for _, sheet in SHEETS]
        resistor.append(('archived', [(p, 'R99', 1) for p in archived]))
        ground.append(('archived', [(p, '#GHOST', 1) for p in archived]))
    body = (_symbol('R11', 'R', 'Owned:R2', ('yes', 'yes', 'no'), 1, 20, resistor) +
            _symbol('#PWR11', 'GND', '', ('yes', 'yes', 'no'), 1, 60, ground))
    return {'binding.kicad_sch': root + ')\n', CHILD: _sheet_file(uid('child'), body)}


VARIANTS = ('control', 'pin4', 'pin4-corrected', 'extra-native', 'footprint-edit', 'shared-bad',
            'empty-footprint', 'absent-footprint', 'embedded-retyped', 'embedded-extra-pin',
            'virtual-nonpower', 'power-exported', 'connector-1x5', 'archived-alias',
            'archived-same-path', 'archived-same-path-first', 'archived-same-path-exported',
            'reference-property-alias')
HIERARCHY_VARIANTS = ('hierarchy', 'hierarchy-duplicate', 'hierarchy-missing', 'hierarchy-archived')

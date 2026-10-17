"""Owned subset fixtures composed from the owned native-binding primitives.

No downloaded design, vendor data or real component approval is represented.
"""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('owned_native_primitives',
    Path(__file__).resolve().parents[1] / 'native-binding/generate.py')
BASE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BASE)


def resistor(ref, entries, variant='control'):
    flags = ('no', 'yes', 'yes' if variant == 'dnp' else 'no')
    node = BASE._symbol(ref, 'R', 'Owned:SOT3' if variant == 'footprint' else 'Owned:R2',
                        flags, 1, 20, entries)
    properties = [('LCSC', 'C1'), ('MPN', 'R-C1'), ('Manufacturer', 'Owned fixture'),
                  ('JLCPCB_Assembly', 'not_assembled'), ('DNP', 'true')]
    if variant == 'missing-property':
        properties = [(k, v) for k, v in properties if k != 'MPN']
    return node[:-1] + ''.join(BASE.prop(k, v) for k, v in properties) + ')'


def schematic(variant='control'):
    if variant == 'broken':
        return '(kicad_sch (version 20250114) broken'
    return BASE._sheet_file(BASE.uid('root'), resistor('R1', BASE._root_entries('R1', 1, 'control'), variant))


def hierarchy():
    root = schematic().rstrip()[:-1]
    for index, (name, sheet) in enumerate(BASE.SHEETS):
        root += (f'(sheet (at {50 + 60 * index} 100) (size 30 20) (stroke (width 0) (type default)) '
                 f'(fill (color 0 0 0 0)) (uuid "{sheet}")' + BASE.prop('Sheetname', name) +
                 BASE.prop('Sheetfile', 'child.kicad_sch') +
                 f'(instances (project "binding" (path "/{BASE.uid("root")}" (page "{index + 2}")))))')
    entries = [('binding', [(f'/{BASE.uid("root")}/{sheet}', ref, 1)
                           for (_, sheet), ref in zip(BASE.SHEETS, ('R11', 'R12'))])]
    return {'binding.kicad_sch': root + ')\n',
            'child.kicad_sch': BASE._sheet_file(BASE.uid('child'), resistor('R11', entries))}


def trio_library():
    extra = (BASE._definition('C', BASE.SYMBOLS['R']) +
             BASE._definition('Conn3', [(1, [('1', 0), ('2', -2.54), ('3', -5.08)])]))
    return BASE.library().rstrip()[:-1] + extra.encode() + b')\n'


def trio_footprints():
    return {'Owned.pretty/R2.kicad_mod': BASE.footprints()['Owned.pretty/R2.kicad_mod'],
            'Owned.pretty/PinHeader_1x03.kicad_mod': BASE.footprint('PinHeader_1x03',
                [BASE.tht(n, 2.54 * i) for i, n in enumerate('123')], 'through_hole')}


def trio():
    nodes = resistor('R1', BASE._root_entries('R1', 1, 'control'))
    for ref, symbol, primitive, footprint, lcsc, mpn in (
            ('C1', 'C', 'R', 'Owned:R2', 'C2', 'C-C2'),
            ('J1', 'Conn3', 'Tri', 'Owned:PinHeader_1x03', 'C3', 'J-C3')):
        node = BASE._symbol(ref, primitive, footprint, ('no', 'yes', 'no'), 1, 50,
                            BASE._root_entries(ref, 1, 'control'))
        node = node.replace('Owned:' + primitive + '"', 'Owned:' + symbol + '"')
        nodes += node[:-1] + ''.join(BASE.prop(k, v) for k, v in
            [('LCSC', lcsc), ('MPN', mpn), ('Manufacturer', 'Owned fixture')]) + ')'
    extra = (BASE._definition('C', BASE.SYMBOLS['R'], prefix='Owned:') +
             BASE._definition('Conn3', [(1, [('1', 0), ('2', -2.54), ('3', -5.08)])], prefix='Owned:'))
    return BASE._sheet_file(BASE.uid('root'), nodes).replace('(lib_symbols ', '(lib_symbols ' + extra)

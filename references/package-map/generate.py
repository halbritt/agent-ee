"""Derive the explicit KiCad footprint / LCSC package-name table.

Usage: python3 -I generate.py <kicad footprint root> <output.json>

Footprint IDs come only from file names present in the pinned KiCad library
directory; each is parsed by KiCad library-convention names (imperial/metric
chip codes, body WxL, pitch, pin count) and kept only when every dimension
agrees with the rule below. LCSC package names are an original declared
vocabulary: observed product-page strings plus names whose pin count and body
are explicit or fixed by the cited JEDEC outline. Bare names whose body is not
fixed (for example "SOP-8") are deliberately absent, so they stay unknown.
The check reads only the generated table; it never parses names at run time.
"""
import json
import re
import sys
from pathlib import Path

# Imperial EIA code -> metric code; a footprint must carry the matching pair.
CHIPS = {'0201': '0603', '0402': '1005', '0603': '1608', '0805': '2012', '1206': '3216',
         '1210': '3225', '1812': '4532', '2010': '5025', '2512': '6332'}
CHIP_LIBRARIES = {'Resistor_SMD': ('R', 'resistor'), 'Capacitor_SMD': ('C', 'capacitor'),
                  'Inductor_SMD': ('L', 'inductor'), 'LED_SMD': ('LED', 'led'),
                  'Diode_SMD': ('D', 'diode')}
# SOT-23 outline (JEDEC TO-236 for three leads); the wide SOT-23W body is excluded.
SOT23 = {'SOT-23': 3, 'SOT-23-3': 3, 'SOT-23_Handsoldering': 3, 'SOT-23-5': 5,
         'SOT-23-5_HandSoldering': 5, 'SOT-23-6': 6, 'SOT-23-6_Handsoldering': 6}
# Gull-wing dual outlines with a fixed JEDEC body and pitch: SOIC narrow
# (MS-012, 3.9 mm, 1.27 mm) and TSSOP (MO-153, 4.4 mm, 0.65 mm).
GULL_WING = {('SOIC', 8): '3.9x4.9', ('SOIC', 14): '3.9x8.7', ('SOIC', 16): '3.9x9.9',
             ('TSSOP', 8): '4.4x3', ('TSSOP', 14): '4.4x5', ('TSSOP', 16): '4.4x5',
             ('TSSOP', 20): '4.4x6.5', ('TSSOP', 24): '4.4x7.8', ('TSSOP', 28): '4.4x9.7'}
PITCH = {'SOIC': '1.27', 'TSSOP': '0.65'}
QFN_NAMES = ('QFN', 'VQFN', 'UFQFPN')
OBSERVED = [  # Public product pages read 2026-10-17; facts, not copied tables.
    {'lcsc': 'C1525', 'package': '0402', 'category': 'capacitor', 'source': 'https://www.lcsc.com/product-detail/C1525.html'},
    {'lcsc': 'C14663', 'package': '0603', 'category': 'capacitor', 'source': 'https://www.lcsc.com/product-detail/C14663.html'},
    {'lcsc': 'C9055', 'package': 'SOT-23-5', 'category': 'regulator', 'source': 'https://www.lcsc.com/product-detail/C9055.html'},
    {'lcsc': 'C51118', 'package': 'SOT-25-5', 'category': 'regulator', 'source': 'https://www.lcsc.com/product-detail/C51118.html'},
    {'lcsc': 'C20917', 'package': 'SOT-23', 'category': None, 'source': 'https://www.lcsc.com/product-detail/C20917.html'},
    {'lcsc': 'C7593', 'package': 'SOIC-8', 'category': None, 'source': 'https://www.lcsc.com/product-detail/C7593.html'},
    {'lcsc': 'C1340704', 'package': 'UFQFPN-48(7x7)', 'category': None, 'source': 'https://jlcpcb.com/partdetail/STM32F072C8U7/C1340704'},
]
RULES = {
    'chip-imperial': 'Two-terminal chip by EIA imperial code; footprint library fixes the component class.',
    'named-sot23': 'SOT-23 outline by explicit name and lead count; lead counts are never crossed.',
    'gull-wing': 'SOIC/TSSOP by lead count with the JEDEC body width, length and pitch for that name.',
    'body-size': 'QFN/QFP by terminal family, lead count and explicit body WxL; pitch and exposed pad are not compared.',
}


def number(text):
    return format(float(text), 'g')


def footprints(root):
    table = {}
    names = lambda lib: sorted(p.name[:-len('.kicad_mod')] for p in (root / f'{lib}.pretty').glob('*.kicad_mod'))
    for library, (prefix, category) in CHIP_LIBRARIES.items():
        for name in names(library):
            match = re.fullmatch(prefix + r'_(\d{4})_(\d{4})Metric(?:_Pad\d+(?:\.\d+)?x\d+(?:\.\d+)?mm_HandSolder)?', name)
            if match and CHIPS.get(match[1]) == match[2]:
                table[f'{library}:{name}'] = {'rule': 'chip-imperial', 'classes': [category],
                    'package': {'family': 'chip', 'pins': 2, 'body': match[1]}}
    for name in names('Package_TO_SOT_SMD'):
        if name in SOT23:
            table[f'Package_TO_SOT_SMD:{name}'] = {'rule': 'named-sot23', 'classes': None,
                'package': {'family': 'sot-23', 'pins': SOT23[name], 'body': None}}
    for name in names('Package_SO'):
        match = re.fullmatch(r'(SOIC|TSSOP)-(\d+)_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)mm_P(\d+(?:\.\d+)?)mm', name)
        if match:
            body = f'{number(match[3])}x{number(match[4])}'
            if GULL_WING.get((match[1], int(match[2]))) == body and number(match[5]) == PITCH[match[1]]:
                table[f'Package_SO:{name}'] = {'rule': 'gull-wing', 'classes': None,
                    'package': {'family': 'gull-wing-dual', 'pins': int(match[2]), 'body': body}}
    quads, pitches = {}, {}
    for library, family, pattern in (
            ('Package_DFN_QFN', 'quad-flat-no-lead', r'QFN-(\d+)-1EP_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)mm_P(\d+(?:\.\d+)?)mm_EP[\d.]+x[\d.]+mm(?:_ThermalVias)?'),
            ('Package_QFP', 'quad-flat', r'LQFP-(\d+)_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)mm_P(\d+(?:\.\d+)?)mm')):
        for name in names(library):
            match = re.fullmatch(pattern, name)
            if match:
                package = {'family': family, 'pins': int(match[1]),
                           'body': f'{number(match[2])}x{number(match[3])}'}
                quads[f'{library}:{name}'] = package
                pitches.setdefault(tuple(package.values()), set()).add(number(match[4]))
    # A package name carries no pitch. Where the library has one lead count and
    # body in more than one pitch, neither side is mapped; the outcome is unknown.
    for footprint, package in quads.items():
        if len(pitches[tuple(package.values())]) == 1:
            table[footprint] = {'rule': 'body-size', 'classes': None, 'package': package}
    return table


def packages(footprint_table):
    table = {code: {'rule': 'chip-imperial', 'package': {'family': 'chip', 'pins': 2, 'body': code}}
             for code in CHIPS}
    for name, pins in (('SOT-23', 3), ('SOT-23-3', 3), ('SOT-23-3L', 3), ('TO-236-3(SOT-23-3)', 3),
                       ('SOT-23-5', 5), ('SOT-23-5L', 5), ('SOT-25-5', 5), ('SOT-23-6', 6), ('SOT-23-6L', 6)):
        table[name] = {'rule': 'named-sot23', 'package': {'family': 'sot-23', 'pins': pins, 'body': None}}
    for (family, pins), body in GULL_WING.items():
        table[f'{family}-{pins}'] = {'rule': 'gull-wing',
            'package': {'family': 'gull-wing-dual', 'pins': pins, 'body': body}}
    # Body-size names only for outlines that exist in the footprint table, so
    # a body-mismatched part is reported rather than every conceivable name.
    for entry in footprint_table.values():
        package = entry['package']
        if package['family'] in ('quad-flat-no-lead', 'quad-flat'):
            spellings = QFN_NAMES if package['family'] == 'quad-flat-no-lead' else ('LQFP',)
            for spelling in spellings:
                table[f"{spelling}-{package['pins']}({package['body']})"] = {
                    'rule': 'body-size', 'package': dict(package)}
    return table


def main(root, output):
    root = Path(root)
    footprint_table = footprints(root)
    table = {'schema': 'agent-ee.package-map/1', 'version': 'kicad-10.0.7-lcsc-1',
             'derivation': {'footprint_library': 'kicad-footprints 10.0.7',
                            'generator': 'references/package-map/generate.py',
                            'observations': OBSERVED},
             'rules': RULES, 'footprints': footprint_table, 'packages': packages(footprint_table)}
    Path(output).write_text(json.dumps(table, indent=1, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(*sys.argv[1:])

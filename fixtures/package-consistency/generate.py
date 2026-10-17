"""Original native footprint/LCSC fixture and synthetic catalog rows.

Symbol geometry, UUIDs and catalog rows are authored here; no third-party
design file is copied. LCSC IDs C1525, C14663, C9055 and C25744 carry their
public package strings (read 2026-10-17); every stock, price and attribute is a
test value. C990000048 is a synthetic UFQFPN-48(7x7) row: catalog/1 has no
microcontroller category, so the real UFQFPN-48 MCU row cannot be represented.
"""
import csv
import hashlib
import io
from uuid import NAMESPACE_URL, uuid5

PROJECT = 'packages'


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'agent-ee/package-consistency/' + name))


def prop(name, value):
    return f'(property "{name}" "{value}" (at 0 0 0) (effects (font (size 1 1)) (hide yes)))'


SYMBOLS = {'Owned:C': 2, 'Owned:R': 2, 'Owned:REG5': 5, 'Owned:MCU48': 48, 'Owned:Hole': 1}
FOOTPRINTS = {
    'C1': 'Capacitor_SMD:C_0402_1005Metric', 'C2': 'Capacitor_SMD:C_0603_1608Metric',
    'U1': 'Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.6x5.6mm', 'U2': 'Package_TO_SOT_SMD:SOT-23-5',
    'R1': 'Resistor_SMD:R_0402_1005Metric', 'R2': 'Resistor_SMD:R_0402_1005Metric',
    'JDBG': 'Resistor_SMD:R_0402_1005Metric', 'H1': 'MountingHole:MountingHole_3.2mm_M3',
    'CH': 'Capacitor_SMD:C_0402_1005Metric'}
LCSC = {'C1': 'C1525', 'C2': 'C14663', 'U1': 'C990000048', 'U2': 'C9055', 'R1': 'C25744',
        'R2': 'C25744', 'CH': 'C1525'}
# Each variant changes one declared field; expectations live in the tests.
VARIANTS = {
    'control': {},
    'sot-mismatch': {('U2', 'Footprint'): 'Package_TO_SOT_SMD:SOT-23'},
    'class': {('C2', 'Footprint'): 'Resistor_SMD:R_0603_1608Metric'},
    'unmapped-footprint': {('U2', 'Footprint'): 'Owned_Footprints:REG5_custom'},
    'unmapped-package': {('U2', 'LCSC'): 'C990000005'},
    'absent-part': {('R1', 'LCSC'): 'C999999'},
    'authored-dnp-only': {('R2', 'LCSC'): None},
    'mismatch-and-unknown': {('U2', 'Footprint'): 'Package_TO_SOT_SMD:SOT-23', ('R1', 'LCSC'): 'C999999'},
}


def _definitions():
    out = []
    for symbol, count in SYMBOLS.items():
        name = symbol.split(':')[1]
        pins = ''.join(f'(pin passive line (at 0 {-2.54 * i} 0) (length 2.54) '
                       f'(name "{i + 1}" (effects (font (size 1 1)))) (number "{i + 1}" (effects (font (size 1 1)))))'
                       for i in range(count))
        out.append(f'(symbol "{symbol}" (pin_names (offset 0)) (in_bom yes) (on_board yes) '
                   + prop('Reference', 'X') + prop('Value', name) + f'(symbol "{name}_1_1" {pins}))')
    return ''.join(out)


def _placed(ref, symbol, x, fields, paths, *, in_bom=True, dnp=False, extra=''):
    pins = ''.join(f'(pin "{i + 1}" (uuid "{uid(ref + "-pin" + str(i))}"))' for i in range(SYMBOLS[symbol]))
    properties = ''.join(prop(name, value) for name, value in fields.items() if value is not None)
    instances = ''.join(f'(path "{path}" (reference "{reference}") (unit 1))' for path, reference in paths)
    return (f'(symbol (lib_id "{symbol}") (at {x} 50 0) (unit 1) (in_bom {"yes" if in_bom else "no"}) '
            f'(on_board yes) (dnp {"yes" if dnp else "no"}) (uuid "{uid(ref)}")'
            + prop('Reference', paths[0][1]) + prop('Value', symbol.split(':')[1]) + properties + extra + pins
            + f'(instances (project "{PROJECT}" {instances})))')


def schematic(variant='control', child=False):
    changes = VARIANTS[variant]

    def fields(ref):
        values = {'Footprint': FOOTPRINTS[ref], 'LCSC': LCSC.get(ref)}
        for (target, name), value in changes.items():
            if target == ref:
                values[name] = value
        return values

    root = f'/{uid("root")}'
    if child:
        paths = [(f'{root}/{uid("sheet-ch1")}', 'C101'), (f'{root}/{uid("sheet-ch2")}', 'C201')]
        body = _placed('CH', 'Owned:C', 40, fields('CH'), paths)
        own = uid('child')
    else:
        parts = [('C1', 'Owned:C'), ('C2', 'Owned:C'), ('U1', 'Owned:MCU48'), ('U2', 'Owned:REG5'),
                 ('R1', 'Owned:R'), ('R2', 'Owned:R'), ('JDBG', 'Owned:R'), ('H1', 'Owned:Hole')]
        placed = []
        for index, (ref, symbol) in enumerate(parts):
            # R2 carries an authored DNP field without the native flag; JDBG is
            # natively DNP; H1 is natively excluded from the BOM.
            placed.append(_placed(ref, symbol, 30 + 20 * index, fields(ref), [(root, ref)],
                                  in_bom=ref != 'H1', dnp=ref == 'JDBG',
                                  extra=prop('DNP', 'yes') if ref == 'R2' else ''))
        for index, name in enumerate(('ch1', 'ch2')):
            placed.append(f'(sheet (at {40 + 50 * index} 200) (size 30 20) (stroke (width 0) (type default)) '
                          f'(fill (color 0 0 0 0)) (uuid "{uid("sheet-" + name)}")' + prop('Sheetname', name)
                          + prop('Sheetfile', 'ch.kicad_sch')
                          + f'(instances (project "{PROJECT}" (path "{root}" (page "{index + 2}")))))')
        body = ''.join(placed)
        own = uid('root')
    return (f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{own}") (paper "A2") '
            f'(lib_symbols {_definitions()}) {body})\n')


CATALOG_ROWS = [
    # lcsc, mfr, mpn, package, category, attributes
    ('C1525', 'Samsung Electro-Mechanics', 'CL05B104KO5NNNC', '0402', 'capacitor', '{"capacitance":"100 nF ±10%"}'),
    ('C14663', 'YAGEO', 'CC0603KRX7R9BB104', '0603', 'capacitor', '{"capacitance":"100 nF ±10%"}'),
    ('C9055', 'MaxLinear', 'SPX3819M5-L-3-3/TR', 'SOT-23-5', 'regulator', '{"output_voltage":"3.3 V"}'),
    ('C25744', 'UNI-ROYAL', '0402WGF1002TCE', '0402', 'resistor', '{"resistance":"10 kohm ±1%"}'),
    ('C990000005', 'Owned', 'OWNED-REG-UNMAPPED', 'CASE-OWNED-5', 'regulator', '{}'),
    ('C990000048', 'Owned', 'OWNED-QFN48-TEST', 'UFQFPN-48(7x7)', 'regulator', '{}'),
]


def catalog_source():
    """Return (CSV bytes, catalog-source/1 manifest) for catalog.capture_catalog."""
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(['lcsc', 'mfr', 'mpn', 'package', 'category', 'stock', 'price1',
                     'is_basic', 'is_preferred', 'last_seen', 'attributes'])
    for lcsc, mfr, mpn, package, category, attributes in CATALOG_ROWS:
        writer.writerow([lcsc, mfr, mpn, package, category, '1000', '0.01', '1', '0', '2026-10-17', attributes])
    payload = stream.getvalue().encode()
    return payload, {'schema': 'agent-ee.catalog-source/1', 'snapshot_date': '2026-10-17',
        'source_url': 'https://example.invalid/agent-ee-package-fixture', 'source_commit': 'owned-fixture-v1',
        'currency': 'USD', 'stale_before': None, 'volumes': [{'path': 'owned.csv',
        'sha256': hashlib.sha256(payload).hexdigest(), 'size_bytes': len(payload), 'format': 'csv'}]}

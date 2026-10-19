"""Source-bound LSM6DSO32 primary-SPI model; generic source is retained."""
import hashlib
import json

from agent_ee.schematic_emit import parse_quoted, format_sexpr
from agent_ee.symbol_capture import capture_symbols
from agent_ee.symbol_library import children


def write_primary_spi(source, output):
    raw = source.read_bytes()
    subset, source_capture = capture_symbols(raw, ['LSM6DSO32'], library='Pod', source_file=source.name)
    tree = parse_quoted(subset)
    symbols = list(children(tree, 'symbol'))
    if len(symbols) != 1 or symbols[0][1] != 'LSM6DSO32':
        raise ValueError('expected captured standalone LSM6DSO32 symbol')
    changes = []
    for unit in children(symbols[0], 'symbol'):
        for pin in children(unit, 'pin'):
            number = list(children(pin, 'number'))[0][1]
            if number in ('2', '3'):
                name = list(children(pin, 'name'))[0][1]
                if pin[1] != 'bidirectional' or name != {'2': 'SDx', '3': 'SCx'}[number]:
                    raise ValueError('source mode-pin semantics changed')
                changes.append(dict(pin=number, name=name, source='bidirectional', delivered='input'))
                pin[1] = 'input'
    if {p['pin'] for p in changes} != {'2', '3'} or len(changes) != 2:
        raise ValueError('mode pin inventory differs')
    adapted = ('\n'.join(format_sexpr(tree)) + '\n').encode()
    captured, receipt = capture_symbols(adapted, ['LSM6DSO32'], library='PodPrimarySPI', source_file='PodPrimarySPI.kicad_sym')
    output.write_bytes(captured)
    record = dict(source_sha256=hashlib.sha256(raw).hexdigest(),
        output_sha256=hashlib.sha256(captured).hexdigest(), changes=changes,
        applicability='Primary SPI Mode1 only; sensor-hub master mode must remain disabled.',
        evidence=dict(sha256='d1776336f5060da06c4fa4c3aac293372e20cffa4f9d72fe3296b9a56ed6c0b7',
                      locator='LSM6DSO32 DocID032891 Rev1, Table2 p24 and Table19 p55'),
        source_capture=source_capture, capture=receipt)
    output.with_suffix('.adaptation.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')

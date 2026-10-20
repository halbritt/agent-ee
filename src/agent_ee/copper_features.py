"""Explicit source-bound on-board copper membership, sharing fab qualification."""
from .catalog import exact_fields, nonempty
from .fab_export import (copper_field_allowed, copper_footprint_problems,
                         copper_schematic_problems, declared_copper_features)
from .probe_features import properties
from .storage import identity
from .symbol_library import children, field, parse_library, parse_sexpr, resolve_symbol

KIND = 'copper_feature'


def is_copper(part):
    return isinstance(part, dict) and part.get('kind') == KIND


def validate_part(part):
    from .parts_manifest import _blob
    exact_fields(part, ('kind', 'id', 'role', 'required', 'instances', 'lib_id', 'footprint', 'copper'))
    if part['kind'] != KIND or part['required'] is not True:
        raise ValueError('copper feature must be required; DNP suppression is refused')
    for key in ('id', 'role', 'lib_id', 'footprint'):
        nonempty(part[key])
    for key in ('lib_id', 'footprint'):
        if part[key].count(':') != 1 or not all(part[key].split(':')):
            raise ValueError('copper feature requires exact library:member identity')
    copper = part['copper']
    exact_fields(copper, ('symbol', 'footprint', 'pins', 'native_flags'))
    for key in ('symbol', 'footprint'):
        _blob(copper[key])
    if copper['native_flags'] != dict(in_bom=False, dnp=False, exclude_from_board=False) or any(
            type(v) is not bool for v in copper['native_flags'].values()):
        raise ValueError('copper requires in_bom=false, dnp=false, exclude_from_board=false')
    pins = copper['pins']
    if not isinstance(pins, list) or not 2 <= len(pins) <= 64:
        raise ValueError('copper requires 2..64 declared pins')
    for pin in pins:
        exact_fields(pin, ('number', 'name', 'electrical_type'))
        for value in pin.values():
            nonempty(value)
        if pin['electrical_type'] != 'passive':
            raise ValueError('copper requires passive pins')
    numbers = {p['number'] for p in pins}
    if len(numbers) != len(pins):
        raise ValueError('duplicate copper pin number')
    if not isinstance(part['instances'], list) or not part['instances']:
        raise ValueError('explicit copper instances required')
    for selector in part['instances']:
        exact_fields(selector, ('sheet', 'reference', 'nets'))
        declared_copper_features({'copper_features': [selector['reference']]})
        sheet = nonempty(selector['sheet'])
        if not sheet.startswith('/') or not sheet.endswith('/'):
            raise ValueError('exact native sheet selector required')
        if not isinstance(selector['nets'], dict) or set(selector['nets']) != numbers:
            raise ValueError('exact per-pin native nets required')
        for net in selector['nets'].values():
            nonempty(net)


def _symbol(node, lib_id, pins):
    if children(node, 'extends') or children(node, 'power'):
        raise ValueError('copper symbol inheritance/power unsupported')
    for name, value in properties(node).items():
        if not copper_field_allowed(name, value):
            raise ValueError('copper symbol has unqualified field: ' + name)
    actual, _, power = resolve_symbol({node[1]: node}, node[1], '', '')
    actual = [{k: p[k] for k in ('number', 'name', 'electrical_type')} for p in actual]
    if power or sorted(actual, key=lambda p: p['number']) != sorted(pins, key=lambda p: p['number']):
        raise ValueError('copper symbol pins differ')
    return identity([node[0], lib_id, *node[2:]])


def qualify(part, root):
    from .parts_manifest import _read_blob
    validate_part(part)
    copper = part['copper']
    symbols = parse_library(_read_blob(root, copper['symbol']))
    name = part['lib_id'].split(':')[1]
    if name not in symbols:
        raise ValueError('copper captured symbol missing: ' + part['id'])
    signature = _symbol(symbols[name], part['lib_id'], copper['pins'])
    fp = parse_sexpr(_read_blob(root, copper['footprint']))
    if fp[0] != 'footprint' or fp[1] != part['footprint'].split(':')[1]:
        raise ValueError('copper source footprint identity differs')
    # These are file-format headers, absent from a footprint saved in a board.
    headers = {'version', 'generator', 'generator_version'}
    body = [*fp[:2], *[n for n in fp[2:]
                      if not isinstance(n, list) or not n or n[0] not in headers]]
    # Library coordinates have an implicit zero placement; saved boards carry it explicitly.
    if not children(body, 'at'):
        body.append(['at', '0', '0'])
    problems = copper_footprint_problems(body, part['id'])
    pads = children(fp, 'pad')
    if len(pads) != len(copper['pins']) or {p[1] for p in pads} != {p['number'] for p in copper['pins']}:
        problems.append('copper pad count/numbers differ from declaration')
    if problems:
        raise ValueError('; '.join(problems))
    return dict(part_id=part['id'], lib_id=part['lib_id'], footprint=part['footprint'],
                symbol=copper['symbol'], footprint_source=copper['footprint'],
                pins=copper['pins'], native_flags=copper['native_flags'], symbol_body_sha256=signature)


def declarations(document, root):
    result = {}
    for part in document['parts']:
        if is_copper(part):
            record = qualify(part, root)
            for selector in part['instances']:
                ref = selector['reference']
                if ref in result:
                    raise ValueError('globally unique copper references required')
                result[ref] = {**record, **selector}
    return result


def validate_schematic(data, records):
    if not records:
        return set()
    problems = copper_schematic_problems(data, set(records))
    if problems:
        raise ValueError('; '.join(problems))
    root = parse_sexpr(data)
    embedded = {s[1]: s for lib in children(root, 'lib_symbols') for s in children(lib, 'symbol')}
    found = set()
    for sym in children(root, 'symbol'):
        props = properties(sym)
        ref = props.get('Reference')
        if ref not in records:
            continue
        r = records[ref]
        if (field(sym, 'lib_id', 1) != [r['lib_id']] or props.get('Footprint') != r['footprint'] or
                field(sym, 'in_bom', 1) != ['no'] or field(sym, 'on_board', 1) != ['yes'] or
                field(sym, 'dnp', 1) != ['no']):
            raise ValueError(ref + ': copper schematic identity/flags differ')
        if _symbol(embedded[r['lib_id']], r['lib_id'], r['pins']) != r['symbol_body_sha256']:
            raise ValueError(ref + ': copper embedded symbol changed')
        if ref in found:
            raise ValueError(ref + ': duplicate copper instance')
        found.add(ref)
    return found


def native_component(record, comp, graph):
    ref = record['reference']
    if (comp['lib_id'] != record['lib_id'] or comp['footprint'] != record['footprint'] or
            comp['sheet'] != record['sheet'] or not comp['populated'] or
            not comp['on_board'] or comp['in_bom']):
        raise ValueError(ref + ': copper native identity/flags differ')
    fields = {**comp.get('binding_fields', {}), **comp['properties']}
    for name, value in fields.items():
        if name in ('Sheetname', 'Sheetfile') or (name == 'exclude_from_bom' and value is None):
            continue
        if value is not None and not copper_field_allowed(name, value):
            raise ValueError(ref + ': copper has unqualified native field: ' + name)
    pins = {p['number']: {k: p[k] for k in ('name', 'electrical_type')} for p in record['pins']}
    if set(comp['pins']) != set(pins) or comp['pin_metadata'] != pins:
        raise ValueError(ref + ': copper native pins differ')
    for pin, net in record['nets'].items():
        names = [n['name'] for n in graph['nets'].values() if (ref, pin) in n['terminals']]
        if names != [net]:
            raise ValueError(ref + ': copper native net differs: ' + pin)

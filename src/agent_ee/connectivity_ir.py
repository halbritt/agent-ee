"""Versioned offline connectivity intent bound exclusively to captured bytes.

Run ``python -m agent_ee.connectivity_ir --help`` for the standalone boundary.
This result is not a coordinator witness or a native KiCad qualification.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath

from .symbol_library import ELECTRICAL_TYPES, LibraryError, ParsedLibraries, children, resolve_symbol

INTENT_SCHEMA = 'agent-ee.connectivity-intent/1'
RESOLVED_SCHEMA = 'agent-ee.resolved-connectivity/1'


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def _read_json(value):
    if isinstance(value, (str, bytes)):
        if len(value) > 8 * 1024 * 1024:
            raise ValueError('intent exceeds 8 MiB')
        return json.loads(value, object_pairs_hook=_unique_pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(ValueError('non-finite JSON')))
    # Also reject opaque/non-JSON Python objects at the pure boundary.
    return _read_json(_json_bytes(value))


def _object(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('object requires fields ' + ', '.join(required))


def _text(value, empty=False):
    if not isinstance(value, str) or (not value and not empty) or '\x00' in value:
        raise ValueError('expected nonempty string')


def _list(value):
    if not isinstance(value, list) or len(value) > 10000:
        raise ValueError('expected bounded list')


def _terminal(value):
    _object(value, ('component', 'pin'))
    _text(value['component'])
    pin = value['pin']
    if not isinstance(pin, dict) or set(pin) not in ({'number'}, {'name'}):
        raise ValueError('terminal requires exactly one number/name selector')
    _text(next(iter(pin.values())))


def _validate_flat_intent(value):
    _object(value, ('schema', 'libraries', 'components', 'nets', 'no_connects'), ('hierarchy', 'ports'))
    if value['schema'] != INTENT_SCHEMA:
        raise ValueError('unknown intent schema')
    for key in ('libraries', 'components', 'nets', 'no_connects', 'hierarchy', 'ports'):
        _list(value.get(key, []))
    for lib in value['libraries']:
        _object(lib, ('name', 'path', 'sha256'))
        for entry in lib.values():
            _text(entry)
        path = PurePosixPath(lib['path'])
        if path.is_absolute() or '..' in path.parts or str(path) != lib['path'] or '\\' in lib['path'] or path.suffix != '.kicad_sym':
            raise ValueError('library path must be safe relative .kicad_sym path')
        if len(lib['sha256']) != 64 or any(c not in '0123456789abcdef' for c in lib['sha256']):
            raise ValueError('invalid library sha256')
    for comp in value['components']:
        _object(comp, ('id', 'reference', 'lib_id', 'value', 'footprint', 'units'), ('part_binding', 'in_bom'))
        for key in ('id', 'reference', 'lib_id', 'value', 'footprint'):
            _text(comp[key], empty=key in ('value', 'footprint'))
        if comp['lib_id'].count(':') != 1 or not all(comp['lib_id'].split(':')):
            raise ValueError('lib_id requires library:symbol')
        _list(comp['units'])
        if not comp['units'] or any(type(x) is not int or x < 1 for x in comp['units']) or len(set(comp['units'])) != len(comp['units']):
            raise ValueError('units require unique positive integers')
        if 'in_bom' in comp and type(comp['in_bom']) is not bool:
            raise ValueError('in_bom must be a boolean')
        if 'part_binding' in comp and not isinstance(comp['part_binding'], dict):
            raise ValueError('opaque part_binding must be an object')
    for net in value['nets']:
        _object(net, ('id', 'name', 'terminals'))
        _text(net['id'])
        _text(net['name'], empty=True)
        _list(net['terminals'])
        for terminal in net['terminals']:
            _terminal(terminal)
    for terminal in value['no_connects']:
        _terminal(terminal)


def _normal_intent(value):
    value = copy.deepcopy(value)
    for key, sort_key in (('libraries', 'name'), ('components', 'id'), ('nets', 'id')):
        value[key].sort(key=lambda x: (x[sort_key], _json_bytes(x)))
    for comp in value['components']:
        comp['units'].sort()
    for net in value['nets']:
        net['terminals'].sort(key=_json_bytes)
    value['no_connects'].sort(key=_json_bytes)
    return value


def canonical_resolved(record):
    """Canonical bytes, including identity; does not promote an unusable record."""
    return _json_bytes(record)


def _seal(record):
    record['canonical_sha256'] = hashlib.sha256(_json_bytes(record)).hexdigest()
    return record


def _resolve_flat_intent(intent, captured_libraries, *, _pin_limit=None, _library_cache=None):
    """Resolve explicit path→bytes captures; never reads a file or environment."""
    result = dict(schema=RESOLVED_SCHEMA, execution='ok', outcome='pass', coverage='complete',
                  findings=[], intent_sha256=None, libraries=[], components=[], nets=[],
                  no_connects=[], hierarchy=[], ports=[])
    def finding(code, location, message, **extra):
        result['findings'].append(dict(code=code, location=location, message=message, **extra))
    def incomplete(execution):
        # A failed capture remains failed even when another item is unsupported.
        if result['execution'] != 'failed':
            result.update(execution=execution, outcome='unknown', coverage='missing')
    try:
        raw = _read_json(intent)
        _validate_flat_intent(raw)
        raw = _normal_intent(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        result.update(execution='malformed', outcome='unknown', coverage='missing')
        finding('intent_schema', '/', str(exc))
        return _seal(result)
    if not isinstance(captured_libraries, dict):
        incomplete('failed')
        finding('library_coverage', '/libraries', 'captured_libraries must be an explicit path-to-bytes map')
        return _seal(result)
    result['intent_sha256'] = hashlib.sha256(_json_bytes(raw)).hexdigest()
    result['hierarchy'], result['ports'] = raw.get('hierarchy', []), raw.get('ports', [])
    if result['hierarchy'] or result['ports']:
        incomplete('unsupported')
        finding('hierarchy_unsupported', '/', 'hierarchy and ports retained but not resolved in v1')
    cache = _library_cache if _library_cache is not None else ParsedLibraries()
    libraries = {}
    for lib in raw['libraries']:
        if lib['name'] in libraries or any(x['path'] == lib['path'] for x in result['libraries']):
            finding('duplicate_library', '/libraries', 'duplicate library name or path')
            continue
        result['libraries'].append(lib)
        data = captured_libraries.get(lib['path'])
        try:
            if data is None:
                raise LibraryError('missing captured library: ' + lib['path'])
            if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != lib['sha256']:
                raise LibraryError('captured library hash/type mismatch')
            libraries[lib['name']] = (lib, cache.symbols(data))
        except LibraryError as exc:
            incomplete('failed')
            finding('library_coverage', '/libraries/' + lib['name'], str(exc))
    components, references = {}, set()
    for comp in raw['components']:
        location = '/components/' + comp['id']
        if comp['id'] in components or comp['reference'] in references:
            finding('duplicate_component', location, 'duplicate logical id or display reference')
            continue
        references.add(comp['reference'])
        components[comp['id']] = None
        library, symbol = comp['lib_id'].split(':')
        if library not in libraries:
            if not any(x['name'] == library for x in raw['libraries']):
                finding('unknown_library', location, 'lib_id has no declared captured library')
            continue
        lib, symbols = libraries[library]
        if symbol not in symbols:
            finding('unknown_symbol', location, 'unknown symbol: ' + symbol, available_symbols=sorted(symbols))
            continue
        try:
            pins, sources, power = resolve_symbol(symbols, symbol, lib['path'], lib['sha256'])
            # resolve_symbol has validated these inherited base-unit identifiers.
            base = symbols[sources[0]['symbol']]
            available_units = {int(node[1].rsplit('_', 2)[1])
                               for node in children(base, 'symbol')} - {0}
            # Common-only libraries represent one physical unit, numbered 1.
            if set(comp['units']) - (available_units or {1}):
                raise LibraryError('selected unit does not exist')
            pins = [p for p in pins if p['unit'] == 0 or p['unit'] in comp['units']]
            if not pins:
                finding('empty_pins', location, 'selected symbol has empty pins inventory')
                continue
            if _pin_limit is not None and sum(len(c['pins']) for c in result['components']) + len(pins) > _pin_limit:
                raise LibraryError('physical pin inventory exceeds hierarchy limit')
            numbers = [p['number'] for p in pins]
            if len(set(numbers)) != len(numbers):
                raise LibraryError('duplicate physical pin number across selected/common units unsupported')
            resolved = dict(comp, pins=sorted(pins, key=lambda p: (p['number'], p['unit'])),
                            sources=sources, power=power,
                            field_sources={key: location + '/' + key for key in comp})
            components[comp['id']] = resolved
            result['components'].append(resolved)
        except LibraryError as exc:
            incomplete('unsupported')
            finding('symbol_unsupported', location, str(exc))
    bound = set()
    def expand(terminal, location):
        comp = components.get(terminal['component'])
        if comp is None:
            finding('unknown_component', location, 'component unavailable: ' + terminal['component'])
            return []
        selector, value = next(iter(terminal['pin'].items()))
        selected = [p for p in comp['pins'] if p[selector] == value]
        if not selected:
            finding('unknown_pin', location, comp['reference'] + ': unknown pin ' + value,
                    available_pins=[dict(number=p['number'], name=p['name']) for p in comp['pins']])
        terminals = []
        for pin in selected:
            key = (comp['id'], pin['number'])
            if key in bound:
                finding('duplicate_terminal', location, 'pin already bound: ' + '.'.join(key))
            bound.add(key)
            terminals.append(dict(component=comp['id'], pin=pin['number']))
        return terminals
    net_ids = set()
    for net in raw['nets']:
        location = '/nets/' + net['id']
        if net['id'] in net_ids:
            finding('duplicate_net', location, 'duplicate net id')
        net_ids.add(net['id'])
        terminals = [t for raw_terminal in net['terminals'] for t in expand(raw_terminal, location)]
        if len({(t['component'], t['pin']) for t in terminals}) < 2:
            finding('singleton_net', location, 'net needs at least two distinct physical terminals')
        result['nets'].append(dict(net, terminals=sorted(terminals, key=_json_bytes)))
    for terminal in raw['no_connects']:
        result['no_connects'].extend(expand(terminal, '/no_connects'))
    result['no_connects'].sort(key=_json_bytes)
    for comp in result['components']:
        for pin in comp['pins']:
            if (comp['id'], pin['number']) not in bound:
                finding('unaccounted_pin', '/components/' + comp['id'], 'pin must be connected or explicit no-connect: ' + pin['number'])
    if result['execution'] == 'ok' and result['findings']:
        result['outcome'] = 'defect'
    result['findings'].sort(key=_json_bytes)
    return _seal(result)


def _hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('invalid SHA256')


def _validate_flat_resolved(record):
    """Reject partial, rejected, tampered or structurally invalid parity inputs."""
    _object(record, ('schema', 'execution', 'outcome', 'coverage', 'findings', 'intent_sha256',
                     'libraries', 'components', 'nets', 'no_connects', 'hierarchy', 'ports', 'canonical_sha256'))
    _list(record['findings'])
    if (record['schema'], record['execution'], record['outcome'], record['coverage']) != (RESOLVED_SCHEMA, 'ok', 'pass', 'complete') or record['findings']:
        raise ValueError('resolved intent is not complete and passing')
    payload = {k: v for k, v in record.items() if k != 'canonical_sha256'}
    if hashlib.sha256(_json_bytes(payload)).hexdigest() != record['canonical_sha256']:
        raise ValueError('resolved identity mismatch')
    for key in ('libraries', 'components', 'nets', 'no_connects', 'hierarchy', 'ports'):
        _list(record[key])
    for key in ('intent_sha256', 'canonical_sha256'):
        _hash(record[key])
    library_sources = {}
    for library in record['libraries']:
        _object(library, ('name', 'path', 'sha256'))
        _text(library['name'])
        _text(library['path'])
        _hash(library['sha256'])
        if library['path'] in library_sources:
            raise ValueError('duplicate library path')
        library_sources[library['path']] = library['sha256']
    def check_source(source, pin=False):
        _object(source, ('path', 'sha256', 'symbol', 'unit_symbol', 'pin') if pin else ('path', 'sha256', 'symbol'))
        for value in source.values():
            _text(value)
        if library_sources.get(source['path']) != source['sha256']:
            raise ValueError('pin/symbol source is not in captured library inventory')
    # Recheck graph boundaries rather than trusting a success receipt.
    ids, refs, inventory = set(), set(), set()
    for comp in record['components']:
        _object(comp, ('id', 'reference', 'lib_id', 'value', 'footprint', 'units',
                       'pins', 'sources', 'power', 'field_sources'), ('part_binding', 'in_bom'))
        for key in ('id', 'reference', 'lib_id', 'value', 'footprint'):
            _text(comp[key], empty=key in ('value', 'footprint'))
        _list(comp['pins'])
        _list(comp['sources'])
        _list(comp['units'])
        if not comp['sources'] or not comp['units'] or type(comp['power']) is not bool:
            raise ValueError('invalid symbol metadata')
        if any(type(u) is not int or u < 1 for u in comp['units']) or len(set(comp['units'])) != len(comp['units']):
            raise ValueError('invalid selected units')
        if not isinstance(comp['field_sources'], dict) or any(not isinstance(v, str) for v in comp['field_sources'].values()):
            raise ValueError('invalid intent field locators')
        if comp['lib_id'].count(':') != 1 or not all(comp['lib_id'].split(':')):
            raise ValueError('invalid library symbol id')
        if 'in_bom' in comp and type(comp['in_bom']) is not bool:
            raise ValueError('in_bom must be a boolean')
        if 'part_binding' in comp and not isinstance(comp['part_binding'], dict):
            raise ValueError('invalid opaque binding')
        for source in comp['sources']:
            check_source(source)
        if comp['id'] in ids or comp['reference'] in refs or not comp['pins']:
            raise ValueError('invalid component inventory')
        ids.add(comp['id'])
        refs.add(comp['reference'])
        for pin in comp['pins']:
            _object(pin, ('number', 'name', 'electrical_type', 'unit', 'x_nm', 'y_nm', 'orientation', 'source'))
            _text(pin['number'])
            _text(pin['name'], empty=True)
            if not isinstance(pin['electrical_type'], str) or pin['electrical_type'] not in ELECTRICAL_TYPES:
                raise ValueError('invalid electrical pin type')
            if type(pin['unit']) is not int or pin['unit'] not in (0, *comp['units']):
                raise ValueError('invalid pin unit')
            if any(type(pin[k]) is not int or abs(pin[k]) > 10**15 for k in ('x_nm', 'y_nm')):
                raise ValueError('invalid exact pin coordinates')
            if type(pin['orientation']) is not int or pin['orientation'] not in (0, 90, 180, 270):
                raise ValueError('invalid pin orientation')
            check_source(pin['source'], pin=True)
            if pin['source']['pin'] != pin['number']:
                raise ValueError('pin locator mismatch')
            key = (comp['id'], pin['number'])
            if key in inventory:
                raise ValueError('duplicate physical pin')
            inventory.add(key)
    memberships = []
    net_ids = set()
    for net in record['nets']:
        _object(net, ('id', 'name', 'terminals'))
        _text(net['id'])
        _text(net['name'], empty=True)
        _list(net['terminals'])
        for terminal in net['terminals']:
            _object(terminal, ('component', 'pin'))
            _text(terminal['component'])
            _text(terminal['pin'])
        if net['id'] in net_ids or len(net['terminals']) < 2:
            raise ValueError('invalid net inventory')
        net_ids.add(net['id'])
        memberships.extend((t['component'], t['pin']) for t in net['terminals'])
    for terminal in record['no_connects']:
        _object(terminal, ('component', 'pin'))
        _text(terminal['component'])
        _text(terminal['pin'])
    memberships.extend((t['component'], t['pin']) for t in record['no_connects'])
    if len(memberships) != len(set(memberships)) or set(memberships) != inventory:
        raise ValueError('incomplete or multiply bound pin inventory')
    if record['hierarchy'] or record['ports']:
        raise ValueError('unsupported hierarchy')
    return record


def _validate_intent(value):
    if isinstance(value, dict) and value.get('schema') == 'agent-ee.connectivity-intent/2':
        from ._connectivity_hierarchy import validate_intent
        validate_intent(value)
    else:
        _validate_flat_intent(value)


def resolve_intent(intent, captured_libraries, *, _library_cache=None):
    """Resolve captured bytes with explicit version dispatch; v1 remains unchanged."""
    try:
        raw = _read_json(intent)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return _resolve_flat_intent(intent, captured_libraries, _library_cache=_library_cache)
    if isinstance(raw, dict) and raw.get('schema') == 'agent-ee.connectivity-intent/2':
        from ._connectivity_hierarchy import resolve
        return resolve(raw, captured_libraries, _library_cache=_library_cache)
    return _resolve_flat_intent(intent, captured_libraries, _library_cache=_library_cache)


def validate_resolved(record):
    """Validate the whole versioned record; a selfhash is not source authority."""
    if isinstance(record, dict) and record.get('schema') == 'agent-ee.resolved-connectivity/2':
        from ._connectivity_hierarchy import validate_resolved as validate_hierarchy
        return validate_hierarchy(record)
    return _validate_flat_resolved(record)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('intent', type=Path)
    parser.add_argument('--captures', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    with args.intent.open('rb') as stream:
        data = stream.read(8 * 1024 * 1024 + 1)
    captures = {}
    try:
        raw = _read_json(data)
        _validate_intent(raw)
        root = args.captures.resolve(strict=True)
        for lib in raw['libraries']:
            path = root / lib['path']
            # Captured files must be regular files without a symlink escape.
            if not path.is_file() or any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
                continue
            try:
                with path.open('rb') as stream:
                    captures[lib['path']] = stream.read(32 * 1024 * 1024 + 1)
            except OSError:
                continue  # Pure resolver records missing coverage for the declared capture.
    except (ValueError, TypeError, UnicodeError, RecursionError, OSError):
        pass  # Resolver owns schema diagnostics; absent captures remain failed coverage.
    result = resolve_intent(data, captures)
    args.out.mkdir(parents=True, exist_ok=False)
    artifact = canonical_resolved(result)
    (args.out / 'resolved.json').write_bytes(artifact + b'\n')
    manifest = dict(schema='agent-ee.connectivity-validation/1', execution=result['execution'],
                    outcome=result['outcome'], coverage=result['coverage'],
                    artifacts=[dict(path='resolved.json', sha256=hashlib.sha256(artifact + b'\n').hexdigest())])
    (args.out / 'manifest.json').write_bytes(_json_bytes(manifest) + b'\n')
    return 0 if result['outcome'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())

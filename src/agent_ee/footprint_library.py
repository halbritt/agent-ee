"""Offline footprint inventory and assignment over explicit captured bytes.

The complete manifest is an owner declaration of library closure, not discovery
of the host's KiCad installation. This module never opens a library path.
"""
import hashlib
import json
from pathlib import PurePosixPath


class FootprintError(ValueError):
    pass


def load_document(path):
    from pathlib import Path
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise FootprintError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_bytes(), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(FootprintError('non-finite JSON')))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _seal(result):
    result['canonical_sha256'] = digest(result)
    return result


def _object(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise FootprintError('expected fields: ' + ', '.join(keys))


def _text(value, empty=False):
    if not isinstance(value, str) or (not empty and not value) or '\x00' in value:
        raise FootprintError('expected string')
    return value


def _members(manifest):
    _object(manifest, ('schema', 'complete', 'libraries'))
    if manifest['schema'] != 'agent-ee.footprint-libraries/1' or manifest['complete'] is not True:
        raise FootprintError('complete versioned footprint manifest required')
    if not isinstance(manifest['libraries'], list) or not manifest['libraries']:
        raise FootprintError('nonempty libraries required')
    names, paths = set(), set()
    entries = []
    for lib in manifest['libraries']:
        _object(lib, ('name', 'members'))
        name = _text(lib['name'])
        if ':' in name or name in names:
            raise FootprintError('duplicate/invalid library name')
        names.add(name)
        if not isinstance(lib['members'], list):
            raise FootprintError('explicit member list required')
        for member in lib['members']:
            _object(member, ('path', 'sha256'))
            path = PurePosixPath(_text(member['path']))
            if (path.is_absolute() or '..' in path.parts or str(path) != member['path']
                    or '\\' in str(path) or path.suffix != '.kicad_mod' or str(path) in paths):
                raise FootprintError('duplicate/unsafe footprint path')
            sha = _text(member['sha256'])
            if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
                raise FootprintError('invalid sha256')
            paths.add(str(path))
            entries.append((name, member))
    if len(entries) > 10000:
        raise FootprintError('footprint member limit exceeded')
    return sorted(entries, key=lambda x: (x[0], x[1]['path']))


DECLARATIONS_SCHEMA = 'agent-ee.footprint-declarations/1'
DECLARED_SHAPES = ('custom', 'trapezoid')


def _numbers(value):
    if not isinstance(value, list) or len(set(map(str, value))) != len(value):
        raise FootprintError('declared pad numbers must be a list of unique strings')
    return [_text(x, empty=True) for x in value]


def validate_declarations(declarations):
    """Owner-captured pad/assembly/pinless declarations; nothing is guessed from names.

    Returns ``(footprints, pinless)`` keyed by exact footprint and symbol identity.
    """
    _object(declarations, ('schema', 'footprints', 'pinless_symbols'))
    if declarations['schema'] != DECLARATIONS_SCHEMA:
        raise FootprintError('unsupported footprint declaration schema')
    if not isinstance(declarations['footprints'], list) or not isinstance(declarations['pinless_symbols'], list):
        raise FootprintError('footprint and pinless declaration lists required')
    footprints, pinless = {}, {}
    for entry in declarations['footprints']:
        _object(entry, ('footprint', 'mechanical_pads', 'thermal_via_pads', 'custom_pads', 'assembly', 'rationale'))
        lib_id = _text(entry['footprint'])
        if lib_id.count(':') != 1 or not all(lib_id.split(':')) or lib_id in footprints:
            raise FootprintError('unique exact library:footprint declaration required')
        mechanical, thermal, custom = (_numbers(entry[k]) for k in ('mechanical_pads', 'thermal_via_pads', 'custom_pads'))
        if set(mechanical) & set(thermal) or '' in thermal:
            raise FootprintError('thermal-via pads must be numbered and not declared mechanical')
        if entry['assembly'] not in (None, 'smd', 'through_hole'):
            raise FootprintError('declared assembly must be null, smd or through_hole')
        if not (mechanical or thermal or custom or entry['assembly']):
            raise FootprintError('footprint declaration declares nothing')
        _text(entry['rationale'])
        footprints[lib_id] = entry
    for entry in declarations['pinless_symbols']:
        _object(entry, ('lib_id', 'rationale'))
        lib_id = _text(entry['lib_id'])
        if lib_id.count(':') != 1 or not all(lib_id.split(':')) or lib_id in pinless:
            raise FootprintError('unique exact library:symbol pinless declaration required')
        _text(entry['rationale'])
        pinless[lib_id] = entry
    return footprints, pinless


def _apply_declaration(pads, attributes, entry):
    """Every declared number must change a pad's handling; a stale declaration is unknown."""
    if entry is None:
        return
    for number in entry['mechanical_pads']:
        if not any(p['number'] == number and p.get('declared') == 'mechanical' for p in pads):
            raise FootprintError(f'declared mechanical pad {number!r} matches no copper or numbered NPTH pad')
    for number in entry['thermal_via_pads']:
        types = {p['type'] for p in pads if p['number'] == number and p['role'] == 'electrical'}
        if types != {'smd', 'thru_hole'}:
            raise FootprintError(f'declared thermal-via pad {number!r} needs SMD and through-hole copper pads')
        for pad in pads:
            if pad['number'] == number and pad['type'] == 'thru_hole':
                pad['thermal_via'] = True
    for number in entry['custom_pads']:
        if not any(p['number'] == number and p.get('geometry') == 'declared_custom' for p in pads):
            raise FootprintError(f'declared custom pad {number!r} matches no custom/trapezoid pad')
    if entry['assembly'] is not None and set(attributes) & {'smd', 'through_hole'}:
        raise FootprintError('declared assembly contradicts or duplicates a native assembly attribute')


def _footprint(data, library, source, declared=None):
    # Shared reader owned by AGENTEE-39; no competing S-expression parser.
    try:
        from .symbol_library import parse_sexpr, children, field, nanometres, LibraryError
    except ImportError as exc:
        raise FootprintError("shared generic S-expression reader unavailable: " + str(exc)) from exc
    try:
        if b'\x00' in data:
            raise LibraryError('NUL byte in native footprint')
        node = parse_sexpr(data)
        if len(node) < 2 or node[0] != 'footprint' or not isinstance(node[1], str):
            raise LibraryError('expected native footprint')
        if node[1] != PurePosixPath(source['path']).stem:
            raise LibraryError('footprint name differs from manifest filename')
        attrs = children(node, 'attr')
        if len(attrs) > 1 or (attrs and any(not isinstance(x, str) for x in attrs[0])):
            raise LibraryError('invalid footprint attributes')
        if field(node, 'layer', 1)[0] not in ('F.Cu', 'B.Cu'):
            raise LibraryError('unqualified footprint side')
        attr = attrs[0][1:] if attrs else []
        entry = (declared or {}).get(library + ':' + node[1])
        mechanical = set(entry['mechanical_pads']) if entry else set()
        custom = set(entry['custom_pads']) if entry else set()
        metadata = {}
        for key in ('descr', 'tags'):
            metadata[key] = field(node, key, 1)[0] if children(node, key) else ''
        pads = []
        for pad in children(node, 'pad'):
            if len(pad) < 4 or any(not isinstance(x, str) for x in pad[:4]):
                raise LibraryError('invalid pad header')
            number, kind, shape = pad[1:4]
            # Declared custom geometry is accepted for numbering only, never qualified.
            geometry = 'declared_custom' if number in custom and (
                shape in DECLARED_SHAPES or any(children(pad, x) for x in ('primitives', 'options'))) else None
            if kind not in ('smd', 'thru_hole', 'np_thru_hole') or (
                    shape not in ('rect', 'circle', 'oval', 'roundrect') and geometry is None):
                raise LibraryError('unsupported native pad type/geometry')
            if children(pad, 'padstack') or (geometry is None and any(children(pad, x) for x in ('primitives', 'options'))):
                raise LibraryError('unsupported custom padstack')
            layers = children(pad, 'layers')
            if len(layers) != 1 or len(layers[0]) < 2 or any(not isinstance(x, str) for x in layers[0][1:]):
                raise LibraryError('pad requires explicit layers')
            layers = sorted(layers[0][1:])
            known_layers = {'F.Cu', 'B.Cu', '*.Cu', 'F.Paste', 'B.Paste', 'F.Mask', 'B.Mask', '*.Mask'} | {f'In{i}.Cu' for i in range(1, 31)}
            if any(layer not in known_layers for layer in layers) or len(set(layers)) != len(layers):
                raise LibraryError('unsupported/duplicate pad layer')
            copper = any(x.endswith('.Cu') for x in layers)
            at = children(pad, 'at')
            if len(at) != 1 or len(at[0]) not in (3, 4):
                raise LibraryError('pad requires x/y/optional rotation')
            x, y = map(nanometres, at[0][1:3])
            angle = nanometres(at[0][3]) if len(at[0]) == 4 else 0
            size = list(map(nanometres, field(pad, 'size', 2)))
            if any(v <= 0 for v in size):
                raise LibraryError('nonpositive pad size')
            drill = children(pad, 'drill')
            if kind in ('thru_hole', 'np_thru_hole'):
                if len(drill) != 1 or len(drill[0]) not in (2, 4):
                    raise LibraryError('through pad requires qualified drill')
                values = drill[0][1:]
                if values[0] == 'oval':
                    values = values[1:]
                    if len(values) != 2:
                        raise LibraryError('oval drill requires two dimensions')
                elif len(values) != 1:
                    raise LibraryError('unsupported drill form')
                if any(nanometres(v) <= 0 for v in values):
                    raise LibraryError('nonpositive drill')
            elif drill:
                raise LibraryError('SMD drill unsupported')
            extra = {}
            if kind == 'np_thru_hole':
                if number and number not in mechanical:
                    raise LibraryError('numbered mechanical pad unsupported')
                role = 'mechanical'
                if number:
                    extra['declared'] = 'mechanical'
            elif not copper:
                if kind != 'smd' or not layers or any(not x.endswith('.Paste') for x in layers):
                    raise LibraryError('noncopper pad is not qualified paste-only')
                role = 'paste'
            elif number in mechanical:
                role, extra['declared'] = 'mechanical', 'mechanical'
            else:
                if not number:
                    raise LibraryError('unnumbered copper pad unsupported')
                role = 'electrical'
            if geometry:
                extra['geometry'] = geometry
            pads.append(dict(number=number, type=kind, shape=shape, layers=layers,
                             x_nm=x, y_nm=y, orientation_microdegrees=angle,
                             size_nm=size, drill=drill[0][1:] if drill else None, role=role, native=pad, **extra))
        if not pads:
            raise LibraryError('footprint contains no pads')
        _apply_declaration(pads, attr, entry)
        result = dict(lib_id=library + ':' + node[1], name=node[1], description=metadata['descr'],
                      tags=metadata['tags'], attributes=sorted(attr), pads=sorted(pads, key=canonical),
                      electrical_numbers=sorted({p['number'] for p in pads if p['role'] == 'electrical'}),
                      source=dict(source))
        if entry is not None:
            result['declaration'] = entry
        return result
    except (LibraryError, TypeError) as exc:
        raise FootprintError(str(exc)) from exc


def index_footprints(manifest, captured_files, declarations=None):
    """Validate every declared member before any selected-name absence claim.

    Optional ``declarations`` are captured owner pad/assembly declarations
    (``validate_declarations``); without them the index is unchanged.
    """
    result = dict(schema='agent-ee.footprint-index/1', execution='ok', outcome='info',
                  coverage='complete', footprints=[], findings=[], manifest_sha256=None)
    if declarations is not None:
        result['declarations_sha256'] = digest(declarations)
    code = 'manifest'
    try:
        entries = _members(manifest)
        result['manifest_sha256'] = digest(manifest)
        code = 'declaration'
        declared = validate_declarations(declarations)[0] if declarations is not None else {}
    except (FootprintError, TypeError, ValueError) as exc:
        result.update(execution='malformed', outcome='unknown', coverage='missing')
        result['findings'].append(dict(code=code, message=str(exc)))
        return _seal(result)
    total_bytes = 0
    for library, member in entries:
        try:
            data = captured_files.get(member['path'])
            if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != member['sha256']:
                raise FootprintError('missing/unreadable/hash-mismatched captured footprint')
            total_bytes += len(data)
            if total_bytes > 64 * 1024 * 1024:
                raise FootprintError('footprint inventory exceeds 64 MiB byte limit')
            footprint = _footprint(data, library, member, declared)
            if any(x['lib_id'] == footprint['lib_id'] for x in result['footprints']):
                raise FootprintError('duplicate exact footprint identity')
            result['footprints'].append(footprint)
        except FootprintError as exc:
            result.update(execution='failed', outcome='unknown', coverage='missing')
            result['findings'].append(dict(code='library_coverage', path=member['path'], message=str(exc)))
    names = {library + ':' + PurePosixPath(member['path']).stem for library, member in entries}
    for lib_id in sorted(set(declared) - names):
        result.update(execution='failed', outcome='unknown', coverage='missing')
        result['findings'].append(dict(code='declaration', footprint=lib_id,
                                       message='declared footprint is not a manifest member'))
    result['footprints'].sort(key=lambda x: x['lib_id'])
    return _seal(result)


def _symbol_inventory(lib_id, libraries, captured_files, *, _library_cache=None):
    from .symbol_library import ParsedLibraries, resolve_symbol, LibraryError
    cache = _library_cache if _library_cache is not None else ParsedLibraries()
    if not isinstance(lib_id, str) or lib_id.count(':') != 1:
        raise FootprintError('exact library:symbol identity required')
    library, symbol = lib_id.split(':')
    if not library or not symbol or not isinstance(libraries, list):
        raise FootprintError('explicit captured symbol libraries required')
    names, paths = set(), set()
    selected = None
    for lib in libraries:
        _object(lib, ('name', 'path', 'sha256'))
        name, path, sha = (lib[x] for x in ('name', 'path', 'sha256'))
        _text(name)
        _text(path)
        _text(sha)
        p = PurePosixPath(path)
        if (name in names or path in paths or p.is_absolute() or '..' in p.parts
                or str(p) != path or '\\' in path or p.suffix != '.kicad_sym'):
            raise FootprintError('ambiguous/unsafe symbol library declaration')
        names.add(name)
        paths.add(path)
        data = captured_files.get(path)
        if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != sha:
            raise FootprintError('missing/unreadable/hash-mismatched captured symbol library')
        try:
            if b'\x00' in data:
                raise LibraryError('NUL byte in native symbol library')
            symbols = cache.symbols(data)
            if name == library:
                if symbol not in symbols:
                    raise FootprintError('selected symbol absent from captured library: ' + lib_id)
                selected = resolve_symbol(symbols, symbol, path, sha)
        except LibraryError as exc:
            raise FootprintError(str(exc)) from exc
    if selected is None:
        raise FootprintError('selected symbol library is not captured')
    return selected


def board_membership(sources, captured_files):
    from .symbol_library import parse_library, children, field, LibraryError
    on_board = True  # KiCad's default when no declaration is present.
    try:
        for source in sources:
            node = parse_library(captured_files[source['path']])[source['symbol']]
            if children(node, 'on_board'):
                value = field(node, 'on_board', 1)[0]
                if value not in ('yes', 'no'):
                    raise LibraryError('invalid native board-membership flag')
                on_board = value == 'yes'
    except LibraryError as exc:
        raise FootprintError(str(exc)) from exc
    return on_board


def validate_declaration(declaration):
    _object(declaration, ('schema', 'libraries', 'components'))
    if declaration['schema'] != 'agent-ee.footprint-assignments-input/1':
        raise FootprintError('unsupported assignment schema')
    if not isinstance(declaration['components'], list) or not declaration['components']:
        raise FootprintError('nonempty component declarations required')
    if not isinstance(declaration['libraries'], list) or not declaration['libraries']:
        raise FootprintError('nonempty symbol library list required')
    for lib in declaration['libraries']:
        _object(lib, ('name', 'path', 'sha256'))
        for value in lib.values():
            _text(value)
    refs = set()
    for comp in declaration['components']:
        _object(comp, ('reference', 'lib_id', 'footprint', 'assembly', 'connector'))
        ref = _text(comp['reference'])
        _text(comp['footprint'], empty=True)
        _text(comp['lib_id'])
        if comp['lib_id'].count(':') != 1 or not all(comp['lib_id'].split(':')):
            raise FootprintError('exact library:symbol identity required')
        if ref in refs or comp['assembly'] not in ('smd', 'through_hole', 'mixed'):
            raise FootprintError('unique references and explicit assembly intent required')
        refs.add(ref)
        conn = comp['connector']
        if conn is not None:
            _object(conn, ('rows', 'columns'))
            if any(type(v) is not int or v <= 0 for v in conn.values()):
                raise FootprintError('connector requires positive integer rows/columns')


def check_assignments(manifest, captured_files, declaration, native_membership=False, declarations=None):
    """Check declared physical components against full shared-resolver pin inventory.

    Declaration bytes must be captured by the coordinator before using this as
    evidence. All logical device units are compared, including unconnected pins.
    No schematic/intent reference-name inference or package choice is performed.
    With native_membership the caller has already established native board
    membership; captured power/on_board flags are recorded but exclude nothing.
    Optional captured ``declarations`` declare mechanical copper pads, thermal
    vias, custom pads, attr-less assembly and pinless symbols; anything
    undeclared keeps the default defect/unknown handling.
    """
    index = index_footprints(manifest, captured_files, declarations)
    result = dict(schema='agent-ee.footprint-assignments/1', execution=index['execution'],
                  outcome='pass' if index['execution'] == 'ok' else 'unknown',
                  coverage=index['coverage'], index=index, index_sha256=index['canonical_sha256'],
                  declaration_sha256=None, assignments=[], findings=list(index['findings']))
    if declarations is not None:
        result['declarations_sha256'] = digest(declarations)
    try:
        result['declaration_sha256'] = digest(declaration)
        validate_declaration(declaration)
        pinless = validate_declarations(declarations)[1] if declarations is not None else {}
    except (ValueError, TypeError) as exc:
        result.update(execution='malformed', outcome='unknown', coverage='missing')
        result['findings'].append(dict(code='declaration', message=str(exc)))
        return _seal(result)
    footprints = {x['lib_id']: x for x in index['footprints']}
    for comp in sorted(declaration['components'], key=lambda x: x['reference']):
        assignment = dict(reference=comp['reference'], lib_id=comp['lib_id'], footprint=comp['footprint'],
                          status='unknown', findings=[])
        result['assignments'].append(assignment)
        try:
            if comp['assembly'] == 'mixed':
                raise FootprintError('mixed assembly intent is not qualified')
            pins, sources, power = _symbol_inventory(comp['lib_id'], declaration['libraries'], captured_files)
            assignment['symbol_sources'] = sources
            assignment['pin_numbers'] = sorted({p['number'] for p in pins})
            on_board = board_membership(sources, captured_files)
            if native_membership:
                assignment.update(library_power=power, library_on_board=on_board)
                power, on_board = False, True
            assignment['on_board'] = on_board
            if power or not on_board:
                assignment.update(status='excluded', exclusion='pinned native power symbol' if power else 'pinned native on_board=no')
                continue
            if pins and comp['lib_id'] in pinless:
                raise FootprintError('pinless declaration contradicts the symbol pin inventory')
            if not pins and comp['lib_id'] not in pinless:
                raise FootprintError('physical symbol has no qualified pins')
            if not pins:
                assignment['pinless'] = pinless[comp['lib_id']]
            if index['coverage'] != 'complete':
                raise FootprintError('footprint index incomplete; absence/parity unavailable')
            if not comp['footprint']:
                assignment['findings'].append(dict(code='empty_footprint'))
            elif comp['footprint'] not in footprints:
                assignment['findings'].append(dict(code='absent_footprint'))
            else:
                fp = footprints[comp['footprint']]
                assignment['footprint_source'] = fp['source']
                if 'declaration' in fp:
                    assignment['footprint_declaration'] = fp['declaration']
                    assignment['mechanical_pads'] = sorted({p['number'] for p in fp['pads'] if p.get('declared')})
                numbers = set(fp['electrical_numbers'])
                missing = sorted(set(assignment['pin_numbers']) - numbers)
                extra = sorted(numbers - set(assignment['pin_numbers']))
                if missing or extra:
                    assignment['findings'].append(dict(code='pad_pin_parity', missing_pads=missing, extra_pads=extra))
                if comp['connector'] is not None:
                    expected = comp['connector']['rows'] * comp['connector']['columns']
                    if len(numbers) != expected:
                        assignment['findings'].append(dict(code='connector_count', expected=expected, actual=len(numbers)))
                # Declared thermal vias are electrical but do not make an SMD part through-hole.
                types = {p['type'] for p in fp['pads'] if p['role'] == 'electrical' and not p.get('thermal_via')}
                attrs = set(fp['attributes']) & {'smd', 'through_hole'}
                declared_assembly = (fp.get('declaration') or {}).get('assembly')
                if declared_assembly is not None:
                    attrs = {declared_assembly}  # The index refuses one beside a native attribute.
                technology = {frozenset({'smd'}): 'smd', frozenset({'thru_hole'}): 'through_hole',
                              frozenset({'smd', 'thru_hole'}): 'mixed'}.get(frozenset(types))
                if not types and not pins and len(attrs) == 1:
                    technology = next(iter(attrs))  # Declared pinless: no electrical pads to compare.
                if technology is None or not attrs or (technology == 'smd' and attrs != {'smd'}) or (technology == 'through_hole' and attrs != {'through_hole'}):
                    raise FootprintError('ambiguous/missing native assembly attributes')
                if technology == 'mixed':
                    raise FootprintError('mixed assembly semantics are not qualified')
                if comp['assembly'] != technology:
                    assignment['findings'].append(dict(code='assembly_mismatch', expected=comp['assembly'], actual=technology))
            assignment['status'] = 'defect' if assignment['findings'] else 'pass'
        except FootprintError as exc:
            result.update(execution='failed', outcome='unknown', coverage='missing')
            assignment['findings'].append(dict(code='coverage', message=str(exc)))
        result['findings'].extend(dict(finding, reference=comp['reference'], footprint=comp['footprint'],
                                       lib_id=comp['lib_id']) for finding in assignment['findings'])
    if result['coverage'] == 'complete' and result['findings']:
        result['outcome'] = 'defect'
    if result['coverage'] == 'complete' and all(a['status'] == 'excluded' for a in result['assignments']):
        result['outcome'] = 'info'
    return _seal(result)


def resolve_package(table, family, package, manifest, captured_files):
    """Exact captured alias rows; ambiguity never chooses the first row."""
    result = dict(schema='agent-ee.package-resolution/1', execution='failed', outcome='unknown',
                  table_sha256=digest(table), footprint=None, source=None)
    try:
        _object(table, ('schema', 'convention', 'rows'))
        if table['schema'] != 'agent-ee.package-aliases/1' or table['convention'] != 'explicit-imperial-to-metric':
            raise FootprintError('versioned explicit alias convention required')
        if not isinstance(table['rows'], list):
            raise FootprintError('alias rows required')
        for row in table['rows']:
            _object(row, ('family', 'package', 'footprint'))
            for v in row.values():
                _text(v)
        matches = [r for r in table['rows'] if (r['family'], r['package']) == (family, package)]
        if len(matches) != 1:
            raise FootprintError('ambiguous alias' if matches else 'unresolved alias')
        index = index_footprints(manifest, captured_files)
        if index['coverage'] != 'complete':
            raise FootprintError('incomplete footprint index')
        target = matches[0]['footprint']
        matches = [fp for fp in index['footprints'] if fp['lib_id'] == target]
        if len(matches) != 1:
            raise FootprintError('alias target unavailable')
        result.update(execution='ok', outcome='info', footprint=matches[0]['lib_id'], source=matches[0]['source'])
    except (ValueError, TypeError) as exc:
        result['message'] = str(exc)
    return _seal(result)


def read_captures(root, paths):
    """Read only explicit safe paths, including containment after symlink resolution."""
    from pathlib import Path
    root = Path(root).resolve(strict=True)
    captures = {}
    for path in paths:
        p = PurePosixPath(path)
        if p.is_absolute() or '..' in p.parts or str(p) != path or '\\' in path:
            raise FootprintError('unsafe captured path')
        resolved = (root / path).resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise FootprintError('captured path escapes root')
        captures[path] = resolved.read_bytes()
    return captures


def main(argv=None):
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--captures', required=True)
    parser.add_argument('--assignments')
    parser.add_argument('--declarations', help='captured agent-ee.footprint-declarations/1 file')
    parser.add_argument('--query', help='literal exact footprint identity; never shell syntax')
    parser.add_argument('--out', required=True, help='new output JSON file')
    args = parser.parse_args(argv)
    try:
        manifest = load_document(args.manifest)
        declaration = load_document(args.assignments) if args.assignments else None
        declarations = load_document(args.declarations) if args.declarations else None
        members = _members(manifest)
        paths = [member['path'] for _, member in members]
        if declaration is not None:
            paths += [lib['path'] for lib in declaration['libraries']]
        captures = read_captures(args.captures, paths)
        result = (check_assignments(manifest, captures, declaration, declarations=declarations)
                  if declaration is not None else index_footprints(manifest, captures, declarations))
        if args.query is not None:
            result = dict(schema='agent-ee.footprint-query/1', query=args.query,
                          index_sha256=result['canonical_sha256'], execution=result['execution'],
                          outcome='info' if result['coverage'] == 'complete' else 'unknown',
                          matches=[x for x in result.get('footprints', []) if x['lib_id'] == args.query])
            result = _seal(result)
        with Path(args.out).open('xb') as stream:
            stream.write(canonical(result) + b'\n')
        return 0 if result['execution'] == 'ok' else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())

"""Bind captured footprint assignments to one strict native KiCad design inventory.

Native XML is read only through ``native_netlist.read_native_inventory``. Index,
pin inventory and pad parity reuse ``footprint_library``. Run
``python -m agent_ee.native_binding --help`` for the sandboxed boundary. See
docs/native-binding.md for the exporter semantics these rules depend on.
"""
import hashlib
import posixpath

from .footprint_library import (FootprintError, _seal, _symbol_inventory, check_assignments, digest,
                                validate_declaration)

SCHEMA = 'agent-ee.footprint-native-assignments/1'
PREDICATE = 'footprint:native-assignment-parity/1'
BOARD_EXCLUSION = 'native exclude_from_board instance property'
VIRTUAL_EXCLUSION = 'native exporter omits #-referenced instances; embedded definition is power'
# Findings that leave a reference's binding unestablished rather than defective.
UNKNOWN_CODES = frozenset(('undeclared_native_component', 'library_pin_mismatch', 'coverage'))


def native_pin_name(name):
    """Pin-name rewriting observed in KiCad 10.0.7 XML exports of owned symbols."""
    return '' if name == '~' else name.replace(' ', '_')


MAX_OCCURRENCES = 4096
# Occurrence identities left unestablished; each makes board scope unproved.
OCCURRENCE_PROBLEMS = {
    'ambiguous_occurrence_identity': 'omitted occurrence has instance entries with different references',
    'missing_occurrence_identity': 'omitted occurrence has no instance entry of its own',
    'duplicate_occurrence_reference': 'reference names more than one omitted occurrence',
    'unexplained_omission': 'native export omits an occurrence whose reference does not begin with #',
    'occurrence_identity_conflict': 'native reference differs from the occurrence instance entries',
    'native_identity_unmatched': 'native component identity is not an occurrence of the captured hierarchy',
    'hierarchy_mismatch': 'native sheets differ from the captured hierarchy occurrences',
    'legacy_symbol_instances': 'legacy symbol_instances block is not qualified',
}


def _sheet(path, root):
    """Embedded definitions, placed symbols and child sheets of one captured file."""
    from .symbol_library import LibraryError, children, field
    if root[0] != 'kicad_sch':
        raise LibraryError('expected kicad_sch')
    caches = children(root, 'lib_symbols')
    if len(caches) > 1:
        raise LibraryError('duplicate embedded symbol cache')
    embedded = {}
    for node in children(caches[0], 'symbol') if caches else []:
        if len(node) < 2 or not isinstance(node[1], str) or node[1] in embedded:
            raise LibraryError('invalid or duplicate embedded symbol')
        embedded[node[1]] = node
    symbols, uuids = [], set()
    for node in children(root, 'symbol'):
        lib_id = field(node, 'lib_id', 1)[0]
        key = field(node, 'lib_name', 1)[0] if children(node, 'lib_name') else lib_id
        uuid = field(node, 'uuid', 1)[0]
        if uuid in uuids:
            raise LibraryError('duplicate symbol UUID')
        uuids.add(uuid)
        # Document order across every project: the exporter's own reading order.
        entries = [(entry[1], field(entry, 'reference', 1)[0])
                   for instances in children(node, 'instances')
                   for project in children(instances, 'project')
                   for entry in children(project, 'path')]
        if any(not isinstance(p, str) or not isinstance(r, str) for p, r in entries):
            raise LibraryError('invalid symbol instance entry')
        definition = embedded.get(key)
        symbols.append(dict(uuid=uuid, lib_id=lib_id, entries=entries,
                            embedded_power=definition is not None and bool(children(definition, 'power'))))
    sheets = []
    for node in children(root, 'sheet'):
        files = [p[2] for p in children(node, 'property')
                 if len(p) > 2 and p[1] in ('Sheetfile', 'Sheet file')]
        if len(files) != 1 or not isinstance(files[0], str):
            raise LibraryError('sheet needs exactly one Sheetfile')
        sheets.append((field(node, 'uuid', 1)[0], posixpath.normpath(
            posixpath.join(posixpath.dirname(path), files[0]))))
    return dict(uuid=field(root, 'uuid', 1)[0], symbols=symbols, sheets=sheets,
                legacy=bool(children(root, 'symbol_instances')))


def virtual_symbols(schematics, root, graph):
    """Exporter-omitted symbol occurrences of the captured hierarchy under ``root``.

    ``schematics`` maps every captured sheet path to bytes; ``graph`` is the
    strict native inventory exported from them. An occurrence is one sheet
    instance path and symbol UUID. Exported occurrences take the native
    reference. An omitted one takes the reference of its own instance entries,
    whatever their project. Entries for other paths never apply. Records with
    ``problem`` leave an identity unestablished. Each resolved record says
    whether the embedded definition, which KiCad exports from, is power.
    """
    from .symbol_library import LibraryError, parse_sexpr
    records = []
    try:
        files = {path: _sheet(path, parse_sexpr(data)) for path, data in schematics.items()}
        if root not in files:
            raise LibraryError('root sheet not captured')
        occurrences, pending = [], [(root, '/' + files[root]['uuid'], '/', ())]
        while pending:
            path, instance, sheet, trail = pending.pop()
            if path in trail:
                raise LibraryError('sheet hierarchy cycle')
            occurrences.append((path, instance, sheet))
            if len(occurrences) > MAX_OCCURRENCES:
                raise LibraryError('sheet occurrence limit exceeded')
            for uuid, child in reversed(files[path]['sheets']):
                if child not in files:
                    raise LibraryError(f'sheet {child!r} not captured')
                pending.append((child, instance + '/' + uuid, sheet + uuid + '/', trail + (path,)))
        sheets = [sheet for _, _, sheet in occurrences]
        if len(sheets) != len(set(sheets)) or {p for p, _, _ in occurrences} != files.keys():
            raise LibraryError('captured sheets differ from one hierarchy')
    except (LibraryError, IndexError, TypeError) as exc:
        raise FootprintError('exporter-omission audit failed: ' + str(exc)) from exc
    native = {(c['sheet'], uuid): ref for ref, c in graph['components'].items()
              for uuid in c['uuid'].split()}
    if sorted(sheets) != sorted(graph['sheets']):
        records.append(dict(file=root, references=[], problem='hierarchy_mismatch', captured=sorted(sheets),
                            native=sorted(graph['sheets'])))
    records.extend(dict(file=path, references=[], problem='legacy_symbol_instances')
                   for path in sorted(files) if files[path]['legacy'])
    seen = set()
    for path, instance, sheet in occurrences:
        for symbol in files[path]['symbols']:
            own = sorted({ref for entry, ref in symbol['entries'] if entry == instance})
            record = dict(file=path, sheet=sheet, uuid=symbol['uuid'], lib_id=symbol['lib_id'],
                          references=own, embedded_power=symbol['embedded_power'])
            exported = native.get((sheet, symbol['uuid']))
            if exported is not None:
                seen.add((sheet, symbol['uuid']))
                if exported.startswith('#') or (own and exported not in own):
                    records.append(dict(record, problem='occurrence_identity_conflict', native=exported))
            elif len(own) != 1:
                records.append(dict(record, problem='ambiguous_occurrence_identity' if own
                                    else 'missing_occurrence_identity'))
            else:
                records.append(record if own[0].startswith('#') else dict(record, problem='unexplained_omission'))
    records.extend(dict(file=None, sheet=sheet, uuid=uuid, references=[ref],
                        problem='native_identity_unmatched')
                   for (sheet, uuid), ref in sorted(native.items()) if (sheet, uuid) not in seen)
    resolved = [r['references'][0] for r in records if 'problem' not in r]
    for record in records:
        if 'problem' not in record and resolved.count(record['references'][0]) > 1:
            record['problem'] = 'duplicate_occurrence_reference'
    return records


def _projection(graph):
    keys = ('sheet', 'uuid', 'lib_id', 'value', 'footprint', 'populated', 'on_board', 'in_bom',
            'pins', 'library_pins')
    return dict(sheets=graph['sheets'], exporter=graph['exporter'], library_parts=graph['library_parts'],
                components={ref: {k: c[k] for k in keys} for ref, c in graph['components'].items()})


def _library_differences(lib_id, libraries, captured_files, native_pins):
    """Complete captured pins versus complete native libpart pins, by number."""
    captured = {}
    for pin in _symbol_inventory(lib_id, libraries, captured_files)[0]:
        captured.setdefault(pin['number'], set()).add((native_pin_name(pin['name']), pin['electrical_type']))
    differences = []
    for number in sorted(captured.keys() | native_pins.keys()):
        expected, actual = captured.get(number), native_pins.get(number)
        if (expected is None or actual is None or len(expected) != 1
                or next(iter(expected)) != (actual['name'], actual['electrical_type'])):
            differences.append(dict(pin=number, native=actual,
                                    captured=sorted(map(list, expected)) if expected else None))
    return differences


def bind_native_assignments(manifest, captured_files, declaration, graph, virtual, native_xml_sha256=None):
    """Bind every native board component to an exact declaration and captured library.

    ``graph`` is a strict native inventory; ``virtual`` comes from
    ``virtual_symbols`` over the same candidate's captured sheets and graph.
    """
    result = dict(schema=SCHEMA, predicate=PREDICATE, execution='ok', outcome='unknown',
                  coverage='complete', native_xml_sha256=native_xml_sha256, declaration_sha256=None,
                  native_inventory_sha256=digest(_projection(graph)), exporter=graph['exporter'],
                  native_sheets=list(graph['sheets']), bindings=[], exclusions=[],
                  virtual_symbols=virtual, assignments=None, findings=[])
    try:
        result['declaration_sha256'] = digest(declaration)
        validate_declaration(declaration)
    except (ValueError, TypeError) as exc:
        result.update(execution='malformed', coverage='missing')
        result['findings'].append(dict(code='declaration', message=str(exc)))
        return _seal(result)
    declared = {c['reference']: c for c in declaration['components']}
    native = graph['components']
    virtual_refs, unresolved = {}, False
    for record in virtual:
        if 'problem' in record:
            unresolved = True
            result['findings'].append(dict(record, code=record['problem'],
                                           message=OCCURRENCE_PROBLEMS[record['problem']]))
        elif record['embedded_power']:
            result['exclusions'].append(dict(record, kind='exporter_virtual', reason=VIRTUAL_EXCLUSION))
            virtual_refs.update(dict.fromkeys(record['references'], record))
        else:
            unresolved = True
            result['findings'].append(dict(record, code='virtual_nonpower_symbol',
                message='native export omits this non-power instance; its board scope is unproved'))
    effective, extra = [], []
    for ref in sorted(native.keys() | declared.keys()):
        comp, decl = native.get(ref), declared.get(ref)
        binding = dict(reference=ref, status='unknown', findings=[])
        result['bindings'].append(binding)
        if decl is not None:
            binding.update(declaration_locator=f'components[reference={ref!r}]',
                           declared_lib_id=decl['lib_id'], declared_footprint=decl['footprint'])
        if comp is None:
            if ref in virtual_refs:
                native_id = virtual_refs[ref]['lib_id']
                binding.update(lib_id=native_id, exclusion=VIRTUAL_EXCLUSION)
                if decl['lib_id'] == native_id:
                    binding['status'] = 'excluded'
                else:
                    binding['findings'].append(dict(code='lib_id_mismatch', declared=decl['lib_id'],
                                                    native=native_id))
            else:
                binding['findings'].append(dict(code='ghost_declaration',
                    message='declared reference is absent from the native design'))
            continue
        binding.update({k: comp[k] for k in ('lib_id', 'footprint', 'populated', 'on_board', 'in_bom',
                                             'sheet', 'uuid')}, native_locator=comp['locator'])
        if not comp['on_board']:
            binding.update(status='excluded', exclusion=BOARD_EXCLUSION)
            result['exclusions'].append(dict(kind='board', reference=ref, reason=BOARD_EXCLUSION,
                                             native_locator=comp['locator']))
            continue
        if decl is None:
            binding['findings'].append(dict(code='undeclared_native_component',
                message='native board component has no declared assembly/connector policy'))
            continue
        for key in ('lib_id', 'footprint'):
            if decl[key] != comp[key]:
                binding['findings'].append(dict(code=key + '_mismatch', declared=decl[key], native=comp[key]))
        try:
            differences = _library_differences(comp['lib_id'], declaration['libraries'], captured_files,
                                               graph['library_parts'][comp['lib_id']])
            if differences:
                binding['findings'].append(dict(code='library_pin_mismatch', pins=differences,
                    message='native embedded pin inventory differs from the captured selected library'))
        except FootprintError:
            pass  # The shared parity pass reports this reference's captured-library failure.
        # Parity always evaluates the native selection, never a declared substitute.
        effective.append(dict(reference=ref, lib_id=comp['lib_id'], footprint=comp['footprint'],
                              assembly=decl['assembly'], connector=decl['connector']))
        extra.append(binding)
    base, parity = None, {}
    if effective:
        base = check_assignments(manifest, captured_files, dict(declaration, components=effective),
                                 native_membership=True)
        result['assignments'] = base
        parity = {a['reference']: a for a in base['assignments']}
        for binding in extra:
            assignment = parity[binding['reference']]
            binding['assignment_status'] = assignment['status']
            binding['findings'].extend(assignment['findings'])
        result['findings'].extend(base['findings'])
    for binding in result['bindings']:
        shared = parity.get(binding['reference'], {}).get('findings', [])
        own = [f for f in binding['findings'] if f not in shared]
        result['findings'].extend(dict(f, reference=binding['reference']) for f in own)
        if binding['status'] == 'excluded':
            continue
        codes = {f['code'] for f in binding['findings']}
        if codes & UNKNOWN_CODES or binding.get('assignment_status') == 'unknown':
            binding['status'], unresolved = 'unknown', True
        else:
            binding['status'] = 'defect' if codes else 'pass'
    if base is not None and base['execution'] != 'ok':
        result.update(execution=base['execution'], coverage='missing')
    elif unresolved:
        result.update(execution='unsupported', coverage='missing')
    elif any(b['status'] == 'defect' for b in result['bindings']):
        result['outcome'] = 'defect'
    else:
        result['outcome'] = 'pass' if effective else 'info'
    return _seal(result)


def _failure(execution, code, message, native_xml_sha256):
    return _seal(dict(schema=SCHEMA, predicate=PREDICATE, execution=execution, outcome='unknown',
                      coverage='missing', native_xml_sha256=native_xml_sha256, bindings=[],
                      findings=[dict(code=code, message=message)]))


def main(argv=None):
    import argparse
    from pathlib import Path
    from xml.etree.ElementTree import ParseError
    from .footprint_library import _members, canonical, load_document, read_captures
    from .native_netlist import read_native_inventory
    from .netlist_parity import Unsupported
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--captures', required=True)
    parser.add_argument('--assignments', required=True)
    parser.add_argument('--native-xml', required=True, help='retained native netlist producer XML')
    parser.add_argument('--schematic', required=True, nargs='+', help='every captured sheet path, root first')
    parser.add_argument('--out', required=True, help='new output JSON file')
    args = parser.parse_args(argv)
    try:
        sha = hashlib.sha256(Path(args.native_xml).read_bytes()).hexdigest()
    except OSError as exc:
        parser.exit(2, str(exc) + '\n')
    try:
        graph = read_native_inventory(args.native_xml)
    except Unsupported as exc:
        result = _failure('unsupported', 'native_inventory', str(exc), sha)
    except (ValueError, KeyError, TypeError, ParseError, UnicodeError) as exc:
        result = _failure('malformed', 'native_inventory', str(exc), sha)
    else:
        try:
            manifest = load_document(args.manifest)
            declaration = load_document(args.assignments)
            paths = [m['path'] for _, m in _members(manifest)]
            paths += [lib['path'] for lib in declaration['libraries']]
            captures = read_captures(args.captures, paths)
            sheets = read_captures(args.captures, args.schematic)
            virtual = virtual_symbols(sheets, args.schematic[0], graph)
            result = bind_native_assignments(manifest, captures, declaration, graph, virtual, sha)
        except OSError as exc:
            result = _failure('failed', 'captured_input', str(exc), sha)
        except FootprintError as exc:
            result = _failure('failed', 'captured_input', str(exc), sha)
        except (ValueError, KeyError, TypeError) as exc:
            result = _failure('malformed', 'captured_input', str(exc), sha)
    with Path(args.out).open('xb') as stream:
        stream.write(canonical(result) + b'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

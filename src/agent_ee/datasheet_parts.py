"""Pure selection adapters. Selection provenance never confers owner approval."""
from collections import Counter
import csv
import hashlib
import io
from pathlib import Path
import re

from .candidate import safe_relative
from .catalog import exact_fields, nonempty
from .storage import identity

SCHEMA = 'agent-ee.datasheet-bindings/1'
RUNG1 = 'agent-ee.rung1-parts-manifest/0-experimental'
COLUMNS = ['Comment', 'Designator', 'Footprint', 'LCSC Part #', 'Quantity']
COLUMNS_4 = ['Comment', 'Designator', 'Footprint', 'LCSC']


def captured_path(root, rel):
    root = Path(root).resolve(strict=True)
    path = root / str(safe_relative(rel))
    if not path.resolve(strict=True).is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents) if p != root):
        raise ValueError('captured source path escapes root or uses symlink')
    if not path.is_file():
        raise ValueError('captured source must be a file')
    return path


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('expected SHA-256')
    return value


def _record(qualification, source, parts):
    result = dict(schema=SCHEMA, qualification=qualification, source=source,
                  parts=sorted(parts, key=lambda p: p['reference']))
    validate_bindings(result)
    return result


def validate_bindings(data):
    exact_fields(data, ('schema', 'qualification', 'source', 'parts'))
    if data['schema'] != SCHEMA or data['qualification'] not in ('selection-only', 'experimental-unqualified'):
        raise ValueError('unsupported datasheet bindings')
    if not isinstance(data['source'], dict) or not data['source']:
        raise ValueError('bindings need source identities')
    if not isinstance(data['parts'], list) or not 1 <= len(data['parts']) <= 256:
        raise ValueError('bindings need 1..256 parts')
    source = data['source']
    if data['qualification'] == 'selection-only':
        exact_fields(source, ('selection_sha256', 'catalog_manifest_sha256', 'requirements_sha256', 'selection'))
        selected = source['selection']
        if (not isinstance(selected, dict) or selected.get('schema') != 'agent-ee.selection/1' or
                identity(selected) != _hash(source['selection_sha256']) or
                selected.get('snapshot_manifest_sha256') != _hash(source['catalog_manifest_sha256']) or
                selected.get('requirements_sha256') != _hash(source['requirements_sha256'])):
            raise ValueError('canonical selection source identity mismatch')
        selections = selected.get('parts')
        if not isinstance(selections, list) or any(not isinstance(p, dict) or not isinstance(p.get('id'), str) for p in selections):
            raise ValueError('invalid canonical source selections')
        chosen = {p['id']: p.get('chosen') for p in selections}
        if len(chosen) != len(selections):
            raise ValueError('duplicate source selection ID')
    else:
        exact_fields(source, ('manifest_sha256', 'catalog_sha256', 'manifest'))
        manifest = source['manifest']
        if (not isinstance(manifest, dict) or manifest.get('schema') != RUNG1 or
                manifest.get('qualification') != 'experimental-unqualified' or
                identity(manifest) != _hash(source['manifest_sha256']) or
                not isinstance(manifest.get('parts'), dict) or not isinstance(manifest.get('selections'), dict) or
                not isinstance(manifest.get('catalog'), dict) or
                manifest['catalog'].get('sha256') != _hash(source['catalog_sha256'])):
            raise ValueError('experimental source identity mismatch')
    refs, allocated = set(), Counter()
    for part in data['parts']:
        exact_fields(part, ('reference', 'selection_id', 'part_number', 'datasheet_url', 'source'))
        ref = nonempty(part['reference'])
        if ref in refs:
            raise ValueError('duplicate reference allocation')
        refs.add(ref)
        _hash(part['selection_id'])
        if part['part_number'] is not None:
            nonempty(part['part_number'])
        if part['datasheet_url'] is not None and not isinstance(part['datasheet_url'], str):
            raise ValueError('Datasheet must be text or null')
        if data['qualification'] == 'selection-only':
            exact_fields(part['source'], ('requirement', 'chosen'))
            requirement = part['source']['requirement']
            if not isinstance(requirement, str) or requirement not in chosen or part['source']['chosen'] != chosen[requirement]:
                raise ValueError('part source differs from chosen selection')
            allocated[requirement] += 1
            row = chosen[requirement]
            if row is not None and (not isinstance(row, dict) or not isinstance(row.get('attributes'), dict)):
                raise ValueError('chosen source row needs attributes')
            expected_url = row['attributes'].get('Datasheet') if row else None
            expected_mpn = row.get('mpn') if row else None
            expected_id = identity({'selection': selected, 'requirement': requirement, 'reference': ref})
            if (part['datasheet_url'] != expected_url or part['part_number'] != expected_mpn or
                    part['selection_id'] != expected_id):
                raise ValueError('part differs from canonical source identity')
        else:
            exact_fields(part['source'], ('selection', 'properties'))
            if (ref not in manifest['parts'] or ref not in manifest['selections'] or
                    part['source']['selection'] != manifest['selections'][ref] or
                    part['source']['properties'] != manifest['parts'][ref] or
                    part['selection_id'] != manifest['selections'][ref]['selection_id'] or
                    part['datasheet_url'] is not None or part['part_number'] is not None):
                raise ValueError('part differs from experimental source identity')
    if data['qualification'] == 'selection-only':
        build = selected.get('build_quantity')
        for entry in selections:
            needed = entry.get('needed_stock')
            if (type(build) is not int or build < 1 or type(needed) is not int or needed < 1 or
                    needed % build or allocated[entry['id']] != needed // build):
                raise ValueError('bindings omit or overallocate source obligations')
    if data['qualification'] == 'experimental-unqualified' and (refs != set(manifest['parts']) or refs != set(manifest['selections'])):
        raise ValueError('experimental bindings omit source references')
    return data


def bindings_from_selection(snapshot_directory, manifest_sha256, requirements, selection, references):
    """Rederive catalog/1 selection; allocate explicit native refs per requirement."""
    from .part_selection import select_parts
    actual = select_parts(snapshot_directory, manifest_sha256, requirements)
    if actual != selection:
        raise ValueError('selection differs from captured catalog/requirements')
    required = {p['id']: p for p in requirements['parts']}
    if not isinstance(references, dict) or set(references) != set(required):
        raise ValueError('reference allocation must cover every selection requirement')
    parts = []
    for selected in actual['parts']:
        refs = references[selected['id']]
        if (not isinstance(refs, list) or len(refs) != required[selected['id']]['quantity_per_board']):
            raise ValueError('reference allocation multiplicity mismatch')
        row = selected['chosen']
        for ref in refs:
            nonempty(ref)
            parts.append(dict(reference=ref,
                selection_id=identity({'selection': actual, 'requirement': selected['id'], 'reference': ref}),
                part_number=row['mpn'] if row else None,
                datasheet_url=row['attributes'].get('Datasheet') if row else None,
                source={'requirement': selected['id'], 'chosen': row}))
    return _record('selection-only', {'selection_sha256': identity(actual),
        'catalog_manifest_sha256': manifest_sha256, 'requirements_sha256': identity(requirements),
        'selection': actual}, parts)


def bindings_from_rung1(manifest, source_root):
    """Read the exact 37d96d5 experimental shape, without reading absolute provenance paths."""
    exact_fields(manifest, ('schema', 'qualification', 'catalog', 'policy', 'parts', 'selections',
                            'assembly_exclusions', 'unknown_fields'))
    if manifest['schema'] != RUNG1 or manifest['qualification'] != 'experimental-unqualified':
        raise ValueError('unsupported experimental manifest')
    catalog = manifest['catalog']
    exact_fields(catalog, ('path', 'sha256', 'source', 'columns'))
    if catalog['columns'] not in (COLUMNS, COLUMNS_4):
        raise ValueError('unsupported experimental catalog columns')
    raw = captured_path(source_root, catalog['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != _hash(catalog['sha256']):
        raise ValueError('catalog byte hash mismatch')
    source = catalog['source']
    exact_fields(source, ('path', 'repository_path', 'read_from', 'commit', 'checkout_head', 'remote',
                         'sha256', 'bytes', 'working_tree_differs', 'working_tree_sha256', 'note'))
    if source['sha256'] != catalog['sha256'] or type(source['bytes']) is not int or source['bytes'] != len(raw):
        raise ValueError('catalog source identity mismatch')
    for key in ('path', 'repository_path', 'read_from', 'remote', 'note'):
        nonempty(source[key])
    for key in ('commit', 'checkout_head'):
        if not isinstance(source[key], str) or not re.fullmatch('[0-9a-f]{40}', source[key]):
            raise ValueError('catalog source needs Git commit identity')
    if type(source['working_tree_differs']) is not bool:
        raise ValueError('invalid working tree observation')
    if source['working_tree_sha256'] is not None:
        _hash(source['working_tree_sha256'])
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8')))
    raw_rows = list(reader)
    expected_cols = catalog['columns']
    if (reader.fieldnames != expected_cols or not raw_rows or
            any(set(r) != set(expected_cols) or None in r.values() for r in raw_rows)):
        raise ValueError('malformed experimental catalog rows')
    if expected_cols == COLUMNS_4:
        from .generate import StageFailure, read_catalog
        try:
            rows = read_catalog(raw)
        except StageFailure as exc:
            raise ValueError(str(exc)) from exc
    else:
        rows = raw_rows
    policy = manifest['policy']
    exact_fields(policy, ('value_match', 'families', 'rank', 'not_assembled'))
    # Retain policy as provenance only; this adapter does not qualify its ranking or stock semantics.
    for key in ('value_match', 'not_assembled'):
        nonempty(policy[key])
    exact_fields(policy['families'], ('Device:R', 'Device:C'))
    if not isinstance(policy['rank'], list) or len(policy['rank']) != 4:
        raise ValueError('invalid experimental rank declaration')
    for item in [*policy['rank'], *policy['families'].values()]:
        nonempty(item)
    exact_fields(manifest['unknown_fields'], ('MPN', 'Manufacturer', 'LCSC'))
    for item in manifest['unknown_fields'].values():
        nonempty(item)
    selections, props = manifest['selections'], manifest['parts']
    if not isinstance(selections, dict) or not selections or not isinstance(props, dict) or set(selections) != set(props):
        raise ValueError('parts/selections reference mismatch')
    exclusions = manifest['assembly_exclusions']
    if not isinstance(exclusions, list):
        raise ValueError('assembly exclusions must be a list')
    excluded = {}
    for exclusion in exclusions:
        exact_fields(exclusion, ('ref', 'rule', 'reason'))
        if exclusion['ref'] in excluded or exclusion['ref'] not in selections or exclusion['rule'] != 'JLCPCB_Assembly=not_assembled':
            raise ValueError('invalid assembly exclusion')
        nonempty(exclusion['reason'])
        excluded[exclusion['ref']] = exclusion['reason']
    parts = []
    for ref, selection in selections.items():
        nonempty(ref)
        if not isinstance(selection, dict):
            raise ValueError('selection must be an object')
        chosen = selection.get('chosen')
        keys = ('lib_id', 'value', 'chosen', 'rejected', 'footprint_file', 'selection_id')
        exact_fields(selection, (*keys, 'catalog_rows', 'reason') if chosen is None else (*keys, 'default_package', 'package_note'))
        nonempty(selection['lib_id'])
        nonempty(selection['value'])
        if not isinstance(selection['rejected'], list):
            raise ValueError('rejected rows must be a list')
        for rejected in selection['rejected']:
            if not isinstance(rejected, dict):
                raise ValueError('rejected row must be an object')
            base = ('catalog_line', 'footprint', 'lcsc', 'quantity', 'reason')
            exact_fields(rejected, (*base, 'package', 'rank_key') if 'rank_key' in rejected else base)
            rejected_line = rejected['catalog_line']
            if type(rejected_line) is not int or not 2 <= rejected_line <= len(rows) + 1:
                raise ValueError('rejected line outside captured rows')
            rejected_row = rows[rejected_line - 2]
            if (rejected_row['Comment'] != selection['value'] or
                    any(rejected_row[k] != rejected[v] for k, v in
                        [('Footprint', 'footprint'), ('LCSC Part #', 'lcsc'), ('Quantity', 'quantity')])):
                raise ValueError('rejected row identity mismatch')
            nonempty(rejected['reason'])
            if 'rank_key' in rejected:
                nonempty(rejected['package'])
                if (not isinstance(rejected['rank_key'], list) or len(rejected['rank_key']) != 4 or
                        any(type(v) is not int for v in rejected['rank_key'])):
                    raise ValueError('invalid rejected rank key')
        footprint = selection['footprint_file']
        exact_fields(footprint, ('path', 'sha256', 'pads'))
        nonempty(footprint['path'])
        _hash(footprint['sha256'])
        if not isinstance(footprint['pads'], list) or not footprint['pads'] or len(set(footprint['pads'])) != len(footprint['pads']):
            raise ValueError('invalid declared footprint pads')
        for pad in footprint['pads']:
            nonempty(pad)
        fields = props[ref]
        extra = ('JLCPCB_Assembly',) if chosen is None else ('LCSC',)
        exact_fields(fields, ('Footprint', 'Datasheet', 'datasheet_obligation', *extra))
        if fields['Datasheet'] is not None or fields['datasheet_obligation'] != 'missing:catalog_has_no_datasheet_column':
            raise ValueError('experimental catalog cannot carry Datasheet URLs')
        nonempty(fields['Footprint'])
        line = None
        if chosen is None:
            if (selection['catalog_rows'] != [] or selection['rejected'] != [] or
                    ref not in excluded or selection['reason'] != excluded[ref] or
                    fields['JLCPCB_Assembly'] != 'not_assembled' or
                    any(r['Comment'] == selection['value'] for r in rows)):
                raise ValueError('unselected part lacks exact exclusion or hides a catalog row')
        else:
            if ref in excluded:
                raise ValueError('chosen part cannot be assembly excluded')
            exact_fields(chosen, ('catalog_line', 'footprint', 'lcsc', 'quantity', 'package', 'rank_key'))
            line = chosen['catalog_line']
            if type(line) is not int or not 2 <= line <= len(rows) + 1:
                raise ValueError('chosen catalog line outside captured rows')
            row = rows[line - 2]
            if (row['Comment'] != selection['value'] or row['Footprint'] != chosen['footprint'] or
                    row['LCSC Part #'] != chosen['lcsc'] or row['Quantity'] != chosen['quantity'] or
                    fields['Footprint'] != chosen['footprint'] or fields['LCSC'] != chosen['lcsc']):
                raise ValueError('chosen row/part identity mismatch')
            family = {'Device:R': 'Resistor_SMD:R_', 'Device:C': 'Capacitor_SMD:C_'}.get(selection['lib_id'])
            if family is None or not re.fullmatch(re.escape(family) + r'\d{4,5}_\d{4,5}Metric', chosen['footprint']):
                raise ValueError('chosen footprint/symbol family mismatch')
            if (chosen['package'] != chosen['footprint'].split('_')[2] or
                    not re.fullmatch(r'C\d+', chosen['lcsc']) or not chosen['quantity'].isdigit() or
                    not isinstance(chosen['rank_key'], list) or len(chosen['rank_key']) != 4 or
                    any(type(v) is not int for v in chosen['rank_key'])):
                raise ValueError('invalid chosen metadata')
            nonempty(selection['default_package'])
            nonempty(selection['package_note'])
        expected = identity(dict(catalog_sha256=catalog['sha256'], catalog_line=line,
                                lib_id=selection['lib_id'], value=selection['value'], reference=ref))
        if selection['selection_id'] != expected:
            raise ValueError('selection ID mismatch')
        parts.append(dict(reference=ref, selection_id=expected, part_number=None, datasheet_url=None,
                          source={'selection': selection, 'properties': fields}))
    return _record('experimental-unqualified', {'manifest_sha256': identity(manifest),
                    'catalog_sha256': catalog['sha256'], 'manifest': manifest}, parts)


def validate_native_bindings(native_xml, bindings):
    """Compare the complete native set/URLs without dropping assembly exclusions."""
    from .native_netlist import read_native_inventory
    validate_bindings(bindings)
    native = read_native_inventory(native_xml)['components']
    expected = {p['reference']: p for p in bindings['parts']}
    if set(native) != set(expected):
        raise ValueError('native/binding reference sets differ')
    for ref, part in expected.items():
        if native[ref]['datasheet'] != part['datasheet_url']:
            raise ValueError(f'{ref}: native Datasheet differs from selection')
    return native

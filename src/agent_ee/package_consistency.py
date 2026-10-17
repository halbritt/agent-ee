"""Name-level KiCad footprint versus catalog package consistency (AGENTEE-30).

Footprint and LCSC fields come from the shared strict native XML reader, package
and category from the pinned catalog read inside the networkless sandbox, and
their relation only from a captured explicit table. Unmapped names are unknown.
"""
import re
import sys
from pathlib import Path
from xml.etree.ElementTree import ParseError

from .candidate import safe_relative
from .catalog import CATEGORIES, strict_json
from .hardware_interface import exact_keys, text
from .storage import digest, read_json, write_json

MAX_TABLE_BYTES = 8 * 1024 * 1024
MAX_TABLE_ENTRIES = 100_000
FAMILIES = {'chip-imperial': 'chip', 'named-sot23': 'sot-23', 'gull-wing': 'gull-wing-dual',
            'body-size': ('quad-flat-no-lead', 'quad-flat')}
LCSC = re.compile(r'C[1-9][0-9]*')
LOOKUP_SCHEMA = 'agent-ee.package-catalog-lookup/1'


def load_table(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_TABLE_BYTES + 1)
    if len(raw) > MAX_TABLE_BYTES:
        raise ValueError('package map exceeds byte limit')
    return validate_table(strict_json(raw.decode('utf-8')))


def _package(value, rule):
    exact_keys(value, 'family pins body')
    families = FAMILIES[rule]
    if value['family'] not in (families if isinstance(families, tuple) else (families,)):
        raise ValueError('package family does not belong to its rule')
    if type(value['pins']) is not int or not 2 <= value['pins'] <= 1024:
        raise ValueError('package pin count must be an explicit integer')
    body = value['body']
    if rule == 'named-sot23':
        if body is not None:
            raise ValueError('named outline has no separate body field')
    elif rule == 'chip-imperial':
        if value['pins'] != 2 or not isinstance(body, str) or not re.fullmatch(r'\d{4}', body):
            raise ValueError('chip package needs two terminals and an imperial code')
    elif not isinstance(body, str) or not re.fullmatch(r'\d+(?:\.\d+)?x\d+(?:\.\d+)?', body):
        raise ValueError('package needs an explicit WxL body')


def validate_table(table):
    """Explicit entries only; every footprint/package names its rule and descriptor."""
    exact_keys(table, 'schema version derivation rules footprints packages')
    if table['schema'] != 'agent-ee.package-map/1':
        raise ValueError('unsupported package map schema')
    text(table['version'])
    if not isinstance(table['derivation'], dict) or not table['derivation']:
        raise ValueError('package map requires derivation provenance')
    rules = table['rules']
    if not isinstance(rules, dict) or not rules or set(rules) - FAMILIES.keys():
        raise ValueError('package map rules must be supported named rules')
    for description in rules.values():
        text(description)
    footprints, packages = table['footprints'], table['packages']
    if (not isinstance(footprints, dict) or not isinstance(packages, dict) or not footprints or not packages
            or len(footprints) + len(packages) > MAX_TABLE_ENTRIES):
        raise ValueError('package map requires bounded footprint and package entries')
    for footprint, entry in footprints.items():
        library, _, name = text(footprint).partition(':')
        if not library or not name or ':' in name or footprint != footprint.strip():
            raise ValueError('footprint entry requires an exact library:name ID')
        exact_keys(entry, 'rule classes package')
        if entry['rule'] not in rules:
            raise ValueError('footprint names an undeclared rule')
        classes = entry['classes']
        if classes is not None:
            if (not isinstance(classes, list) or not classes or len(set(classes)) != len(classes)
                    or any(c not in CATEGORIES for c in classes)):
                raise ValueError('footprint classes must be known catalog categories')
        elif entry['rule'] == 'chip-imperial':
            raise ValueError('chip footprint requires a component class')
        _package(entry['package'], entry['rule'])
    for name, entry in packages.items():
        if text(name) != name.strip():
            raise ValueError('package entry requires an exact trimmed name')
        exact_keys(entry, 'rule package')
        if entry['rule'] not in rules:
            raise ValueError('package names an undeclared rule')
        _package(entry['package'], entry['rule'])
    return table


def capability(params, scope, files, inputs, check_id):
    exact_keys(params, 'netlist_check catalog catalog_manifest_sha256 mapping lcsc_field class_mismatch')
    catalog = str(safe_relative(params['catalog']))
    mapping = str(safe_relative(params['mapping']))
    manifest, database = f'{catalog}/manifest.json', f'{catalog}/catalog.sqlite3'
    if catalog != params['catalog'] or mapping != params['mapping'] or inputs is None:
        raise ValueError('catalog and mapping must be exact captured paths')
    if any(rel not in files for rel in (manifest, database, mapping)):
        raise ValueError('catalog snapshot files and mapping table must be captured')
    if (not isinstance(params['catalog_manifest_sha256'], str)
            or files[manifest] != params['catalog_manifest_sha256']):
        raise ValueError('captured catalog manifest differs from the pinned hash')
    if not isinstance(params['lcsc_field'], str) or not params['lcsc_field'].strip() or params['lcsc_field'] in (
            'dnp', 'exclude_from_board', 'exclude_from_bom'):
        raise ValueError('lcsc_field must name an exact native component field')
    if params['class_mismatch'] not in ('defect', 'info'):
        raise ValueError('class_mismatch policy must be defect or info')
    load_table(Path(inputs) / mapping)
    specs = scope['checks']
    ids = [s['id'] for s in specs]
    prior = next((s for s in specs if s['id'] == params['netlist_check']), None)
    if prior is None or prior['kind'] != 'netlist' or ids.index(prior['id']) >= ids.index(check_id):
        raise ValueError('unresolved/forward/wrong-kind native netlist prerequisite')
    mapped = [r for r in scope.get('baseline', {}).get('requirements', []) if check_id in r['checks']]
    if len(mapped) != 1:
        raise ValueError('package_consistency requires exactly one requirement per check')
    predicates = ['package-consistency:footprint-package']
    if params['class_mismatch'] == 'defect':
        predicates.append('package-consistency:component-class')
    return {'immutable_inputs': sorted({manifest, database, mapping}), 'predicates': predicates}


def _difference(footprint, package):
    keys = [key for key in ('family', 'pins', 'body') if footprint[key] != package[key]]
    if 'pins' in keys:
        return keys, 'pin count differs; pin-count aliases are never crossed'
    return keys, 'package ' + ' and '.join(keys) + ' differ'


def evaluate(graph, rows, table, lcsc_field, class_policy):
    """Pure per-reference comparison; incomplete coverage never becomes pass."""
    records = []
    for ref, comp in sorted(graph['components'].items()):
        lcsc = comp['properties'].get(lcsc_field)
        record = {'reference': ref, 'sheet': comp['sheet'], 'uuid': comp['uuid'], 'symbol': comp['lib_id'],
                  'footprint': comp['footprint'], 'lcsc': lcsc, 'populated': comp['populated'],
                  'on_board': comp['on_board'], 'in_bom': comp['in_bom'], 'status': 'unknown',
                  'rule': None, 'locator': comp['locator']}
        records.append(record)
        native = [flag for flag, active in (('dnp', not comp['populated']),
            ('exclude_from_board', not comp['on_board']), ('exclude_from_bom', not comp['in_bom'])) if active]
        if native:
            # Only valueless native flags exclude; authored fields stay data.
            record.update(status='excluded', reason='native ' + ', '.join(native))
            continue
        if not lcsc:
            record['reason'] = f'no {lcsc_field} field value'
            continue
        if not LCSC.fullmatch(lcsc):
            record['reason'] = 'field value is not an exact LCSC identity'
            continue
        if not comp['footprint']:
            record['reason'] = 'no assigned footprint'
            continue
        row = rows.get(lcsc)
        if row is None:
            record['reason'] = 'LCSC part absent from catalog snapshot'
            continue
        record['catalog'] = row
        footprint = table['footprints'].get(comp['footprint'])
        package = table['packages'].get(row['package'])
        if footprint is None or package is None:
            record['reason'] = ('footprint' if footprint is None else 'catalog package') + ' outside the mapping table'
            record['rule'] = {'footprint': footprint and footprint['rule'], 'package': package and package['rule']}
            continue
        record['rule'] = {'footprint': footprint['rule'], 'package': package['rule']}
        record['footprint_package'], record['catalog_package'] = footprint['package'], package['package']
        if footprint['package'] != package['package']:
            record['differences'], record['reason'] = _difference(footprint['package'], package['package'])
            record['status'] = 'mismatch'
        elif footprint['classes'] is not None and row['category'] not in footprint['classes']:
            record.update(status='class_mismatch', severity=class_policy, footprint_classes=footprint['classes'],
                          reason=f"catalog category {row['category']} is not a footprint class")
        else:
            record['status'] = 'match'
    eligible = [r for r in records if r['status'] != 'excluded']
    complete = bool(eligible) and all(r['status'] != 'unknown' for r in eligible)
    defects = [r for r in eligible if r['status'] == 'mismatch' or
               (r['status'] == 'class_mismatch' and class_policy == 'defect')]
    advisories = [r['reference'] for r in eligible if r['status'] == 'class_mismatch' and class_policy == 'info']
    counts = {status: sum(r['status'] == status for r in records)
              for status in ('match', 'mismatch', 'class_mismatch', 'unknown', 'excluded')}
    return {'records': records, 'counts': counts, 'advisories': advisories, 'complete': complete,
            'execution': 'ok' if complete else 'unsupported',
            'outcome': ('defect' if defects else 'pass') if complete else 'unknown'}


def lookup(snapshot, manifest_sha256, request, output):
    """Sandbox entry point: canonical catalog reader, then only requested rows."""
    from .catalog import load_catalog
    wanted = read_json(request)
    if not isinstance(wanted, list) or any(not isinstance(v, str) or not LCSC.fullmatch(v) for v in wanted):
        raise ValueError('lookup request must be a list of LCSC identities')
    loaded = load_catalog(snapshot, manifest_sha256)
    by_lcsc = {row['lcsc']: row for row in loaded['rows']}
    rows = {}
    for lcsc in sorted(set(wanted)):
        row = by_lcsc.get(lcsc)
        rows[lcsc] = None if row is None else {key: row[key] for key in
            ('lcsc', 'mfr', 'mpn', 'package', 'category', 'provenance')}
    manifest = loaded['manifest']
    write_json(output, {'schema': LOOKUP_SCHEMA, 'snapshot_manifest_sha256': manifest_sha256,
        'snapshot_sha256': manifest['database']['sha256'], 'snapshot_date': manifest['snapshot_date'], 'rows': rows})


def _lookup_rows(path, wanted, manifest_sha256):
    """Accept only the requested identities from the sandboxed reader."""
    record = read_json(path)
    exact_keys(record, 'schema snapshot_manifest_sha256 snapshot_sha256 snapshot_date rows')
    if record['schema'] != LOOKUP_SCHEMA or record['snapshot_manifest_sha256'] != manifest_sha256:
        raise ValueError('catalog lookup identity differs from the pinned snapshot')
    if not isinstance(record['rows'], dict) or set(record['rows']) != set(wanted):
        raise ValueError('catalog lookup does not answer exactly the requested identities')
    for lcsc, row in record['rows'].items():
        if row is None:
            continue
        exact_keys(row, 'lcsc mfr mpn package category provenance')
        if row['lcsc'] != lcsc or row['category'] not in CATEGORIES:
            raise ValueError('catalog lookup row is inconsistent')
        text(row['package'])
    return record


def check_package_consistency(ctx):
    from .checks import CheckError, parse_xml_netlist
    from .datasheet_facts import prerequisite
    from .native_netlist import read_native_inventory
    from .netlist_parity import Unsupported
    try:
        capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
        table = load_table(ctx.inputs / ctx.params['mapping'])
    except (ValueError, TypeError, KeyError, OSError, UnicodeDecodeError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    try:
        xml = prerequisite(ctx, ctx.params['netlist_check'], 'netlist', 'design.xml')
        connectivity = prerequisite(ctx, ctx.params['netlist_check'], 'netlist', 'connectivity.json')
        graph = read_native_inventory(xml)
        if read_json(connectivity) != parse_xml_netlist(xml):
            raise ValueError('captured connectivity projection differs from strict native XML')
        producer = read_json(ctx.run_dir / 'checks' / f"{ctx.params['netlist_check']}.json")
        if len(graph['sheets']) != producer['details']['sheet_instances']:
            raise ValueError('native XML hierarchy differs from producer coverage')
    except CheckError as exc:
        raise CheckError('failed', str(exc)) from exc
    except Unsupported as exc:
        raise CheckError('unsupported', str(exc)) from exc
    except (OSError, ValueError, TypeError, KeyError, ParseError) as exc:
        raise CheckError('failed', str(exc)) from exc
    field = ctx.params['lcsc_field']
    wanted = sorted({c['properties'].get(field) for c in graph['components'].values()
                     if LCSC.fullmatch(c['properties'].get(field) or '')})
    write_json(ctx.work / 'request.json', wanted)
    ctx.evidence('request.json')
    sha = ctx.params['catalog_manifest_sha256']
    ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.package_consistency',
                 f"/candidate/{ctx.params['catalog']}", sha, '/work/request.json', '/work/catalog-rows.json'],
                timeout=600)
    ctx.evidence('catalog-rows.json')
    try:
        lookup_record = _lookup_rows(ctx.work / 'catalog-rows.json', wanted, sha)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise CheckError('malformed', f'catalog lookup: {exc}') from exc
    report = evaluate(graph, lookup_record['rows'], table, field, ctx.params['class_mismatch'])
    report.update(schema='agent-ee.package-consistency-result/1', candidate_id=ctx.manifest['candidate_id'],
        netlist_check=ctx.params['netlist_check'], native_xml_sha256=digest(xml),
        connectivity_sha256=digest(connectivity), lcsc_field=field, class_mismatch=ctx.params['class_mismatch'],
        mapping={'path': ctx.params['mapping'], 'sha256': digest(ctx.inputs / ctx.params['mapping']),
                 'version': table['version']},
        catalog={key: lookup_record[key] for key in ('snapshot_manifest_sha256', 'snapshot_sha256', 'snapshot_date')},
        catalog_lookup_sha256=digest(ctx.work / 'catalog-rows.json'))
    write_json(ctx.work / 'packages.json', report)
    ctx.evidence('packages.json')
    ctx.result['details'].update(report)
    counts = report['counts']
    ctx.result.update(execution=report['execution'], outcome=report['outcome'],
        summary=(f"Footprint/package names: {report['outcome']}; {counts['match']} match, {counts['mismatch']} mismatch, "
                 f"{counts['class_mismatch']} class mismatch, {counts['unknown']} unknown, {counts['excluded']} excluded"))
    ctx.result['limitations'].append('Name-level comparison through the captured mapping table only; pitch, exposed pad '
        'and land-pattern geometry are not compared. Catalog package strings are claims as of the snapshot date. '
        'Unmapped names, absent fields or parts are unknown. Only native dnp/exclude flags exclude a component.')


if __name__ == '__main__':
    try:
        lookup(*sys.argv[1:])
    except Exception as exc:  # The coordinator records the nonzero exit as missing coverage.
        print(f'package catalog lookup failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1)

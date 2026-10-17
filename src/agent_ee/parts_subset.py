"""One offline predicate: declared native part membership and bounded multiplicity."""
import argparse
import copy
from xml.etree.ElementTree import ParseError
from pathlib import Path

from .parts_manifest import (IDENTITY_FIELDS, LIMIT, PREDICATE, dependency_closure,
                             load_manifest, qualify_manifest, read_captured)
from .storage import digest, identity, write_json


def compare_inventory(document, graph, virtual, missing_provenance=()):
    """Compare a validated manifest with the shared strict native inventory.

    Callers must qualify captured dependencies first. ``virtual`` must come from
    native_binding.virtual_symbols for these same captured sheets and inventory.
    This diagnostic function grants no owner approval or sealed witness itself.
    """
    projection = {k: graph[k] for k in ('components', 'sheets', 'exporter', 'library_parts')}
    report = dict(schema='agent-ee.parts-subset/1', predicate=PREDICATE, execution='ok',
                  outcome='unknown', coverage='complete', matches=[], exclusions=[], findings=[],
                  native_sheets=list(graph['sheets']), native_inventory_sha256=identity(projection), native_inventory=projection,
                  evaluated=0, limitation=LIMIT)
    unknown = bool(missing_provenance)
    report['findings'].extend(dict(code='missing_provenance', **item) for item in missing_provenance)
    for record in virtual:
        if 'problem' in record or not record.get('embedded_power'):
            unknown = True
            report['findings'].append(dict(code='unresolved_exporter_omission', occurrence=record))
        else:
            report['exclusions'].append(dict(reason='qualified_native_virtual_power', occurrence=record))
    declared = {(s['sheet'], s['reference']): p for p in document['parts'] for s in p['instances']}
    observed = {(c['sheet'], ref): c for ref, c in graph['components'].items()}
    for sheet, ref in sorted(declared.keys() | observed.keys()):
        part, comp = declared.get((sheet, ref)), observed.get((sheet, ref))
        row = dict(sheet=sheet, reference=ref, part_id=part['id'] if part else None,
                   status='pass', findings=[])
        report['matches'].append(row)
        if comp is not None:
            row.update(uuid=comp['uuid'], native_locator=comp['locator'],
                       populated=comp['populated'], on_board=comp['on_board'], in_bom=comp['in_bom'])
        if part is not None:
            row['declaration_locator'] = 'parts[id=' + part['id'] + '].instances'
        if comp is None:
            if part['required']:
                row['findings'].append(dict(code='required_absent'))
            else:
                row['status'] = 'optional_absent'
        elif not comp['populated']:
            if part and part['required']:
                row['findings'].append(dict(code='required_unpopulated'))
            else:
                row['status'] = 'excluded'
                report['exclusions'].append(dict(sheet=sheet, reference=ref, reason='native_dnp'))
        elif part is None:
            row['findings'].append(dict(code='unexpected_populated_instance'))
        else:
            report['evaluated'] += 1
            if not comp['on_board']:
                row['findings'].append(dict(code='declared_physical_instance_off_board'))
            for field in IDENTITY_FIELDS:
                actual = comp[field] if field in ('lib_id', 'footprint') else comp['properties'].get(
                    document['property_fields'][field])
                if actual is None or actual == '':
                    unknown = True
                    row['findings'].append(dict(code='missing_native_identity', field=field,
                                                declared=part[field], native=actual))
                elif actual != part[field]:
                    row['findings'].append(dict(code=field + '_mismatch', field=field,
                                                declared=part[field], native=actual))
        if row['findings']:
            row['status'] = ('unknown' if any(f['code'] == 'missing_native_identity'
                                            for f in row['findings']) else 'defect')
    defects = any(r['status'] == 'defect' for r in report['matches'])
    # Missing required instances are a complete negative evaluation, including
    # an empty native inventory. Optional-only empty scope cannot pass vacuously.
    if not report['evaluated'] and not defects:
        unknown = True
        report['findings'].append(dict(code='empty_coverage'))
    report.update(coverage='missing' if unknown else 'complete',
                  execution='unsupported' if unknown else 'ok',
                  outcome='unknown' if unknown else 'defect' if defects else 'pass')
    report['counts'] = dict(native=len(observed), capacity=len(declared), evaluated=report['evaluated'],
        optional_absent=sum(r['status'] == 'optional_absent' for r in report['matches']),
        excluded=len(report['exclusions']), unknown=sum(r['status'] == 'unknown' for r in report['matches']),
        defects=sum(r['status'] == 'defect' for r in report['matches']))
    return copy.deepcopy(report)


def evaluate(root, manifest_path, native_xml, schematic_paths):
    """Replay captured provenance and native hierarchy; retain diagnostic failures."""
    from .native_netlist import read_native_inventory
    from .native_binding import virtual_symbols
    report = dict(schema='agent-ee.parts-subset/1', predicate=PREDICATE, execution='malformed',
                  outcome='unknown', coverage='missing', findings=[], matches=[], limitation=LIMIT)
    try:
        document = load_manifest(root, manifest_path)
        report['manifest_sha256'] = digest(Path(root) / manifest_path)
        report['native_xml_sha256'] = digest(native_xml)
        qualified = qualify_manifest(document, root)
        graph = read_native_inventory(native_xml)
        sheets = {p: read_captured(root, p) for p in schematic_paths}
        virtual = virtual_symbols(sheets, schematic_paths[0], graph)
        compared = compare_inventory(document, graph, virtual, qualified['missing_provenance'])
        report.update(compared, qualification=qualified)
    except (OSError, ValueError, KeyError, TypeError, ParseError) as exc:
        report['findings'].append(dict(code='unavailable_captured_evidence', message=str(exc)))
    return report


def capability(params, files, inputs, scope, check_id):
    """Root integration hook: predicate plus complete immutable source closure."""
    from .catalog import exact_fields
    exact_fields(params, ('manifest', 'netlist_check', 'requirement_id'))
    specs = scope.get('checks', [])
    ids = [s['id'] for s in specs]
    producer = next((s for s in specs if s['id'] == params['netlist_check']), None)
    if (producer is None or producer['kind'] != 'netlist' or check_id not in ids or
            ids.index(producer['id']) >= ids.index(check_id)):
        raise ValueError('ordered native netlist prerequisite required')
    document = load_manifest(inputs, params['manifest'])
    closure = dependency_closure(document, inputs)
    closure[params['manifest']] = digest(Path(inputs) / params['manifest'])
    mapped = [r for r in scope.get('baseline', {}).get('requirements', []) if check_id in r['checks']]
    if (len(mapped) != 1 or mapped[0]['id'] != params['requirement_id'] or
            mapped[0]['source'] != {k: document['declaration_source'][k] for k in ('path', 'locator')}):
        raise ValueError('parts membership must map one requirement at its captured declaration source')
    mutable = scope.get('mutable_inputs', [])
    for path in closure:
        if path not in files:
            raise ValueError('uncaptured parts dependency: ' + path)
        if any(path == m or path.startswith(m.rstrip('/') + '/') for m in mutable):
            raise ValueError('parts dependency cannot be mutable: ' + path)
    return dict(immutable_inputs=sorted(closure), predicates=[PREDICATE])


def check_parts_subset(ctx):
    """Existing coordinator/executor adapter; root registers this explicitly."""
    from .checks import CheckError, verified_hierarchy
    from .datasheet_facts import prerequisite
    from .storage import read_json
    ctx.result['details']['evaluated_predicates'] = []
    try:
        capability(ctx.params, ctx.manifest['files'], ctx.inputs, ctx.scope, ctx.cid)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    cid = ctx.params['netlist_check']
    xml = prerequisite(ctx, cid, 'netlist', 'design.xml')
    producer = read_json(ctx.run_dir / 'checks' / (cid + '.json'))
    if producer.get('outcome') != 'info':
        raise CheckError('unsupported', 'native netlist prerequisite did not establish inventory')
    sheets, expected = verified_hierarchy(ctx)
    if producer.get('details', {}).get('sheet_instances') != expected:
        raise CheckError('malformed', 'native producer hierarchy coverage differs')
    try:
        ctx.execute(['/usr/bin/python3', '-m', 'agent_ee.parts_subset',
                     '/candidate', ctx.params['manifest'], '/evidence/netlist/design.xml',
                     '--schematic', *sheets, '--out', '/work/parts-subset.json'], 120,
                    extra_ro=[(xml, '/evidence/netlist/design.xml')])
    finally:
        ctx.evidence('parts-subset.json')
    report = ctx.load_json('parts-subset.json', 'parts subset')
    if (report.get('native_xml_sha256') != digest(xml) or
            report.get('manifest_sha256') != digest(ctx.inputs / ctx.params['manifest'])):
        raise CheckError('malformed', 'parts report source identity differs')
    ctx.result['details'].update(report=report, netlist_check=cid, native_xml_sha256=digest(xml),
                                 candidate_id=ctx.manifest['candidate_id'])
    ctx.result['limitations'].append(LIMIT)
    if report['execution'] != 'ok':
        raise CheckError('unsupported', 'parts membership coverage unavailable; see parts-subset.json')
    if len(report['native_sheets']) != expected:
        raise CheckError('malformed', 'parts report native hierarchy differs')
    ctx.result.update(execution='ok', outcome=report['outcome'],
                      summary='Declared native parts membership and multiplicity: ' + report['outcome'])
    ctx.result['details']['evaluated_predicates'] = [PREDICATE]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('captures')
    parser.add_argument('manifest')
    parser.add_argument('native_xml')
    parser.add_argument('--schematic', nargs='+', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    write_json(Path(args.out), evaluate(args.captures, args.manifest, args.native_xml, args.schematic))


if __name__ == '__main__':
    main()

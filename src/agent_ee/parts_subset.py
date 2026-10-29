"""One offline predicate: declared native part membership and bounded multiplicity."""

from .check_registry import register as _register_check
from . import check_prerequisites as _requires
import argparse
import copy
import json
import uuid
from xml.etree.ElementTree import ParseError
from pathlib import Path

from .parts_manifest import (IDENTITY_FIELDS, LIMIT, PREDICATE, dependency_closure,
                             load_manifest, qualify_manifest, read_captured)
from .storage import digest, identity, write_json
from .probe_features import is_probe, native_component
from .copper_features import is_copper


def reference_set(value):
    """Canonical exact native occurrence/reference/all-placed-unit selector."""
    from .catalog import exact_fields
    if not isinstance(value, list) or not value or len(value) > 10000:
        raise ValueError('reference_set requires a nonempty bounded list')
    keys, units = [], set()
    for row in value:
        exact_fields(row, ('sheet', 'reference', 'uuids'))
        path, ref, ids = row['sheet'], row['reference'], row['uuids']
        if (not isinstance(path, str) or not path.startswith('/') or not path.endswith('/')
                or (path != '/' and any(str(uuid.UUID(x)) != x for x in path[1:-1].split('/')))):
            raise ValueError('reference_set sheet must be a canonical native UUID occurrence path')
        if not isinstance(ref, str) or not ref or ref.strip() != ref or ref.startswith('#') or '\x00' in ref:
            raise ValueError('reference_set requires a physical reference')
        if (not isinstance(ids, list) or not ids or ids != sorted(set(ids))
                or any(not isinstance(x, str) or str(uuid.UUID(x)) != x for x in ids)):
            raise ValueError('reference_set requires sorted unique canonical unit UUIDs')
        if any((path, x) in units for x in ids):
            raise ValueError('reference_set unit UUID is aliased')
        units.update((path, x) for x in ids);keys.append((path, ref))
    if keys != sorted(set(keys)):
        raise ValueError('reference_set occurrences/references must be sorted and unique')
    return copy.deepcopy(value)


def report_scope_matches(report, expected):
    """Scope claims cannot replace the report's actual selected membership rows."""
    if expected is None:
        return 'reference_set' not in report and 'reference_set_sha256' not in report
    selected = reference_set(expected)
    if report.get('reference_set') != selected or report.get('reference_set_sha256') != identity(selected):
        return False
    rows=report.get('matches',[])
    actual={(r.get('sheet'),r.get('reference')):r for r in rows}
    if len(rows) != len(actual) or set(actual) != {(r['sheet'],r['reference']) for r in selected}:
        return False
    return all('uuid' not in actual[(r['sheet'],r['reference'])] or
        sorted(actual[(r['sheet'],r['reference'])]['uuid'].split()) == r['uuids'] for r in selected)


def _declared_scope(document, selected):
    expected = {(r['sheet'], r['reference']) for r in selected}
    declared = {(r['sheet'], r['reference']) for p in document['parts'] for r in p['instances']}
    if declared != expected:
        raise ValueError('manifest must declare exactly the reference_set occurrences/references')


def compare_inventory(document, graph, virtual, missing_provenance=(), probes=None, copper=None, reference_scope=None):
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
    total_native = len(observed)
    selected = reference_set(reference_scope) if reference_scope is not None else None
    if selected is not None:
        _declared_scope(document, selected)
        scope = {(r['sheet'], r['reference']): r['uuids'] for r in selected}
        report.update(reference_set=selected, reference_set_sha256=identity(selected),
                      outside_reference_set=[dict(sheet=k[0], reference=k[1], uuid=c['uuid'])
                                             for k,c in sorted(observed.items()) if k not in scope])
        observed = {k:c for k,c in observed.items() if k in scope}
    for sheet, ref in sorted(declared.keys() | observed.keys()):
        part, comp = declared.get((sheet, ref)), observed.get((sheet, ref))
        row = dict(sheet=sheet, reference=ref, part_id=part['id'] if part else None,
                   status='pass', findings=[])
        report['matches'].append(row)
        if comp is not None:
            row.update(uuid=comp['uuid'], native_locator=comp['locator'],
                       populated=comp['populated'], on_board=comp['on_board'], in_bom=comp['in_bom'])
        if comp is not None and selected is not None and sorted(comp['uuid'].split()) != scope[(sheet, ref)]:
            row['findings'].append(dict(code='selected_unit_uuid_mismatch', declared=scope[(sheet, ref)], native=sorted(comp['uuid'].split())))
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
            if is_copper(part):
                from .copper_features import native_component as check_copper
                try:
                    if copper is None or ref not in copper:
                        raise ValueError(ref + ': captured copper qualification required')
                    check_copper(copper[ref], comp, graph)
                except ValueError as exc:
                    row['findings'].append(dict(code='copper_feature_mismatch', message=str(exc)))
                if row['findings']:
                    row['status'] = 'defect'
                continue
            if is_probe(part):
                try:
                    if probes is None or ref not in probes:
                        raise ValueError(ref + ': probe captured qualification required')
                    native_component(probes[ref], comp, graph)
                except ValueError as exc:
                    row['findings'].append(dict(code='probe_feature_mismatch', message=str(exc)))
                if row['findings']:
                    row['status'] = 'defect'
                continue
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
    report['counts'] = dict(native=total_native, capacity=len(declared), evaluated=report['evaluated'],
        optional_absent=sum(r['status'] == 'optional_absent' for r in report['matches']),
        excluded=len(report['exclusions']), unknown=sum(r['status'] == 'unknown' for r in report['matches']),
        defects=sum(r['status'] == 'defect' for r in report['matches']))
    if selected is not None:
        report['counts'].update(selected_native=len(observed), outside_scope=total_native-len(observed))
    return copy.deepcopy(report)


def evaluate(root, manifest_path, native_xml, schematic_paths, reference_scope=None):
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
        from .probe_features import declarations, validate_schematic
        probes = declarations(document, root)
        seen = set()
        for data in sheets.values():
            seen.update(validate_schematic(data, probes))
        if seen != set(probes):
            raise ValueError('missing probe feature(s): ' + ', '.join(sorted(set(probes) - seen)))
        from . import copper_features
        copper = copper_features.declarations(document, root)
        copper_seen = set()
        for data in sheets.values():
            seen_here = copper_features.validate_schematic(data, copper)
            if copper_seen & seen_here:
                raise ValueError('duplicate copper feature across sheets')
            copper_seen.update(seen_here)
        if copper_seen != set(copper):
            raise ValueError('missing copper feature(s): ' + ', '.join(sorted(set(copper) - copper_seen)))
        compared = compare_inventory(document, graph, virtual, qualified['missing_provenance'], probes, copper, reference_scope)
        report.update(compared, qualification=qualified)
    except (OSError, ValueError, KeyError, TypeError, ParseError) as exc:
        report['findings'].append(dict(code='unavailable_captured_evidence', message=str(exc)))
    return report


def capability(params, files, inputs, scope, check_id):
    """Describe the predicate and complete immutable source closure."""
    from .catalog import exact_fields
    exact_fields(params, ('manifest', 'netlist_check', 'requirement_id', *(['reference_set'] if 'reference_set' in params else [])))
    specs = scope.get('checks', [])
    ids = [s['id'] for s in specs]
    producer = next((s for s in specs if s['id'] == params['netlist_check']), None)
    if (producer is None or producer['kind'] != 'netlist' or check_id not in ids or
            ids.index(producer['id']) >= ids.index(check_id)):
        raise ValueError('ordered native netlist prerequisite required')
    document = load_manifest(inputs, params['manifest'])
    if 'reference_set' in params:
        _declared_scope(document, reference_set(params['reference_set']))
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
    """Evaluate captured membership through the coordinator executor."""
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
    arguments = ['--reference-set', json.dumps(ctx.params['reference_set'], separators=(',', ':'))] if 'reference_set' in ctx.params else []
    try:
        ctx.execute(['/usr/bin/python3', '-m', 'agent_ee.parts_subset',
                     '/candidate', ctx.params['manifest'], '/evidence/netlist/design.xml',
                     '--schematic', *sheets, *arguments, '--out', '/work/parts-subset.json'], 120,
                    extra_ro=[(xml, '/evidence/netlist/design.xml')])
    finally:
        ctx.evidence('parts-subset.json')
    report = ctx.load_json('parts-subset.json', 'parts subset')
    if (report.get('native_xml_sha256') != digest(xml) or
            report.get('manifest_sha256') != digest(ctx.inputs / ctx.params['manifest'])):
        raise CheckError('malformed', 'parts report source identity differs')
    expected_scope = ctx.params.get('reference_set')
    if not report_scope_matches(report, expected_scope):
        raise CheckError('malformed', 'parts report reference scope differs from immutable declared parameter')
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
    parser.add_argument('--reference-set', type=json.loads)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    write_json(Path(args.out), evaluate(args.captures, args.manifest, args.native_xml, args.schematic, args.reference_set))


if __name__ == '__main__':
    main()


def describe_parts_subset(ctx):
    descriptor = capability(ctx.params, ctx.files, ctx.inputs, ctx.scope, ctx.spec['id'])
    ctx.prerequisite(ctx.params.get('netlist_check'), ('netlist',))
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('parts_subset', describe_parts_subset, check_parts_subset,
        early_prerequisites=(_requires.placement_clear,), prerequisites=(_requires.stage_progress,))

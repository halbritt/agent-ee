"""Deterministic part properties from captured manifests and explicit component refs."""
import argparse
import copy
import hashlib
from pathlib import Path

from .datasheet_selection import datasheet
from .catalog import exact_fields, nonempty, strict_json
from .connectivity_ir import _read_json, resolve_intent
from .part_decisions import validate_deviation
from .parts_manifest import (load_manifest, qualify_manifest, read_captured, _read_blob,
                             _path)
from .storage import canonical, identity, write_json
from .probe_features import is_probe, declarations, marker, parse_marker, matches_marker, PROPERTY
from . import copper_features
from .fab_export import copper_field_allowed
from .assembly_readiness import _passive_kind


def assembly_declarations(root, document):
    exact_fields(document, ('schema', 'deviations'))
    if document['schema'] != 'agent-ee.part-deviations/1' or not isinstance(document['deviations'], list):
        raise ValueError('expected captured part-deviations/1')
    assembly, dependencies = [], {}
    for index, deviation in enumerate(document['deviations']):
        is_assembly = validate_deviation(deviation)
        for ref in deviation['evidence_refs']:
            exact_fields(ref, ('path', 'locator'))
            nonempty(ref['locator'])
            raw = read_captured(root, ref['path'])
            if not raw:
                raise ValueError('empty deviation evidence')
            dependencies[ref['path']] = hashlib.sha256(raw).hexdigest()
        if is_assembly:
            assembly.append((index, deviation))
    return assembly, dependencies


def _child_instances(selected, part, component, claimed):
    """Declared hierarchical instances of a component in a (possibly reused) child sheet, or None.

    Each ``{sheet, reference}`` must be a non-root allocation of the part and is
    claimed by one component only; the root-sheet rule applies when none are declared.
    """
    if 'instances' not in selected:
        return None
    instances = selected['instances']
    if not isinstance(instances, list) or not instances:
        raise ValueError('declared instances must be a nonempty list: ' + component)
    allocated = {(s['sheet'], s['reference']) for s in part['instances']}
    for instance in instances:
        exact_fields(instance, ('sheet', 'reference'))
        key = nonempty(instance['sheet']), nonempty(instance['reference'])
        if key[0] == '/' or '/' in key[1] or key not in allocated:
            raise ValueError('declared instance outside the part\'s child-sheet allocation: ' + component)
        if key in claimed:
            raise ValueError('declared instance claimed twice: ' + key[0] + key[1])
        claimed.add(key)
    return copy.deepcopy(instances)


def _native_net_names(resolved, libraries):
    """Root-sheet native names, using the emitter's captured power-symbol rules."""
    from .schematic_emitter import _global_power_net, _library_symbol
    from .symbol_library import parse_sexpr
    global_nets, trees = set(), {}
    for comp in resolved['components']:
        if not comp['power']:
            continue
        if len(comp['pins']) != 1:
            raise ValueError('copper net projection requires one-pin power symbols')
        nets = [n for n in resolved['nets'] if any(t['component'] == comp['id'] and
                t['pin'] == comp['pins'][0]['number'] for t in n['terminals'])]
        if len(nets) != 1:
            raise ValueError('copper net projection requires connected power symbols')
        path = comp['sources'][0]['path']
        if path not in trees:
            trees[path] = parse_sexpr(libraries[path])
        symbol = _library_symbol(trees[path], comp['lib_id'].split(':')[1], comp['lib_id'],
                                 '/components/' + comp['id'], comp['sources'])[0]
        if _global_power_net(comp, nets[0], symbol, '/components/' + comp['id']):
            global_nets.add(nets[0]['id'])
    return {n['id']: ('' if n['id'] in global_nets else '/') + n['name'] for n in resolved['nets']}


def fill(root, intent, manifest, refs, deviations, *, captures, ratings=None, source_migration=False, migration_only=False):
    """Return new intent and evidence; supplied objects/files are never modified.

    part_ref is an immutable sidecar contract, not an extension to intent/1.
    Pin-accounting defects remain for the emitter/writer repair loop.
    ``ratings`` opts into source-derived passive rating fields (passive_ratings).
    """
    if type(source_migration) is not bool or type(migration_only) is not bool:
        raise ValueError('source migration options must be boolean')
    if migration_only and not source_migration:
        raise ValueError('migration_only requires explicit source_migration')
    root = Path(root)
    intent = _read_json(intent)
    exact_fields(refs, ('schema', 'components'))
    if refs['schema'] != 'agent-ee.part-refs/1' or not isinstance(refs['components'], dict):
        raise ValueError('expected part-refs/1 component map')
    qualified = qualify_manifest(manifest, root)
    fields = manifest['property_fields']
    if (fields['lcsc'] != 'LCSC' or fields['manufacturer'] != 'Manufacturer' or
            fields['mpn'] not in ('MPN', 'Partnumber', 'PartNumber')):
        raise ValueError('binding fill requires canonical native identity property names')
    assignments = strict_json(_read_blob(root, manifest['cad']['assignments']))
    expected_libraries = {lib['name']: lib['sha256'] for lib in assignments['libraries']}
    libraries = {lib['path']: read_captured(root, (Path(captures) / lib['path']).as_posix())
                 for lib in intent['libraries']}
    resolved = resolve_intent(intent, libraries)
    if (resolved['execution'] != 'ok' or len(resolved['components']) != len(intent['components']) or
            any(f['code'] not in ('unknown_pin', 'unaccounted_pin') for f in resolved['findings'])):
        raise ValueError('binding fill requires resolved captured component identities')
    physical = {c['id']: c for c in resolved['components'] if not c['power']}
    if (set(refs['components']) - set(physical) or
            (not migration_only and set(refs['components']) != set(physical))):
        raise ValueError('part_ref coverage must equal all non-power components')
    assembly, dependencies = assembly_declarations(root, deviations)
    dependencies.update(qualified['dependencies'])
    dependencies.update({(Path(captures) / lib['path']).as_posix(): lib['sha256'] for lib in intent['libraries']})
    parts = {p['id']: p for p in manifest['parts']}
    probes = declarations(manifest, root)
    copper = copper_features.declarations(manifest, root)
    if migration_only:
        expected = {c['id'] for c in physical.values() if c['reference'] in probes or c['reference'] in copper}
        if set(refs['components']) != expected:
            raise ValueError('migration_only part_refs must cover exactly declared source features')
    native_names = _native_net_names(resolved, libraries) if copper else {}
    missing_copper = set(copper) - {c['reference'] for c in physical.values()}
    if missing_copper:
        raise ValueError('missing copper feature(s): ' + ', '.join(sorted(missing_copper)))
    missing_probes = set(probes) - {c['reference'] for c in physical.values()}
    if missing_probes:
        raise ValueError('missing probe feature(s): ' + ', '.join(sorted(missing_probes)))
    output, records, claimed = copy.deepcopy(intent), [], set()
    migrations, preserved = [], []
    rating_report = None
    if migration_only and ratings is not None:
        raise ValueError('migration_only preserves purchased bindings; ratings cannot be derived')
    if ratings is not None:
        from . import passive_ratings
        facts, extractor, used = passive_ratings.load_sources(root, ratings)
        verified, pdfs = passive_ratings.verify_citations(root, facts, extractor) if facts else ({}, {})
        dependencies.update(used)
        dependencies.update(pdfs)
        rating_report = {'sources_sha256': identity(ratings), 'derived': {}, 'missing': {}}
        rated_mpns = set()
    for comp in output['components']:
        if comp['id'] not in physical:
            continue
        if migration_only and comp['reference'] not in probes and comp['reference'] not in copper:
            if PROPERTY in comp.get('part_binding', {}):
                raise ValueError('probe has no immutable source declaration: ' + comp['reference'])
            preserved.append({'component': comp['id'], 'reference': comp['reference'],
                              'identity_sha256': identity(comp)})
            continue
        if comp['id'] not in refs['components']:
            raise ValueError('source migration requires feature part_ref: ' + comp['id'])
        selected = refs['components'][comp['id']]
        exact_fields(selected, ('part_ref', 'instances') if isinstance(selected, dict) and 'instances' in selected
                     else ('part_ref',))
        nonempty(selected['part_ref'])
        if selected['part_ref'] not in parts:
            raise ValueError('unknown part_ref: ' + selected['part_ref'])
        part = parts[selected['part_ref']]
        instances = _child_instances(selected, part, comp['id'], claimed)
        library = comp['lib_id'].split(':')[0]
        actual_library = next(lib for lib in intent['libraries'] if lib['name'] == library)
        if actual_library['sha256'] != expected_libraries.get(library):
            raise ValueError('component symbol capture differs from part_ref CAD')
        if any(comp[k] != part[k] for k in ('lib_id', 'footprint')):
            raise ValueError('component CAD differs from part_ref: ' + comp['id'])
        if instances is None and not any(s['sheet'] == '/' and s['reference'] == comp['reference']
                                         for s in part['instances']):
            raise ValueError('component reference outside part instance allocation: ' + comp['id'])
        if is_probe(part):
            if instances is not None:
                raise ValueError('probe features take root-sheet allocations only: ' + comp['id'])
            record = probes[comp['reference']]
            connected = [net['name'] for net in resolved['nets'] if
                         any(t['component'] == comp['id'] and t['pin'] == record['pin']['number']
                             for t in net['terminals'])]
            if connected != [record['net']]:
                raise ValueError('probe intent net differs: ' + comp['reference'])
            fields = {PROPERTY: marker(record)}
            previous = comp.get('part_binding', {}).get(PROPERTY)
            if source_migration and previous is not None and previous != fields[PROPERTY]:
                # The same decoder and exact legacy identity gate used by annotation.
                if not isinstance(previous, str) or not matches_marker(previous, record) or parse_marker(previous) != record:
                    raise ValueError('writer binding mismatch: ' + comp['id'])
                if set(comp['part_binding']) != {PROPERTY}:
                    raise ValueError('writer binding mismatch: ' + comp['id'])
                migrations.append(dict(reference=comp['reference'],
                    old_encoding_sha256=hashlib.sha256(previous.encode('utf-8')).hexdigest(),
                    new_encoding_sha256=hashlib.sha256(fields[PROPERTY].encode('utf-8')).hexdigest(),
                    decoded_identity_sha256=identity(record)))
                comp['part_binding'] = fields
            if any(fields.get(k) != v for k, v in comp.get('part_binding', {}).items()):
                raise ValueError('writer binding mismatch: ' + comp['id'])
            comp['part_binding'] = fields
            records.append({'component': comp['id'], 'reference': comp['reference'],
                            'part_ref': part['id'], 'fields': fields, 'assembly_deviation': None})
            continue
        if copper_features.is_copper(part):
            if instances is not None:
                raise ValueError('copper features take root-sheet allocations only: ' + comp['id'])
            record = copper[comp['reference']]
            actual_pins = [{k: pin[k] for k in ('number', 'name', 'electrical_type')}
                           for pin in physical[comp['id']]['pins']]
            if sorted(actual_pins, key=lambda p: p['number']) != sorted(record['pins'], key=lambda p: p['number']):
                raise ValueError('copper intent pins differ: ' + comp['reference'])
            for pin, net in record['nets'].items():
                connected = [native_names[n['id']] for n in resolved['nets'] if any(
                    t['component'] == comp['id'] and t['pin'] == pin for t in n['terminals'])]
                if connected != [net]:
                    raise ValueError('copper intent net differs: ' + comp['reference'] + '/' + pin)
            fields = comp.get('part_binding', {})
            if any(not copper_field_allowed(k, v) for k, v in fields.items()):
                raise ValueError('writer binding mismatch: ' + comp['id'])
            if comp.get('in_bom', False) is not False:
                raise ValueError('copper intent BOM flag differs: ' + comp['reference'])
            comp['in_bom'] = False
            records.append({'component': comp['id'], 'reference': comp['reference'],
                            'part_ref': part['id'], 'fields': fields, 'assembly_deviation': None})
            continue
        sheet, paths = datasheet(root, part)
        dependencies.update(paths)
        fields = {'LCSC': part['lcsc'], 'Manufacturer': part['manufacturer'], 'Partnumber': part['mpn'],
                  'Datasheet': sheet['url'], 'PARAM_Datasheet': canonical({
                      'schema': 'agent-ee.datasheet-binding/1', 'url': sheet['url'],
                      'sha256': sheet['sha256']}).decode()}
        for key, name in manifest['property_fields'].items():
            value = part[{'lcsc': 'lcsc', 'mpn': 'mpn', 'manufacturer': 'manufacturer'}[key]]
            if name in fields and fields[name] != value:
                raise ValueError('manifest property field collides with canonical binding')
            fields[name] = value
        if 'selection' not in part:
            choice = next(p['chosen'] for p in qualified['selection']['parts'] if p['id'] == part['selection_id'])
            fields.update({'PARAM_' + k: canonical(v).decode() for k, v in choice['normalized'].items()})
        matches = [(i, d) for i, d in assembly if d['original']['mpn'] == part['mpn'] and
                   d['original']['lcsc'] in (None, part['lcsc'])]
        if len(matches) > 1:
            raise ValueError('ambiguous assembly deviation for part_ref')
        if matches:
            fields['JLCPCB_Assembly'] = 'not_assembled'
        if part['lcsc'] == 'NO_LCSC':
            record = strict_json(read_captured(root, part['selection']['part_record']))
            pinned = strict_json(_read_blob(root, record['deviation_ref']))
            if not matches or deviations != pinned or matches[0][0] != record['deviation_ref']['index']:
                raise ValueError('external part requires its exact captured assembly deviation; requalification required')
        if rating_report is not None:
            kind = _passive_kind({'lib_id': comp['lib_id'], 'reference': comp['reference']})
            if kind is not None:
                rated_mpns.add(part['mpn'])
                derived = passive_ratings.derive(root, part, kind, catalog=ratings['catalog'], facts=facts,
                                                 verified=verified)
                # A reused child sheet's symbol carries one field for all its instances;
                # each hierarchical instance gets its own entry for the same part record.
                keys = [comp['reference']] if instances is None else [s['sheet'] + s['reference'] for s in instances]
                if 'missing' in derived:
                    rating_report['missing'].update(dict.fromkeys(keys, derived['missing']))
                else:
                    collisions = set(derived['fields']) & set(fields)
                    if collisions:
                        raise ValueError('rating field collides with binding: ' + ', '.join(sorted(collisions)))
                    fields.update(derived['fields'])
                    rating_report['derived'].update({key: {'record': derived['record'],
                                                           'record_sha256': derived['record_sha256']}
                                                     for key in keys})
        for name, value in comp.get('part_binding', {}).items():
            if name not in fields or value != fields[name]:
                raise ValueError('writer binding mismatch: ' + comp['id'] + '/' + name)
        comp['part_binding'] = fields
        records.append({'component': comp['id'], 'reference': comp['reference'], 'part_ref': part['id'],
                        **({} if instances is None else {'instances': instances}),
                        'fields': fields, 'assembly_deviation': matches[0][0] if matches else None})
    if rating_report is not None:
        unused = sorted(f['id'] for f in facts if f['part_number'] not in rated_mpns)
        if unused:
            raise ValueError('rating facts name no bound R/C part: ' + ', '.join(unused))
    report = {'ratings': rating_report} if rating_report is not None else {}
    if migration_only:
        report.update(operation='source-migration-only', preserved_components=preserved)
    if source_migration:
        report['probe_migrations'] = sorted(migrations, key=lambda r: r['reference'])
    return output, {'schema': 'agent-ee.binding-fill/2' if source_migration else 'agent-ee.binding-fill/1', **report, 'input_sha256': identity(intent),
                    'output_sha256': identity(output), 'parts_sha256': identity(manifest),
                    'part_refs_sha256': identity(refs), 'deviations_sha256': identity(deviations),
                    'dependencies': dict(sorted(dependencies.items())), 'components': records,
                    'limitations': ['Captured identity projection only; no suitability, stock, purchase or hardware acceptance.']}


def configuration(source_root, *, intent, manifest, part_refs, deviations, captures, dependencies, output=None,
                  ratings=None, source_migration=False, migration_only=False):
    """One ordinary receipted producer preceding emit/annotate; its bound intent is its only write.

    By default the intent is filled in place. A distinct `output` keeps the read intent
    writer-owned and makes the bound projection a coordinator output (pod campaigns).
    An optional captured `ratings` declaration is a declared input (AGENTEE-233)."""
    from .stage_receipts import validate_step
    if type(source_migration) is not bool or type(migration_only) is not bool:
        raise ValueError('source migration options must be boolean')
    if migration_only and not source_migration:
        raise ValueError('migration_only requires explicit source_migration')
    output = intent if output is None else output
    extra = [] if ratings is None else [ratings]
    for path in (intent, output, manifest, part_refs, deviations, *extra, *dependencies):
        _path(path)
    if captures != '.':  # '.' is the candidate root, as fill and the schematic stage accept
        _path(captures)
    if output in {manifest, part_refs, deviations, *extra, *dependencies}:
        raise ValueError('binding source cannot be the output intent')
    if intent in {manifest, part_refs, deviations, *extra, *dependencies}:
        raise ValueError('binding source cannot be the read intent')
    return validate_step({'schema': 'agent-ee.stage-step/1', 'stage': 'schematic', 'step': 'binding-fill',
        'argv': ['/usr/bin/env', 'PYTHONPATH=/tool/agent-ee', '/usr/bin/python3', '-B', '-P',
                 '-m', 'agent_ee.binding_fill', '--intent', intent, '--manifest', manifest,
                 '--part-refs', part_refs, '--deviations', deviations, '--captures', captures,
                 *(['--ratings', ratings] if ratings is not None else []),
                 *(['--source-migration'] if source_migration else []),
                 *(['--migration-only'] if migration_only else [])],
        'mounts': [{'source': str(Path(source_root).absolute()), 'at': '/tool/agent-ee'}],
        'source': '/tool/agent-ee', 'version_argv': ['/usr/bin/python3', '--version'],
        'inputs': sorted({manifest, part_refs, deviations, *extra, *dependencies} | ({intent} - {output})),
        'writes': {output: 'out/intent.json'}, 'artifacts': ['out/binding-fill.json'],
        'adoptable_exits': [0], 'timeout_s': 600 if source_migration else 120})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('intent', 'manifest', 'part-refs', 'deviations', 'captures'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--ratings', help='captured agent-ee.passive-rating-sources/1 declaration')
    parser.add_argument('--source-migration', action='store_true',
                        help='explicitly migrate exact validated legacy probe bindings; emit binding-fill/2')
    parser.add_argument('--migration-only', action='store_true',
                        help='with --source-migration, preserve purchased components verbatim; migrate declared features only')
    args = parser.parse_args(argv)
    root, out = Path('/candidate'), Path('/work/out')
    out.mkdir(exist_ok=False)
    try:
        intent, report = fill(root, read_captured(root, args.intent), load_manifest(root, args.manifest),
            strict_json(read_captured(root, args.part_refs)), strict_json(read_captured(root, args.deviations)),
            captures=args.captures, source_migration=args.source_migration, migration_only=args.migration_only,
            ratings=strict_json(read_captured(root, args.ratings)) if args.ratings else None)
        write_json(out / 'binding-fill.json', report)
        write_json(out / 'intent.json', intent)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        write_json(out / 'binding-fill.json', {'schema': 'agent-ee.binding-fill/2' if args.source_migration else 'agent-ee.binding-fill/1',
                   'execution': 'failed', 'reason': str(exc)})
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

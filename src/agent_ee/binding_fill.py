"""Deterministic part properties from captured manifests and explicit component refs."""
import argparse
import copy
import hashlib
import re
from pathlib import Path

from .catalog import exact_fields, nonempty, strict_json
from .connectivity_ir import _read_json, resolve_intent
from .part_decisions import validate_deviation
from .parts_manifest import (load_manifest, qualify_manifest, read_captured, _read_blob,
                             _path)
from .storage import canonical, identity, write_json
from .probe_features import is_probe, declarations, marker, PROPERTY


def datasheet(root, part):
    from .datasheet_facts import load_acquisition
    sheet = part['datasheet']
    if sheet['status'] != 'captured':
        raise ValueError('binding fill requires captured datasheet: ' + part['id'])
    provenance = sheet['provenance']
    match = re.fullmatch(r'parts\[id=(.+)\]', provenance['locator'])
    if match is None:
        raise ValueError('datasheet provenance must select a captured acquisition id')
    blob = {k: provenance[k] for k in ('path', 'sha256')}
    document = strict_json(_read_blob(root, blob))
    bindings = sorted([{'id': r['id'], 'datasheet_url': r['url']} for r in document['parts']],
                      key=lambda r: r['id'])
    acquired, paths = load_acquisition(root, blob, bindings)
    record = acquired.get(match[1])
    if record is None or record['missing']:
        raise ValueError('part datasheet acquisition missing')
    artifact = {'path': (Path(blob['path']).parent / record['path']).as_posix(), 'sha256': record['sha256']}
    if artifact != sheet['artifact']:
        raise ValueError('part datasheet artifact differs from acquisition')
    return record, {p: hashlib.sha256(read_captured(root, p)).hexdigest() for p in paths}


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


def fill(root, intent, manifest, refs, deviations, *, captures):
    """Return new intent and evidence; supplied objects/files are never modified.

    part_ref is an immutable sidecar contract, not an extension to intent/1.
    Pin-accounting defects remain for the emitter/writer repair loop.
    """
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
    if set(refs['components']) != set(physical):
        raise ValueError('part_ref coverage must equal all non-power components')
    assembly, dependencies = assembly_declarations(root, deviations)
    dependencies.update(qualified['dependencies'])
    dependencies.update({(Path(captures) / lib['path']).as_posix(): lib['sha256'] for lib in intent['libraries']})
    parts = {p['id']: p for p in manifest['parts']}
    probes = declarations(manifest, root)
    missing_probes = set(probes) - {c['reference'] for c in physical.values()}
    if missing_probes:
        raise ValueError('missing probe feature(s): ' + ', '.join(sorted(missing_probes)))
    output, records = copy.deepcopy(intent), []
    for comp in output['components']:
        if comp['id'] not in physical:
            continue
        selected = refs['components'][comp['id']]
        exact_fields(selected, ('part_ref',))
        nonempty(selected['part_ref'])
        if selected['part_ref'] not in parts:
            raise ValueError('unknown part_ref: ' + selected['part_ref'])
        part = parts[selected['part_ref']]
        library = comp['lib_id'].split(':')[0]
        actual_library = next(lib for lib in intent['libraries'] if lib['name'] == library)
        if actual_library['sha256'] != expected_libraries.get(library):
            raise ValueError('component symbol capture differs from part_ref CAD')
        if any(comp[k] != part[k] for k in ('lib_id', 'footprint')):
            raise ValueError('component CAD differs from part_ref: ' + comp['id'])
        if not any(s['sheet'] == '/' and s['reference'] == comp['reference'] for s in part['instances']):
            raise ValueError('component reference outside part instance allocation: ' + comp['id'])
        if is_probe(part):
            record = probes[comp['reference']]
            connected = [net['name'] for net in resolved['nets'] if
                         any(t['component'] == comp['id'] and t['pin'] == record['pin']['number']
                             for t in net['terminals'])]
            if connected != [record['net']]:
                raise ValueError('probe intent net differs: ' + comp['reference'])
            fields = {PROPERTY: marker(record)}
            if any(fields.get(k) != v for k, v in comp.get('part_binding', {}).items()):
                raise ValueError('writer binding mismatch: ' + comp['id'])
            comp['part_binding'] = fields
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
        for name, value in comp.get('part_binding', {}).items():
            if name not in fields or value != fields[name]:
                raise ValueError('writer binding mismatch: ' + comp['id'] + '/' + name)
        comp['part_binding'] = fields
        records.append({'component': comp['id'], 'reference': comp['reference'], 'part_ref': part['id'],
                        'fields': fields, 'assembly_deviation': matches[0][0] if matches else None})
    return output, {'schema': 'agent-ee.binding-fill/1', 'input_sha256': identity(intent),
                    'output_sha256': identity(output), 'parts_sha256': identity(manifest),
                    'part_refs_sha256': identity(refs), 'deviations_sha256': identity(deviations),
                    'dependencies': dict(sorted(dependencies.items())), 'components': records,
                    'limitations': ['Captured identity projection only; no suitability, stock, purchase or hardware acceptance.']}


def configuration(source_root, *, intent, manifest, part_refs, deviations, captures, dependencies, output=None):
    """One ordinary receipted producer preceding emit/annotate; its bound intent is its only write.

    By default the intent is filled in place. A distinct `output` keeps the read intent
    writer-owned and makes the bound projection a coordinator output (pod campaigns)."""
    from .stage_receipts import validate_step
    output = intent if output is None else output
    for path in (intent, output, manifest, part_refs, deviations, *dependencies):
        _path(path)
    if captures != '.':  # '.' is the candidate root, as fill and the schematic stage accept
        _path(captures)
    if output in {manifest, part_refs, deviations, *dependencies}:
        raise ValueError('binding source cannot be the output intent')
    if intent in {manifest, part_refs, deviations, *dependencies}:
        raise ValueError('binding source cannot be the read intent')
    return validate_step({'schema': 'agent-ee.stage-step/1', 'stage': 'schematic', 'step': 'binding-fill',
        'argv': ['/usr/bin/env', 'PYTHONPATH=/tool/agent-ee', '/usr/bin/python3', '-B', '-P',
                 '-m', 'agent_ee.binding_fill', '--intent', intent, '--manifest', manifest,
                 '--part-refs', part_refs, '--deviations', deviations, '--captures', captures],
        'mounts': [{'source': str(Path(source_root).absolute()), 'at': '/tool/agent-ee'}],
        'source': '/tool/agent-ee', 'version_argv': ['/usr/bin/python3', '--version'],
        'inputs': sorted({manifest, part_refs, deviations, *dependencies} | ({intent} - {output})),
        'writes': {output: 'out/intent.json'}, 'artifacts': ['out/binding-fill.json'],
        'adoptable_exits': [0], 'timeout_s': 120})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('intent', 'manifest', 'part-refs', 'deviations', 'captures'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    root, out = Path('/candidate'), Path('/work/out')
    out.mkdir(exist_ok=False)
    try:
        intent, report = fill(root, read_captured(root, args.intent), load_manifest(root, args.manifest),
            strict_json(read_captured(root, args.part_refs)), strict_json(read_captured(root, args.deviations)),
            captures=args.captures)
        write_json(out / 'binding-fill.json', report)
        write_json(out / 'intent.json', intent)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        write_json(out / 'binding-fill.json', {'schema': 'agent-ee.binding-fill/1',
                   'execution': 'failed', 'reason': str(exc)})
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

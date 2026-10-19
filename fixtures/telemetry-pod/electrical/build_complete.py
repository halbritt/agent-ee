#!/usr/bin/env python3
"""Assemble captured pod electrical inputs; candidate circuit, never acceptance."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

from agent_ee.part_record import prepare_record
from agent_ee.parts_manifest import qualify_manifest
from agent_ee.storage import identity
from agent_ee.connectivity_ir import resolve_intent
from agent_ee.binding_fill import fill
from build_core import build as build_core
from power_cad import write_cad
from mode_cad import write_primary_spi
from prepare_intent import prepare, probe_and_power_features
from j1_courtyard import derive as derive_j1


def add_footprint(footprints, library, member, blob):
    members = footprints.setdefault(library, {})
    # Multiple selected parts can use byte-identical captures of one FPID.
    # Preserve the existing last-path selection only when the content agrees.
    if member in members and members[member]['sha256'] != blob['sha256']:
        raise ValueError("duplicate footprint identity: " + library + ":" + member)
    members[member] = blob


def build(output, roots):
    here = Path(__file__).resolve().parent
    load = lambda p: json.loads(Path(p).read_text())
    lock = load(here / 'power-source-lock.json')
    inputs = {}
    for item in lock['files']:
        raw = (roots[item['root']] / item['source']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('power source pin mismatch: ' + item['path'])
        if item['path'] in inputs:
            raise ValueError('duplicate captured path')
        inputs[item['path']] = raw
    j1_decision = (here / "j1-placement-exclusion.json").read_bytes()
    j1_raw, j1_receipt = derive_j1(inputs, j1_decision)
    if j1_receipt["derived"]["path"] in inputs:
        raise ValueError("J1 generated path collides with captured input")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    build_core(output / 'core', roots)
    for path, raw in inputs.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    write_cad(output / 'authored-cad')
    write_primary_spi(output / 'core/symbols/Pod.kicad_sym', output / 'authored-cad/PodPrimarySPI.kicad_sym')

    def blob(path):
        return dict(path=path, sha256=hashlib.sha256((output / path).read_bytes()).hexdigest())

    def write(path, value):
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')

    derived_path = output / j1_receipt['derived']['path']
    derived_path.parent.mkdir(parents=True, exist_ok=True)
    with derived_path.open('xb') as stream:
        stream.write(j1_raw)
    write('j1-courtyard-derivation.json', j1_receipt)

    for name in ('j1-placement-exclusion.json', 'j1_courtyard.py', 'power-selections.json', 'power-source-lock.json', 'power_cad.py',
                 'complete-decisions.md', 'support-decisions.json', 'mode_cad.py', 'prepare_intent.py',
                 'probe-selections.json'):
        (output / name).write_bytes((here / name).read_bytes())
    original = (output / 'core/requirements/io-map.actual.csv').read_bytes()
    source_rows = list(csv.DictReader(io.StringIO(original.decode())))
    derived = original.replace(b',POD_SENSOR_RETURN,', b',GND,')
    derived_rows = list(csv.DictReader(io.StringIO(derived.decode())))
    expected = json.loads(json.dumps(source_rows))
    contacts = {'AMPSEAL-' + str(n) for n in (7, 10, 13, 16)}
    changes = []
    for row in expected:
        if row['Connector_Pin'] in contacts:
            if row['Net'] != 'POD_SENSOR_RETURN':
                raise ValueError('sensor return source net changed')
            changes.append(dict(connector_pin=row['Connector_Pin'], signal_name=row['Signal_Name'],
                                field='Net', source='POD_SENSOR_RETURN', native='GND'))
            row['Net'] = 'GND'
    if len(changes) != 4 or expected != derived_rows:
        raise ValueError('sensor return adaptation changed undeclared cells')
    (output / 'requirements').mkdir()
    (output / 'requirements/io-map.actual.csv').write_bytes(derived)
    write('requirements/io-map-aliases.json', dict(
        original=blob('core/requirements/io-map.source.csv'),
        can_named=blob('core/requirements/io-map.actual.csv'),
        prior_aliases=blob('core/io-map-aliases.json'), derived=blob('requirements/io-map.actual.csv'),
        changes=changes, reason='Four connector return pins share continuous GND; physical connector-local sense pickup is a separate layout obligation.'))
    core = load(here / 'core-selections.json')['parts']
    rows = []
    for p in core:
        rows.append(dict(p, catalog='core/' + p['catalog'], datasheets='core/' + p['datasheets']))
        if p['id'] == 'imu':
            rows[-1]['lib_id'] = 'PodPrimarySPI:LSM6DSO32'
    rows.extend(load(here / 'power-selections.json')['parts'])
    core_ids = {p['id'] for p in core}
    libraries, footprints, parts = {}, {}, []
    decision = dict(**blob('complete-decisions.md'), locator='Circuit decisions')
    for p in rows:
        prefix = 'core/' if p['id'] in core_ids else ''
        library, symbol = p['lib_id'].split(':')
        symbol_path = ('authored-cad/' + library + '.kicad_sym' if library in ('PodPower', 'PodPrimarySPI')
                       else prefix + 'symbols/' + library + '.kicad_sym')
        libraries[library] = dict(name=library, **blob(symbol_path))
        fp_lib, member = p['footprint'].split(':')
        fp_path = ('authored-cad/' if fp_lib == 'PodPower' else prefix + 'footprints/') + fp_lib + '.pretty/' + member + '.kicad_mod'
        if p['id'] == 'connector-j1':
            if (p['reference'], p['footprint'], p['lcsc']) != ('J1', 'ampseal:ampseal-23', 'C498958'):
                raise ValueError('J1 selected identity differs from courtyard decision')
            fp_path = j1_receipt['derived']['path']
        add_footprint(footprints, fp_lib, member, blob(fp_path))
        record = prepare_record(output, p['lcsc'], blob(p['catalog']), blob(p['datasheets']), p['datasheet_id'])
        if p['id'] == 'connector-j1' and record['mpn'] != '776228-1':
            raise ValueError('J1 actual ordering code differs from courtyard decision')
        record_path = 'records/' + p['id'] + '.json'
        write(record_path, record)
        parts.append(dict(id=p['id'], role=p['role'], required=True,
            instances=[dict(sheet='/', reference=p['reference'])], selection_id=p['id'],
            lib_id=p['lib_id'], footprint=p['footprint'],
            **{k: record[k] for k in ('lcsc', 'mpn', 'manufacturer', 'package', 'stock')},
            selection=dict(kind='named', lcsc=p['lcsc'], part_record=record_path,
                           part_record_sha256=blob(record_path)['sha256'], decision_ref=decision),
            datasheet=dict(status='captured', required=True, artifact=record['artifact'],
                           provenance=dict(**record['datasheets'], locator='parts[id=' + p['datasheet_id'] + ']'))))
    probes = load(here / 'probe-selections.json')
    for part in probes['parts']:
        library = part['lib_id'].split(':')[0]
        captured_library = dict(name=library, **part['probe']['symbol'])
        if library in libraries and libraries[library] != captured_library:
            raise ValueError('probe symbol conflicts with captured library')
        libraries[library] = captured_library
        fp_lib, member = part['footprint'].split(':')
        members = footprints.setdefault(fp_lib, {})
        if member in members and members[member] != part['probe']['footprint']:
            raise ValueError('probe footprint conflicts with captured member')
        members[member] = part['probe']['footprint']
    parts.extend(probes['parts'])
    declarations = load(output / 'core/cad/footprint-declarations.json')
    for name in ('DDA0008B', 'DDA0008J_C02'):
        declarations['footprints'].append(dict(footprint='PodPower:' + name,
            mechanical_pads=[], thermal_via_pads=['9'], custom_pads=[], assembly=None,
            rationale='Manufacturer land drawing: exposed ground pad9 with eight copper-connected0.2mm thermal holes. Paste/fill process remains unqualified.'))
    declarations['footprints'].append(dict(
        footprint='Connector_Coaxial:SMA_Molex_73251-1153_EdgeMount_Horizontal',
        mechanical_pads=[], thermal_via_pads=['2'], custom_pads=[], assembly=None,
        rationale='Two native0.46mm plated ground stitching vias share electrical pad2 with top/bottom SMD ground lands; they are not connector through-hole leads. Existing thermal_via_pads schema names this shared-number via handling; no thermal-performance claim.'))
    write('cad/declarations.json', declarations)
    cad = dict(schema='agent-ee.footprint-libraries/1', complete=True,
        libraries=[dict(name=lib, members=[members[k] for k in sorted(members)]) for lib, members in sorted(footprints.items())])
    assignments = dict(schema='agent-ee.footprint-assignments-input/1', libraries=list(libraries.values()),
        components=[dict(reference=p['id'], lib_id=p['lib_id'], footprint=p['footprint'], assembly=p.get('assembly', 'smd'), connector=None) for p in rows])
    assignments['components'].extend(dict(reference=p['id'], lib_id=p['lib_id'],
        footprint=p['footprint'], assembly='smd', connector=None) for p in probes['parts'])
    write('cad/manifest.json', cad)
    write('cad/assignments.json', assignments)
    members = [dict(path=p['path'], sha256=p['sha256']) for p in libraries.values()] + [m for lib in cad['libraries'] for m in lib['members']]
    manifest = dict(schema='agent-ee.parts/1', declaration_source=decision, catalog=None, selection=None,
        cad=dict(manifest=blob('cad/manifest.json'), assignments=blob('cad/assignments.json'), declarations=blob('cad/declarations.json'),
                 members_sha256=identity(sorted(members, key=lambda p: p['path']))),
        property_fields=dict(lcsc='LCSC', mpn='Partnumber', manufacturer='Manufacturer'),
        population='native-dnp-optional-only/1', parts=parts)
    # Publish the candidate inputs needed by the existing flat compiler, then
    # bind its freshly generated CAN libraries in the final CAD declaration.
    # The historical core library captures remain untouched under core/.
    write('parts-electrical.json', manifest)
    refs = dict(schema='agent-ee.part-refs/1', components={p['component']:dict(part_ref=p['id']) for p in rows})
    if set(refs['components']) & set(probes['components']):
        raise ValueError('probe component collides with purchased part')
    refs['components'].update(probes['components'])
    write('part-refs.json', refs)
    write('electrical-selections.json', dict(parts=rows, done=False))
    composed = prepare(output, probe_and_power_features(output))
    for path, raw in composed['captures'].items():
        target = output / path
        if target.exists() and target.read_bytes() != raw:
            raise ValueError('composition would rewrite a captured input: ' + path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    refreshed = {lib['name']: lib for lib in composed['intent']['libraries'] if lib['name'].startswith('Block_')}
    old_can = [lib for lib in assignments['libraries'] if lib['name'].startswith('Block_')]
    if set(refreshed) != {lib['name'] for lib in old_can}:
        raise ValueError('CAN library inventory changed')
    assignments['libraries'] = [refreshed.get(lib['name'], lib) for lib in assignments['libraries']]
    write('cad/assignments.json', assignments)
    members = [dict(path=p['path'], sha256=p['sha256']) for p in assignments['libraries']] + [m for lib in cad['libraries'] for m in lib['members']]
    manifest['cad']['assignments'] = blob('cad/assignments.json')
    manifest['cad']['members_sha256'] = identity(sorted(members, key=lambda p: p['path']))
    import agent_ee.block_flat as flat_producer
    write('can-library-regeneration.json', dict(
        authority='informational source and delivered capture inventory; not source review qualification',
        historical=old_can, delivered=list(refreshed.values()),
        producer_sha256=hashlib.sha256(Path(flat_producer.__file__).read_bytes()).hexdigest(),
        composition=blob('core/can-source/composition.json'),
        reason='Reviewed source-version-aware property normalization preserves native legacy tilde semantics. Historical core bytes remain unchanged.'))
    write('intent.unbound.json', composed['intent'])
    resolution = resolve_intent(composed['intent'], composed['captures'])
    if (resolution['execution'], resolution['outcome'], resolution['coverage']) != ('ok', 'pass', 'complete'):
        raise ValueError('full pin allocation did not resolve')
    write('resolved-intent.unbound.json', resolution)
    net_by_terminal = {(t['component'], t['pin']): net['name']
                       for net in resolution['nets'] for t in net['terminals']}
    write('pin-allocation.actual.json', dict(done=False,
        authority='Informational resolved actual pin inventory; not a replacement for canonical requirements or native verification',
        intent=blob('intent.unbound.json'), resolution=blob('resolved-intent.unbound.json'),
        decisions=blob('complete-decisions.md'), components=[dict(
            component=c['id'], reference=c['reference'], lib_id=c['lib_id'], power=c['power'],
            pins=[dict(number=p['number'], name=p['name'], electrical_type=p['electrical_type'],
                       net=net_by_terminal.get((c['id'], p['number'])),
                       disposition='connect' if (c['id'], p['number']) in net_by_terminal else 'no_connect')
                  for p in c['pins']]) for c in resolution['components']]))
    write('block-instances.json', composed['block_instances'])
    write('part-property-adaptations.json', composed['part_property_adaptations'])
    result = qualify_manifest(manifest, output)
    write('parts-electrical.json', manifest)
    write('parts-electrical-qualification.json', result)
    deviations = dict(schema='agent-ee.part-deviations/1', deviations=[])
    write('part-deviations.json', deviations)
    bound, binding = fill(output, composed['intent'], manifest, refs, deviations, captures='.')
    write('intent.json', bound)
    write('binding-fill.json', binding)
    write('bundle-complete.json', dict(done=False,
        scope='Bound candidate circuit inputs: 102 purchased components, six declared manufactured probes and five virtual power markers; native and sealed consumer checks remain required',
        manifest=blob('parts-electrical.json'), intent=blob('intent.json'),
        source_lock_sha256=hashlib.sha256((here / 'power-source-lock.json').read_bytes()).hexdigest(),
        files=[blob(str(p.relative_to(output))) for p in sorted(output.rglob('*')) if p.is_file()],
        limitations=['Input inventory only; no campaign seal, engineering acceptance or fabrication release.',
                     'Canonical166 power projection remains unresolved because the spec delegates exact regulator selection; actual selections are explicit in parts/1 and source-bound engineering decisions.',
                     'Temperature, connector orientation/height, manufacturer land-pattern qualification and physical/bench conditions remain open.'] + j1_receipt['limitations']))
    return manifest, result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--root', action='append', default=[])
    args = parser.parse_args()
    roots = {}
    for item in args.root:
        name, path = item.split('=', 1)
        if name in roots:
            raise ValueError('duplicate root')
        roots[name] = Path(path)
    manifest, result = build(args.out, roots)
    print(json.dumps(dict(parts=len(manifest['parts']), output=str(args.out), done=False)))

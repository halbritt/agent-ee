"""Finite C1/C2 film-capacitor derivation of the unaccepted ADP reference.

The ordinary parts, native CAD and placement checks still decide whether the
generated source is usable. Captured identity is not electrical qualification.
"""
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
from pathlib import Path

from . import (acquisition_film_analysis, acquisition_power_analysis, acquisition_power_prepare,
               acquisition_reverse_blocking, cpl_rotation, easyeda_footprint,
               part_record, parts_manifest, typed_overlay)
from .catalog import strict_json
from .footprint_library import _members
from .storage import identity, json_bytes

DATA = Path(__file__).resolve().parents[2] / 'fixtures/acquisition-film'
REFERENCE = 'acquisition-control-film-r1'
MPN = '25MU225MB23225'
LCSC = 'C3778570'
FOOTPRINT = 'C_25MU225MB23225_C3778570'
LIBRARY = acquisition_reverse_blocking.LIBRARY
SUPPLIER = 'film-reference/supplier/C3778570.raweasy.json'
RECORD = 'film-reference/C3778570.part-record.json'
MODEL_PATH = 'lib3d/Capacitor_SMD.3dshapes/C_1210_3225Metric.step'
_GEOMETRY = strict_json((DATA / 'geometry-sources.json').read_bytes())
_LEGACY_GEOMETRY = strict_json((DATA / 'geometry-sources-r1.json').read_bytes())
MODEL = _GEOMETRY['model']
SOURCES = _GEOMETRY['sources']
MODELS = {MODEL: SOURCES, _LEGACY_GEOMETRY['model']: _LEGACY_GEOMETRY['sources']}
CHECKER = _GEOMETRY['checker']
ANALYSIS = 'analysis/film/'


def footprint_bytes(raw):
    pins = strict_json((DATA / 'selected-pins.json').read_bytes())
    if sha256(raw).hexdigest() != pins[SUPPLIER]:
        raise ValueError('film footprint requires the exact captured C3778570')
    parsed = easyeda_footprint.parse_package(raw, LCSC, 200)
    pattern = parsed['pattern']
    if (parsed['identity']['manufacturer_part'] != MPN or pattern['holes']
            or sorted(p['number'] for p in pattern['pads']) != ['1', '2']):
        raise ValueError('film supplier identity or complete pad inventory differs')
    rows = [f'(footprint "{FOOTPRINT}" (version 20241229) (generator "agent-ee")',
        '(layer "F.Cu") (attr smd)',
        '(fp_text reference "REF**" (at 0 -2) (layer "F.SilkS") (effects (font (size 0.8 0.8) (thickness 0.12))))',
        f'(fp_text value "{MPN}" (at 0 2) (layer "F.Fab") (effects (font (size 0.8 0.8) (thickness 0.12))))',
        '(fp_rect (start -1.75 -1.4) (end 1.75 1.4) (stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
        '(fp_rect (start -2.45 -1.65) (end 2.45 1.65) (stroke (width 0.05) (type default)) (fill none) (layer "F.CrtYd"))',
        '(fp_line (start -0.7 -1.55) (end 0.7 -1.55) (stroke (width 0.12) (type default)) (layer "F.SilkS"))',
        '(fp_line (start -0.7 1.55) (end 0.7 1.55) (stroke (width 0.12) (type default)) (layer "F.SilkS"))']
    for pad in pattern['pads']:
        p = pad['primitive']
        if pad['layers'] != ['F.Cu'] or p['shape'] != 'rect' or Fraction(p['angle']) != 0:
            raise ValueError('film supplier pad geometry differs')
        x, y = map(acquisition_reverse_blocking._native_mm, p['center'])
        w, h = map(acquisition_reverse_blocking._native_mm, p['size'])
        rows.append(f'(pad "{pad["number"]}" smd rect (at {x} {y}) (size {w} {h}) '
                    '(layers "F.Cu" "F.Paste" "F.Mask"))')
    rows.append('(model "${KIPRJMOD}/../' + MODEL_PATH + '" '
                '(offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))')
    return ('\n'.join(rows) + '\n)\n').encode()


def _parts(files, record):
    manifest = parts_manifest.validate_manifest(strict_json(files['parts.json']))
    decisions = dict(schema='agent-ee.acquisition-film-selections/1', reference=REFERENCE,
        original_parts_sha256=sha256(files['parts.json']).hexdigest(),
        parts=[dict(id=ref, mpn=MPN, lcsc=LCSC, basis='Generated nonpolar film replacement')
               for ref in ('C1', 'C2')], accepted=False)
    decision_path = 'film-reference/decisions.json'
    files[decision_path] = json_bytes(decisions)
    for part in manifest['parts']:
        if part['id'] not in ('C1', 'C2'):
            continue
        for key in ('mpn', 'lcsc', 'manufacturer', 'package', 'stock'):
            part[key] = record[key]
        part['footprint'] = LIBRARY + ':' + FOOTPRINT
        part['datasheet'] = dict(required=True, status='captured', artifact=record['artifact'],
            provenance={**record['datasheets'], 'locator': 'parts[id=' + record['datasheet_id'] + ']'})
        if 'source_selection' in record:
            part['datasheet']['source_selection'] = record['source_selection']
        part['selection'] = dict(kind='named', lcsc=LCSC, part_record=RECORD,
            part_record_sha256=sha256(files[RECORD]).hexdigest(),
            decision_ref=dict(path=decision_path, sha256=sha256(files[decision_path]).hexdigest(),
                              locator='parts[id=' + part['id'] + ']'))
    circuit = strict_json(files['circuit.json'])
    for component in circuit['components']:
        if component['reference'] in ('C1', 'C2'):
            component['value'] = '2.2u'
            component['footprint'] = LIBRARY + ':' + FOOTPRINT
    files['circuit.json'] = json_bytes(circuit)
    assignments = strict_json(files[manifest['cad']['assignments']['path']])
    for row in assignments['components']:
        if row['reference'] in ('C1', 'C2'):
            row['footprint'] = LIBRARY + ':' + FOOTPRINT
    footprint_path = LIBRARY + '.pretty/' + FOOTPRINT + '.kicad_mod'
    files[footprint_path] = footprint_bytes(files[SUPPLIER])
    old_members = {lib + ':' + Path(blob['path']).stem: blob
        for lib, blob in _members(strict_json(files[manifest['cad']['manifest']['path']]))}
    old_members[LIBRARY + ':' + FOOTPRINT] = dict(path=footprint_path,
                                                sha256=sha256(files[footprint_path]).hexdigest())
    libraries = {}
    for footprint in sorted({p['footprint'] for p in manifest['parts']}):
        libraries.setdefault(footprint.split(':')[0], []).append(old_members[footprint])
    cad = dict(schema='agent-ee.footprint-libraries/1', complete=True,
        libraries=[dict(name=name, members=members) for name, members in libraries.items()])
    for key, document in (('assignments', assignments), ('manifest', cad)):
        path = manifest['cad'][key]['path']
        files[path] = json_bytes(document)
        manifest['cad'][key]['sha256'] = sha256(files[path]).hexdigest()
    members = [blob for _, blob in _members(cad)] + [
        {k: lib[k] for k in ('path', 'sha256')} for lib in circuit['libraries']]
    manifest['cad']['members_sha256'] = identity(sorted(members, key=lambda row: row['path']))
    files['parts.json'] = json_bytes(parts_manifest.validate_manifest(manifest))


def sources(base, selected):
    """Generate source bytes before a model run, retaining exact base contracts."""
    base, selected = Path(base), Path(selected)
    pins = strict_json((DATA / 'base-pins.json').read_bytes())
    plan_raw = (base.parent / 'build-plan.json').read_bytes()
    if sha256(plan_raw).hexdigest() != pins['plan_sha256']:
        raise ValueError('film derivation requires the exact ADP build plan')
    files = acquisition_power_prepare._captured(base, pins['files'])
    selected_pins = strict_json((DATA / 'selected-pins.json').read_bytes())
    additions = acquisition_power_prepare._captured(selected, selected_pins)
    record = strict_json(additions[RECORD])
    closure = part_record.qualify_record(record, selected)
    closure[RECORD] = sha256(additions[RECORD]).hexdigest()
    if (closure != {p: h for p, h in selected_pins.items()
                   if p not in (SUPPLIER, 'film-reference/supplier/C3778570.provenance.json', MODEL_PATH)}
            or (record['mpn'], record['lcsc'], record['manufacturer']) != (MPN, LCSC, 'Rubycon')):
        raise ValueError('film part identity or complete source closure differs')
    cpl_rotation.load_suppliers(additions.__getitem__, ['film-reference/supplier/C3778570.provenance.json'])
    additions.update(acquisition_power_prepare._captured(selected,
        strict_json((DATA / 'engineering-pins.json').read_bytes())))
    for path, raw in additions.items():
        if path in files:
            raise ValueError('film source collides with retained source: ' + path)
        files[path] = raw
    _parts(files, record)
    for path, fixture, expected in [
            ('analysis/geometry-model.json', 'geometry-model.json', MODEL),
            ('analysis/geometry_model.py', 'geometry_model.py', CHECKER),
            ('analysis/geometry_contact.py', 'geometry_contact.py', SOURCES['analysis/geometry_contact.py']),
            ('film-reference/geometry-path-basis.json', 'geometry-path-basis.json', SOURCES['film-reference/geometry-path-basis.json']),
            ('film-reference/geometry-basis.json', 'geometry-basis.json', SOURCES['film-reference/geometry-basis.json'])]:
        raw = (DATA / fixture).read_bytes()
        if sha256(raw).hexdigest() != expected:
            raise ValueError('film geometry derivation differs')
        files[path] = raw
    return files, strict_json(plan_raw)


def _power(files, run, film_model):
    generated, application = acquisition_film_analysis.inputs(files['circuit.json'], files['parts.json'],
        (run / 'probe/ADP7104-3.3.sub').read_bytes(), (run / 'probe/ADP7104-3.3.asy').read_bytes(),
        film_model.read_bytes())
    pins_raw = (run / 'input-pins.json').read_bytes()
    pins = strict_json(pins_raw)
    execution_raw = (run / 'execution.json').read_bytes()
    execution = strict_json(execution_raw)
    if (execution['input_pins_sha256'] != sha256(pins_raw).hexdigest()
            or execution['exit_code'] != 0 or execution['timed_out'] or execution['input_changes']
            or any(row.get('exit_code') != 0 for row in execution['cleanup'])):
        raise ValueError('film model did not complete on its unchanged pre-execution inputs')
    for name, expected in [('circuit.json', application['circuit_sha256']),
                           ('parts.json', application['parts_sha256']),
                           ('25MU225MB23225.net', acquisition_film_analysis.RUBYCON_SHA256)]:
        if [pin for path, pin in pins['files'].items() if Path(path).name == name] != [expected]:
            raise ValueError('film model was not bound before execution: ' + name)
    actual_generated = {str(run / 'probe' / name): sha256(raw).hexdigest() for name, raw in generated.items()}
    if pins['generated_files'] != actual_generated:
        raise ValueError('film model pre-execution generated inputs differ')
    for name, raw in generated.items():
        if (run / 'probe' / name).read_bytes() != raw:
            raise ValueError('film model generated input changed: ' + name)
    for suffix in ('raw', 'log'):
        name = 'high-z-removal.' + suffix
        raw = (run / 'probe' / name).read_bytes()
        if sha256(raw).hexdigest() != execution['outputs'][name]['sha256']:
            raise ValueError('film model output changed: ' + name)
        generated[name] = raw
    measured = acquisition_film_analysis.measurements(generated['high-z-removal.raw'], generated['high-z-removal.log'])
    generated.update({'input-pins.json': pins_raw, 'execution.json': execution_raw,
        'executed-application.json': generated['power-application.json'],
        'power_analysis.py': Path(acquisition_power_analysis.__file__).read_bytes()})
    for name in ('power_replay.py', 'power_adapter.py'):
        generated[name] = (DATA / name).read_bytes()
    custody = {key: application[key] for key in ('circuit_sha256', 'parts_sha256', 'selections')}
    custody['files'] = {name: sha256(raw).hexdigest() for name, raw in generated.items()}
    generated['retained-case.json'] = json_bytes(custody)
    # Historical ADP waveforms are not operative evidence for the new purchasing selection.
    for path in list(files):
        if path.startswith(acquisition_power_prepare.ANALYSIS):
            del files[path]
    files.update({ANALYSIS + name: raw for name, raw in generated.items()})
    basis = strict_json(files['power-reference/engineering-basis.json'])
    basis['declaration'] = application
    basis['sources'].update({p: sha256(raw).hexdigest() for p, raw in files.items()
                             if p.startswith('film-reference/engineering/')})
    basis['capacitor'] = ('C1/C2 Rubycon25MU225MB23225,2.2uF+/-20%,25V nonpolar MU acrylic film. '
        'Tolerance minimum1.76uF exceeds ADP0.7uF. Rubycon technical note reports no DC-bias capacitance shift. '
        'MU DF at1kHz does not by itself establish ESR<0.2ohm. Exact20C/DC0V reference RLC values and '
        'the0.8 capacitance sensitivity remain conditional evidence; nominal25C applicability requires judgment.')
    basis['model'] = application['changed_hypothesis'] + ' ' + ' '.join(application['limitations'])
    files['power-reference/engineering-basis.json'] = json_bytes(basis)
    calculation = strict_json(files['engineering-calculations.json'])
    calculation['sources']['power-reference/engineering-basis.json'] = sha256(files['power-reference/engineering-basis.json']).hexdigest()
    calculation['unresolved'] = ['Fresh film capacitor applicability and final-board judgments; supplier preview and physical follow-up.']
    files['engineering-calculations.json'] = json_bytes(calculation)
    files['OPERATING.md'] = files['OPERATING.md'].replace(b'analysis/adp7104/power-application.json', b'analysis/film/power-application.json')
    files['OPERATING.md'] += (
        '\nFilm development reference: C1/C2 are nonpolar Rubycon25MU225MB23225 (C3778570),2.2uF25V. '
        'The previous Samsung capacitor sensitivity is superseded. See analysis/film/power-application.json. '
        'The new model releases the host before disconnecting the source. The host must implement that sequencing. '
        'Reference model values are not product guarantees or physical-validation evidence. '
        'No prior native checks, judgments, source-role qualification or file acceptance apply.\n').encode()
    return measured


def _requirements(files):
    document = strict_json(files['requirement-map.json'])
    pool = typed_overlay._check_pool(document)
    pool['engineering-geometry']['params']['tool']['inputs'].append('analysis/geometry_contact.py')
    catalog = acquisition_power_prepare._catalog(files, (
        'preparation/power-repair/catalog/catalog.json', 'power-reference/u1-stock/catalog.json',
        'power-reference/d1-stock/catalog.json', 'film-reference/catalog/catalog.json'))
    pool['assembly']['params'].update(catalog=catalog, catalog_sha256=sha256(files[catalog]).hexdigest())
    files['cpl/supplier/C3778570.provenance.json'] = files['film-reference/supplier/C3778570.provenance.json']
    selected = {p['lcsc'] for p in strict_json(files['parts.json'])['parts']}
    supplier_paths = ['cpl/supplier/' + lcsc + '.provenance.json' for lcsc in sorted(selected)]
    cpl_rotation.load_suppliers(files.__getitem__, supplier_paths)
    pool['cpl']['params']['supplier_footprints'] = supplier_paths
    replay = deepcopy(pool.pop('adp-power-retained-model'))
    replay['id'] = 'film-power-retained-model'
    params = replay['params']
    params['adapter'] = ANALYSIS + 'power_adapter.py'
    params['tool']['argv'][1] = '/candidate/' + ANALYSIS + 'power_replay.py'
    params['tool']['inputs'] = ['circuit.json', 'parts.json'] + sorted(p for p in files if p.startswith(ANALYSIS))
    for row in params['limits'].values():
        row['condition'] = acquisition_film_analysis.CONDITION
    for name in ('pre_removal_host', 'disconnected_source'):
        params['limits'][name] = dict(max=1e-12, unit='A', condition=acquisition_film_analysis.CONDITION)
    pool[replay['id']] = replay
    briefs = strict_json(files['preparation/judgment-briefs.json'])
    for brief in briefs.values():
        brief['questions'][0] = 'Evaluate the exact authored span on the fresh film-capacitor candidate; no previous verdict applies.'
        brief['questions'].append('C1/C2 are Rubycon MU nonpolar film. Assess nominal25C applicability of source capacitance and ESR evidence; '
            'distinguish the20C/DC0V reference model from a guarantee. Preserve the original scope rather than inventing universal lot/lifetime requirements.')
        brief['sources'] = [ANALYSIS + 'power-application.json' if p.startswith(acquisition_power_prepare.ANALYSIS) else p
                            for p in brief['sources']]
        brief['sources'].append('film-reference/geometry-basis.json')
        brief['sources'].append('film-reference/geometry-path-basis.json')
        brief['sources'] += ['film-reference/engineering/' + p for p in (
            'host-extraction-r2/MU.txt', 'technical-note.txt', 'model-cautions.txt',
            'impedance-observation.json', 'import.json')]
    def revise(row):
        if 'checks' in row:
            row['checks'] = [deepcopy(pool['film-power-retained-model' if c['id'] == 'adp-power-retained-model' else c['id']])
                             for c in row['checks']]
        for span in row.get('spans', []):
            revise(span['mapping'])
    for row in document['requirements'] + document['prose']:
        revise(row)
    document['scope']['name'] = 'Generated ADP and MU film acquisition development reference'
    document['scope']['scope'] = ('Distinct unaccepted film-capacitor reference at nominal25C under OPERATING.md. '
        'All authored native/functional/manufacturing obligations retained. Model replay is conditional evidence only. '
        'Fresh engineering judgments and full review required; no frozenG4 reference replacement authorized.')
    names = sorted({p.split('/')[0] for p in files} | {'placement-mechanics.json', 'acquisition-block-source.json'})
    document['scope']['inputs'] = [dict(path=p, at=p) for p in names
                                 if p not in ('requirement-map.json', 'spec-record.json', 'obligations.json')]
    files['requirement-map.json'] = json_bytes(document)
    files['preparation/judgment-briefs.json'] = json_bytes(briefs)


def prepare(base, selected, model_run, film_model, out):
    base, selected, model_run, film_model, out = map(Path, (base, selected, model_run, film_model, out))
    if out.exists() or not out.parent.is_dir():
        raise ValueError('output must be new under an existing parent')
    typed_overlay._disjoint('output', out.resolve(), dict(base=base.resolve(), selected=selected.resolve(),
        model_run=model_run.resolve(), film_model=film_model.resolve()))
    files, plan = sources(base, selected)
    measurements = _power(files, model_run, film_model)
    _requirements(files)
    inventory, accounting = acquisition_power_prepare.materialize(files, plan, out, REFERENCE)
    report = dict(schema='agent-ee.acquisition-film-preparation/1', reference=REFERENCE,
        files=inventory, accounting=accounting, model_measurements=measurements,
        accepted=False, file_release='not_assessed', remaining=[
            'Native build and all required checks', 'Fresh nominal25C engineering judgments and full review',
            'Reusable source-role qualification and direct owner decision before frozenG4 replacement'])
    (out / 'preparation.json').write_bytes(json_bytes(report))
    return report

"""Finite C1/C2 film-capacitor derivation of the unaccepted ADP reference.

The ordinary parts, native CAD and placement checks still decide whether the
generated source is usable. Captured identity is not electrical qualification.
"""
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
from pathlib import Path

from . import (acquisition_film_analysis, acquisition_film_removal, acquisition_power_analysis, acquisition_power_prepare,
               acquisition_reverse_blocking, cpl_rotation, easyeda_footprint,
               jlc_stock_capture, part_record, parts_manifest, typed_overlay)
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
_PREVIOUS_GEOMETRY = strict_json((DATA / 'geometry-sources-r2.json').read_bytes())
_GROUND_GEOMETRY = strict_json((DATA / 'geometry-sources-r3.json').read_bytes())
MODEL = _GEOMETRY['model']
SOURCES = _GEOMETRY['sources']
MODELS = {row['model']:row['sources'] for row in (_GEOMETRY, _GROUND_GEOMETRY, _PREVIOUS_GEOMETRY, _LEGACY_GEOMETRY)}
CHECKERS = {row['model']:row['checker'] for row in (_GEOMETRY, _GROUND_GEOMETRY, _PREVIOUS_GEOMETRY)}
CHECKER = _GEOMETRY['checker']
ANALYSIS = 'analysis/film/'
STOCK = 'film-reference/assembly-stock-refresh/catalog.json'
C3_MPN = 'CC0603KRX7R9BB104'
C3_ASSEMBLY = 'film-reference/c3-assembly/'


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
            ('analysis/ground_reference.py', 'ground_reference.py', SOURCES['analysis/ground_reference.py']),
            ('analysis/geometry_adapter.py', 'geometry_adapter.py', SOURCES['analysis/geometry_adapter.py']),
            ('film-reference/ground-return-basis.md', 'ground-return-basis.md', SOURCES['film-reference/ground-return-basis.md']),
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
    supplement = 'operating-supplement/ADS1115-OPERATING.md'
    files['OPERATING.md'] += (
        '\n## ADC timing supplement adoption\n\n'
        f'This current operating contract adopts `{supplement}`, SHA-256 '
        f'`{sha256(files[supplement]).hexdigest()}`. Its parent operating-document '
        'digest identifies the historical source, not this generated revision. '
        'Apply its power-up communication, conversion-readiness and timing '
        'corrections to the host instructions above. All other operating '
        'conditions remain in force; no physical host validation is claimed.\n').encode()
    return measured


def _stock_catalog(files, root):
    """Attach a complete raw-backed observation without rewriting part identities."""
    root = Path(root)
    raw = (root/'catalog.json').read_bytes()
    captured = acquisition_power_prepare._captured(root, {'catalog.json':sha256(raw).hexdigest()})
    catalog = strict_json(raw)
    def read_response(path):
        requests = [row for row in catalog['requests']
                    if isinstance(row.get('response'),dict) and row['response'].get('path') == path]
        if len(requests) != 1:
            raise ValueError('fresh assembly catalog response coverage differs')
        additions = acquisition_power_prepare._captured(root,
            {path:requests[0]['response']['sha256']})
        captured.update(additions)
        return additions[path]
    jlc_stock_capture.validate_capture(catalog, read_response)
    if catalog['source']['endpoint'] != jlc_stock_capture.DEFAULT_ENDPOINT:
        raise ValueError('fresh assembly catalog requires the public JLC source')
    parts = strict_json(files['parts.json'])['parts']
    if set(catalog['parts']) != {part['lcsc'] for part in parts}:
        raise ValueError('fresh assembly catalog must cover exactly the selected LCSC identities')
    for part in parts:
        row = catalog['parts'][part['lcsc']]
        if any(row.get(key) != part[key] for key in ('lcsc','mpn','package')):
            raise ValueError('fresh assembly catalog changes selected identity: ' + part['id'])
    prefix = str(Path(STOCK).parent) + '/'
    additions = {prefix+path:data for path,data in captured.items()}
    if set(additions) & set(files):
        raise ValueError('fresh assembly stock collides with retained source')
    files.update(additions)


def _c3_assembly_source(files, root):
    """Attach the pinned series specification; it adds evidence and changes no selection."""
    c3 = next(part for part in strict_json(files['parts.json'])['parts'] if part['id'] == 'C3')
    if c3['mpn'] != C3_MPN:
        raise ValueError('C3 assembly source applies to a different selected part')
    pins = strict_json((DATA / 'c3-assembly-pins.json').read_bytes())
    additions = {C3_ASSEMBLY + name: raw
                 for name, raw in acquisition_power_prepare._captured(Path(root), pins).items()}
    if set(additions) & set(files):
        raise ValueError('C3 assembly source collides with retained source')
    files.update(additions)


def _requirements(files):
    document = strict_json(files['requirement-map.json'])
    pool = typed_overlay._check_pool(document)
    pool['engineering-geometry']['params']['tool']['inputs'].append('analysis/geometry_contact.py')
    pool['engineering-geometry']['params']['tool']['inputs'].append('analysis/ground_reference.py')
    for name,limit,unit in [('ground_saved_net_changes',0,'count'),('ground_transition_missing',0,'count'),('ground_corridor_missing',0,'count'),
                          ('ground_plane_components',1,'count'),('ground_return_path',3,'mm')]:
        pool['engineering-geometry']['params']['limits'][name]=dict(
            max=limit,unit=unit,condition='nominal25C-reviewed-acquisition')
    pool['engineering-geometry']['params']['limits']['ground_plane_components']['min']=1
    pool['engineering-geometry']['params']['limits']['plated_ring_minimum']=dict(
        min=.18,unit='mm',condition='nominal25C-reviewed-acquisition')
    catalog = acquisition_power_prepare._catalog(files, (
        'preparation/power-repair/catalog/catalog.json', 'power-reference/u1-stock/catalog.json',
        'power-reference/d1-stock/catalog.json', 'film-reference/catalog/catalog.json'))
    if STOCK in files:
        catalog = STOCK
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
    removal = None
    if acquisition_film_removal.AT + 'evidence-manifest.json' in files:
        removal = acquisition_film_removal.check(replay, files)
        pool[removal['id']] = removal
    briefs = strict_json(files['preparation/judgment-briefs.json'])
    for brief in briefs.values():
        brief['questions'][0] = 'Evaluate the exact authored span on the fresh film-capacitor candidate; no previous verdict applies.'
        brief['questions'].append('C1/C2 are Rubycon MU nonpolar film. Assess nominal25C applicability of source capacitance and ESR evidence; '
            'distinguish the20C/DC0V reference model from a guarantee. Preserve the original scope rather than inventing universal lot/lifetime requirements.')
        brief['sources'] = [ANALYSIS + 'power-application.json' if p.startswith(acquisition_power_prepare.ANALYSIS) else p
                            for p in brief['sources']]
        brief['sources'].append('film-reference/geometry-basis.json')
        brief['sources'].append('film-reference/geometry-path-basis.json')
        if brief['requirement'] in ('ACQ-ELEC-4-ERROR-EVALUATION','ACQ-ELEC-4-FILTER-SCOPE',
                                     'ACQ-MECH-3-RETURN-PATHS','ACQ-MECH-3-LIMIT-RATIONALE'):
            brief['sources'].append('film-reference/ground-return-basis.md')
            brief['questions'].append(
                'Evaluate the fresh final-board ground-reference measurements and conditional DC/RF model. '
                'ADC and filters have generated local plane ties; the check must witness native copper '
                'corridors to both adjacent J3 grounds. Account for board-ground error explicitly. '
                'ADS1115 sinks SDA but never SCL; host sink currents return at J2.1. '
                'Do not turn material/plating assumptions, 25mA stress sensitivity, or selected RF parasitics '
                'into guaranteed accuracy or attenuation. No original operating condition changes.')
        if brief['requirement'] == 'ACQ-MECH-4-ASSEMBLY-APPLICABILITY' and C3_ASSEMBLY + 'datasheet.txt' in files:
            brief['sources'] += [C3_ASSEMBLY + 'capture.json', C3_ASSEMBLY + 'datasheet.txt']
            brief['questions'].append(
                'The exact C3 CC0603KRX7R9BB104 sheet gives no assembly-process limits. Evaluate the attached YAGEO '
                'X7R series specification against that exact part code, including its ordering code, rated voltage, '
                'size, soldering recommendation, soldering-heat and solderability tests and MSL statement. '
                'State what a series specification does not establish about a supplier process.')
        brief['sources'] += ['film-reference/engineering/' + p for p in (
            'host-extraction-r2/MU.txt', 'technical-note.txt', 'model-cautions.txt',
            'impedance-observation.json', 'import.json')]
        if 'power-reference/loop-applicability.md' in files:
            brief['sources'].append('power-reference/loop-applicability.md')
            if brief['requirement'] == 'ACQ-ELEC-2-SOURCE-EVALUATION':
                brief['sources'] += ['analysis/film-loop/' + p for p in (
                    'pilot/result.json', 'comparison/result.json', 'comparison-exact/result.json', 'REPRODUCE.md')]
                brief['questions'].append(
                    'Evaluate the new dual-injection loop evidence and its actual model scope. '
                    'The final film cases remove implicit inductor resistance and retain the original '
                    'datasheet ESR statements. Assess whether this resolves nominal application '
                    'compatibility; model predicates alone do not constitute engineering acceptance.')
        if removal is not None and brief['requirement'] == 'ACQ-ELEC-2-SOURCE-EVALUATION':
            brief['sources'] += ['power-reference/removal-bounds.md',
                                 'analysis/film-removal/summary.json']
            brief['questions'].append(
                'Evaluate independent C1/C2 low/high removal sensitivities at both input limits, '
                'with explicit zero inductor Rser and the unchanged host/source sequence. '
                'Use the fresh film-independent-removal check and retained raw traces. '
                'Distinguish model evidence from guaranteed product corners or physical validation.')
    def revise(row):
        if 'checks' in row:
            checks = []
            for check in row['checks']:
                selected = 'film-power-retained-model' if check['id'] == 'adp-power-retained-model' else check['id']
                checks.append(deepcopy(pool[selected]))
                if removal is not None and selected == 'film-power-retained-model':
                    checks.append(deepcopy(removal))
            row['checks'] = checks
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


def _loop_evidence(files, root):
    """Attach a fixed, reproducible model investigation without promoting its verdict."""
    declaration = strict_json((DATA / 'loop-evidence.json').read_bytes())
    for name in ('circuit', 'parts'):
        if sha256(files[name + '.json']).hexdigest() != declaration[name + '_sha256']:
            raise ValueError('loop evidence requires the exact generated ' + name)
    additions = acquisition_power_prepare._captured(root, declaration['files'])
    if set(additions) & set(files):
        raise ValueError('loop evidence collides with existing candidate inputs')
    pilot = strict_json(additions['analysis/film-loop/pilot/result.json'])
    final = strict_json(additions['analysis/film-loop/comparison-exact/result.json'])
    if (not pilot['suitable_for_conditional_comparison']
            or not final['all_named_model_predicates_pass'] or final['case_count'] != 12
            or final['accepted'] or final['file_release'] != 'not_assessed'):
        raise ValueError('loop evidence does not establish the declared limited model result')
    files.update(additions)
    files['analysis/film-loop/evidence-manifest.json'] = json_bytes(declaration)
    document = 'power-reference/loop-applicability.md'
    basis = strict_json(files['power-reference/engineering-basis.json'])
    basis['loop_applicability'] = dict(path=document, sha256=sha256(files[document]).hexdigest(),
        status='Nominal application assessment awaiting fresh independent judgment; not physical validation.')
    basis['sources'][document] = sha256(files[document]).hexdigest()
    files['power-reference/engineering-basis.json'] = json_bytes(basis)
    calculations = strict_json(files['engineering-calculations.json'])
    calculations['sources']['power-reference/engineering-basis.json'] = sha256(files['power-reference/engineering-basis.json']).hexdigest()
    calculations['sources'][document] = sha256(files[document]).hexdigest()
    files['engineering-calculations.json'] = json_bytes(calculations)
    files['OPERATING.md'] += (
        '\nNominal power applicability: see power-reference/loop-applicability.md and '
        'analysis/film-loop/REPRODUCE.md for the retained loop-response investigation. '
        'It changes no operating condition and claims no physical validation. '
        'Fresh candidate-bound engineering judgment remains required.\n').encode()


def prepare(base, selected, model_run, film_model, out, *, loop_evidence=None, removal_evidence=None,
            assembly_catalog=None, c3_assembly=None):
    base, selected, model_run, film_model, out = map(Path, (base, selected, model_run, film_model, out))
    if out.exists() or not out.parent.is_dir():
        raise ValueError('output must be new under an existing parent')
    roots = dict(base=base.resolve(), selected=selected.resolve(),
        model_run=model_run.resolve(), film_model=film_model.resolve())
    if loop_evidence is not None:
        roots['loop_evidence'] = Path(loop_evidence).resolve()
    if removal_evidence is not None:
        roots['removal_evidence'] = Path(removal_evidence).resolve()
    if assembly_catalog is not None:
        roots['assembly_catalog'] = Path(assembly_catalog).resolve()
    if c3_assembly is not None:
        roots['c3_assembly'] = Path(c3_assembly).resolve()
    typed_overlay._disjoint('output', out.resolve(), roots)
    files, plan = sources(base, selected)
    measurements = _power(files, model_run, film_model)
    if loop_evidence is not None:
        _loop_evidence(files, Path(loop_evidence))
    if removal_evidence is not None:
        acquisition_film_removal.attach(files, Path(removal_evidence))
    if assembly_catalog is not None:
        _stock_catalog(files, Path(assembly_catalog))
    if c3_assembly is not None:
        _c3_assembly_source(files, Path(c3_assembly))
    _requirements(files)
    inventory, accounting = acquisition_power_prepare.materialize(files, plan, out, REFERENCE)
    report = dict(schema='agent-ee.acquisition-film-preparation/1', reference=REFERENCE,
        files=inventory, accounting=accounting, model_measurements=measurements,
        accepted=False, file_release='not_assessed', remaining=[
            'Native build and all required checks', 'Fresh nominal25C engineering judgments and full review',
            'Reusable source-role qualification and direct owner decision before frozenG4 replacement'])
    (out / 'preparation.json').write_bytes(json_bytes(report))
    return report

"""Prepare the distinct, unaccepted ADP development reference from exact sources.

No native CAD, judgment, source-role qualification or model run is inherited.
The ordinary typed build, parts compiler and native generators consume the result.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from . import (acquisition_block_source, acquisition_mechanics, acquisition_power_analysis,
               acquisition_power_geometry, acquisition_power_parts, acquisition_reverse_blocking,
               build_workflow, cpl_rotation, jlc_stock_capture, parts_manifest, requirement_map,
               spec_record, typed_overlay)
from .catalog import strict_json
from .footprint_library import _members
from .storage import identity, json_bytes

DATA = acquisition_reverse_blocking.DATA
REFERENCE = acquisition_reverse_blocking.REFERENCE
ANALYSIS = 'analysis/adp7104/'


def _captured(root, pins):
    return {p: parts_manifest._read_blob(root, dict(path=p, sha256=h)) for p, h in pins.items()}


def _models(files):
    """Attach captured project models without changing any copper or pad geometry."""
    selections = acquisition_reverse_blocking.SELECTIONS
    model = {'U1': 'Package_SO.3dshapes/SOIC-8_3.9x4.9mm_P1.27mm.step',
             'D1': 'Diode_SMD.3dshapes/D_SOD-123.step'}
    manifest = strict_json(files['parts.json'])
    cad_path = manifest['cad']['manifest']['path']
    cad = strict_json(files[cad_path])
    for ref, name in model.items():
        footprint = selections[ref]['footprint']
        library, member = footprint.split(':')
        path = library + '.pretty/' + member + '.kicad_mod'
        raw = files[path].decode()
        if '(model ' in raw or not raw.rstrip().endswith(')') or 'lib3d/' + name not in files:
            raise ValueError('new power footprint/model input differs: ' + ref)
        files[path] = (raw.rstrip()[:-1] + '\n(model "${KIPRJMOD}/../lib3d/' + name + '" '
            '(offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))\n)\n').encode()
        for lib, blob in _members(cad):
            if blob['path'] == path:
                blob['sha256'] = sha256(files[path]).hexdigest()
    files[cad_path] = json_bytes(cad)
    manifest['cad']['manifest']['sha256'] = sha256(files[cad_path]).hexdigest()
    circuit = strict_json(files['circuit.json'])
    members = [blob for _, blob in _members(cad)] + [
        {k: lib[k] for k in ('path', 'sha256')} for lib in circuit['libraries']]
    manifest['cad']['members_sha256'] = identity(sorted(members, key=lambda row: row['path']))
    record = spec_record.verify(files['spec-record.json'], files['spec.md'])
    membership = next(r for r in record['requirements'] if r['id'] == 'ACQ-NATIVE-4')
    citation = manifest['declaration_source']
    if (citation['path'] != 'spec.md' or citation['sha256'] != sha256(files['spec.md']).hexdigest()
            or strict_json(citation['locator'].encode()) != membership['locator']):
        raise ValueError('parts declaration differs from authored membership clause')
    # The normal first build stage creates this exact provenance copy before
    # parts qualification; the public parts-only producer keeps its own path.
    citation['path'] = requirement_map.PROVENANCE + '/spec.md'
    files['parts.json'] = json_bytes(parts_manifest.validate_manifest(manifest))


def _locality(files):
    text = acquisition_block_source.engineering_text(reference=REFERENCE)
    path = acquisition_block_source.ENGINEERING_PATH
    document = strict_json(files['electrical-locality.json'])
    for row in document['obligations']:
        if row['id'] in acquisition_block_source.ADDED_LEGS:
            leg = row['leg']
            a, b = leg['from'], leg['to']
            quote = f"{a['reference']}.{a['pad']} to {b['reference']}.{b['pad']} pad distance shall be at most {leg['max_distance_nm'] // 1000000} mm."
            if quote not in text.decode().splitlines():
                raise ValueError('generated placement endpoint differs from finite recipe')
            leg['citation'] = quote
            leg['provenance']['citation'].update(path=path, sha256=sha256(text).hexdigest(), quote=quote)
            leg['provenance']['basis'] = 'Root706 adopted distance retained; agent707 derived ADP endpoint, 2026-11-01; engineering choice.'
    document['source_records'][path] = text.decode()
    files[path] = text
    files['electrical-locality.json'] = json_bytes(document)
    placement = strict_json(files['placement.json'])
    placement['electrical_locality'] = document
    files['placement.json'] = json_bytes(placement)


def _catalog(files):
    paths = ['preparation/power-repair/catalog/catalog.json',
             'power-reference/u1-stock/catalog.json', 'power-reference/d1-stock/catalog.json']
    parents = [strict_json(files[path]) for path in paths]
    contracts = [{k: v for k, v in p['source'].items() if k != 'min_interval_seconds'} for p in parents]
    if any(c != contracts[0] for c in contracts):
        raise ValueError('assembly catalog source contracts differ')
    combined = dict(schema=jlc_stock_capture.SCHEMA,
        as_of=min((p['as_of'] for p in parents), key=lambda t: jlc_stock_capture._utc(t, 'as_of')),
        completed_at=max((p['completed_at'] for p in parents), key=lambda t: jlc_stock_capture._utc(t, 'completed_at')),
        stale_before=None, source={**parents[0]['source'],
            'min_interval_seconds': min(p['source']['min_interval_seconds'] for p in parents)},
        requests=[], parts={})
    target = 'power-reference/assembly-stock/catalog.json'
    for path, parent in zip(paths, parents):
        prefix = str(Path(path).parent) + '/'
        jlc_stock_capture.validate_capture(parent, lambda p: files[prefix + p])
        if set(combined['parts']) & set(parent['parts']):
            raise ValueError('assembly catalog parent observations overlap')
        combined['parts'].update(parent['parts'])
        for request in parent['requests']:
            rel = request['response']['path']
            files['power-reference/assembly-stock/' + rel] = files[prefix + rel]
        combined['requests'] += parent['requests']
    files[target] = json_bytes(combined)
    files['power-reference/assembly-stock/derivation.json'] = json_bytes(dict(
        kind='Offline conservative union; NOT a fresh capture. Parent pacing metadata retained in parents; union declares their minimum, never a stronger pacing promise.',
        parents=[dict(path=p, sha256=sha256(files[p]).hexdigest()) for p in paths]))
    return target


def _cpl(files, context, pool):
    selected = {p['lcsc'] for p in strict_json(files['parts.json'])['parts']}
    for ref in ('U1', 'D1'):
        part = acquisition_reverse_blocking.SELECTIONS[ref]
        path = context['supplier'][ref]['path']
        lcsc = part['lcsc']
        files['cpl/supplier/' + lcsc + '.provenance.json'] = json_bytes(dict(
            schema='agent-ee.supplier-footprint/1', format='easyeda-std-component/1', lcsc=lcsc,
            path=path, sha256=sha256(files[path]).hexdigest(), size_bytes=len(files[path]),
            retrieved_at='2026-11-01T06:18:05.395550+00:00',
            source_url='https://easyeda.com/api/products/' + lcsc + '/components?version=6.4.19.5',
            retrieval='Offline copy of exact retained supplier result; timestamp is build-source preparation, not a new HTTP fetch.'))
    paths = ['cpl/supplier/' + lcsc + '.provenance.json' for lcsc in sorted(selected)]
    suppliers = cpl_rotation.load_suppliers(files.__getitem__, paths)
    record = strict_json(files[context['parts']['D1']['path']])
    polarity = dict(schema='agent-ee.component-polarity/1', lcsc='C155367', mpn='B0520LW-7-F',
        manufacturer='Diodes Incorporated', supplier_capture_sha256=suppliers['C155367']['sha256'],
        pin_polarity={'1': 'cathode', '2': 'anode'},
        manufacturer_source=dict(path=record['artifact']['path'], sha256=record['artifact']['sha256'],
            page=1, locator='Mechanical Data, polarity statement; DS30051 Rev18-2', statement='Polarity: Cathode Band'),
        package_feature_ids=['gge1059', 'gge1003', 'gge1002'],
        symbol_feature_ids=['gge17', 'gge18', 'gge10', 'gge3'],
        rationale='Engineering interpretation of captured supplier CAD: full-height band gge1059 at x3997.1781 is adjacent to pad1 gge1003 at x3993.563 rather than pad2 gge1002 at x4006.437. Symbol cathode bar gge17 at x395 is on pin1 gge10 at x380; triangle gge18 points toward that bar. Manufacturer identifies the cathode band. Native Device:D_Schottky separately identifies pin1 as K.',
        limitation='Supplier drawing fidelity is assumed; manufacturer does not number terminals here. Supplier placement preview remains pending.')
    cpl_rotation.validate_record(polarity, suppliers, files.__getitem__)
    path = 'cpl/polarity/C155367.polarity.json'
    files[path] = json_bytes(polarity)
    pool['cpl']['params'].update(supplier_footprints=paths, polarity_evidence=[path])
    # Native top-view classification measured on the generated board is
    # supplier bottomside_left -> board leftside_top: CPL offset270deg.
    policy = strict_json(files['cpl/jlc-cpl-policy.json'])
    policy['footprint_rules'] = [r for r in policy['footprint_rules'] if r['id'] != 'sot-23-5-180']
    policy['footprint_rules'].append(dict(id='adp7104-soic-ep-270',
        footprint_prefix=acquisition_reverse_blocking.FOOTPRINT, rotation_degrees=270))
    policy['provenance'] += ' ADP development reference: generated native U1 top-view frame leftside_top versus supplier bottomside_left gives CPL offset270deg. D1 is leftside_top in both frames and needs0deg. Fresh native CPL assessment remains required.'
    files['cpl/jlc-cpl-policy.json'] = json_bytes(policy)
    profile = strict_json(files['cpl/assembly-profile.json'])
    profile['policy_sha256'] = sha256(files['cpl/jlc-cpl-policy.json']).hexdigest()
    profile['sources'] = ['cpl/jlc-cpl-policy.json', 'Captured supplier footprints and new D1 polarity evidence; fresh native CPL assessment required.']
    files['cpl/assembly-profile.json'] = json_bytes(profile)


def _power(files):
    generated, declaration = acquisition_power_analysis.inputs(files['circuit.json'], files['parts.json'],
        files[ANALYSIS + 'ADP7104-3.3.sub'], files[ANALYSIS + 'ADP7104-3.3.asy'])
    custody = strict_json((DATA / 'retained-case.json').read_bytes())
    for name, raw in generated.items():
        if name != 'power-application.json' and sha256(raw).hexdigest() != custody['files'][name]:
            raise ValueError('operative power model/deck differs from retained execution')
        files[ANALYSIS + name] = raw
    files[ANALYSIS + 'retained-case.json'] = json_bytes(custody)
    files[ANALYSIS + 'power_analysis.py'] = Path(acquisition_power_analysis.__file__).read_bytes()
    for name in ('power_replay.py', 'power_adapter.py'):
        files[ANALYSIS + name] = (DATA / name).read_bytes()
    document = strict_json(files['analysis/electrical-intervals.json'])
    values = dict(diode_drop=('.385', '.385'), diode_reverse_leak=('.00025', '.00025'),
        iq_allowance=('.002', '.002'), input_bleed_r=('990', '1010'), output_bleed_r=('2178', '2222'),
        reg_input_floor=('4.333', '4.333'), reg_input_absmax=('22', '22'),
        reg_output_low=('3.25875', '3.25875'), reg_output_high=('3.333', '3.333'))
    for name, (low, high) in values.items():
        row = document['parameters'][name]
        row.update(min=low + ' ' + row['unit'], max=high + ' ' + row['unit'])
    # 0.105W is the external input-power ceiling, not a package thermal rating.
    thermal = next(a for a in document['assertions'] if a['id'] == 'u1-dissipation-25c-screen')
    thermal['id'] = 'conditional-u1-power-below-source-ceiling'
    thermal['target']['max'] = '0.105 W'
    files['analysis/electrical-intervals.json'] = json_bytes(document)
    basis = dict(schema='agent-ee.acquisition-power-basis/1', accepted=False,
        sources={p: sha256(files[p]).hexdigest() for p in [*acquisition_power_geometry.SOURCES,
                 'power-reference/ADI_LinearRegulators_Release.xlsm', 'power-reference/adi-full-table-selected.json']
                 if p in files},
        declaration=declaration,
        input='B0520LW .385V maximum at0.5A/25C used conditionally at<=20mA by monotonic forward-bias inference; 10mV wire allowance gives4.355V. ADP accuracy requires VIN>=VOUT+1V;4.333V using3.333V high output leaves22mV.',
        rail='ADP RevI Table1 0..85C accuracy -1.25%/+1% with >=1mA load gives3.25875..3.333V; retained16.5mV wiring budget leaves3.24225V,8.25mV above3.234V. Fresh thermal/route applicability judgment required.',
        current='R5=1k1%,R6=2.2k1%; retained1mA engineering reserve is not external-load permission. ADI_LinearRegulators_Release.xlsm xl/worksheets/sheet6.xml row533 supplies2mA dropout IGND as a design-tool allowance, conditional and not manufacturer maximum; macros not executed. Worst declared budget13.239394mA. R6 alone supplies>=1.4554455mA at3.234V.',
        reverse='D1 250uA maximum25C reverse leakage at20V is conditionally applied at5.25V by monotonic reverse-bias inference; R5max1010ohm gives0.2525V<0.3V. ADP reverse blocking removes the TPS reverse-differential restriction; its55mV threshold is typical, not a clamp guarantee.',
        thermal='RevI Table6 SOIC minimum-pin-trace25mm2 typical thetaJA167.8C/W. Entire input20mA*5.25V=.105W dissipated in U1 would imply17.619C rise, TJ42.619C atTA25C under that reference thermal model. This is a conservative energy screen with typical thermal transfer, not final-board qualification; actual pad9/plane and0..85C accuracy applicability need fresh judgment.',
        capacitor='Retained Samsung typical bias/ESR curves support sensitivity inputs only; no universal lot/lifetime/temp bound. Encrypted-model replay remains sampled evidence. Require fresh nominal25C capacitor, stability and removal applicability judgment.',
        model='High-Z model uses zero-target non-sinking source that can clamp negativeVIN; not physical disconnection. Host release is concurrent in model; external pre-off sequencing remains an operating obligation.',
        source_roles='No prior TPS cohort qualification inherited. Qualify new reusable roles from this new design before adaptation/G4 credit.')
    files['power-reference/engineering-basis.json'] = json_bytes(basis)
    calc = strict_json(files['engineering-calculations.json'])
    prefixes = ('adc_', 'i2c_', 'filter_', 'finished_', 'input_filter_', 'maximum_conversion_', 'minimum_conversion_')
    calc['values'] = {k: v for k, v in calc['values'].items() if k.startswith(prefixes)}
    calc['sources'] = {p: sha256(files[p]).hexdigest() for p in calc['sources'] if p in files
                       and not p.startswith(('power-repair/', 'analysis/power-'))}
    calc['sources']['power-reference/engineering-basis.json'] = sha256(files['power-reference/engineering-basis.json']).hexdigest()
    calc['unresolved'] = ['Fresh ADP applicability and final-board judgments; supplier preview and physical follow-up.']
    files['engineering-calculations.json'] = json_bytes(calc)
    operating = files['OPERATING.md'].decode().split('\n470 margin repair')[0]
    operating += '\nADP development reference; no file acceptance yet. U1 ADP7104ARDZ-3.3-R7, D1 B0520LW-7-F, R5 1k and R6 2.2k. See power-reference/engineering-basis.json and analysis/adp7104/power-application.json for the new conditional power evidence. J2 pin4 external load remains zero. Retained1mA reserve grants no load permission. No prior TPS model or source-role acceptance applies.\n'
    operating += '\nADC applicability:128SPS remains operative. The8SPS gain/offset/INL conditions do not establish a128SPS maximum. The62.5uVrms128SPS noise is typical and includes quantization; no separate RMS term or guaranteed total accuracy is claimed.\n'
    files['OPERATING.md'] = operating.encode()


def _requirements(files, context):
    document = strict_json(files['requirement-map.json'])
    document['requirements'] = [r for r in document['requirements']
        if not r['id'].startswith(('analog-', 'host-', 'power-'))]
    pool = typed_overlay._check_pool(document)
    for key in list(pool):
        if key.startswith('power-'):
            del pool[key]
    catalog = _catalog(files)
    pool['assembly']['params'].update(catalog=catalog, catalog_sha256=sha256(files[catalog]).hexdigest())
    _cpl(files, context, pool)
    condition = 'adp7104-25c-highz-zero-target-model'
    limits = {'steady_vout_min': dict(min=3.234, unit='V'), 'steady_vout_max': dict(max=3.366, unit='V'),
        'vout_max': dict(max=3.366, unit='V'), 'vout_min': dict(min=-.3, unit='V'),
        'vin_min': dict(min=-.3, unit='V'),
        'vin_max': dict(max=22, unit='V'), 'source_max': dict(max=.020000001, unit='A'),
        'source_min': dict(min=-1e-9, unit='A'),
        'host_current_on_removal': dict(max=1e-12, unit='A'),
        'reserve_current_on_removal': dict(max=1e-12, unit='A'),
        'source_power_on_removal': dict(max=0, unit='W')}
    for row in limits.values():
        row['condition'] = condition
    replay = dict(id='adp-power-retained-model', kind='measurements', stage='verification', params=dict(
        adapter=ANALYSIS + 'power_adapter.py', parameters={}, limits=limits, timeout_s=60,
        tool=dict(argv=['/usr/bin/python3', '/candidate/' + ANALYSIS + 'power_replay.py',
                        '/candidate', '/work/model-measurements.json'],
            sha256=pool['engineering-geometry']['params']['tool']['sha256'], domain='electrical',
            inputs=['circuit.json', 'parts.json'] + sorted(p for p in files if p.startswith(ANALYSIS)))))
    pool[replay['id']] = replay
    briefs = {}
    def revise(row):
        if 'checks' in row:
            before = row['checks']
            row['checks'] = [deepcopy(pool[c['id']]) for c in before if c['id'] in pool]
            if any(c['id'].startswith('power-') for c in before):
                row['checks'].append(deepcopy(replay))
        if 'judgment' in row:
            briefs[row['judgment']['record']] = dict(requirement=row['id'],
                questions=['Evaluate the exact authored span on the fresh ADP candidate; no previous verdict applies.',
                    'For power/temperature/return claims, distinguish conditional manufacturer-source calculations, sampled encrypted-model sensitivity and final native geometry. Address capacitor/ESR, headroom, minimum load, thermal pad/plane, source termination and actual return paths without inventing a lifetime/lot/temperature requirement.'],
                sources=['spec.md', 'OPERATING.md', 'engineering-calculations.json',
                         'power-reference/engineering-basis.json', 'analysis/adp7104/power-application.json'])
        for span in row.get('spans', []):
            revise(span['mapping'])
            if 'judgment' in span['mapping']:
                briefs[span['mapping']['judgment']['record']]['span'] = [span['byte_start'], span['byte_end']]
    for row in document['requirements'] + document['prose']:
        revise(row)
    scope = document['scope']
    scope.pop('block_instances')
    scope['name'] = 'Generated ADP acquisition development reference'
    scope['scope'] = 'Distinct unaccepted ADP reference at nominal25C under OPERATING.md; all authored native/functional/manufacturing obligations retained. Retained waveform replay is model evidence only. Fresh candidate judgments and full review required; new source-role qualification remains outstanding.'
    # Capture every actual top-level source namespace; native CAD is generated later.
    names = sorted({p.split('/')[0] for p in files} | {'placement-mechanics.json', 'acquisition-block-source.json'})
    scope['inputs'] = [dict(path=p, at=p) for p in names if p not in ('requirement-map.json', 'spec-record.json', 'obligations.json')]
    files['requirement-map.json'] = json_bytes(document)
    files['preparation/judgment-briefs.json'] = json_bytes(briefs)


def prepare(base, selected, extra, out):
    """Materialize a new complete build source. Refuse drift; preserve failures."""
    base, selected, extra, out = map(Path, (base, selected, extra, out))
    if out.exists() or not out.parent.is_dir():
        raise ValueError('output must be new under an existing parent')
    typed_overlay._disjoint('output', out.resolve(), dict(base=base.resolve(), selected=selected.resolve(), extra=extra.resolve()))
    pins = strict_json((DATA / 'build-source-pins.json').read_bytes())
    plan_raw = (base.parent / 'build-plan.json').read_bytes()
    if sha256(plan_raw).hexdigest() != pins['plan_sha256']:
        raise ValueError('base build plan differs from retained acquisition input')
    files = _captured(base, pins['files'])
    context_raw = parts_manifest.read_captured(selected, 'context.json')
    context = strict_json(context_raw)
    parts, provenance = acquisition_power_parts.inputs(base, selected, context_raw)
    for path, raw in parts.items():
        if path in files and files[path] != raw:
            raise ValueError('retained source collision: ' + path)
        files[path] = raw
    extra_pins = strict_json((DATA / 'build-extra-pins.json').read_bytes())
    for path, raw in _captured(extra, extra_pins['files']).items():
        if path in files:
            raise ValueError('extra source collision: ' + path)
        files[path] = raw
    files['power-reference/model-provenance.json'] = json_bytes(extra_pins['model_provenance'])
    _models(files)
    model, basis = acquisition_power_geometry.revise(files['analysis/geometry-model.json'])
    files['analysis/geometry-model.json'], files['power-reference/geometry-basis.json'] = model, basis
    _locality(files)
    _power(files)
    _requirements(files, context)
    plan = strict_json(plan_raw)
    plan['source'] = 'inputs'
    root = out / 'inputs'
    root.mkdir(parents=True)
    for path, raw in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    def write(path, document):
        (root / path).write_bytes(json_bytes(document))
    write('placement-mechanics.json', acquisition_mechanics.declaration(root))
    acquisition_mechanics.load(root / 'placement-mechanics.json')
    write(acquisition_block_source.PATH, acquisition_block_source.derive(root, plan, reference=REFERENCE))
    (out / 'build-plan.json').write_bytes(json_bytes(plan))
    inventory = build_workflow.preflight_build(out / 'build-plan.json', out / 'validation')[5]
    record = spec_record.verify(files['spec-record.json'], files['spec.md'])
    obligation = requirement_map.load_obligations(files['obligations.json'], record, files['spec.md'])
    accounting = requirement_map.account_requirements(strict_json(files['requirement-map.json']),
        record, files['spec.md'], files=inventory, inputs=root, obligations=obligation)
    report = dict(schema='agent-ee.acquisition-power-build-preparation/1', reference=REFERENCE,
        files=inventory, parts_preparation=provenance, accounting=accounting,
        accepted=False, file_release='not_assessed',
        remaining=['Native build and required checks', 'Fresh candidate-bound judgments/full review',
                   'New reusable source-role qualification before adaptation and frozenG4 owner decision'])
    (out / 'preparation.json').write_bytes(json_bytes(report))
    return report

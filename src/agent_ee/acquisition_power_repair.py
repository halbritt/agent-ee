"""Finite offline acquisition R6 exceptions: retained620 and explicit470 margin repair."""
import copy
from decimal import Decimal, localcontext, ROUND_CEILING
from fractions import Fraction
import json
from pathlib import Path

from . import (acquisition_alternative, acquisition_source_supplement, active_spice,
               assembly_readiness, cpl_rotation, easyeda_footprint, jlc_stock_capture,
               part_record, parts_manifest, requirement_map)
from .storage import digest, identity, json_bytes

AT = 'power-repair'
PREFIX = 'preparation/power-repair'
PINS = Path(__file__).resolve().parents[2] / 'fixtures/acquisition-typed/power-repair.json'
PINS470 = PINS.with_name('power-repair-470.json')
# Supporting rail assertion: conditional JEDEC-reference U1 dissipation screen at declared 25C.
# Limit floor((125C recommended TJmax - 25C)/187.1C/W DBV RthetaJA) to 5 decimals, TI SBVS338H 5.3/5.4.
THERMAL_SCREEN = 'u1-dissipation-25c-screen'
THERMAL_EXPRESSION = '(vin-rail_low)*(2*rail/pullup_r+rail/output_bleed_r+adc_load+external_load)+vin*iq_allowance'
THERMAL_LIMIT = '0.53447'
THERMAL_OPERANDS = dict(vin=('4.75 V', '5.25 V'), rail_low=('3.234 V', '3.234 V'), rail=('3.234 V', '3.366 V'),
    pullup_r=('2178 ohm', '2222 ohm'), adc_load=('.0003 A', '.0003 A'), external_load=('.001 A', '.001 A'),
    iq_allowance=('.001 A', '.001 A'))
THERMAL_SOURCES = {'manufacturer-text/tps7a20.txt': '655f6b6f0bcdb005e2316f7d9217eb9defb56ac31e262d7de64645d7efdac523',
    'manufacturer-text/manifest.json': 'ee1c4d695cdd0ba20527a50bd73516dc2e955559a9bd59829edd5d35748fe43f',
    'datasheets-regulator-qualified/manifest.json': '897ae4ce0d6234e6d8e70cc86cd16e861ab7294c5dcf6efebd32bcf8eb20afe1',
    'datasheets-regulator-qualified/24a62c3bba44bad0f419bbf8b75ac28c0402b7c3f91125020e1d3595e602b2a5.pdf':
        'b6c487d79ebed6dd43bf220b07fc5b3ecb9ee55d87e6daa2312acda6546046c0'}
MODEL470 = '02292f70aca8b8a07b2dbbbab42515824b1c2c6d40ba49103a2ef3d7b23b5ffa'
BASIS470 = ('YAGEO RC0603FR-07470RL; RC_L V14 ordering p2 and RC0603 dimensions p4 '
             'maxima1.7/.9/.55mm; power-repair/manufacturer/rc-series.pdf '
             'sha2567b6f0ae72708ca4b38126f84b5407a3e970a94cb60bc1a0afdaeb25a23d839bb; '
             'nominal25C ratings and manufacturer application/process limitations retained')
LCSC = 'C137695'
MPN = 'RC0603FR-07620RL'
MODEL = '43be6345262e5eeb0f54b9214009d13168fa5889333b006315f489dac7c8fa8f'
BASIS = ('YAGEO RC0603FR-07620RL exact620ohm specsheet physical p1 L/W/T maxima1.7/.9/.55mm; '
         'power-repair/request-04.body sha2567773bdb7ba69857695d47efe9c3b454887c85f6498e0c16f772b4bb35223f9b4; '
         'same numerical body dimensions, manufacturer application/process limitations retained')


def pin_path(revision='620'):
    if revision not in ('620', '470'):
        raise ValueError('unsupported finite R6 revision')
    return PINS470 if revision == '470' else PINS


def pins(revision='620'):
    return json.loads(pin_path(revision).read_bytes())


def revision(captures):
    actual = {n: identity_bytes(raw) for n, raw in captures.items()}
    for selected in ('620', '470'):
        if actual == pins(selected)['files']:
            return selected
    raise ValueError('power repair capture drift or unsupported source revision')


def _part(selected):
    return ('C114669', 'RC0603FR-07470RL') if selected == '470' else (LCSC, MPN)


def _bounds(selected):
    return ('465.3', '474.7') if selected == '470' else ('613.8', '626.2')


def read(root):
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('power repair source must not pass through links')
    inventory = requirement_map.input_inventory(root)
    selected = next((v for v in ('620', '470') if inventory == pins(v)['files']), None)
    if selected is None:
        raise ValueError('power repair missing, drifted or mismatched source')
    expected = pins(selected)
    captures = {n: (root / n).read_bytes() for n in expected['files']}
    if captures['activation.txt'].decode() != expected['activation']:
        raise ValueError('explicit sole R6 repair activation required')
    supplier_files(captures)
    return captures


def supplier_files(captures):
    lcsc, mpn = _part(revision(captures))
    raw = captures[f'supplier-capture/supplier/{lcsc}.raweasy.json']
    provenance = captures[f'supplier-capture/supplier/{lcsc}.provenance.json']
    supplier = cpl_rotation.load_suppliers(lambda name: raw if name.endswith('.raweasy.json') else provenance,
                                         [f'cpl/supplier/{lcsc}.provenance.json'])[lcsc]
    parsed = easyeda_footprint.parse_package(supplier['raw'], lcsc, 200)
    receipt = json.loads(captures['supplier-capture/capture-result.json'])
    body = captures[f'supplier-capture/{lcsc}.http-response.json']
    start, end = receipt['result_span_bytes']
    source = json.loads(provenance)
    if (parsed['identity']['manufacturer_part'] != mpn or receipt['outcome'] != 'captured_and_parsed'
            or body[start:end] != raw or identity_bytes(body) != receipt['requests'][0]['sha256']
            or receipt['requests'][0]['url'] != source['source_url']
            or receipt['requests'][0]['ended_at'] != source['retrieved_at']):
        raise ValueError('exact R6 supplier identity/HTTP custody differs')
    return {f'supplier/{lcsc}.raweasy.json': raw, f'supplier/{lcsc}.provenance.json': provenance}


def identity_bytes(raw):
    from hashlib import sha256
    return sha256(raw).hexdigest()


def geometry_sources(selected='620'):
    if selected == '470':
        return {AT + '/' + n: h for n, h in pins(selected)['geometry_sources'].items()}
    return {AT + '/' + n: pins()['files'][n] for n in ('activation.txt', 'request-04.body',
            'request-04.headers', 'request-04.curl.json', 'exact620.txt', 'derivation.json')}


def _write(root, name, raw):
    target = Path(root) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return dict(path=name, sha256=identity_bytes(raw))


def _catalog(root, captures, old_blob):
    old = json.loads(parts_manifest._read_blob(root, old_blob))
    new = json.loads(captures['catalog/catalog.json'])
    if old['source'] != new['source'] or set(old['parts']) & set(new['parts']):
        raise ValueError('R6 catalog parents overlap or source contracts differ')
    for parent, reader in ((old, lambda n: parts_manifest.read_captured(root, str(Path(old_blob['path']).parent / n))),
                           (new, lambda n: captures['catalog/' + n])):
        jlc_stock_capture.validate_capture(parent, reader)
        for request in parent['requests']:
            _write(root, PREFIX + '/catalog/' + request['response']['path'], reader(request['response']['path']))
    union = dict(schema=jlc_stock_capture.SCHEMA, as_of=min(old['as_of'], new['as_of']),
        completed_at=max(old['completed_at'], new['completed_at']), stale_before=None,
        source=old['source'], requests=old['requests'] + new['requests'], parts={**old['parts'], **new['parts']})
    _write(root, PREFIX + '/catalog/parent-adc.json', parts_manifest._read_blob(root, old_blob))
    _write(root, PREFIX + '/catalog/parent-r6.json', captures['catalog/catalog.json'])
    _write(root, PREFIX + '/catalog/derivation.json', json_bytes(dict(
        kind='Conservative offline two-parent union, NOT a fresh capture; original fetch times unchanged',
        parents=[old_blob, dict(path=AT + '/catalog/catalog.json', sha256=identity_bytes(captures['catalog/catalog.json']))])))
    return _write(root, PREFIX + '/catalog/catalog.json', json_bytes(union)), union


def _datasheet(root, captures):
    if revision(captures) == '470':
        original = json.loads(captures['manufacturer/original-manifest.json'])
        source = next(p for p in original['parts'] if p['id'] == 'RC-series')
        raw = captures['manufacturer/rc-series.pdf']
        if identity_bytes(raw) != source['sha256']:
            raise ValueError('RC series original capture binding differs')
        artifact = _write(root, PREFIX + '/datasheets/RC-series.pdf', raw)
        bindings = [dict(id='C114669', datasheet_url=source['url'])]
        row = {**source, 'id':'C114669', 'path':'RC-series.pdf'}
        return _write(root, PREFIX + '/datasheets/manifest.json', json_bytes(dict(
            schema='agent-ee.datasheet-capture/1', bindings_sha256=identity(bindings),
            outcome='captured', parts=[row])))
    request = next(r for r in json.loads(captures['request-ledger.json'])['requests'] if r['number'] == 4)
    url = request['url']
    artifact = _write(root, PREFIX + '/datasheets/C137695.pdf', captures['request-04.body'])
    bindings = [dict(id=LCSC, datasheet_url=url)]
    manifest = dict(schema='agent-ee.datasheet-capture/1', bindings_sha256=identity(bindings),
        outcome='captured', parts=[dict(id=LCSC, url=url, final_url=url, fetched_at=request['finished_at'],
            sha256=artifact['sha256'], path='C137695.pdf', missing=False, reason=None)])
    return _write(root, PREFIX + '/datasheets/manifest.json', json_bytes(manifest))


def _power(root, overlay, captures):
    selected = revision(captures)
    for name, expected in pins(selected)['models'].items():
        raw = captures['models/' + name]
        if identity_bytes(raw) != expected:
            raise ValueError('reviewed diagnostic power model changed: ' + name)
        if '/corners' in name:
            raw = _corner(raw, selected)
        _write(root, name, raw)
    original_removal = Path(root) / 'analysis/power-removal/corners.lib'
    original_removal.write_bytes(_corner(original_removal.read_bytes(), selected))
    checks = json.loads(captures['checks.original.json'])
    if selected == '470':
        checks.append(_minimum_uvlo_check(next(c for c in checks if c['id'] == 'power-removal-stress-lowcin')))
    for check in checks:
        for source in check['params']['sources']:
            if source['id'] == 'supply':
                if source['dc_v'] == 4.752:
                    source['dc_v'] = 4.75
                for key in ('initial_v', 'final_v'):
                    if source.get('step', {}).get(key) == 4.752:
                        source['step'][key] = 4.75
        if check['id'] == 'power-startup-lowcout-esr100m':
            check['params']['analyses'][0]['step_s'] = 1e-6
        active_spice.validate_params(check['params'])
    groups = {'power-startup': [c for c in checks if c['id'].startswith('power-startup')],
              'power-removal': [c for c in checks if c['id'].startswith('power-removal')]}
    path = Path(root) / 'requirement-map.json'
    document = json.loads(path.read_bytes())
    def replace(node):
        if isinstance(node, dict):
            for key, value in list(node.items()):
                if key == 'checks':
                    node[key] = [{**new, 'stage': c['stage']} for c in value
                                 for new in copy.deepcopy(groups.get(c['id'], [c]))]
                else:
                    replace(value)
        elif isinstance(node, list):
            for value in node:
                replace(value)
    replace(document)
    path.write_bytes(json_bytes(document))
    for entry in overlay['mappings'].values():
        for span in entry.get('spans', []):
            if span['mode'] == 'checks':
                span['checks'] = [new['id'] for cid in span['checks'] for new in groups.get(cid, [dict(id=cid)])]
    labels = {c['id']: dict(temperature_c=c['params']['temperature_c'],
        input_loss_a=next(s['current_limit']['sink_a'] for s in c['params']['sources'] if s['id'] == 'iq_allowance'),
        classification=('retained200uA engineering stress, not guaranteed' if c['id'] in ('power-removal','power-removal-stress-lowcin','power-removal-uvlo111-lowcin')
            else 'extrapolated unguaranteed20/25uA sensitivity, including below1.6V' if c['id'].startswith('power-removal')
            else 'startup engineering bracket;1us motivated by oldR6 diagnostic, new620target unverified')) for c in checks}
    if selected == '470':
        labels['power-removal-uvlo111-lowcin'].update(scope='Removal-only1.11V falling/1.135V counterfactual rising; not startup/re-enable', timestep='Root adopts2us; hypothetical4700.5us refinement supports;1us UNKNOWN numerical failure at.01s/node n8; no actual470 qualification', model_sha256='3b7254090cc5e655b75bf1ddb2f99afbac86c44d16d19df864c28754deb12cf4')
    if selected == '470':
        for row in labels.values():
            row['classification'] = row['classification'].replace('new620target', 'new470target')
    _write(root, PREFIX + '/power-cases.json', json_bytes(labels))
    for name in ('analysis/power-startup/README.md', 'analysis/power-removal/README.md'):
        target = Path(root) / name
        target.write_text('Fresh explicitly activated R6=620ohm repair; RBLEED_OUTPUT626.2ohm (+1% adverse).\n'
            'Reviewed source-only removal derivative and zero ADC load replace the optimistic removal gate.\n'
            'Original COUT12.65uF retained (+nativeC3); no C_PAD or solver/model regularization.\n'
            '200uA retained engineering stress;20/25uA are extrapolated sensitivities, not guarantees below1.6V.\n'
            'CIN2u/COUT3u startup cases are finite engineering brackets, not guaranteed joint capacitor bounds.\n'
            'See preparation/power-repair/power-cases.json; all seven require new native results.old10us startup100m convergence failed; oldR6/4.752V 1us converged, fresh620/4.75 target unverified.\n'
            'Measurements/limits unchanged; check supply now matches spec minimum4.75V. No acceptance inherited.\n')
    if selected == '470':
        for name in ('analysis/power-startup/README.md', 'analysis/power-removal/README.md'):
            target = Path(root) / name
            target.write_text(_narrative470(target.read_text()))
    return checks


def _minimum_uvlo_check(control):
    """Keep the typical low-CIN control; stage its removal-only derivative separately."""
    corner = copy.deepcopy(control)
    corner['id'] = 'power-removal-uvlo111-lowcin'
    corner['params']['analyses'][0]['step_s'] = 2e-6
    corner['params']['model_files'] = [n.replace('power-removal-corrected', 'power-removal-uvlo111')
                                       for n in corner['params']['model_files']]
    for part in corner['params']['active_parts'].values():
        part['model_file'] = part['model_file'].replace('power-removal-corrected', 'power-removal-uvlo111')
    corner['params']['active_parts']['U1']['subcircuit'] = 'TPS7A20_ADJ_TRANS_REMOVAL_UVLO111'
    return corner


def _corner(raw, selected='620'):
    old = b'.subckt RBLEED_OUTPUT P N\nR_EFFECTIVE P N 2222\n'
    if raw.count(old) != 1:
        raise ValueError('reviewed R6 corner differs from2222ohm source')
    return raw.replace(old, ('.subckt RBLEED_OUTPUT P N\nR_EFFECTIVE P N ' + _bounds(selected)[1] + '\n').encode())


def _decimal(value):
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_CEILING
        return str(Decimal(value.numerator) / Decimal(value.denominator))


def currents(selected='620'):
    lower, upper = map(Fraction, _bounds(selected))
    rail, low = Fraction('3.366'), Fraction('3.234')
    output = 2 * rail / Fraction('2178') + rail / lower + Fraction('.0003') + Fraction('.001')
    return output, output + Fraction('.001') + Fraction('5.25') / Fraction('21780'), low / upper


def _thermal_sources(root, manufacturer_text=None):
    """Exact captured TI SBVS338H bytes behind the thermal screen; drift refuses."""
    for name, expected in THERMAL_SOURCES.items():
        # Additive text is already admitted by _load_overlay, but not copied yet.
        actual = (manufacturer_text.get(name.removeprefix('manufacturer-text/'))
                  if manufacturer_text is not None and name.startswith('manufacturer-text/')
                  else digest(Path(root) / name))
        if actual != expected:
            raise ValueError('captured TI thermal source differs: ' + name)
    return dict(THERMAL_SOURCES)


def _calculations(root, selected='620'):
    path = Path(root) / 'analysis/electrical-intervals.json'
    doc = json.loads(path.read_bytes())
    doc['parameters']['output_bleed_r'].update(min=_bounds(selected)[0]+' ohm', max=_bounds(selected)[1]+' ohm')
    doc['parameters']['diode_drop'].update(min='1 V', max='1 V')
    budget = next(a for a in doc['assertions'] if a['id'] == 'input-current-budget')
    if budget['lhs'] != '3*rail/pullup_r+adc_load+external_load+iq_allowance+vin/input_bleed_r':
        raise ValueError('authored current accounting differs')
    budget['lhs'] = '2*rail/pullup_r+rail/output_bleed_r+adc_load+external_load+iq_allowance+vin/input_bleed_r'
    operands = {name: (doc['parameters'][name]['min'], doc['parameters'][name]['max']) for name in THERMAL_OPERANDS}
    if (operands != THERMAL_OPERANDS or budget['requirement'] not in ('ACQ-ELEC-2', 'ACQ-ELEC-2-RAIL')
            or any(a['id'] == THERMAL_SCREEN for a in doc['assertions'])):
        raise ValueError('authored thermal screen operands differ')
    # Supporting evidence under the existing rail requirement; the 1mA external term is a calculation
    # reserve (actual J2 load is zero) and iq_allowance is the ASSUMED loaded ground current.
    doc['assertions'].append(dict(id=THERMAL_SCREEN, requirement=budget['requirement'], expression=THERMAL_EXPRESSION,
                                  unit='W', target=dict(min='0 W', max=THERMAL_LIMIT + ' W')))
    path.write_bytes(json_bytes(doc))
    output, total, idle = currents(selected)
    power = (Fraction('5.25') - Fraction('3.234')) * output + Fraction('.001') * Fraction('5.25')
    reference = (Fraction('125') - Fraction('25')) / Fraction('187.1')
    if Fraction(THERMAL_LIMIT) > reference or reference - Fraction(THERMAL_LIMIT) >= Fraction(1, 100000):
        raise ValueError('thermal screen limit is not the rounded-down reference budget')
    rows = dict(maximum_output_load_budget=(output, 'A', 'R3/R4 minimum2178ohm; R6 minimum613.8ohm;300uA ADC allocation+1mA external'),
        input_current_budget=(total, 'A', 'Output load plus ASSUMED1mA loaded ground allowance (no manufacturer maximum) and R5 at5.25V'),
        source_current_margin=(Fraction('.02')-total, 'A', '20mA source contract minus conditional modeled input budget'),
        minimum_output_bleed=(Fraction('3.2505')/Fraction(_bounds(selected)[1]), 'A', 'Existing minimum-load assertion at specified regulator lower bound; R6+1%'),
        minimum_output_bleed_operating_floor=(idle, 'A', 'Operating3.234V floor/R6+1%; nominal25C tolerance only'),
        minimum_protected_input_after_diode=(Fraction('3.75'), 'V', '4.75V minus1V@250mA BAT46W max25C, CONDITIONAL monotonic VF interpretation at<=20mA'),
        minimum_regulator_input_budget=(Fraction('3.74'), 'V', 'Above diode interpretation plus retained10mV wiring/contact allowance'),
        regulator_headroom_above_accuracy_test_floor=(Fraction('.14'), 'V', '3.74V budget minus3.6V DBV accuracy condition;25C diode interpretation conditional'),
        conditional_u1_power=(power, 'W', 'Worst5.25V,3.234V output plus ASSUMED1mA ground allowance, not a guaranteed loadedIGND bound'),
        conditional_u1_temperature_rise=(power*Fraction('187.1'), 'degC', 'TI DBV187.1C/W reference thermal metric, not measured board Rtheta; conditional power'),
        u1_dissipation_screen_reference_budget=(reference, 'W', '(TI SBVS338H 5.3 recommended TJmax125C, not150C absolute/shutdown, '
            'minus declared ambient25C)/5.4 DBV RthetaJA187.1C/W (not YCK201.4); JEDEC reference metric, not board Rtheta'),
        u1_dissipation_screen_limit=(Fraction(THERMAL_LIMIT), 'W', THERMAL_SCREEN + ' target max: reference budget rounded DOWN; '
            'conditional25C screen of conditional_u1_power, not a board dissipation rating or junction-temperature guarantee; '
            'no0..50C, PCB heat-spreading, transient, fault or backpower applicability'),
        u1_power_zero_external_load=(power - (Fraction('5.25') - Fraction('3.234')) * Fraction('.001'), 'W',
            'conditional_u1_power with actual J2 load0; the screen retains the1mA external calculation reserve'),
        output_bleed_dissipation=(Fraction('3.366')**2/Fraction(_bounds(selected)[0]), 'W', 'R6 minimum atnominal25C; exact620sheet0.1W@70C; no board thermal qualification'))
    path = Path(root) / 'engineering-calculations.json';calc=json.loads(path.read_bytes())
    for name, (value, unit, basis) in rows.items():
        if selected == '470':
            basis = basis.replace('613.8', '465.3').replace('exact620sheet', 'RC series standard0603')
        calc['values'][name] = dict(value=_decimal(value), unit=unit, basis=basis,
            exact_fraction=dict(numerator=value.numerator, denominator=value.denominator))
    if selected == '470' and 'regulator_ground_current_allowance' in calc['values']:
        calc['values']['regulator_ground_current_allowance']['basis'] = (
            'ASSUMED1mA loaded ground allowance retained for revised11.625mA output; TI provides no applicable loaded maximum; no guaranteed input budget')
    calc['values']['thermal_sensitivity_rise_all_power_in_u1']['basis'] = (
        'All20mA*5.25V source power at assumed400K/W; DBV reference187.1C/W, not YCK201.4; no measured board thermal guarantee')
    path.write_bytes(json_bytes(calc))
    return rows


def apply(root, overlay, captures, as_of):
    root = Path(root)
    selected = revision(captures)
    lcsc, mpn = _part(selected)
    original = requirement_map.input_inventory(root)
    document = parts_manifest.load_manifest(root, 'parts.json')
    parts_manifest.qualify_manifest(document, root)
    target = next(p for p in document['parts'] if p['id'] == 'R6')
    if (len(document['parts']) != 17 or target['lcsc'] != 'C114662' or target['mpn'] != 'RC0603FR-072K2L'
            or target['instances'] != [dict(reference='R6', sheet='/')]
            or target['footprint'] != 'Resistor_SMD:R_0603_1608Metric'):
        raise ValueError('R6 repair requires exact original17part acquisition allocation')
    mapping = json.loads((root / 'requirement-map.json').read_bytes())
    check = acquisition_alternative._assembly_check(mapping)
    old_catalog = dict(path=check['params']['catalog'], sha256=check['params']['catalog_sha256'])
    catalog, union = _catalog(root, captures, old_catalog)
    policy = assembly_readiness.validate_params({**check['params'], 'as_of': as_of})
    info = assembly_readiness._verify_jlc_capture(root, catalog['path'], union)
    assembly_readiness._capture_gate(info, policy, set())
    row = union['parts'].get(lcsc)
    if (row is None or row['mpn'] != mpn or row['mfr'] != 'YAGEO' or row['package'] != '0603'
            or row['stock'] < max(policy['boards'], policy['stock_floor'])):
        raise ValueError('exact R6 identity/package/stock not admitted')
    datasheets = _datasheet(root, captures)
    decision = _write(root, PREFIX + '/decision.json', json_bytes(dict(
        activation=AT + '/activation.txt', activation_sha256=identity_bytes(captures['activation.txt']),
        catalog=catalog, original_inventory=original, selected=lcsc, part='R6',
        qualification='Explicit soleR6 exception; source-only unverified repair, not supplier/native acceptance',
        parts=[dict(id=p['id'], lcsc=lcsc if p['id']=='R6' else p['lcsc'], instances=p['instances']) for p in document['parts']])))
    for part in document['parts']:
        old = json.loads(parts_manifest.read_captured(root, part['selection']['part_record']))
        record = part_record.prepare_record(root, lcsc if part['id'] == 'R6' else part['lcsc'], catalog,
            datasheets if part['id'] == 'R6' else old['datasheets'],
            lcsc if part['id'] == 'R6' else old['datasheet_id'],
            source_selection=None if part['id'] == 'R6' else old.get('source_selection'))
        named = _write(root, PREFIX + '/named/' + record['lcsc'] + '.json', json_bytes(record))
        for key in ('lcsc','mpn','manufacturer','package','stock'):
            part[key] = record[key]
        part['selection'] = dict(kind='named', lcsc=part['lcsc'], part_record=named['path'],
            part_record_sha256=named['sha256'], decision_ref={**decision, 'locator':'parts[id=' + part['id'] + ']'})
        part['datasheet'].update(artifact=record['artifact'], provenance={**record['datasheets'],
            'locator':'parts[id=' + record['datasheet_id'] + ']'}, status='captured')
    parts_manifest.prepare_manifest(document, root)
    (root / 'parts.json').write_bytes(json_bytes(document))
    circuit = json.loads((root / 'circuit.json').read_bytes())
    r6 = next(c for c in circuit['components'] if c['reference'] == 'R6')
    if r6['lib_id'] != 'Device:R' or r6['value'] != '2.2k':
        raise ValueError('R6 circuit source differs')
    r6['value'] = selected
    (root / 'circuit.json').write_bytes(json_bytes(circuit))
    selections = json.loads((root / 'component-selections.json').read_bytes())
    next(p for p in selections if p['reference'] == 'R6').update(lcsc=lcsc, value=selected)
    (root / 'component-selections.json').write_bytes(json_bytes(selections))
    check['params'].update(catalog=catalog['path'], catalog_sha256=catalog['sha256'])
    (root / 'requirement-map.json').write_bytes(json_bytes(mapping))
    cases = _power(root, overlay, captures)
    _calculations(root, selected)
    calculations_path = root / 'engineering-calculations.json'
    calculations = json.loads(calculations_path.read_bytes())
    for name in (catalog['path'], datasheets['path'], (PREFIX+'/datasheets/RC-series.pdf' if selected == '470' else PREFIX+'/datasheets/C137695.pdf'),
                 PREFIX+'/power-cases.json', *pins(selected)['models']):
        calculations['sources'][name] = digest(root / name)
    for name in ('authority/bleed-repair-proposal-addendum-1.md',
                 'authority/independent-power-source-review.txt', 'diagnostic-result.json',
                 'startup-resolution-result.json', 'authority/startup-step-adoption.txt'):
        calculations['sources'][AT+'/'+name] = identity_bytes(captures[name])
    if selected == '470':
        for name in ('manufacturer/rc-series.pdf', 'manufacturer/rc-series.txt', 'manufacturer/original-manifest.json', 'derivation470.json', 'uvlo/REPORT.md', 'uvlo/independent-source-review.txt', 'uvlo/independent-native-review.txt', 'uvlo/620-failure-result.json', 'uvlo/470-proposal-result.json', 'uvlo/470-steps-result.json', 'uvlo/470-proposal-native-result.json', 'uvlo/470-steps-native-result.json', 'authority/470-timestep-update.txt'):
            calculations['sources'][AT+'/'+name] = identity_bytes(captures[name])
    calculations['sources'].update(_thermal_sources(root, overlay['added_inputs']['manufacturer-text']['files']))
    calculations_path.write_bytes(json_bytes(calculations))
    note = ('\n\nExplicit reference exception: R6 alone is now620ohm1% YAGEO RC0603FR-07620RL/C137695. '
        'R3/R4 remain2.2k. Original-reference brief bytes are preserved; power-repair/activation.txt '
        'separately activates this exception and corrected seven-case power analysis. '
        'Nominal25C input budget is conditional on an assumed1mA loaded ground-current allowance, not a manufacturer maximum. '
        'BAT46W .45V@10mA no longer covers the increased~11.1mA budget. Use1V@250mA with explicitly conditional '
        'monotonic VF interpretation and20mA external limit:3.75V after diode at4.75V supply,3.74V after retained10mV wire allowance. '
        'DBV accuracy inputfloor3.6V; DBV thermal187.1C/W, not YCK201.4. Conditional U1 power~25.16mW/temperature rise~4.71C '
        'is not a guaranteed board thermal result. Exact fraction calculations are in engineering-calculations.json. '
        'Supplier preview, physical/temperature qualification, new native results and independent judgments remain pending. '
        '200uA removal stress retained;20/25uA are extrapolated sensitivities, not manufacturer bounds below1.6V. '
        'Original COUT12.65uF is retained. Historical startup100m10us is UNKNOWN; adopt root-authorized1us only, motivated by oldR6/4.752V convergence. Fresh620/4.75 result pending; no C_PAD/model correction or inherited pass.\n')
    for name in ('OPERATING.md','ENGINEERING-REVIEW.md'):
        p = root / name
        text = p.read_text()
        if name == 'ENGINEERING-REVIEW.md':
            start = text.index('**ACQ-ELEC-1 — input conditions.**')
            end = text.index('**ACQ-ELEC-4 — analog channels.**')
            text = text[:start] + (
                '**ACQ-ELEC-1 — revised input evaluation.** The increased R6 load invalidates the old '
                '0.45V@10mA transfer. At the20mA external limit, a CONDITIONAL monotonic interpretation '
                'uses BAT46W1.00V maximum@250mA/25C:4.75-1=3.75V after diode,3.74V after retained10mV wire allowance. '
                'DBV accuracy inputfloor is3.6V, with0.14V conditional margin. No loadedIGND maximum is supplied; '
                'the1mA allowance remains an assumption. No0..50C board qualification follows.\n\n'
                '**ACQ-ELEC-2 — revised regulated-rail evaluation.** R3/R4 stay2178..2222ohm; R6 is613.8..626.2ohm at25C. '
                'Current accounting is2*rail/pullup_r+rail/output_bleed_r plus ADC/external/ground/R5 terms. '
                'Exact conditional fractions and rounded values are in engineering-calculations.json; output about9.875mA, '
                'input about11.116mA, below20mA under the assumed ground allowance. Minimum R6 load remains above1mA. '
                'Geometry uses this revised load for its existing16.5mV rail-drop check; no material, clearance or copper limit changes. '
                'Worst5.25V U1 power is about25.16mW INCLUDING ASSUMED1mA ground allowance; DBV187.1C/W reference gives about4.71C rise, '
                'not a measured board thermal bound. Former201.4C/W DBV attribution and old load results are superseded. '
                'Supporting interval u1-dissipation-25c-screen bounds that same enclosure (1mA external calculation reserve kept while '
                'actual J2 load is0; ASSUMED1mA loadedIGND) by0.53447W, the rounded-down (125C recommended TJmax-25C)/187.1C/W '
                'DBV JEDEC reference budget. It is a conditional25C screen, not a board thermal or junction-temperature guarantee; '
                'the dissipation source evaluation remains a judgment. '
                'Original startup COUT12.65uF remains, and finite low-COUT3u/ESR100m and5m brackets require fresh evaluation. '
                'The exact Samsung curves are typical; combined bias/AC/temp/aging bounds are not guaranteed. '
                'All three startup cases use4.75V. The100m case alone adopts root-authorized1us timestep; oldR6/4.752V convergence is motivation only. '
                'No new hardware stability, overshoot or native pass is claimed.\n\n'
                '**ACQ-ELEC-3 — revised removal evaluation.** The source-only reviewed removal derivative '
                'f74fbe9f and zero ADC load replace unsupported output sinking/active-discharge reliance. '
                'Four removal cases retain limits including OUT-IN<=0.3V: originalCIN3u and lowCIN2u at200uA engineering stress, '
                'plus20/25uA extrapolated sensitivities. These smaller currents are NOT manufacturer guarantees below1.6V. '
                'R6 adverse value626.2ohm applies throughout; no C_PAD, capacitance/ESR change or altered limit. '
                'Historical2.2k corrected-model failures0.87959/1.36248V are retained, and new620ohm results are pending. '
                'Reverse input/source sequencing and physical/temperature obligations remain.\n\n') + text[end:]
            text = text.replace('The maximum resistor load is about 5.21 mW, less than 6% of 0.1 W.',
                'The revised R6 load is about18.46mW atnominal25C, below the exact0.1W@70C rating; board thermal confirmation remains.')
            text = text.replace('Values, 1% tolerance,', 'The explicitly activated R6 exception is620ohm; other values and1% tolerance,')
        if selected == '470':
            text, current_note = _narrative470(text), _narrative470(note)
        else:
            current_note = note
        p.write_text(text + current_note)
    _write(root, PREFIX + '/activation.txt', captures['activation.txt'])
    screen = overlay['judgments']['judgments/acq-elec-2-source-evaluation.json']
    screen['sources'] += sorted(THERMAL_SOURCES)
    screen['questions'].append('Assess executed interval ' + THERMAL_SCREEN + ' (<=' + THERMAL_LIMIT + 'W) with its '
        'engineering-calculations derivation and captured TI SBVS338H 5.3/5.4/7.1.5 text; it is a conditional JEDEC-reference '
        'screen under assumed1mA loadedIGND at25C, not proof of board thermal behavior or of the whole dissipation obligation.')
    for brief in overlay['judgments'].values():
        brief['sources'] += [AT + '/activation.txt', PREFIX + '/decision.json', PREFIX + '/power-cases.json']
        brief['questions'].append('Assess the explicitly activated R6 exception and fresh '+('eight' if selected == '470' else 'seven')+'-case results; no historical judgment or100m convergence credit.')
        if selected == '470':
            brief['sources'] += [AT+'/uvlo/REPORT.md', AT+'/uvlo/independent-source-review.txt', AT+'/uvlo/independent-native-review.txt', AT+'/uvlo/620-failure-result.json', AT+'/uvlo/470-proposal-result.json', AT+'/uvlo/470-steps-result.json', AT+'/uvlo/470-proposal-native-result.json', AT+'/uvlo/470-steps-native-result.json', AT+'/authority/470-timestep-update.txt']
            brief['questions'].append('Assess required removal-only minimum fallingUVLO1.11V case at200uA/CIN2u/2us; rising1.135V is counterfactual and cannot qualify startup/re-enable. Retain620 actual failures and hypothetical4701us numerical UNKNOWN; fresh judgments must bind production compiledscope and candidate, never injected diagnostic scope or R4 drafts.')
    overlay['base']['requirement_map_sha256'] = digest(root / 'requirement-map.json')
    return dict(activation_sha256=identity_bytes(captures['activation.txt']), selected=lcsc, decision=decision,
        catalog=catalog, cases=[c['id'] for c in cases], original_inventory_sha256=identity(original),
        diagnostic_motivation=dict(before=dict(path=AT+'/diagnostic-result.json', sha256=identity_bytes(captures['diagnostic-result.json'])),
            timestep_resolution=dict(path=AT+'/startup-resolution-result.json', sha256=identity_bytes(captures['startup-resolution-result.json'])),
            scope='Historical diagnostics are motivation only; fresh'+selected+'/4.75 target has no inherited acceptance'))


def finalize_geometry(root, selected='620'):
    root = Path(root)
    path = root / 'analysis/geometry-model.json'
    if digest(path) != acquisition_source_supplement.CORRECTED_MODEL:
        raise ValueError('R6 geometry requires admitted exact Murata correction first')
    model = json.loads(path.read_bytes())
    model['packages']['R6']['basis'] = BASIS470 if selected == '470' else BASIS
    model['dc_estimate']['input_current_a'] = float(_decimal(currents(selected)[1]))
    path.write_bytes(json_bytes(model))
    if digest(path) != (MODEL470 if selected == '470' else MODEL):
        raise ValueError('unsupported finite R6 geometry model')
    calc_path = root / 'engineering-calculations.json';calc=json.loads(calc_path.read_bytes())
    calc['sources']['analysis/geometry-model.json'] = digest(path)
    calc_path.write_bytes(json_bytes(calc))


def _narrative470(text):
    for old, new in (('RC0603FR-07620RL','RC0603FR-07470RL'), ('C137695','C114669'),
                     ('620ohm','470ohm'), ('fresh620','fresh470'), ('Fresh620','Fresh470'),
                     ('626.2','474.7'), ('613.8','465.3'), ('Four removal cases','Five removal cases'),
                     ('plus20/25uA extrapolated sensitivities.', 'plus20/25uA extrapolated sensitivities and required200uA/CIN2u minimum1.11V fallingUVLO control.'),
                     ('seven-case power analysis','eight-case power analysis'),
                     ('all seven require new native results','all eight require new native results'),
                     ('~11.1mA','~12.866mA'), ('9.875mA','11.625mA'), ('11.116mA','12.866mA'),
                     ('25.16mW','28.686mW'), ('4.71C','5.367C'), ('18.46mW','24.350mW'),
                     ('exact sheet','RC series family/coding evidence'), ('exact0.1W','RC series0.1W')):
        text = text.replace(old, new)
    return text + ('\n470 margin repair retains typical checks and requires an additional200uA/CIN2u removal-only1.11V fallingUVLO check at2us. '
                   'Derivative3b725409 preserves all equations; rising1.135V is counterfactual, not startup/re-enable qualification. '
                   'Actual620 minimumUVLO defects at2us/1us .3008950026/.3008992110V are preserved as motivation only. '
                   'RC-series captured ordering/0603 tables support470 explicitly; exact620 PDF remains historical, not470 evidence. '
                   'Hypothetical4705us/2us/.5us pass;1us UNKNOWN numerical failure at.01s/node n8; no regularization or all-timestep claim. '
                   'Fresh470 native/supplier/physical evidence and judgments remain required.\n')

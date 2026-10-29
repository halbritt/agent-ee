"""One explicit, offline acquisition exception: exact R6=620 ohm and reviewed power cases."""
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
LCSC = 'C137695'
MPN = 'RC0603FR-07620RL'
MODEL = '43be6345262e5eeb0f54b9214009d13168fa5889333b006315f489dac7c8fa8f'
BASIS = ('YAGEO RC0603FR-07620RL exact620ohm specsheet physical p1 L/W/T maxima1.7/.9/.55mm; '
         'power-repair/request-04.body sha2567773bdb7ba69857695d47efe9c3b454887c85f6498e0c16f772b4bb35223f9b4; '
         'same numerical body dimensions, manufacturer application/process limitations retained')


def pins():
    return json.loads(PINS.read_bytes())


def read(root):
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('power repair source must not pass through links')
    expected = pins()
    if requirement_map.input_inventory(root) != expected['files']:
        raise ValueError('power repair missing, drifted or mismatched source')
    captures = {n: (root / n).read_bytes() for n in expected['files']}
    if captures['activation.txt'].decode() != expected['activation']:
        raise ValueError('explicit sole R6 repair activation required')
    supplier_files(captures)
    return captures


def supplier_files(captures):
    raw = captures['supplier-capture/supplier/C137695.raweasy.json']
    provenance = captures['supplier-capture/supplier/C137695.provenance.json']
    supplier = cpl_rotation.load_suppliers(lambda name: raw if name.endswith('.raweasy.json') else provenance,
                                         ['cpl/supplier/C137695.provenance.json'])[LCSC]
    parsed = easyeda_footprint.parse_package(supplier['raw'], LCSC, 200)
    receipt = json.loads(captures['supplier-capture/capture-result.json'])
    body = captures['supplier-capture/C137695.http-response.json']
    start, end = receipt['result_span_bytes']
    source = json.loads(provenance)
    if (parsed['identity']['manufacturer_part'] != MPN or receipt['outcome'] != 'captured_and_parsed'
            or body[start:end] != raw or identity_bytes(body) != receipt['requests'][0]['sha256']
            or receipt['requests'][0]['url'] != source['source_url']
            or receipt['requests'][0]['ended_at'] != source['retrieved_at']):
        raise ValueError('exact R6 supplier identity/HTTP custody differs')
    return {'supplier/C137695.raweasy.json': raw, 'supplier/C137695.provenance.json': provenance}


def identity_bytes(raw):
    from hashlib import sha256
    return sha256(raw).hexdigest()


def geometry_sources():
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
    request = next(r for r in json.loads(captures['request-ledger.json'])['requests'] if r['number'] == 4)
    url = request['url']
    artifact = _write(root, PREFIX + '/datasheets/C137695.pdf', captures['request-04.body'])
    bindings = [dict(id=LCSC, datasheet_url=url)]
    manifest = dict(schema='agent-ee.datasheet-capture/1', bindings_sha256=identity(bindings),
        outcome='captured', parts=[dict(id=LCSC, url=url, final_url=url, fetched_at=request['finished_at'],
            sha256=artifact['sha256'], path='C137695.pdf', missing=False, reason=None)])
    return _write(root, PREFIX + '/datasheets/manifest.json', json_bytes(manifest))


def _power(root, overlay, captures):
    for name, expected in pins()['models'].items():
        raw = captures['models/' + name]
        if identity_bytes(raw) != expected:
            raise ValueError('reviewed diagnostic power model changed: ' + name)
        if '/corners' in name:
            raw = _corner(raw)
        _write(root, name, raw)
    original_removal = Path(root) / 'analysis/power-removal/corners.lib'
    original_removal.write_bytes(_corner(original_removal.read_bytes()))
    checks = json.loads(captures['checks.original.json'])
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
        classification=('retained200uA engineering stress, not guaranteed' if c['id'] in ('power-removal','power-removal-stress-lowcin')
            else 'extrapolated unguaranteed20/25uA sensitivity, including below1.6V' if c['id'].startswith('power-removal')
            else 'startup engineering bracket;1us motivated by oldR6 diagnostic, new620target unverified')) for c in checks}
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
    return checks


def _corner(raw):
    old = b'.subckt RBLEED_OUTPUT P N\nR_EFFECTIVE P N 2222\n'
    if raw.count(old) != 1:
        raise ValueError('reviewed R6 corner differs from2222ohm source')
    return raw.replace(old, b'.subckt RBLEED_OUTPUT P N\nR_EFFECTIVE P N 626.2\n')


def _decimal(value):
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_CEILING
        return str(Decimal(value.numerator) / Decimal(value.denominator))


def currents():
    rail, low = Fraction('3.366'), Fraction('3.234')
    output = 2 * rail / Fraction('2178') + rail / Fraction('613.8') + Fraction('.0003') + Fraction('.001')
    return output, output + Fraction('.001') + Fraction('5.25') / Fraction('21780'), low / Fraction('626.2')


def _calculations(root):
    path = Path(root) / 'analysis/electrical-intervals.json'
    doc = json.loads(path.read_bytes())
    doc['parameters']['output_bleed_r'].update(min='613.8 ohm', max='626.2 ohm')
    doc['parameters']['diode_drop'].update(min='1 V', max='1 V')
    budget = next(a for a in doc['assertions'] if a['id'] == 'input-current-budget')
    if budget['lhs'] != '3*rail/pullup_r+adc_load+external_load+iq_allowance+vin/input_bleed_r':
        raise ValueError('authored current accounting differs')
    budget['lhs'] = '2*rail/pullup_r+rail/output_bleed_r+adc_load+external_load+iq_allowance+vin/input_bleed_r'
    path.write_bytes(json_bytes(doc))
    output, total, idle = currents()
    power = (Fraction('5.25') - Fraction('3.234')) * output + Fraction('.001') * Fraction('5.25')
    rows = dict(maximum_output_load_budget=(output, 'A', 'R3/R4 minimum2178ohm; R6 minimum613.8ohm;300uA ADC allocation+1mA external'),
        input_current_budget=(total, 'A', 'Output load plus ASSUMED1mA loaded ground allowance (no manufacturer maximum) and R5 at5.25V'),
        source_current_margin=(Fraction('.02')-total, 'A', '20mA source contract minus conditional modeled input budget'),
        minimum_output_bleed=(Fraction('3.2505')/Fraction('626.2'), 'A', 'Existing minimum-load assertion at specified regulator lower bound; R6+1%'),
        minimum_output_bleed_operating_floor=(idle, 'A', 'Operating3.234V floor/R6+1%; nominal25C tolerance only'),
        minimum_protected_input_after_diode=(Fraction('3.75'), 'V', '4.75V minus1V@250mA BAT46W max25C, CONDITIONAL monotonic VF interpretation at<=20mA'),
        minimum_regulator_input_budget=(Fraction('3.74'), 'V', 'Above diode interpretation plus retained10mV wiring/contact allowance'),
        regulator_headroom_above_accuracy_test_floor=(Fraction('.14'), 'V', '3.74V budget minus3.6V DBV accuracy condition;25C diode interpretation conditional'),
        conditional_u1_power=(power, 'W', 'Worst5.25V,3.234V output plus ASSUMED1mA ground allowance, not a guaranteed loadedIGND bound'),
        conditional_u1_temperature_rise=(power*Fraction('187.1'), 'degC', 'TI DBV187.1C/W reference thermal metric, not measured board Rtheta; conditional power'),
        output_bleed_dissipation=(Fraction('3.366')**2/Fraction('613.8'), 'W', 'R6 minimum atnominal25C; exact620sheet0.1W@70C; no board thermal qualification'))
    path = Path(root) / 'engineering-calculations.json';calc=json.loads(path.read_bytes())
    for name, (value, unit, basis) in rows.items():
        calc['values'][name] = dict(value=_decimal(value), unit=unit, basis=basis,
            exact_fraction=dict(numerator=value.numerator, denominator=value.denominator))
    calc['values']['thermal_sensitivity_rise_all_power_in_u1']['basis'] = (
        'All20mA*5.25V source power at assumed400K/W; DBV reference187.1C/W, not YCK201.4; no measured board thermal guarantee')
    path.write_bytes(json_bytes(calc))
    return rows


def apply(root, overlay, captures, as_of):
    root = Path(root)
    if {n: identity_bytes(raw) for n, raw in captures.items()} != pins()['files']:
        raise ValueError('power repair capture drift before application')
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
    row = union['parts'].get(LCSC)
    if (row is None or row['mpn'] != MPN or row['mfr'] != 'YAGEO' or row['package'] != '0603'
            or row['stock'] < max(policy['boards'], policy['stock_floor'])):
        raise ValueError('exact R6 identity/package/stock not admitted')
    datasheets = _datasheet(root, captures)
    decision = _write(root, PREFIX + '/decision.json', json_bytes(dict(
        activation=AT + '/activation.txt', activation_sha256=identity_bytes(captures['activation.txt']),
        catalog=catalog, original_inventory=original, selected=LCSC, part='R6',
        qualification='Explicit soleR6 exception; source-only unverified repair, not supplier/native acceptance',
        parts=[dict(id=p['id'], lcsc=LCSC if p['id']=='R6' else p['lcsc'], instances=p['instances']) for p in document['parts']])))
    for part in document['parts']:
        old = json.loads(parts_manifest.read_captured(root, part['selection']['part_record']))
        record = part_record.prepare_record(root, LCSC if part['id'] == 'R6' else part['lcsc'], catalog,
            datasheets if part['id'] == 'R6' else old['datasheets'],
            LCSC if part['id'] == 'R6' else old['datasheet_id'],
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
    r6['value'] = '620'
    (root / 'circuit.json').write_bytes(json_bytes(circuit))
    selections = json.loads((root / 'component-selections.json').read_bytes())
    next(p for p in selections if p['reference'] == 'R6').update(lcsc=LCSC, value='620')
    (root / 'component-selections.json').write_bytes(json_bytes(selections))
    check['params'].update(catalog=catalog['path'], catalog_sha256=catalog['sha256'])
    (root / 'requirement-map.json').write_bytes(json_bytes(mapping))
    cases = _power(root, overlay, captures)
    _calculations(root)
    calculations_path = root / 'engineering-calculations.json'
    calculations = json.loads(calculations_path.read_bytes())
    for name in (catalog['path'], datasheets['path'], PREFIX+'/datasheets/C137695.pdf',
                 PREFIX+'/power-cases.json', *pins()['models']):
        calculations['sources'][name] = digest(root / name)
    for name in ('authority/bleed-repair-proposal-addendum-1.md',
                 'authority/independent-power-source-review.txt', 'diagnostic-result.json',
                 'startup-resolution-result.json', 'authority/startup-step-adoption.txt'):
        calculations['sources'][AT+'/'+name] = identity_bytes(captures[name])
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
        p.write_text(text + note)
    _write(root, PREFIX + '/activation.txt', captures['activation.txt'])
    for brief in overlay['judgments'].values():
        brief['sources'] += [AT + '/activation.txt', PREFIX + '/decision.json', PREFIX + '/power-cases.json']
        brief['questions'].append('Assess the explicitly activated R6 exception and fresh seven-case results; no historical judgment or100m convergence credit.')
    overlay['base']['requirement_map_sha256'] = digest(root / 'requirement-map.json')
    return dict(activation_sha256=identity_bytes(captures['activation.txt']), selected=LCSC, decision=decision,
        catalog=catalog, cases=[c['id'] for c in cases], original_inventory_sha256=identity(original),
        diagnostic_motivation=dict(before=dict(path=AT+'/diagnostic-result.json', sha256=identity_bytes(captures['diagnostic-result.json'])),
            timestep_resolution=dict(path=AT+'/startup-resolution-result.json', sha256=identity_bytes(captures['startup-resolution-result.json'])),
            scope='OldR6/4.752V diagnostic motivation only; fresh620/4.75 target has no inherited acceptance'))


def finalize_geometry(root):
    root = Path(root)
    path = root / 'analysis/geometry-model.json'
    if digest(path) != acquisition_source_supplement.CORRECTED_MODEL:
        raise ValueError('R6 geometry requires admitted exact Murata correction first')
    model = json.loads(path.read_bytes())
    model['packages']['R6']['basis'] = BASIS
    model['dc_estimate']['input_current_a'] = float(_decimal(currents()[1]))
    path.write_bytes(json_bytes(model))
    if digest(path) != MODEL:
        raise ValueError('unsupported finite R6 geometry model')
    calc_path = root / 'engineering-calculations.json';calc=json.loads(calc_path.read_bytes())
    calc['sources']['analysis/geometry-model.json'] = digest(path)
    calc_path.write_bytes(json_bytes(calc))

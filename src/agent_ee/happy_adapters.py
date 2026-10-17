"""Closed target-level adapters for installed kicad-happy 2.3.1/schema 1.4.0.

Native findings remain informational. These coordinator calculations establish
only the named bounded proxy, with captured assumptions, never certification.
"""
from decimal import Decimal
import math
import re

from .storage import identity

# Native entrypoint and source branches are documented in docs/kicad-happy.md.
CONTRACTS = {
    'esd-distance/1': ('emc', 'ES-001', {'protection', 'connector', 'protected_net'}),
    'thermal-ldo-estimate/1': ('thermal', 'TH-DET',
        {'ambient_c', 'input_v', 'output_v', 'load_a', 'load_source', 'rtheta_source', 'tj_max_source'}),
    'capacitor-voltage/1': ('schematic', 'capacitor-voltage',
        {'operating_v', 'rail_net', 'ground_net', 'dielectric', 'regulator'}),
    'power-pin-inventory/1': ('schematic', 'power-pin-inventory', {'ground_net', 'pins'}),
    'usb-vbus-presence/1': ('schematic', 'UC-002', {'connector', 'vbus_net', 'subcheck'}),
    'regulator-feedback/1': ('schematic', 'PR-DET', {
        'vref_v', 'input_v', 'load_a', 'input_net', 'output_net', 'feedback_net',
        'ground_net', 'regulator', 'top', 'bottom'}),
}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate(rule):
    name = rule.get('adapter')
    if name not in CONTRACTS:
        raise ValueError('unknown qualified adapter')
    analyzer, native_rule, fields = CONTRACTS[name]
    if (rule.get('analyzer'), rule.get('rule_id')) != (analyzer, native_rule):
        raise ValueError('adapter/native rule mismatch')
    if 'field' in rule or not isinstance(rule.get('assumptions'), dict) or set(rule['assumptions']) != fields:
        raise ValueError('adapter requires exact immutable assumptions')
    source = rule.get('assumptions_source')
    if (not isinstance(source, dict) or set(source) != {'path', 'locator'}
            or any(not isinstance(v, str) or not v.strip() for v in source.values())):
        raise ValueError('adapter requires captured assumptions source and locator')
    from .candidate import safe_relative
    safe_relative(source['path'])
    for key, value in rule['assumptions'].items():
        if key == 'pins':
            if not isinstance(value, list) or not value:
                raise ValueError('power inventory requires explicit pins')
            ids = []
            for pin in value:
                if (not isinstance(pin, dict) or set(pin) != {'component','pin','net','role','current_a'}
                        or any(not isinstance(pin[k], str) or not pin[k] for k in ('component','pin','net'))
                        or pin['role'] not in ('power_in','power_out')
                        or not finite(pin['current_a']) or pin['current_a'] <= 0):
                    raise ValueError('invalid power pin assumption')
                ids.append((pin['component'], pin['pin']))
            if len(set(ids)) != len(ids):
                raise ValueError('duplicate power pin')
        elif key.endswith(('_v', '_a', '_c', '_fraction')):
            if not finite(value) or (not key.endswith('_c') and value <= 0):
                raise ValueError('invalid finite operating assumption: ' + key)
        elif not isinstance(value, str) or not value:
            raise ValueError('invalid adapter assumption: ' + key)
    validate_envelope(rule)
    if not {'min', 'max'} & rule.keys():
        raise ValueError('adapter requires bounds')


def validate_envelope(rule):
    a, name = rule['assumptions'], rule['adapter']
    valid = True
    if name == 'regulator-feedback/1':
        valid = (a['vref_v'] == 1.21 and set(rule['components']) == {a['regulator'],a['top'],a['bottom']}
                 and len({a[k] for k in ('regulator','top','bottom')}) == 3
                 and len({a[k] for k in ('input_net','output_net','feedback_net','ground_net')}) == 4)
    elif name == 'usb-vbus-presence/1':
        valid = (rule['components'] == [a['connector']] and a['subcheck'] == 'vbus_esd_protection'
                 and rule.get('min') == 1 and rule.get('max') == 1)
    elif name == 'power-pin-inventory/1':
        valid = set(rule['components']) == {p['component'] for p in a['pins']} and rule.get('min') == 1 and rule.get('max') == 1
        sources = [p['net'] for p in a['pins'] if p['role'] == 'power_out']
        valid = valid and len(sources) == len(set(sources)) and {p['net'] for p in a['pins']} == set(sources)
    elif name == 'capacitor-voltage/1':
        valid = len(rule['components']) == 1 and a['rail_net'] != a['ground_net'] and a['dielectric'] in ('X7R','X5R','C0G','NP0')
    elif name == 'thermal-ldo-estimate/1':
        valid = (len(rule['components']) == 1 and a['input_v'] > a['output_v']
                 and a['load_source'] == 'output_cap_proxy' and a['rtheta_source'] == 'package_table'
                 and a['tj_max_source'] == 'default_125' and -55 <= a['ambient_c'] <= 150)
    elif name == 'esd-distance/1':
        valid = (set(rule['components']) == {a['protection'],a['connector']} and a['protection'] != a['connector']
                 and rule.get('max') == 15 and 'min' not in rule)
    if not valid:
        raise ValueError('declaration outside qualified adapter envelope: ' + name)


class NotEvaluated(Exception):
    """An expected unsupported/missing/ambiguous native branch."""


def require(condition, reason):
    if not condition:
        raise NotEvaluated(reason)


def unique(items, predicate, reason):
    require(isinstance(items, list) and all(isinstance(v, dict) for v in items), reason)
    matches = [(i, v) for i, v in enumerate(items) if predicate(v)]
    require(len(matches) == 1, reason)
    return matches[0]


class Native:
    def __init__(self, reports):
        self.reports = reports
        self.locators = []

    def item(self, analyzer, collection, predicate):
        require(analyzer in self.reports, 'missing analyzer ' + analyzer)
        i, item = unique(self.reports[analyzer].get(collection), predicate,
                         'missing or ambiguous native ' + collection)
        require(not item.get('suppressed'), 'suppressed native target')
        self.locators.append({'raw_output_path': analyzer + '.json',
                              'raw_locator': f'/{collection}/{i}'})
        return item

    def component(self, ref, kind=None):
        c = self.item('schematic', 'components', lambda c: c.get('reference') == ref)
        require(not c.get('dnp') and c.get('on_board') is True, 'unpopulated or unsupported target')
        require(kind is None or c.get('type') == kind, 'wrong native component class')
        require(all(isinstance(c.get(k), str) for k in ('reference','value','lib_id')),
                'malformed native component identity')
        return c

    def nets(self, ref, kind=None):
        c = self.component(ref, kind)
        nets = c.get('pin_nets')
        require(isinstance(nets, dict) and bool(nets)
                and all(isinstance(k, str) and isinstance(v, str) and v for k, v in nets.items()),
                'missing native pin connectivity')
        # Cross-check the two exported views; disagreement cannot establish topology.
        inventory = self.reports['schematic'].get('nets')
        require(isinstance(inventory, dict), 'missing net inventory')
        for pin, net in nets.items():
            data = inventory.get(net)
            require(isinstance(data, dict), 'missing native net')
            pins = data.get('pins')
            unique(pins, lambda p: p.get('component') == ref and p.get('pin_number') == pin,
                   'inconsistent native pin inventory')
        return nets

    def finding(self, ref, rule):
        return self.item('schematic', 'findings',
                         lambda f: f.get('ref') == ref and f.get('rule_id') == rule)


def feedback(native, rule):
    a = rule['assumptions']
    require(set(rule['components']) == {a['regulator'], a['top'], a['bottom']}, 'target identity mismatch')
    reg = native.finding(a['regulator'], 'PR-DET')
    require(reg.get('topology') == 'LDO' and not reg.get('inverting'), 'only noninverting adjustable LDO supported')
    require(all(reg.get(k) == a[v] for k, v in (
        ('input_rail', 'input_net'), ('output_rail', 'output_net'), ('fb_net', 'feedback_net'))),
        'regulator rail/feedback topology absent or changed')
    require(len({a['input_net'], a['output_net'], a['feedback_net'], a['ground_net']}) == 4,
            'ambiguous feedback topology')
    component = native.component(a['regulator'], 'ic')
    require(component['value'] == 'TPS7A4501' and a['vref_v'] == 1.21,
            'only primary-source-qualified TPS7A4501 ground-referenced divider supported')
    pins = native.nets(a['regulator'], 'ic')
    require(pins == {'1':a['input_net'], '2':a['input_net'], '3':a['ground_net'],
                     '4':a['output_net'], '5':a['feedback_net'], '6':a['ground_net']},
            'only qualified six-pin TPS7A4501 topology supported')
    divider = reg.get('feedback_divider')
    require(isinstance(divider, dict), 'native divider not evaluated')
    values = []
    for side, ref, nets in (('r_top', a['top'], {a['output_net'], a['feedback_net']}),
                            ('r_bottom', a['bottom'], {a['feedback_net'], a['ground_net']})):
        resistor_nets = native.nets(ref, 'resistor')
        require(len(resistor_nets) == 2 and set(resistor_nets.values()) == nets, 'divider disconnected or wrong topology')
        resistor = divider.get(side, {})
        require(isinstance(resistor, dict), 'malformed native divider resistor')
        require(resistor.get('ref') == ref and finite(resistor.get('ohms')) and resistor['ohms'] > 0,
                'missing positive native resistance')
        c = native.component(ref, 'resistor')
        require(finite(c.get('parsed_value')) and c['parsed_value'] == resistor['ohms'],
                'contradictory native resistor value')
        values.append(resistor['ohms'])
    require(reg.get('vref_source') == 'lookup' and reg.get('assumed_vref') == a['vref_v']
            and reg.get('_estimated_vout_provenance') == {'source': 'divider_plus_lookup', 'confidence': 'heuristic'},
            'unsupported or contradictory native vref provenance')
    value = float(Decimal(str(a['vref_v'])) * (1 + Decimal(str(values[0])) / Decimal(str(values[1]))))
    require(finite(reg.get('estimated_vout')) and abs(reg['estimated_vout'] - value) <= .00051,
            'native voltage inconsistent with captured ideal-divider assumption')
    return value


def protection(native, rule):
    a = rule['assumptions']
    require(rule['components'] == [a['connector']] and a['subcheck'] == 'vbus_esd_protection',
            'only exact USB VBUS presence subcheck supported')
    connector = native.component(a['connector'], 'connector')
    require('USB' in (connector.get('value', '') + connector.get('lib_id', '')).upper(), 'not a native USB connector')
    require(a['vbus_net'] in native.nets(a['connector']).values(), 'VBUS net missing')
    pins = native.reports['schematic']['nets'][a['vbus_net']]['pins']
    require(all(isinstance(p, dict) and all(isinstance(p.get(k), str) and p[k]
                for k in ('component', 'pin_number')) for p in pins),
            'malformed native VBUS pin inventory')
    require(len({(p['component'], p['pin_number']) for p in pins}) == len(pins),
            'ambiguous native VBUS pin inventory')
    unique(pins, lambda p: p.get('component') == a['connector'] and p.get('pin_name') in ('VBUS','V+','VCC','VUSB'),
           'missing or ambiguous VBUS pin')
    usb = native.reports['schematic'].get('usb_compliance', {})
    require(isinstance(usb, dict), 'malformed native USB subchecks')
    i, entry = unique(usb.get('connectors'), lambda c: c.get('connector') == a['connector'],
                      'missing or ambiguous USB subcheck target')
    checks = entry.get('checks', {})
    require(isinstance(checks, dict) and checks.get(a['subcheck']) in ('pass', 'fail'), 'VBUS subcheck not evaluated')
    recognized = []
    for ref in sorted({p['component'] for p in pins}):
        c = native.component(ref)
        text = (c.get('value','') + ' ' + c.get('lib_id','')).lower()
        if c.get('type') == 'diode' and any(k in text for k in ('tvs','esd','smaj','smbj','p6ke')):
            recognized.append(ref)
        elif c.get('type') == 'ic' and any(k in text for k in
                ('usblc','prtr5v','ip4','sp0','tpd','esd','pesd','rclamp','nup','lesd')):
            recognized.append(ref)
    require((checks[a['subcheck']] == 'pass') == bool(recognized), 'contradictory native device presence')
    native.locators.insert(0, {'raw_output_path': 'schematic.json',
        'raw_locator': f'/usb_compliance/connectors/{i}/checks/vbus_esd_protection'})
    return 1 if recognized else 0


def power_inventory(native, rule):
    a = rule['assumptions']
    expected = {(p['component'],p['pin']): p for p in a['pins']}
    require(set(rule['components']) == {p['component'] for p in a['pins']}, 'power target identity mismatch')
    nets = native.reports['schematic'].get('nets')
    require(isinstance(nets, dict) and bool(nets), 'missing native net inventory')
    actual = {}
    for net, data in nets.items():
        require(isinstance(data, dict) and isinstance(data.get('pins'), list), 'malformed net inventory')
        if net == a['ground_net']:
            continue
        for p in data['pins']:
            require(isinstance(p, dict), 'malformed native pin')
            if p.get('pin_type') not in ('power_in','power_out'):
                continue
            key = (p.get('component'), p.get('pin_number'))
            require(key not in actual and all(isinstance(k,str) and k for k in key), 'ambiguous native power pin')
            actual[key] = (net, p['pin_type'])
    require(set(actual) == set(expected), 'complete native power-pin inventory differs from declared coverage')
    for ref, pin in actual:
        require(native.nets(ref).get(pin) == actual[(ref,pin)][0], 'contradictory native pin connectivity')
    # The criterion describes only this complete typed pin inventory, not
    # detector-estimated currents, hidden power pins or whole-board adequacy.
    expected_sources = {}
    for p in a['pins']:
        if p['role'] == 'power_out':
            expected_sources[p['net']] = expected_sources.get(p['net'],0)+1
    require(all(n == 1 for n in expected_sources.values()) and
            all(p['net'] in expected_sources for p in a['pins']), 'declared rail has missing/duplicated source')
    native.locators.insert(0, {'raw_output_path':'schematic.json','raw_locator':'/nets'})
    return int(all(actual[k] == (p['net'],p['role']) for k,p in expected.items()))


def capacitor_voltage(native, rule):
    a = rule['assumptions']
    require(len(rule['components']) == 1 and a['rail_net'] != a['ground_net'], 'ambiguous capacitor target')
    ref = rule['components'][0]
    c = native.component(ref, 'capacitor')
    require(set(native.nets(ref).values()) == {a['rail_net'],a['ground_net']}
            and len(c['pin_nets']) == 2, 'capacitor power/ground topology missing')
    text = c.get('value','')
    require(isinstance(text,str) and a['dielectric'] in ('X7R','X5R','C0G','NP0')
            and re.search(r'(?<![A-Z0-9])' + a['dielectric'] + r'(?![A-Z0-9])', text.upper()),
            'unsupported or missing explicit dielectric')
    ratings = re.findall(r'(?:^|[/\s])(\d+(?:\.\d+)?)V(?=$|[/\s])', text, re.I)
    require(len(ratings) == 1 and float(ratings[0]) > 0, 'missing or ambiguous native voltage rating')
    rating = float(ratings[0])
    reg = native.finding(a['regulator'],'PR-DET')
    require(reg.get('output_rail') == a['rail_net'], 'native capacitor rail missing')
    cap_index, cap = unique(reg.get('output_capacitors'), lambda c: c.get('ref') == ref,
                    'capacitor rating branch not evaluated')
    reg_locator = native.locators[-1]
    native.locators.insert(0, {'raw_output_path': 'schematic.json',
        'raw_locator': reg_locator['raw_locator'] + f'/output_capacitors/{cap_index}'})
    require(finite(cap.get('rated_voltage_V')) and cap.get('rated_voltage_V') == rating and cap.get('dielectric') == a['dielectric'],
            'contradictory native rating/dielectric')
    # Operating voltage is normative, not the detector-estimated rail voltage.
    return a['operating_v'] / rating


def thermal_estimate(native, rule):
    a = rule['assumptions']
    require(len(rule['components']) == 1, 'thermal estimate requires exact component')
    ref = rule['components'][0]
    native.component(ref,'ic')
    assessment = native.item('thermal','assessments',lambda x: x.get('ref') == ref and x.get('rule_id') == 'TH-DET')
    native.locators.insert(0, native.locators.pop())
    require(assessment.get('component_type') == 'ldo' and assessment.get('pdiss_confidence') == 'heuristic',
            'only native heuristic LDO dissipation supported')
    require(a['load_source'] == 'output_cap_proxy' and a['rtheta_source'] == 'package_table'
            and a['tj_max_source'] == 'default_125', 'unsupported thermal assumption contract')
    require(assessment.get('rtheta_ja_source') == 'package_table'
            and assessment.get('tj_max_source') == 'default_125' and assessment.get('tj_max_c') == 125,
            'missing native thermal provenance')
    # This version qualifies just these two generic table entries, no datasheet
    # resistance or enhanced-copper board correction is inferred.
    resistance = {'SOT-223':60.0, 'SOT-23':250.0}.get(assessment.get('package'))
    require(resistance is not None and assessment.get('rtheta_ja_raw') == resistance
            and assessment.get('pcb_correction') == 1.0 and assessment.get('rtheta_ja_effective') == resistance,
            'unknown package or unqualified board thermal correction')
    reg = native.finding(ref,'PR-DET')
    pdiss = reg.get('power_dissipation')
    require(isinstance(pdiss,dict) and reg.get('topology') == 'LDO', 'native LDO dissipation absent')
    require(isinstance(pdiss.get('_iout_provenance'), dict), 'missing load provenance')
    require(pdiss['_iout_provenance'].get('source') == 'output_cap_proxy'
            and all(pdiss.get(k) == a[v] for k,v in (
                ('vin_estimated_V','input_v'),('vout_V','output_v'),('estimated_iout_A','load_a'))),
            'native heuristic load/voltage differs from captured operating assumption')
    power_decimal = (Decimal(str(a['input_v'])) - Decimal(str(a['output_v']))) * Decimal(str(a['load_a']))
    power = float(power_decimal)
    require(power > .01 and finite(assessment.get('pdiss_w'))
            and abs(assessment['pdiss_w'] - power) <= .00051
            and finite(pdiss.get('estimated_pdiss_W')) and abs(pdiss['estimated_pdiss_W'] - power) <= .00051,
            'inconsistent native power calculation')
    require(assessment.get('ambient_c') == a['ambient_c'] and isinstance(assessment.get('pdiss_source'),str)
            and bool(assessment['pdiss_source']), 'missing ambient/dissipation source')
    value = float(Decimal(str(a['ambient_c'])) + power_decimal * Decimal(str(resistance)))
    require(finite(assessment.get('tj_estimated_c')) and abs(assessment['tj_estimated_c'] - value) <= .051
            and finite(assessment.get('margin_c'))
            and abs(assessment['margin_c'] - (125-value)) <= .051, 'inconsistent native temperature calculation')
    return value


def external_connector(fp):
    ref = fp.get('reference','')
    val = fp.get('value','').lower()
    lib = fp.get('library',fp.get('lib_id','')).lower()
    if ref.startswith(('J','P','CN')):
        return not (any(k in val for k in ('test','tp','jumper','solder')) or ref.upper().startswith(('JP','SJ')))
    return any(k in lib for k in ('connector','conn_','usb'))


def esd_distance(native, rule):
    a = rule['assumptions']
    require(set(rule['components']) == {a['protection'],a['connector']}, 'ESD pair target mismatch')
    require(rule.get('max') == 15 and 'min' not in rule, 'ES-001 supports its native 15mm upper bound only')
    pd = native.finding(a['protection'],'PD-DET')
    require(pd.get('type') in ('tvs','esd','esd_ic','tvs_array','varistor')
            and pd.get('protected_net') == a['protected_net'], 'native ESD path branch skipped or unsupported')
    require(a['protected_net'] in native.nets(a['protection']).values()
            and a['protected_net'] in native.nets(a['connector'],'connector').values(),
            'protection/connector electrical association missing')
    protection_fp = native.item('pcb','footprints',lambda f: f.get('reference') == a['protection'])
    connector_fp = native.item('pcb','footprints',lambda f: f.get('reference') == a['connector'])
    for fp in (protection_fp, connector_fp):
        pads = fp.get('pad_nets')
        require(isinstance(pads,dict) and bool(pads) and all(isinstance(p,dict) for p in pads.values()),
                'missing native PCB pad connectivity')
        require(a['protected_net'] in {p.get('net') for p in pads.values()},
                'native PCB protection/connector electrical association missing')
    footprints = native.reports['pcb']['footprints']
    require(all(isinstance(f.get('reference'),str) and isinstance(f.get('value'),str)
                and isinstance(f.get('library',f.get('lib_id','')),str) for f in footprints),
            'malformed native footprint identity')
    require(len({f['reference'] for f in footprints}) == len(footprints), 'ambiguous native footprint inventory')
    connectors = [f for f in footprints if external_connector(f)]
    require(bool(connectors), 'native external connector branch skipped')
    require(all(finite(f.get(k)) for f in [protection_fp]+connectors for k in ('x','y')),
            'native footprint position missing')
    distances = sorted((math.hypot(f['x']-protection_fp['x'], f['y']-protection_fp['y']), f['reference'])
                       for f in connectors)
    distance, nearest = distances[0]
    require(nearest == a['connector'] and (len(distances) == 1 or distances[1][0] != distance),
            'nearest native connector is unrelated or ambiguous')
    require(distance <= 50, 'native ES-001 skips distance greater than 50mm')
    # Reconstruct this exact native branch's eligibility; no inference from
    # category scores or the absence of another EMC rule is made.
    findings = native.reports['emc'].get('findings')
    require(isinstance(findings,list), 'missing native EMC findings')
    matches = [(i,f) for i,f in enumerate(findings) if f.get('rule_id') == 'ES-001'
               and f.get('components') == [a['protection'],a['connector']]]
    require(len(matches) == int(distance > 15), 'contradictory native ES-001 branch result')
    if matches:
        i,f = matches[0]
        require(not f.get('suppressed'), 'suppressed native ES-001')
        native.locators.insert(0, {'raw_output_path':'emc.json','raw_locator':f'/findings/{i}'})
    return distance


EVALUATORS = {'regulator-feedback/1': feedback, 'usb-vbus-presence/1': protection,
              'power-pin-inventory/1': power_inventory, 'capacitor-voltage/1': capacitor_voltage,
              'thermal-ldo-estimate/1': thermal_estimate, 'esd-distance/1': esd_distance}


def evaluate(reports, rule):
    native = Native(reports)
    result = {'id': rule['id'], 'status': 'not_evaluated', 'adapter': rule['adapter'],
              'criterion_id': identity(rule)}
    try:
        value = EVALUATORS[rule['adapter']](native, rule)
        require(finite(value), 'nonfinite adapter calculation')
        failed = ('min' in rule and value < rule['min'] or 'max' in rule and value > rule['max'])
        result.update(status='violation' if failed else 'satisfied', value=value,
                      native_locator={'components': rule['components'], 'nets': [], 'pins': []},
                      raw_output_path=native.locators[0]['raw_output_path'],
                      raw_locator=native.locators[0]['raw_locator'], native_sources=native.locators,
                      assumptions=rule['assumptions'], assumptions_source=rule['assumptions_source'])
    except NotEvaluated as exc:
        result['reason'] = str(exc)
    return result

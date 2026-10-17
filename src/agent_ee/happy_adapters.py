"""Closed target-level adapters for installed kicad-happy 2.3.1/schema 1.4.0.

Native findings remain informational. These coordinator calculations establish
only the named bounded proxy, with captured assumptions, never certification.
"""
import math
import re

# Native entrypoint and source branches are documented in docs/kicad-happy.md.
CONTRACTS = {
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
    if not {'min', 'max'} & rule.keys():
        raise ValueError('adapter requires bounds')


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
            pins = inventory.get(net, {}).get('pins')
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
    pins = native.nets(a['regulator'], 'ic')
    require(set(pins.values()) == {a['input_net'], a['output_net'], a['feedback_net']},
            'only three-pin adjustable regulator supported')
    divider = reg.get('feedback_divider')
    require(isinstance(divider, dict), 'native divider not evaluated')
    values = []
    for side, ref, nets in (('r_top', a['top'], {a['output_net'], a['feedback_net']}),
                            ('r_bottom', a['bottom'], {a['feedback_net'], a['ground_net']})):
        require(set(native.nets(ref, 'resistor').values()) == nets, 'divider disconnected or wrong topology')
        resistor = divider.get(side, {})
        require(resistor.get('ref') == ref and finite(resistor.get('ohms')) and resistor['ohms'] > 0,
                'missing positive native resistance')
        c = native.component(ref, 'resistor')
        require(finite(c.get('parsed_value')) and c['parsed_value'] == resistor['ohms'],
                'contradictory native resistor value')
        values.append(resistor['ohms'])
    require(reg.get('vref_source') == 'lookup' and reg.get('assumed_vref') == a['vref_v']
            and reg.get('_estimated_vout_provenance') == {'source': 'divider_plus_lookup', 'confidence': 'heuristic'},
            'unsupported or contradictory native vref provenance')
    value = a['vref_v'] * (1 + values[0] / values[1])
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
    unique(pins, lambda p: p.get('component') == a['connector'] and p.get('pin_name') in ('VBUS','V+','VCC','VUSB'),
           'missing or ambiguous VBUS pin')
    usb = native.reports['schematic'].get('usb_compliance', {})
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


EVALUATORS = {'regulator-feedback/1': feedback, 'usb-vbus-presence/1': protection,
              'power-pin-inventory/1': power_inventory}


def evaluate(reports, rule):
    native = Native(reports)
    result = {'id': rule['id'], 'status': 'not_evaluated', 'adapter': rule['adapter']}
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

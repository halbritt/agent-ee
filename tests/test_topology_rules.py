"""Requirement topology oracle over native exports and owned synthetic graphs."""
import copy
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee.baseline import freeze_evidence, verify_evidence
from agent_ee.candidate import capture
from agent_ee.checks import Ctx, CheckError, check_topology_rules, run_checks
from agent_ee.storage import read_json, write_json
from agent_ee.topology_rules import capability, evaluate, quantity, validate_rules
from toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/topology-rules'
spec = importlib.util.spec_from_file_location('topology_fixture', FIXTURE / 'generate.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def graph(parts, dnp=()):
    """Native-inventory-shaped graph from ``(ref, symbol, value, labels)`` parts."""
    components, nets, memberships = {}, {}, {}
    codes = {}
    for ref, symbol, value, labels in parts:
        pins = tuple(num for num, _ in generator.SYMBOLS[symbol])
        components[ref] = {'reference': ref, 'lib_id': symbol, 'value': value,
                           'populated': ref not in dnp, 'pins': pins}
        for pin, label in zip(pins, labels):
            name = f'unconnected-({ref}-Pad{pin})' if label == 'NC' else '/' + label
            nid = codes.setdefault(name, str(len(codes) + 1))
            net = nets.setdefault(nid, {'name': name, 'terminals': (), 'no_connect_marker': label == 'NC',
                                        'locator': f'nets/net[@code={nid!r}]'})
            net['terminals'] += ((ref, pin),)
            memberships[ref, pin] = nid
    return {'components': components, 'nets': nets, 'memberships': memberships}


def records(report):
    return {r['id']: r for r in report['records']}


def only(document, *ids):
    document = copy.deepcopy(document)
    document['rules'] = [r for r in document['rules'] if r['id'] in ids]
    return document


class QuantityTests(unittest.TestCase):
    def test_written_values_and_refusals(self):
        for value, expected in (('4.7k', ('4700', None)), ('4k7', ('4700', None)), ('120R', ('120', 'ohm')),
                                ('120', ('120', None)), ('10 kΩ', ('10000', 'ohm')), ('100nF', ('1E-7', 'F')),
                                ('4n7', ('4.7E-9', None)), ('1M', ('1E+6', None)), ('1m', ('0.001', None)),
                                ('2R2', ('2.2', None))):
            with self.subTest(value=value):
                got = quantity(value)
                self.assertEqual((got[0].normalize(), got[1]), (type(got[0])(expected[0]).normalize(), expected[1]))
        for value in ('', '10k 1%', 'DNP', '4.7kk', None, 'green'):
            with self.subTest(value=value):
                self.assertIsNone(quantity(value))


class RuleFileTests(unittest.TestCase):
    def test_owned_rule_files_validate(self):
        validate_rules(generator.hub_rules())
        validate_rules(generator.driver_rules())

    def test_malformed_rule_files_are_refused(self):
        for mutate in (
            lambda d: d.update(schema='agent-ee.topology-rules/2'),
            lambda d: d.update(extra=True),
            lambda d: d['classes'].update({'NoColon': {'class': 'resistor', 'terminals': {'1': '1', '2': '2'}}}),
            lambda d: d['classes']['Owned:R'].update({'class': 'thermistor'}),
            lambda d: d['classes']['Owned:R'].update(terminals={'1': '1', '2': '1'}),
            lambda d: d['classes']['Owned:LED'].update(terminals={'A': '2'}),
            lambda d: d['rules'].append(copy.deepcopy(d['rules'][0])),
            lambda d: d['rules'][0].update(type='nearby'),
            lambda d: d['rules'][0].update(min=0),
            lambda d: d['rules'][0].update(min=True),
            lambda d: d['rules'][0].update(max=3),
            lambda d: d['rules'][0].update(nets=['VBUS+']),
            lambda d: d['rules'][0].update(nets=['PGND', 'PGND']),
            lambda d: d['rules'][0].update(**{'class': 'connector'}),
            lambda d: d['rules'][0].update(**{'class': 'transistor'}),
            lambda d: d['rules'][1].update(**{'class': 'ferrite'}),
            lambda d: d['rules'][2].update(terminal={'reference': 'U1'}),
            lambda d: d['rules'][4].update(value='about 120'),
            lambda d: d['rules'][5].update(references=['R11']),
            lambda d: d['rules'][7].pop('net'),
            lambda d: d['rules'][7].update(id='-bad'),
            lambda d: d['rules'][7].update(requirement=''),
            lambda d: d.update(rules=[]),
        ):
            document = generator.driver_rules()
            mutate(document)
            with self.subTest(document=document), self.assertRaises((ValueError, TypeError, KeyError)):
                validate_rules(document)

    def test_malformed_expected_components_are_refused(self):
        for mutate in (
            lambda r: r.update(match='subset'),
            lambda r: r.update(components=[]),
            lambda r: r['components'].append(copy.deepcopy(r['components'][0])),
            lambda r: r['components'][6].update(terminals={'anode': 'LED_A'}),
            lambda r: r['components'][3].update(value='pull-up'),
            lambda r: r['components'][3].update(symbol='Owned:C'),
            lambda r: r.update(fixed_nets={'NOT_A_ROLE': '/GND'}),
            lambda r: r.update(fixed_nets={'GND': '/GND', 'VCC': '/GND'}),
            lambda r: r.update(max_visits=0),
            lambda r: r.update(max_visits=10**7),
        ):
            document = generator.hub_rules()
            mutate(document['rules'][0])
            with self.subTest(rule=document['rules'][0]), self.assertRaises((ValueError, TypeError, KeyError)):
                validate_rules(document)

    def test_capability_binds_every_rule_to_a_mapping_requirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(Path(tmp) / 'rules.json', generator.hub_rules())
            files = {'rules.json': 'x', 'req.md': 'y'}
            params = {'rules': 'rules.json', 'netlist_check': 'netlist'}

            def scope(*ids, path='req.md'):
                return {'baseline': {'requirements': [{'id': i, 'source': {'path': path, 'locator': i},
                                                       'checks': ['netlist', 'rules']} for i in ids]}}
            descriptor = capability(params, scope('HUB-1', 'HUB-2'), files, tmp, 'rules')
            self.assertEqual(descriptor['predicates'], ['topology-rule:hub-shape', 'topology-rule:sda-pullup'])
            self.assertEqual(descriptor['immutable_inputs'], ['rules.json'])
            for bad_scope, bad_files, bad_params in (
                (scope('HUB-1'), files, params), (scope('HUB-1', 'HUB-2', 'HUB-3'), files, params),
                (scope('HUB-1', 'HUB-2', path='other.md'), files, params),
                (scope('HUB-1', 'HUB-2'), {'req.md': 'y'}, params),
                (scope('HUB-1', 'HUB-2'), files, {'rules': '../rules.json', 'netlist_check': 'netlist'}),
                (scope('HUB-1', 'HUB-2'), files, {'rules': 'rules.json'}),
            ):
                with self.subTest(scope=bad_scope, files=bad_files, params=bad_params), self.assertRaises(ValueError):
                    capability(bad_params, bad_scope, bad_files, tmp, 'rules')


class EvaluationTests(unittest.TestCase):
    def test_absent_or_ambiguous_names_are_unknown_with_reason(self):
        document = generator.driver_rules()
        document['rules'][0]['nets'] = ['VBUS+', 'PGND_MISSING']
        document['rules'][2]['terminal'] = {'reference': 'U9', 'pin': '3'}
        document['rules'][3]['terminal'] = {'reference': 'U1', 'pin': '9'}
        document['rules'][5]['references'] = ['R11', 'R99']
        document['rules'][7]['net'] = '/VCC_MISSING'
        report = evaluate(validate_rules(document), graph(generator.driver()))
        found = records(report)
        self.assertEqual(report['outcome'], 'unknown')
        self.assertEqual(report['execution'], 'unsupported')
        self.assertEqual(sorted(report['unknown']), ['bulk-caps', 'divider-top', 'en-unconnected',
                                                     'gate-resistor', 'vcc-pin'])
        self.assertIn("net 'PGND_MISSING' is absent", found['bulk-caps']['reasons'][0])
        self.assertIn('reference U9 is absent', found['gate-resistor']['reasons'][0])
        self.assertIn('pin U1.9 is absent', found['en-unconnected']['reasons'][0])
        self.assertIn('reference R99 is absent', found['divider-top']['reasons'][0])
        self.assertIn("net '/VCC_MISSING' is absent", found['vcc-pin']['reasons'][0])
        parts = generator.driver() + [('J9', 'Owned:Conn_4', 'X', ['a/VCC', 'b/VCC', 'X1', 'X2'])]
        report = evaluate(only(generator.driver_rules(), 'no-bootstrap-diode'), graph(parts))
        self.assertEqual(report['outcome'], 'unknown')
        self.assertIn('ambiguous', records(report)['no-bootstrap-diode']['reasons'][0])

    def test_unclassified_parts_turn_defect_or_absence_into_unknown(self):
        parts = [p for p in generator.driver('seven-caps') if p[0] != 'U1']
        parts.append(('X1', 'Owned:Driver', 'mystery', ['VBUS+', 'PGND', 'VCC', 'VB', 'N1']))
        document = only(generator.driver_rules(), 'bulk-caps', 'no-bootstrap-diode', 'can-termination')
        del document['classes']['Owned:Driver']
        report = evaluate(validate_rules(document), graph(parts))
        found = records(report)
        self.assertEqual(found['bulk-caps']['status'], 'unknown')
        self.assertEqual(found['bulk-caps']['possible_unclassified'], ['X1'])
        self.assertEqual(found['no-bootstrap-diode']['status'], 'unknown')
        self.assertEqual(found['can-termination']['status'], 'satisfied')
        self.assertEqual(report['unclassified']['X1']['reason'], 'symbol has no declared class')

    def test_forbidden_bridge_through_an_undeclared_pin(self):
        document = only(generator.driver_rules(), 'no-bootstrap-diode')
        document['classes']['Owned:Conn_4'] = {'class': 'transistor', 'terminals': {'B': '1'}}
        document['rules'][0]['class'] = 'transistor'
        parts = generator.driver() + [('Q1', 'Owned:Conn_4', 'NPN', ['X1', 'X2', 'VCC', 'VB'])]
        found = records(evaluate(validate_rules(document), graph(parts)))['no-bootstrap-diode']
        self.assertEqual((found['status'], found['offending']), ('violation', ['Q1']))
        document['classes']['Owned:Conn_4'] = {'class': 'diode', 'terminals': {'K': '1', 'A': '2'}}
        document['rules'][0]['class'] = 'diode'
        found = records(evaluate(validate_rules(document), graph(parts)))
        self.assertEqual(found['no-bootstrap-diode']['status'], 'unknown')
        self.assertIn('Q1', found['no-bootstrap-diode']['possible_unclassified'])

    def test_dnp_parts_are_not_counted(self):
        report = evaluate(only(generator.driver_rules(), 'bulk-caps'), graph(generator.driver(), dnp={'C8'}))
        self.assertEqual(records(report)['bulk-caps']['count'], 7)
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(report['excluded_dnp'], ['C8'])

    def test_count_bounds_and_value_mismatch(self):
        document = only(generator.driver_rules(), 'bulk-caps', 'can-termination')
        document['rules'][0]['max'] = 9
        report = evaluate(document, graph(generator.driver()))
        self.assertEqual(report['outcome'], 'pass')
        document['rules'][0].update(min=1, max=7)
        document['rules'][1]['value'] = '100R'
        found = records(evaluate(document, graph(generator.driver())))
        self.assertEqual(found['bulk-caps']['status'], 'violation')
        self.assertEqual(found['can-termination']['status'], 'violation')
        self.assertEqual(found['can-termination']['offending'], ['RT1'])

    def test_pin_rules(self):
        parts = generator.driver()
        parts[0] = ('U1', 'Owned:Driver', 'DRV', ['VCC', 'VB', 'OUTH', 'VCC', 'PGND'])
        found = records(evaluate(only(generator.driver_rules(), 'en-unconnected', 'vcc-pin'), graph(parts)))
        self.assertEqual(found['en-unconnected']['status'], 'violation')
        self.assertEqual(sorted(found['en-unconnected']['offending']), [['C21', '1'], ['U1', '1']])
        self.assertEqual(found['vcc-pin']['status'], 'satisfied')
        parts[0] = ('U1', 'Owned:Driver', 'DRV', ['VB', 'VB', 'OUTH', 'NC', 'PGND'])
        found = records(evaluate(only(generator.driver_rules(), 'en-unconnected', 'vcc-pin'), graph(parts)))
        self.assertEqual(found['en-unconnected']['status'], 'satisfied')
        self.assertTrue(found['en-unconnected']['no_connect_marker'])
        self.assertEqual((found['vcc-pin']['status'], found['vcc-pin']['actual_net']['name']), ('violation', '/VB'))

    def test_hub_matcher_mapping_swaps_and_polarity(self):
        document = only(generator.hub_rules(), 'hub-shape')
        report = evaluate(document, graph(generator.hub()))
        self.assertEqual(report['outcome'], 'pass')
        self.assertEqual(records(report)['hub-shape']['mapping'], generator.HUB_MAPPING)
        swapped = records(evaluate(document, graph(generator.hub('resistor-swap'))))['hub-shape']
        self.assertEqual(swapped['status'], 'satisfied')
        self.assertEqual(swapped['mapping']['roles'], generator.HUB_MAPPING['roles'])
        self.assertEqual(swapped['mapping']['nets'], generator.HUB_MAPPING['nets'])
        self.assertEqual(swapped['mapping']['swapped'], ['R_SDA'])
        led = records(evaluate(document, graph(generator.hub('led-swap'))))['hub-shape']
        self.assertEqual(led['status'], 'violation')
        self.assertNotIn('mapping', led)
        self.assertIn('deepest_partial', led)

    def test_connector_terminals_are_never_swapped(self):
        parts = generator.hub()
        parts[0] = ('J1', 'Owned:Conn_4', 'UP', ['VCC', 'GND', 'SCL', 'SDA'])
        found = records(evaluate(only(generator.hub_rules(), 'hub-shape'), graph(parts)))['hub-shape']
        self.assertEqual(found['status'], 'violation')

    def test_kind_count_mismatch_names_counts(self):
        found = records(evaluate(only(generator.hub_rules(), 'hub-shape'), graph(generator.hub('extra-resistor'))))
        record = found['hub-shape']
        self.assertEqual(record['status'], 'violation')
        self.assertEqual(record['kind_counts']['resistor'], {'expected': 3, 'actual': 4})
        self.assertIn('resistor expected 3 actual 4', record['reasons'][0])
        parts = [p for p in generator.hub() if p[0] != 'C1']
        record = records(evaluate(only(generator.hub_rules(), 'hub-shape'), graph(parts)))['hub-shape']
        self.assertEqual(record['status'], 'violation')
        self.assertIn('capacitor expected 1 actual 0', record['reasons'][0])

    def test_missing_value_and_unclassified_extra_part_are_unknown(self):
        document = only(generator.hub_rules(), 'hub-shape')
        record = records(evaluate(document, graph(generator.hub('missing-value'))))['hub-shape']
        self.assertEqual(record['status'], 'unknown')
        self.assertIn('missing or unreadable values: R1', record['reasons'][0])
        record = records(evaluate(document, graph(generator.hub('test-point'))))['hub-shape']
        self.assertEqual(record['status'], 'unknown')
        self.assertEqual(record['unclassified'], ['TP1'])

    def test_embedded_match_inside_a_larger_design(self):
        document = only(generator.hub_rules(), 'hub-shape')
        document['rules'][0]['match'] = 'embedded'
        document['rules'][0]['fixed_nets'] = {'GND': '/GND'}
        parts = generator.hub() + [('R9', 'Owned:R', '1k', ['SCL', 'GND']), ('TP1', 'Owned:TP', 'TP', ['SDA'])]
        record = records(evaluate(document, graph(parts)))['hub-shape']
        self.assertEqual(record['status'], 'satisfied')
        self.assertEqual(record['mapping']['nets']['GND'], '/GND')
        record = records(evaluate(document, graph([p for p in generator.hub() if p[0] != 'R2'])))['hub-shape']
        self.assertEqual(record['status'], 'violation')
        led = records(evaluate(document, graph(generator.hub('led-swap') + [('TP1', 'Owned:TP', 'TP', ['SDA'])])))
        self.assertEqual(led['hub-shape']['status'], 'unknown')
        document['rules'][0]['fixed_nets'] = {'GND': 'NOPE'}
        record = records(evaluate(document, graph(generator.hub())))['hub-shape']
        self.assertEqual(record['status'], 'unknown')

    def test_visit_bound_exhaustion_is_unknown_with_bound(self):
        document = only(generator.hub_rules(), 'hub-shape')
        document['rules'][0]['max_visits'] = 3
        record = records(evaluate(validate_rules(document), graph(generator.hub())))['hub-shape']
        self.assertEqual(record['status'], 'unknown')
        self.assertEqual(record['max_visits'], 3)
        self.assertEqual(record['visits'], 4)
        self.assertIn('visit bound 3 exhausted', record['reasons'][0])
        # Twelve interchangeable resistors and an impossible net-role demand exhaust the default bound.
        rows = [(f'R{i}', 'Owned:R', '1k', [f'N{i}', f'N{i + 1}']) for i in range(12)]
        roles = [{'role': f'X{i}', 'class': 'resistor', 'terminals': {'1': 'A', '2': f'B{i}'}} for i in range(11)]
        roles.append({'role': 'Y', 'class': 'resistor', 'terminals': {'1': 'Z', '2': 'Z2'}})
        document = {'schema': 'agent-ee.topology-rules/1', 'classes': generator.CLASSES,
                    'rules': [{'id': 'star', 'requirement': 'S', 'type': 'expected_components',
                               'match': 'embedded', 'components': roles}]}
        record = evaluate(validate_rules(document), graph(rows))['records'][0]
        self.assertEqual(record['status'], 'violation')
        rows = [(f'R{i}', 'Owned:R', '1k', ['A', f'N{i}']) for i in range(12)]
        roles = [{'role': f'X{i}', 'class': 'resistor', 'terminals': {'1': 'A', '2': f'B{i}'}} for i in range(12)]
        roles.append({'role': 'Y', 'class': 'resistor', 'terminals': {'1': 'A', '2': 'B0'}})
        rows.append(('R99', 'Owned:R', '1k', ['A', 'M']))
        document['rules'][0]['components'] = roles
        record = evaluate(validate_rules(document), graph(rows))['records'][0]
        self.assertEqual(record['status'], 'unknown')
        self.assertEqual(record['max_visits'], 20000)
        self.assertGreater(record['visits'], 20000)


class NativeTopologyTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0

    def run_design(self, design, variant='control', rules=None):
        self.counter += 1
        root = self.root / str(self.counter)
        root.mkdir()
        parts = getattr(generator, design)(variant)
        (root / 'topology.kicad_sch').write_text(generator.schematic(parts))
        document = rules or getattr(generator, design + '_rules')()
        tags = sorted({r['requirement'] for r in document['rules']})
        (root / 'requirements.md').write_text(''.join(f'{t}: topology requirement {t}.\n' for t in tags))
        write_json(root / 'rules.json', document)
        paths = ['topology.kicad_sch', 'requirements.md', 'rules.json']
        scope = {'schema': 'agent-ee.scope/3', 'schematic': 'topology.kicad_sch',
                 'inputs': [{'path': p, 'at': p} for p in paths], 'requirements': ['requirements.md'],
                 'checks': [{'id': 'netlist', 'kind': 'netlist'}, {'id': 'rules', 'kind': 'topology_rules',
                            'params': {'rules': 'rules.json', 'netlist_check': 'netlist'}}],
                 'baseline': {'requirements': [{'id': t, 'statement': f'Topology {t}',
                              'source': {'path': 'requirements.md', 'locator': t},
                              'checks': ['netlist', 'rules'], 'intent': 'acceptance'} for t in tags],
                              'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}}
        write_json(root / 'scope.json', scope)
        run = root / 'run'
        run.mkdir()
        capture(root / 'scope.json', run)
        checks = run_checks(run, time.monotonic() + 60)
        self.assertEqual(checks[0]['execution'], 'ok', checks)
        return run, checks

    def rerun(self, run):
        manifest = read_json(run / 'candidate.json')
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 30)
        try:
            check_topology_rules(ctx)
        except CheckError as exc:
            ctx.result.update(execution=exc.execution, outcome='unknown', summary=str(exc))
        return ctx.result

    def test_driver_caps_diode_gate_and_values(self):
        for variant, violations in (('control', []), ('seven-caps', ['bulk-caps']),
                                    ('bootstrap-diode', ['no-bootstrap-diode']),
                                    ('direct-gate', ['gate-resistor']), ('unequal-divider', ['divider-top'])):
            with self.subTest(variant=variant):
                run, checks = self.run_design('driver', variant)
                result = checks[1]
                self.assertEqual((result['execution'], result['outcome']),
                                 ('ok', 'defect' if violations else 'pass'), result)
                self.assertEqual(result['details']['violations'], violations)
                found = records(result['details'])
                if variant == 'control':
                    self.assertEqual(found['bulk-caps']['count'], 8)
                    self.assertTrue(found['en-unconnected']['no_connect_marker'])
                    self.assertEqual(found['gate-resistor']['matched'], ['RG1'])
                    self.assertEqual(found['can-termination']['matched'], ['RT1'])
                if variant == 'seven-caps':
                    self.assertEqual(found['bulk-caps']['count'], 7)
                if variant == 'bootstrap-diode':
                    self.assertEqual(found['no-bootstrap-diode']['offending'], ['D1'])
                if variant == 'direct-gate':
                    self.assertEqual(found['gate-resistor']['reasons'], ['pin ties directly to the target net'])
                self.assertIn('work/rules/topology-rules.json', result['evidence'])
                self.assertIn('work/netlist/connectivity.json', result['evidence'])
                report = read_json(run / 'work/rules/topology-rules.json')
                self.assertEqual(report['schema'], 'agent-ee.topology-rules-result/1')
                self.assertEqual(len(report['rules_sha256']), 64)
                freeze_evidence(run, checks)
                verify_evidence(run, checks, required=True)

    def test_hub_recorded_mapping_polarity_and_resistor_swap(self):
        run, checks = self.run_design('hub')
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'pass'), checks[1])
        self.assertEqual(records(checks[1]['details'])['hub-shape']['mapping'], generator.HUB_MAPPING)
        _, checks = self.run_design('hub', 'resistor-swap')
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'pass'), checks[1])
        mapping = records(checks[1]['details'])['hub-shape']['mapping']
        self.assertEqual((mapping['roles'], mapping['nets']), (generator.HUB_MAPPING['roles'], generator.HUB_MAPPING['nets']))
        _, checks = self.run_design('hub', 'led-swap')
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'defect'), checks[1])
        self.assertEqual(checks[1]['details']['violations'], ['hub-shape'])

    def test_missing_value_count_mismatch_and_visit_bound(self):
        _, checks = self.run_design('hub', 'missing-value')
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('unsupported', 'unknown'), checks[1])
        _, checks = self.run_design('hub', 'extra-resistor')
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'defect'), checks[1])
        self.assertEqual(records(checks[1]['details'])['hub-shape']['kind_counts']['resistor'],
                         {'expected': 3, 'actual': 4})
        rules = generator.hub_rules()
        rules['rules'][0]['max_visits'] = 2
        _, checks = self.run_design('hub', rules=rules)
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('unsupported', 'unknown'), checks[1])
        self.assertEqual(records(checks[1]['details'])['hub-shape']['max_visits'], 2)

    def test_malformed_rule_file_at_runtime_and_failed_prerequisite(self):
        run, checks = self.run_design('hub')
        self.assertEqual(checks[1]['outcome'], 'pass', checks[1])
        path = run / 'inputs/rules.json'
        original = path.read_bytes()
        for broken in (b'{', b'{"schema": "agent-ee.topology-rules/1", "schema": "x"}', b'[]',
                       b'{"schema": "agent-ee.topology-rules/1", "classes": {}, "rules": []}'):
            path.write_bytes(broken)
            result = self.rerun(run)
            self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)
        path.write_bytes(original)
        connectivity = run / 'work/netlist/connectivity.json'
        connectivity.write_bytes(b'{}')
        result = self.rerun(run)
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'), result)


if __name__ == '__main__':
    unittest.main()

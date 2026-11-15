"""Authored roles select existing electrical checks; no native tools or model calls."""
import copy
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
import unittest

from agent_ee import design_electrical as electrical
from agent_ee.design_compile import compile_design
from agent_ee.design_rules import capability, evaluate
from agent_ee.storage import read_json, write_json
from tests.test_design_compile import design_fixture
from tests import test_design_rules


def terminal(ref, pin):
    return dict(reference=ref, pin=str(pin))


class ElectricalRolesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.roles = dict(differential_buses=[dict(id='bus', positive=terminal('U1', 1),
            negative=terminal('U1', 2), rail=terminal('U1', 3), ground=terminal('U1', 4),
            transceiver=dict(reference='U1', part_number='original-transceiver'))],
            offboard_controls=[dict(id='enable', connector=terminal('J1', 1),
                ic=terminal('U1', 5), net='ENABLE', expected_bias='pulldown', ground=terminal('U1', 4))])
        self.parts = [('U1', 'Owned:XCVR', 'original-transceiver', ['A', 'B', 'VCC', 'GND', 'ENABLE']),
                      ('J1', 'Owned:Header', 'header', ['ENABLE', 'GND']),
                      ('R1', 'Owned:R', '120', ['A', 'B']),
                      ('R2', 'Owned:R', '10k', ['ENABLE', 'GND'])]
        self.design = dict(electrical_roles=self.roles,
            parts={ref: dict(value=value, lib_id=symbol) for ref, symbol, value, _ in self.parts},
            connectors=[dict(reference='J1')], nets={})
        for ref, _, _, nets in self.parts:
            for pin, net in enumerate(nets, 1):
                self.design['nets'].setdefault(net, []).append(f'{ref}.{pin}')
        write_json(self.root / 'topology-rules.json', dict(classes={
            'Owned:R': dict(**{'class': 'resistor'}, terminals=dict(a='1', b='2'))}))

    def test_emitted_rules_detect_missing_bus_bias_and_preserve_control_default(self):
        electrical.validate(self.design)
        electrical.emit(self.design, self.root)
        pack = read_json(self.root / 'electrical-role-rules.json')
        bindings = read_json(self.root / 'electrical-role-bindings.json')
        for biased in (False, True):
            parts = self.parts + ([('R3', 'Owned:R', '680', ['A', 'VCC']),
                                   ('R4', 'Owned:R', '680', ['B', 'GND'])] if biased else [])
            graph = test_design_rules.DifferentialAndControlRuleTests().make_graph(parts)
            report = evaluate(pack, bindings, ['BUS_FAILSAFE', 'CONTROL_DEFAULT'], graph, {})
            cards = {row['id']: row for row in report['cards']}
            self.assertEqual(cards['BUS_FAILSAFE']['status'], 'pass' if biased else 'warning')
            self.assertEqual(cards['CONTROL_DEFAULT']['status'], 'pass')
            if not biased:
                self.assertEqual(cards['BUS_FAILSAFE']['items'][0]['reason'],
                                 'failsafe_bias_and_true_failsafe_absent')
                self.assertEqual(cards['BUS_FAILSAFE']['items'][0]['severity'], 'warning')
        graph = test_design_rules.DifferentialAndControlRuleTests().make_graph(self.parts[:-1])
        report = evaluate(pack, bindings, ['CONTROL_DEFAULT'], graph, {})
        self.assertEqual(next(c for c in report['cards'] if c['id'] == 'CONTROL_DEFAULT')['status'], 'warning')

    def test_resistor_family_symbols_are_classified_without_functional_witnesses(self):
        for symbol in ('Device:R_Small', 'Device:R_US'):
            with self.subTest(symbol=symbol):
                design = copy.deepcopy(self.design)
                for ref in ('R1', 'R2'):
                    design['parts'][ref]['lib_id'] = symbol
                write_json(self.root / 'topology-rules.json', dict(classes={}))
                electrical.emit(design, self.root)
                parts = [(ref, symbol if ref.startswith('R') else lib, value, nets)
                         for ref, lib, value, nets in self.parts]
                report = evaluate(read_json(self.root / 'electrical-role-rules.json'),
                    read_json(self.root / 'electrical-role-bindings.json'),
                    ['BUS_FAILSAFE', 'CONTROL_DEFAULT'],
                    test_design_rules.DifferentialAndControlRuleTests().make_graph(parts), {})
                cards = {c['id']: c for c in report['cards']}
                self.assertEqual(cards['BUS_FAILSAFE']['status'], 'warning')
                self.assertEqual(cards['BUS_FAILSAFE']['items'][0]['reason'],
                                 'failsafe_bias_and_true_failsafe_absent')
                self.assertEqual(cards['CONTROL_DEFAULT']['status'], 'pass')

    def test_emitted_checks_run_on_native_export_and_report_bias_warning(self):
        test_design_rules.require_toolchain(native=True)
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        for biased in (False, True):
            root = self.root / str(biased)
            root.mkdir()
            write_json(root / 'topology-rules.json', read_json(self.root / 'topology-rules.json'))
            electrical.emit(self.design, root)
            parts = self.parts + ([('R3', 'Owned:R', '680', ['A', 'VCC']),
                                   ('R4', 'Owned:R', '680', ['B', 'GND'])] if biased else [])
            generator = test_design_rules.generator
            with patch.object(generator, 'parts', return_value=parts), patch.dict(generator.SYMBOLS,
                    {'Owned:XCVR': ['A', 'B', 'VCC', 'GND', 'ENABLE'], 'Owned:Header': ['ENABLE', 'GND']}):
                (root / 'rules.kicad_sch').write_text(generator.schematic())
            (root / 'requirements.md').write_text('R1: Check the declared original bus and enable roles.')
            checks = [dict(id='netlist', kind='netlist'), dict(id='roles', kind='design_rules', params=dict(
                pack='electrical-role-rules.json', bindings='electrical-role-bindings.json',
                netlist_check='netlist', cards=['BUS_FAILSAFE', 'CONTROL_DEFAULT']))]
            paths = ['rules.kicad_sch', 'requirements.md', 'electrical-role-rules.json', 'electrical-role-bindings.json']
            scope = test_design_rules.scope_for(checks,
                [test_design_rules.requirement('R1', ['netlist', 'roles'])], paths)
            write_json(root / 'scope.json', scope)
            run = root / 'run'
            run.mkdir()
            capture(root / 'scope.json', run)
            results = {r['id']: r for r in run_checks(run, time.monotonic() + 60)}
            self.assertEqual(results['netlist']['execution'], 'ok', results['netlist'])
            result = results['roles']
            self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
            self.assertIn('work/roles/design-rules.json', result['evidence'])
            cards = {c['id']: c for c in result['details']['cards']}
            self.assertEqual(cards['BUS_FAILSAFE']['status'], 'pass' if biased else 'warning')
            self.assertEqual(cards['CONTROL_DEFAULT']['status'], 'pass')

    def test_roles_must_resolve_to_actual_declared_terminals_and_component(self):
        for mutation, message in (
            (lambda d: d['electrical_roles']['differential_buses'][0]['positive'].update(pin='99'), 'no declared net'),
            (lambda d: d['electrical_roles']['differential_buses'][0]['transceiver'].update(part_number='other'), 'match the declared part'),
            (lambda d: d['electrical_roles']['offboard_controls'][0].update(net='OTHER'), 'IC terminal'),
            (lambda d: d['connectors'].clear(), 'declared in connectors'),
            (lambda d: d['electrical_roles']['offboard_controls'][0].pop('ground'), 'explicit ground'),
            (lambda d: d['electrical_roles'].pop('offboard_controls'), 'declare differential_buses'),
            (lambda d: d['electrical_roles']['offboard_controls'][0].update(expected_bias='pullup'), 'explicit rail'),
            (lambda d: d['electrical_roles']['differential_buses'].append(
                copy.deepcopy(d['electrical_roles']['differential_buses'][0])), 'duplicate'),
        ):
            changed = copy.deepcopy(self.design)
            mutation(changed)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                electrical.validate(changed)

    def test_compiler_selects_rule_and_captures_bindings_without_claiming_owner_authority(self):
        captures = self.root / 'captures'
        captures.mkdir()
        design = design_fixture(captures)
        # This original abstract fixture tests compilation, not transceiver performance.
        design['electrical_roles']['differential_buses'] = [dict(id='abstract',
            positive=terminal('R1', 1), negative=terminal('R1', 2),
            rail=terminal('R2', 1), ground=terminal('R2', 2),
            transceiver=dict(reference='R1', part_number='10k'))]
        compile_design(design, captures, self.root / 'bundle')
        root = self.root / 'bundle/inputs'
        mapping = read_json(root / 'requirement-map.json')
        checks = [check for row in mapping['requirements'] for check in row.get('checks', [])]
        check = next(c for c in checks if c['kind'] == 'design_rules')
        self.assertEqual(check['params']['cards'], ['BUS_FAILSAFE'])
        described = capability(check['params'], dict(checks=checks),
            {'electrical-role-rules.json': '', 'electrical-role-bindings.json': ''}, root, check['id'])
        self.assertEqual(described['predicates'], ['design-rule:BUS_FAILSAFE:diff-bus-failsafe-bias/1'])
        self.assertEqual(described['bindings']['roles'], {'differential_buses': design['electrical_roles']['differential_buses']})
        self.assertIn('design-author choices', described['pack']['provenance']['authored_by'])
        self.assertIn('not owner', ' '.join(mapping['scope']['limitations']))
        design.pop('electrical_roles')
        compile_design(design, captures, self.root / 'legacy')
        legacy = read_json(self.root / 'legacy/inputs/requirement-map.json')
        self.assertIn('not selected', ' '.join(legacy['scope']['limitations']))
        self.assertFalse((self.root / 'legacy/inputs/electrical-role-rules.json').exists())

    def test_legacy_design_and_explicit_no_roles_are_distinct_from_checked_roles(self):
        for declaration in (None, dict(differential_buses=[], offboard_controls=[])):
            design = copy.deepcopy(self.design)
            if declaration is None:
                design.pop('electrical_roles')
            else:
                design['electrical_roles'] = declaration
            electrical.validate(design)
            electrical.emit(design, self.root)
            self.assertFalse((self.root / 'electrical-role-rules.json').exists())
            self.assertIn('not selected' if declaration is None else 'were not run', electrical.limitations(design))


if __name__ == '__main__':
    unittest.main()

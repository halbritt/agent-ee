"""Schematic readability lint (AGENTEE-45) over owned, independently specified fixtures.

Expected transforms in fixtures/schematic-lint/generate.py were derived by hand
from KiCad's sheet convention; native tests confirm the pin positions with
kicad-cli 10.0.7 ERC and take net membership only from the native XML netlist.
"""
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee.candidate import capture
from agent_ee.capabilities import describe_check
from agent_ee.checks import run_checks
from agent_ee.executor import execute
from agent_ee.schematic_lint import NATIVE_RULES, RULES, _Lint, capability, lint, load_sheets, profile
from agent_ee.storage import read_json, write_json
from toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/schematic-lint'
spec = importlib.util.spec_from_file_location('lint_fixture', FIXTURE / 'generate.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)

MM = 1000000
PARAMS = {'netlist_check': 'netlist', 'power_nets': ['+3V3', 'VCC_3V3'], 'ground_nets': ['GND'],
          'symbols': {'Lint:R': {'class': 'resistor'}, 'Lint:C': {'class': 'capacitor'},
                      'Lint:LED': {'class': 'led'},
                      'Lint:LDO': {'class': 'regulator', 'input': '1', 'output': '3'}}}
GEOMETRY_RULES = ('symbol-overlap/1', 'label-in-symbol/1', 'wire-overlap/1', 'on-grid/1', 'title-block/1',
                  'hidden-properties/1', 'reference-visible/1', 'reference-prefix/1', 'rail-label/1')


def nm(value):
    return round(value * MM)


def offline(files, rules=GEOMETRY_RULES, **extra):
    """Lint without a native inventory: only rules that need no net membership."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for name, data in files.items():
        (root / name).write_bytes(data)
    params = dict(PARAMS, rules={r: RULES[r] for r in rules}, **extra)
    sheets = load_sheets(root, next(iter(files)))
    tmp.cleanup()
    return sheets, profile(params)


class ProfileTests(unittest.TestCase):
    def test_unknown_rules_parameters_and_declarations_are_refused(self):
        for params in (
            dict(PARAMS, rules={'no-such-rule/1': 'warning'}),
            dict(PARAMS, rules={'symbol-overlap/1': 'blocking'}),
            dict(PARAMS, rules={}),
            dict(PARAMS, extra=True),
            {k: v for k, v in PARAMS.items() if k != 'netlist_check'},
            dict(PARAMS, ground_nets=['+3V3']),
            dict(PARAMS, symbols={'Lint:LDO': {'class': 'regulator', 'input': '1'}}),
            dict(PARAMS, symbols={'Lint:LDO': {'class': 'regulator', 'input': '1', 'output': '1'}}),
            dict(PARAMS, symbols={'R': {'class': 'resistor'}}),
            dict(PARAMS, symbols={'Lint:R': {'class': 'transistor'}}),
            dict(PARAMS, grid_mm=0), dict(PARAMS, grid_mm=1e-7), dict(PARAMS, grid_mm=True),
            dict(PARAMS, title_block=['revision']),
            dict(PARAMS, diff_pairs=[['_P', '_P']]), dict(PARAMS, diff_pairs=[['_P']]),
        ):
            with self.subTest(params=params), self.assertRaises(ValueError):
                profile(params)

    def test_only_rules_raised_to_defect_are_acceptance_predicates(self):
        scope = {'schematic': 'lint.kicad_sch'}
        files = {'lint.kicad_sch': {}}
        self.assertEqual(capability(PARAMS, scope, files, None, 'lint')['predicates'], [])
        raised = dict(PARAMS, rules={'divider-order/1': 'defect', 'on-grid/1': 'info'})
        self.assertEqual(capability(raised, scope, files, None, 'lint')['predicates'],
                         ['schematic-lint:divider-order/1'])
        specs = [{'id': 'netlist', 'kind': 'netlist'}, {'id': 'lint', 'kind': 'schematic_lint', 'params': raised}]
        descriptor = describe_check(specs[1], check_specs=specs, files=files, scope=scope)
        self.assertEqual(descriptor['status'], 'supported', descriptor)
        self.assertTrue(descriptor['can_evaluate_bound'])
        self.assertEqual(descriptor['prerequisites'], [{'check_id': 'netlist', 'kind': 'netlist'}])
        informational = describe_check(dict(specs[1], params=PARAMS), check_specs=specs, files=files, scope=scope)
        self.assertFalse(informational['can_evaluate_bound'])
        for bad in (dict(raised, rules={'no-such-rule/1': 'warning'}), dict(raised, netlist_check='missing')):
            descriptor = describe_check(dict(specs[1], params=bad), check_specs=specs, files=files, scope=scope)
            self.assertEqual(descriptor['status'], 'unsupported', descriptor)
        forward = [specs[1], specs[0]]
        self.assertEqual(describe_check(forward[0], check_specs=forward, files=files, scope=scope)['status'],
                         'unsupported')


class TransformTests(unittest.TestCase):
    def test_rotated_and_mirrored_bodies_and_pins_match_hand_checked_fixture(self):
        sheets, lint_profile = offline(generator.transforms(), rules=('label-in-symbol/1',))
        model = _Lint(sheets, None, lint_profile).sheets[0]
        symbols = {s.reference: s for s in model['symbols']}
        for index, (rot, mirror, body, pin1, pin2) in enumerate(generator.TRANSFORMS):
            ox = generator.TRANSFORM_ORIGIN[0] + generator.TRANSFORM_STEP * index
            oy = generator.TRANSFORM_ORIGIN[1]
            sym = symbols[f'U{index + 1}']
            with self.subTest(rotation=rot, mirror=mirror):
                self.assertEqual(sym.body, (nm(ox + body[0]), nm(oy + body[1]), nm(ox + body[2]), nm(oy + body[3])))
                self.assertEqual({p['number']: p['point'] for p in sym.pins},
                                 {'1': (nm(ox + pin1[0]), nm(oy + pin1[1])), '2': (nm(ox + pin2[0]), nm(oy + pin2[1]))})
        report = lint(sheets, None, lint_profile)
        self.assertEqual(report['outcome'], 'info', report)
        self.assertEqual(sorted(f['items'][0]['text'] for f in report['findings']),
                         [f'IN{i}' for i in range(1, 9)])
        self.assertEqual(sorted(f['items'][1]['reference'] for f in report['findings']),
                         [f'U{i}' for i in range(1, 9)])
        self.assertEqual(report['coverage'], [])


class OfflineRuleTests(unittest.TestCase):
    def run_offline(self, variant, rules=GEOMETRY_RULES, **extra):
        sheets, lint_profile = offline(generator.design(variant), rules=rules, **extra)
        return lint(sheets, None, lint_profile)

    def test_control_has_no_geometry_or_presentation_findings(self):
        report = self.run_offline('control')
        self.assertEqual((report['outcome'], report['findings'], report['coverage']), ('pass', [], []), report)

    def test_off_grid_sheet_is_flagged_with_its_uuid(self):
        report = self.run_offline('off-grid-sheet')
        self.assertEqual([(f['rule'], f['items'][0]['kind'], f['items'][0]['uuid']) for f in report['findings']],
                         [('on-grid/1', 'sheet', generator.uid('sheet/power'))])
        self.assertIn('preserve every existing connection', report['findings'][0]['action'])
        coarse = self.run_offline('control', grid_mm=2.54)
        self.assertTrue(coarse['findings'] and all(f['rule'] == 'on-grid/1' for f in coarse['findings']))

    def test_empty_title_block_revision_and_shared_endpoint_wires(self):
        report = self.run_offline('no-revision')
        self.assertEqual([(f['rule'], f['items'][0]['field'], f['sheet']) for f in report['findings']],
                         [('title-block/1', 'rev', '/')])
        overlap = self.run_offline('wire-overlap')
        self.assertEqual([f['rule'] for f in overlap['findings']], ['wire-overlap/1'] * 2)
        self.assertEqual({f['items'][1]['uuid'] for f in overlap['findings']} |
                         {f['items'][0]['uuid'] for f in overlap['findings']},
                         {generator.uid('wire/' + k) for k in ('mid-a', 'mid-b', 'mid-overlap')})

    def test_same_origin_symbols_name_both(self):
        report = self.run_offline('overlap')
        self.assertEqual([f['rule'] for f in report['findings']], ['symbol-overlap/1'])
        self.assertEqual({i['reference'] for i in report['findings'][0]['items']}, {'R1', 'R7'})

    def test_properties_reference_visibility_and_prefix(self):
        items = [generator.symbol('R1', 'Lint:R', 50.8, 50.8, show=('Footprint',), pins=('1', '2')),
                 generator.symbol('R2', 'Lint:R', 76.2, 50.8, hide=('Reference',), pins=('1', '2')),
                 generator.symbol('U9', 'Lint:R', 101.6, 50.8, pins=('1', '2'))]
        sheets, lint_profile = offline({'p.kicad_sch': generator.schematic(items)})
        report = lint(sheets, None, lint_profile)
        self.assertEqual([(f['rule'], f['items'][0]['reference']) for f in report['findings']],
                         [('hidden-properties/1', 'R1'), ('reference-prefix/1', 'U9'), ('reference-visible/1', 'R2')])

    def test_unresolved_symbol_is_missing_coverage_not_pass(self):
        report = self.run_offline('unresolved')
        self.assertEqual(report['outcome'], 'unknown', report)
        self.assertEqual(sorted(c['reference'] for c in report['coverage']), ['D1', 'D2', 'D3'])
        for gap in report['coverage']:
            self.assertIn('symbol-overlap/1', gap['rules'])
            self.assertEqual(gap['lib_id'], 'Lint:LED')
        # Rules that need no library definition do not inherit the gap.
        title_only = self.run_offline('unresolved', rules=('title-block/1',))
        self.assertEqual(title_only['outcome'], 'pass', title_only)


class NativeLintTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0

    def run_variant(self, variant='control', params=None, checks=None, files=None):
        self.counter += 1
        root = self.root / str(self.counter)
        root.mkdir()
        files = files or generator.design(variant)
        for name, data in files.items():
            (root / name).write_bytes(data)
        (root / 'requirements.md').write_text('Presentation: generated schematics follow the lint profile.')
        paths = [*files, 'requirements.md']
        checks = checks or [{'id': 'netlist', 'kind': 'netlist'},
                            {'id': 'lint', 'kind': 'schematic_lint', 'params': params or PARAMS}]
        scope = {'schema': 'agent-ee.scope/2', 'schematic': 'lint.kicad_sch',
                 'inputs': [{'path': p, 'at': p} for p in paths], 'requirements': ['requirements.md'],
                 'checks': checks,
                 'baseline': {'requirements': [{'id': 'presentation', 'statement': 'Readable schematic',
                     'source': {'path': 'requirements.md', 'locator': 'Presentation'},
                     'checks': [c['id'] for c in checks]}],
                     'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}}
        write_json(root / 'scope.json', scope)
        run = root / 'run'
        run.mkdir()
        capture(root / 'scope.json', run)
        results = {r['id']: r for r in run_checks(run, time.monotonic() + 120)}
        return run, results

    def lint_result(self, variant, params=None, files=None):
        run, results = self.run_variant(variant, params, files=files)
        self.assertEqual(results['netlist']['execution'], 'ok', results['netlist'])
        result = results['lint']
        if result['execution'] == 'ok':
            self.assertEqual(read_json(run / 'work/lint/lint.json')['findings'], result['details']['findings'])
        return result

    def findings(self, result, rule=None):
        return [f for f in result['details']['findings'] if rule is None or f['rule'] == rule]

    def test_correct_fixture_gives_zero_findings(self):
        result = self.lint_result('control')
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual((result['details']['findings'], result['details']['coverage']), ([], []))
        self.assertEqual(result['details']['native_inventory'], 'complete')
        self.assertEqual([s['names'] for s in result['details']['sheets']], ['/', '/power/'])
        self.assertIn('work/lint/lint.json', result['evidence'])
        self.assertIn('work/netlist/design.xml', result['evidence'])

    def test_overlap_wire_through_pin_and_shared_endpoint(self):
        overlap = self.lint_result('overlap')
        self.assertEqual([f['rule'] for f in self.findings(overlap)], ['symbol-overlap/1'], overlap)
        self.assertEqual({i['reference'] for i in self.findings(overlap)[0]['items']}, {'R1', 'R7'})
        cross = self.lint_result('wire-cross')
        found = self.findings(cross)
        self.assertEqual([f['rule'] for f in found], ['wire-crosses-pin/1'], cross)
        self.assertEqual(found[0]['items'][0]['uuid'], generator.uid('wire/sense'))
        self.assertEqual((found[0]['items'][1]['reference'], found[0]['items'][1]['pin']), ('R8', '1'))
        self.assertEqual(found[0]['sheet'], '/')
        # The control's two MID wires share an endpoint on the same net.
        self.assertEqual(self.findings(self.lint_result('control')), [])
        # A hidden (undrawn) pin under the same wire is not a legibility finding.
        files = generator.design('wire-cross')
        pin = b'(at 0 3.81 270)\n\t\t\t\t\t(length 1.27)'
        self.assertEqual(files['lint.kicad_sch'].count(pin), 1)
        files['lint.kicad_sch'] = files['lint.kicad_sch'].replace(pin, pin + b'\n\t\t\t\t\t(hide yes)')
        hidden = self.lint_result('wire-cross', files=files)
        self.assertEqual((hidden['outcome'], self.findings(hidden)), ('pass', []), hidden)

    def test_divider_regulator_and_led_placement(self):
        divider = self.findings(self.lint_result('divider-inverted'))
        self.assertEqual(len(divider), 1, divider)
        self.assertEqual((divider[0]['rule'], [i['reference'] for i in divider[0]['items']]),
                         ('divider-order/1', ['R1', 'R2']))
        caps = self.findings(self.lint_result('caps-swapped'))
        self.assertEqual([(f['rule'], f['items'][0]['reference'], f['items'][1]['reference']) for f in caps],
                         [('regulator-capacitors/1', 'C1', 'U1'), ('regulator-capacitors/1', 'C2', 'U1')])
        leds = self.findings(self.lint_result('led-order'))
        self.assertEqual([(f['rule'], [i['reference'] for i in f['items']]) for f in leds],
                         [('led-chain-order/1', ['D1', 'D2', 'D3'])])

    def test_rail_label_grid_pair_and_title_findings(self):
        rail = self.findings(self.lint_result('rail-label'))
        self.assertEqual([(f['rule'], f['sheet'], f['items'][0]['text']) for f in rail],
                         [('rail-label/1', '/power/', 'VCC_3V3')])
        grid = self.findings(self.lint_result('off-grid-sheet'))
        self.assertEqual([(f['rule'], f['items'][0]['uuid']) for f in grid],
                         [('on-grid/1', generator.uid('sheet/power'))])
        pair = self.findings(self.lint_result('unpaired'))
        self.assertEqual([(f['rule'], f['message']) for f in pair],
                         [('diff-pair-naming/1', 'USB_DP has no USB_DN partner')])
        title = self.findings(self.lint_result('no-revision'))
        self.assertEqual([(f['rule'], f['items'][0]['field']) for f in title], [('title-block/1', 'rev')])

    def test_profile_can_raise_a_rule_to_defect(self):
        params = dict(PARAMS, rules=dict(RULES, **{'divider-order/1': 'defect'}))
        result = self.lint_result('divider-inverted', params)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertEqual(self.lint_result('control', params)['outcome'], 'pass')

    def test_unresolved_symbol_never_passes(self):
        # KiCad exports a netlist without D1-D3; the producer refuses it, so the
        # lint never evaluates (its own D1-D3 gaps: OfflineRuleTests).
        params = dict(PARAMS, rules=dict(RULES, **{'led-chain-order/1': 'defect'}))
        run, results = self.run_variant('unresolved', params)
        netlist, result = results['netlist'], results['lint']
        self.assertEqual((netlist['execution'], netlist['outcome']), ('failed', 'unknown'), netlist)
        self.assertEqual(netlist['details']['missing_refs'], ['D1', 'D2', 'D3'])
        self.assertEqual([(g['references'], g['lib_id'], g['problem']) for g in netlist['details']['hierarchy_gaps']],
                         [([ref], 'Lint:LED', 'unexplained_omission') for ref in ('D1', 'D2', 'D3')])
        self.assertFalse((run / 'work/netlist/connectivity.json').exists())
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'), result)
        self.assertIn('netlist prerequisite unverified', result['summary'])
        self.assertEqual((result['evidence'], result['details']), ([], {}))
        self.assertFalse((run / 'work/lint/lint.json').exists())

    def test_refused_native_inventory_is_missing_coverage_not_pass(self):
        # A pinless H1 passes the netlist producer's identity audit, but the
        # lint's strict inventory refuses its <pins>-less libpart.
        params = dict(PARAMS, rules=dict(RULES, **{'led-chain-order/1': 'defect'}))
        run, results = self.run_variant('pinless', params)
        netlist, result = results['netlist'], results['lint']
        self.assertEqual((netlist['execution'], netlist['outcome']), ('ok', 'info'), netlist)
        self.assertIn('H1', netlist['details']['refs'])
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'unknown'), result)
        self.assertEqual(result['details']['native_inventory'], 'refused')
        self.assertEqual(result['details']['findings'], [])
        self.assertEqual(result['details']['coverage'], [dict(
            sheet='/', sheet_path='/', file='lint.kicad_sch', rules=sorted(NATIVE_RULES),
            reason='native netlist inventory unusable: expected one native pins')])
        self.assertEqual(read_json(run / 'work/lint/lint.json')['native_inventory'], 'refused')
        self.assertIn('work/lint/lint.json', result['evidence'])

    def test_unknown_rule_never_passes(self):
        unknown = self.lint_result('control', dict(PARAMS, rules={'no-such-rule/1': 'warning'}))
        self.assertEqual((unknown['execution'], unknown['outcome']), ('unsupported', 'unknown'), unknown)

    def test_missing_or_later_netlist_prerequisite_is_not_a_clean_lint(self):
        lint_spec = {'id': 'lint', 'kind': 'schematic_lint', 'params': PARAMS}
        _, results = self.run_variant(checks=[lint_spec, {'id': 'netlist', 'kind': 'netlist'}])
        self.assertEqual(results['lint']['execution'], 'unsupported', results['lint'])
        _, results = self.run_variant(checks=[{'id': 'netlist', 'kind': 'erc'}, lint_spec])
        self.assertEqual(results['lint']['execution'], 'unsupported', results['lint'])

    def test_native_erc_pin_positions_match_hand_checked_transforms(self):
        project = self.root / 'transforms'
        work = self.root / 'transforms-work'
        project.mkdir()
        (work / 'kicad-config' / '10.0').mkdir(parents=True)
        for table in ('sym-lib-table', 'fp-lib-table'):
            (work / 'kicad-config' / '10.0' / table).write_text(f'({table.replace("-", "_")}\n  (version 7)\n)\n')
        for name, data in generator.transforms().items():
            (project / name).write_bytes(data)
        record = execute(project, work, ['kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all',
                                         '--units', 'mm', '-o', '/work/erc.json', '/candidate/transforms.kicad_sch'],
                         timeout=300)
        self.assertEqual(record['execution'], 'ok', (work / 'console.log').read_text())
        native = {}
        for sheet in json.loads((work / 'erc.json').read_text())['sheets']:
            for violation in sheet['violations']:
                if violation['type'] == 'pin_not_connected':
                    item = violation['items'][0]
                    ref, pin = item['description'].split()[1], item['description'].split()[3]
                    native[ref, pin] = (nm(item['pos']['x']), nm(item['pos']['y']))
        expected = {}
        for index, (_, _, _, pin1, pin2) in enumerate(generator.TRANSFORMS):
            ox = generator.TRANSFORM_ORIGIN[0] + generator.TRANSFORM_STEP * index
            oy = generator.TRANSFORM_ORIGIN[1]
            for number, (dx, dy) in (('1', pin1), ('2', pin2)):
                expected[f'U{index + 1}', number] = (nm(ox + dx), nm(oy + dy))
        self.assertEqual(native, expected)


if __name__ == '__main__':
    unittest.main()

"""Role-keyed SPICE templates: exact value parsing, expansion and native regulator runs."""
import copy
import json
import math
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee.active_spice import capability, validate_params
from agent_ee.baseline import compile_baseline, freeze_evidence, verify_evidence
from agent_ee.candidate import capture
from agent_ee.checks import run_checks
from agent_ee.spice_templates import Unsupported, expand, parse_quantity, validate_library
from agent_ee.storage import digest, read_json, write_json
from toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures' / 'active-spice' / 'ldo'
LIBRARY = ROOT / 'src' / 'agent_ee' / 'data' / 'spice-templates.json'
NAMES = ('ldo.kicad_sch', 'ldo.lib', 'requirements.md', 'architecture.json', 'targets.json', 'interface.json')


class QuantityTests(unittest.TestCase):
    def test_exact_unit_bearing_values(self):
        cases = [('3.3 V', 'V', 3.3), ('3.3V', 'V', 3.3), ('10 mA', 'A', 0.01), ('100 ns', 's', 1e-7),
                 ('1 kHz', 'Hz', 1000), ('2.5 MHz', 'Hz', 2.5e6), ('-4.5e-1 V', 'V', -0.45),
                 ('2 V/V', 'V/V', 2), ('.5 uA', 'A', 5e-7)]
        for text, unit, value in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_quantity(text, unit), value)
        self.assertAlmostEqual(parse_quantity('-60 dB', 'V/V'), 1e-3)

    def test_uninterpretable_values_are_refused_never_substituted(self):
        for text, unit in [('4k7', 'V'), ('10m', 'V'), ('10m', 'A'), ('2.2uF 25V', 'V'), ('2.2uF', 'V'),
                           ('3.3 v', 'V'), ('3.3  V', 'V'), ('5 dB', 'V'), ('10 mV/V', 'V/V'),
                           ('1 mdB', 'V/V'), ('1e400 V', 'V'), ('1e-400 V', 'V'), ('1e400 dB', 'V/V'), ('', 'V'), ('3.3', 'V'), ('5 V', 'A'),
                           ('1 MEG', 'Hz'), ('1 Meg Hz', 'Hz')]:
            with self.subTest(text=text, unit=unit), self.assertRaises(Unsupported):
                parse_quantity(text, unit)
        for value in (3.3, None, True):
            with self.subTest(value=value), self.assertRaises(Unsupported):
                parse_quantity(value, 'V')


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.library = json.loads(LIBRARY.read_text())

    def test_shipped_library_is_versioned_and_covers_roles(self):
        validate_library(self.library)
        self.assertEqual(sorted(self.library['templates']), ['amplifier', 'filter', 'ldo'])
        kinds = [a['kind'] for a in self.library['templates']['ldo']['analyses']]
        self.assertEqual(kinds, ['dc', 'transient', 'ac'])

    def test_literal_limits_nets_and_misplaced_references_are_refused(self):
        def ldo(library):
            return library['templates']['ldo']
        mutations = [
            lambda t: ldo(t)['analyses'][0]['measurements'][0].update(min=3.2),
            lambda t: ldo(t)['analyses'][0]['measurements'][0].update(positive_net='/VOUT'),
            lambda t: ldo(t)['analyses'][0]['measurements'][0].update(min={'condition': 'vin_min', 'unit': 'V'}),
            lambda t: ldo(t)['sources'][0].update(dc_v={'target': 'vout_min', 'unit': 'V'}),
            lambda t: ldo(t)['sources'][0].update(positive_net={'terminal': 'undeclared'}),
            lambda t: ldo(t)['sources'][0].update(dc_v={'condition': 'vin_nominal', 'unit': 'ohm'}),
            lambda t: ldo(t)['analyses'][0]['measurements'][2]['min'].update(negate=False),
            lambda t: ldo(t)['analyses'][0]['measurements'][0].pop('min') and
                      ldo(t)['analyses'][0]['measurements'][0].pop('max'),
            lambda t: ldo(t)['analyses'][1]['measurements'][0].update(name='vout_at_vin_min'),
            lambda t: ldo(t)['analyses'][1].update(id='line'),
            lambda t: ldo(t).update(sources={}),
            lambda t: ldo(t).update(current_sources='load'),
            lambda t: t.update(version=''),
            lambda t: t.update(schema='agent-ee.spice-templates/2'),
        ]
        for mutate in mutations:
            library = copy.deepcopy(self.library)
            mutate(library)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                validate_library(library)


def staged(root, edit=None):
    """Copy the owned fixture and shipped library into a capture-shaped tree."""
    design = root / 'design'
    design.mkdir(parents=True)
    for name in NAMES:
        shutil.copy(FIXTURE / name, design / name)
    shutil.copy(LIBRARY, design / 'spice-templates.json')
    if edit:
        edit(design)
    files = {f'design/{p.name}': digest(p) for p in design.iterdir()}
    return files


def edit_json(name, change):
    def edit(design):
        document = json.loads((design / name).read_text())
        change(document)
        (design / name).write_text(json.dumps(document))
    return edit


class ExpansionTests(unittest.TestCase):
    def setUp(self):
        self.scope = read_json(FIXTURE / 'control.scope.json')
        self.params = self.scope['checks'][2]['params']
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-spice-templates-')
        self.addCleanup(self.tmp.cleanup)

    def expand(self, edit=None, scope=None):
        root = Path(tempfile.mkdtemp(dir=self.tmp.name))
        files = staged(root, edit)
        return expand(copy.deepcopy(self.params), scope or self.scope, files, root)

    def test_ldo_role_emits_dc_load_step_and_ac_with_target_limits(self):
        params, report = self.expand()
        validate_params(params)
        self.assertEqual([(a['id'], a['kind']) for a in params['analyses']],
                         [('line', 'dc'), ('load', 'transient'), ('ripple', 'ac')])
        line, load, ripple = params['analyses']
        self.assertEqual((line['source'], line['start_v'], line['stop_v']), ('vin', 4.5, 6.0))
        bounds = {m['name']: (m.get('min'), m.get('max')) for a in params['analyses'] for m in a['measurements']}
        self.assertEqual(bounds['vout_at_vin_min'], (3.234, 3.366))
        self.assertEqual(bounds['line_regulation'], (-0.005, 0.005))
        self.assertEqual(bounds['load_regulation'], (-0.01, 0.01))
        self.assertEqual(bounds['load_step_minimum'], (3.2, None))
        self.assertAlmostEqual(bounds['ripple_transfer'][1], 10 ** (-50 / 20))
        self.assertEqual(ripple['measurements'][0]['at_hz'], 100)
        self.assertEqual(params['current_sources'][0]['step'],
                         {'initial_a': 0.01, 'final_a': 0.1, 'delay_s': 1e-4, 'rise_s': 1e-6})
        self.assertEqual(params['sources'][0]['positive_net'], '/VIN')
        self.assertEqual(report['terminals']['vout']['net'], '/VOUT')
        self.assertEqual(report['library']['version'], '2026-10-18.1')
        self.assertTrue(all(m['execution'] == 'supported' for m in report['measurements']))
        self.assertEqual(report['immutable_inputs'], sorted(f'design/{n}' for n in (
            'architecture.json', 'interface.json', 'spice-templates.json', 'targets.json')))

    def test_uninterpretable_target_makes_only_its_measurements_unsupported(self):
        cases = {'4k7': ('vout_max', {'vout_at_vin_min', 'vout_at_vin_max'}),
                 '10m': ('line_regulation_max', {'line_regulation'}),
                 '2.2uF 25V': ('ripple_frequency', {'ripple_transfer'})}
        for text, (target, affected) in cases.items():
            with self.subTest(text=text):
                params, report = self.expand(edit_json(
                    'targets.json', lambda d: d['blocks']['ldo1'].update({target: text})))
                status = {m['name']: m for m in report['measurements']}
                self.assertEqual({n for n, m in status.items() if m['execution'] == 'unsupported'}, affected)
                for name in affected:
                    self.assertIn(repr(text), status[name]['reason'])
                emitted = {m['name'] for a in params['analyses'] for m in a['measurements']}
                self.assertEqual(emitted, set(status) - affected)

    def test_missing_target_is_unsupported_not_defaulted(self):
        params, report = self.expand(edit_json('targets.json', lambda d: d['blocks']['ldo1'].pop('vout_transient_min')))
        status = {m['name']: m for m in report['measurements']}
        self.assertEqual(status['load_step_minimum']['execution'], 'unsupported')
        self.assertIn('not declared', status['load_step_minimum']['reason'])
        self.assertNotIn('load_step_minimum', [m['name'] for a in params['analyses'] for m in a['measurements']])

    def test_uninterpretable_stimulus_makes_every_dependent_measurement_unsupported(self):
        params, report = self.expand(edit_json(
            'architecture.json', lambda d: d['blocks']['ldo1']['conditions'].update(load_light='10m')))
        self.assertIsNone(params)
        self.assertTrue(all(m['execution'] == 'unsupported' and "'10m'" in m['reason'] for m in report['measurements']))
        params, report = self.expand(edit_json(
            'architecture.json', lambda d: d['blocks']['ldo1']['conditions'].update(load_step_stop='4k7')))
        status = {m['name']: m['execution'] for m in report['measurements']}
        self.assertEqual({n for n, s in status.items() if s == 'unsupported'}, {'load_regulation', 'load_step_minimum'})
        self.assertEqual([a['id'] for a in params['analyses']], ['line', 'ripple'])

    def test_target_outside_template_sweep_is_unsupported(self):
        params, report = self.expand(edit_json(
            'targets.json', lambda d: d['blocks']['ldo1'].update(ripple_frequency='100 MHz')))
        status = {m['name']: m for m in report['measurements']}
        self.assertEqual(status['ripple_transfer']['execution'], 'unsupported')
        self.assertIn('outside analysis interval', status['ripple_transfer']['reason'])

    def test_role_without_template_and_unbound_terminal(self):
        params, report = self.expand(edit_json('architecture.json', lambda d: d['blocks']['ldo1'].update(role='buck')))
        self.assertIsNone(params)
        self.assertFalse(report['template'])
        self.assertIn("no template for role 'buck'", report['summary'])
        def unbind(contract):
            contract['invariants'] = [i for i in contract['invariants'] if i['terminal'] != 'vin']
        params, report = self.expand(edit_json('interface.json', unbind))
        self.assertIsNone(params)
        self.assertIn('named_net', report['terminals']['vin']['unresolved'])
        self.assertTrue(all('terminal vin' in m['reason'] for m in report['measurements']))

    def test_baseline_bindings_and_captured_bytes_are_required(self):
        scope = copy.deepcopy(self.scope)
        scope['baseline']['reference_architectures'] = ['design/requirements.md']
        with self.assertRaisesRegex(ValueError, 'reference architecture'):
            self.expand(scope=scope)
        scope = copy.deepcopy(self.scope)
        scope['baseline']['targets'] = ['design/requirements.md']
        with self.assertRaisesRegex(ValueError, 'baseline target'):
            self.expand(scope=scope)
        with self.assertRaisesRegex(ValueError, 'scope/2'):
            self.expand(scope={'schema': 'agent-ee.scope/1'})
        root = Path(tempfile.mkdtemp(dir=self.tmp.name))
        files = staged(root)
        (root / 'design' / 'targets.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'changed'):
            expand(copy.deepcopy(self.params), self.scope, files, root)
        params = copy.deepcopy(self.params)
        params['template']['block'] = 'ldo9'
        with self.assertRaisesRegex(ValueError, 'block ldo9'):
            root = Path(tempfile.mkdtemp(dir=self.tmp.name))
            expand(params, self.scope, staged(root), root)

    def test_capability_counts_only_supported_bounded_measurements(self):
        root = Path(tempfile.mkdtemp(dir=self.tmp.name))
        files = staged(root, edit_json('targets.json', lambda d: d['blocks']['ldo1'].update(vout_max='4k7')))
        descriptor = capability(self.params, files, root, self.scope)
        self.assertEqual(descriptor['bounded_metrics'],
                         ['line_regulation', 'load_regulation', 'load_step_minimum', 'ripple_transfer'])
        self.assertIn('design/spice-templates.json', descriptor['immutable_inputs'])
        self.assertIn('design/ldo.lib', descriptor['immutable_inputs'])
        with self.assertRaisesRegex(ValueError, 'missing, linked or changed'):
            capability(self.params, files, None, self.scope)

    def test_scope3_baseline_freezes_template_inputs(self):
        root = Path(tempfile.mkdtemp(dir=self.tmp.name))
        files = staged(root)
        files['design/ldo.kicad_sch'] = digest(root / 'design' / 'ldo.kicad_sch')
        scope = copy.deepcopy(self.scope)
        scope['schema'] = 'agent-ee.scope/3'
        for requirement in scope['baseline']['requirements']:
            requirement['intent'] = 'acceptance'
        baseline = compile_baseline(scope, files, root)
        descriptor = baseline['coverage']['checks']['regulator']
        self.assertEqual(descriptor['predicates'], ['active-spice:' + n for n in (
            'vout_at_vin_min', 'vout_at_vin_max', 'line_regulation', 'load_regulation',
            'load_step_minimum', 'ripple_transfer')])
        for name in ('spice-templates.json', 'interface.json', 'architecture.json', 'targets.json', 'ldo.lib'):
            self.assertIn(f'design/{name}', descriptor['immutable_inputs'])


class NativeTemplateTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-spice-templates-native-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        shutil.copytree(FIXTURE, self.source)
        self.scope = read_json(FIXTURE / 'control.scope.json')
        self.scope['inputs'][0]['path'] = str(self.source)
        self.scope['inputs'][1]['path'] = str(LIBRARY)

    def edit(self, name, change):
        document = read_json(self.source / name)
        change(document)
        write_json(self.source / name, document)

    def run_case(self):
        write_json(self.root / 'scope.json', self.scope)
        run = self.root / 'run'
        run.mkdir()
        capture(self.root / 'scope.json', run)
        results = run_checks(run, time.monotonic() + 300)
        self.assertEqual(results[0]['execution'], 'ok', results[0])
        freeze_evidence(run, results)
        self.results = results
        return run, results[-1]

    def test_ldo_template_runs_dc_load_step_and_ac_against_analytic_model(self):
        run, result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        metrics = {k: v['value'] for k, v in result['details']['metrics'].items()}
        # Owned model: 3.3 V + 1 mV/V about 5 V, 50 mohm output resistance.
        self.assertAlmostEqual(metrics['vout_at_vin_min'], 3.3 - 0.0005 - 0.01 * 0.05, delta=1e-9)
        self.assertAlmostEqual(metrics['vout_at_vin_max'], 3.3 + 0.001 - 0.01 * 0.05, delta=1e-9)
        self.assertAlmostEqual(metrics['line_regulation'], 0.0015, delta=1e-9)
        self.assertAlmostEqual(metrics['load_regulation'], -0.09 * 0.05, delta=1e-7)
        self.assertAlmostEqual(metrics['load_step_minimum'], 3.3 - 0.1 * 0.05, delta=1e-6)
        self.assertAlmostEqual(metrics['ripple_transfer'], 0.001, delta=1e-8)
        self.assertEqual(result['details']['margins']['ripple_transfer']['max'] > 0, True)
        for name in ('template-expansion.json', 'line/deck.cir', 'line/result.raw', 'load/result.raw',
                     'ripple/result.raw', 'measurements.json'):
            self.assertIn('work/regulator/' + name, result['evidence'])
        deck = (run / 'work' / 'regulator' / 'line' / 'deck.cir').read_text()
        self.assertIn('.dc V0 4.5 6 0.014999999999999999', deck)
        self.assertIn('I0 ', deck)
        self.assertIn('PWL(0 0.01 0.0001 0.01 0.000101 0.10000000000000001)', deck)
        verify_evidence(run, self.results, required=True)

    def test_uninterpretable_target_is_unsupported_and_others_still_measured(self):
        self.edit('targets.json', lambda d: d['blocks']['ldo1'].update(vout_max='4k7'))
        run, result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        records = {m['name']: m['execution'] for m in result['details']['template']['measurements']}
        self.assertEqual(records['vout_at_vin_min'], 'unsupported')
        self.assertEqual(records['vout_at_vin_max'], 'unsupported')
        self.assertNotIn('vout_at_vin_min', result['details']['metrics'])
        self.assertIn('line_regulation', result['details']['metrics'])
        self.assertIn('template measurements unsupported: vout_at_vin_min, vout_at_vin_max', result['summary'])

    def test_complete_bound_violation_stays_a_defect_beside_unsupported(self):
        self.edit('targets.json', lambda d: d['blocks']['ldo1'].update(vout_max='4k7', load_regulation_max='1 mV'))
        run, result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertIn('load_regulation', result['summary'])
        self.assertIn('unsupported: vout_at_vin_min', result['summary'])

    def test_role_without_template_is_unsupported(self):
        self.edit('architecture.json', lambda d: d['blocks']['ldo1'].update(role='buck'))
        run, result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertIn("no template for role 'buck'", result['summary'])
        self.assertIn('work/regulator/template-expansion.json', result['evidence'])

    def test_missing_subcircuit_model_is_missing_coverage_not_pass(self):
        self.scope['checks'][2]['params']['active_parts']['U1']['subcircuit'] = 'absent'
        run, result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertIn('named subcircuit absent', result['summary'])
        self.assertNotIn('metrics', result['details'])

    def test_uninterpretable_native_passive_value_is_never_substituted(self):
        for value in ('4k7', '2.2uF 25V'):
            with self.subTest(value=value):
                sheet = self.source / 'ldo.kicad_sch'
                original = sheet.read_text()
                sheet.write_text(original.replace('(property "Value" "10u"', f'(property "Value" "{value}"'))
                self.addCleanup(sheet.write_text, original)
                shutil.rmtree(self.root / 'run', ignore_errors=True)
                run, result = self.run_case()
                sheet.write_text(original)
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
                self.assertIn('C2: unsupported passive value', result['summary'])

    def test_interface_binding_contradicting_native_pins_is_unsupported(self):
        def swap(contract):
            for invariant in contract['invariants']:
                invariant['names'] = {'/VIN': ['/VOUT'], '/VOUT': ['/VIN']}.get(invariant['names'][0], invariant['names'])
        self.edit('interface.json', swap)
        run, result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertIn('native pin is not on', result['summary'])


if __name__ == '__main__':
    unittest.main()

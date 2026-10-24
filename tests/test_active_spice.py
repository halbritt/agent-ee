"""Captured-model validation and real native active-device simulation."""
import copy
import math
import shutil
import tempfile
import time
import unittest
from array import array
from pathlib import Path

from agent_ee.active_spice import (bind_design, capability, capture_models, extract_metrics,
                                   read_trace, validate_params)
from agent_ee.baseline import compile_baseline, freeze_evidence, validate_witnesses, verify_evidence
from agent_ee.candidate import capture, verify_snapshot
from agent_ee.checks import Ctx, check_active_spice, CheckError, run_checks
from agent_ee.storage import digest, read_json, write_json
from toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'active-spice'


class ModelTests(unittest.TestCase):
    def test_captured_include_closure_and_explicit_formals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'root.lib').write_text('.include "core.lib"\n')
            (root / 'core.lib').write_text('.subckt buffer PLUS MINUS OUT\nE1 OUT 0 PLUS MINUS 1e6\n.ends buffer\n')
            files = {p.name: digest(p) for p in root.iterdir()}
            models = capture_models(root, files, ['root.lib', 'core.lib'])
            self.assertEqual(models['subcircuits']['buffer']['formals'], ['PLUS', 'MINUS', 'OUT'])
            self.assertEqual(models['files'], files)
            self.assertEqual(models['staged']['core.lib'], (root / 'core.lib').read_text())

    def test_unsafe_model_data_is_refused_before_solver(self):
        bad = ['.control\nquit\n.endc', '.end', '.tran 1n 1m',
               '.include /etc/passwd', '.include ../escape.lib',
               '.include "missing.lib"', '.include "root.lib"',
               '.subckt x a b\n+ .control\n.ends x',
               '.subckt x a b\nB1 a b v=read(1)\n.ends x',
               '.subckt x a b\nV1 a b PWL FILE=data\n.ends x',
               '.model evil d file=x', '.model code arbitrary(foo=1)',
               '.subckt x a a\n.ends x', '.subckt x a b\n.ends x\n.subckt X a b\n.ends X']
        for text in bad:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'root.lib'
                path.write_text(text + '\n')
                with self.assertRaises(ValueError):
                    capture_models(tmp, {'root.lib': digest(path)}, ['root.lib'])

    def test_comment_encoding_does_not_admit_non_ascii_statements_or_control_code(self):
        active = b'.subckt x a b\nR1 a b 1k\n.ends x\n'
        for raw in (active.replace(b'1k', b'1k\xa0'), b'\xa0* comment\n' + active,
                    b'* punctuation \x96\n' + active + b'.control\nquit\n.endc\n'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'root.lib'
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    capture_models(tmp, {'root.lib': digest(path)}, ['root.lib'])
        # A legacy NEL byte inside a comment must not become a logical newline.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'root.lib'
            path.write_bytes(b'\t* comment\x85.control\n' + active)
            models = capture_models(tmp, {'root.lib': digest(path)}, ['root.lib'])
            self.assertEqual(models['subcircuits']['x']['line'], 2)
            self.assertEqual(models['staged']['root.lib'].split('\n', 1)[1], active.decode('ascii'))

    def test_bare_subcircuit_end_is_accepted_without_admitting_directives(self):
        for end in ('.ends', '.ENDS', '  .Ends  '):
            with self.subTest(end=end), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'root.lib'
                text = '.subckt buf a b\nR1 a b 1k\n' + end + '\n'
                path.write_text(text)
                models = capture_models(tmp, {'root.lib': digest(path)}, ['root.lib'])
                self.assertEqual(models['subcircuits']['buf']['formals'], ['a', 'b'])
                self.assertEqual(models['staged']['root.lib'], text)
        for text in ('.subckt buf a b\nR1 a b 1k\n.ends .control\n',
                     '.subckt buf a b\nR1 a b 1k\n.ends\n+ .control\n',
                     '.subckt buf a b\nR1 a b 1k\n.ends buf .endc\n',
                     '.subckt buf a b\nR1 a b 1k\n.ends\n.ends\n',
                     '.ends\n', '.subckt buf a b\nR1 a b 1k\n.param\n.ends\n',
                     '.subckt buf a b\nR1 a b 1k\n.ends\n.control\n', '.subckt buf a b\n.endc\n.ends\n'):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'root.lib'
                path.write_text(text)
                with self.assertRaises(ValueError):
                    capture_models(tmp, {'root.lib': digest(path)}, ['root.lib'])

    def test_ambiguous_model_definitions_and_silent_diode_levels_are_refused(self):
        for statements in ('.model dm D (level=99)', '.model dm D (level={1+98})',
                           '.model rm R (rsh=100)\n.model RM R (rsh=200)'):
            with self.subTest(statements=statements), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'root.lib'
                path.write_text('.subckt x a b\n' + statements + '\nR1 a b 1k\n.ends x\n')
                with self.assertRaises(ValueError):
                    capture_models(tmp, {'root.lib': digest(path)}, ['root.lib'])

    def test_repeated_include_expansion_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'core.lib').write_text('.subckt x a b\nR1 a b 1k\n.ends x\n')
            (root / 'root.lib').write_text('.include core.lib\n.include core.lib\n')
            files = {p.name: digest(p) for p in root.iterdir()}
            with self.assertRaisesRegex(ValueError, 'repeated model include'):
                capture_models(root, files, list(files))

    def test_changed_and_linked_models_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'core.lib'
            path.write_text('.subckt x a b\nR1 a b 1k\n.ends x\n')
            files = {'core.lib': digest(path)}
            path.write_text(path.read_text() + '* changed\n')
            with self.assertRaisesRegex(ValueError, 'changed'):
                capture_models(root, files, ['core.lib'])
            (root / 'alias.lib').symlink_to(path)
            with self.assertRaisesRegex(ValueError, 'linked'):
                capture_models(root, {'alias.lib': digest(path)}, ['alias.lib'])


class DeclarationTests(unittest.TestCase):
    def setUp(self):
        self.params = read_json(FIXTURE / 'control.scope.json')['checks'][1]['params']

    def test_scope3_capability_requires_bounds_prior_netlist_and_models(self):
        scope = read_json(FIXTURE / 'control.scope.json')
        scope['schema'] = 'agent-ee.scope/3'
        for requirement in scope['baseline']['requirements']:
            requirement['intent'] = 'acceptance'
        files = {f'design/{name}': 'a' * 64 for name in
                 ('requirements.md', 'owned.lib', 'active.kicad_sch', 'active.kicad_pcb')}
        baseline = compile_baseline(scope, files)
        descriptor = baseline['coverage']['checks']['active']
        self.assertEqual(descriptor['immutable_inputs'], ['design/owned.lib'])
        self.assertEqual(descriptor['predicates'],
                         ['active-spice:out_1ms', 'active-spice:gain_100hz'])
        self.assertEqual(descriptor['prerequisites'], [{'check_id': 'netlist', 'kind': 'netlist'}])
        with self.assertRaisesRegex(ValueError, 'not captured'):
            compile_baseline(scope, {k: v for k, v in files.items() if k != 'design/owned.lib'})
        scope['checks'].reverse()
        with self.assertRaisesRegex(ValueError, 'forward'):
            compile_baseline(scope, files)
        scope['checks'].reverse()
        for analysis in scope['checks'][1]['params']['analyses']:
            for metric in analysis['measurements']:
                metric.pop('min', None)
                metric.pop('max', None)
                metric['informational'] = True
        with self.assertRaisesRegex(ValueError, 'no executable bound coverage'):
            compile_baseline(scope, files)

    def test_nonfinite_ranges_units_and_missing_bounds_refused(self):
        mutations = [lambda p: p.update(sources=[None]),
                     lambda p: p['sources'][0].update(unrecognized_waveform='custom'),
                     lambda p: p.update(temperature_c=float('nan')),
                     lambda p: p.update(temperature_c=10**400),
                     lambda p: p['sources'][0]['step'].update(delay_s=1e308, rise_s=1e308),
                     lambda p: p['sources'][0].update(dc_v=True),
                     lambda p: p['analyses'][0].update(step_s=0),
                     lambda p: p['analyses'][0].update(id='version'),
                     lambda p: p['analyses'][1].update(id='models'),
                     lambda p: p['analyses'][0]['measurements'][0].update(at_s=0.1),
                     lambda p: p['analyses'][1].update(points_per_decade=1.5),
                     lambda p: p['analyses'][1]['measurements'][0].update(unit='V'),
                     lambda p: p['analyses'][1]['measurements'][0].update(min=1, max=0),
                     lambda p: p['analyses'][1]['measurements'][0].update(max=float('inf'))]
        for mutate in mutations:
            p = copy.deepcopy(self.params)
            mutate(p)
            with self.subTest(params=p), self.assertRaises(ValueError):
                validate_params(p)
        p = copy.deepcopy(self.params)
        del p['analyses'][0]['measurements'][0]['min']
        del p['analyses'][0]['measurements'][0]['max']
        with self.assertRaisesRegex(ValueError, 'informational'):
            validate_params(p)
        p['analyses'][0]['measurements'][0]['informational'] = True
        validate_params(p)

    def test_all_declared_model_files_enter_baseline(self):
        scope = read_json(FIXTURE / 'control.scope.json')
        params = scope['checks'][1]['params']
        params['model_files'].append('design/nested.lib')
        files = {'design/requirements.md': 'a', 'design/owned.lib': 'b', 'design/nested.lib': 'c'}
        baseline = compile_baseline(scope, files)
        self.assertEqual(baseline['files'], files)
        self.assertEqual(capability(params, files)['bounded_metrics'], ['out_1ms', 'gain_100hz'])
        changed = compile_baseline(scope, {**files, 'design/nested.lib': 'changed'})
        self.assertNotEqual(baseline['baseline_id'], changed['baseline_id'])
        from agent_ee.campaign import mutable_inputs
        scope['mutable_inputs'] = ['design/nested.lib']
        with self.assertRaisesRegex(ValueError, 'immutable baseline'):
            mutable_inputs({'scope': scope, 'files': files}, baseline)
        with self.assertRaisesRegex(ValueError, 'not captured'):
            compile_baseline(scope, {'design/requirements.md': 'a'})

    def test_pin_mapping_checks_complete_inventory_not_only_connected_subset(self):
        conn = {'components': {'U1': {'value': 'owned', 'libsource': {'lib': 'test', 'part': 'amp'}}},
                'nets': {'gnd': ['U1.1'], 'out': ['U1.2']}}
        params = {'ground_net': 'gnd', 'active_parts': {'U1': {'model_file': 'x.lib',
                    'subcircuit': 'test', 'pin_map': {'1': 'PLUS', '2': 'OUT'}}}}
        model = {'subcircuits': {'test': {'path': 'x.lib', 'name': 'test', 'formals': ['OUT', 'PLUS']}},
                 'files': {'x.lib': 'sha'}}
        lines, binding = bind_design(conn, {'U1': ['1', '2']}, params, model)
        self.assertEqual(lines, ['X0 n2 0 test'])  # formal order, not native pin order
        self.assertEqual(binding['components']['U1']['mapping'][0]['pin'], '2')
        for inventory in ({'U1': ['1', '2', '3']}, {'U1': None}):
            with self.assertRaisesRegex(ValueError, 'inventory'):
                bind_design(conn, inventory, params, model)
        missing = copy.deepcopy(params)
        missing['active_parts']['U1']['subcircuit'] = 'absent'
        with self.assertRaisesRegex(ValueError, 'subcircuit absent'):
            bind_design(conn, {'U1': ['1', '2']}, missing, model)
        passive_conn = copy.deepcopy(conn)
        passive_conn['components']['R1'] = {'value': '1e9999', 'libsource': {'lib': 'Device', 'part': 'R'}}
        passive_conn['nets']['gnd'].append('R1.1')
        passive_conn['nets']['out'].append('R1.2')
        with self.assertRaisesRegex(ValueError, 'finite range'):
            bind_design(passive_conn, {'U1': ['1', '2'], 'R1': ['1', '2']}, params, model)
        params['active_parts']['U1']['pin_map']['1'] = 'NONEXISTENT'
        with self.assertRaisesRegex(ValueError, 'existing formal'):
            bind_design(conn, {'U1': ['1', '2']}, params, model)
        conn['nets']['out'].append('U1.1')
        with self.assertRaisesRegex(ValueError, 'duplicate native pin'):
            bind_design(conn, {'U1': ['1', '2']}, params, model)


class TraceTests(unittest.TestCase):
    def trace(self, path, *, complex_data=False):
        header = ('Title: owned test\nPlotname: ' + ('AC Analysis' if complex_data else 'Transient Analysis')
                  + '\nFlags: ' + ('complex' if complex_data else 'real')
                  + '\nNo. Variables: 3\nNo. Points: 2\nVariables:\n'
                  + ('\t0\tfrequency\tfrequency grid=3\n' if complex_data else '\t0\ttime\ttime\n')
                  + '\t1\tv(n1)\tvoltage\n\t2\tv(n2)\tvoltage\nBinary:\n')
        values = ([10, 0, 0, 2, 1, 1, 20, 0, 0, 4, 2, 2] if complex_data
                  else [0, 2, 1, 1, 4, 2])
        path.write_bytes(header.encode() + array('d', values).tobytes())

    def test_differential_complex_transfer_uses_complex_input_and_interpolates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.raw'
            self.trace(path, complex_data=True)
            analysis = {'kind': 'ac', 'measurements': [{'name': 'ratio', 'observable': 'transfer_magnitude',
                'input_positive_net': 'in', 'input_negative_net': 'gnd',
                'output_positive_net': 'out', 'output_negative_net': 'in',
                'at_hz': 15, 'unit': 'V/V', 'min': 0.7}]}
            metrics, limits, raw = extract_metrics(path, analysis, {'gnd': '0', 'in': 'n1', 'out': 'n2'}, {})
            self.assertAlmostEqual(metrics['ratio']['value'], math.sqrt(0.5))
            self.assertEqual(raw['points'], 2)
            analysis['measurements'][0]['input_negative_net'] = 'in'
            with self.assertRaisesRegex(ValueError, 'zero AC'):
                extract_metrics(path, analysis, {'gnd': '0', 'in': 'n1', 'out': 'n2'}, {})

    def test_bad_shape_nan_wrong_analysis_and_out_of_trace_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.raw'
            self.trace(path)
            original = path.read_bytes()
            for payload in (original[:-8], original.replace(b'Flags: real', b'Flags: complex'),
                            original[:-8] + array('d', [float('nan')]).tobytes(),
                            original.replace(b'0\ttime\ttime', b'0\ttime\tvoltage'),
                            original.replace(b'\t2\tv(n2)', b'\t1\tv(n2)')):
                path.write_bytes(payload)
                with self.assertRaises(ValueError):
                    read_trace(path, 'transient')
            path.write_bytes(original)
            analysis = {'kind': 'transient', 'measurements': [{'name': 'v', 'unit': 'V', 'observable': 'voltage',
                'positive_net': 'in', 'negative_net': 'gnd', 'at_s': 2}]}
            with self.assertRaisesRegex(ValueError, 'outside retained trace'):
                extract_metrics(path, analysis, {'in': 'n1', 'out': 'n2', 'gnd': '0'}, {})


def raw(path, plot, axis, rows):
    """Owned real-valued ngspice-shaped trace: axis then v(n1), v(n2)."""
    header = (f'Title: owned test\nPlotname: {plot}\nFlags: real\nNo. Variables: 3\n'
              f'No. Points: {len(rows)}\nVariables:\n\t0\t{axis[0]}\t{axis[1]}\n'
              '\t1\tv(n1)\tvoltage\n\t2\tv(n2)\tvoltage\nBinary:\n')
    path.write_bytes(header.encode() + array('d', [v for row in rows for v in row]).tobytes())


class SweepLoadAndWindowTests(unittest.TestCase):
    def setUp(self):
        self.params = read_json(FIXTURE / 'control.scope.json')['checks'][1]['params']
        self.params['current_sources'] = [{'id': 'load', 'positive_net': '/OUT', 'negative_net': '/GND',
            'dc_a': 0.001, 'step': {'initial_a': 0.001, 'final_a': 0.01, 'delay_s': 0.001, 'rise_s': 1e-6}}]
        self.params['analyses'].append({'id': 'line', 'kind': 'dc', 'source': 'positive', 'start_v': 12,
            'stop_v': 15, 'points': 31, 'measurements': [
                {'name': 'out_12v', 'observable': 'voltage', 'positive_net': '/OUT', 'negative_net': '/GND',
                 'at_v': 12, 'unit': 'V', 'max': 1},
                {'name': 'out_change', 'observable': 'voltage_change', 'positive_net': '/OUT',
                 'negative_net': '/GND', 'from_v': 12, 'to_v': 15, 'unit': 'V', 'min': -1, 'max': 1}]})
        self.params['analyses'][0]['measurements'].append(
            {'name': 'out_floor', 'observable': 'voltage_minimum', 'positive_net': '/OUT',
             'negative_net': '/GND', 'from_s': 0.001, 'to_s': 0.005, 'unit': 'V', 'min': 0})

    def test_dc_sweep_current_load_and_window_declarations(self):
        validate_params(self.params)
        nodes = {'/OUT': 'n1', '/GND': '0'}
        from agent_ee.active_spice import analysis_command, source_lines
        self.assertEqual(analysis_command(self.params, self.params['analyses'][2]), '.dc V1 12 15 0.10000000000000001')
        line = source_lines({'sources': [], 'current_sources': self.params['current_sources']}, nodes)[0]
        self.assertEqual(line, 'I0 n1 0 DC 0.001 PWL(0 0.001 0.001 0.001 0.0010009999999999999 0.01)')
        mutations = [lambda p: p['analyses'][2].update(points=1),
                     lambda p: p['analyses'][2].update(points=2.0),
                     lambda p: p['analyses'][2].update(start_v=15),
                     lambda p: p['analyses'][2].update(source='load'),
                     lambda p: p['analyses'][2].update(source='absent'),
                     lambda p: p['analyses'][2]['measurements'][0].update(at_v=16),
                     lambda p: p['analyses'][2]['measurements'][0].update(unit='V/V'),
                     lambda p: p['analyses'][2]['measurements'][0].update(observable='voltage_minimum'),
                     lambda p: p['analyses'][2]['measurements'][1].pop('to_v'),
                     lambda p: p['analyses'][0]['measurements'][1].update(to_s=0.001),
                     lambda p: p['analyses'][0]['measurements'][1].update(at_s=0.002),
                     lambda p: p['current_sources'][0].update(dc_a=0),
                     lambda p: p['current_sources'][0].update(id='positive'),
                     lambda p: p['current_sources'][0]['step'].update(rise_s=0),
                     lambda p: p['current_sources'][0]['step'].pop('final_a'),
                     lambda p: p['current_sources'][0].update(dc_a=float('nan')),
                     lambda p: p['current_sources'][0].update(ac_magnitude_v=1),
                     lambda p: p['analyses'][2].update(kind='op')]
        for mutate in mutations:
            p = copy.deepcopy(self.params)
            mutate(p)
            with self.subTest(params=p), self.assertRaises(ValueError):
                validate_params(p)

    def test_transient_change_and_window_extremes_use_interpolant(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.raw'
            raw(path, 'Transient Analysis', ('time', 'time'), [(0, 0, 1), (1, 2, 1), (2, 1, 1), (3, 3, 1)])
            analysis = {'kind': 'transient', 'stop_s': 3, 'measurements': [
                {'name': 'change', 'observable': 'voltage_change', 'positive_net': 'a', 'negative_net': 'g',
                 'from_s': 0.5, 'to_s': 2.5, 'unit': 'V'},
                {'name': 'low', 'observable': 'voltage_minimum', 'positive_net': 'a', 'negative_net': 'g',
                 'from_s': 0.5, 'to_s': 2.5, 'unit': 'V'},
                {'name': 'high', 'observable': 'voltage_maximum', 'positive_net': 'a', 'negative_net': 'b',
                 'from_s': 1.5, 'to_s': 3, 'unit': 'V', 'max': 2}]}
            metrics, limits, _ = extract_metrics(path, analysis, {'a': 'n1', 'b': 'n2', 'g': '0'}, {})
            self.assertEqual(metrics['change']['value'], 2 - 1)
            self.assertEqual(metrics['low']['value'], 1)
            self.assertEqual(metrics['high']['value'], 2)
            self.assertEqual(sorted(limits), ['high'])

    def test_dc_trace_must_match_declared_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.raw'
            analysis = {'kind': 'dc', 'start_v': 1, 'stop_v': 2, 'points': 3, 'measurements': [
                {'name': 'top', 'observable': 'voltage', 'positive_net': 'a', 'negative_net': 'g',
                 'at_v': 2, 'unit': 'V'},
                {'name': 'rise', 'observable': 'voltage_change', 'positive_net': 'a', 'negative_net': 'g',
                 'from_v': 1, 'to_v': 1.25, 'unit': 'V'}]}
            nodes = {'a': 'n1', 'b': 'n2', 'g': '0'}
            raw(path, 'DC transfer characteristic', ('v(v-sweep)', 'voltage'),
                [(1, 0, 0), (1.5, 1, 0), (2 - 1e-14, 3, 0)])
            metrics, _, _ = extract_metrics(path, analysis, nodes, {})
            self.assertEqual((metrics['top']['value'], metrics['rise']['value']), (3, 0.5))
            for rows in ([(1, 0, 0), (1.5, 1, 0)], [(1, 0, 0), (1.4, 1, 0), (2, 3, 0)],
                         [(1, 0, 0), (1.5, 1, 0), (2.1, 3, 0)]):
                raw(path, 'DC transfer characteristic', ('v(v-sweep)', 'voltage'), rows)
                with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, 'DC sweep samples'):
                    extract_metrics(path, analysis, nodes, {})
            raw(path, 'Transient Analysis', ('time', 'time'), [(1, 0, 0), (1.5, 1, 0), (2, 3, 0)])
            with self.assertRaisesRegex(ValueError, 'wrong raw analysis'):
                read_trace(path, 'dc')


class NativeActiveTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-active-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        shutil.copytree(FIXTURE, self.source)
        self.scope = read_json(FIXTURE / 'control.scope.json')
        self.scope['inputs'][0]['path'] = str(self.source)

    def run_case(self):
        write_json(self.root / 'scope.json', self.scope)
        run = self.root / 'run'
        run.mkdir()
        capture(self.root / 'scope.json', run)
        results = run_checks(run, time.monotonic() + 120)
        self.assertEqual(results[0]['execution'], 'ok', results[0])
        freeze_evidence(run, results)
        return run, results

    def test_scope3_control_retains_nominal_response_and_seals(self):
        self.scope['schema'] = 'agent-ee.scope/3'
        for requirement in self.scope['baseline']['requirements']:
            requirement['intent'] = 'acceptance'
        self.test_native_control_against_independent_analytic_response()

    def test_scope3_seed_preserves_witness_and_tamper_boundaries(self):
        self.scope['schema'] = 'agent-ee.scope/3'
        for requirement in self.scope['baseline']['requirements']:
            requirement['intent'] = 'acceptance'
        self.test_seed_fails_both_bounds_and_sealed_witness_rejects_artifact_tampering()

    def test_native_control_against_independent_analytic_response(self):
        run, results = self.run_case()
        result = results[-1]
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        metrics = result['details']['metrics']
        gain = 1e6 / (1 + 1e6)
        self.assertAlmostEqual(metrics['out_1ms']['value'], .1 * (1-math.exp(-1)) * gain, delta=3e-6)
        self.assertAlmostEqual(metrics['gain_100hz']['value'], gain / math.sqrt(1+(2*math.pi*.1)**2), delta=1e-6)
        for name in ('step/deck.cir', 'gain/result.raw', 'step/result.raw', 'models/design/owned.lib',
                     'version/console.log', 'measurements.json', 'binding.json', 'model-manifest.json'):
            self.assertIn('work/active/' + name, result['evidence'])
        verify_evidence(run, results, required=True)

    def test_seed_fails_both_bounds_and_sealed_witness_rejects_artifact_tampering(self):
        path = self.source / 'active.kicad_sch'
        path.write_text(path.read_text().replace('"10k"', '"100k"'))
        board = self.source / 'active.kicad_pcb'
        board.write_text(board.read_text().replace('"10k"', '"100k"'))
        run, results = self.run_case()
        result = results[-1]
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertTrue(all(bounds['min'] < 0 for bounds in result['details']['margins'].values()))
        witnesses = [{'requirement_id': 'R-response', 'check_id': 'active'}]
        evidence = [{'path': 'checks/active.json', 'locator': 'details.metrics'}]
        validate_witnesses(run, witnesses, evidence)
        for rel in ('step/deck.cir', 'gain/result.raw', 'measurements.json', 'models/design/owned.lib', 'binding.json'):
            path = run / 'work/active' / rel
            original = path.read_bytes()
            path.write_bytes(original + b'changed')
            with self.subTest(rel=rel), self.assertRaisesRegex(ValueError, 'tool evidence changed'):
                validate_witnesses(run, witnesses, evidence)
            with self.assertRaises(ValueError):
                verify_evidence(run, results, required=True)
            path.write_bytes(original)
        path = run / 'inputs/design/owned.lib'
        path.write_text(path.read_text() + '* changed model\n')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            verify_snapshot(run)

    def test_missing_model_does_not_drop_active_component(self):
        # A second unmodeled active component remains populated in the native export.
        path = self.source / 'active.kicad_sch'
        path.write_text(path.read_text().replace('"Device:R"', '"agent:Unknown"')
                        .replace('"R_1_1"', '"Unknown_1_1"'))
        run, results = self.run_case()
        self.assertEqual((results[-1]['execution'], results[-1]['outcome']), ('unsupported', 'unknown'), results[-1])
        self.assertIn('explicit model', results[-1]['summary'])
        self.assertFalse((run / 'work/active/step').exists())

    def test_nonexistent_formal_refused(self):
        self.scope['checks'][1]['params']['active_parts']['U1']['pin_map']['3'] = 'DOES_NOT_EXIST'
        run, results = self.run_case()
        self.assertEqual(results[-1]['execution'], 'unsupported', results[-1])
        self.assertIn('existing formal', results[-1]['summary'])

    def test_bare_subcircuit_end_runs_native_control(self):
        model = self.source / 'owned.lib'
        model.write_text(model.read_text().replace('.ends buffer', '.ENDS'))
        run, results = self.run_case()
        result = results[-1]
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        gain = 1e6 / (1 + 1e6)
        self.assertAlmostEqual(result['details']['metrics']['out_1ms']['value'],
                               .1 * (1-math.exp(-1)) * gain, delta=3e-6)
        self.assertEqual((run / 'work/active/models/design/owned.lib').read_text(), model.read_text())
        verify_evidence(run, results, required=True)

    def test_vendor_encoded_comments_preserve_native_response_and_original_capture(self):
        model = self.source / 'owned.lib'
        active = model.read_bytes()
        original = b'* Vendor comment with legacy punctuation \x96 and \xb0C\r\n' + active
        model.write_bytes(original)
        run, results = self.run_case()
        result = results[-1]
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        gain = 1e6 / (1 + 1e6)
        self.assertAlmostEqual(result['details']['metrics']['out_1ms']['value'],
                               .1 * (1-math.exp(-1)) * gain, delta=3e-6)
        self.assertEqual((run / 'inputs/design/owned.lib').read_bytes(), original)
        staged = (run / 'work/active/models/design/owned.lib').read_bytes()
        self.assertEqual(staged.split(b'\n', 1)[1], active)
        self.assertIn(b'\\x96', staged.split(b'\n', 1)[0])
        verify_evidence(run, results, required=True)

    def test_directive_after_bare_subcircuit_end_refused_before_solver(self):
        model = self.source / 'owned.lib'
        model.write_text(model.read_text().replace('.ends buffer', '.ends\n+ .control'))
        run, results = self.run_case()
        self.assertEqual(results[-1]['execution'], 'unsupported', results[-1])
        self.assertIn('embedded model directive', results[-1]['summary'])
        self.assertFalse((run / 'work/active/version').exists())

    def test_outside_capture_include_refused_before_solver(self):
        (self.source / 'owned.lib').write_text('.include /etc/passwd\n')
        run, results = self.run_case()
        self.assertEqual(results[-1]['execution'], 'unsupported', results[-1])
        self.assertFalse((run / 'work/active/version').exists())

    def test_ignored_model_parameter_refuses_measurements_and_retains_native_trace(self):
        (self.source / 'owned.lib').write_text('.subckt buffer INP INM VP VN OUT\n'
            'E1 OUT 0 INP INM 1e6\nRVP VP 0 rm 1e9\nRVN VN 0 1e9\n'
            '.model rm R (rsh=100 unsupported=10)\n.ends buffer\n')
        run, results = self.run_case()
        result = results[-1]
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertIn('ngspice model/device semantics', result['summary'])
        self.assertIn('unrecognized parameter (unsupported) - ignored',
                      (run / 'work/active/step/ngspice.log').read_text())
        for name in ('step/ngspice.log', 'step/result.raw', 'step/stdout.log', 'step/stderr.log'):
            self.assertIn('work/active/' + name, result['evidence'])
        self.assertNotIn('metrics', result['details'])
        self.assertFalse((run / 'work/active/measurements.json').exists())
        verify_evidence(run, results, required=True)

    def test_owned_malformed_model_and_primitive_parameter_boundaries(self):
        cases = (
            ('RVP VP 0 1e9 unsupported=10', 'failed', 'unknown parameter'),
            ('RVP VP 0 rm 1e9\n.model rm R (rsh=)', 'failed', 'Syntax error'),
            ('D1 OUT 0 dm\n.model dm D (unsupported=10)', 'unsupported', 'ignored'),
            ('D1 OUT 0 dm\n.model dm D (level=99)', 'unsupported', None),
        )
        for index, (statements, status, diagnostic) in enumerate(cases):
            with self.subTest(statements=statements):
                self.root = Path(self.tmp.name) / str(index)
                self.root.mkdir()
                (self.source / 'owned.lib').write_text('.subckt buffer INP INM VP VN OUT\n'
                    'E1 OUT 0 INP INM 1e6\n' + statements + '\n.ends buffer\n')
                run, results = self.run_case()
                result = results[-1]
                self.assertEqual((result['execution'], result['outcome']), (status, 'unknown'), result)
                self.assertNotIn('metrics', result['details'])
                self.assertFalse((run / 'work/active/measurements.json').exists())
                if diagnostic:
                    self.assertIn(diagnostic, (run / 'work/active/step/ngspice.log').read_text())
                else:
                    self.assertIn('explicit diode model level', result['summary'])
                    self.assertFalse((run / 'work/active/version').exists())

    def test_actual_solver_failure_is_unknown_not_defect_and_retains_log(self):
        (self.source / 'owned.lib').write_text('.subckt buffer INP INM VP VN OUT\n'
            'V1 OUT 0 1\nV2 OUT 0 2\n.ends buffer\n')
        run, results = self.run_case()
        self.assertEqual((results[-1]['execution'], results[-1]['outcome']), ('failed', 'unknown'), results[-1])
        self.assertIn('work/active/step/ngspice.log', results[-1]['evidence'])
        self.assertNotIn('metrics', results[-1]['details'])

    def test_wrong_candidate_or_wrong_kind_prerequisite_is_not_run(self):
        run, results = self.run_case()
        manifest = read_json(run / 'candidate.json')
        spec = manifest['scope']['checks'][1]
        for key, bad in (('candidate_id', 'wrong'), ('kind', 'geometry'), ('execution', 'failed')):
            prior = {**results[0], key: bad}
            write_json(run / 'checks/netlist.json', prior)
            ctx = Ctx(run, manifest, spec, time.monotonic()+120)
            with self.subTest(key=key), self.assertRaises(CheckError) as raised:
                check_active_spice(ctx)
            self.assertEqual(raised.exception.execution, 'not_run')

    def test_nested_captured_model_closure_is_staged_and_sealed(self):
        (self.source / 'core.lib').write_text((self.source / 'owned.lib').read_text())
        (self.source / 'owned.lib').write_text('* retained comment\n.include "core.lib"\n')
        self.scope['inputs'][0]['include'].append('core.lib')
        params = self.scope['checks'][1]['params']
        params['model_files'].append('design/core.lib')
        params['active_parts']['U1']['model_file'] = 'design/core.lib'
        run, results = self.run_case()
        self.assertEqual((results[-1]['execution'], results[-1]['outcome']), ('ok', 'pass'), results[-1])
        self.assertEqual((run / 'work/active/models/design/owned.lib').read_text(),
                         '* retained comment\n.include /work/models/design/core.lib\n')
        self.assertIn('design/core.lib', read_json(run / 'baseline.json')['files'])

    def test_missing_named_subcircuit_is_unsupported(self):
        self.scope['checks'][1]['params']['active_parts']['U1']['subcircuit'] = 'absent'
        run, results = self.run_case()
        self.assertEqual(results[-1]['execution'], 'unsupported', results[-1])
        self.assertIn('subcircuit absent', results[-1]['summary'])

    def test_later_ac_failure_cannot_promote_completed_transient_to_pass(self):
        sources = self.scope['checks'][1]['params']['sources']
        del sources[0]['ac_magnitude_v']
        sources[1]['ac_magnitude_v'] = 1  # AC supply excitation; signal input is zero.
        run, results = self.run_case()
        self.assertEqual((results[-1]['execution'], results[-1]['outcome']), ('malformed', 'unknown'), results[-1])
        self.assertIn('zero AC transfer', results[-1]['summary'])
        self.assertIn('work/active/step/result.raw', results[-1]['evidence'])
        self.assertIn('work/active/gain/result.raw', results[-1]['evidence'])
        self.assertNotIn('metrics', results[-1]['details'])

    def test_all_unbounded_measurements_are_explicitly_informational(self):
        for analysis in self.scope['checks'][1]['params']['analyses']:
            for measurement in analysis['measurements']:
                del measurement['min'], measurement['max']
                measurement['informational'] = True
        run, results = self.run_case()
        self.assertEqual((results[-1]['execution'], results[-1]['outcome']), ('ok', 'info'), results[-1])

    def test_real_sandbox_deadline_failure_retains_execution(self):
        from agent_ee.active_spice import run_analysis
        write_json(self.root / 'scope.json', self.scope)
        run = self.root / 'run'
        run.mkdir()
        manifest = capture(self.root / 'scope.json', run)
        ctx = Ctx(run, manifest, self.scope['checks'][1], time.monotonic()+2)
        with self.assertRaises(CheckError) as raised:
            run_analysis(ctx, ctx.work / 'deadline', ['/usr/bin/python3', '-c', 'import time; time.sleep(10)'])
        self.assertEqual(raised.exception.execution, 'interrupted')
        execution = read_json(ctx.work / 'deadline/execution.json')
        self.assertEqual(execution['execution'], 'timeout')
        self.assertIn('work/active/deadline/execution.json', ctx.result['evidence'])

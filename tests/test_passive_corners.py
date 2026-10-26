"""Owned RC expectations: written before native qualification runs."""
import copy
import unittest

from agent_ee import passive_corners
from test_checks import BASE_PARAMS, conn_fixture


class CornerContractTest(unittest.TestCase):
    def test_shipped_fixture_qualification_matches_reviewed_producer(self):
        self.assertEqual(read_json(REPO / 'fixtures/passive-corners/qualification.json'),
                         passive_corners.qualification(),
                         'Shipped fixture needs semantic review, pin refresh and native requalification')

    def test_complete_endpoint_grid_and_native_values(self):
        params = {**copy.deepcopy(BASE_PARAMS), 'tolerances': {
            'R1': [-0.1, 0.1], 'C1': [-0.1, 0.1]}, 'max_corners': 4}
        plan = passive_corners.plan(conn_fixture(), params)
        self.assertEqual(len(plan['corners']), 4)
        for corner, expected in zip(plan['corners'], [0.00081, 0.00099, 0.00099, 0.00121]):
            self.assertAlmostEqual(corner['tau_s'], expected, places=15)

    def test_zero_tolerances_deduplicate(self):
        params = {**copy.deepcopy(BASE_PARAMS), 'tolerances': {
            'R1': [0, 0], 'C1': [0, 0]}, 'max_corners': 1}
        planned = passive_corners.plan(conn_fixture(), params)
        self.assertEqual(len(planned['corners']), 1)
        self.assertEqual(planned['corners'][0]['values'], planned['nominal']['values'])

    def test_unqualified_contracts_refuse(self):
        base = {**copy.deepcopy(BASE_PARAMS), 'tolerances': {
            'R1': [-0.1, 0.1], 'C1': [-0.1, 0.1]}, 'max_corners': 4}
        for override in ({'tolerances': {'R1': [0, 0]}}, {'max_corners': 3},
                         {'max_corners': 5}, {'step_v': -1}, {'stop_ms': 10000},
                         {'tolerances': {'R1': [-1, .1], 'C1': [0, 0]}},
                         {'tolerances': {'R1': [float('nan'), .1], 'C1': [0, 0]}},
                         {'correlation': 'positive'}, {'grid': [0, .1]},
                         {'measurements': [{'name': 'early', 'at_ms': .000001, 'max': 1}]}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                passive_corners.plan(conn_fixture(), {**base, **override})
        conn = conn_fixture()
        conn['nets']['/IN'].remove('R1.1')
        conn['nets']['GND'].append('R1.1')
        with self.assertRaisesRegex(ValueError, 'topology'):
            passive_corners.plan(conn, base)
        conn = conn_fixture()
        conn['components']['C1']['libsource']['part'] = 'L'
        with self.assertRaises(checks.CheckError):
            passive_corners.plan(conn, base)


import contextlib
import io
import math
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from agent_ee import checks
from agent_ee.baseline import validate_witnesses
from agent_ee.candidate import capture
from agent_ee.run_record import run_review, verify
from agent_ee.storage import digest, read_json, write_json
import test_capability_pipeline as pipeline
from toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]


class NativeCornerTests(unittest.TestCase):
    agent = pipeline.NativeCapabilityPipelineTests.agent

    def setUp(self):
        pipeline.NativeCapabilityPipelineTests.setUp(self)
        self.expected = read_json(REPO / 'tests/fixtures/passive-corners.json')

    def prepare(self, label, *, zero=False):
        root = pipeline.NativeCapabilityPipelineTests.prepare(self, label)
        scope = read_json(root / 'scope.json')
        scope['checks'] = [s for s in scope['checks'] if s['kind'] in ('netlist', 'rc_transient')]
        scope['baseline']['requirements'] = [r for r in scope['baseline']['requirements']
                                             if r['id'] in ('R-response', 'I-netlist')]
        spec = scope['checks'][1]
        spec['kind'] = 'passive_corners'
        spec['params'].update(tolerances={'R1': [0, 0] if zero else [-.1, .1],
                                         'C1': [0, 0] if zero else [-.1, .1]},
                              max_corners=4, qualification='qualification.json')
        limits = self.expected['defect_limits_v' if label == 'corner-defect' else 'control_limits_v']
        spec['params']['measurements'] = [{'name': 'vout_1ms', 'at_ms': 1.,
                                          'min': limits[0], 'max': limits[1]}]
        write_json(root / 'qualification.json', passive_corners.qualification())
        scope['inputs'].append({'path': str(root / 'qualification.json'), 'at': 'qualification.json'})
        write_json(root / 'scope.json', scope)
        return root

    def test_native_control_defect_zero_and_sealed_witnesses(self):
        for label in ('corner-control', 'corner-defect', 'corner-zero'):
            with self.subTest(label=label):
                root = self.prepare(label, zero=label == 'corner-zero')
                with patch('agent_ee.reviewers.dispatch', side_effect=self.agent), \
                     contextlib.redirect_stdout(io.StringIO()):
                    status = run_review(root / 'scope.json', root / 'run', 180, host=self.host)
                result = verify(root / 'run', host=self.host)
                expected = self.expected['expected_defect' if label == 'corner-defect' else 'expected_control']
                self.assertEqual(result['outcome'], expected, result)
                self.assertEqual(status, int(label == 'corner-defect'))
                check = next(c for c in result['checks'] if c['kind'] == 'passive_corners')
                self.assertEqual(check['execution'], 'ok', check)
                metric = check['details']['metrics']['vout_1ms']
                self.assertAlmostEqual(metric['value'], self.expected['nominal_v'], delta=.000102)
                self.assertEqual(metric['unit'], 'V')
                self.assertIn('ramp=1ns;initial=0V', metric['condition'])
                ensemble = check['details']['ensemble']
                self.assertTrue(ensemble['complete'])
                self.assertEqual(ensemble['expected'], ensemble['completed'])
                self.assertEqual(len(ensemble['completed']), 1 if label == 'corner-zero' else 4)
                for corner, tau in zip(ensemble['plan']['corners'],
                                       [.001] if label == 'corner-zero' else self.expected['endpoint_tau_s']):
                    report = read_json(root / 'run/work/rc-step' / corner['id'] / 'metrics.json')
                    got = report['measurements']['vout_1ms']['measured']
                    self.assertAlmostEqual(got, 1 - math.exp(-.001 / tau), delta=.000102)
                if label == 'corner-zero':
                    manifest = read_json(root / 'run/candidate.json')
                    nominal_spec = copy.deepcopy(manifest['scope']['checks'][1])
                    nominal_spec.update(id='nominal-comparison', kind='rc_transient')
                    nominal_ctx = checks.Ctx(root / 'run', manifest, nominal_spec, time.monotonic() + 120)
                    checks.check_rc_transient(nominal_ctx)
                    native_nominal = nominal_ctx.result['details']['metrics']['vout_1ms']['value']
                    self.assertAlmostEqual(metric['value'], native_nominal, delta=.000102)
                if label == 'corner-defect':
                    self.assertLess(metric['lower'], .60)
                    self.assertGreater(metric['upper'], .68)
                    self.assertTrue(any(f.get('status') == 'accepted' and f['source'] == 'check'
                                        for f in result['findings']))
                    witness = [{'requirement_id': 'R-response', 'check_id': 'rc-step'}]
                    evidence = [{'path': 'checks/rc-step.json', 'locator': 'outcome'}]
                    validate_witnesses(root / 'run', witness, evidence)
                    self.assert_coupling_keeps_envelope(check)
                    for rel in ('ensemble.json', 'corner-00/deck.cir', 'corner-00/result.raw',
                                'corner-00/metrics.json'):
                        path = root / 'run/work/rc-step' / rel
                        original = path.read_bytes()
                        path.write_bytes(original + b'\n')
                        with self.assertRaisesRegex(ValueError, 'changed'):
                            validate_witnesses(root / 'run', witness, evidence)
                        path.write_bytes(original)

    def assert_coupling_keeps_envelope(self, corner):
        # The native corner result joins a protocol thermal result in a separate run directory.
        from agent_ee.measurements import check_coupled_margins
        metric = corner['details']['metrics']['vout_1ms']
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            manifest = {'candidate_id': corner['candidate_id'], 'files': {}, 'scope': {
                'schema': 'agent-ee.scope/1',
                'checks': [{'id': 'rc-step'}, {'id': 'thermal'}, {'id': 'joint'}]}}
            write_json(run / 'checks/rc-step.json', corner)
            write_json(run / 'checks/thermal.json', {
                'candidate_id': corner['candidate_id'], 'execution': 'ok',
                'details': {'producer': {'domain': 'thermal'}, 'metrics': {'peak': {
                    'value': 40, 'unit': 'degC', 'condition': metric['condition']}}}})
            for change in (None, 'condition', 'unit'):
                with self.subTest(coupling=change):
                    limit = {'unit': 'mV' if change == 'unit' else 'V',
                             'condition': metric['condition'], 'min': .60, 'max': .68}
                    ctx = checks.Ctx(run, manifest, {'id': 'joint', 'kind': 'coupled_margins', 'params': {
                        'condition': metric['condition'] + (';load=1k' if change == 'condition' else ''),
                        'members': [{'name': 'vout', 'check': 'rc-step', 'metric': 'vout_1ms'},
                                    {'name': 'heat', 'check': 'thermal', 'metric': 'peak'}],
                        'limits': {'vout': limit, 'heat': {'unit': 'degC', 'max': 85,
                                                           'condition': metric['condition']}}}},
                        time.monotonic() + 30)
                    if change:
                        with self.assertRaisesRegex(checks.CheckError, change):
                            check_coupled_margins(ctx)
                        self.assertNotIn('margins', ctx.result['details'])
                        continue
                    check_coupled_margins(ctx)
                    self.assertEqual(ctx.result['outcome'], 'defect')
                    self.assertEqual(ctx.result['details']['metrics']['vout'], metric)
                    self.assertEqual(ctx.result['details']['margins']['vout'],
                                     corner['details']['margins']['vout_1ms'])

    def test_partial_ensembles_and_identity_changes_never_pass(self):
        root = self.prepare('corner-failures')
        (root / 'run').mkdir()
        capture(root / 'scope.json', root / 'run')
        manifest = read_json(root / 'run/candidate.json')
        spec = manifest['scope']['checks'][1]
        # Produce real connectivity once; injected failures affect only the tested boundary.
        net = checks.Ctx(root / 'run', manifest, manifest['scope']['checks'][0], time.monotonic() + 120)
        checks.check_netlist(net)
        write_json(root / 'run/checks/netlist.json', net.result)
        real = passive_corners.run_analysis
        for failed_id in ('corner-01', 'corner-03'):
            spec_copy = copy.deepcopy(spec)
            spec_copy['id'] = failed_id + '-check'
            manifest_copy = copy.deepcopy(manifest)
            manifest_copy['scope']['checks'][1] = spec_copy
            ctx = checks.Ctx(root / 'run', manifest_copy, spec_copy, time.monotonic() + 120)
            def fail(ctx, conn, params, analysis):
                if analysis['id'] == failed_id:
                    raise checks.CheckError('failed', 'injected native failure boundary')
                return real(ctx, conn, params, analysis)
            with patch.object(passive_corners, 'run_analysis', side_effect=fail):
                with self.assertRaises(checks.CheckError):
                    passive_corners.check_passive_corners(ctx)
            self.assertEqual(ctx.result['outcome'], 'unknown')
            self.assertNotIn('metrics', ctx.result['details'])
            receipt = read_json(ctx.work / 'ensemble.json')
            self.assertFalse(receipt['complete'])
            self.assertNotEqual(receipt['expected'], receipt['completed'])
        for field in ('parser_sha256', 'model_sha256', 'tool_sha256', 'policy'):
            changed = passive_corners.qualification()
            changed[field] = 'changed'
            write_json(root / 'run/inputs/qualification.json', changed)
            modified = copy.deepcopy(manifest)
            modified['files']['qualification.json'] = digest(root / 'run/inputs/qualification.json')
            ctx = checks.Ctx(root / 'run', modified, spec, time.monotonic() + 120)
            with self.assertRaisesRegex(checks.CheckError, 'requalification'):
                passive_corners.check_passive_corners(ctx)
            self.assertNotIn('metrics', ctx.result['details'])

    def test_corrupt_corner_output_and_empirical_transfer_refuse(self):
        root = self.prepare('corrupt-corner')
        (root / 'run').mkdir()
        capture(root / 'scope.json', root / 'run')
        manifest = read_json(root / 'run/candidate.json')
        net = checks.Ctx(root / 'run', manifest, manifest['scope']['checks'][0], time.monotonic() + 120)
        checks.check_netlist(net)
        write_json(root / 'run/checks/netlist.json', net.result)
        original_analyze = checks.analyze_sim
        for failure in ('truncated', 'nonfinite', 'oracle', 'empirical', 'tool'):
            with self.subTest(failure=failure):
                copied = copy.deepcopy(manifest)
                spec = copied['scope']['checks'][1]
                spec['id'] = failure
                if failure == 'empirical':
                    copied['scope']['empirical'] = 'nominal-residual.json'
                ctx = checks.Ctx(root / 'run', copied, spec, time.monotonic() + 120)
                def corrupt(work, wanted):
                    if work.name == 'corner-01':
                        if failure == 'truncated':
                            path = work / 'result.raw'
                            path.write_bytes(path.read_bytes()[:-3])
                        elif failure in ('nonfinite', 'oracle'):
                            log = work / 'ngspice.log'
                            import re
                            log.write_text(re.sub(r'(vout_1ms\s*=\s*)\S+',
                                                  r'\g<1>' + ('nan' if failure == 'nonfinite' else '0.1'),
                                                  log.read_text()))
                    return original_analyze(work, wanted)
                with patch.object(checks, 'analyze_sim', side_effect=corrupt):
                    if failure == 'tool':
                        with patch.object(passive_corners, 'TOOL_SHA256', '0' * 64):
                            with self.assertRaisesRegex(checks.CheckError, 'requalification'):
                                passive_corners.check_passive_corners(ctx)
                    else:
                        with self.assertRaises(checks.CheckError):
                            passive_corners.check_passive_corners(ctx)
                self.assertEqual(ctx.result['outcome'], 'unknown')
                self.assertNotIn('margins', ctx.result['details'])
                if failure != 'empirical':
                    self.assertNotIn('metrics', ctx.result['details'])
                else:
                    self.assertIn('metrics', ctx.result['details'])


class StaticCornerCoverageTests(unittest.TestCase):
    def test_descriptor_and_scope2_own_qualification_inputs(self):
        from agent_ee.baseline import compile_baseline
        from agent_ee.capabilities import describe_check
        scope = read_json(REPO / 'fixtures/passive-corners/control.scope.json')
        files = {p: '0' * 64 for p in ('qualification.json', 'requirements/system.md',
                 'requirements/reference.md', 'requirements/targets.md', scope['schematic'])}
        spec = scope['checks'][1]
        descriptor = describe_check(spec, check_specs=scope['checks'], files=files, scope=scope)
        self.assertEqual(descriptor['status'], 'supported', descriptor)
        self.assertTrue(descriptor['can_evaluate_bound'])
        self.assertIn('qualification.json', descriptor['immutable_inputs'])
        self.assertIn('qualification.json', compile_baseline(scope, files)['files'])
        scope['schema'] = 'agent-ee.scope/2'
        for req in scope['baseline']['requirements']:
            req.pop('intent')
        self.assertIn('qualification.json', compile_baseline(scope, files)['files'])
        for override in ({'max_corners': 3}, {'qualification': 'absent.json'},
                         {'measurements': [{'name': 'v', 'at_ms': True, 'min': 0}]},
                         {'measurements': [1]}, {'measurements': [{'name': 'v', 'at_ms': 1, 'min': True}]}):
            invalid = copy.deepcopy(spec)
            invalid['params'].update(override)
            report = describe_check(invalid, check_specs=[scope['checks'][0], invalid], files=files, scope=scope)
            self.assertEqual(report['status'], 'unsupported', report)

    def test_coupling_requires_canonical_corner_condition(self):
        from agent_ee.capabilities import describe_check
        scope = read_json(REPO / 'fixtures/passive-corners/control.scope.json')
        files = {p: '0' * 64 for p in ('qualification.json', 'requirements/system.md',
                 'requirements/reference.md', 'requirements/targets.md', scope['schematic'],
                 'adapter.py', 'solver.py')}
        corner = scope['checks'][1]
        expected = passive_corners.condition(corner['params'], corner['params']['measurements'][0])
        self.assertIn('ramp=1ns;initial=0V;at=1ms;independent-tolerances=', expected)
        thermal = {'id': 'thermal', 'kind': 'measurements', 'params': {
            'adapter': 'adapter.py', 'tool': {'argv': ['/usr/bin/python3', '/candidate/solver.py'],
                'sha256': 'a' * 64, 'domain': 'thermal', 'inputs': ['solver.py']},
            'limits': {'peak': {'unit': 'degC', 'condition': expected, 'max': 85}}}}
        for condition, status in ((expected, 'supported'),
                                  (expected.replace('step=1V', 'step=2V'), 'unsupported'),
                                  ('ideal-unloaded-1.0V-step-at-1.0ms', 'unsupported')):
            coupled = {'id': 'joint', 'kind': 'coupled_margins', 'params': {
                'condition': condition,
                'members': [{'name': 'vout', 'check': 'rc-step', 'metric': 'vout_1ms'},
                            {'name': 'heat', 'check': 'thermal', 'metric': 'peak'}],
                'limits': {'vout': {'unit': 'V', 'condition': condition, 'min': .55, 'max': .72},
                           'heat': {'unit': 'degC', 'condition': condition, 'max': 85}}}}
            specs = [*scope['checks'], thermal, coupled]
            with self.subTest(condition=condition):
                report = describe_check(coupled, check_specs=specs, files=files, scope=scope)
                self.assertEqual(report['status'], status, report)

    def test_only_qualified_topology_is_supported(self):
        params = {**copy.deepcopy(BASE_PARAMS), 'tolerances': {
            'R1': [-.1, .1], 'C1': [-.1, .1]}, 'max_corners': 4}
        for part in ('R', 'L', 'Q_NPN_BCE'):
            conn = conn_fixture()
            ref = 'R2' if part == 'R' else 'L1' if part == 'L' else 'Q1'
            conn['components'][ref] = {**copy.deepcopy(conn['components']['R1']),
                                      'libsource': {'lib': 'Device', 'part': part}}
            conn['nets']['/OUT'].append(ref + '.1')
            conn['nets']['GND'].append(ref + '.2')
            with self.subTest(part=part), self.assertRaises((ValueError, checks.CheckError)):
                passive_corners.plan(conn, params)

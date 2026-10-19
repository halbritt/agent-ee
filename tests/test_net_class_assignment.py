"""Native saved-project assignment controls for the pod's exact RF net name."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest

from toolchain import require_toolchain
from agent_ee import checks
from agent_ee.baseline import compile_baseline
from agent_ee.capabilities import describe_check
from agent_ee.net_constraints import emit
from agent_ee.net_class_assignment import check_net_class_assignment
from agent_ee.storage import digest, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


class NetClassAssignmentTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        # Owned RC copper fixture, renamed to the real native pod net (no vendor design).
        board = (ROOT / 'fixtures/rc/control/rc_filter.kicad_pcb').read_bytes().replace(b'"/IN"', b'"/POD_GNSS_RF"')
        (self.inputs / 'board.kicad_pcb').write_bytes(board)
        self.doc = {'schema': 'agent-ee.net-constraints/1', 'classes': [dict(name='RF_50', requirement='rf',
                    nets=['/POD_GNSS_RF'], track_width_mm=.335, clearance_mm=.2032, impedance_ohm=50)]}
        raw = json.dumps(self.doc).encode()
        (self.inputs / 'constraints.json').write_bytes(raw)
        emitted = emit(raw, (ROOT / 'fixtures/netclass-completeness/native.kicad_pro').read_bytes(),
                       envelope_raw=(ROOT / 'fixtures/board-envelope/rc.envelope.json').read_bytes(), board_raw=board)
        for key, rel in [('project', 'board.kicad_pro'), ('rules', 'board.kicad_dru'), ('record', 'record.json')]:
            (self.inputs / rel).write_bytes(emitted[key])
        (self.inputs / 'requirements.md').write_text('rf: RF class application required')
        self.spec = dict(id='assignment', kind='net_class_assignment', params=dict(
            project='board.kicad_pro', record='record.json', constraints='constraints.json'))
        self.scope = dict(schema='agent-ee.scope/2', board='board.kicad_pcb', checks=[self.spec],
            baseline=dict(requirements=[dict(id='rf', statement='RF class',
                source=dict(path='requirements.md', locator='rf'), checks=['assignment'])],
                targets=['requirements.md'], reference_architectures=['requirements.md']))

    def manifest(self):
        files = {p.name: digest(p) for p in self.inputs.iterdir()}
        return dict(candidate_id='native-assignment-test', scope=self.scope, files=files)

    def run_check(self):
        ctx = checks.Ctx(self.root, self.manifest(), self.spec, time.monotonic() + 120)
        check_net_class_assignment(ctx)
        return ctx.result

    def test_native_assignment_width_clearance_and_impedance(self):
        result = self.run_check()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        native = result['details']['native']
        self.assertEqual(native['nets']['/POD_GNSS_RF'], 'RF_50')
        self.assertEqual(native['classes']['RF_50'], dict(track_width_nm=335000, clearance_nm=203200))
        record = read_json(self.inputs / 'record.json')
        self.assertEqual(record['classes'][0]['impedance']['status'],
                         'recorded_not_enforced')
        self.assertFalse(record['acceptance_ready'])
        self.assertEqual(record['project_sha256'], digest(self.inputs / 'board.kicad_pro'))
        self.assertIn('work/assignment/net-class-native.json', result['evidence'])

    def test_native_missing_class_and_unassigned_net_fail_closed(self):
        path = self.inputs / 'board.kicad_pro'
        original = read_json(path)
        for label in ('class_absent', 'net_unassigned'):
            with self.subTest(label=label):
                project = copy.deepcopy(original)
                if label == 'class_absent':
                    project['net_settings']['classes'] = project['net_settings']['classes'][:1]
                else:
                    project['net_settings']['netclass_patterns'] = []
                write_json(path, project)
                result = self.run_check()
                self.assertEqual(result['outcome'], 'defect')
                self.assertIn(label, [d['reason'] for d in result['details']['defects']])

    def test_native_missing_net_and_below_width_clearance(self):
        board = self.inputs / 'board.kicad_pcb'
        board.write_bytes(board.read_bytes().replace(b'"/POD_GNSS_RF"', b'"/OTHER"'))
        self.assertIn('net_absent', [d['reason'] for d in self.run_check()['details']['defects']])
        path = self.inputs / 'board.kicad_pro'
        project = read_json(path)
        project['net_settings']['classes'][1].update(track_width=.334999, clearance=.203199)
        write_json(path, project)
        reasons = [d['reason'] for d in self.run_check()['details']['defects']]
        self.assertIn('min_track_width_mm_below_minimum', reasons)
        self.assertIn('clearance_mm_below_minimum', reasons)

    def test_registration_baseline_and_preflight(self):
        from agent_ee.preflight import capture_expectation
        manifest = self.manifest()
        descriptor = describe_check(self.spec, check_specs=[self.spec], files=manifest['files'],
                                    scope=self.scope, inputs=self.inputs)
        self.assertEqual(descriptor['status'], 'supported', descriptor)
        self.assertEqual(descriptor['predicates'], ['netclass:effective-assignment'])
        immutable = {'board.kicad_pro', 'board.kicad_dru', 'record.json', 'constraints.json'}
        self.assertEqual(set(descriptor['immutable_inputs']), immutable)
        baseline = compile_baseline(self.scope, manifest['files'], self.inputs)
        self.assertTrue(immutable <= baseline['files'].keys())
        self.assertNotIn('board.kicad_pcb', baseline['files'])
        expectation = capture_expectation(dict(run_id='run', policy_files={}, runtime_files={}),
                                          manifest, [], inputs=self.inputs)
        self.assertIn('pcbnew', expectation['required_runtime'])
        self.assertIs(checks.CHECK_FUNCS['net_class_assignment'], check_net_class_assignment)

    def test_missing_output_and_crashed_probe_never_pass(self):
        from unittest.mock import patch
        for mode in ('missing', 'crashed'):
            ctx = checks.Ctx(self.root, self.manifest(), self.spec, time.monotonic() + 120)
            failure = checks.CheckError('failed', 'probe crashed') if mode == 'crashed' else None
            with patch.object(ctx, 'execute', side_effect=failure), self.assertRaises(checks.CheckError):
                check_net_class_assignment(ctx)
            self.assertEqual(ctx.result['outcome'], 'unknown')

    def test_fractional_nanometre_clearance_does_not_round_below_minimum(self):
        from agent_ee.net_class_assignment import evaluate
        record=read_json(self.inputs/'record.json')
        record['classes'][0]['clearance_mm']=.2032004
        native=dict(classes={'RF_50':dict(track_width_nm=335000,clearance_nm=203200)},
                    nets={'/POD_GNSS_RF':'RF_50'})
        self.assertEqual([d['reason'] for d in evaluate(record,native)],['clearance_mm_below_minimum'])

    def test_malformed_native_inventory_is_unknown(self):
        from agent_ee.net_class_assignment import evaluate
        record=read_json(self.inputs/'record.json')
        for native in ({'classes':[], 'nets':{}}, {'classes':{'RF_50':{}}, 'nets':{}},
                       {'classes':{}, 'nets':{'/POD_GNSS_RF':None}}):
            with self.subTest(native=native), self.assertRaises(ValueError):
                evaluate(record,native)

    def test_malformed_or_changed_record_refused(self):
        path = self.inputs / 'record.json'
        original = read_json(path)
        for patch in ({'outcome': 'unknown'}, {'source_sha256': '0' * 64}, {'classes': []}, {'rules_sha256': None}):
            write_json(path, {**original, **patch})
            with self.assertRaises(checks.CheckError):
                self.run_check()
        write_json(path, original)
        rules = self.inputs / 'board.kicad_dru'
        rules.write_bytes(b'(version 1)\n')
        self.assertIn('rules_differ_from_record', [d['reason'] for d in self.run_check()['details']['defects']])


if __name__ == '__main__':
    unittest.main()

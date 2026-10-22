"""Whole-planner PyPy transport: exact plans, visible errors and child ownership."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agent_ee import placement_runtime as runtime, placement_scoring, placement_trace, placer_runner
from test_block_arrangement import ArrangementFixture, plan_policy
from test_placer_search import anchor_case, promotion_case


class RuntimeTest(unittest.TestCase):
    def test_default_calls_original_planner_without_starting_process(self):
        source, policy = promotion_case()
        with mock.patch.object(runtime.subprocess, 'Popen', side_effect=AssertionError('unexpected process')):
            self.assertEqual(placer_runner._plan(source, {'policy': policy}),
                             placer_runner._plan_local(source, {'policy': policy}))

    def test_real_cpython_is_refused_without_fallback(self):
        source, policy = promotion_case()
        with runtime.experimental(sys.executable) as session:
            with mock.patch.object(placer_runner, '_plan_local', side_effect=AssertionError('fallback')):
                with self.assertRaisesRegex(runtime.PlanningProcessError, 'did not identify itself as PyPy'):
                    placer_runner._plan(source, {'policy': policy})
        self.assertIsNone(runtime.active())
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0]['status'], 'error')
        self.assertEqual(session.calls[0]['runtime']['implementation'], 'CPython')
        self.assertNotEqual(session.calls[0]['exit_code'], 0)

    def test_transport_rejects_non_json_types_before_launch_and_default_stays_unchanged(self):
        source, policy = promotion_case()
        tuple_policy = {**policy, 'fixed_refs': tuple(policy['fixed_refs'])}
        with mock.patch.object(runtime.subprocess, 'Popen', side_effect=AssertionError('unexpected process')):
            with self.assertRaisesRegex(ValueError, 'expected unique explicit fixed_refs'):
                placer_runner._plan(source, {'policy': tuple_policy})
            cases = [
                (source, {'policy': tuple_policy}, None, 'JSON-native values, got tuple'),
                ({**source, 'extra': {7: 'integer key'}}, {'policy': policy}, None, 'keys must be strings'),
                (source, {'policy': policy}, {**policy, 'fixed_refs': ('F',)}, 'JSON-native values, got tuple'),
                (source, {'policy': policy, 'extra': {'set'}}, None, 'JSON-native values, got set'),
                (source, {'policy': policy, 'extra': b'bytes'}, None, 'JSON-native values, got bytes'),
                (source, {'policy': policy, 'extra': float('inf')}, None, 'Out of range float values'),
            ]
            for facts, request, effective, message in cases:
                with self.subTest(message=message), runtime.experimental(sys.executable) as session:
                    with self.assertRaisesRegex(ValueError, message):
                        placer_runner._plan(facts, request, effective)
                    self.assertEqual(session.calls[0]['status'], 'error')
                    self.assertIsNone(session.calls[0]['exit_code'])

    def test_trace_rust_and_nested_runtime_are_explicitly_refused_before_launch(self):
        source, policy = promotion_case()
        with runtime.experimental(sys.executable) as session:
            with mock.patch.object(runtime.subprocess, 'Popen', side_effect=AssertionError('unexpected process')):
                with placement_trace.recording():
                    with self.assertRaisesRegex(ValueError, 'active placement trace'):
                        placer_runner._plan(source, {'policy': policy})
                with mock.patch.object(placement_scoring, 'active', return_value=object()):
                    with self.assertRaisesRegex(ValueError, 'active Rust scorer'):
                        placer_runner._plan(source, {'policy': policy})
                with self.assertRaisesRegex(ValueError, 'already active'):
                    with runtime.experimental(sys.executable):
                        self.fail('nested runtime admitted')
        self.assertEqual(session.calls, [])

    def test_callback_error_and_malformed_protocol_terminate_and_reap_real_child(self):
        event = json.dumps({'schema': runtime._SCHEMA, 'kind': 'heartbeat', 'record': {'status': 'packing'}})
        for output, callback, error in [
                (event, mock.Mock(side_effect=RuntimeError('callback failed')), RuntimeError),
                ('not json', mock.Mock(), json.JSONDecodeError)]:
            with self.subTest(output=output), tempfile.TemporaryDirectory() as directory:
                command = [sys.executable, '-c', 'import sys,time; sys.stderr.write("child diagnostics\\n"); '
                           'sys.stderr.flush(); print(' + repr(output) + ', flush=True); time.sleep(30)']
                spawned = []
                launch = subprocess.Popen

                def capture(*args, **kwargs):
                    process = launch(*args, **kwargs)
                    spawned.append(process)
                    return process

                record = {}
                with mock.patch.object(runtime.subprocess, 'Popen', side_effect=capture):
                    with self.assertRaises(error):
                        runtime._exchange(command, Path(directory), callback, record)
                self.assertEqual(len(spawned), 1)
                self.assertIsNotNone(spawned[0].returncode)
                self.assertLess(spawned[0].returncode, 0)
                self.assertTrue(spawned[0].stdout.closed)
                self.assertEqual(record['stderr'], 'child diagnostics\n')

    def test_child_exit_without_result_is_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            record = {}
            with self.assertRaisesRegex(runtime.PlanningProcessError, 'exited 7 without a result: broken'):
                runtime._exchange([sys.executable, '-c', 'import sys;sys.stderr.write("broken");sys.exit(7)'],
                                  Path(directory), self.fail, record)
            self.assertEqual(record['exit_code'], 7)
            self.assertEqual(record['stderr'], 'broken')


class PyPyFixture:
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get('AGENT_EE_PYPY') or shutil.which('pypy3')
        if configured is None:
            reason = 'toolchain prerequisite: set AGENT_EE_PYPY or install pypy3 on PATH'
            if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
                raise AssertionError('AGENT_EE_REQUIRE_TOOLCHAIN=1: ' + reason)
            raise unittest.SkipTest(reason)
        cls.pypy = Path(configured)


class PyPyRuntimeTest(PyPyFixture, unittest.TestCase):
    def test_complete_plan_and_heartbeat_parity_and_python_score_witness(self):
        for case in [promotion_case, anchor_case]:
            source, policy = case()
            original = copy.deepcopy((source, policy))
            expected_heartbeat, actual_heartbeat = [], []
            expected = placer_runner._plan_local(source, {'policy': policy}, heartbeat=expected_heartbeat.append)
            with runtime.experimental(self.pypy) as session:
                with mock.patch.object(placer_runner, '_heartbeat', actual_heartbeat.append), \
                        mock.patch.object(placer_runner, '_plan_local', side_effect=AssertionError('local fallback')):
                    actual = placer_runner._plan(source, {'policy': policy})
            self.assertEqual(actual, expected)
            self.assertGreaterEqual(len(expected_heartbeat), 2)
            self.assertEqual(actual_heartbeat, expected_heartbeat)
            self.assertEqual((source, policy), original)
            self.assertEqual(len(session.calls), 1)
            call = session.calls[0]
            self.assertEqual((call['status'], call['exit_code'], call['error']), ('ok', 0, None))
            self.assertEqual(call['runtime']['implementation'], 'PyPy')
            self.assertEqual(call['runtime']['executable_sha256'], runtime._sha(self.pypy.resolve()))
            self.assertEqual(call['source_sha256'], runtime._sources())
            self.assertTrue(call['first_wire_cost']['returned'])
            self.assertGreater(call['wall_ns'], 0)

    def test_budget_exhaustion_remains_a_typed_plan_and_validation_remains_valueerror(self):
        source, policy = promotion_case()
        policy['step_budget'] = 1
        expected = placer_runner._plan_local(source, {'policy': policy}, heartbeat=lambda _: None)
        with runtime.experimental(self.pypy) as session:
            actual = placer_runner._plan(source, {'policy': policy})
            self.assertEqual(actual, expected)
            self.assertEqual(actual['status'], 'packing_budget_exhausted')
            policy['step_budget'] = 0
            with self.assertRaisesRegex(ValueError, 'step budget must be 1..1000000') as raised:
                placer_runner._plan(source, {'policy': policy})
        self.assertIn('PyPy planning traceback:', raised.exception.__notes__[0])
        self.assertEqual([c['status'] for c in session.calls], ['ok', 'error'])
        self.assertEqual(session.calls[1]['error']['type'], 'ValueError')


class PyPyBlocksTest(PyPyFixture, ArrangementFixture, unittest.TestCase):
    def test_complete_block_request_preserves_native_netlist_identity_and_effective_policy(self):
        request = {'policy': self.policy, 'blocks': {'arrangement': plan_policy(), 'candidates': self.entries}}
        effective = {**self.policy, 'obstacles': self.policy['obstacles'] + [
            {'side': 'F', 'polygons': [[[1000000000, 1000000000], [1001000000, 1000000000],
                                      [1001000000, 1001000000], [1000000000, 1001000000]]]}]}
        netlist = self.dir / 'design.xml'
        original = netlist.read_bytes()
        expected = placer_runner._plan_local(self.board, request, effective, netlist, heartbeat=lambda _: None)
        with runtime.experimental(self.pypy) as session:
            actual = placer_runner._plan(self.board, request, effective, netlist)
        for plan in (expected, actual):
            plan['block_arrangement']['search'].pop('elapsed_s')
        self.assertEqual(actual, expected)
        self.assertEqual(actual['status'], 'planned')
        self.assertEqual(actual['policy']['obstacles'], effective['obstacles'])
        self.assertEqual(netlist.read_bytes(), original)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0]['runtime']['implementation'], 'PyPy')


if __name__ == '__main__':
    unittest.main()

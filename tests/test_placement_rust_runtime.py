"""The Rust runtime owns the whole planner and never falls back to Python."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from agent_ee import placement_rust_runtime, placement_runtime, placer_runner
from test_placer_search import promotion_case


class RustRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get('AGENT_EE_RUST_PLANNER')
        if configured is None:
            if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
                raise AssertionError('set AGENT_EE_RUST_PLANNER to the built standalone planner')
            raise unittest.SkipTest('set AGENT_EE_RUST_PLANNER to exercise the standalone planner')
        cls.binary = Path(configured).resolve(strict=True)

    def test_complete_search_has_no_caller_python_planning(self):
        facts, policy = promotion_case()
        expected_heartbeat, actual_heartbeat = [], []
        expected = placer_runner._plan_local(facts, {'policy': policy}, heartbeat=expected_heartbeat.append)
        with placement_rust_runtime.experimental(self.binary) as session:
            with mock.patch.object(placer_runner, '_plan_local', side_effect=AssertionError('Python planner called')), \
                    mock.patch.object(placer_runner, '_heartbeat', actual_heartbeat.append):
                with mock.patch('agent_ee.placer._wire_cost', side_effect=AssertionError('Python scorer called')):
                    actual = placer_runner._plan(facts, {'policy': policy})
        self.assertEqual(actual, expected)
        self.assertIsNone(placement_runtime.active())
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0]['status'], 'ok')
        self.assertEqual(session.calls[0]['exit_code'], 0)
        self.assertEqual(len(session.calls[0]['executable_sha256']), 64)
        self.assertGreaterEqual(len(expected_heartbeat), 2)
        self.assertEqual(actual_heartbeat, expected_heartbeat)
        self.assertIsNone(session.calls[0]['error'])

    def test_default_executable_protocol_stays_one_json_result(self):
        facts, policy = promotion_case()
        payload = {'schema': 'agent-ee.rust-planning-input/1', 'facts': facts,
                   'request': {'policy': policy}, 'policy': None, 'netlist_xml': None}
        process = subprocess.run([str(self.binary)], input=json.dumps(payload).encode(),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        self.assertEqual(len(process.stdout.splitlines()), 1)
        response = json.loads(process.stdout)
        self.assertEqual(response['plan'], placer_runner._plan_local(facts, {'policy': policy},
                                                                    heartbeat=lambda _: None))
        self.assertNotIn('kind', response)

    def test_refusal_is_returned_without_replanning(self):
        facts, policy = promotion_case()
        policy['step_budget'] = 1
        expected = placer_runner._plan_local(facts, {'policy': policy}, heartbeat=lambda _: None)
        with placement_rust_runtime.experimental(self.binary):
            with mock.patch.object(placer_runner, '_plan_local', side_effect=AssertionError('Python fallback')):
                actual = placer_runner._plan(facts, {'policy': policy})
        self.assertEqual(actual, expected)
        self.assertNotEqual(actual['status'], 'planned')

    def test_invalid_input_and_nested_runtime_are_visible(self):
        facts, policy = promotion_case()
        policy['gap_nm'] = -1
        with placement_rust_runtime.experimental(self.binary) as session:
            with mock.patch.object(placer_runner, '_plan_local', side_effect=AssertionError('Python fallback')):
                with self.assertRaisesRegex(ValueError, 'gap'):
                    placer_runner._plan(facts, {'policy': policy})
                with self.assertRaisesRegex(ValueError, 'already active'):
                    with placement_rust_runtime.experimental(self.binary):
                        self.fail('nested runtime admitted')
        self.assertEqual(session.calls[0]['status'], 'error')
        self.assertNotEqual(session.calls[0]['exit_code'], 0)
        self.assertEqual(session.calls[0]['error']['type'], 'ValueError')
        self.assertIsNone(placement_runtime.active())

    def test_request_validation_messages_match_original(self):
        facts, policy = promotion_case()
        requests = [
            {'policy': policy, 'targeted': {}, 'blocks': {}},
            {'policy': policy, 'blocks': {}},
            {'policy': {**policy, 'min_courtyard_edge_mm': 1}, 'min_courtyard_edge_mm': 2},
        ]
        for request in requests:
            with self.subTest(request=request):
                with self.assertRaises(ValueError) as expected:
                    placer_runner._plan_local(facts, request, heartbeat=lambda _: None)
                with placement_rust_runtime.experimental(self.binary):
                    with self.assertRaises(ValueError) as actual:
                        placer_runner._plan(facts, request)
                self.assertEqual(str(actual.exception), str(expected.exception))

    def test_effective_policy_override_and_numeric_margin_equality(self):
        facts, policy = promotion_case()
        for request, effective in [
                ({'policy': {'min_courtyard_edge_mm': 1}, 'min_courtyard_edge_mm': 2}, policy),
                ({'policy': {**policy, 'min_courtyard_edge_mm': 0}, 'min_courtyard_edge_mm': 0.0}, None)]:
            with self.subTest(request=request):
                original = copy.deepcopy((facts, request, effective))
                expected = placer_runner._plan_local(facts, request, effective, heartbeat=lambda _: None)
                with placement_rust_runtime.experimental(self.binary):
                    actual = placer_runner._plan(facts, request, effective)
                self.assertEqual(actual, expected)
                self.assertEqual((facts, request, effective), original)


class RustTransportTest(unittest.TestCase):
    def test_callback_failure_and_malformed_records_reap_owned_child_and_retain_stderr(self):
        heartbeat = json.dumps({'schema': placement_rust_runtime._SCHEMA, 'kind': 'heartbeat',
                                'record': {'status': 'packing', 'steps_used': 17}})
        for output, callback, exception in [
                (heartbeat, mock.Mock(side_effect=KeyboardInterrupt('interrupted')), KeyboardInterrupt),
                (heartbeat, mock.Mock(side_effect=ValueError('callback failed')), ValueError),
                ('not json', mock.Mock(), placement_runtime.PlanningProcessError),
                ('[]', mock.Mock(), placement_runtime.PlanningProcessError)]:
            with self.subTest(output=output, exception=exception):
                command = [sys.executable, '-c',
                           'import sys,time; sys.stdin.buffer.read(); '
                           'sys.stderr.write("child diagnostics\\n"); sys.stderr.flush(); '
                           'print(' + repr(output) + ', flush=True); time.sleep(30)']
                spawned, launch = [], subprocess.Popen

                def capture(*args, **kwargs):
                    process = launch(*args, **kwargs)
                    self.assertGreater(process.pid, 1)  # Only a real child owned by this test is signalled.
                    spawned.append(process)
                    return process

                record = {}
                with mock.patch.object(placement_rust_runtime.subprocess, 'Popen', side_effect=capture):
                    with self.assertRaises(exception):
                        placement_rust_runtime._exchange(command, b'{}', callback, record)
                self.assertEqual(len(spawned), 1)
                self.assertLess(spawned[0].returncode, 0)
                self.assertEqual(record['exit_code'], spawned[0].returncode)
                self.assertEqual(record['stderr'], 'child diagnostics\n')
                self.assertTrue(spawned[0].stdin.closed)
                self.assertTrue(spawned[0].stdout.closed)

    def test_nonzero_exit_without_result_is_process_error_with_diagnostics(self):
        command = [sys.executable, '-c',
                   'import sys; sys.stdin.buffer.read(); sys.stderr.write("broken"); sys.exit(7)']
        record = {}
        with self.assertRaisesRegex(placement_runtime.PlanningProcessError, 'exited 7 without a result: broken'):
            placement_rust_runtime._exchange(command, b'{}', self.fail, record)
        self.assertEqual(record['exit_code'], 7)
        self.assertEqual(record['stderr'], 'broken')

    def test_result_followed_by_another_record_is_rejected(self):
        row = json.dumps({'schema': placement_rust_runtime._SCHEMA, 'plan': {}})
        command = [sys.executable, '-c',
                   'import sys; sys.stdin.buffer.read(); print(' + repr(row + '\n' + row) + ')']
        with self.assertRaisesRegex(placement_runtime.PlanningProcessError, 'unexpected.*protocol'):
            placement_rust_runtime._exchange(command, b'{}', self.fail, {})

"""The Rust runtime owns the whole planner and never falls back to Python."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agent_ee import placement_rust_runtime, placement_runtime, placer_runner
from test_placer_search import promotion_case


class RustRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.binary = placement_rust_runtime.resolve_executable()
        except placement_runtime.PlanningProcessError:
            if (os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1'
                    or 'AGENT_EE_RUST_PLANNER' in os.environ):
                raise
            raise unittest.SkipTest('install the standalone Rust planner or set AGENT_EE_RUST_PLANNER')

    def test_default_dispatch_runs_rust_without_a_reference_fallback(self):
        facts, policy = promotion_case()
        expected = placer_runner._plan_local(facts, {'policy': policy}, heartbeat=lambda _: None)
        execution = {}
        with mock.patch.dict(os.environ, {'AGENT_EE_RUST_PLANNER': str(self.binary)}), \
                mock.patch.object(placer_runner, '_plan_local', side_effect=AssertionError('Python planner called')):
            actual = placer_runner._plan(facts, {'policy': policy}, execution=execution)
        self.assertEqual(actual, expected)
        self.assertEqual((execution['implementation'], execution['status']), ('rust', 'ok'))
        self.assertEqual(len(execution['executable_sha256']), 64)
        self.assertNotIn('implementation', actual)

    def test_reference_context_is_explicit_and_restored(self):
        facts, policy = promotion_case()
        with placement_runtime.python_reference(), \
                mock.patch.object(placement_rust_runtime.native_run.subprocess, 'Popen', side_effect=AssertionError('Rust called')):
            self.assertEqual(placer_runner._plan(facts, {'policy': policy}),
                             placer_runner._plan_local(facts, {'policy': policy}))
            with self.assertRaisesRegex(ValueError, 'already active'):
                with placement_runtime.python_reference():
                    self.fail('nested reference admitted')
        self.assertIsNone(placement_runtime.active())

    def test_complete_search_has_no_caller_python_planning(self):
        facts, policy = promotion_case()
        expected_heartbeat, actual_heartbeat = [], []
        expected = placer_runner._plan_local(facts, {'policy': policy}, heartbeat=expected_heartbeat.append)
        with placement_rust_runtime.using(self.binary) as session:
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
        with placement_rust_runtime.using(self.binary):
            with mock.patch.object(placer_runner, '_plan_local', side_effect=AssertionError('Python fallback')):
                actual = placer_runner._plan(facts, {'policy': policy})
        self.assertEqual(actual, expected)
        self.assertNotEqual(actual['status'], 'planned')

    def test_invalid_input_and_nested_runtime_are_visible(self):
        facts, policy = promotion_case()
        policy['gap_nm'] = -1
        with placement_rust_runtime.using(self.binary) as session:
            with mock.patch.object(placer_runner, '_plan_local', side_effect=AssertionError('Python fallback')):
                with self.assertRaisesRegex(ValueError, 'gap'):
                    placer_runner._plan(facts, {'policy': policy})
                with self.assertRaisesRegex(ValueError, 'already active'):
                    with placement_rust_runtime.using(self.binary):
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
                with placement_rust_runtime.using(self.binary):
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
                with placement_rust_runtime.using(self.binary):
                    actual = placer_runner._plan(facts, request, effective)
                self.assertEqual(actual, expected)
                self.assertEqual((facts, request, effective), original)


class RustTransportTest(unittest.TestCase):
    def test_stale_build_receipt_fails_before_starting_child(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'planner'
            binary.write_bytes(b'changed planner')
            binary.with_name('planner.json').write_text(json.dumps({
                'schema': 'agent-ee.placement-engine-install/1',
                'binary_sha256': hashlib.sha256(b'original planner').hexdigest()}))
            with mock.patch.object(placement_rust_runtime.native_run.subprocess, 'Popen', side_effect=AssertionError('child started')):
                with self.assertRaisesRegex(placement_runtime.PlanningProcessError, 'differs from its build receipt'):
                    placement_rust_runtime.Session(binary).plan({}, {}, None, None, heartbeat=self.fail)

    def test_bad_explicit_binary_never_uses_installed_binary_or_python(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / 'missing')
            with mock.patch.dict(os.environ, {'AGENT_EE_RUST_PLANNER': missing}), \
                    mock.patch.object(placement_rust_runtime.shutil, 'which', side_effect=AssertionError('PATH fallback')), \
                    mock.patch.object(placer_runner, '_plan_local', side_effect=AssertionError('Python fallback')):
                with self.assertRaisesRegex(placement_runtime.PlanningProcessError, 'not executable'):
                    placer_runner._plan({}, {})

    def test_installed_binary_is_resolved_with_cleared_environment(self):
        # The production Bubblewrap sandbox mounts /usr and clears caller env.
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(placement_rust_runtime, '_INSTALLED', Path(sys.executable)), \
                mock.patch.object(placement_rust_runtime.shutil, 'which', side_effect=AssertionError('PATH searched')):
            self.assertEqual(placement_rust_runtime.resolve_executable(), Path(sys.executable).resolve())

    def test_json_validation_rejects_lossy_input_before_starting_child(self):
        for payload, reason in [({'tuple': (1, 2)}, 'JSON-native values'), ({1: 2}, 'keys must be strings')]:
            with self.subTest(payload=payload):
                with mock.patch.object(placement_rust_runtime.native_run.subprocess, 'Popen', side_effect=AssertionError('child started')):
                    with self.assertRaisesRegex(ValueError, reason):
                        placement_rust_runtime.Session(sys.executable).plan(payload, {}, None, None, heartbeat=self.fail)

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
                spawned, input_streams, launch = [], [], subprocess.Popen

                def capture(*args, **kwargs):
                    process = launch(*args, **kwargs)
                    self.assertGreater(process.pid, 1)  # Only a real child owned by this test is signalled.
                    spawned.append(process)
                    input_streams.append(kwargs['stdin'])
                    return process

                record = {}
                with mock.patch.object(placement_rust_runtime.native_run.subprocess, 'Popen', side_effect=capture):
                    with self.assertRaises(exception):
                        placement_rust_runtime._exchange(command, b'{}', callback, record)
                self.assertEqual(len(spawned), 1)
                self.assertLess(spawned[0].returncode, 0)
                self.assertEqual(record['exit_code'], spawned[0].returncode)
                self.assertEqual(record['stderr'], 'child diagnostics\n')
                self.assertTrue(input_streams[0].closed)
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

"""Launch refusal tests count dispatches below the production guard."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.reviewers import reviewer_call
from agent_ee.storage import read_json


class PreflightTests(unittest.TestCase):
    def test_missing_context_never_dispatches(self):
        with tempfile.TemporaryDirectory() as temp, patch('agent_ee.harnesses.native_call') as provider:
            record, value = reviewer_call(Path(temp), 'circuit', {'harness': 'codex'}, '', {}, 10)
            self.assertEqual(record['execution'], 'not_run')
            self.assertIn('missing_context', record['error'])
            self.assertIsNone(value)
            provider.assert_not_called()
            self.assertEqual(read_json(Path(temp) / 'review-work/circuit/preflight/receipt.json')['result'], 'refused')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        (self.run / 'inputs').mkdir()
        from agent_ee.cli import policy_files
        from agent_ee.preflight import capture_expectation
        from agent_ee.storage import digest
        self.binary = self.run / 'binary'
        self.binary.write_bytes(b'\x7fELFtest-only identity; dispatch is substituted')
        self.runtime = {name: {'path': str(self.binary), 'sha256': digest(self.binary)}
                        for name in ('codex', 'codex_tool_host', 'claude', 'bwrap', '/usr/bin/python3')}
        self.definition = {'harness': 'codex', 'model': 'test', 'effort': 'high', 'timeout_s': 10,
                           'tool_mode': 'investigate'}
        self.expected = capture_expectation({'run_id': 'run', 'policy_files': policy_files(),
            'runtime_files': self.runtime}, {'candidate_id': 'candidate', 'scope': {'schema': 'agent-ee.scope/1'}},
            [self.definition])
        self.context = {'expected': self.expected, 'boundary': 'initial', 'deadline': time.monotonic() + 20}
        self.runtime_patch = patch('agent_ee.cli.runtime_files', side_effect=lambda **kwargs: self.runtime)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)

    def call(self, role='circuit', context=None):
        return reviewer_call(self.run, role, self.definition, '', {}, 10,
                             launch_context=self.context if context is None else context)

    def assert_refused(self, reason):
        with patch('agent_ee.reviewers.dispatch') as provider:
            record, value = self.call()
        provider.assert_not_called()
        self.assertEqual(record['execution'], 'not_run')
        self.assertIn(reason, record['error'])
        self.assertIsNone(value)
        return record

    def test_runtime_and_policy_drift_refuse_before_probe_or_dispatch(self):
        with patch('agent_ee.preflight.execute') as probe:
            self.runtime = {**self.runtime, 'changed': {}}
            self.assert_refused('runtime_drift')
            probe.assert_not_called()
        with patch('agent_ee.cli.policy_files', return_value={'changed': 'hash'}), \
             patch('agent_ee.reviewers.dispatch') as provider:
            record, _ = self.call('physical')
            self.assertIn('policy_drift', record['error'])
            provider.assert_not_called()

    def test_selected_missing_binary_is_specific_refusal(self):
        self.runtime = {**self.runtime, 'codex': {'path': None, 'sha256': None}}
        self.assert_refused('missing_binary')

    def test_malformed_expectation_and_definition_change_refuse(self):
        self.expected['identity'] = 'bad'
        self.assert_refused('malformed_expectation')

    def test_changed_declared_producer_refuses(self):
        from agent_ee.storage import identity
        from agent_ee.storage import digest
        producer = self.run / 'producer'
        producer.write_bytes(b'original declared producer')
        self.expected['declared_tools'] = {str(producer): digest(producer)}
        self.expected['identity'] = identity({k: v for k, v in self.expected.items() if k != 'identity'})
        producer.write_bytes(b'replaced declared producer')
        self.assert_refused('declared_tool_drift')

    def test_broken_and_malformed_probe_never_dispatch(self):
        with patch('agent_ee.preflight.execute', return_value={'execution': 'ok'}):
            self.assert_refused('malformed_probe_receipt')

    def test_native_networkless_probe_and_tamper_detection(self):
        from toolchain import require_toolchain
        from agent_ee.preflight import verify_receipt
        from agent_ee.storage import write_json
        require_toolchain()
        with patch('agent_ee.reviewers.dispatch', return_value=({'execution': 'ok'}, {})) as provider:
            record, _ = self.call()
        provider.assert_called_once()
        self.assertEqual(provider.call_args.kwargs['executable'], str(self.binary))
        self.assertLess(provider.call_args.args[5], 10)
        verify_receipt(self.run, record, self.expected, 'circuit', 'initial')
        path = self.run / record['preflight']['path']
        self.assertIn('--unshare-net', read_json(path)['probe']['sandbox_argv'])
        write_json(path, {'schema': 'invalid'})
        with self.assertRaisesRegex(ValueError, 'changed'):
            verify_receipt(self.run, record, self.expected, 'circuit', 'initial')

    def test_duplicate_receipt_cannot_authorize_another_call(self):
        from agent_ee.storage import write_json
        write_json(self.run / 'review-work/circuit/preflight/receipt.json', {'schema': 'forged'})
        self.assert_refused('duplicate_receipt')
        self.assertEqual(read_json(self.run / 'review-work/circuit/preflight/receipt.json'), {'schema': 'forged'})

    def test_adjudicator_and_implementation_drift_each_block_dispatch(self):
        self.runtime = {**self.runtime, 'changed': {}}
        for boundary in ('adjudicator', 'implementation'):
            with self.subTest(boundary=boundary), patch('agent_ee.reviewers.dispatch') as provider:
                record, _ = self.call(boundary, {**self.context, 'boundary': boundary})
                self.assertIn('runtime_drift', record['error'])
                provider.assert_not_called()

    def test_deadline_and_owner_cancellation_refuse(self):
        from agent_ee.executor import Cancellation
        with patch('agent_ee.reviewers.dispatch') as provider:
            record, _ = self.call(context={**self.context, 'deadline': time.monotonic() - 1})
            self.assertIn('deadline_exhausted', record['error'])
            cancel = Cancellation()
            cancel.cancel()
            record, _ = reviewer_call(self.run, 'other', self.definition, '', {}, 10,
                                      launch_context=self.context, cancellation=cancel)
            self.assertIn('cancelled', record['error'])
            provider.assert_not_called()

    def test_actual_invalid_bwrap_option_refuses(self):
        from agent_ee.executor import sandbox_argv
        def invalid(*args, **kwargs):
            return ['bwrap', '--agent-ee-invalid-option', *sandbox_argv(*args, **kwargs)[1:]]
        with patch('agent_ee.executor.sandbox_argv', side_effect=invalid):
            self.assert_refused('sandbox_invalid_option')

    def test_claude_override_checked_path_is_the_mounted_path(self):
        import json
        import os
        from toolchain import require_toolchain
        from agent_ee.storage import identity
        from agent_ee.harnesses import executable_for
        require_toolchain()
        self.definition = {**self.definition, 'harness': 'claude'}
        self.expected['definitions'] = [self.definition]
        self.expected['identity'] = identity({k: v for k, v in self.expected.items() if k != 'identity'})
        home = self.run / 'credential-source'
        (home / '.claude').mkdir(parents=True)
        (home / '.claude/.credentials.json').write_text('scripted credential; no network')
        def provider(inputs, work, argv, **kwargs):
            self.assertEqual(kwargs['extra_ro'][0], (str(self.binary), '/reviewer'))
            (work / 'stdout.log').write_text(json.dumps({'subtype': 'success', 'structured_output': {}}))
            return {'execution': 'ok'}
        with patch.dict(os.environ, {'AGENT_EE_CLAUDE_BINARY': str(self.binary)}), \
             patch('agent_ee.reviewers.Path.home', return_value=home), \
             patch('agent_ee.reviewers.execute', side_effect=provider) as calls:
            self.assertEqual(executable_for('claude'), str(self.binary))
            # No tools makes the scripted terminal JSON sufficient for this adapter test.
            self.definition['tool_mode'] = 'none'
            self.expected['identity'] = identity({k: v for k, v in self.expected.items() if k != 'identity'})
            record, _ = self.call()
        self.assertEqual(record['execution'], 'ok')
        calls.assert_called_once()

    def test_foreign_baseline_receipt_is_rejected_even_with_updated_link_hash(self):
        from toolchain import require_toolchain
        from agent_ee.preflight import verify_receipt
        from agent_ee.storage import digest, write_json
        require_toolchain()
        with patch('agent_ee.reviewers.dispatch', return_value=({'execution': 'ok'}, {})):
            record, _ = self.call()
        path = self.run / record['preflight']['path']
        receipt = read_json(path)
        receipt['baseline_id'] = 'foreign-baseline'
        write_json(path, receipt)
        record['preflight']['sha256'] = digest(path)
        with self.assertRaisesRegex(ValueError, 'foreign'):
            verify_receipt(self.run, record, self.expected, 'circuit', 'initial')

    def test_replaced_captured_binary_has_zero_provider_invocations(self):
        from agent_ee.storage import digest
        self.binary.write_bytes(b'\x7fELFreplaced after capture')
        def current(**kwargs):
            return {name: {**entry, 'sha256': digest(entry['path'])} for name, entry in self.runtime.items()}
        with patch('agent_ee.cli.runtime_files', side_effect=current):
            self.assert_refused('runtime_drift')

    def test_required_native_check_binary_missing_at_capture_refuses_inference(self):
        from agent_ee.preflight import capture_expectation
        from agent_ee.cli import policy_files
        self.runtime = {**self.runtime, 'pcbnew': {'path': None, 'sha256': None}}
        self.expected = capture_expectation({'run_id': 'run', 'policy_files': policy_files(),
            'runtime_files': self.runtime}, {'candidate_id': 'candidate', 'scope': {
                'schema': 'agent-ee.scope/1', 'checks': [{'id': 'width', 'kind': 'geometry'}]}}, [self.definition])
        self.context['expected'] = self.expected
        with patch('agent_ee.reviewers.dispatch', return_value=({'execution': 'ok'}, {})) as provider:
            record, _ = self.call()
        provider.assert_not_called()
        self.assertIn('missing_binary:pcbnew', record['error'])

    def test_simulated_namespace_denial_is_specific_and_no_provider_runs(self):
        def denied(inputs, work, argv, **kwargs):
            work.mkdir(parents=True)
            (work / 'stderr.log').write_text('bwrap: Creating new namespace failed: Operation not permitted')
            return {'execution': 'failed', 'exit_code': 1, 'argv': argv, 'sandbox_argv': ['bwrap', '--unshare-net']}
        with patch('agent_ee.preflight.execute', side_effect=denied):
            self.assert_refused('sandbox_namespace_denied')

    def test_missing_receipt_link_is_not_admission_evidence(self):
        from agent_ee.preflight import verify_receipt
        with self.assertRaisesRegex(ValueError, 'missing or foreign'):
            verify_receipt(self.run, {'execution': 'ok'}, self.expected, 'circuit', 'initial')

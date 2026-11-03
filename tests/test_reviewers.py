"""Standalone reviewer seats must gate only the runtime they actually execute."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.reviewers import reviewer_call
from agent_ee.storage import read_json


class StandaloneSeatTests(unittest.TestCase):
    def test_scripted_seat_needs_no_native_runtime(self):
        definition = dict(harness='claude', model='scripted', effort='low')
        answer = ({'execution': 'ok'}, {'answer': 'scripted'})
        for label in ('shortlist-libraries', 'extraction-a'):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp, \
                    patch('agent_ee.reviewers.dispatch', return_value=answer), \
                    patch('agent_ee.run_record.runtime_files', side_effect=AssertionError('native runtime inspected')):
                self.assertEqual(reviewer_call(temp, label, definition, '', {}, 10), answer)
                self.assertEqual(list(Path(temp).iterdir()), [])

    def test_native_seat_still_refuses_missing_selected_binary(self):
        for harness in ('claude', 'codex', 'agy'):
            for label in ('shortlist-libraries', 'extraction-a'):
                with self.subTest(harness=harness, label=label), tempfile.TemporaryDirectory() as temp, \
                        patch('agent_ee.native_run.run', side_effect=AssertionError('native process launched')):
                    definition = dict(harness=harness, model='test', effort='low')
                    runtime = {harness: {'path': None, 'sha256': None}}
                    record, value = reviewer_call(temp, label, definition, '', {}, 10,
                                                 launch_context={'host': {'runtime_files': runtime}})
                    self.assertEqual(record['execution'], 'not_run')
                    self.assertIn('missing_binary:' + harness, record['error'])
                    self.assertIsNone(value)
                    receipt = read_json(Path(temp) / record['preflight']['path'])
                    self.assertEqual(receipt['result'], 'refused')

    def test_wire_limit_applies_before_scripted_seat(self):
        schema = {'type': 'string', 'enum': [str(n) for n in range(1001)]}
        with tempfile.TemporaryDirectory() as temp, \
                patch('agent_ee.reviewers.dispatch', side_effect=AssertionError('oversized schema dispatched')):
            with self.assertRaisesRegex(ValueError, 'exceeds 1000 enum values'):
                reviewer_call(temp, 'shortlist-libraries', {'harness': 'claude'}, '', schema, 10)

    def test_production_call_without_context_refuses_missing_selected_binary(self):
        for harness in ('claude', 'codex', 'agy'):
            for label in ('shortlist-libraries', 'extraction-a'):
                runtime = {harness: {'path': None, 'sha256': None}}
                with self.subTest(harness=harness, label=label), tempfile.TemporaryDirectory() as temp, \
                        patch('agent_ee.run_record._runtime_files', return_value=runtime), \
                        patch('agent_ee.native_run.run', side_effect=AssertionError('native process launched')), \
                        patch('agent_ee.reviewers.claude_call', side_effect=AssertionError('Claude seat launched')), \
                        patch('agent_ee.harnesses.native_call', side_effect=AssertionError('model seat launched')):
                    definition = dict(harness=harness, model='test', effort='low')
                    record, value = reviewer_call(temp, label, definition, '', {}, 10)
                    self.assertEqual(record['execution'], 'not_run')
                    self.assertIn('missing_binary:' + harness, record['error'])
                    self.assertIsNone(value)
                    receipt = read_json(Path(temp) / record['preflight']['path'])
                    self.assertEqual(receipt['result'], 'refused')

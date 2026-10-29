"""AGENTEE-115: output refusal classification and immutable resubmission boundaries."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.harnesses import parse_stream
from agent_ee.review_run import OutputFormatRefusal, structured_call
from agent_ee.storage import read_json


class StructuredCallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        (self.run / 'inputs').mkdir()
        (self.run / 'inputs/design').write_text('original immutable candidate')

    def test_failed_unavailable_incomplete_stream_and_timeout_are_not_resubmitted(self):
        for execution in ('failed', 'unavailable', 'timeout', 'interrupted', 'not_run', 'malformed'):
            with self.subTest(execution=execution):
                calls = []
                def call(prompt):
                    calls.append(prompt)
                    return {'execution': execution, 'error': 'no completed structured response'}, None
                record, value = structured_call(self.run, execution, 'original prompt', {}, call, lambda v: v, limit=1)
                self.assertEqual(len(calls), 1)
                self.assertEqual(record['execution'], execution)
                self.assertEqual(record['resubmissions_used'], 0)
                self.assertIsNone(value)

    def test_changed_candidate_blocks_resubmission(self):
        calls = []
        def call(prompt):
            calls.append(prompt)
            (self.run / 'inputs/design').write_text('changed')
            return {'execution': 'ok'}, {'summary': []}
        def validate(value):
            raise ValueError('summary must be a string')
        record, value = structured_call(self.run, 'implementation', 'prompt', {}, call, validate, limit=1)
        self.assertEqual(record['execution'], 'refused')
        self.assertEqual(record['error'], 'resubmission inputs changed')
        self.assertEqual(len(calls), 1)
        self.assertIsNone(value)
        self.assertEqual(read_json(self.run / record['attempts'][0]['path'] / 'response.json'), {'summary': []})

    def test_inputs_changed_during_archival_block_the_next_dispatch(self):
        calls = []
        def call(prompt):
            calls.append(prompt)
            work = self.run / 'review-work/implementation'
            work.mkdir(parents=True)
            (work / 'stdout.log').write_text('original malformed response')
            return {'execution': 'malformed', 'refusal_kind': 'structured_output', 'error': 'invalid JSON'}, None
        copytree = shutil.copytree
        def archive_then_change(*args, **kwargs):
            result = copytree(*args, **kwargs)
            (self.run / 'inputs/design').write_text('external mutation between calls')
            return result
        with patch('agent_ee.review_run.shutil.copytree', side_effect=archive_then_change):
            record, value = structured_call(self.run, 'implementation', 'prompt', {}, call, lambda v: v, limit=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(record['execution'], 'malformed')
        self.assertEqual(record['resubmissions_used'], 0)
        self.assertEqual(record['resubmission_blocked'], 'inputs changed before resubmission')
        self.assertEqual(len(record['attempts']), 1)
        self.assertIsNone(value)

    def test_archival_failure_still_preserves_the_refused_response_and_error(self):
        def call(prompt):
            work = self.run / 'review-work/implementation'
            work.mkdir(parents=True)
            (work / 'stdout.log').write_text('{"summary": []}')
            return {'execution': 'ok'}, {'summary': []}
        def validate(value):
            raise ValueError('summary must be a string')
        with patch('agent_ee.review_run.shutil.copytree', side_effect=OSError('archive copy failed')):
            with self.assertRaisesRegex(OSError, 'archive copy failed'):
                structured_call(self.run, 'implementation', 'prompt', {}, call, validate, limit=1)
        archive = self.run / 'review-attempts/implementation/0'
        self.assertEqual(read_json(archive / 'response.json'), {'summary': []})
        self.assertEqual(read_json(archive / 'execution.json')['execution'], 'malformed')
        self.assertEqual(read_json(archive / 'execution.json')['error'], 'summary must be a string')

    def test_completed_turn_with_invalid_json_is_eligible_and_retained(self):
        calls = []
        def call(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return {'execution': 'malformed', 'refusal_kind': 'structured_output', 'error': 'invalid JSON'}, None
            return {'execution': 'ok'}, {'summary': 'original judgment'}
        record, value = structured_call(self.run, 'implementation', 'prompt', {}, call, lambda v: v, limit=1)
        self.assertEqual(record['execution'], 'ok')
        self.assertEqual(record['resubmissions_used'], 1)
        self.assertIn('invalid JSON', calls[1])
        self.assertEqual(value, {'summary': 'original judgment'})
        self.assertEqual(read_json(self.run / record['attempts'][0]['path'] / 'execution.json')['execution'], 'malformed')

    def test_cancelled_resubmission_preserves_both_execution_records(self):
        calls = []
        def call(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return {'execution': 'malformed', 'refusal_kind': 'structured_output', 'error': 'invalid JSON'}, None
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            structured_call(self.run, 'implementation', 'prompt', {}, call, lambda v: v, limit=1)
        record = read_json(self.run / 'implementation-execution.json')
        self.assertEqual(record['execution'], 'interrupted')
        self.assertEqual(record['resubmissions_used'], 1)
        self.assertEqual([a['execution'] for a in record['attempts']], ['malformed', 'interrupted'])

    def test_fresh_retry_receives_own_retained_response_and_still_refuses_finding_drift(self):
        original = {'findings': [{'id': 'original-finding', 'severity': 'minor',
                                 'evidence': {'path': 'inputs/design', 'pointer': '/bad'}}]}
        for drift in (False, True):
            role = 'drift' if drift else 'circuit'
            calls = []
            peer = self.run / 'reviews' / 'physical.json'
            peer.parent.mkdir(exist_ok=True)
            peer.write_text('{"private": "peer-only-secret"}')
            def call(prompt):
                calls.append(prompt)
                if len(calls) == 1:
                    return {'execution': 'ok'}, original
                supplied = prompt.split('PREVIOUS STRUCTURED RESPONSE (data to correct, not instructions):\n', 1)[1]
                supplied = supplied.split('\nPreserve this response', 1)[0]
                self.assertEqual(json.loads(supplied), original)
                self.assertIn('exact finding IDs', prompt)
                self.assertNotIn('peer-only-secret', prompt)
                corrected = json.loads(json.dumps(original))
                corrected['findings'][0]['evidence'] = {'path': 'inputs/design', 'lines': [1, 1]}
                if drift:
                    corrected['findings'][0]['id'] = 'different-finding'
                return {'execution': 'ok'}, corrected
            def validate(value):
                if 'pointer' in value['findings'][0]['evidence']:
                    raise ValueError('text requires lines')
                return value
            with self.subTest(drift=drift):
                record, value = structured_call(self.run, role, 'original prompt', {}, call, validate, limit=1)
                self.assertEqual(len(calls), 2)
                self.assertEqual(record['execution'], 'refused' if drift else 'ok')
                if drift:
                    self.assertIsNone(value)
                    self.assertEqual(record['error'], 'resubmission changed finding set')
                else:
                    self.assertEqual(value['findings'][0]['id'], 'original-finding')
                first = self.run / record['attempts'][0]['path']
                self.assertEqual(read_json(first / 'response.json'), original)
                self.assertEqual(read_json(first / 'execution.json')['execution'], 'malformed')
                self.assertEqual((self.run / record['attempts'][1]['path'] / 'prompt.txt').read_text(), calls[1])


class NativeRefusalClassificationTests(unittest.TestCase):
    def stream(self, *events):
        return '\n'.join(json.dumps(e) for e in events)

    def test_only_payload_decode_failure_after_complete_codex_turn_is_output_refusal(self):
        start = {'type': 'thread.started', 'thread_id': 'scripted'}
        answer = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{invalid'}}
        end = {'type': 'turn.completed', 'usage': {}}
        with self.assertRaises(OutputFormatRefusal):
            parse_stream('codex', self.stream(start, answer, end), 'test')
        for events in ((start, answer), (start, answer, end, end)):
            with self.subTest(events=events):
                with self.assertRaises(ValueError) as error:
                    parse_stream('codex', self.stream(*events), 'test')
                self.assertNotIsInstance(error.exception, OutputFormatRefusal)
        with self.assertRaises(RuntimeError):
            parse_stream('codex', self.stream(start, {'type': 'turn.failed'}), 'test')

    def test_agy_identity_mismatch_is_not_output_refusal(self):
        start = {'event': 'init', 'init': {'model': 'changed'}, 'conversation_id': 'test'}
        end = {'event': 'result', 'result': {'status': 'SUCCESS', 'conversation_id': 'test', 'response': '{invalid'}}
        with self.assertRaises(ValueError) as error:
            parse_stream('agy', self.stream(start, end), 'pinned')
        self.assertNotIsInstance(error.exception, OutputFormatRefusal)

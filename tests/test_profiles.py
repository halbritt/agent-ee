import unittest
from agent_ee.profiles import validate_profile

class ProfileTests(unittest.TestCase):
    def test_selection_is_explicit_for_every_role(self):
        with self.assertRaisesRegex(ValueError, 'profile'):
            validate_profile({})
        profile = {'schema': 'agent-ee.profile/1', 'name': 'trial',
            'reviewers': [{'role': 'circuit', 'harness': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'high', 'timeout_s': 300},
                          {'role': 'physical', 'harness': 'agy', 'model': 'gemini-4-argon-high', 'effort': 'high', 'timeout_s': 300}],
            'adjudicator': {'harness': 'claude', 'model': 'claude-fable-5', 'effort': 'high', 'timeout_s': 300}}
        self.assertEqual(validate_profile(profile), profile)
        del profile['adjudicator']['model']
        with self.assertRaisesRegex(ValueError, 'model'):
            validate_profile(profile)

class NativeStreamTests(unittest.TestCase):
    def test_agy_uses_structured_output_and_checks_model_identity(self):
        import json
        from agent_ee.harnesses import parse_stream
        events = [{'event': 'init', 'conversation_id': 'c', 'init': {'model': 'gemini-4-argon-high'}},
            {'event': 'result', 'result': {'status': 'SUCCESS', 'conversation_id': 'c',
             'response': '{"answer":"wrapped", "toolAction":"finish"}', 'structured_output': {'findings': []}}}]
        value, record = parse_stream('agy', '\n'.join(map(json.dumps, events)), 'gemini-4-argon-high')
        self.assertEqual(value, {'findings': []})
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            parse_stream('agy', '\n'.join(map(json.dumps, events)), 'another-model')

    def test_partial_or_failed_codex_stream_cannot_pass(self):
        import json
        from agent_ee.harnesses import parse_stream
        events = [{'type': 'thread.started', 'thread_id': 'thread'},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"findings":[]}'}}]
        with self.assertRaises(ValueError):
            parse_stream('codex', '\n'.join(map(json.dumps, events)), 'gpt-6.1-sol')
        events += [{'type': 'turn.failed', 'error': 'quota exhausted'}]
        with self.assertRaisesRegex(RuntimeError, 'provider error'):
            parse_stream('codex', '\n'.join(map(json.dumps, events)), 'gpt-6.1-sol')

class ProfileFreshnessTests(unittest.TestCase):
    def test_external_profile_change_invalidates_prior_candidate(self):
        import tempfile
        from pathlib import Path
        from agent_ee.candidate import capture, verify_source
        from agent_ee.storage import write_json
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'design').write_text('native')
            write_json(root/'scope.json', {'schema':'agent-ee.scope/1','inputs':[{'path':'design','at':'design'}]})
            write_json(root/'profile.json', {'name':'first'})
            (root/'run').mkdir()
            capture(root/'scope.json',root/'run',root/'profile.json')
            verify_source(root/'run')
            write_json(root/'profile.json', {'name':'changed'})
            with self.assertRaisesRegex(ValueError, 'profile changed'):
                verify_source(root/'run')

class StructuredOutputTests(unittest.TestCase):
    def test_agy_structured_payload_does_not_depend_on_prose_being_json(self):
        import json
        from agent_ee.harnesses import parse_stream
        events = [{'event':'init','conversation_id':'c','init':{'model':'gemini-4-argon-high'}},
            {'event':'result','result':{'status':'SUCCESS','conversation_id':'c',
              'response':'Review is complete.', 'structured_output':{'findings':[]}}}]
        value, _ = parse_stream('agy','\n'.join(map(json.dumps,events)),'gemini-4-argon-high')
        self.assertEqual(value, {'findings':[]})

class CorrectionIdentityTests(unittest.TestCase):
    def test_switching_only_reviewer_profile_cannot_count_as_design_repair(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from agent_ee.run_record import link_correction
        from agent_ee.storage import write_json
        with tempfile.TemporaryDirectory() as temp:
            old, new = Path(temp)/'old', Path(temp)/'new'
            old.mkdir();new.mkdir()
            manifest={'scope':{'checks':[],'requirements':[]},'files':{'board':'same-bytes'}}
            write_json(old/'candidate.json',manifest);write_json(new/'candidate.json',manifest)
            before={'candidate_id':'old-profile','run_id':'old-run','findings':[{'id':'circuit/flaw','status':'accepted'}]}
            after={'candidate_id':'new-profile','run_id':'new-run','outcome':'criteria_satisfied','checks':[{'id':'native','execution':'ok','outcome':'pass'}]}
            write_json(new/'result.json',after)
            with patch('agent_ee.run_record.verify',side_effect=[before,after]), patch('agent_ee.run_record.save_result'):
                with self.assertRaisesRegex(ValueError,'captured input'):
                    link_correction(old,new,'circuit/flaw',['native'], 'Different reviewer called it acceptable')


class CodexModelEvidenceTests(unittest.TestCase):
    session = '01234567-89ab-4cde-8fab-0123456789ab'

    def call(self, root, rollout):
        import json
        import os
        from unittest.mock import patch
        from agent_ee import native_run
        from agent_ee.harnesses import native_call
        from agent_ee.storage import write_json

        auth = root / 'subscription'
        auth.mkdir()
        write_json(auth / 'auth.json', {'test_credential': 'DO-NOT-RETAIN'})
        stream = [
            {'type': 'thread.started', 'thread_id': self.session},
            {'type': 'item.completed', 'item': {'type': 'agent_message',
                'text': '{"findings": [], "model": "self-report-is-not-evidence"}'}},
            {'type': 'turn.completed', 'usage': {}},
        ]
        files = {} if rollout is None else {
            'home/.codex/sessions/2026/10/27/rollout-test-' + self.session + '.jsonl':
                '\n'.join(json.dumps(row) for row in rollout).encode(),
        }
        def respond(call):
            self.assertNotIn('--ephemeral', call.intent.argv)
            self.assertIn('forced_login_method="chatgpt"', call.intent.argv)
            return native_run.Outcome(stdout='\n'.join(map(json.dumps, stream)).encode(), files=files)

        transport = native_run.Recorded(respond)
        with patch.dict(os.environ, {'AGENT_EE_CODEX_AUTH_HOME': str(auth)}), native_run.using(transport):
            return native_call(root, 'probe', {'harness': 'codex', 'model': 'gpt-6.1-sol',
                'effort': 'high', 'tool_mode': 'read-only'}, 'scripted prompt', {}, 10,
                executable='/usr/bin/true')

    def test_matching_cli_turn_metadata_survives_home_cleanup_without_private_fields(self):
        import tempfile
        from pathlib import Path
        from agent_ee.storage import read_json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, value = self.call(root, [
                {'type': 'session_meta', 'payload': {'id': self.session, 'cli_version': '0.164.0',
                    'model_provider': 'openai', 'base_instructions': 'PRIVATE-INSTRUCTIONS'}},
                {'type': 'turn_context', 'payload': {'model': 'gpt-6.1-sol',
                    'developer_instructions': 'PRIVATE-INSTRUCTIONS'}},
            ])
            self.assertEqual(record['execution'], 'ok')
            self.assertEqual(record['model_observed'], 'gpt-6.1-sol')
            self.assertEqual(value['model'], 'self-report-is-not-evidence')
            evidence = read_json(root / record['model_evidence']['path'])
            self.assertEqual(evidence['harness_session'], self.session)
            self.assertEqual(evidence['model_observed'], 'gpt-6.1-sol')
            self.assertEqual(evidence['cli_version'], '0.164.0')
            self.assertFalse((root / 'review-work/probe/home').exists())
            retained = ''.join(p.read_text() for p in (root / 'review-work').rglob('*') if p.is_file())
            self.assertNotIn('PRIVATE-INSTRUCTIONS', retained)
            self.assertNotIn('DO-NOT-RETAIN', retained)

    def test_incomplete_ambiguous_or_unbound_metadata_keeps_observed_model_unknown(self):
        import tempfile
        from pathlib import Path
        from agent_ee.storage import read_json

        meta = {'type': 'session_meta', 'payload': {'id': self.session,
            'cli_version': '0.164.0', 'model_provider': 'openai'}}
        turn = {'type': 'turn_context', 'payload': {'model': 'gpt-6.1-sol'}}
        cases = {
            'missing': None,
            'no_turn': [meta],
            'wrong_session': [dict(meta, payload=dict(meta['payload'], id='unrelated')), turn],
            'duplicate_session': [meta, meta, turn],
            'conflicting_models': [meta, turn, {'type': 'turn_context', 'payload': {'model': 'other-model'}}],
            'missing_version': [dict(meta, payload={'id': self.session, 'model_provider': 'openai'}), turn],
            'malformed': [meta, {'type': 'turn_context', 'payload': None}],
            'response_model': [meta, {'type': 'response_item', 'payload': {'model': 'gpt-6.1-sol'}}],
        }
        for name, rows in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                record, value = self.call(root, rows)
                self.assertEqual(record['execution'], 'ok')
                self.assertIsNotNone(value)
                self.assertIsNone(record['model_observed'])
                self.assertIn('UNKNOWN', record['identity_limitation'])
                evidence = read_json(root / record['model_evidence']['path'])
                self.assertIsNone(evidence['model_observed'])
                self.assertFalse((root / 'review-work/probe/home').exists())

    def test_observed_model_mismatch_refuses_result_and_retains_identity(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            record, value = self.call(Path(tmp), [
                {'type': 'session_meta', 'payload': {'id': self.session,
                    'cli_version': '0.164.0', 'model_provider': 'openai'}},
                {'type': 'turn_context', 'payload': {'model': 'different-model'}},
            ])
            self.assertEqual(record['execution'], 'failed')
            self.assertEqual(record['model_observed'], 'different-model')
            self.assertIsNone(value)
            self.assertIn('no fallback', record['error'])

    def test_conflicting_provider_metadata_stops_subscription_operation(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            record, value = self.call(Path(tmp), [
                {'type': 'session_meta', 'payload': {'id': self.session,
                    'cli_version': '0.164.0', 'model_provider': 'openrouter'}},
                {'type': 'turn_context', 'payload': {'model': 'gpt-6.1-sol'}},
            ])
            self.assertEqual(record['execution'], 'failed')
            self.assertIsNone(value)
            self.assertIsNone(record['model_observed'])
            self.assertIn('subscription route', record['error'])

    def test_linked_rollout_is_not_read_as_model_metadata(self):
        import tempfile
        from pathlib import Path
        from agent_ee.harnesses import codex_model_identity

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sessions = home / '.codex/sessions'
            sessions.mkdir(parents=True)
            credential = home / 'auth.json'
            credential.write_text('private credential, not session metadata')
            (sessions / ('rollout-test-' + self.session + '.jsonl')).symlink_to(credential)
            evidence = codex_model_identity(home, self.session)
            self.assertIsNone(evidence['model_observed'])
            self.assertIn('Linked', evidence['identity_limitation'])
            self.assertNotIn('private credential', str(evidence))

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

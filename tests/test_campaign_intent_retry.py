"""Real owned schematic producers with scripted writers, never provider launches."""
from pathlib import Path
import unittest
from unittest.mock import patch
import shutil
import time

from tests import test_campaign as campaign_tests
from agent_ee.campaign import verify_campaign
from agent_ee.storage import read_json, write_json


class IntentRetryTests(unittest.TestCase):
    def setUp(self):
        campaign_tests.MutableIntentSchematicCampaignTests.setUp(self)
        self.config['writer_editable'] = [self.intent]
        self.mode = 'repair'
        self.calls = 0
        self.config['max_seconds'] = 180
        self.starting_intents = []
        self.starting_seeds = []

    def agent(self, run, label, *args, **kwargs):
        if label == 'implementation':
            design = Path(run) / 'review-work/implementation/design'
            self.starting_intents.append(read_json(design / self.intent))
            self.starting_seeds.append((design / 'design/generation/block-seed.json').read_bytes())
        response = campaign_tests.MutableIntentSchematicCampaignTests.agent(self, run, label, *args, **kwargs)
        if label == 'implementation':
            self.calls += 1
            path = Path(run) / 'review-work/implementation/design' / self.intent
            intent = read_json(path)
            if self.mode == 'bound':
                number = str(1000 + self.calls)
            else:
                number = '999' if self.calls == 1 or self.mode == 'repeat' else '4'
            intent['no_connects'][0]['pin']['number'] = number
            if self.mode == 'library':
                intent['libraries'][0]['sha256'] = '0' * 64
            if self.mode == 'unauthorized' and self.calls == 2:
                native = Path(run) / 'review-work/implementation/design' / self.board
                native.write_bytes(native.read_bytes() + b'\n')
            write_json(path, intent)
        return response

    campaign = campaign_tests.MutableIntentSchematicCampaignTests.campaign

    def test_pin_resolution_repair_retains_failure_and_replays(self):
        # A preceding producer owns a seed output. Each retry must reset that
        # output before the writer, then rerun the entire declared sequence.
        prefix = {'schema': 'agent-ee.stage-step/1', 'stage': 'schematic', 'step': 'binding-fill',
                  'argv': ['/usr/bin/python3', '-c',
                           'from pathlib import Path; Path("seed.json").write_text("prepared")'],
                  'writes': {'design/generation/block-seed.json': 'seed.json'}, 'timeout_s': 10}
        status, state = self.campaign([prefix, *self.stage['implementation_steps']])
        self.assertEqual((status, state['state'], self.calls), (0, 'criteria_satisfied', 2), state.get('error'))
        root = self.root / 'campaign'
        failed = root / 'intent-retries/1/1'
        self.assertEqual(read_json(failed / 'receipts/2-emit-schematic/receipt.json')['status'], 'failed')
        self.assertFalse((failed / 'receipts/3-forward-annotate').exists())
        feedback = read_json(root / 'implementations/1/work/intent-feedback.json')
        self.assertIn('resolution_unknown_pin', [f['code'] for f in feedback['findings']])
        self.assertIn('resolution_unknown_pin', self.writer_prompts[1])
        self.assertEqual(self.starting_intents[1]['no_connects'][0]['pin']['number'], '999')
        self.assertEqual(self.starting_seeds[0], self.starting_seeds[1])
        self.assertNotEqual(self.starting_seeds[1], b'prepared')
        self.assertEqual(verify_campaign(root)['state'], 'criteria_satisfied')
        # Alter separately anchored custody surfaces; restore each before the next probe.
        for rel in ('intent-retries/1/1/receipts/2-emit-schematic/run/out/emission.json',
                    'intent-retries/1/1/receipts/2-emit-schematic/receipt.json',
                    'intent-retries/1/1/implementation-execution.json',
                    'implementations/1/work/intent-feedback.json',
                    'implementations/1/implementation-prompt.txt'):
            target = root / rel
            before = target.read_bytes()
            with self.subTest(tamper=rel):
                target.write_bytes(before + b'\n')
                # JSON whitespace is immaterial to feedback values, but its exact
                # bytes are also in the structured call's input identity.
                if rel.endswith('intent-feedback.json'):
                    value = read_json(target); value['findings'] = []; write_json(target, value)
                with self.assertRaises(ValueError):
                    verify_campaign(root)
                target.write_bytes(before)
        state_path = root / 'state.json'
        before = state_path.read_bytes()
        changed = read_json(state_path); changed['intent_retries'] = {}; write_json(state_path, changed)
        with self.assertRaisesRegex(ValueError, 'intent retry archive inventory'):
            verify_campaign(root)
        state_path.write_bytes(before)
        self.assertEqual(verify_campaign(root)['state'], 'criteria_satisfied')
        # Re-pin the edited diagnostic, ordinary receipt, index and archive
        # anchor coherently. Pure emitter replay must still refuse the forgery.
        from agent_ee.storage import digest, identity
        from agent_ee.review_run import tree_hashes
        from agent_ee.stage_receipts import bind_implementation
        emission_path = failed / 'receipts/2-emit-schematic/run/out/emission.json'
        emission = read_json(emission_path)
        emission['findings'][0]['message'] = 'forged repair advice'
        write_json(emission_path, emission)
        receipt_path = failed / 'receipts/2-emit-schematic/receipt.json'
        receipt = read_json(receipt_path)
        receipt['artifacts']['out/emission.json'] = digest(emission_path)
        receipt['identity'] = identity({k: v for k, v in receipt.items() if k != 'identity'})
        write_json(receipt_path, receipt)
        index = read_json(failed / 'stage-receipts.json')
        bind_implementation(failed, index['delivered'], 1, index['source_candidate_id'])
        state = read_json(state_path)
        state['intent_retries']['1'][0]['tree_sha256'] = identity(tree_hashes(failed))
        write_json(state_path, state)
        with self.assertRaisesRegex(ValueError, 'diagnostic differs from owned emitter replay'):
            verify_campaign(root)

    def test_repeated_failed_intent_stops_without_third_writer(self):
        self.mode = 'repeat'
        status, state = self.campaign(self.stage['implementation_steps'])
        self.assertEqual((status, state['state'], self.calls), (1, 'incomplete', 2))
        self.assertIn('no progress', state['error'])
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')
        state_path = self.root / 'campaign/state.json'
        before = state_path.read_bytes()
        changed = read_json(state_path)
        changed['state'] = 'repair'
        write_json(state_path, changed)
        with self.assertRaisesRegex(ValueError, 'intent retry failure must be terminal incomplete'):
            verify_campaign(self.root / 'campaign')
        state_path.write_bytes(before)
        from agent_ee.campaign import implement
        with patch('agent_ee.campaign._implement_once') as writer:
            with self.assertRaisesRegex(ValueError, 'deadline exhausted'):
                implement(self.root / 'campaign', Path(state['history'][-1]['run']),
                          self.config['implementation'], state, time.monotonic() - 1)
            writer.assert_not_called()

    def test_distinct_invalid_intents_exhaust_frozen_attempt_bound(self):
        self.mode = 'bound'
        status, state = self.campaign(self.stage['implementation_steps'])
        self.assertEqual((status, state['state'], self.calls), (1, 'incomplete', 3))
        self.assertIn('finding_retry_limit exhausted', state['error'])
        self.assertEqual(len(state['intent_retries']['1']), 2)
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')

    def test_retry_writer_cannot_edit_native_output(self):
        self.mode = 'unauthorized'
        status, state = self.campaign(self.stage['implementation_steps'])
        self.assertEqual((status, state['state'], self.calls), (1, 'incomplete', 2))
        self.assertIn('writer_editable forbids', state['error'])
        self.assertFalse((self.root / 'campaign/implementations/1/receipts').exists())
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')

    def test_nonrecoverable_library_resolution_has_no_retry(self):
        self.mode = 'library'
        status, state = self.campaign(self.stage['implementation_steps'])
        self.assertEqual((status, state['state'], self.calls), (1, 'incomplete', 1))
        self.assertNotIn('intent_retries', state)
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')

    def test_same_named_producer_with_different_command_has_no_retry(self):
        spec = self.stage['implementation_steps'][0]
        spec['argv'] = spec['argv'][:5] + ['-c',
            'import time; from agent_ee.schematic_stage import main; sys.exit(main())'] + spec['argv'][7:]
        status, state = self.campaign(self.stage['implementation_steps'])
        self.assertEqual((status, state['state'], self.calls), (1, 'incomplete', 1))
        self.assertNotIn('intent_retries', state)
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')

    def test_same_command_from_changed_source_has_no_retry(self):
        spec = self.stage['implementation_steps'][0]
        copied = self.root / 'changed-source'
        shutil.copytree(spec['mounts'][0]['source'], copied)
        module = copied / 'agent_ee/schematic_stage.py'
        module.chmod(0o600)
        module.write_text(module.read_text() + '\n# Different source identity.\n')
        spec['mounts'][0]['source'] = str(copied)
        status, state = self.campaign(self.stage['implementation_steps'])
        self.assertEqual((status, state['state'], self.calls), (1, 'incomplete', 1))
        self.assertNotIn('intent_retries', state)
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')

    def test_interrupted_retry_keeps_failed_attempt_replayable(self):
        with patch('agent_ee.intent_retry.seed', side_effect=KeyboardInterrupt):
            status, state = self.campaign(self.stage['implementation_steps'])
        self.assertEqual((status, state['state'], self.calls), (1, 'incomplete', 1))
        self.assertEqual(len(state['intent_retries']['1']), 1)
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')


class LegacyIntentRetryBoundaryTests(unittest.TestCase):
    def test_explicit_null_stages_preserves_legacy_implementation_call(self):
        import tempfile
        from agent_ee.intent_retry import implement
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = root / 'previous'
            write_json(previous / 'candidate.json', {'scope': {'stages': None}})
            write_json(root / 'config.json', {})
            state = {'history': [{}]}
            with patch('agent_ee.campaign._implement_once', return_value='legacy-result') as attempt:
                result = implement(root, previous, {}, state, time.monotonic() + 10, attempt)
            self.assertEqual(result, 'legacy-result')
            attempt.assert_called_once_with(root, previous, {}, state,
                                            unittest.mock.ANY, retry=None)
            self.assertNotIn('intent_retries', state)

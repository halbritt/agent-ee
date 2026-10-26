"""Writer permission is narrower than coordinator mutable output authority."""
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import campaign, stage_receipts
from agent_ee.storage import read_json, write_json, digest
from tests import test_stage_receipts as receipt_fixture
from tests.toolchain import require_toolchain


class WriterAuthorityTests(unittest.TestCase):
    def setUp(self):
        require_toolchain()
        self.lane = receipt_fixture.CampaignStageReceiptTests()
        self.lane.setUp()
        self.addCleanup(self.lane.doCleanups)
        self.root = self.lane.root
        write_json(self.root/'source/intent.json', {'desired': 5})
        scope = read_json(self.root/'scope.json')
        scope['mutable_inputs'] += ['design/schematic', 'design/board', 'design/intent.json']
        write_json(self.root/'scope.json', scope)
        self.lane.config['writer_editable'] = ['design/intent.json']

    def writer(self, rel, text):
        original = self.lane.agent
        def dispatch(run, label, *args, **kwargs):
            result = original(run, label, *args, **kwargs)
            if label == 'implementation':
                (Path(run)/'review-work/implementation/design'/rel).write_text(text)
            return result
        return patch('agent_ee.reviewers.dispatch', side_effect=dispatch)

    def stage(self, name):
        self.lane.staged_scope()
        scope = read_json(self.root/'scope.json')
        scope['mutable_inputs'] = [scope['board'] if p == 'design/board' else p for p in scope['mutable_inputs']]
        scope['checks'][0]['stage'] = name
        subject = scope['stages']['subjects']['placement']
        scope['stages'] = dict(schema='agent-ee.stages/1', order=[name], subjects={name:subject}, finding_retry_limit=3)
        write_json(self.root/'scope.json', scope)

    def test_placement_writer_cannot_edit_prior_schematic_output(self):
        self.stage('placement')
        with self.writer('design/schematic', 'unauthorized writer native edit'):
            status, state = self.lane.campaign([self.lane.step()])
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        self.assertIn('writer_editable', state['error'])
        work = self.root/'campaign/implementations/1'
        self.assertFalse((work/'receipts').exists())
        self.assertFalse((work/'proposal.scope.json').exists())
        self.assertEqual(campaign.verify_campaign(self.root/'campaign', host=self.lane.host)['state'], 'incomplete')

    def test_verification_writer_cannot_edit_native_with_no_scheduled_steps(self):
        self.stage('verification')
        with self.writer('design/schematic', 'unauthorized verification edit'):
            status, state = self.lane.campaign([])
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        self.assertIn('writer_editable', state['error'])
        self.assertFalse((self.root/'campaign/implementations/1/proposal.scope.json').exists())
        self.assertEqual(campaign.verify_campaign(self.root/'campaign', host=self.lane.host)['state'], 'incomplete')

    def test_malformed_writer_output_does_not_retry_forbidden_edits(self):
        self.stage('verification')
        original = self.lane.agent
        attempts = []
        def malformed(run, label, *args, **kwargs):
            if label == 'implementation':
                attempts.append(label)
                (Path(run)/'review-work/implementation/design/design/schematic').write_text('forbidden')
                return {'execution':'ok'}, {'unexpected':'shape'}
            return original(run, label, *args, **kwargs)
        with patch('agent_ee.reviewers.dispatch', side_effect=malformed):
            status, state = self.lane.campaign([])
        self.assertEqual((status,state['state']),(1,'incomplete'))
        self.assertIn('writer_editable',state['error'])
        self.assertEqual(len(attempts),1)
        self.assertFalse((self.root/'campaign/implementations/1/proposal.scope.json').exists())
        self.assertEqual(campaign.verify_campaign(self.root/'campaign', host=self.lane.host)['state'],'incomplete')

    def test_allowed_intent_edit_and_coordinator_output_remain_adoptable(self):
        self.stage('placement')
        with self.writer('design/intent.json', '{"desired":6}'):
            status, state = self.lane.campaign([self.lane.step()])
        self.assertEqual((status, state['state']), (0, 'criteria_satisfied'))
        work = self.root/'campaign/implementations/1'
        receipt = stage_receipts.validate_receipt(work/'receipts/1-fix-helper')
        self.assertEqual(receipt['inputs']['design/intent.json'], digest(work/'receipts/1-fix-helper/inputs/design/intent.json'))
        self.assertEqual(read_json(Path(state['history'][1]['run'])/'inputs/design/design.json'), {'value':5})
        self.assertIn('writer_editable', self.lane.writer_prompts[0])
        self.assertEqual(campaign.verify_campaign(self.root/'campaign', host=self.lane.host)['state'], 'criteria_satisfied')

    def test_replay_refuses_writer_edit_hidden_in_first_producer_input(self):
        self.stage('placement')
        # Model a compromised live writer guard; retained verification must independently refuse.
        with patch.object(campaign, 'check_writer_proposal', side_effect=lambda d,m,s,e: campaign.check_proposal(d,m,s)):
            with self.writer('design/schematic', 'hidden earlier-stage output edit'):
                status, state = self.lane.campaign([self.lane.step()])
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        self.assertIn('writer_editable', state['error'])
        work = self.root/'campaign/implementations/1'
        stage_receipts.validate_receipt(work/'receipts/1-fix-helper')
        with self.assertRaisesRegex(ValueError, 'writer_editable'):
            campaign.verify_campaign(self.root/'campaign', host=self.lane.host)

    def test_replay_refuses_no_step_writer_edit_and_missing_index(self):
        self.stage('verification')
        with patch.object(campaign, 'check_writer_proposal', side_effect=lambda d,m,s,e: campaign.check_proposal(d,m,s)):
            with self.writer('design/design.json', '{"value":5}'):
                status, state = self.lane.campaign([])
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        self.assertIn('writer_editable', state['error'])
        with self.assertRaisesRegex(ValueError, 'writer_editable'):
            campaign.verify_campaign(self.root/'campaign', host=self.lane.host)
        work = self.root/'campaign/implementations/1'
        (work/'stage-receipts.json').unlink()
        state['stage_receipts'] = {}
        with self.assertRaisesRegex(ValueError, 'anchored receipt index'):
            campaign.verify_writer_records(self.root/'campaign', state, self.lane.config)

    def test_replay_requires_successor_capture_from_proposal(self):
        self.stage('placement')
        status, state = self.lane.campaign([self.lane.step()])
        self.assertEqual(status, 0)
        following = Path(state['history'][1]['run'])/'candidate.json'
        value = read_json(following)
        value['scope_path'] = '/unrelated/scope.json'
        write_json(following, value)
        # Exercise the campaign-specific provenance rule independently of the ordinary seal.
        with self.assertRaisesRegex(ValueError, 'successor was not captured'):
            campaign.verify_writer_records(self.root/'campaign', state, self.lane.config)

    def test_legacy_preflight_does_not_evaluate_absent_native_defaults(self):
        scope = read_json(self.root/'scope.json')
        scope.pop('board')
        scope.pop('schematic')
        write_json(self.root/'scope.json', scope)
        self.lane.config.pop('writer_editable')
        # This boundary test stops before native review; it verifies unchanged optional-field behavior.
        with patch.object(campaign,'continue_campaign',return_value=17):
            status,_ = self.lane.campaign([])
        self.assertEqual(status,17)

    def test_invalid_allowlist_refused_before_campaign_directory(self):
        for value in (None, 'design/intent.json', [None], ['../escape'], ['design/intent.json']*2,
                      ['design/requirements'], ['design/missing']):
            self.lane.config['writer_editable'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'writer_editable'):
                self.lane.campaign([])
            self.assertFalse((self.root/'campaign').exists())


if __name__ == '__main__':
    unittest.main()

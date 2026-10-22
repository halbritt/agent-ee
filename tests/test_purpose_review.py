"""Purpose navigation and advisory retention, without simulated model judgment."""
import json
import shutil
import time
import unittest
from unittest.mock import patch

from agent_ee.candidate import capture
from agent_ee.findings import adjudicate, collect_findings, validate_review
from agent_ee.reviewers import adjudicator_call, build_packet, initial_reviews
from agent_ee.storage import digest, identity, read_json, write_json
from tests import test_findings as fixture
from tests.toolchain import enable_visuals_for_module

setUpModule = enable_visuals_for_module


class PurposeReviewTests(unittest.TestCase):
    setUp = fixture.FindingsTests.setUp
    _review = fixture.FindingsTests._review

    def packet(self, reserve=600):
        # Original illustrative declarations, not imported pod design or native evidence.
        write_json(self.source / 'arrangement.json', {
            'policy': {'obstacles': [{'side': 'B', 'purpose': 'card insertion',
                                     'area_mm2': reserve}], 'fixed_refs': ['J1']},
            'arrangement': {'J1': {'side': 'B', 'pose': 'inboard' if reserve else 'edge'}}})
        self.scope_data['packet_max_bytes'] = 0
        for definition in self.scope_data['review_profile']['reviewers']:
            definition['lens'] = 'Only a custom emphasis.'
        write_json(self.scope_path, self.scope_data)
        self.run_dir = self.root / f'purpose-{reserve}'
        self.run_dir.mkdir()
        capture(self.scope_path, self.run_dir)
        shutil.copytree(self.root / 'run1/checks', self.run_dir / 'checks')
        shutil.copytree(self.root / 'run1/work', self.run_dir / 'work')
        # A drawn illustration, explicitly not a KiCad render qualification.
        image = self.run_dir / 'work/illustration/bottom.svg'
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect width="20" height="30"/></svg>')
        return build_packet(self.run_dir, [self.erc_check, self.rc_check], self.run_id)

    def test_complete_sources_and_images_survive_zero_excerpt_budget(self):
        packet = self.packet()
        purpose = packet['purpose_review']
        self.assertEqual(purpose['authority'], 'advisory')
        self.assertEqual(purpose['empty_field_threshold']['enforcement'], 'advisory')
        self.assertIn('agent-chosen', purpose['empty_field_threshold']['provenance'])
        self.assertEqual(purpose['empty_field_threshold']['area_mm2'], 100)
        sources = {row['path']: row for row in purpose['candidate_sources']}
        captured = read_json(self.run_dir / 'candidate.json')['files']
        self.assertEqual(set(sources), {'inputs/' + rel for rel in captured})
        path = 'inputs/project/arrangement.json'
        self.assertEqual(sources[path]['sha256'], digest(self.run_dir / path))
        self.assertEqual(read_json(self.run_dir / path)['policy']['obstacles'][0]['area_mm2'], 600)
        self.assertIn('work/illustration/bottom.svg', purpose['images'])
        self.assertEqual(len(purpose['questions']), 4)
        self.assertTrue(all(a['bytes_included'] == 0 for a in packet['artifacts']))
        self.assertEqual(packet['checks'], [self.erc_check, self.rc_check])
        self.assertEqual(purpose['measurements'][1]['outcome'], 'defect')
        self.assertEqual(packet['packet_sha256'], identity({k: v for k, v in packet.items() if k != 'packet_sha256'}))
        old_digest = packet['packet_sha256']
        alternative = self.packet(reserve=0)
        self.assertNotEqual(old_digest, alternative['packet_sha256'])
        self.assertNotEqual(sources[path]['sha256'], next(row['sha256'] for row in
                            alternative['purpose_review']['candidate_sources'] if row['path'] == path))

    def test_missing_and_linked_images_are_not_visual_coverage_and_peers_are_absent(self):
        private = self.run_dir / 'review-work/physical/experiments/private.svg'
        private.parent.mkdir(parents=True)
        private.write_text('<svg/>')
        linked = self.run_dir / 'work/rc_transient/link.svg'
        linked.symlink_to(private)
        packet = build_packet(self.run_dir, [self.rc_check], self.run_id)
        purpose = packet['purpose_review']
        self.assertEqual(purpose['images'], [])
        self.assertIn('visual purpose coverage is missing', purpose['limitations'][0])
        self.assertNotIn('review-work/', json.dumps(purpose))
        self.assertNotIn(str(linked.relative_to(self.run_dir)), purpose['images'])
        self.assertTrue(any('symlink' in limit for limit in packet['packet_limits']))

    def test_all_initial_roles_receive_mandatory_purpose_brief_even_with_custom_lens(self):
        packet = self.packet()
        prompts = {}

        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            prompts[role] = prompt
            return {'execution': 'ok'}, {
                'schema': 'agent-ee.review/2', 'candidate_id': packet['candidate_id'],
                'run_id': packet['run_id'], 'packet_sha256': packet['packet_sha256'],
                'role': role, 'coverage': [], 'limitations': ['Transport contract test only'], 'findings': []}

        with patch('agent_ee.reviewers.reviewer_call', side_effect=transport):
            reviews, records = initial_reviews(self.run_dir, packet, time.monotonic() + 30)
        self.assertEqual(len(reviews), 2, records)
        for prompt in prompts.values():
            self.assertEqual(json.loads(prompt.split('PACKET:\n', 1)[1]), packet)
            for phrase in ('EVERY declared obstacle', 'EVERY empty field', 'EVERY part',
                           'single change', 'estimated saving', 'assessment=concern',
                           'card insertion', 'probe access', 'renders', 'Only a custom emphasis.'):
                self.assertIn(phrase, prompt)
            self.assertNotIn('initial_reviews', prompt)

    def test_failed_measurement_without_output_remains_failed_unknown(self):
        failed = {**self.rc_check, 'execution': 'failed', 'outcome': 'unknown',
                  'evidence': ['work/missing/result.json']}
        packet = build_packet(self.run_dir, [failed], self.run_id)
        indexed = packet['purpose_review']['measurements'][0]
        self.assertEqual((indexed['execution'], indexed['outcome']), ('failed', 'unknown'))
        self.assertEqual(indexed['evidence'], [])
        self.assertEqual(packet['checks'], [failed])
        self.assertTrue(any('artifact unavailable' in limit for limit in packet['packet_limits']))

    def test_adjudicator_receives_savings_retention_and_correctness_preservation_rules(self):
        packet = build_packet(self.run_dir, [], self.run_id)
        prompts = []

        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            prompts.append(prompt)
            return {'execution': 'ok'}, {'schema': 'agent-ee.decisions/2',
                'candidate_id': packet['candidate_id'], 'run_id': packet['run_id'], 'decisions': []}

        with patch('agent_ee.reviewers.reviewer_call', side_effect=transport):
            record, decisions = adjudicator_call(self.run_dir, packet, [], [], time.monotonic() + 30)
        self.assertIsNotNone(decisions, record)
        self.assertIn('estimated saving, basis and uncertainty', prompts[0])
        self.assertIn('Missing evidence for a purpose saving is a coverage limitation', prompts[0])
        self.assertIn('Never promote an estimated saving', prompts[0])
        self.assertIn('established correctness defect', prompts[0])

    def test_advisory_saving_is_retained_beside_defect_without_weakening_it(self):
        packet = self.packet()
        self.candidate_id, self.packet_sha256 = packet['candidate_id'], packet['packet_sha256']
        evidence = [{'path': 'inputs/project/arrangement.json', 'locator': 'illustrative reserve declaration',
                     'pointer': '/policy/obstacles/0'}]
        waste = {'id': 'purpose-area', 'title': 'Purpose alternative',
                 'location': 'Illustrative J1 bottom insertion reserve',
                 'condition': 'Declared inboard pose compared with the edge alternative',
                 'mechanism': 'Moving insertion off the edge could recover the declared reserve',
                 'assessment': 'concern', 'severity': 'info', 'evidence': evidence,
                 'reasoning': 'Illustrative saving: up to 600 mm2 from the declared reserve; '
                              'edge alternative; unqualified estimate without native or enclosure validation.'}
        review = validate_review({**self._review(findings=[waste]), 'schema': 'agent-ee.review/2'},
                                 self.candidate_id, self.run_id, self.packet_sha256, self.run_dir)
        findings = collect_findings([review], [self.rc_check])
        decisions = {'schema': 'agent-ee.decisions/2', 'candidate_id': self.candidate_id,
                     'run_id': self.run_id, 'decisions': [
                         {'finding_id': 'circuit/purpose-area', 'disposition': 'noted',
                          'reason': 'Illustrative advisory with stated estimate', 'evidence': evidence},
                         {'finding_id': 'check/rc_transient', 'disposition': 'accepted',
                          'reason': 'Failing measured criterion', 'evidence': [
                              {'path': 'checks/rc_transient.json', 'locator': 'failed response', 'pointer': '/details/v_1ms'}]}]}
        retained = {f['id']: f for f in adjudicate(findings, decisions, self.run_dir)}
        self.assertEqual(retained['circuit/purpose-area']['reasoning'], waste['reasoning'])
        self.assertEqual(retained['circuit/purpose-area']['status'], 'noted')
        self.assertEqual(retained['check/rc_transient']['assessment'], 'defect')
        self.assertEqual(retained['check/rc_transient']['status'], 'accepted')
        decisions['decisions'][1]['disposition'] = 'noted'
        with self.assertRaisesRegex(ValueError, "cannot be 'noted'"):
            adjudicate(findings, decisions, self.run_dir)

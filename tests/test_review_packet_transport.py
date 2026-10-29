"""Oversized review packets reach investigate reviewers through their read-only mount, without loss."""
import json
import time
import unittest
from unittest.mock import patch

from agent_ee import reviewers
from agent_ee.candidate import capture
from agent_ee.executor import sandbox_argv
from agent_ee.harnesses import evidence_mounts
from agent_ee.reviewers import PROMPT_PACKET_CHARS, adjudicator_call, initial_reviews, prompt_packet
from agent_ee.storage import digest, identity, read_json, write_json
from tests import test_findings as fixture


def resolve(document, pointer):
    for token in pointer.split('/')[1:]:
        document = document[token.replace('~1', '/').replace('~0', '~')]
    return document


class PacketTransportTests(unittest.TestCase):
    setUp = fixture.FindingsTests.setUp

    def write_packet(self, scale=1):
        """Shaped like the acquisition review packet (39 checks, 725 artifacts, 1525 citation files); synthetic."""
        packet = dict(schema='agent-ee.packet/1', candidate_id=self.candidate_id, run_id=self.run_id,
            scope='Synthetic transport fixture; no engineering claim.',
            checks=[dict(id=f'check-{i}', kind='drc', execution='ok', outcome='pass', summary='synthetic',
                         evidence=[f'checks/check-{i}.json'], details=dict(rows=['x' * 200] * (100 * scale)))
                    for i in range(39)],
            artifacts=[dict(path=f'work/check-{i % 39}/a{i}.txt', sha256='0' * 64, bytes_total=500, bytes_included=400,
                            extent='prefix', content='y' * (400 * scale)) for i in range(725)],
            citation_files=[dict(path=f'inputs/project/f{i}.kicad_sch', format='text', locator_kinds=['lines'])
                            for i in range(1525)],
            baseline=dict(requirements=['BASELINE-WITNESS-MARKER'], criteria=[]),
            purpose_review=dict(questions=['PURPOSE-BRIEF-MARKER']),
            rf_review=dict(applicable=False, binding_sha256='1' * 64),
            criteria=[dict(id=f'check-{i}', kind='drc') for i in range(39)],
            packet_limits=['text artifact partially supplied'] * 10)
        packet['packet_sha256'] = identity(packet)
        write_json(self.run_dir / 'packet.json', packet)
        return packet

    def review(self, packet, role):
        return {'schema': 'agent-ee.review/2', 'candidate_id': packet['candidate_id'], 'run_id': packet['run_id'],
                'packet_sha256': packet['packet_sha256'], 'role': role, 'coverage': [],
                'limitations': ['Transport test only'], 'findings': []}

    def test_oversized_investigate_packet_is_bounded_and_complete_by_pointer(self):
        packet = self.write_packet(scale=4)
        self.assertGreater(len(json.dumps(packet)), 1_048_576)
        note, view = prompt_packet(self.run_dir, packet, tools=True)
        self.assertLessEqual(len(json.dumps(view)), PROMPT_PACKET_CHARS)
        self.assertIn('/evidence/packet.json', note)
        self.assertIn(digest(self.run_dir / 'packet.json'), note)
        self.assertIn(packet['packet_sha256'], note)
        stored = read_json(self.run_dir / 'packet.json')
        referenced = {key for key, value in view.items() if isinstance(value, dict) and 'packet_reference' in value}
        self.assertTrue({'checks', 'artifacts'} <= referenced)
        for key, value in packet.items():
            if key in referenced:
                self.assertEqual(resolve(stored, view[key]['pointer']), value)
                self.assertIn(key, note)
            else:
                self.assertEqual(view[key], value)
        for key in ('schema', 'candidate_id', 'run_id', 'packet_sha256', 'baseline', 'purpose_review', 'rf_review'):
            self.assertEqual(view[key], packet[key])
        self.assertEqual([row['id'] for row in view['checks']['index']], [row['id'] for row in packet['checks']])
        self.assertEqual(view['artifacts']['items'], 725)
        self.assertFalse(view['artifacts']['index_complete'])

    def test_small_tool_less_and_unbound_packets_stay_inline_and_complete(self):
        small = self.write_packet(scale=0)
        small['checks'], small['artifacts'], small['citation_files'] = small['checks'][:2], small['artifacts'][:2], []
        write_json(self.run_dir / 'packet.json', small)
        self.assertEqual(prompt_packet(self.run_dir, small, tools=True), ('', small))
        large = self.write_packet(scale=4)
        # fixed_packet reviewers have no tools: never substitute a reference they cannot read.
        self.assertEqual(prompt_packet(self.run_dir, large, tools=False), ('', large))
        write_json(self.run_dir / 'packet.json', dict(large, run_id='other'))
        self.assertEqual(prompt_packet(self.run_dir, large, tools=True), ('', large))
        (self.run_dir / 'packet.json').unlink()
        self.assertEqual(prompt_packet(self.run_dir, large, tools=True), ('', large))

    def test_packet_is_a_read_only_mount_for_every_role_without_peer_judgments(self):
        self.write_packet(scale=0)
        for rel in ('reviews/circuit.json', 'review-attempts/circuit/0/prompt.txt',
                    'review-work/circuit/experiments/note.json'):
            write_json(self.run_dir / rel, {'marker': 'PEER-SECRET'})
        for role in ('circuit', 'physical', 'rf', 'adjudicator'):
            mounts = evidence_mounts(self.run_dir, role)
            self.assertIn((self.run_dir / 'packet.json', '/evidence/packet.json'), mounts)
            if role != 'adjudicator':
                self.assertFalse(any(part in str(source) for source, _ in mounts
                                     for part in ('reviews', 'review-attempts', 'review-work')))
            argv = sandbox_argv(self.run_dir / 'inputs', self.root / 'work', ['true'], extra_ro=tuple(mounts))
            position = argv.index('/evidence/packet.json')
            self.assertEqual(argv[position - 2:position], ['--ro-bind', str(self.run_dir / 'packet.json')])

    def test_initial_and_adjudicator_prompts_fit_and_keep_identity_rules_and_peer_context(self):
        packet = self.write_packet(scale=4)
        prompts = {}

        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            prompts[role] = (prompt, schema, definition['tool_mode'])
            if role == 'adjudicator':
                return {'execution': 'ok'}, {'schema': 'agent-ee.decisions/2', 'candidate_id': packet['candidate_id'],
                                             'run_id': packet['run_id'], 'decisions': []}
            return {'execution': 'ok'}, self.review(packet, role)

        with patch('agent_ee.reviewers.reviewer_call', side_effect=transport):
            reviews, records = initial_reviews(self.run_dir, packet, time.monotonic() + 30)
            peer = [dict(review, limitations=['INITIAL-REVIEW-MARKER']) for review in reviews]
            record, decisions = adjudicator_call(self.run_dir, packet, [], peer, time.monotonic() + 30)
        self.assertEqual(len(reviews), 2, records)
        self.assertEqual(record['execution'], 'ok')
        for role, (prompt, schema, mode) in prompts.items():
            self.assertEqual(mode, 'investigate')
            self.assertLess(len(prompt), 1_048_576, role)
            self.assertIn('PACKET TRANSPORT', prompt)
            self.assertIn('Every evidence citation MUST include', prompt)
            for marker in ('BASELINE-WITNESS-MARKER', 'PURPOSE-BRIEF-MARKER'):
                self.assertIn(marker, prompt)
            self.assertEqual(schema['properties']['candidate_id']['enum'], [packet['candidate_id']])
            self.assertEqual(schema['properties']['run_id']['enum'], [packet['run_id']])
            if role == 'adjudicator':
                self.assertEqual(json.loads(prompt.split('\n')[-1])['initial_reviews'], peer)
            else:
                self.assertEqual(schema['properties']['packet_sha256']['enum'], [packet['packet_sha256']])
                self.assertIn('Return agent-ee.review/2', prompt)
                self.assertNotIn('INITIAL-REVIEW-MARKER', prompt)
                self.assertNotIn('initial_reviews', prompt)

    def test_fixed_packet_profile_keeps_the_complete_inline_packet(self):
        self.scope_data['review_profile']['tool_mode'] = 'fixed_packet'
        write_json(self.scope_path, self.scope_data)
        self.run_dir = self.root / 'fixed'
        self.run_dir.mkdir()
        self.candidate_id = capture(self.scope_path, self.run_dir)['candidate_id']
        packet = self.write_packet(scale=0)
        prompts = {}

        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            prompts[role] = (prompt, definition['tool_mode'])
            return {'execution': 'ok'}, self.review(packet, role)

        with patch.object(reviewers, 'PROMPT_PACKET_CHARS', 10), \
                patch('agent_ee.reviewers.reviewer_call', side_effect=transport):
            reviews, records = initial_reviews(self.run_dir, packet, time.monotonic() + 30)
        self.assertEqual(len(reviews), 2, records)
        for prompt, mode in prompts.values():
            self.assertEqual(mode, 'fixed_packet')
            self.assertNotIn('PACKET TRANSPORT', prompt)
            self.assertEqual(json.loads(prompt.split('PACKET:\n', 1)[1]), packet)

"""Draft feedback uses final admission rules; it grants no engineering acceptance."""
import copy
import json
import os
import shutil
import time
import unittest
from unittest.mock import patch

from agent_ee import harnesses, native_run, reviewers
from agent_ee.findings import validate_review
from agent_ee.storage import digest, write_json
from tests import test_findings as fixture


class DraftReviewTests(unittest.TestCase):
    def setUp(self):
        fixture.FindingsTests.setUp(self)
        self.packet = dict(candidate_id=self.candidate_id, run_id=self.run_id,
                           packet_sha256=self.packet_sha256)
        write_json(self.run_dir / 'packet.json', self.packet)
        self.schema = copy.deepcopy(reviewers.REVIEW_SCHEMA)
        reviewers.bind_evidence_schema(self.schema, 'findings', self.run_dir, self.packet, 'circuit')
        self.value = fixture.FindingsTests._review(self, findings=[fixture.FindingsTests._finding(
            self, assessment='concern', evidence=[dict(path='inputs/project/rc.kicad_sch',
                locator='Synthetic symbol', lines=[1, 1])])])
        self.value['schema'] = 'agent-ee.review/2'
        self.draft = self.root / 'draft.json'

    def check(self, value):
        from agent_ee import review_draft
        write_json(self.draft, value)
        control = review_draft.prepare(self.run_dir, 'circuit', self.packet, self.schema)
        before = self.draft.read_bytes()
        result = review_draft.check(self.draft, self.run_dir, control)
        self.assertEqual(self.draft.read_bytes(), before)
        self.assertEqual(result['draft_sha256'], digest(self.draft))
        return result

    def test_observed_wrong_format_and_out_of_range_fail_final_admission(self):
        for citation, error in (
            (dict(path='inputs/project/rc.kicad_sch', locator='symbol', pointer='/unused'), 'frozen producer policy'),
            (dict(path='checks/erc.json', locator='check', lines=[1, 10000]), 'out of range')):
            value = copy.deepcopy(self.value)
            value['findings'][0]['evidence'] = [citation]
            with self.subTest(citation=citation), self.assertRaisesRegex(ValueError, error):
                reviewers.validate_evidence_paths(value, self.schema)
                validate_review(value, self.candidate_id, self.run_id, self.packet_sha256, self.run_dir)

    def test_draft_reports_both_observed_errors_and_accepts_exact_valid_bytes(self):
        self.assertTrue(self.check(self.value)['valid'])
        for citation, error in (
            (dict(path='inputs/project/rc.kicad_sch', locator='symbol', pointer='/unused'), 'frozen producer policy'),
            (dict(path='checks/erc.json', locator='check', lines=[1, 10000]), 'out of range')):
            value = copy.deepcopy(self.value)
            value['findings'][0]['evidence'] = [citation]
            result = self.check(value)
            self.assertFalse(result['valid'])
            self.assertIn(error, result['error'])

    def test_identity_and_fresh_schema_cannot_be_selected_by_the_draft(self):
        for key, wrong in (('candidate_id', 'other'), ('run_id', 'other'),
                           ('packet_sha256', 'other'), ('role', 'physical'), ('schema', 'agent-ee.review/1')):
            with self.subTest(key=key):
                self.assertFalse(self.check(dict(self.value, **{key: wrong}))['valid'])

    def test_unknown_peer_traversal_and_symlink_citations_refuse(self):
        own = self.run_dir / 'review-work/circuit/experiments'
        own.mkdir(parents=True)
        (own / 'link.txt').symlink_to(self.run_dir / 'inputs/project/rc.kicad_sch')
        for path in ('inputs/project/unknown.txt', 'inputs/project/../project/rc.kicad_sch',
                     'review-work/physical/experiments/peer.txt', 'review-work/circuit/experiments/link.txt'):
            value = copy.deepcopy(self.value)
            value['findings'][0]['evidence'][0]['path'] = path
            with self.subTest(path=path):
                self.assertFalse(self.check(value)['valid'])

    def test_new_own_json_experiment_requires_lines_and_resolves_actual_bytes(self):
        path = 'review-work/circuit/experiments/new.json'
        write_json(self.run_dir / path, {'measurement': 1})
        value = copy.deepcopy(self.value)
        citation = dict(path=path, locator='Synthetic measurement', pointer='/measurement')
        value['findings'][0]['evidence'] = [citation]
        self.assertFalse(self.check(value)['valid'])
        del citation['pointer']
        citation['lines'] = [1, 1]
        self.assertTrue(self.check(value)['valid'])
        citation['lines'] = [True, 1]
        self.assertFalse(self.check(value)['valid'])

    def test_checked_draft_cannot_bypass_final_admission_after_change(self):
        self.assertTrue(self.check(self.value)['valid'])
        changed = copy.deepcopy(self.value)
        changed['findings'][0]['evidence'][0]['lines'] = [1, 10000]
        with self.assertRaisesRegex(ValueError, 'out of range'):
            validate_review(changed, self.candidate_id, self.run_id, self.packet_sha256, self.run_dir)

    def test_retained_bindings_cannot_change_or_disappear_after_preparation(self):
        from agent_ee import review_draft
        write_json(self.draft, self.value)
        control = review_draft.prepare(self.run_dir, 'circuit', self.packet, self.schema)
        for path in (control / 'schema.json', self.run_dir / 'packet.json', self.run_dir / 'candidate.json'):
            original = path.read_bytes()
            with self.subTest(path=path, change='edit'):
                path.write_text('{}')
                self.assertFalse(review_draft.check(self.draft, self.run_dir, control)['valid'])
            with self.subTest(path=path, change='remove'):
                path.unlink()
                self.assertFalse(review_draft.check(self.draft, self.run_dir, control)['valid'])
            path.write_bytes(original)

    def test_in_memory_packet_preserves_existing_fresh_review_contract(self):
        (self.run_dir / 'packet.json').unlink()
        self.assertTrue(self.check(self.value)['valid'])
        self.assertFalse(self.check(dict(self.value, packet_sha256='other'))['valid'])

    def test_draft_input_refuses_nonregular_alias_and_oversized_files(self):
        from agent_ee import review_draft
        control = review_draft.prepare(self.run_dir, 'circuit', self.packet, self.schema)
        for kind in ('fifo', 'symlink', 'oversized'):
            path = self.root / kind
            if kind == 'fifo':
                os.mkfifo(path)
            elif kind == 'symlink':
                write_json(self.draft, self.value)
                path.symlink_to(self.draft)
            else:
                with path.open('wb') as stream:
                    stream.truncate(8 * 1024 * 1024 + 1)
            with self.subTest(kind=kind):
                self.assertFalse(review_draft.check(path, self.run_dir, control)['valid'])

    def test_mount_refuses_aliased_experiment_root_or_parent(self):
        from agent_ee import review_draft
        review_draft.prepare(self.run_dir, 'circuit', self.packet, self.schema)
        peer = self.run_dir / 'review-work/physical/experiments'
        peer.mkdir(parents=True)
        for rel in ('review-work/circuit/experiments', 'review-work/circuit'):
            path = self.run_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                shutil.rmtree(path)
            path.symlink_to(peer, target_is_directory=True)
            with self.subTest(path=rel), self.assertRaises(ValueError):
                harnesses.evidence_mounts(self.run_dir, 'circuit')
            path.unlink()

    def test_initial_call_provides_exact_readonly_context_and_real_sandbox_command(self):
        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            self.assertIn('python3 -m agent_ee.review_draft /work/draft.json', prompt)
            mounts = harnesses.evidence_mounts(run, role)
            mapped = dict((dest, source) for source, dest in mounts)
            control = mapped['/review-validation']
            self.assertEqual(json.loads((control / 'schema.json').read_text()), schema)
            self.assertNotIn('/evidence/review-work/physical/experiments', mapped)
            work = run / 'review-work' / role
            work.mkdir(parents=True, exist_ok=True)
            value = dict(self.value, role=role)
            write_json(work / 'draft.json', value)
            execution = native_run.run(native_run.Intent('draft-validation-test',
                ['python3', '-m', 'agent_ee.review_draft', '/work/draft.json'], timeout=20),
                inputs=run / 'inputs', work=work, mounts=mounts)
            self.assertEqual(execution['execution'], 'ok', execution)
            result = json.loads((work / 'stdout.log').read_text())
            self.assertTrue(result['valid'], result)
            self.assertEqual(result['draft_sha256'], digest(work / 'draft.json'))
            return {'execution': 'ok'}, value
        with patch('agent_ee.reviewers.reviewer_call', side_effect=transport):
            values, records = reviewers.initial_reviews(self.run_dir, self.packet,
                time.monotonic() + 30, roles={'circuit'})
        self.assertEqual(len(values), 1, records)

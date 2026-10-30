"""AGENTEE-461: cancellation cannot expose a role-less execution record."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.review_run import structured_call
from agent_ee.reviewers import initial_reviews
from agent_ee.storage import read_json, write_json


class ReviewerPublicationTests(unittest.TestCase):
    def test_cancellation_at_each_publication_boundary(self):
        for boundary in ('before_rename', 'after_rename'):
            for prior in (False, True):
                with self.subTest(boundary=boundary, prior=prior), tempfile.TemporaryDirectory() as tmp:
                    run = Path(tmp)
                    target = run / 'review-executions/circuit.json'
                    previous = {'role': 'circuit', 'execution': 'malformed', 'error': 'prior refusal'}
                    if prior:
                        write_json(target, previous)
                    replace = Path.replace
                    observed = []

                    def interrupt(source, destination):
                        if destination != target:
                            return replace(source, destination)
                        self.assertEqual(source.parent, target.parent)
                        staged = read_json(source)
                        self.assertEqual(staged['role'], 'circuit')
                        self.assertEqual(staged['execution'], 'interrupted')
                        self.assertEqual(len(staged['attempts']), 1)
                        self.assertEqual(staged['attempts'][0]['execution'], 'interrupted')
                        observed.append(staged)
                        if boundary == 'after_rename':
                            replace(source, destination)
                        raise KeyboardInterrupt

                    def cancelled(prompt):
                        raise KeyboardInterrupt

                    with patch.object(Path, 'replace', interrupt), self.assertRaises(KeyboardInterrupt):
                        structured_call(run, 'circuit', 'prompt', {}, cancelled, lambda value: value)
                    self.assertEqual(len(observed), 1)
                    if boundary == 'after_rename':
                        self.assertEqual(read_json(target), observed[0])
                    elif prior:
                        self.assertEqual(read_json(target), previous)
                    else:
                        self.assertFalse(target.exists())
                    self.assertFalse(target.with_name(target.name + '.tmp').exists())

    def test_initial_review_publications_always_include_role(self):
        for execution in ('ok', 'malformed', 'interrupted', 'not_run'):
            with self.subTest(execution=execution), tempfile.TemporaryDirectory() as tmp:
                run = Path(tmp)
                definition = dict(harness='codex', model='scripted-test', effort='high', timeout_s=5)
                profile = {'schema': 'agent-ee.profile/1', 'name': 'publication',
                           'reviewers': [dict(definition, role=role) for role in ('circuit', 'physical')],
                           'adjudicator': definition}
                write_json(run / 'candidate.json', {'scope': {'review_profile': profile}, 'files': {}})
                packet = dict(candidate_id='candidate', run_id='run', packet_sha256='packet')
                observed = []
                replace = Path.replace

                def inspect(source, destination):
                    if destination.parent == run / 'review-executions':
                        record = read_json(source)
                        self.assertEqual(record['role'], destination.stem)
                        self.assertIn('execution', record)
                        observed.append(record)
                    return replace(source, destination)

                def call(run_dir, role, *args, **kwargs):
                    if execution == 'interrupted':
                        raise KeyboardInterrupt
                    response = {'schema': 'agent-ee.review/2', **packet, 'role': role,
                                'coverage': [], 'limitations': [], 'findings': []}
                    return {'execution': execution}, response if execution == 'ok' else None

                with patch('agent_ee.reviewers.reviewer_call', side_effect=call), patch.object(Path, 'replace', inspect):
                    if execution == 'interrupted':
                        with self.assertRaises(KeyboardInterrupt):
                            initial_reviews(run, packet, time.monotonic() + 10)
                    else:
                        initial_reviews(run, packet, time.monotonic() + 10,
                                        roles=set() if execution == 'not_run' else None)
                self.assertEqual({record['role'] for record in observed}, {'circuit', 'physical'})
                for path in (run / 'review-executions').glob('*.json'):
                    self.assertEqual(read_json(path)['role'], path.stem)

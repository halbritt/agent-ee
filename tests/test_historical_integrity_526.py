"""AGENTEE-526 characterization; these fixtures confer no release qualification."""
import copy
from pathlib import Path
import tempfile
import unittest

from agent_ee.baseline import freeze_evidence
from agent_ee.candidate import capture, verify_snapshot
from agent_ee.run_record import save_result, verify
from agent_ee.storage import digest, write_json


class HistoricalReviewBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'requirement.txt').write_text('Captured synthetic requirement.\n')
        scope_path = self.root / 'scope.json'
        write_json(scope_path, dict(schema='agent-ee.scope/1', checks=[],
            inputs=[dict(path=str(self.source), at='requirements')],
            limitations=['Synthetic integrity fixture; no native or engineering acceptance.']))
        self.run = self.root / 'run'
        self.run.mkdir()
        manifest = capture(scope_path, self.run)
        self.policy = self.root / 'policy'
        self.policy.mkdir()
        for name in ('viewer.py', 'routing_rules.py'):
            (self.policy / name).write_text('# Synthetic policy observation.\n')
        self.binary = self.root / 'native-tool'
        self.binary.write_bytes(b'Synthetic runtime fingerprint; never executed.\n')
        self.host = self.observation()
        result = dict(run_id='historical-integrity-526', candidate_id=manifest['candidate_id'],
            state='interrupted', checks=[], reviews=[], review_executions=[], findings=[],
            adjudicated=False, limitations=manifest['scope']['limitations'], **copy.deepcopy(self.host))
        (self.run / 'work').mkdir()
        (self.run / 'work/evidence.txt').write_text('Retained synthetic observation.\n')
        freeze_evidence(self.run, [])
        save_result(self.run, result)

    def observation(self):
        return dict(policy_files={p.name: digest(p) for p in self.policy.glob('*.py')},
            runtime_files={'native-tool': dict(path=str(self.binary), sha256=digest(self.binary))})

    def test_unchanged_snapshot_verifies_without_promoting_incomplete_review(self):
        before = (self.run / 'result.json').read_bytes()
        result = verify(self.run, current_source=False, host=self.host)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertEqual(result['candidate_id'], verify_snapshot(self.run)['candidate_id'])
        self.assertEqual((self.run / 'result.json').read_bytes(), before)

    def test_changed_live_requirement_preserves_snapshot_but_refuses_current_source(self):
        (self.source / 'requirement.txt').write_text('A different current requirement.\n')
        historical = verify(self.run, current_source=False, host=self.host)
        self.assertEqual(historical['outcome'], 'incomplete')
        with self.assertRaisesRegex(ValueError, 'source inputs changed'):
            verify(self.run, host=self.host)

    def test_snapshot_flag_still_refuses_both_unrelated_and_required_policy_changes(self):
        # Characterizes the AGENTEE-526 defect, not the proposed integrity-only API.
        for name in ('viewer.py', 'routing_rules.py'):
            with self.subTest(policy=name):
                path = self.policy / name
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b'# changed observation\n')
                    verify_snapshot(self.run)
                    with self.assertRaisesRegex(ValueError, 'review implementation changed'):
                        verify(self.run, current_source=False, host=self.observation())
                finally:
                    path.write_bytes(original)

    def test_replaced_runtime_bytes_refuse_snapshot_review_verification(self):
        self.binary.write_bytes(b'Replacement runtime; never executed.\n')
        verify_snapshot(self.run)
        with self.assertRaisesRegex(ValueError, 'tool runtime changed'):
            verify(self.run, current_source=False, host=self.observation())

    def test_retained_input_and_evidence_tamper_refuse_without_rewriting_history(self):
        before = (self.run / 'result.json').read_bytes()
        cases = (('inputs/requirements/requirement.txt', 'candidate snapshot changed'),
                 ('work/evidence.txt', 'execution/review evidence changed'))
        for relative, refusal in cases:
            with self.subTest(path=relative):
                path = self.run / relative
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b'tampered\n')
                    with self.assertRaisesRegex(ValueError, refusal):
                        verify(self.run, current_source=False, host=self.host)
                    self.assertEqual((self.run / 'result.json').read_bytes(), before)
                finally:
                    path.write_bytes(original)
        self.assertEqual(verify(self.run, current_source=False, host=self.host)['outcome'], 'incomplete')


if __name__ == '__main__':
    unittest.main()

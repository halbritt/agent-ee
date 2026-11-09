"""Reviewed baseline import, output custody and finite retained-run admission."""
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

try:
    from nightly_support import nightly_fixture
except ImportError:
    from tests.nightly_support import nightly_fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import nightly_evidence
import nightly_full_suite as nightly


class GateImportTests(unittest.TestCase):
    def test_failed_attempt_counts_toward_an_explicit_retained_admission_threshold(self):
        """Renamed from the journal-based no-start variant (AGENTEE-619): an observed failed exit is
        an incomplete attempt that still occupies retained history when a threshold is selected."""
        with nightly_fixture() as case:
            options = dict(root=case['root'], state_dir=case['state_dir'], worktree_dir=case['worktree_dir'],
                           max_retained_runs=1, skip_gh_status=True, skip_plane=True, initialize_baseline=True)
            (case['base'] / 'report').write_text('')
            with patch.dict(os.environ, NIGHTLY_SUITE_EXIT='75'):
                self.assertNotEqual(nightly.run_nightly_suite(**options), 0)
            self.assertFalse((case['state_dir'] / 'active-run.json').exists())
            before = (case['base'] / 'runner-args').read_bytes()
            self.assertNotEqual(nightly.run_nightly_suite(**options), 0)
            check = json.loads((case['state_dir'] / 'retention-check.json').read_text())
            self.assertEqual(check['decision'], 'hold')
            self.assertEqual(check['runs'], 1)
            self.assertGreater(check['bytes'], 0)
            self.assertEqual((case['base'] / 'runner-args').read_bytes(), before)
            self.assertFalse((case['state_dir'] / 'state.json').exists())

    def test_missing_corrupt_or_forgotten_history_never_counts_as_zero(self):
        for fault in ('missing-index', 'corrupt-index', 'missing-attempt', 'changed-execution'):
            with self.subTest(fault=fault), nightly_fixture() as case:
                self.assertEqual(nightly.run_nightly_suite(case['root'], case['state_dir'],
                    worktree_dir=case['worktree_dir'], initialize_baseline=True, skip_gh_status=True), 0)
                index = case['state_dir'] / 'retention-index.json'
                if fault == 'missing-index': index.unlink()
                elif fault == 'corrupt-index': index.write_text('not json')
                elif fault == 'missing-attempt': next((case['state_dir'] / 'runs').glob('*/attempt.json')).unlink()
                else:
                    with next((case['state_dir'] / 'runs').glob('*/execution.json')).open('a') as stream:
                        stream.write('\n')
                with self.assertRaises(ValueError):
                    nightly_evidence.guard_retention(case['state_dir'])
                check = json.loads((case['state_dir'] / 'retention-check.json').read_text())
                self.assertEqual(check['decision'], 'hold')
                self.assertIsNone(check['runs'])
                self.assertIsNone(check['bytes'])
                self.assertIn('reconcile', check['remediation'])

    def test_multiple_lineage_references_do_not_double_count_one_attempt(self):
        with nightly_fixture() as case:
            self.assertEqual(nightly.run_nightly_suite(case['root'], case['state_dir'],
                worktree_dir=case['worktree_dir'], initialize_baseline=True, skip_gh_status=True), 0)
            original = next((case['state_dir'] / 'runs').glob('*/attempt.json'))
            identity = json.loads(original.read_text())['attempt_id']
            alias = case['state_dir'] / 'runs/import-lineage'
            alias.mkdir()
            (alias / 'baseline-origin.json').write_text(json.dumps({'original_attempt_id': identity}))
            nightly_evidence.register_run(case['state_dir'], alias, identity, state='imported')
            count, size = nightly_evidence.history_usage(case['state_dir'])
            self.assertEqual(count, 1)
            self.assertGreater(size, 0)

    def prepare(self, case):
        gate = case['base'] / 'reviewed-gate'
        tree = case['git']('-C', str(case['root']), 'rev-parse', 'HEAD^{tree}')
        self.assertEqual(nightly.main(['qualify', '--root', str(case['root']), '--commit', case['commit'],
            '--tree', tree, '--evidence-dir', str(gate), '--runner', str(case['base'] / 'bin/ae-ci-exec'),
            '--skip-gh-status']), 0)
        selection = {'schema': 'agent-ee.baseline-import/1', 'gate_directory': str(gate),
            'commit': case['commit'], 'tree': tree, 'review_reference': 'owned-test-review@1',
            'input_manifest_sha256': 'b' * 64,
            'output_manifest_sha256': hashlib.sha256((gate / 'output-manifest.json').read_bytes()).hexdigest()}
        return gate, selection

    def invoke(self, case, selection):
        selected = case['base'] / 'import-selection.json'
        selected.write_text(json.dumps(selection))
        return nightly.main(['import-baseline', '--root', str(case['root']),
            '--state-dir', str(case['base'] / 'import-state'), '--selection', str(selected)])

    def test_complete_gate_import_keeps_original_identity_time_and_never_reexecutes(self):
        with nightly_fixture() as case:
            gate, selection = self.prepare(case)
            receipt = json.loads((gate / 'execution.json').read_text())
            before = (case['base'] / 'runner-args').read_bytes()
            self.assertEqual(self.invoke(case, selection), 0)
            state = json.loads((case['base'] / 'import-state/state.json').read_text())
            self.assertEqual(state['last_run_timestamp'], receipt['finished_at'])
            self.assertEqual(state['last_commit'], case['commit'])
            self.assertEqual((case['base'] / 'runner-args').read_bytes(), before)
            self.assertFalse((case['base'] / 'gh-calls').exists())
            self.assertNotEqual(self.invoke(case, selection), 0)

    def test_corrupt_missing_and_mismatched_gate_evidence_is_refused(self):
        # Companion native-input receipt and private outcome journal faults are gone with those
        # contracts (AGENTEE-619); an incomplete receipt and an exit/verdict disagreement replace them.
        faults = ('changed-log', 'missing-log', 'incomplete-receipt', 'exit-disagrees',
                  'not-full', 'controller', 'main-sha', 'tree', 'active', 'state')
        for fault in faults:
            with self.subTest(fault=fault), nightly_fixture() as case:
                gate, selection = self.prepare(case)
                if fault == 'changed-log':
                    with (gate / 'suite.log').open('a') as stream:
                        stream.write('changed after review\n')
                elif fault == 'missing-log':
                    (gate / 'suite.log').unlink()
                elif fault in ('incomplete-receipt', 'exit-disagrees', 'not-full', 'controller'):
                    path = gate / 'execution.json'
                    record = json.loads(path.read_text())
                    if fault == 'controller':
                        record['nightly_runner_sha256'] = '0' * 64
                    elif fault == 'not-full':
                        record['full_suite'] = False
                    elif fault == 'exit-disagrees':
                        record['exit_code'] = 1
                    else:
                        record['completion'] = 'incomplete'
                    path.write_text(json.dumps(record))
                    nightly_evidence.write_manifest(gate)
                    selection['output_manifest_sha256'] = nightly_evidence.digest(gate / 'output-manifest.json')
                elif fault in ('main-sha', 'tree'):
                    selection['commit' if fault == 'main-sha' else 'tree'] = '0' * 40
                else:
                    state = case['base'] / 'import-state'
                    state.mkdir()
                    (state / ('active-run.json' if fault == 'active' else 'state.json')).write_text('{}')
                before = (case['base'] / 'runner-args').read_bytes()
                self.assertNotEqual(self.invoke(case, selection), 0)
                self.assertEqual((case['base'] / 'runner-args').read_bytes(), before)
                self.assertFalse((case['base'] / 'gh-calls').exists())

    def test_retention_limit_preserves_evidence_and_unchanged_main_can_still_skip(self):
        with nightly_fixture() as case:
            options = dict(root=case['root'], state_dir=case['state_dir'], worktree_dir=case['worktree_dir'],
                           fresh_worktree=True, max_retained_runs=1, skip_gh_status=True, skip_plane=True)
            self.assertEqual(nightly.run_nightly_suite(**options, initialize_baseline=True), 0)
            before = (case['base'] / 'runner-args').read_bytes()
            self.assertEqual(nightly.run_nightly_suite(**options), 0)
            (case['root'] / 'owned.txt').write_text('next\n')
            case['git']('-C', str(case['root']), '-c', 'user.name=owned', '-c', 'user.email=test@invalid',
                        'commit', '-qam', 'move')
            case['git']('-C', str(case['root']), 'push', '-q', 'origin', 'HEAD:main')
            self.assertNotEqual(nightly.run_nightly_suite(**options), 0)
            self.assertEqual((case['base'] / 'runner-args').read_bytes(), before)
            self.assertTrue(next((case['state_dir'] / 'runs').glob('*/output-manifest.json')).exists())

    def test_fresh_worktree_never_reuses_ignored_outputs(self):
        with nightly_fixture() as case:
            options = dict(root=case['root'], state_dir=case['state_dir'], worktree_dir=case['worktree_dir'],
                           fresh_worktree=True, skip_gh_status=True, skip_plane=True)
            self.assertEqual(nightly.run_nightly_suite(**options, initialize_baseline=True), 0)
            first = next((case['state_dir'] / 'runs').glob('*/execution.json'))
            old = Path(json.loads(first.read_text())['command'][1])
            stale = old / 'build/stale.json'
            stale.parent.mkdir()
            stale.write_bytes(b'prior retained output\n')
            (case['root'] / 'owned.txt').write_text('next\n')
            case['git']('-C', str(case['root']), '-c', 'user.name=owned', '-c', 'user.email=test@invalid',
                        'commit', '-qam', 'move')
            case['git']('-C', str(case['root']), 'push', '-q', 'origin', 'HEAD:main')
            self.assertEqual(nightly.run_nightly_suite(**options), 0)
            paths = {json.loads(path.read_text())['command'][1] for path in (case['state_dir'] / 'runs').glob('*/execution.json')}
            self.assertEqual(len(paths), 2)
            self.assertEqual(stale.read_bytes(), b'prior retained output\n')


if __name__ == '__main__':
    unittest.main()

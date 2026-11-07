"""A rejected review keeps its initiating error without pretending it completed."""
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee import campaign
from agent_ee.storage import read_json, write_json


class IncompleteDiagnosticTests(unittest.TestCase):
    def test_initiating_review_error_survives_later_receipt_verification_failure(self):
        with tempfile.TemporaryDirectory(prefix='ee625-incomplete-') as directory:
            root = Path(directory)
            run = root/'runs/4'
            write_json(run/'candidate.json', {'candidate_id': 'owned-protocol-fixture'})
            original = 'ValueError: carried placement result is not bound to its receipt'
            write_json(run/'result.json', {'state': 'incomplete', 'error': original})
            state = {'state': 'running', 'history': [{'run': 'prior'}]}
            with self.assertRaisesRegex(ValueError, 'run 4 incomplete: '+original):
                campaign.record_iteration(root, state, run, {})
            self.assertEqual(state, {'state': 'running', 'history': [{'run': 'prior'}]})
            self.assertEqual(read_json(run/'result.json'), {'state': 'incomplete', 'error': original})

    def test_incomplete_campaign_persists_original_error_without_reason_or_new_history(self):
        with tempfile.TemporaryDirectory(prefix='ee625-incomplete-') as directory:
            root = Path(directory)
            run = root/'runs/4'
            write_json(root/'config.json', {})
            write_json(run/'candidate.json', {'candidate_id': 'owned-protocol-fixture'})
            original = 'ValueError: carried placement result is not bound to its receipt'
            write_json(run/'result.json', {'state': 'incomplete', 'error': original})
            state = {'state': 'running', 'history': [{'run': 'prior'}]}
            status = campaign.continue_campaign(root, state, root/'unused-scope.json',
                                                time.monotonic()+1, initial_run=run)
            saved = read_json(root/'state.json')
            self.assertEqual(status, 1)
            self.assertEqual(saved['state'], 'incomplete')
            self.assertIn(original, saved['error'])
            self.assertNotIn('reason', saved)
            self.assertEqual(saved['history'], [{'run': 'prior'}])

    def test_complete_result_still_requires_integrity_verification(self):
        with tempfile.TemporaryDirectory(prefix='ee625-complete-') as directory:
            root = Path(directory)
            run = root/'runs/1'
            write_json(run/'candidate.json', {'candidate_id': 'owned-protocol-fixture'})
            write_json(run/'result.json', {'state': 'complete', 'error': 'old diagnostic'})
            state = {'history': []}
            with self.assertRaises((ValueError, KeyError)) as caught:
                campaign.record_iteration(root, state, run, {})
            self.assertNotIn('old diagnostic', str(caught.exception))
            self.assertEqual(state['history'], [])


if __name__ == '__main__':
    unittest.main()

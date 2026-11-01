"""A failed build step's stage reason names what the step reported."""
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee.build_stages import _step_cause


class StepCauseTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.path / 'run/out').mkdir(parents=True)

    def test_findings_of_a_failed_report_are_the_cause(self):
        (self.path / 'run/console.log').write_text('')
        (self.path / 'run/out/plan.json').write_text(json.dumps(dict(execution='failed', findings=[
            dict(code='pad_pin_mismatch', location='/components/J2', message='footprint pads differ')])))
        (self.path / 'run/out/earlier.json').write_text(json.dumps(dict(execution='ok', findings=[
            dict(code='not_the_cause')])))
        self.assertEqual(_step_cause(self.path), 'pad_pin_mismatch /components/J2 footprint pads differ')

    def test_console_tail_is_used_when_no_report_has_findings(self):
        (self.path / 'run/out/notes.json').write_text('not json')
        (self.path / 'run/console.log').write_text('x' * 700 + 'Traceback: the real error\n')
        cause = _step_cause(self.path)
        self.assertTrue(cause.endswith('the real error'))
        self.assertLessEqual(len(cause), 600)

    def test_unreadable_report_entries_cannot_hide_the_failure(self):
        (self.path / 'run/out/a-directory.json').mkdir()
        (self.path / 'run/out/plan.json').write_text(json.dumps(dict(execution='failed', findings=[dict(code='real_cause')])))
        self.assertEqual(_step_cause(self.path), 'real_cause')

    def test_credential_shaped_text_does_not_reach_the_stage_reason(self):
        for secret in ('abc123', 'z' * 700):  # A long token must not lose its prefix to truncation.
            with self.subTest(length=len(secret)):
                (self.path / 'run/console.log').write_text(f'curl failed: Authorization: Bearer {secret}\n')
                self.assertNotIn(secret[:6], _step_cause(self.path))

    def test_a_silent_failure_says_so(self):
        self.assertEqual(_step_cause(self.path), 'the step left no findings or console output')


if __name__ == '__main__':
    unittest.main()

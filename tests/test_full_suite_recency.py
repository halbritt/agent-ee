"""Tests for Full Suite Recency Check and Nightly Runner (AGENTEE-614 Parts 2 & 3)."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_full_suite_recency
import nightly_full_suite


class RecencyEvaluationTests(unittest.TestCase):
    def test_fresh_status_passes(self):
        """A status within max_hours evaluates to fresh and returns True."""
        now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
        created = now - timedelta(hours=4)
        statuses = [{
            'context': 'local-full-suite',
            'state': 'success',
            'created_at': created.isoformat(),
            'description': '2026-11-05T08:00:00Z: 182 problems (+0, -0, =182)'
        }]
        ok, msg = check_full_suite_recency.check_recency(statuses, max_hours=36.0, now_utc=now)
        self.assertTrue(ok)
        self.assertIn('fresh', msg)
        self.assertIn('4.0h <= 36.0h', msg)

    def test_stale_status_fails(self):
        """A status older than max_hours evaluates to stale and returns False."""
        now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
        created = now - timedelta(hours=40)
        statuses = [{
            'context': 'local-full-suite',
            'state': 'success',
            'created_at': created.isoformat(),
            'description': 'old run'
        }]
        ok, msg = check_full_suite_recency.check_recency(statuses, max_hours=36.0, now_utc=now)
        self.assertFalse(ok)
        self.assertIn('stale', msg)
        self.assertIn('40.0h > 36.0h', msg)

    def test_missing_context_fails(self):
        """Statuses without 'local-full-suite' context return False."""
        statuses = [{
            'context': 'continuous-integration/travis-ci',
            'state': 'success',
            'created_at': '2026-11-05T10:00:00Z'
        }]
        ok, msg = check_full_suite_recency.check_recency(statuses, max_hours=36.0)
        self.assertFalse(ok)
        self.assertIn("No 'local-full-suite' commit status found", msg)

    def test_multiple_statuses_selects_latest(self):
        """When multiple local-full-suite statuses exist, the newest created_at is selected."""
        now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
        statuses = [
            {
                'context': 'local-full-suite',
                'state': 'failure',
                'created_at': (now - timedelta(hours=45)).isoformat(),
                'description': 'old status'
            },
            {
                'context': 'local-full-suite',
                'state': 'success',
                'created_at': (now - timedelta(hours=5)).isoformat(),
                'description': 'newest status'
            },
            {
                'context': 'local-full-suite',
                'state': 'failure',
                'created_at': (now - timedelta(hours=20)).isoformat(),
                'description': 'intermediate status'
            }
        ]
        ok, msg = check_full_suite_recency.check_recency(statuses, max_hours=36.0, now_utc=now)
        self.assertTrue(ok)
        self.assertIn('5.0h <= 36.0h', msg)
        self.assertIn('newest status', msg)

    def test_empty_statuses_fails(self):
        """Empty status list returns False."""
        ok, msg = check_full_suite_recency.check_recency([], max_hours=36.0)
        self.assertFalse(ok)
        self.assertIn('No commit statuses found', msg)

    def test_cli_invocation_with_status_json(self):
        """CLI tool accepts --status-json and exits 0 for fresh, 1 for stale."""
        now = datetime.now(timezone.utc)
        fresh_payload = [{
            'context': 'local-full-suite',
            'state': 'success',
            'created_at': (now - timedelta(hours=2)).isoformat(),
            'description': 'fresh CLI test'
        }]
        stale_payload = [{
            'context': 'local-full-suite',
            'state': 'success',
            'created_at': (now - timedelta(hours=48)).isoformat(),
            'description': 'stale CLI test'
        }]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fresh_file = tmp_path / 'fresh.json'
            fresh_file.write_text(json.dumps(fresh_payload))
            stale_file = tmp_path / 'stale.json'
            stale_file.write_text(json.dumps(stale_payload))

            proc_fresh = subprocess.run([
                sys.executable, str(ROOT / 'scripts/check_full_suite_recency.py'),
                '--status-json', str(fresh_file),
                '--max-hours', '36'
            ], capture_output=True, text=True)
            self.assertEqual(proc_fresh.returncode, 0)
            self.assertIn('RECENCY OK', proc_fresh.stdout)

            proc_stale = subprocess.run([
                sys.executable, str(ROOT / 'scripts/check_full_suite_recency.py'),
                '--status-json', str(stale_file),
                '--max-hours', '36'
            ], capture_output=True, text=True)
            self.assertEqual(proc_stale.returncode, 1)
            self.assertIn('RECENCY FAILURE', proc_stale.stderr)


class ProblemComparisonTests(unittest.TestCase):
    def test_problem_parsing_synthetic(self):
        """Log parser extracts FAIL, ERROR, SKIP refusal, and missing inputs."""
        log_content = (
            "FAIL: test_a (tests.test_mod.Case.test_a)\n"
            "ERROR: test_b (tests.test_mod.Case.test_b)\n"
            "SKIP refused in strict mode: test_c: toolchain prerequisite missing\n"
            "::error title=required input fome-mc-store missing::test_d not executed\n"
            "test_e (tests.test_mod.Case.test_e) ... ok\n"
        )
        with tempfile.NamedTemporaryFile('w+', encoding='utf-8', delete=False) as f:
            f.write(log_content)
            f.flush()
            log_path = Path(f.name)

        try:
            problems = nightly_full_suite.parse_problems_from_log(log_path)
            self.assertEqual(len(problems), 4)
            self.assertEqual(problems['tests.test_mod.Case.test_a'], 'FAIL')
            self.assertEqual(problems['tests.test_mod.Case.test_b'], 'ERROR')
            self.assertTrue(problems['test_c'].startswith('SKIP: '))
            self.assertEqual(problems['test_d'], 'MISSING-INPUT fome-mc-store')
        finally:
            log_path.unlink()

    def test_compare_problems_arithmetic(self):
        """compare_problems accurately classifies fixed, added, and kept problems."""
        old = {'t1': 'FAIL', 't2': 'FAIL', 't3': 'ERROR'}
        new = {'t2': 'FAIL', 't3': 'ERROR', 't4': 'FAIL', 't5': 'SKIP: x'}
        comp = nightly_full_suite.compare_problems(old, new)
        self.assertEqual(comp['fixed'], ['t1'])
        self.assertEqual(comp['added'], ['t4', 't5'])
        self.assertEqual(comp['kept'], ['t2', 't3'])

        summary = nightly_full_suite.format_compare_summary(comp)
        self.assertIn('old 3 new 4 fixed 1 added 2 kept 2', summary)
        self.assertIn('+ t4 | FAIL', summary)

    def test_baseline2_and_20261104_exact_counts(self):
        """When baseline2 and full-suite-20261104 logs exist, exact counts match 144/182/4/42/140."""
        p1 = Path('/home/halbritt/.local/share/agent-ee/zeroasic-20261029-agent707/goal-20261103/baseline2/suite.log')
        p2 = Path('/home/halbritt/.local/share/agent-ee/zeroasic-20261029-agent707/goal-20261103/full-suite-20261104/suite.log')
        if not (p1.exists() and p2.exists()):
            self.skipTest('Historical suite logs not available in execution environment')

        old = nightly_full_suite.parse_problems_from_log(p1)
        new = nightly_full_suite.parse_problems_from_log(p2)
        comp = nightly_full_suite.compare_problems(old, new)
        self.assertEqual(len(comp['old']), 144)
        self.assertEqual(len(comp['new']), 182)
        self.assertEqual(len(comp['fixed']), 4)
        self.assertEqual(len(comp['added']), 42)
        self.assertEqual(len(comp['kept']), 140)


if __name__ == '__main__':
    unittest.main()

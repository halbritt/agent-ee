"""Tests for Full Suite Recency Check and Nightly Runner (AGENTEE-614 Parts 2 & 3)."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    from nightly_support import nightly_fixture
except ImportError:
    from tests.nightly_support import nightly_fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_full_suite_recency
import nightly_full_suite


COMMIT = 'a' * 40


def completed_status(created, state='success', problems=0, label=''):
    finished = created.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return {'context': 'local-full-suite', 'state': state, 'created_at': created.isoformat(),
            'description': f'strict-complete {COMMIT} {finished} problems={problems} {label}'}


class RecencyEvaluationTests(unittest.TestCase):
    def test_fresh_status_passes(self):
        """A recent authoritative exact-commit completion passes."""
        now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
        created = now - timedelta(hours=4)
        statuses = [{
            'context': 'local-full-suite',
            'state': 'success',
            'created_at': created.isoformat(),
            'description': completed_status(created)['description']
        }]
        ok, msg = check_full_suite_recency.check_recency(statuses, max_hours=36.0, now_utc=now)
        self.assertTrue(ok)
        self.assertIn('Completed local-full-suite', msg)
        self.assertIn('age 4.0h', msg)

    def test_stale_status_fails(self):
        """Historical test identity retained: lead F4 says completed unchanged SHA does not expire."""
        now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
        created = now - timedelta(hours=40)
        statuses = [{
            'context': 'local-full-suite',
            'state': 'success',
            'created_at': created.isoformat(),
            'description': completed_status(created)['description']
        }]
        ok, msg = check_full_suite_recency.check_recency(statuses, max_hours=36.0, now_utc=now)
        self.assertTrue(ok)
        self.assertIn('no expiration', msg)
        self.assertIn('age 40.0h', msg)

    def test_pending_or_error_status_does_not_establish_a_completed_suite(self):
        now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
        for state, expected in (('pending', False), ('error', False), ('failure', True)):
            with self.subTest(state=state):
                ok, _ = check_full_suite_recency.check_recency([{
                    'context': 'local-full-suite', 'state': state,
                    'created_at': (now - timedelta(hours=1)).isoformat(),
                    'description': completed_status(now - timedelta(hours=1), state=state)['description'],
                }], now_utc=now)
                self.assertEqual(ok, expected)

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
                'description': completed_status(now - timedelta(hours=45), label='old status')['description']
            },
            {
                'context': 'local-full-suite',
                'state': 'success',
                'created_at': (now - timedelta(hours=5)).isoformat(),
                'description': completed_status(now - timedelta(hours=5), label='newest status')['description']
            },
            {
                'context': 'local-full-suite',
                'state': 'failure',
                'created_at': (now - timedelta(hours=20)).isoformat(),
                'description': completed_status(now - timedelta(hours=20), label='intermediate status')['description']
            }
        ]
        ok, msg = check_full_suite_recency.check_recency(statuses, max_hours=36.0, now_utc=now)
        self.assertTrue(ok)
        self.assertIn('age 5.0h', msg)
        self.assertIn('suite state=success', msg)

    def test_empty_statuses_fails(self):
        """Empty status list returns False."""
        ok, msg = check_full_suite_recency.check_recency([], max_hours=36.0)
        self.assertFalse(ok)
        self.assertIn('No commit statuses found', msg)

    def test_completed_failure_is_exact_commit_evidence_with_failed_quality_visible(self):
        now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
        status = completed_status(now - timedelta(hours=72), state='failure', problems=7)
        ok, message = check_full_suite_recency.check_recency([status], now_utc=now, commit_sha=COMMIT)
        self.assertTrue(ok)
        self.assertIn('suite state=failure', message)
        self.assertIn('problems=7', message)
        self.assertFalse(check_full_suite_recency.check_recency([status], now_utc=now, commit_sha='b' * 40)[0])
        self.assertFalse(check_full_suite_recency.check_recency([], now_utc=now, commit_sha='b' * 40)[0])

    def test_newer_incomplete_or_malformed_evidence_cannot_borrow_older_success(self):
        now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
        old = completed_status(now - timedelta(hours=72))
        current = completed_status(now - timedelta(hours=1))
        variants = [dict(current, state=state) for state in ('pending', 'error', 'unknown')]
        variants += [dict(current, description='aborted without a verdict', state='failure'),
                     dict(current, created_at='malformed'), dict(current, created_at='2026-11-05T11:00:00'),
                     completed_status(now + timedelta(hours=1)),
                     dict(current, description=f'strict-complete {COMMIT} 2026-11-06T00:00:00Z problems=0'),
                     dict(current, description=f'strict-complete {COMMIT} malformed problems=0'),
                     completed_status(now - timedelta(hours=1), problems=3)]
        for latest in variants:
            with self.subTest(latest=latest):
                self.assertFalse(check_full_suite_recency.check_recency(
                    [old, latest], now_utc=now, commit_sha=COMMIT)[0])

    def test_cli_invocation_with_status_json(self):
        """Compatibility --max-hours remains accepted; completed old SHA passes, pending does not."""
        now = datetime.now(timezone.utc)
        fresh_payload = [{
            'context': 'local-full-suite',
            'state': 'success',
            'created_at': (now - timedelta(hours=48)).isoformat(),
            'description': completed_status(now - timedelta(hours=48))['description']
        }]
        stale_payload = [{
            'context': 'local-full-suite',
            'state': 'success',
            'created_at': (now - timedelta(hours=48)).isoformat(),
            'description': 'pending without a complete verdict'
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
    def parse(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'suite.log'
            log.write_text(text, encoding='utf-8')
            return nightly_full_suite.parse_problems_from_log(log)

    def test_canonical_summary_preserves_subtests_and_repeated_occurrences(self):
        problems = self.parse(
            'ERROR: test_a (test_mod.Case.test_a) (value=1)\n'
            'ERROR: test_mod.Case.test_a (value=1)\n'
            'ERROR: test_mod.Case.test_b (value=1)\n'
            'ERROR: test_mod.Case.test_b (value=1)\n'
            'strict suite FAILED: 1 modules, 2 tests, 3 problem(s), '
            '0 required test(s) not executed for missing inputs, 3 other problem(s)\n')
        self.assertEqual(problems, {
            'test_mod.Case.test_a (value=1)': 'ERROR',
            'test_mod.Case.test_b (value=1)': 'ERROR',
            'test_mod.Case.test_b (value=1) [occurrence 2]': 'ERROR',
        })

    def test_problem_parsing_synthetic(self):
        """Log parser extracts FAIL, ERROR, SKIP refusal, and missing inputs."""
        log_content = (
            "FAIL: test_a (tests.test_mod.Case.test_a)\n"
            "ERROR: test_b (tests.test_mod.Case.test_b)\n"
            "test_e (tests.test_mod.Case.test_e) ... ok\n"
            "FAILURE: tests.test_mod.Case.test_a\n"
            "ERROR: tests.test_mod.Case.test_b\n"
            "SKIP refused in strict mode: test_c: toolchain prerequisite missing\n"
            "NOT EXECUTED, required input fome-mc-store missing: test_d\n"
            "strict suite FAILED: 1 modules, 5 tests, 4 problem(s), "
            "1 required test(s) not executed for missing inputs, 3 other problem(s)\n"
            "::error title=required input fome-mc-store missing::test_d not executed: absent\n"
            "ae-ci-exec: gc: done\n"
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

    def test_problem_kind_transition_is_reported_as_a_regression(self):
        old = {'test_a.T.test_x': 'MISSING-INPUT capture', 'test_b.T.test_x': 'SKIP: absent'}
        new = {'test_a.T.test_x': 'ERROR', 'test_b.T.test_x': 'SKIP: different diagnostic'}
        comp = nightly_full_suite.compare_problems(old, new)
        self.assertEqual(comp['changed'], ['test_a.T.test_x'])
        self.assertEqual(comp['kept'], ['test_b.T.test_x'])
        self.assertEqual(nightly_full_suite.regressions(comp), ['test_a.T.test_x'])
        self.assertIn('test_a.T.test_x | MISSING-INPUT capture -> ERROR',
                      nightly_full_suite.format_compare_summary(comp))

    def test_baseline2_and_20261104_exact_counts(self):
        """Portable historical inventories retain the exact 144/182/4/42/140 comparison."""
        reports = json.loads((ROOT / 'tests/fixtures/full-suite-problem-inventories.json').read_text())['reports']
        def replay(report):
            return self.parse('\n'.join(report['inventory'] + [report['verdict']]) + '\n')
        old = replay(reports['baseline2'])
        new = replay(reports['full-suite-20261104'])
        comp = nightly_full_suite.compare_problems(old, new)
        self.assertEqual(len(comp['old']), 144)
        self.assertEqual(len(comp['new']), 182)
        self.assertEqual(len(comp['fixed']), 4)
        self.assertEqual(len(comp['added']), 42)
        self.assertEqual(len(comp['kept']), 140)

    def test_incomplete_or_ambiguous_reports_cannot_look_clean(self):
        failed = ('strict suite FAILED: 1 modules, 1 tests, 2 problem(s), '
                  '0 required test(s) not executed for missing inputs, 2 other problem(s)\n')
        for text in ('', 'ERROR: test_a (test_mod.Case.test_a)\n',
                     'ERROR: test_mod.Case.test_a\n' + failed,
                     'unrecognized inventory\nERROR: test_mod.Case.test_a\n' + failed,
                     'strict suite passed: 1 modules, 1 tests, no skips; 1 native 3D board render tests not selected\n'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    self.parse(text)
        self.assertEqual(self.parse('strict suite passed: 1 modules, 1 tests, no skips\n'), {})


class NightlyLifecycleTests(unittest.TestCase):
    def test_missing_state_requires_explicit_initialization(self):
        with nightly_fixture() as case:
            self.assertNotEqual(self.run_case(case), 0)
            self.assertFalse((case['base'] / 'runner-args').exists())
            self.assertFalse((case['state_dir'] / 'state.json').exists())
            self.assertEqual(nightly_full_suite.main([
                'run', '--root', str(case['root']), '--state-dir', str(case['state_dir']),
                '--worktree', str(case['worktree_dir']), '--initialize-baseline', '--skip-plane', '--skip-gh-status']), 0)
            before = (case['base'] / 'runner-args').read_bytes()
            self.assertNotEqual(self.run_case(case, initialize_baseline=True), 0)
            self.assertEqual((case['base'] / 'runner-args').read_bytes(), before)

    def run_case(self, case, **options):
        return nightly_full_suite.run_nightly_suite(
            root=case['root'], state_dir=case['state_dir'], worktree_dir=case['worktree_dir'], **options)

    def advance_main(self, case):
        owned = case['root'] / 'owned.txt'
        owned.write_text(owned.read_text() + 'moved main\n')
        case['git']('-C', str(case['root']), '-c', 'user.name=agent-ee test',
                    '-c', 'user.email=test@invalid', 'commit', '-q', '-am', 'moved')
        case['git']('-C', str(case['root']), 'push', '-q', 'origin', 'HEAD:main')
        return case['git']('-C', str(case['root']), 'rev-parse', 'HEAD')

    def test_first_inventory_is_recorded_without_inherited_regression_filing(self):
        with nightly_fixture() as case:
            (case['base'] / 'report').write_text(
                'ERROR: test_mod.Case.test_a\n'
                'strict suite FAILED: 1 modules, 1 tests, 1 problem(s), '
                '0 required test(s) not executed for missing inputs, 1 other problem(s)\n')
            with patch.dict(os.environ, NIGHTLY_SUITE_EXIT='1', NIGHTLY_PLANE_EXIT='2'):
                self.assertEqual(self.run_case(case, initialize_baseline=True), 0)
            self.assertFalse((case['base'] / 'plane-args').exists())
            args = (case['base'] / 'gh-args').read_text()
            self.assertIn('state=failure', args)
            self.assertIn('problems=1 baseline', args)
            comp = json.loads((case['state_dir'] / 'latest-compare.json').read_text())
            self.assertTrue(comp['baseline'])
            self.assertEqual(comp['added'], [])
            self.assertEqual(comp['new'], {'test_mod.Case.test_a': 'ERROR'})
            self.assertFalse((case['state_dir'] / 'latest-failures.json').exists())

    def test_reporting_failure_is_visible_and_retry_does_not_rerun_suite(self):
        with nightly_fixture() as case:
            self.assertEqual(self.run_case(case, initialize_baseline=True), 0)
            changed_commit = self.advance_main(case)
            (case['base'] / 'report').write_text(
                'ERROR: test_mod.Case.test_a\n'
                'strict suite FAILED: 1 modules, 1 tests, 1 problem(s), '
                '0 required test(s) not executed for missing inputs, 1 other problem(s)\n')
            with patch.dict(os.environ, NIGHTLY_SUITE_EXIT='1', NIGHTLY_PLANE_EXIT='2'):
                self.assertNotEqual(self.run_case(case), 0)
            args = (case['base'] / 'plane-args').read_text().splitlines()
            self.assertIn('--description-html', args)
            self.assertNotIn('--description', args)
            before = (case['base'] / 'runner-args').read_bytes()
            self.assertEqual(self.run_case(case), 0)
            self.assertEqual((case['base'] / 'runner-args').read_bytes(), before)
            state = json.loads((case['state_dir'] / 'state.json').read_text())
            self.assertEqual(state['last_commit'], changed_commit)
            self.assertTrue(state['reporting']['plane'])

    def test_moved_main_runs_once_and_records_exact_source(self):
        with nightly_fixture() as case:
            self.assertEqual(self.run_case(case, initialize_baseline=True, skip_plane=True, skip_gh_status=True), 0)
            before = (case['base'] / 'runner-args').read_bytes()
            self.assertEqual(self.run_case(case, skip_plane=True, skip_gh_status=True), 0)
            self.assertEqual((case['base'] / 'runner-args').read_bytes(), before)
            (case['root'] / 'owned.txt').write_text('moved main\n')
            case['git']('-C', str(case['root']), '-c', 'user.name=agent-ee test',
                        '-c', 'user.email=test@invalid', 'commit', '-q', '-am', 'moved')
            case['git']('-C', str(case['root']), 'push', '-q', 'origin', 'HEAD:main')
            moved = case['git']('-C', str(case['root']), 'rev-parse', 'HEAD')
            self.assertEqual(self.run_case(case, skip_plane=True, skip_gh_status=True), 0)
            receipts = [json.loads(p.read_text()) for p in (case['state_dir'] / 'runs').glob('*/execution.json')]
            self.assertEqual({r['commit'] for r in receipts}, {case['commit'], moved})
            self.assertEqual(len(receipts), 2)
            self.assertTrue(all(r['exit_code'] == 0 and len(r['suite_log_sha256']) == 64 for r in receipts))
            self.assertEqual(case['git']('-C', str(case['worktree_dir']), 'rev-parse', 'HEAD'), moved)

    def test_failed_or_incomplete_execution_cannot_advance_state_or_publish_completion(self):
        for report, exit_code in (('', '1'), ('strict suite passed: 1 modules, 1 tests, no skips\n', '137')):
            with self.subTest(exit_code=exit_code), nightly_fixture() as case:
                (case['base'] / 'report').write_text(report)
                with patch.dict(os.environ, NIGHTLY_SUITE_EXIT=exit_code):
                    self.assertNotEqual(self.run_case(case, initialize_baseline=True), 0)
                self.assertFalse((case['state_dir'] / 'state.json').exists())
                self.assertIn('state=error', (case['base'] / 'gh-args').read_text())
                self.assertNotIn('strict-complete', (case['base'] / 'gh-args').read_text())
                self.assertFalse((case['base'] / 'plane-args').exists())
                receipt = json.loads(next((case['state_dir'] / 'runs').glob('*/execution.json')).read_text())
                self.assertEqual(receipt['exit_code'], int(exit_code))
                self.assertIn('suite_log_sha256', receipt)
                before = (case['base'] / 'runner-args').read_bytes()
                self.assertNotEqual(self.run_case(case), 0)
                self.assertEqual((case['base'] / 'runner-args').read_bytes(), before)
                self.assertTrue((case['state_dir'] / 'active-run.json').exists())

    def test_failed_fetch_does_not_fall_back_to_local_head(self):
        with nightly_fixture() as case:
            case['git']('-C', str(case['root']), 'remote', 'set-url', 'origin', str(case['base'] / 'absent'))
            self.assertNotEqual(self.run_case(case, initialize_baseline=True), 0)
            self.assertFalse((case['base'] / 'runner-args').exists())

    def test_existing_worktree_reproducer_is_preserved(self):
        with nightly_fixture() as case:
            self.assertEqual(self.run_case(case, initialize_baseline=True, skip_plane=True, skip_gh_status=True), 0)
            retained = case['worktree_dir'] / 'reproducer.json'
            retained.write_bytes(b'owned retained input\n')
            (case['root'] / 'owned.txt').write_text('next main\n')
            case['git']('-C', str(case['root']), '-c', 'user.name=agent-ee test',
                        '-c', 'user.email=test@invalid', 'commit', '-q', '-am', 'next')
            case['git']('-C', str(case['root']), 'push', '-q', 'origin', 'HEAD:main')
            before = (case['base'] / 'runner-args').read_bytes()
            self.assertNotEqual(self.run_case(case), 0)
            self.assertEqual(retained.read_bytes(), b'owned retained input\n')
            self.assertEqual((case['base'] / 'runner-args').read_bytes(), before)

    def test_github_failure_resumes_reporting_without_new_native_execution(self):
        with nightly_fixture() as case:
            with patch.dict(os.environ, NIGHTLY_GH_EXIT='1'):
                self.assertNotEqual(self.run_case(case, initialize_baseline=True), 0)
            before = (case['base'] / 'runner-args').read_bytes()
            self.assertEqual(self.run_case(case), 0)
            self.assertEqual((case['base'] / 'runner-args').read_bytes(), before)
            self.assertTrue(json.loads((case['state_dir'] / 'state.json').read_text())['reporting']['github'])

    def test_missing_input_to_error_transition_files_a_regression(self):
        with nightly_fixture() as case:
            trailer = ('strict suite FAILED: 1 modules, 1 tests, 1 problem(s), '
                       '0 required test(s) not executed for missing inputs, 1 other problem(s)\n')
            report = case['base'] / 'report'
            report.write_text('SKIP refused in strict mode: test_mod.Case.test_a: input absent\n' + trailer)
            with patch.dict(os.environ, NIGHTLY_SUITE_EXIT='1'):
                self.assertEqual(self.run_case(case, initialize_baseline=True), 0)
                self.advance_main(case)
                report.write_text('ERROR: test_mod.Case.test_a\n' + trailer)
                self.assertEqual(self.run_case(case), 0)
            comparison = json.loads((case['state_dir'] / 'latest-compare.json').read_text())
            self.assertEqual(comparison['changed'], ['test_mod.Case.test_a'])
            self.assertIn('--description-html', (case['base'] / 'plane-args').read_text())
            self.assertIn('state=failure', (case['base'] / 'gh-args').read_text())

    def test_kept_failure_stays_failed_and_unchanged_head_never_refreshes_status(self):
        with nightly_fixture() as case:
            (case['base'] / 'report').write_text(
                'ERROR: test_mod.Case.test_a\n'
                'strict suite FAILED: 1 modules, 1 tests, 1 problem(s), '
                '0 required test(s) not executed for missing inputs, 1 other problem(s)\n')
            with patch.dict(os.environ, NIGHTLY_SUITE_EXIT='1'):
                self.assertEqual(self.run_case(case, initialize_baseline=True), 0)
                self.advance_main(case)
                self.assertEqual(self.run_case(case), 0)
                before = (case['base'] / 'gh-calls').read_bytes()
                state_before = json.loads((case['state_dir'] / 'state.json').read_text())
                self.assertEqual(self.run_case(case), 0)
            self.assertIn('state=failure', (case['base'] / 'gh-args').read_text())
            self.assertEqual((case['base'] / 'gh-calls').read_bytes(), before)
            self.assertEqual(json.loads((case['state_dir'] / 'state.json').read_text())['last_run_timestamp'],
                             state_before['last_run_timestamp'])
            self.assertFalse((case['base'] / 'plane-args').exists())

    def test_unreconciled_state_is_preserved_instead_of_rebaselining(self):
        for text in ('not json', '{"last_commit": "legacy"}'):
            with self.subTest(text=text), nightly_fixture() as case:
                case['state_dir'].mkdir()
                state = case['state_dir'] / 'state.json'
                state.write_text(text)
                self.assertNotEqual(self.run_case(case), 0)
                self.assertEqual(state.read_text(), text)
                self.assertFalse((case['base'] / 'runner-args').exists())

    def test_skip_identity_retains_colons_inside_subtest_parameters(self):
        with nightly_fixture() as case:
            (case['base'] / 'report').write_text(
                "SKIP refused in strict mode: test_mod.Case.test_a (value='a: b'): dependency: absent\n"
                'strict suite FAILED: 1 modules, 1 tests, 1 problem(s), '
                '0 required test(s) not executed for missing inputs, 1 other problem(s)\n')
            problems = nightly_full_suite.parse_problems_from_log(case['base'] / 'report')
            self.assertEqual(problems, {"test_mod.Case.test_a (value='a: b')": 'SKIP: dependency: absent'})

    def test_timeout_allows_runner_term_cleanup_and_retains_failure(self):
        with nightly_fixture() as case:
            runner = case['base'] / 'bin/ae-ci-exec'
            runner.write_text('#!/bin/sh\n'
                              'trap \'printf cleaned > "$NIGHTLY_PROBE/term-cleanup"; exit 124\' TERM\n'
                              'sleep 2\n'
                              'cat "$NIGHTLY_PROBE/report"\n')
            with patch.object(nightly_full_suite, 'SUITE_TIMEOUT_SECONDS', 0.2), \
                    patch.object(nightly_full_suite, 'SUITE_STOP_GRACE_SECONDS', 3):
                self.assertNotEqual(self.run_case(case, initialize_baseline=True), 0)
            self.assertEqual((case['base'] / 'term-cleanup').read_text(), 'cleaned')
            receipt = json.loads(next((case['state_dir'] / 'runs').glob('*/execution.json')).read_text())
            self.assertEqual(receipt['exit_code'], 124)
            self.assertEqual(len(receipt['nightly_runner_sha256']), 64)
            self.assertTrue((case['state_dir'] / 'active-run.json').exists())


class PlaneFilingTests(unittest.TestCase):
    def test_deduplication_requires_a_matching_external_identity(self):
        conflict = json.dumps({'ok': False, 'error': {'http_status': 409}})
        for payload, expected in (
                ({'ok': True, 'data': {'external_source': 'nightly', 'external_id': 'commit-a'}}, True),
                ({'ok': True, 'data': {'external_source': 'nightly', 'external_id': 'commit-b'}}, False),
                ({'ok': False, 'error': {'http_status': 401}}, False)):
            with self.subTest(payload=payload):
                replies = [subprocess.CompletedProcess([], 5, conflict, ''),
                           subprocess.CompletedProcess([], 0, json.dumps(payload), '')]
                with patch.object(nightly_full_suite.subprocess, 'run', side_effect=replies) as boundary:
                    self.assertEqual(nightly_full_suite.file_plane_issue(
                        'AGENTEE', 'Regression', 'test <value> & result', 'nightly', 'commit-a'), expected)
                create = boundary.call_args_list[0].args[0]
                self.assertIn('--description-html', create)
                self.assertIn('<pre>test &lt;value&gt; &amp; result</pre>', create)
                lookup = boundary.call_args_list[1].args[0]
                self.assertIn('--external-source', lookup)
                self.assertIn('--external-id', lookup)



if __name__ == '__main__':
    unittest.main()

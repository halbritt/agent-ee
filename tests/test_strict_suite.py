"""Strict-suite judging and declared prerequisite gaps (AGENTEE-16 hosted CI).

A declared gap is reported by name and never counted as a pass; every other
skip, missing result or unexpected success fails the strict run.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

try:
    import toolchain
except ImportError:  # run as tests.test_strict_suite from the repository root
    from tests import toolchain

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('strict_suite', ROOT / 'scripts/strict_suite.py')
strict_suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strict_suite)
GAP = 'declared gap fome-mc-store: fome-mc object store with df304f332129 not available at /absent'


def record(module='test_x', exit=0, **result):
    fields = dict(module=module, run=1, failures=[], errors=[], skipped=[], expected_failures=[],
                  unexpected_successes=[])
    fields.update(result)
    return dict(module=module, exit=exit, seconds=0.1, log='', result=fields)


class JudgeTests(unittest.TestCase):
    def test_declared_gap_skip_is_reported_not_passed_silently(self):
        problems, gaps = strict_suite.judge([record(skipped=[['test_x.T.t', GAP]])], {'fome-mc-store'})
        self.assertEqual(problems, [])
        self.assertEqual(gaps, [('fome-mc-store', 'test_x.T.t', GAP)])
        text = strict_suite.summary_markdown([record(skipped=[['test_x.T.t', GAP]])], problems, gaps,
                                             {'fome-mc-store'})
        self.assertIn('### Declared gap `fome-mc-store`: 1 test(s) not executed', text)
        self.assertIn('`test_x.T.t`', text)

    def test_gap_skip_without_declaration_and_plain_skip_are_refused(self):
        for declared, reason in ((set(), GAP), ({'fome-mc-store'}, 'toolchain prerequisite: bwrap not found'),
                                 ({'fome-mc-store'}, 'declared gap other: reason')):
            problems, gaps = strict_suite.judge([record(skipped=[['test_x.T.t', reason]])], declared)
            self.assertEqual(gaps, [])
            self.assertEqual(problems, [f'SKIP refused in strict mode: test_x.T.t: {reason}'])

    def test_failures_errors_unexpected_success_and_missing_result_fail(self):
        missing = dict(module='test_y', exit=-9, seconds=1.0, log='', problem='no valid result; exit -9')
        problems, _ = strict_suite.judge([
            record(exit=1, failures=[['test_x.T.a', 'tb']], errors=[['test_x.T.b', 'tb']],
                   unexpected_successes=['test_x.T.c']), missing], set())
        self.assertEqual(problems, ['FAILURE: test_x.T.a', 'ERROR: test_x.T.b', 'UNEXPECTED SUCCESS: test_x.T.c',
                                    'test_y: no valid result; exit -9'])

    def test_nonzero_exit_with_a_successful_result_fails(self):
        problems, _ = strict_suite.judge([record(exit=3)], set())
        self.assertEqual(problems, ['test_x: exit 3 with a successful result'])

    def test_module_process_result_is_read_back(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-strict-suite-test-') as scratch:
            outcome = strict_suite.run_module('test_tree_hygiene', scratch, 300)
        self.assertEqual(outcome['exit'], 0, outcome['log'])
        self.assertEqual(outcome['result']['module'], 'test_tree_hygiene')
        self.assertGreaterEqual(outcome['result']['run'], 1)
        self.assertEqual(strict_suite.judge([outcome], set()), ([], []))

    def test_every_suite_module_is_selected(self):
        self.assertEqual(strict_suite.modules(), sorted(p.stem for p in (ROOT / 'tests').glob('test_*.py')))
        self.assertIn('test_strict_suite', strict_suite.modules())


class DeclaredGapTests(unittest.TestCase):
    def env(self, **values):
        clean = {k: v for k, v in os.environ.items()
                 if k not in ('AGENT_EE_REQUIRE_TOOLCHAIN', 'AGENT_EE_DECLARED_GAPS')}
        return patch.dict(os.environ, {**clean, **values}, clear=True)

    def test_absent_prerequisite_skips_fails_strict_and_names_a_declared_gap(self):
        with self.env(), self.assertRaisesRegex(unittest.SkipTest, '^absent$'):
            toolchain.unavailable('fome-mc-store', 'absent')
        with self.env(AGENT_EE_REQUIRE_TOOLCHAIN='1'), \
                self.assertRaisesRegex(AssertionError, 'AGENT_EE_REQUIRE_TOOLCHAIN=1: absent'):
            toolchain.unavailable('fome-mc-store', 'absent')
        with self.env(AGENT_EE_REQUIRE_TOOLCHAIN='1', AGENT_EE_DECLARED_GAPS='fome-mc-store'), \
                self.assertRaisesRegex(unittest.SkipTest, '^declared gap fome-mc-store: absent$'):
            toolchain.unavailable('fome-mc-store', 'absent')

    def test_unknown_declared_gap_is_refused(self):
        with self.env(AGENT_EE_REQUIRE_TOOLCHAIN='1', AGENT_EE_DECLARED_GAPS='fome-mc-store,toolchain'), \
                self.assertRaisesRegex(AssertionError, 'unknown gap\\(s\\): toolchain'):
            toolchain.unavailable('fome-mc-store', 'absent')

    def test_fome_mc_store_requires_every_pinned_revision(self):
        env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1')
        def git(*args):
            return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=agent-ee test',
                                   '-c', 'user.email=test@invalid', *args],
                                  capture_output=True, check=True, text=True, env=env).stdout.strip()
        with tempfile.TemporaryDirectory(prefix='agent-ee-fome-store-') as tmp:
            repo = Path(tmp) / 'store'
            repo.mkdir()
            git('init', '-q')
            (repo / 'owned.txt').write_text('original test content\n')
            git('add', 'owned.txt')
            git('commit', '-q', '-m', 'owned')
            commit = git('rev-parse', 'HEAD')
            with self.env(AGENT_EE_REQUIRE_TOOLCHAIN='1'):
                self.assertEqual(toolchain.fome_mc_store(repo, commit), repo)
                self.assertEqual(toolchain.fome_mc_store(repo), repo)
                with self.assertRaisesRegex(AssertionError, 'with 0123456789ab not available'):
                    toolchain.fome_mc_store(repo, commit, '0123456789abcdef0123456789abcdef01234567')
                with self.assertRaisesRegex(AssertionError, f'not available at {Path(tmp) / "absent"}'):
                    toolchain.fome_mc_store(Path(tmp) / 'absent', commit)


if __name__ == '__main__':
    unittest.main()

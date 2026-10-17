"""Prerequisite policy tests use unittest discovery, never application mocks."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ToolchainPolicyTests(unittest.TestCase):
    def discover(self, strict=False):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "git").symlink_to(shutil.which("git"))
            env = dict(os.environ, PATH=tmp, PYTHONPATH=os.pathsep.join([
                str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'src')]))
            env.pop('AGENT_EE_REQUIRE_TOOLCHAIN', None)
            if strict:
                env['AGENT_EE_REQUIRE_TOOLCHAIN'] = '1'
            return subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s',
                str(Path(__file__).resolve().parent), '-p', 'test_candidate.py', '-v'],
                env=env, capture_output=True, text=True, timeout=60)

    def test_discovery_without_tools_skips_only_native_tests(self):
        result = self.discover()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('OK (skipped=2)', result.stderr)
        self.assertIn('bwrap not found', result.stderr)
        self.assertIn('test_external_input_changes_invalidate_evidence', result.stderr)

    def test_strict_discovery_fails_without_tools(self):
        result = self.discover(strict=True)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn('AGENT_EE_REQUIRE_TOOLCHAIN=1', result.stderr)
        self.assertIn('bwrap not found', result.stderr)
        self.assertNotIn('skipped=', result.stderr)


class GateBoundaryTests(unittest.TestCase):
    def test_available_prerequisites_preserve_assertion_failures(self):
        from unittest.mock import patch
        from toolchain import toolchain_test

        @toolchain_test
        def broken_check():
            raise AssertionError('real regression')

        with patch('toolchain.prerequisite_problem', return_value=None):
            with self.assertRaisesRegex(AssertionError, 'real regression'):
                broken_check()

    def test_probe_errors_are_not_skipped(self):
        from unittest.mock import patch
        from toolchain import require_toolchain
        with patch('toolchain.prerequisite_problem', side_effect=RuntimeError('unexpected')):
            with self.assertRaisesRegex(RuntimeError, 'unexpected'):
                require_toolchain()

    def test_unusable_namespace_reports_diagnostic(self):
        from unittest.mock import patch
        from toolchain import prerequisite_problem
        prerequisite_problem.cache_clear()
        self.addCleanup(prerequisite_problem.cache_clear)
        result = subprocess.CompletedProcess([], 1, '', 'Operation not permitted')
        with patch('toolchain.shutil.which', return_value='/usr/bin/bwrap'), \
                patch('toolchain.subprocess.run', return_value=result):
            self.assertIn('Bubblewrap/user namespaces unavailable: Operation not permitted',
                          prerequisite_problem())

    def test_missing_native_executable_is_a_prerequisite(self):
        from unittest.mock import patch
        from toolchain import prerequisite_problem
        for tool, responses in (
            ('kicad-cli', [0, 1]),
            ('ngspice', [0, 0, 1]),
            ('pcbnew', [0, 0, 0, 42]),
        ):
            with self.subTest(tool=tool):
                prerequisite_problem.cache_clear()
                self.addCleanup(prerequisite_problem.cache_clear)
                results = [subprocess.CompletedProcess([], code, '', '') for code in responses]
                with patch('toolchain.shutil.which', return_value='/usr/bin/bwrap'), \
                        patch('toolchain.subprocess.run', side_effect=results):
                    self.assertIn(tool, prerequisite_problem(native=True))

    def test_production_missing_bubblewrap_remains_unavailable_in_both_modes(self):
        from unittest.mock import patch
        from agent_ee.executor import execute
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'inputs').mkdir()
            for strict in ('0', '1'):
                with self.subTest(strict=strict), patch.dict(os.environ, {
                    'PATH': tmp, 'AGENT_EE_REQUIRE_TOOLCHAIN': strict,
                }):
                    record = execute(root / 'inputs', root / strict, ['/usr/bin/true'])
                    self.assertEqual(record['execution'], 'unavailable')
                    self.assertIsNone(record['exit_code'])
                    self.assertIn('bwrap', (root / strict / 'console.log').read_text())

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


class VisualEnvironmentTests(unittest.TestCase):
    def probe(self, source, value=None):
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([
            str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'src')]))
        env.pop('AGENT_EE_SKIP_VISUALS', None)
        if value is not None:
            env['AGENT_EE_SKIP_VISUALS'] = value
        result = subprocess.run([sys.executable, '-c', source], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_shared_entries_default_to_skip_and_preserve_explicit_values(self):
        for entry in ('toolchain', 'tests'):
            for value in (None, '0', '1', 'false'):
                with self.subTest(entry=entry, value=value):
                    self.assertEqual(self.probe(
                        f'import {entry}, os; print(os.environ["AGENT_EE_SKIP_VISUALS"])', value),
                        '1' if value is None else value)

    def test_default_produces_explicit_skip_without_native_execution(self):
        self.probe("""
import toolchain, tempfile
from pathlib import Path
from agent_ee.candidate_visuals import produce
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    board = root / 'board.kicad_pcb'
    board.write_text('(invalid board: a renderer would fail)')
    report = produce(board, root / 'visuals')
    assert report['status'] == 'skipped', report
    assert report['images'] == [], report
    assert 'execution' not in report, report
""")

    def test_invalid_environment_remains_a_failure(self):
        self.probe("""
import toolchain, tempfile
from pathlib import Path
from agent_ee.candidate_visuals import produce
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    board = root / 'board.kicad_pcb'
    board.write_text('(invalid)')
    try:
        produce(board, root / 'visuals')
    except ValueError as exc:
        assert str(exc) == 'AGENT_EE_SKIP_VISUALS must be 0 or 1', exc
    else:
        raise AssertionError('invalid flag accepted')
    assert not (root / 'visuals').exists()
""", 'false')

    def test_imaging_modules_restore_environment_after_stopall(self):
        self.probe("""
import importlib, os, tests, unittest
from unittest.mock import patch
for previous in ('0', '1'):
    os.environ['AGENT_EE_SKIP_VISUALS'] = previous
    for name in ('test_candidate_visuals', 'test_render3d', 'test_schematic_render',
                 'test_datasheet_footprint', 'test_purpose_review', 'test_schematic_text'):
        module = importlib.import_module('tests.' + name)
        module.setUpModule()
        patch.stopall()  # Existing render3d tests use this per-test cleanup.
        assert os.environ['AGENT_EE_SKIP_VISUALS'] == '0', name
        unittest.doModuleCleanups()
        assert os.environ['AGENT_EE_SKIP_VISUALS'] == previous, name
""")

    def test_production_import_preserves_unset_imaging_default(self):
        self.probe('import agent_ee.candidate_visuals, os; '
                   'assert "AGENT_EE_SKIP_VISUALS" not in os.environ')


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

    def test_invalid_bubblewrap_option_fails_in_both_modes(self):
        from unittest.mock import patch
        from toolchain import prerequisite_problem, require_toolchain
        if shutil.which('bwrap') is None:
            require_toolchain()
        for strict in ('0', '1'):
            prerequisite_problem.cache_clear()
            self.addCleanup(prerequisite_problem.cache_clear)
            with self.subTest(strict=strict), patch.dict(os.environ, {
                'AGENT_EE_REQUIRE_TOOLCHAIN': strict,
            }), patch('toolchain.sandbox_argv', return_value=[
                'bwrap', '--agent-ee-invalid-regression-option',
            ]):
                try:
                    with self.assertRaisesRegex(AssertionError, 'Unknown option'):
                        require_toolchain()
                except unittest.SkipTest as exc:
                    self.fail(f'malformed probe was skipped: {exc}')

    def test_real_shell_missing_native_executables(self):
        from unittest.mock import patch
        import toolchain
        toolchain.require_toolchain(native=True)
        original = toolchain.sandbox_argv
        for missing in ('kicad-cli', 'ngspice'):
            def absent_tool(inputs, work, command):
                # Run the real shell in the real sandbox, selecting an absent binary.
                command = [arg.replace('command -v ' + missing + ' ',
                    'command -v agent_ee_definitely_absent_binary ') for arg in command]
                return original(inputs, work, command)

            for strict in ('0', '1'):
                with self.subTest(tool=missing, strict=strict):
                    toolchain.prerequisite_problem.cache_clear()
                    self.addCleanup(toolchain.prerequisite_problem.cache_clear)
                    with patch.dict(os.environ, {'AGENT_EE_REQUIRE_TOOLCHAIN': strict}), \
                            patch('toolchain.sandbox_argv', side_effect=absent_tool):
                        expected = AssertionError if strict == '1' else unittest.SkipTest
                        with self.assertRaisesRegex(expected, missing + ' not found'):
                            toolchain.require_toolchain(native=True)

    def test_unusable_namespace_reports_diagnostic(self):
        from unittest.mock import patch
        from toolchain import prerequisite_problem, require_toolchain
        for diagnostic in (
                'bwrap: Creating new namespace failed: Operation not permitted',
                'bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted'):
            for strict in ('0', '1'):
                with self.subTest(strict=strict):
                    prerequisite_problem.cache_clear()
                    self.addCleanup(prerequisite_problem.cache_clear)
                    result = subprocess.CompletedProcess([], 1, '',
                        diagnostic)
                    with patch.dict(os.environ, {'AGENT_EE_REQUIRE_TOOLCHAIN': strict}), \
                            patch('toolchain.shutil.which', return_value='/usr/bin/bwrap'), \
                            patch('toolchain.subprocess.run', return_value=result):
                        expected = AssertionError if strict == '1' else unittest.SkipTest
                        with self.assertRaisesRegex(expected, 'Bubblewrap/user namespaces unavailable'):
                            require_toolchain()

    def test_unknown_sandbox_errors_fail_in_both_modes(self):
        from unittest.mock import patch
        from toolchain import prerequisite_problem, require_toolchain
        for strict in ('0', '1'):
            for code, stderr in ((1, 'Operation not permitted'),
                                 (2, 'bwrap: malformed command'), (-9, '')):
                with self.subTest(strict=strict, code=code, stderr=stderr):
                    prerequisite_problem.cache_clear()
                    self.addCleanup(prerequisite_problem.cache_clear)
                    result = subprocess.CompletedProcess([], code, '', stderr)
                    with patch.dict(os.environ, {'AGENT_EE_REQUIRE_TOOLCHAIN': strict}), \
                            patch('toolchain.shutil.which', return_value='/usr/bin/bwrap'), \
                            patch('toolchain.subprocess.run', return_value=result):
                        try:
                            with self.assertRaisesRegex(AssertionError, 'Bubblewrap prerequisite probe failed'):
                                require_toolchain()
                        except unittest.SkipTest as exc:
                            self.fail(f'unexpected probe failure was skipped: {exc}')

    def test_native_discovery_errors_fail_in_both_modes(self):
        from unittest.mock import patch
        from toolchain import prerequisite_problem, require_toolchain
        for strict in ('0', '1'):
            for code, stderr in ((1, ''), (2, 'shell syntax error'),
                                 (42, 'bwrap: unexpected failure'), (-9, '')):
                with self.subTest(strict=strict, code=code, stderr=stderr):
                    prerequisite_problem.cache_clear()
                    self.addCleanup(prerequisite_problem.cache_clear)
                    results = [subprocess.CompletedProcess([], 0, '', ''),
                               subprocess.CompletedProcess([], code, '', stderr)]
                    with patch.dict(os.environ, {'AGENT_EE_REQUIRE_TOOLCHAIN': strict}), \
                            patch('toolchain.shutil.which', return_value='/usr/bin/bwrap'), \
                            patch('toolchain.subprocess.run', side_effect=results):
                        try:
                            with self.assertRaisesRegex(AssertionError, 'kicad-cli prerequisite probe failed'):
                                require_toolchain(native=True)
                        except unittest.SkipTest as exc:
                            self.fail(f'unexpected probe failure was skipped: {exc}')

    def test_missing_native_executable_is_a_prerequisite(self):
        from unittest.mock import patch
        from toolchain import prerequisite_problem, require_toolchain
        for strict in ('0', '1'):
            for tool, responses in (
                ('kicad-cli', [0, 42]),
                ('ngspice', [0, 0, 42]),
                ('pcbnew', [0, 0, 0, 42]),
            ):
                with self.subTest(tool=tool, strict=strict):
                    prerequisite_problem.cache_clear()
                    self.addCleanup(prerequisite_problem.cache_clear)
                    results = [subprocess.CompletedProcess([], code, '', '') for code in responses]
                    with patch.dict(os.environ, {'AGENT_EE_REQUIRE_TOOLCHAIN': strict}), \
                            patch('toolchain.shutil.which', return_value='/usr/bin/bwrap'), \
                            patch('toolchain.subprocess.run', side_effect=results):
                        expected = AssertionError if strict == '1' else unittest.SkipTest
                        with self.assertRaisesRegex(expected, tool):
                            require_toolchain(native=True)

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

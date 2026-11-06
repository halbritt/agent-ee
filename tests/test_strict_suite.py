"""Strict-suite judging and required-input partitions (AGENTEE-16 hosted CI).

The strict run passes only when every test executed and passed: any skip,
failure, expected failure, missing result or unexpected success fails it,
including a test that could not execute because a required input (the fome-mc
store) is absent. No environment setting turns that into a pass.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
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
MISSING = ('Traceback (most recent call last):\nAssertionError: AGENT_EE_REQUIRE_TOOLCHAIN=1: required input '
           'fome-mc-store missing: fome-mc object store with df304f332129 not available at /absent\n')
# The skip reason a5e5ca4's runner accepted as a declared gap; it must now fail.
DECLARED = 'declared gap fome-mc-store: fome-mc object store with df304f332129 not available at /absent'
# main() precompiles the real src/ before submitting modules. These tests call main() in-process,
# often while the strict suite itself is running, so they must never write bytecode into src/.
REAL_PRECOMPILE = strict_suite.precompile_sources
_precompile = patch.object(strict_suite, 'precompile_sources', return_value=True)
_module_environment = patch.dict(os.environ)


def setUpModule():
    _precompile.start()
    _module_environment.start()
    # Nested suite fixtures declare their own test inventory and input policy.
    os.environ.pop('AGENT_EE_MODULE_ENV', None)


def tearDownModule():
    _module_environment.stop()
    _precompile.stop()


def record(module='test_x', exit=0, **result):
    fields = dict(module=module, run=1, failures=[], errors=[], skipped=[], expected_failures=[],
                  unexpected_successes=[])
    fields.update(result)
    return dict(module=module, exit=exit, seconds=0.1, log='', result=fields)


class SchedulingTests(unittest.TestCase):
    SELECTED = ['test_c', 'test_b', 'test_a', 'test_d']

    def schedule(self, text):
        with tempfile.TemporaryDirectory(prefix='agent-ee-strict-timings-') as tmp:
            log = Path(tmp) / 'previous.log'
            log.write_text(text)
            return strict_suite.schedule_modules(self.SELECTED, log)

    def test_without_timings_is_alphabetical_even_for_explicit_selection(self):
        self.assertEqual(strict_suite.schedule_modules(self.SELECTED), sorted(self.SELECTED))

    def test_longest_first_with_alphabetical_ties_and_untimed_modules(self):
        self.assertEqual(self.schedule(
            'unittest output\n===== test_b (2.5s, exit 0) [1/4]\n'
            '===== test_c (9.0s, exit timeout after 30s) [2/4]\n'
            '===== test_a (2.5s, exit 1) [3/4]\n'
            '===== test_unselected (99.0s, exit 0) [4/4]\n'),
            ['test_c', 'test_a', 'test_b', 'test_d'])

    def test_partial_run_uses_completed_timings_and_sorts_the_rest(self):
        self.assertEqual(self.schedule('===== test_d (3.0s, exit 0) [1/4]\n'
                                       'unittest still running when capture stopped\n'),
                         ['test_d', 'test_a', 'test_b', 'test_c'])

    def test_child_banner_does_not_discard_completed_timings(self):
        for banner in ('===== native diagnostic\n', '===== cleanup\n', '===== \n'):
            with self.subTest(banner=banner):
                self.assertEqual(self.schedule('===== test_d (3.0s, exit 0) [1/4]\n' + banner),
                                 ['test_d', 'test_a', 'test_b', 'test_c'])

    def test_zero_timing_precedes_untimed_modules(self):
        self.assertEqual(self.schedule('===== test_d (0.0s, exit 0) [1/1]\n'),
                         ['test_d', 'test_a', 'test_b', 'test_c'])

    def test_missing_unreadable_or_non_utf8_log_falls_back(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-strict-bad-log-') as tmp:
            log = Path(tmp) / 'previous.log'
            for path in (log, Path(tmp)):
                self.assertEqual(strict_suite.schedule_modules(self.SELECTED, path), sorted(self.SELECTED))
            log.write_bytes(b'\xff')
            self.assertEqual(strict_suite.schedule_modules(self.SELECTED, log), sorted(self.SELECTED))

    def test_malformed_log_falls_back_without_partial_timings(self):
        valid = '===== test_c (9.0s, exit 0) [1/2]\n'
        for invalid in ('', 'unrelated log\n', '===== test_a (1.5s,\n', '===== test_a\n', '===== test_\n',
                        '===== test_a (oops, exit 0) [2/2]\n',
                        *(f'===== test_a ({value}s, exit 0) [2/2]\n'
                          for value in ('NaN', 'inf', '-1', '1e999')),
                        valid):
            with self.subTest(invalid=invalid):
                text = invalid if invalid in ('', 'unrelated log\n') else valid + invalid
                self.assertEqual(self.schedule(text), sorted(self.SELECTED))

    def test_main_submits_in_scheduled_order_and_keeps_output_formats(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-strict-submit-') as tmp:
            log, summary = Path(tmp) / 'previous.log', Path(tmp) / 'summary.md'
            log.write_text('===== test_c (8.0s, exit 0) [1/1]\n')
            submitted = []
            def run(module, *args):
                submitted.append(module)
                return record(module=module)
            out, err = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, AGENT_EE_REQUIRE_TOOLCHAIN='1'), \
                    patch.object(strict_suite, 'modules', return_value=self.SELECTED), \
                    patch.object(strict_suite, 'run_module', side_effect=run), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = strict_suite.main(['--jobs', '1', '--previous-log', str(log),
                                          '--summary', str(summary), *self.SELECTED])
            self.assertEqual(code, 0)
            self.assertEqual(submitted, ['test_c', 'test_a', 'test_b', 'test_d'])
            self.assertTrue(out.getvalue().startswith('===== test_c (0.1s, exit 0) [1/4]\n'))
            records = [record(module=name) for name in sorted(self.SELECTED)]
            self.assertEqual(summary.read_text(), strict_suite.summary_markdown(records, [], []) + '\n')
            self.assertEqual(err.getvalue(), strict_suite.verdict(records, [], []) + '\n')


class PrecompileTests(unittest.TestCase):
    """Before any module runs, every src/ cache a child could write matches its source exactly."""

    def tree(self, tmp):
        package = Path(tmp) / 'src/agent_ee'
        (package / 'sub').mkdir(parents=True)
        for path in (package / '__init__.py', package / 'sub/__init__.py'):
            path.write_text('')
        (package / 'mod.py').write_text('VALUE = 1\n')
        return Path(tmp)

    @staticmethod
    def header(source):
        """The (mtime, size) a timestamp .pyc records, as import validates it."""
        import importlib.util as util
        data = Path(util.cache_from_source(str(source))).read_bytes()
        return int.from_bytes(data[8:12], 'little'), int.from_bytes(data[12:16], 'little')

    @staticmethod
    def stamp(source):
        info = source.stat()
        return int(info.st_mtime) & 0xFFFFFFFF, info.st_size & 0xFFFFFFFF

    def test_missing_stale_and_same_second_edits_are_precompiled(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-strict-pyc-') as tmp:
            root = self.tree(tmp)
            source = root / 'src/agent_ee/mod.py'
            self.assertTrue(REAL_PRECOMPILE(root))
            self.assertEqual(self.header(source), self.stamp(source))
            # A size-changing edit inside the same mtime second: a timestamp-only check keeps the old cache.
            mtime = source.stat().st_mtime_ns
            source.write_text('VALUE = 2222\n')
            os.utime(source, ns=(mtime, mtime))
            self.assertTrue(REAL_PRECOMPILE(root))
            self.assertEqual(self.header(source), self.stamp(source))
            self.assertEqual(self.header(root / 'src/agent_ee/sub/__init__.py'),
                             self.stamp(root / 'src/agent_ee/sub/__init__.py'))

    def test_unwritable_caches_are_skipped_but_writable_descendants_are_not(self):
        import importlib.util as util
        with tempfile.TemporaryDirectory(prefix='agent-ee-strict-pyc-') as tmp:
            root = self.tree(tmp)
            package = root / 'src/agent_ee'
            package.chmod(0o555)
            try:
                if os.access(package, os.W_OK):
                    self.fail('test host lets this user write a mode 555 directory (running as root?)')
                self.assertTrue(REAL_PRECOMPILE(root))
                self.assertFalse(Path(util.cache_from_source(str(package / 'mod.py'))).exists())
                self.assertTrue(Path(util.cache_from_source(str(package / 'sub/__init__.py'))).is_file())
            finally:
                package.chmod(0o755)

    def test_compile_errors_and_unreadable_directories_fail(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-strict-pyc-') as tmp:
            root = self.tree(tmp)
            (root / 'src/agent_ee/broken.py').write_text('def (:\n')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(REAL_PRECOMPILE(root))
        with tempfile.TemporaryDirectory(prefix='agent-ee-strict-pyc-') as tmp:
            root = self.tree(tmp)
            hidden = root / 'src/agent_ee/sub'
            hidden.chmod(0o000)
            try:
                if os.access(hidden, os.R_OK):
                    self.fail('test host lets this user read a mode 000 directory (running as root?)')
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertFalse(REAL_PRECOMPILE(root))
            finally:
                hidden.chmod(0o755)

    def test_a_directory_symlink_under_src_fails(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-strict-pyc-') as tmp:
            root = self.tree(tmp)
            outside = Path(tmp) / 'outside'
            outside.mkdir()
            (outside / '__init__.py').write_text('')
            (root / 'src/linked').symlink_to(outside, target_is_directory=True)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertFalse(REAL_PRECOMPILE(root))
            self.assertIn('directory symlink', err.getvalue())

    def test_main_refuses_to_run_modules_when_precompiling_fails(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, AGENT_EE_REQUIRE_TOOLCHAIN='1'), \
                patch.object(strict_suite, 'modules', return_value=['test_a']), \
                patch.object(strict_suite, 'precompile_sources', return_value=False), \
                patch.object(strict_suite, 'run_module') as run, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = strict_suite.main(['--jobs', '1', 'test_a'])
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertIn('bytecode could not be precompiled', err.getvalue())


class JudgeTests(unittest.TestCase):
    def test_missing_required_input_is_named_and_fails(self):
        records = [record(exit=1, failures=[['test_x.T.t', MISSING]])]
        problems, missing = strict_suite.judge(records)
        self.assertEqual(problems, ['NOT EXECUTED, required input fome-mc-store missing: test_x.T.t'])
        self.assertEqual(missing, [('fome-mc-store', 'test_x.T.t',
                                    'fome-mc object store with df304f332129 not available at /absent')])
        text = strict_suite.summary_markdown(records, problems, missing)
        self.assertIn('strict suite FAILED', text)
        self.assertNotIn('passed', text)
        self.assertIn('### Required input `fome-mc-store` missing: 1 required test(s) not executed', text)
        self.assertIn('`test_x.T.t`', text)

    def test_every_skip_is_refused_including_a_former_declared_gap(self):
        for reason in (DECLARED, 'toolchain prerequisite: bwrap not found', 'declared gap other: reason'):
            problems, missing = strict_suite.judge([record(skipped=[['test_x.T.t', reason]])])
            self.assertEqual(missing, [])
            self.assertEqual(problems, [f'SKIP refused in strict mode: test_x.T.t: {reason}'])
            self.assertTrue(strict_suite.verdict([record()], problems, missing).startswith('strict suite FAILED'))

    def test_failures_errors_unexpected_success_and_missing_result_fail(self):
        missing = dict(module='test_y', exit=-9, seconds=1.0, log='', problem='no valid result; exit -9')
        problems, _ = strict_suite.judge([
            record(exit=1, failures=[['test_x.T.a', 'tb']], errors=[['test_x.T.b', 'tb']],
                   unexpected_successes=['test_x.T.c']), missing])
        self.assertEqual(problems, ['FAILURE: test_x.T.a', 'ERROR: test_x.T.b', 'UNEXPECTED SUCCESS: test_x.T.c',
                                    'test_y: no valid result; exit -9'])

    def test_expected_failure_is_refused(self):
        # unittest counts an expected failure as success; a strict gate must not.
        problems, missing = strict_suite.judge([record(expected_failures=['test_x.T.e'])])
        self.assertEqual(missing, [])
        self.assertEqual(problems, ['EXPECTED FAILURE refused in strict mode: test_x.T.e'])
        self.assertTrue(strict_suite.verdict([record()], problems, missing).startswith('strict suite FAILED'))

    def test_nonzero_exit_with_a_successful_result_fails(self):
        problems, _ = strict_suite.judge([record(exit=3)])
        self.assertEqual(problems, ['test_x: exit 3 with a successful result'])

    def test_module_process_result_is_read_back(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-strict-suite-test-') as scratch:
            outcome = strict_suite.run_module('test_tree_hygiene', scratch, 300)
        self.assertEqual(outcome['exit'], 0, outcome['log'])
        self.assertEqual(outcome['result']['module'], 'test_tree_hygiene')
        self.assertGreaterEqual(outcome['result']['run'], 1)
        self.assertEqual(strict_suite.judge([outcome]), ([], []))

    def test_child_initializes_defaults_for_a_module_without_toolchain_imports(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-strict-visual-default-') as tmp:
            root = Path(tmp)
            tree = root / 'tree'
            tests = tree / 'tests'
            tests.mkdir(parents=True)
            for name in ('__init__.py', 'toolchain.py'):
                (tests / name).symlink_to(ROOT / 'tests' / name)
            (tests / 'test_visual_default.py').write_text(textwrap.dedent("""
                import os, unittest
                class T(unittest.TestCase):
                    def test_default(self):
                        self.assertEqual(os.environ['AGENT_EE_SKIP_VISUALS'], '1')
                """))
            env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
            env.pop('AGENT_EE_SKIP_VISUALS', None)
            with patch.dict(os.environ, env, clear=True), patch.object(strict_suite, 'ROOT', tree), \
                    patch.object(strict_suite, 'TESTS', tests):
                outcome = strict_suite.run_module('test_visual_default', root, 30)
        self.assertEqual(outcome['exit'], 0, outcome['log'])
        self.assertEqual(outcome['result']['run'], 1)
        self.assertEqual(strict_suite.judge([outcome]), ([], []))

    def test_every_suite_module_is_selected(self):
        self.assertEqual(strict_suite.modules(), sorted(p.stem for p in (ROOT / 'tests').glob('test_*.py')))
        self.assertIn('test_strict_suite', strict_suite.modules())

    def test_native_board_render_regressions_have_explicit_selection_markers(self):
        def cases(group):
            for member in group:
                if isinstance(member, unittest.TestSuite):
                    yield from cases(member)
                else:
                    yield member
        marked = set()
        for name in ('test_candidate_visuals', 'test_render3d', 'test_render_gallery'):
            suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern=name + '.py')
            marked.update(test.id() for test in cases(suite)
                          if getattr(getattr(test, test._testMethodName),
                                     '_agent_ee_native_board_render', False))
        self.assertEqual(marked, {
            'test_candidate_visuals.CandidateVisualTests.test_enclosed_imaging_timeout_reaps_render_and_svg_children',
            'test_candidate_visuals.CandidateVisualTests.test_native_four_layer_images_and_packet_hashes',
            'test_candidate_visuals.CandidateVisualTests.test_imaging_preserves_twenty_second_drc_budget',
            'test_candidate_visuals.CandidateVisualTests.test_native_placement_default_retains_images_beside_result',
            'test_candidate_visuals.CandidateVisualTests.test_networkless_placement_runner_retains_images_through_nested_executor',
            'test_candidate_visuals.CandidateVisualTests.test_stock_defaults_and_project_models_survive_proposal_and_imaging',
            'test_render3d.NativeRenderSmokeTests.test_real_small_fixture_renders_all_three_views_without_source_changes',
            'test_render_gallery.GalleryTests.test_native_render_binding_refuses_snapshot_and_permutation_tampering',
        })

    def test_runner_recognises_exactly_the_toolchain_partitions(self):
        self.assertEqual(strict_suite.REQUIRED_INPUTS, toolchain.REQUIRED_INPUTS)
        for gap in toolchain.REQUIRED_INPUTS:
            text = toolchain.MISSING_INPUT.format(gap=gap) + 'absent'
            self.assertEqual(strict_suite.MISSING.search(text).group('gap', 'reason'), (gap, 'absent'))


class MainTests(unittest.TestCase):
    """main() over an owned miniature tree, run as real per-module processes."""
    MODULES = {
        'test_passing': """
            import unittest
            class T(unittest.TestCase):
                def test_passes(self):
                    pass
            """,
        'test_render_selection': """
            import unittest
            class T(unittest.TestCase):
                def test_regular(self):
                    pass
                def test_native_board_render(self):
                    pass
                test_native_board_render._agent_ee_native_board_render = True
            """,
        'test_skipping': f"""
            import unittest
            class T(unittest.TestCase):
                def test_passes(self):
                    pass
                def test_former_declared_gap(self):
                    raise unittest.SkipTest({DECLARED!r})
            """,
        'test_expected_failure': """
            import unittest
            class T(unittest.TestCase):
                @unittest.expectedFailure
                def test_known_broken(self):
                    self.fail('known broken')
            """,
        'test_inputs': f"""
            import sys, unittest
            sys.path.insert(0, {str(ROOT / 'tests')!r})
            from toolchain import fome_mc_store
            class T(unittest.TestCase):
                def test_store(self):
                    fome_mc_store('/nonexistent/agent-ee-strict-suite-store', 'HEAD')
            """,
    }

    def run_main(self, modules, **env):
        with tempfile.TemporaryDirectory(prefix='agent-ee-strict-main-') as tmp:
            tree = Path(tmp) / 'tree'
            (tree / 'tests').mkdir(parents=True)
            for name, source in self.MODULES.items():
                (tree / 'tests' / f'{name}.py').write_text(textwrap.dedent(source))
            summary = Path(tmp) / 'summary.md'
            clean = {k: v for k, v in os.environ.items()
                     if k not in ('AGENT_EE_REQUIRE_TOOLCHAIN', 'AGENT_EE_DECLARED_GAPS')}
            out, err = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {**clean, 'AGENT_EE_REQUIRE_TOOLCHAIN': '1',
                                         'PYTHONPATH': str(ROOT / 'src'), **env}, clear=True), \
                    patch.object(strict_suite, 'ROOT', tree), patch.object(strict_suite, 'TESTS', tree / 'tests'), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = strict_suite.main(['--jobs', '2', '--summary', str(summary), *modules])
            return code, out.getvalue(), err.getvalue(), summary.read_text()

    def test_all_passing_modules_pass(self):
        code, out, err, summary = self.run_main(['test_passing'])
        self.assertEqual(code, 0, out + err)
        self.assertIn('strict suite passed: 1 modules, 1 tests, no skips', err)
        self.assertIn('strict suite passed', summary)

    def test_native_render_selection_preserves_other_tests_and_reports_omissions(self):
        code, out, err, summary = self.run_main(['test_render_selection'],
                                                AGENT_EE_NATIVE_RENDER_MODE='omit')
        self.assertEqual(code, 0, out + err)
        self.assertIn('1 tests', err)
        self.assertIn('test_render_selection.T.test_native_board_render', summary)
        self.assertIn('not selected', summary)
        code, out, err, summary = self.run_main(['test_render_selection'],
                                                AGENT_EE_NATIVE_RENDER_MODE='run')
        self.assertEqual(code, 0, out + err)
        self.assertIn('2 tests', err)
        self.assertNotIn('not selected', summary)


    def test_expected_failure_fails_the_run(self):
        code, out, err, summary = self.run_main(['test_passing', 'test_expected_failure'])
        self.assertEqual(code, 1, out + err)
        for text in (err, summary):
            self.assertNotIn('passed', text)
        self.assertIn('EXPECTED FAILURE refused in strict mode: test_expected_failure.T.test_known_broken', err)
        self.assertIn('strict suite FAILED: 2 modules, 2 tests, 1 problem(s)', err)
        self.assertIn('- `EXPECTED FAILURE refused in strict mode: test_expected_failure.T.test_known_broken`',
                      summary)

    def test_skipped_or_unexecuted_required_tests_fail_with_and_without_declared_gaps(self):
        for env in ({}, {'AGENT_EE_DECLARED_GAPS': 'fome-mc-store'}):
            with self.subTest(env=env):
                code, out, err, summary = self.run_main(['test_passing', 'test_skipping', 'test_inputs'], **env)
                self.assertEqual(code, 1, out + err)
                for text in (out, err, summary):
                    self.assertNotIn('passed', text)
                self.assertIn('strict suite FAILED: 3 modules, 4 tests, 2 problem(s), 1 required test(s) '
                              'not executed for missing inputs, 1 other problem(s)', err)
                self.assertIn(f'SKIP refused in strict mode: test_skipping.T.test_former_declared_gap: {DECLARED}',
                              err)
                self.assertIn('NOT EXECUTED, required input fome-mc-store missing: test_inputs.T.test_store', err)
                self.assertIn('::error title=required input fome-mc-store missing::'
                              'test_inputs.T.test_store not executed', out)
                self.assertIn('### Required input `fome-mc-store` missing: 1 required test(s) not executed',
                              summary)


class RenderChangeSelectionTests(unittest.TestCase):
    def test_changed_renderer_runs_native_tests_and_unrelated_change_omits_them(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-render-selection-') as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'src/agent_ee').mkdir(parents=True)
            (root / 'src/agent_ee/render3d.py').write_text('original\n')
            (root / 'README.md').write_text('original\n')
            def git(*args):
                return subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test',
                                       '-c', 'user.email=test@example.invalid', *args],
                                      check=True, capture_output=True, text=True).stdout.strip()
            git('add', '.')
            git('commit', '-qm', 'base')
            base = git('rev-parse', 'HEAD')
            (root / 'README.md').write_text('changed\n')
            git('commit', '-qam', 'docs')
            with patch.object(strict_suite, 'ROOT', root):
                self.assertEqual(strict_suite.render_mode_from_change_base(base), 'omit')
                self.assertEqual(strict_suite.render_mode_from_change_base(''), 'run')
                self.assertEqual(strict_suite.render_mode_from_change_base('unknown-base'), 'run')
                (root / 'src/agent_ee/render3d.py').write_text('changed\n')
                self.assertEqual(strict_suite.render_mode_from_change_base(base), 'run')
                git('commit', '-qam', 'renderer')
                self.assertEqual(strict_suite.render_mode_from_change_base(base), 'run')
                for path in ('Dockerfile', 'docker/kicad-10.0-releases.asc', 'docker/apt-retry.sh', '.dockerignore'):
                    with self.subTest(path=path):
                        previous = git('rev-parse', 'HEAD')
                        changed = root / path
                        changed.parent.mkdir(parents=True, exist_ok=True)
                        changed.write_text('changed\n')
                        git('add', path)
                        git('commit', '-qm', 'image toolchain')
                        self.assertEqual(strict_suite.render_mode_from_change_base(previous), 'run')
                previous = git('rev-parse', 'HEAD')
                gallery_installer = root / 'scripts/install_render_gallery.sh'
                gallery_installer.parent.mkdir(parents=True, exist_ok=True)
                gallery_installer.write_text('changed\n')
                git('add', 'scripts/install_render_gallery.sh')
                git('commit', '-qm', 'gallery deployment')
                self.assertEqual(strict_suite.render_mode_from_change_base(previous), 'omit')
                for path in ('src/agent_ee/candidate.py', 'src/agent_ee/executor.py'):
                    with self.subTest(path=path):
                        previous = git('rev-parse', 'HEAD')
                        (root / path).write_text('generic change\n')
                        git('add', path)
                        git('commit', '-qm', 'unrelated source')
                        self.assertEqual(strict_suite.render_mode_from_change_base(previous), 'omit')


class RequiredInputTests(unittest.TestCase):
    def env(self, **values):
        clean = {k: v for k, v in os.environ.items()
                 if k not in ('AGENT_EE_REQUIRE_TOOLCHAIN', 'AGENT_EE_DECLARED_GAPS')}
        return patch.dict(os.environ, {**clean, **values}, clear=True)

    def test_absent_required_input_skips_and_always_fails_strict(self):
        with self.env(), self.assertRaisesRegex(unittest.SkipTest, '^absent$'):
            toolchain.unavailable('fome-mc-store', 'absent')
        for declared in ({}, {'AGENT_EE_DECLARED_GAPS': 'fome-mc-store'}):
            for gap in toolchain.REQUIRED_INPUTS:
                with self.env(AGENT_EE_REQUIRE_TOOLCHAIN='1', **declared), self.assertRaisesRegex(
                        AssertionError, f'^AGENT_EE_REQUIRE_TOOLCHAIN=1: required input {gap} missing: absent$'):
                    toolchain.unavailable(gap, 'absent')

    def test_unknown_partition_is_refused(self):
        # pod-retained-inputs was removed once the J1 tests read AGENT_EE_J1_DRAWING directly.
        for gap in ('toolchain', 'pod-retained-inputs'):
            with self.env(), self.assertRaisesRegex(ValueError, f'unknown required-input partition: {gap}'):
                toolchain.unavailable(gap, 'absent')
        self.assertEqual(toolchain.REQUIRED_INPUTS, ('fome-mc-store', 'pcbschemagen-verifier'))

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
            (repo / 'owned.txt').write_text('moved on\n')
            git('commit', '-q', '-am', 'later')
            later = git('rev-parse', 'HEAD')
            with self.env(AGENT_EE_REQUIRE_TOOLCHAIN='1'):
                # The scratch checkout is at the pinned commit, not the store's moved HEAD.
                checkout = toolchain.fome_mc_checkout(repo, Path(tmp) / 'pinned', commit[:12])
                self.assertEqual(subprocess.run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], env=env,
                                                capture_output=True, text=True, check=True).stdout.strip(), commit)
                self.assertEqual(git('rev-parse', 'HEAD'), later)
                self.assertEqual(git('status', '--porcelain'), '')
                with self.assertRaisesRegex(AssertionError, 'required input fome-mc-store missing'):
                    toolchain.fome_mc_checkout(repo, Path(tmp) / 'absent-commit',
                                               '0123456789abcdef0123456789abcdef01234567')
                self.assertFalse((Path(tmp) / 'absent-commit').exists())


class WorkflowInputTests(unittest.TestCase):
    """AGENTEE-619: GitHub checks recency; admitted local execution owns tests."""
    WORKFLOW = (ROOT / '.github/workflows/ci.yml').read_text()

    def full_gate_script(self):
        """Extract the sole recency command and reject extra executable YAML."""
        import re
        lines = self.WORKFLOW.splitlines()
        top = [line for line in lines if line and not line[0].isspace() and not line.startswith('#')]
        self.assertEqual(len(top), 5)
        self.assertEqual({line.split(':', 1)[0] for line in top},
                         {'name', 'on', 'permissions', 'env', 'jobs'})
        jobs = lines[lines.index('jobs:') + 1:]
        self.assertEqual([line for line in jobs if line.strip() and not line.startswith('    ')],
                         ['  recency:'])
        fields = [line.strip().split(':', 1)[0] for line in jobs
                  if line.strip() and len(line) - len(line.lstrip()) == 4]
        self.assertEqual(sorted(fields), ['name', 'runs-on', 'steps', 'timeout-minutes'])
        steps = [line for line in jobs if line.startswith('      - ')]
        self.assertEqual(len(steps), 2)
        self.assertTrue(steps[0].startswith('      - uses: actions/checkout@'))
        self.assertEqual(steps[1], '      - name: Check local full-suite recency')
        runs = [i for i, line in enumerate(jobs) if re.match(r"\s*(?:run|'run'|\"run\")\s*:", line)]
        self.assertEqual(len(runs), 1)
        self.assertEqual(jobs[runs[0]], '        run: |')
        fields = [line.strip().split(':', 1)[0] for line in jobs
                  if line.strip() and len(line) - len(line.lstrip()) == 8]
        self.assertEqual(sorted(fields), ['env', 'run'])
        body = jobs[runs[0] + 1:]
        self.assertTrue(body and any(line.strip() for line in body))
        self.assertTrue(all(not line.strip() or line.startswith('          ') for line in body))
        return '\n'.join(line[10:] if line.strip() else '' for line in body) + '\n'

    def test_full_gate_extraction_refuses_missing_ambiguous_or_unsupported_blocks(self):
        original = self.WORKFLOW
        for broken in (original.replace('  recency:', '  other-job:'),
                       original + '\n  recency:\n    steps: []\n',
                       original.replace('        run: |', '        run: true'),
                       original + '        "run": |\n          exit 0\n',
                       original + '        shell: sh\n',
                       original + '      - run: exit 0\n',
                       original.replace('          python3', '         python3')):
            with self.subTest(workflow=broken[-200:]):
                self.WORKFLOW = broken
                with self.assertRaises((AssertionError, ValueError)):
                    self.full_gate_script()
        self.WORKFLOW = original

    def test_supported_triggers_default_to_full_and_affected_is_explicit(self):
        triggers = self.WORKFLOW.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertIn('schedule:', triggers)
        self.assertIn('workflow_dispatch:', triggers)
        for forbidden in ('push:', 'pull_request:', 'paths-ignore:', 'inputs:'):
            self.assertNotIn(forbidden, triggers)
        script = self.full_gate_script()
        self.assertIn('scripts/check_full_suite_recency.py', script)
        self.assertIn('--commit "${{ github.sha }}"', script)
        self.assertNotIn('--max-hours', script)
        for forbidden in ('strict_suite.py', 'unittest', 'pytest', 'docker', 'ae-ci-exec'):
            self.assertNotIn(forbidden, script)

    def test_cheap_checks_precede_native_setup_and_full_gate_refuses_non_success(self):
        # Exercise the actual workflow command against a controllable GitHub API boundary.
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            (root / 'scripts/check_full_suite_recency.py').symlink_to(ROOT / 'scripts/check_full_suite_recency.py')
            gh = root / 'gh'
            gh.write_text('#!/bin/sh\ncat "$RECENCY_STATUSES"\n')
            gh.chmod(0o755)
            statuses = root / 'statuses.json'
            script = self.full_gate_script().replace('${{ github.sha }}', 'a' * 40)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                       RECENCY_STATUSES=str(statuses))
            for hours, state, expected in ((2, 'success', 0), (48, 'success', 0),
                                           (2, 'pending', 1), (2, 'error', 1)):
                created = datetime.now(timezone.utc) - timedelta(hours=hours)
                statuses.write_text(json.dumps([{
                    'context': 'local-full-suite', 'state': state,
                    'created_at': created.isoformat(),
                    'description': f"strict-complete {'a' * 40} {created.strftime('%Y-%m-%dT%H:%M:%SZ')} problems=0",
                }]))
                proc = subprocess.run(['bash', '--noprofile', '--norc', '-e', '-c', script],
                                      cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(proc.returncode, expected, proc.stderr)
            statuses.write_text('[]')
            proc = subprocess.run(['bash', '--noprofile', '--norc', '-e', '-c', script],
                                  cwd=root, env=env, capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)

    def test_superseded_runs_cancel_within_gate_mode_and_cannot_accept_cancellation(self):
        # Remote recency checks cannot cancel a local suite. Its real lock serializes starts.
        import fcntl
        import importlib.util
        spec = importlib.util.spec_from_file_location('nightly_lock_test', ROOT / 'scripts/nightly_full_suite.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        self.assertNotIn('cancel-in-progress:', self.WORKFLOW)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            with (state / 'job.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(runner.subprocess, 'run', side_effect=AssertionError('overlapping run')):
                    self.assertEqual(runner.run_nightly_suite(state, state_dir=state), 0)

    def test_fome_mc_fetch_is_the_tests_pin_without_credentials(self):
        # Input declarations remain for ae-ci-exec until the runtime owner replaces its contract.
        self.assertIn(f'\n  FOME_MC_COMMIT: {toolchain.FOME_MC_COMMIT}\n', self.WORKFLOW)
        self.assertIn('\n  FOME_MC_STORE: build/external/fome-mc\n', self.WORKFLOW)
        self.assertIn('  contents: read\n', self.WORKFLOW)
        self.assertIn('  statuses: read\n', self.WORKFLOW)
        self.assertEqual(self.WORKFLOW.count('uses: actions/checkout@'), 1)
        for absent in ('secrets.', 'git clone', 'credential.helper', 'sudo', 'safe.directory'):
            self.assertNotIn(absent, self.WORKFLOW)
        self.assertIn('build', (ROOT / '.dockerignore').read_text().split())
        self.assertIn('build/', (ROOT / '.gitignore').read_text().split())

    def test_j1_drawing_hash_is_the_decision_source_hash(self):
        decision = json.loads((ROOT / 'fixtures/telemetry-pod/electrical/j1-placement-exclusion.json').read_text())
        self.assertIn(f"\n  J1_DRAWING_SHA256: {decision['sources']['drawing']['sha256']}\n", self.WORKFLOW)
        self.assertNotIn('Pinned external inputs', self.WORKFLOW)

    def test_j1_readme_acquisition_matches_the_workflow(self):
        readme = (ROOT / 'fixtures/telemetry-pod/j1/README.md').read_text()
        command = readme.split('```sh\n', 1)[1].split('```', 1)[0]
        user_agent = "--user-agent 'Mozilla/5.0 (X11; Linux x86_64)'"
        self.assertIn('\n  J1_DRAWING_URL: https://www.farnell.com/cad/2916153.pdf\n', self.WORKFLOW)
        decision = 'fixtures/telemetry-pod/electrical/j1-placement-exclusion.json'
        sha256 = json.loads((ROOT / decision).read_text())['sources']['drawing']['sha256']
        for fragment in (user_agent, '--fail', "--proto '=https'", '--max-time 90 --max-filesize 10000000',
                         'https://www.farnell.com/cad/2916153.pdf', sha256, '| sha256sum --check --strict'):
            self.assertIn(fragment, command)

    def test_strict_suite_mounts_both_inputs_read_only_and_no_pod_override(self):
        # Mount/input custody is the runtime owner's contract; exercise the real delegation boundary.
        try:
            from nightly_support import nightly_fixture
        except ImportError:
            from tests.nightly_support import nightly_fixture
        import importlib.util
        spec = importlib.util.spec_from_file_location('nightly_delegate_test', ROOT / 'scripts/nightly_full_suite.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with nightly_fixture() as case:
            self.assertEqual(runner.run_nightly_suite(
                case['root'], state_dir=case['state_dir'], worktree_dir=case['worktree_dir'],
                skip_plane=True, skip_gh_status=True, initialize_baseline=True), 0)
            args = (case['base'] / 'runner-args').read_text().splitlines()
            self.assertEqual(args[:3], [str(case['worktree_dir']), '--', 'env'])
            self.assertRegex(args[3], r'^AGENT_EE_NIGHTLY_ATTEMPT_ID=[0-9a-f-]{36}$')
            self.assertEqual(args[4:], ['python3', 'scripts/strict_suite.py', '--jobs=2', '--full'])
            self.assertEqual((case['base'] / 'weights').read_text().splitlines(), ['2'])
        for absent in ('AGENT_EE_POD_INPUTS=', 'AGENT_EE_DECLARED_GAPS', 'continue-on-error', 'docker'):
            self.assertNotIn(absent, self.WORKFLOW)


if __name__ == '__main__':
    unittest.main()

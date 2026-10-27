"""Affected gates must preserve unknown dependencies and full release qualification."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import os
import shutil
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('strict_selection', ROOT / 'scripts/strict_selection.py')
selection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(selection)


class PathSelectionTests(unittest.TestCase):
    MODULES = ['test_action_gate', 'test_strict_suite', 'test_strict_selection', 'test_other']

    def test_reviewed_planning_prose_avoids_native_setup(self):
        plan = selection.select_paths(['docs/planning/2026-10-25-recovery/recovery-plan.md'],
                                      ['test_action_gate', 'test_strict_suite'])
        self.assertEqual(plan['mode'], 'cheap')
        self.assertEqual(plan['modules'], [])
        self.assertFalse(plan['native_required'])
        self.assertIn('reviewed narrative documentation', plan['reasons'][0])

    def test_unknown_or_executable_document_data_requires_full_roster(self):
        for path in ('docs/new.md', 'docs/file-origins.json', 'docs/schema.json',
                     'fixtures/rung1/spec.md', 'src/agent_ee/models3d.py',
                     'tests/toolchain.py', '.github/workflows/ci.yml',
                     'scripts/strict_selection.py', 'scripts/strict_suite.py'):
            with self.subTest(path=path):
                plan = selection.select_paths([*selection.NARRATIVE_DOCS, path], self.MODULES)
                self.assertEqual(plan['mode'], 'full')
                self.assertEqual(plan['modules'], sorted(self.MODULES))
                self.assertTrue(plan['native_required'])
                self.assertIn(path, plan['reasons'][-1])

    def test_isolated_gate_change_includes_native_consumer_and_fast_guards(self):
        plan = selection.select_paths(['scripts/action_gate.py'], self.MODULES)
        self.assertEqual(plan['mode'], 'affected')
        self.assertEqual(plan['modules'], ['test_action_gate', 'test_strict_selection', 'test_strict_suite'])
        self.assertTrue(plan['native_required'])
        self.assertIn('consumer', plan['reasons'][0])

    def test_missing_mapped_consumer_and_empty_diff_refuse_narrowing(self):
        for paths, modules in ((['scripts/action_gate.py'], ['test_strict_suite']),
                               ([], self.MODULES)):
            with self.subTest(paths=paths):
                self.assertEqual(selection.select_paths(paths, modules)['mode'], 'full')


class ChangeBaseTests(unittest.TestCase):
    def test_committed_docs_dirty_tree_deleted_data_and_unknown_base(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-select-') as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.run(['git', '-C', tmp, '-c', 'user.name=Test',
                                       '-c', 'user.email=test@example.invalid', *args],
                                      capture_output=True, text=True, check=True).stdout.strip()
            git('init', '-q')
            doc = root / 'docs/planning/2026-10-25-recovery/recovery-plan.md'
            doc.parent.mkdir(parents=True)
            doc.write_text('original\n')
            data = root / 'docs/input.json'
            data.write_text('{}')
            git('add', '.')
            git('commit', '-qm', 'base')
            base = git('rev-parse', 'HEAD')
            doc.write_text('updated\n')
            git('commit', '-qam', 'narrative')
            available = PathSelectionTests.MODULES
            self.assertEqual(selection.from_change_base(root, base, available)['mode'], 'cheap')
            for invalid in (None, '', 'unknown', '--output=/tmp/agent-ee-select-invalid'):
                with self.subTest(base=invalid):
                    self.assertEqual(selection.from_change_base(root, invalid, available)['mode'], 'full')
            (root / 'untracked').write_text('dirty')
            plan = selection.from_change_base(root, base, available)
            self.assertEqual(plan['mode'], 'full')
            self.assertIn('dirty', plan['reasons'][0])
            (root / 'untracked').unlink()
            data.unlink()
            git('commit', '-qam', 'deleted executable data')
            self.assertEqual(selection.from_change_base(root, base, available)['mode'], 'full')

    def test_full_plan_requires_no_native_setup(self):
        proc = subprocess.run([sys.executable, str(ROOT / 'scripts/strict_suite.py'),
                               '--print-selection', '--full'], cwd=ROOT,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        plan = json.loads(proc.stdout)
        self.assertEqual(plan['mode'], 'full')
        self.assertIn('test_strict_selection', plan['modules'])
        self.assertIn('qualification', plan['reasons'][0])

    def test_full_runner_includes_render_tests_despite_ambient_omit(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-full-select-') as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            (root / 'tests').mkdir()
            (root / 'src').mkdir()
            (root / 'tests/__init__.py').write_text('')
            for name in ('strict_suite.py', 'strict_selection.py'):
                shutil.copyfile(ROOT / 'scripts' / name, root / 'scripts' / name)
            (root / 'tests/test_example.py').write_text('''
import unittest
class Example(unittest.TestCase):
    def test_regular(self):
        self.assertTrue(True)
    def test_native(self):
        self.assertTrue(True)
    test_native._agent_ee_native_board_render = True
''')
            env = dict(os.environ, AGENT_EE_REQUIRE_TOOLCHAIN='1',
                       AGENT_EE_NATIVE_RENDER_MODE='omit', PYTHONPATH=str(root))
            command = [sys.executable, str(root / 'scripts/strict_suite.py'), '--jobs', '1']
            for mode in (['--full'], ['--change-base', 'unknown']):
                with self.subTest(mode=mode):
                    proc = subprocess.run([*command, *mode], cwd=root, env=env, capture_output=True, text=True)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertIn('1 modules, 2 tests, no skips', proc.stderr)
                    self.assertNotIn('not selected', proc.stderr)
            refused = subprocess.run([*command, '--full', '--render-mode', 'omit'], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertEqual(refused.returncode, 2)
            self.assertIn('requires native board renders', refused.stderr)


if __name__ == '__main__':
    unittest.main()

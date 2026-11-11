"""Tests for Lane Gate Planner (AGENTEE-614 Part 1 / Defect #42)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import lane_gate_planner


class LaneGatePlannerTests(unittest.TestCase):
    def test_execution_image_changes_require_every_test_module(self):
        # AGENTEE-618: updated Poppler broke consumers outside the selected lane.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tests = root / 'tests'
            tests.mkdir()
            for name in ('test_file_origins.py', 'test_unrelated.py', 'helper.py'):
                (tests / name).write_text('')
            for changed in ('Dockerfile', 'docker/install.sh', 'docker/removed/config',
                            '.dockerignore', str(root / 'Dockerfile')):
                with self.subTest(changed=changed):
                    plan = lane_gate_planner.plan_lane_gate(root, [changed])
                    self.assertEqual(plan['mode'], 'full')
                    self.assertEqual(plan['modules'], ['test_file_origins', 'test_unrelated'])
                    self.assertTrue(plan['native_required'])
                    self.assertFalse(plan['qualifying'])
                    self.assertTrue(any('full suite required' in reason for reason in plan['reasons']))

    def test_local_decoupling_returns_architecture_boundaries_and_dependents(self):
        """Diff touching local_decoupling.py returns test_architecture_boundaries and dependents."""
        plan = lane_gate_planner.plan_lane_gate(
            ROOT,
            changed_files=['src/agent_ee/local_decoupling.py']
        )
        self.assertEqual(plan['schema'], 'agent-ee.lane-gate-plan/1')
        self.assertEqual(plan['qualification'], 'non-qualifying')
        self.assertFalse(plan['qualifying'])
        self.assertTrue(plan['native_required'])

        modules = set(plan['modules'])
        self.assertIn('test_architecture_boundaries', modules)
        self.assertIn('test_local_decoupling', modules)
        self.assertIn('test_placement_job_guards', modules)
        for scanner in lane_gate_planner.CORE_SCANNERS:
            self.assertIn(scanner, modules)

    def test_touched_test_module_included(self):
        """Touched test module in tests/test_*.py is included directly alongside scanners."""
        plan = lane_gate_planner.plan_lane_gate(
            ROOT,
            changed_files=['tests/test_fab_consistency.py']
        )
        modules = set(plan['modules'])
        self.assertIn('test_fab_consistency', modules)
        for scanner in lane_gate_planner.CORE_SCANNERS:
            self.assertIn(scanner, modules)
        self.assertEqual(plan['analysis']['touched_test_modules'], ['test_fab_consistency'])

    def test_multi_hop_transitive_dependents(self):
        """Local return seed changes propagate transitively to test_local_return_seed and guards."""
        plan = lane_gate_planner.plan_lane_gate(
            ROOT,
            changed_files=['src/agent_ee/local_return_seed.py']
        )
        modules = set(plan['modules'])
        self.assertIn('test_local_return_seed', modules)
        self.assertIn('test_placement_job_guards', modules)
        self.assertIn('test_architecture_boundaries', modules)

    def test_plan_json_schema_and_non_qualifying_label(self):
        """Plan JSON schema must strictly match agent-ee.lane-gate-plan/1 with non-qualifying label."""
        plan = lane_gate_planner.plan_lane_gate(
            ROOT,
            changed_files=['src/agent_ee/board_text.py'],
            change_base='origin/main',
            head='HEAD'
        )
        self.assertEqual(plan['schema'], 'agent-ee.lane-gate-plan/1')
        self.assertEqual(plan['qualification'], 'non-qualifying')
        self.assertIs(plan['qualifying'], False)
        self.assertEqual(plan['mode'], 'affected-lane-gate')
        self.assertEqual(plan['inputs']['change_base'], 'origin/main')
        self.assertEqual(plan['inputs']['head'], 'HEAD')
        self.assertIn('src/agent_ee/board_text.py', plan['inputs']['changed_files'])
        self.assertIn('test_board_text', plan['modules'])
        self.assertIn('test_native_run_guard', plan['modules'])

    def test_write_plan_and_gate_log(self):
        """CLI flags --write-plan and --gate-log create properly formatted JSON files."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan_file = tmp_path / 'custom-plan.json'
            gate_log = tmp_path / 'build' / 'gate.log'

            ret = lane_gate_planner.main([
                '--root', str(ROOT),
                '--diff-paths', 'tests/test_toolchain.py',
                '--write-plan', str(plan_file),
                '--gate-log', str(gate_log),
                '--format', 'json'
            ])
            self.assertEqual(ret, 0)
            self.assertTrue(plan_file.exists())
            written_plan = json.loads(plan_file.read_text())
            self.assertEqual(written_plan['schema'], 'agent-ee.lane-gate-plan/1')
            self.assertIn('test_toolchain', written_plan['modules'])

            sidecar_log = tmp_path / 'build' / 'gate.log.plan.json'
            self.assertTrue(sidecar_log.exists())
            sidecar_plan = json.loads(sidecar_log.read_text())
            self.assertEqual(sidecar_plan, written_plan)

    def test_cli_output_formats(self):
        """CLI respects space, lines, and json output formats."""
        for fmt in ('space', 'lines', 'json'):
            with self.subTest(fmt=fmt):
                proc = subprocess.run([
                    sys.executable, str(ROOT / 'scripts/lane_gate_planner.py'),
                    '--root', str(ROOT),
                    '--diff-paths', 'tests/test_fab_consistency.py',
                    '--format', fmt
                ], capture_output=True, text=True, check=True)
                output = proc.stdout.strip()
                if fmt == 'json':
                    parsed = json.loads(output)
                    self.assertEqual(parsed['schema'], 'agent-ee.lane-gate-plan/1')
                elif fmt == 'lines':
                    lines = output.splitlines()
                    self.assertIn('test_fab_consistency', lines)
                    self.assertIn('test_architecture_boundaries', lines)
                    self.assertNotIn('test_file_origins', lines)
                    self.assertNotIn('test_tool_source', lines)
                else:  # space
                    tokens = output.split()
                    self.assertIn('test_fab_consistency', tokens)
                    self.assertIn('test_architecture_boundaries', tokens)
                    self.assertNotIn('test_file_origins', tokens)
                    self.assertNotIn('test_tool_source', tokens)

    def test_git_change_base_and_dirty_handling(self):
        """Unknown change base reports error; clean git diff against HEAD works."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_repo = Path(tmp)
            subprocess.run(['git', 'init'], cwd=tmp_repo, capture_output=True, check=True)
            subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=tmp_repo, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Tester'], cwd=tmp_repo, check=True)
            dummy = tmp_repo / 'README.md'
            dummy.write_text('initial')
            subprocess.run(['git', 'add', 'README.md'], cwd=tmp_repo, check=True)
            subprocess.run(['git', 'commit', '-m', 'initial'], cwd=tmp_repo, check=True)

            # Unknown base returns error code 1 with --allow-dirty
            proc = subprocess.run([
                sys.executable, str(ROOT / 'scripts/lane_gate_planner.py'),
                '--root', str(tmp_repo),
                '--change-base', 'nonexistent_commit_abcdef1234567890',
                '--allow-dirty'
            ], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn('unknown change base', proc.stderr)

            # Clean commit HEAD works without --allow-dirty
            proc_clean = subprocess.run([
                sys.executable, str(ROOT / 'scripts/lane_gate_planner.py'),
                '--root', str(tmp_repo),
                '--change-base', 'HEAD'
            ], capture_output=True, text=True)
            self.assertEqual(proc_clean.returncode, 0)

            # Dirty checkout without --allow-dirty refuses
            dirty_marker = tmp_repo / 'untracked.txt'
            dirty_marker.write_text('dirty')
            proc_dirty = subprocess.run([
                sys.executable, str(ROOT / 'scripts/lane_gate_planner.py'),
                '--root', str(tmp_repo),
                '--change-base', 'HEAD'
            ], capture_output=True, text=True)
            self.assertEqual(proc_dirty.returncode, 1)
            self.assertIn('dirty checkout cannot justify', proc_dirty.stderr)

            # Dirty checkout with --allow-dirty succeeds
            proc_allowed = subprocess.run([
                sys.executable, str(ROOT / 'scripts/lane_gate_planner.py'),
                '--root', str(tmp_repo),
                '--change-base', 'HEAD',
                '--allow-dirty'
            ], capture_output=True, text=True)
            self.assertEqual(proc_allowed.returncode, 0)

    def test_core_scanners_exclude_tool_source_and_select_it_by_dependency(self):
        """test_tool_source is not in CORE_SCANNERS, but still runs when imported or touched."""
        self.assertEqual(
            lane_gate_planner.CORE_SCANNERS,
            ['test_architecture_boundaries', 'test_native_run_guard', 'test_tree_hygiene'],
        )
        ordinary = lane_gate_planner.plan_lane_gate(ROOT, ['tests/test_fab_consistency.py'])
        self.assertNotIn('test_tool_source', ordinary['modules'])
        self.assertNotIn('test_file_origins', ordinary['modules'])

        src_leaf = lane_gate_planner.plan_lane_gate(ROOT, ['src/agent_ee/acquisition.py'])
        self.assertIn('test_acquisition_film', src_leaf['modules'])
        self.assertNotIn('test_tool_source', src_leaf['modules'])
        self.assertNotIn('test_file_origins', src_leaf['modules'])

        dep_plan = lane_gate_planner.plan_lane_gate(ROOT, ['src/agent_ee/stage_receipts.py'])
        self.assertIn('test_tool_source', dep_plan['analysis']['reverse_dependent_test_modules'])
        self.assertIn('test_tool_source', dep_plan['modules'])
        self.assertNotIn('test_file_origins', dep_plan['modules'])

        touched_plan = lane_gate_planner.plan_lane_gate(ROOT, ['tests/test_tool_source.py'])
        self.assertIn('test_tool_source', touched_plan['modules'])

    def test_origins_scanner_selected_for_origin_inputs_and_non_code_paths(self):
        """test_file_origins runs for origin code/data and non-code paths, not owned code edits."""
        for trigger in (
            'scripts/check_origins.py',
            'tests/test_file_origins.py',
            'docs/file-origins.json',
            'docs/relocated-artifacts.json',
            '.gitignore',
            'pyproject.toml',
            'docs/architecture.md',
            'fixtures/rc/control.scope.json',
            'tests/fixtures/sample.py',
        ):
            with self.subTest(trigger=trigger):
                plan = lane_gate_planner.plan_lane_gate(ROOT, [trigger])
                self.assertIn('test_file_origins', plan['modules'])

        for code_edit in (
            'src/agent_ee/local_decoupling.py',
            'tests/test_fab_consistency.py',
            'scripts/lane_gate_planner.py',
            'crates/placement_engine/src/main.rs',
        ):
            with self.subTest(code_edit=code_edit):
                plan = lane_gate_planner.plan_lane_gate(ROOT, [code_edit])
                self.assertNotIn('test_file_origins', plan['modules'])

    def test_git_change_base_selects_origins_on_added_or_renamed_code_paths(self):
        """Git --change-base detects added/renamed/deleted code paths and selects test_file_origins."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / 'src/agent_ee').mkdir(parents=True)
            (repo / 'tests').mkdir()
            for name in ('test_file_origins.py', 'test_tree_hygiene.py'):
                (repo / 'tests' / name).write_text('')
            (repo / 'src/agent_ee/existing.py').write_text('X = 1\n')
            (repo / 'src/agent_ee/old_name.py').write_text('Y = 1\n')
            subprocess.run(['git', 'init'], cwd=repo, capture_output=True, check=True)
            subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=repo, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Tester'], cwd=repo, check=True)
            subprocess.run(['git', 'add', '.'], cwd=repo, check=True)
            subprocess.run(['git', 'commit', '-m', 'init'], cwd=repo, capture_output=True, check=True)

            # In-place modification of existing owned .py does not select test_file_origins
            (repo / 'src/agent_ee/existing.py').write_text('X = 2\n')
            subprocess.run(['git', 'commit', '-am', 'modify'], cwd=repo, capture_output=True, check=True)
            mod_out = subprocess.run([
                sys.executable, str(ROOT / 'scripts/lane_gate_planner.py'),
                '--root', str(repo), '--change-base', 'HEAD~1', '--format', 'json',
            ], capture_output=True, text=True, check=True).stdout
            self.assertNotIn('test_file_origins', json.loads(mod_out)['modules'])

            # Adding a new .py selects test_file_origins
            (repo / 'src/agent_ee/brand_new.py').write_text('Z = 1\n')
            subprocess.run(['git', 'add', 'src/agent_ee/brand_new.py'], cwd=repo, check=True)
            subprocess.run(['git', 'commit', '-m', 'add'], cwd=repo, capture_output=True, check=True)
            add_out = subprocess.run([
                sys.executable, str(ROOT / 'scripts/lane_gate_planner.py'),
                '--root', str(repo), '--change-base', 'HEAD~1', '--format', 'json',
            ], capture_output=True, text=True, check=True).stdout
            self.assertIn('test_file_origins', json.loads(add_out)['modules'])

            # Renaming a .py selects test_file_origins
            subprocess.run(['git', 'mv', 'src/agent_ee/old_name.py', 'src/agent_ee/new_name.py'],
                           cwd=repo, check=True)
            subprocess.run(['git', 'commit', '-m', 'rename'], cwd=repo, capture_output=True, check=True)
            ren_out = subprocess.run([
                sys.executable, str(ROOT / 'scripts/lane_gate_planner.py'),
                '--root', str(repo), '--change-base', 'HEAD~1', '--format', 'json',
            ], capture_output=True, text=True, check=True).stdout
            self.assertIn('test_file_origins', json.loads(ren_out)['modules'])


if __name__ == '__main__':
    unittest.main()

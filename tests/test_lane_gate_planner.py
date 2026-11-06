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
                    self.assertIn('test_file_origins', lines)
                else:  # space
                    tokens = output.split()
                    self.assertIn('test_fab_consistency', tokens)
                    self.assertIn('test_file_origins', tokens)

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


if __name__ == '__main__':
    unittest.main()

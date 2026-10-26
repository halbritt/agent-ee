"""Fresh-native rule agreement and a zone-free placement capture boundary."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from agent_ee import placement_job as job
from tests.test_placement_job import Fixture, _box, build_board, groups, intent, MM
from tests.test_placement_job_clearance import evaluate


# Deliberately independent of capture and its helpers: one cold native load.
ORACLE = '''
import json, pcbnew, sys
board = pcbnew.LoadBoard(sys.argv[1])
s = board.GetDesignSettings()
print(json.dumps({
    'board': int(s.m_MinClearance),
    'default': int(s.m_NetSettings.GetDefaultNetclass().GetClearance()),
    'hole': int(s.m_HoleClearance),
    'hole_pair': int(s.m_HoleToHoleMin),
    'edge': int(s.m_CopperEdgeClearance),
    'mask': int(s.m_SolderMaskMinWidth)}))
'''


class FreshRuleTests(Fixture):
    def setUp(self):
        super().setUp()
        self.capture = lambda path, declared, grouped: job.capture(path, job.normalize_intent(declared, grouped))
        # Diagnostic rerun of these exact contracts against the held parent.
        old = os.environ.get('AGENT_EE_OLD_PLACEMENT_JOB')
        if old:
            spec = importlib.util.spec_from_file_location('agent_ee.old_placement_job', old)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.capture = module.capture

    def oracle(self, path):
        done = subprocess.run([sys.executable, '-c', ORACLE, str(path)],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def assert_native_rules(self, path):
        expected = self.oracle(path)
        problem, _ = self.capture(path, intent(), groups())
        actual = dict(problem['rules'])
        actual['hole_clearance_nm'] = next(
            f['holes_local_nm'][0]['clearance_nm'] for f in problem['footprints']
            if f['native_ref'] == 'H1')
        oracle = {'copper_clearance_nm': max(expected['board'], expected['default']),
                  'hole_to_hole_clearance_nm': expected['hole_pair'],
                  'copper_edge_clearance_nm': expected['edge'],
                  'mask_min_web_nm': expected['mask'], 'hole_clearance_nm': expected['hole']}
        print('FRESH_NATIVE_RULES ' + json.dumps({'path': str(path), 'native': expected,
              'captured': actual, 'expected': oracle}), flush=True)
        self.assertEqual(actual, oracle)
        return problem

    def project_rules(self, value):
        return {'board': {'design_settings': {'rules': {
            key: value for key in ('min_clearance', 'min_hole_clearance',
                                  'min_hole_to_hole', 'min_copper_edge_clearance')}}},
            'net_settings': {'classes': [{'name': 'Default', 'clearance': value}]}}

    def cold_path(self, name):
        # Generate at another path so SaveBoard cannot warm the tested project.
        path = self.dir / f'{name}.kicad_pcb'
        path.write_bytes(self.board.read_bytes())
        return path

    def test_weak_and_strong_explicit_to_omitted_all_consumed_rules(self):
        import pcbnew
        fields = ('min_clearance', 'min_hole_clearance', 'min_hole_to_hole',
                  'min_copper_edge_clearance', 'solder_mask_min_width', 'Default clearance')
        for value in (0.001, 1.4):
            for field in fields:
                with self.subTest(value=value, field=field):
                    path = self.cold_path(f'{value}-{field}')
                    project = self.project_rules(value)
                    if field == 'min_clearance':
                        project['net_settings']['classes'][0]['clearance'] = 0
                    if field == 'Default clearance':
                        project['board']['design_settings']['rules']['min_clearance'] = 0
                    if field == 'solder_mask_min_width':
                        project['board']['design_settings'][field] = value
                    pro = path.with_suffix('.kicad_pro')
                    pro.write_text(json.dumps(project))
                    warm = pcbnew.LoadBoard(str(path))
                    with self.subTest(stage='explicit'):
                        self.assert_native_rules(path)
                    if field == 'Default clearance':
                        del project['net_settings']['classes'][0]['clearance']
                    elif field == 'solder_mask_min_width':
                        del project['board']['design_settings'][field]
                    else:
                        del project['board']['design_settings']['rules'][field]
                    pro.write_text(json.dumps(project))
                    with self.subTest(stage='omitted'):
                        self.assert_native_rules(path)
                    self.assertIsNotNone(warm)

    def test_absent_project_after_weak_and_strong_loads(self):
        import pcbnew
        for value in (0.001, 1.4):
            with self.subTest(value=value):
                path = self.cold_path(str(value))
                pro = path.with_suffix('.kicad_pro')
                pro.write_text(json.dumps(self.project_rules(value)))
                warm = pcbnew.LoadBoard(str(path))
                self.assert_native_rules(path)
                pro.unlink()
                self.assert_native_rules(path)
                self.assertIsNotNone(warm)

    def test_empty_project_and_missing_default_class_use_native_defaults(self):
        for project in ({}, {'net_settings': {'classes': [{'name': 'Default'}]}}):
            self.project.write_text(json.dumps(project))
            self.assert_native_rules(self.board)

    def test_fractional_native_units_are_not_reinterpreted_by_python(self):
        for value in (0.0000015, 0.1234567):
            with self.subTest(value=value):
                self.project.write_text(json.dumps(self.project_rules(value)))
                self.assert_native_rules(self.board)

    def test_native_board_mask_width_then_omission(self):
        import pcbnew
        for value in (0.001, 1.4):
            with self.subTest(value=value):
                board = pcbnew.LoadBoard(str(self.board))
                board.GetDesignSettings().m_SolderMaskMinWidth = round(value * MM)
                pcbnew.SaveBoard(str(self.board), board)
                path = self.cold_path(f'mask-{value}')
                warm = pcbnew.LoadBoard(str(path))
                self.assertEqual(self.assert_native_rules(path)['rules']['mask_min_web_nm'], round(value * MM))
                node = f'(solder_mask_min_width {value})'
                text = path.read_text()
                self.assertEqual(text.count(node), 1)
                path.write_text(text.replace(node, ''))
                self.assert_native_rules(path)
                self.assertIsNotNone(warm)

    def test_invalid_project_rules_refuse(self):
        for field, value in (('min_clearance', -0.1), ('min_hole_clearance', True),
                             ('min_hole_to_hole', '0.1'), ('min_copper_edge_clearance', None)):
            with self.subTest(field=field):
                project = self.project_rules(0.2)
                project['board']['design_settings']['rules'][field] = value
                self.project.write_text(json.dumps(project))
                with self.assertRaises(job.JobRefusal):
                    job.capture(self.board, job.normalize_intent(intent(), groups()))


class CaptureBoundaryTests(Fixture):
    def test_live_capture_child_is_cancelled_or_times_out_and_reaped(self):
        for mode in ('cancel', 'deadline'):
            with self.subTest(mode=mode):
                started = self.dir / f'{mode}-started'
                shim = self.dir / f'{mode}-python'
                shim.write_text('#!/usr/bin/python3\nimport os,time\nfrom pathlib import Path\n'
                                f'Path({str(started)!r}).write_text(str(os.getpid()))\n'
                                'time.sleep(20)\n')
                shim.chmod(0o755)
                done = threading.Event()
                def cancel():
                    limit = time.monotonic() + 2
                    while not done.wait(0.01) and time.monotonic() < limit:
                        if started.exists():
                            (self.dir / 'CANCEL').touch()
                            return
                thread = threading.Thread(target=cancel) if mode == 'cancel' else None
                if thread:
                    thread.start()
                try:
                    with patch.object(job.sys, 'executable', str(shim)):
                        with self.assertRaisesRegex(job.JobStopped, 'cancelled' if mode == 'cancel' else 'deadline'):
                            job.capture(self.board, job.normalize_intent(intent(), groups()), cancel_dir=self.dir,
                                        clock=job.Clock(3 if mode == 'cancel' else 0.3))
                    self.assertTrue(started.exists())
                    self.assertFalse(Path(f'/proc/{started.read_text()}').exists(), 'owned child was reaped')
                finally:
                    done.set()
                    if thread:
                        thread.join(timeout=3)
                    (self.dir / 'CANCEL').unlink(missing_ok=True)

    def test_deadline_and_cancellation_before_capture(self):
        with self.assertRaisesRegex(job.JobStopped, 'deadline'):
            job.capture(self.board, job.normalize_intent(intent(), groups()), clock=job.Clock(0))
        (self.dir / 'CANCEL').touch()
        with self.assertRaisesRegex(job.JobStopped, 'cancelled'):
            job.capture(self.board, job.normalize_intent(intent(), groups()), cancel_dir=self.dir)

    def test_failed_or_missing_child_output_never_returns_a_problem(self):
        for code, output in ((1, b''), (0, b''), (0, b'{}'), (0, b'null'),
                             (0, b'{"problem":null,"record":null}'),
                             (0, b'{"problem":{},"record":{}}')):
            with self.subTest(code=code, output=output):
                with patch.object(job, '_run_child', return_value=subprocess.CompletedProcess(
                        [], code, output, b'native diagnostic')):
                    with self.assertRaises(job.JobRefusal):
                        job.capture(self.board, job.normalize_intent(intent(), groups()))

    def test_project_change_during_capture_refuses(self):
        def changed(*args, **kwargs):
            self.project.write_text('{"changed":true}')
            output = {'problem': {'schema': job.INPUT_SCHEMA},
                      'record': {'schema': 'agent-ee.placement-job-capture/1'}}
            return subprocess.CompletedProcess([], 0, json.dumps(output).encode(), b'')
        with patch.object(job, '_run_child', side_effect=changed):
            with self.assertRaisesRegex(job.JobRefusal, 'changed during capture'):
                job.capture(self.board, job.normalize_intent(intent(), groups()))


class BoardZoneTests(Fixture):
    def zone_board(self, kind, *, footprint=False):
        import pcbnew as k
        # One movable body, no unrelated placement conflicts.
        board = k.LoadBoard(str(self.board))
        moving = next(f for f in board.GetFootprints() if f.GetReference() == 'R1')
        for fp in list(board.GetFootprints()):
            if fp.m_Uuid.AsString() != moving.m_Uuid.AsString():
                board.Remove(fp)
        for shape in _box(k, moving, k.F_CrtYd, 8 * MM, 4 * MM, 12 * MM, 8 * MM):
            moving.Add(shape)
        zone = k.ZONE(moving if footprint else board)
        zone.SetLayer(k.F_Cu)
        if kind != 'copper':
            zone.SetIsRuleArea(True)
            zone.SetDoNotAllowFootprints(kind == 'keepout')
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in ((7, 3), (13, 3), (13, 9), (7, 9)):
            outline.Append(x * MM, y * MM)
        (moving if footprint else board).Add(zone)
        k.SaveBoard(str(self.board), board)
        return self.board

    def test_native_negative_keepout_old_admits_new_refuses(self):
        path = self.zone_board('keepout')
        report, execution = job.drc(path, self.dir)
        self.assertEqual(execution['exit_code'], 0)
        violations = [v for v in report['violations'] if v['type'] == 'items_not_allowed']
        self.assertTrue(violations, report)
        old_path = os.environ.get('AGENT_EE_OLD_PLACEMENT_JOB')
        if old_path:
            spec = importlib.util.spec_from_file_location('agent_ee.old_placement_job', old_path)
            old = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(old)
            problem, _ = old.capture(path, {}, {'groups': []})
            result = evaluate(problem, [{}])[0]
            self.assertTrue(result['feasible'], result)
            print('OLD_KEEPOUT_FALSE_FEASIBLE ' + json.dumps(result), flush=True)
        with self.assertRaisesRegex(job.JobRefusal, 'board-level zones'):
            job.capture(path, job.normalize_intent({}, {'groups': []}))
        print('KEEPOUT_CONTROL ' + json.dumps({'native': violations, 'exit': execution['exit_code'],
                                             'new': 'refused before solve'}), flush=True)

    def test_copper_and_rule_area_refuse(self):
        for kind in ('copper', 'rule-area'):
            with self.subTest(kind=kind):
                build_board(self.board)
                self.zone_board(kind)
                with self.assertRaisesRegex(job.JobRefusal, 'board-level zones'):
                    job.capture(self.board, job.normalize_intent({}, {'groups': []}))

    def test_footprint_zone_refusal_is_preserved(self):
        self.zone_board('keepout', footprint=True)
        with self.assertRaisesRegex(job.JobRefusal, 'footprint rule areas/zones'):
            job.capture(self.board, job.normalize_intent({}, {'groups': []}))

    def test_zero_zone_positive(self):
        problem, _ = self.problem()
        self.assertEqual(len(problem['footprints']), 6)


if __name__ == '__main__':
    unittest.main()

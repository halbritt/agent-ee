"""Explicit single-ended useful-path matching through public placement and build."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from agent_ee import build_workflow, placement_job as job
from agent_ee.storage import digest, read_json, write_json
from tests.test_placement_job_rules import public_job
from tests.toolchain import require_kicad_route, require_toolchain


SPEC = dict(groups=[['DQ0', 'DQ1', 'DQ2', 'DQ3']], tolerance_mm=.1, amplitude_mm=3)


def prepare(root, checkout):
    shutil.copytree(Path(checkout) / 'fixtures/group_matching', root)
    write_json(root / 'intent.json', dict(fixed_poses=[], edge_connectors=[], proximity=[]))
    write_json(root / 'groups.json', dict(groups=[
        dict(name=f'lane-{i}', anchor=f'J{2*i+1}', refs=[f'J{2*i+1}', f'J{2*i+2}'])
        for i in range(4)]))
    files = dict(board=root / 'group.kicad_pcb', project=root / 'group.kicad_pro',
                 rules=root / 'group.kicad_dru', intent=root / 'intent.json', groups=root / 'groups.json')
    config = dict(schema=job.CONFIG_SCHEMA, budget_s=240,
        inputs={name: dict(path=str(path), sha256=digest(path)) for name, path in files.items()},
        search=dict(seeds=[dict(seed=0, start='scattered')], steps_per_seed=10, shortlist=1),
        router=dict(checkout=str(checkout), grid_step_mm=.1, length_match=copy.deepcopy(SPEC)))
    write_json(root / 'job.json', config)
    return config


class LengthGroupsJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.checkout = require_kicad_route()
        cls.temp = tempfile.TemporaryDirectory(prefix='ee-length-groups-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(os.environ.get('AGENT_EE_LENGTH_EVIDENCE', cls.temp.name))
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.config = prepare(cls.root / 'inputs', cls.checkout)
        cls.done = public_job(cls.root / 'inputs/job.json', cls.root / 'positive')
        cls.report = read_json(cls.root / 'positive/job-report.json')

    def assert_matched(self, report, spec=SPEC):
        self.assertEqual(report['status'], 'completed', report)
        final = report['final']
        self.assertEqual(final['opens'], 0)
        self.assertEqual(final['identity_problems'], [])
        measured = final['length_matching']
        self.assertEqual(measured['status'], 'PASS', measured)
        self.assertEqual(measured['board_sha256'], digest(final['board']))
        self.assertEqual(measured['requirement'], dict(spacing_widths=2, **spec))
        group, = measured['groups']
        self.assertEqual({path['net'] for path in group['paths']}, set(spec['groups'][0]))
        self.assertEqual(len(group['measurements']), 4)
        self.assertEqual(group['skew_violations'], [])
        router = read_json(report['route']['result_path'])
        self.assertEqual(router['validation']['length_matching']['status'], 'PASS')

    def test_public_job_checks_paths_again_after_final_native_save(self):
        self.assertEqual(self.done.returncode, 0, self.done.stdout + self.done.stderr)
        self.assert_matched(self.report)
        final = Path(self.report['final']['board'])
        for name, suffix in [('project', '.kicad_pro'), ('rules', '.kicad_dru')]:
            self.assertEqual(digest(final.with_suffix(suffix)), self.config['inputs'][name]['sha256'])
        self.assertTrue(all(digest(e['path']) == e['sha256'] for e in self.config['inputs'].values()))

    def test_public_job_preserves_option_like_names_and_refuses_changed_group_resume(self):
        root = self.root / 'option-inputs'
        config = prepare(root, self.checkout)
        names = ['-DQ0', '--help', 'DQ*', '/sheet/DQ[3]']
        mapping = root / 'rename.json'
        write_json(mapping, dict(zip(SPEC['groups'][0], names)))
        board = Path(config['inputs']['board']['path'])
        project = board.with_suffix('.kicad_pro').read_bytes()
        subprocess.run([sys.executable, str(Path(self.checkout) / 'src/kicad_route/kicad/rename_nets.py'),
                        str(board), str(mapping)], check=True, capture_output=True, timeout=30)
        board.with_suffix('.kicad_pro').write_bytes(project)
        condition = ' || '.join(f"A.fromTo('J{2*i+1}-1', 'J{2*i+2}-1')" for i in range(4))
        rules = Path(config['inputs']['rules']['path'])
        rules.write_text('(version 1)\n'
            '(rule "signal width floor" (constraint track_width (min 0.25mm)))\n'
            f'(rule "required group skew" (condition {json.dumps(condition)}) (constraint skew (max 0.1mm)))\n')
        for name in ('board', 'rules'):
            config['inputs'][name]['sha256'] = digest(config['inputs'][name]['path'])
        spec = copy.deepcopy(SPEC); spec['groups'] = [names]
        config['router']['length_match'] = spec
        request = root / 'job.json'; write_json(request, config)
        out = self.root / 'option-names'
        done = public_job(request, out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        report = read_json(out / 'job-report.json')
        self.assert_matched(report, spec)
        self.assertTrue(all(digest(e['path']) == e['sha256'] for e in config['inputs'].values()))
        before = {name: digest(out / name) for name in ('checkpoint.json', 'job-report.json')}
        changed = copy.deepcopy(config)
        changed['router']['length_match']['groups'][0][0] = '-MISSING'
        request = root / 'changed.json'; write_json(request, changed)
        refused = public_job(request, out, '--resume')
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('different job identity', refused.stdout + refused.stderr)
        self.assertEqual(before, {name: digest(out / name) for name in before})

    def test_invalid_group_controls_refuse_before_routing(self):
        for index, value in enumerate([None, True, {}, dict(SPEC, unknown=1),
                dict(SPEC, groups=[]), dict(SPEC, groups=[['DQ0']]),
                dict(SPEC, groups=[['DQ0', 'DQ0']]), dict(SPEC, tolerance_mm=True),
                dict(SPEC, amplitude_mm=0), dict(SPEC, spacing_widths=.5)]):
            with self.subTest(value=value):
                config = copy.deepcopy(self.config); config['router']['length_match'] = value
                report = job.run_job(config, self.root / f'invalid-{index}')
                self.assertEqual(report['status'], 'refused', report)
                self.assertIn('length_match', report['reason'])
                self.assertNotIn('route', report)

    def test_changed_groups_cannot_resume_an_existing_job(self):
        config = copy.deepcopy(self.config)
        config['router']['length_match']['tolerance_mm'] = .05
        path = self.root / 'changed.json'; write_json(path, config)
        before = digest(self.root / 'positive/checkpoint.json')
        report_before = digest(self.root / 'positive/job-report.json')
        done = public_job(path, self.root / 'positive', '--resume')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('different job identity', done.stdout + done.stderr)
        self.assertEqual(digest(self.root / 'positive/checkpoint.json'), before)
        self.assertEqual(digest(self.root / 'positive/job-report.json'), report_before)

    def test_connected_loop_is_rejected_even_when_original_native_drc_passes(self):
        config = prepare(self.root / 'loop-inputs', self.checkout)
        board = Path(config['inputs']['board']['path'])
        shutil.copyfile(self.report['final']['board'], board)
        project = board.with_suffix('.kicad_pro').read_bytes()
        subprocess.run([sys.executable, '-c', '''import sys,pcbnew as k
b=k.LoadBoard(sys.argv[1]);tracks=list(b.GetTracks())
code=next(t.GetNetCode() for t in tracks if t.GetNetname()=='DQ1')
for t in tracks:
 if t.GetNetname()=='DQ1':b.Remove(t)
for a,z in [((40,50),(88,50)),((64,50),(64.75,50.661438)),((64.75,50.661438),(65.5,50))]:
 t=k.PCB_TRACK(b);t.SetStart(k.VECTOR2I(k.FromMM(a[0]),k.FromMM(a[1])))
 t.SetEnd(k.VECTOR2I(k.FromMM(z[0]),k.FromMM(z[1])))
 t.SetWidth(k.FromMM(.25));t.SetLayer(k.F_Cu);t.SetNetCode(code);b.Add(t)
k.SaveBoard(sys.argv[1],b)
''', str(board)], check=True, capture_output=True, timeout=30)
        board.with_suffix('.kicad_pro').write_bytes(project)
        config['inputs']['board']['sha256'] = digest(board)
        path = self.root / 'loop.json'; write_json(path, config)
        out = self.root / 'loop'
        done = public_job(path, out)
        self.assertNotEqual(done.returncode, 0)
        report = read_json(out / 'job-report.json')
        self.assertEqual(report['status'], 'verification_failed', report)
        final = report['final']
        self.assertEqual(final['opens'], 0)
        native = read_json(Path(final['board']).parent / 'native-drc.json')
        self.assertFalse([v for v in native['violations'] if v['severity'] == 'error'])
        measured = final['length_matching']
        self.assertEqual(measured['status'], 'FAIL')
        self.assertEqual(measured['board_sha256'], digest(final['board']))
        group, = measured['groups']
        self.assertEqual(len(group['measurements']), 4)
        self.assertEqual(len(group['skew_violations']), 1)
        self.assertIn('actual 48.0000 mm', group['skew_violations'][0]['description'])
        self.assertFalse((out / 'routed-incumbent.json').exists())

    def test_build_producer_carries_matching_into_clean_native_project(self):
        out = self.root / 'build-stage'; inputs = out / 'inputs'
        config = prepare(inputs, self.checkout)
        requirements = out / 'requirements'; requirements.mkdir()
        write_json(requirements / 'scope.json', dict(board='group.kicad_pcb', checks=[]))
        write_json(requirements / 'accounting.json', dict(requirements=[]))
        stage = out / 'placement-routing'; stage.mkdir()
        plan = dict(placement=dict(budget_s=config['budget_s'], search=config['search'],
            router=config['router'], intent='intent.json', groups='groups.json'))
        candidate = build_workflow._execute('placement-routing', plan, inputs, stage,
            [dict(stage='requirements', directory='requirements')], out, diagnostic=True)
        report = read_json(stage / 'placement/job-report.json')
        self.assert_matched(report)
        final = candidate / 'group.kicad_pcb'
        self.assertEqual(digest(final), report['final']['board_sha256'])
        native, _ = job.drc(final, stage, refill=True, label='native-final')
        self.assertEqual(native['violations'], [])
        self.assertEqual(native['unconnected_items'], [])
        for path in inputs.rglob('*'):
            if path.is_file() and (path.suffix == '.kicad_mod' or path.name == 'fp-lib-table'):
                self.assertEqual(digest(path), digest(candidate / path.relative_to(inputs)))


if __name__ == '__main__':
    unittest.main()

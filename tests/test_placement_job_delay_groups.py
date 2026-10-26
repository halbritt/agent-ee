"""Authored native timing profiles survive placement, planes, routing and save."""
import copy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from build_stage_fixture import requirements as retained_requirements

from agent_ee import add_planes, placement_job as job
from agent_ee.storage import digest, read_json, write_json
from tests.test_placement_job_rules import public_job
from tests.toolchain import require_kicad_route, require_toolchain


SPEC = dict(groups=[['DQ0', 'DQ1']], tolerance_ps=1, amplitude_mm=3)


def prepare(root, checkout):
    shutil.copytree(Path(checkout) / 'fixtures/delay_matching', root)
    board = root / 'delay.kicad_pcb'
    project = board.with_suffix('.kicad_pro').read_bytes()
    # Supply planes as captured engineering intent to the normal plane producer.
    # Existing-pour placement remains a separately documented unsupported input.
    subprocess.run([sys.executable, '-c', '''import sys,pcbnew as k
b=k.LoadBoard(sys.argv[1])
for z in list(b.Zones()):b.Remove(z)
k.SaveBoard(sys.argv[1],b,True)
''', str(board)], check=True, capture_output=True, timeout=30)
    board.with_suffix('.kicad_pro').write_bytes(project)
    write_json(root / 'intent.json', dict(fixed_poses=[], edge_connectors=[], proximity=[]))
    write_json(root / 'groups.json', dict(groups=[
        dict(name='outer', anchor='J1', refs=['J1', 'J2']),
        dict(name='inner', anchor='J3', refs=['J3', 'J4']),
        dict(name='reference', anchor='J5', refs=['J5'])]))
    write_json(root / 'planes.json', dict(schema=add_planes.SCHEMA, zones=[
        dict(net='GND', layer=layer, outline='board', clearance_mm=.2, min_width_mm=.15,
             thermal=dict(connection='solid', gap_mm=.3, spoke_width_mm=.3),
             priority=0, island_removal='always') for layer in ('In1.Cu', 'In3.Cu')]))
    files = dict(board=board, project=board.with_suffix('.kicad_pro'), rules=board.with_suffix('.kicad_dru'),
                 intent=root/'intent.json', groups=root/'groups.json', planes=root/'planes.json')
    config = dict(schema=job.CONFIG_SCHEMA, budget_s=240,
        inputs={name: dict(path=str(path), sha256=digest(path)) for name, path in files.items()},
        search=dict(seeds=[dict(seed=0, start='scattered')], steps_per_seed=10, shortlist=1),
        router=dict(checkout=str(checkout), grid_step_mm=.1, length_match=copy.deepcopy(SPEC)))
    write_json(root / 'job.json', config)
    return config


class DelayGroupsJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.checkout = require_kicad_route()
        cls.temp = tempfile.TemporaryDirectory(prefix='ee-delay-groups-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(os.environ.get('AGENT_EE_DELAY_EVIDENCE', cls.temp.name))
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.config = prepare(cls.root/'inputs', cls.checkout)
        cls.done = public_job(cls.root/'inputs/job.json', cls.root/'positive')
        cls.report = read_json(cls.root/'positive/job-report.json')

    def assert_matched(self, report):
        self.assertEqual(report['status'], 'completed', report)
        final = report['final']
        self.assertEqual(final['opens'], 0)
        self.assertEqual(final['identity_problems'], [])
        self.assertEqual(final['planes']['status'], 'PASS')
        measured = final['length_matching']
        self.assertEqual(measured['status'], 'PASS', measured)
        self.assertEqual(measured['board_sha256'], digest(final['board']))
        self.assertEqual(measured['project_sha256'], digest(Path(final['board']).with_suffix('.kicad_pro')))
        self.assertEqual(measured['requirement'], dict(spacing_widths=2, **SPEC))
        group, = measured['groups']
        self.assertEqual(len(group['measurements']), 2)
        self.assertEqual(group['skew_violations'], [])
        self.assertEqual(report['preflight']['delay_model']['status'], 'PASS')

    def test_public_job_grades_saved_native_timing(self):
        self.assertEqual(self.done.returncode, 0, self.done.stdout+self.done.stderr)
        self.assert_matched(self.report)
        self.assertTrue(all(digest(e['path']) == e['sha256'] for e in self.config['inputs'].values()))

    def test_connected_delay_violation_fails(self):
        config = copy.deepcopy(self.config)
        config['router']['length_match']['amplitude_mm'] = .001
        path, out = self.root/'insufficient.json', self.root/'insufficient'
        write_json(path, config); done = public_job(path, out)
        self.assertNotEqual(done.returncode, 0)
        report = read_json(out/'job-report.json')
        self.assertEqual(report['status'], 'verification_failed', report)
        self.assertEqual(report['final']['opens'], 0)
        self.assertEqual(report['final']['length_matching']['status'], 'FAIL')
        self.assertFalse((out/'routed-incumbent.json').exists())

    def test_missing_dielectric_refuses_before_native_defaulting(self):
        config = prepare(self.root/'missing-inputs', self.checkout)
        board = Path(config['inputs']['board']['path'])
        board.write_text(board.read_text().replace('(epsilon_r 4)', '', 1))
        config['inputs']['board']['sha256'] = digest(board)
        path, out = self.root/'missing.json', self.root/'missing'
        write_json(path, config); done = public_job(path, out)
        self.assertNotEqual(done.returncode, 0)
        report = read_json(out/'job-report.json')
        self.assertIn('original delay model is unproven', report['reason'])
        self.assertIn('epsilon_r', report['reason'])
        self.assertNotIn('route', report)

    def test_changed_delay_requirement_cannot_resume(self):
        config = copy.deepcopy(self.config); config['router']['length_match']['tolerance_ps'] = .5
        path = self.root/'changed.json'; write_json(path, config)
        before = digest(self.root/'positive/checkpoint.json')
        done = public_job(path, self.root/'positive', '--resume')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('different job identity', done.stdout+done.stderr)
        self.assertEqual(digest(self.root/'positive/checkpoint.json'), before)

    def test_build_producer_preserves_profiles_rules_and_libraries(self):
        out = self.root/'build-stage'; inputs = out/'inputs'
        config = prepare(inputs, self.checkout)
        attempt = retained_requirements(out, inputs, dict(board='delay.kicad_pcb', checks=[]), dict(requirements=[]))
        stage = attempt.directory
        plan = dict(placement=dict(budget_s=config['budget_s'], search=config['search'],
            router=config['router'], intent='intent.json', groups='groups.json', planes='planes.json'))
        candidate = attempt.execute(plan, inputs, diagnostic=True)
        report = read_json(stage/'placement/job-report.json'); self.assert_matched(report)
        native, _ = job.drc(candidate/'delay.kicad_pcb', stage, refill=True, label='native-final')
        self.assertEqual(native['violations'], [])
        self.assertEqual(native['unconnected_items'], [])
        for path in inputs.rglob('*'):
            if path.is_file() and (path.suffix in ('.kicad_mod', '.kicad_pro', '.kicad_dru') or path.name == 'fp-lib-table'):
                self.assertEqual(digest(path), digest(candidate/path.relative_to(inputs)))


if __name__ == '__main__':
    unittest.main()

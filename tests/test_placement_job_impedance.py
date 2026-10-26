"""Nominal pair impedance and actual filled reference through the normal job."""
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


SPEC = dict(target_ohm=100, tolerance_percent=10, reference_net='GND', max_uncoupled_mm=3)


def prepare(root, checkout):
    root.mkdir(parents=True)
    for suffix in ('.kicad_pcb', '.kicad_pro', '.kicad_dru'):
        shutil.copy2(Path(checkout) / 'fixtures/pair_impedance' / ('board' + suffix), root / ('board' + suffix))
    # The job must create the reference through its normal plane producer.
    subprocess.run([sys.executable, '-c', '''import sys,pcbnew as k
b=k.LoadBoard(sys.argv[1])
for z in list(b.Zones()):b.Remove(z)
k.SaveBoard(sys.argv[1],b)
''', str(root / 'board.kicad_pcb')], check=True, capture_output=True, timeout=30)
    write_json(root / 'intent.json', dict(fixed_poses=[], edge_connectors=[], proximity=[]))
    write_json(root / 'groups.json', dict(groups=[dict(name='interface', anchor='J1', refs=['J1', 'J2']),
                                                  dict(name='reference', anchor='G1', refs=['G1', 'G2'])]))
    write_json(root / 'planes.json', {'schema': add_planes.SCHEMA, 'zones': [
        {'net': 'GND', 'layer': 'In1.Cu', 'outline': 'board', 'clearance_mm': .2, 'min_width_mm': .2,
         'thermal': {'connection': 'solid', 'gap_mm': .3, 'spoke_width_mm': .3},
         'priority': 0, 'island_removal': 'always'}]})
    files = {name: root / path for name, path in dict(board='board.kicad_pcb', project='board.kicad_pro',
             rules='board.kicad_dru', intent='intent.json', groups='groups.json', planes='planes.json').items()}
    config = dict(schema=job.CONFIG_SCHEMA, budget_s=240,
        inputs={name: dict(path=str(path), sha256=digest(path)) for name, path in files.items()},
        search=dict(seeds=[dict(seed=0, start='scattered')], steps_per_seed=10, shortlist=1),
        router=dict(checkout=str(checkout), grid_step_mm=.025, pair_layers=['F.Cu'], pair_impedance=SPEC.copy()))
    write_json(root / 'job.json', config)
    return config


class PairImpedanceJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        checkout = require_kicad_route()
        cls.temp = tempfile.TemporaryDirectory(prefix='ee-pair-impedance-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(os.environ.get('AGENT_EE_IMPEDANCE_EVIDENCE', cls.temp.name))
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.config = prepare(cls.root / 'inputs', checkout)
        cls.done = public_job(cls.root / 'inputs/job.json', cls.root / 'positive')
        cls.report = read_json(cls.root / 'positive/job-report.json')

    def test_public_job_checks_actual_geometry_after_final_native_fill(self):
        self.assertEqual(self.done.returncode, 0, self.done.stdout + self.done.stderr)
        report = self.report
        self.assertEqual(report['status'], 'completed', report)
        self.assertEqual(report['preflight']['impedance_stackup']['board_sha256'],
                         self.config['inputs']['board']['sha256'])
        final = report['final']
        self.assertEqual(final['opens'], 0)
        self.assertEqual(final['identity_problems'], [])
        self.assertEqual(final['planes']['status'], 'PASS')
        self.assertEqual(final['impedance']['status'], 'PASS')
        self.assertEqual(final['impedance']['board_sha256'], digest(final['board']))
        self.assertEqual(final['impedance']['plan']['requirement'], SPEC)
        for row in final['impedance']['nets']:
            self.assertGreater(row['coupled_mm'], 7)
            self.assertLess(row['uncoupled_mm'], 3)
        drc = read_json(Path(final['board']).parent / 'native-drc.json')
        self.assertEqual(drc['violations'], [])
        self.assertTrue(all(digest(e['path']) == e['sha256'] for e in self.config['inputs'].values()))

    def test_public_job_rejects_missing_reference(self):
        config = copy.deepcopy(self.config)
        del config['inputs']['planes']
        path, out = self.root / 'no-plane.json', self.root / 'no-plane'
        write_json(path, config)
        done = public_job(path, out)
        self.assertNotEqual(done.returncode, 0)
        report = read_json(out / 'job-report.json')
        self.assertNotEqual(report['status'], 'completed', report)
        self.assertFalse((out / 'routed-incumbent.json').exists())

        self.assertEqual(report['final']['impedance']['status'], 'FAIL')

    def test_public_job_rejects_missing_dielectric_data(self):
        config = prepare(self.root / 'missing-er-inputs', self.config['router']['checkout'])
        board = Path(config['inputs']['board']['path'])
        raw = board.read_text()
        self.assertIn('(epsilon_r 4.2)', raw)
        board.write_text(raw.replace('(epsilon_r 4.2)', ''))
        config['inputs']['board']['sha256'] = digest(board)
        path, out = self.root / 'missing-er.json', self.root / 'missing-er'
        write_json(path, config)
        done = public_job(path, out)
        self.assertNotEqual(done.returncode, 0)
        report = read_json(out / 'job-report.json')
        self.assertNotEqual(report['status'], 'completed', report)
        self.assertFalse((out / 'routed-incumbent.json').exists())
        self.assertIn('original impedance stackup is unproven', report['reason'])
        self.assertIn('epsilon_r', report['reason'])

    def test_public_job_rejects_uncoupled_limit(self):
        config = copy.deepcopy(self.config)
        config['router']['pair_impedance']['max_uncoupled_mm'] = .5
        path, out = self.root / 'strict-escape.json', self.root / 'strict-escape'
        write_json(path, config)
        done = public_job(path, out)
        self.assertNotEqual(done.returncode, 0)
        report = read_json(out / 'job-report.json')
        self.assertNotEqual(report['status'], 'completed', report)
        self.assertEqual(report['final']['impedance']['status'], 'FAIL')
        self.assertEqual(report['final']['opens'], 0)
        self.assertFalse((out / 'routed-incumbent.json').exists())

    def test_impedance_request_binds_resume(self):
        out = self.root / 'cancelled'
        out.mkdir();(out / 'CANCEL').touch()
        public_job(self.root / 'inputs/job.json', out)
        self.assertEqual(read_json(out / 'job-report.json')['status'], 'cancelled')
        checkpoint = (out / 'checkpoint.json').read_bytes()
        (out / 'CANCEL').unlink()
        config = copy.deepcopy(self.config)
        config['router']['pair_impedance']['target_ohm'] = 90
        write_json(self.root / 'changed.json', config)
        done = public_job(self.root / 'changed.json', out, '--resume')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('different job identity', done.stdout + done.stderr)
        self.assertEqual(checkpoint, (out / 'checkpoint.json').read_bytes())

    def test_build_producer_carries_impedance_and_reference_plan(self):
        out = self.root / 'build-stage';inputs = out / 'inputs'
        config = prepare(inputs, self.config['router']['checkout'])
        attempt = retained_requirements(out, inputs, dict(board='board.kicad_pcb', checks=[]), dict(requirements=[]))
        stage = attempt.directory
        plan = dict(placement=dict(budget_s=config['budget_s'], search=config['search'],
            router=config['router'], intent='intent.json', groups='groups.json', planes='planes.json'))
        candidate = attempt.execute(plan, inputs, diagnostic=True)
        report = read_json(stage / 'placement/job-report.json')
        self.assertEqual(report['status'], 'completed', report)
        self.assertEqual(report['final']['impedance']['status'], 'PASS')
        self.assertEqual(digest(candidate / 'board.kicad_pcb'), report['final']['board_sha256'])


if __name__ == '__main__':
    unittest.main()

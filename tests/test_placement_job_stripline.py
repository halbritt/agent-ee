"""Symmetric stripline through the ordinary placement job and build producer."""
import copy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from agent_ee import build_workflow, placement_job as job
from agent_ee.storage import digest, read_json, write_json
from tests.test_placement_job_impedance import prepare as outer_inputs
from tests.test_placement_job_rules import public_job
from tests.toolchain import require_kicad_route, require_toolchain


def prepare(root, checkout):
    config = outer_inputs(root, checkout)
    source = Path(checkout) / 'fixtures/pair_stripline'
    for item in source.iterdir():
        if item.is_dir():
            shutil.copytree(item, root / item.name)
        else:
            shutil.copy2(item, root / item.name)
    # The normal plane producer must produce both references from this plan.
    subprocess.run([sys.executable, '-c', '''import sys,pcbnew as k
b=k.LoadBoard(sys.argv[1])
for z in list(b.Zones()):b.Remove(z)
k.SaveBoard(sys.argv[1],b)
''', str(root / 'board.kicad_pcb')], check=True, capture_output=True, timeout=30)
    planes = read_json(root / 'planes.json')
    planes['zones'] = [{**planes['zones'][0], 'layer': layer} for layer in ('F.Cu', 'In2.Cu')]
    write_json(root / 'planes.json', planes)
    config['router']['pair_layers'] = ['In1.Cu']
    config['router']['pair_impedance']['max_uncoupled_mm'] = 4
    for row in config['inputs'].values():
        row['sha256'] = digest(row['path'])
    write_json(root / 'job.json', config)
    return config


class StriplineJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.checkout = require_kicad_route()
        cls.temp = tempfile.TemporaryDirectory(prefix='ee-stripline-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(os.environ.get('AGENT_EE_STRIPLINE_EVIDENCE', cls.temp.name))
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.config = prepare(cls.root / 'inputs', cls.checkout)
        cls.done = public_job(cls.root / 'inputs/job.json', cls.root / 'positive')
        cls.report = read_json(cls.root / 'positive/job-report.json')

    def test_public_job_measures_both_references_after_final_fill(self):
        self.assertEqual(self.done.returncode, 0, self.done.stdout + self.done.stderr)
        self.assertEqual(self.report['status'], 'completed', self.report)
        final = self.report['final']
        self.assertEqual(final['opens'], 0)
        self.assertEqual(final['identity_problems'], [])
        self.assertEqual(final['planes']['status'], 'PASS')
        self.assertEqual(final['impedance']['status'], 'PASS')
        self.assertEqual(final['impedance']['board_sha256'], digest(final['board']))
        self.assertEqual(final['impedance']['plan']['reference_layers'], ['F.Cu', 'In2.Cu'])
        for row in final['impedance']['nets']:
            self.assertGreater(row['coupled_mm'], 7)
            self.assertLess(row['uncoupled_mm'], 4)
            self.assertTrue(all(0 < area < 1 for area in row['reference_opening_area_mm2'].values()))
        self.assertTrue(all(digest(row['path']) == row['sha256'] for row in self.config['inputs'].values()))

    def test_public_job_rejects_either_missing_reference(self):
        for layer in ('F.Cu', 'In2.Cu'):
            with self.subTest(layer=layer):
                name = layer.replace('.', '-')
                config = copy.deepcopy(self.config)
                planes = read_json(config['inputs']['planes']['path'])
                planes['zones'] = [z for z in planes['zones'] if z['layer'] != layer]
                plan = self.root / (name + '-planes.json')
                write_json(plan, planes)
                config['inputs']['planes'] = dict(path=str(plan), sha256=digest(plan))
                path, out = self.root / (name + '-missing.json'), self.root / (name + '-missing')
                write_json(path, config)
                done = public_job(path, out)
                self.assertNotEqual(done.returncode, 0)
                report = read_json(out / 'job-report.json')
                self.assertNotEqual(report['status'], 'completed', report)
                self.assertFalse((out / 'routed-incumbent.json').exists())
                self.assertEqual(report['final']['impedance']['status'], 'FAIL')
                self.assertTrue(any(layer in d for d in report['final']['impedance']['defects']))

    def test_missing_lower_dielectric_is_rejected_before_native_defaulting(self):
        config = prepare(self.root / 'missing-er-inputs', self.checkout)
        board = Path(config['inputs']['board']['path'])
        raw = board.read_text()
        start = raw.index('(epsilon_r 4.2)', raw.index('(epsilon_r 4.2)') + 1)
        board.write_text(raw[:start] + raw[start:].replace('(epsilon_r 4.2)', '', 1))
        config['inputs']['board']['sha256'] = digest(board)
        path, out = self.root / 'missing-er.json', self.root / 'missing-er'
        write_json(path, config)
        done = public_job(path, out)
        self.assertNotEqual(done.returncode, 0)
        report = read_json(out / 'job-report.json')
        self.assertIn('original impedance stackup is unproven', report['reason'])
        self.assertIn('epsilon_r', report['reason'])
        self.assertFalse((out / 'routed-incumbent.json').exists())

    def test_build_producer_preserves_native_library_dependencies(self):
        out = self.root / 'build-stage';inputs = out / 'inputs'
        config = prepare(inputs, self.checkout)
        requirements = out / 'requirements';requirements.mkdir()
        write_json(requirements / 'scope.json', dict(board='board.kicad_pcb', checks=[]))
        write_json(requirements / 'accounting.json', dict(requirements=[]))
        stage = out / 'placement-routing';stage.mkdir()
        plan = dict(placement=dict(budget_s=config['budget_s'], search=config['search'],
            router=config['router'], intent='intent.json', groups='groups.json', planes='planes.json'))
        candidate = build_workflow._execute('placement-routing', plan, inputs, stage,
            [dict(stage='requirements', directory='requirements')], out, diagnostic=True)
        report = read_json(stage / 'placement/job-report.json')
        self.assertEqual(report['status'], 'completed', report)
        self.assertEqual(report['final']['impedance']['status'], 'PASS')
        self.assertEqual(digest(candidate / 'board.kicad_pcb'), report['final']['board_sha256'])
        drc, _ = job.drc(candidate / 'board.kicad_pcb', stage, refill=True, label='native-final')
        self.assertEqual(drc['unconnected_items'], [])
        self.assertEqual(drc['violations'], [])
        for path in inputs.rglob('*'):
            if path.is_file() and (path.suffix == '.kicad_mod' or path.name == 'fp-lib-table'):
                self.assertEqual(digest(path), digest(candidate / path.relative_to(inputs)))


if __name__ == '__main__':
    unittest.main()

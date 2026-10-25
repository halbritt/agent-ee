"""Series-part matching survives the public job and real build producer."""
import copy
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_ee import build_workflow, placement_job as job
from agent_ee.storage import digest, read_json, write_json
from tests.test_placement_job_rules import public_job
from tests.toolchain import require_kicad_route, require_toolchain

SPEC = dict(chains=[['C1', 'C2']], tolerance_mm=.1, amplitude_mm=3)


def prepare(root, checkout):
    shutil.copytree(Path(checkout) / 'fixtures/ac_matching', root)
    board = root / 'chain.kicad_pcb'
    write_json(root / 'intent.json', dict(fixed_poses=[], edge_connectors=[], proximity=[]))
    write_json(root / 'groups.json', dict(groups=[
        dict(name='source', anchor='J1', refs=['J1']),
        dict(name='coupling', anchor='C1', refs=['C1', 'C2']),
        dict(name='receiver', anchor='J2', refs=['J2'])]))
    files = dict(board=board, project=board.with_suffix('.kicad_pro'), rules=board.with_suffix('.kicad_dru'),
                 intent=root / 'intent.json', groups=root / 'groups.json')
    config = dict(schema=job.CONFIG_SCHEMA, budget_s=240,
        inputs={name: dict(path=str(path), sha256=digest(path)) for name, path in files.items()},
        search=dict(seeds=[dict(seed=0, start='scattered')], steps_per_seed=10, shortlist=1),
        router=dict(checkout=str(checkout), pair_layers=['F.Cu'], ac_match=copy.deepcopy(SPEC)))
    write_json(root / 'job.json', config)
    return config


class AcMatchingJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.checkout = require_kicad_route()
        cls.temp = tempfile.TemporaryDirectory(prefix='ee-ac-matching-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(os.environ.get('AGENT_EE_AC_MATCHING_EVIDENCE', cls.temp.name))
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.config = prepare(cls.root / 'inputs', cls.checkout)
        cls.done = public_job(cls.root / 'inputs/job.json', cls.root / 'positive')
        cls.report = read_json(cls.root / 'positive/job-report.json')

    def assert_matched(self, report):
        self.assertEqual(report['status'], 'completed', report)
        final = report['final']
        self.assertEqual(final['opens'], 0)
        self.assertEqual(final['identity_problems'], [])
        measured = final['ac_matching']
        self.assertEqual(measured['status'], 'PASS', measured)
        self.assertEqual(measured['board_sha256'], digest(final['board']))
        self.assertEqual(measured['requirement'], SPEC)
        chain, = measured['chains']
        self.assertEqual(chain['bridges'], ['C1', 'C2'])
        self.assertEqual(set(chain['measurements_mm']), {'DPA_P', 'DPA_N', 'DPB_P', 'DPB_N'})
        self.assertLessEqual(chain['skew_mm'] + chain['rounding_bound_mm'], .1)

    def test_public_job_measures_final_composed_paths(self):
        self.assertEqual(self.done.returncode, 0, self.done.stdout + self.done.stderr)
        self.assert_matched(self.report)
        self.assertTrue(all(digest(e['path']) == e['sha256'] for e in self.config['inputs'].values()))

    def test_connected_unmatched_chain_rejects(self):
        config = copy.deepcopy(self.config)
        config['router']['ac_match']['amplitude_mm'] = .001
        path, out = self.root / 'insufficient.json', self.root / 'insufficient'
        write_json(path, config)
        done = public_job(path, out)
        self.assertNotEqual(done.returncode, 0)
        report = read_json(out / 'job-report.json')
        self.assertEqual(report['status'], 'verification_failed', report)
        self.assertEqual(report['final']['opens'], 0)
        self.assertEqual(report['final']['ac_matching']['status'], 'FAIL')
        self.assertFalse((out / 'routed-incumbent.json').exists())

    def test_changed_chain_requirement_cannot_resume(self):
        config = copy.deepcopy(self.config)
        config['router']['ac_match']['tolerance_mm'] = .05
        path = self.root / 'changed.json'
        write_json(path, config)
        before = digest(self.root / 'positive/checkpoint.json')
        done = public_job(path, self.root / 'positive', '--resume')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('different job identity', done.stdout + done.stderr)
        self.assertEqual(digest(self.root / 'positive/checkpoint.json'), before)

    def test_invalid_chain_request_cannot_start(self):
        for index, value in enumerate((None, dict(chains=[], tolerance_mm=.1, amplitude_mm=3),
                dict(chains=[['C1', 'C1']], tolerance_mm=.1, amplitude_mm=3),
                dict(chains=[['C1', 'C2']], tolerance_mm=-.1, amplitude_mm=3))):
            with self.subTest(value=value):
                config = copy.deepcopy(self.config)
                config['router']['ac_match'] = value
                report = job.run_job(config, self.root / f'invalid-{index}')
                self.assertEqual(report['status'], 'refused', report)
                self.assertIn('router.ac_match', report['reason'])
        config = copy.deepcopy(self.config)
        config['router']['ac_match']['tolerance_mm'] = float('nan')
        out = self.root / 'nonfinite-json'
        with self.assertRaisesRegex(ValueError, 'not JSON compliant'):
            job.run_job(config, out)
        self.assertFalse(out.exists())

    def test_build_producer_preserves_native_chain_and_libraries(self):
        out = self.root / 'build-stage'
        inputs = out / 'inputs'
        config = prepare(inputs, self.checkout)
        requirements = out / 'requirements'
        requirements.mkdir()
        write_json(requirements / 'scope.json', dict(board='chain.kicad_pcb', checks=[]))
        write_json(requirements / 'accounting.json', dict(requirements=[]))
        stage = out / 'placement-routing'
        stage.mkdir()
        plan = dict(placement=dict(budget_s=config['budget_s'], search=config['search'],
            router=config['router'], intent='intent.json', groups='groups.json'))
        candidate = build_workflow._execute('placement-routing', plan, inputs, stage,
            [dict(stage='requirements', directory='requirements')], out, diagnostic=True)
        self.assert_matched(read_json(stage / 'placement/job-report.json'))
        native, _ = job.drc(candidate / 'chain.kicad_pcb', stage, refill=True, label='native-final')
        self.assertEqual(native['violations'], [])
        self.assertEqual(native['unconnected_items'], [])
        for path in inputs.rglob('*'):
            if path.is_file() and (path.suffix in ('.kicad_mod', '.kicad_pro', '.kicad_dru') or path.name == 'fp-lib-table'):
                self.assertEqual(digest(path), digest(candidate / path.relative_to(inputs)))


if __name__ == '__main__':
    unittest.main()

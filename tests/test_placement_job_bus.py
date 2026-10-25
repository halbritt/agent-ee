"""Existing geometric bus routing through the normal placement/build workflow."""
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
from tests.test_placement_job_rules import public_job
from tests.toolchain import require_kicad_route, require_toolchain


def prepare(root, checkout):
    shutil.copytree(Path(checkout) / 'fixtures/bus_routing', root)
    write_json(root / 'intent.json', dict(fixed_poses=[], edge_connectors=[], proximity=[]))
    write_json(root / 'groups.json', dict(groups=[
        dict(name='bus', anchor='J1', refs=['J1', 'J2', 'TP1']),
        dict(name='auxiliary', anchor='J3', refs=['J3', 'J4'])]))
    files = dict(board=root / 'bus.kicad_pcb', project=root / 'bus.kicad_pro',
                 rules=root / 'bus.kicad_dru', intent=root / 'intent.json', groups=root / 'groups.json')
    config = dict(schema=job.CONFIG_SCHEMA, budget_s=240,
        inputs={name: dict(path=str(path), sha256=digest(path)) for name, path in files.items()},
        search=dict(seeds=[dict(seed=0, start='scattered')], steps_per_seed=10, shortlist=1),
        router=dict(checkout=str(checkout), grid_step_mm=.1,
                    bus=dict(detection_radius_mm=20, min_nets=3)))
    write_json(root / 'job.json', config)
    return config


class BusJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.checkout = require_kicad_route()
        cls.temp = tempfile.TemporaryDirectory(prefix='ee-bus-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(os.environ.get('AGENT_EE_BUS_EVIDENCE', cls.temp.name))
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.config = prepare(cls.root / 'inputs', cls.checkout)
        cls.done = public_job(cls.root / 'inputs/job.json', cls.root / 'positive')
        cls.report = read_json(cls.root / 'positive/job-report.json')

    def assert_bus(self, report):
        self.assertEqual(report['status'], 'completed', report)
        self.assertEqual(report['final']['opens'], 0)
        self.assertEqual(report['final']['identity_problems'], [])
        result = read_json(report['route']['result_path'])
        groups = result['process']['bus_routing']['groups']
        self.assertEqual(len(groups), 1)
        group = next(iter(groups.values()))
        self.assertEqual(group['members_in_physical_order'], [f'D{i}' for i in range(8)])
        self.assertTrue(group['corridor_planned'])
        story = read_json(result['process']['bus_routing']['diagnostics'])
        self.assertNotIn('bus', story['nets']['AUX'])
        self.assertEqual(story['phase3_order'], ['D3'])
        return result

    def test_public_job_routes_grouped_bus_and_tap_without_rule_or_placement_changes(self):
        self.assertEqual(self.done.returncode, 0, self.done.stdout + self.done.stderr)
        result = self.assert_bus(self.report)
        self.assertEqual(result['settings']['bus']['detection_radius'], 20)
        self.assertEqual(result['settings']['bus']['min_nets'], 3)
        final = Path(self.report['final']['board'])
        for name, suffix in [('project', '.kicad_pro'), ('rules', '.kicad_dru')]:
            self.assertEqual(digest(final.with_suffix(suffix)), self.config['inputs'][name]['sha256'])
        self.assertTrue(all(digest(e['path']) == e['sha256'] for e in self.config['inputs'].values()))

    def test_invalid_controls_refuse_before_native_routing(self):
        for index, controls in enumerate([None, True, {'unknown': 1}, {'min_nets': True},
                {'min_nets': 1}, {'attraction_bonus': 1.5}, {'detection_radius_mm': 'nan'},
                {'attraction_radius_mm': 0}]):
            with self.subTest(controls=controls):
                config = copy.deepcopy(self.config); config['router']['bus'] = controls
                report = job.run_job(config, self.root / f'invalid-{index}')
                self.assertEqual(report['status'], 'refused')
                self.assertIn('bus', report['reason'])
                self.assertNotIn('route', report)
        # Nonfinite numbers cannot be captured as canonical JSON anywhere in a
        # job. That existing ingress refusal precedes the bus-specific checks.
        config = copy.deepcopy(self.config)
        config['router']['bus'] = {'detection_radius_mm': float('nan')}
        out = self.root / 'nonfinite-json'
        with self.assertRaisesRegex(ValueError, 'not JSON compliant'):
            job.run_job(config, out)
        self.assertFalse(out.exists())

    def test_changed_bus_controls_cannot_resume_an_existing_job(self):
        self.assertEqual(self.report['status'], 'completed', self.report)
        config = copy.deepcopy(self.config)
        config['router']['bus']['detection_radius_mm'] = 10
        path = self.root / 'changed.json'; write_json(path, config)
        before = digest(self.root / 'positive/checkpoint.json')
        previous_report = digest(self.root / 'positive/job-report.json')
        done = subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'place', 'run', str(path),
            '--out', str(self.root / 'positive'), '--resume'], capture_output=True, text=True, timeout=60)
        (self.root / 'changed-resume.log').write_text(done.stdout + done.stderr)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(digest(self.root / 'positive/checkpoint.json'), before)
        self.assertEqual(digest(self.root / 'positive/job-report.json'), previous_report)
        self.assertIn('different job identity', done.stdout + done.stderr)

    def test_build_producer_delivers_native_clean_bus_with_library_dependencies(self):
        out = self.root / 'build-stage'; inputs = out / 'inputs'
        config = prepare(inputs, self.checkout)
        requirements = out / 'requirements'; requirements.mkdir()
        write_json(requirements / 'scope.json', dict(board='bus.kicad_pcb', checks=[]))
        write_json(requirements / 'accounting.json', dict(requirements=[]))
        stage = out / 'placement-routing'; stage.mkdir()
        plan = dict(placement=dict(budget_s=config['budget_s'], search=config['search'],
            router=config['router'], intent='intent.json', groups='groups.json'))
        candidate = build_workflow._execute('placement-routing', plan, inputs, stage,
            [dict(stage='requirements', directory='requirements')], out, diagnostic=True)
        report = read_json(stage / 'placement/job-report.json')
        self.assert_bus(report)
        final = candidate / 'bus.kicad_pcb'
        self.assertEqual(digest(final), report['final']['board_sha256'])
        native, _ = job.drc(final, stage, refill=True, label='native-final')
        self.assertEqual(native['violations'], [])
        self.assertEqual(native['unconnected_items'], [])
        for path in inputs.rglob('*'):
            if path.is_file() and (path.suffix == '.kicad_mod' or path.name == 'fp-lib-table'):
                self.assertEqual(digest(path), digest(candidate / path.relative_to(inputs)))


if __name__ == '__main__':
    unittest.main()

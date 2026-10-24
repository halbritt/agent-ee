"""Declared planes must reach native routing and survive final verification."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

from agent_ee import add_planes, placement_job as job
from agent_ee.storage import digest, read_json, write_json
from tests.test_placement_job import Fixture, intent, groups
from tests.toolchain import require_kicad_route


class PlaneJobTests(Fixture):
    def setUp(self):
        super().setUp()
        require_kicad_route()
        evidence = os.environ.get('AGENT_EE_PLANE_JOB_EVIDENCE')
        if evidence:
            self.addCleanup(shutil.copytree, self.dir,
                            Path(evidence) / self._testMethodName, dirs_exist_ok=True)
        self.plan = {
            'schema': add_planes.SCHEMA,
            'zones': [{'net': 'GND', 'layer': 'F.Cu', 'outline': 'board',
                       'clearance_mm': 0.2, 'min_width_mm': 0.2,
                       'thermal': {'connection': 'thermal', 'gap_mm': 0.3,
                                   'spoke_width_mm': 0.3},
                       'priority': 0, 'island_removal': 'always'}]}

    def config(self):
        files = {'board': self.board, 'project': self.project,
                 'intent': self.dir / 'intent.json', 'groups': self.dir / 'groups.json',
                 'planes': self.dir / 'planes.json'}
        write_json(files['intent'], intent())
        write_json(files['groups'], groups())
        write_json(files['planes'], self.plan)
        return {'schema': job.CONFIG_SCHEMA, 'budget_s': 300,
                'inputs': {name: {'path': str(path), 'sha256': digest(path)}
                           for name, path in files.items()},
                'search': self.search(2, 30000),
                'router': {'checkout': os.environ['AGENT_EE_KICAD_ROUTE_CHECKOUT'],
                           'grid_step_mm': 0.025}}

    def test_public_job_routes_with_declared_plane_and_checks_saved_fill(self):
        self.run_plane_job()

    def test_four_layer_job_connects_declared_inner_plane(self):
        import pcbnew as k
        board = k.LoadBoard(str(self.board))
        board.SetCopperLayerCount(4)
        k.SaveBoard(str(self.board), board)
        self.plan['zones'][0]['layer'] = 'In1.Cu'
        self.run_plane_job()

    def test_declared_plane_with_no_board_copper_cannot_complete_job(self):
        self.plan['zones'][0]['outline'] = {
            'polygon_mm': [[30, 30], [40, 30], [40, 40], [30, 40]]}
        config = self.config()
        write_json(self.dir / 'job.json', config)
        done = subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'place', 'run',
                               str(self.dir / 'job.json'), '--out', str(self.dir / 'job')],
                              capture_output=True, text=True, timeout=330)
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        report = read_json(self.dir / 'job/job-report.json')
        self.assertEqual(report['status'], 'route_failed', report)
        self.assertEqual(report['route']['status'], 'plane_failed', report)
        self.assertFalse((self.dir / 'job/routed-incumbent.json').exists())
        self.assertFalse(report['claims']['accepted'])
        self.assertTrue(all(digest(entry['path']) == entry['sha256']
                            for entry in config['inputs'].values()))

    def run_plane_job(self):
        import pcbnew as k
        config = self.config()
        write_json(self.dir / 'job.json', config)
        before = {name: digest(entry['path']) for name, entry in config['inputs'].items()}
        done = subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'place', 'run',
                               str(self.dir / 'job.json'), '--out', str(self.dir / 'job')],
                              capture_output=True, text=True, timeout=330)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        report = read_json(self.dir / 'job/job-report.json')
        self.assertEqual(report['status'], 'completed', report)
        board = k.LoadBoard(report['final']['board'])
        zones = list(board.Zones())
        self.assertEqual({zone.m_Uuid.AsString() for zone in zones},
                         set(add_planes.zone_ids(self.plan)))
        self.assertTrue(all(zone.IsFilled() for zone in zones))
        self.assertEqual(report['final']['planes']['status'], 'PASS')
        self.assertEqual(report['final']['planes']['board_sha256'], digest(report['final']['board']))
        self.assertEqual(report['final']['opens'], 0)
        self.assertEqual(report['final']['identity_problems'], [])
        # The router must receive the plane, not have it added only for the report.
        staged = Path(report['route']['argv'][report['route']['argv'].index('--dir') + 1])
        routed_input = k.LoadBoard(str(staged / 'board.kicad_pcb'))
        self.assertEqual({zone.m_Uuid.AsString() for zone in routed_input.Zones()},
                         set(add_planes.zone_ids(self.plan)))
        self.assertEqual(before, {name: digest(entry['path']) for name, entry in config['inputs'].items()})

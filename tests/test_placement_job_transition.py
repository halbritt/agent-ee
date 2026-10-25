"""Paired layer transitions through public placement and the real build producer."""
import copy
import os
from pathlib import Path
import tempfile
import unittest

from agent_ee import placement_job as job
from agent_ee.storage import digest, read_json, write_json
from tests.test_placement_job_matching import asymmetric
from tests.test_placement_job_rules import public_job
from tests.toolchain import require_kicad_route, require_toolchain


LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']


def transition(root, checkout):
    import pcbnew as k
    config = asymmetric(root, checkout)
    path = Path(config['inputs']['board']['path'])
    project = path.with_suffix('.kicad_pro').read_bytes()
    board = k.LoadBoard(str(path))
    zone = k.ZONE(board)
    zone.SetIsRuleArea(True)
    zone.SetZoneName('outer-layer barrier requires inner-layer pair')
    layers = k.LSET()
    layers.AddLayer(k.F_Cu)
    layers.AddLayer(k.B_Cu)
    zone.SetLayerSet(layers)
    zone.SetDoNotAllowTracks(True)
    zone.SetDoNotAllowVias(True)
    zone.SetDoNotAllowZoneFills(True)
    zone.SetDoNotAllowPads(False)
    zone.SetDoNotAllowFootprints(False)
    shape = zone.Outline()
    shape.NewOutline()
    for x, y in [(58, 39), (62, 39), (62, 81), (58, 81)]:
        shape.Append(k.FromMM(x), k.FromMM(y))
    board.Add(zone)
    k.SaveBoard(str(path), board)
    path.with_suffix('.kicad_pro').write_bytes(project)
    config['inputs']['board']['sha256'] = digest(path)
    config['router'].update(grid_step_mm=.1, pair_layers=LAYERS)
    write_json(root / 'job.json', config)
    return config


class PairTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        checkout = require_kicad_route()
        cls.temp = tempfile.TemporaryDirectory(prefix='ee-pair-transition-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(os.environ.get('AGENT_EE_TRANSITION_EVIDENCE', cls.temp.name))
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.config = transition(cls.root / 'inputs', checkout)
        cls.done = public_job(cls.root / 'inputs/job.json', cls.root / 'positive')
        cls.report = read_json(cls.root / 'positive/job-report.json')

    def test_public_job_preserves_barrier_anchors_rules_and_coupled_inner_route(self):
        import pcbnew as k
        self.assertEqual(self.done.returncode, 0, self.done.stdout + self.done.stderr)
        self.assertEqual(self.report['status'], 'completed', self.report)
        final = Path(self.report['final']['board'])
        native = read_json(final.parent / 'native-drc.json')
        self.assertEqual(native['violations'], [])
        self.assertEqual(native['unconnected_items'], [])
        self.assertEqual(self.report['final']['identity_problems'], [])
        board = k.LoadBoard(str(final))
        self.assertEqual(board.GetCopperLayerCount(), 4)
        vias = [t for t in board.GetTracks() if t.GetClass() == 'PCB_VIA']
        tracks = [t for t in board.GetTracks() if t.GetClass() != 'PCB_VIA']
        self.assertEqual(len(vias), 4)
        self.assertEqual({board.GetLayerName(t.GetLayer()) for t in tracks}, {'F.Cu', 'In1.Cu'})
        self.assertEqual({t.GetWidth() for t in tracks}, {250000})
        zone, = list(board.Zones())
        self.assertTrue(zone.GetIsRuleArea())
        self.assertTrue(zone.GetDoNotAllowTracks())
        self.assertTrue(zone.GetDoNotAllowVias())
        self.assertFalse(zone.GetDoNotAllowPads())
        self.assertFalse(zone.GetDoNotAllowFootprints())
        self.assertEqual({board.GetLayerName(l) for l in zone.GetLayerSet().Seq()}, {'F.Cu', 'B.Cu'})
        self.assertEqual({fp.GetReference(): (fp.GetPosition().x, fp.GetPosition().y, fp.GetOrientationDegrees())
                          for fp in board.GetFootprints()},
                         {'J1': (45000000, 50000000, 0), 'J2': (75000000, 70000000, 90)})
        for key, suffix in [('project', '.kicad_pro'), ('rules', '.kicad_dru')]:
            self.assertEqual(digest(final.with_suffix(suffix)), self.config['inputs'][key]['sha256'])
        self.assertTrue(all(digest(e['path']) == e['sha256'] for e in self.config['inputs'].values()))
        argv = self.report['route']['argv']
        start = argv.index('--pair-layers') + 1
        self.assertEqual(argv[start:start + 4], LAYERS)

    def test_outer_only_request_rejects_incomplete_route(self):
        config = copy.deepcopy(self.config)
        config['router']['pair_layers'] = ['F.Cu', 'B.Cu']
        path, out = self.root / 'outer.json', self.root / 'outer'
        write_json(path, config)
        done = public_job(path, out)
        self.assertNotEqual(done.returncode, 0)
        report = read_json(out / 'job-report.json')
        self.assertNotEqual(report['status'], 'completed', report)
        self.assertFalse((out / 'routed-incumbent.json').exists())

    def test_placement_restrictions_are_still_refused(self):
        import pcbnew as k
        for constraint in ['Footprints', 'Pads', 'PlacementArea']:
            with self.subTest(constraint=constraint):
                config = transition(self.root / constraint, self.config['router']['checkout'])
                path = Path(config['inputs']['board']['path'])
                board = k.LoadBoard(str(path))
                zone, = list(board.Zones())
                if constraint == 'PlacementArea':
                    zone.SetPlacementAreaEnabled(True)
                else:
                    getattr(zone, 'SetDoNotAllow' + constraint)(True)
                k.SaveBoard(str(path), board)
                with self.assertRaisesRegex(job.JobRefusal, 'board-level zones'):
                    job.capture(path, read_json(self.root / constraint / 'intent.json'),
                                read_json(self.root / constraint / 'groups.json'))

    def test_layer_selection_is_bound_to_resume_identity(self):
        out = self.root / 'cancelled'
        out.mkdir()
        (out / 'CANCEL').touch()
        public_job(self.root / 'inputs/job.json', out)
        self.assertEqual(read_json(out / 'job-report.json')['status'], 'cancelled')
        checkpoint = (out / 'checkpoint.json').read_bytes()
        (out / 'CANCEL').unlink()
        changed = copy.deepcopy(self.config)
        changed['router']['pair_layers'] = ['F.Cu', 'B.Cu']
        write_json(self.root / 'changed.json', changed)
        done = public_job(self.root / 'changed.json', out, '--resume')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('different job identity', done.stdout + done.stderr)
        self.assertEqual(checkpoint, (out / 'checkpoint.json').read_bytes())

    def test_build_producer_carries_layers_and_native_keepout(self):
        from agent_ee import build_workflow
        out = self.root / 'build-stage'
        inputs = out / 'inputs'
        config = transition(inputs, self.config['router']['checkout'])
        requirements = out / 'requirements'
        requirements.mkdir()
        write_json(requirements / 'scope.json', dict(board='interconnect.kicad_pcb', checks=[]))
        write_json(requirements / 'accounting.json', dict(requirements=[]))
        stage = out / 'placement-routing'
        stage.mkdir()
        plan = dict(placement=dict(budget_s=config['budget_s'], search=config['search'],
            router=config['router'], intent='intent.json', groups='groups.json'))
        candidate = build_workflow._execute('placement-routing', plan, inputs, stage,
            [dict(stage='requirements', directory='requirements')], out, diagnostic=True)
        captured = read_json(stage / 'placement-config.json')
        self.assertEqual(captured['router']['pair_layers'], LAYERS)
        report = read_json(stage / 'placement/job-report.json')
        self.assertEqual(report['status'], 'completed', report)
        self.assertEqual(digest(candidate / 'interconnect.kicad_pcb'), report['final']['board_sha256'])

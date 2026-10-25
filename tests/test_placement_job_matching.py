"""Saved native copper decides the public job's intra-pair matching result."""
import copy
import os
from pathlib import Path
import tempfile
import unittest

from agent_ee import placement_job as job
from agent_ee.storage import digest, read_json, write_json
from tests.test_placement_job_rules import prepare, public_job
from tests.toolchain import require_kicad_route, require_toolchain


def asymmetric(root, checkout):
    import pcbnew as k
    config = prepare(root, checkout)
    path = Path(config['inputs']['board']['path'])
    board = k.LoadBoard(str(path))
    point = lambda x, y: k.VECTOR2I(k.FromMM(x), k.FromMM(y))
    for item in list(board.GetDrawings()):
        board.Delete(item)
    for a, b in [((40, 40), (85, 40)), ((85, 40), (85, 80)),
                 ((85, 80), (40, 80)), ((40, 80), (40, 40))]:
        edge = k.PCB_SHAPE(board)
        edge.SetShape(k.SHAPE_T_SEGMENT)
        edge.SetLayer(k.Edge_Cuts)
        edge.SetStart(point(*a))
        edge.SetEnd(point(*b))
        edge.SetWidth(k.FromMM(.05))
        board.Add(edge)
    connector = next(fp for fp in board.GetFootprints() if fp.GetReference() == 'J2')
    connector.SetOrientationDegrees(90)
    connector.SetPosition(point(75, 70))
    project = path.with_suffix('.kicad_pro').read_bytes()
    k.SaveBoard(str(path), board)
    path.with_suffix('.kicad_pro').write_bytes(project)
    rules = path.with_suffix('.kicad_dru')
    rules.write_bytes(rules.read_bytes().replace(b'15.1mm', b'70mm') +
        b'\n(rule "qualification uncoupled extent" (condition "A.NetClass == \'CAN\'") '
        b'(constraint diff_pair_uncoupled (max 6mm)))\n')
    for entry in config['inputs'].values():
        entry['sha256'] = digest(entry['path'])
    config['router']['intra_pair_match'] = dict(tolerance_mm=.1, amplitude_mm=2)
    write_json(root / 'job.json', config)
    return config


class MatchingJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        checkout = require_kicad_route()
        cls.temp = tempfile.TemporaryDirectory(prefix='ee-pair-matching-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(os.environ.get('AGENT_EE_MATCHING_EVIDENCE', cls.temp.name))
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.config = asymmetric(cls.root / 'inputs', checkout)
        cls.done = public_job(cls.root / 'inputs/job.json', cls.root / 'positive')
        cls.report = read_json(cls.root / 'positive/job-report.json')

    def test_normal_job_matches_saved_pair_and_preserves_constraints(self):
        import pcbnew as k
        self.assertEqual(self.done.returncode, 0, self.done.stdout + self.done.stderr)
        report = self.report
        self.assertEqual(report['status'], 'completed', report)
        self.assertEqual(report['final']['opens'], 0)
        self.assertEqual(report['final']['identity_problems'], [])
        final = Path(report['final']['board'])
        native = read_json(final.parent / 'native-drc.json')
        self.assertFalse(native['violations'])
        board = k.LoadBoard(str(final))
        lengths = {}
        for track in board.GetTracks():
            lengths[track.GetNetname()] = lengths.get(track.GetNetname(), 0) + track.GetLength() / 1e6
        self.assertLess(abs(lengths['CAN+'] - lengths['CAN-']), .1)
        self.assertEqual(board.GetCopperLayerCount(), 4)
        self.assertEqual({t.GetWidth() for t in board.GetTracks()}, {250000})
        for key, suffix in [('project', '.kicad_pro'), ('rules', '.kicad_dru')]:
            self.assertEqual(digest(final.with_suffix(suffix)), self.config['inputs'][key]['sha256'])
        self.assertTrue(all(digest(e['path']) == e['sha256'] for e in self.config['inputs'].values()))
        argv = report['route']['argv']
        self.assertIn('--diff-pair-intra-match', argv)
        self.assertEqual(argv[argv.index('--meander-amplitude') + 1], '2')

    def test_insufficient_meander_room_rejects_connected_result(self):
        config = copy.deepcopy(self.config)
        config['router']['intra_pair_match']['amplitude_mm'] = 1
        path = self.root / 'insufficient.json'
        write_json(path, config)
        out = self.root / 'insufficient'
        done = public_job(path, out)
        self.assertNotEqual(done.returncode, 0)
        report = read_json(out / 'job-report.json')
        self.assertEqual(report['status'], 'verification_failed', report)
        self.assertEqual(report['final']['opens'], 0)
        native = read_json(Path(report['final']['board']).parent / 'native-drc.json')
        self.assertEqual({v['type'] for v in native['violations']}, {'skew_out_of_range'})
        self.assertFalse((out / 'routed-incumbent.json').exists())

    def test_matching_config_rejects_missing_unknown_non_numeric_and_nonfinite_controls(self):
        for i, value in enumerate([None, {}, {'tolerance_mm': .1, 'amplitude_mm': 2, 'extra': 1},
            {'tolerance_mm': True, 'amplitude_mm': 2},
            {'tolerance_mm': .1, 'amplitude_mm': 0}]):
            with self.subTest(value=value):
                config = copy.deepcopy(self.config)
                config['router']['intra_pair_match'] = value
                report = job.run_job(config, self.root / f'invalid-{i}')
                self.assertEqual(report['status'], 'refused')
                self.assertNotIn('route', report)

    def test_nonfinite_matching_value_cannot_get_a_job_identity(self):
        config = copy.deepcopy(self.config)
        config['router']['intra_pair_match']['tolerance_mm'] = float('nan')
        out = self.root / 'nonfinite'
        with self.assertRaises(ValueError):
            job.run_job(config, out)
        self.assertFalse(out.exists())

    def test_build_stage_carries_matching_controls_to_real_placement(self):
        from agent_ee import build_workflow
        out = self.root / 'build-stage'
        inputs = out / 'inputs'
        config = asymmetric(inputs, self.config['router']['checkout'])
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
        self.assertEqual(captured['router']['intra_pair_match'], config['router']['intra_pair_match'])
        report = read_json(stage / 'placement/job-report.json')
        self.assertEqual(report['status'], 'completed', report)
        self.assertEqual(digest(candidate / 'interconnect.kicad_pcb'), report['final']['board_sha256'])

    def test_matching_controls_are_bound_to_resume_identity(self):
        out = self.root / 'cancelled'
        out.mkdir()
        (out / 'CANCEL').touch()
        public_job(self.root / 'inputs/job.json', out)
        self.assertEqual(read_json(out / 'job-report.json')['status'], 'cancelled')
        checkpoint = (out / 'checkpoint.json').read_bytes()
        (out / 'CANCEL').unlink()
        changed = copy.deepcopy(self.config)
        changed['router']['intra_pair_match']['tolerance_mm'] = .2
        write_json(self.root / 'changed.json', changed)
        resumed = public_job(self.root / 'changed.json', out, '--resume')
        self.assertNotEqual(resumed.returncode, 0)
        self.assertIn('different job identity', resumed.stdout + resumed.stderr)
        self.assertEqual(checkpoint, (out / 'checkpoint.json').read_bytes())

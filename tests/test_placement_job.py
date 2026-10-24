"""AGENTEE-515 whole-board placement job: native capture, prepared Rust search,
native materialization/reload, incumbent custody, cancellation and resume.

Every board here is an original synthetic board built through pcbnew. The
unchanged esp_prog input is external (AGENTEE-513 bundle, by SHA-256); its
capture test runs when AGENT_EE_ESP_PROG_PREPARED names that directory.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

from toolchain import require_kicad_route, require_toolchain

from agent_ee import placement_job as job

ESP_PROG = {'board.kicad_pcb': 'c9ed55ee7893be572e6dfea09d0cc9c3920f51ba39098b807c02c69b8df4828d',
            'board.kicad_pro': '9a9ab694fc8a9a456a6791060d998c01a171e30308a170e1b4f97d864b2b6395',
            'intent.json': '320cf961d58dab9aea164f34afc11099ae48f75fc0b100273a155d4d62330d3c',
            'groups.json': 'd48e2a5d95f733be6d4bdee06736034a5e34acd68e6289edef00fe89b86837e8'}
MM = 1_000_000


def _v(pcbnew, x, y):
    return pcbnew.VECTOR2I(int(x), int(y))


def _segment(pcbnew, parent, layer, a, b, width=100_000):
    shape = pcbnew.PCB_SHAPE(parent)
    shape.SetShape(pcbnew.SHAPE_T_SEGMENT)
    shape.SetStart(_v(pcbnew, *a))
    shape.SetEnd(_v(pcbnew, *b))
    shape.SetLayer(layer)
    shape.SetWidth(width)
    return shape


def _box(pcbnew, parent, layer, x0, y0, x1, y1, closed=True):
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    edges = list(zip(corners, corners[1:] + corners[:1]))
    return [_segment(pcbnew, parent, layer, a, b) for a, b in (edges if closed else edges[:3])]


def build_board(path, *, malformed_courtyard=False, locked_hole=True):
    """Original 20 x 12 mm two-layer board: two 2-pad parts with fab outlines, an
    asymmetric 3-pad part, a fixed unnetted fiducial, a silk-only logo and a
    locked NPTH mounting hole. Every footprint starts at the board centre (a pile)."""
    import pcbnew
    board = pcbnew.BOARD()
    for shape in _box(pcbnew, board, pcbnew.Edge_Cuts, 0, 0, 20 * MM, 12 * MM):
        board.Add(shape)
    nets = {}
    for name in ('A', 'B', 'GND'):
        nets[name] = pcbnew.NETINFO_ITEM(board, name)
        board.Add(nets[name])

    def footprint(ref, x, y, pads, fab=None, silk=None, crtyd=None, locked=False):
        fp = pcbnew.FOOTPRINT(board)
        fp.SetReference(ref)
        fp.SetValue(ref)
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        board.Add(fp)
        for number, (px, py, sx, sy, net, kind) in pads.items():
            pad = pcbnew.PAD(fp)
            pad.SetNumber(number)
            if kind == 'npth':
                pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
                pad.SetShape(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.PAD_SHAPE_CIRCLE)
                pad.SetSize(pcbnew.PADSTACK.ALL_LAYERS, _v(pcbnew, sx, sy))
                pad.SetDrillSize(_v(pcbnew, sx, sy))
                pad.SetLayerSet(pad.UnplatedHoleMask())
            else:
                pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
                pad.SetShape(pcbnew.PADSTACK.ALL_LAYERS,
                             pcbnew.PAD_SHAPE_CIRCLE if kind == 'round' else pcbnew.PAD_SHAPE_RECT)
                pad.SetSize(pcbnew.PADSTACK.ALL_LAYERS, _v(pcbnew, sx, sy))
                pad.SetLayerSet(pad.SMDMask())
            fp.Add(pad)
            pad.SetFPRelativePosition(_v(pcbnew, px, py))
            if net:
                pad.SetNet(nets[net])
        for layer, rect in ((pcbnew.F_Fab, fab), (pcbnew.F_SilkS, silk)):
            if rect:
                for shape in _box(pcbnew, fp, layer, *rect):
                    fp.Add(shape)
        if crtyd:
            for shape in _box(pcbnew, fp, pcbnew.F_CrtYd, *crtyd, closed=False):
                fp.Add(shape)
        fp.SetPosition(_v(pcbnew, x, y))
        fp.SetLocked(locked)
        return fp

    centre = (10 * MM, 6 * MM)
    two = lambda a, b: {'1': (-750_000, 0, 800_000, 900_000, a, 'rect'), '2': (750_000, 0, 800_000, 900_000, b, 'rect')}
    footprint('R1', *centre, two('A', 'GND'), fab=(-1_000_000, -500_000, 1_000_000, 500_000),
              crtyd=(-1_300_000, -800_000, 1_300_000, 800_000) if malformed_courtyard else None)
    footprint('C1', *centre, two('A', 'B'), fab=(-1_000_000, -500_000, 1_000_000, 500_000))
    footprint('U1', *centre, {'1': (-1_200_000, -600_000, 600_000, 500_000, 'B', 'rect'),
                              '2': (-1_200_000, 600_000, 600_000, 500_000, 'GND', 'rect'),
                              '3': (1_500_000, 0, 700_000, 1_200_000, 'A', 'rect')},
              silk=(-1_800_000, -1_100_000, 2_000_000, 1_100_000))
    footprint('FID', 3 * MM, 3 * MM, {'1': (0, 0, 1_000_000, 1_000_000, None, 'round')})
    logo = pcbnew.FOOTPRINT(board)
    logo.SetReference('')
    board.Add(logo)
    for shape in _box(pcbnew, logo, pcbnew.B_SilkS, -1_000_000, -1_000_000, 1_000_000, 1_000_000):
        logo.Add(shape)
    logo.SetPosition(_v(pcbnew, *centre))
    if locked_hole:
        footprint('H1', 17 * MM, 9 * MM, {'': (0, 0, 2_200_000, 2_200_000, None, 'npth')}, locked=True)
    pcbnew.SaveBoard(str(path), board)
    return path


def intent():
    return {'fixed_poses': [{'ref': 'FID', 'x': 3.0, 'y': 3.0, 'rot': 0.0, 'side': 'F', 'basis': 'mechanical'}],
            'edge_connectors': [], 'proximity': []}


def groups():
    return {'groups': [{'name': 'g', 'anchor': 'R1', 'refs': ['R1', 'C1']}]}


def engine():
    return job.rust_executable()


class Fixture(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.dir = Path(tempfile.mkdtemp(prefix='ee515-'))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.board = build_board(self.dir / 'board.kicad_pcb')
        self.project = self.dir / 'board.kicad_pro'
        self.project.write_text('{}\n')

    def problem(self):
        return job.capture(self.board, intent(), groups())

    def search(self, seeds=2, steps=20000):
        return {'seeds': [{'seed': s, 'start': 'scattered' if s % 2 == 0 else 'clustered'} for s in range(seeds)],
                'steps_per_seed': steps, 'shortlist': 2}


class CaptureTests(Fixture):
    def test_capture_roles_and_labelled_derived_occupancy(self):
        problem, record = self.problem()
        roles = {r['native_ref']: r for r in problem['footprints']}
        self.assertEqual(len(problem['footprints']), 6)
        self.assertEqual(roles['FID']['role'], 'fixed_copper_mark')
        self.assertEqual(roles['FID']['occupancy'], {'status': 'none'})
        self.assertEqual(roles['']['role'], 'silk_only')
        self.assertEqual(roles['H1']['role'], 'fixed_body')
        for ref, rung in (('R1', 'fab+pads'), ('C1', 'fab+pads'), ('U1', 'silk+pads')):
            self.assertEqual(roles[ref]['role'], 'movable')
            self.assertEqual(roles[ref]['occupancy']['label'], 'derived_occupancy')
            self.assertEqual(roles[ref]['occupancy']['rung'], rung)
        notes = {r['native_ref']: r for r in record['footprints']}
        self.assertIn('not proof of package body separation', notes['R1']['occupancy']['note'])
        self.assertEqual(problem['problem_sha256'], hashlib.sha256(
            job.canonical({k: v for k, v in problem.items() if k != 'problem_sha256'})).hexdigest())

    def test_malformed_courtyard_is_a_hard_refusal(self):
        build_board(self.board, malformed_courtyard=True)
        with self.assertRaisesRegex(job.JobRefusal, 'malformed courtyard'):
            self.problem()

    def test_duplicate_uuid_alias_collision_is_refused_by_the_engine(self):
        problem, _ = self.problem()
        problem['footprints'][1]['uuid'] = problem['footprints'][0]['uuid']
        with self.assertRaisesRegex(job.JobRefusal, 'duplicate native footprint UUID'):
            job.solve(problem, self.search(1, 10), self.dir, deadline_s=30)

    def test_changed_input_bytes_are_refused_by_hash(self):
        entry = {'path': str(self.board), 'sha256': '0' * 64}
        with self.assertRaisesRegex(job.JobRefusal, 'does not match its declared sha256'):
            job.verified_input(entry, 'board')


class NativeTransformTests(Fixture):
    def test_engine_transform_equals_native_save_reload_for_every_quarter_turn(self):
        problem, _ = self.problem()
        u1 = next(r for r in problem['footprints'] if r['native_ref'] == 'U1')
        for q in range(4):
            evaluated = self._evaluate(problem, {u1['uuid']: [12 * MM, 5 * MM, q]})
            poses = evaluated['poses']
            check = job.materialize(self.board, poses, self.dir / f'q{q}', b'{}\n', intent=intent())
            self.assertEqual(check['problems'], [], q)
            self.assertEqual(check['integrity'], 'preserved')
            row = next(p for p in poses if p['uuid'] == u1['uuid'])
            native = check['inventory'][u1['uuid']]['pads']
            self.assertEqual({p['uuid']: (p['x_nm'], p['y_nm']) for p in row['pads']},
                             {u: (p['x_nm'], p['y_nm']) for u, p in native.items()}, q)

    def test_reload_detects_wrong_pad_position_and_moved_fixed_part(self):
        problem, _ = self.problem()
        u1 = next(r for r in problem['footprints'] if r['native_ref'] == 'U1')
        poses = self._evaluate(problem, {u1['uuid']: [12 * MM, 5 * MM, 1]})['poses']
        wrong = json.loads(json.dumps(poses))
        row = next(p for p in wrong if p['uuid'] == u1['uuid'])
        row['pads'][0]['x_nm'] += 1
        check = job.materialize(self.board, wrong, self.dir / 'wrong', b'{}\n', intent=intent())
        self.assertEqual(check['integrity'], 'violated')
        self.assertTrue(any('differs from the engine transform' in p for p in check['problems']))
        fixed = json.loads(json.dumps(poses))
        fid = next(p for p in fixed if p['native_ref'] == 'FID')
        fid['moved'], fid['x_nm'] = True, fid['x_nm'] + MM
        with self.assertRaisesRegex(job.JobRefusal, 'protected'):
            job.materialize(self.board, fixed, self.dir / 'fixed', b'{}\n', intent=intent())
        self.assertFalse((self.dir / 'fixed/board.kicad_pcb').exists())

    def test_uuid_round_trip_preserves_every_footprint(self):
        problem, _ = self.problem()
        output, _ = job.solve(problem, self.search(2, 30000), self.dir, deadline_s=60)
        self.assertTrue(output['shortlist'], output['seeds'])
        candidate = output['shortlist'][0]
        check = job.materialize(self.board, candidate['poses'], self.dir / 'c', b'{}\n', intent=intent())
        self.assertEqual(check['problems'], [])
        import pcbnew
        before = job.native_inventory(pcbnew.LoadBoard(str(self.board)))
        self.assertEqual(set(check['inventory']), set(before))
        for uuid, row in before.items():
            self.assertEqual(check['inventory'][uuid]['ref'], row['ref'])
            self.assertEqual(set(check['inventory'][uuid]['pads']), set(row['pads']))
        self.assertEqual((self.dir / 'c' / 'board.kicad_pro').read_bytes(), b'{}\n')

    def _evaluate(self, problem, poses):
        import subprocess
        payload = dict(problem, evaluate=[poses])
        done = subprocess.run([str(engine())], input=json.dumps(payload).encode(), capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout[-2000:])
        return json.loads(done.stdout)['evaluations'][0]


class MechanicalSelectionTests(Fixture):
    """Separate from esp_prog: a non-empty selection of fixed mechanical/copper objects
    (fiducial copper mark, locked NPTH hole) constrains the search and never moves."""

    def test_fixed_mechanical_objects_constrain_and_survive_search(self):
        problem, _ = self.problem()
        fixed = [r for r in problem['footprints'] if r['role'] in ('fixed_body', 'fixed_copper_mark')]
        self.assertEqual({r['native_ref'] for r in fixed}, {'FID', 'H1'})
        hole = next(r for r in problem['footprints'] if r['native_ref'] == 'H1')
        r1 = next(r for r in problem['footprints'] if r['native_ref'] == 'R1')
        on_hole = self._terms(problem, {r1['uuid']: [hole['pose']['x_nm'], hole['pose']['y_nm'], 0]})
        self.assertFalse(on_hole['feasible'])
        self.assertTrue(any(v.get('with') == 'H1' or v.get('ref') == 'H1' for v in on_hole['violations']))
        output, _ = job.solve(problem, self.search(2, 30000), self.dir, deadline_s=60)
        for candidate in output['shortlist']:
            rows = {p['native_ref']: p for p in candidate['poses']}
            for ref in ('FID', 'H1', ''):
                self.assertFalse(rows[ref]['moved'])

    def _terms(self, problem, poses):
        import subprocess
        done = subprocess.run([str(engine())], input=json.dumps(dict(problem, evaluate=[poses])).encode(),
                              capture_output=True, timeout=60)
        return json.loads(done.stdout)['evaluations'][0]


class CustodyTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='ee515-custody-'))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def entry(self, cost, **kw):
        return {'index': 0, 'seed': 0, 'engine_cost': str(cost), 'status': 'checked', 'integrity': 'preserved',
                'edge_intent': {'status': 'pass'},
                'placement_introduced_errors': 0, 'engine_feasible': True, 'engine_violations': [], **kw}

    def test_failed_trials_never_replace_the_incumbent(self):
        good = self.entry(100, board_sha256='a' * 64)
        self.assertTrue(job.promote(self.dir, good))
        pointer = (self.dir / 'incumbent.json').read_bytes()
        for failed in (self.entry(1, integrity='violated'), self.entry(1, status='failed'),
                       self.entry(1, edge_intent={}), self.entry(1, edge_intent={'status': 'failed'}),
                       self.entry(1, placement_introduced_errors=1), self.entry(1, engine_feasible=False),
                       self.entry(1, engine_violations=[{'kind': 'edge_setback'}]), self.entry(500)):
            self.assertFalse(job.promote(self.dir, failed))
            self.assertEqual((self.dir / 'incumbent.json').read_bytes(), pointer)
        better = self.entry(50, board_sha256='b' * 64)
        self.assertTrue(job.promote(self.dir, better))
        self.assertEqual(json.loads((self.dir / 'incumbent.json').read_text())['board_sha256'], 'b' * 64)
        self.assertFalse(list(self.dir.glob('.incumbent*')), 'no partial pointer is left behind')

    def test_diagnostic_is_retained_separately_and_never_promoted(self):
        diagnostic = self.entry(10, engine_feasible=False, engine_violations=[{'kind': 'pair'}],
                                placement_introduced_errors=1)
        self.assertFalse(job.promote(self.dir, diagnostic))
        self.assertTrue(job.retain_diagnostic(self.dir, diagnostic))
        self.assertFalse((self.dir / 'incumbent.json').exists())
        self.assertFalse(job.retain_diagnostic(self.dir, dict(diagnostic, integrity='violated')))

    def test_checkpoint_is_atomic_and_bound_to_identity(self):
        job.checkpoint(self.dir, {'identity': 'x', 'completed_seeds': [0]})
        self.assertEqual(job.load_checkpoint(self.dir, 'x')['completed_seeds'], [0])
        with self.assertRaisesRegex(job.JobRefusal, 'different job identity'):
            job.load_checkpoint(self.dir, 'y')
        self.assertFalse(list(self.dir.glob('.checkpoint*')))


class LimitTests(Fixture):
    def test_cancel_stops_the_engine_within_bounds_and_keeps_no_candidate(self):
        problem, _ = self.problem()
        (self.dir / 'CANCEL').write_text('')
        started = time.monotonic()
        output, _ = job.solve(problem, self.search(4, 10_000_000), self.dir, deadline_s=120)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(output['status'], 'cancelled')
        self.assertEqual(output['shortlist'], [])

    def test_deadline_bounds_the_whole_portfolio(self):
        problem, _ = self.problem()
        started = time.monotonic()
        output, execution = job.solve(problem, self.search(6, 50_000_000), self.dir, deadline_s=1.5)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(output['status'], 'deadline')
        if execution.get('interruption'):
            self.assertEqual(execution['interruption'], 'deadline')
            self.assertIsNone(output['timing']['prepare_s'], 'an interrupted child has no measured prepare time')
            self.assertIsNone(output['prng'], 'no final solver record was received')
            self.assertEqual(output['shortlist'], [])
            self.assertNotIn('not_started', {s['status'] for s in output['seeds']},
                             'unobserved seed progress must not be invented')
        else:
            self.assertIn('not_started', {s['status'] for s in output['seeds']})

    def test_resume_skips_completed_seeds_and_replays_the_rest(self):
        problem, _ = self.problem()
        events = []
        full, _ = job.solve(problem, self.search(2, 20000), self.dir, deadline_s=60, on_seed=events.append)
        self.assertEqual([e['seed'] for e in events], [0, 1])
        resumed, _ = job.solve(problem, dict(self.search(2, 20000), completed_seeds=[0]), self.dir, deadline_s=60)
        self.assertEqual(resumed['seeds'][0]['status'], 'skipped_completed')
        self.assertEqual(resumed['seeds'][1]['best_cost'], full['seeds'][1]['best_cost'])


class EntryPointTests(Fixture):
    def test_one_job_runs_capture_search_checks_route_and_final_checks(self):
        require_kicad_route()
        files = {'board': self.board, 'project': self.project, 'intent': self.dir / 'intent.json',
                 'groups': self.dir / 'groups.json'}
        files['intent'].write_text(json.dumps(intent()))
        files['groups'].write_text(json.dumps(groups()))
        config = {'schema': job.CONFIG_SCHEMA, 'budget_s': 300,
                  'inputs': {k: {'path': str(v), 'sha256': hashlib.sha256(Path(v).read_bytes()).hexdigest()}
                             for k, v in files.items()},
                  'search': self.search(2, 30000),
                  'router': {'checkout': os.environ['AGENT_EE_KICAD_ROUTE_CHECKOUT'], 'budget_s': 60}}
        before = {k: Path(v).read_bytes() for k, v in files.items()}
        config_path = self.dir / 'job-config.json'
        config_path.write_text(json.dumps(config))
        import subprocess
        done = subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'place', 'run',
                               str(config_path), '--out', str(self.dir / 'job')],
                              capture_output=True, text=True, timeout=330)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        report = json.loads((self.dir / 'job/job-report.json').read_text())
        self.assertEqual(report['status'], 'completed', report.get('route'))
        self.assertEqual(report['routed_candidate'], 'incumbent')
        self.assertEqual(report['design_name'], self.board.stem)
        argv = report['route']['argv']
        self.assertEqual(argv[argv.index('--name') + 1], self.board.stem)
        self.assertEqual(report['routing_trials'][-1]['status'], 'completed')
        self.assertEqual(report['route_selection']['board_sha256'], report['routing_trials'][-1]['board_sha256'])
        self.assertEqual(set(report['phases']), set(job.PHASES))
        self.assertEqual(report['claims'], {'accepted': False, 'clean': False, 'superiority': None})
        final = report['final']
        self.assertEqual(final['identity_problems'], [])
        self.assertEqual(final['drc']['exit_code'], 0)
        self.assertIsInstance(final['metrics']['vias'], int)
        self.assertEqual({k: Path(v).read_bytes() for k, v in files.items()}, before, 'inputs are immutable')
        with self.assertRaisesRegex(job.JobRefusal, 'not empty'):
            job.run_job(config, self.dir / 'job')

    def test_cancelled_job_keeps_no_candidate_and_resumes_under_the_same_identity(self):
        require_kicad_route()
        config = self._config()
        out = self.dir / 'job'
        out.mkdir()
        (out / 'CANCEL').write_text('')
        cancelled = job.run_job(config, out)
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertEqual(cancelled['engine']['status'], 'cancelled')
        self.assertFalse((out / 'incumbent.json').exists())
        (out / 'CANCEL').unlink()
        resumed = job.run_job(config, out, resume=True)
        self.assertEqual(resumed['attempt'], 2)
        self.assertEqual(resumed['identity'], cancelled['identity'])
        self.assertEqual(resumed['status'], 'completed', resumed.get('route'))
        self.assertTrue((out / 'job-report-a1.json').exists() and (out / 'job-report-a2.json').exists())
        changed = dict(config, search=dict(config['search'], steps_per_seed=config['search']['steps_per_seed'] + 1))
        with self.assertRaisesRegex(job.JobRefusal, 'different job identity'):
            job.run_job(changed, out, resume=True)

    def _config(self):
        files = {'board': self.board, 'project': self.project, 'intent': self.dir / 'intent.json',
                 'groups': self.dir / 'groups.json'}
        files['intent'].write_text(json.dumps(intent()))
        files['groups'].write_text(json.dumps(groups()))
        return {'schema': job.CONFIG_SCHEMA, 'budget_s': 300,
                'inputs': {k: {'path': str(v), 'sha256': hashlib.sha256(Path(v).read_bytes()).hexdigest()}
                           for k, v in files.items()},
                'search': self.search(2, 30000),
                'router': {'checkout': os.environ['AGENT_EE_KICAD_ROUTE_CHECKOUT'], 'budget_s': 60}}


class EspProgCaptureTests(unittest.TestCase):
    def test_unchanged_esp_prog_capture_is_accepted_with_labelled_derived_occupancy(self):
        if not os.environ.get('AGENT_EE_ESP_PROG_PREPARED'):
            if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
                self.fail('AGENT_EE_REQUIRE_TOOLCHAIN=1: external esp_prog bundle not mounted '
                          '(AGENT_EE_ESP_PROG_PREPARED)')
            self.skipTest('external esp_prog bundle not mounted')
        require_toolchain(native=True)
        root = Path(os.environ['AGENT_EE_ESP_PROG_PREPARED'])
        for name, sha in ESP_PROG.items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), sha, name)
        problem, record = job.capture(root / 'board.kicad_pcb', json.loads((root / 'intent.json').read_text()),
                                      json.loads((root / 'groups.json').read_text()))
        rows = problem['footprints']
        self.assertEqual(len(rows), 21)
        self.assertEqual(len({r['uuid'] for r in rows}), 21)
        self.assertEqual(sum(r['native_ref'] == '' and r['role'] == 'silk_only' for r in rows), 3)
        self.assertEqual(sum(r['native_ref'] == 'Ref*' and r['role'] == 'fixed_copper_mark' for r in rows), 2)
        movable = [r for r in rows if r['role'] == 'movable']
        self.assertEqual(len(movable), 16)
        self.assertTrue(all(r['occupancy']['label'] == 'derived_occupancy' for r in movable))
        self.assertTrue(all(r['occupancy']['rung'] in ('fab+pads', 'silk+pads', 'pads') for r in movable))
        self.assertEqual(hashlib.sha256((root / 'board.kicad_pcb').read_bytes()).hexdigest(), ESP_PROG['board.kicad_pcb'])


if __name__ == '__main__':
    unittest.main()

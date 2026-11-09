"""Real-board features the placement job accepts (AGENTEE-635, AGENTEE-636).

Copper pours, outlines that are not rectangles, parts at angles other than a
quarter turn, and routing-only jobs. Every board here is built through pcbnew
from the synthetic fixture of ``test_placement_job``; no third-party board is used.
"""
import math
import unittest

from agent_ee import placement_job as job
from agent_ee import placement_problem
from tests.test_placement_job import Fixture, _segment, _v, groups, intent, MM
from tests.test_placement_job_clearance import evaluate


class Boards(Fixture):
    def edit(self, change):
        import pcbnew
        board = pcbnew.LoadBoard(str(self.board))
        change(pcbnew, board)
        pcbnew.SaveBoard(str(self.board), board)
        self.project.write_text('{}\n')

    def capture(self, declared=None, grouped=None):
        return job.capture(self.board, job.normalize_intent(
            intent() if declared is None else declared, groups() if grouped is None else grouped))

    @staticmethod
    def outline(pcbnew, board, points, extra=()):
        """Replace the board edge with a closed chain through ``points`` (mm) plus ``extra`` shapes."""
        for item in [d for d in board.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]:
            board.Remove(item)
        for a, b in zip(points, points[1:] + points[:1]):
            if a is not None and b is not None:
                board.Add(_segment(pcbnew, board, pcbnew.Edge_Cuts, (a[0] * MM, a[1] * MM), (b[0] * MM, b[1] * MM)))
        for make in extra:
            board.Add(make(pcbnew, board))

    @staticmethod
    def by_ref(problem):
        return {row['native_ref']: row for row in problem['footprints']}

    def outside(self, problem, ref, x_mm, y_mm):
        """Whether the engine reports ``ref`` at (x, y) as off the board."""
        uuid = self.by_ref(problem)[ref]['uuid']
        result = evaluate(problem, [{uuid: [int(x_mm * MM), int(y_mm * MM), 0]}])[0]
        return any(v.get('ref') == ref and v['kind'] in ('occupancy_outside_outline', 'copper_edge_clearance')
                   for v in result['violations'])


L_SHAPE = [(0, 0), (20, 0), (20, 6), (12, 6), (12, 12), (0, 12)]   # the fixture board less its lower-right quarter


class OutlineTests(Boards):
    def test_rectangle_is_passed_as_before(self):
        problem, record = self.capture()
        self.assertEqual(problem['outline'], {'kind': 'rectangle', 'rect_nm': [0, 0, 20 * MM, 12 * MM]})
        self.assertEqual(record['outline'], problem['outline'])

    def test_l_shaped_outline_is_the_real_board_area(self):
        self.edit(lambda k, board: self.outline(k, board, L_SHAPE))
        problem, record = self.capture()
        outline = problem['outline']
        self.assertEqual((outline['kind'], outline['rect_nm']), ('polygon', [0, 0, 20 * MM, 12 * MM]))
        self.assertEqual(sorted(map(tuple, outline['polygon_nm'])), sorted((x * MM, y * MM) for x, y in L_SHAPE))
        self.assertEqual(outline['holes_nm'], [])
        self.assertNotIn('polygon_nm', record['outline'])
        self.assertFalse(self.outside(problem, 'R1', 4, 9))      # in the leg
        self.assertFalse(self.outside(problem, 'R1', 16, 3))     # in the arm
        self.assertTrue(self.outside(problem, 'R1', 16, 9))      # in the removed quarter, inside the bounds
        self.assertTrue(self.outside(problem, 'R1', 12.5, 9))    # across the inner edge

    def test_arcs_and_cut_outs_are_accepted(self):
        def rounded(k, board):
            # A quarter-circle corner of radius 2 mm at the lower right, and a 3 mm round cut-out.
            corner = [(0, 0), (20, 0), (20, 10), None, (18, 12), (0, 12)]

            def arc(k, board):
                shape = k.PCB_SHAPE(board)
                shape.SetShape(k.SHAPE_T_ARC)
                shape.SetLayer(k.Edge_Cuts)
                shape.SetWidth(100_000)
                mid = (18 + 2 * math.cos(math.pi / 4), 10 + 2 * math.sin(math.pi / 4))
                shape.SetArcGeometry(_v(k, 20 * MM, 10 * MM), _v(k, mid[0] * MM, mid[1] * MM), _v(k, 18 * MM, 12 * MM))
                return shape

            def hole(k, board):
                shape = k.PCB_SHAPE(board)
                shape.SetShape(k.SHAPE_T_CIRCLE)
                shape.SetLayer(k.Edge_Cuts)
                shape.SetWidth(100_000)
                shape.SetCenter(_v(k, 5 * MM, 6 * MM))
                shape.SetEnd(_v(k, 6.5 * MM, 6 * MM))
                return shape
            self.outline(k, board, corner, extra=(arc, hole))
        self.edit(rounded)
        problem, _ = self.capture()
        outline = problem['outline']
        self.assertEqual((outline['kind'], outline['rect_nm']), ('polygon', [0, 0, 20 * MM, 12 * MM]))
        self.assertGreater(len(outline['polygon_nm']), 6)        # the arc became segments
        self.assertEqual(len(outline['holes_nm']), 1)
        self.assertGreater(outline['arc_max_error_nm'], 0)
        for x, y in outline['polygon_nm']:                       # every arc vertex within the stated error
            if x > 18 * MM and y > 10 * MM:
                self.assertAlmostEqual(math.hypot(x - 18 * MM, y - 10 * MM), 2 * MM, delta=2 * outline['arc_max_error_nm'])
        self.assertTrue(self.outside(problem, 'R1', 5, 6))       # on the cut-out
        self.assertTrue(self.outside(problem, 'R1', 19.2, 11.5)) # beyond the rounded corner
        self.assertFalse(self.outside(problem, 'R1', 12, 3))

    def test_open_outline_refuses_a_placement_but_not_a_routing_only_job(self):
        self.edit(lambda k, board: self.outline(k, board, [(0, 0), (20, 0), (20, 12), (0, 12), None]))
        with self.assertRaisesRegex(job.JobRefusal, 'board outline is not one closed contour'):
            self.capture()

        def lock(k, board):
            for fp in board.GetFootprints():
                fp.SetLocked(True)
        self.edit(lock)
        problem, record = self.capture(grouped={'groups': []})
        self.assertTrue(record['routing_only'])
        self.assertEqual(problem['outline']['kind'], 'rectangle')

    def test_declared_edge_connector_needs_a_rectangle(self):
        self.edit(lambda k, board: self.outline(k, board, L_SHAPE))
        declared = dict(intent(), edge_connectors=[{'ref': 'U1', 'edge': 'west'}])
        with self.assertRaisesRegex(job.JobRefusal, 'declared edge connectors need a rectangular board outline'):
            self.capture(declared)


class RotationTests(Boards):
    def turn(self, ref, degrees, locked=False):
        def change(k, board):
            fp = board.FindFootprintByReference(ref)
            fp.SetOrientationDegrees(degrees)
            fp.SetLocked(locked)
        self.edit(change)

    def test_split_turns(self):
        for degrees, expected in ((0.0, (0, 0.0)), (90.0, (1, 0.0)), (-90.0, (3, 0.0)), (360.0, (0, 0.0)),
                                  (45.0, (0, 45.0)), (135.0, (1, 45.0)), (-45.0, (3, 45.0)), (-135.0, (2, 45.0)),
                                  (89.9999999999, (1, 0.0)), (30.0, (0, 30.0)), (200.0, (2, 20.0))):
            with self.subTest(degrees=degrees):
                turns, residual = placement_problem.split_turns(degrees)
                self.assertEqual(turns, expected[0])
                self.assertAlmostEqual(residual, expected[1], places=9)

    def test_part_at_45_degrees_is_captured_with_the_bounds_of_its_rotated_shapes(self):
        straight = self.by_ref(self.capture()[0])['R1']
        self.turn('R1', 135.0)
        turned = self.by_ref(self.capture()[0])['R1']
        self.assertEqual(turned['role'], 'movable')
        self.assertEqual((turned['pose']['quarter_turns'], turned['pose']['residual_deg']), (1, 45.0))
        self.assertNotIn('residual_deg', straight['pose'])
        self.assertEqual(turned['rotations'], [0, 1, 2, 3])
        # The bounds of the rotated pads and fab drawing are square and hold more area than the body.
        x0, y0, x1, y1 = turned['occupancy']['local_rect_nm']
        sx0, sy0, sx1, sy1 = straight['occupancy']['local_rect_nm']
        self.assertAlmostEqual(x1 - x0, y1 - y0, delta=2)
        self.assertGreater((x1 - x0) * (y1 - y0), (sx1 - sx0) * (sy1 - sy0))
        # The copper polygons are the rotated pads themselves, not their bounds.
        for copper in turned['copper_local_nm']:
            polygon = copper['geometry']['polygons'][0]
            area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(polygon, polygon[1:] + polygon[:1]))) / 2
            self.assertAlmostEqual(area, 800_000 * 900_000, delta=0.02 * 800_000 * 900_000)

    def test_moved_part_keeps_its_angle_offset_on_the_saved_board(self):
        import pcbnew
        self.turn('R1', 45.0)
        normalized = job.normalize_intent(intent(), groups())
        problem, _ = job.capture(self.board, normalized)
        rows = self.by_ref(problem)
        moves = {rows['R1']['uuid']: [4 * MM, 3 * MM, 1], rows['C1']['uuid']: [15 * MM, 3 * MM, 0],
                 rows['U1']['uuid']: [10 * MM, 9 * MM, 2]}
        poses = evaluate(problem, [moves])[0]['poses']
        check = job.materialize(self.board, poses, self.dir / 'placed', self.project.read_bytes(), normalized=normalized)
        self.assertEqual((check['integrity'], check['problems']), ('preserved', []))
        saved = pcbnew.LoadBoard(check['board'])
        r1 = saved.FindFootprintByReference('R1')
        self.assertAlmostEqual(r1.GetOrientationDegrees() % 360.0, 135.0, places=6)
        self.assertEqual((r1.GetPosition().x, r1.GetPosition().y), (4 * MM, 3 * MM))
        self.assertAlmostEqual(saved.FindFootprintByReference('U1').GetOrientationDegrees() % 360.0, 180.0, places=6)

    def test_locked_part_at_any_angle_is_accepted_and_stays(self):
        self.turn('R1', 33.0, locked=True)
        normalized = job.normalize_intent(intent(), groups())
        problem, _ = job.capture(self.board, normalized)
        row = self.by_ref(problem)['R1']
        self.assertEqual((row['role'], row['rotations']), ('fixed_body', [0]))
        self.assertAlmostEqual(row['pose']['residual_deg'], 33.0, places=9)
        poses = evaluate(problem, [{}])[0]['poses']
        check = job.materialize(self.board, poses, self.dir / 'placed', self.project.read_bytes(), normalized=normalized)
        self.assertEqual((check['integrity'], check['problems'], check['moved']), ('preserved', [], []))

    def test_declared_fixed_pose_rotation_is_compared_in_degrees(self):
        self.turn('FID', 45.0)
        fixed = lambda rot: dict(intent(), fixed_poses=[dict(intent()['fixed_poses'][0], rot=rot)])
        self.capture(fixed(45.0))
        self.capture(fixed(405.0))
        with self.assertRaisesRegex(job.JobRefusal, 'differs from the fixed native rotation'):
            self.capture(fixed(0.0))


class RoutingOnlyTests(Boards):
    def trapezoid(self, k, board):
        """A pad shape the placement model does not represent."""
        pad = next(iter(board.FindFootprintByReference('C1').Pads()))
        pad.SetShape(k.PADSTACK.ALL_LAYERS, k.PAD_SHAPE_TRAPEZOID)

    def lock_all(self, k, board):
        for fp in board.GetFootprints():
            fp.SetLocked(True)

    def test_placement_needs_the_model_and_refuses_what_it_cannot_represent(self):
        self.edit(self.trapezoid)
        with self.assertRaisesRegex(job.JobRefusal, 'unsupported native pad shape'):
            self.capture()

    def test_fully_fixed_board_is_accepted_without_the_placement_model(self):
        self.edit(lambda k, board: (self.trapezoid(k, board), self.lock_all(k, board)))
        problem, record = self.capture()
        self.assertTrue(record['routing_only'])
        self.assertEqual(record['placement_model'], 'not built (routing-only job)')
        rows = self.by_ref(problem)
        self.assertEqual({row['role'] for row in problem['footprints']}, {'fixed_body'})
        self.assertEqual({row['occupancy']['status'] for row in problem['footprints']}, {'none'})
        self.assertEqual([p['net'] for p in rows['C1']['pads']], ['A', 'B'])
        self.assertEqual(rows['C1']['copper_local_nm'], [])
        # The engine grades the one placement there is: nothing to move, nothing violated.
        result = evaluate(problem, [{}])[0]
        self.assertTrue(result['feasible'], result)
        self.assertFalse(any(row['moved'] for row in result['poses']))
        # The saved-board measurement makes the same choice of model.
        measured = job._evaluate_board_native(self.board, fixed_poses=intent()['fixed_poses'], groups=groups()['groups'])
        self.assertTrue(measured['feasible'])

    def test_one_movable_part_brings_the_model_back(self):
        def change(k, board):
            self.trapezoid(k, board)
            self.lock_all(k, board)
            board.FindFootprintByReference('R1').SetLocked(False)
        self.edit(change)
        with self.assertRaisesRegex(job.JobRefusal, 'unsupported native pad shape'):
            self.capture()

    def test_declared_edge_check_keeps_the_model_on_a_fixed_board(self):
        self.edit(self.lock_all)
        declared = dict(intent(), edge_connectors=[{'ref': 'U1', 'edge': 'west'}])
        problem, record = self.capture(declared)
        self.assertTrue(record['routing_only'])
        self.assertEqual(record['placement_model'], 'built')
        self.assertEqual(self.by_ref(problem)['U1']['occupancy']['rung'], 'silk+pads')


class PourTests(Boards):
    def pour(self, k, board):
        zone = k.ZONE(board)
        zone.SetLayer(k.F_Cu)
        zone.SetNet(board.FindNet('GND'))
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in ((1, 1), (19, 1), (19, 11), (1, 11)):
            outline.Append(x * MM, y * MM)
        board.Add(zone)

    def test_pour_is_kept_refilled_and_ignored_by_the_integrity_comparison(self):
        import pcbnew
        self.edit(self.pour)
        self.assertNotIn(b'filled_polygon', self.board.read_bytes())
        normalized = job.normalize_intent(intent(), groups())
        problem, record = job.capture(self.board, normalized)
        self.assertEqual(record['pour_zones'], 1)
        rows = self.by_ref(problem)
        moves = {rows['R1']['uuid']: [4 * MM, 3 * MM, 0], rows['C1']['uuid']: [15 * MM, 3 * MM, 0],
                 rows['U1']['uuid']: [10 * MM, 9 * MM, 0]}
        poses = evaluate(problem, [moves])[0]['poses']
        check = job.materialize(self.board, poses, self.dir / 'placed', self.project.read_bytes(), normalized=normalized)
        self.assertEqual((check['integrity'], check['problems']), ('preserved', []))
        self.assertEqual(len(check['moved']), 3)
        saved = pcbnew.LoadBoard(check['board'])
        zone = next(iter(saved.Zones()))
        self.assertTrue(zone.IsFilled())
        self.assertIn(b'filled_polygon', (self.dir / 'placed/board.kicad_pcb').read_bytes())
        # A changed zone outline is still protected content.
        text = self.board.read_bytes()
        nodes = job._non_footprint_nodes(text)
        self.assertNotEqual(nodes, job._non_footprint_nodes(text.replace(b'(xy 19 11)', b'(xy 18 11)')))

    def test_native_drc_judges_the_refilled_board(self):
        self.edit(self.pour)
        report, execution = job.drc(self.board, self.dir, refill=True)
        self.assertIn('--refill-zones', execution['argv'])
        self.assertIsInstance(report['violations'], list)


class ErrorAttributionTests(unittest.TestCase):
    def test_input_error_between_unmoved_items_is_pre_existing(self):
        owners = {'pad-a': 'fp-a', 'pad-b': 'fp-b'}
        edge = {'type': 'copper_edge_clearance', 'severity': 'error', 'items': [{'uuid': 'pad-a'}, {'uuid': 'edge'}]}
        pair = {'type': 'clearance', 'severity': 'error', 'items': [{'uuid': 'pad-a'}, {'uuid': 'pad-b'}]}
        fresh = {'type': 'clearance', 'severity': 'error', 'items': [{'uuid': 'pad-b'}, {'uuid': 'edge'}]}
        report = {'violations': [edge, pair, fresh]}
        baseline = {job._key(edge), job._key(pair)}
        count = lambda split: {k: len(v) for k, v in split.items()}
        # Without the moved set, only an error inside one footprint can be called pre-existing.
        self.assertEqual(count(job.classify_errors(report, owners, baseline)),
                         {'pre_existing': 0, 'placement_introduced': 3, 'routing_introduced': 0})
        self.assertEqual(count(job.classify_errors(report, owners, baseline, moved=[])),
                         {'pre_existing': 2, 'placement_introduced': 1, 'routing_introduced': 0})
        # A moved footprint owns its errors again, even when the input reported the same pair.
        self.assertEqual(count(job.classify_errors(report, owners, baseline, moved=['fp-b'])),
                         {'pre_existing': 1, 'placement_introduced': 2, 'routing_introduced': 0})
        self.assertEqual(count(job.classify_errors(report, owners, baseline, {job._key(edge), job._key(pair)}, moved=[])),
                         {'pre_existing': 2, 'placement_introduced': 0, 'routing_introduced': 1})


if __name__ == '__main__':
    unittest.main()

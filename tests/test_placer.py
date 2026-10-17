"""Deterministic packing public behavior over independent integer geometry."""
import unittest

from agent_ee.placer import pack
from test_placement import capture, part


def request(**changes):
    params = {'schema': 'agent-ee.packing-policy/1', 'step_budget': 10000,
              'gap_nm': 500000, 'fixed_refs': ['U1'], 'priority_refs': [],
              'ground_nets': ['GND'], 'obstacles': []}
    params.update(changes)
    return params


class PlacerTest(unittest.TestCase):
    def test_deterministic_plan_keeps_static_part_and_passes_judge(self):
        board = capture([part('U1', 2, 2), part('C1', 2, 2)])
        first = pack(board, request())
        self.assertEqual(first['status'], 'planned')
        self.assertEqual(first, pack(board, request()))
        by_ref = {p['ref']: p for p in first['placements']}
        self.assertEqual(by_ref['U1']['position_nm'], [2000000, 2000000])
        self.assertNotEqual(by_ref['C1']['position_nm'], [2000000, 2000000])
        self.assertEqual(first['judgment']['verdict'], 'pass')
        self.assertLessEqual(first['steps_used'], 10000)

    def test_proved_too_small_is_defect_but_budget_is_unknown(self):
        from test_placement import rectangle
        board = capture([part('U1', 0, 0), part('C1', 0, 0)], rectangle(0, 0, 2, 2))
        report = pack(board, request(fixed_refs=[]))
        self.assertEqual((report['status'], report['outcome']), ('packing_infeasible', 'defect'))
        self.assertEqual(report['placements'], [])
        board = capture([part('U1', 2, 2), part('C1', 2, 2)])
        report = pack(board, request(step_budget=1))
        self.assertEqual((report['status'], report['outcome']), ('packing_budget_exhausted', 'unknown'))
        self.assertEqual(report['steps_used'], 1)
        self.assertEqual(report['placements'], [])

    def test_unsupported_geometry_does_not_place_and_inputs_unchanged(self):
        import copy
        board = capture([part('U1', 2, 2), part('C1', 2, 2)])
        board['footprints'][1]['courtyards'] = {}
        original = copy.deepcopy(board)
        report = pack(board, request())
        self.assertEqual(report['status'], 'unsupported_geometry')
        self.assertEqual(board, original)

    def test_locked_and_obstacles_are_respected(self):
        from test_placement import rectangle
        board = capture([part('U1', 2, 2), part('C1', 2, 2)])
        board['footprints'][0]['locked'] = True
        policy = request(fixed_refs=[], obstacles=[{'side': 'F', 'polygons': [rectangle(0, 0, 1, 1)]}])
        plan = pack(board, policy)
        self.assertEqual(plan['status'], 'planned')
        self.assertTrue(next(p for p in plan['placements'] if p['ref'] == 'U1')['fixed'])
        policy['obstacles'] = [{'side': 'F', 'polygons': [rectangle(2, 2, 2, 2)]}]
        self.assertEqual(pack(board, policy)['status'], 'fixed_conflict')

    def test_net_cost_and_nonrectangular_pad_refusal(self):
        board = capture([part('U1', 10, 10), part('C1', 2, 2)])
        for fp in board['footprints']:
            fp['pads'] = [{'net': 'VCC', 'position_nm': list(fp['position_nm']),
                           'shape': 1, 'rotation_deg': 0}]
        plan = pack(board, request())
        self.assertEqual(plan['status'], 'planned')
        cap = next(p for p in plan['placements'] if p['ref'] == 'C1')
        # The pad sits at the footprint origin: rotation 270 brings it to the
        # right edge of the cap courtyard, exactly the requested .5mm gap.
        self.assertEqual(cap['position_nm'], [9500000, 10000000])
        self.assertEqual(cap['rotation_deg'], 270)
        board['footprints'][1]['pads'][0]['shape'] = 0
        self.assertEqual(pack(board, request())['status'], 'unsupported_geometry')

    def test_equal_inputs_with_reversed_polygon_winding_plan_equally(self):
        import copy
        board = capture([part('U1', 2, 2), part('C1', 2, 2)])
        reverse = copy.deepcopy(board)
        for fp in reverse['footprints']:
            fp['courtyards']['F']['polygons'][0].reverse()
        self.assertEqual(pack(board, request())['placements'], pack(reverse, request())['placements'])

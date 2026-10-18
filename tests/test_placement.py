"""Independent placement geometry examples; dimensions are integer nanometres."""
import copy
import unittest

from agent_ee.placement import judge

MM = 1_000_000


def polygon(points):
    return [[int(x * MM), int(y * MM)] for x, y in points]


def rectangle(x, y, w, h):
    return polygon([(x, y), (x+w, y), (x+w, y+h), (x, y+h)])


def part(ref, x, y, side='F', shape=None):
    return {'ref': ref, 'uuid': ref, 'value': '', 'side': side,
            'position_nm': [int(x*MM), int(y*MM)], 'rotation_deg': 0,
            'courtyards': {side: {'status': 'proven', 'polygons': [shape or rectangle(x, y, 2, 2)]}},
            'pads': []}


def capture(parts, outline=None):
    return {'schema': 'agent-ee.placement-probe/1', 'board_sha256': 'a'*64,
            'pcbnew_version': 'test example', 'outline': {'status': 'proven',
            'polygons': [outline or rectangle(0, 0, 20, 20)]}, 'footprints': parts}


class JudgeTest(unittest.TestCase):
    def test_same_side_positive_overlap_is_hard_failure(self):
        report = judge(capture([part('U1', 2, 2), part('C1', 3, 3)]))
        self.assertEqual(report['verdict'], 'hard_fail')
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(report['hard_failures'][0]['kind'], 'courtyard_overlap')
        self.assertEqual(report['hard_failures'][0]['overlap_area_mm2'], 1)

    def test_opposite_sides_and_touching_boundaries_do_not_overlap(self):
        self.assertEqual(judge(capture([part('U1', 2, 2), part('C1', 3, 3, 'B')]))['verdict'], 'pass')
        self.assertEqual(judge(capture([part('U1', 2, 2), part('C1', 4, 2)]))['verdict'], 'pass')

    def test_full_concave_polygons_not_bounding_boxes(self):
        left = polygon([(2, 2), (6, 2), (6, 3), (3, 3), (3, 6), (2, 6)])
        right = rectangle(4, 4, 1, 1)
        self.assertEqual(judge(capture([part('U1', 2, 2, shape=left), part('C1', 4, 4, shape=right)]))['verdict'], 'pass')
        # A crossing pair with no vertex inside the other still overlaps by 1mm².
        horizontal = rectangle(2, 3, 6, 1)
        vertical = rectangle(4, 1, 1, 6)
        report = judge(capture([part('U1', 2, 3, shape=horizontal), part('C1', 4, 1, shape=vertical)]))
        self.assertEqual(report['hard_failures'][0]['overlap_area_mm2'], 1)

    def test_concave_outline_outside_area_and_not_just_vertices(self):
        outline = polygon([(0, 0), (10, 0), (10, 10), (6, 10), (6, 4), (4, 4), (4, 10), (0, 10)])
        report = judge(capture([part('U1', 2, 6, shape=rectangle(2, 6, 6, 2))], outline))
        self.assertEqual(report['verdict'], 'hard_fail')
        self.assertEqual(report['hard_failures'][0]['outside_area_mm2'], 4)

    def test_missing_unsupported_invalid_geometry_is_unknown(self):
        for status in ('outline_missing', 'outline_unproven'):
            board = capture([part('U1', 2, 2)])
            board['outline'] = {'status': status}
            self.assertEqual(judge(board)['verdict'], 'unknown')
        for geometry in ({}, {'F': {'status': 'missing'}},
                         {'F': {'status': 'proven', 'polygons': [polygon([(0,0), (2,2), (0,2), (2,0)])]}}):
            board = capture([part('U1', 2, 2)])
            board['footprints'][0]['courtyards'] = geometry
            self.assertEqual(judge(board)['verdict'], 'unknown')

    def test_defect_survives_other_unknown_and_zero_score_never_proves_defect(self):
        board = capture([part('U1', 2, 2), part('C1', 3, 3), part('R1', 10, 10)])
        board['footprints'][2]['courtyards'] = {}
        self.assertEqual(judge(board)['verdict'], 'hard_fail')

    def test_nanometre_overlap_and_reversed_winding(self):
        board = capture([part('U1', 2, 2), part('C1', 4, 2)])
        poly = board['footprints'][1]['courtyards']['F']['polygons'][0]
        for p in poly:
            p[0] -= 1
        poly.reverse()
        report = judge(board)
        self.assertEqual(report['verdict'], 'hard_fail')
        self.assertEqual(report['hard_failures'][0]['overlap_area_mm2'], .000002)

    def test_owned_two_cap_policy_example_score70_and_connector_waiver(self):
        board = capture([part('U1', 1, 1), part('C1', 8, 1), part('C2', 12, 1), part('J1', 16, 1)])
        for fp in board['footprints']:
            fp['pads'] = [{'net': 'VCC'}, {'net': 'GND'}] if fp['ref'] != 'J1' else [{'net': 'VBUS'}]
        policy = {'schema': 'agent-ee.placement-policy/1', 'ground_nets': ['GND'], 'ic_refs': ['U1'],
                  'connector_refs': ['J1'], 'max_shared_refs': 8,
                  'capacitors': {'C1': {'capacitance_nf': 100, 'max_distance_mm': 2.5, 'deduction': 15},
                                 'C2': {'capacitance_nf': 1000, 'max_distance_mm': 5, 'deduction': 15}}}
        before = copy.deepcopy(board)
        report = judge(board, policy)
        self.assertEqual(report['score'], 70)
        self.assertEqual(report['verdict'], 'pass')
        self.assertEqual([a['ref'] for a in report['advisories']], ['C1', 'C2'])
        self.assertEqual(board, before)
        board['footprints'][1]['pads'][0]['net'] = 'VBUS'
        report = judge(board, policy)
        # 8 mm from J1 is beyond C1's 2.5 mm limit: no waiver, and no IC on VBUS.
        self.assertEqual(report['associations'][0]['kind'], 'unassociated')
        self.assertEqual(report['score'], 85)
        board['footprints'][3]['position_nm'] = [int(10.5*MM), MM]
        report = judge(board, policy)
        self.assertEqual(report['associations'][0], {'ref': 'C1', 'kind': 'interface_filter', 'connectors': ['J1'],
                                                     'nearest_connector': 'J1', 'distance_mm': 2.5, 'nets': ['VBUS']})
        self.assertEqual(report['score'], 85)
        policy['capacitors']['C2']['deduction'] = 150
        self.assertEqual(judge(board, policy)['verdict'], 'pass')

    def test_connector_waiver_needs_the_capacitor_within_its_limit(self):
        board = capture([part('U1', 1, 1), part('C1', 4, 1), part('C2', 5, 1), part('J1', 19, 1)])
        for fp in board['footprints']:
            fp['pads'] = [{'net': 'VCC'}, {'net': 'GND'}]
        policy = {'schema': 'agent-ee.placement-policy/1', 'ground_nets': ['GND'], 'ic_refs': ['U1'],
                  'connector_refs': ['J1'], 'max_shared_refs': 8,
                  'capacitors': {'C1': {'capacitance_nf': 100, 'max_distance_mm': 2.5, 'deduction': 15},
                                 'C2': {'capacitance_nf': 1000, 'max_distance_mm': 5, 'deduction': 15}}}
        report = judge(board, policy)
        self.assertEqual([(a['ref'], a['kind']) for a in report['associations']],
                         [('C1', 'decoupling'), ('C2', 'decoupling')])
        self.assertEqual([(a['ref'], a['distance_mm']) for a in report['advisories']], [('C1', 3)])
        self.assertEqual(report['score'], 85)
        board['footprints'][3]['position_nm'] = [int(5.5*MM), MM]
        report = judge(board, policy)
        self.assertEqual([(a['ref'], a['kind']) for a in report['associations']],
                         [('C1', 'interface_filter'), ('C2', 'interface_filter')])
        self.assertEqual((report['advisories'], report['score']), ([], 100))

    def test_no_policy_gives_no_score(self):
        report = judge(capture([part('U1', 2, 2)]))
        self.assertEqual((report['verdict'], report['score'], report['advisories']), ('pass', None, []))

    def test_shared_net_cap_and_ground_exclusion_are_explicit(self):
        board = capture([part('U1', 1, 1), part('C1', 8, 1)])
        for fp in board['footprints']:
            fp['pads'] = [{'net': 'GND'}]
        policy = {'schema': 'agent-ee.placement-policy/1', 'ground_nets': ['GND'], 'ic_refs': ['U1'],
                  'connector_refs': [], 'max_shared_refs': 8,
                  'capacitors': {'C1': {'capacitance_nf': 100, 'max_distance_mm': 2.5, 'deduction': 15}}}
        self.assertEqual(judge(board, policy)['associations'][0]['kind'], 'unassociated')
        for fp in board['footprints']:
            fp['pads'] = [{'net': 'VCC'}]
        policy['max_shared_refs'] = 1
        self.assertEqual(judge(board, policy)['associations'][0]['kind'], 'unassociated')
        policy['max_shared_refs'] = 9
        with self.assertRaises(ValueError):
            judge(board, policy)

    def test_duplicate_refs_and_missing_hash_are_not_a_pass(self):
        board = capture([part('U1', 1, 1), part('U1', 8, 1)])
        with self.assertRaises(ValueError):
            judge(board)
        board = capture([part('U1', 1, 1)])
        board['board_sha256'] = ''
        with self.assertRaises(ValueError):
            judge(board)

    def test_off_board_parts_listed_and_reasons_name_the_unknown(self):
        outline = polygon([(0, 0), (10, 0), (10, 10), (6, 10), (6, 4), (4, 4), (4, 10), (0, 10)])
        report = judge(capture([part('U1', 2, 6, shape=rectangle(2, 6, 6, 2)), part('C1', 1, 1)], outline))
        self.assertEqual(report['off_board'], ['U1'])
        self.assertEqual(judge(capture([part('U1', 2, 2)]))['off_board'], [])
        for status in ('outline_missing', 'outline_unproven'):
            board = capture([part('U1', 2, 2)])
            board['outline'] = {'status': status}
            self.assertEqual(judge(board)['unknown'], [{'kind': 'outline', 'reason': status}])
        board = capture([part('U1', 2, 2), part('C1', 8, 8)])
        board['footprints'][1]['courtyards'] = {}
        report = judge(board)
        self.assertEqual(report['unknown'], [{'kind': 'courtyard', 'ref': 'C1', 'reason': 'missing'}])
        self.assertEqual(report['verdict'], 'unknown')

    def test_connector_edge_advisory_deducts_without_deciding_verdict(self):
        board = capture([part('J1', 1, 9), part('J2', 9, 9)])
        policy = {'schema': 'agent-ee.placement-policy/1', 'ground_nets': [], 'ic_refs': [],
                  'connector_refs': ['J1', 'J2'], 'max_shared_refs': 8, 'capacitors': {},
                  'connector_edge': {'max_distance_mm': 2, 'deduction': 10}}
        report = judge(board, policy)
        self.assertEqual([(a['kind'], a['ref'], a['distance_mm']) for a in report['advisories']],
                         [('connector_edge_distance', 'J2', 9)])
        self.assertEqual((report['score'], report['verdict']), (90, 'pass'))
        board['outline'] = {'status': 'outline_unproven'}
        report = judge(board, policy)
        self.assertEqual((report['score'], report['advisories'], report['verdict']), (100, [], 'unknown'))
        policy['connector_edge'] = {'max_distance_mm': -1, 'deduction': 10}
        with self.assertRaises(ValueError):
            judge(board, policy)

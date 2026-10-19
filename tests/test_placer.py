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
    def keepout_board(self, *, owner='U1', layers=('F.Cu', 'B.Cu'), shape=None):
        from test_placement import rectangle
        board = capture([part('U1', 2, 2), part('C1', 2, 2)])
        board['rule_areas'] = [{'uuid': 'owned-zone', 'owner': next(
            fp['uuid'] for fp in board['footprints'] if fp['ref'] == owner),
            'layers': list(layers), 'forbid': {'footprints': True},
            'outline': {'status': 'proven', 'polygons': [shape or rectangle(0, 0, 7, 5)]}}]
        return board

    def test_owned_keepout_obstacle(self):
        import copy
        from agent_ee.placer import transform
        from agent_ee.placement import intersection_area, qualified_polygons
        for fixed, priority in ((['U1'], []), ([], ['U1']), ([], ['C1'])):
            board = self.keepout_board()
            original = copy.deepcopy(board)
            plan = pack(board, request(fixed_refs=fixed, priority_refs=priority, step_budget=100000))
            self.assertEqual(plan['status'], 'planned', plan)
            self.assertEqual(board, original)
            placed = {}
            for fp in board['footprints']:
                at = next(p for p in plan['placements'] if p['ref'] == fp['ref'])
                placed[fp['ref']] = transform(fp, at['position_nm'], at['rotation_deg'])
            # Independent vertex transform for the actual exclusion, including
            # when the owner was placed after C1 and must not engulf it.
            owner = board['footprints'][0]
            at = next(p for p in plan['placements'] if p['ref'] == 'U1')
            points = []
            for x, y in board['rule_areas'][0]['outline']['polygons'][0]:
                x, y = x-owner['position_nm'][0], y-owner['position_nm'][1]
                for _ in range(at['rotation_deg']//90):
                    x, y = y, -x
                points.append([x+at['position_nm'][0], y+at['position_nm'][1]])
            polygon, = qualified_polygons({'status': 'proven', 'polygons': [points]})
            courtyard, = qualified_polygons(placed['C1']['courtyards']['F'])
            self.assertEqual(intersection_area(polygon, courtyard), 0)

    def test_owned_keepout_conflicting_fixed_parts_fail_and_opposite_side_is_free(self):
        board = self.keepout_board()
        # Remove courtyard overlap so only the owned keepout causes refusal.
        from test_placement import part
        board['footprints'][1] = part('C1', 5, 2)
        self.assertEqual(pack(board, request(fixed_refs=['U1', 'C1']))['status'], 'fixed_conflict')
        board['rule_areas'][0]['layers'] = ['B.Cu']
        self.assertEqual(pack(board, request(fixed_refs=['U1', 'C1']))['status'], 'planned')
        board['rule_areas'][0]['layers'] = ['F.Cu']
        board['rule_areas'][0]['forbid']['footprints'] = False
        self.assertEqual(pack(board, request(fixed_refs=['U1', 'C1']))['status'], 'planned')

    def test_owned_keepout_uses_polygon_instead_of_bounding_box(self):
        from test_placement import MM, part
        # C1 fits in the notch of this L, inside its bounding box.
        shape = [[x*MM, y*MM] for x, y in ((0, 0), (7, 0), (7, 1), (1, 1), (1, 7), (0, 7))]
        board = self.keepout_board(shape=shape)
        board['footprints'][1] = part('C1', 5, 5)
        self.assertEqual(pack(board, request(fixed_refs=['U1', 'C1']))['status'], 'planned')

    def test_owned_keepout_unknown_geometry_and_owner_never_plan(self):
        import copy
        for mutation in ('owner', 'outline', 'flag', 'layers'):
            board = self.keepout_board()
            area = board['rule_areas'][0]
            if mutation == 'owner':
                area['owner'] = 'missing-footprint'
            elif mutation == 'outline':
                area['outline']['status'] = 'unsupported'
            elif mutation == 'flag':
                area['forbid']['footprints'] = 'yes'
            else:
                area['layers'] = None
            original = copy.deepcopy(board)
            plan = pack(board, request())
            self.assertEqual((plan['status'], plan['outcome'], plan['placements']),
                             ('unsupported_geometry', 'unknown', []))
            self.assertEqual(board, original)

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
        self.assertEqual((report['proof']['kind'], report['step_budget']), ('courtyard_area', 10000))
        board = capture([part('U1', 2, 2), part('C1', 2, 2)])
        report = pack(board, request(step_budget=1))
        self.assertEqual((report['status'], report['outcome']), ('packing_budget_exhausted', 'unknown'))
        self.assertEqual(report['steps_used'], 1)
        self.assertEqual(report['placements'], [])

    def test_part_wider_or_longer_than_board_is_defect_in_any_rotation(self):
        from test_placement import MM, rectangle
        def single(w, h, outline):
            return pack(capture([part('C1', 50, 50, shape=rectangle(50, 50, w, h))], outline),
                        request(fixed_refs=[], step_budget=777))
        # 4mm square in a 3mm wide strip: total area fits, the minimum width does not.
        report = single(4, 4, rectangle(0, 0, 3, 40))
        self.assertEqual((report['status'], report['outcome']), ('packing_infeasible', 'defect'))
        self.assertEqual((report['steps_used'], report['step_budget'], report['placements']), (0, 777, []))
        self.assertEqual(report['policy']['step_budget'], 777)
        self.assertEqual(report['proof'], {'kind': 'min_width', 'ref': 'C1',
                                           'board_min_width_squared_nm2': [9*MM*MM, 1],
                                           'footprint_min_width_squared_nm2': [16*MM*MM, 1]})
        # 30 x 1mm on 20 x 20mm: longer than the board diagonal at any angle.
        report = single(30, 1, rectangle(0, 0, 20, 20))
        self.assertEqual((report['status'], report['outcome']), ('packing_infeasible', 'defect'))
        self.assertEqual(report['proof']['kind'], 'diameter')
        self.assertEqual(report['proof']['board_diameter_squared_nm2'], [800*MM*MM, 1])
        # 25 x 1mm could fit diagonally: the quarter-turn search fails, which is not a proof.
        report = single(25, 1, rectangle(0, 0, 20, 20))
        self.assertEqual((report['status'], report['outcome']), ('packing_search_exhausted', 'unknown'))
        self.assertNotIn('proof', report)
        # Exactly as wide as the strip, or turned to fit: placed, not infeasible.
        self.assertEqual(single(3, 3, rectangle(0, 0, 3, 40))['status'], 'planned')
        plan = single(10, 2, rectangle(0, 0, 3, 40))
        self.assertEqual((plan['status'], plan['placements'][0]['rotation_deg']), ('planned', 90))

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

    def padded(self, shape=1, rotation=0):
        board = capture([part('U1', 10, 10), part('C1', 2, 2)])
        board['pcbnew_version'] = '10.0.7'
        for fp in board['footprints']:
            fp['pads'] = [{'number': '1', 'net': 'VCC', 'position_nm': list(fp['position_nm']),
                           'shape': 1, 'rotation_deg': 0}]
        board['footprints'][1]['pads'][0].update(shape=shape, rotation_deg=rotation)
        return board

    def test_net_cost_places_pad_beside_its_peer(self):
        plan = pack(self.padded(), request())
        self.assertEqual(plan['status'], 'planned')
        cap = next(p for p in plan['placements'] if p['ref'] == 'C1')
        # The pad sits at the footprint origin: rotation 270 brings it to the
        # right edge of the cap courtyard, exactly the requested .5mm gap.
        self.assertEqual(cap['position_nm'], [9500000, 10000000])
        self.assertEqual(cap['rotation_deg'], 270)

    def test_stock_pad_shapes_at_quarter_turns_plan_identically_and_are_recorded(self):
        # Pad outlines are outside the packing geometry: only the shape number differs.
        expected = pack(self.padded(), request())['placements']
        for shape in (0, 1, 2, 4, 5):
            for rotation in (0, 90, 180, 270, -90, 90.0):
                plan = pack(self.padded(shape, rotation), request())
                self.assertEqual(plan['status'], 'planned', (shape, rotation, plan.get('reason')))
                self.assertEqual(plan['placements'], expected)
        self.assertEqual(plan['pad_shapes'], {
            'pcbnew_major': '10', 'accepted': {'0': 'circle', '1': 'rect', '2': 'oval', '4': 'roundrect',
                                               '5': 'chamfered_rect'},
            'pad_rotation': 'quarter-turn', 'pad_outline_in_geometry': False})

    def test_unqualified_pad_shape_angle_or_enum_version_is_typed_unknown(self):
        import math
        cases = [((3, 0), "native pad shape 3 not qualified"), ((6, 0), "native pad shape 6 not qualified"),
                 ((7, 0), 'not qualified'), ((-1, 0), 'not qualified'), ((True, 0), 'not qualified'),
                 (('4', 0), 'not qualified'), ((None, 0), 'not qualified'), ((4.0, 0), 'not qualified'),
                 ((4, 45), 'quarter turn'), ((0, 89.9), 'quarter turn'), ((1, math.nan), 'quarter turn'),
                 ((1, math.inf), 'quarter turn'), ((1, '90'), 'quarter turn'), ((1, None), 'quarter turn')]
        for (shape, rotation), reason in cases:
            report = pack(self.padded(shape, rotation), request())
            self.assertEqual((report['status'], report['outcome']), ('unsupported_geometry', 'unknown'), shape)
            self.assertIn(reason, report['reason'])
            self.assertEqual(report['placements'], [])
            self.assertNotIn('pad_shapes', report)
        for version in ('9.0.4', '11.0.0', 'test example', None):
            board = self.padded(4)
            board['pcbnew_version'] = version
            report = pack(board, request())
            self.assertEqual((report['status'], report['outcome']), ('unsupported_geometry', 'unknown'))
            self.assertIn('pad shape enum not qualified', report['reason'])
        board = self.padded()
        del board['footprints'][1]['pads'][0]['shape']
        self.assertEqual(pack(board, request())['status'], 'unsupported_geometry')
        board['footprints'][1]['pads'][0] = 'pad'
        self.assertEqual(pack(board, request())['status'], 'unsupported_geometry')

    def test_equal_inputs_with_reversed_polygon_winding_plan_equally(self):
        import copy
        board = capture([part('U1', 2, 2), part('C1', 2, 2)])
        reverse = copy.deepcopy(board)
        for fp in reverse['footprints']:
            fp['courtyards']['F']['polygons'][0].reverse()
        self.assertEqual(pack(board, request())['placements'], pack(reverse, request())['placements'])

    def test_edge_margin(self):
        from agent_ee.placement_predicates import edge_clearance
        from test_placement import MM, rectangle
        board = capture([part('U1', 5, 5), part('C1', 5, 5), part('R1', 5, 5)], rectangle(0, 0, 20, 20))
        margin_mm = 1.5
        margin_nm = int(margin_mm * MM)
        unmargined = pack(board, request(fixed_refs=[]))
        self.assertEqual((unmargined['status'], unmargined['min_courtyard_edge_mm']), ('planned', 0.0))
        # Without an edge margin, parts are placed flush against the outline (0 mm from Edge.Cuts).
        unmargined_by_ref = {p['ref']: p for p in unmargined['placements']}
        unmargined_placed = capture([
            part(fp['ref'], unmargined_by_ref[fp['ref']]['position_nm'][0] / MM,
                 unmargined_by_ref[fp['ref']]['position_nm'][1] / MM)
            for fp in board['footprints']
        ], rectangle(0, 0, 20, 20))
        self.assertEqual(edge_clearance(unmargined_placed, margin_nm)['status'], 'defect')

        plan = pack(board, request(fixed_refs=[], min_courtyard_edge_mm=margin_mm))
        self.assertEqual(plan['status'], 'planned')
        self.assertEqual(plan['min_courtyard_edge_mm'], margin_mm)
        by_ref = {p['ref']: p for p in plan['placements']}
        placed = capture([
            part(fp['ref'], by_ref[fp['ref']]['position_nm'][0] / MM, by_ref[fp['ref']]['position_nm'][1] / MM)
            for fp in board['footprints']
        ], rectangle(0, 0, 20, 20))
        clearance = edge_clearance(placed, margin_nm)
        self.assertEqual(clearance['status'], 'pass', clearance)
        self.assertEqual(len(clearance['measured']), 3)
        for item in clearance['measured']:
            self.assertGreaterEqual(item['distance_mm'], margin_mm)

        # Boundary checks: invalid margin values raise ValueError; margin wider than board exhausts search.
        for bad in (-0.1, True, '1.5', 1e-7):
            with self.assertRaises(ValueError):
                pack(board, request(fixed_refs=[], min_courtyard_edge_mm=bad))
        tight = pack(capture([part('C1', 5, 5)], rectangle(0, 0, 4, 20)),
                     request(fixed_refs=[], min_courtyard_edge_mm=1.5))
        self.assertEqual((tight['status'], tight['min_courtyard_edge_mm']), ('packing_search_exhausted', 1.5))

    def test_edge_mounted_exempt(self):
        from agent_ee.placement_predicates import edge_clearance
        from test_placement import MM, rectangle
        # AMPSEAL connector mounted flush on the x=0 board edge, inside the 1.5 mm edge margin.
        ampseal = part('J1', 2, 6, shape=rectangle(0, 2, 4, 8))
        board = capture([ampseal, part('U1', 2, 6), part('C1', 2, 6)], rectangle(0, 0, 20, 20))
        plan = pack(board, request(fixed_refs=['J1'], min_courtyard_edge_mm=1.5))
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        self.assertEqual(plan['min_courtyard_edge_mm'], 1.5)
        by_ref = {p['ref']: p for p in plan['placements']}
        self.assertTrue(by_ref['J1']['fixed'])
        self.assertEqual(by_ref['J1']['position_nm'], [2 * MM, 6 * MM])
        self.assertEqual(by_ref['J1']['rotation_deg'], 0)
        # Movable footprints U1 and C1 are placed at least 1.5 mm from every outline edge.
        movable = capture([
            part(ref, by_ref[ref]['position_nm'][0] / MM, by_ref[ref]['position_nm'][1] / MM)
            for ref in ('U1', 'C1')
        ], rectangle(0, 0, 20, 20))
        self.assertEqual(edge_clearance(movable, int(1.5 * MM))['status'], 'pass')

        # Locked edge-mounted footprints are likewise exempt by reference.
        locked_board = capture([ampseal, part('U1', 2, 6)], rectangle(0, 0, 20, 20))
        locked_board['footprints'][0]['locked'] = True
        locked_plan = pack(locked_board, request(fixed_refs=[], min_courtyard_edge_mm=1.5))
        self.assertEqual(locked_plan['status'], 'planned')
        j1_locked = next(p for p in locked_plan['placements'] if p['ref'] == 'J1')
        self.assertTrue(j1_locked['fixed'])
        self.assertEqual(j1_locked['position_nm'], [2 * MM, 6 * MM])


def test_edge_margin():
    PlacerTest('test_edge_margin').test_edge_margin()


def test_edge_mounted_exempt():
    PlacerTest('test_edge_mounted_exempt').test_edge_mounted_exempt()


class EnvelopeFixedTest(unittest.TestCase):
    """AGENTEE-179: the request fixes envelope parts and holes; their courtyards are exact obstacles."""
    GAP = 500000

    def pod(self):
        import json
        from fractions import Fraction
        from pathlib import Path
        from agent_ee.board_envelope import validate
        from test_placement import MM, rectangle
        envelope = json.loads((Path(__file__).resolve().parents[1] / 'fixtures/telemetry-pod/envelope.json').read_text())
        envelope['obligations'] = []  # Only declared mechanics are this fixture's scope.
        envelope['mounting_holes'] = [dict(id=f'H{i}', at=[x, y], drill_mm=3.2, plated=False, tolerance_mm=0)
                                      for i, (x, y) in enumerate(((4, 4), (76, 4), (76, 46), (4, 46)), 1)]
        # J1 stands for the AMPSEAL header, J2 for the SMA and U1 for the IMU.
        envelope['fixed_footprints'] = [dict(reference=ref, side=side, edge=edge, position_mm=along, inset_mm=inset,
                                             rotation_deg=0, insertion=insertion, tolerance_mm=0)
                                        for ref, side, edge, along, inset, insertion in (
                                            ('J1', 'front', 'left', 30, 2, 'through_edge'),
                                            ('J2', 'back', 'right', 15, 1, 'through_edge'),
                                            ('U1', 'front', 'top', 50, 30, 'from_top'))]
        record = validate(json.dumps(envelope).encode())
        shapes = {'J1': (-1, -8, 9, 16), 'J2': (-6, -3, 6.5, 6), 'U1': (-1.5, -1.5, 3, 3)}
        fixed = []
        for item in record['fixed_footprints']:
            x, y = map(Fraction, item['anchor_mm'])  # Exact decimal text from the validator.
            dx, dy, w, h = shapes[item['reference']]
            fp = part(item['reference'], x, y, side=item['side'][0].upper(), shape=rectangle(x+dx, y+dy, w, h))
            fixed.append(fp)
        for hole in record['mounting_holes']:
            x, y = map(Fraction, hole['at_mm'])
            square = rectangle(x-Fraction('1.6'), y-Fraction('1.6'), Fraction('3.2'), Fraction('3.2'))
            fp = part(hole['id'], x, y, shape=square)
            fp['courtyards']['B'] = {'status': 'proven', 'polygons': [square]}
            fixed.append(fp)
        nets = {'C1': 'CAN_H', 'C2': 'CAN_L', 'R1': 'IMU_INT', 'R2': 'IMU_INT', 'B1': 'RF'}
        movable = [part(ref, 60, 40, side='B' if ref == 'B1' else 'F') for ref in nets]
        board = capture(fixed + movable, rectangle(0, 0, 80, 50))
        board['pcbnew_version'] = '10.0.7'
        anchors = {'CAN_H': ('J1', 0), 'CAN_L': ('J1', 2), 'IMU_INT': ('U1', 0), 'RF': ('J2', 0)}
        by_ref = {fp['ref']: fp for fp in board['footprints']}
        for ref, net in nets.items():
            owner, offset = anchors[net]
            x, y = by_ref[owner]['position_nm']
            by_ref[owner]['pads'].append({'number': str(len(by_ref[owner]['pads'])+1), 'net': net,
                                          'position_nm': [x, y + offset*MM], 'shape': 1, 'rotation_deg': 0})
            by_ref[ref]['pads'] = [{'number': '1', 'net': net, 'position_nm': list(by_ref[ref]['position_nm']),
                                    'shape': 1, 'rotation_deg': 0}]
        return record, board

    def test_fixed_from_envelope(self):
        import copy
        from agent_ee import placement_adapter as pa
        from agent_ee.placer import transform
        record, board = self.pod()
        original = copy.deepcopy(board)
        refs = pa.envelope_references(record)
        self.assertEqual(refs, ['H1', 'H2', 'H3', 'H4', 'J1', 'J2', 'U1'])
        policy = pa.helper_request(b'', request(fixed_refs=[], step_budget=200000), refs)['policy']
        plan = pack(board, policy)
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        self.assertEqual(plan['judgment']['verdict'], 'pass')
        self.assertEqual(board, original)
        at = {p['ref']: p for p in plan['placements']}
        fixed = {fp['ref']: fp for fp in board['footprints'] if fp['ref'] in refs}
        for ref, fp in fixed.items():
            self.assertEqual((at[ref]['position_nm'], at[ref]['rotation_deg'], at[ref]['fixed']),
                             (fp['position_nm'], fp['rotation_deg'], True), ref)
        # Independent axis-aligned separation: every courtyard here is a rectangle.
        def box(points):
            return [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points),
                    max(p[1] for p in points)]
        for fp in board['footprints']:
            if fp['ref'] in refs:
                continue
            placed = transform(fp, at[fp['ref']]['position_nm'], at[fp['ref']]['rotation_deg'])
            for side, courtyard in placed['courtyards'].items():
                a = box(courtyard['polygons'][0])
                for other in fixed.values():
                    if side not in other['courtyards']:
                        continue
                    b = box(other['courtyards'][side]['polygons'][0])
                    dx, dy = max(b[0]-a[2], a[0]-b[2], 0), max(b[1]-a[3], a[1]-b[3], 0)
                    self.assertGreaterEqual(dx*dx + dy*dy, self.GAP*self.GAP, (fp['ref'], other['ref'], side))
        # Without the derived request the same unlocked parts are free to move.
        free = pack(board, request(fixed_refs=[], step_budget=200000))
        self.assertEqual(free['status'], 'planned', free.get('reason'))
        self.assertTrue(any(p['position_nm'] != fixed[p['ref']]['position_nm']
                            for p in free['placements'] if p['ref'] in fixed))

    def test_fixed_courtyard_is_a_polygon_not_its_bounding_box(self):
        from test_placement import polygon, rectangle
        # The fixed L's bounding box is the whole board; only its notch is free.
        shape = polygon([(0, 0), (8, 0), (8, 5), (5, 5), (5, 8), (0, 8)])
        board = capture([part('U1', 0, 0, shape=shape), part('C1', 1, 1)], rectangle(0, 0, 8, 8))
        plan = pack(board, request())
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        cap = next(p for p in plan['placements'] if p['ref'] == 'C1')
        x, y = cap['position_nm']
        self.assertTrue(5_500_000 <= x <= 6_000_000 and 5_500_000 <= y <= 6_000_000, cap)
        # The gap still holds against the exact polygon: a 3 mm notch cannot take 2 mm plus 1.5 mm.
        self.assertEqual(pack(board, request(gap_nm=1_500_000))['status'], 'packing_search_exhausted')

    def test_fixed_courtyard_conflict_names_the_reference(self):
        from test_placement import rectangle
        record, board = self.pod()
        from agent_ee import placement_adapter as pa
        refs = pa.envelope_references(record)
        by_ref = {fp['ref']: fp for fp in board['footprints']}
        # The IMU stand-in now reaches within the gap of hole H1's courtyard.
        by_ref['U1']['courtyards']['F']['polygons'] = [rectangle(5.8, 5.8, 3, 3)]
        plan = pack(board, request(fixed_refs=refs))
        self.assertEqual((plan['status'], plan['outcome'], plan['placements']), ('fixed_conflict', 'unknown', []))
        # Fixed parts are checked in capture order; the later part of the pair is named.
        self.assertIn('fixed placement H1 conflicts', plan['reason'])


class BoardOnlyNetlistTest(unittest.TestCase):
    """AGENTEE-179: scaffold holes are board-only footprints absent from the native export."""
    XML = ('<export><components><comp ref="U1"/><comp ref="C1"/></components><nets>'
           '<net name="SIG"><node ref="U1" pin="1"/><node ref="C1" pin="1"/></net></nets></export>')

    def facts(self, hole_net=''):
        board = capture([part('U1', 2, 2), part('C1', 6, 2), part('H1', 10, 10)])
        for fp in board['footprints']:
            fp['pads'] = [{'number': '' if fp['ref'] == 'H1' else '1',
                           'net': hole_net if fp['ref'] == 'H1' else 'SIG'}]
        return board

    def test_board_only_holes_are_exempt_only_without_nets_or_symbols(self):
        from agent_ee.placer_runner import _netlist_binding
        _netlist_binding(self.XML, self.facts(), frozenset({'H1'}))
        with self.assertRaisesRegex(ValueError, 'component inventory differs'):
            _netlist_binding(self.XML, self.facts())
        with self.assertRaisesRegex(ValueError, 'board-only footprint has a net'):
            _netlist_binding(self.XML, self.facts('SIG'), frozenset({'H1'}))
        with self.assertRaisesRegex(ValueError, 'board-only footprint has a net or a native netlist component'):
            _netlist_binding(self.XML, self.facts(), frozenset({'H1', 'C1'}))


def test_fixed_from_envelope():
    EnvelopeFixedTest('test_fixed_from_envelope').test_fixed_from_envelope()

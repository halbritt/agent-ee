"""AGENTEE-212 follow-up: bounded deterministic placer search and region-capacity proofs.

The real pod placement stopped at its first unplaceable part: the greedy
pass never revisits an earlier choice. These fixtures are authored here from
numbers: a part greedily taken spot that leaves no room for a later group
member, an anchor placed where its member cannot reach, and regions whose
capacity is provably too small, with and without the packing gap.
"""
import copy
import hashlib
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_ee import placement_groups
from agent_ee.placement import judge
from agent_ee.placer import pack, transform
from test_placement import MM, capture, part, rectangle
from test_placer import request
from test_placer_residuals import box, fresh_pad_layers, netlist
from toolchain import require_toolchain


def netted(fp, net):
    fp['pads'] = [{'number': '1', 'net': net, 'position_nm': list(fp['position_nm']), 'shape': 1, 'rotation_deg': 0}]
    return fp


def board(parts, outline):
    result = capture(parts, outline)
    result['pcbnew_version'] = '10.0.7'
    return result


def promotion_case():
    """X is cheapest beside F's pad, inside the region P alone can fill."""
    parts = [netted(part('F', 0, 0, shape=box(0, 0, 3, 8)), 'N'), netted(part('X', 20, 3), 'N'),
             part('P', 14, 0, shape=box(14, 0, 23.5, 7))]
    parts[0]['pads'][0]['position_nm'] = [3 * MM, 4 * MM]
    groups = [{'id': 'p-region', 'references': ['P'], 'region': {'polygons': [box(3.5, 0, 13.5, 8)]}}]
    return board(parts, rectangle(0, 0, 24, 8)), request(fixed_refs=['F'], priority_refs=['X'], groups=groups)


def anchor_case():
    """A is cheapest in the slot between F and an obstacle, where L cannot be within reach."""
    parts = [netted(part('F', 0, 0, shape=box(0, 0, 3, 10)), 'N'), netted(part('A', 20, 0), 'N'),
             part('L', 20, 3, shape=box(20, 3, 26, 9))]
    parts[0]['pads'][0]['position_nm'] = [3 * MM, 5 * MM]
    groups = [{'id': 'a-member', 'references': ['L'], 'anchor': {'reference': 'A'}, 'max_distance_nm': 8 * MM}]
    obstacles = [{'side': 'F', 'polygons': [box(6, 0, 12, 10)]}]
    return board(parts, rectangle(0, 0, 30, 10)), request(fixed_refs=['F'], groups=groups, obstacles=obstacles)


def placed_capture(source, plan):
    by_ref = {fp['ref']: fp for fp in source['footprints']}
    return {**source, 'footprints': [transform(by_ref[p['ref']], p['position_nm'], p['rotation_deg'])
                                     for p in plan['placements']]}


class SearchTest(unittest.TestCase):
    def assert_independently_valid(self, source, policy, plan):
        """Every accepted placement passes the exact judge and group grading, fixed parts unmoved."""
        placed = placed_capture(source, plan)
        self.assertEqual(judge(placed)['verdict'], 'pass')
        if 'groups' in policy:
            self.assertEqual(placement_groups.grade(placed, placement_groups.validate(policy['groups']))['status'],
                             'pass')
        by_ref = {fp['ref']: fp for fp in source['footprints']}
        for entry in plan['placements']:
            if entry['fixed']:
                self.assertEqual((entry['position_nm'], entry['rotation_deg']),
                                 (by_ref[entry['ref']]['position_nm'], by_ref[entry['ref']]['rotation_deg']))

    def test_part_blocked_by_an_earlier_greedy_choice_is_placed_before_it(self):
        source, policy = promotion_case()
        original = copy.deepcopy(source)
        # Alone, X takes the spot beside F's pad, inside P's region: the greedy choice.
        alone = pack(board([fp for fp in source['footprints'] if fp['ref'] != 'P'], rectangle(0, 0, 24, 8)),
                     request(fixed_refs=['F']))
        x = {p['ref']: p for p in alone['placements']}['X']['position_nm']
        self.assertLess(x[0], 13.5 * MM)
        plan = pack(source, policy)
        self.assertEqual((plan['status'], plan['outcome']), ('planned', 'info'), plan.get('reason'))
        self.assertEqual(plan['search'], {'strategy': 'ranked-first-fit-backjump/1', 'backjumps': 1})
        at = {p['ref']: p['position_nm'] for p in plan['placements']}
        self.assertGreaterEqual(at['X'][0], 13.5 * MM)
        self.assertEqual(plan['group_grading']['status'], 'pass')
        self.assert_independently_valid(source, policy, plan)
        # Deterministic: the same input gives the same plan, and the input is unchanged.
        self.assertEqual(plan, pack(source, policy))
        self.assertEqual(source, original)

    def test_member_out_of_reach_of_its_greedy_anchor_is_placed_with_it(self):
        source, policy = anchor_case()
        # Alone, A takes the slot beside F's pad with its origin west of 4.5 mm; L, which
        # only fits east of the obstacle (x >= 12.5 mm), is then more than 8 mm away.
        alone = pack(board([fp for fp in source['footprints'] if fp['ref'] != 'L'], rectangle(0, 0, 30, 10)),
                     request(fixed_refs=['F'], obstacles=policy['obstacles']))
        self.assertLess({p['ref']: p for p in alone['placements']}['A']['position_nm'][0], 4.5 * MM)
        plan = pack(source, policy)
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        self.assertEqual(plan['search']['backjumps'], 1)
        at = {p['ref']: p['position_nm'] for p in plan['placements']}
        self.assertGreaterEqual(at['L'][0], 12.5 * MM)
        self.assertLessEqual(math.dist(at['A'], at['L']), 8 * MM)
        self.assertEqual(plan['group_grading']['status'], 'pass')
        self.assert_independently_valid(source, policy, plan)
        self.assertEqual(plan, pack(source, policy))

    def test_generation_drops_only_candidates_the_predicates_reject(self):
        from agent_ee import placer
        # Fixed rectangle J, fixed L-shaped G, two placed movable parts and an L-shaped candidate Q.
        parts = [part('J', 0, 0, shape=box(0, 0, 3, 8)), part('G', 10, 0, shape=[[10*MM, 0], [14*MM, 0], [14*MM, 2*MM],
                                                                               [12*MM, 2*MM], [12*MM, 5*MM], [10*MM, 5*MM]]),
                 part('M1', 5, 5), part('M2', 16, 4), part('Q', 20, 6, shape=[[20*MM, 6*MM], [23*MM, 6*MM], [23*MM, 7*MM],
                                                                [21*MM, 7*MM], [21*MM, 7_500_000], [20*MM, 7_500_000]])]
        source = board(parts, rectangle(0, 0, 24, 8))
        policy = request(fixed_refs=['J', 'G'])
        capture = copy.deepcopy(source)
        outline = placer._qualified(capture)
        fixed, placed, exact = placer._initial_reservations(capture, policy, outline, [], {}, {})
        search = placer._Search(capture, placed, exact, [], outline, 0, policy, {})
        by_ref = {fp['ref']: fp for fp in capture['footprints']}
        search.push(by_ref['M1'])
        search.push(by_ref['M2'])
        self.assertEqual(sorted(slab for _, slab in search.solid),  # J, and G's two slabs.
                         [(0, 0, 3*MM, 8*MM), (10*MM, 0, 12*MM, 5*MM), (12*MM, 0, 14*MM, 2*MM)])
        q, dropped = by_ref['Q'], 0
        anchors = [(side, b) for side, b, _ in search.reserved] + [(o['side'], o['box']) for o in exact]
        for rotation in (0, 90, 180, 270):
            at_origin = transform(q, [0, 0], rotation)
            xs, ys = placer._candidate_axes(q, rotation, outline, anchors, search.gap, 0, (),
                                            placer._window(outline, 0, None, []))
            slabs = {'F': placer._slabs(at_origin['courtyards']['F']['polygons'][0])}
            kept = set(placer._unreserved(placer.boxes(at_origin), slabs, xs, ys, search.reserved, search.solid,
                                          search.gap, set()))
            for x in xs:
                for y in ys:
                    proposed = transform(q, [x, y], rotation)
                    if (x, y) not in kept:
                        dropped += 1
                        self.assertIsNotNone(search.blockers(proposed, None, []), (x, y, rotation))
        self.assertGreater(dropped, 0)

    def test_search_stays_within_the_step_budget_and_unknown(self):
        source, policy = promotion_case()
        steps = pack(source, policy)['steps_used']
        for budget in (1, steps - 1):
            report = pack(source, {**policy, 'step_budget': budget})
            self.assertEqual((report['status'], report['outcome'], report['placements']),
                             ('packing_budget_exhausted', 'unknown', []))
            self.assertEqual((report['steps_used'], report['step_budget']), (budget, budget))
            self.assertIn('unplaced', report['search'])
        self.assertEqual(pack(source, {**policy, 'step_budget': steps})['status'], 'planned')

    def test_unplaceable_part_with_no_movable_blocker_is_named_and_unknown(self):
        # The 6.9 mm strip holds P's area many times over, but no quarter turn of a
        # 9.5 x 7 mm courtyard fits its width: a bounded search failure, never a proof.
        parts = [netted(part('X', 20, 3), 'N'), part('P', 14, 0, shape=box(14, 0, 23.5, 7))]
        groups = [{'id': 'p-region', 'references': ['P'], 'region': {'polygons': [box(0, 0, 6.9, 30)]}}]
        report = pack(board(parts, rectangle(0, 0, 30, 30)), request(fixed_refs=[], groups=groups))
        self.assertEqual((report['status'], report['outcome']), ('packing_search_exhausted', 'unknown'))
        self.assertEqual(report['search']['unplaced'], 'P')
        self.assertIn('no candidate for P', report['reason'])
        self.assertNotIn('proof', report)


class RegionCapacityTest(unittest.TestCase):
    def squares(self, side, count, region=(0, 0, 10, 10), fixed=()):
        parts = [part(f'C{i}', 12, 12, shape=rectangle(12, 12, side, side)) for i in range(1, count + 1)]
        parts += [part(ref, x, y, shape=rectangle(x, y, s, s)) for ref, x, y, s in fixed]
        groups = [{'id': 'block', 'references': [f'C{i}' for i in range(1, count + 1)],
                   'region': {'polygons': [box(*region)]}}]
        return board(parts, rectangle(0, 0, 20, 20)), groups

    def test_exactly_full_region_packs_and_one_nanometre_more_is_proven_infeasible(self):
        source, groups = self.squares(5, 4)
        plan = pack(source, request(fixed_refs=[], gap_nm=0, groups=groups))
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        self.assert_in_region(source, plan, (0, 0, 10, 10))
        source, groups = self.squares(5.000001, 4)
        report = pack(source, request(fixed_refs=[], gap_nm=0, groups=groups, step_budget=321))
        self.assertEqual((report['status'], report['outcome'], report['steps_used'], report['placements']),
                         ('packing_infeasible', 'defect', 0, []))
        proof = report['proof']
        self.assertEqual((proof['kind'], proof['group'], proof['side'], proof['references']),
                         ('region_capacity', 'block', 'F', ['C1', 'C2', 'C3', 'C4']))
        self.assertEqual(proof['available_area_nm2'], [100 * MM * MM, 1])
        self.assertEqual(proof['required_courtyard_area_nm2'], [4 * 5000001**2, 1])
        self.assertIn('group block region', report['reason'])

    def assert_in_region(self, source, plan, region):
        for fp in placed_capture(source, plan)['footprints']:
            for poly in fp['courtyards']['F']['polygons']:
                for x, y in poly:
                    self.assertTrue(region[0]*MM <= x <= region[2]*MM and region[1]*MM <= y <= region[3]*MM)

    def test_fixed_courtyards_in_the_region_are_subtracted_and_named(self):
        # 4 x 4.6 mm squares need 84.64 mm2; the region has 100, less F's 16.
        source, groups = self.squares(4.6, 4, fixed=(('F', 0, 0, 4),))
        report = pack(source, request(fixed_refs=['F'], gap_nm=0, groups=groups))
        self.assertEqual((report['status'], report['proof']['kind']), ('packing_infeasible', 'region_capacity'))
        self.assertEqual((report['proof']['blocked_by'], report['proof']['blocked_area_nm2']),
                         (['F'], [16 * MM * MM, 1]))
        # Outside the region, the same part blocks nothing.
        source, groups = self.squares(4.6, 4, fixed=(('F', 15, 15, 4),))
        report = pack(source, request(fixed_refs=['F'], gap_nm=0, groups=groups))
        self.assertNotEqual(report['status'], 'packing_infeasible')

    def test_members_of_a_nested_region_count_in_the_enclosing_one(self):
        parts = [part('M1', 12, 0, shape=rectangle(12, 0, 8, 8)), part('M2', 12, 12, shape=rectangle(12, 12, 4.5, 4.5)),
                 part('M3', 17, 12, shape=rectangle(17, 12, 4.5, 4.5))]
        groups = [{'id': 'outer', 'references': ['M1'], 'region': {'polygons': [box(0, 0, 10, 10)]}},
                  {'id': 'inner', 'references': ['M2', 'M3'], 'region': {'polygons': [box(0, 0, 5, 10)]}}]
        report = pack(board(parts, rectangle(0, 0, 30, 30)), request(fixed_refs=[], gap_nm=0, groups=groups))
        self.assertEqual((report['status'], report['outcome']), ('packing_infeasible', 'defect'))
        self.assertEqual((report['proof']['group'], report['proof']['groups'], report['proof']['references']),
                         ('outer', ['inner', 'outer'], ['M1', 'M2', 'M3']))

    def test_gap_bound_proves_what_area_alone_cannot(self):
        # Four 5 mm squares tile the 10 mm region only with no gap; a 0.5 mm gap grows
        # each to at least 30.1875 mm2 against at most ~110.4 mm2 of grown region.
        source, groups = self.squares(5, 4)
        report = pack(source, request(fixed_refs=[], gap_nm=500_000, groups=groups))
        self.assertEqual((report['status'], report['outcome']), ('packing_infeasible', 'defect'))
        proof = report['proof']
        self.assertEqual((proof['kind'], proof['gap_nm']), ('region_capacity_gap', 500_000))
        required = proof['required_grown_courtyard_area_nm2']
        available = proof['available_area_nm2']
        self.assertEqual(required, [4 * (25 * MM * MM + 20 * MM * 250_000 + 3 * 250_000**2), 1])
        self.assertGreater(required[0] * available[1], available[0] * required[1])
        # Four 4.5 mm squares fit with the gap: no proof, and the search places them.
        source, groups = self.squares(4.5, 4)
        plan = pack(source, request(fixed_refs=[], gap_nm=500_000, groups=groups))
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))


class NativeSearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-ee212s-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()

    def native_board(self, path):
        """The promotion case as an owned pcbnew board: F locked, X and P movable."""
        import pcbnew as k
        board = k.BOARD()
        def vec(x, y):
            return k.VECTOR2I(round(x*MM), round(y*MM))
        for a, b in (((0, 0), (24, 0)), ((24, 0), (24, 8)), ((24, 8), (0, 8)), ((0, 8), (0, 0))):
            edge = k.PCB_SHAPE(board, k.SHAPE_T_SEGMENT)
            edge.SetLayer(k.Edge_Cuts)
            edge.SetStart(vec(*a))
            edge.SetEnd(vec(*b))
            edge.SetWidth(50000)
            board.Add(edge)
        net = k.NETINFO_ITEM(board, 'N')
        board.Add(net)
        def footprint(ref, courtyard, at, pad_at, name, locked=False):
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            fp.SetValue('owned')
            board.Add(fp)
            shape = k.PCB_SHAPE(fp, k.SHAPE_T_RECT)
            shape.SetLayer(k.F_CrtYd)
            shape.SetStart(vec(*courtyard[:2]))
            shape.SetEnd(vec(*courtyard[2:]))
            shape.SetWidth(50000)
            fp.Add(shape)
            pad = k.PAD(fp)
            pad.SetNumber('1')
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetShape(k.PAD_SHAPE_RECT)
            pad.SetSize(vec(0.6, 0.6))
            owned = k.LSET()  # A fresh set: shared static LSETs are never mutated.
            for layer in (k.F_Cu, k.F_Mask, k.F_Paste):
                owned.AddLayer(layer)
            pad.SetLayerSet(owned)
            pad.SetPosition(vec(*pad_at))
            fp.Add(pad)
            if name:
                pad.SetNet(net)
            fp.SetPosition(vec(*at))
            fp.SetLocked(locked)
        footprint('F', (-1.5, -4, 1.5, 4), (1.5, 4), (1, 0), 'N', locked=True)
        footprint('X', (-1, -1, 1, 1), (20, 4), (0, 0), 'N')
        footprint('P', (-4.75, -3.5, 4.75, 3.5), (18, 30), (0, 0), '')
        k.SaveBoard(str(path), board)
        return path

    def test_backjumped_plan_is_written_judged_and_keeps_pad_layers_after_fresh_reload(self):
        from agent_ee.placement_probe import extract
        from agent_ee.placer_runner import place_candidate
        source = self.native_board(self.root / 'search.kicad_pcb')
        xml = self.root / 'netlist.xml'
        xml.write_text(netlist(('F', 'P', 'X'), {'N': [('F', '1'), ('X', '1')]}))
        before = fresh_pad_layers(source, self.root / 'before.json')
        groups = [{'id': 'p-region', 'references': ['P'], 'region': {'polygons': [box(3.5, 0, 13.5, 8)]}}]
        config = {'policy': request(fixed_refs=['F'], priority_refs=['X'], groups=groups, step_budget=100000),
                  'authorization': {'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
                                    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                                    'writable_root': str(self.scratch)}}
        result = place_candidate(source, xml, self.scratch / 'proposal', config)
        self.assertEqual((result['status'], result['judgment']['verdict'], result['native_courtyard_overlaps']),
                         ('placed', 'pass', []), result)
        self.assertEqual(result['plan']['search']['backjumps'], 1)
        self.assertEqual(result['judgment']['group_grading']['status'], 'pass')
        placed = self.scratch / 'proposal/candidate.kicad_pcb'
        after = {fp['ref']: fp for fp in extract(placed)['footprints']}
        self.assertEqual(after['F'], {fp['ref']: fp for fp in extract(source)['footprints']}['F'])
        self.assertGreaterEqual(after['X']['position_nm'][0], 13.5 * MM)
        # Exact pad layer sets after a fresh-process reload of the written board.
        layers = fresh_pad_layers(placed, self.root / 'after.json')
        self.assertEqual(layers, before)
        self.assertEqual(layers, {f'{ref}.1': ['F.Cu', 'F.Mask', 'F.Paste'] for ref in ('F', 'P', 'X')})


if __name__ == '__main__':
    unittest.main()

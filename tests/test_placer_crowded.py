"""AGENTEE-212 continuation: candidate completeness and compaction in crowded clusters.

The real pod's fixed RF module has a concave courtyard whose box spans far past
its on-board edges, so no candidate was aligned to those edges and a part
anchored beside it had no candidate at all. Parts bound to a declared anchor
point have only a small disc of positions, which a cluster placed earlier can
take. These fixtures are authored here from numbers.
"""
import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_ee import placer
from agent_ee.placer import pack, transform
from test_placement import MM, part, rectangle
from test_placer import request
from test_placer_residuals import box
import test_placer_search
from test_placer_search import board


def notch_case():
    """P must lie within 0.6 mm of a point on the inner edge of a fixed L-shaped courtyard."""
    ell = [[0, 0], [20 * MM, 0], [20 * MM, 2 * MM], [8 * MM, 2 * MM], [8 * MM, 10 * MM], [0, 10 * MM]]
    parts = [part('G', 0, 0, shape=ell), part('P', 15, 5, shape=box(15, 5, 17, 6))]
    groups = [{'id': 'p-edge', 'references': ['P'], 'anchor': {'point_nm': [8 * MM, 6 * MM]},
               'max_distance_nm': 600_000}]
    return board(parts, rectangle(0, 0, 20, 10)), request(fixed_refs=['G'], groups=groups)


def corner_case():
    """P must lie at the board corner, which anchor A (priority, placed first before) takes greedily."""
    parts = [part('A', 5, 5, shape=box(5, 5, 8, 8)), part('M', 2, 7, shape=box(2, 7, 3, 8)),
             part('P', 7, 1, shape=box(7, 1, 9, 2))]
    groups = [{'id': 'a-support', 'references': ['M'], 'anchor': {'reference': 'A'}, 'max_distance_nm': 6 * MM},
              {'id': 'p-corner', 'references': ['P'], 'anchor': {'point_nm': [0, 0]}, 'max_distance_nm': 500_000}]
    return board(parts, rectangle(0, 0, 10, 10)), request(fixed_refs=[], priority_refs=['A'], gap_nm=250_000,
                                                          groups=groups)


def centred(ref, w, h, pads):
    """A part at the origin with a w x h mm courtyard centred on it and (net, x, y) pads in mm."""
    fp = part(ref, 0, 0, shape=box(-w / 2, -h / 2, w / 2, h / 2))
    fp['pads'] = [{'number': str(i + 1), 'net': net, 'position_nm': [round(x * MM), round(y * MM)], 'shape': 1,
                   'rotation_deg': 0} for i, (net, x, y) in enumerate(pads)]
    return fp


def cluster_case():
    """Six passives on six pins of a 3 x 3 mm IC must all lie within 4.5 mm of it on an 8 x 6 mm board."""
    pins = [('N0', -0.29, -1.25), ('N1', -1.25, -1.15), ('N2', -0.77, -1.25), ('N3', 1.25, -1.09),
            ('N4', -0.48, -1.25), ('N5', -1.25, -0.02)]
    sizes = [(2.96, 1.46), (2.2, 2.2), (2.96, 1.46), (2.0, 1.0), (2.96, 1.46), (2.2, 2.2)]
    parts = [centred('U1', 3, 3, pins)]
    parts += [centred(f'C{i + 1}', w, h, [(pins[i][0], -w / 2 + 0.4, 0), ('GND', w / 2 - 0.4, 0)])
              for i, (w, h) in enumerate(sizes)]
    for fp in parts:  # Captured at (2, 2) mm; the packer moves every part.
        for poly in fp['courtyards']['F']['polygons']:
            for p in poly:
                p[0] += 2 * MM
                p[1] += 2 * MM
        for pad in fp['pads']:
            pad['position_nm'] = [pad['position_nm'][0] + 2 * MM, pad['position_nm'][1] + 2 * MM]
        fp['position_nm'] = [2 * MM, 2 * MM]
    groups = [{'id': 'support', 'references': [f'C{i + 1}' for i in range(6)], 'anchor': {'reference': 'U1'},
               'max_distance_nm': 4_500_000}]
    return board(parts, rectangle(0, 0, 8, 6)), request(fixed_refs=[], priority_refs=['U1'], gap_nm=250_000,
                                                         step_budget=3000, groups=groups)


class CandidateCompletenessTest(unittest.TestCase):
    def test_part_anchored_at_a_concave_fixed_edge_is_aligned_to_that_edge(self):
        source, policy = notch_case()
        plan = pack(source, policy)
        self.assertEqual((plan['status'], plan['outcome']), ('planned', 'info'), plan.get('reason'))
        at = {p['ref']: p for p in plan['placements']}
        # The only admissible origins lie 0.5 mm (the gap) to 0.6 mm east of the edge at x = 8 mm.
        self.assertEqual(at['P']['position_nm'][0], 8_500_000)
        self.assertLessEqual(math.dist(at['P']['position_nm'], (8 * MM, 6 * MM)), 600_000)
        test_placer_search.SearchTest.assert_independently_valid(self, source, policy, plan)
        self.assertEqual(plan, pack(source, policy))

    def test_concave_edge_is_not_a_candidate_without_its_slabs(self):
        # The same search with only the fixed courtyard's box as alignment finds nothing:
        # every candidate x it generates is the edge itself, nearer than the gap.
        source, policy = notch_case()
        capture = copy.deepcopy(source)
        outline = placer._qualified(capture)
        _, placed, exact = placer._initial_reservations(capture, policy, outline, [], {}, {})
        p = {fp['ref']: fp for fp in capture['footprints']}['P']
        anchors = [(o['side'], o['box']) for o in exact]
        window = placer._window(outline, 0, None, [(None, (8 * MM, 6 * MM), 600_000)])
        xs, _ = placer._candidate_axes(p, 0, outline, anchors, policy['gap_nm'], 0, [(8 * MM, 6 * MM)], window)
        self.assertEqual(xs, [8 * MM])
        slabs = [(o['side'], slab) for o in exact for poly in o['polygons'] for slab in placer._slabs(poly)]
        xs, _ = placer._candidate_axes(p, 0, outline, anchors + slabs, policy['gap_nm'], 0, [(8 * MM, 6 * MM)],
                                       window)
        self.assertIn(8_500_000, xs)

    def test_positions_nearer_than_the_gap_to_fixed_slabs_are_rejected_by_the_predicates(self):
        # Every position the sweep marks as nearer than the gap must fail the exact predicates;
        # the search counts it as a step without running them.
        source, policy = notch_case()
        policy = dict(policy, groups=[])
        capture = copy.deepcopy(source)
        outline = placer._qualified(capture)
        _, placed, exact = placer._initial_reservations(capture, policy, outline, [], {}, {})
        search = placer._Search(capture, placed, exact, [], outline, 0, policy, {})
        p = {fp['ref']: fp for fp in capture['footprints']}['P']
        anchors = [(o['side'], o['box']) for o in exact] + search.solid
        doomed_total = kept_total = 0
        for rotation in (0, 90, 180, 270):
            at_origin = transform(p, [0, 0], rotation)
            xs, ys = placer._candidate_axes(p, rotation, outline, anchors, search.gap, 0, (),
                                            placer._window(outline, 0, None, []))
            # Off-grid positions straddle the gap boundary in both axes and at the corner.
            xs = sorted(set(xs) | {x + d for x in xs for d in (-1, 1, -250_000, 250_000)})
            ys = sorted(set(ys) | {y + d for y in ys for d in (-1, 1, -250_000, 250_000)})
            slabs = {'F': placer._slabs(at_origin['courtyards']['F']['polygons'][0])}
            doomed = set()
            for x, y in placer._unreserved(placer.boxes(at_origin), slabs, xs, ys, search.reserved, search.solid,
                                           search.gap, set(), doomed):
                verdict = search.blockers(transform(p, [x, y], rotation), None, [])
                if (x, y) in doomed:
                    doomed_total += 1
                    self.assertEqual(verdict, set(), (x, y, rotation))
                elif verdict is None:
                    kept_total += 1
        self.assertGreater(doomed_total, 0)
        self.assertGreater(kept_total, 0)


class CrowdedClusterTest(unittest.TestCase):
    def test_part_bound_to_an_anchor_point_is_placed_before_the_clusters(self):
        source, policy = corner_case()
        plan = pack(source, policy)
        self.assertEqual((plan['status'], plan['outcome']), ('planned', 'info'), plan.get('reason'))
        # P claims the corner before A's cluster, so no backjump is needed.
        self.assertEqual(plan['search']['backjumps'], 0)
        at = {p['ref']: p['position_nm'] for p in plan['placements']}
        self.assertLessEqual(math.dist(at['P'], (0, 0)), 500_000)
        test_placer_search.SearchTest.assert_independently_valid(self, source, policy, plan)

    def test_failure_records_the_deepest_partial_placement(self):
        # Diagnostic only: the plan stays empty and unknown, and the input unchanged.
        source, policy = cluster_case()
        original = copy.deepcopy(source)
        report = pack(source, policy)
        self.assertEqual((report['status'], report['outcome'], report['placements']),
                         ('packing_search_exhausted', 'unknown', []))
        self.assertTrue(1 <= report['search']['placed_peak'] < 7, report['search'])
        self.assertEqual(report, pack(source, policy))
        self.assertEqual(source, original)
        # One step places U1 (its first candidate is admissible); C1 then has none left.
        report = pack(source, dict(policy, step_budget=1))
        self.assertEqual((report['status'], report['search']['placed_peak']), ('packing_budget_exhausted', 1))


if __name__ == '__main__':
    unittest.main()

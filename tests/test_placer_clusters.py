"""Original fixtures for joint pad-locality cluster packing (AGENTEE-335)."""
import copy
import time
import unittest
from unittest.mock import patch

from agent_ee import electrical_locality
from agent_ee.placer import pack, transform
from test_electrical_locality import board, declaration
from test_placement import MM, part, rectangle
from test_placer import request


def cluster_board(limit=5*MM):
    source = board()
    cap = copy.deepcopy(source['footprints'][1])
    cap['ref'] = cap['uuid'] = 'C2'
    for pad in cap['pads']:
        pad['uuid'] = 'C2-' + pad['number']
    source['footprints'].append(cap)
    doc = declaration(limit=limit)
    second = declaration(cap='C2', limit=limit)['obligations'][0]
    doc['obligations'].append(second)
    return source, doc


def three_cap_case():
    source, doc = cluster_board()
    third = copy.deepcopy(source['footprints'][1])
    third['ref'] = third['uuid'] = 'C3'
    for pad in third['pads']:
        pad['uuid'] = 'C3-' + pad['number']
    source['footprints'].append(third)
    doc['obligations'] += declaration(cap='C3')['obligations']
    source['outline']['polygons'] = [rectangle(0, 0, 10, 8)]
    source['footprints'][0] = transform(source['footprints'][0], [0, 6*MM], 0)
    for index, limit in enumerate((3*MM, 4*MM, 4*MM)):
        for role in ('supply', 'return'):
            doc['obligations'][index][role]['max_distance_nm'] = limit
    policy = request(electrical_locality=doc, priority_refs=['C1'], step_budget=500)
    return source, policy


class ClusterTests(unittest.TestCase):
    def test_tightest_member_precedes_loose_priority_and_is_deterministic(self):
        source, doc = cluster_board()
        for role in ('supply', 'return'):
            doc['obligations'][1][role]['max_distance_nm'] = 3*MM
        policy = request(electrical_locality=doc, priority_refs=['C1'])
        before = copy.deepcopy((source, policy))
        plan = pack(source, policy)
        self.assertEqual(plan['status'], 'planned', plan)
        self.assertEqual(plan['search']['locality_clusters'][0]['references'], ['C2', 'C1'])
        self.assertEqual(plan, pack(source, policy))
        self.assertEqual((source, policy), before)
        self.assertEqual(plan['locality_grading']['status'], 'pass')

    def test_joint_tightest_first_succeeds_within_independent_search_budget(self):
        source, doc = cluster_board()
        source['outline']['polygons'] = [rectangle(0, 0, 10, 5)]
        source['footprints'][0] = transform(source['footprints'][0], [8*MM, 2*MM], 0)
        for index, limit in enumerate((4*MM, 3*MM)):
            for role in ('supply', 'return'):
                doc['obligations'][index][role]['max_distance_nm'] = limit
        plan = pack(source, request(electrical_locality=doc, priority_refs=['C1'], step_budget=50))
        self.assertEqual(plan['status'], 'planned', plan)
        self.assertLess(plan['steps_used'], 50)
        self.assertEqual(plan['locality_grading']['status'], 'pass')

    def test_infeasible_fixed_cluster_is_named_unknown_within_seconds(self):
        source, doc = cluster_board(limit=0)
        start = time.monotonic()
        plan = pack(source, request(electrical_locality=doc, step_budget=1000000))
        self.assertLess(time.monotonic()-start, 3)
        self.assertEqual((plan['status'], plan['outcome'], plan['placements']),
                         ('locality_cluster_exhausted', 'unknown', []))
        self.assertEqual(plan['search']['blocking_cluster']['obligations'], ['C1', 'C2'])
        self.assertEqual(plan['search']['blocking_cluster']['references'], ['U1', 'C1', 'C2'])
        self.assertLess(plan['steps_used'], 1000)

    def test_joint_search_revisits_a_nonlead_member_instead_of_exhausting(self):
        source, policy = three_cap_case()
        doc = policy['electrical_locality']
        plan = pack(source, policy)
        self.assertEqual(plan['status'], 'planned', plan)
        self.assertLess(plan['steps_used'], 20)
        self.assertEqual(plan['locality_grading']['status'], 'pass')
        placements = {p['ref']: p for p in plan['placements']}
        saved = {**source, 'footprints': [transform(fp, placements[fp['ref']]['position_nm'],
                    placements[fp['ref']]['rotation_deg']) for fp in source['footprints']]}
        self.assertEqual(electrical_locality.grade(saved, doc)['status'], 'pass')

    def test_individually_feasible_members_have_no_joint_fixed_geometry_candidate(self):
        source, doc = cluster_board(limit=3*MM)
        source['footprints'][0] = transform(source['footprints'][0], [0, 0], 0)
        policy = request(electrical_locality=doc, obstacles=[
            dict(side='F', polygons=[rectangle(0, 2.5, 20, 17.5)])])
        # Each capacitor can occupy the one slot beside the fixed IC, but the
        # two cannot share it. No area proof or individual pad refusal explains this.
        for i in (1, 2):
            one = {**source, 'footprints': source['footprints'][:1] + [source['footprints'][i]]}
            single = {**policy, 'electrical_locality': {**doc, 'obligations': [doc['obligations'][i-1]]}}
            self.assertEqual(pack(one, single)['status'], 'planned')
        started = time.monotonic()
        plan = pack(source, policy)
        self.assertLess(time.monotonic()-started, 3)
        self.assertEqual((plan['status'], plan['outcome'], plan['placements']),
                         ('locality_cluster_exhausted', 'unknown', []))
        self.assertEqual(plan['search']['blocking_cluster']['obligations'], ['C1', 'C2'])
        self.assertLess(plan['steps_used'], 20)

    def test_backjump_keeps_cluster_whole_and_accumulates_its_work(self):
        source, policy = three_cap_case()
        blocker = part('X', 0, 0)
        blocker['pads'] = copy.deepcopy(source['footprints'][0]['pads'])
        for pad in blocker['pads']:
            pad['uuid'] = 'X-' + pad['number']
            pad['position_nm'] = [MM, MM]
        source['footprints'].append(blocker)
        policy.update(priority_refs=['X'], step_budget=10000)
        plan = pack(source, policy)
        self.assertEqual(plan['status'], 'planned', plan)
        self.assertEqual(plan['search']['backjumps'], 1)
        self.assertEqual(plan['search']['locality_clusters'][0]['references'], ['C1', 'C2', 'C3'])
        # Each attempt alone uses fewer than 40 candidates; their combined work
        # exceeds 40. Resetting the cluster guard on backjump would publish a plan.
        with patch('agent_ee.placer.CLUSTER_STEP_BUDGET', 40):
            bounded = pack(source, policy)
        self.assertEqual((bounded['status'], bounded['outcome'], bounded['placements']),
                         ('locality_cluster_exhausted', 'unknown', []))
        self.assertEqual(bounded['search']['backjumps'], 1)
        self.assertEqual(bounded['search']['blocking_cluster']['obligations'], ['C1', 'C2', 'C3'])
        self.assertEqual(plan['locality_grading']['status'], 'pass')

    def test_switch_peer_and_movable_hub_stay_in_the_same_unit(self):
        source, doc = cluster_board()
        inductor = copy.deepcopy(source['footprints'][1])
        inductor['ref'] = inductor['uuid'] = 'L1'
        for pad in inductor['pads']:
            pad['uuid'] = 'L1-' + pad['number']
        source['footprints'].append(inductor)
        switch = copy.deepcopy(doc['obligations'][0]['supply'])
        switch['to']['reference'] = 'L1'
        doc['obligations'][0]['switch'] = switch
        plan = pack(source, request(fixed_refs=[], electrical_locality=doc))
        self.assertEqual(plan['status'], 'planned', plan)
        unit, = plan['search']['locality_clusters']
        self.assertEqual(unit['references'], ['U1', 'C1', 'C2', 'L1'])
        self.assertEqual(plan['locality_grading']['status'], 'pass')
        self.assertIn('switch', {m['role'] for m in plan['locality_grading']['measured']})

    def test_cluster_guard_is_unknown_and_cleans_up_partial_state(self):
        source, doc = cluster_board()
        original = copy.deepcopy(source)
        with patch('agent_ee.placer.CLUSTER_STEP_BUDGET', 1):
            plan = pack(source, request(electrical_locality=doc))
        self.assertEqual((plan['status'], plan['outcome'], plan['placements']),
                         ('locality_cluster_exhausted', 'unknown', []))
        self.assertEqual(plan['search']['blocking_cluster']['obligations'], ['C1', 'C2'])
        self.assertEqual(plan['search']['termination'], 'locality_cluster_exhausted')
        self.assertEqual(source, original)
        self.assertEqual(pack(source, request(electrical_locality=doc))['status'], 'planned')


if __name__ == '__main__':
    unittest.main()

"""Original fixtures for joint pad-locality cluster packing (AGENTEE-335)."""
import copy
import random
import unittest
from unittest.mock import patch

from agent_ee import electrical_locality
from agent_ee.placer import _pack, pack, transform
from test_electrical_locality import FIXTURE_SOURCES, board, declaration
from test_placement import MM, part, rectangle
from test_placer import request
from test_placer_copper_extent import copper_violations


def clone(fp, ref):
    copied = copy.deepcopy(fp)
    copied['ref'] = copied['uuid'] = ref
    for pad in copied['pads']:
        pad['uuid'] = ref + '-' + pad['number']
    return copied


def random_cluster_case(rng):
    """Original seeded geometry shared with the reviewer's random differential."""
    source = board()
    source['max_error_nm'] = 5000  # fixture fact: KiCad's default m_MaxError, which native extraction always emits
    hub, capacitor = source['footprints']
    count = rng.randint(2, 4)
    footprints = [hub] + [clone(capacitor, f'C{i}') for i in range(1, count + 1)]
    doc = {'schema': electrical_locality.SCHEMA, 'source_records': FIXTURE_SOURCES, 'obligations': []}

    def limit():
        return rng.choice([int(rng.uniform(1.5, 8)*MM), 3*MM, 4*MM, 2*MM + 500000])

    for i in range(1, count + 1):
        obligation = declaration(cap=f'C{i}', limit=limit())['obligations'][0]
        if rng.random() < 0.3:
            obligation['return']['max_distance_nm'] = limit()
        doc['obligations'].append(obligation)
    if rng.random() < 0.35:
        footprints.append(clone(capacitor, 'L1'))
        switch = copy.deepcopy(doc['obligations'][0]['supply'])
        switch['to']['reference'] = 'L1'
        switch['max_distance_nm'] = limit()
        doc['obligations'][0]['switch'] = switch
    for i in range(rng.randint(0, 4)):
        footprints.append(part(f'R{i}', 15, 15))
    width, height = rng.choice([(20, 20), (12, 10), (10, 8), (14, 6), (9, 9)])
    source['outline']['polygons'] = [rectangle(0, 0, width, height)]
    fixed = ['U1'] if rng.random() < 0.7 else []
    footprints[0] = transform(footprints[0],
        [rng.randint(0, width-3)*MM, rng.randint(0, height-3)*MM], rng.choice([0, 90]))
    source['footprints'] = footprints
    obstacles = []
    if rng.random() < 0.4:
        x, y = rng.uniform(0, width-2), rng.uniform(0, height-2)
        obstacles.append(dict(side='F', polygons=[rectangle(x, y, rng.uniform(1, 4), rng.uniform(1, 4))]))
    priorities = rng.sample([fp['ref'] for fp in footprints[1:]], rng.randint(0, 2))
    policy = request(electrical_locality=doc, fixed_refs=fixed, priority_refs=priorities,
                     obstacles=obstacles, step_budget=rng.choice([200, 2000, 20000]))
    return source, policy


def without_joint_diagnostics(result):
    """Only the joint and failed copper-aligned attempts are additive; preserve legacy cluster membership."""
    result = copy.deepcopy(result)
    result.get('search', {}).pop('joint_cluster_attempt', None)
    result.get('search', {}).pop('copper_aligned_attempt', None)  # AGENTEE-371 (366b gap 4)
    return result


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


def joint_pack(source, policy):
    """Exercise the joint engine separately from the public legacy-first contract."""
    return _pack(source, policy, heartbeat=None, joint=True)


class ClusterTests(unittest.TestCase):
    def test_advisory_only_joint_cluster_does_not_prune(self):
        # P1 seed 1/case 0: hard axis pruning exhausts this advisory-only case.
        source, policy = random_cluster_case(random.Random(1))
        doc = policy['electrical_locality']
        doc.pop('source_records')
        for obligation in doc['obligations']:
            for role in electrical_locality.roles(obligation):
                obligation[role].pop('provenance')
                obligation[role]['max_distance_nm'] = 1
        result = joint_pack(source, policy)
        self.assertEqual(result['status'], 'planned', result)
        self.assertEqual(result['locality_grading']['status'], 'info')
        self.assertTrue(all(a['exceeded'] for a in result['locality_grading']['advisories']))

    def test_tightest_member_precedes_loose_priority_and_is_deterministic(self):
        source, doc = cluster_board()
        for role in ('supply', 'return'):
            doc['obligations'][1][role]['max_distance_nm'] = 3*MM
        policy = request(electrical_locality=doc, priority_refs=['C1'])
        before = copy.deepcopy((source, policy))
        plan = joint_pack(source, policy)
        self.assertEqual(plan['status'], 'planned', plan)
        self.assertEqual(plan['search']['locality_clusters'][0]['references'], ['C2', 'C1'])
        self.assertEqual(plan, joint_pack(source, policy))
        self.assertEqual((source, policy), before)
        self.assertEqual(plan['locality_grading']['status'], 'pass')

    def test_joint_tightest_first_succeeds_within_independent_search_budget(self):
        source, doc = cluster_board()
        source['outline']['polygons'] = [rectangle(0, 0, 10, 5)]
        source['footprints'][0] = transform(source['footprints'][0], [8*MM, 2*MM], 0)
        for index, limit in enumerate((4*MM, 3*MM)):
            for role in ('supply', 'return'):
                doc['obligations'][index][role]['max_distance_nm'] = limit
        plan = joint_pack(source, request(electrical_locality=doc, priority_refs=['C1'], step_budget=50))
        self.assertEqual(plan['status'], 'planned', plan)
        self.assertLess(plan['steps_used'], 50)
        self.assertEqual(plan['locality_grading']['status'], 'pass')

    def test_finite_fixed_cluster_search_is_named_unknown_with_bounded_steps(self):
        source, doc = cluster_board(limit=0)
        plan = joint_pack(source, request(electrical_locality=doc, step_budget=1000000))
        self.assertEqual((plan['status'], plan['outcome'], plan['placements']),
                         ('locality_cluster_exhausted', 'unknown', []))
        self.assertEqual(plan['search']['blocking_cluster']['obligations'], ['C1', 'C2'])
        self.assertEqual(plan['search']['blocking_cluster']['references'], ['U1', 'C1', 'C2'])
        self.assertLess(plan['steps_used'], 1000)

    def test_joint_search_revisits_a_nonlead_member_instead_of_exhausting(self):
        source, policy = three_cap_case()
        doc = policy['electrical_locality']
        plan = joint_pack(source, policy)
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
        plan = joint_pack(source, policy)
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
        plan = joint_pack(source, policy)
        self.assertEqual(plan['status'], 'planned', plan)
        self.assertEqual(plan['search']['backjumps'], 1)
        self.assertEqual(plan['search']['locality_clusters'][0]['references'], ['C1', 'C2', 'C3'])
        # Each attempt alone uses fewer than 40 candidates; their combined work
        # exceeds 40. Resetting the cluster guard on backjump would publish a plan.
        with patch('agent_ee.placer.CLUSTER_STEP_BUDGET', 40):
            bounded = joint_pack(source, policy)
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
        plan = joint_pack(source, request(fixed_refs=[], electrical_locality=doc))
        self.assertEqual(plan['status'], 'planned', plan)
        unit, = plan['search']['locality_clusters']
        self.assertEqual(unit['references'], ['U1', 'C1', 'C2', 'L1'])
        self.assertEqual(plan['locality_grading']['status'], 'pass')
        self.assertIn('switch', {m['role'] for m in plan['locality_grading']['measured']})

    def test_cluster_guard_is_unknown_and_cleans_up_partial_state(self):
        source, policy = three_cap_case()
        policy['step_budget'] = 2000
        original = copy.deepcopy((source, policy))
        with patch('agent_ee.placer.CLUSTER_STEP_BUDGET', 1):
            legacy = _pack(source, policy, heartbeat=None, joint=False)
            plan = pack(source, policy)
            guarded = joint_pack(source, policy)
        self.assertEqual((plan['status'], plan['outcome'], plan['placements']),
                         (legacy['status'], 'unknown', []))
        self.assertEqual(without_joint_diagnostics(plan), without_joint_diagnostics(legacy))
        attempt = plan['search']['joint_cluster_attempt']
        self.assertEqual(attempt['status'], 'locality_cluster_exhausted')
        self.assertEqual(attempt['blocking_cluster']['obligations'], ['C1', 'C2', 'C3'])
        self.assertEqual(attempt['termination'], 'locality_cluster_exhausted')
        self.assertEqual(guarded['placements'], [])
        self.assertEqual((source, policy), original)
        self.assertEqual(pack(source, policy)['status'], 'planned')

    def test_complete_proposal_locality_defect_is_rejected_on_both_paths(self):
        # AGENTEE-347: deliberately let the search accept and prefer far corners.
        # Keep the independent grader real: even the joint axis projections admit
        # square corners outside the declared circular distance limit.
        source, doc = cluster_board(limit=3*MM)
        policy = request(electrical_locality=doc)
        original = copy.deepcopy((source, policy))
        for path in ('legacy', 'joint', 'public'):
            with self.subTest(path=path):
                run = pack if path == 'public' else (
                    lambda source, policy: _pack(source, policy, heartbeat=None, joint=path == 'joint'))
                self.assertEqual(run(source, policy)['status'],
                                 'planned')
                with patch.object(electrical_locality.CandidateLocality, 'constraint',
                                  side_effect=lambda position: (set(), -sum(position))):
                    result = run(source, policy)
                self.assertEqual((result['status'], result['outcome'], result['placements']),
                                 ('judge_rejected', 'unknown', []))
                if path == 'public':
                    self.assertEqual(result['search']['joint_cluster_attempt']['status'],
                                     'judge_rejected')
                self.assertEqual(result['reason'], 'complete proposal failed pad locality')
                self.assertEqual(result['locality_grading']['status'], 'defect')
                self.assertTrue(result['locality_grading']['defects'])
                self.assertEqual(result['locality_grading']['unknown'], [])
                self.assertLess(result['steps_used'], 20)
                self.assertEqual((source, policy), original)

    def test_seeded_legacy_first_differential_preserves_whole_results(self):
        # Representative subset of the original review differential (AGENTEE-347),
        # using the retained per-part engine rather than loading historical code.
        cases = []
        for seed in (1, 7, 335):
            rng = random.Random(seed)
            for i in range(8):
                source, policy = random_cluster_case(rng)
                # These slow finite refusals duplicate the other failure cases.
                if (seed, i) not in ((1, 6), (7, 0), (7, 5)):
                    cases.append((f'{seed}:{i}', source, policy))
        # Seed 2, case 55 from the reviewer's 280-case differential completes
        # jointly; keep it instead of duplicating the hand-written budget test.
        rng = random.Random(2)
        for _ in range(56):
            source, policy = random_cluster_case(rng)
        cases.append(('2:55', source, policy))
        counts = dict(legacy_plan=0, both_fail=0, joint_completion=0)
        movable = 0
        for label, source, policy in cases:
            with self.subTest(case=label):
                movable += not policy['fixed_refs']
                original = copy.deepcopy((source, policy))
                legacy = _pack(source, policy, heartbeat=None, joint=False)
                result = pack(source, policy)
                self.assertEqual(result, pack(source, policy))
                self.assertEqual((source, policy), original)
                self.assertLessEqual(result['steps_used'], policy['step_budget'])
                if label == '2:55':
                    self.assertEqual(legacy['status'], 'packing_search_exhausted')
                    self.assertEqual(result['status'], 'planned')
                attempt = result.get('search', {}).get('joint_cluster_attempt')
                if attempt:
                    self.assertLessEqual(result['steps_used'] + attempt['steps_used'],
                                         policy['step_budget'])
                if legacy['status'] == 'planned':
                    counts['legacy_plan'] += 1
                    self.assertEqual(result, legacy)
                    self.assertIsNone(attempt)
                elif result['status'] == 'planned':
                    counts['joint_completion'] += 1
                    self.assertEqual(result['search'].get('legacy_attempt'),
                                     dict(status=legacy['status'], steps_used=legacy['steps_used']))
                    self.assertGreater(result['steps_used'], legacy['steps_used'])
                    self.assertEqual(result['locality_grading']['status'], 'pass')
                    poses = {p['ref']: p for p in result['placements']}
                    saved = {**source, 'footprints': [transform(fp,
                        poses[fp['ref']]['position_nm'], poses[fp['ref']]['rotation_deg'])
                        for fp in source['footprints']]}
                    self.assertEqual(electrical_locality.grade(
                        saved, policy['electrical_locality'])['status'], 'pass')
                else:
                    counts['both_fail'] += 1
                    self.assertEqual(without_joint_diagnostics(result),
                                     without_joint_diagnostics(legacy))
                    self.assertEqual(result['placements'], [])
        self.assertGreater(movable, 0)
        for category, count in counts.items():
            self.assertGreater(count, 0, category)

    def test_joint_completion_uses_only_remaining_steps_and_regrades(self):
        source, policy = three_cap_case()
        policy['step_budget'] = 2000
        legacy = _pack(source, policy, heartbeat=None, joint=False)
        joint = joint_pack(source, policy)
        with patch('agent_ee.placer.time.monotonic', side_effect=AssertionError('clock')):
            result = pack(source, policy)
        self.assertEqual(legacy['status'], 'packing_search_exhausted')
        self.assertEqual(result['status'], 'planned')
        self.assertEqual(result['steps_used'], legacy['steps_used'] + joint['steps_used'])
        self.assertLessEqual(result['steps_used'], policy['step_budget'])
        self.assertEqual(result['locality_grading']['status'], 'pass')
        for budget in (500, legacy['steps_used'] + 1):
            bounded_policy = {**policy, 'step_budget': budget}
            bounded = pack(source, bounded_policy)
            attempt = bounded['search']['joint_cluster_attempt']
            self.assertEqual(bounded['placements'], [])
            self.assertLessEqual(bounded['steps_used'] + attempt['steps_used'], budget)
            self.assertEqual(attempt['status'], 'not_attempted' if budget == 500
                             else 'packing_budget_exhausted')

    def test_legacy_plan_does_not_attempt_joint_search_or_read_clock(self):
        source, doc = cluster_board()
        policy = request(electrical_locality=doc)
        legacy = _pack(source, policy, heartbeat=None, joint=False)
        with patch('agent_ee.placer._Search.place_cluster', side_effect=AssertionError('joint search')), \
                patch('agent_ee.placer.time.monotonic', side_effect=AssertionError('clock')):
            result = pack(source, policy)
        self.assertEqual(result, legacy)
        self.assertNotIn('joint_cluster_attempt', result['search'])

    def test_joint_attempt_inherits_absolute_opt_in_deadline(self):
        source, policy = three_cap_case()
        policy.update(step_budget=2000, search_timeout_s=60)
        from agent_ee import placer
        original = placer._Search.__init__
        searches = []

        def remember(search, *args, **kwargs):
            original(search, *args, **kwargs)
            searches.append(search)

        with patch.object(placer._Search, '__init__', remember):
            result = pack(source, policy)
        self.assertEqual(result['status'], 'planned')
        self.assertEqual(len(searches), 2)
        self.assertEqual(searches[1].deadline, searches[0].deadline)
        self.assertLess(searches[1].budget, searches[0].budget)

    def test_expired_opt_in_deadline_keeps_legacy_failure(self):
        from agent_ee import placer
        source, policy = three_cap_case()
        policy.update(step_budget=2000, search_timeout_s=60)
        original = placer._Search.__init__
        attempts = []

        def initialize(search, *args, **kwargs):
            attempts.append(search)
            original(search, *args, **kwargs)

        with patch.object(placer._Search, '__init__', initialize), \
                patch.object(placer.time, 'monotonic', side_effect=lambda: 100 if len(attempts) == 2 else 0):
            result = pack(source, policy)
        self.assertEqual((result['status'], result['outcome'], result['placements']),
                         ('packing_search_exhausted', 'unknown', []))
        diagnostic = result['search']['joint_cluster_attempt']
        self.assertEqual(diagnostic['status'], 'packing_budget_exhausted')
        self.assertEqual(diagnostic['steps_used'], 0)
        self.assertEqual(attempts[1].deadline, 60)

    def test_projection_pruning_bounds_joint_candidate_evaluations(self):
        source, policy = three_cap_case()
        with patch.object(electrical_locality.CandidateLocality, 'constraint',
                          autospec=True, side_effect=electrical_locality.CandidateLocality.constraint) as evaluate:
            result = joint_pack(source, policy)
        self.assertEqual(result['status'], 'planned')
        self.assertLessEqual(evaluate.call_count, 300)


    def test_seeded_legacy_plans_preserve_verdict_placements_and_steps(self):
        # Frozen per-part outputs from ee40bb3579bb00843c34ca9b5dfe60c4f4b8fb52; seeded original multi-member fixtures.
        # (seed, case) in copper_gated were re-pinned by AGENTEE-366 (coordinator ruling):
        # their frozen plans put C3's pad 1 copper at y=-50000..50000 nm, off the board.
        # (1, 1) was re-pinned again by ruling 366f: the structural setback S = max_error_nm + 50
        # (5050 nm on this fixture) moves every placement +S in y; verdict and steps are unchanged.
        copper_gated = {(1, 1), (2, 1)}
        expected = {1: [{'outcome': 'unknown',
              'placements': [],
              'status': 'packing_budget_exhausted',
              'steps_used': 200},
             {'outcome': 'info',
              'placements': [{'fixed': False,
                              'position_nm': [2500000, 2555050],
                              'ref': 'C1',
                              'rotation_deg': 0,
                              'uuid': 'C1'},
                             {'fixed': False,
                              'position_nm': [0, 2555050],
                              'ref': 'C2',
                              'rotation_deg': 0,
                              'uuid': 'C2'},
                             {'fixed': False,
                              'position_nm': [0, 55050],
                              'ref': 'C3',
                              'rotation_deg': 0,
                              'uuid': 'C3'},
                             {'fixed': False,
                              'position_nm': [5000000, 2055050],
                              'ref': 'C4',
                              'rotation_deg': 90,
                              'uuid': 'C4'},
                             {'fixed': False,
                              'position_nm': [2500000, 2055050],
                              'ref': 'U1',
                              'rotation_deg': 90,
                              'uuid': 'U1'}],
              'status': 'planned',
              'steps_used': 3738},
             {'outcome': 'info',
              'placements': [{'fixed': False,
                              'position_nm': [14000000, 12500000],
                              'ref': 'C1',
                              'rotation_deg': 180,
                              'uuid': 'C1'},
                             {'fixed': False,
                              'position_nm': [11500000, 12000000],
                              'ref': 'C2',
                              'rotation_deg': 270,
                              'uuid': 'C2'},
                             {'fixed': False,
                              'position_nm': [12000000, 15500000],
                              'ref': 'C3',
                              'rotation_deg': 0,
                              'uuid': 'C3'},
                             {'fixed': False,
                              'position_nm': [0, 2500000],
                              'ref': 'R0',
                              'rotation_deg': 0,
                              'uuid': 'R0'},
                             {'fixed': False,
                              'position_nm': [0, 0],
                              'ref': 'R1',
                              'rotation_deg': 0,
                              'uuid': 'R1'},
                             {'fixed': False,
                              'position_nm': [0, 5000000],
                              'ref': 'R2',
                              'rotation_deg': 0,
                              'uuid': 'R2'},
                             {'fixed': False,
                              'position_nm': [0, 7500000],
                              'ref': 'R3',
                              'rotation_deg': 0,
                              'uuid': 'R3'},
                             {'fixed': True,
                              'position_nm': [12000000, 13000000],
                              'ref': 'U1',
                              'rotation_deg': 0,
                              'uuid': 'U1'}],
              'status': 'planned',
              'steps_used': 19},
             {'outcome': 'unknown',
              'placements': [],
              'status': 'packing_search_exhausted',
              'steps_used': 502},
             {'outcome': 'info',
              'placements': [{'fixed': False,
                              'position_nm': [9500000, 13000000],
                              'ref': 'C1',
                              'rotation_deg': 270,
                              'uuid': 'C1'},
                             {'fixed': False,
                              'position_nm': [12000000, 13500000],
                              'ref': 'C2',
                              'rotation_deg': 180,
                              'uuid': 'C2'},
                             {'fixed': False,
                              'position_nm': [12500000, 15000000],
                              'ref': 'C3',
                              'rotation_deg': 90,
                              'uuid': 'C3'},
                             {'fixed': False,
                              'position_nm': [10000000, 16500000],
                              'ref': 'C4',
                              'rotation_deg': 0,
                              'uuid': 'C4'},
                             {'fixed': False,
                              'position_nm': [0, 0],
                              'ref': 'R0',
                              'rotation_deg': 0,
                              'uuid': 'R0'},
                             {'fixed': False,
                              'position_nm': [0, 2500000],
                              'ref': 'R1',
                              'rotation_deg': 0,
                              'uuid': 'R1'},
                             {'fixed': True,
                              'position_nm': [10000000, 14000000],
                              'ref': 'U1',
                              'rotation_deg': 0,
                              'uuid': 'U1'}],
              'status': 'planned',
              'steps_used': 18},
             {'outcome': 'unknown',
              'placements': [],
              'status': 'packing_search_exhausted',
              'steps_used': 735},
             {'outcome': 'unknown',
              'placements': [],
              'status': 'packing_search_exhausted',
              'steps_used': 1761},
             {'outcome': 'info',
              'placements': [{'fixed': False,
                              'position_nm': [0, 2500000],
                              'ref': 'C1',
                              'rotation_deg': 0,
                              'uuid': 'C1'},
                             {'fixed': False,
                              'position_nm': [0, 5000000],
                              'ref': 'C2',
                              'rotation_deg': 0,
                              'uuid': 'C2'},
                             {'fixed': False,
                              'position_nm': [0, 7500000],
                              'ref': 'C3',
                              'rotation_deg': 0,
                              'uuid': 'C3'},
                             {'fixed': False,
                              'position_nm': [2500000, 2500000],
                              'ref': 'C4',
                              'rotation_deg': 0,
                              'uuid': 'C4'},
                             {'fixed': False,
                              'position_nm': [5000000, 6500000],
                              'ref': 'L1',
                              'rotation_deg': 90,
                              'uuid': 'L1'},
                             {'fixed': False,
                              'position_nm': [2500000, 0],
                              'ref': 'R0',
                              'rotation_deg': 0,
                              'uuid': 'R0'},
                             {'fixed': False,
                              'position_nm': [2500000, 8000000],
                              'ref': 'R1',
                              'rotation_deg': 0,
                              'uuid': 'R1'},
                             {'fixed': False,
                              'position_nm': [5000000, 0],
                              'ref': 'R2',
                              'rotation_deg': 0,
                              'uuid': 'R2'},
                             {'fixed': False,
                              'position_nm': [0, 0],
                              'ref': 'R3',
                              'rotation_deg': 0,
                              'uuid': 'R3'},
                             {'fixed': False,
                              'position_nm': [2500000, 5500000],
                              'ref': 'U1',
                              'rotation_deg': 0,
                              'uuid': 'U1'}],
              'status': 'planned',
              'steps_used': 894}],
         2: [{'outcome': 'info',
              'placements': [{'fixed': False,
                              'position_nm': [10500000, 0],
                              'ref': 'C1',
                              'rotation_deg': 270,
                              'uuid': 'C1'},
                             {'fixed': False,
                              'position_nm': [11000000, 2500000],
                              'ref': 'C2',
                              'rotation_deg': 0,
                              'uuid': 'C2'},
                             {'fixed': False,
                              'position_nm': [0, 0],
                              'ref': 'R0',
                              'rotation_deg': 0,
                              'uuid': 'R0'},
                             {'fixed': True,
                              'position_nm': [11000000, 2000000],
                              'ref': 'U1',
                              'rotation_deg': 90,
                              'uuid': 'U1'}],
              'status': 'planned',
              'steps_used': 12},
             {'outcome': 'info',
              'placements': [{'fixed': False,
                              'position_nm': [2000000, 7500000],
                              'ref': 'C1',
                              'rotation_deg': 180,
                              'uuid': 'C1'},
                             {'fixed': False,
                              'position_nm': [4500000, 6500000],
                              'ref': 'C2',
                              'rotation_deg': 180,
                              'uuid': 'C2'},
                             {'fixed': False,
                              'position_nm': [0, 8000000],
                              'ref': 'C3',
                              'rotation_deg': 0,
                              'uuid': 'C3'},
                             {'fixed': False,
                              'position_nm': [0, 0],
                              'ref': 'R0',
                              'rotation_deg': 0,
                              'uuid': 'R0'},
                             {'fixed': False,
                              'position_nm': [0, 2500000],
                              'ref': 'R1',
                              'rotation_deg': 0,
                              'uuid': 'R1'},
                             {'fixed': False,
                              'position_nm': [2500000, 0],
                              'ref': 'R2',
                              'rotation_deg': 0,
                              'uuid': 'R2'},
                             {'fixed': False,
                              'position_nm': [5000000, 0],
                              'ref': 'R3',
                              'rotation_deg': 0,
                              'uuid': 'R3'},
                             {'fixed': False,
                              'position_nm': [2500000, 9000000],
                              'ref': 'U1',
                              'rotation_deg': 90,
                              'uuid': 'U1'}],
              'status': 'planned',
              'steps_used': 473},
             {'outcome': 'info',
              'placements': [{'fixed': False,
                              'position_nm': [0, 2500000],
                              'ref': 'C1',
                              'rotation_deg': 0,
                              'uuid': 'C1'},
                             {'fixed': False,
                              'position_nm': [1500000, 5000000],
                              'ref': 'C2',
                              'rotation_deg': 0,
                              'uuid': 'C2'},
                             {'fixed': False,
                              'position_nm': [0, 0],
                              'ref': 'R0',
                              'rotation_deg': 0,
                              'uuid': 'R0'},
                             {'fixed': False,
                              'position_nm': [2500000, 0],
                              'ref': 'R1',
                              'rotation_deg': 0,
                              'uuid': 'R1'},
                             {'fixed': False,
                              'position_nm': [4000000, 5000000],
                              'ref': 'R2',
                              'rotation_deg': 0,
                              'uuid': 'R2'},
                             {'fixed': False,
                              'position_nm': [5000000, 0],
                              'ref': 'R3',
                              'rotation_deg': 0,
                              'uuid': 'R3'},
                             {'fixed': False,
                              'position_nm': [2500000, 4500000],
                              'ref': 'U1',
                              'rotation_deg': 90,
                              'uuid': 'U1'}],
              'status': 'planned',
              'steps_used': 221},
             {'outcome': 'unknown', 'placements': [], 'status': 'fixed_conflict', 'steps_used': 0},
             {'outcome': 'unknown',
              'placements': [],
              'status': 'packing_budget_exhausted',
              'steps_used': 200},
             {'outcome': 'unknown', 'placements': [], 'status': 'fixed_conflict', 'steps_used': 0},
             {'outcome': 'info',
              'placements': [{'fixed': False,
                              'position_nm': [3500000, 0],
                              'ref': 'C1',
                              'rotation_deg': 270,
                              'uuid': 'C1'},
                             {'fixed': False,
                              'position_nm': [6500000, 2000000],
                              'ref': 'C2',
                              'rotation_deg': 90,
                              'uuid': 'C2'},
                             {'fixed': False,
                              'position_nm': [0, 2500000],
                              'ref': 'R0',
                              'rotation_deg': 0,
                              'uuid': 'R0'},
                             {'fixed': False,
                              'position_nm': [2500000, 2500000],
                              'ref': 'R1',
                              'rotation_deg': 0,
                              'uuid': 'R1'},
                             {'fixed': False,
                              'position_nm': [3983496, 5000000],
                              'ref': 'R2',
                              'rotation_deg': 0,
                              'uuid': 'R2'},
                             {'fixed': False,
                              'position_nm': [3983496, 7500000],
                              'ref': 'R3',
                              'rotation_deg': 0,
                              'uuid': 'R3'},
                             {'fixed': True,
                              'position_nm': [4000000, 0],
                              'ref': 'U1',
                              'rotation_deg': 0,
                              'uuid': 'U1'}],
              'status': 'planned',
              'steps_used': 13},
             {'outcome': 'info',
              'placements': [{'fixed': False,
                              'position_nm': [2500000, 0],
                              'ref': 'C1',
                              'rotation_deg': 270,
                              'uuid': 'C1'},
                             {'fixed': False,
                              'position_nm': [5500000, 2000000],
                              'ref': 'C2',
                              'rotation_deg': 90,
                              'uuid': 'C2'},
                             {'fixed': False,
                              'position_nm': [3000000, 2500000],
                              'ref': 'L1',
                              'rotation_deg': 0,
                              'uuid': 'L1'},
                             {'fixed': False,
                              'position_nm': [0, 2500000],
                              'ref': 'R0',
                              'rotation_deg': 0,
                              'uuid': 'R0'},
                             {'fixed': False,
                              'position_nm': [0, 5000000],
                              'ref': 'R1',
                              'rotation_deg': 0,
                              'uuid': 'R1'},
                             {'fixed': False,
                              'position_nm': [0, 7500000],
                              'ref': 'R2',
                              'rotation_deg': 0,
                              'uuid': 'R2'},
                             {'fixed': False,
                              'position_nm': [0, 10000000],
                              'ref': 'R3',
                              'rotation_deg': 0,
                              'uuid': 'R3'},
                             {'fixed': True,
                              'position_nm': [3000000, 0],
                              'ref': 'U1',
                              'rotation_deg': 0,
                              'uuid': 'U1'}],
              'status': 'planned',
              'steps_used': 16}]}
        movable_cases = planned = 0
        for seed, rows in expected.items():
            rng = random.Random(seed)
            for i, legacy in enumerate(rows):
                source, policy = random_cluster_case(rng)
                movable_cases += not policy['fixed_refs']
                original = copy.deepcopy((source, policy))
                with self.subTest(seed=seed, case=i, fixed=policy['fixed_refs']):
                    result = pack(source, policy)
                    self.assertEqual((source, policy), original)
                    if legacy['status'] == 'planned':
                        planned += 1
                        for key in ('status', 'outcome', 'steps_used', 'placements'):
                            self.assertEqual(result[key], legacy[key], key)
                        if (seed, i) in copper_gated:
                            setback = source['max_error_nm'] + 50  # S, derived here independently
                            self.assertEqual(copper_violations(source, result['placements'], setback), [])
                    if result['status'] == 'planned':
                        self.assertEqual(result['locality_grading']['status'], 'pass')
                        poses = {p['ref']: p for p in result['placements']}
                        transformed = {**source, 'footprints': [transform(fp,
                            poses[fp['ref']]['position_nm'], poses[fp['ref']]['rotation_deg'])
                            for fp in source['footprints']]}
                        self.assertEqual(electrical_locality.grade(
                            transformed, policy['electrical_locality'])['status'], 'pass')
                    else:
                        self.assertEqual(result['placements'], [])
        self.assertGreater(movable_cases, 0)
        self.assertGreater(planned, 0)


if __name__ == '__main__':
    unittest.main()

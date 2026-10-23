"""AGENTEE-249: board capacity at the packing gap, as a diagnostic and as a proof.

The proof applies by default once the search has failed (legacy first);
``capacity_proof: true`` applies it before the search and ``false`` opts out.

The fixtures are authored here from numbers. A 20 x 12 mm board with a 0.5 mm
edge margin and a fixed 12 x 9 mm block in its middle leaves two 3.25 mm wide
columns, 11 mm tall, and two 0.75 mm high strips (each clear of the block by
the 0.25 mm gap). A 3.2 mm square is at least 3.2 mm wide at every angle, so it
fits only in the columns, at most three to a column (3 x 3.2 + 2 x 0.25 =
10.1 mm; four need 13.55 mm): six squares fit and seven do not, although seven
need far less than the board's free area.
"""
from fractions import Fraction
import copy
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_ee import placer
from agent_ee.placer import pack
from test_placement import MM, capture, part, polygon, rectangle
from test_placer import authored_gap, request
from test_placer_residuals import box
import test_placer_search
from test_placer_search import board

MM2 = MM * MM


def authored_margin(policy):
    return {**policy, 'constraint_provenance': {**policy.get('constraint_provenance', {}),
        'min_courtyard_edge_mm': {'class': 'fixture_author', 'enforcement': 'hard',
            'value_nm': placer._edge_margin_nm(policy),
            'citation': 'tests/test_placer_capacity.py: authored fixture courtyard edge margin'}}}


def columns_case(count, **changes):
    parts = [part('U1', 4, 3, shape=box(4, 1.5, 16, 10.5))]
    parts += [part(f'C{i + 1}', 0, 0, shape=box(0, 0, 3.2, 3.2)) for i in range(count)]
    policy = request(gap_nm=250_000, min_courtyard_edge_mm=0.5, step_budget=20000, **changes)
    if policy.get('capacity_proof'):
        policy = authored_gap(authored_margin(policy), 'tests/test_placer_capacity.py:columns_case; authored 0.25 mm gap')
    return board(parts, rectangle(0, 0, 20, 12)), policy


def grown_square(side_mm, r_mm):
    # Steiner with pi taken as 3, as the engine bounds it: exact for the test's decimals.
    side, r = Fraction(str(side_mm)), Fraction(str(r_mm))
    return (side * side + 4 * side * r + 3 * r * r) * MM2


def fragmented_area():
    """Free area a 3.2 mm square grown by 0.125 mm can cover, at any angle, as the engine bounds it.

    Its centre keeps 1.725 mm (half the square plus r) from every grown blocker
    along each axis: in each 3.5 mm grown column, x within 1.725 mm of the
    middle, so 2.1..2.15 mm from the left. It reaches 3.2/sqrt(2) mm (rounded
    up to the half nanometre) plus r beyond that, past the column into the
    strips: 0.125 mm by 2.25 mm beside the block's x-grown slab, then 1 mm high
    strips above and below its y-grown slab.
    """
    half = math.isqrt(2 * 3_200_000 ** 2) + 1  # 3.2 mm x sqrt(2), not a perfect square, in half nanometres.
    reach = Fraction('2.15') * MM + Fraction(half, 2) + 125_000
    column = Fraction('3.5') * Fraction('11.25') * MM2
    beside = Fraction('0.125') * Fraction('2.25') * MM2 + (reach - 4 * MM) * 2 * MM
    return 2 * (column + beside)


def rational(pair):
    return Fraction(*pair)


class CapacityDiagnosticTest(unittest.TestCase):
    def test_overlapping_declared_obstacles_do_not_double_count_capacity(self):
        source = capture([part('C1', 0, 0)])
        full = rectangle(4, 4, 6, 4)
        alternatives = [[rectangle(4, 4, 3, 4), rectangle(7, 4, 3, 4)],
                        [rectangle(4, 4, 4, 4), rectangle(6, 4, 4, 4)],
                        [full, full, rectangle(5, 5, 1, 1)]]
        base = request(fixed_refs=[], gap_nm=250000, capacity_proof=True)
        expected = pack(source, {**base, 'obstacles': [{'side': 'F', 'polygons': [full]}]})
        self.assertEqual(expected['status'], 'planned', expected)
        for polygons in alternatives:
            actual = pack(source, {**base, 'obstacles': [{'side': 'F', 'polygons': polygons}]})
            self.assertEqual(actual['status'], 'planned', actual)
            self.assertEqual(actual['capacity'], expected['capacity'])

    def test_six_squares_are_placed_and_every_capacity_row_holds(self):
        source, policy = columns_case(6, capacity_proof=True)
        plan = pack(source, policy)
        self.assertEqual((plan['status'], plan['outcome']), ('planned', 'info'), plan.get('reason'))
        test_placer_search.SearchTest.assert_independently_valid(self, source, policy, plan)
        capacity = plan['capacity']
        self.assertEqual((capacity['basis'], capacity['gap_nm']), ('diagnostic-only', 250_000))
        free = capacity['sides']['F']['free_area']
        # Container: the board shrunk by margin - gap/2 = 0.375 mm, less the block grown by 0.125 mm
        # along each axis (its corners stay free).
        container = Fraction('19.25') * Fraction('11.25') * MM2
        block = (Fraction('12.25') * 9 + 12 * Fraction('0.25')) * MM2
        self.assertEqual(rational(free['available_area_upper_nm2']), container - block)
        self.assertEqual(rational(free['required_grown_courtyard_area_nm2']), 6 * grown_square(3.2, 0.125))
        self.assertEqual(rational(free['ratio']), 6 * grown_square(3.2, 0.125) / (container - block))
        row, = capacity['sides']['F']['fragmentation']
        self.assertEqual((row['square_nm'], len(row['references'])), (3_200_000, 6))
        self.assertEqual(rational(row['available_area_upper_nm2']), fragmented_area())
        self.assertLess(rational(row['ratio']), 1)
        self.assertEqual(capacity['sides']['F']['blocked_by'], ['U1'])
        self.assertNotIn('B', capacity['sides'])
        self.assertEqual(plan, pack(source, policy))

    def test_seven_squares_are_proved_infeasible_only_by_fragmentation(self):
        source, policy = columns_case(7, capacity_proof=True)
        original = copy.deepcopy(source)
        report = pack(source, policy)
        self.assertEqual((report['status'], report['outcome'], report['steps_used'], report['placements']),
                         ('packing_infeasible', 'defect', 0, []))
        proof = report['proof']
        self.assertEqual((proof['kind'], proof['side'], proof['square_nm']), ('board_capacity_fragmented', 'F', 3_200_000))
        self.assertEqual(rational(proof['available_area_upper_nm2']), fragmented_area())
        self.assertEqual(rational(proof['required_grown_courtyard_area_nm2']), 7 * grown_square(3.2, 0.125))
        self.assertEqual(proof['references'], [f'C{i + 1}' for i in range(7)])
        # Free area alone proves nothing: the squares need well under the board's free area.
        free = report['capacity']['sides']['F']['free_area']
        self.assertEqual(rational(free['ratio']), Fraction(7007, 8700))  # About 81 %.
        self.assertEqual(source, original)
        self.assertEqual(report, pack(source, policy))

    def test_unsourced_gap_cannot_establish_a_fragmentation_defect(self):
        source, policy = columns_case(7, capacity_proof=True)
        del policy['constraint_provenance']['gap_nm']
        report = pack(source, policy)
        self.assertEqual((report['status'], report['outcome'], report['placements']),
                         ('packing_infeasible', 'unknown', []))
        self.assertEqual(report['owner_confirmation'], ['gap_nm'])
        self.assertIn('gap_nm', report['reason'])
        self.assertEqual(report['proof']['kind'], 'board_capacity_fragmented')
        self.assertEqual(next(b for b in report['binding_constraints'] if b['constraint'] == 'gap_nm')['class'],
                         'unknown')

    def test_sourced_gap_is_value_bound_and_retained_in_the_proof(self):
        source, policy = columns_case(7, capacity_proof=True)
        gap = policy['constraint_provenance']['gap_nm']
        report = pack(source, policy)
        self.assertEqual(report['outcome'], 'defect')
        self.assertIn({'constraint': 'gap_nm', **gap}, report['binding_constraints'])
        for bad in ({**gap, 'value_nm': 1}, {**gap, 'value_nm': True},
                    {**gap, 'citation': ''}, {**gap, 'citation': ' '}, {**gap, 'value_nm': -1},
                    {**gap, 'class': 'assumed'}, {**gap, 'extra': 'x'},
                    {**gap, 'enforcement': 'soft'}, []):
            with self.subTest(gap=bad), self.assertRaisesRegex(ValueError, 'constraint_provenance'):
                pack(source, {**policy, 'constraint_provenance': {'gap_nm': bad}})

    def test_caller_qualified_manufacturer_gap_remains_hard(self):
        # Exercise the geometry API's class handling with an original synthetic declaration.
        # This test does not claim to capture or authenticate a JLCPCB assembly record.
        source, policy = columns_case(7, capacity_proof=True)
        gap = {**policy['constraint_provenance']['gap_nm'], 'class': 'manufacturer',
               'citation': 'synthetic caller-qualified manufacturer requirement: gap 0.25 mm'}
        report = pack(source, {**policy, 'constraint_provenance': {**policy['constraint_provenance'], 'gap_nm': gap}})
        self.assertEqual((report['status'], report['outcome']), ('packing_infeasible', 'defect'))
        self.assertIn({'constraint': 'gap_nm', **gap}, report['binding_constraints'])
        self.assertNotIn('owner_confirmation', report)

    def test_unsourced_gap_does_not_weaken_a_zero_gap_capacity_defect(self):
        # Area alone fits, but the authored edge margin leaves a strip too narrow for a square.
        source = capture([part('C1', 5, 5)], rectangle(0, 0, 4, 20))
        policy = request(fixed_refs=[], gap_nm=250000, min_courtyard_edge_mm=1.5, capacity_proof=True)
        report = pack(source, authored_margin(policy))
        self.assertEqual((report['status'], report['outcome'], report['proof']['gap_nm']),
                         ('packing_infeasible', 'defect', 0))
        self.assertNotIn('gap_nm', [b['constraint'] for b in report['binding_constraints']])

    def test_unsourced_edge_margin_cannot_establish_a_capacity_defect(self):
        source = capture([part('C1', 5, 5)], rectangle(0, 0, 4, 20))
        policy = request(fixed_refs=[], gap_nm=0, min_courtyard_edge_mm=1.5, capacity_proof=True)
        report = pack(source, policy)
        self.assertEqual((report['status'], report['outcome']), ('packing_infeasible', 'unknown'))
        self.assertEqual(report['owner_confirmation'], ['min_courtyard_edge_mm'])
        self.assertIn('min_courtyard_edge_mm', report['reason'])
        self.assertEqual(report['binding_constraints'][-1]['class'], 'unknown')
        self.assertEqual(report['binding_constraints'][-1]['enforcement'], 'soft')
        self.assertEqual(pack(source, {**policy, 'min_courtyard_edge_mm': 0})['status'], 'planned')

    def test_unsourced_margin_and_gap_are_both_named(self):
        source, policy = columns_case(7, capacity_proof=True)
        policy.pop('constraint_provenance')
        report = pack(source, policy)
        self.assertEqual(report['outcome'], 'unknown')
        self.assertEqual(report['owner_confirmation'], ['min_courtyard_edge_mm', 'gap_nm'])

    def test_structural_capacity_defect_survives_both_advisory_limits(self):
        source, policy = columns_case(10, capacity_proof=True)
        policy.pop('constraint_provenance')
        original = copy.deepcopy(policy)
        report = pack(source, policy)
        self.assertEqual((report['status'], report['outcome'], report['proof']['gap_nm']),
                         ('packing_infeasible', 'defect', 0))
        self.assertEqual(report['proof']['kind'], 'board_capacity_fragmented')
        self.assertNotIn('owner_confirmation', report)
        self.assertTrue({'gap_nm', 'min_courtyard_edge_mm'}.isdisjoint(
            b['constraint'] for b in report['binding_constraints']))
        self.assertEqual((report['capacity']['gap_nm'], report['capacity']['min_courtyard_edge_nm']),
                         (250000, 500000))
        self.assertEqual(policy, original)

    def test_zero_gap_retry_names_only_the_remaining_unsourced_margin(self):
        source, policy = columns_case(8, capacity_proof=True)
        policy.pop('constraint_provenance')
        report = pack(source, policy)
        self.assertEqual((report['outcome'], report['proof']['gap_nm']), ('unknown', 0))
        self.assertEqual(report['owner_confirmation'], ['min_courtyard_edge_mm'])
        self.assertNotIn('gap_nm', [b['constraint'] for b in report['binding_constraints']])

    def test_margin_independent_capacity_defect_retries_without_unsourced_margin(self):
        source = board([part(f'C{i}', 0, 0) for i in range(16)], rectangle(0, 0, 10, 10))
        policy = authored_gap(request(fixed_refs=[], gap_nm=1000000,
                                      min_courtyard_edge_mm=0.01, capacity_proof=True),
                              'original fixture: 1 mm gap')
        report = pack(source, policy)
        self.assertEqual((report['status'], report['outcome']), ('packing_infeasible', 'defect'))
        self.assertNotIn('min_courtyard_edge_mm', [b['constraint'] for b in report['binding_constraints']])
        self.assertEqual(report['capacity']['min_courtyard_edge_nm'], 10000)

    def test_edge_margin_source_is_value_bound_and_validated(self):
        source = capture([part('C1', 5, 5)], rectangle(0, 0, 4, 20))
        policy = request(fixed_refs=[], gap_nm=0, min_courtyard_edge_mm=1.5, capacity_proof=True)
        source_entry = {'class': 'fixture_author', 'enforcement': 'hard', 'value_nm': 1500000,
                        'citation': 'original test fixture: authored 1.5 mm courtyard edge margin'}
        for cls in ('fixture_author', 'manufacturer'):
            entry = {**source_entry, 'class': cls}
            report = pack(source, {**policy, 'constraint_provenance': {'min_courtyard_edge_mm': entry}})
            self.assertEqual(report['outcome'], 'defect')
            self.assertIn({'constraint': 'min_courtyard_edge_mm', **entry}, report['binding_constraints'])
        for field, value in (('value_nm', 1), ('value_nm', True), ('value_nm', -1),
                             ('citation', ''), ('citation', ' '), ('class', 'owner_stated'),
                             ('enforcement', 'soft'), ('extra', 'x')):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, 'constraint_provenance'):
                pack(source, {**policy, 'constraint_provenance': {
                    'min_courtyard_edge_mm': {**source_entry, field: value}}})

    def test_opted_out_the_same_input_stays_unknown_with_the_diagnostic(self):
        source, policy = columns_case(7, capacity_proof=False)
        report = pack(source, policy)
        self.assertIn(report['status'], ('packing_search_exhausted', 'packing_budget_exhausted'))
        self.assertEqual((report['outcome'], report['placements']), ('unknown', []))
        self.assertNotIn('proof', report)
        self.assertNotIn('search_failure', report)
        row, = report['capacity']['sides']['F']['fragmentation']
        self.assertGreater(rational(row['ratio']), 1)  # Reported, never a verdict when opted out.

    def test_by_default_the_proof_replaces_the_failed_search(self):
        source, policy = columns_case(7)
        opted_out = pack(source, {**policy, 'capacity_proof': False})
        report = pack(source, policy)
        # Unsourced margin and gap: the same proof, but unknown, naming both for confirmation.
        self.assertEqual((report['status'], report['outcome'], report['placements']),
                         ('packing_infeasible', 'unknown', []))
        self.assertEqual(report['proof']['kind'], 'board_capacity_fragmented')
        self.assertEqual(report['owner_confirmation'], ['min_courtyard_edge_mm', 'gap_nm'])
        # The failed legacy search ran first and stays as the diagnostic record.
        self.assertEqual(report['search_failure'], {key: opted_out[key] for key in ('status', 'outcome', 'reason')})
        self.assertEqual((report['search'], report['steps_used'], report['capacity']),
                         (opted_out['search'], opted_out['steps_used'], opted_out['capacity']))
        # Sourced limits: a defect, with the same proof and binding constraints as the explicit opt-in.
        source, policy = columns_case(7, capacity_proof=True)
        explicit = pack(source, policy)
        default = pack(source, {key: value for key, value in policy.items() if key != 'capacity_proof'})
        self.assertEqual((default['status'], default['outcome']), ('packing_infeasible', 'defect'))
        for key in ('proof', 'binding_constraints', 'capacity', 'reason'):
            self.assertEqual(default[key], explicit[key], key)
        self.assertNotIn('owner_confirmation', default)
        self.assertEqual((explicit['steps_used'], default['search_failure']['outcome']), (0, 'unknown'))
        self.assertGreater(default['steps_used'], 0)

    def test_default_proof_never_replaces_a_plan(self):
        # Legacy first: even an (artificially) exceeded row cannot displace a plan the search found.
        source, policy = columns_case(6)
        plan = pack(source, policy)
        self.assertEqual(plan['status'], 'planned')
        fake = {'kind': 'board_capacity_gap', 'side': 'F', 'gap_nm': 0, 'blocked_by': [], 'square_nm': 0,
                'references': ['C1'], 'required_grown_courtyard_area_nm2': [1, 1], 'available_area_upper_nm2': [0, 1]}
        original = placer._capacity_impossibility
        placer._capacity_impossibility = lambda sides, policy: fake
        try:
            self.assertEqual(pack(source, policy), plan)
            self.assertEqual(pack(source, dict(policy, capacity_proof=True))['status'], 'packing_infeasible')
        finally:
            placer._capacity_impossibility = original

    def test_default_proof_never_replaces_a_judge_rejection(self):
        # Coordinator ruling: a judge rejection is not search exhaustion, so even an
        # (artificially) exceeded row leaves its status and reason unchanged.
        source, policy = columns_case(6)
        fake = {'kind': 'board_capacity_gap', 'side': 'F', 'gap_nm': 0, 'blocked_by': [], 'square_nm': 0,
                'references': ['C1'], 'required_grown_courtyard_area_nm2': [1, 1], 'available_area_upper_nm2': [0, 1]}
        original = placer._capacity_impossibility, placer.judge
        placer.judge = lambda capture, overhangs=None, mechanical=None: {'verdict': 'defect'}
        try:
            rejected = pack(source, dict(policy, capacity_proof=False))
            self.assertEqual(rejected['status'], 'judge_rejected')
            placer._capacity_impossibility = lambda sides, policy: fake
            report = pack(source, policy)
            self.assertEqual(pack(source, dict(policy, capacity_proof=True))['status'], 'packing_infeasible')
        finally:
            placer._capacity_impossibility, placer.judge = original
        self.assertEqual((report['status'], report['outcome'], report['reason']),
                         ('judge_rejected', 'unknown', 'independent full-polygon judge rejected complete proposal'))
        self.assertNotIn('proof', report)
        self.assertNotIn('search_failure', report)
        for key in set(report) | set(rejected):
            if key != 'policy':
                self.assertEqual(report.get(key), rejected.get(key), key)

    def test_squares_grown_by_half_the_gap_exceed_the_free_area(self):
        # Sixteen 2 mm squares (64 mm2) on a 10 x 10 mm board pass the plain area proof;
        # grown by 0.5 mm in a container shrunk by 0.5 - 0.5 = 0 mm they need 140 mm2.
        parts = [part(f'C{i}', 0, 0) for i in range(16)]
        policy = authored_gap(request(fixed_refs=[], gap_nm=1_000_000, min_courtyard_edge_mm=0.5,
                                      capacity_proof=True),
                              'tests/test_placer_capacity.py:test_squares_grown_by_half_the_gap_exceed_the_free_area')
        policy = authored_margin(policy)
        report = pack(board(parts, rectangle(0, 0, 10, 10)), policy)
        self.assertEqual((report['status'], report['outcome']), ('packing_infeasible', 'defect'))
        self.assertEqual(report['proof']['kind'], 'board_capacity_gap')
        self.assertEqual(rational(report['proof']['available_area_upper_nm2']), 100 * MM2)
        self.assertEqual(rational(report['proof']['required_grown_courtyard_area_nm2']), 16 * grown_square(2, 0.5))
        # Nine fit (three rows of three, 2 + 1 + 2 + 1 + 2 = 8 mm inside the 9 mm margin box): no proof.
        nine = pack(board(parts[:9], rectangle(0, 0, 10, 10)), dict(policy, step_budget=100000))
        self.assertEqual(nine['status'], 'planned', nine.get('reason'))

    def test_former_search_unknowns_are_proved_after_the_search(self):
        # Truly infeasible inputs that were search failures before the default (AGENTEE-249).
        tight = (capture([part('C1', 5, 5)], rectangle(0, 0, 4, 20)), request(fixed_refs=[], min_courtyard_edge_mm=1.5))
        shape = polygon([(0, 0), (8, 0), (8, 5), (5, 5), (5, 8), (0, 8)])
        notch = (capture([part('U1', 0, 0, shape=shape), part('C1', 1, 1)], rectangle(0, 0, 8, 8)),
                 request(gap_nm=1_500_000))
        for source, policy in (tight, notch):
            self.assertEqual(pack(source, dict(policy, capacity_proof=False))['status'], 'packing_search_exhausted')
            sourced = authored_gap(authored_margin(policy),
                                   'tests/test_placer_capacity.py:test_former_search_unknowns_are_proved_after_the_search')
            for proved in (pack(source, sourced), pack(source, dict(sourced, capacity_proof=True))):
                self.assertEqual((proved['status'], proved['outcome'], proved['proof']['kind']),
                                 ('packing_infeasible', 'defect', 'board_capacity_fragmented'))

    def test_planned_fixtures_are_unchanged_by_the_opt_in(self):
        import test_placer_crowded
        for source, policy in (test_placer_crowded.notch_case(), test_placer_crowded.corner_case()):
            plan = pack(source, policy)
            self.assertEqual(plan['status'], 'planned')
            self.assertEqual(pack(source, dict(policy, capacity_proof=True))['placements'], plan['placements'])
            for side in plan['capacity']['sides'].values():
                for row in [side['free_area']] + side['fragmentation']:
                    self.assertLessEqual(rational(row['required_grown_courtyard_area_nm2']),
                                         rational(row['available_area_upper_nm2']))

    def test_capacity_proof_must_be_a_boolean(self):
        source, policy = columns_case(1)
        for bad in (1, 'yes', None):
            with self.assertRaises(ValueError):
                pack(source, dict(policy, capacity_proof=bad))


def separated(corners, rect):
    """A convex quadrilateral and a box share no interior point, with a float margin (separating axes)."""
    x0, y0, x1, y1 = rect
    box = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    axes = [(1, 0), (0, 1)] + [(corners[i][1] - corners[i - 1][1], corners[i - 1][0] - corners[i][0]) for i in (1, 2)]
    for ax, ay in axes:
        p = [ax * x + ay * y for x, y in corners]
        q = [ax * x + ay * y for x, y in box]
        if max(p) <= min(q) - 1e-9 or max(q) <= min(p) - 1e-9:
            return True
    return False


class SquareCoverTest(unittest.TestCase):
    def test_square_cover_matches_a_brute_force_on_integer_grids(self):
        rng = random.Random(249)
        for _ in range(150):
            w, h = rng.randint(1, 9), rng.randint(1, 9)
            blockers = []
            for _ in range(rng.randint(0, 4)):
                x0, y0 = rng.randint(-2, w + 1), rng.randint(-2, h + 1)
                blockers.append((x0, y0, x0 + rng.randint(1, 5), y0 + rng.randint(1, 5)))
            a = rng.randint(0, 5)
            covered = set()
            for cx in range(0, w - a + 1):
                for cy in range(0, h - a + 1):
                    if a and all(cx >= x1 or cx + a <= x0 or cy >= y1 or cy + a <= y0 for x0, y0, x1, y1 in blockers):
                        covered |= {(i, j) for i in range(cx, cx + a) for j in range(cy, cy + a)}
            if a == 0:
                covered = {(i, j) for i in range(w) for j in range(h)
                           if all(not (x0 <= i < x1 and y0 <= j < y1) for x0, y0, x1, y1 in blockers)}
            self.assertEqual(placer._square_cover_area((0, 0, w, h), blockers, a), len(covered),
                             (w, h, blockers, a))

    def test_rotated_squares_lie_in_the_probe_cover(self):
        # The geometric step of the fragmentation bound, sampled: a square of side a at any
        # angle inside the free space covers only points the reach boxes hold, clear of blockers.
        rng = random.Random(2490)
        checked = 0
        for _ in range(60):
            w, h, a = rng.randint(8, 30), rng.randint(8, 30), 2 * rng.randint(2, 5)
            blockers = []
            for _ in range(rng.randint(0, 4)):
                x0, y0 = rng.randint(-2, w), rng.randint(-2, h)
                blockers.append((x0, y0, x0 + rng.randint(1, 8), y0 + rng.randint(1, 8)))
            root = math.isqrt(2 * a * a)
            reach, solid = placer._probe_cover((0, 0, w, h), blockers, a // 2, root + 1)
            centres, _ = placer._probe_cover((0, 0, w, h), blockers, a // 2, 0)  # Reach 0: the admissible centres.
            for _ in range(200):
                theta = rng.uniform(0, math.pi / 2)
                c, s = math.cos(theta) * a / 2, math.sin(theta) * a / 2
                cx, cy = rng.uniform(0, w), rng.uniform(0, h)
                corners = [(cx + c * u - s * v, cy + s * u + c * v) for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
                if not all(1e-6 < x < w - 1e-6 and 1e-6 < y < h - 1e-6 for x, y in corners):
                    continue
                if any(not separated(corners, b) for b in blockers):
                    continue
                checked += 1
                self.assertTrue(any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in centres), (cx, cy))
                for _ in range(20):
                    u, v = rng.uniform(-0.999, 0.999), rng.uniform(-0.999, 0.999)
                    x, y = cx + c * u - s * v, cy + s * u + c * v
                    self.assertTrue(any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in reach), (x, y))
                    self.assertFalse(any(x0 < x < x1 and y0 < y < y1 for x0, y0, x1, y1 in solid), (x, y))
        self.assertGreater(checked, 500)
        # One admissible centre, reaching 3 along each axis and clipped to the box; a blocker stays solid.
        self.assertEqual(placer._probe_cover((0, 0, 10, 10), [], 5, 3), ([(2, 2, 8, 8)], []))
        reach, solid = placer._probe_cover((0, 0, 10, 10), [(9, 9, 12, 12)], 4, 7)
        self.assertEqual((set(reach), solid), ({(0, 0, 10, 10)}, [(9, 9, 10, 10)]))

    def test_square_size_of_courtyards(self):
        def size(points):
            return placer._square_size([tuple(p) for p in polygon(points)])
        self.assertEqual(size([(0, 0), (5, 0), (5, 3), (0, 3)]), 3 * MM)
        # An L with a 2 mm arm and a 3 mm arm: the thinner arm decides.
        self.assertEqual(size([(0, 0), (8, 0), (8, 2), (3, 2), (3, 8), (0, 8)]), 2 * MM)
        # Two 4 mm squares joined by a 1 mm neck.
        dumbbell = [(0, 0), (4, 0), (4, 1.5), (6, 1.5), (6, 0), (10, 0), (10, 4), (6, 4), (6, 2.5), (4, 2.5),
                    (4, 4), (0, 4)]
        self.assertEqual(size(dumbbell), MM)
        self.assertEqual(size([(0, 0), (4, 0), (0, 3)]), 0)  # Not rectilinear: no square claim.


if __name__ == '__main__':
    unittest.main()

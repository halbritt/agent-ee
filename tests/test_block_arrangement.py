"""Stage-B block arrangement and residual packing (AGENTEE-340).

Every fixture is original: the authored stage-A sheets, parts, pads, nets and limits.
"""
import copy
import json
import random
import subprocess
import sys
import unittest

from agent_ee import block_arrangement as arrangement, blocks, placer
from test_blocks import Fixture, PARTS, ROOT, SHEETS, board, native_xml, policy
from test_placement import MM, part, rectangle

CITE = 'Owned fixture rule EE340: authored test requirement.'


def plan_policy(**changes):
    value = {'schema': arrangement.POLICY, 'step_budget': 20000}
    value.update(changes)
    return {k: v for k, v in value.items() if v is not None}


class ArrangementFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.inv = self.inventory()[0]
        self.derivation = self.derive()
        self.policy = policy()
        self.entries = [{'candidate': blocks.pack_block(self.board, self.inv, self.derivation, b['id'],
                                                         self.policy)['candidate']}
                        for b in self.derivation['blocks']]

    def arrange(self, rules=None, entries=None, capture=None, pol=None, derivation=None, inv=None, **kwargs):
        return arrangement.arrange(capture or self.board, inv or self.inv, derivation or self.derivation,
                                   pol or self.policy, rules or plan_policy(),
                                   self.entries if entries is None else entries, **kwargs)

    def placements(self, plan):
        return {p['ref']: p for p in plan['placements']}


def placer_obstacles(plan, case):
    """The residual packing obstacles, recomputed as the arranged plan declares them."""
    record = plan['block_arrangement']
    case.assertEqual(len(record['residual']['policy_sha256']), 64)
    return record['residual']['obstacles']


class ArrangementTests(ArrangementFixture, unittest.TestCase):
    def test_blocks_stay_rigid_and_residual_is_packed(self):
        plan = self.arrange()
        self.assertEqual((plan['status'], plan['outcome']), ('planned', 'info'), plan.get('reason'))
        record = plan['block_arrangement']
        poses = self.placements(plan)
        self.assertEqual(sorted(poses), sorted(fp['ref'] for fp in self.board['footprints']))
        self.assertEqual(poses['H1']['position_nm'], [40 * MM, 30 * MM])
        self.assertEqual({r for r, p in poses.items() if p['fixed']}, {'H1'})
        # Every member keeps its frame pose relative to the arranged anchor.
        for entry, arranged in zip(self.entries, sorted(record['arranged'], key=lambda a: a['block'])):
            cand = entry['candidate']
            self.assertEqual(cand['block_id'], arranged['block'])
            turns = arranged['rotation_deg'] // 90
            for row in cand['members']:
                dx, dy = placer.rotate(row['pose']['position_nm'], turns)
                self.assertEqual(poses[row['reference']]['position_nm'],
                                 [arranged['position_nm'][0] + dx, arranged['position_nm'][1] + dy])
                self.assertEqual(poses[row['reference']]['rotation_deg'],
                                 (row['pose']['rotation_deg'] + arranged['rotation_deg']) % 360)
        self.assertEqual(plan['judgment']['verdict'], 'pass')
        self.assertEqual(plan['locality_grading']['status'], 'pass')
        # The boundary obligation crossing Power and Sensor is graded on the complete plan.
        self.assertEqual({(d['block'], d['id'], d['status']) for d in record['deferred_grading']},
                         {('Power', 'cross-C2', 'pass'), ('Sensor', 'cross-C2', 'pass')})
        self.assertEqual(record['residual']['references'], ['J1'])
        self.assertEqual(record['inter_block_nets'], [{'between': ['Power', 'Sensor'], 'nets': 1}])
        self.assertEqual([b['provenance']['class'] for b in record['blocks']], ['search-derived'] * 2)
        power = record['blocks'][0]
        self.assertEqual([(r['role'], r['net'], r['boundary'], r['outside']) for r in power['boundary']],
                         [('n1', 'VIN', True, ['J1']), ('n2', 'GND', True, ['C2', 'J1', 'R1', 'U2']),
                          ('n3', 'SW', False, []), ('n4', 'VOUT', True, ['C2', 'U2'])])
        self.assertEqual(plan['steps_used'], record['search']['steps_used'] + record['residual']['steps_used'])
        self.assertEqual(plan['policy'], self.policy)

    def test_deterministic_and_input_preserving(self):
        board, entries = copy.deepcopy(self.board), copy.deepcopy(self.entries)
        first = self.arrange()
        second = self.arrange()
        for plan in (first, second):
            plan['block_arrangement']['search'].pop('elapsed_s')
        self.assertEqual(first, second)
        self.assertEqual((self.board, self.entries), (board, entries))

    def test_admission_refusals(self):
        def problems(result):
            self.assertEqual((result['status'], result['outcome'], result['placements']),
                             ('arrangement_refused', 'unknown', []))
            return result['block_arrangement']['problems']
        self.assertEqual(problems(self.arrange(entries=self.entries[:1])),
                         [{'kind': 'block_candidate_missing', 'blocks': ['Sensor']}])
        found = problems(self.arrange(entries=self.entries + self.entries[:1]))
        self.assertEqual(found, [{'candidate': 2, 'block': 'Power', 'problems': [{'kind': 'duplicate_block'}]}])
        # A candidate packed under another policy is refused by stage-A admission.
        other = blocks.pack_block(self.board, self.inv, self.derivation, 'Power', policy(gap_nm=300000))['candidate']
        found = problems(self.arrange(entries=[{'candidate': other}] + self.entries[1:]))
        self.assertEqual({p['kind'] for p in found[0]['problems']}, {'source_mismatch'})
        # A boundary role whose recorded net is not the board net it lands on (review gap).
        doc = copy.deepcopy(self.entries[0]['candidate'])
        for row in doc['members']:
            for pin in row['pins']:
                if pin.get('net') == 'VIN':
                    pin['net'] = 'VIN_OTHER'
        self.assertEqual(blocks.admit(doc, self.board, self.inv, self.derivation, self.policy)['status'], 'admitted')
        found = problems(self.arrange(entries=[{'candidate': doc}] + self.entries[1:]))
        self.assertEqual(found[0]['problems'], [{'kind': 'boundary_net_mismatch', 'role': 'n1',
                                                 'expected': 'VIN_OTHER', 'actual': 'VIN'}])
        with self.assertRaisesRegex(ValueError, 'arrangement candidate needs candidate'):
            self.arrange(entries=[{'candidate': doc, 'extra': 1}])

    def test_library_candidate_maps_with_declared_boundary_nets(self):
        extra = (('U11', '/s4/', 'BUCK', 'Owned:QFN4', 'owned-buck', {'1': 'VIN', '2': 'GND', '3': 'SW2', '4': 'V2'}),
                 ('C11', '/s4/', 'C', 'Owned:C0805', '4.7uF', {'1': 'VIN', '2': 'GND'}),
                 ('L11', '/s4/', 'L', 'Owned:L1210', '10uH', {'1': 'SW2', '2': 'V2'}),
                 ('C13', '/s4/', 'C', 'Owned:C0805', '22uF', {'1': 'V2', '2': 'GND'}),
                 ('R9', '/', 'R', 'Owned:R0603', '1k', {'1': 'V2', '2': 'GND'}))
        parts = PARTS + extra
        target = board(parts)
        target['outline']['polygons'] = [rectangle(0, 0, 60, 40)]
        inv, names, sha = self.inventory(native_xml(parts, sheets={**SHEETS, '/s4/': '/Power2/'}))
        derivation = blocks.derive(inv, names, target, inventory_sha256=sha)
        pol = policy()
        own = [{'candidate': blocks.pack_block(target, inv, derivation, b, pol)['candidate']}
               for b in ('Power', 'Sensor')]
        mapping = {'U1': 'U11', 'C1': 'C11', 'L1': 'L11', 'C3': 'C13'}
        library = {'candidate': self.entries[0]['candidate'], 'mapping': mapping}

        def run(entry):
            return self.arrange(entries=own + [entry], capture=target, pol=pol, derivation=derivation, inv=inv)
        refused = run(library)
        self.assertEqual(refused['status'], 'arrangement_refused')
        self.assertEqual({p['kind'] for p in refused['block_arrangement']['problems'][0]['problems']},
                         {'boundary_undeclared'})
        wrong = run({**library, 'boundary_nets': {'n1': 'VIN', 'n2': 'GND', 'n4': 'VOUT'}})
        self.assertEqual(wrong['block_arrangement']['problems'][0]['problems'],
                         [{'kind': 'boundary_net_mismatch', 'role': 'n4', 'expected': 'VOUT', 'actual': 'V2'}])
        plan = run({**library, 'boundary_nets': {'n1': 'VIN', 'n2': 'GND', 'n4': 'V2'}})
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        record = {b['block']: b for b in plan['block_arrangement']['blocks']}
        self.assertEqual(record['Power2']['references'], ['C11', 'C13', 'L11', 'U11'])
        self.assertEqual(record['Power2']['candidate_sha256'], blocks.digest(self.entries[0]['candidate']))
        self.assertEqual(plan['block_arrangement']['residual']['references'], ['J1', 'R9'])
        # A mapping onto members that are not one derived block refuses.
        bad = run({**library, 'mapping': {**mapping, 'C3': 'R9'}, 'boundary_nets': {}})
        self.assertEqual(bad['status'], 'arrangement_refused')

    def test_hard_keepout_moves_blocks_and_advisory_is_reported_only(self):
        plain = self.arrange()
        box = [x for p in self.placements(plain).values() if not p['fixed'] for x in [p['position_nm']]]
        x0 = min(x for x, _ in box) // MM - 1
        y0 = min(y for _, y in box) // MM - 1
        keepout = {'id': 'iso', 'side': 'F', 'polygons': [rectangle(x0, y0, 14, 14)],
                   'provenance': {'enforcement': 'hard', 'citation': CITE}}
        hard = self.arrange(plan_policy(keepouts=[keepout]))
        self.assertEqual(hard['status'], 'planned', hard.get('reason'))
        self.assertEqual(hard['block_arrangement']['rules'],
                         [{'rule': 'keepout', 'enforcement': 'hard', 'status': 'pass', 'findings': [], 'id': 'iso'}])
        advisory = dict(keepout, provenance={'enforcement': 'advisory', 'reason': 'unsourced isolation'})
        soft = self.arrange(plan_policy(keepouts=[advisory]))
        self.assertEqual(soft['status'], 'planned')
        self.assertEqual(self.placements(soft), self.placements(plain))
        row = soft['block_arrangement']['rules'][0]
        self.assertEqual((row['enforcement'], row['status']), ('advisory', 'defect'))
        self.assertTrue(row['findings'])
        with self.assertRaisesRegex(ValueError, 'hard rule needs a citation'):
            self.arrange(plan_policy(keepouts=[dict(keepout, provenance={'enforcement': 'hard'})]))

    def test_thermal_spread_limit_and_objective(self):
        thermal = {'sources': ['U1', 'U2'], 'min_distance_nm': 8 * MM,
                   'provenance': {'enforcement': 'hard', 'citation': CITE}}
        plan = self.arrange(plan_policy(thermal=thermal))
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        row = plan['block_arrangement']['rules'][0]
        self.assertEqual((row['rule'], row['status']), ('thermal_spread', 'pass'))
        self.assertGreaterEqual(row['min_distance_mm'], 8)
        # An unsourced limit is reported, never enforced.
        soft = self.arrange(plan_policy(thermal=dict(thermal, provenance={'enforcement': 'advisory'})))
        row = soft['block_arrangement']['rules'][0]
        self.assertEqual((row['enforcement'], row['status']), ('advisory', 'defect'))
        self.assertLess(row['min_distance_mm'], 8)
        # Thermal spread first in the objective separates the sources without a limit.
        spread = self.arrange(plan_policy(thermal=dict(thermal, provenance={'enforcement': 'advisory'}),
                                          objective={'terms': ['thermal-spread', 'pad-locality'],
                                                     'provenance': {'enforcement': 'advisory'}}))
        self.assertGreater(spread['block_arrangement']['rules'][0]['min_distance_mm'], row['min_distance_mm'])
        # A sourced limit that cannot hold with the 6 mm cross-block obligation is a typed
        # unknown, not a defect: Sensor's conflicts jump back over every Power candidate.
        far = self.arrange(plan_policy(thermal=dict(thermal, min_distance_nm=10 * MM)))
        self.assertEqual((far['status'], far['outcome'], far['placements']),
                         ('arrangement_search_exhausted', 'unknown', []))
        search = far['block_arrangement']['search']
        self.assertEqual((search['unplaced'], search['placed_peak']), ('Power', 1))
        self.assertGreater(search['backjumps'], 0)
        # Backjumps count against the step budget, as in the placer.
        bounded = self.arrange(plan_policy(step_budget=search['backjumps'] // 2,
                                           thermal=dict(thermal, min_distance_nm=10 * MM)))
        self.assertEqual((bounded['status'], bounded['outcome']), ('arrangement_budget_exhausted', 'unknown'))
        self.assertIn(bounded['reason'], ('deterministic backjump budget exhausted',
                                          'deterministic arrangement candidate budget exhausted'))

    def test_shadows_and_fixed_parts_across_the_board(self):
        # J1 is a residual part on the back; L1 casts a hard shadow through the board.
        back = copy.deepcopy(self.board)
        j1 = next(f for f in back['footprints'] if f['ref'] == 'J1')
        j1['side'] = 'B'
        j1['courtyards'] = {'B': j1['courtyards']['F']}
        for pad in j1['pads']:
            pad['layers'] = ['B.Cu']
        rule = {'references': ['L1'], 'margin_nm': 20 * MM, 'provenance': {'enforcement': 'hard', 'citation': CITE}}
        plan = self.arrange(plan_policy(no_stacking=rule), capture=back)
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        row = plan['block_arrangement']['rules'][0]
        self.assertEqual((row['rule'], row['status']), ('no_stacking', 'pass'))
        self.assertIn('block-arrangement shadow L1', [o.get('source') for o in placer_obstacles(plan, self)])
        soft = self.arrange(plan_policy(no_stacking=dict(rule, provenance={'enforcement': 'advisory'})), capture=back)
        self.assertEqual(soft['block_arrangement']['rules'][0]['findings'], [['L1', 'J1']])
        # AGENTEE-355: a probe keeps native winding, so a back courtyard may be clockwise.
        # Grading the complete plan once raised "polygon cannot be triangulated" here.
        clockwise = copy.deepcopy(back)
        next(f for f in clockwise['footprints'] if f['ref'] == 'J1')['courtyards']['B']['polygons'][0].reverse()
        turned = self.arrange(plan_policy(no_stacking=dict(rule, provenance={'enforcement': 'advisory'})),
                              capture=clockwise)
        self.assertEqual(turned['block_arrangement']['rules'][0]['findings'], [['L1', 'J1']])
        # A fixed back part: no block lands over it unless exempt.
        fixed = copy.deepcopy(back)
        j1 = next(f for f in fixed['footprints'] if f['ref'] == 'J1')
        dx, dy = 1 * MM - j1['position_nm'][0], 3 * MM - j1['position_nm'][1]
        j1.update(position_nm=[1 * MM, 3 * MM], courtyards={'B': {'status': 'proven', 'polygons': [rectangle(1, 3, 2, 2)]}})
        for pad in j1['pads']:
            pad['position_nm'] = [pad['position_nm'][0] + dx, pad['position_nm'][1] + dy]
        fixed_policy = policy(fixed_refs=['H1', 'J1'])
        entries = [{'candidate': blocks.pack_block(fixed, self.inv, self.derive(capture=fixed), b, fixed_policy)
                    ['candidate']} for b in ('Power', 'Sensor')]
        over = {'exempt_blocks': [], 'provenance': {'enforcement': 'hard', 'citation': CITE}}
        free = self.arrange(entries=entries, capture=fixed, pol=fixed_policy, derivation=self.derive(capture=fixed))
        self.assertEqual(free['status'], 'planned')
        rules = plan_policy(no_block_over_fixed=over)
        kept = self.arrange(rules, entries=entries, capture=fixed, pol=fixed_policy,
                            derivation=self.derive(capture=fixed))
        self.assertEqual(kept['status'], 'planned', kept.get('reason'))
        self.assertEqual(kept['block_arrangement']['rules'][0]['status'], 'pass')
        graded = arrangement.grade_rules({**fixed, 'footprints': [
            placer.transform(fp, self.placements(free)[fp['ref']]['position_nm'],
                             self.placements(free)[fp['ref']]['rotation_deg']) for fp in fixed['footprints']]},
            rules, {r: 'Power' for r in ('U1', 'C1', 'L1', 'C3')} | {r: 'Sensor' for r in ('U2', 'C2', 'R1')},
            {'H1', 'J1'})
        self.assertEqual(graded[0]['status'], 'defect')
        self.assertTrue(all(f[2] == 'J1' for f in graded[0]['findings']))

    def test_internal_stacking_and_stranded_groups_refuse(self):
        back = copy.deepcopy(self.board)
        c1 = next(f for f in back['footprints'] if f['ref'] == 'C1')
        c1.update(side='B', courtyards={'B': c1['courtyards']['F']})
        for pad in c1['pads']:
            pad['layers'] = ['B.Cu']
        derivation = self.derive(capture=back)
        rule = {'references': ['U1'], 'margin_nm': 30 * MM, 'provenance': {'enforcement': 'hard', 'citation': CITE}}
        entries = [{'candidate': blocks.pack_block(back, self.inv, derivation, b, self.policy)['candidate']}
                   for b in ('Power', 'Sensor')]
        result = self.arrange(plan_policy(no_stacking=rule), entries=entries, capture=back, derivation=derivation)
        self.assertEqual(result['status'], 'arrangement_refused')
        self.assertEqual(result['block_arrangement']['problems'][0]['kind'], 'block_internal_stacking')
        grouped = policy(groups=[{'id': 'near-j1', 'references': ['C2'], 'anchor': {'reference': 'J1'},
                                  'max_distance_nm': 9 * MM}])
        entries = [{'candidate': blocks.pack_block(self.board, self.inv, self.derivation, b, grouped)['candidate']}
                   for b in ('Power', 'Sensor')]
        result = self.arrange(entries=entries, pol=grouped)
        self.assertEqual(result['block_arrangement']['problems'], [{'kind': 'group_anchor_residual',
                                                                    'groups': ['near-j1']}])

    def test_typed_exhaustion_and_policy_validation(self):
        result = self.arrange(plan_policy(step_budget=1))
        self.assertEqual((result['status'], result['outcome']), ('arrangement_budget_exhausted', 'unknown'))
        self.assertEqual(result['block_arrangement']['search']['partial']['authority'],
                         'diagnostic-only-unvalidated-partial-arrangement')
        small = copy.deepcopy(self.board)
        small['outline']['polygons'] = [rectangle(0, 0, 8, 8)]
        next(f for f in small['footprints'] if f['ref'] == 'H1').update(
            position_nm=[5 * MM, 5 * MM], courtyards={'F': {'status': 'proven', 'polygons': [rectangle(5, 5, 2, 2)]}})
        result = self.arrange(capture=small, derivation=self.derive(capture=small), entries=[
            {'candidate': blocks.pack_block(small, self.inv, self.derive(capture=small), b, self.policy)['candidate']}
            for b in ('Power', 'Sensor')])
        self.assertEqual((result['status'], result['outcome'], result['placements']),
                         ('arrangement_search_exhausted', 'unknown', []))
        self.assertIn('infeasibility unproved', result['reason'])
        cases = ((plan_policy(schema='x'), 'expected agent-ee.block-arrangement/1'),
                 (plan_policy(step_budget=0), 'step budget'),
                 (plan_policy(extra=1), 'unknown extra'),
                 (plan_policy(priority_blocks=['Nope']), 'priority blocks'),
                 (plan_policy(objective={'terms': ['area'], 'provenance': {'enforcement': 'advisory'}}),
                  'objective terms'),
                 (plan_policy(thermal={'sources': ['U1'], 'provenance': {'enforcement': 'advisory'}}),
                  'at least two sources'),
                 (plan_policy(thermal={'sources': ['U1', 'Q9'], 'provenance': {'enforcement': 'advisory'}}),
                  'unknown references Q9'),
                 (plan_policy(no_stacking={'references': ['L1'], 'margin_nm': -1,
                                           'provenance': {'enforcement': 'advisory'}}), 'margin_nm'),
                 (plan_policy(no_block_over_fixed={'exempt_blocks': ['Nope'],
                                                   'provenance': {'enforcement': 'advisory'}}), 'exempt blocks'),
                 (plan_policy(keepouts=[{'id': 'k', 'side': 'X', 'polygons': [rectangle(0, 0, 1, 1)],
                                         'provenance': {'enforcement': 'advisory'}}]), 'keep-out side'),
                 (plan_policy(search_timeout_s=0), 'search_timeout_s'))
        for rules, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.arrange(rules)

    def test_priority_blocks_order(self):
        plan = self.arrange(plan_policy(priority_blocks=['Sensor']))
        self.assertEqual(plan['block_arrangement']['order'], ['Sensor', 'Power'])
        self.assertEqual(self.arrange()['block_arrangement']['order'], ['Power', 'Sensor'])
        # A block with a hard opposite-side shadow constrains both sides, so it goes first.
        shadow = {'references': ['R1'], 'margin_nm': 0, 'provenance': {'enforcement': 'hard', 'citation': CITE}}
        self.assertEqual(self.arrange(plan_policy(no_stacking=shadow))['block_arrangement']['order'],
                         ['Sensor', 'Power'])
        advisory = dict(shadow, provenance={'enforcement': 'advisory'})
        self.assertEqual(self.arrange(plan_policy(no_stacking=advisory))['block_arrangement']['order'],
                         ['Power', 'Sensor'])


class SweepTests(unittest.TestCase):
    def test_single_box_sweep_equals_the_placer_reservation(self):
        import random
        seen = {'doomed': 0, 'conflicts': 0, 'blocked': 0}
        for seed in range(40):
            rng = random.Random(seed)
            def box(size=6):
                x, y = rng.randrange(0, 30), rng.randrange(0, 30)
                return (x * MM // 2, y * MM // 2, (x + rng.randrange(1, size)) * MM // 2,
                        (y + rng.randrange(1, size)) * MM // 2)
            cand = box()
            reserved = [(rng.choice('FB'), box(), f'R{i}') for i in range(rng.randrange(0, 12))]
            solid = [(rng.choice('FB'), box()) for _ in range(rng.randrange(0, 6))]
            xs = sorted({rng.randrange(-5, 25) * MM // 4 for _ in range(25)})
            ys = sorted({rng.randrange(-5, 25) * MM // 4 for _ in range(25)})
            gap = rng.choice((0, 250000, MM))
            mine, theirs = (set(), set()), (set(), set())
            got = list(arrangement._sweep([('F', cand)], [('F', cand)], xs, ys, reserved, solid, gap, mine[0],
                                          mine[1], lambda: None))
            want = list(placer._unreserved({'F': cand}, {'F': [cand]}, xs, ys, reserved, solid, gap, theirs[0],
                                           theirs[1]))
            with self.subTest(seed=seed):
                self.assertEqual(got, want)
                self.assertEqual(mine, theirs)
                # A forbidding rule area blocks exact overlaps only, as a gapless solid.
                forbid = list(arrangement._sweep([('F', cand)], [('F', cand)], xs, ys, [], [], gap, set(), set(),
                                                 lambda: None, solid))
                self.assertEqual(forbid, list(placer._unreserved({'F': cand}, {'F': [cand]}, xs, ys, [], solid, 0,
                                                                 set(), set())))
                # A drilled square overlapping a target slab on either side blocks; its owner conflicts.
                square = box(3)
                owners = set()
                drilled = list(arrangement._sweep([('F', cand)], [], xs, ys, [], [], gap, owners, set(), lambda: None,
                                                  (), [square], [(b, o) for _, b, o in reserved]))
                pseudo = [('F', b, o) for _, b, o in reserved]
                theirs = set()
                self.assertEqual(drilled, list(placer._unreserved({'F': square}, {}, xs, ys, pseudo, [], 0, theirs)))
                self.assertEqual(owners, theirs)
                # A candidate shadow reserves against placed members only, never against placed shadows.
                owners, theirs = set(), set()
                shaded = list(arrangement._sweep([], [], xs, ys, reserved, [], gap, owners, set(), lambda: None,
                                                 shadows=[('B', cand)], shaded=[('B', box(), 'S')]))
                self.assertEqual(shaded, list(placer._unreserved({'B': cand}, {}, xs, ys, reserved, [], gap, theirs)))
                self.assertEqual(owners, theirs)
            seen['doomed'] += len(mine[1])
            seen['conflicts'] += len(mine[0])
            seen['blocked'] += len(xs) * len(ys) - len(got)
        self.assertTrue(all(seen.values()), seen)

    def test_inscribed_square_lies_inside_the_drilled_reservation(self):
        import math
        from fractions import Fraction
        from agent_ee.placement import area2, intersection_area
        for n in (6, 8, 32):
            for r in (150000, 400000):
                poly = [[round(r * math.cos(2 * math.pi * k / n)) + 5 * MM,
                         round(r * math.sin(2 * math.pi * k / n)) - 3 * MM] for k in range(n)]
                x0, y0, x1, y1 = arrangement._inscribed(poly)
                square = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
                self.assertEqual(intersection_area(square, poly), Fraction(abs(area2(square)), 2))
                self.assertGreater(x1 - x0, r)
                self.assertEqual(arrangement._inscribed(poly[::-1]), (x0, y0, x1, y1))
        self.assertIsNone(arrangement._inscribed([[0, 0], [4, 0], [4, 4], [2, 1], [0, 4]]))
        self.assertIsNone(arrangement._inscribed([[0, 0], [2, 0], [2, 2], [0, 2]]))


class MemberAxesTests(unittest.TestCase):
    """Member-aligned axes, the placed drilled-pad prefilter and the ranking cache (AGENTEE-355)."""

    def test_contacts_add_only_free_member_aligned_origins(self):
        seen = {'extra': 0, 'doomed': 0}
        for seed in range(40):
            rng = random.Random(seed)

            def box(size=6):
                x, y = rng.randrange(0, 30), rng.randrange(0, 30)
                return (x * MM // 2, y * MM // 2, (x + rng.randrange(1, size)) * MM // 2,
                        (y + rng.randrange(1, size)) * MM // 2)
            members = [('F', box(4)) for _ in range(rng.randrange(1, 4))]
            reserved = [(rng.choice('FB'), box(), f'R{i}') for i in range(rng.randrange(1, 12))]
            solid = [(rng.choice('FB'), box()) for _ in range(rng.randrange(0, 4))]
            gap = rng.choice((0, 250000, MM))
            entries = [e for side, b, owner in reserved for e in arrangement._gapped(members, [(side, b)], gap, owner)]
            entries += arrangement._solid(members, solid, gap)
            xs = sorted({rng.randrange(-5, 25) * MM // 4 for _ in range(25)})
            ys = sorted({rng.randrange(-5, 25) * MM // 4 for _ in range(25)})
            window = (-2 * MM, 6 * MM)
            plain = list(arrangement._scan(entries, xs, ys, gap, set(), set(), lambda: None))
            doomed = set()
            got = list(arrangement._scan(entries, xs, ys, gap, set(), doomed, lambda: None, contacts=window))
            with self.subTest(seed=seed):
                self.assertEqual([p for p in got if p[1] in ys], plain)
                for x, y in got:
                    live = [e for e in entries if e[0] < x < e[1]]
                    # Every origin is free of every blocked rectangle (open on both axes).
                    self.assertFalse([e for e in live if e[5] is None and e[2] < y < e[3]])
                    near = any(max(0, e[5][0] - x, x - e[5][1]) < gap and e[2] - gap <= y <= e[3] + gap
                               for e in live if e[5] is not None)
                    if (x, y) in doomed:
                        self.assertTrue(near)
                    if y not in ys:
                        # A contact: some member box touches a reserved box or obstacle in y.
                        self.assertTrue(window[0] <= y <= window[1])
                        self.assertTrue(any(y in (e[2], e[3]) for e in live if e[5] is None))
                        seen['extra'] += 1
                seen['doomed'] += len(doomed)
        self.assertTrue(all(seen.values()), seen)

    def test_frontier_members(self):
        boxes = [('F', (0, 0, 2, 2)), ('F', (3, 0, 5, 2)), ('F', (3, 3, 5, 5)), ('B', (1, 0, 4, 2))]
        left, right = arrangement._frontier(boxes)
        self.assertEqual(left, [('F', (0, 0, 2, 2)), ('F', (3, 3, 5, 5)), ('B', (1, 0, 4, 2))])
        self.assertEqual(right, [('F', (3, 0, 5, 2)), ('F', (3, 3, 5, 5)), ('B', (1, 0, 4, 2))])

    def test_placed_drilled_pads_block_only_overlapping_courtyards(self):
        import math
        octagon = [[round(400000 * math.cos(math.pi * k / 4)) + 10 * MM,
                    round(400000 * math.sin(math.pi * k / 4)) + 10 * MM] for k in range(8)]
        holed = part('P1', 9, 9, side='B')
        holed['packing_pad_geometry'] = {'status': 'proven', 'copper': [], 'holes': [{'polygons': [octagon]}]}
        holed = placer.transform(holed, holed['position_nm'], 0)
        cand = part('C1', 0, 0, shape=rectangle(0, 0, 2, 1))
        cand['packing_pad_geometry'] = {'status': 'proven', 'copper': [], 'holes': []}
        square = arrangement._inscribed(octagon)
        slabs = [('*', s) for poly in cand['courtyards']['F']['polygons'] for s in placer._slabs(poly)]
        entries = arrangement._gapped(slabs, [('*', square)], 0, 'P')
        blocked = 0
        for x in range(6 * MM, 12 * MM, MM // 4):
            for y in range(7 * MM, 12 * MM, MM // 4):
                if any(e[0] < x < e[1] and e[2] < y < e[3] for e in entries):
                    blocked += 1
                    moved = placer.transform(cand, [x, y], 0)
                    self.assertTrue(placer._hole_conflict(moved, holed), (x, y))
        self.assertGreater(blocked, 0)

    def test_ranking_cache_is_transparent(self):
        fixture = ArrangementTests('test_deterministic_and_input_preserving')
        fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
        cases = []
        for hole in (2, 5):
            # A 10 mm board: with H1 at 2 mm both blocks fit; at 5 mm the search backjumps and exhausts.
            small = copy.deepcopy(fixture.board)
            small['outline']['polygons'] = [rectangle(0, 0, 10, 10)]
            next(f for f in small['footprints'] if f['ref'] == 'H1').update(
                position_nm=[hole * MM, hole * MM],
                courtyards={'F': {'status': 'proven', 'polygons': [rectangle(hole, hole, 2, 2)]}})
            derivation = fixture.derive(capture=small)
            cases.append((small, derivation, [{'candidate': blocks.pack_block(
                small, fixture.inv, derivation, b, fixture.policy)['candidate']} for b in ('Power', 'Sensor')]))

        def runs():
            out = []
            for capture, derivation, entries in cases:
                result = fixture.arrange(capture=capture, derivation=derivation, entries=entries)
                result['block_arrangement'].get('search', {}).pop('elapsed_s', None)
                out.append(result)
            return out
        cached = runs()
        self.assertEqual([r['status'] for r in cached], ['planned', 'arrangement_search_exhausted'])
        self.assertEqual(cached[1]['block_arrangement']['search']['backjumps'], 5)
        original = arrangement.OWNED_CACHE
        arrangement.OWNED_CACHE = 0
        self.addCleanup(setattr, arrangement, 'OWNED_CACHE', original)
        self.assertEqual(runs(), cached)


class CliTests(ArrangementFixture, unittest.TestCase):
    def test_arrange_cli(self):
        (self.dir / 'probe.json').write_text(json.dumps(self.board))
        (self.dir / 'design.xml').write_bytes(native_xml())
        (self.dir / 'policy.json').write_text(json.dumps(self.policy))
        (self.dir / 'rules.json').write_text(json.dumps(plan_policy()))
        paths = []
        for entry in self.entries:
            path = self.dir / (entry['candidate']['block_id'] + '.json')
            path.write_text(json.dumps(entry['candidate']))
            paths += ['--candidate', path]
        args = [sys.executable, '-m', 'agent_ee.block_arrangement', '--probe', self.dir / 'probe.json',
                '--netlist', self.dir / 'design.xml', '--policy', self.dir / 'policy.json',
                '--arrangement', self.dir / 'rules.json', *paths, '--out', self.dir / 'plan.json']
        done = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=300,
                              env={'PYTHONPATH': str(ROOT / 'src')})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)['status'], 'planned')
        self.assertIn('"status": "arranging"', done.stderr)
        self.assertEqual(json.loads((self.dir / 'plan.json').read_text())['status'], 'planned')
        done = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=300,
                              env={'PYTHONPATH': str(ROOT / 'src')})
        self.assertEqual((done.returncode, json.loads(done.stdout)['status']), (1, 'refused'))


class NativeArrangementTests(unittest.TestCase):
    """An owned pcbnew board through the sandbox-ready writer: save, reload, judge, DRC."""

    def board(self, path):
        import pcbnew as k
        board = k.BOARD()

        def vec(x, y):
            return k.VECTOR2I(int(x * MM), int(y * MM))
        corners = [(0, 0), (40, 0), (40, 30), (0, 30)]
        for i, a in enumerate(corners):
            edge = k.PCB_SHAPE(board)
            edge.SetShape(k.SHAPE_T_SEGMENT)
            edge.SetLayer(k.Edge_Cuts)
            edge.SetStart(vec(*a))
            edge.SetEnd(vec(*corners[(i + 1) % 4]))
            edge.SetWidth(50000)
            board.Add(edge)
        nets = {}
        for i, (ref, _, _, fpid, value, pads) in enumerate(PARTS):
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            fp.SetValue(value)
            fp.SetFPID(k.LIB_ID(*fpid.split(':')))
            board.Add(fp)
            half = 1.5 if ref.startswith('U') else 1.0
            box = [(-half, -half), (half, -half), (half, half), (-half, half)]
            for j, a in enumerate(box):
                line = k.PCB_SHAPE(fp)
                line.SetShape(k.SHAPE_T_SEGMENT)
                line.SetLayer(k.F_CrtYd)
                line.SetStart(vec(*a))
                line.SetEnd(vec(*box[(j + 1) % 4]))
                line.SetWidth(50000)
                fp.Add(line)
            for j, (number, net) in enumerate(sorted(pads.items())):
                if net not in nets:
                    nets[net] = k.NETINFO_ITEM(board, net)
                    board.Add(nets[net])
                pad = k.PAD(fp)
                pad.SetNumber(number)
                pad.SetShape(k.PAD_SHAPE_RECT)
                pad.SetAttribute(k.PAD_ATTRIB_SMD)
                pad.SetSize(vec(0.5, 0.5))
                layers = k.LSET()
                layers.AddLayer(k.F_Cu)
                pad.SetLayerSet(layers)
                pad.SetPosition(vec(-half / 2 + (j % 2) * half, -half / 2 + (j // 2) * half))
                pad.SetNet(nets[net])
                fp.Add(pad)
            fp.SetPosition(vec(4 + 4.5 * i, 25))
        k.SaveBoard(str(path), board)

    def test_arranged_plan_saves_reloads_and_passes_the_native_gate(self):
        import hashlib
        import tempfile
        from pathlib import Path
        from toolchain import require_toolchain
        require_toolchain(native=True)
        from agent_ee.native_netlist import read_native_inventory, read_sheet_names
        from agent_ee.placement_probe import extract
        from agent_ee.placer_runner import place_candidate
        from test_blocks import obligations
        with tempfile.TemporaryDirectory(prefix='agent-ee-arrangement-native-') as tmp:
            root = Path(tmp)
            source, netlist, scratch = root / 'seed.kicad_pcb', root / 'design.xml', root / 'scratch'
            scratch.mkdir()
            self.board(source)
            netlist.write_bytes(native_xml())
            facts = extract(source)
            inventory = read_native_inventory(netlist)
            derivation = blocks.derive(inventory, read_sheet_names(netlist), facts,
                                       inventory_sha256=hashlib.sha256(netlist.read_bytes()).hexdigest())
            pol = {'schema': 'agent-ee.packing-policy/1', 'step_budget': 20000, 'gap_nm': 250000,
                   'fixed_refs': [], 'priority_refs': [], 'ground_nets': ['GND'], 'obstacles': [],
                   'electrical_locality': obligations()}
            candidates = []
            for block in derivation['blocks']:
                packed = blocks.pack_block(facts, inventory, derivation, block['id'], pol)
                self.assertEqual(packed['status'], 'packed', packed.get('reason'))
                candidates.append({'candidate': packed['candidate']})
            request = {'policy': pol, 'blocks': {'arrangement': plan_policy(), 'candidates': candidates},
                       'authorization': {'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
                                         'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                                         'writable_root': str(scratch)}}
            result = place_candidate(source, netlist, scratch / 'arranged', request)
            self.assertEqual(result['status'], 'placed', result.get('reason'))
            self.assertEqual(result['native_drc_comparison']['blocking_findings'], [])
            self.assertEqual(result['native_courtyard_overlaps'], [])
            self.assertEqual(result['judgment']['verdict'], 'pass')
            self.assertEqual(result['judgment']['locality_grading']['status'], 'pass')
            self.assertEqual(result['judgment']['block_rules'], [])
            record = result['plan']['block_arrangement']
            self.assertEqual(record['order'], ['Power', 'Sensor'])
            self.assertEqual({d['status'] for d in record['deferred_grading']}, {'pass'})
            saved = {f['ref']: f for f in extract(scratch / 'arranged/candidate.kicad_pcb')['footprints']}
            # The saved board keeps every block rigid: member offsets from the anchor are the frame's.
            for entry in candidates:
                cand = entry['candidate']
                anchor = saved[cand['frame']['anchor']]
                turns = int(anchor['rotation_deg']) // 90
                for row in cand['members']:
                    dx, dy = placer.rotate(row['pose']['position_nm'], turns)
                    self.assertEqual(saved[row['reference']]['position_nm'],
                                     [anchor['position_nm'][0] + dx, anchor['position_nm'][1] + dy])
            # A request may not combine block arrangement with targeted plans.
            with self.assertRaisesRegex(ValueError, 'cannot be combined'):
                place_candidate(source, netlist, scratch / 'mixed', {**request, 'targeted': {}})


if __name__ == '__main__':
    unittest.main()

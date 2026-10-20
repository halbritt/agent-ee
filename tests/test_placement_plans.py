"""Score gates and deterministic original planners, with no input mutation."""
import copy
import json
import unittest

from agent_ee.placement_plans import plan
from test_placement import MM, capture, part, polygon
from test_placer import request


def scoring():
    return {'schema': 'agent-ee.placement-policy/1', 'ground_nets': ['GND'],
            'ic_refs': ['U1'], 'connector_refs': [], 'max_shared_refs': 8,
            'capacitors': {'C1': {'capacitance_nf': 100, 'max_distance_mm': 4, 'deduction': 15},
                           'C2': {'capacitance_nf': 100, 'max_distance_mm': 7, 'deduction': 15}}}


def row(**changes):
    spec = {'mode': 'decoupling_row', 'anchor_ref': 'U1', 'capacitor_refs': ['C2', 'C1'],
            'offset_nm': [3*MM, 0], 'step_nm': [3*MM, 0], 'judge_policy': scoring()}
    spec.update(changes)
    return spec


def board():
    facts = capture([part('U1', 2, 2), part('C1', 12, 2), part('C2', 16, 2), part('H1', 12, 12)])
    facts['pcbnew_version'] = '10.0.7'
    for fp in facts['footprints'][:3]:
        fp['pads'] = [{'number': '1', 'position_nm': fp['position_nm'][:], 'net': 'VCC',
                       'shape': 0, 'attribute': 'SMD', 'rotation_deg': 0}]
    facts['footprints'][-1]['locked'] = True
    return facts


class PlacementPlansTest(unittest.TestCase):
    def test_missing_required_keys_are_typed_before_geometry_or_writes(self):
        specs = [row(), {'mode': 'net_cluster_seed', 'refs': ['C1'],
                        'origin_nm': [5*MM, 2*MM], 'spacing_nm': [3*MM, 3*MM],
                        'judge_policy': scoring()},
                 {'mode': 'bga_fanout', 'bga_ref': 'U1', 'escape_offset_nm': 200000,
                  'judge_policy': scoring()}]
        for spec in specs:
            for key in set(spec) - {'mode'}:
                with self.subTest(mode=spec['mode'], key=key):
                    incomplete = {k: v for k, v in spec.items() if k != key}
                    with self.assertRaisesRegex(ValueError, 'missing required keys: ' + key):
                        plan(board(), request(), incomplete)

    def test_fixed_overhang_before_and_after_match_saved_judge_policy(self):
        from agent_ee.placement import judge
        from agent_ee.placer import transform
        facts = board()
        facts['footprints'][-1] = transform(facts['footprints'][-1], [19*MM, 12*MM], 0)
        overhangs = [{'reference': 'H1', 'edges': [{'name': 'right',
                     'start_nm': [20*MM, 0], 'end_nm': [20*MM, 20*MM],
                     'outward': 'x+', 'max_nm': MM}],
                     'source': {'envelope_id': 'original', 'envelope_sha256': 'a'*64,
                                'pointer': '/fixed_footprints/0'}}]
        result = plan(facts, request(fixed_overhangs=overhangs), row())
        self.assertEqual(result['status'], 'planned', result)
        self.assertEqual(result['before'], judge(facts, scoring(), overhangs=overhangs))
        self.assertEqual(result['after']['overhangs'], overhangs)
        self.assertEqual(result['after']['verdict'], 'pass')
        undeclared = plan(facts, request(), row())
        self.assertEqual(undeclared['after']['verdict'], 'hard_fail')
        self.assertEqual(undeclared['status'], 'blocked')

    def test_cluster_seed_is_eligible_only_with_strict_score_improvement(self):
        spec = {'mode': 'net_cluster_seed', 'refs': ['C1', 'C2'],
                'origin_nm': [5*MM, 2*MM], 'spacing_nm': [3*MM, 3*MM],
                'judge_policy': scoring()}
        result = plan(board(), request(), spec)
        self.assertEqual((result['status'], result['before']['score'], result['after']['score']),
                         ('planned', 70, 100), result)
        for origin in ([12*MM, 2*MM], [2*MM, 2*MM]):
            blocked = plan(board(), request(), {**spec, 'origin_nm': origin})
            self.assertEqual(blocked['status'], 'blocked', blocked)

    def test_selection_keeps_blocked_unknown_and_tie_candidates(self):
        from agent_ee.placement_plans import select
        specs = [row(step_nm=[7*MM, 0]), row(offset_nm=[10*MM, 0]), row(), row()]
        original = copy.deepcopy(specs)
        result = select(board(), request(), specs)
        self.assertEqual(result['selection']['selected_index'], 2)
        self.assertEqual(result['selection']['plans'][0]['status'], 'planned')
        self.assertEqual(result['selection']['plans'][0]['after']['score'], 85)
        self.assertEqual(result['selection']['plans'][1]['status'], 'blocked')
        self.assertEqual(specs, original)
        facts = board()
        facts['outline']['status'] = 'unproven'
        blocked = select(facts, request(), specs)
        self.assertEqual(blocked['status'], 'blocked')
        self.assertIsNone(blocked['selection']['selected_index'])
        for bad in ([], {}, [row()]*65, [row(), {'mode': 'decoupling_row'}]):
            with self.assertRaises(ValueError):
                select(board(), request(), bad)

    def test_row_improves_score_and_is_byte_identical_without_mutation(self):
        facts, policy, spec = board(), request(), row()
        original = copy.deepcopy((facts, policy, spec))
        first = plan(facts, policy, spec)
        self.assertEqual(first['schema'], 'agent-ee.packing-plan/1')
        self.assertEqual(first['judgment'], first['after'])
        self.assertEqual((first['status'], first['before']['score'], first['after']['score']),
                         ('planned', 70, 100), first)
        self.assertEqual(json.dumps(first, sort_keys=True).encode(),
                         json.dumps(plan(facts, policy, spec), sort_keys=True).encode())
        self.assertEqual((facts, policy, spec), original)
        placements = {p['ref']: p for p in first['placements']}
        self.assertEqual(placements['C1']['position_nm'], [5*MM, 2*MM])
        self.assertEqual(placements['C2']['position_nm'], [8*MM, 2*MM])
        self.assertEqual((placements['H1']['position_nm'], placements['H1']['fixed']), ([12*MM, 12*MM], True))

    def test_tie_and_decrease_block_even_when_geometry_passes(self):
        for improve_initial in (False, True):
            facts = board()
            if improve_initial:
                # Starting score is 100; moving back to the original distant row decreases it.
                from agent_ee.placer import transform
                for fp in facts['footprints']:
                    if fp['ref'] in ('C1', 'C2'):
                        facts['footprints'][facts['footprints'].index(fp)] = transform(
                            fp, [(5 if fp['ref'] == 'C1' else 8)*MM, 2*MM], 0)
            result = plan(facts, request(), row(offset_nm=[10*MM, 0]))
            self.assertEqual(result['after']['verdict'], 'pass')
            self.assertEqual(result['status'], 'blocked')
            self.assertIn('placement score must strictly improve', result['reasons'])
            self.assertEqual(result['before']['score'], 100 if improve_initial else 70)
            self.assertEqual(result['after']['score'], 70)

    def test_outside_including_courtyard_crossing_concave_outline_blocks(self):
        for offset in ([19*MM, 0], [9*MM, 9*MM]):
            facts = board()
            facts['outline']['polygons'] = [polygon([(0, 0), (20, 0), (20, 20), (12, 20),
                                                     (12, 8), (8, 8), (8, 20), (0, 20)])]
            result = plan(facts, request(), row(offset_nm=offset))
            self.assertEqual(result['status'], 'blocked')
            self.assertTrue(any('outside outline' in r for r in result['reasons']))
            self.assertEqual(result['after']['verdict'], 'hard_fail')

    def test_missing_outline_coverage_keeps_before_defect(self):
        facts = board()
        facts['footprints'][2] = copy.deepcopy(facts['footprints'][1])
        facts['footprints'][2].update(ref='C2', uuid='C2')
        facts['outline'] = {'status': 'outline_unproven', 'reason': 'open non-rectangular outline'}
        result = plan(facts, request(), row())
        self.assertEqual((result['status'], result['outcome'], result['after']), ('blocked', 'unknown', None))
        self.assertEqual(result['before']['verdict'], 'hard_fail')
        self.assertIn('missing coverage', result['reasons'][0])

    def test_locked_held_and_untargeted_obstacles_cannot_move(self):
        for blocker in ('locked', 'held', 'untargeted'):
            facts = board()
            policy = request()
            if blocker == 'locked':
                facts['footprints'][1]['locked'] = True
            elif blocker == 'held':
                policy['fixed_refs'].append('C1')
            else:
                facts['footprints'].append(part('R1', 5, 2))
            original = copy.deepcopy(facts)
            result = plan(facts, policy, row())
            self.assertEqual(result['status'], 'blocked', result)
            self.assertTrue(any('cannot move' in r or 'conflicts' in r for r in result['reasons']))
            self.assertEqual(facts, original)

    def test_named_capacitors_and_non_ground_connectivity_required(self):
        with self.assertRaisesRegex(ValueError, 'explicitly declared'):
            plan(board(), request(), row(capacitor_refs=['H1']))
        facts = board()
        facts['footprints'][1]['pads'][0]['net'] = 'GND'
        with self.assertRaisesRegex(ValueError, 'no shared non-ground'):
            plan(facts, request(), row())

    def test_cluster_union_transitive_ground_exclusion_and_seed_only(self):
        facts = board()
        # U1 -- A -- C1 -- B -- C2; H1 only shares ground.
        facts['footprints'][0]['pads'][0]['net'] = 'A'
        facts['footprints'][1]['pads'][0]['net'] = 'A'
        extra = copy.deepcopy(facts['footprints'][1]['pads'][0])
        extra['net'] = 'B'
        facts['footprints'][1]['pads'].append(extra)
        facts['footprints'][2]['pads'][0]['net'] = 'B'
        ground = copy.deepcopy(extra)
        ground['net'] = 'GND'
        facts['footprints'][2]['pads'].append(ground)
        facts['footprints'][3]['pads'] = [copy.deepcopy(ground)]
        spec = {'mode': 'net_cluster_seed', 'refs': ['H1', 'C2', 'U1', 'C1'],
                'origin_nm': [2*MM, 2*MM], 'spacing_nm': [3*MM, 3*MM], 'judge_policy': scoring()}
        result = plan(facts, request(), spec)
        self.assertEqual(result['clusters'], [['C1', 'C2', 'U1'], ['H1']])
        self.assertTrue(result['seed_only'])
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result, plan(facts, request(), spec))

    def test_bga_pitch_escape_targets_and_unsupported_grid(self):
        facts = board()
        bga = facts['footprints'][0]
        bga['pads'] = [{'number': str(i), 'position_nm': [x, y], 'net': 'VCC',
                        'shape': 0, 'attribute': 'SMD', 'rotation_deg': 0}
                       for i, (x, y) in enumerate(((2*MM, 2*MM), (3*MM, 2*MM),
                                                  (2*MM, 3*MM), (3*MM, 3*MM)), 1)]
        spec = {'mode': 'bga_fanout', 'bga_ref': 'U1', 'escape_offset_nm': 200_000, 'judge_policy': scoring()}
        result = plan(facts, request(), spec)
        self.assertEqual(result['pitch_nm'], MM)
        self.assertEqual(result['escape_targets'][0]['position_nm'], [1_800_000, 1_800_000])
        self.assertEqual(result['status'], 'blocked')
        self.assertTrue(result['plan_only'])
        self.assertEqual(result, plan(facts, request(), spec))
        for pad in bga['pads']:
            pad['position_nm'][0] -= 2*MM
        result = plan(facts, request(), spec)
        self.assertTrue(any('escape target outside outline' in r for r in result['reasons']))
        bga['pads'].pop()
        with self.assertRaisesRegex(ValueError, 'complete axis-aligned pad grid'):
            plan(facts, request(), spec)


if __name__ == '__main__':
    unittest.main()

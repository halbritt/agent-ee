"""AGENTEE-361: advisory geometry and saved-board limits never prove defects."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_ee import placement_groups
from agent_ee.placement_check import evaluate, parameters
from agent_ee.placer import pack
from test_placement import MM, capture, part, rectangle
from test_placer import request
from test_placer_residuals import box


def source(value=None, kind='fixture_author'):
    record = {'class': kind, 'enforcement': 'hard',
              'citation': 'tests/test_unsourced_placement.py: original authored requirement'}
    if value is not None:
        record['value_nm'] = value
    return record


def region(gid='g', refs=('C1',), size=1, sourced=False):
    group = {'id': gid, 'references': list(refs), 'region': {'polygons': [box(0, 0, size, size)]}}
    if sourced:
        group['constraint_provenance'] = {'region': source()}
    return group


class UnsourcedPlacementTest(unittest.TestCase):
    def test_obstacle_capacity_requires_a_source_and_names_it(self):
        board = capture([part('C1', 5, 5)], rectangle(0, 0, 10, 10))
        obstacle = {'side': 'F', 'polygons': [box(0, 0, 10, 10)]}
        policy = request(fixed_refs=[], gap_nm=0, capacity_proof=True, obstacles=[obstacle])
        report = pack(board, policy)
        self.assertEqual((report['status'], report['outcome']), ('packing_infeasible', 'unknown'))
        self.assertEqual(report['owner_confirmation'], ['obstacle 0'])
        binding = {b['constraint']: b for b in report['binding_constraints']}
        self.assertEqual((binding['obstacle 0']['class'], binding['obstacle 0']['enforcement']), ('unknown', 'soft'))
        for kind in ('fixture_author', 'board', 'datasheet', 'manufacturer'):
            with self.subTest(kind=kind):
                hard = pack(board, {**policy, 'obstacles': [{**obstacle, 'provenance': source(kind=kind)}]})
                self.assertEqual(hard['outcome'], 'defect')
                self.assertEqual(hard['binding_constraints'][1]['class'], kind)
        self.assertNotIn('provenance', obstacle)

    def test_region_capacity_names_all_nested_regions(self):
        board = capture([part('C1', 12, 12), part('C2', 15, 15)], rectangle(0, 0, 20, 20))
        groups = [region('outer', ('C1',), 2, True), region('inner', ('C2',), 1)]
        report = pack(board, request(fixed_refs=[], gap_nm=0, groups=groups))
        self.assertEqual(report['outcome'], 'unknown')
        self.assertEqual(report['owner_confirmation'], ['group inner region'])
        binding = {b['constraint']: b for b in report['binding_constraints']}
        self.assertEqual(binding['group outer region']['class'], 'fixture_author')
        self.assertEqual(binding['group inner region']['class'], 'unknown')

    def test_later_sourced_region_defect_survives_earlier_unsourced_proof(self):
        board = capture([part('C1', 12, 12), part('C2', 15, 15)], rectangle(0, 0, 20, 20))
        groups = [region('advisory'), region('hard', ('C2',), sourced=True)]
        report = pack(board, request(fixed_refs=[], gap_nm=0, groups=groups))
        self.assertEqual((report['outcome'], report['proof']['group']), ('defect', 'hard'))
        self.assertNotIn('owner_confirmation', report)

    def test_region_proof_depending_on_obstacle_is_unknown(self):
        board = capture([part('C1', 12, 12)], rectangle(0, 0, 20, 20))
        obstacle = {'side': 'F', 'polygons': [box(0, 0, 2, 2)]}
        report = pack(board, request(fixed_refs=[], gap_nm=0, groups=[region(size=2, sourced=True)], obstacles=[obstacle]))
        self.assertEqual(report['outcome'], 'unknown')
        self.assertEqual(report['owner_confirmation'], ['obstacle 0'])

    def test_independent_region_proof_does_not_bind_unsourced_obstacle(self):
        board = capture([part('C1', 12, 12)], rectangle(0, 0, 20, 20))
        report = pack(board, request(fixed_refs=[], gap_nm=0, groups=[region(sourced=True)],
                     obstacles=[{'side': 'F', 'polygons': [box(0, 0, 1, 1)]}]))
        self.assertEqual(report['outcome'], 'defect')
        self.assertNotIn('obstacle 0', [b['constraint'] for b in report['binding_constraints']])

    def test_edge_minimum_unsourced_is_advisory_but_outside_stays_defect(self):
        board = capture([part('C1', 0, 5)], rectangle(0, 0, 10, 10))
        params = {'criterion': 'edge-clearance/1', 'drc_check': 'drc', 'min_courtyard_edge_mm': 1}
        report = evaluate('edge-clearance/1', board, {}, params)
        self.assertEqual((report['status'], report['unknown'], report['defects']), ('info', [], []))
        self.assertEqual(report['advisories'][0]['kind'], 'unsourced_limit')
        self.assertEqual(report['measured'][0]['constraint_provenance']['class'], 'unknown')
        params['constraint_provenance'] = {'min_courtyard_edge_mm': source(MM)}
        self.assertEqual(parameters(params), [])
        self.assertEqual(evaluate('edge-clearance/1', board, {}, params)['status'], 'defect')
        outside = capture([part('C1', -1, 5)], rectangle(0, 0, 10, 10))
        self.assertEqual(evaluate('edge-clearance/1', outside, {}, {**params, 'constraint_provenance': {}})['status'], 'defect')

    def test_unsourced_group_limits_are_advisory_and_measured(self):
        board = capture([part('C1', 10, 10)], rectangle(0, 0, 20, 20))
        group = {**region(), 'anchor': {'point_nm': [0, 0]}, 'max_distance_nm': MM}
        report = placement_groups.grade(board, placement_groups.validate([group]))
        self.assertEqual((report['status'], report['defects'], report['unknown']), ('info', [], []))
        self.assertEqual({u['kind'] for u in report['advisories']}, {'unsourced_limit'})
        self.assertEqual(report['measured'][0]['constraint_provenance']['enforcement'], 'soft')
        group['constraint_provenance'] = {'region': source(), 'max_distance_nm': source(MM)}
        hard = placement_groups.grade(board, placement_groups.validate([group]))
        self.assertEqual(hard['status'], 'defect')
        self.assertEqual({d['kind'] for d in hard['defects']}, {'outside_group_region', 'group_anchor_distance'})
        group['constraint_provenance'].pop('max_distance_nm')
        mixed = placement_groups.grade(board, placement_groups.validate([group]))
        self.assertEqual(mixed['status'], 'defect')
        self.assertEqual([d['kind'] for d in mixed['defects']], ['outside_group_region'])

    def test_satisfied_unsourced_limits_are_reported_and_keep_pass(self):
        board = capture([part('C1', 5, 5)], rectangle(0, 0, 10, 10))
        params = {'criterion': 'edge-clearance/1', 'drc_check': 'drc', 'min_courtyard_edge_mm': 1}
        edge = evaluate('edge-clearance/1', board, {}, params)
        self.assertEqual((edge['status'], edge['defects'], edge['unknown']), ('pass', [], []))
        self.assertEqual({a['exceeded'] for a in edge['advisories']}, {False})
        self.assertEqual(len(edge['advisories']), len(edge['measured']))
        params['constraint_provenance'] = {'min_courtyard_edge_mm': source(MM)}
        sourced = evaluate('edge-clearance/1', board, {}, params)
        self.assertEqual((sourced['status'], sourced['advisories']), ('pass', []))
        group = {**region(size=10), 'anchor': {'point_nm': [5 * MM, 5 * MM]}, 'max_distance_nm': MM}
        report = placement_groups.grade(board, placement_groups.validate([group]))
        self.assertEqual((report['status'], report['defects'], report['unknown']), ('pass', [], []))
        self.assertEqual(sorted((a['constraint'], a['exceeded']) for a in report['advisories']),
                         [('group g max_distance_nm', False), ('group g region', False)])
        self.assertTrue(placement_groups.satisfied(report))
        group['constraint_provenance'] = {'region': source(), 'max_distance_nm': source(MM)}
        hard = placement_groups.grade(board, placement_groups.validate([group]))
        self.assertEqual((hard['status'], hard['advisories']), ('pass', []))

    def test_exceeded_advisories_stay_blocking_for_planners(self):
        from agent_ee import block_arrangement, blocks
        board = capture([part('C1', 10, 10)], rectangle(0, 0, 20, 20))
        group = {'id': 'g', 'references': ['C1'], 'anchor': {'point_nm': [0, 0]}, 'max_distance_nm': MM}
        report = placement_groups.grade(board, placement_groups.validate([group]))
        self.assertEqual((report['status'], report['defects'], report['unknown']), ('info', [], []))
        self.assertFalse(placement_groups.satisfied(report))
        self.assertEqual([v['constraint'] for v in placement_groups.violations(report)], ['group g max_distance_nm'])
        self.assertFalse(blocks._grade_usable('groups', report))
        self.assertTrue(blocks._grade_usable('groups', placement_groups.grade(
            capture([part('C1', 0.5, 0.5)], rectangle(0, 0, 20, 20)), placement_groups.validate([group]))))
        admitted = {'B1': {'deferred': {'obligations': [], 'groups': ['g']}}}
        self.assertEqual(block_arrangement._deferred_grading({'group_grading': report}, admitted)[0]['status'], 'defect')

    def test_planner_accepts_satisfied_unsourced_group(self):
        board = capture([part('C1', 5, 5)], rectangle(0, 0, 20, 20))
        group = {'id': 'g', 'references': ['C1'], 'anchor': {'point_nm': [5 * MM, 5 * MM]}, 'max_distance_nm': 3 * MM}
        report = pack(board, request(fixed_refs=[], gap_nm=0, groups=[group]))
        self.assertEqual(report['status'], 'planned')
        self.assertEqual(report['group_grading']['status'], 'pass')
        self.assertEqual({a['exceeded'] for a in report['group_grading']['advisories']}, {False})

    def test_unsourced_obstacle_does_not_hide_independent_board_proof(self):
        from test_placer_capacity import columns_case
        board, policy = columns_case(7, capacity_proof=True)
        policy['obstacles'] = [{'side': 'F', 'polygons': [box(0, 0, 1, 1)]}]
        report = pack(board, policy)
        self.assertEqual(report['outcome'], 'defect')
        self.assertNotIn('obstacle 0', [b['constraint'] for b in report['binding_constraints']])

    def test_unsourced_region_does_not_hide_independent_board_proof(self):
        from test_placer_capacity import columns_case
        board, policy = columns_case(7, capacity_proof=True)
        policy['groups'] = [region(refs=('C1',))]
        report = pack(board, policy)
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(report['proof']['kind'], 'board_capacity_fragmented')
        self.assertNotIn('group g region', [b['constraint'] for b in report['binding_constraints']])

    def test_native_keepout_stays_hard(self):
        board = capture([part('C1', 12, 12)], rectangle(0, 0, 20, 20))
        board['rule_areas'] = [{'uuid': 'original-keepout', 'owner': 'board', 'layers': ['F.Cu'],
                              'forbid': {'footprints': True, 'pads': False},
                              'outline': {'status': 'proven', 'polygons': [box(0, 0, 2, 2)]}}]
        report = pack(board, request(fixed_refs=[], gap_nm=0, groups=[region(size=2, sourced=True)]))
        self.assertEqual(report['outcome'], 'defect')
        binding = {b['constraint']: b for b in report['binding_constraints']}
        self.assertEqual((binding['rule area 0']['class'], binding['rule area 0']['enforcement']), ('board', 'hard'))

    def test_bad_or_mismatched_sources_are_refused(self):
        for mutation in ({'class': 'owner'}, {'citation': ''}, {'enforcement': 'soft'}, {'value_nm': True}, {'value_nm': None}, {'value_nm': 2*MM}):
            group = {'id': 'g', 'references': ['C1'], 'anchor': {'point_nm': [0, 0]}, 'max_distance_nm': MM,
                     'constraint_provenance': {'max_distance_nm': {**source(MM), **mutation}}}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                placement_groups.validate([group])
            params = {'criterion': 'edge-clearance/1', 'drc_check': 'drc', 'min_courtyard_edge_mm': 1,
                      'constraint_provenance': {'min_courtyard_edge_mm': {**source(MM), **mutation}}}
            with self.subTest(edge_mutation=mutation), self.assertRaises(ValueError):
                parameters(params)
        for provenance in (None, {}, {'class': 'manufacturer'}, {**source(), 'class': 'owner'}):
            policy = request(fixed_refs=[], obstacles=[{'side': 'F', 'polygons': [box(0, 0, 1, 1)], 'provenance': provenance}])
            with self.subTest(provenance=provenance), self.assertRaises(ValueError):
                pack(capture([part('C1', 5, 5)]), policy)


if __name__ == '__main__':
    unittest.main()

"""Named advisory regions never prune and are reported on the placement (AGENTEE-436).

Every fixture is original integer geometry.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_ee import advisory_regions
from agent_ee.placer import pack, transform
from test_placement import MM, capture, part, rectangle
from test_placer import authored_source, request

AGENT = {'class': 'agent-advisory', 'reason': 'Original fixture: an unsourced agent rule.'}


def region(rid, **geometry):
    return {'id': rid, 'source': AGENT, 'proximity_nm': 0, **geometry}


def strip(plan):
    """A plan without the advisory report and the policy that declared it."""
    return {k: v for k, v in plan.items() if k not in ('advisory_regions', 'policy')}


class AdvisoryRegionTests(unittest.TestCase):
    def test_region_never_prunes_where_the_same_obstacle_refuses(self):
        # U1 fixed on a 6 x 2 mm board; C1 fits only right of it.
        board = capture([part('U1', 0, 0), part('C1', 3, 0)], outline=rectangle(0, 0, 6, 2))
        right = [rectangle(2, 0, 4, 2)]
        blocked = pack(board, request(gap_nm=0, obstacles=[{'side': 'F', 'polygons': right}]))
        self.assertEqual((blocked['status'], blocked['outcome']), ('packing_search_exhausted', 'unknown'), blocked)
        plain = pack(board, request(gap_nm=0))
        self.assertEqual(plain['status'], 'planned')
        policy = request(gap_nm=0, advisory_regions=[region('right-half', side='F', polygons=right)])
        reported = pack(board, policy)
        self.assertEqual(strip(reported), strip(plain))
        self.assertEqual(reported['policy'], policy)
        row, = reported['advisory_regions']['regions']
        self.assertEqual((row['status'], row['enforcement'], row['kind']), ('info', 'advisory', 'absolute'))
        self.assertEqual(row['intruders'], [{'reference': 'C1', 'fixed': False}])
        self.assertEqual(row['source'], AGENT)
        self.assertEqual(reported['advisory_regions']['search'], 'never-pruned')

    def test_fixed_intruder_is_advisory_not_a_fixed_conflict(self):
        # A fixed back part under a fixed front owner: the J3-under-L1 case in miniature.
        board = capture([part('L1', 2, 2), part('J3', 3, 3, 'B'), part('C1', 10, 10)])
        shadow = region('L1-shadow', shadow_of={'reference': 'L1', 'margin_nm': 500000})
        as_region = pack(board, request(fixed_refs=['L1', 'J3'], advisory_regions=[shadow]))
        self.assertEqual(as_region['status'], 'planned', as_region.get('reason'))
        row, = as_region['advisory_regions']['regions']
        self.assertEqual((row['kind'], row['side'], row['status']), ('shadow', 'B', 'info'))
        self.assertEqual(row['intruders'], [{'reference': 'J3', 'fixed': True}])
        self.assertEqual(row['polygons'], [[[int(1.5 * MM), int(1.5 * MM)], [int(4.5 * MM), int(1.5 * MM)],
                                            [int(4.5 * MM), int(4.5 * MM)], [int(1.5 * MM), int(4.5 * MM)]]])
        self.assertEqual(row['subject'], {'reference': 'L1', 'uuid': 'L1', 'fpid': '', 'side': 'F',
                                          'position_nm': [2 * MM, 2 * MM], 'rotation_deg': 0,
                                          'courtyard_box_nm': [2 * MM, 2 * MM, 4 * MM, 4 * MM]})
        # The same geometry as a stationary obstacle refuses the fixed placement.
        static = {'side': 'B', 'polygons': row['polygons']}
        refused = pack(board, request(fixed_refs=['L1', 'J3'], obstacles=[static]))
        self.assertEqual(refused['status'], 'fixed_conflict')

    def test_shadow_follows_its_owner_on_every_pose(self):
        board = capture([part('L1', 2, 2), part('R1', 3, 3, 'B'), part('R2', 13, 13, 'B')])
        regions = [region('L1-shadow', shadow_of={'reference': 'L1', 'margin_nm': 0}, proximity_nm=MM)]
        first = advisory_regions.grade(board, regions, set())['regions'][0]
        self.assertEqual([i['reference'] for i in first['intruders']], ['R1'])
        self.assertEqual(first['near'], [])
        moved = copy.deepcopy(board)
        moved['footprints'][0] = transform(board['footprints'][0], [12 * MM, 10 * MM], 0)
        second = advisory_regions.grade(moved, regions, set())['regions'][0]
        self.assertEqual(second['intruders'], [])
        # R2's courtyard starts 1 mm below the moved shadow: exactly at the proximity.
        self.assertEqual(second['near'], [{'reference': 'R2', 'fixed': False, 'distance_nm': MM}])
        self.assertEqual(second['subject']['position_nm'], [12 * MM, 10 * MM])
        self.assertNotEqual(first['polygons'], second['polygons'])
        regions[0]['proximity_nm'] = MM - 1
        self.assertEqual(advisory_regions.grade(moved, regions, set())['regions'][0]['near'], [])

    def test_absolute_subject_is_bound_and_excluded(self):
        board = capture([part('U2', 5, 5), part('C1', 8, 5), part('C2', 15, 15)])
        ring = region('imu-ring', side='F', polygons=[rectangle(4, 4, 4, 4)], subject='U2', proximity_nm=MM,
                      concern='Tall or heavy parts away from the IMU (original fixture requirement).')
        row = advisory_regions.grade(board, [ring], {'U2'})['regions'][0]
        self.assertEqual(row['subject']['reference'], 'U2')
        self.assertEqual(row['intruders'], [])
        self.assertEqual(row['near'], [{'reference': 'C1', 'fixed': False, 'distance_nm': 0}])
        self.assertEqual(row['concern'], ring['concern'])
        clear = advisory_regions.grade(capture([part('U2', 5, 5)]), [ring], {'U2'})['regions'][0]
        self.assertEqual((clear['status'], clear['intruders'], clear['near']), ('pass', [], []))

    def test_native_keepouts_and_sourced_obstacles_are_unchanged(self):
        from test_placer import PlacerTest
        board = PlacerTest.keepout_board(None)
        everywhere = [region('all', side='F', polygons=[rectangle(0, 0, 20, 20)])]
        for extra in ({}, {'obstacles': [{'side': 'F', 'polygons': [rectangle(10, 0, 10, 20)],
                                           'provenance': authored_source('Original fixture: right half reserved.')}]}):
            plain = pack(board, request(**extra))
            reported = pack(board, request(**extra, advisory_regions=everywhere))
            self.assertEqual(plain['status'], 'planned', plain.get('reason'))
            self.assertEqual(strip(reported), strip(plain))
            self.assertEqual([i['reference'] for i in reported['advisory_regions']['regions'][0]['intruders']],
                             ['C1', 'U1'])
        # A fixed part in a native footprint keep-out still refuses, regions or not.
        board['footprints'][1] = part('C1', 5, 2)
        self.assertEqual(pack(board, request(fixed_refs=['U1', 'C1'], advisory_regions=everywhere))['status'],
                         'fixed_conflict')

    def test_validation_refuses_malformed_regions(self):
        refs = {'U1', 'L1'}
        good = region('a', side='F', polygons=[rectangle(0, 0, 1, 1)])
        advisory_regions.validate([good, {**good, 'id': 'b', 'source': {
            'class': 'datasheet', 'citation': 'Original fixture datasheet p.1'}}], refs)
        bad = [
            [good, good],
            [{**good, 'source': {'class': 'board', 'citation': 'x'}}],
            [{**good, 'source': {'class': 'agent-advisory', 'citation': 'x'}}],
            [{**good, 'source': {'class': 'owner', 'reason': 'x'}}],
            [{**good, 'source': {'class': 'owner', 'citation': ' '}}],
            [{**good, 'proximity_nm': -1}],
            [{**good, 'proximity_nm': 1.5}],
            [{**good, 'side': 'X'}],
            [{**good, 'subject': 'Q9'}],
            [{**good, 'enforcement': 'hard'}],
            [{**good, 'shadow_of': {'reference': 'L1', 'margin_nm': 0}}],
            [region('s', shadow_of={'reference': 'Q9', 'margin_nm': 0})],
            [region('s', shadow_of={'reference': 'L1', 'margin_nm': -1})],
            [region('s', shadow_of={'reference': 'L1'})],
            [region('s', side='F', polygons=[[[0, 0], [1, 0]]])],
            'not-a-list',
        ]
        for regions in bad:
            with self.subTest(regions=regions), self.assertRaises(ValueError):
                advisory_regions.validate(regions, refs)
        with self.assertRaises(ValueError):
            pack(capture([part('U1', 2, 2)]), request(advisory_regions=[{**good, 'source': {'class': 'board'}}]))

    def test_height_evidence_describes_bodies_and_leaves_risk_unresolved(self):
        board = capture([part('L1', 2, 2), part('R1', 3, 3, 'B'), part('R2', 2, 3, 'B')])
        report = advisory_regions.grade(board, [region('s', shadow_of={'reference': 'L1', 'margin_nm': 0})], set())
        heights = {'schema': advisory_regions.HEIGHTS, 'board_sha256': 'a' * 64, 'pcbnew_version': 'test',
                   'footprints': [{'ref': 'R1', 'status': 'measured', 'height_mm': '0.55', 'models': []},
                                  {'ref': 'R2', 'status': 'unknown', 'reason': 'embedded STEP bounds unqualified'}]}
        rows = advisory_regions.annotate(report, heights)['regions'][0]['intruders']
        self.assertEqual(rows[0]['body'], {'status': 'measured', 'height_mm': '0.55', 'source': 'native-step-bounds'})
        self.assertEqual(rows[1]['body'], {'status': 'unknown', 'reason': 'embedded STEP bounds unqualified'})
        self.assertEqual({r['tall_heavy_risk'] for r in rows}, {'unresolved'})
        self.assertNotIn('body', report['regions'][0]['intruders'][0])
        with self.assertRaises(ValueError):
            advisory_regions.annotate(report, {**heights, 'board_sha256': 'b' * 64})
        with self.assertRaises(ValueError):
            advisory_regions.annotate(report, {**heights, 'footprints': heights['footprints'] * 2})

    def test_cli_grades_a_capture(self):
        board = capture([part('L1', 2, 2), part('J3', 3, 3, 'B')])
        policy = request(fixed_refs=['L1', 'J3'],
                         advisory_regions=[region('L1-shadow', shadow_of={'reference': 'L1', 'margin_nm': 0})])
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'probe.json').write_text(json.dumps(board))
            (root / 'policy.json').write_text(json.dumps(policy))
            args = ['--probe', str(root / 'probe.json'), '--policy', str(root / 'policy.json'),
                    '--out', str(root / 'out.json')]
            self.assertEqual(advisory_regions.main(args), 0)
            row, = json.loads((root / 'out.json').read_text())['regions']
            self.assertEqual(row['intruders'], [{'reference': 'J3', 'fixed': True}])
            self.assertEqual(advisory_regions.main(args), 1)  # Never overwrites.


if __name__ == '__main__':
    unittest.main()

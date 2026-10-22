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
        self.assertEqual(blocked['search_failure']['status'], 'packing_search_exhausted', blocked)
        # AGENTEE-249 default: the proof binds the unsourced (soft) obstacle, so it stays unknown.
        self.assertEqual((blocked['status'], blocked['outcome'], blocked['owner_confirmation']),
                         ('packing_infeasible', 'unknown', ['obstacle 0']), blocked)
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

    def test_unqualified_capture_geometry_or_identity_is_refused_never_clear(self):
        box = region('box', side='B', polygons=[rectangle(0, 0, 5, 5)])
        shadow = region('s', shadow_of={'reference': 'L1', 'margin_nm': 0})
        inside = capture([part('L1', 12, 12), part('R1', 1, 1, 'B')])
        self.assertEqual(advisory_regions.grade(inside, [box], set())['regions'][0]['status'], 'info')

        def broken(change):
            board = copy.deepcopy(inside)
            change(board)
            return board
        def courtyard(ref, side, record):
            return lambda b: next(f for f in b['footprints'] if f['ref'] == ref)['courtyards'].update({side: record})
        def drop(ref, side):
            return lambda b: next(f for f in b['footprints'] if f['ref'] == ref)['courtyards'].pop(side)
        cases = {
            'missing intruder courtyard': (broken(drop('R1', 'B')), box),
            'missing shadow owner courtyard': (broken(drop('L1', 'F')), shadow),
            'unproven courtyard': (broken(courtyard('R1', 'B', {'status': 'unproven',
                                                               'polygons': [rectangle(1, 1, 2, 2)]})), box),
            'courtyard without polygons': (broken(courtyard('R1', 'B', {'status': 'proven'})), box),
            'unsupported courtyard side': (broken(courtyard('R1', 'X', {'status': 'proven',
                                                                       'polygons': [rectangle(1, 1, 2, 2)]})), box),
            'unknown mounting side': (broken(lambda b: b['footprints'][1].update(side='')), box),
            'invalid schema': (broken(lambda b: b.update(schema='invalid')), box),
            'invalid board hash': (broken(lambda b: b.update(board_sha256='invalid')), box),
            'missing uuid': (broken(lambda b: b['footprints'][1].pop('uuid')), box),
            'empty inventory': (broken(lambda b: b.update(footprints=[])), box),
        }
        for first, second in ((part('R1', 1, 1, 'B'), part('R1', 10, 10, 'B')),
                              (part('R1', 10, 10, 'B'), part('R1', 1, 1, 'B'))):
            cases[f"duplicate reference, inside {'first' if first['position_nm'][0] < MM * 5 else 'last'}"] = (
                capture([first, {**second, 'uuid': 'other'}]), box)
            cases[f"duplicate uuid, inside {'first' if first['position_nm'][0] < MM * 5 else 'last'}"] = (
                capture([first, {**second, 'ref': 'R2'}]), box)
        for name, (board, rule) in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    advisory_regions.grade(board, [rule], set())
                with tempfile.TemporaryDirectory() as root:
                    root = Path(root)
                    (root / 'probe.json').write_text(json.dumps(board))
                    (root / 'policy.json').write_text(json.dumps(request(advisory_regions=[rule])))
                    self.assertEqual(advisory_regions.main(
                        ['--probe', str(root / 'probe.json'), '--policy', str(root / 'policy.json'),
                         '--out', str(root / 'out.json')]), 1)
                    self.assertFalse((root / 'out.json').exists())

    def test_opposite_side_exclusion_and_two_sided_courtyards_still_grade(self):
        box = region('box', side='B', polygons=[rectangle(0, 0, 5, 5)])
        # A front part with no back courtyard is legitimately outside a back region.
        row = advisory_regions.grade(capture([part('C1', 1, 1)]), [box], set())['regions'][0]
        self.assertEqual((row['status'], row['intruders']), ('pass', []))
        # A front part declaring a proven back courtyard (a through-hole body) is graded there.
        tht = part('J1', 1, 1)
        tht['courtyards']['B'] = {'status': 'proven', 'polygons': [rectangle(1, 1, 2, 2)]}
        row = advisory_regions.grade(capture([tht]), [box], set())['regions'][0]
        self.assertEqual(row['intruders'], [{'reference': 'J1', 'fixed': False}])

    def test_sourced_height_binds_and_a_probe_never_overrides_it(self):
        board = capture([part('L1', 2, 2), part('R1', 3, 3, 'B'), part('R2', 2, 3, 'B'), part('R3', 3, 2, 'B')])
        report = advisory_regions.grade(board, [region('s', shadow_of={'reference': 'L1', 'margin_nm': 0})], set())
        datasheet = {'class': 'datasheet', 'citation': 'Original fixture datasheet, dimensions table',
                     'sha256': '0' * 64}
        sourced = advisory_regions.sourced_heights({'schema': advisory_regions.SOURCED_HEIGHTS, 'parts': [
            {'reference': 'R1', 'part': 'FIXTURE-1', 'height_mm': '4.8', 'tolerance_mm': '0.2', 'source': datasheet},
            {'reference': 'R2', 'height_mm': '5.2', 'tolerance_mm': '0.2', 'source': datasheet}]},
            {fp['ref'] for fp in board['footprints']})
        heights = {'schema': advisory_regions.HEIGHTS, 'board_sha256': 'a' * 64, 'pcbnew_version': 'test',
                   'footprints': [{'ref': 'R1', 'status': 'unknown', 'reason': 'no 3D model'},
                                  {'ref': 'R2', 'status': 'measured', 'height_mm': '5.6', 'models': []}]}
        out = advisory_regions.annotate(report, heights, sourced)
        rows = {r['reference']: r for r in out['regions'][0]['intruders']}
        self.assertEqual(rows['R1']['body'], {'status': 'sourced', 'height_mm': '4.8', 'tolerance_mm': '0.2',
                                              'source': datasheet, 'part': 'FIXTURE-1',
                                              'model_probe': {'status': 'unknown', 'reason': 'no 3D model'}})
        # 5.6 mm measured is outside 5.2 +/- 0.2 mm: both are kept, the body is a conflict.
        self.assertEqual(rows['R2']['body']['status'], 'conflict')
        self.assertEqual(rows['R2']['body']['model_probe'], {'status': 'measured', 'height_mm': '5.6'})
        self.assertEqual(rows['R3']['body'], {'status': 'unknown', 'reason': 'not measured'})
        self.assertEqual({r['tall_heavy_risk'] for r in rows.values()}, {'unresolved'})
        self.assertEqual(out['sourced_heights']['references'], ['R1', 'R2'])
        heights['footprints'][1]['height_mm'] = '5.4'
        self.assertEqual(advisory_regions.annotate(report, heights, sourced)['regions'][0]['intruders'][1]['body']
                         ['status'], 'sourced')
        # Sourced alone, with no probe: unsourced parts stay unknown.
        alone = {r['reference']: r['body']['status']
                 for r in advisory_regions.annotate(report, None, sourced)['regions'][0]['intruders']}
        self.assertEqual(alone, {'R1': 'sourced', 'R2': 'sourced', 'R3': 'unknown'})
        good = {'reference': 'R1', 'height_mm': '4.8', 'tolerance_mm': '0.2', 'source': datasheet}
        for bad in ({**good, 'reference': 'Q9'}, {**good, 'height_mm': 4.8}, {**good, 'height_mm': '0'},
                    {**good, 'tolerance_mm': '-0.1'}, {**good, 'height_mm': 'NaN'},
                    {**good, 'source': {**datasheet, 'sha256': 'x'}},
                    {**good, 'source': {'class': 'datasheet', 'citation': 'no hash'}},
                    {**good, 'source': {'class': 'agent-advisory', 'reason': 'guess'}},
                    {**good, 'source': {'class': 'owner', 'citation': 'owner note', 'sha256': '0' * 64}},
                    {**good, 'extra': 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                advisory_regions.sourced_heights({'schema': advisory_regions.SOURCED_HEIGHTS, 'parts': [bad]},
                                                 {'R1'})
        with self.assertRaises(ValueError):
            advisory_regions.sourced_heights({'schema': advisory_regions.SOURCED_HEIGHTS, 'parts': [good, good]},
                                             {'R1'})

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

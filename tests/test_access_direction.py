"""Original socket/access fixtures; dimensions are authored test requirements."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee import access_direction as access, blocks, placement_predicates
from agent_ee.placement import judge
from agent_ee.placer import pack, transform
from agent_ee.placement_probe import extract
from test_placement import MM, capture, part, rectangle
from test_placer import request
from test_block_arrangement import ArrangementFixture
from toolchain import require_toolchain


def declaration(*, cited=True, width=2):
    value = {'schema': access.SCHEMA, 'mouth_nm': [2 * MM, MM], 'outward': 'x+',
             'lane_polygons_nm': [rectangle(2, 0, 30, width)]}
    if cited:
        value['provenance'] = {'class': 'fixture_author', 'enforcement': 'hard',
                               'citation': 'Original EE379 test requirement: outward lane, 30 mm travel.'}
    return value


def socket(*, cited=True, width=2, side='B'):
    fp = part('J3', 7, 7, side)
    fp['access_direction'] = declaration(cited=cited, width=width)
    return fp


def proposed(board, plan):
    poses = {p['ref']: p for p in plan['placements']}
    return {**board, 'footprints': [transform(fp, poses[fp['ref']]['position_nm'], poses[fp['ref']]['rotation_deg'])
                                  for fp in board['footprints']]}


class AccessTests(unittest.TestCase):
    def test_inboard_defect_and_outboard_measurement_on_each_edge_and_side(self):
        for side in ('F', 'B'):
            fp = socket(side=side)
            fp['access_direction']['lane_polygons_nm'][0].reverse()
            self.assertEqual(judge(capture([fp]))['hard_failures'][0]['kind'], 'access_direction')
            for rotation, position in ((0, [18, 5]), (90, [5, 2]), (180, [2, 5]), (270, [5, 18])):
                placed = transform(fp, [v * MM for v in position], rotation)
                row = access.evaluate(placed, rectangle(0, 0, 20, 20))
                with self.subTest(side=side, rotation=rotation):
                    self.assertEqual(row['status'], 'pass', row)
                    self.assertEqual(row['inboard_area_mm2'], 0)
                    self.assertTrue(row['overhang']['regions_nm'])
                    self.assertEqual(judge(capture([placed]))['verdict'], 'pass')

    def test_movable_socket_600_mm2_lane_and_residual_repack(self):
        board = capture([socket(width=20), part('U1', 8, 8)])
        before = copy.deepcopy(board)
        plan = pack(board, request(fixed_refs=[]))
        self.assertEqual(plan['status'], 'planned', plan)
        self.assertEqual(board, before)
        row = judge(proposed(board, plan))['access_directions'][0]
        self.assertEqual(row['status'], 'pass')
        self.assertEqual(row['inboard_area_mm2'], 0)
        self.assertEqual(plan['judgment']['access_directions'], [row])
        # The same placement remains admissible when block members become fixed in residual packing.
        poses = proposed(board, plan)
        rerun = pack(poses, request(fixed_refs=['J3']))
        self.assertEqual(rerun['status'], 'planned', rerun)
        self.assertEqual(next(p for p in rerun['placements'] if p['ref'] == 'J3'),
                         {**next(p for p in plan['placements'] if p['ref'] == 'J3'), 'fixed': True})

    def test_courtyard_lane_overhang_avoids_false_area_width_and_capacity_proofs(self):
        fp = socket(width=20)
        fp['courtyards']['B']['polygons'] = [rectangle(7, 7, 32, 20)]
        board = capture([fp])
        plan = pack(board, request(fixed_refs=[], capacity_proof=True))
        self.assertEqual(plan['status'], 'planned', plan)
        result = judge(proposed(board, plan))
        self.assertEqual(result['verdict'], 'pass', result)
        self.assertEqual(result['declared_overhangs'][0]['overhang_area_mm2'], 600)
        self.assertEqual(plan['capacity']['excluded_access_overhang_refs'], ['J3'])
        # Unrelated structural area defects still prove infeasibility.
        board['footprints'].append(part('R1', 0, 0, 'B', rectangle(0, 0, 30, 30)))
        failed = pack(board, request(fixed_refs=[], capacity_proof=True))
        self.assertEqual((failed['status'], failed['outcome']), ('packing_infeasible', 'defect'))

    def test_only_cited_lane_authorizes_courtyard_overhang(self):
        fp = socket()
        fp['access_direction']['mouth_nm'] = [1500000, MM]
        fp['access_direction']['lane_polygons_nm'] = [rectangle(1.5, 0, 30, 2)]
        fp = transform(fp, [18500000, 5*MM], 0)
        self.assertEqual(judge(capture([fp]))['verdict'], 'pass')
        # The allowance does not waive courtyard area outside the lane width.
        wider = copy.deepcopy(fp)
        wider['courtyards']['B']['polygons'] = [rectangle(18.5, 5, 2, 3)]
        self.assertIn('outside_outline', [r['kind'] for r in judge(capture([wider]))['hard_failures']])
        fp['access_direction'].pop('provenance')
        self.assertIn('outside_outline', [r['kind'] for r in judge(capture([fp]))['hard_failures']])

    def test_exit_edge_permission_preserves_other_edge_margins(self):
        from agent_ee.placer import _fits
        fp = socket()
        fp['access_direction']['mouth_nm'] = [1500000, MM]
        fp['access_direction']['lane_polygons_nm'] = [rectangle(1.5, 0, 30, 2)]
        outline = rectangle(0, 0, 20, 20)
        safe = transform(fp, [18500000, 5*MM], 0)
        corner = transform(fp, [18500000, 0], 0)
        self.assertTrue(_fits(safe, outline, 250000))
        self.assertFalse(_fits(corner, outline, 250000))

    def test_existing_fixed_envelope_maximum_is_not_relaxed_by_a_lane(self):
        fp = socket()
        fp['access_direction']['mouth_nm'] = [1500000, MM]
        fp['access_direction']['lane_polygons_nm'] = [rectangle(1.5, 0, 30, 2)]
        fp = transform(fp, [18500000, 5*MM], 0)
        limits = [{'reference': 'J3', 'edges': [{'name': 'right', 'start_nm': [20*MM, 0],
            'end_nm': [20*MM, 20*MM], 'outward': 'x+', 'max_nm': 100000}],
            'source': {'envelope_id': 'original EE379 fixed maximum', 'envelope_sha256': 'a'*64,
                       'pointer': '/fixed_footprints/0'}}]
        result = judge(capture([fp]), overhangs=limits)
        self.assertEqual(result['verdict'], 'hard_fail')
        self.assertEqual(result['hard_failures'][0]['kind'], 'outside_outline')
        self.assertEqual(pack(capture([fp]), request(fixed_refs=['J3'], fixed_overhangs=limits))['status'],
                         'fixed_conflict')

    def test_uncited_is_reported_without_changing_search_or_verdict(self):
        for fixed in ([], ['J3']):
            board = capture([socket(cited=False)])
            plain = copy.deepcopy(board)
            plain['footprints'][0].pop('access_direction')
            a, b = (pack(source, request(fixed_refs=fixed)) for source in (board, plain))
            self.assertEqual(a['status'], b['status'])
            self.assertEqual(a['placements'], b['placements'])
            self.assertEqual(a['steps_used'], b['steps_used'])
            self.assertEqual(a['judgment']['verdict'], b['judgment']['verdict'])
            self.assertTrue(a['judgment']['advisories'][0]['advisory'])
        board['footprints'][0]['rotation_deg'] = 45
        self.assertEqual(judge(board)['verdict'], 'pass')
        self.assertEqual(judge(board)['access_directions'][0]['status'], 'info')

    def test_missing_or_blank_citation_cannot_gate_even_if_marked_hard(self):
        for citation in (None, '', '   '):
            fp = socket()
            source = fp['access_direction']['provenance']
            if citation is None:
                source.pop('citation')
            else:
                source['citation'] = citation
            result = pack(capture([fp]), request(fixed_refs=['J3']))
            self.assertEqual(result['status'], 'planned', result)
            self.assertEqual(result['judgment']['access_directions'][0]['status'], 'info')
            self.assertEqual(result['judgment']['verdict'], 'pass')

    def test_uncited_missing_mounting_side_is_reported_without_crashing(self):
        fp = socket(cited=False)
        fp.pop('side')
        report = judge(capture([fp]))
        self.assertEqual(report['verdict'], 'unknown')  # Existing missing mounting-side courtyard coverage.
        self.assertEqual(report['access_directions'][0]['status'], 'info')

    def test_advisory_access_never_becomes_a_binding_search_failure_reason(self):
        from test_placer import authored_source
        obstacles = [{'side': 'B', 'polygons': [rectangle(x+1.5, 0, .5, 20)],
                      'provenance': authored_source('Original EE379 comb fixture: 1.5 mm corridors.')}
                     for x in range(0, 20, 2)]
        board = capture([socket(cited=False)])
        plain = copy.deepcopy(board)
        plain['footprints'][0].pop('access_direction')
        policy = request(fixed_refs=[], gap_nm=0, obstacles=obstacles)
        declared, legacy = (pack(b, policy) for b in (board, plain))
        self.assertEqual(declared['status'], 'packing_search_exhausted')
        for key in ('status', 'outcome', 'reason', 'steps_used', 'placements'):
            self.assertEqual(declared[key], legacy[key], key)

    def test_fixed_inboard_is_refused_with_reason(self):
        result = pack(capture([socket()]), request(fixed_refs=['J3']))
        self.assertEqual((result['status'], result['outcome']), ('fixed_conflict', 'unknown'))
        self.assertIn('J3', result['reason'])
        self.assertIn('access direction', result['reason'])

    def test_lane_too_wide_for_every_edge_refuses_and_names_access(self):
        result = pack(capture([socket(width=21)]), request(fixed_refs=[]))
        self.assertEqual((result['status'], result['outcome']), ('packing_search_exhausted', 'unknown'))
        self.assertIn('access direction', result['reason'])
        self.assertEqual(result['placements'], [])

    def test_mouth_inboard_outside_and_inward_normal_cannot_pass(self):
        fp = socket()
        for x, rotation in ((17, 0), (19, 0), (20, 180)):
            pose = transform(fp, [x * MM, 5 * MM], rotation)
            self.assertEqual(access.evaluate(pose, rectangle(0, 0, 20, 20))['status'], 'defect')

    def test_concave_board_beyond_the_edge_can_still_obstruct_the_lane(self):
        fp = transform(socket(), [4 * MM, 2 * MM], 0)
        outline = [[0,0], [10*MM,0], [10*MM,10*MM], [8*MM,10*MM],
                   [8*MM,2*MM], [6*MM,2*MM], [6*MM,10*MM], [0,10*MM]]
        self.assertEqual(access.evaluate(fp, outline)['status'], 'defect')

    def test_concave_sweep_uses_only_the_clearance_width(self):
        from test_placement import polygon
        fp = transform(socket(), [4*MM, 3*MM], 0)
        outline = polygon([(0,0),(6,0),(6,8),(10,8),(10,6),(12,6),(12,10),(0,10)])
        row = access.evaluate(fp, outline)
        self.assertEqual((row['status'], row['inboard_area_mm2']), ('pass', 0))
        edge = row['overhang']['edges'][0]
        self.assertEqual(sorted((edge['start_nm'][1], edge['end_nm'][1])), [3*MM, 5*MM])

    def test_lane_outside_but_unqualified_corner_band_is_unknown(self):
        fp = transform(socket(), [18*MM, -MM], 0)
        row = access.evaluate(fp, rectangle(0, 0, 20, 20))
        self.assertEqual(row['inboard_area_mm2'], 0)
        self.assertEqual(row['status'], 'unknown')
        self.assertFalse(access.admits(fp, rectangle(0, 0, 20, 20)))

    def test_bad_declarations_fail_unknown_never_pass_or_crash(self):
        invalid = [None, '{}', '{', ' ' * 8193]
        for key, value in (('outward', []), ('mouth_nm', [False, 0]), ('schema', 'future'),
                           ('lane_polygons_nm', []), ('provenance', None),
                           ('lane_polygons_nm', [rectangle(0, 0, 5, 2)]), ('mouth_nm', [2*MM, 3*MM])):
            invalid.append({**declaration(), key: value})
        for value in invalid:
            fp = socket()
            fp['access_direction'] = value
            with self.subTest(value=value):
                result = pack(capture([fp]), request(fixed_refs=[]))
                self.assertEqual((result['status'], result['outcome']), ('unsupported_geometry', 'unknown'), result)
                self.assertEqual(judge(capture([fp]))['verdict'], 'unknown')

    def test_external_overhanging_body_cannot_occupy_a_clearance_lane(self):
        fp = transform(socket(), [18 * MM, 5 * MM], 0)
        other = part('J4', 21, 5, side='B')
        allowance = [{'reference': 'J4', 'edges': [{'name': 'right', 'start_nm': [20*MM, 0],
            'end_nm': [20*MM, 20*MM], 'outward': 'x+', 'max_nm': 5*MM}],
            'source': {'envelope_id': 'original EE379 overhang fixture', 'envelope_sha256': 'a'*64,
                       'pointer': '/fixed_footprints/0'}}]
        board = capture([fp, other])
        result = judge(board, overhangs=allowance)
        self.assertEqual(result['verdict'], 'hard_fail')
        self.assertEqual(result['hard_failures'][0]['blocked_by'], ['J4'])
        packed = pack(board, request(fixed_refs=['J3', 'J4'], fixed_overhangs=allowance))
        self.assertEqual(packed['status'], 'fixed_conflict')
        # A proven inner curve bound decides a defect; the outer-only band is unknown.
        outer = [[[20*MM, 7*MM-1], [21*MM, 7*MM-1], [21*MM, 9*MM], [20*MM, 9*MM]]]
        inner = [rectangle(20, 7, 1, 2)]
        other['courtyards']['B'] = {'status': 'proven', 'polygons': outer, 'inner_polygons': inner,
                                     'approximation': {'tolerance_nm': 1}}
        result = judge(board, overhangs=allowance)
        self.assertEqual(result['verdict'], 'unknown', result)
        self.assertEqual(result['access_directions'][0]['approximate_peers'], ['J4'])
        other['courtyards']['B']['inner_polygons'] = copy.deepcopy(outer)
        self.assertEqual(judge(board, overhangs=allowance)['verdict'], 'hard_fail')
        other['courtyards']['B']['inner_polygons'] = inner
        fp['access_direction'].pop('provenance')
        self.assertEqual(judge(board, overhangs=allowance)['verdict'], 'pass')

    def test_unsupported_sourced_rotation_unknown_and_unrelated_defect_survives(self):
        fp = socket()
        fp['rotation_deg'] = 45
        board = capture([fp, part('U1', 3, 3), part('C1', 3, 3)])
        result = judge(board)
        self.assertEqual(result['verdict'], 'hard_fail')
        self.assertIn('courtyard_overlap', [r['kind'] for r in result['hard_failures']])
        self.assertEqual(result['unknown'][0]['kind'], 'access_direction')

    def test_access_defect_survives_an_unqualified_peer_courtyard(self):
        peer = part('U1', 2, 2)
        peer['courtyards']['F']['status'] = 'missing'
        report = judge(capture([socket(side='F'), peer]))
        self.assertEqual(report['verdict'], 'hard_fail')
        self.assertEqual(report['hard_failures'][0]['kind'], 'access_direction')
        self.assertEqual(report['unknown'][0]['kind'], 'courtyard')

    def test_missing_native_pose_is_unknown(self):
        for key in ('rotation_deg', 'position_nm', 'side'):
            fp = socket()
            fp.pop(key)
            report = judge(capture([fp]))
            self.assertEqual(report['verdict'], 'unknown')
            self.assertIn('access_direction', [u['kind'] for u in report['unknown']])

    def test_containment_evidence_contains_the_access_measurement_and_advisory(self):
        for cited, status in ((True, 'defect'), (False, 'pass')):
            result = placement_predicates.outline_containment(capture([socket(cited=cited)]))
            self.assertEqual(result['status'], status)
            self.assertEqual(len(result['access_directions']), 1)
            self.assertEqual(len(result['advisories']), 0 if cited else 1)


class BlockAccessTests(ArrangementFixture, unittest.TestCase):
    def test_stage_a_defers_access_and_stage_b_rotates_storage_member_to_edge(self):
        # Original block fixture substitutes a storage socket role on a movable member.
        fp = next(f for f in self.board['footprints'] if f['ref'] == 'U2')
        fp['access_direction'] = declaration()
        fp['access_direction']['mouth_nm'] = [2750000, MM]
        fp['access_direction']['lane_polygons_nm'] = [rectangle(2.75, 0, 30, 3)]
        self.entries = [{'candidate': blocks.pack_block(self.board, self.inv, self.derivation, b['id'],
                                                       self.policy)['candidate']}
                        for b in self.derivation['blocks']]
        plan = self.arrange()
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        row = plan['judgment']['access_directions'][0]
        self.assertEqual(row['ref'], 'U2')
        self.assertEqual((row['status'], row['inboard_area_mm2']), ('pass', 0))
        self.assertTrue(any(r['ref'] == 'U2' and r['overhang_area_mm2'] > 0
                            for r in plan['judgment']['declared_overhangs']))
        final = proposed(self.board, plan)
        self.assertEqual(judge(final)['access_directions'], [row])
        # Frame geometry stays rigid; access only constrains the final macro pose.
        poses = self.placements(plan)
        stored = {m['reference']: m['pose'] for entry in self.entries
                  for m in entry['candidate']['members']}
        self.assertEqual((poses['U2']['rotation_deg'] - poses['C2']['rotation_deg']) % 360,
                         (stored['U2']['rotation_deg'] - stored['C2']['rotation_deg']) % 360)
        # Moving the same member to an unexitable lane refuses at stage B.
        fp['access_direction']['lane_polygons_nm'] = [rectangle(2.75, 0, 30, 101)]
        entries = [{'candidate': blocks.pack_block(self.board, self.inv, self.derivation, b['id'],
                                                  self.policy)['candidate']}
                   for b in self.derivation['blocks']]
        refused = self.arrange(entries=entries)
        self.assertNotEqual(refused['status'], 'planned')
        self.assertIn('U2', refused['reason'])
        self.assertIn('access direction', refused['reason'])
        self.assertGreater(refused['block_arrangement']['search']['rejected'].get('access_direction', 0), 0)


class NativeAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def test_saved_native_declaration_replayed_after_real_placement_writer(self):
        import pcbnew as k
        from agent_ee.placer_runner import place_candidate
        from test_placement_native import NativePlacementTest
        with tempfile.TemporaryDirectory(prefix='ee379-native-') as directory:
            root = Path(directory)
            fixture = NativePlacementTest()
            fixture.root = root
            path = fixture.board('overlap')
            board = k.LoadBoard(str(path))
            fp = next(f for f in board.GetFootprints() if f.GetReference() == 'U1')
            value = declaration()
            value['mouth_nm'] = [750000, 0]
            value['lane_polygons_nm'] = [rectangle(.75, -1, 30, 2)]
            value['lane_polygons_nm'][0].reverse()
            field = k.PCB_FIELD(fp, k.FIELD_T_USER, access.DECLARATION)
            field.SetName(access.DECLARATION)
            field.SetText(json.dumps(value))
            field.SetVisible(False)
            fp.Add(field)
            k.SaveBoard(str(path), board)
            original = path.read_bytes()
            facts = extract(path)
            self.assertEqual(json.loads(next(f for f in facts['footprints'] if f['ref'] == 'U1')['access_direction']), value)
            self.assertEqual(judge(facts)['verdict'], 'hard_fail')
            netlist = root / 'owned.xml'
            netlist.write_text('<export><components><comp ref="U1"/><comp ref="C1"/></components><nets/></export>')
            output = root / 'output'
            output.mkdir()
            config = {'policy': request(fixed_refs=[]), 'authorization': {
                'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
                'source_sha256': hashlib.sha256(original).hexdigest(), 'writable_root': str(output)}}
            report = place_candidate(path, netlist, output / 'candidate', config)
            self.assertEqual(report['status'], 'placed', report)
            self.assertTrue(report['judgment']['declared_overhangs'])
            row = report['judgment']['access_directions'][0]
            self.assertEqual((row['status'], row['inboard_area_mm2']), ('pass', 0))
            saved = json.loads((output / 'candidate' / 'placement-judgment.json').read_text())
            self.assertEqual(saved['access_directions'], [row])
            self.assertEqual(path.read_bytes(), original)

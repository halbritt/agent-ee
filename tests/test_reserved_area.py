"""Original reserve geometry; no donor board or library data."""
import copy
from fractions import Fraction
from pathlib import Path
import tempfile
import unittest

from agent_ee import reserved_area as reserve
from test_placement import capture, part, rectangle, MM
from test_placer import request
from toolchain import require_toolchain


def proven(polygons):
    return {'status': 'proven', 'polygons': polygons}


class AreaTests(unittest.TestCase):
    def test_union_clipping_excess_and_per_side_owners(self):
        facts = capture([part('J3', 2, 2, 'B')])
        policy = request(obstacles=[{'id': 'antenna', 'side': 'B', 'polygons': [
            rectangle(-1, 0, 3, 2), rectangle(0, 0, 3, 2)]}])
        fab = {'J3': {'B': proven([rectangle(2, 2, 1, 1)])}}
        original = copy.deepcopy((facts, policy, fab))
        report = reserve.report(facts, policy, fab)
        self.assertEqual(report['status'], 'measured')
        self.assertEqual(report['sides']['F']['union_area_mm2'], [0, 1])
        rows = report['sides']['B']['owners']
        self.assertEqual([(r['owner'], r['area_mm2']) for r in rows], [('antenna', [6, 1]), ('J3', [3, 1])])
        self.assertEqual(report['sides']['B']['union_area_mm2'], [9, 1])
        self.assertEqual(rows[0]['side_fraction'], [3, 200])
        self.assertEqual((facts, policy, fab), original)
        self.assertEqual(report, reserve.report(facts, policy, fab))

    def test_overlaps_between_kinds_not_double_counted(self):
        facts = capture([part('U1', 0, 0)])
        policy = request(obstacles=[{'side': 'F', 'polygons': [rectangle(0, 0, 2, 2)]}])
        report = reserve.report(facts, policy, {'U1': {'F': proven([rectangle(0, 0, 1, 1)])}})
        self.assertEqual(report['sides']['F']['union_area_mm2'], [4, 1])
        self.assertEqual([r['area_mm2'] for r in report['sides']['F']['owners']], [[4, 1], [3, 1]])

    def test_rational_sloping_intersection_and_concave_board(self):
        # Two triangles meet on sloping edges at rational coordinates.
        board = [(0, 0), (6, 0), (6, 6), (0, 6)]
        left = [(0, 0), (6, 0), (0, 6)]
        right = [(0, 0), (6, 0), (6, 6)]
        self.assertEqual(reserve._area(board, [([left, right], [])]), 27)
        self.assertEqual(reserve._area(board, [([left], [right])]), 9)
        concave = [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)]
        self.assertEqual(reserve._area(concave, [([board], [])]), 12)
        self.assertEqual(reserve._area(board, [([[(0,0), (3,0), (0,1)]], [])]), Fraction(3, 2))

    def test_seeded_triangle_unions_against_shared_intersection(self):
        import random
        from agent_ee.placement import area2, intersection_area, simple_polygon
        rng = random.Random(380)
        outline = [(-100, -100), (100, -100), (100, 100), (-100, 100)]
        for _ in range(50):
            a = simple_polygon([[rng.randrange(20), 0], [30, rng.randrange(1, 20)], [0, 30]])
            b = simple_polygon([[rng.randrange(20), 0], [30, rng.randrange(1, 20)], [0, 30]])
            common = intersection_area(a, b)
            self.assertEqual(reserve._area(outline, [([a, b], [])]),
                             Fraction(area2(a)+area2(b), 2)-common)
            self.assertEqual(reserve._area(outline, [([a], [b])]), Fraction(area2(a), 2)-common)

    def test_measurement_work_guard_reports_unknown(self):
        from unittest.mock import patch
        facts = capture([])
        policy = request(obstacles=[{'side': 'F', 'polygons': [rectangle(0, 0, 4, 4)]}])
        with patch.object(reserve, 'MAX_EDGE_PAIRS', 0):
            report = reserve.report(facts, policy, {})
        self.assertEqual(report['status'], 'unknown')
        self.assertIn('work guard', report['sides']['F']['owners'][0]['reason'])
        self.assertNotIn('area_mm2', report['sides']['F']['owners'][0])

    def test_cheaper_pose_cannot_leave_a_sourced_region(self):
        facts = capture([part('J3', 8, 8, 'B')])
        facts['rule_areas'] = [{'uuid': 'lane', 'name': 'card', 'owner': 'J3',
            'layers': ['B.Cu'], 'forbid': {'footprints': True},
            'outline': proven([rectangle(-22, 8, 30, 2)])}]
        fab = {'J3': {'B': proven([rectangle(8, 8, 2, 2)])}}
        policy = request(fixed_refs=[], gap_nm=0, groups=[{
            'id': 'mount', 'references': ['J3'], 'region': {'polygons': [rectangle(8, 8, 2, 2)]},
            'constraint_provenance': {'region': {'class': 'fixture_author', 'enforcement': 'hard',
                                               'citation': 'Original EE380 fixture mounting region'}}}])
        costs = reserve.report(facts, policy, fab)
        reserve.alternatives(facts, policy, fab, costs, {})
        lane = next(r for r in costs['sides']['B']['owners'] if r['kind'] == 'rule_area')
        self.assertEqual(lane['alternative']['status'], 'none_found')
        self.assertEqual(lane['area_mm2'], [16, 1])

    def test_geometry_only_no_saving_is_not_a_complete_native_search(self):
        row = {'side': 'B', 'kind': 'rule_area', 'id': 'lane', 'owner': 'J3', 'area_mm2': [2, 1]}
        trials = [(((0, 0), 0), {('B', 'rule_area', 'lane'): {'area_mm2': [2, 1]}})]
        alternative = reserve._cheapest(row, trials)
        self.assertEqual(alternative['status'], 'none_found')
        self.assertIs(alternative['search_complete'], False)
        self.assertEqual(alternative['native_attempts'], [])

    def test_contained_courtyard_excess_has_no_rigid_pose_saving(self):
        facts = capture([part('U1', 2, 2)])
        fab = {'U1': {'F': proven([rectangle(2, 2, 1, 1)])}}
        policy = request(fixed_refs=[])
        costs = reserve.report(facts, policy, fab)
        reserve.alternatives(facts, policy, fab, costs, {})
        row = costs['sides']['F']['owners'][0]
        self.assertEqual(row['area_mm2'], [3, 1])
        self.assertEqual(row['alternative']['status'], 'no_cheaper_contained_pose')

    def test_unknown_fab_curves_and_outline_do_not_become_zero(self):
        facts = capture([part('U1', 2, 2)])
        report = reserve.report(facts, request(), {})
        self.assertEqual(report['status'], 'unknown')
        self.assertNotIn('union_area_mm2', report['sides']['F'])
        self.assertEqual(report['sides']['F']['owners'][0]['status'], 'unknown')
        facts['footprints'][0]['courtyards']['F']['inner_polygons'] = [rectangle(2, 2, 1, 1)]
        report = reserve.report(facts, request(), {'U1': {'F': proven([rectangle(2, 2, 1, 1)])}})
        self.assertIn('bounded', report['sides']['F']['owners'][0]['reason'])
        facts['outline'] = {'status': 'unsupported'}
        self.assertEqual(reserve.report(facts, request(), {})['status'], 'unknown')

    def test_threshold_is_strictly_above_five_percent_and_advisory(self):
        facts = capture([])
        policy = request(obstacles=[{'id': 'lane', 'side': 'B', 'polygons': [rectangle(0, 0, 5, 4)]}])
        self.assertEqual(reserve.report(facts, policy, {})['findings'], [])
        policy['obstacles'][0]['polygons'] = [rectangle(0, 0, 6, 4)]
        report = reserve.report(facts, policy, {})
        self.assertEqual(report['findings'][0]['reserve']['owner'], 'lane')
        self.assertEqual(report['findings'][0]['enforcement'], 'advisory')
        self.assertEqual(report['threshold']['provenance'], 'AGENTEE-380 suggestion; unsourced')


class NativeAreaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def test_owned_card_lane_six_hundred_then_zero_and_geometry_alternative(self):
        import pcbnew as k
        from agent_ee.placement_probe import extract
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            board = k.BOARD()
            def contour(parent, layer, points):
                for a, b in zip(points, points[1:]+points[:1]):
                    shape = k.PCB_SHAPE(parent)
                    shape.SetShape(k.SHAPE_T_SEGMENT)
                    shape.SetLayer(layer)
                    shape.SetStart(k.VECTOR2I(*a)); shape.SetEnd(k.VECTOR2I(*b))
                    shape.SetWidth(50000); parent.Add(shape)
            contour(board, k.Edge_Cuts, rectangle(0, 0, 90, 65))
            fp = k.FOOTPRINT(board); fp.SetReference('J3'); board.Add(fp)
            # Use Flip before drawing the authored back-side contours.
            fp.Flip(k.VECTOR2I(0, 0), False)
            contour(fp, k.B_CrtYd, rectangle(0, -1, 2, 2))
            contour(fp, k.B_Fab, rectangle(0, -1, 1, 2))
            label = k.PCB_TEXT(fp); label.SetText('${REFERENCE}'); label.SetLayer(k.B_Fab)
            fp.Add(label)
            zone = k.ZONE(fp); zone.SetIsRuleArea(True); zone.SetDoNotAllowFootprints(True)
            zone.SetLayer(k.B_Cu); zone.SetZoneName('J3 card access lane')
            poly = zone.Outline(); poly.NewOutline()
            for x, y in rectangle(-30, -10, 30, 20): poly.Append(x, y)
            fp.Add(zone); fp.SetPosition(k.VECTOR2I(45*MM, 32*MM))
            path = root / 'card.kicad_pcb'; k.SaveBoard(str(path), board)
            facts = extract(path); fab = reserve.fabrication_outlines(path)
            policy = request(gap_nm=0)
            report = reserve.report(facts, policy, fab)
            lane = next(r for r in report['sides']['B']['owners'] if r['kind'] == 'rule_area')
            self.assertEqual((lane['owner'], lane['area_mm2']), ('J3', [600, 1]))
            self.assertEqual(lane['side_fraction'], [4, 39])
            unchanged = copy.deepcopy(facts)
            reserve.alternatives(facts, policy, fab, report, {})
            self.assertEqual(facts, unchanged)
            self.assertEqual(lane['alternative']['area_mm2'], [0, 1])
            self.assertEqual(lane['alternative']['saving_mm2'], [600, 1])
            self.assertEqual(lane['alternative']['status'], 'geometry_qualified_native_unverified')
            repeated = reserve.report(facts, policy, fab)
            reserve.alternatives(facts, policy, fab, repeated, {})
            self.assertEqual(report, repeated)
            fixed = reserve.report(facts, policy, fab)
            reserve.alternatives(facts, {**policy, 'fixed_refs': ['J3']}, fab, fixed, {})
            self.assertTrue(all(r['alternative']['reason'] == 'fixed owner pose'
                                for r in fixed['sides']['B']['owners']))
            rigid = reserve.report(facts, policy, fab)
            reserve.alternatives(facts, policy, fab, rigid,
                                 {'block_arrangement': {'blocks': [{'references': ['J3']}]}})
            self.assertTrue(all(r['alternative']['reason'] == 'rigid block member'
                                for r in rigid['sides']['B']['owners']))
            # Native saved second board, rather than a transformed fact masquerading as one.
            fp.SetPosition(k.VECTOR2I(0, 32*MM)); k.SaveBoard(str(path), board)
            edge = reserve.report(extract(path), policy, reserve.fabrication_outlines(path))
            lane = next(r for r in edge['sides']['B']['owners'] if r['kind'] == 'rule_area')
            self.assertEqual(lane['area_mm2'], [0, 1])
            self.assertEqual(edge['status'], 'measured')


class PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def test_saved_result_judgment_and_unchanged_plan_carry_advisory(self):
        import json
        from test_placer_native import NativePlacerTest
        from agent_ee.placer_runner import place_candidate, _plan
        from agent_ee.placement_probe import extract
        fixture = NativePlacerTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.config['policy']['obstacles'] = [
            {'id': 'owner-access', 'side': 'B', 'polygons': [rectangle(0, 0, 20, 8)]}]
        before = _plan(extract(fixture.source), fixture.config)
        destination = fixture.scratch / 'costed'
        result = place_candidate(fixture.source, fixture.netlist, destination, fixture.config)
        self.assertEqual(result['status'], 'placed')
        self.assertEqual(result['plan'], before)
        self.assertEqual(result['reserved_area'], result['judgment']['reserved_area'])
        self.assertEqual(result['reserved_area'], json.loads(
            (destination / 'placement-judgment.json').read_text())['reserved_area'])
        self.assertEqual(result, json.loads((destination / 'placement-result.json').read_text()))
        self.assertEqual(result['reserved_area']['findings'][0]['reserve']['owner'], 'owner-access')


import test_block_provenance as provenance_tests


class PacketCostTests(provenance_tests.ProvenanceFixture, unittest.TestCase):
    scope = provenance_tests.ReviewInputTests.scope
    bound = provenance_tests.ReviewInputTests.bound

    def test_cost_findings_survive_sealed_block_packet_and_bind_board(self):
        from agent_ee.block_arrangement import provenance_artifact, validate_provenance
        from agent_ee.reviewers import build_packet
        import hashlib
        artifact = self.bound()
        facts = capture([])
        facts['board_sha256'] = artifact['board_sha256']
        costs = reserve.report(facts, request(obstacles=[
            {'id': 'large-lane', 'side': 'B', 'polygons': [rectangle(0, 0, 20, 10)]}]), {})
        artifact = provenance_artifact(self.plan, artifact['board_sha256'],
            plan_sha256=hashlib.sha256(self.plan_bytes).hexdigest(), reserved_area=costs)
        run, _, _ = self.scope(artifact)
        packet = build_packet(run, [], 'ee380')
        carried = packet['block_provenance']['record']['reserved_area']
        self.assertEqual(carried, costs)
        self.assertEqual(carried['findings'][0]['reserve']['owner'], 'large-lane')
        artifact['reserved_area']['board_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'saved board'):
            validate_provenance(artifact)


class NativeAlternativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        import pcbnew as k
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        board = k.BOARD()
        def contour(parent, layer, polygon):
            for a, b in zip(polygon, polygon[1:]+polygon[:1]):
                shape = k.PCB_SHAPE(parent); shape.SetShape(k.SHAPE_T_SEGMENT)
                shape.SetLayer(layer); shape.SetWidth(50000)
                shape.SetStart(k.VECTOR2I(*a)); shape.SetEnd(k.VECTOR2I(*b)); parent.Add(shape)
        contour(board, k.Edge_Cuts, rectangle(0, 0, 90, 65))
        fp = k.FOOTPRINT(board); fp.SetReference('J3'); board.Add(fp)
        fp.Reference().SetVisible(False); fp.Value().SetVisible(False)
        fp.Flip(k.VECTOR2I(0, 0), False)
        contour(fp, k.B_CrtYd, rectangle(0, -1, 2, 2))
        contour(fp, k.B_Fab, rectangle(0, -1, 1, 2))
        pad = k.PAD(fp); pad.SetNumber('1'); pad.SetAttribute(k.PAD_ATTRIB_SMD)
        pad.SetShape(k.PAD_SHAPE_RECT); pad.SetSize(k.VECTOR2I(500000, 500000))
        layers = k.LSET(); layers.AddLayer(k.B_Cu); pad.SetLayerSet(layers); pad.SetPosition(k.VECTOR2I(MM, 0)); fp.Add(pad)
        zone = k.ZONE(fp); zone.SetIsRuleArea(True); zone.SetDoNotAllowFootprints(True)
        zone.SetLayer(k.B_Cu); zone.SetZoneName('owned card lane')
        polygon = zone.Outline(); polygon.NewOutline()
        for x, y in rectangle(-30, -10, 30, 20): polygon.Append(x, y)
        fp.Add(zone); fp.SetPosition(k.VECTOR2I(45*MM, 32*MM))
        self.source = self.root / 'card.kicad_pcb'
        self.assertTrue(k.SaveBoard(str(self.source), board))
        import json
        self.source.with_suffix('.kicad_pro').write_text(json.dumps({'board': {'design_settings': {
            'rule_severities': {'missing_courtyard': 'error'}}}}))
        self.policy = request(gap_nm=0)

    def run_alternatives(self, name):
        from agent_ee.placement_probe import extract
        from agent_ee.reserved_area_native import NativeAlternatives
        facts = extract(self.source); fab = reserve.fabrication_outlines(self.source)
        costs = reserve.report(facts, self.policy, fab)
        validator = NativeAlternatives(self.source, self.root / name, facts, self.policy, {})
        reserve.alternatives(facts, self.policy, fab, costs, {}, validator)
        return next(r for r in costs['sides']['B']['owners']
                    if r['kind'] == 'rule_area' and r['owner'] == 'J3'), validator

    def test_owned_six_hundred_lane_native_legal_zero_and_retained_witnesses(self):
        from agent_ee.storage import digest, read_json
        original = self.source.read_bytes()
        lane, validator = self.run_alternatives('native')
        alternative = lane['alternative']
        self.assertEqual(lane['area_mm2'], [600, 1])
        self.assertEqual(alternative['status'], 'native_validated', alternative)
        self.assertEqual(alternative['saving_mm2'], [600, 1])
        self.assertEqual(alternative['area_mm2'], [0, 1])
        witness = alternative['native_attempts'][-1]['validation']
        path = self.root / witness['directory']
        self.assertEqual(digest(path / 'candidate.kicad_pcb'), witness['board_sha256'])
        raw = read_json(self.root / witness['witness'])
        self.assertEqual(digest(path / 'native-drc.json'), raw['drc_sha256'])
        self.assertEqual(read_json(path / 'placement-write-scope.json')['status'], 'allowed')
        self.assertEqual(self.source.read_bytes(), original)
        again, _ = self.run_alternatives('native-again')
        self.assertEqual(again['alternative']['position_nm'], alternative['position_nm'])
        self.assertEqual(again['alternative']['rotation_deg'], alternative['rotation_deg'])

    def test_cheapest_geometry_fails_native_then_next_legal_pose_is_reported(self):
        import pcbnew as k
        board = k.LoadBoard(str(self.source))
        zone = k.ZONE(board); zone.SetIsRuleArea(True); zone.SetDoNotAllowPads(True)
        zone.SetLayer(k.B_Cu); zone.SetZoneName('native-only pad exclusion')
        polygon = zone.Outline(); polygon.NewOutline()
        for x, y in rectangle(0, 31, 2, 2): polygon.Append(x, y)
        board.Add(zone); self.assertTrue(k.SaveBoard(str(self.source), board))
        lane, _ = self.run_alternatives('blocked-cheapest')
        alternative = lane['alternative']
        self.assertEqual(alternative['status'], 'native_validated', alternative)
        attempts = alternative['native_attempts']
        self.assertEqual(attempts[0]['position_nm'], [0, 32*MM])
        self.assertEqual(attempts[0]['validation']['status'], 'rejected', attempts)
        self.assertIn('items_not_allowed', attempts[0]['validation']['blocking_types'])
        self.assertEqual(attempts[-1]['validation']['status'], 'validated')
        self.assertNotEqual((alternative['position_nm'], alternative['rotation_deg']),
                            (attempts[0]['position_nm'], attempts[0]['rotation_deg']))

    def test_numeric_text_in_keepout_name_cannot_waive_structural_finding(self):
        import pcbnew as k
        from agent_ee.placement_probe import extract
        from agent_ee.reserved_area_native import NativeAlternatives
        from agent_ee.storage import read_json
        board = k.LoadBoard(str(self.source))
        zone = k.ZONE(board); zone.SetIsRuleArea(True); zone.SetDoNotAllowPads(True)
        zone.SetLayer(k.B_Cu); zone.SetZoneName('keepout 1 mm; actual 0.5 mm')
        polygon = zone.Outline(); polygon.NewOutline()
        for x, y in rectangle(0, 31, 2, 2): polygon.Append(x, y)
        board.Add(zone); self.assertTrue(k.SaveBoard(str(self.source), board))
        validator = NativeAlternatives(self.source, self.root / 'injected-name',
                                       extract(self.source), self.policy, {})
        result = validator('J3', (0, 32*MM), 180)
        drc = read_json(self.root / result['directory'] / 'native-drc.json')
        self.assertTrue(any('keepout 1 mm; actual 0.5 mm' in v['description']
                            for v in drc['violations']))
        self.assertEqual(result['status'], 'rejected', result)
        self.assertEqual(result['blocking_types'], ['items_not_allowed'])
        self.assertNotIn('items_not_allowed', result['advisory_types'])

    def test_every_alternative_drc_failure_keeps_search_unknown(self):
        from unittest.mock import patch
        from agent_ee import placer_runner
        native = placer_runner._native_drc
        calls = []
        def fail_alternatives(board, scratch):
            calls.append(str(board))
            if len(calls) > 1:
                raise OSError('injected alternative DRC failure')
            return native(board, scratch)
        with patch('agent_ee.placer_runner._native_drc', side_effect=fail_alternatives):
            lane, validator = self.run_alternatives('all-unknown')
        self.assertIsNotNone(validator.baseline)
        self.assertGreater(len(calls), 1)
        self.assert_unknown_search(lane['alternative'], 'injected alternative DRC failure')

    def test_baseline_drc_failure_keeps_search_unknown(self):
        from unittest.mock import patch
        with patch('agent_ee.placer_runner._native_drc',
                   side_effect=OSError('injected baseline DRC failure')):
            lane, validator = self.run_alternatives('baseline-unknown')
        self.assertIsNone(validator.baseline)
        self.assert_unknown_search(lane['alternative'], 'injected baseline DRC failure')

    def assert_unknown_search(self, alternative, first_reason):
        from agent_ee.storage import read_json
        self.assertEqual(alternative['status'], 'unknown', alternative)
        self.assertEqual(alternative['qualification'], 'hypothetical, unverified')
        self.assertIs(alternative['search_complete'], False)
        attempts = alternative['native_attempts']
        self.assertGreater(len(attempts), 1)
        for attempt in attempts:
            validation = attempt['validation']
            self.assertEqual(validation['status'], 'unknown')
            self.assertEqual(validation['qualification'], 'hypothetical, unverified')
            self.assertEqual(read_json(self.root / validation['witness'])['status'], 'unknown')
        self.assertIn(first_reason, read_json(self.root / attempts[0]['validation']['witness'])['reason'])

    def test_all_native_rejections_complete_finite_search(self):
        self.source.with_suffix('.kicad_dru').write_bytes(b'''(version 1)
(rule "Owned fixture pad exclusion"
  (condition "A.Type == 'Pad'")
  (constraint disallow pad))
''')
        lane, _ = self.run_alternatives('all-rejected')
        alternative = lane['alternative']
        self.assertEqual(alternative['status'], 'none_found', alternative)
        self.assertIs(alternative['search_complete'], True)
        self.assertGreater(len(alternative['native_attempts']), 1)
        for attempt in alternative['native_attempts']:
            self.assertEqual(attempt['validation']['status'], 'rejected')
            self.assertIn('items_not_allowed', attempt['validation']['blocking_types'])

    def test_tool_failure_stays_unknown_and_is_never_legal(self):
        from unittest.mock import patch
        from agent_ee.placement_probe import extract
        from agent_ee.reserved_area_native import NativeAlternatives
        facts = extract(self.source)
        validator = NativeAlternatives(self.source, self.root / 'unavailable', facts, self.policy, {})
        with patch('agent_ee.placer_runner._native_drc', side_effect=OSError('test tool unavailable')):
            result = validator('J3', (0, 32*MM), 180)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['qualification'], 'hypothetical, unverified')
        self.assertIn('test tool unavailable', (self.root / result['witness']).read_text())

    def test_preservation_refusal_is_never_legal(self):
        from unittest.mock import patch
        from agent_ee.placement_probe import extract
        from agent_ee.reserved_area_native import NativeAlternatives
        validator = NativeAlternatives(self.source, self.root / 'bad-save', extract(self.source), self.policy, {})
        with patch('agent_ee.placement_adapter.write_scope', return_value={
                'status': 'refused', 'violations': ['injected preservation refusal']}):
            result = validator('J3', (0, 32*MM), 180)
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('preservation', result['reason'])

    def test_ignored_placement_category_never_establishes_legality(self):
        import json
        from agent_ee.placement_probe import extract
        from agent_ee.reserved_area_native import NativeAlternatives
        self.source.with_suffix('.kicad_pro').write_text(json.dumps({'board': {'design_settings': {
            'rule_severities': {'items_not_allowed': 'ignore', 'missing_courtyard': 'error'}}}}))
        validator = NativeAlternatives(self.source, self.root / 'ignored', extract(self.source), self.policy, {})
        result = validator('J3', (0, 32*MM), 180)
        self.assertEqual(result['status'], 'unknown', result)
        self.assertIn('ignored placement', (self.root / result['witness']).read_text())

    def test_captured_rule_bytes_are_used_and_preserved(self):
        from agent_ee.storage import digest
        original = self.source.with_suffix('.kicad_pro').read_bytes()
        lane, validator = self.run_alternatives('rules')
        self.assertEqual(lane['alternative']['status'], 'native_validated')
        witness = lane['alternative']['native_attempts'][-1]['validation']
        saved = self.root / witness['directory'] / 'candidate.kicad_pro'
        self.assertEqual(saved.read_bytes(), original)
        self.assertEqual(digest(saved), witness['rules']['.kicad_pro'])
        self.assertEqual(self.source.with_suffix('.kicad_pro').read_bytes(), original)

    def test_saved_proposal_judgment_reports_native_alternative_without_moving_proposal(self):
        import json
        import pcbnew as k
        from agent_ee.placement_probe import extract
        from agent_ee.placer import pack
        from agent_ee.placer_runner import _evaluate_proposal
        facts = extract(self.source)
        # Author a planned inboard pose; diagnostic motion remains authorized.
        plan = pack(facts, {**self.policy, 'fixed_refs': ['J3']})
        self.assertEqual(plan['status'], 'planned')
        plan['policy'] = self.policy
        plan['placements'][0]['fixed'] = False
        original_plan = copy.deepcopy(plan)
        output = self.root / 'proposal' / 'candidate.kicad_pcb'
        output.parent.mkdir()
        netlist = b'<export><components><comp ref="J3"/></components><nets/></export>'
        result = _evaluate_proposal(k.LoadBoard(str(self.source)), output, plan, netlist, facts, self.source)
        lane = next(r for r in result['reserved_area']['sides']['B']['owners']
                    if r['kind'] == 'rule_area' and r['owner'] == 'J3')
        self.assertEqual(lane['alternative']['status'], 'native_validated', lane)
        self.assertEqual(lane['alternative']['saving_mm2'], [600, 1])
        self.assertEqual(plan, original_plan)
        saved = extract(output)
        self.assertEqual(saved['footprints'][0]['position_nm'], [45*MM, 32*MM])
        self.assertEqual(result['reserved_area'], json.loads(
            (output.parent / 'placement-judgment.json').read_text())['reserved_area'])
        witness = lane['alternative']['native_attempts'][-1]['validation']
        self.assertTrue((output.parent / witness['directory'] / 'validation.json').is_file())

    def test_unknown_cheaper_attempt_keeps_minimum_unresolved(self):
        from unittest.mock import patch
        from agent_ee import placer_runner
        native = placer_runner._native_drc
        calls = []
        def transient_failure(board, scratch):
            calls.append(str(board))
            if len(calls) == 2:  # Baseline succeeded; first alternative tool failed.
                raise OSError('injected first-alternative tool failure')
            return native(board, scratch)
        with patch('agent_ee.placer_runner._native_drc', side_effect=transient_failure):
            lane, _ = self.run_alternatives('transient')
        alternative = lane['alternative']
        self.assertEqual(alternative['status'], 'native_validated', alternative)
        self.assertFalse(alternative['search_complete'])
        self.assertEqual(alternative['native_attempts'][0]['validation']['status'], 'unknown')
        self.assertEqual(alternative['native_attempts'][-1]['validation']['status'], 'validated')

    def test_captured_custom_rule_is_executed_on_the_alternative(self):
        from agent_ee.placement_probe import extract
        from agent_ee.reserved_area_native import NativeAlternatives
        rules = b'''(version 1)
(rule "Owned fixture pad exclusion"
  (condition "A.Type == 'Pad'")
  (constraint disallow pad))
'''
        self.source.with_suffix('.kicad_dru').write_bytes(rules)
        validator = NativeAlternatives(self.source, self.root / 'custom-rules',
                                       extract(self.source), self.policy, {})
        result = validator('J3', (0, 32*MM), 180)
        self.assertEqual(result['status'], 'rejected', result)
        self.assertIn('items_not_allowed', result['blocking_types'])
        output = self.root / result['directory'] / 'candidate.kicad_dru'
        self.assertEqual(output.read_bytes(), rules)
        self.assertEqual(self.source.with_suffix('.kicad_dru').read_bytes(), rules)

    def test_native_cost_report_is_deterministic_across_drc_timestamps(self):
        from agent_ee.placement_probe import extract
        from agent_ee.reserved_area_native import NativeAlternatives
        facts = extract(self.source); fab = reserve.fabrication_outlines(self.source)
        reports = []
        for name in ('first', 'second'):
            costs = reserve.report(facts, self.policy, fab)
            validator = NativeAlternatives(self.source, self.root / name / 'reserve-alternatives',
                                           facts, self.policy, {})
            reserve.alternatives(facts, self.policy, fab, costs, {}, validator)
            lane = next(r for r in costs['sides']['B']['owners'] if r['kind'] == 'rule_area')
            self.assertEqual(lane['alternative']['status'], 'native_validated', lane)
            selected = lane['alternative']['native_attempts'][-1]['validation']
            self.assertNotIn('drc_sha256', selected)
            self.assertTrue((self.root / name / selected['witness']).is_file())
            reports.append(costs)
        self.assertEqual(reports[0], reports[1])

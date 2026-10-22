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


def validated(costs):
    """Real producer output, as captured JSON, passes the shared packet ingress validator."""
    import json
    return reserve.validate(json.loads(json.dumps(costs)))


# Copied declarations and witnesses whose contents are typed elsewhere, not by the reserve validator.
OPAQUE = ('provenance', 'overhang_envelope', 'write_scope')


def single_field_mutations(record):
    """Every single-field mutation of a valid reserve record, each of which must be refused.

    An extra field on each nested record; wrongly typed values (scalar and container) for
    each field and list element; an inexact, negative or zero-denominator value for each ratio. An owner row
    shown again as a finding's reserve is mutated in both copies, so the allow-list
    itself (not the copies' disagreement) must refuse.
    """
    import json
    def wrong(value):
        return 7 if isinstance(value, str) else 'x'
    def unhashable(value):
        return {} if isinstance(value, list) else []
    def walk(value, path):
        """(label, path of the mutated holder, mutation of that holder)."""
        if isinstance(value, dict):
            yield path + ('<extra>',), path, lambda holder: holder.__setitem__('unexpected', 1)
            items = value.items()
        elif isinstance(value, list):
            items = enumerate(value)
        else:
            return
        for key, item in items:
            for kind in (wrong, unhashable):
                yield (path + (key, kind.__name__), path,
                       lambda holder, key=key, item=item, kind=kind: holder.__setitem__(key, kind(item)))
            if (isinstance(item, list) and len(item) == 2 and all(type(v) is int for v in item)
                    and isinstance(key, str) and (key.endswith('_mm2') or key.endswith('fraction'))):
                for bad in ([-1, 1], [1, 0], [1.0, 1]):
                    yield (path + (key, str(bad)), path,
                           lambda holder, key=key, bad=bad: holder.__setitem__(key, bad))
            if key not in OPAQUE:
                yield from walk(item, path + (key,))
    def at(holder, path):
        for key in path:
            holder = holder[key]
        return holder
    copies = {}
    for i, finding in enumerate(record['findings']):
        side = finding['reserve']['side']
        for j, row in enumerate(record['sides'][side]['owners']):
            if row == finding['reserve']:
                copies[('sides', side, 'owners', j)] = ('findings', i, 'reserve')
    for label, holder, mutate in walk(record, ()):
        mutated = json.loads(json.dumps(record))
        mutate(at(mutated, holder))
        shown = copies.get(holder[:4])
        if shown is not None:
            mutate(at(mutated, shown + holder[4:]))
        yield '/'.join(map(str, label)), mutated


def assert_closed(test, record):
    """``record`` is accepted, and every single-field mutation of it is refused."""
    import json
    validated(record)
    count = 0
    for label, mutated in single_field_mutations(json.loads(json.dumps(record))):
        count += 1
        with test.subTest(mutation=label):
            with test.assertRaises(ValueError):
                reserve.validate(mutated)
    test.assertGreater(count, 0)
    return count


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
        assert_closed(self, report)

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
        assert_closed(self, report)

    def test_cheaper_pose_cannot_leave_a_sourced_region(self):
        facts = capture([part('J3', 8, 8, 'B')])
        facts['rule_areas'] = [{'uuid': 'lane', 'name': 'card', 'owner': 'J3',
            'layers': ['B.Cu'], 'forbid': {'footprints': True, 'pads': False},
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
        assert_closed(self, costs)

    def test_geometry_only_no_saving_is_not_a_complete_native_search(self):
        row = {'side': 'B', 'kind': 'rule_area', 'id': 'lane', 'owner': 'J3', 'area_mm2': [2, 1]}
        trials = [(((0, 0), 0), {('B', 'rule_area', 'lane', 'J3'): {'area_mm2': [2, 1]}})]
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
        assert_closed(self, costs)

    def test_fixed_owner_overhang_alternative_stays_inside_its_declared_band(self):
        from agent_ee import placer
        from agent_ee.placement import overhang_bands
        facts = capture([part('U1', 5, 5)])
        fab = {'U1': {'F': proven([rectangle(5, 5, 1, 2)])}}
        def band(max_nm):
            return [{'reference': 'U1', 'source': {'envelope_id': 'ee384-fixture', 'envelope_sha256': 'b'*64,
                                                   'pointer': '/fixed/U1'},
                     'edges': [{'name': 'left', 'start_nm': [0, 20*MM], 'end_nm': [0, 0],
                                'outward': 'x-', 'max_nm': max_nm}]}]
        outline = rectangle(0, 0, 20, 20)
        savings = []
        for max_nm in (MM, MM // 2):
            policy = request(gap_nm=0, fixed_overhangs=band(max_nm))
            costs = reserve.report(facts, policy, fab)
            unchanged = copy.deepcopy(facts)
            reserve.alternatives(facts, policy, fab, costs, {})
            self.assertEqual(facts, unchanged)
            row = costs['sides']['F']['owners'][0]
            self.assertEqual((row['kind'], row['area_mm2']), ('courtyard_excess', [2, 1]))
            decision = row['alternative']
            self.assertEqual(decision['status'], 'decision_required', decision)
            self.assertEqual(decision['fixed_source']['overhang_envelope'], policy['fixed_overhangs'][0])
            request_ = decision['decision_required']
            candidate = placer.transform(facts['footprints'][0], request_['candidate_position_nm'],
                                         request_['candidate_rotation_deg'])
            # The candidate leaves the board only inside the declared envelope band.
            self.assertFalse(placer._fits(candidate, outline))
            self.assertTrue(placer._fits(candidate, outline, 0,
                                         overhang_bands(policy['fixed_overhangs'], outline)['U1']))
            savings.append(request_['saving_mm2'])
            assert_closed(self, costs)
        self.assertEqual(savings, [[2, 1], [1, 1]])
        # Without a declared band, a contained fixed courtyard has no cheaper pose.
        policy = request(gap_nm=0)
        costs = reserve.report(facts, policy, fab)
        reserve.alternatives(facts, policy, fab, costs, {})
        self.assertEqual(costs['sides']['F']['owners'][0]['alternative']['status'], 'no_cheaper_contained_pose')

    def test_rigid_block_member_fixed_elsewhere_is_not_searched(self):
        facts = capture([part('U1', 2, 2), part('C1', 8, 8)])
        policy = request(gap_nm=0, obstacles=[], fixed_refs=['U1'])
        fab = {'U1': {'F': proven([rectangle(2, 2, 1, 1)])}, 'C1': {'F': proven([rectangle(8, 8, 1, 1)])}}
        facts['rule_areas'] = [{'uuid': 'lane', 'name': 'lane', 'owner': 'C1', 'layers': ['F.Cu'],
                                'forbid': {'footprints': True}, 'outline': proven([rectangle(10, 8, 4, 2)])}]
        costs = reserve.report(facts, policy, fab)
        reserve.alternatives(facts, policy, fab, costs, {'placements': [], 'block_arrangement': {
            'arrangement': {}, 'blocks': [{'block': 'b', 'anchor': 'C1', 'references': ['C1', 'U1']}]}})
        lane = next(r for r in costs['sides']['F']['owners'] if r['kind'] == 'rule_area')
        self.assertEqual(lane['alternative'], {'status': 'not_searched',
                                               'reason': 'rigid block with an owner-fixed member'})
        assert_closed(self, costs)

    def test_unknown_fab_curves_and_outline_do_not_become_zero(self):
        facts = capture([part('U1', 2, 2)])
        report = reserve.report(facts, request(), {})
        self.assertEqual(report['status'], 'unknown')
        self.assertNotIn('union_area_mm2', report['sides']['F'])
        self.assertEqual(report['sides']['F']['owners'][0]['status'], 'unknown')
        facts['footprints'][0]['courtyards']['F']['inner_polygons'] = [rectangle(2, 2, 1, 1)]
        report = reserve.report(facts, request(), {'U1': {'F': proven([rectangle(2, 2, 1, 1)])}})
        self.assertIn('bounded', report['sides']['F']['owners'][0]['reason'])
        assert_closed(self, report)
        facts['outline'] = {'status': 'unsupported'}
        self.assertEqual(reserve.report(facts, request(), {})['status'], 'unknown')
        assert_closed(self, reserve.report(facts, request(), {}))
        assert_closed(self, reserve.unmeasured('a'*64, 'offline_unsaved_plan', 'no saved board'))

    def test_threshold_is_strictly_above_five_percent_and_advisory(self):
        facts = capture([])
        policy = request(obstacles=[{'id': 'lane', 'side': 'B', 'polygons': [rectangle(0, 0, 5, 4)]}])
        self.assertEqual(reserve.report(facts, policy, {})['findings'], [])
        policy['obstacles'][0]['polygons'] = [rectangle(0, 0, 6, 4)]
        report = reserve.report(facts, policy, {})
        self.assertEqual(report['findings'][0]['reserve']['owner'], 'lane')
        self.assertEqual(report['findings'][0]['enforcement'], 'advisory')
        self.assertEqual(report['threshold']['provenance'], 'AGENTEE-380 suggestion; unsourced')
        assert_closed(self, report)


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
            assert_closed(self, report)
            # AGENTEE-384: a fixed owner is never moved; its saving is a named owner decision.
            fixed = reserve.report(facts, policy, fab)
            reserve.alternatives(facts, {**policy, 'fixed_refs': ['J3']}, fab, fixed, {})
            self.assertEqual(facts, unchanged)
            decision = next(r for r in fixed['sides']['B']['owners'] if r['kind'] == 'rule_area')['alternative']
            self.assertEqual(decision['status'], 'decision_required')
            self.assertEqual(decision['validation_status'], 'geometry_qualified_native_unverified')
            self.assertIs(decision['applied'], False)
            self.assertEqual(decision['decision_required']['saving_mm2'], [600, 1])
            self.assertEqual(decision['decision_required']['current_position_nm'], [45*MM, 32*MM])
            self.assertEqual(decision['fixed_source']['declared_by'], ['policy fixed_refs'])
            assert_closed(self, fixed)
            # A rigid block moves as one: here the block is J3 alone.
            rigid = reserve.report(facts, policy, fab)
            reserve.alternatives(facts, policy, fab, rigid,
                                 {'placements': [{'ref': 'J3', 'fixed': False}], 'block_arrangement': {
                                     'arrangement': {}, 'blocks': [{'block': 'card', 'references': ['J3']}]}})
            moved = next(r for r in rigid['sides']['B']['owners'] if r['kind'] == 'rule_area')['alternative']
            self.assertEqual(moved['status'], 'geometry_qualified_native_unverified')
            self.assertEqual(moved['rigid_block'], {'block': 'card', 'anchor': 'J3', 'references': ['J3']})
            self.assertEqual(moved['saving_mm2'], [600, 1])
            assert_closed(self, rigid)
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

    def test_advisory_failures_cannot_refuse_publication(self):
        from unittest.mock import patch
        from test_placer_native import NativePlacerTest
        from agent_ee.placer_runner import place_candidate
        for target, error in (
                ('agent_ee.reserved_area.fabrication_outlines', ValueError('bad fabrication')),
                ('agent_ee.reserved_area.report', OSError('measurement unavailable')),
                ('agent_ee.reserved_area.alternatives', KeyError('unexpected search failure')),
                ('agent_ee.reserved_area_native.NativeAlternatives', OSError('validator initialization failed'))):
            with self.subTest(target=target):
                fixture = NativePlacerTest(); fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                with patch(target, side_effect=error):
                    result = place_candidate(fixture.source, fixture.netlist,
                                             fixture.scratch / 'advisory-failure', fixture.config)
                self.assertEqual(result['status'], 'placed', result)
                self.assertEqual(result['reserved_area']['status'], 'unknown')
                self.assertEqual(result['reserved_area']['sides'], {})
                self.assertIn(str(error), result['reserved_area']['reason'])
                self.assertEqual(result['reserved_area'], result['judgment']['reserved_area'])


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

    def generic_scope(self, document, board_text='(kicad_pcb (version 20260206) (generator "owned"))\n'):
        """A scope without block provenance that declares a captured reserve record."""
        import json
        from agent_ee.candidate import capture as capture_candidate
        from agent_ee.cli import validate_scope
        root = Path(tempfile.mkdtemp(prefix='agent-ee-reserve-review-', dir=self.dir))
        source = root / 'source'
        source.mkdir()
        (source / 'pod.kicad_sch').write_text('(kicad_sch (version 20260206))\n')
        (source / 'pod.kicad_pcb').write_text(board_text)
        (source / 'placement-judgment.json').write_text(json.dumps(document, sort_keys=True))
        scope = {'schema': 'agent-ee.scope/1', 'name': 'placed', 'scope': 'Saved placement review',
                 'schematic': 'project/pod.kicad_sch', 'board': 'project/pod.kicad_pcb',
                 'reserved_area': 'project/placement-judgment.json',
                 'checks': [{'id': 'drc', 'kind': 'drc'}], 'inputs': [{'path': 'source', 'at': 'project'}],
                 'review_profile': json.loads((provenance_tests.ROOT / 'profiles/claude.json').read_text())}
        validate_scope(scope)
        (root / 'scope.json').write_text(json.dumps(scope))
        run = root / 'run'
        run.mkdir()
        return run, capture_candidate(root / 'scope.json', run), source

    def test_generic_scope_without_block_provenance_carries_bound_reserve(self):
        import hashlib
        from agent_ee.reviewers import build_packet
        board_text = '(kicad_pcb (version 20260206) (generator "owned"))\n'
        facts = capture([])
        facts['board_sha256'] = hashlib.sha256(board_text.encode()).hexdigest()
        costs = reserve.report(facts, request(obstacles=[
            {'id': 'large-lane', 'side': 'B', 'polygons': [rectangle(0, 0, 20, 10)]}]), {})
        run, manifest, _ = self.generic_scope({'schema': 'agent-ee.placement-judgment/1',
                                               'reserved_area': costs})
        packet = build_packet(run, [], 'ee384')
        self.assertNotIn('block_provenance', packet)
        carried = packet['reserved_area']
        self.assertEqual(carried['record'], costs)
        self.assertEqual(carried['path'], 'inputs/project/placement-judgment.json')
        self.assertEqual(carried['sha256'], manifest['files']['project/placement-judgment.json'])
        self.assertEqual(carried['record']['findings'][0]['reserve']['owner'], 'large-lane')
        # A bare reserve document is accepted too; another board's record never is.
        run, _, _ = self.generic_scope(costs)
        self.assertEqual(build_packet(run, [], 'ee384')['reserved_area']['record'], costs)
        other = {**costs, 'board_sha256': '0'*64}
        run, _, _ = self.generic_scope({'reserved_area': other})
        with self.assertRaisesRegex(ValueError, 'not the captured'):
            build_packet(run, [], 'ee384')
        run, _, _ = self.generic_scope({'schema': 'agent-ee.placement-judgment/1'})
        with self.assertRaisesRegex(ValueError, 'carries no'):
            build_packet(run, [], 'ee384')
        # A record changed after capture is stale evidence, never carried.
        run, _, source = self.generic_scope(costs)
        (run / 'inputs' / 'project' / 'placement-judgment.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'stale'):
            build_packet(run, [], 'ee384')

    def test_malformed_or_hard_reserve_record_is_refused_before_reviewers(self):
        import copy, hashlib
        from agent_ee.block_arrangement import provenance_artifact
        from agent_ee.reviewers import build_packet
        board_text = '(kicad_pcb (version 20260206) (generator "owned"))\n'
        facts = capture([])
        facts['board_sha256'] = hashlib.sha256(board_text.encode()).hexdigest()
        valid = reserve.report(facts, request(), {})
        self.assertEqual(valid['status'], 'measured')
        variants = {}
        r = copy.deepcopy(valid); r.pop('status'); variants['missing status'] = (r, 'status')
        r = copy.deepcopy(valid); r['status'] = 'pass'; variants['unknown status word'] = (r, 'status')
        r = copy.deepcopy(valid); r['sides'] = {}; variants['measured without sides'] = (r, 'both sides')
        r = copy.deepcopy(valid)
        for s in r['sides'].values():
            s['status'] = 'unknown'; s['reason'] = 'review unavailable'
            s.pop('union_area_mm2'); s.pop('side_fraction')
        variants['all unknown sides but top status measured'] = (r, 'both sides')
        r = copy.deepcopy(valid); r['sides']['F']['union_area_mm2'] = [1, 0]
        variants['zero denominator'] = (r, 'positive denominator')
        r = copy.deepcopy(valid); r['sides']['B']['side_fraction'] = [-1, 2]
        variants['negative area'] = (r, 'non-negative')
        r = copy.deepcopy(valid); r['board_area_mm2'] = [1.5, 2]
        variants['inexact ratio'] = (r, 'exact')
        r = copy.deepcopy(valid); r['authority'] = 'hard gating requirement'
        variants['hard authority'] = (r, 'authority')
        r = copy.deepcopy(valid); r.pop('board_sha256'); variants['no board identity'] = (r, 'board_sha256')
        r = copy.deepcopy(valid); r['board_sha256'] = None; variants['null board identity'] = (r, 'board_sha256')
        for label, (record, reason) in variants.items():
            with self.subTest(label):
                run, _, _ = self.generic_scope(record, board_text)
                with self.assertRaisesRegex(ValueError, reason):
                    build_packet(run, [], 'ee384')
                with self.assertRaisesRegex(ValueError, reason):
                    reserve.validate(record)
        # The block-provenance ingress uses the same validator.
        artifact = self.bound()
        with self.assertRaisesRegex(ValueError, 'authority'):
            provenance_artifact(self.plan, artifact['board_sha256'],
                plan_sha256=hashlib.sha256(self.plan_bytes).hexdigest(),
                reserved_area={**valid, 'board_sha256': artifact['board_sha256'], 'authority': 'hard'})
        # Typed unknowns stay carriable as unknown; a measured owner under an unknown side is fine.
        unknown = reserve.unmeasured(facts['board_sha256'], 'offline_unsaved_plan', 'no saved board')
        run, _, _ = self.generic_scope(unknown, board_text)
        self.assertEqual(build_packet(run, [], 'ee384')['reserved_area']['record']['status'], 'unknown')
        r = copy.deepcopy(valid); r['sides']['F']['owners'] = [
            {'owner': 'X', 'kind': 'declared_obstacle', 'side': 'F', 'status': 'unknown', 'reason': 'r'}]
        with self.assertRaisesRegex(ValueError, 'owner is unknown'):
            reserve.validate(r)
        r['sides']['F'].update(status='unknown', reason='unmeasured owners'); r['status'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'not allowed side_fraction, union_area_mm2'):
            reserve.validate(r)
        for key in ('union_area_mm2', 'side_fraction'):
            r['sides']['F'].pop(key)
        self.assertIs(reserve.validate(r), r)

    def assert_both_ingress_paths(self, record, reason=None):
        """Carry ``record`` through a generic scope and a bound block provenance scope.

        With ``reason`` both packets must be refused before any reviewer sees them.
        """
        import hashlib, json
        from agent_ee.block_arrangement import provenance_artifact
        from agent_ee.reviewers import build_packet
        record = json.loads(json.dumps(record))
        bound = self.bound()
        artifact = provenance_artifact(self.plan, bound['board_sha256'],
            plan_sha256=hashlib.sha256(self.plan_bytes).hexdigest())
        artifact['reserved_area'] = {**record, 'board_sha256': bound['board_sha256']}
        generic, _, _ = self.generic_scope(record)
        block, _, _ = self.scope(artifact)
        if reason is None:
            self.assertEqual(build_packet(generic, [], 'ee384')['reserved_area']['record'], record)
            self.assertEqual(build_packet(block, [], 'ee384')['block_provenance']['record']['reserved_area'],
                             artifact['reserved_area'])
            return
        for run in (generic, block):
            with self.assertRaisesRegex(ValueError, reason):
                build_packet(run, [], 'ee384')
        with self.assertRaisesRegex(ValueError, reason):
            reserve.validate(record)

    def test_nested_reserve_evidence_is_validated_before_reviewers(self):
        import hashlib, json
        from agent_ee import placer
        board_text = '(kicad_pcb (version 20260206) (generator "owned"))\n'
        facts = capture([])
        facts['board_sha256'] = hashlib.sha256(board_text.encode()).hexdigest()
        # Round-trip separates the producer's shared side and finding rows, as captured JSON does.
        valid = json.loads(json.dumps(reserve.report(facts, request(obstacles=[
            {'id': 'lane', 'side': 'F', 'polygons': [rectangle(0, 0, 20, 10)]}]), {})))
        self.assertEqual((valid['status'], len(valid['findings'])), ('measured', 1))
        area = valid['sides']['F']['owners'][0]['area_mm2']

        def attempt(status, x=0):
            return {'position_nm': [x, 0], 'rotation_deg': 0,
                    'validation': {'status': status, 'qualification': reserve.QUALIFICATION[status]}}

        validated = {'status': 'native_validated', 'qualification': 'legal under captured rules',
                     'native_attempts': [attempt('unknown', 1), attempt('validated')], 'search_complete': False,
                     'position_nm': [0, 0], 'rotation_deg': 0, 'area_mm2': [0, 1], 'saving_mm2': area,
                     'tested': 4, 'domain': reserve.DOMAIN}
        fixed_source = {'declared_by': ['policy fixed_refs'], 'provenance': placer.UNDECLARED}
        decision = {**validated, 'status': 'decision_required', 'validation_status': 'native_validated',
                    'applied': False, 'fixed_source': fixed_source, 'enforcement': reserve.FIXED_ENFORCEMENT,
                    'decision_required': {
                        'decision': 'change owner-fixed pose', 'reference': 'lane',
                        'fixed_source': fixed_source,
                        'current_position_nm': [5, 5], 'current_rotation_deg': 0,
                        'candidate_position_nm': [0, 0], 'candidate_rotation_deg': 0,
                        'saving_mm2': area, 'qualification': 'legal under captured rules'}}

        def with_alternative(alternative, finding=True):
            record = copy.deepcopy(valid)
            record['sides']['F']['owners'][0]['alternative'] = copy.deepcopy(alternative)
            if finding:
                record['findings'][0]['reserve']['alternative'] = copy.deepcopy(alternative)
            return record

        # The legitimate mixed case: an unknown cheaper attempt, then a validated
        # choice whose search stays incomplete. Owner decisions and geometry-only
        # diagnostics with consistent records are carried too.
        geometry = {**validated, 'status': 'geometry_qualified_native_unverified',
                    'qualification': 'hypothetical, unverified', 'native_attempts': []}
        for label, alternative in (('mixed unknown then validated', validated), ('owner decision', decision),
                                   ('geometry only', geometry)):
            with self.subTest(accepted=label):
                self.assert_both_ingress_paths(with_alternative(alternative))

        variants = {}
        # The four review probe variants, exactly as reproduced.
        r = copy.deepcopy(valid); r['findings'][0]['reserve']['area_mm2'] = [1, 0]
        variants['finding measured area zero denominator'] = (r, 'positive denominator')
        probe = {'status': 'native_validated', 'qualification': 'legal under captured rules',
                 'native_attempts': [{'validation': {'status': 'unknown'}}]}
        variants['probe native validated invalid saving, unknown witness'] = (
            with_alternative({**probe, 'saving_mm2': [-1, 0]}, finding=False), 'alternative')
        variants['probe native validated valid saving, unknown witness'] = (
            with_alternative({**probe, 'saving_mm2': [1, 1]}, finding=False), 'alternative')
        r = copy.deepcopy(valid); r['findings'][0]['reserve'].update(status='unknown', area_mm2=[1, 0])
        variants['finding reserve contradicts measured owner'] = (r, 'missing reason; not allowed area_mm2')
        # The same defects with side and finding copies kept equal, so the nested validator itself refuses.
        variants['native validated negative saving'] = (
            with_alternative({**validated, 'saving_mm2': [-1, 1]}), 'non-negative')
        variants['native validated zero-denominator saving'] = (
            with_alternative({**validated, 'saving_mm2': [-1, 0]}), 'positive denominator')
        variants['native validated with only unknown attempts'] = (
            with_alternative({**validated, 'native_attempts': [attempt('unknown')]}), 'validated native attempt')
        variants['native validated with no attempt'] = (
            with_alternative({**validated, 'native_attempts': []}), 'validated native attempt')
        variants['native validated pose is not the validated attempt'] = (
            with_alternative({**validated, 'position_nm': [9, 9]}), 'validated native attempt')
        variants['complete search after an unknown attempt'] = (
            with_alternative({**validated, 'search_complete': True}), 'search_complete')
        variants['attempt validation without its qualification'] = (
            with_alternative({**validated, 'native_attempts': [
                {'position_nm': [0, 0], 'rotation_deg': 0, 'validation': {'status': 'validated'}}]}),
            'fixed qualification')
        variants['attempt validation status word'] = (
            with_alternative({**validated, 'native_attempts': [
                {**attempt('validated'), 'validation': {'status': 'pass', 'qualification': 'legal'}}]}),
            'fixed qualification')
        variants['saving disagrees with owner area'] = (
            with_alternative({**validated, 'saving_mm2': [1, 1]}), 'owner area less')
        variants['inexact alternative area'] = (
            with_alternative({**validated, 'area_mm2': [0.0, 1]}), 'exact')
        variants['validated alternative qualified hypothetical'] = (
            with_alternative({**validated, 'qualification': 'hypothetical, unverified'}), 'qualified')
        variants['geometry-only alternative qualified legal'] = (
            with_alternative({**geometry, 'qualification': 'legal under captured rules'}), 'qualified')
        variants['geometry-only alternative with native attempts'] = (
            with_alternative({**geometry, 'native_attempts': [attempt('validated')]}), 'geometry-only')
        variants['unknown alternative status word'] = (
            with_alternative({**validated, 'status': 'legal'}), 'alternative status must be one of')
        variants['unknown alternative carrying a saving'] = (
            with_alternative({**validated, 'status': 'unknown', 'qualification': 'hypothetical, unverified'}),
            'not allowed area_mm2, position_nm, rotation_deg, saving_mm2')
        variants['unknown alternative with only rejected attempts'] = (
            with_alternative({'status': 'unknown', 'qualification': 'hypothetical, unverified',
                              'search_complete': False, 'tested': 2, 'domain': reserve.DOMAIN,
                              'native_attempts': [attempt('rejected')], 'reason': 'r'}), 'unknown native attempt')
        variants['none found after an unknown attempt'] = (
            with_alternative({'status': 'none_found', 'search_complete': True, 'tested': 2,
                              'domain': reserve.DOMAIN, 'native_attempts': [attempt('unknown')],
                              'reason': 'r'}), 'none_found')
        variants['already zero on a nonzero owner'] = (
            with_alternative({'status': 'already_zero', 'reason': 'r'}), 'already_zero')
        variants['decision with unknown validation status'] = (
            with_alternative({**decision, 'validation_status': 'unknown'}), 'validation_status')
        variants['decision applied'] = (with_alternative({**decision, 'applied': True}), 'applied false')
        variants['decision record disagrees with its saving'] = (
            with_alternative({**decision, 'decision_required': {**decision['decision_required'],
                                                                'saving_mm2': [1, 1]}}), 'disagrees')
        variants['decision of a native validated pose with only unknown attempts'] = (
            with_alternative({**decision, 'native_attempts': [attempt('unknown')]}), 'validated native attempt')
        r = with_alternative(validated)
        r['sides']['F']['owners'][0]['status'] = 'unknown'
        variants['alternative on an unmeasured owner'] = (r, 'not allowed alternative, area_mm2, side_fraction')
        for key, value, reason in (('owner', 'other', 'disagrees with its side owner'),
                                   ('kind', 'rule_area', 'missing id, name'),
                                   ('id', 'zone', 'not allowed id'),
                                   ('side', 'B', 'disagrees with its side owner'),
                                   ('kind', 'pass', 'kind must be one of')):
            r = copy.deepcopy(valid); r['findings'][0]['reserve'][key] = value
            variants[f'finding reserve {key} {value}'] = (r, reason)
        r = copy.deepcopy(valid); r['findings'][0]['reserve']['area_mm2'] = [1, 1]
        variants['finding reserve area differs from its owner'] = (r, 'disagrees with its side owner')
        r = copy.deepcopy(valid); r['findings'][0].pop('reserve')
        variants['finding without its reserve'] = (r, 'naming its reserve')
        for label, (record, reason) in variants.items():
            with self.subTest(label):
                self.assert_both_ingress_paths(record, reason)

    def test_closed_allow_list_refuses_review_probe_variants_on_both_paths(self):
        """Third-review probe: unsearched, none_found and unknown alternatives carry no
        selected-pose measurement and no legal qualification, on either packet path."""
        import hashlib, json
        board_text = '(kicad_pcb (version 20260206) (generator "owned"))\n'
        facts = capture([])
        facts['board_sha256'] = hashlib.sha256(board_text.encode()).hexdigest()
        valid = json.loads(json.dumps(reserve.report(facts, request(obstacles=[
            {'id': 'lane', 'side': 'F', 'polygons': [rectangle(0, 0, 20, 10)]}]), {})))

        def with_alternative(alternative):
            record = copy.deepcopy(valid)
            record['sides']['F']['owners'][0]['alternative'] = copy.deepcopy(alternative)
            record['findings'][0]['reserve']['alternative'] = copy.deepcopy(alternative)
            return record

        unknown_attempt = {'position_nm': [0, 0], 'rotation_deg': 0,
                           'validation': {'status': 'unknown', 'qualification': reserve.QUALIFICATION['unknown']}}
        bases = {status: {'status': status, 'reason': 'fixture'}
                 for status in ('not_searched', 'no_cheaper_contained_pose')}
        bases['none_found'] = {'status': 'none_found', 'reason': 'fixture', 'tested': 1, 'search_complete': False,
                               'domain': reserve.DOMAIN, 'native_attempts': []}
        bases['unknown'] = {'status': 'unknown', 'reason': 'fixture', 'tested': 1, 'search_complete': False,
                            'domain': reserve.DOMAIN, 'native_attempts': [unknown_attempt],
                            'qualification': 'hypothetical, unverified'}
        for status, base in bases.items():
            with self.subTest(accepted=status):
                self.assert_both_ingress_paths(with_alternative(base))
                assert_closed(self, with_alternative(base))
            with self.subTest(status=status, defect='malformed area'):
                self.assert_both_ingress_paths(with_alternative({**base, 'area_mm2': [-1, 0]}),
                                               'not allowed area_mm2')
            if status != 'unknown':
                with self.subTest(status=status, defect='legal qualification without validation'):
                    self.assert_both_ingress_paths(
                        with_alternative({**base, 'qualification': 'legal under captured rules'}),
                        'not allowed qualification')
        legal_unknown = {**bases['unknown'], 'qualification': 'legal under captured rules'}
        with self.subTest(status='unknown', defect='legal qualification'):
            self.assert_both_ingress_paths(with_alternative(legal_unknown), "qualified 'hypothetical, unverified'")
        for status in bases:
            for key, value in (('saving_mm2', [1, 1]), ('position_nm', [0, 0]), ('rotation_deg', 0),
                               ('decision_required', {})):
                with self.subTest(status=status, smuggled=key):
                    self.assert_both_ingress_paths(with_alternative({**bases[status], key: value}),
                                                   f'not allowed {key}')

    def test_uncaptured_scope_board_never_binds_a_reserve(self):
        import json
        from agent_ee.candidate import capture as capture_candidate
        from agent_ee.cli import validate_scope
        from agent_ee.reviewers import build_packet
        for record in ({'schema': reserve.SCHEMA, 'status': 'measured', 'sides': {}, 'findings': []},
                       reserve.unmeasured(None, 'offline_unsaved_plan', 'no saved board')):
            with self.subTest(board_sha256=record.get('board_sha256', 'absent')):
                run, _, _ = self.generic_scope(record)
                root = run.parent
                scope = json.loads((root / 'scope.json').read_text())
                scope['board'] = 'project/uncaptured.kicad_pcb'
                validate_scope(scope)
                (root / 'scope.json').write_text(json.dumps(scope))
                second = root / 'missing-board-run'
                second.mkdir()
                manifest = capture_candidate(root / 'scope.json', second)
                self.assertNotIn(scope['board'], manifest['files'])
                with self.assertRaisesRegex(ValueError, 'not a captured input'):
                    reserve.review_input(second, manifest)
                with self.assertRaisesRegex(ValueError, 'not a captured input'):
                    build_packet(second, [], 'ee384')

    def test_offline_cli_reserve_is_a_typed_unknown_and_names_the_runner_section(self):
        import json
        from agent_ee import block_arrangement as arrangement, blocks
        from test_block_arrangement import plan_policy
        from test_blocks import PARTS, board, native_xml
        root = self.dir / 'offline'
        root.mkdir()
        two = native_xml(PARTS)
        (root / 'design.xml').write_bytes(two)
        target = board(PARTS)
        target['outline']['polygons'] = [rectangle(0, 0, 60, 40)]
        (root / 'probe.json').write_text(json.dumps(target))
        (root / 'policy.json').write_text(json.dumps(self.policy))
        (root / 'arrangement.json').write_text(json.dumps(plan_policy()))
        inv, names, sha = self.inventory(two)
        derivation = blocks.derive(inv, names, target, inventory_sha256=sha)
        paths = []
        for name in ('Power', 'Sensor'):
            (root / f'{name}.json').write_text(json.dumps(
                blocks.pack_block(target, inv, derivation, name, self.policy)['candidate']))
            paths += ['--candidate', str(root / f'{name}.json')]
        self.assertEqual(arrangement.main([
            '--probe', str(root / 'probe.json'), '--netlist', str(root / 'design.xml'),
            '--policy', str(root / 'policy.json'), '--arrangement', str(root / 'arrangement.json'),
            '--out', str(root / 'plan.json'), '--provenance', str(root / 'provenance.json'),
            '--runner-section', str(root / 'section.json')] + paths), 0)
        written = arrangement.validate_provenance(json.loads((root / 'provenance.json').read_text()))
        unknown = written['reserved_area']
        self.assertEqual((unknown['status'], unknown['limitation']), ('unknown', 'offline_unsaved_plan'))
        self.assertEqual((unknown['sides'], unknown['findings']), ({}, []))
        self.assertNotIn('union_area_mm2', json.dumps(unknown))
        self.assertIn('--runner-section', unknown['reason'])
        self.assertEqual(json.loads((root / 'section.json').read_text()), arrangement.request_section(
            None, root / 'arrangement.json', [root / 'Power.json', root / 'Sensor.json']))


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
        assert_closed(self, costs)
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

    def test_unrelated_baseline_defect_is_unknown_not_pose_rejection(self):
        import pcbnew as k
        from agent_ee.placement_probe import extract
        from agent_ee.reserved_area_native import NativeAlternatives
        from agent_ee.storage import read_json
        board = k.LoadBoard(str(self.source))
        peer = k.FOOTPRINT(board); peer.SetReference('U9'); board.Add(peer)
        peer.Reference().SetVisible(False); peer.Value().SetVisible(False)
        peer.Flip(k.VECTOR2I(0, 0), False)
        for layer in (k.B_CrtYd, k.B_Fab):
            contour = rectangle(-1, -1, 2, 2)
            for a, b in zip(contour, contour[1:]+contour[:1]):
                shape = k.PCB_SHAPE(peer); shape.SetShape(k.SHAPE_T_SEGMENT)
                shape.SetLayer(layer); shape.SetWidth(50000)
                shape.SetStart(k.VECTOR2I(*a)); shape.SetEnd(k.VECTOR2I(*b)); peer.Add(shape)
        pad = k.PAD(peer); pad.SetNumber('1'); pad.SetAttribute(k.PAD_ATTRIB_SMD)
        pad.SetShape(k.PAD_SHAPE_RECT); pad.SetSize(k.VECTOR2I(MM, MM))
        layers = k.LSET(); layers.AddLayer(k.B_Cu); pad.SetLayerSet(layers)
        peer.Add(pad); peer.SetPosition(k.VECTOR2I(75*MM, 55*MM))
        zone = k.ZONE(board); zone.SetIsRuleArea(True)
        zone.SetDoNotAllowTracks(True); zone.SetDoNotAllowPads(False)
        zone.SetLayer(k.B_Cu); zone.SetZoneName('unrelated baseline pad exclusion')
        polygon = zone.Outline(); polygon.NewOutline()
        for x, y in rectangle(74, 54, 2, 2): polygon.Append(x, y)
        board.Add(zone); self.assertTrue(k.SaveBoard(str(self.source), board))
        # Ruling 418b: a native-only pad rule, so the pack keeps the pose and native DRC attributes it.
        self.source.with_suffix('.kicad_dru').write_text(
            '(version 1)\n(rule "unrelated baseline pad exclusion"\n  (constraint disallow pad)\n'
            '  (condition "A.intersectsArea(\'unrelated baseline pad exclusion\')"))\n')
        facts = extract(self.source)
        validator = NativeAlternatives(self.source, self.root / 'baseline-defect', facts, self.policy, {})
        result = validator('J3', (0, 32*MM), 180)
        self.assertEqual(result['status'], 'unknown', result)
        self.assertIn('baseline', read_json(self.root / result['witness'])['reason'])
        self.assertIn('items_not_allowed', result['blocking_types'])
        self.assertTrue(any(v['type'] == 'items_not_allowed' for v in validator.baseline['violations']))
        lane, _ = self.run_alternatives('baseline-search')
        self.assert_unknown_search(lane['alternative'], 'baseline')

        # The same retained defect cannot hide a new exclusion at the trial pose.
        zone = k.ZONE(board); zone.SetIsRuleArea(True)
        zone.SetDoNotAllowTracks(True); zone.SetDoNotAllowPads(False)
        zone.SetLayer(k.B_Cu); zone.SetZoneName('new candidate pad exclusion')
        polygon = zone.Outline(); polygon.NewOutline()
        for x, y in rectangle(0, 31, 2, 2): polygon.Append(x, y)
        board.Add(zone); self.assertTrue(k.SaveBoard(str(self.source), board))
        with self.source.with_suffix('.kicad_dru').open('a') as rules:
            rules.write('(rule "new candidate pad exclusion"\n  (constraint disallow pad)\n'
                        '  (condition "A.intersectsArea(\'new candidate pad exclusion\')"))\n')
        validator = NativeAlternatives(self.source, self.root / 'baseline-plus-new',
                                       extract(self.source), self.policy, {})
        result = validator('J3', (0, 32*MM), 180)
        self.assertEqual(result['status'], 'rejected', result)
        self.assertIn('items_not_allowed', result['blocking_types'])

    def test_cost_summary_binds_normalized_native_drc(self):
        import hashlib
        import json
        from agent_ee.storage import read_json
        lane, _ = self.run_alternatives('bound-drc')
        validation = lane['alternative']['native_attempts'][-1]['validation']
        for prefix, path in (('', self.root / validation['directory'] / 'native-drc.json'),
                             ('baseline_', self.root / 'bound-drc/baseline/native-drc.json')):
            report = read_json(path)
            report.pop('date', None); report.pop('source', None)
            for key in ('violations', 'unconnected_items', 'ignored_checks', 'included_severities'):
                report[key] = sorted(report[key], key=lambda row: json.dumps(row, sort_keys=True))
            canonical = json.dumps(report, sort_keys=True, separators=(',', ':')).encode()
            self.assertEqual(validation[prefix + 'drc_content_sha256'], hashlib.sha256(canonical).hexdigest())
            from agent_ee.reserved_area_native import drc_content_digest
            report['date'] = 'different execution date'
            report['source'] = 'another scratch path'
            for key in ('violations', 'unconnected_items', 'ignored_checks', 'included_severities'):
                report[key].reverse()
            path.write_text(json.dumps(report))
            self.assertEqual(drc_content_digest(path), validation[prefix + 'drc_content_sha256'])
            report.pop('date'); report.pop('source')
            report['violations'].append({'type': 'tamper'})
            path.write_text(json.dumps(report))
            self.assertNotEqual(validation[prefix + 'drc_content_sha256'], drc_content_digest(path))

    def test_unexpected_validator_exception_is_retained_unknown(self):
        from unittest.mock import patch
        with patch('agent_ee.reserved_area_native.NativeAlternatives._validate',
                   side_effect=KeyError('injected validator failure')):
            lane, _ = self.run_alternatives('unexpected')
        self.assert_unknown_search(lane['alternative'], 'injected validator failure')

    def native_only_pad_rule(self, name):
        """A tracks-only rule area plus a custom DRC rule disallowing pads that meet it.

        Ruling 418a: the packing predicate binds rule areas forbidding pads or
        footprints (AGENTEE-418), not custom .kicad_dru constraints, so the pose
        over this area passes the predicates and only native DRC rejects it.
        """
        import pcbnew as k
        board = k.LoadBoard(str(self.source))
        zone = k.ZONE(board); zone.SetIsRuleArea(True); zone.SetDoNotAllowTracks(True)
        zone.SetDoNotAllowPads(False); zone.SetDoNotAllowFootprints(False)
        zone.SetDoNotAllowVias(False); zone.SetDoNotAllowZoneFills(False)
        zone.SetLayer(k.B_Cu); zone.SetZoneName(name)
        polygon = zone.Outline(); polygon.NewOutline()
        for x, y in rectangle(0, 31, 2, 2): polygon.Append(x, y)
        board.Add(zone); self.assertTrue(k.SaveBoard(str(self.source), board))
        self.source.with_suffix('.kicad_dru').write_text(
            f'(version 1)\n(rule "{name}"\n  (constraint disallow pad)\n'
            f'  (condition "A.intersectsArea(\'{name}\')"))\n')

    def pad_keepout(self, name):
        import pcbnew as k
        board = k.LoadBoard(str(self.source))
        zone = k.ZONE(board); zone.SetIsRuleArea(True); zone.SetDoNotAllowPads(True)
        zone.SetLayer(k.B_Cu); zone.SetZoneName(name)
        polygon = zone.Outline(); polygon.NewOutline()
        for x, y in rectangle(0, 31, 2, 2): polygon.Append(x, y)
        board.Add(zone); self.assertTrue(k.SaveBoard(str(self.source), board))

    def test_cheapest_geometry_fails_native_then_next_legal_pose_is_reported(self):
        self.native_only_pad_rule('native-only pad exclusion')
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
        self.native_only_pad_rule('keepout 1 mm; actual 0.5 mm')
        validator = NativeAlternatives(self.source, self.root / 'injected-name',
                                       extract(self.source), self.policy, {})
        result = validator('J3', (0, 32*MM), 180)
        drc = read_json(self.root / result['directory'] / 'native-drc.json')
        self.assertTrue(any('keepout 1 mm; actual 0.5 mm' in v['description']
                            for v in drc['violations']))
        self.assertEqual(result['status'], 'rejected', result)
        self.assertEqual(result['blocking_types'], ['items_not_allowed'])
        self.assertNotIn('items_not_allowed', result['advisory_types'])

    def test_pad_keepout_pose_is_excluded_by_the_packing_predicate_before_native_validation(self):
        # AGENTEE-418: the B.Cu pad keep-out binds J3's land in the packing predicate,
        # so [0, 32 mm] is never offered to native DRC and the next legal pose is reported.
        from agent_ee.placement_probe import extract
        self.pad_keepout('pad exclusion')
        facts = extract(self.source)
        self.assertIsNone(reserve._candidate_cost(facts, self.policy, reserve.fabrication_outlines(self.source),
                                                  {}, {'J3': ((0, 32*MM), 180)}))
        lane, _ = self.run_alternatives('predicate-excluded')
        alternative = lane['alternative']
        self.assertEqual(alternative['status'], 'native_validated', alternative)
        attempts = alternative['native_attempts']
        self.assertNotIn([0, 32*MM], [a['position_nm'] for a in attempts])
        self.assertEqual(attempts[-1]['validation']['status'], 'validated')

    def test_numeric_text_in_keepout_name_cannot_waive_predicate_rejection(self):
        from agent_ee.placement_probe import extract
        from agent_ee.reserved_area_native import NativeAlternatives
        self.pad_keepout('keepout 1 mm; actual 0.5 mm')
        validator = NativeAlternatives(self.source, self.root / 'injected-name',
                                       extract(self.source), self.policy, {})
        result = validator('J3', (0, 32*MM), 180)
        self.assertEqual((result['status'], result['qualification'], result['reason']),
                         ('rejected', 'not legal', 'saved pose failed placement predicates'), result)
        self.assertNotIn('advisory_types', result)
        self.assertFalse((self.root / result['directory'] / 'native-drc.json').exists())

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
        assert_closed(self, result['reserved_area'])
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

    def test_fixed_owner_native_decision_record_never_moves_the_proposal(self):
        import json
        import pcbnew as k
        from agent_ee.placement_probe import extract
        from agent_ee.placer import pack
        from agent_ee.placer_runner import _evaluate_proposal
        from agent_ee.storage import read_json
        original = self.source.read_bytes()
        self.policy = request(gap_nm=0, fixed_refs=['J3'])
        lane, _ = self.run_alternatives('fixed')
        record = lane['alternative']
        self.assertEqual(record['status'], 'decision_required', record)
        self.assertEqual(record['validation_status'], 'native_validated')
        self.assertEqual(record['qualification'], 'legal under captured rules')
        self.assertIs(record['applied'], False)
        decision = record['decision_required']
        self.assertEqual((decision['reference'], decision['saving_mm2']), ('J3', [600, 1]))
        self.assertEqual(decision['fixed_source']['declared_by'], ['policy fixed_refs'])
        self.assertEqual(decision['fixed_source']['provenance']['class'], 'unknown')
        self.assertEqual(decision['current_position_nm'], [45*MM, 32*MM])
        self.assertEqual([decision['candidate_position_nm'], decision['candidate_rotation_deg']],
                         [record['position_nm'], record['rotation_deg']])
        witness = record['native_attempts'][-1]['validation']
        self.assertEqual(witness['moves'], [{'reference': 'J3', 'position_nm': record['position_nm'],
                                             'rotation_deg': record['rotation_deg']}])
        self.assertEqual(read_json(self.root / witness['directory'] / 'placement-write-scope.json')['status'],
                         'allowed')
        self.assertEqual(self.source.read_bytes(), original)
        # The saved proposal keeps the fixed pose and carries the decision record.
        facts = extract(self.source)
        plan = pack(facts, self.policy)
        self.assertEqual(plan['status'], 'planned')
        plan['policy'] = self.policy
        self.assertTrue(plan['placements'][0]['fixed'])
        output = self.root / 'proposal' / 'candidate.kicad_pcb'
        output.parent.mkdir()
        netlist = b'<export><components><comp ref="J3"/></components><nets/></export>'
        result = _evaluate_proposal(k.LoadBoard(str(self.source)), output, plan, netlist, facts, self.source)
        assert_closed(self, result['reserved_area'])
        saved = next(r for r in result['reserved_area']['sides']['B']['owners']
                     if r['kind'] == 'rule_area' and r['owner'] == 'J3')
        self.assertEqual(saved['alternative']['status'], 'decision_required', saved)
        self.assertEqual(saved['area_mm2'], [600, 1])
        self.assertEqual(extract(output)['footprints'][0]['position_nm'], [45*MM, 32*MM])
        self.assertEqual(result['reserved_area'], json.loads(
            (output.parent / 'placement-judgment.json').read_text())['reserved_area'])

    def add_member(self):
        import pcbnew as k
        board = k.LoadBoard(str(self.source))
        fp = k.FOOTPRINT(board); fp.SetReference('R1'); board.Add(fp)
        fp.Reference().SetVisible(False); fp.Value().SetVisible(False)
        fp.Flip(k.VECTOR2I(0, 0), False)
        for layer, outline in ((k.B_CrtYd, rectangle(0, -1, 1, 2)), (k.B_Fab, rectangle(0, -1, 1, 1))):
            for a, b in zip(outline, outline[1:]+outline[:1]):
                shape = k.PCB_SHAPE(fp); shape.SetShape(k.SHAPE_T_SEGMENT); shape.SetLayer(layer)
                shape.SetWidth(50000); shape.SetStart(k.VECTOR2I(*a)); shape.SetEnd(k.VECTOR2I(*b)); fp.Add(shape)
        pad = k.PAD(fp); pad.SetNumber('1'); pad.SetAttribute(k.PAD_ATTRIB_SMD)
        pad.SetShape(k.PAD_SHAPE_RECT); pad.SetSize(k.VECTOR2I(400000, 400000))
        layers = k.LSET(); layers.AddLayer(k.B_Cu); pad.SetLayerSet(layers)
        pad.SetPosition(k.VECTOR2I(MM // 2, 0)); fp.Add(pad)
        fp.SetPosition(k.VECTOR2I(48*MM, 34*MM))
        self.assertTrue(k.SaveBoard(str(self.source), board))

    def test_rigid_block_moves_whole_block_natively_with_relative_poses_kept(self):
        from agent_ee.placement_probe import extract
        from agent_ee.placer import rotate
        from agent_ee.reserved_area_native import NativeAlternatives
        from agent_ee.storage import read_json
        self.add_member()
        original = self.source.read_bytes()
        facts = extract(self.source); fab = reserve.fabrication_outlines(self.source)
        by_ref = {f['ref']: f for f in facts['footprints']}
        plan = {'placements': [{'ref': f['ref'], 'uuid': f['uuid'], 'fixed': False} for f in facts['footprints']],
                'block_arrangement': {'arrangement': {}, 'blocks': [
                    {'block': 'card', 'anchor': 'J3', 'references': ['J3', 'R1']}]}}
        costs = reserve.report(facts, self.policy, fab)
        validator = NativeAlternatives(self.source, self.root / 'rigid', facts, self.policy, plan)
        reserve.alternatives(facts, self.policy, fab, costs, plan, validator)
        assert_closed(self, costs)
        lane = next(r for r in costs['sides']['B']['owners'] if r['kind'] == 'rule_area')
        alternative = lane['alternative']
        self.assertEqual(alternative['status'], 'native_validated', alternative)
        self.assertEqual(alternative['saving_mm2'], [600, 1])
        self.assertEqual(alternative['rigid_block'], {'block': 'card', 'anchor': 'J3', 'references': ['J3', 'R1']})
        moves = {m['reference']: m for m in alternative['moves']}
        self.assertEqual(moves['J3']['position_nm'], alternative['position_nm'])
        turns = int((alternative['rotation_deg'] - by_ref['J3']['rotation_deg']) / 90)
        offset = [by_ref['R1']['position_nm'][i] - by_ref['J3']['position_nm'][i] for i in (0, 1)]
        self.assertEqual([moves['R1']['position_nm'][i] - moves['J3']['position_nm'][i] for i in (0, 1)],
                         rotate(offset, turns))
        self.assertEqual((moves['R1']['rotation_deg'] - moves['J3']['rotation_deg']) % 360,
                         (by_ref['R1']['rotation_deg'] - by_ref['J3']['rotation_deg']) % 360)
        # The retained native scratch board moved both members exactly as recorded.
        witness = alternative['native_attempts'][-1]['validation']
        self.assertEqual(witness['moves'], sorted(alternative['moves'], key=lambda m: m['reference']))
        saved = {f['ref']: f for f in extract(self.root / witness['directory'] / 'candidate.kicad_pcb')['footprints']}
        for ref, move in moves.items():
            self.assertEqual(saved[ref]['position_nm'], move['position_nm'])
        scope = read_json(self.root / witness['directory'] / 'placement-write-scope.json')
        self.assertEqual(scope['status'], 'allowed')
        self.assertEqual(self.source.read_bytes(), original)
        # A member's contained courtyard excess is invariant under the rigid move.
        member = next(r for r in costs['sides']['B']['owners'] if r['owner'] == 'R1')
        self.assertEqual(member['area_mm2'], [1, 1])
        self.assertEqual(member['alternative']['status'], 'no_cheaper_contained_pose')

    def test_rigid_block_move_rejected_natively_never_moves_one_member_alone(self):
        import pcbnew as k
        from agent_ee.placement_probe import extract
        from agent_ee.reserved_area_native import NativeAlternatives
        self.add_member()
        # A captured custom rule refuses every pad, so every whole-block pose is rejected natively.
        self.source.with_suffix('.kicad_dru').write_bytes(b'''(version 1)
(rule "Owned fixture pad exclusion"
  (condition "A.Type == 'Pad'")
  (constraint disallow pad))
''')
        facts = extract(self.source); fab = reserve.fabrication_outlines(self.source)
        plan = {'placements': [{'ref': f['ref'], 'uuid': f['uuid'], 'fixed': False} for f in facts['footprints']],
                'block_arrangement': {'arrangement': {}, 'blocks': [
                    {'block': 'card', 'anchor': 'J3', 'references': ['J3', 'R1']}]}}
        costs = reserve.report(facts, self.policy, fab)
        validator = NativeAlternatives(self.source, self.root / 'rigid-rejected', facts, self.policy, plan)
        reserve.alternatives(facts, self.policy, fab, costs, plan, validator)
        assert_closed(self, costs)
        lane = next(r for r in costs['sides']['B']['owners'] if r['kind'] == 'rule_area')
        alternative = lane['alternative']
        self.assertEqual(alternative['status'], 'none_found', alternative)
        self.assertIs(alternative['search_complete'], True)
        self.assertNotIn('moves', alternative)
        for attempt in alternative['native_attempts']:
            self.assertEqual(attempt['validation']['status'], 'rejected')
            self.assertEqual([m['reference'] for m in attempt['validation']['moves']], ['J3', 'R1'])

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

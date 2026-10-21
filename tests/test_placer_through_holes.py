"""Original cross-side pad/drill fixtures and native publication gates (AGENTEE-318)."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee.placement_probe import extract
from agent_ee.placer import _hole_conflict, _qualified, pack, transform
from agent_ee.placer_runner import _drc_comparison, _native_drc, _proposal_drc
import test_placement_native as native_fixture
from test_placer import request
from toolchain import require_toolchain


class ThroughHolePlacementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-pth-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def board(self, npth=False, moving=False, shape=None):
        import pcbnew as k
        fixture = native_fixture.NativePlacementTest()
        fixture.root = self.root
        source = fixture.board('opposite')
        board = k.LoadBoard(str(source))
        front = next(f for f in board.GetFootprints() if f.GetReference() == 'U1')
        pad = next(iter(front.Pads()))
        pad.SetAttribute(k.PAD_ATTRIB_NPTH if npth else k.PAD_ATTRIB_PTH)
        pad.SetShape(k.PAD_SHAPE_OVAL if shape is None else shape)
        pad.SetSize(k.VECTOR2I(1800000, 1200000))
        pad.SetDrillSize(k.VECTOR2I(800000, 500000))
        pad.SetDrillShape(k.PAD_DRILL_SHAPE_OBLONG)
        pad.SetLayerSet(k.LSET.AllCuMask())
        # An off-centre native pad extends beyond its actual front courtyard.
        pad.SetPosition(k.VECTOR2I(6000000, 3000000))
        back = next(f for f in board.GetFootprints() if f.GetReference() == 'C1')
        back.SetPosition(k.VECTOR2I(6000000, 3000000))
        if moving:
            front.SetPosition(k.VECTOR2I(15000000, 3000000))
        k.SaveBoard(str(source), board)
        return source

    def test_pth_and_npth_reserve_both_sides_without_inventing_courtyards(self):
        for npth in (False, True):
            facts = extract(self.board(npth))
            _qualified(facts)
            by_ref = {f['ref']: f for f in facts['footprints']}
            front, back = by_ref['U1'], by_ref['C1']
            self.assertEqual(set(front['courtyards']), {'F'})
            self.assertTrue(_hole_conflict(front, back))
            self.assertTrue(_hole_conflict(back, front))
            self.assertTrue(front['packing_pad_geometry']['holes'])
            self.assertTrue(any('B.Cu' in e['layers'] for e in front['packing_pad_geometry']['holes']))
            planned = pack(facts, request())
            self.assertEqual(planned['status'], 'planned', planned)
            placed = {p['ref']: transform(by_ref[p['ref']], p['position_nm'], p['rotation_deg'])
                      for p in planned['placements']}
            self.assertFalse(_hole_conflict(placed['U1'], placed['C1']))
            self.assertEqual(json.dumps(placed['U1']['courtyards'], sort_keys=True),
                             json.dumps(front['courtyards'], sort_keys=True))

    def test_native_shapes_and_rigid_reservation_transform_all_turns(self):
        import pcbnew as k
        for shape in (k.PAD_SHAPE_CIRCLE, k.PAD_SHAPE_RECT, k.PAD_SHAPE_OVAL,
                      k.PAD_SHAPE_ROUNDRECT, k.PAD_SHAPE_CHAMFERED_RECT):
            source = self.board(shape=shape)
            facts = extract(source)
            original = next(f for f in facts['footprints'] if f['ref'] == 'U1')
            for rotation in (0, 90, 180, 270):
                board = k.LoadBoard(str(source))
                fp = next(f for f in board.GetFootprints() if f.GetReference() == 'U1')
                fp.SetOrientationDegrees(rotation)
                fp.SetPosition(k.VECTOR2I(12000000, 13000000))
                saved = self.root / 'transformed.kicad_pcb'
                k.SaveBoard(str(saved), board)
                actual = next(f for f in extract(saved)['footprints'] if f['ref'] == 'U1')
                expected = transform(original, [12000000, 13000000], rotation)
                # Native arc sampling phase may change with rotation; its outward extents agree.
                bounds = lambda f: [(min(p[0] for p in c), min(p[1] for p in c),
                                      max(p[0] for p in c), max(p[1] for p in c))
                    for kind in ('holes', 'copper') for e in f['packing_pad_geometry'][kind]
                    for c in e['polygons']]
                self.assertEqual(bounds(actual), bounds(expected), (shape, rotation))
                self.assertEqual(actual['pads'][0]['position_nm'], expected['pads'][0]['position_nm'])
                from agent_ee.placement import edges
                from agent_ee.placement_predicates import point_segment2
                for kind in ('holes', 'copper'):
                    for native, rigid in zip(actual['packing_pad_geometry'][kind],
                                             expected['packing_pad_geometry'][kind], strict=True):
                        for a, b in zip(native['polygons'], rigid['polygons'], strict=True):
                            # Two independently sampled outward 1 um bounds of
                            # the same native curve can differ by at most 2 um.
                            for points, boundary in ((a, b), (b, a)):
                                for point in points:
                                    self.assertLessEqual(min(point_segment2(point, start, end)
                                        for start, end in edges(boundary)), 2000**2, (shape, rotation, kind))

    def test_moving_hole_and_fixed_conflict_and_missing_geometry(self):
        facts = extract(self.board())
        refused = pack(facts, request(fixed_refs=['U1', 'C1']))
        self.assertEqual((refused['status'], refused['outcome']), ('fixed_conflict', 'unknown'))
        planned = pack(facts, request(fixed_refs=['C1']))
        self.assertEqual(planned['status'], 'planned', planned)
        missing = copy.deepcopy(facts)
        for f in missing['footprints']:
            f.pop('packing_pad_geometry')
        self.assertEqual(pack(missing, request())['status'], 'unsupported_geometry')

    def test_peer_copper_outside_its_courtyard_and_internal_copper_layers(self):
        import pcbnew as k
        source = self.board()
        board = k.LoadBoard(str(source))
        board.SetCopperLayerCount(4)
        peer = next(f for f in board.GetFootprints() if f.GetReference() == 'C1')
        peer.SetPosition(k.VECTOR2I(10000000, 3000000))
        next(iter(peer.Pads())).SetPosition(k.VECTOR2I(6000000, 3000000))
        k.SaveBoard(str(source), board)
        facts = extract(source)
        _qualified(facts)
        by_ref = {f['ref']: f for f in facts['footprints']}
        self.assertTrue(_hole_conflict(by_ref['U1'], by_ref['C1']))
        self.assertEqual({l for e in by_ref['U1']['packing_pad_geometry']['copper'] for l in e['layers']},
                         {'F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'})

    def test_malformed_native_reservations_remain_unknown(self):
        facts = extract(self.board())
        for geometry in (None, [], {}, {'status': 'proven'},
                         {'status': 'proven', 'copper': [], 'holes': []}):
            altered = copy.deepcopy(facts)
            next(f for f in altered['footprints'] if f['ref'] == 'U1')['packing_pad_geometry'] = geometry
            refused = pack(altered, request())
            self.assertEqual((refused['status'], refused['outcome']), ('unsupported_geometry', 'unknown'))
            self.assertEqual(refused['placements'], [])

    def test_native_writer_places_pth_npth_and_moving_drill_without_collisions(self):
        import hashlib
        from agent_ee.placer_runner import place_candidate
        for npth, fixed in ((False, ['U1']), (True, ['U1']), (False, ['C1'])):
            source = self.board(npth)
            netlist = self.root / 'native.xml'
            netlist.write_text('<export><components><comp ref="U1"/><comp ref="C1"/></components><nets/></export>')
            work = self.root / f'writer-{npth}-{fixed[0]}'
            work.mkdir()
            config = {'policy': request(fixed_refs=fixed), 'authorization': {
                'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
                'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'writable_root': str(work)}}
            result = place_candidate(source, netlist, work / 'proposal', config)
            self.assertEqual((result['status'], result['write_scope']['status']), ('placed', 'allowed'))
            self.assertEqual(result['native_drc_comparison']['blocking_findings'], [])
            after = extract(work / 'proposal/candidate.kicad_pcb')
            _qualified(after)
            a, b = after['footprints']
            self.assertFalse(_hole_conflict(a, b))
            self.assertEqual(len(a['courtyards']), 1)
            self.assertEqual(len(b['courtyards']), 1)

    def test_saved_probe_uses_source_project_clearance_and_round_trips(self):
        import hashlib
        from agent_ee.placer_runner import place_candidate
        original = self.board()
        source = self.root / 'project-owned.kicad_pcb'
        source.write_bytes(original.read_bytes())
        source.with_suffix('.kicad_pro').write_text(json.dumps({
            'board': {'design_settings': {'rules': {'min_clearance': 2.5}}}}))
        before = next(f for f in extract(source)['footprints'] if f['ref'] == 'U1')
        self.assertEqual(before['packing_pad_geometry']['clearance_nm'], 2500000)
        self.assertEqual(json.loads(json.dumps(before)), before)
        netlist = self.root / 'native.xml'
        netlist.write_text('<export><components><comp ref="U1"/><comp ref="C1"/></components><nets/></export>')
        work = self.root / 'project-writer'
        work.mkdir()
        config = {'policy': request(), 'authorization': {
            'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'writable_root': str(work)}}
        report = place_candidate(source, netlist, work / 'proposal', config)
        self.assertEqual(report['status'], 'placed')
        saved = json.loads((work / 'proposal/placement-probe.json').read_text())
        after = next(f for f in saved['footprints'] if f['ref'] == 'U1')
        self.assertEqual(after, before)

    def test_native_gate_refuses_new_cross_side_collisions_retains_findings(self):
        import pcbnew as k
        source = self.board(moving=True)
        scratch = self.root / 'proposal'
        scratch.mkdir()
        output = scratch / 'candidate.kicad_pcb'
        board = k.LoadBoard(str(source))
        fp = next(f for f in board.GetFootprints() if f.GetReference() == 'U1')
        fp.SetPosition(k.VECTOR2I(3000000, 3000000))
        k.SaveBoard(str(output), board)
        with self.assertRaisesRegex(ValueError, 'introduced native DRC collisions'):
            _proposal_drc(source, output)
        comparison = json.loads((scratch / 'native-drc-comparison.json').read_text())
        self.assertTrue(comparison['blocking_findings'])
        self.assertIn('pth_inside_courtyard', comparison['added_by_type'])
        self.assertTrue((scratch / 'baseline/native-drc.json').is_file())
        self.assertTrue((scratch / 'native-drc.json').is_file())

    def test_writer_refuses_unsafe_plan_and_retains_native_evidence_before_publication(self):
        import hashlib
        from unittest.mock import patch
        from agent_ee.placer_runner import place_candidate
        source = self.board(moving=True)
        saved = source.read_bytes()
        facts = extract(source)
        policy = request(fixed_refs=['C1'])
        plan = pack(facts, policy)
        self.assertEqual(plan['status'], 'planned', plan)
        for proposed in plan['placements']:
            if proposed['ref'] == 'U1':
                proposed.update(position_nm=[3000000, 3000000], rotation_deg=0, fixed=False)
        netlist = self.root / 'native.xml'
        netlist.write_text('<export><components><comp ref="U1"/><comp ref="C1"/></components><nets/></export>')
        work = self.root / 'unsafe-writer'
        work.mkdir()
        config = {'policy': policy, 'authorization': {
            'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
            'source_sha256': hashlib.sha256(saved).hexdigest(), 'writable_root': str(work)}}
        # Inject a bad planner result; all saved-board/native gates remain real.
        with patch('agent_ee.placer_runner._plan', return_value=plan):
            with self.assertRaisesRegex(ValueError, 'introduced native DRC collisions'):
                place_candidate(source, netlist, work / 'proposal', config)
        self.assertFalse((work / 'proposal').exists())
        failure, = work.glob('proposal-failure-*')
        self.assertTrue((failure / 'failed-candidate.kicad_pcb').is_file())
        self.assertTrue((failure / 'baseline/native-drc.json').is_file())
        delta = json.loads((failure / 'native-drc-comparison.json').read_text())
        self.assertTrue(delta['blocking_findings'])
        self.assertEqual(source.read_bytes(), saved)

    def test_native_gate_accepts_unchanged_baseline_defects_and_keeps_settings(self):
        source = self.board()
        project = {'board': {'design_settings': {'rules': {'min_clearance': 0.4}}}}
        source.with_suffix('.kicad_pro').write_text(json.dumps(project))
        source.with_suffix('.kicad_dru').write_text('(version 1)\n(rule "owned clearance" (constraint clearance (min 0.4mm)))\n')
        scratch = self.root / 'unchanged'
        scratch.mkdir()
        output = scratch / 'candidate.kicad_pcb'
        output.write_bytes(source.read_bytes())
        overlaps, comparison = _proposal_drc(source, output)
        self.assertEqual(comparison['blocking_findings'], [])
        self.assertEqual(comparison['added_findings'], [])
        self.assertEqual(output.with_suffix('.kicad_pro').read_bytes(), source.with_suffix('.kicad_pro').read_bytes())
        self.assertEqual(output.with_suffix('.kicad_dru').read_bytes(), source.with_suffix('.kicad_dru').read_bytes())

    def test_native_clearance_settings_and_ignored_collision_categories(self):
        for category in ('shorting_items', 'clearance', 'pth_inside_courtyard'):
            original = self.board()
            # pcbnew caches project settings by path: build this immutable input
            # and its project before its first native load.
            source = self.root / (category + '.kicad_pcb')
            source.write_bytes(original.read_bytes())
            project = {'board': {'design_settings': {'rules': {'min_clearance': 2.5},
                       'rule_severities': {category: 'ignore'}}}}
            source.with_suffix('.kicad_pro').write_text(json.dumps(project))
            geometry = next(f for f in extract(source)['footprints'] if f['ref'] == 'U1')['packing_pad_geometry']
            self.assertGreaterEqual(geometry['clearance_nm'], 2500000)
            scratch = self.root / category
            scratch.mkdir()
            with self.assertRaisesRegex(ValueError, 'explicitly ignored'):
                _native_drc(source, scratch)


class DrcComparisonTest(unittest.TestCase):
    def report(self, *types):
        return {'ignored_checks': [], 'included_severities': ['error', 'warning'],
                'violations': [{'type': t, 'items': [{'uuid': 'a'}, {'uuid': 'b'}]} for t in types]}

    def test_all_collision_categories_multiplicity_and_silk_are_separate(self):
        before = self.report('clearance')
        after = self.report('clearance', 'clearance', 'shorting_items', 'pth_inside_courtyard',
                            'silk_overlap', 'silk_over_copper')
        delta = _drc_comparison(before, after)
        self.assertEqual(len(delta['blocking_findings']), 2)
        self.assertEqual([v['type'] for v in delta['advisory_findings']].count('clearance'), 2)
        self.assertEqual(len(delta['silkscreen_findings']), 2)
        self.assertEqual(delta['added_by_type']['clearance'], 1)
        # Location/wording changes are not a new physical item pair.
        unchanged = copy.deepcopy(before)
        unchanged['violations'][0].update(description='moved', pos={'x': 12, 'y': 3})
        self.assertEqual(_drc_comparison(before, unchanged)['added_findings'], [])

    def test_malformed_or_incomparable_native_reports_refuse(self):
        before = self.report()
        after = self.report('clearance')
        after['violations'][0]['items'] = []
        with self.assertRaisesRegex(ValueError, 'item identities'):
            _drc_comparison(before, after)
        after = self.report()
        after['ignored_checks'] = [{'key': 'clearance'}]
        with self.assertRaisesRegex(ValueError, 'settings differ'):
            _drc_comparison(before, after)

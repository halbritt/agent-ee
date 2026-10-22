"""Original native geometries for structural copper containment (AGENTEE-331)."""
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from agent_ee import outline_containment as containment
from agent_ee.design_rule_provenance import classify_finding, import_constraints, bind_project
from agent_ee.placer_runner import _proposal_drc
from agent_ee.storage import json_bytes, read_json, write_json
from tests.toolchain import require_toolchain
from owner_fixture import bind_owner_rules, cite


class NativeOutlineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def board(self):
        import sys
        from tests import toolchain
        sys.modules.setdefault('toolchain', toolchain)
        from tests.test_placement_native import NativePlacementTest
        maker = NativePlacementTest(); maker.root = self.root
        path = maker.board()
        write_json(path.with_suffix('.kicad_pro'), {'board': {'design_settings': {
            'rules': {'min_copper_edge_clearance': .5}}}})
        return path

    def mutate(self, path, kind, outside):
        import pcbnew as k
        board = k.LoadBoard(str(path))
        if kind == 'pad':
            fp = next(f for f in board.GetFootprints() if f.GetReference() == 'C1')
            fp.SetPosition(k.VECTOR2I(k.FromMM(23 if outside else 20), k.FromMM(3)))
            uid = next(iter(fp.Pads())).m_Uuid.AsString()
        else:
            item = k.PCB_TRACK(board)
            item.SetStart(k.VECTOR2I(k.FromMM(23 if outside else 19), k.FromMM(5)))
            item.SetEnd(k.VECTOR2I(k.FromMM(25), k.FromMM(5)))
            item.SetWidth(k.FromMM(.25)); item.SetLayer(k.F_Cu); board.Add(item)
            uid = item.m_Uuid.AsString()
        k.SaveBoard(str(path), board)
        return uid

    def test_crossing_and_wholly_outside_pad_track_refuse_publication(self):
        import pcbnew as k
        source = self.board()
        for kind in ('pad', 'track'):
            for outside in (False, True):
                with self.subTest(kind=kind, wholly_outside=outside):
                    work = self.root / f'{kind}-{outside}'; work.mkdir()
                    output = work / 'candidate.kicad_pcb'; shutil.copyfile(source, output)
                    uid = self.mutate(output, kind, outside)
                    with self.assertRaisesRegex(ValueError, 'introduced native DRC'):
                        _proposal_drc(source, output)
                    delta = read_json(work / 'native-drc-comparison.json')
                    found = [v for v in delta['blocking_findings'] if v['type'] == 'copper_outside_outline']
                    self.assertEqual([v['items'][0]['uuid'] for v in found], [uid])
                    self.assertEqual(found[0]['constraint_provenance']['class'], 'engineering')
                    self.assertEqual(found[0]['constraint_provenance']['enforcement'], 'hard')
                    raw = read_json(work / 'native-drc.json')
                    self.assertNotIn('copper_outside_outline', [v['type'] for v in raw['violations']])
                    # Every retained witness can be reproduced directly from the saved board.
                    self.assertEqual(containment.findings(k.LoadBoard(str(output))), found_without_source(found))

    def test_cutout_and_concavity_use_polygon_difference(self):
        import pcbnew as k
        path = self.board()
        board = k.LoadBoard(str(path))
        fp = next(f for f in board.GetFootprints() if f.GetReference() == 'C1')
        # Inside the bounding rectangle but inside the concave notch.
        fp.SetPosition(k.VECTOR2I(k.FromMM(10), k.FromMM(12)))
        self.assertEqual(len(containment.findings(board)), 1)
        # Internal board cutout, wholly surrounding a pad.
        fp.SetPosition(k.VECTOR2I(k.FromMM(15), k.FromMM(3)))
        cutout = k.PCB_SHAPE(board); cutout.SetShape(k.SHAPE_T_RECT); cutout.SetLayer(k.Edge_Cuts)
        cutout.SetStart(k.VECTOR2I(k.FromMM(14), k.FromMM(2)))
        cutout.SetEnd(k.VECTOR2I(k.FromMM(16), k.FromMM(4))); board.Add(cutout)
        self.assertEqual(len(containment.findings(board)), 1)

    def test_round_pad_is_inside_when_bbox_corners_are_outside(self):
        import pcbnew as k
        board = k.BOARD()
        edge = k.PCB_SHAPE(board); edge.SetShape(k.SHAPE_T_CIRCLE); edge.SetLayer(k.Edge_Cuts)
        edge.SetStart(k.VECTOR2I(0, 0)); edge.SetEnd(k.VECTOR2I(k.FromMM(10), 0)); board.Add(edge)
        fp = k.FOOTPRINT(board); board.Add(fp)
        pad = k.PAD(fp); pad.SetShape(k.PAD_SHAPE_CIRCLE); pad.SetAttribute(k.PAD_ATTRIB_SMD)
        pad.SetSize(k.VECTOR2I(k.FromMM(18), k.FromMM(18))); pad.SetLayerSet(k.PAD.SMDMask()); fp.Add(pad)
        self.assertEqual(containment.findings(board), [])

    def test_unavailable_outline_fails_explicitly(self):
        import pcbnew as k
        with self.assertRaisesRegex(ValueError, 'closed board outline'):
            containment.findings(k.BOARD())

    def test_native_child_failure_cannot_produce_a_passing_witness(self):
        import pcbnew as k
        source = self.root / 'inputs'; source.mkdir()
        k.SaveBoard(str(source / 'open.kicad_pcb'), k.BOARD())
        with self.assertRaisesRegex(ValueError, 'native copper containment failed'):
            containment.sandboxed(source, 'open.kicad_pcb', self.root / 'failed-child')
        self.assertFalse((self.root / 'failed-child/copper-containment.json').exists())

    def test_saved_filled_zone_and_copper_graphics_are_checked(self):
        import pcbnew as k
        path = self.board(); board = k.LoadBoard(str(path))
        zone = k.ZONE(board); zone.SetLayer(k.F_Cu)
        poly = k.SHAPE_POLY_SET(); poly.NewOutline()
        for x, y in ((22, 2), (24, 2), (24, 4), (22, 4)):
            poly.Append(k.FromMM(x), k.FromMM(y))
        zone.Outline().Append(poly); board.Add(zone)
        # Requested unfilled zone area is not copper.
        self.assertEqual(containment.findings(board), [])
        zone.SetFilledPolysList(k.F_Cu, poly); zone.SetIsFilled(True)
        shape = k.PCB_SHAPE(board); shape.SetShape(k.SHAPE_T_SEGMENT); shape.SetLayer(k.B_Cu)
        shape.SetStart(k.VECTOR2I(k.FromMM(23), k.FromMM(6)))
        shape.SetEnd(k.VECTOR2I(k.FromMM(25), k.FromMM(6))); shape.SetWidth(k.FromMM(.2)); board.Add(shape)
        k.SaveBoard(str(path), board)
        found = containment.findings(k.LoadBoard(str(path)))
        self.assertEqual({f['items'][0]['uuid'] for f in found}, {zone.m_Uuid.AsString(), shape.m_Uuid.AsString()})

    def test_captured_drc_and_parity_classify_wholly_outside_copper(self):
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.board_build import parity_drc
        repo = Path(__file__).resolve().parents[1]
        source = self.root / 'project'; shutil.copytree(repo / 'fixtures/rc/control', source)
        path = source / 'rc_filter.kicad_pcb'
        self.mutate(path, 'track', True)
        write_json(path.with_suffix('.kicad_pro'), {'board': {'design_settings': {
            'rules': {'min_copper_edge_clearance': .5}}}})
        parity = parity_drc(path, self.root / 'parity')
        outside = [f for f in parity['classified_findings'] if f['type'] == 'copper_outside_outline']
        self.assertTrue(outside)
        self.assertTrue(all(f['constraint_provenance']['class'] == 'engineering' for f in outside))
        scope = read_json(repo / 'fixtures/rc/control.scope.json')
        for entry in scope['inputs']:
            entry['path'] = str(source if entry['at'] == 'project' else repo / 'fixtures/rc' / entry['path'])
        scope['checks'] = [{'id': 'drc', 'kind': 'drc'}]
        write_json(self.root / 'scope.json', scope)
        run = self.root / 'captured'; run.mkdir(); capture(self.root / 'scope.json', run)
        result = run_checks(run, time.monotonic() + 120)[0]
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertIn('work/drc/containment/copper-containment.json', result['evidence'])
        from agent_ee.findings import collect_findings
        findings = collect_findings([], [result])
        self.assertTrue(any(e['path'] == 'work/drc/drc-constraints.json' for e in findings[0]['evidence']))
        execution = read_json(run / 'work/drc/containment/execution.json')
        self.assertEqual((execution['execution'], execution['exit_code']), ('ok', 0))
        self.assertIn('agent_ee.outline_containment', execution['argv'])
        evidence = read_json(run / 'work/drc/drc-constraints.json')
        self.assertTrue(any(v['type'] == 'copper_outside_outline' and
            v['constraint_provenance']['class'] == 'engineering' for v in evidence['violations']))

    def test_native_outline_defect_survives_unavailable_containment(self):
        import pcbnew as k
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        repo = Path(__file__).resolve().parents[1]
        source = self.root / 'project'; shutil.copytree(repo / 'fixtures/rc/control', source)
        path = source / 'rc_filter.kicad_pcb'
        board = k.LoadBoard(str(path))
        board.Remove(next(item for item in board.GetDrawings() if item.GetLayer() == k.Edge_Cuts))
        k.SaveBoard(str(path), board)
        scope = read_json(repo / 'fixtures/rc/control.scope.json')
        for entry in scope['inputs']:
            entry['path'] = str(source if entry['at'] == 'project' else repo / 'fixtures/rc' / entry['path'])
        scope['checks'] = [{'id': 'drc', 'kind': 'drc'}]
        write_json(self.root / 'scope.json', scope)
        run = self.root / 'captured'; run.mkdir(); capture(self.root / 'scope.json', run)
        result = run_checks(run, time.monotonic() + 120)[0]
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        raw = read_json(run / 'work/drc/drc.json')
        self.assertIn('invalid_outline', [v['type'] for v in raw['violations']])
        self.assertEqual(result['details']['containment']['outcome'], 'unknown')
        self.assertFalse((run / 'work/drc/containment/copper-containment.json').exists())
        self.assertIn('work/drc/containment/execution.json', result['evidence'])
        from agent_ee.board_build import parity_drc
        parity = parity_drc(path, self.root / 'invalid-outline-parity')
        self.assertGreater(parity['graded_violations'], 0)
        self.assertEqual(parity['containment']['outcome'], 'unknown')

    def test_moved_numeric_findings_cannot_reuse_single_item_baseline(self):
        import pcbnew as k
        from agent_ee.placer_runner import _drc_comparison, _drc_item_states
        source = self.board()
        original = k.LoadBoard(str(source))
        fp = next(f for f in original.GetFootprints() if f.GetReference() == 'C1')
        uid = next(iter(fp.Pads())).m_Uuid.AsString()
        output = self.root / 'moved.kicad_pcb'
        fp.SetPosition(k.VECTOR2I(k.FromMM(19.6), k.FromMM(3))); k.SaveBoard(str(output), original)
        for kind, field in (('copper_edge_clearance', 'min_copper_edge_clearance'), ('hole_clearance', 'min_hole_clearance')):
            with self.subTest(kind=kind):
                def report(actual):
                    return {'ignored_checks': [], 'violations': [dict(type=kind, items=[{'uuid': uid}],
                        description=f'Violation (board setup constraints limit 0.5000 mm; actual {actual:.4f} mm)')]}
                sources = {'rules': {}, 'settings': [dict(setting='board.design_settings.rules.'+field,
                    value=.5, **{'class': 'manufacturer'}, citation={'path': 'original-fixture'})]}
                delta = _drc_comparison(report(.4), report(.2), _drc_item_states(source), _drc_item_states(output), sources)
                self.assertEqual(len(delta['blocking_findings']), 1)
                unchanged = _drc_comparison(report(.2), report(.2), _drc_item_states(output), _drc_item_states(output), sources)
                self.assertEqual(unchanged['added_findings'], [])

    def test_numeric_geometry_identity_ignores_board_serialization_whitespace(self):
        import pcbnew as k
        from agent_ee.placer_runner import _drc_comparison, _drc_item_states
        source = self.board()
        output = self.root / 'formatting.kicad_pcb'
        output.write_text(source.read_text().replace('\n', '\n '))
        board = k.LoadBoard(str(source))
        pad = next(iter(next(f for f in board.GetFootprints() if f.GetReference() == 'C1').Pads()))
        edge = next(iter(board.GetDrawings()))
        finding = dict(type='copper_edge_clearance', description=
            'Violation (board setup constraints limit 0.5000 mm; actual 0.2000 mm)',
            items=[{'uuid': pad.m_Uuid.AsString()}, {'uuid': edge.m_Uuid.AsString()}])
        report = {'ignored_checks': [], 'violations': [finding]}
        sources = {'rules': {}, 'settings': [dict(setting='board.design_settings.rules.min_copper_edge_clearance',
            value=.5, **{'class': 'manufacturer'}, citation={'path': 'original-fixture'})]}
        delta = _drc_comparison(report, report, _drc_item_states(source), _drc_item_states(output), sources)
        self.assertEqual(delta['added_findings'], [])

    def test_native_worsened_sourced_edge_is_new_on_same_pad(self):
        import pcbnew as k
        source = self.board()
        board = k.LoadBoard(str(source))
        fp = next(f for f in board.GetFootprints() if f.GetReference() == 'C1')
        fp.SetPosition(k.VECTOR2I(k.FromMM(19.35), k.FromMM(3)))
        k.SaveBoard(str(source), board)
        doc = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {
            'min_copper_edge_clearance': {'value_mm': .5, 'class': 'manufacturer', 'basis': 'original edge probe'}}}
        cite(doc, doc['board_setup_rules']['min_copper_edge_clearance'])  # AGENTEE-348 cited form keeps it hard
        project, record = import_constraints({}, json_bytes(doc), 'limits.json', 'probe')
        bind_project(project, record); write_json(source.with_suffix('.kicad_pro'), project)
        work = self.root / 'edge'; work.mkdir(); output = work / 'candidate.kicad_pcb'
        fp.SetPosition(k.VECTOR2I(k.FromMM(19.65), k.FromMM(3))); k.SaveBoard(str(output), board)
        with self.assertRaisesRegex(ValueError, 'introduced native DRC'):
            _proposal_drc(source, output)
        delta = read_json(work / 'native-drc-comparison.json')
        baseline = [v for v in delta['baseline_findings'] if v['type'] == 'copper_edge_clearance']
        added = [v for v in delta['blocking_findings'] if v['type'] == 'copper_edge_clearance']
        self.assertTrue(baseline); self.assertTrue(added)
        self.assertEqual({i['uuid'] for v in baseline for i in v['items']},
                         {i['uuid'] for v in added for i in v['items']})
        self.assertTrue(all(v['constraint_provenance']['class'] == 'manufacturer' for v in added))

    def test_plane_publication_refuses_preexisting_wholly_outside_copper(self):
        from tests.test_add_planes import PlaneFixture
        fixture = type('OriginalPlaneProbe', (PlaneFixture, unittest.TestCase), {})()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.mutate(fixture.source / fixture.board, 'track', True)
        # Its outline begins at (10,10); this track lies wholly below the board.
        report = fixture.run_plan()
        self.assertEqual(report['execution'], 'failed', report)
        self.assertIn('outside board outline', str(report['findings']))
        self.assertFalse((fixture.root / 'run/board.kicad_pcb').exists())
        classified = read_json(fixture.root / 'run/drc/constraints.json')['violations']
        self.assertTrue(any(v['type'] == 'copper_outside_outline' and
            v['constraint_provenance']['class'] == 'engineering' for v in classified))

    def test_sublayout_copied_item_uuids_resolve_sourced_net_classes(self):
        import pcbnew as k
        from agent_ee.sublayout import native_drc
        source = self.board()
        board = k.LoadBoard(str(source))
        for fp in board.GetFootprints():
            net = k.NETINFO_ITEM(board, fp.GetReference()); board.Add(net)
            for pad in fp.Pads():
                pad.SetNet(net)
        k.SaveBoard(str(source), board)
        template = {'board': {'design_settings': {'rules': {'min_clearance': .2}}},
            'net_settings': {'meta': {'version': 5}, 'classes': [{'name': 'Default', 'clearance': .09}]}}
        doc = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {},
            'net_class_default': {'clearance_mm': .09, 'class': 'manufacturer', 'basis': 'original copied-item probe'}}
        cite(doc, doc['net_class_default'])  # AGENTEE-348 cited form keeps it hard
        project, record = import_constraints(template, json_bytes(doc), 'limits.json', 'probe')
        bind_project(project, record); write_json(source.with_suffix('.kicad_pro'), project)
        candidate = self.root / 'copied-items.kicad_pcb'
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                pad.SetUuidDirect(k.KIID())
            if fp.GetReference() == 'C1':
                fp.SetPosition(k.VECTOR2I(k.FromMM(3.55), k.FromMM(3)))
        k.SaveBoard(str(candidate), board)
        observed = native_drc(candidate, self.root / 'copied-drc', project_board=source)
        gaps = [v for v in observed['report']['violations'] if v['type'] == 'clearance']
        self.assertTrue(gaps)
        self.assertTrue(all(v['constraint_provenance']['class'] == 'manufacturer' for v in gaps))
        self.assertTrue(any('0.2000 mm' in v['description'] and '0.0500 mm' in v['description'] for v in gaps))

    def test_sublayout_uses_project_sources_and_owner_rules(self):
        from agent_ee.sublayout import native_drc
        path = self.board()
        original = read_json(path.with_suffix('.kicad_pro'))
        original['board']['design_settings']['rules']['min_text_height'] = 8
        for cls in ('default', 'manufacturer'):
            doc = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {
                'min_text_height': {'value_mm': 8, 'class': cls, 'basis': 'original probe text limit'}}}
            if cls != 'default':  # AGENTEE-348 cited form keeps it hard
                cite(doc, doc['board_setup_rules']['min_text_height'])
            project, record = import_constraints(original, json_bytes(doc), 'limits.json', 'probe')
            bind_project(project, record); write_json(path.with_suffix('.kicad_pro'), project)
            drc = native_drc(path, self.root / cls)
            text = [v for v in drc['report']['violations'] if v['type'] == 'text_height']
            self.assertTrue(text)
            self.assertTrue(all(v['constraint_provenance']['class'] == cls for v in text))
        path.with_suffix('.kicad_dru').write_text('(version 1)\n(rule "owner text" (constraint text_height (min 9mm)))\n')
        bind_owner_rules(path)  # AGENTEE-348 H1 migration: the owner rule is declared and cited
        drc = native_drc(path, self.root / 'owner')
        text = [v for v in drc['report']['violations'] if v['type'] == 'text_height']
        self.assertTrue(any(v['constraint_provenance']['class'] == 'owner' for v in text))
        self.assertEqual((self.root / 'owner/drc-input' / path.with_suffix('.kicad_dru').name).read_bytes(), path.with_suffix('.kicad_dru').read_bytes())


def found_without_source(findings):
    return [{k: v for k, v in f.items() if k != 'constraint_provenance'} for f in findings]


if __name__ == '__main__':
    unittest.main()

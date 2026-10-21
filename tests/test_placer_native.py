"""Isolated saved-board writes, checked with actual pcbnew and native DRC."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from agent_ee.placer_runner import place_candidate
from test_placer import request
import test_placement_native as native_fixtures
from toolchain import require_toolchain


def export_rc_netlist(test, root):
    """Actual kicad-cli XML export of a scratch copy of the owned RC schematic.

    kicad-cli writes project-local settings beside the schematic, so the
    repository fixture itself is never the export target.
    """
    import shutil
    import subprocess
    fixture = Path(__file__).resolve().parents[1] / 'fixtures/rc/control'
    project = root / 'rc-project'
    project.mkdir()
    for name in ('rc_filter.kicad_sch', 'rc_filter.kicad_pro', 'sym-lib-table', 'fp-lib-table'):
        shutil.copyfile(fixture / name, project / name)
    exported = root / 'native-export.xml'
    completed = subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                                '-o', str(exported), str(project / 'rc_filter.kicad_sch')],
                               capture_output=True, text=True, timeout=60)
    test.assertEqual(completed.returncode, 0, completed.stderr)
    return exported


class NativePlacerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-placer-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        fixture = native_fixtures.NativePlacementTest()
        fixture.root = self.root
        self.source = fixture.board('overlap')
        self.saved = self.source.read_bytes()
        self.netlist = self.root / 'netlist.xml'
        # Owned native-XML dialect input, not claimed as kicad-cli export evidence.
        self.netlist.write_text('<export><components><comp ref="U1"/><comp ref="C1"/></components><nets/></export>')
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()
        self.config = {'policy': request(), 'authorization': {
            'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
            'source_sha256': hashlib.sha256(self.saved).hexdigest(), 'writable_root': str(self.scratch)}}

    def test_silk_only_proposal_publishes_receipt_with_open_defects(self):
        import json
        import pcbnew as k
        from agent_ee.placer_runner import _plan
        from agent_ee.placement_probe import extract
        board = k.LoadBoard(str(self.source))
        for fp in board.GetFootprints():
            fp.Reference().SetVisible(False)
            fp.Value().SetVisible(False)
            pad = next(iter(fp.Pads()))
            layers = pad.GetLayerSet()
            layers.AddLayer(k.F_Mask)
            pad.SetLayerSet(layers)
        k.SaveBoard(str(self.source), board)
        plan = _plan(extract(self.source), self.config)
        # Static board graphics at future pad positions cannot be relocated.
        for placement in plan['placements']:
            x, y = placement['position_nm']
            shape = k.PCB_SHAPE(board)
            shape.SetShape(k.SHAPE_T_SEGMENT)
            shape.SetLayer(k.F_SilkS)
            shape.SetStart(k.VECTOR2I(x - 200000, y))
            shape.SetEnd(k.VECTOR2I(x + 200000, y))
            shape.SetWidth(150000)
            board.Add(shape)
        k.SaveBoard(str(self.source), board)
        original = self.source.read_bytes()
        self.config['authorization']['source_sha256'] = hashlib.sha256(original).hexdigest()
        destination = self.scratch / 'open-silk'
        report = place_candidate(self.source, self.netlist, destination, self.config)
        self.assertEqual((report['status'], report['outcome'], report['verdict']),
                         ('placed', 'defect', 'defect'))
        self.assertTrue(report['open_defects'])
        self.assertTrue(all(d['type'] == 'placement_silkscreen_conflict' and
                            d['status'] == 'open' and d['verdict'] == 'defect'
                            for d in report['open_defects']))
        self.assertEqual(report['native_drc_comparison']['blocking_findings'], [])
        self.assertEqual(report['open_defects'], report['native_drc_comparison']['open_defects'])
        self.assertEqual(json.loads((destination / 'placement-result.json').read_text()), report)
        self.assertEqual(self.source.read_bytes(), original)

    def test_imu_axes_reserved_in_recorded_effective_policy(self):
        import subprocess
        script = """
import pcbnew as k, sys
b=k.LoadBoard(sys.argv[1])
t=k.PCB_TEXT(b); t.SetText('IMU_AXES'); t.SetLayer(k.F_SilkS)
t.SetPosition(k.VECTOR2I(3000000,3000000)); t.SetTextSize(k.VECTOR2I(800000,800000))
b.Add(t); assert k.SaveBoard(sys.argv[1], b)
"""
        result = subprocess.run(['/usr/bin/python3', '-B', '-c', script, str(self.source)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.config['authorization']['source_sha256'] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        source = self.source.read_bytes()
        result = place_candidate(self.source, self.netlist, self.scratch / 'marked', self.config)
        self.assertEqual((result['status'], result['outcome']), ('fixed_conflict', 'unknown'), result)
        self.assertIn('fixed placement U1 conflicts', result['reason'])
        self.assertTrue(any(o.get('source', '').startswith('IMU_AXES/') for o in result['policy']['obstacles']))
        self.assertEqual(self.config['policy']['obstacles'], [])
        self.assertEqual(self.source.read_bytes(), source)
        self.assertFalse((self.scratch / 'marked').exists())

    def test_touching_obstacle_union_survives_native_save_and_regrade(self):
        from agent_ee.placement import intersection_area
        from agent_ee.placement_probe import extract
        from test_placement import rectangle
        ring = [rectangle(1, 1, 5, 0.5), rectangle(1, 5.5, 5, 0.5),
                rectangle(1, 1.5, 0.5, 4), rectangle(5.5, 1.5, 0.5, 4)]
        self.config['policy']['obstacles'] = [{'side': 'F', 'polygons': ring}]
        destination = self.scratch / 'obstacle-union'
        report = place_candidate(self.source, self.netlist, destination, self.config)
        self.assertEqual(report['status'], 'placed', report)
        self.assertEqual(report['judgment']['verdict'], 'pass')
        self.assertEqual(report['native_courtyard_overlaps'], [])
        saved = extract(destination / 'candidate.kicad_pcb')
        for fp in saved['footprints']:
            for poly in fp['courtyards']['F']['polygons']:
                for obstacle in ring:
                    self.assertEqual(intersection_area(poly, obstacle), 0, fp['ref'])
        fixed = next(f for f in saved['footprints'] if f['ref'] == 'U1')
        self.assertEqual(fixed['position_nm'], [3000000, 3000000])
        self.assertEqual(self.source.read_bytes(), self.saved)

    def test_actual_saved_placements_repeat_and_preserve_static_and_source(self):
        first = place_candidate(self.source, self.netlist, self.scratch / 'one', self.config)
        second = place_candidate(self.source, self.netlist, self.scratch / 'two', self.config)
        self.assertEqual(first['status'], 'placed', first)
        self.assertEqual(second['status'], 'placed', second)
        self.assertEqual((self.scratch / 'one/candidate.kicad_pcb').read_bytes(),
                         (self.scratch / 'two/candidate.kicad_pcb').read_bytes())
        self.assertEqual(first['judgment']['verdict'], 'pass')
        self.assertEqual(first['native_courtyard_overlaps'], [])
        self.assertEqual(first['min_courtyard_edge_mm'], 0.0)
        from agent_ee.placement_predicates import edge_clearance
        from agent_ee.placement_probe import extract
        old = {f['ref']: f for f in extract(self.source)['footprints']}
        new = {f['ref']: f for f in extract(self.scratch / 'one/candidate.kicad_pcb')['footprints']}
        self.assertEqual(new['U1'], old['U1'])
        self.assertNotEqual(new['C1']['position_nm'], old['C1']['position_nm'])
        margined = place_candidate(self.source, self.netlist, self.scratch / 'margined',
                                   {**self.config, 'min_courtyard_edge_mm': 0.5})
        self.assertEqual((margined['status'], margined['min_courtyard_edge_mm']), ('placed', 0.5))
        self.assertEqual(edge_clearance(extract(self.scratch / 'margined/candidate.kicad_pcb'), 500_000)['status'], 'pass')
        with self.assertRaisesRegex(ValueError, 'conflicting min_courtyard_edge_mm'):
            place_candidate(self.source, self.netlist, self.scratch / 'conflict',
                            {**self.config, 'policy': request(min_courtyard_edge_mm=0.3), 'min_courtyard_edge_mm': 0.5})
        self.assertEqual(self.source.read_bytes(), self.saved)

    def owned_keepout_source(self):
        import pcbnew as k
        board = k.LoadBoard(str(self.source))
        owner = next(fp for fp in board.GetFootprints() if fp.GetReference() == 'U1')
        zone = k.ZONE(owner)
        zone.SetIsRuleArea(True)
        zone.SetDoNotAllowFootprints(True)
        zone.SetDoNotAllowTracks(False)
        zone.SetDoNotAllowVias(False)
        zone.SetDoNotAllowPads(False)
        layers = k.LSET()
        layers.AddLayer(k.F_Cu)
        layers.AddLayer(k.B_Cu)
        zone.SetLayerSet(layers)
        zone.Outline().NewOutline()
        for x, y in ((2, 2), (8, 2), (8, 4), (4, 4), (4, 6), (2, 6)):
            zone.Outline().Append(x*1_000_000, y*1_000_000)
        owner.Add(zone)
        k.SaveBoard(str(self.source), board)
        return self.source.read_bytes()

    def test_owned_keepout_native_drc_probe(self):
        import json
        import subprocess
        from agent_ee.executor import execute
        from agent_ee.placement import intersection_area, qualified_polygons
        from agent_ee.placement_probe import extract
        saved = self.owned_keepout_source()
        source_facts = extract(self.source)
        ids = {fp['ref']: fp['uuid'] for fp in source_facts['footprints']}
        drc = self.root / 'before-drc.json'
        run = subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
                              '--output', str(drc), str(self.source)], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        hits = {i['uuid'] for v in json.loads(drc.read_text())['violations']
                if v['type'] == 'items_not_allowed' for i in v['items']}
        self.assertIn(ids['C1'], hits)  # Native positive control: the peer is inside.
        self.assertNotIn(ids['U1'], hits)  # The owner overlaps its own rule area legally.
        # Run the actual writer, probe and native DRC in the networkless sandbox.
        inputs = self.root / 'keepout-inputs'
        inputs.mkdir()
        (inputs / 'board.kicad_pcb').write_bytes(saved)
        (inputs / 'netlist.xml').write_bytes(self.netlist.read_bytes())
        for fixed in (True, False):
            config = {'policy': request(fixed_refs=['U1'] if fixed else [], priority_refs=['U1'],
                                        step_budget=100000),
                      'authorization': {**self.config['authorization'], 'writable_root': '/work',
                                        'source_sha256': hashlib.sha256(saved).hexdigest()}}
            (inputs / 'request.json').write_text(json.dumps(config))
            work = self.root / ('keepout-fixed' if fixed else 'keepout-moving')
            execution = execute(inputs, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.placer_runner',
                '/candidate/board.kicad_pcb', '/candidate/netlist.xml', '--request', '/candidate/request.json',
                '--destination', '/work/proposal', '--result', '/work/result.json'], timeout=120)
            self.assertEqual(execution['execution'], 'ok', (work / 'console.log').read_text())
            result = json.loads((work / 'result.json').read_text())
            self.assertEqual((result['status'], result['write_scope']['status']), ('placed', 'allowed'))
            after = json.loads((work / 'proposal/placement-probe.json').read_text())
            area, = after['rule_areas']
            polygon, = qualified_polygons(area['outline'])
            peer = next(f for f in after['footprints'] if f['ref'] == 'C1')
            courtyard, = qualified_polygons(peer['courtyards']['F'])
            self.assertEqual(intersection_area(polygon, courtyard), 0)
            native = json.loads((work / 'proposal/native-drc.json').read_text())
            self.assertFalse(any(v['type'] == 'items_not_allowed' for v in native['violations']), native)
            if not fixed:
                self.assertIn('U1', [t['reference'] for t in result['write_scope']['transforms']])
        self.assertEqual(self.source.read_bytes(), saved)

    def test_saved_owned_zone_edit_is_retained_and_never_published(self):
        import json
        import pcbnew as k
        from unittest.mock import patch
        from agent_ee import placer_runner as runner
        saved = self.owned_keepout_source()
        self.config['authorization']['source_sha256'] = hashlib.sha256(saved).hexdigest()
        apply_plan = runner._apply_plan
        def clear_rule(board, plan, output):
            apply_plan(board, plan, output)
            changed = k.LoadBoard(str(output))
            owner = next(fp for fp in changed.GetFootprints() if fp.GetReference() == 'U1')
            next(iter(owner.Zones())).SetDoNotAllowFootprints(False)
            k.SaveBoard(str(output), changed)
        destination = self.scratch / 'illegal-zone-save'
        with patch.object(runner, '_apply_plan', clear_rule):
            with self.assertRaisesRegex(ValueError, 'exceeded write scope'):
                place_candidate(self.source, self.netlist, destination, self.config)
        self.assertFalse(destination.exists())
        retained, = self.scratch.glob('illegal-zone-save-failure-*')
        proof = json.loads((retained / 'placement-write-scope.json').read_text())
        self.assertEqual(proof['status'], 'refused')
        self.assertIn('zone', ' '.join(proof['violations']))
        failure = json.loads((retained / 'failure.json').read_text())
        self.assertEqual((failure['execution'], failure['outcome']), ('failed', 'unknown'))
        self.assertEqual(self.source.read_bytes(), saved)

    def test_refuses_unauthorized_or_existing_destination_and_wrong_netlist(self):
        import copy
        destination = self.scratch / 'proposal'
        for mutation in ('hash', 'writer', 'root'):
            config = copy.deepcopy(self.config)
            if mutation == 'hash':
                config['authorization']['source_sha256'] = 'b'*64
            elif mutation == 'writer':
                config['authorization']['single_design_writer'] = False
            else:
                config['authorization']['writable_root'] = str(self.root)
            with self.assertRaises(ValueError):
                place_candidate(self.source, self.netlist, destination, config)
            self.assertFalse(destination.exists())
        destination.mkdir()
        with self.assertRaises(ValueError):
            place_candidate(self.source, self.netlist, destination, self.config)
        self.netlist.write_text('<export><components><comp ref="R99"/></components><nets/></export>')
        with self.assertRaises(ValueError):
            place_candidate(self.source, self.netlist, self.scratch / 'wrong-nets', self.config)
        self.assertFalse((self.scratch / 'wrong-nets').exists())
        self.assertEqual(self.source.read_bytes(), self.saved)

    def test_budget_failure_and_native_failure_publish_no_partial_board(self):
        import copy
        from unittest.mock import patch
        config = copy.deepcopy(self.config)
        config['policy']['step_budget'] = 1
        destination = self.scratch / 'budget'
        report = place_candidate(self.source, self.netlist, destination, config)
        self.assertEqual(report['status'], 'packing_budget_exhausted')
        self.assertEqual(report['placements'], [])
        self.assertFalse(destination.exists())
        with patch('agent_ee.placer_runner._native_drc', side_effect=ValueError('injected tool failure')):
            with self.assertRaisesRegex(ValueError, 'injected tool failure'):
                place_candidate(self.source, self.netlist, self.scratch / 'failed', self.config)
        self.assertFalse((self.scratch / 'failed').exists())
        self.assertEqual(list(self.scratch.glob('.placement-staging-*')), [])
        diagnostics = list(self.scratch.glob('failed-failure-*'))
        self.assertEqual(len(diagnostics), 1)
        import json
        failure = json.loads((diagnostics[0] / 'failure.json').read_text())
        self.assertEqual((failure['execution'], failure['outcome']), ('failed', 'unknown'))
        self.assertIn('injected tool failure', failure['error'])
        self.assertTrue((diagnostics[0] / 'failed-candidate.kicad_pcb').is_file())
        self.assertEqual(self.source.read_bytes(), self.saved)

    def small_board(self, name, width, height):
        """Owned saved board: a width x height mm outline and two 2 x 2mm parts staged off it."""
        import pcbnew as k
        board = k.BOARD()
        def vec(x, y):
            return k.VECTOR2I(int(x*1e6), int(y*1e6))
        def loop(parent, layer, corners):
            for a, b in zip(corners, corners[1:] + corners[:1]):
                shape = k.PCB_SHAPE(parent)
                shape.SetShape(k.SHAPE_T_SEGMENT)
                shape.SetLayer(layer)
                shape.SetStart(vec(*a))
                shape.SetEnd(vec(*b))
                shape.SetWidth(50000)
                parent.Add(shape)
        loop(board, k.Edge_Cuts, [(0, 0), (width, 0), (width, height), (0, height)])
        for ref, x in (('U1', 40), ('C1', 45)):
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            fp.SetValue('owned')
            board.Add(fp)
            loop(fp, k.F_CrtYd, [(-1, -1), (1, -1), (1, 1), (-1, 1)])
            pad = k.PAD(fp)
            pad.SetNumber('1')
            pad.SetShape(k.PAD_SHAPE_RECT)
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetSize(vec(.5, .5))
            layers = k.LSET()
            layers.AddLayer(k.F_Cu)
            pad.SetLayerSet(layers)
            fp.Add(pad)
            fp.SetPosition(vec(x, 40))
        path = self.root / f'{name}.kicad_pcb'
        k.SaveBoard(str(path), board)
        return path

    def test_board_too_small_is_typed_defect_with_budget_and_no_board(self):
        import json
        from agent_ee.placer_runner import main
        # 3 x 2.5mm holds less than the two 8mm² courtyards; a 1.5mm strip is
        # narrower than either part at any angle although its area suffices.
        for name, width, height, kind in (('area', 3, 2.5, 'courtyard_area'), ('strip', 1.5, 30, 'min_width')):
            source = self.small_board(name, width, height)
            saved = source.read_bytes()
            config = {'policy': request(fixed_refs=[], step_budget=4321), 'authorization': {
                **self.config['authorization'], 'source_sha256': hashlib.sha256(saved).hexdigest()}}
            report = place_candidate(source, self.netlist, self.scratch / f'{name}-proposal', config)
            self.assertEqual((report['status'], report['outcome']), ('packing_infeasible', 'defect'), report)
            self.assertEqual(report['proof']['kind'], kind)
            self.assertEqual((report['step_budget'], report['steps_used'], report['placements']), (4321, 0, []))
            self.assertEqual(report['board_sha256'], hashlib.sha256(saved).hexdigest())
            # The CLI records the same typed failure, budget included, and exits nonzero.
            request_path = self.root / f'{name}-request.json'
            request_path.write_text(json.dumps(config))
            result = self.scratch / f'{name}-result.json'
            self.assertEqual(main([str(source), str(self.netlist), '--request', str(request_path),
                                   '--destination', str(self.scratch / f'{name}-cli'), '--result', str(result)]), 1)
            self.assertEqual(json.loads(result.read_text()), json.loads(json.dumps(report)))
            self.assertEqual(source.read_bytes(), saved)
        # Only the result files beside the writer lease locks: no proposal, staging or retained failure board.
        self.assertEqual(sorted(p.name for p in self.scratch.iterdir() if not p.name.endswith('.writer.lock')),
                         ['area-result.json', 'strip-result.json'])

    def test_writer_executes_in_actual_networkless_readonly_sandbox(self):
        import json
        from agent_ee.executor import execute
        inputs = self.root / 'inputs'
        inputs.mkdir()
        (inputs / 'board.kicad_pcb').write_bytes(self.saved)
        (inputs / 'netlist.xml').write_bytes(self.netlist.read_bytes())
        config = {'policy': request(), 'authorization': {**self.config['authorization'], 'writable_root': '/work'}}
        (inputs / 'request.json').write_text(json.dumps(config))
        work = self.root / 'sandbox-work'
        execution = execute(inputs, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.placer_runner',
            '/candidate/board.kicad_pcb', '/candidate/netlist.xml', '--request', '/candidate/request.json',
            '--destination', '/work/proposal', '--result', '/work/result.json'], timeout=60)
        self.assertEqual(execution['execution'], 'ok', (work / 'console.log').read_text())
        report = json.loads((work / 'result.json').read_text())
        self.assertEqual(report['status'], 'placed')
        self.assertEqual(report['judgment']['board_sha256'], hashlib.sha256(
            (work / 'proposal/candidate.kicad_pcb').read_bytes()).hexdigest())
        self.assertEqual((inputs / 'board.kicad_pcb').read_bytes(), self.saved)

    def test_actual_native_xml_export_drives_independent_rectangular_fixture(self):
        import xml.etree.ElementTree as ET
        import pcbnew as k
        exported = export_rc_netlist(self, self.root)
        xml = ET.parse(exported).getroot()
        refs = sorted(c.get('ref') for c in xml.findall('components/comp'))
        self.assertEqual(refs, ['C1', 'J1', 'R1'])
        board = k.BOARD()
        def vec(x, y):
            return k.VECTOR2I(int(x*1e6), int(y*1e6))
        for a, b in [((0,0),(30,0)), ((30,0),(30,30)), ((30,30),(0,30)), ((0,30),(0,0))]:
            shape = k.PCB_SHAPE(board)
            shape.SetShape(k.SHAPE_T_SEGMENT)
            shape.SetLayer(k.Edge_Cuts)
            shape.SetStart(vec(*a))
            shape.SetEnd(vec(*b))
            board.Add(shape)
        nets = {}
        for net in xml.findall('nets/net'):
            native = k.NETINFO_ITEM(board, net.get('name'))
            board.Add(native)
            for node in net.findall('node'):
                nets[node.get('ref'), node.get('pin')] = native
        for ref in refs:
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            board.Add(fp)
            rectangle = k.PCB_SHAPE(fp)
            rectangle.SetShape(k.SHAPE_T_RECT)
            rectangle.SetLayer(k.F_CrtYd)
            rectangle.SetStart(vec(-2,-2))
            rectangle.SetEnd(vec(2,2))
            rectangle.SetWidth(50000)
            fp.Add(rectangle)
            terminals = sorted(pin for r, pin in nets if r == ref)
            for i, pin in enumerate(terminals):
                pad = k.PAD(fp)
                pad.SetNumber(pin)
                pad.SetShape(k.PAD_SHAPE_RECT)
                pad.SetAttribute(k.PAD_ATTRIB_SMD)
                pad.SetSize(vec(.5,.5))
                pad.SetPosition(vec(i-1,0))
                layers = k.LSET()
                layers.AddLayer(k.F_Cu)
                pad.SetLayerSet(layers)
                pad.SetNet(nets[ref,pin])
                fp.Add(pad)
            fp.SetPosition(vec(10,10))
        source = self.root / 'native-netlist-board.kicad_pcb'
        k.SaveBoard(str(source), board)
        config = {'policy': request(fixed_refs=['J1']), 'authorization': {
            **self.config['authorization'], 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}}
        report = place_candidate(source, exported, self.scratch / 'native-export', config)
        self.assertEqual(report['status'], 'placed', report)
        self.assertEqual(report['judgment']['verdict'], 'pass')
        self.assertEqual(report['native_courtyard_overlaps'], [])

    def test_missing_native_drc_tool_is_unknown_with_retained_failed_proposal(self):
        import json
        from unittest.mock import patch
        with patch.dict('os.environ', {'PATH': '/nonexistent'}):
            with self.assertRaises((OSError, ValueError)):
                place_candidate(self.source, self.netlist, self.scratch / 'missing-tool', self.config)
        self.assertFalse((self.scratch / 'missing-tool').exists())
        diagnostics = list(self.scratch.glob('missing-tool-failure-*'))
        self.assertEqual(len(diagnostics), 1)
        failure = json.loads((diagnostics[0] / 'failure.json').read_text())
        self.assertEqual(failure['outcome'], 'unknown')
        self.assertIn('kicad-cli', failure['error'])
        self.assertEqual(self.source.read_bytes(), self.saved)


STOCK = Path('/usr/share/kicad/footprints')
MM = 1_000_000


def pad_facts(board_path):
    """Independent footprint-local pad facts read back through pcbnew."""
    import pcbnew as k
    facts = {}
    for fp in k.LoadBoard(str(board_path)).GetFootprints():
        facts[fp.GetReference()] = sorted(
            (p.GetNumber(), int(p.GetShape(k.F_Cu)), tuple(p.GetSize(k.F_Cu)), tuple(p.GetDrillSize()),
             tuple(p.GetFPRelativePosition()), round(p.GetFPRelativeOrientation().AsDegrees(), 6) % 360,
             round(p.GetRoundRectRadiusRatio(k.F_Cu), 6), round(p.GetChamferRectRatio(k.F_Cu), 6),
             int(p.GetChamferPositions(k.F_Cu)), int(p.GetAttribute()), p.GetNetname())
            for p in fp.Pads())
    return facts


class StockPadShapeNativeTest(unittest.TestCase):
    """AGENTEE-109: reviewed native pad shapes, unchanged stock footprints."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-stock-pads-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()
        self.netlist = self.root / 'netlist.xml'
        # Owned native-XML dialect input, not claimed as kicad-cli export evidence.
        self.netlist.write_text('<export><components><comp ref="U1"/><comp ref="C1"/></components><nets/></export>')

    def config(self, source, **policy):
        return {'policy': request(**policy), 'authorization': {
            'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'writable_root': str(self.scratch)}}

    def shaped_board(self, name, pads, locked=False):
        """U1 and C1 with 4 mm courtyards; C1 carries the given (shape, degrees) pads."""
        import pcbnew as k
        board = k.BOARD()
        def vec(x, y):
            return k.VECTOR2I(int(x*MM), int(y*MM))
        def line(parent, layer, a, b):
            shape = k.PCB_SHAPE(parent)
            shape.SetShape(k.SHAPE_T_SEGMENT)
            shape.SetLayer(layer)
            shape.SetStart(vec(*a))
            shape.SetEnd(vec(*b))
            shape.SetWidth(50000)
            parent.Add(shape)
        corners = [(0, 0), (30, 0), (30, 30), (0, 30)]
        for i, a in enumerate(corners):
            line(board, k.Edge_Cuts, a, corners[(i+1) % 4])
        for ref, spec in (('U1', [(k.PAD_SHAPE_RECT, 0)]), ('C1', pads)):
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            fp.SetValue('owned')
            board.Add(fp)
            box = [(-2, -2), (2, -2), (2, 2), (-2, 2)]
            for i, a in enumerate(box):
                line(fp, k.F_CrtYd, a, box[(i+1) % 4])
            for i, (shape, degrees) in enumerate(spec):
                pad = k.PAD(fp)
                pad.SetNumber(str(i + 1))
                if shape == 'per_layer':
                    pad.Padstack().SetMode(k.PADSTACK.MODE_FRONT_INNER_BACK)
                    shape = k.PAD_SHAPE_RECT
                pad.SetShape(k.F_Cu, shape)
                pad.SetSize(k.F_Cu, vec(.6, .4))
                if shape in (k.PAD_SHAPE_CIRCLE, k.PAD_SHAPE_OVAL):
                    # Plated through-hole with a real drill, so drills are compared too.
                    pad.SetAttribute(k.PAD_ATTRIB_PTH)
                    pad.SetSize(k.F_Cu, vec(.9, .9) if shape == k.PAD_SHAPE_CIRCLE else vec(1.2, .8))
                    pad.SetDrillSize(vec(.5, .5))
                    pad.SetLayerSet(k.PAD.PTHMask())
                else:
                    pad.SetAttribute(k.PAD_ATTRIB_SMD)
                    layers = k.LSET()
                    layers.AddLayer(k.F_Cu)
                    pad.SetLayerSet(layers)
                if shape == k.PAD_SHAPE_ROUNDRECT:
                    pad.SetRoundRectRadiusRatio(k.F_Cu, .25)
                if shape == k.PAD_SHAPE_CHAMFERED_RECT:
                    pad.SetChamferRectRatio(k.F_Cu, .2)
                    pad.SetChamferPositions(k.F_Cu, 1)  # RECT_CHAMFER_TOP_LEFT; not exported by SWIG.
                if shape == k.PAD_SHAPE_TRAPEZOID:
                    pad.SetDelta(k.F_Cu, vec(.1, 0))
                if shape == k.PAD_SHAPE_CUSTOM:
                    pad.SetAnchorPadShape(k.F_Cu, k.PAD_SHAPE_RECT)
                pad.SetPosition(vec(-1.5 + i*.75, 0))
                pad.SetOrientationDegrees(degrees)
                fp.Add(pad)
            fp.SetPosition(vec(3, 3) if ref == 'U1' else vec(3.5, 3))
            if ref == 'U1' and locked:
                fp.SetLocked(True)
        path = self.root / (name + '.kicad_pcb')
        k.SaveBoard(str(path), board)
        return path

    def test_every_qualified_native_shape_places_at_quarter_turns_with_unchanged_pads(self):
        import pcbnew as k
        from agent_ee.placement_probe import extract
        pads = [(k.PAD_SHAPE_CIRCLE, 0), (k.PAD_SHAPE_RECT, 90), (k.PAD_SHAPE_OVAL, 90),
                (k.PAD_SHAPE_ROUNDRECT, 180), (k.PAD_SHAPE_CHAMFERED_RECT, 270)]
        source = self.shaped_board('qualified', pads)
        saved = source.read_bytes()
        self.assertEqual([p['shape'] for p in extract(source)['footprints'][0]['pads']], [0, 1, 2, 4, 5])
        report = place_candidate(source, self.netlist, self.scratch / 'qualified', self.config(source))
        self.assertEqual(report['status'], 'placed', report)
        self.assertEqual(report['judgment']['verdict'], 'pass')
        self.assertEqual(report['native_courtyard_overlaps'], [])
        self.assertEqual(report['plan']['pad_shapes']['accepted'],
                         {'0': 'circle', '1': 'rect', '2': 'oval', '4': 'roundrect', '5': 'chamfered_rect'})
        candidate = self.scratch / 'qualified/candidate.kicad_pcb'
        before, after = pad_facts(source), pad_facts(candidate)
        self.assertEqual(after, before)
        self.assertEqual([p[1] for p in after['C1']], [0, 1, 2, 4, 5])
        self.assertEqual([p[3] for p in after['C1']][:3], [(500000, 500000), (0, 0), (500000, 500000)])
        moved = {f['ref']: f for f in extract(candidate)['footprints']}['C1']
        self.assertNotEqual(moved['position_nm'], [3500000, 3000000])
        # Absolute pad angles follow the footprint by a quarter turn.
        self.assertEqual({p['rotation_deg'] % 90 for p in moved['pads']}, {0})
        self.assertEqual(source.read_bytes(), saved)

    def test_unqualified_native_pads_angles_padstacks_and_courtyards_are_typed_unknown(self):
        import pcbnew as k
        cases = {'trapezoid': [(k.PAD_SHAPE_TRAPEZOID, 0)], 'custom': [(k.PAD_SHAPE_CUSTOM, 0)],
                 'angled': [(k.PAD_SHAPE_ROUNDRECT, 45)], 'per-layer': [('per_layer', 0)]}
        reasons = {'trapezoid': 'native pad shape 3 not qualified', 'custom': 'native pad shape 6 not qualified',
                   'angled': 'pad rotation must be a quarter turn', 'per-layer': 'per-layer padstack not qualified'}
        fixture = native_fixtures.NativePlacementTest()
        fixture.root = self.root
        boards = {name: self.shaped_board(name, pads) for name, pads in cases.items()}
        boards.update({variant: fixture.board(variant) for variant in ('missing_courtyard', 'rotated')})
        reasons.update({'missing_courtyard': 'courtyard', 'rotated': 'quarter turns'})
        for name, source in boards.items():
            saved = source.read_bytes()
            destination = self.scratch / name
            report = place_candidate(source, self.netlist, destination, self.config(source))
            self.assertEqual((report['status'], report['outcome']), ('unsupported_geometry', 'unknown'), name)
            self.assertIn(reasons[name], report['reason'])
            self.assertEqual(report['placements'], [])
            self.assertFalse(destination.exists())
            self.assertEqual(source.read_bytes(), saved)
        # Only the writer lease files remain; no proposal or staging directory.
        self.assertEqual([p.name for p in self.scratch.iterdir() if not p.name.endswith('.writer.lock')], [])

    def test_locked_footprint_stays_unchanged_with_stock_shapes(self):
        import pcbnew as k
        from agent_ee.placement_probe import extract
        source = self.shaped_board('locked', [(k.PAD_SHAPE_ROUNDRECT, 0), (k.PAD_SHAPE_CIRCLE, 0)], locked=True)
        report = place_candidate(source, self.netlist, self.scratch / 'locked', self.config(source, fixed_refs=[]))
        self.assertEqual(report['status'], 'placed', report)
        old = {f['ref']: f for f in extract(source)['footprints']}
        new = {f['ref']: f for f in extract(self.scratch / 'locked/candidate.kicad_pcb')['footprints']}
        self.assertEqual(new['U1'], old['U1'])
        self.assertTrue(next(p for p in report['plan']['placements'] if p['ref'] == 'U1')['fixed'])
        self.assertNotEqual(new['C1']['position_nm'], old['C1']['position_nm'])

    def test_pad_rewrite_in_saved_board_is_refused_and_not_published(self):
        import json
        import pcbnew as k
        from unittest.mock import patch
        import agent_ee.placer_runner as runner
        source = self.shaped_board('rewrite', [(k.PAD_SHAPE_ROUNDRECT, 0), (k.PAD_SHAPE_CIRCLE, 0)])
        original = runner._apply_plan
        def rect_rewrite(board, plan, output):
            # The rejected stand-in: stock pads silently turned into rectangles.
            for fp in board.GetFootprints():
                for pad in fp.Pads():
                    pad.SetShape(k.F_Cu, k.PAD_SHAPE_RECT)
            original(board, plan, output)
        with patch.object(runner, '_apply_plan', rect_rewrite):
            with self.assertRaisesRegex(ValueError, 'changed footprint pad geometry'):
                place_candidate(source, self.netlist, self.scratch / 'rewrite', self.config(source))
        self.assertFalse((self.scratch / 'rewrite').exists())
        diagnostics = list(self.scratch.glob('rewrite-failure-*'))
        self.assertEqual(len(diagnostics), 1)
        failure = json.loads((diagnostics[0] / 'failure.json').read_text())
        self.assertEqual((failure['status'], failure['outcome']), ('not_placed', 'unknown'))

    def test_unchanged_stock_rc_footprints_place_on_50x30_envelope_with_fixed_j1(self):
        import xml.etree.ElementTree as ET
        import pcbnew as k
        from agent_ee.placement import judge
        from agent_ee.placement_probe import extract
        exported = export_rc_netlist(self, self.root)
        xml = ET.parse(exported).getroot()
        self.assertEqual(sorted((c.get('ref'), c.findtext('footprint')) for c in xml.findall('components/comp')),
                         [('C1', 'Capacitor_SMD:C_0805_2012Metric'),
                          ('J1', 'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical'),
                          ('R1', 'Resistor_SMD:R_0805_2012Metric')])
        board = k.BOARD()
        corners = [(100, 100), (150, 100), (150, 130), (100, 130)]
        for i, a in enumerate(corners):
            edge = k.PCB_SHAPE(board)
            edge.SetShape(k.SHAPE_T_SEGMENT)
            edge.SetLayer(k.Edge_Cuts)
            edge.SetStart(k.VECTOR2I(a[0]*MM, a[1]*MM))
            edge.SetEnd(k.VECTOR2I(corners[(i+1) % 4][0]*MM, corners[(i+1) % 4][1]*MM))
            edge.SetWidth(100000)
            board.Add(edge)
        nets = {}
        for net in xml.findall('nets/net'):
            native = k.NETINFO_ITEM(board, net.get('name'))
            board.Add(native)
            for node in net.findall('node'):
                nets[node.get('ref'), node.get('pin')] = native
        stock, digests = {}, {}
        for i, comp in enumerate(sorted(xml.findall('components/comp'), key=lambda c: c.get('ref'))):
            ref = comp.get('ref')
            library, name = comp.findtext('footprint').split(':')
            path = STOCK / f'{library}.pretty' / f'{name}.kicad_mod'
            digests[path] = hashlib.sha256(path.read_bytes()).hexdigest()
            fp = k.FootprintLoad(str(path.parent), name)
            stock[ref] = k.FootprintLoad(str(path.parent), name), library
            fp.SetFPID(k.LIB_ID(library, name))
            fp.SetReference(ref)
            board.Add(fp)
            for pad in fp.Pads():
                pad.SetNet(nets[ref, pad.GetNumber()])
            if ref == 'J1':
                fp.SetPosition(k.VECTOR2I(103_810_000, 112_460_000))
                fp.SetLocked(True)
            else:
                fp.SetPosition(k.VECTOR2I(160_000_000 + i*5_080_000, 140_000_000))  # Off-board staging.
        source = self.root / 'stock-rc.kicad_pcb'
        k.SaveBoard(str(source), board)
        facts = extract(source)
        self.assertEqual({f['ref']: sorted(p['shape'] for p in f['pads']) for f in facts['footprints']},
                         {'C1': [4, 4], 'R1': [4, 4], 'J1': [0, 0, 1]})
        report = place_candidate(source, exported, self.scratch / 'stock-rc', self.config(source, fixed_refs=['J1']))
        self.assertEqual(report['status'], 'placed', report)
        candidate = self.scratch / 'stock-rc/candidate.kicad_pcb'
        placed = extract(candidate)
        self.assertEqual(judge(placed)['verdict'], 'pass')
        self.assertEqual(report['judgment']['board_sha256'], hashlib.sha256(candidate.read_bytes()).hexdigest())
        self.assertEqual(report['native_courtyard_overlaps'], [])
        import json
        drc = json.loads((self.scratch / 'stock-rc/native-drc.json').read_text())
        self.assertEqual([v for v in drc['violations'] if 'courtyard' in v['type']], [])
        by_ref = {f['ref']: f for f in placed['footprints']}
        self.assertEqual((by_ref['J1']['position_nm'], by_ref['J1']['locked']), ([103_810_000, 112_460_000], True))
        for ref in ('C1', 'R1'):
            x, y = by_ref[ref]['position_nm']
            self.assertTrue(100*MM < x < 150*MM and 100*MM < y < 130*MM, (ref, x, y))
        # Every placed pad keeps the stock library's footprint-local geometry: no rectangle stand-in.
        placed_pads = pad_facts(candidate)
        for ref, (fp, library) in stock.items():
            library_pads = sorted((p.GetNumber(), int(p.GetShape(k.F_Cu)), tuple(p.GetSize(k.F_Cu)),
                                   tuple(p.GetDrillSize()), tuple(p.GetFPRelativePosition()))
                                  for p in fp.Pads())
            self.assertEqual([row[:5] for row in placed_pads[ref]], library_pads, ref)
        self.assertEqual(placed_pads, pad_facts(source))
        self.assertEqual({(r, p[0]): p[-1] for r, pads in placed_pads.items() for p in pads},
                         {('J1', '1'): '/IN', ('R1', '1'): '/IN', ('R1', '2'): '/OUT', ('C1', '1'): '/OUT',
                          ('J1', '2'): '/OUT', ('C1', '2'): 'GND', ('J1', '3'): 'GND'})
        for path, sha in digests.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sha)

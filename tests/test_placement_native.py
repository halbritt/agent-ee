"""Owned boards constructed independently through pcbnew, saved, then reloaded."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_ee.placement import judge
from agent_ee.placement_probe import extract
from toolchain import require_toolchain


class NativePlacementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-placement-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def board(self, variant='control'):
        import pcbnew as k
        board = k.BOARD()
        def vec(x, y):
            return k.VECTOR2I(int(x*1e6), int(y*1e6))
        def line(parent, layer, a, b):
            shape = k.PCB_SHAPE(parent)
            shape.SetShape(k.SHAPE_T_SEGMENT)
            shape.SetLayer(layer)
            shape.SetStart(vec(*a))
            shape.SetEnd(vec(*b))
            shape.SetWidth(50000)
            parent.Add(shape)
        outline = [(0, 0), (20, 0), (20, 20), (12, 20), (12, 8), (8, 8), (8, 20), (0, 20)]
        if variant != 'missing_outline':
            for i, a in enumerate(outline):
                if variant == 'open_outline' and i == 0:
                    continue
                line(board, k.Edge_Cuts, a, outline[(i+1) % len(outline)])
        for ref, x, y in [('U1', 3, 3), ('C1', 15, 3)]:
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            fp.SetValue('owned')
            board.Add(fp)
            if variant != 'missing_courtyard' or ref != 'C1':
                corners = [(-1,-1), (1,-1), (1,1), (-1,1)]
                for i, a in enumerate(corners):
                    line(fp, k.F_CrtYd, a, corners[(i+1) % 4])
            pad = k.PAD(fp)
            pad.SetNumber('1')
            pad.SetShape(k.PAD_SHAPE_RECT)
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetSize(vec(.5,.5))
            layers = k.LSET()
            layers.AddLayer(k.F_Cu)
            pad.SetLayerSet(layers)
            fp.Add(pad)
            if ref == 'C1':
                if variant in ('overlap', 'opposite'):
                    x, y = 3.5, 3
                if variant == 'outside':
                    x, y = 10, 10
                if variant == 'opposite':
                    fp.Flip(vec(0, 0), False)
            fp.SetPosition(vec(x, y))
            if variant == 'rotated':
                fp.SetOrientationDegrees(45)
        if variant == 'curved_outline':
            shape = k.PCB_SHAPE(board)
            shape.SetShape(k.SHAPE_T_CIRCLE)
            shape.SetLayer(k.Edge_Cuts)
            shape.SetStart(vec(30,30))
            shape.SetEnd(vec(31,30))
            board.Add(shape)
        path = self.root / (variant + '.kicad_pcb')
        k.SaveBoard(str(path), board)
        return path

    def test_saved_native_control_mutations_and_hash_binding(self):
        expected = {'control': 'pass', 'rotated': 'pass', 'overlap': 'hard_fail', 'opposite': 'pass',
                    'outside': 'hard_fail', 'missing_outline': 'unknown', 'open_outline': 'unknown',
                    'curved_outline': 'unknown', 'missing_courtyard': 'unknown'}
        for variant, verdict in expected.items():
            with self.subTest(variant=variant):
                path = self.board(variant)
                saved = path.read_bytes()
                facts = extract(path)
                report = judge(facts)
                self.assertEqual(report['verdict'], verdict, report)
                self.assertEqual(facts['board_sha256'], hashlib.sha256(saved).hexdigest())
                self.assertEqual(path.read_bytes(), saved)
                if variant == 'outside':
                    self.assertEqual(report['hard_failures'][0]['outside_area_mm2'], 4)
                    self.assertEqual(report['off_board'], ['C1'])
                if variant in ('missing_outline', 'open_outline', 'curved_outline'):
                    status = 'outline_missing' if variant == 'missing_outline' else 'outline_unproven'
                    self.assertEqual(facts['outline']['status'], status)
                    self.assertEqual(report['unknown'], [{'kind': 'outline', 'reason': status}])
                if variant == 'missing_courtyard':
                    self.assertEqual(report['unknown'], [{'kind': 'courtyard', 'ref': 'C1', 'reason': 'missing'}])
                if variant == 'overlap':
                    self.assertEqual(report['hard_failures'][0]['overlap_area_mm2'], 3)

    def test_native_drc_control_and_overlap_agree(self):
        for variant, expected in [('control', False), ('overlap', True)]:
            path = self.board(variant)
            output = self.root / (variant + '.drc.json')
            command = ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--output', str(output), str(path)]
            process = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(process.returncode, 0, process.stderr)
            report = json.loads(output.read_text())
            overlaps = [v for v in report['violations'] if v['type'] == 'courtyards_overlap']
            self.assertEqual(bool(overlaps), expected, report)

    def test_real_probe_runs_in_readonly_networkless_tool_sandbox(self):
        from agent_ee.executor import execute
        inputs, work = self.root / 'inputs', self.root / 'work'
        inputs.mkdir()
        source = self.board('overlap')
        target = inputs / 'board.kicad_pcb'
        target.write_bytes(source.read_bytes())
        execution = execute(inputs, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.placement_probe',
                            '/candidate/board.kicad_pcb', '--out', '/work/placement.json'])
        self.assertEqual(execution['execution'], 'ok', (work / 'console.log').read_text())
        facts = json.loads((work / 'placement.json').read_text())
        self.assertEqual(judge(facts)['verdict'], 'hard_fail')
        self.assertEqual(facts['board_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())

    def test_saved_cap_nets_and_centres_support_declared_association(self):
        import pcbnew as k
        path = self.board()
        board = k.LoadBoard(str(path))
        net = k.NETINFO_ITEM(board, 'VCC')
        board.Add(net)
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                pad.SetNet(net)
        k.SaveBoard(str(path), board)
        policy = {'schema': 'agent-ee.placement-policy/1', 'ground_nets': ['GND'], 'ic_refs': ['U1'],
                  'connector_refs': [], 'max_shared_refs': 8,
                  'capacitors': {'C1': {'capacitance_nf': 100, 'max_distance_mm': 2.5, 'deduction': 15}}}
        report = judge(extract(path), policy)
        self.assertEqual(report['score'], 85)
        self.assertEqual(report['associations'][0]['distance_mm'], 12)
        self.assertEqual(report['associations'][0]['ic'], 'U1')

    def test_coordinator_adapter_refuses_undeclared_criteria_before_any_tool(self):
        """Criterion checks with a native DRC prerequisite live in test_placement_gates_native."""
        import time
        from agent_ee.checks import Ctx, CheckError
        from agent_ee.placement_check import check_placement
        path = self.board('control')
        run = self.root / 'run'
        (run / 'inputs').mkdir(parents=True)
        (run / 'inputs/board.kicad_pcb').write_bytes(path.read_bytes())
        manifest = {'candidate_id': 'owned-control', 'scope': {'board': 'board.kicad_pcb'},
                    'files': {'board.kicad_pcb': hashlib.sha256(path.read_bytes()).hexdigest()}}
        for params in ({}, {'score_threshold': 70}, {'criterion': 'courtyard-overlap/1'},
                       {'criterion': 'courtyard-overlap/1', 'drc_check': 'drc'}):
            with self.subTest(params=params):
                ctx = Ctx(run, manifest, {'id': 'placement', 'kind': 'placement', 'params': params},
                          time.monotonic() + 60)
                with self.assertRaises(CheckError) as caught:
                    check_placement(ctx)
                self.assertEqual(caught.exception.execution, 'unsupported' if 'drc_check' not in params else 'not_run')
                self.assertEqual(ctx.result['outcome'], 'unknown')
                self.assertNotIn('tool', ctx.result['details'])


class NativeDecouplingScoreTest(unittest.TestCase):
    """Owned SOIC-8 with two 0402 capacitors: the Konnect-shaped scoring case.

    The board is built here, not copied from the Konnect fixture. The 0402 pad
    offset and size are the generic values KiCad's stock 0402 footprint also
    uses; the SOIC pads and both courtyards are written for this test. C1
    (100 nF, limit 2.5 mm) sits 4 mm and C2 (1 µF, limit 5 mm) 7 mm from U1;
    each declared deduction is 15. In the connector variant U1 pin 1 and C3
    share VBUS with J1, 2.25 mm from J1 and 5.75 mm from U1. In the VCC
    connector variants J1 carries VCC and GND like the Konnect fixture's
    connector, either far from both caps or 3 mm from C2.
    """
    POLICY = {'schema': 'agent-ee.placement-policy/1', 'ground_nets': ['GND'], 'ic_refs': ['U1'],
              'connector_refs': [], 'max_shared_refs': 8,
              'capacitors': {'C1': {'capacitance_nf': 100, 'max_distance_mm': 2.5, 'deduction': 15},
                             'C2': {'capacitance_nf': 1000, 'max_distance_mm': 5, 'deduction': 15}}}

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-placement-soic-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def board(self, variant='control'):
        import pcbnew as k
        board = k.BOARD()
        def vec(x, y):
            return k.VECTOR2I(round(x*1e6), round(y*1e6))
        def outline(parent, layer, w, h):
            corners = [(-w, -h), (w, -h), (w, h), (-w, h)]
            for i, a in enumerate(corners):
                shape = k.PCB_SHAPE(parent)
                shape.SetShape(k.SHAPE_T_SEGMENT)
                shape.SetLayer(layer)
                shape.SetStart(vec(*a))
                shape.SetEnd(vec(*corners[(i+1) % 4]))
                shape.SetWidth(50000)
                parent.Add(shape)
        nets = {}
        for name in ('VCC', 'GND', 'VBUS'):
            nets[name] = k.NETINFO_ITEM(board, name)
            board.Add(nets[name])
        def footprint(ref, value, x, y, courtyard, pads):
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            fp.SetValue(value)
            board.Add(fp)
            outline(fp, k.F_CrtYd, *courtyard)
            for number, px, py, size, net in pads:
                pad = k.PAD(fp)
                pad.SetNumber(number)
                pad.SetShape(k.PAD_SHAPE_RECT)
                pad.SetAttribute(k.PAD_ATTRIB_SMD)
                pad.SetSize(vec(*size))
                layers = k.LSET()
                layers.AddLayer(k.F_Cu)
                pad.SetLayerSet(layers)
                pad.SetPosition(vec(px, py))
                fp.Add(pad)
                if net:
                    pad.SetNet(nets[net])
            fp.SetPosition(vec(x, y))
            return fp
        edge = k.PCB_SHAPE(board)
        edge.SetShape(k.SHAPE_T_RECT)
        edge.SetLayer(k.Edge_Cuts)
        edge.SetStart(vec(0, 0))
        edge.SetEnd(vec(25, 20))
        edge.SetWidth(50000)
        board.Add(edge)
        soic = {1: 'VBUS' if variant == 'connector' else None, 4: 'GND', 8: 'VCC'}
        footprint('U1', 'owned SOIC-8', 10, 10, (3.7, 2.7),
                  [(str(n), -2.7 if n <= 4 else 2.7, -1.905 + 1.27*((n-1) if n <= 4 else (8-n)),
                    (1.55, .6), soic.get(n)) for n in range(1, 9)])
        chip = (1.0, .5)
        def cap(ref, value, x, y, net='VCC'):
            footprint(ref, value, x, y, chip,
                      [('1', -.48, 0, (.56, .62), net), ('2', .48, 0, (.56, .62), 'GND')])
        cap('C1', '100nF', 10.5 if variant == 'overlap' else 10, 12.5 if variant == 'overlap' else 14)
        cap('C2', '1uF', 17, 10)
        if variant == 'connector':
            footprint('J1', 'owned 2-pin', 2, 10, (1.0, 2.0),
                      [('1', 0, -1, (1.2, 1.2), 'VBUS'), ('2', 0, 1, (1.2, 1.2), 'GND')])
            cap('C3', '100nF', 4.25, 10, 'VBUS')
        if variant in ('far_vcc_connector', 'near_vcc_connector'):
            x, y = (22, 17) if variant == 'far_vcc_connector' else (17, 13)
            footprint('J1', 'owned 2-pin', x, y, (1.0, 2.0),
                      [('1', 0, -1, (1.2, 1.2), 'VCC'), ('2', 0, 1, (1.2, 1.2), 'GND')])
        path = self.root / (variant + '.kicad_pcb')
        k.SaveBoard(str(path), board)
        return path

    def test_saved_soic8_two_0402_caps_score70_with_only_decoupling_deductions(self):
        from agent_ee.executor import execute
        inputs, work = self.root / 'inputs', self.root / 'work'
        inputs.mkdir()
        (inputs / 'board.kicad_pcb').write_bytes(self.board().read_bytes())
        execution = execute(inputs, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.placement_probe',
                            '/candidate/board.kicad_pcb', '--out', '/work/placement.json'])
        self.assertEqual(execution['execution'], 'ok', (work / 'console.log').read_text())
        facts = json.loads((work / 'placement.json').read_text())
        self.assertEqual(facts['outline']['status'], 'proven')
        report = judge(facts, self.POLICY)
        self.assertEqual((report['verdict'], report['score']), ('pass', 70), report)
        self.assertEqual((report['hard_failures'], report['unknown'], report['off_board']), ([], [], []))
        self.assertEqual([(a['kind'], a['ref'], a['ic'], a['distance_mm']) for a in report['advisories']],
                         [('decoupling_distance', 'C1', 'U1', 4), ('decoupling_distance', 'C2', 'U1', 7)])
        self.assertEqual([(a['kind'], a['nets']) for a in report['associations']],
                         [('decoupling', ['VCC']), ('decoupling', ['VCC'])])

    def test_same_side_overlap_is_hard_fail_whatever_the_score(self):
        facts = extract(self.board('overlap'))
        report = judge(facts, self.POLICY)
        self.assertEqual((report['verdict'], report['score']), ('hard_fail', 70))
        self.assertEqual([(f['kind'], f['refs']) for f in report['hard_failures']],
                         [('courtyard_overlap', ['C1', 'U1'])])
        lenient = {**self.POLICY, 'capacitors': {r: {**c, 'max_distance_mm': 50}
                                                 for r, c in self.POLICY['capacitors'].items()}}
        self.assertEqual(judge(facts, lenient)['score'], 100)
        self.assertEqual(judge(facts, lenient)['verdict'], 'hard_fail')

    def test_vbus_cap_within_limit_is_an_interface_filter_without_deduction(self):
        policy = {**self.POLICY, 'connector_refs': ['J1'],
                  'capacitors': {**self.POLICY['capacitors'],
                                 'C3': {'capacitance_nf': 100, 'max_distance_mm': 2.5, 'deduction': 15}}}
        report = judge(extract(self.board('connector')), policy)
        self.assertEqual((report['verdict'], report['score']), ('pass', 70), report)
        filters = [a for a in report['associations'] if a['ref'] == 'C3']
        self.assertEqual(filters, [{'ref': 'C3', 'kind': 'interface_filter', 'connectors': ['J1'],
                                    'nearest_connector': 'J1', 'distance_mm': 2.25, 'nets': ['VBUS']}])
        self.assertEqual(sorted(a['ref'] for a in report['advisories']), ['C1', 'C2'])

    def test_vcc_connector_far_from_the_caps_waives_nothing(self):
        policy = {**self.POLICY, 'connector_refs': ['J1']}
        report = judge(extract(self.board('far_vcc_connector')), policy)
        self.assertEqual((report['verdict'], report['score']), ('pass', 70), report)
        self.assertEqual([(a['kind'], a['ref'], a['ic'], a['distance_mm']) for a in report['advisories']],
                         [('decoupling_distance', 'C1', 'U1', 4), ('decoupling_distance', 'C2', 'U1', 7)])
        self.assertEqual([(a['ref'], a['kind'], a['nets']) for a in report['associations']],
                         [('C1', 'decoupling', ['VCC']), ('C2', 'decoupling', ['VCC'])])

    def test_vcc_connector_within_one_cap_limit_waives_only_that_cap(self):
        policy = {**self.POLICY, 'connector_refs': ['J1']}
        report = judge(extract(self.board('near_vcc_connector')), policy)
        self.assertEqual((report['verdict'], report['score']), ('pass', 85), report)
        self.assertEqual([(a['kind'], a['ref']) for a in report['advisories']], [('decoupling_distance', 'C1')])
        self.assertEqual(report['associations'][1], {'ref': 'C2', 'kind': 'interface_filter', 'connectors': ['J1'],
                                                     'nearest_connector': 'J1', 'distance_mm': 3, 'nets': ['VCC']})

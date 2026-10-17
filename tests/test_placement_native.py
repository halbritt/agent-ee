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

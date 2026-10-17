"""Isolated saved-board writes, checked with actual pcbnew and native DRC."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from agent_ee.placer_runner import place_candidate
from test_placer import request
import test_placement_native as native_fixtures
from toolchain import require_toolchain


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

    def test_actual_saved_placements_repeat_and_preserve_static_and_source(self):
        first = place_candidate(self.source, self.netlist, self.scratch / 'one', self.config)
        second = place_candidate(self.source, self.netlist, self.scratch / 'two', self.config)
        self.assertEqual(first['status'], 'placed', first)
        self.assertEqual(second['status'], 'placed', second)
        self.assertEqual((self.scratch / 'one/candidate.kicad_pcb').read_bytes(),
                         (self.scratch / 'two/candidate.kicad_pcb').read_bytes())
        self.assertEqual(first['judgment']['verdict'], 'pass')
        self.assertEqual(first['native_courtyard_overlaps'], [])
        from agent_ee.placement_probe import extract
        old = {f['ref']: f for f in extract(self.source)['footprints']}
        new = {f['ref']: f for f in extract(self.scratch / 'one/candidate.kicad_pcb')['footprints']}
        self.assertEqual(new['U1'], old['U1'])
        self.assertNotEqual(new['C1']['position_nm'], old['C1']['position_nm'])
        self.assertEqual(self.source.read_bytes(), self.saved)

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
        import subprocess
        import xml.etree.ElementTree as ET
        import pcbnew as k
        schematic = Path(__file__).resolve().parents[1] / 'fixtures/rc/control/rc_filter.kicad_sch'
        exported = self.root / 'native-export.xml'
        completed = subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                                    '-o', str(exported), str(schematic)], capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
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

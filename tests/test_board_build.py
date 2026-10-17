"""Board build from a native XML netlist, checked with actual pcbnew, placer and DRC."""
import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # Also run as tests.test_board_build.
from agent_ee import board_build  # noqa: E402
from agent_ee.board_build import BoardBuildError, read_envelope, read_netlist, read_request  # noqa: E402
from toolchain import require_toolchain  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures/rc/control'
STOCK = Path('/usr/share/kicad/footprints')
LIBRARIES = {'Resistor_SMD': 'R_0805_2012Metric', 'Capacitor_SMD': 'C_0805_2012Metric',
             'Connector_PinHeader_2.54mm': 'PinHeader_1x03_P2.54mm_Vertical'}
# Independent expectation: the shared rung-1 connectivity (rung1-contract).
EXPECTED = {('J1', '1'): '/IN', ('R1', '1'): '/IN', ('R1', '2'): '/OUT', ('C1', '1'): '/OUT',
            ('J1', '2'): '/OUT', ('C1', '2'): 'GND', ('J1', '3'): 'GND'}
ENVELOPE = {'schema': 'agent-ee.rung1-envelope/0-experimental', 'outline_mm': {'x': 100, 'y': 100, 'w': 50, 'h': 30},
            'copper_layers': 2, 'stackup': 'template-2-layer',
            'fixed_refs': [{'ref': 'J1', 'x_mm': 103.81, 'y_mm': 112.46, 'rotation_deg': 0, 'side': 'F'}]}
REQUEST = {'policy': {'schema': 'agent-ee.packing-policy/1', 'step_budget': 100000, 'gap_nm': 500000,
                      'fixed_refs': ['J1'], 'priority_refs': [], 'ground_nets': ['GND'], 'obstacles': []},
           'authorization': {'schema': 'agent-ee.placement-write/1', 'single_design_writer': True}}


def placeholder_library(root):
    """Owned copies of the stock footprints with every pad made rectangular.

    The landed placer (AGENTEE-54) accepts only rectangular pads. These are
    mechanical placeholders for exercising the placed path; they are not
    production footprint models and are never written to the repository.
    """
    for lib, name in LIBRARIES.items():
        text = (STOCK / f'{lib}.pretty' / f'{name}.kicad_mod').read_text()
        text, count = re.subn(r'(\(pad "[^"]*" (?:smd|thru_hole) )(?:roundrect|circle|oval)\b', r'\1rect', text)
        target = root / f'{lib}.pretty' / f'{name}.kicad_mod'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


class EnvelopeAndRequestTest(unittest.TestCase):
    """Pure input validation; no native tools."""

    def test_envelope_converts_to_nanometres_and_refuses_unknown_or_unplaceable_values(self):
        record = read_envelope(ENVELOPE)
        self.assertEqual(record['outline_nm'], {'x': 100_000_000, 'y': 100_000_000, 'w': 50_000_000, 'h': 30_000_000})
        self.assertEqual(record['fixed_refs'][0]['position_nm'], [103_810_000, 112_460_000])
        for mutate in (lambda e: e.update(extra=1), lambda e: e['outline_mm'].update(w=0),
                       lambda e: e['fixed_refs'][0].update(rotation_deg=45),
                       lambda e: e['fixed_refs'][0].update(side='X'), lambda e: e.update(schema='x'),
                       lambda e: e['fixed_refs'].append(dict(e['fixed_refs'][0]))):
            envelope = copy.deepcopy(ENVELOPE)
            mutate(envelope)
            with self.assertRaises(BoardBuildError):
                read_envelope(envelope)

    def test_request_needs_explicit_writer_authorization_and_envelope_fixed_refs(self):
        envelope = read_envelope(ENVELOPE)
        self.assertEqual(read_request(copy.deepcopy(REQUEST), envelope)['policy']['fixed_refs'], ['J1'])
        for mutate in (lambda r: r['authorization'].update(single_design_writer=False),
                       lambda r: r['authorization'].update(writable_root='/tmp'),
                       lambda r: r['policy'].update(fixed_refs=[]),
                       lambda r: r['policy'].update(schema='other')):
            request = copy.deepcopy(REQUEST)
            mutate(request)
            with self.assertRaises(BoardBuildError):
                read_request(request, envelope)

    def test_netlist_terminal_on_two_nets_is_refused(self):
        xml = (b'<export><components><comp ref="R1"><footprint>L:F</footprint><tstamps>u</tstamps>'
               b'<sheetpath names="/" tstamps="/"/></comp></components><nets>'
               b'<net name="A"><node ref="R1" pin="1"/></net><net name="B"><node ref="R1" pin="1"/></net>'
               b'</nets></export>')
        with self.assertRaisesRegex(BoardBuildError, 'is on nets A and B'):
            read_netlist(xml)


class RowPlanTest(unittest.TestCase):
    def test_row_follows_fixed_courtyards_with_gap_on_centre_line(self):
        def fp(ref, x0, x1, at):
            box = [[x0, -1000], [x1, -1000], [x1, 1000], [x0, 1000]]
            return {'ref': ref, 'side': 'F', 'rotation_deg': 0.0, 'position_nm': at,
                    'courtyards': {'F': {'status': 'proven', 'polygons': [box]}}}
        envelope = read_envelope({**ENVELOPE, 'fixed_refs': [dict(ENVELOPE['fixed_refs'][0], ref='J1')]})
        facts = {'footprints': [fp('J1', 101_000_000, 104_000_000, [0, 0]),
                                fp('R1', -2_000, 2_000, [0, 0]), fp('C1', -3_000, 1_000, [0, 0])]}
        placements, reason = board_build.row_plan(facts, envelope, 500)
        self.assertIsNone(reason)
        self.assertEqual([(p['ref'], p['position_nm']) for p in placements],
                         [('C1', [104_000_500 + 3_000, 115_000_000]),
                          ('R1', [104_000_500 + 4_000 + 500 + 2_000, 115_000_000])])
        facts['footprints'][1]['courtyards'] = {}
        self.assertIsNone(board_build.row_plan(facts, envelope, 500)[0])


class NativeBoardBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-board-build-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Work on a copy: kicad-cli writes .kicad_prl beside the schematic.
        self.project = self.root / 'project'
        self.project.mkdir()
        for name in ('rc_filter.kicad_pro', 'rc_filter.kicad_sch', 'sym-lib-table', 'fp-lib-table'):
            shutil.copyfile(FIXTURE / name, self.project / name)
        self.template = self.root / 'template'
        self.template.mkdir()
        for name in ('rc_filter.kicad_pcb', 'rc_filter.kicad_pro'):
            shutil.copyfile(FIXTURE / name, self.template / name)
        self.netlist = self.root / 'netlist.xml'
        exported = subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                                   '-o', str(self.netlist), str(self.project / 'rc_filter.kicad_sch')],
                                  capture_output=True, text=True, timeout=120)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        for path in (self.project / 'rc_filter.kicad_prl', ):
            path.unlink(missing_ok=True)
        self.envelope = self.root / 'envelope.json'
        self.envelope.write_text(json.dumps(ENVELOPE))
        self.request = self.root / 'request.json'
        self.request.write_text(json.dumps(REQUEST))
        self.placeholders = placeholder_library(self.root / 'placeholder-footprints')

    def build(self, out, footprints=STOCK, netlist=None):
        return board_build.build(self.project, netlist or self.netlist, footprints, self.envelope,
                                 self.template / 'rc_filter.kicad_pcb', self.request, self.root / out)

    def xml_terminals(self):
        root = ET.parse(self.netlist).getroot()
        return {(n.get('ref'), n.get('pin')): net.get('name')
                for net in root.findall('nets/net') for n in net.findall('node')}

    def test_stock_pads_place_with_exact_xml_nets_and_preserved_pad_geometry(self):
        from agent_ee.placement_probe import extract
        self.assertEqual(self.xml_terminals(), EXPECTED)
        receipt = self.build('stock')
        out = self.root / 'stock'
        facts = extract(out / 'unplaced/rc_filter.kicad_pcb')
        self.assertEqual(sorted(f['ref'] for f in facts['footprints']), ['C1', 'J1', 'R1'])
        pads = {(f['ref'], p['number']): p['net'] for f in facts['footprints'] for p in f['pads']}
        self.assertEqual(pads, EXPECTED)
        self.assertEqual(facts['outline']['status'], 'proven')
        j1 = next(f for f in facts['footprints'] if f['ref'] == 'J1')
        self.assertEqual((j1['position_nm'], j1['locked']), ([103_810_000, 112_460_000], True))
        self.assertEqual(receipt['unplaced']['parity']['schematic_parity'], 0)
        self.assertEqual(receipt['unplaced']['parity']['unconnected_items'], 4)
        for lib, name in LIBRARIES.items():
            entry = receipt['inputs']['footprints'][f'{lib}:{name}']
            self.assertEqual(entry['sha256'], board_build.digest(STOCK / f'{lib}.pretty' / f'{name}.kicad_mod'))
        self.assertEqual((receipt['status'], receipt['placement']['status']), ('placed', 'placed'))
        placed = extract(out / 'placed/rc_filter.kicad_pcb')
        def pad_geometry(board):
            return {(f['ref'], p['number']): (p['shape'], p['size_nm'])
                    for f in board['footprints'] for p in f['pads']}
        self.assertEqual(pad_geometry(placed), pad_geometry(facts))
        self.assertEqual({(f['ref'], p['number']): p['net']
                          for f in placed['footprints'] for p in f['pads']}, EXPECTED)
        self.assertEqual(receipt['placed']['parity']['schematic_parity'], 0)
        self.assertNotIn('placer_reason', receipt)
        self.assertEqual(json.loads((out / 'build-receipt.json').read_text()), receipt)
        self.assertEqual((receipt['schema'], receipt['qualification']),
                         ('agent-ee.rung1-board-build-receipt/0-experimental', 'experimental-unqualified'))

    def test_placed_board_passes_judge_and_native_schematic_parity(self):
        from agent_ee.placement import judge
        from agent_ee.placement_probe import extract
        receipt = self.build('placed', self.placeholders)
        out = self.root / 'placed'
        self.assertEqual(receipt['status'], 'placed', receipt.get('placement'))
        board = out / 'placed/rc_filter.kicad_pcb'
        judgment = judge(extract(board))
        self.assertEqual(judgment['verdict'], 'pass', judgment)
        self.assertEqual(judgment['outcome'], 'pass')
        parity = json.loads((out / receipt['placed']['parity']['report']).read_text())
        self.assertEqual(parity['schematic_parity'], [])
        self.assertEqual(len(parity['unconnected_items']), receipt['placed']['parity']['unconnected_items'])
        self.assertGreater(len(parity['unconnected_items']), 0)  # Unrouted, as expected.
        result = json.loads((out / 'placed/placement-result.json').read_text())
        self.assertEqual(result['board_sha256'], board_build.digest(board))
        pads = {(f['ref'], p['number']): p['net'] for f in extract(board)['footprints'] for p in f['pads']}
        self.assertEqual(pads, EXPECTED)

    def test_identical_inputs_give_byte_identical_boards_in_process_and_in_sandbox(self):
        from agent_ee.executor import execute
        first = self.build('one', self.placeholders)
        second = self.build('two', self.placeholders)
        self.assertEqual((first['status'], second['status']), ('placed', 'placed'))
        for name in ('unplaced/rc_filter.kicad_pcb', 'placed/rc_filter.kicad_pcb', 'placed/rc_filter.kicad_pro'):
            self.assertEqual((self.root / 'one' / name).read_bytes(), (self.root / 'two' / name).read_bytes(), name)
        inputs = self.root / 'inputs'
        for source, target in ((self.project, 'project'), (self.template, 'template'),
                               (self.placeholders, 'footprints')):
            shutil.copytree(source, inputs / target)
        for path in (self.netlist, self.envelope, self.request):
            shutil.copyfile(path, inputs / path.name)
        work = self.root / 'sandbox-work'
        execution = execute(inputs, work, [
            '/usr/bin/python3', '-B', '-m', 'agent_ee.board_build', '/candidate/project',
            '--netlist', '/candidate/netlist.xml', '--footprints', '/candidate/footprints',
            '--envelope', '/candidate/envelope.json', '--template-board', '/candidate/template/rc_filter.kicad_pcb',
            '--placement-request', '/candidate/request.json', '--out', '/work/out'], timeout=600)
        self.assertEqual(execution['exit_code'], 0, (work / 'console.log').read_text()[-4000:])
        for name in ('unplaced/rc_filter.kicad_pcb', 'placed/rc_filter.kicad_pcb'):
            self.assertEqual((work / 'out' / name).read_bytes(), (self.root / 'one' / name).read_bytes(), name)

    def test_row_fallback_option_uses_qualified_stock_placer_when_it_succeeds(self):
        from agent_ee.placement import judge
        from agent_ee.placement_probe import extract
        first = board_build.build(self.project, self.netlist, STOCK, self.envelope,
                                  self.template / 'rc_filter.kicad_pcb', self.request, self.root / 'row1', 'row')
        second = board_build.build(self.project, self.netlist, STOCK, self.envelope,
                                   self.template / 'rc_filter.kicad_pcb', self.request, self.root / 'row2', 'row')
        self.assertEqual(first['placement']['status'], 'placed')
        self.assertEqual((first['status'], first['placer']),
                         ('placed', 'agent_ee.placer.pack (AGENTEE-54)'))
        self.assertNotIn('placer_reason', first)
        board = self.root / 'row1/placed/rc_filter.kicad_pcb'
        self.assertEqual(board.read_bytes(), (self.root / 'row2/placed/rc_filter.kicad_pcb').read_bytes())
        facts = extract(board)
        self.assertEqual(judge(facts)['verdict'], 'pass')
        report = json.loads((self.root / 'row1' / first['placed']['parity']['report']).read_text())
        self.assertEqual((report['schematic_parity'], report['violations']), ([], []))
        self.assertGreater(len(report['unconnected_items']), 0)
        refs = {f['ref']: f for f in facts['footprints']}
        self.assertEqual({(f['ref'], p['number']): p['net'] for f in facts['footprints'] for p in f['pads']}, EXPECTED)
        self.assertEqual(refs['J1']['position_nm'], [103_810_000, 112_460_000])

    def test_pin_without_matching_pad_is_refused(self):
        text = self.netlist.read_text()
        # R1 pin 2 becomes pin 3 in both the symbol pin list and the net node.
        text = text.replace('<node ref="R1" pin="2"', '<node ref="R1" pin="3"')
        start = text.index('<comp ref="R1">')
        end = text.index('</comp>', start)
        text = text[:start] + text[start:end].replace('<pin num="2"/>', '<pin num="3"/>') + text[end:]
        altered = self.root / 'altered.xml'
        altered.write_text(text)
        with self.assertRaisesRegex(BoardBuildError, r"R1: pin\(s\) \['3'\] have no pad"):
            self.build('pinless', netlist=altered)
        self.assertFalse((self.root / 'pinless/unplaced/rc_filter.kicad_pcb').exists())

    def test_footprint_missing_from_fpdir_is_refused_without_fallback(self):
        shutil.rmtree(self.placeholders / 'Capacitor_SMD.pretty')
        with self.assertRaisesRegex(BoardBuildError, 'Capacitor_SMD:C_0805_2012Metric not found'):
            self.build('missing', self.placeholders)
        self.assertFalse((self.root / 'missing/unplaced/rc_filter.kicad_pcb').exists())

    def test_existing_output_is_refused(self):
        (self.root / 'used').mkdir()
        (self.root / 'used/keep').write_text('x')
        with self.assertRaisesRegex(BoardBuildError, 'must be absent or empty'):
            self.build('used', self.placeholders)


if __name__ == '__main__':
    unittest.main()

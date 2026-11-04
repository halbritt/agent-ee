"""Each footprint alone under the board's own fabrication rules (tool defect #32).

The native tests build real scratch boards and run kicad-cli DRC on original footprints;
recorded() answers the same native runs with canned reports for the capture tests.
"""
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from agent_ee import native_run
from agent_ee.design import DesignError
from agent_ee import footprint_rules
from tests.test_design_compile import design_fixture
from toolchain import require_toolchain


def owned_footprint(name, hole_gap_mm):
    """Original two-pad footprint with one 0.6 mm NPTH hole hole_gap_mm below pad 1's lower edge."""
    y = 0.5 + 0.3 + hole_gap_mm
    return (f'(footprint "{name}" (version 20241229) (generator "agent-ee-test") (layer "F.Cu")\n'
            '  (attr smd)\n'
            '  (fp_rect (start -2 -1.5) (end 2 2) (stroke (width 0.05) (type default)) (layer "F.CrtYd"))\n'
            '  (pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu" "F.Mask" "F.Paste"))\n'
            '  (pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu" "F.Mask" "F.Paste"))\n'
            f'  (pad "" np_thru_hole circle (at -1 {y:.4f}) (size 0.6 0.6) (drill 0.6) (layers "*.Cu" "*.Mask"))\n'
            ')\n').encode()


def pth_footprint(name, hole_gap_mm):
    """Original footprint: plated pad 1 (0.8 mm drill, 0.15 mm ring) and SMD pad 2 hole_gap_mm from its hole.

    The two copper lands stay 0.1 mm or more apart, so only the plated-hole rule can fire."""
    x = -1 + 0.4 + hole_gap_mm + 0.5
    return (f'(footprint "{name}" (version 20241229) (generator "agent-ee-test") (layer "F.Cu")\n'
            '  (attr through_hole)\n'
            '  (fp_rect (start -2 -1.5) (end 2 2) (stroke (width 0.05) (type default)) (layer "F.CrtYd"))\n'
            '  (pad "1" thru_hole circle (at -1 0) (size 1.1 1.1) (drill 0.8) (layers "*.Cu" "*.Mask"))\n'
            f'  (pad "2" smd rect (at {x:.4f} 0) (size 1 1) (layers "F.Cu" "F.Mask" "F.Paste"))\n'
            ')\n').encode()


# A plated pad (0.8 mm drill, 1.1 mm copper, net A) and a 0.6 mm net-B track on F.Cu at x = {x} nm, saved
# with the compiled rule files beside it. The copper stays 0.1 mm or more apart: only the hole rule can fire.
TRACK_BOARD = """import pcbnew
from pathlib import Path
out = Path('/work/board.kicad_pcb')
board = pcbnew.BOARD()
footprint = pcbnew.FOOTPRINT(board)
footprint.SetReference('J1')
board.Add(footprint)
nets = {{name: pcbnew.NETINFO_ITEM(board, name) for name in ('A', 'B')}}
for net in nets.values():
    board.Add(net)
pad = pcbnew.PAD(footprint)
pad.SetNumber('1')
pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
pad.SetSize(pcbnew.VECTOR2I(1100000, 1100000))
pad.SetDrillSize(pcbnew.VECTOR2I(800000, 800000))
pad.SetLayerSet(pcbnew.LSET.AllCuMask())
pad.SetPosition(pcbnew.VECTOR2I(10000000, 10000000))
pad.SetNet(nets['A'])
footprint.Add(pad)
track = pcbnew.PCB_TRACK(board)
track.SetStart(pcbnew.VECTOR2I({x}, 9000000))
track.SetEnd(pcbnew.VECTOR2I({x}, 11000000))
track.SetWidth(600000)
track.SetLayer(pcbnew.F_Cu)
track.SetNet(nets['B'])
board.Add(track)
assert pcbnew.SaveBoard(str(out), board)
for source in Path('/candidate').glob('*.kicad_*'):
    out.with_suffix(source.suffix).write_bytes(source.read_bytes())
"""


# The board's NPTH limit as compile now cites it in the refusal (fab profile limit npth-copper).
NPTH_LIMIT = ("against the 0.2000 mm limit of board setup constraints hole clearance "
              "(board.design_settings.rules.min_hole_clearance from fab profile limit 'npth-copper' "
              "(min_npth_hole_clearance_mm))")


def hole_finding(ref, actual='0.1944', uuids=('11111111-1111-4111-8111-111111111111',
                                               '22222222-2222-4222-8222-222222222222')):
    """A native hole_clearance finding in KiCad 10's report form, as in benchmark r5b."""
    return dict(type='hole_clearance', severity='error',
                description=('Hole clearance violation (board setup constraints hole clearance 0.2000 mm; '
                             f'actual {actual} mm)'),
                items=[dict(description=f'Pad 1 [/SIGNAL] of {ref} on F.Cu', uuid=uuids[0], pos=dict(x=1, y=1)),
                       dict(description=f'NPTH pad of {ref}', uuid=uuids[1], pos=dict(x=1, y=2))])


def recorded(findings, *, build=None):
    """Recorded scratch-board and DRC runs: findings[ref] lists that part's DRC violations.

    Every finding item belongs to the footprint, except one described as 'PCB' (the board itself)."""
    def respond(call):
        if call.intent.tool == 'pcbnew_script':
            if build is not None:
                return build
            request = json.loads((call.inputs / 'request.json').read_bytes())
            files = {}
            for part in request['parts']:
                ref = part['reference']
                uuids = sorted({i['uuid'] for v in findings.get(ref, []) for i in v.get('items', [])
                                if i.get('description') != 'PCB'})
                files[f'boards/{ref}/board.kicad_pcb'] = b'(kicad_pcb)\n'
                files[f'boards/{ref}/owners.json'] = json.dumps(dict(pcbnew_version='recorded',
                                                                     items=uuids)).encode()
            return native_run.Outcome(files=files)
        report = dict(kicad_version='recorded', violations=findings.get(call.inputs.name, []),
                      unconnected_items=[])
        return native_run.Outcome(files={'native-drc.json': json.dumps(report).encode()})
    return native_run.Recorded(respond)


class NativeFootprintRulesTest(unittest.TestCase):
    """Real KiCad: each part's footprint alone, under the rule files compile emits."""

    def setUp(self):
        require_toolchain(native=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.design = design_fixture(self.root / 'original')
        self.cad = self.root / 'original' / 'cad'

    def run_check(self, hole_gap_mm, name):
        (self.cad / 'Owned.pretty/R2.kicad_mod').write_bytes(owned_footprint('R2', hole_gap_mm))
        return footprint_rules.check(self.design, self.cad, self.root / name)

    def test_a_hole_too_close_to_its_own_pad_is_refused_with_the_rule_and_both_values(self):
        # Benchmark r5b, usb-regulator attempt 2: the receptacle's NPTH pegs were 0.1944 mm from
        # its GND pads, under KiCad's 0.25 mm default, and failed only final verification.
        with self.assertRaises(DesignError) as caught:
            self.run_check(0.15, 'near')
        lines = caught.exception.problems
        self.assertEqual([line.split(':', 1)[0] for line in lines], ['parts.R1.footprint', 'parts.R2.footprint'])
        for ref, line in zip(('R1', 'R2'), lines):
            self.assertIn('Owned:R2 breaks this board\'s fabrication rules on its own', line)
            self.assertIn('1 native DRC error inside the footprint', line)
            self.assertIn('hole_clearance: ', line)
            self.assertIn(f'Pad 1 [/SIGNAL] of {ref} on F.Cu', line)
            self.assertIn(f'NPTH pad of {ref}', line)
            self.assertIn('measure 0.1500 mm ' + NPTH_LIMIT, line)
            self.assertIn(f'Choose another footprint for {ref}', line)
        report = json.loads((self.root / 'near/report.json').read_bytes())
        self.assertEqual(report['status'], 'refused')
        for part in report['parts']:
            self.assertEqual([e['type'] for e in part['errors']], ['hole_clearance'])
            # The scratch board's own missing-outline error is not the footprint's.
            self.assertEqual([e['type'] for e in part['other_errors']], ['invalid_outline'])
            self.assertTrue(part['kicad_version'])

    def test_an_npth_hole_that_meets_the_sourced_limit_passes(self):
        # 0.22 mm is above JLCPCB's published 0.20 mm and below KiCad's unsourced 0.25 mm default.
        report = self.run_check(0.22, 'clear')
        self.assertEqual(report['status'], 'pass')
        self.assertEqual([(p['reference'], p['status'], p['errors']) for p in report['parts']],
                         [('R1', 'pass', []), ('R2', 'pass', [])])

    def test_the_board_rule_files_decide_so_an_allowed_hole_is_not_refused(self):
        # Rule files whose project allows a 0.1 mm hole clearance, as a fabrication capability
        # recorded in the board's rules would: the same footprint then passes.
        files, derivation = footprint_rules.rule_files(self.design)
        project = json.loads(files['owned.kicad_pro'])
        project['board']['design_settings']['rules']['min_hole_clearance'] = 0.1
        (self.cad / 'Owned.pretty/R2.kicad_mod').write_bytes(owned_footprint('R2', 0.15))
        report = footprint_rules.check_under(self.design, self.cad, self.root / 'allowed',
                                             {**files, 'owned.kicad_pro': json.dumps(project).encode()},
                                             derivation)
        self.assertEqual(report['status'], 'pass')

    def test_the_plated_hole_rule_fires_below_its_sourced_limit_and_passes_at_it(self):
        (self.cad / 'Owned.pretty/R2.kicad_mod').write_bytes(pth_footprint('R2', 0.27))
        with self.assertRaises(DesignError) as caught:
            footprint_rules.check(self.design, self.cad, self.root / 'pth-near')
        line = caught.exception.problems[0]
        self.assertTrue(line.startswith('parts.R1.footprint: '), line)
        # KiCad words a custom rule's hole finding "(rule '<name>' clearance <limit> mm; actual ...)".
        self.assertIn("Pad 2 [GND] of R1 on F.Cu and PTH pad 1 [/SIGNAL] of R1 measure 0.2700 mm against the "
                      "0.2800 mm limit of rule 'fab_pth-copper' clearance (custom rule 'fab_pth-copper' from fab "
                      "profile limit 'pth-copper' (min_pth_hole_clearance_mm), in the board's .kicad_dru)", line)
        (self.cad / 'Owned.pretty/R2.kicad_mod').write_bytes(pth_footprint('R2', 0.28))
        report = footprint_rules.check(self.design, self.cad, self.root / 'pth-at')
        self.assertEqual([(p['reference'], p['status']) for p in report['parts']], [('R1', 'pass'), ('R2', 'pass')])

    def test_a_track_near_a_plated_hole_is_judged_by_the_sourced_limit(self):
        # Review round 2: the plated-hole rule compared B.Pad_Type on a track, which has none, so a real
        # track 0.27 mm from a plated hole passed. Same compiled rule files as the footprint check.
        files, _ = footprint_rules.rule_files(self.design)
        findings = {}
        for gap in ('0.27', '0.28'):
            inputs, board, work = (self.root / ('track-' + gap) / name for name in ('inputs', 'board', 'drc'))
            for directory in (inputs, board, work):
                directory.mkdir(parents=True)
            for name, raw in files.items():
                (inputs / name).write_bytes(raw)
            x = 10_000_000 + 400_000 + int(Decimal(gap) * 1_000_000) + 300_000
            (inputs / 'build.py').write_text(TRACK_BOARD.format(x=x))
            record = native_run.run(native_run.Intent('pcbnew_script', ('/usr/bin/python3', '-B', '/candidate/build.py'),
                                                      timeout=120), inputs=inputs, work=board)
            self.assertEqual(record['execution'], 'ok', record)
            record = native_run.run(native_run.Intent('drc', native_run.drc_argv(
                '/candidate/board.kicad_pcb', '/work/native-drc.json', output_flag='--output'), timeout=120),
                inputs=board, work=work)
            self.assertEqual(record['execution'], 'ok', record)
            report = json.loads((work / 'native-drc.json').read_text())
            findings[gap] = [v for v in report['violations'] if v['type'] == 'hole_clearance']
        self.assertEqual(findings['0.28'], [])
        (finding,) = findings['0.27']
        self.assertEqual(finding['severity'], 'error')
        self.assertIn("(rule 'fab_pth-copper' clearance 0.2800 mm; actual 0.2700 mm)", finding['description'])
        self.assertEqual(sorted(item['description'].split(' ', 1)[0] for item in finding['items']), ['PTH', 'Track'])

    def test_the_stock_usb_c_receptacle_of_benchmark_r5b_is_still_refused(self):
        # J1 of r5b usb-regulator attempt 2, the KiCad library footprint read in place: its four
        # 0.1944 mm NPTH-to-pad gaps are below the sourced 0.20 mm limit as well.
        name = 'USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal'
        stock = Path('/usr/share/kicad/footprints/Connector_USB.pretty') / (name + '.kicad_mod')
        self.assertTrue(stock.is_file(), stock)
        (self.cad / 'Connector_USB.pretty').mkdir()
        (self.cad / 'Connector_USB.pretty' / stock.name).write_bytes(stock.read_bytes())
        self.design['parts'] = {'J1': dict(lcsc='C3025063', value='USB4105-GF-A-060',
                                           lib_id='Connector:USB_C_Receptacle_USB2.0_16P',
                                           footprint='Connector_USB:' + name, group='divider')}
        pins = {'GND': ['A1', 'A12', 'B1', 'B12', 'SH'], 'VBUS': ['A4', 'A9', 'B4', 'B9'], 'CC1': ['A5'], 'CC2': ['B5']}
        self.design['nets'] = {net: ['J1.' + pin for pin in group] for net, group in pins.items()}
        self.design['no_connects'] = ['J1.' + pin for pin in ('A6', 'A7', 'A8', 'B6', 'B7', 'B8')]
        with self.assertRaises(DesignError) as caught:
            footprint_rules.check(self.design, self.cad, self.root / 'j1')
        (line,) = caught.exception.problems
        self.assertIn('4 native DRC errors inside the footprint', line)
        self.assertEqual(line.count('measure 0.1944 mm ' + NPTH_LIMIT), 4)


class RecordedFootprintRulesTest(unittest.TestCase):
    """Grading and wording from canned native reports; no KiCad needed."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.design = design_fixture(self.root / 'original')
        self.cad = self.root / 'original' / 'cad'

    def test_scratch_boards_carry_each_part_nets_and_the_compiled_rule_files(self):
        adapter = recorded({})
        with native_run.using(adapter):
            report = footprint_rules.check(self.design, self.cad, self.root / 'out')
        self.assertEqual(report['status'], 'pass')
        request = json.loads((self.root / 'out/inputs/request.json').read_bytes())
        self.assertEqual({p['reference']: p['nets'] for p in request['parts']},
                         {'R1': {'1': '/SIGNAL', '2': 'GND'}, 'R2': {'1': '/SIGNAL', '2': 'GND'}})
        self.assertEqual(request['rule_files'], ['owned.kicad_dru', 'owned.kicad_pro'])
        # Every scratch board is judged by the placement job's own DRC command.
        self.assertEqual({c.intent.argv for c in adapter.calls if c.intent.tool == 'drc'},
                         {tuple(native_run.drc_argv('/candidate/board.kicad_pcb', '/work/native-drc.json',
                                                    output_flag='--output'))})

    def test_only_errors_whose_items_are_all_the_footprint_own_are_refused(self):
        warning = dict(hole_finding('R1'), severity='warning', type='silk_over_copper')
        board = dict(type='invalid_outline', severity='error', items=[dict(description='PCB', uuid='board')],
                     description='Board has malformed outline (no edges found on Edge.Cuts layer)')
        itemless = dict(type='invalid_outline', severity='error', description='Board has no outline', items=[])
        adapter = recorded({'R1': [warning, board, itemless]})
        with native_run.using(adapter):
            report = footprint_rules.check(self.design, self.cad, self.root / 'out')
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(report['parts'][0]['warnings'], ['silk_over_copper'])
        self.assertEqual(report['parts'][0]['other_errors'], [board, itemless])

    def test_a_limit_from_the_fab_profile_is_named_with_its_limit(self):
        finding = dict(type='hole_to_hole', severity='error',
                       description='Drilled hole too close to other hole (board setup constraints hole to '
                                   'hole 0.4500 mm; actual 0.3000 mm)',
                       items=[dict(description='NPTH pad of R2', uuid='a'), dict(description='NPTH pad of R2',
                                                                                     uuid='b')])
        with native_run.using(recorded({'R2': [finding]})), self.assertRaises(DesignError) as caught:
            footprint_rules.check(self.design, self.cad, self.root / 'out')
        self.assertIn("(board.design_settings.rules.min_hole_to_hole from fab profile limit 'holes' "
                      '(min_hole_to_hole_mm))', caught.exception.problems[0])

    def test_findings_beyond_the_listed_ones_are_counted(self):
        many = [hole_finding('R1', actual=f'0.1{n}00') for n in range(footprint_rules.LISTED + 2)]
        with native_run.using(recorded({'R1': many})), self.assertRaises(DesignError) as caught:
            footprint_rules.check(self.design, self.cad, self.root / 'out')
        line = caught.exception.problems[0]
        self.assertIn(f'{footprint_rules.LISTED + 2} native DRC errors', line)
        self.assertIn('; and 2 more.', line)
        self.assertEqual(line.count('hole_clearance:'), footprint_rules.LISTED)

    def test_a_native_run_that_does_not_complete_is_a_refusal_not_a_pass(self):
        with native_run.using(recorded({}, build=native_run.Outcome(exit_code=1))):
            with self.assertRaises(DesignError) as caught:
                footprint_rules.check(self.design, self.cad, self.root / 'out')
        self.assertTrue(caught.exception.problems[0].startswith(
            'captures.footprint-rules: the native footprint rule check did not complete (scratch boards: failed)'))
        self.assertFalse((self.root / 'out/report.json').exists())


if __name__ == '__main__':
    unittest.main()

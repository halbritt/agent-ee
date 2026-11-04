"""Each footprint alone under the board's own fabrication rules (tool defect #32).

The native tests build real scratch boards and run kicad-cli DRC on original footprints;
recorded() answers the same native runs with canned reports for the capture tests.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


def hole_finding(ref, actual='0.1944', uuids=('11111111-1111-4111-8111-111111111111',
                                               '22222222-2222-4222-8222-222222222222')):
    """A native hole_clearance finding in KiCad 10's report form, as in benchmark r5b."""
    return dict(type='hole_clearance', severity='error',
                description=('Hole clearance violation (board setup constraints hole clearance 0.2500 mm; '
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
            self.assertIn('measure 0.1500 mm against the 0.2500 mm limit of board setup constraints hole '
                          'clearance (board.design_settings.rules.min_hole_clearance, KiCad\'s default', line)
            self.assertIn(f'Choose another footprint for {ref}', line)
        report = json.loads((self.root / 'near/report.json').read_bytes())
        self.assertEqual(report['status'], 'refused')
        for part in report['parts']:
            self.assertEqual([e['type'] for e in part['errors']], ['hole_clearance'])
            # The scratch board's own missing-outline error is not the footprint's.
            self.assertEqual([e['type'] for e in part['other_errors']], ['invalid_outline'])
            self.assertTrue(part['kicad_version'])

    def test_a_footprint_whose_hole_clears_the_rule_passes(self):
        report = self.run_check(0.30, 'clear')
        self.assertEqual(report['status'], 'pass')
        self.assertEqual([(p['reference'], p['status'], p['errors']) for p in report['parts']],
                         [('R1', 'pass', []), ('R2', 'pass', [])])

    def test_the_board_rule_files_decide_so_an_allowed_hole_is_not_refused(self):
        original = footprint_rules.rule_files

        def allowing(design):
            files, derivation = original(design)
            name = next(n for n in files if n.endswith('.kicad_pro'))
            project = json.loads(files[name])
            project['board']['design_settings']['rules']['min_hole_clearance'] = 0.1
            return {**files, name: json.dumps(project).encode()}, derivation

        with patch.object(footprint_rules, 'rule_files', allowing):
            report = self.run_check(0.15, 'allowed')
        self.assertEqual(report['status'], 'pass')


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
        self.assertEqual(request['rule_files'], ['owned.kicad_pro'])
        self.assertEqual([(c.intent.tool, c.inputs.name) for c in adapter.calls],
                         [('pcbnew_script', 'inputs'), ('drc', 'R1'), ('drc', 'R2')])
        self.assertEqual(adapter.calls[1].intent.argv, tuple(native_run.drc_argv(
            '/candidate/board.kicad_pcb', '/work/native-drc.json', output_flag='--output')))

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

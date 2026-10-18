"""Native fab conservation and explicit unsupported-format boundaries."""
import copy
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_ee import checks
from agent_ee.baseline import compile_baseline, freeze_evidence, validate_witnesses, verify_evidence
from agent_ee.candidate import capture
from agent_ee.fab_export import write_zip
from agent_ee.fab_roundtrip import ParseError, histograms_match, parse_excellon, parse_gerber
from agent_ee.storage import digest, read_json, write_json
from toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/fab-export'
GERBER = (b'%TF.FileFunction,Copper,L1,Top*%\n%FSLAX46Y46*%\n%MOMM*%\n%LPD*%\nG01*\n'
          b'%TA.AperFunction,SMDPad,CuDef*%\n%ADD10R,1.0X2.0*%\n%TD*%\nD10*\n'
          b'X1000000Y-2000000D03*\nX2000000D02*\nY-3000000D01*\nM02*\n')
EXCELLON = (b'M48\n; #@! TF.FileFunction,Plated,1,2,PTH\nFMAT,2\nMETRIC\nT1C0.300\n'
            b'%\nG90\nG05\nT1\nX1.0Y-2.0\nX3.0Y-4.0\nM30\n')


class ParserTests(unittest.TestCase):
    def test_diameter_tolerance_pairs_counts_and_allows_native_tool_rounding(self):
        self.assertTrue(histograms_match({}, {}))
        self.assertTrue(histograms_match({300200: 2, 300400: 3}, {300000: 5}))
        self.assertTrue(histograms_match({300000: 2}, {301000: 2}))
        self.assertFalse(histograms_match({300000: 2}, {301001: 2}))
        self.assertFalse(histograms_match({300000: 2}, {300000: 1}))
        self.assertFalse(histograms_match({300000: 2, 310000: 1}, {300000: 1, 310000: 2}))

    def test_linear_modal_coordinates_apertures_and_metric_hits(self):
        plot = parse_gerber(GERBER, 'copper.gtl')
        self.assertEqual(plot['bounds_nm'], [1000000, -3000000, 2000000, -2000000])
        self.assertEqual(plot['apertures'][10]['function'], 'SMDPad,CuDef')
        self.assertEqual(len(plot['flashes']), 1)
        self.assertEqual(parse_excellon(EXCELLON, 'pth.drl')['histogram'], {300000: 2})

    def test_empty_native_drill_role_is_valid_and_non_ascii_is_not(self):
        empty = b'M48\nMETRIC\n%\nG90\nG05\nM30\n'
        self.assertEqual(parse_excellon(empty, 'empty.drl')['histogram'], {})
        for parser in (parse_excellon, parse_gerber):
            with self.assertRaises(ParseError) as raised:
                parser(b'\n\xff', 'bad')
            self.assertEqual((raised.exception.execution, raised.exception.line), ('malformed', 2))

    def test_gerber_inch_unknown_constructs_and_state_errors_never_parse(self):
        cases = [(GERBER.replace(b'MOMM', b'MOIN'), 'malformed'),
                 (GERBER.replace(b'G01*', b'G99*'), 'malformed'),
                 (GERBER.replace(b'G01*', b'G02*'), 'unsupported'),
                 (GERBER.replace(b'G01*', b'G36*'), 'unsupported'),
                 (GERBER.replace(b'G01*', b'%AMRoundRect*\n0 native macro*\n%'), 'unsupported'),
                 (GERBER.replace(b'LPD', b'LPC'), 'unsupported'),
                 (GERBER.replace(b'LPD', b'SR X2Y2I1J1'), 'unsupported'),
                 (GERBER.replace(b'ADD10R,1.0X2.0', b'AMMACRO*1,1,1,0,0'), 'unsupported'),
                 (GERBER.replace(b'TD*', b'TF.Unrecognized,x*'), 'unsupported'),
                 (GERBER.replace(b'TD*', b'TD.AperFunction*'), 'unsupported'),
                 (GERBER.replace(b'ADD10R,1.0X2.0', b'ADD10C,NaN'), 'malformed'),
                 (GERBER.replace(b'D10*', b'D11*'), 'malformed'),
                 (GERBER.replace(b'M02*', b''), 'malformed'),
                 (GERBER + b'G01*\n', 'malformed'),
                 (GERBER.replace(b'X1000000Y-2000000D03', b'Y-2000000D03'), 'malformed')]
        for data, execution in cases:
            with self.subTest(data=data), self.assertRaises(ParseError) as raised:
                parse_gerber(data, 'bad.gtl')
            self.assertEqual(raised.exception.execution, execution)
            self.assertGreater(raised.exception.line, 0)
            self.assertIn('bad.gtl:', str(raised.exception))

    def test_excellon_inch_slots_unknown_and_incomplete_state_refused(self):
        cases = [(EXCELLON.replace(b'METRIC', b'INCH'), 'malformed'),
                 (EXCELLON.replace(b'G05', b'G85'), 'unsupported'),
                 (EXCELLON.replace(b'X1.0Y-2.0', b'X1.0Y-2.0G85X3.0Y-4.0'), 'unsupported'),
                 (EXCELLON.replace(b'G05', b'G99'), 'malformed'),
                 (EXCELLON.replace(b'G90', b'G91'), 'unsupported'),
                 (EXCELLON.replace(b'T1\n', b'T2\n'), 'malformed'),
                 (EXCELLON.replace(b'G90\n', b''), 'malformed'),
                 (EXCELLON.replace(b'M30', b''), 'malformed'),
                 (EXCELLON + b'X1Y2\n', 'malformed'),
                 (EXCELLON.replace(b'T1C0.300', b'T1C0.300\nT1C0.500'), 'malformed')]
        for data, execution in cases:
            with self.subTest(data=data), self.assertRaises(ParseError) as raised:
                parse_excellon(data, 'bad.drl')
            self.assertEqual(raised.exception.execution, execution)
            self.assertGreater(raised.exception.line, 0)

    def test_line_numbers_are_source_lines_including_blanks_and_comments(self):
        for parser, data in ((parse_gerber, b'\nG04 comment*\nG99*\n'),
                             (parse_excellon, b'\n; comment\nG99\n')):
            with self.assertRaises(ParseError) as raised:
                parser(data, 'file')
            self.assertEqual(raised.exception.line, 3)


class NativeRoundtripTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-roundtrip-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        shutil.copytree(FIXTURE, self.source)
        self.scope = read_json(FIXTURE / 'control.scope.json')
        self.scope.update(schema='agent-ee.scope/3')
        self.scope['inputs'][0]['path'] = str(self.source)
        self.scope['checks'].insert(1, {'id': 'geometry', 'kind': 'geometry'})
        self.scope['checks'].append({'id': 'roundtrip', 'kind': 'fab_roundtrip',
                                    'params': {'export_check': 'fab'}})
        req = self.scope['baseline']['requirements'][0]
        req.update(intent='acceptance', checks=['fab', 'roundtrip'])
        self.scope['baseline']['requirements'].append({**req, 'id': 'I-native', 'intent': 'informational',
                                                       'checks': ['netlist', 'geometry']})
        self.runs = 0

    def run_case(self, *, board='fab.kicad_pcb', mutate=None):
        self.scope['board'] = 'design/' + board
        self.runs += 1
        case = self.root / str(self.runs)
        case.mkdir()
        write_json(case / 'scope.json', self.scope)
        self.run_dir = case / 'run'
        self.run_dir.mkdir()
        capture(case / 'scope.json', self.run_dir)
        producer = checks.CHECK_FUNCS['fab_export']

        def fault(ctx):
            producer(ctx)
            self.assertEqual(ctx.result['execution'], 'ok', ctx.result)
            stage = ctx.work / 'fab'
            mutate(ctx, stage)
            # Faults enter before coordinator sealing. Rehash the injected package
            # and zip it so the geometry comparison is the check under test.
            manifest = read_json(stage / 'manifest.json')
            members = [(p.name, p) for directory in ('gerbers', 'drill')
                       for p in sorted((stage / directory).iterdir())]
            write_zip(stage / 'gerbers.zip', members)
            for rel in manifest['files']:
                if (stage / rel).is_file():
                    manifest['files'][rel] = digest(stage / rel)
                else:
                    ctx.result['evidence'].remove(f'work/fab/fab/{rel}')
            write_json(stage / 'manifest.json', manifest)
        if mutate:
            with mock.patch.dict(checks.CHECK_FUNCS, {'fab_export': fault}):
                results = checks.run_checks(self.run_dir, time.monotonic() + 300)
        else:
            results = checks.run_checks(self.run_dir, time.monotonic() + 300)
        self.assertTrue(all(r['execution'] == 'ok' for r in results[:-1]), results)
        freeze_evidence(self.run_dir, results)
        verify_evidence(self.run_dir, results, required=True)
        return results[-1]

    def witness(self, result, accepted):
        args = (self.run_dir, [{'requirement_id': 'R-fab', 'check_id': 'roundtrip'}],
                [{'path': 'checks/roundtrip.json', 'locator': 'details.defects'}])
        if accepted:
            validate_witnesses(*args)
        else:
            with self.assertRaisesRegex(ValueError, 'freshly failed criterion'):
                validate_witnesses(*args)

    @staticmethod
    def edit(stage, pattern, before, after):
        path = next(stage.glob(pattern))
        text = path.read_text()
        if before not in text:
            raise AssertionError(f'{before!r} absent from {path}')
        path.write_text(text.replace(before, after, 1))

    def test_native_two_and_four_layers_conserve_exact_histograms_bounds_and_flashes(self):
        for board, count in (('fab.kicad_pcb', 2), ('fab4.kicad_pcb', 4)):
            with self.subTest(board=board):
                result = self.run_case(board=board)
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
                copper = [r for r in result['details']['plots'] if r['layer'].endswith('.Cu')]
                self.assertEqual(len(copper), count)
                if count == 4:
                    self.assertEqual({r['layer'] for r in copper}, {'F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'})
                for plot in copper:
                    self.assertEqual(plot['pad_flashes'], plot['expected_pad_flashes'])
                    self.assertEqual(plot['via_flashes'], plot['expected_via_flashes'])
                    self.assertGreater(plot['aperture_count'], 0)
                outline = next(r for r in result['details']['plots'] if r['layer'] == 'Edge.Cuts')
                self.assertEqual(outline['bounds_nm'], [100000000, -120000000, 136000000, -100000000])
                self.assertEqual(outline['bounds_nm'], outline['expected_bounds_nm'])
                drills = {r['role']: r for r in result['details']['drill']}
                self.assertEqual(drills['pth']['histogram'], [{'diameter_nm': 300000, 'count': 1},
                                                             {'diameter_nm': 1000000, 'count': 3}])
                self.assertEqual(drills['npth']['histogram'], [{'diameter_nm': 1500000, 'count': 1}])
                for drill in drills.values():
                    self.assertEqual(drill['histogram'], drill['expected_histogram'])
                self.witness(result, False)

    def test_missing_mask_drill_and_inner_layer_are_named_sealed_defects(self):
        for pattern, name, board in (('gerbers/*B_Mask*', 'B_Mask', 'fab.kicad_pcb'),
                                     ('drill/*-NPTH.drl', 'NPTH', 'fab.kicad_pcb'),
                                     ('gerbers/*In1_Cu*', 'In1.Cu', 'fab4.kicad_pcb'),
                                     ('gerbers/*In2_Cu*', 'In2.Cu', 'fab4.kicad_pcb')):
            with self.subTest(pattern=pattern):
                result = self.run_case(board=board, mutate=lambda ctx, stage: next(stage.glob(pattern)).unlink())
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
                self.assertIn(name, result['location'])
                self.witness(result, True)

    def test_dropped_hole_wrong_diameter_plating_and_flash_never_pass(self):
        cases = [('drill/*-PTH.drl', 'X120.0Y-108.0\n', '', 'ok', 'drill_histogram'),
                 ('drill/*-NPTH.drl', 'T1C1.500', 'T1C1.600', 'ok', 'drill_histogram'),
                 ('drill/*-NPTH.drl', 'NonPlated', 'Plated', 'malformed', None),
                 ('gerbers/*F_Cu*', 'X115200000Y-106000000D03*\n', '', 'ok', 'flash_count')]
        for pattern, before, after, execution, code in cases:
            with self.subTest(case=before):
                result = self.run_case(mutate=lambda ctx, stage: self.edit(stage, pattern, before, after))
                self.assertEqual(result['execution'], execution, result)
                self.assertEqual(result['outcome'], 'defect' if code else 'unknown', result)
                if code:
                    self.assertIn(code, {d['code'] for d in result['details']['defects']})
                self.witness(result, bool(code))

    def test_fractional_native_drill_diameters_survive_excellon_rounding(self):
        board = self.source / 'fab.kicad_pcb'
        board.write_text(board.read_text().replace('(drill 1)', '(drill 1.000499)'))
        result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        pth = next(d for d in result['details']['drill'] if d['role'] == 'pth')
        self.assertIn({'diameter_nm': 1000499, 'count': 3}, pth['expected_histogram'])
        self.assertIn({'diameter_nm': 1000000, 'count': 3}, pth['histogram'])

    def test_outline_tolerance_is_one_micron_in_absolute_export_frame(self):
        for delta, outcome in ((1000, 'pass'), (1001, 'defect')):
            with self.subTest(delta=delta):
                result = self.run_case(mutate=lambda ctx, stage: self.edit(
                    stage, 'gerbers/*Edge_Cuts*', 'X136000000Y-100000000D01*',
                    f'X{136000000 + delta}Y-100000000D01*'))
                self.assertEqual((result['execution'], result['outcome']), ('ok', outcome), result)
                self.witness(result, outcome == 'defect')

    def test_native_parser_faults_report_source_line_and_cannot_witness(self):
        for pattern, before, after, execution in (
                ('gerbers/*F_Cu*', '%MOMM*%', '%MOIN*%', 'malformed'),
                ('gerbers/*F_Cu*', 'G01*', 'G99*', 'malformed'),
                ('gerbers/*F_Cu*', 'G01*', 'G36*', 'unsupported'),
                ('drill/*-PTH.drl', 'METRIC', 'INCH', 'malformed'),
                ('drill/*-PTH.drl', 'G05', 'G85', 'unsupported')):
            with self.subTest(after=after):
                result = self.run_case(mutate=lambda ctx, stage: self.edit(stage, pattern, before, after))
                self.assertEqual((result['execution'], result['outcome']), (execution, 'unknown'), result)
                self.assertGreater(result['details']['parser_error']['line'], 0)
                self.assertIn(':', result['summary'])
                self.witness(result, False)

    def test_unattributed_plot_or_flash_remains_unknown_without_false_defects(self):
        for before in ('%TF.FileFunction,Copper,L1,Top*%\n',
                       '%TA.AperFunction,SMDPad,CuDef*%\n'):
            with self.subTest(before=before):
                result = self.run_case(mutate=lambda ctx, stage: self.edit(stage, 'gerbers/*F_Cu*', before, ''))
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
                self.assertEqual(result['details']['defects'], [])
                self.witness(result, False)

    def test_native_roundrect_macro_is_unsupported_at_its_opening_line(self):
        board = self.source / 'fab.kicad_pcb'
        mutation = ('import pcbnew as p, sys; b=p.LoadBoard(sys.argv[1]); '
                    '[(pad.SetShape(p.PAD_SHAPE_ROUNDRECT), pad.SetRoundRectRadiusRatio(.25)) '
                    'for f in b.GetFootprints() for pad in f.Pads() '
                    'if pad.GetAttribute()==p.PAD_ATTRIB_SMD]; p.SaveBoard(sys.argv[1],b)')
        prepared = subprocess.run(['/usr/bin/python3', '-c', mutation, str(board)],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        error = result['details']['parser_error']
        self.assertIn('%AM', error['message'])
        self.assertGreater(error['line'], 0)
        self.witness(result, False)

    def test_missing_exact_probe_facts_are_unsupported(self):
        def legacy(ctx, stage):
            path = ctx.run_dir / 'work/geometry/geometry.json'
            geometry = read_json(path)
            del geometry['pads'][0]['flash_layers']
            write_json(path, geometry)
        result = self.run_case(mutate=legacy)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.witness(result, False)

    def test_capability_requires_ordered_export_geometry_and_exact_params(self):
        scope = copy.deepcopy(self.scope)
        files = dict.fromkeys(['design/fab.kicad_sch', 'design/fab.kicad_pcb', 'design/requirements.md'], 'sha')
        descriptor = compile_baseline(scope, files)['coverage']['checks']['roundtrip']
        self.assertEqual(descriptor['predicates'], ['fab-package:roundtrip/1'])
        for change in ('geometry', 'export', 'params'):
            bad = copy.deepcopy(scope)
            if change == 'params':
                bad['checks'][-1]['params']['tolerance'] = 1
            else:
                kind = 'geometry' if change == 'geometry' else 'fab_export'
                spec = next(s for s in bad['checks'] if s['kind'] == kind)
                bad['checks'].remove(spec)
                bad['checks'].append(spec)
            with self.subTest(change=change), self.assertRaises(ValueError):
                compile_baseline(bad, files)


if __name__ == '__main__':
    unittest.main()

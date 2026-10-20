"""Native fab conservation and explicit unsupported-format boundaries."""
import copy
import os
import re
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
from fractions import Fraction

from agent_ee import fab_consistency as package
from agent_ee.executor import sandbox_argv
from agent_ee.fab_roundtrip import (ParseError, compare_copper, expected_copper, expected_counts, expression,
                                    histograms_match, parse_excellon, parse_gerber)
from agent_ee.reference_blocks import verify_external_source
from agent_ee.storage import digest, read_json, write_json
from tests.toolchain import fome_mc_store, require_toolchain

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
                 (GERBER.replace(b'G01*', b'G74*'), 'unsupported'),
                 (GERBER.replace(b'G01*', b'G36*'), 'malformed'),
                 (GERBER.replace(b'G01*', b'%AMThermal*\n7,0,0,1.0,0.6,0.2,0*%'), 'unsupported'),
                 (GERBER.replace(b'G01*', b'%AMEmpty*\n0 comment only*\n%'), 'malformed'),
                 (GERBER.replace(b'LPD', b'LPC'), 'unsupported'),
                 (GERBER.replace(b'LPD', b'SR X2Y2I1J1'), 'unsupported'),
                 (GERBER.replace(b'ADD10R,1.0X2.0', b'AMMACRO*6,0,0,1,0.1,0.1,2,0.01,1.2,0'), 'unsupported'),
                 (GERBER.replace(b'ADD10R,1.0X2.0', b'ADD10P,1.0X6'), 'unsupported'),
                 (GERBER.replace(b'ADD10R,1.0X2.0', b'ADD10Undefined,1.0'), 'malformed'),
                 (GERBER.replace(b'TD*', b'TF.Unrecognized,x*'), 'unsupported'),
                 (GERBER.replace(b'TD*', b'TD.Unrecognized*'), 'unsupported'),
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

    def test_macro_expressions_are_exact_arithmetic(self):
        variables = {1: Fraction('0.225'), 2: Fraction('-0.25')}
        self.assertEqual(expression('$1+$1', variables), Fraction('0.45'))
        self.assertEqual(expression('-$2x2-1/4', variables), Fraction('0.25'))
        self.assertEqual(expression('($1+0.025)X-(4)', variables), Fraction(-1))
        for bad in ('$3', '1/0', '(1', '1+', '1 2', 'x1', 'NaN'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                expression(bad, variables)

    def test_macro_primitives_evaluate_to_exact_extents(self):
        macro = (b'%AMShapes*\n0 comment*\n1,1,$1,$2,0*\n20,1,0.2,0,0,1,0,90*\n'
                 b'21,1,0.4,0.2,0,0,0*\n4,1,3,0,0,0.5,0,0.5,0.5,0,0,0*\n5,1,4,0,0,0.2,0*%\n'
                 b'%TA.AperFunction,SMDPad,CuDef*%\n%ADD11Shapes,0.6X2*%\n%TD*%\n')
        plot = parse_gerber(GERBER.replace(b'G01*\n', b'G01*\n' + macro).replace(
            b'X1000000Y-2000000D03*', b'D11*\nX1000000Y-2000000D03*'), 'macro.gtl')
        aperture = plot['apertures'][11]
        self.assertEqual((aperture['shape'], aperture['macro'], aperture['primitives']), ('macro', 'Shapes', 5))
        # The circle at (2, 0) spans x 1.7..2.3 and y -0.3..0.3; the vector line
        # rotated 90 degrees about the origin spans y 0..1 and x -0.1..0.1.
        self.assertEqual(aperture['bbox_nm'], [-200000, -300000, 2300000, 1000000])
        self.assertEqual(plot['flashes'][0]['bbox_nm'], [800000, -2300000, 3300000, -1000000])

    def test_macro_semantics_outside_the_kicad_set_stay_unknown(self):
        def case(body, params=b'1'):
            data = GERBER.replace(b'G01*\n', b'G01*\n%AMM*\n' + body + b'*%\n%ADD11M,' + params + b'*%\n')
            with self.assertRaises(ParseError) as raised:
                parse_gerber(data, 'm.gtl')
            return raised.exception
        self.assertEqual(case(b'1,0,1,0,0').execution, 'unsupported')        # clear exposure
        self.assertEqual(case(b'$2=$1x2*1,1,$2,0,0').execution, 'unsupported')  # variable definition
        self.assertEqual(case(b'2,1,0.1,0,0,1,0,0').execution, 'unsupported')  # deprecated vector line
        self.assertEqual(case(b'4,1,3,0,0,1,0,1,1,0,1,0').execution, 'malformed')  # outline not closed
        self.assertEqual(case(b'4,1,4,0,0,1,0,1,1,0,0,0').execution, 'malformed')  # vertex count mismatch
        self.assertEqual(case(b'1,1,$2,0,0').execution, 'malformed')        # undefined parameter
        self.assertEqual(case(b'1,1,-1,0,0').execution, 'malformed')        # negative diameter
        self.assertEqual(case(b'1,1,0,0,0').execution, 'malformed')         # empty image
        error = case(b'1,1,1,0,0*7,0,0,1.0,0.6,0.2,0')
        self.assertEqual((error.execution, error.line), ('unsupported', 7))
        self.assertIn('primitive 7', str(error))

    def test_g75_arcs_regions_and_zero_size_select(self):
        body = (b'%ADD11C,0.000000*%\nD10*\nX0Y0D02*\nG75*\nG02*\nX2000000Y0I1000000J0D01*\nG01*\n'
                b'D11*\n%TA.AperFunction,Conductor*%\nG36*\nX0Y-5000000D02*\nG01*\nX1000000Y-5000000D01*\n'
                b'G03*\nX1000000Y-3000000I0J1000000D01*\nG01*\nX0Y-5000000D01*\nX5000000Y-5000000D02*\n'
                b'X6000000Y-5000000D01*\nX5000000Y-4000000D01*\nX5000000Y-5000000D01*\nG37*\n%TD.AperFunction*%\n'
                b'D10*\n')
        plot = parse_gerber(GERBER.replace(b'D10*\n', body), 'arc.gtl')
        self.assertEqual([kind for _, kind in plot['strokes']], ['arc', 'linear'])
        self.assertEqual([(r['function'], r['draws']) for r in plot['regions']], [('Conductor', 3), ('Conductor', 3)])
        # The clockwise arc from (0,0) to (2,0) about (1,0) passes its top at (1,1).
        self.assertEqual(plot['bounds_nm'], [0, -5000000, 6000000, 1000000])
        # The counter-clockwise region arc from (1,-5) to (1,-3) about (1,-4) bulges right to x = 2.
        self.assertEqual(plot['regions'][0]['bbox_nm'], [0, -5000000, 2000000, -3000000])
        full = GERBER.replace(b'D10*\n', b'D10*\nX0Y0D02*\nG75*\nG02*\nX0Y0I1000000J0D01*\nG01*\n')
        self.assertEqual(parse_gerber(full, 'circle.gtl')['bounds_nm'][:2], [0, -3000000])
        cases = [(b'D10*\nX0Y0D02*\nG03*\nX2000000Y0I1000000J0D01*\n', 'unsupported'),     # no G75
                 (b'D10*\nX0Y0D02*\nG75*\nG03*\nX2000000Y0I1000000J9000D01*\n', 'malformed'),  # radii
                 (b'D10*\nX0Y0I1000000J0D01*\n', 'malformed'),                         # I/J linear
                 (b'G36*\nX0Y0D02*\nX1Y0D01*\nX1Y1D01*\nG37*\n', 'malformed'),          # open contour
                 (b'G36*\nX0Y0D03*\nG37*\n', 'malformed'),                              # flash in region
                 (b'G36*\nD10*\nG37*\n', 'malformed'),                                  # select in region
                 (b'G36*\nG37*\n', 'malformed'),                                         # empty region
                 (b'G37*\n', 'malformed'),
                 (b'%ADD11C,0.000000*%\nD11*\nX0Y0D03*\n', 'malformed')]                   # imageless flash
        for snippet, execution in cases:
            with self.subTest(snippet=snippet), self.assertRaises(ParseError) as raised:
                parse_gerber(GERBER.replace(b'D10*\n', snippet + b'D10*\n'), 'bad.gtl')
            self.assertEqual(raised.exception.execution, execution)
        with self.assertRaises(ParseError) as raised:
            parse_gerber(GERBER.replace(b'M02*', b'G36*\nM02*'), 'open.gtl')
        self.assertEqual(raised.exception.execution, 'malformed')


class NativeCase(unittest.TestCase):
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

    @staticmethod
    def edit_re(stage, pattern, regex, after):
        path = next(stage.glob(pattern))
        text, count = re.subn(regex, after, path.read_text(), count=1, flags=re.M | re.S)
        if count != 1:
            raise AssertionError(f'{regex!r} absent from {path}')
        path.write_text(text)


class NativeRoundtripTests(NativeCase):
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
                ('gerbers/*F_Cu*', 'G01*', 'G74*', 'unsupported'),
                ('gerbers/*F_Cu*', 'G01*', 'G36*', 'malformed'),
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

    def test_missing_exact_probe_facts_are_unsupported(self):
        def legacy(ctx, stage):
            path = ctx.run_dir / 'work/geometry/geometry.json'
            geometry = read_json(path)
            del geometry['pads'][0]['flash_layers']
            write_json(path, geometry)
        result = self.run_case(mutate=legacy)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.witness(result, False)

    def test_legacy_probe_without_fill_shape_or_extent_facts_is_unsupported(self):
        def strip(key):
            def legacy(ctx, stage):
                path = ctx.run_dir / 'work/geometry/geometry.json'
                geometry = read_json(path)
                if key == 'flash_bbox_nm':
                    del geometry['pads'][0][key]
                else:
                    del geometry[key]
                write_json(path, geometry)
            return legacy
        for key in ('zone_fill_outlines', 'copper_conductor_shapes', 'flash_bbox_nm'):
            with self.subTest(key=key):
                result = self.run_case(mutate=strip(key))
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
                self.assertIn('rerun' if key != 'flash_bbox_nm' else 'flash extents', result['summary'])
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


class PodRoundtripTests(NativeCase):
    """The 4-layer pod probe: KiCad 10 aperture macros, arc tracks and zone pours."""
    @classmethod
    def setUpClass(cls):
        # One unmutated export serves every test; mutations rerun the pipeline.
        cls.cache = tempfile.TemporaryDirectory(prefix='agent-ee-pod-')
        cls.passed = None

    @classmethod
    def tearDownClass(cls):
        cls.cache.cleanup()

    def setUp(self):
        super().setUp()
        self.scope['inputs'][0]['include'].append('pod4.kicad_pcb')

    def pod(self, mutate=None):
        if mutate is None and type(self).passed is not None:
            return type(self).passed
        result = self.run_case(board='pod4.kicad_pcb', mutate=mutate)
        if mutate is None:
            cache = Path(self.cache.name)
            shutil.copytree(self.run_dir / 'work/fab/fab/gerbers', cache / 'gerbers')
            shutil.copy(self.run_dir / 'work/geometry/geometry.json', cache / 'geometry.json')
            type(self).passed = result
        return result

    def plots(self, result):
        return {r['layer']: r for r in result['details']['plots']}

    def stage_plot(self, layer):
        name = layer.replace('.', '_')
        return parse_gerber(next((Path(self.cache.name) / 'gerbers').glob(f'*-{name}.*')).read_bytes(), name)

    def probe(self):
        return read_json(Path(self.cache.name) / 'geometry.json')

    def probe_pads(self):
        return {f"{p['ref']}.{p['pad']}": p for p in self.probe()['pads']}

    def assert_defect(self, result, code, text):
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertEqual({d['code'] for d in result['details']['defects']}, {code}, result['details']['defects'])
        self.assertIn(text, result['location'])
        self.witness(result, True)

    def test_pod_probe_package_conserves_pad_track_and_region_counts(self):
        result = self.run_case(board='pod4.kicad_pcb')  # fresh run: its sealed evidence is inspected
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertIn('track/arc draws, zone regions', result['summary'])
        plots = self.plots(result)
        counts = {layer: (plots[layer]['pad_flashes'], plots[layer]['via_flashes'], plots[layer]['track_segments'],
                          plots[layer]['track_arcs'], plots[layer]['conductor_regions'])
                  for layer in ('F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu')}
        self.assertEqual(counts, {'F.Cu': (13, 1, 3, 1, 0), 'In1.Cu': (3, 1, 0, 0, 1),
                                  'In2.Cu': (3, 1, 0, 0, 2), 'B.Cu': (5, 1, 1, 1, 1)})
        for layer in counts:
            plot = plots[layer]
            for key in ('pad_flashes', 'via_flashes', 'track_segments', 'track_arcs', 'conductor_regions'):
                self.assertEqual(plot[key], plot['expected_' + key], (layer, key))
            self.assertEqual(plot['pad_extent_mismatches'], {'probe_pads': [], 'flash_positions_nm': []})
        self.assertEqual(plots['F.Cu']['macro_pad_flashes'], 11)
        evidence = read_json(self.run_dir / 'work/roundtrip/fab-roundtrip.json')
        self.assertEqual(evidence['plots'], result['details']['plots'])
        self.witness(result, False)

    def test_roundrect_macro(self):
        result = self.pod()
        self.assertEqual(result['outcome'], 'pass', result)
        plot, pads = self.stage_plot('F.Cu'), self.probe_pads()
        roundrect = [code for code, a in plot['apertures'].items() if a.get('macro') == 'RoundRect']
        self.assertEqual(len(roundrect), 1)
        flashed = sorted(f['bbox_nm'] for f in plot['flashes'] if f['aperture'] == roundrect[0])
        # Exact macro evaluation reproduces pcbnew's per-layer pad extents, Y negated.
        expected = sorted([b[0], -b[3], b[2], -b[1]] for b in (pads[p]['flash_bbox_nm']['F.Cu'] for p in ('R1.1', 'R1.2')))
        self.assertEqual(flashed, expected)
        # Widening one macro corner by 0.1 mm moves the flashed extent; the pad defect is named.
        result = self.pod(lambda ctx, stage: self.edit(
            stage, 'gerbers/*F_Cu*', 'RoundRect,0.225000X-0.225000X-0.250000X0.225000X',
            'RoundRect,0.225000X-0.225000X-0.250000X0.325000X'))
        self.assert_defect(result, 'pad_extent', 'R1.')

    def test_freepoly_macro(self):
        result = self.pod()
        self.assertEqual(result['outcome'], 'pass', result)
        plot, pads = self.stage_plot('F.Cu'), self.probe_pads()
        freepoly = {code: a for code, a in plot['apertures'].items() if a.get('macro', '').startswith('FreePoly')}
        self.assertEqual(len(freepoly), 2)  # the custom C2 pad and the rotated chamfered C10 pad
        flashed = sorted(f['bbox_nm'] for f in plot['flashes'] if f['aperture'] in freepoly)
        expected = sorted([b[0], -b[3], b[2], -b[1]] for b in
                          (pads[p]['flash_bbox_nm']['F.Cu'] for p in ('C2.1', 'C2.2', 'C10.1', 'C10.2')))
        self.assertEqual(flashed, expected)
        # Moving one custom-pad outline vertex outward 0.2 mm is a pad extent defect on C2.
        result = self.pod(lambda ctx, stage: self.edit(
            stage, 'gerbers/*F_Cu*', '-0.450000,0.475000,0.450000,0.475000', '-0.650000,0.475000,0.450000,0.475000'))
        self.assert_defect(result, 'pad_extent', 'C2.')

    def test_arc_track(self):
        result = self.pod()
        self.assertEqual(result['outcome'], 'pass', result)
        for layer in ('F.Cu', 'B.Cu'):
            self.assertEqual([kind for _, kind in self.stage_plot(layer)['strokes']].count('arc'), 1, layer)
        arc = r'^X(\d+)Y(-\d+)I(-?\d+)J(-?\d+)D01\*\n'
        result = self.pod(lambda ctx, stage: self.edit_re(stage, 'gerbers/*F_Cu*', arc, ''))
        self.assert_defect(result, 'track_count', '0 arc')
        for regex, after, execution in ((r'J(-?\d+)D01\*', r'J\g<1>9D01*', 'malformed'),
                                        (r'^G75\*\n', '', 'unsupported')):
            with self.subTest(after=after):
                result = self.pod(lambda ctx, stage: self.edit_re(stage, 'gerbers/*F_Cu*', regex, after))
                self.assertEqual((result['execution'], result['outcome']), (execution, 'unknown'), result)
                self.assertGreater(result['details']['parser_error']['line'], 0)
                self.witness(result, False)

    def test_region_pour(self):
        result = self.pod()
        self.assertEqual(result['outcome'], 'pass', result)
        regions = self.stage_plot('In2.Cu')['regions']
        self.assertEqual([r['function'] for r in regions], ['Conductor', 'Conductor'])
        self.assertEqual(self.probe()['zone_fill_outlines'], {'F.Cu': 0, 'In1.Cu': 1, 'In2.Cu': 2, 'B.Cu': 1})
        result = self.pod(lambda ctx, stage: self.edit_re(stage, 'gerbers/*In2_Cu*', r'^G36\*\n.*?^G37\*\n', ''))
        self.assert_defect(result, 'region_count', '1 Conductor regions')
        result = self.pod(lambda ctx, stage: self.edit_re(stage, 'gerbers/*In1_Cu*', r'^G37\*\n', ''))
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)
        self.witness(result, False)

    def test_unknown_primitive_unknown(self):
        # A thermal (primitive 7) in place of a RoundRect corner circle stays unsupported at its line.
        result = self.pod(lambda ctx, stage: self.edit(
            stage, 'gerbers/*F_Cu*', '1,1,$1+$1,$2,$3*', '7,$2,$3,0.8,0.5,0.1,0*'))
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        error = result['details']['parser_error']
        self.assertIn('primitive 7', error['message'])
        self.assertGreater(error['line'], 0)
        self.assertEqual(result['details']['defects'], [])
        self.witness(result, False)


class FomeMcReparseProbeTests(unittest.TestCase):
    """fome-mc's pinned board, read only from the local Git object store, never committed."""
    SOURCE = dict(kind='pinned-external', repository='fome-mc', commit='df304f3321296f7e517e0d2e2aa52e1380d52993',
                  path='fome-mc.kicad_pcb', blob='f50b5d18bb3bc5dc984d5f46e0a631a38bc6ebe0',
                  sha256='b9d2d92518b6e72f750a74d556e3047896fa9d511c3ccd82bd8e5d2be38581ab')
    LAYERS = 'F.Cu,In1.Cu,In2.Cu,B.Cu,F.Mask,B.Mask,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,Edge.Cuts'

    def test_fome_mc_gerbers_reparse_without_unknowns_and_conserve_copper(self):
        require_toolchain(native=True)
        repository = Path(os.environ.get('AGENT_EE_FOME_MC_CHECKOUT', str(Path.home() / 'git/fome-mc')))
        fome_mc_store(repository, self.SOURCE['commit'])
        status = verify_external_source(self.SOURCE, str(repository))
        self.assertEqual(status['status'], 'verified', status)
        with tempfile.TemporaryDirectory(prefix='agent-ee-fome-probe-') as tmp:
            inputs, work = Path(tmp) / 'inputs', Path(tmp) / 'work'
            inputs.mkdir()
            for directory in ('home', 'kicad-config', 'out'):
                (work / directory).mkdir(parents=True)
            (inputs / 'fome-mc.kicad_pcb').write_bytes(subprocess.run(
                ['git', '-C', str(repository), 'cat-file', 'blob', self.SOURCE['blob']],
                capture_output=True, check=True).stdout)
            for argv in (['kicad-cli', 'pcb', 'export', 'gerbers', '-o', '/work/out/', '--layers', self.LAYERS,
                          '/candidate/fome-mc.kicad_pcb'],
                         ['/usr/bin/python3', '-m', 'agent_ee.geometry_probe', '/candidate/fome-mc.kicad_pcb',
                          '-o', '/work/geometry.json']):
                done = subprocess.run(sandbox_argv(inputs, work, argv), capture_output=True, text=True, timeout=300)
                self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            facts = read_json(work / 'geometry.json')
            pads, vias, _, bounds = expected_counts(facts)
            segments, arcs, fills, shapes, extents = expected_copper(facts)
            records, defects, gaps = {}, [], []
            for path in sorted((work / 'out').glob('*.g*')):
                if path.suffix == '.gbrjob':
                    continue
                plot = parse_gerber(path.read_bytes(), path.name)  # any ParseError fails the probe
                layer = package.plot_layer(plot['file_function'], len(facts['copper_layers']))
                record = records[layer] = {'macros': sum(a['shape'] == 'macro' for a in plot['apertures'].values()),
                                           'regions': len(plot['regions'])}
                if layer in facts['copper_layers']:
                    compare_copper(plot, path.name, layer, record, lambda code, where: defects.append(where), gaps, {
                        'pads': pads[layer], 'vias': vias[layer], 'segments': segments[layer], 'arcs': arcs[layer],
                        'regions': fills[layer], 'shapes': shapes[layer], 'extents': extents[layer]})
                if layer == 'Edge.Cuts':
                    self.assertTrue(all(abs(a - b) <= 1000 for a, b in zip(plot['bounds_nm'], bounds)))
            self.assertEqual(len(records), 11)
            self.assertEqual((defects, gaps), ([], []))
            self.assertGreater(records['F.Cu']['macro_pad_flashes'], 1000)
            self.assertGreater(sum(records[layer]['conductor_regions'] for layer in facts['copper_layers']), 0)
            self.assertGreater(records['F.Mask']['regions'], 0)


if __name__ == '__main__':
    unittest.main()

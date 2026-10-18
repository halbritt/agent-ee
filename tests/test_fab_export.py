"""JLCPCB fab package generation (AGENTEE-23): pure rules and real kicad-cli runs."""
import copy
import csv
import io
import json
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from agent_ee import executor
from agent_ee.baseline import compile_baseline, freeze_evidence, verify_evidence
from agent_ee.candidate import capture
from agent_ee.capabilities import describe_check
from agent_ee.checks import CheckError, Ctx, run_checks
from agent_ee.fab_export import (BOM_HEADER, CPL_HEADER, SCHEMA, ascii_text, assembled_parts,
                                 board_facts, bom_rows,
                                 check_fab_export, cpl_rows, inventory, normalise, render_bom,
                                 scan_sexpr, validate_params, verify_manifest, write_zip,
                                 GERBER_TIMES)
from agent_ee.storage import digest, read_json, write_json
from toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'fab-export'
BOARD = (FIXTURE / 'fab.kicad_pcb').read_bytes()


def part(lcsc='C900001', value='1µF', footprint='lib:C', maker='Maker', number='PN-1'):
    return {'lcsc': lcsc, 'value': value, 'footprint': footprint, 'manufacturer': maker,
            'partnumber': number}


class BomTests(unittest.TestCase):
    def test_grouped_by_lcsc_natural_sorted_counted_and_sanitised(self):
        rows, flagged = bom_rows({'C10': part(), 'C2': part(), 'C1': part(),
                                  'R2': part('C900047', '4.7kΩ', 'lib:R', number='PN-R'),
                                  'R10': part('C900047', '4.7kΩ', 'lib:R', number='PN-R')})
        self.assertEqual(flagged, [])
        text = render_bom(rows)
        self.assertEqual(text.splitlines()[0], BOM_HEADER)
        parsed = list(csv.reader(io.StringIO(text)))
        self.assertEqual(parsed[0], ['Designator', 'Footprint', 'Quantity', 'Value', 'Manufacturer',
                                     'Partnumber', 'LCSC Part #'])
        self.assertEqual(parsed[1], ['C1,C2,C10', 'lib:C', '3', '1uF', 'Maker', 'PN-1', 'C900001'])
        self.assertEqual(parsed[2], ['R2,R10', 'lib:R', '2', '4.7kohm', 'Maker', 'PN-R', 'C900047'])
        for row in parsed[1:]:
            self.assertEqual(int(row[2]), len(row[0].split(',')))
        self.assertTrue(text.isascii())

    def test_conflicting_members_of_one_lcsc_stay_visible_as_separate_rows(self):
        rows, _ = bom_rows({'C1': part(), 'C2': part(footprint='lib:C_other')})
        self.assertEqual([(r[0], r[1], r[6]) for r in rows],
                         [('C1', 'lib:C', 'C900001'), ('C2', 'lib:C_other', 'C900001')])

    def test_missing_or_malformed_binding_is_flagged_with_empty_lcsc_and_never_merged(self):
        rows, flagged = bom_rows({'R1': part(lcsc=''), 'R2': part(lcsc=''), 'R3': part(lcsc='C0123'),
                                  'R4': part(maker=''), 'R5': part()})
        self.assertEqual([f['reference'] for f in flagged], ['R1', 'R2', 'R3', 'R4'])
        by_ref = {r[0]: r for r in rows}
        self.assertEqual((by_ref['R1'][6], by_ref['R2'][6], by_ref['R3'][6]), ('', '', ''))
        self.assertIn('Manufacturer', flagged[3]['reason'])

    def test_ascii_sanitising_is_deterministic_and_refuses_unmappable_text(self):
        self.assertEqual(ascii_text('Fixture Wérks ±10°', 'x'), 'Fixture Werks +/-10deg')
        for bad in ('中文', 'tab\there', 'line\nbreak'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ascii_text(bad, 'x')


class CplTests(unittest.TestCase):
    POS = ('Ref,Val,Package,PosX,PosY,Rot,Side\n'
           '"R2","4.7kΩ","R",116.000000,-114.000000,90.000000,bottom\n'
           '"C10","1µF","C",1.5,-2.25,0.000000,top\n"C2","x, y","C",3.0,-4.0,180.0,top\n')

    def test_native_position_rows_rewritten_to_jlc_header_without_rotation_policy(self):
        rows = cpl_rows(self.POS)
        self.assertEqual(rows, [['C2', '3.0', '-4.0', 'Top', '180.0'],
                                ['C10', '1.5', '-2.25', 'Top', '0.000000'],
                                ['R2', '116.000000', '-114.000000', 'Bottom', '90.000000']])
        self.assertEqual(CPL_HEADER, 'Designator,Mid X,Mid Y,Layer,Rotation')

    def test_unexpected_header_nonfinite_duplicate_or_side_refused(self):
        for text in ('', 'Ref,Val,Package,PosX,PosY,Rot\n',
                     'Ref,Val,Package,PosX,PosY,Rot,Side\nR1,v,p,nan,0,0,top\n',
                     'Ref,Val,Package,PosX,PosY,Rot,Side\nR1,v,p,1e3,0,0,top\n',
                     'Ref,Val,Package,PosX,PosY,Rot,Side\nR1,v,p,0,0,0,top\nR1,v,p,0,0,0,top\n',
                     'Ref,Val,Package,PosX,PosY,Rot,Side\nR1,v,p,0,0,0,inner\n',
                     'Ref,Val,Package,PosX,PosY,Rot,Side\n"R 1",v,p,0,0,0,top\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                cpl_rows(text)


class BoardFactTests(unittest.TestCase):
    def test_owned_fixture_layers_and_populated_inventory(self):
        facts = board_facts(BOARD)
        self.assertEqual(facts['unsupported'], [])
        self.assertEqual((facts['copper_layers'], facts['copper']), (2, ['F.Cu', 'B.Cu']))
        self.assertEqual(facts['board_non_dnp'], ['C1', 'C2', 'C10', 'J1', 'R1', 'R2'])
        sides = {f['ref']: (f['side'], f['dnp']) for f in facts['footprints']}
        self.assertEqual(sides['R2'], ('Bottom', False))
        self.assertEqual(sides['C3'], ('Top', True))
        four = board_facts((FIXTURE / 'fab4.kicad_pcb').read_bytes())
        self.assertEqual(four['copper'], ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'])

    def test_unsupported_features_are_reported_not_exported(self):
        text = BOARD.decode()
        r1 = text.index('(property "Reference" "R1"')
        attr = text.index('(attr smd)', r1)
        cases = {
            'exclusion': text[:attr] + '(attr smd exclude_from_pos_files)' + text[attr + 10:],
            'variant': text.replace('(embedded_fonts no)\n\t)', '(variant (name "alt") (dnp yes))\n'
                                    '\t\t(embedded_fonts no)\n\t)', 1),
            'missing layer': text.replace('(3 "B.Mask" user)', ''),
            'duplicate': text.replace('"Reference" "C10"', '"Reference" "C1"'),
            'odd copper': text.replace('(2 "B.Cu" signal)', '(2 "B.Cu" signal)\n\t\t(4 "In1.Cu" signal)'),
        }
        for name, board in cases.items():
            with self.subTest(name=name):
                self.assertTrue(board_facts(board.encode())['unsupported'])

    def test_scanner_balances_large_skipped_subtrees_and_refuses_malformed_bytes(self):
        big = b'(kicad_pcb (layers (0 "F.Cu" signal)) ' + b'(segment (start 0 0))' * 300000 + b')'
        tree, heads = scan_sexpr(big, lambda path: len(path) == 1 or path[1] == 'layers')
        self.assertEqual(tree, ['kicad_pcb', ['layers', ['0', 'F.Cu', 'signal']]])
        self.assertIn('segment', heads)
        for bad in (b'', b'(a', b'(a))', b'(a)(b)', b'(a "open)', b'((a))', b'(a \xff)', b'atom'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                scan_sexpr(bad, lambda path: True)


class PackagingTests(unittest.TestCase):
    def test_timestamp_normalisation_is_exact_and_refuses_leftovers(self):
        header = ('%TF.CreationDate,2026-10-17T11:58:22-07:00*%\n'
                  'G04 Created by KiCad (PCBNEW 10.0.7) date 2026-10-17 11:58:22*\nM02*\n')
        later = header.replace('11:58:22', '23:01:09')
        self.assertEqual(normalise(header, GERBER_TIMES, 'x'), normalise(later, GERBER_TIMES, 'x'))
        with self.assertRaises(ValueError):
            normalise(header.replace('%TF.CreationDate', '%TF.Other'), GERBER_TIMES, 'x')
        with self.assertRaises(ValueError):
            normalise(header + 'G04 stamp 2026-10-17 11:58*\n', GERBER_TIMES, 'x')

    def test_zip_is_byte_deterministic_in_declared_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'b.gbl').write_text('b')
            (root / 'a.gtl').write_text('a')
            members = [('a.gtl', root / 'a.gtl'), ('b.gbl', root / 'b.gbl')]
            write_zip(root / 'one.zip', members)
            time.sleep(1.1)
            (root / 'a.gtl').touch()
            write_zip(root / 'two.zip', members)
            self.assertEqual((root / 'one.zip').read_bytes(), (root / 'two.zip').read_bytes())
            with zipfile.ZipFile(root / 'one.zip') as archive:
                self.assertEqual(archive.namelist(), ['a.gtl', 'b.gbl'])
                self.assertEqual({i.date_time for i in archive.infolist()}, {(1980, 1, 1, 0, 0, 0)})

    def test_manifest_inventory_mismatch_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fab = Path(tmp)
            (fab / 'gerbers').mkdir()
            (fab / 'gerbers' / 'x.gtl').write_text('x')
            (fab / 'bom.csv').write_text('b')
            manifest = {'schema': SCHEMA, 'files': inventory(fab)}
            (fab / 'manifest.json').write_text('{}')
            self.assertEqual(verify_manifest(fab, manifest), manifest['files'])
            (fab / 'bom.csv').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'changed=..bom.csv'):
                verify_manifest(fab, manifest)
            (fab / 'bom.csv').write_text('b')
            (fab / 'extra.txt').write_text('e')
            with self.assertRaisesRegex(ValueError, 'extra=..extra.txt'):
                verify_manifest(fab, manifest)
            (fab / 'extra.txt').unlink()
            (fab / 'gerbers' / 'x.gtl').unlink()
            with self.assertRaisesRegex(ValueError, 'missing=..gerbers/x.gtl'):
                verify_manifest(fab, manifest)
            (fab / 'gerbers' / 'x.gtl').write_text('')
            with self.assertRaisesRegex(ValueError, 'empty'):
                inventory(fab)


def component(dnp=False, excluded=(), **fields):
    binding = part(**fields)
    return {'dnp': dnp, 'excluded': sorted(excluded), 'value': binding['value'],
            'footprint': binding['footprint'], 'lcsc': binding['lcsc'],
            'manufacturer': binding['manufacturer'], 'Partnumber': binding['partnumber'], 'PartNumber': ''}


def footprint(ref, dnp=False):
    return {'ref': ref, 'side': 'Top', 'dnp': dnp, 'attributes': ['dnp'] if dnp else []}


class AssemblyAuthorityTests(unittest.TestCase):
    def test_board_dnp_flags_decide_the_bom_and_disagreements_are_recorded(self):
        components = {'C1': component(), 'J1': component(dnp=True), 'R1': component(), 'R2': component(dnp=True)}
        footprints = [footprint('C1'), footprint('J1'), footprint('R1', dnp=True), footprint('R2', dnp=True)]
        parts, disagreements, unsupported = assembled_parts(components, footprints)
        self.assertEqual(unsupported, [])
        self.assertEqual(sorted(parts), ['C1', 'J1'])
        self.assertEqual(parts['J1']['partnumber'], 'PN-1')
        self.assertEqual(disagreements, [{'designator': 'J1', 'schematic_dnp': True, 'board_dnp': False},
                                         {'designator': 'R1', 'schematic_dnp': False, 'board_dnp': True}])

    def test_component_without_footprint_keeps_its_schematic_state(self):
        components = {'C1': component(), 'R5': component(), 'R6': component(dnp=True, excluded=['exclude_from_board'])}
        parts, disagreements, unsupported = assembled_parts(components, [footprint('C1')])
        # R5 reaches the BOM and must then fail designator agreement with the board.
        self.assertEqual((sorted(parts), disagreements, unsupported), (['C1', 'R5'], [], []))

    def test_exclusions_are_refused_for_any_part_populated_in_either_source(self):
        for schematic_dnp, board_dnp in ((False, False), (False, True), (True, False)):
            with self.subTest(schematic_dnp=schematic_dnp, board_dnp=board_dnp):
                components = {'C1': component(dnp=schematic_dnp, excluded=['exclude_from_bom'])}
                parts, _, unsupported = assembled_parts(components, [footprint('C1', dnp=board_dnp)])
                self.assertEqual(parts, {})
                self.assertIn('v1 refuses non-DNP exclusions', unsupported[0])
        parts, _, unsupported = assembled_parts({'C1': component(dnp=True, excluded=['exclude_from_bom'])},
                                                [footprint('C1', dnp=True)])
        self.assertEqual((parts, unsupported), ({}, []))
        conflict = component()
        conflict['PartNumber'] = 'PN-2'
        _, _, unsupported = assembled_parts({'C1': conflict}, [footprint('C1')])
        self.assertIn('conflicting Partnumber', unsupported[0])


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.scope = read_json(FIXTURE / 'control.scope.json')
        self.files = {f'design/{name}': digest(FIXTURE / name) for name in
                      ('fab.kicad_sch', 'fab.kicad_pcb', 'fab4.kicad_pcb', 'requirements.md')}

    def describe(self, scope):
        return describe_check(scope['checks'][-1], check_specs=scope['checks'], files=self.files, scope=scope)

    def test_scope3_coverage_requires_preceding_netlist_and_exact_params(self):
        scope = copy.deepcopy(self.scope)
        scope['schema'] = 'agent-ee.scope/3'
        scope['baseline']['requirements'][0]['intent'] = 'acceptance'
        baseline = compile_baseline(scope, self.files)
        descriptor = baseline['coverage']['checks']['fab']
        self.assertEqual(descriptor['status'], 'supported')
        self.assertEqual(descriptor['prerequisites'], [{'check_id': 'netlist', 'kind': 'netlist'}])
        self.assertNotIn('design/fab.kicad_pcb', descriptor['immutable_inputs'])
        bad = copy.deepcopy(scope)
        bad['checks'].reverse()
        self.assertEqual(describe_check(bad['checks'][0], check_specs=bad['checks'], files=self.files,
                                        scope=bad)['status'], 'unsupported')
        for params in ({}, {'netlist_check': 'netlist', 'layers': ['F.Cu']}, {'netlist_check': 'fab'}):
            changed = copy.deepcopy(scope)
            changed['checks'][1]['params'] = params
            with self.subTest(params=params):
                self.assertEqual(self.describe(changed)['status'], 'unsupported')
        for params in ([], {'netlist_check': 3}, {'netlist_check': 'n', 'output': 'x'}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                validate_params(params)


class NativeFabExportTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(tools=('kicad-cli',))
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-fab-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        shutil.copytree(FIXTURE, self.source)
        self.scope = read_json(FIXTURE / 'control.scope.json')
        self.scope['inputs'][0]['path'] = str(self.source)
        self.runs = 0

    def run_case(self, board='fab.kicad_pcb'):
        self.scope['board'] = 'design/' + board
        self.runs += 1
        case = self.root / f'case{self.runs}'
        case.mkdir()
        write_json(case / 'scope.json', self.scope)
        run = case / 'run'
        run.mkdir()
        capture(case / 'scope.json', run)
        results = run_checks(run, time.monotonic() + 300)
        self.assertEqual(results[0]['execution'], 'ok', results[0])
        return run, results[-1]

    def assert_incomplete(self, run, result, execution):
        self.assertEqual((result['execution'], result['outcome']), (execution, 'unknown'), result)
        self.assertFalse((run / 'work/fab/fab').exists())
        self.assertFalse((run / 'work/fab/fab.incomplete/manifest.json').exists())
        self.assertFalse(any(e.startswith('work/fab/fab/') for e in result['evidence']))

    def test_two_layer_package_is_complete_bound_and_sealed(self):
        run, result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        fab = run / 'work/fab/fab'
        manifest = read_json(fab / 'manifest.json')
        candidate = read_json(run / 'candidate.json')
        self.assertEqual(manifest['schema'], 'agent-ee.fab-manifest/1')
        self.assertEqual(set(manifest), {'schema', 'candidate_id', 'board_path', 'board_sha256', 'tool',
                                         'files', 'copper_layers', 'enabled_fab_layers', 'drill',
                                         'designators', 'generated_utc'})
        self.assertEqual(manifest['candidate_id'], candidate['candidate_id'])
        self.assertEqual(manifest['board_sha256'], candidate['files']['design/fab.kicad_pcb'])
        self.assertEqual(manifest['board_sha256'], digest(FIXTURE / 'fab.kicad_pcb'))
        self.assertEqual(manifest['copper_layers'], 2)
        self.assertEqual(manifest['tool']['sha256'], digest(manifest['tool']['path']))
        self.assertEqual(verify_manifest(fab, manifest), manifest['files'])
        self.assertEqual(manifest['drill'], {'pth': 'fab-PTH.drl', 'npth': 'fab-NPTH.drl'})
        with zipfile.ZipFile(fab / 'gerbers.zip') as archive:
            names = archive.namelist()
        self.assertEqual(names, sorted(p.name for p in (fab / 'gerbers').iterdir()) +
                         sorted(p.name for p in (fab / 'drill').iterdir()))
        for suffix in ('F_Cu.gtl', 'B_Cu.gbl', 'F_Mask.gts', 'B_Mask.gbs', 'F_Paste.gtp', 'B_Paste.gbp',
                       'F_Silkscreen.gto', 'B_Silkscreen.gbo', 'Edge_Cuts.gm1', 'PTH.drl', 'NPTH.drl'):
            self.assertIn('fab-' + suffix, names)
        self.assertFalse(any('In1_Cu' in n for n in names))
        bom = (fab / 'bom.csv').read_text()
        cpl = (fab / 'cpl.csv').read_text()
        self.assertEqual(bom.splitlines()[0], BOM_HEADER)
        self.assertEqual(cpl.splitlines()[0], CPL_HEADER)
        bom_rows_ = list(csv.reader(io.StringIO(bom)))[1:]
        cpl_rows_ = list(csv.reader(io.StringIO(cpl)))[1:]
        self.assertIn(['C1,C2,C10', 'agent_fab:C_1608_owned', '3', '1uF', 'Owned Fixture Works',
                       'OFW-C-1U', 'C900001'], bom_rows_)
        self.assertIn('4.7kohm', [r[3] for r in bom_rows_])
        self.assertTrue(bom.isascii() and cpl.isascii())
        bom_refs = sorted(ref for r in bom_rows_ for ref in r[0].split(','))
        cpl_refs = sorted(r[0] for r in cpl_rows_)
        # DNP C3 is in the schematic and on the board, and in no fab output.
        self.assertEqual(bom_refs, cpl_refs)
        self.assertEqual(sorted(manifest['designators']['board_non_dnp']), cpl_refs)
        self.assertNotIn('C3', cpl_refs)
        self.assertIn('C3', read_json(run / 'work/netlist/connectivity.json')['components'])
        self.assertEqual(result['details']['dnp_disagreements'], [])
        self.assertEqual({r[0]: r[3] for r in cpl_rows_}['R2'], 'Bottom')
        summary = read_json(fab / 'pcb_summary.json')
        self.assertEqual(summary['metadata']['date'], '1970-01-01T00:00:00')
        for rel in manifest['files']:
            self.assertIn(f'work/fab/fab/{rel}', result['evidence'])
        self.assertIn('work/fab/fab/manifest.json', result['evidence'])
        self.assertIn('work/fab/steps/gerbers/execution.json', result['evidence'])
        results = [read_json(run / 'checks/netlist.json'), result]
        freeze_evidence(run, results)
        verify_evidence(run, results, required=True)
        gerber = fab / 'gerbers/fab-F_Cu.gtl'
        gerber.write_bytes(gerber.read_bytes() + b'G04 tamper*\n')
        with self.assertRaisesRegex(ValueError, 'changed=..gerbers/fab-F_Cu.gtl'):
            verify_manifest(fab, read_json(fab / 'manifest.json'))
        with self.assertRaises(ValueError):
            verify_evidence(run, results, required=True)

    def test_four_layer_package_adds_inner_copper(self):
        run, result = self.run_case('fab4.kicad_pcb')
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        manifest = read_json(run / 'work/fab/fab/manifest.json')
        self.assertEqual(manifest['copper_layers'], 4)
        self.assertEqual(manifest['enabled_fab_layers'][:4], ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'])
        with zipfile.ZipFile(run / 'work/fab/fab/gerbers.zip') as archive:
            names = archive.namelist()
        self.assertIn('fab4-In1_Cu.g1', names)
        self.assertIn('fab4-In2_Cu.g2', names)
        plots = [n for n in names if not n.endswith(('.drl', '-drl_map.gbr'))]
        self.assertEqual(len(plots), 11)

    def test_two_runs_are_byte_identical_except_generation_time(self):
        first, a = self.run_case()
        time.sleep(1.1)  # Native headers must differ before normalisation.
        second, b = self.run_case()
        self.assertEqual((a['outcome'], b['outcome']), ('pass', 'pass'))
        ma = read_json(first / 'work/fab/fab/manifest.json')
        mb = read_json(second / 'work/fab/fab/manifest.json')
        self.assertEqual(ma['files'], mb['files'])
        self.assertEqual({k: v for k, v in ma.items() if k != 'generated_utc'},
                         {k: v for k, v in mb.items() if k != 'generated_utc'})
        for rel in ma['files']:
            self.assertEqual((first / 'work/fab/fab' / rel).read_bytes(),
                             (second / 'work/fab/fab' / rel).read_bytes(), rel)
        raw = 'work/fab/steps/gerbers/out/fab-F_Cu.gtl'
        self.assertNotEqual((first / raw).read_bytes(), (second / raw).read_bytes())

    def test_bom_board_designator_mismatch_never_completes(self):
        path = self.source / 'fab.kicad_pcb'
        path.write_text(path.read_text().replace('"Reference" "R1"', '"Reference" "R99"'))
        run, result = self.run_case()
        self.assert_incomplete(run, result, 'malformed')
        sets = result['details']['designators']
        self.assertEqual(sets['cpl'], sets['board_non_dnp'])
        self.assertIn('R1', sets['bom'])
        self.assertIn('R99', sets['cpl'])
        self.assertFalse(sets['agree'])

    def test_schematic_board_dnp_disagreement_follows_the_board_and_is_recorded(self):
        schematic, board = self.source / 'fab.kicad_sch', self.source / 'fab.kicad_pcb'
        original_schematic, original_board = schematic.read_text(), board.read_text()
        for dnp_source in ('schematic', 'board'):
            with self.subTest(dnp_source=dnp_source):
                # J1 is the fixture's first schematic instance and only through-hole footprint.
                schematic.write_text(original_schematic.replace('(dnp no)', '(dnp yes)', 1)
                                     if dnp_source == 'schematic' else original_schematic)
                board.write_text(original_board.replace('(attr through_hole)', '(attr through_hole dnp)', 1)
                                 if dnp_source == 'board' else original_board)
                run, result = self.run_case()
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
                sets = result['details']['designators']
                self.assertTrue(sets['agree'])
                self.assertEqual(sets['bom'], sets['cpl'])
                self.assertEqual(sets['bom'], sets['board_non_dnp'])
                self.assertEqual('J1' in sets['bom'], dnp_source == 'schematic')
                self.assertEqual(result['details']['dnp_disagreements'], [
                    {'designator': 'J1', 'schematic_dnp': dnp_source == 'schematic',
                     'board_dnp': dnp_source == 'board'}])
                self.assertEqual('J1' in result['details']['schematic_dnp'], dnp_source == 'schematic')
                self.assertTrue(any('disagree on J1' in item for item in result['limitations']))
                manifest = read_json(run / 'work/fab/fab/manifest.json')
                self.assertEqual(manifest['designators']['board_non_dnp'], sets['board_non_dnp'])
                self.assertEqual(verify_manifest(run / 'work/fab/fab', manifest), manifest['files'])

    def test_real_export_is_consumed_by_consistency_gate_and_sealed(self):
        self.scope['checks'].insert(1, {'id': 'geometry', 'kind': 'geometry'})
        self.scope['checks'].append({'id': 'consistency', 'kind': 'fab_consistency',
                                     'params': {'export_check': 'fab'}})
        self.scope['baseline']['requirements'][0]['checks'] += ['geometry', 'consistency']
        run, result = self.run_case()
        results = [read_json(run / 'checks' / (spec['id'] + '.json'))
                   for spec in self.scope['checks']]
        for check, outcome in zip(results, ('info', 'info', 'pass', 'pass'), strict=True):
            self.assertEqual((check['execution'], check['outcome']), ('ok', outcome), check)
        self.assertEqual(result['kind'], 'fab_consistency')
        freeze_evidence(run, results)
        verify_evidence(run, results, required=True)

    def test_missing_part_binding_flags_row_and_never_completes(self):
        path = self.source / 'fab.kicad_sch'
        text = path.read_text()
        self.assertEqual(text.count('(property "LCSC" "C900010"'), 1)
        path.write_text(text.replace('(property "LCSC" "C900010"', '(property "Notes" "C900010"'))
        run, result = self.run_case()
        self.assert_incomplete(run, result, 'unsupported')
        self.assertEqual([f['reference'] for f in result['details']['flagged_parts']], ['R1'])
        rows = list(csv.reader(io.StringIO((run / 'work/fab/fab.incomplete/bom.csv').read_text())))
        self.assertIn(['R1', 'agent_fab:R_1608_owned', '1', '10k', 'Owned Fixture Works', 'OFW-R-10K', ''],
                      rows)
        self.assertIn('work/fab/fab.incomplete/bom.csv', result['evidence'])

    def damaged(self, mutate):
        real = executor.execute

        def wrapper(inputs, work, argv, **kwargs):
            record = real(inputs, work, argv, **kwargs)
            mutate(Path(work), argv)
            return record
        with mock.patch.object(executor, 'execute', wrapper):
            return self.run_case()

    def test_empty_missing_or_unparseable_native_output_is_never_pass(self):
        def truncate(name):
            def mutate(work, argv):
                for path in (work / 'out').glob('*' + name) if (work / 'out').is_dir() else ():
                    path.write_bytes(b'')
            return mutate

        def remove(name):
            def mutate(work, argv):
                for path in (work / 'out').glob('*' + name) if (work / 'out').is_dir() else ():
                    path.unlink()
            return mutate

        def corrupt(name):
            def mutate(work, argv):
                for path in (work / 'out').glob('*' + name) if (work / 'out').is_dir() else ():
                    path.write_bytes(path.read_bytes().replace(b'M02*', b''))
            return mutate
        cases = {'empty copper': truncate('F_Cu.gtl'), 'removed mask': remove('B_Mask.gbs'),
                 'removed npth': remove('NPTH.drl'), 'empty pos': truncate('pos.csv'),
                 'removed stats': remove('stats.json'), 'truncated plot': corrupt('Edge_Cuts.gm1')}
        for name, mutate in cases.items():
            with self.subTest(name=name):
                run, result = self.damaged(mutate)
                self.assert_incomplete(run, result, 'failed' if name.startswith('empty') else 'malformed')

    def test_native_nonzero_exit_is_failed_with_no_package(self):
        path = self.source / 'fab.kicad_pcb'
        path.write_text(path.read_text().replace('(version 20260206)', '(version 99990101)'))
        run, result = self.run_case()
        self.assert_incomplete(run, result, 'failed')
        self.assertNotEqual(result['details']['tools']['stats']['exit_code'], 0)
        self.assertIn('work/fab/steps/stats/console.log', result['evidence'])

    def test_unsupported_board_features_are_refused(self):
        board = self.source / 'fab4.kicad_pcb'
        text = board.read_text()
        board.write_text(text.replace('\t(segment', '\t(via blind\n\t\t(at 130 105)\n\t\t(size 0.6)\n'
                                      '\t\t(drill 0.3)\n\t\t(layers "F.Cu" "In1.Cu")\n\t\t(net "")\n'
                                      '\t\t(uuid "00000000-0000-4000-8000-000000000001")\n\t)\n\t(segment', 1))
        run, result = self.run_case('fab4.kicad_pcb')
        self.assert_incomplete(run, result, 'unsupported')
        self.assertIn('1 blind via(s)', result['details']['unsupported'])
        board.write_text(text.replace('(attr smd)', '(attr smd exclude_from_bom)', 1))
        run, result = self.run_case('fab4.kicad_pcb')
        self.assert_incomplete(run, result, 'unsupported')
        self.assertNotIn('tools', result['details'])  # refused before any native export

    def test_prerequisite_and_parameter_boundaries(self):
        run, result = self.run_case()
        manifest = read_json(run / 'candidate.json')
        spec = manifest['scope']['checks'][1]
        prior = read_json(run / 'checks/netlist.json')
        for key, bad in (('candidate_id', 'wrong'), ('kind', 'geometry'), ('execution', 'failed')):
            write_json(run / 'checks/netlist.json', {**prior, key: bad})
            ctx = Ctx(run, manifest, spec, time.monotonic() + 120)
            with self.subTest(key=key), self.assertRaises(CheckError) as raised:
                check_fab_export(ctx)
            self.assertEqual(raised.exception.execution, 'not_run')
        write_json(run / 'checks/netlist.json', prior)
        ctx = Ctx(run, manifest, {**spec, 'params': {'netlist_check': 'netlist', 'layers': 'F.Cu'}},
                  time.monotonic() + 120)
        with self.assertRaises(CheckError) as raised:
            check_fab_export(ctx)
        self.assertEqual(raised.exception.execution, 'unsupported')


if __name__ == '__main__':
    unittest.main()

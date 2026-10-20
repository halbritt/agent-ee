"""JLCPCB fab package generation (AGENTEE-23): pure rules and real kicad-cli runs."""
import copy
import csv
import io
import json
import re
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
from agent_ee.fab_export import (BOM_COLUMNS, BOM_HEADER, BOM_HEADER_SHA256, BOM_TEMPLATE_SHA256,
                                 CPL_COLUMNS, CPL_HEADER, CPL_HEADER_SHA256, CPL_TEMPLATE_SHA256,
                                 GERBER_TIMES, SCHEMA, VARIANT_BOM_FIELDS, ascii_text, assembled_parts,
                                 assembly_exclusions, board_facts, bom_rows, check_fab_export, cpl_rows,
                                 copper_feature_problems,
                                 header_records, inventory, jlc_template_header, normalise, render_bom,
                                 render_cpl, scan_sexpr, validate_params, variant_parts,
                                 verify_jlc_templates, verify_manifest, write_zip)
from agent_ee.storage import digest, read_json, write_json
try:
    from toolchain import require_toolchain
except ImportError:
    from tests.toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'fab-export'
BOARD = (FIXTURE / 'fab.kicad_pcb').read_bytes()
RULE = 'JLCPCB_Assembly=not_assembled'


def mark_schematic(text, ref, value='not_assembled', unbind=False):
    """Give one fixture symbol the rung-1 JLCPCB_Assembly field; optionally drop its binding."""
    start = text.index(f'(property "Reference" "{ref}"')
    end = text.index('(instances', start)
    symbol = text[start:end]
    if unbind:
        for name in ('LCSC', 'Manufacturer', 'Partnumber'):
            at = symbol.index(f'(property "{name}"')
            symbol = symbol[:at] + symbol[symbol.index('(hide yes)))', at) + len('(hide yes)))'):]
    field = (f'(property "JLCPCB_Assembly" "{value}" (at 0 0 0) (effects (font (size 1.27 1.27)) '
             '(hide yes)))')
    return text[:start] + field + symbol + text[end:]


def mark_board(text, ref, value='not_assembled'):
    """Give one fixture footprint the same property, as board_build copies symbol fields."""
    start = text.index(f'(property "Reference" "{ref}"')
    at = text.index('\t\t(property "Value"', start)
    field = (f'\t\t(property "JLCPCB_Assembly" "{value}"\n\t\t\t(at 0 0 0)\n\t\t\t(layer "F.Fab")\n'
             '\t\t\t(hide yes)\n\t\t\t(uuid "00000000-0000-4000-8000-0000000000a1")\n'
             '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n')
    return text[:at] + field + text[at:]


def copper_fixture(source, *, custom=False):
    """Turn the owned two-pad DNP capacitor into an open solder jumper JP1."""
    schematic = source / 'fab.kicad_sch'
    lines = schematic.read_text().splitlines()
    for i, line in enumerate(lines):
        if '(property "Reference" "C3"' in line:
            line = line.replace('"C3"', '"JP1"').replace('(dnp yes)', '(dnp no)')
            line = line.replace('(in_bom yes)', '(in_bom no)').replace('"100n"', '"Solder jumper"')
            line = line.replace('agent_fab:C_1608_owned', 'agent_fab:SolderJumper_owned')
            for name in ('LCSC', 'Manufacturer', 'Partnumber'):
                line = re.sub(r'\(property "' + name + r'".*?\(hide yes\)\)\)', '', line)
            lines[i] = line
    schematic.write_text('\n'.join(lines) + '\n')
    board = source / 'fab.kicad_pcb'
    text = board.read_text().replace('"C3"', '"JP1"')
    start = text.rfind('(footprint ', 0, text.index('(property "Reference" "JP1"'))
    end = text.index('\n\t(footprint ', start + 1)
    block = text[start:end].replace('(attr smd dnp)', '(attr smd exclude_from_bom exclude_from_pos_files)')
    block = block.replace('agent_fab:C_1608_owned', 'agent_fab:SolderJumper_owned')
    if custom:
        block = block.replace('smd rect', 'smd custom').replace('(size 0.9 0.95)',
            '(size 0.9 0.95) (options (clearance outline) (anchor rect)) '
            '(primitives (gr_rect (start -0.45 -0.475) (end 0.45 0.475) (width 0) (fill yes)))')
    board.write_text(text[:start] + block + text[end:])


def part(lcsc='C900001', value='1µF', footprint='lib:C', maker='Maker', number='PN-1'):
    return {'lcsc': lcsc, 'value': value, 'footprint': footprint, 'manufacturer': maker,
            'partnumber': number}


class BomTests(unittest.TestCase):
    def test_bom_header_matches_jlc_template(self):
        template = jlc_template_header(FIXTURE / 'jlc-bom-template.csv')
        self.assertEqual(template['header'], BOM_HEADER)
        self.assertEqual(template['header'],
                         'Designator,Footprint,Quantity,Comment,Manufacturer,Partnumber,"LCSC Part #"')
        self.assertEqual(template['columns'], BOM_COLUMNS)
        self.assertIn('Comment', template['columns'])
        self.assertNotIn('Value', template['columns'])
        self.assertEqual(template['sha256'], BOM_HEADER_SHA256)
        self.assertEqual(template['template_sha256'], BOM_TEMPLATE_SHA256)
        rows, flagged = bom_rows({'C1': part()})
        self.assertEqual(flagged, [])
        rendered = render_bom(rows)
        self.assertEqual(rendered.splitlines()[0], template['header'])
        self.assertEqual(next(csv.reader([rendered.splitlines()[0]])), template['columns'])
        self.assertEqual(header_records()['bom'],
                         {k: template[k] for k in ('header', 'columns', 'sha256', 'template_sha256')})

    def test_changed_template_byte_changes_expected_header_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('jlc-bom-template.csv', 'jlc-cpl-template.csv', 'jlc-templates.json'):
                shutil.copyfile(FIXTURE / name, root / name)
            self.assertEqual(header_records(root), header_records())
            bom_path = root / 'jlc-bom-template.csv'
            original_bom = bom_path.read_bytes()
            mutated_header = original_bom.replace(b'Comment', b'Value  ', 1)
            self.assertNotEqual(mutated_header, original_bom)
            bom_path.write_bytes(mutated_header)
            changed = jlc_template_header(bom_path)
            self.assertNotEqual(changed['sha256'], BOM_HEADER_SHA256)
            self.assertNotEqual(changed['template_sha256'], BOM_TEMPLATE_SHA256)
            with self.assertRaisesRegex(ValueError, 'template sha256'):
                verify_jlc_templates(root)
            meta = read_json(root / 'jlc-templates.json')
            meta['templates']['bom']['template_sha256'] = changed['template_sha256']
            write_json(root / 'jlc-templates.json', meta)
            with self.assertRaisesRegex(ValueError, 'header sha256'):
                verify_jlc_templates(root)
            # Restoring the metadata and changing a byte in the CPL header also changes its header hash.
            shutil.copyfile(FIXTURE / 'jlc-templates.json', root / 'jlc-templates.json')
            bom_path.write_bytes(original_bom)
            cpl_path = root / 'jlc-cpl-template.csv'
            original_cpl = cpl_path.read_bytes()
            cpl_path.write_bytes(original_cpl.replace(b'Rotation', b'Angle   ', 1))
            changed_cpl = jlc_template_header(cpl_path)
            self.assertNotEqual(changed_cpl['sha256'], CPL_HEADER_SHA256)
            self.assertNotEqual(changed_cpl['template_sha256'], CPL_TEMPLATE_SHA256)
            with self.assertRaisesRegex(ValueError, 'template sha256'):
                verify_jlc_templates(root)
            # Changing a sample-row byte changes the template hash and fails template verification.
            cpl_path.write_bytes(original_cpl)
            bom_path.write_bytes(original_bom.replace(b'C900001', b'C900002', 1))
            self.assertNotEqual(jlc_template_header(bom_path)['template_sha256'], BOM_TEMPLATE_SHA256)
            with self.assertRaisesRegex(ValueError, 'template sha256'):
                verify_jlc_templates(root)

    def test_grouped_by_lcsc_natural_sorted_counted_and_sanitised(self):
        rows, flagged = bom_rows({'C10': part(), 'C2': part(), 'C1': part(),
                                  'R2': part('C900047', '4.7kΩ', 'lib:R', number='PN-R'),
                                  'R10': part('C900047', '4.7kΩ', 'lib:R', number='PN-R')})
        self.assertEqual(flagged, [])
        text = render_bom(rows)
        self.assertEqual(text.splitlines()[0], BOM_HEADER)
        parsed = list(csv.reader(io.StringIO(text)))
        self.assertEqual(parsed[0], ['Designator', 'Footprint', 'Quantity', 'Comment', 'Manufacturer',
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

    def test_cpl_header_matches_jlc_template(self):
        template = jlc_template_header(FIXTURE / 'jlc-cpl-template.csv')
        self.assertEqual(template['header'], CPL_HEADER)
        self.assertEqual(template['header'], 'Designator,Mid X,Mid Y,Layer,Rotation')
        self.assertEqual(template['columns'], CPL_COLUMNS)
        self.assertEqual(template['sha256'], CPL_HEADER_SHA256)
        self.assertEqual(template['template_sha256'], CPL_TEMPLATE_SHA256)
        rows = cpl_rows(self.POS)
        rendered = render_cpl(rows)
        self.assertEqual(rendered.splitlines()[0], template['header'])
        self.assertEqual(next(csv.reader([rendered.splitlines()[0]])), template['columns'])
        self.assertEqual(header_records()['cpl'],
                         {k: template[k] for k in ('header', 'columns', 'sha256', 'template_sha256')})

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


def test_bom_header_matches_jlc_template():
    BomTests('test_bom_header_matches_jlc_template').test_bom_header_matches_jlc_template()


def test_cpl_header_matches_jlc_template():
    CplTests('test_cpl_header_matches_jlc_template').test_cpl_header_matches_jlc_template()


def test_changed_template_byte_changes_expected_header_hash():
    BomTests('test_changed_template_byte_changes_expected_header_hash').test_changed_template_byte_changes_expected_header_hash()


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


class CopperFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'source'
        shutil.copytree(FIXTURE, self.source)
        copper_fixture(self.source)
        self.data = (self.source / 'fab.kicad_pcb').read_bytes()

    def test_only_declaration_admits_native_exclusions(self):
        self.assertTrue(board_facts(self.data)['unsupported'])
        self.assertEqual(board_facts(self.data, copper_features={'JP1'})['unsupported'], [])
        self.assertTrue(copper_feature_problems(self.data, {'JP2'}))

    def test_declaration_is_validated(self):
        for refs in (None, 'JP1', [], ['JP1', 'JP1'], ['JP1 ', 'JP2'], [1]):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                validate_params({'netlist_check': 'n', 'copper_features': refs})
        self.assertEqual(validate_params({'netlist_check': 'n', 'copper_features': ['JP1']})['copper_features'],
                         ['JP1'])

    def test_orderable_aliases_and_duplicate_native_fields_cannot_hide_binding(self):
        from agent_ee.fab_export import part_bindings
        xml = self.source / 'native.xml'
        for alias in ('MPN', 'mpn', 'PartNumber', 'Manufacturer Part Number', 'LCSC Part #', 'JLCPCB Part #'):
            with self.subTest(alias=alias):
                xml.write_text(f'<export><components><comp ref="JP1"><fields>'
                               f'<field name="{alias}">BUY-ME</field><field name="{alias}"></field>'
                               '</fields></comp></components></export>')
                components, problems = part_bindings(xml)
                self.assertEqual(problems, [])
                self.assertTrue(copper_feature_problems(self.data, {'JP1'}, components))

    def test_token_orderable_fields_are_refused_at_every_binding_source(self):
        from agent_ee.fab_export import copper_schematic_problems, part_bindings
        aliases = ('Mfr Part Number', 'MFR_PN', 'Manufacturer PN', 'Digikey_PN',
                   'Mouser Part Number', 'PN', 'mfg-number', 'MfgPartNumber',
                   'Digi-Key PN', 'MFR stock part', 'LCSC Stock Code')
        sch = (self.source / 'fab.kicad_sch').read_text()
        for alias in aliases:
            with self.subTest(alias=alias):
                addition = f'(property "{alias}" "BUY-ME" (layer "F.Fab"))'
                board = self.data.replace(b'(property "Reference" "JP1"',
                                          addition.encode() + b'(property "Reference" "JP1"')
                self.assertIn(f'copper feature JP1: has orderable footprint field {alias}',
                              copper_feature_problems(board, {'JP1'}))
                schematic = sch.replace('(property "Reference" "JP1"',
                    addition + '(property "Reference" "JP1"').encode()
                self.assertTrue(copper_schematic_problems(schematic, {'JP1'}))
                variant = sch.replace('(reference "JP1") (unit 1)',
                    '(reference "JP1") (unit 1) (variant (name "ALT") '
                    f'(field (name "{alias}") (value "BUY-ME")))').encode()
                self.assertTrue(copper_schematic_problems(variant, {'JP1'}))
                xml = self.source / 'native.xml'
                xml.write_text('<export><components><comp ref="JP1"><fields>'
                               f'<field name="{alias}">BUY-ME</field>'
                               '</fields></comp></components></export>')
                components, problems = part_bindings(xml)
                self.assertEqual(problems, [])
                self.assertTrue(copper_feature_problems(self.data, {'JP1'}, components))
                empty = board.replace(b'"BUY-ME"', b'"  "')
                self.assertEqual(copper_feature_problems(empty, {'JP1'}), [])

    def test_unrelated_property_tokens_remain_admitted(self):
        for name in ('Manufacturer', 'Description', 'Number of pads', 'Department', 'PN notes unrelated'):
            # PN is a binding token only when it is the whole field name or paired
            # with a manufacturer/distributor token.
            with self.subTest(name=name):
                data = self.data.replace(b'(property "Reference" "JP1"',
                    f'(property "{name}" "copper jumper" (layer "F.Fab"))'.encode() +
                    b'(property "Reference" "JP1"')
                self.assertEqual(copper_feature_problems(data, {'JP1'}), [])

    def test_malformed_properties_are_typed_unqualified_content(self):
        for prop in ('(property "MPN" (at 0 0))', '(property "Description" (at 0 0))',
                     '(property "MPN")'):
            with self.subTest(prop=prop):
                data = self.data.replace(b'(property "Reference" "JP1"',
                    prop.encode() + b'(property "Reference" "JP1"')
                expected = 'copper feature JP1: unqualified footprint content property'
                self.assertIn(expected, copper_feature_problems(data, {'JP1'}))
                self.assertIn(expected, board_facts(data, copper_features={'JP1'})['unsupported'])

    def test_malformed_property_identity_is_typed_unreadable_content(self):
        for old, new in ((b'(property "Reference" "JP1"', b'(property "Reference" (at 0 0)'),
                         (b'(property "Reference" "JP1"',
                          b'(property (name "MPN") "BUY-ME") (property "Reference" "JP1"')):
            with self.subTest(new=new):
                data = self.data.replace(old, new)
                self.assertTrue(copper_feature_problems(data, {'JP1'}))
                with self.assertRaisesRegex(ValueError, 'unqualified footprint content property'):
                    board_facts(data, copper_features={'JP1'})

    def test_schematic_variant_mpn_cannot_hide_behind_native_bom_exclusion(self):
        from agent_ee.fab_export import copper_schematic_problems
        text = (self.source / 'fab.kicad_sch').read_text()
        self.assertEqual(copper_schematic_problems(text.encode(), {'JP1'}), [])
        for name in ('MPN', 'Partnumber', 'LCSC'):
            with self.subTest(name=name):
                data = text.replace('(reference "JP1") (unit 1)',
                    '(reference "JP1") (unit 1) (variant (name "ALT") '
                    f'(field (name "{name}") (value "BUY-ME")))').encode()
                self.assertTrue(copper_schematic_problems(data, {'JP1'}))
                self.assertEqual(copper_schematic_problems(data, set()), [])

    def test_reviewer_physical_content_probes(self):
        attr = b'(attr smd exclude_from_bom exclude_from_pos_files)'
        for addition in (b'(model)', b'(group)', b'(dimension)', b'(jumper_pad_groups)',
                         b'(private_layers)', b'(net_tie_pad_groups)', b'(embedded_files)', b'bare',
                         b'(fp_poly (layer "F.Cu"))', b'(fp_line (layer "User.1"))',
                         b'(fp_line (layer "F.CrtYd"))', b'(fp_text user "x" (layer "F.Fab") (layer "B.Fab"))',
                         b'(property "Label" "x" (layer "F.Cu"))'):
            with self.subTest(addition=addition):
                self.assertTrue(copper_feature_problems(self.data.replace(attr, attr + addition), {'JP1'}))
        for old, new in ((b'smd rect', b'np_thru_hole rect'), (b'smd rect', b'connect rect'),
                         (b'smd rect', b'smd rect (drill 1)'),
                         (b'"F.Cu" "F.Mask" "F.Paste"', b'"In1.Cu"'),
                         (b'"F.Cu" "F.Mask" "F.Paste"', b'"F.Cu" "F.SilkS"'),
                         (b'"F.Cu" "F.Mask" "F.Paste"', b'"F.Mask"')):
            with self.subTest(new=new):
                start = self.data.index(b'(property "Reference" "JP1"')
                self.assertIn(old, self.data[start:])
                data = self.data[:start] + self.data[start:].replace(old, new, 1)
                self.assertTrue(copper_feature_problems(data, {'JP1'}))
        for layers in (b'"*.Cu" "F.Mask"', b'"B.Cu" "B.Mask"'):
            start = self.data.index(b'(property "Reference" "JP1"')
            data = self.data[:start] + self.data[start:].replace(
                b'"F.Cu" "F.Mask" "F.Paste"', layers)
            self.assertEqual(copper_feature_problems(data, {'JP1'}), [])

    def test_custom_copper_primitives_refuse_unknowns_and_layer_overrides(self):
        from agent_ee.fab_export import copper_pad
        from agent_ee.symbol_library import parse_sexpr
        pad = '(pad "1" smd custom (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask") (primitives %s))'
        primitive = '(gr_rect (start -0.5 -0.5) (end 0.5 0.5) (width 0) (fill yes))'
        self.assertTrue(copper_pad(parse_sexpr((pad % primitive).encode())))
        for extra in ('', '(model "part.step")', primitive.replace('(width 0)', '(layer "Edge.Cuts")')):
            with self.subTest(extra=extra):
                self.assertFalse(copper_pad(parse_sexpr((pad % extra).encode())))

    def test_shape_and_binding_boundaries(self):
        attr = b'(attr smd exclude_from_bom exclude_from_pos_files)'
        for addition in (b'(model "owned.step")', b'(model "")', b'(property "MPN" "BUY-ME")',
                         b'(property "LCSC" "C900001")', b'(property "PartNumber" "PN")',
                         b'(zone (net 0))', b'(fp_line (start 0 0) (end 1 1) (layer "Edge.Cuts"))'):
            with self.subTest(addition=addition):
                self.assertTrue(copper_feature_problems(self.data.replace(attr, attr + addition), {'JP1'}))
        for attrs in (b'(attr smd exclude_from_bom)', b'(attr smd exclude_from_pos_files)', b'(attr smd)'):
            with self.subTest(attrs=attrs):
                self.assertTrue(copper_feature_problems(self.data.replace(attr, attrs), {'JP1'}))
        start = self.data.index(b'(property "Reference" "JP1"')
        for old, new in ((b'smd rect', b'thru_hole rect'), (b'"F.Paste"', b'"Edge.Cuts"')):
            data = self.data[:start] + self.data[start:].replace(old, new, 1)
            self.assertTrue(copper_feature_problems(data, {'JP1'}), (old, new))
        for comp in (None, {'excluded': ['exclude_from_board']},
                     {'excluded': [], 'orderable': ['MPN']}, {'excluded': [], 'lcsc': 'C1'}):
            bindings = {} if comp is None else {'JP1': comp}
            self.assertTrue(copper_feature_problems(self.data, {'JP1'}, bindings))
        self.assertEqual(copper_feature_problems(self.data, {'JP1'}, {'JP1': {'excluded': []}}), [])


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


def component(dnp=False, excluded=(), marker=None, **fields):
    binding = part(**fields)
    return {'dnp': dnp, 'excluded': sorted(excluded), 'value': binding['value'],
            'footprint': binding['footprint'], 'lcsc': binding['lcsc'],
            'manufacturer': binding['manufacturer'], 'Partnumber': binding['partnumber'], 'PartNumber': '',
            'fields': {} if marker is None else {'JLCPCB_Assembly': marker}}


def footprint(ref, dnp=False, marker=None):
    return {'ref': ref, 'side': 'Top', 'dnp': dnp, 'attributes': ['dnp'] if dnp else [],
            'fields': {} if marker is None else {'JLCPCB_Assembly': marker}}


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


class AssemblyExclusionTests(unittest.TestCase):
    def test_declared_rule_excludes_a_marked_populated_part_and_keeps_dnp_and_assembled_parts(self):
        unbound = component(marker='not_assembled', lcsc='', maker='', number='')
        components = {'C1': component(), 'C3': component(dnp=True, marker='not_assembled'), 'J1': unbound}
        footprints = [footprint('C1'), footprint('C3', dnp=True, marker='not_assembled'),
                      footprint('J1', marker='not_assembled')]
        excluded, unsupported = assembly_exclusions(RULE, components, footprints)
        self.assertEqual((excluded, unsupported), ([{'designator': 'J1', 'rule': RULE}], []))
        parts, _, problems = assembled_parts(components, footprints, {'J1'})
        # J1 needs no binding once excluded; DNP C3 stays a DNP omission, not a rule exclusion.
        self.assertEqual((sorted(parts), problems), (['C1'], []))
        self.assertEqual(bom_rows(parts)[1], [])
        parts, _, _ = assembled_parts(components, footprints)
        self.assertEqual(sorted(parts), ['C1', 'J1'])
        self.assertEqual([f['reference'] for f in bom_rows(parts)[1]], ['J1'])

    def test_marked_part_is_never_excluded_silently(self):
        marked = [footprint('C1'), footprint('J1', marker='not_assembled')]
        cases = {
            'no rule declared': (None, {'C1': component(), 'J1': component(marker='not_assembled')}, marked,
                                 'no matching assembly_exclusion rule'),
            'board lacks the property': (RULE, {'J1': component(marker='not_assembled')}, [footprint('J1')],
                                         'assembly fields disagree'),
            'netlist lacks the field': (RULE, {'J1': component()}, [footprint('J1', marker='not_assembled')],
                                        'assembly fields disagree'),
            'values differ': (RULE, {'J1': component(marker='not_assembled')},
                              [footprint('J1', marker='assembled')], 'assembly fields disagree'),
            'unknown marker': (RULE, {'J1': component(marker='assembled')}, [footprint('J1', marker='assembled')],
                               'not a recognised assembly marker'),
            'no board footprint': (RULE, {'J1': component(marker='not_assembled')}, [],
                                   'has no board footprint'),
            'board-only footprint': (RULE, {}, [footprint('J1', marker='not_assembled')], 'has no component'),
        }
        for name, (rule, components, footprints, text) in cases.items():
            with self.subTest(name):
                excluded, unsupported = assembly_exclusions(rule, components, footprints)
                self.assertEqual(excluded, [])
                self.assertEqual(len(unsupported), 1, unsupported)
                self.assertIn(text, unsupported[0])

    def test_board_dnp_stays_authoritative_over_a_disagreeing_marker(self):
        # A board-DNP part is omitted by DNP whatever either source carries.
        components = {'J1': component(marker='not_assembled')}
        self.assertEqual(assembly_exclusions(RULE, components, [footprint('J1', dnp=True)]), ([], []))
        self.assertEqual(assembly_exclusions(None, components, [footprint('J1', dnp=True)]), ([], []))
        # A schematic-DNP part populated on the board is populated: the rule applies to it.
        components = {'J1': component(dnp=True, marker='not_assembled')}
        self.assertEqual(assembly_exclusions(RULE, components, [footprint('J1', marker='not_assembled')]),
                         ([{'designator': 'J1', 'rule': RULE}], []))

    def test_native_exclusion_attributes_stay_refused_for_rule_excluded_parts(self):
        components = {'J1': component(excluded=['exclude_from_bom'], marker='not_assembled')}
        _, _, unsupported = assembled_parts(components, [footprint('J1', marker='not_assembled')], {'J1'})
        self.assertIn('v1 refuses non-DNP exclusions', unsupported[0])

    def test_board_facts_read_the_rule_property_once(self):
        text = mark_board(BOARD.decode(), 'J1')
        fields = {f['ref']: f['fields'] for f in board_facts(text.encode())['footprints']}
        self.assertEqual(fields['J1'], {'JLCPCB_Assembly': 'not_assembled'})
        self.assertEqual(fields['R1'], {})
        with self.assertRaisesRegex(ValueError, 'repeats an assembly rule property'):
            board_facts(mark_board(text, 'J1').encode())


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.scope = read_json(FIXTURE / 'control.scope.json')
        self.files = {f'design/{name}': digest(FIXTURE / name) for name in
                      ('fab.kicad_sch', 'fab.kicad_pcb', 'fab4.kicad_pcb', 'requirements.md')}

    def describe(self, scope):
        return describe_check(scope['checks'][-1], check_specs=scope['checks'], files=self.files, scope=scope)

    def test_scope3_copper_declaration_uses_existing_fab_predicate(self):
        scope = copy.deepcopy(self.scope)
        scope['schema'] = 'agent-ee.scope/3'
        scope['baseline']['requirements'][0]['intent'] = 'acceptance'
        scope['checks'][1]['params']['copper_features'] = ['JP1']
        descriptor = compile_baseline(scope, self.files)['coverage']['checks']['fab']
        self.assertEqual(descriptor['status'], 'supported')
        self.assertEqual(descriptor['predicates'], ['fab-export:jlcpcb-package/1'])

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
        for params in ([], {'netlist_check': 3}, {'netlist_check': 'n', 'output': 'x'},
                       {'netlist_check': 'n', 'assembly_exclusion': 'JLCPCB_Assembly=x'},
                       {'netlist_check': 'n', 'assembly_exclusion': ['JLCPCB_Assembly=not_assembled']},
                       {'assembly_exclusion': RULE}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                validate_params(params)
        ruled = copy.deepcopy(scope)
        ruled['checks'][1]['params']['assembly_exclusion'] = RULE
        self.assertEqual(self.describe(ruled)['status'], 'supported')
        ruled['checks'][1]['params']['assembly_exclusion'] = 'Value=10k'
        self.assertEqual(self.describe(ruled)['status'], 'unsupported')


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
                                         'files', 'headers', 'copper_layers', 'enabled_fab_layers', 'drill',
                                         'designators', 'generated_utc'})
        self.assertEqual(manifest['headers'], header_records())
        self.assertEqual(result['details']['headers'], header_records())
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
        self.assertEqual((manifest['designators']['excluded'], manifest['designators']['exclusion_rule']),
                         ([], None))
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

    def test_declared_owned_copper_jumper_is_absent_from_native_pos_bom_and_cpl(self):
        copper_fixture(self.source)
        self.scope['checks'][1]['params']['copper_features'] = ['JP1']
        run, result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertIn('JP1', result['details']['board']['board_non_dnp'])
        for key in ('native_pos', 'bom', 'cpl', 'assembled'):
            self.assertNotIn('JP1', result['details']['designators'][key])

    def test_non_string_copper_property_is_a_typed_export_refusal(self):
        copper_fixture(self.source)
        self.scope['checks'][1]['params']['copper_features'] = ['JP1']
        board = self.source / 'fab.kicad_pcb'
        board.write_text(board.read_text().replace('(property "Reference" "JP1"',
            '(property "MPN" (at 0 0)) (property "Reference" "JP1"'))
        _, result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        problem = 'copper feature JP1: unqualified footprint content property'
        self.assertEqual(result['summary'].count(problem), 1, result)
        self.assertEqual(result['details']['unsupported'].count(problem), 1, result)

    def test_copper_refusals_are_not_duplicated_in_summary(self):
        copper_fixture(self.source)
        self.scope['checks'][1]['params']['copper_features'] = ['JP1', 'JP9']
        _, result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        problem = 'copper feature JP9 needs exactly one board footprint'
        self.assertEqual(result['summary'].count(problem), 1, result)

    def test_board_only_holes_require_both_native_exclusions(self):
        path = self.source / 'fab.kicad_pcb'
        original = path.read_text()
        for attrs in ('board_only', 'board_only exclude_from_bom', 'board_only exclude_from_pos_files',
                      'exclude_from_bom exclude_from_pos_files'):
            with self.subTest(attrs=attrs):
                hole = f'''(footprint "Owned:Mount" (layer "F.Cu") (at 110 110)
                    (property "Reference" "H1" (at 0 0) (layer "F.Fab")) (attr {attrs})
                    (pad "" np_thru_hole circle (at 0 0) (size 2 2) (drill 2) (layers "*.Cu" "*.Mask")))'''
                path.write_text(original.rstrip()[:-1] + hole + ')')
                run, result = self.run_case()
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
                self.assertIn('v1 refuses non-DNP exclusions', result['summary'])

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

    def mark(self, schematic=True, board=True, unbind=True):
        if schematic:
            path = self.source / 'fab.kicad_sch'
            path.write_text(mark_schematic(path.read_text(), 'J1', unbind=unbind))
        if board:
            path = self.source / 'fab.kicad_pcb'
            path.write_text(mark_board(path.read_text(), 'J1'))

    def test_declared_exclusion_omits_the_marked_part_lists_it_and_passes_consistency(self):
        self.mark()
        self.scope['checks'][1]['params']['assembly_exclusion'] = RULE
        self.scope['checks'].insert(1, {'id': 'geometry', 'kind': 'geometry'})
        self.scope['checks'].append({'id': 'consistency', 'kind': 'fab_consistency',
                                     'params': {'export_check': 'fab'}})
        self.scope['baseline']['requirements'][0]['checks'] += ['geometry', 'consistency']
        run, consistency = self.run_case()
        result = read_json(run / 'checks/fab.json')
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual((consistency['execution'], consistency['outcome']), ('ok', 'pass'), consistency)
        fab = run / 'work/fab/fab'
        manifest = read_json(fab / 'manifest.json')
        self.assertEqual(manifest['designators'], {
            'board_non_dnp': ['C1', 'C2', 'C10', 'J1', 'R1', 'R2'],
            'excluded': [{'designator': 'J1', 'rule': RULE}], 'exclusion_rule': RULE})
        bom_refs = [ref for row in list(csv.reader(io.StringIO((fab / 'bom.csv').read_text())))[1:]
                    for ref in row[0].split(',')]
        cpl_refs = [row[0] for row in list(csv.reader(io.StringIO((fab / 'cpl.csv').read_text())))[1:]]
        self.assertEqual(sorted(bom_refs), sorted(cpl_refs))
        self.assertEqual(sorted(cpl_refs), ['C1', 'C10', 'C2', 'R1', 'R2'])  # J1 excluded, C3 DNP
        sets = result['details']['designators']
        self.assertEqual((sets['excluded'], sets['native_pos'], sets['agree']),
                         (['J1'], ['C1', 'C2', 'C10', 'J1', 'R1', 'R2'], True))
        self.assertIn('J1', (run / 'work/fab/steps/pos/out/pos.csv').read_text())
        exclusion = result['details']['assembly_exclusion']
        candidate = read_json(run / 'candidate.json')
        self.assertEqual(exclusion['scope_sha256'], candidate['scope_sha256'])
        self.assertEqual(candidate['scope']['checks'][2]['params']['assembly_exclusion'], RULE)
        self.assertTrue(any('J1 populated but excluded' in item for item in result['limitations']))
        self.assertEqual(consistency['details']['designators']['excluded'], manifest['designators']['excluded'])
        self.assertEqual(consistency['details']['designators']['parity_violations'], [])
        results = [read_json(run / 'checks' / (spec['id'] + '.json')) for spec in self.scope['checks']]
        freeze_evidence(run, results)
        verify_evidence(run, results, required=True)

    def test_pod_consigned_header_deviation_matches_sealed_bom_cpl_exclusion(self):
        # MPN only: owned three-pin geometry is not an AMPSEAL footprint or pin map.
        schematic_path = self.source / 'fab.kicad_sch'
        schematic_path.write_text(schematic_path.read_text().replace('OFW-HDR3', '776228-1')
                                 .replace('C900030', 'NO_LCSC'))
        self.mark(unbind=False)
        self.scope['checks'][1]['params']['assembly_exclusion'] = RULE
        write_json(self.source / 'catalog.json', {'schema': 'agent-ee.catalog-snapshot/1',
            'snapshot_date': '2026-10-18', 'stale_before': '2026-10-18', 'parts': [
                {'lcsc': lcsc, 'stock': 1000, 'is_basic': True, 'last_seen': '2026-10-18'}
                for lcsc in ('C900001', 'C900010', 'C900047')]})
        (self.source / 'purchase.txt').write_text('Original synthetic hand-fit purchase list; not a supplier quote.\n')
        write_json(self.source / 'deviations.json', {'schema': 'agent-ee.part-deviations/1', 'deviations': [{
            'requirement_id': 'POD-AC-5', 'original': {'mpn': '776228-1', 'lcsc': None},
            'assembly': 'consigned', 'reason': 'Hand-fitted after delivery',
            'stock_deviation': {'reason': 'Not in synthetic assembly catalog'},
            'purchase_source': 'Owned fixture purchase list, line 1',
            'evidence_refs': [{'path': 'design/purchase.txt', 'locator': 'line 1'}]}]})
        self.scope['checks'].append({'id': 'assembly', 'kind': 'assembly_readiness', 'params': {
            'catalog': 'design/catalog.json', 'deviations': 'design/deviations.json',
            'require_passive_ratings': False}})
        self.scope['inputs'][0]['include'] += ['catalog.json', 'deviations.json', 'purchase.txt']
        self.scope['baseline']['requirements'][0]['checks'].append('assembly')
        run, result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        fab_result = read_json(run / 'checks/fab.json')
        self.assertEqual((fab_result['execution'], fab_result['outcome']), ('ok', 'pass'), fab_result)
        report = read_json(run / 'work/assembly/assembly-readiness.json')
        self.assertEqual([(d['reference'], d['mpn']) for d in report['declared_deviation']], [('J1', '776228-1')])
        self.assertEqual({ref for p in report['parts'].values() for ref in p['references']},
                         {'C1', 'C2', 'C10', 'R1', 'R2'})
        fab = run / 'work/fab/fab'
        manifest = read_json(fab / 'manifest.json')
        self.assertEqual(manifest['designators']['excluded'], [{'designator': 'J1', 'rule': RULE}])
        bom = list(csv.reader(io.StringIO((fab / 'bom.csv').read_text())))[1:]
        cpl = list(csv.reader(io.StringIO((fab / 'cpl.csv').read_text())))[1:]
        self.assertEqual({ref for row in bom for ref in row[0].split(',')}, {row[0] for row in cpl})
        self.assertNotIn('J1', {row[0] for row in cpl})
        results = [read_json(run / 'checks' / (spec['id'] + '.json')) for spec in self.scope['checks']]
        freeze_evidence(run, results)
        verify_evidence(run, results, required=True)
        report_path = run / 'work/assembly/assembly-readiness.json'
        report_path.write_bytes(report_path.read_bytes() + b'\n')
        with self.assertRaises(ValueError):
            verify_evidence(run, results, required=True)

    def test_owner_hand_fit_policy_keeps_stocked_header_lcsc_and_matches_bom_cpl_exclusion(self):
        # AGENTEE-239: exact stocked identity admitted only under the scope's pinned owner-decision citation.
        schematic_path = self.source / 'fab.kicad_sch'
        schematic_path.write_text(schematic_path.read_text().replace('OFW-HDR3', '776228-1'))
        self.mark(unbind=False)
        self.scope['checks'][1]['params']['assembly_exclusion'] = RULE
        write_json(self.source / 'catalog.json', {'schema': 'agent-ee.catalog-snapshot/1',
            'snapshot_date': '2026-10-18', 'stale_before': '2026-10-18', 'parts': [
                {'lcsc': lcsc, 'stock': 1000, 'is_basic': True, 'last_seen': '2026-10-18'}
                for lcsc in ('C900001', 'C900010', 'C900047')] + [
                {'lcsc': 'C900030', 'mpn': '776228-1', 'stock': 260, 'is_basic': False, 'last_seen': '2026-10-18'}]})
        (self.source / 'purchase.txt').write_text('Original synthetic hand-fit purchase list; not a supplier quote.\n')
        quote = 'Header 776228-1 is fitted by hand after delivery, never by the assembler.'
        (self.source / 'decision.md').write_text(f'Owned synthetic owner decision.\n{quote}\n')
        write_json(self.source / 'deviations.json', {'schema': 'agent-ee.part-deviations/1', 'deviations': [{
            'requirement_id': 'POD-AC-5', 'original': {'mpn': '776228-1', 'lcsc': 'C900030'},
            'assembly': 'consigned', 'policy': 'owner_hand_fit', 'reason': 'Hand-fitted after delivery',
            'stock_deviation': {'reason': 'Owner hand-fit policy, not a stock exception'},
            'purchase_source': 'Owned fixture purchase list, line 1',
            'evidence_refs': [{'path': 'design/purchase.txt', 'locator': 'line 1'}]}]})
        self.scope['checks'].append({'id': 'assembly', 'kind': 'assembly_readiness', 'params': {
            'catalog': 'design/catalog.json', 'deviations': 'design/deviations.json',
            'require_passive_ratings': False, 'hand_fit_policy': {
                'source': 'design/decision.md', 'sha256': digest(self.source / 'decision.md'), 'line': 2,
                'quote': quote, 'identities': [{'mpn': '776228-1', 'lcsc': 'C900030'}]}}})
        self.scope['inputs'][0]['include'] += ['catalog.json', 'deviations.json', 'purchase.txt', 'decision.md']
        self.scope['baseline']['requirements'][0]['checks'].append('assembly')
        run, result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        report = read_json(run / 'work/assembly/assembly-readiness.json')
        self.assertEqual([(d['reference'], d['lcsc'], d['stock_state']) for d in report['declared_deviation']],
                         [('J1', 'C900030', 'owner_hand_fit')])
        self.assertNotIn('C900030', report['parts'])
        self.assertEqual(report['hand_fit_policy']['sha256'], digest(run / 'inputs/design/decision.md'))
        fab = run / 'work/fab/fab'
        manifest = read_json(fab / 'manifest.json')
        self.assertEqual(manifest['designators']['excluded'], [{'designator': 'J1', 'rule': RULE}])
        bom = list(csv.reader(io.StringIO((fab / 'bom.csv').read_text())))[1:]
        cpl = list(csv.reader(io.StringIO((fab / 'cpl.csv').read_text())))[1:]
        self.assertEqual({ref for row in bom for ref in row[0].split(',')}, {row[0] for row in cpl})
        self.assertNotIn('J1', {row[0] for row in cpl})
        results = [read_json(run / 'checks' / (spec['id'] + '.json')) for spec in self.scope['checks']]
        freeze_evidence(run, results)
        verify_evidence(run, results, required=True)

    def test_marked_part_without_rule_or_with_one_source_is_refused_before_export(self):
        schematic, board = self.source / 'fab.kicad_sch', self.source / 'fab.kicad_pcb'
        original_schematic, original_board = schematic.read_text(), board.read_text()
        for name, rule, sources, text in (('no rule', None, (True, True), 'no matching assembly_exclusion'),
                                          ('schematic only', RULE, (True, False), 'assembly fields disagree'),
                                          ('board only', RULE, (False, True), 'assembly fields disagree')):
            with self.subTest(name):
                schematic.write_text(original_schematic)
                board.write_text(original_board)
                self.mark(*sources, unbind=False)
                params = {'netlist_check': 'netlist'}
                if rule:
                    params['assembly_exclusion'] = rule
                self.scope['checks'][1]['params'] = params
                run, result = self.run_case()
                self.assert_incomplete(run, result, 'unsupported')
                self.assertNotIn('tools', result['details'])
                self.assertTrue(any(text in item for item in result['details']['unsupported']),
                                result['details']['unsupported'])

    def test_declared_rule_without_marked_parts_is_recorded_and_changes_nothing(self):
        self.scope['checks'][1]['params']['assembly_exclusion'] = RULE
        run, result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        manifest = read_json(run / 'work/fab/fab/manifest.json')
        self.assertEqual((manifest['designators']['excluded'], manifest['designators']['exclusion_rule']),
                         ([], RULE))
        self.assertIn('J1', result['details']['designators']['cpl'])

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


def variant_schematic(text):
    """Owned variant clauses on R2: ALT overrides its part number, LITE makes it DNP."""
    old = '(reference "R2") (unit 1)'
    return text.replace(old, old + ' (variant (name "ALT") (field (name "Partnumber") (value "OFW-R-4K7-ALT")))'
                                   ' (variant (name "LITE") (dnp yes))', 1)


class BomVariantTests(unittest.TestCase):
    HEADER = ','.join(VARIANT_BOM_FIELDS) + '\n'

    def test_params_take_one_nonempty_variant_name(self):
        self.assertEqual(validate_params({'netlist_check': 'n', 'bom_variant': 'ALT'})['bom_variant'], 'ALT')
        for bad in ('', ' ALT', 3, ['ALT'], None):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'bom_variant'):
                validate_params({'netlist_check': 'n', 'bom_variant': bad})

    def test_variant_rows_are_read_per_reference_without_excluded_parts(self):
        parts, problems = variant_parts(self.HEADER + 'R1,10k,lib:R,C1,Maker,PN-1,\nR2,1k,lib:R,C2,Maker,,PN-2\n',
                                        {'R2'})
        self.assertEqual(parts, {'R1': part('C1', '10k', 'lib:R', 'Maker', 'PN-1')})
        self.assertEqual(problems, [])
        _, problems = variant_parts(self.HEADER + 'R1,10k,lib:R,C1,Maker,PN-1,PN-X\n')
        self.assertIn('conflicting Partnumber', problems[0])
        for text, message in ((self.HEADER.replace('Reference', 'Refs') + 'R1,,,,,,\n', 'header'),
                              (self.HEADER + 'R1,10k\n', 'columns'), ('', 'empty'),
                              (self.HEADER + 'R1,,,,,,\nR1,,,,,,\n', 'repeated'),
                              (self.HEADER + '"R1,R2",,,,,,\n', 'invalid')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                variant_parts(text)


class NativeBomVariantTests(unittest.TestCase):
    """fab_export with a declared bom_variant on the owned fixture (AGENTEE-66 + AGENTEE-126)."""

    def setUp(self):
        require_toolchain(tools=('kicad-cli',))
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-fab-variant-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        shutil.copytree(FIXTURE, self.source)
        schematic = self.source / 'fab.kicad_sch'
        schematic.write_text(variant_schematic(schematic.read_text()))
        (self.source / 'fab.kicad_pro').write_text(json.dumps(
            {'meta': {'filename': 'fab.kicad_pro', 'version': 3},
             'schematic': {'variants': [{'name': 'ALT'}, {'name': 'LITE'}]}}))
        self.scope = read_json(FIXTURE / 'control.scope.json')
        self.scope['inputs'][0]['path'] = str(self.source)
        self.scope['inputs'][0]['include'].append('fab.kicad_pro')
        self.runs = 0

    def run_case(self, variant):
        scope = copy.deepcopy(self.scope)
        if variant is not None:
            scope['checks'][1]['params']['bom_variant'] = variant
        self.runs += 1
        case = self.root / f'case{self.runs}'
        case.mkdir()
        write_json(case / 'scope.json', scope)
        run = case / 'run'
        run.mkdir()
        capture(case / 'scope.json', run)
        results = run_checks(run, time.monotonic() + 300)
        self.assertEqual(results[0]['execution'], 'ok', results[0])
        return run, results[-1]

    def test_registered_variant_reaches_only_the_bom_export_and_is_sealed(self):
        run, result = self.run_case('ALT')
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        bom = result['details']['tools']['bom']
        self.assertEqual(bom['argv'][:4], ['kicad-cli', 'sch', 'export', 'bom'])
        self.assertEqual(bom['argv'][-3:], ['--variant', 'ALT', '/candidate/design/fab.kicad_sch'])
        self.assertTrue(all('--variant' not in result['details']['tools'][step]['argv']
                            for step in ('stats', 'gerbers', 'drill', 'pos')))
        fab = run / 'work/fab/fab'
        manifest = read_json(fab / 'manifest.json')
        self.assertEqual(verify_manifest(fab, manifest), manifest['files'])
        record = manifest['bom_variant']
        self.assertEqual((record['name'], record['registered'], record['binding_changes']),
                         ('ALT', ['ALT', 'LITE'], ['R2']))
        self.assertEqual(record['project'], {'path': 'design/fab.kicad_pro',
                                             'sha256': digest(self.source / 'fab.kicad_pro')})
        self.assertEqual(record['native_bom_sha256'], digest(run / 'work/fab/steps/bom/out/bom.csv'))
        self.assertIn('work/fab/steps/bom/out/bom.csv', result['evidence'])
        rows = {r[0]: r for r in csv.reader(io.StringIO((fab / 'bom.csv').read_text()))}
        self.assertEqual(rows['R2'][5], 'OFW-R-4K7-ALT')
        # The CPL is the default variant: the same bytes as an unvaried export of this design.
        default_run, default = self.run_case(None)
        self.assertEqual(default['outcome'], 'unknown')  # variant clauses need a declared variant
        self.assertIn('declares no bom_variant', default['summary'])
        plain = copy.deepcopy(self.scope)
        plain['inputs'][0]['path'] = str(FIXTURE)
        plain['inputs'][0]['include'].remove('fab.kicad_pro')
        self.scope = plain
        plain_run, plain_result = self.run_case(None)
        self.assertEqual(plain_result['outcome'], 'pass', plain_result)
        self.assertNotIn('bom_variant', read_json(plain_run / 'work/fab/fab/manifest.json'))
        self.assertEqual((fab / 'cpl.csv').read_bytes(), (plain_run / 'work/fab/fab/cpl.csv').read_bytes())

    def test_variant_that_changes_the_populated_set_is_refused_by_name(self):
        run, result = self.run_case('LITE')
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertIn("BOM variant 'LITE' changes the populated set (adds [], removes ['R2'])", result['summary'])
        self.assertIn('BOM/CPL designator parity refuses it', result['summary'])
        self.assertEqual(result['details']['bom_variant']['populated_set_change'], {'added': [], 'removed': ['R2']})
        self.assertFalse((run / 'work/fab/fab').exists())
        self.assertFalse((run / 'work/fab/fab.incomplete/manifest.json').exists())

    def test_unregistered_variant_or_uncaptured_registry_is_refused_before_kicad_cli(self):
        run, result = self.run_case('NOPE')
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertIn("BOM variant 'NOPE' refused before kicad-cli ran", result['summary'])
        self.assertIn("['NOPE'] are not registered in fab.kicad_pro", result['summary'])
        self.assertNotIn('tools', result['details'])
        self.scope['inputs'][0]['include'].remove('fab.kicad_pro')
        run, result = self.run_case('ALT')
        self.assertEqual(result['execution'], 'unsupported', result)
        self.assertIn('variant registry design/fab.kicad_pro is not a captured candidate file', result['summary'])
        self.assertNotIn('tools', result['details'])


if __name__ == '__main__':
    unittest.main()

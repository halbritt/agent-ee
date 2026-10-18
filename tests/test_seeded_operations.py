"""AGENTEE-108 seed operations on owned snippets: exact edits and connectivity refusal."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # Also run as tests.test_seeded_operations.

from agent_ee.benchmark import (check_partitions, conductor_groups, mutate, parse, replace_spans,
                                schematic_target, wire_edits)

J1, R1, J2, TP1 = ('11111111-1111-1111-1111-11111111111%d' % i for i in range(1, 5))
LIBRARY = '''(lib_symbols
  (symbol "Owned:Pin" (symbol "Pin_1_1" (pin passive line (at 0 0) (name "P") (number "1"))))
  (symbol "Owned:R" (symbol "R_1_1" (pin passive line (at 0 0) (name "A") (number "1"))
                                    (pin passive line (at 0 -10) (name "B") (number "2")))))'''


def symbol(lib, ref, uuid, x, y, extra=''):
    return (f'(symbol (lib_id "Owned:{lib}") (at {x} {y} 0) (unit 1) (in_bom yes) (on_board yes) (dnp no) '
            f'(uuid "{uuid}") (property "Reference" "{ref}") (property "Value" "X") '
            f'(property "Footprint" "Owned:{ref}") (property "LCSC" "C1"){extra})')


def schematic(wires=None, labels=''):
    # J1 -w1- R1.1 ; R1.2 -w2- J2 ; TP1 sits on w1.
    wires = wires if wires is not None else ('(wire (pts (xy 0 0) (xy 10 0)) (uuid "w1"))'
                                             '(wire (pts (xy 10 10) (xy 20 10)) (uuid "w2"))')
    return ('(kicad_sch (version 20231120) (generator "eeschema") (uuid "00000000-0000-0000-0000-0000000000aa") '
            + LIBRARY + symbol('Pin', 'J1', J1, 0, 0) + symbol('R', 'R1', R1, 10, 0)
            + symbol('Pin', 'J2', J2, 20, 10) + symbol('Pin', 'TP1', TP1, 5, 0) + wires + labels + ')\n')


def board(*, b_track=False, junction=True, track_end=4, track_width=0.5):
    """Owned saved board: J1(A) - R1 - J2(B); net B joined only by a filled zone."""
    import pcbnew
    pcb = pcbnew.BOARD()
    nets = {}
    for name in ('A', 'B'):
        nets[name] = pcbnew.NETINFO_ITEM(pcb, name)
        pcb.Add(nets[name])
    def footprint(ref, uuid, pads):
        fp = pcbnew.FOOTPRINT(pcb)
        fp.SetReference(ref)
        fp.SetValue('X')
        fp.SetFPIDAsString('Owned:'+ref)
        fp.SetPath(pcbnew.KIID_PATH('/'+uuid))
        fp.SetAttributes(pcbnew.FP_SMD)
        fp.SetField('LCSC', 'C1')
        pcb.Add(fp)
        for number, x, net in pads:
            pad = pcbnew.PAD(fp)
            pad.SetNumber(number)
            pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            layers = pcbnew.LSET()
            layers.AddLayer(pcbnew.F_Cu)
            pad.SetLayerSet(layers)
            pad.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(1), pcbnew.FromMM(1)))
            pad.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), 0))
            pad.SetNet(nets[net])
            fp.Add(pad)
    footprint('J1', J1, [('1', 0, 'A')])
    footprint('R1', R1, [('1', 4, 'A'), ('2', 6, 'B')])
    footprint('J2', J2, [('1', 10, 'B')])
    footprint('TP1', TP1, [('1', 2, 'A')])
    def track(a, b, net, width):
        item = pcbnew.PCB_TRACK(pcb)
        item.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(a), 0))
        item.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(b), 0))
        item.SetLayer(pcbnew.F_Cu)
        item.SetWidth(pcbnew.FromMM(width))
        item.SetNet(nets[net])
        pcb.Add(item)
    if junction:
        # The two A tracks end inside TP1's pad without touching, so TP1 is a copper junction.
        track(0, 1.6, 'A', track_width)
        track(2.4, track_end, 'A', track_width)
    else:
        track(0, track_end, 'A', track_width)
    if b_track:
        track(6, 10, 'B', 0.5)
    zone = pcbnew.ZONE(pcb)
    zone.SetLayer(pcbnew.F_Cu)
    zone.SetNet(nets['B'])
    outline = zone.Outline()
    outline.NewOutline()
    for x, y in [(5.5, -1), (10.5, -1), (10.5, 1), (5.5, 1)]:
        outline.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
    zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    pcb.Add(zone)
    pcbnew.ZONE_FILLER(pcb).Fill(pcb.Zones())
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)/'owned.kicad_pcb'
        pcbnew.SaveBoard(str(path), pcb)
        return path.read_text()


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class OwnedProject:
    """Write owned files and build the case/board records mutate consumes."""
    def __init__(self, directory, sch, pcb, extra=None):
        self.root = Path(directory)
        self.files = {'owned.kicad_sch': sch, 'owned.kicad_pcb': pcb, **(extra or {})}
        for rel, text in self.files.items():
            path = self.root/rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.board = {'native_files': {'schematic': 'owned.kicad_sch', 'pcb': 'owned.kicad_pcb',
                                       'project': 'owned.kicad_pro'}}

    def selector(self, ref, uuid):
        from agent_ee.benchmark import children, properties, select, uid, value
        fp = next(f for f in children(parse(self.files['owned.kicad_pcb']), 'footprint')
                  if properties(f).get('Reference') == ref)
        return {'reference': ref, 'schematic': {'lib_id': 'Owned:R' if ref == 'R1' else 'Owned:Pin',
                                                'symbol_uuid': uuid},
                'pcb': {'footprint_uuid': uid(fp), 'footprint_path': value(fp, 'path'), 'name': 'Owned:'+ref}}

    def mutate(self, mutation, ref='R1', uuid=R1, files=('owned.kicad_sch', 'owned.kicad_pcb')):
        case = {'id': 'owned', 'mutation': mutation, 'source_selectors': [self.selector(ref, uuid)] if ref else [],
                'source_file_sha256': {rel: sha(self.files[rel]) for rel in files}}
        return mutate(case, self.board, self.root, self.root)


def native():
    from toolchain import require_toolchain
    require_toolchain(native=True)


class SchematicPartitionTests(unittest.TestCase):
    def test_declared_delta_operations_accept_exact_and_refuse_other_conductor_changes(self):
        source = schematic(labels='(label "A" (at 5 0 0) (uuid "la"))(label "B" (at 15 10 0) (uuid "lb"))')
        renamed = source.replace('(label "A"', '(label "B"')
        old, new = conductor_groups(parse(source)), conductor_groups(parse(renamed))
        merge = {'operation': 'rename_exact_label',
                 'expected_delta': [{'kind': 'merge', 'terminals': [['J1', '1'], ['J2', '1']]}]}
        self.assertTrue(check_partitions(old, new, merge, 'owned')['passed'])
        # Renaming the label moved J1 and TP1 too; a single-terminal move is refused.
        move = {**merge, 'expected_delta': [{'kind': 'move', 'terminal': ['R1', '1'], 'to': ['J2', '1']}]}
        with self.assertRaisesRegex(ValueError, 'differs from its declaration'):
            check_partitions(old, new, move, 'owned')
        moved = {'operation': 'move_wire_endpoints', 'wire_replacements': [
            {'wire_uuid': 'w1', 'before_xy': [[0, 0], [10, 0]], 'after_xy': [[0, 0], [10, 10]]}]}
        changed = replace_spans(source, wire_edits(source, parse(source), moved))
        old, new = conductor_groups(parse(source)), conductor_groups(parse(changed))
        # The diagonal leaves R1.1 and TP1 (formerly on w1) alone and joins J1 to net B.
        moved['expected_delta'] = [{'kind': 'isolate', 'terminal': ['R1', '1']},
                                   {'kind': 'isolate', 'terminal': ['TP1', '1']},
                                   {'kind': 'merge', 'terminals': [['J1', '1'], ['J2', '1']]}]
        self.assertTrue(check_partitions(old, new, moved, 'owned')['passed'])
        moved['expected_delta'] = moved['expected_delta'][:1]
        with self.assertRaisesRegex(ValueError, 'differs from its declaration'):
            check_partitions(old, new, moved, 'owned')
        with self.assertRaisesRegex(ValueError, 'already isolated'):
            check_partitions(old, new, {**moved, 'expected_delta': [{'kind': 'isolate', 'terminal': ['R1', '2']}] * 2}, 'owned')

    def test_connectivity_neutral_operations_refuse_any_schematic_delta(self):
        source = schematic()
        opened = schematic(wires='(wire (pts (xy 10 10) (xy 20 10)) (uuid "w2"))')
        old, new = conductor_groups(parse(source)), conductor_groups(parse(opened))
        for op in ('replace_exact_field', 'set_assembly_state', 'project_rule_edit', 'pcb_track_width',
                   'pcb_move_footprint', 'pcb_delete_zone'):
            with self.subTest(op=op):
                self.assertTrue(check_partitions(old, old, {'operation': op}, 'owned')['passed'])
                with self.assertRaisesRegex(ValueError, 'changed schematic connectivity'):
                    check_partitions(old, new, {'operation': op}, 'owned')
        # remove_component keeps every remaining group except the removed terminals.
        removed = conductor_groups(parse(source.replace(symbol('Pin', 'TP1', TP1, 5, 0), '')))
        self.assertTrue(check_partitions(old, removed, {'operation': 'remove_component', 'reference': 'TP1'}, 'owned')['passed'])
        with self.assertRaisesRegex(ValueError, 'protection component not removed'):
            check_partitions(old, old, {'operation': 'remove_component', 'reference': 'TP1'}, 'owned')


class HierarchyTargetTests(unittest.TestCase):
    ROOT = '(kicad_sch (uuid "r0") (lib_symbols) {sheets})'
    SHEET = '(sheet (at 0 0) (size 1 1) (uuid "{uuid}") (property "Sheetname" "{name}") (property "Sheetfile" "child.kicad_sch"))'

    def target(self, sheets, spec, *, declared=True, child='(kicad_sch (uuid "c0") (lib_symbols))'):
        texts = {'root.kicad_sch': self.ROOT.format(sheets=sheets), 'child.kicad_sch': child}
        board = {'native_files': {'schematic': 'root.kicad_sch'}}
        if declared:
            board['hierarchy'] = 'single-level sheet instances'
        return schematic_target(texts, board, {'schematic': spec})

    def test_single_instance_path_resolves_and_shared_or_nested_sheets_are_refused(self):
        one = self.SHEET.format(uuid='s1', name='a')
        rel, _, instance = self.target(one, {'file': 'child.kicad_sch', 'instance_path': '/r0/s1'})
        self.assertEqual((rel, instance), ('child.kicad_sch', '/r0/s1'))
        with self.assertRaisesRegex(ValueError, 'unique instance path'):
            self.target(one, {'file': 'child.kicad_sch', 'instance_path': '/r0/other'})
        with self.assertRaisesRegex(ValueError, 'unique instance path'):
            self.target(one + self.SHEET.format(uuid='s2', name='b'), {'file': 'child.kicad_sch', 'instance_path': '/r0/s1'})
        with self.assertRaisesRegex(ValueError, 'nested'):
            self.target(one, {'file': 'child.kicad_sch', 'instance_path': '/r0/s1'},
                        child='(kicad_sch (uuid "c0") (lib_symbols) ' + self.SHEET.format(uuid='s3', name='n') + ')')
        with self.assertRaisesRegex(ValueError, 'hierarchical mutation unsupported'):
            self.target(one, {'file': 'child.kicad_sch', 'instance_path': '/r0/s1'}, declared=False)

    def test_instance_reference_comes_from_native_instance_path(self):
        from agent_ee.benchmark import reference_of
        node = parse('(symbol (property "Reference" "R?") (instances (project "p" '
                     '(path "/r0/s1" (reference "R7") (unit 1)) (path "/r0/s2" (reference "R8") (unit 1)))))')
        self.assertEqual(reference_of(node, '/r0/s2'), 'R8')
        self.assertEqual(reference_of(node), 'R?')
        with self.assertRaisesRegex(ValueError, 'unique reference'):
            reference_of(node, '/r0/s3')


class PcbOperationTests(unittest.TestCase):
    def setUp(self):
        native()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def project(self, **options):
        directory = Path(self.tmp.name)/str(len(list(Path(self.tmp.name).iterdir())))
        return OwnedProject(directory, schematic(), board(**options))

    def test_connectivity_neutral_operations_refuse_any_copper_delta(self):
        from agent_ee.benchmark import children, span, verify_pcb_connectivity
        before = board(b_track=True)
        root = parse(before)
        opened = replace_spans(before, [span(children(root, 'segment')[0], '')])  # J1 loses its A track
        for op in ('replace_exact_field', 'set_assembly_state', 'pcb_track_width', 'pcb_delete_zone'):
            with self.subTest(op=op):
                self.assertTrue(verify_pcb_connectivity(before, before, {'operation': op}, 'R1', R1)['passed'])
                with self.assertRaisesRegex(ValueError, 'changed saved copper connectivity'):
                    verify_pcb_connectivity(before, opened, {'operation': op}, 'R1', R1)

    def test_replace_exact_field_edits_both_representations_and_refuses_identity_fields(self):
        project = self.project()
        changed, receipt = project.mutate({'operation': 'replace_exact_field', 'field': 'LCSC', 'before': 'C1',
                                           'after': 'C2', 'representations': ['schematic', 'pcb']})
        self.assertEqual(receipt['changed_files'], ['owned.kicad_pcb', 'owned.kicad_sch'])
        self.assertTrue(receipt['schematic_connectivity']['passed'] and receipt['pcb_connectivity']['passed'])
        self.assertEqual(changed['owned.kicad_sch'].count('"LCSC" "C2"'), 1)
        for field in ('Reference', 'Value', 'Footprint'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'identity'):
                project.mutate({'operation': 'replace_exact_field', 'field': field, 'before': 'X', 'after': 'Y',
                                'representations': ['schematic']})
        with self.assertRaisesRegex(ValueError, 'stale'):
            project.mutate({'operation': 'replace_exact_field', 'field': 'LCSC', 'before': 'C9', 'after': 'C2',
                            'representations': ['schematic', 'pcb']})

    def test_set_assembly_state_changes_only_population_and_refuses_stale_or_other_attributes(self):
        project = self.project()
        changed, receipt = project.mutate({'operation': 'set_assembly_state', 'before': {'dnp': False, 'exclude_from_bom': False},
                                           'after': {'dnp': True, 'exclude_from_bom': True}})
        self.assertIn('(dnp yes)', changed['owned.kicad_sch'])
        self.assertIn('(in_bom no)', changed['owned.kicad_sch'])
        self.assertIn('exclude_from_bom dnp)', changed['owned.kicad_pcb'])
        self.assertTrue(receipt['pcb_connectivity']['passed'])
        with self.assertRaisesRegex(ValueError, 'stale'):
            project.mutate({'operation': 'set_assembly_state', 'before': {'dnp': True}, 'after': {'dnp': False}})
        # on_board would drop the footprint from connectivity; it is not an assembly state.
        with self.assertRaisesRegex(ValueError, 'Boolean dnp'):
            project.mutate({'operation': 'set_assembly_state', 'before': {'on_board': True}, 'after': {'on_board': False}})

    def test_remove_component_refuses_a_footprint_that_carries_other_copper(self):
        project = self.project()
        changed, receipt = project.mutate({'operation': 'remove_component', 'before_value': 'X'})
        self.assertNotIn(R1, changed['owned.kicad_sch'])
        self.assertTrue(receipt['pcb_connectivity']['passed'] and receipt['schematic_connectivity']['passed'])
        # TP1's pad joins the two A tracks; deleting it would sever J1 from R1.
        with self.assertRaisesRegex(ValueError, 'severed or altered'):
            project.mutate({'operation': 'remove_component', 'before_value': 'X'}, ref='TP1', uuid=TP1)

    def test_track_width_refuses_narrowing_that_loses_a_pad_contact(self):
        from agent_ee.benchmark import children, net_name, uid, values, one
        project = self.project(junction=False, track_width=0.5)
        pcb = parse(project.files['owned.kicad_pcb'])
        segments = [uid(s) for s in children(pcb, 'segment') if net_name(pcb, s) == 'A']
        changed, receipt = project.mutate({'operation': 'pcb_track_width', 'net': 'A', 'before_width_mm': 0.5,
                                           'after_width_mm': 0.2, 'segment_uuids': segments})
        self.assertIn('(width 0.2)', changed['owned.kicad_pcb'])
        self.assertTrue(receipt['pcb_connectivity']['passed'])
        with self.assertRaisesRegex(ValueError, 'inventory'):
            project.mutate({'operation': 'pcb_track_width', 'net': 'A', 'before_width_mm': 0.5,
                            'after_width_mm': 0.2, 'segment_uuids': segments + ['extra']})
        # A wide track whose end stops short of R1.1 reaches the pad only by width.
        project = self.project(junction=False, track_end=3.2, track_width=2.0)
        pcb = parse(project.files['owned.kicad_pcb'])
        segments = [uid(s) for s in children(pcb, 'segment') if net_name(pcb, s) == 'A']
        with self.assertRaisesRegex(ValueError, 'changed saved copper connectivity'):
            project.mutate({'operation': 'pcb_track_width', 'net': 'A', 'before_width_mm': 2.0,
                            'after_width_mm': 0.2, 'segment_uuids': segments})

    def test_move_footprint_may_detach_only_its_own_pads(self):
        project = self.project()
        changed, receipt = project.mutate({'operation': 'pcb_move_footprint', 'before_xy': [0, 0], 'offset_mm': [0, 5]})
        self.assertEqual(receipt['pcb_connectivity']['moved_pads_detached'], 2)
        with self.assertRaisesRegex(ValueError, 'stale'):
            project.mutate({'operation': 'pcb_move_footprint', 'before_xy': [1, 0], 'offset_mm': [0, 5]})
        # Moving the junction pad splits J1 from R1.1: another pad loses copper.
        with self.assertRaisesRegex(ValueError, 'beyond its own pads'):
            project.mutate({'operation': 'pcb_move_footprint', 'before_xy': [0, 0], 'offset_mm': [0, 5]},
                           ref='TP1', uuid=TP1)

    def test_delete_zone_refuses_removing_the_only_copper_path(self):
        from agent_ee.benchmark import children, uid
        # A native zone may omit its UUID; it is never the addressed zone.
        text = board(b_track=True)
        text = text.rstrip()[:-1] + '(zone (net "A") (layer "F.Cu") (polygon (pts (xy 20 20) (xy 21 20) (xy 21 21)))))\n'
        project = OwnedProject(Path(self.tmp.name)/'uuidless', schematic(), text)
        zone = uid(children(parse(project.files['owned.kicad_pcb']), 'zone')[0])
        changed, receipt = project.mutate({'operation': 'pcb_delete_zone', 'zone_uuid': zone, 'net': 'B', 'layer': 'F.Cu'})
        self.assertNotIn(zone, changed['owned.kicad_pcb'])
        self.assertTrue(receipt['pcb_connectivity']['passed'])
        with self.assertRaisesRegex(ValueError, 'stale zone'):
            project.mutate({'operation': 'pcb_delete_zone', 'zone_uuid': zone, 'net': 'A', 'layer': 'F.Cu'})
        project = self.project(b_track=False)
        zone = uid(children(parse(project.files['owned.kicad_pcb']), 'zone')[0])
        with self.assertRaisesRegex(ValueError, 'changed saved copper connectivity'):
            project.mutate({'operation': 'pcb_delete_zone', 'zone_uuid': zone, 'net': 'B', 'layer': 'F.Cu'})


RC = Path(__file__).resolve().parents[1]/'fixtures/rc/control'
RC_ROOT = 'dc99dc4f-7c27-5bb7-bd88-7cf6f3eb0fcb'
ROOT_UUID, SHEET_UUID = '00000000-0000-4000-8000-0000000000a0', '00000000-0000-4000-8000-0000000000a1'
HIERARCHICAL_ROOT = f"""(kicad_sch (version 20260306) (generator "eeschema") (generator_version "10.0")
  (uuid "{ROOT_UUID}") (paper "A4") (lib_symbols)
  (sheet (at 20 20) (size 30 20) (uuid "{SHEET_UUID}")
    (property "Sheetname" "rc" (at 20 19 0) (effects (font (size 1.27 1.27))))
    (property "Sheetfile" "child.kicad_sch" (at 20 41 0) (effects (font (size 1.27 1.27))))
    (instances (project "owned" (path "/{ROOT_UUID}" (page "2")))))
  (sheet_instances (path "/" (page "1"))))
"""


def hierarchical_rc():
    """The owned RC fixture as the single child sheet of an owned root."""
    child = (RC/'rc_filter.kicad_sch').read_text()
    child = child.replace(f'(path "/{RC_ROOT}"', f'(path "/{ROOT_UUID}/{SHEET_UUID}"').replace('"rc_filter"', '"owned"')
    return {'owned.kicad_sch': HIERARCHICAL_ROOT, 'child.kicad_sch': child}


class FileOperationTests(unittest.TestCase):
    CPL = 'Designator,Val,Mid X,Mid Y\nR1,X,4.0000mm,0.0000mm\n"J1","X","0.0000mm","0.0000mm"\n'

    def project(self, tmp):
        settings = json.loads((RC/'rc_filter.kicad_pro').read_text())
        settings['board']['design_settings']['rules'] = {'min_resolved_spokes': 2, 'min_clearance': 0.0}
        self.pro = json.dumps(settings, indent=2) + '\n'
        project = OwnedProject(tmp, (RC/'rc_filter.kicad_sch').read_text(), '(kicad_pcb)\n',
                               {'owned.kicad_pro': self.pro, 'export/cpl.csv': self.CPL})
        return project

    def test_project_rule_edit_changes_one_rule_and_refuses_connectivity_bearing_settings(self):
        native()
        with tempfile.TemporaryDirectory() as tmp:
            project = self.project(tmp)
            files = ('owned.kicad_sch', 'owned.kicad_pro')
            rule = {'operation': 'project_rule_edit', 'file': 'owned.kicad_pro',
                    'pointer': ['board', 'design_settings', 'rules', 'min_resolved_spokes'], 'before': 2, 'after': 1}
            changed, receipt = project.mutate(rule, ref=None, files=files)
            self.assertEqual(changed['owned.kicad_pro'], self.pro.replace('"min_resolved_spokes": 2', '"min_resolved_spokes": 1'))
            self.assertEqual(receipt['changed_files'], ['owned.kicad_pro'])
            self.assertTrue(receipt['schematic_connectivity']['passed'])
            # Text variables can rename nets; only design rules and net classes are editable.
            with self.assertRaisesRegex(ValueError, 'one scalar design rule'):
                project.mutate({**rule, 'pointer': ['text_variables', 'NET'], 'before': 'A', 'after': 'B'}, ref=None, files=files)
            with self.assertRaisesRegex(ValueError, 'stale'):
                project.mutate({**rule, 'before': 3}, ref=None, files=files)
            with self.assertRaisesRegex(ValueError, 'one scalar'):
                project.mutate({**rule, 'after': [1]}, ref=None, files=files)
            with self.assertRaisesRegex(ValueError, 'native project file'):
                project.mutate({**rule, 'file': 'export/cpl.csv'}, ref=None, files=('export/cpl.csv',))

    def test_project_rule_must_be_textually_unique(self):
        from agent_ee.benchmark import project_rule_text
        text = '{"board": {"design_settings": {"rules": {"x": 2}}}, "net_settings": {"x": 2}}'
        with self.assertRaisesRegex(ValueError, 'textually unique'):
            project_rule_text(text, {'pointer': ['board', 'design_settings', 'rules', 'x'], 'before': 2, 'after': 1})

    def test_fab_file_edit_changes_one_cell_and_refuses_native_files_and_rebinding(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self.project(tmp)
            edit = {'operation': 'fab_file_edit', 'file': 'export/cpl.csv', 'key_column': 'Designator',
                    'key': 'J1', 'column': 'Mid X', 'before': '0.0000mm', 'after': '1.0000mm'}
            changed, receipt = project.mutate(edit, ref=None, files=('export/cpl.csv',))
            self.assertEqual(changed['export/cpl.csv'], self.CPL.replace('"J1","X","0.0000mm"', '"J1","X","1.0000mm"'))
            self.assertEqual(receipt['fab_row'], {'rows': 2, 'cells_changed': 1, 'key': 'J1', 'column': 'Mid X'})
            # The designator binds a row to a component; rewriting it is a connectivity change.
            with self.assertRaisesRegex(ValueError, 'non-key column'):
                project.mutate({**edit, 'column': 'Designator', 'before': 'J1', 'after': 'R1'}, ref=None, files=('export/cpl.csv',))
            with self.assertRaisesRegex(ValueError, 'native design files'):
                project.mutate({**edit, 'file': 'owned.kicad_pcb'}, ref=None, files=('owned.kicad_pcb',))
            with self.assertRaisesRegex(ValueError, 'stale'):
                project.mutate({**edit, 'before': '9mm'}, ref=None, files=('export/cpl.csv',))


class NativeHierarchyTests(unittest.TestCase):
    def setUp(self):
        native()

    def test_native_partitions_match_flat_source_through_a_sheet_instance(self):
        from agent_ee.benchmark import native_partitions
        flat = native_partitions({'rc_filter.kicad_sch': (RC/'rc_filter.kicad_sch').read_text()}, 'rc_filter.kicad_sch')
        nested = native_partitions(hierarchical_rc(), 'owned.kicad_sch')
        self.assertEqual(set(flat.values()), set(nested.values()))
        # The independent geometry reader agrees once its virtual power-symbol pins are dropped.
        geometry = conductor_groups(parse((RC/'rc_filter.kicad_sch').read_text()))
        self.assertEqual(flat, {k: frozenset(t for t in g if not t[0].startswith('#'))
                                for k, g in geometry.items() if not k[0].startswith('#')})

    def test_hierarchical_value_edit_and_declared_delta_are_verified_natively(self):
        from agent_ee.benchmark import children, properties, select, uid, value, values
        texts = hierarchical_rc()
        pcb = (RC/'rc_filter.kicad_pcb').read_text()
        with tempfile.TemporaryDirectory() as tmp:
            project = OwnedProject(tmp, texts['owned.kicad_sch'], pcb, {'child.kicad_sch': texts['child.kicad_sch']})
            project.board['hierarchy'] = 'single-level sheet instances'
            child = parse(texts['child.kicad_sch'])
            resistor = next(s for s in children(child, 'symbol') if properties(s)['Reference'] == 'R1')
            fp = next(f for f in children(parse(pcb), 'footprint') if properties(f).get('Reference') == 'R1')
            selector = {'reference': 'R1', 'schematic': {'file': 'child.kicad_sch', 'lib_id': value(resistor, 'lib_id'),
                        'symbol_uuid': uid(resistor), 'instance_path': f'/{ROOT_UUID}/{SHEET_UUID}'},
                        'pcb': {'footprint_uuid': uid(fp), 'footprint_path': value(fp, 'path'), 'name': values(fp)[0]}}
            files = {rel: sha(text) for rel, text in project.files.items()}
            before = properties(resistor)['Value']
            case = {'id': 'owned', 'source_selectors': [selector], 'source_file_sha256': files, 'mutation': {
                'operation': 'replace_exact_value_properties', 'before': before, 'after': before+'0'}}
            changed, receipt = mutate(case, project.board, project.root, project.root)
            self.assertEqual(receipt['changed_files'], ['child.kicad_sch', 'owned.kicad_pcb'])
            self.assertEqual(receipt['schematic_connectivity']['method'], 'sandboxed kicad-cli kicadxml netlist terminal partitions')
            # Renaming one label changes real conductors; a wrong declaration is refused natively.
            label = children(child, 'label')[0]
            other = next(values(n)[0] for n in children(child, 'label') if values(n)[0] != values(label)[0])
            case['mutation'] = {'operation': 'rename_exact_label', 'label_kind': 'label', 'label_uuid': uid(label),
                                'before': values(label)[0], 'after': other,
                                'expected_delta': [{'kind': 'isolate', 'terminal': ['R1', '1']}]}
            with self.assertRaisesRegex(ValueError, 'differs from its declaration|already isolated'):
                mutate(case, project.board, project.root, project.root)
            shared = texts['owned.kicad_sch'].replace('(sheet_instances', select(parse(texts['owned.kicad_sch']), 'sheet', SHEET_UUID)
                and texts['owned.kicad_sch'][texts['owned.kicad_sch'].index('(sheet (at'):texts['owned.kicad_sch'].index('(sheet_instances')]
                .replace(SHEET_UUID, '00000000-0000-4000-8000-0000000000a2') + '(sheet_instances')
            (project.root/'owned.kicad_sch').write_text(shared)
            case['source_file_sha256']['owned.kicad_sch'] = sha(shared)
            with self.assertRaisesRegex(ValueError, 'single-instance sheet'):
                mutate(case, project.board, project.root, project.root)


class ReportExtensionTests(unittest.TestCase):
    CASE = {'id': 'seed', 'mutation': {'operation': 'pcb_move_footprint'},
            'source_selectors': [{'pcb': {'footprint_uuid': 'fp'}}]}

    def erc(self, *rows):
        return {'check': {'kind': 'erc', 'execution': 'ok', 'outcome': 'defect'},
                'native': {'sheets': [{'violations': [{'type': t, 'items': [{'uuid': u}]} for t, u in rows]}]}}

    def test_planned_detectors_are_unsupported_and_never_silently_scored(self):
        from agent_ee.benchmark_run import classify
        planned = {'kind': 'planned', 'detector': 'AGENTEE-53', 'expected_execution': 'unsupported'}
        self.assertEqual(classify(planned, None, self.CASE), 'unsupported')
        with self.assertRaisesRegex(ValueError, 'unexpectedly executed'):
            classify(planned, self.erc(), self.CASE)

    def test_erc_requires_new_predicted_type_at_registered_item(self):
        from agent_ee.benchmark_run import classify
        expected = {'kind': 'erc', 'predicate': {'type': 'pin_not_connected', 'item_uuids': ['pin']}}
        control = self.erc(('pin_not_connected', 'other'))
        self.assertEqual(classify(expected, self.erc(('pin_not_connected', 'pin')), self.CASE, control), 'detection')
        self.assertEqual(classify(expected, self.erc(('label_dangling', 'pin')), self.CASE, control), 'miss')
        self.assertEqual(classify(expected, self.erc(('pin_not_connected', 'other')), self.CASE, control), 'miss')
        self.assertEqual(classify(expected, self.erc(('pin_not_connected', 'pin')), self.CASE,
                                  self.erc(('pin_not_connected', 'pin'))), 'control_unresolved')
        self.assertEqual(classify(expected, self.erc(('pin_not_connected', 'pin')), self.CASE, None), 'control_unresolved')

    def test_drc_unconnected_pads_resolve_by_retained_pad_uuid(self):
        from agent_ee.benchmark_run import classify
        expected = {'kind': 'drc', 'predicate': {'kind': 'unconnected_items', 'pads': ['1', '2']}}
        def record(uuid):
            return {'check': {'kind': 'drc', 'execution': 'ok', 'outcome': 'defect'}, 'pcb_pad_uuids': {'fp': {'1': ['p1'], '2': ['p2']}},
                    'native': {'unconnected_items': [{'type': 'unconnected_items', 'items': [{'uuid': uuid}]}]}}
        self.assertEqual(classify(expected, record('p2'), self.CASE, record('x')), 'detection')
        self.assertEqual(classify(expected, record('x'), self.CASE, record('x')), 'miss')
        missing = {**record('p2'), 'pcb_pad_uuids': {'fp': {'1': ['p1']}}}
        self.assertEqual(classify(expected, missing, self.CASE, record('x')), 'not_evaluated')

    def test_parity_prediction_targets_only_its_named_component(self):
        from agent_ee.benchmark_run import classify
        case = {'id': 'seed', 'source_selectors': [{'reference': 'U1', 'pcb': {'footprint_uuid': 'u1'}},
                                                   {'reference': 'R1', 'pcb': {'footprint_uuid': 'r1'}}]}
        expected = {'kind': 'drc', 'predicate': {'kind': 'schematic_parity', 'pins': ['5', '6'], 'reference': 'U1'}}
        def record(uuid):
            return {'check': {'kind': 'drc', 'execution': 'ok', 'outcome': 'defect'},
                    'pcb_pad_uuids': {'u1': {'5': ['p5'], '6': ['p6']}, 'r1': {'1': ['r'], '2': ['s']}},
                    'native': {'schematic_parity': [{'type': 'net_conflict', 'items': [{'uuid': uuid}]}]}}
        self.assertEqual(classify(expected, record('p6'), case, record('x')), 'detection')
        # The relation selector R1 is not a parity target, even at a new record.
        self.assertEqual(classify(expected, record('r'), case, record('x')), 'miss')
        absent = {**expected, 'predicate': {**expected['predicate'], 'reference': 'J9'}}
        self.assertEqual(classify(absent, record('p6'), case, record('x')), 'not_evaluated')

    def test_native_visibility_separates_catalogue_classes_and_counts_new_records(self):
        from agent_ee.benchmark_run import native_delta, visibility
        control = {'erc': self.erc(('a', '1'))}
        self.assertEqual(native_delta({'erc': self.erc(('a', '1'), ('a', '1'), ('b', '2'))}, control), {'erc': 2})
        self.assertEqual(native_delta({'erc': {**self.erc(), 'native': None}}, control), {'erc': None})
        cases = [{'id': 'seen', 'predictions': [{'kind': 'erc', 'status': 'detection'}],
                  'native_visibility': {'real_defect': True, 'native_prediction_detected': True, 'new_native_records': {'erc': 1}}},
                 {'id': 'artifact', 'predictions': [{'kind': 'drc', 'status': 'detection'}],
                  'native_visibility': {'real_defect': False, 'native_prediction_detected': True, 'new_native_records': {'drc': 3}}},
                 {'id': 'quiet', 'predictions': [{'kind': 'planned', 'status': 'unsupported'}],
                  'native_visibility': {'real_defect': False, 'native_prediction_detected': False, 'new_native_records': {'drc': None}}}]
        report = visibility(cases)
        self.assertEqual(report['native_visible_real_defect']['cases'], ['seen'])
        self.assertEqual(report['native_invisible_real_defect']['cases'], ['artifact', 'quiet'])
        self.assertEqual(report['native_invisible_real_defect']['native_prediction_detected'], ['artifact'])
        self.assertEqual(report['native_invisible_real_defect']['native_unavailable'], ['quiet'])
        self.assertIsNone(visibility([{'id': 'old', 'predictions': []}]))


def git_commit(repository, message):
    import subprocess
    subprocess.run(['git', '-C', str(repository), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repository), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-qm', message], check=True)
    return subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()


def owner_manifest():
    """The owned AGENTEE-21 manifest recast as an owner-Git corpus with a planned detector."""
    data = json.loads((Path(__file__).resolve().parents[1]/'benchmarks/seeded/expectations.json').read_text())
    board = data['boards'][0]
    board['source'] = {'kind': 'owner_git', 'tree': '0'*40}
    board['license'] = {**board['license'], 'hardware_grant': None, 'status': 'Owner repository without a licence file.'}
    data['cases'][0]['expected_checks'].append({'kind': 'planned', 'check_id': 'planned-x', 'detector': 'AGENTEE-1',
                                                'expected_execution': 'unsupported', 'qualifying_locator': 'x'})
    return data


class OwnerCorpusTests(unittest.TestCase):
    def test_owner_source_is_read_by_pinned_identity_and_never_written(self):
        import subprocess
        from agent_ee.benchmark_run import owner_tree
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)/'owner'
            subprocess.run(['git', 'init', '-q', str(repository)], check=True)
            (repository/'board.kicad_sch').write_text('(kicad_sch)\n')
            (repository/'notes.md').write_text('not pinned\n')
            git_commit(repository, 'owned')
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repository), *args], text=True).strip()
            before = (git('rev-parse', 'HEAD'), git('status', '--porcelain'))
            board = {'commit': git('rev-parse', 'HEAD'), 'source': {'kind': 'owner_git', 'tree': git('rev-parse', 'HEAD^{tree}')},
                     'native_file_sha256': {'board.kicad_sch': sha('(kicad_sch)\n')}, 'captured_dependency_sha256': {}}
            target = Path(tmp)/'cache'
            target.mkdir()
            owner_tree(repository, board, target)
            self.assertEqual(sorted(p.name for p in (target/'tree').iterdir()), ['board.kicad_sch'])
            self.assertEqual((git('rev-parse', 'HEAD'), git('status', '--porcelain')), before)
            for change, message in ((dict(source={**board['source'], 'tree': '0'*40}), 'tree identity'),
                                    (dict(native_file_sha256={'board.kicad_sch': '0'*64}), 'hash mismatch')):
                target = Path(tmp)/('cache-'+message.split()[0])
                target.mkdir()
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    owner_tree(repository, {**board, **change}, target)

    def test_committed_minimum_is_per_corpus_and_owner_declarations_are_required(self):
        import copy
        from agent_ee.benchmark_run import MINIMUM_CORPUS, load_manifest
        self.assertEqual(MINIMUM_CORPUS['fome-mc'], (1, 6))
        data = owner_manifest()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'manifest.json'
            path.write_text(json.dumps(data))
            self.assertEqual(len(load_manifest(path)['cases']), 30)
            for change, message in ((('corpus', 'other'), 'unknown benchmark corpus'),
                                    (('container_prefix', 'evil; rm'), 'container prefix')):
                altered = copy.deepcopy(data)
                altered[change[0]] = change[1]
                path.write_text(json.dumps(altered))
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    load_manifest(path)
            altered = copy.deepcopy(data)
            altered['boards'][0]['license']['status'] = ''
            path.write_text(json.dumps(altered))
            with self.assertRaisesRegex(ValueError, 'license/publication'):
                load_manifest(path)
            altered = copy.deepcopy(data)
            altered['cases'][0]['expected_checks'][-1]['expected_execution'] = 'ok'
            path.write_text(json.dumps(altered))
            with self.assertRaisesRegex(ValueError, 'planned detector'):
                load_manifest(path)


def store_script():
    import importlib.util
    path = Path(__file__).resolve().parents[1]/'scripts/fome_mc_benchmark_store.py'
    spec = importlib.util.spec_from_file_location('fome_mc_benchmark_store', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PrivateStoreTests(unittest.TestCase):
    """The private Git-backed store mechanics, on an owned manifest and owned report bytes."""

    def setUp(self):
        import subprocess
        self.script = store_script()
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-108-store-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'source'
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        seeded = self.source/'benchmarks/seeded'
        seeded.mkdir(parents=True)
        (self.source/'unrelated.txt').write_text('never materialised\n')
        (seeded/'fome-mc-expectations.json').write_text(json.dumps(owner_manifest()))
        self.preregistration = git_commit(self.source, 'preregister')
        (seeded/'fome-mc-report-run1.json').write_text('{"run": 1}\n')
        self.commit = git_commit(self.source, 'report')
        self.files = {str(p.relative_to(self.source)): sha(p.read_text()) for p in sorted(seeded.iterdir())}
        self.before = subprocess.check_output(['git', '-C', str(self.source), 'status', '--porcelain'], text=True)

    def place(self, store):
        return self.script.place(store, self.source, self.commit, self.preregistration, self.files)

    def test_place_fetches_only_the_files_and_proves_preregistration(self):
        import subprocess
        store = self.root/'store'
        result = self.place(store)
        self.assertEqual((result['status'], result['head'], result['cases']), ('verified', self.commit, 30))
        self.assertEqual(sorted(str(p.relative_to(store)) for p in store.rglob('*') if p.is_file() and '.git' not in p.parts),
                         sorted(self.files))
        self.assertEqual(subprocess.check_output(['git', '-C', str(self.source), 'status', '--porcelain'], text=True),
                         self.before)
        self.assertEqual(self.script.verify(store, self.preregistration, self.files)['status'], 'verified')
        with self.assertRaises(FileExistsError):
            self.place(store)

    def test_changed_bytes_and_unproven_preregistration_are_refused(self):
        import subprocess
        store = self.root/'store'
        self.place(store)
        report = store/'benchmarks/seeded/fome-mc-report-run1.json'
        report.write_text('{"run": 2}\n')
        with self.assertRaisesRegex(ValueError, 'uncommitted changes'):
            self.script.verify(store, self.preregistration, self.files)
        report.write_text('{"run": 1}\n')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.script.verify(store, self.preregistration, {**self.files, 'benchmarks/seeded/fome-mc-report-run1.json': '0'*64})
        # A preregistration that is not an ancestor of the source commit is refused before any write.
        with self.assertRaises(subprocess.CalledProcessError):
            self.script.place(self.root/'reverse', self.source, self.preregistration, self.commit, self.files)
        self.assertFalse((self.root/'reverse').exists())
        with self.assertRaises(FileNotFoundError):
            self.script.verify(self.root/'missing', self.preregistration, self.files)

    def test_store_must_be_its_own_git_checkout_and_never_tracked(self):
        import shutil
        import subprocess
        from agent_ee.benchmark_run import committed_manifest
        plain = self.root/'plain'
        shutil.copytree(self.source/'benchmarks', plain/'benchmarks')
        with self.assertRaisesRegex(ValueError, 'not its own Git checkout'):
            self.script.verify(plain, self.preregistration, self.files)
        with self.assertRaisesRegex(ValueError, 'not in a Git checkout'):
            committed_manifest(plain/'benchmarks/seeded/fome-mc-expectations.json', self.preregistration)
        # A manifest ignored inside another checkout (the build/ case) has no Git proof either.
        outer = self.root/'outer'
        subprocess.run(['git', 'init', '-q', str(outer)], check=True)
        (outer/'.gitignore').write_text('build/\n')
        git_commit(outer, 'ignore build')
        shutil.copytree(self.source/'benchmarks', outer/'build/store/benchmarks')
        with self.assertRaisesRegex(ValueError, 'not tracked by its Git checkout'):
            committed_manifest(outer/'build/store/benchmarks/seeded/fome-mc-expectations.json', self.preregistration)
        with self.assertRaisesRegex(ValueError, 'not its own Git checkout'):
            self.script.verify(outer/'build/store', self.preregistration, self.files)
        with self.assertRaisesRegex(ValueError, 'would be tracked'):
            self.place(outer/'tracked')
        self.assertFalse((outer/'tracked').exists())
        self.assertEqual(self.place(outer/'build/placed')['status'], 'verified')
        self.assertEqual(subprocess.check_output(['git', '-C', str(outer), 'status', '--porcelain'], text=True), '')


# FomeCorpusTests reads the owner's private fome-mc benchmark store (AGENTEE-118
# owner gate; see docs/seeded-benchmark.md). It is an opt-in host qualification:
# without AGENT_EE_FOME_MC_BENCHMARK the class is deleted below, so discovery
# never collects it and never reports a skip on its account. When the variable
# names a store, a missing or unverifiable store is an error, not a skip.
FOME_STORE = os.environ.get('AGENT_EE_FOME_MC_BENCHMARK')


class FomeCorpusTests(unittest.TestCase):
    def setUp(self):
        self.store = Path(FOME_STORE)
        self.path = self.store/'benchmarks/seeded/fome-mc-expectations.json'

    def test_private_store_is_git_backed_and_preregistered(self):
        script = store_script()
        result = script.verify(self.store)
        self.assertEqual((result['status'], result['preregistration'], result['cases']),
                         ('verified', script.PREREGISTRATION, 16))

    def test_corpus_is_preregistered_with_every_new_operation_and_honest_detectors(self):
        from agent_ee.benchmark_run import contract_for, load_manifest
        from agent_ee.storage import identity
        manifest = load_manifest(self.path)
        board, = manifest['boards']
        self.assertEqual((manifest['corpus'], board['hierarchy'], board['license']['hardware_grant']),
                         ('fome-mc', 'single-level sheet instances', None))
        self.assertFalse(manifest['repository_publication_authorized'])
        self.assertEqual(identity(contract_for(board, manifest['cases'])), board['contract_sha256'])
        operations = {c['mutation']['operation'] for c in manifest['cases']}
        self.assertLessEqual({'replace_exact_field', 'set_assembly_state', 'remove_component', 'project_rule_edit',
                              'pcb_track_width', 'pcb_move_footprint', 'pcb_delete_zone', 'fab_file_edit'}, operations)
        first = {'F01', 'F03', 'F04', 'F05', 'F16', 'F26'}
        self.assertLessEqual(first, {c['catalogue_id'] for c in manifest['cases']})
        for case in manifest['cases']:
            with self.subTest(case=case['id']):
                receipt = case['structural_receipt']
                self.assertTrue(receipt['structural_only'] and receipt['changed_files'])
                self.assertTrue(all(v['passed'] for k, v in receipt.items()
                                    if k in ('wiring', 'schematic_connectivity', 'pcb_connectivity', 'pcb_wiring')))
                self.assertIsInstance(case['native_visibility']['real_defect'], bool)
                for check in case['expected_checks']:
                    if check['kind'] == 'planned':
                        self.assertEqual(check['expected_execution'], 'unsupported')
                for selector in case['source_selectors']:
                    self.assertTrue(selector['schematic']['instance_path'].startswith('/'))
        self.assertTrue(any(c['native_visibility']['real_defect'] for c in manifest['cases']))
        self.assertTrue(any(not c['native_visibility']['real_defect'] for c in manifest['cases']))


if not FOME_STORE:
    del FomeCorpusTests


if __name__ == '__main__':
    unittest.main()

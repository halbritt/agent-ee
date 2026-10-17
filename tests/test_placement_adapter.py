"""Placement-iteration write scope and coordinator-run helper step (AGENTEE-91)."""
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from agent_ee import placement_adapter as pa
from agent_ee.storage import digest, read_json, write_json
from toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
BOARD = 'project/rc_filter.kicad_pcb'
SCHEMATIC = 'project/rc_filter.kicad_sch'
POLICY = 'project/placement-policy.json'
FP_R = '11111111-1111-4111-8111-111111111111'
FP_J = '22222222-2222-4222-8222-222222222222'

# Owned minimal saved-board text in the KiCad 10 dialect; not a production footprint.
TEXT = '''(kicad_pcb
 (version 20260206) (generator "pcbnew") (generator_version "10.0")
 (general (thickness 1.6) (legacy_teardrops no))
 (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
 (setup (pad_to_mask_clearance 0))
 (footprint "Owned:R" (layer "F.Cu") (uuid "11111111-1111-4111-8111-111111111111") (at 10 10)
  (property "Reference" "R1" (at 0 -1.5 0) (layer "F.SilkS") (uuid "a1") (effects (font (size 1 1))))
  (property "Value" "10k" (at 0 1.5 0) (layer "F.Fab") (uuid "a2") (effects (font (size 1 1))))
  (path "/r1") (attr smd)
  (fp_line (start -1 -1) (end 1 -1) (stroke (width 0.05) (type solid)) (layer "F.CrtYd") (uuid "a3"))
  (fp_line (start 1 -1) (end 1 1) (stroke (width 0.05) (type solid)) (layer "F.CrtYd") (uuid "a4"))
  (fp_text user "${REFERENCE}" (at 0 0 90) (layer "F.Fab") (uuid "a5") (effects (font (size 0.5 0.5))))
  (pad "1" smd rect (at -0.5 0) (size 0.5 0.5) (layers "F.Cu") (net "/IN") (uuid "a6"))
  (pad "2" smd rect (at 0.5 0) (size 0.5 0.5) (layers "F.Cu") (net "/OUT") (uuid "a7"))
  (model "${KICAD10_3DMODEL_DIR}/owned.wrl" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0))))
 (footprint "Owned:J" (layer "F.Cu") (uuid "22222222-2222-4222-8222-222222222222") (at 2 10)
  (property "Reference" "J1" (at 0 -2 0) (layer "F.SilkS") (uuid "b1") (effects (font (size 1 1))))
  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net "/IN") (uuid "b2")))
 (gr_line (start 0 0) (end 30 0) (stroke (width 0.05) (type default)) (layer "Edge.Cuts") (uuid "e1"))
 (gr_line (start 30 0) (end 0 0) (stroke (width 0.05) (type default)) (layer "Edge.Cuts") (uuid "e2"))
)
'''


def mutate(*pairs, text=TEXT):
    for old, new in pairs:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text.encode()


def rotated(turn, x='12', y='9'):
    """R1 translated and rotated, with every rotated child advanced as KiCad 10 saves it."""
    return mutate(('(at 10 10)', f'(at {x} {y} {turn})'), ('(at 0 -1.5 0)', f'(at 0 -1.5 {turn})'),
                  ('(at 0 1.5 0)', f'(at 0 1.5 {turn})'), ('(at 0 0 90)', f'(at 0 0 {(90 + turn) % 360})'),
                  ('(at -0.5 0)', f'(at -0.5 0 {turn})'), ('(at 0.5 0)', f'(at 0.5 0 {turn})'))


class WriteScopeTest(unittest.TestCase):
    def assertRefused(self, after, fragment, protected=()):
        report = pa.write_scope(TEXT.encode(), after, protected)
        self.assertEqual(report['status'], 'refused', report)
        self.assertTrue(any(fragment in v for v in report['violations']), report['violations'])
        self.assertEqual(report['transforms'], [])
        return report

    def test_identical_board_is_allowed_without_transforms(self):
        report = pa.write_scope(TEXT.encode(), TEXT.encode(), [FP_J])
        self.assertEqual((report['status'], report['transforms'], report['violations']), ('allowed', [], []))
        self.assertEqual(report['before_sha256'], hashlib.sha256(TEXT.encode()).hexdigest())

    def test_translation_and_each_quarter_turn_with_child_angles_are_allowed(self):
        for turn in (90, 180, 270, -90):
            with self.subTest(turn=turn):
                report = pa.write_scope(TEXT.encode(), rotated(turn), [FP_J])
                self.assertEqual(report['status'], 'allowed', report['violations'])
                self.assertEqual(report['transforms'], [{'uuid': FP_R, 'reference': 'R1',
                    'rotation_deg': turn % 360, 'from': ['10', '10', '0'], 'to': ['12', '9', str(turn)]}])

    def test_translation_only_and_reordered_keyed_graphics_are_allowed(self):
        line3 = '  (fp_line (start -1 -1) (end 1 -1) (stroke (width 0.05) (type solid)) (layer "F.CrtYd") (uuid "a3"))\n'
        line4 = '  (fp_line (start 1 -1) (end 1 1) (stroke (width 0.05) (type solid)) (layer "F.CrtYd") (uuid "a4"))\n'
        after = mutate(('(at 10 10)', '(at 20.25 3)'), (line3 + line4, line4 + line3))
        report = pa.write_scope(TEXT.encode(), after)
        self.assertEqual(report['status'], 'allowed', report['violations'])
        self.assertEqual(report['transforms'][0]['rotation_deg'], 0)

    def test_board_items_outside_footprint_transforms_are_refused(self):
        cases = {
            'outline moved': (('(end 30 0) (stroke', '(end 31 0) (stroke'), 'board: removed or changed gr_line e1'),
            'track added': (('\n)\n', '\n (segment (start 1 1) (end 2 2) (width 0.25) (layer "F.Cu") (net "/IN") (uuid "t1"))\n)\n'),
                            'board: added or changed segment t1'),
            'zone added': (('\n)\n', '\n (zone (net "GND") (layer "B.Cu") (uuid "z1") (polygon (pts (xy 0 0) (xy 1 0) (xy 1 1))))\n)\n'),
                           'board: added or changed zone z1'),
            'rules changed': (('(pad_to_mask_clearance 0)', '(pad_to_mask_clearance 0.1)'), 'board: removed or changed setup'),
            'header changed': (('(generator_version "10.0")', '(generator_version "9.0")'), 'generator_version'),
            'footprint uuid changed': (('(uuid "22222222-2222-4222-8222-222222222222")',
                                        '(uuid "33333333-3333-4333-8333-333333333333")'), 'footprint 2222'),
        }
        for name, (pair, fragment) in cases.items():
            with self.subTest(name):
                self.assertRefused(mutate(pair), fragment)

    def test_item_reorder_and_unparseable_or_ambiguous_boards_are_refused(self):
        e1 = ' (gr_line (start 0 0) (end 30 0) (stroke (width 0.05) (type default)) (layer "Edge.Cuts") (uuid "e1"))\n'
        e2 = ' (gr_line (start 30 0) (end 0 0) (stroke (width 0.05) (type default)) (layer "Edge.Cuts") (uuid "e2"))\n'
        self.assertRefused(mutate((e1 + e2, e2 + e1)), 'item order changed')
        self.assertRefused(TEXT.encode()[:-3], 'board unparseable')
        self.assertRefused(b'(kicad_sch (version 1))', 'not kicad_pcb')
        self.assertRefused(mutate(('(uuid "22222222-2222-4222-8222-222222222222")',
                                   '(uuid "11111111-1111-4111-8111-111111111111")')), 'unique native UUID')

    def test_footprint_local_content_changes_are_refused(self):
        cases = {
            'pad local position': ('(at -0.5 0)', '(at -0.6 0)'),
            'pad size': ('(at -0.5 0) (size 0.5 0.5)', '(at -0.5 0) (size 0.6 0.5)'),
            'pad net': ('(net "/OUT") (uuid "a7")', '(net "GND") (uuid "a7")'),
            'pad number': ('(pad "2" smd', '(pad "3" smd'),
            'pad uuid': ('(uuid "a7")', '(uuid "a8")'),
            'field value': ('"Value" "10k"', '"Value" "100k"'),
            'field position': ('(at 0 1.5 0)', '(at 0 1.6 0)'),
            'model': ('owned.wrl', 'other.wrl'),
            'schematic path': ('(path "/r1")', '(path "/r2")'),
            'courtyard graphic': ('(start 1 -1) (end 1 1)', '(start 1 -1) (end 1 2)'),
            'library identity': ('"Owned:R"', '"Owned:R2"'),
            'lone pad rotation': ('(at -0.5 0)', '(at -0.5 0 90)'),
        }
        for name, pair in cases.items():
            with self.subTest(name):
                self.assertRefused(mutate(pair), 'R1')

    def test_rotation_must_be_a_quarter_turn_applied_to_every_rotated_child(self):
        self.assertRefused(mutate(('(at 10 10)', '(at 10 10 45)')), 'not a quarter turn')
        # The footprint turned but one pad kept its saved board angle.
        partial = rotated(90).replace(b'(at 0.5 0 90)', b'(at 0.5 0)')
        self.assertRefused(partial, 'pad a7 changed beyond the footprint transform')

    def test_side_flip_is_refused_as_unsupported(self):
        self.assertRefused(mutate(('(footprint "Owned:R" (layer "F.Cu")', '(footprint "Owned:R" (layer "B.Cu")')),
                           'side flips are not supported')

    def test_protected_footprint_cannot_move_rotate_or_change_lock(self):
        self.assertRefused(mutate(('(at 2 10)', '(at 3 10)')), 'protected footprint transform changed', [FP_J])
        self.assertRefused(mutate(('(at 2 10)', '(at 2 10 90)')), 'protected footprint transform changed', [FP_J])
        self.assertRefused(mutate(('(at 2 10)', '(at 2.0 10)')), 'protected footprint transform changed', [FP_J])
        self.assertRefused(TEXT.encode(), 'absent from source board', ['44444444-4444-4444-8444-444444444444'])
        self.assertRefused(mutate(('(at 2 10)', '(at 2 10) (locked yes)')), 'locked')
        # The same move of an unprotected footprint is a transform.
        self.assertEqual(pa.write_scope(TEXT.encode(), mutate(('(at 2 10)', '(at 3 10)')))['status'], 'allowed')

    def test_protected_set_includes_declared_uuids_policy_references_and_locked_parts(self):
        locked = mutate(('(at 2 10)', '(at 2 10) (locked yes)'))
        self.assertEqual(pa.protected_footprints(locked, []), ([FP_J], ['J1']))
        self.assertEqual(pa.protected_footprints(TEXT.encode(), [FP_R], ['J1']), (sorted([FP_R, FP_J]), ['J1', 'R1']))
        with self.assertRaisesRegex(ValueError, 'absent from source board'):
            pa.protected_footprints(TEXT.encode(), ['44444444-4444-4444-8444-444444444444'])
        with self.assertRaisesRegex(ValueError, 'exactly one source footprint'):
            pa.protected_footprints(TEXT.encode(), [], ['U9'])


class DeclarationAndContextTest(unittest.TestCase):
    def declaration(self, **changes):
        value = {'schema': pa.ADAPTER_SCHEMA, 'helper': 'agent-ee.placer/1', 'policy': POLICY,
                 'fixed_footprints': [FP_J], 'timeout_s': 600}
        value.update(changes)
        return value

    def test_declaration_boundaries(self):
        self.assertEqual(pa.validate_declaration(self.declaration())['fixed_footprints'], [FP_J])
        self.assertIsNone(pa.validate_declaration(self.declaration(helper=None, policy=None))['helper'])
        for name, changes in {'unknown helper': {'helper': 'freerouting'}, 'helper without policy': {'policy': None},
                              'policy without helper': {'helper': None}, 'absolute policy': {'policy': '/etc/passwd'},
                              'escaping policy': {'policy': '../policy.json'}, 'non-uuid fixed': {'fixed_footprints': ['J1']},
                              'duplicate fixed': {'fixed_footprints': [FP_J, FP_J]}, 'bool timeout': {'timeout_s': True},
                              'zero timeout': {'timeout_s': 0}, 'schema': {'schema': 'agent-ee.placement-adapter/2'},
                              'extra key': {'default_placement': 'grid'}}.items():
            with self.subTest(name), self.assertRaises(ValueError):
                pa.validate_declaration(self.declaration(**changes))

    def test_bound_policy_must_be_captured_and_immutable(self):
        manifest = {'scope': {'board': BOARD, 'schematic': SCHEMATIC}, 'files': {BOARD: 'a', SCHEMATIC: 'b', POLICY: 'c'}}
        bound = pa.bind_declaration(self.declaration(), manifest, [BOARD, SCHEMATIC])
        self.assertEqual((bound['board'], bound['schematic']), (BOARD, SCHEMATIC))
        with self.assertRaisesRegex(ValueError, 'immutable'):
            pa.bind_declaration(self.declaration(), manifest, [BOARD, SCHEMATIC, POLICY])
        with self.assertRaisesRegex(ValueError, 'immutable'):
            pa.bind_declaration(self.declaration(policy='project/missing.json'), manifest, [BOARD])
        with self.assertRaisesRegex(ValueError, 'mutable design input'):
            pa.bind_declaration(self.declaration(), manifest, [SCHEMATIC])

    def test_dispatch_only_for_staged_earliest_failing_placement(self):
        staged = {'scope': {'stages': {'schema': 'agent-ee.stages/1'}}}
        state = lambda stage: {'history': [{'earliest_failing_stage': stage}]}
        self.assertTrue(pa.is_placement_iteration(state('placement'), staged))
        for stage in ('schematic', 'routing', None):
            self.assertFalse(pa.is_placement_iteration(state(stage), staged))
        self.assertFalse(pa.is_placement_iteration(state('placement'), {'scope': {}}))
        self.assertFalse(pa.is_placement_iteration({'history': []}, staged))

    def test_request_binds_exact_board_and_adds_protected_references(self):
        policy = {'schema': 'agent-ee.packing-policy/1', 'fixed_refs': ['U1']}
        request = pa.helper_request(b'board bytes', policy, ['J1', 'U1'])
        self.assertEqual(request['policy']['fixed_refs'], ['U1', 'J1'])
        self.assertEqual(policy['fixed_refs'], ['U1'])
        self.assertEqual(request['authorization'], {'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
            'source_sha256': hashlib.sha256(b'board bytes').hexdigest(), 'writable_root': '/work/placement'})

    def test_helper_outcomes_other_than_completed_are_incomplete(self):
        for status in ('failed', 'timeout', 'cancelled', 'unavailable', 'refused', 'partial'):
            with self.subTest(status), self.assertRaisesRegex(ValueError, 'no default placement substituted'):
                pa.qualify_receipt('/nonexistent', {'status': status, 'exit_code': 1}, BOARD, [])

    def test_writer_context_shows_only_sealed_placement_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            write_json(run / 'work/place/placement-probe.json', {'schema': 'agent-ee.placement-probe/1'})
            write_json(run / 'work/drc/placement-probe.json', {'schema': 'agent-ee.placement-probe/1'})
            checks = [{'id': 'place', 'kind': 'placement', 'execution': 'ok', 'outcome': 'defect', 'summary': 's',
                       'details': {'hard_failures': [1]}, 'evidence': []},
                      {'id': 'drc', 'kind': 'drc', 'execution': 'ok', 'outcome': 'defect'}]
            findings = [{'source': 'check', 'status': 'accepted', 'local_id': 'place'},
                        {'source': 'check', 'status': 'accepted', 'local_id': 'drc'},
                        {'source': 'reviewer', 'status': 'accepted', 'local_id': 'place'}]
            write_json(run / 'result.json', {'checks': checks, 'findings': findings, 'artifact_hashes': {
                'work/place/placement-probe.json': digest(run / 'work/place/placement-probe.json')}})
            manifest = {'scope': {'checks': [{'id': 'place', 'stage': 'placement'}, {'id': 'drc', 'stage': 'routing'}]}}
            context = pa.writer_context(run, manifest)
            self.assertEqual([c['id'] for c in context['checks']], ['place'])
            self.assertEqual(context['checks'][0]['probe']['schema'], 'agent-ee.placement-probe/1')
            self.assertEqual(context['findings'], findings[:1])
            self.assertEqual(context['routing_feedback']['status'], 'unavailable')
            self.assertEqual(context['authority'], 'context-only')
            write_json(run / 'work/place/placement-probe.json', {'schema': 'tampered'})
            self.assertNotIn('probe', pa.writer_context(run, manifest)['checks'][0])

    def test_proposal_scope_refuses_non_board_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, design = Path(tmp, 'source'), Path(tmp, 'design')
            for root in (source, design):
                (root / 'project').mkdir(parents=True)
                (root / BOARD).write_text(TEXT)
                (root / SCHEMATIC).write_text('(kicad_sch)')
            self.assertEqual(pa.proposal_scope(source, design, BOARD, [])['status'], 'allowed')
            (design / SCHEMATIC).write_text('(kicad_sch (changed))')
            (design / 'project/extra.txt').write_text('new')
            report = pa.proposal_scope(source, design, BOARD, [])
            self.assertEqual(report['status'], 'refused')
            self.assertIn('non-board file changed: ' + SCHEMATIC, report['violations'])
            self.assertIn('non-board file added: project/extra.txt', report['violations'])
            (design / BOARD).unlink()
            self.assertIn('board missing: ' + BOARD, pa.proposal_scope(source, design, BOARD, [])['violations'])


def owned_design(design):
    """Owned rectangular-pad board whose nets come from the native RC schematic export."""
    import pcbnew as k
    design = Path(design)
    (design / 'project').mkdir(parents=True)
    shutil.copyfile(REPO / 'fixtures/rc/control/rc_filter.kicad_sch', design / SCHEMATIC)
    exported = design.parent / 'reference-netlist.xml'
    completed = subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', str(exported),
                                str(design / SCHEMATIC)], capture_output=True, text=True, timeout=60)
    if completed.returncode:
        raise AssertionError(completed.stderr)
    xml = ET.parse(exported).getroot()
    board = k.BOARD()
    def vec(x, y):
        return k.VECTOR2I(int(x*1e6), int(y*1e6))
    for a, b in [((0, 0), (30, 0)), ((30, 0), (30, 30)), ((30, 30), (0, 30)), ((0, 30), (0, 0))]:
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
    # C1 and R1 overlap; J1 is the declared fixed connector.
    for ref, (x, y) in {'C1': (15, 15), 'J1': (4, 15), 'R1': (15.5, 15)}.items():
        fp = k.FOOTPRINT(board)
        fp.SetReference(ref)
        fp.SetValue('owned-' + ref)
        board.Add(fp)
        for layer, start, end, width in ((k.F_CrtYd, (-2, -2), (2, 2), 50000), (k.F_SilkS, (-1.5, -1.5), (1.5, -1.5), 120000)):
            shape = k.PCB_SHAPE(fp)
            shape.SetShape(k.SHAPE_T_RECT if layer == k.F_CrtYd else k.SHAPE_T_SEGMENT)
            shape.SetLayer(layer)
            shape.SetStart(vec(*start))
            shape.SetEnd(vec(*end))
            shape.SetWidth(width)
            fp.Add(shape)
        model = k.FP_3DMODEL()
        model.m_Filename = '${KICAD10_3DMODEL_DIR}/owned.wrl'
        fp.Add3DModel(model)
        for i, pin in enumerate(sorted(p for r, p in nets if r == ref)):
            pad = k.PAD(fp)
            pad.SetNumber(pin)
            pad.SetShape(k.PAD_SHAPE_RECT)
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetSize(vec(.5, .5))
            pad.SetPosition(vec(i-1, 0))
            layers = k.LSET()
            layers.AddLayer(k.F_Cu)
            pad.SetLayerSet(layers)
            pad.SetNet(nets[ref, pin])
            fp.Add(pad)
        fp.SetPosition(vec(x, y))
    k.SaveBoard(str(design / BOARD), board)
    for generated in design.rglob('*'):
        if generated.suffix in ('.kicad_pro', '.kicad_prl'):
            generated.unlink()
    write_json(design / POLICY, {'schema': 'agent-ee.packing-policy/1', 'step_budget': 100000, 'gap_nm': 500000,
                                 'fixed_refs': [], 'priority_refs': [], 'ground_nets': ['GND'], 'obstacles': []})
    return design


def uuid_of(board, ref):
    return next(k for k, node in pa.footprints(pa._board(Path(board).read_bytes())).items() if pa._reference(node) == ref)


class NativeWriteScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-placement-adapter-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.design = owned_design(self.root / 'design')
        self.board = self.design / BOARD
        self.before = self.board.read_bytes()
        self.fixed = uuid_of(self.board, 'J1')

    def edited(self, name, edit):
        import pcbnew as k
        board = k.LoadBoard(str(self.board))
        edit(k, board, {fp.GetReference(): fp for fp in board.GetFootprints()})
        path = self.root / f'{name}.kicad_pcb'
        k.SaveBoard(str(path), board)
        return path.read_bytes()

    def test_actual_pcbnew_quarter_turns_and_moves_are_exact_transforms(self):
        for turn in (90, 180, 270):
            def edit(k, board, fps, turn=turn):
                for ref in ('C1', 'R1'):
                    fps[ref].SetOrientationDegrees(turn)
                    fps[ref].SetPosition(k.VECTOR2I(fps[ref].GetPosition().x + 3_000_000, 22_000_000))
            with self.subTest(turn=turn):
                report = pa.write_scope(self.before, self.edited(f'turn{turn}', edit), [self.fixed])
                self.assertEqual(report['status'], 'allowed', report['violations'])
                self.assertEqual(sorted((t['reference'], t['rotation_deg']) for t in report['transforms']),
                                 [('C1', turn), ('R1', turn)])

    def test_actual_pcbnew_out_of_scope_edits_are_refused(self):
        def move_fixed(k, board, fps):
            fps['J1'].SetPosition(k.VECTOR2I(5_000_000, 15_000_000))
        def flip(k, board, fps):
            fps['C1'].Flip(fps['C1'].GetPosition(), False)
        def free_angle(k, board, fps):
            fps['C1'].SetOrientationDegrees(30)
        def pad_size(k, board, fps):
            next(iter(fps['R1'].Pads())).SetSize(k.VECTOR2I(600000, 500000))
        def pad_net(k, board, fps):
            pads = list(fps['R1'].Pads())
            pads[0].SetNet(pads[1].GetNet())
        def outline(k, board, fps):
            edge = next(d for d in board.GetDrawings() if d.GetLayer() == k.Edge_Cuts)
            edge.SetEnd(k.VECTOR2I(edge.GetEnd().x, edge.GetEnd().y + 1_000_000))
        def track(k, board, fps):
            segment = k.PCB_TRACK(board)
            segment.SetStart(k.VECTOR2I(1_000_000, 1_000_000))
            segment.SetEnd(k.VECTOR2I(2_000_000, 1_000_000))
            segment.SetLayer(k.F_Cu)
            board.Add(segment)
        def value(k, board, fps):
            fps['R1'].SetValue('changed')
        cases = {'fixed connector moved': (move_fixed, 'protected footprint transform changed'),
                 'side flip': (flip, 'side flips are not supported'), 'free angle': (free_angle, 'not a quarter turn'),
                 'pad size': (pad_size, 'changed beyond the footprint transform'),
                 'pad net': (pad_net, 'changed beyond the footprint transform'),
                 'outline': (outline, 'board: removed or changed gr_line'), 'copper': (track, 'board: added or changed segment'),
                 'field': (value, 'changed beyond the footprint transform')}
        for name, (edit, fragment) in cases.items():
            with self.subTest(name):
                report = pa.write_scope(self.before, self.edited(name.replace(' ', '-'), edit), [self.fixed])
                self.assertEqual(report['status'], 'refused')
                self.assertTrue(any(fragment in v for v in report['violations']), report['violations'])


class NativeHelperDriverTest(unittest.TestCase):
    """The step argv run directly by the production executor, independent of receipts."""
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-placement-driver-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.design = owned_design(self.root / 'design')
        self.source = (self.design / BOARD).read_bytes()
        self.fixed = uuid_of(self.design / BOARD, 'J1')
        self.bound = {'board': BOARD, 'schematic': SCHEMATIC, 'timeout_s': 300}

    def execute(self, name, policy=None, refs=('J1',)):
        from agent_ee.executor import execute
        policy = policy or read_json(self.design / POLICY)
        spec, request = pa.prepare_step(self.root / name / 'step', self.source, policy, self.bound, list(refs))
        work = self.root / name / 'work'
        record = execute(self.design, work, spec['argv'], timeout=300, network=False,
                         extra_ro=[(m['source'], m['at']) for m in spec['mounts']])
        return record, work, request

    def test_networkless_driver_exports_native_netlist_and_places_within_scope(self):
        record, work, request = self.execute('one')
        self.assertEqual(record['execution'], 'ok', (work / 'console.log').read_text()[-3000:])
        self.assertIn('--unshare-net', record['sandbox_argv'])
        driver = read_json(work / 'placement/driver.json')
        self.assertEqual((driver['status'], driver['netlist_export']['exit_code']), ('placed', 0))
        exported = ET.parse(work / 'placement/netlist.xml').getroot()
        self.assertEqual(sorted(c.get('ref') for c in exported.findall('components/comp')), ['C1', 'J1', 'R1'])
        result = read_json(work / 'placement/placement-result.json')
        output = (work / pa.OUTPUT).read_bytes()
        self.assertEqual((result['status'], result['source_sha256'], result['board_sha256']),
                         ('placed', hashlib.sha256(self.source).hexdigest(), hashlib.sha256(output).hexdigest()))
        self.assertEqual(request['policy']['fixed_refs'], ['J1'])
        scope = pa.write_scope(self.source, output, [self.fixed])
        self.assertEqual(scope['status'], 'allowed', scope['violations'])
        self.assertTrue(scope['transforms'])
        self.assertNotIn(self.fixed, {t['uuid'] for t in scope['transforms']})
        self.assertEqual((self.design / BOARD).read_bytes(), self.source)
        _, again, _ = self.execute('two')
        self.assertEqual((again / pa.OUTPUT).read_bytes(), output)

    def test_typed_packing_failure_and_netlist_failure_publish_no_board(self):
        policy = {**read_json(self.design / POLICY), 'step_budget': 1}
        record, work, _ = self.execute('budget', policy)
        self.assertEqual((record['execution'], record['exit_code']), ('failed', 1))
        self.assertEqual(read_json(work / 'placement/placement-result.json')['status'], 'packing_budget_exhausted')
        self.assertEqual(read_json(work / 'placement/driver.json')['status'], 'not_placed')
        self.assertFalse((work / pa.OUTPUT).exists())
        (self.design / SCHEMATIC).write_text('not a schematic')
        record, work, _ = self.execute('netlist')
        self.assertEqual(record['exit_code'], 3, (work / 'console.log').read_text()[-2000:])
        driver = read_json(work / 'placement/driver.json')
        self.assertEqual((driver['status'], driver['netlist_export']['exit_code']), ('netlist_export_failed', 3))
        self.assertFalse((work / pa.OUTPUT).exists())
        # kicad-cli exports an empty netlist from this parseable stub; the unchanged
        # placer inventory binding refuses it, so exit 0 from the export proves nothing.
        (self.design / SCHEMATIC).write_text('(kicad_sch (version 20260306))')
        record, work, _ = self.execute('empty-netlist')
        self.assertEqual(record['exit_code'], 1)
        driver = read_json(work / 'placement/driver.json')
        self.assertEqual((driver['status'], driver['netlist_export']['exit_code']), ('error', 0))
        self.assertIn('component inventory differs', driver['error'])
        self.assertFalse((work / pa.OUTPUT).exists())


def stage_receipts():
    if importlib.util.find_spec('agent_ee.stage_receipts') is None:
        reason = 'AGENTEE-95 agent_ee.stage_receipts is not integrated on this branch'
        if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
            raise AssertionError('AGENT_EE_REQUIRE_TOOLCHAIN=1: ' + reason)
        raise unittest.SkipTest(reason)
    from agent_ee import stage_receipts as module
    return module


class ReceiptIterationTest(unittest.TestCase):
    """Coordinator dispatch through the actual AGENTEE-95 stage-receipt module."""
    @classmethod
    def setUpClass(cls):
        cls.receipts = stage_receipts()
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-placement-iteration-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = owned_design(self.root / 'runs/1/inputs')
        self.work = self.root / 'implementations/1'
        self.design = self.work / 'review-work/implementation/design'
        shutil.copytree(self.source, self.design)
        self.fixed = uuid_of(self.source / BOARD, 'J1')
        files = {p.relative_to(self.source).as_posix(): digest(p) for p in self.source.rglob('*') if p.is_file()}
        self.manifest = {'candidate_id': 'c' * 64, 'files': files,
                         'scope': {'board': BOARD, 'schematic': SCHEMATIC, 'stages': {'schema': 'agent-ee.stages/1'}, 'checks': []}}
        self.state = {'history': [{'earliest_failing_stage': 'placement'}], 'mutable_inputs': [BOARD, SCHEMATIC],
                      'stage_receipts': {}}
        self.declaration = {'schema': pa.ADAPTER_SCHEMA, 'helper': 'agent-ee.placer/1', 'policy': POLICY,
                            'fixed_footprints': [self.fixed], 'timeout_s': 300}

    def iterate(self, declaration=None, **kwargs):
        import time
        return pa.run_placement_iteration(self.work, self.design, self.source, self.manifest, self.state,
                                          declaration or self.declaration, time.monotonic() + 600,
                                          receipts=self.receipts, **kwargs)

    def assertUnadopted(self):
        self.assertEqual((self.design / BOARD).read_bytes(), (self.source / BOARD).read_bytes())

    def test_qualified_helper_proposal_is_adopted_anchored_and_reverifiable(self):
        record = self.iterate()
        self.assertEqual(record['status'], 'proposed')
        self.assertEqual(record['helper']['status'], 'completed')
        self.assertEqual(record['protected'], [self.fixed])
        self.assertEqual(record['proposal_scope']['status'], 'allowed')
        self.assertNotIn(self.fixed, {t['uuid'] for t in record['proposal_scope']['transforms']})
        receipt = self.receipts.validate_receipt(self.work / record['helper']['receipt'])
        self.assertEqual(receipt['outputs'][BOARD], digest(self.design / BOARD))
        self.assertNotEqual(receipt['inputs'][BOARD], receipt['outputs'][BOARD])
        self.assertEqual(receipt['tool']['reported_version']['status'], 'reported')
        self.assertIsNotNone(receipt['tool']['source_tree']['sha256'])
        index = read_json(self.work / 'stage-receipts.json')
        self.assertEqual(self.state['stage_receipts']['1'], digest(self.work / 'stage-receipts.json'))
        self.assertEqual([r['applicability'] for r in index['receipts']], ['applicable'])
        self.assertEqual(read_json(self.work / 'placement-adapter.json'), record)
        captured = self.root / 'runs/2/inputs'
        shutil.copytree(self.design, captured)
        args = (self.work, self.source, captured, self.manifest, self.declaration, self.state['mutable_inputs'])
        self.assertEqual(pa.verify_record(*args, receipts=self.receipts), record)
        request = self.work / 'placement/1/request/request.json'
        original = request.read_bytes()
        write_json(request, {**read_json(request), 'policy': {**read_json(request)['policy'], 'fixed_refs': []}})
        with self.assertRaisesRegex(ValueError, 'request differs from the coordinator derivation'):
            pa.verify_record(*args, receipts=self.receipts)
        request.write_bytes(original)
        (captured / SCHEMATIC).write_text('(kicad_sch (changed))')
        with self.assertRaisesRegex(ValueError, 'differs from retained receipts or captured proposal'):
            pa.verify_record(*args, receipts=self.receipts)

    def test_helper_failure_leaves_iteration_incomplete_without_substitution(self):
        write_json(self.source / POLICY, {**read_json(self.source / POLICY), 'step_budget': 1})
        shutil.copyfile(self.source / POLICY, self.design / POLICY)
        with self.assertRaisesRegex(ValueError, 'placement helper failed .*no default placement substituted'):
            self.iterate()
        self.assertUnadopted()
        record = read_json(self.work / 'placement-adapter.json')
        self.assertEqual((record['status'], record['helper']['status']), ('incomplete', 'failed'))
        self.assertIsNone(record['proposal_scope'])
        index = read_json(self.work / 'stage-receipts.json')
        self.assertEqual(self.state['stage_receipts']['1'], digest(self.work / 'stage-receipts.json'))
        self.assertEqual(index['receipts'][0]['applicability'], 'not_applicable')
        retained = self.work / record['helper']['receipt'] / 'run/placement/placement-result.json'
        self.assertEqual(read_json(retained)['status'], 'packing_budget_exhausted')

    def test_independent_scope_proof_refuses_helper_moving_a_protected_part(self):
        # A coordinator request that omitted the fixed connector lets the real helper move it.
        board = (self.design / BOARD).read_bytes()
        bound = {'board': BOARD, 'schematic': SCHEMATIC, 'timeout_s': 300}
        policy = {**read_json(self.source / POLICY), 'priority_refs': ['J1']}
        spec, _ = pa.prepare_step(self.work / 'placement/1', board, policy, bound, [])
        receipt_dir = self.work / 'receipts/1-agent-ee-placer'
        receipt = self.receipts.run_step(receipt_dir, self.design, spec, {'implementation': 1})
        self.assertEqual(receipt['status'], 'completed')
        scope = pa.write_scope(board, (receipt_dir / 'run' / pa.OUTPUT).read_bytes())
        self.assertIn(self.fixed, {t['uuid'] for t in scope['transforms']})
        with self.assertRaisesRegex(ValueError, 'outside placement scope.*protected footprint transform changed'):
            pa.qualify_receipt(receipt_dir, receipt, BOARD, [self.fixed])
        self.assertUnadopted()

    def test_writer_only_iteration_refuses_out_of_scope_writer_edit(self):
        declaration = {**self.declaration, 'helper': None, 'policy': None}
        (self.design / SCHEMATIC).write_bytes((self.design / SCHEMATIC).read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'changed more than footprint transforms.*' + SCHEMATIC):
            self.iterate(declaration)
        record = read_json(self.work / 'placement-adapter.json')
        self.assertEqual((record['status'], record['helper']), ('refused', None))
        self.assertFalse((self.work / 'receipts').exists())
        self.assertEqual(self.state['stage_receipts'], {})

    def test_cancelled_helper_is_retained_and_never_adopted(self):
        from agent_ee.executor import Cancellation
        cancellation = Cancellation()
        timer = threading.Timer(1.0, cancellation.cancel)
        timer.start()
        self.addCleanup(timer.cancel)
        with self.assertRaises(KeyboardInterrupt):
            self.iterate(cancellation=cancellation)
        self.assertUnadopted()
        record = read_json(self.work / 'placement-adapter.json')
        self.assertEqual(record['status'], 'cancelled')
        receipt = self.receipts.validate_receipt(self.work / record['helper']['receipt'])
        self.assertEqual(receipt['status'], 'cancelled')
        self.assertIn('1', self.state['stage_receipts'])

    def test_legacy_state_other_stage_and_exhausted_deadline_run_no_helper(self):
        legacy = {k: v for k, v in self.state.items() if k != 'stage_receipts'}
        import time
        routing = {**self.state, 'history': [{'earliest_failing_stage': 'routing'}]}
        with self.assertRaisesRegex(ValueError, 'only when the earliest failing stage is placement'):
            pa.run_placement_iteration(self.work, self.design, self.source, self.manifest, routing,
                                       self.declaration, time.monotonic() + 600, receipts=self.receipts)
        self.assertFalse((self.work / 'placement-adapter.json').exists())
        with self.assertRaisesRegex(ValueError, 'predates stage receipts'):
            pa.run_placement_iteration(self.work, self.design, self.source, self.manifest, legacy,
                                       self.declaration, time.monotonic() + 600, receipts=self.receipts)
        self.assertFalse((self.work / 'receipts').exists())
        with self.assertRaisesRegex(ValueError, 'deadline exhausted'):
            pa.run_placement_iteration(self.work, self.design, self.source, self.manifest, self.state,
                                       self.declaration, time.monotonic() - 1, receipts=self.receipts)
        self.assertUnadopted()
        self.assertEqual(read_json(self.work / 'stage-receipts.json')['receipts'], [])


if __name__ == '__main__':
    unittest.main()

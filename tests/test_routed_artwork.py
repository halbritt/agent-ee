"""Native routed-board silk repair, restricted geometry and receipt boundaries."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_ee import routed_artwork as art, stage_receipts
from agent_ee.silk_artwork import DECLARATION
from agent_ee.storage import read_json
from agent_ee.symbol_library import children, parse_sexpr
import test_silk_artwork as silk_fixture
from toolchain import fome_mc_store, require_toolchain


def authored_free_graphics(board):
    """Explicit singleton classification for these authored free-graphic fixtures.

    This test helper is never used for imported designs or body outlines.
    """
    root = parse_sexpr(board)
    groups = []
    for owner, parent in [(None, root), *[(children(fp, 'uuid')[0][1], fp)
                                         for fp in children(root, 'footprint')]]:
        for item in parent[1:]:
            if (isinstance(item, list) and item and item[0] in art.GRAPHICS and
                    children(item, 'layer') in ([['layer', 'F.SilkS']], [['layer', 'B.SilkS']])):
                groups.append(dict(owner_uuid=owner, members=[children(item, 'uuid')[0][1]]))
    return dict(schema=art.ARTWORK_POLICY, source_sha256=hashlib.sha256(board).hexdigest(), free_groups=groups)


class RoutedArtworkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='ee350-')))

    def board(self, *, back=False, graphic=False, footprint=False):
        import pcbnew as k
        helper = silk_fixture.SilkArtworkTest()
        helper.root = self.root
        source = helper.board(back=back)
        path = helper.output(source)
        board = k.LoadBoard(str(path))
        track = k.PCB_TRACK(board)
        track.SetStart(k.VECTOR2I(2000000, 2000000))
        track.SetEnd(k.VECTOR2I(5000000, 2000000))
        track.SetWidth(250000)
        track.SetLayer(k.F_Cu)
        board.Add(track)
        via = k.PCB_VIA(board)
        via.SetPosition(k.VECTOR2I(5000000, 2000000))
        via.SetWidth(600000)
        via.SetDrill(300000)
        via.SetLayerPair(k.F_Cu, k.B_Cu)
        board.Add(via)
        zone = k.ZONE(board)
        zone.SetLayer(k.B_Cu)
        zone.SetIsRuleArea(True)
        zone.SetDoNotAllowTracks(True)
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in ((20, 20), (22, 20), (22, 22), (20, 22)):
            outline.Append(x * 1000000, y * 1000000)
        board.Add(zone)
        if graphic:
            fp = next(iter(board.GetFootprints()))
            for f in board.GetFootprints():
                f.Reference().SetVisible(False)
            owner = fp if footprint else board
            shape = k.PCB_SHAPE(owner)
            shape.SetShape(k.SHAPE_T_SEGMENT)
            shape.SetLayer(k.B_SilkS if back else k.F_SilkS)
            shape.SetWidth(150000)
            pad = next(iter(fp.Pads())).GetPosition()
            shape.SetStart(k.VECTOR2I(pad.x - 500000, pad.y))
            shape.SetEnd(k.VECTOR2I(pad.x + 500000, pad.y))
            owner.Add(shape)
        k.SaveBoard(str(path), board, True)
        return path

    def test_front_and_back_fields_on_routed_board_preserve_all_other_tokens(self):
        for back in (False, True):
            with self.subTest(back=back), tempfile.TemporaryDirectory(dir=self.root) as d:
                prior = self.root
                self.root = Path(d)
                try:
                    source = self.board(back=back)
                    before = source.read_bytes()
                    report = art.produce(source, self.root / 'run')
                    output = self.root / 'run' / art.BOARD
                    self.assertFalse(report['open_defects'])
                    proof = art.write_scope(before, output.read_bytes())
                    self.assertTrue(proof['moved_identities'])
                    for tag in ('segment', 'via', 'zone', 'setup'):
                        self.assertEqual(children(parse_sexpr(before), tag),
                                         children(parse_sexpr(output.read_bytes()), tag))
                    self.assertTrue(children(parse_sexpr(before), 'segment'))
                    self.assertTrue(children(parse_sexpr(before), 'via'))
                    self.assertTrue(children(parse_sexpr(before), 'zone'))
                finally:
                    self.root = prior

    def test_native_producer_closes_carry_with_internal_slots(self):
        import pcbnew as k
        from agent_ee import placement_defects as pd
        from agent_ee.placer_runner import _native_drc
        for footprint_owned in (False, True):
            with self.subTest(footprint_owned=footprint_owned), tempfile.TemporaryDirectory(dir=self.root) as d:
                prior, self.root = self.root, Path(d)
                try:
                    source = self.board()
                    board = k.LoadBoard(str(source))
                    owner = next(iter(board.GetFootprints())) if footprint_owned else board
                    slot = k.PCB_SHAPE(owner)
                    slot.SetShape(k.SHAPE_T_RECT)
                    slot.SetLayer(k.Edge_Cuts)
                    slot.SetStart(k.VECTOR2I(1000000, 12000000))
                    slot.SetEnd(k.VECTOR2I(2000000, 17000000))
                    slot.SetWidth(50000)
                    owner.Add(slot)
                    k.SaveBoard(str(source), board, True)
                    baseline = self.root / 'baseline'
                    baseline.mkdir()
                    native = _native_drc(source, baseline)
                    defects = [dict(type='placement_silkscreen_conflict', status='open',
                                    verdict='defect', native=v) for v in native['violations']
                               if v['type'] in ('silk_overlap', 'silk_over_copper')]
                    self.assertTrue(defects)
                    original = pd._identities(source)
                    report = art.produce(source, self.root / 'run', artwork_policy=authored_free_graphics(source.read_bytes()))
                    self.assertFalse(report['open_defects'])
                    current = pd._identities(self.root / 'run' / art.BOARD, report['containment'])
                    for defect in defects:
                        self.assertTrue(pd._closes(defect, original, current, set(), set(), True))
                finally:
                    self.root = prior

    def test_native_producer_moves_carried_labels_around_slot_and_round_hole(self):
        import pcbnew as k
        from agent_ee import placement_defects as pd
        from agent_ee.placer_runner import _native_drc
        from agent_ee.silk_artwork import relocate
        source = self.board()
        probe = self.root / 'probe'
        probe.mkdir()
        free = {m['reference']: m['to_nm'] for m in relocate(
            k.LoadBoard(str(source)), _native_drc(source, probe)['violations'])['moved_fields']}
        # Both labels drop straight below their pads without cutouts.
        self.assertEqual(free, {'U1': [3000000, 5250000], 'C1': [15000000, 5250000]})
        board = k.LoadBoard(str(source))
        slot = k.PCB_SHAPE(board)
        slot.SetShape(k.SHAPE_T_RECT)
        slot.SetLayer(k.Edge_Cuts)
        slot.SetStart(k.VECTOR2I(2500000, 5000000))
        slot.SetEnd(k.VECTOR2I(3500000, 7000000))
        slot.SetWidth(50000)
        board.Add(slot)
        hole = k.PCB_SHAPE(board)
        hole.SetShape(k.SHAPE_T_CIRCLE)
        hole.SetLayer(k.Edge_Cuts)
        hole.SetCenter(k.VECTOR2I(15000000, 6000000))
        hole.SetEnd(k.VECTOR2I(15900000, 6000000))
        hole.SetWidth(50000)
        board.Add(hole)
        k.SaveBoard(str(source), board, True)
        baseline = self.root / 'baseline'
        baseline.mkdir()
        defects = [dict(type='placement_silkscreen_conflict', status='open', verdict='defect', native=v)
                   for v in _native_drc(source, baseline)['violations']
                   if v['type'] in ('silk_overlap', 'silk_over_copper')]
        self.assertEqual(len(defects), 2)
        original = pd._identities(source)
        report = art.produce(source, self.root / 'run')
        self.assertFalse(report['open_defects'])
        artwork = read_json(self.root / 'run' / art.ARTWORK)
        self.assertEqual(artwork['search_diagnostics'], [])
        moved = {m['reference']: m['to_nm'] for m in artwork['moved_fields']}
        self.assertEqual(set(moved), {'U1', 'C1'})
        output = k.LoadBoard(str(self.root / 'run' / art.BOARD))
        for fp in output.GetFootprints():
            ref = fp.GetReference()
            self.assertNotEqual(moved[ref], free[ref])
            box = fp.Reference().GetBoundingBox()
            if ref == 'U1':
                self.assertTrue(box.GetRight() < 2500000 or box.GetLeft() > 3500000 or
                                box.GetBottom() < 5000000 or box.GetTop() > 7000000)
            else:
                # Nearest box point to the true circle stays outside its radius.
                nx = min(max(15000000, box.GetLeft()), box.GetRight())
                ny = min(max(6000000, box.GetTop()), box.GetBottom())
                self.assertGreater((nx - 15000000)**2 + (ny - 6000000)**2, 900000**2)
        current = pd._identities(self.root / 'run' / art.BOARD, report['containment'])
        for defect in defects:
            self.assertTrue(pd._closes(defect, original, current, set(), set(), True))

    def test_visible_value_fields_are_relocated_on_routed_board(self):
        import pcbnew as k
        source = self.board()
        board = k.LoadBoard(str(source))
        for fp in board.GetFootprints():
            fp.Reference().SetVisible(False)
            value = fp.Value()
            value.SetVisible(True)
            value.SetText('owned')
            value.SetLayer(k.F_SilkS)
            value.SetTextSize(k.VECTOR2I(1000000, 1000000))
            value.SetTextThickness(150000)
            value.SetPosition(next(iter(fp.Pads())).GetPosition())
        k.SaveBoard(str(source), board, True)
        report = art.produce(source, self.root / 'run')
        self.assertFalse(report['open_defects'])
        moved = read_json(self.root / 'run' / art.ARTWORK)['moved_fields']
        self.assertTrue(moved)
        self.assertEqual({m['field'] for m in moved}, {'Value'})

    def test_unchanged_copper_proof_alone_keeps_an_unmeasured_short_as_baseline(self):
        import pcbnew as k
        from agent_ee.placer_runner import CollisionMeasurementRefusal, _drc_comparison
        source = self.board()
        board = k.LoadBoard(str(source))
        # Overlapping 2 mm pads on distinct nets: a pre-existing, unmeasured native short.
        for fp in board.GetFootprints():
            net = k.NETINFO_ITEM(board, fp.GetReference()); board.Add(net)
            for pad in fp.Pads():
                pad.SetNet(net)
            if fp.GetReference() == 'C1':
                fp.SetPosition(k.VECTOR2I(4500000, 3000000))
        k.SaveBoard(str(source), board, True)
        report = art.produce(source, self.root / 'run')
        comparison = report['native_drc_comparison']
        self.assertTrue(report['write_scope']['moved_identities'])
        self.assertIn('shorting_items', [v['type'] for v in comparison['candidate_findings']], comparison)
        self.assertEqual(comparison['blocking_findings'], [])
        output = self.root / 'run' / art.BOARD
        reports = [read_json(output.parent / 'baseline' / 'native-drc.json'), read_json(output.parent / 'native-drc.json')]
        with self.assertRaisesRegex(CollisionMeasurementRefusal, 'native saved geometry required'):
            _drc_comparison(*reports)

    def test_board_and_footprint_graphics_translate_without_reshaping(self):
        for footprint in (False, True):
            with self.subTest(footprint=footprint), tempfile.TemporaryDirectory(dir=self.root) as d:
                prior = self.root
                self.root = Path(d)
                try:
                    source = self.board(graphic=True, footprint=footprint)
                    report = art.produce(source, self.root / 'run', artwork_policy=authored_free_graphics(source.read_bytes()))
                    self.assertFalse(report['open_defects'])
                    artwork = read_json(self.root / 'run' / art.ARTWORK)
                    self.assertTrue(artwork['moved_graphics'])
                    self.assertTrue(report['write_scope']['moved_identities'])
                finally:
                    self.root = prior

    def test_scope_refuses_non_silk_edits_and_marking_damage(self):
        from test_placement_adapter import TEXT
        source = TEXT.encode()
        changes = [
            ('(at 10 10)', '(at 11 10)'),
            ('(size 0.5 0.5)', '(size 0.6 0.5)'),
            ('(net "/IN")', '(net "/OUT")'),
            ('(at 0 -1.5 0)', '(at 0 -1.5 90)'),
            ('"Reference" "R1"', '"Reference" "R2"'),
            ('(layer "F.SilkS")', '(layer "F.Fab")'),
            ('(size 1 1)', '(size 0.9 1)'),
            ('(uuid "a1")', '(uuid "new")'),
            ('(effects (font (size 1 1)))', '(hide yes) (effects (font (size 1 1)))'),
        ]
        for old, new in changes:
            with self.subTest(old=old), self.assertRaises(ValueError):
                art.write_scope(source, TEXT.replace(old, new, 1).encode())
        with self.assertRaises(ValueError):
            art.write_scope(b'(not_board)', b'(not_board)')
        with self.assertRaises(ValueError):
            art.write_scope(source, b'(')

    def test_graphic_scope_refuses_reshape_layer_and_unknown_attribute_changes(self):
        b = b'(kicad_pcb (gr_line (start 1 2) (end 3 2) (layer "F.SilkS") (uuid "s")))'
        self.assertEqual(art.write_scope(b, b.replace(b'1 2', b'2 3').replace(b'3 2', b'4 3'), artwork_policy=authored_free_graphics(b))['status'], 'allowed')
        for a in (b.replace(b'3 2', b'4 2'), b.replace(b'F.SilkS', b'F.Mask'),
                  b.replace(b'(uuid', b'(unknown yes) (uuid')):
            with self.subTest(a=a), self.assertRaises(ValueError):
                art.write_scope(b, a)

    def test_unknown_nested_coordinates_are_not_translation_authority(self):
        before = (b'(kicad_pcb (gr_line (start 1 2) (end 3 2) '
                  b'(unknown (xy 1 2)) (layer "F.SilkS") (uuid "s")))')
        # Moving an unknown xy with the line must not be normalized away.
        after = before.replace(b'1 2', b'2 3').replace(b'3 2', b'4 3')
        with self.assertRaises(ValueError):
            art.write_scope(before, after)

    def test_list_headed_and_nested_silk_tokens_refuse_typed(self):
        line = b'(gr_line (start 1 2) (end 3 2) (layer "F.SilkS") (uuid "s"))'
        field = (b'(footprint "x" (property "Reference" "R1" (at 1 2) (layer "F.SilkS") (uuid "f")) '
                 b'(uuid "x"))')
        cases = {
            'list-headed graphic': (b'(kicad_pcb ' + line.replace(b'(gr_line', b'((gr_line)') + b')',
                                    b'(kicad_pcb ' + line.replace(b'(gr_line', b'((gr_line)').replace(
                                        b'1 2', b'2 2') + b')', 'list-headed node'),
            'list-headed graphic child': (b'(kicad_pcb ' + line.replace(b'(uuid', b'((x) 1) (uuid') + b')',
                                          b'(kicad_pcb ' + line.replace(b'(uuid', b'((x) 1) (uuid').replace(
                                              b'1 2', b'2 2').replace(b'3 2', b'4 2') + b')',
                                          'list-headed child'),
            'nested field name': (b'(kicad_pcb ' + field.replace(b'"Reference"', b'("Reference")') + b')',
                                  b'(kicad_pcb ' + field.replace(b'"Reference"', b'("Reference")').replace(
                                      b'(at 1 2)', b'(at 2 2)') + b')', 'non-atom field name'),
            'nested uuid': (b'(kicad_pcb ' + line.replace(b'"s"', b'("s")') + b')',
                            b'(kicad_pcb ' + line.replace(b'"s"', b'("s")') + b')', 'ambiguous artwork identity'),
            'nested declaration': (b'(kicad_pcb ' + field.replace(
                                       b'(uuid "x")', b'(property "' + DECLARATION.encode() + b'" ("[]")) (uuid "x")') + b')',
                                   b'(kicad_pcb ' + field.replace(
                                       b'(uuid "x")', b'(property "' + DECLARATION.encode() + b'" ("[]")) (uuid "x")').replace(
                                       b'(at 1 2)', b'(at 2 2)') + b')', 'malformed movable silk declaration'),
            'bare declaration': (b'(kicad_pcb ' + field.replace(
                                     b'(uuid "x")', b'(property "' + DECLARATION.encode() + b'") (uuid "x")') + b')',
                                 b'(kicad_pcb ' + field.replace(
                                     b'(uuid "x")', b'(property "' + DECLARATION.encode() + b'") (uuid "x")').replace(
                                     b'(at 1 2)', b'(at 2 2)') + b')', 'malformed movable silk declaration'),
        }
        for name, (before, after, message) in cases.items():
            with self.subTest(case=name), self.assertRaisesRegex(ValueError, message):
                art.write_scope(before, after)
        # The atom-only spelling of the same field move remains allowed.
        allowed = art.write_scope(b'(kicad_pcb ' + field + b')',
                                  b'(kicad_pcb ' + field.replace(b'(at 1 2)', b'(at 2 2)') + b')')
        self.assertEqual(allowed['moved_identities'], ['f'])

    def test_empty_child_and_nameless_property_refuse_in_both_orders(self):
        # The base crashed on both shapes; a typed refusal must not become a skip.
        line = (b'(gr_line (start 1 2) (end 3 2) (layer "F.SilkS") '
                b'(uuid "11111111-1111-4111-8111-111111111111"))')
        field = (b'(footprint "x" (property "Reference" "R1" (at 1 2) (layer "F.SilkS") (uuid "f")) '
                 b'(uuid "x"))')
        def moved(board):
            return board.replace(b'(start 1 2)', b'(start 2 2)').replace(
                b'(end 3 2)', b'(end 4 2)').replace(b'(at 1 2)', b'(at 2 2)')
        cases = {
            'empty graphic child': (b'(kicad_pcb ' + line.replace(b'))', b') ())') + b')',
                                    'empty child', line, ['11111111-1111-4111-8111-111111111111']),
            'nameless property': (b'(kicad_pcb ' + field.replace(b'(uuid "x")', b'(property) (uuid "x")') + b')',
                                  'nameless property', field, ['f']),
        }
        for name, (before, message, control, identities) in cases.items():
            for left, right in ((before, moved(before)), (moved(before), before)):
                with self.subTest(case=name, order=left is before), \
                        self.assertRaisesRegex(ValueError, message):
                    art.write_scope(left, right)
            # The well-formed spelling of the same translation stays allowed.
            control = b'(kicad_pcb ' + control + b')'
            allowed = art.write_scope(control, moved(control), artwork_policy=authored_free_graphics(control))
            self.assertEqual(allowed['status'], 'allowed')
            self.assertEqual(allowed['moved_identities'], identities)

    def test_stale_render_cache_refuses_text_or_field_translation(self):
        nodes = (
            '(gr_text "mark" (at 1 2) (layer "F.SilkS") (uuid "mark") '
            '(render_cache "mark" 0 (polygon (pts (xy 1 2) (xy 2 2) (xy 1 3)))))',
            '(footprint "owned" (property "Reference" "U1" (at 1 2) '
            '(layer "F.SilkS") (uuid "mark") (render_cache "U1" 0)))',
        )
        for node in nodes:
            before = ('(kicad_pcb ' + node + ')').encode()
            after = before.replace(b'(at 1 2)', b'(at 2 3)')
            with self.subTest(node=node), self.assertRaisesRegex(ValueError, 'render_cache'):
                art.write_scope(before, after)

    def test_scope_refuses_backslashes_even_when_parser_erases_them(self):
        before = b'(kicad_pcb (gr_text "R1" (at 1 2) (layer "F.SilkS") (uuid "mark")))'
        for escaped in (b'R\\1', b'R\\01'):
            after = before.replace(b'R1', escaped)
            with self.subTest(escaped=escaped), self.assertRaisesRegex(ValueError, 'backslash'):
                art.write_scope(before, after)
            with self.assertRaisesRegex(ValueError, 'backslash'):
                art.write_scope(after, after)

    def test_carried_markings_must_remain_wholly_inside_outline(self):
        import pcbnew as k
        from agent_ee import placement_defects as pd
        from agent_ee.placer_runner import _native_drc
        for case in ('inside_control', 'footprint_cutout', 'board_cutout',
                     'offboard_all', 'offboard_one', 'edge_straddle'):
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=self.root) as d:
                prior, self.root = self.root, Path(d)
                try:
                    source = self.board()
                    baseline = self.root / 'baseline-drc'
                    baseline.mkdir()
                    drc = _native_drc(source, baseline)
                    defects = [dict(type='placement_silkscreen_conflict', status='open',
                                    verdict='defect', native=v) for v in drc['violations']
                               if v['type'] in ('silk_overlap', 'silk_over_copper')]
                    self.assertTrue(defects)
                    original = pd._identities(source)
                    board = k.LoadBoard(str(source))
                    fields = sorted(board.GetFootprints(), key=lambda f: f.GetReference())
                    moved = fields if case == 'offboard_all' else fields[:1]
                    bad = {fp.Reference().m_Uuid.AsString() for fp in moved}
                    for fp in moved:
                        position = (k.VECTOR2I(15000000, 12000000) if case in
                                    ('inside_control', 'footprint_cutout', 'board_cutout') else
                                    k.VECTOR2I(-300000, 3000000) if case == 'edge_straddle' else
                                    k.VECTOR2I(-80000000, -80000000))
                        fp.Reference().SetPosition(position)
                        if case in ('footprint_cutout', 'board_cutout'):
                            # Original reviewer probe: surround a moved reference
                            # with a slot owned by its footprint (or the board).
                            owner = fp if case == 'footprint_cutout' else board
                            box = fp.Reference().GetBoundingBox()
                            box.Inflate(400000)
                            points = [(box.GetLeft(), box.GetTop()), (box.GetRight(), box.GetTop()),
                                      (box.GetRight(), box.GetBottom()), (box.GetLeft(), box.GetBottom())]
                            for a, b in zip(points, points[1:] + points[:1]):
                                edge = k.PCB_SHAPE(owner)
                                edge.SetShape(k.SHAPE_T_SEGMENT)
                                edge.SetLayer(k.Edge_Cuts)
                                edge.SetWidth(50000)
                                edge.SetStart(k.VECTOR2I(*a))
                                edge.SetEnd(k.VECTOR2I(*b))
                                owner.Add(edge)
                    k.SaveBoard(str(source), board, True)
                    # Inspect the deliberately bad saved state directly. The final
                    # artwork producer now repairs edge conflicts as well.
                    from agent_ee.silk_artwork import containment_witness
                    current = pd._identities(source, containment_witness(source))
                    # Even a silent DRC cannot establish on-board containment.
                    affected = [d for d in defects if pd._items(d) & bad]
                    self.assertTrue(affected)
                    for graphic_translation in (False, True):
                        for defect in affected:
                            closes = pd._closes(defect, original, current, set(), set(), True,
                                                graphic_translation=graphic_translation)
                            self.assertEqual(closes, case == 'inside_control')
                finally:
                    self.root = prior

    def test_native_receipt_is_bound_to_source_and_saved_geometry(self):
        from agent_ee.cli import policy_files
        source = self.board(graphic=True)
        design = self.root / 'design'
        design.mkdir()
        (design / 'board.kicad_pcb').write_bytes(source.read_bytes())
        spec = art.step(self.root / 'tool', 'board.kicad_pcb', artwork_policy=authored_free_graphics(source.read_bytes()))
        receipt_dir = self.root / 'receipt'
        receipt = stage_receipts.run_step(receipt_dir, design, spec, {'implementation': 1})
        self.assertEqual(receipt['status'], 'completed', read_json(receipt_dir / 'run/execution.json'))
        self.assertEqual(receipt['violations'], [])
        proof = art.qualify_receipt(receipt_dir, receipt, 'board.kicad_pcb', policy_files())
        self.assertTrue(proof['moved_identities'])
        for mutation in ('pin', 'argv', 'output', 'status'):
            changed = copy.deepcopy(receipt)
            pins = policy_files()
            if mutation == 'pin':
                pins['routed_artwork.py'] = '0' * 64
            elif mutation == 'argv':
                changed['spec']['argv'] = ['/usr/bin/true']
            elif mutation == 'output':
                changed['outputs']['board.kicad_pcb'] = '0' * 64
            else:
                changed['status'] = 'failed'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                art.qualify_receipt(receipt_dir, changed, 'board.kicad_pcb', pins)
        (receipt_dir / 'run' / art.BOARD).unlink()
        with self.assertRaises(OSError):
            art.qualify_receipt(receipt_dir, receipt, 'board.kicad_pcb', policy_files())

    def test_host_sandbox_is_nonroot_and_source_mounts_are_read_only(self):
        from agent_ee.executor import execute
        inputs = self.root / 'inputs'
        inputs.mkdir()
        (inputs / 'subject').write_text('immutable')
        probe = ('import os; assert os.geteuid() != 0; '
                 'assert not os.access("/candidate/subject", os.W_OK); '
                 'assert not os.access("/app/agent_ee/routed_artwork.py", os.W_OK); '
                 'print("nonroot; candidate and package read-only")')
        record = execute(inputs, self.root / 'probe', ['/usr/bin/python3', '-c', probe], timeout=30)
        self.assertEqual(record['execution'], 'ok', record)
        self.assertIn('nonroot; candidate and package read-only',
                      (self.root / 'probe/stdout.log').read_text())

    def test_source_rules_preserved_and_ignored_coverage_refuses_publication(self):
        import json
        source = self.board()
        payload = b'{"board":{"design_settings":{"rule_severities":{"silk_overlap":"warning"}}}}\n'
        source.with_suffix('.kicad_pro').write_bytes(payload)
        source.with_suffix('.kicad_dru').write_bytes(b'(version 1)\n')
        art.produce(source, self.root / 'run')
        for suffix in ('.kicad_pro', '.kicad_dru'):
            self.assertEqual(source.with_suffix(suffix).read_bytes(),
                             (self.root / 'run' / art.BOARD).with_suffix(suffix).read_bytes())
        source.with_suffix('.kicad_pro').write_text(json.dumps({'board': {'design_settings': {
            'rule_severities': {'silk_overlap': 'ignore'}}}}))
        with self.assertRaisesRegex(ValueError, 'explicitly ignored'):
            art.produce(source, self.root / 'ignored')
        self.assertFalse((self.root / 'ignored' / art.REPORT).exists())

    def test_exhausted_graphic_search_retains_native_defect_and_identity(self):
        import pcbnew as k
        source = self.board(graphic=True)
        board = k.LoadBoard(str(source))
        graphic = next(s for s in board.GetDrawings() if s.GetLayer() == k.F_SilkS)
        uid = graphic.m_Uuid.AsString()
        graphic.SetStart(k.VECTOR2I(-100000000, graphic.GetStart().y))
        graphic.SetEnd(k.VECTOR2I(100000000, graphic.GetEnd().y))
        k.SaveBoard(str(source), board, True)
        report = art.produce(source, self.root / 'run', artwork_policy=authored_free_graphics(source.read_bytes()))
        self.assertTrue(report['open_defects'])
        self.assertTrue(any(uid == i['uuid'] for d in report['open_defects'] for i in d['native']['items']))
        artwork = read_json(self.root / 'run' / art.ARTWORK)
        self.assertIn({'uuid': uid, 'reference': '', 'reason': 'bounded_search_exhausted'},
                      artwork['search_diagnostics'])
        self.assertNotIn(uid, report['write_scope']['moved_identities'])

    # Agent-authored stand-ins for the pod's escaped JSON properties (AGENTEE-434).
    POD_PROPERTIES = {
        'AGENT_EE_Ratings': '{"record_sha256":"' + '0' * 64 + '","voltage_v":"50","path":"C:\\\\lib\\\\x"}',
        'PARAM_Datasheet': '{"url":"https://example.invalid/d.pdf","note":"line1\nline2\r"}',
    }

    def pod_board(self, *, graphic=False):
        import pcbnew as k
        source = self.board(graphic=graphic)
        board = k.LoadBoard(str(source))
        for fp in board.GetFootprints():
            for name, text in self.POD_PROPERTIES.items():
                field = k.PCB_FIELD(fp, k.FIELD_T_USER, name)
                field.SetText(text)
                field.SetLayer(k.F_Fab)
                field.SetVisible(False)
                fp.Add(field)
        k.SaveBoard(str(source), board, True)
        # A pod board is KiCad's saved fixed point: new fields gain defaults on resave.
        k.SaveBoard(str(source), k.LoadBoard(str(source)), True)
        return source

    def test_pod_board_with_escaped_json_properties_attests_unchanged(self):
        source = self.pod_board()
        before = source.read_bytes()
        # KiCad writes the JSON with \" and \\ escapes (and \n/\r for line breaks).
        for escape in (b'\\"', b'\\\\', b'\\n', b'\\r'):
            self.assertIn(escape, before)
        self.assertEqual(art.write_scope(before, before)['moved_identities'], [])
        report = art.produce(source, self.root / 'run', artwork_policy=authored_free_graphics(source.read_bytes()))
        self.assertFalse(report['open_defects'])
        output = (self.root / 'run' / art.BOARD).read_bytes()
        proof = art.write_scope(before, output, artwork_policy=authored_free_graphics(before))
        self.assertEqual(proof, report['write_scope'])
        self.assertTrue(proof['moved_identities'])
        # The escaped properties keep their exact saved spelling.
        def spelled(data):
            return sorted(p for p in children(parse_sexpr(data, raw=True), 'footprint')
                          for p in children(p, 'property') if p[1] != '"Reference"' and '\\' in p[2])
        self.assertEqual(spelled(before), spelled(output))
        self.assertEqual(len(spelled(before)), 2 * len(children(parse_sexpr(before), 'footprint')))

    def test_backslash_and_spelling_ambiguity_refuses(self):
        field = (b'(footprint "x" (property "Reference" "R1" (at 1 2) (layer "F.SilkS") (uuid "f")) '
                 b'(property "AGENT_EE_Ratings" "{\\"v\\":\\"a\\\\\\\\b\\",\\"n\\":\\"x\\ny\\r\\"}" '
                 b'(at 0 0) (layer "F.Fab") (hide yes) (uuid "g")) (uuid "x"))')
        board = b'(kicad_pcb ' + field + b')'
        moved = board.replace(b'(at 1 2)', b'(at 2 2)')
        # Positive control: writer escapes on a moved board stay allowed.
        self.assertEqual(art.write_scope(board, moved)['moved_identities'], ['f'])
        ambiguous = {
            'octal': (b'x\\ny', b'x\\1y', 'writer set'),
            'two-digit octal': (b'x\\ny', b'x\\01y', 'writer set'),
            'hex': (b'x\\ny', b'x\\x41y', 'writer set'),
            'tab': (b'x\\ny', b'x\\ty', 'writer set'),
            'bell': (b'x\\ny', b'x\\ay', 'writer set'),
            'bare token': (b'(hide yes)', b'(hide y\\es)', 'outside a quoted string'),
            'comment': (b'(kicad_pcb ', b'(kicad_pcb ; \\1\n', 'outside parsed atoms'),
        }
        for name, (old, new, message) in ambiguous.items():
            spelled = board.replace(old, new, 1)
            self.assertNotEqual(spelled, board)
            for left, right in ((spelled, spelled), (board, spelled), (spelled, moved)):
                with self.subTest(case=name), self.assertRaisesRegex(ValueError, message):
                    art.write_scope(left, right)
        # Decoded-equal respellings of unchanged nodes are token changes.
        respelled = {
            'escaped vs literal line break': (b'x\\ny', b'x\ny'),
            'quoted vs bare layer': (b'(layer "F.Fab")', b'(layer F.Fab)'),
            'quoted vs bare number': (b'(at 0 0)', b'(at "0" 0)'),
        }
        for name, (old, new) in respelled.items():
            spelled = board.replace(old, new, 1)
            self.assertEqual(parse_sexpr(spelled), parse_sexpr(board))
            for after in (spelled, moved.replace(old, new, 1)):
                with self.subTest(case=name), self.assertRaisesRegex(ValueError, 'non-silk board token changed'):
                    art.write_scope(board, after)
        # A moved field must keep its text spelling exactly.
        with self.assertRaisesRegex(ValueError, 'translation authority'):
            art.write_scope(board, moved.replace(b'"Reference" "R1"', b'"Reference" R1'))

    def test_write_scope_target_restricts_moves_to_named_fields(self):
        fp = (b'(footprint "x" (property "Reference" "%s" (at 1 2) (layer "F.SilkS") (uuid "%s-ref")) '
              b'(property "Value" "v" (at 1 3) (layer "F.SilkS") (uuid "%s-val")) '
              b'(fp_line (start 1 2) (end 3 2) (layer "F.SilkS") (uuid "%s-line")) (uuid "%s"))')
        def footprint(ref):
            return fp % ((ref,) * 5)
        board = b'(kicad_pcb ' + footprint(b'C128') + footprint(b'R73') + footprint(b'U1') + b')'
        def move(data, ref, what):
            node = footprint(ref)
            edit = {'ref': (b'(at 1 2)', b'(at 2 2)'), 'val': (b'(at 1 3)', b'(at 2 3)'),
                    'line': (b'(start 1 2) (end 3 2)', b'(start 2 2) (end 4 2)')}[what]
            start = data.index(node[:node.index(b'(property')] + b'(property "Reference" "' + ref)
            end = data.index(b'(uuid "' + ref + b'"))', start)
            changed = data[start:end].replace(*edit)
            self.assertNotEqual(changed, data[start:end])
            return data[:start] + changed + data[end:]
        target = [('C128', 'Reference'), ('R73', 'Reference')]
        both = move(move(board, b'C128', 'ref'), b'R73', 'ref')
        proof = art.write_scope(board, both, targets=target)
        self.assertEqual(proof['moved_identities'], ['C128-ref', 'R73-ref'])
        self.assertEqual(proof['targets'], [['C128', 'Reference'], ['R73', 'Reference']])
        # Untargeted proofs keep their historical shape.
        self.assertNotIn('targets', art.write_scope(board, both))
        self.assertEqual(art.write_scope(board, move(board, b'C128', 'ref'), targets=target)['moved_identities'],
                         ['C128-ref'])
        outside = {'other reference': move(both, b'U1', 'ref'), 'targeted footprint value': move(both, b'C128', 'val'),
                   'targeted footprint graphic': move(both, b'R73', 'line')}
        for name, after in outside.items():
            # Each is allowed without a target, so only the target refuses it.
            self.assertEqual(art.write_scope(board, after, artwork_policy=authored_free_graphics(board))['status'], 'allowed')
            with self.subTest(case=name), self.assertRaisesRegex(ValueError, 'outside the artwork target'):
                art.write_scope(board, after, targets=target)
        duplicate = board.replace(footprint(b'U1'), footprint(b'C128').replace(b'"C128-', b'"dup-').replace(
            b'(uuid "C128"))', b'(uuid "dup"))'))
        invalid = {
            'unknown reference': (board, [('C9', 'Reference')], 'exactly one footprint field'),
            'unknown field': (board, [('C128', 'Footprint')], 'exactly one footprint field'),
            'duplicate reference': (duplicate, [('C128', 'Reference')], 'exactly one footprint field'),
            'empty': (board, [], 'empty'),
            'option-like': (board, [('-C128', 'Reference')], 'pair'),
            'malformed': (board, [('C128',)], 'pair'),
        }
        for name, (data, targets, message) in invalid.items():
            with self.subTest(case=name), self.assertRaisesRegex(ValueError, message):
                art.write_scope(data, data, targets=targets)

    def test_native_targeted_relocation_changes_only_named_fields(self):
        source = self.pod_board()
        before = source.read_bytes()
        report = art.produce(source, self.root / 'run', targets=[('C1', 'Reference')])
        artwork = read_json(self.root / 'run' / art.ARTWORK)
        self.assertEqual([(m['reference'], m['field']) for m in artwork['moved_fields']], [('C1', 'Reference')])
        self.assertEqual(artwork['targets'], [['C1', 'Reference']])
        self.assertNotIn('moved_graphics', artwork)
        self.assertEqual(report['write_scope']['targets'], [['C1', 'Reference']])
        output = (self.root / 'run' / art.BOARD).read_bytes()
        self.assertEqual(art.write_scope(before, output, targets=[('C1', 'Reference')]), report['write_scope'])
        # The untargeted U1 conflict stays an open, typed defect.
        self.assertTrue(report['open_defects'])
        self.assertEqual(report['verdict'], 'defect')
        u1 = [c[1] for fp in children(parse_sexpr(before), 'footprint')
              for p in children(fp, 'property') if p[1:3] == ['Reference', 'U1']
              for c in p if isinstance(c, list) and c[0] == 'uuid']
        self.assertEqual(len(u1), 1)
        self.assertIn(u1[0], {i['uuid'] for d in report['open_defects'] for i in d['native']['items']})
        # Every node outside C1's Reference field is unchanged in raw spelling.
        def rest(data):
            root = parse_sexpr(data, raw=True)
            return [n if n[:1] != ['footprint'] else
                    [c for c in n if not (isinstance(c, list) and c[:3] == ['property', '"Reference"', '"C1"'])]
                    for n in root]
        self.assertEqual(rest(before), rest(output))
        self.assertNotEqual(before, output)
        for targets, message in (([('C9', 'Reference')], 'exactly one footprint field'),
                                 ([('C1', 'AGENT_EE_Ratings')], 'not a movable visible silk field')):
            with self.subTest(targets=targets), self.assertRaisesRegex(ValueError, message):
                art.produce(source, self.root / targets[0][0] / targets[0][1], targets=targets)

    def test_native_targeted_receipt_binds_canonical_target_argv(self):
        from agent_ee.cli import policy_files
        source = self.pod_board()
        design = self.root / 'design'
        design.mkdir()
        (design / 'board.kicad_pcb').write_bytes(source.read_bytes())
        spec = art.step(self.root / 'tool', 'board.kicad_pcb', targets=[('C1', 'Reference')])
        self.assertEqual(spec['argv'][5:], ['--target', 'C1', 'Reference'])
        receipt_dir = self.root / 'receipt'
        receipt = stage_receipts.run_step(receipt_dir, design, spec, {'implementation': 1})
        self.assertEqual(receipt['status'], 'completed', read_json(receipt_dir / 'run/execution.json'))
        proof = art.qualify_receipt(receipt_dir, receipt, 'board.kicad_pcb', policy_files())
        self.assertEqual(proof['targets'], [['C1', 'Reference']])
        self.assertEqual(len(proof['moved_identities']), 1)
        for argv, message in (([], 'does not bind'), (['--target', 'C1'], 'malformed'),
                              (['--target', 'U1', 'Reference'], 'outside the artwork target'),
                              (['--target', 'U1', 'Reference', '--target', 'C1', 'Reference'], 'not canonical'),
                              (['--target', 'C1', 'Reference', '--target', 'C1', 'Reference'], 'not canonical'),
                              (['--tar', 'C1', 'Reference'], 'malformed')):
            changed = copy.deepcopy(receipt)
            changed['spec']['argv'] = receipt['spec']['argv'][:5] + argv
            with self.subTest(argv=argv), self.assertRaisesRegex(ValueError, message):
                art.qualify_receipt(receipt_dir, changed, 'board.kicad_pcb', policy_files())

    def test_field_pose_request_allows_only_exact_requested_poses(self):
        F = FieldPoseFixture
        board, after = F.board(), F.board(**F.MOVED)
        request = F.request(board)
        proof = art.write_scope(board, after, field_poses=request)
        self.assertEqual(proof['moved_identities'], sorted([F.uid(1), F.uid(4)]))
        self.assertEqual(proof['field_poses'], art.normalize_field_poses(request))
        self.assertEqual(proof['before_sha256'], hashlib.sha256(board).hexdigest())
        # The historical translation-only default still refuses the requested rotation.
        with self.assertRaisesRegex(ValueError, 'translation authority'):
            art.write_scope(board, after)
        translated = F.board(c128='3.975 -0.925 0')
        only = F.request(board, fields=[F.C128])
        self.assertEqual(art.write_scope(board, translated, field_poses=only)['moved_identities'], [F.uid(4)])

        def entry(name, **changes):
            r = F.request(board)
            r['fields'] = [dict(e, **changes) if e['field_uuid'] == F.uid(1) else e for e in r['fields']]
            return name, r
        refused = [
            ('wrong board hash', after, dict(F.request(board), source_sha256='0' * 64), 'different source board'),
            ('wrong field uuid', after, entry('', field_uuid=F.uid(99))[1], 'no unique footprint field'),
            ('wrong footprint uuid', after, entry('', footprint_uuid=F.uid(20))[1], 'no unique footprint field'),
            ('wrong source pose', after, entry('', **{'from': dict(F.U31['from'], x_nm=7_600_001)})[1],
             'source pose differs'),
            ('wrong source angle', after, entry('', **{'from': dict(F.U31['from'], angle_deg='270')})[1],
             'source pose differs'),
            ('wrong destination', after, entry('', to=dict(F.U31['to'], y_nm=22_425_001))[1],
             'destination position differs'),
            ('unrequested rotation', F.board(**dict(F.MOVED, c128='3.975 -0.925 90')), request,
             'destination angle differs'),
            ('requested rotation not applied', F.board(**dict(F.MOVED, u31='2.425 -0.575 90')), request,
             'destination angle differs'),
            ('unselected silk field', F.board(**F.MOVED, r9='0.5 2'), request, 'outside the field-pose request'),
            ('unselected footprint graphic', F.board(**F.MOVED, line='(start 0.5 1) (end 1.5 1)'), request,
             'outside the field-pose request'),
            ('unselected fab value', F.board(**F.MOVED, value='0.5 -2.4 90'), request, 'non-silk board token'),
            ('style: font size', F.board(**F.MOVED, size='1.2 1.2'), request, 'translation authority'),
            ('style: stroke', F.board(**F.MOVED, stroke='0.2'), request, 'translation authority'),
            ('style: mirroring', F.board(**F.MOVED, justify=''), request, 'translation authority'),
            ('style: text', F.board(**F.MOVED, text='U32'), request, 'translation authority'),
            ('style: layer', F.board(**F.MOVED, layer='F.SilkS'), request, 'translation authority'),
            ('visibility', F.board(**F.MOVED, hide='(hide yes)'), request, 'not a movable visible silk field'),
            ('property content', F.board(**F.MOVED, datasheet='other.pdf'), request, 'non-silk board token'),
            # A footprint move also moves its fields, so the bound destination refuses first.
            ('footprint pose', F.board(**F.MOVED, fp='10 20.1 -90'), request, 'destination position differs'),
            ('copper pad', F.board(**F.MOVED, pad='0.1 0'), request, 'non-silk board token'),
            ('copper track', F.board(**F.MOVED, track='5.1'), request, 'non-silk board token'),
            # KiCad refuses to load quoted or non-native pose numbers; so does the qualifier,
            # alongside an otherwise valid requested edit, before any value comparison.
            ('quoted destination x', F.board(**dict(F.MOVED, u31='"2.425" -0.575 0')), request, 'native number'),
            ('quoted destination y', F.board(**dict(F.MOVED, u31='2.425 "-0.575" 0')), request, 'native number'),
            ('quoted destination angle', F.board(**dict(F.MOVED, u31='2.425 -0.575 "0"')), request,
             'native number'),
            ('list destination token', F.board(**dict(F.MOVED, u31='2.425 (-0.575) 0')), request, 'native number'),
            ('exponent destination', F.board(**dict(F.MOVED, u31='2.425e0 -0.575 0')), request, 'native number'),
            ('signed-plus destination', F.board(**dict(F.MOVED, u31='+2.425 -0.575 0')), request, 'native number'),
            ('bare-point destination', F.board(**dict(F.MOVED, u31='2.425 -.575 0')), request, 'native number'),
            ('quoted footprint angle', F.board(**dict(F.MOVED, fp='10 20 "-90"')), request, 'native number'),
        ]
        for name, changed, req, message in refused:
            self.assertNotEqual(changed, after if req is request else b'')
            with self.subTest(case=name), self.assertRaisesRegex(ValueError, message):
                art.write_scope(board, changed, field_poses=req)
        # A quoted source pose refuses too, even when its request is bound to those bytes.
        quoted = F.board(u31='0 "2.4" 90')
        with self.assertRaisesRegex(ValueError, 'native number'):
            art.write_scope(quoted, after, field_poses=F.request(quoted))
        invalid = {
            'targets too': (dict(field_poses=request, targets=[('U31', 'Reference')]), 'exclusive'),
            'fab field': (dict(field_poses=F.request(board, fields=[dict(
                F.U31, field_uuid=F.uid(2))])), 'not a movable visible silk field'),
            'non-quarter-turn footprint': (dict(field_poses=F.request(board, fields=[dict(
                F.U31, footprint_uuid=F.uid(30), field_uuid=F.uid(31))])), 'quarter-turn footprint'),
            'no-op': (dict(field_poses=F.request(board, fields=[dict(F.U31, to=F.U31['from'])])), 'does not change'),
            'duplicate': (dict(field_poses=F.request(board, fields=[F.U31, F.U31])), 'twice'),
            'non-canonical angle': (dict(field_poses=F.request(board, fields=[dict(
                F.U31, to=dict(F.U31['to'], angle_deg='0.0'))])), 'canonical angle'),
            'full turn': (dict(field_poses=F.request(board, fields=[dict(
                F.U31, to=dict(F.U31['to'], angle_deg='360'))])), 'canonical angle'),
            'float coordinate': (dict(field_poses=F.request(board, fields=[dict(
                F.U31, to=dict(F.U31['to'], x_nm=10575000.0))])), 'integer'),
            'unknown key': (dict(field_poses=dict(F.request(board), note='x')), 'malformed'),
            'empty': (dict(field_poses=F.request(board, fields=[])), 'malformed'),
        }
        for name, (kwargs, message) in invalid.items():
            with self.subTest(case=name), self.assertRaisesRegex(ValueError, message):
                art.write_scope(board, after, **kwargs)

    def test_field_pose_receipt_scope_is_canonical(self):
        F = FieldPoseFixture
        request = F.request(F.board())
        canonical = art.canonical_field_poses(request)
        head = ['/usr/bin/python3', '-B', '-I', '/artwork-tool/artwork.py', '/candidate/b.kicad_pcb']
        self.assertEqual(art.receipt_scope(head + ['--field-pose', canonical]),
                         {'field_poses': art.normalize_field_poses(request)})
        self.assertEqual(art.receipt_scope(head), {'targets': None})
        self.assertEqual(art.receipt_scope(head + ['--target', 'C1', 'Reference']),
                         {'targets': frozenset({('C1', 'Reference')})})
        reordered = dict(request, fields=request['fields'][::-1])
        for argv, message in ((['--field-pose', json.dumps(request)], 'not canonical'),
                              (['--field-pose', json.dumps(reordered, sort_keys=True, separators=(',', ':'))],
                               'not canonical'),
                              (['--field-pose'], 'malformed'), (['--field-pose', '{'], 'malformed'),
                              # An explicit null is not an absent request and never selects the default.
                              (['--field-pose', 'null'], 'is null'), (['--field-pose', '[]'], 'malformed'),
                              (['--field-pose', canonical, '--target', 'C1', 'Reference'], 'malformed')):
            with self.subTest(argv=argv), self.assertRaisesRegex(ValueError, message):
                art.receipt_scope(head + argv)
        for call in (lambda: art.canonical_field_poses(None), lambda: art.decode_field_poses('null')):
            with self.assertRaisesRegex(ValueError, 'is null'):
                call()
        self.assertEqual(art.decode_field_poses(canonical), art.normalize_field_poses(request))

    def test_explicit_null_field_pose_receipt_refuses_without_edits(self):
        from agent_ee.cli import policy_files
        source = self.pod_board(graphic=True)
        design = self.root / 'design'
        design.mkdir()
        (design / 'board.kicad_pcb').write_bytes(source.read_bytes())
        # The reviewer's repro: an ordinary step with an explicit JSON-null request appended.
        spec = art.step(self.root / 'tool', 'board.kicad_pcb')
        spec['argv'] += ['--field-pose', 'null']
        receipt_dir = self.root / 'receipt'
        receipt = stage_receipts.run_step(receipt_dir, design, spec, {'implementation': 1})
        self.assertNotEqual(receipt['status'], 'completed')
        self.assertFalse((receipt_dir / 'run' / art.BOARD).exists())
        self.assertFalse((receipt_dir / 'run' / art.ARTWORK).exists())
        # No board was written: the receipt records the output as absent, never as an edit.
        self.assertIsNone(receipt['outputs']['board.kicad_pcb'])
        self.assertIn('field-pose request is null', (receipt_dir / 'run' / 'stderr.log').read_text())
        self.assertEqual((design / 'board.kicad_pcb').read_bytes(), source.read_bytes())
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            art.qualify_receipt(receipt_dir, receipt, 'board.kicad_pcb', policy_files())
        # Explicitly classify the authored free graphic for the positive receipt;
        # appending a second, null field-pose scope is refused by the qualifier.
        default = art.step(self.root / 'tool2', 'board.kicad_pcb',
                           artwork_policy=authored_free_graphics(source.read_bytes()))
        receipt_dir = self.root / 'default'
        receipt = stage_receipts.run_step(receipt_dir, design, default, {'implementation': 2})
        self.assertEqual(receipt['status'], 'completed', read_json(receipt_dir / 'run/execution.json'))
        self.assertTrue(art.qualify_receipt(receipt_dir, receipt, 'board.kicad_pcb', policy_files())['moved_identities'])
        forged = copy.deepcopy(receipt)
        forged['spec']['argv'] += ['--field-pose', 'null']
        with self.assertRaisesRegex(ValueError, 'policy arguments malformed'):
            art.qualify_receipt(receipt_dir, forged, 'board.kicad_pcb', policy_files())

    def test_native_field_pose_producer_writes_exact_rotation_and_receipts_it(self):
        from agent_ee.cli import policy_files
        import pcbnew as k
        source = self.pod_board()
        before = source.read_bytes()
        fp = [f for f in k.LoadBoard(str(source)).GetFootprints() if f.GetReference() == 'C1'][0]
        field = fp.Reference()
        pos = field.GetPosition()
        start = {'x_nm': pos.x, 'y_nm': pos.y, 'angle_deg': str(art._native_pose(field)[2])}
        request = {'schema': art.FIELD_POSE_SCHEMA, 'source_sha256': hashlib.sha256(before).hexdigest(),
                   'fields': [{'footprint_uuid': fp.m_Uuid.AsString(), 'field_uuid': field.m_Uuid.AsString(),
                               'from': start, 'to': {'x_nm': pos.x + 500000, 'y_nm': pos.y - 250000,
                                                     'angle_deg': '90' if start['angle_deg'] != '90' else '0'}}]}
        report = art.produce(source, self.root / 'run', field_poses=request)
        artwork = read_json(self.root / 'run' / art.ARTWORK)
        self.assertEqual([(m['reference'], m['field'], m['to']) for m in artwork['moved_fields']],
                         [('C1', 'Reference', request['fields'][0]['to'])])
        output = (self.root / 'run' / art.BOARD).read_bytes()
        proof = art.write_scope(before, output, field_poses=request)
        self.assertEqual(proof, report['write_scope'])
        self.assertEqual(proof['moved_identities'], [field.m_Uuid.AsString()])
        # Native reload and the independent byte-level qualifier agree on the pose.
        reloaded = {f.m_Uuid.AsString(): art._native_pose(f) for f in
                    next(f for f in k.LoadBoard(str(self.root / 'run' / art.BOARD)).GetFootprints()
                         if f.GetReference() == 'C1').GetFields()}
        self.assertEqual(reloaded[field.m_Uuid.AsString()], art._request_pose(request['fields'][0]['to']))
        # Native refusals before any write: wrong board and wrong source pose.
        for name, changed, message in (
                ('board', dict(request, source_sha256='0' * 64), 'different source board'),
                ('pose', dict(request, fields=[dict(request['fields'][0], **{'from': dict(start, x_nm=pos.x + 1)})]),
                 'source pose differs')):
            with self.subTest(case=name), self.assertRaisesRegex(ValueError, message):
                art.produce(source, self.root / name, field_poses=changed)
        # The receipt binds the canonical request and the qualifier replays it.
        design = self.root / 'design'
        design.mkdir()
        (design / 'board.kicad_pcb').write_bytes(before)
        spec = art.step(self.root / 'tool', 'board.kicad_pcb', field_poses=request)
        self.assertEqual(spec['argv'][5:], ['--field-pose', art.canonical_field_poses(request)])
        receipt_dir = self.root / 'receipt'
        receipt = stage_receipts.run_step(receipt_dir, design, spec, {'implementation': 1})
        self.assertEqual(receipt['status'], 'completed', read_json(receipt_dir / 'run/execution.json'))
        qualified = art.qualify_receipt(receipt_dir, receipt, 'board.kicad_pcb', policy_files())
        self.assertEqual(qualified['field_poses'], art.normalize_field_poses(request))
        stale = dict(request, fields=[dict(request['fields'][0], to=dict(request['fields'][0]['to'],
                                                                         x_nm=pos.x + 750000))])
        for argv, message in ((['--field-pose', art.canonical_field_poses(stale)], 'destination position differs'),
                              ([], 'translation authority'),
                              (['--target', 'C1', 'Reference'], 'translation authority')):
            changed = copy.deepcopy(receipt)
            changed['spec']['argv'] = receipt['spec']['argv'][:5] + argv
            with self.subTest(argv=argv[:1]), self.assertRaisesRegex(ValueError, message):
                art.qualify_receipt(receipt_dir, changed, 'board.kicad_pcb', policy_files())
        with self.assertRaisesRegex(ValueError, 'exclusive'):
            art.step(self.root / 'tool2', 'board.kicad_pcb', field_poses=request, targets=[('C1', 'Reference')])


class FieldPoseFixture:
    """An original saved-board fragment with a quarter-turn and a half-turn bottom footprint.

    U31 (at 10 20 -90) has its Reference at board (7.6, 20) 90 deg; C128 (at 30 20 180)
    has its Reference at board (30, 18.15) 0 deg. MOVED writes U31 to (10.575, 22.425)
    0 deg and C128 to (26.025, 20.925) 0 deg, as footprint-relative file tokens.
    """
    MOVED = {'u31': '2.425 -0.575 0', 'c128': '3.975 -0.925 0'}

    @staticmethod
    def uid(n):
        return f'00000000-0000-4000-8000-{n:012d}'

    U31 = {'footprint_uuid': uid.__func__(10), 'field_uuid': uid.__func__(1),
           'from': {'x_nm': 7600000, 'y_nm': 20000000, 'angle_deg': '90'},
           'to': {'x_nm': 10575000, 'y_nm': 22425000, 'angle_deg': '0'}}
    C128 = {'footprint_uuid': uid.__func__(20), 'field_uuid': uid.__func__(4),
            'from': {'x_nm': 30000000, 'y_nm': 18150000, 'angle_deg': '0'},
            'to': {'x_nm': 26025000, 'y_nm': 20925000, 'angle_deg': '0'}}

    @classmethod
    def board(cls, *, u31='0 2.4 90', c128='0 1.85 0', r9='0 2', line='(start 0 1) (end 1 1)',
              value='0 -2.4 90', size='1 1', stroke='0.15', justify='(justify mirror)', text='U31',
              layer='B.SilkS', hide='', datasheet='d.pdf', fp='10 20 -90', pad='0 0', track='5'):
        u = cls.uid
        effects = '(effects (font (size %s) (thickness %s)) %s)'
        return (
            f'(kicad_pcb (version 20250000)\n'
            f'(footprint "SOT-23" (layer "B.Cu") (uuid "{u(10)}") (at {fp})\n'
            f'(property "Reference" "{text}" (at {u31}) (layer "{layer}") {hide} (uuid "{u(1)}") '
            f'{effects % (size, stroke, justify)})\n'
            f'(property "Value" "TL431" (at {value}) (layer "B.Fab") (uuid "{u(2)}") {effects % ("1 1", "0.15", "")})\n'
            f'(property "Datasheet" "{datasheet}" (at 0 0 90) (layer "B.Fab") (hide yes) (uuid "{u(3)}"))\n'
            f'(fp_line {line} (stroke (width 0.12) (type solid)) (layer "B.SilkS") (uuid "{u(11)}"))\n'
            f'(pad "1" smd rect (at {pad}) (size 1 1) (layers "B.Cu" "B.Mask") (net 1 "N") (uuid "{u(12)}")))\n'
            f'(footprint "C_1206" (layer "B.Cu") (uuid "{u(20)}") (at 30 20 180)\n'
            f'(property "Reference" "C128" (at {c128}) (layer "B.SilkS") (uuid "{u(4)}") '
            f'{effects % ("1 1", "0.15", "(justify mirror)")}))\n'
            f'(footprint "R_0603" (layer "B.Cu") (uuid "{u(30)}") (at 40 40 45)\n'
            f'(property "Reference" "R9" (at {r9} 45) (layer "B.SilkS") (uuid "{u(31)}") '
            f'{effects % ("1 1", "0.15", "(justify mirror)")}))\n'
            f'(segment (start 1 1) (end {track} 1) (width 0.25) (layer "B.Cu") (net 1) (uuid "{u(40)}")))\n'
        ).encode()

    @classmethod
    def request(cls, board, fields=None):
        return {'schema': art.FIELD_POSE_SCHEMA, 'source_sha256': hashlib.sha256(board).hexdigest(),
                'fields': [cls.U31, cls.C128] if fields is None else fields}


# A real telemetry-pod board read from the pinned fome-mc store (never committed
# here): the retained electrical-locality baseline, whose footprints carry the
# agent-ee escaped JSON properties and whose visible references have native
# silk conflicts. R73/R175/U19 have a legal position within the search bound.
POD_COMMIT = '154cb59ec485c16fcb86d90ab35e2d8cf73a9e01'
POD_DIR = 'telemetry-pod/expansion/electrical-locality/'
POD_TARGETS = [('R175', 'Reference'), ('R73', 'Reference'), ('U19', 'Reference')]


class RealPodArtworkTest(unittest.TestCase):
    """AGENTEE-434 on a real pod board: escape-exact attestation and targeted repair."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        repo = fome_mc_store(os.environ.get('AGENT_EE_FOME_MC_CHECKOUT', Path.home() / 'git/fome-mc'), POD_COMMIT)
        def blob(name):
            return subprocess.check_output(['git', '-C', str(repo), 'show', f'{POD_COMMIT}:{POD_DIR}{name}'])
        cls.pod = blob('baseline.kicad_pcb')
        # The store's own retained digest identifies the board.
        sums = dict(reversed(line.split('  ', 1)) for line in blob('SHA256SUMS').decode().splitlines())
        if hashlib.sha256(cls.pod).hexdigest() != sums['baseline.kicad_pcb']:
            raise AssertionError('pinned pod board differs from its retained SHA256SUMS digest')

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='ee434-pod-')))
        # Board only: the pod project's ignored DRC classes would refuse artwork DRC.
        self.source = self.root / 'design' / 'board.kicad_pcb'
        self.source.parent.mkdir()
        self.source.write_bytes(self.pod)

    def footprint_edit(self, data, ref, old, new):
        """Replace old once inside the footprint whose Reference is ref."""
        at = data.index(b'(property "Reference" "%s"\n' % ref)
        start = data.rindex(b'\n\t(footprint ', 0, at)
        end = data.index(b'\n\t)\n', at)
        block = data[start:end]
        self.assertIn(old, block)
        return data[:start] + block.replace(old, new, 1) + data[end:]

    def reference_uuid(self, ref):
        uid = [c[1] for fp in children(parse_sexpr(self.pod), 'footprint')
               for p in children(fp, 'property') if p[1:3] == ['Reference', ref]
               for c in p if isinstance(c, list) and c[0] == 'uuid']
        self.assertEqual(len(uid), 1)
        return uid[0]

    def test_real_pod_board_with_escaped_properties_attests_unchanged(self):
        before = self.pod
        self.assertIn(b'\\"', before)
        escaped = [p for fp in children(parse_sexpr(before, raw=True), 'footprint') for p in children(fp, 'property')
                   if p[1] in ('"PARAM_Datasheet"', '"AGENT_EE_Ratings"') and '\\"' in p[2]]
        self.assertEqual({p[1] for p in escaped}, {'"PARAM_Datasheet"', '"AGENT_EE_Ratings"'})
        digest = hashlib.sha256(before).hexdigest()
        self.assertEqual(art.write_scope(before, before), {
            'status': 'allowed', 'before_sha256': digest, 'after_sha256': digest, 'moved_identities': []})
        self.assertEqual(art.write_scope(before, before, targets=POD_TARGETS)['moved_identities'], [])

    def test_real_pod_targeted_relocation_moves_only_named_fields(self):
        before = self.pod
        report = art.produce(self.source, self.root / 'run', targets=POD_TARGETS)
        artwork = read_json(self.root / 'run' / art.ARTWORK)
        self.assertEqual(sorted((m['reference'], m['field']) for m in artwork['moved_fields']), POD_TARGETS)
        self.assertNotIn('moved_graphics', artwork)
        output = (self.root / 'run' / art.BOARD).read_bytes()
        proof = art.write_scope(before, output, targets=POD_TARGETS)
        self.assertEqual(proof, report['write_scope'])
        self.assertEqual(proof['targets'], [list(t) for t in POD_TARGETS])
        moved = sorted(self.reference_uuid(ref) for ref, _ in POD_TARGETS)
        self.assertEqual(proof['moved_identities'], moved)
        # Repaired targets leave the open set; untargeted conflicts stay typed defects.
        open_items = {i['uuid'] for d in report['open_defects'] for i in d['native']['items']}
        self.assertFalse(open_items & set(moved))
        self.assertIn(self.reference_uuid('U31'), open_items)
        self.assertEqual(report['verdict'], 'defect')
        # Every raw node outside the three targeted Reference fields keeps its spelling.
        selected = [['property', '"Reference"', f'"{ref}"'] for ref, _ in POD_TARGETS]
        def rest(data):
            return [n if n[:1] != ['footprint'] else
                    [c for c in n if not (isinstance(c, list) and c[:3] in selected)]
                    for n in parse_sexpr(data, raw=True)]
        self.assertEqual(rest(before), rest(output))
        self.assertNotEqual(before, output)

    def test_real_pod_targeted_receipt_replays(self):
        from agent_ee.cli import policy_files
        spec = art.step(self.root / 'tool', 'board.kicad_pcb', targets=POD_TARGETS)
        self.assertEqual(spec['argv'][5:], [v for t in POD_TARGETS for v in ('--target', *t)])
        receipt_dir = self.root / 'receipt'
        receipt = stage_receipts.run_step(receipt_dir, self.source.parent, spec, {'implementation': 1})
        self.assertEqual(receipt['status'], 'completed', read_json(receipt_dir / 'run/execution.json'))
        proof = art.qualify_receipt(receipt_dir, receipt, 'board.kicad_pcb', policy_files())
        self.assertEqual(proof['before_sha256'], hashlib.sha256(self.pod).hexdigest())
        self.assertEqual(proof['targets'], [list(t) for t in POD_TARGETS])
        self.assertEqual(proof['moved_identities'], sorted(self.reference_uuid(r) for r, _ in POD_TARGETS))

    def test_real_pod_escaped_content_and_unselected_field_edits_refuse(self):
        r73 = [('R73', 'Reference')]
        moved = self.footprint_edit(self.pod, b'R73', b'(at 0 -1.65 270)', b'(at 0.5 -1.65 270)')
        # Positive control: the named field alone moves, receipted by target.
        self.assertEqual(art.write_scope(self.pod, moved, targets=r73)['moved_identities'],
                         [self.reference_uuid('R73')])
        refused = {
            'targeted footprint escaped datasheet': (self.footprint_edit(
                moved, b'R73', b'"PARAM_Datasheet" "{\\"', b'"PARAM_Datasheet" "{ \\"'), 'non-silk board token'),
            'escaped ratings content': (moved.replace(
                b'(property "AGENT_EE_Ratings" "{\\"', b'(property "AGENT_EE_Ratings" "{ \\"', 1), 'non-silk board token'),
            'escape respelled outside the writer set': (moved.replace(
                b'(property "AGENT_EE_Ratings" "{\\"', b'(property "AGENT_EE_Ratings" "{\\x22', 1), 'writer set'),
            'unselected reference': (self.footprint_edit(
                moved, b'R175', b'(at 0 2.63 0)', b'(at 0.5 2.63 0)'), 'outside the artwork target'),
            'targeted footprint value': (self.footprint_edit(
                moved, b'R73', b'(at 0 1.65 270)', b'(at 0.5 1.65 270)'), 'non-silk board token'),
        }
        for name, (after, message) in refused.items():
            self.assertNotEqual(after, moved)
            for targets in (r73, None):
                if targets is None and message == 'outside the artwork target':
                    # Untargeted, the same move is an allowed silk translation.
                    self.assertEqual(art.write_scope(self.pod, after)['status'], 'allowed')
                    continue
                with self.subTest(case=name, targets=targets), self.assertRaisesRegex(ValueError, message):
                    art.write_scope(self.pod, after, targets=targets)
        with self.assertRaisesRegex(ValueError, 'not a movable visible silk field'):
            art.produce(self.source, self.root / 'hidden', targets=[('R73', 'PARAM_Datasheet')])

    def test_real_pod_field_pose_transform_and_rotation_request(self):
        import pcbnew as k
        root = parse_sexpr(self.pod)
        # The qualifier's exact file transform equals native poses for every field.
        native = {f.m_Uuid.AsString(): art._native_pose(f)
                  for fp in k.LoadBoard(str(self.source)).GetFootprints() for f in fp.GetFields()}
        exact = {children(p, 'uuid')[0][1]: art.field_pose(fp, p)
                 for fp in children(root, 'footprint') for p in children(fp, 'property')}
        self.assertGreater(len(exact), 1000)
        self.assertEqual(exact, native)
        # A requested 270 -> 0 deg rotation of R73's Reference, read back natively.
        after = self.footprint_edit(self.pod, b'R73', b'(at 0 -1.65 270)', b'(at 0.5 -1.65 0)')
        edited = self.root / 'edited.kicad_pcb'
        edited.write_bytes(after)
        fp, prop = next((fp, p) for fp in children(root, 'footprint') for p in children(fp, 'property')
                        if p[1:3] == ['Reference', 'R73'])
        field = next(f for f in next(f for f in k.LoadBoard(str(edited)).GetFootprints()
                                     if f.GetReference() == 'R73').GetFields() if f.GetName() == 'Reference')
        def pose(p):
            return {'x_nm': p[0], 'y_nm': p[1], 'angle_deg': str(p[2])}
        entry = {'footprint_uuid': children(fp, 'uuid')[0][1], 'field_uuid': children(prop, 'uuid')[0][1],
                 'from': pose(art.field_pose(fp, prop)), 'to': pose(art._native_pose(field))}
        self.assertEqual(entry['from']['angle_deg'], '270')
        self.assertEqual(entry['to']['angle_deg'], '0')
        request = {'schema': art.FIELD_POSE_SCHEMA, 'source_sha256': hashlib.sha256(self.pod).hexdigest(),
                   'fields': [entry]}
        proof = art.write_scope(self.pod, after, field_poses=request)
        self.assertEqual(proof['moved_identities'], [self.reference_uuid('R73')])
        # Translation-only authority and unrelated edits still refuse on the escaped board.
        with self.assertRaisesRegex(ValueError, 'translation authority'):
            art.write_scope(self.pod, after, targets=[('R73', 'Reference')])
        for name, changed, message in (
                ('escaped content', self.footprint_edit(after, b'R73', b'"PARAM_Datasheet" "{\\"',
                                                        b'"PARAM_Datasheet" "{ \\"'), 'non-silk board token'),
                ('unselected field', self.footprint_edit(after, b'R175', b'(at 0 2.63 0)', b'(at 0.5 2.63 0)'),
                 'outside the field-pose request'),
                ('unrequested angle', self.footprint_edit(self.pod, b'R73', b'(at 0 -1.65 270)', b'(at 0.5 -1.65 90)'),
                 'destination angle differs')):
            with self.subTest(case=name), self.assertRaisesRegex(ValueError, message):
                art.write_scope(self.pod, changed, field_poses=request)

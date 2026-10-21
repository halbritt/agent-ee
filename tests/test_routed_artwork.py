"""Native routed-board silk repair, restricted geometry and receipt boundaries."""
import copy
from pathlib import Path
import tempfile
import unittest

from agent_ee import routed_artwork as art, stage_receipts
from agent_ee.silk_artwork import DECLARATION
from agent_ee.storage import read_json
from agent_ee.symbol_library import children, parse_sexpr
import test_silk_artwork as silk_fixture
from toolchain import require_toolchain


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

    def test_board_and_footprint_graphics_translate_without_reshaping(self):
        for footprint in (False, True):
            with self.subTest(footprint=footprint), tempfile.TemporaryDirectory(dir=self.root) as d:
                prior = self.root
                self.root = Path(d)
                try:
                    source = self.board(graphic=True, footprint=footprint)
                    report = art.produce(source, self.root / 'run')
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
        self.assertEqual(art.write_scope(b, b.replace(b'1 2', b'2 3').replace(b'3 2', b'4 3'))['status'], 'allowed')
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
                    report = art.produce(source, self.root / 'run')
                    current = pd._identities(self.root / 'run' / art.BOARD, report['containment'])
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
        spec = art.step(self.root / 'tool', 'board.kicad_pcb')
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
        report = art.produce(source, self.root / 'run')
        self.assertTrue(report['open_defects'])
        self.assertTrue(any(uid == i['uuid'] for d in report['open_defects'] for i in d['native']['items']))
        artwork = read_json(self.root / 'run' / art.ARTWORK)
        self.assertIn({'uuid': uid, 'reference': '', 'reason': 'bounded_search_exhausted'},
                      artwork['search_diagnostics'])
        self.assertNotIn(uid, report['write_scope']['moved_identities'])

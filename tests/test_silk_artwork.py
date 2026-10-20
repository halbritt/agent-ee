"""Original native artwork conflicts, position-only authority and publication gate."""
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee.placement_adapter import write_scope
from agent_ee.placer_runner import _proposal_drc, _native_drc
from agent_ee.silk_artwork import relocate, movable_names
from toolchain import require_toolchain
import test_placement_native as native_fixture


class SilkArtworkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='ee320-')))

    def board(self, back=False):
        import pcbnew as k
        fixture = native_fixture.NativePlacementTest()
        fixture.root = self.root
        path = fixture.board()
        b = k.LoadBoard(str(path))
        for fp in b.GetFootprints():
            fp.Reference().SetLayer(k.F_SilkS)
            fp.Reference().SetTextSize(k.VECTOR2I(1000000, 1000000))
            fp.Reference().SetTextThickness(150000)
            fp.Reference().SetPosition(k.VECTOR2I(fp.GetPosition().x, 6000000))
            fp.Value().SetVisible(False)
            pad = next(iter(fp.Pads()))
            layers = k.LSET()
            layers.AddLayer(k.F_Cu)
            layers.AddLayer(k.F_Mask)
            pad.SetLayerSet(layers)
            pad.SetSize(k.VECTOR2I(2000000, 2000000))
            if back:
                fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_TOP_BOTTOM)
        k.SaveBoard(str(path), b)
        return path

    def output(self, source):
        import pcbnew as k
        work = self.root / 'proposal'
        work.mkdir()
        path = work / 'candidate.kicad_pcb'
        b = k.LoadBoard(str(source))
        for fp in b.GetFootprints():
            fp.Reference().SetPosition(next(iter(fp.Pads())).GetPosition())
        k.SaveBoard(str(path), b)
        return path

    def test_native_relocation_reserves_other_courtyard_interiors(self):
        import pcbnew as k
        mm = lambda x, y: k.VECTOR2I(int(x*1e6), int(y*1e6))
        def line(parent, layer, a, b, w=0.05):
            s = k.PCB_SHAPE(parent); s.SetShape(k.SHAPE_T_SEGMENT); s.SetLayer(layer)
            s.SetStart(mm(*a)); s.SetEnd(mm(*b)); s.SetWidth(int(w*1e6)); parent.Add(s)
        def rect(parent, layer, x0, y0, x1, y1, w=0.05):
            pts = [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
            for i in range(4): line(parent, layer, pts[i], pts[(i+1)%4], w)
        b = k.BOARD()
        rect(b, k.Edge_Cuts, 0, 0, 40, 40)
        # A: wide pad at (20,10); its Reference will be placed on the pad.
        A = k.FOOTPRINT(b); A.SetReference('A1'); A.SetValue('owned'); b.Add(A); A.SetPosition(mm(20, 10))
        rect(A, k.F_CrtYd, 15.5, 9, 24.5, 11)
        p = k.PAD(A); p.SetNumber('1'); p.SetShape(k.PAD_SHAPE_RECT); p.SetAttribute(k.PAD_ATTRIB_SMD)
        ls = k.LSET(); ls.AddLayer(k.F_Cu); ls.AddLayer(k.F_Mask); p.SetLayerSet(ls); p.SetSize(mm(8, 2)); A.Add(p); p.SetPosition(mm(20, 10))
        # Board silk line blocks the area above A.
        line(b, k.F_SilkS, (5, 7.5), (35, 7.5), 0.15)
        # B: big courtyard + fab body right below A, its only pad far away.
        B = k.FOOTPRINT(b); B.SetReference('B1'); B.SetValue('owned'); b.Add(B); B.SetPosition(mm(20, 16))
        rect(B, k.F_CrtYd, 10, 11.2, 30, 20); rect(B, k.F_Fab, 10.5, 11.5, 29.5, 19.5, 0.1)
        q = k.PAD(B); q.SetNumber('1'); q.SetShape(k.PAD_SHAPE_RECT); q.SetAttribute(k.PAD_ATTRIB_SMD)
        q.SetLayerSet(ls); q.SetSize(mm(1, 1)); B.Add(q); q.SetPosition(mm(20, 19))
        for fp in (A, B):
            fp.Value().SetVisible(False)
            r = fp.Reference(); r.SetLayer(k.F_SilkS); r.SetTextSize(mm(1, 1)); r.SetTextThickness(150000)
        A.Reference().SetPosition(mm(20, 3)); B.Reference().SetPosition(mm(20, 19))
        B.Reference().SetPosition(mm(33, 30))
        src = self.root / 'src.kicad_pcb'; k.SaveBoard(str(src), b)
        work = self.root / 'out'; work.mkdir(); out = work / 'candidate.kicad_pcb'
        c = k.LoadBoard(str(src))
        fa = next(f for f in c.GetFootprints() if f.GetReference() == 'A1'); fa.Reference().SetPosition(mm(20, 10))
        k.SaveBoard(str(out), c); pre = out.read_bytes()
        _, delta = _proposal_drc(src, out)
        self.assertEqual(delta['silkscreen_findings'], [])
        final = k.LoadBoard(str(out))
        fields = {fp.GetReference(): fp for fp in final.GetFootprints()}
        box = fields['A1'].Reference().GetBoundingBox()
        self.assertFalse(box.Intersects(fields['B1'].GetCourtyard(k.F_CrtYd).BBox()))
        art = json.loads((work / 'placement-silkscreen.json').read_text())
        self.assertEqual(len(art['moved_fields']), 1)
        self.assertEqual(art['findings'], [])
        self.assertEqual(write_scope(pre, out.read_bytes())['status'], 'allowed')

    def test_flipped_footprint_conflict_is_typed_without_field_relocation(self):
        import pcbnew as k
        source = self.board()
        out = self.output(source)
        board = k.LoadBoard(str(out))
        for fp in board.GetFootprints():
            fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_TOP_BOTTOM)
        k.SaveBoard(str(out), board)
        original = out.read_bytes()
        with self.assertRaisesRegex(ValueError, 'unresolved native silkscreen'):
            _proposal_drc(source, out)
        art = json.loads((out.parent / 'placement-silkscreen.json').read_text())
        self.assertEqual(art['moved_fields'], [])
        self.assertEqual(len(art['search_diagnostics']), 2)
        self.assertEqual({d['reason'] for d in art['search_diagnostics']}, {'footprint_side_flipped'})
        self.assertEqual(len([f for f in art['findings']
                              if f.get('reason') == 'footprint_side_flipped']), 2)
        self.assertTrue(all(f['type'] == 'placement_silkscreen_conflict' for f in art['findings']))
        self.assertEqual(out.read_bytes(), original)

    def test_flipped_conflict_refuses_even_if_other_field_moves_clear(self):
        import pcbnew as k
        source = self.board()
        board = k.LoadBoard(str(source))
        prints = {fp.GetReference(): fp for fp in board.GetFootprints()}
        prints['C1'].Flip(prints['C1'].GetPosition(), k.FLIP_DIRECTION_TOP_BOTTOM)
        k.SaveBoard(str(source), board)
        out = self.output(source)
        board = k.LoadBoard(str(out))
        for fp in board.GetFootprints():
            if fp.GetReference() == 'U1':
                fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_TOP_BOTTOM)
            fp.Reference().SetPosition(k.VECTOR2I(9000000, 4000000))
        k.SaveBoard(str(out), board)
        with self.assertRaisesRegex(ValueError, 'unresolved native silkscreen'):
            _proposal_drc(source, out)
        art = json.loads((out.parent / 'placement-silkscreen.json').read_text())
        self.assertEqual([m['reference'] for m in art['moved_fields']], ['C1'])
        self.assertEqual(art['added_after'], {})
        self.assertEqual(len(art['findings']), 1)
        self.assertEqual(art['findings'][0]['type'], 'placement_silkscreen_conflict')
        self.assertEqual(art['findings'][0]['reason'], 'footprint_side_flipped')

    def test_native_rule_bytes_restored_after_artwork_save(self):
        import hashlib
        source = self.board()
        payloads = {'.kicad_pro': b'{"board":{"design_settings":{"rule_severities":{"silk_overlap":"warning"}}}}',
                    '.kicad_dru': b'(version 1)\n'}
        for suffix, payload in payloads.items():
            source.with_suffix(suffix).write_bytes(payload)
        out = self.output(source)
        _, delta = _proposal_drc(source, out)
        self.assertEqual(delta['silkscreen_findings'], [])
        art = json.loads((out.parent / 'placement-silkscreen.json').read_text())
        self.assertTrue(art['moved_fields'])
        for suffix, payload in payloads.items():
            expected = hashlib.sha256(payload).hexdigest()
            for path in (source.with_suffix(suffix), out.with_suffix(suffix),
                         (out.parent / 'baseline' / out.name).with_suffix(suffix)):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)

    def test_ignored_silk_edge_clearance_refuses_native_coverage(self):
        source = self.board()
        path = source.with_name('ignored-edge.kicad_pcb')
        path.write_bytes(source.read_bytes())
        path.with_suffix('.kicad_pro').write_text(json.dumps({'board': {'design_settings': {
            'rule_severities': {'silk_edge_clearance': 'ignore'}}}}))
        with self.assertRaisesRegex(ValueError, 'explicitly ignored'):
            _native_drc(path, self.root)

    def test_native_front_back_relocation_preserves_required_fields_and_scope(self):
        import pcbnew as k
        for back in (False, True):
            with self.subTest(back=back):
                # Independent native project names avoid pcbnew settings cache.
                old_root = self.root
                self.root = old_root / str(back)
                self.root.mkdir()
                source = self.board(back)
                original = source.read_bytes()
                out = self.output(source)
                pre = out.read_bytes()
                _, delta = _proposal_drc(source, out)
                self.assertFalse(delta['silkscreen_findings'], delta)
                evidence = json.loads((out.parent / 'placement-silkscreen.json').read_text())
                self.assertGreater(evidence['added_before']['silk_over_copper'], 0)
                self.assertEqual(evidence['findings'], [])
                self.assertEqual(len(evidence['moved_fields']), 2)
                self.assertEqual(write_scope(pre, out.read_bytes())['status'], 'allowed')
                b = k.LoadBoard(str(out))
                for fp in b.GetFootprints():
                    f = fp.Reference()
                    self.assertTrue(f.IsVisible())
                    self.assertEqual(f.GetTextSize(), k.VECTOR2I(1000000, 1000000))
                    self.assertEqual(f.GetTextThickness(), 150000)
                    self.assertEqual(f.IsMirrored(), back)
                    self.assertEqual(f.GetLayer(), k.B_SilkS if back else k.F_SilkS)
                self.assertEqual(source.read_bytes(), original)
                self.root = old_root

    def test_native_graphics_conflict_is_typed_and_graphics_unchanged(self):
        import pcbnew as k
        source = self.board()
        b = k.LoadBoard(str(source))
        for i, fp in enumerate(sorted(b.GetFootprints(), key=lambda f: f.GetReference())):
            shape = k.PCB_SHAPE(fp, k.SHAPE_T_SEGMENT)
            shape.SetLayer(k.F_SilkS)
            shape.SetStart(k.VECTOR2I(3000000, 12000000 + i * 3000000))
            shape.SetEnd(k.VECTOR2I(7000000, 12000000 + i * 3000000))
            shape.SetWidth(200000)
            fp.Add(shape)
        k.SaveBoard(str(source), b)
        out = self.output(source)
        b = k.LoadBoard(str(out))
        for fp in b.GetFootprints():
            shape = next(s for s in fp.GraphicalItems() if s.GetLayer() == k.F_SilkS)
            shape.SetStart(k.VECTOR2I(3000000, 12000000))
            shape.SetEnd(k.VECTOR2I(7000000, 12000000))
        k.SaveBoard(str(out), b)
        pre = out.read_bytes()
        with self.assertRaisesRegex(ValueError, 'unresolved native silkscreen'):
            _proposal_drc(source, out)
        evidence = json.loads((out.parent / 'placement-silkscreen.json').read_text())
        self.assertTrue(evidence['findings'])
        self.assertTrue(all(f['type'] == 'placement_silkscreen_conflict' for f in evidence['findings']))
        self.assertEqual(write_scope(pre, out.read_bytes())['status'], 'allowed')

    def test_unplaceable_fields_never_hidden_or_shrunk(self):
        import pcbnew as k
        source = self.board()
        out = self.output(source)
        b = k.LoadBoard(str(out))
        for fp in b.GetFootprints():
            pad = next(iter(fp.Pads()))
            pad.SetSize(k.VECTOR2I(100000000, 100000000))
        k.SaveBoard(str(out), b)
        pre = out.read_bytes()
        report = _native_drc(out, out.parent)
        diagnostics = relocate(b, report['violations'])
        self.assertEqual(len(diagnostics['search_diagnostics']), 2)
        self.assertEqual(diagnostics['moved_fields'], [])
        k.SaveBoard(str(out), b)
        self.assertEqual(out.read_bytes(), pre)

    def test_artwork_gate_runs_in_networkless_sandbox(self):
        from agent_ee.executor import execute
        source = self.board()
        inputs = self.root / 'inputs'
        inputs.mkdir()
        (inputs / 'board.kicad_pcb').write_bytes(source.read_bytes())
        result = execute(inputs, self.root / 'sandbox', ['/usr/bin/python3', '-B', '-c', """
from pathlib import Path
import pcbnew as k
from agent_ee.placer_runner import _proposal_drc
from agent_ee.placement_adapter import write_scope
b=k.LoadBoard('/candidate/board.kicad_pcb')
for fp in b.GetFootprints():
    fp.Reference().SetPosition(next(iter(fp.Pads())).GetPosition())
p=Path('/work/candidate.kicad_pcb')
assert k.SaveBoard(str(p), b)
before=p.read_bytes()
_, comparison=_proposal_drc(Path('/candidate/board.kicad_pcb'), p)
assert comparison['silkscreen_findings']==[]
assert write_scope(before,p.read_bytes())['status']=='allowed'
"""])
        self.assertEqual((result['execution'], result['exit_code']), ('ok', 0),
                         (self.root / 'sandbox/console.log').read_text())
        evidence = json.loads((self.root / 'sandbox/placement-silkscreen.json').read_text())
        self.assertEqual(len(evidence['moved_fields']), 2)
        self.assertEqual(evidence['findings'], [])

    def test_visible_values_relocate_without_changing_their_text(self):
        import pcbnew as k
        source = self.board()
        b = k.LoadBoard(str(source))
        for fp in b.GetFootprints():
            value = fp.Value()
            value.SetVisible(True)
            value.SetLayer(k.F_SilkS)
            value.SetTextSize(k.VECTOR2I(1000000, 1000000))
            value.SetTextThickness(150000)
            value.SetPosition(k.VECTOR2I(fp.GetPosition().x, 7000000))
        k.SaveBoard(str(source), b)
        out = self.output(source)
        b = k.LoadBoard(str(out))
        for fp in b.GetFootprints():
            fp.Value().SetPosition(next(iter(fp.Pads())).GetPosition())
        k.SaveBoard(str(out), b)
        _, delta = _proposal_drc(source, out)
        self.assertEqual(delta['silkscreen_findings'], [])
        evidence = json.loads((out.parent / 'placement-silkscreen.json').read_text())
        self.assertEqual({f['field'] for f in evidence['moved_fields']}, {'Reference', 'Value'})
        saved = k.LoadBoard(str(out))
        for fp in saved.GetFootprints():
            self.assertEqual(fp.GetValue(), 'owned')
            self.assertTrue(fp.Value().IsVisible())

    def test_declared_custom_field_relocates_but_unmarked_field_stays(self):
        import pcbnew as k
        from agent_ee.silk_artwork import DECLARATION
        source = self.board()
        b = k.LoadBoard(str(source))
        prints = sorted(b.GetFootprints(), key=lambda fp: fp.GetReference())
        for i, fp in enumerate(prints):
            fp.SetField('Label', 'PIN1')
            f = fp.GetField('Label')
            f.SetVisible(True)
            f.SetLayer(k.F_SilkS)
            f.SetTextSize(k.VECTOR2I(1000000, 1000000))
            f.SetTextThickness(150000)
            f.SetPosition(k.VECTOR2I(fp.GetPosition().x, 7000000))
            if i == 0:
                fp.SetField(DECLARATION, '["Label"]')
                fp.GetField(DECLARATION).SetVisible(False)
        k.SaveBoard(str(source), b)
        out = self.output(source)
        b = k.LoadBoard(str(out))
        for fp in b.GetFootprints():
            fp.GetField('Label').SetPosition(next(iter(fp.Pads())).GetPosition())
        k.SaveBoard(str(out), b)
        before = out.read_bytes()
        with self.assertRaisesRegex(ValueError, 'unresolved native silkscreen'):
            _proposal_drc(source, out)
        evidence = json.loads((out.parent / 'placement-silkscreen.json').read_text())
        self.assertEqual([f['reference'] for f in evidence['moved_fields'] if f['field'] == 'Label'],
                         [prints[0].GetReference()])
        saved = k.LoadBoard(str(out))
        for fp in saved.GetFootprints():
            self.assertEqual(fp.GetFieldText('Label'), 'PIN1')
            self.assertTrue(fp.GetField('Label').IsVisible())
            if fp.GetReference() == prints[1].GetReference():
                self.assertEqual(fp.GetField('Label').GetPosition(), next(iter(fp.Pads())).GetPosition())
        self.assertEqual(write_scope(before, out.read_bytes())['status'], 'allowed')

    def test_minimum_and_side_readability_fail_closed_without_mutation(self):
        import pcbnew as k
        source = self.board()
        out = self.output(source)
        b = k.LoadBoard(str(out))
        fields = sorted((fp.Reference() for fp in b.GetFootprints()), key=lambda f: f.GetText())
        fields[0].SetTextSize(k.VECTOR2I(500000, 500000))
        fields[1].SetMirrored(True)
        k.SaveBoard(str(out), b)
        original = out.read_bytes()
        drc = _native_drc(out, out.parent)
        result = relocate(b, drc['violations'])
        self.assertEqual({d['reason'] for d in result['search_diagnostics']},
                         {'text_below_declared_minimum', 'text_side_readability'})
        self.assertEqual(result['moved_fields'], [])
        k.SaveBoard(str(out), b)
        self.assertEqual(out.read_bytes(), original)

    def test_ignored_silk_is_unknown_tool_coverage(self):
        source = self.board()
        # Use a new project identity before loading its settings.
        path = source.with_name('ignored.kicad_pcb')
        path.write_bytes(source.read_bytes())
        path.with_suffix('.kicad_pro').write_text(json.dumps({'board': {'design_settings': {
            'rule_severities': {'silk_overlap': 'ignore'}}}}))
        with self.assertRaisesRegex(ValueError, 'explicitly ignored'):
            _native_drc(path, self.root)

    def test_unchanged_baseline_silk_defect_is_retained(self):
        source = self.board()
        out = self.output(source)
        source.write_bytes(out.read_bytes())
        original = out.read_bytes()
        _, delta = _proposal_drc(source, out)
        self.assertEqual(delta['added_findings'], [])
        self.assertEqual(out.read_bytes(), original)
        report = json.loads((out.parent / 'native-drc.json').read_text())
        self.assertGreater(sum(v['type'] == 'silk_over_copper' for v in report['violations']), 0)


class SilkScopeTest(unittest.TestCase):
    def test_only_silk_positions_are_writable(self):
        from test_placement_adapter import TEXT, mutate
        before = TEXT.encode()
        moved = mutate(('(at 0 -1.5 0)', '(at 1 -2 0)'))
        proof = write_scope(before, moved)
        self.assertEqual(proof['status'], 'allowed')
        self.assertEqual(proof['family'], 'agent-ee.placement-transforms/4')
        self.assertEqual(proof['silkscreen_relocations'], [{'footprint': '11111111-1111-4111-8111-111111111111',
                                                          'field': 'a1', 'name': 'Reference'}])
        for old, new in [('(at 0 -1.5 0)', '(at 1 -2 90)'),
                         ('(size 1 1)', '(size 0.5 0.5)'),
                         ('"Reference" "R1"', '"Reference" "WRONG"'),
                         ('"F.SilkS"', '"B.SilkS"')]:
            after = before.replace(old.encode(), new.encode(), 1)
            self.assertEqual(write_scope(before, after)['status'], 'refused', (old, new))
        self.assertEqual(write_scope(before, moved.replace(b'(size 1 1)', b'(size 1 1)) (hide yes', 1))['status'], 'refused')
        self.assertEqual(movable_names('["Label"]'), {'Reference', 'Value', 'Label'})
        for value in ('{}', '[1]', '["Label", "Label"]', '["agent_ee_movable_silk_fields"]'):
            with self.assertRaises(ValueError):
                movable_names(value)

    def test_custom_fields_require_explicit_footprint_declaration(self):
        from test_placement_adapter import TEXT
        field = ('(property "Label" "PIN1" (at 0 2 0) (layer "F.SilkS") '
                 '(uuid "custom-label") (effects (font (size 1 1))))')
        before = TEXT.replace('  (path "/r1")', field + '  (path "/r1")')
        after = before.replace('(at 0 2 0)', '(at 1 3 0)')
        self.assertEqual(write_scope(before.encode(), after.encode())['status'], 'refused')
        declaration = ('(property "agent_ee_movable_silk_fields" ' + json.dumps('["Label"]') +
                       ' (at 0 0) (layer "F.Fab") (uuid "declaration") '
                       '(effects (font (size 1 1)) (hide yes)))')
        before = before.replace('  (path "/r1")', declaration + '  (path "/r1")')
        after = before.replace('(at 0 2 0)', '(at 1 3 0)')
        self.assertEqual(write_scope(before.encode(), after.encode())['status'], 'allowed')

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

    def test_native_outline_holes_relocate_and_contain(self):
        import pcbnew as k
        from agent_ee.silk_artwork import contained_markings
        from agent_ee import placement_defects as pd
        for footprint_owned in (False, True):
            for narrow in (False, True):
                with self.subTest(footprint_owned=footprint_owned, narrow=narrow):
                    board = k.BOARD()
                    fp = k.FOOTPRINT(board)
                    board.Add(fp)
                    fp.SetReference('R1')
                    fp.Value().SetVisible(False)
                    field = fp.Reference()
                    field.SetLayer(k.F_SilkS)
                    field.SetTextSize(k.VECTOR2I(1000000, 1000000))
                    field.SetTextThickness(150000)
                    def rect(owner, x0, y0, x1, y1):
                        points = [(x0,y0), (x1,y0), (x1,y1), (x0,y1)]
                        for a, b in zip(points, points[1:] + points[:1]):
                            edge = k.PCB_SHAPE(owner)
                            edge.SetShape(k.SHAPE_T_SEGMENT)
                            edge.SetLayer(k.Edge_Cuts)
                            edge.SetWidth(50000)
                            edge.SetStart(k.VECTOR2I(*a))
                            edge.SetEnd(k.VECTOR2I(*b))
                            owner.Add(edge)
                    rect(board, 0, 0, 30000000, 30000000)
                    owner = fp if footprint_owned else board
                    rect(owner, 14000000, 10000000,
                         14500000 if narrow else 18000000, 20000000)
                    uid = field.m_Uuid.AsString()
                    for label, x, y, expected in (
                            ('inside', 5000000, 5000000, True),
                            ('cutout', 14250000, 15000000, False),
                            ('straddle', 14000000, 15000000, False),
                            ('offboard', -5000000, 5000000, False)):
                        field.SetPosition(k.VECTOR2I(x, y))
                        self.assertEqual(uid in contained_markings(board), expected, label)
                    field.SetPosition(k.VECTOR2I(14000000, 15000000))
                    path = self.root / 'holes.kicad_pcb'
                    k.SaveBoard(str(path), board)
                    original = pd._identities(path)
                    defect = dict(type='placement_silkscreen_conflict', status='open',
                                  verdict='defect', native=dict(items=[dict(uuid=uid)]))
                    report = relocate(board, [dict(type='silk_overlap', items=[dict(uuid=uid)])])
                    self.assertEqual(report['search_diagnostics'], [])
                    self.assertEqual(len(report['moved_fields']), 1)
                    k.SaveBoard(str(path), board)
                    from agent_ee.silk_artwork import containment_witness
                    current = pd._identities(path, containment_witness(path))
                    self.assertIn(uid, current.contained())
                    self.assertTrue(pd._closes(defect, original, current, set(), set(), True))

    def test_outline_topology_and_box_hole_boundaries(self):
        import pcbnew as k
        from agent_ee.silk_artwork import _outline_with_holes, _box_contained
        from agent_ee.placement_probe import primitive, prove_contours
        for case, rectangles, qualified in (
                ('two_holes', [(0,0,30,30), (5,5,8,8), (20,20,22,22)], True),
                ('islands', [(0,0,10,10), (20,20,30,30)], False),
                ('touching', [(0,0,30,30), (0,5,8,8)], False),
                ('crossing', [(0,0,30,30), (28,5,32,8)], False),
                ('nested_holes', [(0,0,30,30), (5,5,20,20), (8,8,10,10)], False),
                ('overlapping_holes', [(0,0,30,30), (5,5,10,10), (8,8,12,12)], False)):
            with self.subTest(case=case):
                board = k.BOARD()
                for x0, y0, x1, y1 in rectangles:
                    shape = k.PCB_SHAPE(board)
                    shape.SetShape(k.SHAPE_T_RECT)
                    shape.SetLayer(k.Edge_Cuts)
                    shape.SetStart(k.VECTOR2I(x0, y0))
                    shape.SetEnd(k.VECTOR2I(x1, y1))
                    board.Add(shape)
                outline = _outline_with_holes(board)
                self.assertEqual(outline is not None, qualified)
                # The default parser must retain its placement verdict boundary.
                legacy = prove_contours([primitive(s) for s in board.GetDrawings()], 'missing', 'unknown')
                self.assertEqual(legacy['status'], 'proven' if case == 'islands' else 'unknown')
                if qualified:
                    for box, expected in (
                            ([(1,1),(3,1),(3,3),(1,3)], True),
                            ([(4,4),(9,4),(9,9),(4,9)], False),  # encloses whole hole
                            ([(4,5),(5,5),(5,6),(4,6)], False),  # touches hole
                            ([(6,6),(7,6),(7,7),(6,7)], False),
                            ([(4,6),(9,6),(9,7),(4,7)], False)):
                        self.assertEqual(_box_contained(box, outline), expected)
                shape = k.PCB_SHAPE(board)
                shape.SetShape(k.SHAPE_T_SEGMENT)
                shape.SetLayer(k.Edge_Cuts)
                shape.SetStart(k.VECTOR2I(40,40))
                shape.SetEnd(k.VECTOR2I(41,41))
                board.Add(shape)
                self.assertIsNone(_outline_with_holes(board))

    def test_curved_outline_bounds_are_conservative(self):
        import pcbnew as k
        from agent_ee.silk_artwork import _outline_with_holes, _box_contained, contained_markings
        from agent_ee.placement_probe import primitive, prove_contours
        M = 1000000
        def add(board, kind, *points, radius=0):
            shape = k.PCB_SHAPE(board)
            shape.SetShape(kind)
            shape.SetLayer(k.Edge_Cuts)
            if kind == k.SHAPE_T_ARC:
                shape.SetArcGeometry(*(k.VECTOR2I(*p) for p in points))
            elif kind == k.SHAPE_T_CIRCLE:
                (cx, cy), = points
                shape.SetCenter(k.VECTOR2I(cx, cy))
                shape.SetEnd(k.VECTOR2I(cx + radius, cy))
            else:
                shape.SetStart(k.VECTOR2I(*points[0]))
                shape.SetEnd(k.VECTOR2I(*points[1]))
                if radius:
                    shape.SetCornerRadius(radius)
            board.Add(shape)
        def box(x0, y0, x1, y1):
            return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        c, r = 15*M, 1600000
        def round_hole(b):
            add(b, k.SHAPE_T_RECT, (0, 0), (30*M, 30*M))
            add(b, k.SHAPE_T_CIRCLE, (c, c), radius=r)
        def oblong(b):
            add(b, k.SHAPE_T_RECT, (0, 0), (30*M, 30*M))
            add(b, k.SHAPE_T_SEGMENT, (10*M, 14*M), (20*M, 14*M))
            add(b, k.SHAPE_T_SEGMENT, (20*M, 16*M), (10*M, 16*M))
            add(b, k.SHAPE_T_ARC, (20*M, 14*M), (21*M, 15*M), (20*M, 16*M))
            add(b, k.SHAPE_T_ARC, (10*M, 16*M), (9*M, 15*M), (10*M, 14*M))
        def rounded(b):
            add(b, k.SHAPE_T_RECT, (0, 0), (30*M, 30*M), radius=3*M)
            add(b, k.SHAPE_T_RECT, (10*M, 10*M), (20*M, 20*M), radius=2*M)
        def square_hole(b):
            add(b, k.SHAPE_T_RECT, (0, 0), (30*M, 30*M))
            add(b, k.SHAPE_T_RECT, (10*M, 10*M), (20*M, 20*M))
        def disc(b):
            add(b, k.SHAPE_T_CIRCLE, (c, c), radius=15*M)
            add(b, k.SHAPE_T_CIRCLE, (c, c), radius=2*M)
        for case, build, probes in (
                ('round_hole', round_hole, (
                    ('clear', box(M, M, 3*M, 3*M), True),
                    ('tangent', box(c+r, c-M, c+r+M, c+M), False),
                    ('within_tolerance', box(c+r+1, c-M, c+r+M, c+M), False),
                    ('beyond_tolerance', box(c+r+5001, c-M, c+r+M, c+M), True),
                    ('inside_hole', box(c-M//10, c-M//10, c+M//10, c+M//10), False),
                    ('straddle', box(c+r-1000, c-M, c+r+M, c+M), False),
                    ('diagonal_clear', box(c+1131400, c+1131400, c+3*M, c+3*M), True),
                    ('encloses', box(c-2*M, c-2*M, c+2*M, c+2*M), False))),
                ('oblong_slot', oblong, (
                    ('tip_tangent', box(21*M, 14*M, 22*M, 16*M), False),
                    ('tip_straddle', box(21*M-1, 14*M, 22*M, 16*M), False),
                    ('tip_clear', box(21*M+5001, 14*M, 22*M, 16*M), True),
                    ('side_clear', box(12*M, 16*M+1, 13*M, 17*M), True))),
                ('rounded_rectangles', rounded, (
                    ('outer_corner_cut', box(0, 0, M, M), False),
                    ('outer_corner_kept', box(M, M, 2*M, 2*M), True),
                    # The cutout's rounded corner frees a square its sharp corner would block.
                    ('cutout_corner_freed', box(9*M, 9*M, 10*M+500000, 10*M+500000), True),
                    ('cutout_side', box(9*M, 14*M, 10*M, 15*M), False))),
                ('square_hole', square_hole, tuple(
                    (f'touch_{side}', box(*corners), False) for side, corners in (
                        ('left', (9*M, 12*M, 10*M, 13*M)), ('right', (20*M, 12*M, 21*M, 13*M)),
                        ('top', (12*M, 9*M, 13*M, 10*M)), ('bottom', (12*M, 20*M, 13*M, 21*M)))) + (
                    ('apart', box(20*M+1, 12*M, 21*M, 13*M), True),)),
                ('disc_board', disc, (
                    ('near_rim', box(29*M-20000, 14*M, 29*M, 15*M), True),
                    # On the true rim, inside the outer bound: only the subset ring refuses.
                    ('rim_bulge', box(c+15*M-M, c-1000, c+15*M+1, c+1000), False),
                    ('rim_tangent', box(29*M, 14*M, 30*M, 16*M), False)))):
            with self.subTest(case=case):
                board = k.BOARD()
                build(board)
                outline = _outline_with_holes(board)
                self.assertIsNotNone(outline)
                for label, corners, expected in probes:
                    self.assertEqual(_box_contained(corners, outline), expected, label)
                # Placement grading keeps refusing curved outlines.
                legacy = prove_contours([primitive(s) for s in board.GetDrawings()], 'missing', 'unknown')
                self.assertEqual(legacy['status'], 'unknown')
        for case, build in (
                ('touching_circles', lambda b: (add(b, k.SHAPE_T_RECT, (0, 0), (30*M, 30*M)),
                                                add(b, k.SHAPE_T_CIRCLE, (10*M, 10*M), radius=M),
                                                add(b, k.SHAPE_T_CIRCLE, (12*M, 10*M), radius=M))),
                ('circle_touches_edge', lambda b: (add(b, k.SHAPE_T_RECT, (0, 0), (30*M, 30*M)),
                                                   add(b, k.SHAPE_T_CIRCLE, (M, 10*M), radius=M))),
                ('below_tolerance', lambda b: (add(b, k.SHAPE_T_RECT, (0, 0), (30*M, 30*M)),
                                               add(b, k.SHAPE_T_CIRCLE, (c, c), radius=5000))),
                ('over_rounded', lambda b: (add(b, k.SHAPE_T_RECT, (0, 0), (30*M, 30*M)),
                                            add(b, k.SHAPE_T_RECT, (10*M, 10*M), (14*M, 20*M), radius=2*M))),
                ('open_arc', lambda b: (add(b, k.SHAPE_T_RECT, (0, 0), (30*M, 30*M)),
                                        add(b, k.SHAPE_T_ARC, (10*M, 10*M), (11*M, 11*M), (12*M, 10*M)))),
                ('hole_tangent_to_rim', lambda b: (add(b, k.SHAPE_T_CIRCLE, (c, c), radius=15*M),
                                                   add(b, k.SHAPE_T_CIRCLE, (c+13*M, c), radius=2*M))),
                # Disjoint from the rim, but closer than the bounds can separate.
                ('hole_near_rim', lambda b: (add(b, k.SHAPE_T_CIRCLE, (c, c), radius=15*M),
                                             add(b, k.SHAPE_T_CIRCLE, (c+13*M-1000, c), radius=2*M))),
                ('circle_islands', lambda b: (add(b, k.SHAPE_T_CIRCLE, (5*M, 5*M), radius=2*M),
                                              add(b, k.SHAPE_T_CIRCLE, (20*M, 5*M), radius=2*M)))):
            with self.subTest(case=case):
                board = k.BOARD()
                build(board)
                self.assertIsNone(_outline_with_holes(board))
        # Native field bounds against a round hole, board- and footprint-owned.
        for footprint_owned in (False, True):
            with self.subTest(native=footprint_owned):
                board = k.BOARD()
                fp = k.FOOTPRINT(board)
                board.Add(fp)
                fp.Value().SetVisible(False)
                field = fp.Reference()
                field.SetLayer(k.F_SilkS)
                field.SetTextSize(k.VECTOR2I(M, M))
                field.SetTextThickness(150000)
                add(board, k.SHAPE_T_RECT, (0, 0), (30*M, 30*M))
                add(fp if footprint_owned else board, k.SHAPE_T_CIRCLE, (c, c), radius=r)
                uid = field.m_Uuid.AsString()
                for label, x, expected in (('clear', 5*M, True), ('over_hole', c, False),
                                           ('straddle', c + r, False)):
                    field.SetPosition(k.VECTOR2I(x, c))
                    self.assertEqual(uid in contained_markings(board), expected, label)

    def test_independent_contours_keep_resource_limit(self):
        from agent_ee.placement_probe import prove_contours
        rings = [dict(kind='polygon', points=[[i*3,0],[i*3+1,0],[i*3+1,1],[i*3,1]])
                 for i in range(65)]
        self.assertEqual(prove_contours(rings, 'missing', 'unknown', separate_contours=True)['status'],
                         'unknown')
        self.assertEqual(prove_contours(rings[:64], 'missing', 'unknown', separate_contours=True)['status'],
                         'proven')

    def test_artwork_save_preserves_moved_baseline_collision_refusal(self):
        import pcbnew as k
        import test_placer_through_holes as pth_fixture
        source = pth_fixture.ThroughHolePlacementTest.board(self)
        board = k.LoadBoard(str(source))
        front = next(fp for fp in board.GetFootprints() if fp.GetReference() == 'U1')
        pad = next(iter(front.Pads()))
        layers = pad.GetLayerSet()
        layers.AddLayer(k.F_Mask)
        pad.SetLayerSet(layers)
        field = front.Reference()
        field.SetLayer(k.F_SilkS)
        field.SetTextSize(k.VECTOR2I(1000000, 1000000))
        field.SetTextThickness(150000)
        field.SetPosition(k.VECTOR2I(3000000, 6000000))
        k.SaveBoard(str(source), board)
        for fp in board.GetFootprints():
            fp.Move(k.VECTOR2I(1000000, 0))
        field.SetPosition(pad.GetPosition())
        work = self.root / 'mixed-gates'
        work.mkdir()
        output = work / 'candidate.kicad_pcb'
        k.SaveBoard(str(output), board)
        with self.assertRaisesRegex(ValueError, 'introduced native DRC'):
            _proposal_drc(source, output)
        artwork = json.loads((work / 'placement-silkscreen.json').read_text())
        self.assertTrue(artwork['moved_fields'])
        self.assertEqual(artwork['regressions'], [])
        delta = json.loads((work / 'native-drc-comparison.json').read_text())
        self.assertTrue(any(v['type'] == 'pth_inside_courtyard'
                            for v in delta['blocking_findings']))

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
        _, delta = _proposal_drc(source, out)
        self.assertTrue(delta['open_defects'])
        self.assertTrue(all(f['verdict'] == 'defect' and f['status'] == 'open'
                            for f in delta['open_defects']))
        art = json.loads((out.parent / 'placement-silkscreen.json').read_text())
        self.assertEqual(art['moved_fields'], [])
        self.assertEqual(len(art['search_diagnostics']), 2)
        self.assertEqual({d['reason'] for d in art['search_diagnostics']}, {'footprint_side_flipped'})
        self.assertEqual(len([f for f in art['findings']
                              if f.get('reason') == 'footprint_side_flipped']), 2)
        self.assertTrue(all(f['type'] == 'placement_silkscreen_conflict' for f in art['findings']))
        # Only the lineage stamp of the carried result is added (AGENTEE-351); every other byte is kept.
        from agent_ee.placement_defects import STAMP, carry_digest
        block = f'\t(title_block\n\t\t(comment 9 "{STAMP} {carry_digest(delta["open_defects"])}")\n\t)\n'.encode()
        self.assertEqual(out.read_bytes().count(block), 1)
        self.assertEqual(out.read_bytes().replace(block, b''), original)

    def test_flipped_conflict_is_carried_even_if_other_field_moves_clear(self):
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
        _, delta = _proposal_drc(source, out)
        self.assertTrue(delta['open_defects'])
        self.assertTrue(all(f['verdict'] == 'defect' and f['status'] == 'open'
                            for f in delta['open_defects']))
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

    def test_artwork_comparisons_retain_bound_numeric_sources(self):
        from agent_ee.design_rule_provenance import bind_project, import_constraints
        from agent_ee.storage import json_bytes, write_json
        import pcbnew as k
        original = self.board()
        board = k.LoadBoard(str(original))
        text = k.PCB_TEXT(board)
        text.SetText('Original undersized board marking')
        text.SetLayer(k.F_SilkS)
        text.SetTextSize(k.VECTOR2I(500000, 500000))
        text.SetTextThickness(100000)
        text.SetPosition(k.VECTOR2I(5000000, 8000000))
        board.Add(text)
        k.SaveBoard(str(original), board)
        for cls in ('default', 'owner', 'manufacturer', 'datasheet', 'engineering'):
            with self.subTest(provenance=cls):
                root = self.root / cls
                root.mkdir()
                source = root / original.name
                source.write_bytes(original.read_bytes())
                declared = {'schema': 'fome-mc.constraint-provenance/1',
                            'board_setup_rules': {'min_text_height': {
                                'value_mm': 0.8, 'class': cls, 'basis': 'Original artwork test limit'}}}
                project, record = import_constraints({'board': {'design_settings': {
                    'rules': {'min_text_height': 0.8}}}}, json_bytes(declared),
                    'original-limits.json', 'original-test-source')
                bind_project(project, record)
                write_json(source.with_suffix('.kicad_pro'), project)
                # output() uses self.root for its unique proposal directory.
                self.root = root
                try:
                    out = self.output(source)
                finally:
                    self.root = root.parent
                _, delta = _proposal_drc(source, out)
                artwork = json.loads((out.parent / 'placement-silkscreen.json').read_text())
                self.assertTrue(artwork['moved_fields'])
                for findings in (delta['baseline_findings'], delta['candidate_findings']):
                    heights = [v for v in findings if v['type'] == 'text_height']
                    self.assertTrue(heights, delta)
                    for finding in heights:
                        provenance = finding['constraint_provenance']
                        self.assertEqual(provenance['class'], cls)
                        self.assertEqual(provenance['enforcement'],
                                         'advisory' if cls == 'default' else 'hard')
                        self.assertEqual(provenance['citation']['path'], 'original-limits.json')
                self.assertEqual(delta['blocking_findings'], [])

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
        _, delta = _proposal_drc(source, out)
        self.assertTrue(delta['open_defects'])
        self.assertTrue(all(f['verdict'] == 'defect' and f['status'] == 'open'
                            for f in delta['open_defects']))
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
        _, delta = _proposal_drc(source, out)
        self.assertTrue(delta['open_defects'])
        self.assertTrue(all(f['verdict'] == 'defect' and f['status'] == 'open'
                            for f in delta['open_defects']))
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

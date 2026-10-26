"""Owned native mask apertures preserve resolved expansion during capture."""
from agent_ee import placement_job as job
from agent_ee.placement_probe import extract
from tests.test_placement_job import Fixture, build_board, groups, intent


class MaskGraphicCaptureTests(Fixture):
    def mask_graphic(self, name, *, local_margin=None, board_margin=100000, side='F', turn=0, kind='shape'):
        import pcbnew
        path = self.dir / f'{name}.kicad_pcb'
        build_board(path)
        board = pcbnew.LoadBoard(str(path))
        board.GetDesignSettings().m_SolderMaskExpansion = board_margin
        fp = next(f for f in board.GetFootprints() if f.GetReference() == 'R1')
        position = pcbnew.VECTOR2I(int(fp.GetPosition().x), int(fp.GetPosition().y))
        fp.SetPosition(pcbnew.VECTOR2I(0, 0))
        if side == 'B':
            fp.SetLayer(pcbnew.B_Cu)
            layers = pcbnew.LSET()
            for layer in (pcbnew.B_Cu, pcbnew.B_Paste, pcbnew.B_Mask):
                layers.AddLayer(layer)
            for pad in fp.Pads():
                pad.SetLayerSet(layers)
            for item in fp.GraphicalItems():
                if item.GetLayer() == pcbnew.F_Fab:
                    item.SetLayer(pcbnew.B_Fab)
        if kind == 'text':
            graphic = pcbnew.PCB_TEXT(fp)
            graphic.SetText('M')
            graphic.SetPosition(pcbnew.VECTOR2I(2000000, 1000000))
            graphic.SetTextSize(pcbnew.VECTOR2I(500000, 500000))
            graphic.SetTextThickness(100000)
            graphic.SetIsKnockout(True)
        else:
            graphic = (pcbnew.PCB_TEXTBOX if kind == 'textbox' else pcbnew.PCB_SHAPE)(fp)
            if kind == 'shape':
                graphic.SetShape(pcbnew.SHAPE_T_RECT)
            else:
                graphic.SetText('M')
            graphic.SetStart(pcbnew.VECTOR2I(2000000, 1000000))
            graphic.SetEnd(pcbnew.VECTOR2I(4000000, 3000000))
            graphic.SetWidth(100000)
        graphic.SetLayer(pcbnew.F_Mask if side == 'F' else pcbnew.B_Mask)
        if local_margin is not None:
            graphic.SetLocalSolderMaskMargin(local_margin)
        fp.Add(graphic)
        fp.SetPosition(position)
        fp.SetOrientationDegrees(90 * turn)
        self.assertTrue(pcbnew.SaveBoard(str(path), board))
        path.with_suffix('.kicad_pro').write_text('{}\n')
        return path, board, fp

    def test_mask_graphic_native_expansion_once_across_sides_and_turns(self):
        # 2x2 mm rectangle with 0.1 mm stroke; negative expansion never shrinks capture.
        cases = ((100000, [1850000, 850000, 4150000, 3150000]),
                 (0, [1950000, 950000, 4050000, 3050000]),
                 (-25000, [1950000, 950000, 4050000, 3050000]))
        for margin, expected in cases:
            for side in ('F', 'B'):
                for turn in range(4):
                    with self.subTest(margin=margin, side=side, turn=turn):
                        path, board, fp = self.mask_graphic(f'inherited-{margin}-{side}-{turn}',
                                                          board_margin=margin, side=side, turn=turn)
                        problem, _ = job.capture(path, job.normalize_intent(intent(), groups()))
                        row = next(f for f in problem['footprints'] if f['native_ref'] == 'R1')
                        self.assertEqual(row['mask_local_nm'][-1], expected)
                        self.assertEqual(len(row['mask_local_nm']), 3)

    def test_live_native_graphic_override_and_nonpositive_conservatism(self):
        # KiCad 10 does not serialize local margins on explicit mask-layer graphics.
        # Exercise the real native API/classifier without pretending the file kept them.
        cases = ((50000, [11900000, 6900000, 14100000, 9100000]),
                 (0, [11950000, 6950000, 14050000, 9050000]),
                 (-25000, [11950000, 6950000, 14050000, 9050000]))
        for margin, expected in cases:
            for side in ('F', 'B'):
                with self.subTest(margin=margin, side=side):
                    path, board, fp = self.mask_graphic(f'local-{margin}-{side}',
                                                       local_margin=margin, side=side)
                    captured = next(f for f in extract(path)['footprints'] if f['ref'] == 'R1')
                    actual = job.classify_footprint(fp, captured)
                    self.assertEqual(actual['mask'][-1], expected)
                    self.assertEqual(len(actual['mask']), 3)

    def test_mask_text_and_textbox_keep_effective_native_bounds(self):
        import pcbnew
        for kind in ('text', 'textbox'):
            for side in ('F', 'B'):
                with self.subTest(kind=kind, side=side):
                    path, board, fp = self.mask_graphic(f'{kind}-{side}', kind=kind,
                                                       side=side,
                                                       local_margin=50000 if kind == 'textbox' else None)
                    board = pcbnew.LoadBoard(str(path))
                    fp = next(f for f in board.GetFootprints() if f.GetReference() == 'R1')
                    graphic = next(g for g in fp.GraphicalItems()
                                   if g.GetLayer() in (pcbnew.F_Mask, pcbnew.B_Mask))
                    box, origin = graphic.GetBoundingBox(), fp.GetPosition()
                    expected = [int(box.GetLeft() - origin.x), int(box.GetTop() - origin.y),
                                int(box.GetRight() - origin.x), int(box.GetBottom() - origin.y)]
                    problem, _ = job.capture(path, job.normalize_intent(intent(), groups()))
                    row = next(f for f in problem['footprints'] if f['native_ref'] == 'R1')
                    self.assertEqual(row['mask_local_nm'][-1], expected)
                    self.assertEqual(len(row['mask_local_nm']), 3)

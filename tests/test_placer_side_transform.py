"""Declared footprint side changes in the disposable placement writer (AGENTEE-207 R5)."""
import hashlib
import unittest

import test_placer_native as native


class SideTransformTest(unittest.TestCase):
    # The native placer fixture's setup only; its own tests are not inherited.
    setUpClass = classmethod(native.NativePlacerTest.setUpClass.__func__)
    setUp = native.NativePlacerTest.setUp

    def plan(self, side=None, fixed_c1=False, ref='C1'):
        import pcbnew as k
        placements = []
        for fp in k.LoadBoard(str(self.source)).GetFootprints():
            entry = {'ref': fp.GetReference(), 'uuid': fp.m_Uuid.AsString(),
                     'position_nm': [fp.GetPosition().x, fp.GetPosition().y],
                     'rotation_deg': fp.GetOrientationDegrees(), 'fixed': fp.GetReference() == 'U1' or (fixed_c1 and fp.GetReference() == 'C1')}
            if side is not None and fp.GetReference() == ref:
                entry['side'] = side
            placements.append(entry)
        return {'placements': placements}

    def authorization(self, side='B', ref='C1', sha=None):
        import pcbnew as k
        fp = next(f for f in k.LoadBoard(str(self.source)).GetFootprints() if f.GetReference() == ref)
        return {'schema': 'agent-ee.side-changes/1', 'source_sha256': sha or hashlib.sha256(self.saved).hexdigest(),
                'changes': [{'ref': ref, 'uuid': fp.m_Uuid.AsString(), 'side': side}]}

    def test_declared_flip_survives_save_and_reload_with_the_transformed_inventory(self):
        import pcbnew as k
        from agent_ee import placer_runner as runner
        plan = self.plan('B')
        changes = runner._side_changes(self.source, self.authorization(), plan)
        out = self.scratch / 'flipped.kicad_pcb'
        runner._apply_plan(k.LoadBoard(str(self.source)), plan, out, changes)
        saved = {fp.GetReference(): fp for fp in k.LoadBoard(str(out)).GetFootprints()}
        self.assertTrue(saved['C1'].IsFlipped())
        self.assertFalse(saved['U1'].IsFlipped())
        expected = {**runner._pad_inventory(k.LoadBoard(str(self.source))), **runner._flipped_inventory(self.source, changes)}
        self.assertEqual(runner._pad_inventory(k.LoadBoard(str(out))), expected)
        original = runner._pad_inventory(k.LoadBoard(str(self.source)))
        uuid = next(iter(changes))
        self.assertNotEqual(expected[uuid], original[uuid])  # layers mirrored, so the raw inventory changed
        self.assertEqual([p[:3] for p in expected[uuid]['pads']], [p[:3] for p in original[uuid]['pads']])  # uuid, number, net
        self.assertEqual(self.source.read_bytes(), self.saved)  # the baseline is never flipped

    def test_undeclared_wrong_side_fixed_or_foreign_flips_are_refused(self):
        from agent_ee import placer_runner as runner
        cases = [
            (None, self.plan('B'), 'not authorized'),                           # plan cannot authorize itself
            (self.authorization(), self.plan(), 'differs from its authorized'),   # authorized but undeclared
            (self.authorization(), self.plan('F'), 'differs from its authorized'),  # wrong side
            (self.authorization('F'), self.plan('F'), 'keeps its original side'),
            (self.authorization(), self.plan('B', fixed_c1=True), 'differs from its authorized'),  # fixed part
            (self.authorization(sha='b' * 64), self.plan('B'), 'another board'),
            ({**self.authorization(), 'extra': 1}, self.plan('B'), 'side-change authorization must'),
        ]
        for authorization, plan, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    runner._side_changes(self.source, authorization, plan)
        wrong = self.authorization()
        wrong['changes'][0]['ref'] = 'U1'
        with self.assertRaisesRegex(ValueError, 'exact original reference and UUID'):
            runner._side_changes(self.source, wrong, self.plan('B'))

    def test_corruption_on_a_declared_flip_and_on_unflipped_parts_is_refused(self):
        import pcbnew as k
        from agent_ee import placer_runner as runner
        plan = self.plan('B')
        changes = runner._side_changes(self.source, self.authorization(), plan)
        expected = {**runner._pad_inventory(k.LoadBoard(str(self.source))), **runner._flipped_inventory(self.source, changes)}
        for ref, mutate in (('C1', lambda pad: pad.SetNumber('9')), ('C1', lambda pad: pad.SetShape(k.PAD_SHAPE_CIRCLE)),
                            ('C1', lambda pad: pad.SetNetCode(0)), ('U1', lambda pad: pad.SetNumber('9'))):
            with self.subTest(ref=ref):
                out = self.scratch / f'corrupt-{ref}-{len(list(self.scratch.iterdir()))}.kicad_pcb'
                runner._apply_plan(k.LoadBoard(str(self.source)), plan, out, changes)
                board = k.LoadBoard(str(out))
                pad = next(iter(next(fp for fp in board.GetFootprints() if fp.GetReference() == ref).Pads()))
                mutate(pad)
                k.SaveBoard(str(out), board)
                self.assertNotEqual(runner._pad_inventory(k.LoadBoard(str(out))), expected)

    def test_no_side_changes_keeps_the_original_behaviour(self):
        import pcbnew as k
        from agent_ee import placer_runner as runner
        plan = self.plan()
        self.assertEqual(runner._side_changes(self.source, None, plan), {})
        self.assertEqual(runner._flipped_inventory(self.source, {}), {})
        out = self.scratch / 'unchanged.kicad_pcb'
        runner._apply_plan(k.LoadBoard(str(self.source)), plan, out)
        self.assertEqual(runner._pad_inventory(k.LoadBoard(str(out))), runner._pad_inventory(k.LoadBoard(str(self.source))))
        self.assertFalse(any(fp.IsFlipped() for fp in k.LoadBoard(str(out)).GetFootprints()))


if __name__ == '__main__':
    unittest.main()

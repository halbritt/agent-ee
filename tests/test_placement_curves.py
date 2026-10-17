"""Curved primitives must never acquire straight-polygon qualification."""
from pathlib import Path
import tempfile
import unittest

from agent_ee.placement import judge
from agent_ee.placement_probe import extract
import test_placement_native as native_fixtures
from toolchain import require_toolchain


class NativeCurveQualificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def test_saved_rectangle_with_corner_radius_is_unproven(self):
        import pcbnew as k
        with tempfile.TemporaryDirectory(prefix='agent-ee-placement-radius-') as temporary:
            fixture = native_fixtures.NativePlacementTest()
            fixture.root = Path(temporary)
            path = fixture.board()
            board = k.LoadBoard(str(path))
            for shape in list(board.GetDrawings()):
                board.Remove(shape)
            rounded = k.PCB_SHAPE(board)
            rounded.SetShape(k.SHAPE_T_RECT)
            rounded.SetLayer(k.Edge_Cuts)
            rounded.SetStart(k.VECTOR2I(0,0))
            rounded.SetEnd(k.VECTOR2I(20000000,20000000))
            rounded.SetCornerRadius(1000000)
            board.Add(rounded)
            k.SaveBoard(str(path), board)
            facts = extract(path)
            self.assertEqual(facts['outline']['status'], 'outline_unproven')
            self.assertEqual(judge(facts)['verdict'], 'unknown')

    def test_saved_courtyard_with_corner_radius_is_unproven(self):
        import pcbnew as k
        with tempfile.TemporaryDirectory(prefix='agent-ee-courtyard-radius-') as temporary:
            fixture = native_fixtures.NativePlacementTest()
            fixture.root = Path(temporary)
            path = fixture.board()
            board = k.LoadBoard(str(path))
            fp = next(f for f in board.GetFootprints() if f.GetReference() == 'C1')
            for shape in list(fp.GraphicalItems()):
                fp.Remove(shape)
            rounded = k.PCB_SHAPE(fp)
            rounded.SetShape(k.SHAPE_T_RECT)
            rounded.SetLayer(k.F_CrtYd)
            rounded.SetStart(k.VECTOR2I(14000000,2000000))
            rounded.SetEnd(k.VECTOR2I(16000000,4000000))
            rounded.SetCornerRadius(200000)
            fp.Add(rounded)
            k.SaveBoard(str(path), board)
            facts = extract(path)
            cap = next(f for f in facts['footprints'] if f['ref'] == 'C1')
            self.assertEqual(cap['courtyards']['F']['status'], 'courtyard_unproven')
            self.assertEqual(judge(facts)['verdict'], 'unknown')

    def test_two_closed_board_islands_are_explicitly_unproven(self):
        import pcbnew as k
        with tempfile.TemporaryDirectory(prefix='agent-ee-placement-islands-') as temporary:
            fixture = native_fixtures.NativePlacementTest()
            fixture.root = Path(temporary)
            path = fixture.board()
            board = k.LoadBoard(str(path))
            island = k.PCB_SHAPE(board)
            island.SetShape(k.SHAPE_T_RECT)
            island.SetLayer(k.Edge_Cuts)
            island.SetStart(k.VECTOR2I(30000000,30000000))
            island.SetEnd(k.VECTOR2I(40000000,40000000))
            board.Add(island)
            k.SaveBoard(str(path), board)
            facts = extract(path)
            self.assertEqual(facts['outline']['status'], 'outline_unproven')
            self.assertEqual(judge(facts)['verdict'], 'unknown')

    def test_probe_cli_refuses_overwriting_its_source(self):
        from agent_ee.placement_probe import main
        with tempfile.TemporaryDirectory(prefix='agent-ee-placement-output-') as temporary:
            fixture = native_fixtures.NativePlacementTest()
            fixture.root = Path(temporary)
            path = fixture.board()
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                main([str(path), '--out', str(path)])
            self.assertEqual(path.read_bytes(), original)

"""The build's finishing stage consumes routed native files without moving copper."""
from pathlib import Path
import tempfile
import unittest

from agent_ee import routed_artwork
from agent_ee.storage import read_json, write_json
from agent_ee.symbol_library import children, parse_sexpr
import test_routed_artwork as routed_fixture
from build_stage_fixture import requirements as retained_requirements
from toolchain import require_toolchain


class BuildArtworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='ee-build-artwork-')))

    def source(self, **options):
        source = self.root / 'source'
        source.mkdir()
        fixture = routed_fixture.RoutedArtworkTest()
        fixture.root = source
        return fixture.board(**options)

    def execute_stage(self, board):
        attempt = retained_requirements(self.root, board.parent, dict(board=board.name))
        self.stage = attempt.directory
        return attempt.execute({}, board.parent)

    def test_native_stage_clears_labels_preserving_copper_and_all_other_tokens(self):
        board = self.source(back=True)
        before = board.read_bytes()
        candidate = self.execute_stage(board)
        after = (candidate / board.name).read_bytes()
        self.assertEqual(board.read_bytes(), before)
        self.assertNotEqual(after, before)
        report = read_json(self.stage / 'artwork.json')
        self.assertEqual(report['status'], 'repaired')
        self.assertFalse(report['open_defects'])
        proof = routed_artwork.write_scope(before, after, targets=report['targets'])
        self.assertTrue(proof['moved_identities'])
        for tag in ('segment', 'via', 'zone', 'setup'):
            self.assertEqual(children(parse_sexpr(before), tag), children(parse_sexpr(after), tag))
        receipt = read_json(self.stage / 'receipts/0/receipt.json')
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(set(receipt['changed']), {board.name})

    def test_no_movable_field_keeps_graphics_and_hidden_fields_unchanged(self):
        board = self.source(graphic=True)
        before = board.read_bytes()
        candidate = self.execute_stage(board)
        self.assertEqual((candidate / board.name).read_bytes(), before)
        report = read_json(self.stage / 'artwork.json')
        self.assertEqual(report['status'], 'no_movable_fields')
        self.assertFalse((self.stage / 'receipts').exists())
        self.assertFalse(report['acceptance_ready'])

    def test_unresolved_labels_fail_without_hiding_or_resizing_them(self):
        import pcbnew as k
        board = self.source()
        native = k.LoadBoard(str(board))
        for fp in native.GetFootprints():
            fp.Reference().SetTextSize(k.VECTOR2I(100000, 100000))
            fp.Reference().SetPosition(next(iter(fp.Pads())).GetPosition())
        k.SaveBoard(str(board), native, True)
        before = board.read_bytes()
        with self.assertRaisesRegex(ValueError, 'silkscreen conflict'):
            self.execute_stage(board)
        self.assertEqual(board.read_bytes(), before)
        report = read_json(self.stage / 'artwork.json')
        self.assertEqual(report['status'], 'unresolved')
        self.assertTrue(report['open_defects'])
        after = self.stage / 'candidate' / board.name
        routed_artwork.write_scope(before, after.read_bytes(), targets=report['targets'])


if __name__ == '__main__':
    unittest.main()

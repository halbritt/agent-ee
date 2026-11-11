"""AGENTEE-637: parts on opposite sides of a board may share board area, as native DRC allows.

Every board here is an authored synthetic board built through pcbnew.
"""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_ee import placement_job as job
from tests.toolchain import require_toolchain
from tests.test_placement_job import MM, _box
from tests.test_placement_job_clearance import evaluate

PAIR_RULES = ('clearance', 'shorting_items', 'courtyards_overlap', 'solder_mask_bridge', 'hole_clearance')


class TwoSidedPlacementTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.directory = Path(tempfile.mkdtemp(prefix='ee637-sides-'))
        self.addCleanup(shutil.rmtree, self.directory)

    def board(self, name, *, side, through=False):
        """Part T on the front at (10, 10) and part L at (15, 10) on `side`: one 2 x 1 mm pad each
        on its own net and a 3 x 2 mm fab outline. `through` drills T's pad (0.6 mm, all layers)."""
        import pcbnew as k
        board = k.BOARD()
        for edge in _box(k, board, k.Edge_Cuts, 0, 0, 30 * MM, 30 * MM):
            board.Add(edge)

        def part(ref, x, front, drill):
            net = k.NETINFO_ITEM(board, 'N' + ref)
            board.Add(net)
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            fp.SetValue(ref)
            fp.Reference().SetVisible(False)
            fp.Value().SetVisible(False)
            board.Add(fp)
            if not front:
                fp.SetLayer(k.B_Cu)
            pad = k.PAD(fp)
            fp.Add(pad)
            pad.SetNumber('1')
            pad.SetShape(k.PADSTACK.ALL_LAYERS, k.PAD_SHAPE_RECT)
            pad.SetSize(k.PADSTACK.ALL_LAYERS, k.VECTOR2I(2 * MM, MM))
            if drill:
                pad.SetAttribute(k.PAD_ATTRIB_PTH)
                pad.SetDrillSize(k.VECTOR2I(600_000, 600_000))
                pad.SetLayerSet(pad.PTHMask())
            else:
                pad.SetAttribute(k.PAD_ATTRIB_SMD)
                layers = k.LSET()
                for layer in ((k.F_Cu, k.F_Mask) if front else (k.B_Cu, k.B_Mask)):
                    layers.AddLayer(layer)
                pad.SetLayerSet(layers)
            pad.SetNet(net)
            for shape in _box(k, fp, k.F_Fab if front else k.B_Fab, -1_500_000, -1_000_000, 1_500_000, 1_000_000):
                fp.Add(shape)
            fp.SetPosition(k.VECTOR2I(x * MM, 10 * MM))

        part('T', 10, True, through)
        part('L', 15, side == 'F', False)
        path = self.directory / name / 'board.kicad_pcb'
        path.parent.mkdir()
        k.SaveBoard(str(path), board)
        path.with_suffix('.kicad_pro').write_text(json.dumps({
            'board': {'design_settings': {'rules': {'min_clearance': 0.15}}},
            'net_settings': {'classes': [{'name': 'Default', 'clearance': 0.15}]}}))
        return path

    def seat(self, path, x, y):
        """(the engine's pair rules, the native pair errors) with L moved to (x, y) nm."""
        import pcbnew as k
        problem, _ = job.capture(path, job.normalize_intent({}, {'groups': []}))
        rows = {r['native_ref']: r for r in problem['footprints']}
        evaluation = evaluate(problem, [{rows['L']['uuid']: [x, y, 0]}])[0]
        rules = {}
        for violation in evaluation['violations']:
            self.assertEqual(violation['kind'], 'pair', violation)
            rules.update(violation['rules'])
        board = k.LoadBoard(str(path))
        next(f for f in board.GetFootprints() if f.GetReference() == 'L').SetPosition(k.VECTOR2I(x, y))
        target = path.parent / f'seat-{x}-{y}'
        target.mkdir()
        k.SaveBoard(str(target / 'board.kicad_pcb'), board)
        shutil.copyfile(path.with_suffix('.kicad_pro'), target / 'board.kicad_pro')
        report, _ = job.drc(target / 'board.kicad_pcb', target)
        native = sorted({v['type'] for v in report['violations']
                         if v.get('severity') == 'error' and v['type'] in PAIR_RULES})
        return rows, set(rules), native

    def test_capture_states_the_side_and_the_layers(self):
        rows, rules, native = self.seat(self.board('capture', side='B', through=True), 15 * MM, 10 * MM)
        self.assertEqual((rows['T']['side'], rows['L']['side']), ('F', 'B'))
        self.assertEqual(rows['L']['copper_local_nm'][0]['geometry']['layers'], ['B.Cu'])
        self.assertEqual([m['layers'] for m in rows['L']['mask_local_nm']], [['B.Mask']])
        self.assertEqual(set(rows['T']['copper_local_nm'][0]['geometry']['layers']), {'F.Cu', 'B.Cu'})
        self.assertEqual([sorted(m['layers']) for m in rows['T']['mask_local_nm']], [['B.Mask', 'F.Mask']])
        self.assertEqual((rules, native), (set(), []), 'apart, nothing is broken')

    def test_a_back_part_may_lie_under_a_front_part(self):
        # The same spot on the other side: the engine and native DRC both accept it.
        _, rules, native = self.seat(self.board('under', side='B'), 10 * MM, 10 * MM)
        self.assertEqual((rules, native), (set(), []))
        # On the same side both reject it.
        _, rules, native = self.seat(self.board('beside', side='F'), 10 * MM, 10 * MM)
        self.assertLessEqual({'body', 'copper'}, rules)
        self.assertTrue(native, 'native DRC reports the two front pads on one spot')

    def test_a_through_part_still_meets_the_other_side(self):
        path = self.board('through', side='B', through=True)
        # The back pad on the through pad: copper on a shared layer and the drill, for both judges.
        _, rules, native = self.seat(path, 10 * MM, 10 * MM)
        self.assertLessEqual({'copper', 'hole_copper'}, rules)
        self.assertNotIn('body', rules, 'the bodies are on opposite sides')
        self.assertTrue(native)
        # The back part beside the pin with its body over the drill: the engine keeps bodies off
        # the holes of parts on the other side (a pin or a screw passes there). Native DRC has
        # no such rule, so this is the one place the engine is stricter than the native check.
        _, rules, native = self.seat(path, 10 * MM, 11_200_000)
        self.assertEqual((rules, native), ({'body_hole'}, []))
        # Further down the body clears the drill and nothing is broken.
        _, rules, native = self.seat(path, 10 * MM, 12 * MM)
        self.assertEqual((rules, native), (set(), []))


if __name__ == '__main__':
    unittest.main()

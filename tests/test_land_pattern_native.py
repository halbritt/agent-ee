"""Native pcbnew control: owned footprints saved, placed, flipped and reloaded."""
from fractions import Fraction
import hashlib
import math
import sys
import tempfile
import unittest
from pathlib import Path

from agent_ee.footprint_library import canonical
from agent_ee.land_pattern import board_footprint, board_footprints, library_footprint
sys.path.insert(0, str(Path(__file__).resolve().parent))
from land_pattern_fixtures import manifest
from toolchain import require_toolchain


PADS = [  # number, shape, attribute, position, angle, size, drill, offset
    ('1', 'roundrect', 'smd', (1, 2), 90, (1, 0.6), None, None),
    ('2', 'oval', 'pth', (-2, 1), 90, (2, 1), (1.2, 0.6), (0.3, 0.1)),
    ('3', 'rect', 'pth', (2, -1), 0, (1.5, 1.5), (0.8, 0.8), (0.2, 0)),
    ('4', 'circle', 'smd', (-1, -2), 0, (0.8, 0.8), None, None),
    ('', 'circle', 'npth', (0, -3), 0, (1, 1), (1, 1), None),
]
PLACEMENTS = [  # reference, x, y, orientation, flip direction or None
    ('P1', 10, 10, 0, None), ('P2', 30, 10, 90, None), ('P3', 50, 10, 30, None),
    ('P4', 10, 30, 0, 'TOP_BOTTOM'), ('P5', 30, 30, 90, 'TOP_BOTTOM'), ('P6', 50, 30, 30, 'TOP_BOTTOM'),
    ('P7', 70, 30, 0, 'LEFT_RIGHT'),
]


def rotate(point, degrees):
    a = math.radians(degrees)
    x, y = point
    return (x * math.cos(a) + y * math.sin(a), -x * math.sin(a) + y * math.cos(a))


class NativeLandPatternTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-land-pattern-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def build(self):
        import pcbnew as k
        mm = k.FromMM
        board = k.BOARD()
        fp = k.FOOTPRINT(board)
        fp.SetFPID(k.LIB_ID('Owned', 'NATIVE'))
        shapes = dict(rect=k.PAD_SHAPE_RECT, oval=k.PAD_SHAPE_OVAL, circle=k.PAD_SHAPE_CIRCLE,
                      roundrect=k.PAD_SHAPE_ROUNDRECT)
        attributes = dict(smd=k.PAD_ATTRIB_SMD, pth=k.PAD_ATTRIB_PTH, npth=k.PAD_ATTRIB_NPTH)
        for number, shape, attr, pos, angle, size, drill, offset in PADS:
            pad = k.PAD(fp)
            pad.SetNumber(number)
            pad.SetAttribute(attributes[attr])
            pad.SetShape(shapes[shape])
            pad.SetSize(k.VECTOR2I(mm(size[0]), mm(size[1])))
            pad.SetLayerSet({'smd': pad.SMDMask, 'pth': pad.PTHMask, 'npth': pad.UnplatedHoleMask}[attr]())
            if drill:
                if drill[0] != drill[1]:
                    pad.SetDrillShape(k.PAD_DRILL_SHAPE_OBLONG)
                pad.SetDrillSize(k.VECTOR2I(mm(drill[0]), mm(drill[1])))
            if offset:
                pad.SetOffset(k.VECTOR2I(mm(offset[0]), mm(offset[1])))
            if shape == 'roundrect':
                pad.SetRoundRectRadiusRatio(0.25)
            fp.Add(pad)
            pad.SetFPRelativePosition(k.VECTOR2I(mm(pos[0]), mm(pos[1])))
            pad.SetFPRelativeOrientation(k.EDA_ANGLE(angle, k.DEGREES_T))
        fp.SetReference('REF**')
        library = self.root / 'Owned.pretty'
        library.mkdir()
        io = k.PCB_IO_MGR.FindPlugin(k.PCB_IO_MGR.KICAD_SEXP)
        io.FootprintSave(str(library), fp)
        for ref, x, y, angle, flip in PLACEMENTS:
            instance = io.FootprintLoad(str(library), 'NATIVE')
            instance.SetReference(ref)
            board.Add(instance)
            instance.SetPosition(k.VECTOR2I(mm(x), mm(y)))
            instance.SetOrientationDegrees(angle)
            if flip:
                instance.Flip(instance.GetPosition(), getattr(k, 'FLIP_DIRECTION_' + flip))
        k.SaveBoard(str(self.root / 'native.kicad_pcb'), board)
        return k.LoadBoard(str(self.root / 'native.kicad_pcb'))

    def test_saved_instances_return_to_saved_library_frame_and_match_pcbnew_world_geometry(self):
        import pcbnew as k
        reloaded = self.build()
        member = 'Owned.pretty/NATIVE.kicad_mod'
        data = (self.root / member).read_bytes()
        captures = {member: data}
        reference = library_footprint(manifest(captures), captures, 'Owned:NATIVE')
        self.assertEqual(reference['inventory']['ignored']['npth_without_copper'], 1)
        board_bytes = (self.root / 'native.kicad_pcb').read_bytes()
        found = board_footprints(board_bytes)
        self.assertEqual(sorted(found), [p[0] for p in PLACEMENTS])
        pcb = {fp.GetReference(): fp for fp in reloaded.GetFootprints()}
        for ref, _, _, _, flip in PLACEMENTS:
            with self.subTest(ref=ref):
                instance = board_footprint(found, ref, hashlib.sha256(board_bytes).hexdigest())
                self.assertEqual(canonical(instance['pattern']), canonical(reference['pattern']))
                fp = pcb[ref]
                back = fp.GetLayer() == k.B_Cu
                self.assertEqual(back, flip is not None)
                self.assertEqual(instance['source']['side'], 'B.Cu' if back else 'F.Cu')
                origin = k.ToMM(fp.GetPosition())
                angle = fp.GetOrientationDegrees()
                native = {p.GetNumber(): p for p in fp.Pads()}

                def world(point):
                    x, y = (float(Fraction(v)) / 1e6 for v in point)
                    if back:
                        y = -y
                    dx, dy = rotate((x, y), angle)
                    return origin[0] + dx, origin[1] + dy

                for pad in instance['pattern']['pads']:
                    actual = native[pad['number']]
                    wanted = sorted({'F.Cu': 'B.Cu', 'B.Cu': 'F.Cu'}[s] if back else s for s in pad['layers'])
                    side = k.F_Cu if 'F.Cu' in wanted else k.B_Cu
                    centre = k.ToMM(actual.GetEffectiveShape(side).BBox().Centre())
                    expected = world(pad['primitive']['center'])
                    self.assertAlmostEqual(centre[0], expected[0], delta=2e-6)
                    self.assertAlmostEqual(centre[1], expected[1], delta=2e-6)
                    have = sorted(reloaded.GetLayerName(layer) for layer in actual.GetLayerSet().Seq()
                                  if layer in (k.F_Cu, k.B_Cu))
                    self.assertEqual(have, wanted)
                    if pad['primitive']['shape'] == 'roundrect':
                        self.assertAlmostEqual(k.ToMM(actual.GetRoundRectCornerRadius(side)),
                                               float(Fraction(pad['primitive']['radius'])) / 1e6, delta=1e-6)
                native_holes = []
                for p in fp.Pads():
                    if p.GetDrillSize().x:
                        segment = p.GetEffectiveHoleShape().GetSeg()
                        a, b = k.ToMM(segment.A), k.ToMM(segment.B)
                        native_holes.append(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
                native_holes.sort()
                expected = sorted(world(h['center']) for h in instance['pattern']['holes'])
                self.assertEqual(len(native_holes), len(expected))
                for got, want in zip(native_holes, expected):
                    self.assertAlmostEqual(got[0], want[0], delta=2e-6)
                    self.assertAlmostEqual(got[1], want[1], delta=2e-6)


if __name__ == '__main__':
    unittest.main()

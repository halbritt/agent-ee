"""Native SVG geometry and annotations share one saved-board coordinate frame."""
import copy
from pathlib import Path
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

from agent_ee import group_views
from agent_ee.storage import digest, read_json
from tests.toolchain import require_toolchain

SVG = '{http://www.w3.org/2000/svg}'


class GroupViewsTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        import pcbnew as k
        self.k = k
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.board = self.root / 'source/board.kicad_pcb'
        self.board.parent.mkdir()
        self.groups = dict(schema=1, groups=[dict(name='left', anchor='P1', refs=['P1']),
                                           dict(name='right', anchor='P2', refs=['P2'])])
        self.make_board()

    def make_board(self, dx=0, dy=0):
        k = self.k
        board = k.BOARD()
        points = [(10, 20), (40, 20), (40, 35), (10, 35)]
        for a, b in zip(points, points[1:] + points[:1]):
            edge = k.PCB_SHAPE()
            edge.SetShape(k.SHAPE_T_SEGMENT)
            edge.SetLayer(k.Edge_Cuts)
            edge.SetStart(k.VECTOR2I(k.FromMM(a[0] + dx), k.FromMM(a[1] + dy)))
            edge.SetEnd(k.VECTOR2I(k.FromMM(b[0] + dx), k.FromMM(b[1] + dy)))
            edge.SetWidth(k.FromMM(0.05))
            board.Add(edge)
        for ref, x, y, back in [('P1', 14, 24, False), ('P2', 34, 30, True)]:
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            fp.SetPosition(k.VECTOR2I(k.FromMM(x + dx), k.FromMM(y + dy)))
            pad = k.PAD(fp)
            pad.SetNumber('1')
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetShape(k.PAD_SHAPE_RECT)
            pad.SetSize(k.VECTOR2I(k.FromMM(2), k.FromMM(1)))
            pad.SetLayerSet(pad.SMDMask())
            pad.SetPosition(fp.GetPosition())
            fp.Add(pad)
            board.Add(fp)
            if back:
                fp.Flip(fp.GetPosition(), False)
        k.SaveBoard(str(self.board), board)

    def test_native_geometry_side_and_translated_origin_binding(self):
        for index, (dx, dy) in enumerate([(0, 0), (70, -30)]):
            self.make_board(dx, dy)
            before = digest(self.board)
            result = group_views.produce(self.board, self.groups, self.root / f'views{index}')
            self.assertEqual(result['board_sha256'], before)
            self.assertEqual(digest(self.board), before)
            self.assertFalse(result['acceptance_assessed'])
            self.assertEqual(result['unresolved'], [])
            self.assertEqual([m['side'] for m in result['members']], ['F', 'B'])
            self.assertEqual(result['members'][0]['position_mm'], [14 + dx, 24 + dy])
            self.assertEqual(result['members'][1]['position_mm'], [34 + dx, 30 + dy])
            for side, expected, native_x in [('top', [(4, 4), (24, 10)], 4),
                                             ('bottom', [(26, 4), (6, 10)], 24)]:
                view = result['views'][side]
                path = self.root / f'views{index}' / view['path']
                self.assertEqual(digest(path), view['sha256'])
                svg = ET.parse(path).getroot()
                native_group = svg.find('.//' + SVG + 'g[@id="native-board"]')
                self.assertEqual(native_group.attrib['transform'], 'matrix(-1 0 0 1 30.0 0)' if side == 'bottom'
                                 else 'matrix(1 0 0 1 0 0)')
                markers = svg.findall('.//' + SVG + 'circle[@data-ref]')
                self.assertEqual([(float(m.attrib['data-view-x-mm']), float(m.attrib['data-view-y-mm']))
                                  for m in markers], expected)
                # A real plotted pad independently locates the coordinate frame:
                # front pad centre (4,4), back pad centre (24,10) before mirroring.
                raw = ET.parse(self.root / f'views{index}' / view['native']['path']).getroot()
                paths = [p.attrib.get('d', '') for p in raw.iter(SVG + 'path')]
                rectangles = []
                for d in paths:
                    nums = [float(v) for v in re.findall(r'-?\d+\.\d+', d)]
                    if len(nums) == 8:
                        xs, ys = nums[::2], nums[1::2]
                        if abs(max(xs) - min(xs) - 2) < 0.001 and abs(max(ys) - min(ys) - 1) < 0.001:
                            rectangles.append(((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2))
                self.assertIn((native_x, 4 if side == 'top' else 10), rectangles)

    def test_unknown_or_ambiguous_membership_refuses_before_render(self):
        for mutation in ('unknown', 'duplicate', 'anchor'):
            groups = copy.deepcopy(self.groups)
            if mutation == 'unknown': groups['groups'][0]['refs'].append('MISSING')
            if mutation == 'duplicate': groups['groups'][1]['refs'].append('P1')
            if mutation == 'anchor': groups['groups'][0]['anchor'] = 'P2'
            out = self.root / mutation
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                group_views.produce(self.board, groups, out)
            self.assertFalse(out.exists())

    def test_raw_input_identity_and_ungrouped_native_items_are_explicit(self):
        raw = self.root / 'original.kicad_pcb'
        raw.write_bytes(self.board.read_bytes() + b'\n')
        groups = copy.deepcopy(self.groups)
        groups['groups'].pop()
        result = group_views.produce(self.board, groups, self.root / 'views', input_board_path=raw)
        self.assertEqual(result['input_board_sha256'], digest(raw))
        self.assertEqual(digest(self.root / 'views' / result['snapshots']['input_board']), digest(raw))
        self.assertNotEqual(result['input_board_sha256'], result['board_sha256'])
        self.assertEqual([m['reference'] for m in result['ungrouped']], ['P2'])
        self.assertTrue(result['unresolved'])
        self.assertIn('unresolved', (self.root / 'views/top.svg').read_text().lower())
        self.assertEqual(read_json(self.root / 'views/manifest.json'), result)

    def test_ambiguous_native_ref_and_unsupported_crop_refuse_without_outputs(self):
        board = self.k.LoadBoard(str(self.board))
        for fp in board.GetFootprints():
            fp.SetReference('P1')
        self.k.SaveBoard(str(self.board), board)
        groups = dict(groups=[dict(name='single', anchor='P1', refs=['P1'])])
        with self.assertRaisesRegex(ValueError, 'ambiguous native reference'):
            group_views.produce(self.board, groups, self.root / 'ambiguous')
        self.assertFalse((self.root / 'ambiguous').exists())
        self.make_board()
        board = self.k.LoadBoard(str(self.board))
        board.Remove(next(g for g in board.GetDrawings() if g.GetLayer() == self.k.Edge_Cuts))
        self.k.SaveBoard(str(self.board), board)
        with self.assertRaisesRegex(ValueError, 'rectangular Edge.Cuts'):
            group_views.produce(self.board, self.groups, self.root / 'bad-outline')
        self.assertFalse((self.root / 'bad-outline').exists())

    def test_outside_view_and_xml_labels_remain_explicit(self):
        board = self.k.LoadBoard(str(self.board))
        for fp in board.GetFootprints():
            if fp.GetReference() == 'P2':
                fp.SetPosition(self.k.VECTOR2I(self.k.FromMM(100), self.k.FromMM(100)))
        self.k.SaveBoard(str(self.board), board)
        self.groups['groups'][0]['name'] = 'left <& Ω>'
        result = group_views.produce(self.board, self.groups, self.root / 'views')
        self.assertEqual([m['reference'] for m in result['unresolved']], ['P2'])
        self.assertEqual(result['unresolved'][0]['kind'], 'member_outside_view')
        svg = ET.parse(self.root / 'views/top.svg').getroot()
        self.assertEqual([m.attrib['data-ref'] for m in svg.findall('.//' + SVG + 'circle[@data-ref]')], ['P1'])
        labels = [t.text for t in svg.iter(SVG + 'text')]
        self.assertTrue(any('OUTSIDE VIEW' in t for t in labels))
        self.assertTrue(any('left <& Ω>' in t for t in labels))

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

    def edge(self, parent, kind, *points):
        """One Edge.Cuts shape on the board or in a footprint; points in mm."""
        k = self.k
        shape = k.PCB_SHAPE(parent)
        shape.SetShape(kind)
        shape.SetLayer(k.Edge_Cuts)
        shape.SetWidth(k.FromMM(0.05))
        at = [k.VECTOR2I(k.FromMM(x), k.FromMM(y)) for x, y in points]
        if kind == k.SHAPE_T_ARC:
            shape.SetArcGeometry(*at)                        # start, a point on the arc, end
        elif kind == k.SHAPE_T_CIRCLE:
            shape.SetCenter(at[0])
            shape.SetEnd(at[1])                              # centre, a point on the circle
        else:
            shape.SetStart(at[0])
            shape.SetEnd(at[1])
        parent.Add(shape)

    def reshape(self, draw, keep=False):
        """Replace the outline of the saved board (or add to it) with what ``draw(board)`` adds."""
        k = self.k
        board = k.LoadBoard(str(self.board))
        if not keep:
            for item in [g for g in board.GetDrawings() if g.GetLayer() == k.Edge_Cuts]:
                board.Remove(item)
        draw(board)
        k.SaveBoard(str(self.board), board)

    def chain(self, board, points):
        for a, b in zip(points, points[1:] + points[:1]):
            self.edge(board, self.k.SHAPE_T_SEGMENT, a, b)

    def plotted_pads(self, native_svg):
        """Centres of the 2 x 1 mm pads KiCad plotted, in the coordinates of its own page."""
        centres = []
        for path in ET.parse(native_svg).getroot().iter(SVG + 'path'):
            nums = [float(v) for v in re.findall(r'-?\d+\.\d+', path.attrib.get('d', ''))]
            if len(nums) == 8:
                xs, ys = nums[::2], nums[1::2]
                if abs(max(xs) - min(xs) - 2) < 0.001 and abs(max(ys) - min(ys) - 1) < 0.001:
                    centres.append(((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2))
        return centres

    def test_any_closed_outline_frames_the_view_by_its_bounds(self):
        k = self.k

        def stadium(board):                                  # straight top and bottom, half-circle ends
            self.edge(board, k.SHAPE_T_SEGMENT, (15, 20), (35, 20))
            self.edge(board, k.SHAPE_T_ARC, (35, 20), (43, 28), (35, 36))
            self.edge(board, k.SHAPE_T_SEGMENT, (35, 36), (15, 36))
            self.edge(board, k.SHAPE_T_ARC, (15, 36), (7, 28), (15, 20))

        def cut_out(board):                                  # a slot that belongs to a footprint
            part = k.FOOTPRINT(board)
            part.SetReference('H1')
            board.Add(part)
            self.edge(part, k.SHAPE_T_CIRCLE, (0, 0), (1.5, 0))
            part.SetPosition(k.VECTOR2I(k.FromMM(25), k.FromMM(27)))

        def owned(board):                                    # the whole outline belongs to a footprint
            part = k.FOOTPRINT(board)
            part.SetReference('BRD')
            board.Add(part)
            self.chain(part, [(9, 19), (41, 19), (41, 36), (9, 36)])

        cases = {
            # A six-corner board: the bounds are not the bounds of any one edge.
            'l-shape': (lambda board: self.chain(board, [(8, 18), (40, 18), (40, 35), (20, 35), (20, 26), (8, 26)]),
                        False, (8, 18, 40, 35), []),
            'arcs': (stadium, False, (7, 20, 43, 36), []),
            'circle': (lambda board: self.edge(board, k.SHAPE_T_CIRCLE, (25, 28), (45, 28)), False, (5, 8, 45, 48), []),
            'footprint cut-out': (cut_out, True, (10, 20, 40, 35), ['H1']),
            'footprint outline': (owned, False, (9, 19, 41, 36), ['BRD']),
        }
        for index, (name, (draw, keep, frame, extra)) in enumerate(cases.items()):
            with self.subTest(outline=name):
                self.make_board()
                self.reshape(draw, keep)
                out = self.root / f'outline{index}'
                result = group_views.produce(self.board, self.groups, out)
                x0, y0, x1, y1 = frame
                width, height = x1 - x0, y1 - y0
                self.assertEqual([m['reference'] for m in result['ungrouped']], extra)
                self.assertFalse(any(m['outside_view'] for m in result['members']))
                for side in ('top', 'bottom'):
                    view = result['views'][side]
                    # KiCad turns an arc into segments within the board's maximum error (5 um).
                    for got, want in zip(view['transform']['native_crop_origin_mm'] + view['transform']['native_size_mm'],
                                         (x0, y0, width, height)):
                        self.assertAlmostEqual(got, want, delta=0.006)
                    markers = {m.attrib['data-ref']: (float(m.attrib['data-view-x-mm']), float(m.attrib['data-view-y-mm']))
                               for m in ET.parse(out / view['path']).getroot().findall('.//' + SVG + 'circle[@data-ref]')}
                    for ref, (x, y) in (('P1', (14, 24)), ('P2', (34, 30))):
                        want = (width - (x - x0) if side == 'bottom' else x - x0, y - y0)
                        self.assertAlmostEqual(markers[ref][0], want[0], delta=0.006)
                        self.assertAlmostEqual(markers[ref][1], want[1], delta=0.006)
                    # The pad KiCad plotted on this face sits where the frame says it does.
                    x, y = (14, 24) if side == 'top' else (34, 30)
                    pads = self.plotted_pads(out / view['native']['path'])
                    self.assertTrue(any(abs(px - (x - x0)) < 0.006 and abs(py - (y - y0)) < 0.006 for px, py in pads),
                                    (name, side, pads))

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
        with self.assertRaisesRegex(ValueError, 'no closed Edge.Cuts outline'):
            group_views.produce(self.board, self.groups, self.root / 'bad-outline')
        self.assertFalse((self.root / 'bad-outline').exists())
        # No outline at all is refused the same way: there are no bounds to crop to.
        self.reshape(lambda board: None)
        with self.assertRaisesRegex(ValueError, 'no closed Edge.Cuts outline'):
            group_views.produce(self.board, self.groups, self.root / 'no-outline')
        self.assertFalse((self.root / 'no-outline').exists())

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

"""Candidates are gated on copper extent as well as courtyard (AGENTEE-366).

Original fixtures: a part whose pad copper reaches beyond its courtyard must
not be placed with that copper off the board or inside a footprint keep-out.
"""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import placer
from agent_ee.placer import pack, transform
from test_placement import MM, capture, part, rectangle
from test_placer import request

# Courtyard [-2, 2] mm square about the origin; copper reaches past it on every
# side by a different amount, so each quarter turn needs a different inset.
REACH = {'left': 3_500_000, 'top': 3_500_000, 'right': 2_500_000, 'bottom': 2_600_000}
BBOX = [-REACH['left'], -REACH['top'], REACH['right'], REACH['bottom']]


def custom_pad(side='F', bbox=BBOX):
    return {'uuid': 'C1-1', 'number': '1', 'net': '', 'position_nm': [0, 0], 'shape': 6, 'rotation_deg': 0,
            'size_nm': [600000, 400000], 'layers': [side + '.Cu'],
            'custom_geometry': {'status': 'proven', 'primitives': [['gr_rect']], 'anchor': 1,
                                'merged_polygons_nm': [[[bbox[0], bbox[1]], [bbox[2], bbox[1]],
                                                        [bbox[2], bbox[3]], [bbox[0], bbox[3]]]],
                                'bbox_nm': list(bbox)}}


def offset_part(side='F', rotation=0, pads=None):
    """C1 at the origin, absolute geometry fixed, labelled with the given initial rotation."""
    fp = part('C1', 0, 0, side=side, shape=rectangle(-2, -2, 4, 4))
    fp['rotation_deg'] = rotation
    fp['pads'] = pads if pads is not None else [custom_pad(side)]
    return fp


def board(parts, outline=None):
    out = capture(parts, outline or rectangle(0, 0, 20, 20))
    out['pcbnew_version'] = '10.0.7'
    return out


def copper(fp, pad):
    return placer._copper_box(fp, pad)


def extent(fp):
    boxes = [b for b in (copper(fp, pad) for pad in fp['pads']) if b is not None]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def placed(capture_, plan, ref='C1'):
    fp = next(f for f in capture_['footprints'] if f['ref'] == ref)
    pose = next(p for p in plan['placements'] if p['ref'] == ref)
    return transform(fp, pose['position_nm'], pose['rotation_deg'])


def courtyard_only():
    """The pre-AGENTEE-366 search: no copper gate and no copper alignment."""
    return [patch.object(placer, '_copper_escapes', lambda *a, **k: None),
            patch.object(placer, '_cache_copper_extent', lambda fp: fp.pop('_packing_copper', None))]


class CopperExtentTest(unittest.TestCase):
    def pack(self, capture_, policy, mutant=False):
        if not mutant:
            return pack(copy.deepcopy(capture_), policy)
        patches = courtyard_only()
        for p in patches:
            p.start()
        try:
            return pack(copy.deepcopy(capture_), policy)
        finally:
            for p in patches:
                p.stop()

    def test_offset_custom_copper_stays_on_board_every_turn_and_side(self):
        outline = (0, 0, 20 * MM, 20 * MM)
        for side in ('F', 'B'):
            for rotation in (0, 90, 180, 270):
                with self.subTest(side=side, rotation=rotation):
                    capture_ = board([offset_part(side, rotation)])
                    plan = self.pack(capture_, request(fixed_refs=[]))
                    self.assertEqual(plan['status'], 'planned', plan)
                    fp = placed(capture_, plan)
                    box = copper(fp, fp['pads'][0])
                    self.assertTrue(outline[0] <= box[0] and outline[1] <= box[1]
                                    and box[2] <= outline[2] and box[3] <= outline[3], box)
                    # Copper bounds align to the outline: the first fit is flush, not merely inset.
                    self.assertEqual((box[0], box[1]), (0, 0), box)
                    self.assertEqual(plan['judgment']['verdict'], 'pass')
                    # Courtyard-only gating puts the same copper off the board.
                    legacy = self.pack(capture_, request(fixed_refs=[]), mutant=True)
                    self.assertEqual(legacy['status'], 'planned', legacy)
                    fp = placed(capture_, legacy)
                    box = copper(fp, fp['pads'][0])
                    self.assertTrue(box[0] < 0 or box[1] < 0, box)

    def test_offset_stock_pad_copper_stays_on_board_every_turn_and_side(self):
        # Two rectangular lands whose native shape centres sit 2.5 mm and 2 mm outside
        # their anchors, past both courtyard sides; a 4 mm strip admits only lengthwise turns.
        for side in ('F', 'B'):
            for rotation in (0, 90, 180, 270):
                with self.subTest(side=side, rotation=rotation):
                    pads = [{'uuid': f'C1-{n}', 'number': str(n), 'net': '', 'position_nm': [x, 0], 'shape': 1,
                             'rotation_deg': 0, 'size_nm': [1_000_000, 800_000], 'layers': [side + '.Cu'],
                             'copper_centroid_offset_nm': [[d, 1], [0, 1]]}
                            for n, x, d in ((1, -1_000_000, -2_500_000), (2, 1_000_000, 2_000_000))]
                    capture_ = board([offset_part(side, rotation, pads)], rectangle(0, 0, 20, 4))
                    plan = self.pack(capture_, request(fixed_refs=[]))
                    self.assertEqual(plan['status'], 'planned', plan)
                    box = extent(placed(capture_, plan))
                    self.assertTrue(0 <= box[0] and 0 <= box[1] and box[2] <= 20 * MM and box[3] <= 4 * MM, box)
                    self.assertEqual(box[0], 0, box)
                    legacy = self.pack(capture_, request(fixed_refs=[]), mutant=True)
                    box = extent(placed(capture_, legacy))
                    self.assertFalse(0 <= box[0] and 0 <= box[1] and box[2] <= 20 * MM and box[3] <= 4 * MM, box)

    def test_copper_alone_cannot_fit_is_typed_unknown_not_a_placement(self):
        # The 4 mm courtyard fits a 5 x 5 mm board; its 7 x 7 mm copper cannot.
        capture_ = board([offset_part()], rectangle(0, 0, 5, 5))
        plan = self.pack(capture_, request(fixed_refs=[]))
        self.assertEqual((plan['status'], plan['outcome'], plan['placements']),
                         ('packing_search_exhausted', 'unknown', []))
        self.assertEqual(self.pack(capture_, request(fixed_refs=[]), mutant=True)['status'], 'planned')

    def test_copper_beyond_courtyard_is_kept_out_of_board_keepouts_on_its_side(self):
        # A front keep-out strip x in [0, 3] mm; the courtyard may touch it, its copper may not enter it.
        for side, blocked in (('F', True), ('B', False)):
            with self.subTest(side=side):
                capture_ = board([offset_part(side)], rectangle(0, 0, 20, 20))
                capture_['rule_areas'] = [{'uuid': 'strip', 'owner': 'board', 'layers': ['F.Cu'],
                                           'forbid': {'footprints': True},
                                           'outline': {'status': 'proven', 'polygons': [rectangle(0, 0, 3, 20)]}}]
                plan = self.pack(capture_, request(fixed_refs=[]))
                self.assertEqual(plan['status'], 'planned', plan)
                fp = placed(capture_, plan)
                box = copper(fp, fp['pads'][0])
                self.assertEqual(box[0] >= 3 * MM, blocked, box)
                if blocked:
                    fp = placed(capture_, self.pack(capture_, request(fixed_refs=[]), mutant=True))
                    self.assertLess(copper(fp, fp['pads'][0])[0], 3 * MM)

    def test_copper_respects_owned_keepouts_in_both_directions(self):
        # U1 is fixed at the left and owns a front keep-out x in [4, 8] mm.
        u1 = part('U1', 1, 1, shape=rectangle(0, 0, 3, 20))
        capture_ = board([u1, offset_part()], rectangle(0, 0, 20, 20))
        capture_['rule_areas'] = [{'uuid': 'owned', 'owner': 'U1', 'layers': ['F.Cu'],
                                   'forbid': {'footprints': True},
                                   'outline': {'status': 'proven', 'polygons': [rectangle(4, 0, 4, 20)]}}]
        plan = self.pack(capture_, request(fixed_refs=['U1'], gap_nm=0))
        self.assertEqual(plan['status'], 'planned', plan)
        fp = placed(capture_, plan)
        self.assertGreaterEqual(copper(fp, fp['pads'][0])[0], 8 * MM)
        fp = placed(capture_, self.pack(capture_, request(fixed_refs=['U1'], gap_nm=0), mutant=True))
        self.assertLess(copper(fp, fp['pads'][0])[0], 8 * MM)
        # A moving owner's keep-out must not engulf a placed peer's copper: fixed P's
        # courtyard spans x in [0, 2] mm, its copper reaches x = 5 mm. On a 2 mm high
        # strip, U1 (4 x 2 mm) lies lengthwise and owns 3 mm keep-outs at both ends.
        p = part('P', 1, 1, shape=rectangle(0, 0, 2, 2))
        p['pads'] = [custom_pad(bbox=[500_000, 500_000, 5 * MM, 1_500_000])]
        u1 = part('U1', 0, 0, shape=rectangle(-2, -1, 4, 2))
        capture_ = board([u1, p], rectangle(0, 0, 20, 2))
        capture_['rule_areas'] = [{'uuid': name, 'owner': 'U1', 'layers': ['F.Cu'], 'forbid': {'footprints': True},
                                   'outline': {'status': 'proven', 'polygons': [rectangle(x, -1, 3, 2)]}}
                                  for name, x in (('left', -5), ('right', 2))]
        policy = request(fixed_refs=['P'], gap_nm=0)
        owned = copy.deepcopy(capture_)
        placer._qualified(owned)
        placer._keepouts(owned)
        for mutant, engulfed in ((False, False), (True, True)):
            with self.subTest(mutant=mutant):
                plan = self.pack(capture_, policy, mutant)
                self.assertEqual(plan['status'], 'planned', plan)
                keepouts = [k['polygons'][0] for k in placed(owned, plan, 'U1')['packing_keepouts']]
                self.assertEqual(any(placer._overlaps(k, [(500_000, 500_000), (5 * MM, 500_000),
                                                         (5 * MM, 1_500_000), (500_000, 1_500_000)])
                                     for k in keepouts), engulfed)

    def test_copper_inside_courtyard_keeps_the_legacy_search_exactly(self):
        # Every land lies in the courtyard: the plan, steps and search record are unchanged.
        pads = [{'uuid': f'C1-{i}', 'number': str(i), 'net': 'N', 'position_nm': [x, 0], 'shape': 1, 'rotation_deg': 0,
                 'size_nm': [600_000, 400_000], 'layers': ['F.Cu'], 'copper_centroid_offset_nm': [[0, 1], [0, 1]]}
                for i, x in ((1, -1_000_000), (2, 1_000_000))]
        u1 = part('U1', 10, 10, shape=rectangle(9, 9, 2, 2))
        u1['pads'] = [{'uuid': 'U1-1', 'number': '1', 'net': 'N', 'position_nm': [10 * MM, 10 * MM], 'shape': 1,
                       'rotation_deg': 0, 'size_nm': [600_000, 400_000], 'layers': ['F.Cu'],
                       'copper_centroid_offset_nm': [[0, 1], [0, 1]]}]
        capture_ = board([u1, offset_part(pads=pads)])
        stripped = copy.deepcopy(capture_)
        for fp in stripped['footprints']:
            for pad in fp['pads']:
                del pad['size_nm'], pad['copper_centroid_offset_nm']
        self.assertEqual(self.pack(capture_, request()), self.pack(stripped, request()))
        self.assertEqual(self.pack(capture_, request()), self.pack(capture_, request(), mutant=True))

    def test_malformed_land_facts_are_typed_unknown(self):
        for change in ({'size_nm': [1, 'x']}, {'copper_centroid_offset_nm': [[0, 0], [0, 1]]},
                       {'layers': 'F.Cu'}):
            with self.subTest(change=change):
                pad = {'uuid': 'C1-1', 'number': '1', 'net': '', 'position_nm': [0, 0], 'shape': 1,
                       'rotation_deg': 0, 'size_nm': [600_000, 400_000], 'layers': ['F.Cu'],
                       'copper_centroid_offset_nm': [[0, 1], [0, 1]], **change}
                plan = self.pack(board([offset_part(pads=[pad])]), request(fixed_refs=[]))
                self.assertEqual((plan['status'], plan['outcome']), ('unsupported_geometry', 'unknown'), plan)

    def test_copper_box_by_shape_turn_and_record(self):
        fp = {'ref': 'C1'}
        rect = {'number': '1', 'shape': 1, 'position_nm': [10, 20], 'rotation_deg': 90, 'size_nm': [6, 4],
                'copper_centroid_offset_nm': [[1, 2], [-3, 1]]}
        # Centre (10.5, 17); a quarter-turned 6 x 4 land spans 4 in x, 6 in y; bounds round outward.
        self.assertEqual(copper(fp, rect), (8, 14, 13, 20))
        chamfered = {**rect, 'shape': 5, 'rotation_deg': 0, 'copper_centroid_offset_nm': [[0, 1], [0, 1]]}
        # The area centroid lies in the land: twice its size about the centroid contains it.
        self.assertEqual(copper(fp, chamfered), (4, 16, 16, 24))
        self.assertEqual(copper(fp, custom_pad()), tuple(BBOX))
        self.assertIsNone(copper(fp, {k: v for k, v in rect.items() if k != 'size_nm'}))
        self.assertIsNone(copper(fp, {k: v for k, v in rect.items() if k != 'copper_centroid_offset_nm'}))
        self.assertEqual(placer._copper_sides({'layers': ['F.Paste', 'F.Mask']}), ((), False))
        self.assertEqual(placer._copper_sides({'layers': ['*.Cu', '*.Mask']}), (('F', 'B'), True))
        self.assertEqual(placer._copper_sides({'layers': ['In1.Cu']}), ((), True))



class TargetedPlanCopperTest(unittest.TestCase):
    def test_row_target_with_copper_off_the_board_is_blocked(self):
        from agent_ee import placement_plans
        from test_placement_plans import board as plan_board, row
        facts = plan_board()
        c1 = next(f for f in facts['footprints'] if f['ref'] == 'C1')
        # C1's courtyard spans y in [2, 4] mm; its custom copper reaches y = 1 mm, then y = -1 mm.
        for top, blocked in ((MM, False), (-MM, True)):
            with self.subTest(top=top):
                c1['pads'] = [{**custom_pad(bbox=[12_500_000, top, 13_500_000, 3 * MM]), 'net': 'VCC',
                               'position_nm': c1['position_nm'][:]}]
                result = placement_plans.plan(copy.deepcopy(facts), request(), row())
                self.assertEqual(result['status'], 'blocked' if blocked else 'planned', result['reasons'])
                self.assertEqual('C1: target copper outside outline or in keep-out' in result['reasons'], blocked)
                with patch.object(placement_plans, '_copper_escapes', lambda *a, **k: None):
                    self.assertEqual(placement_plans.plan(copy.deepcopy(facts), request(), row())['status'],
                                     'planned')


class BlockArrangementCopperTest(unittest.TestCase):
    """Stage B arranges rigid blocks whose members carry copper beyond their courtyards."""

    def setUp(self):
        from test_block_arrangement import ArrangementFixture
        from test_blocks import Fixture, policy
        from agent_ee import blocks

        class Case(ArrangementFixture, unittest.TestCase):
            def setUp(inner):
                Fixture.setUp(inner)
                u1 = next(f for f in inner.board['footprints'] if f['ref'] == 'U1')
                # U1's courtyard starts at x = 3 mm; its custom copper reaches x = 2 mm.
                u1['pads'][0].update(custom_pad(bbox=[2_000_000, 3_300_000, 3_700_000, 3_700_000]),
                                     position_nm=u1['pads'][0]['position_nm'], uuid=u1['pads'][0]['uuid'],
                                     number=u1['pads'][0]['number'], net=u1['pads'][0]['net'])
                inner.inv = inner.inventory()[0]
                inner.derivation = inner.derive()
                inner.policy = policy()
                inner.entries = [{'candidate': blocks.pack_block(inner.board, inner.inv, inner.derivation, b['id'],
                                                                 inner.policy)['candidate']}
                                 for b in inner.derivation['blocks']]

            def runTest(inner):
                pass

        self.case = Case()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_member_copper_stays_on_board(self):
        board_ = self.case.board
        outline = board_['outline']['polygons'][0]
        x1, y1 = max(p[0] for p in outline), max(p[1] for p in outline)
        for mutant, inside in ((False, True), (True, False)):
            with self.subTest(mutant=mutant):
                if mutant:
                    with patch.object(placer, '_copper_escapes', lambda *a, **k: None):
                        plan = self.case.arrange()
                else:
                    plan = self.case.arrange()
                self.assertEqual(plan['status'], 'planned', plan.get('reason'))
                fp = placed(board_, plan, 'U1')
                box = extent(fp)
                self.assertEqual(0 <= box[0] and 0 <= box[1] and box[2] <= x1 and box[3] <= y1, inside, box)



class NativeOffsetCustomPadTest(unittest.TestCase):
    """Native writer, every quarter turn and side: the saved copper stays on the board."""

    @classmethod
    def setUpClass(cls):
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-copper-extent-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()
        self.netlist = self.root / 'netlist.xml'
        self.netlist.write_text('<export><components><comp ref="C1"/></components>'
                                '<nets><net code="1" name="SIGNAL"><node ref="C1" pin="1"/></net></nets></export>')

    def source(self, rotation, back):
        """C1: a 4 mm courtyard about its origin; one custom pad whose copper reaches past it on every side."""
        import pcbnew as k
        board = k.BOARD()

        def vec(x, y):
            return k.VECTOR2I(int(x * MM), int(y * MM))

        def line(parent, layer, a, b):
            shape = k.PCB_SHAPE(parent)
            shape.SetShape(k.SHAPE_T_SEGMENT)
            shape.SetLayer(layer)
            shape.SetStart(vec(*a))
            shape.SetEnd(vec(*b))
            shape.SetWidth(50000)
            parent.Add(shape)
        corners = [(0, 0), (20, 0), (20, 20), (0, 20)]
        for i, a in enumerate(corners):
            line(board, k.Edge_Cuts, a, corners[(i + 1) % 4])
        fp = k.FOOTPRINT(board)
        fp.SetReference('C1')
        fp.SetValue('owned')
        board.Add(fp)
        box = [(-2, -2), (2, -2), (2, 2), (-2, 2)]
        for i, a in enumerate(box):
            line(fp, k.F_CrtYd, a, box[(i + 1) % 4])
        pad = k.PAD(fp)
        pad.SetNumber('1')
        pad.SetShape(k.F_Cu, k.PAD_SHAPE_CUSTOM)
        pad.SetAnchorPadShape(k.F_Cu, k.PAD_SHAPE_RECT)
        pad.SetSize(k.F_Cu, vec(.6, .4))
        pad.SetAttribute(k.PAD_ATTRIB_SMD)
        fp.Add(pad)
        fp.SetPosition(vec(10, 10))
        path = self.root / f'offset-{rotation}-{back}.kicad_pcb'
        k.SaveBoard(str(path), board)
        text = path.read_text()
        self.assertEqual(text.count('(primitives)'), 1)
        text = text.replace('(primitives)',
                            '(primitives (gr_rect (start -3.5 -3.5) (end 2.5 2.6) (width 0) (fill yes)))')
        text = text.replace('(options', '(zone_connect 2) (options')
        path.write_text(text)
        board = k.LoadBoard(str(path))
        net = k.NETINFO_ITEM(board, 'SIGNAL')
        board.Add(net)
        fp = next(iter(board.GetFootprints()))
        for pad in fp.Pads():
            layers = k.LSET()
            for layer in (k.F_Cu, k.F_Paste, k.F_Mask):
                layers.AddLayer(layer)
            pad.SetLayerSet(layers)
            pad.SetNet(net)
        if back:
            fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetOrientationDegrees(rotation)
        k.SaveBoard(str(path), board)
        return path

    def place(self, source, name):
        from agent_ee.placer_runner import place_candidate
        config = {'policy': request(fixed_refs=[]), 'authorization': {
            'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'writable_root': str(self.scratch)}}
        return place_candidate(source, self.netlist, self.scratch / name, config)

    def saved_copper(self, name):
        import pcbnew as k
        board = k.LoadBoard(str(self.scratch / name / 'candidate.kicad_pcb'))
        pad = next(iter(next(iter(board.GetFootprints())).Pads()))
        self.assertEqual(pad.GetShape(k.F_Cu), k.PAD_SHAPE_CUSTOM)
        box = pad.GetBoundingBox()
        return box.GetLeft(), box.GetTop(), box.GetRight(), box.GetBottom()

    def test_saved_custom_copper_lies_inside_the_outline_every_turn_and_side(self):
        from agent_ee.placement_probe import extract
        for back in (False, True):
            for rotation in (0, 90, 180, 270):
                with self.subTest(back=back, rotation=rotation):
                    source = self.source(rotation, back)
                    pad = extract(source)['footprints'][0]['pads'][0]
                    self.assertEqual(pad['custom_geometry']['status'], 'proven', pad)
                    self.assertEqual(pad['layers'][0], ('B' if back else 'F') + '.Cu')
                    name = f'placed-{back}-{rotation}'
                    report = self.place(source, name)
                    self.assertEqual((report['status'], report['outcome']), ('placed', 'info'), report)
                    self.assertEqual(report['judgment']['verdict'], 'pass')
                    box = self.saved_copper(name)
                    self.assertTrue(0 <= box[0] and 0 <= box[1] and box[2] <= 20 * MM and box[3] <= 20 * MM, box)
                    # The saved native copper extent is flush with the outline corner.
                    self.assertEqual(box[:2], (0, 0))
                    # Courtyard-only gating publishes the same copper off the board, with no open defect.
                    patches = courtyard_only()
                    for p in patches:
                        p.start()
                    try:
                        legacy = self.place(source, name + '-legacy')
                    finally:
                        for p in patches:
                            p.stop()
                    self.assertEqual((legacy['status'], legacy['outcome'], legacy['open_defects']),
                                     ('placed', 'info', []))
                    box = self.saved_copper(name + '-legacy')
                    self.assertTrue(box[0] < 0 or box[1] < 0, box)


if __name__ == '__main__':
    unittest.main()

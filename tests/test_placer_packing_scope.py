"""Packing gate scope: copper against peers, pad-only and inner-layer rule areas (AGENTEE-418).

Original fixtures. Copper outside a courtyard binds a peer's courtyard as a
courtyard does; a rule area forbidding pads binds every land whose copper
layers meet its layers, inner layers included; a footprint rule area on inner
layers only binds no footprint, which the native test pins against kicad-cli
DRC. Stage-B block arrangement adds copper alignment axes. Each regression has
a mutant control without the gate.
"""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import block_arrangement, placer
from agent_ee.placer import pack
from test_placement import MM, part, rectangle
from test_placer import request
from test_placer_copper_extent import (SETBACK, BlockArrangementCopperTest, NativeBoard, board, courtyard_only,
                                       custom_pad, extent, offset_part, placed)

THROUGH = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']


def land(ref, number, x, y, side='F', layers=None, size=(600_000, 400_000)):
    """A native-style stock rectangular land: size and copper centroid at its position."""
    return {'uuid': f'{ref}-{number}', 'number': number, 'net': '', 'position_nm': [int(x * MM), int(y * MM)],
            'shape': 1, 'rotation_deg': 0, 'size_nm': list(size), 'layers': layers or [side + '.Cu'],
            'copper_centroid_offset_nm': [[0, 1], [0, 1]]}


def lands_part(ref='C1', side='F', layers=None, x=0, y=0):
    """A 4 mm courtyard about (x, y) mm with lands 1.5 mm left and right of it."""
    fp = part(ref, x, y, side=side, shape=rectangle(x - 2, y - 2, 4, 4))
    fp['pads'] = [land(ref, '1', x - 1.5, y, side, layers), land(ref, '2', x + 1.5, y, side, layers)]
    return fp


def area(uuid, layers, polygon, owner='board', footprints=False, pads=True):
    return {'uuid': uuid, 'owner': owner, 'layers': list(layers), 'forbid': {'footprints': footprints, 'pads': pads},
            'outline': {'status': 'proven', 'polygons': [polygon]}}


def box(points):
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


def meets(a, b):
    """Positive-area overlap of two (x0, y0, x1, y1) boxes, independent of the placer."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def lands_of(fp):
    return [placer._copper_box(fp, pad) for pad in fp['pads']]


def courtyard_box(fp):
    return box(fp['courtyards'][fp['side']]['polygons'][0])


class PadKeepoutTest(unittest.TestCase):
    def pack(self, capture_, policy, mutant=False):
        if not mutant:
            return pack(capture_, policy)
        patches = courtyard_only()
        for p in patches:
            p.start()
        try:
            return pack(capture_, policy)
        finally:
            for p in patches:
                p.stop()

    def test_lands_avoid_a_board_pad_keepout_that_the_courtyard_may_enter(self):
        # On a 4 mm wide board the courtyard must cross a keep-out x in [1, 3] mm between its lands.
        middle = rectangle(1, 0, 2, 20)
        capture_ = board([lands_part()], rectangle(0, 0, 4, 20))
        capture_['rule_areas'] = [area('pads', ['F.Cu'], middle)]
        plan = self.pack(capture_, request(fixed_refs=[]))
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        fp = placed(capture_, plan)
        self.assertTrue(meets(courtyard_box(fp), box(middle)))
        self.assertFalse(any(meets(b, box(middle)) for b in lands_of(fp)), lands_of(fp))
        # Lands that would enter it are kept out; before AGENTEE-418 they entered it.
        strip = rectangle(0, 0, 3, 20)
        capture_ = board([lands_part()])
        capture_['rule_areas'] = [area('pads', ['F.Cu'], strip)]
        original = copy.deepcopy(capture_)
        plan = self.pack(capture_, request(fixed_refs=[]))
        self.assertEqual((plan['status'], plan['outcome']), ('planned', 'info'), plan.get('reason'))
        self.assertEqual(capture_, original)
        fp = placed(capture_, plan)
        self.assertFalse(any(meets(b, box(strip)) for b in lands_of(fp)), lands_of(fp))
        fp = placed(capture_, self.pack(capture_, request(fixed_refs=[]), mutant=True))
        self.assertTrue(any(meets(b, box(strip)) for b in lands_of(fp)))

    def test_pad_keepout_binds_lands_on_its_layers_only(self):
        whole = rectangle(0, 0, 20, 20)
        for side, layers, area_layers, blocked in (
                ('F', None, ['F.Cu'], True), ('B', None, ['F.Cu'], False),
                ('F', None, ['In1.Cu'], False), ('F', THROUGH, ['In1.Cu'], True),
                ('B', THROUGH, ['F.Cu'], True), ('F', ['*.Cu'], ['In2.Cu'], True),
                ('F', None, ['*.Cu'], True), ('B', None, ['F.Cu', 'In1.Cu'], False)):
            with self.subTest(side=side, layers=layers, area=area_layers):
                capture_ = board([lands_part(side=side, layers=layers)])
                capture_['rule_areas'] = [area('pads', area_layers, whole)]
                plan = self.pack(capture_, request(fixed_refs=[]))
                if blocked:
                    self.assertEqual((plan['status'], plan['outcome'], plan['placements']),
                                     ('packing_search_exhausted', 'unknown', []), plan.get('reason'))
                    # Before AGENTEE-418 the full-board pad keep-out did not constrain packing.
                    self.assertEqual(self.pack(capture_, request(fixed_refs=[]), mutant=True)['status'], 'planned')
                else:
                    self.assertEqual(plan, self.pack(capture_, request(fixed_refs=[]), mutant=True))
                    self.assertEqual(plan['status'], 'planned')

    def test_inner_layer_only_footprint_keepout_binds_no_footprint(self):
        # Native KiCad 10 never reports a footprint against it (NativeRuleAreaSemanticsTest).
        capture_ = board([lands_part(layers=THROUGH)])
        capture_['rule_areas'] = [area('inner', ['In1.Cu', 'In2.Cu'], rectangle(0, 0, 20, 20),
                                       footprints=True, pads=False)]
        plan = self.pack(capture_, request(fixed_refs=[]))
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        capture_['rule_areas'][0]['forbid']['pads'] = True
        self.assertEqual(self.pack(capture_, request(fixed_refs=[]))['status'], 'packing_search_exhausted')

    def test_owned_pad_keepout_binds_peer_lands_in_both_directions_and_exempts_its_owner(self):
        # Fixed U1 (courtyard x in [0, 3] mm) owns a pad keep-out x in [1, 7] mm that covers its own land.
        u1 = part('U1', 1.5, 10, shape=rectangle(0, 0, 3, 20))
        u1['pads'] = [land('U1', '1', 1.5, 10)]
        capture_ = board([u1, lands_part(x=10, y=10)])
        capture_['rule_areas'] = [area('owned', ['F.Cu'], rectangle(1, 0, 6, 20), owner='U1')]
        policy = request(fixed_refs=['U1'], gap_nm=0)
        plan = self.pack(capture_, policy)
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        self.assertTrue(all(b[0] >= 7 * MM for b in lands_of(placed(capture_, plan))))
        self.assertTrue(any(b[0] < 7 * MM for b in lands_of(placed(capture_, self.pack(capture_, policy, True)))))
        # A moving owner's pad keep-out must not engulf a placed peer's land: fixed P has a land at
        # x in [0.7, 1.3] mm; U1 (4 x 2 mm on a 2 mm strip) owns 3 mm pad keep-outs at both ends.
        p = part('P', 1, 1, shape=rectangle(0, 0, 2, 2))
        p['pads'] = [land('P', '1', 1, 1)]
        u1 = part('U1', 0, 0, shape=rectangle(-2, -1, 4, 2))
        capture_ = board([u1, p], rectangle(0, 0, 20, 2))
        capture_['rule_areas'] = [area(name, ['F.Cu'], rectangle(x, -1, 3, 2), owner='U1')
                                  for name, x in (('left', -5), ('right', 2))]
        policy = request(fixed_refs=['P'], gap_nm=0)
        owned = copy.deepcopy(capture_)
        placer._qualified(owned)
        placer._keepouts(owned)
        target = box([[700_000, 800_000], [1_300_000, 1_200_000]])
        for mutant, engulfed in ((False, False), (True, True)):
            with self.subTest(mutant=mutant):
                plan = self.pack(capture_, policy, mutant)
                self.assertEqual(plan['status'], 'planned', plan.get('reason'))
                areas = [box(k['polygons'][0]) for k in placed(owned, plan, 'U1')['packing_pad_keepouts']]
                self.assertEqual(any(meets(a, target) for a in areas), engulfed, areas)

    def test_fixed_land_in_a_pad_keepout_is_an_input_defect_or_unproven(self):
        u1 = lands_part('U1', x=10, y=10)
        capture_ = board([u1])
        capture_['rule_areas'] = [area('pads', ['F.Cu'], rectangle(11, 9, 3, 2))]
        plan = self.pack(capture_, request(fixed_refs=['U1']))
        self.assertEqual((plan['status'], plan['outcome'], plan['placements']),
                         ('fixed_input_copper', 'defect', []), plan.get('reason'))
        self.assertEqual(plan['findings'], [
            {'kind': 'fixed_input_copper', 'reference': 'U1', 'pad': '2',
             'copper_bounds_nm': [11_200_000, 9_800_000, 11_800_000, 10_200_000], 'setback_nm': SETBACK,
             'keepout': 'rule area pads', 'violation': 'in_pad_keepout'}])
        # On the other side, and before AGENTEE-418, the fixed part plans.
        with patch.object(placer, '_lands_in', lambda *a, **k: []):
            self.assertEqual(pack(capture_, request(fixed_refs=['U1']))['status'], 'planned')
        other = copy.deepcopy(capture_)
        other['rule_areas'][0]['layers'] = ['B.Cu']
        self.assertEqual(self.pack(other, request(fixed_refs=['U1']))['status'], 'planned')
        # A hand-built land without native facts is bounded by its courtyard only: unproven, unknown.
        for pad in u1['pads']:
            del pad['size_nm'], pad['copper_centroid_offset_nm']
        plan = self.pack(capture_, request(fixed_refs=['U1']))
        self.assertEqual((plan['status'], plan['outcome'], plan['placements']),
                         ('unsupported_geometry', 'unknown', []), plan.get('reason'))
        self.assertEqual({(f['pad'], f['violation'], f['copper_bounds_nm']) for f in plan['findings']},
                         {('1', 'pad_keepout_unproven', None), ('2', 'pad_keepout_unproven', None)})
        self.assertIn('pad keep-out unproven', plan['reason'])

    def test_malformed_pad_flag_is_typed_unknown(self):
        capture_ = board([lands_part(x=10, y=10)])
        for flag in ('yes', 1, None):
            with self.subTest(flag=flag):
                capture_['rule_areas'] = [area('pads', ['F.Cu'], rectangle(0, 0, 3, 3), pads=flag)]
                plan = self.pack(capture_, request(fixed_refs=[]))
                self.assertEqual((plan['status'], plan['outcome'], plan['placements']),
                                 ('unsupported_geometry', 'unknown', []))
                self.assertEqual(plan['reason'], 'malformed rule-area pad flag')

    def test_hand_built_area_without_the_native_pad_flag_forbids_no_pads(self):
        # Documented residual: native extraction always records forbid.pads (NativeRuleAreaSemanticsTest).
        capture_ = board([lands_part()])
        capture_['rule_areas'] = [area('legacy', ['F.Cu'], rectangle(0, 0, 3, 20))]
        del capture_['rule_areas'][0]['forbid']['pads']
        capture_['rule_areas'][0]['forbid']['footprints'] = False
        plan = self.pack(capture_, request(fixed_refs=[]))
        self.assertEqual(plan, self.pack(capture_, request(fixed_refs=[]), mutant=True))


class PeerCourtyardTest(unittest.TestCase):
    def test_copper_stays_out_of_a_peer_courtyard_in_both_directions(self):
        # Fixed U1 courtyard x in [0, 3] mm; C1's copper reaches 1.5 mm left of its courtyard.
        u1 = part('U1', 1.5, 2, shape=rectangle(0, 0, 3, 4))
        c1 = offset_part(pads=[custom_pad(bbox=[-3_500_000, -1_500_000, 1_500_000, 1_500_000])])
        capture_ = board([u1, c1], rectangle(0, 0, 20, 4))
        policy = request(fixed_refs=['U1'], gap_nm=0)
        plan = pack(capture_, policy)
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        self.assertFalse(meets(extent(placed(capture_, plan)), courtyard_box(u1)))
        with patch.object(placer, '_copper_meets', lambda *a: False):
            mutant = pack(capture_, policy)
        self.assertTrue(meets(extent(placed(capture_, mutant)), courtyard_box(u1)))
        # The reverse: fixed P's copper reaches 3 mm right of its courtyard; C1's courtyard must stay off it.
        p = part('P', 1, 2, shape=rectangle(0, 0, 2, 4))
        p['pads'] = [{**custom_pad(bbox=[500_000, 1_500_000, 5 * MM, 2_500_000]), 'uuid': 'P-1'}]
        c1 = part('C1', 0, 0, shape=rectangle(-2, -2, 4, 4))
        capture_ = board([p, c1], rectangle(0, 0, 20, 4))
        policy = request(fixed_refs=['P'], gap_nm=0)
        copper = (500_000, 1_500_000, 5 * MM, 2_500_000)
        plan = pack(capture_, policy)
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        self.assertFalse(meets(courtyard_box(placed(capture_, plan)), copper))
        with patch.object(placer, '_copper_meets', lambda *a: False):
            mutant = pack(capture_, policy)
        self.assertTrue(meets(courtyard_box(placed(capture_, mutant)), copper))

    def test_copper_stays_out_of_peer_copper_outside_its_courtyard(self):
        # Both parts' copper leaves their courtyards toward each other on a 4 mm strip.
        p = part('P', 1, 2, shape=rectangle(0, 0, 2, 4))
        p['pads'] = [{**custom_pad(bbox=[500_000, 1_500_000, 4 * MM, 2_500_000]), 'uuid': 'P-1'}]
        c1 = offset_part(pads=[custom_pad(bbox=[-3 * MM, -500_000, 1_500_000, 500_000])])
        capture_ = board([p, c1], rectangle(0, 0, 20, 4))
        plan = pack(capture_, request(fixed_refs=['P'], gap_nm=0))
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        self.assertFalse(meets(extent(placed(capture_, plan)), (500_000, 1_500_000, 4 * MM, 2_500_000)))

    def test_fixed_copper_in_a_fixed_peer_courtyard_is_a_fixed_conflict(self):
        u1 = part('U1', 1.5, 2, shape=rectangle(0, 0, 3, 4))
        c1 = offset_part(pads=[custom_pad(bbox=[-3_500_000, -1_500_000, 1_500_000, 1_500_000])])
        c1 = placer.transform(c1, [5 * MM, 2 * MM], 0)
        capture_ = board([u1, c1], rectangle(0, 0, 20, 4))
        for fixed in (['U1', 'C1'], ['C1', 'U1']):
            with self.subTest(fixed=fixed):
                plan = pack(capture_, request(fixed_refs=fixed, gap_nm=0))
                self.assertEqual((plan['status'], plan['outcome'], plan['placements']),
                                 ('fixed_conflict', 'unknown', []))
                self.assertIn('copper outside its courtyard meets', plan['reason'])
        with patch.object(placer, '_copper_meets', lambda *a: False):
            self.assertEqual(pack(capture_, request(fixed_refs=['U1', 'C1'], gap_nm=0))['status'], 'planned')
        moved = copy.deepcopy(capture_)
        moved['footprints'][1] = placer.transform(moved['footprints'][1], [7 * MM, 2 * MM], 0)
        self.assertEqual(pack(moved, request(fixed_refs=['U1', 'C1'], gap_nm=0))['status'], 'planned')

    def test_targeted_row_with_copper_over_a_peer_courtyard_is_blocked(self):
        from agent_ee import placement_plans
        from test_placement_plans import board as plan_board, row
        from test_placer_copper_extent import MAX_ERROR
        facts = {**plan_board(), 'max_error_nm': MAX_ERROR}
        c1 = next(f for f in facts['footprints'] if f['ref'] == 'C1')
        # The row targets C1 at x in [5, 7] mm beside U1 (x in [2, 4] mm); C1's copper reaches left, into U1.
        for left, blocked in ((10_500_000, True), (11_200_000, False)):
            with self.subTest(left=left):
                c1['pads'] = [{**custom_pad(bbox=[left, 2_500_000, 13_500_000, 3_500_000]), 'net': 'VCC',
                               'position_nm': c1['position_nm'][:]}]
                result = placement_plans.plan(copy.deepcopy(facts), request(), row())
                self.assertEqual(result['status'], 'blocked' if blocked else 'planned', result['reasons'])
                self.assertEqual('C1: target copper meets a peer courtyard or peer copper' in result['reasons'],
                                 blocked)

    def test_targeted_pad_in_a_pad_keepout_is_blocked(self):
        from agent_ee import placement_plans
        from test_placement_plans import board as plan_board, row
        from test_placer_copper_extent import MAX_ERROR
        facts = {**plan_board(), 'max_error_nm': MAX_ERROR}
        c1 = next(f for f in facts['footprints'] if f['ref'] == 'C1')
        # At its target C1's land lies at x in [5.7, 6.3] mm.
        c1['pads'] = [{**land('C1', '1', 13, 3), 'net': 'VCC'}]
        for layers, blocked in ((['F.Cu'], True), (['B.Cu'], False)):
            with self.subTest(layers=layers):
                facts['rule_areas'] = [area('pads', layers, rectangle(5.5, 2.5, 1, 1))]
                result = placement_plans.plan(copy.deepcopy(facts), request(), row())
                self.assertEqual('C1: target pad in a rule area forbidding pads' in result['reasons'], blocked,
                                 result['reasons'])


class ArrangementScopeTest(unittest.TestCase):
    """Stage B: member copper against fixed peers and pad keep-outs; copper alignment axes."""

    def setUp(self):
        fixture = BlockArrangementCopperTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.case = fixture.case

    def arrange(self, capture_):
        """Stage A candidates re-packed on the changed board (its deferred rule areas are counted), then stage B."""
        from agent_ee import blocks
        entries = [{'candidate': blocks.pack_block(capture_, self.case.inv, self.case.derivation, b['id'],
                                                   self.case.policy)['candidate']}
                   for b in self.case.derivation['blocks']]
        return self.case.arrange(capture=capture_, entries=entries)

    def member_fps(self, capture_, plan):
        members = {r for b in plan['block_arrangement']['blocks'] for r in b['references']}
        return [placed(capture_, plan, ref) for ref in sorted(members)]

    def test_copper_axes_put_member_copper_the_setback_from_the_outline(self):
        plan = self.case.arrange()
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        self.assertEqual(extent(placed(self.case.board, plan, 'U1'))[0], SETBACK)
        with patch.object(block_arrangement, '_copper_axes', lambda *a: (set(), set())):
            mutant = self.case.arrange()
        self.assertNotEqual(mutant.get('placements'), plan['placements'])
        if mutant['status'] == 'planned':
            self.assertGreater(extent(placed(self.case.board, mutant, 'U1'))[0], SETBACK)

    def test_member_courtyards_stay_off_fixed_copper_outside_its_courtyard(self):
        board_ = copy.deepcopy(self.case.board)
        h1 = next(f for f in board_['footprints'] if f['ref'] == 'H1')
        # Fixed H1 (courtyard [40, 42] x [30, 32] mm) has copper reaching left across most of the board.
        copper = (MM, MM, 40_500_000, 31_500_000)
        h1['pads'] = [{**custom_pad(bbox=list(copper)), 'uuid': 'H1-1'}]
        plan = self.arrange(board_)
        if plan['status'] == 'planned':
            self.assertFalse(any(meets(courtyard_box(fp), copper) for fp in self.member_fps(board_, plan)))
        else:
            self.assertEqual(plan['outcome'], 'unknown', plan)
        with patch.object(placer, '_copper_meets', lambda *a: False):
            mutant = self.arrange(board_)
        self.assertEqual(mutant['status'], 'planned', mutant.get('reason'))
        self.assertTrue(any(meets(courtyard_box(fp), copper) for fp in self.member_fps(board_, mutant)))

    def test_member_lands_avoid_a_board_pad_keepout(self):
        board_ = copy.deepcopy(self.case.board)
        strip = rectangle(0, 0, 12, 40)
        board_['rule_areas'] = [area('pads', ['F.Cu'], strip)]
        plan = self.arrange(board_)
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        self.assertFalse(any(meets(b, box(strip)) for fp in self.member_fps(board_, plan) for b in lands_of(fp)
                             if b is not None))
        with patch.object(placer, '_lands_in', lambda *a, **k: []):
            mutant = self.arrange(board_)
        self.assertEqual(mutant['status'], 'planned', mutant.get('reason'))
        self.assertTrue(any(meets(b, box(strip)) for fp in self.member_fps(board_, mutant) for b in lands_of(fp)
                            if b is not None))


class NativeRuleAreaSemanticsTest(unittest.TestCase):
    """kicad-cli DRC on owned native boards: which rule areas report a footprint or a pad.

    The packing gate's rule-area scope rests on these native facts: a footprint
    area binds a footprint on its own side's outer copper only (never on inner
    layers alone); a pad area binds every pad whose copper layers meet it,
    through-hole pads on inner layers included, the owner's own pads too.
    """

    @classmethod
    def setUpClass(cls):
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-packing-scope-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def drc(self, layers, forbid, *, through=False, back=False, over='pad', owned=False, name='case'):
        """Reported items_not_allowed for C1 (4 mm courtyard, one 1 mm pad at its centre) on a 4-layer board."""
        import pcbnew as k

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
        board_ = k.BOARD()
        board_.SetCopperLayerCount(4)
        corners = [(0, 0), (20, 0), (20, 20), (0, 20)]
        for i, a in enumerate(corners):
            line(board_, k.Edge_Cuts, a, corners[(i + 1) % 4])
        fp = k.FOOTPRINT(board_)
        fp.SetReference('C1')
        board_.Add(fp)
        square = [(-2, -2), (2, -2), (2, 2), (-2, 2)]
        for i, a in enumerate(square):
            line(fp, k.F_CrtYd, a, square[(i + 1) % 4])
        pad = k.PAD(fp)
        pad.SetNumber('1')
        pad.SetShape(k.F_Cu, k.PAD_SHAPE_CIRCLE if through else k.PAD_SHAPE_RECT)
        pad.SetSize(k.F_Cu, vec(1, 1))
        if through:
            pad.SetAttribute(k.PAD_ATTRIB_PTH)
            pad.SetDrillSize(vec(.5, .5))
            pad.SetLayerSet(k.PAD.PTHMask())
        else:
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetLayerSet(k.PAD.SMDMask())
        fp.Add(pad)
        fp.SetPosition(vec(10, 10))
        if back:
            fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
        zone = k.ZONE(fp if owned else board_)
        zone.SetIsRuleArea(True)
        layer_set = k.LSET()
        for layer in layers:
            layer_set.AddLayer(board_.GetLayerID(layer))
        zone.SetLayerSet(layer_set)
        zone.SetDoNotAllowFootprints('footprints' in forbid)
        zone.SetDoNotAllowPads('pads' in forbid)
        zone.SetDoNotAllowTracks(False)
        zone.SetDoNotAllowVias(False)
        zone.SetDoNotAllowZoneFills(False)
        # Over the pad (and courtyard), or over the courtyard corner only.
        x0, y0, x1, y1 = (9.8, 9.8, 13, 13) if over == 'pad' else (11.5, 11.5, 13, 13)
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            outline.Append(int(x * MM), int(y * MM))
        (fp if owned else board_).Add(zone)
        path = self.root / f'{name}.kicad_pcb'
        k.SaveBoard(str(path), board_)
        report = self.root / f'{name}.json'
        subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '-o', str(report),
                        str(path)], check=True, capture_output=True)
        violations = json.loads(report.read_text())['violations']
        return path, sorted(item['description'] for v in violations if v['type'] == 'items_not_allowed'
                            for item in v['items'])

    def test_inner_layer_only_footprint_area_reports_no_footprint(self):
        for through in (False, True):
            for back in (False, True):
                for over in ('pad', 'courtyard'):
                    with self.subTest(through=through, back=back, over=over):
                        _, found = self.drc(['In1.Cu'], ('footprints',), through=through, back=back, over=over)
                        self.assertEqual(found, [])
        # The control: the same area on the footprint's own outer layer reports it.
        self.assertEqual(self.drc(['F.Cu'], ('footprints',), over='courtyard')[1], ['Footprint C1'])
        self.assertEqual(self.drc(['B.Cu'], ('footprints',), over='courtyard')[1], [])

    def test_pad_area_reports_pads_whose_copper_layers_meet_it(self):
        for layers, through, back, reported in (
                (['In1.Cu'], True, False, True), (['In1.Cu'], True, True, True), (['In1.Cu'], False, False, False),
                (['F.Cu'], False, False, True), (['F.Cu'], False, True, False), (['B.Cu'], False, True, True),
                (['F.Cu'], True, True, True)):
            with self.subTest(layers=layers, through=through, back=back):
                _, found = self.drc(layers, ('pads',), through=through, back=back)
                self.assertEqual(bool(found), reported, found)
                if reported:
                    self.assertEqual(len(found), 1)
                    self.assertIn('pad 1', found[0].lower())
                    self.assertTrue(found[0].endswith('of C1') or ' of C1 on ' in found[0], found)
        # Over the courtyard but not the pad, a pad area reports nothing.
        self.assertEqual(self.drc(['F.Cu'], ('pads',), over='courtyard')[1], [])

    def test_owned_pad_area_reports_its_owner_and_extraction_records_the_flag(self):
        from agent_ee.placement_probe import extract
        path, found = self.drc(['F.Cu'], ('pads',), owned=True)
        self.assertEqual(found, ['Pad 1 [<no net>] of C1 on F.Cu'])
        facts = extract(path)
        rule, = facts['rule_areas']
        self.assertEqual(rule['forbid'], {'footprints': False, 'tracks': False, 'vias': False, 'pads': True,
                                          'zone_fills': False})
        self.assertEqual(rule['owner'], facts['footprints'][0]['uuid'])
        # The packing gate exempts the owner's own pad (rigid, pose-invariant, a baseline finding).
        placer._qualified(facts)
        placer._keepouts(facts)
        fp = facts['footprints'][0]
        self.assertEqual(len(fp['packing_pad_keepouts']), 1)
        self.assertIsNone(placer._copper_escapes(fp, placer._qualified(copy.deepcopy(facts)), [], []))


class NativePadKeepoutPublicationTest(NativeBoard, unittest.TestCase):
    """Native writer: a board pad-only keep-out x in [0, 3] mm; C1's lands stay out of it when published."""

    def setUp(self):
        super().setUp()
        self.netlist.write_text('<export><components><comp ref="C1"/></components><nets><net code="1" name="SIGNAL">'
                                '<node ref="C1" pin="1"/><node ref="C1" pin="2"/></net></nets></export>')

    def source(self, layers):
        import pcbnew as k

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
        board_ = k.BOARD()
        corners = [(0, 0), (20, 0), (20, 20), (0, 20)]
        for i, a in enumerate(corners):
            line(board_, k.Edge_Cuts, a, corners[(i + 1) % 4])
        net = k.NETINFO_ITEM(board_, 'SIGNAL')
        board_.Add(net)
        fp = k.FOOTPRINT(board_)
        fp.SetReference('C1')
        fp.SetValue('owned')
        board_.Add(fp)
        square = [(-2, -2), (2, -2), (2, 2), (-2, 2)]
        for i, a in enumerate(square):
            line(fp, k.F_CrtYd, a, square[(i + 1) % 4])
        fp.SetPosition(vec(10, 10))
        for n, dx in ((1, -1.5), (2, 1.5)):
            pad = k.PAD(fp)
            pad.SetNumber(str(n))
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetShape(k.F_Cu, k.PAD_SHAPE_RECT)
            pad.SetSize(k.F_Cu, vec(.6, .6))
            pad.SetLayerSet(k.PAD.SMDMask())
            pad.SetPosition(vec(10 + dx, 10))
            pad.SetNet(net)
            fp.Add(pad)
        zone = k.ZONE(board_)
        zone.SetIsRuleArea(True)
        layer_set = k.LSET()
        for layer in layers:
            layer_set.AddLayer(board_.GetLayerID(layer))
        zone.SetLayerSet(layer_set)
        zone.SetDoNotAllowFootprints(False)
        zone.SetDoNotAllowPads(True)
        zone.SetDoNotAllowTracks(False)
        zone.SetDoNotAllowVias(False)
        zone.SetDoNotAllowZoneFills(False)
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in ((0, 0), (3, 0), (3, 20), (0, 20)):
            outline.Append(int(x * MM), int(y * MM))
        board_.Add(zone)
        path = self.root / f"pad-keepout-{'-'.join(layers)}.kicad_pcb"
        k.SaveBoard(str(path), board_)
        return path

    def saved_lands(self, path):
        import pcbnew as k
        board_ = k.LoadBoard(str(path))
        return [(b.GetLeft(), b.GetTop(), b.GetRight(), b.GetBottom())
                for pad in next(iter(board_.GetFootprints())).Pads() for b in [pad.GetBoundingBox()]]

    def test_published_lands_stay_out_of_a_native_pad_keepout(self):
        from agent_ee.placement_probe import extract
        source = self.source(['F.Cu'])
        rule, = extract(source)['rule_areas']
        self.assertEqual((rule['owner'], rule['forbid']['pads'], rule['forbid']['footprints']), ('board', True, False))
        report = self.place(source, 'gated')
        self.assertEqual((report['status'], report['outcome'], report['open_defects']), ('placed', 'info', []), report)
        lands = self.saved_lands(self.scratch / 'gated' / 'candidate.kicad_pcb')
        self.assertTrue(all(x0 >= 3 * MM for x0, _, _, _ in lands), lands)
        self.assertEqual(self.not_allowed(self.scratch / 'gated'), [])
        # Courtyard-only gating saves a land in the keep-out (independent native geometry), which
        # native DRC reports as items_not_allowed.
        patches = courtyard_only()
        for p in patches:
            p.start()
        try:
            self.place(source, 'legacy')
        finally:
            for p in patches:
                p.stop()
        lands = self.saved_lands(self.scratch / 'legacy' / 'candidate.kicad_pcb')
        self.assertTrue(any(x0 < 3 * MM for x0, _, _, _ in lands), lands)
        self.assertEqual(self.not_allowed(self.scratch / 'legacy'), ['Pad 1 [SIGNAL] of C1 on F.Cu'])

    def not_allowed(self, root):
        report = json.loads((root / 'native-drc.json').read_text())
        return sorted(item['description'] for v in report['violations'] if v['type'] == 'items_not_allowed'
                      for item in v['items'])

    def test_inner_layer_pad_keepout_publishes_surface_lands_over_it(self):
        report = self.place(self.source(['In1.Cu']), 'inner')
        self.assertEqual((report['status'], report['outcome'], report['open_defects']), ('placed', 'info', []), report)
        lands = self.saved_lands(self.scratch / 'inner' / 'candidate.kicad_pcb')
        self.assertTrue(any(x0 < 3 * MM for x0, _, _, _ in lands), lands)
        self.assertEqual(self.not_allowed(self.scratch / 'inner'), [])


if __name__ == '__main__':
    unittest.main()

"""DFM geometry rules: exact probe facts, captured thresholds, fail-closed coverage."""
import copy
import json
import math
import tempfile
import time
import unittest
from pathlib import Path

from toolchain import require_toolchain

from agent_ee import candidate, checks
from agent_ee.baseline import freeze_evidence, validate_witnesses, verify_evidence
from agent_ee.capabilities import describe_check
from agent_ee.dfm_geometry import (
    FALSE_NEGATIVE_CLASSES, RULES, SUITE_LIMITATION, compile_profile, evaluate, rule_limitation,
)
from agent_ee.storage import write_json

import dfm_fixture

MM = 1_000_000


def p(x, y):
    return [round(x * MM), round(y * MM)]


def rect(x0, y0, x1, y1):
    return [[p(x0, y0), p(x1, y0), p(x1, y1), p(x0, y1)]]


def pad(uuid, ref, number, net, contours, *, attribute='SMD', footprint=None, **extra):
    box = contours[0]
    centre = [(box[0][0] + box[2][0]) // 2, (box[0][1] + box[2][1]) // 2]
    record = {'uuid': uuid, 'ref': ref, 'pad': number, 'net': net, 'attribute': attribute,
              'footprint_uuid': footprint or 'fp-' + ref, 'padstack_mode': 'NORMAL', 'shape': 'RECTANGLE',
              'position_nm': centre, 'size_nm': [box[2][0] - box[0][0], box[2][1] - box[0][1]],
              'offset_nm': [0, 0], 'drill_shape': 'CIRCLE', 'drill_nm': [0, 0],
              'copper_layers': ['F.Cu'], 'polygons_nm': {'F.Cu': contours}}
    record.update(extra)
    return record


def track(uuid, start, end, net='N', layer='F.Cu', mid=None):
    record = {'uuid': uuid, 'net': net, 'layer': layer, 'width_nm': 200000,
              'native_class': 'PCB_ARC' if mid else 'PCB_TRACK', 'start_nm': p(*start), 'end_nm': p(*end)}
    if mid:
        record['mid_nm'] = p(*mid)
    return record


def via(uuid, x, y, size, drill, kind='THROUGH'):
    return {'uuid': uuid, 'net': 'V', 'via_type': kind, 'padstack_mode': 'NORMAL', 'position_nm': p(x, y),
            'width_nm': round(size * MM), 'drill_nm': round(drill * MM),
            'layers': ['F.Cu', 'B.Cu'], 'copper_layers': ['F.Cu', 'B.Cu']}


def facts(**overrides):
    base = {'copper_layers': ['F.Cu', 'B.Cu'], 'board_thickness_nm': 1600000, 'max_error_nm': 5000,
            'pads': [], 'courtyards': [], 'tracks': [], 'vias': [],
            'copper_area': {'F.Cu': {'area_nm2': 10, 'bbox_nm': [0, 0, 1, 1], 'unfilled_zones': []},
                            'B.Cu': {'area_nm2': 10, 'bbox_nm': [0, 0, 1, 1], 'unfilled_zones': []}}}
    base.update(overrides)
    return base


def run(rule, **fact_overrides):
    profile = compile_profile({'schema': 'agent-ee.dfm-profile/1', 'name': 'unit', 'rules': [rule]})
    return evaluate(facts(**fact_overrides), profile)['rules'][0]


RING = {'id': 'ring', 'rule': 'annular_ring', 'min_ring_mm': 0.1}
ASPECT = {'id': 'aspect', 'rule': 'via_aspect_ratio', 'max_ratio': 8}
ACID = {'id': 'acid', 'rule': 'acid_trap', 'min_angle_deg': 45}
BRIDGE = {'id': 'bridge', 'rule': 'smd_bridge_gap', 'min_gap_mm': 0.2}
SPACING = {'id': 'spacing', 'rule': 'component_spacing', 'min_gap_mm': 0.5}
SKEW = {'id': 'skew', 'rule': 'diff_pair_skew', 'max_skew_mm': 0.5, 'pairs': 'name-suffix/1'}
BALANCE = {'id': 'balance', 'rule': 'copper_balance', 'max_layer_share': 0.85}


class AnnularRingTests(unittest.TestCase):
    def test_seeded_via_below_threshold_is_a_defect_at_its_coordinates(self):
        result = run(RING, vias=[via('bad', 22, 5, 0.4, 0.3), via('good', 20, 5, 0.6, 0.3)])
        self.assertEqual((result['execution'], result['status'], result['checked']), ('ok', 'defect', 2))
        [finding] = result['findings']
        self.assertEqual((finding['code'], finding['entity'], finding['at_mm'], finding['measured_mm']),
                         ('DFM-002', 'via:bad', [22.0, 5.0], 0.05))

    def test_compliant_via_and_exact_threshold_give_none(self):
        result = run(RING, vias=[via('edge', 20, 5, 0.5, 0.3)])
        self.assertEqual((result['status'], result['findings']), ('pass', []))
        # One nanometre short of the ring is still a defect: no float rounding into a pass.
        self.assertEqual(run(RING, vias=[{**via('nm', 20, 5, 0.5, 0.3), 'width_nm': 499999}])['status'],
                         'defect')

    def test_pth_pad_ring_uses_drill_offset_and_refuses_unqualified_shapes(self):
        tht = pad('tht', 'J1', '1', 'N', rect(0, 0, 1.0, 1.0), attribute='PTH',
                  drill_nm=[600000, 600000], offset_nm=[150000, 0])
        result = run(RING, pads=[tht])
        self.assertEqual(result['findings'][0]['measured_mm'], 0.05)
        custom = {**tht, 'uuid': 'custom', 'shape': 'CUSTOM'}
        result = run(RING, pads=[custom])
        self.assertEqual((result['execution'], result['status']), ('unsupported', 'not_evaluated'))
        self.assertEqual(result['unsupported'][0]['entity'], 'pad:custom')

    def test_per_layer_padstack_is_not_assumed_uniform(self):
        result = run(RING, vias=[{**via('stack', 1, 1, 0.6, 0.3), 'padstack_mode': 'CUSTOM'}])
        self.assertEqual(result['execution'], 'unsupported')


class AspectRatioTests(unittest.TestCase):
    def test_missing_stackup_thickness_is_unsupported_never_pass(self):
        for value in (None, 0, True, '1.6'):
            with self.subTest(value=value):
                result = run(ASPECT, board_thickness_nm=value, vias=[via('v', 1, 1, 0.6, 0.3)])
                self.assertEqual((result['execution'], result['status']), ('unsupported', 'not_evaluated'))
                self.assertEqual(result['findings'], [])
        missing = facts(vias=[via('v', 1, 1, 0.6, 0.3)])
        del missing['board_thickness_nm']
        profile = compile_profile({'schema': 'agent-ee.dfm-profile/1', 'name': 'unit', 'rules': [ASPECT]})
        self.assertEqual(evaluate(missing, profile)['rules'][0]['execution'], 'unsupported')
        # Even with no vias, a missing thickness never becomes a vacuous pass.
        self.assertEqual(run(ASPECT, board_thickness_nm=None)['execution'], 'unsupported')

    def test_ratio_threshold_and_non_through_vias(self):
        result = run(ASPECT, vias=[via('thin', 3, 4, 0.4, 0.15), via('ok', 1, 1, 0.6, 0.3)])
        [finding] = result['findings']
        self.assertEqual((finding['code'], finding['at_mm'], finding['measured_ratio']),
                         ('DFM-008', [3.0, 4.0], round(1.6 / 0.15, 6)))
        blind = run(ASPECT, vias=[via('blind', 1, 1, 0.6, 0.3, kind='BLIND')])
        self.assertEqual(blind['execution'], 'unsupported')


class AcidTrapTests(unittest.TestCase):
    def junction(self, degrees, **extra):
        a = math.radians(degrees)
        return run(ACID, tracks=[track('t1', (5, 20), (10, 20)),
                                 track('t2', (10, 20), (10 - 5 * math.cos(a), 20 - 5 * math.sin(a)))], **extra)

    def test_thirty_degree_shared_endpoint_is_an_acid_trap(self):
        result = self.junction(30)
        [finding] = result['findings']
        self.assertEqual((finding['code'], finding['at_mm'], finding['items'], finding['layer']),
                         ('DFM-003', [10.0, 20.0], ['t1', 't2'], 'F.Cu'))
        self.assertAlmostEqual(finding['measured_deg'], 30, places=4)

    def test_ninety_degree_junction_gives_none(self):
        result = self.junction(90)
        self.assertEqual((result['status'], result['checked'], result['findings']), ('pass', 1, []))

    def test_different_layers_do_not_form_a_junction(self):
        result = run(ACID, tracks=[track('t1', (5, 20), (10, 20)),
                                   track('t2', (10, 20), (6, 18), layer='B.Cu')])
        self.assertEqual((result['status'], result['checked']), ('not_applicable', 0))

    def test_arc_uses_its_endpoint_tangent_not_its_chord(self):
        # Chord from (10,20) to (6,20) runs along t1 (0 degrees) but the arc leaves upward at 90.
        arc = track('arc', (10, 20), (6, 20), mid=(8, 18))
        result = run(ACID, tracks=[track('t1', (10, 20), (14, 20)), arc])
        self.assertEqual(result['findings'], [])
        shallow = track('arc2', (10, 20), (14, 20.5), mid=(12, 20.5))
        result = run(ACID, tracks=[track('t1', (10, 20), (14, 20)), shallow])
        self.assertEqual(len(result['findings']), 1)

    def test_junction_inside_pad_copper_is_covered_not_a_wedge(self):
        result = self.junction(30, pads=[pad('pad', 'U1', '1', 'N', rect(9.5, 19.5, 10.5, 20.5))])
        self.assertEqual((result['findings'], result['pad_or_via_covered_junctions']), ([], 1))

    def test_tracks_sharing_both_endpoints_give_distinct_findings(self):
        loop = [track('t1', (0, 0), (4, 0)), track('arc', (0, 0), (4, 0), mid=(2, 0.5))]
        result = run(ACID, tracks=loop)
        self.assertEqual([f['at_mm'] for f in result['findings']], [[0.0, 0.0], [4.0, 0.0]])
        self.assertEqual(len({f['entity'] for f in result['findings']}), 2)

    def test_degenerate_arc_is_unsupported(self):
        bad = track('arc', (10, 20), (6, 20), mid=(8, 20))
        result = run(ACID, tracks=[track('t1', (10, 20), (14, 20)), bad])
        self.assertEqual(result['execution'], 'unsupported')


class BridgeGapTests(unittest.TestCase):
    def pads(self, gap, net_b='B', footprint_b=None):
        return [pad('a', 'R1', '2', 'A', rect(10.7, 9.6, 11.3, 10.4)),
                pad('b', 'R2', '1', net_b, rect(11.3 + gap, 9.6, 11.9 + gap, 10.4), footprint=footprint_b)]

    def test_point_one_five_mm_gap_between_different_net_footprints_is_bridge_risk(self):
        result = run(BRIDGE, pads=self.pads(0.15))
        [finding] = result['findings']
        self.assertEqual((finding['code'], finding['measured_mm'], finding['refs'], finding['pads'],
                          finding['nets'], finding['at_mm']),
                         ('DFM-010', 0.15, ['R1', 'R2'], ['R1:2', 'R2:1'], ['A', 'B'], [11.375, 9.6]))

    def test_real_rectangles_not_centre_radius(self):
        # Diagonal neighbours 0.15 mm apart on both axes are 0.212 mm apart corner to corner.
        a = pad('a', 'R1', '1', 'A', rect(0, 0, 1, 1))
        b = pad('b', 'R2', '1', 'B', rect(1.15, 1.15, 2.15, 2.15))
        self.assertEqual(run(BRIDGE, pads=[a, b])['findings'], [])
        rotated = pad('r', 'R3', '1', 'B', [[p(1.3, 0.5), p(1.8, 0.0), p(2.3, 0.5), p(1.8, 1.0)]])
        # A 45-degree pad whose vertex sits 0.3 mm from the square's edge.
        [finding] = run({**BRIDGE, 'min_gap_mm': 0.35}, pads=[a, rotated])['findings']
        self.assertAlmostEqual(finding['measured_mm'], 0.3, places=6)
        self.assertEqual(run({**BRIDGE, 'min_gap_mm': 0.3}, pads=[a, rotated])['findings'], [])

    def test_same_net_same_footprint_or_wide_gap_give_none(self):
        for pads in (self.pads(0.15, net_b='A'), self.pads(0.15, footprint_b='fp-R1'), self.pads(0.25)):
            with self.subTest(pads=pads[1]):
                self.assertEqual(run(BRIDGE, pads=pads)['findings'], [])

    def test_overlap_is_zero_gap_and_missing_polygon_is_unsupported(self):
        self.assertEqual(run(BRIDGE, pads=self.pads(-0.1))['findings'][0]['measured_mm'], 0.0)
        broken = self.pads(0.15)
        broken[1]['polygons_nm'] = {}
        self.assertEqual(run(BRIDGE, pads=broken)['execution'], 'unsupported')


class SmdViaClearanceTests(unittest.TestCase):
    rule = {'id': 'smd-vias', 'rule': 'smd_via_clearance', 'min_gap_mm': 0.15}

    def test_same_net_via_disk_is_checked_at_exact_clearance_boundary(self):
        target = pad('p', 'U1', '1', 'V', rect(-0.5, -0.5, 0.5, 0.5))
        for x, expected in [(0, 'defect'), (0.7, 'defect'), (0.949999, 'defect'),
                            (0.95, 'pass'), (0.950001, 'pass')]:
            with self.subTest(x=x):
                result = run(self.rule, pads=[target], vias=[via('v', x, 0, 0.6, 0.3)])
                self.assertEqual((result['execution'], result['status']), ('ok', expected), result)
                self.assertEqual(result['checked'], 1)
                if expected == 'defect':
                    self.assertEqual(result['findings'][0]['refs'], ['U1'])
                    self.assertEqual(result['findings'][0]['items'], ['v', 'p'])

    def test_only_shared_outer_layer_smd_copper_is_measured_and_missing_geometry_refuses(self):
        target = pad('p', 'U1', '1', 'V', rect(-0.5, -0.5, 0.5, 0.5))
        buried = via('v', 0, 0, 0.6, 0.3)
        buried['copper_layers'] = ['In1.Cu', 'In2.Cu']
        self.assertEqual(run(self.rule, pads=[target], vias=[buried],
            copper_layers=['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'])['status'], 'not_applicable')
        for patch in [dict(polygons_nm={}), dict(shape='CUSTOM'), dict(padstack_mode='CUSTOM'), dict(copper_layers=[])]:
            with self.subTest(patch=patch):
                result = run(self.rule, pads=[{**target, **patch}], vias=[via('v', 2, 0, 0.6, 0.3)])
                self.assertEqual(result['execution'], 'unsupported', result)
        result = run(self.rule, pads=[target], vias=[{**via('v', 2, 0, 0.6, 0.3), 'padstack_mode': 'CUSTOM'}])
        self.assertEqual(result['execution'], 'unsupported', result)

    def test_closed_native_polygon_with_repeated_vertex_preserves_clearance(self):
        target = pad('p', 'U1', '1', 'V', rect(-0.5, -0.5, 0.5, 0.5))
        contour = target['polygons_nm']['F.Cu'][0]
        contour.append(contour[0])
        for x, status in [(0.949999, 'defect'), (0.95, 'pass')]:
            result = run(self.rule, pads=[target], vias=[via('v', x, 0, 0.6, 0.3)])
            self.assertEqual((result['execution'], result['status']), ('ok', status), result)


class SpacingTests(unittest.TestCase):
    def courtyard(self, ref, contours, **extra):
        return {'ref': ref, 'footprint_uuid': 'fp-' + ref, 'side': 'F.Cu', 'pad_count': 2,
                'board_only': False, 'exclude_from_pos_files': False, 'position_nm': [0, 0],
                'polygons_nm': contours, 'holes': 0, **extra}

    def test_courtyard_gap_below_minimum(self):
        result = run(SPACING, courtyards=[self.courtyard('R1', rect(0, 0, 2, 1)),
                                          self.courtyard('R2', rect(2.3, 0, 4, 1)),
                                          self.courtyard('R3', rect(10, 0, 12, 1))])
        [finding] = result['findings']
        self.assertEqual((finding['code'], finding['refs'], finding['measured_mm']),
                         ('DFM-006', ['R1', 'R2'], 0.3))

    def test_missing_courtyard_is_unsupported_and_unplaced_parts_are_skipped(self):
        result = run(SPACING, courtyards=[self.courtyard('R1', [])])
        self.assertEqual(result['execution'], 'unsupported')
        result = run(SPACING, courtyards=[self.courtyard('H1', [], board_only=True),
                                          self.courtyard('H2', [], exclude_from_pos_files=True)])
        self.assertEqual((result['execution'], result['status']), ('ok', 'not_applicable'))


class SkewTests(unittest.TestCase):
    def tracks(self, arc=True):
        tail = (track('dp2', (15, 25), (17, 25), 'USB_D+', mid=(16, 24)) if arc
                else track('dp2', (15, 25), (17, 25), 'USB_D+'))
        return [track('dp1', (5, 25), (15, 25), 'USB_D+'), tail,
                track('dn1', (5, 27), (15, 27), 'USB_D-'), track('dn2', (15, 27), (17, 27), 'USB_D-')]

    def test_usb_pair_skew_includes_arc_length(self):
        result = run(SKEW, tracks=self.tracks())
        [finding] = result['findings']
        self.assertEqual((finding['code'], finding['nets'], finding['arcs']), ('DFM-012', ['USB_D+', 'USB_D-'], 1))
        self.assertAlmostEqual(finding['lengths_mm'][0], 10 + math.pi, places=5)
        self.assertAlmostEqual(finding['measured_mm'], math.pi - 2, places=5)

    def test_chord_twin_and_declared_pairs(self):
        self.assertEqual(run(SKEW, tracks=self.tracks(arc=False))['status'], 'pass')
        declared = {**SKEW, 'pairs': [['USB_D+', 'USB_D-']]}
        self.assertEqual(run(declared, tracks=self.tracks())['status'], 'defect')

    def test_unrouted_or_absent_partner_is_unsupported(self):
        result = run(SKEW, tracks=self.tracks()[:2], pads=[pad('x', 'J1', '1', 'USB_D-', rect(0, 0, 1, 1))])
        self.assertEqual(result['execution'], 'unsupported')
        missing = run({**SKEW, 'pairs': [['TX_P', 'TX_N']]}, tracks=self.tracks())
        self.assertEqual(missing['execution'], 'unsupported')
        self.assertEqual(run(SKEW, tracks=[])['status'], 'not_applicable')


class BalanceTests(unittest.TestCase):
    def areas(self, top, bottom, unfilled=()):
        return {'F.Cu': {'area_nm2': top, 'bbox_nm': [0, 0, 2 * MM, 2 * MM], 'unfilled_zones': list(unfilled)},
                'B.Cu': {'area_nm2': bottom, 'bbox_nm': [0, 0, 1, 1], 'unfilled_zones': []}}

    def test_unbalanced_layer_pair(self):
        [finding] = run(BALANCE, copper_area=self.areas(90, 10))['findings']
        self.assertEqual((finding['code'], finding['layers'], finding['measured_share'], finding['at_mm']),
                         ('DFM-009', ['F.Cu', 'B.Cu'], 0.9, [1.0, 1.0]))
        self.assertEqual(run(BALANCE, copper_area=self.areas(60, 40))['status'], 'pass')

    def test_unfilled_zone_or_missing_area_is_unsupported(self):
        self.assertEqual(run(BALANCE, copper_area=self.areas(60, 40, ['z']))['execution'], 'unsupported')
        self.assertEqual(run(BALANCE, copper_area={})['execution'], 'unsupported')


class ProfileTests(unittest.TestCase):
    def test_every_threshold_is_explicit(self):
        compile_profile(dfm_fixture.PROFILE)
        bad_rules = ({'id': 'r', 'rule': 'annular_ring'}, {**RING, 'min_ring_mm': float('nan')},
                     {**RING, 'min_ring_mm': True}, {**RING, 'min_ring_mm': 0}, {**RING, 'extra': 1},
                     {**RING, 'rule': 'etch_compensation'}, {**ACID, 'min_angle_deg': 180},
                     {**BALANCE, 'max_layer_share': 0.4}, {**SKEW, 'pairs': [['A', 'A']]},
                     {**SKEW, 'pairs': [['A', 'B'], ['B', 'C']]}, {**SKEW, 'pairs': 'kicad-classes'},
                     {**RING, 'id': 'Bad Id'})
        for rule in bad_rules:
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                compile_profile({'schema': 'agent-ee.dfm-profile/1', 'name': 'x', 'rules': [rule]})
        for document in ({'schema': 'agent-ee.dfm-profile/1', 'name': 'x', 'rules': []},
                         {'schema': 'agent-ee.dfm-profile/2', 'name': 'x', 'rules': [RING]},
                         {'schema': 'agent-ee.dfm-profile/1', 'name': 'x', 'rules': [RING, RING]},
                         {'schema': 'agent-ee.dfm-profile/1', 'name': 'x', 'rules': [RING], 'score': 1}):
            with self.subTest(document=document), self.assertRaises(ValueError):
                compile_profile(document)

    def test_missing_fact_section_or_malformed_items_never_pass(self):
        profile = compile_profile(dfm_fixture.PROFILE)
        with self.assertRaises(ValueError):
            evaluate(None, profile)
        with self.assertRaises(ValueError):
            evaluate({'pads': []}, profile)
        broken = facts(vias=[{'uuid': 'v'}], tracks=[{'uuid': 't'}])
        report = evaluate(broken, profile)
        by_id = {r['id']: r for r in report['rules']}
        for rid in ('annular-ring', 'via-aspect', 'acid-trap', 'diff-skew'):
            self.assertEqual(by_id[rid]['execution'], 'unsupported', rid)

    def test_each_rule_lists_definition_bound_false_negative_classes_and_exercised_class_is_never_pass(self):
        for kind, (_, _, classes) in RULES.items():
            with self.subTest(rule=kind):
                self.assertTrue(classes)
                self.assertEqual(FALSE_NEGATIVE_CLASSES[kind], classes)
                self.assertTrue(all(isinstance(c, str) and c.strip() for c in classes))
        profile = compile_profile(dfm_fixture.PROFILE)
        expected_limits = [SUITE_LIMITATION, *(rule_limitation(r) for r in profile['rules'])]
        empty_report = evaluate(facts(), profile)
        broken_report = evaluate(facts(vias=[{'uuid': 'v'}], tracks=[{'uuid': 't'}], board_thickness_nm=None), profile)
        for report in (empty_report, broken_report):
            self.assertEqual(report['limitations'], expected_limits)
            for rule in report['rules']:
                self.assertEqual(rule['false_negative_classes'], list(FALSE_NEGATIVE_CLASSES[rule['rule']]))
        exercised = (
            (RING, {'pads': [{**pad('c', 'J1', '1', 'N', rect(0, 0, 1, 1), attribute='PTH',
                                    drill_nm=[400000, 400000]), 'shape': 'CUSTOM'}]}),
            (ASPECT, {'vias': [via('b', 1, 1, 0.6, 0.3, kind='BLIND')]}),
            (ACID, {'tracks': [track('t1', (10, 20), (14, 20)), track('arc', (10, 20), (6, 20), mid=(8, 20))]}),
            (BRIDGE, {'pads': [{**pad('s', 'R1', '1', 'A', rect(0, 0, 1, 1)), 'polygons_nm': {}}]}),
            (SPACING, {'courtyards': [{'ref': 'R1', 'footprint_uuid': 'fp-R1', 'side': 'F.Cu', 'pad_count': 2,
                                       'board_only': False, 'exclude_from_pos_files': False,
                                       'position_nm': [0, 0], 'polygons_nm': [], 'holes': 0}]}),
            (SKEW, {'pads': [pad('p', 'J1', '1', 'USB_D+', rect(0, 0, 1, 1)),
                             pad('n', 'J1', '2', 'USB_D-', rect(2, 0, 3, 1))],
                    'tracks': [track('dp', (0, 0), (5, 0), 'USB_D+')]}),
            (BALANCE, {'copper_area': {'F.Cu': {'area_nm2': 50, 'bbox_nm': [0, 0, 1, 1], 'unfilled_zones': ['z1']},
                                       'B.Cu': {'area_nm2': 50, 'bbox_nm': [0, 0, 1, 1], 'unfilled_zones': []}}}),
        )
        for spec, overrides in exercised:
            with self.subTest(exercised=spec['rule']):
                result = run(spec, **overrides)
                self.assertEqual((result['execution'], result['status']), ('unsupported', 'not_evaluated'))
                self.assertEqual(result['false_negative_classes'], list(FALSE_NEGATIVE_CLASSES[spec['rule']]))


class SavedThicknessTests(unittest.TestCase):
    def thickness(self, text):
        from agent_ee.geometry_probe import saved_thickness
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'board.kicad_pcb'
            path.write_text(text)
            return saved_thickness(path)

    def test_only_a_root_general_thickness_is_a_saved_fact(self):
        self.assertEqual(self.thickness('(kicad_pcb (version 1) (general (thickness 1.2)) (layers))'),
                         (1200000, None))
        decoy = '(kicad_pcb (generator "(general (thickness 9))") (general (thickness 0.8)) (layers))'
        self.assertEqual(self.thickness(decoy), (800000, None))
        for text in ('(kicad_pcb (version 1) (setup (general (thickness 1.2))))',
                     '(kicad_pcb (version 1) (general (legacy_teardrops no)))',
                     '(kicad_pcb (version 1) (general (thickness 1.2 1.3)))', 'not a board'):
            with self.subTest(text=text):
                value, problem = self.thickness(text)
                self.assertIsNone(value)
                self.assertTrue(problem)


class NativeDfmTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_board(self, profile=None, **board):
        source = self.root / 'source'
        source.mkdir()
        dfm_fixture.make_board(source / 'board.kicad_pcb', **board)
        write_json(source / 'dfm-profile.json', profile or dfm_fixture.PROFILE)
        (source / 'requirements.md').write_text('R-dfm: owned DFM geometry thresholds')
        scope = {'schema': 'agent-ee.scope/2', 'inputs': [{'path': str(source), 'at': 'project'}],
                 'board': 'project/board.kicad_pcb',
                 'checks': [{'id': 'dfm', 'kind': 'dfm_geometry',
                             'params': {'dfm_profile': 'project/dfm-profile.json'}}],
                 'baseline': {'requirements': [{'id': 'R-dfm', 'statement': 'Declared DFM geometry',
                     'source': {'path': 'project/requirements.md', 'locator': 'R-dfm'}, 'checks': ['dfm']}],
                     'targets': ['project/requirements.md'],
                     'reference_architectures': ['project/requirements.md']}}
        (self.root / 'scope.json').write_text(json.dumps(scope))
        run = self.root / 'run'
        run.mkdir()
        candidate.capture(self.root / 'scope.json', run)
        [result] = checks.run_checks(run, time.monotonic() + 300)
        return run, result

    def assert_runtime_limitations(self, result, report=None, profile=None):
        compiled = compile_profile(profile or dfm_fixture.PROFILE)
        expected = [SUITE_LIMITATION, *(rule_limitation(r) for r in compiled['rules'])]
        self.assertEqual(result['limitations'], expected)
        joined = '\n'.join(result['limitations'])
        for phrase in ('mid-track T-junction acid traps are not detected',
                       'junctions inside pad copper or a via disk count as covered',
                       'ERROR_OUTSIDE', 'custom/trapezoid/chamfered PTH pads', 'unfilled zones'):
            if profile is None:
                self.assertIn(phrase, joined)
        for rule in result['details']['rules']:
            self.assertEqual(rule['false_negative_classes'], list(FALSE_NEGATIVE_CLASSES[rule['rule']]))
        if report is not None:
            self.assertEqual(report['limitations'], expected)
            for rule in report['rules']:
                self.assertEqual(rule['false_negative_classes'], list(FALSE_NEGATIVE_CLASSES[rule['rule']]))

    def test_same_net_via_in_smd_pad_is_rejected_from_native_geometry(self):
        profile = dict(schema='agent-ee.dfm-profile/1', name='no-via-in-pad', rules=[SmdViaClearanceTests.rule])
        run_dir, result = self.run_board(profile=profile, same_net_smd_via=True)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        finding = next(f for f in result['details']['findings'] if f['pad'] == '1')
        self.assertEqual(finding['nets'], ['NET_A', 'NET_A'])
        self.assertEqual((finding['refs'], finding['measured_mm']), (['R1'], 0))
        freeze_evidence(run_dir, [result])
        verify_evidence(run_dir, [result], required=True)

    def test_native_vias_clear_of_smd_pads_pass(self):
        profile = dict(schema='agent-ee.dfm-profile/1', name='no-via-in-pad', rules=[SmdViaClearanceTests.rule])
        _, result = self.run_board(profile=profile)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertGreater(result['details']['rules'][0]['checked'], 0)

    def test_seeded_board_reports_every_geometric_defect_through_the_check(self):
        run, result = self.run_board()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result['summary'])
        report = json.loads((run / 'work/dfm/dfm-report.json').read_text())
        self.assertEqual(report['candidate_id'], result['candidate_id'])
        self.assert_runtime_limitations(result, report)
        rules = {r['id']: r for r in report['rules']}
        self.assertEqual({k: r['status'] for k, r in rules.items()},
                         {'acid-trap': 'defect', 'annular-ring': 'defect', 'component-spacing': 'defect',
                          'copper-balance': 'pass', 'diff-skew': 'defect', 'smd-bridge': 'defect',
                          'via-aspect': 'pass'})
        [ring] = rules['annular-ring']['findings']
        self.assertEqual((ring['at_mm'], ring['measured_mm']), ([22.0, 5.0], 0.05))
        [acid] = rules['acid-trap']['findings']
        self.assertEqual(acid['at_mm'], [10.0, 20.0])
        self.assertAlmostEqual(acid['measured_deg'], 30, places=3)
        [bridge] = rules['smd-bridge']['findings']
        self.assertEqual((bridge['measured_mm'], bridge['refs'], bridge['pads']),
                         (0.15, ['R1', 'R2'], ['R1:2', 'R2:1']))
        [skew] = rules['diff-skew']['findings']
        self.assertEqual(skew['arcs'], 1)
        self.assertAlmostEqual(skew['measured_mm'], math.pi - 2, places=4)
        # The re-implemented arc length agrees with pcbnew's own PCB_ARC length.
        geo = json.loads((run / 'work/dfm/geometry.json').read_text())
        native = sum(t['length_mm'] for t in geo['tracks'] if t['net'] == 'USB_D+')
        self.assertAlmostEqual(native, skew['lengths_mm'][0], places=3)
        kinds = sorted({g['kind'] for g in result['details']['gate_findings']})
        self.assertEqual(kinds, ['dfm/acid-trap', 'dfm/annular-ring', 'dfm/component-spacing',
                                 'dfm/diff-skew', 'dfm/smd-bridge'])
        freeze_evidence(run, [result])
        verify_evidence(run, [result], required=True)
        validate_witnesses(run, [{'requirement_id': 'R-dfm', 'check_id': 'dfm'}],
                           [{'path': 'checks/dfm.json', 'locator': 'details.findings'}])

    def test_compliant_twin_passes_every_rule(self):
        run, result = self.run_board(via_size=0.6, bridge_gap=0.3, acid_angle=90, arc=False)
        report = json.loads((run / 'work/dfm/dfm-report.json').read_text())
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result['details'])
        self.assertEqual(result['details']['findings'], [])
        self.assertEqual({r['status'] for r in result['details']['rules']}, {'pass'})
        self.assert_runtime_limitations(result, report)

    def test_copper_balance_measures_united_native_copper(self):
        profile = copy.deepcopy(dfm_fixture.PROFILE)
        profile['rules'] = [{'id': 'copper-balance', 'rule': 'copper_balance', 'max_layer_share': 0.9}]
        run, result = self.run_board(profile)
        self.assertEqual(result['outcome'], 'defect')
        [finding] = result['details']['findings']
        self.assertEqual(finding['layers'], ['F.Cu', 'B.Cu'])
        self.assertGreater(finding['measured_share'], 0.9)
        self.assert_runtime_limitations(result, profile=profile)

    def test_board_without_saved_thickness_leaves_aspect_ratio_unsupported(self):
        run, result = self.run_board(thickness=False)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
        rules = {r['id']: r for r in result['details']['rules']}
        self.assertEqual((rules['via-aspect']['execution'], rules['via-aspect']['status']),
                         ('unsupported', 'not_evaluated'))
        self.assertIn('thickness', rules['via-aspect']['reason'])
        self.assert_runtime_limitations(result)
        freeze_evidence(run, [result])
        with self.assertRaisesRegex(ValueError, 'failed criterion'):
            validate_witnesses(run, [{'requirement_id': 'R-dfm', 'check_id': 'dfm'}],
                               [{'path': 'checks/dfm.json', 'locator': 'outcome'}])

    def test_missing_courtyards_fail_closed(self):
        _, result = self.run_board(courtyards=False, via_size=0.6, bridge_gap=0.3, acid_angle=90, arc=False)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
        self.assertIn('component-spacing', result['summary'])
        self.assert_runtime_limitations(result)

    def test_board_exercising_custom_pad_or_unfilled_zone_gets_unknown_not_pass(self):
        for flag, rule_id in (('custom_pad', 'annular-ring'), ('unfilled_zone', 'copper-balance')):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as sub:
                self.root = Path(sub)
                _, result = self.run_board(via_size=0.6, bridge_gap=0.3, acid_angle=90, arc=False, **{flag: True})
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
                by_id = {r['id']: r for r in result['details']['rules']}
                self.assertEqual((by_id[rule_id]['execution'], by_id[rule_id]['status']),
                                 ('unsupported', 'not_evaluated'))
                self.assert_runtime_limitations(result)


class CapabilityTests(unittest.TestCase):
    def test_profile_is_an_immutable_input_with_one_predicate_per_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp)
            (inputs / 'board.kicad_pcb').write_text('(kicad_pcb)')
            write_json(inputs / 'dfm.json', dfm_fixture.PROFILE)
            from agent_ee.storage import digest
            files = {name: digest(inputs / name) for name in ('board.kicad_pcb', 'dfm.json')}
            spec = {'id': 'dfm', 'kind': 'dfm_geometry', 'params': {'dfm_profile': 'dfm.json'}}
            scope = {'board': 'board.kicad_pcb', 'checks': [spec]}
            result = describe_check(spec, check_specs=[spec], files=files, scope=scope, inputs=inputs)
            self.assertEqual(result['status'], 'supported', result)
            self.assertEqual(result['immutable_inputs'], ['dfm.json'])
            self.assertEqual(sorted(result['predicates']),
                             sorted('dfm:' + r['id'] for r in dfm_fixture.PROFILE['rules']))
            spec['params'] = {'dfm_profile': 'dfm.json', 'min_ring_mm': 0.1}
            self.assertEqual(describe_check(spec, check_specs=[spec], files=files, scope=scope,
                                            inputs=inputs)['status'], 'unsupported')


if __name__ == '__main__':
    unittest.main()


class CustomSmdCoverageTests(unittest.TestCase):
    rule = {'id':'custom-gap', 'rule':'smd_via_clearance', 'min_gap_mm':0.1}

    def custom(self):
        import hashlib
        contours=rect(-0.5,-0.5,3,0.5)
        target=pad('custom','U1','2','V',contours,shape='CUSTOM')
        topology=dict(saved=dict(outlines_nm=contours,holes_nm=[[]],arcs=0),
                      unfractured=dict(outlines_nm=contours,holes_nm=[[]],arcs=0))
        target['custom_copper']=dict(schema='agent-ee.custom-smd-copper/1',status='qualified',
            pad_uuid=target['uuid'],footprint_uuid=target['footprint_uuid'],
            recipe='single-filled-gr_poly+rect-anchor/1',api='GetEffectivePolygon/ERROR_OUTSIDE',
            max_error_nm=5000,source_pad_sha256='a'*64,source_vertex_count=4,
            anchor_shape='RECTANGLE',native_anchor=1,merged_pad_local_polygons_nm=contours,
            layers={'F.Cu':dict(status='complete',whole_shape_covered=True,topology=topology,polygon_sha256=hashlib.sha256(
                json.dumps(contours,separators=(',',':')).encode()).hexdigest())})
        return target

    def test_standard_only_dfm_facts_avoid_full_source_reader(self):
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from agent_ee import geometry_probe
        empty=lambda:NS(Simplify=lambda:None,OutlineCount=lambda:0,Area=lambda:0)
        sdk=NS(SHAPE_POLY_SET=empty,F_Cu=0,B_Cu=2,ERROR_INSIDE=0)
        board=NS(GetDesignSettings=lambda:NS(m_MaxError=5000,GetBoardThickness=lambda:1600000),
                 GetEnabledLayers=lambda:NS(CuStack=lambda:[0,2]),GetLayerName=lambda l:{0:'F.Cu',2:'B.Cu'}[l],
                 GetFootprints=lambda:[],GetTracks=lambda:[],GetDrawings=lambda:[],Zones=lambda:[])
        with tempfile.TemporaryDirectory() as tmp, patch.dict('sys.modules',pcbnew=sdk):
            path=Path(tmp)/'large.kicad_pcb'
            path.write_bytes(b'(kicad_pcb (general (thickness 1.6)))'+b' '*(32*1024*1024+1))
            with patch('agent_ee.placement_probe._custom_pad_nodes',side_effect=AssertionError('full source parsed')):
                result=geometry_probe.dfm_facts(board,str(path))
            self.assertEqual(result['board_thickness_nm'],1600000)
            self.assertEqual(result['pads'],[])

    def test_unreadable_custom_source_retains_explained_refusal(self):
        from agent_ee.geometry_probe import _custom_source_nodes
        from types import SimpleNamespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp, patch.dict('sys.modules',pcbnew=SimpleNamespace()):
            path=Path(tmp)/'bad.kicad_pcb';path.write_bytes(b'(')
            nodes,error=_custom_source_nodes(path)
            self.assertEqual(nodes,{})
            self.assertIn('LibraryError',error)
            self.assertIn('expected one complete',error)

    def test_default_native_fixture_constructs_complete_board_and_rejects_malformed_input(self):
        # Only the SDK is doubled: exercise the actual fixture builder/parser.
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from agent_ee.symbol_library import parse_sexpr
        loaded=[]
        orientations=[]
        fp=NS(Pads=lambda:[NS(SetNet=lambda net:None)],SetOrientationDegrees=orientations.append)
        board=NS(SetCopperLayerCount=lambda count:None,GetFootprints=lambda:[fp],Add=lambda item:None)
        def save(path, native, *args):
            if not Path(path).exists(): Path(path).write_bytes(b'(kicad_pcb (version 20260206))')
            return True
        def load(path):
            loaded.append(parse_sexpr(Path(path).read_bytes()))
            return board
        sdk=NS(BOARD=lambda:board,SaveBoard=save,LoadBoard=load,NETINFO_ITEM=lambda *args:object())
        with tempfile.TemporaryDirectory() as tmp, patch.dict('sys.modules',pcbnew=sdk):
            path=NativeCustomSmdTests.fixture(Path(tmp)/'complete',angle=90)
            self.assertEqual(parse_sexpr(path.read_bytes())[0],'kicad_pcb')
            self.assertEqual(len(loaded),1)
            self.assertEqual(orientations,[90])
            with self.assertRaisesRegex(ValueError,'generated custom DFM fixture malformed'):
                NativeCustomSmdTests.fixture(Path(tmp)/'bad',raw_pad='(pad "1" smd custom')
            self.assertEqual(len(loaded),1)

    def test_custom_extension_uses_full_via_disk_and_exact_point1_gap(self):
        target=self.custom()
        for x,outcome in ((2,'defect'),(3.399999,'defect'),(3.4,'pass'),(3.400001,'pass')):
            with self.subTest(x=x):
                result=run(self.rule,pads=[target],vias=[via('v',x,0,0.6,0.3)])
                self.assertEqual((result['execution'],result['status']),('ok',outcome),result)
                self.assertEqual(result['checked'],1)

    def test_custom_missing_holes_unknown_topology_stack_and_polygon_drift_refuse(self):
        variants=[]
        a=self.custom();a.pop('custom_copper');variants.append(a)
        for patch in (dict(padstack_mode='CUSTOM'),dict(copper_layers=[]),dict(copper_layers=['F.Cu','B.Cu'])):
            a=self.custom();a.update(patch);variants.append(a)
        for field,value in (('status','unsupported'),('pad_uuid','other'),('source_pad_sha256','invalid'),('layers',{}),('merged_pad_local_polygons_nm',[]),('anchor_shape','CIRCLE')):
            a=self.custom();a['custom_copper'][field]=value;variants.append(a)
        for stage in ('saved','unfractured'):
            a=self.custom();a['custom_copper']['layers']['F.Cu']['topology'][stage]['holes_nm']=[[[[0,0],[1,0],[0,1]]]];variants.append(a)
        a=self.custom();a['polygons_nm']['F.Cu'][0][1][0]+=1;variants.append(a)
        a=self.custom();a['custom_copper']['layers']['F.Cu']['topology'].pop('unfractured');variants.append(a)
        a=self.custom();a['custom_copper']['layers']['F.Cu'].pop('whole_shape_covered');variants.append(a)
        for i,target in enumerate(variants):
            with self.subTest(i=i):
                result=run(self.rule,pads=[target],vias=[via('v',4,0,0.6,0.3)])
                self.assertEqual(result['execution'],'unsupported',result)


class NativeCustomSmdTests(unittest.TestCase):
    """Native saved fixtures retain their inputs/probe/results under ignored build/."""
    def setUp(self):
        require_toolchain(native=True)
        base=Path('build').resolve();base.mkdir(exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='dfm-custom-',dir=base))
        print('CUSTOM_DFM_ARTIFACTS',self.root)

    @staticmethod
    def fixture(directory, *, raw_pad=None, angle=0, back=False):
        import pcbnew as k
        directory.mkdir(parents=True)
        path=directory/'board.kicad_pcb'
        b=k.BOARD();b.SetCopperLayerCount(2);k.SaveBoard(str(path),b,True)
        if raw_pad is None:
            raw_pad='''(pad "1" smd custom (at 0 0) (size 1 1) (layers "F.Cu")
 (net "V") (options (clearance outline) (anchor rect))
 (primitives (gr_poly (pts (xy 0 -0.5) (xy 3 -0.5) (xy 3 0.5) (xy 0 0.5)) (width 0) (fill yes))))'''
        footprint=f'''(footprint "OwnedCustom" (layer "F.Cu") (at 20 20 0)
 (property "Reference" "U1" (at 0 0) (layer "F.Fab")) {raw_pad})'''
        data=path.read_bytes();at=data.rstrip().rfind(b')');path.write_bytes(data[:at]+footprint.encode()+data[at:])
        from agent_ee.symbol_library import parse_sexpr
        try:
            parse_sexpr(path.read_bytes())
        except ValueError as exc:
            raise ValueError(f'generated custom DFM fixture malformed: {path}: {exc}') from exc
        b=k.LoadBoard(str(path))
        if b is None:
            raise ValueError(f'native load failed for generated custom DFM fixture: {path}')
        fp=next(iter(b.GetFootprints()))
        test_net=k.NETINFO_ITEM(b,'CUSTOM_DFM_TEST');b.Add(test_net)
        for pad in fp.Pads(): pad.SetNet(test_net)
        fp.SetOrientationDegrees(angle)
        if back: fp.Flip(fp.GetPosition(),k.FLIP_DIRECTION_TOP_BOTTOM)
        k.SaveBoard(str(path),b,True)
        return path

    @staticmethod
    def probe(path, label):
        import pcbnew as k
        from agent_ee import geometry_probe
        board,before=geometry_probe.load_readonly_board(path)
        facts=geometry_probe.dfm_facts(board,str(path));geometry_probe.verify_unchanged(path,before)
        write_json(path.parent/(label+'-facts.json'),facts)
        return facts

    @staticmethod
    def place_via(path, point):
        import pcbnew as k
        b=k.LoadBoard(str(path));pad=next(p for fp in b.GetFootprints() for p in fp.Pads())
        via=k.PCB_VIA(b);via.SetPosition(k.VECTOR2I(*point));via.SetWidth(600000);via.SetDrill(300000)
        via.SetLayerPair(k.F_Cu,k.B_Cu);via.SetNet(pad.GetNet());b.Add(via)
        assert k.SaveBoard(str(path),b,True)

    @staticmethod
    def evaluate_saved(path,label):
        facts=NativeCustomSmdTests.probe(path,label)
        profile=compile_profile(dict(schema='agent-ee.dfm-profile/1',name='custom-native',rules=[CustomSmdCoverageTests.rule]))
        result=evaluate(facts,profile);write_json(path.parent/(label+'-report.json'),result)
        return facts,result['rules'][0]

    def test_saved_union_anchor_extension_and_transformed_collision(self):
        from agent_ee.board_envelope import inside
        for angle,back in ((0,False),(90,False),(180,True)):
            path=self.fixture(self.root/f'pose-{angle}-{back}',angle=angle,back=back)
            before=self.probe(path,'before');pad=before['pads'][0]
            self.assertEqual(pad['custom_copper']['status'],'qualified',pad['custom_copper'])
            layer=pad['copper_layers'][0];contour=pad['polygons_nm'][layer][0]
            xs=[p[0] for p in contour];ys=[p[1] for p in contour]
            error=before['max_error_nm']
            nominal={(0,False):(19500000,19500000,23000000,20500000),
                     (90,False):(19500000,17000000,20500000,20500000),
                     (180,True):(17000000,19500000,20500000,20500000)}[(angle,back)]
            self.assertEqual([min(xs),min(ys),max(xs),max(ys)],
                             [nominal[0]-error,nominal[1]-error,nominal[2]+error,nominal[3]+error])
            self.assertEqual(pad['custom_copper']['pad_uuid'],pad['uuid'])
            self.assertEqual(pad['custom_copper']['footprint_uuid'],pad['footprint_uuid'])
            self.assertEqual(pad['orientation_deg'] % 360, angle % 360)
            self.assertEqual(pad['copper_layers'],['B.Cu' if back else 'F.Cu'])
            self.assertTrue(pad['custom_copper']['layers'][layer]['whole_shape_covered'])
            # A native-copper interior far outside the nominal 1x1 anchor.
            samples=[(min(xs)+(max(xs)-min(xs))*i//10,min(ys)+(max(ys)-min(ys))*j//10)
                     for i in range(1,10) for j in range(1,10)]
            centre=pad['position_nm']
            point=next(p for p in samples if inside(p,contour) and max(abs(p[a]-centre[a]) for a in (0,1))>1000000)
            self.place_via(path,point);_,result=self.evaluate_saved(path,'collision')
            self.assertEqual((result['execution'],result['status'],result['checked']),('ok','defect',1),result)

    def test_native_straight_edge_gap_099999_1_100001(self):
        for gap,status in ((99999,'defect'),(100000,'pass'),(100001,'pass'),('nominal','defect')):
            path=self.fixture(self.root/str(gap))
            before=self.probe(path,'before');contour=before['pads'][0]['polygons_nm']['F.Cu'][0]
            right=max(p[0] for p in contour)
            vertical=[(a,b) for a,b in zip(contour,contour[1:]+contour[:1]) if a[0]==b[0]==right and a[1]!=b[1]]
            a,b=max(vertical,key=lambda pair:abs(pair[1][1]-pair[0][1]))
            point=(23400000,20000000) if gap=='nominal' else (right+300000+gap,(a[1]+b[1])//2)
            write_json(path.parent/'control.json',dict(native_edge_nm=[a,b],via_radius_nm=300000,
                requested_outward_gap_nm=gap,via_position_nm=point,
                nominal_case_is_conservative_false_positive=gap=='nominal'))
            self.place_via(path,point);facts,result=self.evaluate_saved(path,'gap')
            self.assertEqual(facts['vias'][0]['width_nm'],600000)
            self.assertEqual((result['execution'],result['status']),('ok',status),result)

    def test_existing_standard_smd_native_polygons_still_measure(self):
        import pcbnew as k
        path=self.fixture(self.root/'standard')
        b=k.LoadBoard(str(path));pad=next(p for fp in b.GetFootprints() for p in fp.Pads())
        pad.SetShape(k.PAD_SHAPE_RECTANGLE);pad.SetSize(k.VECTOR2I(1000000,1000000))
        k.SaveBoard(str(path),b,True)
        self.place_via(path,(21000000,20000000));facts,result=self.evaluate_saved(path,'standard')
        self.assertEqual(facts['pads'][0]['shape'],'RECTANGLE')
        self.assertNotIn('custom_copper',facts['pads'][0])
        self.assertEqual((result['execution'],result['status']),('ok','pass'),result)

    def test_native_large_standard_board_and_bounded_custom_source_refusal(self):
        import pcbnew as k
        for custom in (False,True):
            path=self.fixture(self.root/('large-custom' if custom else 'large-standard'))
            if not custom:
                b=k.LoadBoard(str(path));p=next(p for f in b.GetFootprints() for p in f.Pads())
                p.SetShape(k.PAD_SHAPE_RECTANGLE);p.SetSize(k.VECTOR2I(1000000,1000000));k.SaveBoard(str(path),b,True)
            # Valid native source over the unchanged shared parser's 32 MiB limit.
            with path.open('ab') as stream: stream.write(b' '*(32*1024*1024+1))
            facts=self.probe(path,'large')
            self.assertEqual(len(facts['pads']),1)
            if custom:
                witness=facts['pads'][0]['custom_copper']
                self.assertEqual(witness['status'],'unsupported')
                self.assertIn('LibraryError',witness['reason'])
                self.assertIn('32 MiB',witness['reason'])
            else:
                self.assertEqual(facts['pads'][0]['shape'],'RECTANGLE')
                self.assertNotIn('custom_copper',facts['pads'][0])

    def test_native_hole_and_unsupported_source_are_not_qualified(self):
        # A slit polygon encodes an interior hole; copied Unfracture must expose it.
        pts='(xy -2 -2) (xy 2 -2) (xy 2 2) (xy -2 2) (xy -2 -2) (xy -1 -1) (xy -1 1) (xy 1 1) (xy 1 -1) (xy -1 -1) (xy -2 -2)'
        raw=f'''(pad "1" smd custom (at 0 0) (size 0.2 0.2) (layers "F.Cu")
 (net "V") (options (clearance outline) (anchor rect))
 (primitives (gr_poly (pts {pts}) (width 0) (fill yes))))'''
        path=self.fixture(self.root/'hole',raw_pad=raw)
        facts=self.probe(path,'hole');self.assertEqual(facts['pads'][0]['custom_copper']['status'],'unsupported',facts['pads'][0])
        self.place_via(path,(25000000,20000000));_,result=self.evaluate_saved(path,'hole-via')
        self.assertEqual(result['execution'],'unsupported',result)

"""JLCPCB CPL rotation correction (AGENTEE-72): pure rules and the real native pipeline."""
import copy
import csv
import hashlib
import io
import json
import shutil
import tempfile
import time
import unittest
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from agent_ee.capabilities import describe_check
from agent_ee.cpl_rotation import (CYCLES, aggregate, capability, classify_pin1, corrected_rotation,
                                   evaluate, load_policy, match_policy, pin1_offset, read_bom_bindings,
                                   read_native_cpl, validate_params, _midpoint)
from agent_ee.easyeda_footprint import SupplierError, symbol_pins
from agent_ee.fab_export import cpl_rows
from agent_ee.storage import digest, read_json, write_json
from land_pattern_fixtures import component, pad
from toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'cpl-rotation'
POLICY = (FIXTURE / 'jlc-cpl-policy.json').read_bytes()


def policy_bytes(**changes):
    doc = json.loads(POLICY)
    doc.update(changes)
    return json.dumps(doc).encode()


def rotate(pads, quarter_turns):
    """Rotate [(number, x, y)] counter-clockwise (+Y up) by quarter turns."""
    for _ in range(quarter_turns % 4):
        pads = [(n, -y, x) for n, x, y in pads]
    return pads


def mirror(pads):
    return [(n, -x, y) for n, x, y in pads]


# Owned SOIC-8-like pad centres in a +Y-up top view: pin 1 top of the left column.
SOIC_TOP_VIEW = ([(str(i + 1), Fraction(-2475000), Fraction(1905000 - 1270000 * i)) for i in range(4)]
                 + [(str(i + 5), Fraction(2475000), Fraction(-1905000 + 1270000 * i)) for i in range(4)])
MM = 1000000


class PolicyTests(unittest.TestCase):
    def test_fixture_policy_is_strict_versioned_and_exact(self):
        policy = load_policy(POLICY)
        self.assertEqual((policy['schema_version'], policy['policy_id']), (1, 'agent-ee-jlc-cpl-v1'))
        self.assertEqual([r['id'] for r in policy['footprint_rules']],
                         ['soic-8-3.9x4.9-p1.27', 'hro-type-c-31-m-12'])
        self.assertEqual(policy['footprint_rules'][0]['rotation_degrees'], Decimal(270))
        self.assertEqual(policy['footprint_rules'][0]['offset_x_mm'], Decimal(0))

    def test_unknown_keys_duplicates_and_nonfinite_values_refuse_the_policy(self):
        rule = {'id': 'r', 'footprint_prefix': 'SOIC-8', 'rotation_degrees': 90}
        override = {'id': 'o', 'designator': 'U1', 'rotation_degrees': 90}
        cases = {
            'unknown top key': policy_bytes(extra=1),
            'unknown rule key': policy_bytes(footprint_rules=[dict(rule, mirror=True)]),
            'unknown override key': policy_bytes(component_overrides=[dict(override, side='Top')]),
            'missing rule key': policy_bytes(footprint_rules=[{'id': 'r', 'rotation_degrees': 90}]),
            'duplicate rule id': policy_bytes(footprint_rules=[rule, dict(rule, footprint_prefix='SOT')]),
            'duplicate id across groups': policy_bytes(component_overrides=[dict(override, id='soic-8-3.9x4.9-p1.27')]),
            'duplicate designator': policy_bytes(component_overrides=[override, dict(override, id='o2')]),
            'NaN rotation': POLICY.replace(b'"rotation_degrees": 270', b'"rotation_degrees": NaN'),
            'Infinity offset': policy_bytes(footprint_rules=[rule]).replace(b'"rotation_degrees": 90',
                                                                            b'"rotation_degrees": 90, "offset_x_mm": Infinity'),
            'overflowing rotation': POLICY.replace(b'"rotation_degrees": 270', b'"rotation_degrees": 1e999'),
            'boolean rotation': policy_bytes(footprint_rules=[dict(rule, rotation_degrees=True)]),
            'sub-nanometre offset': policy_bytes(footprint_rules=[dict(rule, offset_x_mm=0.0000001)]),
            'empty id': policy_bytes(footprint_rules=[dict(rule, id='')]),
            'schema version 2': policy_bytes(schema_version=2),
            'duplicate JSON key': b'{"schema_version": 1, "schema_version": 1}',
            'not JSON': b'policy',
        }
        for name, data in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                load_policy(data)

    def test_precedence_override_then_first_prefix_rule_then_none(self):
        policy = load_policy(policy_bytes(
            footprint_rules=[{'id': 'soic', 'footprint_prefix': 'SOIC-8_', 'rotation_degrees': 270},
                             {'id': 'soic-late', 'footprint_prefix': 'SOIC', 'rotation_degrees': 90}],
            component_overrides=[{'id': 'u7', 'designator': 'U7', 'rotation_degrees': 90,
                                  'offset_x_mm': 0.15, 'offset_y_mm': -0.1}]))
        self.assertEqual(match_policy(policy, 'U7', 'Package_SO:SOIC-8_3.9x4.9mm')[1]['id'], 'u7')
        kind, rule = match_policy(policy, 'U1', 'Package_SO:SOIC-8_3.9x4.9mm')
        self.assertEqual((kind, rule['id']), ('footprint_rule', 'soic'))
        self.assertEqual(match_policy(policy, 'U2', 'lib:SOIC-14')[1]['id'], 'soic-late')
        self.assertEqual(match_policy(policy, 'U3', 'lib:soic-8_x'), (None, None))  # case-sensitive
        self.assertEqual(match_policy(policy, 'R1', None), (None, None))

    def test_issue518_analogue_u1_soic8_gets_plus_270_and_bottom_transform(self):
        # Owned native-format position rows mirroring the reported KiCad 10 export:
        # U1 SOIC-8 at 90 degrees, a HRO USB-C receptacle at 0, a 0402 at 450, a bottom SOIC.
        native = ('Ref,Val,Package,PosX,PosY,Rot,Side\n'
                  '"U1","NE555","SOIC-8_3.9x4.9mm_P1.27mm",47.000000,-35.000000,90.000000,top\n'
                  '"J1","USB","USB_C_Receptacle_HRO_TYPE-C-31-M-12",30.000000,-26.500000,0.000000,top\n'
                  '"R1","10k","R_0402_1005Metric",1.000000,-2.000000,450.000000,top\n'
                  '"U2","NE555","SOIC-8_3.9x4.9mm_P1.27mm",5.000000,-6.000000,90.000000,bottom\n')
        packages = {r[0]: r[2] for r in csv.reader(io.StringIO(native)) if r[0] != 'Ref'}
        policy = load_policy(POLICY)
        after = {}
        for ref, _, _, side, rot in cpl_rows(native):
            kind, rule = match_policy(policy, ref, 'Package_SO:' + packages[ref])
            after[ref] = (rule and rule['id'], str(corrected_rotation(Decimal(rot), side,
                                                                       rule['rotation_degrees'] if rule else 0)))
        self.assertEqual(after['U1'], ('soic-8-3.9x4.9-p1.27', '0.000000'))
        self.assertEqual(after['J1'], ('hro-type-c-31-m-12', '180.000000'))
        self.assertEqual(after['R1'], (None, '90.000000'))
        self.assertEqual(after['U2'], ('soic-8-3.9x4.9-p1.27', '0.000000'))  # 180 - (90 - 270)
        self.assertEqual(str(corrected_rotation(Decimal('-90'), 'Bottom', Decimal(0))), '270.000000')
        self.assertEqual(str(corrected_rotation(Decimal('359.9999999'), 'Top', Decimal(0))), '0.000000')

    def test_params_are_exact(self):
        good = {'export_check': 'fab', 'geometry_check': 'geometry', 'policy': 'design/p.json',
                'supplier_footprints': ['design/s.json'], 'source_tolerance_nm': '200'}
        self.assertEqual(validate_params(good), good)
        for change in ({'extra': 1}, {'policy': '../p.json'}, {'supplier_footprints': 'design/s.json'},
                       {'supplier_footprints': ['a', 'a']}, {'source_tolerance_nm': 200},
                       {'export_check': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_params({**good, **change})


class Pin1FrameTests(unittest.TestCase):
    def test_matching_frame_is_offset_zero_and_quarter_turns_are_their_angle(self):
        board, frame, _ = classify_pin1(SOIC_TOP_VIEW)
        self.assertEqual((board, frame), ('leftside_top', 'sided'))
        for turns in range(4):
            supplier = classify_pin1(rotate(SOIC_TOP_VIEW, -turns))[0]
            with self.subTest(turns=turns):
                self.assertEqual(pin1_offset(supplier, board), 90 * turns)
        # The supplier drawn with pins along the bottom, pin 1 left: KiCad needs +270.
        horizontal = classify_pin1(rotate(SOIC_TOP_VIEW, 1))[0]
        self.assertEqual(horizontal, 'bottomside_left')
        self.assertEqual(pin1_offset(horizontal, board), 270)

    def test_every_position_in_a_cycle_and_reflections_cross_cycles(self):
        for turns in range(4):
            for pads in (SOIC_TOP_VIEW, mirror(SOIC_TOP_VIEW)):
                position = classify_pin1(rotate(pads, turns))[0]
                self.assertTrue(any(position in cycle for cycle in CYCLES))
        for turns in range(4):
            mirrored = classify_pin1(rotate(mirror(SOIC_TOP_VIEW), turns))[0]
            with self.subTest(turns=turns):
                self.assertIsNone(pin1_offset(mirrored, 'leftside_top'))

    def test_linear_and_corner_frames_rotate_within_one_cycle(self):
        row = [('1', Fraction(-MM), Fraction(0)), ('2', Fraction(MM), Fraction(0))]
        corner = [('1', Fraction(-1100000), Fraction(-850000)), ('2', Fraction(1100000), Fraction(-850000)),
                  ('3', Fraction(1100000), Fraction(850000)), ('4', Fraction(-1100000), Fraction(850000))]
        for pads, frame in ((row, 'linear'), (corner, 'corner')):
            base = classify_pin1(pads)
            self.assertEqual(base[1], frame)
            for turns in range(4):
                rotated = classify_pin1(rotate(pads, turns))
                with self.subTest(frame=frame, turns=turns):
                    self.assertEqual(rotated[1], frame)
                    self.assertEqual(pin1_offset(base[0], rotated[0]), 90 * turns)
            # A mirror is indistinguishable here: it still lands in the same cycle.
            self.assertIsNotNone(pin1_offset(base[0], classify_pin1(mirror(pads))[0]))

    def test_unclassifiable_pin1_is_reported_not_guessed(self):
        interior = SOIC_TOP_VIEW + [('9', Fraction(0), Fraction(0))]
        interior = [('9' if n == '1' else '1' if n == '9' else n, x, y) for n, x, y in interior]
        centred = [('1', Fraction(-MM), Fraction(0)), ('2', Fraction(MM), Fraction(MM)),
                   ('3', Fraction(MM), Fraction(-MM))]
        for name, pads in (('interior', interior), ('centred', centred),
                           ('two pin 1', SOIC_TOP_VIEW + [('1', Fraction(0), Fraction(5 * MM))]),
                           ('single pad', [('1', Fraction(0), Fraction(0))]),
                           ('row middle', [('2', Fraction(-MM), Fraction(0)), ('1', Fraction(0), Fraction(0)),
                                           ('3', Fraction(MM), Fraction(0))])):
            with self.subTest(name=name):
                position, frame, reason = classify_pin1(pads)
                self.assertIsNone(position)
                self.assertTrue(reason)


class MidpointTests(unittest.TestCase):
    def test_union_centre_uses_box2i_integer_half_size(self):
        fp = {'pads': [{'bbox_nm': [0, 0, 3, 3]}, {'bbox_nm': [10, -5, 11, 2]}]}
        self.assertEqual(_midpoint(fp)[0], (5, -1))  # left 0 + 11 // 2, top -5 + 8 // 2

    def test_no_pads_missing_or_malformed_boxes_never_fall_back_to_the_anchor(self):
        for fp, mode in (({'pads': []}, 'geometry_missing'), ({'pads': [{'size_mm': [1, 1]}]}, 'geometry_missing'),
                         ({'pads': [{'bbox_nm': [0, 0, -1, 1]}]}, 'geometry_malformed'),
                         ({'pads': [{'bbox_nm': [0, 0, 1.5, 1]}]}, 'geometry_malformed')):
            with self.subTest(fp=fp):
                point, problem, _ = _midpoint(fp)
                self.assertEqual((point, problem), (None, mode))


# ------------------------------------------------------------ synthetic boards

SOIC_PADS = ''.join(f'(pad "{n}" smd rect (at {x} {y}) (size 1.95 0.6) (layers "F.Cu"))'
                    for n, x, y in [(str(i + 1), -2.475, -1.905 + 1.27 * i) for i in range(4)]
                    + [(str(i + 5), 2.475, 1.905 - 1.27 * i) for i in range(4)])
DIODE_PADS = ('(pad "1" smd rect (at -1.6 0) (size 0.9 1.2) (layers "F.Cu") (pinfunction "{k}"))'
              '(pad "2" smd rect (at 1.6 0) (size 0.9 1.2) (layers "F.Cu") (pinfunction "{a}"))')


def footprint(lib, ref, x, y, angle, pads, side='F.Cu'):
    return f'(footprint "{lib}" (layer "{side}") (at {x} {y} {angle}) (property "Reference" "{ref}") {pads})'


def board(*footprints):
    return ('(kicad_pcb (version 20260206) ' + ' '.join(footprints) + ')').encode()


def probe(ref, lib, x_nm, y_nm, boxes, layer='F.Cu', **flags):
    return dict({'ref': ref, 'layer': layer, 'dnp': False, 'exclude_from_pos_files': False, 'fpid': lib,
                 'position_nm': [x_nm, y_nm], 'pads': [{'bbox_nm': b} for b in boxes]}, **flags)


def soic_supplier(lcsc='C1', *, flip=False):
    """Owned EasyEDA SOIC drawn horizontally: pins 1-4 along the bottom, pin 1 left (or mirrored)."""
    sign = -1 if flip else 1
    shapes = ([pad('RECT', 4000 + sign * (-7.5 + 5 * i), 3010.6, 2.4, 7.7, str(i + 1), ident=f'a{i}')
               for i in range(4)]
              + [pad('RECT', 4000 + sign * (7.5 - 5 * i), 2989.4, 2.4, 7.7, str(i + 5), ident=f'b{i}')
                 for i in range(4)])
    return component(lcsc, shapes)


def diode_supplier(lcsc, pin1_name):
    data = json.loads(component(lcsc, [pad('RECT', 3993.7, 3000, 3.5, 4.7, '1', ident='a'),
                                       pad('RECT', 4006.3, 3000, 3.5, 4.7, '2', ident='b')]))
    data['dataStr']['shape'] = [
        f'P~show~0~1~-20~0~180~p1~0^^-20~0^^M -20 0 h 15~#880000^^0~-2~3~0~{pin1_name}~start~~~#0000FF'
        '^^0~-11~-1~0~1~end~~~#0000FF^^0~-8~0^^0~M -5 3',
        'P~show~0~2~20~0~0~p2~0^^20~0^^M 20 0 h -15~#880000^^0~2~3~0~X~end~~~#0000FF'
        '^^0~11~-1~0~2~start~~~#0000FF^^0~8~0^^0~M 5 -3']
    return json.dumps(data, sort_keys=True).encode()


def supplier(raw):
    return {'raw': raw, 'provenance': 'design/p.json', 'sha256': hashlib.sha256(raw).hexdigest()}


SOIC_BOX = [[44000000, 32000000, 46000000, 38000000], [48000000, 32000000, 50000000, 38000000]]


class EvaluateTests(unittest.TestCase):
    def run_case(self, rows, geometry, footprints, bindings, suppliers, policy=POLICY):
        return evaluate(rows, geometry, board(*footprints), bindings, load_policy(policy), suppliers, 200)

    def soic_case(self, angle='90', board_angle=90, supplier_raw=None, policy=POLICY, lib='lib:SOIC-8_3.9x4.9mm_P1.27mm'):
        rows = [['U1', '47.000000', '-35.000000', 'Top', angle]]
        geometry = [probe('U1', lib, 47000000, 35000000, SOIC_BOX)]
        footprints = [footprint(lib, 'U1', 47, 35, board_angle, SOIC_PADS)]
        return self.run_case(rows, geometry, footprints, {'U1': 'C1'},
                             {'C1': supplier(supplier_raw or soic_supplier())}, policy)

    def test_policy_and_pin1_agree_and_the_row_moves_to_the_pad_midpoint(self):
        records, applied, unmatched = self.soic_case()
        record = records[0]
        self.assertEqual((record['execution'], record['outcome']), ('ok', 'pass'), record['modes'])
        self.assertEqual(record['pin1']['board'], 'leftside_top')
        self.assertEqual(record['pin1']['supplier'], 'bottomside_left')
        self.assertEqual(record['pin1']['offset_degrees'], 270)
        self.assertEqual(record['cpl'], ['U1', '47.000000', '-35.000000', 'Top', '0.000000'])
        self.assertEqual([a['designator'] for a in applied], ['U1'])
        self.assertEqual(unmatched, [])

    def test_unmatched_footprint_whose_supplier_frame_needs_270_is_a_rotation_conflict(self):
        records, applied, unmatched = self.soic_case(lib='lib:SO8_custom')
        self.assertEqual(records[0]['outcome'], 'defect')
        self.assertEqual([m['mode'] for m in records[0]['modes']], ['rotation_conflict'])
        self.assertEqual(([a['designator'] for a in applied], [u['designator'] for u in unmatched]), ([], ['U1']))
        # A designator override supplies the missing turn.
        override = policy_bytes(component_overrides=[{'id': 'u1', 'designator': 'U1', 'rotation_degrees': 270}])
        records, applied, _ = self.soic_case(lib='lib:SO8_custom', policy=override)
        self.assertEqual((records[0]['outcome'], applied[0]['match_kind']), ('pass', 'component_override'))

    def test_matching_supplier_frame_gives_offset_zero_and_a_quarter_turn_gives_90(self):
        upright = component('C1', [pad('RECT', 4000 + (-9.75 if i < 4 else 9.75),
                                       3000 + (-7.5 + 5 * i if i < 4 else 7.5 - 5 * (i - 4)), 7.7, 2.4,
                                       str(i + 1), ident=f'p{i}') for i in range(8)])
        no_rule = policy_bytes(footprint_rules=[])
        records, _, _ = self.soic_case(supplier_raw=upright, policy=no_rule)
        self.assertEqual((records[0]['pin1']['offset_degrees'], records[0]['outcome']), (0, 'pass'))
        quarter = policy_bytes(footprint_rules=[{'id': 'q', 'footprint_prefix': 'SOIC', 'rotation_degrees': 90}])
        # Board leftside_top turned a quarter clockwise: pin 1 at the right end of the top row.
        turned = component('C1', [pad('RECT', 4000 + (7.5 - 5 * i if i < 4 else -7.5 + 5 * (i - 4)),
                                      3000 + (-9.75 if i < 4 else 9.75), 2.4, 7.7, str(i + 1), ident=f'p{i}')
                                  for i in range(8)])
        records, _, _ = self.soic_case(supplier_raw=turned, policy=quarter)
        self.assertEqual(records[0]['pin1']['supplier'], 'topside_right')
        self.assertEqual((records[0]['pin1']['offset_degrees'], records[0]['outcome']), (90, 'pass'))

    def test_mirrored_supplier_footprint_is_a_reflection_defect(self):
        records, _, _ = self.soic_case(supplier_raw=soic_supplier(flip=True))
        self.assertEqual(records[0]['outcome'], 'defect')
        self.assertEqual([m['mode'] for m in records[0]['modes']], ['reflection'])
        self.assertIsNone(records[0]['pin1']['offset_degrees'])

    def diode_case(self, board_pin1, supplier_pin1):
        pads = DIODE_PADS.format(k=board_pin1, a='A' if board_pin1 == 'K' else 'K')
        rows = [['D1', '70.000000', '-40.000000', 'Top', '0.000000']]
        geometry = [probe('D1', 'lib:D_SOD-123', 70000000, 40000000,
                          [[67950000, 39400000, 68850000, 40600000], [71150000, 39400000, 72050000, 40600000]])]
        return self.run_case(rows, geometry, [footprint('lib:D_SOD-123', 'D1', 70, 40, 0, pads)],
                             {'D1': 'C2'}, {'C2': supplier(diode_supplier('C2', supplier_pin1))})

    def test_diode_polarity_agreement_passes_and_anode_versus_cathode_is_a_defect(self):
        records, _, _ = self.diode_case('K', 'K')
        self.assertEqual((records[0]['outcome'], records[0]['polarity']['board']), ('pass', 'cathode'))
        records, _, _ = self.diode_case('A', 'C')
        self.assertEqual(records[0]['outcome'], 'defect')
        self.assertEqual([m['mode'] for m in records[0]['modes']], ['polarity_conflict'])
        self.assertEqual((records[0]['polarity']['board'], records[0]['polarity']['supplier']), ('anode', 'cathode'))
        records, _, _ = self.diode_case('K', 'IN')
        self.assertEqual((records[0]['execution'], records[0]['outcome']), ('unsupported', 'unknown'))
        self.assertEqual([m['mode'] for m in records[0]['modes']], ['polarity_unknown'])

    def test_duplicate_missing_geometry_and_missing_supplier_are_per_designator_failures(self):
        lib = 'lib:SOIC-8_3.9x4.9mm_P1.27mm'
        rows = [['U1', '47.000000', '-35.000000', 'Top', '90'], ['U2', '10.000000', '-10.000000', 'Top', '90'],
                ['U3', '20.000000', '-10.000000', 'Top', '90'], ['U4', '30.000000', '-10.000000', 'Top', '90'],
                ['U4', '31.000000', '-10.000000', 'Top', '90'], ['U5', '40.000000', '-10.000000', 'Top', '90']]
        boxes = lambda x, y: [[x - 3000000, y - 3000000, x - 1000000, y + 3000000],
                              [x + 1000000, y - 3000000, x + 3000000, y + 3000000]]
        geometry = [probe('U1', lib, 47000000, 35000000, boxes(47000000, 35000000)),
                    probe('U3', lib, 20000000, 10000000, boxes(20000000, 10000000)),
                    probe('U4', lib, 30000000, 10000000, boxes(30000000, 10000000)),
                    probe('U5', lib, 40000000, 10000000, boxes(40000000, 10000000)),
                    probe('U5', lib, 40000000, 10000000, boxes(40000000, 10000000)),
                    probe('U9', lib, 0, 0, boxes(0, 0), dnp=True)]
        footprints = [footprint(lib, ref, x, y, 90, SOIC_PADS)
                      for ref, x, y in (('U1', 47, 35), ('U2', 10, 10), ('U3', 20, 10), ('U4', 30, 10),
                                        ('U5', 40, 10))]
        records, applied, unmatched = self.run_case(rows, geometry, footprints,
                                                    {'U1': 'C1', 'U2': 'C1', 'U3': 'C3', 'U4': 'C1', 'U5': 'C1'},
                                                    {'C1': supplier(soic_supplier())})
        by_ref = {}
        for record in records:
            by_ref.setdefault(record['designator'], []).append(record)
        self.assertEqual(by_ref['U1'][0]['outcome'], 'pass')
        expected = {'U2': 'geometry_missing', 'U3': 'supplier_missing', 'U4': 'duplicate_designator',
                    'U5': 'duplicate_designator'}
        for ref, mode in expected.items():
            for record in by_ref[ref]:
                with self.subTest(ref=ref):
                    self.assertEqual((record['execution'], record['outcome']), ('unsupported', 'unknown'))
                    self.assertIn(mode, [m['mode'] for m in record['modes']])
        for ref in ('U2', 'U4', 'U5'):
            self.assertTrue(all(r['cpl'] is None and r['geometry'] is None for r in by_ref[ref]))
        self.assertIsNotNone(by_ref['U3'][0]['cpl'])  # policy rotation stays computable; pin 1 is unverified
        listed = sorted([a['designator'] for a in applied] + [u['designator'] for u in unmatched])
        self.assertEqual(listed, sorted(r[0] for r in rows))
        self.assertEqual(aggregate(records), ('unsupported', 'unknown', 'incomplete'))

    def test_anchor_or_side_disagreement_with_the_probe_is_malformed(self):
        lib = 'lib:SOIC-8_3.9x4.9mm_P1.27mm'
        footprints = [footprint(lib, 'U1', 47, 35, 90, SOIC_PADS)]
        for row, layer, mode in ((['U1', '47.000001', '-35.000000', 'Top', '90'], 'F.Cu', 'anchor_mismatch'),
                                 (['U1', '47.000000', '35.000000', 'Top', '90'], 'F.Cu', 'anchor_mismatch'),
                                 (['U1', '47.000000', '-35.000000', 'Top', '90'], 'B.Cu', 'side_mismatch')):
            geometry = [probe('U1', lib, 47000000, 35000000, SOIC_BOX, layer=layer)]
            records, _, _ = self.run_case([row], geometry, footprints, {'U1': 'C1'},
                                          {'C1': supplier(soic_supplier())})
            with self.subTest(mode=mode):
                self.assertEqual((records[0]['execution'], records[0]['cpl']), ('malformed', None))
                self.assertEqual(records[0]['modes'][0]['mode'], mode)

    def test_defect_dominates_aggregate_with_incomplete_coverage(self):
        records = [{'execution': 'ok', 'outcome': 'defect'}, {'execution': 'unsupported', 'outcome': 'unknown'}]
        self.assertEqual(aggregate(records), ('ok', 'defect', 'incomplete'))
        self.assertEqual(aggregate(records[:1]), ('ok', 'defect', 'complete'))
        self.assertEqual(aggregate([{'execution': 'ok', 'outcome': 'pass'}]), ('ok', 'pass', 'complete'))


class SupplierPinTests(unittest.TestCase):
    def test_symbol_pins_are_read_verbatim_and_malformed_records_refuse(self):
        pins = symbol_pins(diode_supplier('C2', 'K'), 'C2')['pins']
        self.assertEqual([(p['number'], p['displayed'], p['name']) for p in pins], [('1', '1', 'K'), ('2', '2', 'X')])
        data = json.loads(diode_supplier('C2', 'K'))
        data['dataStr']['shape'][0] = 'P~show~0~1~0~0^^short'
        with self.assertRaises(SupplierError):
            symbol_pins(json.dumps(data).encode(), 'C2')
        with self.assertRaises(SupplierError):
            symbol_pins(diode_supplier('C2', 'K'), 'C3')


class StagingReaderTests(unittest.TestCase):
    def test_export_cpl_and_bom_readers_are_strict(self):
        rows = read_native_cpl('Designator,Mid X,Mid Y,Layer,Rotation\nU1,1.000000,-2.000000,Top,90.000000\n')
        self.assertEqual(rows, [['U1', '1.000000', '-2.000000', 'Top', '90.000000']])
        for text in ('', 'Designator,Mid X,Mid Y,Layer,Rotation\n', 'Ref,X\nU1,1\n',
                     'Designator,Mid X,Mid Y,Layer,Rotation\nU1,1e3,0,Top,0\n',
                     'Designator,Mid X,Mid Y,Layer,Rotation\nU1,1,0,Inner,0\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                read_native_cpl(text)
        bom = ('Designator,Footprint,Quantity,Value,Manufacturer,Partnumber,LCSC Part #\n'
               '"R1,R2",lib:R,2,10k,M,P,C1\nU1,lib:U,1,x,M,P,\n')
        self.assertEqual(read_bom_bindings(bom), {'R1': 'C1', 'R2': 'C1', 'U1': ''})
        with self.assertRaises(ValueError):
            read_bom_bindings(bom + 'R1,lib:R,1,10k,M,P,C1\n')


# ------------------------------------------------------------- native pipeline

class NativeCplRotationTests(unittest.TestCase):
    """Real kicad-cli export, real pcbnew probe, owned fixture; one pipeline, several cpl checks."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(tools=('kicad-cli',))
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-cpl-')
        root = cls.root = Path(cls.tmp.name)
        source = cls.source = root / 'source'
        shutil.copytree(FIXTURE, source)
        scope = read_json(FIXTURE / 'control.scope.json')
        scope['inputs'][0]['path'] = str(source)
        base = scope['checks'][-1]
        missing = copy.deepcopy(base)
        missing.update(id='cpl_missing')
        missing['params']['supplier_footprints'] = [p for p in base['params']['supplier_footprints']
                                                    if 'C910020' not in p]
        scope['checks'] += [missing]
        scope['baseline']['requirements'][0]['checks'] += ['cpl_missing']
        cls.scope = scope
        write_json(root / 'scope.json', scope)
        cls.rundir = root / 'run'
        cls.rundir.mkdir()
        capture(root / 'scope.json', cls.rundir)
        cls.results = {r['id']: r for r in run_checks(cls.rundir, time.monotonic() + 900)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def report(self, cid='cpl'):
        return read_json(self.rundir / 'work' / cid / 'cpl-rotation.json')

    def cpl(self, rel='work/cpl/cpl/cpl.csv'):
        return {row[0]: row for row in csv.reader(io.StringIO((self.rundir / rel).read_text()))}

    def test_prerequisites_ran_natively(self):
        for cid in ('netlist', 'fab', 'geometry'):
            self.assertEqual(self.results[cid]['execution'], 'ok', self.results[cid])
        self.assertEqual(self.results['fab']['outcome'], 'pass')

    def test_midpoint_oracle_control_and_issue518_u1(self):
        result = self.results['cpl']
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result['details'])
        report = self.report()
        records = {r['designator']: r for r in report['designators']}
        oracle = {'JF0': ['20.000000', '21.270000'], 'JF1': ['31.270000', '20.000000'],
                  'JF2': ['40.000000', '18.730000'], 'JF3': ['48.730000', '20.000000'],
                  'JF4': ['60.721984', '20.721984']}
        for ref, midpoint in oracle.items():
            with self.subTest(ref=ref):
                self.assertEqual(records[ref]['geometry']['midpoint_mm'], midpoint)
                self.assertEqual(records[ref]['geometry']['algorithm'], 'native_board_pad_boxes_union_center')
        cpl = self.cpl()
        native = {row[0]: row for row in csv.reader(io.StringIO(
            (self.rundir / 'work/fab/fab/cpl.csv').read_text()))}
        self.assertEqual(cpl['Designator'], ['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation'])
        self.assertEqual(cpl['JF0'][1:3], ['20.000000', '-21.270000'])
        self.assertEqual(cpl['JF4'][1:3], ['60.721984', '-20.721984'])
        self.assertEqual(cpl['R1'][1:3], native['R1'][1:3])  # centre-anchored 0805 control stays put
        self.assertEqual(records['U1']['pin1']['offset_degrees'], 270)
        self.assertEqual((native['U1'][4], cpl['U1'][4]), ('90.000000', '0.000000'))
        self.assertEqual(records['U1']['policy']['rule_id'], 'soic-8-3.9x4.9-p1.27')
        self.assertEqual(records['D1']['polarity']['board'], 'cathode')

    def test_every_row_is_listed_once_status_preview_required_and_evidence_sealed(self):
        from agent_ee.baseline import freeze_evidence, verify_evidence
        report = self.report()
        applied = [e['designator'] for e in report['applied_corrections']]
        unmatched = [e['designator'] for e in report['unmatched_footprints']]
        cpl_refs = sorted(r for r in self.cpl() if r != 'Designator')
        self.assertEqual(sorted(applied + unmatched), cpl_refs)
        self.assertFalse(set(applied) & set(unmatched))
        self.assertEqual(applied, ['U1'])
        self.assertEqual((report['status'], report['physical_validation']), ('PREVIEW_REQUIRED', False))
        self.assertEqual(report['policy']['sha256'], digest(FIXTURE / 'jlc-cpl-policy.json'))
        self.assertEqual(report['cpl']['sha256'], digest(self.rundir / 'work/cpl/cpl/cpl.csv'))
        self.assertIn('work/cpl/cpl/cpl.csv', self.results['cpl']['evidence'])
        results = [read_json(self.rundir / 'checks' / f'{cid}.json') for cid in ('netlist', 'fab', 'geometry', 'cpl', 'cpl_missing')]
        freeze_evidence(self.rundir, results)
        verify_evidence(self.rundir, results, required=True)

    def test_nan_policy_is_refused_at_capture_and_at_run_with_no_cpl(self):
        from agent_ee.candidate import capture
        from agent_ee.checks import CheckError, Ctx
        nan = POLICY.replace(b'"rotation_degrees": 270', b'"rotation_degrees": NaN')
        (self.source / 'nan-policy.json').write_bytes(nan)
        scope = copy.deepcopy(self.scope)
        scope['inputs'][0]['include'].append('nan-policy.json')
        scope['checks'][3]['params']['policy'] = 'design/nan-policy.json'
        write_json(self.root / 'nan-scope.json', scope)
        (self.root / 'nan-run').mkdir()
        with self.assertRaisesRegex(ValueError, 'non-finite'):
            capture(self.root / 'nan-scope.json', self.root / 'nan-run')
        # Run-time guard: the same bytes presented as a captured input of the sealed run.
        manifest = read_json(self.rundir / 'candidate.json')
        (self.rundir / 'inputs/design/nan-policy.json').write_bytes(nan)
        manifest['files']['design/nan-policy.json'] = hashlib.sha256(nan).hexdigest()
        spec = copy.deepcopy(self.scope['checks'][3])
        spec.update(id='cpl_nan')
        spec['params']['policy'] = 'design/nan-policy.json'
        manifest['scope'] = dict(manifest['scope'], checks=manifest['scope']['checks'] + [spec])
        ctx = Ctx(self.rundir, manifest, spec, time.monotonic() + 60)
        from agent_ee.cpl_rotation import check_cpl_rotation
        with self.assertRaisesRegex(CheckError, 'policy refused') as caught:
            check_cpl_rotation(ctx)
        self.assertEqual(caught.exception.execution, 'malformed')
        self.assertEqual(sorted(p.name for p in ctx.work.iterdir()), [])

    def test_missing_supplier_footprint_is_unsupported_for_that_designator_only(self):
        result = self.results['cpl_missing']
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
        self.assertEqual(result['details']['modes'], {'D1': ['supplier_missing']})
        self.assertFalse((self.rundir / 'work/cpl_missing/cpl').exists())
        self.assertTrue((self.rundir / 'work/cpl_missing/cpl.incomplete/cpl.csv').is_file())

    def test_capability_names_immutable_policy_and_supplier_bytes(self):
        inputs = self.rundir / 'inputs'
        files = read_json(self.rundir / 'candidate.json')['files']
        descriptor = describe_check(self.scope['checks'][3], check_specs=self.scope['checks'], files=files,
                                    scope=self.scope, inputs=inputs)
        self.assertEqual(descriptor['status'], 'supported', descriptor)
        self.assertEqual([p['check_id'] for p in descriptor['prerequisites']], ['fab', 'geometry'])
        self.assertIn('design/jlc-cpl-policy.json', descriptor['immutable_inputs'])
        self.assertIn('design/supplier/C910020.raweasy.json', descriptor['immutable_inputs'])
        broken = copy.deepcopy(self.scope['checks'])
        broken[3]['params']['policy'] = 'design/requirements.md'
        bad = describe_check(broken[3], check_specs=broken, files=files, scope=self.scope, inputs=inputs)
        self.assertEqual(bad['status'], 'unsupported')
        forward = copy.deepcopy(self.scope['checks'])
        forward.insert(0, forward.pop(3))
        moved = describe_check(forward[0], check_specs=forward, files=files, scope=self.scope, inputs=inputs)
        self.assertEqual(moved['status'], 'unsupported')
        self.assertEqual(capability(self.scope['checks'][3]['params'], files, None)['immutable_inputs'],
                         sorted(['design/jlc-cpl-policy.json'] + self.scope['checks'][3]['params']['supplier_footprints']))


if __name__ == '__main__':
    unittest.main()

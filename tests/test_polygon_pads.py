"""Original records protect polygon metadata at parse, compile and package boundaries."""
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee.easyeda_footprint import SupplierError, parse_package
from agent_ee.design_compile import cpl_policy
from agent_ee.cpl_rotation import evaluate, load_policy
from agent_ee.symbol_library import children, parse_sexpr
from tests.land_pattern_fixtures import component, pad, kicad_footprint
from tests.test_pad_correspondence import _sexpr, policy_inputs
from tests.test_cpl_rotation import supplier, board, footprint, probe


def soic(pad1_center=(3990, 2992.5)):
    rows = [(str(i+1), -10, -7.5+5*i) for i in range(4)] + [
        (str(i+5), 10, 7.5-5*i) for i in range(4)]
    records, lands = [], []
    for number, x, y in rows:
        if number == '1':
            record = pad('POLYGON', *pad1_center, 7.5, 2.5, number,
                         points='3986.25 2991.25 3993.75 2991.25 3993.75 2993.75 3986.25 2993.75')
        else:
            record = pad('RECT', 4000+x, 3000+y, 7.5, 2.5, number)
        records.append(record)
        lands.append(f'(pad "{number}" smd rect (at {x*0.254} {y*0.254}) '
                     '(size 1.905 0.635) (layers "F.Cu" "F.Mask"))')
    return component('C900000099', records), kicad_footprint('OwnedSO8', ' '.join(lands))


class PolygonMetadataTests(unittest.TestCase):
    def test_center_and_size_must_agree_with_the_outline_box(self):
        for center, size in (((3, 1), (2, 2)), ((1, 3), (2, 2)),
                             ((1, 1), (3, 2)), ((1, 1), (2, 3))):
            raw = component('C9', [pad('POLYGON', *center, *size, 'X', points='0 0 2 0 2 2 0 2')])
            with self.subTest(center=center, size=size), self.assertRaises(SupplierError) as caught:
                parse_package(raw, 'C9', 200)
            self.assertEqual((caught.exception.mode, caught.exception.execution), ('source_inconsistent', 'malformed'))

    def test_consistency_tolerance_is_inclusive_and_preserves_outline_and_metadata(self):
        for field in ('center', 'size'):
            for error, accepted in (('0.0005', True), ('0.0005001', False)):
                from fractions import Fraction
                from agent_ee.easyeda_footprint import text
                center = text(1 + Fraction(error)) if field == 'center' else '1'
                width = text(2 + Fraction(error)) if field == 'size' else '2'
                raw = component('C9', [pad('POLYGON', center, 1, width, 2, 'X',
                                         points='0 0 2 0 2 2 0 2')], origin=(0, 0))
                with self.subTest(field=field, error=error):
                    if accepted:
                        p = parse_package(raw, 'C9', 127)['pattern']['pads'][0]['primitive']
                        self.assertEqual(p['points'], [['0', '0'], ['508000', '0'], ['508000', '508000'], ['0', '508000']])
                        self.assertEqual(p['center'][0], text(Fraction(center)*254000))
                        self.assertEqual(p['size'][0], text(Fraction(width)*254000))
                    else:
                        with self.assertRaises(SupplierError) as caught:
                            parse_package(raw, 'C9', 127)
                        self.assertEqual(caught.exception.mode, 'source_inconsistent')

    def test_quarter_turn_metadata_checks_swapped_size_without_rotating_points_twice(self):
        points = '0 0 4 0 4 2 0 2'
        for angle in ('90', '270', '450'):
            with self.subTest(angle=angle):
                p = parse_package(component('C9', [pad('POLYGON', 2, 1, 2, 4, 'X', rotation=angle,
                    points=points)], origin=(0, 0)), 'C9', 200)['pattern']['pads'][0]['primitive']
                self.assertEqual(p['points'], [['0', '0'], ['1016000', '0'], ['1016000', '508000'], ['0', '508000']])
                self.assertEqual(p['angle'], '0')

    def test_through_polygon_drill_cannot_move_to_an_inconsistent_center(self):
        for center, accepted in (((0, 0), True), ((10, 10), False)):
            raw = component('C9', [pad('POLYGON', *center, 4, 4, 'X', layer='11', hole_r='0.5',
                points='-2 -2 2 -2 2 2 -2 2')], origin=(0, 0))
            with self.subTest(center=center):
                if accepted:
                    parsed = parse_package(raw, 'C9', 200)
                    self.assertEqual(parsed['pattern']['holes'][0]['center'], ['0', '0'])
                else:
                    with self.assertRaises(SupplierError) as caught:
                        parse_package(raw, 'C9', 200)
                    self.assertEqual(caught.exception.mode, 'source_inconsistent')

    def test_soic_pin_one_center_spoof_refuses_before_compile_publishes_a_policy(self):
        for center, accepted in (((3990, 2992.5), True), ((4010, 3007.5), False)):
            with self.subTest(center=center), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                raw, fp = soic(center)
                design = policy_inputs(root, raw, fp, ref='U1', lcsc='C900000099', footprint_name='OwnedSO8')
                if accepted:
                    cpl_policy(design, root)
                    policy = json.loads((root / 'cpl/jlc-cpl-policy.json').read_bytes())
                    self.assertEqual(policy['footprint_rules'][0]['rotation_degrees'], 0)
                else:
                    with self.assertRaises(SupplierError) as caught:
                        cpl_policy(design, root)
                    self.assertEqual(caught.exception.mode, 'source_inconsistent')
                    self.assertFalse((root / 'cpl/jlc-cpl-policy.json').exists())

    def test_soic_spoof_cannot_pass_package_with_the_wrong_180_degree_policy(self):
        raw, fp = soic((4010, 3007.5))
        pads = ' '.join(_sexpr(p) for p in children(parse_sexpr(fp), 'pad'))
        placed = board(footprint('Owned:OwnedSO8', 'U1', 70, 40, 0, pads))
        geometry = [probe('U1', 'Owned:OwnedSO8', 70000000, 40000000,
                          [[66000000, 37000000, 74000000, 43000000]])]
        policy = load_policy(json.dumps(dict(schema_version=1, policy_id='owned', provenance='synthetic',
            footprint_rules=[dict(id='r', footprint_prefix='OwnedSO8', rotation_degrees=180)],
            component_overrides=[])).encode())
        records, _, _ = evaluate([['U1', '70', '-40', 'Top', '0']], geometry, placed,
            {'U1': 'C900000099'}, policy, {'C900000099': supplier(raw)}, 200)
        self.assertEqual(records[0]['outcome'], 'unknown')
        self.assertEqual(records[0]['modes'][0]['mode'], 'pin1_unclassified')
        self.assertIn('source_inconsistent', records[0]['modes'][0]['message'])

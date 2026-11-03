"""Original connector records; no supplier or installed-library data."""
import copy
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee.easyeda_footprint import SupplierError, parse_package, text
from agent_ee.land_geometry import area_bounds
from agent_ee.land_pattern import PatternError, extract_footprint, register
from agent_ee.pad_correspondence import derive, verify, validate_declaration
from agent_ee.symbol_library import parse_sexpr
from tests.land_pattern_fixtures import component, kicad_footprint, pad, provenance


def connector():
    """Two joined terminals, two distinct signals and four plated case lands."""
    records = [pad('POLYGON', -4, -4, 2, 4, 'K1K2',
                   points='-5 -6 -3 -6 -3 -6 -3 -2 -5 -2 -5 -6'),
               pad('RECT', 0, -4, 1, 4, 'S0'), pad('RECT', 3, -4, 1, 4, 'S1')]
    pads = []
    def native(number, x, y, w, h, drill=None):
        mm = lambda n: text(Fraction(str(n)) * Fraction('0.254'))
        if drill:
            head = f'thru_hole circle (drill {mm(drill)})'
            layers = '"*.Cu" "*.Mask"'
        else:
            head, layers = 'smd rect', '"F.Cu" "F.Mask"'
        pads.append(f'(pad "{number}" {head} (at {mm(x)} {mm(y)}) '
                    f'(size {mm(w)} {mm(h)}) (layers {layers}))')
    for number, x, w in [('K1', '-4.5', 1), ('K2', '-3.5', 1), ('S0', 0, 1), ('S1', 3, 1)]:
        native(number, x, -4, w, 4)
    for number, (x, y) in enumerate([(-8, 0), (8, 0), (-8, 6), (8, 6)], 71):
        records.append(pad('ELLIPSE', x, y, 3, 3, str(number), layer='11', hole_r='0.5'))
        native('CASE', x, y, 3, 3, drill=1)
    raw = component('C900000017', records, origin=(0, 0))
    footprint = kicad_footprint('OwnedConnector', ' '.join(pads))
    native_pattern = extract_footprint(parse_sexpr(footprint), side='F.Cu', frame_angle=0, label='owned')
    return raw, footprint, native_pattern, parse_package(raw, 'C900000017', 200)


NETS = dict(K1='RETURN', K2='RETURN', S0='DATA', S1='SELECT', CASE='CASE_NET')


class PolygonPadTests(unittest.TestCase):
    def test_concavity_exact_decimals_and_repeated_endpoints_survive_conversion(self):
        points = '10.25 20 12.25 20 12.25 20 12.25 21 11.25 21 11.25 23 10.25 23 10.25 20'
        raw = component('C9', [pad('POLYGON', 11, 21, 2, 3, 'X', points=points)], origin=(10, 20))
        result = parse_package(raw, 'C9', 200)
        p = result['pattern']['pads'][0]['primitive']
        self.assertEqual(p['points'], [['63500', '0'], ['571500', '0'], ['571500', '254000'],
                                      ['317500', '254000'], ['317500', '762000'], ['63500', '762000']])
        bounds = area_bounds(result['pattern'])['copper']['F.Cu']
        expected = 4 * 254000**2
        self.assertEqual(Fraction(**bounds['lower']), expected)
        self.assertEqual(bounds['lower'], bounds['upper'])
        self.assertEqual(result['pad_locators'][0]['raw'][10], points)

    def test_polygon_registration_transforms_every_vertex_once(self):
        _, _, native, supplier = connector()
        original = copy.deepcopy(supplier)
        p = supplier['pattern']['pads'][0]['primitive']
        for angle in (0, 90, 180, 270):
            _, registered = register(native['pattern'], supplier['pattern'],
                dict(translation='declared_origin', rotation=str(angle), mirror=False))
            out = registered['pads'][0]['primitive']
            x, y = map(Fraction, p['points'][0])
            expected = {0: (x, y), 90: (y, -x), 180: (-x, -y), 270: (-y, x)}[angle]
            self.assertEqual(out['points'][0], list(map(text, expected)))
            self.assertEqual(out['angle'], '0')
            self.assertEqual(area_bounds(registered)['copper'], area_bounds(supplier['pattern'])['copper'])
        self.assertEqual(supplier, original)

    def test_polygon_canvas_points_are_not_rotated_again_by_pad_metadata(self):
        raw = component('C9', [pad('POLYGON', 0, 0, 2, 2, 'X', rotation='37',
                                  points='0 0 2 1 0 2')], origin=(0, 0))
        p = parse_package(raw, 'C9', 200)['pattern']['pads'][0]['primitive']
        self.assertEqual(p['points'], [['0', '0'], ['508000', '254000'], ['0', '508000']])
        self.assertEqual(p['angle'], '0')

    def test_malformed_polygons_are_never_replaced_by_their_boxes(self):
        for points in ('0 0 1 1', '0 0 1 0 1', '0 0 1 1 2 2',
                       '0 0 3 0 0 2 2 2 0 0', '0 0 1 0 0 0 0 1'):
            with self.subTest(points=points), self.assertRaises(SupplierError):
                parse_package(component('C9', [pad('POLYGON', 0, 0, 2, 2, 'X', points=points)]), 'C9', 200)


class CorrespondenceTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.footprint, self.native, self.supplier = connector()

    def derive(self, **kw):
        return derive(self.native['pattern'], self.supplier['pattern'], nets=kw.pop('nets', NETS), **kw)

    def test_composite_and_renumbered_plated_lands_have_explicit_identity_proofs(self):
        result = self.derive()
        self.assertEqual(result['rotation_degrees'], 0)
        self.assertEqual(result['pad_map']['K1K2'], ['K1', 'K2'])
        self.assertEqual(result['identity_proof']['K1K2'], 'composite_number_and_copper')
        for number in ('71', '72', '73', '74'):
            self.assertEqual(result['pad_map'][number], ['CASE'])
            self.assertEqual(result['identity_proof'][number], 'repeated_plated_land_and_drill_geometry')
        self.assertEqual(len(result['pairs']), 8)

    def test_declared_map_is_checked_and_arbitrary_electrical_renumbering_needs_it(self):
        self.supplier['pattern']['pads'][0]['number'] = 'JOIN'
        with self.assertRaisesRegex(PatternError, 'unproven'):
            self.derive()
        declaration = dict(rotation_degrees=0, pad_map={
            'JOIN': ['K1', 'K2'], 'S0': ['S0'], 'S1': ['S1'],
            **{str(n): ['CASE'] for n in range(71, 75)}})
        self.assertEqual(self.derive(declaration=declaration)['identity_proof']['JOIN'], 'declared_map_and_copper')
        for changed in ({**declaration['pad_map'], 'JOIN': ['S0']},
                        {k: v for k, v in declaration['pad_map'].items() if k != 'S0'},
                        {**declaration['pad_map'], 'S0': ['S1']}):
            with self.subTest(changed=changed), self.assertRaises(PatternError):
                self.derive(declaration=dict(rotation_degrees=0, pad_map=changed))

    def test_physical_shorts_of_different_or_unconnected_nets_refuse(self):
        for nets in ({**NETS, 'K2': 'OTHER'}, {k: v for k, v in NETS.items() if k != 'K2'}, {}):
            with self.subTest(nets=nets), self.assertRaisesRegex(PatternError, 'merges distinct or unconnected'):
                self.derive(nets=nets)

    def test_signal_swap_missing_land_mirror_and_wrong_rotation_refuse(self):
        original = copy.deepcopy(self.supplier['pattern'])
        self.supplier['pattern']['pads'][1]['number'], self.supplier['pattern']['pads'][2]['number'] = 'S1', 'S0'
        with self.assertRaises(PatternError):
            self.derive()
        self.supplier['pattern'] = copy.deepcopy(original)
        self.supplier['pattern']['pads'].pop(2)
        with self.assertRaises(PatternError):
            self.derive()
        self.supplier['pattern'] = copy.deepcopy(original)
        for p in self.supplier['pattern']['pads']:
            p['primitive']['center'][0] = text(-Fraction(p['primitive']['center'][0]))
            if 'points' in p['primitive']:
                p['primitive']['points'] = [[text(-Fraction(x)), y] for x, y in p['primitive']['points']]
        with self.assertRaises(PatternError):
            self.derive()
        self.supplier['pattern'] = original
        with self.assertRaises(PatternError):
            verify(self.native['pattern'], original, 180, nets=NETS)

    def test_unique_rotation_is_derived_without_pad_one(self):
        for angle in (90, 180, 270):
            _, turned = register(self.native['pattern'], self.supplier['pattern'],
                dict(translation='declared_origin', rotation=str(angle), mirror=False))
            result = derive(self.native['pattern'], turned, nets=NETS)
            self.assertEqual(result['rotation_degrees'], (-angle) % 360)

    def test_geometry_cannot_rename_a_single_electrical_through_land(self):
        for p in self.native['pattern']['pads']:
            if p['number'] == 'CASE':
                p['number'] = 'UNIQUE_' + p['primitive']['center'][0] + '_' + p['primitive']['center'][1]
        with self.assertRaises(PatternError):
            self.derive()

    def test_mismatched_annuli_do_not_move_the_electrical_translation_datum(self):
        # Different mounting-land sizes must not displace an otherwise matching row.
        for p in self.supplier['pattern']['pads']:
            if p['number'] in ('73', '74'):
                p['primitive']['size'] = ['1016000', '1016000']
        result = self.derive()
        self.assertEqual(result['translation']['method'], 'unique_numbered_electrical_centers_median')
        self.assertEqual(result['rotation_degrees'], 0)

    def test_two_identity_preserving_rotations_refuse_without_a_declaration(self):
        raw = component('C9', [pad('RECT', x, y, 1, 1, name)
            for name, x, y in [('X', -3, 0), ('X', 3, 0), ('Y', 0, -3), ('Y', 0, 3)]], origin=(0, 0))
        pattern = parse_package(raw, 'C9', 200)['pattern']
        with self.assertRaisesRegex(PatternError, 'expected one electrical registration, found 2'):
            derive(pattern, pattern)

    def test_declaration_shape_is_strict(self):
        for value in ({}, dict(rotation_degrees=True, pad_map={'a': ['b']}),
                      dict(rotation_degrees=45, pad_map={'a': ['b']}),
                      dict(rotation_degrees=0, pad_map={'a': ['b', 'b']})):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_declaration(value)


class CompileAndPackageTests(unittest.TestCase):
    def test_compile_and_package_use_the_same_registration_and_net_short_gate(self):
        from agent_ee.design_compile import cpl_policy
        from agent_ee.cpl_rotation import evaluate, load_policy
        from agent_ee.storage import digest, write_json
        from tests.test_cpl_rotation import supplier, probe, footprint, board
        raw, fp, native, package = connector()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for path in ('supplier', 'named', 'Owned.pretty'):
                (root / path).mkdir()
            (root / 'Owned.pretty/OwnedConnector.kicad_mod').write_bytes(fp)
            write_json(root / 'cad.json', dict(schema='agent-ee.footprint-libraries/1', complete=True,
                libraries=[dict(name='Owned', members=[dict(path='Owned.pretty/OwnedConnector.kicad_mod',
                    sha256=digest(root / 'Owned.pretty/OwnedConnector.kicad_mod'))])]))
            (root / 'supplier/C900000017.raweasy.json').write_bytes(raw)
            (root / 'supplier/C900000017.provenance.json').write_bytes(
                provenance('C900000017', 'supplier/C900000017.raweasy.json', raw))
            write_json(root / 'named/J7.json', dict(package='OwnedConnector'))
            design = dict(parts=dict(J7=dict(footprint='Owned:OwnedConnector', lcsc='C900000017',
                lib_id='Owned:Connector')), nets={})
            for pin, net in NETS.items():
                design['nets'].setdefault(net, []).append('J7.' + pin)
            cpl_policy(design, root)
            policy = load_policy((root / 'cpl/jlc-cpl-policy.json').read_bytes())
            report = json.loads((root / 'design-compile-report.json').read_bytes())
            self.assertEqual(report['land_patterns'][0]['pad_correspondence']['pad_map']['K1K2'], ['K1', 'K2'])
            # Reuse the original synthetic footprint pads in a placed board node.
            from agent_ee.symbol_library import children
            pads = ' '.join(_sexpr(p) for p in children(parse_sexpr(fp), 'pad'))
            placed = board(footprint('Owned:OwnedConnector', 'J7', 70, 40, 0, pads))
            geometry = [probe('J7', 'Owned:OwnedConnector', 70000000, 40000000,
                              [[67000000, 38000000, 73000000, 43000000]])]
            rows = [['J7', '70.000000', '-40.000000', 'Top', '0']]
            for nets, expected in ((NETS, 'pass'), ({**NETS, 'K2': 'OTHER'}, 'unknown')):
                records, _, _ = evaluate(rows, geometry, placed, {'J7': 'C900000017'}, policy,
                    {'C900000017': supplier(raw)}, 200, pad_nets={'J7': nets})
                self.assertEqual(records[0]['outcome'], expected, records[0]['modes'])
            policy['footprint_rules'][0]['rotation_degrees'] = 90
            records, _, _ = evaluate(rows, geometry, placed, {'J7': 'C900000017'}, policy,
                {'C900000017': supplier(raw)}, 200, pad_nets={'J7': NETS})
            self.assertEqual(records[0]['modes'][0]['mode'], 'rotation_conflict')

            # The compiler must forward the circuit nets to the same short gate.
            import shutil
            shutil.copytree(root / 'cpl/supplier', root / 'supplier')
            shutil.rmtree(root / 'cpl')
            design['nets']['RETURN'].remove('J7.K2')
            design['nets']['OTHER'] = ['J7.K2']
            from agent_ee.design import DesignError
            with self.assertRaisesRegex(DesignError, r'parts.J7.lcsc: C900000017: deterministic.*merges distinct'):
                cpl_policy(design, root)

    def test_statement_registration_is_validated_at_both_schema_boundaries(self):
        from agent_ee.design import DesignError, validate
        from tests.test_design_compile import design_fixture
        with tempfile.TemporaryDirectory() as tmp:
            design = design_fixture(Path(tmp))
            design['parts']['R1']['cpl_registration'] = dict(rotation_degrees=0,
                                                          pad_map={'1': ['1'], '2': ['2']})
            validate(design)
            design['parts']['R1']['cpl_registration']['rotation_degrees'] = 45
            with self.assertRaisesRegex(DesignError, 'parts.R1.cpl_registration'):
                validate(design)


def _sexpr(node):
    return '(' + ' '.join(_sexpr(v) if isinstance(v, list) else json.dumps(v) for v in node) + ')'

"""Supplier (EasyEDA) versus KiCad land-pattern check over owned captured bytes."""
import copy
from fractions import Fraction
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_ee.easyeda_footprint import SupplierError, parse_package
from agent_ee.footprint_library import canonical
from agent_ee.land_pattern import (PatternError, board_footprint, board_footprints, capability, evaluate,
                                   extract_footprint, main, replay, validate_criteria)
from agent_ee.symbol_library import parse_sexpr
from land_pattern_fixtures import (MODEL, SILK, SOT_KICAD, chip, chip_kicad, chip_roundrect_kicad, component,
                                   kicad_footprint, manifest, pad, provenance, sot_supplier)


def ratio(record):
    return Fraction(record['numerator'], record['denominator'])


def supplier(shapes, lcsc='C900000001', **kw):
    return parse_package(component(lcsc, shapes, **kw), lcsc, '200')


class SupplierParserTests(unittest.TestCase):
    def test_exact_units_origin_inventory_and_retained_locators(self):
        result = supplier(chip(2, '2.4', '2.8') + [MODEL])
        pads = result['pattern']['pads']
        self.assertEqual([p['primitive']['center'] for p in pads], [['-508000', '0'], ['508000', '0']])
        self.assertEqual(pads[0]['primitive']['size'], ['609600', '711200'])
        self.assertEqual(pads[0]['layers'], ['F.Cu'])
        self.assertEqual(result['inventory'], dict(records=4, pads=2, holes=0,
                         ignored_noncopper={'SVGNODE@3DModel': 1, 'TRACK@TopSilk': 1}))
        self.assertEqual(result['pad_locators'][1]['raw'][8], '2')
        self.assertEqual(result['identity']['package'], 'OWNED-PKG')

    def test_duplicate_numbers_and_positions_are_retained(self):
        shapes = [pad('RECT', 4000, 3000, 2, 2, '3', ident='a'), pad('RECT', 4000, 3000, 2, 2, '3', ident='b')]
        result = supplier(shapes)
        self.assertEqual([p['number'] for p in result['pattern']['pads']], ['3', '3'])
        self.assertEqual([p['id'] for p in result['pad_locators']], ['a', 'b'])

    def test_unknown_and_unqualified_records_are_unsupported_never_dropped(self):
        cases = {
            'PT~M 245 303 L 235 310 L 245 317 Z ~#880000~1~0~none~gge49~0~': 'unsupported_shape',
            pad('POLYGON', 4000, 3000, 2, 2, '1', points='3999 2999 4001 2999 4000 3001'): 'unsupported_shape',
            pad('ELLIPSE', 4000, 3000, 2, 3, '1'): 'unsupported_shape',
            'VIA~4000~3000~2.4~~0.6~gge1~0': 'unsupported_shape',
            'TRACK~1~1~~3990 3000 4010 3000~gge2~0': 'unsupported_shape',
            'SOLIDREGION~11~~M 3990 3000 L 3992 3000 L 3992 3002 Z~solid~gge3~~~~0': 'unsupported_shape',
            'CIRCLE~4000~3000~1~0.2~22~gge4~0~~': 'unsupported_shape',
            'TRACK~1~9~~3990 3000 4010 3000~gge5~0': 'unsupported_layer',
            'SVGNODE~' + json.dumps(dict(layerid='3', attrs={})): 'unsupported_shape',
            pad('RECT', 4000, 3000, 2, 2, '1', rotation='45'): 'unsupported_rotation',
            pad('RECT', 4000, 3000, 2, 2, '1', layer='21'): 'unsupported_layer',
            pad('RECT', 4000, 3000, 2, 2, ''): 'unsupported_shape',
        }
        for record, mode in cases.items():
            with self.subTest(record=record[:40]), self.assertRaises(SupplierError) as caught:
                supplier(chip(2, 2, 2) + [record])
            self.assertEqual((caught.exception.mode, caught.exception.execution), (mode, 'unsupported'))

    def test_malformed_numbers_are_refused_not_defaulted(self):
        for field, value in ((11, ''), (11, 'NaN'), (2, '1e3'), (4, '0'), (5, '-1'), (9, 'x')):
            fields = pad('RECT', 4000, 3000, 2, 2, '1').split('~')
            fields[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(SupplierError) as caught:
                supplier(['~'.join(fields)])
            self.assertEqual(caught.exception.execution, 'malformed')
        with self.assertRaises(SupplierError):
            supplier(['~'.join(pad('RECT', 4000, 3000, 2, 2, '1').split('~')[:19])])
        with self.assertRaisesRegex(SupplierError, 'duplicate JSON key'):
            parse_package(b'{"a": 1, "a": 2}', 'C1', '0')
        with self.assertRaisesRegex(SupplierError, 'nonfinite'):
            parse_package(b'{"a": NaN}', 'C1', '0')

    def test_identity_must_match_binding_and_single_package(self):
        with self.assertRaises(SupplierError) as caught:
            parse_package(component('C900000002', chip(2, 2, 2)), 'C900000001', '0')
        self.assertEqual(caught.exception.mode, 'supplier_identity')
        bad = json.loads(component('C900000001', chip(2, 2, 2)))
        bad['dataStr']['head']['puuid'] = 'other'
        with self.assertRaisesRegex(SupplierError, 'puuid'):
            parse_package(json.dumps(bad).encode(), 'C900000001', '0')
        bad = json.loads(component('C900000001', chip(2, 2, 2)))
        bad['szlcsc']['number'] = 'C1'
        with self.assertRaisesRegex(SupplierError, 'szlcsc'):
            parse_package(json.dumps(bad).encode(), 'C900000001', '0')
        envelope = json.dumps(dict(success=True, code=0, result=json.loads(component('C9', chip(2, 2, 2)))))
        with self.assertRaises(SupplierError):
            parse_package(envelope.encode(), 'C9', '0')
        bad = json.loads(component('C900000001', chip(2, 2, 2)))
        bad['packageDetail']['docType'] = 2
        with self.assertRaisesRegex(SupplierError, 'docType 4'):
            parse_package(json.dumps(bad).encode(), 'C900000001', '0')

    def test_redundant_outline_consistency_uses_declared_bound(self):
        good = pad('RECT', 4000, 3000, 2, 2, '1', points='3999.0001 3001 3999 2999 4001 2999 4001 3001')
        self.assertEqual(len(supplier([good])['pattern']['pads']), 1)  # 25.4 nm <= 200 nm
        bad = pad('RECT', 4000, 3000, 2, 2, '1', points='3999.01 3001 3999 2999 4001 2999 4001 3001')
        with self.assertRaises(SupplierError) as caught:
            supplier([bad])
        self.assertEqual(caught.exception.mode, 'source_inconsistent')
        oval = pad('OVAL', 4000, 3000, 4, 2, '1', points='3999 3000 4001 3000')
        self.assertEqual(supplier([oval])['pattern']['pads'][0]['primitive']['shape'], 'oval')
        with self.assertRaises(SupplierError):
            supplier([pad('OVAL', 4000, 3000, 4, 2, '1', points='4000 2999 4000 3001')])

    def test_plated_round_and_slotted_holes_and_npth(self):
        round_pad = pad('ELLIPSE', 4010, 3000, 6, 6, '1', layer='11', hole_r='1.5')
        slot = pad('OVAL', 3990, 3000, 4, 8, '2', layer='11', hole_r='1', hole_len='5',
                   hole_pts='3990 2998.5 3990 3001.5')
        result = supplier([round_pad, slot, 'HOLE~4000~3010~1~gge9~0'])
        holes = result['pattern']['holes']
        self.assertEqual(holes[0], dict(shape='circle', center=['2540000', '0'], size=['762000', '762000'], angle='0'))
        self.assertEqual(holes[1]['shape'], 'oval')
        self.assertEqual(holes[1]['size'], ['508000', '1270000'])
        self.assertEqual(holes[2]['center'], ['0', '2540000'])
        self.assertEqual(result['pattern']['pads'][0]['layers'], ['B.Cu', 'F.Cu'])
        self.assertEqual([h['plated'] for h in result['hole_locators']], [True, True, False])
        failures = {
            'missing endpoints': pad('OVAL', 3990, 3000, 4, 8, '2', layer='11', hole_r='1', hole_len='5'),
            'not axis-aligned': pad('OVAL', 3990, 3000, 4, 8, '2', layer='11', hole_r='1', hole_len='5',
                                    hole_pts='3989 2998.5 3991 3001.5'),
            'disagree': pad('OVAL', 3990, 3000, 4, 8, '2', layer='11', hole_r='1', hole_len='6',
                            hole_pts='3990 2998.5 3990 3001.5'),
            'offset': pad('OVAL', 3990, 3000, 4, 8, '2', layer='11', hole_r='1', hole_len='5',
                          hole_pts='3990 2999.5 3990 3002.5'),
            'non-plated': pad('ELLIPSE', 4010, 3000, 6, 6, '1', layer='11', hole_r='1.5', plated='N'),
            'not multilayer': pad('ELLIPSE', 4010, 3000, 6, 6, '1', layer='1', hole_r='1.5'),
            'disagree ': pad('ELLIPSE', 4010, 3000, 6, 6, '1', layer='11'),
        }
        for label, record in failures.items():
            with self.subTest(label), self.assertRaises(SupplierError):
                supplier([record])


LIB = 'lib/Owned.pretty/'


class NativeExtractionTests(unittest.TestCase):
    def extract(self, pads, extra=''):
        return extract_footprint(parse_sexpr(kicad_footprint('X', pads, extra=extra)), side='F.Cu',
                                 frame_angle=Fraction(0), label='X')

    def test_roundrect_radius_offset_slots_npth_and_paste_only(self):
        result = self.extract(
            '(pad "1" smd roundrect (at 1 2 90) (size 1 0.6) (layers "F.Cu" "F.Mask") (roundrect_rratio 0.25)) '
            '(pad "2" thru_hole oval (at -2 1 90) (size 2 1) (drill oval 1.2 0.6 (offset 0.3 0.1)) '
            '(layers "*.Cu" "*.Mask") (remove_unused_layers no)) '
            '(pad "" np_thru_hole circle (at 0 -2) (size 1 1) (drill 1) (layers "*.Cu" "*.Mask")) '
            '(pad "" smd rect (at 3 3) (size 0.2 0.2) (layers "F.Paste"))')
        pads, holes = result['pattern']['pads'], result['pattern']['holes']
        self.assertEqual(pads[0]['primitive'], dict(shape='roundrect', center=['1000000', '2000000'],
                         size=['1000000', '600000'], angle='90', radius='150000'))
        # Copper is offset from the drill in the pad frame (pcbnew 10.0.7 control).
        self.assertEqual(pads[1]['primitive']['center'], ['-1900000', '700000'])
        self.assertEqual(pads[1]['layers'], ['B.Cu', 'F.Cu'])
        self.assertEqual(holes[0], dict(shape='oval', center=['-2000000', '1000000'], size=['1200000', '600000'],
                                        angle='90'))
        self.assertEqual(holes[1]['shape'], 'circle')
        self.assertEqual(result['inventory']['ignored'], {'npth_without_copper': 1, 'paste_or_mask_only_pad': 1})

    def test_geometry_affecting_or_unknown_native_fields_refuse(self):
        base = '(at 0 0) (size 1 1) (layers "F.Cu")'
        cases = {
            f'(pad "1" smd roundrect {base} (roundrect_rratio 0.25) (chamfer_ratio 0.2) (chamfer top_left))': 'unsupported_shape',
            f'(pad "1" smd custom {base} (options (clearance outline)) (primitives))': 'unsupported_shape',
            f'(pad "1" smd trapezoid {base} (rect_delta 0.1 0))': 'unsupported_shape',
            f'(pad "1" smd rect {base} (future_geometry 1))': 'unsupported_shape',
            '(pad "1" smd rect (at 0 0) (size 1 1) (layers "In1.Cu"))': 'unsupported_layer',
            '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "B.Cu"))': 'unsupported_layer',
            f'(pad "" smd rect {base})': 'unsupported_shape',
            f'(pad "1" smd roundrect {base})': 'native_malformed',
            f'(pad "1" smd roundrect {base} (roundrect_rratio 0.6))': 'native_malformed',
            f'(pad "1" smd rect {base} (roundrect_rratio 0.2))': 'unsupported_shape',
            f'(pad "1" smd circle (at 0 0) (size 1 2) (layers "F.Cu"))': 'native_malformed',
            '(pad "1" smd rect (at 0 0) (size 0 1) (layers "F.Cu"))': 'native_malformed',
            '(pad "1" thru_hole circle (at 0 0) (size 2 2) (layers "F.Cu"))': 'native_malformed',
            '(pad "1" thru_hole circle (at 0 0) (size 2 2) (drill 1) (layers "F.Cu"))': 'unsupported_layer',
            '(pad "" np_thru_hole circle (at 0 0) (size 2 2) (drill 1) (layers "*.Cu"))': 'unsupported_shape',
            '(pad "1" thru_hole circle (at 0 0 45) (size 2 2) (drill 1 (offset 0.1 0)) (layers "*.Cu"))': 'unsupported_rotation',
        }
        for pads, mode in cases.items():
            with self.subTest(pads=pads), self.assertRaises(PatternError) as caught:
                self.extract(pads)
            self.assertEqual(caught.exception.mode, mode)
        for extra in ('(fp_poly (pts (xy 0 0) (xy 1 0) (xy 1 1)) (layer "F.Cu"))',
                      '(zone (layer "F.Cu"))', '(fp_text user "X" (at 0 0) (layer "B.Cu"))',
                      '(property "Reference" "R1" (at 0 0) (layer "F.Cu"))', '(net_tie_pad_groups "1,2")'):
            with self.subTest(extra=extra), self.assertRaises(PatternError):
                self.extract('(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))', extra)

    def test_board_instances_return_to_library_frame(self):
        pads = ('(pad "1" smd roundrect (at 1 2 {a1}) (size 1 0.6) (layers "{cu}" "{mask}") (roundrect_rratio 0.25)) '
                '(pad "2" thru_hole oval (at -2 {y2} {a2}) (size 2 1) (drill oval 1.2 0.6 (offset 0.3 {oy})) '
                '(layers "*.Cu" "*.Mask"))')
        library = self.extract(pads.format(a1=90, a2=90, cu='F.Cu', mask='F.Mask', y2=1, oy=0.1))
        # Front instance at 30 degrees: positions stay footprint-local, pad angles are absolute.
        front = pads.format(a1=120, a2=120, cu='F.Cu', mask='F.Mask', y2=1, oy=0.1)
        # Back instance at -30 degrees (flipped from 30): local Y, angles and offset Y mirrored.
        back = pads.format(a1=240, a2=240, cu='B.Cu', mask='B.Mask', y2=-1, oy=-0.1).replace('(at 1 2', '(at 1 -2')
        board = ('(kicad_pcb (version 20260206) '
                 f'(footprint "Owned:X" (layer "F.Cu") (uuid "u1") (at 10 10 30) (property "Reference" "U1") {front}) '
                 f'(footprint "Owned:X" (layer "B.Cu") (uuid "u2") (at 30 10 -30) (property "Reference" "U2") {back}) '
                 f'(footprint "Owned:X" (layer "F.Cu") (at 50 10) (property "Reference" "U3") {front}) '
                 f'(footprint "Owned:X" (layer "F.Cu") (at 50 10) (property "Reference" "U3") {front}))').encode()
        found = board_footprints(board)
        for ref in ('U1', 'U2'):
            with self.subTest(ref=ref):
                instance = board_footprint(found, ref, 'sha')
                self.assertEqual(instance['pattern'], library['pattern'])
        self.assertEqual(board_footprint(found, 'U2', 'sha')['source']['side'], 'B.Cu')
        for ref in ('U3', 'U9'):
            with self.subTest(ref=ref), self.assertRaises(PatternError) as caught:
                board_footprint(found, ref, 'sha')
            self.assertEqual(caught.exception.mode, 'native_missing')


class Workspace:
    """A captured candidate directory with owned supplier, library and criteria bytes."""

    def __init__(self, root):
        self.root = Path(root)
        self.files = {}
        self.requirements = []

    def write(self, path, data):
        self.files[path] = data
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def supplier(self, ref, lcsc, shapes, **kw):
        raw = component(lcsc, shapes, **kw)
        self.write(f'supplier/{ref}.raweasy.json', raw)
        prov = provenance(lcsc, f'supplier/{ref}.raweasy.json', raw)
        self.write(f'supplier/{ref}.provenance.json', prov)
        return dict(provenance=f'supplier/{ref}.provenance.json', sha256=hashlib.sha256(prov).hexdigest())

    def library(self, footprints):
        members = {}
        for name, pads in footprints.items():
            data = kicad_footprint(name, pads)
            self.write(LIB + name + '.kicad_mod', data)
            members[LIB + name + '.kicad_mod'] = data
        self.write('lib/manifest.json', canonical(manifest(members)))

    def require(self, ref, lcsc, binding, native, rotation='0', translation='declared_origin', aliases=None):
        self.requirements.append(dict(reference=ref, lcsc=lcsc, supplier=binding, native=native,
                                      registration=dict(translation=translation, rotation=rotation, mirror=False),
                                      pin_aliases=aliases))

    def criteria(self, **override):
        value = dict(schema='agent-ee.land-pattern-criteria/1', threshold='0.98', pin_tolerance_nm='100000',
                     source_tolerance_nm='200', refinement_levels=[2, 4, 8, 16], library_manifest='lib/manifest.json',
                     requirements=copy.deepcopy(self.requirements))
        value.update(override)
        return value

    def evaluate(self, board=None, **override):
        return evaluate(self.criteria(**override), dict(self.files), board)


def lib(name):
    return dict(source='library', lib_id='Owned:' + name)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ws = Workspace(tmp.name)
        self.ws.library({'SOT': SOT_KICAD, 'CHIP_A': chip_kicad('0.508', '0.6096', '0.7112'),
                         'CHIP_B': chip_kicad('0.762', '0.889', '0.9906')})

    def only(self, report):
        self.assertEqual(len(report['references']), 1)
        return report['references'][0]

    def test_sot23_same_geometry_and_pins_pass_at_exact_iou_one(self):
        self.ws.require('U1', 'C900000003', self.ws.supplier('U1', 'C900000003', sot_supplier()), lib('SOT'),
                        rotation='90')
        report = self.ws.evaluate()
        record = self.only(report)
        self.assertEqual((report['execution'], report['outcome'], report['coverage']), ('ok', 'pass', 'complete'))
        self.assertEqual(record['mode'], 'match')
        self.assertEqual(ratio(record['copper']['F.Cu']['lower']), 1)
        self.assertEqual(record['holes']['decision'], 'not_applicable')
        self.assertEqual(sorted((p['native'], p['supplier']) for p in record['pins']['pairs']),
                         [('1', '1'), ('2', '2'), ('3', '3')])
        self.assertEqual(report['counts'], dict(eligible=1, evaluated=1, passed=1, defect=0, unknown=0,
                                                unsupported=0, malformed=0, failed=0))

    def test_sot23_swapped_numbers_are_a_defect_even_at_iou_one(self):
        binding = self.ws.supplier('U1', 'C900000003', sot_supplier(('2', '1', '3')))
        self.ws.require('U1', 'C900000003', binding, lib('SOT'), rotation='90')
        report = self.ws.evaluate()
        record = self.only(report)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'))
        self.assertEqual(record['modes'], ['pin_conflict'])
        self.assertEqual(ratio(record['copper']['F.Cu']['lower']), 1)
        self.assertIn(dict(native='1', supplier='2', native_index=0, supplier_index=0), record['pins']['pairs'])
        self.assertEqual(record['witness']['criteria_sha256'], report['criteria_sha256'])
        # An explicit captured bijective alias map is the only way numbers may differ.
        self.ws.requirements[0]['pin_aliases'] = {'1': '2', '2': '1', '3': '3'}
        self.assertEqual(self.only(self.ws.evaluate())['outcome'], 'pass')

    def test_declared_registration_is_not_searched(self):
        self.ws.require('U1', 'C900000003', self.ws.supplier('U1', 'C900000003', sot_supplier()), lib('SOT'),
                        rotation='0')
        record = self.only(self.ws.evaluate())
        self.assertEqual(record['outcome'], 'defect')
        self.assertIn('low_accuracy', record['modes'])
        self.assertEqual(record['registration']['rotation'], '0')

    def test_wrong_size_variant_reports_exact_low_iou(self):
        # Owned 0402-like supplier pattern against 0402-like and 0603-like rect footprints.
        binding = self.ws.supplier('R1', 'C900000004', chip(2, '2.4', '2.8'))
        self.ws.require('R1', 'C900000004', binding, lib('CHIP_A'))
        self.ws.require('R2', 'C900000004', binding, lib('CHIP_B'))
        report = self.ws.evaluate()
        good, wrong = report['references']
        self.assertEqual(good['outcome'], 'pass')
        self.assertEqual((wrong['outcome'], wrong['modes'][0]), ('defect', 'low_accuracy'))
        # Independent analytic control (exact rectangles, mm):
        # supplier pad x [0.2032, 0.8128], y +-0.3556; native x [0.3175, 1.2065], y +-0.4953.
        inter = 2 * (Fraction('0.8128') - Fraction('0.3175')) * Fraction('0.7112')
        union = 2 * Fraction('0.6096') * Fraction('0.7112') + 2 * Fraction('0.889') * Fraction('0.9906') - inter
        metric = wrong['copper']['F.Cu']
        self.assertEqual(ratio(metric['lower']), inter / union)
        self.assertEqual(ratio(metric['upper']), inter / union)
        self.assertEqual(metric['presented'], dict(lower='0.366197', upper='0.366198'))
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'))

    def test_missing_supplier_unknown_shape_and_coverage_never_pass(self):
        self.ws.require('U1', 'C900000003', None, lib('SOT'))
        report = self.ws.evaluate()
        record = self.only(report)
        self.assertEqual((record['execution'], record['outcome'], record['mode']),
                         ('unsupported', 'unknown', 'supplier_missing'))
        self.assertEqual((report['execution'], report['outcome'], report['coverage']),
                         ('unsupported', 'unknown', 'incomplete'))
        ptm = 'PT~M 245 303 L 235 310 L 245 317 Z ~#880000~1~0~none~gge49~0~'
        self.ws.require('U2', 'C900000003', self.ws.supplier('U2', 'C900000003', sot_supplier() + [ptm]),
                        lib('SOT'), rotation='90')
        record = self.ws.evaluate()['references'][1]
        self.assertEqual((record['execution'], record['outcome'], record['mode']),
                         ('unsupported', 'unknown', 'unsupported_shape'))
        # A proven defect elsewhere still stands, with incomplete coverage recorded.
        self.ws.require('U3', 'C900000003', self.ws.supplier('U3', 'C900000003', sot_supplier(('3', '2', '1'))),
                        lib('SOT'), rotation='90')
        report = self.ws.evaluate()
        self.assertEqual((report['execution'], report['outcome'], report['coverage']), ('ok', 'defect', 'incomplete'))
        self.assertEqual(report['counts']['unsupported'], 2)
        self.assertEqual(report['counts']['passed'], 0)

    def test_reruns_are_identical_and_tampering_refuses(self):
        self.ws.require('U1', 'C900000003', self.ws.supplier('U1', 'C900000003', sot_supplier(('2', '1', '3'))),
                        lib('SOT'), rotation='90')
        first, second = self.ws.evaluate(), self.ws.evaluate()
        self.assertEqual(canonical(first), canonical(second))
        criteria = self.ws.criteria()
        self.assertTrue(replay(first, criteria, dict(self.ws.files)))
        forged = dict(first, outcome='pass')
        self.assertFalse(replay(forged, criteria, dict(self.ws.files)))
        tampered = dict(self.ws.files)
        raw = tampered['supplier/U1.raweasy.json']
        tampered['supplier/U1.raweasy.json'] = raw.replace(b'"OWNED"', b'"OWNEX"', 1)
        record = evaluate(criteria, tampered)['references'][0]
        self.assertEqual((record['execution'], record['mode']), ('malformed', 'supplier_identity'))
        self.assertFalse(replay(first, criteria, tampered))
        tampered = dict(self.ws.files)
        tampered[LIB + 'SOT.kicad_mod'] += b' '
        record = evaluate(criteria, tampered)['references'][0]
        self.assertEqual((record['execution'], record['mode']), ('unsupported', 'native_missing'))
        changed = copy.deepcopy(criteria)
        changed['threshold'] = '0.5'
        self.assertFalse(replay(first, changed, dict(self.ws.files)))

    def test_bbox_centring_is_a_declared_rule_not_a_fit(self):
        shifted = self.ws.supplier('U1', 'C900000003', sot_supplier(), origin=(4001, 3002))
        self.ws.require('U1', 'C900000003', shifted, lib('SOT'), rotation='90')
        self.assertEqual(self.only(self.ws.evaluate())['outcome'], 'defect')
        self.ws.requirements[0]['registration']['translation'] = 'copper_bbox_center'
        self.assertEqual(self.only(self.ws.evaluate())['outcome'], 'pass')

    def test_board_instance_source_and_through_hole_holes(self):
        tht = ('(pad "1" thru_hole circle (at -1.27 0) (size 1.524 1.524) (drill 0.762) (layers "*.Cu" "*.Mask")) '
               '(pad "2" thru_hole oval (at 1.27 0) (size 1.016 2.032) (drill oval 0.508 1.27) (layers "*.Cu" "*.Mask"))')
        shapes = [pad('ELLIPSE', 3995, 3000, 6, 6, '1', layer='11', hole_r='1.5'),
                  pad('OVAL', 4005, 3000, 4, 8, '2', layer='11', hole_r='1', hole_len='5',
                      hole_pts='4005 2998.5 4005 3001.5')]
        board = ('(kicad_pcb (version 20260206) (footprint "Owned:THT" (layer "B.Cu") (at 5 5 90) '
                 f'(property "Reference" "J1") {tht.replace(" 0) (size", " 0 90) (size")}))').encode()
        self.ws.write('board.kicad_pcb', board)
        self.ws.require('J1', 'C900000005', self.ws.supplier('J1', 'C900000005', shapes), dict(source='board'))
        report = self.ws.evaluate('board.kicad_pcb')
        record = self.only(report)
        self.assertEqual(record['outcome'], 'pass', record)
        self.assertEqual(record['native']['side'], 'B.Cu')
        self.assertEqual(sorted(record['copper']), ['B.Cu', 'F.Cu'])
        self.assertEqual(record['holes']['decision'], 'pass')
        # A different drill is a proven hole defect.
        self.ws.write('board.kicad_pcb', board.replace(b'(drill 0.762)', b'(drill 0.5)'))
        record = self.only(self.ws.evaluate('board.kicad_pcb'))
        self.assertEqual(record['outcome'], 'defect')
        self.assertEqual(record['holes']['decision'], 'defect')
        record = self.only(self.ws.evaluate(None))
        self.assertEqual((record['execution'], record['mode']), ('unsupported', 'native_missing'))


# Published pad dimensions as facts (no supplier or library file is copied): the
# EasyEDA C11702 land pattern has 2.227 x 2.126 unit RECT pads at +-1.704 units
# (0.5657 x 0.5400 mm at +-0.4328 mm); KiCad 10 stock R_0402/R_0603 pads are
# 0.25-ratio roundrects 0.54 x 0.64 at +-0.51 and 0.80 x 0.95 at +-0.825 mm.
C11702_SHAPES = chip(Fraction('1.704'), '2.227', '2.126')
VARIANTS = {'R0402': chip_roundrect_kicad('0.51', '0.54', '0.64'),
            'R0603': chip_roundrect_kicad('0.825', '0.8', '0.95')}


def sampled_iou(supplier_pad, native_pad, step=0.002):
    """Independent float control: midpoint sampling of one rect pad against one roundrect pad (mm)."""
    (sx, sw, sh), (nx, nw, nh, r) = supplier_pad, native_pad
    def inside_native(x, y):
        dx, dy = max(abs(x - nx) - (nw / 2 - r), 0), max(abs(y) - (nh / 2 - r), 0)
        return abs(x - nx) <= nw / 2 and abs(y) <= nh / 2 and dx * dx + dy * dy <= r * r
    x0, x1 = min(sx - sw / 2, nx - nw / 2), max(sx + sw / 2, nx + nw / 2)
    y1 = max(sh, nh) / 2
    inter = union = 0
    for i in range(int((x1 - x0) / step) + 1):
        x = x0 + (i + 0.5) * step
        for j in range(int(2 * y1 / step) + 1):
            y = -y1 + (j + 0.5) * step
            a = abs(x - sx) <= sw / 2 and abs(y) <= sh / 2
            b = inside_native(x, y)
            inter += a and b
            union += a or b
    return inter / union


class C11702ThresholdTests(unittest.TestCase):
    """The absolute threshold is the only acceptance rule (AGENTEE-31 criterion 1 coordinator ruling, 2026-10-22)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ws = Workspace(tmp.name)
        self.ws.library(VARIANTS)
        self.binding = self.ws.supplier('R1', 'C11702', C11702_SHAPES)

    def test_c11702_shaped_pattern_is_low_accuracy_against_0402_and_0603(self):
        self.ws.require('R1', 'C11702', self.binding, lib('R0402'))
        self.ws.require('R2', 'C11702', self.binding, lib('R0603'))
        report = self.ws.evaluate(pin_tolerance_nm='250000')
        r0402, r0603 = report['references']
        self.assertEqual((r0402['execution'], r0402['outcome'], r0402['modes']), ('ok', 'defect', ['low_accuracy']))
        self.assertEqual(r0402['pins']['mode'], 'pin_agreement')
        self.assertEqual([(p['native'], p['supplier']) for p in r0402['pins']['pairs']], [('1', '1'), ('2', '2')])
        metric = r0402['copper']['F.Cu']
        self.assertEqual(metric['decision'], 'defect')
        control = sampled_iou((0.4328, 0.5657, 0.54), (0.51, 0.54, 0.64, 0.135))
        self.assertLess(ratio(metric['lower']), control)
        self.assertLess(control, ratio(metric['upper']))
        self.assertAlmostEqual(control, 0.671, delta=0.002)
        self.assertLess(ratio(metric['upper']), Fraction('0.98'))
        self.assertEqual((r0603['execution'], r0603['outcome']), ('ok', 'defect'))
        self.assertEqual(r0603['modes'][0], 'low_accuracy')
        self.assertLess(ratio(r0603['copper']['F.Cu']['upper']), Fraction('0.19'))
        self.assertIn('presented', r0603['copper']['F.Cu'])
        self.assertEqual((report['execution'], report['outcome'], report['coverage']), ('ok', 'defect', 'complete'))
        self.assertEqual(report['counts']['passed'], 0)
        self.assertEqual(canonical(report), canonical(self.ws.evaluate(pin_tolerance_nm='250000')))
        self.assertTrue(replay(report, self.ws.criteria(pin_tolerance_nm='250000'), dict(self.ws.files)))

    def test_three_percent_overlap_is_low_accuracy_never_pass(self):
        # Regression for the withdrawn nearest-variant rule, which passed this pair at IoU about 0.031.
        tiny = self.ws.supplier('R1', 'C900000009', chip(Fraction('2.008'), '0.4', '0.4'))
        self.ws.require('R1', 'C900000009', tiny, lib('R0402'))
        record = self.ws.evaluate()['references'][0]
        self.assertEqual((record['execution'], record['outcome'], record['modes']), ('ok', 'defect', ['low_accuracy']))
        metric = record['copper']['F.Cu']
        self.assertGreater(ratio(metric['lower']), Fraction('0.03'))
        self.assertLess(ratio(metric['upper']), Fraction('0.033'))
        self.assertEqual(record['pins']['mode'], 'pin_agreement')

    def test_relative_acceptance_criteria_are_refused(self):
        self.ws.require('R1', 'C11702', self.binding, lib('R0402'))
        nearest = dict(rule='nearest_variant', alternatives=[lib('R0603')])
        for schema in ('agent-ee.land-pattern-criteria/1', 'agent-ee.land-pattern-criteria/2'):
            criteria = self.ws.criteria(schema=schema)
            criteria['requirements'][0]['acceptance'] = nearest
            with self.subTest(schema=schema):
                with self.assertRaises((ValueError, TypeError)):
                    validate_criteria(criteria)
                report = evaluate(criteria, dict(self.ws.files))
                self.assertEqual((report['execution'], report['outcome']), ('malformed', 'unknown'))


class CriteriaTests(unittest.TestCase):
    def criteria(self):
        ws = Workspace('/nonexistent')
        ws.require('U1', 'C1', dict(provenance='p.json', sha256='0' * 64), lib('SOT'))
        return ws.criteria()

    def test_complete_immutable_criteria_required(self):
        self.assertEqual(validate_criteria(self.criteria())['sources'], ['library'])
        mutations = [
            lambda c: c.update(requirements=[]),
            lambda c: c.update(threshold=0.98),
            lambda c: c.update(threshold='1'),
            lambda c: c.update(refinement_levels=[4, 2]),
            lambda c: c.update(refinement_levels=[2, 3]),
            lambda c: c.update(extra=1),
            lambda c: c['requirements'][0]['registration'].update(rotation='45'),
            lambda c: c['requirements'][0]['registration'].update(mirror=True),
            lambda c: c['requirements'][0]['registration'].update(translation='best_fit'),
            lambda c: c['requirements'][0].update(pin_aliases={'1': 'A', '2': 'A'}),
            lambda c: c['requirements'][0].update(lcsc='11702'),
            lambda c: c['requirements'].append(copy.deepcopy(c['requirements'][0])),
            lambda c: c.update(library_manifest=None),
            lambda c: c['requirements'][0]['supplier'].update(provenance='../escape.json'),
        ]
        for index, mutate in enumerate(mutations):
            criteria = self.criteria()
            mutate(criteria)
            with self.subTest(index=index), self.assertRaises((ValueError, TypeError)):
                validate_criteria(criteria)
            report = evaluate(criteria, {})
            self.assertEqual((report['execution'], report['outcome']), ('malformed', 'unknown'))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ws = Workspace(tmp.name)
        self.ws.library({'SOT': SOT_KICAD})
        self.ws.require('U1', 'C900000003', self.ws.supplier('U1', 'C900000003', sot_supplier(('2', '1', '3'))),
                        lib('SOT'), rotation='90')
        self.ws.write('criteria.json', canonical(self.ws.criteria()))
        self.scope = dict(baseline=dict(requirements=[dict(id='LP', checks=['lp'],
                                                           source=dict(path='criteria.json', locator='all'))]))

    def test_capability_binds_complete_immutable_closure(self):
        files = dict.fromkeys(self.ws.files, 'hash')
        result = capability(dict(criteria='criteria.json'), self.scope, files, self.ws.root, 'lp')
        self.assertEqual(result['immutable_inputs'], sorted(['criteria.json', 'lib/manifest.json', LIB + 'SOT.kicad_mod',
                         'supplier/U1.provenance.json', 'supplier/U1.raweasy.json']))
        self.assertEqual(result['predicates'], ['land_pattern:copper-hole-iou-pin-agreement/1'])
        for path in list(files):
            reduced = {k: v for k, v in files.items() if k != path}
            with self.subTest(path=path), self.assertRaises(ValueError):
                capability(dict(criteria='criteria.json'), self.scope, reduced, self.ws.root, 'lp')
        with self.assertRaisesRegex(ValueError, 'baseline requirement'):
            capability(dict(criteria='criteria.json'), {}, files, self.ws.root, 'lp')
        with self.assertRaises(ValueError):
            capability(dict(criteria='criteria.json', extra=1), self.scope, files, self.ws.root, 'lp')

    def test_cli_writes_sealed_report_once(self):
        out = self.ws.root / 'out.json'
        self.assertEqual(main([str(self.ws.root / 'criteria.json'), '--captures', str(self.ws.root),
                               '--out', str(out)]), 0)
        report = json.loads(out.read_bytes())
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'))
        self.assertEqual(report, evaluate(self.ws.criteria(), dict(self.ws.files)))
        with self.assertRaises(FileExistsError):
            main([str(self.ws.root / 'criteria.json'), '--captures', str(self.ws.root), '--out', str(out)])
        (self.ws.root / 'supplier/U1.raweasy.json').unlink()
        out2 = self.ws.root / 'out2.json'
        main([str(self.ws.root / 'criteria.json'), '--captures', str(self.ws.root), '--out', str(out2)])
        report = json.loads(out2.read_bytes())
        self.assertEqual((report['execution'], report['outcome']), ('failed', 'unknown'))
        self.assertEqual(report['references'][0]['mode'], 'input_missing')

    def test_adapter_runs_offline_in_the_existing_sandbox(self):
        from toolchain import require_toolchain
        require_toolchain()
        import shutil
        import time
        from agent_ee.checks import Ctx
        run = Path(tempfile.mkdtemp(prefix='agent-ee-land-run-'))
        self.addCleanup(shutil.rmtree, run)
        shutil.copytree(self.ws.root, run / 'inputs')
        manifest = dict(candidate_id='owned', files=dict.fromkeys(self.ws.files, 'hash'),
                        scope=dict(self.scope, checks=[]))
        ctx = Ctx(run, manifest, dict(id='lp', kind='land_pattern', params=dict(criteria='criteria.json')),
                  time.monotonic() + 120)
        from agent_ee.land_pattern import check_land_pattern
        check_land_pattern(ctx)
        self.assertEqual((ctx.result['execution'], ctx.result['outcome']), ('ok', 'defect'))
        self.assertEqual(ctx.result['location'], 'U1:pin_conflict')
        self.assertIn('work/lp/land-pattern.json', ctx.result['evidence'])
        self.assertEqual(ctx.result['details']['report'], evaluate(self.ws.criteria(), dict(self.ws.files)))
        self.assertEqual(ctx.result['details']['tool']['exit_code'], 0)


if __name__ == '__main__':
    unittest.main()

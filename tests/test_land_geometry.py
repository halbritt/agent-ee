"""Owned analytical controls for rational land-pattern geometry."""
from fractions import Fraction as F
import unittest

from agent_ee.land_geometry import polygon_area


class PolygonTests(unittest.TestCase):
    def test_overlapping_union_and_aperture(self):
        a = [(0, 0), (2, 0), (2, 2), (0, 2)]
        b = [(1, 0), (3, 0), (3, 2), (1, 2)]
        hole = [(1, 0), (2, 0), (2, 1), (1, 1)]
        self.assertEqual(polygon_area([a, b]), F(6))
        self.assertEqual(polygon_area([a, b], subtract=[hole]), F(5))


def primitive(shape='rect', x='0', y='0', w='2', h='2', **extra):
    return dict(shape=shape, center=[x, y], size=[w, h], **extra)


def pattern(*primitives, holes=()):
    return {'pads': [{'number': str(i+1), 'layers': ['F.Cu'], 'primitive': p}
                     for i, p in enumerate(primitives)], 'holes': list(holes)}


def number(record):
    return F(record['numerator'], record['denominator'])


class BoundsTests(unittest.TestCase):
    def test_circle_is_bounded_and_refines(self):
        from agent_ee.land_geometry import area_bounds
        import math
        previous = None
        for n in (1, 2, 4, 8):
            result = area_bounds(pattern(primitive('circle')), refinement=n)['copper']['F.Cu']
            lo, hi = number(result['lower']), number(result['upper'])
            self.assertLess(float(lo), math.pi)
            self.assertGreater(float(hi), math.pi)
            if previous:
                self.assertGreater(lo, previous[0])
                self.assertLess(hi, previous[1])
            previous = lo, hi


class ComparisonTests(unittest.TestCase):
    def test_wrong_resistor_size_and_exact_identity(self):
        from agent_ee.land_geometry import compare_patterns
        a = pattern(primitive(w='1', h='1'))
        b = pattern(primitive(w='2', h='2'))
        result = compare_patterns(a, b)
        self.assertEqual(result['mode'], 'low_accuracy')
        self.assertEqual(number(result['copper']['F.Cu']['lower']), F(1, 4))
        for shape, extra in [('rect', {}), ('circle', {}), ('oval', {}), ('roundrect', {'radius': '0.3'})]:
            p = pattern(primitive(shape, **extra))
            same = compare_patterns(p, p)
            self.assertEqual(same['mode'], 'pin_agreement')
            self.assertEqual(number(same['copper']['F.Cu']['lower']), 1)
            self.assertEqual(same['holes']['decision'], 'not_applicable')

    def test_threshold_equality_and_curved_uncertainty(self):
        from agent_ee.land_geometry import compare_patterns
        a, b = pattern(primitive()), pattern(primitive(x='1'))
        equality = compare_patterns(a, b, threshold='0.333333333333333333333333333333333333')
        self.assertEqual(equality['copper']['F.Cu']['decision'], 'pass')
        exact = compare_patterns(a, pattern(primitive(w='4', h='4')), threshold='0.25')
        self.assertEqual(exact['copper']['F.Cu']['decision'], 'defect')
        curved = compare_patterns(pattern(primitive('circle')), pattern(primitive('circle', x='1')),
                                  threshold='0.25', levels=(1,))
        self.assertEqual(curved['mode'], 'numeric_uncertainty')
        self.assertLess(number(curved['copper']['F.Cu']['lower']), F(1, 4))
        self.assertGreater(number(curved['copper']['F.Cu']['upper']), F(1, 4))

    def test_pin_swap_at_unit_iou_and_ambiguity(self):
        from agent_ee.land_geometry import compare_patterns, match_pins
        import copy
        original = pattern(primitive(x='-3'), primitive(x='2'), primitive(x='0', y='4'))
        swapped = copy.deepcopy(original)
        swapped['pads'][0]['number'], swapped['pads'][1]['number'] = '2', '1'
        result = compare_patterns(original, swapped)
        self.assertEqual(result['mode'], 'pin_conflict')
        self.assertEqual(number(result['copper']['F.Cu']['lower']), 1)
        coincident = pattern(primitive(), primitive())
        self.assertEqual(match_pins(coincident, coincident)['mode'], 'pin_mapping_unavailable')
        coincident['pads'][1]['number'] = '1'
        self.assertEqual(match_pins(coincident, coincident), {'mode': 'pin_agreement', 'pairs': []})
        self.assertEqual(match_pins(original, original, aliases={'1': '2'})['mode'], 'alias_conflict')

    def test_non_greedy_correspondence(self):
        from agent_ee.land_geometry import match_pins
        a = pattern(primitive(x='0'), primitive(x='2'))
        b = pattern(primitive(x='1'), primitive(x='-1'))
        b['pads'][0]['number'], b['pads'][1]['number'] = '2', '1'
        self.assertEqual(match_pins(a, b, tolerance='1'),
                         {'mode': 'pin_agreement', 'pairs': [[0, 1], [1, 0]]})


class AnalyticTests(unittest.TestCase):
    def test_capsule_roundrect_and_annulus_controls(self):
        from agent_ee.land_geometry import area_bounds
        import math
        cases = [
            (pattern(primitive('oval', w='6', h='2')), 8+math.pi),
            (pattern(primitive('roundrect', w='6', h='4', radius='1')), 20+math.pi),
            (pattern(primitive('circle', w='4', h='4'), holes=[primitive('circle')]), 3*math.pi),
        ]
        for p, expected in cases:
            previous = None
            for n in (1, 2, 4, 8):
                bounds = area_bounds(p, refinement=n)['copper']['F.Cu']
                lo, hi = number(bounds['lower']), number(bounds['upper'])
                self.assertLess(float(lo), expected)
                self.assertGreater(float(hi), expected)
                if previous:
                    self.assertGreaterEqual(lo, previous[0])
                    self.assertLessEqual(hi, previous[1])
                previous = lo, hi

    def test_circle_lens_and_monotonic_iou(self):
        from agent_ee.land_geometry import compare_patterns
        import math
        # Unit circles separated by radius: lens = 2*pi/3 - sqrt(3)/2.
        lens = 2*math.pi/3-math.sqrt(3)/2
        expected = lens/(2*math.pi-lens)
        a, b = pattern(primitive('circle')), pattern(primitive('circle', x='1'))
        previous = None
        for n in (1, 2, 4, 8):
            metric = compare_patterns(a, b, threshold='0.25', levels=(n,))['copper']['F.Cu']
            lo, hi = number(metric['lower']), number(metric['upper'])
            self.assertLess(float(lo), expected)
            self.assertGreater(float(hi), expected)
            if previous:
                self.assertGreaterEqual(lo, previous[0])
                self.assertLessEqual(hi, previous[1])
            previous = lo, hi
        refined = compare_patterns(a, b, threshold='0.25')['copper']['F.Cu']
        self.assertEqual(refined['decision'], 'defect')

    def test_holes_overlap_layers_and_offset_slots(self):
        from agent_ee.land_geometry import area_bounds, compare_patterns
        p = pattern(primitive(w='4', h='4'), primitive(x='2', w='4', h='4'),
                    holes=[primitive(w='2', h='2'), primitive(x='1', w='2', h='2')])
        p['pads'][0]['layers'].append('B.Cu')
        result = area_bounds(p)
        # F copper union24, holes union6; B copper16 minus union6.
        self.assertEqual(number(result['copper']['F.Cu']['lower']), 18)
        self.assertEqual(number(result['copper']['B.Cu']['upper']), 10)
        self.assertEqual(number(result['holes']['lower']), 6)
        slot = primitive('oval', x='1', w='4', h='2', angle='90')
        ring = pattern(primitive(w='10', h='10'), holes=[slot, slot])
        same = compare_patterns(ring, ring)
        self.assertEqual(number(same['holes']['lower']), 1)
        no_hole = pattern(primitive(w='10', h='10'))
        self.assertEqual(number(compare_patterns(ring, no_hole)['holes']['upper']), 0)

    def test_exact_quarter_turns_and_deterministic_order(self):
        from agent_ee.land_geometry import compare_patterns
        import copy
        a = pattern(primitive('roundrect', w='6', h='2', radius='0.4', angle='90'))
        b = pattern(primitive('roundrect', w='2', h='6', radius='0.4', angle='-360'))
        self.assertEqual(number(compare_patterns(a, b)['copper']['F.Cu']['lower']), 1)
        a = pattern(primitive(x='-2'), primitive(x='2'), primitive(x='0', y='3'))
        b = copy.deepcopy(a)
        b['pads'].reverse()
        first = compare_patterns(a, b)
        self.assertEqual(first, compare_patterns(a, b))
        self.assertEqual(number(first['copper']['F.Cu']['lower']), 1)


class PolygonBoundaryTests(unittest.TestCase):
    def test_slanted_intersection_and_collinear_edges(self):
        from agent_ee.land_geometry import polygon_intersection_area
        square = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        diamond = [(F(3, 2), 0), (0, F(3, 2)), (-F(3, 2), 0), (0, -F(3, 2))]
        self.assertEqual(polygon_intersection_area([square], [diamond]), F(7, 2))
        self.assertEqual(polygon_area([square, diamond]), 5)
        self.assertEqual(polygon_area([square, list(reversed(square))]), 4)
        adjacent = [(1, -1), (3, -1), (3, 1), (1, 1)]
        self.assertEqual(polygon_intersection_area([square], [adjacent]), 0)
        self.assertEqual(polygon_area([square, adjacent]), 8)
        # Concave L with an inner unit square touching its reflex vertex.
        l_shape = [(0, 0), (3, 0), (3, 1), (1, 1), (1, 3), (0, 3)]
        self.assertEqual(polygon_area([l_shape]), 5)
        self.assertEqual(polygon_intersection_area([l_shape], [square]), 1)


class RefusalTests(unittest.TestCase):
    def assert_mode(self, mode, call, execution=None):
        from agent_ee.land_geometry import GeometryError
        with self.assertRaises(GeometryError) as caught:
            call()
        self.assertEqual(caught.exception.mode, mode)
        if execution:
            self.assertEqual(caught.exception.execution, execution)

    def test_numeric_shape_and_rotation_refusals(self):
        from agent_ee.land_geometry import area_bounds
        for field in ('NaN', 'Infinity', '1e3', 1.5, True, None):
            with self.subTest(field=field):
                self.assert_mode('malformed_geometry', lambda: area_bounds(pattern(primitive(w=field))), 'malformed')
        for kwargs in ({'w': '0'}, {'w': '-1'}, {'shape': 'roundrect'},
                       {'shape': 'roundrect', 'radius': '2'}, {'radius': '0.2'}, {'unknown': 1}):
            with self.subTest(kwargs=kwargs):
                self.assert_mode('malformed_geometry', lambda: area_bounds(pattern(primitive(**kwargs))))
        self.assert_mode('unsupported_shape', lambda: area_bounds(pattern(primitive('ellipse'))))
        self.assert_mode('unsupported_shape', lambda: area_bounds(pattern(primitive('circle', w='3'))))
        for angle in ('0.000001', '89.999999', '45', '-0.001'):
            self.assert_mode('unsupported_rotation', lambda: area_bounds(pattern(primitive(angle=angle))))

    def test_work_limits_and_empty_copper(self):
        from agent_ee.land_geometry import Limits, area_bounds, compare_patterns, match_pins
        a = pattern(primitive('circle'))
        self.assert_mode('work_limit', lambda: area_bounds(a, refinement=2, limits=Limits(max_segments=4)))
        self.assert_mode('work_limit', lambda: area_bounds(a, limits=Limits(max_pair_checks=1)))
        self.assert_mode('work_limit', lambda: area_bounds(a, limits=Limits(max_slabs=1)))
        self.assert_mode('work_limit', lambda: area_bounds(a, limits=Limits(max_numeric_bits=2)))
        self.assert_mode('work_limit', lambda: area_bounds(pattern(primitive(), primitive()), limits=Limits(max_pads=1)))
        self.assert_mode('work_limit', lambda: match_pins(a, a, limits=Limits(max_match_states=1)))
        self.assert_mode('empty_copper', lambda: compare_patterns(pattern(), pattern()))
        empty = pattern(primitive(), holes=[primitive(w='4', h='4')])
        self.assert_mode('empty_copper', lambda: compare_patterns(empty, empty))
        self.assert_mode('malformed_geometry', lambda: compare_patterns(a, a, levels=(2, 3)))
        self.assert_mode('malformed_geometry', lambda: compare_patterns(a, a, threshold='1.1'))
        self.assert_mode('malformed_geometry', lambda: compare_patterns(a, a, pin_tolerance='-1'))

    def test_malformed_polygons_are_never_areas(self):
        for points in ([(0, 0), (1, 0)], [(0, 0), (1, 0), (2, 0)],
                       [(0, 0), (2, 2), (0, 2), (2, 0)],
                       [(0, 0), (3, 0), (1, 0), (1, 2), (0, 2)],
                       [(0, 0), (1, 0), (1, 1), (0, 0)]):
            self.assert_mode('malformed_geometry', lambda: polygon_area([points]))

    def test_ambiguous_conflict_never_emits_arbitrary_correspondence(self):
        from agent_ee.land_geometry import match_pins
        left, right = pattern(primitive(), primitive()), pattern(primitive(), primitive())
        right['pads'][0]['number'], right['pads'][1]['number'] = '3', '4'
        self.assertEqual(match_pins(left, right), {'mode': 'pin_conflict', 'pairs': []})

    def test_iou_ratio_cannot_exceed_numeric_budget(self):
        from agent_ee.land_geometry import compare_patterns, Limits
        a, b = pattern(primitive('circle')), pattern(primitive('circle', x='1'))
        # Individual sweep areas fit 67 bits; their ratio needs 70 bits.
        self.assert_mode('work_limit', lambda: compare_patterns(
            a, b, levels=(8,), limits=Limits(max_numeric_bits=67)))


class HighPrecisionControlTests(unittest.TestCase):
    def test_lens_against_independent_rational_analytic_enclosure(self):
        from agent_ee.land_geometry import compare_patterns
        from math import isqrt
        # Independent 50-place pi bracket and integer-square-root enclosure.
        # For radius1 and centre distance1, intersection = 2*pi/3 - sqrt(3)/2.
        pi_lo = F('3.14159265358979323846264338327950288419716939937510')
        pi_hi = pi_lo + F(1, 10**50)
        scale = 10**60
        sqrt_lo = F(isqrt(3*scale*scale), scale)
        sqrt_hi = sqrt_lo + F(1, scale)
        lens_lo, lens_hi = 2*pi_lo/3-sqrt_hi/2, 2*pi_hi/3-sqrt_lo/2
        oracle_lo = lens_lo/(2*pi_hi-lens_lo)
        oracle_hi = lens_hi/(2*pi_lo-lens_hi)
        a, b = pattern(primitive('circle')), pattern(primitive('circle', x='1'))
        result = compare_patterns(a, b, threshold='0.25', levels=(16,))['copper']['F.Cu']
        self.assertLess(number(result['lower']), oracle_lo)
        self.assertGreater(number(result['upper']), oracle_hi)
        self.assertLess(number(result['upper'])-number(result['lower']), F(1, 100))

    def test_input_records_are_borrowed_and_unchanged(self):
        from agent_ee.land_geometry import compare_patterns
        import copy
        a = pattern(primitive('roundrect', radius='0.2'), holes=[primitive('oval', w='1', h='0.5')])
        before = copy.deepcopy(a)
        compare_patterns(a, a)
        self.assertEqual(a, before)

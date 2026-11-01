"""Conductive ownership and complete geometry for contained standard vias."""
import unittest

from agent_ee.easyeda_footprint import SupplierError, parse_package
from tests.land_pattern_fixtures import component, pad


def via(x='4001', y='3002', *, diameter='2.4', radius='0.6', net='', ident='via-a', locked='0'):
    return '~'.join(('VIA', x, y, diameter, net, radius, ident, locked))


def package(extra, *, layer='1', rotation='0', tolerance='200'):
    source = [pad('RECT', 3985, 3000, 2, 2, '1', ident='lead'),
              pad('RECT', 4000, 3000, 16, 12, '9', layer=layer, rotation=rotation, ident='ep')]
    return parse_package(component('C900000001', source + extra), 'C900000001', tolerance)


class ContainedViaTests(unittest.TestCase):
    def test_through_copper_drill_and_derived_pin_custody_are_complete(self):
        for layer in ('1', '2'):
            with self.subTest(layer=layer):
                result = package([via()], layer=layer)
                self.assertEqual(result['pattern']['pads'][-1], dict(number='9', layers=['B.Cu', 'F.Cu'],
                    primitive=dict(shape='circle', center=['254000', '508000'], size=['609600', '609600'], angle='0')))
                self.assertEqual(result['pattern']['holes'], [dict(shape='circle', center=['254000', '508000'],
                    size=['304800', '304800'], angle='0')])
                self.assertEqual(result['hole_locators'], [dict(owner=2, plated=True, id='via-a')])
                locator = result['pad_locators'][-1]
                self.assertEqual(locator['raw'], via().split('~'))
                self.assertEqual(locator['pin_assignment'], dict(method='unique_rectangular_smd_containment',
                    pad_index=1, pad_id='ep'))
                self.assertEqual((result['inventory']['pads'], result['inventory']['holes'],
                                  result['inventory']['vias']), (3, 1, 1))

    def test_record_order_and_rotated_rectangles_do_not_change_ownership(self):
        source = [via(), pad('RECT', 4000, 3000, 12, 16, '9', rotation='90', ident='ep')]
        result = parse_package(component('C900000001', source), 'C900000001', '200')
        self.assertEqual([p['number'] for p in result['pattern']['pads']], ['9', '9'])
        self.assertEqual(result['pad_locators'][-1]['index'], 0)

    def test_containment_is_exact_and_tolerance_cannot_adopt_a_protruding_annulus(self):
        self.assertEqual(package([via(x='4006.8', y='3000')])['inventory']['vias'], 1)
        with self.assertRaisesRegex(SupplierError, 'contained'):
            package([via(x='4006.800001', y='3000')], tolerance='1000000')

    def test_ambiguous_connected_or_drilled_owner_and_colliding_vias_refuse(self):
        cases = {
            'duplicate owner': [pad('RECT', 4000, 3000, 16, 12, '9', ident='ep2'), via()],
            'other surface copper': [pad('RECT', 4001, 3002, 2, 2, '2', layer='2', ident='back'), via()],
            'owner touches another pin': [pad('RECT', 4008.5, 3000, 1, 1, '2', ident='edge'), via()],
            'owner has extra hole': ['HOLE~3998~3000~0.2~hole~0', via()],
            'via collision': [via(), via(x='4001.5', ident='via-b')],
            'duplicate via id': [via(), via(x='3997', ident='via-a')],
            'duplicate pad id': [via(ident='ep')],
            'free standing': [via(x='4100')],
        }
        for label, extra in cases.items():
            with self.subTest(label=label), self.assertRaises(SupplierError) as caught:
                package(extra)
            self.assertEqual(caught.exception.execution, 'unsupported')

    def test_unknown_fields_named_nets_and_malformed_dimensions_refuse(self):
        for raw in (via(net='GND'), via(locked='surprise'), via()+'~unknown', via().rsplit('~', 1)[0]):
            with self.subTest(raw=raw), self.assertRaises(SupplierError) as caught:
                package([raw])
            self.assertEqual(caught.exception.execution, 'unsupported')
        for raw in (via(x='1e3'), via(diameter='0'), via(radius='-1'), via(radius='1.2'),
                    via(radius='NaN'), via(ident='')):
            with self.subTest(raw=raw), self.assertRaises(SupplierError) as caught:
                package([raw])
            self.assertEqual(caught.exception.execution, 'malformed')

    def test_unqualified_owner_shapes_and_excessive_via_work_refuse(self):
        for shape in ('OVAL', 'ELLIPSE'):
            with self.subTest(shape=shape), self.assertRaisesRegex(SupplierError, 'contained'):
                parse_package(component('C900000001',
                    [pad(shape, 4000, 3000, 20, 20, '9'), via()]), 'C900000001', '200')
        with self.assertRaisesRegex(SupplierError, '64'):
            package([via(ident='v'+str(n)) for n in range(65)])

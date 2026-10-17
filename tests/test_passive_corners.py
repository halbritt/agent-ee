"""Owned RC expectations: written before native qualification runs."""
import copy
import unittest

from agent_ee import passive_corners
from test_checks import BASE_PARAMS, conn_fixture


class CornerContractTest(unittest.TestCase):
    def test_complete_endpoint_grid_and_native_values(self):
        params = {**copy.deepcopy(BASE_PARAMS), 'tolerances': {
            'R1': [-0.1, 0.1], 'C1': [-0.1, 0.1]}, 'max_corners': 4}
        plan = passive_corners.plan(conn_fixture(), params)
        self.assertEqual(len(plan['corners']), 4)
        for corner, expected in zip(plan['corners'], [0.00081, 0.00099, 0.00099, 0.00121]):
            self.assertAlmostEqual(corner['tau_s'], expected, places=15)

    def test_zero_tolerances_deduplicate(self):
        params = {**copy.deepcopy(BASE_PARAMS), 'tolerances': {
            'R1': [0, 0], 'C1': [0, 0]}, 'max_corners': 1}
        planned = passive_corners.plan(conn_fixture(), params)
        self.assertEqual(len(planned['corners']), 1)
        self.assertEqual(planned['corners'][0]['values'], planned['nominal']['values'])

    def test_unqualified_contracts_refuse(self):
        base = {**copy.deepcopy(BASE_PARAMS), 'tolerances': {
            'R1': [-0.1, 0.1], 'C1': [-0.1, 0.1]}, 'max_corners': 4}
        for override in ({'tolerances': {'R1': [0, 0]}}, {'max_corners': 3},
                         {'max_corners': 5}, {'step_v': -1}, {'stop_ms': 10000},
                         {'tolerances': {'R1': [-1, .1], 'C1': [0, 0]}},
                         {'tolerances': {'R1': [float('nan'), .1], 'C1': [0, 0]}},
                         {'correlation': 'positive'}, {'grid': [0, .1]},
                         {'measurements': [{'name': 'early', 'at_ms': .000001, 'max': 1}]}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                passive_corners.plan(conn_fixture(), {**base, **override})
        conn = conn_fixture()
        conn['nets']['/IN'].remove('R1.1')
        conn['nets']['GND'].append('R1.1')
        with self.assertRaisesRegex(ValueError, 'topology'):
            passive_corners.plan(conn, base)
        conn = conn_fixture()
        conn['components']['C1']['libsource']['part'] = 'L'
        with self.assertRaises(Exception):
            passive_corners.plan(conn, base)

"""Wire identity compatibility and malformed chain refusal without native tooling."""
import hashlib
import json
import unittest

from agent_ee.plane_polygon_identity import polygon_digest, polygon_nm


class PolygonIdentityTests(unittest.TestCase):
    def test_native_digest_contract_with_hole_and_float_round_trip(self):
        chains = [[[10000001, -10000003], [20000001, -10000003],
                   [20000001, 20000003], [10000001, 20000003]],
                  [[12000001, 12000001], [15000001, 12000001], [12000001, 15000001]]]
        mm = [[[x/1e6, y/1e6] for x, y in chain] for chain in chains]
        restored = polygon_nm(mm[0], mm[1:])
        self.assertEqual(restored, chains)
        expected = hashlib.sha256(json.dumps(chains).encode()).hexdigest()
        self.assertEqual(polygon_digest(restored), expected)
        self.assertNotEqual(polygon_digest(chains[:1]), expected)

    def test_native_fractured_hole_bridge_is_valid_without_rewriting_identity(self):
        chain = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 5],
                 [3, 5], [3, 7], [7, 7], [7, 3], [3, 3], [3, 5], [0, 5]]
        self.assertEqual(polygon_digest([chain]),
                         hashlib.sha256(json.dumps([chain]).encode()).hexdigest())

    def test_invalid_outline_and_hole_chains_refuse(self):
        good = [[0, 0], [10, 0], [10, 10], [0, 10]]
        invalid = [[], [[0, 0]], [[0, 0], [1, 1]],
                   [[0, 0], [1, 1], [2, 2]],
                   [[0, 0], [10, 0], [5, 0], [5, 5]],
                   [[0, 0], [10, 10], [0, 10], [10, 0]],
                   [[0, 0], [10, 0], [10, 0], [10, 10], [0, 10]],
                   [[0, 0], [True, 0], [1, 1]]]
        for chain in invalid:
            for chains in ([chain], [good, chain]):
                with self.subTest(chains=chains), self.assertRaises(ValueError):
                    polygon_digest(chains)

    def test_off_grid_and_nonfinite_coordinates_refuse(self):
        for value in (True, float('nan'), float('inf'), 0.0000001, 3000, 10**400):
            with self.subTest(value=value), self.assertRaises(ValueError):
                polygon_nm([[value, 0], [1, 0], [1, 1]], [])

"""AGENTEE-332: cheaper qualification and candidate transforms with identical answers.

Seeded differentials against in-test oracles (the pre-change pairwise, deepcopy
and full-scan formulations) and plan digests pinned from the ac662ff packer.
"""
import copy
import hashlib
import json
import os
import random
import unittest
from fractions import Fraction

from agent_ee import placement
from agent_ee.placement import area2, edges, intersection_area, intersects
from agent_ee.placement_predicates import segment_distance2
from agent_ee.placer import _fits, pack, transform
from test_placement import MM, capture, polygon, rectangle
from test_placer import request
from test_placer_copper_extent import copper_violations


def seeded_board(seed):
    """An owned random board: rectangles, netted pads, optional keepouts, geometry and locality."""
    rng = random.Random(seed)
    parts, nets = [], ['VCC', 'GND', 'SIG_A', 'SIG_B', 'SW']
    size = rng.randint(13, 20)
    for i in range(rng.randint(9, 16)):
        ref = 'P' + str(i)
        x, y = rng.randint(1, size-4), rng.randint(1, size-4)
        w, h = rng.randint(1, 4), rng.randint(1, 4)
        side = 'F' if rng.random() < .8 else 'B'
        fp = {'ref': ref, 'uuid': 'uuid-' + ref, 'value': '', 'side': side,
              'position_nm': [x*MM, y*MM], 'rotation_deg': rng.choice((0, 90, 180, 270)),
              'courtyards': {side: {'status': 'proven', 'polygons': [rectangle(x, y, w, h)]}}, 'pads': []}
        if w > 1 and h > 1 and rng.random() < .3:  # an L-shaped courtyard
            fp['courtyards'][side]['polygons'] = [polygon([(x, y), (x+w, y), (x+w, y+1), (x+1, y+1), (x+1, y+h), (x, y+h)])]
        for k in range(rng.randint(1, 3)):
            px, py = x*MM + rng.randint(0, w*MM), y*MM + rng.randint(0, h*MM)
            fp['pads'].append({'uuid': f'{ref}-{k+1}', 'number': str(k+1), 'net': rng.choice(nets),
                               'position_nm': [px, py], 'size_nm': [200000, 200000], 'shape': rng.choice((0, 1, 4)),
                               'attribute': 'smd', 'layers': [side + '.Cu'], 'rotation_deg': rng.choice((0, 90)),
                               'copper_centroid_offset_nm': [[rng.randint(-40, 40), rng.choice((1, 2, 5))],
                                                             [rng.randint(-40, 40), rng.choice((1, 3))]]})
        if rng.random() < .5:
            fp['packing_pad_geometry'] = {'status': 'proven', 'holes': [], 'copper': [
                {'layers': [side + '.Cu'], 'polygons': [[[p['position_nm'][0]-100000, p['position_nm'][1]-100000],
                                                         [p['position_nm'][0]+100000, p['position_nm'][1]-100000],
                                                         [p['position_nm'][0]+100000, p['position_nm'][1]+100000],
                                                         [p['position_nm'][0]-100000, p['position_nm'][1]+100000]]]}
                for p in fp['pads']]}
        parts.append(fp)
    board = capture(parts, rectangle(0, 0, size, size))
    board['pcbnew_version'] = '10.0.7'
    if rng.random() < .3:
        board['rule_areas'] = [{'uuid': 'zone', 'owner': 'board', 'layers': ['F.Cu'], 'forbid': {'footprints': True},
                                'outline': {'status': 'proven', 'polygons': [rectangle(6, 6, 4, 3)]}}]
    policy = request(step_budget=3000, gap_nm=rng.choice((0, 250000, 500000)),
                     fixed_refs=[parts[0]['ref']], priority_refs=[parts[-1]['ref']],
                     min_courtyard_edge_mm=rng.choice((0, 0.5, 1.0)))
    if rng.random() < .5 and len(parts) >= 3:
        ic, cap = parts[1], parts[2]
        ic['pads'][0]['net'] = cap['pads'][0]['net'] = 'VCC'
        if len(ic['pads']) > 1 and len(cap['pads']) > 1:
            ic['pads'][1]['net'] = cap['pads'][1]['net'] = 'GND'
            limit = rng.choice((5, 8, 12)) * MM
            text = f'Original seeded fixture author: supply and return limits are {limit} nm.'
            provenance = {'class': 'owner', 'basis': 'Authored seeded test requirement',
                          'decision_reference': {'path': 'fixture-decisions/geometry-cost.txt',
                              'sha256': hashlib.sha256(text.encode()).hexdigest(),
                              'locator': 'supply and return limits', 'quote': text,
                              'owner': 'Original fixture author'}}
            policy['electrical_locality'] = {'schema': 'agent-ee.electrical-locality/1',
                'source_records': {'fixture-decisions/geometry-cost.txt': text}, 'obligations': [{
                'id': 'o1', 'metric': 'pad-centre-to-centre',
                'supply': {'from': {'reference': ic['ref'], 'pad': '1', 'net': 'VCC'},
                           'to': {'reference': cap['ref'], 'pad': '1', 'net': 'VCC'},
                           'max_distance_nm': limit, 'citation': 'Owned seeded fixture limit.',
                           'provenance': copy.deepcopy(provenance)},
                'return': {'from': {'reference': ic['ref'], 'pad': '2', 'net': 'GND'},
                           'to': {'reference': cap['ref'], 'pad': '2', 'net': 'GND'},
                           'max_distance_nm': limit, 'citation': 'Owned seeded fixture limit.',
                           'provenance': copy.deepcopy(provenance)}}]}
    return board, policy


def plan_digest(result):
    keys = ('status', 'outcome', 'reason', 'steps_used', 'search', 'placements')
    return hashlib.sha256(json.dumps({k: result.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()[:16]


# Digests of pack(*seeded_board(seed)) computed with the ac662ff packer (before this change).
# Seeds in COPPER_GATED were re-pinned by AGENTEE-366 (coordinator ruling): their ac662ff
# plans put pad copper off the board or in the board F.Cu keep-out.
PINNED = {
    0: 'd4f80f98aa3a1a0c',
    1: '0d5aa4f15a941cc0',
    2: 'ae08e933892b1e88',
    3: 'fa3fa6268bd1e59b',
    4: '09b23360d8a95cc5',
    5: '3c46df6fd9305d1a',
    6: 'd313f75a3cfdbd56',
    7: '777a59037f3ffdc4',
    8: '2e85300b93402a02',
    9: 'e02185f7851d0996',
    10: 'f2ab3ca3e1cc1ce6',
    11: '1521230d33ab9e2e',
    12: '72e0f8277b1683e1',
    13: '6a29a8f2a7857d5c',
    14: 'c664064d0d37f301',
    15: '399a119463a05d4f',
    16: 'd978a0fa28d7e773',
    17: '0444d37ea7d6f653',
    18: '15a48b2fcd8ecd9d',
    19: '44c4df1469d1eb85',
    20: 'a60a301148bd0d50',
    21: '866791f48730197e',
    22: '95ccca70a8e1aab2',
    23: 'a4775eb9502b93d9',
    24: 'aa3fd93efc95a660',
    25: '8ca216b473944963',
    26: '1077b78a42c5b39a',
    27: '285efa2c09d1c809',
    28: '4948f9e467f6a828',
    29: '1f2c9f3828aa6f0f',
    30: '4da96b8a8d3c363e',
    31: '9f28a86c33bc69b6',
    32: '145f3a6c72c6fb23',
    33: '4cb17af59e0d87dd',
    34: '94634067d906f081',
    35: '3e1e3826b8410485',
    36: 'a8ca538f90429ba5',
    37: '29de9f60e3175c54',
    38: 'f15cdc5daa4d3922',
    39: '9bf5febe723a0055',
}
COPPER_GATED = {0, 3, 23, 38}


def oracle_transform(fp, position, rotation):
    """The pre-change formulation: deep copy, then move every point."""
    from agent_ee.placer import rotate, _cache_pad_reservations
    placed = copy.deepcopy(fp)
    origin = fp['position_nm']
    turns = int((rotation-fp['rotation_deg']) / 90)
    def move(p):
        x, y = rotate([p[0]-origin[0], p[1]-origin[1]], turns)
        return [x+position[0], y+position[1]]
    for courtyard in placed['courtyards'].values():
        for key in ('polygons', 'inner_polygons'):
            if key in courtyard:
                courtyard[key] = [[move(p) for p in poly] for poly in courtyard[key]]
    for keepout in placed.get('packing_keepouts', []):
        keepout['polygons'] = [[move(p) for p in poly] for poly in keepout['polygons']]
    for kind in ('copper', 'holes'):
        for entry in placed.get('packing_pad_geometry', {}).get(kind, []):
            entry['polygons'] = [[move(p) for p in poly] for poly in entry['polygons']]
    for pad in placed['pads']:
        custom = pad.get('custom_geometry', {})
        if custom.get('status') == 'proven':
            box = pad['custom_geometry']['bbox_nm']
            corners = [move([x, y]) for x in (box[0], box[2]) for y in (box[1], box[3])]
            pad['custom_geometry']['bbox_nm'] = [min(p[0] for p in corners), min(p[1] for p in corners),
                                                 max(p[0] for p in corners), max(p[1] for p in corners)]
        if 'copper_centroid_offset_nm' in pad:
            offset = rotate([Fraction(*v) for v in pad['copper_centroid_offset_nm']], turns)
            pad['copper_centroid_offset_nm'] = [[v.numerator, v.denominator] for v in offset]
        pad['position_nm'] = move(pad['position_nm'])
        pad['rotation_deg'] = (pad['rotation_deg'] + rotation-fp['rotation_deg']) % 360
    placed.update(position_nm=list(position), rotation_deg=rotation)
    _cache_pad_reservations(placed)
    return placed


def oracle_simple(points):
    """Pairwise self-intersection as before the sweep; the rest of simple_polygon is unchanged."""
    segments = list(edges(points))
    for i, (a, b) in enumerate(segments):
        for j in range(i+1, len(segments)):
            if j == i+1 or (i == 0 and j == len(segments)-1):
                continue
            if intersects(a, b, *segments[j]):
                return False
    return True


def oracle_fits(candidate, outline, margin_nm):
    for courtyard in candidate['courtyards'].values():
        for p in courtyard['polygons']:
            if intersection_area(p, outline) != Fraction(abs(area2(p)), 2):
                return False
            if margin_nm > 0 and min(segment_distance2(a, b, c, d)
                                     for a, b in edges(p) for c, d in edges(outline)) < margin_nm * margin_nm:
                return False
    return True


class TransformCopyTests(unittest.TestCase):
    def footprint(self, rng, custom=False):
        board, _ = seeded_board(rng.randint(0, 10**6))
        fp = rng.choice(board['footprints'])
        fp['packing_keepouts'] = [{'layers': ['F.Cu'], 'polygons': [rectangle(1, 1, 2, 1)]}]
        side = fp['side']
        fp['courtyards'][side]['inner_polygons'] = [rectangle(2, 2, 1, 1)]
        if custom and fp['pads']:
            fp['pads'][0]['shape'] = 6
            fp['pads'][0]['custom_geometry'] = {'status': 'proven', 'primitives': [{'kind': 'owned'}],
                                                'merged_polygons_nm': [rectangle(0, 0, 1, 1)],
                                                'bbox_nm': [0, 0, 300000, 700000]}
        return fp

    def test_transform_and_translation_equal_the_deepcopy_oracle(self):
        from agent_ee.placer import _translate
        rng = random.Random(332)
        cases = 0
        for case in range(120):
            fp = self.footprint(rng, custom=case % 3 == 0)
            before = copy.deepcopy(fp)
            for rotation in (0, 90, 180, 270):
                origin = transform(fp, [0, 0], rotation)
                for _ in range(2):
                    position = [rng.randint(-30*MM, 30*MM), rng.randint(-30*MM, 30*MM)]
                    expected = oracle_transform(fp, position, rotation)
                    self.assertEqual(transform(fp, position, rotation), expected)
                    self.assertEqual(_translate(origin, tuple(position)), expected)
                    cases += 1
            self.assertEqual(fp, before, 'transform must not mutate its source')
        self.assertGreater(cases, 900)

    def test_result_containers_are_independent_of_the_source(self):
        fp = self.footprint(random.Random(7), custom=True)
        before = copy.deepcopy(fp)
        placed = transform(fp, [3*MM, 4*MM], 90)
        placed['courtyards'][fp['side']]['polygons'][0][0][0] += 1
        placed['pads'][0]['position_nm'][0] += 1
        placed['pads'][0]['custom_geometry']['bbox_nm'][0] += 1
        placed['pads'][0]['rotation_deg'] += 90
        placed['packing_keepouts'][0]['polygons'][0][0][1] += 1
        placed['packing_pad_geometry'] = {'status': 'other'}
        placed['_packing_targets'].clear()
        self.assertEqual(fp, before)


class PolygonSweepTests(unittest.TestCase):
    def test_sweep_self_intersection_matches_pairwise_oracle(self):
        import math
        rng = random.Random(9332)
        outcomes = {True: 0, False: 0}
        for case in range(3000):
            k = rng.randint(3, 14)
            if case % 2:
                cx, cy = rng.randint(0, 100), rng.randint(0, 100)
                angles = sorted((rng.uniform(0, 2*math.pi), rng.randint(1, 40)) for _ in range(k))
                raw = [[cx + int(r*math.cos(t)), cy + int(r*math.sin(t))] for t, r in angles]
            else:
                raw = [[rng.randint(0, 12), rng.randint(0, 12)] for _ in range(k)]
            try:
                points = placement.simple_polygon(raw)
                simple = True
            except ValueError as exc:
                if str(exc) != 'polygon self-intersection':
                    continue
                simple = False
            outcomes[simple] += 1
            if simple:
                self.assertTrue(oracle_simple(points))
            else:
                reduced = [tuple(p) for p in raw]
                if reduced[0] == reduced[-1]:
                    reduced.pop()
                self.assertFalse(oracle_simple(reduced))
        self.assertGreater(outcomes[True], 500)
        self.assertGreater(outcomes[False], 500)

    def test_adjacent_edges_and_touching_contours_keep_their_verdicts(self):
        placement.simple_polygon([[0, 0], [4, 0], [4, 4], [0, 4]])
        with self.assertRaisesRegex(ValueError, 'self-intersection'):
            placement.simple_polygon([[0, 0], [4, 4], [4, 0], [0, 3]])  # a bow tie with nonzero signed area
        with self.assertRaisesRegex(ValueError, 'self-intersection'):  # a vertex touching a far edge
            placement.simple_polygon([[0, 0], [8, 0], [8, 8], [4, 0], [0, 8]])
        placement.simple_polygon([[0, 0], [8, 0], [8, 8], [4, 1], [0, 8]])  # the same vertex lifted off the edge


class FitsPrefilterTests(unittest.TestCase):
    def test_margin_prefilter_matches_the_full_edge_scan(self):
        rng = random.Random(5332)
        outlines = [rectangle(0, 0, 20, 20),
                    polygon([(0, 0), (20, 0), (20, 12), (12, 12), (12, 20), (0, 20)]),
                    polygon([(0, 0), (20, 0), (20, 20), (14, 20), (14, 6), (6, 6), (6, 20), (0, 20)])]
        verdicts = {True: 0, False: 0}
        for case in range(1500):
            outline = rng.choice(outlines)
            w, h = rng.randint(1, 5), rng.randint(1, 5)
            x, y = rng.randint(-1, 19), rng.randint(-1, 19)
            shape = rectangle(x, y, w, h) if case % 3 else polygon(
                [(x, y), (x+w, y), (x+w, y+h), (x+w-Fraction(1, 2), y+h), (x+w-Fraction(1, 2), y+Fraction(1, 2)), (x, y+Fraction(1, 2))])
            candidate = {'courtyards': {'F': {'polygons': [shape]}}}
            margin = rng.choice((0, 250000, 500000, 1500000))
            expected = oracle_fits(candidate, outline, margin)
            self.assertEqual(_fits(candidate, outline, margin), expected)
            verdicts[expected] += 1
        self.assertGreater(verdicts[True], 200)
        self.assertGreater(verdicts[False], 200)


class SeededPackDifferentialTests(unittest.TestCase):
    def test_advisory_scan_neither_prunes_axes_nor_adds_movable_peer_conflicts(self):
        from agent_ee.placer import _Search
        from test_electrical_locality import board, declaration
        facts, doc = board(), declaration(limit=1)
        doc.pop('source_records')
        for role in ('supply', 'return'):
            doc['obligations'][0][role].pop('provenance')
        hub, cap = facts['footprints']
        # Opposite sides exclude geometric blockers, isolating locality conflicts.
        hub['side'] = 'B'
        hub['courtyards'] = {'B': hub['courtyards']['F']}
        for pad in hub['pads']:
            pad['layers'] = ['B.Cu']
        search = _Search(facts, [], [], [],
                         placement.simple_polygon(facts['outline']['polygons'][0]),
                         0, request(electrical_locality=doc), {})
        search.push(hub)  # A movable peer, so a spurious conflict cannot be filtered as fixed.
        search.cluster_active, search.cluster_start_steps = True, 0
        conflicts = set()
        self.assertIsNotNone(next(search.scan(cap, conflicts), None))
        self.assertEqual(conflicts, set())

    def test_advisory_joint_cluster_matches_full_exact_ranking(self):
        from unittest.mock import patch
        from agent_ee import electrical_locality
        from test_placer_clusters import joint_pack, random_cluster_case
        board, policy = random_cluster_case(random.Random(1))
        doc = policy['electrical_locality']
        doc.pop('source_records')
        for obligation in doc['obligations']:
            for role in electrical_locality.roles(obligation):
                obligation[role].pop('provenance')
                obligation[role]['max_distance_nm'] = 1
        before = copy.deepcopy((board, policy))
        lazy = joint_pack(board, policy)
        self.assertEqual(lazy['status'], 'planned', lazy)
        self.assertEqual(lazy['locality_grading']['status'], 'info')
        self.assertTrue(lazy['locality_grading']['advisories'])
        self.assertTrue(all(a['exceeded'] for a in lazy['locality_grading']['advisories']))
        with patch('agent_ee.placer._ranked', lambda bounds, exact: iter(sorted(map(exact, bounds)))):
            eager = joint_pack(board, policy)
        self.assertEqual(lazy, eager)
        self.assertEqual((board, policy), before)

    def test_seeded_boards_pack_to_the_pinned_ac662ff_plans(self):
        statuses = set()
        for seed, pinned in PINNED.items():
            board, policy = seeded_board(seed)
            result = pack(board, policy)
            statuses.add(result['status'])
            with self.subTest(seed=seed, status=result['status']):
                self.assertEqual(plan_digest(result), pinned)
                if seed in COPPER_GATED:
                    self.assertEqual((result['status'], result['outcome']), ('planned', 'info'))
                    self.assertEqual(copper_violations(board, result['placements']), [])
        self.assertIn('planned', statuses)


class LazyRankingTests(unittest.TestCase):
    def test_lazy_order_equals_sorting_every_exact_key_and_resolves_a_prefix_only(self):
        from agent_ee.placer import _ranked
        rng = random.Random(1332)
        for case in range(300):
            exact = {}
            bounds = []
            for i in range(rng.randint(1, 60)):
                x, y, rotation = rng.randint(0, 5), rng.randint(0, 5), rng.choice((0, 90, 180, 270))
                if (x, y, rotation) in exact:
                    continue
                local = rng.choice((0, Fraction(1, 3), Fraction(2, 3), 1))
                wire = rng.randint(0, 40)
                key = (local, wire, x, y, rotation, rng.random() < .2)
                exact[(x, y, rotation)] = key
                bounds.append((local, wire - rng.randint(0, wire), *key[2:]))
            resolved = []
            def resolve(key):
                resolved.append(key)
                return exact[key[2:5]]
            ordered = list(_ranked(bounds, resolve))
            self.assertEqual(ordered, sorted(exact.values()))
            self.assertEqual(len(resolved), len(bounds))
            # Consuming only the first key resolves only entries whose bound precedes it.
            resolved.clear()
            first = next(iter(_ranked(bounds, resolve)))
            self.assertEqual(first, ordered[0])
            self.assertTrue(all(key <= first for key in resolved))

    def test_axis_bounds_never_exceed_the_exact_wire_cost(self):
        from agent_ee.placer import _pad_nets, _wire_bounds, _wire_cost
        rng = random.Random(2332)
        exact_hits = cases = 0
        for case in range(400):
            board, policy = seeded_board(rng.randint(0, 10**6))
            fps = board['footprints']
            peers = {}
            for fp in fps[:-1]:
                for pad in fp['pads']:
                    peers.setdefault(pad['net'], []).append(tuple(pad['position_nm']))
            fp, rotation = fps[-1], rng.choice((0, 90, 180, 270))
            xs = sorted({rng.randint(-5*MM, 25*MM) for _ in range(12)})
            ys = sorted({rng.randint(-5*MM, 25*MM) for _ in range(12)})
            bound_x, bound_y = _wire_bounds(_pad_nets(fp, (0, 0), rotation, policy['ground_nets']), peers, xs, ys)
            for x in xs:
                for y in ys:
                    cost = _wire_cost(_pad_nets(fp, (x, y), rotation, policy['ground_nets']), peers)
                    self.assertLessEqual(bound_x[x] + bound_y[y], cost)
                    exact_hits += bound_x[x] + bound_y[y] == cost
                    cases += 1
        self.assertGreater(exact_hits, 0)
        self.assertGreater(cases, 10000)


class OverlapTests(unittest.TestCase):
    def test_separating_edge_test_matches_clipping_for_convex_and_concave_pairs(self):
        import math
        from agent_ee.placer import _overlaps
        rng = random.Random(7332)
        verdicts = {True: 0, False: 0}
        convex_pairs = 0
        def shape():
            kind = rng.random()
            x, y = rng.randint(0, 10), rng.randint(0, 10)
            if kind < .4:
                return rectangle(x, y, rng.randint(1, 4), rng.randint(1, 4))
            if kind < .7:  # a 24-gon approximating a circle, like a native drill reservation
                r = rng.randint(1, 3)
                return [[x*MM + int(r*MM*math.cos(2*math.pi*k/24)), y*MM + int(r*MM*math.sin(2*math.pi*k/24))]
                        for k in range(24)]
            w, h = rng.randint(2, 4), rng.randint(2, 4)
            return polygon([(x, y), (x+w, y), (x+w, y+1), (x+1, y+1), (x+1, y+h), (x, y+h)])  # concave
        for case in range(3000):
            a, b = placement.simple_polygon(shape()), placement.simple_polygon(shape())
            if rng.random() < .3:  # shift to touch or barely overlap an edge of the first shape
                dx = rng.choice((-1, 0, 1))
                box_a, box_b = placement.box([a]), placement.box([b])
                b = [(p[0] + box_a[2] - box_b[0] + dx, p[1]) for p in b]
            expected = intersection_area(a, b) > 0
            self.assertEqual(_overlaps(a, b), expected, (a, b))
            self.assertEqual(_overlaps(b, a), expected)
            verdicts[expected] += 1
            convex_pairs += placement.convex(a) and placement.convex(b)
        self.assertGreater(verdicts[True], 300)
        self.assertGreater(verdicts[False], 300)
        self.assertGreater(convex_pairs, 1000)


def benchmarks_skipped(environ=os.environ):
    """AGENT_EE_SKIP_BENCHMARKS=1 applies only outside strict runs: AGENT_EE_REQUIRE_TOOLCHAIN=1 runs every benchmark."""
    return environ.get('AGENT_EE_SKIP_BENCHMARKS') == '1' and environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') != '1'


class BenchmarkSkipPolicyTests(unittest.TestCase):
    def test_strict_runs_never_skip_the_benchmark(self):
        self.assertFalse(benchmarks_skipped({}))
        self.assertTrue(benchmarks_skipped({'AGENT_EE_SKIP_BENCHMARKS': '1'}))
        self.assertFalse(benchmarks_skipped({'AGENT_EE_REQUIRE_TOOLCHAIN': '1'}))
        self.assertFalse(benchmarks_skipped({'AGENT_EE_SKIP_BENCHMARKS': '1', 'AGENT_EE_REQUIRE_TOOLCHAIN': '1'}))

    def test_both_flags_leave_the_benchmark_unskipped_at_import(self):
        import subprocess
        import sys
        probe = ('import test_placer_geometry_cost as t\n'
                 'm = t.PodScaleQualificationBenchmark.test_331_footprints_with_1986_native_96_gons_qualify_with_bounded_pair_tests\n'
                 'print(getattr(m, "__unittest_skip__", False), getattr(t.PodScaleQualificationBenchmark, "__unittest_skip__", False))')
        here = os.path.dirname(os.path.abspath(__file__))
        env = {**os.environ, 'AGENT_EE_SKIP_BENCHMARKS': '1', 'AGENT_EE_REQUIRE_TOOLCHAIN': '1',
               'PYTHONPATH': os.pathsep.join([os.path.join(here, '..', 'src'), here])}
        result = subprocess.run([sys.executable, '-c', probe], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.split(), ['False', 'False'])


class PodScaleQualificationBenchmark(unittest.TestCase):
    """Deterministic work bounds for qualification at pod scale, with a safety ceiling.

    AGENT_EE_SKIP_BENCHMARKS=1 skips it where CI time is short, except under
    AGENT_EE_REQUIRE_TOOLCHAIN=1; the bounds are counts, so a pass does not
    depend on the host's speed.
    """
    @unittest.skipIf(benchmarks_skipped(), 'AGENT_EE_SKIP_BENCHMARKS=1 outside a strict run')
    def test_331_footprints_with_1986_native_96_gons_qualify_with_bounded_pair_tests(self):
        import math
        import time
        from unittest.mock import patch
        from agent_ee.placer import _qualified
        from test_placement import part
        footprints = []
        for i in range(331):
            x, y = (i % 20) * 4, (i // 20) * 4
            fp = part('P' + str(i), x, y, shape=rectangle(x, y, 3, 3))
            fp['pads'], copper = [], []
            for k in range(6):
                cx, cy = x*MM + 500000 + (k % 3) * 1000000, y*MM + 700000 + (k // 3) * 1600000
                fp['pads'].append({'uuid': f'P{i}-{k}', 'number': str(k+1), 'net': 'N', 'position_nm': [cx, cy],
                                   'size_nm': [600000, 600000], 'shape': 4, 'attribute': 'smd', 'layers': ['F.Cu'],
                                   'rotation_deg': 0})
                copper.append({'layers': ['F.Cu'], 'polygons': [[
                    [cx + int(300000*math.cos(2*math.pi*t/96)), cy + int(300000*math.sin(2*math.pi*t/96))]
                    for t in range(96)]]})
            fp['packing_pad_geometry'] = {'status': 'proven', 'holes': [], 'copper': copper}
            footprints.append(fp)
        board = capture(footprints, rectangle(0, 0, 84, 72))
        board['pcbnew_version'] = '10.0.7'
        polygons, vertices = 331 * 6, 96
        started = time.monotonic()
        with patch.object(placement, 'intersects', wraps=placement.intersects) as pair_tests:
            outline = _qualified(copy.deepcopy(board))
        elapsed = time.monotonic() - started
        self.assertEqual(outline, [(0, 0), (84*MM, 0), (84*MM, 72*MM), (0, 72*MM)])
        # The pairwise formulation ran vertices*(vertices-3)/2 tests per polygon (8,865,504 here);
        # the sweep tests only pairs whose edge boxes overlap: none for a regular polygon.
        self.assertLess(pair_tests.call_count, polygons * vertices, pair_tests.call_count)
        self.assertLess(elapsed, 90.0, 'pod-scale qualification safety ceiling')


def clocked_board(seed):
    """Seven netted footprints, six fixed: every scan of the movable one ranks many candidates."""
    from test_placement import part
    rng = random.Random(seed)
    parts = []
    for i, (x, y) in enumerate([(2, 2), (2, 8), (8, 2), (8, 8), (12, 4), (4, 12), (0, 0)]):
        fp = part('P' + str(i), x, y, shape=rectangle(x, y, 1, 1))
        fp['pads'] = [{'uuid': 'pad' + str(i), 'number': '1', 'net': 'N',
                       'position_nm': [x*MM + rng.randrange(0, MM), y*MM + rng.randrange(0, MM)],
                       'size_nm': [100000, 100000], 'shape': 1, 'attribute': 'smd', 'layers': ['F.Cu'],
                       'rotation_deg': 0}]
        parts.append(fp)
    board = capture(parts)
    board['pcbnew_version'] = '10.0.7'
    return board


class LazyRankingCheckpointTests(unittest.TestCase):
    """Lazy exact ranking checks the deadline and heartbeat before every exact wire cost.

    A controlled clock charges one virtual second per exact wire-cost call and
    nothing for other work; the bounds, geometry and keys stay real. The base
    packer, which computed every exact key in the checked generation loop,
    stops after two calls at a two-second limit and beats at most every five.
    """
    def run_clocked(self, policy, heartbeat=None):
        from unittest.mock import patch
        from agent_ee import placer
        ticks, original = [0], placer._wire_cost
        def costly(*args):
            ticks[0] += 1
            return original(*args)
        events = []
        def beat(record):
            if record['status'] == 'packing':
                events.append(ticks[0])
        with patch.object(placer.time, 'monotonic', lambda: ticks[0]), patch.object(placer, '_wire_cost', costly):
            result = pack(clocked_board(353), policy, heartbeat=beat if heartbeat else None)
        return result, ticks[0], [0] + events + [ticks[0]]

    def test_deadline_expiring_during_lazy_resolution_stops_after_two_exact_costs(self):
        result, calls, _ = self.run_clocked(request(fixed_refs=['P' + str(i) for i in range(6)],
                                                    search_timeout_s=2, step_budget=300))
        self.assertEqual(result['status'], 'packing_budget_exhausted')
        self.assertEqual(result['steps_used'], 0)
        self.assertEqual(calls, 2)

    def test_heartbeat_gap_stays_at_five_seconds_while_resolving(self):
        result, _, events = self.run_clocked(request(fixed_refs=['P' + str(i) for i in range(6)],
                                                     search_timeout_s=1000, step_budget=300), heartbeat=True)
        self.assertEqual(result['status'], 'planned')
        self.assertLessEqual(max(b - a for a, b in zip(events, events[1:])), 5)


class IncompleteTargetInventoryTests(unittest.TestCase):
    """A proven hole inventory needs every peer's target inventory (AGENTEE-353 H3).

    U1 declares a hole reservation covering C1's courtyard but carries no drilled
    pad, and C1 has no native packing geometry. Missing exclusion evidence is
    typed unknown before search, never a plan.
    """
    def board(self):
        from test_placement import part
        board = capture([part('U1', 1, 1), part('C1', 8, 8)])
        board['footprints'][0]['packing_pad_geometry'] = {
            'status': 'proven', 'copper': [], 'holes': [{'layers': ['F.Cu'], 'polygons': [rectangle(8, 8, 2, 2)]}]}
        return board

    def assert_refused(self, board):
        result = pack(board, request(fixed_refs=['U1', 'C1'], gap_nm=0))
        self.assertEqual((result['status'], result['outcome'], result['placements']),
                         ('unsupported_geometry', 'unknown', []), result)
        self.assertIn('target reservations missing', result['reason'])

    def test_missing_target_inventory_refuses_before_search(self):
        self.assert_refused(self.board())

    def test_explicit_null_target_inventory_refuses(self):
        board = self.board()
        board['footprints'][1]['packing_pad_geometry'] = None
        self.assert_refused(board)

    def test_input_supplied_private_caches_are_not_evidence(self):
        board = self.board()
        board['footprints'][1].update({'_packing_targets': [], '_packing_targets_box': [0, 0, 1, 1]})
        self.assert_refused(board)

    def test_complete_inventories_still_exclude_the_hole(self):
        board = self.board()
        board['footprints'][1]['packing_pad_geometry'] = {'status': 'proven', 'copper': [], 'holes': []}
        result = pack(board, request(fixed_refs=['U1', 'C1'], gap_nm=0))
        self.assertNotEqual(result['status'], 'planned', result)
        self.assertEqual(result['placements'], [], result)

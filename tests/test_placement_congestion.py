"""AGENTEE-57 pre-route placement and congestion report over owned synthetic probe captures."""
import copy
import json
import math
from pathlib import Path
import random
import tempfile
import unittest

from agent_ee import placement_congestion as congestion
from agent_ee.baseline import compile_baseline
from agent_ee.placement_congestion import (BANDS, analyze, clearance, from_file, pad_shapes, parameters,
                                           severity, summary)

MM = 1_000_000
PARAMS = {'clearance_mm': 0.2, 'track_width_mm': 0.25, 'cell_mm': 2, 'routing_layers': ['F.Cu', 'B.Cu']}


def nm(value):
    return int(round(value * MM))


def rectangle(x0, y0, x1, y1):
    return [[nm(x0), nm(y0)], [nm(x1), nm(y0)], [nm(x1), nm(y1)], [nm(x0), nm(y1)]]


def pad(number, net, x, y, w, h, shape=1, rotation=0.0, layers=('F.Cu', 'F.Mask', 'F.Paste'), attribute='SMD'):
    return {'uuid': f'pad-{number}-{x}-{y}', 'number': str(number), 'net': net,
            'position_nm': [nm(x), nm(y)], 'shape': shape, 'size_nm': [nm(w), nm(h)],
            'rotation_deg': rotation, 'attribute': attribute, 'layers': sorted(layers)}


def footprint(ref, x, y, pads, courtyard=None, side='F', locked=False, rotation=0.0):
    record = {'ref': ref, 'uuid': f'uuid-{ref}', 'value': ref, 'fpid': 'Owned:Synthetic', 'side': side,
              'locked': locked, 'position_nm': [nm(x), nm(y)], 'rotation_deg': rotation, 'courtyards': {},
              'pads': sorted(pads, key=lambda p: (p['number'], p['uuid']))}
    if courtyard is not None:
        record['courtyards'][side] = {'status': 'proven', 'primitives': [], 'polygons': [rectangle(*courtyard)]}
    return record


def two_pad(ref, x, y, nets=('A', 'B'), pitch=1.9, size=(1.0, 1.45), shape=1, **kw):
    pads = [pad(1, nets[0], x - pitch/2, y, *size, shape=shape), pad(2, nets[1], x + pitch/2, y, *size, shape=shape)]
    half = pitch/2 + size[0]/2 + 0.25
    return footprint(ref, x, y, pads, (x - half, y - size[1]/2 - 0.25, x + half, y + size[1]/2 + 0.25), **kw)


def probe(footprints, outline=(0, 0, 30, 20)):
    return {'schema': 'agent-ee.placement-probe/1', 'board_sha256': 'a' * 64, 'pcbnew_version': '10.0.7',
            'outline': {'status': 'proven', 'primitives': [], 'polygons': [rectangle(*outline)]},
            'markings': [], 'rule_areas': [], 'footprints': sorted(footprints, key=lambda f: f['ref'])}


def shift(fp, dx=0.0, dy=0.0):
    out = copy.deepcopy(fp)
    vector = (nm(dx), nm(dy))
    out['position_nm'] = [out['position_nm'][0] + vector[0], out['position_nm'][1] + vector[1]]
    for p in out['pads']:
        p['position_nm'] = [p['position_nm'][0] + vector[0], p['position_nm'][1] + vector[1]]
    for record in out['courtyards'].values():
        record['polygons'] = [[[x + vector[0], y + vector[1]] for x, y in poly] for poly in record['polygons']]
    return out


def apply_move(capture, move):
    out = copy.deepcopy(capture)
    out['footprints'] = [shift(fp, move['dx_mm'], move['dy_mm']) if fp['ref'] == move['reference'] else fp
                         for fp in out['footprints']]
    return out


def overlapping():
    # C1 pad 1 (x 7.05..8.05) overlaps R1 pad 2 (x 7.45..8.45) by 0.6 mm on different nets.
    return probe([two_pad('R1', 7, 10, ('IN', 'MID')), two_pad('C1', 8.5, 10, ('OUT', 'GND'))])


class ClearanceGeometry(unittest.TestCase):
    def test_signed_distance_oracles(self):
        circle = pad(1, 'A', 0, 0, 1, 1, shape=congestion.CIRCLE)
        other = pad(2, 'B', 3, 4, 2, 2, shape=congestion.CIRCLE)
        # Centre distance 5 mm, radii 0.5 and 1 mm.
        self.assertAlmostEqual(clearance(pad_shapes(circle)[0], pad_shapes(other)[0]), 3.5 * MM, places=3)
        left, right = pad(1, 'A', 0, 0, 2, 1), pad(2, 'B', 1.5, 0, 2, 1)
        self.assertAlmostEqual(clearance(pad_shapes(left)[0], pad_shapes(right)[0]), -0.5 * MM, places=3)
        # A 1 mm square turned 45 degrees reaches sqrt(2)/2 mm along x.
        diamond = pad(1, 'A', 0, 0, 1, 1, rotation=45.0)
        wall = pad(2, 'B', 2, 0, 1, 4)
        self.assertAlmostEqual(clearance(pad_shapes(diamond)[0], pad_shapes(wall)[0]),
                               (1.5 - math.sqrt(2)/2) * MM, places=3)
        # Ovals: stadium radius is half the short side.
        oval = pad(1, 'A', 0, 0, 3, 1, shape=congestion.OVAL)
        dot = pad(2, 'B', 0, 1.5, 0.4, 0.4, shape=congestion.CIRCLE)
        self.assertAlmostEqual(clearance(pad_shapes(oval)[0], pad_shapes(dot)[0]), 0.8 * MM, places=3)

    def test_rounded_rectangle_is_bounded_not_guessed(self):
        outer, inner = pad_shapes(pad(1, 'A', 0, 0, 2, 1, shape=congestion.ROUNDRECT))
        corner = pad(2, 'B', 1.3, 0.8, 0.2, 0.2, shape=congestion.CIRCLE)
        low = clearance(outer, pad_shapes(corner)[0])
        high = clearance(inner, pad_shapes(corner)[0])
        self.assertLess(low, high)
        self.assertEqual(pad_shapes(pad(1, 'A', 0, 0, 1, 1, shape=congestion.CHAMFERED_RECT))[1], None)
        self.assertEqual(pad_shapes(pad(1, 'A', 0, 0, 1, 1, shape=congestion.CUSTOM)), (None, None))

    def test_blocked_interval_matches_bisection(self):
        rng = random.Random(57)
        for _ in range(200):
            a = pad(1, 'A', rng.uniform(-2, 2), rng.uniform(-2, 2), rng.uniform(.2, 2), rng.uniform(.2, 2),
                    shape=rng.choice([0, 1, 2]), rotation=rng.choice([0.0, 30.0, 90.0]))
            b = pad(2, 'B', rng.uniform(-2, 2), rng.uniform(-2, 2), rng.uniform(.2, 2), rng.uniform(.2, 2),
                    shape=rng.choice([0, 1, 2]), rotation=rng.choice([0.0, 45.0]))
            margin = rng.choice([0, 200000])
            for _, direction in congestion.DIRECTIONS:
                interval = congestion._blocked(pad_shapes(a)[0], pad_shapes(b)[0], margin, direction)

                def closer(t):
                    moved = pad_shapes(a, (direction[0] * t, direction[1] * t))[0]
                    return clearance(moved, pad_shapes(b)[0]) < margin
                for t in [k * 0.25 * MM for k in range(-40, 41)]:
                    expected = closer(t)
                    inside = interval is not None and interval[0] + 10 < t < interval[1] - 10
                    outside = interval is None or t < interval[0] - 10 or t > interval[1] + 10
                    if inside:
                        self.assertTrue(expected, (a, b, direction, t, interval))
                    elif outside:
                        self.assertFalse(expected, (a, b, direction, t, interval))


class Report(unittest.TestCase):
    def test_overlapping_pads_give_negative_clearance_and_a_verified_move(self):
        capture = overlapping()
        report = analyze(capture, PARAMS)
        self.assertEqual(report['status'], 'defect')
        issue = report['issues'][0]
        self.assertEqual((issue['rank'], issue['kind'], issue['class'], issue['refs']),
                         (1, 'pad_overlap', 'defect', ['C1', 'R1']))
        self.assertEqual(issue['clearance_mm'], -0.6)
        pair, = issue['pad_pairs']
        self.assertEqual(pair['pads'], [['C1', '1'], ['R1', '2']])
        self.assertEqual(pair['nets'], ['OUT', 'MID'])
        self.assertEqual(pair['required_mm'], 0.2)
        move = issue['suggested_move']
        self.assertEqual(move['op'], 'move')
        self.assertRegex(move['text'], r'^move (C1|R1) [0-9.]+ mm (up|down|left|right)$')
        self.assertEqual({p['ref'] for p in issue['parts']}, {'C1', 'R1'})
        for part in issue['parts']:
            self.assertEqual(set(part['free_space_mm']), {'status', 'up', 'down', 'left', 'right'})
        # Applying the move clears pads (with clearance) and courtyards.
        after = analyze(apply_move(capture, move), PARAMS)
        self.assertFalse([i for i in after['issues'] if i['kind'] in ('pad_overlap', 'pad_clearance',
                                                                       'courtyard_overlap')])
        # The move is minimal to the micrometre: one micrometre less still violates.
        short = dict(move, dx_mm=move['dx_mm'] - math.copysign(0.002, move['dx_mm']) if move['dx_mm'] else 0,
                     dy_mm=move['dy_mm'] - math.copysign(0.002, move['dy_mm']) if move['dy_mm'] else 0)
        self.assertTrue([i for i in analyze(apply_move(capture, short), PARAMS)['issues']
                         if i['refs'] == ['C1', 'R1']])
        # Courtyard separation (1.9 mm) dominates the 0.8 mm pad clearance move.
        self.assertEqual(move['distance_mm'], 1.9)
        self.assertEqual(move['text'], 'move C1 1.9 mm right')
        self.assertEqual((move['x_mm'], move['y_mm']), (10.4, 10.0))

    def test_courtyard_overlap_comes_from_the_exact_judge(self):
        report = analyze(overlapping(), PARAMS)
        courtyard, = [i for i in report['issues'] if i['kind'] == 'courtyard_overlap']
        self.assertEqual(courtyard['refs'], ['C1', 'R1'])
        self.assertGreater(courtyard['overlap_area_mm2'], 0)
        self.assertEqual([i['kind'] for i in report['issues']], ['pad_overlap', 'courtyard_overlap'])

    def test_clearance_deficit_is_advisory_and_same_net_contact_is_not(self):
        # 0.1 mm copper gap between different nets: below 0.2 mm, not an overlap.
        capture = probe([footprint('R1', 7, 10, [pad(2, 'MID', 7.95, 10, 1, 1.45)], (6, 9, 8.45, 11)),
                         footprint('C1', 9.5, 10, [pad(1, 'OUT', 9.05, 10, 1, 1.45)], (8.55, 9, 11, 11))])
        report = analyze(capture, PARAMS)
        issue, = report['issues']
        self.assertEqual((issue['kind'], issue['class'], issue['clearance_mm']), ('pad_clearance', 'advisory', 0.1))
        self.assertEqual(report['status'], 'info')
        for fp in capture['footprints']:
            fp['pads'][0]['net'] = 'SAME'
        self.assertEqual(analyze(capture, PARAMS)['issues'], [])

    def test_same_board_same_ordered_output(self):
        capture = probe([two_pad('R1', 7, 10, ('IN', 'MID')), two_pad('C1', 8.5, 10, ('OUT', 'GND')),
                         two_pad('R2', 7.5, 11, ('IN', 'OUT')), two_pad('C2', 25, 15, ('MID', 'GND'))])
        first = json.dumps(analyze(capture, PARAMS), sort_keys=True)
        self.assertEqual(first, json.dumps(analyze(copy.deepcopy(capture), PARAMS), sort_keys=True))
        shuffled = copy.deepcopy(capture)
        random.Random(1).shuffle(shuffled['footprints'])
        self.assertEqual(first, json.dumps(analyze(shuffled, PARAMS), sort_keys=True))
        ranks = [i['rank'] for i in json.loads(first)['issues']]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))

    def test_locked_parts_are_not_moved(self):
        capture = overlapping()
        capture['footprints'][0]['locked'] = True   # C1
        move = analyze(capture, PARAMS)['issues'][0]['suggested_move']
        self.assertEqual(move['reference'], 'R1')
        for fp in capture['footprints']:
            fp['locked'] = True
        issue = analyze(capture, PARAMS)['issues'][0]
        self.assertIsNone(issue['suggested_move'])
        self.assertIn('locked', issue['suggestion_unavailable'])

    def test_rotation_suggestion(self):
        # R1 is turned so that its IN pad faces away from J1.
        j1 = footprint('J1', 3, 10, [pad(1, 'IN', 3, 10, 1.7, 1.7)], (1.9, 8.9, 4.1, 11.1))
        r1 = two_pad('R1', 12, 10, ('OUT', 'IN'))
        c1 = footprint('C1', 21, 10, [pad(1, 'OUT', 21, 10, 1, 1.45)], (20.25, 9, 21.75, 11))
        capture = probe([j1, r1, c1])
        self.assertFalse([i for i in analyze(capture, PARAMS)['issues'] if i['kind'] == 'ratsnest_rotation'])
        issue, = [i for i in analyze(capture, {**PARAMS, 'min_rotation_gain_mm': 1})['issues']
                  if i['kind'] == 'ratsnest_rotation']
        self.assertEqual(issue['suggested_move'], {'op': 'rotate', 'reference': 'R1', 'delta_deg': 180,
                                                   'rotation_deg': 180, 'text': 'rotate R1 180°'})
        self.assertEqual(issue['manhattan_gain_mm'], 3.8)
        self.assertEqual(issue['class'], 'advisory')


def bus():
    """Six nets cross between two owned connector columns, reversed, past three resistors."""
    left = footprint('J1', 2, 10, [pad(n, f'N{n}', 2, 4.5 + n, 0.8, 0.8) for n in range(1, 7)], (1, 5, 3, 11.5))
    right = footprint('J2', 28, 10, [pad(n, f'N{7 - n}', 28, 4.5 + n, 0.8, 0.8) for n in range(1, 7)],
                      (27, 5, 29, 11.5))
    parts = [two_pad(f'R{k}', 15, 3 + 3 * k, (f'N{k}', f'N{k + 2}')) for k in range(1, 4)]
    return probe([left, right, *parts], outline=(0, 0, 30, 20))


class Congestion(unittest.TestCase):
    def test_region_lists_nearby_parts_in_order_and_bands_are_computed(self):
        params = PARAMS
        report = analyze(bus(), params)
        regions = report['congestion']['regions']
        self.assertTrue(regions)
        probabilities = [r['probability_of_failure'] for r in regions]
        self.assertEqual(probabilities, sorted(probabilities, reverse=True))
        top = regions[0]
        self.assertGreaterEqual(top['probability_of_failure'], BANDS[-1][1])
        for region in regions:
            self.assertNotIn('severity', json.dumps(region))
            self.assertNotIn('band', region)
            distances = [n['distance_mm'] for n in region['nearby']]
            self.assertEqual(distances, sorted(distances))
            order = [(n['distance_mm'], n['ref']) for n in region['nearby']]
            self.assertEqual(order, sorted(order))
            for entry in region['nearby']:
                self.assertIn(entry['free_space_mm']['status'], ('proven', 'unknown'))
        self.assertEqual(report['method']['severity_bands'], {'high': 0.05, 'medium': 0.02, 'low': 0.01})
        line = next(line for line in summary(report) if line.startswith(top['id']))
        self.assertIn(f"band={severity(top['probability_of_failure'])}", line)
        # The band follows the stored probability; nothing else carries it.
        edited = copy.deepcopy(report)
        edited['congestion']['regions'][0]['probability_of_failure'] = 0.015
        self.assertIn('band=low', next(line for line in summary(edited) if line.startswith(top['id'])))
        self.assertEqual(json.dumps(report, sort_keys=True), json.dumps(analyze(bus(), params), sort_keys=True))

    def test_relief_move_is_predicted_and_bounded_by_free_space(self):
        params = PARAMS
        for region in analyze(bus(), params)['congestion']['regions']:
            move = region['suggested_move']
            if move is None:
                self.assertTrue(region['suggestion_unavailable'])
                continue
            entry = next(n for n in region['nearby'] if n['ref'] == move['reference'])
            self.assertLessEqual(move['distance_mm'], entry['free_space_mm'][move['direction']])
            self.assertIn('region_probability_after', move)

    def test_severity_bands(self):
        self.assertEqual([severity(p) for p in (0.0, 0.0099, 0.01, 0.0199, 0.02, 0.0499, 0.05, 1.0)],
                         [None, None, 'low', 'low', 'medium', 'medium', 'high', 'high'])

    def test_declared_maximum_turns_a_region_into_a_routing_defect(self):
        params = PARAMS
        top = analyze(bus(), params)['congestion']['regions'][0]
        report = analyze(bus(), {**params, 'max_failure_probability': top['probability_of_failure'] / 2})
        defect = next(i for i in report['issues'] if i['kind'] == 'routing_congestion')
        self.assertEqual((defect['class'], defect['region']), ('defect', top['id']))
        self.assertEqual(report['status'], 'defect')
        quiet = analyze(bus(), {**params, 'max_failure_probability': min(0.999, top['probability_of_failure'] + 1e-6)})
        self.assertFalse([i for i in quiet['issues'] if i['kind'] == 'routing_congestion'])

    def test_an_uncongested_board_is_info_never_pass(self):
        capture = probe([two_pad('R1', 5, 10, ('A', 'B')), two_pad('R2', 20, 10, ('A', 'B'))])
        report = analyze(capture, PARAMS)
        self.assertEqual((report['status'], report['issues'], report['congestion']['regions']), ('info', [], []))


class UnknownCoverage(unittest.TestCase):
    def test_probe_without_outputs_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = from_file(Path(tmp) / 'placement-probe.json', PARAMS)
            self.assertEqual((missing['status'], missing['issues']), ('unknown', []))
            (Path(tmp) / 'bad.json').write_text('{not json')
            self.assertEqual(from_file(Path(tmp) / 'bad.json', PARAMS)['status'], 'unknown')
            (Path(tmp) / 'empty.json').write_text(json.dumps({**overlapping(), 'footprints': []}))
            self.assertEqual(from_file(Path(tmp) / 'empty.json', PARAMS)['status'], 'unknown')
            out = Path(tmp) / 'out.json'
            self.assertEqual(congestion.main([str(Path(tmp) / 'absent.json'), '--params', json.dumps(PARAMS),
                                              '--out', str(out)]), 2)
            self.assertEqual(json.loads(out.read_text())['status'], 'unknown')
        for broken in ({'schema': 'other'}, {**overlapping(), 'board_sha256': ''}):
            with self.assertRaises(ValueError):
                analyze(broken, PARAMS)
        capture = overlapping()
        del capture['footprints'][0]['pads'][0]['size_nm']
        with self.assertRaisesRegex(ValueError, 'pad record'):
            analyze(capture, PARAMS)

    def test_unproved_geometry_is_unknown_not_clean(self):
        capture = overlapping()
        capture['outline'] = {'status': 'outline_missing', 'primitives': []}
        report = analyze(capture, PARAMS)
        self.assertIsNone(report['congestion'])
        self.assertIn('outline', [u['kind'] for u in report['unknown']])
        issue = report['issues'][0]
        self.assertEqual(issue['kind'], 'pad_overlap')
        self.assertIsNone(issue['suggested_move'])
        self.assertEqual(issue['parts'][0]['free_space_mm']['status'], 'unknown')
        capture = overlapping()
        capture['footprints'][0]['pads'][0]['shape'] = congestion.CUSTOM
        capture['footprints'][1]['courtyards'] = {}
        report = analyze(capture, PARAMS)
        kinds = [u['kind'] for u in report['unknown']]
        self.assertIn('pad_shape_unsupported', kinds)
        self.assertIn('courtyard', kinds)
        self.assertNotEqual(report['status'], 'pass')

    def test_rounded_pads_straddling_the_requirement_are_unresolved(self):
        # Square corners 0.141 mm apart; inscribed circles 0.5556 mm apart; 0.2 mm required.
        capture = probe([footprint('R1', 5, 10, [pad(1, 'A', 5, 10, 1, 1, shape=congestion.ROUNDRECT)],
                                   (4.4, 9.4, 5.55, 10.55)),
                         footprint('C1', 6.1, 11.1, [pad(1, 'B', 6.1, 11.1, 1, 1, shape=congestion.ROUNDRECT)],
                                   (5.55, 10.55, 6.7, 11.7))])
        report = analyze(capture, PARAMS)
        self.assertFalse([i for i in report['issues'] if i['kind'] in ('pad_overlap', 'pad_clearance')])
        unresolved, = [u for u in report['unknown'] if u['kind'] == 'pad_clearance_unresolved']
        low, high = unresolved['clearance_bounds_mm']
        self.assertEqual((low, high), (0.141421, 0.555635))
        self.assertEqual(report['status'], 'info')


class Parameters(unittest.TestCase):
    def test_declarations(self):
        self.assertEqual(parameters(PARAMS)['routing_layers'], ['B.Cu', 'F.Cu'])
        for change in ({'cell_mm': 0}, {'cell_mm': 1}, {'routing_layers': []}, {'routing_layers': ['F.Cu', 'F.Cu']},
                       {'routing_layers': ['F.SilkS']}, {'clearance_mm': True}, {'track_width_mm': '0.0000001'},
                       {'max_failure_probability': 1}, {'max_failure_probability': 0}, {'extra': 1},
                       {'min_rotation_gain_mm': -1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parameters({**PARAMS, **change})
        for key in ('clearance_mm', 'track_width_mm', 'cell_mm', 'routing_layers'):
            with self.subTest(missing=key), self.assertRaises(ValueError):
                parameters({k: v for k, v in PARAMS.items() if k != key})

    def test_capability_is_advisory_and_cannot_discharge_acceptance(self):
        files = dict.fromkeys(['design.sch', 'design.pcb', 'req'], 'hash')
        scope = {'schema': 'agent-ee.scope/3', 'schematic': 'design.sch', 'board': 'design.pcb',
                 'checks': [{'id': 'drc', 'kind': 'drc'},
                            {'id': 'congestion', 'kind': 'placement_congestion', 'params': PARAMS}],
                 'baseline': {'requirements': [
                     {'id': 'R1', 'intent': 'acceptance', 'statement': 'DRC clean',
                      'source': {'path': 'req', 'locator': 'R1'}, 'checks': ['drc']},
                     {'id': 'I1', 'intent': 'informational', 'statement': 'Pre-route congestion report',
                      'source': {'path': 'req', 'locator': 'I1'}, 'checks': ['congestion']}],
                     'targets': ['req'], 'reference_architectures': ['req']}}
        coverage = compile_baseline(scope, files)['coverage']['checks']['congestion']
        self.assertEqual(coverage['predicates'], [])
        self.assertFalse(coverage['can_evaluate_bound'])
        scope['baseline']['requirements'][1]['intent'] = 'acceptance'
        with self.assertRaisesRegex(ValueError, 'no executable bound'):
            compile_baseline(scope, files)
        scope['checks'][1]['params'] = {**PARAMS, 'cell_mm': 0.5}
        with self.assertRaisesRegex(ValueError, 'unsupported capability'):
            compile_baseline(scope, files)


if __name__ == '__main__':
    unittest.main()

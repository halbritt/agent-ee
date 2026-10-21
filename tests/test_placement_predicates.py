"""Placement predicates: native authority, exact boundaries and unknown coverage.

Owned integer-nanometre examples. DRC records follow the kicad-cli 10.0.7 JSON
shape observed in the native tests; they exercise interpretation only.
"""
import copy
from decimal import Decimal
import hashlib
from itertools import combinations
import json
import math
import unittest

from agent_ee import placement_predicates as p
from tests.test_placer import authored_source
from agent_ee.placement_check import parameters, predicate_names

MM = 1_000_000


def rect(x, y, w, h):
    return [[int(x*MM), int(y*MM)], [int((x+w)*MM), int(y*MM)], [int((x+w)*MM), int((y+h)*MM)],
            [int(x*MM), int((y+h)*MM)]]


def part(ref, x, y, side='F', courtyard=True, w=2, h=2, pads=(), rotation=0):
    fp = {'ref': ref, 'uuid': 'u-' + ref, 'value': '', 'side': side, 'locked': False,
          'position_nm': [int(x*MM), int(y*MM)], 'rotation_deg': rotation, 'courtyards': {}, 'pads': []}
    if courtyard:
        fp['courtyards'][side] = {'status': 'proven', 'polygons': [rect(x - w/2, y - h/2, w, h)]}
    for i, (net, px, py) in enumerate(pads):
        fp['pads'].append({'uuid': f'{ref}-p{i}', 'number': str(i + 1), 'net': net,
                           'position_nm': [int(px*MM), int(py*MM)]})
    return fp


def probe(parts, outline=None, areas=()):
    return {'schema': 'agent-ee.placement-probe/1', 'board_sha256': 'a'*64, 'pcbnew_version': '10.0.7',
            'outline': {'status': 'proven', 'polygons': [outline or rect(0, 0, 20, 20)]},
            'footprints': parts, 'rule_areas': list(areas)}


def drc(*violations, ignored=()):
    return {'kicad_version': '10.0.7', 'source': 'board.kicad_pcb', 'coordinate_units': 'mm',
            'included_severities': ['error', 'warning', 'exclusion'],
            'ignored_checks': [{'key': k, 'description': k} for k in ignored],
            'violations': list(violations), 'unconnected_items': [], 'schematic_parity': []}


def item(kind, *refs, severity='error'):
    return {'type': kind, 'severity': severity, 'description': kind,
            'items': [{'uuid': 'u-' + r, 'description': 'Footprint ' + r, 'pos': {'x': 1, 'y': 1}} for r in refs]}


def area(uuid, x, y, w, h, layers=('F.Cu',), footprints=True, owner='board', status='proven'):
    outline = {'status': status, 'polygons': [rect(x, y, w, h)]} if status == 'proven' else {
        'status': status, 'reason': 'holes'}
    return {'uuid': uuid, 'name': uuid, 'owner': owner, 'layers': list(layers), 'placement_area': False,
            'forbid': {'footprints': footprints, 'tracks': not footprints, 'vias': False, 'pads': False,
                       'zone_fills': False}, 'outline': outline}


class DrcShapeTest(unittest.TestCase):
    def test_report_requires_all_severities_lists_and_item_uuids(self):
        p.validate_drc(drc())
        for broken in ({'included_severities': ['error']}, {'ignored_checks': None},
                       {'violations': [{'type': 'x', 'items': [{'description': 'no uuid'}]}]}):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                p.validate_drc({**drc(), **broken})


class CourtyardOverlapTest(unittest.TestCase):
    def test_native_item_is_the_defect_and_cites_uuids(self):
        facts = probe([part('R1', 5, 5), part('C1', 6, 5)])
        native = item('courtyards_overlap', 'C1', 'R1')
        result = p.courtyard_overlap(facts, drc(native))
        self.assertEqual(result['status'], 'defect')
        self.assertEqual(result['defects'][0]['refs'], ['C1', 'R1'])
        self.assertEqual([i['uuid'] for i in result['defects'][0]['native']['items']], ['u-C1', 'u-R1'])

    def test_separated_control_passes_and_excluded_native_overlap_still_counts(self):
        facts = probe([part('R1', 5, 5), part('C1', 10, 5)])
        self.assertEqual(p.courtyard_overlap(facts, drc())['status'], 'pass')
        excluded = item('courtyards_overlap', 'C1', 'R1', severity='exclusion')
        self.assertEqual(p.courtyard_overlap(facts, drc(excluded))['status'], 'defect')

    def test_missing_courtyard_is_unknown_not_a_defect(self):
        result = p.courtyard_overlap(probe([part('R1', 5, 5), part('C1', 10, 5, courtyard=False)]), drc())
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['unknown'][0], {'kind': 'courtyard_missing', 'ref': 'C1', 'uuid': 'u-C1',
                                                'reason': 'courtyard missing on mounting side'})
        self.assertEqual(result['defects'], [])

    def test_courtyard_only_on_opposite_side_is_missing(self):
        fp = part('C1', 10, 5)
        fp['courtyards'] = {'B': fp['courtyards']['F']}
        self.assertEqual(p.courtyard_overlap(probe([part('R1', 5, 5), fp]), drc())['status'], 'unknown')

    def test_native_malformed_and_ignored_checks_leave_coverage_unknown(self):
        facts = probe([part('R1', 5, 5), part('C1', 10, 5)])
        malformed = p.courtyard_overlap(facts, drc(item('malformed_courtyard', 'C1')))
        self.assertEqual((malformed['status'], malformed['unknown'][0]['ref']), ('unknown', 'C1'))
        for check in ('courtyards_overlap', 'malformed_courtyard'):
            self.assertEqual(p.courtyard_overlap(facts, drc(ignored=[check]))['status'], 'unknown')

    def test_helper_overlap_without_native_item_is_unknown_never_manufactured(self):
        result = p.courtyard_overlap(probe([part('R1', 5, 5), part('C1', 6, 5)]), drc())
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['unknown'][0]['kind'], 'helper_disagreement')
        self.assertEqual(result['defects'], [])

    def test_proved_native_defect_survives_unrelated_unknown(self):
        facts = probe([part('R1', 5, 5), part('C1', 6, 5), part('U1', 15, 15, courtyard=False)])
        result = p.courtyard_overlap(facts, drc(item('courtyards_overlap', 'C1', 'R1')))
        self.assertEqual(result['status'], 'defect')
        self.assertEqual([u['ref'] for u in result['unknown']], ['U1'])


class ContainmentTest(unittest.TestCase):
    def test_outside_is_defect_open_or_missing_outline_unknown(self):
        self.assertEqual(p.outline_containment(probe([part('R1', 5, 5)]))['status'], 'pass')
        outside = p.outline_containment(probe([part('R1', 20, 5)]))
        self.assertEqual((outside['status'], outside['defects'][0]['outside_area_mm2']), ('defect', 2))
        for status in ('outline_missing', 'outline_unproven'):
            facts = probe([part('R1', 20, 5)])
            facts['outline'] = {'status': status, 'primitives': []}
            self.assertEqual(p.outline_containment(facts)['status'], 'unknown')

    def test_containment_ignores_overlap_owned_by_courtyard_predicate(self):
        self.assertEqual(p.outline_containment(probe([part('R1', 5, 5), part('C1', 6, 5)]))['status'], 'pass')


class EdgeClearanceTest(unittest.TestCase):
    def test_exact_minimum_passes_one_nanometre_less_fails(self):
        # Courtyard right edge at 19 mm; outline edge at 20 mm.
        facts = probe([part('R1', 18, 10)])
        self.assertEqual(p.edge_clearance(facts, MM)['status'], 'pass')
        result = p.edge_clearance(facts, MM + 1, {'min_courtyard_edge_mm':
            authored_source('tests/test_placement_predicates.py: authored 1 mm plus 1 nm edge minimum', MM + 1)})
        self.assertEqual(result['status'], 'defect')
        self.assertEqual((result['defects'][0]['ref'], result['defects'][0]['distance_mm']), ('R1', 1.0))

    def test_crossing_or_outside_courtyard_is_a_clearance_defect(self):
        for x in (19.5, 25):
            result = p.edge_clearance(probe([part('R1', x, 10)]), MM // 10)
            self.assertEqual(result['status'], 'defect')
            self.assertFalse(result['defects'][0]['inside_outline'])

    def test_unproven_outline_and_courtyard_are_unknown(self):
        facts = probe([part('R1', 10, 10)])
        facts['outline'] = {'status': 'outline_unproven', 'primitives': []}
        self.assertEqual(p.edge_clearance(facts, MM)['status'], 'unknown')
        facts = probe([part('R1', 10, 10), part('C1', 15, 15)])
        facts['footprints'][1]['courtyards']['F'] = {'status': 'courtyard_unproven', 'primitives': []}
        result = p.edge_clearance(facts, MM)
        self.assertEqual((result['status'], result['unknown'][0]['ref']), ('unknown', 'C1'))

    def test_concave_outline_uses_every_edge_not_bounding_box(self):
        notch = [[0, 0], [20*MM, 0], [20*MM, 20*MM], [12*MM, 20*MM], [12*MM, 8*MM], [8*MM, 8*MM],
                 [8*MM, 20*MM], [0, 20*MM]]
        # Bounding-box clearance would be 4.5 mm; the notch edge at x=8 is 1.5 mm away.
        result = p.edge_clearance(probe([part('R1', 5.5, 12)], notch), 3 * MM, {'min_courtyard_edge_mm':
            authored_source('tests/test_placement_predicates.py: authored concave edge minimum', 3 * MM)})
        self.assertEqual(result['status'], 'defect')
        self.assertEqual(result['defects'][0]['distance_mm'], 1.5)


class KeepoutTest(unittest.TestCase):
    def test_native_footprint_item_is_defect_with_helper_corroboration(self):
        facts = probe([part('R1', 5, 5)], areas=[area('K1', 4, 4, 3, 3)])
        result = p.footprint_keepout(facts, drc(item('items_not_allowed', 'R1')))
        self.assertEqual(result['status'], 'defect')
        self.assertEqual((result['defects'][0]['ref'], result['defects'][0]['helper_areas']), ('R1', ['K1']))

    def test_copper_only_opposite_side_and_touching_areas_pass(self):
        for keepout in (area('K1', 4, 4, 3, 3, footprints=False), area('K1', 4, 4, 3, 3, layers=('B.Cu',)),
                        area('K1', 6, 4, 3, 3)):
            with self.subTest(keepout=keepout['layers']):
                self.assertEqual(p.footprint_keepout(probe([part('R1', 5, 5)], areas=[keepout]), drc())['status'],
                                 'pass')

    def test_helper_hit_without_native_item_is_unknown(self):
        result = p.footprint_keepout(probe([part('R1', 5, 5)], areas=[area('K1', 4, 4, 3, 3)]), drc())
        self.assertEqual((result['status'], result['unknown'][0]['kind']), ('unknown', 'helper_disagreement'))

    def test_unsupported_area_owned_area_missing_courtyard_or_ignored_check_unknown(self):
        cases = [(probe([part('R1', 5, 5)], areas=[area('K1', 10, 10, 2, 2, status='unsupported')]), drc()),
                 (probe([part('R1', 5, 5)], areas=[area('K1', 10, 10, 2, 2, owner='u-R1')]), drc()),
                 (probe([part('R1', 5, 5, courtyard=False)], areas=[area('K1', 10, 10, 2, 2)]), drc()),
                 (probe([part('R1', 5, 5)]), drc(ignored=['items_not_allowed']))]
        for facts, report in cases:
            self.assertEqual(p.footprint_keepout(facts, report)['status'], 'unknown')

    def test_missing_courtyard_without_applicable_area_is_not_unknown(self):
        facts = probe([part('R1', 5, 5, courtyard=False)], areas=[area('K1', 10, 10, 2, 2, layers=('B.Cu',))])
        self.assertEqual(p.footprint_keepout(facts, drc())['status'], 'pass')

    def test_inventory_is_required(self):
        facts = probe([part('R1', 5, 5)])
        del facts['rule_areas']
        with self.assertRaisesRegex(ValueError, 'rule-area inventory'):
            p.footprint_keepout(facts, drc())


def validation(**fixed):
    """An agent-ee.envelope-validation/1 record in AGENTEE-85's e08673c shape."""
    record = {'reference': 'J1', 'side': 'front', 'layer': 'F.Cu', 'edge': 'left', 'position_mm': '8',
              'inset_mm': '4', 'anchor_mm': ['100', '100'], 'rotation_deg': '0', 'insertion': 'from_top',
              'mating_axis': 'z-', 'tolerance_mm': '0'}
    record.update(fixed)
    return {'schema': 'agent-ee.envelope-validation/1', 'envelope_id': 'rc-filter', 'envelope_sha256': 'e'*64,
            'outline': {'edges': [
                {'name': 'top', 'start_mm': ['96', '96'], 'end_mm': ['116', '96'], 'axis_aligned': True,
                 'length_mm': '20', 'outward': 'y-'},
                {'name': 'right', 'start_mm': ['116', '96'], 'end_mm': ['116', '108'], 'axis_aligned': True,
                 'length_mm': '12', 'outward': 'x+'},
                {'name': 'bottom', 'start_mm': ['116', '108'], 'end_mm': ['96', '108'], 'axis_aligned': True,
                 'length_mm': '20', 'outward': 'y+'},
                {'name': 'left', 'start_mm': ['96', '108'], 'end_mm': ['96', '96'], 'axis_aligned': True,
                 'length_mm': '12', 'outward': 'x-'}]},
            'fixed_footprints': [record]}


class ConnectorEdgeTest(unittest.TestCase):
    def facts(self, x=100, y=100, side='F', rotation=0, outline=None):
        return probe([part('J1', x, y, side=side, rotation=rotation), part('R1', 105, 100)],
                     outline or rect(96, 96, 20, 12))

    def test_declared_header_at_anchor_passes(self):
        declarations = p.envelope_declarations(validation())
        self.assertEqual(declarations[0]['anchor'], (100*MM, 100*MM))
        self.assertEqual(declarations[0]['edge']['outward'], 'x-')
        result = p.connector_edge(self.facts(), declarations)
        self.assertEqual(result['status'], 'pass', result)

    def test_moving_header_off_edge_names_edge_and_distance(self):
        result = p.connector_edge(self.facts(x=110), p.envelope_declarations(validation()))
        self.assertEqual(result['status'], 'defect')
        violation = result['defects'][0]['violations'][0]
        self.assertEqual((violation['kind'], violation['edge'], violation['distance_mm']),
                         ('off_declared_edge_position', 'left', 10.0))
        self.assertEqual((violation['inset_mm'], violation['declared_inset_mm']), (14.0, 4.0))

    def test_tolerance_boundary_is_exact(self):
        declarations = p.envelope_declarations(validation(tolerance_mm='0.5'))
        self.assertEqual(p.connector_edge(self.facts(x=100.5), declarations)['status'], 'pass')
        self.assertEqual(p.connector_edge(self.facts(x=100.500001), declarations)['status'], 'defect')

    def test_side_rotation_and_insertion_side_violations(self):
        declarations = p.envelope_declarations(validation())
        flipped = p.connector_edge(self.facts(side='B'), declarations)['defects'][0]['violations']
        self.assertEqual({v['kind'] for v in flipped}, {'side', 'insertion_side'})
        turned = p.connector_edge(self.facts(rotation=-90.0), declarations)['defects'][0]['violations']
        self.assertEqual(turned, [{'kind': 'rotation', 'declared_deg': '0', 'actual_deg': '270'}])

    def test_missing_duplicate_reference_and_unproven_outline_are_unknown(self):
        declarations = p.envelope_declarations(validation())
        missing = probe([part('R1', 105, 100)], rect(96, 96, 20, 12))
        self.assertEqual(p.connector_edge(missing, declarations)['status'], 'unknown')
        duplicate = self.facts()
        duplicate['footprints'].append({**copy.deepcopy(duplicate['footprints'][0]), 'uuid': 'other'})
        self.assertEqual(p.connector_edge(duplicate, declarations)['status'], 'unknown')
        unproven = self.facts()
        unproven['outline'] = {'status': 'outline_unproven', 'primitives': []}
        self.assertEqual(p.connector_edge(unproven, declarations)['status'], 'unknown')
        unproven['footprints'][0]['position_nm'] = [110*MM, 100*MM]
        self.assertEqual(p.connector_edge(unproven, declarations)['status'], 'defect')

    def test_declared_edge_absent_from_saved_outline_is_unknown(self):
        result = p.connector_edge(self.facts(outline=rect(97, 96, 19, 12)), p.envelope_declarations(validation()))
        self.assertEqual(result['status'], 'unknown')
        self.assertIn('not part of the saved outline', result['unknown'][0]['reasons'][0])

    def test_through_edge_facing_uses_declared_local_axis_and_native_transform(self):
        declaration = p.envelope_declarations(validation(insertion='through_edge'))[0]
        declaration.update(rotation_deg=None, local_mating_axis='x-')
        self.assertEqual(p.connector_access(declaration, self.facts()['footprints'],
                                            self.facts()['outline'])['status'], 'pass')
        inward = p.connector_access(declaration, self.facts(rotation=180.0)['footprints'], self.facts()['outline'])
        self.assertEqual(inward['violations'], [{'kind': 'facing', 'faces': 'x+', 'required': 'x-', 'edge': 'left'}])
        oblique = p.connector_access(declaration, self.facts(rotation=45.0)['footprints'], self.facts()['outline'])
        self.assertEqual(oblique['status'], 'unknown')
        declaration['local_mating_axis'] = None
        undetermined = p.connector_access(declaration, self.facts()['footprints'], self.facts()['outline'])
        self.assertEqual(undetermined['status'], 'unknown')
        declaration['insertion'] = None
        self.assertIn('insertion direction undeclared',
                      p.connector_access(declaration, self.facts()['footprints'], self.facts()['outline'])['reasons'])

    def test_back_side_transform_mirrors_before_rotation(self):
        self.assertEqual(p.board_axis('y+', Decimal(0), 'B'), 'y-')
        self.assertEqual(p.board_axis('y+', Decimal(90), 'B'), 'x-')
        self.assertEqual(p.board_axis('x+', Decimal(90), 'F'), 'y-')
        self.assertIsNone(p.board_axis('x+', Decimal('45'), 'F'))

    def test_no_declarations_is_not_a_vacuous_pass(self):
        record = validation()
        record['fixed_footprints'] = []
        with self.assertRaisesRegex(ValueError, 'no declared connectors'):
            p.connector_edge(self.facts(), p.envelope_declarations(record))


def spanning_oracle(points, weight):
    """Brute force over every edge subset: an independent MST total."""
    n = len(points)
    pairs = list(combinations(range(n), 2))
    best = None
    for subset in combinations(pairs, n - 1):
        parent = list(range(n))

        def root(i):
            while parent[i] != i:
                i = parent[i]
            return i
        for a, b in subset:
            parent[root(a)] = root(b)
        if len({root(i) for i in range(n)}) == 1:
            total = sum(weight(points[a], points[b]) for a, b in subset)
            best = total if best is None else min(best, total)
    return best


class RatsnestTest(unittest.TestCase):
    def facts(self, pads, mirror=False):
        parts = []
        for ref, entries in pads.items():
            parts.append(part(ref, 0, 0, pads=[(net, -x if mirror else x, y) for net, x, y in entries]))
        return probe(parts)

    def test_three_pad_analytic_geometry(self):
        facts = self.facts({'R1': [('N', 0, 0)], 'C1': [('N', 3, 4)], 'U1': [('N', 3, 0)]})
        totals = p.ratsnest(facts)['totals']
        self.assertEqual(totals['manhattan_nm'], 7 * MM)
        self.assertEqual(totals['euclidean_nm'], [7 * MM, 7 * MM])

    def test_irrational_length_is_a_one_nanometre_interval(self):
        totals = p.ratsnest(self.facts({'R1': [('N', 0, 0)], 'C1': [('N', 1, 1)]}))['totals']
        self.assertEqual(totals['euclidean_nm'], [1414213, 1414214])
        self.assertEqual(totals['manhattan_nm'], 2 * MM)

    def test_matches_brute_force_oracle_on_mixed_nets(self):
        pads = {'R1': [('A', 0, 0), ('B', 7, 1)], 'C1': [('A', 2.5, 4), ('B', 1, 1)],
                'U1': [('A', 5, 0.5), ('A', 5, 0.5), ('B', 3, 9)], 'J1': [('A', 1, 6), ('B', 8, 2)]}
        proxy = p.ratsnest(self.facts(pads))
        for net in ('A', 'B'):
            points = [(int(x*MM), int(y*MM)) for entries in pads.values() for n, x, y in entries if n == net]
            manhattan = spanning_oracle(points, lambda a, b: abs(a[0]-b[0]) + abs(a[1]-b[1]))
            euclid = spanning_oracle(points, lambda a, b: math.dist(a, b))
            self.assertEqual(proxy['nets'][net]['manhattan_nm'], manhattan)
            low, high = proxy['nets'][net]['euclidean_nm']
            self.assertLessEqual(low, euclid)
            self.assertLessEqual(euclid, high)

    def test_duplicate_centres_ties_reordering_and_mirroring_are_byte_stable(self):
        pads = {'R1': [('N', 0, 0), ('N', 0, 0)], 'C1': [('N', 1, 0)], 'U1': [('N', 0, 1)], 'J1': [('M', 4, 4)]}
        first = p.ratsnest(self.facts(pads))
        reordered = self.facts(pads)
        reordered['footprints'].reverse()
        for fp in reordered['footprints']:
            fp['pads'].reverse()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(p.ratsnest(reordered), sort_keys=True))
        self.assertEqual(len(first['nets']['N']['terminals']), 4)
        self.assertEqual(first['nets']['M']['manhattan_nm'], 0)
        mirrored = p.ratsnest(self.facts(pads, mirror=True))
        self.assertEqual(mirrored['totals'], first['totals'])

    def test_unassigned_pads_are_inventoried_not_measured(self):
        proxy = p.ratsnest(self.facts({'R1': [('', 0, 0), ('N', 1, 0)], 'C1': [('N', 2, 0)]}))
        self.assertEqual(proxy['unassigned_pads'], [['R1', 'u-R1', '1', 'R1-p0']])
        self.assertEqual(proxy['totals']['terminals'], 2)

    def test_declared_maxima_exact_boundary_and_straddling_interval(self):
        exact = p.ratsnest(self.facts({'R1': [('N', 0, 0)], 'C1': [('N', 3, 4)], 'U1': [('N', 3, 0)]}))
        self.assertEqual(p.ratsnest_bounds(exact, {'manhattan': 7 * MM})['status'], 'pass')
        failed = p.ratsnest_bounds(exact, {'manhattan': 7 * MM - 1})
        self.assertEqual(failed['status'], 'defect')
        self.assertLess(failed['margins']['ratsnest_manhattan_total']['max'], 0)
        root2 = p.ratsnest(self.facts({'R1': [('N', 0, 0)], 'C1': [('N', 1, 1)]}))
        self.assertEqual(p.ratsnest_bounds(root2, {'euclidean': 1414213})['status'], 'unknown')
        self.assertEqual(p.ratsnest_bounds(root2, {'euclidean': 1414214})['status'], 'pass')
        self.assertEqual(p.ratsnest_bounds(root2, {'euclidean': 1414212})['status'], 'defect')

    def test_no_net_terminals_is_unknown(self):
        empty = p.ratsnest(self.facts({'R1': [('', 0, 0)]}))
        self.assertEqual(p.ratsnest_bounds(empty, {'manhattan': MM})['status'], 'unknown')


class RoutingFeedbackTest(unittest.TestCase):
    BOARD = 'b' * 64

    def result(self, digests, top=None, board='board.kicad_pcb', blocked=True):
        routing = {'status': 'INCOMPLETE'}
        if blocked:
            routing['blocked_by'] = {'method': 'heuristic corridors; see docs/blocked.md', 'top': top or []}
        return {'kind': 'route', 'input': {'board': board, 'fingerprint': p.route_fingerprint(digests)},
                'routing': routing}

    def test_applicable_footprint_blockers_are_information_with_native_uuid(self):
        digests = {'board.kicad_pcb': self.BOARD, 'board.kicad_pro': 'c'*64}
        top = [{'id': 'footprint:R1', 'category': 'footprint', 'kind': 'footprint', 'label': 'R1 pads',
                'blocks': 2, 'blocks_any_layer': 1},
               {'id': 'footprint:Q9', 'category': 'footprint', 'kind': 'footprint', 'label': 'Q9', 'blocks': 1,
                'blocks_any_layer': 1},
               {'id': 'rule_area:wall', 'category': 'keepout', 'kind': 'rule_area', 'label': 'wall', 'blocks': 3,
                'blocks_any_layer': 3}]
        fps = [part('R1', 1, 1), part('C1', 5, 5)]
        out = p.routing_feedback(self.result(digests, top), digests, self.BOARD, fps)
        self.assertEqual((out['status'], out['authority']), ('applicable', 'advisory'))
        self.assertEqual([(b['reference'], b['uuid']) for b in out['blockers']], [('R1', 'u-R1')])
        self.assertEqual([u['id'] for u in out['unattributed']], ['footprint:Q9'])

    def test_stale_tampered_and_malformed_results_are_unavailable(self):
        digests = {'board.kicad_pcb': self.BOARD}
        stale = p.routing_feedback(self.result(digests), digests, 'd'*64, [])
        self.assertEqual(stale['status'], 'stale')
        forged = self.result(digests)
        forged['input']['fingerprint'] = 'f' * 64
        self.assertEqual(p.routing_feedback(forged, digests, self.BOARD, [])['status'], 'unavailable')
        for broken in ({}, {'kind': 'route'}, {**self.result(digests), 'routing': None},
                       {**self.result(digests), 'routing': {'blocked_by': {'error': 'x'}}}):
            self.assertEqual(p.routing_feedback(broken, digests, self.BOARD, [])['status'], 'unavailable')

    def test_complete_attempt_without_blockers_is_applicable_and_empty(self):
        digests = {'board.kicad_pcb': self.BOARD}
        out = p.routing_feedback(self.result(digests, blocked=False), digests, self.BOARD, [])
        self.assertEqual((out['status'], out['blockers']), ('applicable', []))

    def test_fingerprint_matches_kicad_route_algorithm(self):
        digests = {'b.kicad_pcb': '1'*64, 'a.kicad_pro': '2'*64}
        expected = hashlib.sha256(f"a.kicad_pro\0{'2'*64}\nb.kicad_pcb\0{'1'*64}\n".encode()).hexdigest()
        self.assertEqual(p.route_fingerprint(digests), expected)


class ParameterTest(unittest.TestCase):
    def test_one_criterion_explicit_prerequisite_and_exact_declarations(self):
        self.assertEqual(parameters({'criterion': 'courtyard-overlap/1', 'drc_check': 'drc'}), [])
        self.assertEqual(parameters({'criterion': 'connector-edge/1', 'drc_check': 'drc',
                                     'envelope': 'project/rc.envelope.json'}), ['project/rc.envelope.json'])
        bad = [{}, {'criterion': 'courtyard-overlap/1'}, {'criterion': 'score/1', 'drc_check': 'drc'},
               {'criterion': 'courtyard-overlap/1', 'drc_check': 'drc', 'score_threshold': 70},
               {'criterion': 'edge-clearance/1', 'drc_check': 'drc'},
               {'criterion': 'edge-clearance/1', 'drc_check': 'drc', 'min_courtyard_edge_mm': 0.0000001},
               {'criterion': 'edge-clearance/1', 'drc_check': 'drc', 'min_courtyard_edge_mm': -1},
               {'criterion': 'courtyard-overlap/1', 'drc_check': 'drc', 'min_courtyard_edge_mm': 1},
               {'criterion': 'connector-edge/1', 'drc_check': 'drc', 'envelope': '../escape.json'},
               {'criterion': 'ratsnest-mst/1', 'drc_check': 'drc', 'max_manhattan_mm': True},
               {'criterion': 'courtyard-overlap/1', 'drc_check': 'drc', 'routing_feedback': {'result': 'r.json'}}]
        for params in bad:
            with self.subTest(params=params), self.assertRaises(ValueError):
                parameters(params)

    def test_ratsnest_predicates_exist_only_for_declared_maxima(self):
        self.assertEqual(predicate_names({'criterion': 'ratsnest-mst/1', 'drc_check': 'd'}), [])
        self.assertEqual(predicate_names({'criterion': 'ratsnest-mst/1', 'drc_check': 'd', 'max_manhattan_mm': 5,
                                          'max_euclidean_mm': 4}),
                         ['placement:ratsnest-mst/1:euclidean:max', 'placement:ratsnest-mst/1:manhattan:max'])
        self.assertEqual(predicate_names({'criterion': 'footprint-keepout/1', 'drc_check': 'd'}),
                         ['placement:footprint-keepout/1'])


if __name__ == '__main__':
    unittest.main()


class CapabilityTest(unittest.TestCase):
    FILES = {'project/b.kicad_pcb': 'a'*64, 'project/b.kicad_sch': 'b'*64, 'project/rc.envelope.json': 'c'*64}
    SCOPE = {'board': 'project/b.kicad_pcb', 'schematic': 'project/b.kicad_sch'}

    def describe(self, specs, index):
        from agent_ee.capabilities import describe_check
        return describe_check(specs[index], check_specs=specs, files=self.FILES, scope=self.SCOPE)

    def test_projection_needs_a_later_full_drc_and_has_its_own_predicate(self):
        stage = {'id': 'drc-stage', 'kind': 'drc', 'params': {'projection': 'placement-stage/1'}}
        full = {'id': 'drc', 'kind': 'drc'}
        self.assertEqual(self.describe([stage, full], 0)['predicates'], ['native:drc:placement-stage/1'])
        self.assertEqual(self.describe([stage, full], 1)['predicates'], ['native:drc:violations'])
        for specs in ([stage], [full, stage], [stage, {**stage, 'id': 'again'}]):
            self.assertEqual(self.describe(specs, specs.index(stage))['status'], 'unsupported')
        for bad in ({'id': 'x', 'kind': 'drc', 'params': {'projection': 'routing/1'}},
                    {'id': 'x', 'kind': 'erc', 'params': {'projection': 'placement-stage/1'}}):
            self.assertEqual(self.describe([bad, full], 0)['status'], 'unsupported')

    def test_placement_requires_an_earlier_drc_prerequisite(self):
        drc = {'id': 'drc', 'kind': 'drc'}
        check = {'id': 'courtyard', 'kind': 'placement',
                 'params': {'criterion': 'courtyard-overlap/1', 'drc_check': 'drc'}}
        described = self.describe([drc, check], 1)
        self.assertEqual((described['status'], described['predicates'], described['prerequisites']),
                         ('supported', ['placement:courtyard-overlap/1'], [{'check_id': 'drc', 'kind': 'drc'}]))
        self.assertEqual(self.describe([check, drc], 0)['status'], 'unsupported')
        netlist = {'id': 'drc', 'kind': 'netlist'}
        self.assertEqual(self.describe([netlist, check], 1)['status'], 'unsupported')
        bad = {**check, 'params': {**check['params'], 'score_threshold': 70}}
        self.assertEqual(self.describe([drc, bad], 1)['status'], 'unsupported')

    def test_connector_edge_needs_the_integrated_envelope_validator(self):
        drc = {'id': 'drc', 'kind': 'drc'}
        check = {'id': 'connector', 'kind': 'placement', 'params': {
            'criterion': 'connector-edge/1', 'drc_check': 'drc', 'envelope': 'project/rc.envelope.json'}}
        described = self.describe([drc, check], 1)
        try:
            import agent_ee.board_envelope  # noqa: F401  AGENTEE-85 lands separately.
        except ImportError:
            self.assertEqual(described['status'], 'unsupported')
            self.assertIn('AGENTEE-85', described['unsupported_reasons'][0]['message'])
        else:
            self.assertEqual(described['immutable_inputs'], ['project/rc.envelope.json'])


class DrcProjectionRuntimeTest(unittest.TestCase):
    def test_projection_without_later_full_drc_is_refused_before_any_tool(self):
        import tempfile
        import time
        from pathlib import Path
        from agent_ee.checks import CheckError, Ctx, check_drc
        stage = {'id': 'drc-stage', 'kind': 'drc', 'params': {'projection': 'placement-stage/1'}}
        with tempfile.TemporaryDirectory() as tmp:
            for specs, message in (([stage], 'later full drc'),
                                   ([{**stage, 'params': {'projection': 'routing/1'}}], 'unknown DRC projection')):
                manifest = {'candidate_id': 'c', 'files': {}, 'scope': {'board': 'b.kicad_pcb', 'checks': specs}}
                ctx = Ctx(Path(tmp), manifest, specs[0], time.monotonic() + 60)
                with self.assertRaisesRegex(CheckError, message) as caught:
                    check_drc(ctx)
                self.assertEqual(caught.exception.execution, 'unsupported')
                self.assertNotIn('tool', ctx.result['details'])

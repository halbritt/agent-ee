"""AGENTEE-212: placer residuals found on the real telemetry-pod seed.

Circular courtyards, declared fixed-courtyard overhangs bound to the envelope,
board-only hole exclusion at the seed's scale, declared group/region input and
refusal of parts without courtyards. Geometry is authored here from numbers
(the seed's courtyard extents and hole positions as facts); no third-party
design file enters the tree. Native tests build owned boards with pcbnew, run
the real writer and native DRC, and compare pad layer sets after a
fresh-process reload.
"""
import copy
import hashlib
import json
import math
import os
from fractions import Fraction
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_ee import courtyard_curves as curves
from agent_ee import placement_groups
from agent_ee.placement import area2, courtyard_bounds, intersection_area, judge, overhang_bands
from agent_ee.placement_predicates import (courtyard_overlap, envelope_overhangs, footprint_keepout,
                                           outline_containment, point_segment2)
from agent_ee.placement_probe import prove_contours
from agent_ee.placer import pack
from test_placement import MM, capture, part, rectangle
from test_placer import authored_groups, request
from toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]


def circle_record(x, y, r=1):
    return prove_contours([{'kind': 'circle', 'center': [round(x*MM), round(y*MM)],
                            'end': [round((x+r)*MM), round(y*MM)]}], 'courtyard_missing', 'courtyard_unproven', True)


def probe_part(ref, x, y, r=1):
    """A bare probe pad: an exact circular courtyard, as the seed's TP1..TP6 carry."""
    fp = part(ref, x, y)
    fp['courtyards'] = {'F': circle_record(x, y, r)}
    return fp


def box(x0, y0, x1, y1):
    return rectangle(x0, y0, x1-x0, y1-y0)


def pod_envelope(fixed, holes=()):
    """The pinned pod envelope with fixture-authored fixed footprints and holes."""
    from agent_ee.board_envelope import validate
    envelope = json.loads((REPO / 'fixtures/telemetry-pod/envelope.json').read_text())
    envelope['obligations'] = []
    envelope['keepouts'] = []
    envelope['mounting_holes'] = [dict(id=f'H{i}', at=[x, y], drill_mm=3.2, plated=False, tolerance_mm=0)
                                  for i, (x, y) in enumerate(holes, 1)]
    envelope['fixed_footprints'] = fixed
    return validate(json.dumps(envelope).encode())


def fixed(ref, edge, along, inset, overhangs=None, insertion='from_top'):
    item = dict(reference=ref, side='front', edge=edge, position_mm=along, inset_mm=inset, rotation_deg=0,
                insertion=insertion, tolerance_mm=0)
    if overhangs is not None:
        item['courtyard_overhangs'] = [dict(edge=e, max_mm=d) for e, d in overhangs]
    return item


# Half the step of a 1 mm circle's 48-vertex bounds: the inner bound's deepest chord.
MIDCHORD = math.pi / 48
EMPTY_DRC = {'kicad_version': '10.0.7', 'violations': [], 'unconnected_items': [], 'schematic_parity': [],
             'ignored_checks': [], 'included_severities': ['error', 'warning', 'exclusion']}


# The real seed's fixed U1 and J2 courtyard extents (mm), as numbers: U1 leaves the
# outline across the top and the right edge, J2 across the right edge.
SEED_U1 = (38.5, -21.25, 86.5, 19.95)
SEED_J2 = (72.73, 21.91, 89.73, 34.09)


class CurvedCourtyardTest(unittest.TestCase):
    def test_circle_bounds_are_exact_and_within_the_declared_tolerance(self):
        centre, radius = (86_360_000, 382_270_000), 1_000_000
        outer, inner = curves.circle(list(centre), [centre[0] + radius, centre[1]])
        r2 = radius * radius
        self.assertEqual(len(outer), len(inner))
        for a, b in zip(outer, outer[1:] + outer[:1]):
            self.assertGreaterEqual(point_segment2(centre, a, b), r2)  # Never enters the disk.
        for p in outer:
            self.assertLessEqual(math.dist(p, centre), radius + curves.ARC_TOLERANCE_NM)
        for a, b in zip(inner, inner[1:] + inner[:1]):
            self.assertLessEqual((a[0]-centre[0])**2 + (a[1]-centre[1])**2, r2)  # On or inside.
            self.assertGreaterEqual(math.sqrt(point_segment2(centre, a, b)), radius - curves.ARC_TOLERANCE_NM)
        record = circle_record(10, 10)
        self.assertEqual((record['status'], record['approximation']['tolerance_nm']), ('proven', 5000))
        outer_area = Fraction(area2(courtyard_bounds(record)[0][0]), 2)
        inner_area = Fraction(area2(courtyard_bounds(record)[1][0]), 2)
        self.assertTrue(inner_area < math.pi * MM * MM < outer_area)

    def test_arcs_bound_convex_and_concave_contours(self):
        def seg(a, b):
            return {'kind': 'segment', 'start': [a[0]*MM, a[1]*MM], 'end': [b[0]*MM, b[1]*MM]}
        def arc(a, m, b):
            return {'kind': 'arc', 'start': [a[0]*MM, a[1]*MM], 'mid': [m[0]*MM, m[1]*MM], 'end': [b[0]*MM, b[1]*MM]}
        # A D shape (convex arc) and a square with a semicircular notch (concave arc).
        for primitives, true_area in (
                ([seg((0, -1), (0, 1)), arc((0, -1), (1, 0), (0, 1))], math.pi / 2),
                ([seg((0, 0), (4, 0)), seg((4, 0), (4, 4)), seg((4, 4), (3, 4)), arc((3, 4), (2, 3), (1, 4)),
                  seg((1, 4), (0, 4)), seg((0, 4), (0, 0))], 16 - math.pi / 2)):
            record = prove_contours(primitives, 'courtyard_missing', 'courtyard_unproven', True)
            self.assertEqual(record['status'], 'proven', record.get('reason'))
            outer, inner = courtyard_bounds(record)
            self.assertLess(Fraction(area2(inner[0]), 2) / MM**2, true_area)
            self.assertGreater(Fraction(area2(outer[0]), 2) / MM**2, true_area)
            self.assertEqual(intersection_area(inner[0], outer[0]), Fraction(area2(inner[0]), 2))
        # Degenerate and tiny curves are refused with their reason, never approximated.
        flat = prove_contours([seg((0, 0), (2, 0)), arc((0, 0), (1, 0), (2, 0))], 'm', 'courtyard_unproven', True)
        self.assertEqual((flat['status'], flat['reason']), ('courtyard_unproven', 'degenerate arc: start, mid and end are collinear'))
        tiny = prove_contours([{'kind': 'circle', 'center': [0, 0], 'end': [8000, 0]}], 'm', 'courtyard_unproven', True)
        self.assertEqual(tiny['status'], 'courtyard_unproven')
        self.assertIn('declared tolerance', tiny['reason'])
        # Outlines and rule areas still refuse every curve.
        outline = prove_contours([{'kind': 'circle', 'center': [0, 0], 'end': [MM, 0]}], 'm', 'outline_unproven')
        self.assertEqual(outline['status'], 'outline_unproven')

    def test_radii_up_to_twice_the_tolerance_and_degenerate_arcs_are_refused_by_name(self):
        tolerance = curves.ARC_TOLERANCE_NM
        def circle(r, x=0, y=0):
            return prove_contours([{'kind': 'circle', 'center': [x, y], 'end': [x + r, y]}],
                                  'courtyard_missing', 'courtyard_unproven', True)
        def d_shape(r):
            return prove_contours([{'kind': 'segment', 'start': [0, -r], 'end': [0, r]},
                                   {'kind': 'arc', 'start': [0, -r], 'mid': [r, 0], 'end': [0, r]}],
                                  'courtyard_missing', 'courtyard_unproven', True)
        too_small = 'curve radius not larger than twice the declared tolerance'
        # 1 nm and 1000 nm once raised a math domain error from the step count.
        for r in (1, 1000, 2 * tolerance):
            for record in (circle(r), d_shape(r)):
                self.assertEqual((record['status'], record.get('reason')), ('courtyard_unproven', too_small), r)
            with self.assertRaisesRegex(curves.CurveError, too_small):
                curves.circle([0, 0], [r, 0])
            with self.assertRaisesRegex(curves.CurveError, too_small):
                curves.arc([0, -r], [r, 0], [0, r])
        for record in (circle(2 * tolerance + 1), d_shape(2 * tolerance + 1)):
            self.assertEqual(record['status'], 'proven', record.get('reason'))
            self.assertEqual(record['approximation']['tolerance_nm'], tolerance)
        for primitives, reason in (
                ([{'kind': 'arc', 'start': [0, 0], 'mid': [MM, MM], 'end': [0, 0]}], 'arc start equals end'),
                ([{'kind': 'segment', 'start': [0, 0], 'end': [2*MM, 0]},
                  {'kind': 'arc', 'start': [0, 0], 'mid': [0, 0], 'end': [2*MM, 0]}],
                 'degenerate arc: start, mid and end are collinear')):
            record = prove_contours(primitives, 'courtyard_missing', 'courtyard_unproven', True)
            self.assertEqual((record['status'], record['reason']), ('courtyard_unproven', reason))
        # The packer and the judge refuse a 1 nm probe courtyard by name.
        board = capture([part('C2', 15, 15), part('TP1', 5, 5)])
        board['footprints'][1]['courtyards']['F'] = circle(1, 5*MM, 5*MM)
        report = pack(board, request(fixed_refs=[]))
        self.assertEqual((report['status'], report['outcome'], report['placements']),
                         ('unsupported_geometry', 'unknown', []))
        self.assertTrue(report['reason'].startswith('TP1 F courtyard not qualified: courtyard_unproven ('), report)
        judged = judge(board)
        self.assertEqual(judged['verdict'], 'unknown')
        self.assertEqual([(u['kind'], u['ref'], u['side']) for u in judged['unknown']], [('courtyard', 'TP1', 'F')])

    def test_overlap_and_keepout_predicates_leave_the_tolerance_band_unknown(self):
        # The reviewer's 1.998 mm midchord pair: the circles overlap by 2 um, the inner
        # bounds do not, the outer bounds do. Native silence cannot make it a pass.
        dx, dy = round(1_998_000 * math.cos(MIDCHORD)), round(1_998_000 * math.sin(MIDCHORD))
        def pair(dx, dy):
            board = capture([probe_part('TP1', 10, 10), probe_part('TP2', 10 + dx / MM, 10 + dy / MM)])
            return courtyard_overlap(board, EMPTY_DRC)
        band = pair(dx, dy)
        self.assertEqual((band['status'], band['defects']), ('unknown', []))
        self.assertEqual([(u['kind'], u['refs']) for u in band['unknown']], [('courtyard_approximation', ['TP1', 'TP2'])])
        axis = pair(1_998_000, 0)  # The inner bounds overlap: the helper disagrees with native silence.
        self.assertEqual([u['kind'] for u in axis['unknown']], ['helper_disagreement'])
        self.assertEqual((pair(2_010_000, 0)['status'], pair(2_010_000, 0)['unknown']), ('pass', []))
        # A native item for the pair stays the defect, without a second unknown.
        board = capture([probe_part('TP1', 10, 10), probe_part('TP2', 10 + dx / MM, 10 + dy / MM)])
        self.assertEqual(board['footprints'][1]['courtyards']['F']['primitives'][0]['center'], [10*MM + dx, 10*MM + dy])
        native = dict(EMPTY_DRC, violations=[{'type': 'courtyards_overlap', 'severity': 'error',
                                              'items': [{'uuid': 'TP1'}, {'uuid': 'TP2'}]}])
        flagged = courtyard_overlap(board, native)
        self.assertEqual((flagged['status'], flagged['unknown']), ('defect', []))
        # A footprint keepout 1 um beyond the true circle meets only the outer bound.
        def keepout(x0):
            board = capture([probe_part('TP1', 10, 10)], rectangle(0, 0, 30, 30))
            board['rule_areas'] = [{'uuid': 'ko', 'name': 'ko', 'owner': 'board', 'layers': ['F.Cu'],
                                    'forbid': {'footprints': True}, 'placement_area': False,
                                    'outline': {'status': 'proven', 'polygons': [rectangle(x0, 0, 10, 30)]}}]
            return footprint_keepout(board, EMPTY_DRC)
        report = keepout(11.001)
        self.assertEqual(report['status'], 'unknown')
        self.assertEqual([(u['kind'], u['areas']) for u in report['unknown']], [('courtyard_approximation', ['ko'])])
        self.assertEqual([u['kind'] for u in keepout(10.99)['unknown']], ['helper_disagreement'])
        self.assertEqual(keepout(11.01)['status'], 'pass')

    def test_circular_courtyards_pack_and_overlap_is_a_defect_only_on_the_inner_bound(self):
        board = capture([part('U1', 1, 1), probe_part('TP1', 15, 15), probe_part('TP2', 15, 15)])
        plan = pack(board, request(gap_nm=0))
        self.assertEqual((plan['status'], plan['judgment']['verdict']), ('planned', 'pass'), plan.get('reason'))
        # Overlapping circles: a proved defect.
        overlap = capture([probe_part('TP1', 5, 5), probe_part('TP2', 6.5, 5)])
        report = judge(overlap)
        self.assertEqual(report['verdict'], 'hard_fail')
        self.assertEqual([f['refs'] for f in report['hard_failures']], [['TP1', 'TP2']])
        # Clear by 4 um, inside the 5 um tolerance: the outer bounds meet, the circles do not.
        close = judge(capture([probe_part('TP1', 5, 5), probe_part('TP2', 7.004, 5)]))
        self.assertEqual((close['verdict'], close['hard_failures']), ('unknown', []))
        self.assertEqual([u['kind'] for u in close['unknown']], ['courtyard_approximation'])
        # Clear by 50 um: pass.
        self.assertEqual(judge(capture([probe_part('TP1', 5, 5), probe_part('TP2', 7.05, 5)]))['verdict'], 'pass')

    def test_circle_infeasibility_is_proved_only_on_the_inner_bound(self):
        def probes(side, count=2):
            return pack(capture([probe_part(f'TP{i}', 1.2, 1.2) for i in range(1, count+1)],
                                rectangle(0, 0, side, side)), request(fixed_refs=[]))
        # Two 1 mm circles: inner bounds 6.265 mm2 exceed a 2.4 mm square's 5.76 mm2.
        report = probes(2.4)
        self.assertEqual((report['status'], report['proof']['kind']), ('packing_infeasible', 'courtyard_area'))
        # A 2.506 mm square (6.280 mm2) lies between the inner (6.265) and outer (6.292) areas:
        # not proved, so a bounded search failure, never a defect.
        report = probes(2.506)
        self.assertEqual((report['status'], report['outcome']), ('packing_search_exhausted', 'unknown'))
        # One circle in a 2.002 mm square fits in truth; its outer bound (2.004 mm) does not.
        report = probes(2.002, 1)
        self.assertEqual((report['status'], report['outcome']), ('packing_search_exhausted', 'unknown'))


class FixedOverhangTest(unittest.TestCase):
    def board(self):
        u1 = part('U1', 62.5, 6.5, shape=box(*SEED_U1))
        j2 = part('J2', 75.44, 28, shape=box(*SEED_J2))
        j2['courtyards']['B'] = copy.deepcopy(j2['courtyards']['F'])
        movable = [part(ref, 2, 2) for ref in ('C1', 'C2')]
        for fp in (u1, j2):
            fp['locked'] = True
        return capture([u1, j2, *movable], rectangle(0, 0, 80, 50))

    def record(self, u1=(('top', 21.25), ('right', 6.5)), j2=(('right', 9.73),)):
        return pod_envelope([fixed('U1', 'top', 62.5, 6.5, u1), fixed('J2', 'right', 28, 4.56, j2, 'through_edge')])

    def test_declared_overhang_places_and_undeclared_is_refused_by_name(self):
        board = self.board()
        declarations = envelope_overhangs(self.record())
        self.assertEqual([(d['reference'], [(e['name'], e['max_nm']) for e in d['edges']]) for d in declarations],
                         [('U1', [('top', 21_250_000), ('right', 6_500_000)]), ('J2', [('right', 9_730_000)])])
        self.assertEqual(declarations[0]['source']['pointer'], '/fixed_footprints/0/courtyard_overhangs')
        plan = pack(board, request(fixed_refs=[], fixed_overhangs=declarations))
        self.assertEqual((plan['status'], plan['judgment']['verdict']), ('planned', 'pass'), plan.get('reason'))
        self.assertEqual(sorted((d['ref'], d['side']) for d in plan['judgment']['declared_overhangs']),
                         [('J2', 'B'), ('J2', 'F'), ('U1', 'F')])
        # Without the declaration, the same fixed courtyards are a named conflict.
        for policy in (request(fixed_refs=[]), request(fixed_refs=[], fixed_overhangs=declarations[1:])):
            refused = pack(board, policy)
            self.assertEqual((refused['status'], refused['outcome'], refused['placements']),
                             ('fixed_conflict', 'unknown', []))
            self.assertIn('fixed placement U1 conflicts', refused['reason'])
        # One nanometre short on one declared side, or the corner side left out: refused.
        for u1 in ((('top', 21.25), ('right', 6.499999)), (('top', 21.25),), (('top', 21.249999), ('right', 6.5))):
            short = pack(board, request(fixed_refs=[], fixed_overhangs=envelope_overhangs(self.record(u1))))
            self.assertEqual(short['status'], 'fixed_conflict', u1)
        # An allowance is never an exemption for a movable part.
        movable = copy.deepcopy(board)
        movable['footprints'][0]['locked'] = False
        with self.assertRaisesRegex(ValueError, 'overhang declared for movable reference U1'):
            pack(movable, request(fixed_refs=[], fixed_overhangs=declarations))

    def test_containment_predicate_grades_declared_overhangs(self):
        board = self.board()
        declarations = envelope_overhangs(self.record())
        undeclared = outline_containment(board)
        self.assertEqual((undeclared['status'], sorted({d['ref'] for d in undeclared['defects']})),
                         ('defect', ['J2', 'U1']))
        declared = outline_containment(board, declarations)
        self.assertEqual(declared['status'], 'pass')
        self.assertEqual(len(declared['declared_overhangs']), 3)
        # Beyond the declared distance, the excess alone remains a defect.
        tight = outline_containment(board, envelope_overhangs(self.record(j2=(('right', 9),))))
        self.assertEqual([(d['ref'], d['side']) for d in tight['defects']], [('J2', 'B'), ('J2', 'F')])
        self.assertAlmostEqual(tight['defects'][0]['outside_area_mm2'], 0.73 * (34.09 - 21.91))

    def test_malformed_or_mismatched_declarations_refuse(self):
        for overhangs, message in (([('middle', 1)], 'not a distinct outline edge'),
                                   ([('top', 1), ('top', 2)], 'not a distinct outline edge'),
                                   ([('top', 0)], 'must be positive'), ([], 'nonempty list')):
            with self.subTest(overhangs=overhangs), self.assertRaisesRegex(ValueError, message):
                pod_envelope([fixed('U1', 'top', 62.5, 6.5, overhangs)])
        board = self.board()
        declaration = envelope_overhangs(self.record())[0]
        moved = copy.deepcopy(declaration)
        moved['edges'][0]['start_nm'] = [1, 0]  # Not an edge of the proven outline.
        plan = pack(board, request(fixed_refs=[], fixed_overhangs=[moved]))
        self.assertEqual((plan['status'], plan['outcome']), ('unsupported_geometry', 'unknown'))
        self.assertIn('U1: overhang edge top is not a proven outline edge', plan['reason'])
        inward = copy.deepcopy(declaration)
        inward['edges'][0]['outward'] = 'y+'
        self.assertEqual(pack(board, request(fixed_refs=[], fixed_overhangs=[inward]))['status'], 'unsupported_geometry')
        unsourced = copy.deepcopy(declaration)
        del unsourced['source']
        with self.assertRaisesRegex(ValueError, 'reference, edges and source'):
            overhang_bands([unsourced], rectangle(0, 0, 80, 50))
        # The obligation text names the allowance.
        from agent_ee.board_envelope import derive_requirements
        texts = [r['statement'] for r in derive_requirements(self.record(), 'envelope.json')['requirements']
                 if r['id'].endswith('fixed-U1')]
        self.assertIn('at most 21.25 mm across edge top; 6.5 mm across edge right, and into the corner', texts[0])

    def test_adapter_takes_overhangs_only_from_the_captured_envelope(self):
        from agent_ee import placement_adapter as pa
        record = self.record()
        self.assertEqual(pa.envelope_overhangs(record), envelope_overhangs(record))
        with tempfile.TemporaryDirectory(prefix='agent-ee-ee212-binding-') as temporary:
            inputs = Path(temporary)
            for rel, data in (('board.kicad_pcb', b'owned placeholder board'),
                              ('design.kicad_sch', b'owned placeholder schematic')):
                (inputs / rel).write_bytes(data)
            envelope = json.loads((REPO / 'fixtures/telemetry-pod/envelope.json').read_text())
            envelope.update(obligations=[], keepouts=[], mounting_holes=[],
                            fixed_footprints=[fixed('U1', 'top', 62.5, 6.5, (('top', 21.25), ('right', 6.5)))])
            (inputs / 'envelope.json').write_text(json.dumps(envelope))
            policy = request(fixed_refs=[])
            (inputs / 'policy.json').write_text(json.dumps(policy))
            from agent_ee.storage import digest
            files = {rel: digest(inputs / rel) for rel in ('board.kicad_pcb', 'design.kicad_sch', 'envelope.json',
                                                            'policy.json')}
            manifest = {'files': files, 'scope': {'board': 'board.kicad_pcb', 'schematic': 'design.kicad_sch',
                        'checks': [{'id': 'edge-u1', 'kind': 'placement', 'params': {
                            'criterion': 'connector-edge/1', 'envelope': 'envelope.json', 'reference': 'U1',
                            'drc_check': 'drc'}}]}}
            declaration = {'schema': pa.ADAPTER_SCHEMA, 'helper': 'agent-ee.placer/1', 'policy': 'policy.json',
                           'fixed_footprints': [], 'timeout_s': 60}
            envelopes = pa.envelope_fixed(manifest, inputs, ['board.kicad_pcb'])
            self.assertEqual([o['reference'] for o in envelopes[0]['overhangs']], ['U1'])
            bound = pa.bind_declaration(declaration, manifest, ['board.kicad_pcb'])
            self.assertEqual(pa._policy(inputs, bound, manifest), policy)
            # A packing policy that carries its own allowance is refused before any board is read.
            (inputs / 'policy.json').write_text(json.dumps({**policy, 'fixed_overhangs': []}))
            manifest['files']['policy.json'] = digest(inputs / 'policy.json')
            with self.assertRaisesRegex(ValueError, 'fixed_overhangs come only from a captured envelope'):
                pa.binding(declaration, manifest, inputs, ['board.kicad_pcb'])


class GroupRegionTest(unittest.TestCase):
    def board(self):
        parts = [part('U5', 1, 1), part('C1', 1, 1), part('C2', 1, 1), probe_part('TP1', 5, 5),
                 probe_part('TP2', 5, 5), part('R9', 1, 1)]
        for fp in parts:
            fp['pads'] = [{'number': '1', 'net': 'SW' if fp['ref'] in ('U5', 'C1', 'C2') else '',
                           'position_nm': list(fp['position_nm']), 'shape': 1, 'rotation_deg': 0}]
        board = capture(parts, rectangle(0, 0, 30, 20))
        board['pcbnew_version'] = '10.0.7'
        return board

    GROUPS = authored_groups([{'id': 'pre-buck', 'references': ['C1', 'C2'], 'anchor': {'reference': 'U5'}, 'max_distance_nm': 4 * MM},
              {'id': 'probes', 'references': ['TP1', 'TP2'], 'region': {'polygons': [box(20, 2, 28, 18)]}},
              {'id': 'target', 'references': ['R9'], 'anchor': {'point_nm': [15 * MM, 15 * MM]},
               'max_distance_nm': 2 * MM}], 'tests/test_placer_residuals.py: authored group geometry')

    def test_groups_are_enforced_by_the_packer_and_graded(self):
        board = self.board()
        plan = pack(board, request(fixed_refs=[], step_budget=200000, groups=self.GROUPS))
        self.assertEqual(plan['status'], 'planned', plan.get('reason'))
        self.assertEqual(plan['group_grading']['status'], 'pass')
        at = {p['ref']: p['position_nm'] for p in plan['placements']}
        for ref in ('C1', 'C2'):
            self.assertLessEqual(math.dist(at[ref], at['U5']), 4 * MM)
        for ref in ('TP1', 'TP2'):
            self.assertTrue(21 * MM <= at[ref][0] <= 27 * MM and 3 * MM <= at[ref][1] <= 17 * MM, at[ref])
        self.assertLessEqual(math.dist(at['R9'], (15 * MM, 15 * MM)), 2 * MM)
        # The same plan without groups puts the probes elsewhere: the groups did the work.
        free = {p['ref']: p['position_nm'] for p in pack(board, request(fixed_refs=[]))['placements']}
        self.assertNotEqual((free['TP1'], free['TP2'], free['R9']), (at['TP1'], at['TP2'], at['R9']))
        # A region too narrow for a probe, with room for its area, is a bounded search
        # failure, never a defect.
        small = copy.deepcopy(self.GROUPS)
        small[1]['region'] = {'polygons': [box(20, 2, 21.5, 17.5)]}
        report = pack(board, request(fixed_refs=[], groups=small))
        self.assertEqual((report['status'], report['outcome']), ('packing_search_exhausted', 'unknown'))
        # One smaller than the probes' inner-bound courtyard area is proven too small (AGENTEE-212).
        small[1]['region'] = {'polygons': [box(20, 2, 21.5, 3.5)]}
        report = pack(board, request(fixed_refs=[], groups=small))
        self.assertEqual((report['status'], report['outcome'], report['proof']['kind'], report['proof']['group']),
                         ('packing_infeasible', 'defect', 'region_capacity', 'probes'))

    def test_grade_reports_region_and_anchor_defects_and_unknowns(self):
        from agent_ee.placer import transform
        board = self.board()
        plan = pack(board, request(fixed_refs=[], step_budget=200000, groups=self.GROUPS))
        by_ref = {fp['ref']: fp for fp in board['footprints']}
        placed = {p['ref']: transform(by_ref[p['ref']], p['position_nm'], p['rotation_deg'])
                  for p in plan['placements']}
        facts = {**board, 'footprints': list(placed.values())}
        groups = placement_groups.validate(self.GROUPS)
        self.assertEqual(placement_groups.grade(facts, groups)['status'], 'pass')
        moved = copy.deepcopy(facts)
        for fp in moved['footprints']:
            if fp['ref'] in ('TP2', 'C2'):
                dx = 10 * MM if fp['ref'] == 'C2' else -10 * MM
                fp.update(transform(fp, [fp['position_nm'][0] + dx, fp['position_nm'][1]], 0))
        result = placement_groups.grade(moved, groups)
        self.assertEqual(result['status'], 'defect')
        self.assertEqual(sorted((d['kind'], d['ref']) for d in result['defects']),
                         [('group_anchor_distance', 'C2'), ('outside_group_region', 'TP2')])
        missing = copy.deepcopy(facts)
        missing['footprints'] = [fp for fp in missing['footprints'] if fp['ref'] != 'U5']
        self.assertEqual([u['kind'] for u in placement_groups.grade(missing, groups)['unknown']], ['anchor_missing'])

    def test_group_declarations_refuse_membership_without_a_constraint(self):
        refs = {'U5', 'C1', 'C2', 'TP1', 'TP2', 'R9'}
        for groups, message in (
                ([{'id': 'g', 'references': ['C1']}], 'membership alone constrains nothing'),
                ([{'id': 'g', 'references': ['C1'], 'anchor': {'reference': 'U5'}}], 'declared together'),
                ([{'id': 'a', 'references': ['C1'], 'region': {'polygons': [box(0, 0, 1, 1)]}},
                  {'id': 'b', 'references': ['C1'], 'region': {'polygons': [box(0, 0, 1, 1)]}}], 'at most one group'),
                ([{'id': 'a', 'references': ['C1'], 'anchor': {'reference': 'C2'}, 'max_distance_nm': 1},
                  {'id': 'b', 'references': ['C2'], 'anchor': {'reference': 'U5'}, 'max_distance_nm': 1}],
                 'itself an anchored member'),
                ([{'id': 'g', 'references': ['X1'], 'region': {'polygons': [box(0, 0, 1, 1)]}}], 'unknown reference'),
                ([{'id': 'g', 'references': ['C1'], 'anchor': {'reference': 'C1'}, 'max_distance_nm': 1}],
                 'non-member')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                placement_groups.validate(groups, refs)
        with self.assertRaisesRegex(ValueError, 'expected agent-ee.placement-groups/1'):
            placement_groups.load({'schema': 'agent463.packing-policy-proposal/1', 'groups': []})
        self.assertEqual(placement_groups.load({'schema': placement_groups.SCHEMA, 'groups': self.GROUPS}),
                         placement_groups.validate(self.GROUPS))


class MissingCourtyardTest(unittest.TestCase):
    def test_fixed_and_movable_parts_without_courtyards_are_refused_by_name(self):
        parts = [part('J1', 10, 10), part('H1', 2, 2), part('C1', 5, 5), part('C2', 15, 15)]
        for fp in parts[:3]:
            fp['courtyards'] = {}
        parts[0]['locked'] = parts[1]['locked'] = True
        board = capture(parts)
        original = copy.deepcopy(board)
        report = pack(board, request(fixed_refs=['H1']))
        self.assertEqual((report['status'], report['outcome'], report['steps_used'], report['placements']),
                         ('unsupported_geometry', 'unknown', 0, []))
        self.assertEqual(report['reason'], 'mounting-side courtyard missing: C1, H1, J1')
        self.assertEqual(board, original)
        # An unqualified courtyard is named with its side and native reason.
        board = capture([part('C2', 15, 15), part('TP1', 5, 5)])
        board['footprints'][1]['courtyards']['F'] = prove_contours(
            [{'kind': 'circle', 'center': [5*MM, 5*MM], 'end': [5*MM + 8000, 5*MM]}],
            'courtyard_missing', 'courtyard_unproven', True)
        report = pack(board, request(fixed_refs=[]))
        self.assertEqual(report['status'], 'unsupported_geometry')
        self.assertTrue(report['reason'].startswith('TP1 F courtyard not qualified: courtyard_unproven ('), report)


# ----------------------------------------------------------------- native

FRESH_PAD_LAYERS = ("import json,sys,pcbnew\n"
                    "board=pcbnew.LoadBoard(sys.argv[1])\n"
                    "json.dump({fp.GetReference()+'.'+p.GetNumber():sorted(pcbnew.BOARD.GetStandardLayerName(l)"
                    " for l in p.GetLayerSet().Seq()) for fp in board.GetFootprints() for p in fp.Pads()},"
                    "open(sys.argv[2],'w'))\n")


def fresh_pad_layers(board, out):
    """Pad layer names from a fresh process that only loads the saved board.

    LSET.AllCuMask() is shared native state; a writer that mutated it would fold
    *.Mask into *.Cu on save, which only a fresh-process reload shows."""
    subprocess.run(['/usr/bin/python3', '-B', '-c', FRESH_PAD_LAYERS, str(board), str(out)],
                   env=dict(os.environ, PYTHONPATH=str(REPO / 'src')), capture_output=True, check=True)
    return json.loads(Path(out).read_text())


def native_pod(path, refs=(), holes=((4, 4), (76, 4), (76, 46), (4, 46)), bulk=0):
    """An owned 80 x 50 mm board: U1/J1/J2 fixed with the seed's outboard courtyard extents,
    board-only NPTH holes as the scaffold writes them, TP1..TP3 probe pads with 1 mm circular
    courtyards and R1; ``bulk`` adds that many netted single-pad parts R100... ."""
    import pcbnew as k
    board = k.BOARD()
    def vec(x, y):
        return k.VECTOR2I(round(x*MM), round(y*MM))
    def rect(parent, layer, x0, y0, x1, y1):
        shape = k.PCB_SHAPE(parent, k.SHAPE_T_RECT)
        shape.SetLayer(layer)
        shape.SetStart(vec(x0, y0))
        shape.SetEnd(vec(x1, y1))
        shape.SetWidth(50000)
        parent.Add(shape)
    for a, b in (((0, 0), (80, 0)), ((80, 0), (80, 50)), ((80, 50), (0, 50)), ((0, 50), (0, 0))):
        edge = k.PCB_SHAPE(board, k.SHAPE_T_SEGMENT)
        edge.SetLayer(k.Edge_Cuts)
        edge.SetStart(vec(*a))
        edge.SetEnd(vec(*b))
        edge.SetWidth(50000)
        board.Add(edge)
    nets = {}
    def net(name):
        if name and name not in nets:
            nets[name] = k.NETINFO_ITEM(board, name)
            board.Add(nets[name])
        return nets.get(name)
    def smd(fp, number, x, y, size, shape, layers, name=''):
        pad = k.PAD(fp)
        pad.SetNumber(number)
        pad.SetAttribute(k.PAD_ATTRIB_SMD)
        pad.SetShape(shape)
        pad.SetSize(vec(size, size))
        owned = k.LSET()  # A fresh set: shared static LSETs are never mutated.
        for layer in layers:
            owned.AddLayer(layer)
        pad.SetLayerSet(owned)
        pad.SetPosition(vec(x, y))
        fp.Add(pad)
        if name:
            pad.SetNet(net(name))
    def footprint(ref, courtyard, at, locked=False, back=False):
        fp = k.FOOTPRINT(board)
        fp.SetReference(ref)
        fp.SetValue('owned')
        board.Add(fp)
        for layer in (k.F_CrtYd, k.B_CrtYd) if back else (k.F_CrtYd,):
            if courtyard is not None:
                rect(fp, layer, *courtyard)
        fp.SetPosition(vec(*at))
        fp.SetLocked(locked)
        return fp
    front = (k.F_Cu, k.F_Mask, k.F_Paste)
    u1 = footprint('U1', (-24, -27.75, 24, 13.45), (62.5, 6.5), locked=True)  # Seed U1 extent.
    smd(u1, '1', 62.5, 6.5, 1, k.PAD_SHAPE_RECT, front, 'EN')
    j1 = footprint('J1', (-30.4, -16.05, 30.4, 16.05), (40, 36.5), locked=True)  # Nominal flange, 2.55 mm out.
    for number, x, name in (('1', 38, 'TX'), ('2', 42, 'RX')):
        pad = k.PAD(j1)
        pad.SetNumber(number)
        pad.SetAttribute(k.PAD_ATTRIB_PTH)
        pad.SetShape(k.PAD_SHAPE_CIRCLE)
        pad.SetSize(vec(1.6, 1.6))
        pad.SetDrillSize(vec(1, 1))
        through = k.LSET(k.LSET.AllCuMask())  # Copy, then add the masks to the copy.
        through.AddLayer(k.F_Mask)
        through.AddLayer(k.B_Mask)
        pad.SetLayerSet(through)
        pad.SetPosition(vec(x, 36.5))
        j1.Add(pad)
        pad.SetNet(net(name))
    j2 = footprint('J2', (-2.71, -6.09, 14.29, 6.09), (75.44, 28), locked=True, back=True)  # Seed J2 extent.
    smd(j2, '1', 75.44, 28, 1, k.PAD_SHAPE_RECT, front)
    for i, (x, y) in enumerate(holes, 1):
        fp = footprint(f'H{i}', None, (0, 0), locked=True)
        fp.SetAttributes(k.FP_BOARD_ONLY | k.FP_EXCLUDE_FROM_BOM | k.FP_EXCLUDE_FROM_POS_FILES)
        pad = k.PAD(fp)
        pad.SetAttribute(k.PAD_ATTRIB_NPTH)
        pad.SetShape(k.PAD_SHAPE_CIRCLE)
        pad.SetDrillSize(vec(3.2, 3.2))
        pad.SetSize(vec(3.2, 3.2))
        layers = k.LSET(k.LSET.AllCuMask())
        layers.AddLayer(k.F_Mask)
        layers.AddLayer(k.B_Mask)
        pad.SetLayerSet(layers)
        fp.Add(pad)
        for layer in (k.F_CrtYd, k.B_CrtYd):
            rect(fp, layer, -1.6, -1.6, 1.6, 1.6)
        fp.SetPosition(vec(x, y))
    for i, name in enumerate(('TX', 'RX', 'EN'), 1):
        tp = footprint(f'TP{i}', None, (0, 0))
        circle = k.PCB_SHAPE(tp, k.SHAPE_T_CIRCLE)
        circle.SetLayer(k.F_CrtYd)
        circle.SetCenter(vec(0, 0))
        circle.SetEnd(vec(1, 0))
        circle.SetWidth(50000)
        tp.Add(circle)
        smd(tp, '1', 0, 0, 1.5, k.PAD_SHAPE_CIRCLE, (k.F_Cu, k.F_Mask), name)
        tp.SetPosition(vec(20 + 3*i, 80))
    r1 = footprint('R1', (-1.25, -0.75, 1.25, 0.75), (0, 0))
    smd(r1, '1', -0.6, 0, 0.6, k.PAD_SHAPE_RECT, front, 'EN')
    smd(r1, '2', 0.6, 0, 0.6, k.PAD_SHAPE_RECT, front)
    r1.SetPosition(vec(40, 90))
    for i in range(100, 100 + bulk):
        fp = footprint(f'R{i}', (-0.5, -0.5, 0.5, 0.5), (0, 0))
        smd(fp, '1', 0, 0, 0.4, k.PAD_SHAPE_RECT, front, f'N{i}')
        fp.SetPosition(vec(i % 70, 60 + i // 70))
    k.SaveBoard(str(path), board)
    return path


def netlist(refs, nets):
    comps = ''.join(f'<comp ref="{r}"/>' for r in refs)
    body = ''.join(f'<net name="{n}">' + ''.join(f'<node ref="{r}" pin="{p}"/>' for r, p in nodes) + '</net>'
                   for n, nodes in nets.items())
    return f'<export><components>{comps}</components><nets>{body}</nets></export>'


POD_NETS = {'TX': [('J1', '1'), ('TP1', '1')], 'RX': [('J1', '2'), ('TP2', '1')],
            'EN': [('U1', '1'), ('TP3', '1'), ('R1', '1')]}
POD_REFS = ('J1', 'J2', 'R1', 'TP1', 'TP2', 'TP3', 'U1')


class NativeResidualsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-ee212-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()

    def config(self, source, **policy):
        return {'policy': request(fixed_refs=[], **policy), 'authorization': {
            'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'writable_root': str(self.scratch)}}

    def record(self):
        return pod_envelope([fixed('J1', 'bottom', 40, 13.5, (('bottom', 2.55),)),
                             fixed('J2', 'right', 28, 4.56, (('right', 9.73),), 'through_edge'),
                             fixed('U1', 'top', 62.5, 6.5, (('top', 21.25), ('right', 6.5)))],
                            ((4, 4), (34, 4), (76, 46), (4, 46)))

    GROUPS = [{'id': 'programming-probes', 'references': ['TP1', 'TP2', 'TP3'],
               'region': {'polygons': [box(8, 6, 30, 18)]}},
              {'id': 'en-pullup', 'references': ['R1'], 'anchor': {'point_nm': [74 * MM, 40 * MM]},
               'max_distance_nm': 3 * MM}]

    def test_writer_places_probes_beside_declared_overhangs_and_board_only_holes(self):
        from agent_ee.placement_probe import extract
        from agent_ee.placer_runner import place_candidate
        # H2 sits at (34, 4): the seed's (76, 4) lies inside its own U1 courtyard.
        source = native_pod(self.root / 'pod.kicad_pcb', holes=((4, 4), (34, 4), (76, 46), (4, 46)))
        xml = self.root / 'netlist.xml'
        xml.write_text(netlist(POD_REFS, POD_NETS))
        facts = extract(source)
        self.assertEqual({fp['ref']: fp['courtyards']['F']['approximation']['tolerance_nm']
                          for fp in facts['footprints'] if fp['ref'].startswith('TP')},
                         {'TP1': 5000, 'TP2': 5000, 'TP3': 5000})
        before = fresh_pad_layers(source, self.root / 'before.json')
        declarations = envelope_overhangs(self.record())
        config = self.config(source, gap_nm=250_000, step_budget=200000, fixed_overhangs=declarations,
                             groups=self.GROUPS)
        result = place_candidate(source, xml, self.scratch / 'proposal', config)
        self.assertEqual((result['status'], result['judgment']['verdict'], result['native_courtyard_overlaps']),
                         ('placed', 'pass', []), result)
        self.assertEqual(result['judgment']['group_grading']['status'], 'pass')
        self.assertEqual(sorted((d['ref'], d['side']) for d in result['judgment']['declared_overhangs']),
                         [('J1', 'F'), ('J2', 'B'), ('J2', 'F'), ('U1', 'F')])
        placed = self.scratch / 'proposal/candidate.kicad_pcb'
        after = {fp['ref']: fp for fp in extract(placed)['footprints']}
        initial = {fp['ref']: fp for fp in facts['footprints']}
        for ref in ('U1', 'J1', 'J2', 'H1', 'H2', 'H3', 'H4'):
            self.assertEqual(after[ref], initial[ref], ref)
        for ref in ('TP1', 'TP2', 'TP3'):
            x, y = after[ref]['position_nm']
            self.assertTrue(9 * MM <= x <= 29 * MM and 7 * MM <= y <= 17 * MM, (ref, x, y))
        self.assertLessEqual(math.dist(after['R1']['position_nm'], (74 * MM, 40 * MM)), 3 * MM)
        # Exact pad layer sets after a fresh-process reload: fixed, placed and holes.
        layers = fresh_pad_layers(placed, self.root / 'after.json')
        self.assertEqual(layers, before)
        through = sorted(['F.Cu', 'B.Cu', *[f'In{i}.Cu' for i in range(1, 31)], 'F.Mask', 'B.Mask'])
        smd = ['F.Cu', 'F.Mask', 'F.Paste']
        self.assertEqual(layers, {'J1.1': through, 'J1.2': through, **{f'H{i}.': through for i in range(1, 5)},
                                  'U1.1': smd, 'J2.1': smd, 'R1.1': smd, 'R1.2': smd,
                                  **{f'TP{i}.1': ['F.Cu', 'F.Mask'] for i in range(1, 4)}})
        # The saved board grades as declared; undeclared, the same overhangs are defects.
        saved = extract(placed)
        self.assertEqual(outline_containment(saved, declarations)['status'], 'pass')
        self.assertEqual(sorted({d['ref'] for d in outline_containment(saved)['defects']}), ['J1', 'J2', 'U1'])
        # Undeclared, the outboard courtyards must fit the board: their front area alone
        # exceeds it, a proved defect; nothing is published.
        refused = place_candidate(source, xml, self.scratch / 'undeclared',
                                  self.config(source, gap_nm=250_000, groups=self.GROUPS))
        self.assertEqual((refused['status'], refused['outcome'], refused['placements']),
                         ('packing_infeasible', 'defect', []))
        self.assertEqual((refused['proof']['kind'], refused['proof']['side']), ('courtyard_area', 'F'))
        self.assertFalse((self.scratch / 'undeclared').exists())
        # With J1 and J2 declared but not U1, the remaining outboard part is named.
        named = place_candidate(source, xml, self.scratch / 'u1-undeclared',
                                self.config(source, gap_nm=250_000, groups=self.GROUPS,
                                            fixed_overhangs=[d for d in declarations if d['reference'] != 'U1']))
        self.assertEqual((named['status'], named['outcome']), ('fixed_conflict', 'unknown'))
        self.assertIn('fixed placement U1 conflicts', named['reason'])
        self.assertFalse((self.scratch / 'u1-undeclared').exists())

    def probe_pair(self, source, name, offset_nm):
        """TP1 at (10, 10) mm and TP2 offset from it, saved and native-DRC checked:
        (native overlap pairs, judge overlap pairs, judge TP1/TP2 unknowns, predicate)."""
        import pcbnew as k
        from agent_ee.placement_probe import extract
        board = k.LoadBoard(str(source))
        for fp in board.GetFootprints():
            if fp.GetReference() in ('TP1', 'TP2'):
                dx, dy = offset_nm if fp.GetReference() == 'TP2' else (0, 0)
                fp.SetPosition(k.VECTOR2I(10 * MM + dx, 10 * MM + dy))
        path = self.root / f'{name}.kicad_pcb'
        k.SaveBoard(str(path), board)
        report = self.root / f'{name}-drc.json'
        run = subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
                              '--output', str(report), str(path)], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        facts = extract(path)
        ids = {fp['uuid']: fp['ref'] for fp in facts['footprints']}
        drc = json.loads(report.read_text())
        native = sorted(sorted(ids[i['uuid']] for i in v['items']) for v in drc['violations']
                        if v['type'] == 'courtyards_overlap')
        judged = judge(facts)
        overlaps = [f['refs'] for f in judged['hard_failures'] if f['kind'] == 'courtyard_overlap']
        band = [u['kind'] for u in judged['unknown'] if u.get('refs') == ['TP1', 'TP2']]
        return native, overlaps, band, courtyard_overlap(facts, drc)

    def test_circular_courtyard_overlap_native_defect_and_clear_pass(self):
        source = native_pod(self.root / 'pod.kicad_pcb', holes=((4, 4), (34, 4), (76, 46), (4, 46)))
        outcomes = {}
        for name, x in (('overlap', 11.5), ('clear', 12.05)):
            native, overlaps, _, predicate = self.probe_pair(source, name, (round((x - 10) * MM), 0))
            outcomes[name] = (native, overlaps, predicate['status'])
        self.assertEqual(outcomes['overlap'], ([['TP1', 'TP2']], [['TP1', 'TP2']], 'defect'))
        self.assertEqual(outcomes['clear'], ([], [], 'pass'))

    def test_circle_tolerance_band_stays_unknown_under_native_drc_silence(self):
        # Reviewer probe 1e: 1 mm circles 1.998 mm apart overlap by 2 um and native DRC
        # reports nothing. Along the axis the inner bounds overlap (the judge proves the
        # defect, the predicate names the disagreement); at the midchord only the
        # tolerance band does, and both stay unknown. Silence never upgrades a band to pass.
        source = native_pod(self.root / 'pod.kicad_pcb', holes=((4, 4), (34, 4), (76, 46), (4, 46)))
        cases = {'axis': (1_998_000, 0),
                 'midchord': (round(1_998_000 * math.cos(MIDCHORD)), round(1_998_000 * math.sin(MIDCHORD))),
                 'band_clear': (2_002_000, 0), 'clear': (2_010_000, 0)}
        outcomes = {}
        for name, offset in cases.items():
            native, overlaps, band, predicate = self.probe_pair(source, name, offset)
            outcomes[name] = (native, overlaps, band, predicate['status'], [u['kind'] for u in predicate['unknown']])
        self.assertEqual(outcomes['axis'], ([], [['TP1', 'TP2']], [], 'unknown', ['helper_disagreement']))
        self.assertEqual(outcomes['midchord'], ([], [], ['courtyard_approximation'], 'unknown',
                                                ['courtyard_approximation']))
        # 2 um clear in truth, inside the outer bounds' 5 um: unknown, not pass.
        self.assertEqual(outcomes['band_clear'], ([], [], ['courtyard_approximation'], 'unknown',
                                                  ['courtyard_approximation']))
        self.assertEqual(outcomes['clear'], ([], [], [], 'pass', []))

    def test_board_only_holes_are_excluded_at_seed_scale_and_only_under_the_rule(self):
        import pcbnew as k
        from agent_ee.placement_probe import extract
        from agent_ee.placer_runner import _board_only, _netlist_binding
        bulk = 101  # 7 parts here + 101 = the seed's 108 electrical components; + 4 holes = 112.
        source = native_pod(self.root / 'scale.kicad_pcb', bulk=bulk)
        refs = [*POD_REFS, *[f'R{i}' for i in range(100, 100 + bulk)]]
        nets = {**POD_NETS, **{f'N{i}': [(f'R{i}', '1')] for i in range(100, 100 + bulk)}}
        xml = netlist(refs, nets)
        facts = extract(source)
        board = k.LoadBoard(str(source))
        self.assertEqual((len(facts['footprints']), len(refs)), (112, 108))
        self.assertEqual(_board_only(board), {'H1', 'H2', 'H3', 'H4'})
        _netlist_binding(xml, facts, _board_only(board))
        with self.assertRaisesRegex(ValueError, 'component inventory differs'):
            _netlist_binding(xml, facts)
        # A hole without the native board-only attribute is not excluded.
        hole = next(fp for fp in board.GetFootprints() if fp.GetReference() == 'H3')
        hole.SetAttributes(k.FP_EXCLUDE_FROM_BOM)
        plain = self.root / 'plain.kicad_pcb'
        k.SaveBoard(str(plain), board)
        reloaded = k.LoadBoard(str(plain))
        self.assertEqual(_board_only(reloaded), {'H1', 'H2', 'H4'})
        with self.assertRaisesRegex(ValueError, 'component inventory differs'):
            _netlist_binding(xml, extract(plain), _board_only(reloaded))
        # A board-only hole with a net, or one the export names, is refused.
        netted = copy.deepcopy(facts)
        next(fp for fp in netted['footprints'] if fp['ref'] == 'H1')['pads'][0]['net'] = 'N100'
        with self.assertRaisesRegex(ValueError, 'board-only footprint has a net'):
            _netlist_binding(xml, netted, _board_only(board))
        with self.assertRaisesRegex(ValueError, 'board-only footprint has a net or a native netlist component'):
            _netlist_binding(netlist([*refs, 'H1'], nets), facts, _board_only(board))


class NativeGroupCheckTest(unittest.TestCase):
    """placement-groups/1 through the registered coordinator check on owned RC variants."""
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-ee212-groups-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.count = self.runs = 0

    def test_registered_group_check_passes_and_names_defects(self):
        import test_placement_gates_native as gates
        groups = {'schema': placement_groups.SCHEMA, 'groups': [
            {'id': 'filter', 'references': ['R1', 'C1'], 'region': {'polygons': [box(102, 98, 113, 102)]},
             'anchor': {'reference': 'J1'}, 'max_distance_nm': 11 * MM}]}
        groups['groups'] = authored_groups(groups['groups'], 'tests/test_placer_residuals.py: native group fixture')
        outcomes = {}
        for name, move in (('pass', None), ('defect', {'C1': (114, 104)})):
            source = gates.NativePlacementGates.source(self, move=move, extra={
                'project/groups.json': json.dumps(groups).encode()})
            spec = gates.placement('groups', 'placement-groups/1', groups='project/groups.json')
            run, results = gates.NativePlacementGates.run_scope(self, source, [gates.DRC_STAGE, spec, gates.DRC_FULL])
            outcomes[name] = results['groups']
            self.assertIn('work/groups/placement-judgment.json', results['groups']['evidence'])
        self.assertEqual((outcomes['pass']['execution'], outcomes['pass']['outcome']), ('ok', 'pass'), outcomes['pass'])
        defect = outcomes['defect']
        self.assertEqual((defect['execution'], defect['outcome']), ('ok', 'defect'), defect)
        self.assertEqual([(d['kind'], d['ref']) for d in defect['details']['defects']],
                         [('outside_group_region', 'C1'), ('group_anchor_distance', 'C1')])
        self.assertEqual([(m['ref'], m['distance_mm']) for m in defect['details']['measured']],
                         [('C1', round(math.hypot(14, 4), 6)), ('R1', 5.0)])
        self.assertEqual(defect['details']['gate_findings'][0]['entity'], 'C1')


if __name__ == '__main__':
    unittest.main()

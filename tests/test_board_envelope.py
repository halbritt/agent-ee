"""Board envelope record: exact structure, provenance and obligation binding."""
import copy
import decimal
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.board_envelope import (BINDINGS, CHECK_KINDS, board_facts, derive, obligation_coverage,
                                     require_obligations, round_trip, stage_one_requirements, validate)
from agent_ee.storage import canonical, digest
from test_fab_profile import owned_profile
from toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
RC_ENVELOPE = ROOT / 'fixtures/board-envelope/rc.envelope.json'
RC_BOARD = ROOT / 'fixtures/rc/control/rc_filter.kicad_pcb'


def raw(value):
    return json.dumps(value).encode()


def rc():
    return json.loads(RC_ENVELOPE.read_text())


def l_shape():
    """Concave outline: a 20x20 square with the top-right 10x10 quadrant removed."""
    corners = [(0, 0), (10, 0), (10, 10), (20, 10), (20, 20), (0, 20)]
    names = ['a', 'b', 'c', 'd', 'e', 'f']
    value = rc()
    value['outline'] = {'edges': [{'name': n, 'start': list(corners[i]), 'end': list(corners[(i + 1) % 6])}
                                  for i, n in enumerate(names)]}
    value['fixed_footprints'] = []
    return value


def with_rotation(token):
    """The RC envelope with J1's rotation written as this exact JSON number token."""
    data = RC_ENVELOPE.read_bytes()
    assert data.count(b'"rotation_deg": 0,') == 1
    return data.replace(b'"rotation_deg": 0,', b'"rotation_deg": ' + token.encode() + b',')


def rotated_board(token):
    board = RC_BOARD.read_bytes()
    assert board.count(b'(at 100 100)') == 1
    return board.replace(b'(at 100 100)', b'(at 100 100 ' + token.encode() + b')')


def fixed_status(trip):
    return {r['field']: r for r in trip['fields']}['/fixed_footprints/0']


def mapped_scope(requirements, kinds=None):
    """Each obligation mapped to a check of its first kind; fixed footprints to connector-edge/1."""
    checks = [{'id': 'drc', 'kind': 'drc'},
              {'id': 'placement', 'kind': 'placement',
               'params': {'criterion': 'outline-containment/1', 'drc_check': 'drc'}},
              {'id': 'connector', 'kind': 'placement',
               'params': {'criterion': 'connector-edge/1', 'drc_check': 'drc', 'envelope': ENVELOPE_PATH}},
              {'id': 'fab', 'kind': 'fab_profile', 'params': {'fab_profile': 'requirements/fab-profile.json'}},
              {'id': 'package', 'kind': 'fab_package'}]

    def check(r):
        kind = (kinds or {}).get(r['id'], r['check_kinds'][0])
        if kind == 'placement' and r['source']['locator'].startswith('/fixed_footprints/'):
            return 'connector'
        return {'placement': 'placement', 'fab_profile': 'fab', 'fab_package': 'package'}[kind]
    return {'schema': 'agent-ee.scope/3', 'checks': checks, 'baseline': {'requirements': [
        {'id': r['id'], 'statement': r['statement'], 'source': dict(r['source']), 'intent': 'acceptance',
         'checks': [check(r)]} for r in requirements['requirements']]}}


ENVELOPE_PATH = 'requirements/envelope.json'
PROFILE_ID = 'R-envelope-rc-filter-fab-profile'


def owned_capture(root, specs, requirement=PROFILE_ID, prefix=''):
    """Owned synthetic profile and source text (not a real fabricator), hashed as captured.

    Returns (profile bytes, files, inputs); the profile cites its source at
    prefix + 'owned-capabilities.txt' relative to root.
    """
    target = root / prefix if prefix else root
    target.mkdir(parents=True, exist_ok=True)
    document = owned_profile(target, specs)
    for limit in document['limits']:
        limit['requirement'] = requirement
        limit['source']['path'] = prefix + limit['source']['path']
    data = json.dumps(document).encode()
    (target / 'fab-profile.json').write_bytes(data)
    files = {p.relative_to(root).as_posix(): digest(p) for p in root.rglob('*') if p.is_file()}
    return data, files, root


def profiled(profile):
    value = rc()
    value['fab_profile'] = {'name': 'owned_standard_2026-10', 'sha256': hashlib.sha256(profile).hexdigest()}
    return value


LAYERS_AND_SIZE = [('max_layers', 2, 'count', 'minimum'), ('max_board_mm', [20, 12], 'mm', 'minimum')]


class EnvelopeValidationTests(unittest.TestCase):
    def test_rc_envelope_validates_structurally_with_profile_unresolved(self):
        record = validate(RC_ENVELOPE.read_bytes())
        self.assertEqual(record['structural'], 'pass')
        self.assertEqual(record['envelope_sha256'], hashlib.sha256(RC_ENVELOPE.read_bytes()).hexdigest())
        # No captured fab profile exists: never a pass, never a default profile.
        self.assertEqual(record['outcome'], 'unknown')
        self.assertFalse(record['acceptance_ready'])
        self.assertEqual([u['predicate'] for u in record['unresolved']], ['fab_profile_selected'])
        self.assertEqual(record['fab_profile'], {'status': 'unselected'})
        self.assertTrue(record['outline']['axis_aligned_rectangle'])
        self.assertEqual(record['outline']['area_mm2'], '240')
        self.assertEqual(record['outline']['bounds_mm'], {'min': ['96', '96'], 'max': ['116', '108']})
        self.assertEqual({e['name']: e['outward'] for e in record['outline']['edges']},
                         {'top': 'y-', 'right': 'x+', 'bottom': 'y+', 'left': 'x-'})
        self.assertEqual(record['copper_layers'], ['F.Cu', 'B.Cu'])
        [j1] = record['fixed_footprints']
        self.assertEqual((j1['anchor_mm'], j1['layer'], j1['mating_axis']), (['100', '100'], 'F.Cu', 'z-'))

    def test_reversed_winding_keeps_inward_and_outward_directions(self):
        value = rc()
        edges = value['outline']['edges']
        value['outline']['edges'] = [{'name': e['name'], 'start': e['end'], 'end': e['start']} for e in reversed(edges)]
        # Left edge now runs from (96, 96) toward (96, 108): 4 mm along, 4 mm inward is J1.
        value['fixed_footprints'][0]['position_mm'] = 4
        record = validate(raw(value))
        self.assertEqual(record['outline']['winding'], 'negative_shoelace')
        self.assertEqual({e['name']: e['outward'] for e in record['outline']['edges']},
                         {'top': 'y-', 'right': 'x+', 'bottom': 'y+', 'left': 'x-'})
        self.assertEqual(record['fixed_footprints'][0]['anchor_mm'], ['100', '100'])

    def test_open_outline_is_refused(self):
        value = rc()
        value['outline']['edges'][1]['start'] = [116, 96.000001]
        with self.assertRaisesRegex(ValueError, 'outline is open: edge top ends'):
            validate(raw(value))
        value = rc()
        value['outline']['edges'][3]['end'] = [96, 97]
        with self.assertRaisesRegex(ValueError, 'outline is open: edge left ends'):
            validate(raw(value))

    def test_self_intersecting_degenerate_and_overlapping_outlines_are_refused(self):
        cases = {
            'bow tie': [(0, 0), (10, 10), (10, 0), (0, 10)],
            'collinear zero area': [(0, 0), (10, 0), (5, 0)],
            'backtracking spike': [(0, 0), (10, 0), (10, 10), (10, 5), (0, 10)],
            'touching vertex': [(0, 0), (10, 0), (5, 5), (10, 10), (0, 10), (5, 5)],
        }
        for label, corners in cases.items():
            value = rc()
            value['fixed_footprints'] = []
            value['outline'] = {'edges': [{'name': f'e{i}', 'start': list(corners[i]),
                                           'end': list(corners[(i + 1) % len(corners)])}
                                          for i in range(len(corners))]}
            with self.subTest(label), self.assertRaisesRegex(ValueError, 'self-intersecting|zero area'):
                validate(raw(value))
        value = rc()
        value['outline']['edges'][0]['end'] = value['outline']['edges'][0]['start']
        value['outline']['edges'][1]['start'] = value['outline']['edges'][0]['start']
        with self.assertRaisesRegex(ValueError, 'zero-length edge'):
            validate(raw(value))

    def test_edge_name_not_in_outline_is_refused(self):
        value = rc()
        value['fixed_footprints'][0]['edge'] = 'west'
        with self.assertRaisesRegex(ValueError, "edge 'west' is not an outline edge name"):
            validate(raw(value))

    def test_connector_position_outside_its_edge_is_refused(self):
        value = rc()
        value['fixed_footprints'][0]['position_mm'] = 12
        validate(raw(value))  # the far end of the 12 mm edge is still on it
        value['fixed_footprints'][0]['position_mm'] = 12.000001
        with self.assertRaisesRegex(ValueError, 'position 12.000001 mm is outside edge left of length 12 mm'):
            validate(raw(value))
        value['fixed_footprints'][0]['position_mm'] = -1
        with self.assertRaisesRegex(ValueError, 'nonnegative'):
            validate(raw(value))
        value = rc()
        value['fixed_footprints'][0]['inset_mm'] = 20.000001
        with self.assertRaisesRegex(ValueError, 'anchor .* is outside the outline'):
            validate(raw(value))

    def test_duplicate_references_and_identifiers_are_refused(self):
        value = rc()
        value['fixed_footprints'].append(copy.deepcopy(value['fixed_footprints'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate reference or identifier: J1'):
            validate(raw(value))
        value = rc()
        value['mounting_holes'] = [{'id': 'J1', 'at': [110, 104], 'drill_mm': 1, 'plated': False}]
        with self.assertRaisesRegex(ValueError, 'duplicate reference or identifier: J1'):
            validate(raw(value))
        value = rc()
        value['outline']['edges'][1]['name'] = 'top'
        with self.assertRaisesRegex(ValueError, 'duplicate edge name: top'):
            validate(raw(value))

    def test_unknown_fields_duplicate_keys_and_inexact_numbers_are_refused(self):
        mutations = {
            'top-level field': lambda v: v.update(notes='x'),
            'outline field': lambda v: v['outline'].update(arcs=[]),
            'edge field': lambda v: v['outline']['edges'][0].update(radius=1),
            'stackup field': lambda v: v['stackup'].update(material='FR4'),
            'copper weight on dielectric': lambda v: v['stackup']['layers'][1].update(copper_weight_oz=1),
            'fixed field': lambda v: v['fixed_footprints'][0].update(pitch=2.54),
            'missing field': lambda v: v.pop('keepouts'),
            'schema': lambda v: v.update(schema='agent-ee.envelope/2'),
        }
        for label, mutate in mutations.items():
            value = rc()
            mutate(value)
            with self.subTest(label), self.assertRaises(ValueError):
                validate(raw(value))
        text = RC_ENVELOPE.read_bytes()
        for label, data in {
            'duplicate key': text.replace(b'"layer_count": 2,', b'"layer_count": 2, "layer_count": 2,'),
            'NaN': text.replace(b'"inset_mm": 4', b'"inset_mm": NaN'),
            'sub-nanometre': text.replace(b'"inset_mm": 4', b'"inset_mm": 4.0000001'),
            'boolean number': text.replace(b'"inset_mm": 4', b'"inset_mm": true'),
            'string number': text.replace(b'"inset_mm": 4', b'"inset_mm": "4"'),
            'not UTF-8': text.replace(b'rc-filter', b'rc-\xff'),
        }.items():
            self.assertNotEqual(data, text, label)
            with self.subTest(label), self.assertRaises(ValueError):
                validate(data)

    def test_resource_bounds_are_refused_before_quadratic_checks(self):
        value = rc()
        value['outline']['edges'] = [{'name': f'e{i}', 'start': [i, 0], 'end': [i + 1, 0]} for i in range(1025)]
        with self.assertRaisesRegex(ValueError, 'outline needs 3 to 1024 named edges'):
            validate(raw(value))
        value = rc()
        value['keepouts'] = [{'id': f'K{i}', 'polygon': [], 'layers': [], 'forbid': []} for i in range(1025)]
        with self.assertRaisesRegex(ValueError, 'at most 1024'):
            validate(raw(value))
        value = rc()
        value['keepouts'] = [{'id': 'K1', 'polygon': [[100, 100]] * 1025, 'layers': ['F.Cu'], 'forbid': ['vias']}]
        with self.assertRaisesRegex(ValueError, 'keepout K1 polygon needs 3 to 1024 vertices'):
            validate(raw(value))
        with self.assertRaisesRegex(ValueError, 'at most 1048576'):
            validate(RC_ENVELOPE.read_bytes() + b' ' * 1024 * 1024)

    def test_layer_count_and_stackup_must_agree_exactly(self):
        value = rc()
        value['layer_count'] = 4
        with self.assertRaisesRegex(ValueError, "layer_count 4 requires copper layers"):
            validate(raw(value))
        for count in (1, 33, True, 2.0):
            value = rc()
            value['layer_count'] = count
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'integer from 2 to 32'):
                validate(raw(value))
        value = rc()
        value['stackup']['thickness_mm'] = 1.6000001
        with self.assertRaisesRegex(ValueError, 'exact to 1 nm'):
            validate(raw(value))
        value['stackup']['thickness_mm'] = 1.601
        with self.assertRaisesRegex(ValueError, 'sum to 1.6 mm, not thickness_mm 1.601'):
            validate(raw(value))
        value = rc()
        value['stackup']['layers'] = value['stackup']['layers'][:2]
        with self.assertRaisesRegex(ValueError, 'alternate'):
            validate(raw(value))
        value = rc()
        value['stackup']['layers'][2]['name'] = 'In1.Cu'
        with self.assertRaisesRegex(ValueError, 'requires copper layers'):
            validate(raw(value))
        four = rc()
        four['layer_count'] = 4
        four['stackup'] = {'thickness_mm': 1.6, 'layers': [
            {'name': 'F.Cu', 'kind': 'copper', 'thickness_mm': 0.035, 'copper_weight_oz': 1},
            {'name': 'prepreg-1', 'kind': 'dielectric', 'thickness_mm': 0.2},
            {'name': 'In1.Cu', 'kind': 'copper', 'thickness_mm': 0.0175, 'copper_weight_oz': 0.5},
            {'name': 'core', 'kind': 'dielectric', 'thickness_mm': 1.095},
            {'name': 'In2.Cu', 'kind': 'copper', 'thickness_mm': 0.0175, 'copper_weight_oz': 0.5},
            {'name': 'prepreg-2', 'kind': 'dielectric', 'thickness_mm': 0.2},
            {'name': 'B.Cu', 'kind': 'copper', 'thickness_mm': 0.035, 'copper_weight_oz': 1}]}
        self.assertEqual(validate(raw(four))['copper_layers'], ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'])

    def test_side_insertion_and_oblique_edges(self):
        value = rc()
        value['fixed_footprints'][0]['insertion'] = 'from_bottom'
        with self.assertRaisesRegex(ValueError, 'front side cannot be mated from_bottom'):
            validate(raw(value))
        value['fixed_footprints'][0].update(side='back', insertion='through_edge', inset_mm=0)
        record = validate(raw(value))['fixed_footprints'][0]
        self.assertEqual((record['layer'], record['mating_axis'], record['anchor_mm']), ('B.Cu', 'x-', ['96', '100']))
        value['fixed_footprints'][0]['insertion'] = 'sideways'
        with self.assertRaisesRegex(ValueError, 'insertion must be one of'):
            validate(raw(value))
        value = rc()
        value['outline'] = {'edges': [{'name': 'base', 'start': [0, 10], 'end': [10, 10]},
                                      {'name': 'slope', 'start': [10, 10], 'end': [0, 0]},
                                      {'name': 'side', 'start': [0, 0], 'end': [0, 10]}]}
        value['fixed_footprints'][0].update(edge='slope', position_mm=1, inset_mm=0)
        with self.assertRaisesRegex(ValueError, 'oblique'):
            validate(raw(value))
        value['fixed_footprints'][0].update(edge='base')
        self.assertFalse(validate(raw(value))['outline']['axis_aligned_rectangle'])

    def test_mounting_holes_must_lie_inside_with_their_radius(self):
        value = rc()
        value['mounting_holes'] = [{'id': 'H1', 'at': [98, 98], 'drill_mm': 4, 'plated': False}]
        self.assertEqual(validate(raw(value))['mounting_holes'][0]['drill_mm'], '4')
        value['mounting_holes'][0]['drill_mm'] = 4.000002
        with self.assertRaisesRegex(ValueError, 'mounting hole H1 is not inside'):
            validate(raw(value))
        value['mounting_holes'][0].update(drill_mm=1, at=[120, 100])
        with self.assertRaisesRegex(ValueError, 'mounting hole H1 is not inside'):
            validate(raw(value))
        value['mounting_holes'][0].update(at=[100, 100], plated='no')
        with self.assertRaisesRegex(ValueError, 'plated must be boolean'):
            validate(raw(value))

    def test_keepouts_must_be_contained_in_a_concave_outline(self):
        value = l_shape()
        value['keepouts'] = [{'id': 'K1', 'polygon': [[0, 10], [10, 10], [10, 20], [0, 20]],
                              'layers': ['F.Cu', 'B.Cu'], 'forbid': ['tracks', 'footprints']}]
        record = validate(raw(value))
        self.assertFalse(record['outline']['axis_aligned_rectangle'])
        self.assertEqual(record['keepouts'][0]['forbid'], ['footprints', 'tracks'])
        # A diagonal touching only the reflex vertex (10, 10) stays in the closed outline.
        value['keepouts'][0]['polygon'] = [[0, 0], [20, 20], [0, 20]]
        self.assertEqual(validate(raw(value))['keepouts'][0]['polygon_mm'], [['0', '0'], ['20', '20'], ['0', '20']])
        # Every vertex is inside, but the edge from (5,5) to (15,11) crosses the notch at x=10, y=8.
        value['keepouts'][0]['polygon'] = [[5, 5], [15, 11], [5, 15]]
        with self.assertRaisesRegex(ValueError, 'keepout K1 is not contained'):
            validate(raw(value))
        # Contact at the reflex vertex followed by a piece inside the notch.
        value['keepouts'][0]['polygon'] = [[5, 15], [15, 5], [5, 5]]
        with self.assertRaisesRegex(ValueError, 'keepout K1 is not contained'):
            validate(raw(value))
        for field, bad in (('layers', ['In1.Cu']), ('layers', []), ('forbid', ['zones']),
                           ('forbid', ['tracks', 'tracks'])):
            value = l_shape()
            value['keepouts'] = [{'id': 'K1', 'polygon': [[1, 1], [9, 1], [9, 9]],
                                  'layers': ['F.Cu'], 'forbid': ['tracks'], field: bad}]
            with self.subTest(field=field, bad=bad), self.assertRaisesRegex(ValueError, f'keepout K1 {field}'):
                validate(raw(value))


class EnvelopeExactAngleTests(unittest.TestCase):
    """Rotations are exact decimals: never rounded, never given an implicit tolerance."""

    def test_nonzero_near_zero_rotation_is_preserved_and_never_matches_zero(self):
        exact = '0.00000000000000000000000000001'
        for token in (exact, '1E-29', '1e-29'):
            data = with_rotation(token)
            with self.subTest(token=token):
                self.assertEqual(validate(data)['fixed_footprints'][0]['rotation_deg'], exact)
                statement = json.loads(derive(data, source_path='e.json')['envelope-requirements.json'])
                self.assertIn(f'rotation {exact} deg', statement['requirements'][-1]['statement'])
                # The saved RC J1 is at exactly 0 degrees.
                trip = round_trip(data, RC_BOARD.read_bytes())
                self.assertEqual((fixed_status(trip)['status'], trip['outcome']), ('mismatch', 'mismatch'))
                self.assertEqual(fixed_status(trip)['detail']['board']['rotation_deg'], '0')
                self.assertEqual(fixed_status(round_trip(data, rotated_board(exact)))['status'], 'match')
        self.assertEqual(fixed_status(round_trip(RC_ENVELOPE.read_bytes(), rotated_board(exact)))['status'],
                         'mismatch')

    def test_saved_rotations_normalize_exactly_modulo_360(self):
        near = '359.99999999999999999999999999999'
        for declared, saved, status in (
                (near, '-0.00000000000000000000000000001', 'match'),
                (near, '0', 'mismatch'), (near, '360', 'mismatch'),
                (near, '-0.0000000000000000000000000001', 'mismatch'),
                ('0', '360', 'match'), ('0', '-0', 'match'), ('0', '-360', 'match'), ('0', '720', 'match'),
                ('270', '-90', 'match'), ('270', '-90.00000000000000000000000000001', 'mismatch'),
                ('90', '450', 'match'), ('90', '-270', 'match'), ('90', '90.000', 'match'),
                ('90', '89.99999999999999999999999999999', 'mismatch')):
            with self.subTest(declared=declared, saved=saved):
                trip = round_trip(with_rotation(declared), rotated_board(saved))
                self.assertEqual(fixed_status(trip)['status'], status)
        self.assertEqual(fixed_status(round_trip(with_rotation('0'), rotated_board('-0.00000000000000000000000000001')))
                         ['detail']['board']['rotation_deg'], near)

    def test_unrepresentable_rotations_and_weights_are_refused_not_rounded(self):
        # 32 places are kept exactly; a 33rd significant place is refused.
        self.assertEqual(validate(with_rotation('1E-32'))['fixed_footprints'][0]['rotation_deg'],
                         '0.' + '0' * 31 + '1')
        # Trailing zeros carry no value and are not refused.
        self.assertEqual(validate(with_rotation('90.' + '0' * 40))['fixed_footprints'][0]['rotation_deg'], '90')
        for token in ('1E-33', '0.' + '0' * 32 + '1', '1E-999999999', '359.' + '9' * 33):
            with self.subTest(token=token[:40]), self.assertRaisesRegex(ValueError, 'not exactly representable'):
                validate(with_rotation(token))
        for token in ('360', '-1E-29', '1E+999999999'):
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, r'rotation_deg must be in \[0, 360\)'):
                validate(with_rotation(token))
        data = RC_ENVELOPE.read_bytes()
        self.assertEqual(data.count(b'"copper_weight_oz": 1}'), 2)
        for token in (b'1E-999999999', b'1E+999999999', b'1E-33'):
            with self.subTest(weight=token), self.assertRaisesRegex(ValueError, 'copper_weight_oz is not exactly'):
                validate(data.replace(b'"copper_weight_oz": 1}', b'"copper_weight_oz": ' + token + b'}'))
        weighted = validate(data.replace(b'"copper_weight_oz": 1}', b'"copper_weight_oz": 5E-1}'))
        self.assertEqual(weighted['stackup']['layers'][0]['copper_weight_oz'], '0.5')
        # A saved rotation that cannot be represented, or is not a number, refuses the comparison.
        for token in ('1E-33', 'abc', 'NaN', 'Infinity', '9' * 129):
            with self.subTest(saved=token[:20]), self.assertRaisesRegex(ValueError, 'J1 rotation'):
                round_trip(RC_ENVELOPE.read_bytes(), rotated_board(token))

    def test_results_do_not_depend_on_the_ambient_decimal_context(self):
        value = rc()
        value['mounting_holes'] = [{'id': 'H1', 'at': [113.000001, 105.123457], 'drill_mm': 1.000001, 'plated': True}]
        value['outline']['edges'][0]['end'] = [116.000001, 96]
        value['outline']['edges'][1]['start'] = [116.000001, 96]
        value['stackup']['layers'][0]['copper_weight_oz'] = decimal.Decimal('0.123456789012345678901234567891')
        data = json.dumps(value, default=str).replace('"0.123456789012345678901234567891"',
                                                     '0.123456789012345678901234567891').encode()
        data = data.replace(b'"rotation_deg": 0,', b'"rotation_deg": 1E-29,')
        expected = (canonical(validate(data)), canonical(round_trip(data, RC_BOARD.read_bytes())),
                    derive(data, source_path='e.json'))
        with decimal.localcontext() as context:
            context.prec = 2
            context.traps[decimal.Inexact] = context.traps[decimal.Rounded] = True
            self.assertEqual((canonical(validate(data)), canonical(round_trip(data, RC_BOARD.read_bytes())),
                              derive(data, source_path='e.json')), expected)
        record = json.loads(expected[0])
        # Only the top-right corner moves: a trapezoid of (20.000001 + 20) / 2 * 12 mm^2.
        self.assertEqual(record['outline']['area_mm2'], '240.000006')
        self.assertEqual(record['outline']['edges'][0]['length_mm'], '20.000001')
        self.assertEqual(record['mounting_holes'][0]['at_mm'], ['113.000001', '105.123457'])
        self.assertEqual(record['stackup']['layers'][0]['copper_weight_oz'], '0.123456789012345678901234567891')


class EnvelopeProfileTests(unittest.TestCase):
    """Profile support comes only from the AGENTEE-75 compiler and its sourced minima."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def capture(self, specs, **kwargs):
        return owned_capture(self.root, specs, **kwargs)

    def test_profile_reference_is_hash_bound_and_malformed_profiles_are_refused(self):
        profile, files, inputs = self.capture(LAYERS_AND_SIZE)
        value = profiled(profile)
        self.assertEqual(validate(raw(value), profile=profile)['fab_profile']['bytes'], 'sha256_match')
        self.assertEqual([u['predicate'] for u in validate(raw(value))['unresolved']], ['fab_profile_bytes'])
        with self.assertRaisesRegex(ValueError, 'do not match the referenced sha256'):
            validate(raw(value), profile=profile + b' ')
        with self.assertRaisesRegex(ValueError, 'references no fab profile'):
            validate(RC_ENVELOPE.read_bytes(), profile=profile)
        for bad, message in ((b'{"schema": "agent-ee.fab-profile/1", "name": "owned"}', 'no defaults'),
                             (b'{', 'Expecting'), (b'\xff', 'UTF-8'),
                             (profile.replace(b'"as_of": "2026-10-17", "limits"',
                                              b'"as_of": "2026-10-17", "as_of": "2026-10-17", "limits"'),
                              'duplicate JSON key')):
            with self.subTest(bad=bad[:30]), self.assertRaisesRegex(ValueError, message):
                validate(raw(profiled(bad)), profile=bad)
        renamed = profiled(profile)
        renamed['fab_profile']['name'] = 'another_fab'
        with self.assertRaisesRegex(ValueError, "named 'owned_standard_2026-10', not the referenced 'another_fab'"):
            validate(raw(renamed), profile=profile, captured=(files, inputs))
        value['fab_profile']['sha256'] = value['fab_profile']['sha256'].upper()
        with self.assertRaisesRegex(ValueError, 'lowercase hex'):
            validate(raw(value))
        value['fab_profile'] = {'name': 'owned'}
        with self.assertRaisesRegex(ValueError, 'fab_profile fields'):
            validate(raw(value))

    def test_captured_sourced_minima_resolve_layer_count_and_fixed_axis_size(self):
        profile, files, inputs = self.capture(LAYERS_AND_SIZE)
        record = validate(raw(profiled(profile)), profile=profile, captured=(files, inputs))
        self.assertEqual((record['outcome'], record['unresolved']), ('pass', []))
        self.assertFalse(record['acceptance_ready'])
        fab = record['fab_profile']
        self.assertTrue(fab['sources_verified'])
        self.assertEqual(fab['source_paths'], ['owned-capabilities.txt'])
        self.assertEqual([(s['predicate'], s['limit_id'], s['actual'], s['status']) for s in fab['support']],
                         [('fab_profile_supports_layer_count', 'limit-0', 2, 'satisfied'),
                          ('fab_profile_supports_board_size', 'limit-1', [20.0, 12.0], 'satisfied')])

    def test_offline_or_changed_sources_leave_support_unresolved_never_assumed(self):
        profile, files, inputs = self.capture(LAYERS_AND_SIZE)
        offline = validate(raw(profiled(profile)), profile=profile)
        self.assertEqual(offline['outcome'], 'unknown')
        self.assertEqual([u['predicate'] for u in offline['unresolved']],
                         ['fab_profile_supports_layer_count', 'fab_profile_supports_board_size'])
        self.assertIn('limit source not verified: captured file missing', offline['unresolved'][0]['reason'])
        self.assertEqual((offline['fab_profile']['support'], offline['fab_profile']['sources_verified']), ([], False))
        (inputs / 'owned-capabilities.txt').write_text('changed after hashing')
        changed = validate(raw(profiled(profile)), profile=profile, captured=(files, inputs))
        self.assertEqual(changed['outcome'], 'unknown')
        self.assertIn('captured file missing or hash changed', changed['unresolved'][0]['reason'])

    def test_unsupported_layer_count_and_rotated_size_are_refused(self):
        profile, files, inputs = self.capture([('max_layers', 1, 'count', 'minimum')])
        with self.assertRaisesRegex(ValueError, r'limit-0 \(max_layers 1\) does not support the envelope: 2'):
            validate(raw(profiled(profile)), profile=profile, captured=(files, inputs))
        # The RC board is 20 mm in x and 12 mm in y; [12, 20] fits only if rotated, which is never granted.
        for bound in ([12, 20], [19.999999, 12], [20, 11.999999]):
            with self.subTest(bound=bound):
                profile, files, inputs = self.capture([('max_layers', 2, 'count', 'minimum'),
                                                       ('max_board_mm', bound, 'mm', 'minimum')])
                with self.assertRaisesRegex(ValueError, 'max_board_mm .* fixed saved axes; no rotation'):
                    validate(raw(profiled(profile)), profile=profile, captured=(files, inputs))

    def test_preferred_or_absent_layer_limits_cannot_decide_support(self):
        for specs in ([('max_layers', 1, 'count', 'preferred')], [('min_trace_mm', 0.1, 'mm', 'minimum')]):
            with self.subTest(specs=specs):
                profile, files, inputs = self.capture(specs)
                record = validate(raw(profiled(profile)), profile=profile, captured=(files, inputs))
                self.assertEqual(record['outcome'], 'unknown')
                self.assertEqual(record['unresolved'], [{'predicate': 'fab_profile_supports_layer_count',
                                                         'reason': 'profile states no minimum-tier max_layers limit'}])


class EnvelopeRequirementTests(unittest.TestCase):
    def requirements(self, data=None, **kwargs):
        artifacts = derive(data or RC_ENVELOPE.read_bytes(), source_path='requirements/envelope.json', **kwargs)
        return artifacts, json.loads(artifacts['envelope-requirements.json'])

    def test_same_envelope_bytes_derive_byte_identical_requirements(self):
        first, _ = self.requirements()
        second, _ = self.requirements()
        self.assertEqual(first, second)
        self.assertEqual(first['envelope.json'], RC_ENVELOPE.read_bytes())
        self.assertEqual(set(first), {'envelope.json', 'envelope-validation.json', 'envelope-requirements.json',
                                      'requirements.md', 'dialog-requirements.json'})

    def test_every_requirement_cites_a_resolving_envelope_field(self):
        artifacts, document = self.requirements()
        envelope = json.loads(RC_ENVELOPE.read_text())
        self.assertEqual(document['envelope_sha256'], hashlib.sha256(RC_ENVELOPE.read_bytes()).hexdigest())
        self.assertFalse(document['complete'])
        ids = [r['id'] for r in document['requirements']]
        self.assertEqual(ids, ['R-envelope-rc-filter-outline', 'R-envelope-rc-filter-layer-count',
                               'R-envelope-rc-filter-stackup', 'R-envelope-rc-filter-fixed-J1'])
        for requirement in document['requirements']:
            locator = requirement['source']['locator']
            self.assertEqual(requirement['source']['path'], 'requirements/envelope.json')
            self.assertTrue(requirement['statement'].startswith(f'Envelope rc-filter field {locator}: '))
            target = envelope
            for part in locator.lstrip('/').split('/'):
                target = target[int(part)] if isinstance(target, list) else target[part]
            self.assertEqual(requirement['intent'], 'acceptance')
            self.assertTrue(requirement['check_kinds'])
            self.assertIn(f"{requirement['id']}: {requirement['statement']}", artifacts['requirements.md'].decode())
        fixed = document['requirements'][-1]['statement']
        self.assertIn('origin 8 mm along edge left and 4 mm inward, at (100, 100) mm, rotation 0 deg', fixed)
        self.assertIn('Unresolved: fab_profile_selected', artifacts['requirements.md'].decode())
        dialog = json.loads(artifacts['dialog-requirements.json'])
        self.assertEqual(dialog['schema'], 'agent-ee.requirements/1')
        self.assertEqual([r['id'] for r in dialog['requirements']], ids)
        self.assertTrue(all(set(r) == {'id', 'text', 'source_question_ids'} and not r['source_question_ids']
                            and 'requirements/envelope.json#/' in r['text'] for r in dialog['requirements']))

    def test_changing_one_field_changes_only_its_cited_requirement(self):
        _, before = self.requirements()
        value = rc()
        value['fixed_footprints'][0]['position_mm'] = 9
        _, after = self.requirements(raw(value))
        pairs = list(zip(before['requirements'], after['requirements']))
        self.assertTrue(all(a == b for a, b in pairs[:-1]))
        self.assertNotEqual(pairs[-1][0]['field_sha256'], pairs[-1][1]['field_sha256'])
        self.assertIn('at (100, 99) mm', pairs[-1][1]['statement'])
        # Reformatting the file changes the envelope hash but no field citation.
        _, reformatted = self.requirements(json.dumps(rc(), indent=1).encode())
        self.assertNotEqual(reformatted['envelope_sha256'], before['envelope_sha256'])
        self.assertEqual(reformatted['requirements'], before['requirements'])

    def test_holes_keepouts_and_profile_become_obligations(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile, _, _ = owned_capture(Path(tmp), LAYERS_AND_SIZE)
        value = profiled(profile)
        value['mounting_holes'] = [{'id': 'H1', 'at': [113, 105], 'drill_mm': 2.2, 'plated': True}]
        value['keepouts'] = [{'id': 'K1', 'polygon': [[106, 104], [110, 104], [110, 107], [106, 107]],
                              'layers': ['B.Cu'], 'forbid': ['vias']}]
        _, document = self.requirements(raw(value), profile=profile)
        by_id = {r['id']: r for r in document['requirements']}
        self.assertEqual(by_id['R-envelope-rc-filter-fab-profile']['source']['locator'], '/fab_profile')
        self.assertEqual(by_id['R-envelope-rc-filter-hole-H1']['source']['locator'], '/mounting_holes/0')
        self.assertIn('plated 2.2 mm hole centred at (113, 105) mm',
                      by_id['R-envelope-rc-filter-hole-H1']['statement'])
        self.assertEqual(by_id['R-envelope-rc-filter-keepout-K1']['source']['locator'], '/keepouts/0')
        self.assertEqual(by_id[PROFILE_ID]['check_kinds'], ['fab_profile'])
        self.assertEqual(by_id['R-envelope-rc-filter-layer-count']['check_kinds'], ['fab_package', 'board_envelope'])
        # Offline the profile's sources are unverified, so support is not assumed.
        self.assertEqual([u['predicate'] for u in document['unresolved']],
                         ['fab_profile_supports_layer_count', 'fab_profile_supports_board_size'])

    def test_unsafe_source_path_is_refused(self):
        for path in ('/abs/envelope.json', '../envelope.json'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                derive(RC_ENVELOPE.read_bytes(), source_path=path)


class EnvelopeObligationTests(unittest.TestCase):
    """A kind name never covers an obligation; only a field binding can."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        profile, self.files, self.inputs = owned_capture(Path(self.tmp.name), LAYERS_AND_SIZE,
                                                         prefix='requirements/')
        self.profile = profile
        self.use(raw(profiled(profile)))
        self.assertTrue(self.document['complete'])

    def use(self, envelope):
        self.files[ENVELOPE_PATH] = hashlib.sha256(envelope).hexdigest()
        artifacts = derive(envelope, source_path=ENVELOPE_PATH, profile=self.profile,
                           captured=(self.files, self.inputs))
        self.record = json.loads(artifacts['envelope-validation.json'])
        self.document = json.loads(artifacts['envelope-requirements.json'])

    def coverage(self, scope):
        return {r['id']: r for r in obligation_coverage(scope, self.document, self.record, self.files)}

    def test_only_profile_and_single_connector_bind_and_the_rest_stay_unknown(self):
        self.assertEqual(set(BINDINGS), {('fab_profile', 'fab_profile'), ('fixed_footprints', 'placement'),
                                       ('outline', 'board_envelope'), ('layer_count', 'board_envelope'),
                                       ('stackup', 'board_envelope')})
        report = self.coverage(mapped_scope(self.document))
        self.assertEqual({k: (r['status'], r['locator'], r['checks']) for k, r in report.items()}, {
            PROFILE_ID: ('bound', '/fab_profile', ['fab']),
            'R-envelope-rc-filter-fixed-J1': ('bound', '/fixed_footprints/0', ['connector']),
            'R-envelope-rc-filter-outline': ('unknown', '/outline', ['placement']),
            'R-envelope-rc-filter-layer-count': ('unknown', '/layer_count', ['package']),
            'R-envelope-rc-filter-stackup': ('unknown', '/stackup', ['package'])})
        self.assertIn('exact native board_envelope outline/1', report['R-envelope-rc-filter-outline']['reason'])
        self.assertIn('exact native board_envelope layers/1',
                      report['R-envelope-rc-filter-layer-count']['reason'])
        with self.assertRaisesRegex(ValueError, 'refused, not dropped: R-envelope-rc-filter-outline .*'
                                                'R-envelope-rc-filter-stackup') as raised:
            require_obligations(mapped_scope(self.document), self.document, self.record, self.files)
        self.assertNotIn(PROFILE_ID, str(raised.exception))
        self.assertNotIn('fixed-J1', str(raised.exception))

    def test_connector_edge_binding_needs_exact_bytes_one_connector_and_one_mapping(self):
        scope = mapped_scope(self.document)
        scope['checks'][2]['params']['criterion'] = 'courtyard-overlap/1'
        with self.assertRaisesRegex(ValueError, "criterion 'courtyard-overlap/1'; only connector-edge/1"):
            obligation_coverage(scope, self.document, self.record, self.files)
        scope = mapped_scope(self.document)
        scope['checks'][2]['params']['envelope'] = 'requirements/fab-profile.json'
        with self.assertRaisesRegex(ValueError, 'envelope is not these captured bytes'):
            obligation_coverage(scope, self.document, self.record, self.files)
        scope = mapped_scope(self.document)
        scope['baseline']['requirements'].append({'id': 'R-other', 'statement': 'Other', 'intent': 'acceptance',
                                                  'source': {'path': 'x', 'locator': 'y'}, 'checks': ['connector']})
        with self.assertRaisesRegex(ValueError, r"also mapped by \['R-other'\]"):
            obligation_coverage(scope, self.document, self.record, self.files)
        # Two fixed connectors share one conjunctive verdict: neither is attributable.
        value = profiled(self.profile)
        value['fixed_footprints'].append({**value['fixed_footprints'][0], 'reference': 'J2', 'position_mm': 2})
        self.use(raw(value))
        scope = mapped_scope(self.document)
        with self.assertRaisesRegex(ValueError, r"also mapped by \['R-envelope-rc-filter-fixed-J2'\]"):
            obligation_coverage(scope, self.document, self.record, self.files)
        connector = scope['checks'][2]
        scope['checks'][2:3] = [{**connector, 'id': f'connector-{ref}'} for ref in ('J1', 'J2')]
        for requirement in scope['baseline']['requirements']:
            if requirement['checks'] == ['connector']:
                requirement['checks'] = ['connector-' + requirement['id'].rsplit('-', 1)[1]]
        report = self.coverage(scope)
        for ref in ('J1', 'J2'):
            with self.subTest(ref):
                entry = report[f'R-envelope-rc-filter-fixed-{ref}']
                self.assertEqual(entry['status'], 'unknown')
                self.assertIn('grades all 2 fixed footprints as one verdict', entry['reason'])

    def test_unresolved_profile_is_refused_even_when_every_obligation_binds(self):
        artifacts = derive(RC_ENVELOPE.read_bytes(), source_path=ENVELOPE_PATH)
        document = json.loads(artifacts['envelope-requirements.json'])
        record = json.loads(artifacts['envelope-validation.json'])
        files = {ENVELOPE_PATH: hashlib.sha256(RC_ENVELOPE.read_bytes()).hexdigest()}
        with patch.dict(BINDINGS, {('outline', 'placement'): accept, ('layer_count', 'fab_package'): accept,
                                   ('stackup', 'fab_package'): accept}):
            with self.assertRaisesRegex(ValueError, 'incomplete: unresolved fab_profile_selected'):
                require_obligations(mapped_scope(document), document, record, files)

    def test_omitted_or_unmapped_obligations_are_refused_not_dropped(self):
        scope = mapped_scope(self.document)
        scope['baseline']['requirements'] = [r for r in scope['baseline']['requirements'] if r['id'] != PROFILE_ID]
        self.assertEqual(self.coverage(scope)[PROFILE_ID]['status'], 'unmapped')
        with self.assertRaisesRegex(ValueError, f'{PROFILE_ID} \\(unmapped: no mapped check; needs one fab_profile'):
            require_obligations(scope, self.document, self.record, self.files)
        scope = mapped_scope(self.document)
        scope['baseline']['requirements'][-1]['checks'] = []
        self.assertEqual(self.coverage(scope)['R-envelope-rc-filter-fixed-J1']['status'], 'unmapped')

    def test_altered_stale_wrong_kind_and_vacuous_mappings_are_refused(self):
        for label, mutate, message in (
                ('statement', lambda r: r.update(statement=r['statement'] + ' approximately'), 'differs'),
                ('locator', lambda r: r['source'].update(locator='/outline/edges/0'), 'differs'),
                ('path', lambda r: r['source'].update(path='requirements/other.json'), 'differs'),
                ('intent', lambda r: r.update(intent='informational'), 'differs')):
            scope = mapped_scope(self.document)
            mutate(scope['baseline']['requirements'][0])
            with self.subTest(label), self.assertRaisesRegex(ValueError, message):
                obligation_coverage(scope, self.document, self.record, self.files)
        # A registered kind outside the field's kinds is a vacuous remap, even if executable.
        scope = mapped_scope(self.document, kinds={'R-envelope-rc-filter-layer-count': 'fab_profile'})
        with self.assertRaisesRegex(ValueError, "layer-count maps check fab of kind 'fab_profile' outside kinds"):
            obligation_coverage(scope, self.document, self.record, self.files)
        scope = mapped_scope(self.document)
        scope['baseline']['requirements'][-1]['checks'].append('fab')
        with self.assertRaisesRegex(ValueError, "fixed-J1 maps check fab of kind 'fab_profile'"):
            obligation_coverage(scope, self.document, self.record, self.files)
        # The right kind over different profile bytes cannot cover the referenced profile.
        scope = mapped_scope(self.document)
        scope['checks'][3]['params'] = {'fab_profile': 'requirements/owned-capabilities.txt'}
        with self.assertRaisesRegex(ValueError, 'captured profile is not the referenced sha256'):
            obligation_coverage(scope, self.document, self.record, self.files)
        scope = mapped_scope(self.document)
        scope['baseline']['requirements'].append({**scope['baseline']['requirements'][0],
                                                  'id': 'R-envelope-rc-filter-hole-H9'})
        with self.assertRaisesRegex(ValueError, r"not derived from the captured envelope: \['R-envelope-rc-filter-hole-H9'\]"):
            obligation_coverage(scope, self.document, self.record, self.files)
        scope = mapped_scope(self.document)
        scope['schema'] = 'agent-ee.scope/2'
        with self.assertRaisesRegex(ValueError, 'scope/3'):
            obligation_coverage(scope, self.document, self.record, self.files)
        other = dict(self.record, envelope_sha256='0' * 64)
        with self.assertRaisesRegex(ValueError, 'different bytes'):
            obligation_coverage(mapped_scope(self.document), self.document, other, self.files)


def accept(*args):
    """Test double for a binding a future owner item must supply; never production coverage."""


INTENT = json.dumps({'id': 'rc-1', 'task': 'Passive RC low-pass filter between VIN and VOUT.',
                     'input_nodes': ['VIN', 'GND'], 'output_nodes': ['VOUT', 'GND'], 'input_voltage': '5',
                     'output_voltage': '5', 'components': ['R', 'C', 'Conn_01x03'],
                     'sub_module_name': 'rc_filter'}).encode()


def stage_one_dialog(envelope_dialog, extra_question_requirement='R-shape'):
    """AGENTEE-26 intent import, envelope obligations appended, AGENTEE-25 dialog captured."""
    from agent_ee.design_dialog import capture_dialog
    from agent_ee.design_intent import import_record
    original = stage_one_requirements(import_record(INTENT)['requirements.json'], envelope_dialog)
    idea = b'Owner idea: the RC fixture as a generated board.\n'
    questions = json.dumps({'schema': 'agent-ee.questions/1', 'context': 'Mechanics', 'questions': [
        {'id': 'shape', 'question': 'Keep the captured envelope?', 'options': ['yes', 'no'], 'default': 'yes',
         'depends_on': [], 'requirement_id': extra_question_requirement}]}).encode()
    answers = json.dumps({'schema': 'agent-ee.answers/1', 'questions_sha256': hashlib.sha256(questions).hexdigest(),
                          'idea_sha256': hashlib.sha256(idea).hexdigest(),
                          'requirements_sha256': hashlib.sha256(original).hexdigest(),
                          'answers': {'shape': {'value': 'yes'}}}).encode()
    return original, capture_dialog(idea, questions, answers, original)


class EnvelopeStageOneTests(unittest.TestCase):
    def test_intent_import_and_dialog_carry_every_obligation_unchanged(self):
        artifacts = derive(RC_ENVELOPE.read_bytes(), source_path='requirements/envelope.json')
        envelope = json.loads(artifacts['dialog-requirements.json'])['requirements']
        original, dialog = stage_one_dialog(artifacts['dialog-requirements.json'])
        self.assertEqual(json.loads(dialog['dialog.json'])['outcome'], 'pass')
        derived = json.loads(dialog['requirements.json'])['requirements']
        self.assertTrue(derived[0]['id'].startswith('R-intent-'))
        self.assertEqual(derived[1:1 + len(envelope)], envelope)
        self.assertEqual([r['id'] for r in derived[1 + len(envelope):]], ['R-shape'])
        self.assertEqual(dialog['original-requirements.json'], original)
        self.assertEqual(stage_one_requirements(import_record_requirements(), artifacts['dialog-requirements.json']),
                         original)

    def test_collisions_foreign_ids_and_malformed_inputs_are_refused(self):
        envelope = derive(RC_ENVELOPE.read_bytes(), source_path='requirements/envelope.json')['dialog-requirements.json']
        intent = import_record_requirements()
        with self.assertRaisesRegex(ValueError, 'already contain envelope obligations'):
            stage_one_requirements(envelope, envelope)
        foreign = json.loads(envelope)
        foreign['requirements'][0]['id'] = 'R-outline'
        with self.assertRaisesRegex(ValueError, 'R-envelope- obligations'):
            stage_one_requirements(intent, raw(foreign))
        empty = raw({'schema': 'agent-ee.requirements/1', 'requirements': []})
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            stage_one_requirements(intent, empty)
        with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
            stage_one_requirements(intent.replace(b'"schema"', b'"schema":"x","schema"', 1), envelope)
        # A dialog question cannot claim an obligation ID either.
        with self.assertRaisesRegex(ValueError, 'collision'):
            stage_one_dialog(envelope, 'R-envelope-rc-filter-outline')


def import_record_requirements():
    from agent_ee.design_intent import import_record
    return import_record(INTENT)['requirements.json']


class EnvelopeBaselineTests(unittest.TestCase):
    """Capture-time integration in compile_baseline; no tools run."""
    def setUp(self):
        import shutil
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / 'source'
        (source / 'project').mkdir(parents=True)
        for suffix in ('.kicad_pcb', '.kicad_pro', '.kicad_sch'):
            shutil.copyfile(RC_BOARD.with_suffix(suffix), source / 'project' / f'rc_filter{suffix}')
        self.profile, _, _ = owned_capture(source, LAYERS_AND_SIZE, prefix='requirements/')
        self.requirements = source / 'requirements'
        for name in ('targets.md', 'reference.md'):
            (self.requirements / name).write_text(f'Owned {name} for the envelope integration test.\n')
        self.envelope = raw(profiled(self.profile))
        (self.requirements / 'envelope.json').write_bytes(self.envelope)
        artifacts = derive(self.envelope, source_path='requirements/envelope.json', profile=self.profile)
        self.document = json.loads(artifacts['envelope-requirements.json'])
        _, dialog = stage_one_dialog(artifacts['dialog-requirements.json'])
        (self.requirements / 'dialog-requirements.json').write_bytes(dialog['requirements.json'])
        self.runs = 0

    def scope(self, *, schema='agent-ee.scope/3', envelope=True, mapping=None, dialog=True):
        mapping = mapping or {}
        default = {PROFILE_ID: 'fab', 'R-envelope-rc-filter-fixed-J1': 'connector'}
        requirements = [{'id': r['id'], 'statement': r['statement'], 'source': dict(r['source']),
                         'intent': 'acceptance', 'checks': [mapping.get(r['id'], default.get(r['id'], 'drc'))]}
                        for r in self.document['requirements'] if mapping.get(r['id'], True) is not None]
        if schema != 'agent-ee.scope/3':
            for requirement in requirements:
                del requirement['intent']
        scope = {'schema': schema, 'name': 'envelope', 'scope': 'Envelope baseline integration',
                 'inputs': [{'path': 'source/project', 'at': 'project'},
                            {'path': 'source/requirements', 'at': 'requirements'}],
                 'schematic': 'project/rc_filter.kicad_sch', 'board': 'project/rc_filter.kicad_pcb',
                 'checks': [{'id': 'drc', 'kind': 'drc'},
                            {'id': 'connector', 'kind': 'placement', 'params': {
                                'criterion': 'connector-edge/1', 'drc_check': 'drc', 'envelope': ENVELOPE_PATH}},
                            {'id': 'fab', 'kind': 'fab_profile', 'params': {'fab_profile': 'requirements/fab-profile.json'}}],
                 'baseline': {'requirements': requirements, 'targets': ['requirements/targets.md'],
                              'reference_architectures': ['requirements/reference.md']}}
        if envelope:
            scope['envelope'] = {'path': 'requirements/envelope.json', 'profile': 'requirements/fab-profile.json',
                                 'dialog': 'requirements/dialog-requirements.json' if dialog else None}
        return scope

    def capture(self, scope):
        from agent_ee.candidate import capture
        from agent_ee.storage import write_json
        self.runs += 1
        run = self.root / f'run{self.runs}'
        run.mkdir()
        write_json(self.root / 'scope.json', scope)
        return run, capture(self.root / 'scope.json', run)

    def stand_in(self):
        """Test double for bindings no item supplies yet: these fields bind to drc only inside this block.

        J1 keeps the real AGENTEE-92 connector-edge/1 binding.
        """
        fields = ('outline', 'layer_count', 'stackup')
        return patch.dict(CHECK_KINDS, {f: ['drc'] for f in fields}), \
            patch.dict(BINDINGS, {(f, 'drc'): accept for f in fields})

    def test_production_capture_refuses_unbound_obligations_before_any_baseline(self):
        unknown = ('R-envelope-rc-filter-outline .*R-envelope-rc-filter-layer-count .*'
                   'R-envelope-rc-filter-stackup')
        drc = {'id': 'R-drc', 'statement': 'DRC passes.', 'intent': 'acceptance', 'checks': ['drc'],
               'source': {'path': 'requirements/targets.md', 'locator': 'drc'}}
        vacuous = self.scope()
        proposed = self.scope(mapping={'R-envelope-rc-filter-outline': 'placement',
                                       'R-envelope-rc-filter-layer-count': 'package',
                                       'R-envelope-rc-filter-stackup': 'package'})
        proposed['checks'][2:2] = [{'id': 'placement', 'kind': 'placement',
                                    'params': {'criterion': 'outline-containment/1', 'drc_check': 'drc'}},
                                   {'id': 'package', 'kind': 'fab_package'}]
        omitted = self.scope(mapping={r['id']: None for r in self.document['requirements']
                                      if r['id'] not in (PROFILE_ID, 'R-envelope-rc-filter-fixed-J1')})
        for scope in (proposed, omitted):
            scope['baseline']['requirements'].append(drc)
        for label, scope, message in (
                ('vacuous drc', vacuous, "outline maps check drc of kind 'drc' outside kinds \\['placement', 'board_envelope'\\]"),
                ('unregistered or unbound kinds', proposed, 'refused, not dropped: ' + unknown),
                ('omitted', omitted, 'refused, not dropped: ' + unknown)):
            with self.subTest(label), self.assertRaisesRegex(ValueError, message) as raised:
                self.capture(scope)
            self.assertNotIn('fixed-J1', str(raised.exception))
            self.assertFalse((self.root / f'run{self.runs}' / 'baseline.json').exists())
            self.assertFalse((self.root / f'run{self.runs}' / 'candidate.json').exists())

    def test_real_native_bindings_leave_only_stackup_uncovered_and_refuse_capture(self):
        # No stand-ins: native outline/layers and the real profile/connector bind,
        # while missing manufacturing authority still prevents a baseline.
        criteria = {'outline': 'outline/1', 'layer-count': 'layers/1', 'stackup': 'stackup/1'}
        scope = self.scope(mapping={f'R-envelope-rc-filter-{field}': field for field in criteria})
        scope['checks'].extend({'id': field, 'kind': 'board_envelope',
                                'params': {'criterion': criterion, 'envelope': ENVELOPE_PATH}}
                               for field, criterion in criteria.items())
        scope['baseline']['requirements'].append({
            'id': 'R-drc', 'statement': 'DRC passes.', 'intent': 'acceptance', 'checks': ['drc'],
            'source': {'path': 'requirements/targets.md', 'locator': 'drc'}})
        files = {p.relative_to(self.root / 'source').as_posix(): digest(p)
                 for p in (self.root / 'source').rglob('*') if p.is_file()}
        artifacts = derive(self.envelope, source_path=ENVELOPE_PATH, profile=self.profile,
                           captured=(files, self.root / 'source'))
        record = json.loads(artifacts['envelope-validation.json'])
        document = json.loads(artifacts['envelope-requirements.json'])
        report = obligation_coverage(scope, document, record, files)
        self.assertEqual({r['id']: r['status'] for r in report}, {
            'R-envelope-rc-filter-outline': 'bound', 'R-envelope-rc-filter-layer-count': 'bound',
            'R-envelope-rc-filter-stackup': 'unknown', PROFILE_ID: 'bound',
            'R-envelope-rc-filter-fixed-J1': 'bound'})
        with self.assertRaisesRegex(ValueError, 'refused, not dropped: R-envelope-rc-filter-stackup.*copper_weight_oz') as raised:
            self.capture(scope)
        for suffix in ('outline', 'layer-count', 'fixed-J1', 'fab-profile'):
            self.assertNotIn('R-envelope-rc-filter-' + suffix, str(raised.exception))
        run = self.root / f'run{self.runs}'
        self.assertFalse((run / 'baseline.json').exists())
        self.assertFalse((run / 'candidate.json').exists())

    def test_complete_bindings_freeze_envelope_profile_sources_and_dialog(self):
        from agent_ee.baseline import load_baseline
        from agent_ee.campaign import mutable_inputs
        kinds, bindings = self.stand_in()
        with kinds, bindings:
            self.document = json.loads(derive(self.envelope, source_path='requirements/envelope.json',
                                              profile=self.profile)['envelope-requirements.json'])
            run, manifest = self.capture(self.scope())
            baseline = load_baseline(run)
            frozen = baseline['envelope']
            self.assertEqual(baseline['schema'], 'agent-ee.baseline/2')
            self.assertEqual({k: frozen[k] for k in ('path', 'profile', 'dialog')},
                             {'path': 'requirements/envelope.json', 'profile': 'requirements/fab-profile.json',
                              'dialog': 'requirements/dialog-requirements.json'})
            self.assertEqual(frozen['validation']['envelope_sha256'], hashlib.sha256(self.envelope).hexdigest())
            self.assertEqual((frozen['validation']['outcome'], frozen['requirements']['complete']), ('pass', True))
            self.assertEqual({c['id']: c['status'] for c in frozen['coverage']},
                             {r['id']: 'bound' for r in self.document['requirements']})
            self.assertTrue({'requirements/envelope.json', 'requirements/fab-profile.json',
                             'requirements/owned-capabilities.txt', 'requirements/dialog-requirements.json'}
                            <= baseline['files'].keys())
            self.assertEqual(baseline['coverage']['checks']['fab']['predicates'], ['fab:limit-0', 'fab:limit-1'])
            self.assertEqual(baseline['coverage']['checks']['connector']['predicates'], ['placement:connector-edge/1'])
            self.assertEqual(baseline['coverage']['requirements']['R-envelope-rc-filter-fixed-J1']['acceptance_checks'],
                             ['connector'])
            with self.assertRaisesRegex(ValueError, 'outside the immutable baseline'):
                mutable_inputs({**manifest, 'scope': {**manifest['scope'], 'mutable_inputs': [
                    'project/rc_filter.kicad_pcb', 'requirements/envelope.json']}}, baseline)
            # Identical bytes compile to the identical baseline in a fresh capture.
            again, _ = self.capture(self.scope())
            self.assertEqual(load_baseline(again)['baseline_id'], baseline['baseline_id'])
            envelope = run / 'inputs/requirements/envelope.json'
            envelope.write_bytes(envelope.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'envelope path: captured file missing or hash changed'):
                load_baseline(run)
        # Without the stand-in the same retained run is no longer a valid baseline.
        with self.assertRaisesRegex(ValueError, 'outside kinds'):
            load_baseline(again)

    def test_dialog_must_carry_exactly_the_captured_envelope_obligations(self):
        kinds, bindings = self.stand_in()
        moved = profiled(self.profile)
        moved['fixed_footprints'][0]['position_mm'] = 9
        stale = derive(raw(moved), source_path='requirements/envelope.json')['dialog-requirements.json']
        _, other = stage_one_dialog(stale)
        partial = json.loads((self.requirements / 'dialog-requirements.json').read_bytes())
        partial['requirements'] = [r for r in partial['requirements'] if r['id'] != PROFILE_ID]
        extra = json.loads((self.requirements / 'dialog-requirements.json').read_bytes())
        extra['requirements'].append({'id': 'R-envelope-rc-filter-hole-H9', 'text': 'Forged.',
                                      'source_question_ids': []})
        with kinds, bindings:
            self.document = json.loads(derive(self.envelope, source_path='requirements/envelope.json',
                                              profile=self.profile)['envelope-requirements.json'])
            for label, data, message in (('stale envelope', other['requirements.json'], 'do not carry exactly'),
                                         ('omitted', raw(partial), 'do not carry exactly'),
                                         ('extra', raw(extra), r"extra \['R-envelope-rc-filter-hole-H9'\]")):
                (self.requirements / 'dialog-requirements.json').write_bytes(data)
                with self.subTest(label), self.assertRaisesRegex(ValueError, message):
                    self.capture(self.scope())
            # An explicit null dialog binds no decision record; obligations are still enforced.
            run, _ = self.capture(self.scope(dialog=False))
            self.assertNotIn('requirements/dialog-requirements.json',
                             json.loads((run / 'baseline.json').read_text())['files'])

    def test_declared_envelope_is_the_only_consumed_envelope_and_copies_stay_inert(self):
        kinds, bindings = self.stand_in()
        moved = profiled(self.profile)
        moved['fixed_footprints'][0]['position_mm'] = 9
        stale = derive(raw(moved), source_path=ENVELOPE_PATH)
        _, stale_dialog = stage_one_dialog(stale['dialog-requirements.json'])
        current = derive(self.envelope, source_path=ENVELOPE_PATH)

        def with_target(name, scope=None):
            scope = scope or self.scope()
            scope['baseline']['targets'].append(f'requirements/{name}')
            return scope
        with kinds, bindings:
            self.document = json.loads(derive(self.envelope, source_path=ENVELOPE_PATH,
                                              profile=self.profile)['envelope-requirements.json'])
            run, _ = self.capture(self.scope())
            baseline = json.loads((run / 'baseline.json').read_text())
            # Unreferenced copies, including a whitespace-padded one, add no authority or file.
            for name, data in (('copy.json', self.envelope), ('padded.txt', b' \n' * 3000 + self.envelope),
                               ('stale-requirements.json', stale['envelope-requirements.json']),
                               ('current-requirements.json', current['envelope-requirements.json']),
                               ('stale-stage-one.json', stale_dialog['requirements.json'])):
                (self.requirements / name).write_bytes(data)
            run, _ = self.capture(self.scope())
            self.assertEqual(json.loads((run / 'baseline.json').read_text()), baseline)
            # A reference architecture is informative: captured, but never the envelope.
            scope = self.scope()
            scope['baseline']['reference_architectures'].append('requirements/copy.json')
            run, _ = self.capture(scope)
            referenced = json.loads((run / 'baseline.json').read_text())
            self.assertIn('requirements/copy.json', referenced['files'])
            self.assertEqual(referenced['envelope'], baseline['envelope'])
            # A consumed record of these exact bytes, and the matching stage-one set, are admitted.
            for name in ('current-requirements.json', 'dialog-requirements.json'):
                with self.subTest(name):
                    run, _ = self.capture(with_target(name, self.scope(dialog=False)))
                    self.assertIn(f'requirements/{name}', json.loads((run / 'baseline.json').read_text())['files'])
            connector = self.scope()
            connector['checks'][1]['params']['envelope'] = 'requirements/copy.json'
            cited = self.scope()
            cited['baseline']['requirements'].append({
                'id': 'R-mechanics', 'statement': 'The board meets its envelope.', 'intent': 'acceptance',
                'checks': ['drc'], 'source': {'path': 'requirements/copy.json', 'locator': '/'}})
            generic = self.scope()
            generic['baseline']['requirements'].append({
                'id': 'R-mechanics', 'statement': 'The outline is as captured.', 'intent': 'acceptance',
                'checks': ['drc'], 'source': {'path': ENVELOPE_PATH, 'locator': '/outline'}})
            for label, scope, message in (
                    ('check over a copy', connector, r"other than the declared .*\['requirements/copy.json'\]"),
                    ('requirement citing a copy', cited, r"other than the declared .*\['requirements/copy.json'\]"),
                    ('target copy', with_target('copy.json'), r"other than the declared .*\['requirements/copy.json'\]"),
                    ('generic ID citing the envelope', generic, r"not its derived obligations: \['R-mechanics'\]"),
                    ('record of other bytes', with_target('stale-requirements.json'),
                     r"bytes other than the declared envelope: \['requirements/stale-requirements.json'\]"),
                    ('stale stage-one set', with_target('stale-stage-one.json', self.scope(dialog=False)),
                     'stage-one requirements requirements/stale-stage-one.json do not carry exactly')):
                with self.subTest(label), self.assertRaisesRegex(ValueError, message):
                    self.capture(scope)
                self.assertFalse((self.root / f'run{self.runs}' / 'baseline.json').exists())

    def test_undeclared_and_unaudited_envelopes_are_refused(self):
        cases = [('undeclared', self.scope(envelope=False), 'without a scope envelope declaration'),
                 ('scope/2', self.scope(schema='agent-ee.scope/2'), 'requires agent-ee.scope/3'),
                 ('scope/2 undeclared', self.scope(schema='agent-ee.scope/2', envelope=False),
                  'without a scope envelope declaration')]
        scope = self.scope(envelope=False)
        scope['inputs'] = scope['inputs'][:1] + [{'path': f'source/requirements/{n}', 'at': f'requirements/{n}'}
                                                 for n in ('fab-profile.json', 'owned-capabilities.txt',
                                                           'targets.md', 'reference.md')]
        cases.append(('requirement IDs only', scope, r"envelope requirement without .*R-envelope-rc-filter-outline"))
        for label, scope, message in cases:
            with self.subTest(label), self.assertRaisesRegex(ValueError, message):
                self.capture(scope)
        scope = self.scope()
        scope['envelope']['profile'] = None
        with self.assertRaisesRegex(ValueError, 'declare its captured path'):
            self.capture(scope)
        scope = self.scope()
        scope['envelope']['extra'] = True
        with self.assertRaisesRegex(ValueError, 'scope envelope fields'):
            self.capture(scope)
        from agent_ee.baseline import compile_baseline
        with self.assertRaisesRegex(ValueError, 'requires agent-ee.scope/3'):
            compile_baseline({'schema': 'agent-ee.scope/1', 'envelope': {}, 'checks': []}, {})


class EnvelopeAuthorityTests(unittest.TestCase):
    """Only a declared or consumed envelope is authority; a captured reference is inert."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'requirements.md').write_text('Owned ERC requirement, no mechanical declaration.')
        (self.root / 'reference-envelope.json').write_bytes(RC_ENVELOPE.read_bytes())

    def compile(self, *, source='requirements.md', targets=('requirements.md',),
                references=('requirements.md',), packet=(), checks=()):
        """Astra's AGENTEE-85 probe scope: ordinary scope/2 ERC, optionally consuming more paths."""
        from agent_ee.baseline import compile_baseline
        files = {p.name: digest(p) for p in self.root.iterdir()}
        scope = {'schema': 'agent-ee.scope/2', 'checks': [{'id': 'erc', 'kind': 'erc'}, *checks],
                 'requirements': list(packet), 'baseline': {
                     'requirements': [{'id': 'R-erc', 'statement': 'ERC only',
                                       'source': {'path': source, 'locator': 'all'},
                                       'checks': ['erc', *(c['id'] for c in checks)]}],
                     'targets': list(targets), 'reference_architectures': list(references)}}
        return compile_baseline(scope, files, self.root)

    def test_unrelated_captured_reference_envelope_does_not_change_an_erc_baseline(self):
        # Before the repair this raised: captured envelope ... without a scope envelope declaration.
        baseline = self.compile()
        self.assertEqual(baseline['schema'], 'agent-ee.baseline/1')
        self.assertNotIn('envelope', baseline)
        self.assertEqual(set(baseline['files']), {'requirements.md'})
        (self.root / 'reference-envelope.json').unlink()
        self.assertEqual(self.compile()['baseline_id'], baseline['baseline_id'])
        (self.root / 'reference-envelope.json').write_bytes(RC_ENVELOPE.read_bytes())
        # Several reference examples, listed as reference architectures, stay informative.
        (self.root / 'second-envelope.json').write_bytes(RC_ENVELOPE.read_bytes())
        referenced = self.compile(references=('requirements.md', 'reference-envelope.json', 'second-envelope.json'))
        self.assertNotIn('envelope', referenced)
        self.assertEqual(set(referenced['files']), {'requirements.md', 'reference-envelope.json',
                                                    'second-envelope.json'})

    def test_consumed_envelope_without_a_declaration_is_refused_under_any_name_or_size(self):
        artifacts = derive(RC_ENVELOPE.read_bytes(), source_path='reference-envelope.json')
        (self.root / 'envelope-requirements.json').write_bytes(artifacts['envelope-requirements.json'])
        # Larger than the record bound and not named .json: found because it is consumed.
        (self.root / 'mechanics.txt').write_bytes(b' \n' * 600000 + RC_ENVELOPE.read_bytes())
        connector = {'id': 'connector', 'kind': 'placement',
                     'params': {'criterion': 'connector-edge/1', 'drc_check': 'erc', 'envelope': 'reference-envelope.json'}}
        for label, kwargs, message in (
                ('requirement source', {'source': 'reference-envelope.json'}, 'requirement R-erc source'),
                ('padded source', {'source': 'mechanics.txt'}, r'mechanics.txt \(envelope; requirement R-erc source'),
                ('target', {'targets': ('requirements.md', 'reference-envelope.json')}, 'baseline target'),
                ('packet requirement', {'packet': ('reference-envelope.json',)}, 'packet requirement'),
                ('check parameter', {'checks': (connector,)}, 'check connector envelope'),
                ('derived record', {'targets': ('requirements.md', 'envelope-requirements.json')},
                 r'envelope-requirements.json \(derived; baseline target')):
            with self.subTest(label), self.assertRaisesRegex(
                    ValueError, 'consumed envelope or envelope requirement without a scope envelope declaration: '
                                '.*' + message):
                self.compile(**kwargs)

    def test_consumed_stage_one_envelope_obligations_cannot_be_omitted(self):
        from agent_ee.design_intent import import_record
        artifacts = derive(RC_ENVELOPE.read_bytes(), source_path='requirements/envelope.json')
        _, dialog = stage_one_dialog(artifacts['dialog-requirements.json'])
        (self.root / 'stage-one.json').write_bytes(dialog['requirements.json'])
        (self.root / 'intent-only.json').write_bytes(import_record(INTENT)['requirements.json'])
        # Stage one carries the envelope obligations; a scope that consumes it drops them all.
        with self.assertRaisesRegex(ValueError, r'without a scope envelope declaration: stage-one.json '
                                                r'\(stage_one; requirement R-erc source\)'):
            self.compile(source='stage-one.json')
        with self.assertRaisesRegex(ValueError, r'stage-one.json \(stage_one; baseline target\)'):
            self.compile(targets=('stage-one.json',))
        # The same schema without envelope obligations, or stage one only as a reference, is not envelope authority.
        self.assertNotIn('envelope', self.compile(source='intent-only.json', targets=('intent-only.json',)))
        self.assertNotIn('envelope', self.compile(references=('stage-one.json',)))


class EnvelopeNativeBaselineTests(unittest.TestCase):
    """Declared envelope through capture, native DRC, real connector-edge/1 and fab_profile checks."""
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-envelope-native-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def source(self, name, move=None):
        import shutil
        from test_placement_gates_native import EDIT, UNROUTE
        source = self.root / name
        (source / 'project').mkdir(parents=True)
        for path in RC_BOARD.parent.iterdir():
            shutil.copyfile(path, source / 'project' / path.name)
        board = source / 'project/rc_filter.kicad_pcb'
        edit = {'move': move or {}, 'no_courtyard': [], 'areas': []}
        for script in (EDIT, UNROUTE):
            process = subprocess.run(['/usr/bin/python3', '-c', script, str(board), json.dumps(edit)],
                                     capture_output=True, text=True, timeout=120)
            self.assertEqual(process.returncode, 0, process.stderr[-2000:])
        profile, _, _ = owned_capture(source, LAYERS_AND_SIZE, prefix='requirements/')
        (source / 'requirements/targets.md').write_text('Owned envelope native targets.\n')
        envelope = raw(profiled(profile))
        (source / 'requirements/envelope.json').write_bytes(envelope)
        document = json.loads(derive(envelope, source_path=ENVELOPE_PATH,
                                     profile=profile)['envelope-requirements.json'])
        return source, document

    def run_scope(self, source, document):
        from agent_ee import candidate, checks
        from test_placement_gates_native import DRC_FULL, DRC_STAGE, STOCK
        bound = {PROFILE_ID: 'fab', 'R-envelope-rc-filter-fixed-J1': 'connector'}
        requirements = [{'id': r['id'], 'statement': r['statement'], 'source': r['source'], 'intent': 'acceptance',
                         'checks': [bound.get(r['id'], 'drc-final')]} for r in document['requirements']]
        requirements.append({'id': 'R-drc-placement', 'statement': 'Placement-stage DRC passes.',
                             'source': {'path': 'requirements/targets.md', 'locator': 'drc'},
                             'intent': 'acceptance', 'checks': ['drc-placement']})
        scope = {'schema': 'agent-ee.scope/3', 'board': 'project/rc_filter.kicad_pcb',
                 'schematic': 'project/rc_filter.kicad_sch',
                 'inputs': [{'path': str(source / 'project'), 'at': 'project'},
                            {'path': str(source / 'requirements'), 'at': 'requirements'}, *STOCK],
                 'envelope': {'path': ENVELOPE_PATH, 'profile': 'requirements/fab-profile.json', 'dialog': None},
                 'checks': [DRC_STAGE,
                            {'id': 'connector', 'kind': 'placement', 'params': {
                                'criterion': 'connector-edge/1', 'drc_check': 'drc-placement',
                                'envelope': ENVELOPE_PATH}},
                            {'id': 'fab', 'kind': 'fab_profile',
                             'params': {'fab_profile': 'requirements/fab-profile.json'}}, DRC_FULL],
                 'baseline': {'requirements': requirements, 'targets': ['requirements/targets.md'],
                              'reference_architectures': ['requirements/targets.md']}}
        (source / 'scope.json').write_text(json.dumps(scope))
        run = source / 'run'
        run.mkdir()
        candidate.capture(source / 'scope.json', run)
        return run, {r['id']: r for r in checks.run_checks(run, time.monotonic() + 900)}

    def test_declared_envelope_obligations_are_graded_and_witnessed_natively(self):
        from agent_ee.baseline import freeze_evidence, load_baseline, validate_witnesses
        # Stand-in only for fields no item binds yet; J1 and the profile use their real bindings.
        fields = ('outline', 'layer_count', 'stackup')
        with patch.dict(CHECK_KINDS, {f: ['drc'] for f in fields}), \
                patch.dict(BINDINGS, {(f, 'drc'): accept for f in fields}):
            at_anchor, document = self.source('at-anchor')
            run, results = self.run_scope(at_anchor, document)
            self.assertEqual((results['connector']['execution'], results['connector']['outcome']), ('ok', 'pass'),
                             results['connector'])
            self.assertEqual(results['connector']['details']['envelope']['envelope_sha256'],
                             load_baseline(run)['envelope']['validation']['envelope_sha256'])
            self.assertEqual((results['fab']['execution'], results['fab']['outcome']), ('ok', 'pass'), results['fab'])
            moved, document = self.source('moved', move={'J1': (103, 100)})
            run, results = self.run_scope(moved, document)
            connector = results['connector']
            self.assertEqual((connector['execution'], connector['outcome']), ('ok', 'defect'), connector)
            violation, = connector['details']['defects'][0]['violations']
            self.assertEqual((violation['edge'], violation['distance_mm']), ('left', 3.0))
            ordered = [results[c] for c in ('drc-placement', 'connector', 'fab', 'drc-final')]
            freeze_evidence(run, ordered)
            evidence = [{'path': 'checks/connector.json'}]
            validate_witnesses(run, [{'requirement_id': 'R-envelope-rc-filter-fixed-J1', 'check_id': 'connector'}],
                               evidence)
            with self.assertRaisesRegex(ValueError, 'does not trace'):
                validate_witnesses(run, [{'requirement_id': PROFILE_ID, 'check_id': 'connector'}], evidence)
        with self.assertRaisesRegex(ValueError, 'outside kinds'):
            load_baseline(run)


class EnvelopeRoundTripTests(unittest.TestCase):
    def test_rc_fixture_outline_layers_and_header_round_trip(self):
        trip = round_trip(RC_ENVELOPE.read_bytes(), RC_BOARD.read_bytes())
        statuses = {r['field']: r['status'] for r in trip['fields']}
        self.assertEqual(trip['outcome'], 'unknown')
        self.assertEqual(trip['board_sha256'], 'f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de')
        for field in ('/outline', '/layer_count', '/stackup/thickness_mm', '/fixed_footprints/0'):
            self.assertEqual(statuses[field], 'match', field)
        # The saved board has no stackup section and KiCad saves no copper weight.
        self.assertEqual(statuses['/stackup/layers/0/thickness_mm'], 'not_in_board')
        self.assertEqual(statuses['/stackup/layers/0/copper_weight_oz'], 'not_in_board')

    def test_moved_header_changed_outline_and_layers_mismatch(self):
        board = RC_BOARD.read_bytes()
        self.assertEqual(board.count(b'(at 100 100)'), 1)
        trip = round_trip(RC_ENVELOPE.read_bytes(), board.replace(b'(at 100 100)', b'(at 100.5 100)'))
        statuses = {r['field']: r for r in trip['fields']}
        self.assertEqual(trip['outcome'], 'mismatch')
        self.assertEqual(statuses['/fixed_footprints/0']['status'], 'mismatch')
        self.assertEqual(statuses['/fixed_footprints/0']['detail']['board']['at_mm'], ['100.5', '100'])
        value = rc()
        value['fixed_footprints'][0]['tolerance_mm'] = 0.5
        self.assertEqual(round_trip(raw(value), board.replace(b'(at 100 100)', b'(at 100.5 100)'))['outcome'], 'unknown')
        self.assertEqual(board.count(b'(end 116 108)'), 1)
        shrunk = board.replace(b'(end 116 108)', b'(end 116 107)')
        self.assertEqual({r['field']: r['status'] for r in round_trip(RC_ENVELOPE.read_bytes(), shrunk)['fields']}
                         ['/outline'], 'mismatch')
        self.assertEqual(board.count(b'(thickness 1.6)'), 1)
        thicker = board.replace(b'(thickness 1.6)', b'(thickness 1.0)')
        self.assertEqual({r['field']: r['status'] for r in round_trip(RC_ENVELOPE.read_bytes(), thicker)['fields']}
                         ['/stackup/thickness_mm'], 'mismatch')

    def test_rotation_is_compared_modulo_360_and_references_must_be_unique(self):
        board = RC_BOARD.read_bytes()
        value = rc()
        value['fixed_footprints'][0]['rotation_deg'] = 270
        self.assertEqual(round_trip(raw(value), board.replace(b'(at 100 100)', b'(at 100 100 -90)'))['outcome'], 'unknown')
        self.assertEqual(round_trip(raw(value), board)['outcome'], 'mismatch')
        # A duplicated unrelated reference is listed, not fatal; a duplicated fixed one is ambiguous.
        self.assertEqual(board.count(b'(property "Reference" "C1"'), 1)
        twice_c1 = board.replace(b'(property "Reference" "R1"', b'(property "Reference" "C1"')
        self.assertEqual(round_trip(RC_ENVELOPE.read_bytes(), twice_c1)['outcome'], 'unknown')
        # An exact second copy of J1 would match on its own; ambiguity must not pick one.
        start = board.index(b'\t(footprint "Connector_PinHeader')
        depth, end = 0, start
        for end in range(start, len(board)):
            depth += {40: 1, 41: -1}.get(board[end], 0)
            if depth == 0 and board[end] == 41:
                break
        twice_j1 = board[:start] + board[start:end + 1] + b'\n' + board[start:]
        trip = round_trip(RC_ENVELOPE.read_bytes(), twice_j1)
        fixed = {r['field']: r for r in trip['fields']}['/fixed_footprints/0']
        self.assertEqual((trip['outcome'], fixed['detail']['board_matches']), ('mismatch', 2))
        missing = board.replace(b'(property "Reference" "J1"', b'(property "Reference" "J9"')
        fixed = {r['field']: r for r in round_trip(RC_ENVELOPE.read_bytes(), missing)['fields']}['/fixed_footprints/0']
        self.assertEqual((fixed['status'], fixed['detail']['board_matches']), ('mismatch', 0))

    def test_unsupported_outline_items_and_native_stackups(self):
        arc = (b'(kicad_pcb (layers (0 "F.Cu" signal) (2 "B.Cu" signal)) (general (thickness 1.6))'
               b' (gr_rect (start 96 96) (end 116 108) (layer "Edge.Cuts"))'
               b' (gr_arc (start 1 1) (mid 2 2) (end 3 1) (layer "Edge.Cuts")))')
        facts = board_facts(arc)
        self.assertEqual((len(facts['edge_segments']), facts['other_edge_cuts']), (4, ['gr_arc']))
        envelope = rc()
        envelope['fixed_footprints'] = []
        statuses = {r['field']: r['status'] for r in round_trip(raw(envelope), arc)['fields']}
        self.assertEqual(statuses['/outline'], 'mismatch')
        stacked = (b'(kicad_pcb (layers (0 "F.Cu" signal) (2 "B.Cu" signal)) (general (thickness 1.6))'
                   b' (setup (stackup (layer "F.SilkS" (type "Top Silk Screen"))'
                   b' (layer "F.Cu" (type "copper") (thickness 0.035))'
                   b' (layer "dielectric 1" (type "core") (thickness 1.53) (material "FR4"))'
                   b' (layer "B.Cu" (type "copper") (thickness 0.035))))'
                   b' (gr_rect (start 96 96) (end 116 108) (layer "Edge.Cuts")))')
        trip = round_trip(raw(envelope), stacked)
        self.assertEqual(trip['outcome'], 'unknown')
        self.assertEqual({r['field']: r['status'] for r in trip['fields']}['/stackup/layers/1/thickness_mm'], 'match')
        with self.assertRaises(ValueError):
            board_facts(b'(kicad_pcb (gr_line (layer "Edge.Cuts")))')
        with self.assertRaises(ValueError):
            board_facts(b'(kicad_sch)')

    def test_every_saved_edge_cuts_item_is_inventoried_and_refuses_an_outline_match(self):
        board = RC_BOARD.read_bytes()
        value = b'(property "Value" "Conn_01x03"\n\t\t\t(at 0 7.46 0)\n\t\t\t(layer "F.Fab")'
        self.assertEqual(board.count(value), 1)
        self.assertEqual(board.count(b'\t\t(at 100 100)\n'), 1)
        self.assertTrue(board.endswith(b')\n'))
        in_j1 = {
            'footprint J1 fp_text': '(fp_text user "note" (at 0 0 0) (layer "{}") (effects (font (size 1 1))))',
            'footprint J1 fp_text_box': '(fp_text_box "note" (start 0 0) (end 1 1) (layer "{}"))',
            'footprint J1 fp_line': '(fp_line (start 0 0) (end 1 0) (stroke (width 0.1) (type solid)) (layer "{}"))',
            'footprint J1 fp_curve': '(fp_curve (pts (xy 0 0) (xy 1 1) (xy 2 1) (xy 3 0)) (layer "{}"))',
            'footprint J1 pad': '(pad "9" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "{}"))',
        }
        on_board = {
            'dimension': '(dimension (type aligned) (layer "{}") (pts (xy 97 97) (xy 105 97)) (height 0))',
            'gr_text': '(gr_text "note" (at 100 104 0) (layer "{}"))',
            'target': '(target plus (at 100 104) (size 5) (width 0.1) (layer "{}"))',
            'zone': '(zone (net 0) (layers "F.Cu" "{}") (polygon (pts (xy 97 97) (xy 99 97) (xy 99 99))))',
            'gr_line': '(gr_line (start 97 97) (end 99 97) (layer "{}" "F.SilkS"))',
        }

        def variant(label, layer):
            if label == 'footprint J1 property Value':
                return board.replace(value, value.replace(b'"F.Fab"', f'"{layer}"'.encode()))
            if label in in_j1:
                item = in_j1[label].format(layer).encode()
                return board.replace(b'\t\t(at 100 100)\n', b'\t\t(at 100 100)\n\t\t' + item + b'\n')
            return board[:-2] + on_board[label].format(layer).encode() + b')\n'
        for label in ['footprint J1 property Value', *in_j1, *on_board]:
            with self.subTest(label):
                trip = round_trip(RC_ENVELOPE.read_bytes(), variant(label, 'Edge.Cuts'))
                outline = {r['field']: r for r in trip['fields']}['/outline']
                self.assertEqual((outline['status'], trip['outcome']), ('mismatch', 'mismatch'))
                self.assertEqual(outline['detail'], {'missing': 0, 'extra': 0, 'other': [label]})
                # Paired control: the same item on a non-outline layer leaves the outline match intact.
                control = round_trip(RC_ENVELOPE.read_bytes(), variant(label, 'Dwgs.User'))
                self.assertEqual(control['outcome'], 'unknown')
                self.assertEqual({r['field']: r for r in control['fields']}['/outline']['detail']['other'], [])

    def test_native_saved_edge_cuts_field_text_and_dimension_refuse_the_outline(self):
        require_toolchain(native=True)
        probe = ('import json, sys, pcbnew\n'
                 'source, out = sys.argv[1], sys.argv[2]\n'
                 'def save(name, edit):\n'
                 '    b = pcbnew.LoadBoard(source)\n'
                 '    edit(b, b.FindFootprintByReference("J1"))\n'
                 '    pcbnew.SaveBoard(f"{out}/{name}.kicad_pcb", b)\n'
                 'def text(b, j):\n'
                 '    t = pcbnew.PCB_TEXT(j); t.SetText("note"); t.SetLayer(pcbnew.Edge_Cuts); j.Add(t)\n'
                 '    d = pcbnew.PCB_DIM_ALIGNED(b); d.SetLayer(pcbnew.Edge_Cuts)\n'
                 '    d.SetStart(pcbnew.VECTOR2I(97000000, 97000000)); d.SetEnd(pcbnew.VECTOR2I(105000000, 97000000))\n'
                 '    b.Add(d)\n'
                 'save("resaved", lambda b, j: None)\n'
                 'save("field", lambda b, j: j.Value().SetLayer(pcbnew.Edge_Cuts))\n'
                 'save("field-control", lambda b, j: j.Value().SetLayer(pcbnew.Dwgs_User))\n'
                 'save("text", text)\n'
                 'report = {}\n'
                 'for name in ("resaved", "field", "field-control", "text"):\n'
                 '    b = pcbnew.LoadBoard(f"{out}/{name}.kicad_pcb")\n'
                 '    j = b.FindFootprintByReference("J1")\n'
                 '    report[name] = sorted([i.GetClass(), i.GetLayerName()] for i in\n'
                 '        [*j.GetFields(), *j.GraphicalItems(), *b.GetDrawings()]\n'
                 '        if i.GetLayer() == pcbnew.Edge_Cuts)\n'
                 'print(json.dumps(report))\n')
        with tempfile.TemporaryDirectory(prefix='agent-ee-envelope-native-') as tmp:
            result = subprocess.run(['/usr/bin/python3', '-c', probe, str(RC_BOARD), tmp], capture_output=True,
                                    text=True, timeout=120, env={**os.environ, 'PYTHONPATH': ''})
            self.assertEqual(result.returncode, 0, result.stderr)
            native = json.loads(result.stdout.strip().splitlines()[-1])
            saved = {name: (Path(tmp) / f'{name}.kicad_pcb').read_bytes() for name in native}
        segments = [['PCB_SHAPE', 'Edge.Cuts']] * 4
        # Reloaded native boards confirm what each saved file holds on Edge.Cuts.
        self.assertEqual(native['resaved'], segments)
        self.assertEqual(native['field-control'], segments)
        self.assertEqual(native['field'], sorted(segments + [['PCB_FIELD', 'Edge.Cuts']]))
        self.assertEqual(native['text'],
                         sorted(segments + [['PCB_DIM_ALIGNED', 'Edge.Cuts'], ['PCB_TEXT', 'Edge.Cuts']]))
        reference = board_facts(RC_BOARD.read_bytes())['edge_segments']
        for name, other in (('resaved', []), ('field-control', []), ('field', ['footprint J1 property Value']),
                            ('text', ['dimension', 'dimension gr_text', 'footprint J1 fp_text'])):
            with self.subTest(name):
                # pcbnew re-saving keeps the native outline geometry identical.
                self.assertEqual(board_facts(saved[name])['edge_segments'], reference)
                trip = round_trip(RC_ENVELOPE.read_bytes(), saved[name])
                fields = {r['field']: r for r in trip['fields']}
                self.assertEqual(fields['/outline']['detail'], {'missing': 0, 'extra': 0, 'other': other})
                self.assertEqual(fields['/fixed_footprints/0']['status'], 'match')
                self.assertEqual(trip['outcome'], 'mismatch' if other else 'unknown')

    def test_native_pcbnew_agrees_with_static_board_facts(self):
        require_toolchain(native=True)
        probe = ('import json, sys, pcbnew\n'
                 'b = pcbnew.LoadBoard(sys.argv[1])\n'
                 'edges = [[[s.GetStart().x, s.GetStart().y], [s.GetEnd().x, s.GetEnd().y]]\n'
                 '         for s in b.GetDrawings() if s.GetLayer() == pcbnew.Edge_Cuts]\n'
                 'shapes = sorted({s.GetShape() for s in b.GetDrawings() if s.GetLayer() == pcbnew.Edge_Cuts})\n'
                 'j = b.FindFootprintByReference("J1")\n'
                 'print(json.dumps({"edges": edges, "segment": shapes == [pcbnew.SHAPE_T_SEGMENT],\n'
                 '  "copper": b.GetCopperLayerCount(), "thickness": b.GetDesignSettings().GetBoardThickness(),\n'
                 '  "j1": [j.GetPosition().x, j.GetPosition().y, j.GetOrientationDegrees(), j.GetLayerName()]}))\n')
        result = subprocess.run(['/usr/bin/python3', '-c', probe, str(RC_BOARD)], capture_output=True,
                                text=True, timeout=120, env={**os.environ, 'PYTHONPATH': ''})
        self.assertEqual(result.returncode, 0, result.stderr)
        native = json.loads(result.stdout.strip().splitlines()[-1])
        facts = board_facts(RC_BOARD.read_bytes())
        self.assertTrue(native['segment'])
        self.assertEqual({frozenset(map(tuple, e)) for e in native['edges']},
                         {frozenset(s) for s in facts['edge_segments']})
        self.assertEqual(native['copper'], len(facts['copper_layers']))
        self.assertEqual(native['thickness'], facts['thickness_nm'])
        [j1] = facts['footprints']['J1']
        self.assertEqual(native['j1'], [j1['at'][0], j1['at'][1], float(j1['rotation_deg']), j1['layer']])


class EnvelopeCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'agent_ee.board_envelope', *map(str, args)],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')})

    def test_cli_writes_new_directory_and_reports_unresolved_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'derived'
            result = self.run_cli(RC_ENVELOPE, '--source-path', 'requirements/envelope.json', '--out', out,
                                  '--board', RC_BOARD)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(json.loads((out / 'round-trip.json').read_text())['outcome'], 'unknown')
            expected = derive(RC_ENVELOPE.read_bytes(), source_path='requirements/envelope.json')
            for name, data in expected.items():
                self.assertEqual((out / name).read_bytes(), data, name)
            again = self.run_cli(RC_ENVELOPE, '--source-path', 'requirements/envelope.json', '--out', out)
            self.assertEqual(again.returncode, 2)
            self.assertIn('envelope refused or unavailable', again.stderr)
            bad = Path(tmp) / 'open.json'
            value = rc()
            value['outline']['edges'][0]['end'] = [115, 96]
            bad.write_bytes(raw(value))
            refused = self.run_cli(bad, '--source-path', 'requirements/envelope.json', '--out', Path(tmp) / 'x')
            self.assertEqual(refused.returncode, 2)
            self.assertIn('outline is open', refused.stderr)
            self.assertFalse((Path(tmp) / 'x').exists())

    def test_cli_resolves_profile_only_against_hashed_sources_and_writes_stage_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            profile, files, inputs = owned_capture(tmp / 'capture', LAYERS_AND_SIZE)
            envelope = tmp / 'envelope.json'
            envelope.write_bytes(raw(profiled(profile)))
            intent = tmp / 'intent-requirements.json'
            intent.write_bytes(import_record_requirements())
            common = [envelope, '--source-path', 'requirements/envelope.json', '--profile',
                      inputs / 'fab-profile.json']
            offline = self.run_cli(*common, '--out', tmp / 'offline')
            self.assertEqual(offline.returncode, 1, offline.stderr)
            resolved = self.run_cli(*common, '--sources', inputs, '--board', RC_BOARD, '--out', tmp / 'resolved',
                                    '--original-requirements', intent)
            self.assertEqual(resolved.returncode, 1, resolved.stderr)
            self.assertEqual(json.loads((tmp / 'resolved/round-trip.json').read_text())['outcome'], 'unknown')
            expected = derive(envelope.read_bytes(), source_path='requirements/envelope.json', profile=profile,
                              captured=(files, inputs))
            for name, data in expected.items():
                self.assertEqual((tmp / 'resolved' / name).read_bytes(), data, name)
            self.assertEqual((tmp / 'resolved/stage-one-requirements.json').read_bytes(),
                             stage_one_requirements(intent.read_bytes(), expected['dialog-requirements.json']))
            unsourced = self.run_cli(envelope, '--source-path', 'requirements/envelope.json', '--sources', inputs,
                                     '--out', tmp / 'unsourced')
            self.assertEqual(unsourced.returncode, 2)
            self.assertIn('--sources needs --profile', unsourced.stderr)


if __name__ == '__main__':
    unittest.main()

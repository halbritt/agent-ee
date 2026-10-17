"""Board envelope record: exact structure, provenance and obligation binding."""
import copy
import decimal
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_ee.board_envelope import (board_facts, derive, require_obligations, round_trip, validate)
from agent_ee.storage import canonical
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
    checks = [{'id': 'placement', 'kind': 'placement'}, {'id': 'fab', 'kind': 'fab_profile'},
              {'id': 'package', 'kind': 'fab_package'}]
    by_kind = {'placement': 'placement', 'fab_profile': 'fab', 'fab_package': 'package'}
    return {'schema': 'agent-ee.scope/3', 'checks': checks, 'baseline': {'requirements': [
        {'id': r['id'], 'statement': r['statement'], 'source': dict(r['source']), 'intent': 'acceptance',
         'checks': [by_kind[(kinds or {}).get(r['id'], r['check_kinds'][0])]]}
        for r in requirements['requirements']]}}


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
    def test_profile_reference_is_hash_bound_and_support_stays_unresolved(self):
        profile = b'{"schema": "agent-ee.fab-profile/1", "name": "owned"}'
        value = rc()
        value['fab_profile'] = {'name': 'owned', 'sha256': hashlib.sha256(profile).hexdigest()}
        record = validate(raw(value), profile=profile)
        self.assertEqual(record['fab_profile']['bytes'], 'sha256_match')
        self.assertEqual(record['outcome'], 'unknown')
        self.assertEqual([u['predicate'] for u in record['unresolved']], ['fab_profile_supports_layer_count'])
        self.assertEqual([u['predicate'] for u in validate(raw(value))['unresolved']],
                         ['fab_profile_bytes', 'fab_profile_supports_layer_count'])
        with self.assertRaisesRegex(ValueError, 'do not match the referenced sha256'):
            validate(raw(value), profile=profile + b' ')
        with self.assertRaisesRegex(ValueError, 'references no fab profile'):
            validate(RC_ENVELOPE.read_bytes(), profile=profile)
        value['fab_profile']['sha256'] = value['fab_profile']['sha256'].upper()
        with self.assertRaisesRegex(ValueError, 'lowercase hex'):
            validate(raw(value))
        value['fab_profile'] = {'name': 'owned'}
        with self.assertRaisesRegex(ValueError, 'fab_profile fields'):
            validate(raw(value))


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
        profile = b'profile bytes'
        value = rc()
        value['fab_profile'] = {'name': 'owned', 'sha256': hashlib.sha256(profile).hexdigest()}
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
        self.assertEqual([u['predicate'] for u in document['unresolved']], ['fab_profile_supports_layer_count'])

    def test_unsafe_source_path_is_refused(self):
        for path in ('/abs/envelope.json', '../envelope.json'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                derive(RC_ENVELOPE.read_bytes(), source_path=path)


class EnvelopeObligationTests(unittest.TestCase):
    def setUp(self):
        artifacts = derive(RC_ENVELOPE.read_bytes(), source_path='requirements/envelope.json')
        self.document = json.loads(artifacts['envelope-requirements.json'])

    def test_fully_mapped_obligations_still_refuse_unresolved_profile(self):
        with self.assertRaisesRegex(ValueError, 'incomplete: unresolved fab_profile_selected'):
            require_obligations(mapped_scope(self.document), self.document)

    def test_obligation_without_mapped_check_is_refused_not_dropped(self):
        scope = mapped_scope(self.document)
        scope['baseline']['requirements'] = [r for r in scope['baseline']['requirements']
                                             if r['id'] != 'R-envelope-rc-filter-fixed-J1']
        with self.assertRaisesRegex(ValueError, 'R-envelope-rc-filter-fixed-J1 has no mapped check'):
            require_obligations(scope, self.document)
        scope = mapped_scope(self.document)
        scope['baseline']['requirements'][0]['checks'] = []
        with self.assertRaisesRegex(ValueError, 'R-envelope-rc-filter-outline has no mapped check'):
            require_obligations(scope, self.document)

    def test_altered_informational_or_wrong_kind_mappings_are_refused(self):
        for label, mutate, message in (
                ('statement', lambda r: r.update(statement=r['statement'] + ' approximately'), 'differs'),
                ('locator', lambda r: r['source'].update(locator='/outline/edges/0'), 'differs'),
                ('intent', lambda r: r.update(intent='informational'), 'differs')):
            scope = mapped_scope(self.document)
            mutate(scope['baseline']['requirements'][0])
            with self.subTest(label), self.assertRaisesRegex(ValueError, message):
                require_obligations(scope, self.document)
        scope = mapped_scope(self.document, kinds={'R-envelope-rc-filter-fixed-J1': 'fab_package'})
        with self.assertRaisesRegex(ValueError, r"maps checks \['package'\] outside kinds \['placement'\]"):
            require_obligations(scope, self.document)
        scope = mapped_scope(self.document)
        scope['schema'] = 'agent-ee.scope/2'
        with self.assertRaisesRegex(ValueError, 'scope/3'):
            require_obligations(scope, self.document)


class EnvelopeRoundTripTests(unittest.TestCase):
    def test_rc_fixture_outline_layers_and_header_round_trip(self):
        trip = round_trip(RC_ENVELOPE.read_bytes(), RC_BOARD.read_bytes())
        statuses = {r['field']: r['status'] for r in trip['fields']}
        self.assertEqual(trip['outcome'], 'match')
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
        self.assertEqual(round_trip(raw(value), board.replace(b'(at 100 100)', b'(at 100.5 100)'))['outcome'], 'match')
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
        self.assertEqual(round_trip(raw(value), board.replace(b'(at 100 100)', b'(at 100 100 -90)'))['outcome'], 'match')
        self.assertEqual(round_trip(raw(value), board)['outcome'], 'mismatch')
        # A duplicated unrelated reference is listed, not fatal; a duplicated fixed one is ambiguous.
        self.assertEqual(board.count(b'(property "Reference" "C1"'), 1)
        twice_c1 = board.replace(b'(property "Reference" "R1"', b'(property "Reference" "C1"')
        self.assertEqual(round_trip(RC_ENVELOPE.read_bytes(), twice_c1)['outcome'], 'match')
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
        self.assertEqual(trip['outcome'], 'match')
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
                self.assertEqual(control['outcome'], 'match')
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
                self.assertEqual(trip['outcome'], 'mismatch' if other else 'match')

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
            self.assertEqual(json.loads((out / 'round-trip.json').read_text())['outcome'], 'match')
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


if __name__ == '__main__':
    unittest.main()

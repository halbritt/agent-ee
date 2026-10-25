"""AGENTEE-479 step 1-2: exact original-dialect adapter and upstream pad-edge metric."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_ee import pad_edge, placement_intent

ROOT = Path(__file__).resolve().parents[1]
ESP = Path(os.environ.get('AGENT_EE_ESP_PROG_INPUTS', ROOT / 'build/agent545/inputs'))
ESP_INTENT_SHA = '320cf961d58dab9aea164f34afc11099ae48f75fc0b100273a155d4d62330d3c'
ESP_GROUPS_SHA = 'd48e2a5d95f733be6d4bdee06736034a5e34acd68e6289edef00fe89b86837e8'
ESP_BOARD_SHA = 'b4aa540d031e18283216d806aa6288cf51260e7328f9aaeb13b55d4eac93eef7'


def minimal_dialect():
    """A small intent in the exact original dialect, mirroring the esp_prog structure."""
    return {
        'kind': 'floorplan-intent', 'schema': 1, 'min_reader': 12, 'units': 'mm', 'board': 'board.kicad_pcb',
        'assembly': {'sides': 'F', 'why': 'observed'},
        'blocks': [], 'keepouts': [], 'must_lock': [], 'decaps': {},
        'defaults': {'zone_tolerance_mm': 0.5},
        'context': {'basis': {'assembly.sides': 'observed_baseline'}, 'cutouts': [], 'edge_contours': 0,
                    'file_locked': [], 'note': 'n', 'budget_withheld': {'overlap_area': 'pile'},
                    'brief': {'consequences': [
                        {'id': 'interfaces[J1].cable_envelope_mm', 'ref': 'J1', 'status': 'unmeasured',
                         'value': None, 'why': 'not measured by 2D placement'},
                        {'id': 'interfaces[J1].mount_mode', 'ref': 'J1', 'status': 'compiled',
                         'compiled_to': 'edge_connectors[J1].max_setback_mm', 'value': 0.75, 'why': 'w'}]}},
        'dispositions': {'rules': {'decap_distance': 'none supplied'}, 'withheld': {'overlap_area': 'pile'}},
        'edge_connectors': [{'ref': 'J1', 'edge': 'west', 'side': 'F', 'class': 'edge_receptacle',
                             'max_setback_mm': 0.75, 'overhang_mm': {'min': 0.0, 'max': 2.0}, 'source': 'brief',
                             'context': {'mount_mode': 'edge_mount', 'cable_entry': 'in_plane', 'role': 'host'}}],
        'envelope': {'rect': [0.0, 0.0, 30.0, 20.0], 'tolerance_mm': 0.5},
        'fixed_poses': [{'ref': 'Ref*', 'x': 1.0, 'y': 2.0, 'rot': 0.0, 'side': 'F', 'basis': 'mechanical', 'why': 'w'}],
        'legality_budget': {'oob_count': 0},
        'proximity': [{'ref': 'Y1', 'near': 'U1', 'max_mm': 8.1, 'basis': 'pad_edge',
                       'pads': {'U1': ['9', '10'], 'Y1': ['1', '2']},
                       'context': {'basis': 'corpus 75th percentile', 'limit': 'derived_default',
                                   'role': 'crystal', 'source': 'placement.crystals'}}],
    }


def minimal_groups():
    return {'schema': 1, 'board': 'board.kicad_pcb', 'groups': [
        {'name': 'core', 'role': 'core', 'anchor': 'U1', 'refs': ['U1', 'Y1'], 'near': ['usb'], 'why': 'w'},
        {'name': 'usb', 'role': 'interface', 'anchor': 'J1', 'refs': ['J1'], 'near': ['core'], 'why': 'w'}]}


class DialectAdapterTests(unittest.TestCase):
    def test_exact_dialect_is_classified_without_dropping_or_weakening_any_field(self):
        record = placement_intent.adapt(minimal_dialect(), minimal_groups())
        self.assertEqual(record['schema'], placement_intent.COMPATIBILITY_SCHEMA)
        self.assertFalse(record['runnable'])
        by_path = {d['path']: d for d in record['dispositions']}
        self.assertEqual(by_path['intent.assembly.sides']['disposition'], 'operative_pending_capture')
        self.assertEqual(by_path['intent.envelope']['disposition'], 'operative_pending_capture')
        self.assertEqual(by_path['intent.legality_budget.oob_count']['disposition'], 'operative_supported')
        self.assertEqual(by_path['intent.edge_connectors[0].side']['disposition'], 'operative_pending_capture')
        self.assertEqual(by_path['intent.edge_connectors[0].context.cable_entry']['value'], 'in_plane')
        self.assertEqual(by_path['intent.proximity[0]']['authority'], 'advisory_objective')
        self.assertEqual(by_path['intent.proximity[0]']['metric'], pad_edge.METRIC_ID)
        self.assertEqual(by_path['groups.groups[0].near']['near'], ['usb'])
        self.assertEqual([c['id'] for c in record['unresolved_claims']], ['interfaces[J1].cable_envelope_mm'])
        self.assertEqual(record['unresolved_claims'][0]['status'], 'unresolved_unmeasured')
        self.assertEqual(by_path['intent.context.brief.consequences[1]']['disposition'], 'descriptive')
        self.assertEqual(set(record['withheld']), {'rules.decap_distance', 'withheld.overlap_area', 'budget.overlap_area'})
        for key in ('blocks', 'keepouts', 'must_lock', 'decaps'):
            self.assertEqual(by_path['intent.' + key]['disposition'], 'noop_empty')
        pending = {p['path'] for p in record['pending']}
        self.assertEqual(pending, {'intent.assembly.sides', 'intent.envelope', 'intent.edge_connectors[0].side',
                                   'intent.edge_connectors[0].context.cable_entry', 'intent.proximity[0]',
                                   'groups.groups[0].near', 'groups.groups[1].near'})
        # The native projection validates today and carries only what the validator accepts.
        placement_intent.validate(record['native_intent'], record['native_groups'])
        self.assertEqual(record['native_intent']['edge_connectors'][0]['context'], {'mount_mode': 'edge_mount'})
        self.assertNotIn('side', record['native_intent']['edge_connectors'][0])
        self.assertNotIn('proximity', record['native_intent'])
        self.assertEqual(record['proximity_pad_edge'][0]['basis'], 'pad_edge')
        self.assertNotIn('near', record['native_groups']['groups'][0])

    def test_native_intent_passes_through_runnable(self):
        native = {'schema': placement_intent.SCHEMA, 'fixed_poses': [{'ref': 'R1', 'x': 1.0, 'y': 1.0}]}
        record = placement_intent.adapt(native, {'groups': []})
        self.assertTrue(record['runnable'])
        self.assertEqual(record['dialect'], 'native')

    def test_dialect_negatives_refuse_with_exact_paths(self):
        def mutate(**changes):
            document = minimal_dialect()
            for path, value in changes.items():
                target, keys = document, path.split('.')
                for key in keys[:-1]:
                    target = target[key]
                if value is Ellipsis:
                    del target[keys[-1]]
                else:
                    target[keys[-1]] = value
            return document

        cases = [
            ('min_reader', mutate(min_reader=11), 'intent.min_reader'),
            ('min_reader_13', mutate(min_reader=13), 'intent.min_reader'),
            ('schema', mutate(schema=2), 'intent.schema'),
            ('kind', mutate(kind='placement-intent'), 'intent.kind'),
            ('units', mutate(units='mil'), 'intent.units'),
            ('unknown_top', mutate(regions=[]), 'intent.regions'),
            ('blocks', mutate(blocks=[{'name': 'b'}]), 'intent.blocks'),
            ('keepouts', mutate(keepouts=[{'rect': [0, 0, 1, 1]}]), 'intent.keepouts'),
            ('must_lock', mutate(must_lock=['U1']), 'intent.must_lock'),
            ('decaps', mutate(decaps={'U1': ['C1']}), 'intent.decaps'),
            ('cutouts', mutate(**{'context.cutouts': [[0, 0, 1, 1]]}), 'intent.context.cutouts'),
            ('edge_contours', mutate(**{'context.edge_contours': 2}), 'intent.context.edge_contours'),
            ('file_locked', mutate(**{'context.file_locked': ['U1']}), 'intent.context.file_locked'),
            ('oob', mutate(**{'legality_budget.oob_count': 1}), 'intent.legality_budget.oob_count'),
            ('defaults_extra', mutate(defaults={'zone_tolerance_mm': 0.5, 'other': 1}), 'intent.defaults.other'),
            ('context_unknown', mutate(**{'context.extra': 1}), 'intent.context.extra'),
            ('basis', mutate(**{'proximity': [dict(minimal_dialect()['proximity'][0], basis='pad_centre')]}),
             'intent.proximity[0].basis'),
            ('limit_missing', mutate(**{'proximity': [dict(minimal_dialect()['proximity'][0],
                                                           context={'basis': 'b'})]}), 'intent.proximity[0].context.limit'),
            ('cable_side', mutate(**{'edge_connectors': [dict(minimal_dialect()['edge_connectors'][0], side='B',
                                                                 context={'mount_mode': 'top_mount',
                                                                          'cable_entry': 'perpendicular_top'})]}),
             'intent.edge_connectors[0].context.cable_entry'),
            ('edge_unknown', mutate(**{'edge_connectors': [dict(minimal_dialect()['edge_connectors'][0],
                                                                  keep='x')]}), 'intent.edge_connectors[0].keep'),
        ]
        for label, document, expected in cases:
            with self.subTest(case=label):
                with self.assertRaises(ValueError) as caught:
                    placement_intent.adapt(document, minimal_groups())
                self.assertIn(expected, str(caught.exception))
        groups = minimal_groups()
        groups['groups'][0]['near'] = ['core']
        with self.assertRaises(ValueError) as caught:
            placement_intent.adapt(minimal_dialect(), groups)
        self.assertIn('groups.groups[0].near', str(caught.exception))
        groups['groups'][0]['near'] = ['nope']
        with self.assertRaises(ValueError):
            placement_intent.adapt(minimal_dialect(), groups)

    def test_proximity_dialect_identity_and_map_shapes_refuse_with_exact_paths(self):
        """Chief review of 2c31eb1c: the proximity side-band and dynamic maps get one authoritative check."""
        def with_prox(**changes):
            document = minimal_dialect()
            document['proximity'][0].update(changes)
            return document

        cases = [
            ('negative_limit', with_prox(max_mm=-1), 'intent.proximity[0].max_mm'),
            ('text_limit', with_prox(max_mm='not a distance'), 'intent.proximity[0].max_mm'),
            ('boolean_limit', with_prox(max_mm=True), 'intent.proximity[0].max_mm'),
            ('infinite_limit', with_prox(max_mm=float('inf')), 'intent.proximity[0].max_mm'),
            ('empty_subject', with_prox(ref=''), 'intent.proximity[0].ref'),
            ('same_ref_near', with_prox(near='Y1'), 'intent.proximity[0].near'),
            ('unknown_pad_selector', with_prox(pads={'U1': ['9'], 'Y1': ['1'], 'UNDECLARED': ['1']}),
             'intent.proximity[0].pads.UNDECLARED'),
            ('empty_pad_list', with_prox(pads={'U1': [], 'Y1': ['1']}), 'intent.proximity[0].pads.U1'),
            ('duplicate_pad_names', with_prox(pads={'U1': ['9', '9'], 'Y1': ['1']}), 'intent.proximity[0].pads.U1'),
            ('pads_not_object', with_prox(pads=['1']), 'intent.proximity[0].pads'),
        ]
        for label, document, expected in cases:
            with self.subTest(case=label):
                with self.assertRaises(ValueError) as caught:
                    placement_intent.adapt(document, minimal_groups())
                self.assertIn(expected, str(caught.exception))
        # Exact dialect integer types: True is not 1 and 12.0 is not 12.
        for label, key, value in (('boolean_schema', 'schema', True), ('float_min_reader', 'min_reader', 12.0),
                                  ('boolean_min_reader', 'min_reader', True)):
            with self.subTest(case=label):
                document = minimal_dialect()
                document[key] = value
                self.assertFalse(placement_intent.is_dialect(document))
                with self.assertRaises(ValueError) as caught:
                    placement_intent.adapt(document, minimal_groups())
                self.assertIn('intent.' + key, str(caught.exception))
        # Dynamic maps are type-checked with their path, never an AttributeError.
        for label, mutate, expected in (
                ('nonobject_rules', lambda d: d['dispositions'].__setitem__('rules', []), 'intent.dispositions.rules'),
                ('nonobject_withheld', lambda d: d['dispositions'].__setitem__('withheld', 'x'), 'intent.dispositions.withheld'),
                ('nontext_rule', lambda d: d['dispositions']['rules'].__setitem__('decap_distance', 3), 'intent.dispositions.rules.decap_distance'),
                ('nonobject_basis', lambda d: d['context'].__setitem__('basis', ['x']), 'intent.context.basis'),
                ('nonobject_budget', lambda d: d['context'].__setitem__('budget_withheld', 1), 'intent.context.budget_withheld'),
                ('nontext_note', lambda d: d['context'].__setitem__('note', 5), 'intent.context.note')):
            with self.subTest(case=label):
                document = minimal_dialect()
                mutate(document)
                with self.assertRaises(ValueError) as caught:
                    placement_intent.adapt(document, minimal_groups())
                self.assertIn(expected, str(caught.exception))
        # The undeclared-subject fallback stays permitted: partner pads only, and neither side declared.
        record = placement_intent.adapt(with_prox(pads={'U1': ['9', '10']}), minimal_groups())
        self.assertEqual(record['proximity_pad_edge'][0]['pads'], {'U1': ['9', '10']})
        record = placement_intent.adapt(with_prox(pads={}), minimal_groups())
        self.assertEqual(record['proximity_pad_edge'][0]['pads'], {})
        # The native form still declares both sides.
        native = {'schema': placement_intent.SCHEMA, 'proximity': [{'ref': 'Y1', 'near': 'U1', 'max_mm': 1.0,
                                                                    'pads': {'U1': ['9']}}]}
        with self.assertRaises(ValueError) as caught:
            placement_intent.validate(native, {'groups': []})
        self.assertIn('intent.proximity[0].pads.Y1', str(caught.exception))

    def test_unknown_authority_label_is_visible_not_hard(self):
        document = minimal_dialect()
        document['proximity'][0]['context']['limit'] = 'datasheet'
        record = placement_intent.adapt(document, minimal_groups())
        row = next(d for d in record['dispositions'] if d['path'] == 'intent.proximity[0]')
        self.assertEqual(row['authority'], 'unassessed_authority')
        self.assertEqual(row['limit'], 'datasheet')


class PadEdgeMetricTests(unittest.TestCase):
    def test_c1_overlap_is_signed_never_clamped(self):
        self.assertAlmostEqual(pad_edge.rect_gap((0.0, 0.0, 1.0, 1.0), (0.75, 0.0, 1.75, 1.0)), -0.25, places=9)
        self.assertAlmostEqual(pad_edge.rect_gap((0.0, 0.0, 1.0, 1.0), (3.0, 4.0, 4.0, 5.0)), math.hypot(2.0, 3.0), places=9)
        self.assertAlmostEqual(pad_edge.rect_gap((0.0, 0.0, 1.0, 1.0), (2.0, 0.5, 3.0, 1.5)), 1.0, places=9)

    def test_c2_rotated_pad_uses_axis_aligned_half_extents(self):
        rect = pad_edge.pad_rect(0.0, 0.0, 2.0, 0.6, 45.0)
        self.assertAlmostEqual(rect[2], 0.9192, places=4)
        self.assertAlmostEqual(rect[3], 0.9192, places=4)
        self.assertAlmostEqual(pad_edge.rect_gap(rect, (1.2, 1.2, 1.8, 1.8)), 0.3971, places=4)
        self.assertEqual(pad_edge.pad_rect_from_bbox(0.0, 0.0, -1.5, -0.5, 1.0, 0.5), (-1.5, -0.5, 1.5, 0.5))

    def pads(self):
        return {
            'Y1': [{'number': '1', 'rect': (0.0, 0.0, 1.0, 1.0), 'net_id': 1, 'net': 'A'},
                   {'number': '2', 'rect': (3.0, 0.0, 4.0, 1.0), 'net_id': 2, 'net': 'B'},
                   {'number': '3', 'rect': (0.0, 3.0, 1.0, 4.0), 'net_id': 0, 'net': ''},
                   {'number': '3', 'rect': (3.0, 3.0, 4.0, 4.0), 'net_id': 0, 'net': ''}],
            'U1': [{'number': '9', 'rect': (0.0, 6.0, 1.0, 7.0), 'net_id': 1, 'net': 'A'},
                   {'number': '10', 'rect': (3.0, 9.0, 4.0, 10.0), 'net_id': 2, 'net': 'B'},
                   {'number': '11', 'rect': (3.0, 2.0, 4.0, 2.5), 'net_id': 3, 'net': 'C'}]}

    def test_c3_one_leg_per_declared_subject_pad_with_net_narrowing(self):
        claim = {'ref': 'Y1', 'near': 'U1', 'max_mm': 6.0, 'pads': {'Y1': ['1', '2'], 'U1': ['9', '10', '11']}}
        result = pad_edge.claim_legs(claim, self.pads())
        self.assertEqual(result['status'], 'measured')
        self.assertEqual([(l['subject_pad'], l['partner_pad'], l['paired_by']) for l in result['legs']],
                         [('1', '9', 'net'), ('2', '10', 'net')])
        self.assertAlmostEqual(result['legs'][0]['gap_mm'], 5.0)
        self.assertAlmostEqual(result['legs'][1]['gap_mm'], 8.0)  # nearer pad 11 is ignored: net narrowing
        self.assertEqual([l['within_max_mm'] for l in result['legs']], [True, False])

    def test_c4_missing_declared_pad_is_blind_not_partial(self):
        claim = {'ref': 'Y1', 'near': 'U1', 'max_mm': 6.0, 'pads': {'Y1': ['1', '7'], 'U1': ['9', '10']}}
        result = pad_edge.claim_legs(claim, self.pads())
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['legs'], [])
        self.assertEqual(result['missing'], [{'ref': 'Y1', 'pad': '7'}])
        self.assertEqual(pad_edge.claim_legs({'ref': 'Y1', 'near': 'ZZ', 'max_mm': 1}, self.pads())['status'],
                         'unresolved')

    def test_declaration_modes_match_the_upstream_contract(self):
        pads = self.pads()
        # Both declared: one leg per subject pad, net-narrowed (pad 2 pairs with 10 on net B, not nearer 11).
        both = pad_edge.claim_legs({'ref': 'Y1', 'near': 'U1', 'max_mm': 6.0, 'pads': {'Y1': ['1', '2'], 'U1': ['9', '10', '11']}}, pads)
        self.assertEqual([(l['subject_pad'], l['partner_pad'], l['paired_by']) for l in both['legs']], [('1', '9', 'net'), ('2', '10', 'net')])
        # Partner only: undeclared subject -> ONE minimum leg over all subject pads, no net narrowing.
        partner_only = pad_edge.claim_legs({'ref': 'Y1', 'near': 'U1', 'max_mm': 6.0, 'pads': {'U1': ['9', '10', '11']}}, pads)
        self.assertEqual(len(partner_only['legs']), 1)
        self.assertEqual(partner_only['pads_basis'], 'all_non_aperture')
        self.assertEqual((partner_only['legs'][0]['subject_pad'], partner_only['legs'][0]['partner_pad'], partner_only['legs'][0]['paired_by']),
                         ('3', '11', 'any'))  # a duplicate-numbered pad 3 is nearest pad 11 despite the foreign net
        self.assertAlmostEqual(partner_only['legs'][0]['gap_mm'], 0.5)
        # Neither declared: one minimum over all pads of both footprints.
        neither = pad_edge.claim_legs({'ref': 'Y1', 'near': 'U1', 'max_mm': 6.0}, pads)
        self.assertEqual(len(neither['legs']), 1)
        self.assertEqual(neither['legs'][0]['paired_by'], 'any')

    def test_duplicate_pad_numbers_all_select_and_undeclared_subject_emits_one_leg(self):
        claim = {'ref': 'Y1', 'near': 'U1', 'max_mm': 6.0, 'pads': {'Y1': ['3'], 'U1': ['9']}}
        result = pad_edge.claim_legs(claim, self.pads())
        self.assertEqual(len(result['legs']), 2)
        self.assertEqual({l['paired_by'] for l in result['legs']}, {'any'})
        result = pad_edge.claim_legs({'ref': 'Y1', 'near': 'U1', 'max_mm': 6.0}, self.pads())
        self.assertEqual(len(result['legs']), 1)
        self.assertEqual(result['pads_basis'], 'all_non_aperture')


@unittest.skipUnless((ESP / 'intent.json').exists(), 'retained esp_prog inputs are not present on this host')
class RetainedEspProgTests(unittest.TestCase):
    def test_original_bytes_adapt_and_are_preserved(self):
        intent_raw, groups_raw = (ESP / 'intent.json').read_bytes(), (ESP / 'groups.json').read_bytes()
        self.assertEqual(hashlib.sha256(intent_raw).hexdigest(), ESP_INTENT_SHA)
        self.assertEqual(hashlib.sha256(groups_raw).hexdigest(), ESP_GROUPS_SHA)
        record = placement_intent.adapt_bytes(intent_raw, groups_raw)
        self.assertEqual(record['source']['intent_sha256'], ESP_INTENT_SHA)
        self.assertEqual(hashlib.sha256((ESP / 'intent.json').read_bytes()).hexdigest(), ESP_INTENT_SHA)
        self.assertFalse(record['runnable'])
        self.assertEqual(sorted(c['id'] for c in record['unresolved_claims']),
                         ['interfaces[CON1].cable_envelope_mm', 'interfaces[CON2].cable_envelope_mm',
                          'interfaces[USB1].cable_envelope_mm'])
        self.assertEqual([r['ref'] for r in record['native_intent']['fixed_poses']], ['Ref*', 'Ref*~2'])
        self.assertEqual([r['ref'] for r in record['native_intent']['edge_connectors']], ['CON1', 'CON2', 'USB1'])
        self.assertEqual([(r['ref'], r['near']) for r in record['proximity_pad_edge']],
                         [('Y1', 'U1'), ('C2', 'Y1'), ('C4', 'Y1')])
        by_path = {d['path']: d for d in record['dispositions']}
        self.assertTrue(all(by_path[f'intent.proximity[{i}]']['authority'] == 'advisory_objective' for i in range(3)))
        self.assertEqual([g['name'] for g in record['native_groups']['groups']],
                         ['usb_in', 'power', 'core', 'reset', 'target'])
        self.assertEqual(set(record['withheld']), {'rules.decap_distance', 'rules.zone_containment',
                                                   'withheld.overlap_area', 'budget.overlap_area'})
        placement_intent.validate(record['native_intent'], record['native_groups'])
        # The current validator still refuses the original directly, as 520 requires.
        with self.assertRaises(ValueError):
            placement_intent.validate(json.loads(intent_raw), json.loads(groups_raw))

    def test_pad_edge_legs_replay_the_contract_measurements(self):
        try:
            import pcbnew
        except ImportError:
            self.skipTest('pcbnew is not importable in this interpreter')
        board_path = ESP / 'board.kicad_pcb'
        self.assertEqual(hashlib.sha256(board_path.read_bytes()).hexdigest(), ESP_BOARD_SHA)
        board = pcbnew.LoadBoard(str(board_path))
        intent = json.loads((ESP / 'intent.json').read_bytes())
        pads = {ref: pad_edge.pcbnew_pads(board, ref) for ref in ('Y1', 'U1', 'C2', 'C4')}
        expected = {(0, '1', '9'): 2.9704, (0, '2', '10'): 3.3268, (1, '1', '2'): 0.658, (2, '1', '1'): -0.075}
        seen = {}
        for index, claim in enumerate(intent['proximity']):
            result = pad_edge.claim_legs(claim, pads)
            self.assertEqual(result['status'], 'measured')
            for leg in result['legs']:
                seen[(index, leg['subject_pad'], leg['partner_pad'])] = round(leg['gap_mm'], 4)
                self.assertEqual(leg['paired_by'], 'net')
        self.assertEqual(seen, expected)


if __name__ == '__main__':
    unittest.main()


class GroupLinkTests(unittest.TestCase):
    def layout(self, c1_x=10.0):
        """Two groups: power {U2 anchor, C1}, core {U1 anchor}; rail GND shared, VBUS scoped."""
        def pad(number, x, y, net_id, net, w=1.0, h=1.0):
            return {'number': number, 'rect': (x - w / 2, y - h / 2, x + w / 2, y + h / 2), 'net_id': net_id, 'net': net}
        return {
            'U2': {'x': 0.0, 'y': 0.0, 'side': 'F', 'pads': [pad('1', -1.0, 0.0, 1, 'GND'), pad('2', 0.0, 0.0, 2, 'VBUS'),
                                                             pad('3', 1.0, 0.0, 3, '/+3.3V')]},
            'C1': {'x': c1_x, 'y': 0.0, 'side': 'F', 'pads': [pad('1', c1_x - 0.5, 0.0, 2, 'VBUS'),
                                                               pad('2', c1_x + 0.5, 0.0, 1, 'GND')]},
            'U1': {'x': 0.0, 'y': 20.0, 'side': 'F', 'pads': [pad('1', 0.0, 20.0, 3, '/+3.3V'), pad('2', 2.0, 20.0, 1, 'GND'),
                                                              pad('3', 4.0, 20.0, 4, '/SIG')]},
            'J1': {'x': 30.0, 'y': 20.0, 'side': 'F', 'pads': [pad('1', 30.0, 20.0, 4, '/SIG'), pad('2', 31.0, 20.0, 1, 'GND')]},
        }

    def groups(self):
        return {'groups': [{'name': 'power', 'anchor': 'U2', 'refs': ['U2', 'C1'], 'near': ['core']},
                           {'name': 'core', 'anchor': 'U1', 'refs': ['U1'], 'near': ['io']},
                           {'name': 'io', 'anchor': 'J1', 'refs': ['J1']}]}

    def rails(self):
        return {'shared_rails': {'GND': 'return', '/+3.3V': 'regulated rail'},
                'scoped': {'VBUS': {'intra_group': 'power', 'near_pairs': []}}}

    def test_links_use_signed_pad_edge_and_scoped_rails(self):
        from agent_ee import group_links
        report = group_links.measure(self.groups(), self.layout(), self.rails())
        self.assertTrue(report['S1_complete_partition']['complete'])
        self.assertEqual(report['S2_shared_rails']['unknown_multi_group_nets'], [])
        power = report['groups']['power']
        intra = power['R2_intra_group_signal_links']
        self.assertEqual([(l['net'], l['ref_a'], l['ref_b']) for l in intra], [('VBUS', 'C1', 'U2')])
        self.assertAlmostEqual(intra[0]['gap_mm'], 8.5)  # pad-edge gap, not the 9.5 centre distance
        link = power['R2_member_to_anchor_links'][0]
        self.assertEqual((link['member_pad'], link['anchor_pad'], link['shared_net']), ('1', '3', None))
        self.assertAlmostEqual(link['gap_mm'], 7.5)  # nearest anchor pad wins even across nets
        # Only rails cross power<->core, so the accepted-contract any-pad fallback tethers them.
        self.assertEqual(power['R1_declared_near_interfaces'][0]['paired_by'], 'any_pad_fallback')
        self.assertIsNone(power['R1_declared_near_interfaces'][0]['net'])
        core = report['groups']['core']['R1_declared_near_interfaces'][0]
        self.assertEqual((core['net'], core['from_ref'], core['to_ref'], core['gap_mm']), ('/SIG', 'U1', 'J1', 25.0))
        self.assertIn('rejected as unconditional hard release gates', power['diagnostic_only_not_hard_gate']['limitation'])
        self.assertEqual(report['authority'], 'measurements and diagnostics only; no automatic group pass')

    def test_objective_strictly_improves_when_a_dispersed_member_moves_closer(self):
        from agent_ee import group_links
        far = group_links.objective(group_links.measure(self.groups(), self.layout(c1_x=10.0), self.rails()))
        near = group_links.objective(group_links.measure(self.groups(), self.layout(c1_x=3.0), self.rails()))
        self.assertLess(near, far)
        self.assertAlmostEqual(far - near, 14.0)  # R2 signal 8.5->1.5 and anchor 7.5->0.5

    def test_partition_and_unknown_multi_group_nets_are_surfaced_not_classified(self):
        from agent_ee import group_links
        layout = self.layout()
        layout['R9'] = {'x': 5.0, 'y': 5.0, 'side': 'F', 'pads': [{'number': '1', 'rect': (4.5, 4.5, 5.5, 5.5), 'net_id': 4, 'net': '/SIG'}]}
        groups = self.groups()
        groups['groups'][0]['refs'].append('R9')
        report = group_links.measure(groups, layout, self.rails())
        self.assertEqual(report['S2_shared_rails']['unknown_multi_group_nets'], ['/SIG'])
        groups['groups'][1]['refs'].append('R9')
        s1 = group_links.partition(groups, layout)
        self.assertEqual(s1['duplicate_refs'], ['R9'])
        self.assertFalse(s1['complete'])
        self.assertEqual(group_links.partition(self.groups(), list(self.layout()) + ['Q7'])['ungrouped_refs'], ['Q7'])


ESP_FINAL = Path(os.environ.get('AGENT_EE_ESP_PROG_FINAL', ROOT / 'build/agent545/board-run-portfolio/final-a1'))
ESP_FINAL_SHA = 'dfffc042e9f4c60dce9da0a25523443dd63dd3ecf56ee50a0a744a6cbdde92a1'


@unittest.skipUnless((ESP_FINAL / 'board.kicad_pcb').exists() and (ESP / 'groups.json').exists(),
                     'retained esp_prog final-a1 board is not present on this host')
class RetainedGroupReplayTests(unittest.TestCase):
    def test_group_links_replay_the_contract_measurements(self):
        try:
            import pcbnew
        except ImportError:
            self.skipTest('pcbnew is not importable in this interpreter')
        from agent_ee import group_links
        board_path = ESP_FINAL / 'board.kicad_pcb'
        self.assertEqual(hashlib.sha256(board_path.read_bytes()).hexdigest(), ESP_FINAL_SHA)
        groups = json.loads((ESP / 'groups.json').read_bytes())
        refs = [r for g in groups['groups'] for r in g['refs']]
        board = pcbnew.LoadBoard(str(board_path))
        footprints = group_links.pcbnew_footprints(board, refs)
        rails = {'shared_rails': {'GND': 'ground return', '/+3.3V': 'groups.json power.why', 'Net-(C1-Pad1)': 'groups.json power.why'},
                 'scoped': {'Net-(C1-Pad1)': {'intra_group': 'power', 'near_pairs': [['usb_in', 'power'], ['power', 'usb_in']]}}}
        report = group_links.measure(groups, footprints, rails)
        power = report['groups']['power']
        self.assertEqual([(l['ref_a'], l['pad_a'], l['ref_b'], l['pad_b'], l['gap_mm']) for l in power['R2_intra_group_signal_links']],
                         [('C1', '1', 'U2', '2', 4.592)])
        self.assertEqual([(l['member'], l['member_pad'], l['anchor_pad'], l['shared_net'], l['gap_mm'])
                          for l in power['R2_member_to_anchor_links']],
                         [('C1', '1', '3', None, 3.642), ('C3', '1', '3', '/+3.3V', 5.242)])
        usb = {l['to_group']: l for l in report['groups']['usb_in']['R1_declared_near_interfaces']}
        self.assertEqual((usb['power']['from_pad'], usb['power']['to_ref'], usb['power']['to_pad'], usb['power']['gap_mm']),
                         ('1', 'U2', '2', 2.1621))
        self.assertEqual((usb['core']['from_pad'], usb['core']['to_ref'], usb['core']['to_pad'], usb['core']['net'], usb['core']['gap_mm']),
                         ('2', 'U1', '7', '/D_N', 12.8095))
        self.assertEqual(report['S2_shared_rails']['unknown_multi_group_nets'], [])
        self.assertTrue(report['S1_complete_partition']['complete'])


class GroupRailsSidecarTests(unittest.TestCase):
    def sidecar(self):
        return json.loads((ROOT / 'fixtures/group-rails/esp-prog.json').read_bytes())

    def test_fixture_sidecars_validate_and_bind_to_their_sources(self):
        from agent_ee import group_rails
        esp = self.sidecar()
        canonical = group_rails.validate(esp, board_sha256=ESP_BOARD_SHA, groups_sha256=ESP_GROUPS_SHA,
                                         group_names={'usb_in', 'power', 'core', 'reset', 'target'},
                                         nets={'GND', '/+3.3V', 'Net-(C1-Pad1)', '/D_N'})
        self.assertEqual(set(canonical['shared']), {'GND', '/+3.3V', 'Net-(C1-Pad1)'})
        self.assertEqual(canonical['scoped']['Net-(C1-Pad1)']['intra_group'], 'power')
        self.assertEqual(canonical['bindings']['board_sha256'], ESP_BOARD_SHA)
        acq = json.loads((ROOT / 'fixtures/group-rails/acquisition.json').read_bytes())
        self.assertEqual(set(group_rails.validate(acq)['shared']), {'GND', '/V3V3'})

    def test_stale_or_unknown_bindings_refuse_with_paths(self):
        from agent_ee import group_rails
        cases = [
            ('stale_board', dict(board_sha256='0' * 64), 'group_rails.bindings.board_sha256'),
            ('stale_groups', dict(groups_sha256='0' * 64), 'group_rails.bindings.groups_sha256'),
            ('unknown_net', dict(nets={'GND'}), 'group_rails.shared./+3.3V'),
            ('unknown_group', dict(group_names={'power'}), 'group_rails.shared./+3.3V.groups'),
        ]
        for label, kwargs, expected in cases:
            with self.subTest(case=label):
                with self.assertRaises(ValueError) as caught:
                    group_rails.validate(self.sidecar(), **kwargs)
                self.assertIn(expected, str(caught.exception))
        doc = self.sidecar()
        doc['scoped']['/D_N'] = {'evidence': 'x'}
        with self.assertRaises(ValueError) as caught:
            group_rails.validate(doc)
        self.assertIn('group_rails.scoped./D_N', str(caught.exception))
        doc = self.sidecar()
        doc['shared']['GND'] = 'ground'
        with self.assertRaises(ValueError) as caught:
            group_rails.validate(doc)
        self.assertIn('group_rails.shared.GND', str(caught.exception))
        doc = self.sidecar()
        doc['classifier'] = 'by-name'
        with self.assertRaises(ValueError) as caught:
            group_rails.validate(doc)
        self.assertIn('group_rails.classifier', str(caught.exception))


class NearFallbackTests(GroupLinkTests):
    def test_rail_only_near_groups_use_any_pad_fallback_and_follow_movement(self):
        from agent_ee import group_links
        groups = {'groups': [{'name': 'power', 'anchor': 'U2', 'refs': ['U2', 'C1'], 'near': ['io']},
                             {'name': 'io', 'anchor': 'J1', 'refs': ['J1']}]}
        rails = {'shared': {'GND': 'return'}, 'scoped': {}}
        layout = self.layout()
        del layout['U1']
        near = group_links.measure(groups, layout, rails)['groups']['power']['R1_declared_near_interfaces'][0]
        self.assertEqual(near['paired_by'], 'any_pad_fallback')
        self.assertIsNone(near['net'])
        self.assertEqual(near['candidate_pairs'], 5 * 2)
        far_layout = self.layout()
        del far_layout['U1']
        far_layout['J1'] = {'x': 60.0, 'y': 20.0, 'side': 'F',
                            'pads': [{'number': '1', 'rect': (59.5, 19.5, 60.5, 20.5), 'net_id': 4, 'net': '/SIG'},
                                     {'number': '2', 'rect': (60.5, 19.5, 61.5, 20.5), 'net_id': 1, 'net': 'GND'}]}
        far = group_links.measure(groups, far_layout, rails)['groups']['power']['R1_declared_near_interfaces'][0]
        self.assertGreater(far['gap_mm'], near['gap_mm'])
        self.assertGreater(group_links.objective(group_links.measure(groups, far_layout, rails)),
                           group_links.objective(group_links.measure(groups, layout, rails)))
        self.assertEqual((near['from_ref'], near['from_pad'], near['to_ref'], near['to_pad']), ('C1', '2', 'J1', '1'))


class CaptureDeclarationModeTests(unittest.TestCase):
    """Declaration modes survive native capture: both declared, partner only, neither; missing declared pad refuses."""

    def setUp(self):
        from tests.test_placement_job import Fixture
        fixture = Fixture('setUp')
        fixture.setUp()
        self.board = fixture.board
        self.addCleanup(lambda: None)

    def dialect(self, pads):
        document = minimal_dialect()
        document['fixed_poses'] = [{'ref': 'FID', 'x': 3.0, 'y': 3.0, 'rot': 0.0, 'side': 'F', 'basis': 'mechanical', 'why': 'w'}]
        document['edge_connectors'] = []
        document['envelope'] = {'rect': [0.0, 0.0, 20.0, 12.0], 'tolerance_mm': 0.5}
        document['context']['brief'] = {'consequences': []}
        document['proximity'] = [{'ref': 'C1', 'near': 'R1', 'max_mm': 3.0, 'basis': 'pad_edge', 'pads': pads,
                                  'context': {'limit': 'derived_default', 'basis': 'corpus', 'role': 'r', 'source': 's'}}]
        return document

    def groups(self):
        return {'schema': 1, 'board': 'board.kicad_pcb', 'groups': [{'name': 'g', 'anchor': 'R1', 'refs': ['R1', 'C1'], 'why': 'w'}]}

    def test_modes_reach_the_engine_input_and_missing_declared_pads_refuse(self):
        from agent_ee import placement_job as job
        try:
            import pcbnew  # noqa: F401
        except ImportError:
            self.skipTest('pcbnew is not importable')
        both = job.capture(self.board, self.dialect({'C1': ['1'], 'R1': ['1']}), self.groups())[0]['proximity'][0]
        self.assertEqual((both['subject_declared'], both['partner_declared'], len(both['ref_pads']), len(both['near_pads'])),
                         (True, True, 1, 1))
        partner = job.capture(self.board, self.dialect({'R1': ['1']}), self.groups())[0]['proximity'][0]
        self.assertEqual((partner['subject_declared'], partner['partner_declared'], len(partner['near_pads'])), (False, True, 1))
        self.assertEqual(len(partner['ref_pads']), 2, 'an undeclared subject selects all non-aperture pads')
        neither = job.capture(self.board, self.dialect({}), self.groups())[0]['proximity'][0]
        self.assertEqual((neither['subject_declared'], neither['partner_declared']), (False, False))
        self.assertEqual((len(neither['ref_pads']), len(neither['near_pads'])), (2, 2))
        with self.assertRaises(job.JobRefusal) as caught:
            job.capture(self.board, self.dialect({'C1': ['9'], 'R1': ['1']}), self.groups())
        self.assertIn('intent.proximity[0].pads.C1', str(caught.exception))


class ChiefCounterexampleTests(unittest.TestCase):
    """Chief review 7e9c18a6 of 87bbbf05: unknown multi-group nets must not seed signal candidates."""

    def inputs(self):
        groups = {'groups': [{'name': 'A', 'anchor': 'U1', 'refs': ['U1', 'R1'], 'near': ['B']},
                             {'name': 'B', 'anchor': 'U2', 'refs': ['U2']}, {'name': 'C', 'anchor': 'U3', 'refs': ['U3']}]}
        footprints = {
            'U1': {'x': 0, 'y': 0, 'side': 'F', 'pads': [{'number': '1', 'rect': (0, 0, 1, 1), 'net': 'X', 'net_id': 1},
                                                         {'number': '2', 'rect': (2, 0, 3, 1), 'net': '/S', 'net_id': 2}]},
            'R1': {'x': 7, 'y': 0, 'side': 'F', 'pads': [{'number': '1', 'rect': (7, 0, 8, 1), 'net': '/S', 'net_id': 2},
                                                         {'number': '2', 'rect': (7, 0, 8, 1), 'net': 'X', 'net_id': 1}]},
            'U2': {'x': 20, 'y': 0, 'side': 'F', 'pads': [{'number': '1', 'rect': (20, 0, 21, 1), 'net': 'X', 'net_id': 1}]},
            'U3': {'x': 30, 'y': 0, 'side': 'F', 'pads': [{'number': '1', 'rect': (30, 0, 31, 1), 'net': 'X', 'net_id': 1}]}}
        return groups, footprints

    def test_unknown_multi_group_net_is_visible_and_never_a_signal_candidate(self):
        from agent_ee import group_links
        groups, footprints = self.inputs()
        report = group_links.measure(groups, footprints, {'shared': {}, 'scoped': {}})
        self.assertEqual(report['S2_shared_rails']['unknown_multi_group_nets'], ['X'])
        a = report['groups']['A']
        self.assertEqual([l['net'] for l in a['R2_intra_group_signal_links']], ['/S'])
        near = a['R1_declared_near_interfaces'][0]
        self.assertEqual(near['paired_by'], 'any_pad_fallback')
        self.assertIsNone(near['net'])
        self.assertEqual(near['candidate_pairs'], 4)  # all four A pads against the one B pad
        self.assertEqual((near['from_ref'], near['from_pad'], near['to_ref'], near['to_pad'], near['gap_mm']),
                         ('R1', '1', 'U2', '1', 12.0))

    def test_rails_annotations_must_match_board_membership_and_shapes(self):
        from agent_ee import group_rails
        base = {'schema': group_rails.SCHEMA, 'bindings': {'board_sha256': 'a' * 64, 'groups_sha256': 'b' * 64},
                'shared': {'X': {'evidence': 'e', 'groups': ['A', 'B', 'C'], 'parts': ['U1', 'U2']}},
                'scoped': {'X': {'evidence': 'e', 'intra_group': 'A', 'near_pairs': [['A', 'B']]}}}
        membership = dict(group_names={'A', 'B', 'C'}, nets={'X', '/S'},
                          net_groups={'X': {'A', 'B', 'C'}, '/S': {'A'}}, net_parts={'X': {'U1', 'R1', 'U2', 'U3'}, '/S': {'U1', 'R1'}})
        group_rails.validate(base, **membership)
        cases = [
            ('missing_group', lambda d: d['shared']['X'].__setitem__('groups', ['A', 'MISSING_GROUP']), 'group_rails.shared.X.groups'),
            ('group_without_pad', lambda d: d['shared'].__setitem__('/S', {'evidence': 'e', 'groups': ['B']}), 'group_rails.shared./S.groups'),
            ('missing_part', lambda d: d['shared']['X'].__setitem__('parts', ['U1', 'MISSING_PART']), 'group_rails.shared.X.parts'),
            ('duplicate_group', lambda d: d['shared']['X'].__setitem__('groups', ['A', 'A']), 'group_rails.shared.X.groups'),
            ('empty_parts', lambda d: d['shared']['X'].__setitem__('parts', []), 'group_rails.shared.X.parts'),
            ('near_pairs_object', lambda d: d['scoped']['X'].__setitem__('near_pairs', {}), 'group_rails.scoped.X.near_pairs'),
            ('near_pair_without_pad', lambda d: (d['shared'].__setitem__('/S', {'evidence': 'e'}),
                                                 d['scoped'].__setitem__('/S', {'evidence': 'e', 'near_pairs': [['A', 'B']]})),
             'group_rails.scoped./S.near_pairs[0]'),
            ('intra_group_without_pad', lambda d: (d['shared'].__setitem__('/S', {'evidence': 'e'}),
                                                   d['scoped'].__setitem__('/S', {'evidence': 'e', 'intra_group': 'B'})),
             'group_rails.scoped./S.intra_group'),
            ('duplicate_pair', lambda d: d['scoped']['X'].__setitem__('near_pairs', [['A', 'B'], ['A', 'B']]), 'near_pairs[1]'),
        ]
        for label, mutate, expected in cases:
            with self.subTest(case=label):
                doc = json.loads(json.dumps(base))
                mutate(doc)
                with self.assertRaises(ValueError) as caught:
                    group_rails.validate(doc, **membership)
                self.assertIn(expected, str(caught.exception))


class EngineParityTests(unittest.TestCase):
    """Same poses, same candidate sets, same argmin and the same unweighted gap in Python and Rust."""

    def engine(self):
        from agent_ee.placement_rust_runtime import resolve_executable
        try:
            return resolve_executable()
        except Exception as exc:
            self.skipTest(f'placement engine unavailable: {exc}')

    def problem(self):
        def fp(n, ref, x, y, net):
            uuid = f'00000000-0000-0000-0000-0000{n:08x}'
            return {'uuid': uuid, 'native_ref': ref, 'role': 'movable', 'pose': {'x_nm': x, 'y_nm': y, 'quarter_turns': 0},
                    'rotations': [0, 1, 2, 3],
                    'occupancy': {'status': 'derived', 'label': 'derived_occupancy', 'rung': 'pads',
                                  'local_rect_nm': [-1_000_000, -500_000, 1_000_000, 500_000]},
                    'copper_local_nm': [[-900_000, -300_000, -300_000, 300_000], [300_000, -300_000, 900_000, 300_000]],
                    'pads': [{'uuid': f'{uuid}-1', 'number': '1', 'net': net, 'local_nm': [-600_000, 0], 'half_nm': [300_000, 300_000]},
                             {'uuid': f'{uuid}-2', 'number': '2', 'net': 'GND', 'local_nm': [600_000, 0], 'half_nm': [300_000, 300_000]}]}
        from agent_ee import placement_job as job
        return {'schema': job.INPUT_SCHEMA, 'problem_sha256': 'a' * 64,
                'outline': {'kind': 'rectangle', 'rect_nm': [0, 0, 12_000_000, 10_000_000]},
                'rules': {'copper_clearance_nm': 200_000, 'copper_edge_clearance_nm': 300_000, 'mask_min_web_nm': 100_000},
                'footprints': [fp(1, 'R1', 3_000_000, 5_000_000, 'A'), fp(2, 'R2', 9_500_000, 5_000_000, 'A'),
                               fp(3, 'R3', 9_000_000, 2_000_000, 'B'), fp(4, 'R4', 2_000_000, 8_000_000, 'B')],
                'groups': [{'name': 'g', 'anchor': 'R1', 'refs': ['R1', 'R2'], 'near': ['h']},
                           {'name': 'h', 'anchor': 'R3', 'refs': ['R3', 'R4']}],
                'rails': {'shared': {'GND': 'ground return'}, 'scoped': {}},
                'weights': {'wire': 1, 'group': 1, 'proximity': 4, 'link': 1, 'edge': 4, 'hard': 50, 'escape': 20},
                'evaluate': [{}]}

    def test_links_agree_between_python_report_and_rust_engine(self):
        self.check_parity(self.problem())

    def test_explicit_apertures_in_anchor_and_near_endpoints_keep_report_and_engine_in_parity(self):
        problem = self.problem()
        # A copper-free aperture on member R2 placed right beside anchor R1, and one on near-group R3
        # beside R2: neither may become a link endpoint in Python or Rust.
        problem['footprints'][1]['pads'].append({'uuid': 'aperture-r2', 'number': '', 'net': '', 'local_nm': [-7_100_000, 0], 'half_nm': [0, 0]})
        problem['footprints'][2]['pads'].append({'uuid': 'aperture-r3', 'number': '', 'net': 'GND', 'local_nm': [0, 3_000_000], 'half_nm': [0, 0]})
        evaluation = self.check_parity(problem)
        pads = {l['a_pad'] for l in evaluation['links']} | {l['b_pad'] for l in evaluation['links']}
        self.assertFalse(pads & {'aperture-r2', 'aperture-r3'})
        clean = self.check_parity(self.problem())
        self.assertEqual(evaluation['links'], clean['links'])
        self.assertEqual(evaluation['terms'], clean['terms'])

    def test_all_aperture_member_has_no_anchor_link_and_nothing_is_forged(self):
        problem = self.problem()
        for pad in problem['footprints'][1]['pads']:
            pad['half_nm'] = [0, 0]
        evaluation = self.check_parity(problem)
        self.assertNotIn(('R2_member_to_anchor', 'g'), {(l['kind'], l['group']) for l in evaluation['links']})
        self.assertTrue(all(isinstance(l['gap_nm'], int) and l['candidate_pairs'] > 0 for l in evaluation['links']))

    def check_parity(self, problem):
        import subprocess
        from agent_ee import group_links
        executable = self.engine()
        done = subprocess.run([str(executable)], input=json.dumps(problem).encode(), capture_output=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr.decode(errors='replace')[-800:])
        evaluation = json.loads(done.stdout)['evaluations'][0]
        rust_links = evaluation['links']
        footprints, pad_numbers = {}, {}
        for row in problem['footprints']:
            pads = []
            for pad in row['pads']:
                if pad['half_nm'] == [0, 0]:
                    continue  # pcbnew_footprints reports non-aperture pads only
                cx, cy = (row['pose']['x_nm'] + pad['local_nm'][0]) / 1e6, (row['pose']['y_nm'] + pad['local_nm'][1]) / 1e6
                hx, hy = pad['half_nm'][0] / 1e6, pad['half_nm'][1] / 1e6
                pads.append({'number': pad['number'], 'rect': (cx - hx, cy - hy, cx + hx, cy + hy),
                             'net_id': {'A': 1, 'B': 2, 'GND': 3}[pad['net']], 'net': pad['net']})
                pad_numbers[pad['uuid']] = pad['number']
            footprints[row['native_ref']] = {'x': row['pose']['x_nm'] / 1e6, 'y': row['pose']['y_nm'] / 1e6, 'side': 'F', 'pads': pads}
        report = group_links.measure({'groups': problem['groups']}, footprints, problem['rails'])
        by_kind = {(r['kind'], r['group']): r for r in rust_links}
        g = report['groups']['g']
        if g['R2_intra_group_signal_links']:
            intra, rust_intra = g['R2_intra_group_signal_links'][0], by_kind[('R2_intra_group_signal', 'g')]
            self.assertEqual(rust_intra['candidate_pairs'], 1)
            self.assertEqual((pad_numbers[rust_intra['a_pad']], pad_numbers[rust_intra['b_pad']]), (intra['pad_a'], intra['pad_b']))
            self.assertAlmostEqual(rust_intra['gap_nm'] / 1e6, intra['gap_mm'], places=4)
        else:
            self.assertNotIn(('R2_intra_group_signal', 'g'), by_kind)
        rust_anchor = [l for l in rust_links if l['kind'] == 'R2_member_to_anchor' and l['group'] == 'g']
        self.assertEqual([(l['a'], pad_numbers[l['a_pad']], pad_numbers[l['b_pad']], round(l['gap_nm'] / 1e6, 4)) for l in rust_anchor],
                         [(l['member'], l['member_pad'], l['anchor_pad'], l['gap_mm']) for l in g['R2_member_to_anchor_links']])
        near, rust_near = g['R1_declared_near_interfaces'][0], by_kind[('R1_declared_near_interface', 'g')]
        self.assertEqual((rust_near['paired_by'], near['paired_by']), ('any_pad_fallback', 'any_pad_fallback'))
        self.assertEqual(rust_near['candidate_pairs'], near['candidate_pairs'])
        self.assertEqual((rust_near['a'], pad_numbers[rust_near['a_pad']], rust_near['b'], pad_numbers[rust_near['b_pad']]),
                         (near['from_ref'], near['from_pad'], near['to_ref'], near['to_pad']))
        self.assertAlmostEqual(rust_near['gap_nm'] / 1e6, near['gap_mm'], places=4)
        unweighted_nm = sum(max(0, r['gap_nm']) for r in rust_links)
        self.assertEqual(int(evaluation['terms']['link_gap_nm']), unweighted_nm)
        self.assertEqual(evaluation['link_weight'], 1)
        self.assertAlmostEqual(unweighted_nm / 1e6, group_links.objective(report), places=3)
        return evaluation


class CaptureContractTests(EngineParityTests):
    """Schema /2 is the modern contract; unmarked /1 records replay as labelled legacy pad-centre jobs."""

    def run_engine(self, problem):
        import subprocess
        done = subprocess.run([str(self.engine())], input=json.dumps(problem).encode(), capture_output=True, timeout=120)
        return done.returncode, json.loads(done.stdout)

    def legacy(self):
        from agent_ee import placement_job as job
        problem = self.problem()
        problem['schema'] = job.LEGACY_INPUT_SCHEMA
        problem.pop('rails')
        problem['weights'].pop('link')
        for group in problem['groups']:
            group.pop('near', None)
        for row in problem['footprints']:
            for pad in row['pads']:
                pad.pop('half_nm')
        problem['proximity'] = [{'ref': 'R2', 'near': 'R1', 'ref_pads': [problem['footprints'][1]['pads'][0]['uuid']],
                                 'near_pads': [pad['uuid'] for pad in problem['footprints'][0]['pads']],
                                 'max_nm': 0, 'basis': 'pad_edge'}]
        return problem

    def test_fresh_capture_declares_the_modern_contract(self):
        from agent_ee import placement_job as job
        self.assertEqual(job.INPUT_SCHEMA, 'agent-ee.placement-job-input/2')
        self.assertNotEqual(job.INPUT_SCHEMA, job.LEGACY_INPUT_SCHEMA)
        code, out = self.run_engine(self.problem())
        self.assertEqual(code, 0, out)
        self.assertEqual(out['proximity_contract'], 'pad_edge_signed_rect/1')
        self.assertEqual(out['evaluations'][0]['link_weight'], 1)

    def test_legacy_record_is_labelled_pad_centre_without_links(self):
        code, out = self.run_engine(self.legacy())
        self.assertEqual(code, 0, out)
        self.assertEqual(out['proximity_contract'], 'legacy_pad_centre/1')
        evaluation = out['evaluations'][0]
        self.assertEqual(evaluation['proximity_contract'], 'legacy_pad_centre/1')
        self.assertEqual([(r['basis'], r['metric_applied'], r['pad_edge_scored']) for r in evaluation['proximity']],
                         [('pad_edge', 'legacy_pad_centre/1', False)])
        self.assertEqual((evaluation['links'], evaluation['link_weight']), ([], 0))
        self.assertEqual(evaluation['terms']['link_gap_nm'], '0')

    def test_modern_record_without_proximity_rows_refuses_absent_or_null_half_extents(self):
        # Chief counterexample on dfc47810: with no proximity rows a missing/null half_nm silently centred.
        for value in ('absent', None, [300_000], ['a', 'b'], [-1, 1], [0, 300_000]):
            problem = self.problem()
            pad = problem['footprints'][1]['pads'][0]
            if value == 'absent':
                pad.pop('half_nm')
            else:
                pad['half_nm'] = value
            code, out = self.run_engine(problem)
            self.assertEqual(code, 1, (value, out))
            self.assertIn(problem['footprints'][1]['uuid'], out['error'])
            self.assertIn('half', out['error'])
        code, out = self.run_engine(self.problem())
        self.assertEqual((code, out['proximity_contract']), (0, 'pad_edge_signed_rect/1'))

    def test_modern_record_refuses_missing_or_mixed_extents_and_legacy_refuses_modern_fields(self):
        modern = self.legacy()
        from agent_ee import placement_job as job
        modern['schema'] = job.INPUT_SCHEMA
        code, out = self.run_engine(modern)
        self.assertEqual(code, 1)
        self.assertIn('lacks half_nm', out['error'])
        # Every pad measured, but the pad_edge row names an explicit aperture: mixed geometry refuses.
        for row in modern['footprints']:
            for pad in row['pads']:
                pad['half_nm'] = [300_000, 300_000]
        modern['footprints'][1]['pads'][0]['half_nm'] = [0, 0]
        code, out = self.run_engine(modern)
        self.assertEqual((code, out['error']), (1, 'pad_edge proximity needs captured pad half extents'))
        marked = self.legacy()
        marked['footprints'][0]['pads'][0]['half_nm'] = [300_000, 300_000]
        code, out = self.run_engine(marked)
        self.assertEqual(code, 1)
        self.assertIn('carries pad half extents', out['error'])
        unknown = self.legacy()
        unknown['schema'] = 'agent-ee.placement-job-input/3'
        code, out = self.run_engine(unknown)
        # The binary routes unknown schemas to the planner entry, which refuses them by name too.
        self.assertEqual((code, out['error']), (1, 'unsupported planning input schema'))

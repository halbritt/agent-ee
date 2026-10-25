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
            ('empty_pads', with_prox(pads={}), 'intent.proximity[0].pads'),
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
        # The undeclared-subject fallback stays permitted: partner pads only.
        record = placement_intent.adapt(with_prox(pads={'U1': ['9', '10']}), minimal_groups())
        self.assertEqual(record['proximity_pad_edge'][0]['pads'], {'U1': ['9', '10']})

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
        self.assertEqual(power['R1_declared_near_interfaces'][0]['paired_by'], 'any')
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
            ('unknown_group', dict(group_names={'power'}), 'group_rails.scoped.Net-(C1-Pad1).near_pairs[0]'),
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
        self.assertEqual(near['paired_by'], 'any')
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

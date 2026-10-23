"""Saved-coordinate contracts, independent of search and native writer success."""
from copy import deepcopy
import hashlib
import json
import unittest

from agent_ee import block_contract, electrical_locality
from test_placement import capture, part, MM


def fixture():
    board = capture([part('U1', 2, 2), part('C1', 4, 2), part('H1', 10, 10), part('TP1', 12, 12)])
    for fp in board['footprints'][:2]:
        fp['pads'] = [{'number': str(i+1), 'uuid': f"{fp['ref']}-{i+1}", 'net': net,
                      'position_nm': [fp['position_nm'][0], fp['position_nm'][1]+i*MM],
                      'copper_centroid_offset_nm': [[0, 1], [0, 1]]}
                     for i, net in enumerate(('VCC', 'GND'))]
    allocation = {'schema': 'agent-ee.block-allocation/2', 'board_sha256': board['board_sha256'],
                  'blocks': [{'id': 'device', 'references': ['U1', 'C1'], 'anchor': {'member': 'U1'},
                              'interfaces_to_fixed': None}],
                  'fixed': [{'reference': 'H1'}], 'shared': [{'reference': 'TP1', 'role': 'test'}]}
    text = 'Original synthetic numeric requirement: 3 mm'
    source = {'path': 'fixture/limits.txt', 'sha256': hashlib.sha256(text.encode()).hexdigest(),
              'locator': 'numeric requirement', 'quote': text}

    def pair(pad, net):
        return {'from': {'reference': 'U1', 'pad': pad, 'net': net},
                'to': {'reference': 'C1', 'pad': pad, 'net': net}, 'max_distance_nm': 3*MM,
                'citation': 'fixture/limits.txt numeric requirement',
                'provenance': {'class': 'datasheet', 'basis': 'authored numeric fixture',
                    'derivation': {'kind': 'numeric', 'value_nm': 3*MM, 'source': source}}}
    contract = {'schema': block_contract.SCHEMA, 'allocation': allocation,
        'eligibility': {'schema': 'fome-mc.side-eligibility/1', 'sources': {'board_sha256': board['board_sha256']},
                        'default': {'faces': ['F', 'B']}, 'blocks': {'device': {'eligible_faces': ['F', 'B']}},
                        'fixed': {'H1': {'eligible_faces': ['F', 'B']}},
                        'shared': {'TP1': {'eligible_faces': ['F', 'B']}}},
        'relationships': {'schema': 'agent-ee.electrical-locality/1', 'source_records': {source['path']: text},
                          'obligations': [{'id': 'support', 'metric': 'pad-centre-to-centre',
                                           'supply': pair('1', 'VCC'), 'return': pair('2', 'GND')}]},
        'templates': [], 'thickness_nm': 1600000}
    return board, contract


def moved(board, ref, dx=0, dy=0, side=None):
    result = deepcopy(board)
    result['board_sha256'] = 'b'*64
    fp = next(f for f in result['footprints'] if f['ref'] == ref)
    fp['position_nm'] = [fp['position_nm'][0]+dx, fp['position_nm'][1]+dy]
    for record in fp['courtyards'].values():
        for polygon in record['polygons']:
            for p in polygon:
                p[0] += dx
                p[1] += dy
    for pad in fp['pads']:
        pad['position_nm'][0] += dx
        pad['position_nm'][1] += dy
    if side is not None:
        fp['courtyards'] = {side: fp['courtyards'][fp['side']]}
        fp['side'] = side
    return result


def explicit_return_fixture(device_ref='U100', capacitor_ref='C133'):
    """Authored geometry and 5mm test limit, not production endpoint authority."""
    board, contract = fixture()
    names = {'U1': device_ref, 'C1': capacitor_ref}
    nets = ('/+3V3', '/+5V_CAN')
    for fp in board['footprints'][:2]:
        fp['ref'] = names[fp['ref']]
        fp['uuid'] = fp['ref']
        for pad, number, net in zip(fp['pads'], ('5', '3') if fp['ref'] == device_ref else ('1', '2'), nets):
            pad.update(number=number, net=net, uuid=f"{fp['ref']}-{number}")
    block = contract['allocation']['blocks'][0]
    block.update(references=[device_ref, capacitor_ref], anchor={'member': device_ref})
    text = 'Original authored regression requirement: 5 mm'
    source = {'path': 'fixture/explicit-return-5mm.txt', 'sha256': hashlib.sha256(text.encode()).hexdigest(),
              'locator': 'numeric test requirement', 'quote': text}
    relationships = contract['relationships']
    relationships['source_records'] = {source['path']: text}
    obligation = relationships['obligations'][0]
    for role, source_pad, target_pad, net in (('supply', '5', '1', '/+3V3'),
                                              ('return', '3', '2', '/+5V_CAN')):
        obligation[role] = {
            'from': {'reference': device_ref, 'pad': source_pad, 'net': net},
            'to': {'reference': capacitor_ref, 'pad': target_pad, 'net': net},
            'max_distance_nm': 5*MM, 'citation': source['path'] + ' numeric test requirement',
            'provenance': {'class': 'datasheet', 'basis': 'authored regression fixture only',
                'derivation': {'kind': 'numeric', 'value_nm': 5*MM, 'source': source}}}
    return board, contract


class NonGroundReturnContractTest(unittest.TestCase):
    def test_explicit_non_ground_return_is_measured_by_all_three_checks(self):
        for device_ref, capacitor_ref in (('U100', 'C133'), ('U21', 'C84')):
            with self.subTest(device=device_ref, capacitor=capacitor_ref):
                board, contract = explicit_return_fixture(device_ref, capacitor_ref)
                before = deepcopy((board, contract))
                self.assertEqual({p['net'] for fp in board['footprints'] for p in fp['pads']}, {'/+3V3', '/+5V_CAN'})
                generic = electrical_locality.grade(board, contract['relationships'])
                complete = block_contract.check_complete(board, contract)
                saved = block_contract.judge(board, contract, original_capture=board)
                for label, report, rows, distance_key in (
                        ('locality', generic, generic['measured'], 'distance_squared_nm'),
                        ('planning', complete, complete['relationships'], 'distance_squared_nm2'),
                        ('saved', saved, saved['relationships'], 'distance_squared_nm2')):
                    with self.subTest(check=label):
                        self.assertEqual(report['status'], 'pass', report)
                        self.assertEqual({row['role']: row[distance_key] for row in rows},
                                         {'supply': 4*MM**2, 'return': 4*MM**2})
                by_role = {row['role']: row for row in generic['measured']}
                self.assertEqual(by_role['return']['from'], {'reference': device_ref, 'pad': '3', 'net': '/+5V_CAN'})
                self.assertEqual(by_role['return']['to'], {'reference': capacitor_ref, 'pad': '2', 'net': '/+5V_CAN'})
                self.assertEqual((board, contract), before)

    def test_missing_explicit_return_is_rejected_by_all_three_checks(self):
        board, contract = explicit_return_fixture()
        del contract['relationships']['obligations'][0]['return']
        checks = {
            'locality': lambda: electrical_locality.grade(board, contract['relationships']),
            'planning': lambda: block_contract.check_complete(board, contract),
            'saved': lambda: block_contract.judge(board, contract, original_capture=board),
        }
        for label, check in checks.items():
            with self.subTest(check=label), self.assertRaisesRegex(ValueError, 'supply and return'):
                check()

    def test_missing_wrong_net_or_unmeasurable_return_cannot_pass(self):
        for change, reason in (('missing_source_pad', 'pad missing'),
                               ('missing_capacitor_pad', 'pad missing'),
                               ('historical_ground_net', 'native pad net differs'),
                               ('missing_centroid', 'native copper centroid unavailable')):
            board, contract = explicit_return_fixture()
            altered = deepcopy(board)
            device, capacitor = altered['footprints'][:2]
            if change == 'missing_source_pad':
                device['pads'].pop(1)
            elif change == 'missing_capacitor_pad':
                capacitor['pads'].pop(1)
            elif change == 'historical_ground_net':
                # The historical /GND endpoint does not satisfy the adopted /+5V_CAN declaration.
                capacitor['pads'][1]['net'] = '/GND'
            else:
                del capacitor['pads'][1]['copper_centroid_offset_nm']
                capacitor['pads'][1]['copper_centroid_reason'] = 'authored unavailable geometry'
            generic = electrical_locality.grade(altered, contract['relationships'])
            complete = block_contract.check_complete(altered, contract)
            saved = block_contract.judge(altered, contract, original_capture=board)
            for label, report in (('locality', generic), ('planning', complete), ('saved', saved)):
                with self.subTest(change=change, check=label):
                    self.assertNotIn(report['status'], ('pass', 'info'), report)
                    unavailable = [row for row in report['unknown'] if row.get('role') == 'return']
                    self.assertEqual(len(unavailable), 1, report)
                    self.assertIn(reason, unavailable[0]['reason'])
            self.assertEqual({row['role']: row['distance_squared_nm'] for row in generic['measured']},
                             {'supply': 4*MM**2})

    def test_far_return_is_a_defect_while_supply_remains_close(self):
        board, contract = explicit_return_fixture()
        altered = deepcopy(board)
        # Only the return endpoint moves: supply remains 2mm, return becomes 6mm against a 5mm limit.
        altered['footprints'][1]['pads'][1]['position_nm'][0] += 4*MM
        generic = electrical_locality.grade(altered, contract['relationships'])
        complete = block_contract.check_complete(altered, contract)
        saved = block_contract.judge(altered, contract, original_capture=board)
        for label, report, rows, distance_key, defect_kind in (
                ('locality', generic, generic['measured'], 'distance_squared_nm', 'electrical_locality_distance'),
                ('planning', complete, complete['relationships'], 'distance_squared_nm2', 'pin_distance_exceeded'),
                ('saved', saved, saved['relationships'], 'distance_squared_nm2', 'pin_distance_exceeded')):
            with self.subTest(check=label):
                self.assertEqual(report['status'], 'defect', report)
                self.assertEqual({row['role']: row[distance_key] for row in rows},
                                 {'supply': 4*MM**2, 'return': 36*MM**2})
                self.assertEqual([(row['kind'], row.get('role')) for row in report['defects']],
                                 [(defect_kind, 'return')])


class BlockContractTest(unittest.TestCase):
    def test_total_roles_include_board_only_and_preserve_input(self):
        board, contract = fixture()
        before = deepcopy((board, contract))
        normalized = block_contract.membership(board, contract['allocation'])
        self.assertEqual(normalized['roles']['H1'], {'role': 'fixed'})
        self.assertEqual(normalized['roles']['TP1'], {'role': 'shared', 'shared_role': 'test'})
        report = block_contract.judge(board, contract, original_capture=board)
        self.assertEqual(report['status'], 'pass', report)
        self.assertEqual(report['blocks'][0]['anchor_spread_squared_nm2'], 4*MM**2)
        self.assertEqual(report['blocks'][0]['internal_connections']['nets'][0]['hpwl_3d_nm'], 2*MM)
        self.assertEqual((board, contract), before)

    def test_planning_preflight_binds_bridge_policy_and_eligibility(self):
        board, contract = fixture()
        section = {'contract': contract, 'side_eligibility': deepcopy(contract['eligibility']), 'candidates': [],
                   'allocation_text': json.dumps({'schema': 'agent-ee.block-allocation/1', 'blocks': [
                       {'id': 'device', 'references': ['U1', 'C1'], 'anchor': 'U1'}]})}
        policy = {'electrical_locality': deepcopy(contract['relationships'])}
        self.assertEqual(block_contract.validate(board, section, policy)['roles']['H1'], {'role': 'fixed'})
        for change in ('eligibility', 'policy', 'bridge'):
            candidate, altered_policy = deepcopy(section), deepcopy(policy)
            if change == 'eligibility': candidate['side_eligibility']['default']['faces'] = ['F']
            elif change == 'policy': altered_policy['electrical_locality']['obligations'][0]['supply']['max_distance_nm'] += 1
            else: candidate['allocation_text'] = json.dumps({'schema': 'agent-ee.block-allocation/1', 'blocks': []})
            with self.subTest(change=change), self.assertRaises(ValueError):
                block_contract.validate(board, candidate, altered_policy)
        with self.assertRaisesRegex(ValueError, 'require block contract'):
            block_contract.validate(board, {'candidates': [{'candidate': {'schema': 'agent-ee.block-candidate-set/1'}}]}, policy)

    def test_shared_non_null_constraint_stays_unresolved_even_when_empty(self):
        # No shared-constraint evaluator exists: an explicit declaration cannot
        # become permission merely because its JSON value is falsey.
        for value in ({}, [], '', False, 0, {'same_face_as': 'U1'}):
            board, contract = fixture()
            entry = contract['eligibility']['shared']['TP1']
            entry['constraint'] = deepcopy(value)
            before = deepcopy((board, contract))
            for check, report in (
                    ('planning', block_contract.check_complete(board, contract)),
                    ('saved', block_contract.judge(board, contract, original_capture=board))):
                with self.subTest(value=value, check=check):
                    self.assertEqual(report['status'], 'unknown', report)
                    self.assertEqual(report['defects'], [])
                    self.assertEqual(report['unknown'], [{
                        'kind': 'shared_eligibility_requirement_unresolved',
                        'label': 'TP1', 'declaration': entry}])
            self.assertEqual((board, contract), before)

        for explicit_null in (False, True):
            board, contract = fixture()
            if explicit_null:
                contract['eligibility']['shared']['TP1']['constraint'] = None
            for check, report in (
                    ('planning', block_contract.check_complete(board, contract)),
                    ('saved', block_contract.judge(board, contract, original_capture=board))):
                with self.subTest(explicit_null=explicit_null, check=check):
                    self.assertEqual(report['status'], 'pass', report)
                    self.assertEqual(report['unknown'], [])

    def test_minimal_planning_check_retains_exact_final_distance(self):
        board, contract = fixture()
        result = block_contract.check_complete(moved(board, 'C1', 10*MM), contract)
        self.assertEqual(result['schema'], 'agent-ee.block-contract-check/1')
        self.assertEqual(result['status'], 'defect')
        self.assertEqual(result['defects'], [
            {'kind': 'pin_distance_exceeded', 'relationship': 'support', 'role': role,
             'distance_squared_nm2': 144*MM**2} for role in ('supply', 'return')])
        self.assertEqual(result['unknown'], [])

    def test_explicit_support_override_can_differ_from_anchor_face(self):
        board, contract = fixture()
        contract['eligibility']['blocks']['device'].update(
            eligible_faces=['F'], face_restrictions=['device anchor stays on front'])
        contract['eligibility']['members'] = {'C1': {'eligible_faces': ['B']}}
        contract['templates'] = [{'relationship': 'support', 'role': role, 'template': 'decoupler-under-device'}
                                 for role in ('supply', 'return')]
        report = block_contract.judge(moved(board, 'C1', side='B'), contract, original_capture=board)
        self.assertEqual(report['status'], 'pass', report)

    def test_frozen_schema_spelling_is_accepted_without_rewriting_input_hash(self):
        board, contract = fixture()
        contract['allocation']['schema'] = block_contract.ALLOCATION[1]
        contract['eligibility']['schema'] = block_contract.ELIGIBILITY[1]
        self.assertEqual(block_contract.judge(board, contract, original_capture=board)['status'], 'pass')

    def test_missing_duplicate_unknown_and_ambiguous_membership_refused(self):
        board, contract = fixture()
        for change in ('missing', 'duplicate', 'unknown', 'shared_role', 'anchor', 'binding'):
            allocation = deepcopy(contract['allocation'])
            if change == 'missing': allocation['fixed'] = []
            elif change == 'duplicate': allocation['blocks'][0]['references'].append('H1')
            elif change == 'unknown': allocation['shared'][0]['reference'] = 'ZZ99'
            elif change == 'shared_role': allocation['shared'][0]['role'] = ''
            elif change == 'anchor': allocation['blocks'][0]['anchor'] = {'fixed': 'TP1'}
            else: allocation['board_sha256'] = '0'*64
            with self.subTest(change=change), self.assertRaises(ValueError):
                block_contract.membership(board, allocation)

    def test_fixed_and_shared_faces_cannot_exceed_default_permission(self):
        for role, reference in (('fixed', 'H1'), ('shared', 'TP1')):
            board, contract = fixture()
            eligibility = contract['eligibility']
            eligibility['default']['faces'] = ['F']
            eligibility['blocks']['device']['eligible_faces'] = ['F']
            eligibility['fixed']['H1']['eligible_faces'] = ['F']
            eligibility['shared']['TP1']['eligible_faces'] = ['F']
            with self.subTest(role=role):
                self.assertEqual(block_contract.judge(board, contract, original_capture=board)['status'], 'pass')
                eligibility[role][reference]['eligible_faces'] = ['B']
                with self.assertRaisesRegex(ValueError, 'role eligibility exceeds default permission'):
                    block_contract.judge(board, contract, original_capture=board)
                with self.assertRaisesRegex(ValueError, 'role eligibility exceeds default permission'):
                    block_contract.check_complete(board, contract)

    def test_fixed_role_does_not_freeze_pose_or_side(self):
        board, contract = fixture()
        final = moved(board, 'H1', MM, 2*MM, 'B')
        self.assertEqual(block_contract.judge(final, contract, original_capture=board)['status'], 'pass')

    def test_actual_fixed_and_native_locked_pose_authority_is_independent(self):
        board, contract = fixture()
        final = moved(board, 'H1', MM, 0, 'B')
        # The logical role alone permits this same mutation, as tested above.
        for authority in ('request', 'native'):
            original = deepcopy(board)
            plan = {'policy': {'fixed_refs': ['H1']}} if authority == 'request' else None
            if authority == 'native': original['footprints'][2]['locked'] = True
            with self.subTest(authority=authority):
                report = block_contract.judge(final, contract, original_capture=original, plan=plan)
                self.assertEqual(report['status'], 'defect', report)
                self.assertEqual(report['pose_constraints'][0]['status'], 'defect')
                self.assertTrue(any(d['kind'] == 'fixed_pose_changed' for d in report['defects']))
        report = block_contract.judge(board, contract, original_capture=board,
                                       plan={'policy': {'fixed_refs': ['U1']}})
        self.assertEqual(report['pose_constraints'][0]['authority'], ['explicit_request_fixed_refs'])
        self.assertEqual(report['pose_constraints'][0]['status'], 'pass')

    def test_missing_or_changed_saved_member_is_typed_defect(self):
        board, contract = fixture()
        for change in ('removed', 'uuid', 'pad_net', 'pad_duplicate'):
            final = deepcopy(board)
            if change == 'removed': final['footprints'].pop(1)
            elif change == 'uuid': final['footprints'][1]['uuid'] = 'different'
            elif change == 'pad_net': final['footprints'][1]['pads'][0]['net'] = 'WRONG'
            else: final['footprints'][1]['pads'].append(deepcopy(final['footprints'][1]['pads'][0]))
            with self.subTest(change=change):
                report = block_contract.judge(final, contract, original_capture=board)
                self.assertEqual(report['status'], 'defect', report)

    def test_final_coordinates_override_passing_plan_and_detect_moved_member(self):
        board, contract = fixture()
        final = moved(board, 'C1', 10*MM)
        report = block_contract.judge(final, contract, original_capture=board,
                                      plan={'status': 'planned', 'placements': [{'ref': 'C1', 'position_nm': [4*MM, 2*MM]}]})
        self.assertEqual(report['status'], 'defect', report)
        distances = [r['distance_squared_nm2'] for r in report['relationships']]
        self.assertEqual(distances, [144*MM**2, 144*MM**2])
        self.assertEqual(sum(d['kind'] == 'pin_distance_exceeded' for d in report['defects']), 2)

    def test_forbidden_side_needs_independent_eligibility_not_fixed_role(self):
        board, contract = fixture()
        contract['eligibility']['members'] = {'C1': {'eligible_faces': ['F']}}
        report = block_contract.judge(moved(board, 'C1', side='B'), contract, original_capture=board)
        self.assertTrue(any(d['kind'] == 'forbidden_side' for d in report['defects']))

    def test_deliberate_cross_face_needs_template_geometry_and_exact_3d_bound(self):
        board, contract = fixture()
        final = moved(board, 'C1', side='B')
        no_template = block_contract.judge(final, contract, original_capture=board)
        self.assertEqual({r['classification'] for r in no_template['relationships']}, {'lost_cohesion'})
        contract['templates'] = [{'relationship': 'support', 'role': role, 'template': 'decoupler-under-device'}
                                 for role in ('supply', 'return')]
        report = block_contract.judge(final, contract, original_capture=board)
        self.assertEqual(report['status'], 'pass', report)
        self.assertEqual({r['classification'] for r in report['relationships']}, {'deliberate_cross_face'})
        self.assertEqual(report['relationships'][0]['distance_squared_nm2'], 6560000000000)
        # 2.8mm XY fits the old planar 3mm bound, but 1.6mm thickness makes it fail.
        report = block_contract.judge(moved(final, 'C1', 800000), contract, original_capture=board)
        self.assertEqual(report['status'], 'defect')
        self.assertTrue(all(r['exceeded'] for r in report['relationships']))

    def test_same_face_template_refuses_split(self):
        board, contract = fixture()
        contract['templates'] = [{'relationship': 'support', 'role': 'supply', 'template': 'same-face-only'}]
        report = block_contract.judge(moved(board, 'C1', side='B'), contract, original_capture=board)
        self.assertTrue(any(d['kind'] == 'same_face_required' for d in report['defects']))

    def test_engineering_number_is_not_promoted_to_hard_or_deliberate(self):
        board, contract = fixture()
        for pair in (contract['relationships']['obligations'][0][r] for r in ('supply', 'return')):
            pair['provenance'] = {'class': 'engineering', 'basis': 'layout estimate'}
        report = block_contract.judge(moved(board, 'C1', 10*MM), contract, original_capture=board)
        self.assertEqual(report['status'], 'unknown')
        self.assertFalse(report['defects'])
        self.assertTrue(all(a['exceeded'] for a in report['advisories']))
        contract['templates'] = [{'relationship': 'support', 'role': role, 'template': 'decoupler-under-device'}
                                 for role in ('supply', 'return')]
        report = block_contract.judge(moved(board, 'C1', side='B'), contract, original_capture=board)
        self.assertEqual({r['classification'] for r in report['relationships']}, {'unknown'})

    def test_missing_endpoint_thickness_and_required_bound_are_unknown(self):
        board, contract = fixture()
        for change in ('endpoint', 'thickness', 'bound', 'courtyard'):
            doc = deepcopy(contract)
            final = moved(board, 'C1', side='B')
            doc['templates'] = [{'relationship': 'support', 'role': role, 'template': 'decoupler-under-device'}
                                for role in ('supply', 'return')]
            if change == 'endpoint': doc['relationships']['obligations'][0]['supply']['to']['pad'] = '99'
            elif change == 'thickness': doc['thickness_nm'] = None
            elif change == 'bound': del doc['relationships']['obligations'][0]['supply']['max_distance_nm']
            else: final['footprints'][0]['courtyards'] = {}
            with self.subTest(change=change):
                self.assertEqual(block_contract.judge(final, doc, original_capture=board)['status'], 'unknown')

    def test_defect_survives_unrelated_missing_geometry(self):
        board, contract = fixture()
        final = moved(board, 'C1', 10*MM)
        final['footprints'][0]['courtyards'] = {}
        report = block_contract.judge(final, contract, original_capture=board)
        self.assertTrue(report['unknown'])
        self.assertEqual(report['status'], 'defect')

    def test_stale_source_and_missing_coverage_never_pass(self):
        board, contract = fixture()
        contract['relationships']['source_records']['fixture/limits.txt'] += ' changed'
        self.assertEqual(block_contract.judge(board, contract, original_capture=board)['status'], 'unknown')
        contract['relationships'] = {'schema': 'agent-ee.electrical-locality/1', 'obligations': []}
        report = block_contract.judge(board, contract, original_capture=board)
        self.assertTrue(any(u['kind'] == 'block_pin_relationships_missing' for u in report['unknown']))

    def test_member_override_cannot_exceed_default_permission(self):
        board, contract = fixture()
        contract['eligibility']['default']['faces'] = ['F']
        contract['eligibility']['blocks']['device']['eligible_faces'] = ['F']
        contract['eligibility']['members'] = {'C1': {'eligible_faces': ['F', 'B']}}
        with self.assertRaisesRegex(ValueError, 'exceeds default'):
            block_contract.judge(board, contract, original_capture=board)

    def test_mechanical_members_need_no_pin_obligations(self):
        board, contract = fixture()
        for fp in board['footprints']:
            fp['pads'] = []
        contract['relationships'] = {'schema': 'agent-ee.electrical-locality/1', 'obligations': []}
        self.assertEqual(block_contract.judge(board, contract, original_capture=board)['status'], 'pass')
        self.assertEqual(block_contract.check_complete(board, contract)['status'], 'pass')
        section = {'contract': contract, 'side_eligibility': contract['eligibility'], 'candidates': [],
                   'allocation_text': json.dumps({'schema': 'agent-ee.block-allocation/1', 'blocks': [
                       {'id': 'device', 'references': ['U1', 'C1'], 'anchor': 'U1'}]})}
        self.assertIsNotNone(block_contract.validate(board, section, {}))

    def test_missing_hard_pair_citation_remains_unknown(self):
        board, contract = fixture()
        del contract['relationships']['obligations'][0]['supply']['citation']
        report = block_contract.check_complete(board, contract)
        self.assertEqual(report['status'], 'unknown')
        self.assertTrue(any(row.get('reason') == 'declared numeric limit and citation required'
                            for row in report['unknown']))

    def test_exact_fractional_pad_centroid_does_not_round_into_bound(self):
        board, contract = fixture()
        final = moved(board, 'C1', MM)
        final['footprints'][1]['pads'][0]['copper_centroid_offset_nm'][0] = [1, 3]
        report = block_contract.judge(final, contract, original_capture=board)
        exact = report['relationships'][0]['distance_squared_nm2']
        self.assertEqual(exact, {'numerator': 81000018000001, 'denominator': 9})
        self.assertEqual(report['status'], 'defect')


if __name__ == '__main__':
    unittest.main()

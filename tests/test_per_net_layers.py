"""AGENTEE-258: native routed-board per-net layer contracts and fail-closed boundaries."""
import copy
import unittest

from agent_ee import kicad_route_attempt as attempt, routing_adapter
from tests import test_route_layer_policy as native


POLICY = {**native.OUTER, 'per_net_signal_layers': {'SIGNAL': ['F.Cu']}}
# The pod's declaration shape (/POD_GNSS_RF on F.Cu only over the In1.Cu GND
# plane) on the owned probe net: B.Cu stays globally allowed but priced high.
POD = {'signal_layers': ['F.Cu', 'B.Cu'], 'layer_costs': [1, 1000], 'pour_area_loss_pct': 0.5,
       'per_net_signal_layers': {'SIGNAL': ['F.Cu']},
       'reference': {'net': 'GND', 'layers': ['In1.Cu'], 'rf_nets': ['SIGNAL']}}


class PerNetLayerContractTest(unittest.TestCase):
    def setUp(self):
        self.sha = 'a' * 64
        self.track = dict(uuid='track', kind='segment', net='SIGNAL', layer='F.Cu',
                          width=250000, start=[0, 0], end=[1000000, 0])
        self.via = dict(uuid='via', net='SIGNAL', layers=['F.Cu', 'B.Cu'],
                        via_type='through', position=[1000000, 0], diameter=600000, drill=300000)
        self.facts = dict(sha256=self.sha, copper_layers=[{'name': n} for n in
                          ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']], tracks=[self.track], vias=[])

    def grade(self, facts=None, policy=POLICY):
        return attempt.per_net_layer_grade(policy, self.facts if facts is None else facts, self.sha)

    def test_declaration_validation_and_router_settings_preserved(self):
        self.assertEqual(attempt.validate_layer_policy(POLICY), POLICY)
        request = dict(project='project', board='pod.kicad_pcb', schematic='pod.kicad_sch',
                       name='pod', label='per-net', checkout='/opt/kicad-route', layer_policy=POLICY)
        self.assertEqual(routing_adapter.validate_declaration({'schema': 'agent-ee.routing-adapter/1',
                                                             **request})['layer_policy'], POLICY)
        self.assertEqual(attempt.policy_argv(POLICY), attempt.policy_argv(native.OUTER))
        for value in ({}, [], {'SIGNAL': []}, {'SIGNAL': ['In1.Cu']},
                      {'SIGNAL': ['F.Cu', 'F.Cu']}, {'SIGNAL': ['F.Cu', 'bad']}, {'\n': ['F.Cu']}):
            with self.subTest(value=value), self.assertRaises(attempt.RequestError):
                attempt.validate_layer_policy({**POLICY, 'per_net_signal_layers': value})

    def test_absent_declaration_is_unknown_and_global_policy_is_not_per_net_evidence(self):
        self.assertEqual(self.grade(policy=native.OUTER)['status'], 'unknown')

    def test_every_grade_is_bound_to_the_validated_board_hash_and_declaration(self):
        for facts in (self.facts, {**self.facts, 'sha256': 'b' * 64}, {**self.facts, 'tracks': None},
                      {**self.facts, 'tracks': [dict(self.track, layer='B.Cu')]}):
            with self.subTest(facts=facts):
                grade = self.grade(facts)
                self.assertEqual(grade['board_sha256'], self.sha)
                self.assertEqual(grade['declared'], POLICY['per_net_signal_layers'])
        self.assertEqual(self.grade(policy=native.OUTER)['board_sha256'], self.sha)
        self.assertEqual(self.grade()['copper_measured'], {'SIGNAL': 1})

    def test_all_tracks_and_arcs_including_preexisting_copper_are_checked(self):
        self.assertEqual(self.grade()['status'], 'pass')
        for kind in ('segment', 'arc'):
            bad = dict(self.track, kind=kind, mid=[500000, 100000], layer='B.Cu')
            grade = self.grade({**self.facts, 'tracks': [self.track, bad]})
            self.assertEqual(grade['status'], 'defect', grade)
            self.assertEqual(grade['violations'][0]['disallowed_layers'], ['B.Cu'])

    def test_vias_span_all_intermediate_copper_layers(self):
        facts = {**self.facts, 'vias': [self.via]}
        grade = self.grade(facts)
        self.assertEqual(grade['status'], 'defect')
        self.assertEqual(grade['violations'][0]['disallowed_layers'], ['In1.Cu', 'In2.Cu', 'B.Cu'])
        # Endpoints alone are insufficient even when both are allowed.
        policy = {**POLICY, 'per_net_signal_layers': {'SIGNAL': ['F.Cu', 'B.Cu']}}
        self.assertEqual(self.grade(facts, policy)['status'], 'defect')
        policy = {**POLICY, 'per_net_signal_layers': {'SIGNAL': ['F.Cu', 'In1.Cu']}}
        blind = dict(self.via, layers=['F.Cu', 'In1.Cu'], via_type='blind')
        self.assertEqual(self.grade({**facts, 'vias': [blind]}, policy)['status'], 'pass')

    def test_each_net_has_its_own_allowlist_and_undeclared_nets_are_outside_scope(self):
        other = dict(self.track, uuid='other', net='OTHER', layer='B.Cu')
        facts = {**self.facts, 'tracks': [self.track, other]}
        self.assertEqual(self.grade(facts)['status'], 'pass')
        policy = {**POLICY, 'per_net_signal_layers': {'SIGNAL': ['F.Cu'], 'OTHER': ['B.Cu']}}
        self.assertEqual(self.grade(facts, policy)['status'], 'pass')
        policy['per_net_signal_layers']['OTHER'] = ['F.Cu']
        grade = self.grade(facts, policy)
        self.assertEqual((grade['status'], [v['net'] for v in grade['violations']]), ('defect', ['OTHER']))
        policy['per_net_signal_layers']['MISSING'] = ['F.Cu']
        self.assertEqual(self.grade(facts, policy)['status'], 'defect')

    def test_unparseable_missing_and_unbound_geometry_is_unknown(self):
        for change in (dict(sha256='b' * 64), dict(copper_layers=None), dict(vias=None),
                       dict(copper_layers=[{'name': 'F.Cu'}, {'name': 'F.Cu'}]),
                       dict(tracks=[]), dict(tracks=[None]), dict(tracks=[{'uuid': 'bad'}]),
                       dict(tracks=[dict(self.track, width='bad')]),
                       dict(tracks=[dict(self.track, layer=['F.Cu'])]),
                       dict(tracks=[dict(self.track, kind='unknown')]),
                       dict(tracks=[dict(self.track, kind='arc')]),
                       dict(vias=[dict(self.via, layers=['F.Cu', 'missing'])]),
                       dict(vias=[dict(self.via, via_type='unknown')]),
                       dict(vias=[dict(self.via, layers=['F.Cu', 'In1.Cu'])]),
                       dict(vias=[dict(self.via, drill=0)])):
            with self.subTest(change=change):
                self.assertEqual(self.grade({**self.facts, **change})['status'], 'unknown')
        self.assertEqual(attempt.per_net_layer_grade(POLICY, None, self.sha)['status'], 'unknown')
        missing = {**POLICY, 'per_net_signal_layers': {'MISSING': ['F.Cu']}}
        self.assertEqual(self.grade(policy=missing)['status'], 'unknown')
        missing_layer = {**POLICY, 'per_net_signal_layers': {'SIGNAL': ['F.Cu', 'In9.Cu']}}
        self.assertEqual(self.grade(policy=missing_layer)['status'], 'unknown')

    def test_known_defect_survives_another_unparseable_item(self):
        self.assertEqual(self.grade({**self.facts, 'vias': [self.via, None]})['status'], 'defect')


class PerNetLayerNativeTest(native.PlaneFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.checkout = native.require_kicad_route()
        common = dict(clearance_mm=.25, min_width_mm=.2, thermal=dict(connection='thermal', gap_mm=.3,
                      spoke_width_mm=.3), priority=0, island_removal='always', net='GND', outline='board')
        self.plan = dict(schema=native.planes.SCHEMA,
                         zones=[dict(common, layer='In1.Cu'), dict(common, layer='In2.Cu')])
        self.save()

    route = native.RouteLayerPolicyNativeTest.route

    def facts(self, work):
        return native.read_json(work / 'routing/attempt/validate/candidate-facts.json')

    def test_compliant_route_and_reference_are_bound_and_pass(self):
        receipt, summary, report, work = self.route(35, POD, 'per-net-clean')
        self.assertEqual(receipt['status'], 'completed', report['semantics'])
        self.assertEqual(summary['proposal'], 'eligible', report['semantics'])
        self.assertEqual(summary['layer_policy']['status'], 'pass', summary['layer_policy'])
        item = summary['layer_policy']['items']['per_net_signal_layers']
        self.assertEqual(item['status'], 'pass', item)
        self.assertEqual(summary['layer_policy']['items']['reference_continuity']['status'], 'pass')
        # Bound to the delivered candidate by hash: the board file, the native facts, the item.
        board = work / 'routing/candidate/project/pod.kicad_pcb'
        self.assertEqual(item['board_sha256'], attempt.digest(board))
        self.assertEqual(item['board_sha256'], summary['candidate_sha256'])
        self.assertEqual(self.facts(work)['sha256'], item['board_sha256'])
        signal = [t for t in self.facts(work)['tracks'] if t['net'] == 'SIGNAL']
        self.assertTrue(signal)
        self.assertEqual({t['layer'] for t in signal}, {'F.Cu'})
        self.assertEqual(item['copper_measured']['SIGNAL'],
                         len(signal) + len([v for v in self.facts(work)['vias'] if v['net'] == 'SIGNAL']))
        # Replaying a changed retained per-net grade must fail independently.
        value = copy.deepcopy(report)
        value['semantics']['layer_policy']['items']['per_net_signal_layers']['status'] = 'defect'
        attempt.write_report(work / 'routing/adapter-report.json', value)
        with self.assertRaisesRegex(ValueError, 'layer policy grade differs'):
            routing_adapter.verify(work)

    def assert_rejected_and_bound(self, summary, report, work):
        self.assertEqual(summary['classification'], 'policy_failed', report['semantics'])
        self.assertFalse((work / 'routing/candidate').exists())
        item = summary['layer_policy']['items']['per_net_signal_layers']
        self.assertEqual(item['board_sha256'], report['rejected_candidate_sha256'])
        self.assertEqual(self.facts(work)['sha256'], item['board_sha256'])
        return summary['layer_policy']['items']

    def test_back_segment_violates_front_only_net_despite_global_allowance(self):
        policy = {**POLICY, 'layer_costs': [1000, 1]}
        receipt, summary, report, work = self.route(35, policy, 'per-net-back')
        items = self.assert_rejected_and_bound(summary, report, work)
        item = items['per_net_signal_layers']
        self.assertEqual(item['status'], 'defect', item)
        self.assertTrue(any(v['kind'] == 'track' and v['disallowed_layers'] == ['B.Cu']
                            for v in item['violations']), item)
        # The per-net declaration alone rejects it: the global grade items all pass.
        self.assertEqual({k: v['status'] for k, v in items.items() if k != 'per_net_signal_layers'},
                         {'disallowed_tracks': 'pass', 'pour_integrity': 'pass', 'area_loss': 'pass',
                          'reference_continuity': 'pass'}, items)

    def test_via_to_another_layer_is_rejected(self):
        receipt, summary, report, work = self.route(35, POLICY, 'per-net-via', 'layer_change')
        items = self.assert_rejected_and_bound(summary, report, work)
        item = items['per_net_signal_layers']
        self.assertEqual(item['status'], 'defect')
        vias = {v['uuid'] for v in self.facts(work)['vias'] if v['net'] == 'SIGNAL'}
        self.assertTrue(vias, 'the router did not change side through a via')
        self.assertEqual({v['uuid'] for v in item['violations'] if v['kind'] == 'via'}, vias)
        for v in item['violations']:
            if v['kind'] == 'via':
                self.assertEqual(v['disallowed_layers'], ['In1.Cu', 'In2.Cu', 'B.Cu'])
        self.assertEqual(items['disallowed_tracks']['status'], 'pass')

    def test_missing_reference_under_part_of_route_is_rejected(self):
        # y = 25 crosses the owned In1/In2 fill keepout at x 28..32.
        policy = {k: v for k, v in POD.items() if k != 'layer_costs'}
        policy['signal_layers'] = ['F.Cu']
        receipt, summary, report, work = self.route(25, policy, 'per-net-void')
        items = self.assert_rejected_and_bound(summary, report, work)
        self.assertEqual(items['per_net_signal_layers']['status'], 'pass')
        self.assertEqual(items['reference_continuity']['status'], 'defect')
        self.assertEqual({t['reference_layer'] for t in items['reference_continuity']['interrupted']},
                         {'In1.Cu'})

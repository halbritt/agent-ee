"""Routing feedback must not weaken copper custody or native acceptance."""
from unittest.mock import patch

from agent_ee import placement_job as job
from agent_ee.storage import read_json, write_json
from tests.kicad_route_fixture import make_checkout, make_layout
from tests.test_placement_job import Fixture


class RoutingFeedbackTests(Fixture):
    def router(self):
        """The fixture checkout and its qualified layout: the board route's real preflight passes."""
        checkout, code = make_checkout(self.dir / 'router', 'complete')
        return checkout, make_layout(self.dir / 'pin', code)

    def test_scoped_cleanup_cannot_drop_required_length_group_members(self):
        record = {'status': 'routed', 'router_accepted': False, 'board': str(self.board)}
        feedback = {'kind': 'dangling_net', 'nets': ['A']}
        controls = [dict(length_match=dict(groups=[['A', 'B']], tolerance_mm=.1, amplitude_mm=1, spacing_widths=2)),
                    dict(ac_match=dict(chains=[['C1', 'C2']], tolerance_mm=.1, amplitude_mm=1))]
        checkout, layout = self.router()
        for index, matching in enumerate(controls):
            with self.subTest(matching=matching), \
                    patch.object(job, '_produce_planes', return_value={}), \
                    patch.object(job, '_route_attempt', return_value=record) as run, \
                    patch.object(job, '_route_feedback', return_value=feedback):
                result = job.route(self.dir, self.dir / f'route-{index}', checkout, budget_s=10, timeout_s=10,
                    home=self.dir / 'home', planes=b'{}', layout=layout, **matching)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(result['attempts'][0]['feedback']['recovery'], 'not_applied')
            self.assertFalse(result['router_accepted'])

    def test_existing_copper_never_enables_generated_copper_repair(self):
        import pcbnew
        board = pcbnew.LoadBoard(str(self.board))
        track = pcbnew.PCB_TRACK(board)
        track.SetStart(pcbnew.VECTOR2I(1000000, 1000000))
        track.SetEnd(pcbnew.VECTOR2I(2000000, 1000000))
        track.SetWidth(250000)
        track.SetLayer(pcbnew.F_Cu)
        board.Add(track)
        pcbnew.SaveBoard(str(self.board), board)
        # Plane production does not own or remove authored tracks. A refused
        # route must stay refused, even when a feedback strategy is available.
        checkout, layout = self.router()
        with patch.object(job, '_produce_planes', return_value={}), \
                patch.object(job, '_route_attempt', return_value={'status': 'no_candidate'}) as run, \
                patch.object(job, '_route_feedback', side_effect=AssertionError('authored copper repair')):
            result = job.route(self.dir, self.dir / 'route', checkout, budget_s=10, timeout_s=10,
                               home=self.dir / 'home', planes=b'{}', layout=layout)
        argv = run.call_args.kwargs['argv']
        self.assertEqual(argv[argv.index('--protect') + 1], 'existing')
        self.assertEqual(result['status'], 'no_candidate')
        self.assertEqual(result['copper_policy'], 'preserve_existing')

    def feedback_case(self):
        import pcbnew
        board = pcbnew.LoadBoard(str(self.board))
        net = board.FindNet('A')
        track = pcbnew.PCB_TRACK(board)
        track.SetStart(pcbnew.VECTOR2I(1000000, 1000000))
        track.SetEnd(pcbnew.VECTOR2I(1100000, 1000000))
        track.SetWidth(250000)
        track.SetLayer(pcbnew.F_Cu)
        track.SetNet(net)
        board.Add(track)
        pcbnew.SaveBoard(str(self.board), board)
        result = self.dir / 'result.json'
        write_json(result, {'process': {'status': 'PASS'}, 'routing': {'unconnected_total': 0},
                           'settings': {'pairs': []}, 'validation': {
                               'drc': {'new_parity': {'count': 0}},
                               'preservation': {'footprints': 'PASS', 'pour_integrity': 'PASS'}}})
        (self.dir / 'validate').mkdir()
        findings = self.dir / 'validate/new-findings.json'
        write_json(findings, {'violations': [{'type': 'track_dangling',
                                            'items': [{'uuid': track.m_Uuid.AsString()}]}]})
        return {'status': 'routed', 'router_accepted': False, 'result_path': str(result),
                'board': str(self.board)}, result, findings

    def test_only_identified_single_ended_nets_are_eligible_for_scoped_cleanup(self):
        record, result, findings = self.feedback_case()
        def feedback():
            return job._route_feedback(record, self.dir, env={}, clock=job.Clock(10), cancel_dir=None)
        selected = feedback()
        self.assertEqual(selected['nets'], ['A'])
        data = read_json(result)
        for pairs in (None, [{'p': 'A', 'n': 'B'}]):
            data['settings']['pairs'] = pairs
            write_json(result, data)
            self.assertIsNone(feedback(), 'missing pair detection or an actual pair cannot be treated as single-ended')
        data['settings']['pairs'] = []
        data['routing']['unconnected_total'] = 1
        write_json(result, data)
        self.assertIsNone(feedback(), 'cleanup cannot hide a route that is still incomplete')
        data['routing']['unconnected_total'] = 0
        write_json(result, data)
        write_json(findings, {'violations': [{'type': 'track_dangling', 'items': [{'uuid': 'missing'}]}]})
        self.assertIsNone(feedback(), 'a finding must identify actual saved copper')

    def test_native_router_rejection_cannot_become_job_completion(self):
        final = {'integrity': 'preserved', 'edge_intent': {'status': 'pass'}, 'eligibility': 'eligible'}
        self.assertEqual(job._completion_status({'status': 'routed', 'router_accepted': False}, True, final),
                         'verification_failed')

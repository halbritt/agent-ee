"""Routing feedback must not weaken copper custody or native acceptance."""
from unittest.mock import patch

from agent_ee import placement_job as job
from agent_ee.storage import read_json, write_json
from tests.kicad_route_fixture import make_checkout
from tests.test_placement_job import Fixture


class RoutingFeedbackTests(Fixture):
    def router(self):
        """The fixture checkout, a clean commit: the board route's real preflight passes."""
        return make_checkout(self.dir / 'router', 'complete')[0]

    def test_scoped_cleanup_cannot_drop_required_length_group_members(self):
        record = {'status': 'routed', 'router_accepted': False, 'board': str(self.board)}
        feedback = {'kind': 'dangling_net', 'nets': ['A']}
        controls = [dict(length_match=dict(groups=[['A', 'B']], tolerance_mm=.1, amplitude_mm=1, spacing_widths=2)),
                    dict(ac_match=dict(chains=[['C1', 'C2']], tolerance_mm=.1, amplitude_mm=1))]
        checkout = self.router()
        for index, matching in enumerate(controls):
            with self.subTest(matching=matching), \
                    patch.object(job, '_produce_planes', return_value={}), \
                    patch.object(job, '_route_attempt', return_value=record) as run, \
                    patch.object(job, '_route_feedback', return_value=feedback):
                result = job.route(self.dir, self.dir / f'route-{index}', checkout, budget_s=10, timeout_s=10,
                    home=self.dir / 'home', planes=b'{}', **matching)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(result['attempts'][0]['feedback']['recovery'], 'not_applied')
            self.assertFalse(result['router_accepted'])

    def routed(self, name, vias, accepted=True):
        """A router result on disk and the attempt record that binds it."""
        path = self.dir / f'result-{name}.json'
        write_json(path, {'routing': {'stats': {'new_vias': vias}}})
        return {'status': 'routed', 'router_accepted': accepted, 'board': str(self.board), 'result_path': str(path)}

    def route_twice(self, first, second, *, recipes=True, name='r'):
        from tests.kicad_route_fixture import commit_all
        checkout = make_checkout(self.dir / f'router-{name}', 'complete')[0]
        if recipes:
            (checkout / 'src/kicad_route/cli.py').write_text('p.add_argument("--recipe", choices=["auto"])\n')
            commit_all(checkout, 'the router declares --recipe')
        with patch.object(job, '_produce_planes', return_value={}), \
                patch.object(job, '_route_feedback', return_value=None), \
                patch.object(job, '_route_attempt', side_effect=[dict(first), dict(second)]) as run:
            result = job.route(self.dir, self.dir / f'route-{name}', checkout, budget_s=100, timeout_s=100,
                               home=self.dir / f'home-{name}', planes=b'{}')
        return result, run

    def test_an_accepted_two_layer_board_is_routed_again_and_the_fewer_vias_kept(self):
        """The job routes as before; a board the router accepted is routed once more at the router's
        via-saving costs, from the same input, and that board is the result when the router accepts
        it too and it has fewer vias."""
        result, run = self.route_twice(self.routed('first', 42), self.routed('second', 2))
        self.assertEqual(run.call_count, 2)
        first, second = (call.kwargs['argv'] for call in run.call_args_list)
        self.assertNotIn('--recipe', first)
        self.assertEqual(second[-2:], ['--recipe', 'auto'])
        self.assertEqual(second[second.index('--dir') + 1], first[first.index('--dir') + 1])
        self.assertTrue(result['result_path'].endswith('result-second.json'))
        self.assertEqual(result['attempts'][1]['fewer_vias'],
                         {'recipe': 'auto', 'kept': True, 'vias_before': 42, 'vias': 2})

    def test_the_first_board_stays_unless_the_second_is_accepted_and_has_fewer_vias(self):
        """On a two-layer board with a ground pour the via-saving costs left 2 vias for 42 and starved
        three thermal reliefs: the router rejects that board and the job keeps the one it had."""
        for name, second, reason in (
                ('rejected', self.routed('s1', 2, accepted=False), 'the router did not accept this board'),
                ('no-fewer', self.routed('s2', 42), 'no fewer vias'),
                ('no-board', {'status': 'no_candidate'}, 'the router did not accept this board')):
            with self.subTest(name):
                result, run = self.route_twice(self.routed('f-' + name, 42), second, name=name)
                self.assertEqual(run.call_count, 2)
                self.assertTrue(result['result_path'].endswith(f'result-f-{name}.json'))
                self.assertEqual(result['router_accepted'], True)
                self.assertEqual(result['attempts'][1]['fewer_vias']['kept'], False)
                self.assertEqual(result['attempts'][1]['fewer_vias']['reason'], reason)

    def test_no_second_routing_without_something_to_gain(self):
        """Not for a router that has no recipes, a board the router did not accept, or one without vias."""
        for name, first, recipes in (('old-router', self.routed('o', 42), False),
                                     ('rejected', self.routed('x', 42, accepted=False), True),
                                     ('no-vias', self.routed('z', 0), True)):
            with self.subTest(name):
                result, run = self.route_twice(first, {'status': 'no_candidate'}, recipes=recipes, name='n-' + name)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(len(result['attempts']), 1)

    def authored_track(self):
        """One authored track on the fixture board: the job then owns no copper."""
        import pcbnew
        board = pcbnew.LoadBoard(str(self.board))
        track = pcbnew.PCB_TRACK(board)
        track.SetStart(pcbnew.VECTOR2I(1000000, 1000000))
        track.SetEnd(pcbnew.VECTOR2I(2000000, 1000000))
        track.SetWidth(250000)
        track.SetLayer(pcbnew.F_Cu)
        board.Add(track)
        pcbnew.SaveBoard(str(self.board), board)

    def test_existing_copper_never_enables_generated_copper_repair(self):
        self.authored_track()
        # Plane production does not own or remove authored tracks. A refused route stays
        # refused, and the repair selector is told that the copper is not the job's.
        checkout = self.router()
        with patch.object(job, '_produce_planes', return_value={}), \
                patch.object(job, '_route_attempt', return_value={'status': 'no_candidate'}) as run, \
                patch.object(job, '_route_feedback', wraps=job._route_feedback) as select:
            result = job.route(self.dir, self.dir / 'route', checkout, budget_s=10, timeout_s=10,
                               home=self.dir / 'home', planes=b'{}')
        argv = run.call_args.kwargs['argv']
        self.assertEqual(run.call_count, 1)
        self.assertEqual(argv[argv.index('--protect') + 1], 'existing')
        self.assertEqual(result['status'], 'no_candidate')
        self.assertEqual(result['copper_policy'], 'preserve_existing')
        self.assertIs(select.call_args.kwargs['owned_copper'], False)

    def test_existing_copper_is_never_rerouted_on_the_routed_board(self):
        # The dangling-track repair reroutes a net on the routed board. Only a job that owns
        # that board's copper is offered it, and the route loop would not apply it otherwise.
        record, _, _ = self.feedback_case()
        def select(**owned):
            return job._route_feedback(record, self.dir, env={}, clock=job.Clock(10), cancel_dir=None, **owned)
        self.assertEqual(select()['kind'], 'dangling_net')
        self.assertIsNone(select(owned_copper=False))
        checkout = self.router()
        rejected = {'status': 'routed', 'router_accepted': False, 'board': str(self.board)}
        with patch.object(job, '_route_attempt', return_value=rejected) as run, \
                patch.object(job, '_route_feedback', return_value={'kind': 'dangling_net', 'nets': ['A']}):
            result = job.route(self.dir, self.dir / 'route', checkout, budget_s=10, timeout_s=10,
                               home=self.dir / 'home')
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result['attempts'][0]['feedback']['recovery'], 'not_applied')
        self.assertEqual(result['copper_policy'], 'preserve_existing')
        self.assertFalse(result['router_accepted'])

    def test_a_starved_thermal_is_retried_on_a_board_with_copper_of_its_own(self):
        # AGENTEE-654: the thermal retry routes the same unrouted input again with the starved
        # pad's corridors reserved. It needs no copper of the job's own, so a board that brings
        # its pours (no plane plan) or its tracks gets it too, under its own --protect policy.
        feedback = {'kind': 'thermal_corridors', 'evidence': 'spokes.json', 'sha256': 'x', 'pads': ['pad-uuid@B.Cu']}
        checkout = self.router()
        for index, (planes, authored) in enumerate(((None, False), (b'{}', True))):
            if authored:
                self.authored_track()
            rejected = {'status': 'routed', 'router_accepted': False, 'board': str(self.board)}
            accepted = {'status': 'routed', 'router_accepted': True, 'board': str(self.board)}
            with self.subTest(planes=planes, authored=authored), \
                    patch.object(job, '_produce_planes', return_value={}), \
                    patch.object(job, '_route_attempt', side_effect=[dict(rejected), dict(accepted)]) as run, \
                    patch.object(job, '_route_feedback', side_effect=[feedback, None]) as select:
                result = job.route(self.dir, self.dir / f'route-{index}', checkout, budget_s=10, timeout_s=10,
                                   home=self.dir / 'home', planes=planes)
                first, retry = (call.kwargs['argv'] for call in run.call_args_list)
                self.assertEqual(retry[retry.index('--reserve-spoke-pad') + 1], 'pad-uuid@B.Cu')
                for option in ('--protect', '--dir', '--board'):
                    self.assertEqual(retry[retry.index(option) + 1], first[first.index(option) + 1], option)
                self.assertEqual(first[first.index('--protect') + 1], 'existing')
                self.assertNotIn('--reserve-spoke-pad', first)
                self.assertNotEqual(retry[retry.index('--out') + 1], first[first.index('--out') + 1])
                self.assertIs(select.call_args.kwargs['owned_copper'], False)
                self.assertTrue(result['router_accepted'])
                self.assertEqual(result['copper_policy'], 'preserve_existing')
                self.assertEqual([a.get('router_accepted') for a in result['attempts']], [False, True])
                self.assertEqual(result['attempts'][0]['feedback'], feedback)

    def test_a_starved_thermal_selects_its_pads_whoever_owns_the_copper(self):
        result = self.dir / 'result.json'
        write_json(result, {'process': {'status': 'PASS'}, 'routing': {'unconnected_total': 0},
                           'validation': {'drc': {'new_parity': {'count': 0}},
                                          'preservation': {'footprints': 'PASS', 'pour_integrity': 'FAIL'}}})
        (self.dir / 'validate').mkdir()
        write_json(self.dir / 'validate/new-findings.json', {'violations': [{'type': 'starved_thermal', 'items': []}]})
        record = {'status': 'routed', 'router_accepted': False, 'result_path': str(result), 'board': str(self.board)}
        pad = {'uuid': 'pad-uuid', 'layer': 'B.Cu', 'new': True, 'attributed': True}

        def spokes(report, code=3):
            def run(argv, **kwargs):
                write_json(self.dir / 'spokes.json', report)
                return type('Done', (), {'returncode': code})()
            return run

        def select(report, code=3, **owned):
            with patch.object(job, '_run_child', side_effect=spokes(report, code)):
                return job._route_feedback(record, self.dir, env={}, clock=job.Clock(10), cancel_dir=None, **owned)
        for owned in ({}, {'owned_copper': False}):
            with self.subTest(owned=owned):
                chosen = select({'starved_pads': [pad]}, **owned)
                self.assertEqual((chosen['kind'], chosen['pads']), ('thermal_corridors', ['pad-uuid@B.Cu']))
                # A pad that no new copper is shown to have starved is not a basis for a retry,
                self.assertIsNone(select({'starved_pads': [dict(pad, attributed=False)]}, **owned))
                # nor is a report that found no new starved pad (exit 0).
                self.assertIsNone(select({'starved_pads': []}, code=0, **owned))

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
        # A route without --diff-pairs has no pair handling, so no pair plan is expected (defect 31).
        record['argv'] = ['python3', '-B', 'kicad-route', 'run', '--budget', '10']
        del data['settings']['pairs']
        write_json(result, data)
        self.assertEqual(feedback()['nets'], ['A'])
        for settings in ({'pairs': []}, {'diff_pairs': 'auto'}):
            data['settings'] = settings
            write_json(result, data)
            self.assertIsNone(feedback(), 'pair evidence from an unpaired request is a contradiction')
        record['argv'] += ['--diff-pairs', 'auto']
        data['settings'] = {}
        write_json(result, data)
        self.assertIsNone(feedback(), 'a paired request needs the router to report an empty pair plan')
        del record['argv']
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

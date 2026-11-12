"""Declared pair request and honest coverage, independent of routing completeness."""
import json
from pathlib import Path
import tempfile
import unittest
from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from tests.toolchain import require_kicad_route


class PairCoverageTests(unittest.TestCase):
    def test_diff_pair_request_forwarded(self):
        spec = routing_adapter.step(project='project', board='can.kicad_pcb',
            schematic='can.kicad_sch', name='can', label='pair',
            checkout=require_kicad_route(), diff_pairs='auto')
        request = attempt.parse_request(spec['argv'][4:])
        argv = attempt.tool_argv(request, attempt.default_layout(request))
        self.assertEqual(argv[argv.index('--diff-pairs') + 1], 'auto')

    def test_single_ended_can_is_never_pair_coverage(self):
        record = {'scope': {'nets': ['CAN+', 'CAN-']}}
        report = attempt.pair_coverage({}, {}, record, 'complete')
        self.assertFalse(report['acceptance_ready'])
        self.assertEqual(report['status'], 'unknown')
        self.assertIn('CAN pair routed single-ended', report['limitations'])
        rejected = attempt.pair_coverage({}, {'routing': {'status': 'COMPLETE'},
            'process': {'status': 'PASS'}}, record, 'validation_failed')
        self.assertIn('CAN pair routed single-ended', rejected['limitations'])
        self.assertIn('Single-ended attempt did not produce an eligible board', rejected['limitations'])
        self.assertEqual(rejected['status'], 'unknown')
        failed = attempt.pair_coverage({}, None, record, 'tool_failed')
        self.assertNotIn('CAN pair routed single-ended', failed['limitations'])

    def test_missing_rejected_or_preserved_pair_is_not_coupled(self):
        plan = {'p': 'CAN+', 'n': 'CAN-', 'netclass': 'CAN', 'width_mm': .25, 'gap_mm': .35}
        record = {'pairs': [plan], 'pair_routing': {'CAN+': {'status': 'COUPLED'}}}
        pair = {**plan, 'status': 'ROUTED', 'routed': True, 'backend_status': 'COUPLED',
                'length_p': 10., 'length_n': 10., 'skew': 0., 'gap_violations': 0}
        result = {'settings': {'pairs': [plan]}, 'pairs': [pair]}
        valid = attempt.pair_coverage({'diff_pairs': 'auto'}, result, record, 'complete')
        self.assertEqual(valid['status'], 'reported_coupled')
        self.assertFalse(valid['acceptance_ready'])
        mutations = [{'backend_status': 'ALREADY_CONNECTED'}, {'backend_status': 'REJECTED'},
                     {'routed': False}, {'gap_violations': 1}, {'gap_violations': False},
                     {'length_p': None}, {'skew': float('nan')}, {'skew': 5.}]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                bad = {**result, 'pairs': [{**pair, **mutation}]}
                coverage = attempt.pair_coverage({'diff_pairs': 'auto'}, bad, record, 'complete')
                self.assertEqual(coverage['status'], 'unknown')
                self.assertFalse(coverage['acceptance_ready'])
        missing = attempt.pair_coverage({'diff_pairs': 'auto'}, None, None, 'not_run')
        self.assertEqual(missing['status'], 'unknown')
        self.assertIn('unavailable', missing['limitations'][-1])

    def test_coverage_claim_is_rederived_and_legacy_record_stays_readable(self):
        import tempfile
        from tests.test_kicad_route_adapter import Case
        with tempfile.TemporaryDirectory() as tmp:
            case = Case(tmp, 'complete').run()
            path = case.work / 'routing/adapter-report.json'
            case.report['semantics']['pair_coverage']['status'] = 'reported_coupled'
            attempt.write_report(path, case.report)
            with self.assertRaisesRegex(ValueError, 'pair coverage differs'):
                case.verify()
            # Old retained reports lack the additive diagnostic. Replay derives it.
            del case.report['semantics']['pair_coverage']
            attempt.write_report(path, case.report)
            summary = case.verify()
            self.assertEqual(summary['pair_coverage']['status'], 'unknown')

    def test_native_pad_contact_uses_copper_shape_and_unique_endpoints(self):
        import tempfile
        from pathlib import Path
        import pcbnew as k
        from tests.routing_fixture import make_board
        from tests.toolchain import require_toolchain
        from agent_ee.routing_native import route_coverage
        require_toolchain(native=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'probe.kicad_pcb'
            make_board(path, lengths=(10, 10), net_names=('CAN+', 'CAN-'))
            board = k.LoadBoard(str(path))
            track = next(t for t in board.GetTracks() if t.GetNetname() == 'CAN+')
            start = k.VECTOR2I(track.GetStart().x, track.GetStart().y)
            # Actual SMD pad interior, rather than its centre, is a native connection.
            track.SetStart(k.VECTOR2I(start.x + 100000, start.y))
            self.assertTrue(route_coverage(board, 'CAN+')['complete'])
            pad = next(p for f in board.GetFootprints() for p in f.Pads()
                       if p.GetNetname() == 'CAN+' and p.GetPosition() == start)
            pad.SetShape(k.PAD_SHAPE_OVAL)
            pad.SetSize(k.VECTOR2I(800000, 400000))
            pad.SetOrientationDegrees(90)
            # Inside the oval's bounding rectangle, outside its curved copper.
            track.SetStart(k.VECTOR2I(start.x + 190000, start.y + 390000))
            self.assertFalse(route_coverage(board, 'CAN+')['complete'])
            track.SetStart(k.VECTOR2I(start.x + 100000, start.y))
            self.assertTrue(route_coverage(board, 'CAN+')['complete'])
            track.SetStart(k.VECTOR2I(start.x + 210000, start.y))
            self.assertFalse(route_coverage(board, 'CAN+')['complete'], 'endcap contact alone is unsupported')
            track.SetStart(k.VECTOR2I(start.x + 100000, start.y))
            extra = k.PCB_TRACK(board); extra.SetNet(board.FindNet('CAN+'))
            extra.SetLayer(k.F_Cu); extra.SetWidth(50000)
            extra.SetStart(k.VECTOR2I(start.x, start.y)); extra.SetEnd(k.VECTOR2I(start.x, start.y + 100000))
            board.Add(extra)
            self.assertFalse(route_coverage(board, 'CAN+')['complete'], 'multiple copper endpoints in a pad')


class BoardRecipeTests(unittest.TestCase):
    """What a job needs to try its router's via-saving costs on a board."""

    def test_only_a_router_that_declares_the_option_has_a_recipe(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            self.assertIsNone(routing_adapter.router_recipe(root / 'absent'))
            (root / 'src/kicad_route').mkdir(parents=True)
            (root / 'src/kicad_route/cli.py').write_text('p.add_argument("--budget", type=int)\n')
            self.assertIsNone(routing_adapter.router_recipe(root))
            (root / 'src/kicad_route/cli.py').write_text(
                'p.add_argument("--budget", type=int)\np.add_argument("--recipe", choices=["auto"])\n')
            self.assertEqual(routing_adapter.router_recipe(root), 'auto')

    def test_the_second_command_is_the_first_with_the_recipe_its_own_output_and_budget(self):
        argv = routing_adapter.board_route_argv('/router', '/staged', '/out', budget_s=100, owned_copper=True)
        again = routing_adapter.board_recipe_argv(argv, '/again/out', 41.9, 'auto')
        self.assertEqual(again[-2:], ['--recipe', 'auto'])
        self.assertEqual((again[again.index('--out') + 1], again[again.index('--budget') + 1]), ('/again/out', '41'))
        self.assertEqual(again[again.index('--dir') + 1], '/staged')              # the same unrouted input
        self.assertEqual(again[again.index('--protect') + 1], 'locked')           # and the same copper policy
        self.assertNotIn('--recipe', argv)
        self.assertEqual(argv[argv.index('--budget') + 1], '100')                 # the first command is not changed

    def test_layer_count_and_new_vias_are_read_from_the_files(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            board = root / 'board.kicad_pcb'
            board.write_text('(kicad_pcb\n\t(layers\n\t\t(0 "F.Cu" signal)\n\t\t(2 "B.Cu" signal)\n'
                             '\t\t(9 "F.Adhes" user "F.Adhesive")\n\t)\n)\n')
            self.assertEqual(routing_adapter.copper_layer_count(board), 2)
            board.write_text(board.read_text().replace('(2 "B.Cu" signal)',
                                                       '(4 "In1.Cu" signal)\n\t\t(6 "In2.Cu" power "GND")\n\t\t(2 "B.Cu" signal)'))
            self.assertEqual(routing_adapter.copper_layer_count(board), 4)
            self.assertIsNone(routing_adapter.copper_layer_count(root / 'absent.kicad_pcb'))
            result = root / 'result.json'
            result.write_text(json.dumps({'routing': {'stats': {'new_vias': 7}}}))
            self.assertEqual(routing_adapter.new_via_count({'result_path': str(result)}), 7)
            result.write_text('{}')
            self.assertIsNone(routing_adapter.new_via_count({'result_path': str(result)}))
            self.assertIsNone(routing_adapter.new_via_count({}))


class BoardPairIntentTests(unittest.TestCase):
    """Board routing handles pairs only when they are declared (tool defect 31)."""

    SCRIPT = '/router/bin/kicad-route'  # the router records its arguments after this script

    def argv(self, **options):
        return routing_adapter.board_route_argv('/router', '/staged', '/out', budget_s=10, **options)

    def test_pair_handling_only_when_declared(self):
        self.assertNotIn('--diff-pairs', self.argv())
        self.assertNotIn('--diff-pairs', self.argv(diff_pairs=None))
        argv = self.argv(diff_pairs='auto')
        self.assertEqual(argv[argv.index('--diff-pairs') + 1], 'auto')
        with self.assertRaisesRegex(ValueError, 'diff_pairs'):
            self.argv(diff_pairs='manual')

    def test_router_options_declare_pairs_explicitly(self):
        self.assertIsNone(routing_adapter.validate_router_options({'checkout': '/r'})['diff_pairs'])
        self.assertEqual(routing_adapter.validate_router_options(
            {'checkout': '/r', 'diff_pairs': 'auto'})['diff_pairs'], 'auto')
        with self.assertRaisesRegex(ValueError, "router.diff_pairs must be 'auto'"):
            routing_adapter.validate_router_options({'checkout': '/r', 'diff_pairs': None})
        for key, value in (('pair_layers', ['F.Cu']), ('intra_pair_match', {'tolerance_mm': .1, 'amplitude_mm': 1}),
                           ('ac_match', {'chains': [['C1', 'C2']], 'tolerance_mm': .1, 'amplitude_mm': 1})):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, f'router.{key} route differential pairs'):
                    routing_adapter.validate_router_options({'checkout': '/r', key: value})
                self.assertEqual(routing_adapter.validate_router_options(
                    {'checkout': '/r', 'diff_pairs': 'auto', key: value})['diff_pairs'], 'auto')

    def test_dangling_repair_retry_without_pair_flag(self):
        argv = self.argv()
        retry, _ = routing_adapter.board_retry_argv(argv, '/retry', 5, {'kind': 'dangling_net', 'nets': ['/A']},
                                                    [], '/routed/board.kicad_pcb')
        self.assertEqual(retry[-3:], ['--nets', '/A', '--backend-arg=--force-reroute'])
        paired, _ = routing_adapter.board_retry_argv(self.argv(diff_pairs='auto'), '/retry', 5,
            {'kind': 'dangling_net', 'nets': ['/A']}, [], '/routed/board.kicad_pcb')
        self.assertNotIn('--diff-pairs', paired)

    def refusal(self, command, **changes):
        record = {'schema': 'kicad-route/refusal/1', 'kind': 'refusal', 'exit_code': 4, 'command': 'run',
                  'reason_class': 'input', 'message': "unpaired differential net '/LOAD_P': expected '/LOAD_N'",
                  'argv': command[command.index(self.SCRIPT) + 1:], 'cwd': '/x', 'input_fingerprint': None, 'discarded_run_dir': None,
                  'refused_utc': '2026-11-04T01:42:21+00:00', 'record': 'out/refusals/1.json'}
        record.update(changes)
        return record

    def test_router_refusal_record_is_bound_to_the_invocation(self):
        import json
        import tempfile
        argv = self.argv(diff_pairs='auto')
        with tempfile.TemporaryDirectory() as tmp:
            route_dir = Path(tmp)
            (route_dir / 'out').mkdir()
            stdout = route_dir / 'kicad-route.stdout'
            stdout.write_text(json.dumps(self.refusal(argv)))
            record = routing_adapter.bind_board_attempt(route_dir, argv, 4)
            self.assertEqual(record['status'], 'router_refused')
            self.assertEqual(record['refusal'], {'reason_class': 'input', 'record': 'out/refusals/1.json',
                'message': "unpaired differential net '/LOAD_P': expected '/LOAD_N'"})
            for exit_code, changes in ((1, {}), (4, {'argv': argv[argv.index(self.SCRIPT) + 2:]}), (4, {'schema': 'kicad-route/refusal/2'}),
                                       (4, {'reason_class': 'other'}), (4, {'message': ''}),
                                       (4, {'exit_code': 1}), (4, {'kind': 'route'})):
                with self.subTest(exit_code=exit_code, changes=changes):
                    stdout.write_text(json.dumps(self.refusal(argv, **changes)))
                    invalid = routing_adapter.bind_board_attempt(route_dir, argv, exit_code)
                    self.assertEqual(invalid['status'], 'missing_output')
                    if exit_code == 4:
                        self.assertNotIn('failure_kind', invalid)
            stdout.write_text('not json')
            unreadable = routing_adapter.bind_board_attempt(route_dir, argv, 4)
            self.assertEqual((unreadable['status'], unreadable['failure_kind']), ('missing_output', 'environment'))


class SearchBudgetTests(unittest.TestCase):
    def test_search_grace_and_checks_fit_the_share_and_one_more_second_would_not(self):
        # kicad-route waits max(30, budget // 5) s for a clean stop, then 10 s and 5 s more.
        self.assertEqual([routing_adapter.stop_grace_s(b) for b in (1, 149, 150, 155, 404)], [45, 45, 45, 46, 95])
        for share in (106, 150, 195, 254, 255, 256, 300, 390, 560, 740, 900, 3600):
            with self.subTest(share=share):
                budget = routing_adapter.search_budget_s(share, 60)
                self.assertGreaterEqual(budget, 1)
                self.assertLessEqual(budget + routing_adapter.stop_grace_s(budget) + 60, share)
                self.assertGreater(budget + 1 + routing_adapter.stop_grace_s(budget + 1) + 60, share)
        self.assertEqual(routing_adapter.search_budget_s(560, 60), 404)

    def test_a_share_without_room_for_a_search_says_so(self):
        for share in (0, 60, 105):
            with self.subTest(share=share):
                self.assertLess(routing_adapter.search_budget_s(share, 60), 1)


if __name__ == '__main__':
    unittest.main()

"""Declared pair request and honest coverage, independent of routing completeness."""
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


if __name__ == '__main__':
    unittest.main()

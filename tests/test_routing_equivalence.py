"""Owned copper fixtures exercise the native comparison and sealed check path."""
import json
import re
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from agent_ee import baseline, checks
from agent_ee.routing_equivalence import canonical_ring, compare_boards
from agent_ee.storage import digest, identity, write_json
from tests.routing_fixture import make_board
from tests.toolchain import require_toolchain


class CanonicalRingTest(unittest.TestCase):
    def test_start_winding_and_closing_vertex_are_irrelevant(self):
        points = [(0, 0), (10, 0), (10, 10), (0, 10)]
        expected = canonical_ring(points)
        self.assertEqual(expected, canonical_ring(points[2:] + points[:2]))
        self.assertEqual(expected, canonical_ring(points[::-1]))
        self.assertEqual(expected, canonical_ring(points + [points[0]]))
        with self.assertRaises(ValueError):
            canonical_ring([(0, 0), (1, 1)])


class RoutingEquivalenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-equivalence-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        self.board = self.inputs / 'board.kicad_pcb'
        self.reference = self.inputs / 'reference.kicad_pcb'
        make_board(self.board, vias=1)
        self.reference.write_bytes(self.board.read_bytes())

    def edit(self, callback):
        import pcbnew as k
        board = k.LoadBoard(str(self.board))
        callback(board, k)
        k.SaveBoard(str(self.board), board)

    def compare(self):
        return compare_boards(self.board, self.reference)

    def test_uuid_segment_order_and_endpoint_direction_are_informational(self):
        raw = self.board.read_text()
        raw = re.sub(r'\(uuid "[^"]+"\)', lambda m: f'(uuid "{uuid.uuid4()}")', raw)
        blocks = re.findall(r'\t\(segment\b.*?\n\t\)', raw, re.S)
        self.assertGreater(len(blocks), 1)
        iterator = iter(reversed(blocks))
        raw = re.sub(r'\t\(segment\b.*?\n\t\)', lambda m: next(iterator), raw, flags=re.S)
        self.board.write_text(raw)
        report = self.compare()
        self.assertTrue(report['geometry_equal'])
        self.assertFalse(report['byte_equal'])
        self.assertNotEqual(report['board_sha256'], report['reference_sha256'])
        self.assertEqual(report['only_board'], [])
        def reverse(board, k):
            for track in board.GetTracks():
                if track.GetClass() == 'PCB_TRACK':
                    start, end = k.VECTOR2I(track.GetStart()), k.VECTOR2I(track.GetEnd())
                    track.SetStart(end); track.SetEnd(start)
        self.edit(reverse)
        self.assertTrue(self.compare()['geometry_equal'])

    def move_one_nanometre(self):
        def move(board, k):
            track = next(t for t in board.GetTracks() if t.GetClass() == 'PCB_TRACK')
            position = track.GetStart()
            track.SetStart(k.VECTOR2I(position.x + 1, position.y))
        self.edit(move)

    def test_one_nanometre_move_names_differing_segment(self):
        self.move_one_nanometre()
        report = self.compare()
        self.assertFalse(report['geometry_equal'])
        self.assertEqual(len(report['only_board']), 1)
        self.assertEqual(len(report['only_reference']), 1)
        self.assertEqual(report['only_board'][0]['kind'], 'PCB_TRACK')
        self.assertIn('endpoints_nm', report['only_board'][0])
        self.assertIn('net', report['only_board'][0])

    def test_width_layer_net_and_via_drill_are_geometry(self):
        edits = [lambda t, b, k: t.SetWidth(t.GetWidth() + 1),
                 lambda t, b, k: t.SetLayer(k.B_Cu)]
        for edit in edits:
            self.board.write_bytes(self.reference.read_bytes())
            self.edit(lambda b, k: edit(next(t for t in b.GetTracks()
                                            if t.GetClass() == 'PCB_TRACK' and t.GetLayer() == k.F_Cu), b, k))
            self.assertFalse(self.compare()['geometry_equal'])
        self.board.write_bytes(self.reference.read_bytes().replace(b'"D+"', b'"RENAMED"'))
        self.assertFalse(self.compare()['geometry_equal'])
        self.board.write_bytes(self.reference.read_bytes())
        self.edit(lambda b, k: next(t for t in b.GetTracks() if t.GetClass() == 'PCB_VIA').SetDrill(300001))
        self.assertFalse(self.compare()['geometry_equal'])

    def test_native_arc_midpoint_is_not_lost(self):
        def arc(board, k):
            track = k.PCB_ARC(board)
            track.SetStart(k.VECTOR2I(0, 0)); track.SetMid(k.VECTOR2I(1000000, 1000000))
            track.SetEnd(k.VECTOR2I(2000000, 0)); track.SetWidth(200000)
            track.SetLayer(k.F_Cu); track.SetNet(board.FindNet('D+')); board.Add(track)
        self.edit(arc)
        self.reference.write_bytes(self.board.read_bytes())
        self.edit(lambda b, k: next(t for t in b.GetTracks() if t.GetClass() == 'PCB_ARC').SetMid(k.VECTOR2I(1000000, 1000001)))
        self.assertFalse(self.compare()['geometry_equal'])
        self.assertEqual(self.compare()['only_board'][0]['kind'], 'PCB_ARC')

    def zone(self, points, *, fill_end=8000000, hole_end=4000000):
        def add(board, k):
            for old in list(board.Zones()):
                board.Delete(old)
            zone = k.ZONE(board); zone.SetLayer(k.F_Cu); zone.SetNet(board.FindNet('D+'))
            zone.SetMinThickness(100000)
            poly = zone.Outline(); poly.NewOutline()
            for x, y in points:
                poly.Append(x, y)
            fill = k.SHAPE_POLY_SET(); fill.NewOutline()
            for x, y in [(1000000, 1000000), (fill_end, 1000000), (fill_end, 8000000), (1000000, 8000000)]:
                fill.Append(x, y)
            fill.NewHole(0)
            for x, y in [(2000000, 2000000), (hole_end, 2000000), (hole_end, 4000000), (2000000, 4000000)]:
                fill.Append(x, y, 0, 0)
            fill.Fracture()
            zone.SetFilledPolysList(k.F_Cu, fill); zone.SetIsFilled(True)
            board.Add(zone)
        self.edit(add)

    def test_zone_contour_winding_fill_and_holes(self):
        points = [(0, 0), (10000000, 0), (10000000, 10000000), (0, 10000000)]
        self.zone(points)
        self.reference.write_bytes(self.board.read_bytes())
        self.zone(points[2:] + points[:2])
        self.assertTrue(self.compare()['geometry_equal'])
        self.zone(points[::-1])
        self.assertTrue(self.compare()['geometry_equal'])
        for options in ({'fill_end': 8000001}, {'hole_end': 4000001}):
            with self.subTest(options=options):
                self.zone(points, **options)
                self.assertFalse(self.compare()['geometry_equal'])
                self.assertEqual(self.compare()['only_board'][0]['kind'], 'ZONE')
        self.zone([(0, 0), (10000001, 0), *points[2:]])
        self.assertFalse(self.compare()['geometry_equal'])

    def manifest(self, version=3):
        (self.inputs / 'board.kicad_sch').write_text('(kicad_sch)')
        (self.inputs / 'req').write_text('R1: repeated routes preserve copper geometry')
        spec = {'id': 'equivalence', 'kind': 'routing_equivalence',
                'params': {'reference_board': self.reference.name}}
        scope = {'schema': f'agent-ee.scope/{version}', 'board': self.board.name, 'schematic': 'board.kicad_sch',
                 'checks': [spec], 'requirements': ['req'], 'baseline': {
                     'requirements': [{'id': 'R1', 'statement': 'Routes preserve geometry',
                                       'source': {'path': 'req', 'locator': 'R1'}, 'checks': ['equivalence'],
                                       **({'intent': 'acceptance'} if version == 3 else {})}],
                     'targets': ['req'], 'reference_architectures': ['req']}}
        files = {p.name: digest(p) for p in self.inputs.iterdir()}
        return {'scope': scope, 'files': files, 'candidate_id': identity({'scope': scope, 'files': files})}

    def run_check(self, manifest=None):
        manifest = manifest or self.manifest()
        write_json(self.root / 'candidate.json', manifest)
        return checks.run_checks(self.root, time.monotonic() + 600)[0]

    def test_registered_dispatch_records_byte_difference_and_sealed_defect(self):
        self.board.write_text(self.board.read_text() + '\n')
        report = self.run_check()
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        self.assertFalse(report['details']['byte_equal'])
        self.move_one_nanometre()
        manifest = self.manifest()
        write_json(self.root / 'baseline.json', baseline.compile_baseline(manifest['scope'], manifest['files'], self.inputs))
        report = self.run_check(manifest)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'), report)
        baseline.freeze_evidence(self.root, [report])
        baseline.verify_evidence(self.root, [report], required=True)
        baseline.validate_witnesses(self.root, [{'requirement_id': 'R1', 'check_id': 'equivalence'}],
                                    [{'path': 'checks/equivalence.json'}])
        with (self.root / 'work/equivalence/comparison.json').open('a') as stream:
            stream.write('\n')
        with self.assertRaisesRegex(ValueError, 'seal'):
            baseline.verify_evidence(self.root, [report], required=True)

    def test_reference_is_baseline_immutable_for_scope_two_and_three(self):
        from agent_ee.campaign import mutable_inputs
        for version in (2, 3):
            manifest = self.manifest(version)
            compiled = baseline.compile_baseline(manifest['scope'], manifest['files'], self.inputs)
            self.assertIn(self.reference.name, compiled['files'])
            self.assertNotIn(self.board.name, compiled['files'])
            if version == 3:
                descriptor = compiled['coverage']['checks']['equivalence']
                self.assertTrue(descriptor['can_evaluate_bound'])
                self.assertEqual(descriptor['predicates'], ['routing:copper-geometry-equal'])
            manifest['scope']['mutable_inputs'] = [self.reference.name]
            with self.assertRaisesRegex(ValueError, 'immutable baseline'):
                mutable_inputs(manifest, compiled)
            original = self.reference.read_bytes()
            self.reference.write_bytes(original + b'\n')
            manifest['files'][self.reference.name] = digest(self.reference)
            self.assertNotEqual(compiled['baseline_id'], baseline.compile_baseline(
                manifest['scope'], manifest['files'], self.inputs)['baseline_id'])
            self.reference.write_bytes(original)

    def test_invalid_contract_paths_and_changed_hash_fail_closed(self):
        from agent_ee.routing_equivalence import capability
        manifest = self.manifest()
        for params in ({}, {'reference_board': self.board.name}, {'reference_board': '../escape'},
                       {'reference_board': '/absolute'}, {'reference_board': 'missing'},
                       {'reference_board': self.reference.name, 'unknown': True}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                capability(params, manifest['scope'], manifest['files'], self.inputs)
        self.reference.write_text('changed')
        report = self.run_check(manifest)
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'))

    def test_missing_and_malformed_native_reports_stay_unknown(self):
        from unittest.mock import patch
        report_path = self.root / 'work/equivalence/comparison.json'
        for payload in (None, [], {}, {'schema': 'agent-ee.routing-equivalence/1',
                                      'geometry_equal': 'yes', 'byte_equal': False}):
            with self.subTest(payload=payload):
                write_json(report_path, payload)
                with patch.object(checks.Ctx, 'execute', return_value={}):
                    report = self.run_check()
                self.assertEqual((report['execution'], report['outcome']), ('malformed', 'unknown'))
        report_path.unlink()
        with patch.object(checks.Ctx, 'execute', return_value={}):
            report = self.run_check()
        self.assertEqual((report['execution'], report['outcome']), ('malformed', 'unknown'))

    def test_tool_failure_cannot_reuse_a_previous_successful_report(self):
        from unittest.mock import patch
        self.assertEqual(self.run_check()['outcome'], 'pass')
        with patch.object(checks.Ctx, 'execute', side_effect=checks.CheckError('failed', 'unit boundary failure')):
            report = self.run_check()
        self.assertEqual((report['execution'], report['outcome']), ('failed', 'unknown'))

    def test_missing_malformed_empty_and_unsupported_boards_cannot_pass(self):
        for content in ('(kicad_pcb', '(wrong_root)', '(kicad_pcb (version 20260101))'):
            self.board.write_text(content)
            report = self.run_check()
            self.assertNotEqual(report['execution'], 'ok', report)
            self.assertEqual(report['outcome'], 'unknown')
        make_board(self.board, unrouted=True, lengths=())
        report = self.run_check()
        self.assertNotEqual(report['execution'], 'ok', report)
        self.assertEqual(report['outcome'], 'unknown')
        self.board.write_bytes(self.reference.read_bytes())
        def custom_via(board, k):
            via = next(t for t in board.GetTracks() if t.GetClass() == 'PCB_VIA')
            via.Padstack().SetMode(k.PADSTACK.MODE_CUSTOM)
        self.edit(custom_via)
        report = self.run_check()
        self.assertNotEqual(report['execution'], 'ok', report)
        self.assertEqual(report['outcome'], 'unknown')
        manifest = self.manifest()
        self.reference.unlink()
        report = self.run_check(manifest)
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'))

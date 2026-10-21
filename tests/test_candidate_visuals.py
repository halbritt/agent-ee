"""Original four-layer native fixture and explicit visual failure boundaries."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent_ee.candidate import capture
from agent_ee.candidate_visuals import produce, packet_visuals
from agent_ee.storage import digest
from toolchain import require_toolchain


class CandidateVisualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        environment = patch.dict(os.environ, {'AGENT_EE_SKIP_VISUALS': '0'})
        environment.start()
        self.addCleanup(environment.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures/rc/control'
        self.source = self.root / 'source'
        shutil.copytree(fixture, self.source)
        self.board = self.source / 'rc_filter.kicad_pcb'

    def test_native_four_layer_images_and_packet_hashes(self):
        import pcbnew
        from agent_ee.reviewers import build_packet
        board = pcbnew.LoadBoard(str(self.board))
        board.SetCopperLayerCount(4)
        pcbnew.SaveBoard(str(self.board), board)
        before = {p.name: p.read_bytes() for p in self.source.iterdir() if p.is_file()}
        scope = {'schema': 'agent-ee.scope/1', 'name': 'visuals', 'scope': 'inspection',
                 'inputs': [{'path': str(self.source), 'at': 'project'}],
                 'board': 'project/rc_filter.kicad_pcb', 'schematic': 'project/rc_filter.kicad_sch',
                 'checks': []}
        scope_path = self.root / 'scope.json'
        scope_path.write_text(json.dumps(scope))
        run = self.root / 'run'
        run.mkdir()
        manifest = capture(scope_path, run)
        visuals = manifest['visuals']
        self.assertEqual(visuals['status'], 'available', visuals)
        self.assertEqual(len(visuals['images']), 6)
        self.assertEqual({i['view'] for i in visuals['images']},
                         {'top', 'bottom', 'front', 'back', 'In1.Cu', 'In2.Cu'})
        for image in visuals['images']:
            self.assertEqual(digest(run / image['path']), image['sha256'])
        packet = build_packet(run, [], 'visual-native')
        self.assertEqual(packet['candidate_visuals'], visuals)
        self.assertEqual(visuals['reproducibility']['status'], 'not_verified')
        self.assertTrue(visuals['reproducibility']['reason'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.source.iterdir() if p.is_file()})
        image = visuals['images'][0]
        (run / image['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'image changed'):
            build_packet(run, [], 'tampered')

    def test_explicit_skip_has_no_native_launch_and_type_is_strict(self):
        with patch('agent_ee.candidate_visuals.execute', side_effect=AssertionError('must not launch')):
            report = produce(self.board, self.root / 'skipped', skip=True)
        self.assertEqual(report['status'], 'skipped')
        self.assertEqual(report['images'], [])
        for value in ('false', 0, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                produce(self.board, self.root / 'invalid', skip=value)

    def test_native_failure_never_reports_available(self):
        board = self.source / 'invalid.kicad_pcb'
        board.write_text('(invalid)')
        report = produce(board, self.root / 'failed')
        self.assertEqual(report['status'], 'unavailable')
        self.assertEqual(report['images'], [])
        self.assertNotEqual(report['execution']['execution'], 'ok')
        self.assertTrue((self.root / 'failed/native/execution.json').is_file())

    def test_timeout_and_missing_manifest_remain_unavailable(self):
        for status in ('timeout', 'failed', 'ok'):
            with self.subTest(status=status), patch('agent_ee.candidate_visuals.execute',
                                                    return_value={'execution': status}):
                report = produce(self.board, self.root / status)
                self.assertEqual(report['status'], 'unavailable')
                self.assertEqual(report['images'], [])

    def test_old_capture_and_stale_source_are_explicit(self):
        self.assertEqual(packet_visuals(self.root, {})['status'], 'unavailable')
        manifest = {'scope': {'board': 'board'}, 'files': {'board': 'current'},
                    'visuals': {'board_sha256': 'stale', 'images': []}}
        with self.assertRaisesRegex(ValueError, 'different board'):
            packet_visuals(self.root, manifest)

    def test_native_placement_default_retains_images_beside_result(self):
        from test_placer_native import NativePlacerTest
        from agent_ee.placer_runner import place_candidate
        fixture = NativePlacerTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        destination = fixture.scratch / 'visual-default'
        result = place_candidate(fixture.source, fixture.netlist, destination, fixture.config)
        self.assertEqual(result['status'], 'placed')
        self.assertEqual(result['visuals']['status'], 'available', result['visuals'])
        self.assertEqual(len(result['visuals']['images']), 4)
        self.assertEqual(result['board_sha256'], result['visuals']['board_sha256'])
        for image in result['visuals']['images']:
            self.assertEqual(digest(destination / image['path']), image['sha256'])
        self.assertTrue((destination / 'visuals/manifest.json').is_file())
        self.assertEqual(json.loads((destination / 'placement-result.json').read_text()), result)

    def test_native_placement_skip_preserves_result_verdict(self):
        from test_placer_native import NativePlacerTest
        from agent_ee.placer_runner import place_candidate
        fixture = NativePlacerTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        config = {**fixture.config, 'skip_visuals': True}
        destination = fixture.scratch / 'visual-skip'
        result = place_candidate(fixture.source, fixture.netlist, destination, config)
        self.assertEqual(result['status'], 'placed')
        self.assertEqual(result['visuals']['status'], 'skipped')
        self.assertEqual(json.loads((destination / 'placement-result.json').read_text()), result)


if __name__ == '__main__':
    unittest.main()

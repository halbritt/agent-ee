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

    def test_declared_check_cannot_overwrite_coordinator_visual_manifest(self):
        import time
        from agent_ee.checks import Ctx
        from agent_ee.cli import validate_scope
        scope = {'schema': 'agent-ee.scope/1', 'name': 'visual-isolation', 'scope': 'inspection',
                 'inputs': [{'path': str(self.source), 'at': 'project'}],
                 'board': 'project/rc_filter.kicad_pcb', 'schematic': 'project/rc_filter.kicad_sch',
                 'checks': [{'id': 'candidate-visuals', 'kind': 'drc'}], 'skip_visuals': True}
        validate_scope(scope)
        scope_path = self.root / 'scope.json'
        scope_path.write_text(json.dumps(scope))
        run = self.root / 'run'
        run.mkdir()
        manifest = capture(scope_path, run)
        visual_manifest = next((run / 'work').glob('*/manifest.json'))
        captured = visual_manifest.read_bytes()
        ctx = Ctx(run, manifest, scope['checks'][0], time.monotonic() + 30)
        ctx.execute(['/usr/bin/python3', '-c',
                     "from pathlib import Path; Path('/work/manifest.json').write_text('check output')"],
                    timeout=10)
        self.assertEqual(visual_manifest.read_bytes(), captured)
        self.assertEqual((ctx.work / 'manifest.json').read_text(), 'check output')

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

    def _placement_fixture(self):
        from test_placer_native import NativePlacerTest
        fixture = NativePlacerTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_source_drift_during_imaging_refuses_placement_publication(self):
        from agent_ee.placer_runner import place_candidate
        fixture = self._placement_fixture()
        destination = fixture.scratch / 'source-drift'
        original = fixture.source.read_bytes()

        def observe_and_change_source(board, out, **kwargs):
            report = produce(board, out, skip=True)
            fixture.source.write_bytes(original + b'\n')
            return report

        with patch('agent_ee.candidate_visuals.produce', side_effect=observe_and_change_source):
            with self.assertRaisesRegex(ValueError, 'captured placement inputs changed'):
                place_candidate(fixture.source, fixture.netlist, destination, fixture.config)
        self.assertFalse(destination.exists())

    def test_native_placement_default_retains_images_beside_result(self):
        from agent_ee.placer_runner import place_candidate
        fixture = self._placement_fixture()
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

    def test_networkless_placement_runner_retains_images_through_nested_executor(self):
        from agent_ee.executor import execute
        fixture = self._placement_fixture()
        inputs = self.root / 'placement-inputs'
        inputs.mkdir()
        (inputs / 'board.kicad_pcb').write_bytes(fixture.source.read_bytes())
        (inputs / 'netlist.xml').write_bytes(fixture.netlist.read_bytes())
        config = {**fixture.config, 'authorization': {
            **fixture.config['authorization'], 'writable_root': '/work'}}
        (inputs / 'request.json').write_text(json.dumps(config))
        work = self.root / 'sandbox-placement'
        execution = execute(inputs, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.placer_runner',
            '/candidate/board.kicad_pcb', '/candidate/netlist.xml', '--request', '/candidate/request.json',
            '--destination', '/work/proposal', '--result', '/work/result.json'], timeout=240)
        self.assertEqual(execution['execution'], 'ok', (work / 'console.log').read_text())
        report = json.loads((work / 'result.json').read_text())
        self.assertEqual(report['visuals']['status'], 'available', report['visuals'])
        self.assertEqual(len(report['visuals']['images']), 4)
        for image in report['visuals']['images']:
            self.assertEqual(digest(work / 'proposal' / image['path']), image['sha256'])

    def test_native_placement_skip_preserves_result_verdict(self):
        from agent_ee.placer_runner import place_candidate
        fixture = self._placement_fixture()
        config = {**fixture.config, 'skip_visuals': True}
        destination = fixture.scratch / 'visual-skip'
        result = place_candidate(fixture.source, fixture.netlist, destination, config)
        self.assertEqual(result['status'], 'placed')
        self.assertEqual(result['visuals']['status'], 'skipped')
        self.assertEqual(json.loads((destination / 'placement-result.json').read_text()), result)


if __name__ == '__main__':
    unittest.main()

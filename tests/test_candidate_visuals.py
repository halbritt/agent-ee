"""Original four-layer native fixture and explicit visual failure boundaries."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent_ee.candidate import capture
from agent_ee.candidate_visuals import produce, packet_visuals, capture_visuals
from agent_ee.storage import digest
from toolchain import enable_visuals_for_module, native_board_render_test, require_toolchain


setUpModule = enable_visuals_for_module


class CandidateVisualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures/rc/control'
        self.source = self.root / 'source'
        shutil.copytree(fixture, self.source)
        self.board = self.source / 'rc_filter.kicad_pcb'

    @native_board_render_test
    def test_native_four_layer_images_and_packet_hashes(self):
        import pcbnew
        from agent_ee.reviewers import build_packet
        from agent_ee.cli import run_review
        board = pcbnew.LoadBoard(str(self.board))
        board.SetCopperLayerCount(4)
        pcbnew.SaveBoard(str(self.board), board)
        before = {p.name: p.read_bytes() for p in self.source.iterdir() if p.is_file()}
        scope = {'schema': 'agent-ee.scope/1', 'name': 'visuals', 'scope': 'inspection',
                 'inputs': [{'path': str(self.source), 'at': 'project'}],
                 'board': 'project/rc_filter.kicad_pcb', 'schematic': 'project/rc_filter.kicad_sch',
                 'checks': [{'id': 'drc', 'kind': 'drc'}]}
        scope_path = self.root / 'scope.json'
        scope_path.write_text(json.dumps(scope))
        run = self.root / 'run'
        run_review(scope_path, run, 240, checks_only=True)
        result = json.loads((run / 'result.json').read_text())
        self.assertNotIn('error', result, result.get('error'))
        self.assertEqual(result['checks'][0]['execution'], 'ok', result['checks'])
        manifest = json.loads((run / 'candidate.json').read_text())
        visuals = manifest['visuals']
        self.assertEqual(visuals['status'], 'available', visuals)
        self.assertEqual(len(visuals['images']), 6)
        self.assertEqual({i['view'] for i in visuals['images']},
                         {'top', 'bottom', 'front', 'back', 'In1.Cu', 'In2.Cu'})
        for image in visuals['images']:
            self.assertEqual(digest(run / image['path']), image['sha256'])
        packet = build_packet(run, result['checks'], result['run_id'])
        self.assertEqual(packet['candidate_visuals'], visuals)
        self.assertEqual(visuals['reproducibility']['status'], 'not_verified')
        self.assertIn('AGENTEE-382 pod board', visuals['reproducibility']['reason'])
        self.assertIn('SVG <title> creation timestamp', visuals['reproducibility']['reason'])
        self.assertIn('not normalized', visuals['reproducibility']['reason'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.source.iterdir() if p.is_file()})
        image = visuals['images'][0]
        (run / image['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'image changed'):
            build_packet(run, [], 'tampered')

    @native_board_render_test
    def test_imaging_preserves_twenty_second_drc_budget(self):
        from agent_ee.cli import run_review
        outcomes = []
        for skip in (True, False):
            with self.subTest(skip=skip):
                scope = {'schema': 'agent-ee.scope/1', 'name': 'visual-budget',
                         'scope': 'inspection', 'skip_visuals': skip,
                         'inputs': [{'path': str(self.source), 'at': 'project'}],
                         'board': 'project/rc_filter.kicad_pcb',
                         'schematic': 'project/rc_filter.kicad_sch',
                         'checks': [{'id': 'drc', 'kind': 'drc'}]}
                scope_path = self.root / f'budget-{skip}.json'
                scope_path.write_text(json.dumps(scope))
                run = self.root / f'budget-{skip}'
                run_review(scope_path, run, 20, checks_only=True)
                result = json.loads((run / 'result.json').read_text())
                self.assertNotIn('error', result, result.get('error'))
                check = result['checks'][0]
                outcomes.append((check['execution'], check['outcome']))
                self.assertEqual(outcomes[-1], ('ok', 'defect'))
                self.assertFalse(any('budget exhausted' in x for x in result['limitations']))
                manifest = json.loads((run / 'candidate.json').read_text())
                self.assertEqual(manifest['visuals']['status'], 'skipped' if skip else 'available')
        self.assertEqual(outcomes[0], outcomes[1])

    def test_long_imaging_excluded_without_refreshing_expired_budget(self):
        import time
        from types import SimpleNamespace
        from agent_ee.cli import run_review
        for budget in (0, 20):
            with self.subTest(budget=budget):
                scope = {'schema': 'agent-ee.scope/1', 'name': 'long-visual-budget',
                         'scope': 'inspection', 'skip_visuals': True,
                         'inputs': [{'path': str(self.source), 'at': 'project'}],
                         'board': 'project/rc_filter.kicad_pcb',
                         'schematic': 'project/rc_filter.kicad_sch',
                         'checks': [{'id': 'drc', 'kind': 'drc'}]}
                scope_path = self.root / f'long-budget-{budget}.json'
                scope_path.write_text(json.dumps(scope))
                offset = [0]
                clock = SimpleNamespace(monotonic=lambda: time.monotonic() + offset[0])
                remaining = []

                def imaging(run):
                    result = capture_visuals(run)
                    offset[0] += 300
                    return result

                def observe_deadline(run, deadline):
                    remaining.append(deadline - clock.monotonic())
                    return []

                with patch('agent_ee.cli.time', clock), \
                     patch('agent_ee.candidate_visuals.capture_visuals', side_effect=imaging), \
                     patch('agent_ee.checks.run_checks', side_effect=observe_deadline):
                    run_review(scope_path, self.root / f'long-budget-{budget}', budget, checks_only=True)
                self.assertEqual(len(remaining), 1)
                self.assertLessEqual(remaining[0], budget)
                self.assertGreater(remaining[0], budget - 5)

    def test_missing_changed_and_linked_images_share_refusal(self):
        image = self.root / 'image.png'
        image.write_bytes(b'original')
        manifest = {'scope': {'board': 'board'}, 'files': {'board': 'current'},
                    'visuals': {'board_sha256': 'current',
                                'images': [{'path': image.name, 'sha256': digest(image)}]}}
        image.unlink()
        for state in ('missing', 'changed', 'directory', 'symlink', 'fifo'):
            with self.subTest(state=state):
                if state == 'changed':
                    image.write_bytes(b'changed')
                elif state == 'directory':
                    image.mkdir()
                elif state == 'fifo':
                    os.mkfifo(image)
                elif state == 'symlink':
                    target = self.root / 'original.png'
                    target.write_bytes(b'original')
                    image.symlink_to(target)
                with self.assertRaisesRegex(ValueError, '^candidate visual image changed$'):
                    packet_visuals(self.root, manifest)
                if image.is_dir():
                    image.rmdir()
                elif image.exists() or image.is_symlink():
                    image.unlink()

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
        capture(scope_path, run)
        manifest = capture_visuals(run)
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
                                                    return_value={'execution': status}) as execute:
                report = produce(self.board, self.root / status)
                self.assertEqual(report['status'], 'unavailable')
                self.assertEqual(report['images'], [])
                self.assertEqual(execute.call_args.kwargs['timeout'], 180)

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

    @native_board_render_test
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

    @native_board_render_test
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
        # An explicit milestone render: the typed policy crosses --clearenv as AGENT_EE_SKIP_VISUALS=0
        # (AGENTEE-458); ordinary helper placement passes the skip instead.
        execution = execute(inputs, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.placer_runner',
            '/candidate/board.kicad_pcb', '/candidate/netlist.xml', '--request', '/candidate/request.json',
            '--destination', '/work/proposal', '--result', '/work/result.json'], timeout=240, skip_visuals=False)
        self.assertEqual(execution['execution'], 'ok', (work / 'console.log').read_text())
        options = execution['sandbox_argv'][:execution['sandbox_argv'].index('--')]
        at = options.index('AGENT_EE_SKIP_VISUALS')
        self.assertEqual(options[at - 1:at + 2], ['--setenv', 'AGENT_EE_SKIP_VISUALS', '0'])
        report = json.loads((work / 'result.json').read_text())
        self.assertEqual(report['visuals']['status'], 'available', report['visuals'])
        self.assertEqual(len(report['visuals']['images']), 4)
        for image in report['visuals']['images']:
            self.assertEqual(digest(work / 'proposal' / image['path']), image['sha256'])
        # Candidate-bound: the retained imaging manifest names the published proposal board.
        manifest = json.loads((work / 'proposal/visuals/manifest.json').read_text())
        self.assertEqual(manifest['board_sha256'], digest(work / 'proposal/candidate.kicad_pcb'))
        self.assertEqual((report['board_sha256'], report['visuals']['board_sha256']),
                         (manifest['board_sha256'], manifest['board_sha256']))

    @native_board_render_test
    def test_stock_defaults_and_project_models_survive_proposal_and_imaging(self):
        import pcbnew
        from agent_ee.placer_runner import place_candidate
        fixture = self._placement_fixture()
        # Original VRML cuboid; no third-party model is copied into the project.
        model = fixture.source.parent / 'inputs/models/owned.wrl'
        model.parent.mkdir(parents=True)
        model.write_text('#VRML V2.0 utf8\nShape { geometry Box { size 1 2 3 } }\n')
        board = pcbnew.LoadBoard(str(fixture.source))
        footprint = next(iter(board.GetFootprints()))
        for filename in ('${KIPRJMOD}/inputs/models/owned.wrl',
                         '${KIPRJMOD}/inputs/models/missing.stp',
                         '${KICAD10_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603_1608Metric.step'):
            shape = pcbnew.FP_3DMODEL()
            shape.m_Filename = filename
            footprint.Models().append(shape)
        pcbnew.SaveBoard(str(fixture.source), board)
        fixture.config['authorization']['source_sha256'] = digest(fixture.source)
        destination = fixture.scratch / 'model-closure'
        result = place_candidate(fixture.source, fixture.netlist, destination, fixture.config)
        self.assertEqual(result['status'], 'placed')
        self.assertEqual(result['visuals']['status'], 'available', result['visuals'])
        self.assertEqual((destination / 'inputs/models/owned.wrl').read_bytes(), model.read_bytes())
        self.assertEqual(result['project_models']['files']['inputs/models/owned.wrl']['sha256'], digest(model))
        self.assertFalse((destination / 'inputs/models/missing.stp').exists())
        models = [m for fp in result['visuals']['model_coverage']['footprints'] for m in fp['models']]
        by_path = {m['path']: m for m in models}
        stock = by_path['${KICAD10_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603_1608Metric.step']
        self.assertEqual((stock['status'], stock['resolution_source']), ('resolved', 'kicad-default'))
        self.assertEqual(by_path['${KIPRJMOD}/inputs/models/owned.wrl']['sha256'], digest(model))
        self.assertEqual(by_path['${KIPRJMOD}/inputs/models/missing.stp']['status'], 'missing')
        self.assertEqual(json.loads((destination / 'project-models.json').read_text()), result['project_models'])

    def test_project_model_drift_during_imaging_refuses_publication(self):
        import pcbnew
        from agent_ee.placer_runner import place_candidate
        fixture = self._placement_fixture()
        model = fixture.source.parent / 'owned.wrl'
        model.write_text('#VRML V2.0 utf8\nShape { geometry Box { size 1 2 3 } }\n')
        board = pcbnew.LoadBoard(str(fixture.source))
        shape = pcbnew.FP_3DMODEL()
        shape.m_Filename = '${KIPRJMOD}/owned.wrl'
        next(iter(board.GetFootprints())).Models().append(shape)
        pcbnew.SaveBoard(str(fixture.source), board)
        fixture.config['authorization']['source_sha256'] = digest(fixture.source)
        destination = fixture.scratch / 'model-drift'

        def change_model(board, out, **kwargs):
            report = produce(board, out, skip=True)
            model.write_text('changed source bytes')
            return report

        with patch('agent_ee.candidate_visuals.produce', side_effect=change_model):
            with self.assertRaisesRegex(ValueError, 'project model changed'):
                place_candidate(fixture.source, fixture.netlist, destination, fixture.config)
        self.assertFalse(destination.exists())

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

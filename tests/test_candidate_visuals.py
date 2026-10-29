"""Original four-layer native fixture and explicit visual failure boundaries."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import native_run
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
        from agent_ee.run_record import run_review
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
        from agent_ee.run_record import run_review
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
        from agent_ee.run_record import run_review
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

                with patch('agent_ee.run_record.time', clock), \
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
        from agent_ee.run_record import validate_scope
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
        with native_run.using(native_run.Recorded(lambda call: AssertionError('must not launch'))):
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
            adapter = native_run.Recorded(lambda call: native_run.Outcome(execution=status))
            with self.subTest(status=status), native_run.using(adapter):
                report = produce(self.board, self.root / status)
                self.assertEqual(report['status'], 'unavailable')
                self.assertEqual(report['images'], [])
                self.assertEqual(adapter.calls[0].timeout, 180)

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
    def test_enclosed_imaging_timeout_reaps_render_and_svg_children(self):
        """The enclosing imaging stop must reap separate native process groups."""
        from agent_ee import executor
        from test_render3d import png_bytes
        cli = os.path.realpath(shutil.which('kicad-cli') or '')
        self.assertTrue(cli.startswith('/usr/'), cli)
        # Authored PNGs exercise process lifetime only, never render quality.
        shim_code = r'''#!/usr/bin/python3
import json, os, sys, time
from pathlib import Path
REAL_CLI = __REAL_CLI__
args = sys.argv[1:]
render = args[:2] == ['pcb', 'render'] and '--help' not in args
svg = args[:3] == ['pcb', 'export', 'svg']
if not (render or svg):
    os.execv(REAL_CLI, [REAL_CLI, *args])
phase = 'render' if render else 'svg'
pid, pgid = os.getpid(), os.getpgrp()
assert pid > 1 and pgid > 1
stat = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
record = dict(pid=pid, pgid=pgid, ppid=os.getppid(), start_ticks=int(stat[19]),
              pid_namespace=os.readlink('/proc/self/ns/pid'), phase=phase,
              blocking=phase == os.environ['AGENT_EE_TEST_VISUAL_PHASE'], argv=sys.argv,
              image_evidence='AUTHORED_LIFECYCLE_ONLY')
home = Path(os.environ['HOME'])
assert home.is_dir(), f'production imaging HOME was not created: {home}'
with (home / f'lifecycle-{pid}-{stat[19]}.json').open('x') as stream:
    json.dump(record, stream)
    stream.flush()
    os.fsync(stream.fileno())
print(json.dumps(record), flush=True)
if record['blocking']:
    time.sleep(30)
    raise SystemExit('lifecycle shim outlived the outer imaging deadline')
if not render:
    raise SystemExit('unexpected unblocked SVG command')
Path(args[args.index('--output') + 1]).write_bytes(Path(__file__).with_name('lifecycle.png').read_bytes())
'''
        observer_code = r'''
import json, math, os, shutil, sys, time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from agent_ee import candidate_visuals, footprint_probe, native_run

phase, board_name = sys.argv[1:]
os.environ['AGENT_EE_TEST_VISUAL_PHASE'] = phase
assert footprint_probe._enclosing_sandbox(), 'must exercise the recognized /app branch'
project = Path('/work/project')
shutil.copytree('/candidate', project)
proof_path = Path('/work/timeout-proof.json')

def identity(pid):
    assert pid > 1
    stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return dict(pid=pid, pgid=os.getpgid(pid), start_ticks=int(stat[19]), state=stat[0],
                pid_namespace=os.readlink(f'/proc/{pid}/ns/pid'))

proof = dict(phase=phase, observer=identity(os.getpid()), boundaries=[], processes=[],
             image_evidence='AUTHORED_LIFECYCLE_ONLY', checked_before_executor_exit=False)
real_run = native_run.run
def bounded(intent, **kwargs):
    if intent.tool == 'candidate_visuals':
        assert intent.isolation == 'host', 'standalone Sandbox is not this control'
        assert intent.timeout == 180
        assert intent.stop_grace_s is not None and math.isfinite(intent.stop_grace_s) and intent.stop_grace_s > 0
        boundary = dict(argv=list(intent.argv), production_timeout=intent.timeout,
                        test_timeout=8, stop_grace_s=intent.stop_grace_s)
        proof['boundaries'].append(boundary)
        assert 'on_launch' not in kwargs
        def launched(pid):
            boundary['supervisor'] = identity(pid)
        shortened = replace(intent, timeout=8)
        assert shortened.argv == intent.argv and shortened.stop_grace_s == intent.stop_grace_s
        return real_run(shortened, **kwargs, on_launch=launched)
    return real_run(intent, **kwargs)

def survivors(records):
    found = []
    for record in records:
        pid = record['pid']
        assert pid > 1 and record['pgid'] > 1
        try:
            stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        except FileNotFoundError:
            continue
        if int(stat[19]) == record['start_ticks']:
            found.append({**record, 'state_at_observation': stat[0]})  # Zombies are still owned survivors.
    return found

try:
    with patch.object(native_run, 'run', side_effect=bounded):
        report = candidate_visuals.produce(project / board_name, project / 'visuals', inputs=project)
    proof['report'] = report
finally:
    proof['processes'] = [json.loads(p.read_text()) for p in sorted(project.rglob('lifecycle-*.json'))]
    supervisors = [b['supervisor'] for b in proof['boundaries'] if 'supervisor' in b]
    owned = [*supervisors, *proof['processes']]
    limit = time.monotonic() + 1
    remaining = survivors(owned)
    proof['remaining_at_return'] = remaining
    while remaining and time.monotonic() < limit:
        time.sleep(.02)
        remaining = survivors(owned)
    proof.update(remaining=remaining, checked_before_executor_exit=True,
                 observer_at_check=identity(os.getpid()))
    proof_path.write_text(json.dumps(proof, indent=2) + '\n')
assert len(proof['boundaries']) == 1, proof
supervisor = proof['boundaries'][0]['supervisor']
blocking = [p for p in proof['processes'] if p['blocking'] and p['phase'] == phase]
assert len(blocking) >= (2 if phase == 'render' else 1), proof
assert all(p['ppid'] == supervisor['pid'] and p['pgid'] != supervisor['pgid']
           and p['pid_namespace'] == supervisor['pid_namespace'] == proof['observer']['pid_namespace']
           for p in blocking), proof
assert report['execution']['execution'] == 'timeout' and report['status'] == 'unavailable', proof
assert not proof['remaining'], proof
'''
        for phase in ('render', 'svg'):
            with self.subTest(phase=phase):
                root = self.root / ('timeout-' + phase)
                shim = root / 'shim'
                shim.mkdir(parents=True)
                (shim / 'kicad-cli').write_text(shim_code.replace('__REAL_CLI__', repr(cli)))
                (shim / 'kicad-cli').chmod(0o755)
                (shim / 'lifecycle.png').write_bytes(png_bytes(1600, 900))
                (root / 'observer.py').write_text(observer_code)
                real_sandbox = executor.sandbox_argv

                def with_shim(*args, **kwargs):
                    command = real_sandbox(*args, **kwargs)
                    at = command.index('--clearenv')
                    return [*command[:at], '--ro-bind', str(shim), '/usr/local/bin', *command[at:]]

                work = root / 'executor'
                with patch.object(executor, 'sandbox_argv', side_effect=with_shim):
                    execution = executor.execute(self.source, work,
                        ['/usr/bin/python3', '-B', '-c', observer_code, phase, self.board.name],
                        timeout=30, network=False, skip_visuals=False)
                self.assertEqual(execution['execution'], 'ok', (work / 'console.log').read_text()[-8000:])
                proof = json.loads((work / 'timeout-proof.json').read_text())
                self.assertTrue(proof['checked_before_executor_exit'])
                self.assertEqual(proof['remaining'], [])
                self.assertEqual(proof['observer']['start_ticks'], proof['observer_at_check']['start_ticks'])

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

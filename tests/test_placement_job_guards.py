"""H1-H4 regression negatives, derived from the AGENTEE-515 Astra probes."""
import copy
from contextlib import ExitStack
import inspect
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from agent_ee import placement_job as job
import test_placement_job as fixtures
from test_placement_job import Fixture, intent, groups, MM


def materialize(source, poses, destination):
    # Allows the identical negative to exercise the immutable pre-repair source.
    kwargs = {'intent': intent()} if 'intent' in inspect.signature(job.materialize).parameters else {}
    return job.materialize(source, poses, destination, b'{}\n', **kwargs)


class ProtectedPoseTests(Fixture):
    def test_coherent_locked_and_intent_fixed_moves_refused_before_save(self):
        problem, _ = self.problem()
        poses = fixtures.NativeTransformTests._evaluate(self, problem, {})['poses']
        for ref in ('H1', 'FID'):
            for moved in (True, False):
                with self.subTest(ref=ref, moved=moved):
                    wrong = copy.deepcopy(poses)
                    row = next(p for p in wrong if p['native_ref'] == ref)
                    row['x_nm'] += MM
                    row['moved'] = moved
                    for pad in row['pads']:
                        pad['x_nm'] += MM
                    destination = self.dir / f'{ref}-{moved}'
                    with self.assertRaisesRegex(job.JobRefusal, 'protected'):
                        materialize(self.board, wrong, destination)
                    self.assertFalse((destination / 'board.kicad_pcb').exists())

    def test_protected_moved_flag_alone_is_refused(self):
        problem, _ = self.problem()
        poses = fixtures.NativeTransformTests._evaluate(self, problem, {})['poses']
        for ref in ('H1', 'FID'):
            with self.subTest(ref=ref):
                wrong = copy.deepcopy(poses)
                next(p for p in wrong if p['native_ref'] == ref)['moved'] = True
                with self.assertRaisesRegex(job.JobRefusal, 'protected'):
                    materialize(self.board, wrong, self.dir / ref)


class FinalInventoryTests(Fixture):
    def test_native_mutations_are_ineligible_even_with_clean_drc(self):
        import pcbnew
        problem, _ = self.problem()
        poses = fixtures.NativeTransformTests._evaluate(self, problem, {})['poses']
        placed = materialize(self.board, poses, self.dir / 'placed')
        placed['directory'] = str(self.dir / 'placed')
        (self.dir / 'placed/native-drc.json').write_text('{"violations":[],"unconnected_items":[]}')
        for mutation in ('position', 'size', 'drill', 'shape', 'layers', 'graphic', 'rule', 'outline', 'drawing', 'fpid'):
            with self.subTest(mutation=mutation):
                board = pcbnew.LoadBoard(placed['board'])
                fp = next(f for f in board.GetFootprints() if f.GetReference() == 'R1')
                pad = next(iter(fp.Pads()))
                if mutation == 'position':
                    pad.SetPosition(pcbnew.VECTOR2I(pad.GetPosition().x + 100000, pad.GetPosition().y))
                elif mutation == 'size':
                    pad.SetSize(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.VECTOR2I(123456, 234567))
                elif mutation == 'drill':
                    hole = next(f for f in board.GetFootprints() if f.GetReference() == 'H1')
                    next(iter(hole.Pads())).SetDrillSize(pcbnew.VECTOR2I(500000, 600000))
                elif mutation == 'shape':
                    pad.SetShape(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.PAD_SHAPE_OVAL)
                elif mutation == 'layers':
                    layers = pcbnew.LSET()
                    layers.AddLayer(pcbnew.B_Cu)
                    pad.SetLayerSet(layers)
                elif mutation == 'graphic':
                    next(iter(fp.GraphicalItems())).SetWidth(200000)
                elif mutation == 'rule':
                    board.GetDesignSettings().m_SolderMaskExpansion += 100000
                elif mutation == 'fpid':
                    fp.SetFPID(pcbnew.LIB_ID('changed', 'changed'))
                elif mutation == 'outline':
                    next(iter(board.GetDrawings())).SetWidth(200000)
                elif mutation == 'drawing':
                    from test_placement_job import _segment
                    board.Add(_segment(pcbnew, board, pcbnew.Dwgs_User, (0, 0), (MM, MM)))
                path = self.dir / f'{mutation}.kicad_pcb'
                pcbnew.SaveBoard(str(path), board)
                # Real native DRC still runs. Clean-report injection isolates eligibility
                # from the board's unrelated physical errors, after that native check.
                actual_drc = job.drc
                def clean_drc(*args, **kwargs):
                    _, execution = actual_drc(*args, **kwargs)
                    return {'violations': [], 'unconnected_items': []}, execution
                with patch.object(job, 'drc', side_effect=clean_drc):
                    final = job.final_checks({'status': 'routed', 'board': str(path)}, placed,
                                             job._owners(board), set(), self.dir / f'final-{mutation}', groups(), intent())
                self.assertTrue(final['identity_problems'], mutation)
                self.assertEqual(final.get('eligibility'), 'INELIGIBLE')
                self.assertEqual(final['physical_checks'], 'FAILED')

    def test_only_tracks_vias_and_explicitly_owned_zones_may_change(self):
        import pcbnew
        problem, _ = self.problem()
        poses = fixtures.NativeTransformTests._evaluate(self, problem, {})['poses']
        placed = materialize(self.board, poses, self.dir / 'placed')
        placed['directory'] = str(self.dir / 'placed')
        (self.dir / 'placed/native-drc.json').write_text('{"violations":[],"unconnected_items":[]}')
        board = pcbnew.LoadBoard(placed['board'])
        track = pcbnew.PCB_TRACK(board)
        track.SetStart(pcbnew.VECTOR2I(MM, MM))
        track.SetEnd(pcbnew.VECTOR2I(2 * MM, MM))
        track.SetWidth(200000)
        track.SetLayer(pcbnew.F_Cu)
        board.Add(track)
        via = pcbnew.PCB_VIA(board)
        via.SetPosition(pcbnew.VECTOR2I(2 * MM, MM))
        via.SetWidth(600000)
        via.SetDrill(300000)
        via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        board.Add(via)
        path = self.dir / 'routed.kicad_pcb'
        pcbnew.SaveBoard(str(path), board)
        final = job.final_checks({'status': 'routed', 'board': str(path)}, placed, job._owners(board), set(),
                                 self.dir / 'final', groups(), intent())
        self.assertEqual(final['identity_problems'], [])
        zone = pcbnew.ZONE(board)
        zone.SetLayer(pcbnew.F_Cu)
        resistor = next(fp for fp in board.GetFootprints() if fp.GetReference() == 'R1')
        zone.SetNet(next(iter(resistor.Pads())).GetNet())
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in ((1, 1), (2, 1), (2, 2), (1, 2)):
            outline.Append(pcbnew.VECTOR2I(x * MM, y * MM))
        board.Add(zone)
        pcbnew.SaveBoard(str(path), board)
        routed = {'status': 'routed', 'board': str(path)}
        protected = job.final_checks(routed, placed, job._owners(board), set(), self.dir / 'protected-zone', groups(), intent())
        self.assertTrue(protected['identity_problems'], 'an unowned zone must be protected')
        owned = job.final_checks(routed, placed, job._owners(board), set(), self.dir / 'owned-zone', groups(), intent(),
                                 owned_zone_uuids={zone.m_Uuid.AsString()})
        self.assertEqual(owned['identity_problems'], [])


class ControllerBoundaryTests(Fixture):
    def test_cancel_and_deadline_between_judge_route_and_final_preserve_incumbent(self):
        problem, _ = self.problem()
        for mode in ('cancel', 'deadline', 'cancel-after-route', 'deadline-after-route', 'integrity-failure'):
            with self.subTest(mode=mode):
                out = self.dir / mode
                config = {'budget_s': 60, 'inputs': {k: {} for k in ('board', 'project', 'intent', 'groups')},
                          'search': {'shortlist': 1}, 'router': {'checkout': 'unused', 'budget_s': 1}}
                values = [(self.board, self.board.read_bytes()), (self.project, b'{}'),
                          (self.board, json.dumps(intent()).encode()), (self.board, json.dumps(groups()).encode())]
                remaining = [60.0]
                drc_calls = 0
                entry = {'index': 0, 'seed': 0, 'board_sha256': 'fixture', 'errors': {}, 'engine_violations': [],
                         'directory': str(self.dir), 'board': str(self.board)}
                def judged(*args, **kwargs):
                    (out / 'incumbent.json').write_text(json.dumps(entry))
                    return {'board': str(self.board), 'board_sha256': 'fixture', 'integrity': 'preserved', 'problems': []}
                def checked_drc(*args, **kwargs):
                    nonlocal drc_calls
                    drc_calls += 1
                    if drc_calls == 2:
                        if mode == 'cancel':
                            (out / 'CANCEL').touch()
                        elif mode == 'deadline':
                            remaining[0] = -1.0
                    return {'violations': [], 'unconnected_items': []}, {}
                output = {'timing': {'prepare_s': 0}, 'status': 'completed', 'counters': {}, 'seeds': [], 'prng': 'probe',
                          'shortlist': [{'seed': 0, 'cost': '1', 'poses': []}]}
                with ExitStack() as stack:
                    stack.enter_context(patch.object(job, 'verified_input', side_effect=values))
                    stack.enter_context(patch.object(job, 'preflight', return_value={'engine_sha256': 'probe'}))
                    stack.enter_context(patch.object(job, 'capture', return_value=(problem, {})))
                    stack.enter_context(patch.object(job, 'solve', return_value=(output, {})))
                    stack.enter_context(patch.object(job.Clock, 'remaining', side_effect=lambda: remaining[0]))
                    stack.enter_context(patch.object(job, 'materialize', side_effect=judged))
                    stack.enter_context(patch.object(job, 'promote', return_value=False))
                    stack.enter_context(patch.object(job, 'retain_diagnostic', return_value=False))
                    stack.enter_context(patch.object(job, 'drc', side_effect=checked_drc))
                    def routed(*args, **kwargs):
                        if mode.startswith('cancel'):
                            (out / 'CANCEL').touch()
                        elif mode.startswith('deadline'):
                            remaining[0] = -1.0
                        return {'status': 'routed', 'board': str(self.board), 'preflight': {
                            k: None for k in ('commit', 'pin', 'executable_sha256', 'adapter_sha256', 'problems')}}
                    route = stack.enter_context(patch.object(job, 'route', side_effect=routed))
                    final = stack.enter_context(patch.object(job, 'final_checks', return_value={
                        'integrity': 'violated', 'physical_checks': 'FAILED'}))
                    report = job.run_job(config, out)
                self.assertEqual(route.called, mode.endswith('after-route') or mode == 'integrity-failure')
                self.assertEqual(final.called, mode == 'integrity-failure')
                if route.called:
                    self.assertEqual(route.call_args.kwargs['budget_s'], 60)
                    self.assertEqual(route.call_args.kwargs['timeout_s'], 60)
                expected = 'integrity_failed' if mode == 'integrity-failure' else (
                    'cancelled' if mode.startswith('cancel') else 'deadline')
                self.assertEqual(report['status'], expected)
                self.assertEqual(json.loads((out / 'incumbent.json').read_text()), entry)
                self.assertEqual(json.loads((out / 'job-report.json').read_text())['status'], report['status'])

    def test_stalled_engine_stream_is_bounded(self):
        problem, _ = self.problem()
        child = self.dir / 'stalled'
        child.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(20)\n')
        child.chmod(0o755)
        def alarm(*args):
            raise TimeoutError('stalled stdout exceeded outer test bound')
        old = signal.signal(signal.SIGALRM, alarm)
        signal.setitimer(signal.ITIMER_REAL, 2)
        started = time.monotonic()
        try:
            try:
                output, _ = job.solve(problem, self.search(1, 1), self.dir, deadline_s=0.2, executable=child)
            except TimeoutError as exc:
                self.fail(str(exc))
            self.assertEqual(output['status'], 'deadline')
            self.assertEqual(output['seeds'], [], 'no seed progress was observed')
            self.assertIsNone(output['timing']['prepare_s'])
            self.assertLess(time.monotonic() - started, 1)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)


class ChildCancellationTests(Fixture):
    def test_watchdog_bounds_child_which_never_reads_stdin(self):
        started = time.monotonic()
        with self.assertRaisesRegex(Exception, 'deadline'):
            job._run_child([sys.executable, '-c', 'import time; time.sleep(20)'],
                           input_bytes=b'x' * 1000000, clock=job.Clock(0.2))
        self.assertLess(time.monotonic() - started, 1)

    def test_cancel_during_route_and_drc_bounds_stalled_children(self):
        from agent_ee import kicad_route_attempt, routing_adapter
        for phase in ('route', 'drc'):
            with self.subTest(phase=phase):
                cancel = self.dir / 'CANCEL'
                cancel.unlink(missing_ok=True)
                started_file = self.dir / f'{phase}-started'
                shim = self.dir / ('bin/kicad-route' if phase == 'route' else 'kicad-cli')
                shim.parent.mkdir(exist_ok=True)
                shim.write_text('#!/usr/bin/python3\nimport os,time\nfrom pathlib import Path\n'
                                f'Path({str(started_file)!r}).write_text(str(os.getpid()))\n'
                                'time.sleep(20)\n')
                shim.chmod(0o755)
                stopped = threading.Event()
                def cancel_child():
                    limit = time.monotonic() + 2
                    while not stopped.wait(0.01) and time.monotonic() < limit:
                        if started_file.exists():
                            cancel.touch()
                            return
                thread = threading.Thread(target=cancel_child)
                thread.start()
                old = signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(
                    TimeoutError(f'{phase} ignored cancellation')))
                signal.setitimer(signal.ITIMER_REAL, 3)
                started = time.monotonic()
                try:
                    clock = job.Clock(10)
                    with self.assertRaisesRegex(Exception, 'cancelled'):
                        if phase == 'route':
                            optional = {'clock': clock, 'cancel_dir': self.dir} if 'clock' in inspect.signature(job.route).parameters else {}
                            with patch.object(routing_adapter, 'preflight', return_value={'problems': []}), \
                                    patch.object(kicad_route_attempt, 'PYTHON', sys.executable):
                                job.route(self.dir, self.dir / 'route', self.dir, budget_s=10, timeout_s=10,
                                          home=self.dir / 'home', **optional)
                        else:
                            optional = {'clock': clock, 'cancel_dir': self.dir} if 'clock' in inspect.signature(job.drc).parameters else {}
                            with patch.dict(os.environ, {'PATH': str(self.dir) + os.pathsep + os.environ['PATH']}):
                                job.drc(self.board, self.dir, **optional)
                    self.assertLess(time.monotonic() - started, 1.5)
                    pid = int(started_file.read_text())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                finally:
                    stopped.set()
                    thread.join(timeout=1)
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    signal.signal(signal.SIGALRM, old)

    def test_watchdog_kills_descendants_even_after_leader_closes_streams(self):
        marker = self.dir / 'descendant'
        script = ('import subprocess,sys\n'
                  'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(20)"])\n'
                  f'open({str(marker)!r},"w").write(str(p.pid))\n')
        with self.assertRaisesRegex(Exception, 'deadline'):
            job._run_child([sys.executable, '-c', script], clock=job.Clock(0.3))
        pid = int(marker.read_text())
        limit = time.monotonic() + 1
        while time.monotonic() < limit:
            stat = Path(f'/proc/{pid}/stat')
            if not stat.exists() or stat.read_text().split()[2] == 'Z':
                break
            time.sleep(0.01)
        else:
            self.fail('owned descendant remains running after deadline')


class RequiredFixtureTests(unittest.TestCase):
    def test_missing_fixture_fails_strict_but_skips_optional(self):
        name = 'test_unchanged_esp_prog_capture_is_accepted_with_labelled_derived_occupancy'
        for strict in ('1', '0'):
            with self.subTest(strict=strict), patch.dict(os.environ, {'AGENT_EE_REQUIRE_TOOLCHAIN': strict}):
                with patch.dict(os.environ):
                    os.environ.pop('AGENT_EE_ESP_PROG_PREPARED', None)
                    result = unittest.TestResult()
                    fixtures.EspProgCaptureTests(name).run(result)
                    self.assertEqual(len(result.failures), int(strict == '1'))
                    self.assertEqual(len(result.skipped), int(strict != '1'))
                    self.assertEqual(result.errors, [])

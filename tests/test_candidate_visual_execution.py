"""CPU-only contracts for the enclosing-executor visual child boundary."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import candidate_visuals as visuals, footprint_probe, native_run
from agent_ee.stage_receipts import file_tree
from agent_ee.storage import digest, write_json


class VisualExecutionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.inputs = self.root / 'project'
        self.inputs.mkdir()
        self.board = self.inputs / 'candidate.kicad_pcb'
        self.board.write_bytes(b'captured board')
        for suffix in ('.kicad_pro', '.kicad_dru'):
            self.board.with_suffix(suffix).write_text('captured ' + suffix)
        (self.inputs / 'models').mkdir()
        (self.inputs / 'models/owned.wrl').write_text('captured model')
        self.enterContext(patch.dict(os.environ, AGENT_EE_SKIP_VISUALS='0'))

    def test_enclosed_child_copies_adjacent_assets_without_recursing_into_output(self):
        before = file_tree(self.inputs)
        out = self.inputs / 'visuals'
        def child(intent, *, work):
            self.assertEqual((intent.isolation, intent.timeout, intent.stop_grace_s), ('host', 180, 5))
            self.assertEqual(intent.argv[:4], ('/usr/bin/python3', '-B', '-P', '-c'))
            board, output = map(Path, intent.argv[-2:])
            self.assertEqual(output, work)
            self.assertEqual(board, work / 'inputs/candidate.kicad_pcb')
            self.assertEqual(file_tree(board.parent), before)
            self.assertFalse((board.parent / 'visuals').exists())
            self.assertEqual(intent.env['PATH'], os.environ['PATH'])
            self.assertEqual(intent.env['HOME'], str(work / 'home'))
            self.assertEqual(intent.env['KICAD_CONFIG_HOME'], str(work / 'kicad-config'))
            self.assertEqual(intent.env['PYTHONPATH'], '/app')
            self.assertTrue(Path(intent.env['HOME']).is_dir())
            self.assertTrue(Path(intent.env['KICAD_CONFIG_HOME']).is_dir())
            board.with_suffix('.kicad_prl').write_text('owned disposable preferences')
            (work / 'top.png').write_bytes(b'CPU image bytes, not native rendering')
            write_json(work / 'visuals.json', {'board_sha256': digest(board), 'status': 'available',
                'images': [{'view': 'top', 'path': 'top.png', 'sha256': digest(work / 'top.png')}]})
            write_json(work / 'imaging-cleanup.json', {'schema': 'agent-ee.candidate-imaging-cleanup/1', 'status': 'unwound'})
            return {'execution': 'ok', 'exit_code': 0}
        with patch.object(footprint_probe, '_enclosing_sandbox', return_value=True), \
                patch.object(native_run, 'run', side_effect=child) as run:
            report = visuals.produce(self.board, out)
        run.assert_called_once()
        self.assertEqual(report['status'], 'available')
        self.assertEqual(report['images'][0]['path'], 'native/top.png')
        for rel, sha in before[0].items():
            self.assertEqual(digest(self.inputs / rel), sha)
        self.assertFalse(self.board.with_suffix('.kicad_prl').exists())
        manifest = {'scope': {'board': 'board'}, 'files': {'board': digest(self.board)}, 'visuals': report}
        visuals.packet_visuals(out, manifest)
        (out / 'native/top.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'image changed'):
            visuals.packet_visuals(out, manifest)

    def test_original_and_disposable_design_or_model_drift_refuses_and_retains_raws(self):
        for i, (original, rel) in enumerate(((True, 'models/owned.wrl'),
                (True, 'candidate.kicad_prl'), (False, 'candidate.kicad_pcb'),
                (False, 'candidate.kicad_pro'), (False, 'candidate.kicad_dru'),
                (False, 'models/owned.wrl'), (False, 'unexpected.txt'))):
            out = self.root / ('failure-' + str(i))
            def child(intent, *, work):
                board = Path(intent.argv[-2])
                target = (self.inputs if original else board.parent) / rel
                target.write_bytes(b'changed')
                (work / 'stderr.log').write_text('raw imaging failure diagnostic')
                return {'execution': 'failed', 'exit_code': 97}
            before = {str(p.relative_to(self.inputs)): p.read_bytes()
                      for p in self.inputs.rglob('*') if p.is_file()}
            with self.subTest(original=original, rel=rel), \
                    patch.object(footprint_probe, '_enclosing_sandbox', return_value=True), \
                    patch.object(native_run, 'run', side_effect=child), \
                    self.assertRaisesRegex(ValueError, 'changed its captured inputs' if original else 'changed its private project'):
                visuals.produce(self.board, out)
            self.assertEqual((out / 'native/stderr.log').read_text(), 'raw imaging failure diagnostic')
            # Restore only this test's fixtures between independent controls.
            for path in self.inputs.rglob('*'):
                if path.is_file() and str(path.relative_to(self.inputs)) not in before:
                    path.unlink()
            for path, data in before.items():
                (self.inputs / path).write_bytes(data)

    def test_timeout_refusal_and_missing_manifest_stay_unavailable(self):
        for status, code in (('timeout', None), ('failed', 97), ('ok', 0)):
            def child(intent, *, work):
                (work / 'stderr.log').write_text('renderer refused or incomplete')
                write_json(work / 'imaging-cleanup.json', {'schema': 'agent-ee.candidate-imaging-cleanup/1', 'status': 'unwound'})
                return {'execution': status, 'exit_code': code}
            with self.subTest(status=status), patch.object(footprint_probe, '_enclosing_sandbox', return_value=True), \
                    patch.object(native_run, 'run', side_effect=child):
                out = self.root / status
                report = visuals.produce(self.board, out)
                self.assertEqual((report['status'], report['images']), ('unavailable', []))
                self.assertEqual(report['execution']['exit_code'], code)
                self.assertTrue((out / 'native/stderr.log').is_file())

    def test_skip_does_not_inspect_guard_or_launch_and_standalone_keeps_sandbox(self):
        with patch.object(footprint_probe, '_enclosing_sandbox', side_effect=AssertionError('skip guard')), \
                patch.object(native_run, 'run', side_effect=AssertionError('skip launch')):
            self.assertEqual(visuals.produce(self.board, self.root / 'skip', skip=True)['status'], 'skipped')
        with native_run.using(native_run.Recorded(lambda call: native_run.Outcome(execution='failed'))) as adapter:
            visuals.produce(self.board, self.root / 'standalone')
        self.assertEqual(adapter.calls[0].intent.isolation, 'sandbox')
        self.assertEqual(adapter.calls[0].intent.argv,
            ('/usr/bin/python3', '-B', '-m', 'agent_ee.candidate_visuals', self.board.name))

    def test_invalid_guard_links_and_output_ancestor_refuse_before_launch(self):
        with patch.object(footprint_probe, '__file__', '/app/agent_ee/footprint_probe.py'), \
                patch.object(Path, 'read_text', return_value='malformed'), \
                patch.object(native_run, 'run', side_effect=AssertionError('invalid guard launch')):
            with self.assertRaisesRegex(ValueError, 'fixed executor sandbox'):
                visuals.produce(self.board, self.root / 'invalid-view')
        link = self.inputs / 'linked-model.wrl'
        link.symlink_to(self.inputs / 'models/owned.wrl')
        with patch.object(footprint_probe, '_enclosing_sandbox', return_value=True), \
                patch.object(native_run, 'run', side_effect=AssertionError('unsafe launch')):
            with self.assertRaisesRegex(ValueError, 'regular captured files'):
                visuals.produce(self.board, self.root / 'linked')
            with self.assertRaises(FileExistsError):
                visuals.produce(self.board, self.root)

    def test_post_child_output_ancestry_replacement_refuses_before_any_manifest_read(self):
        for name in ('output', 'native', 'private'):
            out = self.inputs / ('visuals-' + name)
            moved = self.root / ('moved-' + name)
            def child(intent, *, work):
                (work / 'stderr.log').write_text('retained moved raw failure')
                source = {'output': out, 'native': work, 'private': work / 'inputs'}[name]
                source.rename(moved)
                source.symlink_to(moved, target_is_directory=True)
                return {'execution': 'ok', 'exit_code': 0}
            with self.subTest(name=name), patch.object(footprint_probe, '_enclosing_sandbox', return_value=True), \
                    patch.object(native_run, 'run', side_effect=child), \
                    patch.object(visuals, 'read_json', side_effect=AssertionError('must not follow output link')):
                with self.assertRaisesRegex(ValueError, 'output ancestry changed'):
                    visuals.produce(self.board, out)
            raw = moved / ('native/stderr.log' if name == 'output' else 'stderr.log')
            if name != 'private':
                self.assertEqual(raw.read_text(), 'retained moved raw failure')
            self.assertTrue(moved.is_dir())
            self.assertFalse((moved / 'manifest.json').exists())
            # Remove only owned fixture links so subsequent source inventories are regular.
            {'output': out, 'native': out / 'native', 'private': out / 'native/inputs'}[name].unlink()

    def test_missing_cleanup_receipt_refuses_even_if_child_exits_ok(self):
        with patch.object(footprint_probe, '_enclosing_sandbox', return_value=True), \
                patch.object(native_run, 'run', return_value={'execution': 'ok', 'exit_code': 0}):
            with self.assertRaisesRegex(ValueError, 'cleanup is unconfirmed'):
                visuals.produce(self.board, self.root / 'no-cleanup')

    def test_term_unwinds_actual_render_workers_and_joins_before_cleanup_receipt(self):
        import signal
        import threading
        from agent_ee import render3d, models3d
        board = Path(__file__).resolve().parents[1] / 'fixtures/stages/empty.kicad_pcb'
        out = self.root / 'worker-cleanup'; out.mkdir()
        handlers, observed = {}, []
        entered = threading.Barrier(3)
        def register(sig, handler):
            previous = handlers.get(sig, signal.SIG_DFL)
            handlers[sig] = handler
            return previous
        def child(intent, **kwargs):
            cancel = kwargs['cancel']; observed.append(cancel)
            entered.wait(timeout=2)
            self.assertTrue(cancel.cancelled.wait(timeout=2))
            return native_run.Execution({'execution': 'interrupted', 'returncode': -9}, b'', b'')
        def completed(futures):
            entered.wait(timeout=2)
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            return iter(futures)
        def imaging(board, root):
            render3d.render_board(board, root / 'renders', jobs=2, views=('top', 'bottom'))
        with patch.object(signal, 'signal', side_effect=register), \
                patch.object(signal, 'raise_signal', side_effect=lambda sig: handlers[sig](sig, None)), \
                patch.object(visuals, 'native', side_effect=imaging), \
                patch.object(render3d.shutil, 'which', return_value='/usr/bin/true'), \
                patch.object(render3d, '_probe', return_value=' '.join(render3d._REQUIRED)), \
                patch.object(models3d, 'inspect_models', return_value={'footprints': [], 'warnings': []}), \
                patch.object(render3d.concurrent.futures, 'as_completed', side_effect=completed), \
                patch.object(native_run, 'run', side_effect=child), \
                patch('os.killpg', side_effect=AssertionError('no real signals')):
            with self.assertRaises(KeyboardInterrupt):
                visuals._imaging_main(str(board), str(out))
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(c.cancelled.is_set() for c in observed))
        self.assertFalse(any(t.name.startswith('render3d') for t in threading.enumerate()))
        self.assertEqual(__import__('json').loads((out / 'imaging-cleanup.json').read_text())['status'], 'unwound')

    def test_term_unwinds_svg_and_outer_grace_is_bounded_without_real_signals(self):
        import signal
        import subprocess
        from types import SimpleNamespace
        handlers, sent = {}, []
        def register(sig, handler):
            previous = handlers.get(sig, signal.SIG_DFL); handlers[sig] = handler; return previous
        def stop(pid, sig):
            if pid <= 1:
                raise ValueError('unsafe PID/PG')
            sent.append((pid, sig))
        def child(*args, **kwargs):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        out = self.root / 'svg-cleanup'; out.mkdir()
        with patch.object(signal, 'signal', side_effect=register), \
                patch.object(signal, 'raise_signal', side_effect=lambda sig: handlers[sig](sig, None)), \
                patch.object(visuals, 'native', side_effect=lambda b,r: visuals._plot(b,r,'front',['F.Cu'])), \
                patch.object(native_run, 'run', side_effect=child), patch('os.killpg', side_effect=stop):
            with self.assertRaises(KeyboardInterrupt):
                visuals._imaging_main(str(self.board), str(out))
            self.assertTrue((out / 'imaging-cleanup.json').is_file())
            proc = SimpleNamespace(pid=24680, wait=lambda **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired('mock', kw['timeout'])))
            native_run._stop_group(proc, 5)
            self.assertEqual(sent, [(24680, signal.SIGTERM), (24680, signal.SIGKILL)])
            with self.assertRaisesRegex(ValueError, 'unsafe PID/PG'):
                stop(1, signal.SIGKILL)

    def test_term_respects_existing_held_interrupt_launch_window(self):
        import signal
        from agent_ee.executor import held_interrupt
        handlers = {signal.SIGINT: signal.default_int_handler, signal.SIGTERM: signal.SIG_DFL}
        def register(sig, handler):
            previous = handlers[sig]; handlers[sig] = handler; return previous
        def imaging(board, root):
            with held_interrupt() as pending:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
                self.assertEqual(pending, [signal.SIGINT])
                # Existing supervisor can first retain the newly created handle.
            raise KeyboardInterrupt
        out = self.root / 'launch-window'; out.mkdir()
        with patch.object(signal, 'signal', side_effect=register), \
                patch.object(signal, 'getsignal', side_effect=lambda sig: handlers[sig]), \
                patch.object(signal, 'raise_signal', side_effect=lambda sig: handlers[sig](sig, None)), \
                patch.object(visuals, 'native', side_effect=imaging):
            with self.assertRaises(KeyboardInterrupt):
                visuals._imaging_main(str(self.board), str(out))
        self.assertTrue((out / 'imaging-cleanup.json').is_file())

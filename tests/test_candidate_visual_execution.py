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
            self.assertEqual((intent.isolation, intent.timeout), ('host', 180))
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
            board.with_suffix('.kicad_prl').write_text('owned disposable preferences')
            (work / 'top.png').write_bytes(b'CPU image bytes, not native rendering')
            write_json(work / 'visuals.json', {'board_sha256': digest(board), 'status': 'available',
                'images': [{'view': 'top', 'path': 'top.png', 'sha256': digest(work / 'top.png')}]})
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

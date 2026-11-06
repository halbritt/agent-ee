"""Native test reports and replay inputs survive their workspace cleanup."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.artifact_retention import RetainedTemporaryDirectory, retain


class ArtifactRetentionTests(unittest.TestCase):
    def test_exception_cleanup_retains_case_identity_reports_bytes_and_links_without_following(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence, outside = root / 'evidence', root / 'outside'
            outside.write_text('external target')
            outside.chmod(0o444)
            with patch.dict(os.environ, {'AGENT_EE_EVIDENCE_DIR': str(evidence)}):
                with self.assertRaisesRegex(RuntimeError, 'deliberate failure'):
                    with RetainedTemporaryDirectory('suite.case.test_failure') as working:
                        source = Path(working)
                        (source / 'report.json').write_text('{"status":"refused"}')
                        (source / 'outside-link').symlink_to(outside)
                        retain('suite.case.test_failure', {'verdict': 'refused'})
                        raise RuntimeError('deliberate failure')
            self.assertFalse(source.exists())
            records = [json.loads(p.read_text()) for p in evidence.rglob('evidence.json')]
            self.assertEqual(len(records), 2)
            self.assertTrue(all(r['test_id'] == 'suite.case.test_failure' for r in records))
            tree = next(r for r in records if r['roots'])['roots'][0]
            self.assertEqual(tree['files']['outside-link'], {'symlink': str(outside)})
            captured = next(evidence.rglob('root-0/report.json'))
            self.assertEqual(json.loads(captured.read_text()), {'status': 'refused'})
            self.assertEqual(outside.read_text(), 'external target')
            self.assertEqual(outside.stat().st_mode & 0o777, 0o444)

    def test_disabled_capture_leaves_ordinary_temporary_cleanup_intact(self):
        with patch.dict(os.environ, {'AGENT_EE_EVIDENCE_DIR': ''}):
            with RetainedTemporaryDirectory(self.id()) as working:
                self.assertIsNone(retain(self.id(), {'value': 1}))
            self.assertFalse(Path(working).exists())

    def test_capture_failure_does_not_skip_cleanup_or_hide_the_original_test_error(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / 'blocked'
            destination.write_text('a file cannot contain retained evidence')
            paths = []
            resources = []

            class Probe(unittest.TestCase):
                def runTest(self):
                    working = RetainedTemporaryDirectory(self.id())
                    resources.append(working)  # Observe explicit cleanup, before finalizer fallback.
                    paths.append(Path(working.name))
                    self.addCleanup(working.cleanup)
                    raise RuntimeError('original consumer diagnostic')

            with patch.dict(os.environ, {'AGENT_EE_EVIDENCE_DIR': str(destination)}):
                result = unittest.TestResult()
                Probe().run(result)
            self.assertEqual(len(result.errors), 2)
            diagnostics = '\n'.join(text for _, text in result.errors)
            self.assertIn('original consumer diagnostic', diagnostics)
            self.assertIn('NotADirectoryError', diagnostics)
            self.assertTrue(all(not path.exists() for path in paths))

    def test_native_consumer_cleanup_captures_source_and_both_owned_roots(self):
        from tests.test_routing_experiment_boundary import NativeReceiptCase

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            source.mkdir()
            (source / 'input.json').write_text('{"fixture":"portable lifecycle only"}')
            evidence = root / 'evidence'
            paths = []

            class Probe(NativeReceiptCase, unittest.TestCase):
                RUN, SOURCE, PINNED = 'portable-run', 'owned-portable-source', {}
                fail_body = False

                def setUp(self):
                    self.source = source
                    workspace = tempfile.TemporaryDirectory()
                    self.addCleanup(workspace.cleanup)
                    self.root = Path(workspace.name)
                    paths.append(self.root)

                def runTest(self):
                    boundary = unittest.TestCase()
                    boundary.tmp = tempfile.TemporaryDirectory()
                    boundary.addCleanup(boundary.tmp.cleanup)
                    paths.append(Path(boundary.tmp.name))
                    self.capture_consumer_boundary(boundary)
                    (self.root / 'disposable.json').write_text('{"owned":"disposable"}')
                    (Path(boundary.tmp.name) / 'receipt.json').write_text('{"status":"refused"}')
                    self.consumer_outcomes.append({'fixture': 'portable', 'refusal': 'observed refusal'})
                    if self.fail_body:
                        raise RuntimeError('original direct-consumer diagnostic')

            with patch.dict(os.environ, {'AGENT_EE_EVIDENCE_DIR': str(evidence)}):
                result = unittest.TestResult()
                probe = Probe()
                probe.run(result)
            self.assertTrue(result.wasSuccessful(), result.errors)
            self.assertTrue(all(not path.exists() for path in paths))
            record_path, = evidence.rglob('evidence.json')
            record = json.loads(record_path.read_text())
            self.assertEqual(record['test_id'], probe.id())
            self.assertEqual(record['payload']['source_input']['path'], str(source))
            self.assertEqual(record['payload']['consumer_outcomes'],
                             [{'fixture': 'portable', 'refusal': 'observed refusal'}])
            self.assertEqual(len(record['roots']), 3)
            self.assertEqual(json.loads((record_path.parent / 'root-2/receipt.json').read_text()),
                             {'status': 'refused'})
            self.assertTrue((record_path.parent / 'root-0/input.json').is_file())
            self.assertTrue((record_path.parent / 'root-1/disposable.json').is_file())
            blocked = root / 'blocked'
            blocked.write_text('capture cannot create a directory here')
            Probe.fail_body = True
            with patch.dict(os.environ, {'AGENT_EE_EVIDENCE_DIR': str(blocked)}):
                failed = unittest.TestResult()
                Probe().run(failed)
            self.assertEqual(len(failed.errors), 2)
            diagnostic = '\n'.join(text for _, text in failed.errors)
            self.assertIn('original direct-consumer diagnostic', diagnostic)
            self.assertIn('NotADirectoryError', diagnostic)
            self.assertTrue(all(not path.exists() for path in paths))
            self.assertEqual((source / 'input.json').read_text(), '{"fixture":"portable lifecycle only"}')


if __name__ == '__main__':
    unittest.main()

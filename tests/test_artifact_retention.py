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


if __name__ == '__main__':
    unittest.main()

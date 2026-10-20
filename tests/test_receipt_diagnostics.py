"""Refusal diagnostics preserve failures even when the checkout is read-only."""

import contextlib
import gc
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import receipt_diagnostics as diagnostics


class ReceiptDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'receipt'
        self.source.mkdir()
        self.receipt = {'schema': 'agent-ee.stage-receipt/1', 'status': 'refused',
                        'violations': ['source_changed']}
        (self.source / 'receipt.json').write_text(json.dumps(self.receipt))
        (self.source / 'stderr.log').write_text('tool diagnostics')
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop('AGENT_EE_REFUSED_RECEIPTS', None)
        patch = mock.patch.object(diagnostics, 'RETENTION_ROOT', self.root / 'retained')
        patch.start()
        self.addCleanup(patch.stop)

    def retained(self):
        with self.assertRaisesRegex(AssertionError, 'step refused; violations.*source_changed.*receipt kept at') as raised:
            diagnostics.retain_refusal(self.source, self.receipt)
        path = Path(str(raised.exception).split('receipt kept at ')[1])
        self.assertEqual(json.loads(path.read_text()), self.receipt)
        self.assertEqual((path.parent / 'stderr.log').read_text(), 'tool diagnostics')
        return path

    def test_writable_repository_is_default(self):
        self.assertEqual(self.retained().parent.parent, diagnostics.RETENTION_ROOT)

    def test_absolute_override_takes_precedence(self):
        override = self.root / 'override'
        os.environ['AGENT_EE_REFUSED_RECEIPTS'] = str(override)
        self.assertEqual(self.retained().parent.parent, override)
        self.assertFalse(diagnostics.RETENTION_ROOT.exists())

    def test_relative_or_empty_override_preserves_refusal_with_explicit_error(self):
        for value in ('relative', ''):
            with self.subTest(value=value):
                os.environ['AGENT_EE_REFUSED_RECEIPTS'] = value
                with self.assertRaisesRegex(AssertionError, 'violations.*source_changed.*retention failed:.*must be absolute'):
                    diagnostics.retain_refusal(self.source, self.receipt)
        self.assertFalse(diagnostics.RETENTION_ROOT.exists())

    def test_unwritable_repository_falls_back_to_private_user_temp(self):
        repository = self.root / 'readonly'
        repository.mkdir(mode=0o500)
        self.addCleanup(repository.chmod, 0o700)
        with mock.patch.object(diagnostics, 'RETENTION_ROOT', repository / 'build/refused-step-receipts'):
            path = self.retained()
        private = path.parent.parent
        self.addCleanup(shutil.rmtree, private)
        self.assertEqual(private.parent, Path(tempfile.gettempdir()))
        self.assertTrue(private.name.startswith(f'agent-ee-refused-{os.getuid()}-'))
        self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o700)
        self.assertEqual(private.stat().st_uid, os.getuid())
        self.assertFalse((repository / 'build').exists())

    def test_copy_failure_never_claims_retention_or_changes_refusal(self):
        with mock.patch.object(diagnostics.shutil, 'copytree', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(AssertionError, 'violations.*source_changed.*retention failed: disk full') as raised:
                diagnostics.retain_refusal(self.source, self.receipt)
        self.assertNotIn('receipt kept at', str(raised.exception))
        self.assertIn(str(self.source / 'receipt.json'), str(raised.exception))

    def test_failed_override_is_reported_without_silent_redirection(self):
        os.environ['AGENT_EE_REFUSED_RECEIPTS'] = str(self.source / 'receipt.json' / 'not-a-directory')
        with self.assertRaisesRegex(AssertionError, 'source_changed.*retention failed:'):
            diagnostics.retain_refusal(self.source, self.receipt)

    def test_completed_receipt_needs_no_retention(self):
        os.environ['AGENT_EE_REFUSED_RECEIPTS'] = 'invalid-relative-path'
        diagnostics.retain_refusal(self.source, {'status': 'completed'})
        self.assertFalse(diagnostics.RETENTION_ROOT.exists())

    def test_cleanup_failure_preserves_original_exception_and_removes_fixture(self):
        output = io.StringIO()
        fixture = diagnostics.ReceiptTemporaryDirectory(dir=self.root)
        self.addCleanup(fixture.cleanup)
        shutil.copytree(self.source, Path(fixture.name) / 'run')
        with contextlib.redirect_stderr(output), \
                mock.patch.object(diagnostics.shutil, 'copytree', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'original failure'):
                with fixture:
                    raise RuntimeError('original failure')
        self.assertFalse(Path(fixture.name).exists())
        self.assertIn("violations ['source_changed']", output.getvalue())
        self.assertIn('receipt retention failed: disk full', output.getvalue())

    def test_finalizer_retains_before_removing_fixture(self):
        output = io.StringIO()
        tmp = diagnostics.ReceiptTemporaryDirectory(dir=self.root)
        shutil.copytree(self.source, Path(tmp.name) / 'run')
        name = tmp.name
        with contextlib.redirect_stderr(output):
            del tmp
            gc.collect()
        self.assertFalse(Path(name).exists())
        self.assertIn('receipt kept at', output.getvalue())
        self.assertEqual(len(list(diagnostics.RETENTION_ROOT.glob('*/receipt.json'))), 1)

    def test_scan_failure_does_not_mask_cleanup(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output), \
                mock.patch.object(Path, 'rglob', side_effect=PermissionError('unreadable')):
            with diagnostics.ReceiptTemporaryDirectory(dir=self.root) as tmp:
                pass
        self.assertFalse(Path(tmp).exists())
        self.assertIn('retention scan failed', output.getvalue())

    def test_fallback_permissions_survive_restrictive_umask(self):
        blocker = self.root / 'not-directory'
        blocker.write_text('block repository retention')
        previous = os.umask(0o077)
        try:
            with mock.patch.object(diagnostics, 'RETENTION_ROOT', blocker / 'retained'):
                path = self.retained()
        finally:
            os.umask(previous)
        private = path.parent.parent
        self.addCleanup(shutil.rmtree, private)
        self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o700)

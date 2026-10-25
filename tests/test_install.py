import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.launcher = self.home / '.local/bin/agent-ee'
        self.launcher.parent.mkdir(parents=True)
        self.environment = dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.home / '.codex'))

    def install(self, *options):
        return subprocess.run(['bash', str(ROOT / 'scripts/install.sh'), *options],
                              env=self.environment, capture_output=True, text=True)

    def test_install_is_repeatable_without_replacing_unrelated_paths(self):
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.launcher.resolve(), ROOT / 'bin/agent-ee')
        self.launcher.unlink()
        self.launcher.write_text('unrelated executable\n')
        refused = self.install('--migrate-uv-launcher')
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(self.launcher.read_text(), 'unrelated executable\n')

    def test_recognized_uv_launcher_requires_explicit_migration_and_keeps_backup(self):
        uv = self.home / '.local/share/uv/tools/agent-ee/bin/agent-ee'
        uv.parent.mkdir(parents=True)
        uv.write_text('#!/uv/python\nfrom agent_ee.cli import main\n')
        self.launcher.symlink_to(uv)
        refused = self.install()
        self.assertEqual(refused.returncode, 1)
        self.assertIn('--migrate-uv-launcher', refused.stderr)
        self.assertEqual(self.launcher.resolve(), uv)
        migrated = self.install('--migrate-uv-launcher')
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        self.assertEqual(self.launcher.resolve(), ROOT / 'bin/agent-ee')
        self.assertEqual(self.launcher.with_name('agent-ee.uv-backup').resolve(), uv)
        self.assertEqual(self.install('--migrate-uv-launcher').returncode, 0)

    def test_uv_migration_refuses_unknown_content_and_existing_backup(self):
        uv = self.home / '.local/share/uv/tools/agent-ee/bin/agent-ee'
        uv.parent.mkdir(parents=True)
        self.launcher.symlink_to(uv)
        for content, backup in [('unrelated\n', False), ('from agent_ee.cli import main\n', True)]:
            with self.subTest(content=content, backup=backup):
                uv.write_text(content)
                if backup:
                    self.launcher.with_name('agent-ee.uv-backup').write_text('preserve me')
                self.assertEqual(self.install('--migrate-uv-launcher').returncode, 1)
                self.assertEqual(self.launcher.resolve(), uv)
                self.assertEqual(uv.read_text(), content)

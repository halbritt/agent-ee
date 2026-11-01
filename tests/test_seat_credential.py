"""A login refresh during a long review must not fail the seats that start after it."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

from agent_ee.harnesses import seat_credential


class SeatCredentialTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.login = self.root / 'auth.json'
        self.login.write_text('first login')

    def test_copy_is_private_read_only_outside_the_run_and_removed(self):
        with seat_credential(self.login) as copy:
            self.assertEqual(copy.read_text(), 'first login')
            self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o400)
            self.assertTrue(copy.is_relative_to('/dev/shm'))
            directory = copy.parent
        self.assertFalse(directory.exists())

    def test_held_login_can_still_be_copied_after_a_refresh_replaces_its_file(self):
        with self.login.open() as held:
            replacement = self.root / 'auth.new'
            replacement.write_text('refreshed login')
            os.replace(replacement, self.login)
            with seat_credential(f'/proc/self/fd/{held.fileno()}') as copy:
                self.assertEqual(copy.read_text(), 'first login')

    @unittest.skipUnless(shutil.which('bwrap'), 'bwrap is required to bind the copy')
    def test_copy_can_be_bind_mounted_by_the_seat_sandbox(self):
        with seat_credential(self.login) as copy:
            done = subprocess.run(['bwrap', '--ro-bind', '/', '/', '--ro-bind', str(copy), str(self.login),
                                   '/bin/cat', str(self.login)], capture_output=True, text=True, timeout=30)
        self.assertEqual((done.returncode, done.stdout), (0, 'first login'), done.stderr)


if __name__ == '__main__':
    unittest.main()

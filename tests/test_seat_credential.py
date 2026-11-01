"""A login refresh during a long review must not fail the seats that start after it."""
import os
from pathlib import Path
import stat
import tempfile
import unittest

from agent_ee import native_run
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

    def test_seat_sandbox_binds_and_reads_the_copy(self):
        from toolchain import require_toolchain
        require_toolchain()
        inputs = self.root / 'inputs'
        inputs.mkdir()
        with seat_credential(self.login) as copy:
            record = native_run.run(native_run.Intent('model', ['/usr/bin/cat', '/work/home/auth.json'], timeout=30),
                                    inputs=inputs, work=self.root / 'work', mounts=[(copy, '/work/home/auth.json')])
        self.assertEqual(record['execution'], 'ok', record)
        self.assertEqual((self.root / 'work/stdout.log').read_text(), 'first login')


if __name__ == '__main__':
    unittest.main()

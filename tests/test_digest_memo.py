"""No digest is reused across verification calls (AGENTEE-250).

File status cannot prove unchanged bytes, and with sudo no file is immutable to this
user's processes, so every call hashes every file; within one verification call each
file is hashed once. Every mutation case below must yield the true digest."""
import errno
import hashlib
import mmap
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import executor, storage
from agent_ee.candidate import verify_snapshot
from agent_ee.storage import digest, digest_fd, digest_scope

try:
    from toolchain import toolchain_test
except ImportError:  # module-qualified invocation from the repository root
    from tests.toolchain import toolchain_test

RUNTIME = '/usr/bin/python3'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Counted(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-digest-memo-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.hashed = []
        original = storage._hash_stream

        def counted(stream, checkpoint):
            self.hashed.append(os.fstat(stream.fileno()).st_ino)
            return original(stream, checkpoint)
        patcher = patch('agent_ee.storage._hash_stream', side_effect=counted)
        patcher.start()
        self.addCleanup(patcher.stop)

    def file(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def settle(self):
        # Older than two seconds, the racy-timestamp window a status-validated memo would allow.
        time.sleep(2.2)


class CrossCallMutationTests(Counted):
    """Each case would return a stale digest from a status-validated memo across calls."""

    def test_shared_writable_mapping_change_returns_true_digest(self):
        # agent-469's AGENTEE-250 reproducer: bytes change while size, mtime and ctime do not.
        path = self.file('payload.bin', b'A' * 4096)
        with path.open('r+b') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_WRITE) as mapped:
            mapped[0] = ord('B')
            self.settle()
            original = digest(path)
            before = os.stat(path)
            mapped[1] = ord('C')
            after = os.stat(path)
            returned = digest(path)
            actual = sha(path.read_bytes())
        self.assertEqual(storage._status(before), storage._status(after))
        self.assertNotEqual(original, actual)
        self.assertEqual(returned, actual)
        self.assertEqual(len(self.hashed), 2)

    def test_no_cross_call_memo_remains(self):
        for name in ('digest_memo', 'immutable_to_agents', '_memo', 'RACY_NS', 'LOCAL_FILESYSTEMS'):
            self.assertFalse(hasattr(storage, name), name)

    def test_write_with_restored_timestamps_returns_true_digest(self):
        path = self.file('a', b'alpha')
        self.settle()
        before = os.stat(path)
        self.assertEqual(digest(path), sha(b'alpha'))
        path.write_bytes(b'omega')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(digest(path), sha(b'omega'))

    def test_same_second_rewrite_returns_true_digest(self):
        path = self.file('a', b'alpha')
        self.assertEqual(digest(path), sha(b'alpha'))
        path.write_bytes(b'omega')
        self.assertEqual(digest(path), sha(b'omega'))
        path.write_bytes(b'gamma')
        self.assertEqual(digest_fd(os.open(path, os.O_RDONLY)), sha(b'gamma'))

    def test_rename_over_returns_true_digest(self):
        path, other = self.file('a', b'alpha'), self.file('b', b'bravo')
        self.settle()
        self.assertEqual(digest(path), sha(b'alpha'))
        os.replace(other, path)
        self.assertEqual(digest(path), sha(b'bravo'))

    def test_change_through_hardlink_returns_true_digest(self):
        path = self.file('a', b'alpha')
        link = self.root / 'link'
        os.link(path, link)
        with link.open('r+b') as stream, mmap.mmap(stream.fileno(), 0) as mapped:
            # An already dirty shared page takes later writes without a status change.
            mapped[0] = ord('a')
            self.settle()
            self.assertEqual(digest(path), sha(b'alpha'))
            mapped[:] = b'omega'
            self.assertEqual(digest(path), sha(b'omega'))

    def test_symlink_swap_hashes_new_target_and_nofollow_refuses_link(self):
        first, second = self.file('first', b'first'), self.file('second', b'other')
        link = self.root / 'link'
        link.symlink_to(first)
        self.settle()
        self.assertEqual(digest(link), sha(b'first'))
        staged = self.root / 'staged'
        staged.symlink_to(second)
        os.replace(staged, link)
        self.assertEqual(digest(link), sha(b'other'))
        with self.assertRaises(OSError) as caught:
            digest(link, follow_symlinks=False)
        self.assertEqual(caught.exception.errno, errno.ELOOP)

    def test_snapshot_refuses_a_link_swapped_in_after_its_link_check(self):
        run = self.root / 'run'
        (run / 'inputs').mkdir(parents=True)
        (run / 'inputs/board').symlink_to(self.file('target', b'board'))
        (run / 'candidate.json').write_text('{"files": {}, "scope": {}, "candidate_id": ""}')
        with patch('pathlib.Path.is_symlink', return_value=False), \
                self.assertRaisesRegex(ValueError, 'snapshot gained a live link'):
            verify_snapshot(run)


class CrossCallRehashTests(Counted):
    def test_user_owned_files_are_hashed_on_every_call(self):
        writable, sealed = self.file('writable', b'alpha'), self.file('sealed', b'bravo')
        sealed.chmod(0o444)
        self.settle()
        for _ in range(3):
            self.assertEqual(digest(writable), sha(b'alpha'))
            self.assertEqual(digest(sealed), sha(b'bravo'))
        self.assertEqual(len(self.hashed), 6)

    @toolchain_test
    def test_root_owned_files_are_hashed_on_every_call(self):
        # With sudo a root-owned file is not immutable to this user's processes.
        info = os.stat(RUNTIME)
        self.assertEqual((info.st_uid, info.st_mode & 0o022), (0, 0), RUNTIME)
        expected = digest(RUNTIME)
        for _ in range(2):
            with digest_scope():
                self.assertEqual(digest(RUNTIME), expected)
                self.assertEqual(digest(RUNTIME), expected)
        self.assertEqual(digest(RUNTIME), expected)
        self.assertEqual(len(self.hashed), 4)


class VerificationScopeTests(Counted):
    def test_one_call_hashes_a_file_once_and_never_beyond_the_call(self):
        path = self.file('a', b'alpha')
        with digest_scope():
            with digest_scope():
                self.assertEqual(digest(path), sha(b'alpha'))
            self.assertEqual(digest(path), sha(b'alpha'))
            self.assertEqual(digest_fd(os.open(path, os.O_RDONLY)), sha(b'alpha'))
        self.assertEqual(len(self.hashed), 1)
        with digest_scope():
            digest(path)
        digest(path)
        self.assertEqual(len(self.hashed), 3)
        self.assertIsNone(storage._scope.get())

    def test_status_or_inode_change_within_a_call_is_hashed_again(self):
        path, other = self.file('a', b'alpha'), self.file('b', b'bravo')
        with digest_scope():
            self.assertEqual(digest(path), sha(b'alpha'))
            os.replace(other, path)
            self.assertEqual(digest(path), sha(b'bravo'))
            path.write_bytes(b'longer bytes')
            self.assertEqual(digest(path), sha(b'longer bytes'))

    @toolchain_test
    def test_tool_execution_discards_the_call_observations(self):
        inputs, work = self.root / 'inputs', self.root / 'work'
        inputs.mkdir()
        work.mkdir()
        path = self.file('a', b'alpha')
        with digest_scope():
            digest(path)
            self.assertTrue(storage._scope.get())
            record = executor.execute(inputs, work, ['/usr/bin/true'])
            self.assertEqual(record['execution'], 'ok')
            self.assertEqual(storage._scope.get(), {})
            digest(path)
        self.assertEqual(len(self.hashed), 2)

    def test_checkpoint_still_runs_on_reuse(self):
        path = self.file('a', b'alpha')
        calls = []
        with digest_scope():
            digest(path, checkpoint=lambda: calls.append(1))
            hashed = len(calls)
            digest(path, checkpoint=lambda: calls.append(1))
        self.assertGreaterEqual(hashed, 1)
        self.assertEqual(len(calls), hashed + 1)

        def refuse():
            raise ValueError('cancelled')
        with digest_scope(), self.assertRaisesRegex(ValueError, 'cancelled'):
            digest(path)
            digest(path, checkpoint=refuse)


if __name__ == '__main__':
    unittest.main()

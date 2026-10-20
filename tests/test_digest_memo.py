"""digest() never reuses an observation (AGENTEE-250).

File status cannot prove unchanged bytes, and with sudo no file is immutable to this
user's processes, so digest() and digest_fd() hash the bytes on every call, inside a
digest_scope() too. Only fingerprint(), for runtime files nothing in the scope reads,
reuses an observation, and only within one scope. Every mutation case below must yield
the true digest."""
import contextlib
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
from agent_ee.storage import digest, digest_fd, digest_scope, fingerprint

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

    @contextlib.contextmanager
    def frozen_status(self, path):
        # AGENTEE-294: delayed mmap writeback can update mtime/ctime even after
        # settle(). Control only the stat boundary; mapping and hashing stay real.
        # Cover both path and descriptor lookups so a stat-trusting memo sees the
        # same real stat_result throughout, independently of kernel writeback.
        before = os.stat(path)
        # Keep the observation older than a racy-timestamp memo guard, too.
        self.settle()
        with patch('os.stat', return_value=before), patch('os.fstat', return_value=before):
            yield

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
            with self.frozen_status(path):
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
                self.assertEqual(fingerprint(RUNTIME), expected)
                self.assertEqual(fingerprint(RUNTIME), expected)
        self.assertEqual(digest(RUNTIME), expected)
        self.assertEqual(len(self.hashed), 4)


class DigestInScopeTests(Counted):
    """Inside a scope digest() still reads the bytes, so no stale pin pairs with a later read."""

    def test_agent469_within_scope_probe_returns_true_digest(self):
        # agent-469's within-scope reproducer: hash, change through a shared mapping with
        # status unchanged, hash again inside the same scope. The second digest is the truth.
        path = self.file('payload.bin', b'A' * 4096)
        with path.open('r+b') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_WRITE) as mapped:
            mapped[0] = ord('B')
            with self.frozen_status(path), digest_scope():
                original = digest(path)
                before = os.stat(path)
                mapped[1] = ord('C')
                after = os.stat(path)
                returned = digest(path)
                actual = sha(path.read_bytes())
                fd_returned = digest_fd(os.open(path, os.O_RDONLY))
        self.assertEqual(storage._status(before), storage._status(after))
        self.assertNotEqual(original, actual)
        self.assertEqual(returned, actual)
        self.assertEqual(fd_returned, actual)
        self.assertEqual(len(self.hashed), 3)

    def test_digest_neither_reads_nor_feeds_fingerprint_observations(self):
        path = self.file('a', b'alpha')
        with digest_scope():
            fingerprint(path)
            digest(path)
            digest(path)
            self.assertEqual(len(storage._scope.get()), 1)
            other = self.file('b', b'bravo')
            digest(other)
            self.assertEqual(len(storage._scope.get()), 1)
        self.assertEqual(len(self.hashed), 4)


class FingerprintScopeTests(Counted):
    def test_one_observation_fingerprints_a_file_once_and_never_beyond_it(self):
        path = self.file('a', b'alpha')
        with digest_scope():
            with digest_scope():
                self.assertEqual(fingerprint(path), sha(b'alpha'))
            self.assertEqual(fingerprint(path), sha(b'alpha'))
            link = self.root / 'second-name'
            link.symlink_to(path)
            self.assertEqual(fingerprint(link), sha(b'alpha'))
        self.assertEqual(len(self.hashed), 1)
        with digest_scope():
            fingerprint(path)
        fingerprint(path)
        fingerprint(path)
        self.assertEqual(len(self.hashed), 4)
        self.assertIsNone(storage._scope.get())

    def test_status_or_inode_change_within_an_observation_is_hashed_again(self):
        path, other = self.file('a', b'alpha'), self.file('b', b'bravo')
        with digest_scope():
            self.assertEqual(fingerprint(path), sha(b'alpha'))
            os.replace(other, path)
            self.assertEqual(fingerprint(path), sha(b'bravo'))
            path.write_bytes(b'longer bytes')
            self.assertEqual(fingerprint(path), sha(b'longer bytes'))

    @toolchain_test
    def test_tool_execution_discards_the_observations(self):
        inputs, work = self.root / 'inputs', self.root / 'work'
        inputs.mkdir()
        work.mkdir()
        path = self.file('a', b'alpha')
        with digest_scope():
            fingerprint(path)
            self.assertTrue(storage._scope.get())
            record = executor.execute(inputs, work, ['/usr/bin/true'])
            self.assertEqual(record['execution'], 'ok')
            self.assertEqual(storage._scope.get(), {})
            fingerprint(path)
        self.assertEqual(len(self.hashed), 2)

    def test_checkpoint_still_runs_on_reuse(self):
        path = self.file('a', b'alpha')
        calls = []
        with digest_scope():
            fingerprint(path, checkpoint=lambda: calls.append(1))
            hashed = len(calls)
            fingerprint(path, checkpoint=lambda: calls.append(1))
        self.assertGreaterEqual(hashed, 1)
        self.assertEqual(len(calls), hashed + 1)

        def refuse():
            raise ValueError('cancelled')
        with digest_scope(), self.assertRaisesRegex(ValueError, 'cancelled'):
            fingerprint(path)
            fingerprint(path, checkpoint=refuse)

    def test_only_runtime_fingerprints_take_a_scope(self):
        # Verification paths take no scope: every candidate and record digest is a fresh read.
        import inspect
        from agent_ee import campaign, cli
        for function in (cli.verify, cli.verify_records, cli.artifacts, campaign.verify_campaign_records):
            self.assertNotIn('digest_scope', inspect.getsource(function), function.__name__)
        self.assertIn('digest_scope', inspect.getsource(cli.runtime_files))
        self.assertIn('runtime_digest', inspect.getsource(cli._runtime_files))


if __name__ == '__main__':
    unittest.main()

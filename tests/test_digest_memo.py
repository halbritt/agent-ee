"""Within-run digest reuse is bound to unchanged file status and never outlives its run (AGENTEE-250)."""
import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import storage
from agent_ee.storage import digest, digest_fd, digest_memo


def sha(data):
    return hashlib.sha256(data).hexdigest()


class DigestMemoTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-digest-memo-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.hashed = []
        original = storage._hash_stream

        def counted(stream, checkpoint):
            self.hashed.append(stream.name)
            return original(stream, checkpoint)
        patcher = patch('agent_ee.storage._hash_stream', side_effect=counted)
        patcher.start()
        self.addCleanup(patcher.stop)

    def aged(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def settle(self):
        # Status changes newer than RACY_NS are never reused; wait them out once.
        time.sleep(storage.RACY_NS / 1e9 + 0.2)

    def test_unchanged_file_is_hashed_once_per_run(self):
        path = self.aged('a', b'alpha' * 1000)
        self.settle()
        with digest_memo():
            values = {digest(path) for _ in range(50)}
            values.add(digest_fd(os.open(path, os.O_RDONLY)))
        self.assertEqual(values, {sha(b'alpha' * 1000)})
        self.assertEqual(len(self.hashed), 1)

    def test_no_reuse_outside_or_across_runs(self):
        path = self.aged('a', b'alpha')
        self.settle()
        for _ in range(3):
            digest(path)
        self.assertEqual(len(self.hashed), 3)
        with digest_memo():
            digest(path)
        with digest_memo():
            digest(path)
        self.assertEqual(len(self.hashed), 5)
        self.assertIsNone(storage._memo)

    def test_same_size_rewrite_with_restored_mtime_is_detected(self):
        path = self.aged('a', b'alpha')
        self.settle()
        before = os.stat(path)
        with digest_memo():
            self.assertEqual(digest(path), sha(b'alpha'))
            path.write_bytes(b'omega')
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(os.stat(path).st_size, before.st_size)
            self.assertEqual(digest(path), sha(b'omega'))

    def test_rename_over_and_truncation_are_detected(self):
        path = self.aged('a', b'alpha')
        other = self.aged('b', b'bravo')
        self.settle()
        with digest_memo():
            self.assertEqual(digest(path), sha(b'alpha'))
            os.replace(other, path)
            self.assertEqual(digest(path), sha(b'bravo'))
            with open(path, 'r+b') as stream:
                stream.truncate(2)
            self.assertEqual(digest(path), sha(b'br'))

    def test_recently_changed_file_is_never_reused(self):
        # A write in the same timestamp tick as hashing could leave the status unchanged.
        path = self.aged('a', b'alpha')
        with digest_memo():
            for _ in range(3):
                self.assertEqual(digest(path), sha(b'alpha'))
        self.assertEqual(len(self.hashed), 3)

    def test_checkpoint_still_runs_on_reuse(self):
        path = self.aged('a', b'alpha')
        self.settle()
        calls = []
        with digest_memo():
            digest(path, checkpoint=lambda: calls.append(1))
            hashed = len(calls)
            digest(path, checkpoint=lambda: calls.append(1))
        self.assertGreaterEqual(hashed, 1)
        self.assertEqual(len(calls), hashed + 1)

        def refuse():
            raise ValueError('cancelled')
        with digest_memo(), self.assertRaisesRegex(ValueError, 'cancelled'):
            digest(path)
            digest(path, checkpoint=refuse)


if __name__ == '__main__':
    unittest.main()

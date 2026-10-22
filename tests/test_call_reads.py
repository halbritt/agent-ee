"""Per-call verified reads in parts-manifest qualification (AGENTEE-454).

One dependency_closure/qualify_manifest call reads and hashes each distinct capture
file once and hands every consumer those verified bytes; the next call rereads current
bytes. Fixtures are the original synthetic named-part captures of test_approved_parts.
"""
import collections
import mmap
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from agent_ee import storage
from agent_ee.datasheet_facts import load_acquisition
from agent_ee.parts_manifest import _read_blob, dependency_closure, qualify_manifest
from agent_ee.storage import CallReads, digest, read_json
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_approved_parts import named_fixture
from test_parts_subset import captured_fixture


class ReadCounter:
    """Counts whole-file reads and stream hashes per resolved path; every read still happens."""

    def __init__(self, test):
        self.reads = collections.Counter()
        original_bytes, original_hash = Path.read_bytes, storage._hash_stream

        def read_bytes(path):
            self.reads[os.path.realpath(path)] += 1
            return original_bytes(path)

        def hash_stream(stream, checkpoint):
            name = getattr(stream, 'name', None)
            if isinstance(name, str):
                self.reads[os.path.realpath(name)] += 1
            return original_hash(stream, checkpoint)

        test.enterContext(mock.patch.object(Path, 'read_bytes', read_bytes))
        test.enterContext(mock.patch.object(storage, '_hash_stream', hash_stream))


def flip(path):
    """Same-size, same-mtime change of the last byte through a shared writable mapping."""
    before = os.stat(path)
    with open(path, 'r+b') as stream, mmap.mmap(stream.fileno(), 0) as mapped:
        mapped[-1] = ord('A') if mapped[-1] != ord('A') else ord('B')
        mapped.flush()
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    return before


class CallReadsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_hit_requires_same_real_file_and_digest(self):
        a, b = self.root / 'a.bin', self.root / 'b.bin'
        a.write_bytes(b'first')
        b.write_bytes(b'other')
        reads = CallReads()
        raw, found = reads.read(a)
        self.assertEqual((raw, found), (b'first', digest(a)))
        a.write_bytes(b'later')
        # Within the call, the pinned digest is served from the bytes verified first.
        self.assertEqual(reads.read(a, found), (b'first', found))
        self.assertEqual(reads.read(a), (b'first', found))
        # A different expected digest never hits: the file is read now.
        self.assertEqual(reads.read(a, '0' * 64), (b'later', digest(a)))
        # An alias resolving to a different file never hits under the first file's digest.
        alias = self.root / 'alias.bin'
        alias.symlink_to(b)
        self.assertEqual(reads.read(alias, found), (b'other', digest(b)))
        # A new call rereads current bytes.
        self.assertEqual(CallReads().read(a), (b'later', digest(a)))

    def test_digest_always_reads_current_bytes(self):
        path = self.root / 'x.bin'
        path.write_bytes(b'0123456789')
        first = digest(path)
        before = flip(path)
        after = os.stat(path)
        self.assertEqual((before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns))
        self.assertNotEqual(digest(path), first)


class ClosureReadOnceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.document = captured_fixture(self, self.root)
        self.record = named_fixture(self.root, self.document)
        self.pdf = self.root / self.record['artifact']['path']
        self.acquisition = self.root / self.record['datasheets']['path']

    def test_each_file_read_once_per_call_and_again_next_call(self):
        counter = ReadCounter(self)
        first = dependency_closure(self.document, self.root)
        self.assertEqual(first[self.record['artifact']['path']], self.record['artifact']['sha256'])
        per_call = dict(counter.reads)
        for path in first:
            with self.subTest(path=path):
                self.assertEqual(per_call.get(os.path.realpath(self.root / path)), 1)
        self.assertEqual(dependency_closure(self.document, self.root), first)
        for path in first:
            with self.subTest(second_call=path):
                self.assertEqual(counter.reads[os.path.realpath(self.root / path)], 2)

    def test_load_acquisition_reads_each_pdf_once_without_reads(self):
        counter = ReadCounter(self)
        bindings = [dict(id=r['id'], datasheet_url=r['url']) for r in read_json(self.acquisition)['parts']]
        acquired, paths = load_acquisition(self.root, self.record['datasheets'], bindings)
        self.assertIn(self.record['artifact']['path'], paths)
        self.assertEqual(counter.reads[os.path.realpath(self.pdf)], 1)
        self.assertEqual(counter.reads[os.path.realpath(self.acquisition)], 1)

    def test_changed_bytes_between_calls_refused(self):
        qualify_manifest(self.document, self.root)
        for path in (self.pdf, self.acquisition, self.root / self.record['catalog']['path'],
                     self.root / 'named/C38695/responses/C38695.json'):
            original = path.read_bytes()
            path.write_bytes(original[:-1] + (b'A' if original[-1:] != b'A' else b'B'))
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                dependency_closure(self.document, self.root)
            with self.subTest(path=path.name, entry='qualify'), self.assertRaises(ValueError):
                qualify_manifest(self.document, self.root)
            path.write_bytes(original)
            dependency_closure(self.document, self.root)

    def test_mmap_in_place_write_between_calls_refused(self):
        dependency_closure(self.document, self.root)
        for path in (self.pdf, self.acquisition):
            original = path.read_bytes()
            before = flip(path)
            after = os.stat(path)
            self.assertEqual((before.st_ino, before.st_size, before.st_mtime_ns),
                             (after.st_ino, after.st_size, after.st_mtime_ns))
            with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, 'hash mismatch'):
                dependency_closure(self.document, self.root)
            path.write_bytes(original)

    def test_within_call_substitution_cannot_change_consumed_bytes(self):
        original = self.pdf.read_bytes()
        forged = original.replace(b'Owned', b'Forge')
        self.assertEqual(len(forged), len(original))
        real = os.path.realpath(self.pdf)
        seen, read = [], CallReads.read

        def substitute_after_first_read(reads, path, sha256=None):
            result = read(reads, path, sha256)
            if os.path.realpath(path) == real:
                seen.append(result[0])
                self.pdf.write_bytes(forged)
            return result

        with mock.patch.object(CallReads, 'read', substitute_after_first_read):
            closure = dependency_closure(self.document, self.root)
        self.assertEqual(closure[self.record['artifact']['path']], self.record['artifact']['sha256'])
        self.assertTrue(seen)
        self.assertTrue(all(raw == original for raw in seen))
        # The substituted bytes are refused by the next call, which rereads from scratch.
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            dependency_closure(self.document, self.root)

    def test_policy_rechecked_on_reuse_and_alias_misses(self):
        bindings = [dict(id=r['id'], datasheet_url=r['url']) for r in read_json(self.acquisition)['parts']]
        reads = CallReads()
        load_acquisition(self.root, self.record['datasheets'], bindings, reads)
        # Different bindings are a different key: replayed, and refused on their identity.
        with self.assertRaisesRegex(ValueError, 'binding identity'):
            load_acquisition(self.root, self.record['datasheets'],
                             [dict(id='other', datasheet_url='https://example.org/x.pdf')], reads)
        # A different pinned digest is a different key and still refused.
        with self.assertRaisesRegex(ValueError, 'acquisition hash mismatch'):
            load_acquisition(self.root, dict(self.record['datasheets'], sha256='0' * 64), bindings, reads)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            _read_blob(self.root, dict(path=self.record['artifact']['path'], sha256='0' * 64), reads)
        # A link substituted inside the same call is refused at the next use, never served.
        moved = self.pdf.with_name('moved.pdf')
        self.pdf.rename(moved)
        self.pdf.symlink_to(moved)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            load_acquisition(self.root, self.record['datasheets'], bindings, reads)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            _read_blob(self.root, self.record['artifact'], reads)

    def test_root_alias_to_different_capture_misses(self):
        other = Path(self.enterContext(tempfile.TemporaryDirectory()))
        document = captured_fixture(self, other)
        named_fixture(other, document)
        target = other / self.record['artifact']['path']
        target.write_bytes(target.read_bytes().replace(b'Owned', b'Other'))
        alias = self.root.parent / (self.root.name + '-alias')
        alias.symlink_to(other)
        self.addCleanup(alias.unlink)
        reads = CallReads()
        _read_blob(self.root, self.record['artifact'], reads)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            _read_blob(alias, self.record['artifact'], reads)


if __name__ == '__main__':
    unittest.main()

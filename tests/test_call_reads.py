"""Per-call verified reads in parts-manifest qualification (AGENTEE-454).

One dependency_closure/qualify_manifest call reads and hashes each distinct capture
file once and hands every consumer those verified bytes; the next call rereads current
bytes. Fixtures are the original synthetic named-part captures of test_approved_parts.
"""
import collections
import contextlib
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
import test_datasheet_historical
import test_datasheet_selection
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


@contextlib.contextmanager
def substitute_after_first_read(target, change):
    """Rewrite target's file right after this call's first read of it; yield every read."""
    real, read, seen = os.path.realpath(target), CallReads.read, []

    def substitute(reads, path, sha256=None):
        result = read(reads, path, sha256)
        if os.path.realpath(path) == real:
            seen.append(result[0])
            if len(seen) == 1:
                Path(real).write_bytes(change(result[0]))
        return result

    with mock.patch.object(CallReads, 'read', substitute):
        yield seen


class SelectedSourceReadOnceTests(unittest.TestCase):
    """check_binding/_selected_source/_receipt consume the call's verified bytes (H1)."""

    def setUp(self):
        self.fixture = test_datasheet_selection.SourceSelectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.install(self.fixture.prepare())
        self.root, self.document = self.fixture.root, self.fixture.parts
        self.pdf = self.root / self.fixture.artifact['path']
        self.decision = self.root / self.fixture.ref['path']

    def test_selected_pdf_and_decision_read_once_per_call(self):
        counter = ReadCounter(self)
        closure = dependency_closure(self.document, self.root)
        self.assertEqual(closure[self.fixture.artifact['path']], self.fixture.artifact['sha256'])
        for path in (self.pdf, self.decision):
            with self.subTest(path=path.name):
                self.assertEqual(counter.reads[os.path.realpath(path)], 1)
        qualify_manifest(self.document, self.root)
        for path in (self.pdf, self.decision):
            with self.subTest(qualify=path.name):
                self.assertEqual(counter.reads[os.path.realpath(path)], 2)

    def test_substitution_after_verified_read_is_not_consumed(self):
        cases = ((self.pdf, lambda raw: raw.replace(b'Owned', b'Wrong')),
                 (self.decision, lambda raw: raw.replace(b'general-family', b'general-famile')))
        for path, change in cases:
            original = path.read_bytes()
            with self.subTest(path=path.name):
                with substitute_after_first_read(path, change) as seen:
                    result = qualify_manifest(self.document, self.root)
                    self.assertEqual(result['dependencies'][self.fixture.artifact['path']],
                                     self.fixture.artifact['sha256'])
                    self.assertTrue(seen)
                    self.assertTrue(all(raw == original for raw in seen))
                self.assertNotEqual(path.read_bytes(), original)
                # The next call rereads and refuses the substituted bytes.
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    dependency_closure(self.document, self.root)
            path.write_bytes(original)
            dependency_closure(self.document, self.root)


class HistoricalNoteReadOnceTests(unittest.TestCase):
    """historical_source hashes and parses one read of the note, through the call (H2)."""

    def setUp(self):
        self.fixture = test_datasheet_historical.HistoricalImportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.produce()
        self.root = self.fixture.root
        self.note = self.root / 'import/source-note.json'
        manifest = self.root / 'import/manifest.json'
        self.locator = dict(path='import/manifest.json', sha256=digest(manifest))
        self.note_pin = dict(path='import/source-note.json', sha256=digest(self.note))

    def test_note_read_once_and_parsed_from_verified_bytes(self):
        counter = ReadCounter(self)
        texts, read_text = collections.Counter(), Path.read_text

        def count_text(path, *args, **kwargs):
            texts[os.path.realpath(path)] += 1
            return read_text(path, *args, **kwargs)

        self.enterContext(mock.patch.object(Path, 'read_text', count_text))
        reads = CallReads()
        _, dependencies = load_acquisition(self.root, self.locator, self.fixture.bindings, reads)
        self.assertIn(self.note_pin['path'], dependencies)
        self.assertEqual(_read_blob(self.root, self.note_pin, reads), self.note.read_bytes())
        real = os.path.realpath(self.note)
        # One read by the replay; the second is this test's own comparison read above.
        self.assertEqual((counter.reads[real], texts[real]), (2, 0))

    def test_note_substitution_within_call_and_next_call_refuses(self):
        original = self.note.read_bytes()
        reads = CallReads()
        with substitute_after_first_read(self.note, lambda raw: raw.replace(b'2026', b'2025')) as seen:
            load_acquisition(self.root, self.locator, self.fixture.bindings, reads)
            self.assertEqual(_read_blob(self.root, self.note_pin, reads), original)
        self.assertEqual(seen, [original, original])
        with self.assertRaisesRegex(ValueError, 'historical source note hash mismatch'):
            load_acquisition(self.root, self.locator, self.fixture.bindings, CallReads())
        self.note.write_bytes(original)
        flip(self.note)
        with self.assertRaisesRegex(ValueError, 'historical source note hash mismatch'):
            load_acquisition(self.root, self.locator, self.fixture.bindings)


class CatalogSnapshotReadOnceTests(unittest.TestCase):
    """Generic selection consumes the snapshot bytes the closure verified (H3)."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.document = captured_fixture(self, self.root)
        self.manifest = self.root / 'snapshot/manifest.json'
        self.database = self.root / 'snapshot/catalog.sqlite3'

    def test_snapshot_read_once_per_qualification(self):
        counter = ReadCounter(self)
        qualify_manifest(self.document, self.root)
        for path in (self.manifest, self.database):
            with self.subTest(path=path.name):
                self.assertEqual(counter.reads[os.path.realpath(path)], 1)
        qualify_manifest(self.document, self.root)
        for path in (self.manifest, self.database):
            with self.subTest(second_call=path.name):
                self.assertEqual(counter.reads[os.path.realpath(path)], 2)

    def test_snapshot_substitution_after_verified_read(self):
        cases = ((self.manifest, lambda raw: raw + b' '),
                 (self.database, lambda raw: raw[:-1] + (b'A' if raw[-1:] != b'A' else b'B')))
        for path, change in cases:
            original = path.read_bytes()
            with self.subTest(path=path.name):
                with substitute_after_first_read(path, change) as seen:
                    result = qualify_manifest(self.document, self.root)
                    self.assertEqual(result['selection']['outcome'], 'pass')
                    self.assertTrue(len(seen) >= 2 and all(raw == original for raw in seen))
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    qualify_manifest(self.document, self.root)
            path.write_bytes(original)
            qualify_manifest(self.document, self.root)

    def test_snapshot_link_substituted_inside_call_refused(self):
        moved = self.manifest.with_name('moved.json')
        swapped = []
        real = os.path.realpath(self.manifest)
        read = CallReads.read

        def relink(reads, path, sha256=None):
            result = read(reads, path, sha256)
            if os.path.realpath(path) == real and not swapped:
                self.manifest.rename(moved)
                self.manifest.symlink_to(moved)
                swapped.append(True)
            return result

        with mock.patch.object(CallReads, 'read', relink), self.assertRaisesRegex(ValueError, 'symlink'):
            qualify_manifest(self.document, self.root)
        self.assertTrue(swapped)


if __name__ == '__main__':
    unittest.main()

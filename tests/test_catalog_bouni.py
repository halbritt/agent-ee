"""Synthetic controls for the offline bouni split inspection; no feed bytes are used."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from agent_ee.catalog_bouni import (BouniRefusal, SCHEMA, inspect_split, lcsc_pdf_url,
                                    passive_boundary, validate_manifest)


class SplitTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name) / 'parts'
        self.root.mkdir()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_STORED) as archive:
            archive.writestr('parts.db', bytes(range(256)) * 40)
        data = buffer.getvalue()
        cut = [0, len(data) // 3, 2 * len(data) // 3, len(data)]
        self.chunks = [data[a:b] for a, b in zip(cut, cut[1:])]
        self.manifest = {'schema': SCHEMA, 'archive': 'parts.db.zip', 'member': 'parts.db', 'parts': []}
        for n, chunk in enumerate(self.chunks, 1):
            name = f'parts.db.zip.{n:03}'
            (self.root / name).write_bytes(chunk)
            self.manifest['parts'].append({'path': name, 'size_bytes': len(chunk),
                                           'sha256': hashlib.sha256(chunk).hexdigest()})

    def refuses(self, reason, manifest=None):
        with self.assertRaises(BouniRefusal) as ctx:
            inspect_split(manifest or self.manifest, self.root)
        self.assertEqual(ctx.exception.reason, reason)

    def test_accepts_valid_layout(self):
        report = inspect_split(self.manifest, self.root)
        self.assertEqual(report['part_count'], 3)
        self.assertEqual(report['parts'], ['parts.db.zip.001', 'parts.db.zip.002', 'parts.db.zip.003'])
        self.assertEqual(report['member_size_bytes'], 10240)

    def test_missing_part(self):
        (self.root / 'parts.db.zip.002').unlink()
        self.refuses('missing_part')

    def test_gap_in_manifest_numbering(self):
        self.manifest['parts'].pop(1)
        self.refuses('missing_part')

    def test_reordered_manifest(self):
        self.manifest['parts'][0], self.manifest['parts'][1] = self.manifest['parts'][1], self.manifest['parts'][0]
        self.refuses('reordered_parts')

    def test_duplicate_entry(self):
        self.manifest['parts'][2] = dict(self.manifest['parts'][1])
        self.refuses('duplicate_part')

    def test_checksum_mismatch(self):
        path = self.root / 'parts.db.zip.002'
        data = bytearray(path.read_bytes())
        data[5] ^= 1
        path.write_bytes(bytes(data))
        self.refuses('checksum_mismatch')

    def test_size_mismatch_truncated_and_extended(self):
        path = self.root / 'parts.db.zip.003'
        path.write_bytes(path.read_bytes()[:-1])
        self.refuses('size_mismatch')
        path.write_bytes(self.chunks[2] + b'x')
        self.refuses('size_mismatch')

    def test_truncated_archive_with_consistent_manifest(self):
        short = self.chunks[2][:-10]
        (self.root / 'parts.db.zip.003').write_bytes(short)
        self.manifest['parts'][2].update(size_bytes=len(short), sha256=hashlib.sha256(short).hexdigest())
        self.refuses('archive_truncated')

    def repin(self, data):
        for stale in self.root.iterdir():
            stale.unlink()
        (self.root / 'parts.db.zip.001').write_bytes(data)
        self.manifest['parts'] = [{'path': 'parts.db.zip.001', 'size_bytes': len(data),
                                   'sha256': hashlib.sha256(data).hexdigest()}]

    def test_unreadable_archives_with_consistent_manifest(self):
        original = b''.join(self.chunks)
        corrupt = bytearray(original)
        corrupt[38] ^= 1
        bad_header = bytearray(original)
        bad_header[:4] = b'BAD!'
        cases = [('bad_crc', bytes(corrupt), 'archive_member_corrupt'),
                 ('bad_local_header', bytes(bad_header), 'archive_member_corrupt'),
                 ('truncated_prefix', original[original.index(b'PK\x01\x02'):], 'archive_truncated')]
        for label, data, reason in cases:
            with self.subTest(label=label):
                self.repin(data)
                self.refuses(reason)

    def test_oversized_member(self):
        with self.assertRaises(BouniRefusal) as ctx:
            inspect_split(self.manifest, self.root, max_member_bytes=10239)
        self.assertEqual(ctx.exception.reason, 'archive_member_oversized')
        self.assertEqual(inspect_split(self.manifest, self.root, max_member_bytes=10240)['member_size_bytes'], 10240)

    def test_unlisted_part_on_disk(self):
        (self.root / 'parts.db.zip.004').write_bytes(b'extra')
        self.refuses('unlisted_part')

    def test_bad_names_and_symlink(self):
        self.manifest['parts'][0]['path'] = 'other.zip.001'
        self.refuses('bad_part_name')
        self.setUp()
        link = self.root / 'parts.db.zip.001'
        link.unlink()
        link.symlink_to(self.root / 'parts.db.zip.002')
        self.refuses('part_not_file')

    def test_member_mismatch(self):
        self.manifest['member'] = 'other.db'
        self.refuses('archive_member_mismatch')

    def test_malformed_manifests(self):
        for bad in (None, {}, {**self.manifest, 'extra': 1}, {**self.manifest, 'parts': []},
                    {**self.manifest, 'schema': 'x'}):
            with self.assertRaises(BouniRefusal):
                validate_manifest(bad)
        part = dict(self.manifest['parts'][0], sha256='ABC')
        self.refuses('manifest_malformed', {**self.manifest, 'parts': [part]})


class PdfUrlTests(unittest.TestCase):
    def doc(self, **result):
        return json.dumps({'result': {'productCode': 'C5', **result}}).encode()

    def test_reads_recorded_url(self):
        self.assertEqual(lcsc_pdf_url(self.doc(pdfUrl=' https://example.invalid/a.pdf '), 'C5'),
                         'https://example.invalid/a.pdf')

    def test_absent_blank_null_and_other_part(self):
        self.assertIsNone(lcsc_pdf_url(self.doc(), 'C5'))
        self.assertIsNone(lcsc_pdf_url(self.doc(pdfUrl=''), 'C5'))
        self.assertIsNone(lcsc_pdf_url(self.doc(pdfUrl=None), 'C5'))
        self.assertIsNone(lcsc_pdf_url(self.doc(pdfUrl='https://example.invalid/a.pdf'), 'C6'))

    def test_refusals(self):
        for raw, reason in ((self.doc(pdfUrl=3), 'pdf_url_invalid'), (b'not json', 'response_malformed'),
                            (b'{"result":{"result":1}}', 'response_malformed'),
                            (b'{"result":{"productCode":"C5","productCode":"C5"}}', 'response_malformed')):
            with self.assertRaises(BouniRefusal) as ctx:
                lcsc_pdf_url(raw, 'C5')
            self.assertEqual(ctx.exception.reason, reason)


class BoundaryTests(unittest.TestCase):
    def test_no_default(self):
        with self.assertRaises(BouniRefusal) as ctx:
            passive_boundary()
        self.assertEqual(ctx.exception.reason, 'boundary_not_supplied')

    def test_explicit_values(self):
        self.assertEqual(passive_boundary(['resistor', 'capacitor']), ('capacitor', 'resistor'))
        for bad in ('resistor', [], ['diode'], ['resistor', 'resistor']):
            with self.assertRaises(BouniRefusal) as ctx:
                passive_boundary(bad)
            self.assertEqual(ctx.exception.reason, 'boundary_invalid')


if __name__ == '__main__':
    unittest.main()

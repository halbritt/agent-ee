"""Synthetic controls for the offline bouni split inspection; no feed bytes are used."""
import hashlib
import io
import json
import random
import signal
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
import zlib

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


class FullMemberIntegrity(unittest.TestCase):
    """Reviewer regressions (ee253b H1, H2): integrity comes from the raw span, not ZipExtFile."""

    def make_archive(self, compression):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', compression) as z:
            z.writestr('parts.db', b'abcdef' * 100)
        return bytearray(out.getvalue())

    def inspect(self, data, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'parts'
            root.mkdir()
            name = 'parts.zip.001'
            (root / name).write_bytes(data)
            manifest = dict(schema=SCHEMA, archive='parts.zip', member='parts.db',
                            parts=[dict(path=name, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())])
            return inspect_split(manifest, root, **kwargs)

    def assert_refuses(self, data, reason=None, **kwargs):
        with self.assertRaises(BouniRefusal) as ctx:
            self.inspect(bytes(data), **kwargs)
        if reason:
            self.assertEqual(ctx.exception.reason, reason)

    def test_hidden_corruption_and_size_limit_bypass(self):
        data = self.make_archive(zipfile.ZIP_STORED)
        directory = data.index(b'PK\x01\x02')
        struct.pack_into('<I', data, directory + 16, zlib.crc32(b'a'))
        struct.pack_into('<I', data, directory + 24, 1)
        data[39] ^= 1
        self.assert_refuses(bytes(data), max_member_bytes=1)
        self.assert_refuses(bytes(data), 'archive_member_header_mismatch')

    def test_matching_headers_hide_actual_deflate_size(self):
        data = self.make_archive(zipfile.ZIP_DEFLATED)
        directory = data.index(b'PK\x01\x02')
        for crc_offset, size_offset in [(14, 22), (directory + 16, directory + 24)]:
            struct.pack_into('<I', data, crc_offset, zlib.crc32(b'a'))
            struct.pack_into('<I', data, size_offset, 1)
        decoder = zlib.decompressobj(-15)
        self.assertEqual(len(decoder.decompress(bytes(data[38:directory]))), 600)
        self.assertTrue(decoder.eof)
        self.assert_refuses(bytes(data), max_member_bytes=1)
        self.assert_refuses(bytes(data), 'archive_member_oversized', max_member_bytes=1)
        self.assert_refuses(bytes(data), 'archive_member_corrupt')

    def test_truncated_deflate_stream_must_refuse(self):
        data = self.make_archive(zipfile.ZIP_DEFLATED)
        directory = data.index(b'PK\x01\x02')
        compressed = struct.unpack_from('<I', data, directory + 20)[0]
        truncated = data[:directory - 1] + data[directory:]
        struct.pack_into('<I', truncated, 18, compressed - 1)
        struct.pack_into('<I', truncated, directory - 1 + 20, compressed - 1)
        eocd = truncated.index(b'PK\x05\x06')
        struct.pack_into('<I', truncated, eocd + 16, directory - 1)
        decoder = zlib.decompressobj(-15)
        self.assertEqual(len(decoder.decompress(bytes(truncated[38:directory - 1]))), 600)
        self.assertFalse(decoder.eof)
        self.assert_refuses(bytes(truncated))
        self.assert_refuses(bytes(truncated), 'archive_member_corrupt')

    def test_valid_stored_deflate_empty_and_zip64_accept(self):
        self.assertEqual(self.inspect(bytes(self.make_archive(zipfile.ZIP_STORED)),
                                      max_member_bytes=600)['member_size_bytes'], 600)
        self.assertEqual(self.inspect(bytes(self.make_archive(zipfile.ZIP_DEFLATED)),
                                      max_member_bytes=600)['member_size_bytes'], 600)
        empty = io.BytesIO()
        with zipfile.ZipFile(empty, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('parts.db', b'')
        self.assertEqual(self.inspect(empty.getvalue(), max_member_bytes=0)['member_size_bytes'], 0)
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            forced = io.BytesIO()
            with zipfile.ZipFile(forced, 'w', compression) as z:
                with z.open('parts.db', 'w', force_zip64=True) as member:
                    member.write(b'xyz' * 1000)
            data = forced.getvalue()
            self.assertEqual(struct.unpack_from('<I', data, 22)[0], 0xFFFFFFFF)
            self.assertEqual(self.inspect(data)['member_size_bytes'], 3000)

    def test_deflate_data_after_end_of_stream(self):
        data = self.make_archive(zipfile.ZIP_DEFLATED)
        directory = data.index(b'PK\x01\x02')
        compressed = struct.unpack_from('<I', data, directory + 20)[0]
        padded = data[:directory] + b'\x00' + data[directory:]
        struct.pack_into('<I', padded, 18, compressed + 1)
        struct.pack_into('<I', padded, directory + 1 + 20, compressed + 1)
        eocd = padded.index(b'PK\x05\x06')
        struct.pack_into('<I', padded, eocd + 16, directory + 1)
        self.assert_refuses(padded, 'archive_member_corrupt')

    def deflate_with_trailing(self, payload, junk):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('parts.db', payload)
        data = bytearray(out.getvalue())
        directory = data.index(b'PK\x01\x02')
        compressed = struct.unpack_from('<I', data, directory + 20)[0]
        data = data[:directory] + junk + data[directory:]
        struct.pack_into('<I', data, 18, compressed + len(junk))
        struct.pack_into('<I', data, directory + len(junk) + 20, compressed + len(junk))
        eocd = data.index(b'PK\x05\x06')
        struct.pack_into('<I', data, eocd + 16, directory + len(junk))
        return bytes(data)

    def assert_refuses_within(self, seconds, data, reason):
        def expired(signum, frame):
            raise AssertionError(f'inspect_split did not finish within {seconds} s')
        previous = signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            self.assert_refuses(data, reason)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    def test_trailing_data_at_output_chunk_boundary_refuses_promptly(self):
        # Review ee253c H1: at a 1 MiB output boundary zlib reports EOF while the
        # suffix stays in unconsumed_tail; feeding it back looped forever.
        data = self.deflate_with_trailing(b'a' * (2 * 1024**2), b'junk')
        self.assert_refuses_within(10, data, 'archive_member_corrupt')

    def test_trailing_data_around_output_boundaries(self):
        mib = 1024**2
        for size in (mib - 1, mib, mib + 1, 2 * mib - 1, 2 * mib + 1, 3 * mib):
            for junk in (b'\x00', b'junk', b'\x03' * 4096):
                with self.subTest(size=size, junk=len(junk)):
                    self.assert_refuses_within(10, self.deflate_with_trailing(b'a' * size, junk),
                                               'archive_member_corrupt')
            with self.subTest(size=size, junk=0):
                self.assertEqual(self.inspect(self.deflate_with_trailing(b'a' * size, b''))['member_size_bytes'],
                                 size)

    def test_trailing_data_after_multi_block_compressed_span(self):
        # Incompressible payload: the compressed span crosses read-chunk boundaries.
        payload = random.Random(253).randbytes(1024**2 + 4096)
        self.assertEqual(self.inspect(self.deflate_with_trailing(payload, b''))['member_size_bytes'],
                         len(payload))
        for junk in (b'junk', b'\x00' * (1024**2 + 7)):
            with self.subTest(junk=len(junk)):
                self.assert_refuses_within(10, self.deflate_with_trailing(payload, junk), 'archive_member_corrupt')

    def test_unaccounted_bytes_before_directory(self):
        data = self.make_archive(zipfile.ZIP_STORED)
        directory = data.index(b'PK\x01\x02')
        gap = data[:directory] + b'hidden' + data[directory:]
        eocd = gap.index(b'PK\x05\x06')
        struct.pack_into('<I', gap, eocd + 16, directory + 6)
        self.assert_refuses(gap, 'archive_member_bounds')

    def test_local_central_disagreement(self):
        base = self.make_archive(zipfile.ZIP_DEFLATED)
        directory = base.index(b'PK\x01\x02')
        crc = bytearray(base)
        struct.pack_into('<I', crc, 14, 0)
        method = bytearray(base)
        struct.pack_into('<H', method, directory + 10, zipfile.ZIP_STORED)
        name = bytearray(base)
        name[30] = ord('q')
        flags = bytearray(base)
        struct.pack_into('<H', flags, 6, 0x800)
        for label, data in (('crc', crc), ('method', method), ('name', name), ('flags', flags)):
            with self.subTest(label=label):
                self.assert_refuses(data, 'archive_member_header_mismatch')

    def test_unsupported_methods_encryption_and_descriptor(self):
        for compression in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
            with self.subTest(compression=compression):
                self.assert_refuses(self.make_archive(compression), 'archive_member_unsupported')
        encrypted = self.make_archive(zipfile.ZIP_STORED)
        directory = encrypted.index(b'PK\x01\x02')
        for offset in (6, directory + 8):
            struct.pack_into('<H', encrypted, offset, 1)
        self.assert_refuses(encrypted, 'archive_member_unsupported')

        class Unseekable(io.RawIOBase):
            def __init__(self):
                self.buffer = bytearray()

            def writable(self):
                return True

            def write(self, data):
                self.buffer += data
                return len(data)
        stream = Unseekable()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('parts.db', b'abcdef' * 100)
        data = bytes(stream.buffer)
        self.assertTrue(struct.unpack_from('<H', data, 6)[0] & 0x8)
        self.assert_refuses(data, 'archive_member_unsupported')


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

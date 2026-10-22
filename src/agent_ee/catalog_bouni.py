"""Offline inspection of the bouni split-archive layout, LCSC ``pdfUrl`` reading and the passive boundary.

Nothing here fetches data. The layout modelled below is an owner-authored
description (``agent-ee.catalog-bouni-split/1``) of numbered parts
``NAME.001 ... NAME.NNN`` of one ZIP, checked against an explicit manifest.
It is qualified against synthetic archives only, never against the real feed.
Inspection is read-only: it verifies and reports, and it never opens the joined
archive's contents as a catalog.
"""
import hashlib
import json
from pathlib import Path
import re
import struct
import tempfile
import zipfile
import zlib

SCHEMA = 'agent-ee.catalog-bouni-split/1'
MAX_PARTS = 999
MAX_JOINED_BYTES = 8 * 1024**3
MAX_MEMBER_BYTES = 8 * 1024**3
MAX_MEMBERS = 1
LOCAL_HEADER = struct.Struct('<4s5H3I2H')
ZIP64_MARK = 0xFFFFFFFF
CHUNK = 1024**2
UNSUPPORTED_FLAGS = 0x1 | 0x8 | 0x40
HASH_RE = re.compile(r'[0-9a-f]{64}')
NAME_RE = re.compile(r'([A-Za-z0-9][A-Za-z0-9._-]*\.zip)\.([0-9]{3})')
# Families whose identity part_selection establishes from captured value, tolerance,
# package and ratings (part_selection.VALUE_ATTRIBUTES); see docs/decisions.md.
KNOWN_FAMILIES = ('resistor', 'capacitor', 'inductor')
REASONS = ('manifest_malformed', 'no_parts', 'too_many_parts', 'bad_part_name', 'duplicate_part',
           'missing_part', 'reordered_parts', 'unlisted_part', 'part_not_file', 'size_mismatch',
           'checksum_mismatch', 'archive_name_mismatch', 'joined_size_mismatch', 'archive_truncated',
           'archive_malformed', 'archive_member_mismatch', 'archive_member_corrupt',
           'archive_member_bounds', 'archive_member_oversized',
           'archive_member_header_mismatch', 'archive_member_unsupported', 'boundary_not_supplied',
           'boundary_invalid', 'response_malformed', 'pdf_url_invalid')


class BouniRefusal(ValueError):
    """Typed refusal; ``reason`` is one of REASONS."""

    def __init__(self, reason, detail=''):
        assert reason in REASONS
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason


def _strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError(f'duplicate JSON key {key!r}')
            out[key] = value
        return out

    def constant(value):
        raise ValueError(f'non-JSON numeric constant {value}')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def validate_manifest(manifest):
    """Return the parts in listed order after shape and ordering checks."""
    if (not isinstance(manifest, dict) or set(manifest) != {'schema', 'archive', 'member', 'parts'}
            or manifest['schema'] != SCHEMA or not isinstance(manifest['member'], str)
            or not manifest['member'] or not isinstance(manifest['archive'], str)
            or not isinstance(manifest['parts'], list)):
        raise BouniRefusal('manifest_malformed', 'expected schema, archive, member, parts')
    parts = manifest['parts']
    if not parts:
        raise BouniRefusal('no_parts')
    if len(parts) > MAX_PARTS:
        raise BouniRefusal('too_many_parts')
    seen, numbers = set(), []
    for part in parts:
        if (not isinstance(part, dict) or set(part) != {'path', 'size_bytes', 'sha256'}
                or not isinstance(part['path'], str) or type(part['size_bytes']) is not int
                or part['size_bytes'] <= 0 or not isinstance(part['sha256'], str)
                or not HASH_RE.fullmatch(part['sha256'])):
            raise BouniRefusal('manifest_malformed', 'part needs path, positive size_bytes, sha256')
        match = NAME_RE.fullmatch(part['path'])
        if not match or match[1] != manifest['archive']:
            raise BouniRefusal('bad_part_name', part['path'])
        if part['path'] in seen:
            raise BouniRefusal('duplicate_part', part['path'])
        seen.add(part['path'])
        numbers.append(int(match[2]))
    if sorted(numbers) != list(range(1, len(parts) + 1)):
        raise BouniRefusal('missing_part', 'part numbers must be contiguous from 001')
    if numbers != sorted(numbers):
        raise BouniRefusal('reordered_parts', 'manifest lists parts out of numeric order')
    return parts


def _local_sizes(info, extra, crc, compressed, size):
    """Resolve the local header's sizes, reading ZIP64 values from its extra field when marked."""
    wanted = [i for i, value in enumerate((size, compressed)) if value == ZIP64_MARK]
    values = [size, compressed]
    if wanted:
        while len(extra) >= 4:
            tag, length = struct.unpack_from('<2H', extra)
            if tag == 1:
                body = extra[4:4 + length]
                if len(body) < 8 * len(wanted):
                    break
                for n, index in enumerate(wanted):
                    values[index] = struct.unpack_from('<Q', body, 8 * n)[0]
                return crc, values[1], values[0]
            extra = extra[4 + length:]
        raise BouniRefusal('archive_member_header_mismatch', f'{info.filename}: missing ZIP64 local sizes')
    return crc, compressed, size


def _member_bytes(joined, start, length):
    """Yield exactly ``length`` bytes of the raw span at ``start`` or refuse a short read."""
    joined.seek(start)
    remaining = length
    while remaining:
        block = joined.read(min(CHUNK, remaining))
        if not block:
            raise BouniRefusal('archive_member_bounds', 'compressed span past end of archive')
        remaining -= len(block)
        yield block


def _verify_payload(info, blocks, limit):
    """Decode the raw span and return (actual size, CRC-32) of its complete output under ``limit``."""
    size, crc = 0, 0

    def take(out):
        nonlocal size, crc
        size += len(out)
        if size > limit:
            raise BouniRefusal('archive_member_oversized', f'{info.filename}: actual output exceeds the limit')
        if size > info.file_size:
            raise BouniRefusal('archive_member_corrupt', f'{info.filename}: output exceeds the declared size')
        crc = zlib.crc32(out, crc)
    if info.compress_type == zipfile.ZIP_STORED:
        for block in blocks:
            take(block)
        return size, crc
    decoder = zlib.decompressobj(-15)
    try:
        for block in blocks:
            # Every pass consumes input or yields output, so the loop is bounded
            # by the span length and the output limit. Once the stream ends, any
            # bytes left in the span are trailing data and are never fed back.
            while block:
                if decoder.eof:
                    raise BouniRefusal('archive_member_corrupt', f'{info.filename}: data after end of stream')
                out = decoder.decompress(block, CHUNK)
                tail = decoder.unconsumed_tail
                if not out and len(tail) >= len(block):
                    raise BouniRefusal('archive_member_corrupt', f'{info.filename}: DEFLATE decoder made no progress')
                take(out)
                block = tail
            if decoder.unused_data:
                raise BouniRefusal('archive_member_corrupt', f'{info.filename}: data after end of stream')
        while not decoder.eof and (out := decoder.decompress(b'', CHUNK)):
            take(out)
    except zlib.error as exc:
        raise BouniRefusal('archive_member_corrupt', f'{info.filename}: {exc}') from exc
    if not decoder.eof:
        raise BouniRefusal('archive_member_corrupt', f'{info.filename}: DEFLATE stream has no end')
    if decoder.unused_data:
        raise BouniRefusal('archive_member_corrupt', f'{info.filename}: data after end of stream')
    return size, crc


def _verify_members(archive, joined, entries, max_member_bytes):
    """Verify each member from its raw compressed span; return the actual member sizes.

    The local header must agree with the central directory (signature, flags,
    method, name, CRC-32 and both sizes). Only STORED and DEFLATE without
    encryption or data descriptors are accepted. The members must tile the archive
    body exactly. Exactly the compressed span is read: STORED must be the whole
    span, DEFLATE must reach its end-of-stream marker with nothing left over. The
    size limit and CRC-32 apply to the actual output, never to declared sizes.
    """
    spans = []
    for info in entries:
        if info.file_size > max_member_bytes:
            raise BouniRefusal('archive_member_oversized', info.filename)
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise BouniRefusal('archive_member_unsupported', f'{info.filename}: method {info.compress_type}')
        if info.flag_bits & UNSUPPORTED_FLAGS:
            raise BouniRefusal('archive_member_unsupported',
                               f'{info.filename}: encryption or data descriptor (flags {info.flag_bits:#x})')
        if info.header_offset < 0:
            raise BouniRefusal('archive_truncated', f'{info.filename}: archive start is missing')
        joined.seek(info.header_offset)
        raw = joined.read(LOCAL_HEADER.size)
        if len(raw) != LOCAL_HEADER.size:
            raise BouniRefusal('archive_member_bounds', f'{info.filename}: local header past end of archive')
        signature, _, flags, method, _, _, crc, compressed, size, name_len, extra_len = LOCAL_HEADER.unpack(raw)
        if signature != zipfile.stringFileHeader:
            raise BouniRefusal('archive_member_corrupt', f'{info.filename}: bad local header signature')
        start = info.header_offset + LOCAL_HEADER.size + name_len + extra_len
        end = start + info.compress_size
        if end > archive.start_dir:
            raise BouniRefusal('archive_member_bounds', f'{info.filename}: data outside the archive body')
        name = joined.read(name_len)
        extra = joined.read(extra_len)
        try:
            local_name = name.decode('utf-8' if flags & 0x800 else 'cp437')
        except UnicodeDecodeError:
            local_name = None
        local = _local_sizes(info, extra, crc, compressed, size)
        if (flags != info.flag_bits or method != info.compress_type or local_name != info.orig_filename
                or local != (info.CRC, info.compress_size, info.file_size)):
            raise BouniRefusal('archive_member_header_mismatch',
                               f'{info.filename}: local header disagrees with the central directory')
        if info.compress_type == zipfile.ZIP_STORED and info.compress_size != info.file_size:
            raise BouniRefusal('archive_member_header_mismatch', f'{info.filename}: STORED sizes differ')
        spans.append((info.header_offset, start, end, info))
    spans.sort(key=lambda span: span[0])
    position = 0
    for offset, _, end, info in spans:
        if offset != position:
            raise BouniRefusal('archive_member_bounds',
                               f'{info.filename}: overlaps or leaves a gap before the member')
        position = end
    if position != archive.start_dir:
        raise BouniRefusal('archive_member_bounds', 'unaccounted bytes before the central directory')
    sizes = []
    for _, start, end, info in spans:
        size, crc = _verify_payload(info, _member_bytes(joined, start, end - start), max_member_bytes)
        if size != info.file_size:
            raise BouniRefusal('archive_member_corrupt', f'{info.filename}: size differs from directory')
        if crc != info.CRC:
            raise BouniRefusal('archive_member_corrupt', f'{info.filename}: CRC-32 mismatch')
        sizes.append(size)
    return sizes


def inspect_split(manifest, root, max_member_bytes=MAX_MEMBER_BYTES):
    """Verify every part under ``root`` and the joined ZIP's directory; return a report.

    Parts present on disk but absent from the manifest are refused. The joined
    archive is streamed into a private temporary file and discarded. Every
    member is verified from its raw compressed span (see ``_verify_members``),
    counting at most ``max_member_bytes`` of actual output; the bytes are never
    interpreted.
    """
    parts = validate_manifest(manifest)
    root = Path(root).resolve(strict=True)
    listed = {p['path'] for p in parts}
    for entry in sorted(root.iterdir()):
        if NAME_RE.fullmatch(entry.name) and entry.name not in listed:
            raise BouniRefusal('unlisted_part', entry.name)
    total = sum(p['size_bytes'] for p in parts)
    if total > MAX_JOINED_BYTES:
        raise BouniRefusal('joined_size_mismatch', 'joined archive exceeds the limit')
    with tempfile.TemporaryFile(dir=root.parent) as joined:
        for part in parts:
            path = root / part['path']
            if path.is_symlink() or not path.is_file():
                raise BouniRefusal('part_not_file' if path.exists() or path.is_symlink() else 'missing_part',
                                   part['path'])
            size, digest = 0, hashlib.sha256()
            with path.open('rb') as source:
                while block := source.read(min(1024**2, part['size_bytes'] + 1 - size)):
                    size += len(block)
                    if size > part['size_bytes']:
                        break
                    digest.update(block)
                    joined.write(block)
            if size != part['size_bytes']:
                raise BouniRefusal('size_mismatch', part['path'])
            if digest.hexdigest() != part['sha256']:
                raise BouniRefusal('checksum_mismatch', part['path'])
        joined.flush()
        if joined.tell() != total:
            raise BouniRefusal('joined_size_mismatch')
        try:
            with zipfile.ZipFile(joined) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_MEMBERS:
                    raise BouniRefusal('archive_member_mismatch', 'too many members')
                if [e.filename for e in entries] != [manifest['member']]:
                    raise BouniRefusal('archive_member_mismatch',
                                       'joined ZIP must hold exactly the declared member')
                sizes = _verify_members(archive, joined, entries, max_member_bytes)
        except (zipfile.BadZipFile, EOFError, OSError, ValueError) as exc:
            if isinstance(exc, BouniRefusal):
                raise
            raise BouniRefusal('archive_truncated', str(exc)) from exc
    return {'archive': manifest['archive'], 'member': manifest['member'], 'part_count': len(parts),
            'parts': [p['path'] for p in parts], 'joined_size_bytes': total,
            'member_size_bytes': sizes[0]}


def lcsc_pdf_url(raw, lcsc):
    """The ``pdfUrl`` of a retained LCSC product response, or None. Recorded, never fetched.

    The response shape (``result.productCode`` and ``result.pdfUrl``) is unqualified
    against live LCSC output. A mismatched ``productCode`` yields None.
    """
    try:
        doc = _strict_json(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise BouniRefusal('response_malformed', str(exc)) from exc
    result = doc.get('result') if isinstance(doc, dict) else None
    if not isinstance(result, dict) or not isinstance(result.get('productCode'), str):
        raise BouniRefusal('response_malformed', 'expected result.productCode')
    if result['productCode'] != lcsc:
        return None
    url = result.get('pdfUrl')
    if url is None:
        return None
    if not isinstance(url, str):
        raise BouniRefusal('pdf_url_invalid', 'pdfUrl must be a string or null')
    return url.strip() or None


def passive_boundary(families=None):
    """Validate the explicit family set treated as passive. There is no default.

    The boundary may only name families that ``part_selection`` resolves by
    nominal value with tolerance (``KNOWN_FAMILIES``); every other family keeps
    its identity rows. ``catalog_identity`` calls this at capture and at load.
    """
    if families is None:
        raise BouniRefusal('boundary_not_supplied', 'the passive family set must be explicit')
    if (isinstance(families, str) or not isinstance(families, (list, tuple, set, frozenset))
            or not families or any(f not in KNOWN_FAMILIES for f in families)
            or len(set(families)) != len(families)):
        raise BouniRefusal('boundary_invalid', f'expected a non-empty subset of {KNOWN_FAMILIES}')
    return tuple(sorted(families))

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
HASH_RE = re.compile(r'[0-9a-f]{64}')
NAME_RE = re.compile(r'([A-Za-z0-9][A-Za-z0-9._-]*\.zip)\.([0-9]{3})')
KNOWN_FAMILIES = ('resistor', 'capacitor', 'inductor')
REASONS = ('manifest_malformed', 'no_parts', 'too_many_parts', 'bad_part_name', 'duplicate_part',
           'missing_part', 'reordered_parts', 'unlisted_part', 'part_not_file', 'size_mismatch',
           'checksum_mismatch', 'archive_name_mismatch', 'joined_size_mismatch', 'archive_truncated',
           'archive_malformed', 'archive_member_mismatch', 'archive_member_corrupt',
           'archive_member_bounds', 'archive_member_oversized', 'boundary_not_supplied',
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


def _verify_members(archive, joined, entries, max_member_bytes):
    """Validate local headers, bounds, overlap and payload CRC of each member without keeping bytes."""
    spans = []
    for info in entries:
        if info.file_size > max_member_bytes:
            raise BouniRefusal('archive_member_oversized', info.filename)
        joined.seek(info.header_offset)
        raw = joined.read(LOCAL_HEADER.size)
        if len(raw) != LOCAL_HEADER.size:
            raise BouniRefusal('archive_member_bounds', f'{info.filename}: local header past end of archive')
        fields = LOCAL_HEADER.unpack(raw)
        if fields[0] != zipfile.stringFileHeader:
            raise BouniRefusal('archive_member_corrupt', f'{info.filename}: bad local header signature')
        start = info.header_offset + LOCAL_HEADER.size + fields[9] + fields[10]
        end = start + info.compress_size
        if info.header_offset < 0 or end > archive.start_dir:
            raise BouniRefusal('archive_member_bounds', f'{info.filename}: data outside the archive body')
        spans.append((info.header_offset, end, info.filename))
    spans.sort()
    for (_, end, _), (begin, _, name) in zip(spans, spans[1:]):
        if begin < end:
            raise BouniRefusal('archive_member_bounds', f'{name}: overlaps the previous member')
    for info in entries:
        counted = 0
        try:
            with archive.open(info) as member:
                while block := member.read(min(1024**2, max_member_bytes + 1 - counted)):
                    counted += len(block)
                    if counted > max_member_bytes:
                        raise BouniRefusal('archive_member_oversized', info.filename)
        except (zipfile.BadZipFile, EOFError, ValueError, RuntimeError, NotImplementedError,
                zlib.error) as exc:
            raise BouniRefusal('archive_member_corrupt', f'{info.filename}: {exc}') from exc
        if counted != info.file_size:
            raise BouniRefusal('archive_member_corrupt', f'{info.filename}: size differs from directory')


def inspect_split(manifest, root, max_member_bytes=MAX_MEMBER_BYTES):
    """Verify every part under ``root`` and the joined ZIP's directory; return a report.

    Parts present on disk but absent from the manifest are refused. The joined
    archive is streamed into a private temporary file and discarded. Every
    member's local header, bounds and CRC-32 are verified by streaming it into a
    byte counter (at most ``max_member_bytes``); the bytes are never interpreted.
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
                _verify_members(archive, joined, entries, max_member_bytes)
        except (zipfile.BadZipFile, EOFError, OSError, ValueError) as exc:
            if isinstance(exc, BouniRefusal):
                raise
            raise BouniRefusal('archive_truncated', str(exc)) from exc
    return {'archive': manifest['archive'], 'member': manifest['member'], 'part_count': len(parts),
            'parts': [p['path'] for p in parts], 'joined_size_bytes': total,
            'member_size_bytes': entries[0].file_size}


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

    Pending owner confirmation: the existing identity index excludes only
    resistor, capacitor and inductor, and this input is how a decision is supplied.
    """
    if families is None:
        raise BouniRefusal('boundary_not_supplied', 'the R/C/L boundary is an owner decision')
    if (isinstance(families, str) or not isinstance(families, (list, tuple, set, frozenset))
            or not families or any(f not in KNOWN_FAMILIES for f in families)
            or len(set(families)) != len(families)):
        raise BouniRefusal('boundary_invalid', f'expected a non-empty subset of {KNOWN_FAMILIES}')
    return tuple(sorted(families))

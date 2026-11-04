"""Host-side adapter for the single-member jlcparts split ZIP feed (ZIP64 or plain ZIP trailer)."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import re
from pathlib import Path
import sqlite3
import struct
import tempfile
import zipfile
import zlib

from .catalog import normalize_row, strict_json
from .storage import canonical, identity

FORMAT = 'jlcparts-v2-stocked-split-zip'
MAX_EXPANDED_BYTES = 8 * 1024**3
# Category names are upstream claims. Unsupported families are outside this adapter.
CATEGORY_MAP = {
    'Resistors': 'resistor', 'Capacitors': 'capacitor', 'Inductors': 'inductor', 'Inductors, Coils, Chokes': 'inductor',
    'Diodes': 'diode', 'Connectors': 'connector', 'LED Indication - Discrete': 'led',
    'Voltage Regulators - Linear, Low Drop Out (LDO) Regulators': 'regulator',
    'DC-DC Converters': 'switching_regulator',
}
COMPONENT_COLUMNS = ('lcsc', 'fetched_at', 'present', 'sync_seen', 'category', 'subcategory',
                     'mfr', 'package', 'joints', 'manufacturer', 'library_type', 'preferred',
                     'last_on_stock', 'description', 'datasheet', 'stock', 'price', 'attributes',
                     'rohs', 'eccn', 'assembly', 'assembly_process', 'assembly_mode',
                     'website_component_id', 'attrition')


def split_volumes(volumes, profile=FORMAT):
    ordered = sorted(volumes, key=lambda v: v['path'])
    expected = [f'cache.z{n:02}' for n in range(1, len(ordered))] + ['cache.zip']
    if len(ordered) < 2 or len(ordered) > 100 or [v['path'] for v in ordered] != expected:
        raise ValueError('expected contiguous cache.z01 ... cache.zip volumes')
    if any(v['format'] != profile for v in ordered):
        raise ValueError('mixed native and normalized catalog volumes')
    return ordered


def reassemble(volumes, root, destination, profile=FORMAT):
    """Verify every volume before interpreting ZIP metadata; translate disk offsets.

    Accepts the two single-entry split layouts jlcparts has produced: a ZIP64
    trailer (2026-09-14 snapshot) and a plain ZIP trailer (from 2026-11). In both,
    the central directory and trailer are on the last disk and the member starts
    on disk 0. The joined file is rewritten as one disk; zipfile handles the
    directory, compression, CRC and member validation.
    """
    volumes = split_volumes(volumes, profile)
    with destination.open('xb') as output:
        for volume in volumes:
            path = root / volume['path']
            if path.is_symlink() or not path.is_file():
                raise ValueError('volume must be a regular source file')
            size, hashed = 0, hashlib.sha256()
            with path.open('rb') as source:
                while block := source.read(min(1024**2, volume['size_bytes'] + 1 - size)):
                    size += len(block)
                    if size > volume['size_bytes']:
                        raise ValueError('volume size/hash mismatch')
                    output.write(block)
                    hashed.update(block)
            if size != volume['size_bytes'] or hashed.hexdigest() != volume['sha256']:
                raise ValueError('volume size/hash mismatch')
    base = sum(v['size_bytes'] for v in volumes[:-1])
    last = len(volumes) - 1
    with destination.open('r+b') as archive:
        if archive.seek(0, 2) < 98:
            raise ValueError('unsupported split ZIP trailer')
        archive.seek(-98, 2)
        trailer = bytearray(archive.read(98))
        if trailer[:4] != b'PK\x06\x06':
            # Plain ZIP trailer: every field fits 16/32 bits, so no ZIP64 record follows the data.
            end = struct.unpack('<4sHHHHIIH', trailer[76:])
            if (end != (b'PK\x05\x06', last, last, 1, 1, end[5], end[6], 0)
                    or 0xFFFFFFFF in end[5:7] or end[6] + end[5] + 22 != volumes[-1]['size_bytes']
                    or base + end[6] >= 0xFFFFFFFF):
                raise ValueError('unsupported split ZIP trailer')
            archive.seek(-22, 2)
            archive.write(struct.pack('<4sHHHHIIH', b'PK\x05\x06', 0, 0, 1, 1, end[5], base + end[6], 0))
            return
        end64 = struct.unpack('<4sQHHIIQQQQ', trailer[:56])
        locator = struct.unpack('<4sIQI', trailer[56:76])
        end = struct.unpack('<4sHHHHIIH', trailer[76:])
        if (end64[:2] != (b'PK\x06\x06', 44) or end64[4:8] != (last, last, 1, 1)
                or locator != (b'PK\x06\x07', last, end64[9] + end64[8], len(volumes))
                or end != (b'PK\x05\x06', last, last, 1, 1, end64[8], end64[9], 0)
                or end64[9] + end64[8] + 98 != volumes[-1]['size_bytes']):
            raise ValueError('unsupported split ZIP64 trailer')
        struct.pack_into('<II', trailer, 16, 0, 0)
        struct.pack_into('<Q', trailer, 48, base + end64[9])
        struct.pack_into('<IQI', trailer, 60, 0, base + locator[2], 1)
        struct.pack_into('<HH', trailer, 80, 0, 0)
        struct.pack_into('<I', trailer, 92, base + end64[9])
        archive.seek(-98, 2)
        archive.write(trailer)


def _member(archive):
    entries = archive.infolist()
    if len(entries) != 1:
        raise ValueError('expected one cache.sqlite3 member')
    entry = entries[0]
    mode = (entry.external_attr >> 16) & 0o170000
    if (entry.filename != 'cache.sqlite3' or entry.volume != 0 or entry.header_offset != 4
            or entry.flag_bits & 1 or mode not in (0, 0o100000)
            or not 0 < entry.file_size <= MAX_EXPANDED_BYTES):
        raise ValueError('unsupported or oversized native ZIP member')
    return entry


def _expand(archive_path, entry, sink):
    """Decode the member's raw compressed span through sink.

    zipfile's reader stops at the declared size, so it cannot see a stream that expands past it
    (review of 5704964d). This reads exactly the compressed span after the local header and
    requires the DEFLATE stream to end there, the output to be exactly the declared size and its
    CRC-32 to be the declared one; STORED data must be exactly that size."""
    if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ValueError('unsupported native ZIP compression')
    size, crc = 0, 0

    def take(out):
        nonlocal size, crc
        size += len(out)
        if size > entry.file_size:
            raise ValueError('native ZIP member expands past its declared size')
        if size > MAX_EXPANDED_BYTES:
            raise ValueError('expanded catalog exceeds capture limit')
        crc = zlib.crc32(out, crc)
        sink(out)

    with open(archive_path, 'rb') as joined:
        joined.seek(entry.header_offset)
        header = joined.read(30)
        if len(header) != 30 or header[:4] != b'PK\x03\x04':
            raise ValueError('native ZIP member has no local header')
        method, = struct.unpack_from('<H', header, 8)
        name_length, extra_length = struct.unpack_from('<HH', header, 26)
        if method != entry.compress_type or joined.read(name_length) != entry.orig_filename.encode():
            raise ValueError('native ZIP local header differs from the central directory')
        joined.seek(entry.header_offset + 30 + name_length + extra_length)
        decoder = zlib.decompressobj(-15) if entry.compress_type == zipfile.ZIP_DEFLATED else None
        remaining = entry.compress_size
        try:
            while remaining:
                block = joined.read(min(1024**2, remaining))
                if not block:
                    raise ValueError('native ZIP member runs past the end of the archive')
                remaining -= len(block)
                if decoder is None:
                    take(block)
                    continue
                while block:
                    if decoder.eof:
                        raise ValueError('native ZIP member has data after its end of stream')
                    out = decoder.decompress(block, 1024**2)
                    if not out and len(decoder.unconsumed_tail) >= len(block):
                        raise ValueError('native ZIP member stream makes no progress')
                    take(out)
                    block = decoder.unconsumed_tail
                if decoder.unused_data:
                    raise ValueError('native ZIP member has data after its end of stream')
            while decoder is not None and not decoder.eof and (out := decoder.decompress(b'', 1024**2)):
                take(out)
        except zlib.error as exc:
            raise ValueError(f'native ZIP member stream is corrupt: {exc}') from exc
    if decoder is not None and not decoder.eof:
        raise ValueError('native ZIP member stream has no end')
    if size != entry.file_size:
        raise ValueError('truncated native ZIP member')
    if crc != entry.CRC:
        raise ValueError('native ZIP member CRC-32 differs from the central directory')


def check_archive(volumes, root, work, profile=FORMAT):
    """Refuse volumes capture cannot read: everything capture checks before the database.

    Joins and rewrites the volumes as capture does (sizes and SHA-256 checked first), requires
    the single cache.sqlite3 member, decodes its whole stream (exact end, size and CRC-32)
    without keeping it, and requires an SQLite header at its start. The database's tables,
    columns and rows, and any derived index size, are checked by capture itself."""
    head = bytearray()

    def sink(out):
        if len(head) < 16:
            head.extend(out[:16 - len(head)])

    with tempfile.TemporaryDirectory(prefix='.catalog-layout-', dir=work) as stage:
        joined = Path(stage) / 'joined.zip'
        reassemble(volumes, Path(root), joined, profile)
        with zipfile.ZipFile(joined) as archive:
            entry = _member(archive)
        _expand(joined, entry, sink)
    if bytes(head) != b'SQLite format 3\x00':
        raise ValueError('native ZIP member is not an SQLite database')
    return {'member': entry.filename, 'member_bytes': entry.file_size}


def extract_database(archive_path, destination):
    with zipfile.ZipFile(archive_path) as archive:
        entry = _member(archive)
    with destination.open('xb') as output:
        _expand(archive_path, entry, output.write)


def check_table(connection, table, required):
    if connection.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchall() != [('table',)]:
        raise ValueError(f'native catalog requires {table} table')
    columns = connection.execute(f'PRAGMA table_xinfo({table})').fetchall()
    if tuple(c[1] for c in columns) != required or any(c[6] for c in columns):
        raise ValueError(f'missing or unsupported native {table} columns/order')


def attributes(text, category):
    original = strict_json(text)
    if not isinstance(original, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in original.items()):
        raise ValueError('native attributes must be a text mapping')
    mapped = {'upstream:' + k: v for k, v in original.items()}
    nominal = {'resistor': ('Resistance', 'resistance'), 'capacitor': ('Capacitance', 'capacitance'),
               'inductor': ('Inductance', 'inductance')}.get(category)
    if nominal and nominal[0] in original and 'Tolerance' in original:
        tolerance = original['Tolerance'].strip().removeprefix('±').removeprefix('+/-').strip()
        mapped[nominal[1]] = original[nominal[0]] + ' +/-' + tolerance
    for source, target in (('Power(Watts)', 'power'), ('Voltage Rated', 'voltage'),
                           ('Voltage - Rated', 'voltage'), ('Voltage-Supply(Max)', 'voltage'),
                           ('Voltage Rating', 'voltage'), ('Voltage Rating (Max)', 'voltage'),
                           ('Operating Temperature', 'temperature'),
                           ('Voltage Dropout (Max)', 'dropout'), ('Current - Quiescent (Iq)', 'quiescent_current')):
        if source in original:
            if target in mapped:
                # Conflicting aliases cannot establish a typed rating.
                mapped[target] = 'ambiguous upstream aliases'
            else:
                mapped[target] = original[source].replace('℃', 'degC').replace('~', ' .. ')
    for low, high, target in (('Voltage - Input (Min)', 'Voltage - Input (Max)', 'input_voltage'),
                              ('Voltage - Output (Min/Fixed)', 'Voltage - Output (Max)', 'output_voltage')):
        if low in original and high in original:
            mapped[target] = original[low] + ' .. ' + original[high]
    if category == 'switching_regulator':
        for source, target in (('Voltage - Input (Max)', 'input_voltage_max'),
                               ('Current - Output', 'output_current'),
                               ('Frequency - Switching', 'switching_frequency')):
            if source in original:
                mapped[target] = original[source].replace('~', ' .. ')
        rectifier = {'Yes': 'sync', 'No': 'async'}.get(original.get('Synchronous Rectifier'))
        if rectifier is not None:
            mapped['topology'] = rectifier
    return canonical(mapped).decode()


def price1(text):
    from .catalog import decimal_number, decimal_text
    if not isinstance(text, str):
        raise ValueError('native price must be tier text')
    starts, first = set(), None
    for tier in text.split(',') if text else []:
        match = re.fullmatch(r'([1-9][0-9]*)-([1-9][0-9]*)?:([^:,\s]+)', tier)
        if not match:
            raise ValueError('invalid native price tier')
        low = int(match[1])
        if low in starts or (match[2] is not None and int(match[2]) < low):
            raise ValueError('invalid native price range')
        starts.add(low)
        amount = decimal_number(match[3])
        if amount < 0:
            raise ValueError('negative native price')
        price = decimal_text(amount)
        if first is None or low < first[0]:
            first = low, price
    # price1 follows the upstream convention: per-unit price at its smallest MOQ.
    return '' if first is None else first[1]


def native_rows(path, manifest):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('PRAGMA query_only=ON')
        if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('corrupt native SQLite')
        check_table(connection, 'meta', ('key', 'value'))
        if connection.execute("SELECT value FROM meta WHERE key='format'").fetchall() != [('source-db-v2',)]:
            raise ValueError('unsupported native catalog format')
        check_table(connection, 'jlc_components', COMPONENT_COLUMNS)
        connection.row_factory = sqlite3.Row
        for index, native in enumerate(connection.execute('SELECT * FROM jlc_components ORDER BY lcsc'), 1):
            native = dict(native)
            category = CATEGORY_MAP.get(native['category']) or CATEGORY_MAP.get(native['subcategory'])
            if category is None:
                continue
            for key in ('lcsc', 'stock', 'preferred', 'present', 'fetched_at'):
                if type(native[key]) is not int:
                    raise ValueError(f'invalid native {key}')
            if native['present'] not in (0, 1) or native['fetched_at'] <= 0:
                raise ValueError('invalid native presence/date')
            if native['stock'] < 0:
                raise ValueError('negative native stock')
            if native['present'] == 0 or native['stock'] == 0:
                continue
            seen = datetime.fromtimestamp(native['fetched_at'], timezone.utc).date().isoformat()
            if manifest['stale_before'] is not None and seen < manifest['stale_before']:
                yield None
                continue
            if native['library_type'] not in ('base', 'expand'):
                raise ValueError('unknown native library type')
            raw = dict(lcsc=f'C{native["lcsc"]}', mfr=native['manufacturer'].strip(), mpn=native['mfr'].strip(),
                       package=native['package'].strip(), category=category, stock=str(native['stock']),
                       price1=price1(native['price']), is_basic=str(int(native['library_type'] == 'base')),
                       is_preferred=str(native['preferred']), last_seen=seen,
                       attributes=attributes(native['attributes'], category))
            # URLs and inventory metadata remain facts; no follow-up fetch occurs.
            raw_attributes = strict_json(raw['attributes'])
            raw_attributes.update(upstream_datasheet_url=native['datasheet'],
                                  upstream_manufacturer=native['manufacturer'],
                                  upstream_mfr=native['mfr'], upstream_package=native['package'],
                                  upstream_category=native['category'], upstream_subcategory=native['subcategory'])
            raw['attributes'] = canonical(raw_attributes).decode()
            yield normalize_row(raw, {'volume': 'cache.zip', 'row': index,
                                      'raw_sha256': identity(native)}, manifest['snapshot_date'])


def capture_rows(manifest, source_root):
    rows, pruned, seen = [], 0, set()
    root = Path(source_root).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='.catalog-native-', dir=root.parent) as stage:
        stage = Path(stage)
        reassemble(manifest['volumes'], root, stage / 'joined.zip')
        extract_database(stage / 'joined.zip', stage / 'cache.sqlite3')
        for row in native_rows(stage / 'cache.sqlite3', manifest):
            if row is None:
                pruned += 1
            elif row['lcsc'] in seen:
                raise ValueError('duplicate LCSC identity')
            else:
                seen.add(row['lcsc'])
                rows.append(row)
    if not rows:
        raise ValueError('catalog has no retained supported rows')
    return rows, pruned

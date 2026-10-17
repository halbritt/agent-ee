"""Pinned local catalog capture and offline, content-bound snapshot reads."""
import csv
import ctypes
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
import zipfile

from .storage import canonical, digest, identity, write_json

COLUMNS = ('lcsc', 'mfr', 'mpn', 'package', 'category', 'stock', 'price1',
           'is_basic', 'is_preferred', 'last_seen', 'attributes')
CATEGORIES = ('resistor', 'capacitor', 'inductor', 'diode', 'led', 'connector', 'regulator')
UNITS = {'resistance': 'ohm', 'capacitance': 'F', 'inductance': 'H', 'voltage': 'V',
         'power': 'W', 'temperature': 'degC', 'input_voltage': 'V',
         'output_voltage': 'V', 'dropout': 'V', 'quiescent_current': 'A'}
MAX_VOLUME_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
DERIVED_COLUMNS = COLUMNS + ('normalized', 'provenance')
CATEGORY_ATTRIBUTES = {
    'resistor': ('resistance', 'voltage', 'power', 'temperature'),
    'capacitor': ('capacitance', 'voltage', 'temperature'),
    'inductor': ('inductance', 'temperature'),
    'diode': ('voltage', 'power', 'temperature'),
    'led': ('voltage', 'power', 'temperature'),
    'connector': ('voltage', 'temperature'),
    'regulator': ('input_voltage', 'output_voltage', 'dropout', 'quiescent_current', 'power', 'temperature'),
}


def category_columns(category):
    return tuple(f'{key}_{end}' for key in CATEGORY_ATTRIBUTES[category] for end in ('min', 'max'))


def interval_cells(row, category):
    return tuple(row['normalized'].get(key, {}).get(end)
                 for key in CATEGORY_ATTRIBUTES[category] for end in ('min', 'max'))


def exact_fields(record, fields):
    if not isinstance(record, dict) or set(record) != set(fields):
        raise ValueError(f'expected fields: {", ".join(fields)}')


def nonempty(text):
    if not isinstance(text, str) or not text.strip() or text != text.strip() or '\x00' in text:
        raise ValueError('expected nonempty trimmed text')
    return text


def iso_date(text):
    if not isinstance(text, str) or date.fromisoformat(text).isoformat() != text:
        raise ValueError('expected ISO date YYYY-MM-DD')
    return text


def decimal_text(number):
    if not number.is_finite():
        raise ValueError('nonfinite number')
    return format(number, 'f').rstrip('0').rstrip('.') if '.' in format(number, 'f') else format(number, 'f')


def decimal_number(text):
    if not isinstance(text, str) or not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', text):
        raise ValueError('expected decimal text')
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError('invalid decimal') from exc
    if not number.is_finite() or len(number.as_tuple().digits) > 28 or abs(number.adjusted()) > 50:
        raise ValueError('decimal outside supported precision/range')
    return number


def quantity(text, unit):
    """Explicit SI units only; case-sensitive prefixes avoid SPICE M ambiguity."""
    match = re.fullmatch(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-zΩµμ°]+)\s*', text)
    if not match:
        raise ValueError('expected number and explicit unit')
    number = decimal_number(match[1])
    spelling = match[2].replace('Ω', 'ohm').replace('µ', 'u').replace('μ', 'u').replace('°C', 'degC')
    scales = {'': '1', 'p': '1e-12', 'n': '1e-9', 'u': '1e-6', 'm': '1e-3',
              'k': '1e3', 'M': '1e6', 'G': '1e9'}
    for prefix, scale in scales.items():
        if spelling == prefix + unit and (unit != 'degC' or not prefix):
            return number * Decimal(scale)
    raise ValueError(f'expected unit {unit}')


def attribute_interval(text, unit):
    """Normalize explicit closed ranges, percentage tolerances, or exact ratings."""
    with localcontext() as context:
        context.prec = 80
        if '..' in text:
            ends = text.split('..')
            if len(ends) != 2:
                raise ValueError('invalid interval')
            low, high = (quantity(end.strip(), unit) for end in ends)
        elif '+/-' in text or '±' in text:
            ends = re.split(r'\+/-|±', text)
            if len(ends) != 2 or not ends[1].strip().endswith('%'):
                raise ValueError('expected percentage tolerance')
            nominal = quantity(ends[0].strip(), unit)
            tolerance = decimal_number(ends[1].strip()[:-1])
            if nominal <= 0 or not 0 < tolerance < 100:
                raise ValueError('invalid tolerance')
            delta = nominal * tolerance / 100
            low, high = nominal - delta, nominal + delta
        else:
            low = high = quantity(text, unit)
        if low > high or (unit != 'degC' and low < 0):
            raise ValueError('invalid attribute interval')
        return {'min': decimal_text(low), 'max': decimal_text(high), 'unit': unit}


def normalized_attributes(attributes):
    normalized = {}
    for key, text in attributes.items():
        if key in UNITS:
            try:
                normalized[key] = attribute_interval(text, UNITS[key])
            except ValueError:
                # Preserve raw text; this attribute cannot support a constraint.
                continue
    return normalized


def strict_json(text):
    def pairs(entries):
        record = {}
        for key, entry in entries:
            if key in record:
                raise ValueError('duplicate JSON field')
            record[key] = entry
        return record
    try:
        return json.loads(text, object_pairs_hook=pairs,
                          parse_constant=lambda text: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    except RecursionError as exc:
        raise ValueError('JSON nesting too deep') from exc


def normalize_row(raw, provenance, snapshot_date):
    exact_fields(raw, COLUMNS)
    row = dict(raw)
    for key in ('lcsc', 'mfr', 'mpn', 'package', 'category'):
        nonempty(row[key])
    if not re.fullmatch(r'C[1-9][0-9]*', row['lcsc']) or row['category'] not in CATEGORIES:
        raise ValueError('invalid LCSC/category')
    for key in ('is_basic', 'is_preferred'):
        if row[key] not in ('0', '1'):
            raise ValueError('expected boolean 0/1')
        row[key] = row[key] == '1'
    if row['stock'] == '':
        row['stock'] = None
    elif not isinstance(row['stock'], str) or not re.fullmatch(r'0|[1-9][0-9]*', row['stock']):
        raise ValueError('invalid stock')
    else:
        row['stock'] = int(row['stock'])
        if row['stock'] > 2**63 - 1:
            raise ValueError('stock exceeds SQLite integer range')
    row['price1'] = None if row['price1'] == '' else decimal_text(decimal_number(row['price1']))
    if row['price1'] is not None and Decimal(row['price1']) < 0:
        raise ValueError('negative price')
    if iso_date(row['last_seen']) > snapshot_date:
        raise ValueError('last_seen after snapshot date')
    attributes = strict_json(row['attributes'])
    if not isinstance(attributes, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in attributes.items()):
        raise ValueError('attributes must be a text mapping')
    row['attributes'] = attributes
    row['normalized'] = normalized_attributes(attributes)
    row['provenance'] = provenance
    return row


def safe_path(text):
    path = PurePosixPath(nonempty(text))
    if path.is_absolute() or '..' in path.parts or str(path) != text or '\\' in text:
        raise ValueError('unsafe volume path')
    return path


def validate_source(manifest):
    exact_fields(manifest, ('schema', 'snapshot_date', 'source_url', 'source_commit',
                            'currency', 'stale_before', 'volumes'))
    if manifest['schema'] != 'agent-ee.catalog-source/1' or manifest['currency'] != 'USD':
        raise ValueError('unsupported source schema/currency')
    iso_date(manifest['snapshot_date'])
    nonempty(manifest['source_url'])
    nonempty(manifest['source_commit'])
    if manifest['stale_before'] is not None and iso_date(manifest['stale_before']) > manifest['snapshot_date']:
        raise ValueError('stale cutoff after snapshot date')
    if not isinstance(manifest['volumes'], list) or not manifest['volumes']:
        raise ValueError('expected pinned volumes')
    paths, total = set(), 0
    for volume in manifest['volumes']:
        exact_fields(volume, ('path', 'sha256', 'size_bytes', 'format'))
        path = str(safe_path(volume['path']))
        if path in paths or volume['format'] not in ('csv', 'sqlite', 'zip-csv'):
            raise ValueError('duplicate volume or unsupported format')
        paths.add(path)
        if not isinstance(volume['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', volume['sha256']):
            raise ValueError('invalid volume SHA-256')
        size = volume['size_bytes']
        if type(size) is not int or not 0 < size <= MAX_VOLUME_BYTES:
            raise ValueError('volume size outside capture limit')
        total += size
    if total > MAX_TOTAL_BYTES:
        raise ValueError('catalog exceeds capture limit')


def csv_rows(payload):
    reader = csv.reader(io.StringIO(payload.decode('utf-8'), newline=''), strict=True)
    header = next(reader, None)
    if header is None or len(header) != len(COLUMNS) or set(header) != set(COLUMNS):
        raise ValueError('missing, duplicate or unsupported CSV columns')
    for cells in reader:
        if len(cells) != len(header):
            raise ValueError('CSV row width mismatch')
        yield dict(zip(header, cells))


def sqlite_rows(payload):
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.deserialize(payload)
        connection.execute('PRAGMA trusted_schema=OFF')
        if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('corrupt source SQLite')
        if connection.execute("SELECT type FROM sqlite_master WHERE name='components'").fetchall() != [('table',)]:
            raise ValueError('source requires components table')
        columns = connection.execute('PRAGMA table_xinfo(components)').fetchall()
        if tuple(column[1] for column in columns) != COLUMNS or any(column[6] for column in columns):
            raise ValueError('missing or unsupported SQLite columns/order')
        for cells in connection.execute('SELECT * FROM components ORDER BY lcsc'):
            if any(not isinstance(cell, str) for cell in cells):
                raise ValueError('source SQLite columns must be TEXT')
            yield dict(zip(COLUMNS, cells))


def volume_rows(payload, volume):
    if volume['format'] == 'csv':
        yield from csv_rows(payload)
    elif volume['format'] == 'sqlite':
        yield from sqlite_rows(payload)
    else:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names, total = set(), 0
            entries = archive.infolist()
            if not entries:
                raise ValueError('empty ZIP')
            for entry in entries:
                safe_path(entry.filename)
                total += entry.file_size
                if entry.filename in names or not entry.filename.endswith('.csv') or entry.is_dir() or entry.flag_bits & 1:
                    raise ValueError('duplicate/unsupported/encrypted ZIP member')
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('ZIP symlink refused')
                names.add(entry.filename)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError('expanded ZIP exceeds capture limit')
            for entry in sorted(entries, key=lambda entry: entry.filename):
                yield from csv_rows(archive.read(entry))


def capture_rows(manifest, source_root):
    root = Path(source_root).resolve(strict=True)
    rows, seen, pruned = [], set(), 0
    for volume in sorted(manifest['volumes'], key=lambda volume: volume['path']):
        path = root / volume['path']
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root) or not path.is_file():
            raise ValueError('volume must be a regular source file')
        with path.open('rb') as stream:
            payload = stream.read(volume['size_bytes'] + 1)
        if len(payload) != volume['size_bytes'] or hashlib.sha256(payload).hexdigest() != volume['sha256']:
            raise ValueError('volume size/hash mismatch')
        for index, raw in enumerate(volume_rows(payload, volume), 1):
            row = normalize_row(raw, {'volume': volume['path'], 'row': index,
                                      'raw_sha256': identity(raw)}, manifest['snapshot_date'])
            if row['lcsc'] in seen:
                raise ValueError('duplicate LCSC identity')
            seen.add(row['lcsc'])
            if manifest['stale_before'] is not None and row['last_seen'] < manifest['stale_before']:
                pruned += 1
            else:
                rows.append(row)
    if not rows:
        raise ValueError('catalog has no retained rows')
    return sorted(rows, key=lambda row: int(row['lcsc'][1:])), pruned


def row_counts(rows):
    return {'component_base': len(rows), **{category: sum(row['category'] == category for row in rows) for category in CATEGORIES}}


def table_sql(table):
    """Exact schema text; readers compare sqlite_master so constraints cannot be dropped."""
    declarations = ','.join(f'{key} ' + ('INTEGER' if key in ('stock', 'is_basic', 'is_preferred') else 'TEXT') for key in DERIVED_COLUMNS)
    if table != 'component_base':
        declarations += ',' + ','.join(f'{column} TEXT' for column in category_columns(table))
    return f'CREATE TABLE {table} (' + declarations + ', PRIMARY KEY(lcsc))'


def write_database(path, rows):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('PRAGMA page_size=4096')
        connection.execute(table_sql('component_base'))
        placeholders = ','.join('?' for _ in DERIVED_COLUMNS)
        connection.executemany('INSERT INTO component_base VALUES (' + placeholders + ')',
                               [tuple(canonical(row[key]).decode() if key in ('attributes', 'normalized', 'provenance') else row[key]
                                      for key in DERIVED_COLUMNS) for row in rows])
        for category in CATEGORIES:
            columns = DERIVED_COLUMNS + category_columns(category)
            connection.execute(table_sql(category))
            for row in rows:
                if row['category'] == category:
                    base = tuple(canonical(row[key]).decode() if key in ('attributes', 'normalized', 'provenance') else row[key]
                                 for key in DERIVED_COLUMNS)
                    connection.execute(f'INSERT INTO {category} VALUES (' + ','.join('?' for _ in columns) + ')',
                                       base + interval_cells(row, category))
        connection.commit()
        if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('derived SQLite quick_check failed')


def capture_catalog(source_manifest, source_root, destination):
    """Build in private staging and publish once; never replace an existing snapshot."""
    validate_source(source_manifest)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    rows, pruned = capture_rows(source_manifest, source_root)
    stage = Path(tempfile.mkdtemp(prefix='.catalog-', dir=destination.parent))
    try:
        database = stage / 'catalog.sqlite3'
        write_database(database, rows)
        manifest = {**source_manifest, 'schema': 'agent-ee.catalog/1',
                    'database': {'path': database.name, 'sha256': digest(database),
                                 'size_bytes': database.stat().st_size},
                    'row_counts': row_counts(rows), 'rows_sha256': identity(rows),
                    'pruned_rows': pruned, 'sqlite_version': sqlite3.sqlite_version}
        write_json(stage / 'manifest.json', manifest)
        # Linux RENAME_NOREPLACE publishes the complete directory and refuses races.
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(stage), -100, os.fsencode(destination), 1):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(destination))
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def rederived_row(cells, manifest):
    """Rebuild the raw source row from retained fields and rerun the pinned normalization.

    Hashes only show the bytes are the pinned bytes; they do not show the derived
    columns follow from the raw claims. Every stored field must equal the result.
    """
    stored = dict(zip(DERIVED_COLUMNS, cells))
    decoded = {}
    for key in ('attributes', 'normalized', 'provenance'):
        if not isinstance(stored[key], str):
            raise ValueError(f'snapshot {key} is not canonical JSON')
        decoded[key] = strict_json(stored[key])
        if canonical(decoded[key]).decode() != stored[key]:
            raise ValueError(f'snapshot {key} is not canonical JSON')
    for key in ('is_basic', 'is_preferred'):
        if type(stored[key]) is not int or stored[key] not in (0, 1):
            raise ValueError('invalid snapshot boolean')
    if stored['stock'] is not None and type(stored['stock']) is not int:
        raise ValueError('snapshot stock must be an integer or NULL')
    if stored['price1'] is not None and (not isinstance(stored['price1'], str) or not stored['price1']):
        raise ValueError('snapshot price must be decimal text or NULL')
    raw = {key: stored[key] for key in COLUMNS}
    raw.update(stock='' if stored['stock'] is None else str(stored['stock']),
               price1='' if stored['price1'] is None else stored['price1'],
               is_basic=str(stored['is_basic']), is_preferred=str(stored['is_preferred']))
    provenance = decoded['provenance']
    exact_fields(provenance, ('volume', 'row', 'raw_sha256'))
    if (provenance['volume'] not in {volume['path'] for volume in manifest['volumes']}
            or type(provenance['row']) is not int or provenance['row'] < 1
            or not isinstance(provenance['raw_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', provenance['raw_sha256'])):
        raise ValueError('snapshot row lacks valid raw source provenance')
    row = normalize_row(raw, provenance, manifest['snapshot_date'])
    decoded = {**stored, **decoded, 'is_basic': bool(stored['is_basic']),
               'is_preferred': bool(stored['is_preferred'])}
    if canonical(decoded) != canonical(row):
        raise ValueError(f'snapshot row {row["lcsc"]} does not match rederivation from its raw attributes')
    if manifest['stale_before'] is not None and row['last_seen'] < manifest['stale_before']:
        raise ValueError('snapshot retains a row older than its stale cutoff')
    return row


def read_database(payload, manifest):
    """Decode a snapshot whose manifest source fields were already validated."""
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.deserialize(payload)
        connection.execute('PRAGMA trusted_schema=OFF')
        if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('corrupt snapshot database')
        expected = {'component_base', *CATEGORIES}
        tables = connection.execute("SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if set(tables) != {(name, 'table', table_sql(name)) for name in expected}:
            raise ValueError('unsupported snapshot tables or schema')
        rows, identities, sources = [], set(), set()
        for cells in connection.execute('SELECT * FROM component_base ORDER BY CAST(substr(lcsc,2) AS INTEGER)'):
            row = rederived_row(cells, manifest)
            source = (row['provenance']['volume'], row['provenance']['row'])
            if row['lcsc'] in identities or source in sources:
                raise ValueError('duplicate snapshot LCSC identity or source record')
            identities.add(row['lcsc'])
            sources.add(source)
            rows.append(row)
        by_lcsc = {row['lcsc']: row for row in rows}
        for category in CATEGORIES:
            actual = connection.execute(f'SELECT * FROM {category} ORDER BY lcsc').fetchall()
            base = connection.execute('SELECT * FROM component_base WHERE category=? ORDER BY lcsc', (category,)).fetchall()
            expected_rows = [cells + interval_cells(by_lcsc[cells[0]], category) for cells in base]
            if actual != expected_rows:
                raise ValueError('derived category table mismatch')
        return rows


def load_catalog(snapshot_directory, expected_manifest_sha256):
    root = Path(snapshot_directory)
    payload = (root / 'manifest.json').read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_manifest_sha256:
        raise ValueError('snapshot manifest hash mismatch')
    manifest = strict_json(payload)
    exact_fields(manifest, ('schema', 'snapshot_date', 'source_url', 'source_commit', 'currency',
                            'stale_before', 'volumes', 'database', 'row_counts', 'rows_sha256',
                            'pruned_rows', 'sqlite_version'))
    if manifest['schema'] != 'agent-ee.catalog/1':
        raise ValueError('unsupported catalog schema')
    source = {key: manifest[key] for key in ('snapshot_date', 'source_url', 'source_commit', 'currency', 'stale_before', 'volumes')}
    validate_source({'schema': 'agent-ee.catalog-source/1', **source})
    database = manifest['database']
    exact_fields(database, ('path', 'sha256', 'size_bytes'))
    if database['path'] != 'catalog.sqlite3' or type(database['size_bytes']) is not int or not 0 < database['size_bytes'] <= MAX_TOTAL_BYTES:
        raise ValueError('invalid snapshot database descriptor')
    with (root / database['path']).open('rb') as stream:
        payload = stream.read(database['size_bytes'] + 1)
    if len(payload) != database['size_bytes'] or hashlib.sha256(payload).hexdigest() != database['sha256']:
        raise ValueError('snapshot database size/hash mismatch')
    if type(manifest['pruned_rows']) is not int or manifest['pruned_rows'] < 0:
        raise ValueError('invalid pruned row count')
    nonempty(manifest['sqlite_version'])
    rows = read_database(payload, manifest)
    if not rows or row_counts(rows) != manifest['row_counts'] or identity(rows) != manifest['rows_sha256']:
        raise ValueError('snapshot row identity/count mismatch')
    return {'manifest': manifest, 'rows': rows}


def catalog_check(snapshot_directory, expected_manifest_sha256):
    """Coordinator adapter: failure is unknown; success only establishes snapshot integrity."""
    try:
        snapshot = load_catalog(snapshot_directory, expected_manifest_sha256)
    except (OSError, ValueError, sqlite3.Error, TypeError, KeyError) as exc:
        return {'execution': 'failed', 'outcome': 'unknown', 'details': {
            'snapshot_manifest_sha256': expected_manifest_sha256,
            'snapshot_sha256': None, 'snapshot_date': None, 'error': str(exc)}}
    return {'execution': 'ok', 'outcome': 'pass', 'details': {
        'snapshot_manifest_sha256': expected_manifest_sha256,
        'snapshot_sha256': snapshot['manifest']['database']['sha256'],
        'snapshot_date': snapshot['manifest']['snapshot_date'],
        'row_counts': snapshot['manifest']['row_counts']}}

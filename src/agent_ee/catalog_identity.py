"""Pinned full-catalog identity index and networkless Stage 2 MPN resolution.

The index retains exact identity facts (LCSC id, MPN, manufacturer, package,
library tier, historical stock, last_seen and the raw upstream datasheet URL)
for every non-passive row of the pinned jlcparts ``source-db-v2`` split feed.
It is a separate ``agent-ee.catalog-identity/1`` snapshot: the
``agent-ee.catalog/1`` tables, SI normalization and canonical row hashes are
untouched, and parametric selection stays in ``part_selection``.

Resolution maps ``agent-ee.part-decisions/1`` MPN declarations to candidate LCSC
ids (input for ``jlc_stock_capture``) and candidate datasheet URLs (input for
``datasheet_capture``). Ambiguous or prefix-only matches are refused unless an
explicit, reasoned narrowing names the package or LCSC id. A missing match is
unknown, never proof that a part does not exist. Datasheet URLs are candidates
for capture only; source authority stays with ``datasheet_selection``.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile

from .catalog_bouni import passive_boundary
from .catalog import (MAX_TOTAL_BYTES, MAX_VOLUME_BYTES, exact_fields, iso_date, nonempty,
                      publish_directory, rows_sha256, safe_path, strict_json)
from .catalog_jlcparts import (CATEGORY_MAP, COMPONENT_COLUMNS, check_table, extract_database,
                               reassemble, split_volumes)
from .storage import canonical, digest, identity, write_json

SOURCE_SCHEMA = 'agent-ee.catalog-identity-source/1'
SCHEMA = 'agent-ee.catalog-identity/1'
FORMAT = 'jlcparts-v2-identity-split-zip'
RESOLUTION_SCHEMA = 'agent-ee.catalog-identity-resolution/1'
NARROWING_SCHEMA = 'agent-ee.catalog-identity-narrowing/1'
DECISIONS_SCHEMA = 'agent-ee.part-decisions/1'
# Passive families are resolved parametrically from the verified generic tables;
# validated by catalog_bouni.passive_boundary (decision in docs/decisions.md).
GENERIC_FAMILIES = ('resistor', 'capacitor', 'inductor')
# Subcategories inside a passive category whose identity is not established by
# nominal value, tolerance and package (impedance, B-value, taper, turns ratio,
# pin configuration). Such rows keep their identity rows: fail closed.
NON_GENERIC_SUBCATEGORY_WORDS = ('ferrite', 'bead', 'common mode', 'transformer', 'potentiometer',
                                 'variable', 'trimmer', 'thermistor', 'varistor', 'fuse', 'network',
                                 'array', 'crystal', 'resonator')
TIERS = ('Basic', 'Preferred', 'Extended')
COLUMNS = ('lcsc', 'mpn', 'mpn_key', 'mfr', 'package', 'category', 'subcategory', 'library_tier',
           'stock', 'present', 'last_seen', 'datasheet_url', 'provenance')
LCSC_RE = re.compile(r'C[1-9][0-9]*')
MAX_PREFIX_CANDIDATES = 50
# Resolved identities that may feed exact-id stock capture and datasheet capture.
CAPTURE_STATUSES = ('resolved', 'selected', 'verified', 'declared_lcsc')
LIMIT = ('Catalog identity candidates only: no live stock, datasheet authority, suitability, '
         'or proof of absence is established.')
TABLE_SQL = ('CREATE TABLE identity (' + ','.join(
    f'{c} ' + ('INTEGER' if c in ('stock', 'present') else 'TEXT') for c in COLUMNS) + ', PRIMARY KEY(lcsc))')
INDEX_SQL = 'CREATE INDEX identity_mpn ON identity (mpn_key, lcsc)'


def mpn_key(mpn):
    """Lookup key: trimmed, case-insensitive. Empty MPNs are never matched."""
    return mpn.strip().upper()


def generic(category, subcategory, boundary):
    """True only for a mapped family inside the validated passive boundary."""
    if any(word in subcategory.lower() for word in NON_GENERIC_SUBCATEGORY_WORDS):
        return False
    return (CATEGORY_MAP.get(category) or CATEGORY_MAP.get(subcategory)) in boundary


def validate_source(manifest):
    exact_fields(manifest, ('schema', 'snapshot_date', 'source_url', 'source_commit', 'stale_before', 'volumes'))
    if manifest['schema'] != SOURCE_SCHEMA:
        raise ValueError('unsupported identity source schema')
    iso_date(manifest['snapshot_date'])
    nonempty(manifest['source_url'])
    nonempty(manifest['source_commit'])
    if manifest['stale_before'] is not None and iso_date(manifest['stale_before']) > manifest['snapshot_date']:
        raise ValueError('stale cutoff after snapshot date')
    if not isinstance(manifest['volumes'], list):
        raise ValueError('expected pinned volumes')
    total = 0
    for volume in manifest['volumes']:
        exact_fields(volume, ('path', 'sha256', 'size_bytes', 'format'))
        safe_path(volume['path'])
        if not isinstance(volume['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', volume['sha256']):
            raise ValueError('invalid volume SHA-256')
        if type(volume['size_bytes']) is not int or not 0 < volume['size_bytes'] <= MAX_VOLUME_BYTES:
            raise ValueError('volume size outside capture limit')
        total += volume['size_bytes']
    if total > MAX_TOTAL_BYTES:
        raise ValueError('catalog exceeds capture limit')
    split_volumes(manifest['volumes'], FORMAT)


def _text(native, key):
    if not isinstance(native[key], str):
        raise ValueError(f'invalid native {key}')
    return native[key]


def native_rows(path, manifest, pruned, boundary):
    """Yield identity rows in LCSC order; count generic and stale exclusions."""
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
            for key in ('lcsc', 'stock', 'preferred', 'present', 'fetched_at'):
                if type(native[key]) is not int:
                    raise ValueError(f'invalid native {key}')
            category, subcategory = _text(native, 'category'), _text(native, 'subcategory')
            if generic(category, subcategory, boundary):
                pruned['generic'] += 1
                continue
            if native['lcsc'] < 1 or native['present'] not in (0, 1) or native['preferred'] not in (0, 1):
                raise ValueError('invalid native identity/presence flag')
            if native['fetched_at'] <= 0 or native['stock'] < 0:
                raise ValueError('invalid native date or negative stock')
            seen = datetime.fromtimestamp(native['fetched_at'], timezone.utc).date().isoformat()
            if seen > manifest['snapshot_date']:
                raise ValueError('last_seen after snapshot date')
            if manifest['stale_before'] is not None and seen < manifest['stale_before']:
                pruned['stale'] += 1
                continue
            if native['library_type'] not in ('base', 'expand'):
                raise ValueError('unknown native library type')
            tier = 'Basic' if native['library_type'] == 'base' else 'Preferred' if native['preferred'] else 'Extended'
            mpn = _text(native, 'mfr').strip()
            url = _text(native, 'datasheet')
            yield dict(lcsc=f'C{native["lcsc"]}', mpn=mpn, mpn_key=mpn_key(mpn),
                       mfr=_text(native, 'manufacturer').strip(), package=_text(native, 'package').strip(),
                       category=category, subcategory=subcategory, library_tier=tier,
                       stock=native['stock'], present=native['present'], last_seen=seen,
                       # The raw upstream claim; never fetched or validated here.
                       datasheet_url=url or None,
                       provenance={'volume': 'cache.zip', 'row': index, 'raw_sha256': identity(native)})


def write_database(path, rows):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('PRAGMA page_size=4096')
        connection.execute(TABLE_SQL)
        connection.executemany('INSERT INTO identity VALUES (' + ','.join('?' for _ in COLUMNS) + ')',
                               (tuple(canonical(row[c]).decode() if c == 'provenance' else row[c]
                                      for c in COLUMNS) for row in rows))
        connection.execute(INDEX_SQL)
        connection.commit()
        if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('derived SQLite quick_check failed')


def capture_identity(source_manifest, source_root, destination):
    """Verify every pinned volume, derive the index privately, publish once."""
    validate_source(source_manifest)
    boundary = passive_boundary(GENERIC_FAMILIES)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    root = Path(source_root).resolve(strict=True)
    stage = Path(tempfile.mkdtemp(prefix='.catalog-identity-', dir=destination.parent))
    try:
        with tempfile.TemporaryDirectory(prefix='.catalog-native-', dir=root.parent) as native:
            native = Path(native)
            reassemble(source_manifest['volumes'], root, native / 'joined.zip', FORMAT)
            extract_database(native / 'joined.zip', native / 'cache.sqlite3')
            pruned = {'generic': 0, 'stale': 0}
            rows = list(native_rows(native / 'cache.sqlite3', source_manifest, pruned, boundary))
        if not rows:
            raise ValueError('identity index has no retained rows')
        if len({row['lcsc'] for row in rows}) != len(rows):
            raise ValueError('duplicate LCSC identity')
        database = stage / 'identity.sqlite3'
        write_database(database, rows)
        if not 0 < database.stat().st_size <= MAX_TOTAL_BYTES:
            raise ValueError('derived identity index exceeds capture limit')
        manifest = {**source_manifest, 'schema': SCHEMA, 'generic_families_excluded': list(GENERIC_FAMILIES),
                    'database': {'path': database.name, 'sha256': digest(database),
                                 'size_bytes': database.stat().st_size},
                    'row_count': len(rows), 'rows_sha256': rows_sha256(rows), 'pruned_rows': pruned,
                    'sqlite_version': sqlite3.sqlite_version}
        write_json(stage / 'manifest.json', manifest)
        publish_directory(stage, destination)
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _stored_row(cells, manifest, boundary):
    row = dict(zip(COLUMNS, cells))
    for key in ('lcsc', 'mpn', 'mpn_key', 'mfr', 'package', 'category', 'subcategory',
                'library_tier', 'last_seen', 'provenance'):
        if not isinstance(row[key], str):
            raise ValueError(f'identity {key} must be text')
    if not LCSC_RE.fullmatch(row['lcsc']) or row['mpn'] != row['mpn'].strip() or row['mpn_key'] != mpn_key(row['mpn']):
        raise ValueError('identity row LCSC or MPN key mismatch')
    if row['mfr'] != row['mfr'].strip() or row['package'] != row['package'].strip():
        raise ValueError('identity text is not trimmed')
    if row['library_tier'] not in TIERS or generic(row['category'], row['subcategory'], boundary):
        raise ValueError('identity row tier or category outside profile')
    if type(row['stock']) is not int or row['stock'] < 0 or type(row['present']) is not int or row['present'] not in (0, 1):
        raise ValueError('identity stock/presence must be integers')
    if iso_date(row['last_seen']) > manifest['snapshot_date'] or (
            manifest['stale_before'] is not None and row['last_seen'] < manifest['stale_before']):
        raise ValueError('identity row last_seen outside snapshot window')
    if row['datasheet_url'] is not None and (not isinstance(row['datasheet_url'], str) or not row['datasheet_url']):
        raise ValueError('identity datasheet URL must be nonempty text or NULL')
    provenance = strict_json(row['provenance'])
    if canonical(provenance).decode() != row['provenance']:
        raise ValueError('identity provenance is not canonical JSON')
    exact_fields(provenance, ('volume', 'row', 'raw_sha256'))
    if (provenance['volume'] != 'cache.zip' or type(provenance['row']) is not int or provenance['row'] < 1
            or not isinstance(provenance['raw_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', provenance['raw_sha256'])):
        raise ValueError('identity row lacks valid raw source provenance')
    row['provenance'] = provenance
    return row


class IdentityIndex:
    """A verified in-memory copy; queries run on exactly the hashed bytes."""

    def __init__(self, manifest, manifest_sha256, connection, boundary):
        self.manifest = manifest
        self.boundary = boundary
        self.manifest_sha256 = manifest_sha256
        self.connection = connection

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _rows(self, where, args, limit=-1):
        return [_stored_row(cells, self.manifest, self.boundary) for cells in self.connection.execute(
            'SELECT ' + ','.join(COLUMNS) + ' FROM identity WHERE ' + where + ' ORDER BY mpn_key, lcsc LIMIT ?',
            (*args, limit))]

    def by_lcsc(self, lcsc):
        rows = self._rows('lcsc=?', (lcsc,))
        return rows[0] if rows else None

    def exact(self, mpn):
        key = mpn_key(mpn)
        return self._rows('mpn_key=?', (key,)) if key else []

    def prefix(self, mpn, limit=MAX_PREFIX_CANDIDATES):
        """Rows whose MPN extends the declared one, bounded; returns (rows, truncated)."""
        key = mpn_key(mpn)
        if not key:
            return [], False
        upper = key[:-1] + chr(ord(key[-1]) + 1)
        rows = self._rows('mpn_key>? AND mpn_key<?', (key, upper), limit + 1)
        return rows[:limit], len(rows) > limit


def load_identity_index(directory, expected_manifest_sha256):
    """Verify the manifest pin, DB bytes, schema and every row before any query."""
    root = Path(directory)
    payload = (root / 'manifest.json').read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_manifest_sha256:
        raise ValueError('identity manifest hash mismatch')
    manifest = strict_json(payload)
    exact_fields(manifest, ('schema', 'snapshot_date', 'source_url', 'source_commit', 'stale_before', 'volumes',
                            'generic_families_excluded', 'database', 'row_count', 'rows_sha256',
                            'pruned_rows', 'sqlite_version'))
    boundary = passive_boundary(manifest['generic_families_excluded'])
    if manifest['schema'] != SCHEMA or manifest['generic_families_excluded'] != list(GENERIC_FAMILIES):
        raise ValueError('unsupported identity index schema or profile')
    validate_source({'schema': SOURCE_SCHEMA, **{k: manifest[k] for k in (
        'snapshot_date', 'source_url', 'source_commit', 'stale_before', 'volumes')}})
    exact_fields(manifest['pruned_rows'], ('generic', 'stale'))
    if any(type(v) is not int or v < 0 for v in manifest['pruned_rows'].values()):
        raise ValueError('invalid pruned row counts')
    nonempty(manifest['sqlite_version'])
    database = manifest['database']
    exact_fields(database, ('path', 'sha256', 'size_bytes'))
    if (database['path'] != 'identity.sqlite3' or type(database['size_bytes']) is not int
            or not 0 < database['size_bytes'] <= MAX_TOTAL_BYTES):
        raise ValueError('invalid identity database descriptor')
    path = root / database['path']
    if path.is_symlink():
        raise ValueError('identity database must be a regular file')
    with path.open('rb') as stream:
        raw = stream.read(database['size_bytes'] + 1)
    if len(raw) != database['size_bytes'] or hashlib.sha256(raw).hexdigest() != database['sha256']:
        raise ValueError('identity database size/hash mismatch')
    connection = sqlite3.connect(':memory:')
    try:
        connection.deserialize(raw)
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('PRAGMA query_only=ON')
        # integrity_check also proves the MPN index agrees with the table.
        if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('corrupt identity database')
        schema = set(connection.execute("SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
        if schema != {('identity', 'table', TABLE_SQL), ('identity_mpn', 'index', INDEX_SQL)}:
            raise ValueError('unsupported identity tables or schema')
        hashed, count, sources, previous = hashlib.sha256(b'['), 0, set(), 0
        for cells in connection.execute('SELECT ' + ','.join(COLUMNS) + ' FROM identity ORDER BY CAST(substr(lcsc,2) AS INTEGER)'):
            row = _stored_row(cells, manifest, boundary)
            number = int(row['lcsc'][1:])
            if number <= previous or row['provenance']['row'] in sources:
                raise ValueError('duplicate identity LCSC or source record')
            previous = number
            sources.add(row['provenance']['row'])
            hashed.update((b',' if count else b'') + canonical(row))
            count += 1
        hashed.update(b']')
        if not count or count != manifest['row_count'] or hashed.hexdigest() != manifest['rows_sha256']:
            raise ValueError('identity row identity/count mismatch')
    except BaseException:
        connection.close()
        raise
    return IdentityIndex(manifest, expected_manifest_sha256, connection, boundary)


def _candidate(row):
    return {k: row[k] for k in ('lcsc', 'mpn', 'mfr', 'package', 'category', 'library_tier',
                                'stock', 'present', 'last_seen', 'datasheet_url')}


def _decisions(document):
    from .part_decisions import part
    if not isinstance(document, dict) or document.get('schema') != DECISIONS_SCHEMA:
        raise ValueError('expected an agent-ee.part-decisions/1 document')
    decisions = document['declaration']['decisions'] if 'declaration' in document else None
    if not isinstance(decisions, list) or not decisions:
        raise ValueError('part decisions require a nonempty decision list')
    for decision in decisions:
        if not isinstance(decision, dict) or not isinstance(decision.get('requirement_id'), str):
            raise ValueError('decision requires requirement_id')
        part({k: decision.get(k) for k in ('mpn', 'lcsc')})
    return decisions


def _narrowing(document, decisions):
    if document is None:
        return {}
    exact_fields(document, ('schema', 'entries'))
    if document['schema'] != NARROWING_SCHEMA or not isinstance(document['entries'], list):
        raise ValueError('unsupported identity narrowing')
    keys = {(d['requirement_id'], d['mpn']): d for d in decisions}
    entries = {}
    for entry in document['entries']:
        exact_fields(entry, ('requirement_id', 'mpn', 'package', 'lcsc', 'reason'))
        nonempty(entry['reason'])
        key = (entry['requirement_id'], entry['mpn'])
        if key not in keys or key in entries:
            raise ValueError(f'narrowing names no decision or repeats one: {key}')
        if keys[key]['lcsc'] is not None:
            raise ValueError('a declared LCSC id is verified, not narrowed')
        if entry['package'] is None and entry['lcsc'] is None:
            raise ValueError('narrowing requires a package or an LCSC id')
        if entry['package'] is not None:
            nonempty(entry['package'])
        if entry['lcsc'] is not None and (not isinstance(entry['lcsc'], str) or not LCSC_RE.fullmatch(entry['lcsc'])):
            raise ValueError('narrowing LCSC id must be C followed by a positive integer')
        entries[key] = entry
    return entries


def resolve_decision(index, decision, narrowing=None):
    """Classify one decision; only an unambiguous or explicitly narrowed match resolves."""
    mpn, declared = decision['mpn'], decision['lcsc']
    result = {'requirement_id': decision['requirement_id'], 'mpn': mpn, 'declared_lcsc': declared,
              'narrowing': narrowing, 'match': None, 'status': None, 'lcsc': None,
              'candidates': [], 'truncated': False, 'reason': None}
    if mpn is None:
        row = index.by_lcsc(declared)
        result.update(match='lcsc', candidates=[_candidate(row)] if row else [])
        if row is None:
            return {**result, 'status': 'unknown', 'reason': 'declared LCSC id is absent from the index'}
        return {**result, 'status': 'declared_lcsc', 'lcsc': declared}
    rows, match, truncated = index.exact(mpn), 'exact', False
    if not rows:
        (rows, truncated), match = index.prefix(mpn), 'prefix'
    result.update(match=match if rows else None, candidates=[_candidate(r) for r in rows], truncated=truncated)
    if declared is not None:
        row = index.by_lcsc(declared)
        if row is None:
            return {**result, 'status': 'unknown', 'reason': 'declared LCSC id is absent from the index'}
        if row['mpn_key'] != mpn_key(mpn):
            return {**result, 'status': 'conflict', 'candidates': [_candidate(row)] + result['candidates'],
                    'reason': f'index MPN for {declared} is {row["mpn"]!r}, not the declared MPN'}
        return {**result, 'status': 'verified', 'lcsc': declared}
    if narrowing is not None:
        if truncated:
            return {**result, 'status': 'ambiguous', 'reason': 'prefix candidates exceed the reviewed bound'}
        if narrowing['package'] is not None:
            rows = [r for r in rows if r['package'] == narrowing['package']]
        if narrowing['lcsc'] is not None:
            rows = [r for r in rows if r['lcsc'] == narrowing['lcsc']]
            if not rows:
                return {**result, 'status': 'conflict', 'reason': 'selected LCSC id is not a narrowed candidate'}
            return {**result, 'status': 'selected', 'lcsc': rows[0]['lcsc']}
    if not rows:
        return {**result, 'status': 'unknown',
                'reason': 'no candidate after package narrowing' if narrowing else 'no MPN match in the index'}
    if match == 'prefix':
        return {**result, 'status': 'ambiguous', 'reason': 'prefix-only match requires an explicit LCSC selection'}
    if len(rows) > 1:
        return {**result, 'status': 'ambiguous', 'reason': f'{len(rows)} candidates remain'}
    return {**result, 'status': 'resolved', 'lcsc': rows[0]['lcsc']}


def resolve_decisions(index, decisions_document, narrowing_document=None):
    """Deterministic, networkless resolution of a part-decisions/1 document."""
    from .datasheet_capture import validate_url
    decisions = _decisions(decisions_document)
    narrowing = _narrowing(narrowing_document, decisions)
    entries = [resolve_decision(index, d, narrowing.get((d['requirement_id'], d['mpn'])))
               for d in decisions]
    stock, bindings = set(), {}
    for entry in entries:
        entry['datasheet_url'] = {'url': None, 'status': None}
        if entry['status'] not in CAPTURE_STATUSES:
            continue
        stock.add(entry['lcsc'])
        url = index.by_lcsc(entry['lcsc'])['datasheet_url']
        status = 'absent' if url is None else 'candidate'
        try:
            if url is not None:
                validate_url(url)
        except ValueError:
            status = 'invalid'
        entry['datasheet_url'] = {'url': url, 'status': status}
        if status == 'candidate':
            bindings[entry['lcsc']] = url
    manifest = index.manifest
    return {'schema': RESOLUTION_SCHEMA, 'limit': LIMIT,
            'index': {'manifest_sha256': index.manifest_sha256,
                      'database_sha256': manifest['database']['sha256'],
                      'rows_sha256': manifest['rows_sha256'], 'snapshot_date': manifest['snapshot_date']},
            'decisions_sha256': identity(decisions_document),
            'narrowing_sha256': None if narrowing_document is None else identity(narrowing_document),
            'entries': entries,
            'stock_capture_ids': sorted(stock, key=lambda lcsc: int(lcsc[1:])),
            'datasheet_bindings': [{'id': lcsc, 'datasheet_url': bindings[lcsc]}
                                   for lcsc in sorted(bindings, key=lambda lcsc: int(lcsc[1:]))]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    capture = sub.add_parser('capture', help='derive an identity index from pinned local volumes')
    capture.add_argument('manifest')
    capture.add_argument('--source-root', required=True)
    capture.add_argument('--out', required=True)
    resolve = sub.add_parser('resolve', help='resolve part-decisions/1 MPNs against a pinned index')
    resolve.add_argument('index')
    resolve.add_argument('manifest_sha256')
    resolve.add_argument('decisions')
    resolve.add_argument('--narrowing')
    resolve.add_argument('--out', required=True, help='new resolution JSON file')
    args = parser.parse_args(argv)
    try:
        if args.command == 'capture':
            capture_identity(strict_json(Path(args.manifest).read_bytes()), args.source_root, args.out)
            print(digest(Path(args.out) / 'manifest.json'))
            return 0
        out = Path(args.out)
        if out.exists() or out.is_symlink():
            raise FileExistsError(f'output already exists: {out}')
        narrowing = strict_json(Path(args.narrowing).read_bytes()) if args.narrowing else None
        with load_identity_index(args.index, args.manifest_sha256) as index:
            result = resolve_decisions(index, strict_json(Path(args.decisions).read_bytes()), narrowing)
        write_json(out, result)
        print(digest(out))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f'catalog identity: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

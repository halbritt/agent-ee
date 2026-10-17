"""Owned synthetic catalog inputs; no supplier stock assertions."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee.catalog import capture_catalog, load_catalog
from agent_ee.storage import digest


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def source(self, rows=None):
        rows = rows or [dict(lcsc='C1', mfr='Owned fixture', mpn='R1', package='0603',
                            category='resistor', stock='123', price1='0.01', is_basic='1',
                            is_preferred='0', last_seen='2026-10-17',
                            attributes=json.dumps({'resistance': '1 kohm +/-1%'}))]
        path = self.root / 'owned.csv'
        with path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return dict(schema='agent-ee.catalog-source/1', snapshot_date='2026-10-17',
                    source_url='https://example.invalid/owned', source_commit='owned-v1',
                    currency='USD', stale_before=None,
                    volumes=[dict(path=path.name, sha256=digest(path),
                                  size_bytes=path.stat().st_size, format='csv')])

    def test_capture_query_is_reproducible_and_as_of(self):
        source = self.source()
        first = self.root / 'first'
        second = self.root / 'second'
        capture_catalog(source, self.root, first)
        capture_catalog(source, self.root, second)
        loaded = load_catalog(first, digest(first / 'manifest.json'))
        self.assertEqual(loaded['manifest']['snapshot_date'], '2026-10-17')
        self.assertEqual(loaded['rows'][0]['stock'], 123)
        self.assertIs(loaded['rows'][0]['is_basic'], True)
        self.assertEqual(loaded['rows'][0]['normalized']['resistance'],
                         {'min': '990', 'max': '1010', 'unit': 'ohm'})
        self.assertEqual((first / 'catalog.sqlite3').read_bytes(),
                         (second / 'catalog.sqlite3').read_bytes())
        self.assertEqual((first / 'manifest.json').read_bytes(),
                         (second / 'manifest.json').read_bytes())

    def test_bad_volume_publishes_nothing(self):
        source = self.source()
        source['volumes'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            capture_catalog(source, self.root, self.root / 'bad')
        self.assertFalse((self.root / 'bad').exists())
        self.assertEqual(list(self.root.glob('.catalog-*')), [])

    def test_bad_schema_and_duplicate_rows_abort(self):
        source = self.source()
        path = self.root / 'owned.csv'
        original = path.read_text()
        for broken in (original.replace('stock,', 'stocks,'), original + original.splitlines()[1] + '\n',
                       original + 'too,few,columns\n'):
            with self.subTest(broken=broken):
                path.write_text(broken)
                source['volumes'][0].update(sha256=digest(path), size_bytes=path.stat().st_size)
                with self.assertRaises(ValueError):
                    capture_catalog(source, self.root, self.root / 'bad')
                self.assertFalse((self.root / 'bad').exists())

    def test_unknown_stock_price_and_attribute_are_not_invented(self):
        source = self.source()
        path = self.root / 'owned.csv'
        with path.open() as stream:
            rows = list(csv.DictReader(stream))
        rows[0].update(stock='', price1='', attributes='{"resistance":"unreadable"}')
        source = self.source(rows)
        capture_catalog(source, self.root, self.root / 'snapshot')
        row = load_catalog(self.root / 'snapshot', digest(self.root / 'snapshot/manifest.json'))['rows'][0]
        self.assertIsNone(row['stock'])
        self.assertIsNone(row['price1'])
        self.assertEqual(row['normalized'], {})
        self.assertEqual(row['attributes'], {'resistance': 'unreadable'})

    def test_stale_cutoff_is_explicit_and_existing_destination_is_untouched(self):
        source = self.source()
        path = self.root / 'owned.csv'
        with path.open() as stream:
            row = next(csv.DictReader(stream))
        stale = {**row, 'lcsc': 'C2', 'last_seen': '2020-01-01'}
        source = self.source([row, stale])
        source['stale_before'] = '2026-01-01'
        destination = self.root / 'snapshot'
        manifest = capture_catalog(source, self.root, destination)
        self.assertEqual(manifest['pruned_rows'], 1)
        self.assertEqual(manifest['row_counts']['resistor'], 1)
        before = (destination / 'manifest.json').read_bytes()
        with self.assertRaises(FileExistsError):
            capture_catalog(source, self.root, destination)
        self.assertEqual(before, (destination / 'manifest.json').read_bytes())

    def test_missing_corrupt_and_unpinned_snapshots_are_unknown(self):
        from agent_ee.catalog import catalog_check
        destination = self.root / 'snapshot'
        self.assertEqual(catalog_check(destination, '0' * 64)['outcome'], 'unknown')
        capture_catalog(self.source(), self.root, destination)
        expected = digest(destination / 'manifest.json')
        self.assertEqual(catalog_check(destination, expected)['outcome'], 'pass')
        self.assertEqual(catalog_check(destination, '0' * 64)['execution'], 'failed')
        (destination / 'catalog.sqlite3').write_bytes(b'corrupt')
        self.assertEqual(catalog_check(destination, expected)['execution'], 'failed')
        self.assertEqual(catalog_check(destination, expected)['outcome'], 'unknown')

    def test_units_are_explicit_and_case_sensitive(self):
        from agent_ee.catalog import attribute_interval
        self.assertEqual(attribute_interval('100 nF +/-10%', 'F'),
                         {'min': '0.00000009', 'max': '0.00000011', 'unit': 'F'})
        self.assertEqual(attribute_interval('-40 degC .. 85 degC', 'degC'),
                         {'min': '-40', 'max': '85', 'unit': 'degC'})
        self.assertEqual(attribute_interval('1 Mohm', 'ohm')['min'], '1000000')
        for malformed in ('10k', '100 nF', '1 kohm +/-0%', 'NaN ohm', '-1 ohm'):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                attribute_interval(malformed, 'ohm')

    def test_interval_arithmetic_does_not_depend_on_callers_decimal_context(self):
        from decimal import localcontext
        from agent_ee.catalog import attribute_interval
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(attribute_interval('10 kohm +/-1%', 'ohm')['min'], '9900')

    def test_csv_zip_and_sqlite_imports_and_corrupt_source(self):
        import io
        import sqlite3
        import zipfile
        from agent_ee.catalog import COLUMNS
        source = self.source()
        csv_bytes = (self.root / 'owned.csv').read_bytes()
        archive = self.root / 'owned.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('parts.csv', csv_bytes)
        database = self.root / 'owned.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE components (' + ','.join(column + ' TEXT' for column in COLUMNS) + ')')
            row = next(csv.DictReader(io.StringIO(csv_bytes.decode())))
            connection.execute('INSERT INTO components VALUES (' + ','.join('?' for _ in COLUMNS) + ')', tuple(row.values()))
        for path, fmt in ((archive, 'zip-csv'), (database, 'sqlite')):
            with self.subTest(format=fmt):
                source['volumes'] = [dict(path=path.name, sha256=digest(path), size_bytes=path.stat().st_size, format=fmt)]
                destination = self.root / fmt
                capture_catalog(source, self.root, destination)
                self.assertEqual(load_catalog(destination, digest(destination / 'manifest.json'))['rows'][0]['lcsc'], 'C1')
        database.write_bytes(b'invalid SQLite')
        source['volumes'] = [dict(path=database.name, sha256=digest(database), size_bytes=database.stat().st_size, format='sqlite')]
        with self.assertRaises(sqlite3.Error):
            capture_catalog(source, self.root, self.root / 'invalid')
        self.assertFalse((self.root / 'invalid').exists())

    def test_unsafe_zip_and_duplicate_json_fail_capture(self):
        import zipfile
        source = self.source()
        archive = self.root / 'unsafe.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('../parts.csv', (self.root / 'owned.csv').read_bytes())
        source['volumes'] = [dict(path=archive.name, sha256=digest(archive), size_bytes=archive.stat().st_size, format='zip-csv')]
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            capture_catalog(source, self.root, self.root / 'unsafe')
        source = self.source()
        with (self.root / 'owned.csv').open() as stream:
            row = next(csv.DictReader(stream))
        row['attributes'] = '{"voltage":"50 V","voltage":"5 V"}'
        source = self.source([row])
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            capture_catalog(source, self.root, self.root / 'duplicate')

    def test_snapshot_query_in_networkless_readonly_sandbox(self):
        import subprocess
        from toolchain import require_toolchain
        from agent_ee.executor import sandbox_argv
        require_toolchain()
        snapshot = self.root / 'snapshot'
        capture_catalog(self.source(), self.root, snapshot)
        expected = digest(snapshot / 'manifest.json')
        work = self.root / 'work'
        work.mkdir()
        program = ('from agent_ee.catalog import load_catalog; '
                   f's = load_catalog("/candidate", {expected!r}); '
                   'r = s["rows"][0]; '
                   'assert r["normalized"]["resistance"]["min"] == "990"; '
                   'assert r["stock"] == 123 and r["is_basic"] is True; '
                   'assert s["manifest"]["snapshot_date"] == "2026-10-17"; '
                   'print(r["lcsc"])')
        completed = subprocess.run(sandbox_argv(snapshot, work, ['/usr/bin/python3', '-c', program]),
                                   capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), 'C1')

    def test_category_table_exposes_typed_si_interval_columns(self):
        import sqlite3
        destination = self.root / 'snapshot'
        capture_catalog(self.source(), self.root, destination)
        with sqlite3.connect(destination / 'catalog.sqlite3') as connection:
            self.assertEqual(connection.execute(
                'SELECT lcsc,resistance_min,resistance_max FROM resistor').fetchall(),
                [('C1', '990', '1010')])

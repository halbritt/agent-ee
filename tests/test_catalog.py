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

    def archive_source(self):
        import io
        import zipfile
        from agent_ee.catalog import COLUMNS
        source = self.source()
        with (self.root / 'owned.csv').open() as stream:
            row = next(csv.DictReader(stream))
        volumes = []
        for number in (1, 2):
            payload = io.StringIO(newline='')
            writer = csv.DictWriter(payload, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerow({**row, 'lcsc': f'C{number}'})
            path = self.root / f'volume-{number}.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('parts.csv', payload.getvalue())
            volumes.append(dict(path=path.name, sha256=digest(path),
                                size_bytes=path.stat().st_size, format='zip-csv'))
        return {**source, 'volumes': volumes}

    def test_every_archive_and_database_is_pinned_and_recapture_is_byte_identical(self):
        source = self.archive_source()
        first, second = self.root / 'first', self.root / 'second'
        manifest = capture_catalog(source, self.root, first)
        capture_catalog(source, self.root, second)
        self.assertEqual(manifest['volumes'], source['volumes'])
        for volume in manifest['volumes']:
            self.assertEqual(volume['sha256'], digest(self.root / volume['path']))
        self.assertEqual(manifest['database']['sha256'], digest(first / 'catalog.sqlite3'))
        self.assertEqual(manifest['row_counts']['resistor'], 2)
        loaded = load_catalog(first, digest(first / 'manifest.json'))
        self.assertEqual({row['provenance']['volume'] for row in loaded['rows']},
                         {'volume-1.zip', 'volume-2.zip'})
        for name in ('catalog.sqlite3', 'manifest.json'):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())

    def test_bad_second_archive_aborts_without_publishing_partial_catalog(self):
        source = self.archive_source()
        second = self.root / 'volume-2.zip'
        payload = bytearray(second.read_bytes())
        payload[-1] ^= 1
        second.write_bytes(payload)
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
        failure = catalog_check(destination, expected)
        self.assertEqual(failure['details']['snapshot_manifest_sha256'], expected)
        self.assertIsNone(failure['details']['snapshot_date'])
        self.assertIsNone(failure['details']['snapshot_sha256'])

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

    def test_1k_0603_query_filters_value_package_and_unknown_stock_offline(self):
        import os
        import subprocess
        from toolchain import require_toolchain
        from agent_ee.executor import sandbox_argv
        require_toolchain()
        self.source()
        with (self.root / 'owned.csv').open() as stream:
            row = next(csv.DictReader(stream))
        source = self.source([
            row,
            {**row, 'lcsc': 'C2', 'attributes': '{"resistance":"10 kohm +/-1%"}'},
            {**row, 'lcsc': 'C3', 'package': '0402'},
            {**row, 'lcsc': 'C4', 'stock': ''},
        ])
        snapshot = self.root / 'snapshot'
        capture_catalog(source, self.root, snapshot)
        pin = digest(snapshot / 'manifest.json')
        requirements = dict(schema='agent-ee.part-requirements/1', build_quantity=10,
            stock_floor=100, parts=[dict(id='R1', category='resistor', package='0603',
                quantity_per_board=1, constraints={'resistance': dict(kind='within',
                    nominal='1 kohm', tolerance_percent='1')})])
        work = self.root / 'work'
        work.mkdir()
        program = (
            'import os; from agent_ee.part_selection import select_parts; '
            f'assert os.readlink("/proc/self/ns/net") != {os.readlink("/proc/self/ns/net")!r}; '
            f'r = select_parts("/candidate", {pin!r}, {requirements!r}); '
            'assert r["snapshot_date"] == "2026-10-17"; '
            'p = r["parts"][0]; '
            'assert [c["row"]["lcsc"] for c in p["ranked"]] == ["C1"]; '
            'assert p["chosen"]["stock"] == 123 and p["chosen"]["is_basic"] is True; '
            'assert p["chosen"]["package"] == "0603"; '
            'assert len(p["rejected"]) == 3; print(r["outcome"])')
        completed = subprocess.run(sandbox_argv(snapshot, work, ['/usr/bin/python3', '-c', program]),
                                   capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), 'pass')

    def test_category_table_exposes_typed_si_interval_columns(self):
        import sqlite3
        destination = self.root / 'snapshot'
        capture_catalog(self.source(), self.root, destination)
        with sqlite3.connect(destination / 'catalog.sqlite3') as connection:
            self.assertEqual(connection.execute(
                'SELECT lcsc,resistance_min,resistance_max FROM resistor').fetchall(),
                [('C1', '990', '1010')])

    def test_sqlite_generated_extra_columns_are_not_silently_ignored(self):
        import sqlite3
        from agent_ee.catalog import COLUMNS
        source = self.source()
        with (self.root / 'owned.csv').open() as stream:
            row = next(csv.DictReader(stream))
        database = self.root / 'generated.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE components (' + ','.join(column + ' TEXT' for column in COLUMNS)
                               + ", ignored TEXT GENERATED ALWAYS AS ('extra') VIRTUAL)")
            connection.execute('INSERT INTO components (' + ','.join(COLUMNS) + ') VALUES ('
                               + ','.join('?' for _ in COLUMNS) + ')', tuple(row.values()))
        source['volumes'] = [dict(path=database.name, sha256=digest(database), size_bytes=database.stat().st_size, format='sqlite')]
        with self.assertRaisesRegex(ValueError, 'columns'):
            capture_catalog(source, self.root, self.root / 'generated')

    def test_rehashed_corrupt_sqlite_and_inconsistent_derived_table_are_unknown(self):
        import sqlite3
        from agent_ee.catalog import catalog_check
        destination = self.root / 'snapshot'
        capture_catalog(self.source(), self.root, destination)
        database = destination / 'catalog.sqlite3'
        manifest_path = destination / 'manifest.json'
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                if corrupt:
                    database.write_bytes(b'not a SQLite database')
                else:
                    with sqlite3.connect(database) as connection:
                        connection.execute("UPDATE resistor SET resistance_min='1'")
                manifest = json.loads(manifest_path.read_text())
                manifest['database'].update(sha256=digest(database), size_bytes=database.stat().st_size)
                manifest_path.write_text(json.dumps(manifest))
                result = catalog_check(destination, digest(manifest_path))
                self.assertEqual(result['execution'], 'failed')
                self.assertEqual(result['outcome'], 'unknown')

    def captured(self, name, rows=None, **source_changes):
        source = self.source(rows)
        source.update(source_changes)
        destination = self.root / name
        capture_catalog(source, self.root, destination)
        return destination, digest(destination / 'manifest.json')

    def test_repinned_derived_interval_contradicting_raw_attribute_is_unknown(self):
        from agent_ee.catalog import catalog_check
        from catalog_repin import repin, set_derived
        snapshot, original = self.captured('snapshot')
        # Narrower than the retained raw '1 kohm +/-1%' claim; base and category agree.
        tampered = repin(snapshot, lambda connection: set_derived(
            connection, 'C1', {'resistance': {'min': '999', 'max': '1001', 'unit': 'ohm'}}))
        self.assertNotEqual(tampered, original)
        result = catalog_check(snapshot, tampered)
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
        self.assertRegex(result['details']['error'], 'rederiv')
        with self.assertRaisesRegex(ValueError, 'rederiv'):
            load_catalog(snapshot, tampered)
        self.assertEqual(catalog_check(snapshot, original)['execution'], 'failed')

    def test_repinned_snapshot_with_valid_rederivation_still_passes(self):
        from agent_ee.catalog import catalog_check
        from catalog_repin import repin, set_derived
        snapshot, original = self.captured('snapshot')
        rewritten = repin(snapshot, lambda connection: set_derived(
            connection, 'C1', {'resistance': {'min': '990', 'max': '1010', 'unit': 'ohm'}}))
        self.assertNotEqual(rewritten, original)
        self.assertEqual(catalog_check(snapshot, rewritten)['outcome'], 'pass')
        self.assertEqual(load_catalog(snapshot, rewritten)['rows'][0]['normalized']['resistance'],
                         {'min': '990', 'max': '1010', 'unit': 'ohm'})

    def test_repinned_semantic_row_tampering_is_unknown(self):
        from agent_ee.catalog import catalog_check
        from catalog_repin import repin, set_derived

        def both(assignment, *values):
            return lambda connection: [connection.execute(f'UPDATE {table} SET {assignment}', values)
                                       for table in ('component_base', 'resistor')]
        interval = {'min': '990', 'max': '1010', 'unit': 'ohm'}
        cases = {
            'invented attribute': (lambda connection: set_derived(connection, 'C1', {
                'resistance': interval, 'voltage': {'min': '50', 'max': '50', 'unit': 'V'}}), 'rederiv'),
            'dropped attribute': (lambda connection: set_derived(connection, 'C1', {}), 'rederiv'),
            'invalid unit': (lambda connection: set_derived(connection, 'C1', {
                'resistance': {'min': '0.99', 'max': '1.01', 'unit': 'kohm'}}), 'rederiv'),
            'raw attribute edited': (both('attributes=?', '{"resistance":"1 kohm +/-5%"}'), 'rederiv'),
            'raw attribute not text': (both('attributes=?', '{"resistance":1000}'), 'text mapping'),
            'noncanonical JSON': (both('normalized=?', '{"resistance": {"max":"1010","min":"990","unit":"ohm"}}'), 'canonical JSON'),
            'textual stock': (both("stock='many'"), 'stock'),
            'negative stock': (both('stock=-1'), 'stock'),
            'real stock': (both('stock=123.5'), 'stock'),
            'invalid price': (both("price1='abc'"), 'decimal'),
            'noncanonical price': (both("price1='0.010'"), 'rederiv'),
            'boolean outside 0/1': (both('is_basic=2'), 'boolean'),
            'last_seen after snapshot': (both("last_seen='2026-10-18'"), 'last_seen'),
            'invalid LCSC': (both("lcsc='C01'"), 'LCSC'),
            'unsupported category': (lambda connection: connection.execute(
                "UPDATE component_base SET category='widget'"), 'category'),
            'missing provenance': (both("provenance='{}'"), 'raw_sha256'),
            'foreign provenance volume': (both('provenance=?', json.dumps(
                {'raw_sha256': '0' * 64, 'row': 1, 'volume': 'other.csv'}, separators=(',', ':'), sort_keys=True)), 'provenance'),
            'boolean provenance row': (both('provenance=?', json.dumps(
                {'raw_sha256': '0' * 64, 'row': True, 'volume': 'owned.csv'}, separators=(',', ':'), sort_keys=True)), 'provenance'),
        }
        for number, (label, (edit, reason)) in enumerate(cases.items()):
            with self.subTest(label):
                snapshot, _ = self.captured(f'case-{number}')
                result = catalog_check(snapshot, repin(snapshot, edit))
                self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
                self.assertRegex(result['details']['error'], reason)

    def test_repinned_identity_schema_provenance_and_stale_cutoff_are_unknown(self):
        from agent_ee.catalog import DERIVED_COLUMNS, catalog_check, category_columns
        from catalog_repin import repin
        base = dict(lcsc='C1', mfr='Owned fixture', mpn='R1', package='0603', category='resistor',
                    stock='123', price1='0.01', is_basic='1', is_preferred='0', last_seen='2026-01-01',
                    attributes=json.dumps({'resistance': '1 kohm +/-1%'}))
        rows = [base, {**base, 'lcsc': 'C2', 'mpn': 'R2'}]

        def duplicate_identity(connection):
            for table in ('component_base', 'resistor'):
                columns = DERIVED_COLUMNS + (() if table == 'component_base' else category_columns(table))
                connection.execute(f'CREATE TABLE copy ({",".join(columns)})')
                connection.execute(f'INSERT INTO copy SELECT * FROM {table}')
                connection.execute(f'DROP TABLE {table}')
                connection.execute(f'ALTER TABLE copy RENAME TO {table}')
                connection.execute(f"INSERT INTO {table} SELECT * FROM {table} WHERE lcsc='C1'")

        def shared_provenance(connection):
            for table in ('component_base', 'resistor'):
                connection.execute(f"UPDATE {table} SET provenance=(SELECT provenance FROM component_base "
                                   f"WHERE lcsc='C1') WHERE lcsc='C2'")
        cases = {
            'duplicate identity schema': (duplicate_identity, None, 'schema'),
            'shared provenance record': (shared_provenance, None, 'duplicate'),
            'retained row before stale cutoff': (None, lambda manifest: manifest.update(stale_before='2026-06-01'), 'stale'),
            'negative pruned count': (None, lambda manifest: manifest.update(pruned_rows=-1), 'pruned'),
        }
        for number, (label, (edit_database, edit_manifest, reason)) in enumerate(cases.items()):
            with self.subTest(label):
                snapshot, original = self.captured(f'case-{number}', rows)
                self.assertEqual(catalog_check(snapshot, original)['outcome'], 'pass')
                result = catalog_check(snapshot, repin(snapshot, edit_database, edit_manifest))
                self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
                self.assertRegex(result['details']['error'], reason)

    def test_deeply_nested_snapshot_json_is_a_value_error(self):
        from agent_ee.catalog import strict_json
        with self.assertRaisesRegex(ValueError, 'nest'):
            strict_json('[' * 200000 + ']' * 200000)

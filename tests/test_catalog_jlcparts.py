"""Original source-db-v2 controls, built locally; no supplier data is committed."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.server
import io
import json
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
import zlib

from agent_ee.catalog import capture_catalog, load_catalog, validate_source
from agent_ee.catalog_feed import acquire_catalog, refresh_catalog, run_owned_fetch, source_manifests
from agent_ee.catalog_identity import FORMAT as IDENTITY_FORMAT, SOURCE_SCHEMA as IDENTITY_SOURCE_SCHEMA
from agent_ee.catalog_identity import capture_identity, validate_source as validate_identity_source
from agent_ee.catalog_jlcparts import COMPONENT_COLUMNS, attributes, check_archive, price1
from agent_ee.part_selection import select_parts
from agent_ee.storage import digest


class NativeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def source(self, edit=None, *, member='cache.sqlite3', layout='zip64'):
        db = self.root / 'native.sqlite3'
        db.unlink(missing_ok=True)
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
            connection.execute("INSERT INTO meta VALUES ('format','source-db-v2')")
            numbers = {'lcsc', 'fetched_at', 'present', 'sync_seen', 'joints', 'preferred', 'last_on_stock',
                       'stock', 'rohs', 'assembly'}
            connection.execute('CREATE TABLE jlc_components (' + ','.join(
                c + (' INTEGER' if c in numbers else ' TEXT') for c in COMPONENT_COLUMNS) + ')')
            row = {c: '' for c in COMPONENT_COLUMNS}
            row.update(lcsc=1, fetched_at=int(datetime(2026, 9, 13, tzinfo=timezone.utc).timestamp()),
                       present=1, sync_seen=1, category='Resistors', subcategory='Chip Resistor - Surface Mount',
                       mfr='OWNED-R-1K', package='0603', manufacturer='Owned fixture', joints=2,
                       library_type='base', preferred=0, last_on_stock=1, stock=123, rohs=1, assembly=1,
                       price='100-999:0.02,1-99:0.03,1000-:0.01',
                       datasheet='https://example.invalid/owned.pdf',
                       attributes=json.dumps({'Resistance': '1kΩ', 'Tolerance': '±1%',
                                              'Power(Watts)': '100mW', 'Operating Temperature': '-55℃~+155℃'}))
            rows = [row, {**row, 'lcsc': 2, 'package': '0402'},
                    {**row, 'lcsc': 3, 'attributes': '{"Resistance":"10kΩ","Tolerance":"±1%"}'},
                    {**row, 'lcsc': 4, 'category': 'Unsupported category', 'subcategory': 'Unqualified'},
                    {**row, 'lcsc': 5, 'present': 0},
                    {**row, 'lcsc': 6, 'fetched_at': 1577836800}]
            connection.executemany('INSERT INTO jlc_components VALUES (' + ','.join('?' for _ in COMPONENT_COLUMNS) + ')',
                                   [tuple(r[c] for c in COMPONENT_COLUMNS) for r in rows])
            if edit:
                edit(connection)
        self.split_archive(db.read_bytes(), member=member, layout=layout)
        volumes = [dict(path=p.name, sha256=digest(p), size_bytes=p.stat().st_size, format='jlcparts-v2-stocked-split-zip')
                   for p in (self.root / 'cache.z01', self.root / 'cache.zip')]
        return dict(schema='agent-ee.catalog-source/1', snapshot_date='2026-09-14',
                    source_url='https://example.invalid/pinned/owned-v1', source_commit='owned-v1',
                    currency='USD', stale_before='2025-09-14', volumes=volumes)

    def split_archive(self, payload, *, member, layout='zip64'):
        """Two split volumes of one deflated member starting on disk 0 after the split marker.

        layout zip64 is the 2026-09-14 producer's trailer; zip32 is the plain trailer of the
        2026-11 snapshots (central directory and end record on the last disk, no ZIP64 record)."""
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            entry = zipfile.ZipInfo(member, date_time=(2026, 9, 14, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, payload)
        whole = bytearray(b'PK\x07\x08' + output.getvalue())
        end = struct.unpack('<4sHHHHIIH', whole[-22:])
        central = end[6] + 4
        struct.pack_into('<I', whole, central + 42, 4)
        split = central // 2
        local_central = central - split
        if layout == 'zip32':
            whole[-22:] = struct.pack('<4sHHHHIIH', b'PK\x05\x06', 1, 1, 1, 1, end[5], local_central, 0)
            (self.root / 'cache.z01').write_bytes(whole[:split])
            (self.root / 'cache.zip').write_bytes(whole[split:])
            return
        tail = (struct.pack('<4sQHHIIQQQQ', b'PK\x06\x06', 44, 45, 45, 1, 1, 1, 1, end[5], local_central)
                + struct.pack('<4sIQI', b'PK\x06\x07', 1, local_central + end[5], 2)
                + struct.pack('<4sHHHHIIH', b'PK\x05\x06', 1, 1, 1, 1, end[5], local_central, 0))
        whole[-22:] = tail
        (self.root / 'cache.z01').write_bytes(whole[:split])
        (self.root / 'cache.zip').write_bytes(whole[split:])

    def test_native_capture_is_reproducible_and_uses_existing_normalization(self):
        source = self.source()
        first = capture_catalog(source, self.root, self.root / 'first')
        second = capture_catalog(source, self.root, self.root / 'second')
        self.assertEqual(first, second)
        self.assertEqual(first['volumes'], source['volumes'])
        self.assertEqual(first['pruned_rows'], 1)
        self.assertEqual(first['row_counts']['resistor'], 3)
        self.assertEqual((self.root / 'first/catalog.sqlite3').read_bytes(), (self.root / 'second/catalog.sqlite3').read_bytes())
        loaded = load_catalog(self.root / 'first', digest(self.root / 'first/manifest.json'))
        row = loaded['rows'][0]
        self.assertEqual(row['mfr'], 'Owned fixture')
        self.assertEqual(row['mpn'], 'OWNED-R-1K')
        self.assertEqual((row['stock'], row['is_basic'], row['is_preferred'], row['price1']), (123, True, False, '0.03'))
        self.assertEqual(row['attributes']['upstream:Resistance'], '1kΩ')
        self.assertEqual(row['normalized']['resistance'], dict(min='990', max='1010', unit='ohm'))
        self.assertEqual(row['normalized']['temperature'], dict(min='-55', max='155', unit='degC'))
        self.assertEqual(list(self.root.glob('.catalog-native-*')), [])

    def test_absent_manufacturer_is_unknown_and_zero_stock_is_outside_profile(self):
        def edit(connection):
            connection.execute("UPDATE jlc_components SET manufacturer='' WHERE lcsc=1")
            connection.execute('UPDATE jlc_components SET stock=0 WHERE lcsc=2')
        capture_catalog(self.source(edit), self.root, self.root / 'snapshot')
        rows = load_catalog(self.root / 'snapshot', digest(self.root / 'snapshot/manifest.json'))['rows']
        self.assertEqual([r['lcsc'] for r in rows], ['C1', 'C3'])
        self.assertEqual(rows[0]['mfr'], '')
        self.assertEqual(rows[0]['attributes']['upstream_manufacturer'], '')

    def test_dcdc_mapping(self):
        claims = {'Voltage - Input (Max)': '40V', 'Current - Output': '1000mA',
                  'Synchronous Rectifier': 'Yes', 'Frequency - Switching': '500kHz',
                  'Operating Temperature': '-40℃~+125℃', 'Topology': 'Buck'}

        def edit(connection):
            for number, attributes in ((1, claims), (2, {}), (3, {**claims, 'Synchronous Rectifier': 'No'})):
                connection.execute('UPDATE jlc_components SET category=?, subcategory=?, package=?, attributes=? '
                                   'WHERE lcsc=?', ('Power Management ICs', 'DC-DC Converters', 'SOIC-8',
                                                    json.dumps(attributes), number))
        capture_catalog(self.source(edit), self.root, self.root / 'snapshot')
        snapshot = load_catalog(self.root / 'snapshot', digest(self.root / 'snapshot/manifest.json'))
        self.assertEqual(snapshot['manifest']['row_counts']['switching_regulator'], 3)
        first, unknown, asynchronous = snapshot['rows']
        self.assertEqual(first['category'], 'switching_regulator')
        self.assertEqual(first['package'], 'SOIC-8')
        self.assertEqual(first['normalized'], {
            'input_voltage_max': dict(min='40', max='40', unit='V'),
            'output_current': dict(min='1', max='1', unit='A'), 'topology': {'value': 'sync'},
            'switching_frequency': dict(min='500000', max='500000', unit='Hz'),
            'temperature': dict(min='-40', max='125', unit='degC')})
        for key, value in claims.items():
            self.assertEqual(first['attributes']['upstream:' + key], value)
        self.assertEqual(unknown['normalized'], {})
        self.assertEqual(asynchronous['normalized']['topology'], {'value': 'async'})
        self.assertEqual(first['attributes']['upstream_subcategory'], 'DC-DC Converters')
        self.assertEqual(first['provenance']['row'], 1)

    def test_dcdc_ambiguous_ratings_and_rectification_stay_unknown(self):
        from agent_ee.catalog import normalized_attributes
        claims = {'Voltage - Input (Max)': 'up to 40V', 'Current - Output': '1A per channel',
                  'Synchronous Rectifier': 'Optional', 'Frequency - Switching': '300kHz/600kHz',
                  'Topology': 'Buck'}
        raw = json.loads(attributes(json.dumps(claims), 'switching_regulator'))
        self.assertEqual(normalized_attributes(raw), {})
        self.assertEqual(raw['upstream:Topology'], 'Buck')

    def test_real_sandbox_offline_1k_0603_query(self):
        import os
        from agent_ee.executor import sandbox_argv
        from toolchain import require_toolchain
        require_toolchain()
        capture_catalog(self.source(), self.root, self.root / 'snapshot')
        pin = digest(self.root / 'snapshot/manifest.json')
        work = self.root / 'work'
        work.mkdir()
        program = (
            'import os; from agent_ee.catalog import load_catalog; '
            f'assert os.readlink("/proc/self/ns/net") != {os.readlink("/proc/self/ns/net")!r}; '
            f's=load_catalog("/candidate",{pin!r}); '
            'r=[r for r in s["rows"] if r["package"]=="0603" and '
            'r["normalized"].get("resistance")==dict(min="990",max="1010",unit="ohm")]; '
            'assert len(r)==1; assert r[0]["stock"]==123 and r[0]["is_basic"] is True; '
            'assert s["manifest"]["snapshot_date"]=="2026-09-14"; print(r[0]["lcsc"])')
        run = subprocess.run(sandbox_argv(self.root / 'snapshot', work, ['/usr/bin/python3', '-c', program]),
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), 'C1')

    def test_bad_last_volume_never_publishes(self):
        source = self.source()
        path = self.root / 'cache.zip'
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            capture_catalog(source, self.root, self.root / 'bad')
        self.assertFalse((self.root / 'bad').exists())
        self.assertEqual(list(self.root.glob('.catalog-*')), [])

    def test_missing_columns_unknown_format_and_malformed_rows_fail_closed(self):
        cases = {
            'column': "ALTER TABLE jlc_components DROP COLUMN stock",
            'schema': "UPDATE meta SET value='source-db-v999'",
            'price': "UPDATE jlc_components SET price='free' WHERE lcsc=1",
            'negative stock': 'UPDATE jlc_components SET stock=-1 WHERE lcsc=1',
            'unknown flag': 'UPDATE jlc_components SET preferred=2 WHERE lcsc=1',
            'unknown library': "UPDATE jlc_components SET library_type='maybe' WHERE lcsc=1",
            'attributes': "UPDATE jlc_components SET attributes='[]' WHERE lcsc=1",
            'missing package': "UPDATE jlc_components SET package='' WHERE lcsc=1",
            'future date': 'UPDATE jlc_components SET fetched_at=2000000000 WHERE lcsc=1',
        }
        for name, sql in cases.items():
            with self.subTest(name):
                source = self.source(lambda c: c.execute(sql))
                with self.assertRaises(ValueError):
                    capture_catalog(source, self.root, self.root / 'bad')
                self.assertFalse((self.root / 'bad').exists())
                self.assertEqual(list(self.root.glob('.catalog-*')), [])

    def test_unsafe_member_and_changed_split_layout_fail(self):
        for member in ('../cache.sqlite3', 'other.sqlite3'):
            with self.subTest(member):
                source = self.source(member=member)
                with self.assertRaisesRegex(ValueError, 'member'):
                    capture_catalog(source, self.root, self.root / 'bad')
        source = self.source()
        source['volumes'][0]['path'] = 'cache.z02'
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            validate_source(source)
        source = self.source()
        source['volumes'][0]['format'] = 'csv'
        with self.assertRaisesRegex(ValueError, 'mixed'):
            validate_source(source)

    def test_unknown_quantities_and_prices_remain_unknown(self):
        self.assertEqual(price1(''), '')
        self.assertEqual(price1('1-:1e-6'), '0.000001')
        for invalid in ('0-:1', '1-0:1', '1-:-1', '1-:NaN', '1-2:0.1,1-:0.2'):
            with self.subTest(invalid), self.assertRaises(ValueError):
                price1(invalid)
        raw = json.loads(attributes('{"Resistance":"1kΩ","Voltage Rated":"5V","Voltage - Rated":"50V"}', 'resistor'))
        from agent_ee.catalog import normalized_attributes
        self.assertEqual(normalized_attributes(raw), {})
        self.assertEqual(raw['upstream:Resistance'], '1kΩ')

    def test_regulator_ranges_require_both_source_endpoints(self):
        from agent_ee.catalog import normalized_attributes
        raw = json.loads(attributes(json.dumps({'Voltage - Input (Max)': '18V',
                                               'Voltage - Output (Min/Fixed)': '3.3V'}), 'regulator'))
        self.assertEqual(normalized_attributes(raw), {})
        raw = json.loads(attributes(json.dumps({'Voltage - Input (Min)': '4V',
            'Voltage - Input (Max)': '18V', 'Voltage - Output (Min/Fixed)': '1.2V',
            'Voltage - Output (Max)': '12V', 'Voltage Dropout (Max)': '300mV'}), 'regulator'))
        self.assertEqual(normalized_attributes(raw), {
            'input_voltage': dict(min='4', max='18', unit='V'),
            'output_voltage': dict(min='1.2', max='12', unit='V'),
            'dropout': dict(min='0.3', max='0.3', unit='V')})

    def test_captured_capacitor_and_header_voltage_rating_aliases(self):
        from agent_ee.catalog import normalized_attributes
        for category, name, text in (('capacitor', 'Voltage Rating', '50V'),
                                     ('connector', 'Voltage Rating (Max)', '250V')):
            with self.subTest(category=category):
                raw = json.loads(attributes(json.dumps({name: text}), category))
                self.assertEqual(raw['upstream:' + name], text)
                self.assertEqual(normalized_attributes(raw)['voltage'],
                                 dict(min=text[:-1], max=text[:-1], unit='V'))
        raw = json.loads(attributes(json.dumps({'Voltage Rating': '50V',
                                                'Voltage Rating (Max)': '250V'}), 'capacitor'))
        self.assertNotIn('voltage', normalized_attributes(raw))

    def test_capture_reads_both_split_trailer_layouts_and_refuses_others(self):
        # 2026-09-14 volumes end in a ZIP64 trailer; the 2026-11 snapshots in a plain ZIP trailer.
        for layout in ('zip64', 'zip32'):
            with self.subTest(layout):
                for name in ('cache.z01', 'cache.zip'):
                    (self.root / name).unlink(missing_ok=True)
                source = self.source(layout=layout)
                manifest = capture_catalog(source, self.root, self.root / f'stocked-{layout}')
                self.assertEqual(manifest['row_counts']['resistor'], 3)
                identity = dict(source, schema=IDENTITY_SOURCE_SCHEMA,
                                volumes=[dict(v, format=IDENTITY_FORMAT) for v in source['volumes']])
                del identity['currency']
                capture_identity(identity, self.root, self.root / f'identity-{layout}')
                self.assertTrue((self.root / f'identity-{layout}/manifest.json').is_file())
        # A plain trailer that does not describe one member on the last disk is refused.
        zip_path = self.root / 'cache.zip'
        tail = bytearray(zip_path.read_bytes())
        struct.pack_into('<H', tail, len(tail) - 12, 2)       # total entries
        zip_path.write_bytes(tail)
        source['volumes'][-1]['sha256'] = digest(zip_path)
        with self.assertRaisesRegex(ValueError, 'unsupported split ZIP trailer'):
            capture_catalog(source, self.root, self.root / 'refused')

    def test_member_that_expands_past_its_declared_size_is_refused(self):
        # Review of 5704964d: the member declares 16,384 bytes with the CRC-32 of those bytes, but its
        # stream expands further; zipfile's reader stopped at the declared size and capture kept rows.
        source = self.source(layout='zip32')
        database = (self.root / 'native.sqlite3').read_bytes()
        self.split_archive(database + b'UNDECLARED TRAILING DATA' * 2000, member='cache.sqlite3', layout='zip32')
        paths = [self.root / 'cache.z01', self.root / 'cache.zip']
        whole = bytearray(b''.join(path.read_bytes() for path in paths))
        central, local = whole.index(b'PK\x01\x02'), whole.index(b'PK\x03\x04')
        for offset in (central + 16, local + 14):
            struct.pack_into('<I', whole, offset, zlib.crc32(database))
        for offset in (central + 24, local + 22):
            struct.pack_into('<I', whole, offset, len(database))
        split = paths[0].stat().st_size
        paths[0].write_bytes(whole[:split])
        paths[1].write_bytes(whole[split:])
        for volume, path in zip(source['volumes'], paths):
            volume.update(sha256=digest(path), size_bytes=path.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'native ZIP member expands past its declared size'):
            check_archive(source['volumes'], self.root, self.root)
        with self.assertRaisesRegex(ValueError, 'native ZIP member expands past its declared size'):
            capture_catalog(source, self.root, self.root / 'captured')
        self.assertFalse((self.root / 'captured').exists())

    def test_fetch_helper_runs_only_its_own_fetch_child(self):
        marker = self.root / 'launched'
        command = [sys.executable, '-c', f'open({str(marker)!r}, "w")']
        for args in ((command, self.root / 'request.json', self.root, 5),
                     ('--other', self.root / 'request.json', self.root, 5),
                     ('--fetch', str(self.root / 'request.json'), self.root, 5)):
            with self.subTest(args[0]), self.assertRaisesRegex(ValueError, 'only the catalog_feed fetch child'):
                run_owned_fetch(*args)
        self.assertFalse(marker.exists())

    def test_refresh_writes_records_in_the_form_of_the_dated_2026_09_14_fixtures(self):
        # The 2026-09-14 records stay as fixtures. The refresh's manifest writer, given their
        # commit, date and volumes, reproduces both files byte for byte as JSON values.
        root = Path(__file__).resolve().parents[1] / 'catalog-sources'
        stocked = json.loads((root / 'jlcparts-2026-09-14.json').read_text())
        identity = json.loads((root / 'jlcparts-identity-2026-09-14.json').read_text())
        validate_source(stocked)
        validate_identity_source(identity)
        self.assertEqual(stocked['snapshot_date'], '2026-09-14')   # the date its file name carries
        volumes = [{k: v[k] for k in ('path', 'sha256', 'size_bytes')} for v in stocked['volumes']]
        self.assertEqual(source_manifests('https://raw.githubusercontent.com', 'yaqwsx/jlcparts',
                                          stocked['source_commit'], stocked['snapshot_date'], volumes),
                         (stocked, identity))

    @contextmanager
    def upstream(self):
        """A local stand-in for the GitHub API and raw host. state['commits'] maps a commit to its
        committer date, data/ files and optional served overrides; state['head'] is gh-pages."""
        state = {'head': None, 'commits': {}, 'raw_requests': 0}
        blob = lambda payload: hashlib.sha1(b'blob %d\0' % len(payload) + payload).hexdigest()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                path, _, query = self.path.partition('?')
                parts = path.strip('/').split('/')
                if path == '/repos/owned/jlcparts/branches/gh-pages':
                    head = state['head']
                    body = json.dumps({'name': 'gh-pages', 'commit': {
                        'sha': head, 'commit': {'committer': {'date': state['commits'][head]['date']}}}}).encode()
                elif path == '/repos/owned/jlcparts/contents/data':
                    files = state['commits'][query.removeprefix('ref=')]['files']
                    body = json.dumps([{'name': name, 'type': 'file', 'size': len(data), 'sha': blob(data)}
                                       for name, data in files.items()]
                                      + [{'name': 'index.json', 'type': 'file', 'size': 2, 'sha': blob(b'{}')},
                                         {'name': 'img', 'type': 'dir', 'size': 0, 'sha': '0' * 40}]).encode()
                elif len(parts) == 5 and parts[:2] == ['owned', 'jlcparts'] and parts[3] == 'data':
                    state['raw_requests'] += 1
                    commit = state['commits'][parts[2]]
                    body = commit.get('served', {}).get(parts[4], commit['files'].get(parts[4]))
                else:
                    body = None
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}', state
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def refresh(self, base):
        return refresh_catalog(self.root / 'cache', timeout=30, api=base, raw=base, repository='owned/jlcparts')

    def test_refresh_fetches_the_current_snapshot_records_it_and_selection_names_it(self):
        self.source(layout='zip32')
        files = {name: (self.root / name).read_bytes() for name in ('cache.z01', 'cache.zip')}
        first, second = 'a1' * 20, 'b2' * 20
        with self.upstream() as (base, state):
            state['commits'][first] = {'date': '2026-11-02T21:30:00Z', 'files': files}
            state['head'] = first
            directory, record = self.refresh(base)
            fetched = state['raw_requests']
            # The same head again is verified from the cache, not fetched again.
            self.assertEqual(self.refresh(base), (directory, record))
            self.assertEqual(state['raw_requests'], fetched)
            # Upstream moved: the new snapshot is dated by its commit, beside the first.
            state['commits'][second] = {'date': '2026-11-03T01:00:00+01:00', 'files': files}
            state['head'] = second
            later, later_record = self.refresh(base)
        self.assertEqual(fetched, 2)
        self.assertEqual(directory.name, '2026-11-02-' + first[:12])
        self.assertEqual((later.name, later_record['source']['commit']), ('2026-11-03-' + second[:12], second))
        self.assertTrue(directory.is_dir())
        self.assertEqual({key: record[key] for key in ('source', 'snapshot_date', 'size_bytes', 'sha256',
                                                       'repository', 'branch', 'committed_at')},
                         {'source': {'url': f'{base}/owned/jlcparts/{first}/data', 'commit': first},
                          'snapshot_date': '2026-11-02', 'size_bytes': sum(map(len, files.values())),
                          'sha256': hashlib.sha256(files['cache.z01'] + files['cache.zip']).hexdigest(),
                          'repository': 'https://github.com/owned/jlcparts', 'branch': 'gh-pages',
                          'committed_at': '2026-11-02T21:30:00Z'})
        self.assertEqual(record['source_manifest_sha256'], digest(directory / 'source-manifest.json'))
        source = json.loads((directory / 'source-manifest.json').read_text())
        self.assertEqual((source['source_commit'], source['stale_before']), (first, '2025-11-02'))
        self.assertEqual([(v['path'], v['sha256']) for v in source['volumes']],
                         [(name, hashlib.sha256(data).hexdigest()) for name, data in files.items()])
        # A run that captures and selects from it records the snapshot: the selection's manifest
        # hash binds the refreshed commit and volumes, and its date is the snapshot's.
        capture_catalog(source, directory, self.root / 'snapshot')
        manifest_sha256 = digest(self.root / 'snapshot/manifest.json')
        selection = select_parts(self.root / 'snapshot', manifest_sha256, dict(
            schema='agent-ee.part-requirements/1', build_quantity=1, stock_floor=0, parts=[dict(
                id='R1', category='resistor', package='0603', quantity_per_board=1, constraints={
                    'resistance': {'kind': 'within', 'nominal': '1 kohm', 'tolerance_percent': '1'}})]))
        self.assertEqual(selection['outcome'], 'pass')
        self.assertEqual((selection['snapshot_manifest_sha256'], selection['snapshot_date']),
                         (manifest_sha256, record['snapshot_date']))
        captured = load_catalog(self.root / 'snapshot', manifest_sha256)['manifest']
        self.assertEqual((captured['source_commit'], captured['volumes']), (first, source['volumes']))

    def test_refresh_publishes_nothing_unless_the_volumes_are_the_commits_and_importable(self):
        self.source(layout='zip32')
        files = {name: (self.root / name).read_bytes() for name in ('cache.z01', 'cache.zip')}
        tampered = bytes([files['cache.zip'][0] ^ 1]) + files['cache.zip'][1:]
        self.source(member='other.sqlite3', layout='zip32')
        renamed = {name: (self.root / name).read_bytes() for name in ('cache.z01', 'cache.zip')}
        # Review of 5704964d: a wrong CRC-32 passed the refresh probe, which read only 16 bytes.
        bad_crc = bytearray(files['cache.zip'])
        central = bad_crc.index(b'PK\x01\x02')
        struct.pack_into('<I', bad_crc, central + 16, struct.unpack_from('<I', bad_crc, central + 16)[0] ^ 1)
        for case, commit, error in (
                ('bytes differ from the listed Git blob', {'files': files, 'served': {'cache.zip': tampered}},
                 (RuntimeError, 'catalog download failed')),
                ('no final cache.zip volume', {'files': {'cache.z01': files['cache.z01']}},
                 (RuntimeError, 'catalog download failed')),
                ('an archive capture cannot read', {'files': renamed},
                 (ValueError, 'cannot be imported: unsupported or oversized native ZIP member')),
                ('a member whose CRC-32 differs', {'files': {'cache.z01': files['cache.z01'], 'cache.zip': bytes(bad_crc)}},
                 (ValueError, 'cannot be imported: native ZIP member CRC-32 differs from the central directory'))):
            with self.subTest(case), self.upstream() as (base, state):
                state['commits']['c3' * 20] = dict(commit, date='2026-11-02T00:00:00Z')
                state['head'] = 'c3' * 20
                with self.assertRaisesRegex(*error):
                    self.refresh(base)
                self.assertEqual(list((self.root / 'cache/jlcparts').iterdir()), [])

    def test_cache_hit_must_be_the_resolved_head_with_unchanged_bytes(self):
        self.source(layout='zip32')
        files = {name: (self.root / name).read_bytes() for name in ('cache.z01', 'cache.zip')}
        first, second = 'a1' * 20, 'b2' * 20
        with self.upstream() as (base, state):
            state['commits'][first] = {'date': '2026-11-02T21:30:00Z', 'files': files}
            state['head'] = first
            early, _ = self.refresh(base)
            state['commits'][second] = {'date': '2026-11-03T00:00:00Z', 'files': files}
            state['head'] = second
            later, later_record = self.refresh(base)
            # Review of ec8976ca: an older, internally valid snapshot restored under the newer head's name.
            saved = {path.name: path.read_bytes() for path in later.iterdir()}
            for path in early.iterdir():
                shutil.copy2(path, later / path.name)
            with self.assertRaisesRegex(ValueError, f'holds commit {first} dated 2026-11-02, not the resolved '
                                                    f'upstream head {second} dated 2026-11-03'):
                self.refresh(base)
            for name, payload in saved.items():
                (later / name).write_bytes(payload)
            self.assertEqual(self.refresh(base), (later, later_record))
            # Changed cached bytes, or consistently changed manifests, are refused rather than returned.
            flip = lambda b: bytes([b[0] ^ 1]) + b[1:]
            cutoff = lambda b: b.replace(b'"2025-11-03"', b'"2025-11-02"')
            for case, edits, message in (
                    ('volume bytes', {'cache.z01': flip}, 'cache.z01 size/hash differs from its manifest'),
                    ('manifests', {'source-manifest.json': cutoff, 'identity-source-manifest.json': cutoff},
                     'differs from its snapshot.json')):
                with self.subTest(case):
                    try:
                        for name, edit in edits.items():
                            self.assertNotEqual(edit(saved[name]), saved[name])
                            (later / name).write_bytes(edit(saved[name]))
                        with self.assertRaisesRegex(ValueError, message):
                            self.refresh(base)
                    finally:
                        # Restore even when the case fails, so one case cannot change the next.
                        for name in edits:
                            (later / name).write_bytes(saved[name])

    @contextmanager
    def server(self, source, mode='ok'):
        root = self.root

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                payload = (root / self.path.rsplit('/', 1)[-1]).read_bytes()
                self.send_response(200)
                if mode == 'length':
                    self.send_header('Content-Length', str(len(payload) + 1))
                self.end_headers()
                if mode == 'stall':
                    time.sleep(2)
                    return
                try:
                    self.wfile.write(payload + (b'extra' if mode == 'overflow' else b''))
                except BrokenPipeError:
                    pass

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield {**source, 'source_url': f'http://127.0.0.1:{server.server_port}/owned-v1'}
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_bounded_host_acquisition_and_capture_end_to_end(self):
        with self.server(self.source()) as source:
            acquire_catalog(source, self.root / 'download', timeout=10)
        for v in source['volumes']:
            self.assertEqual(digest(self.root / 'download' / v['path']), v['sha256'])
        capture_catalog(source, self.root / 'download', self.root / 'snapshot')
        self.assertEqual(load_catalog(self.root / 'snapshot', digest(self.root / 'snapshot/manifest.json'))['rows'][0]['stock'], 123)
        with self.assertRaises(FileExistsError):
            acquire_catalog(source, self.root / 'download', timeout=10)

    def test_acquisition_hash_size_and_process_deadline_leave_nothing_published(self):
        for mode in ('hash', 'length', 'overflow', 'stall'):
            with self.subTest(mode):
                source = self.source()
                if mode == 'hash':
                    source['volumes'][-1]['sha256'] = '0' * 64
                with self.server(source, mode) as served:
                    with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                        acquire_catalog(served, self.root / 'bad', timeout=1 if mode == 'stall' else 10)
                self.assertFalse((self.root / 'bad').exists())
                self.assertEqual(list(self.root.glob('.catalog-download-*')), [])


if __name__ == '__main__':
    unittest.main()

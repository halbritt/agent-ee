"""Identity index and Stage 2 MPN resolution over owned synthetic split archives.

Every MPN, manufacturer, URL, stock figure and LCSC id below is an original test
label; no supplier catalog rows are committed or downloaded.
"""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from agent_ee.catalog import capture_catalog, load_catalog
from unittest import mock

from agent_ee import catalog_identity
from agent_ee.catalog_bouni import KNOWN_FAMILIES, BouniRefusal, passive_boundary
from agent_ee.catalog_identity import (FORMAT, GENERIC_FAMILIES, capture_identity, generic, load_identity_index, main,
                                       resolve_decisions)
from agent_ee.part_selection import VALUE_ATTRIBUTES
from agent_ee.catalog_jlcparts import COMPONENT_COLUMNS
from agent_ee.jlc_stock_capture import response_datasheet_url
from agent_ee.storage import digest, write_json

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_catalog_jlcparts
from test_jlc_stock_capture import row as jlc_row, search

STAMP = int(datetime(2026, 9, 13, tzinfo=timezone.utc).timestamp())
NUMBERS = {'lcsc', 'fetched_at', 'present', 'sync_seen', 'joints', 'preferred', 'last_on_stock',
           'stock', 'rohs', 'assembly'}
# (lcsc, category, subcategory, mpn, package, library_type, preferred, stock, present, datasheet)
PARTS = (
    (11, 'Embedded Processors & Controllers', 'Microcontrollers', 'OWNED-MCU32-A', 'LQFP-48', 'expand', 1, 900, 1,
     'https://example.invalid/owned-mcu32.pdf'),
    (12, 'Embedded Processors & Controllers', 'Microcontrollers', 'OWNED-MCU32-ATR', 'LQFP-48', 'expand', 0, 40, 1,
     'https://example.invalid/owned-mcu32-tr.pdf'),
    (13, 'Interface ICs', 'CAN Transceivers', 'OWNED-CANX-1', 'SOIC-8', 'base', 0, 5000, 1,
     'https://example.invalid/owned-canx.pdf'),
    (14, 'Interface ICs', 'CAN Transceivers', 'owned-canx-1', 'DFN-8', 'expand', 0, 0, 1, ''),
    (15, 'Crystals, Oscillators, Resonators', 'Crystals', 'OWNED-XTAL-8M', 'SMD3225-4P', 'base', 0, 7000, 1,
     'ftp://example.invalid/owned-xtal.pdf'),
    (16, 'RF And Wireless', 'GNSS Modules', 'OWNED-GNSS-M1', 'LCC-24', 'expand', 0, 12, 0,
     'https://example.invalid/owned-gnss.pdf'),
    (17, 'Connectors', 'Board To Board Connectors', 'OWNED-B2B-40', 'SMD,P=0.5mm', 'expand', 0, 3, 1,
     'https://example.invalid/owned-b2b.pdf'),
    (18, 'Resistors', 'Chip Resistor - Surface Mount', 'OWNED-R-1K', '0603', 'base', 0, 100, 1,
     'https://example.invalid/owned-r.pdf'),
    (19, 'Capacitors', 'MLCC', 'OWNED-C-100N', '0402', 'base', 0, 100, 1, ''),
    (20, 'Sensors', 'Temperature Sensors', 'OWNED-TEMP-1', 'SOT-23', 'expand', 0, 50, 1,
     'https://example.invalid/owned-temp.pdf'),
)


def decisions(*rows):
    return {'schema': 'agent-ee.part-decisions/1', 'spec': {},
            'declaration': {'section': 'Parts', 'exclusions': [], 'decisions': [
                {'requirement_id': rid, 'function': 'f', 'mpn': mpn, 'lcsc': lcsc,
                 'symbol_hint': None, 'footprint_hint': None} for rid, mpn, lcsc in rows]}}


def narrowing(*entries):
    return {'schema': 'agent-ee.catalog-identity-narrowing/1', 'entries': [
        {'requirement_id': rid, 'mpn': mpn, 'package': package, 'lcsc': lcsc, 'reason': 'owner-reviewed'}
        for rid, mpn, package, lcsc in entries]}


class IdentityIndexTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def source(self, edit=None, *, fmt=FORMAT):
        db = self.root / 'native.sqlite3'
        db.unlink(missing_ok=True)
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
            connection.execute("INSERT INTO meta VALUES ('format','source-db-v2')")
            connection.execute('CREATE TABLE jlc_components (' + ','.join(
                c + (' INTEGER' if c in NUMBERS else ' TEXT') for c in COMPONENT_COLUMNS) + ')')
            rows = []
            for lcsc, category, sub, mpn, package, library, preferred, stock, present, url in PARTS:
                native = {c: '' for c in COMPONENT_COLUMNS}
                native.update(lcsc=lcsc, fetched_at=STAMP, present=present, sync_seen=1, category=category,
                              subcategory=sub, mfr=mpn, package=package, manufacturer='Owned maker', joints=8,
                              library_type=library, preferred=preferred, last_on_stock=1, stock=stock, rohs=1,
                              assembly=1, price='1-:0.5', datasheet=url, attributes='{}')
                rows.append(native)
            rows.append({**rows[-1], 'lcsc': 21, 'mfr': 'OWNED-STALE-1', 'fetched_at': 1577836800})
            connection.executemany('INSERT INTO jlc_components VALUES (' + ','.join('?' for _ in COMPONENT_COLUMNS) + ')',
                                   [tuple(r[c] for c in COMPONENT_COLUMNS) for r in rows])
            if edit:
                edit(connection)
        test_catalog_jlcparts.NativeCatalogTests.split_archive(self, db.read_bytes(), member='cache.sqlite3')
        volumes = [dict(path=p.name, sha256=digest(p), size_bytes=p.stat().st_size, format=fmt)
                   for p in (self.root / 'cache.z01', self.root / 'cache.zip')]
        return dict(schema='agent-ee.catalog-identity-source/1', snapshot_date='2026-09-14',
                    source_url='https://example.invalid/pinned/owned-v1', source_commit='owned-v1',
                    stale_before='2025-09-14', volumes=volumes)

    def capture(self, name='index', edit=None):
        capture_identity(self.source(edit), self.root, self.root / name)
        return self.root / name, digest(self.root / name / 'manifest.json')

    def test_capture_is_deterministic_hash_sealed_and_excludes_generic_passives(self):
        source = self.source()
        first = capture_identity(source, self.root, self.root / 'first')
        second = capture_identity(source, self.root, self.root / 'second')
        self.assertEqual(first, second)
        self.assertEqual((self.root / 'first/identity.sqlite3').read_bytes(),
                         (self.root / 'second/identity.sqlite3').read_bytes())
        self.assertEqual(first['volumes'], source['volumes'])
        self.assertEqual(first['pruned_rows'], {'generic': 2, 'stale': 1})
        self.assertEqual(first['row_count'], 8)
        self.assertEqual(list(self.root.glob('.catalog-*')), [])
        with load_identity_index(self.root / 'first', digest(self.root / 'first/manifest.json')) as index:
            row = index.by_lcsc('C11')
            self.assertEqual({k: row[k] for k in ('lcsc', 'mpn', 'mfr', 'package', 'library_tier', 'stock',
                                                  'present', 'last_seen', 'datasheet_url')},
                             dict(lcsc='C11', mpn='OWNED-MCU32-A', mfr='Owned maker', package='LQFP-48',
                                  library_tier='Preferred', stock=900, present=1, last_seen='2026-09-13',
                                  datasheet_url='https://example.invalid/owned-mcu32.pdf'))
            self.assertEqual(row['provenance']['volume'], 'cache.zip')
            self.assertEqual(row['provenance']['row'], 1)
            self.assertEqual(index.by_lcsc('C12')['library_tier'], 'Extended')
            self.assertEqual(index.by_lcsc('C13')['library_tier'], 'Basic')
            self.assertIsNone(index.by_lcsc('C14')['datasheet_url'])
            self.assertEqual(index.by_lcsc('C16')['present'], 0)
            self.assertIsNone(index.by_lcsc('C18'))
            self.assertIsNone(index.by_lcsc('C21'))

    def test_passive_boundary_both_sides(self):
        """R/C/L value-resolved rows are pruned; every family not established by value is kept."""
        self.assertEqual(GENERIC_FAMILIES, KNOWN_FAMILIES)
        self.assertEqual(GENERIC_FAMILIES, tuple(VALUE_ATTRIBUTES))
        extra = ((30, 'Inductors, Coils, Chokes', 'Power Inductors'),
                 (31, 'Inductors, Coils, Chokes', 'Ferrite Beads'),
                 (32, 'Inductors, Coils, Chokes', 'Common Mode Filters'),
                 (33, 'Resistors', 'Resistor Networks & Arrays'),
                 (34, 'Resistors', 'NTC Thermistors'),
                 (35, 'Capacitors', 'Trimmer Capacitors'),
                 (36, 'Diodes', 'Schottky Diodes'),
                 (37, 'LED Indication - Discrete', 'Light Emitting Diodes (LED)'),
                 (38, 'Filters', 'Ferrite Beads'),
                 (39, 'Circuit Protection', 'Resettable Fuses'),
                 (40, 'Voltage Regulators - Linear, Low Drop Out (LDO) Regulators', 'Linear Regulators'))

        def edit(connection):
            for lcsc, category, sub in extra:
                connection.execute('INSERT INTO jlc_components SELECT * FROM jlc_components WHERE lcsc=13')
                connection.execute('UPDATE jlc_components SET lcsc=?, category=?, subcategory=?, mfr=? '
                                   'WHERE rowid=(SELECT max(rowid) FROM jlc_components)',
                                   (lcsc, category, sub, f'OWNED-BOUNDARY-{lcsc}'))
        path, pin = self.capture(edit=edit)
        manifest = json.loads((path / 'manifest.json').read_text())
        self.assertEqual(manifest['generic_families_excluded'], ['resistor', 'capacitor', 'inductor'])
        self.assertEqual(manifest['pruned_rows'], {'generic': 3, 'stale': 1})
        with load_identity_index(path, pin) as index:
            for lcsc in ('C18', 'C19', 'C30'):
                self.assertIsNone(index.by_lcsc(lcsc), lcsc)
            for lcsc in ['C15', 'C17'] + [f'C{n}' for n in range(31, 41)]:
                self.assertEqual(index.by_lcsc(lcsc)['lcsc'], lcsc)

    def test_unqualified_passive_subfamilies_are_retained(self):
        """Unknown, empty and unlisted R/C/L subcategories are non-generic (fail closed)."""
        cases = ((50, 'Inductors, Coils, Chokes', 'Common-Mode Chokes'), (51, 'Inductors', 'Coupled Inductors'),
                 (52, 'Resistors', 'Unqualified owned subfamily'), (53, 'Capacitors', ''),
                 (54, 'Inductors', 'RF Baluns'))

        def edit(connection):
            for lcsc, category, sub in cases:
                connection.execute('INSERT INTO jlc_components SELECT * FROM jlc_components WHERE lcsc=13')
                connection.execute('UPDATE jlc_components SET lcsc=?, category=?, subcategory=?, mfr=? '
                                   'WHERE rowid=(SELECT max(rowid) FROM jlc_components)',
                                   (lcsc, category, sub, f'OWNED-ADVERSARY-{lcsc}'))
        path, pin = self.capture(edit=edit)
        with load_identity_index(path, pin) as index:
            for lcsc, _, _ in cases:
                self.assertEqual(index.by_lcsc(f'C{lcsc}')['lcsc'], f'C{lcsc}')
        boundary = passive_boundary(GENERIC_FAMILIES)
        self.assertTrue(generic('Inductors', 'Power Inductors', boundary))
        self.assertFalse(generic('Inductors', 'Common-Mode Chokes', boundary))
        self.assertFalse(generic('Capacitors', '', boundary))

    def test_unqualified_subfamily_keeps_ambiguity(self):
        def edit(connection):
            for lcsc, sub, package in ((60, 'Common Mode Filters', 'OWNED-PKG-A'),
                                       (61, 'Common-Mode Chokes', 'OWNED-PKG-B')):
                connection.execute('INSERT INTO jlc_components SELECT * FROM jlc_components WHERE lcsc=13')
                connection.execute('UPDATE jlc_components SET lcsc=?, category=?, subcategory=?, mfr=?, package=? '
                                   'WHERE rowid=(SELECT max(rowid) FROM jlc_components)',
                                   (lcsc, 'Inductors', sub, 'OWNED-COMMONMODE-A', package))
        path, pin = self.capture(edit=edit)
        with load_identity_index(path, pin) as index:
            entry = resolve_decisions(index, decisions(('choke', 'OWNED-COMMONMODE-A', None)))['entries'][0]
        self.assertEqual(entry['status'], 'ambiguous')

    def test_passive_boundary_is_enforced_at_capture_and_load(self):
        for families, reason in ((('resistor', 'capacitor', 'inductor', 'diode'), 'boundary_invalid'),
                                 (('resistor', 'ferrite_bead'), 'boundary_invalid'), ((), 'boundary_invalid')):
            with self.subTest(families), mock.patch.object(catalog_identity, 'GENERIC_FAMILIES', families):
                with self.assertRaises(BouniRefusal) as ctx:
                    capture_identity(self.source(), self.root, self.root / 'widened')
                self.assertEqual(ctx.exception.reason, reason)
                self.assertFalse((self.root / 'widened').exists())
        for name, families, error in (('widened', ['resistor', 'capacitor', 'inductor', 'led'], 'boundary_invalid'),
                                      ('string', 'resistor', 'boundary_invalid'),
                                      ('narrowed', ['resistor', 'capacitor'], 'schema or profile')):
            with self.subTest(name):
                path, _ = self.capture(name)
                manifest = json.loads((path / 'manifest.json').read_text())
                manifest['generic_families_excluded'] = families
                write_json(path / 'manifest.json', manifest)
                with self.assertRaisesRegex(ValueError, error):
                    load_identity_index(path, digest(path / 'manifest.json'))

    def test_existing_catalog_profile_and_row_hashes_are_unchanged(self):
        """The identity profile is separate; the stocked catalog/1 import is untouched."""
        source = self.source(fmt='jlcparts-v2-stocked-split-zip')
        manifest = capture_catalog(dict(source, schema='agent-ee.catalog-source/1', currency='USD'),
                                   self.root, self.root / 'catalog')
        snapshot = load_catalog(self.root / 'catalog', digest(self.root / 'catalog/manifest.json'))
        self.assertEqual([r['lcsc'] for r in snapshot['rows']], ['C17', 'C18', 'C19'])
        self.assertEqual(manifest['schema'], 'agent-ee.catalog/1')
        self.assertNotIn('identity', manifest)
        # Profiles cannot be crossed: the identity importer refuses stocked volumes and vice versa.
        with self.assertRaisesRegex(ValueError, 'mixed native'):
            capture_identity(source, self.root, self.root / 'crossed')
        identity_source = self.source()
        with self.assertRaises(ValueError):
            capture_catalog(dict(identity_source, schema='agent-ee.catalog-source/1', currency='USD'),
                            self.root, self.root / 'crossed')
        self.assertFalse((self.root / 'crossed').exists())

    def test_tampered_index_is_refused(self):
        path, pin = self.capture()
        with self.assertRaisesRegex(ValueError, 'manifest hash'):
            load_identity_index(path, '0' * 64)
        database = path / 'identity.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE identity SET datasheet_url='https://evil.invalid/x.pdf' WHERE lcsc='C13'")
        with self.assertRaisesRegex(ValueError, 'size/hash'):
            load_identity_index(path, pin)
        # Repinning forged bytes still fails row identity rederivation.
        manifest = json.loads((path / 'manifest.json').read_text())
        manifest['database'].update(sha256=digest(database), size_bytes=database.stat().st_size)
        write_json(path / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'row identity'):
            load_identity_index(path, digest(path / 'manifest.json'))

    def test_forged_rows_fail_closed_after_repin(self):
        for name, sql, message in (
                ('key', "UPDATE identity SET mpn_key='OTHER' WHERE lcsc='C13'", 'MPN key'),
                ('generic', "UPDATE identity SET category='Capacitors', subcategory='MLCC' WHERE lcsc='C13'", 'outside profile'),
                ('tier', "UPDATE identity SET library_tier='Gold' WHERE lcsc='C13'", 'outside profile'),
                ('date', "UPDATE identity SET last_seen='2026-09-15' WHERE lcsc='C13'", 'snapshot window'),
                ('stock', "UPDATE identity SET stock=-1 WHERE lcsc='C13'", 'stock/presence')):
            with self.subTest(name):
                path, _ = self.capture(name)
                database = path / 'identity.sqlite3'
                with sqlite3.connect(database) as connection:
                    connection.execute(sql)
                manifest = json.loads((path / 'manifest.json').read_text())
                manifest['database'].update(sha256=digest(database), size_bytes=database.stat().st_size)
                write_json(path / 'manifest.json', manifest)
                with self.assertRaisesRegex(ValueError, message):
                    load_identity_index(path, digest(path / 'manifest.json'))

    def test_bad_volume_and_malformed_native_rows_publish_nothing(self):
        source = self.source()
        raw = (self.root / 'cache.zip').read_bytes()
        (self.root / 'cache.zip').write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            capture_identity(source, self.root, self.root / 'bad')
        for name, sql in (('tier', "UPDATE jlc_components SET library_type='gold' WHERE lcsc=13"),
                          ('stock', 'UPDATE jlc_components SET stock=-5 WHERE lcsc=13'),
                          ('text', 'UPDATE jlc_components SET datasheet=NULL WHERE lcsc=13'),
                          ('future', f'UPDATE jlc_components SET fetched_at={STAMP + 3 * 86400} WHERE lcsc=13')):
            with self.subTest(name):
                source = self.source(lambda c: c.execute(sql))
                with self.assertRaises(ValueError):
                    capture_identity(source, self.root, self.root / 'bad')
        self.assertFalse((self.root / 'bad').exists())
        self.assertEqual(list(self.root.glob('.catalog-*')), [])

    def test_resolution_statuses(self):
        path, pin = self.capture()
        document = decisions(
            ('R1', 'OWNED-CANX-1', None),      # two exact matches (case-insensitive): ambiguous
            ('R2', 'OWNED-CANX-1', 'C13'),     # declared LCSC verified by exact MPN
            ('R3', 'OWNED-MCU32-A', None),     # one exact match; the TR prefix variant is not considered
            ('R4', 'OWNED-MCU32', None),       # prefix-only: requires an explicit selection
            ('R5', 'OWNED-NOT-THERE', None),   # unknown, never absence
            ('R6', 'OWNED-TEMP-1', 'C11'),     # declared LCSC names another MPN
            ('R7', None, 'C17'),               # LCSC-only declaration present in the index
            ('R8', None, 'C999'),              # LCSC-only declaration absent from the index
            ('R9', 'OWNED-XTAL-8M', None),     # resolved, but its URL is not HTTP(S)
            ('R10', 'OWNED-CANX-1', 'C18'))    # declared LCSC outside the profile: unknown
        with load_identity_index(path, pin) as index:
            result = resolve_decisions(index, document)
            again = resolve_decisions(index, copy.deepcopy(document))
        self.assertEqual(result, again)
        status = {e['requirement_id']: (e['status'], e['lcsc']) for e in result['entries']}
        self.assertEqual(status, {
            'R1': ('ambiguous', None), 'R2': ('verified', 'C13'), 'R3': ('resolved', 'C11'),
            'R4': ('ambiguous', None), 'R5': ('unknown', None), 'R6': ('conflict', None),
            'R7': ('declared_lcsc', 'C17'), 'R8': ('unknown', None), 'R9': ('resolved', 'C15'),
            'R10': ('unknown', None)})
        entries = {e['requirement_id']: e for e in result['entries']}
        self.assertEqual([c['lcsc'] for c in entries['R1']['candidates']], ['C13', 'C14'])
        self.assertEqual((entries['R4']['match'], [c['lcsc'] for c in entries['R4']['candidates']]),
                         ('prefix', ['C11', 'C12']))
        self.assertEqual(entries['R5']['candidates'], [])
        self.assertEqual(entries['R9']['datasheet_url'], {'url': 'ftp://example.invalid/owned-xtal.pdf',
                                                          'status': 'invalid'})
        self.assertEqual(entries['R1']['datasheet_url'], {'url': None, 'status': None})
        self.assertEqual(result['stock_capture_ids'], ['C11', 'C13', 'C15', 'C17'])
        self.assertEqual(result['datasheet_bindings'], [
            {'id': 'C11', 'datasheet_url': 'https://example.invalid/owned-mcu32.pdf'},
            {'id': 'C13', 'datasheet_url': 'https://example.invalid/owned-canx.pdf'},
            {'id': 'C17', 'datasheet_url': 'https://example.invalid/owned-b2b.pdf'}])
        self.assertEqual(result['index']['manifest_sha256'], pin)
        # The outputs are the existing capture contracts' inputs.
        from agent_ee.datasheet_capture import _parts
        from agent_ee.jlc_stock_capture import _ids
        self.assertEqual(_parts(result['datasheet_bindings']), result['datasheet_bindings'])
        self.assertEqual(_ids(result['stock_capture_ids']), result['stock_capture_ids'])

    def test_narrowing_by_package_or_explicit_selection(self):
        path, pin = self.capture()
        document = decisions(('R1', 'OWNED-CANX-1', None), ('R4', 'OWNED-MCU32', None))
        with load_identity_index(path, pin) as index:
            narrowed = resolve_decisions(index, document, narrowing(
                ('R1', 'OWNED-CANX-1', 'DFN-8', None), ('R4', 'OWNED-MCU32', None, 'C12')))
            status = {e['requirement_id']: (e['status'], e['lcsc']) for e in narrowed['entries']}
            self.assertEqual(status, {'R1': ('resolved', 'C14'), 'R4': ('selected', 'C12')})
            # C14 has no datasheet URL: it is still a stock-capture id, but no binding is invented.
            self.assertEqual(narrowed['stock_capture_ids'], ['C12', 'C14'])
            self.assertEqual([b['id'] for b in narrowed['datasheet_bindings']], ['C12'])
            # A package filter alone never resolves a prefix-only match.
            prefix = resolve_decisions(index, document, narrowing(('R4', 'OWNED-MCU32', 'LQFP-48', None)))
            self.assertEqual(prefix['entries'][1]['status'], 'ambiguous')
            for entries, outcome in (((('R1', 'OWNED-CANX-1', 'QFN-99', None),), 'unknown'),
                                     ((('R1', 'OWNED-CANX-1', None, 'C11'),), 'conflict'),
                                     ((('R1', 'OWNED-CANX-1', 'SOIC-8', 'C14'),), 'conflict')):
                with self.subTest(entries):
                    self.assertEqual(resolve_decisions(index, document, narrowing(*entries))['entries'][0]['status'],
                                     outcome)
            for bad in (narrowing(('R9', 'OWNED-CANX-1', 'SOIC-8', None)),
                        narrowing(('R1', 'OWNED-CANX-1', None, None)),
                        narrowing(('R1', 'OWNED-CANX-1', 'SOIC-8', None), ('R1', 'OWNED-CANX-1', 'DFN-8', None)),
                        {**narrowing(('R1', 'OWNED-CANX-1', 'SOIC-8', None)), 'schema': 'other'}):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    resolve_decisions(index, document, bad)
            unreasoned = narrowing(('R1', 'OWNED-CANX-1', 'SOIC-8', None))
            unreasoned['entries'][0]['reason'] = ''
            with self.assertRaises(ValueError):
                resolve_decisions(index, document, unreasoned)
            with self.assertRaisesRegex(ValueError, 'verified, not narrowed'):
                resolve_decisions(index, decisions(('R2', 'OWNED-CANX-1', 'C13')),
                                  narrowing(('R2', 'OWNED-CANX-1', 'SOIC-8', None)))
            for bad in ({'schema': 'agent-ee.part-decisions/0'}, decisions(),
                        decisions(('R1', None, None)), decisions(('R1', 'X', 'C0'))):
                with self.subTest(decisions=bad), self.assertRaises(ValueError):
                    resolve_decisions(index, bad)

    def test_networkless_cli_resolution_in_sandbox(self):
        from agent_ee.executor import sandbox_argv
        from toolchain import require_toolchain
        require_toolchain()
        path, pin = self.capture('candidate')
        write_json(path / 'decisions.json', decisions(('R1', 'OWNED-CANX-1', None), ('R3', 'OWNED-MCU32-A', None)))
        write_json(path / 'narrowing.json', narrowing(('R1', 'OWNED-CANX-1', 'SOIC-8', None)))
        work = self.root / 'work'
        work.mkdir()
        program = (
            'import os, sys; from agent_ee.catalog_identity import main; '
            f'assert os.readlink("/proc/self/ns/net") != {os.readlink("/proc/self/ns/net")!r}; '
            f'sys.exit(main(["resolve", "/candidate", {pin!r}, "/candidate/decisions.json", '
            '"--narrowing", "/candidate/narrowing.json", "--out", "/work/resolution.json"]))')
        run = subprocess.run(sandbox_argv(path, work, ['/usr/bin/python3', '-c', program]),
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), digest(work / 'resolution.json'))
        result = json.loads((work / 'resolution.json').read_text())
        self.assertEqual(result['stock_capture_ids'], ['C11', 'C13'])
        self.assertEqual([b['datasheet_url'] for b in result['datasheet_bindings']],
                         ['https://example.invalid/owned-mcu32.pdf', 'https://example.invalid/owned-canx.pdf'])
        # Host CLI: same inputs, byte-identical output; existing outputs are never replaced.
        out = self.root / 'host.json'
        argv = ['resolve', str(path), pin, str(path / 'decisions.json'), '--narrowing',
                str(path / 'narrowing.json'), '--out', str(out)]
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(main(argv), 0)
            self.assertEqual(main(argv), 1)
            self.assertEqual(main(['resolve', str(path), '0' * 64, str(path / 'decisions.json'),
                                   '--out', str(self.root / 'refused.json')]), 1)
        self.assertEqual(stdout.getvalue().strip(), digest(out))
        self.assertIn('output already exists', stderr.getvalue())
        self.assertIn('manifest hash mismatch', stderr.getvalue())
        self.assertEqual(out.read_bytes(), (work / 'resolution.json').read_bytes())
        self.assertFalse((self.root / 'refused.json').exists())


class ResponseDatasheetUrlTests(unittest.TestCase):
    def test_data_manual_url_extraction(self):
        url = 'https://example.invalid/owned-response.pdf'
        self.assertEqual(response_datasheet_url(search(jlc_row('C5', 1, dataManualUrl=url)), 'C5'), url)
        self.assertEqual(response_datasheet_url(search(jlc_row('C5', 1, dataManualUrl=' ' + url + ' ')), 'C5'), url)
        self.assertIsNone(response_datasheet_url(search(jlc_row('C5', 1)), 'C5'))
        self.assertIsNone(response_datasheet_url(search(jlc_row('C5', 1, dataManualUrl='')), 'C5'))
        self.assertIsNone(response_datasheet_url(search(jlc_row('C6', 1, dataManualUrl=url)), 'C5'))
        with self.assertRaises(ValueError):
            response_datasheet_url(search(jlc_row('C5', 1, dataManualUrl=7)), 'C5')
        with self.assertRaises(ValueError):
            response_datasheet_url(search(jlc_row('C5', 1, dataManualUrl=url), jlc_row('C5', 2)), 'C5')

    def test_part_record_crosscheck(self):
        from agent_ee.part_record import datasheet_url_crosscheck
        from test_approved_parts import named_fixture
        from test_parts_subset import captured_fixture
        for extra, identity_url, verdict in (
                ({'dataManualUrl': 'https://example.org/synthetic-C38695.pdf'}, None, 'agree'),
                ({'dataManualUrl': 'https://example.org/other.pdf'}, None, 'differ'),
                ({}, None, 'unknown'),
                ({}, 'https://example.org/synthetic-C38695.pdf', 'agree'),
                ({'dataManualUrl': 'https://example.org/synthetic-C38695.pdf'}, 'https://example.org/x.pdf', 'differ')):
            with self.subTest(extra=extra, identity_url=identity_url), tempfile.TemporaryDirectory() as scratch:
                root = Path(scratch)
                record = named_fixture(root, captured_fixture(self, root), **extra)
                check = datasheet_url_crosscheck(record, root, identity_url)
                self.assertEqual(check['verdict'], verdict)
                self.assertEqual(check['jlc_response'], extra.get('dataManualUrl'))
                self.assertEqual(check['acquisition_url'], record['datasheet_url'])
                # The extra vendor field does not alter jlc-catalog/1 parts or the record.
                self.assertNotIn('datasheet_url', json.loads((root / record['catalog']['path']).read_text())['parts']['C38695'])
                forged = dict(record, datasheet_url='https://example.org/forged.pdf')
                with self.assertRaises(ValueError):
                    datasheet_url_crosscheck(forged, root, identity_url)


if __name__ == '__main__':
    unittest.main()

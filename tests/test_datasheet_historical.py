"""Original PDFs and source notes for offline acquisition replay."""
import copy
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.datasheet_capture import import_historical_pdf
from agent_ee.datasheet_facts import load_acquisition
from agent_ee.storage import digest, read_json, write_json
from tests.test_datasheet_facts import text_pdf


class HistoricalImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf = self.root / 'original.pdf'
        text_pdf(self.pdf, ['Owned part 5 V'])
        self.url = 'https://example.invalid/historical.pdf'
        self.entry = dict(file='original.pdf', sha256=digest(self.pdf), url=self.url,
                          captured_at_utc='2026-01-02T03:04:05+00:00')
        self.note = self.root / 'sources.json'
        self.write_note()
        self.bindings = [dict(id='U1', datasheet_url=self.url)]

    def write_note(self):
        write_json(self.note, [self.entry])
        self.locator = dict(path='sources.json', sha256=digest(self.note), entry=0)

    def produce(self):
        with patch('agent_ee.datasheet_capture._bounded_fetch', side_effect=AssertionError('network')):
            return import_historical_pdf(self.pdf, self.root, self.locator, 'U1', self.root / 'import')

    def replay(self, manifest=None):
        if manifest is not None:
            write_json(self.root / 'import/manifest.json', manifest)
        return load_acquisition(self.root, dict(path='import/manifest.json',
            sha256=digest(self.root / 'import/manifest.json')), self.bindings)

    def test_offline_import_preserves_unknowns_and_dependency_closure(self):
        manifest = self.produce()
        acquired, deps = self.replay()
        record = acquired['U1']
        self.assertEqual(manifest['outcome'], 'imported')
        self.assertEqual(record['acquisition_method'], 'historical_import')
        self.assertEqual(record['redirect_provenance'], 'unknown')
        self.assertIsNone(record['final_url'])
        self.assertIsNone(record['fetched_at'])
        self.assertEqual(record['captured_at'], self.entry['captured_at_utc'])
        self.assertEqual(record['url'], self.url)
        self.assertEqual((self.root / 'import/source-note.json').read_bytes(), self.note.read_bytes())
        self.assertEqual(deps, {'import/manifest.json', 'import/source-note.json', 'import/' + record['path']})
        with self.assertRaises(FileExistsError):
            self.produce()

    def test_recorded_fetch_time_is_copied_exactly(self):
        self.entry['fetched_at'] = '2026-01-01T00:00:00Z'
        self.write_note()
        self.produce()
        self.assertEqual(self.replay()[0]['U1']['fetched_at'], self.entry['fetched_at'])

    def test_pdf_tamper_and_bad_framing_refuse_before_publication(self):
        for raw in (self.pdf.read_bytes() + b'changed', b'%PDF-1.4\nchanged\n%%EOF', b'HTML'):
            self.pdf.write_bytes(raw)
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                self.produce()
            self.assertFalse((self.root / 'import').exists())

    def test_missing_unbound_and_malformed_notes_refuse(self):
        good = copy.deepcopy(self.entry)
        for mutation in (lambda: self.note.unlink(),
                         lambda: self.note.write_text('[]'),
                         lambda: self.locator.update(sha256='0' * 64),
                         lambda: self.locator.update(entry=True)):
            self.entry = copy.deepcopy(good)
            self.write_note()
            mutation()
            with self.assertRaises((OSError, ValueError)):
                self.produce()
        for change in ({'sha256': '0' * 64}, {'final_url': self.url},
                       {'captured_at_utc': '2026-01-01'}, {'url': 'file:///local'}):
            self.entry = {**good, **change}
            self.write_note()
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.produce()

    def test_rehashed_forged_receipts_refuse(self):
        original = self.produce()
        changes = [dict(final_url=self.url), dict(redirect_provenance='observed'),
                   dict(fetched_at='2026-10-20T00:00:00Z'), dict(captured_at='now'),
                   dict(acquisition_method='live_capture'), dict(source_note=None),
                   dict(missing=True)]
        for change in changes:
            manifest = copy.deepcopy(original)
            manifest['parts'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.replay(manifest)
        manifest = copy.deepcopy(original)
        manifest.update(schema='agent-ee.datasheet-capture/1', outcome='captured')
        with self.assertRaises(ValueError):
            self.replay(manifest)
        # Removing the import labels cannot turn unknown redirect/time into live evidence.
        record = manifest['parts'][0]
        for key in ('source_note', 'captured_at', 'acquisition_method', 'redirect_provenance'):
            record.pop(key)
        with self.assertRaises(ValueError):
            self.replay(manifest)
        self.replay(original)
        (self.root / 'import/source-note.json').write_text('[]')
        with self.assertRaisesRegex(ValueError, 'note hash mismatch'):
            self.replay()

    def test_replay_pdf_tamper_missing_note_and_live_record_in_import_refuse(self):
        original = self.produce()
        pdf = self.root / 'import' / original['parts'][0]['path']
        raw = pdf.read_bytes()
        pdf.write_bytes(raw.replace(b'Owned', b'Wrong'))
        with self.assertRaisesRegex(ValueError, 'PDF hash mismatch'):
            self.replay()
        pdf.write_bytes(raw)
        note = self.root / 'import/source-note.json'
        note.unlink()
        with self.assertRaises(OSError):
            self.replay()
        note.symlink_to(self.note)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.replay()
        note.unlink()
        note.write_bytes(self.note.read_bytes())
        for change in (dict(url=self.url + '?invented'), dict(path='../original.pdf'),
                       dict(source_note={**original['parts'][0]['source_note'], 'path': '../sources.json'})):
            manifest = copy.deepcopy(original)
            manifest['parts'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.replay(manifest)
        manifest = copy.deepcopy(original)
        record = manifest['parts'][0]
        for key in ('source_note', 'captured_at', 'acquisition_method', 'redirect_provenance'):
            record.pop(key)
        record.update(final_url=self.url, fetched_at=self.entry['captured_at_utc'])
        with self.assertRaises(ValueError):
            self.replay(manifest)

    def test_cli_malformed_file_is_typed_refusal(self):
        for value in (None, 7, True, [], {}):
            self.entry['file'] = value
            self.write_note()
            result = subprocess.run([sys.executable, '-m', 'agent_ee.datasheet_capture',
                '--import-historical', '--pdf', str(self.pdf), '--source-root', str(self.root),
                '--source-note', 'sources.json', '--source-note-sha256', self.locator['sha256'],
                '--entry', '0', '--id', 'U1', '--out', str(self.root / 'import')], capture_output=True)
            with self.subTest(value=value):
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn(b'Traceback', result.stderr)
                self.assertIn(b'file', result.stderr)
                self.assertFalse((self.root / 'import').exists())
                with self.assertRaises(ValueError):
                    self.produce()

    def test_cli_import_is_offline(self):
        result = subprocess.run([sys.executable, '-m', 'agent_ee.datasheet_capture',
            '--import-historical', '--pdf', str(self.pdf), '--source-root', str(self.root),
            '--source-note', 'sources.json', '--source-note-sha256', self.locator['sha256'],
            '--entry', '0', '--id', 'U1', '--out', str(self.root / 'import')], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.replay()[0]['U1']['fetched_at'])

    def test_each_part_selection_and_fill_receipt_labels_acquisition(self):
        from tests.test_binding_fill import BindingFillTests
        from agent_ee.part_record import prepare_record, qualify_record
        from agent_ee.datasheet_selection import datasheet, selected_source, check_binding
        for historical in (False, True):
            helper = BindingFillTests()
            helper.setUp()
            self.addCleanup(helper.doCleanups)
            old = helper.record
            part = helper.parts['parts'][0]
            if historical:
                manifest = import_historical_pdf(self.pdf, self.root, self.locator, 'U1', helper.root / 'historical')
                blob = dict(path='historical/manifest.json', sha256=digest(helper.root / 'historical/manifest.json'))
                capture_id = 'U1'
            else:
                blob, capture_id = old['datasheets'], old['datasheet_id']
            record = prepare_record(helper.root, old['lcsc'], old['catalog'], blob, capture_id)
            method, redirect = ('historical_import', 'unknown') if historical else ('live', 'observed')
            with self.subTest(historical=historical):
                self.assertEqual(record['acquisition']['acquisition_method'], method)
                self.assertEqual(record['acquisition']['redirect_provenance'], redirect)
                qualify_record(record, helper.root)
                legacy = {k: v for k, v in record.items() if k != 'acquisition'}
                self.assertEqual(qualify_record(legacy, helper.root), qualify_record(record, helper.root))
                forged = copy.deepcopy(record)
                forged['acquisition']['acquisition_method'] = 'invented'
                with self.assertRaises(ValueError):
                    qualify_record(forged, helper.root)
                selection = part['selection']
                write_json(helper.root / selection['part_record'], record)
                selection['part_record_sha256'] = digest(helper.root / selection['part_record'])
                provenance = {**blob, 'locator': 'parts[id=' + capture_id + ']'}
                part['datasheet'].update(artifact=record['artifact'], provenance=provenance)
                write_json(helper.root / 'source-selection.json', dict(schema='agent-ee.datasheet-source-selection/1',
                    parts={'part': dict(selected=[dict(id='selected', sha256=record['artifact']['sha256'],
                                                     provenance=provenance)], rejected=[])}))
                ref = dict(path='source-selection.json', sha256=digest(helper.root / 'source-selection.json'), part_key='part')
                part['datasheet']['source_selection'] = ref
                receipts = [datasheet(helper.root, part)[0],
                            selected_source(helper.root, ref, context='test')[0]['acquisition'],
                            check_binding(helper.root, ref, record['artifact'], provenance, context='test')['acquisition'],
                            helper.fill()[1]['components'][0]['acquisition']]
                for receipt in receipts:
                    self.assertEqual(receipt['acquisition_method'], method)
                    self.assertEqual(receipt['redirect_provenance'], redirect)
                    if historical:
                        self.assertIsNone(receipt['final_url'])
                        self.assertEqual(receipt['source_note'], manifest['parts'][0]['source_note'])

    def test_external_part_receipt_labels_and_replays_import(self):
        from tests.test_binding_fill import ExternalBindingTests
        from agent_ee.part_record import prepare_external_record, qualify_external_record
        helper = ExternalBindingTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        import_historical_pdf(self.pdf, self.root, self.locator, 'U1', helper.root / 'historical')
        blob = dict(path='historical/manifest.json', sha256=digest(helper.root / 'historical/manifest.json'))
        record = prepare_external_record(helper.root, helper.record['identity_source'], blob,
                                         'U1', helper.record['deviation_ref'])
        self.assertEqual(record['acquisition']['acquisition_method'], 'historical_import')
        self.assertEqual(record['acquisition']['redirect_provenance'], 'unknown')
        dependencies = qualify_external_record(record, helper.root)
        self.assertIn('historical/source-note.json', dependencies)
        legacy = {k: v for k, v in record.items() if k != 'acquisition'}
        self.assertEqual(qualify_external_record(legacy, helper.root), dependencies)
        record['acquisition']['redirect_provenance'] = 'observed'
        with self.assertRaises(ValueError):
            qualify_external_record(record, helper.root)

    def test_part_record_replay_preserves_versioned_import_and_note_dependency(self):
        from tests.test_approved_parts import NamedPartTests
        from agent_ee.part_record import prepare_record, qualify_record
        helper = NamedPartTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        import_historical_pdf(self.pdf, self.root, self.locator, 'U1', helper.root / 'historical')
        locator = dict(path='historical/manifest.json', sha256=digest(helper.root / 'historical/manifest.json'))
        record = prepare_record(helper.root, helper.record['lcsc'], helper.record['catalog'], locator, 'U1')
        dependencies = qualify_record(record, helper.root)
        self.assertIn('historical/source-note.json', dependencies)
        self.assertEqual(record['datasheets'], locator)
        self.assertEqual(record['artifact']['sha256'], digest(self.pdf))
        manifest = read_json(helper.root / record['datasheets']['path'])
        self.assertEqual(manifest['parts'][0]['acquisition_method'], 'historical_import')

    def test_native_fact_check_retains_import_receipt_and_unknown_redirect(self):
        from tests.test_datasheet_integration import CanonicalAdapterTests
        from agent_ee.datasheet_parts import bindings_from_selection
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from toolchain import require_toolchain
        require_toolchain(tools=('pdftotext',))
        self.produce()
        helper = CanonicalAdapterTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        snap, sha, req, selection = helper.selected(url=self.url, quantity=1)
        bindings = bindings_from_selection(snap, sha, req, selection, {'resistors': ['U1']})
        write_json(self.root / 'bindings.json', bindings)
        part = bindings['parts'][0]
        record = self.replay()[0]['U1']
        fact = dict(id='rating', part_number=part['part_number'], kind='rating', value='5', unit='V',
                    conditions='original fixture', source=dict(path='import/' + record['path'],
                    sha256=record['sha256'], page=1, locator='owned text', quote='Owned part 5 V'))
        write_json(self.root / 'facts.json', dict(schema='agent-ee.datasheet-facts/2',
            bindings=dict(path='bindings.json', sha256=digest(self.root / 'bindings.json')),
            acquisition=dict(path='import/manifest.json', sha256=digest(self.root / 'import/manifest.json')),
            required_parts=[dict(reference='U1', selection_id=part['selection_id'],
                                part_number=part['part_number'], required_facts=['rating'])], facts=[fact]))
        version = subprocess.run(['/usr/bin/pdftotext', '-v'], capture_output=True, text=True, check=True).stderr.splitlines()[0]
        scope = dict(schema='agent-ee.scope/1', name='historical', scope='original fixture',
            inputs=[dict(path=str(self.root / name), at=name) for name in ('bindings.json', 'facts.json', 'import')],
            checks=[dict(id='facts', kind='datasheet_facts', params=dict(facts='facts.json',
                extractor=dict(path='/usr/bin/pdftotext', sha256=digest('/usr/bin/pdftotext'), version=version)))])
        write_json(self.root / 'scope.json', scope)
        run = self.root / 'run'
        run.mkdir()
        capture(self.root / 'scope.json', run)
        results = run_checks(run, time.monotonic() + 60)
        self.assertEqual(results[0]['outcome'], 'pass', results)
        self.assertEqual(results[0]['details']['acquisitions']['U1']['redirect_provenance'], 'unknown')
        report = read_json(run / 'work/facts/facts.json')
        self.assertEqual(report['acquisitions']['U1']['acquisition_method'], 'historical_import')
        self.assertEqual(report['acquisitions']['U1']['source_note'], record['source_note'])

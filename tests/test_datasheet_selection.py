"""Owned synthetic source decisions: purchase identity is independent of PDF alias."""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from agent_ee.part_record import prepare_record, qualify_record
from agent_ee.parts_manifest import prepare_manifest, qualify_manifest
from agent_ee.storage import digest, identity, read_json, write_json
import test_binding_fill


class SourceSelectionTests(unittest.TestCase):
    def setUp(self):
        test_binding_fill.BindingFillTests.setUp(self)
        self.part = self.parts['parts'][0]
        self.original = copy.deepcopy(self.record)
        self.selected_path = 'selected/manifest.json'
        url = 'https://example.org/owned-general-family.pdf'
        path = hashlib.sha256(url.encode()).hexdigest() + '.pdf'
        (self.root / 'selected').mkdir()
        (self.root / 'selected' / path).write_bytes(b'%PDF-1.4\nOwned general-family fixture.\n%%EOF\n')
        self.artifact = dict(path='selected/' + path, sha256=digest(self.root / 'selected' / path))
        write_json(self.root / self.selected_path, dict(schema='agent-ee.datasheet-capture/1',
            bindings_sha256=identity([dict(id='catalog-alias', datasheet_url=url)]), outcome='captured',
            parts=[dict(id='catalog-alias', url=url, final_url=url, fetched_at='2026-10-18T00:00:00+00:00',
                        sha256=self.artifact['sha256'], path=path, missing=False, reason=None)]))
        self.provenance = dict(path=self.selected_path, sha256=digest(self.root / self.selected_path),
                               locator='parts[id=catalog-alias]')
        self.decision = dict(schema='agent-ee.datasheet-source-selection/1', parts={'C38695': dict(
            selected=[dict(id='general-family', sha256=self.artifact['sha256'], provenance=self.provenance)],
            rejected=[dict(id='supplier-wrong-family', sha256=self.original['artifact']['sha256'],
                           reason='wrong-family; no applicable ordering evidence')])})
        self.pin_decision()

    def fill(self):
        return test_binding_fill.BindingFillTests.fill(self)

    def pin_decision(self):
        write_json(self.root / 'source-selection.json', self.decision)
        self.ref = dict(path='source-selection.json', sha256=digest(self.root / 'source-selection.json'),
                        part_key='C38695')
        self.part['datasheet']['source_selection'] = self.ref

    def prepare(self):
        return prepare_record(self.root, self.original['lcsc'], self.original['catalog'],
                              self.original['datasheets'], self.original['datasheet_id'], source_selection=self.ref)

    def install(self, record):
        selection = self.part['selection']
        write_json(self.root / selection['part_record'], record)
        selection['part_record_sha256'] = digest(self.root / selection['part_record'])
        self.part['datasheet'].update(artifact=record['artifact'], provenance={**record['datasheets'],
            'locator': 'parts[id=' + record['datasheet_id'] + ']'})

    def test_wrong_family_manifest_and_fill_fail_with_instances_and_source(self):
        for operation in (lambda: qualify_manifest(self.parts, self.root), self.fill):
            with self.assertRaises(ValueError) as caught:
                operation()
            for value in ('resistor', 'C38695', 'R1', 'supplier-wrong-family', self.original['artifact']['sha256']):
                self.assertIn(value, str(caught.exception))

    def test_selected_alias_propagates_and_purchase_identity_stays(self):
        record = self.prepare()
        self.assertEqual(record['datasheet_id'], 'catalog-alias')
        self.assertEqual(record['artifact'], self.artifact)
        for key in ('lcsc', 'mpn', 'manufacturer', 'package', 'stock', 'catalog'):
            self.assertEqual(record[key], self.original[key])
        self.install(record)
        self.assertEqual(prepare_manifest(self.parts, self.root), self.parts)
        qualified = qualify_manifest(self.parts, self.root)
        self.assertEqual(qualified['dependencies'][self.ref['path']], self.ref['sha256'])
        intent, report = self.fill()
        fields = intent['components'][0]['part_binding']
        self.assertEqual(fields['LCSC'], 'C38695')
        self.assertEqual(fields['Partnumber'], self.original['mpn'])
        self.assertEqual(json.loads(fields['PARAM_Datasheet'])['sha256'], self.artifact['sha256'])
        self.assertEqual(fields['Datasheet'], record['datasheet_url'])
        self.assertEqual(self.intent['components'][0]['footprint'], self.part['footprint'])

    def test_cli_uses_captured_selection(self):
        command = [sys.executable, '-m', 'agent_ee.part_record', '--root', str(self.root),
                   '--lcsc', self.original['lcsc'], '--catalog', self.original['catalog']['path'],
                   '--datasheets', self.original['datasheets']['path'],
                   '--datasheet-id', self.original['datasheet_id'],
                   '--source-selection', self.ref['path'], '--source-part-key', self.ref['part_key'],
                   '--out', 'selected-record.json']
        completed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(read_json(self.root / 'selected-record.json'), self.prepare())

    def test_ambiguous_selection_refuses(self):
        self.decision['parts']['C38695']['selected'].append({
            **self.decision['parts']['C38695']['selected'][0], 'id': 'another-source'})
        self.pin_decision()
        for operation in (self.prepare, lambda: qualify_manifest(self.parts, self.root)):
            with self.assertRaisesRegex(ValueError, 'ambiguous.*C38695.*general-family.*another-source'):
                operation()

    def test_missing_selection_refuses(self):
        for parts in ({}, {'C38695': dict(selected=[], rejected=[])}):
            self.decision['parts'] = parts
            self.pin_decision()
            for operation in (self.prepare, lambda: qualify_manifest(self.parts, self.root)):
                with self.assertRaisesRegex(ValueError, 'missing.*C38695'):
                    operation()

    def test_rejected_hash_alias_cannot_be_selected(self):
        self.decision['parts']['C38695']['selected'][0]['sha256'] = self.original['artifact']['sha256']
        self.pin_decision()
        with self.assertRaisesRegex(ValueError, 'general-family.*rejected.*supplier-wrong-family'):
            self.prepare()

    def test_selected_hash_must_match_acquisition(self):
        self.decision['parts']['C38695']['selected'][0]['sha256'] = 'a' * 64
        self.pin_decision()
        with self.assertRaisesRegex(ValueError, 'differs from selected source general-family'):
            self.prepare()

    def test_decision_bytes_and_acquisition_pins_are_checked(self):
        (self.root / self.ref['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.prepare()
        self.pin_decision()
        (self.root / self.selected_path).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.prepare()

    def test_qualified_record_cannot_rebind_or_drop_manifest_decision(self):
        record = self.prepare()
        self.install(record)
        del self.part['datasheet']['source_selection']
        with self.assertRaisesRegex(ValueError, 'must retain.*source_selection'):
            qualify_manifest(self.parts, self.root)
        record.update(artifact=self.original['artifact'], datasheets=self.original['datasheets'],
                      datasheet_id=self.original['datasheet_id'], datasheet_url=self.original['datasheet_url'])
        with self.assertRaisesRegex(ValueError, 'rejected.*supplier-wrong-family'):
            qualify_record(record, self.root)

    def test_later_ordering_evidence_is_not_inferred_from_family(self):
        self.ref['part_key'] = 'C38695-exact-ordering'
        with self.assertRaisesRegex(ValueError, 'missing.*C38695-exact-ordering'):
            self.prepare()

    def test_failed_sources_are_recorded_but_never_selectable(self):
        # AGENTEE-248: an optional failed list records unacquired candidates.
        failed = dict(id='supplier-html', reason='HTML bot challenge, not a PDF envelope',
                      provenance={**self.provenance, 'locator': 'parts[id=supplier-html]'})
        self.decision['parts']['C38695']['failed'] = [failed]
        self.pin_decision()
        self.assertEqual(self.prepare()['datasheet_id'], 'catalog-alias')
        for change, pattern in (({**failed, 'id': 'general-family'}, 'selected source is recorded as failed'),
                                ({**failed, 'reason': ''}, 'nonempty'),
                                ({k: v for k, v in failed.items() if k != 'provenance'}, 'expected fields'),
                                ({**failed, 'provenance': {**self.provenance, 'locator': 'x'}}, 'exact acquisition'),
                                ({**failed, 'sha256': 'a' * 64}, 'expected fields')):
            self.decision['parts']['C38695']['failed'] = [change]
            self.pin_decision()
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, pattern):
                self.prepare()
        self.decision['parts']['C38695']['failed'] = [failed, failed]
        self.pin_decision()
        with self.assertRaisesRegex(ValueError, 'duplicate source id'):
            self.prepare()

    def test_generic_manifest_also_refuses_rejected_source(self):
        # Retain the generic catalog fixture instead of the named replacement.
        from test_parts_subset import captured_fixture
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        parts = captured_fixture(self, root)
        part = parts['parts'][0]
        original_sheet = copy.deepcopy(part['datasheet'])
        self.decision['parts']['C38695']['rejected'][0]['sha256'] = original_sheet['artifact']['sha256']
        write_json(root / 'source-selection.json', self.decision)
        part['datasheet']['source_selection'] = dict(path='source-selection.json',
            sha256=digest(root / 'source-selection.json'), part_key='C38695')
        with self.assertRaisesRegex(ValueError, 'part=resistor.*rejected.*supplier-wrong-family'):
            qualify_manifest(parts, root)

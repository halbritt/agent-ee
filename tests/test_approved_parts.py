"""Named-part identity admission with original synthetic supplier/PDF/CAD captures.

Part numbers are facts used as test labels. No live supplier observation, vendor
PDF, device geometry, circuit suitability or owner approval is claimed.
"""
import copy
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_ee.jlc_stock_capture import DEFAULT_ENDPOINT, PARSER, parse_search_response, request_body
from agent_ee.part_record import prepare_record, qualify_record
from agent_ee.parts_manifest import qualify_manifest, prepare_manifest
from agent_ee.storage import digest, identity, read_json, write_json
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_parts_subset import captured_fixture
from test_jlc_stock_capture import row, search


def named_fixture(root, document, index=0, lcsc='C38695', mpn='TJA1051T/3', package='SOIC-8'):
    """Replace a logical entry by a named synthetic identity, retaining owned CAD."""
    def put(path, data):
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            dest.write_bytes(data)
        else:
            write_json(dest, data)
        return dict(path=path, sha256=digest(dest))
    stamp = '2026-10-18T00:00:00+00:00'
    prefix = f'named/{lcsc}/'
    raw = search(row(lcsc, 50, library='expand', componentModelEn=mpn,
                     componentBrandEn='Synthetic manufacturer', componentSpecificationEn=package,
                     firstSortName='Synthetic IC/module category'))
    response = put(prefix + 'responses/' + lcsc + '.json', raw)
    response.update(path='responses/' + lcsc + '.json', size_bytes=len(raw))
    _, observed = parse_search_response(raw, lcsc)
    catalog = put(prefix + 'catalog.json', dict(schema='agent-ee.jlc-catalog/1', as_of=stamp,
        completed_at=stamp, stale_before=None, source=dict(endpoint=DEFAULT_ENDPOINT, method='POST',
            request_body=request_body('{lcsc}').decode(), parser=PARSER, min_interval_seconds=5.0),
        requests=[dict(lcsc=lcsc, fetched_at=stamp, status='found', http_status=200,
                       response=response, reason=None)], parts={lcsc: {**observed, 'last_seen': stamp}}))
    url = 'https://example.org/synthetic-' + lcsc + '.pdf'
    pdf_path = hashlib.sha256(url.encode()).hexdigest() + '.pdf'
    artifact = put(prefix + 'datasheets/' + pdf_path, b'%PDF-1.4\nOwned synthetic envelope only.\n%%EOF\n')
    capture_id = 'original-synthetic-device'
    sheets = put(prefix + 'datasheets/manifest.json', dict(schema='agent-ee.datasheet-capture/1',
        bindings_sha256=identity([dict(id=capture_id, datasheet_url=url)]), outcome='captured',
        parts=[dict(id=capture_id, url=url, final_url=url, fetched_at=stamp, sha256=artifact['sha256'],
                    path=pdf_path, missing=False, reason=None)]))
    record = prepare_record(root, lcsc, catalog, sheets, capture_id)
    record_blob = put(prefix + 'part-record.json', record)
    decision = put(prefix + 'decision.txt', b'Original synthetic named-part admission decision.\n')
    decision['locator'] = 'line 1'
    part = document['parts'][index]
    part.update(lcsc=lcsc, mpn=mpn, manufacturer=record['manufacturer'], package=package, stock=50,
        selection=dict(kind='named', lcsc=lcsc, part_record=record_blob['path'],
                       part_record_sha256=record_blob['sha256'], decision_ref=decision),
        datasheet=dict(status='captured', required=True, artifact=artifact,
                       provenance={**sheets, 'locator': 'parts[id=' + capture_id + ']'}))
    if all('selection' in p for p in document['parts']):
        document.update(catalog=None, selection=None)
    else:
        # Generic requirements/results cover only remaining generic allocations.
        from agent_ee.part_selection import selection_check
        requirements_blob = document['selection']['requirements']
        requirements = read_json(root / requirements_blob['path'])
        requirements['parts'] = [p for p in requirements['parts'] if p['id'] != part['selection_id']]
        put(requirements_blob['path'], requirements)
        requirements_blob['sha256'] = digest(root / requirements_blob['path'])
        result_blob = document['selection']['result']
        result = selection_check(root / document['catalog']['snapshot'], document['catalog']['manifest_sha256'], requirements)
        put(result_blob['path'], result)
        result_blob['sha256'] = digest(root / result_blob['path'])
    put('parts.json', document)
    return record


class NamedPartTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.document = captured_fixture(self, self.root)
        self.record = named_fixture(self.root, self.document)

    def repin_record(self, record):
        selection = self.document['parts'][0]['selection']
        write_json(self.root / selection['part_record'], record)
        selection['part_record_sha256'] = digest(self.root / selection['part_record'])

    def test_named_part_replay(self):
        before = copy.deepcopy(self.document)
        report = qualify_manifest(self.document, self.root)
        self.assertIsNone(report['selection'])
        self.assertEqual(report['missing_provenance'], [])
        self.assertEqual(prepare_manifest(self.document, self.root), before)
        self.assertEqual(self.document, before)
        part = self.document['parts'][0]
        for field in ('mpn', 'lcsc', 'package', 'manufacturer', 'stock'):
            with self.subTest(side='manifest', field=field):
                original = part[field]
                part[field] = 999 if field == 'stock' else 'wrong'
                with self.assertRaises(ValueError):
                    qualify_manifest(self.document, self.root)
                part[field] = original
            with self.subTest(side='record', field=field):
                record = copy.deepcopy(self.record)
                record[field] = 999 if field == 'stock' else 'wrong'
                self.repin_record(record)
                with self.assertRaises(ValueError):
                    qualify_manifest(self.document, self.root)
                self.repin_record(self.record)

    def test_named_part_without_record_refused(self):
        path = self.root / self.document['parts'][0]['selection']['part_record']
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            qualify_manifest(self.document, self.root)
        self.document['parts'][0]['selection'].pop('part_record_sha256')
        with self.assertRaises(ValueError):
            qualify_manifest(self.document, self.root)

    def test_named_closure_pins_every_source_and_rejects_mutation_links(self):
        report = qualify_manifest(self.document, self.root)
        dependencies = report['dependencies']
        for path in (self.record['catalog']['path'], self.record['datasheets']['path'],
                     self.record['artifact']['path'], 'named/C38695/responses/C38695.json',
                     'named/C38695/decision.txt', 'named/C38695/part-record.json'):
            self.assertIn(path, dependencies)
            target = self.root / path
            raw = target.read_bytes()
            target.write_bytes(raw + b' ')
            with self.subTest(path=path), self.assertRaises(ValueError):
                qualify_manifest(self.document, self.root)
            target.write_bytes(raw)
        target = self.root / self.record['artifact']['path']
        target.unlink()
        target.symlink_to(self.root / 'requirements.md')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            qualify_manifest(self.document, self.root)

    def test_unavailable_datasheet_and_unsupported_kind_fail_closed(self):
        part = self.document['parts'][0]
        part['selection']['kind'] = 'inferred'
        with self.assertRaises(ValueError):
            qualify_manifest(self.document, self.root)
        part['selection']['kind'] = 'named'
        part['datasheet'] = dict(status='unavailable', required=False, reason='missing')
        with self.assertRaisesRegex(ValueError, 'captured datasheet'):
            qualify_manifest(self.document, self.root)

    def test_named_record_cli_is_offline_reproducible_and_new_output_only(self):
        args = [sys.executable, '-m', 'agent_ee.part_record', '--root', str(self.root), '--lcsc', 'C38695',
                '--catalog', self.record['catalog']['path'], '--datasheets', self.record['datasheets']['path'],
                '--datasheet-id', self.record['datasheet_id'], '--out', 'record-cli.json']
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_json(self.root / 'record-cli.json'), self.record)
        self.assertEqual(result.stdout.strip(), digest(self.root / 'record-cli.json'))
        self.assertEqual(subprocess.run(args, capture_output=True).returncode, 2)

    def test_failed_missing_and_forged_supplier_or_datasheet_captures_refused(self):
        for source in ('catalog', 'datasheets'):
            path = self.root / self.record[source]['path']
            original = path.read_bytes()
            doc = read_json(path)
            if source == 'catalog':
                doc['parts']['C38695']['mpn'] = 'forged'
            else:
                doc['parts'][0].update(missing=True, path=None, sha256=None, reason='failed')
                doc['outcome'] = 'missing'
            write_json(path, doc)
            record = copy.deepcopy(self.record)
            record[source]['sha256'] = digest(path)
            with self.subTest(source=source), self.assertRaises(ValueError):
                qualify_record(record, self.root)
            path.write_bytes(original)


class NamedPartsPipelineTests(unittest.TestCase):
    def setUp(self):
        from test_parts_subset import NativePartsPipelineTests
        NativePartsPipelineTests.setUp(self)
        named_fixture(self.root, self.document)
        self.document['parts'][0]['instances'][0]['reference'] = 'U1'

    from test_parts_subset import NativePartsPipelineTests as _Native
    run_case = _Native.run_case

    def schematic(self, variant='control'):
        from test_parts_subset import subset_generator
        generator = subset_generator()
        text = generator.schematic().replace('R1', 'U1')
        text = text.replace('"C1"', '"C38695"').replace('"R-C1"', '"TJA1051T/3"')
        text = text.replace('"Owned fixture"', '"Synthetic manufacturer"')
        if variant == 'unlisted':
            extra = generator.resistor('U2', generator.BASE._root_entries('U2', 1, 'control'))
            extra = extra.replace('"C1"', '"C99999"')
            text = text.rstrip()[:-1] + extra + ')'
        return text

    def test_parts_subset_named(self):
        from agent_ee.cli import verify
        run, check = self.run_case()
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'pass'), check)
        verify(run)
        baseline = read_json(run / 'baseline.json')
        for path in ('named/C38695/part-record.json', 'named/C38695/decision.txt',
                     'named/C38695/responses/C38695.json'):
            self.assertIn(path, baseline['files'])
        run, check = self.run_case('unlisted')
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'defect'), check)
        unexpected = [r for r in check['details']['report']['matches'] if r['reference'] == 'U2']
        self.assertEqual(unexpected[0]['findings'][0]['code'], 'unexpected_populated_instance')
        verify(run)


if __name__ == '__main__':
    unittest.main()

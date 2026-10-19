"""AGENTEE-248 cascading datasheet resolution against owned loopback stubs.

No test contacts DigiKey, JLCPCB, LCSC, Exa or any vendor. DigiKey and JLCPCB
captures are produced by their real producers against the existing loopback
stubs; the Exa tier runs a stub ``exa-search`` executable that prints owned
JSON naming loopback URLs. Every PDF is an original synthetic envelope.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_ee import datasheet_cascade as cascade
from agent_ee.datasheet_cascade import resolve_datasheets, validate_cascade
from agent_ee.digikey_capture import capture_digikey
from agent_ee.jlc_stock_capture import capture_jlc_stock
from agent_ee.part_record import prepare_record, qualify_record
from agent_ee.parts_manifest import prepare_manifest, qualify_manifest
from agent_ee.storage import digest, read_json, write_json
import test_binding_fill
from test_digikey_capture import CREDENTIALS, DigiKeyStub, details, product
from test_jlc_stock_capture import JlcStub, row, search

DIGIKEY_PDF = b'%PDF-1.4\n% owned synthetic manufacturer datasheet\n%%EOF\n'
WRONG_FAMILY_PDF = b'%PDF-1.4\n% owned synthetic wrong-family catalog\n%%EOF\n'
EXA_PDF = b'%PDF-1.4\n% owned synthetic datasheet found by search\n%%EOF\n'
CATALOG_PDF = b'%PDF-1.4\n% owned synthetic catalog-index datasheet\n%%EOF\n'
HTML = b'<html><body>Owned synthetic bot challenge</body></html>'
SHA = {name: hashlib.sha256(data).hexdigest() for name, data in (
    ('digikey', DIGIKEY_PDF), ('wrong', WRONG_FAMILY_PDF), ('exa', EXA_PDF), ('catalog', CATALOG_PDF))}


class PdfServer:
    """Loopback datasheet host; ``hits`` counts each path served."""

    def __init__(self):
        routes = {'/dk.pdf': (200, DIGIKEY_PDF), '/jlc-html.pdf': (200, HTML),
                  '/jlc-wrong.pdf': (200, WRONG_FAMILY_PDF), '/exa.pdf': (200, EXA_PDF),
                  '/catalog.pdf': (200, CATALOG_PDF)}
        self.hits = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                server.hits.append(self.path)
                status, body = routes.get(self.path, (404, b'not found'))
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


STUB = '''#!{python}
import json, sys, time
from pathlib import Path
with open({log!r}, 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
mode = Path({mode!r}).read_text().strip()
if mode == 'slow':
    time.sleep(30)
if mode == 'fail':
    print(json.dumps({{'error': 'Exa HTTP 429'}}))
    sys.exit(4)
if mode == 'garbage':
    print('not json')
    sys.exit(0)
print(Path({payload!r}).read_text())
'''


class CascadeFixture:
    """Owned captures under ``self.root`` plus a stub exa-search outside it."""

    def make_fixture(self):
        self.pdf = PdfServer()
        self.addCleanup(self.pdf.close)
        tools = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.exa_log, self.exa_mode, self.exa_payload = tools / 'log', tools / 'mode', tools / 'payload.json'
        self.exa_mode.write_text('ok')
        self.exa_results([self.pdf.url + '/exa.pdf'])
        stub = tools / 'exa-search'
        stub.write_text(STUB.format(python=sys.executable, log=str(self.exa_log), mode=str(self.exa_mode),
                                    payload=str(self.exa_payload)))
        stub.chmod(0o755)
        self.exa_command = [str(stub)]

    def exa_results(self, urls):
        self.exa_payload.write_text(json.dumps({'query': 'q', 'type': 'auto', 'category': None, 'results': [
            {'title': 'Owned result', 'url': url, 'published': None, 'author': None, 'highlights': []}
            for url in urls], 'result_count': len(urls), 'fetched_at': '2026-10-19T00:00:00Z'}))

    def exa_calls(self):
        return [json.loads(line) for line in self.exa_log.read_text().splitlines()] if self.exa_log.exists() else []

    def digikey_capture(self, routes, name='dk'):
        server = DigiKeyStub(routes)
        self.addCleanup(server.close)
        capture_digikey([{'key': k.replace('/', '-'), 'kind': 'product', 'query': k} for k in routes], self.root / name,
                        allow_network=True, credentials=CREDENTIALS, base=server.url, min_interval=0.5, timeout=10)
        return name + '/digikey-capture.json'

    def jlc_capture(self, lcsc, url, name='jlc'):
        extra = {} if url is None else {'dataManualUrl': url}
        server = JlcStub({lcsc: (200, search(row(lcsc, 50, componentModelEn='TJA1051T/3', **extra)))})
        self.addCleanup(server.close)
        capture_jlc_stock([lcsc], self.root / name, allow_network=True, endpoint=server.url, timeout=10)
        return name + '/catalog.json'

    def request(self, *parts, prior=None):
        return {'schema': cascade.REQUEST_SCHEMA, 'prior_selection': prior, 'parts': list(parts)}

    def part(self, key, **tiers):
        return {'key': key, 'digikey': None, 'jlc': None, 'catalog': None, 'exa': None, **tiers}

    def exa(self, **extra):
        return {'query': 'TJA1051T/3 datasheet pdf', 'include_domains': [], 'max_results': 3, **extra}

    def resolve(self, request, out='cascade', **kwargs):
        options = {'timeout': 10, 'search_timeout': 10, 'min_interval': 0.5, **kwargs}
        if options.get('allow_network'):
            options.setdefault('exa_command', self.exa_command)
        return resolve_datasheets(self.root, request, out, **options)

    def attempts(self, report, key):
        return [(a['tier'], a['outcome']) for a in report['parts'][key]['attempts']]


class CascadeTests(CascadeFixture, unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.make_fixture()

    def test_digikey_primary_datasheet_wins_and_later_tiers_are_recorded_unattempted(self):
        dk = self.digikey_capture({'AE-CAN-1': (200, details(product('AE-CAN-1', datasheet=self.pdf.url + '/dk.pdf')))})
        jlc = self.jlc_capture('C38695', self.pdf.url + '/jlc-wrong.pdf')
        report = self.resolve(self.request(self.part('C38695', digikey={'capture': dk, 'key': 'AE-CAN-1'},
                                                     jlc={'capture': jlc, 'lcsc': 'C38695'}, exa=self.exa())),
                              allow_network=True)
        self.assertEqual(self.attempts(report, 'C38695'), [('digikey', 'selected'), ('jlc', 'not_attempted')])
        self.assertEqual(self.pdf.hits, ['/dk.pdf'])
        self.assertEqual(self.exa_calls(), [])
        tiers = report['parts']['C38695']['tiers']
        self.assertEqual([t['status'] for t in tiers], ['candidates', 'candidates', 'undeclared', 'not_reached'])
        self.assertEqual(tiers[0]['source']['field'], 'DatasheetUrl')
        self.assertEqual(tiers[0]['source']['response']['path'], 'dk/responses/AE-CAN-1.json')
        selection = read_json(self.root / 'cascade/source-selection.json')
        self.assertEqual(selection['parts']['C38695']['selected'], [{
            'id': 'C38695#digikey', 'sha256': SHA['digikey'], 'provenance': {
                **report['acquisition'], 'locator': 'parts[id=C38695#digikey]'}}])
        self.assertEqual(report['acquisition']['path'], 'cascade/datasheets/manifest.json')
        dependencies = validate_cascade(self.root, 'cascade/cascade.json')
        for path in (dk, 'dk/responses/AE-CAN-1.json', jlc, 'cascade/datasheets/manifest.json',
                     'cascade/source-selection.json', 'cascade/cascade.json'):
            self.assertIn(path, dependencies)

    def test_html_supplier_url_fails_and_advances_to_catalog_then_records_failure(self):
        from test_catalog_identity import IdentityIndexTests
        self.source = lambda edit=None: IdentityIndexTests.source(self, edit)
        jlc = self.jlc_capture('C13', self.pdf.url + '/jlc-html.pdf')
        url = self.pdf.url + '/catalog.pdf'
        IdentityIndexTests.capture(self, 'index', edit=lambda c: c.execute(
            'UPDATE jlc_components SET datasheet=? WHERE lcsc=13', (url,)))
        catalog = {'directory': 'index', 'manifest_sha256': digest(self.root / 'index/manifest.json'), 'lcsc': 'C13'}
        report = self.resolve(self.request(self.part('C13', jlc={'capture': jlc, 'lcsc': 'C13'}, catalog=catalog)))
        self.assertEqual(self.attempts(report, 'C13'), [('jlc', 'failed'), ('catalog', 'selected')])
        attempt = report['parts']['C13']['attempts'][0]
        self.assertIn('not a complete PDF envelope', attempt['reason'])
        entry = read_json(self.root / 'cascade/source-selection.json')['parts']['C13']
        self.assertEqual([s['id'] for s in entry['failed']], ['C13#jlc'])
        self.assertIn('/jlc-html.pdf', entry['failed'][0]['reason'])
        self.assertEqual(entry['selected'][0]['sha256'], SHA['catalog'])
        manifest = read_json(self.root / 'cascade/datasheets/manifest.json')
        self.assertEqual(manifest['outcome'], 'missing')
        self.assertEqual([(r['id'], r['missing']) for r in manifest['parts']], [('C13#catalog', False), ('C13#jlc', True)])
        self.assertIn('index/identity.sqlite3', validate_cascade(self.root, 'cascade/cascade.json'))

    def test_prior_rejected_hash_advances_and_exa_needs_the_network_opt_in(self):
        jlc = self.jlc_capture('C38695', self.pdf.url + '/jlc-wrong.pdf')
        write_json(self.root / 'prior.json', {'schema': 'agent-ee.datasheet-source-selection/1', 'parts': {
            'C38695': {'selected': [], 'rejected': [{'id': 'supplier-wrong-family', 'sha256': SHA['wrong'],
                                                      'reason': 'wrong family for the purchased part'}]}}})
        prior = {'path': 'prior.json', 'sha256': digest(self.root / 'prior.json')}
        request = self.request(self.part('C38695', jlc={'capture': jlc, 'lcsc': 'C38695'}, exa=self.exa()), prior=prior)
        offline = self.resolve(request, 'offline')
        self.assertEqual(self.exa_calls(), [])
        self.assertEqual(offline['parts']['C38695']['outcome'], 'unresolved')
        self.assertEqual(self.attempts(offline, 'C38695'), [('jlc', 'rejected')])
        self.assertEqual(offline['parts']['C38695']['tiers'][3]['status'], 'unavailable')
        self.assertIn('--allow-network', offline['parts']['C38695']['tiers'][3]['reason'])
        entry = read_json(self.root / 'offline/source-selection.json')['parts']['C38695']
        self.assertEqual(entry['selected'], [])
        self.assertEqual([(r['id'], r['sha256']) for r in entry['rejected']],
                         [('supplier-wrong-family', SHA['wrong']), ('C38695#jlc', SHA['wrong'])])
        self.assertIn('prior.json', validate_cascade(self.root, 'offline/cascade.json'))
        # Unknown never passes: the unresolved key is refused by every consumer.
        from agent_ee.datasheet_selection import selected_source
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous source selection for C38695'):
            selected_source(self.root, {'path': 'offline/source-selection.json', 'part_key': 'C38695',
                                        'sha256': digest(self.root / 'offline/source-selection.json')}, context='t')

        self.exa_results(['https://elsewhere.example/x.pdf', self.pdf.url + '/missing.pdf', self.pdf.url + '/exa.pdf'])
        request['parts'][0]['exa']['include_domains'] = ['127.0.0.1']
        report = self.resolve(request, 'online', allow_network=True)
        self.assertEqual(self.attempts(report, 'C38695'), [('jlc', 'rejected'), ('exa', 'failed'), ('exa', 'selected')])
        self.assertEqual(self.exa_calls(), [['--max-results', '3', '--include-domains', '127.0.0.1', '--',
                                             'TJA1051T/3 datasheet pdf']])
        search_record = report['searches'][0]
        retained = (self.root / 'online' / search_record['response']['path']).read_bytes()
        self.assertEqual(retained, self.exa_payload.read_bytes() + b'\n')
        self.assertEqual(search_record['response']['sha256'], hashlib.sha256(retained).hexdigest())
        self.assertTrue(search_record['fetched_at'].endswith('+00:00'))
        self.assertEqual(report['parts']['C38695']['tiers'][3]['urls'],
                         [self.pdf.url + '/missing.pdf', self.pdf.url + '/exa.pdf'])
        entry = read_json(self.root / 'online/source-selection.json')['parts']['C38695']
        self.assertEqual((entry['selected'][0]['id'], entry['selected'][0]['sha256']), ('C38695#exa-2', SHA['exa']))
        self.assertEqual([f['id'] for f in entry['failed']], ['C38695#exa-1'])
        validate_cascade(self.root, 'online/cascade.json')

    def test_exa_child_deadline_failure_and_garbage_are_recorded_not_hidden(self):
        request = self.request(self.part('K1', exa=self.exa()))
        for mode, reason in (('slow', 'search deadline exceeded'), ('fail', 'failed (4)'),
                             ('garbage', 'unrecognised search output')):
            with self.subTest(mode=mode):
                self.exa_mode.write_text(mode)
                report = self.resolve(request, 'out-' + mode, allow_network=True, search_timeout=1)
                self.assertEqual(report['parts']['K1']['outcome'], 'unresolved')
                self.assertEqual(report['searches'][0]['status'], 'failed')
                self.assertIn(reason, report['searches'][0]['reason'])
                self.assertEqual(report['parts']['K1']['tiers'][3]['status'], 'unavailable')
                self.assertEqual(report['acquisition'], None)
                self.assertEqual(read_json(self.root / f'out-{mode}/source-selection.json')['parts']['K1'],
                                 {'selected': [], 'rejected': [], 'failed': []})
                self.assertEqual(report['searches'][0]['response'] is not None, mode == 'garbage')
                validate_cascade(self.root, f'out-{mode}/cascade.json')

    def test_replay_refuses_tampering(self):
        jlc = self.jlc_capture('C38695', self.pdf.url + '/jlc-html.pdf')
        self.resolve(self.request(self.part('C38695', jlc={'capture': jlc, 'lcsc': 'C38695'}, exa=self.exa())),
                     allow_network=True)
        validate_cascade(self.root, 'cascade/cascade.json')
        report_path = self.root / 'cascade/cascade.json'
        original = report_path.read_bytes()

        def edit(change, pattern):
            report = json.loads(original)
            change(report)
            report_path.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, pattern):
                validate_cascade(self.root, 'cascade/cascade.json')
            report_path.write_bytes(original)

        edit(lambda r: r['parts']['C38695']['attempts'].pop(0), 'cascade order')
        edit(lambda r: r['parts']['C38695']['attempts'][0].update(outcome='rejected'), 'differs from its acquisition')
        edit(lambda r: r['parts']['C38695']['tiers'][3].update(urls=[]), 'Exa tier differs|attempts differ')
        edit(lambda r: r.update(allow_network=False), 'network opt-in')
        edit(lambda r: r['parts']['C38695'].update(outcome='unresolved'), 'outcome contradicts')
        edit(lambda r: r['request']['parts'][0]['exa'].update(query='other'), 'request identity')
        search = self.root / 'cascade/search/0.json'
        raw = search.read_bytes()
        search.write_bytes(raw.replace(b'exa.pdf', b'exb.pdf'))
        with self.assertRaisesRegex(ValueError, 'search response size/sha256'):
            validate_cascade(self.root, 'cascade/cascade.json')
        search.write_bytes(raw)
        selection = self.root / 'cascade/source-selection.json'
        document = read_json(selection)
        document['parts']['C38695']['failed'] = []
        write_json(selection, document)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            validate_cascade(self.root, 'cascade/cascade.json')

    def test_inputs_are_strict_and_refuse_before_any_output(self):
        jlc = self.jlc_capture('C38695', self.pdf.url + '/exa.pdf')
        good = self.request(self.part('C38695', jlc={'capture': jlc, 'lcsc': 'C38695'}))
        for change, pattern in (
                (lambda r: r['parts'][0].pop('catalog'), 'expected fields'),
                (lambda r: r['parts'].append(copy.deepcopy(r['parts'][0])), 'unique'),
                (lambda r: r['parts'][0].update(key='a#b'), 'unique'),
                (lambda r: r['parts'][0].update(exa={**self.exa(), 'max_results': 50}), 'max_results'),
                (lambda r: r['parts'][0].update(exa=self.exa(include_domains=['Bad/Domain'])), 'host names'),
                (lambda r: r['parts'][0]['jlc'].update(lcsc='C99'), 'no request for C99'),
                (lambda r: r['parts'][0]['jlc'].update(capture='../x.json'), 'unsafe|canonical')):
            request = copy.deepcopy(good)
            change(request)
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, pattern):
                self.resolve(request)
            self.assertFalse((self.root / 'cascade').exists())
        with self.assertRaisesRegex(ValueError, 'exa-search command'):
            self.resolve(good, allow_network=True, exa_command=[])
        (self.root / jlc).write_bytes((self.root / jlc).read_bytes().replace(b'"found"', b'"failed"'))
        with self.assertRaises(ValueError):
            self.resolve(good)
        self.assertEqual(list(self.root.glob('.datasheet-cascade-*')), [])

    def test_cli_resolve_reports_unresolved_and_validate_replays(self):
        jlc = self.jlc_capture('C38695', self.pdf.url + '/jlc-html.pdf')
        write_json(self.root / 'request.json', self.request(
            self.part('C38695', jlc={'capture': jlc, 'lcsc': 'C38695'}, exa=self.exa())))
        command = [sys.executable, '-m', 'agent_ee.datasheet_cascade']
        environment = {**os.environ, 'PYTHONPATH': str(Path(cascade.__file__).parents[1])}
        offline = subprocess.run(command + ['resolve', '--root', str(self.root), '--request', 'request.json',
                                            '--out', 'cli-offline'], capture_output=True, text=True, env=environment)
        self.assertEqual(offline.returncode, 2, offline.stderr)
        self.assertEqual(json.loads(offline.stdout)['unresolved'], ['C38695'])
        self.assertEqual(self.exa_calls(), [])
        online = subprocess.run(command + ['resolve', '--root', str(self.root), '--request', 'request.json',
                                           '--out', 'cli-online', '--allow-network', '--exa-command',
                                           self.exa_command[0], '--min-interval', '0.5'],
                                capture_output=True, text=True, env=environment)
        self.assertEqual(online.returncode, 0, online.stderr)
        self.assertEqual(len(self.exa_calls()), 1)
        replay = subprocess.run(command + ['validate', '--root', str(self.root), '--cascade',
                                           'cli-online/cascade.json'], capture_output=True, text=True, env=environment)
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertIn('cli-online/search/0.json', json.loads(replay.stdout)['dependencies'])

    def test_no_check_or_offline_module_imports_the_cascade_producer(self):
        root = Path(cascade.__file__).parent
        importers = [p.name for p in root.glob('*.py') if p.name != 'datasheet_cascade.py'
                     and 'datasheet_cascade' in p.read_text(encoding='utf-8')]
        self.assertEqual(importers, [])


class CascadeToBindingFillTests(CascadeFixture, unittest.TestCase):
    """The cascade's outputs pass part_record, qualify_manifest and binding_fill replay."""

    def setUp(self):
        test_binding_fill.BindingFillTests.setUp(self)
        self.make_fixture()
        self.part_entry = self.parts['parts'][0]
        self.original = copy.deepcopy(self.record)

    def fill(self):
        return test_binding_fill.BindingFillTests.fill(self)

    def test_wrong_family_and_html_supplier_urls_skipped_for_exa_pdf_then_manifest_and_fill_qualify(self):
        jlc = self.jlc_capture('C38695', self.pdf.url + '/jlc-html.pdf', name='live-jlc')
        dk = self.digikey_capture({'TJA1051T/3': (404, b'{"status":404,"title":"Not Found"}')})
        write_json(self.root / 'prior.json', {'schema': 'agent-ee.datasheet-source-selection/1', 'parts': {
            'C38695': {'selected': [], 'rejected': [{'id': 'supplier-wrong-family',
                                                      'sha256': self.original['artifact']['sha256'],
                                                      'reason': 'wrong-family catalog'}]}}})
        self.exa_results([self.pdf.url + '/exa.pdf'])
        request = self.request(self.part('C38695', digikey={'capture': dk, 'key': 'TJA1051T-3'},
                                         jlc={'capture': jlc, 'lcsc': 'C38695'}, exa=self.exa()),
                               prior={'path': 'prior.json', 'sha256': digest(self.root / 'prior.json')})
        report = self.resolve(request, allow_network=True)
        self.assertEqual(report['parts']['C38695']['tiers'][0]['status'], 'unavailable')
        self.assertEqual(self.attempts(report, 'C38695'), [('jlc', 'failed'), ('exa', 'selected')])
        validate_cascade(self.root, 'cascade/cascade.json')
        ref = {'path': 'cascade/source-selection.json', 'sha256': digest(self.root / 'cascade/source-selection.json'),
               'part_key': 'C38695'}
        record = prepare_record(self.root, self.original['lcsc'], self.original['catalog'],
                                self.original['datasheets'], self.original['datasheet_id'], source_selection=ref)
        self.assertEqual((record['datasheet_id'], record['artifact']['sha256']), ('C38695#exa-1', SHA['exa']))
        self.assertEqual(record['datasheets']['path'], 'cascade/datasheets/manifest.json')
        for key in ('lcsc', 'mpn', 'manufacturer', 'package', 'stock', 'catalog'):
            self.assertEqual(record[key], self.original[key])
        self.assertIn('cascade/source-selection.json', qualify_record(record, self.root))
        selection = self.part_entry['selection']
        write_json(self.root / selection['part_record'], record)
        selection['part_record_sha256'] = digest(self.root / selection['part_record'])
        self.part_entry['datasheet'].update(artifact=record['artifact'], source_selection=ref, provenance={
            **record['datasheets'], 'locator': 'parts[id=' + record['datasheet_id'] + ']'})
        self.assertEqual(prepare_manifest(self.parts, self.root), self.parts)
        qualified = qualify_manifest(self.parts, self.root)
        self.assertEqual(qualified['dependencies']['cascade/source-selection.json'], ref['sha256'])
        self.assertEqual(qualified['dependencies']['cascade/datasheets/manifest.json'], record['datasheets']['sha256'])
        intent, _ = self.fill()
        fields = intent['components'][0]['part_binding']
        self.assertEqual(fields['LCSC'], 'C38695')
        self.assertEqual(fields['Datasheet'], self.pdf.url + '/exa.pdf')
        self.assertEqual(json.loads(fields['PARAM_Datasheet'])['sha256'], SHA['exa'])
        # Binding the failed HTML acquisition instead of the selected PDF is refused.
        failed = read_json(self.root / 'cascade/source-selection.json')['parts']['C38695']['failed'][0]
        self.assertEqual(failed['id'], 'C38695#jlc')
        with self.assertRaises(ValueError):
            prepare_record(self.root, self.original['lcsc'], self.original['catalog'],
                           record['datasheets'], 'C38695#jlc')

    def test_unresolved_cascade_is_refused_by_part_record(self):
        jlc = self.jlc_capture('C38695', None, name='live-jlc')
        report = self.resolve(self.request(self.part('C38695', jlc={'capture': jlc, 'lcsc': 'C38695'})))
        self.assertEqual(report['parts']['C38695']['tiers'][1]['reason'], 'JLCPCB row has no dataManualUrl')
        self.assertEqual(report['parts']['C38695']['outcome'], 'unresolved')
        ref = {'path': 'cascade/source-selection.json', 'sha256': digest(self.root / 'cascade/source-selection.json'),
               'part_key': 'C38695'}
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous source selection for C38695'):
            prepare_record(self.root, self.original['lcsc'], self.original['catalog'],
                           self.original['datasheets'], self.original['datasheet_id'], source_selection=ref)


if __name__ == '__main__':
    unittest.main()

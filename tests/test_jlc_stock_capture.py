"""AGENTEE-155 owner-run JLCPCB stock producer, against an owned loopback stub.

No test contacts JLCPCB. Responses are original synthetic documents in the
shape the producer parses; the live response shape is qualified only by an
owner-run capture.
"""
import contextlib
import hashlib
import io
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent_ee import jlc_stock_capture as jsc
from agent_ee.jlc_stock_capture import (capture_jlc_stock, parse_search_response, request_body,
                                        validate_capture)


def row(lcsc, stock, library='base', preferred=False, **extra):
    """One owned synthetic search row."""
    return {'componentCode': lcsc, 'componentModelEn': f'MPN-{lcsc}', 'componentBrandEn': 'Example Mfr',
            'componentSpecificationEn': '0603', 'firstSortName': 'Resistors', 'stockCount': stock,
            'componentLibraryType': library, 'preferredComponentFlag': preferred,
            'componentPrices': [{'startNumber': 100, 'endNumber': 999, 'productPrice': 0.002},
                                {'startNumber': 1, 'endNumber': 99, 'productPrice': 0.004}], **extra}


def search(*rows, code=200):
    return json.dumps({'code': code, 'data': {'componentPageInfo': {'total': len(rows), 'list': list(rows)}},
                       'message': None}).encode()


class JlcStub:
    """Loopback search endpoint; ``routes[lcsc]`` is (HTTP status, body bytes) or 'slow'."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                stub.requests.append({'at': time.monotonic(), 'path': self.path, 'body': body,
                                      'headers': {k.lower(): v for k, v in self.headers.items()}})
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/search')
                    self.end_headers()
                    return
                lcsc = json.loads(body)['keyword']
                route = stub.routes.get(lcsc, (200, search()))
                if route == 'slow':
                    time.sleep(1.5)
                    return
                status, payload = route
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/search'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def stub(test, routes):
    server = JlcStub(routes)
    test.addCleanup(server.close)
    return server


class JlcStockCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_capture_one_request_per_id_rate_limited_raw_bytes_hashed(self):
        found = search(row('C25804', 5000), row('C25805', 7))  # a neighbouring row is not a match
        preferred = search(row('C17024', 300, library='expand', preferred=True))
        server = stub(self, {'C25804': (200, found), 'C17024': (200, preferred),
                             'C90001': (200, search()), 'C90002': (503, b'{"code":503}'),
                             'C90003': (200, b'<html>blocked</html>')})
        ids = ['C90003', 'C25804', 'C90001', 'C17024', 'C90002']
        doc = capture_jlc_stock(ids, self.root / 'cap', allow_network=True, endpoint=server.url,
                                min_interval=1.0, timeout=10)
        # One POST per id, in numeric id order, with the recorded body and no credentials.
        self.assertEqual([json.loads(r['body'])['keyword'] for r in server.requests],
                         ['C17024', 'C25804', 'C90001', 'C90002', 'C90003'])
        for request in server.requests:
            self.assertEqual(request['body'], request_body(json.loads(request['body'])['keyword']))
            self.assertFalse({'authorization', 'cookie', 'proxy-authorization'} & set(request['headers']))
            self.assertEqual(request['headers']['user-agent'], jsc.USER_AGENT)
        starts = [r['at'] for r in server.requests]
        self.assertTrue(all(b - a >= 1.0 for a, b in zip(starts, starts[1:])), starts)

        statuses = {r['lcsc']: (r['status'], r['http_status']) for r in doc['requests']}
        self.assertEqual(statuses, {'C17024': ('found', 200), 'C25804': ('found', 200),
                                    'C90001': ('not_found', 200), 'C90002': ('failed', 503),
                                    'C90003': ('failed', 200)})
        by_id = {r['lcsc']: r for r in doc['requests']}
        self.assertEqual(by_id['C90002']['reason'], 'HTTP 503')
        self.assertIn('unrecognised response', by_id['C90003']['reason'])
        served = {'C25804': found, 'C17024': preferred, 'C90001': search(),
                  'C90002': b'{"code":503}', 'C90003': b'<html>blocked</html>'}
        for lcsc, raw in served.items():
            retained = (self.root / 'cap' / by_id[lcsc]['response']['path']).read_bytes()
            self.assertEqual(retained, raw)
            self.assertEqual(by_id[lcsc]['response']['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertTrue(by_id[lcsc]['fetched_at'].endswith('+00:00'))
            self.assertLessEqual(doc['as_of'], by_id[lcsc]['fetched_at'])
        self.assertEqual(sorted(doc['parts']), ['C17024', 'C25804'])
        self.assertEqual(doc['parts']['C25804'], {
            'lcsc': 'C25804', 'stock': 5000, 'library_tier': 'Basic', 'mfr': 'Example Mfr',
            'mpn': 'MPN-C25804', 'package': '0603', 'category': 'Resistors', 'price1': 0.004,
            'last_seen': by_id['C25804']['fetched_at']})
        self.assertEqual(doc['parts']['C17024']['library_tier'], 'Preferred')
        on_disk = json.loads((self.root / 'cap/catalog.json').read_text())
        self.assertEqual(on_disk, doc)
        self.assertEqual(on_disk['schema'], 'agent-ee.jlc-catalog/1')
        self.assertEqual(on_disk['source']['min_interval_seconds'], 1.0)
        self.assertEqual(list(self.root.glob('.jlc-stock-*')), [])

    def test_opt_in_limits_and_unsafe_targets_refuse_before_any_request(self):
        server = stub(self, {})
        with self.assertRaises(PermissionError):
            capture_jlc_stock(['C1'], self.root / 'no-opt-in', endpoint=server.url)
        with self.assertRaises(SystemExit) as cli:
            jsc.main(['C1', '--out', str(self.root / 'cli'), '--endpoint', server.url])
        self.assertEqual(cli.exception.code, 2)
        bad = {'interval': dict(min_interval=0.5), 'timeout': dict(timeout=61),
               'credentials': dict(endpoint='http://user:pw@127.0.0.1/search'),
               'scheme': dict(endpoint='file:///etc/passwd')}
        for label, kwargs in bad.items():
            with self.subTest(label), self.assertRaises(ValueError):
                capture_jlc_stock(['C1'], self.root / label, allow_network=True,
                                  **{'endpoint': server.url, **kwargs})
        for label, ids in {'bulk': [f'C{i}' for i in range(1, jsc.MAX_IDS + 2)], 'empty': [],
                           'duplicate': ['C1', 'C1'], 'invalid': ['C01']}.items():
            with self.subTest(label), self.assertRaises(ValueError):
                capture_jlc_stock(ids, self.root / label, allow_network=True, endpoint=server.url)
        (self.root / 'exists').mkdir()
        with self.assertRaises(FileExistsError):
            capture_jlc_stock(['C1'], self.root / 'exists', allow_network=True, endpoint=server.url)
        self.assertEqual(server.requests, [])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['exists'])

    def test_redirect_and_deadline_are_failed_requests_not_observations(self):
        server = stub(self, {'C2': 'slow'})
        redirect = server.url.replace('/search', '/redirect')
        doc = capture_jlc_stock(['C1'], self.root / 'redirect', allow_network=True, endpoint=redirect)
        record = doc['requests'][0]
        self.assertEqual((record['status'], record['http_status'], record['reason']), ('failed', 302, 'HTTP 302'))
        self.assertEqual(len(server.requests), 1)  # the redirect target was never requested
        doc = capture_jlc_stock(['C2'], self.root / 'slow', allow_network=True, endpoint=server.url, timeout=0.5)
        record = doc['requests'][0]
        self.assertEqual((record['status'], record['response'], record['reason']),
                         ('failed', None, 'fetch deadline exceeded'))
        self.assertEqual(doc['parts'], {})
        checked = validate_capture(doc, lambda rel: (self.root / 'slow' / rel).read_bytes())
        self.assertEqual(checked['response_verdicts']['C2'],
                         {'path': None, 'sha256': None, 'verdict': 'missing'})

    def test_cli_publishes_capture_and_reports_failed_ids(self):
        server = stub(self, {'C5': (200, search(row('C5', 10)))})
        ids_file = self.root / 'ids.json'
        ids_file.write_text('["C5"]')
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = jsc.main(['--ids-file', str(ids_file), '--out', str(self.root / 'ok'), '--allow-network',
                             '--endpoint', server.url])
        self.assertEqual(code, 0)
        summary = json.loads(printed.getvalue())
        self.assertEqual(summary['catalog_sha256'],
                         hashlib.sha256((self.root / 'ok/catalog.json').read_bytes()).hexdigest())
        self.assertEqual(summary['failed'], [])
        self.assertEqual(json.loads((self.root / 'ok/catalog.json').read_text())['parts']['C5']['stock'], 10)
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = jsc.main(['C6', '--out', str(self.root / 'missing'), '--allow-network', '--endpoint',
                             server.url.replace('/search', '/redirect')])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(printed.getvalue())['failed'], ['C6'])
        self.assertTrue((self.root / 'missing/catalog.json').is_file())

    def test_parser_reads_only_exact_jlc_rows_and_refuses_unknown_shapes(self):
        self.assertEqual(parse_search_response(search(row('C7', 0, library='expand')), 'C7')[1]['library_tier'],
                         'Extended')
        self.assertEqual(parse_search_response(search(row('C70', 9)), 'C7'), ('not_found', None))
        self.assertEqual(parse_search_response(json.dumps(
            {'code': 200, 'data': {'componentPageInfo': {'total': 0, 'list': None}}}).encode(), 'C7'),
            ('not_found', None))
        lcsc_only = row('C7', 5)
        del lcsc_only['stockCount']
        lcsc_only['lcscStock'] = 5  # an LCSC stock figure is not JLCPCB stock
        bad = {'duplicate rows': search(row('C7', 1), row('C7', 2)), 'lcsc stock only': search(lcsc_only),
               'bool stock': search(row('C7', True)), 'negative stock': search(row('C7', -1)),
               'unknown tier': search(row('C7', 1, library='consigned')),
               'malformed tier': search(row('C7', 1, library=[])),
               'code': search(row('C7', 1), code=500), 'no list': b'{"code":200,"data":{}}',
               'duplicate key': b'{"code":200,"code":200,"data":{"componentPageInfo":{"list":[]}}}',
               'nan': b'{"code":200,"data":{"componentPageInfo":{"list":[]}},"x":NaN}',
               'html': b'<html></html>'}
        for label, raw in bad.items():
            with self.subTest(label), self.assertRaises(ValueError):
                parse_search_response(raw, 'C7')

    def test_validate_capture_rederives_parts_and_refuses_tampering(self):
        server = stub(self, {'C5': (200, search(row('C5', 10))), 'C6': (200, search())})
        doc = capture_jlc_stock(['C5', 'C6'], self.root / 'cap', allow_network=True, endpoint=server.url,
                                min_interval=1.0)
        read = lambda rel: (self.root / 'cap' / rel).read_bytes()  # noqa: E731
        info = validate_capture(doc, read)
        self.assertEqual((info['requested'], info['not_found'], info['failed']), (['C5', 'C6'], ['C6'], {}))

        def tampered(change):
            copy = json.loads(json.dumps(doc))
            change(copy)
            return copy
        cases = {
            'stock': lambda d: d['parts']['C5'].update(stock=99),
            'dropped part': lambda d: d['parts'].pop('C5'),
            'invented part': lambda d: d['parts'].update(C6={**d['parts']['C5'], 'lcsc': 'C6'}),
            'status': lambda d: d['requests'][1].update(status='found'),
            'hash': lambda d: d['requests'][0]['response'].update(sha256='0' * 64),
            'boolean size': lambda d: d['requests'][0]['response'].update(size_bytes=True),
            'negative size': lambda d: d['requests'][0]['response'].update(size_bytes=-1),
            'uppercase hash': lambda d: d['requests'][0]['response'].update(sha256='A' * 64),
            'unknown request key': lambda d: d['requests'][0].update(source_url='unverified'),
            'unknown source key': lambda d: d['source'].update(producer='unverified'),
            'unknown response key': lambda d: d['requests'][0]['response'].update(http_status=200),
            'path': lambda d: d['requests'][0]['response'].update(path='../C5.json'),
            'fetched before as_of': lambda d: d['requests'][0].update(fetched_at='2000-01-01T00:00:00+00:00'),
            'stale_before': lambda d: d.update(stale_before='2026-01-01'),
            'interval': lambda d: d['source'].update(min_interval_seconds=0.1),
            'endpoint credentials': lambda d: d['source'].update(endpoint='https://u:p@example.org/search'),
            'extra key': lambda d: d.update(snapshot_date='2026-10-18'),
            'naive time': lambda d: d.update(as_of=d['as_of'].replace('+00:00', '')),
        }
        for label, change in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                validate_capture(tampered(change), read)
        (self.root / 'cap/responses/C5.json').write_bytes(search(row('C5', 99)))
        with self.assertRaises(ValueError):
            validate_capture(doc, read)


if __name__ == '__main__':
    unittest.main()

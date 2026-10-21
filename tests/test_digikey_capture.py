"""AGENTEE-246 owner-run DigiKey API v4 producer and offline validator, against an owned loopback stub.

No test contacts DigiKey or uses real credentials. Every response is an
original synthetic document in the v4 shape the parser reads, with invented
part numbers; the live response shape is qualified only by an owner-run capture.
"""
import base64
import contextlib
import hashlib
import io
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_ee import datasheet_capture
from agent_ee import digikey_capture as dk
from agent_ee.digikey_capture import (Credentials, capture_digikey, load_capture, load_credentials,
                                      parse_response, project_external, validate_capture,
                                      verify_external_identity)
from agent_ee.storage import canonical

CLIENT_ID = 'synthetic-client-id-0001'
CLIENT_SECRET = 'synthetic-client-secret-s3cr3t'
TOKEN = 'synthetic-access-token-t0k3n'
PDF = b'%PDF-1.4\n% owned synthetic datasheet\n%%EOF\n'


def product(mpn, *, quantity=120, price=1.25, datasheet='https://example.invalid/ds.pdf', variations=None,
            parameters=None, manufacturer='Example Connector Co.'):
    """One owned synthetic v4 Product object."""
    return {'ManufacturerProductNumber': mpn, 'Manufacturer': {'Id': 9001, 'Name': manufacturer},
            'Description': {'ProductDescription': 'SYNTHETIC HEADER', 'DetailedDescription': 'Owned fixture'},
            'UnitPrice': price, 'QuantityAvailable': quantity, 'DatasheetUrl': datasheet,
            'ProductStatus': {'Id': 0, 'Status': 'Active'},
            'ProductVariations': variations if variations is not None else [
                {'DigiKeyProductNumber': f'{mpn}-TR-ND', 'MinimumOrderQuantity': 1000},
                {'DigiKeyProductNumber': f'{mpn}-CT-ND', 'MinimumOrderQuantity': 1}],
            'Parameters': parameters if parameters is not None else [
                {'ParameterId': 16, 'ParameterText': 'Package / Case', 'ValueText': 'Synthetic 35-pin header'},
                {'ParameterId': 1500, 'ParameterText': 'Height - Seated (Max)', 'ValueText': '12.30mm'},
                {'ParameterId': 252, 'ParameterText': 'Operating Temperature', 'ValueText': '-40°C ~ 125°C'},
                {'ParameterId': 88, 'ParameterText': 'Number of Positions', 'ValueText': '35'}]}


def details(item):
    return json.dumps({'Product': item, 'SearchLocaleUsed': {'Site': 'US', 'Language': 'en', 'Currency': 'USD'}},
                      ensure_ascii=False).encode()


# AGENTEE-262 regression shapes. Owned synthetic documents that model the two
# live v4 ProductDetails shapes reported for these real part numbers; the part
# numbers, manufacturer names and parameter values are facts quoted from the
# ticket, everything else (descriptions, URLs, prices, ids) is invented.
def zero_stock_module(*, quantity='omitted'):
    """NEO-M9N-00B shape: zero stock on every variation and no top-level QuantityAvailable."""
    item = product('NEO-M9N-00B', manufacturer='u-blox', price=None,
                   datasheet='https://example.invalid/neo-m9n.pdf', variations=[
                       {'DigiKeyProductNumber': 'SYN-NEO-M9N-00BTR-ND', 'MinimumOrderQuantity': 250,
                        'QuantityAvailableforPackageType': 0},
                       {'DigiKeyProductNumber': 'SYN-NEO-M9N-00BCT-ND', 'MinimumOrderQuantity': 1,
                        'QuantityAvailableforPackageType': 0}],
                   parameters=[{'ParameterId': 16, 'ParameterText': 'Package / Case', 'ValueText': '24-SMD Module'},
                               {'ParameterId': 252, 'ParameterText': 'Operating Temperature',
                                'ValueText': '-40°C ~ 85°C'}])
    if quantity == 'omitted':
        del item['QuantityAvailable']
    else:
        item['QuantityAvailable'] = quantity
    return item


CONNECTOR_PARAMETERS = [
    {'ParameterId': 2000, 'ParameterText': 'Connector Type', 'ValueText': 'Header, Male Pins'},
    {'ParameterId': 88, 'ParameterText': 'Number of Positions', 'ValueText': '23'},
    {'ParameterId': 89, 'ParameterText': 'Number of Rows', 'ValueText': '3'},
    {'ParameterId': 90, 'ParameterText': 'Pitch - Mating', 'ValueText': '0.157" (4.00mm)'},
    {'ParameterId': 69, 'ParameterText': 'Mounting Type', 'ValueText': 'Panel Mount, Through Hole, Right Angle'},
    {'ParameterId': 252, 'ParameterText': 'Operating Temperature', 'ValueText': '-40°C ~ 125°C'}]
CONNECTOR_PACKAGE = ('Connector Type: Header, Male Pins; Number of Positions: 23; Number of Rows: 3; '
                     'Pitch - Mating: 0.157" (4.00mm); Mounting Type: Panel Mount, Through Hole, Right Angle')


def connector(parameters=None):
    """776228-1 shape: connector parameters and no 'Package / Case' parameter."""
    return product('776228-1', manufacturer='TE Connectivity AMP Connectors',
                   datasheet='https://example.invalid/776228.pdf', variations=[
                       {'DigiKeyProductNumber': 'SYN-A106-ND', 'MinimumOrderQuantity': 1,
                        'QuantityAvailableforPackageType': 41}],
                   parameters=CONNECTOR_PARAMETERS if parameters is None else parameters)


def keyword(*items, exact=()):
    return json.dumps({'Products': list(items), 'ProductsCount': len(items), 'ExactMatches': list(exact)}).encode()


class DigiKeyStub:
    """Loopback DigiKey v4 API. ``routes[query]`` is (status, body[, headers]) or 'slow'."""

    def __init__(self, routes, *, token_status=200, expires_in=600):
        self.routes, self.token_status, self.expires_in = dict(routes), token_status, expires_in
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, status, payload, headers=()):
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                for name, value in headers:
                    self.send_header(name, value)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _record(self, body):
                stub.requests.append({'at': time.monotonic(), 'method': self.command, 'path': self.path,
                                      'body': body, 'headers': {k.lower(): v for k, v in self.headers.items()}})

            def _route(self, query):
                headers = stub.requests[-1]['headers']
                if (headers.get('authorization') != 'Bearer ' + TOKEN or
                        headers.get('x-digikey-client-id') != CLIENT_ID):
                    return self._reply(401, b'{"status":401,"title":"Unauthorized"}')
                route = stub.routes.get(query, (404, b'{"status":404,"title":"Not Found"}'))
                if route == 'slow':
                    time.sleep(1.5)
                    return
                self._reply(*route)

            def do_GET(self):
                self._record(b'')
                if self.path.startswith('/ds/'):
                    return self._reply(200, PDF)
                prefix, suffix = '/products/v4/search/', '/productdetails'
                if self.path.startswith(prefix) and self.path.endswith(suffix):
                    return self._route(urllib.parse.unquote(self.path[len(prefix):-len(suffix)]))
                self._reply(404, b'{}')

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self._record(body)
                if self.path == '/v1/oauth2/token':
                    form = urllib.parse.parse_qs(body.decode())
                    if (stub.token_status != 200 or form != {'client_id': [CLIENT_ID],
                            'client_secret': [CLIENT_SECRET], 'grant_type': ['client_credentials']}):
                        return self._reply(stub.token_status if stub.token_status != 200 else 401,
                                           b'{"error":"invalid_client"}')
                    return self._reply(200, json.dumps({'access_token': TOKEN, 'token_type': 'Bearer',
                                                        'expires_in': stub.expires_in}).encode())
                if self.path == '/products/v4/search/keyword':
                    return self._route(json.loads(body)['Keywords'])
                self._reply(404, b'{}')

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


CREDENTIALS = Credentials(CLIENT_ID, CLIENT_SECRET)


def q(key, query=None, kind='product'):
    return {'key': key, 'kind': kind, 'query': query or key}


class DigiKeyCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def stub(self, routes, **kwargs):
        server = DigiKeyStub(routes, **kwargs)
        self.addCleanup(server.close)
        return server

    def capture(self, server, items, name='cap', **kwargs):
        return capture_digikey(items, self.root / name, allow_network=True, credentials=CREDENTIALS,
                               base=server.url, **{'min_interval': 0.5, 'timeout': 10, **kwargs})

    def test_oauth_exchange_lookup_rate_limit_and_retained_bytes(self):
        header = details(product('AE-HDR-35', datasheet='//example.invalid/hdr.pdf'))
        module = details(product('AE-GNSS-9', manufacturer='Example Module Ltd', variations=[], datasheet=None))
        search = keyword(product('AE-SW-2'), product('AE-SW-20'))
        served = {'AE-HDR-35': (200, header), 'AE-GNSS-9': (200, module), 'AE-SW-2': (200, search),
                  'AE-BUSY': (429, b'{"status":429}', [('Retry-After', '1')])}
        server = self.stub(served)
        children = []
        real_popen = subprocess.Popen

        def launch(*args, **kwargs):
            children.append(args[0])
            return real_popen(*args, **kwargs)
        items = [q('hdr', 'AE-HDR-35'), q('gnss', 'AE-GNSS-9'), q('sw', 'AE-SW-2', 'keyword'),
                 q('busy', 'AE-BUSY'), q('zz-missing', 'AE-ABSENT')]
        with patch.object(datasheet_capture.subprocess, 'Popen', launch):
            doc = self.capture(server, items)
        # 2-legged client-credentials exchange first, then bearer + client-id on every lookup.
        token, *lookups = server.requests
        self.assertEqual((token['method'], token['path']), ('POST', '/v1/oauth2/token'))
        self.assertEqual(token['headers']['content-type'], 'application/x-www-form-urlencoded')
        self.assertEqual([(r['method'], r['path']) for r in lookups], [  # keys run in sorted order
            ('GET', '/products/v4/search/AE-BUSY/productdetails'),
            ('GET', '/products/v4/search/AE-GNSS-9/productdetails'),
            ('GET', '/products/v4/search/AE-HDR-35/productdetails'),
            ('POST', '/products/v4/search/keyword'),
            ('GET', '/products/v4/search/AE-ABSENT/productdetails')])
        for request in lookups:
            self.assertEqual(request['headers']['authorization'], 'Bearer ' + TOKEN)
            self.assertEqual(request['headers']['x-digikey-client-id'], CLIENT_ID)
            self.assertEqual(request['headers']['user-agent'], dk.USER_AGENT)
            self.assertFalse({'cookie', 'proxy-authorization'} & set(request['headers']))
        self.assertEqual(lookups[3]['body'], dk.keyword_body('AE-SW-2'))
        # Rate limit: every exchange (token included) is paced; Retry-After widens the next gap.
        starts = [r['at'] for r in server.requests]
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertTrue(all(gap >= 0.5 for gap in gaps), gaps)
        self.assertGreaterEqual(gaps[1], 1.0, gaps)
        # Credentials travel only on the child's stdin: never in argv, the manifest or a response.
        self.assertEqual(len(children), 6)
        for argv in children:
            self.assertEqual(argv[-2:], ['agent_ee.digikey_capture', '--fetch'])
        on_disk = (self.root / 'cap' / dk.MANIFEST).read_bytes()
        for secret in (CLIENT_ID, CLIENT_SECRET, TOKEN):
            self.assertNotIn(secret.encode(), on_disk)
            for path in (self.root / 'cap').rglob('*'):
                self.assertNotIn(secret.encode(), path.read_bytes() if path.is_file() else b'')
        self.assertEqual(json.loads(on_disk), doc)
        self.assertEqual(doc['token_exchanges'], [{'fetched_at': doc['token_exchanges'][0]['fetched_at'],
                                                   'http_status': 200, 'expires_in': 600}])
        by_key = {r['key']: r for r in doc['requests']}
        self.assertEqual({k: (r['status'], r['http_status'], r['reason']) for k, r in by_key.items()}, {
            'busy': ('failed', 429, 'HTTP 429'), 'gnss': ('found', 200, None), 'hdr': ('found', 200, None),
            'sw': ('found', 200, None), 'zz-missing': ('failed', 404, 'HTTP 404')})
        for key, query in (('hdr', 'AE-HDR-35'), ('gnss', 'AE-GNSS-9'), ('sw', 'AE-SW-2'), ('busy', 'AE-BUSY')):
            raw = (self.root / 'cap' / by_key[key]['response']['path']).read_bytes()
            self.assertEqual(raw, served[query][1])
            self.assertEqual(by_key[key]['response']['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertTrue(by_key[key]['fetched_at'].endswith('+00:00'))
            self.assertLessEqual(doc['as_of'], by_key[key]['fetched_at'])
        self.assertEqual(doc['parts']['hdr'], {
            'query': 'AE-HDR-35', 'mpn': 'AE-HDR-35', 'manufacturer': 'Example Connector Co.',
            'digikey_part_number': 'AE-HDR-35-CT-ND', 'package': 'Synthetic 35-pin header',
            'package_source': {'rule': 'package_case', 'parameters': ['Package / Case']},
            'primary_datasheet_url': 'https://example.invalid/hdr.pdf', 'quantity_available': 120,
            'unit_price': 1.25, 'product_status': 'Active', 'seated_height': '12.30mm',
            'operating_temperature': '-40°C ~ 125°C', 'parameters': [
                {'parameter': 'Package / Case', 'value': 'Synthetic 35-pin header'},
                {'parameter': 'Height - Seated (Max)', 'value': '12.30mm'},
                {'parameter': 'Operating Temperature', 'value': '-40°C ~ 125°C'},
                {'parameter': 'Number of Positions', 'value': '35'}],
            'last_seen': by_key['hdr']['fetched_at']})
        self.assertEqual((doc['parts']['gnss']['digikey_part_number'], doc['parts']['gnss']['primary_datasheet_url']),
                         (None, None))
        self.assertEqual(doc['parts']['sw']['mpn'], 'AE-SW-2')  # AE-SW-20 is not an exact match
        self.assertEqual(sorted(doc['parts']), ['gnss', 'hdr', 'sw'])
        self.assertEqual(list(self.root.glob('.digikey-capture-*')), [])
        # The producer records failures; a consumer refuses them unless it opts in.
        with self.assertRaisesRegex(ValueError, 'busy failed'):
            load_capture(self.root, 'cap/' + dk.MANIFEST)
        info = load_capture(self.root, 'cap/' + dk.MANIFEST, allow_failed=True)[1]
        self.assertEqual((info['found'], sorted(info['failed'])), (['gnss', 'hdr', 'sw'], ['busy', 'zz-missing']))

    def test_refuses_before_any_socket_or_output_without_opt_in_or_credentials(self):
        server = self.stub({})

        def no_socket(*args, **kwargs):
            raise AssertionError('a socket was opened')

        def no_child(*args, **kwargs):
            raise AssertionError('a request child was started')
        env = {k: v for k, v in os.environ.items() if k not in dk.CREDENTIAL_NAMES}
        with patch.object(socket.socket, 'connect', no_socket), \
                patch.object(datasheet_capture.subprocess, 'Popen', no_child), patch.dict(os.environ, env, clear=True):
            with self.assertRaises(PermissionError):
                capture_digikey([q('a')], self.root / 'no-opt-in', credentials=CREDENTIALS, base=server.url)
            with self.assertRaises(PermissionError):
                capture_digikey([q('a')], self.root / 'no-creds', allow_network=True, base=server.url)
            for client_id, secret in (('', CLIENT_SECRET), (CLIENT_ID, None), (CLIENT_ID, 'has space')):
                with self.subTest(client_id=client_id), self.assertRaises(PermissionError) as raised:
                    load_credentials(environ={'DIGIKEY_CLIENT_ID': client_id, 'DIGIKEY_CLIENT_SECRET': secret})
                self.assertNotIn(CLIENT_SECRET, str(raised.exception))
            with self.assertRaises(PermissionError):
                load_credentials()  # the environment has neither name
            for argv in (['capture', 'AE-X', '--out', str(self.root / 'cli-no-opt-in'), '--base-url', server.url],
                         ['capture', 'AE-X', '--out', str(self.root / 'cli-no-creds'), '--allow-network',
                          '--base-url', server.url]):
                with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit) as cli:
                    dk.main(argv)
                self.assertEqual(cli.exception.code, 2)
            bad = {'interval': dict(min_interval=0.1), 'timeout': dict(timeout=61),
                   'plain http': dict(base='http://api.example.invalid'),
                   'path': dict(base=server.url + '/v1'), 'userinfo': dict(base='https://u:p@api.example.invalid')}
            for label, kwargs in bad.items():
                with self.subTest(label), self.assertRaises(ValueError):
                    capture_digikey([q('a')], self.root / label, allow_network=True, credentials=CREDENTIALS,
                                    **{'base': server.url, **kwargs})
            for label, items in {'bulk': [q(f'k{i}') for i in range(dk.MAX_QUERIES + 1)], 'empty': [],
                                 'duplicate': [q('a'), q('a')], 'bad key': [q('../a', 'X')],
                                 'kind': [q('a', 'X', 'parametric')], 'query': [q('a', ' X')]}.items():
                with self.subTest(label), self.assertRaises(ValueError):
                    capture_digikey(items, self.root / label, allow_network=True, credentials=CREDENTIALS,
                                    base=server.url)
            (self.root / 'exists').mkdir()
            with self.assertRaises(FileExistsError):
                capture_digikey([q('a')], self.root / 'exists', allow_network=True, credentials=CREDENTIALS,
                                base=server.url)
        self.assertEqual(server.requests, [])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['exists'])
        # Credentials the endpoint rejects: only the token exchange is sent, and nothing is published.
        with self.assertRaisesRegex(PermissionError, 'token exchange failed: HTTP 401') as raised:
            capture_digikey([q('a')], self.root / 'rejected', allow_network=True,
                            credentials=Credentials(CLIENT_ID, 'wrong-secret'), base=server.url, min_interval=0.5)
        self.assertNotIn('wrong-secret', str(raised.exception))
        self.assertEqual([r['path'] for r in server.requests], ['/v1/oauth2/token'])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['exists'])

    def test_secrets_file_is_private_and_never_echoed(self):
        secrets = self.root / 'secrets.env'
        secrets.write_text(f'# owner secrets\nOTHER=1\nexport DIGIKEY_CLIENT_ID="{CLIENT_ID}"\n'
                           f'DIGIKEY_CLIENT_SECRET={CLIENT_SECRET}\n')
        secrets.chmod(0o600)
        credentials = load_credentials(secrets)
        self.assertEqual((credentials.client_id, credentials.client_secret), (CLIENT_ID, CLIENT_SECRET))
        self.assertNotIn(CLIENT_SECRET, repr(credentials))
        secrets.chmod(0o640)
        with self.assertRaisesRegex(PermissionError, 'not group/other accessible'):
            load_credentials(secrets)
        secrets.chmod(0o600)
        (self.root / 'link.env').symlink_to(secrets)
        with self.assertRaisesRegex(PermissionError, 'not a link'):
            load_credentials(self.root / 'link.env')
        twice = self.root / 'twice.env'
        twice.write_text(f'DIGIKEY_CLIENT_ID={CLIENT_ID}\nDIGIKEY_CLIENT_ID=x\nDIGIKEY_CLIENT_SECRET=y\n')
        twice.chmod(0o600)
        with self.assertRaises(PermissionError) as raised:
            load_credentials(twice)
        self.assertNotIn(CLIENT_ID, str(raised.exception))

    def test_cli_capture_validate_and_project(self):
        server = self.stub({'AE-HDR-35': (200, details(product('AE-HDR-35')))})
        secrets = self.root / 'secrets.env'
        secrets.write_text(f'DIGIKEY_CLIENT_ID={CLIENT_ID}\nDIGIKEY_CLIENT_SECRET={CLIENT_SECRET}\n')
        secrets.chmod(0o600)
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = dk.main(['capture', 'AE-HDR-35', '--out', str(self.root / 'cap'), '--allow-network',
                            '--secrets-file', str(secrets), '--base-url', server.url, '--min-interval', '0.5'])
        self.assertEqual(code, 0)
        summary = json.loads(printed.getvalue())
        self.assertEqual(summary['capture_sha256'],
                         hashlib.sha256((self.root / 'cap' / dk.MANIFEST).read_bytes()).hexdigest())
        self.assertEqual(summary['failed'], [])
        self.assertNotIn(CLIENT_SECRET, printed.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(dk.main(['validate', '--root', str(self.root), '--capture', 'cap/' + dk.MANIFEST]), 0)
        self.assertEqual(json.loads(printed.getvalue())['found'], ['AE-HDR-35'])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dk.main(['project', '--root', str(self.root), '--capture', 'cap/' + dk.MANIFEST,
                                      '--key', 'AE-HDR-35', '--out', str(self.root / 'projection')]), 0)
        identity = json.loads((self.root / 'projection/external-identity.json').read_text())
        self.assertEqual(identity['schema'], 'agent-ee.external-part-identity/1')
        self.assertEqual(json.loads((self.root / 'projection/datasheet-bindings.json').read_text()),
                         [{'id': 'AE-HDR-35', 'datasheet_url': 'https://example.invalid/ds.pdf'}])
        verify_external_identity(self.root, identity)

    def test_deadline_and_token_refresh(self):
        # This test exercises session timing and capture outcomes. Real HTTP and
        # child cleanup are covered separately; host scheduling must not decide
        # which of these synthetic exchanges exceeds its deadline.
        elapsed = 0.0
        requests = []
        payloads = {'AE-A': details(product('AE-A')), 'AE-B': details(product('AE-B')),
                    'AE-ECHO': json.dumps({'echo': TOKEN}).encode()}

        def advance(seconds):
            nonlocal elapsed
            self.assertGreaterEqual(seconds, 0)
            elapsed += seconds

        def child(command, seconds, stdin=None):
            self.assertEqual(command[-2:], ['agent_ee.digikey_capture', '--fetch'])
            self.assertEqual(seconds, 0.8)
            spec = json.loads(stdin)
            self.assertEqual(spec['seconds'], seconds)
            url = urllib.parse.urlsplit(spec['url'])
            self.assertEqual(url.netloc, '127.0.0.1:1')
            requests.append(url.path)
            if url.path == dk.TOKEN_PATH:
                self.assertEqual(spec['method'], 'POST')
                self.assertEqual(urllib.parse.parse_qs(base64.b64decode(spec['body']).decode()),
                                 {'client_id': [CLIENT_ID], 'client_secret': [CLIENT_SECRET],
                                  'grant_type': ['client_credentials']})
                raw = json.dumps({'access_token': TOKEN, 'token_type': 'Bearer', 'expires_in': 2}).encode()
            else:
                self.assertEqual(spec['method'], 'GET')
                self.assertEqual(spec['headers']['Authorization'], 'Bearer ' + TOKEN)
                self.assertEqual(spec['headers']['X-DIGIKEY-Client-Id'], CLIENT_ID)
                query = url.path.removeprefix('/products/v4/search/').removesuffix('/productdetails')
                self.assertEqual(url.path, dk.PRODUCT_PATH.format(query=query))
                if query == 'AE-SLOW':
                    advance(seconds)
                    return None  # The owned-child runner's timeout contract.
                raw = payloads[query]  # An unexpected request must fail the test.
            advance(0.1)
            return json.dumps({'http_status': 200, 'body': base64.b64encode(raw).decode(),
                               'reason': None, 'retry_after': None}).encode()

        clock = SimpleNamespace(monotonic=lambda: elapsed, sleep=advance)
        with patch.object(dk, 'time', clock), patch.object(datasheet_capture, 'run_owned_child', child):
            doc = capture_digikey([q('a', 'AE-A'), q('b', 'AE-B'), q('c', 'AE-SLOW'), q('d', 'AE-ECHO')],
                                  self.root / 'cap', allow_network=True, credentials=CREDENTIALS,
                                  base='http://127.0.0.1:1', min_interval=0.5, timeout=0.8)
        self.assertEqual([(r['status'], r['http_status'], r['reason']) for r in doc['requests'][:2]],
                         [('found', 200, None), ('found', 200, None)])
        record = doc['requests'][2]
        self.assertEqual((record['status'], record['response'], record['reason']),
                         ('failed', None, 'fetch deadline exceeded'))
        # A response that echoes a credential is recorded as failed and its bytes are not retained.
        record = doc['requests'][3]
        self.assertEqual((record['status'], record['http_status'], record['response'], record['reason']),
                         ('failed', 200, None, 'response echoes a credential; bytes not retained'))
        self.assertFalse((self.root / 'cap/responses/d.json').exists())
        # A 2 s token is renewed before it lapses; each renewal is recorded without the token.
        self.assertGreaterEqual(len(doc['token_exchanges']), 2)
        self.assertEqual(requests.count('/v1/oauth2/token'), len(doc['token_exchanges']))
        self.assertEqual(requests, [dk.TOKEN_PATH,
                                   dk.PRODUCT_PATH.format(query='AE-A'),
                                   dk.PRODUCT_PATH.format(query='AE-B'), dk.TOKEN_PATH,
                                   dk.PRODUCT_PATH.format(query='AE-SLOW'), dk.TOKEN_PATH,
                                   dk.PRODUCT_PATH.format(query='AE-ECHO')])
        info = validate_capture(doc, lambda rel: (self.root / 'cap' / rel).read_bytes(), allow_failed=True)
        self.assertEqual(info['response_verdicts']['c'], {'path': None, 'sha256': None, 'verdict': 'missing'})

    def test_parser_refuses_unknown_shapes(self):
        good = product('AE-P1')
        self.assertEqual(parse_response(details(good), 'product', 'ae-p1-ct-nd')[1]['digikey_part_number'],
                         'AE-P1-CT-ND')
        self.assertEqual(parse_response(keyword(product('AE-P10')), 'keyword', 'AE-P1'), ('not_found', None))
        self.assertEqual(parse_response(keyword(good, exact=[good]), 'keyword', 'AE-P1')[0], 'found')
        conflicting = product('AE-P1', parameters=[
            {'ParameterText': 'Height - Seated (Max)', 'ValueText': '1.0mm'},
            {'ParameterText': 'Height - Seated (Max)', 'ValueText': '2.0mm'}])
        bad = {'other product': details(product('AE-P2')), 'html': b'<html></html>',
               'duplicate key': b'{"Product":{},"Product":{}}',
               'nan': details(good).replace(b'1.25', b'NaN'),
               'negative stock': details(product('AE-P1', quantity=-1)),
               'bool stock': details(product('AE-P1', quantity=True)),
               'string stock': details(product('AE-P1', quantity='0')),
               'float stock': details(product('AE-P1', quantity=1.0)),
               'object stock': details(product('AE-P1', quantity={})),
               'price': details(product('AE-P1', price='1.25')),
               'datasheet scheme': details(product('AE-P1', datasheet='ftp://example.invalid/x.pdf')),
               'conflicting height': details(conflicting), 'no parameters': details({**good, 'Parameters': None}),
               'no manufacturer': details({**good, 'Manufacturer': None}), 'no product': b'{"Products":[]}'}
        for label, raw in bad.items():
            with self.subTest(label), self.assertRaises(ValueError):
                parse_response(raw, 'product', 'AE-P1')
        with self.assertRaisesRegex(ValueError, 'distinct products'):
            parse_response(keyword(good, product('AE-P1', manufacturer='Other Co.')), 'keyword', 'AE-P1')
        with self.assertRaises(ValueError):
            parse_response(b'{"ExactMatches":[]}', 'keyword', 'AE-P1')

    def test_validator_rederives_and_refuses_tampering(self):
        server = self.stub({'AE-A': (200, details(product('AE-A'))), 'AE-N': (200, keyword())})
        items = [q('a', 'AE-A'), q('n', 'AE-N', 'keyword')]
        doc = self.capture(server, items)
        read = lambda rel: (self.root / 'cap' / rel).read_bytes()  # noqa: E731
        info = validate_capture(doc, read, expected=items)
        self.assertEqual((info['found'], info['not_found'], info['failed']), (['a'], ['n'], {}))

        def tampered(change):
            copy = json.loads(json.dumps(doc))
            change(copy)
            return copy
        cases = {f'forged {field}': (lambda field: lambda d: d['parts']['a'].update(
                     {field: (7 if isinstance(d['parts']['a'][field], (int, float)) else 'forged')}))(field)
                 for field in ('mpn', 'manufacturer', 'digikey_part_number', 'package', 'package_source',
                               'primary_datasheet_url',
                               'quantity_available', 'unit_price', 'seated_height', 'operating_temperature',
                               'last_seen', 'query')}
        cases.update({
            'forged parameter': lambda d: d['parts']['a']['parameters'][3].update(value='36'),
            'float quantity': lambda d: d['parts']['a'].update(quantity_available=120.0),
            'dropped part': lambda d: d['parts'].pop('a'),
            'unrequested part': lambda d: d['parts'].update(z=d['parts']['a']),
            'status': lambda d: d['requests'][1].update(status='found'),
            'failed without reason': lambda d: d['requests'][0].update(status='failed'),
            'hash': lambda d: d['requests'][0]['response'].update(sha256='0' * 64),
            'size': lambda d: d['requests'][0]['response'].update(size_bytes=True),
            'path': lambda d: d['requests'][0]['response'].update(path='../a.json'),
            'unknown request key': lambda d: d['requests'][0].update(token=TOKEN),
            'unknown source key': lambda d: d['source'].update(client_id=CLIENT_ID),
            'unknown token key': lambda d: d['token_exchanges'][0].update(access_token=TOKEN),
            'token status': lambda d: d['token_exchanges'][0].update(http_status=401),
            'no token exchange': lambda d: d.update(token_exchanges=[]),
            'parser': lambda d: d['source'].update(parser='other/1'),
            'interval': lambda d: d['source'].update(min_interval_seconds=0.1),
            'plain http base': lambda d: d['source'].update(base_url='http://api.example.invalid'),
            'fetched before as_of': lambda d: d['requests'][0].update(fetched_at='2000-01-01T00:00:00+00:00'),
            'naive time': lambda d: d.update(as_of=d['as_of'].replace('+00:00', '')),
            'kind': lambda d: d['requests'][1].update(kind='product'),
            'extra key': lambda d: d.update(stale_before=None),
        })
        for label, change in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                validate_capture(tampered(change), read)
        with self.assertRaisesRegex(ValueError, 'expected queries'):
            validate_capture(doc, read, expected=[items[0]])
        with self.assertRaisesRegex(ValueError, 'expected queries'):
            validate_capture(doc, read, expected=[*items, q('x')])
        manifest = self.root / 'cap' / dk.MANIFEST
        original = manifest.read_bytes()
        manifest.write_bytes(original.replace(b'{"as_of"', b'{"as_of":"x","as_of"', 1))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            load_capture(self.root, 'cap/' + dk.MANIFEST)
        manifest.write_bytes(original)
        response = self.root / 'cap/responses/a.json'
        response.write_bytes(details(product('AE-A', quantity=99999)))
        with self.assertRaisesRegex(ValueError, 'size/sha256'):
            load_capture(self.root, 'cap/' + dk.MANIFEST)
        response.unlink()
        response.symlink_to(self.root / 'cap/responses/n.json')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            load_capture(self.root, 'cap/' + dk.MANIFEST)

    def test_projection_feeds_datasheet_capture_and_external_part_record(self):
        from agent_ee.datasheet_capture import capture_datasheets
        from agent_ee.part_record import prepare_external_record, qualify_external_record
        from agent_ee.storage import digest, write_json
        server = self.stub({})
        server.routes['AE-HDR-35'] = (200, details(product('AE-HDR-35', datasheet=server.url + '/ds/hdr.pdf')))
        server.routes['AE-NOPKG'] = (200, details(product('AE-NOPKG', parameters=[])))
        self.capture(server, [q('hdr', 'AE-HDR-35'), q('nopkg', 'AE-NOPKG')])
        path = 'cap/' + dk.MANIFEST
        projection = project_external(self.root, path, 'hdr')
        self.assertEqual(canonical(projection), canonical(project_external(self.root, path, 'hdr')))
        identity = projection['identity']
        self.assertEqual({k: identity[k] for k in ('schema', 'mpn', 'manufacturer', 'package')}, {
            'schema': 'agent-ee.external-part-identity/1', 'mpn': 'AE-HDR-35',
            'manufacturer': 'Example Connector Co.', 'package': 'Synthetic 35-pin header'})
        self.assertEqual(projection['ratings'], {'seated_height': '12.30mm',
                                                 'operating_temperature': '-40°C ~ 125°C'})
        self.assertEqual(projection['datasheet_provenance']['response']['path'], 'cap/responses/hdr.json')
        self.assertIn('DigiKey AE-HDR-35-CT-ND', projection['hand_fit_evidence']['purchase_source'])
        with self.assertRaisesRegex(ValueError, 'no found DigiKey record'):
            project_external(self.root, path, 'absent')
        with self.assertRaisesRegex(ValueError, 'needs a package'):
            project_external(self.root, path, 'nopkg')
        # The datasheet binding is consumed unchanged by datasheet acquisition.
        acquisition = capture_datasheets([projection['datasheet_binding']], self.root / 'datasheets')
        self.assertEqual(acquisition['outcome'], 'captured')
        write_json(self.root / 'external-identity.json', identity)
        deviations = {'schema': 'agent-ee.part-deviations/1', 'deviations': [{
            'requirement_id': 'SYN-HEADER', 'original': {'mpn': 'AE-HDR-35', 'lcsc': None},
            'assembly': 'hand_soldered', 'reason': 'Owned synthetic hand-fit decision.',
            'stock_deviation': {'reason': 'No JLC assembly supply declared.'},
            **projection['hand_fit_evidence']}]}
        write_json(self.root / 'deviations.json', deviations)

        def blob(rel, **extra):
            return {'path': rel, 'sha256': digest(self.root / rel), **extra}
        record = prepare_external_record(self.root, blob('external-identity.json', locator='/'),
                                         blob('datasheets/manifest.json'), 'hdr', blob('deviations.json', index=0))
        self.assertEqual((record['mpn'], record['lcsc'], record['stock'], record['datasheet_url']),
                         ('AE-HDR-35', 'NO_LCSC', None, server.url + '/ds/hdr.pdf'))
        dependencies = qualify_external_record(record, self.root)
        self.assertIn(path, dependencies)
        self.assertIn('cap/responses/hdr.json', dependencies)
        self.assertEqual(verify_external_identity(self.root, identity), projection['dependencies'])
        for field in ('mpn', 'manufacturer', 'package'):
            with self.subTest(field), self.assertRaisesRegex(ValueError, 'differs from the DigiKey capture'):
                verify_external_identity(self.root, {**identity, field: 'forged'})

    def test_live_shape_zero_stock_omits_quantity_available(self):
        # AGENTEE-262: NEO-M9N-00B shape. Omitted (or null) top-level stock is
        # recorded as unknown (null), never zero, and the capture still validates.
        server = self.stub({'NEO-M9N-00B': (200, details(zero_stock_module())),
                            'SYN-NULL-STOCK': (200, details({**zero_stock_module(quantity=None),
                                                             'ManufacturerProductNumber': 'SYN-NULL-STOCK'}))})
        items = [q('neo', 'NEO-M9N-00B'), q('null', 'SYN-NULL-STOCK')]
        doc = self.capture(server, items)
        self.assertEqual({r['key']: (r['status'], r['reason']) for r in doc['requests']},
                         {'neo': ('found', None), 'null': ('found', None)})
        part = doc['parts']['neo']
        self.assertNotIn(b'"QuantityAvailable"', (self.root / 'cap/responses/neo.json').read_bytes())
        self.assertIsNone(part['quantity_available'])
        self.assertIsNone(doc['parts']['null']['quantity_available'])
        self.assertEqual((part['mpn'], part['manufacturer'], part['package'], part['package_source'],
                          part['product_status'], part['digikey_part_number'], part['primary_datasheet_url']),
                         ('NEO-M9N-00B', 'u-blox', '24-SMD Module',
                          {'rule': 'package_case', 'parameters': ['Package / Case']}, 'Active',
                          'SYN-NEO-M9N-00BCT-ND', 'https://example.invalid/neo-m9n.pdf'))
        path = 'cap/' + dk.MANIFEST
        info = load_capture(self.root, path, expected=items)[1]  # no allow_failed: the capture is consumable
        self.assertEqual((info['found'], info['failed']), (['neo', 'null'], {}))
        read = lambda rel: (self.root / 'cap' / rel).read_bytes()  # noqa: E731
        for forged in (0, 1, 0.0, False):  # unknown stock never becomes a number
            copy = json.loads(json.dumps(doc))
            copy['parts']['neo']['quantity_available'] = forged
            with self.subTest(forged=forged), self.assertRaisesRegex(ValueError, 're-derived'):
                validate_capture(copy, read)
        projection = project_external(self.root, path, 'neo')
        self.assertEqual(projection['identity']['package'], '24-SMD Module')
        self.assertIn('quantity_available=unknown (QuantityAvailable not reported)',
                      projection['hand_fit_evidence']['purchase_source'])
        self.assertEqual(verify_external_identity(self.root, projection['identity']), projection['dependencies'])
        # The keyword parser shares the derivation.
        self.assertIsNone(parse_response(keyword(zero_stock_module()), 'keyword', 'NEO-M9N-00B')[1]
                          ['quantity_available'])
        # A present top-level value is still read exactly, including a real zero.
        self.assertEqual(parse_response(details(zero_stock_module(quantity=0)), 'product', 'NEO-M9N-00B')[1]
                         ['quantity_available'], 0)

    def test_live_shape_connector_package_from_explicit_parameters(self):
        # AGENTEE-262: 776228-1 shape. No 'Package / Case'; the package is the
        # verbatim connector parameters with provenance, else unknown.
        from agent_ee.datasheet_capture import capture_datasheets
        from agent_ee.part_record import prepare_external_record, qualify_external_record
        from agent_ee.storage import digest, write_json
        server = self.stub({})
        missing_pitch = [p for p in CONNECTOR_PARAMETERS if p['ParameterText'] != 'Pitch - Mating']
        no_rows = [p for p in CONNECTOR_PARAMETERS if p['ParameterText'] != 'Number of Rows']
        conflicting = [*CONNECTOR_PARAMETERS, {'ParameterText': 'Number of Positions', 'ValueText': '24'}]
        supplier = [{'ParameterText': 'Supplier Device Package', 'ValueText': 'SYN-24-MOD'}, *CONNECTOR_PARAMETERS]
        both = [{'ParameterText': 'Package / Case', 'ValueText': 'Synthetic Case'},
                {'ParameterText': 'Supplier Device Package', 'ValueText': 'SYN-24-MOD'},
                {'ParameterText': 'Supplier Device Package', 'ValueText': 'SYN-OTHER'}]
        server.routes.update({
            '776228-1': (200, details({**connector(), 'DatasheetUrl': server.url + '/ds/776228.pdf'})),
            'SYN-NOPITCH': (200, details({**connector(missing_pitch), 'ManufacturerProductNumber': 'SYN-NOPITCH'})),
            'SYN-NOROWS': (200, details({**connector(no_rows), 'ManufacturerProductNumber': 'SYN-NOROWS'})),
            'SYN-CONFLICT': (200, details({**connector(conflicting), 'ManufacturerProductNumber': 'SYN-CONFLICT'})),
            'SYN-SDP': (200, details({**connector(supplier), 'ManufacturerProductNumber': 'SYN-SDP'})),
            'SYN-BOTH': (200, details({**connector(both), 'ManufacturerProductNumber': 'SYN-BOTH'}))})
        items = [q('amp', '776228-1'), q('nopitch', 'SYN-NOPITCH'), q('norows', 'SYN-NOROWS'),
                 q('conflict', 'SYN-CONFLICT'), q('sdp', 'SYN-SDP'), q('both', 'SYN-BOTH')]
        doc = self.capture(server, items)
        self.assertEqual({r['key']: r['status'] for r in doc['requests']}, {k['key']: 'found' for k in items})
        parts = doc['parts']
        self.assertEqual((parts['amp']['package'], parts['amp']['package_source']), (CONNECTOR_PACKAGE, {
            'rule': 'connector_parameters', 'parameters': ['Connector Type', 'Number of Positions',
                                                           'Number of Rows', 'Pitch - Mating', 'Mounting Type']}))
        self.assertEqual(parts['norows']['package_source']['parameters'],
                         ['Connector Type', 'Number of Positions', 'Pitch - Mating', 'Mounting Type'])
        self.assertEqual(parts['norows']['package'], CONNECTOR_PACKAGE.replace('Number of Rows: 3; ', ''))
        # Insufficient or conflicting connector parameters: unknown, never a guess.
        for key in ('nopitch', 'conflict'):
            self.assertEqual((parts[key]['package'], parts[key]['package_source']), (None, None), key)
        self.assertEqual((parts['sdp']['package'], parts['sdp']['package_source']),
                         ('SYN-24-MOD', {'rule': 'supplier_device_package', 'parameters': ['Supplier Device Package']}))
        # 'Package / Case' wins, and a later rule's conflict cannot refuse it.
        self.assertEqual(parts['both']['package'], 'Synthetic Case')
        path = 'cap/' + dk.MANIFEST
        load_capture(self.root, path, expected=items)
        for key in ('nopitch', 'conflict'):
            with self.subTest(key), self.assertRaisesRegex(ValueError, 'package unknown.*needs a package'):
                project_external(self.root, path, key)
        read = lambda rel: (self.root / 'cap' / rel).read_bytes()  # noqa: E731
        for label, change in {'guessed package': {'package': 'Header 23 pos'},
                              'dropped provenance': {'package_source': None},
                              'other rule': {'package_source': {'rule': 'package_case',
                                                                'parameters': ['Package / Case']}}}.items():
            copy = json.loads(json.dumps(doc))
            copy['parts']['nopitch' if label == 'guessed package' else 'amp'].update(change)
            with self.subTest(label), self.assertRaisesRegex(ValueError, 're-derived'):
                validate_capture(copy, read)
        projection = project_external(self.root, path, 'amp')
        self.assertEqual(canonical(projection), canonical(project_external(self.root, path, 'amp')))
        self.assertEqual({k: projection['identity'][k] for k in ('schema', 'mpn', 'manufacturer', 'package')}, {
            'schema': 'agent-ee.external-part-identity/1', 'mpn': '776228-1',
            'manufacturer': 'TE Connectivity AMP Connectors', 'package': CONNECTOR_PACKAGE})
        self.assertEqual(projection['package_provenance'], {
            'capture': {'path': path, 'sha256': projection['dependencies'][path]},
            'response': {'path': 'cap/responses/amp.json',
                         'sha256': hashlib.sha256((self.root / 'cap/responses/amp.json').read_bytes()).hexdigest()},
            'key': 'amp', 'locator': 'Product.Parameters', **parts['amp']['package_source']})
        # The connector identity is consumed unchanged by the external part record.
        capture_datasheets([projection['datasheet_binding']], self.root / 'datasheets')
        write_json(self.root / 'external-identity.json', projection['identity'])
        write_json(self.root / 'deviations.json', {'schema': 'agent-ee.part-deviations/1', 'deviations': [{
            'requirement_id': 'SYN-CONNECTOR', 'original': {'mpn': '776228-1', 'lcsc': None},
            'assembly': 'hand_soldered', 'reason': 'Owned synthetic hand-fit decision.',
            'stock_deviation': {'reason': 'No JLC assembly supply declared.'}, **projection['hand_fit_evidence']}]})

        def blob(rel, **extra):
            return {'path': rel, 'sha256': digest(self.root / rel), **extra}
        record = prepare_external_record(self.root, blob('external-identity.json', locator='/'),
                                         blob('datasheets/manifest.json'), 'amp', blob('deviations.json', index=0))
        self.assertEqual((record['package'], record['stock']), (CONNECTOR_PACKAGE, None))
        self.assertIn('cap/responses/amp.json', qualify_external_record(record, self.root))
        verify_external_identity(self.root, projection['identity'])
        # A 'Package / Case' conflict still refuses the response, as before.
        clash = connector([{'ParameterText': 'Package / Case', 'ValueText': 'A'},
                           {'ParameterText': 'Package / Case', 'ValueText': 'B'}])
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            parse_response(details(clash), 'product', '776228-1')

    def test_no_check_or_offline_module_imports_the_producer(self):
        # AGENTEE-248: the host-side datasheet cascade producer replays captures
        # through load_capture; it is itself guarded against check/offline importers
        # (test_datasheet_cascade), so no check reaches this module transitively.
        root = Path(dk.__file__).parent
        importers = [p.name for p in root.glob('*.py') if p.name not in ('digikey_capture.py', 'datasheet_cascade.py')
                     and 'digikey_capture' in p.read_text(encoding='utf-8')]
        self.assertEqual(importers, [])


if __name__ == '__main__':
    unittest.main()

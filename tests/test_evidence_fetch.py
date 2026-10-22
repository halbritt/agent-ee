"""Outer fetch tool tests; no sandbox network or provider calls."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('evidence_fetch', Path(__file__).resolve().parents[1] / 'scripts/evidence_fetch.py')
fetcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetcher)


class EvidenceFetchTests(unittest.TestCase):
    def test_complete_group_is_published(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / 'store'
            (store / 'demo/sub').mkdir(parents=True)
            (store / 'demo/sub/raw.bin').write_bytes(b'\x00record\xff')
            manifest = fetcher.inventory(store)
            output = root / 'output'
            output.mkdir()
            fetcher.fetch(manifest, 'demo', store.as_uri(), output)
            self.assertEqual((output / 'demo/sub/raw.bin').read_bytes(), b'\x00record\xff')
            self.assertEqual(fetcher.inventory(output), manifest)

    def test_bad_download_never_publishes_partial_group(self):
        for payload in (b'wrong', b'reco', b'record extra', None):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                store = root / 'store'
                (store / 'demo').mkdir(parents=True)
                (store / 'demo/a').write_bytes(b'first')
                (store / 'demo/z').write_bytes(b'record')
                manifest = fetcher.inventory(store)
                if payload is None:
                    (store / 'demo/z').unlink()
                else:
                    (store / 'demo/z').write_bytes(payload)
                output = root / 'output'
                output.mkdir()
                with self.assertRaises((ValueError, OSError)):
                    fetcher.fetch(manifest, 'demo', store.as_uri(), output)
                self.assertEqual(list(output.iterdir()), [])

    def test_unsafe_manifest_is_rejected_before_writing(self):
        for name in ('../escape', '/absolute', 'a/../../escape', 'a//b', './a', 'a/./b', 'a\\b'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                manifest = {'schema': fetcher.SCHEMA, 'groups': {'demo': {'files': {
                    name: {'bytes': 0, 'sha256': hashlib.sha256(b'').hexdigest()}}}}}
                with self.assertRaises(ValueError):
                    fetcher.fetch(manifest, 'demo', 'file:///unused', Path(directory))
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_links_and_existing_destinations_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'store/demo').mkdir(parents=True)
            (root / 'store/demo/raw').write_bytes(b'record')
            manifest = fetcher.inventory(root / 'store')
            (root / 'output').mkdir()
            (root / 'alias').symlink_to(root / 'output', target_is_directory=True)
            with self.assertRaises(OSError):
                fetcher.fetch(manifest, 'demo', (root / 'store').as_uri(), root / 'alias')
            for kind in ('directory', 'file', 'symlink'):
                target = root / 'output/demo'
                if kind == 'directory':
                    target.mkdir()
                elif kind == 'file':
                    target.write_bytes(b'preserve')
                else:
                    target.symlink_to(root / 'missing')
                with self.subTest(kind=kind), self.assertRaises(FileExistsError):
                    fetcher.fetch(manifest, 'demo', (root / 'store').as_uri(), root / 'output')
                if kind == 'directory':
                    self.assertEqual(list(target.iterdir()), [])
                    target.rmdir()
                else:
                    if kind == 'file':
                        self.assertEqual(target.read_bytes(), b'preserve')
                    target.unlink()

    def test_inventory_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'demo').mkdir()
            (root / 'demo/link').symlink_to('/nonexistent')
            with self.assertRaises(ValueError):
                fetcher.inventory(root)

    def test_http_interrupt_and_racing_destination(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'store/demo').mkdir(parents=True)
            (root / 'store/demo/raw').write_bytes(b'record')
            manifest = fetcher.inventory(root / 'store')
            (root / 'output').mkdir()
            mode = ['valid']

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header('Content-Length', '6')
                    self.end_headers()
                    if mode[0] == 'race':
                        (root / 'output/demo').mkdir()
                    self.wfile.write(b'rec' if mode[0] == 'partial' else b'record')
                    self.close_connection = True

                def log_message(self, *args):
                    pass

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                url = f'http://127.0.0.1:{server.server_port}'
                mode[0] = 'partial'
                with self.assertRaises(ValueError):
                    fetcher.fetch(manifest, 'demo', url, root / 'output')
                self.assertEqual(list((root / 'output').iterdir()), [])
                mode[0] = 'race'
                with self.assertRaises(FileExistsError):
                    fetcher.fetch(manifest, 'demo', url, root / 'output')
                self.assertEqual(list((root / 'output/demo').iterdir()), [])
                (root / 'output/demo').rmdir()
                mode[0] = 'valid'
                fetcher.fetch(manifest, 'demo', url, root / 'output')
                self.assertEqual((root / 'output/demo/raw').read_bytes(), b'record')
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_manifest_conflicts_and_invalid_records(self):
        good = {'bytes': 0, 'sha256': hashlib.sha256(b'').hexdigest()}
        for files in ({'a': good, 'a/b': good}, {'a': {'bytes': -1, 'sha256': '0' * 64}},
                      {'a': {'bytes': True, 'sha256': '0' * 64}},
                      {'a': {'bytes': 0, 'sha256': 'bad'}}, {}):
            with self.subTest(files=files), self.assertRaises(ValueError):
                fetcher.group_files({'schema': fetcher.SCHEMA, 'groups': {'demo': {'files': files}}}, 'demo')
        with self.assertRaises(ValueError):
            json.loads('{"groups": {}, "groups": {}}', object_pairs_hook=fetcher.unique_object)

    def test_inventory_has_exact_known_size_hash_and_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'demo/sub').mkdir(parents=True)
            (root / 'demo/sub/a #%.bin').write_bytes(b'abc')
            (root / 'SUMMARY.md').write_text('keep this summary')
            expected = {'schema': fetcher.SCHEMA, 'groups': {'demo': {'files': {'sub/a #%.bin': {
                'bytes': 3, 'sha256': 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'}}}}}
            self.assertEqual(fetcher.inventory(root), expected)
            (root / 'output').mkdir()
            fetcher.fetch(expected, 'demo', root.as_uri(), root / 'output')
            self.assertEqual((root / 'output/demo/sub/a #%.bin').read_bytes(), b'abc')

    def test_manifest_generator_refuses_overwrite(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'evidence/demo').mkdir(parents=True)
            (root / 'evidence/demo/raw').write_bytes(b'abc')
            output = root / 'manifest.json'
            command = [sys.executable, str(Path(fetcher.__file__)), 'manifest',
                       '--evidence', str(root / 'evidence'), '--out', str(output)]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            original = output.read_bytes()
            (root / 'evidence/demo/raw').write_bytes(b'new')
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn('FileExistsError', second.stderr)
            self.assertEqual(output.read_bytes(), original)


class FakeS3:
    """Path-style S3 stand-in that, like Garage, rejects a body whose SHA-256 differs from the signed one."""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        self.objects, self.requests = {}, []
        store = self

        class Handler(BaseHTTPRequestHandler):
            def _key(self):
                store.requests.append((self.command, self.path, self.headers.get('Authorization', '')))
                if not self.headers.get('Authorization', '').startswith('AWS4-HMAC-SHA256 Credential=GKTEST/'):
                    self.send_response(403)
                    self.end_headers()
                    return None
                return self.path

            def do_HEAD(self):
                if (key := self._key()) is not None:
                    self.send_response(200 if key in store.objects else 404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()

            def do_GET(self):
                if (key := self._key()) is None:
                    return
                if key not in store.objects:
                    self.send_response(404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(store.objects[key])))
                self.end_headers()
                self.wfile.write(store.objects[key])

            def do_PUT(self):
                if (key := self._key()) is None:
                    return
                body = self.rfile.read(int(self.headers['Content-Length']))
                ok = hashlib.sha256(body).hexdigest() == self.headers['x-amz-content-sha256']
                if ok:
                    store.objects[key] = body
                self.send_response(200 if ok else 400)
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


def credentials(directory, endpoint, mode=0o600, **changed):
    import os
    values = {'AWS_ACCESS_KEY_ID': 'GKTEST', 'AWS_SECRET_ACCESS_KEY': 'not-a-real-secret',
              'AWS_DEFAULT_REGION': 'garage', 'AGENT_EE_EVIDENCE_S3_ENDPOINT': endpoint, **changed}
    path = Path(directory) / 'evidence-s3.env'
    path.write_text('# test credentials\n' + ''.join(f'{k}={v}\n' for k, v in values.items() if v is not None))
    os.chmod(path, mode)
    return path


class S3StoreTests(unittest.TestCase):
    def test_signature_matches_independent_reference(self):
        # Expected values were produced by botocore's S3SigV4Auth for the same requests.
        import datetime
        store = fetcher.S3Store('s3://bucket-x/pre', {
            'AWS_ACCESS_KEY_ID': 'GKEXAMPLE', 'AWS_SECRET_ACCESS_KEY': 'secret-example',
            'AWS_DEFAULT_REGION': 'garage', 'AGENT_EE_EVIDENCE_S3_ENDPOINT': 'http://127.0.0.1:3900'})
        now = datetime.datetime(2026, 10, 22, 12, 0, 0, tzinfo=datetime.timezone.utc)
        get = store.request('GET', store.object_key('grp', 'a dir/b #%~.json'), now=now)
        self.assertEqual(get.full_url, 'http://127.0.0.1:3900/bucket-x/pre/grp/a%20dir/b%20%23%25~.json')
        put = store.request('PUT', store.object_key('grp', 'x.bin'), hashlib.sha256(b'abc').hexdigest(),
                            b'abc', 3, now=now)
        for request, signature in ((get, 'bf4d08a5ebf7dc5d245d54cb0adfbfaa2286e61b0ed10835b3b7077353a1b717'),
                                   (put, '76e029b8c850c020daebb6fcdcc6dcb503e7cc67678384c8533fbb89b33fc456')):
            with self.subTest(method=request.get_method()):
                self.assertEqual(request.get_header('Authorization'),
                                 'AWS4-HMAC-SHA256 Credential=GKEXAMPLE/20261022/garage/s3/aws4_request, '
                                 'SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature=' + signature)
        self.assertNotIn('secret-example', repr(store))

    def test_absent_or_unsafe_credentials_are_a_typed_refusal(self):
        import os
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(fetcher.StoreUnavailable, 'credentials not found'):
                fetcher.open_store('s3://bucket', root / 'absent.env')
            for mode, changed, message in ((0o644, {}, 'mode 0600'),
                                           (0o600, {'AWS_SECRET_ACCESS_KEY': None}, 'lack AWS_SECRET_ACCESS_KEY'),
                                           (0o600, {'AGENT_EE_EVIDENCE_S3_ENDPOINT': 'http://h:1/path'}, 'origin')):
                path = credentials(root, 'http://127.0.0.1:9', mode, **changed)
                with self.subTest(message=message), self.assertRaisesRegex(fetcher.StoreUnavailable, message):
                    fetcher.open_store('s3://bucket', path)
                path.unlink()
            (root / 'out').mkdir()
            (root / 'manifest.json').write_text(json.dumps({'schema': fetcher.SCHEMA, 'groups': {}}))
            environment = dict(os.environ, AGENT_EE_EVIDENCE_CREDENTIALS=str(root / 'absent.env'))
            result = subprocess.run([sys.executable, str(Path(fetcher.__file__)), 'fetch', 'demo',
                                     '--manifest', str(root / 'manifest.json'), '--base-url', 's3://bucket',
                                     '--destination', str(root / 'out')],
                                    capture_output=True, text=True, env=environment)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn('refused (store unavailable)', result.stderr)
            self.assertIn(str(root / 'absent.env'), result.stderr)
            self.assertEqual(list((root / 'out').iterdir()), [])
            with self.assertRaises(fetcher.StoreUnavailable):
                fetcher.fetch({'schema': fetcher.SCHEMA, 'groups': {'demo': {'files': {
                    'a': {'bytes': 0, 'sha256': fetcher.EMPTY_SHA256}}}}}, 'demo', 's3://bucket', root / 'out')

    def test_upload_fetch_round_trip_and_corruption(self):
        fake = FakeS3()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'evidence/demo/sub').mkdir(parents=True)
                (root / 'evidence/demo/sub/a #%.bin').write_bytes(b'\x00record\xff')
                (root / 'evidence/demo/b.json').write_bytes(b'{}')
                manifest = fetcher.inventory(root / 'evidence')
                store = fetcher.open_store('s3://bucket/pre', credentials(root, fake.endpoint))
                self.assertEqual(fetcher.upload(manifest, 'demo', root / 'evidence', store), 2)
                self.assertEqual(sorted(fake.objects), ['/bucket/pre/demo/b.json', '/bucket/pre/demo/sub/a%20%23%25.bin'])
                puts = sum(method == 'PUT' for method, _, _ in fake.requests)
                self.assertEqual(fetcher.upload(manifest, 'demo', root / 'evidence', store), 2)
                self.assertEqual(sum(method == 'PUT' for method, _, _ in fake.requests), puts)
                (root / 'out').mkdir()
                fetcher.fetch(manifest, 'demo', 's3://bucket/pre', root / 'out', store)
                self.assertEqual(fetcher.inventory(root / 'out'), manifest)
                fake.objects['/bucket/pre/demo/b.json'] = b'[]'
                (root / 'out2').mkdir()
                with self.assertRaisesRegex(ValueError, 'size/hash mismatch'):
                    fetcher.fetch(manifest, 'demo', 's3://bucket/pre', root / 'out2', store)
                self.assertEqual(list((root / 'out2').iterdir()), [])
                with self.assertRaisesRegex(ValueError, 'size/hash mismatch'):
                    fetcher.upload(manifest, 'demo', root / 'evidence', store)
                self.assertEqual(fake.objects['/bucket/pre/demo/b.json'], b'[]')
                (root / 'evidence/demo/b.json').write_bytes(b'{]')
                with self.assertRaisesRegex(ValueError, 'local evidence differs from manifest'):
                    fetcher.upload(manifest, 'demo', root / 'evidence', store)
        finally:
            fake.close()

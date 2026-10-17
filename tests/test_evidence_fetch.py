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

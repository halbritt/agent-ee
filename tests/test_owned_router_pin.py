"""Owned-engine manifest drift refuses before any routing process starts."""
import importlib.util
import json
import py_compile
import struct
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_ee import kicad_route_attempt as attempt, routing_adapter
from tests.toolchain import require_kicad_route


class OwnedRouterPinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = require_kicad_route()

    def test_installed_manifest_and_included_engine_qualify(self):
        self.assertEqual(routing_adapter.preflight(self.checkout)['problems'], [])
        self.assertFalse((self.checkout / 'vendor/krt').exists())
        self.assertIn('src/kicad_route/router/rust_router/Cargo.lock', attempt.PINNED_CODE)
        self.assertIn('src/kicad_route/router/UPSTREAM.json', attempt.PINNED_CODE)

    def test_missing_digest_wrong_source_and_replaced_module_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            # Only the runtime is mutable in this probe. Source is the real pin.
            shutil.copytree(self.checkout / '.venv-krt', state / '.venv-krt', symlinks=True)
            original = json.loads((self.checkout / 'manifest.json').read_text())
            self.assertTrue(attempt.verify_state(self.checkout, state))
            for label in ('valid', 'source', 'schema', 'malformed', 'missing_digest', 'outside_venv', 'wrong_module', 'packages', 'module'):
                with self.subTest(label=label):
                    m = json.loads(json.dumps(original))
                    if label == 'schema':
                        m['schema'] = 'kicad-route/state/unknown'
                    if label == 'source':
                        m['krt']['source_sha256'] = '0' * 64
                    if label == 'missing_digest':
                        m['krt']['extension_module']['sha256'] = None
                    if label == 'outside_venv':
                        m['krt']['extension_module']['path'] = '/usr/bin/python3'
                    if label == 'wrong_module':
                        m['krt']['extension_module']['path'] = '.venv-krt/pyvenv.cfg'
                        m['krt']['extension_module']['sha256'] = attempt.digest(state / '.venv-krt/pyvenv.cfg')
                    if label == 'packages':
                        m['krt']['venv']['packages_sha256'] = '0' * 64
                    if label == 'module':
                        (state / m['krt']['extension_module']['path']).write_bytes(b'broken native module')
                    (state / 'manifest.json').write_text('{invalid json' if label == 'malformed' else json.dumps(m))
                    problems = attempt.verify_state(self.checkout, state)
                    if label == 'valid':
                        self.assertEqual(problems, [])
                    else:
                        self.assertTrue(problems, label)

    def test_wrong_commit_and_dirty_owned_source_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for top in ('bin', 'src'):
                shutil.copytree(self.checkout / top, root / top,
                                ignore=shutil.ignore_patterns('__pycache__'))
            (root / '.git').mkdir()
            (root / '.git/HEAD').write_text('0' * 40 + '\n')
            check = routing_adapter.preflight(root, self.checkout)
            self.assertTrue(any('checkout HEAD' in p for p in check['problems']))
            (root / '.git/HEAD').write_text(attempt.PIN + '\n')
            (root / 'src/kicad_route/router/UPSTREAM.json').write_text('{}')
            check = routing_adapter.preflight(root, self.checkout)
            self.assertTrue(any('tool file differs from pin' in p for p in check['problems']))

    def test_ignored_bytecode_cannot_replace_verified_engine_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'tool'
            for top in ('bin', 'src'):
                shutil.copytree(self.checkout / top, root / top,
                                ignore=shutil.ignore_patterns('__pycache__'))
            (root / '.git').mkdir()
            (root / '.git/HEAD').write_text(attempt.PIN + '\n')
            self.assertEqual(routing_adapter.preflight(root, self.checkout)['problems'], [])
            source = root / 'src/kicad_route/engine.py'
            cache = Path(importlib.util.cache_from_source(str(source)))
            cache.parent.mkdir(exist_ok=True)
            malicious = Path(tmp) / 'malicious.py'
            malicious.write_text("raise RuntimeError('UNPINNED BYTECODE EXECUTED')\n")
            py_compile.compile(str(malicious), cfile=str(cache), doraise=True)
            data = bytearray(cache.read_bytes())
            # Match the genuine source timestamp and size: ordinary imports
            # would accept this cache even though its code is unrelated.
            data[8:16] = struct.pack('<II', int(source.stat().st_mtime), source.stat().st_size)
            cache.write_bytes(data)
            self.assertEqual(attempt.verify_code(root), [])
            self.assertEqual(routing_adapter.preflight(root, self.checkout)['problems'], [])

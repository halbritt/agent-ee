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
            # The pin binds its manifest to the sandbox location of a checkout state
            # (/opt/kicad-route), so the copy reuses the qualified runtime as its own state.
            (root / '.venv-krt').symlink_to(self.checkout / '.venv-krt')
            shutil.copyfile(self.checkout / 'manifest.json', root / 'manifest.json')
            self.assertEqual(routing_adapter.preflight(root)['problems'], [])
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
            self.assertEqual(routing_adapter.preflight(root)['problems'], [])

    def test_state_manifest_must_describe_the_sandbox_location(self):
        """b577449 refuses a manifest recorded for another directory (AGENTEE-450).

        The sandbox mounts a checkout state at /opt/kicad-route and a separate state at
        /opt/kicad-route-state; a manifest recorded anywhere else is refused at preflight,
        before the sandboxed router would refuse it."""
        self.assertEqual(json.loads((self.checkout / 'manifest.json').read_text())['state_dir'],
                         attempt.SANDBOX['checkout'])
        # The qualified checkout used as a separate state describes the wrong mount point.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'tool'
            for top in ('bin', 'src'):
                shutil.copytree(self.checkout / top, root / top, ignore=shutil.ignore_patterns('__pycache__'))
            (root / '.git').mkdir()
            (root / '.git/HEAD').write_text(attempt.PIN + '\n')
            problems = routing_adapter.preflight(root, self.checkout)['problems']
            self.assertEqual(problems, [
                "state manifest describes '/opt/kicad-route', not the sandbox state '/opt/kicad-route-state'; "
                "record it with `kicad-route state-manifest` where KICAD_ROUTE_STATE is '/opt/kicad-route-state'"])
        for described in ('/home/elsewhere/kicad-route', None, attempt.SANDBOX['state']):
            with self.subTest(described=described), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp)
                (state / '.venv-krt').symlink_to(self.checkout / '.venv-krt')
                m = json.loads((self.checkout / 'manifest.json').read_text())
                m['state_dir'] = described
                (state / 'manifest.json').write_text(json.dumps(m))
                problems = attempt.verify_state(self.checkout, state)
                self.assertTrue(any('state manifest describes' in p and 'not the sandbox state' in p
                                    for p in problems), problems)
                # The same manifest qualifies where the sandbox would mount it.
                if described == attempt.SANDBOX['state']:
                    self.assertEqual(attempt.verify_state(self.checkout, state, mounted_at=described), [])

    def test_a_staged_router_generation_is_not_the_qualified_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / '.venv-krt').symlink_to(self.checkout / '.venv-krt')
            m = json.loads((self.checkout / 'manifest.json').read_text())
            m['krt']['generation'] = '.router-generations/g1'
            (state / 'manifest.json').write_text(json.dumps(m))
            self.assertIn('state manifest selects a router generation; only the checkout engine is qualified',
                          attempt.verify_state(self.checkout, state))

"""Owned-engine manifest drift and a dirty checkout refuse before any routing process starts."""
import importlib.util
import json
import py_compile
import struct
from pathlib import Path
import tempfile
import unittest

from agent_ee import kicad_route_attempt as attempt, routing_adapter
from tests.test_kicad_route_generations import copy_tool, derive_state
from tests.toolchain import require_kicad_route


class OwnedRouterStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = require_kicad_route()

    def test_installed_manifest_and_included_engine_qualify(self):
        self.assertEqual(routing_adapter.preflight(self.checkout)['problems'], [])
        self.assertFalse((self.checkout / 'vendor/krt').exists())
        # The owned engine's source and lock are part of the code digest an attempt records.
        files, problems = attempt.code_files(self.checkout)
        self.assertEqual(problems, [])
        self.assertIn('src/kicad_route/router/rust_router/Cargo.lock', files)
        self.assertIn('src/kicad_route/router/UPSTREAM.json', files)

    def test_missing_digest_wrong_source_and_replaced_module_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            # Only the runtime is mutable in this probe. Source is the real checkout; the
            # qualified engine is laid out legacy-style whatever the checkout's layout.
            original = derive_state(self.checkout, state, 'legacy', copy_venv=True)
            (state / 'manifest.json').unlink()
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

    def test_unreadable_commit_and_dirty_owned_source_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_tool(self.checkout, Path(tmp) / 'tool')
            self.assertEqual(attempt.checkout_record(root)[1], [])
            (root / 'src/kicad_route/router/UPSTREAM.json').write_text('{}')
            check = routing_adapter.preflight(root, self.checkout)
            self.assertIs(check['clean'], False)
            self.assertTrue(any('uncommitted or untracked changes:  M src/kicad_route/router/UPSTREAM.json' in p
                                for p in check['problems']), check['problems'])
            (root / '.git/HEAD').write_text('0' * 39 + '\n')
            check = routing_adapter.preflight(root, self.checkout)
            self.assertTrue(any(p.startswith("checkout HEAD '" + '0' * 39 + "' is not a commit")
                                for p in check['problems']), check['problems'])

    def test_ignored_bytecode_cannot_replace_verified_engine_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_tool(self.checkout, Path(tmp) / 'tool')
            # kicad-route binds its manifest to the sandbox location of a checkout state
            # (/opt/kicad-route), so the copy reuses the qualified runtime as its own state.
            # The venv is copied: kicad-route ignores .venv-krt only as a directory, and a
            # link there is an untracked change that makes the checkout dirty.
            derive_state(self.checkout, root, 'legacy', copy_venv=True)
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
            # The ignored cache is neither code nor a change to the checkout.
            self.assertEqual(attempt.code_files(root), attempt.code_files(self.checkout))
            self.assertEqual(routing_adapter.preflight(root)['problems'], [])

    def test_state_manifest_qualifies_across_mount_locations_and_refuses_invalid_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_tool(self.checkout, Path(tmp) / 'tool')
            self.assertEqual(routing_adapter.preflight(root, self.checkout)['problems'], [])
        for described in (None, '', '   ', 42):
            with self.subTest(described=described), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp)
                m = derive_state(self.checkout, state, 'legacy')
                m['state_dir'] = described
                (state / 'manifest.json').write_text(json.dumps(m))
                problems = attempt.verify_state(self.checkout, state)
                self.assertTrue(any('invalid state_dir' in p or 'missing state_dir' in p for p in problems), problems)

    def test_a_generation_outside_kicad_routes_selection_rules_is_refused(self):
        # A router generation qualifies only as kicad-route selects it and when built from the
        # checkout's source (tests/test_kicad_route_generations.py); a malformed selection never does.
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            m = derive_state(self.checkout, state, 'legacy')
            m['krt']['generation'] = '.router-generations/g1'
            (state / 'manifest.json').write_text(json.dumps(m))
            self.assertIn("state manifest selects an invalid router generation '.router-generations/g1'",
                          attempt.verify_state(self.checkout, state))

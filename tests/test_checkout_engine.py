"""A checkout's development planner binary must come from that checkout's engine sources."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_ee import placement_job, placement_runtime

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = 'scripts/build_placement_engine.py'
LOCALITY = 'src/agent_ee/data/jlcpcb-locality.json'
_spec = importlib.util.spec_from_file_location('build_placement_engine', REPOSITORY / SCRIPT)
build_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_script)


class CheckoutEngineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in (SCRIPT, LOCALITY):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPOSITORY / name, self.root / name)
        crate = self.root / 'crates/placement-engine'
        (crate / 'src').mkdir(parents=True)
        for name in ('Cargo.toml', 'Cargo.lock', 'src/main.rs'):
            (crate / name).write_text('engine ' + name)
        self.binary = self.root / 'build/placement-engine/agent-ee-placement'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'built')
        self.binary.chmod(0o755)
        self.receipt = self.binary.with_name(self.binary.name + '.json')
        sources = build_script.sources(self.root)
        self.receipt.write_text(json.dumps({
            'schema': build_script.SCHEMA, 'source_sha256': sources,
            'cargo_lock_sha256': sources['crates/placement-engine/Cargo.lock'],
            'binary_sha256': hashlib.sha256(b'built').hexdigest(), 'rust_unit_tests': 'passed',
            'build': {'profile': 'release', 'frozen': True},
            'toolchain': {'rustc': build_script.RUSTC + '\nhost: ' + build_script.TARGET, 'cargo': build_script.CARGO,
                          'target': build_script.TARGET, 'cpu': 'x86-64', 'source_path_remap': '/opt/agent-ee'}}))

    def refused(self, message='does not match this checkout'):
        with self.assertRaisesRegex(placement_runtime.PlanningProcessError, message):
            placement_job._checkout_engine(self.root)

    def test_binary_built_from_these_sources_is_used(self):
        self.assertEqual(placement_job._checkout_engine(self.root), self.binary)

    def test_binary_older_than_an_engine_change_is_refused(self):
        # 2026-11-01: a stale binary reported a design-declared model as feasible with no such code.
        (self.root / 'crates/placement-engine/src/main.rs').write_text('changed')
        self.refused()

    def test_new_engine_source_file_is_refused(self):
        (self.root / 'crates/placement-engine/src/added.rs').write_text('added')
        self.refused()

    def test_other_binary_under_a_matching_receipt_is_refused(self):
        self.binary.write_bytes(b'older build')
        self.refused()

    def test_linked_binary_uses_the_receipt_beside_its_target(self):
        shared = self.root / 'shared'
        self.binary.parent.rename(shared)
        self.binary.parent.mkdir()
        self.binary.symlink_to(shared / self.binary.name)
        self.assertEqual(placement_job._checkout_engine(self.root), shared / self.binary.name)

    def test_unusable_receipts_are_refused_with_rebuild_guidance(self):
        for text in ('{"source_sha256": null}', '[]', 'not json'):
            with self.subTest(text=text):
                self.receipt.write_text(text)
                self.refused('rebuild it')
        self.receipt.unlink()
        self.refused('rebuild it')

    def test_no_binary_says_how_to_build_one(self):
        self.binary.unlink()
        self.refused('build_placement_engine')


if __name__ == '__main__':
    unittest.main()

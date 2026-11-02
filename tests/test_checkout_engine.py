"""A checkout's development planner binary must come from that checkout's engine sources."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_ee import placement_job, placement_runtime

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = 'scripts/build_placement_engine.py'
LOCALITY = 'src/agent_ee/data/jlcpcb-locality.json'


class CheckoutEngineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in (SCRIPT, LOCALITY):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPOSITORY / name, self.root / name)
        crate = self.root / 'crates/placement-engine'
        (crate / 'src').mkdir(parents=True)
        for name in ('Cargo.toml', 'Cargo.lock', 'src/main.rs'):
            (crate / name).write_text('engine ' + name)
        self.binary = self.root / 'build/placement-engine/agent-ee-placement'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'')
        self.binary.chmod(0o755)
        self.receipt = self.binary.with_name(self.binary.name + '.json')
        self.receipt.write_text(json.dumps({'source_sha256': self.sources()}))

    def sources(self):
        paths = [self.root / name for name in (SCRIPT, LOCALITY)]
        paths += [p for p in (self.root / 'crates/placement-engine').rglob('*') if p.is_file()]
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

    def test_binary_built_from_these_sources_is_used(self):
        self.assertEqual(placement_job._checkout_engine(self.root), self.binary.resolve())

    def test_binary_older_than_an_engine_change_is_refused_by_name(self):
        # 2026-11-01: a stale binary reported a design-declared model as feasible with no such code.
        (self.root / 'crates/placement-engine/src/main.rs').write_text('changed')
        with self.assertRaisesRegex(placement_runtime.PlanningProcessError, 'crates/placement-engine/src/main.rs.*rebuild'):
            placement_job._checkout_engine(self.root)

    def test_new_engine_source_file_is_refused(self):
        (self.root / 'crates/placement-engine/src/added.rs').write_text('added')
        with self.assertRaisesRegex(placement_runtime.PlanningProcessError, 'src/added.rs'):
            placement_job._checkout_engine(self.root)

    def test_binary_with_no_receipt_is_refused(self):
        self.receipt.unlink()
        with self.assertRaisesRegex(placement_runtime.PlanningProcessError, 'no usable build receipt'):
            placement_job._checkout_engine(self.root)

    def test_no_binary_says_how_to_build_one(self):
        self.binary.unlink()
        with self.assertRaisesRegex(placement_runtime.PlanningProcessError, 'build_placement_engine'):
            placement_job._checkout_engine(self.root)


if __name__ == '__main__':
    unittest.main()

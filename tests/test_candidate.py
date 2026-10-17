import json
import tempfile
import unittest
from pathlib import Path

from agent_ee.candidate import capture, verify_snapshot, verify_source
from agent_ee.executor import execute

from toolchain import toolchain_test


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "board").write_text("saved board")
        self.scope = self.root / "scope.json"
        self.scope.write_text(json.dumps({"schema": "agent-ee.scope/1",
            "inputs": [{"path": "source", "at": "project"}]}))
        self.run = self.root / "run"
        self.run.mkdir()

    def test_external_input_changes_invalidate_evidence(self):
        external = self.root / "library"
        external.mkdir()
        (external / "pinmap").write_text("pin 1 = IN")
        scope = json.loads(self.scope.read_text())
        scope["inputs"].append({"path": "library", "at": "library"})
        self.scope.write_text(json.dumps(scope))
        first = capture(self.scope, self.run)
        (external / "pinmap").write_text("pin 1 = OUT")
        verify_snapshot(self.run)  # Preserved old candidate remains inspectable.
        with self.assertRaisesRegex(ValueError, "source inputs changed"):
            verify_source(self.run)
        another = self.root / "another"
        another.mkdir()
        second = capture(self.scope, another)
        self.assertNotEqual(first["candidate_id"], second["candidate_id"])

    def test_requirement_and_scope_changes_invalidate(self):
        capture(self.scope, self.run)
        scope = json.loads(self.scope.read_text())
        scope["limits"] = {"volts": 5}
        self.scope.write_text(json.dumps(scope))
        with self.assertRaisesRegex(ValueError, "scope changed"):
            verify_source(self.run)

    def test_added_and_deleted_inputs_invalidate(self):
        capture(self.scope, self.run)
        (self.source / "extra").write_text("new relevant input")
        with self.assertRaisesRegex(ValueError, "source inputs changed"):
            verify_source(self.run)
        (self.source / "extra").unlink()
        (self.source / "board").unlink()
        with self.assertRaises(ValueError):
            verify_source(self.run)

    def test_snapshot_tampering_and_symlink_refused(self):
        capture(self.scope, self.run)
        (self.run / "inputs/project/board").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            verify_snapshot(self.run)
        (self.source / "live").symlink_to(self.scope)
        another = self.root / "another"
        another.mkdir()
        with self.assertRaisesRegex(ValueError, "symlink"):
            capture(self.scope, another)

    @toolchain_test
    def test_os_refuses_writes_and_hides_other_review(self):
        capture(self.scope, self.run)
        record = execute(self.run / "inputs", self.run / "work",
            ["/bin/sh", "-c", "echo changed > /candidate/project/board"], timeout=5)
        self.assertEqual(record["execution"], "failed")
        self.assertIn("Read-only file system", (self.run / "work/console.log").read_text())
        verify_snapshot(self.run)
        record = execute(self.run / "inputs", self.run / "other",
            ["/bin/sh", "-c", f"test ! -e '{self.run}/work/console.log'"], timeout=5)
        self.assertEqual(record["execution"], "ok")

    @toolchain_test
    def test_timeout_and_failed_command_are_distinct(self):
        capture(self.scope, self.run)
        timeout = execute(self.run / "inputs", self.run / "timeout", ["sleep", "2"], timeout=.02)
        failed = execute(self.run / "inputs", self.run / "failed", ["false"], timeout=5)
        missing = execute(self.run / "inputs", self.run / "missing", ["/no-such-tool"], timeout=5)
        self.assertEqual(timeout["execution"], "timeout")
        self.assertEqual(failed["execution"], "failed")
        self.assertNotEqual(missing["execution"], "ok")


if __name__ == "__main__":
    unittest.main()

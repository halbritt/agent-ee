import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import native_run
from agent_ee.candidate import capture
from agent_ee.checks import run_checks
from agent_ee.reviewers import claude_call


class ToolFailureTests(unittest.TestCase):
    def test_malformed_native_report_is_unknown_not_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source").mkdir()
            (root / "source/design.kicad_sch").write_text("(kicad_sch)")
            scope = {"schema": "agent-ee.scope/1", "schematic": "project/design.kicad_sch",
                "inputs": [{"path": "source", "at": "project"}], "checks": [{"id": "erc", "kind": "erc"}]}
            (root / "scope.json").write_text(json.dumps(scope))
            run = root / "run"
            run.mkdir()
            capture(root / "scope.json", run)

            def fake_native(inputs, work, argv, **kwargs):
                work.mkdir(parents=True, exist_ok=True)  # as executor.execute does
                (work / "erc.json").write_text('{"sheets": "not a list"}')
                (work / "console.log").write_text("tool exited successfully\n")
                return {"execution": "ok", "argv": argv, "exit_code": 0, "duration_s": 0,
                        "console_sha256": "unused in parser test"}

            with patch("agent_ee.checks.executor.execute", side_effect=fake_native):
                results = run_checks(run, time.monotonic() + 30)
            self.assertEqual(results[0]["execution"], "malformed")
            self.assertEqual(results[0]["outcome"], "unknown")


class ReviewerFailureTests(unittest.TestCase):
    def invoke(self, text, execution="ok"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "profile"
            (home / ".claude").mkdir(parents=True)
            (home / ".claude/.credentials.json").write_text("test placeholder; not used by fake executor")
            run = root / "run"
            (run / "inputs").mkdir(parents=True)

            def fake_provider(call):
                return native_run.Outcome(execution=execution, exit_code=0 if execution == "ok" else 1,
                                          stdout=text.encode(), stderr=b"harmless harness warning\n")

            with patch("agent_ee.reviewers.Path.home", return_value=home), \
                    patch("agent_ee.reviewers.shutil.which", return_value="/usr/bin/true"), \
                    native_run.using(native_run.Recorded(fake_provider)):
                record, value = claude_call(run, "review", "declared-model", "packet", {}, 10)
            self.assertFalse((run / "review-work/review/home").exists())
            return record, value

    def test_invalid_json_and_incomplete_provider_are_distinct(self):
        malformed, value = self.invoke("not JSON")
        self.assertEqual(malformed["execution"], "malformed")
        self.assertIsNone(value)
        failed, value = self.invoke(json.dumps({"is_error": True, "subtype": "error_max_turns"}))
        self.assertEqual(failed["execution"], "failed")
        self.assertIsNone(value)
        unavailable, value = self.invoke("tool unavailable", execution="unavailable")
        self.assertEqual(unavailable["execution"], "unavailable")
        self.assertIsNone(value)

    def test_stderr_warning_does_not_corrupt_structured_stdout(self):
        record, value = self.invoke(json.dumps({"subtype": "success", "is_error": False,
            "structured_output": {"status": "reviewed"}}))
        self.assertEqual(record["execution"], "ok")
        self.assertEqual(value, {"status": "reviewed"})


if __name__ == "__main__":
    unittest.main()

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "action_gate.py"
spec = importlib.util.spec_from_file_location("action_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def check(cid, execution="ok", outcome="pass", summary="native check summary"):
    return {"id": cid, "kind": "erc" if cid == "erc" else "rc_transient", "candidate_id": "c" * 64,
            "execution": execution, "outcome": outcome, "summary": summary,
            "evidence": [], "details": {}, "limitations": []}


class ActionGateTests(unittest.TestCase):
    """The consumer action fails only on a check defect or incomplete coverage in result.json."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name) / "run"
        self.run_dir.mkdir()
        # Shape of an actual checks-only result: never complete, outcome incomplete, exit 1.
        self.result = {"schema": "agent-ee.result/1", "run_id": "r", "candidate_id": "c" * 64,
            "state": "incomplete", "outcome": "incomplete", "adjudicated": False,
            "started_utc": "2026-10-17T00:00:00+00:00", "finished_utc": "2026-10-17T00:01:00+00:00",
            "expected_check_ids": ["erc", "rc-step"], "checks": [check("erc"), check("rc-step")],
            "reviews": [], "review_executions": [], "findings": [],
            "limitations": ["Checks-only run: independent agent reviews and adjudication not performed."]}

    def write(self):
        (self.run_dir / "result.json").write_text(json.dumps(self.result))

    def gate(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gate.main([str(self.run_dir), *args])
        return code, out.getvalue()

    def test_all_checks_passing_succeeds_despite_checks_only_exit_one(self):
        self.write()
        (self.run_dir / "REPORT.md").write_text("# RC review\n\nOutcome: **incomplete**\n")
        code, text = self.gate("--review-exit", "1", "--verify-exit", "0")
        self.assertEqual(code, 0)
        self.assertEqual(gate.judge(self.run_dir)["verdict"], "checks_passed")
        self.assertIn("checks_passed", text)
        self.assertIn("exited 1", text)
        self.assertIn("# RC review", text)

    def test_check_defect_fails_and_appears_in_summary(self):
        self.result["checks"][1] = check("rc-step", outcome="defect", summary="vout_1ms=0.095 V outside 0.62..0.65")
        self.write()
        code, text = self.gate()
        self.assertEqual(code, 1)
        self.assertEqual(gate.judge(self.run_dir)["verdict"], "check_failed")
        self.assertIn("vout_1ms=0.095 V", text)

    def test_unexecuted_check_is_incomplete_coverage(self):
        for execution in ("failed", "timeout", "unavailable", "malformed", "not_run", "interrupted"):
            self.result["checks"][0] = check("erc", execution=execution, outcome="unknown")
            self.write()
            self.assertEqual(self.gate()[0], 1)
            self.assertEqual(gate.judge(self.run_dir)["verdict"], "coverage_incomplete")
        self.result["checks"][0] = check("erc", outcome="unknown")
        self.write()
        self.assertEqual(gate.judge(self.run_dir)["verdict"], "coverage_incomplete")

    def test_incomplete_coverage_dominates_a_defect(self):
        self.result["checks"] = [check("erc", execution="timeout", outcome="unknown"),
                                 check("rc-step", outcome="defect")]
        self.write()
        judgment = gate.judge(self.run_dir)
        self.assertEqual(judgment["verdict"], "coverage_incomplete")
        self.assertTrue(any("rc-step failed" in r for r in judgment["reasons"]))

    def test_missing_declared_check_or_inventory_is_incomplete(self):
        self.result["checks"] = [check("erc")]
        self.write()
        self.assertEqual(gate.judge(self.run_dir)["verdict"], "coverage_incomplete")
        self.result["checks"] = [check("erc"), check("rc-step")]
        del self.result["expected_check_ids"]
        self.write()
        self.assertEqual(gate.judge(self.run_dir)["verdict"], "coverage_incomplete")

    def test_run_error_interruption_or_unfinalized_run_is_incomplete(self):
        for change in ({"error": "ValueError: declared native/requirement input was not captured"},
                       {"state": "interrupted"}, {"state": "running"}, {"finished_utc": None},
                       {"candidate_id": None}):
            value = dict(self.result, **change)
            (self.run_dir / "result.json").write_text(json.dumps(value))
            self.assertEqual(gate.judge(self.run_dir)["verdict"], "coverage_incomplete", change)
            self.assertEqual(self.gate()[0], 1)

    def test_missing_result_fails(self):
        code, text = self.gate()
        self.assertEqual(code, 1)
        self.assertEqual(gate.judge(self.run_dir)["verdict"], "result_invalid")
        self.assertIn("does not exist", text)

    def test_malformed_result_fails(self):
        for body in ("{not json", "[]", json.dumps({"schema": "other"}),
                     json.dumps(dict(self.result, checks="erc")),
                     json.dumps(dict(self.result, checks=[check("erc"), check("erc")])),
                     json.dumps(dict(self.result, checks=[check("erc", outcome="looks fine"), check("rc-step")])),
                     json.dumps(dict(self.result, checks=[check("erc", execution="failed"), check("rc-step")])),
                     json.dumps(dict(self.result, checks=[check("erc"), check("rc-step"), check("bonus")]))):
            (self.run_dir / "result.json").write_text(body)
            self.assertEqual(gate.judge(self.run_dir)["verdict"], "result_invalid", body)
            self.assertEqual(self.gate()[0], 1)

    def test_failed_verification_invalidates_a_passing_record(self):
        self.write()
        code, text = self.gate("--verify-exit", "2")
        self.assertEqual(code, 1)
        self.assertIn("result_invalid", text)
        self.result["error"] = "ValueError: scope file not found"
        self.write()
        judgment = gate.judge(self.run_dir, verify_exit=2)
        self.assertEqual(judgment["verdict"], "result_invalid")
        self.assertTrue(any("scope file not found" in r for r in judgment["reasons"]))

    def test_absent_run_directory_argument_fails_even_beside_a_result(self):
        self.write()
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.run_dir)
        self.assertEqual(gate.judge(".")["verdict"], "checks_passed")
        self.assertEqual(gate.judge("")["verdict"], "result_invalid")

    def test_expectations_assert_fixture_outcomes(self):
        self.result["checks"][1] = check("rc-step", outcome="defect")
        self.write()
        self.assertEqual(self.gate("--expect", "check_failed", "--expect-check", "rc-step=defect")[0], 0)
        self.assertEqual(self.gate("--expect", "check_failed", "--expect-check", "erc=defect")[0], 1)
        self.assertEqual(self.gate("--expect", "checks_passed")[0], 1)
        self.result["checks"][1] = check("rc-step")
        self.write()
        self.assertEqual(self.gate("--expect", "checks_passed")[0], 0)
        self.assertEqual(self.gate("--expect", "check_failed", "--expect-check", "rc-step=defect")[0], 1)


if __name__ == "__main__":
    unittest.main()

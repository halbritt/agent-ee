import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.cli import outcome, recover_records, run_review, verify
from agent_ee.storage import write_json


class OutcomeTests(unittest.TestCase):
    def result(self):
        return {"state": "complete", "adjudicated": True, "checks": [
            {"id": "functional", "execution": "ok", "outcome": "pass"}],
            "review_executions": [{"execution": "ok"}, {"execution": "ok"}],
            "reviews": [{}, {}], "findings": []}

    def test_unknown_and_failed_are_incomplete(self):
        value = self.result()
        self.assertEqual(outcome(value), "criteria_satisfied")
        value["checks"][0]["outcome"] = "unknown"
        self.assertEqual(outcome(value), "incomplete")
        for execution in ("failed", "malformed", "unavailable", "timeout", "not_run", "interrupted"):
            value["checks"][0].update(execution=execution, outcome="pass")
            self.assertEqual(outcome(value), "incomplete")

    def test_singleton_survives_clean_other_reviews(self):
        value = self.result()
        value["findings"] = [{"id": "only-one", "status": "accepted"}]
        self.assertEqual(outcome(value), "defects_or_unresolved")
        value["findings"][0]["status"] = "deferred"
        self.assertEqual(outcome(value), "defects_or_unresolved")
        value["findings"][0]["status"] = "dismissed"
        self.assertEqual(outcome(value), "criteria_satisfied")

    def test_review_failure_cannot_be_hidden_by_adjudication(self):
        value = self.result()
        value["review_executions"][1]["execution"] = "malformed"
        self.assertEqual(outcome(value), "incomplete")

    def test_dismissal_cannot_override_a_failed_declared_criterion(self):
        value = self.result()
        value["checks"][0]["outcome"] = "defect"
        value["findings"] = [{"id": "check/functional", "status": "dismissed"}]
        self.assertEqual(outcome(value), "defects_or_unresolved")

    def test_all_declared_work_is_required(self):
        value = self.result()
        value["expected_check_ids"] = ["functional", "omitted"]
        self.assertEqual(outcome(value), "incomplete")
        del value["expected_check_ids"]
        value["expected_review_roles"] = ["circuit", "physical", "verification"]
        value["reviews"] = [{"role": "circuit"}, {"role": "physical"}]
        value["review_executions"] = [{"role": "circuit", "execution": "ok"},
                                     {"role": "physical", "execution": "ok"}]
        self.assertEqual(outcome(value), "incomplete")

    def test_supported_nondefect_observation_does_not_invent_a_failing_criterion(self):
        value = self.result()
        value["findings"] = [{"id": "observer/note", "assessment": "concern", "severity": "minor", "status": "noted"}]
        self.assertEqual(outcome(value), "criteria_satisfied")
        value["findings"][0]["status"] = "deferred"
        self.assertEqual(outcome(value), "defects_or_unresolved")


class RecoverRecordsTests(unittest.TestCase):
    def recover(self, record, roles=("circuit",), name="circuit", whole=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "review-executions").mkdir()
            (root / "review-executions" / f"{name}.json").write_text(json.dumps(record))
            scope = {"checks": [], "review_profile": {"reviewers": [{"role": r} for r in roles]}}
            (root / "candidate.json").write_text(json.dumps({"scope": scope}))
            result = {"candidate_id": "synthetic-interrupted-candidate", "limitations": []}
            recover_records(root, result)
            return result if whole else result["review_executions"]

    def test_role_less_execution_record_is_kept_as_interrupted_evidence(self):
        # review-ee14 probe: an empty record used to raise KeyError('role') and abort SIGINT recovery.
        records = self.recover({})
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["role"], "circuit")
        self.assertNotIn("role_reconstructed", records[0])
        self.assertEqual(records[0]["execution"], "interrupted")
        self.assertIn("error", records[0])

    def test_role_less_record_does_not_hide_missing_roles_or_overwrite_status(self):
        records = self.recover({"execution": "timeout"}, roles=("circuit", "physical"))
        by_role = {r["role"]: r for r in records}
        self.assertEqual(by_role["circuit"]["execution"], "timeout")
        self.assertEqual(by_role["physical"]["execution"], "not_run")

    def test_reconstruction_is_recorded_beside_the_execution(self):
        result = self.recover({}, whole=True)
        self.assertEqual(result["recovered_review_roles"], ["circuit"])
        self.assertEqual(result["unexpected_review_executions"], [])

    def test_foreign_stem_is_reported_not_folded_in_as_a_role(self):
        result = self.recover({}, name="unexpected", whole=True)
        self.assertEqual([r["role"] for r in result["review_executions"]], ["circuit"])
        self.assertEqual(result["review_executions"][0]["execution"], "not_run")
        self.assertEqual(result["unexpected_review_executions"][0]["file"], "unexpected.json")
        self.assertEqual(result["recovered_review_roles"], [])

    def test_mismatched_role_cannot_stand_in_for_the_expected_role(self):
        result = self.recover({"role": "physical", "execution": "ok"}, roles=("circuit", "physical"), whole=True)
        by_role = {r["role"]: r for r in result["review_executions"]}
        self.assertEqual(by_role["circuit"]["execution"], "not_run")
        self.assertEqual(by_role["physical"]["execution"], "not_run")
        self.assertEqual(len(result["unexpected_review_executions"]), 1)

    def test_recovered_projection_is_the_archived_record_plus_role_only(self):
        # review_run.verify_role_call compares the record minus 'role' with the archived execution.json;
        # recovery must add nothing else, so an archived interrupted structured_call still verifies.
        raw = {"execution": "interrupted", "attempts": [], "resubmissions_used": 0}
        (record,) = self.recover(dict(raw))
        self.assertEqual({k: v for k, v in record.items() if k != "role"}, raw)
        stored = {"execution": "timeout", "exit_code": 124}
        (record,) = self.recover(dict(stored))
        self.assertEqual({k: v for k, v in record.items() if k != "role"}, stored)

    def test_complete_record_is_unchanged(self):
        record = {"role": "circuit", "execution": "ok"}
        self.assertEqual(self.recover(dict(record)), [record])


class LifecycleTests(unittest.TestCase):
    def test_interruption_preserves_a_completed_defect_and_pending_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "native"
            source.mkdir()
            (source / "design.kicad_sch").write_text("saved schematic")
            (source / "design.kicad_pcb").write_text("saved board")
            config = {"schema": "agent-ee.scope/1", "name": "bounded", "scope": "interrupted lifecycle",
                "inputs": [{"path": "native", "at": "project"}],
                "schematic": "project/design.kicad_sch", "board": "project/design.kicad_pcb",
                "checks": [{"id": "first", "kind": "erc"}, {"id": "pending", "kind": "drc"}]}
            scope = root / "scope.json"
            scope.write_text(json.dumps(config))
            run = root / "run"

            def interrupted(run_dir, deadline):
                write_json(run_dir / "checks/first.json", {"id": "first", "kind": "erc",
                    "execution": "ok", "outcome": "defect", "summary": "completed finding",
                    "evidence": ["inputs/project/design.kicad_sch"], "details": {}, "limitations": []})
                raise KeyboardInterrupt

            with patch("agent_ee.checks.run_checks", side_effect=interrupted), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_review(scope, run, 30), 1)
            result = json.loads((run / "result.json").read_text())
            self.assertEqual(result["state"], "interrupted")
            self.assertEqual(result["outcome"], "incomplete")
            self.assertEqual(result["findings"][0]["id"], "check/first")
            pending = next(c for c in result["checks"] if c["id"] == "pending")
            self.assertEqual(pending["outcome"], "unknown")
            self.assertEqual(pending["execution"], "not_run")

    def test_exhausted_run_preserves_check_and_refuses_output_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "native"
            source.mkdir()
            (source / "design.kicad_sch").write_text("saved schematic")
            (source / "design.kicad_pcb").write_text("saved board")
            config = {"schema": "agent-ee.scope/1", "name": "bounded", "scope": "test lifecycle",
                "inputs": [{"path": "native", "at": "project"}],
                "schematic": "project/design.kicad_sch", "board": "project/design.kicad_pcb",
                "checks": [{"id": "pending", "kind": "erc"}]}
            scope = root / "scope.json"
            scope.write_text(json.dumps(config))
            run = root / "run"
            check = {"id": "pending", "kind": "erc", "execution": "not_run", "outcome": "unknown",
                "summary": "budget exhausted", "evidence": [], "details": {}, "limitations": []}
            with patch("agent_ee.checks.run_checks", return_value=[check]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_review(scope, run, 0), 1)
            result = json.loads((run / "result.json").read_text())
            self.assertEqual(result["outcome"], "incomplete")
            self.assertEqual(result["checks"], [check])
            self.assertFalse(result["adjudicated"])
            self.assertEqual(verify(run)["outcome"], "incomplete")
            with self.assertRaises(FileExistsError):
                run_review(scope, run, 0)


if __name__ == "__main__":
    unittest.main()

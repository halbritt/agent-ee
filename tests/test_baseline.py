import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_ee.baseline import compile_baseline, freeze_evidence, load_baseline, validate_witnesses
from agent_ee.candidate import capture, verify_snapshot
from agent_ee.findings import adjudicate, collect_findings, validate_citation, validate_citation_path
from agent_ee.reviewers import build_packet
from agent_ee.storage import read_json, write_json


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for name in ("board", "schematic", "requirements", "targets", "reference"):
            (self.source / name).write_text(name)
        self.scope = {"schema": "agent-ee.scope/2", "name": "test", "scope": "test",
            "board": "design/board", "schematic": "design/schematic",
            "inputs": [{"path": "source", "at": "design"}],
            "checks": [{"id": "response", "kind": "rc_transient"}],
            "baseline": {"requirements": [{"id": "R1", "statement": "Output meets limits",
                "source": {"path": "design/requirements", "locator": "R1"}, "checks": ["response"]}],
                "targets": ["design/targets"], "reference_architectures": ["design/reference"]}}
        write_json(self.root / "scope.json", self.scope)
        self.run = self.root / "run"
        self.run.mkdir()
        self.manifest = capture(self.root / "scope.json", self.run)
        self.check = {"id": "response", "kind": "rc_transient", "execution": "ok", "outcome": "defect",
            "summary": "Output below minimum", "candidate_id": self.manifest["candidate_id"],
            "evidence": ["work/response/measurement.json"]}
        write_json(self.run / "work/response/measurement.json", {"vout": 0.1})
        write_json(self.run / "checks/response.json", self.check)
        freeze_evidence(self.run, [self.check])
        self.witnesses = [{"requirement_id": "R1", "check_id": "response"}]
        self.evidence = [{"path": "checks/response.json", "locator": "outcome"}]

    def test_baseline_survives_packet_text_budget_and_is_programmatically_available(self):
        packet = build_packet(self.run, [self.check], "run1")
        self.assertEqual(packet["baseline"], load_baseline(self.run))
        self.assertEqual(set(packet["baseline"]["files"]),
                         {"design/requirements", "design/targets", "design/reference"})
        self.assertIn("R1", str(packet["baseline"]))

    def test_verified_baseline_is_citable_but_cannot_replace_failed_check_witness(self):
        self.assertEqual(validate_citation_path("baseline.json", self.run), "baseline.json")
        findings = collect_findings([], [self.check])
        citation = {"path": "baseline.json", "locator": "requirements/R1"}
        decisions = {"schema": "agent-ee.decisions/1", "candidate_id": self.manifest["candidate_id"],
            "run_id": "run1", "decisions": [{"finding_id": "check/response", "disposition": "accepted",
                "reason": "R1 violated", "evidence": [*self.evidence, citation]}]}
        self.assertEqual(adjudicate(findings, decisions, self.run)[0]["status"], "accepted")
        decisions["decisions"][0]["evidence"] = [citation]
        with self.assertRaisesRegex(ValueError, "cited check"):
            adjudicate(findings, decisions, self.run)

    def test_baseline_citation_refuses_mutation_missing_file_and_links(self):
        original = (self.run / "baseline.json").read_bytes()
        write_json(self.run / "baseline.json", {})
        with self.assertRaisesRegex(ValueError, "immutable baseline changed"):
            validate_citation_path("baseline.json", self.run)
        (self.run / "baseline.json").unlink()
        with self.assertRaisesRegex(ValueError, "does not exist"):
            validate_citation_path("baseline.json", self.run)
        (self.run / "work/baseline-copy.json").write_bytes(original)
        (self.run / "baseline.json").symlink_to("work/baseline-copy.json")
        with self.assertRaisesRegex(ValueError, "symlink"):
            validate_citation_path("baseline.json", self.run)

    def test_baseline_whitespace_reformat_and_nonexistent_locators_refused(self):
        original = (self.run / "baseline.json").read_bytes()
        parsed = read_json(self.run / "baseline.json")
        findings = collect_findings([], [self.check])

        for reformatted in (
            json.dumps(parsed, separators=(",", ":")).encode("utf-8"),
            (json.dumps(parsed, indent=4, sort_keys=True) + "\n").encode("utf-8"),
            original + b"\n",
        ):
            (self.run / "baseline.json").write_bytes(reformatted)
            with self.assertRaisesRegex(ValueError, "immutable baseline changed"):
                load_baseline(self.run)
            with self.assertRaisesRegex(ValueError, "immutable baseline changed"):
                verify_snapshot(self.run)
            with self.assertRaisesRegex(ValueError, "immutable baseline changed"):
                validate_citation({"path": "baseline.json", "locator": "/criteria"}, self.run)

        (self.run / "baseline.json").write_bytes(original)
        for valid_loc in ("/criteria", "/requirements/0/id", "requirements/R1", "/files"):
            self.assertEqual(
                validate_citation({"path": "baseline.json", "locator": valid_loc}, self.run),
                {"path": "baseline.json", "locator": valid_loc},
            )

        for bad_loc in ("/no_such_field", "/no_such_field/x", "requirements/R999", "/criteria/99"):
            with self.subTest(bad_locator=bad_loc):
                with self.assertRaisesRegex(ValueError, "does not resolve"):
                    validate_citation({"path": "baseline.json", "locator": bad_loc}, self.run)
                with self.assertRaisesRegex(ValueError, "does not resolve"):
                    adjudicate(
                        findings,
                        {
                            "schema": "agent-ee.decisions/1",
                            "candidate_id": self.manifest["candidate_id"],
                            "run_id": "run1",
                            "decisions": [
                                {
                                    "finding_id": "check/response",
                                    "disposition": "accepted",
                                    "reason": "Citing nonexistent baseline field",
                                    "evidence": [*self.evidence, {"path": "baseline.json", "locator": bad_loc}],
                                }
                            ],
                        },
                        self.run,
                    )

    def test_navigation_cannot_substitute_for_a_sealed_check_witness(self):
        build_packet(self.run, [self.check], "run1")
        with self.assertRaisesRegex(ValueError, "cited check"):
            validate_witnesses(self.run, self.witnesses,
                [{"path": "work/.navigation/index.json", "locator": "/entries/0"}])

    def test_baseline_mutation_and_missing_coverage_refused(self):
        write_json(self.run / "baseline.json", {})
        with self.assertRaisesRegex(ValueError, "baseline changed"):
            verify_snapshot(self.run)
        scope = copy.deepcopy(self.scope)
        scope["checks"].append({"id": "unmapped", "kind": "erc"})
        with self.assertRaisesRegex(ValueError, "every check"):
            compile_baseline(scope, self.manifest["files"])
        scope = copy.deepcopy(self.scope)
        scope["baseline"]["targets"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "not captured"):
            compile_baseline(scope, self.manifest["files"])

    def test_changed_raw_evidence_and_passing_check_cannot_establish_defect(self):
        validate_witnesses(self.run, self.witnesses, self.evidence)
        write_json(self.run / "work/response/measurement.json", {"vout": 0.63})
        with self.assertRaisesRegex(ValueError, "tool evidence changed"):
            validate_witnesses(self.run, self.witnesses, self.evidence)
        self.check["outcome"] = "pass"
        write_json(self.run / "checks/response.json", self.check)
        freeze_evidence(self.run, [self.check])
        with self.assertRaisesRegex(ValueError, "failed criterion"):
            validate_witnesses(self.run, self.witnesses, self.evidence)

    def test_arbitrary_file_citation_and_unmapped_requirement_refused(self):
        with self.assertRaisesRegex(ValueError, "cited check"):
            validate_witnesses(self.run, self.witnesses,
                               [{"path": "inputs/design/board", "locator": "anything"}])
        with self.assertRaisesRegex(ValueError, "trace"):
            validate_witnesses(self.run, [{"requirement_id": "invented", "check_id": "response"}], self.evidence)

    def test_unrelated_reviewer_claim_cannot_launder_a_real_failed_check(self):
        original = collect_findings([], [self.check])[0]
        fabricated = {**original, "id": "circuit/thermal", "source": "review",
            "title": "Invented unrelated thermal defect", "witnesses": self.witnesses}
        decisions = {"schema": "agent-ee.decisions/1", "candidate_id": self.manifest["candidate_id"],
            "run_id": "run1", "decisions": [{"finding_id": fabricated["id"], "disposition": "accepted",
                "reason": "borrowed RC evidence", "evidence": self.evidence}]}
        with self.assertRaisesRegex(ValueError, "only executable check"):
            adjudicate([fabricated], decisions, self.run)

    def test_manual_adjudication_uses_same_gate_as_automatic(self):
        findings = collect_findings([], [self.check])
        decisions = {"schema": "agent-ee.decisions/1", "candidate_id": self.manifest["candidate_id"],
            "run_id": "run1", "decisions": [{"finding_id": "check/response", "disposition": "accepted",
                "reason": "R1 violated", "evidence": self.evidence}]}
        self.assertEqual(adjudicate(findings, decisions, self.run)[0]["status"], "accepted")
        decisions["decisions"][0]["evidence"] = [{"path": "inputs/design/board", "locator": "board"}]
        with self.assertRaisesRegex(ValueError, "cited check"):
            adjudicate(findings, decisions, self.run)


if __name__ == "__main__":
    unittest.main()

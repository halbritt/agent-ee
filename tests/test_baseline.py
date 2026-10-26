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
        for valid_loc in ("/criteria", "/requirements/0/id", "/requirements/0", "/files"):
            self.assertEqual(
                validate_citation({"path": "baseline.json", "locator": valid_loc, "pointer": valid_loc}, self.run),
                {"path": "baseline.json", "locator": valid_loc, "pointer": valid_loc},
            )

        for bad_loc in ("/no_such_field", "/no_such_field/x", "/requirements/999", "/criteria/99"):
            with self.subTest(bad_locator=bad_loc):
                with self.assertRaisesRegex(ValueError, "does not resolve"):
                    validate_citation({"path": "baseline.json", "locator": bad_loc, "pointer": bad_loc}, self.run)
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
                                    "evidence": [*self.evidence, {"path": "baseline.json", "locator": bad_loc, "pointer": bad_loc}],
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


class AdvisoryDefectTests(unittest.TestCase):
    """AGENTEE-217: an advisory check's defect is adjudicable but never acceptance evidence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "source").mkdir()
        for name in ("board", "schematic", "requirements", "targets", "reference"):
            (root / "source" / name).write_text(name)
        congestion = {"clearance_mm": 0.2, "track_width_mm": 0.25, "cell_mm": 2.54, "routing_layers": ["F.Cu"]}
        scope = {"schema": "agent-ee.scope/3", "name": "test", "scope": "test",
            "board": "design/board", "schematic": "design/schematic",
            "inputs": [{"path": str(root / "source"), "at": "design"}],
            "checks": [{"id": "width", "kind": "geometry", "params": {"min_track_width_mm": 0.2}},
                       {"id": "congestion", "kind": "placement_congestion", "params": congestion}],
            "baseline": {"requirements": [
                {"id": "R1", "intent": "acceptance", "statement": "Track width >= 0.2 mm",
                 "source": {"path": "design/requirements", "locator": "R1"}, "checks": ["width"]},
                {"id": "I1", "intent": "informational", "statement": "Pre-route report",
                 "source": {"path": "design/requirements", "locator": "I1"}, "checks": ["congestion"]}],
                "targets": ["design/targets"], "reference_architectures": ["design/reference"]}}
        write_json(root / "scope.json", scope)
        self.run = root / "run"
        self.run.mkdir()
        self.candidate = capture(root / "scope.json", self.run)["candidate_id"]
        self.checks = {}
        for cid, kind, outcome in (("width", "geometry", "defect"), ("congestion", "placement_congestion", "defect")):
            self.checks[cid] = {"id": cid, "kind": kind, "execution": "ok", "outcome": outcome,
                "summary": f"{cid} {outcome}", "candidate_id": self.candidate, "evidence": [f"work/{cid}/report.json"]}
            write_json(self.run / f"work/{cid}/report.json", {"cid": cid})
        self.seal()

    def seal(self):
        for check in self.checks.values():
            write_json(self.run / f"checks/{check['id']}.json", check)
        freeze_evidence(self.run, list(self.checks.values()))
        self.findings = collect_findings([], list(self.checks.values()))

    def decide(self, *decisions):
        return {"schema": "agent-ee.decisions/1", "candidate_id": self.candidate, "run_id": "run1",
                "decisions": [{"finding_id": f"check/{cid}", "disposition": disposition, "reason": "sealed result",
                               "evidence": [{"path": f"checks/{cite}.json", "locator": "outcome"}],
                               **({"duplicate_of": f"check/{target}"} if target else {})}
                              for cid, disposition, cite, target in decisions]}

    def test_advisory_defect_is_accepted_without_a_requirement_witness(self):
        from agent_ee.run_record import outcome
        self.assertEqual(load_baseline(self.run)["coverage"]["requirements"]["I1"]["acceptance_checks"], [])
        updated = {f["id"]: f for f in adjudicate(self.findings, self.decide(
            ("width", "accepted", "width", None), ("congestion", "accepted", "congestion", None)), self.run)}
        congestion, width = updated["check/congestion"], updated["check/width"]
        self.assertEqual((congestion["status"], congestion["authority"], congestion["decision"]["authority"]),
                         ("accepted", "advisory", "advisory"))
        self.assertNotIn("witnesses", congestion)
        self.assertNotIn("authority", width)
        # The congestion defect never becomes a requirement witness.
        for rid in ("I1", "R1"):
            with self.assertRaisesRegex(ValueError, "audited acceptance|trace"):
                validate_witnesses(self.run, [{"requirement_id": rid, "check_id": "congestion"}],
                                   [{"path": "checks/congestion.json", "locator": "outcome"}])
        # A complete adjudication still fails closed on the defect outcome.
        result = {"state": "complete", "adjudicated": True, "checks": list(self.checks.values()),
                  "reviews": [{"role": "a"}, {"role": "b"}], "review_executions": [],
                  "findings": list(updated.values())}
        self.assertEqual(outcome(result), "defects_or_unresolved")
        self.checks["width"]["outcome"] = "pass"
        result["checks"] = list(self.checks.values())
        result["findings"] = [congestion]
        self.assertEqual(outcome(result), "defects_or_unresolved")

    def test_advisory_acceptance_requires_its_unchanged_sealed_failed_result(self):
        with self.assertRaisesRegex(ValueError, "cited check"):
            adjudicate(self.findings, self.decide(("congestion", "accepted", "width", None)), self.run)
        write_json(self.run / "work/congestion/report.json", {"cid": "changed"})
        with self.assertRaisesRegex(ValueError, "tool evidence changed"):
            adjudicate(self.findings, self.decide(("congestion", "accepted", "congestion", None)), self.run)
        write_json(self.run / "work/congestion/report.json", {"cid": "congestion"})
        self.checks["congestion"]["outcome"] = "info"
        write_json(self.run / "checks/congestion.json", self.checks["congestion"])
        with self.assertRaisesRegex(ValueError, "cited check"):
            adjudicate(self.findings, self.decide(("congestion", "accepted", "congestion", None)), self.run)

    def test_advisory_defect_cannot_be_deferred_noted_or_dismissed(self):
        for disposition, message in (("deferred", "cannot be deferred"), ("noted", "cannot be 'noted'"),
                                     ("dismissed", "cannot be dismissed")):
            with self.subTest(disposition=disposition), self.assertRaisesRegex(ValueError, message):
                adjudicate(self.findings, self.decide(("congestion", disposition, "congestion", None)), self.run)

    def test_acceptance_defect_cannot_be_duplicated_into_an_advisory_finding(self):
        with self.assertRaisesRegex(ValueError, "acceptance check defect cannot be represented by an advisory"):
            adjudicate(self.findings, self.decide(("congestion", "accepted", "congestion", None),
                                                  ("width", "duplicate", "width", "congestion")), self.run)
        accepted = adjudicate(self.findings, self.decide(("congestion", "accepted", "congestion", None)), self.run)
        with self.assertRaisesRegex(ValueError, "acceptance check defect cannot be represented by an advisory"):
            adjudicate(accepted, self.decide(("width", "duplicate", "width", "congestion")), self.run)
        # The other way round is allowed: the acceptance finding stays canonical.
        updated = {f["id"]: f for f in adjudicate(self.findings, self.decide(
            ("width", "accepted", "width", None), ("congestion", "duplicate", "congestion", "width")), self.run)}
        self.assertEqual(updated["check/congestion"]["status"], "duplicate")
        self.assertNotIn("authority", updated["check/congestion"])

    def test_an_acceptance_check_is_never_advisory(self):
        from agent_ee.baseline import advisory_check, validate_advisory_defect
        baseline = load_baseline(self.run)
        self.assertEqual((advisory_check(baseline, "congestion"), advisory_check(baseline, "width"),
                          advisory_check(baseline, "missing"), advisory_check(None, "congestion")),
                         (True, False, False, False))
        with self.assertRaisesRegex(ValueError, "no acceptance mapping"):
            validate_advisory_defect(self.run, "width", [{"path": "checks/width.json", "locator": "outcome"}])


if __name__ == "__main__":
    unittest.main()

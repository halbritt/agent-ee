import json
import tempfile
import unittest
from pathlib import Path

from agent_ee.candidate import capture
from agent_ee.findings import (
    adjudicate,
    collect_findings,
    validate_check_result,
    validate_citation,
    validate_citation_path,
    validate_closure,
    validate_review,
)
from agent_ee.storage import write_json


class FindingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "rc.kicad_sch").write_text("(kicad_sch (symbol R1 100k))")
        (self.source / "rc.kicad_pcb").write_text("(kicad_pcb (footprint R1 100k) (via (at 113 100)))")
        (self.source / "requirements.md").write_text("0.62 <= Vout(1ms) <= 0.65 V")
        self.scope_path = self.root / "scope.json"
        self.scope_data = {
            "schema": "agent-ee.scope/1",
            "name": "rc-fixture",
            "scope": "RC low-pass filter verification",
            "schematic": "project/rc.kicad_sch",
            "board": "project/rc.kicad_pcb",
            "requirements": ["project/requirements.md"],
            "checks": [
                {"id": "erc", "kind": "erc"},
                {"id": "drc", "kind": "drc"},
                {"id": "rc_transient", "kind": "rc_transient"},
            ],
            "inputs": [{"path": "source", "at": "project"}],
        }
        self.scope_data["review_profile"] = json.loads(
            (Path(__file__).resolve().parents[1] / "profiles/claude.json").read_text())
        self.scope_path.write_text(json.dumps(self.scope_data))
        self.run_dir = self.root / "run1"
        self.run_dir.mkdir()
        self.manifest = capture(self.scope_path, self.run_dir)
        self.candidate_id = self.manifest["candidate_id"]
        self.run_id = "run-101"
        self.packet_sha256 = "pkt-sha256-001"

        self.erc_check = {
            "id": "erc",
            "kind": "erc",
            "execution": "ok",
            "outcome": "pass",
            "summary": "ERC clean (1 sheet)",
            "evidence": ["checks/erc.json"],
            "details": {"violations": 0},
            "limitations": [],
        }
        self.rc_check = {
            "id": "rc_transient",
            "kind": "rc_transient",
            "execution": "ok",
            "outcome": "defect",
            "summary": "Vout(1ms) = 0.0952 V outside [0.62, 0.65] V",
            "evidence": ["checks/rc_transient.json", "work/rc_transient/spice.log"],
            "details": {"v_1ms": 0.095162582},
            "limitations": ["Ideal unloaded RC simulation"],
        }
        write_json(self.run_dir / "checks" / "erc.json", self.erc_check)
        write_json(self.run_dir / "checks" / "rc_transient.json", self.rc_check)
        (self.run_dir / "work" / "rc_transient").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "work" / "rc_transient" / "spice.log").write_text("v_1ms = 9.516258e-02\n")
        write_json(
            self.run_dir / "result.json",
            {"schema": "agent-ee.result/1", "candidate_id": self.candidate_id, "run_id": self.run_id},
        )

    def _finding(self, fid="r1-tau-too-slow", assessment="defect", evidence=None):
        if evidence is None:
            evidence = [
                {"path": "inputs/project/rc.kicad_sch", "locator": "symbol R1 value 100k"},
                {"path": "checks/rc_transient.json", "locator": "details.v_1ms=0.095162582"},
                {"path": "work/rc_transient/spice.log", "locator": "line 1: v_1ms"},
            ]
        return {
            "id": fid,
            "title": "R1 is 100 kohm instead of 10 kohm, causing Vout(1ms) = 0.095 V",
            "location": "R1 (project/rc.kicad_sch, project/rc.kicad_pcb)",
            "condition": "1 V ideal step input on IN, unloaded OUT",
            "mechanism": "tau = R1 * C1 = 100k * 100n = 10 ms; Vout(1ms) = 1 - exp(-0.1) = 0.0952 V < 0.62 V",
            "severity": "blocking",
            "assessment": assessment,
            "evidence": evidence,
            "reasoning": "Exported netlist and transient check confirm 10 ms time constant violates 1 ms step requirement.",
        }

    def _review(self, role="circuit", findings=None):
        return {
            "schema": "agent-ee.review/1",
            "candidate_id": self.candidate_id,
            "run_id": self.run_id,
            "packet_sha256": self.packet_sha256,
            "role": role,
            "coverage": ["Inspected R1, C1 values and transient check output"],
            "limitations": ["Temperature and tolerance not modeled"],
            "findings": [self._finding()] if findings is None else findings,
        }

    def test_stale_or_malformed_review_and_packet_mismatch_rejected(self):
        valid = validate_review(
            self._review(),
            self.candidate_id,
            self.run_id,
            self.packet_sha256,
            self.run_dir,
        )
        self.assertEqual(valid["role"], "circuit")
        self.assertEqual(valid["findings"][0]["assessment"], "defect")

        for label, mutator in (
            ("stale_candidate", lambda r: r.__setitem__("candidate_id", "stale-candidate-id")),
            ("stale_run", lambda r: r.__setitem__("run_id", "stale-run-id")),
            ("stale_packet", lambda r: r.__setitem__("packet_sha256", "stale-packet-hash")),
            ("bad_schema", lambda r: r.__setitem__("schema", "agent-ee.review/99")),
            ("unsafe_role", lambda r: r.__setitem__("role", "../bad-role")),
            ("reserved_role_check", lambda r: r.__setitem__("role", "check")),
            ("reserved_role_adjudicator", lambda r: r.__setitem__("role", "adjudicator")),
            ("extra_top_key", lambda r: r.__setitem__("unexpected", True)),
            ("duplicate_finding_id", lambda r: r.__setitem__("findings", [self._finding("dup"), self._finding("dup")])),
            ("invalid_severity", lambda r: r["findings"][0].__setitem__("severity", "critical")),
            ("empty_mechanism", lambda r: r["findings"][0].__setitem__("mechanism", "   ")),
        ):
            bad = self._review()
            mutator(bad)
            with self.subTest(case=label), self.assertRaises(ValueError):
                validate_review(bad, self.candidate_id, self.run_id, self.packet_sha256, self.run_dir)

        write_json(
            self.run_dir / "packet.json",
            {
                "schema": "agent-ee.packet/1",
                "candidate_id": self.candidate_id,
                "run_id": self.run_id,
                "packet_sha256": "different-packet-sha",
            },
        )
        with self.assertRaisesRegex(ValueError, "packet.json"):
            validate_review(self._review(), self.candidate_id, self.run_id, self.packet_sha256, self.run_dir)

    def test_invalid_citation_paths_rejected(self):
        (self.run_dir / "reviews").mkdir(exist_ok=True)
        write_json(self.run_dir / "reviews" / "physical.json", {"role": "physical"})
        symlink_path = self.run_dir / "checks" / "linked.json"
        symlink_path.symlink_to(self.run_dir / "checks" / "erc.json")

        for bad_path in (
            "inputs/project/does_not_exist.kicad_sch",
            "inputs/../scope.json",
            "/etc/passwd",
            "reviews/physical.json",
            "result.json",
            "work/console.log",
            "checks/linked.json",
        ):
            rev = self._review(findings=[self._finding(evidence=[{"path": bad_path, "locator": "line 1"}])])
            with self.subTest(path=bad_path), self.assertRaises(ValueError):
                validate_review(rev, self.candidate_id, self.run_id, self.packet_sha256, self.run_dir)

        empty_loc = self._review(
            findings=[self._finding(evidence=[{"path": "inputs/project/rc.kicad_sch", "locator": "  "}])]
        )
        with self.assertRaisesRegex(ValueError, "locator"):
            validate_review(empty_loc, self.candidate_id, self.run_id, self.packet_sha256, self.run_dir)

    def test_legacy_run_cannot_plant_a_citable_baseline(self):
        write_json(self.run_dir / "baseline.json", {"requirements": []})
        with self.assertRaisesRegex(ValueError, "requires an immutable"):
            validate_citation_path("baseline.json", self.run_dir)

    def test_unsupported_defect_downgraded_to_concern_and_cannot_be_accepted_without_evidence(self):
        ungrounded = self._review(findings=[self._finding(fid="unbacked-claim", assessment="defect", evidence=[])])
        validated = validate_review(
            ungrounded, self.candidate_id, self.run_id, self.packet_sha256, self.run_dir
        )
        finding = validated["findings"][0]
        self.assertEqual(finding["assessment"], "concern")
        self.assertEqual(finding["downgraded_from"], "defect")
        self.assertTrue(any("downgraded from defect to concern" in lim for lim in validated["limitations"]))

        collected = collect_findings([validated], [])
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0]["id"], "circuit/unbacked-claim")
        self.assertEqual(collected[0]["assessment"], "concern")
        self.assertEqual(collected[0]["status"], "unresolved")

        with self.assertRaisesRegex(ValueError, "requires assessment='defect'"):
            adjudicate(
                collected,
                {
                    "schema": "agent-ee.decisions/1",
                    "candidate_id": self.candidate_id,
                    "run_id": self.run_id,
                    "decisions": [
                        {
                            "finding_id": "circuit/unbacked-claim",
                            "disposition": "accepted",
                            "reason": "Concerns cannot be accepted as defects",
                            "evidence": [{"path": "inputs/project/rc.kicad_sch", "locator": "R1"}],
                        }
                    ],
                },
                self.run_dir,
            )

        with self.assertRaisesRegex(ValueError, "unsupported or downgraded claim cannot be 'noted'"):
            adjudicate(
                collected,
                {
                    "schema": "agent-ee.decisions/1",
                    "candidate_id": self.candidate_id,
                    "run_id": self.run_id,
                    "decisions": [
                        {
                            "finding_id": "circuit/unbacked-claim",
                            "disposition": "noted",
                            "reason": "Unsupported downgraded claim cannot be noted",
                            "evidence": [{"path": "inputs/project/rc.kicad_sch", "locator": "R1"}],
                        }
                    ],
                },
                self.run_dir,
            )

        deferred = adjudicate(
            collected,
            {
                "schema": "agent-ee.decisions/1",
                "candidate_id": self.candidate_id,
                "run_id": self.run_id,
                "decisions": [
                    {
                        "finding_id": "circuit/unbacked-claim",
                        "disposition": "deferred",
                        "reason": "No thermal/tolerance model in scope; retaining as open concern.",
                        "evidence": [],
                    }
                ],
            },
            self.run_dir,
        )
        self.assertEqual(deferred[0]["status"], "deferred")
        self.assertEqual(deferred[0]["disposition"], "deferred")
        self.assertEqual(deferred[0]["decision"]["reason"], deferred[0]["reason"])

    def test_noted_disposition_contract_and_restrictions(self):
        minor_concern_1 = {**self._finding(fid="gnd-single-via", assessment="concern"), "severity": "minor"}
        info_concern_dup = {**self._finding(fid="gnd-via-note", assessment="concern"), "severity": "info"}
        major_concern = {**self._finding(fid="major-concern", assessment="concern"), "severity": "major"}
        real_defect = self._finding(fid="real-defect", assessment="defect")

        r_circuit = validate_review(
            self._review(role="circuit", findings=[info_concern_dup, major_concern]),
            self.candidate_id,
            self.run_id,
            self.packet_sha256,
            self.run_dir,
        )
        r_physical = validate_review(
            self._review(role="physical", findings=[minor_concern_1, real_defect]),
            self.candidate_id,
            self.run_id,
            self.packet_sha256,
            self.run_dir,
        )
        findings = collect_findings([r_circuit, r_physical], [self.erc_check, self.rc_check])
        ev = [{"path": "inputs/project/rc.kicad_pcb", "locator": "via (113,100)"}]

        # 1. Established defect, check failure, or major/blocking concern cannot be 'noted'.
        for bad_fid, expected_msg in (
            ("check/rc_transient", "established defect or check failure cannot be 'noted'"),
            ("physical/real-defect", "established defect or check failure cannot be 'noted'"),
            ("circuit/major-concern", "'major' concern cannot be 'noted'"),
        ):
            with self.subTest(bad_noted=bad_fid), self.assertRaisesRegex(ValueError, expected_msg):
                adjudicate(
                    findings,
                    {
                        "schema": "agent-ee.decisions/1",
                        "candidate_id": self.candidate_id,
                        "run_id": self.run_id,
                        "decisions": [
                            {
                                "finding_id": bad_fid,
                                "disposition": "noted",
                                "reason": "Attempting to note defect or major concern",
                                "evidence": ev,
                            }
                        ],
                    },
                    self.run_dir,
                )

        # 2. 'noted' requires non-empty evidence citations.
        with self.assertRaisesRegex(ValueError, "noted disposition requires non-empty evidence"):
            adjudicate(
                findings,
                {
                    "schema": "agent-ee.decisions/1",
                    "candidate_id": self.candidate_id,
                    "run_id": self.run_id,
                    "decisions": [
                        {
                            "finding_id": "physical/gnd-single-via",
                            "disposition": "noted",
                            "reason": "Verified observation without citing evidence",
                            "evidence": [],
                        }
                    ],
                },
                self.run_dir,
            )

        # 3. Duplicating a defect or major concern into a 'noted' target must fail.
        for bad_dup_fid in ("physical/real-defect", "circuit/major-concern", "check/rc_transient"):
            with self.subTest(bad_dup_into_noted=bad_dup_fid), self.assertRaisesRegex(ValueError, "duplicate"):
                adjudicate(
                    findings,
                    {
                        "schema": "agent-ee.decisions/1",
                        "candidate_id": self.candidate_id,
                        "run_id": self.run_id,
                        "decisions": [
                            {
                                "finding_id": "physical/gnd-single-via",
                                "disposition": "noted",
                                "reason": "Verified non-defect observation",
                                "evidence": ev,
                            },
                            {
                                "finding_id": bad_dup_fid,
                                "disposition": "duplicate",
                                "duplicate_of": "physical/gnd-single-via",
                                "reason": "Trying to hide defect or major concern behind noted observation",
                                "evidence": ev,
                            },
                        ],
                    },
                    self.run_dir,
                )

        # 4. Valid adjudication: minor/info concern noted with reason+citations, other info concern duplicated to it,
        #    major concern deferred, and real defect accepted.
        adjudicated = adjudicate(
            findings,
            {
                "schema": "agent-ee.decisions/1",
                "candidate_id": self.candidate_id,
                "run_id": self.run_id,
                "decisions": [
                    {
                        "finding_id": "physical/gnd-single-via",
                        "disposition": "noted",
                        "reason": "Verified in native board: single via + B.Cu zone fill connects J1.3 as saved.",
                        "evidence": ev,
                    },
                    {
                        "finding_id": "circuit/gnd-via-note",
                        "disposition": "duplicate",
                        "duplicate_of": "physical/gnd-single-via",
                        "reason": "Same single-via GND return observation.",
                        "evidence": ev,
                    },
                    {
                        "finding_id": "circuit/major-concern",
                        "disposition": "deferred",
                        "reason": "Unresolved major concern stays deferred.",
                        "evidence": ev,
                    },
                    {
                        "finding_id": "physical/real-defect",
                        "disposition": "accepted",
                        "reason": "Confirmed R1=100k defect.",
                        "evidence": [{"path": "checks/rc_transient.json", "locator": "details.v_1ms"}],
                    },
                    {
                        "finding_id": "check/rc_transient",
                        "disposition": "duplicate",
                        "duplicate_of": "physical/real-defect",
                        "reason": "Same R1=100k defect.",
                        "evidence": [{"path": "checks/rc_transient.json", "locator": "summary"}],
                    },
                ],
            },
            self.run_dir,
        )
        by_id = {f["id"]: f for f in adjudicated}
        self.assertEqual(by_id["physical/gnd-single-via"]["status"], "noted")
        self.assertEqual(by_id["physical/gnd-single-via"]["disposition"], "noted")
        self.assertEqual(by_id["physical/gnd-single-via"]["duplicates"], ["circuit/gnd-via-note"])
        self.assertEqual(len(by_id["physical/gnd-single-via"]["evidence"]), 3)
        self.assertEqual(by_id["circuit/gnd-via-note"]["status"], "duplicate")
        self.assertEqual(by_id["circuit/gnd-via-note"]["duplicate_of"], "physical/gnd-single-via")
        self.assertEqual(by_id["circuit/major-concern"]["status"], "deferred")
        self.assertEqual(by_id["physical/real-defect"]["status"], "accepted")

    def test_non_ok_check_execution_requires_unknown_outcome_and_never_becomes_defect(self):
        for exec_status in ("failed", "unavailable", "timeout", "malformed", "not_run", "interrupted", "unsupported"):
            for bad_outcome in ("pass", "defect", "info"):
                bad_check = {
                    "id": "broken_check",
                    "kind": "erc",
                    "execution": exec_status,
                    "outcome": bad_outcome,
                    "summary": "Non-ok execution must be unknown",
                    "evidence": [],
                }
                with self.subTest(execution=exec_status, outcome=bad_outcome), self.assertRaisesRegex(
                    ValueError, "dishonest status"
                ):
                    validate_check_result(bad_check)
                with self.subTest(collect_execution=exec_status, outcome=bad_outcome), self.assertRaisesRegex(
                    ValueError, "dishonest status"
                ):
                    collect_findings([], [bad_check])

        honest_failed_check = {
            "id": "broken_check",
            "kind": "erc",
            "execution": "failed",
            "outcome": "unknown",
            "summary": "Tool crashed; coverage is unknown, not a pass or established defect",
            "evidence": [],
        }
        self.assertEqual(collect_findings([], [honest_failed_check]), [])

    def test_supported_singleton_survives_clean_other_reviews_and_check_defects_cannot_be_dismissed(self):
        pre_review_findings = collect_findings([], [self.erc_check, self.rc_check])
        self.assertEqual([f["id"] for f in pre_review_findings], ["check/rc_transient"])
        self.assertEqual(pre_review_findings[0]["status"], "unresolved")

        circuit_review = validate_review(
            self._review(role="circuit"),
            self.candidate_id,
            self.run_id,
            self.packet_sha256,
            self.run_dir,
        )
        physical_clean = validate_review(
            self._review(role="physical", findings=[]),
            self.candidate_id,
            self.run_id,
            self.packet_sha256,
            self.run_dir,
        )
        layout_clean = validate_review(
            self._review(role="layout", findings=[]),
            self.candidate_id,
            self.run_id,
            self.packet_sha256,
            self.run_dir,
        )

        all_findings = collect_findings(
            [circuit_review, physical_clean, layout_clean],
            [self.erc_check, self.rc_check],
        )
        self.assertEqual(
            [f["id"] for f in all_findings],
            ["check/rc_transient", "circuit/r1-tau-too-slow"],
        )

        # Authoritative check defect cannot be dismissed or deferred by adjudicator.
        for bad_disp in ("dismissed", "deferred"):
            with self.subTest(bad_check_disp=bad_disp), self.assertRaisesRegex(
                ValueError, "authoritative check defect"
            ):
                adjudicate(
                    all_findings,
                    {
                        "schema": "agent-ee.decisions/1",
                        "candidate_id": self.candidate_id,
                        "run_id": self.run_id,
                        "decisions": [
                            {
                                "finding_id": "check/rc_transient",
                                "disposition": bad_disp,
                                "reason": "Trying to waive native/simulation check defect",
                                "evidence": [{"path": "checks/rc_transient.json", "locator": "summary"}],
                            }
                        ],
                    },
                    self.run_dir,
                )

        adjudicated = adjudicate(
            all_findings,
            {
                "schema": "agent-ee.decisions/1",
                "candidate_id": self.candidate_id,
                "run_id": self.run_id,
                "decisions": [
                    {
                        "finding_id": "circuit/r1-tau-too-slow",
                        "disposition": "accepted",
                        "reason": "Confirmed R1=100k in schematic and spice Vout(1ms)=0.0952V < 0.62V.",
                        "evidence": [
                            {"path": "inputs/project/rc.kicad_sch", "locator": "R1 100k"},
                            {"path": "checks/rc_transient.json", "locator": "details.v_1ms"},
                        ],
                    },
                    {
                        "finding_id": "check/rc_transient",
                        "disposition": "duplicate",
                        "duplicate_of": "circuit/r1-tau-too-slow",
                        "reason": "Same root cause (R1=100k causing tau=10ms).",
                        "evidence": [{"path": "checks/rc_transient.json", "locator": "summary"}],
                    },
                ],
            },
            self.run_dir,
        )
        by_id = {f["id"]: f for f in adjudicated}
        self.assertEqual(by_id["circuit/r1-tau-too-slow"]["status"], "accepted")
        self.assertEqual(by_id["circuit/r1-tau-too-slow"]["duplicates"], ["check/rc_transient"])
        self.assertEqual(by_id["check/rc_transient"]["status"], "duplicate")
        self.assertEqual(by_id["check/rc_transient"]["duplicate_of"], "circuit/r1-tau-too-slow")

    def test_dismissed_finding_requires_reason_and_counter_evidence_for_supported_defect(self):
        rev = validate_review(
            self._review(
                role="circuit",
                findings=[
                    self._finding(fid="supported-claim"),
                    self._finding(fid="unsupported-concern", assessment="concern", evidence=[]),
                ],
            ),
            self.candidate_id,
            self.run_id,
            self.packet_sha256,
            self.run_dir,
        )
        findings = collect_findings([rev], [])

        with self.assertRaisesRegex(ValueError, "reason"):
            adjudicate(
                findings,
                {
                    "schema": "agent-ee.decisions/1",
                    "candidate_id": self.candidate_id,
                    "run_id": self.run_id,
                    "decisions": [
                        {
                            "finding_id": "circuit/unsupported-concern",
                            "disposition": "dismissed",
                            "reason": "   ",
                            "evidence": [],
                        }
                    ],
                },
                self.run_dir,
            )

        with self.assertRaisesRegex(ValueError, "counter-evidence"):
            adjudicate(
                findings,
                {
                    "schema": "agent-ee.decisions/1",
                    "candidate_id": self.candidate_id,
                    "run_id": self.run_id,
                    "decisions": [
                        {
                            "finding_id": "circuit/supported-claim",
                            "disposition": "dismissed",
                            "reason": "Claiming dismissal without citing evidence",
                            "evidence": [],
                        }
                    ],
                },
                self.run_dir,
            )

        decided = adjudicate(
            findings,
            {
                "schema": "agent-ee.decisions/1",
                "candidate_id": self.candidate_id,
                "run_id": self.run_id,
                "decisions": [
                    {
                        "finding_id": "circuit/supported-claim",
                        "disposition": "dismissed",
                        "reason": "ERC check confirms net connectivity is valid.",
                        "evidence": [{"path": "checks/erc.json", "locator": "details.violations=0"}],
                    },
                    {
                        "finding_id": "circuit/unsupported-concern",
                        "disposition": "dismissed",
                        "reason": "Speculative concern outside declared unloaded step scope.",
                        "evidence": [{"path": "inputs/project/requirements.md", "locator": "line 1"}],
                    },
                ],
            },
            self.run_dir,
        )
        for item in decided:
            self.assertEqual(item["status"], "dismissed")
            self.assertEqual(item["disposition"], "dismissed")
            self.assertTrue(item["reason"])
            self.assertEqual(item["decision"]["reason"], item["reason"])
        self.assertEqual(len(decided[0]["evidence"]), 3)
        self.assertEqual(len(decided[0]["decision_evidence"]), 1)

    def test_duplicate_validation_rejects_self_unknown_cycle_and_hiding_behind_dismissed(self):
        r1 = validate_review(
            self._review(role="circuit", findings=[self._finding("f1"), self._finding("f2")]),
            self.candidate_id,
            self.run_id,
            self.packet_sha256,
            self.run_dir,
        )
        r2 = validate_review(
            self._review(role="physical", findings=[self._finding("f3")]),
            self.candidate_id,
            self.run_id,
            self.packet_sha256,
            self.run_dir,
        )
        findings = collect_findings([r1, r2], [])
        ev = [{"path": "checks/rc_transient.json", "locator": "summary"}]

        for case, dec_list in (
            (
                "self_duplicate",
                [{"finding_id": "circuit/f1", "disposition": "duplicate", "duplicate_of": "circuit/f1", "reason": "self", "evidence": ev}],
            ),
            (
                "unknown_target",
                [{"finding_id": "circuit/f1", "disposition": "duplicate", "duplicate_of": "circuit/nope", "reason": "missing", "evidence": ev}],
            ),
            (
                "two_node_cycle",
                [
                    {"finding_id": "circuit/f1", "disposition": "duplicate", "duplicate_of": "circuit/f2", "reason": "a->b", "evidence": ev},
                    {"finding_id": "circuit/f2", "disposition": "duplicate", "duplicate_of": "circuit/f1", "reason": "b->a", "evidence": ev},
                ],
            ),
            (
                "three_node_cycle",
                [
                    {"finding_id": "circuit/f1", "disposition": "duplicate", "duplicate_of": "circuit/f2", "reason": "1->2", "evidence": ev},
                    {"finding_id": "circuit/f2", "disposition": "duplicate", "duplicate_of": "physical/f3", "reason": "2->3", "evidence": ev},
                    {"finding_id": "physical/f3", "disposition": "duplicate", "duplicate_of": "circuit/f1", "reason": "3->1", "evidence": ev},
                ],
            ),
            (
                "hide_behind_dismissed",
                [
                    {"finding_id": "circuit/f1", "disposition": "dismissed", "reason": "dismissed target", "evidence": ev},
                    {"finding_id": "circuit/f2", "disposition": "duplicate", "duplicate_of": "circuit/f1", "reason": "hiding", "evidence": ev},
                ],
            ),
        ):
            with self.subTest(case=case), self.assertRaises(ValueError):
                adjudicate(
                    findings,
                    {
                        "schema": "agent-ee.decisions/1",
                        "candidate_id": self.candidate_id,
                        "run_id": self.run_id,
                        "decisions": dec_list,
                    },
                    self.run_dir,
                )

    def test_closure_unified_between_adjudicate_and_validate_closure(self):
        rev = validate_review(
            self._review(role="circuit"),
            self.candidate_id,
            self.run_id,
            self.packet_sha256,
            self.run_dir,
        )
        findings = collect_findings([rev], [self.erc_check, self.rc_check])
        accepted = adjudicate(
            findings,
            {
                "schema": "agent-ee.decisions/1",
                "candidate_id": self.candidate_id,
                "run_id": self.run_id,
                "decisions": [
                    {
                        "finding_id": "circuit/r1-tau-too-slow",
                        "disposition": "accepted",
                        "reason": "R1=100k gives Vout(1ms)=0.0952V, violating [0.62, 0.65]V.",
                        "evidence": [{"path": "checks/rc_transient.json", "locator": "details.v_1ms"}],
                    },
                    {
                        "finding_id": "check/rc_transient",
                        "disposition": "duplicate",
                        "duplicate_of": "circuit/r1-tau-too-slow",
                        "reason": "Same R1=100k fault.",
                        "evidence": [{"path": "checks/rc_transient.json", "locator": "summary"}],
                    },
                ],
            },
            self.run_dir,
        )
        write_json(
            self.run_dir / "result.json",
            {
                "schema": "agent-ee.result/1",
                "candidate_id": self.candidate_id,
                "run_id": self.run_id,
                "state": "complete",
                "outcome": "defects_or_unresolved",
                "checks": [self.erc_check, self.rc_check],
                "findings": accepted,
            },
        )

        # Attempting to close in the SAME candidate must fail.
        with self.assertRaisesRegex(ValueError, "same candidate"):
            adjudicate(
                accepted,
                {
                    "schema": "agent-ee.decisions/1",
                    "candidate_id": self.candidate_id,
                    "run_id": self.run_id,
                    "decisions": [
                        {
                            "finding_id": "circuit/r1-tau-too-slow",
                            "disposition": "closed",
                            "previous_run": str(self.run_dir),
                            "reason": "Cannot close without a new repaired candidate.",
                            "evidence": [{"path": "checks/rc_transient.json", "locator": "details.v_1ms"}],
                        }
                    ],
                },
                self.run_dir,
            )

        # Repair source (100k -> 10k in both schematic and board) and capture fresh run2.
        (self.source / "rc.kicad_sch").write_text("(kicad_sch (symbol R1 10k))")
        (self.source / "rc.kicad_pcb").write_text("(kicad_pcb (footprint R1 10k))")
        run2 = self.root / "run2"
        run2.mkdir()
        manifest2 = capture(self.scope_path, run2)
        cand2 = manifest2["candidate_id"]
        run2_id = "run-202"
        self.assertNotEqual(self.candidate_id, cand2)

        write_json(run2 / "checks" / "erc.json", self.erc_check)
        failed_rc = {
            **self.rc_check,
            "execution": "failed",
            "outcome": "unknown",
            "evidence": ["checks/rc_transient.json"],
        }
        write_json(run2 / "checks" / "rc_transient.json", failed_rc)
        write_json(
            run2 / "result.json",
            {
                "schema": "agent-ee.result/1",
                "candidate_id": cand2,
                "run_id": run2_id,
                "state": "complete",
                "outcome": "incomplete",
                "previous_run": str(self.run_dir),
                "checks": [self.erc_check, failed_rc],
                "findings": [],
            },
        )
        with self.assertRaises(ValueError):
            validate_closure(self.run_dir, run2, "circuit/r1-tau-too-slow", ["rc_transient"], "Repaired R1 to 10k")
        with self.assertRaisesRegex(ValueError, "has not satisfied its declared criteria"):
            adjudicate(
                accepted,
                {
                    "schema": "agent-ee.decisions/1",
                    "candidate_id": cand2,
                    "run_id": run2_id,
                    "decisions": [
                        {
                            "finding_id": "circuit/r1-tau-too-slow",
                            "disposition": "closed",
                            "previous_run": str(self.run_dir),
                            "reason": "Repaired R1 to 10k",
                            "evidence": [{"path": "checks/rc_transient.json", "locator": "details.v_1ms"}],
                        }
                    ],
                },
                run2,
            )

        # Now write fresh passing rc_transient check in run2.
        fresh_rc_pass = {
            "id": "rc_transient",
            "kind": "rc_transient",
            "execution": "ok",
            "outcome": "pass",
            "summary": "Vout(1ms) = 0.6321 V within [0.62, 0.65] V",
            "evidence": ["checks/rc_transient.json"],
            "details": {"v_1ms": 0.6321205588},
            "limitations": ["Ideal unloaded RC simulation"],
        }
        write_json(run2 / "checks" / "rc_transient.json", fresh_rc_pass)
        write_json(
            run2 / "result.json",
            {
                "schema": "agent-ee.result/1",
                "candidate_id": cand2,
                "run_id": run2_id,
                "state": "complete",
                "outcome": "criteria_satisfied",
                "checks": [self.erc_check, fresh_rc_pass],
                "findings": [],
            },
        )

        # Without previous_run linkage (in decision or result.json), adjudicate refuses closed disposition.
        with self.assertRaisesRegex(ValueError, "previous_run"):
            adjudicate(
                accepted,
                {
                    "schema": "agent-ee.decisions/1",
                    "candidate_id": cand2,
                    "run_id": run2_id,
                    "decisions": [
                        {
                            "finding_id": "circuit/r1-tau-too-slow",
                            "disposition": "closed",
                            "reason": "Missing previous_run linkage",
                            "evidence": [{"path": "checks/rc_transient.json", "locator": "details.v_1ms=0.6321205588"}],
                        }
                    ],
                },
                run2,
            )

        correction = validate_closure(
            self.run_dir,
            run2,
            "circuit/r1-tau-too-slow",
            ["erc", "rc_transient"],
            "Restored R1=10k in schematic and PCB; Vout(1ms)=0.6321V passes.",
            evidence=[{"path": "checks/rc_transient.json", "locator": "details.v_1ms=0.6321205588"}],
        )
        self.assertEqual(correction["old_candidate_id"], self.candidate_id)
        self.assertEqual(correction["new_candidate_id"], cand2)

        closed_findings = adjudicate(
            accepted,
            {
                "schema": "agent-ee.decisions/1",
                "candidate_id": cand2,
                "run_id": run2_id,
                "decisions": [
                    {
                        "finding_id": "circuit/r1-tau-too-slow",
                        "disposition": "closed",
                        "previous_run": str(self.run_dir),
                        "checks": ["erc", "rc_transient"],
                        "reason": "Restored R1=10k in schematic and PCB; fresh rc_transient passes.",
                        "evidence": [{"path": "checks/rc_transient.json", "locator": "details.v_1ms=0.6321205588"}],
                    }
                ],
            },
            run2,
        )
        closed_target = next(f for f in closed_findings if f["id"] == "circuit/r1-tau-too-slow")
        self.assertEqual(closed_target["status"], "closed")
        self.assertEqual(len(closed_target["events"]), 2)
        self.assertEqual(closed_target["events"][0]["disposition"], "accepted")
        self.assertEqual(closed_target["events"][1]["disposition"], "closed")
        self.assertEqual(closed_target["events"][1]["closed_in_candidate_id"], cand2)

        # Changing requirements.md in run3 must refuse closure in BOTH validate_closure and adjudicate.
        (self.source / "requirements.md").write_text("0.05 <= Vout(1ms) <= 0.95 V")
        run3 = self.root / "run3"
        run3.mkdir()
        manifest3 = capture(self.scope_path, run3)
        write_json(run3 / "checks" / "erc.json", self.erc_check)
        write_json(run3 / "checks" / "rc_transient.json", fresh_rc_pass)
        write_json(
            run3 / "result.json",
            {
                "schema": "agent-ee.result/1",
                "candidate_id": manifest3["candidate_id"],
                "run_id": "run-303",
                "state": "complete",
                "outcome": "criteria_satisfied",
                "previous_run": str(self.run_dir),
                "checks": [self.erc_check, fresh_rc_pass],
                "findings": [],
            },
        )
        with self.assertRaisesRegex(ValueError, "requirements changed"):
            validate_closure(
                self.run_dir,
                run3,
                "circuit/r1-tau-too-slow",
                ["erc", "rc_transient"],
                "Weakened requirement instead of fixing circuit",
            )
        with self.assertRaisesRegex(ValueError, "requirements changed"):
            adjudicate(
                accepted,
                {
                    "schema": "agent-ee.decisions/1",
                    "candidate_id": manifest3["candidate_id"],
                    "run_id": "run-303",
                    "decisions": [
                        {
                            "finding_id": "circuit/r1-tau-too-slow",
                            "disposition": "closed",
                            "previous_run": str(self.run_dir),
                            "reason": "Weakened requirement instead of fixing circuit",
                            "evidence": [{"path": "checks/rc_transient.json", "locator": "details.v_1ms"}],
                        }
                    ],
                },
                run3,
            )

    def test_structured_citations_resolve_and_preserve_fields(self):
        exp = self.run_dir / "review-work/circuit/experiments"
        exp.mkdir(parents=True)
        write_json(exp / "probe.json", {"a/b": {"~key": [None, 7]}, "": "empty key"})
        (exp / "design.xml").write_text("<export><components><comp ref='R1'><value>10k</value></comp></components></export>")
        examples = [
            {"path": "checks/rc_transient.json", "pointer": "/details/v_1ms"},
            {"path": "checks/rc_transient.json", "pointer": ""},
            {"path": "inputs/project/rc.kicad_sch", "lines": [1, 1]},
            {"path": "work/rc_transient/spice.log", "lines": [1, 1]},
            {"path": "review-work/circuit/experiments/probe.json", "pointer": "/a~1b/~0key/0"},
            {"path": "review-work/circuit/experiments/probe.json", "pointer": "/"},
            {"path": "review-work/circuit/experiments/design.xml", "xpath": "/export/components/comp[@ref='R1']/value"},
            {"path": "review-work/circuit/experiments/design.xml", "xpath": "./components/comp"},
            {"path": "review-work/circuit/experiments/design.xml", "xpath": "/export"},
            {"path": "checks/rc_transient.json", "pointer": "/outcome", "lines": [1, 2]},
        ]
        for fields in examples:
            cite = {"locator": "Description is never parsed: /IN, e.g. R36/R37", **fields}
            with self.subTest(cite=cite):
                self.assertEqual(validate_citation(cite, self.run_dir, require_structured=True), cite)
        check = {**self.rc_check, "evidence": [dict(path="checks/rc_transient.json", locator="metric", pointer="/details/v_1ms")]}
        validate_check_result(check, self.run_dir)
        self.assertEqual(collect_findings([], [check])[0]["evidence"], check["evidence"])

    def test_structured_citations_refuse_missing_targets_and_malformed_fields(self):
        exp = self.run_dir / "review-work/circuit/experiments"
        exp.mkdir(parents=True)
        write_json(exp / "probe.json", {"items": [{"id": "R1"}], "nets": {"/IN": ["R1.1"]}})
        (exp / "design.xml").write_text("<export><components><comp ref='R1'/></components></export>")
        (exp / "bad.xml").write_text("<export>")
        (exp / "empty.txt").write_text("")
        (exp / "binary").write_bytes(b"\x00\xff")
        (exp / "pdf").write_bytes(b"%PDF-1.4\n")
        bad = [
            ("probe.json", {"pointer": "/no_such_field"}),
            ("probe.json", {"pointer": "/items/R1"}),  # RFC 6901 arrays use indices, never IDs.
            ("probe.json", {"pointer": "/items/01"}),
            ("probe.json", {"pointer": "/items/-"}),
            ("probe.json", {"pointer": "/items/1"}),
            ("probe.json", {"pointer": "/items/-1"}),
            ("probe.json", {"pointer": "/items/0/id/missing"}),
            ("probe.json", {"pointer": "items/0"}),
            ("probe.json", {"pointer": "/nets/~2IN"}),
            ("probe.json", {"pointer": None}),
            ("probe.json", {"pointer": 0}),
            ("probe.json", {"pointer": "/items/0", "lines": [1, 9999]}),
            ("design.xml", {"pointer": "/components/R999"}),
            ("design.xml", {"xpath": "/export/components/comp[@ref='R999']"}),
            ("design.xml", {"xpath": "/wrong/components"}),
            ("design.xml", {"xpath": ".//comp["}),
            ("design.xml", {"xpath": ""}),
            ("design.xml", {"xpath": None}),
            ("bad.xml", {"xpath": "."}),
            ("empty.txt", {"lines": [1, 1]}),
            ("binary", {"lines": [1, 1]}),
            ("pdf", {"lines": [1, 1]}),
        ]
        bad += [("probe.json", {"lines": lines}) for lines in
                (None, [], [1], [1, 2, 3], [True, 1], [1, False], [0, 1], [2, 1], [1.0, 2], "1-2")]
        for name, fields in bad:
            cite = {"path": "review-work/circuit/experiments/" + name, "locator": "R1 pin; shared tokens", **fields}
            with self.subTest(cite=cite), self.assertRaises(ValueError):
                validate_citation(cite, self.run_dir)
        for description, pointer in (
            ("R999 pin 7 on net /NO_SUCH_NET", "/nets/~1NO_SUCH_NET"),
            ("U77 has no decoupling capacitor", "/items/U77"),
        ):
            with self.subTest(description=description), self.assertRaisesRegex(ValueError, "does not resolve"):
                validate_citation({"path": "review-work/circuit/experiments/probe.json",
                                   "locator": description, "pointer": pointer}, self.run_dir)
        check = {**self.rc_check, "evidence": [dict(path="checks/rc_transient.json", locator="outcome", pointer="/missing")]}
        with self.assertRaisesRegex(ValueError, "does not resolve"):
            validate_check_result(check, self.run_dir)

    def test_version_two_review_and_decisions_require_structured_citations(self):
        cite = {"path": "checks/rc_transient.json", "locator": "Response metric", "pointer": "/details/v_1ms"}
        review = {**self._review(findings=[self._finding(evidence=[cite])]), "schema": "agent-ee.review/2"}
        def validate():
            return validate_review(review, self.candidate_id, self.run_id, self.packet_sha256, self.run_dir)
        normalized = validate()
        self.assertEqual(normalized["schema"], "agent-ee.review/2")
        self.assertEqual(normalized["findings"][0]["evidence"], [cite])
        findings = collect_findings([normalized], [])
        decision = {"schema": "agent-ee.decisions/2", "candidate_id": self.candidate_id,
                    "run_id": self.run_id, "decisions": [{"finding_id": findings[0]["id"],
                    "disposition": "accepted", "reason": "Measured requirement violation", "evidence": [cite]}]}
        self.assertEqual(adjudicate(findings, decision, self.run_dir)[0]["status"], "accepted")
        del cite["pointer"]
        with self.assertRaisesRegex(ValueError, "requires pointer, lines or xpath"):
            validate()
        with self.assertRaisesRegex(ValueError, "requires pointer, lines or xpath"):
            adjudicate(findings, decision, self.run_dir)
        cite["pointer"] = "/no_such_field"
        for version in ("1", "2"):
            review["schema"] = "agent-ee.review/" + version
            decision["schema"] = "agent-ee.decisions/" + version
            with self.assertRaisesRegex(ValueError, "does not resolve"):
                validate()
            with self.assertRaisesRegex(ValueError, "does not resolve"):
                adjudicate(findings, decision, self.run_dir)

    def test_citation_schema_stays_strict_through_codex_adapter(self):
        from agent_ee.harnesses import strict_schema
        from agent_ee.reviewers import REVIEW_SCHEMA, DECISIONS_SCHEMA, EVIDENCE_SCHEMA
        combinations_seen = set()
        for outer, key in ((REVIEW_SCHEMA, "findings"), (DECISIONS_SCHEMA, "decisions")):
            actual = strict_schema(outer)["properties"][key]["items"]["properties"]["evidence"]
            self.assertEqual(actual, EVIDENCE_SCHEMA)
            for branch in actual["items"]["anyOf"]:
                self.assertEqual(set(branch["required"]), set(branch["properties"]))
                self.assertFalse(branch["additionalProperties"])
                self.assertTrue({"path", "locator"} <= set(branch["required"]))
                fields = frozenset(branch["required"]) - {"path", "locator"}
                self.assertTrue(fields)
                combinations_seen.add(fields)
                # No omitted optional location becomes a required null placeholder.
                for prop in branch["properties"].values():
                    self.assertNotIn("anyOf", prop)
        self.assertEqual(combinations_seen, {
            frozenset(("pointer",)), frozenset(("lines",)), frozenset(("xpath",)),
            frozenset(("pointer", "lines")), frozenset(("pointer", "xpath")),
            frozenset(("lines", "xpath")), frozenset(("pointer", "lines", "xpath"))})

    def test_fresh_reviewer_and_adjudicator_citation_refusals_are_resubmittable(self):
        import copy
        import time
        from unittest.mock import patch
        from agent_ee.reviewers import initial_reviews, adjudicator_call, EVIDENCE_SCHEMA

        citation_schema = EVIDENCE_SCHEMA["items"]
        self.assertEqual(len(citation_schema["anyOf"]), 7)
        for branch in citation_schema["anyOf"]:
            self.assertFalse(branch["additionalProperties"])
            self.assertEqual(set(branch["required"]), set(branch["properties"]))
            self.assertTrue(set(branch["required"]) & {"pointer", "lines", "xpath"})
        packet = {"candidate_id": self.candidate_id, "run_id": self.run_id,
                  "packet_sha256": self.packet_sha256}
        counts = {}
        def reviewer(run, role, definition, prompt, schema, timeout, **kwargs):
            counts[role] = counts.get(role, 0) + 1
            self.assertIn("Every evidence citation MUST include", prompt)
            self.assertEqual(schema["properties"]["schema"]["enum"], ["agent-ee.review/2"])
            self.assertNotEqual(schema["properties"]["findings"]["items"]["properties"]["evidence"], EVIDENCE_SCHEMA)
            citation = {"path": "checks/rc_transient.json", "locator": "response"}
            if counts[role] == 2:
                self.assertIn("frozen producer policy requires locator kind(s) lines, pointer", prompt)
                citation["pointer"] = "/details/v_1ms"
            review = {**self._review(role=role, findings=[self._finding(evidence=[citation])]),
                      "schema": "agent-ee.review/2"}
            return {"execution": "ok"}, review
        with patch("agent_ee.reviewers.reviewer_call", side_effect=reviewer):
            reviews, records = initial_reviews(self.run_dir, packet, time.monotonic() + 30,
                                              launch_context={"max_schema_resubmissions": 1})
        self.assertEqual(len(reviews), 2)
        self.assertEqual([r["execution"] for r in records], ["ok", "ok"])
        self.assertTrue(all(r["resubmissions_used"] == 1 for r in records))
        findings = collect_findings(reviews, [])
        def adjudicator(run, role, definition, prompt, schema, timeout, **kwargs):
            counts[role] = counts.get(role, 0) + 1
            self.assertIn("Every evidence citation MUST include", prompt)
            self.assertEqual(schema["properties"]["schema"]["enum"], ["agent-ee.decisions/2"])
            self.assertNotEqual(schema["properties"]["decisions"]["items"]["properties"]["evidence"], EVIDENCE_SCHEMA)
            citation = {"path": "checks/rc_transient.json", "locator": "response"}
            if counts[role] == 2:
                self.assertIn("frozen producer policy requires locator kind(s) lines, pointer", prompt)
                citation["pointer"] = "/details/v_1ms"
            return {"execution": "ok"}, {"schema": "agent-ee.decisions/2", "candidate_id": self.candidate_id,
                "run_id": self.run_id, "decisions": [{"finding_id": f["id"], "disposition": "accepted",
                "reason": "Measured violation", "evidence": [copy.deepcopy(citation)]} for f in findings]}
        with patch("agent_ee.reviewers.reviewer_call", side_effect=adjudicator):
            record, decisions = adjudicator_call(self.run_dir, packet, findings, reviews,
                time.monotonic() + 30, launch_context={"max_schema_resubmissions": 1})
        self.assertEqual(record["execution"], "ok")
        self.assertEqual(record["resubmissions_used"], 1)
        self.assertEqual(len(decisions["decisions"]), len(findings))

    def test_fresh_model_calls_cannot_return_legacy_schema(self):
        import time
        from unittest.mock import patch
        from agent_ee.reviewers import initial_reviews, adjudicator_call
        packet = {"candidate_id": self.candidate_id, "run_id": self.run_id,
                  "packet_sha256": self.packet_sha256}
        def reviewer(run, role, *args, **kwargs):
            return {"execution": "ok"}, self._review(role=role, findings=[])
        with patch("agent_ee.reviewers.reviewer_call", side_effect=reviewer):
            reviews, records = initial_reviews(self.run_dir, packet, time.monotonic() + 30)
        self.assertEqual(reviews, [])
        self.assertTrue(all(r["execution"] == "malformed" and "fresh review requires" in r["error"] for r in records))
        with patch("agent_ee.reviewers.reviewer_call", return_value=({"execution": "ok"}, {
                "schema": "agent-ee.decisions/1", "candidate_id": self.candidate_id,
                "run_id": self.run_id, "decisions": []})):
            record, decisions = adjudicator_call(self.run_dir, packet, [], [], time.monotonic() + 30)
        self.assertEqual(record["execution"], "malformed")
        self.assertIn("fresh adjudication requires", record["error"])
        self.assertIsNone(decisions)

    def test_reviewer_nonexistent_target_probes_refused_on_retained_connectivity(self):
        run = Path(__file__).resolve().parents[1] / "evidence/adjudicator-comparison/agy/singleton"
        for locator, pointer in (
            ("R999 pin 7 on net /NO_SUCH_NET", "/components/R999"),
            ("R999 pin 7 on net /NO_SUCH_NET", "/nets/~1NO_SUCH_NET"),
            ("U77 has no decoupling capacitor", "/components/U77"),
        ):
            with self.subTest(pointer=pointer), self.assertRaisesRegex(ValueError, "does not resolve"):
                validate_citation({"path": "work/netlist/connectivity.json", "locator": locator,
                                   "pointer": pointer}, run)
        citation = {"path": "work/netlist/connectivity.json", "locator": "IN membership", "pointer": "/nets/~1IN/0"}
        self.assertEqual(validate_citation(citation, run), citation)

    def test_legacy_retained_locators_are_descriptive(self):
        repo = Path(__file__).resolve().parents[1]
        examples = [
            ("evidence/adjudicator-comparison/agy/singleton", "work/netlist/connectivity.json",
             "nets./IN (J1.1, R1.1) and nets./OUT (C1.1, J1.2, R1.2)"),
            ("evidence/adjudicator-comparison/codex/calculation", "work/netlist/design.xml",
             "/export/components/comp[@ref='R1']/value; /export/nets/net[@name='/IN'] and net[@name='/OUT']: R1.1 and R1.2 respectively"),
            ("evidence/model-baselines-expanded/claude", "work/netlist/connectivity.json",
             "/12V_KEY (J1.45,J2.90), /5V_SENSOR_1 (J2.40,J2.41), unconnected-(A1-~{FLT}-Pad2)"),
            ("evidence/model-baselines-expanded/claude", "work/drc/drc.json",
             "padstack/annular_width error items 'PTH pad [<no net>] of J1'"),
            ("evidence/donor-verified", "work/netlist/connectivity.json",
             "R36/R37 dnp:true; '/CAN2+':[C29.2,C33.2,D10.1,D11.1,J1.15,J2.73,R36.1,R37.1,U20.7,U21.7]"),
            ("evidence/donor-verified", "work/erc/erc.json",
             "by_type lib_symbol_mismatch=89, lib_symbol_issues=14, footprint_link_issues=8; e.g. 'The current configuration does not include the footprint library Simon' for J1 uuid 6971898b"),
            ("evidence/donor-final", "work/drc/drc.json",
             "violations: 106x type=lib_footprint_mismatch (e.g. C25 uuid 0070999b, R35 uuid 1905dec1); 9x type=lib_footprint_issues (e.g. P1 uuid 1646febd, J1 uuid 93ba26da, J4 uuid a7443703)"),
        ]
        for run, path, locator in examples:
            citation = {"path": path, "locator": locator}
            with self.subTest(locator=locator):
                self.assertEqual(validate_citation(citation, repo / run), citation)
        # Compatibility does not reinterpret or verify a legacy prose claim.
        self.assertEqual(validate_citation({"path": "checks/rc_transient.json", "locator": "/no_such_field"},
                                          self.run_dir)["locator"], "/no_such_field")


if __name__ == "__main__":
    unittest.main()

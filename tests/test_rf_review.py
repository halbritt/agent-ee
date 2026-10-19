"""Dedicated RF reviewer: applicability, exact binding, completion gates and closure.

Owned minimal fixtures and scripted subscription replies exercise the real
coordinator, sandboxed check and review validation. No provider is called.
"""
import contextlib
import copy
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import rf_review
from agent_ee.cli import outcome, run_review, verify
from agent_ee.profiles import validate_profile
from agent_ee.reviewers import LENSES
from agent_ee.storage import digest, read_json, write_json

try:
    from toolchain import toolchain_test
except ImportError:  # module-qualified invocation from the repository root
    from tests.toolchain import toolchain_test

ROOT = Path(__file__).resolve().parents[1]

RF_BOARD = '''(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(setup
\t\t(stackup
\t\t\t(layer "F.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 1" (type "core") (thickness 0.2104) (epsilon_r 4.4))
\t\t\t(layer "In1.Cu" (type "copper") (thickness 0.0152))
\t\t)
\t)
\t(footprint "Connector_Coaxial:SMA_Owned_Test"
\t\t(layer "F.Cu")
\t\t(at 10 10)
\t\t(property "Reference" "J1")
\t\t(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net "/ANT_RF"))
\t\t(pad "2" smd rect (at 2 0) (size 1 1) (layers "F.Cu") (net "/GND"))
\t)
\t(footprint "RF_GPS:Owned_GNSS"
\t\t(layer "F.Cu")
\t\t(at 30 10)
\t\t(property "Reference" "U1")
\t\t(pad "11" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net "/ANT_RF"))
\t)
\t(segment (start 10 10) (end 30 10) (width 0.335) (layer "F.Cu") (net "/ANT_RF"))
\t(zone
\t\t(net "/GND")
\t\t(layers "In1.Cu")
\t\t(filled_polygon (layer "In1.Cu") (pts (xy 0 0) (xy 40 0) (xy 40 20)))
\t)
)
'''
PLAIN_BOARD = '''(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(footprint "Resistor_SMD:R_0603_1608Metric"
\t\t(layer "F.Cu")
\t\t(at 10 10)
\t\t(property "Reference" "R1")
\t\t(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net "/VIN"))
\t)
\t(footprint "Diode_SMD:D_SMA"
\t\t(layer "F.Cu")
\t\t(at 20 10)
\t\t(property "Reference" "D1")
\t\t(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net "/VIN"))
\t)
)
'''
RF_SCHEMATIC = '(kicad_sch (version 20250114) (symbol (lib_id "RF_GPS:Owned_GNSS")) (global_label "ANT_RF"))\n'
PLAIN_SCHEMATIC = '(kicad_sch (version 20250114) (symbol (lib_id "Device:R")) (global_label "VIN"))\n'
CONSTRAINTS = {"schema": "agent-ee.net-constraints/1", "classes": [{"name": "RF_50", "requirement": "RF-W",
               "nets": ["/ANT_RF"], "track_width_mm": 0.335, "clearance_mm": 0.2, "impedance_ohm": 50}]}


def rf_profile(roles=("circuit", "physical", "rf")):
    definition = {"harness": "codex", "model": "test", "effort": "high", "timeout_s": 10}
    return {"schema": "agent-ee.profile/1", "name": "rf-test",
            "reviewers": [{**definition, "role": r} for r in roles], "adjudicator": definition}


def files_reader(mapping):
    data = {k: v.encode() if isinstance(v, str) else v for k, v in mapping.items()}
    import hashlib
    return {k: hashlib.sha256(v).hexdigest() for k, v in data.items()}, data.__getitem__


def decide(mapping, **scope):
    files, read = files_reader(mapping)
    return rf_review.decide({"schematic": "design/board.kicad_sch", "board": "design/board.kicad_pcb", **scope},
                            files, read)


class ApplicabilityTests(unittest.TestCase):
    def rf_inputs(self):
        return {"design/board.kicad_sch": RF_SCHEMATIC, "design/board.kicad_pcb": RF_BOARD,
                "design/rf-net-constraints.json": json.dumps(CONSTRAINTS)}

    def test_rf_design_requires_review_with_bound_sources(self):
        decision = decide(self.rf_inputs())
        self.assertTrue(decision["required"])
        kinds = {i["kind"] for i in decision["indicators"]}
        self.assertTrue({"rf_constraint_class", "schematic_rf_part", "board_rf_footprint"} <= kinds)
        self.assertEqual(decision["rf_nets"], ["/ANT_RF"])
        self.assertEqual(decision["board"]["rf_copper"]["/ANT_RF"]["widths_mm"], ["0.335"])
        self.assertEqual([z["filled"] for z in decision["board"]["zones"]], [True])
        self.assertEqual(decision["impedance_declarations"][0]["status"], "declared_input_not_qualification")
        self.assertTrue(decision["expects"]["gnss"] and decision["expects"]["controlled_impedance"])
        locations = {loc["item"]: loc["lines"] for loc in decision["board"]["locations"]}
        self.assertEqual(locations["footprint J1"], [11, 17])
        self.assertEqual(locations["setup/stackup"], [4, 10])
        self.assertEqual({e["path"] for e in decision["sources"]["rf_constraints"]},
                         {"inputs/design/rf-net-constraints.json"})
        self.assertIn("inputs/design/board.kicad_pcb", {e["path"] for e in decision["sources"]["planes"]})

    def test_non_rf_design_records_explicit_not_applicable_basis(self):
        decision = decide({"design/board.kicad_sch": PLAIN_SCHEMATIC, "design/board.kicad_pcb": PLAIN_BOARD})
        self.assertFalse(decision["applicable"])
        self.assertFalse(decision["required"])
        self.assertEqual(decision["indicators"], [])  # D_SMA is a diode package, not an RF connector
        self.assertIn("RF review not applicable", decision["basis"][-1])
        self.assertIsNone(rf_review.gate({"rf_review": decision, "expected_review_roles": ["circuit"]}))

    def test_declaration_cannot_waive_detected_rf(self):
        decision = decide(self.rf_inputs(), rf_review={"applicable": False, "basis": "owner says digital only"})
        self.assertTrue(decision["required"])
        self.assertTrue(any("contradicted" in b for b in decision["basis"]))
        declared = decide({"design/board.kicad_sch": PLAIN_SCHEMATIC, "design/board.kicad_pcb": PLAIN_BOARD},
                          rf_review={"applicable": True, "basis": "433 MHz loop added later"})
        self.assertTrue(declared["required"])
        with self.assertRaisesRegex(ValueError, "nonempty basis"):
            decide(self.rf_inputs(), rf_review={"applicable": False, "basis": " "})
        with self.assertRaisesRegex(ValueError, "not captured"):
            decide(self.rf_inputs(), rf_review={"applicable": True, "basis": "x",
                                                "sources": {"datasheets": ["missing.pdf"]}})

    def test_rf_affecting_change_invalidates_and_unrelated_change_does_not(self):
        before = decide(self.rf_inputs())
        changed = self.rf_inputs()
        changed["design/board.kicad_pcb"] = RF_BOARD.replace("(width 0.335)", "(width 0.3)")
        after = decide(changed)
        self.assertNotEqual(before["binding_sha256"], after["binding_sha256"])
        record = rf_review.invalidation({"run_id": "old", "rf_review": before}, after)
        self.assertEqual(record["prior_rf_review"], "invalidated")
        self.assertIn("board", record["changed_categories"])
        stackup = self.rf_inputs()
        stackup["design/rf-net-constraints.json"] = json.dumps({**CONSTRAINTS, "classes": [
            {**CONSTRAINTS["classes"][0], "clearance_mm": 0.25}]})
        self.assertEqual(rf_review.changed_categories(before, decide(stackup)), ["rf_constraints"])
        unrelated = {**self.rf_inputs(), "design/notes.txt": "unrelated prose"}
        self.assertEqual(rf_review.invalidation({"rf_review": before}, decide(unrelated))["prior_rf_review"],
                         "sources_unchanged")


# Original fixture in the shape of the captured pod RF source (AGENTEE-186): the RF
# stackup statement is F.Cu over In1.Cu, repeated by two workflow records.
RF_CAPTURE = {"status": "calculation_captured_impedance_unverified",
              "continuation": {"known_inputs": {"signal_layer": "F.Cu", "reference_layer": "In1.Cu GND"}},
              "w2_continuation": {"source_stackup": {"signal_layer": "F.Cu", "reference_layer": "In1.Cu"}}}
POLICY = {"signal_layers": ["F.Cu", "B.Cu"], "pour_area_loss_pct": 5,
          "reference": {"net": "/GND", "layers": ["In1.Cu"], "rf_nets": ["/ANT_RF"]}}


def grade(continuity="pass"):
    items = {"disallowed_tracks": {"status": "pass"}, "pour_integrity": {"status": "pass"},
             "area_loss": {"status": "pass"}, "reference_continuity": {"status": continuity}}
    return {"status": "defect" if continuity == "defect" else "unknown" if continuity == "unknown" else "pass",
            "items": items}


class RoutingEvidenceTests(unittest.TestCase):
    """Candidate-bound routing adapter evidence (AGENTEE-231 semantics) in the RF binding.

    The receipt's own verification is the routing adapter's (tested natively in
    test_routing_campaign); here its verdict is stubbed so presence, absence,
    tampering and staleness of the bound bytes are exercised offline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="agent-ee-rf-routing-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inputs = {"design/board.kicad_sch": RF_SCHEMATIC, "design/board.kicad_pcb": RF_BOARD,
                       "design/rf-net-constraints.json": json.dumps(CONSTRAINTS),
                       "design/rf-source-capture.json": json.dumps(RF_CAPTURE)}
        self.files, _ = files_reader(self.inputs)
        self.manifest = {"scope": {"schematic": "design/board.kicad_sch", "board": "design/board.kicad_pcb",
                                   "checks": [{"id": "response"}]}, "files": self.files}
        self.receipt = self.root / "receipt/receipt.json"
        routing = self.receipt.parent / "run/routing"
        (routing / "attempt").mkdir(parents=True)
        from agent_ee.kicad_route_attempt import POLICY_FILE
        (routing / "attempt" / POLICY_FILE).write_text('{"schema": "agent-ee.reference-continuity/2"}\n')
        write_json(routing / "adapter-report.json", {"request": {"layer_policy": POLICY},
                   "retained": {"attempt/" + POLICY_FILE: digest(routing / "attempt" / POLICY_FILE)}})
        write_json(self.receipt, {"artifacts": {"routing/adapter-report.json": digest(routing / "adapter-report.json")}})
        self.summary = {"classification": "complete", "backend": "krt", "layer_policy": grade(),
                        "candidate_sha256": self.files["design/board.kicad_pcb"]}
        self.runs = 0

    def capture(self, context="receipt", summary=None, verifier=None):
        self.runs += 1
        run = self.root / f"run-{self.runs}"
        run.mkdir()
        if context == "receipt":
            context = {"receipt": {"path": str(self.receipt), "sha256": digest(self.receipt)}}
        stub = verifier or (lambda receipt_dir: self.summary if summary is None else summary)
        with patch("agent_ee.routing_adapter.verify_campaign_receipt", side_effect=stub) as verify_receipt:
            record = rf_review.capture_routing(run, self.manifest, context)
        self.verified = verify_receipt.call_args_list
        files, read = files_reader(self.inputs)
        return run, record, rf_review.decide(self.manifest["scope"], files, read, rf_review.retained_routing(run))

    def test_registered_kinds_only(self):
        from agent_ee.checks import CHECK_FUNCS
        self.assertLessEqual(set(rf_review.RF_EVIDENCE_KINDS) | set(rf_review.RF_EXPECTED_KINDS), set(CHECK_FUNCS))
        evidence = rf_review.native_evidence([{"id": "nca", "kind": "net_class_assignment", "execution": "ok",
                                               "outcome": "pass"}])
        self.assertEqual(evidence["absent_kinds"], ["routing_rules", "geometry"])
        self.assertNotIn("route_layer_policy", json.dumps(evidence))
        self.assertEqual(evidence["routing_adapter"]["status"], "missing_evidence")

    def test_present_verified_evidence_is_bound_by_sha256(self):
        run, record, decision = self.capture()
        self.assertEqual(record["status"], "verified", record["reason"])
        self.assertEqual([c.args for c in self.verified], [(self.receipt.parent,)])
        routing = decision["routing_adapter"]
        self.assertEqual(routing["record_sha256"], digest(run / "work/rf-routing/evidence.json"))
        self.assertEqual(routing["adapter_report"], {"path": "work/rf-routing/adapter-report.json",
            "sha256": digest(self.receipt.parent / "run/routing/adapter-report.json")})
        self.assertEqual(routing["reference_continuity"]["sha256"],
                         digest(run / "work/rf-routing/reference-continuity.json"))
        self.assertEqual(routing["layer_policy"]["items"]["reference_continuity"]["status"], "pass")
        self.assertEqual(routing["declared_layer_policy"], POLICY)
        self.assertFalse(any(m.startswith("routing adapter") for m in decision["missing"]))
        packet_view = rf_review.native_evidence([], decision)["routing_adapter"]
        self.assertEqual(packet_view["record_sha256"], routing["record_sha256"])
        _, _, absent = self.capture(context=None)
        self.assertNotEqual(absent["binding_sha256"], decision["binding_sha256"])
        self.assertIn("routing_adapter", rf_review.changed_categories(absent, decision))

    def test_absent_evidence_is_missing_never_pass(self):
        for context, reason in ((None, "no routing adapter receipt was supplied"),
                                ({"receipt": None}, "no adopted receipt")):
            with self.subTest(context=context):
                _, record, decision = self.capture(context=context)
                self.assertEqual(record["status"], "missing_evidence")
                self.assertIn(reason, record["reason"])
                self.assertEqual(decision["routing_adapter"]["status"], "missing_evidence")
                self.assertTrue(any("routing adapter evidence missing_evidence" in m for m in decision["missing"]))
                self.assertEqual({r["status"] for r in decision["rf_layer_requirements"]}, {"unknown"})
        _, record, decision = self.capture(verifier=lambda receipt_dir: None)
        self.assertEqual((record["status"], record["reason"]), ("missing_evidence", "the routing attempt left no adapter report"))
        # A run that never retained a record (pre-243 or removed) is missing too.
        files, read = files_reader(self.inputs)
        legacy = rf_review.decide(self.manifest["scope"], files, read, rf_review.retained_routing(self.root))
        self.assertEqual(legacy["routing_adapter"]["status"], "missing_evidence")

    def test_tampered_evidence_is_invalid(self):
        report = self.receipt.parent / "run/routing/adapter-report.json"
        from agent_ee.kicad_route_attempt import POLICY_FILE
        continuity = self.receipt.parent / "run/routing/attempt" / POLICY_FILE
        cases = {
            "receipt_after_anchor": lambda: self.receipt.write_text(self.receipt.read_text() + " "),
            "adapter_report": lambda: report.write_text(report.read_text() + " "),
            "continuity_file": lambda: continuity.write_text("{}"),
        }
        for name, tamper in cases.items():
            with self.subTest(name=name):
                originals = {p: p.read_bytes() for p in (self.receipt, report, continuity)}
                context = {"receipt": {"path": str(self.receipt), "sha256": digest(self.receipt)}}
                tamper()
                _, record, decision = self.capture(context=context)
                self.assertEqual(record["status"], "invalid")
                # An anchor mismatch stops before the receipt is even verified.
                self.assertEqual(len(self.verified), 0 if name == "receipt_after_anchor" else 1)
                self.assertEqual(decision["routing_adapter"]["status"], "invalid")
                self.assertTrue(any("routing adapter evidence invalid" in m for m in decision["missing"]))
                for path, data in originals.items():
                    path.write_bytes(data)
        _, record, _ = self.capture(verifier=lambda receipt_dir: (_ for _ in ()).throw(
            ValueError("routing adapter report identity mismatch")))
        self.assertEqual(record["status"], "invalid")
        self.assertIn("identity mismatch", record["reason"])

    def test_retained_copy_tampered_after_capture_changes_the_binding(self):
        run, _, decision = self.capture()
        for rel in ("adapter-report.json", "reference-continuity.json", "evidence.json"):
            with self.subTest(rel=rel):
                path = run / "work/rf-routing" / rel
                original = path.read_bytes()
                path.write_bytes(original.replace(b"{", b"{ ", 1))
                files, read = files_reader(self.inputs)
                after = rf_review.decide(self.manifest["scope"], files, read, rf_review.retained_routing(run))
                self.assertNotEqual(after["binding_sha256"], decision["binding_sha256"])
                if rel != "evidence.json":
                    self.assertEqual(after["routing_adapter"]["status"], "invalid")
                path.write_bytes(original)

    def test_evidence_of_another_board_is_stale(self):
        _, record, decision = self.capture(summary={**self.summary, "candidate_sha256": "0" * 64})
        self.assertEqual(record["status"], "stale")
        self.assertEqual(decision["routing_adapter"]["status"], "stale")
        self.assertEqual({r["status"] for r in decision["rf_layer_requirements"]}, {"unknown"})
        # A verified record replayed against a changed board is not evidence of it.
        run, _, _ = self.capture()
        changed = {**self.inputs, "design/board.kicad_pcb": RF_BOARD.replace("(width 0.335)", "(width 0.3)")}
        files, read = files_reader(changed)
        moved = rf_review.decide(self.manifest["scope"], files, read, rf_review.retained_routing(run))
        self.assertEqual(moved["routing_adapter"]["status"], "invalid")

    def test_reserved_evidence_directory_cannot_be_a_check(self):
        run = self.root / "reserved"
        run.mkdir()
        manifest = {**self.manifest, "scope": {**self.manifest["scope"], "checks": [{"id": "rf-routing"}]}}
        with self.assertRaisesRegex(ValueError, "reserved"):
            rf_review.capture_routing(run, manifest, None)

    def test_per_net_layer_requirement_is_never_satisfied_by_global_policy(self):
        _, _, decision = self.capture()
        [requirement] = decision["rf_layer_requirements"]
        self.assertEqual((requirement["signal_layer"], requirement["reference_layer"]), ("F.Cu", "In1.Cu"))
        self.assertEqual(requirement["source"], "inputs/design/rf-source-capture.json")
        self.assertEqual(requirement["locators"], ["/continuation/known_inputs", "/w2_continuation/source_stackup"])
        self.assertEqual(requirement["nets"], ["/ANT_RF"])
        # Global F.Cu/B.Cu permission and a reference continuity pass do not enforce F.Cu-only routing.
        self.assertEqual(requirement["status"], "unknown")
        self.assertEqual(requirement["related_evidence"]["global_signal_layers"], ["F.Cu", "B.Cu"])
        self.assertEqual(requirement["related_evidence"]["reference_continuity"], "pass")
        self.assertEqual(requirement["related_evidence"]["board_rf_copper_layers"], {"/ANT_RF": ["F.Cu"]})
        self.assertTrue(any("RF layer requirement F.Cu over In1.Cu" in m and "unknown" in m
                            for m in decision["missing"]))
        _, _, defect = self.capture(summary={**self.summary, "layer_policy": grade("defect")})
        self.assertEqual(defect["rf_layer_requirements"][0]["status"], "defect")
        _, _, unknown = self.capture(summary={**self.summary, "layer_policy": grade("unknown")})
        self.assertEqual(unknown["rf_layer_requirements"][0]["status"], "unknown")
        self.assertTrue(any("reference_continuity: unknown" in m for m in unknown["missing"]))
        _, _, absent = self.capture(context=None)
        self.assertIsNone(absent["rf_layer_requirements"][0]["related_evidence"]["global_signal_layers"])

    def test_assessment_cannot_support_unenforced_or_absent_evidence(self):
        (self.root / "inputs/design").mkdir(parents=True)
        (self.root / "inputs/design/board.kicad_pcb").write_text(RF_BOARD)
        cite = [{"path": "inputs/design/board.kicad_pcb", "locator": "J1", "lines": [11, 17]}]

        def assessment(decision, supported):
            topics = [{"topic": t, "status": "supported" if t == supported else "missing_evidence",
                       "summary": "s", "evidence": cite if t == supported else [],
                       "missing_evidence": [] if t == supported else ["as-built field solution"]}
                      for t in rf_review.TOPICS]
            return {"binding_sha256": decision["binding_sha256"], "topics": topics, "qualification_claimed": False,
                    "external_qualification": [{"method": m, "reason": "r", "scope": "s"}
                                               for m in ("field_solver", "installed_gnss_measurement")]}

        for name, context, summary, topic, error in (
                ("absent", None, None, "ground_reference_continuity_return_paths", "verified routing reference"),
                ("unknown", "receipt", {**self.summary, "layer_policy": grade("unknown")},
                 "ground_reference_continuity_return_paths", "verified routing reference"),
                ("layer", "receipt", None, "width_gap_layer_transitions_launches", "unenforced")):
            with self.subTest(name=name):
                _, _, decision = self.capture(context=context, summary=summary)
                write_json(self.root / "packet.json", {"rf_review": decision})
                with self.assertRaisesRegex(ValueError, error):
                    rf_review.validate_assessment(assessment(decision, topic), [], self.root,
                                                  require_structured=False)
        _, _, decision = self.capture()
        write_json(self.root / "packet.json", {"rf_review": decision})
        rf_review.validate_assessment(assessment(decision, "ground_reference_continuity_return_paths"), [],
                                      self.root, require_structured=False)


class PerNetLayerEvidenceTests(unittest.TestCase):
    """AGENTEE-259: the requirement's own layers graded on the verified receipt's native facts.

    As in RoutingEvidenceTests only the receipt verifier's verdict is stubbed; the
    native routed-board path is test_rf_layer_wiring."""
    setUp = RoutingEvidenceTests.setUp

    def capture(self, **kwargs):
        run, record, _ = RoutingEvidenceTests.capture(self, **kwargs)
        files, read = files_reader(self.inputs)
        routing = rf_review.retained_routing(run)
        return run, record, rf_review.decide(self.manifest["scope"], files, read, routing,
                                             rf_review.retained_evidence(run, routing))

    def evidence(self, tracks=None, vias=(), measured=None, policy=POLICY, facts_sha=None):
        from agent_ee.kicad_route_attempt import POLICY_FILE
        sha = self.files["design/board.kicad_pcb"]
        self.track = {"uuid": "t1", "kind": "segment", "net": "/ANT_RF", "layer": "F.Cu", "width": 335000,
                      "start": [10000000, 10000000], "end": [30000000, 10000000]}
        tracks = [self.track] if tracks is None else tracks
        facts = {"sha256": facts_sha or sha, "tracks": tracks, "vias": list(vias),
                 "copper_layers": [{"name": n} for n in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")]}
        if measured is None:
            measured = {"schema": "agent-ee.reference-continuity/2", "board_sha256": sha, "problems": [], "vias": [],
                        "tracks": [{"uuid": t["uuid"], "net": t["net"], "layer": t["layer"],
                                    "reference_layer": "In1.Cu", "status": "continuous"} for t in tracks]}
        routing = self.receipt.parent / "run/routing"
        (routing / "attempt/validate").mkdir(parents=True, exist_ok=True)
        write_json(routing / "attempt" / rf_review.FACTS_FILE, facts)
        write_json(routing / "attempt" / POLICY_FILE, measured)
        write_json(routing / "adapter-report.json", {"request": {"layer_policy": policy}, "retained": {
            "attempt/" + POLICY_FILE: digest(routing / "attempt" / POLICY_FILE),
            "attempt/" + rf_review.FACTS_FILE: digest(routing / "attempt" / rf_review.FACTS_FILE)}})
        write_json(self.receipt, {"artifacts": {"routing/adapter-report.json": digest(routing / "adapter-report.json")}})

    def requirement(self, **capture):
        run, record, decision = self.capture(**capture)
        [requirement] = decision["rf_layer_requirements"]
        return requirement, decision, run

    def test_verified_facts_and_continuity_satisfy_the_requirement(self):
        self.evidence()
        requirement, decision, run = self.requirement()
        self.assertEqual(requirement["status"], "pass", requirement)
        grade = requirement["related_evidence"]["per_net_signal_layers"]
        self.assertEqual((grade["declared"], grade["board_sha256"]),
                         ({"/ANT_RF": ["F.Cu"]}, self.files["design/board.kicad_pcb"]))
        self.assertEqual(decision["routing_adapter"]["native_facts"],
                         {"path": "work/rf-routing/native-facts.json",
                          "sha256": digest(run / "work/rf-routing/native-facts.json")})
        self.assertFalse(any("RF layer requirement" in m for m in decision["missing"]))
        self.assertIn("not impedance qualification", requirement["reason"])

    def test_wrong_layer_and_via_are_defects_on_verified_facts(self):
        via = {"uuid": "v1", "net": "/ANT_RF", "layers": ["F.Cu", "B.Cu"], "via_type": "through",
               "position": [20000000, 10000000], "diameter": 600000, "drill": 300000}
        for name, change in (("back", {"tracks": [{"layer": "B.Cu"}]}), ("via", {"vias": [via]})):
            with self.subTest(name=name):
                self.evidence()
                tracks = [dict(self.track, **change["tracks"][0])] if "tracks" in change else None
                self.evidence(tracks=tracks, vias=change.get("vias", ()))
                requirement, decision, _ = self.requirement()
                self.assertEqual(requirement["status"], "defect", requirement)
                self.assertTrue(any("RF layer requirement" in m and "defect" in m for m in decision["missing"]))
        # A global allowlist covering B.Cu and a passing continuity grade do not excuse it.
        self.assertEqual(requirement["related_evidence"]["reference_continuity"], "pass")

    def test_incomplete_or_unbound_evidence_stays_unknown(self):
        sha = self.files["design/board.kicad_pcb"]
        other = dict(POLICY, reference=dict(POLICY["reference"], layers=["In2.Cu"]))
        cases = {
            "facts_of_another_board": dict(facts_sha="0" * 64),
            "untracked_segment": dict(measured={"schema": "agent-ee.reference-continuity/2", "board_sha256": sha,
                                                "problems": [], "vias": [], "tracks": []}),
            "measurement_of_another_board": dict(measured={"schema": "agent-ee.reference-continuity/2",
                "board_sha256": "0" * 64, "problems": [], "vias": [], "tracks": [{"uuid": "t1", "net": "/ANT_RF",
                "layer": "F.Cu", "reference_layer": "In1.Cu", "status": "continuous"}]}),
            "reference_on_another_layer": dict(policy=other),
            "reference_not_ground": dict(policy=dict(POLICY, reference=dict(POLICY["reference"], net="/VCC"))),
            "reference_other_named_net": dict(policy=dict(POLICY, reference=dict(POLICY["reference"], net="/AGND"))),
            "no_reference_declared": dict(policy={k: v for k, v in POLICY.items() if k != "reference"}),
            "no_routed_copper": dict(tracks=[]),
        }
        for name, change in cases.items():
            with self.subTest(name=name):
                self.evidence(**change)
                requirement, decision, _ = self.requirement()
                self.assertEqual(requirement["status"], "unknown", requirement)
                self.assertTrue(any("RF layer requirement" in m and "unknown" in m for m in decision["missing"]))
        self.evidence()
        for name, capture in (("continuity_unknown", dict(summary={**self.summary, "layer_policy": grade("unknown")})),
                              ("stale", dict(summary={**self.summary, "candidate_sha256": "0" * 64})),
                              ("absent", dict(context=None))):
            with self.subTest(name=name):
                self.assertEqual(self.requirement(**capture)[0]["status"], "unknown")
        self.assertEqual(self.requirement(summary={**self.summary, "layer_policy": grade("defect")})[0]["status"],
                         "defect")

    def test_tampered_facts_are_invalid_and_change_the_binding(self):
        self.evidence()
        facts = self.receipt.parent / "run/routing/attempt" / rf_review.FACTS_FILE
        original = facts.read_bytes()
        facts.write_bytes(original.replace(b'"F.Cu"', b'"B.Cu"', 1))
        _, record, decision = self.capture()
        self.assertEqual(record["status"], "invalid")
        self.assertIn("native board facts differ", record["reason"])
        self.assertEqual(decision["rf_layer_requirements"][0]["status"], "unknown")
        facts.write_bytes(original)
        run, _, decision = self.capture()
        copied = run / "work/rf-routing/native-facts.json"
        copied.write_bytes(copied.read_bytes().replace(b'"F.Cu"', b'"B.Cu"', 1))
        files, read = files_reader(self.inputs)
        routing = rf_review.retained_routing(run)
        after = rf_review.decide(self.manifest["scope"], files, read, routing, rf_review.retained_evidence(run, routing))
        self.assertEqual(after["routing_adapter"]["status"], "invalid")
        self.assertEqual(after["rf_layer_requirements"][0]["status"], "unknown")
        self.assertNotEqual(after["binding_sha256"], decision["binding_sha256"])

    def test_layer_topic_is_supported_only_on_a_passing_requirement(self):
        (self.root / "inputs/design").mkdir(parents=True)
        (self.root / "inputs/design/board.kicad_pcb").write_text(RF_BOARD)
        cite = [{"path": "inputs/design/board.kicad_pcb", "locator": "RF segment", "lines": [21, 21]}]

        def assessment(decision):
            topics = [{"topic": t, "status": "supported" if t == "width_gap_layer_transitions_launches"
                       else "missing_evidence", "summary": "s",
                       "evidence": cite if t == "width_gap_layer_transitions_launches" else [],
                       "missing_evidence": [] if t == "width_gap_layer_transitions_launches" else ["bench"]}
                      for t in rf_review.TOPICS]
            return {"binding_sha256": decision["binding_sha256"], "topics": topics, "qualification_claimed": False,
                    "external_qualification": [{"method": m, "reason": "r", "scope": "s"}
                                               for m in ("field_solver", "installed_gnss_measurement")]}
        for tracks, ok in ((None, True), ([{"uuid": "t1", "kind": "segment", "net": "/ANT_RF", "layer": "B.Cu",
                             "width": 335000, "start": [0, 0], "end": [1000000, 0]}], False)):
            self.evidence(tracks=tracks)
            run, _, decision = self.capture()
            write_json(self.root / "packet.json", {"rf_review": decision})
            if ok:
                rf_review.validate_assessment(assessment(decision), [], self.root, require_structured=False)
            else:
                with self.assertRaisesRegex(ValueError, "unenforced"):
                    rf_review.validate_assessment(assessment(decision), [], self.root, require_structured=False)


class RoleTests(unittest.TestCase):
    def test_rf_role_is_a_dedicated_validated_profile_role(self):
        validate_profile(rf_profile())
        self.assertIn("rf", LENSES)
        for phrase in ("GNSS receiver", "SMA", "antenna keepout", "coplanar", "stitching", "NEVER RF qualification",
                       "VNA or TDR", "installed BLE/GNSS", "Never invent"):
            self.assertIn(phrase, LENSES["rf"])
        pod = read_json(ROOT / "profiles/pod-review.json")
        validate_profile(pod)
        self.assertIn("rf", [r["role"] for r in pod["reviewers"]])

    def test_pod_plan_requires_rf_reviewer(self):
        from agent_ee import pod_driver
        plan = {"review_profile": read_json(ROOT / "profiles/claude-baseline.json")}
        with self.assertRaisesRegex(ValueError, "dedicated rf reviewer"):
            # validate_plan checks the profile after its own structural checks; call the profile stage only.
            pod_driver._require_rf_reviewer(plan)
        pod_driver._require_rf_reviewer({"review_profile": read_json(ROOT / "profiles/pod-review.json")})

    def test_gate_reasons(self):
        decision = {"required": True, "binding_sha256": "a"}
        base = {"rf_review": decision, "expected_review_roles": ["circuit", "physical", "rf"]}
        self.assertIn("declares no rf reviewer",
                      rf_review.gate({**base, "expected_review_roles": ["circuit", "physical"]}))
        self.assertIn("no execution record", rf_review.gate(base))
        failed = {**base, "review_executions": [{"role": "rf", "execution": "failed", "error": "quota"}]}
        self.assertIn("failed: quota", rf_review.gate(failed))
        ok = {**base, "review_executions": [{"role": "rf", "execution": "ok"}]}
        self.assertIn("malformed", rf_review.gate(ok))
        stale = {**ok, "reviews": [{"role": "rf", "rf_assessment": {"binding_sha256": "b"}}]}
        self.assertIn("stale", rf_review.gate(stale))
        fresh = {**ok, "reviews": [{"role": "rf", "rf_assessment": {"binding_sha256": "a"}}]}
        self.assertIsNone(rf_review.gate(fresh))

    def test_rf_finding_closure_requires_fresh_review_after_rf_change(self):
        finding = {"id": "rf/launch", "role": "rf"}
        old = {"rf_review": {"required": True, "category_sha256": {"board": "1"}}}
        fresh = {"rf_review": {"required": True, "binding_sha256": "n", "category_sha256": {"board": "2"}},
                 "expected_review_roles": ["rf"], "review_executions": [{"role": "rf", "execution": "ok"}],
                 "reviews": [{"role": "rf", "rf_assessment": {"binding_sha256": "n"}}]}
        rf_review.closure_gate(finding, old, fresh)
        with self.assertRaisesRegex(ValueError, "RF-affecting"):
            rf_review.closure_gate(finding, old, {**fresh, "rf_review": {**fresh["rf_review"],
                                                                         "category_sha256": {"board": "1"}}})
        with self.assertRaisesRegex(ValueError, "fresh complete RF review"):
            rf_review.closure_gate(finding, old, {**fresh, "reviews": []})
        rf_review.closure_gate({"id": "check/drc", "role": "check"}, old, {})


class RunIntegrationTests(unittest.TestCase):
    """The real coordinator with a sandboxed measurement check and scripted subscription replies."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="agent-ee-rf-review-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.write_design(RF_SCHEMATIC, RF_BOARD, CONSTRAINTS)
        for name in ("requirements", "targets", "reference", "model"):
            (self.source / name).write_text(name)
        write_json(self.source / "design.json", {"value": 5})
        (self.source / "solver.py").write_text('from pathlib import Path\nimport json\nvalue=json.loads(Path("/candidate/design/design.json").read_text())["value"]\nPath("raw.json").write_text(json.dumps({"value":value}))\n')
        (self.source / "adapter.py").write_text('''import json, sys
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text())
value = json.loads(Path("/work/tool/raw.json").read_text())["value"]
report = {"schema": "agent-ee.measurements/1", "candidate_id": request["candidate_id"],
 "files": request["files"], "tool": "test-protocol", "version": "1", "domain": "electrical",
 "artifacts": ["tool/raw.json"], "metrics": {"output": {"value": value, "unit": "V", "condition": "load"}}}
Path(sys.argv[2]).write_text(json.dumps(report))
''')
        self.calls, self.prompts, self.schemas = [], {}, {}
        self.rf_reply = None
        self.addCleanup(patch.stopall)
        patch('agent_ee.cli.runtime_files', return_value={name: {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
              for name in ('codex', 'codex_tool_host', 'bwrap', '/usr/bin/python3')}).start()

    def write_design(self, schematic, board, constraints=None):
        (self.source / "board.kicad_sch").write_text(schematic)
        (self.source / "board.kicad_pcb").write_text(board)
        if constraints is None:
            (self.source / "rf-net-constraints.json").unlink(missing_ok=True)
        else:
            write_json(self.source / "rf-net-constraints.json", constraints)

    def scope(self, profile):
        scope = {"schema": "agent-ee.scope/2", "name": "rf", "scope": "RF review integration test",
            "board": "design/board.kicad_pcb", "schematic": "design/board.kicad_sch",
            "mutable_inputs": ["design/board.kicad_pcb"],
            "inputs": [{"path": str(self.source), "at": "design"}], "review_profile": profile,
            "checks": [{"id": "response", "kind": "measurements", "params": {
                "adapter": "design/adapter.py", "tool": {"argv": ["/usr/bin/python3", "/candidate/design/solver.py"],
                "sha256": digest("/usr/bin/python3"), "domain": "electrical", "inputs": ["design/solver.py"]},
                "calibration_inputs": ["design/model"],
                "limits": {"output": {"unit": "V", "condition": "load", "min": 0, "max": 10}}}}],
            "baseline": {"requirements": [{"id": "R1", "statement": "Output in [0,10] V at load",
                "source": {"path": "design/requirements", "locator": "R1"}, "checks": ["response"]}],
                "targets": ["design/targets"], "reference_architectures": ["design/reference"]}}
        path = self.root / f"scope-{len(list(self.root.glob('scope-*')))}.json"
        write_json(path, scope)
        return path

    def assessment(self, packet, **overrides):
        topics = [{"topic": t, "status": "missing_evidence", "summary": "not established from captured sources",
                   "evidence": [], "missing_evidence": ["as-built field solution and coupon TDR"]}
                  for t in rf_review.TOPICS]
        topics[0] = {"topic": rf_review.TOPICS[0], "status": "supported",
                     "summary": "J1 pad 1 and U1 pad 11 share /ANT_RF with one 0.335 mm F.Cu segment",
                     "evidence": [{"path": "inputs/design/board.kicad_pcb", "locator": "J1 footprint",
                                   "lines": [11, 17]}], "missing_evidence": []}
        value = {"binding_sha256": packet["rf_review"]["binding_sha256"], "topics": topics,
                 "qualification_claimed": False,
                 "external_qualification": [
                     {"method": "field_solver", "reason": "declared 50 ohm is a calculator input", "scope": "/ANT_RF"},
                     {"method": "tdr", "reason": "fabricated impedance", "scope": "coupon"},
                     {"method": "installed_gnss_measurement", "reason": "installed C/N0", "scope": "pod enclosure"}]}
        value.update(overrides)
        return value

    def agent(self, run, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
        run = Path(run)
        self.calls.append(label)
        self.prompts[label], self.schemas[label] = prompt, schema
        packet = read_json(run / "packet.json")
        identity = {k: packet[k] for k in ("candidate_id", "run_id")}
        if label == "adjudicator":
            findings = read_json(run / "result.json")["findings"]
            return {"execution": "ok"}, {"schema": "agent-ee.decisions/2", **identity, "decisions": [
                {"finding_id": f["id"], "disposition": "deferred", "reason": "RF concern needs bench evidence",
                 "evidence": [{"path": "inputs/design/board.kicad_pcb", "locator": "J1", "lines": [11, 17]}]}
                for f in findings]}
        review = {"schema": "agent-ee.review/2", **identity, "packet_sha256": packet["packet_sha256"],
                  "role": label, "coverage": ["scripted"], "limitations": ["scripted test response"], "findings": []}
        if label == "rf":
            if self.rf_reply == "failed":
                return {"execution": "failed", "error": "subscription quota exhausted; no fallback"}, None
            review["rf_assessment"] = self.assessment(packet)
            if callable(self.rf_reply):
                self.rf_reply(review)
        return {"execution": "ok"}, review

    def run_case(self, profile, name, **kwargs):
        with patch('agent_ee.reviewers.dispatch', side_effect=self.agent), contextlib.redirect_stdout(io.StringIO()):
            run_review(self.scope(profile), self.root / name, 60, **kwargs)
        return read_json(self.root / name / "result.json")

    @toolchain_test
    def test_complete_rf_review_dispatches_bound_brief_and_completes(self):
        result = self.run_case(rf_profile(), "run")
        self.assertEqual(result["outcome"], "criteria_satisfied", result.get("error"))
        self.assertEqual(sorted(self.calls), ["adjudicator", "circuit", "physical", "rf"])
        binding = result["rf_review"]["binding_sha256"]
        self.assertIn("dedicated RF reviewer", self.prompts["rf"])
        self.assertIn(binding, self.prompts["rf"])
        self.assertNotIn("dedicated RF reviewer", self.prompts["circuit"])
        self.assertEqual(self.schemas["rf"]["properties"]["rf_assessment"]["properties"]["binding_sha256"]["enum"],
                         [binding])
        self.assertNotIn("rf_assessment", self.schemas["circuit"]["properties"])
        self.assertIn("RF reviewer findings are adjudicated", self.prompts["adjudicator"])
        packet = read_json(self.root / "run/packet.json")
        self.assertEqual(packet["rf_review"], result["rf_review"])
        self.assertIn("net_class_assignment", packet["rf_native_evidence"]["absent_kinds"])
        verified = verify(self.root / "run")
        self.assertEqual(verified["reviews"][2]["rf_assessment"]["binding_sha256"], binding)
        self.assertIn("RF review required", (self.root / "run/REPORT.md").read_text())
        # A forged decision cannot waive the RF review after the fact.
        forged = read_json(self.root / "run/result.json")
        forged["rf_review"]["required"] = False
        write_json(self.root / "run/result.json", forged)
        with self.assertRaisesRegex(ValueError, "RF review decision differs"):
            verify(self.root / "run")
        del forged["rf_review"]
        write_json(self.root / "run/result.json", forged)
        with self.assertRaisesRegex(ValueError, "RF review decision differs"):
            verify(self.root / "run")

    @toolchain_test
    def test_bound_routing_evidence_reaches_packet_and_verify_detects_tamper(self):
        from agent_ee.kicad_route_attempt import POLICY_FILE
        receipt = self.root / "receipt/receipt.json"
        routing = receipt.parent / "run/routing"
        (routing / "attempt").mkdir(parents=True)
        (routing / "attempt" / POLICY_FILE).write_text("{}\n")
        write_json(routing / "adapter-report.json", {"request": {"layer_policy": POLICY},
                   "retained": {"attempt/" + POLICY_FILE: digest(routing / "attempt" / POLICY_FILE)}})
        write_json(receipt, {"artifacts": {"routing/adapter-report.json": digest(routing / "adapter-report.json")}})
        summary = {"classification": "complete", "backend": "krt", "layer_policy": grade(),
                   "candidate_sha256": digest(self.source / "board.kicad_pcb")}
        with patch("agent_ee.routing_adapter.verify_campaign_receipt", return_value=summary):
            result = self.run_case(rf_profile(), "run", routing_context={
                "receipt": {"path": str(receipt), "sha256": digest(receipt)}})
        self.assertEqual(result["outcome"], "criteria_satisfied", result.get("error"))
        run = self.root / "run"
        bound = result["rf_review"]["routing_adapter"]
        self.assertEqual(bound["status"], "verified")
        self.assertEqual(bound["record_sha256"], digest(run / "work/rf-routing/evidence.json"))
        packet = read_json(run / "packet.json")
        self.assertEqual(packet["rf_native_evidence"]["routing_adapter"]["record_sha256"], bound["record_sha256"])
        self.assertEqual(packet["rf_native_evidence"]["routing_adapter"]["adapter_report"], bound["adapter_report"])
        self.assertIn(bound["record_sha256"], self.prompts["rf"])
        verify(run)
        # The retained copy is review evidence: the packet seal or the RF decision refuses its change.
        (run / "work/rf-routing/adapter-report.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "evidence changed|RF review decision differs"):
            verify(run)

    @toolchain_test
    def test_missing_rf_role_blocks_completion_without_dispatch(self):
        result = self.run_case(rf_profile(("circuit", "physical")), "run")
        self.assertEqual((result["state"], result["outcome"]), ("incomplete", "incomplete"))
        self.assertEqual(self.calls, [])
        self.assertTrue(any("declares no rf reviewer" in l for l in result["limitations"]))
        verify(self.root / "run")

    @toolchain_test
    def test_failed_rf_review_blocks_completion_without_fallback(self):
        self.rf_reply = "failed"
        result = self.run_case(rf_profile(), "run")
        self.assertEqual(result["outcome"], "incomplete")
        self.assertNotIn("adjudicator", self.calls)
        self.assertEqual(self.calls.count("rf"), 1)
        rf = next(r for r in result["review_executions"] if r["role"] == "rf")
        self.assertEqual(rf["execution"], "failed")

    @toolchain_test
    def test_malformed_rf_assessments_block_completion(self):
        cases = {
            "qualification": lambda r: r["rf_assessment"].update(qualification_claimed=True),
            "topic": lambda r: r["rf_assessment"]["topics"].pop(),
            "unsupported": lambda r: r["rf_assessment"]["topics"][0].update(evidence=[]),
            "external": lambda r: r["rf_assessment"].update(external_qualification=[
                {"method": "field_solver", "reason": "impedance", "scope": "/ANT_RF"}]),
            "stale": lambda r: r["rf_assessment"].update(binding_sha256="0" * 64),
            "uncited": lambda r: r["findings"].append({"id": "launch", "title": "SMA launch", "location": "J1",
                "condition": "edge launch", "mechanism": "ground pad discontinuity", "severity": "major",
                "assessment": "concern", "evidence": [], "reasoning": "no citation", "witnesses": []})}
        for name, mutate in cases.items():
            with self.subTest(name=name):
                self.calls.clear()
                self.rf_reply = mutate
                result = self.run_case(rf_profile(), "run-" + name)
                self.assertEqual(result["outcome"], "incomplete")
                self.assertNotIn("rf", [r["role"] for r in result["reviews"]])
                self.assertNotIn("adjudicator", self.calls)

    @toolchain_test
    def test_rf_finding_flows_into_adjudication(self):
        def concern(review):
            review["findings"].append({"id": "launch", "title": "SMA ground launch unverified",
                "location": "J1 pads 1-2, F.Cu", "condition": "edge-mount launch", "severity": "major",
                "mechanism": "return path discontinuity at the coplanar launch", "assessment": "concern",
                "evidence": [{"path": "inputs/design/board.kicad_pcb", "locator": "J1", "lines": [11, 17]}],
                "reasoning": "no field solution of the launch", "witnesses": []})
        self.rf_reply = concern
        result = self.run_case(rf_profile(), "run")
        finding = next(f for f in result["findings"] if f["id"] == "rf/launch")
        self.assertEqual(finding["role"], "rf")
        self.assertEqual(finding["disposition"], "deferred")
        self.assertEqual(result["outcome"], "defects_or_unresolved")
        verify(self.root / "run")

    @toolchain_test
    def test_non_rf_design_completes_with_explicit_not_applicable(self):
        self.write_design(PLAIN_SCHEMATIC, PLAIN_BOARD)
        result = self.run_case(rf_profile(("circuit", "physical")), "run")
        self.assertEqual(result["outcome"], "criteria_satisfied", result.get("error"))
        self.assertFalse(result["rf_review"]["applicable"])
        self.assertIn("RF review not applicable", (self.root / "run/REPORT.md").read_text())

    @toolchain_test
    def test_campaign_run_without_rf_reviewer_stays_incomplete_without_dispatch(self):
        from agent_ee.campaign import run_campaign
        profile = rf_profile(("circuit", "physical"))
        scope = self.scope(profile)
        write_json(self.root / "campaign.json", {"schema": "agent-ee.campaign/1", "scope": scope.name,
            "review_profile": profile, "implementation": profile["adjudicator"], "max_iterations": 2,
            "max_seconds": 60})
        with patch('agent_ee.reviewers.dispatch', side_effect=self.agent), contextlib.redirect_stdout(io.StringIO()):
            run_campaign(self.root / "campaign.json", self.root / "campaign")
        self.assertEqual(self.calls, [])
        first = read_json(self.root / "campaign/runs/1/result.json")
        self.assertEqual(first["outcome"], "incomplete")
        self.assertTrue(first["rf_review"]["required"])
        self.assertTrue(any("RF review gate" in l for l in first["limitations"]))
        self.assertFalse((self.root / "campaign/runs/2").exists())

    @toolchain_test
    def test_targeted_rf_review_and_rf_change_invalidation(self):
        first = self.run_case(rf_profile(), "first", review_roles=["rf"])
        self.assertEqual(self.calls, ["rf"])
        self.assertEqual(first["outcome"], "incomplete")
        self.assertEqual([r["role"] for r in first["reviews"]], ["rf"])
        self.assertIsNone(rf_review.gate(first))
        verify(self.root / "first")
        self.write_design(RF_SCHEMATIC, RF_BOARD.replace("(width 0.335)", "(width 0.3)"), CONSTRAINTS)
        self.calls.clear()
        second = self.run_case(rf_profile(), "second", previous=str(self.root / "first"))
        self.assertEqual(second["rf_invalidation"]["prior_rf_review"], "invalidated")
        self.assertIn("board", second["rf_invalidation"]["changed_categories"])
        self.assertIn("rf", self.calls)
        self.assertNotEqual(second["rf_review"]["binding_sha256"], first["rf_review"]["binding_sha256"])
        # The earlier review is stale for the changed candidate.
        stale = copy.deepcopy(second)
        stale["reviews"] = [r if r["role"] != "rf" else first["reviews"][0] for r in second["reviews"]]
        self.assertIn("stale", rf_review.gate(stale))
        self.assertEqual(outcome(stale), "incomplete")


if __name__ == "__main__":
    unittest.main()

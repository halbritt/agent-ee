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

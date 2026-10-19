"""AGENTEE-259: per-net RF layer requirements graded on a natively routed board, end to end.

The pinned kicad-route/KRT routes an original four-layer probe (GND planes on
In1/In2, RF net SIGNAL) under a declared layer and reference policy; its stage
receipt is the routing evidence an RF review run captures and verify replays.
Only the subscription reviewers are scripted. The captured requirement is the
pod's shape: SIGNAL on F.Cu over the In1.Cu GND reference.
"""
import json
import shutil
import unittest
from unittest.mock import patch

from agent_ee import rf_review
from agent_ee.cli import verify
from agent_ee.storage import digest, write_json
from tests import test_per_net_layers as per_net
from tests import test_rf_review as rf
from tests import test_route_layer_policy as native

CAPTURE = {"source_stackup": {"signal_layer": "F.Cu", "reference_layer": "In1.Cu GND"}}
CONSTRAINTS = {"schema": "agent-ee.net-constraints/1", "classes": [{"name": "RF_50", "requirement": "RF-W",
               "nets": ["SIGNAL"], "track_width_mm": 0.25, "clearance_mm": 0.2, "impedance_ohm": 50}]}
REFERENCE = {"net": "GND", "layers": ["In1.Cu"], "rf_nets": ["SIGNAL"]}
# The router is told only the global allowlist here, so it delivers what the requirement forbids.
BACK = {"signal_layers": ["F.Cu", "B.Cu"], "layer_costs": [1000, 1], "pour_area_loss_pct": 0.5,
        "reference": REFERENCE}
VIA = {"signal_layers": ["F.Cu", "B.Cu"], "pour_area_loss_pct": 5}
NO_REFERENCE = {k: v for k, v in per_net.POD.items() if k != "reference"}
VOID = {"signal_layers": ["F.Cu"], "pour_area_loss_pct": 0.5,
        "per_net_signal_layers": {"SIGNAL": ["F.Cu"]}, "reference": REFERENCE}
LAYER_TOPIC = "width_gap_layer_transitions_launches"


class RfLayerWiringNativeTest(native.PlaneFixture, unittest.TestCase):
    scope = rf.RunIntegrationTests.scope
    agent = rf.RunIntegrationTests.agent
    run_case = rf.RunIntegrationTests.run_case
    assessment = rf.RunIntegrationTests.assessment
    route = native.RouteLayerPolicyNativeTest.route

    def setUp(self):
        super().setUp()
        self.checkout = native.require_kicad_route()
        common = dict(clearance_mm=.25, min_width_mm=.2, thermal=dict(connection='thermal', gap_mm=.3,
                      spoke_width_mm=.3), priority=0, island_removal='always', net='GND', outline='board')
        self.plan = dict(schema=native.planes.SCHEMA,
                         zones=[dict(common, layer='In1.Cu'), dict(common, layer='In2.Cu')])
        self.save()
        self.calls, self.prompts, self.schemas, self.rf_reply = [], {}, {}, self.support_layer_topic
        self.addCleanup(patch.stopall)
        patch('agent_ee.cli.runtime_files', return_value={name: {'path': '/usr/bin/true',
              'sha256': digest('/usr/bin/true')} for name in ('codex', 'codex_tool_host', 'bwrap',
                                                               '/usr/bin/python3')}).start()
        self.reviews = 0

    def support_layer_topic(self, review):
        # Accepted only while every RF layer requirement passes; never gap, stitching or qualification.
        topic = next(t for t in review["rf_assessment"]["topics"] if t["topic"] == LAYER_TOPIC)
        topic.update(status="supported", missing_evidence=[],
                     summary="all SIGNAL copper is on F.Cu over In1.Cu GND per the bound native facts",
                     evidence=[{"path": "inputs/design/board.kicad_pcb", "locator": "board header",
                                "lines": [1, 2]}])

    def review(self, board, receipt):
        """An RF review run of `board` that captures the routing receipt as its evidence."""
        self.reviews += 1
        self.source = self.root / f"rf-design-{self.reviews}"
        self.source.mkdir()
        shutil.copyfile(board, self.source / "board.kicad_pcb")
        shutil.copyfile(self.root / "candidate/project/pod.kicad_sch", self.source / "board.kicad_sch")
        write_json(self.source / "rf-net-constraints.json", CONSTRAINTS)
        write_json(self.source / "rf-source-capture.json", CAPTURE)
        for name in ("requirements", "targets", "reference", "model"):
            (self.source / name).write_text(name)
        write_json(self.source / "design.json", {"value": 5})
        (self.source / "solver.py").write_text(
            'from pathlib import Path\nimport json\nvalue=json.loads(Path("/candidate/design/design.json")'
            '.read_text())["value"]\nPath("raw.json").write_text(json.dumps({"value":value}))\n')
        (self.source / "adapter.py").write_text('''import json, sys
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text())
value = json.loads(Path("/work/tool/raw.json").read_text())["value"]
report = {"schema": "agent-ee.measurements/1", "candidate_id": request["candidate_id"],
 "files": request["files"], "tool": "test-protocol", "version": "1", "domain": "electrical",
 "artifacts": ["tool/raw.json"], "metrics": {"output": {"value": value, "unit": "V", "condition": "load"}}}
Path(sys.argv[2]).write_text(json.dumps(report))
''')
        name = f"review-{self.reviews}"
        context = {"receipt": {"path": str(receipt), "sha256": digest(receipt)}}
        result = self.run_case(rf.rf_profile(), name, routing_context=context)
        [requirement] = result["rf_review"]["rf_layer_requirements"]
        return result, requirement, self.root / name

    def routed(self, y, policy, label, mode='pth'):
        receipt, summary, report, work = self.route(y, policy, label, mode)
        return summary, work, self.root / "route-receipt/receipt.json"

    def assert_layer_topic_refused(self, result):
        rf_exec = next(r for r in result["review_executions"] if r["role"] == "rf")
        self.assertEqual(result["outcome"], "incomplete")
        self.assertNotIn("rf", [r["role"] for r in result["reviews"]])
        self.assertIn("unenforced", json.dumps(rf_exec))

    def test_compliant_route_satisfies_the_requirement_and_replays(self):
        summary, work, receipt = self.routed(35, per_net.POD, 'rf-clean')
        self.assertEqual(summary['classification'], 'complete', summary)
        board = work / 'routing/candidate/project/pod.kicad_pcb'
        result, requirement, run = self.review(board, receipt)
        self.assertEqual(result["outcome"], "criteria_satisfied", result.get("error"))
        routing = result["rf_review"]["routing_adapter"]
        self.assertEqual(routing["status"], "verified", routing["reason"])
        self.assertEqual(routing["native_facts"], {"path": "work/rf-routing/native-facts.json",
                                                   "sha256": digest(run / "work/rf-routing/native-facts.json")})
        self.assertEqual(routing["native_facts"]["sha256"],
                         digest(work / "routing/attempt" / rf_review.FACTS_FILE))
        self.assertEqual(requirement["status"], "pass", requirement)
        self.assertEqual((requirement["signal_layer"], requirement["reference_layer"], requirement["nets"]),
                         ("F.Cu", "In1.Cu", ["SIGNAL"]))
        grade = requirement["related_evidence"]["per_net_signal_layers"]
        self.assertEqual(grade["board_sha256"], digest(board))
        self.assertEqual(grade["declared"], {"SIGNAL": ["F.Cu"]})
        self.assertEqual(requirement["related_evidence"]["reference_continuity"], "pass")
        self.assertEqual(requirement["related_evidence"]["router_per_net_signal_layers"]["status"], "pass")
        self.assertFalse(any("RF layer requirement" in m for m in result["rf_review"]["missing"]))
        self.assertIn(LAYER_TOPIC, [t["topic"] for r in result["reviews"] if r["role"] == "rf"
                                    for t in r["rf_assessment"]["topics"] if t["status"] == "supported"])
        verify(run)
        # The retained facts are review evidence: an edited copy fails replay.
        facts = run / "work/rf-routing/native-facts.json"
        original = facts.read_bytes()
        facts.write_bytes(original.replace(b'"F.Cu"', b'"B.Cu"'))
        with self.assertRaisesRegex(ValueError, "evidence changed|RF review decision differs"):
            verify(run)
        facts.write_bytes(original)
        verify(run)

        # Stale: the same verified receipt is not evidence for another board (the unrouted input).
        result, requirement, _ = self.review(self.root / "candidate/project/pod.kicad_pcb", receipt)
        self.assertEqual(result["rf_review"]["routing_adapter"]["status"], "stale")
        self.assertEqual(requirement["status"], "unknown")
        self.assert_layer_topic_refused(result)

        # Tampered: facts edited in the receipt after routing fail the adapter's own verification.
        retained = work / "routing/attempt" / rf_review.FACTS_FILE
        retained.write_bytes(retained.read_bytes().replace(b'"F.Cu"', b'"B.Cu"'))
        result, requirement, _ = self.review(board, receipt)
        self.assertEqual(result["rf_review"]["routing_adapter"]["status"], "invalid")
        self.assertEqual(requirement["status"], "unknown")
        self.assert_layer_topic_refused(result)

    def test_back_layer_segment_is_a_defect_of_the_reviewed_board(self):
        summary, work, receipt = self.routed(35, BACK, 'rf-back')
        self.assertEqual(summary['classification'], 'complete', summary)
        board = work / 'routing/candidate/project/pod.kicad_pcb'
        result, requirement, run = self.review(board, receipt)
        self.assertEqual(result["rf_review"]["routing_adapter"]["status"], "verified")
        self.assertEqual(requirement["status"], "defect", requirement)
        grade = requirement["related_evidence"]["per_net_signal_layers"]
        self.assertTrue(any(v["kind"] == "track" and v["disallowed_layers"] == ["B.Cu"]
                            for v in grade["violations"]), grade)
        # The global allowlist the router enforced passed; it is not per-net evidence.
        self.assertEqual(summary['layer_policy']['status'], 'pass')
        self.assertTrue(any("RF layer requirement F.Cu over In1.Cu" in m and "defect" in m
                            for m in result["rf_review"]["missing"]))
        self.assert_layer_topic_refused(result)
        verify(run)

    def test_via_to_another_layer_is_a_defect(self):
        summary, work, receipt = self.routed(35, VIA, 'rf-via', 'layer_change')
        self.assertEqual(summary['classification'], 'complete', summary)
        board = work / 'routing/candidate/project/pod.kicad_pcb'
        result, requirement, run = self.review(board, receipt)
        self.assertEqual(requirement["status"], "defect", requirement)
        vias = [v for v in requirement["related_evidence"]["per_net_signal_layers"]["violations"]
                if v["kind"] == "via"]
        self.assertTrue(vias)
        self.assertEqual({tuple(v["disallowed_layers"]) for v in vias}, {("In1.Cu", "In2.Cu", "B.Cu")})
        self.assert_layer_topic_refused(result)
        verify(run)

    def test_undeclared_reference_leaves_the_requirement_unknown(self):
        summary, work, receipt = self.routed(35, NO_REFERENCE, 'rf-noref')
        self.assertEqual(summary['classification'], 'complete', summary)
        result, requirement, run = self.review(work / 'routing/candidate/project/pod.kicad_pcb', receipt)
        # The signal layers pass on verified facts; the reference beneath them is not evidenced.
        self.assertEqual(requirement["related_evidence"]["per_net_signal_layers"]["status"], "pass")
        self.assertEqual(requirement["status"], "unknown")
        self.assertIn("declares no ground reference", requirement["reason"])
        self.assert_layer_topic_refused(result)
        verify(run)

    def test_reference_void_under_the_route_is_never_satisfied(self):
        summary, work, receipt = self.routed(25, VOID, 'rf-void')
        self.assertEqual(summary['classification'], 'policy_failed', summary)
        self.assertEqual(summary['layer_policy']['items']['reference_continuity']['status'], 'defect')
        # The router withheld the board, so the review is of the unrouted candidate: stale, unknown.
        result, requirement, run = self.review(self.root / "candidate/project/pod.kicad_pcb", receipt)
        self.assertEqual(result["rf_review"]["routing_adapter"]["status"], "stale")
        self.assertEqual(requirement["status"], "unknown")
        self.assert_layer_topic_refused(result)
        verify(run)


if __name__ == "__main__":
    unittest.main()

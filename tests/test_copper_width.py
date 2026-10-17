"""Copper-width/1: real native arcs and explicit coverage boundaries."""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee import candidate, checks
from agent_ee.baseline import freeze_evidence, verify_evidence, validate_witnesses, compile_baseline
from agent_ee.storage import write_json

REPO = Path(__file__).resolve().parents[1]
PARAMS = {"criterion": "copper-width/1", "covered_primitives": ["PCB_TRACK", "PCB_ARC"],
          "min_track_width_mm": 0.2, "exclude_nets": []}


class NativeCopperWidthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_board(self, width, straight=True, excluded=False):
        source = self.root / "source"
        source.mkdir()
        shutil.copy(REPO / "fixtures/rc/control/rc_filter.kicad_pcb", source / "board.kicad_pcb")
        script = '''import pcbnew, sys
p, width, straight = sys.argv[1:]
b = pcbnew.LoadBoard(p)
netcode = next(pad.GetNetCode() for fp in b.GetFootprints() for pad in fp.Pads() if pad.GetNetname() == "/OUT")
if straight == "False":
    for t in list(b.GetTracks()): b.Remove(t)
if width != "None":
    a = pcbnew.PCB_ARC(b)
    a.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(10)))
    a.SetMid(pcbnew.VECTOR2I(pcbnew.FromMM(11), pcbnew.FromMM(11)))
    a.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(12), pcbnew.FromMM(10)))
    a.SetWidth(pcbnew.FromMM(float(width)))
    a.SetLayer(pcbnew.F_Cu)
    a.SetNetCode(netcode)
    b.Add(a)
pcbnew.SaveBoard(p, b)
'''
        generated = subprocess.run(
            ["/usr/bin/python3", "-c", script, str(source / "board.kicad_pcb"), str(width), str(straight)],
            capture_output=True, text=True)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        params = {**PARAMS, "exclude_nets": ["/OUT"] if excluded else []}
        (source / "requirements.md").write_text("R-width: straight tracks and arcs at least 0.20 mm")
        scope = {"schema": "agent-ee.scope/2", "inputs": [{"path": str(source), "at": "project"}],
                 "board": "project/board.kicad_pcb", "checks": [
                     {"id": "width", "kind": "geometry", "params": params},
                     {"id": "legacy", "kind": "geometry", "params": {"min_track_width_mm": 0.2}}]}
        scope["baseline"] = {"requirements": [{"id": "R-width", "statement": "Declared width coverage",
            "source": {"path": "project/requirements.md", "locator": "R-width"},
            "checks": ["width", "legacy"]}], "targets": ["project/requirements.md"],
            "reference_architectures": ["project/requirements.md"]}
        (self.root / "scope.json").write_text(json.dumps(scope))
        run = self.root / "run"
        run.mkdir()
        candidate.capture(self.root / "scope.json", run)
        return run, checks.run_checks(run, time.monotonic() + 120)

    def test_thin_arc_fails_while_five_straight_segments_keep_legacy_pass(self):
        run, (new, old) = self.run_board(0.05)
        self.assertEqual((new["execution"], new["outcome"]), ("ok", "defect"), new)
        self.assertEqual((old["execution"], old["outcome"]), ("ok", "pass"), old)
        self.assertEqual(old["details"]["track_segments"], 5)
        evidence = json.loads((run / "work/width/copper-width.json").read_text())
        self.assertEqual(evidence["candidate_id"], new["candidate_id"])
        self.assertEqual(evidence["counts"], {"eligible": 6, "checked": 6, "excluded": 0, "unsupported": 0})
        arc = next(p for p in evidence["primitives"] if p["type"] == "PCB_ARC")
        self.assertEqual((arc["width_mm"], arc["limit_mm"], arc["net"], arc["layer"]),
                         (0.05, 0.2, "/OUT", "F.Cu"))
        self.assertTrue(arc["uuid"])

    def test_arc_only_valid(self):
        _, (new, old) = self.run_board(0.2, straight=False)
        self.assertEqual((new["execution"], new["outcome"]), ("ok", "pass"), new)
        self.assertEqual(new["details"]["counts"]["checked"], 1)
        self.assertEqual(old["outcome"], "unknown")

    def test_arc_only_invalid(self):
        _, (new, _) = self.run_board(0.05, straight=False)
        self.assertEqual((new["execution"], new["outcome"]), ("ok", "defect"), new)

    def test_empty_eligible_geometry_is_incomplete(self):
        run, results = self.run_board(None, straight=False)
        new = results[0]
        self.assertEqual((new["execution"], new["outcome"]), ("unsupported", "unknown"), new)
        self.assertEqual(new["details"]["counts"]["eligible"], 0)
        freeze_evidence(run, results)
        with self.assertRaisesRegex(ValueError, "failed criterion"):
            validate_witnesses(run, [{"requirement_id": "R-width", "check_id": "width"}],
                               [{"path": "checks/width.json", "locator": "outcome"}])

    def test_excluded_arc_is_not_checked_and_empty_domain_stays_incomplete(self):
        _, (new, _) = self.run_board(0.05, straight=False, excluded=True)
        self.assertEqual((new["execution"], new["outcome"]), ("unsupported", "unknown"), new)
        self.assertEqual(new["details"]["counts"],
                         {"eligible": 0, "checked": 0, "excluded": 1, "unsupported": 0})
        self.assertEqual(new["details"]["primitives"][0]["width_mm"], 0.05)

    def test_width_evidence_is_sealed_and_candidate_bound(self):
        run, results = self.run_board(0.05)
        freeze_evidence(run, results)
        verify_evidence(run, results, required=True)
        witnesses = [{"requirement_id": "R-width", "check_id": "width"}]
        citations = [{"path": "checks/width.json", "locator": "details.offending_primitives"}]
        validate_witnesses(run, witnesses, citations)
        path = run / "work/width/copper-width.json"
        original = path.read_bytes()
        path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "tool evidence changed"):
            validate_witnesses(run, witnesses, citations)
        with self.assertRaisesRegex(ValueError, "seal contradicts"):
            verify_evidence(run, results, required=True)
        path.write_bytes(original)
        results[0]["candidate_id"] = "wrong-candidate"
        write_json(run / "checks/width.json", results[0])
        with self.assertRaisesRegex(ValueError, "different candidate"):
            verify_evidence(run, results, required=True)

    def test_broader_scope_changes_baseline_identity(self):
        run, _ = self.run_board(0.05)
        manifest = json.loads((run / "candidate.json").read_text())
        scope = manifest["scope"]
        broad = compile_baseline(scope, manifest["files"])
        scope["checks"][0]["params"] = {"min_track_width_mm": 0.2}
        narrow = compile_baseline(scope, manifest["files"])
        self.assertNotEqual(broad["baseline_id"], narrow["baseline_id"])


class CopperCoverageBoundaryTests(unittest.TestCase):
    def primitive(self, **overrides):
        return {"uuid": "c35995bb-2413-41cc-b343-0706bcdd99c3", "native_class": "PCB_ARC",
                "net": "signal", "layer": "F.Cu", "width_nm": 200000, **overrides}

    def coverage(self, items, **params):
        return checks.copper_width_coverage({"tracks": items, "zones": []}, {**PARAMS, **params})

    def test_unknown_class_is_unsupported_even_with_a_narrow_width(self):
        result = self.coverage([self.primitive(native_class="PCB_FUTURE", width_nm=50000)])
        self.assertEqual(result["counts"], {"eligible": 1, "checked": 0, "excluded": 0, "unsupported": 1})
        self.assertEqual(result["offending_primitives"], [])

    def test_malformed_primitives_never_become_proven_defects(self):
        for item in (None, {}, self.primitive(uuid=None), self.primitive(layer=None),
                     self.primitive(net=None), self.primitive(width_nm="50000"),
                     self.primitive(width_nm=-1), self.primitive(width_nm=True)):
            with self.subTest(item=item):
                result = self.coverage([item])
                self.assertEqual(result["counts"]["unsupported"], 1)
                self.assertEqual(result["offending_primitives"], [])

    def test_vias_and_zones_are_out_of_scope(self):
        result = self.coverage([self.primitive(native_class="PCB_VIA", width_nm=1)])
        self.assertEqual(result["counts"]["eligible"], 0)
        self.assertEqual(result["out_of_scope"], {"PCB_VIA": 1, "zones": 0})

    def test_missing_explicit_coverage_or_invalid_limits_refused(self):
        for params in ({"criterion": "copper-width/2"}, {"covered_primitives": ["PCB_TRACK"]},
                       {"exclude_nets": None}, {"min_track_width_mm": float("nan")},
                       {"min_track_width_mm": True}, {"min_track_width_mm": 0}):
            with self.subTest(params=params), self.assertRaises(checks.CheckError):
                self.coverage([self.primitive()], **params)

    def test_native_integer_precision_does_not_round_a_narrow_width_into_a_pass(self):
        result = self.coverage([self.primitive(width_nm=199999)])
        self.assertEqual(result["offending_primitives"][0]["width_mm"], 0.199999)


if __name__ == "__main__":
    unittest.main()

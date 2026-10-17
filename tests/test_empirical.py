import tempfile
import time
import unittest
from pathlib import Path

from agent_ee.checks import Ctx
from agent_ee.empirical import calibration_for
from agent_ee.measurements import finish
from agent_ee.storage import digest, write_json


class EmpiricalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        (self.run / "inputs").mkdir()
        (self.run / "inputs/model").write_text("qualified model")
        sample = {"check_id": "thermal", "metric": "peak", "value": 106, "uncertainty": 2,
                  "unit": "degC", "condition": "load"}
        write_json(self.run / "inputs/raw", {"samples": [sample]})
        write_json(self.run / "inputs/report", {"candidate_id": "prototype-candidate"})
        write_json(self.run / "inputs/source", {"candidate_id": "prototype-candidate", "checks": [
            {"id": "thermal", "execution": "ok", "details": {"metrics": {
                "peak": {"value": 95, "unit": "degC", "condition": "load"}}}}]})
        self.bundle = {"schema": "agent-ee.calibration/1", "candidate_id": "prototype-candidate",
            "model_inputs": {"model": digest(self.run / "inputs/model")},
            "artifacts": {p: digest(self.run / "inputs" / p) for p in ("raw", "report", "source")},
            "observations": [{"check_id": "thermal", "metric": "peak", "unit": "degC",
                "condition": "load", "residual_lower": 9, "residual_upper": 13, "prediction": 95,
                "value": 106, "uncertainty": 2, "artifact": "raw", "source_result": "source",
                "report": "report", "locator": "0", "candidate_id": "prototype-candidate"}]}
        write_json(self.run / "inputs/calibration.json", self.bundle)
        self.files = {p.name: digest(p) for p in (self.run / "inputs").iterdir()}
        self.scope = {"schema": "agent-ee.scope/1", "empirical": "calibration.json"}
        write_json(self.run / "candidate.json", {"scope": self.scope, "files": self.files})

    def test_bench_bias_changes_acceptance_and_generates_replay_evidence(self):
        ctx = Ctx(self.run, {"scope": self.scope, "files": self.files, "candidate_id": "new"},
                  {"id": "thermal", "kind": "measurements"}, time.monotonic() + 10)
        finish(ctx, {"peak": {"value": 95, "unit": "degC", "condition": "load"}},
               {"peak": {"unit": "degC", "condition": "load", "max": 100}})
        self.assertEqual(ctx.result["outcome"], "defect")
        self.assertEqual(ctx.result["details"]["metrics"]["peak"]["upper"], 108)
        self.assertEqual(ctx.result["details"]["margins"]["peak"]["max"], -8)
        self.assertIn("work/thermal/bench-replay.json", ctx.result["evidence"])

    def test_changed_model_or_missing_raw_evidence_invalidates_calibration(self):
        for key in ("model", "raw"):
            files = {**self.files, key: "different"}
            with self.assertRaises(ValueError):
                calibration_for(self.run, self.scope, files)

    def test_calibration_cannot_cross_operating_conditions(self):
        ctx = Ctx(self.run, {"scope": self.scope, "files": self.files, "candidate_id": "new"},
                  {"id": "thermal", "kind": "measurements"}, time.monotonic() + 10)
        with self.assertRaisesRegex(ValueError, "condition"):
            finish(ctx, {"peak": {"value": 95, "unit": "degC", "condition": "idle"}},
                   {"peak": {"unit": "degC", "condition": "idle", "max": 100}})

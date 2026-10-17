import tempfile
import time
import unittest
from pathlib import Path

from agent_ee.checks import Ctx, CheckError
from agent_ee.measurements import check_measurements, check_coupled_margins, evaluate_limits
from agent_ee.storage import digest, write_json

from toolchain import toolchain_test


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        (self.run / "inputs").mkdir()
        self.manifest = {"candidate_id": "candidate", "files": {}, "scope": {
            "schema": "agent-ee.scope/1", "checks": []}}
        write_json(self.run / "candidate.json", self.manifest)

    def ctx(self, spec):
        return Ctx(self.run, self.manifest, spec, time.monotonic() + 30)

    def test_limits_reject_nonfinite_values_and_mismatched_operating_points(self):
        limit = {"t": {"unit": "degC", "condition": "full-load", "max": 100}}
        metric = {"t": {"value": 101, "unit": "degC", "condition": "full-load"}}
        self.assertEqual(evaluate_limits(metric, limit), {"t": {"max": -1}})
        for invalid in (float("nan"), float("inf"), True, "99"):
            metric["t"]["value"] = invalid
            with self.assertRaises(ValueError):
                evaluate_limits(metric, limit)
        metric["t"].update(value=99, condition="idle")
        with self.assertRaisesRegex(ValueError, "condition"):
            evaluate_limits(metric, limit)

    def measurement_fixture(self):
        # A protocol fixture, not a claim of thermal solver qualification.
        adapter = self.run / "inputs/adapter.py"
        solver = self.run / "inputs/solver.py"
        solver.write_text('from pathlib import Path\nPath("raw.txt").write_text("101")\n')
        adapter.write_text('''import json, sys
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text())
value = float(Path("/work/tool/raw.txt").read_text())
report = {"schema": "agent-ee.measurements/1", "candidate_id": request["candidate_id"],
    "files": request["files"], "tool": "protocol-fixture", "version": "1", "domain": "thermal",
    "artifacts": ["tool/raw.txt"], "metrics": {"peak": {"value": value, "unit": "degC", "condition": "load"}}}
Path(sys.argv[2]).write_text(json.dumps(report))
''')
        self.manifest["files"] = {"adapter.py": digest(adapter), "solver.py": digest(solver)}
        write_json(self.run / "candidate.json", self.manifest)
        ctx = self.ctx({"id": "thermal", "kind": "measurements", "params": {"adapter": "adapter.py",
            "tool": {"argv": ["/usr/bin/python3", "/candidate/solver.py"], "sha256": digest("/usr/bin/python3"),
                     "domain": "thermal", "inputs": ["solver.py"]},
            "limits": {"peak": {"unit": "degC", "condition": "load", "max": 100}}}})
        return ctx

    @toolchain_test
    def test_real_sandbox_adapter_execution_retains_raw_evidence(self):
        ctx = self.measurement_fixture()
        check_measurements(ctx)
        self.assertEqual((ctx.result["execution"], ctx.result["outcome"]), ("ok", "defect"))
        self.assertIn("work/thermal/tool/raw.txt", ctx.result["evidence"])
        self.assertEqual(ctx.result["details"]["margins"]["peak"]["max"], -1)

    @toolchain_test
    def test_parser_cannot_overwrite_tool_evidence(self):
        ctx = self.measurement_fixture()
        adapter = self.run / "inputs/adapter.py"
        original = adapter.read_text()
        adapter.write_text(original.replace('value = float', 'Path("/work/tool/raw.txt").write_text("5")\nvalue = float'))
        with self.assertRaises(CheckError):
            check_measurements(ctx)
        self.assertEqual((ctx.work / "tool/raw.txt").read_text(), "101")

    @toolchain_test
    def test_parser_and_producer_cannot_forge_coordinator_request(self):
        for label, program in (("parser", "adapter.py"), ("producer", "solver.py")):
            with self.subTest(label=label):
                ctx = self.measurement_fixture()
                ctx.cid = label
                ctx.work = self.run / "work" / label
                ctx.work.mkdir()
                ctx.params["parameters"] = {"ambient": 85}
                path = self.run / "inputs" / program
                path.write_text('from pathlib import Path\nPath("/work/request.json").write_text("forged")\n' + path.read_text())
                with self.assertRaises(CheckError):
                    check_measurements(ctx)
                from agent_ee.storage import read_json
                self.assertEqual(read_json(ctx.work / "request.json")["parameters"], {"ambient": 85})
                if label == "producer":
                    self.assertEqual(read_json(ctx.work / "tool/request.json")["parameters"], {"ambient": 85})

    @toolchain_test
    def test_parser_cannot_relabel_domain_or_replace_raw_provenance(self):
        for attack in ("domain", "artifact"):
            with self.subTest(attack=attack):
                ctx = self.measurement_fixture()
                ctx.cid = attack
                ctx.work = self.run / "work" / attack
                ctx.work.mkdir()
                adapter = self.run / "inputs/adapter.py"
                text = adapter.read_text()
                if attack == "domain":
                    text = text.replace('"domain": "thermal"', '"domain": "electrical"')
                else:
                    text = text.replace('"artifacts": ["tool/raw.txt"]', '"artifacts": ["measurements.json"]')
                adapter.write_text(text)
                with self.assertRaises(CheckError):
                    check_measurements(ctx)

    def test_different_tool_binary_is_refused_before_execution(self):
        ctx = self.measurement_fixture()
        ctx.params["tool"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(CheckError, "pinned baseline"):
            check_measurements(ctx)
        self.assertFalse((ctx.work / "tool/execution.json").exists())

    def coupling(self, temperature, inductance, condition="load"):
        specs = [{"id": "thermal"}, {"id": "layout"}, {"id": "joint"}]
        self.manifest["scope"]["checks"] = specs
        for cid, value, unit in (("thermal", temperature, "degC"), ("layout", inductance, "nH")):
            write_json(self.run / "checks" / f"{cid}.json", {"candidate_id": "candidate", "execution": "ok",
                "details": {"producer": {"domain": cid},
                    "metrics": {"peak": {"value": value, "unit": unit,
                        "condition": condition if cid == "layout" else "load"}}}})
        return self.ctx({"id": "joint", "kind": "coupled_margins", "params": {"condition": "load",
            "members": [{"name": "heat", "check": "thermal", "metric": "peak"},
                        {"name": "loop", "check": "layout", "metric": "peak"}],
            "limits": {"heat": {"unit": "degC", "condition": "load", "max": 100},
                       "loop": {"unit": "nH", "condition": "load", "max": 10}}}})

    def test_thermal_improvement_cannot_hide_lost_layout_margin(self):
        ctx = self.coupling(80, 11)
        check_coupled_margins(ctx)
        self.assertEqual(ctx.result["outcome"], "defect")
        self.assertEqual(ctx.result["details"]["margins"], {"heat": {"max": 20}, "loop": {"max": -1}})

    def test_coupled_results_must_share_operating_point(self):
        ctx = self.coupling(80, 9, "idle")
        with self.assertRaisesRegex(CheckError, "different operating"):
            check_coupled_margins(ctx)


if __name__ == "__main__":
    unittest.main()

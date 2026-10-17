"""Tests for agent_ee.checks against the RC fixtures, through the executor.

Run: python3 -m unittest tests.test_checks  (from the repo root; pytest works
too). Each class captures a fixture scope into a temporary run directory with
agent_ee.candidate.capture and drives agent_ee.checks.run_checks for real
(kicad-cli, pcbnew and ngspice inside Bubblewrap), so a run takes ~1 min.
"""

import json
import math
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent_ee import candidate, checks  # noqa: E402

FIXTURES = REPO / "fixtures" / "rc"


def run_fixture(scope_name):
    run_dir = Path(tempfile.mkdtemp(prefix=f"agent-ee-test-{scope_name}-"))
    candidate.capture(FIXTURES / f"{scope_name}.scope.json", run_dir)
    results = checks.run_checks(run_dir, time.monotonic() + 1200)
    return run_dir, {r["id"]: r for r in results}


class ControlFixtureTest(unittest.TestCase):
    """The unmodified copy must be clean on every declared criterion."""

    @classmethod
    def setUpClass(cls):
        cls.run_dir, cls.results = run_fixture("control")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.run_dir, ignore_errors=True)

    def status(self, cid):
        r = self.results[cid]
        return r["execution"], r["outcome"], r

    def test_results_written_immediately_with_candidate_id(self):
        manifest = json.loads((self.run_dir / "candidate.json").read_text())
        for cid, r in self.results.items():
            path = self.run_dir / "checks" / f"{cid}.json"
            self.assertTrue(path.is_file(), f"missing {path}")
            self.assertEqual(r["candidate_id"], manifest["candidate_id"])
            for rel in r["evidence"]:
                self.assertTrue((self.run_dir / rel).is_file(), f"evidence missing: {rel}")

    def test_erc_clean(self):
        execution, outcome, r = self.status("erc")
        self.assertEqual((execution, outcome), ("ok", "pass"), r["summary"])
        self.assertEqual(r["details"]["sheets_checked"], 1)

    def test_drc_clean_with_parity(self):
        execution, outcome, r = self.status("drc")
        self.assertEqual((execution, outcome), ("ok", "pass"), r["summary"])
        self.assertEqual(r["details"]["schematic_parity"], 0)

    def test_netlist_connectivity(self):
        execution, outcome, r = self.status("netlist")
        self.assertEqual(execution, "ok", r["summary"])
        conn = json.loads((self.run_dir / "work" / "netlist" / "connectivity.json").read_text())
        self.assertEqual(set(conn["components"]), {"R1", "C1", "J1"})
        self.assertEqual(conn["components"]["R1"]["value"], "10k")
        self.assertIn("/OUT", conn["nets"])

    def test_geometry_meets_declared_minimum(self):
        execution, outcome, r = self.status("geometry")
        self.assertEqual((execution, outcome), ("ok", "pass"), r["summary"])
        self.assertGreaterEqual(r["details"]["narrowest_mm"], 0.20)
        geo = json.loads((self.run_dir / "work" / "geometry" / "geometry.json").read_text())
        refs = {fp["ref"]: fp for fp in geo["footprints"]}
        self.assertEqual(set(refs), {"R1", "C1", "J1"})
        self.assertFalse(refs["R1"]["dnp"])
        self.assertTrue(all(p["uuid"] for p in geo["pads"]))

    def test_rc_transient_within_limits(self):
        execution, outcome, r = self.status("rc-step")
        self.assertEqual((execution, outcome), ("ok", "pass"), r["summary"])
        vout = r["details"]["measurements"]["vout_1ms"]["measured"]
        self.assertAlmostEqual(vout, 0.6321206, places=3)
        self.assertGreater(r["details"]["raw"]["points"], 2)
        self.assertEqual(r["details"]["elements"]["R1"]["parsed"], 10_000.0)

    def test_geometry_console_has_no_via_width_assert(self):
        console = (self.run_dir / "work/geometry/console.log").read_text()
        self.assertNotIn("PCB_VIA::GetWidth called without a layer argument", console)


class RCDefaultTest(unittest.TestCase):
    def test_native_default_matches_explicit_and_analytic_response(self):
        with tempfile.TemporaryDirectory(prefix="agent-ee-rc-default-") as tmp:
            root = Path(tmp)
            conditions = []
            identities = []
            for index, amplitude in enumerate((None, 1.0, 1, "1.00", 2.0)):
                with self.subTest(amplitude=amplitude):
                    scope = json.loads((FIXTURES / "control.scope.json").read_text())
                    for item in scope["inputs"]:
                        item["path"] = str((FIXTURES / item["path"]).resolve())
                    scope["checks"] = [c for c in scope["checks"]
                                       if c["kind"] in ("netlist", "rc_transient")]
                    params = scope["checks"][1]["params"]
                    params.pop("step_v")
                    if amplitude is not None:
                        params["step_v"] = amplitude
                    effective = float(amplitude) if amplitude is not None else 1.0
                    params["measurements"] = [{"name": "vout", "at_ms": "1.00",
                                               "min": 0.62 * effective,
                                               "max": 0.65 * effective}]
                    scope_path = root / f"scope-{index}.json"
                    scope_path.write_text(json.dumps(scope))
                    run_dir = root / f"run-{index}"
                    run_dir.mkdir()
                    manifest = candidate.capture(scope_path, run_dir)
                    results = checks.run_checks(run_dir, time.monotonic() + 120)
                    result = results[-1]
                    self.assertEqual((result["execution"], result["outcome"]),
                                     ("ok", "pass"), result)
                    metric = result["details"]["metrics"]["vout"]
                    # Independent RC oracle: fixture R=10 kohm, C=100 nF, t=1 ms.
                    expected = effective * (1 - math.exp(-0.001 / (10000 * 100e-9)))
                    self.assertAlmostEqual(metric["value"], expected, delta=effective * 0.0001)
                    self.assertEqual(result["details"]["step_v"], effective)
                    self.assertEqual(manifest["scope"]["checks"][1]["params"], params)
                    conditions.append(metric["condition"])
                    identities.append(manifest["candidate_id"])
                    if amplitude is None:
                        ctx = checks.Ctx(run_dir, manifest, scope["checks"][1],
                                         time.monotonic() + 120)
                        with patch.object(ctx, "execute", side_effect=checks.CheckError(
                                "failed", "injected simulator execution failure")):
                            with self.assertRaises(checks.CheckError):
                                checks.check_rc_transient(ctx)
                        self.assertEqual(ctx.result["outcome"], "unknown")
                        self.assertNotIn("metrics", ctx.result["details"])
            self.assertEqual(conditions, ["ideal-unloaded-1.0V-step-at-1.0ms",
                                          "ideal-unloaded-1.0V-step-at-1.0ms",
                                          "ideal-unloaded-1V-step-at-1.0ms",
                                          "ideal-unloaded-1.00V-step-at-1.0ms",
                                          "ideal-unloaded-2.0V-step-at-1.0ms"])
            self.assertNotEqual(identities[0], identities[1])


class SeededFixtureTest(unittest.TestCase):
    """100k seed: natively clean, functionally defective at the 1 ms limit."""

    @classmethod
    def setUpClass(cls):
        cls.run_dir, cls.results = run_fixture("seeded")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.run_dir, ignore_errors=True)

    def test_native_checks_stay_clean(self):
        for cid in ("erc", "drc"):
            r = self.results[cid]
            self.assertEqual((r["execution"], r["outcome"]), ("ok", "pass"), r["summary"])

    def test_seed_value_comes_from_native_netlist(self):
        conn = json.loads((self.run_dir / "work" / "netlist" / "connectivity.json").read_text())
        self.assertEqual(conn["components"]["R1"]["value"], "100k")

    def test_rc_transient_detects_functional_defect(self):
        r = self.results["rc-step"]
        self.assertEqual((r["execution"], r["outcome"]), ("ok", "defect"), r["summary"])
        vout = r["details"]["measurements"]["vout_1ms"]["measured"]
        self.assertAlmostEqual(vout, 0.0951626, places=3)
        self.assertFalse(r["details"]["measurements"]["vout_1ms"]["within_limits"])


def make_raw(names, rows, flags="real", lie_points=None):
    """Synthetic ngspice binary raw file bytes."""
    import struct
    header = ("Title: t\nDate: now\nPlotname: Transient Analysis\n"
              f"Flags: {flags}\nNo. Variables: {len(names)}\n"
              f"No. Points: {lie_points if lie_points is not None else len(rows)}\n"
              "Variables:\n")
    for i, name in enumerate(names):
        header += f"\t{i}\t{name}\tvoltage\n"
    header += "Binary:\n"
    body = b"".join(struct.pack("<d", v) for row in rows for v in row)
    return header.encode("latin-1") + body


GOOD_LOG = ("No. of Data Rows : 3\n"
            "  Measurements for Transient Analysis\n"
            "vout_1ms            =  6.321201e-01 at=  1.000000e-03\n")
WANTED = [{"name": "vout_1ms", "at_ms": 1.0, "min": 0.62, "max": 0.65}]


class SimAnalysisFailureTest(unittest.TestCase):
    """Missing/non-finite .meas, solver errors and malformed raw must never pass."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="agent-ee-test-sim-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

    def write(self, log=GOOD_LOG, raw=None):
        (self.work / "ngspice.log").write_text(log)
        if raw is None:
            raw = make_raw(["time", "v(out)"], [[0.0, 0.0], [1e-3, 0.632], [5e-3, 0.993]])
        (self.work / "result.raw").write_bytes(raw)

    def assert_check_error(self, execution, wanted=WANTED):
        with self.assertRaises(checks.CheckError) as caught:
            checks.analyze_sim(self.work, wanted)
        self.assertEqual(caught.exception.execution, execution, str(caught.exception))

    def test_valid_run_passes(self):
        self.write()
        result, failures, raw = checks.analyze_sim(self.work, WANTED)
        self.assertTrue(result["vout_1ms"]["within_limits"])
        self.assertEqual(failures, [])
        self.assertEqual(raw["points"], 3)

    def test_missing_measurement_is_malformed(self):
        self.write(log="  Measurements for Transient Analysis\nother = 1.0\n")
        self.assert_check_error("malformed")

    def test_no_measurement_section_is_malformed(self):
        self.write(log="No. of Data Rows : 3\n")
        self.assert_check_error("malformed")

    def test_nonfinite_measurement_is_malformed(self):
        self.write(log="  Measurements for Transient Analysis\nvout_1ms = nan\n")
        self.assert_check_error("malformed")

    def test_solver_error_is_failed(self):
        self.write(log="doAnalyses: TRAN:  Timestep too small; trouble\n"
                       "run simulation(s) aborted\n" + GOOD_LOG)
        self.assert_check_error("failed")

    def test_missing_log_is_malformed(self):
        (self.work / "result.raw").write_bytes(make_raw(["time"], [[0.0], [1.0]]))
        self.assert_check_error("malformed")

    def test_truncated_raw_is_malformed(self):
        self.write(raw=make_raw(["time", "v(out)"], [[0.0, 0.0], [1e-3, 0.6]])[:-5])
        self.assert_check_error("malformed")

    def test_raw_point_count_lie_is_malformed(self):
        self.write(raw=make_raw(["time", "v(out)"], [[0.0, 0.0]], lie_points=7))
        self.assert_check_error("malformed")

    def test_nonfinite_raw_values_are_malformed(self):
        self.write(raw=make_raw(["time", "v(out)"],
                                [[0.0, 0.0], [1e-3, float("inf")], [5e-3, 0.9]]))
        self.assert_check_error("malformed")

    def test_single_point_raw_is_malformed(self):
        self.write(raw=make_raw(["time", "v(out)"], [[0.0, 0.0]]))
        self.assert_check_error("malformed")


def sheet(body=""):
    return "(kicad_sch\n" + body + "\n)\n"


def sheet_ref(path):
    return f'(property "Sheetfile" "{path}")'


class SheetHierarchyTest(unittest.TestCase):
    """Child resolution is sheet-relative; cycles and input escapes are refused."""

    def setUp(self):
        base = Path(tempfile.mkdtemp(prefix="agent-ee-test-sheets-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.inputs = base / "inputs"
        (self.inputs / "project" / "modules").mkdir(parents=True)
        self.outside = base / "outside.kicad_sch"
        self.outside.write_text(sheet())

    def write(self, rel, body=""):
        (self.inputs / rel).write_text(sheet(body))

    def test_child_resolves_relative_to_containing_sheet(self):
        self.write("project/root.kicad_sch", sheet_ref("modules/sub.kicad_sch"))
        self.write("project/modules/sub.kicad_sch", sheet_ref("leaf.kicad_sch"))
        self.write("project/modules/leaf.kicad_sch")
        files, instances, missing = checks.sheet_instances(self.inputs, "project/root.kicad_sch")
        self.assertEqual(files, ["project/root.kicad_sch", "project/modules/sub.kicad_sch",
                                 "project/modules/leaf.kicad_sch"])
        self.assertEqual(instances, 3)
        self.assertEqual(missing, [])

    def test_reused_sheet_counts_instances(self):
        self.write("project/root.kicad_sch",
                   sheet_ref("modules/sub.kicad_sch") + "\n" + sheet_ref("modules/sub.kicad_sch"))
        self.write("project/modules/sub.kicad_sch", sheet_ref("leaf.kicad_sch"))
        self.write("project/modules/leaf.kicad_sch")
        _, instances, _ = checks.sheet_instances(self.inputs, "project/root.kicad_sch")
        self.assertEqual(instances, 5)  # root + 2x sub + 2x leaf

    def test_missing_child_is_reported(self):
        self.write("project/root.kicad_sch", sheet_ref("modules/vr-conditioner.kicad_sch"))
        files, instances, missing = checks.sheet_instances(self.inputs, "project/root.kicad_sch")
        self.assertEqual(missing, ["modules/vr-conditioner.kicad_sch"])
        self.assertEqual(instances, 2)

    def test_cycle_is_refused(self):
        self.write("project/root.kicad_sch", sheet_ref("a.kicad_sch"))
        self.write("project/a.kicad_sch", sheet_ref("b.kicad_sch"))
        self.write("project/b.kicad_sch", sheet_ref("a.kicad_sch"))
        with self.assertRaisesRegex(ValueError, "cycle"):
            checks.sheet_instances(self.inputs, "project/root.kicad_sch")

    def test_escape_from_inputs_is_refused(self):
        self.write("project/root.kicad_sch", sheet_ref("../../outside.kicad_sch"))
        with self.assertRaisesRegex(ValueError, "escapes the captured inputs"):
            checks.sheet_instances(self.inputs, "project/root.kicad_sch")


def conn_fixture(**overrides):
    components = {
        "R1": {"value": "10k", "footprint": "", "dnp": False,
               "libsource": {"lib": "Device", "part": "R"},
               "sheet": "/", "sheet_uuid": "/", "uuid": "u-r1"},
        "C1": {"value": "100n", "footprint": "", "dnp": False,
               "libsource": {"lib": "Device", "part": "C"},
               "sheet": "/", "sheet_uuid": "/", "uuid": "u-c1"},
        "J1": {"value": "Conn_01x03", "footprint": "", "dnp": False,
               "libsource": {"lib": "Connector_Generic", "part": "Conn_01x03"},
               "sheet": "/", "sheet_uuid": "/", "uuid": "u-j1"},
    }
    components.update(overrides)
    nets = {"/IN": ["J1.1", "R1.1"], "/OUT": ["C1.1", "J1.2", "R1.2"],
            "GND": ["C1.2", "J1.3"]}
    for ref, meta in overrides.items():
        for index, net in enumerate(meta.get("_nets", []), start=1):
            nets.setdefault(net, []).append(f"{ref}.{index}")
        meta.pop("_nets", None)
    return {"components": components, "nets": nets}


BASE_PARAMS = {"input_net": "/IN", "output_net": "/OUT", "ground_net": "GND",
               "step_v": 1.0, "stop_ms": 5.0,
               "measurements": [{"name": "vout_1ms", "at_ms": 1.0, "min": 0.62, "max": 0.65}]}


class DeckBuilderTest(unittest.TestCase):
    """DNP exclusion, native symbol-class gating and parameter validation."""

    def build(self, conn=None, **param_overrides):
        params = dict(BASE_PARAMS, **param_overrides)
        return checks.build_deck(conn or conn_fixture(), params)

    def assert_unsupported(self, conn=None, regex="", **param_overrides):
        with self.assertRaises(checks.CheckError) as caught:
            self.build(conn, **param_overrides)
        self.assertEqual(caught.exception.execution, "unsupported", str(caught.exception))
        if regex:
            self.assertRegex(str(caught.exception), regex)

    def test_control_deck(self):
        deck, elements, ignored, excluded_dnp, _, _ = self.build()
        self.assertIn("R1 IN OUT 10k", deck)
        self.assertIn("C1 OUT 0 100n", deck)
        self.assertEqual(ignored, ["J1"])
        self.assertEqual(excluded_dnp, [])
        self.assertEqual(elements["R1"]["class"], "R")

    def test_dnp_component_is_excluded_and_recorded(self):
        conn = conn_fixture(R9={"value": "120", "footprint": "", "dnp": True,
                                "libsource": {"lib": "Device", "part": "R"},
                                "sheet": "/x/", "sheet_uuid": "/u/", "uuid": "u-r9",
                                "_nets": ["/IN", "/OUT"]})
        deck, elements, _, excluded_dnp, _, _ = self.build(conn)
        self.assertEqual(excluded_dnp, ["R9"])
        self.assertNotIn("R9", deck)
        self.assertNotIn("R9", elements)

    def test_r_prefixed_nonresistor_is_unsupported(self):
        conn = conn_fixture(R1={"value": "10k", "footprint": "", "dnp": False,
                                "libsource": {"lib": "Relay", "part": "FINDER-32.21"},
                                "sheet": "/", "sheet_uuid": "/", "uuid": "u-r1"})
        self.assert_unsupported(conn, regex="not a supported passive symbol class")

    def test_missing_libsource_is_unsupported(self):
        conn = conn_fixture(R1={"value": "10k", "footprint": "", "dnp": False,
                                "libsource": None,
                                "sheet": "/", "sheet_uuid": "/", "uuid": "u-r1"})
        self.assert_unsupported(conn, regex="not a supported passive symbol class")

    def test_class_prefix_mismatch_is_unsupported(self):
        conn = conn_fixture(X7={"value": "10k", "footprint": "", "dnp": False,
                                "libsource": {"lib": "Device", "part": "R"},
                                "sheet": "/", "sheet_uuid": "/", "uuid": "u-x7",
                                "_nets": ["/IN", "GND"]})
        self.assert_unsupported(conn, regex="mismatched")

    def test_min_above_max_is_unsupported(self):
        self.assert_unsupported(regex="min",
                                measurements=[{"name": "m", "at_ms": 1.0, "min": 0.9, "max": 0.1}])

    def test_duplicate_measurement_names_are_unsupported(self):
        self.assert_unsupported(regex="duplicate",
                                measurements=[{"name": "m", "at_ms": 1.0},
                                              {"name": "M", "at_ms": 2.0}])

    def test_at_ms_outside_interval_is_unsupported(self):
        self.assert_unsupported(regex="interval",
                                measurements=[{"name": "m", "at_ms": 9.0}])

    def test_nonpositive_or_nonfinite_stimulus_is_unsupported(self):
        for amplitude in (0.0, -1.0, float("nan"), float("inf"), float("-inf"), "bad", None):
            with self.subTest(amplitude=amplitude):
                self.assert_unsupported(regex="step_v", step_v=amplitude)
        self.assert_unsupported(regex="stop_ms", stop_ms=-1.0)

    def test_nonfinite_limit_is_unsupported(self):
        self.assert_unsupported(regex="not finite",
                                measurements=[{"name": "m", "at_ms": 1.0, "min": float("inf")}])


class DeadlineTest(unittest.TestCase):
    """An exhausted deadline yields interrupted results, never silent passes."""

    def test_exhausted_deadline_interrupts_every_check(self):
        run_dir = Path(tempfile.mkdtemp(prefix="agent-ee-test-deadline-"))
        try:
            candidate.capture(FIXTURES / "control.scope.json", run_dir)
            results = checks.run_checks(run_dir, time.monotonic())
            self.assertTrue(results)
            for r in results:
                self.assertEqual(r["execution"], "interrupted", r)
                self.assertEqual(r["outcome"], "unknown")
                self.assertTrue((run_dir / "checks" / f"{r['id']}.json").is_file())
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

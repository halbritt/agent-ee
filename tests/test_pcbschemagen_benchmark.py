"""Unit and integration tests for pcbschemagen_benchmark (Milestone 4).

Covers:
- Manifest loading, task filtering, and task validation (25 Tier-1 tasks)
- 2.54 mm header wrapper authorization attachment and connector helpers
- Divergence classification (concordant pass/fail, physical bottleneck, semantic bypass)
- Dual-gate evaluation execution, scoring, and divergence detection
- Dual-gate benchmark report generation and JSON schema adherence
- CLI hook invocation
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from agent_ee.pcbschemagen_benchmark import (
    CONCORDANT_FAIL,
    CONCORDANT_PASS,
    DEFAULT_MANIFEST_PATH,
    PHYSICAL_BOTTLENECK,
    SCHEMA,
    SEMANTIC_BYPASS,
    Tier1Manifest,
    attach_header_wrapper,
    classify_divergence,
    evaluate_gate2,
    evaluate_task,
    format_summary_table,
    main,
    make_254mm_pin_header_part,
    make_edge_connector,
    run_benchmark,
)
from agent_ee.storage import read_json, write_json


def _make_valid_tps54302_design() -> dict:
    """Construct a full agent-ee.design/1 statement for Task 6 (TPS54302)."""
    return {
        "schema": "agent-ee.design/1",
        "name": "tier1_task6_tps54302",
        "requirement_prefix": "T106",
        "intent": "12V to 3.3V auxiliary buck regulator using TPS54302.",
        "board": {
            "width_mm": 40.0,
            "height_mm": 30.0,
            "thickness_mm": 1.2,
            "copper_layers": 2,
            "max_component_height_mm": 8.0,
        },
        "parts": {
            "U1": {
                "lcsc": "C96570",
                "value": "TPS54302",
                "lib_id": "Power_Management:TPS54302DDR",
                "footprint": "Package_SO:SOT-23-6",
                "group": "regulator",
            },
            "C1": {
                "lcsc": "C15849",
                "value": "10u",
                "lib_id": "Device:C",
                "footprint": "Capacitor_SMD:C_0805_2012Metric",
                "group": "regulator",
            },
            "C2": {
                "lcsc": "C14663",
                "value": "100n",
                "lib_id": "Device:C",
                "footprint": "Capacitor_SMD:C_0603_1608Metric",
                "group": "regulator",
            },
            "C3": {
                "lcsc": "C15850",
                "value": "22u",
                "lib_id": "Device:C",
                "footprint": "Capacitor_SMD:C_0805_2012Metric",
                "group": "regulator",
            },
            "L1": {
                "lcsc": "C168341",
                "value": "4.7u",
                "lib_id": "Device:L",
                "footprint": "Inductor_SMD:L_0630",
                "group": "regulator",
            },
            "R1": {
                "lcsc": "C25804",
                "value": "100k",
                "lib_id": "Device:R",
                "footprint": "Resistor_SMD:R_0603_1608Metric",
                "group": "regulator",
            },
            "R2": {
                "lcsc": "C23141",
                "value": "32.4k",
                "lib_id": "Device:R",
                "footprint": "Resistor_SMD:R_0603_1608Metric",
                "group": "regulator",
            },
            "J1": make_254mm_pin_header_part("J1", 2, value="Conn_01x02"),
            "J2": make_254mm_pin_header_part("J2", 2, value="Conn_01x02"),
        },
        "nets": {
            "VIN": ["J1.1", "U1.3", "C1.1"],
            "GND": ["J1.2", "J2.2", "U1.1", "C1.2", "R2.2", "C3.2"],
            "SW": ["U1.2", "L1.1", "C2.2"],
            "BOOT": ["U1.6", "C2.1"],
            "VOUT": ["L1.2", "R1.1", "C3.1", "J2.1"],
            "FB": ["U1.4", "R1.2", "R2.1"],
        },
        "no_connects": ["U1.5"],
        "ground": "GND",
        "power_flags": ["VIN", "GND"],
        "connectors": [
            make_edge_connector("J1", edge="left", position_mm=10.0, inset_mm=1.0),
            make_edge_connector("J2", edge="right", position_mm=10.0, inset_mm=1.0),
        ],
        "decoupling": [
            {
                "device": "U1",
                "capacitor": "C1",
                "supply": ["U1.3", "C1.1"],
                "return": ["U1.1", "C1.2"],
                "max_distance_mm": 10.0,
            }
        ],
        "groups": [
            {"name": "regulator", "anchor": "U1", "title": "Buck regulator"},
            {"name": "interfaces", "anchor": "J1", "title": "Headers"},
        ],
        "plane": {"net": "GND", "layer": "B.Cu"},
        "functional": [
            {
                "text": "12V to 3.3V auxiliary buck regulator using TPS54302.",
                "witnesses": [
                    {
                        "id": "w_vin",
                        "type": "pin_net",
                        "terminal": "U1.3",
                        "net": "VIN",
                    }
                ],
            }
        ],
        "bench": ["Verify 3.3V regulated output on bench under load."],
    }


class TestTier1Manifest(unittest.TestCase):
    """Test loading and validation of the 25 Tier-1 benchmark tasks."""

    def test_default_manifest_exists_and_loads(self):
        self.assertTrue(DEFAULT_MANIFEST_PATH.is_file(), f"Missing {DEFAULT_MANIFEST_PATH}")
        manifest = Tier1Manifest.load()

        self.assertEqual(manifest.suite, "PCBSchemaGen-Tier1")
        self.assertEqual(manifest.total_tasks, 25)
        self.assertEqual(manifest.counts.get("pcbbench"), 18)
        self.assertEqual(manifest.counts.get("open_schematics"), 7)

        tasks = manifest.list_tasks()
        self.assertEqual(len(tasks), 25)

    def test_manifest_task_properties(self):
        manifest = Tier1Manifest.load()

        # Check PCBBench task 6
        t6 = manifest.get_task("6")
        self.assertIsNotNone(t6)
        self.assertEqual(t6.dataset, "pcbbench")
        self.assertEqual(t6.sub_module_name, "BUCK_AUX_LOGIC")
        self.assertEqual(t6.primary_ic, "TPS54302")
        self.assertEqual(t6.package, "SOT-23-6")
        self.assertIn("VIN", t6.input_nodes)
        self.assertIn("VOUT", t6.output_nodes)
        self.assertIn("Connector_PinHeader_2.54mm", t6.wrapped_prompt)

        # Check OpenSchematics task 104
        t104 = manifest.get_task("104")
        self.assertIsNotNone(t104)
        self.assertEqual(t104.dataset, "open_schematics")
        self.assertEqual(t104.sub_module_name, "USB_DATA_LOGGER")
        self.assertEqual(t104.level, "Hard")
        self.assertIn("CH32V003", t104.primary_ic)

        # Check OpenSchematics task 246
        t246 = manifest.get_task("246")
        self.assertIsNotNone(t246)
        self.assertEqual(t246.dataset, "open_schematics")
        self.assertEqual(t246.sub_module_name, "USB_C_INTERFACE")
        self.assertEqual(t246.primary_ic, "AP2171W")

    def test_filter_by_dataset(self):
        manifest = Tier1Manifest.load()
        pcbbench_tasks = manifest.filter_by_dataset("pcbbench")
        ose_tasks = manifest.filter_by_dataset("open_schematics")

        self.assertEqual(len(pcbbench_tasks), 18)
        self.assertEqual(len(ose_tasks), 7)
        self.assertEqual(len(pcbbench_tasks) + len(ose_tasks), 25)

    def test_missing_manifest_raises_error(self):
        with self.assertRaises(FileNotFoundError):
            Tier1Manifest.load("/nonexistent/manifest.json")


class TestHeaderWrapper(unittest.TestCase):
    """Test 2.54 mm header wrapper authorization text and connector helpers."""

    def test_attach_header_wrapper(self):
        base_prompt = "Design a low noise amplifier for sensor input."
        wrapped = attach_header_wrapper(base_prompt)

        self.assertTrue(wrapped.startswith(base_prompt))
        self.assertIn("### Interface Authorization Wrapper (2.54 mm Pin Headers)", wrapped)
        self.assertIn("Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", wrapped)
        self.assertIn("Physical Edge Placement", wrapped)

    def test_make_pin_header_part_helper(self):
        part = make_254mm_pin_header_part("J1", 4, value="Conn_01x04")
        self.assertEqual(part["value"], "Conn_01x04")
        self.assertEqual(part["footprint"], "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
        self.assertEqual(part["lib_id"], "Connector_Generic:Conn_01x04")
        self.assertEqual(part["connector"], {"rows": 4, "columns": 1})

    def test_make_edge_connector_helper(self):
        conn = make_edge_connector("J1", edge="top", position_mm=15.0, inset_mm=0.8)
        self.assertEqual(conn["reference"], "J1")
        self.assertEqual(conn["edge"], "top")
        self.assertEqual(conn["position_mm"], 15.0)
        self.assertEqual(conn["inset_mm"], 0.8)


class TestDivergenceClassification(unittest.TestCase):
    """Test divergence categorization between Gate 1 and Gate 2 results."""

    def test_concordant_pass(self):
        cat, expl = classify_divergence(gate1_passed=True, gate2_passed=True)
        self.assertEqual(cat, CONCORDANT_PASS)
        self.assertIn("satisfied both", expl)

    def test_concordant_fail(self):
        cat, expl = classify_divergence(gate1_passed=False, gate2_passed=False)
        self.assertEqual(cat, CONCORDANT_FAIL)
        self.assertIn("failed both", expl)

    def test_physical_bottleneck(self):
        cat, expl = classify_divergence(gate1_passed=True, gate2_passed=False)
        self.assertEqual(cat, PHYSICAL_BOTTLENECK)
        self.assertIn("failed physical board completion", expl)

    def test_semantic_bypass(self):
        cat, expl = classify_divergence(gate1_passed=False, gate2_passed=True)
        self.assertEqual(cat, SEMANTIC_BYPASS)
        self.assertIn("passed physical layout", expl)


class TestDualGateEvaluation(unittest.TestCase):
    """Test execution of dual-gate evaluation across different candidate circuit conditions."""

    def setUp(self):
        self.manifest = Tier1Manifest.load()
        self.task6 = self.manifest.get_task("6")
        self.assertIsNotNone(self.task6)

    def test_valid_tps54302_passes_both_gates(self):
        design = _make_valid_tps54302_design()
        res = evaluate_task(self.task6, design)

        self.assertEqual(res.score, ("PASS", "PASS"))
        self.assertEqual(res.dual_gate_status, "PASS")
        self.assertEqual(res.divergence_category, CONCORDANT_PASS)
        self.assertTrue(res.gate1["passed"])
        self.assertTrue(res.gate2["passed"])

    def test_gate1_failure_missing_critical_connection(self):
        design = _make_valid_tps54302_design()
        # Missing required VIN pin on U1
        design["nets"]["VIN"] = ["J1.1", "C1.1"]  # U1.3 missing

        res = evaluate_task(self.task6, design)
        self.assertEqual(res.score[0], "FAIL")
        self.assertFalse(res.gate1["passed"])
        self.assertEqual(res.dual_gate_status, "FAIL")

    def test_gate2_failure_physical_layer_violation(self):
        design = _make_valid_tps54302_design()
        # Set 4 copper layers (violating compiler 2-layer FR-4 rule)
        design["board"]["copper_layers"] = 4

        res = evaluate_task(self.task6, design)
        self.assertEqual(res.score[1], "FAIL")
        self.assertFalse(res.gate2["passed"])
        self.assertTrue(
            any("only two copper layers are supported" in e or "only 2 copper layers" in e for e in res.gate2["errors"])
        )
        self.assertEqual(res.dual_gate_status, "FAIL")

    def test_semantic_bypass_detection(self):
        """Candidate completes physical checks but violates schematic pin invariants."""
        design = _make_valid_tps54302_design()
        # Put SW in no_connects and leave SW net between L1 and C2
        # All physical checks pass (valid 2-layer, all nets >= 2 pins, connectors ok)
        # But Gate 1 fails because SW must be connected!
        design["no_connects"].append("U1.2")
        design["nets"]["SW"] = ["L1.1", "C2.2"]

        res = evaluate_task(self.task6, design)
        self.assertEqual(res.score[0], "FAIL")
        self.assertEqual(res.score[1], "PASS")
        self.assertEqual(res.divergence_category, SEMANTIC_BYPASS)
        self.assertEqual(res.dual_gate_status, "FAIL")

    def test_physical_bottleneck_detection(self):
        """Candidate passes Gate 1 schematic topo, but fails physical connector edge reach."""
        design = _make_valid_tps54302_design()
        # Remove connectors from connectors array
        design["connectors"] = []

        res = evaluate_task(self.task6, design)
        self.assertEqual(res.score[0], "PASS")
        self.assertEqual(res.score[1], "FAIL")
        self.assertEqual(res.divergence_category, PHYSICAL_BOTTLENECK)
        self.assertEqual(res.dual_gate_status, "FAIL")

    def test_gate2_evaluation_from_build_directory(self):
        """Test Gate 2 evaluation when given an existing build run directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir) / "run"
            cand_dir = run_dir / "stages" / "package" / "candidate"
            cand_dir.mkdir(parents=True)

            write_json(
                run_dir / "build-report.json",
                {
                    "accepted": True,
                    "status": "accepted",
                    "candidate": "stages/package/candidate",
                    "attempts": [{"stage": "package"}],
                },
            )
            write_json(
                run_dir / "qualification.json",
                {
                    "mode": "typed",
                    "dimensions": {"file_release": {"status": "pass"}},
                },
            )
            write_json(
                cand_dir / "routing.json",
                {"unrouted_count": 0, "wire_length_mm": 125.4, "via_count": 6},
            )

            g2 = evaluate_gate2(run_dir, task_id="6")
            self.assertTrue(g2.passed)
            self.assertEqual(g2.status, "PASS")
            self.assertEqual(g2.stage, "package")
            self.assertEqual(g2.wire_length_mm, 125.4)
            self.assertEqual(g2.via_count, 6)


class TestDualGateBenchmarkRunnerSuite(unittest.TestCase):
    """Test full benchmark suite run, reporting, and JSON schema formatting."""

    def setUp(self):
        self.manifest = Tier1Manifest.load()

    def test_run_benchmark_subset_and_report_schema(self):
        tps_design = _make_valid_tps54302_design()
        candidates = {
            "6": tps_design,
        }

        report = run_benchmark(
            manifest=self.manifest,
            candidates=candidates,
            task_ids=["5", "6"],
        )

        self.assertEqual(report.schema, SCHEMA)
        self.assertEqual(report.suite, "PCBSchemaGen-Tier1")
        self.assertEqual(report.summary["evaluated_tasks"], 2)
        self.assertEqual(report.summary["total_manifest_tasks"], 25)

        # Task 6 was provided and passes
        t6_res = next(t for t in report.tasks if t.task_id == "6")
        self.assertEqual(t6_res.score, ("PASS", "PASS"))
        self.assertEqual(t6_res.dual_gate_status, "PASS")

        # Task 5 had no candidate provided and fails
        t5_res = next(t for t in report.tasks if t.task_id == "5")
        self.assertEqual(t5_res.score, ("FAIL", "FAIL"))
        self.assertEqual(t5_res.dual_gate_status, "FAIL")

        # Check summary metrics
        self.assertEqual(report.summary["gate1_passed"], 1)
        self.assertEqual(report.summary["gate2_passed"], 1)
        self.assertEqual(report.summary["dual_gate_passed"], 1)
        self.assertEqual(report.summary["dual_gate_pass_rate"], 0.5)

        # Test JSON serialization and round-trip
        json_text = report.to_json()
        data = json.loads(json_text)
        self.assertEqual(data["schema"], SCHEMA)
        self.assertEqual(len(data["tasks"]), 2)

        # Test summary table rendering
        table = format_summary_table(report)
        self.assertIn("PCBSchemaGen-Tier1 Dual-Gate Benchmark Summary", table)
        self.assertIn("Concordant Pass", table)
        self.assertIn("BUCK_AUX_LOGIC", table)

    def test_cli_invocation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cand_path = Path(tmp_dir) / "6.json"
            write_json(cand_path, _make_valid_tps54302_design())
            out_path = Path(tmp_dir) / "report.json"

            exit_code = main(
                [
                    "--task-id",
                    "6",
                    "--candidate",
                    str(cand_path),
                    "--out",
                    str(out_path),
                    "--summary",
                    "--quiet",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_path.is_file())

            report_data = read_json(out_path)
            self.assertEqual(report_data["schema"], SCHEMA)
            self.assertEqual(report_data["summary"]["dual_gate_passed"], 1)


if __name__ == "__main__":
    unittest.main()

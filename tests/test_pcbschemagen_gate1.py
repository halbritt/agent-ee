"""Unit tests for pcbschemagen_gate1 (Gate 1 Topo Verifier Adapter)."""

import json
from pathlib import Path
import tempfile
import unittest

from agent_ee.pcbschemagen_gate1 import (
    Gate1KGStore,
    design_to_snapshot,
    filter_headers_from_snapshot,
    is_external_header,
    main,
    netlist_to_snapshot,
    resolve_canonical_part_id,
    verify_gate1,
)


def _make_valid_tps54302_design():
    return {
        "schema": "agent-ee.design/1",
        "parts": {
            "U1": {
                "value": "TPS54302",
                "lib_id": "Power_Management:TPS54302DDR",
                "footprint": "Package_SO:SOT-23-6",
                "group": "regulator",
            },
            "C1": {
                "value": "10u",
                "lib_id": "Device:C",
                "footprint": "Capacitor_SMD:C_0805_2012Metric",
            },
            "C2": {
                "value": "100n",
                "lib_id": "Device:C",
                "footprint": "Capacitor_SMD:C_0603_1608Metric",
            },
            "C3": {
                "value": "22u",
                "lib_id": "Device:C",
                "footprint": "Capacitor_SMD:C_0805_2012Metric",
            },
            "L1": {
                "value": "4.7u",
                "lib_id": "Device:L",
                "footprint": "Inductor_SMD:L_0630",
            },
            "R1": {
                "value": "100k",
                "lib_id": "Device:R",
                "footprint": "Resistor_SMD:R_0603_1608Metric",
            },
            "R2": {
                "value": "32.4k",
                "lib_id": "Device:R",
                "footprint": "Resistor_SMD:R_0603_1608Metric",
            },
            "J1": {
                "value": "PinHeader_1x02",
                "lib_id": "Connector_Generic:Conn_01x02",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            },
            "J2": {
                "value": "PinHeader_1x02",
                "lib_id": "Connector_Generic:Conn_01x02",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            },
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
    }



class TestPCBSchemaGenGate1Translation(unittest.TestCase):
    """Test translation from agent-ee.design/1 and KiCad XML netlist into framework/topo snapshots."""

    def setUp(self):
        self.kg = Gate1KGStore()

    def test_translation_from_design_statement(self):
        design = _make_valid_tps54302_design()
        snapshot = design_to_snapshot(design, kg_store=self.kg)

        self.assertIn("components", snapshot)
        self.assertIn("nets", snapshot)

        comps = {c["ref"]: c for c in snapshot["components"]}
        self.assertIn("U1", comps)
        u1 = comps["U1"]
        self.assertEqual(u1["part_id"], "TPS54302")

        # Crucial requirement: pins must have both pin_id and pin_name
        pin_map = {p["pin_id"]: p for p in u1["pins"]}
        self.assertIn("3", pin_map)
        self.assertEqual(pin_map["3"]["pin_name"], "VIN")
        self.assertEqual(pin_map["3"]["net"], "VIN")

        self.assertIn("1", pin_map)
        self.assertEqual(pin_map["1"]["pin_name"], "GND")
        self.assertEqual(pin_map["1"]["net"], "GND")

        self.assertIn("5", pin_map)
        self.assertEqual(pin_map["5"]["pin_name"], "EN")
        self.assertEqual(pin_map["5"]["net"], "NC")

        # Connectors identified
        self.assertTrue(is_external_header("J1", comps["J1"]["part_id"]))
        self.assertTrue(is_external_header("J2", comps["J2"]["part_id"]))

        # Check nets endpoints have both pin_id and pin_name
        nets = {n["name"]: n for n in snapshot["nets"]}
        self.assertIn("VIN", nets)
        vin_eps = {ep["ref"]: ep for ep in nets["VIN"]["endpoints"]}
        self.assertIn("U1", vin_eps)
        self.assertEqual(vin_eps["U1"]["pin_id"], "3")
        self.assertEqual(vin_eps["U1"]["pin_name"], "VIN")

    def test_translation_from_kicad_xml_netlist(self):
        sample_netlist = Path(
            "build/acquisition-release/release-build-v7/stages/07-package-63a92a61a95149678ee3178e64133b22/review/work/erc/stage-subject/native-netlist.xml"
        )
        if not sample_netlist.exists():
            self.skipTest(f"Sample netlist not found at {sample_netlist}")

        snapshot = netlist_to_snapshot(sample_netlist, kg_store=self.kg)
        comps = {c["ref"]: c for c in snapshot["components"]}

        self.assertIn("U2", comps)
        u2 = comps["U2"]
        # Symbol prefix Analog_ADC: should be stripped and mapped to ADS1115IDGS
        self.assertEqual(u2["part_id"], "ADS1115IDGS")

        pin_map = {p["pin_id"]: p for p in u2["pins"]}
        self.assertIn("8", pin_map)
        self.assertEqual(pin_map["8"]["pin_name"], "VDD")
        self.assertIn("3", pin_map)
        self.assertEqual(pin_map["3"]["pin_name"], "GND")

        # Passives have "~" pin_name
        c1 = comps["C1"]
        self.assertEqual(c1["part_id"], "C")
        for p in c1["pins"]:
            self.assertEqual(p["pin_name"], "~")

    def test_canonical_part_id_resolution(self):
        self.assertEqual(
            resolve_canonical_part_id("U1", "TPS54302DDR", lib_id="Power_Management:TPS54302DDR", kg_store=self.kg),
            "TPS54302",
        )
        self.assertEqual(
            resolve_canonical_part_id("R1", "10k", lib_id="Device:R", kg_store=self.kg),
            "R",
        )
        self.assertEqual(
            resolve_canonical_part_id("J1", "Conn_01x02", lib_id="Connector_Generic:Conn_01x02", kg_store=self.kg),
            "PinHeader_1x02",
        )

    def test_filter_headers_from_snapshot(self):
        raw_snap = {
            "components": [
                {"ref": "U1", "part_id": "TPS54302"},
                {"ref": "C1", "part_id": "C"},
                {"ref": "J1", "part_id": "PinHeader_1x02"},
            ],
            "nets": [{"name": "VIN", "endpoints": [{"ref": "U1"}, {"ref": "J1"}]}],
        }
        filtered = filter_headers_from_snapshot(raw_snap)
        refs = [c["ref"] for c in filtered["components"]]
        self.assertEqual(refs, ["U1", "C1"])
        self.assertEqual(len(filtered["nets"]), 1)



class TestPCBSchemaGenGate1Verification(unittest.TestCase):
    """Test Gate 1 verification execution, rule checks, and error reporting."""

    def test_valid_circuit_passes_gate1(self):
        design = _make_valid_tps54302_design()
        result = verify_gate1(design_input=design, task_id=6, task_name="BUCK_AUX_LOGIC")

        self.assertTrue(result.passed)
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.reward, 1.0)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.report["schema"], "agent-ee.pcbschemagen-gate1/1")
        self.assertEqual(result.report["stats"]["critical_errors"], 0)

    def test_missing_required_connection_fails_gate1(self):
        # Case A: floating VIN (U1.3 removed from VIN net and not in no_connects)
        design_floating_vin = _make_valid_tps54302_design()
        design_floating_vin["nets"]["VIN"] = ["J1.1", "C1.1"]  # U1.3 missing

        result = verify_gate1(design_input=design_floating_vin, task_id=6)
        self.assertFalse(result.passed)
        self.assertEqual(result.status, "FAIL")
        self.assertLess(result.reward, 1.0)
        self.assertGreater(len(result.errors), 0)
        categories = [e["category"] for e in result.report["errors"]]
        self.assertIn("supply_missing", categories)

        # Case B: missing SW connection
        design_floating_sw = _make_valid_tps54302_design()
        design_floating_sw["nets"]["SW"] = ["L1.1", "C2.2"]  # U1.2 missing
        result_sw = verify_gate1(design_input=design_floating_sw, task_id=6)
        self.assertFalse(result_sw.passed)
        self.assertEqual(result_sw.status, "FAIL")
        sw_cats = [e["category"] for e in result_sw.report["errors"]]
        self.assertTrue("unconnected_pin" in sw_cats or "supply_missing" in sw_cats)

    def test_pin_role_mismatch_fails_gate1(self):
        # Case A: Supply short (VIN and GND on same net)
        design_shorted = _make_valid_tps54302_design()
        design_shorted["nets"]["GND"].append("U1.3")  # Connect VIN to GND net too
        design_shorted["nets"]["VIN"].remove("U1.3")

        result = verify_gate1(design_input=design_shorted, task_id=6)
        self.assertFalse(result.passed)
        self.assertEqual(result.status, "FAIL")
        categories = [e["category"] for e in result.report["errors"]]
        self.assertIn("supply_short", categories)

        # Case B: SW swapped with GND pin role
        # Normal: U1.1=GND, U1.2=SW. Swapped: U1.1=SW, U1.2=GND.
        design_swapped = _make_valid_tps54302_design()
        design_swapped["nets"]["GND"].remove("U1.1")
        design_swapped["nets"]["SW"].remove("U1.2")
        design_swapped["nets"]["GND"].append("U1.2")  # SW pin connected to GND net
        design_swapped["nets"]["SW"].append("U1.1")   # GND pin connected to SW net

        # With standard bootstrap and bypass rules, swapped SW and GND violates C_DIRECT
        rules = [
            {
                "rule_type": "C_DIRECT",
                "endpoint_a": {"component_part_id": "TPS54302", "pin_role": "buck_boot"},
                "endpoint_b": {"component_part_id": "TPS54302", "pin_role": "buck_sw"},
            },
            {
                "rule_type": "C_DIRECT",
                "endpoint_a": {"component_part_id": "TPS54302", "pin_role": "buck_vin"},
                "endpoint_b": {"component_part_id": "TPS54302", "pin_role": "buck_gnd"},
            },
        ]
        result_swapped = verify_gate1(design_input=design_swapped, task_id=6, rules=rules)
        self.assertFalse(result_swapped.passed)
        self.assertEqual(result_swapped.status, "FAIL")
        categories = [e["category"] for e in result_swapped.report["errors"]]
        self.assertIn("cap_direct_missing", categories)

    def test_external_headers_do_not_trigger_component_count_mismatch(self):
        # Create design with 5 external pin headers
        design_with_headers = _make_valid_tps54302_design()
        for idx in range(3, 7):
            ref = f"J{idx}"
            design_with_headers["parts"][ref] = {
                "value": "PinHeader_1x02",
                "lib_id": "Connector_Generic:Conn_01x02",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            }
            design_with_headers["nets"]["GND"].append(f"{ref}.1")
            design_with_headers["nets"]["VIN"].append(f"{ref}.2")

        # Standard reference snapshot with 0 pin headers
        std_snapshot = {
            "components": [
                {
                    "ref": "U1",
                    "part_id": "TPS54302",
                    "pins": [
                        {"pin_id": "1", "pin_name": "GND", "net": "GND"},
                        {"pin_id": "2", "pin_name": "SW", "net": "SW"},
                        {"pin_id": "3", "pin_name": "VIN", "net": "VIN"},
                        {"pin_id": "4", "pin_name": "FB", "net": "FB"},
                        {"pin_id": "5", "pin_name": "EN", "net": "NC"},
                        {"pin_id": "6", "pin_name": "BOOT", "net": "BOOT"},
                    ],
                },
                {"ref": "C1", "part_id": "C", "pins": [{"pin_id": "1", "net": "VIN"}, {"pin_id": "2", "net": "GND"}]},
                {"ref": "C2", "part_id": "C", "pins": [{"pin_id": "1", "net": "BOOT"}, {"pin_id": "2", "net": "SW"}]},
                {"ref": "C3", "part_id": "C", "pins": [{"pin_id": "1", "net": "VOUT"}, {"pin_id": "2", "net": "GND"}]},
                {"ref": "L1", "part_id": "L", "pins": [{"pin_id": "1", "net": "SW"}, {"pin_id": "2", "net": "VOUT"}]},
                {"ref": "R1", "part_id": "R", "pins": [{"pin_id": "1", "net": "VOUT"}, {"pin_id": "2", "net": "FB"}]},
                {"ref": "R2", "part_id": "R", "pins": [{"pin_id": "1", "net": "FB"}, {"pin_id": "2", "net": "GND"}]},
            ],
            "nets": [
                {"name": "VIN", "endpoints": [{"ref": "U1", "pin_id": "3", "pin_name": "VIN"}]},
                {"name": "GND", "endpoints": [{"ref": "U1", "pin_id": "1", "pin_name": "GND"}]},
                {"name": "SW", "endpoints": [{"ref": "U1", "pin_id": "2", "pin_name": "SW"}]},
                {"name": "BOOT", "endpoints": [{"ref": "U1", "pin_id": "6", "pin_name": "BOOT"}]},
                {"name": "FB", "endpoints": [{"ref": "U1", "pin_id": "4", "pin_name": "FB"}]},
                {"name": "VOUT", "endpoints": [{"ref": "L1", "pin_id": "2", "pin_name": "2"}]},
            ],
        }

        result = verify_gate1(
            design_input=design_with_headers,
            task_id=6,
            std_snapshot=std_snapshot,
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.status, "PASS")
        count_errors = [
            e for e in result.report["errors"] if e.get("category") == "component_count_mismatch"
        ]
        self.assertEqual(len(count_errors), 0)

    def test_cli_invocation(self):
        design = _make_valid_tps54302_design()
        with tempfile.TemporaryDirectory() as tmpdir:
            design_path = Path(tmpdir) / "design.json"
            out_path = Path(tmpdir) / "gate1_report.json"
            design_path.write_text(json.dumps(design), encoding="utf-8")

            # 1. Valid invocation returns 0
            rc = main(["--design", str(design_path), "--task-id", "6", "--out", str(out_path), "--quiet"])
            self.assertEqual(rc, 0)
            self.assertTrue(out_path.exists())
            report = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "PASS")

            # 2. Missing args returns 2
            rc_bad = main([])
            self.assertEqual(rc_bad, 2)

"""Unit and integration tests for native KiCad symbol/footprint part-binding validation."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agent_ee.baseline import compile_baseline, freeze_evidence, load_baseline, validate_witnesses, verify_evidence
from agent_ee.capabilities import describe_check
from agent_ee.candidate import capture
from agent_ee.checks import run_checks
from agent_ee.part_binding import (
    PartBindingError,
    PartBindingMalformedError,
    capability,
    parse_board_bindings,
    parse_param_value,
    parse_schematic_bindings,
    schematic_hierarchy_bindings,
    validate_part_bindings,
)
from agent_ee.storage import read_json, write_json


def _quote_sexpr(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def build_synthetic_sch(symbols: list[dict], *, extra_lib_symbols: str = "") -> bytes:
    """Build an original synthetic .kicad_sch S-expression for tests."""
    lib_Block = f"""  (lib_symbols
    (symbol "Device:R" (in_bom yes) (on_board yes)
      (property "Reference" "R" (at 0 0 0))
      (property "Value" "R" (at 0 0 0))
    )
    (symbol "Device:C" (in_bom yes) (on_board yes)
      (property "Reference" "C" (at 0 0 0))
      (property "Value" "C" (at 0 0 0))
    )
    (symbol "power:GND" (power) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 0 0))
      (property "Value" "GND" (at 0 0 0))
    )
{extra_lib_symbols}
  )"""
    sym_blocks = []
    for idx, sym in enumerate(symbols, start=1):
        lib_id = sym.get("lib_id", "Device:R")
        unit = sym.get("unit", 1)
        in_bom = "yes" if sym.get("in_bom", True) else "no"
        on_board = "yes" if sym.get("on_board", True) else "no"
        dnp = "yes" if sym.get("dnp", False) else "no"
        uuid = sym.get("uuid", f"00000000-0000-0000-0000-{idx:012d}")
        props = dict(sym.get("properties", {}))
        lines = [
            f'  (symbol (lib_id {_quote_sexpr(lib_id)}) (at 100 100 0) (unit {unit})',
            f"    (in_bom {in_bom}) (on_board {on_board}) (dnp {dnp})",
            f'    (uuid {_quote_sexpr(uuid)})',
        ]
        for k, v in props.items():
            lines.append(f"    (property {_quote_sexpr(k)} {_quote_sexpr(v)} (at 0 0 0))")
        if "instance_reference" in sym:
            inst_unit = sym.get("instance_unit", unit)
            lines.append(
                f'    (instances (project "synth" (path "/root" (reference {_quote_sexpr(sym["instance_reference"])}) (unit {inst_unit}))))'
            )
        lines.append("  )")
        sym_blocks.append("\n".join(lines))

    content = "(kicad_sch (version 20231120) (generator \"agent-ee-test\")\n"
    content += lib_Block + "\n"
    content += "\n".join(sym_blocks) + "\n)\n"
    return content.encode("utf-8")


def build_synthetic_pcb(footprints: list[dict]) -> bytes:
    """Build an original synthetic .kicad_pcb S-expression for tests."""
    fp_blocks = []
    for idx, fp in enumerate(footprints, start=1):
        tag = fp.get("tag", "footprint")
        lib_id = fp.get("lib_id", "Resistor_SMD:R_0603_1608Metric")
        uuid = fp.get("uuid", f"10000000-0000-0000-0000-{idx:012d}")
        attrs = fp.get("attrs", ["smd"])
        props = dict(fp.get("properties", {}))
        lines = [
            f'  ({tag} {_quote_sexpr(lib_id)} (layer "F.Cu")',
            f'    (uuid {_quote_sexpr(uuid)})',
        ]
        if attrs:
            lines.append("    (attr " + " ".join(attrs) + ")")
        if "dnp" in fp:
            lines.append(f"    (dnp {'yes' if fp['dnp'] else 'no'})")
        if "in_bom" in fp:
            lines.append(f"    (in_bom {'yes' if fp['in_bom'] else 'no'})")
        if "on_board" in fp:
            lines.append(f"    (on_board {'yes' if fp['on_board'] else 'no'})")
        for k, v in props.items():
            lines.append(f"    (property {_quote_sexpr(k)} {_quote_sexpr(v)} (at 0 0 0))")
        for kind, text_val in fp.get("fp_texts", []):
            lines.append(f"    (fp_text {kind} {_quote_sexpr(text_val)} (at 0 0 0))")
        lines.append("  )")
        fp_blocks.append("\n".join(lines))

    content = "(kicad_pcb (version 20240108) (generator \"agent-ee-test\")\n"
    content += "\n".join(fp_blocks) + "\n)\n"
    return content.encode("utf-8")


def valid_r1_props(**overrides) -> dict[str, str]:
    base = {
        "Reference": "R1",
        "Value": "10k",
        "Footprint": "Resistor_SMD:R_0603_1608Metric",
        "Datasheet": "https://example.invalid/r1.pdf",
        "LCSC": "C12345",
        "Manufacturer": "Yageo",
        "Partnumber": "RC0603FR-0710KL",
        "PARAM_resistance": json.dumps({
            "min": "9900",
            "max": "10100",
            "nominal": "10000",
            "unit": "ohm",
        }),
        "PARAM_voltage": json.dumps({
            "values": ["50", "25.0", "50.0"],
            "unit": "V",
        }),
    }
    base.update(overrides)
    return base


HIER_ROOT = "a0000000-0000-0000-0000-000000000000"
HIER_SHEET_A = "a0000000-0000-0000-0000-0000000000a1"
HIER_SHEET_B = "a0000000-0000-0000-0000-0000000000a2"
HIER_SHEET_C = "a0000000-0000-0000-0000-0000000000a3"
HIER_LIB = """  (lib_symbols
    (symbol "Device:R" (pin_numbers (hide yes)) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "R_0_1" (rectangle (start -1.016 -2.54) (end 1.016 2.54) (stroke (width 0.254) (type default)) (fill (type none))))
      (symbol "R_1_1"
        (pin passive line (at 0 3.81 270) (length 1.27) (name "~" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -3.81 90) (length 1.27) (name "~" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
      )
    )
  )"""


def _hier_symbol(uuid: str, props: dict[str, str], paths: list[tuple[str, str]], x: int = 50) -> str:
    lines = [
        f'  (symbol (lib_id "Device:R") (at {x} 50 0) (unit 1) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)',
        f"    (uuid {_quote_sexpr(uuid)})",
    ]
    for k, v in props.items():
        lines.append(
            f"    (property {_quote_sexpr(k)} {_quote_sexpr(v)} (at {x} 50 0) (effects (font (size 1.27 1.27)) (hide yes)))"
        )
    lines.append('    (instances (project "hier"')
    for path, ref in paths:
        lines.append(f"      (path {_quote_sexpr(path)} (reference {_quote_sexpr(ref)}) (unit 1))")
    lines.append("    ))")
    lines.append("  )")
    return "\n".join(lines)


def _hier_sheet(uuid: str, name: str, sheetfile: str, y: int) -> str:
    return (
        f"  (sheet (at 100 {y}) (size 20 10) (fields_autoplaced yes) (stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0000))\n"
        f"    (uuid {_quote_sexpr(uuid)})\n"
        f"    (property \"Sheetname\" {_quote_sexpr(name)} (at 100 {y} 0) (effects (font (size 1.27 1.27)) (justify left bottom)))\n"
        f"    (property \"Sheetfile\" {_quote_sexpr(sheetfile)} (at 100 {y + 11} 0) (effects (font (size 1.27 1.27)) (justify left top)))\n"
        f"    (instances (project \"hier\" (path {_quote_sexpr('/' + HIER_ROOT)} (page {_quote_sexpr(name)}))))\n"
        f"  )"
    )


def _hier_doc(uuid: str, body: str) -> bytes:
    return (
        '(kicad_sch (version 20231120) (generator "eeschema") (generator_version "8.0")\n'
        f"  (uuid {_quote_sexpr(uuid)})\n  (paper \"A4\")\n{HIER_LIB}\n{body}\n"
        '  (sheet_instances (path "/" (page "1")))\n)\n'
    ).encode("utf-8")


def hier_binding(lcsc: str = "C25804") -> dict[str, str]:
    return {
        "Value": "10k",
        "Footprint": "Resistor_SMD:R_0603_1608Metric",
        "Datasheet": "~",
        "LCSC": lcsc,
        "Manufacturer": "Yageo",
        "Partnumber": "RC0603FR-0710KL",
    }


def build_hierarchy(r30_props: dict[str, str] | None = None, *, chan_paths=None) -> dict[str, bytes]:
    """Original hierarchy: root R1, sheet file sub/chan.kicad_sch reused twice (R10, R20), other.kicad_sch (R30)."""
    root = _hier_doc(HIER_ROOT, "\n".join((
        _hier_symbol("b0000000-0000-0000-0000-000000000001", {"Reference": "R1", **hier_binding()},
                     [("/" + HIER_ROOT, "R1")]),
        _hier_sheet(HIER_SHEET_A, "ChanA", "sub/chan.kicad_sch", 20),
        _hier_sheet(HIER_SHEET_B, "ChanB", "sub/chan.kicad_sch", 60),
        _hier_sheet(HIER_SHEET_C, "Other", "other.kicad_sch", 100),
    )))
    if chan_paths is None:
        chan_paths = [(f"/{HIER_ROOT}/{HIER_SHEET_A}", "R10"), (f"/{HIER_ROOT}/{HIER_SHEET_B}", "R20")]
    chan = _hier_doc("c0000000-0000-0000-0000-000000000001", _hier_symbol(
        "b0000000-0000-0000-0000-000000000002", {"Reference": "R10", **hier_binding()}, chan_paths))
    other = _hier_doc("c0000000-0000-0000-0000-000000000002", _hier_symbol(
        "b0000000-0000-0000-0000-000000000003",
        {"Reference": "R30", **(r30_props if r30_props is not None else {"Value": "1k"})},
        [(f"/{HIER_ROOT}/{HIER_SHEET_C}", "R30")]))
    return {"hier.kicad_sch": root, "sub/chan.kicad_sch": chan, "other.kicad_sch": other}


def hier_board(refs=("R1", "R10", "R20", "R30")) -> bytes:
    return build_synthetic_pcb([
        {"lib_id": "Resistor_SMD:R_0603_1608Metric",
         "properties": {"Reference": ref, **{k: v for k, v in hier_binding().items() if k != "Footprint"}}}
        for ref in refs
    ])


def _toolchain_required(test, tool):
    if shutil.which(tool):
        return
    if os.environ.get("AGENT_EE_REQUIRE_TOOLCHAIN") == "1":
        raise AssertionError(f"AGENT_EE_REQUIRE_TOOLCHAIN=1: {tool} is not on PATH")
    test.skipTest(f"{tool} is not on PATH")


class PartBindingTests(unittest.TestCase):
    def test_1_valid_symbol_and_footprint_passes(self):
        props = valid_r1_props()
        sch = build_synthetic_sch([{"lib_id": "Device:R", "properties": props}])
        pcb_props = {k: v for k, v in props.items() if k != "Footprint"}
        pcb = build_synthetic_pcb([
            {"lib_id": "Resistor_SMD:R_0603_1608Metric", "properties": pcb_props}
        ])

        sch_map = parse_schematic_bindings(sch)
        pcb_map = parse_board_bindings(pcb)
        self.assertIn("R1", sch_map)
        self.assertIn("R1", pcb_map)
        self.assertEqual(
            sch_map["R1"]["params"]["PARAM_resistance"],
            {"min": "9900", "max": "10100", "nominal": "10000", "unit": "ohm"},
        )
        self.assertEqual(
            sch_map["R1"]["params"]["PARAM_voltage"],
            {"min": "25", "max": "50", "values": ["25", "50"], "unit": "V"},
        )

        report = validate_part_bindings(
            sch, pcb, require_datasheet=True, require_params=True
        )
        self.assertEqual(report["execution"], "ok")
        self.assertEqual(report["outcome"], "pass")
        self.assertEqual(report["evaluated_references"], ["R1"])
        self.assertEqual(report["missing_bindings"], [])
        self.assertEqual(report["invalid_bindings"], [])
        self.assertEqual(report["disagreements"], [])

    def test_2_dnp_no_lcsc_and_virtual_exclusion(self):
        r1 = valid_r1_props()
        r_dnp = {
            "Reference": "R2",
            "Value": "4.7k",
            "Footprint": "Resistor_SMD:R_0603_1608Metric",
            "Datasheet": "~",
        }
        r_dnp.update(Manufacturer="Yageo", Partnumber="RC0603FR-074K7L")
        j_nolcsc = {
            "Reference": "J1",
            "Value": "CONN_2",
            "Footprint": "Connector:PinHeader_1x02",
            "LCSC": "NO_LCSC",
            "Manufacturer": "Generic",
            "Partnumber": "HDR-1x02-2.54",
        }
        tp_nolcsc_prop = {
            "Reference": "TP1",
            "Value": "TestPoint",
            "Footprint": "TestPoint:TP_Pad",
            "No_LCSC": "true",
            "Manufacturer": "Board copper",
            "Partnumber": "TP_PAD",
        }
        pwr = {
            "Reference": "#PWR01",
            "Value": "GND",
            "Footprint": "",
        }
        flg = {
            "Reference": "#FLG01",
            "Value": "PWR_FLAG",
            "Footprint": "",
        }
        not_on_board = {
            "Reference": "M1",
            "Value": "MECH_NOTE",
            "Footprint": "",
        }

        sch = build_synthetic_sch([
            {"lib_id": "Device:R", "properties": r1},
            {"lib_id": "Device:R", "dnp": True, "properties": r_dnp},
            {"lib_id": "Device:R", "properties": j_nolcsc},
            {"lib_id": "Device:R", "properties": tp_nolcsc_prop},
            {"lib_id": "power:GND", "properties": pwr},
            {"lib_id": "power:GND", "properties": flg},
            {"lib_id": "Device:R", "on_board": False, "properties": not_on_board},
        ])
        pcb = build_synthetic_pcb([
            {
                "lib_id": "Resistor_SMD:R_0603_1608Metric",
                "properties": {k: v for k, v in r1.items() if k != "Footprint"},
            },
            {
                "lib_id": "Resistor_SMD:R_0603_1608Metric",
                "attrs": ["smd", "dnp"],
                "properties": {k: v for k, v in r_dnp.items() if k != "Footprint"},
            },
            {
                "lib_id": "Connector:PinHeader_1x02",
                "properties": {k: v for k, v in j_nolcsc.items() if k != "Footprint"},
            },
            {
                "lib_id": "TestPoint:TP_Pad",
                "properties": {k: v for k, v in tp_nolcsc_prop.items() if k != "Footprint"},
            },
            {
                "lib_id": "Fiducial:Fiducial_1mm",
                "attrs": ["smd", "virtual"],
                "properties": {"Reference": "FID1", "Value": "Fiducial"},
            },
            {
                "lib_id": "Symbol:Logo",
                "attrs": ["board_only"],
                "fp_texts": [("reference", "REF**"), ("value", "LOGO")],
            },
        ])

        report = validate_part_bindings(sch, pcb)
        self.assertEqual((report["execution"], report["outcome"]), ("ok", "pass"), report)
        self.assertEqual(report["evaluated_references"], ["J1", "R1", "R2", "TP1"])
        self.assertEqual(report["exempt_references"], ["J1", "R2", "TP1"])
        self.assertIn("#PWR01", report["virtual_references"])
        self.assertIn("#FLG01", report["virtual_references"])
        self.assertIn("FID1", report["virtual_references"])
        self.assertIn("REF**", report["virtual_references"])
        self.assertIn("M1", report["virtual_references"])

        # Virtual-only design returns outcome="info"
        sch_virtual_only = build_synthetic_sch([
            {"lib_id": "power:GND", "properties": pwr}
        ])
        pcb_virtual_only = build_synthetic_pcb([
            {
                "lib_id": "Fiducial:Fiducial_1mm",
                "attrs": ["virtual"],
                "properties": {"Reference": "FID1", "Value": "Fiducial"},
            }
        ])
        info_report = validate_part_bindings(sch_virtual_only, pcb_virtual_only)
        self.assertEqual((info_report["execution"], info_report["outcome"]), ("ok", "info"))

    def test_3_malformed_param_values_and_sexpr(self):
        bad_param_cases = [
            ("unparseable_json", "{not_json"),
            ("duplicate_keys", '{"min": "1", "min": "2", "max": "3", "unit": "V"}'),
            ("missing_unit", '{"min": "1", "max": "2"}'),
            ("empty_unit", '{"min": "1", "max": "2", "unit": "   "}'),
            ("reversed_bounds", '{"min": "10", "max": "5", "unit": "ohm"}'),
            ("nominal_out_of_range", '{"min": "1", "max": "5", "nominal": "6", "unit": "V"}'),
            ("nan_constant", '{"min": NaN, "max": 5, "unit": "V"}'),
            ("infinity_constant", '{"min": 1, "max": Infinity, "unit": "V"}'),
            ("boolean_bound", '{"min": true, "max": 5, "unit": "V"}'),
            ("empty_values_set", '{"values": [], "unit": "V"}'),
            ("mixed_interval_and_set", '{"min": "1", "max": "2", "values": ["1"], "unit": "V"}'),
            ("missing_max", '{"min": "1", "unit": "V"}'),
        ]
        for label, raw in bad_param_cases:
            with self.subTest(case=label):
                with self.assertRaises(PartBindingMalformedError):
                    parse_param_value(raw, "PARAM_test")
                sch = build_synthetic_sch([
                    {"lib_id": "Device:R", "properties": valid_r1_props(PARAM_resistance=raw)}
                ])
                report = validate_part_bindings(sch)
                self.assertEqual(
                    (report["execution"], report["outcome"]),
                    ("malformed", "unknown"),
                    f"case {label} did not produce malformed/unknown: {report}",
                )
                self.assertTrue(report["errors"])

        # Unparseable S-expression and wrong root tag
        for bad_bytes in (b"(kicad_sch (unclosed", b"(wrong_root )"):
            with self.assertRaises(PartBindingMalformedError):
                parse_schematic_bindings(bad_bytes)
            rep = validate_part_bindings(sch_bytes=bad_bytes)
            self.assertEqual((rep["execution"], rep["outcome"]), ("malformed", "unknown"))

    def test_4_schematic_vs_board_disagreements_and_multi_unit(self):
        base_props = valid_r1_props()
        base_pcb_props = {k: v for k, v in base_props.items() if k != "Footprint"}

        mutations = [
            ("LCSC", {"LCSC": "C99999"}),
            ("Manufacturer", {"Manufacturer": "Murata"}),
            ("Partnumber", {"Partnumber": "OTHER-MPN-1"}),
            ("Datasheet", {"Datasheet": "https://example.invalid/other.pdf"}),
            ("Value", {"Value": "47k"}),
            (
                "PARAM_resistance",
                {
                    "PARAM_resistance": json.dumps(
                        {"min": "46000", "max": "48000", "nominal": "47000", "unit": "ohm"}
                    )
                },
            ),
        ]
        sch = build_synthetic_sch([{"lib_id": "Device:R", "properties": base_props}])
        for field_name, override in mutations:
            with self.subTest(field=field_name):
                mut_pcb_props = {**base_pcb_props, **override}
                pcb = build_synthetic_pcb([
                    {"lib_id": "Resistor_SMD:R_0603_1608Metric", "properties": mut_pcb_props}
                ])
                report = validate_part_bindings(sch, pcb)
                self.assertEqual((report["execution"], report["outcome"]), ("ok", "defect"))
                fields = [d["field"] for d in report["disagreements"]]
                self.assertIn(field_name, fields)

        # Footprint disagreement
        pcb_wrong_fp = build_synthetic_pcb([
            {"lib_id": "Resistor_SMD:R_0805_2012Metric", "properties": base_pcb_props}
        ])
        fp_report = validate_part_bindings(sch, pcb_wrong_fp)
        self.assertEqual((fp_report["execution"], fp_report["outcome"]), ("ok", "defect"))
        self.assertIn("Footprint", [d["field"] for d in fp_report["disagreements"]])

        # DNP disagreement
        pcb_dnp = build_synthetic_pcb([
            {
                "lib_id": "Resistor_SMD:R_0603_1608Metric",
                "attrs": ["smd", "dnp"],
                "properties": base_pcb_props,
            }
        ])
        dnp_report = validate_part_bindings(sch, pcb_dnp)
        self.assertEqual((dnp_report["execution"], dnp_report["outcome"]), ("ok", "defect"))
        self.assertIn("dnp", [d["field"] for d in dnp_report["disagreements"]])

        # Component missing from board parity defect
        empty_pcb = build_synthetic_pcb([])
        parity_report = validate_part_bindings(sch, empty_pcb)
        self.assertEqual((parity_report["execution"], parity_report["outcome"]), ("ok", "defect"))
        self.assertIn("presence", [d["field"] for d in parity_report["disagreements"]])

        # Multi-unit symbol disagreement inside schematic
        u1a = valid_r1_props(Reference="U1", LCSC="C12345")
        u1b = valid_r1_props(Reference="U1", LCSC="C54321")
        multi_sch = build_synthetic_sch([
            {"lib_id": "Device:R", "unit": 1, "properties": u1a},
            {"lib_id": "Device:R", "unit": 2, "properties": u1b},
        ])
        multi_report = validate_part_bindings(multi_sch)
        self.assertEqual((multi_report["execution"], multi_report["outcome"]), ("ok", "defect"))
        self.assertIn("LCSC", [d["field"] for d in multi_report["disagreements"]])

    def test_5_missing_binding_properties_yield_unknown_never_pass(self):
        for missing_key in ("LCSC", "Manufacturer", "Partnumber"):
            with self.subTest(missing=missing_key):
                props = valid_r1_props(**{missing_key: ""})
                sch = build_synthetic_sch([{"lib_id": "Device:R", "properties": props}])
                pcb_props = {k: v for k, v in props.items() if k != "Footprint"}
                pcb = build_synthetic_pcb([
                    {"lib_id": "Resistor_SMD:R_0603_1608Metric", "properties": pcb_props}
                ])
                report = validate_part_bindings(sch, pcb)
                self.assertEqual(report["execution"], "ok")
                self.assertEqual(report["outcome"], "unknown")
                self.assertNotEqual(report["outcome"], "pass")
                missing_fields = [m["property"] for m in report["missing_bindings"]]
                self.assertIn(missing_key, missing_fields)

    def test_6_invalid_lcsc_format_yields_defect(self):
        for bad_lcsc in ("12345", "C0", "C0123", "c12345", "C-12", "invalid"):
            with self.subTest(lcsc=bad_lcsc):
                props = valid_r1_props(LCSC=bad_lcsc)
                sch = build_synthetic_sch([{"lib_id": "Device:R", "properties": props}])
                pcb_props = {k: v for k, v in props.items() if k != "Footprint"}
                pcb = build_synthetic_pcb([
                    {"lib_id": "Resistor_SMD:R_0603_1608Metric", "properties": pcb_props}
                ])
                report = validate_part_bindings(sch, pcb)
                self.assertEqual((report["execution"], report["outcome"]), ("ok", "defect"))
                self.assertTrue(report["invalid_bindings"])
                self.assertEqual(report["invalid_bindings"][0]["value"], bad_lcsc)

    def test_7_end_to_end_run_checks_compile_baseline_and_describe_check(self):
        def run_candidate(sch_bytes: bytes, pcb_bytes: bytes, check_params: dict | None = None):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            root = Path(tmp.name)
            (root / "design.kicad_sch").write_bytes(sch_bytes)
            (root / "design.kicad_pcb").write_bytes(pcb_bytes)
            (root / "requirements.md").write_text(
                "All non-virtual components must carry valid, matching part-binding properties.\n",
                encoding="utf-8",
            )
            paths = ["design.kicad_sch", "design.kicad_pcb", "requirements.md"]
            spec = {
                "id": "bindings",
                "kind": "part_binding",
                "params": check_params or {"require_datasheet": True, "require_params": True},
            }
            scope = {
                "schema": "agent-ee.scope/3",
                "schematic": "design.kicad_sch",
                "board": "design.kicad_pcb",
                "inputs": [{"path": p, "at": p} for p in paths],
                "checks": [spec],
                "requirements": ["requirements.md"],
                "baseline": {
                    "requirements": [
                        {
                            "id": "REQ_BINDING",
                            "statement": "Every component has valid LCSC, Manufacturer, Partnumber, and PARAM_* bindings.",
                            "source": {"path": "requirements.md", "locator": "L1"},
                            "checks": ["bindings"],
                            "intent": "acceptance",
                        }
                    ],
                    "targets": ["requirements.md"],
                    "reference_architectures": ["requirements.md"],
                },
            }
            files_map = dict.fromkeys(paths, "hash")
            desc = describe_check(
                spec, check_specs=[spec], files=files_map, scope=scope
            )
            self.assertEqual(desc["status"], "supported")
            self.assertTrue(desc["can_evaluate_bound"])
            self.assertEqual(
                desc["predicates"], ["part-binding:schema/1", "part-binding:parity/1"]
            )

            compiled = compile_baseline(scope, files_map)
            self.assertEqual(compiled["schema"], "agent-ee.baseline/2")
            self.assertNotIn("design.kicad_sch", compiled["files"])
            self.assertNotIn("design.kicad_pcb", compiled["files"])

            write_json(root / "scope.json", scope)
            run_dir = root / "run"
            run_dir.mkdir()
            capture(root / "scope.json", run_dir)
            results = run_checks(run_dir, time.monotonic() + 30)
            return run_dir, results[0]

        # 1. Passing candidate
        props = valid_r1_props()
        sch_ok = build_synthetic_sch([{"lib_id": "Device:R", "properties": props}])
        pcb_ok = build_synthetic_pcb([
            {
                "lib_id": "Resistor_SMD:R_0603_1608Metric",
                "properties": {k: v for k, v in props.items() if k != "Footprint"},
            }
        ])
        run_ok, res_ok = run_candidate(sch_ok, pcb_ok)
        self.assertEqual((res_ok["execution"], res_ok["outcome"]), ("ok", "pass"))
        self.assertIn("work/bindings/part_binding.json", res_ok["evidence"])
        self.assertTrue((run_ok / "work/bindings/part_binding.json").is_file())
        freeze_evidence(run_ok, [res_ok])
        verify_evidence(run_ok, [res_ok], required=True)

        # 2. Defect candidate (board LCSC mismatch) + witness validation
        pcb_defect = build_synthetic_pcb([
            {
                "lib_id": "Resistor_SMD:R_0603_1608Metric",
                "properties": {
                    **{k: v for k, v in props.items() if k != "Footprint"},
                    "LCSC": "C99999",
                },
            }
        ])
        run_def, res_def = run_candidate(sch_ok, pcb_defect)
        self.assertEqual((res_def["execution"], res_def["outcome"]), ("ok", "defect"))
        self.assertNotIn("design.kicad_sch", load_baseline(run_def)["files"])
        freeze_evidence(run_def, [res_def])
        validate_witnesses(
            run_def,
            [{"requirement_id": "REQ_BINDING", "check_id": "bindings"}],
            [{"path": "checks/bindings.json", "locator": "disagreements"}],
        )

    def test_8_footprint_missing_properties_is_unknown_for_that_reference(self):
        props = valid_r1_props()
        sch = build_synthetic_sch([
            {"lib_id": "Device:R", "properties": props},
            {"lib_id": "Device:R", "properties": valid_r1_props(Reference="R2")},
        ])
        bare = {"Reference": "R1", "Value": "10k", "Datasheet": props["Datasheet"]}
        pcb = build_synthetic_pcb([
            {"lib_id": "Resistor_SMD:R_0603_1608Metric", "properties": bare},
            {"lib_id": "Resistor_SMD:R_0603_1608Metric",
             "properties": {k: v for k, v in valid_r1_props(Reference="R2").items() if k != "Footprint"}},
        ])
        report = validate_part_bindings(sch, pcb)
        self.assertEqual((report["execution"], report["outcome"]), ("ok", "unknown"), report["disagreements"])
        self.assertEqual(report["reference_outcomes"], {"R1": "unknown", "R2": "pass"})
        self.assertEqual(report["disagreements"], [])
        board_missing = {m["property"] for m in report["missing_bindings"] if m["source"] == "board"}
        self.assertEqual(board_missing, {"LCSC", "Manufacturer", "Partnumber", "PARAM_resistance", "PARAM_voltage"})

        # Board-only input: the bare footprint is unknown, never pass.
        board_only = validate_part_bindings(pcb_bytes=pcb)
        self.assertEqual(board_only["outcome"], "unknown")
        self.assertEqual(board_only["reference_outcomes"]["R1"], "unknown")

        # A defect elsewhere makes the run a defect, while R1 stays unknown.
        pcb_mixed = build_synthetic_pcb([
            {"lib_id": "Resistor_SMD:R_0603_1608Metric", "properties": bare},
            {"lib_id": "Resistor_SMD:R_0603_1608Metric",
             "properties": {**{k: v for k, v in valid_r1_props(Reference="R2").items() if k != "Footprint"},
                            "LCSC": "C99999"}},
        ])
        mixed = validate_part_bindings(sch, pcb_mixed)
        self.assertEqual(mixed["outcome"], "defect")
        self.assertEqual(mixed["reference_outcomes"], {"R1": "unknown", "R2": "defect"})

    def test_9_marker_replaces_lcsc_only(self):
        for marker in ({"LCSC": "NO_LCSC"}, {"No_LCSC": "yes"}, {"LCSC": "DNP"}):
            with self.subTest(marker=marker):
                props = {"Reference": "J1", "Value": "CONN", "Footprint": "Connector:X", **marker}
                sch = build_synthetic_sch([{"lib_id": "Device:R", "properties": props}])
                report = validate_part_bindings(sch)
                self.assertEqual(report["outcome"], "unknown")
                self.assertEqual({m["property"] for m in report["missing_bindings"]},
                                 {"Manufacturer", "Partnumber"})
                bound = build_synthetic_sch([{"lib_id": "Device:R", "properties": {
                    **props, "Manufacturer": "Generic", "Partnumber": "CONN-1"}}])
                self.assertEqual(validate_part_bindings(bound)["outcome"], "pass")

        # Native DNP stands in for LCSC too, and must agree natively across sides.
        dnp_props = valid_r1_props(LCSC="")
        sch = build_synthetic_sch([{"lib_id": "Device:R", "dnp": True, "properties": dnp_props}])
        pcb_attr = build_synthetic_pcb([{"lib_id": "Resistor_SMD:R_0603_1608Metric", "attrs": ["smd", "dnp"],
                                         "properties": {k: v for k, v in dnp_props.items() if k != "Footprint"}}])
        self.assertEqual(validate_part_bindings(sch, pcb_attr)["outcome"], "pass")
        pcb_prop = build_synthetic_pcb([{"lib_id": "Resistor_SMD:R_0603_1608Metric",
                                         "properties": {**{k: v for k, v in dnp_props.items() if k != "Footprint"},
                                                        "DNP": "yes"}}])
        authored = validate_part_bindings(sch, pcb_prop)
        self.assertEqual(authored["outcome"], "defect")
        self.assertEqual([d["field"] for d in authored["disagreements"]], ["native_dnp"])

    def test_10_duplicate_and_unannotated_references(self):
        dup = build_synthetic_sch([
            {"lib_id": "Device:R", "properties": valid_r1_props()},
            {"lib_id": "Device:R", "properties": valid_r1_props()},
        ])
        report = validate_part_bindings(dup)
        self.assertEqual(report["outcome"], "defect")
        self.assertIn("reference", [d["field"] for d in report["disagreements"]])

        unannotated = build_synthetic_sch([
            {"lib_id": "Device:R", "properties": valid_r1_props(Reference="R?")},
            {"lib_id": "Device:R", "properties": valid_r1_props(Reference="R?")},
        ])
        report = validate_part_bindings(unannotated)
        self.assertEqual((report["execution"], report["outcome"]), ("ok", "unknown"))
        self.assertEqual([m["property"] for m in report["missing_bindings"]], ["Reference"])

    def test_11_hierarchical_schematic_occurrences(self):
        files = build_hierarchy()
        root = files.pop("hier.kicad_sch")
        bindings, visited = schematic_hierarchy_bindings(root, path="hier.kicad_sch", sheets=files)
        self.assertEqual(sorted(bindings), ["R1", "R10", "R20", "R30"])
        self.assertEqual(visited, ["hier.kicad_sch", "other.kicad_sch", "sub/chan.kicad_sch"])
        self.assertEqual(bindings["R20"]["sheet"], "sub/chan.kicad_sch")
        self.assertEqual(bindings["R20"]["instance_path"], f"/{HIER_ROOT}/{HIER_SHEET_B}")

        # R30 lives in a child sheet without bindings: unknown, never pass.
        report = validate_part_bindings(root, schematic_path="hier.kicad_sch", sheets=files)
        self.assertEqual((report["execution"], report["outcome"]), ("ok", "unknown"))
        self.assertEqual(report["reference_outcomes"]["R30"], "unknown")

        bound = build_hierarchy({"Value": "10k", **hier_binding()})
        bound_root = bound.pop("hier.kicad_sch")
        ok = validate_part_bindings(bound_root, hier_board(), schematic_path="hier.kicad_sch", sheets=bound)
        self.assertEqual((ok["execution"], ok["outcome"]), ("ok", "pass"), ok["disagreements"])
        self.assertEqual(ok["evaluated_references"], ["R1", "R10", "R20", "R30"])

        # Board parity across reused-sheet occurrences.
        missing_r20 = validate_part_bindings(bound_root, hier_board(("R1", "R10", "R30")),
                                             schematic_path="hier.kicad_sch", sheets=bound)
        self.assertEqual(missing_r20["outcome"], "defect")
        self.assertEqual([(d["reference"], d["field"]) for d in missing_r20["disagreements"]], [("R20", "presence")])

        # Uncaptured child sheet, unmatched occurrence, unsafe path and cycle are malformed.
        no_child = dict(bound)
        del no_child["other.kicad_sch"]
        unmatched = build_hierarchy(chan_paths=[(f"/{HIER_ROOT}/{HIER_SHEET_A}", "R10")])
        unmatched_root = unmatched.pop("hier.kicad_sch")
        escaping = bound_root.replace(b'"Sheetfile" "other.kicad_sch"', b'"Sheetfile" "../other.kicad_sch"')
        cyclic = dict(bound)
        cyclic["other.kicad_sch"] = cyclic["other.kicad_sch"].replace(
            b'(sheet_instances', bytes(_hier_sheet("d0000000-0000-0000-0000-000000000001", "Loop",
                                                   "other.kicad_sch", 140), "utf-8") + b"\n  (sheet_instances")
        for label, (sch, sheets, needle) in {
            "uncaptured": (bound_root, no_child, "not captured"),
            "unmatched": (unmatched_root, unmatched, "no (instances ...) reference"),
            "escaping": (escaping, bound, "outside the captured inputs"),
            "cycle": (bound_root, cyclic, "cycle"),
        }.items():
            with self.subTest(case=label):
                rep = validate_part_bindings(sch, schematic_path="hier.kicad_sch", sheets=sheets)
                self.assertEqual((rep["execution"], rep["outcome"]), ("malformed", "unknown"))
                self.assertIn(needle, rep["errors"][0])

    def test_12_hierarchy_references_match_kicad_cli_netlist(self):
        _toolchain_required(self, "kicad-cli")
        files = build_hierarchy({"Value": "10k", **hier_binding()})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, data in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(data)
            subprocess.run(
                ["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
                 "-o", str(root / "design.xml"), str(root / "hier.kicad_sch")],
                check=True, capture_output=True, timeout=120,
            )
            import xml.etree.ElementTree as ET
            native = sorted(c.get("ref") for c in ET.parse(root / "design.xml").getroot().iter("comp"))
        root_bytes = files.pop("hier.kicad_sch")
        parsed = parse_schematic_bindings(root_bytes, path="hier.kicad_sch", sheets=files)
        self.assertEqual(sorted(parsed), native)
        self.assertEqual(native, ["R1", "R10", "R20", "R30"])

    def test_13_end_to_end_hierarchical_capture(self):
        def run(files, pcb_bytes):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            root = Path(tmp.name)
            for rel, data in {**files, "design.kicad_pcb": pcb_bytes}.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(data)
            (root / "requirements.md").write_text("Bindings hold across every sheet.\n", encoding="utf-8")
            paths = [*files, "design.kicad_pcb", "requirements.md"]
            spec = {"id": "bindings", "kind": "part_binding", "params": {}}
            scope = {
                "schema": "agent-ee.scope/3",
                "schematic": "hier.kicad_sch",
                "board": "design.kicad_pcb",
                "inputs": [{"path": p, "at": p} for p in paths],
                "checks": [spec],
                "requirements": ["requirements.md"],
                "baseline": {
                    "requirements": [{
                        "id": "REQ_BINDING",
                        "statement": "Every component carries a valid part binding.",
                        "source": {"path": "requirements.md", "locator": "L1"},
                        "checks": ["bindings"],
                        "intent": "acceptance",
                    }],
                    "targets": ["requirements.md"],
                    "reference_architectures": ["requirements.md"],
                },
            }
            write_json(root / "scope.json", scope)
            run_dir = root / "run"
            run_dir.mkdir()
            capture(root / "scope.json", run_dir)
            return run_dir, run_checks(run_dir, time.monotonic() + 30)[0]

        run_ok, res_ok = run(build_hierarchy({"Value": "10k", **hier_binding()}), hier_board())
        self.assertEqual((res_ok["execution"], res_ok["outcome"]), ("ok", "pass"), res_ok["summary"])
        evidence = read_json(run_ok / "work/bindings/part_binding.json")
        self.assertEqual([s["path"] for s in evidence["inputs"]["sheets"]], ["other.kicad_sch", "sub/chan.kicad_sch"])
        freeze_evidence(run_ok, [res_ok])
        verify_evidence(run_ok, [res_ok], required=True)

        _, res_unbound = run(build_hierarchy({"Value": "10k", "Footprint": "Resistor_SMD:R_0603_1608Metric"}),
                             hier_board())
        self.assertEqual((res_unbound["execution"], res_unbound["outcome"]), ("ok", "unknown"))

        partial = build_hierarchy({"Value": "10k", **hier_binding()})
        del partial["other.kicad_sch"]
        _, res_partial = run(partial, hier_board())
        self.assertEqual((res_partial["execution"], res_partial["outcome"]), ("malformed", "unknown"))


if __name__ == "__main__":
    unittest.main()

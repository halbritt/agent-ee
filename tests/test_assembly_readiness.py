"""Tests for AGENTEE-29 JLC assembly-readiness check (`src/agent_ee/assembly_readiness.py`)."""

import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_ee.assembly_readiness import (
    AssemblyReadinessExecutionError,
    evaluate_assembly_readiness,
    extract_schematic_components,
    load_catalog_snapshot,
)
from agent_ee.baseline import compile_baseline
from agent_ee.candidate import capture
from agent_ee.capabilities import describe_check
from agent_ee.checks import run_checks
from agent_ee.storage import digest, read_json, write_json


def _build_symbol_sexpr(
    ref,
    lib_id,
    *,
    value="10k",
    footprint="Resistor_SMD:R_0603_1608Metric",
    lcsc="C25804",
    manufacturer="UNI-ROYAL",
    part_prop=None,
    rating_prop=("Power", "0.1W"),
    in_bom=True,
    on_board=True,
    dnp=False,
    unit=1,
    extra_props=None,
):
    props = [
        ("Reference", ref),
        ("Value", value),
    ]
    if footprint is not None:
        props.append(("Footprint", footprint))
    if lcsc is not None:
        props.append(("LCSC", lcsc))
    if manufacturer is not None:
        props.append(("Manufacturer", manufacturer))
    if part_prop is not None:
        props.append(part_prop)
    if rating_prop is not None:
        props.append(rating_prop)
    for k, v in (extra_props or {}).items():
        props.append((k, v))

    prop_lines = "\n".join(
        f'    (property "{k}" "{v}" (at 0 0 0) (effects (font (size 1.27 1.27))))'
        for k, v in props
    )
    dnp_line = f"\n    (dnp {'yes' if dnp else 'no'})" if dnp else ""
    return (
        f'  (symbol (lib_id "{lib_id}") (at 10 10 0) (unit {unit})\n'
        f'    (in_bom {"yes" if in_bom else "no"}) (on_board {"yes" if on_board else "no"}){dnp_line}\n'
        f'    (uuid "uuid-{ref}-{unit}")\n'
        f"{prop_lines}\n"
        f"  )"
    )


def _build_schematic_text(symbols_sexpr, *, power_lib_ids=("power:GND", "power:+9V", "power:PWR_FLAG"), sheets=()):
    lib_sym_blocks = []
    for pid in power_lib_ids:
        lib_sym_blocks.append(f'    (symbol "{pid}" (power) (pin_names (offset 0)))')
    for std_id in ("Device:R", "Device:C", "Device:Q_NPN_BCE", "Connector:Conn_01x02", "Mechanical:MountingHole"):
        lib_sym_blocks.append(f'    (symbol "{std_id}" (pin_names (offset 1.016)))')
    sheet_blocks = []
    for sheet_name, sheet_file, sheet_uuid in sheets:
        sheet_blocks.append(
            f'  (sheet (at 20 20) (size 10 10)\n'
            f'    (uuid "{sheet_uuid}")\n'
            f'    (property "Sheetname" "{sheet_name}" (at 20 20 0))\n'
            f'    (property "Sheetfile" "{sheet_file}" (at 20 22 0))\n'
            f"  )"
        )
    return (
        "(kicad_sch (version 20231120) (generator \"eeschema\")\n"
        "  (uuid \"00000000-0000-0000-0000-000000000001\")\n"
        "  (lib_symbols\n"
        + "\n".join(lib_sym_blocks)
        + "\n  )\n"
        + "\n".join(symbols_sexpr)
        + ("\n" + "\n".join(sheet_blocks) if sheet_blocks else "")
        + "\n)\n"
    )


def _write_sqlite_catalog(dir_path: Path, parts_rows, *, snapshot_date="2026-10-15", stale_before="2026-10-08"):
    db_path = dir_path / "catalog.sqlite"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE component_base ("
            "lcsc TEXT PRIMARY KEY, mfr TEXT, mpn TEXT, package TEXT, category TEXT, "
            "stock INTEGER, price1 REAL, is_basic INTEGER, is_preferred INTEGER, "
            "last_seen TEXT, attributes TEXT, normalized TEXT, provenance TEXT)"
        )
        for row in parts_rows:
            conn.execute(
                "INSERT INTO component_base VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["lcsc"],
                    row.get("mfr", "GenericMfr"),
                    row.get("mpn", f"MPN-{row['lcsc']}"),
                    row.get("package", "0603"),
                    row.get("category", "Passives"),
                    row.get("stock", 500),
                    row.get("price1", 0.01),
                    1 if row.get("is_basic", True) else 0,
                    1 if row.get("is_preferred", False) else 0,
                    row.get("last_seen", snapshot_date),
                    "{}",
                    "{}",
                    "{}",
                ),
            )
        conn.commit()
    finally:
        conn.close()

    manifest_path = dir_path / "catalog-manifest.json"
    manifest = {
        "schema": "agent-ee.catalog/1",
        "snapshot_date": snapshot_date,
        "stale_before": stale_before,
        "database": {
            "path": "catalog.sqlite",
            "sha256": digest(db_path),
            "size_bytes": db_path.stat().st_size,
        },
    }
    write_json(manifest_path, manifest)
    return manifest_path, db_path


class AssemblyReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _write_json_catalog(self, parts, *, snapshot_date="2026-10-15", stale_before="2026-10-08", name="catalog.json"):
        path = self.root / name
        doc = {
            "schema": "agent-ee.catalog-snapshot/1",
            "snapshot_date": snapshot_date,
            "stale_before": stale_before,
            "parts": parts,
        }
        write_json(path, doc)
        return name

    # ------------------------------------------------------------------
    # 1. LCSC binding: missing LCSC, malformed LCSC, unknown LCSC in catalog
    # ------------------------------------------------------------------
    def test_lcsc_binding_missing_invalid_and_unknown_in_catalog(self):
        cat_rel = self._write_json_catalog([
            {"lcsc": "C25804", "stock": 1000, "is_basic": True, "last_seen": "2026-10-15"},
        ])
        sch_text = _build_schematic_text([
            _build_symbol_sexpr(
                "R1", "Device:R", lcsc=None, part_prop=("PartNumber", "RC0603FR-0710KL")
            ),
            _build_symbol_sexpr(
                "R2", "Device:R", lcsc="C999999", part_prop=("PartNumber", "RC0603FR-071KL")
            ),
            _build_symbol_sexpr(
                "R3", "Device:R", lcsc="BAD_ID", part_prop=("PartNumber", "RC0603FR-074K7L")
            ),
            _build_symbol_sexpr(
                "R4", "Device:R", lcsc="C25804", part_prop=("PartNumber", "RC0603FR-0710KL")
            ),
        ])
        (self.root / "design.kicad_sch").write_text(sch_text, encoding="utf-8")

        report = evaluate_assembly_readiness(self.root, "design.kicad_sch", {"catalog": cat_rel})
        self.assertEqual(report["execution"], "ok")
        self.assertEqual(report["outcome"], "defect")
        self.assertEqual(report["missing_lcsc_refs"], ["R1"])
        self.assertEqual(report["unknown_lcsc_in_catalog"], {"R2": "C999999"})
        self.assertEqual(report["invalid_lcsc"], {"R3": "BAD_ID"})

        by_code = {f["code"]: f for f in report["findings"]}
        self.assertEqual(by_code["missing_lcsc"]["reference"], "R1")
        self.assertEqual(by_code["unknown_lcsc_in_catalog"]["reference"], "R2")
        self.assertEqual(by_code["unknown_lcsc_in_catalog"]["lcsc"], "C999999")
        self.assertEqual(by_code["invalid_lcsc"]["reference"], "R3")
        self.assertEqual(by_code["invalid_lcsc"]["lcsc"], "BAD_ID")

    # ------------------------------------------------------------------
    # 2. 3-state stock & freshness, quantity * boards coverage
    # ------------------------------------------------------------------
    def test_stock_zero_gives_defect_and_insufficient_stock_for_quantity_times_boards(self):
        cat_rel = self._write_json_catalog(
            [
                {"lcsc": "C10001", "stock": 0, "is_basic": True, "last_seen": "2026-10-15"},
                {"lcsc": "C10002", "stock": 15, "is_basic": True, "last_seen": "2026-10-15"},
            ],
            snapshot_date="2026-10-15",
        )
        # R1 uses C10001 (stock 0); R2 and R3 both use C10002 (2 per board * 10 boards = 20 > 15 available).
        sch_text = _build_schematic_text([
            _build_symbol_sexpr("R1", "Device:R", lcsc="C10001", part_prop=("PartNumber", "PN1")),
            _build_symbol_sexpr("R2", "Device:R", lcsc="C10002", part_prop=("PartNumber", "PN2")),
            _build_symbol_sexpr("R3", "Device:R", lcsc="C10002", part_prop=("PartNumber", "PN2")),
        ])
        (self.root / "design.kicad_sch").write_text(sch_text, encoding="utf-8")

        report = evaluate_assembly_readiness(
            self.root, "design.kicad_sch", {"catalog": cat_rel, "boards": 10}
        )
        self.assertEqual(report["outcome"], "defect")

        oos = next(f for f in report["findings"] if f["code"] == "out_of_stock")
        self.assertEqual(oos["lcsc"], "C10001")
        self.assertEqual(oos["available"], 0)
        self.assertEqual(oos["required"], 10)
        self.assertEqual(oos["snapshot_date"], "2026-10-15")

        insuff = next(f for f in report["findings"] if f["code"] == "insufficient_stock")
        self.assertEqual(insuff["lcsc"], "C10002")
        self.assertEqual(insuff["references"], ["R2", "R3"])
        self.assertEqual(insuff["available"], 15)
        self.assertEqual(insuff["required"], 20)
        self.assertEqual(insuff["snapshot_date"], "2026-10-15")

    def test_missing_or_stale_stock_gives_unknown_never_pass(self):
        # Case A: stock is None -> unknown (missing_stock_telemetry)
        cat_none = self._write_json_catalog(
            [{"lcsc": "C25804", "stock": None, "is_basic": True, "last_seen": "2026-10-15"}],
            name="cat_none.json",
        )
        sch_text = _build_schematic_text([
            _build_symbol_sexpr("R1", "Device:R", lcsc="C25804", part_prop=("PartNumber", "PN1")),
        ])
        (self.root / "design.kicad_sch").write_text(sch_text, encoding="utf-8")

        res_none = evaluate_assembly_readiness(self.root, "design.kicad_sch", {"catalog": cat_none})
        self.assertEqual(res_none["execution"], "ok")
        self.assertEqual(res_none["outcome"], "unknown")
        self.assertEqual(res_none["findings"][0]["code"], "missing_stock_telemetry")

        # Case B: stock > 0 but older than max_stock_age_days -> unknown (stale_stock_telemetry)
        cat_stale = self._write_json_catalog(
            [{"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-01"}],
            snapshot_date="2026-10-15",
            stale_before="2026-09-01",
            name="cat_stale.json",
        )
        res_stale_days = evaluate_assembly_readiness(
            self.root,
            "design.kicad_sch",
            {"catalog": cat_stale, "max_stock_age_days": 7},
        )
        self.assertEqual(res_stale_days["execution"], "ok")
        self.assertEqual(res_stale_days["outcome"], "unknown")
        self.assertEqual(res_stale_days["findings"][0]["code"], "stale_stock_telemetry")

        # Case C: stock > 0 but older than max_age_hours -> unknown (stale_stock_telemetry)
        cat_hours = self._write_json_catalog(
            [{"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-14T00:00:00Z"}],
            snapshot_date="2026-10-15",
            stale_before="2026-10-01",
            name="cat_hours.json",
        )
        res_stale_hours = evaluate_assembly_readiness(
            self.root,
            "design.kicad_sch",
            {"catalog": cat_hours, "as_of": "2026-10-15T12:00:00Z", "max_age_hours": 24},
        )
        self.assertEqual(res_stale_hours["outcome"], "unknown")
        self.assertEqual(res_stale_hours["findings"][0]["code"], "stale_stock_telemetry")

    # ------------------------------------------------------------------
    # 3. Synthetic 9-component boost-stage fixture (`clean_boost` style)
    #    Strict `PartNumber` reports 4 of 9 missing; `PartNumber|MPN|CompanyPN` reports only `C1`
    # ------------------------------------------------------------------
    def test_synthetic_clean_boost_strict_part_number_vs_any_of_policy(self):
        catalog_parts = [
            {"lcsc": f"C1000{i}", "stock": 200, "is_basic": True, "last_seen": "2026-10-15"}
            for i in range(1, 10)
        ]
        cat_rel = self._write_json_catalog(catalog_parts)

        # 9 populated boost-stage components:
        # - 5 components carry exact "PartNumber": R1, R2, R3, C2, Q1
        # - 2 components carry "MPN" instead of "PartNumber": R4, D1
        # - 1 component carries "CompanyPN" instead of "PartNumber": J1
        # - 1 component carries NO part-number property at all: C1
        # Therefore:
        #   - Strict ["Footprint", "LCSC", "Manufacturer", "PartNumber"] -> 4 of 9 missing (C1, D1, J1, R4)
        #   - Any-of ["Footprint", "LCSC", "Manufacturer", "PartNumber|MPN|CompanyPN"] -> only C1 (1 of 9) missing!
        boost_symbols = [
            _build_symbol_sexpr("C1", "Device:C", lcsc="C10001", part_prop=None, rating_prop=("Voltage", "50V")),
            _build_symbol_sexpr("C2", "Device:C", lcsc="C10002", part_prop=("PartNumber", "CL10B104KB8NNNC"), rating_prop=("Voltage", "50V")),
            _build_symbol_sexpr("D1", "Device:R", lcsc="C10003", part_prop=("MPN", "1N4148WS"), rating_prop=("Voltage", "75V")),
            _build_symbol_sexpr("J1", "Connector:Conn_01x02", lcsc="C10004", part_prop=("CompanyPN", "HDR-1X2-254"), rating_prop=None),
            _build_symbol_sexpr("Q1", "Device:Q_NPN_BCE", lcsc="C10005", part_prop=("PartNumber", "MMBT3904LT1G"), rating_prop=None),
            _build_symbol_sexpr("R1", "Device:R", lcsc="C10006", part_prop=("PartNumber", "RC0603FR-071ML")),
            _build_symbol_sexpr("R2", "Device:R", lcsc="C10007", part_prop=("PartNumber", "RC0603FR-0710KL")),
            _build_symbol_sexpr("R3", "Device:R", lcsc="C10008", part_prop=("PartNumber", "RC0603FR-074K7L")),
            _build_symbol_sexpr("R4", "Device:R", lcsc="C10009", part_prop=("MPN", "RC0603FR-07100RL")),
        ]
        (self.root / "clean_boost.kicad_sch").write_text(
            _build_schematic_text(boost_symbols), encoding="utf-8"
        )

        strict_report = evaluate_assembly_readiness(
            self.root,
            "clean_boost.kicad_sch",
            {
                "catalog": cat_rel,
                "required_properties": ["Footprint", "LCSC", "Manufacturer", "PartNumber"],
            },
        )
        self.assertEqual(strict_report["counts"]["populated"], 9)
        self.assertEqual(strict_report["outcome"], "defect")
        self.assertEqual(len(strict_report["missing_properties"]), 4)
        self.assertEqual(sorted(strict_report["missing_properties"].keys()), ["C1", "D1", "J1", "R4"])

        any_of_report = evaluate_assembly_readiness(
            self.root,
            "clean_boost.kicad_sch",
            {
                "catalog": cat_rel,
                "required_properties": [
                    "Footprint",
                    "LCSC",
                    "Manufacturer",
                    "PartNumber|MPN|CompanyPN",
                ],
            },
        )
        self.assertEqual(any_of_report["counts"]["populated"], 9)
        self.assertEqual(any_of_report["outcome"], "defect")
        self.assertEqual(list(any_of_report["missing_properties"].keys()), ["C1"])

        # Also verify dict form {"any_of": ["PartNumber", "MPN", "CompanyPN"]}
        dict_any_of_report = evaluate_assembly_readiness(
            self.root,
            "clean_boost.kicad_sch",
            {
                "catalog": cat_rel,
                "required_properties": [
                    "Footprint",
                    "LCSC",
                    "Manufacturer",
                    {"any_of": ["PartNumber", "MPN", "CompanyPN"]},
                ],
            },
        )
        self.assertEqual(list(dict_any_of_report["missing_properties"].keys()), ["C1"])

    # ------------------------------------------------------------------
    # 4. Synthetic DI-stage fixture (`di_box` style):
    #    DNP (`R3`) vs `exclude_from_bom` (`TP1`) vs power/virtual (`#PWR01`, `PWR_FLAG`, `H1`)
    # ------------------------------------------------------------------
    def test_synthetic_di_box_dnp_vs_exclude_from_bom_and_non_assembly_separation(self):
        cat_rel = self._write_json_catalog([
            {"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
            {"lcsc": "C17024", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
        ])
        di_symbols = [
            # Populated assembly components
            _build_symbol_sexpr("R1", "Device:R", lcsc="C25804", part_prop=("PartNumber", "RC0603FR-0710KL")),
            _build_symbol_sexpr("C1", "Device:C", lcsc="C17024", part_prop=("PartNumber", "CL10B104KB8NNNC"), rating_prop=("Voltage", "50V")),
            # R3 is explicitly DNP: (dnp yes) -> excluded_dnp (needs no LCSC)
            _build_symbol_sexpr("R3", "Device:R", lcsc=None, part_prop=None, dnp=True),
            # TP1 has (in_bom no) WITHOUT (dnp yes) -> excluded_from_bom (reported distinctly, NOT in excluded_dnp)
            _build_symbol_sexpr("TP1", "Connector:Conn_01x02", lcsc=None, part_prop=None, in_bom=False, on_board=True, dnp=False),
            # Power symbols and off-board mechanical symbol -> ignored_non_assembly
            _build_symbol_sexpr("#PWR01", "power:GND", footprint=None, lcsc=None, manufacturer=None, rating_prop=None),
            _build_symbol_sexpr("PWR_FLAG", "power:PWR_FLAG", footprint=None, lcsc=None, manufacturer=None, rating_prop=None),
            _build_symbol_sexpr("H1", "Mechanical:MountingHole", footprint="MountingHole:MountingHole_3.2mm_M3", lcsc=None, manufacturer=None, rating_prop=None, in_bom=False, on_board=False),
        ]
        (self.root / "di_box.kicad_sch").write_text(
            _build_schematic_text(di_symbols), encoding="utf-8"
        )

        extracted = extract_schematic_components(self.root, "di_box.kicad_sch")
        self.assertEqual([c["reference"] for c in extracted["populated"]], ["C1", "R1"])
        self.assertEqual([c["reference"] for c in extracted["excluded_dnp"]], ["R3"])
        self.assertEqual([c["reference"] for c in extracted["excluded_from_bom"]], ["TP1"])
        self.assertEqual(
            sorted(c["reference"] for c in extracted["ignored_non_assembly"]),
            ["#PWR01", "H1", "PWR_FLAG"],
        )

        report = evaluate_assembly_readiness(self.root, "di_box.kicad_sch", {"catalog": cat_rel})
        self.assertEqual(report["execution"], "ok")
        self.assertEqual(report["outcome"], "pass")
        self.assertEqual(report["excluded_dnp"], ["R3"])
        self.assertEqual(report["excluded_from_bom"], ["TP1"])
        bom_notice = next(f for f in report["findings"] if f["code"] == "exclude_from_bom")
        self.assertEqual(bom_notice["reference"], "TP1")
        self.assertEqual(bom_notice["severity"], "info")

    # ------------------------------------------------------------------
    # 5. Passive rating property requirement & AGENTEE-32 PARAM_power / PARAM_voltage
    # ------------------------------------------------------------------
    def test_passive_rating_property_requirement_and_agentee32_param_compatibility(self):
        cat_rel = self._write_json_catalog([
            {"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
            {"lcsc": "C17024", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
        ])
        # R1 and C1 lack rating properties -> defect when require_passive_ratings=True
        sch_missing = _build_schematic_text([
            _build_symbol_sexpr("R1", "Device:R", lcsc="C25804", part_prop=("PartNumber", "PN-R1"), rating_prop=None),
            _build_symbol_sexpr("C1", "Device:C", lcsc="C17024", part_prop=("PartNumber", "PN-C1"), rating_prop=None),
        ])
        (self.root / "unrated.kicad_sch").write_text(sch_missing, encoding="utf-8")

        rep_unrated = evaluate_assembly_readiness(self.root, "unrated.kicad_sch", {"catalog": cat_rel})
        self.assertEqual(rep_unrated["outcome"], "defect")
        self.assertEqual(sorted(rep_unrated["missing_rating_refs"]), ["C1", "R1"])

        # AGENTEE-32 PARAM_power and PARAM_voltage satisfy the passive rating requirement
        sch_param = _build_schematic_text([
            _build_symbol_sexpr("R1", "Device:R", lcsc="C25804", part_prop=("PartNumber", "PN-R1"), rating_prop=("PARAM_power", "0.1W")),
            _build_symbol_sexpr("C1", "Device:C", lcsc="C17024", part_prop=("PartNumber", "PN-C1"), rating_prop=("PARAM_voltage", "50V")),
        ])
        (self.root / "rated_param.kicad_sch").write_text(sch_param, encoding="utf-8")

        rep_rated = evaluate_assembly_readiness(self.root, "rated_param.kicad_sch", {"catalog": cat_rel})
        self.assertEqual(rep_rated["outcome"], "pass")
        self.assertEqual(rep_rated["missing_rating_refs"], [])

    # ------------------------------------------------------------------
    # 6. Empty schematic (0 populated components) -> outcome='info', never 'pass'
    # ------------------------------------------------------------------
    def test_zero_populated_components_gives_info_not_pass(self):
        cat_rel = self._write_json_catalog([
            {"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
        ])
        sch_empty = _build_schematic_text([
            _build_symbol_sexpr("#PWR01", "power:GND", footprint=None, lcsc=None, manufacturer=None, rating_prop=None),
            _build_symbol_sexpr("R1", "Device:R", lcsc=None, part_prop=None, dnp=True),
        ])
        (self.root / "empty.kicad_sch").write_text(sch_empty, encoding="utf-8")

        report = evaluate_assembly_readiness(self.root, "empty.kicad_sch", {"catalog": cat_rel})
        self.assertEqual(report["execution"], "ok")
        self.assertEqual(report["outcome"], "info")
        self.assertIn("0 populated assembly components", report["summary"])

    # ------------------------------------------------------------------
    # 7. Truncated/unparseable schematic or missing/corrupt snapshot -> execution='failed', outcome='unknown'
    # ------------------------------------------------------------------
    def test_truncated_schematic_and_missing_or_corrupt_snapshot_fail_closed(self):
        cat_rel = self._write_json_catalog([
            {"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
        ])
        # Truncated S-expression
        (self.root / "truncated.kicad_sch").write_text("(kicad_sch (version 20231120)\n  (symbol (lib_id", encoding="utf-8")
        with self.assertRaises(AssemblyReadinessExecutionError) as ctx_err:
            evaluate_assembly_readiness(self.root, "truncated.kicad_sch", {"catalog": cat_rel})
        self.assertEqual(ctx_err.exception.execution, "failed")

        # Missing child sheet in hierarchy
        (self.root / "missing_child.kicad_sch").write_text(
            _build_schematic_text([], sheets=[("SubSheet", "nonexistent_sub.kicad_sch", "uuid-sub-1")]),
            encoding="utf-8",
        )
        with self.assertRaises(AssemblyReadinessExecutionError) as ctx_sheet:
            evaluate_assembly_readiness(self.root, "missing_child.kicad_sch", {"catalog": cat_rel})
        self.assertEqual(ctx_sheet.exception.execution, "failed")

        # Corrupt JSON catalog (duplicate keys)
        (self.root / "dup.json").write_text(
            '{"schema": "agent-ee.catalog-snapshot/1", "schema": "agent-ee.catalog-snapshot/1", "snapshot_date": "2026-10-15", "parts": []}',
            encoding="utf-8",
        )
        with self.assertRaises(AssemblyReadinessExecutionError) as ctx_dup:
            load_catalog_snapshot(self.root, "dup.json")
        self.assertEqual(ctx_dup.exception.execution, "failed")

        # Missing catalog file
        with self.assertRaises(AssemblyReadinessExecutionError) as ctx_missing:
            load_catalog_snapshot(self.root, "does-not-exist.json")
        self.assertEqual(ctx_missing.exception.execution, "failed")

    # ------------------------------------------------------------------
    # 8. SQLite `agent-ee.catalog/1` snapshot, Extended limit, and full `run_checks` / `compile_baseline` integration
    # ------------------------------------------------------------------
    def test_sqlite_catalog_manifest_extended_limit_and_run_checks_pipeline(self):
        source = self.root / "src_tree"
        source.mkdir()

        manifest_path, db_path = _write_sqlite_catalog(
            source,
            [
                {"lcsc": "C25804", "mfr": "UNI-ROYAL", "mpn": "0603WAF1002T5E", "stock": 1000, "is_basic": True, "is_preferred": False},
                {"lcsc": "C17024", "mfr": "Samsung", "mpn": "CL10B104KB8NNNC", "stock": 800, "is_basic": False, "is_preferred": True},
                {"lcsc": "C90001", "mfr": "TI", "mpn": "OPA1678IDR", "stock": 120, "is_basic": False, "is_preferred": False},
                {"lcsc": "C90002", "mfr": "Analog", "mpn": "ADA4898-1YRDZ", "stock": 80, "is_basic": False, "is_preferred": False},
            ],
            snapshot_date="2026-10-15",
            stale_before="2026-10-08",
        )

        # Child sheet with U2 (Extended C90002)
        child_sch = _build_schematic_text([
            _build_symbol_sexpr(
                "U2",
                "Device:Q_NPN_BCE",
                footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                lcsc="C90002",
                manufacturer="Analog",
                part_prop=("MPN", "ADA4898-1YRDZ"),
                rating_prop=None,
            ),
        ])
        (source / "sub.kicad_sch").write_text(child_sch, encoding="utf-8")

        # Root sheet with R1 (Basic C25804), C1 (Preferred C17024), U1 (Extended C90001), plus child sheet
        root_sch = _build_schematic_text(
            [
                _build_symbol_sexpr("R1", "Device:R", lcsc="C25804", part_prop=("PartNumber", "0603WAF1002T5E")),
                _build_symbol_sexpr("C1", "Device:C", lcsc="C17024", part_prop=("PartNumber", "CL10B104KB8NNNC"), rating_prop=("Voltage", "50V")),
                _build_symbol_sexpr(
                    "U1",
                    "Device:Q_NPN_BCE",
                    footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                    lcsc="C90001",
                    manufacturer="TI",
                    part_prop=("PartNumber", "OPA1678IDR"),
                    rating_prop=None,
                ),
            ],
            sheets=[("AudioStage", "sub.kicad_sch", "00000000-0000-0000-0000-000000000002")],
        )
        (source / "top.kicad_sch").write_text(root_sch, encoding="utf-8")
        (source / "top.kicad_pcb").write_text("(kicad_pcb (version 20221018))", encoding="utf-8")
        (source / "req.md").write_text("# Requirements\nR-ASM: JLC assembly readiness\n", encoding="utf-8")

        # Direct evaluation with max_extended_parts=1 should flag defect (2 Extended > 1)
        direct_defect = evaluate_assembly_readiness(
            source,
            "top.kicad_sch",
            {"catalog": "catalog-manifest.json", "max_extended_parts": 1, "boards": 5},
        )
        self.assertEqual(direct_defect["outcome"], "defect")
        self.assertEqual(
            direct_defect["tier_counts"],
            {
                "basic_unique": 1,
                "preferred_unique": 1,
                "extended_unique": 2,
                "basic_placements": 1,
                "preferred_placements": 1,
                "extended_placements": 2,
                "extended_lcsc": ["C90001", "C90002"],
            },
        )
        ext_finding = next(f for f in direct_defect["findings"] if f["code"] == "extended_parts_limit_exceeded")
        self.assertEqual(ext_finding["extended_unique"], 2)
        self.assertEqual(ext_finding["max_extended_parts"], 1)

        # Full scope/3 capture + compile_baseline + describe_check + run_checks with max_extended_parts=2 -> pass
        scope = {
            "schema": "agent-ee.scope/3",
            "name": "assembly-test",
            "scope": "assembly readiness verification",
            "inputs": [{"path": str(source), "at": "design"}],
            "schematic": "design/top.kicad_sch",
            "board": "design/top.kicad_pcb",
            "packet_max_bytes": 16384,
            "checks": [
                {
                    "id": "jlc_assembly",
                    "kind": "assembly_readiness",
                    "params": {
                        "catalog": "design/catalog-manifest.json",
                        "boards": 5,
                        "max_extended_parts": 2,
                        "max_stock_age_days": 14,
                    },
                }
            ],
            "baseline": {
                "requirements": [
                    {
                        "id": "R-ASM",
                        "intent": "acceptance",
                        "statement": "All populated parts must be stocked in JLC catalog with <= 2 Extended parts",
                        "source": {"path": "design/req.md", "locator": "R-ASM"},
                        "checks": ["jlc_assembly"],
                    }
                ],
                "targets": ["design/req.md"],
                "reference_architectures": ["design/req.md"],
            },
        }
        scope_path = self.root / "scope.json"
        write_json(scope_path, scope)
        run_dir = self.root / "run_pass"
        run_dir.mkdir()
        manifest = capture(scope_path, run_dir)

        baseline = read_json(run_dir / "baseline.json")
        self.assertEqual(baseline["schema"], "agent-ee.baseline/2")
        self.assertIn("design/catalog-manifest.json", baseline["files"])
        self.assertIn("design/catalog.sqlite", baseline["files"])

        desc = describe_check(
            scope["checks"][0],
            check_specs=scope["checks"],
            files=manifest["files"],
            scope=scope,
            inputs=run_dir / "inputs",
        )
        self.assertEqual(desc["status"], "supported")
        self.assertTrue(desc["can_evaluate_bound"])
        self.assertIn("assembly:extended-parts-limit", desc["predicates"])

        results = run_checks(run_dir, time.monotonic() + 60.0)
        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res["execution"], "ok")
        self.assertEqual(res["outcome"], "pass")
        self.assertIn("work/jlc_assembly/assembly-readiness.json", res["evidence"])

        # Corrupting the captured schematic in a run causes run_checks to return execution='failed', outcome='unknown'
        (run_dir / "inputs" / "design" / "top.kicad_sch").write_text("(kicad_sch (unclosed", encoding="utf-8")
        failed_results = run_checks(run_dir, time.monotonic() + 60.0)
        self.assertEqual(failed_results[0]["execution"], "failed")
        self.assertEqual(failed_results[0]["outcome"], "unknown")


if __name__ == "__main__":
    unittest.main()

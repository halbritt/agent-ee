"""Tests for AGENTEE-29 JLC assembly-readiness check (`src/agent_ee/assembly_readiness.py`).

Schematics are original fixtures generated here and loaded by the real
kicad-cli; components reach the check only through the native XML netlist,
exported inside the same sandbox the check uses.
"""

import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_ee import executor
from agent_ee.assembly_readiness import (
    AssemblyReadinessExecutionError,
    evaluate_assembly_readiness,
    load_catalog_snapshot,
    read_netlist_components,
    verify_schematic_files,
)
from agent_ee.candidate import capture
from agent_ee.capabilities import describe_check
from agent_ee.checks import run_checks
from agent_ee.storage import digest, read_json, write_json
from toolchain import require_toolchain


HIDDEN = "(effects (font (size 1.27 1.27)) (hide yes))"
SHOWN = "(effects (font (size 1.27 1.27)))"
LIB_PINS = {"R": 2, "C": 2, "D": 2, "Q": 3, "J": 2, "MH": 1, "U": 3}


def _uuid(seed):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"agent-ee-ee29/{seed}"))


def _lib_symbol(name, pins, power=False):
    short = name.split(":", 1)[1]
    if power:
        pin_text = (
            '(pin power_in line (at 0 0 0) (length 0) hide (name "1" ' + SHOWN + ') (number "1" ' + SHOWN + "))"
        )
    else:
        pin_text = "".join(
            f'(pin passive line (at 0 {-2.54 * i:.2f} 0) (length 2.54) (name "{i + 1}" {SHOWN}) '
            f'(number "{i + 1}" {SHOWN}))'
            for i in range(pins)
        )
    return (
        f'(symbol "{name}" {"(power) " if power else ""}(pin_names (offset 0)) (in_bom yes) (on_board yes) '
        f'(property "Reference" "X" (at 0 0 0) {HIDDEN}) (property "Value" "{short}" (at 0 0 0) {HIDDEN}) '
        f'(symbol "{short}_1_1" {pin_text}))'
    )


LIB_SYMBOLS = "".join(
    [_lib_symbol(f"ee29:{name}", pins) for name, pins in LIB_PINS.items()]
    + [_lib_symbol("power:GND", 1, power=True), _lib_symbol("power:PWR_FLAG", 1, power=True)]
)


def part(ref, lib="R", *, value="10k", footprint="ee29:R_0603", lcsc="C25804", manufacturer="Example Mfr",
         part_number=("PartNumber", "PN-GENERIC"), rating=("Power", "0.1W"), extra=None,
         in_bom=True, on_board=True, dnp=False):
    """One placed symbol; ``None`` drops a field."""
    fields = [("Value", value)]
    for name, val in (("Footprint", footprint), ("LCSC", lcsc), ("Manufacturer", manufacturer)):
        if val is not None:
            fields.append((name, val))
    for pair in (part_number, rating):
        if pair is not None:
            fields.append(pair)
    fields.extend((extra or {}).items())
    return {"ref": ref, "lib": lib, "fields": fields, "in_bom": in_bom, "on_board": on_board, "dnp": dnp}


def power(ref, lib="power:GND", value="GND"):
    return {"ref": ref, "lib": lib, "fields": [("Value", value)], "in_bom": True, "on_board": True, "dnp": False}


def _placed(item, x, instance_refs):
    lib = item["lib"] if ":" in item["lib"] else f"ee29:{item['lib']}"
    pins = 1 if lib.startswith("power:") else LIB_PINS[item["lib"]]
    seed = f"{item['ref']}-{x}"
    props = "".join(
        f'(property "{k}" "{v}" (at 0 0 0) {HIDDEN})' for k, v in [("Reference", item["ref"])] + item["fields"]
    )
    pin_uuids = "".join(f'(pin "{i + 1}" (uuid "{_uuid(seed + str(i))}"))' for i in range(pins))
    paths = "".join(f'(path "{path}" (reference "{ref}") (unit 1))' for path, ref in instance_refs)
    yn = lambda flag: "yes" if flag else "no"  # noqa: E731
    return (
        f'(symbol (lib_id "{lib}") (at {x} 50 0) (unit 1) (in_bom {yn(item["in_bom"])}) '
        f'(on_board {yn(item["on_board"])}) (dnp {yn(item["dnp"])}) (uuid "{_uuid(seed)}") '
        f'{props}{pin_uuids}(instances (project "ee29" {paths})))'
    )


def schematic(name, parts, *, sheets=(), instance_paths=None):
    """KiCad 10 schematic text; ``instance_paths`` lists (path, block) for a reused child sheet (U1 -> U101)."""
    sheet_uuid = _uuid(f"sheet-{name}")
    body = []
    for index, item in enumerate(parts):
        if instance_paths is None:
            refs = [(f"/{sheet_uuid}", item["ref"])]
        else:
            refs = [(path, re.sub(r"^(\D+)(\d+)$", lambda m, b=block: f"{m[1]}{b}{int(m[2]):02d}", item["ref"]))
                    for path, block in instance_paths]
        body.append(_placed(item, 10 + 5 * index, refs))
    for index, (sheet_name, sheet_file, child_uuid, root_uuid) in enumerate(sheets):
        x = 100 + 20 * index
        body.append(
            f'(sheet (at {x} 20) (size 10 10) (fields_autoplaced yes) (stroke (width 0.1524) (type solid)) '
            f'(fill (color 0 0 0 0.0)) (uuid "{child_uuid}") '
            f'(property "Sheetname" "{sheet_name}" (at {x} 19 0) {SHOWN}) '
            f'(property "Sheetfile" "{sheet_file}" (at {x} 31 0) {SHOWN}) '
            f'(instances (project "ee29" (path "/{root_uuid}" (page "{index + 2}")))))'
        )
    return (
        f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{sheet_uuid}") (paper "A4") '
        f"(lib_symbols {LIB_SYMBOLS})\n" + "\n".join(body) + '\n(sheet_instances (path "/" (page "1"))))\n'
    )


def _write_sqlite_catalog(dir_path: Path, parts_rows, *, snapshot_date="2026-10-15", stale_before="2026-10-08"):
    db_path = dir_path / "catalog.sqlite"
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
                    row["lcsc"], row.get("mfr", "GenericMfr"), row.get("mpn", f"MPN-{row['lcsc']}"),
                    row.get("package", "0603"), row.get("category", "Passives"), row.get("stock", 500),
                    row.get("price1", 0.01), 1 if row.get("is_basic", True) else 0,
                    1 if row.get("is_preferred", False) else 0, row.get("last_seen", snapshot_date),
                    "{}", "{}", "{}",
                ),
            )
        conn.commit()
    finally:
        conn.close()
    write_json(dir_path / "catalog-manifest.json", {
        "schema": "agent-ee.catalog/1",
        "snapshot_date": snapshot_date,
        "stale_before": stale_before,
        "database": {"path": "catalog.sqlite", "sha256": digest(db_path), "size_bytes": db_path.stat().st_size},
    })


class AssemblyReadinessTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(tools=("kicad-cli",))
        self.tmp = tempfile.TemporaryDirectory(prefix="agent-ee-ee29-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.exports = 0

    def catalog(self, parts, *, snapshot_date="2026-10-15", stale_before="2026-10-08", name="catalog.json"):
        doc = {"schema": "agent-ee.catalog-snapshot/1", "snapshot_date": snapshot_date, "parts": parts}
        if stale_before is not None:
            doc["stale_before"] = stale_before
        write_json(self.root / name, doc)
        return name

    def export(self, name, text, children=()):
        """Write a schematic (plus child sheets) and export its native XML netlist in the sandbox."""
        (self.root / name).write_text(text, encoding="utf-8")
        for child_name, child_text in children:
            (self.root / child_name).write_text(child_text, encoding="utf-8")
        self.exports += 1
        work = self.root.parent / f"{self.root.name}-work{self.exports}"
        work.mkdir()
        self.addCleanup(shutil.rmtree, work, True)
        record = executor.execute(
            self.root, work,
            ["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml", "-o", "/work/design.xml",
             f"/candidate/{name}"],
            timeout=120,
        )
        self.assertEqual(record["execution"], "ok", record)
        return work / "design.xml"

    def evaluate(self, netlist, params):
        return evaluate_assembly_readiness(self.root, netlist, params)

    # ------------------------------------------------------------------ LCSC binding
    def test_lcsc_binding_missing_invalid_and_unknown_in_catalog(self):
        cat = self.catalog([{"lcsc": "C25804", "stock": 1000, "is_basic": True, "last_seen": "2026-10-15"}])
        netlist = self.export("design.kicad_sch", schematic("design", [
            part("R1", lcsc=None),
            part("R2", lcsc="C999999"),
            part("R3", lcsc="BAD_ID"),
            part("R4"),
        ]))
        report = self.evaluate(netlist, {"catalog": cat})
        self.assertEqual((report["execution"], report["outcome"]), ("ok", "defect"))
        self.assertEqual(report["missing_lcsc_refs"], ["R1"])
        self.assertEqual(report["unknown_lcsc_in_catalog"], {"R2": "C999999"})
        self.assertEqual(report["invalid_lcsc"], {"R3": "BAD_ID"})
        by_code = {f["code"]: f for f in report["findings"]}
        self.assertEqual(by_code["missing_lcsc"]["reference"], "R1")
        self.assertIn("R1", by_code["missing_lcsc"]["message"])
        self.assertEqual(by_code["unknown_lcsc_in_catalog"]["lcsc"], "C999999")
        self.assertIn("C999999", by_code["unknown_lcsc_in_catalog"]["message"])
        self.assertEqual(report["parts"]["C25804"]["references"], ["R4"])
        self.assertEqual(report["catalog"]["sha256"], digest(self.root / cat))
        self.assertEqual(report["netlist"]["sha256"], digest(netlist))

    # ------------------------------------------------------------------ stock
    def test_stock_zero_and_below_quantity_times_boards_are_defects_with_snapshot_date(self):
        cat = self.catalog([
            {"lcsc": "C10001", "stock": 0, "is_basic": True, "last_seen": "2026-10-15"},
            {"lcsc": "C10002", "stock": 15, "is_basic": True, "last_seen": "2026-10-15"},
            {"lcsc": "C10003", "stock": 20, "is_basic": True, "last_seen": "2026-10-15"},
        ])
        netlist = self.export("design.kicad_sch", schematic("design", [
            part("R1", lcsc="C10001"),
            part("R2", lcsc="C10002"), part("R3", lcsc="C10002"),
            part("R4", lcsc="C10003"), part("R5", lcsc="C10003"),
        ]))
        report = self.evaluate(netlist, {"catalog": cat, "boards": 10})
        self.assertEqual(report["outcome"], "defect")
        oos = next(f for f in report["findings"] if f["code"] == "out_of_stock")
        self.assertEqual((oos["lcsc"], oos["available"], oos["required"], oos["snapshot_date"]),
                         ("C10001", 0, 10, "2026-10-15"))
        short = [f for f in report["findings"] if f["code"] == "insufficient_stock"]
        self.assertEqual(len(short), 1)
        self.assertEqual((short[0]["lcsc"], short[0]["references"], short[0]["available"], short[0]["required"]),
                         ("C10002", ["R2", "R3"], 15, 20))
        self.assertIn("2026-10-15", short[0]["message"])
        self.assertIn("available 15 < required 20", short[0]["message"])
        # Exactly covered stock (2 x 10 = 20) is ok.
        self.assertEqual(report["parts"]["C10003"]["stock_state"], "ok")

        one_board = self.evaluate(netlist, {"catalog": cat, "boards": 1})
        self.assertEqual([f["code"] for f in one_board["findings"] if f["severity"] == "defect"], ["out_of_stock"])

    def test_missing_stale_future_or_undeclared_stock_freshness_is_unknown_never_pass(self):
        netlist = self.export("design.kicad_sch", schematic("design", [part("R1")]))

        def outcome(parts, params=None, **catalog_kw):
            report = self.evaluate(netlist, {"catalog": self.catalog(parts, **catalog_kw),
                                             **(params or {})})
            return report["outcome"], [f["code"] for f in report["findings"]]

        fresh = {"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"}
        self.assertEqual(outcome([fresh]), ("pass", []))
        self.assertEqual(outcome([{**fresh, "stock": None}]), ("unknown", ["missing_stock_telemetry"]))
        self.assertEqual(outcome([{**fresh, "last_seen": None}]), ("unknown", ["stale_stock_telemetry"]))
        self.assertEqual(outcome([{**fresh, "last_seen": "2026-10-01"}]), ("unknown", ["stale_stock_telemetry"]))
        self.assertEqual(outcome([{**fresh, "last_seen": "2026-10-10"}], {"max_stock_age_days": 3}),
                         ("unknown", ["stale_stock_telemetry"]))
        self.assertEqual(
            outcome([{**fresh, "last_seen": "2026-10-14T00:00:00Z"}],
                    {"as_of": "2026-10-15T12:00:00Z", "max_age_hours": 24}),
            ("unknown", ["stale_stock_telemetry"]))
        self.assertEqual(outcome([{**fresh, "last_seen": "2026-10-20"}]), ("unknown", ["stale_stock_telemetry"]))
        # No declared maximum age anywhere: freshness cannot be established.
        self.assertEqual(outcome([fresh], stale_before=None), ("unknown", ["stale_stock_telemetry"]))
        self.assertEqual(outcome([fresh], {"max_stock_age_days": 3}, stale_before=None), ("pass", []))
        # Stale stock never masks an explicit zero.
        self.assertEqual(outcome([{**fresh, "stock": 0, "last_seen": "2026-01-01"}]), ("defect", ["out_of_stock"]))

    # ------------------------------------------------------------------ property policy (clean_boost-like)
    def test_clean_boost_like_part_number_only_policy_reports_4_of_9_and_any_of_only_c1(self):
        cat = self.catalog([
            {"lcsc": f"C1000{i}", "stock": 200, "is_basic": True, "last_seen": "2026-10-15"} for i in range(1, 10)
        ])
        volts = ("Voltage", "50V")
        netlist = self.export("clean_boost.kicad_sch", schematic("clean_boost", [
            part("C1", "C", lcsc="C10001", part_number=None, rating=volts),
            part("C2", "C", lcsc="C10002", part_number=("PartNumber", "CAP-100N-50V"), rating=volts),
            part("D1", "D", lcsc="C10003", part_number=("MPN", "DIODE-SCHOTTKY-1A"), rating=None),
            part("J1", "J", lcsc="C10004", part_number=("CompanyPN", "HDR-1X2"), rating=None),
            part("Q1", "Q", lcsc="C10005", part_number=("PartNumber", "NPN-SOT23"), rating=None),
            part("R1", lcsc="C10006", part_number=("PartNumber", "RES-1M")),
            part("R2", lcsc="C10007", part_number=("PartNumber", "RES-10K")),
            part("R3", lcsc="C10008", part_number=("PartNumber", "RES-4K7")),
            part("R4", lcsc="C10009", part_number=("MPN", "RES-100R")),
            power("#PWR01"), power("#FLG01", "power:PWR_FLAG", "PWR_FLAG"),
        ]))
        part_number_only = self.evaluate(netlist, {"catalog": cat, "required_properties": ["PartNumber"]})
        self.assertEqual(part_number_only["counts"]["populated"], 9)
        self.assertEqual(sorted(part_number_only["missing_properties"]), ["C1", "D1", "J1", "R4"])
        missing = [f["reference"] for f in part_number_only["findings"] if f["code"] == "missing_property"]
        self.assertEqual(missing, ["C1", "D1", "J1", "R4"])

        for policy in (["PartNumber|MPN|CompanyPN"],
                       ["Footprint", "LCSC", "Manufacturer", {"any_of": ["PartNumber", "MPN", "CompanyPN"]}]):
            with self.subTest(policy=policy):
                any_of = self.evaluate(netlist, {"catalog": cat, "required_properties": policy})
                self.assertEqual(any_of["counts"]["populated"], 9)
                self.assertEqual(list(any_of["missing_properties"]), ["C1"])
                self.assertEqual([f["reference"] for f in any_of["findings"] if f["severity"] != "info"], ["C1"])

    # ------------------------------------------------------------------ DNP vs exclude_from_bom (di_box-like)
    def test_di_box_like_dnp_only_on_native_attribute_and_in_bom_no_reported_separately(self):
        cat = self.catalog([
            {"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
            {"lcsc": "C17024", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
        ])
        base = [
            part("R1"),
            part("C1", "C", lcsc="C17024", rating=("Voltage", "50V")),
            part("R3", lcsc=None, part_number=None, manufacturer=None, dnp=True),
            part("TP1", "J", lcsc=None, part_number=None, manufacturer=None, rating=None, in_bom=False),
            part("H1", "MH", footprint="ee29:MountingHole_M3", lcsc=None, part_number=None, manufacturer=None,
                 rating=None, in_bom=False, on_board=False),
            part("H2", "MH", footprint="ee29:MountingHole_M3", lcsc=None, part_number=None, manufacturer=None,
                 rating=None, in_bom=False),
            power("#PWR01"), power("#FLG01", "power:PWR_FLAG", "PWR_FLAG"),
        ]
        netlist = self.export("di_box.kicad_sch", schematic("di_box", base))
        extracted = read_netlist_components(netlist)
        self.assertEqual([c["reference"] for c in extracted["populated"]], ["C1", "R1"])
        self.assertEqual([c["reference"] for c in extracted["excluded_dnp"]], ["R3"])
        self.assertEqual([c["reference"] for c in extracted["excluded_from_bom"]], ["H2", "TP1"])
        self.assertEqual([c["reference"] for c in extracted["ignored_non_assembly"]], ["H1"])

        report = self.evaluate(netlist, {"catalog": cat})
        self.assertEqual((report["execution"], report["outcome"]), ("ok", "pass"))
        self.assertEqual(report["excluded_dnp"], ["R3"])
        self.assertEqual(report["excluded_from_bom"], ["H2", "TP1"])
        self.assertEqual({f["code"] for f in report["findings"]}, {"exclude_from_bom"})
        self.assertTrue(all(f["severity"] == "info" for f in report["findings"]))
        self.assertNotIn("#PWR01", str(report))

        # A field-level DNP convention is not a native DNP: the part stays populated and is a defect.
        marked = self.export("di_box_marked.kicad_sch", schematic("di_box_marked", base + [
            part("R5", extra={"DNP": "yes"}),
            part("R6", extra={"dnp": "yes"}),
            part("R7", value="DNP"),
        ]))
        marked_report = self.evaluate(marked, {"catalog": cat})
        self.assertEqual(marked_report["excluded_dnp"], ["R3"])
        self.assertEqual(marked_report["populated_refs"], ["C1", "R1", "R5", "R6", "R7"])
        flagged = {f["reference"]: f["markers"] for f in marked_report["findings"]
                   if f["code"] == "non_native_assembly_marker"}
        self.assertEqual(flagged, {"R5": ["DNP"], "R6": ["dnp"], "R7": ["Value=DNP"]})
        self.assertEqual(marked_report["outcome"], "defect")

    # ------------------------------------------------------------------ rating properties
    def test_passive_rating_properties_required_for_r_and_c(self):
        cat = self.catalog([
            {"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
            {"lcsc": "C17024", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
        ])
        unrated = self.export("unrated.kicad_sch", schematic("unrated", [
            part("R1", rating=None), part("C1", "C", lcsc="C17024", rating=None),
            part("D1", "D", rating=None),
        ]))
        report = self.evaluate(unrated, {"catalog": cat})
        self.assertEqual(report["outcome"], "defect")
        self.assertEqual(report["missing_rating_refs"], ["C1", "R1"])
        self.assertEqual(self.evaluate(unrated, {"catalog": cat, "require_passive_ratings": False})["outcome"],
                         "pass")

        rated = self.export("rated.kicad_sch", schematic("rated", [
            part("R1", rating=("PARAM_power", "0.1W")), part("C1", "C", lcsc="C17024", rating=("PARAM_voltage", "50V")),
        ]))
        self.assertEqual(self.evaluate(rated, {"catalog": cat})["outcome"], "pass")

    # ------------------------------------------------------------------ empty
    def test_empty_schematic_gives_info_with_zero_components_not_pass(self):
        cat = self.catalog([{"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"}])
        for name, parts in (("empty", []), ("power_only", [power("#PWR01"), power("#FLG01", "power:PWR_FLAG")])):
            with self.subTest(name=name):
                netlist = self.export(f"{name}.kicad_sch", schematic(name, parts))
                report = self.evaluate(netlist, {"catalog": cat})
                self.assertEqual((report["execution"], report["outcome"]), ("ok", "info"))
                self.assertEqual(report["counts"]["populated"], 0)
                self.assertEqual(report["netlist"]["components"], 0)
                self.assertIn("0 populated assembly components", report["summary"])

    # ------------------------------------------------------------------ fail closed
    def test_unparseable_inputs_and_missing_snapshot_fail_instead_of_empty_findings(self):
        cat = self.catalog([{"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"}])
        good = schematic("good", [part("R1")])
        (self.root / "good.kicad_sch").write_text(good, encoding="utf-8")
        (self.root / "truncated.kicad_sch").write_text(good[: len(good) // 2], encoding="utf-8")
        (self.root / "other.kicad_sch").write_text("(kicad_pcb (version 1))", encoding="utf-8")
        self.assertEqual(list(verify_schematic_files(self.root, ["good.kicad_sch"])), ["good.kicad_sch"])
        for bad in ("truncated.kicad_sch", "other.kicad_sch", "absent.kicad_sch"):
            with self.subTest(schematic=bad), self.assertRaises(AssemblyReadinessExecutionError) as caught:
                verify_schematic_files(self.root, ["good.kicad_sch", bad])
            self.assertEqual(caught.exception.execution, "failed")

        (self.root / "garbage.xml").write_text("<export><components><comp ref=", encoding="utf-8")
        (self.root / "nocomponents.xml").write_text("<export><design/></export>", encoding="utf-8")
        (self.root / "duplicate.xml").write_text(
            '<export><components><comp ref="R1"/><comp ref="R1"/></components></export>', encoding="utf-8")
        for name in ("garbage.xml", "nocomponents.xml", "duplicate.xml", "absent.xml"):
            with self.subTest(netlist=name), self.assertRaises(AssemblyReadinessExecutionError) as caught:
                self.evaluate(self.root / name, {"catalog": cat})
            self.assertEqual(caught.exception.execution, "failed")

        netlist = self.export("design.kicad_sch", schematic("design", [part("R1")]))
        with self.assertRaises(AssemblyReadinessExecutionError) as sheets:
            evaluate_assembly_readiness(self.root, netlist, {"catalog": cat}, expected_sheets=2)
        self.assertEqual(sheets.exception.execution, "failed")
        with self.assertRaises(AssemblyReadinessExecutionError) as missing:
            self.evaluate(netlist, {"catalog": "does-not-exist.json"})
        self.assertEqual(missing.exception.execution, "failed")
        (self.root / "dup.json").write_text(
            '{"schema": "agent-ee.catalog-snapshot/1", "schema": "x", "snapshot_date": "2026-10-15", "parts": []}',
            encoding="utf-8")
        with self.assertRaises(AssemblyReadinessExecutionError) as dup:
            load_catalog_snapshot(self.root, "dup.json")
        self.assertEqual(dup.exception.execution, "failed")
        with self.assertRaises(AssemblyReadinessExecutionError) as params:
            self.evaluate(netlist, {"catalog": cat, "boards": 0})
        self.assertEqual(params.exception.execution, "unsupported")

    # ------------------------------------------------------------------ pipeline
    def test_sqlite_catalog_reused_sheet_extended_limit_and_run_checks_pipeline(self):
        source = self.root / "src_tree"
        source.mkdir()
        _write_sqlite_catalog(source, [
            {"lcsc": "C25804", "stock": 1000, "is_basic": True},
            {"lcsc": "C17024", "stock": 800, "is_basic": False, "is_preferred": True},
            {"lcsc": "C90001", "stock": 120, "is_basic": False},
            {"lcsc": "C90002", "stock": 80, "is_basic": False},
        ])
        root_uuid = _uuid("sheet-top")
        a_uuid, b_uuid = _uuid("inst-a"), _uuid("inst-b")
        # One child sheet reused twice: U1 appears as U101 and U201, so 2 per board.
        child = schematic("stage", [
            part("U1", "U", footprint="ee29:SOIC-8", lcsc="C90002", part_number=("MPN", "OPAMP-X"), rating=None),
        ], instance_paths=[(f"/{root_uuid}/{a_uuid}", 1), (f"/{root_uuid}/{b_uuid}", 2)])
        (source / "stage.kicad_sch").write_text(child, encoding="utf-8")
        top = schematic("top", [
            part("R1"),
            part("C1", "C", lcsc="C17024", rating=("Voltage", "50V")),
            part("U9", "U", footprint="ee29:SOIC-8", lcsc="C90001", rating=None),
            power("#PWR01"),
        ], sheets=[("A", "stage.kicad_sch", a_uuid, root_uuid), ("B", "stage.kicad_sch", b_uuid, root_uuid)])
        (source / "top.kicad_sch").write_text(top, encoding="utf-8")
        (source / "top.kicad_pcb").write_text("(kicad_pcb (version 20221018))", encoding="utf-8")
        (source / "req.md").write_text("# Requirements\nR-ASM: JLC assembly readiness\n", encoding="utf-8")

        def run(name, params):
            scope = {
                "schema": "agent-ee.scope/3", "name": "assembly-test", "scope": "assembly readiness verification",
                "inputs": [{"path": str(source), "at": "design"}],
                "schematic": "design/top.kicad_sch", "board": "design/top.kicad_pcb", "packet_max_bytes": 16384,
                "checks": [{"id": "jlc_assembly", "kind": "assembly_readiness",
                            "params": {"catalog": "design/catalog-manifest.json", **params}}],
                "baseline": {
                    "requirements": [{
                        "id": "R-ASM", "intent": "acceptance",
                        "statement": "All populated parts are stocked for the build with a bounded Extended count",
                        "source": {"path": "design/req.md", "locator": "R-ASM"}, "checks": ["jlc_assembly"],
                    }],
                    "targets": ["design/req.md"], "reference_architectures": ["design/req.md"],
                },
            }
            case = self.root / name
            case.mkdir()
            write_json(case / "scope.json", scope)
            run_dir = case / "run"
            run_dir.mkdir()
            manifest = capture(case / "scope.json", run_dir)
            return scope, manifest, run_dir

        scope, manifest, run_dir = run("pass", {"boards": 5, "max_extended_parts": 2})
        baseline = read_json(run_dir / "baseline.json")
        self.assertIn("design/catalog.sqlite", baseline["files"])
        desc = describe_check(scope["checks"][0], check_specs=scope["checks"], files=manifest["files"],
                              scope=scope, inputs=run_dir / "inputs")
        self.assertEqual(desc["status"], "supported")
        self.assertIn("assembly:extended-parts-limit", desc["predicates"])

        res = run_checks(run_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((res["execution"], res["outcome"]), ("ok", "pass"), res["summary"])
        self.assertIn("work/jlc_assembly/design.xml", res["evidence"])
        self.assertIn("work/jlc_assembly/assembly-readiness.json", res["evidence"])
        details = res["details"]
        self.assertEqual(details["populated_refs"], ["C1", "R1", "U9", "U101", "U201"])
        self.assertEqual(details["parts"]["C90002"]["quantity_per_board"], 2)
        self.assertEqual(details["parts"]["C90002"]["required_stock"], 10)
        self.assertEqual(details["tier_counts"]["extended_lcsc"], ["C90001", "C90002"])
        self.assertEqual(details["schematic"]["sheet_instances"], 3)
        self.assertEqual(details["schematic"]["files"], {
            "design/top.kicad_sch": digest(source / "top.kicad_sch"),
            "design/stage.kicad_sch": digest(source / "stage.kicad_sch"),
        })
        self.assertEqual(details["netlist"]["sha256"], digest(run_dir / "work/jlc_assembly/design.xml"))
        self.assertEqual(details["catalog"]["database_sha256"], digest(source / "catalog.sqlite"))

        # 2 per board x 50 boards = 100 > 80 available; 2 Extended > 1.
        _, _, short_dir = run("short", {"boards": 50, "max_extended_parts": 1})
        short = run_checks(short_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((short["execution"], short["outcome"]), ("ok", "defect"))
        codes = {f["code"]: f for f in short["details"]["findings"]}
        self.assertEqual((codes["insufficient_stock"]["available"], codes["insufficient_stock"]["required"]),
                         (80, 100))
        self.assertEqual(codes["extended_parts_limit_exceeded"]["extended_unique"], 2)

        # kicad-cli exports a truncated child sheet with exit 0 and no components; the check must fail instead.
        truncated = (run_dir / "inputs/design/stage.kicad_sch").read_text()
        (run_dir / "inputs/design/stage.kicad_sch").write_text(truncated[:400], encoding="utf-8")
        failed = run_checks(run_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((failed["execution"], failed["outcome"]), ("failed", "unknown"), failed["summary"])
        self.assertIn("does not parse", failed["summary"])

        (run_dir / "inputs/design/top.kicad_sch").write_text("(kicad_sch (unclosed", encoding="utf-8")
        failed_root = run_checks(run_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((failed_root["execution"], failed_root["outcome"]), ("failed", "unknown"))

        (run_dir / "inputs/design/catalog-manifest.json").unlink()
        (run_dir / "inputs/design/top.kicad_sch").write_text(top, encoding="utf-8")
        (run_dir / "inputs/design/stage.kicad_sch").write_text(truncated, encoding="utf-8")
        no_catalog = run_checks(run_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((no_catalog["execution"], no_catalog["outcome"]), ("failed", "unknown"))


if __name__ == "__main__":
    unittest.main()

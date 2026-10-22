"""JLC assembly-readiness check: LCSC binding, 3-state stock freshness, Basic/Extended and BOM properties.

Components come from the native ``kicad-cli sch export netlist --format kicadxml``
of the captured schematic hierarchy, the same source the JLC BOM in
``fab_export`` is built from; the catalog is a captured offline snapshot.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import xml.etree.ElementTree as ET

from .candidate import safe_relative
from .catalog import decimal_number
from .fab_export import natural_key
from .hardware_interface import unique_object
from .jlc_stock_capture import DEFAULT_ENDPOINT, is_capture, response_paths, validate_capture
from .part_binding import NO_LCSC_VALUES, _is_no_lcsc
from .storage import digest, read_json, write_json
from .symbol_library import LibraryError, parse_sexpr


LCSC_RE = re.compile(r"^C[1-9][0-9]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REF_PREFIX_RE = re.compile(r"^([A-Za-z]+)[0-9]+$")

NETLIST_TIMEOUT = 600
NATIVE_FLAGS = frozenset(("dnp", "exclude_from_bom", "exclude_from_board"))
NON_NATIVE_MARKER_FIELDS = frozenset(("dnp", "dnf", "exclude_from_bom", "exclude_from_board"))
NON_NATIVE_MARKER_VALUES = frozenset(("DNP", "DNF", "DNI"))

DEFAULT_REQUIRED_PROPERTIES = (
    "Footprint",
    "LCSC",
    "Manufacturer",
    {"any_of": ["Partnumber", "PartNumber", "MPN", "CompanyPN"]},
)

CASE_INSENSITIVE_BUILTIN_PROPS = {
    "footprint": ("Footprint", "footprint"),
    "lcsc": ("LCSC", "lcsc", "LCSC Part", "LCSC_Part", "jlcpcb_part"),
    "manufacturer": ("Manufacturer", "manufacturer", "Mfr", "mfr", "MFR"),
}

RESISTOR_RATING_PROPERTIES = (
    "Power",
    "PowerRating",
    "Power_Rating",
    "Rating",
    "Voltage",
    "VoltageRating",
    "Voltage_Rating",
    "PARAM_power",
    "PARAM_voltage",
)

CAPACITOR_RATING_PROPERTIES = (
    "Voltage",
    "VoltageRating",
    "Voltage_Rating",
    "Rating",
    "WVDC",
    "PARAM_voltage",
)

ALLOWED_PARAM_KEYS = frozenset((
    "catalog",
    "catalog_sha256",
    "boards",
    "required_properties",
    "require_passive_ratings",
    "max_extended_parts",
    "max_stock_age_days",
    "max_age_hours",
    "stale_before",
    "as_of",
    "stock_floor",
    "calibration_inputs",
    "deviations",
    "hand_fit_policy",
    "rating_provenance",
    "operating_temperature",
))

# Native fields that may carry the exact manufacturer part number a DigiKey record must equal.
MPN_PROPERTIES = ("Partnumber", "PartNumber", "MPN")

CATALOG_SQLITE_COLUMNS = (
    "lcsc",
    "mfr",
    "mpn",
    "package",
    "category",
    "stock",
    "price1",
    "is_basic",
    "is_preferred",
    "last_seen",
    "attributes",
    "normalized",
    "provenance",
)


class AssemblyReadinessError(ValueError):
    """Base error for assembly readiness validation."""


class AssemblyReadinessExecutionError(AssemblyReadinessError):
    """Terminates assembly readiness with an explicit execution status."""

    def __init__(self, execution, message):
        super().__init__(message)
        self.execution = execution


def _reject_constant(value):
    raise ValueError(f"non-JSON numeric constant: {value}")


def _load_strict_json(path):
    try:
        raw = Path(path).read_text(encoding="utf-8")
        return json.loads(raw, object_pairs_hook=unique_object, parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise AssemblyReadinessExecutionError("failed", f"unreadable or invalid JSON at {path}: {exc}") from exc


def _resolve_captured_file(inputs_root, rel_path, label):
    try:
        rel = safe_relative(rel_path)
    except (ValueError, TypeError) as exc:
        raise AssemblyReadinessExecutionError("failed", f"unsafe {label} path {rel_path!r}: {exc}") from exc
    root = Path(inputs_root).resolve()
    candidate = Path(os.path.normpath(root / str(rel)))
    try:
        inside = candidate.is_relative_to(root)
    except ValueError:
        inside = False
    if not inside:
        raise AssemblyReadinessExecutionError("failed", f"{label} {rel_path!r} escapes captured inputs")
    if any((root / Path(*rel.parts[:end])).is_symlink() for end in range(1, len(rel.parts) + 1)):
        raise AssemblyReadinessExecutionError("failed", f"{label} {rel_path!r} must not be a symlink")
    if not candidate.is_file():
        raise AssemblyReadinessExecutionError("failed", f"missing {label} file: {rel_path!r}")
    return candidate, rel.as_posix()


def _parse_iso_date_or_datetime(value, label):
    """Return (date_obj, datetime_utc_or_none) for YYYY-MM-DD or ISO-8601 timestamp."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty ISO date or timestamp")
    text = value.strip()
    if ISO_DATE_RE.fullmatch(text):
        try:
            d = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"invalid ISO date for {label}: {value!r}") from exc
        return d, datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(iso_text)
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp for {label}: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.date(), dt


def _normalize_part_entry(lcsc_key, raw):
    if not isinstance(raw, dict):
        raise ValueError(f"catalog entry for {lcsc_key!r} must be an object")
    allowed = {"lcsc", "stock", "last_seen", "checked_at", "library_tier", "library_type", "tier",
               "is_basic", "is_preferred", "mfr", "manufacturer", "mpn", "part_number", "package",
               "category", "price1"}
    if set(raw) - allowed:
        raise ValueError(f"catalog entry {lcsc_key!r} has unknown keys: {sorted(set(raw) - allowed)}")
    lcsc = raw.get("lcsc", lcsc_key)
    if not isinstance(lcsc, str) or not LCSC_RE.fullmatch(lcsc):
        raise ValueError(f"invalid catalog LCSC ID: {lcsc!r}")
    if lcsc_key is not None and lcsc != lcsc_key:
        raise ValueError(f"catalog key {lcsc_key!r} mismatches part lcsc {lcsc!r}")

    stock = raw.get("stock")
    if stock is not None:
        if isinstance(stock, bool) or not isinstance(stock, int) or stock < 0:
            raise ValueError(f"catalog part {lcsc} stock must be non-negative int or null, got {stock!r}")

    last_seen = raw.get("last_seen") if "last_seen" in raw else raw.get("checked_at")
    if last_seen is not None:
        _parse_iso_date_or_datetime(last_seen, f"{lcsc}.last_seen")

    tier_raw = raw.get("library_tier") or raw.get("library_type") or raw.get("tier")
    is_basic = raw.get("is_basic")
    is_preferred = raw.get("is_preferred")

    if tier_raw is not None:
        if not isinstance(tier_raw, str):
            raise ValueError(f"catalog part {lcsc} library_tier must be a string")
        tier_norm = tier_raw.strip().capitalize()
        if tier_norm not in ("Basic", "Preferred", "Extended"):
            raise ValueError(f"catalog part {lcsc} has unsupported library_tier {tier_raw!r}")
        is_basic_flag = tier_norm == "Basic"
        is_preferred_flag = tier_norm == "Preferred"
        class_known = True
    else:
        if is_basic is not None and not isinstance(is_basic, (bool, int)):
            raise ValueError(f"catalog part {lcsc} is_basic must be bool or 0/1")
        if is_preferred is not None and not isinstance(is_preferred, (bool, int)):
            raise ValueError(f"catalog part {lcsc} is_preferred must be bool or 0/1")
        if isinstance(is_basic, int) and not isinstance(is_basic, bool) and is_basic not in (0, 1):
            raise ValueError(f"catalog part {lcsc} is_basic integer must be 0 or 1")
        if isinstance(is_preferred, int) and not isinstance(is_preferred, bool) and is_preferred not in (0, 1):
            raise ValueError(f"catalog part {lcsc} is_preferred integer must be 0 or 1")
        is_basic_flag = bool(is_basic)
        is_preferred_flag = bool(is_preferred) and not is_basic_flag
        # Basic is decided by is_basic alone; Preferred and Extended need both flags.
        class_known = is_basic_flag or (is_basic is not None and is_preferred is not None)
        if is_basic_flag:
            tier_norm = "Basic"
        elif is_preferred_flag:
            tier_norm = "Preferred"
        else:
            tier_norm = "Extended"

    mfr = raw.get("mfr") if "mfr" in raw else raw.get("manufacturer", "")
    mpn = raw.get("mpn") if "mpn" in raw else raw.get("part_number", "")
    package = raw.get("package", "")
    category = raw.get("category", "")
    price1 = raw.get("price1")
    if price1 is not None and (isinstance(price1, bool) or not isinstance(price1, (int, float))):
        raise ValueError(f"catalog part {lcsc} price1 must be numeric or null")

    return {
        "lcsc": lcsc,
        "mfr": str(mfr or ""),
        "mpn": str(mpn or ""),
        "package": str(package or ""),
        "category": str(category or ""),
        "stock": stock,
        "price1": float(price1) if price1 is not None else None,
        "is_basic": is_basic_flag,
        "is_preferred": is_preferred_flag,
        "tier": tier_norm,
        "class_known": class_known,
        "last_seen": last_seen.strip() if isinstance(last_seen, str) else None,
    }


def load_catalog_snapshot(inputs_root: Path, catalog_rel: str, *, expected_sha256: str | None = None) -> dict:
    """Load and validate an offline catalog snapshot (agent-ee.catalog/1 or JSON catalog snapshot)."""
    inputs_root = Path(inputs_root)
    catalog_path, norm_rel = _resolve_captured_file(inputs_root, catalog_rel, "catalog")
    actual_sha = digest(catalog_path)
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256):
            raise AssemblyReadinessExecutionError("failed", f"invalid expected_sha256: {expected_sha256!r}")
        if actual_sha != expected_sha256:
            raise AssemblyReadinessExecutionError(
                "failed",
                f"catalog {norm_rel!r} sha256 mismatch: expected {expected_sha256}, got {actual_sha}",
            )

    doc = _load_strict_json(catalog_path)
    if not isinstance(doc, dict):
        raise AssemblyReadinessExecutionError("failed", "catalog root must be a JSON object")

    schema = doc.get("schema")
    if schema == "agent-ee.catalog/1":
        return _load_sqlite_catalog_manifest(inputs_root, catalog_path, norm_rel, actual_sha, doc)
    if schema in ("agent-ee.catalog-snapshot/1", "agent-ee.jlc-catalog/1"):
        verified = _verify_jlc_capture(inputs_root, norm_rel, doc) if is_capture(doc) else None
        catalog = _load_json_catalog_snapshot(norm_rel, actual_sha, doc)
        catalog["capture"] = verified
        return catalog
    raise AssemblyReadinessExecutionError("failed", f"unsupported catalog schema: {schema!r}")


def _verify_jlc_capture(inputs_root, catalog_rel, doc):
    """A producer capture's parts must re-derive from its captured raw responses."""
    parent = PurePosixPath(catalog_rel).parent

    def read_response(rel):
        try:
            combined = (parent / safe_relative(rel)).as_posix()
        except ValueError as exc:
            raise ValueError(f"unsafe response path {rel!r}: {exc}") from exc
        path, _ = _resolve_captured_file(inputs_root, combined, "captured JLC response")
        return path.read_bytes()

    verdicts = {}
    try:
        info = validate_capture(doc, read_response, response_verdicts=verdicts)
        if doc["source"]["endpoint"] != DEFAULT_ENDPOINT:
            raise ValueError("JLC stock evidence requires the JLCPCB public search endpoint; LCSC and other sources are refused")
        return info
    except (OSError, ValueError) as exc:
        error = AssemblyReadinessExecutionError("failed", f"invalid agent-ee.jlc-catalog/1 capture: {exc}")
        error.response_verdicts = verdicts
        raise error from exc


def _capture_gate(capture, cfg, unknown_lcsc):
    """Refuse a producer capture not bound to the run, older than the declared staleness, or lacking an id."""
    if cfg["as_of"] is None:
        raise AssemblyReadinessExecutionError(
            "unsupported", "a jlc-catalog/1 capture requires params.as_of bound to the run")
    if cfg["max_age_hours"] is None and cfg["max_stock_age_days"] is None and cfg["stale_before"] is None:
        raise AssemblyReadinessExecutionError(
            "unsupported",
            "a jlc-catalog/1 capture requires a declared staleness (max_age_hours, max_stock_age_days or stale_before)")
    run_date, run_dt = _parse_iso_date_or_datetime(cfg["as_of"], "as_of")
    captured_date, captured_dt = _parse_iso_date_or_datetime(capture["as_of"], "capture as_of")
    age_hours = (run_dt - captured_dt).total_seconds() / 3600.0
    stale = None
    if captured_dt > run_dt:
        stale = f"capture as_of {capture['as_of']} is after the run as_of {cfg['as_of']}"
    elif _parse_iso_date_or_datetime(capture["completed_at"], "completed_at")[1] > run_dt:
        stale = "capture completed_at is after the run evaluation time"
    elif cfg["max_age_hours"] is not None and age_hours > cfg["max_age_hours"]:
        stale = f"capture is {age_hours:.1f} hour(s) old at the run (max_age_hours={cfg['max_age_hours']})"
    elif cfg["max_stock_age_days"] is not None and (run_date - captured_date).days > cfg["max_stock_age_days"]:
        stale = (f"capture is {(run_date - captured_date).days} day(s) old at the run "
                 f"(max_stock_age_days={cfg['max_stock_age_days']})")
    elif cfg["stale_before"] is not None and captured_date < _parse_iso_date_or_datetime(
            cfg["stale_before"], "stale_before")[0]:
        stale = f"capture as_of {capture['as_of']} is older than stale_before {cfg['stale_before']}"
    if stale:
        raise AssemblyReadinessExecutionError("failed", f"refusing stale jlc-catalog/1 capture: {stale}")
    # Only an observed not-found is evidence of absence; unrequested or failed ids are not.
    uncovered = sorted(set(unknown_lcsc) - set(capture["not_found"]), key=natural_key)
    if uncovered:
        failed = {lcsc: capture["failed"][lcsc] for lcsc in uncovered if lcsc in capture["failed"]}
        raise AssemblyReadinessExecutionError(
            "failed",
            f"jlc-catalog/1 capture lacks an observation for requested LCSC id(s) {uncovered}"
            + (f"; failed requests: {failed}" if failed else ""))


def _load_sqlite_catalog_manifest(inputs_root, manifest_path, manifest_rel, manifest_sha, doc):
    try:
        snapshot_date_str = doc["snapshot_date"]
        stale_before_str = doc["stale_before"]
        snap_date, _ = _parse_iso_date_or_datetime(snapshot_date_str, "snapshot_date")
        stale_date = None
        if stale_before_str is not None:
            stale_date, _ = _parse_iso_date_or_datetime(stale_before_str, "stale_before")
        if not ISO_DATE_RE.fullmatch(snapshot_date_str) or (stale_before_str is not None and not ISO_DATE_RE.fullmatch(stale_before_str)):
            raise ValueError("snapshot_date and stale_before must be YYYY-MM-DD")
        if stale_date is not None and stale_date > snap_date:
            raise ValueError("stale_before cannot be after snapshot_date")
        db_meta = doc["database"]
        if not isinstance(db_meta, dict):
            raise ValueError("database must be an object")
        db_rel_from_manifest = db_meta["path"]
        db_sha_expected = db_meta["sha256"]
        db_size_expected = db_meta["size_bytes"]
        if not isinstance(db_rel_from_manifest, str) or not db_rel_from_manifest:
            raise ValueError("database.path must be a non-empty string")
        if not isinstance(db_sha_expected, str) or not SHA256_RE.fullmatch(db_sha_expected):
            raise ValueError("database.sha256 must be 64 hex chars")
        if isinstance(db_size_expected, bool) or not isinstance(db_size_expected, int) or db_size_expected <= 0:
            raise ValueError("database.size_bytes must be a positive integer")
    except (KeyError, ValueError, TypeError) as exc:
        raise AssemblyReadinessExecutionError("failed", f"invalid agent-ee.catalog/1 manifest: {exc}") from exc

    rel_parent = PurePosixPath(manifest_rel).parent
    try:
        combined_rel = (rel_parent / safe_relative(db_rel_from_manifest)).as_posix()
    except ValueError as exc:
        raise AssemblyReadinessExecutionError("failed", f"unsafe database.path: {exc}") from exc

    db_path, db_norm_rel = _resolve_captured_file(inputs_root, combined_rel, "catalog database")
    try:
        db_bytes = db_path.read_bytes()
    except OSError as exc:
        raise AssemblyReadinessExecutionError("failed", f"unreadable catalog database: {exc}") from exc

    if len(db_bytes) != db_size_expected:
        raise AssemblyReadinessExecutionError(
            "failed",
            f"catalog database size mismatch: expected {db_size_expected}, got {len(db_bytes)}",
        )
    actual_db_sha = digest(db_path)
    if actual_db_sha != db_sha_expected:
        raise AssemblyReadinessExecutionError(
            "failed",
            f"catalog database sha256 mismatch: expected {db_sha_expected}, got {actual_db_sha}",
        )

    parts = {}
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(db_bytes)
        conn.execute("PRAGMA trusted_schema = OFF")
        quick = conn.execute("PRAGMA quick_check").fetchall()
        if quick != [("ok",)]:
            raise AssemblyReadinessExecutionError("failed", f"SQLite quick_check failed: {quick!r}")
        cols_info = conn.execute("PRAGMA table_info(component_base)").fetchall()
        col_names = tuple(row[1] for row in cols_info)
        if col_names != CATALOG_SQLITE_COLUMNS:
            raise AssemblyReadinessExecutionError(
                "failed",
                f"unexpected component_base columns: {col_names!r}",
            )
        cursor = conn.execute(
            "SELECT lcsc, mfr, mpn, package, category, stock, price1, "
            "is_basic, is_preferred, last_seen, attributes, normalized, provenance "
            "FROM component_base"
        )
        for row in cursor:
            (
                lcsc,
                mfr,
                mpn,
                package,
                category,
                stock,
                price1,
                is_basic,
                is_preferred,
                last_seen,
                _attributes,
                _normalized,
                _provenance,
            ) = row
            if lcsc in parts:
                raise AssemblyReadinessExecutionError("failed", f"duplicate LCSC {lcsc!r} in component_base")
            try:
                parts[lcsc] = _normalize_part_entry(
                    lcsc,
                    {
                        "lcsc": lcsc,
                        "mfr": mfr,
                        "mpn": mpn,
                        "package": package,
                        "category": category,
                        "stock": stock,
                        "price1": float(decimal_number(price1)) if isinstance(price1, str) else price1,
                        "is_basic": is_basic,
                        "is_preferred": is_preferred,
                        "last_seen": last_seen,
                    },
                )
            except ValueError as exc:
                raise AssemblyReadinessExecutionError("failed", f"invalid component_base row: {exc}") from exc
    except sqlite3.Error as exc:
        raise AssemblyReadinessExecutionError("failed", f"corrupt or unreadable SQLite catalog: {exc}") from exc
    finally:
        conn.close()

    return {
        "schema": doc["schema"],
        "catalog_rel": manifest_rel,
        "catalog_sha256": manifest_sha,
        "database_rel": db_norm_rel,
        "database_sha256": actual_db_sha,
        "snapshot_date": snapshot_date_str,
        "as_of": snapshot_date_str,
        "stale_before": stale_before_str,
        "parts": parts,
    }


def _load_json_catalog_snapshot(catalog_rel, catalog_sha, doc):
    as_of_raw = doc.get("snapshot_date") if "snapshot_date" in doc else doc.get("as_of")
    try:
        snap_date, _ = _parse_iso_date_or_datetime(as_of_raw, "snapshot_date/as_of")
    except ValueError as exc:
        raise AssemblyReadinessExecutionError("failed", str(exc)) from exc

    stale_before_raw = doc.get("stale_before")
    if stale_before_raw is not None:
        try:
            stale_date, _ = _parse_iso_date_or_datetime(stale_before_raw, "stale_before")
            if stale_date > snap_date:
                raise ValueError("stale_before cannot be after snapshot_date")
        except ValueError as exc:
            raise AssemblyReadinessExecutionError("failed", str(exc)) from exc

    raw_parts = doc.get("parts") if "parts" in doc else doc.get("components")
    if not isinstance(raw_parts, (list, dict)):
        raise AssemblyReadinessExecutionError("failed", "JSON catalog requires 'parts' or 'components' list or object")

    parts = {}
    try:
        if isinstance(raw_parts, dict):
            for lcsc_key, item in raw_parts.items():
                entry = _normalize_part_entry(lcsc_key, item)
                if entry["lcsc"] in parts:
                    raise ValueError(f"duplicate LCSC entry: {entry['lcsc']}")
                parts[entry["lcsc"]] = entry
        else:
            for item in raw_parts:
                entry = _normalize_part_entry(None, item)
                if entry["lcsc"] in parts:
                    raise ValueError(f"duplicate LCSC entry: {entry['lcsc']}")
                parts[entry["lcsc"]] = entry
    except ValueError as exc:
        raise AssemblyReadinessExecutionError("failed", f"invalid JSON catalog part entry: {exc}") from exc

    return {
        "schema": doc["schema"],
        "catalog_rel": catalog_rel,
        "catalog_sha256": catalog_sha,
        "database_rel": None,
        "database_sha256": None,
        "snapshot_date": snap_date.isoformat(),
        "as_of": as_of_raw.strip(),
        "stale_before": stale_before_raw.strip() if isinstance(stale_before_raw, str) else None,
        "parts": parts,
    }


def _native_flags(comp):
    """Native KiCad flags: valueless <property name="dnp|exclude_from_bom|exclude_from_board"/>.

    A user field that happens to share one of these names is exported as a
    property *with* a value attribute and is not a native flag.
    """
    return {
        p.get("name")
        for p in comp.findall("property")
        if p.get("name") in NATIVE_FLAGS and "value" not in p.attrib
    }


def verify_schematic_files(inputs_root: Path, files) -> dict:
    """Require every hierarchy file to be one complete ``(kicad_sch ...)``; return their hashes.

    kicad-cli exports a netlist with exit 0 even when a child sheet is
    truncated, silently dropping its components, so completeness is checked
    on the captured bytes before the export is trusted.
    """
    hashes = {}
    for rel in files:
        path, norm_rel = _resolve_captured_file(inputs_root, rel, "schematic")
        try:
            tree = parse_sexpr(path.read_bytes())
        except (OSError, LibraryError) as exc:
            raise AssemblyReadinessExecutionError("failed", f"schematic {norm_rel!r} does not parse: {exc}") from exc
        if not isinstance(tree, list) or not tree or tree[0] != "kicad_sch":
            raise AssemblyReadinessExecutionError("failed", f"schematic {norm_rel!r} root must be (kicad_sch ...)")
        hashes[norm_rel] = digest(path)
    return hashes


def verify_netlist_coverage(inputs_root: Path, files, extracted) -> dict:
    """Bind every placed symbol occurrence of the captured hierarchy to the netlist, and the reverse.

    kicad-cli exports a hierarchy with exit 0 and the full sheet count even
    when it cannot load a child sheet (an unknown token, a bad boolean), and
    drops that sheet's components. An occurrence is one sheet instance path
    plus symbol UUID, audited by ``native_binding.virtual_symbols``; only
    ``#``-referenced occurrences may be absent from the export. ``files`` is
    the ``sheet_instances`` list with the root first. Any gap fails.
    """
    from .footprint_library import FootprintError
    from .native_binding import OCCURRENCE_PROBLEMS, virtual_symbols

    schematics = {}
    for rel in files:
        path, norm_rel = _resolve_captured_file(inputs_root, rel, "schematic")
        schematics[norm_rel] = path.read_bytes()
    components = [
        c for bucket in ("populated", "excluded_dnp", "excluded_from_bom", "ignored_non_assembly")
        for c in extracted[bucket]
    ]
    graph = {
        "sheets": extracted["sheet_paths"],
        "components": {c["reference"]: {"sheet": c["sheet_path"], "uuid": c["uuid"]} for c in components},
    }
    try:
        records = virtual_symbols(schematics, next(iter(schematics)), graph)
    except FootprintError as exc:
        raise AssemblyReadinessExecutionError(
            "failed", f"schematic hierarchy cannot be bound to the native netlist: {exc}") from exc
    gaps = [r for r in records if "problem" in r]
    if gaps:
        named = sorted({ref for r in gaps for ref in r["references"]}, key=natural_key)
        kinds = sorted({r["problem"] for r in gaps})
        raise AssemblyReadinessExecutionError(
            "failed",
            "native netlist does not cover the captured hierarchy ("
            + "; ".join(f"{k}: {OCCURRENCE_PROBLEMS[k]}" for k in kinds)
            + f"); references: {named or 'unnamed'}; affected sheet files: "
            + f"{sorted({r['file'] for r in gaps if r.get('file')})}",
        )
    return {
        "bound_components": len(components),
        "exporter_omitted": sorted((r["references"][0] for r in records), key=natural_key),
    }


def read_netlist_components(netlist_path: Path) -> dict:
    """Classify components of a native ``kicad-cli sch export netlist --format kicadxml`` file.

    KiCad has already resolved the hierarchy, per-instance references,
    multi-unit symbols and the native DNP / in_bom / on_board attributes, and it
    omits power symbols (PWR_FLAG included). Buckets are disjoint: off-board
    (``exclude_from_board``) and ``#`` references are non-assembly, then native
    ``dnp``, then native ``exclude_from_bom``; everything else is populated.
    """
    try:
        root = ET.parse(netlist_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise AssemblyReadinessExecutionError("failed", f"native XML netlist does not parse: {exc}") from exc
    components_node = root.find("components") if root.tag == "export" else None
    if components_node is None:
        raise AssemblyReadinessExecutionError("failed", "native XML netlist has no <export>/<components> section")

    seen, identities = set(), set()
    ignored_non_assembly, excluded_dnp, excluded_from_bom, populated = [], [], [], []
    for comp in components_node.findall("comp"):
        ref = (comp.get("ref") or "").strip()
        if not ref:
            raise AssemblyReadinessExecutionError("failed", "native XML netlist has a component without a reference")
        if ref in seen:
            raise AssemblyReadinessExecutionError("failed", f"native XML netlist repeats component {ref}")
        seen.add(ref)
        fields = {}
        for field in comp.findall("./fields/field"):
            name = field.get("name")
            if not name:
                raise AssemblyReadinessExecutionError("failed", f"{ref} has a field without a name")
            if name in fields:
                raise AssemblyReadinessExecutionError("failed", f"{ref} repeats native field {name}")
            fields[name] = (field.text or "").strip()
        properties = {}
        for prop in comp.findall("property"):
            name, value = prop.get("name"), prop.get("value")
            if value is None:
                continue
            if name in properties or (name in fields and fields[name] != value.strip()):
                raise AssemblyReadinessExecutionError("failed", f"{ref} has contradictory native property {name}")
            properties[name] = value.strip()
        fields.update(properties)
        value = (comp.findtext("value") or "").strip()
        footprint = (comp.findtext("footprint") or "").strip()
        fields["Value"] = value
        if footprint or "Footprint" not in fields:
            fields["Footprint"] = footprint
        libsource = comp.find("libsource")
        lib_id = f"{libsource.get('lib', '')}:{libsource.get('part', '')}" if libsource is not None else ""
        sheetpath = comp.find("sheetpath")
        sheet_path = (sheetpath.get("tstamps") or "").strip() if sheetpath is not None else ""
        uuids = (comp.findtext("tstamps") or "").split()
        if not sheet_path or not uuids:
            raise AssemblyReadinessExecutionError("failed", f"native XML netlist component {ref} has no sheet/UUID identity")
        if any((sheet_path, uid) in identities for uid in uuids) or len(set(uuids)) != len(uuids):
            raise AssemblyReadinessExecutionError("failed", f"native XML netlist repeats the identity of {ref}")
        identities.update((sheet_path, uid) for uid in uuids)
        flags = _native_flags(comp)
        record = {
            "reference": ref,
            "lib_id": lib_id,
            "sheet": sheetpath.get("names") if sheetpath is not None else None,
            "sheet_path": sheet_path,
            "uuid": " ".join(uuids),
            "dnp": "dnp" in flags,
            "in_bom": "exclude_from_bom" not in flags,
            "on_board": "exclude_from_board" not in flags,
            "properties": fields,
        }
        if ref.startswith("#"):
            ignored_non_assembly.append({**record, "ignore_reason": "virtual_symbol"})
        elif not record["on_board"]:
            ignored_non_assembly.append({**record, "ignore_reason": "off_board"})
        elif record["dnp"]:
            excluded_dnp.append(record)
        elif not record["in_bom"]:
            excluded_from_bom.append(record)
        else:
            populated.append(record)

    def ordered(items):
        return sorted(items, key=lambda c: natural_key(c["reference"]))

    sheet_paths = [(sheet.get("tstamps") or "").strip() for sheet in root.findall("./design/sheet")]
    return {
        "components": len(seen),
        "sheets": len(sheet_paths),
        "sheet_paths": sheet_paths,
        "populated": ordered(populated),
        "excluded_dnp": ordered(excluded_dnp),
        "excluded_from_bom": ordered(excluded_from_bom),
        "ignored_non_assembly": ordered(ignored_non_assembly),
    }


def _non_native_markers(props):
    """Field-level DNP/exclusion conventions that native exports and JLC BOMs ignore."""
    markers = [
        name for name in props
        if name.lower() in NON_NATIVE_MARKER_FIELDS and _truthy_prop(props[name])
    ]
    if props.get("Value", "").strip().upper() in NON_NATIVE_MARKER_VALUES:
        markers.append(f"Value={props['Value'].strip()}")
    return sorted(markers)


def _truthy_prop(value):
    return isinstance(value, str) and value.strip().lower() in ("yes", "true", "1", "y")


def _nonempty_prop_value(val):
    if not isinstance(val, str):
        return ""
    stripped = val.strip()
    if stripped in ("", "~"):
        return ""
    return stripped


def _lookup_single_property(props, prop_name):
    """Look up a single property name on a symbol's property dict."""
    lower = prop_name.lower()
    if lower in CASE_INSENSITIVE_BUILTIN_PROPS:
        for candidate in CASE_INSENSITIVE_BUILTIN_PROPS[lower]:
            val = _nonempty_prop_value(props.get(candidate, ""))
            if val:
                return val
        for k, v in props.items():
            if k.lower() == lower:
                val = _nonempty_prop_value(v)
                if val:
                    return val
        return ""
    return _nonempty_prop_value(props.get(prop_name, ""))


def normalize_required_properties(raw_policy):
    """Validate and normalize `required_properties` parameter into a list of rule specs."""
    if raw_policy is None:
        raw_policy = list(DEFAULT_REQUIRED_PROPERTIES)
    if not isinstance(raw_policy, list) or not raw_policy:
        raise ValueError("required_properties must be a non-empty list")
    normalized = []
    for entry in raw_policy:
        if isinstance(entry, str):
            text = entry.strip()
            if not text:
                raise ValueError("required_properties entry must not be empty")
            if "|" in text:
                choices = [p.strip() for p in text.split("|") if p.strip()]
                if len(choices) < 2:
                    raise ValueError(f"invalid pipe-delimited property spec: {entry!r}")
                normalized.append({"label": text, "any_of": choices})
            else:
                normalized.append({"label": text, "any_of": [text]})
        elif isinstance(entry, dict):
            if set(entry) != {"any_of"}:
                raise ValueError("required_properties dict entry must have exact key 'any_of'")
            choices = entry["any_of"]
            if (
                not isinstance(choices, list)
                or not choices
                or any(not isinstance(c, str) or not c.strip() for c in choices)
            ):
                raise ValueError("any_of must be a non-empty list of property names")
            clean_choices = [c.strip() for c in choices]
            label = "|".join(clean_choices)
            normalized.append({"label": label, "any_of": clean_choices})
        else:
            raise ValueError(f"unsupported required_properties entry: {entry!r}")
    return normalized


def _passive_kind(comp):
    """Return 'R' for resistor, 'C' for capacitor, or None."""
    lib_id = comp.get("lib_id", "")
    lib_part = lib_id.split(":", 1)[-1].upper() if ":" in lib_id else lib_id.upper()
    ref = comp.get("reference", "")
    m = REF_PREFIX_RE.match(ref)
    prefix = m.group(1).upper() if m else ""
    if prefix == "R" or lib_part in ("R", "R_SMALL", "R_US", "R_PACK04"):
        return "R"
    if prefix == "C" or lib_part in ("C", "CP", "C_SMALL", "C_POLARIZED", "CP_SMALL"):
        return "C"
    return None


def _has_passive_rating(props, kind):
    candidates = RESISTOR_RATING_PROPERTIES if kind == "R" else CAPACITOR_RATING_PROPERTIES
    lower_candidates = {c.lower() for c in candidates}
    for key in candidates:
        if _nonempty_prop_value(props.get(key, "")):
            return True
    for k, v in props.items():
        if k.lower() in lower_candidates and _nonempty_prop_value(v):
            return True
    return False


def validate_params(params):
    """Validate check parameters and return normalized config dict."""
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    unknown = set(params) - ALLOWED_PARAM_KEYS
    if unknown:
        raise ValueError(f"unknown assembly_readiness parameter(s): {sorted(unknown)}")

    catalog = params.get("catalog")
    if not isinstance(catalog, str) or not catalog.strip():
        raise ValueError("assembly_readiness requires a non-empty 'catalog' path")
    rel = safe_relative(catalog)
    if rel.as_posix() != catalog:
        raise ValueError(f"catalog path must be normalized relative path: {catalog!r}")

    catalog_sha256 = params.get("catalog_sha256")
    if catalog_sha256 is not None:
        if not isinstance(catalog_sha256, str) or not SHA256_RE.fullmatch(catalog_sha256):
            raise ValueError("catalog_sha256 must be a 64-character lowercase hex digest")

    boards = params.get("boards", 1)
    if isinstance(boards, bool) or not isinstance(boards, int) or boards < 1:
        raise ValueError("boards must be a positive integer >= 1")

    req_props = normalize_required_properties(params.get("required_properties"))

    require_passive_ratings = params.get("require_passive_ratings", True)
    if not isinstance(require_passive_ratings, bool):
        raise ValueError("require_passive_ratings must be a boolean")

    max_extended_parts = params.get("max_extended_parts")
    if max_extended_parts is not None:
        if isinstance(max_extended_parts, bool) or not isinstance(max_extended_parts, int) or max_extended_parts < 0:
            raise ValueError("max_extended_parts must be an integer >= 0")

    max_stock_age_days = params.get("max_stock_age_days")
    if max_stock_age_days is not None:
        if (
            isinstance(max_stock_age_days, bool)
            or not isinstance(max_stock_age_days, (int, float))
            or max_stock_age_days < 0
        ):
            raise ValueError("max_stock_age_days must be a non-negative number")

    max_age_hours = params.get("max_age_hours")
    if max_age_hours is not None:
        if isinstance(max_age_hours, bool) or not isinstance(max_age_hours, (int, float)) or max_age_hours < 0:
            raise ValueError("max_age_hours must be a non-negative number")

    stale_before = params.get("stale_before")
    if stale_before is not None:
        _parse_iso_date_or_datetime(stale_before, "stale_before")

    as_of = params.get("as_of")
    if as_of is not None:
        _parse_iso_date_or_datetime(as_of, "as_of")

    stock_floor = params.get("stock_floor", 0)
    if isinstance(stock_floor, bool) or not isinstance(stock_floor, int) or stock_floor < 0:
        raise ValueError("stock_floor must be an integer >= 0")

    deviations = params.get("deviations")
    if deviations is not None and str(safe_relative(deviations)) != deviations:
        raise ValueError("deviations must be a normalized captured path")

    hand_fit_policy = params.get("hand_fit_policy")
    if hand_fit_policy is not None:
        hand_fit_policy = _validate_hand_fit_policy(hand_fit_policy, deviations)

    rating_provenance = params.get("rating_provenance")
    if rating_provenance is not None:
        rating_provenance = _validate_rating_provenance(rating_provenance)

    operating_temperature = params.get("operating_temperature")
    if operating_temperature is not None:
        operating_temperature = _validate_operating_temperature(operating_temperature)
        if deviations in _temperature_sources(operating_temperature):
            raise ValueError("operating_temperature exemption source cannot be the mutable deviations file")

    return {
        "catalog": catalog,
        "catalog_sha256": catalog_sha256,
        "boards": boards,
        "required_properties": req_props,
        "require_passive_ratings": require_passive_ratings,
        "max_extended_parts": max_extended_parts,
        "max_stock_age_days": max_stock_age_days,
        "max_age_hours": max_age_hours,
        "stale_before": stale_before.strip() if isinstance(stale_before, str) else None,
        "as_of": as_of.strip() if isinstance(as_of, str) else None,
        "stock_floor": stock_floor,
        "deviations": deviations,
        "hand_fit_policy": hand_fit_policy,
        "rating_provenance": rating_provenance,
        "operating_temperature": operating_temperature,
    }


def _validate_rating_provenance(raw):
    """Captured binding-fill receipt (coordinator-pinned) and its binding-fill.json artifact."""
    from .hardware_interface import exact_keys
    exact_keys(raw, "receipt receipt_sha256 report")
    for key in ("receipt", "report"):
        if not isinstance(raw[key], str) or str(safe_relative(raw[key])) != raw[key]:
            raise ValueError(f"rating_provenance.{key} must be a normalized captured path")
    if raw["receipt"] == raw["report"]:
        raise ValueError("rating_provenance receipt and report must be distinct files")
    if not isinstance(raw["receipt_sha256"], str) or not SHA256_RE.fullmatch(raw["receipt_sha256"]):
        raise ValueError("rating_provenance.receipt_sha256 must be a 64-character lowercase hex digest")
    return dict(raw)


def _validate_operating_temperature(raw):
    """Declared ambient range in degC and the captured DigiKey records bound to references."""
    from .digikey_record import EVIDENCE_KEYS, PACKAGE_MAP_KEY, evidence_declaration
    from .hardware_interface import exact_keys
    if not isinstance(raw, dict):
        raise ValueError("operating_temperature must be an object")
    optional = [key for key in (PACKAGE_MAP_KEY, "exemptions", "reduced_acceptance", "delegated_acceptance",
                                "thermal_evidence") if key in raw]
    exact_keys(raw, " ".join(["min_c max_c capture capture_sha256 parts", *optional]))
    bounds = []
    for key in ("min_c", "max_c"):
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"operating_temperature.{key} must be a finite number of degC")
        bounds.append(Decimal(str(value)))
    if bounds[0] > bounds[1]:
        raise ValueError("operating_temperature.min_c exceeds max_c")
    evidence = evidence_declaration({key: raw[key] for key in (*EVIDENCE_KEYS, PACKAGE_MAP_KEY) if key in raw},
                                    "operating_temperature")
    exemptions = raw.get("exemptions", {})
    if not isinstance(exemptions, dict) or ("exemptions" in raw and not exemptions):
        raise ValueError("operating_temperature.exemptions must map references to owner citations")
    for ref, citation in exemptions.items():
        if not isinstance(ref, str) or not ref.strip() or ref != ref.strip():
            raise ValueError("operating_temperature.exemptions must map trimmed references")
        if ref in raw["parts"]:
            raise ValueError(f"operating_temperature reference {ref!r} is both judged and exempt")
        _validate_citation(citation, f"operating_temperature.exemptions.{ref}")
        if _design_source(citation["source"]):
            raise ValueError("operating_temperature exemption source cannot be a writer-mutable KiCad design file")
        if not _reference_token(ref).search(citation["quote"]):
            raise ValueError(f"operating_temperature.exemptions.{ref}.quote does not name {ref!r}")
    declarations = {}
    for field in ("reduced_acceptance", "thermal_evidence"):
        entries = raw.get(field, {})
        if not isinstance(entries, dict) or (field in raw and not entries):
            raise ValueError(f"operating_temperature.{field} must map references to citations")
        declarations[field] = {}
        for ref, entry in entries.items():
            if ref not in evidence["parts"] or ref in exemptions:
                raise ValueError(f"{field} reference {ref!r} must have a DigiKey parts binding")
            if not isinstance(entry, dict):
                raise ValueError(f"{field}.{ref} must be an object")
            citation = {k: v for k, v in entry.items() if field != "reduced_acceptance" or k not in {"min_c", "max_c"}}
            _validate_citation(citation, f"{field}.{ref}")
            if _design_source(citation["source"]):
                raise ValueError(f"{field} source cannot be a writer-mutable KiCad design file")
            if not _reference_token(ref).search(citation["quote"]):
                raise ValueError(f"{field} quote does not name {ref!r}")
            declarations[field][ref] = dict(entry)
            if field == "reduced_acceptance":
                limits = []
                for key in ("min_c", "max_c"):
                    value = entry.get(key)
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                        raise ValueError(f"reduced_acceptance.{ref}.{key} must be finite degC")
                    limits.append(Decimal(str(value)))
                if limits[0] > limits[1]:
                    raise ValueError("reduced acceptance min_c exceeds max_c")
                declarations[field][ref].update(zip(("min_c", "max_c"), limits))
    delegated = raw.get("delegated_acceptance", {})
    if not isinstance(delegated, dict) or ("delegated_acceptance" in raw and not delegated):
        raise ValueError("operating_temperature.delegated_acceptance must map references to delegated records")
    declarations["delegated_acceptance"] = {}
    for ref, entry in delegated.items():
        label = f"delegated_acceptance.{ref}"
        if ref not in evidence["parts"] or ref in exemptions:
            raise ValueError(f"delegated_acceptance reference {ref!r} must have a DigiKey parts binding")
        if ref in declarations["reduced_acceptance"]:
            raise ValueError(f"{ref} cannot be both owner-accepted and delegated")
        declarations["delegated_acceptance"][ref] = _validate_delegated_acceptance(entry, ref, label, bounds[0])
    if (set(declarations["thermal_evidence"]) - set(declarations["reduced_acceptance"])
            - set(declarations["delegated_acceptance"])):
        raise ValueError("thermal_evidence requires reduced_acceptance or delegated_acceptance for the reference")
    return {**evidence, "min_c": bounds[0], "max_c": bounds[1], **declarations,
            "exemptions": {ref: dict(c) for ref, c in exemptions.items()}}


# Recognized delegation sources: kind -> required file name. Anything else is unrecognized and refused.
DELEGATION_KINDS = {"repository_agents_md": "AGENTS.md"}
DECISION_ID_RE = re.compile(r"D-[A-Z0-9]+-[1-9][0-9]*", re.ASCII)
TEMPERATURE_FILES = frozenset({"operating-temperature-acceptances.txt", "operating-temperature-thermal.txt",
                               "operating-temperature-exemptions.txt"})
TEMPERATURE_EXCEPTION_SCOPE = "operating_temperature_selection_exception"
# Owner grants of delegated engineering authority (AGENTEE-310). A captured delegation is recognized only
# when it names one of these by kind, repository and commit, its cited list item is byte-identical to the
# item the owner committed there, and the whole captured file is one of the approved owner snapshots: the
# complete AGENTS.md (full-file SHA-256) at an owner commit whose surrounding text was reviewed to leave the
# grant unconditional and in force. A newly composed file that quotes, fences, conditions or rescopes the
# item is not a snapshot. Grants and snapshots are pinned only by a reviewed agent-ee change that traced them
# to the owner's commits; text a deciding agent writes, copies or captures never extends this table.
OWNER_GRANTS = {
    ("repository_agents_md", "github.com/halbritt/fome-mc", "3f959131abca2dd9d40400496f3f6ebac9d363f5"): {
        # AGENTS.md "Owner-reserved decisions are made autonomously, not parked." (owner direction 2026-10-20)
        "path": "AGENTS.md", "author": "halbritt <halbritt@gmail.com>",
        "item_sha256": "4087feebb9654f736f4eba5cc0401df9faa9cf7c819e2f4c83f039815033b569",
        "scopes": (TEMPERATURE_EXCEPTION_SCOPE,),
        # Full-file SHA-256 -> the owner commit that wrote that AGENTS.md. fb1aa094 only adds the separate
        # gating-provenance item after the grant; the pod commit 7ab81564 carries the fb1aa094 file unchanged.
        "snapshots": {
            "36b520a0d29200075454ba29dea7993f3df246645738231c1d4555e404b6ab01":
                "3f959131abca2dd9d40400496f3f6ebac9d363f5",
            "31a0b80e72a2f03498e96580a929d8056348ee119ca89f874c45586999e745df":
                "fb1aa0949b865efbdcd386b6de34e6da54b9dcd1"}},
}
# Decisions recorded under a pinned owner grant, each with the exact part it authorizes. record_sha256 is the
# SHA-256 of the decision's JSON member ("key": "text", unescaped UTF-8) at the recording commit; snapshots
# are the approved complete decision files (full-file SHA-256 -> owner commit), the recording commit's and
# the pod commit's, in which no other record withdraws, conditions or rescopes the decision. Registered
# under agent-509 request A (2026-10-22), which names D-PCB-9 and D-PCB-12 as the pod use case.
DELEGATED_DECISIONS = {
    ("github.com/halbritt/fome-mc", "D-PCB-9"): {
        "grant_commit": "3f959131abca2dd9d40400496f3f6ebac9d363f5",
        "commit": "40bb1374cd251c338a60f8602e1ae8c208042199",
        "path": "telemetry-pod/expansion/design-rules/constraints.json",
        "record_sha256": "11a296670cb894ed882af826d0190f35a886ecdeb74cff12b8db172e1266dcde",
        "scope": TEMPERATURE_EXCEPTION_SCOPE, "reference": "U3", "subject": "U3 u-blox NEO-M9N",
        "min_c": Decimal("-40"), "max_c": Decimal("85"), "mpn": "NEO-M9N-00B", "manufacturer": "u-blox",
        "normalization": "the decision names the NEO-M9N product; NEO-M9N-00B is its ordering code, the "
                         "pod schematic part number",
        "snapshots": {
            "6fdb9ecbb8a37b23c6cb63c75bf4b1b0a038811e42eb4c80e4e14e8ce8b02f5d":
                "40bb1374cd251c338a60f8602e1ae8c208042199",
            "e7e095b3ca595fdfb3c73fc22ad87c326fbc1c152523e85f2da4df4d908ea2e4":
                "7ab81564dd638ed68b664f3036a78187ce5760bd"}},
    ("github.com/halbritt/fome-mc", "D-PCB-12"): {
        "grant_commit": "3f959131abca2dd9d40400496f3f6ebac9d363f5",
        "commit": "b6ba9a09e7b4f572a3d8e5ad109107fd30ccc5c5",
        "path": "telemetry-pod/expansion/design-rules/constraints.json",
        "record_sha256": "97c4530f63f0d0937314cb2086d990e05f54c494b046cd21ec7352d3d2262ebb",
        "scope": TEMPERATURE_EXCEPTION_SCOPE, "reference": "J3", "subject": "J3 Hirose DM3AT-SF-PEJM5",
        "min_c": Decimal("-25"), "max_c": Decimal("85"), "mpn": "DM3AT-SF-PEJM5",
        "manufacturer": "Hirose Electric Co Ltd", "normalization": None,
        "snapshots": {
            "e375e19c3df7f4f66b6bdaf710fa1c33a73987d1fa7d037f7c699e3d1ec5b17a":
                "b6ba9a09e7b4f572a3d8e5ad109107fd30ccc5c5",
            "e7e095b3ca595fdfb3c73fc22ad87c326fbc1c152523e85f2da4df4d908ea2e4":
                "7ab81564dd638ed68b664f3036a78187ce5760bd"}},
}


def _design_source(source):
    """Writer-mutable KiCad design files and library tables are never citation sources."""
    source = PurePosixPath(source)
    return (source.suffix.lower().startswith(".kicad_") or source.suffix.lower() in {".sch", ".net", ".pro"}
            or source.name.lower() in {"sym-lib-table", "fp-lib-table"})


def _validate_delegated_acceptance(entry, ref, label, board_min):
    """Delegated-engineering record: its own authority kind, a recorded decision and a repository delegation."""
    from .hardware_interface import exact_keys
    exact_keys(entry, "authority min_c max_c decision delegation")
    if entry["authority"] != "delegated_engineering":
        raise ValueError(f"{label}.authority must be 'delegated_engineering'; owner acceptance is declared in "
                         "reduced_acceptance with the owner file")
    limits = []
    for key in ("min_c", "max_c"):
        value = entry[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{label}.{key} must be finite degC")
        limits.append(Decimal(str(value)))
    if limits[0] > limits[1]:
        raise ValueError("delegated acceptance min_c exceeds max_c")
    if limits[0] > board_min:
        raise ValueError(f"{label} may only reduce the maximum; min_c is warmer than the board requirement")
    decision, delegation = entry["decision"], entry["delegation"]
    exact_keys(decision, "id source sha256 line quote")
    exact_keys(delegation, "kind repository commit source sha256 line quote")
    if not isinstance(decision["id"], str) or not DECISION_ID_RE.fullmatch(decision["id"]):
        raise ValueError(f"{label}.decision.id must be a recorded decision id such as D-PCB-9")
    if delegation["kind"] not in DELEGATION_KINDS:
        raise ValueError(f"{label}.delegation.kind {delegation['kind']!r} is not a recognized delegation")
    citations = {}
    for name, raw in (("decision", decision), ("delegation", delegation)):
        citations[name] = {k: raw[k] for k in ("source", "sha256", "line", "quote")}
        _validate_citation(citations[name], f"{label}.{name}")
        source = PurePosixPath(raw["source"])
        if _design_source(source):
            raise ValueError(f"{label}.{name} source cannot be a writer-mutable KiCad design file")
        if source.name.lower() in TEMPERATURE_FILES:
            raise ValueError(f"{label}.{name} source cannot be an owner, thermal or exemption temperature file")
    if decision["source"] == delegation["source"]:
        raise ValueError(f"{label}: a decision cannot be its own delegation")
    if PurePosixPath(delegation["source"]).name != DELEGATION_KINDS[delegation["kind"]]:
        raise ValueError(f"{label}.delegation.source must be the repository's "
                         f"{DELEGATION_KINDS[delegation['kind']]}")
    # Authority comes only from the pinned owner grant and its registered decision, never from the cited files.
    grant = OWNER_GRANTS.get((delegation["kind"], delegation["repository"], delegation["commit"]))
    if grant is None:
        raise ValueError(f"{label}.delegation is not a pinned owner grant: {delegation['repository']!r} at "
                         f"{delegation['commit']!r} is not a recorded owner commit")
    registered = DELEGATED_DECISIONS.get((delegation["repository"], decision["id"]))
    if registered is None or registered["grant_commit"] != delegation["commit"]:
        raise ValueError(f"{label}.decision {decision['id']} is not a registered decision under that owner grant")
    if registered["scope"] not in grant["scopes"] or registered["scope"] != TEMPERATURE_EXCEPTION_SCOPE:
        raise ValueError(f"{label}: the owner grant does not cover an operating-temperature selection exception")
    if registered["reference"] != ref:
        raise ValueError(f"{label}: decision {decision['id']} authorizes {registered['reference']}, not {ref}")
    # Both citations are files of the same repository checkout, at the paths the registration pinned.
    root = delegation["source"][:len(delegation["source"]) - len(grant["path"])]
    if root + grant["path"] != delegation["source"] or root not in {"", root.rstrip("/") + "/"} or \
            decision["source"] != root + registered["path"]:
        raise ValueError(f"{label}: decision and delegation must be {registered['path']!r} and {grant['path']!r} "
                         "of one repository")
    if not _reference_token(ref).search(decision["quote"]):
        raise ValueError(f"{label}.decision.quote does not name {ref!r}")
    return {"authority": "delegated_engineering", "min_c": limits[0], "max_c": limits[1],
            "decision": dict(decision), "delegation": dict(delegation), "registered": registered,
            "grant": grant}


def _temperature_sources(spec):
    """Every captured file an operating_temperature declaration cites."""
    sources = [c["source"] for key in ("exemptions", "reduced_acceptance", "thermal_evidence")
               for c in spec[key].values()]
    for entry in spec["delegated_acceptance"].values():
        sources += [entry["decision"]["source"], entry["delegation"]["source"]]
    return sources


def _reference_token(ref):
    """The reference as a case-insensitive whole token, so R1 never matches R10."""
    return re.compile(r"(?<![A-Za-z0-9_/-])" + re.escape(ref) + r"(?![A-Za-z0-9_/-])", re.I | re.ASCII)


def _validate_citation(raw, label):
    """``{source, sha256, line, quote}`` citing one line of a captured owner decision."""
    from .hardware_interface import exact_keys, text
    exact_keys(raw, "source sha256 line quote")
    source = raw["source"]
    if not isinstance(source, str) or str(safe_relative(source)) != source:
        raise ValueError(f"{label}.source must be a normalized captured path")
    if not isinstance(raw["sha256"], str) or not SHA256_RE.fullmatch(raw["sha256"]):
        raise ValueError(f"{label}.sha256 must be a 64-character lowercase hex digest")
    if isinstance(raw["line"], bool) or not isinstance(raw["line"], int) or raw["line"] < 1:
        raise ValueError(f"{label}.line must be a positive integer")
    text(raw["quote"])
    if "\n" in raw["quote"] or "\r" in raw["quote"]:
        raise ValueError(f"{label}.quote must be text from one source line")


def _verify_exemption(inputs_root, ref, citation, captured_files):
    """Verify the pinned dedicated file in full, then bind the reference to its cited line."""
    try:
        path, rel = _resolve_captured_file(inputs_root, citation["source"], f"{ref} exemption source")
    except AssemblyReadinessExecutionError as exc:
        raise ValueError(str(exc)) from exc
    actual = digest(path)
    if actual != citation["sha256"]:
        raise ValueError(f"{ref} exemption source {rel!r} differs from its declared sha256")
    if captured_files is not None and captured_files.get(rel) != actual:
        raise ValueError(f"{ref} exemption source {rel!r} is not a captured input")
    try:
        content = path.read_bytes().decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{ref} exemption source {rel!r} is unreadable: {exc}") from exc
    if path.name != "operating-temperature-exemptions.txt":
        raise ValueError(f"{ref} exemption source must be a dedicated operating-temperature-exemptions.txt file")
    # Only LF separates statements; stripping more than one newline could hide blank lines.
    lines = content.removesuffix("\n").split("\n")
    statements = [_canonical_exemption_refs(line) for line in lines]
    if citation["line"] > len(lines) or citation["quote"] not in lines[citation["line"] - 1]:
        raise ValueError(f"{ref} exemption quote is absent from {rel!r} line {citation['line']}")
    if ref.casefold() not in statements[citation["line"] - 1]:
        raise ValueError(f"the canonical exemption does not name {ref!r}")


def _verify_temperature_citation(inputs_root, ref, citation, captured_files, *, thermal=False):
    """Bind a reference and range to an immutable, canonical ASCII evidence file."""
    name = "operating-temperature-thermal.txt" if thermal else "operating-temperature-acceptances.txt"
    try:
        path, rel = _resolve_captured_file(inputs_root, citation["source"], f"{ref} temperature evidence")
    except AssemblyReadinessExecutionError as exc:
        raise ValueError(str(exc)) from exc
    actual = digest(path)
    if actual != citation["sha256"]:
        raise ValueError(f"{rel!r} differs from its declared sha256")
    if captured_files is not None and captured_files.get(rel) != actual:
        raise ValueError(f"{rel!r} is not a captured input")
    if path.name != name:
        raise ValueError(f"temperature evidence requires dedicated {name}")
    try:
        lines = path.read_bytes().decode("ascii").removesuffix("\n").split("\n")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"unreadable temperature evidence: {exc}") from exc
    number = r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?"
    phrase = "expected operating temperature is" if thermal else "is accepted for operating temperature"
    pattern = re.compile(r"([A-Za-z]+[0-9]+) " + phrase + rf" ({number}) to ({number}) degC\.", re.I | re.ASCII)
    statements = {}
    for index, line in enumerate(lines, 1):
        match = pattern.fullmatch(line)
        if match is None:
            raise ValueError("every line must be a complete canonical ASCII temperature statement")
        identity, low, high = match.groups()
        if identity.casefold() in statements or Decimal(low) > Decimal(high):
            raise ValueError("temperature statements must name each reference once with ordered limits")
        statements[identity.casefold()] = (index, Decimal(low), Decimal(high))
    if (citation["line"] > len(lines) or citation["quote"] != lines[citation["line"] - 1]
            or ref.casefold() not in statements or statements[ref.casefold()][0] != citation["line"]):
        raise ValueError("temperature citation must quote the exact reference's complete line")
    _, low, high = statements[ref.casefold()]
    return low, high


# A recorded decision names its subject first, then states that part's rated range, e.g.
# "U3 u-blox NEO-M9N (-40..85 C)" or "J3 Hirose DM3AT-SF-PEJM5 (-25..+85 C per the LCSC listing)".
DECISION_NUMBER = r"[-+]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?"
DECISION_WITHDRAWN_RE = re.compile(r"\b(?:retired|withdrawn|superseded|revoked|reversed)\b", re.I | re.ASCII)
DECISION_OWNER_RE = re.compile(r"\bowner\b", re.I | re.ASCII)
DECISION_AUTONOMOUS_RE = re.compile(r"\bDecided autonomously\b", re.ASCII)


def _pinned_source(inputs_root, citation, label, captured_files):
    """Pinned, captured UTF-8 text of a cited source."""
    try:
        path, rel = _resolve_captured_file(inputs_root, citation["source"], label)
    except AssemblyReadinessExecutionError as exc:
        raise ValueError(str(exc)) from exc
    actual = digest(path)
    if actual != citation["sha256"]:
        raise ValueError(f"{label} {rel!r} differs from its declared sha256")
    if captured_files is not None and captured_files.get(rel) != actual:
        raise ValueError(f"{label} {rel!r} is not a captured input")
    try:
        return rel, path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label} {rel!r} is unreadable: {exc}") from exc


def _cited_line(content, citation, label, rel):
    lines = content.removesuffix("\n").split("\n")
    if citation["line"] > len(lines) or citation["quote"] not in lines[citation["line"] - 1]:
        raise ValueError(f"{label} quote is absent from {rel!r} line {citation['line']}")
    return lines, citation["line"] - 1


def _verify_delegated_acceptance(inputs_root, ref, record, captured_files, evidence):
    """Bind a delegated record to its registered decision, the pinned owner grant and the exact part."""
    decision, delegation, registered = record["decision"], record["delegation"], record["registered"]
    rel, content = _pinned_source(inputs_root, decision, f"{ref} decision source", captured_files)
    try:
        doc = json.loads(content)
    except ValueError as exc:
        raise ValueError(f"{ref} decision source {rel!r} is not a JSON decision record: {exc}") from exc
    recorded = doc.get("decisions") if isinstance(doc, dict) else None
    if (not isinstance(recorded, dict) or not recorded
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in recorded.items())):
        raise ValueError(f"{ref} decision source {rel!r} has no recorded decisions object")
    keys = [key for key in recorded if key.startswith(decision["id"] + " ")]
    if len(keys) != 1:
        raise ValueError(f"decision {decision['id']} is not recorded exactly once in {rel!r}")
    key, text = keys[0], recorded[keys[0]]
    if DECISION_OWNER_RE.search(key):
        raise ValueError(f"decision {decision['id']} is recorded as an owner decision, not delegated engineering")
    if DECISION_WITHDRAWN_RE.search(key) or DECISION_WITHDRAWN_RE.search(text):
        raise ValueError(f"decision {decision['id']} is withdrawn, retired or superseded")
    if not DECISION_AUTONOMOUS_RE.search(text) or re.search(r"\bnot autonomous\b", text, re.I):
        raise ValueError(f"decision {decision['id']} does not record an autonomous delegated decision")
    lines, index = _cited_line(content, decision, f"{ref} decision", rel)
    members = {json.dumps(key, ensure_ascii=ascii) + ": " + json.dumps(text, ensure_ascii=ascii)
               for ascii in (False, True)}
    if lines[index].strip().removesuffix(",") not in members or decision["quote"] not in text:
        raise ValueError(f"decision {decision['id']} is not the complete record on {rel!r} line {decision['line']}")
    member = json.dumps(key, ensure_ascii=False) + ": " + json.dumps(text, ensure_ascii=False)
    if hashlib.sha256(member.encode("utf-8")).hexdigest() != registered["record_sha256"]:
        raise ValueError(f"decision {decision['id']} in {rel!r} is not the record registered from commit "
                         f"{registered['commit']}")
    rated = re.compile(re.escape(registered["subject"]) + r" \((" + DECISION_NUMBER + r")\s*\.\.\s*(" +
                       DECISION_NUMBER + r")\s*(?:°\s*)?C\b", re.ASCII)
    matches = list(rated.finditer(text))
    if len(matches) != 1 or matches[0].start() != 0:
        raise ValueError(f"decision {decision['id']} does not state {registered['subject']!r} as its subject "
                         "with one rated range")
    if matches[0].group(0) not in decision["quote"]:
        raise ValueError(f"decision {decision['id']} quote does not cite {ref}'s rated range")
    low, high = (Decimal(matches[0].group(n)) for n in (1, 2))
    if (low, high) != (record["min_c"], record["max_c"]) or (low, high) != (registered["min_c"], registered["max_c"]):
        raise ValueError(f"delegated range differs from decision {decision['id']} range {low} to {high} degC")
    rel, content = _pinned_source(inputs_root, delegation, f"{ref} delegation source", captured_files)
    lines, index = _cited_line(content, delegation, f"{ref} delegation", rel)
    if not lines[index].startswith("- "):
        raise ValueError(f"{ref} delegation line {delegation['line']} of {rel!r} is not a delegation statement")
    unit = [lines[index]]
    for line in lines[index + 1:]:
        if not line.startswith(" ") or not line.strip():
            break
        unit.append(line)
    # The grant is the owner's exact committed item, never a statement that merely mentions autonomy.
    if hashlib.sha256("\n".join(unit).encode("utf-8")).hexdigest() != record["grant"]["item_sha256"]:
        raise ValueError(f"{ref} delegation statement in {rel!r} is not the owner grant committed at "
                         f"{delegation['repository']} {delegation['commit']}")
    # The grant authorizes only in its owner context: both whole files must be approved owner snapshots, so a
    # composed file that quotes, conditions or rescopes the exact item or decision is never authority.
    if delegation["sha256"] not in record["grant"]["snapshots"]:
        raise ValueError(f"{ref} delegation source {rel!r} is not an approved owner snapshot of "
                         f"{delegation['repository']} {record['grant']['path']}; the grant's context is unverified")
    if decision["sha256"] not in registered["snapshots"]:
        raise ValueError(f"decision {decision['id']} source {decision['source']!r} is not an approved snapshot of "
                         f"{delegation['repository']} {registered['path']}; the decision's context is unverified")
    # The decision authorizes one exact part: the evaluated part must be it, by MPN and manufacturer.
    if evidence["mpn"] != registered["mpn"] or evidence["manufacturer"].casefold() != \
            registered["manufacturer"].casefold():
        raise ValueError(f"decision {decision['id']} authorizes {registered['manufacturer']} {registered['mpn']}, "
                         f"not {evidence['manufacturer']} {evidence['mpn']}")
    return low, high


def _operating_temperature(inputs_root, spec, populated, captured_files):
    """Declared ambient against each bound part's DigiKey 'Operating Temperature'; never stock evidence."""
    from .digikey_record import (PACKAGE_MAP_KEY, ambient_verdict, load_package_map, package_verdict,
                                 part_evidence, replay_evidence)
    findings, components, exempt, reduced = [], [], [], []
    report = {"min_c": str(spec["min_c"]), "max_c": str(spec["max_c"]), "capture": spec["capture"],
              "capture_sha256": spec["capture_sha256"], "package_map": spec.get(PACKAGE_MAP_KEY),
              "components": components, "exempt": exempt, "reduced_acceptance": reduced, "unlisted": [], "error": None}
    by_ref = {}
    for comp in populated:
        by_ref.setdefault(comp["reference"], []).append(comp)
    # Every populated part needs a verdict; legacy exemptions always yield unknown.
    unlisted = sorted(set(by_ref) - set(spec["parts"]) - set(spec["exemptions"]), key=natural_key)
    report["unlisted"] = unlisted
    if unlisted:
        findings.append({"severity": "unknown", "code": "operating_temperature_unlisted", "references": unlisted,
                         "message": "populated part(s) without a DigiKey operating-temperature verdict or an "
                                    "exemption: " + ", ".join(unlisted)})
    for ref in sorted(spec["exemptions"], key=natural_key):
        citation = spec["exemptions"][ref]
        entry = {"reference": ref, "status": "unknown", "citation": citation, "reason": None}
        exempt.append(entry)
        try:
            if len(by_ref.get(ref, [])) != 1:
                raise ValueError(f"{ref} is not exactly one populated component")
            _verify_exemption(inputs_root, ref, citation, captured_files)
            raise ValueError("operating-temperature exemptions are retired; migrate to reduced_acceptance with "
                             "a DigiKey parts binding and captured thermal_evidence")
        except ValueError as exc:
            entry["status"], entry["reason"] = "unknown", str(exc)
            findings.append({"severity": "unknown", "code": "operating_temperature_exemption_invalid",
                             "reference": ref, "message": f"{ref}: {exc}"})
    try:
        doc, dependencies = replay_evidence(inputs_root, spec, captured_files)
        package_map = load_package_map(inputs_root, spec, captured_files)
    except (OSError, ValueError, KeyError) as exc:
        # An unreadable or unpinned capture is unknown temperature coverage, never a pass.
        report["error"] = str(exc)
        findings.append({"severity": "unknown", "code": "operating_temperature_unavailable",
                         "references": sorted(spec["parts"], key=natural_key),
                         "message": f"DigiKey operating-temperature capture is unavailable: {exc}"})
        return report, findings
    for ref in sorted(spec["parts"], key=natural_key):
        entry = {"reference": ref, "key": spec["parts"][ref], "status": "unknown", "reason": None}
        components.append(entry)
        acceptance = spec["reduced_acceptance"].get(ref)
        if acceptance is not None:
            entry.update({"citation": {k: v for k, v in acceptance.items() if k not in {"min_c", "max_c"}},
                          "accepted_range": {k: str(acceptance[k]) for k in ("min_c", "max_c")},
                          "rating": None, "thermal": {"status": "unknown", "reason": "pending thermal validation"}})
            reduced.append(entry)
        delegated = spec["delegated_acceptance"].get(ref)
        if delegated is not None:
            # The agent authored the decision; the authority is the repository delegation, never the owner.
            registered = delegated["registered"]
            entry.update({"authority": "delegated_engineering", "author": "agent",
                          "decision": delegated["decision"], "delegation": delegated["delegation"],
                          "authorized_part": {"mpn": registered["mpn"], "manufacturer": registered["manufacturer"],
                                              "normalization": registered["normalization"]},
                          "accepted_range": {k: str(delegated[k]) for k in ("min_c", "max_c")},
                          "rating": None, "thermal": {"status": "unknown", "reason": "pending thermal validation"}})
            report.setdefault("delegated_acceptance", []).append(entry)
        try:
            if len(by_ref.get(ref, [])) != 1:
                raise ValueError(f"{ref} is not exactly one populated component")
            props = by_ref[ref][0]["properties"]
            evidence = part_evidence(doc, spec, dependencies, spec["parts"][ref], "operating_temperature")
            entry["digikey"] = evidence
            numbers = {_nonempty_prop_value(props.get(name, "")) for name in MPN_PROPERTIES} - {""}
            if numbers != {evidence["mpn"]}:
                raise ValueError(f"{ref} part number {sorted(numbers)} is not DigiKey MPN {evidence['mpn']!r}")
            manufacturer = _nonempty_prop_value(props.get("Manufacturer", ""))
            if manufacturer and manufacturer.casefold() != evidence["manufacturer"].casefold():
                raise ValueError(f"{ref} manufacturer {manufacturer!r} is not DigiKey "
                                 f"{evidence['manufacturer']!r}")
            if package_map is not None:
                verdict, reason = package_verdict(evidence, props.get("Footprint", "").strip(), package_map)
                entry["package"] = {"status": verdict, "reason": reason, "footprint": props.get("Footprint", ""),
                                    "digikey_package": evidence["package"],
                                    "package_source": evidence["package_source"]}
                if verdict == "defect":
                    # The record describes another package: a determinate binding defect, and no temperature evidence.
                    findings.append({"severity": "defect", "code": "digikey_package_mismatch", "reference": ref,
                                     "message": f"{ref}: {reason}"})
                    raise ValueError(f"{ref}: DigiKey package does not bind to the footprint")
                if verdict == "unknown":
                    raise ValueError(reason)
            if evidence["value"] is None:
                raise ValueError(f"DigiKey record {evidence['key']} lists no {evidence['parameter']!r}")
            minimum, maximum = spec["min_c"], spec["max_c"]
            if acceptance is not None:
                entry["rating"] = evidence["value"]
                minimum, maximum = _verify_temperature_citation(inputs_root, ref, acceptance, captured_files)
                if (minimum, maximum) != (acceptance["min_c"], acceptance["max_c"]):
                    raise ValueError("owner citation range differs from declared reduced acceptance")
            if delegated is not None:
                entry["rating"] = evidence["value"]
                minimum, maximum = _verify_delegated_acceptance(inputs_root, ref, delegated, captured_files,
                                                               evidence)
            entry["status"], entry["reason"] = ambient_verdict(evidence["value"], minimum, maximum)
            if acceptance is not None or delegated is not None:
                thermal = spec["thermal_evidence"].get(ref)
                if thermal is not None:
                    entry["thermal"]["citation"] = thermal
                    try:
                        low, high = _verify_temperature_citation(inputs_root, ref, thermal, captured_files, thermal=True)
                        verdict, reason = ambient_verdict(evidence["value"], low, high)
                        entry["thermal"].update(status=verdict, reason=reason, min_c=str(low), max_c=str(high))
                    except ValueError as exc:
                        entry["thermal"]["reason"] = f"pending thermal validation: {exc}"
                # A determinate rating defect cannot be hidden by missing or invalid thermal evidence.
                if entry["status"] != "defect" and entry["thermal"]["status"] != "pass":
                    entry["status"], entry["reason"] = entry["thermal"]["status"], entry["thermal"]["reason"]
        except ValueError as exc:
            entry["reason"] = str(exc)
        if entry["status"] != "pass":
            defect = entry["status"] == "defect"
            findings.append({"severity": entry["status"],
                             "code": "operating_temperature_out_of_range" if defect else "operating_temperature_unknown",
                             "reference": ref, "message": f"{ref}: {entry['reason']}"})
    return report, findings


def _validate_hand_fit_policy(raw, deviations):
    """Scope-held citation of a captured owner decision naming the exact hand-fit identities."""
    from .hardware_interface import exact_keys, text
    from .part_decisions import names_identity, part
    exact_keys(raw, "source sha256 line quote identities" + (" selection" if "selection" in raw else ""))
    source = raw["source"]
    if not isinstance(source, str) or str(safe_relative(source)) != source:
        raise ValueError("hand_fit_policy.source must be a normalized captured path")
    if source == deviations:
        raise ValueError("hand_fit_policy.source cannot be the mutable deviations file")
    if not isinstance(raw["sha256"], str) or not SHA256_RE.fullmatch(raw["sha256"]):
        raise ValueError("hand_fit_policy.sha256 must be a 64-character lowercase hex digest")
    if isinstance(raw["line"], bool) or not isinstance(raw["line"], int) or raw["line"] < 1:
        raise ValueError("hand_fit_policy.line must be a positive integer")
    text(raw["quote"])
    if "\n" in raw["quote"] or "\r" in raw["quote"]:
        raise ValueError("hand_fit_policy.quote must be text from one source line")
    identities = raw["identities"]
    if not isinstance(identities, list) or not identities:
        raise ValueError("hand_fit_policy.identities must name at least one exact identity")
    selection = _validate_hand_fit_selection(raw["selection"], raw, deviations) if "selection" in raw else None
    for identity in identities:
        part(identity)
        if identity["mpn"] is None:
            raise ValueError("hand_fit_policy identity requires an exact MPN")
        if selection is None and not names_identity(identity["mpn"], raw["quote"]):
            raise ValueError(f"hand_fit_policy.quote does not name {identity['mpn']!r}")
        if selection is not None:
            # The owner quote names the category; the pinned selection decision names the exact identity.
            for token in (identity["mpn"], identity["lcsc"]):
                if token is None or not names_identity(token, selection["quote"]):
                    raise ValueError(f"hand_fit_policy.selection.quote does not name {token!r}; a category "
                                     "selection must name the exact MPN and LCSC")
    if len({(i["mpn"], i["lcsc"]) for i in identities}) != len(identities):
        raise ValueError("duplicate hand_fit_policy identity")
    if selection is not None and [(i["mpn"], i["lcsc"]) for i in identities] != [
            (selection["mpn"], selection["lcsc"])]:
        raise ValueError("hand_fit_policy.identities must be exactly the one identity the selection sentence names")
    return {key: raw[key] for key in ("source", "sha256", "line", "quote")} | {
        "identities": [dict(i) for i in identities]} | ({"selection": selection} if selection is not None else {})


def _hand_fit_source(raw, label, deviations):
    """A pinned, normalized, non-design captured citation path with a SHA-256."""
    source = raw["source"]
    if not isinstance(source, str) or str(safe_relative(source)) != source:
        raise ValueError(f"{label}.source must be a normalized captured path")
    if source == deviations:
        raise ValueError(f"{label}.source cannot be the mutable deviations file")
    if _design_source(source):
        raise ValueError(f"{label}.source cannot be a writer-mutable KiCad design file")
    if not isinstance(raw["sha256"], str) or not SHA256_RE.fullmatch(raw["sha256"]):
        raise ValueError(f"{label}.sha256 must be a 64-character lowercase hex digest")


def _validate_hand_fit_selection(raw, policy, deviations):
    """Category-to-identity source chain (AGENTEE-207): the owner quote names a part category, never the MPN.

    The owner statement stays the only hand-fit authority and is cited unchanged. The selection is a captured
    decision line naming both that category and the exact MPN/LCSC; each record is a captured JSON object at a
    pointer carrying that exact identity. Nothing here is agent testimony: every link is pinned bytes."""
    from .hardware_interface import exact_keys, text
    from .part_decisions import names_identity
    label = "hand_fit_policy.selection"
    exact_keys(raw, "category source sha256 line quote records")
    text(raw["category"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _/.-]*[A-Za-z0-9]", raw["category"]):
        raise ValueError(f"{label}.category must be one category term from the owner quote")
    if not names_identity(raw["category"], policy["quote"]):
        raise ValueError(f"hand_fit_policy.quote does not name the category {raw['category']!r}")
    _hand_fit_source(raw, label, deviations)
    if raw["source"] == policy["source"]:
        raise ValueError(f"{label}.source must be a decision other than the owner hand-fit statement")
    if isinstance(raw["line"], bool) or not isinstance(raw["line"], int) or raw["line"] < 1:
        raise ValueError(f"{label}.line must be a positive integer")
    text(raw["quote"])
    if "\n" in raw["quote"] or "\r" in raw["quote"]:
        raise ValueError(f"{label}.quote must be text from one source line")
    if not names_identity(raw["category"], raw["quote"]):
        raise ValueError(f"{label}.quote does not name the category {raw['category']!r}")
    sentence = _selection_sentence(raw["quote"], raw["category"])
    records = raw["records"]
    if not isinstance(records, list) or not records:
        raise ValueError(f"{label}.records must name at least one captured identity record")
    for index, record in enumerate(records):
        exact_keys(record, "source sha256 pointer")
        _hand_fit_source(record, f"{label}.records[{index}]", deviations)
        if not isinstance(record["pointer"], str) or not (record["pointer"] == "" or record["pointer"].startswith("/")):
            raise ValueError(f"{label}.records[{index}].pointer must be a JSON pointer")
    if len({(r["source"], r["pointer"]) for r in records}) != len(records):
        raise ValueError(f"duplicate {label} record")
    if len(records) < 2:
        raise ValueError(f"{label}.records must pin both the manifest entry and the part record it names")
    return {key: raw[key] for key in ("category", "source", "sha256", "line", "quote")} | sentence | {
        "records": [dict(r) for r in records]}


# The one recognised affirmative selection form (AGENTEE-207), the form of the pod's captured D-PCB-10:
# "J1 is the 35-position RIGHT-ANGLE TE AMPSEAL 1-776163-1 (LCSC C596198; footprint PodConnector:AMPSEAL_35_1-776163-1)".
# Anything else -- negated, conditional, hedged or merely different wording -- is unsupported, never inferred.
SELECTION_SENTENCE_RE = re.compile(
    r"(?P<reference>[A-Z]+[0-9]+) is the (?P<description>[A-Za-z0-9][A-Za-z0-9 -]*[A-Za-z0-9]) "
    r"(?P<mpn>[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]) \(LCSC (?P<lcsc>C[0-9]+); "
    r"footprint (?P<footprint>[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+)\)", re.ASCII)
UNAFFIRMED_SELECTION_RE = re.compile(
    r"(?:(?<![A-Za-z0-9_])(?:not|never|no|nor|neither|none|cannot|without|instead|rather|but|however|reject|"
    r"rejected|rejects|rejection|replace|replaced|replacing|supersede|superseded|withdraw|withdrawn|revoke|revoked|"
    r"rescind|rescinded|void|former|formerly|previous|previously|alternative|alternate|proposed|tentative|"
    r"tentatively|pending|maybe|perhaps|possibly|might|may|could|would|tbd|tbc)(?![A-Za-z0-9_])|n't)", re.I)
SELECTION_PREFIX_RE = re.compile(r'\s*(?:[-*+]\s+)?|\s*"(?P<key>[^"\\]*)"\s*:\s*"')
SENTENCE_END_RE = re.compile(r'[.!?](?=\s|"|$)')


def _unaffirmed(text):
    """The first negating, revoking, hedging or conditional word in text, or None."""
    match = UNAFFIRMED_SELECTION_RE.search(text) or CONDITIONAL_RE.search(text)
    return match.group(0) if match else None


def _selection_sentence(quote, category):
    """Parse the cited selection quote; only the recognised affirmative form binds a category to an identity."""
    from .part_decisions import names_identity
    label = "hand_fit_policy.selection.quote"
    match = SELECTION_SENTENCE_RE.fullmatch(quote)
    if match is None:
        raise ValueError(f"{label} is not the recognised affirmative selection form 'REF is the <category "
                         "description> MPN (LCSC Cnnn; footprint Library:Footprint)'; other wording is unsupported")
    word = _unaffirmed(match["description"])
    if word is not None:
        raise ValueError(f"{label} description is not an affirmative selection ({word!r})")
    if not names_identity(category, match["description"]):
        raise ValueError(f"{label} does not describe the selected part as the category {category!r}")
    return {key: match[key] for key in ("reference", "mpn", "lcsc", "footprint")}


def _selection_context(content, selection):
    """The cited quote must be a whole affirmative statement and the decision's only mention of the identity."""
    lines = content.splitlines()
    line = lines[selection["line"] - 1]
    if line.count(selection["quote"]) != 1:
        raise ValueError("the selection quote must occur exactly once on its line")
    position = line.index(selection["quote"])
    prefix = SELECTION_PREFIX_RE.fullmatch(line[:position])
    if prefix is None:
        raise ValueError(f"the selection quote does not open its statement (preceded by {line[:position].strip()!r})")
    word = _unaffirmed(prefix["key"] or "")
    if word is not None:
        raise ValueError(f"the selection statement's label is not an affirmative selection ({word!r})")
    rest = line[position + len(selection["quote"]):]
    end = SENTENCE_END_RE.search(rest)
    if end is None:
        raise ValueError("the selection sentence does not end on its cited line")
    word = _unaffirmed(rest[:end.start()])
    if word is not None:
        raise ValueError(f"the selection sentence continues with a non-affirmative qualifier ({word!r})")
    for key in ("mpn", "lcsc", "footprint"):
        pattern = r"(?<![A-Za-z0-9_/-])" + re.escape(selection[key]) + r"(?![A-Za-z0-9_/-])"
        count = len(re.findall(pattern, content))
        if count != 1:
            raise ValueError(f"the selection decision names {selection[key]!r} {count} times; the selection "
                             "sentence must be its only mention")


def _json_pointer(doc, pointer):
    for token in pointer.split("/")[1:] if pointer else []:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(doc, list) and re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(doc):
            doc = doc[int(token)]
        elif isinstance(doc, dict) and token in doc:
            doc = doc[token]
        else:
            raise ValueError(f"pointer {pointer!r} does not resolve")
    return doc


def capability(params, files, inputs=None):
    """Return capability descriptor for scope/3 and baseline compilation."""
    cfg = validate_params(params)
    catalog_rel = cfg["catalog"]
    if catalog_rel not in files:
        raise ValueError(f"catalog {catalog_rel!r} must be a captured input")
    if cfg["deviations"] is not None and cfg["deviations"] not in files:
        raise ValueError("deviations must be a captured input")

    immutable_inputs = [catalog_rel]
    if cfg["hand_fit_policy"] is not None:
        if cfg["hand_fit_policy"]["source"] not in files:
            raise ValueError("hand_fit_policy.source must be a captured input")
        immutable_inputs.append(cfg["hand_fit_policy"]["source"])
        selection = cfg["hand_fit_policy"].get("selection")
        for rel in ([selection["source"], *(r["source"] for r in selection["records"])] if selection else []):
            if rel not in files:
                raise ValueError("hand_fit_policy.selection sources must be captured inputs")
            if rel not in immutable_inputs:
                immutable_inputs.append(rel)
    if cfg["rating_provenance"] is not None:
        for key in ("receipt", "report"):
            if cfg["rating_provenance"][key] not in files:
                raise ValueError(f"rating_provenance.{key} must be a captured input")
            immutable_inputs.append(cfg["rating_provenance"][key])
    if cfg["operating_temperature"] is not None:
        from .digikey_record import evidence_inputs
        cited = _temperature_sources(cfg["operating_temperature"])
        for rel in [*evidence_inputs(cfg["operating_temperature"], inputs), *cited]:
            if rel not in files:
                raise ValueError(f"operating_temperature evidence file {rel!r} must be a captured input")
            immutable_inputs.append(rel)
    if inputs is not None:
        catalog_path = Path(inputs) / catalog_rel
        if catalog_path.is_file():
            try:
                raw_doc = json.loads(catalog_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                raw_doc = None
            if is_capture(raw_doc):
                parent = PurePosixPath(catalog_rel).parent
                for rel in response_paths(raw_doc):
                    response_rel = (parent / safe_relative(rel)).as_posix()
                    if response_rel not in files:
                        raise ValueError(f"captured JLC response {response_rel!r} must be a captured input")
                    immutable_inputs.append(response_rel)
            if isinstance(raw_doc, dict) and raw_doc.get("schema") == "agent-ee.catalog/1":
                db_meta = raw_doc.get("database")
                if isinstance(db_meta, dict) and isinstance(db_meta.get("path"), str):
                    parent = PurePosixPath(catalog_rel).parent
                    db_rel = (parent / safe_relative(db_meta["path"])).as_posix()
                    if db_rel not in files:
                        raise ValueError(f"catalog database {db_rel!r} must be a captured input")
                    immutable_inputs.append(db_rel)

    predicates = [
        "assembly:bom-properties",
        "assembly:lcsc-binding",
        "assembly:stock-coverage",
    ]
    if cfg["require_passive_ratings"]:
        predicates.append("assembly:passive-ratings")
    if cfg["max_extended_parts"] is not None:
        predicates.append("assembly:extended-parts-limit")
    if cfg["rating_provenance"] is not None:
        predicates.append("assembly:rating-provenance")
    if cfg["operating_temperature"] is not None:
        predicates.append("assembly:operating-temperature")

    return {
        "immutable_inputs": sorted(set(immutable_inputs)),
        "predicates": predicates,
    }


def _stale_reason(cfg, catalog, lcsc, last_seen):
    """Why a catalog row's stock observation is stale or unverified at the evaluation time; None when fresh."""
    as_of_raw = cfg["as_of"] or catalog["as_of"] or catalog["snapshot_date"]
    as_of_date, as_of_dt = _parse_iso_date_or_datetime(as_of_raw, "as_of")
    effective_stale_before_str = cfg["stale_before"] or catalog["stale_before"]
    stale_before_date = None
    if effective_stale_before_str is not None:
        stale_before_date, _ = _parse_iso_date_or_datetime(effective_stale_before_str, "stale_before")
    max_stock_age_days = cfg["max_stock_age_days"]
    max_age_hours = cfg["max_age_hours"]
    if last_seen is None:
        return "missing last_seen timestamp"
    if stale_before_date is None and max_stock_age_days is None and max_age_hours is None:
        return ("no maximum stock age is declared (catalog or param stale_before, "
                "max_stock_age_days or max_age_hours)")
    seen_date, seen_dt = _parse_iso_date_or_datetime(last_seen, f"{lcsc}.last_seen")
    age_days = (as_of_date - seen_date).days
    age_hours = (as_of_dt - seen_dt).total_seconds() / 3600.0
    if seen_dt > as_of_dt:
        return f"last_seen {last_seen} is after the evaluation date {as_of_raw}"
    if stale_before_date is not None and seen_date < stale_before_date:
        return f"last_seen {last_seen} is older than stale_before {effective_stale_before_str}"
    if max_stock_age_days is not None and age_days > max_stock_age_days:
        return (f"last_seen {last_seen} is {age_days} day(s) old as of {as_of_raw} "
                f"(max_stock_age_days={max_stock_age_days})")
    if max_age_hours is not None and age_hours > max_age_hours:
        return (f"last_seen {last_seen} is {age_hours:.1f} hour(s) old as of {as_of_raw} "
                f"(max_age_hours={max_age_hours})")
    return None


def _verify_hand_fit_policy(inputs, policy):
    """The cited owner decision must be the pinned captured bytes and hold the exact quote on its line."""
    path, rel = _resolve_captured_file(inputs, policy["source"], "hand_fit_policy source")
    actual = digest(path)
    if actual != policy["sha256"]:
        raise AssemblyReadinessExecutionError(
            "failed", f"hand_fit_policy source {rel!r} sha256 mismatch: expected {policy['sha256']}, got {actual}")
    try:
        lines = path.read_bytes().decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise AssemblyReadinessExecutionError("failed", f"unreadable hand_fit_policy source: {exc}") from exc
    if policy["line"] > len(lines) or policy["quote"] not in lines[policy["line"] - 1]:
        raise AssemblyReadinessExecutionError(
            "failed", f"hand_fit_policy quote is absent from {rel!r} line {policy['line']}")
    selection = policy.get("selection")
    try:
        unit = _policy_unit(lines, policy["line"] - 1)
        # With a selection chain the owner statement must declare the category itself unconditionally.
        for name in [selection["category"]] if selection else [i["mpn"] for i in policy["identities"]]:
            _require_unconditional(unit, name)
    except ValueError as exc:
        raise AssemblyReadinessExecutionError(
            "failed", f"hand_fit_policy {rel!r} line {policy['line']}: {exc}") from exc
    if selection is None:
        return policy | {"class": "owner_hand_fit"}
    return policy | {"class": "owner_hand_fit", "selection": _verify_hand_fit_selection(inputs, policy)}


def _verify_hand_fit_selection(inputs, policy):
    """The complete chain is mandatory: an affirmative pinned decision, a manifest entry pinning the decision's
    footprint, and the part record that entry names. Every link is pinned bytes carrying the one identity."""
    selection, identities = policy["selection"], policy["identities"]

    def pinned(citation, label):
        path, rel = _resolve_captured_file(inputs, citation["source"], label)
        actual = digest(path)
        if actual != citation["sha256"]:
            raise AssemblyReadinessExecutionError(
                "failed", f"{label} {rel!r} sha256 mismatch: expected {citation['sha256']}, got {actual}")
        try:
            return rel, path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise AssemblyReadinessExecutionError("failed", f"unreadable {label}: {exc}") from exc

    rel, content = pinned(selection, "hand_fit_policy selection source")
    lines = content.splitlines()
    if selection["line"] > len(lines) or selection["quote"] not in lines[selection["line"] - 1]:
        raise AssemblyReadinessExecutionError(
            "failed", f"hand_fit_policy selection quote is absent from {rel!r} line {selection['line']}")
    try:
        _selection_context(content, selection)
    except ValueError as exc:
        raise AssemblyReadinessExecutionError(
            "unsupported", f"hand_fit_policy selection {rel!r} line {selection['line']}: {exc}") from exc
    wanted = {(i["mpn"], i["lcsc"]) for i in identities}
    hashes, matched, links, footprints = {r["source"]: r["sha256"] for r in selection["records"]}, set(), [], {}
    for record in selection["records"]:
        rel, content = pinned(record, "hand_fit_policy selection record")
        try:
            obj = _json_pointer(json.loads(content), record["pointer"])
        except ValueError as exc:
            raise AssemblyReadinessExecutionError(
                "failed", f"hand_fit_policy selection record {rel!r}: {exc}") from exc
        found = (obj.get("mpn"), obj.get("lcsc")) if isinstance(obj, dict) else None
        if found not in wanted:
            raise AssemblyReadinessExecutionError(
                "failed", f"hand_fit_policy selection record {rel!r} at {record['pointer']!r} carries "
                          f"{found}, not a policy identity")
        matched.add(found)
        # CAD provenance: every footprint the chain pins for an identity must agree; the design must carry it.
        if "footprint" in obj:
            if not isinstance(obj["footprint"], str) or not obj["footprint"]:
                raise AssemblyReadinessExecutionError(
                    "failed", f"hand_fit_policy selection record {rel!r} has a malformed footprint")
            if footprints.setdefault(found, obj["footprint"]) != obj["footprint"]:
                raise AssemblyReadinessExecutionError(
                    "failed", f"hand_fit_policy selection records pin conflicting footprints for {found}: "
                              f"{footprints[found]!r} and {obj['footprint']!r}")
        # A manifest entry naming its part record must name the chain's pinned record bytes.
        named = obj.get("selection") if isinstance(obj.get("selection"), dict) else {}
        if "part_record" in named or "part_record_sha256" in named:
            if hashes.get(named.get("part_record")) != named.get("part_record_sha256"):
                raise AssemblyReadinessExecutionError(
                    "failed", f"hand_fit_policy selection record {rel!r} names part record "
                              f"{named.get('part_record')!r} {named.get('part_record_sha256')!r}, which is not a "
                              "pinned record of the chain")
            if "lcsc" in named and named["lcsc"] != found[1]:
                raise AssemblyReadinessExecutionError(
                    "failed", f"hand_fit_policy selection record {rel!r} selects {named['lcsc']!r}, not {found[1]!r}")
            if not isinstance(obj.get("footprint"), str):
                raise AssemblyReadinessExecutionError(
                    "unsupported", f"hand_fit_policy selection manifest entry {rel!r} at {record['pointer']!r} "
                                   "pins no footprint; the chain's CAD provenance is unknown")
            links.append({"record": rel, "pointer": record["pointer"], "part_record": named["part_record"],
                          "part_record_sha256": named["part_record_sha256"]})
    if matched != wanted:
        raise AssemblyReadinessExecutionError(
            "failed", f"hand_fit_policy selection records do not carry every policy identity: {sorted(wanted - matched)}")
    # Mandatory complete chain: no manifest entry, or a record no entry names, leaves the custody unknown.
    if not links:
        raise AssemblyReadinessExecutionError(
            "unsupported", "hand_fit_policy selection records hold no manifest entry naming a pinned part record; "
                           "the complete source chain (decision, manifest entry, part record) is mandatory")
    unlinked = sorted(set(hashes) - {link["record"] for link in links} - {link["part_record"] for link in links})
    if unlinked:
        raise AssemblyReadinessExecutionError(
            "unsupported", f"hand_fit_policy selection records {unlinked} are neither a manifest entry nor the "
                           "part record one names")
    identity = (selection["mpn"], selection["lcsc"])
    if footprints.get(identity) != selection["footprint"]:
        raise AssemblyReadinessExecutionError(
            "unsupported", f"hand_fit_policy selection decision pins footprint {selection['footprint']!r} but the "
                           f"chain's records pin {footprints.get(identity)!r}")
    return selection | {"association": "category_selection", "part_record_links": links,
                        "footprints": [{"mpn": mpn, "lcsc": lcsc, "footprint": footprints[(mpn, lcsc)]}
                                       for mpn, lcsc in sorted(footprints)]}


# The owner text, not the quote, decides whether a policy identity is unconditional.
# Any qualifier below is conditional unless its scope is provably confined to a
# sibling part conjunct; otherwise every identity in the unit is refused.
CONDITIONAL_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:if|unless|when|whenever|where|wherever|whereas|provided|providing|only|except|"
    r"excepting|should|assuming|suppose|supposing|lest|until|once|while|otherwise|depending|depends|"
    r"contingent|conditional|conditionally|in\s+case|in\s+the\s+event|as\s+long\s+as|so\s+long\s+as|"
    r"subject\s+to|unstocked|unavailable|out\s+of\s+stock|not\s+in\s+stock)(?![A-Za-z0-9_])", re.I)
HAND_FIT_TERM = r"(?:hand[- ]?(?:fit|solder)|(?:fit|fitted|solder|soldered)\s+by\s+hand(?![A-Za-z0-9_]))"
HAND_FIT_RE = re.compile(r"(?<![A-Za-z0-9_])" + HAND_FIT_TERM, re.I)
NEGATED_HAND_FIT_RE = re.compile(
    r"(?:(?<![A-Za-z0-9_])(?:not|never|no|nor|cannot)|n't)\s+(?:[A-Za-z]+\s+){0,2}" + HAND_FIT_TERM, re.I)
EXEMPTION_REF = r"[A-Z]+[0-9]+"
EXEMPTION_SENTENCE_RE = re.compile(
    r"(?P<refs>" + EXEMPTION_REF + r"(?:, " + EXEMPTION_REF + r")*"
    r"(?:(?:,)? and " + EXEMPTION_REF + r")?) (?P<verb>is|are) "
    r"exempt from operating-temperature verification\.", re.I | re.ASCII)
PREDICATE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:is|are|be|been|being|was|were|fitted|assembled|excluded|soldered)(?![A-Za-z0-9_])|"
    + HAND_FIT_RE.pattern, re.I)
PART_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_/-])[A-Z0-9][A-Z0-9-]+[A-Z0-9](?![A-Za-z0-9_/-])")
LIST_ITEM_RE = re.compile(r"^(\s*)(?:[-*+]|[0-9]+[.)])\s+")


def _unit_bounds(lines, index):
    """First and last line of the Markdown block holding lines[index], and whether it is a list item."""
    def plain(i):
        line = lines[i]
        return bool(line.strip()) and not LIST_ITEM_RE.match(line) and not line.lstrip().startswith(("#", "|"))

    if not lines[index].strip() or lines[index].lstrip().startswith(("#", "|")):
        return index, index, False
    start = index
    while LIST_ITEM_RE.match(lines[start]) is None and start > 0 and lines[start - 1].strip() and (
            plain(start - 1) or LIST_ITEM_RE.match(lines[start - 1])):
        start -= 1
    end = index
    while end + 1 < len(lines) and plain(end + 1):
        end += 1
    return start, end, LIST_ITEM_RE.match(lines[start]) is not None


def _policy_unit(lines, index):
    """Whole owner statement holding the cited line; refuses when what governs it is ambiguous."""
    if sum(1 for line in lines[:index] if line.lstrip().startswith(("```", "~~~"))) % 2:
        raise ValueError("the cited line is inside a code block")
    start, end, item = _unit_bounds(lines, index)
    if item:
        if LIST_ITEM_RE.match(lines[start]).group(1):
            raise ValueError("a nested list item may be governed by its parent item")
        k = start - 1
        while k >= 0:
            if not lines[k].strip():
                k -= 1
                continue
            s, e, prior_item = _unit_bounds(lines, k)
            if lines[e].rstrip().endswith(":"):
                raise ValueError("the cited list item may complete a lead-in ending in ':'")
            if not prior_item:
                break
            k = s - 1
    heading = next((line for line in reversed(lines[:start]) if line.lstrip().startswith("#")), "")
    if CONDITIONAL_RE.search(heading):
        raise ValueError(f"the governing heading {heading.strip()!r} is conditional")
    return " ".join(line.strip() for line in lines[start:end + 1])


def _confined_spans(unit):
    """Spans of last parenthesized part conjuncts ('(A, and B if unstocked)') that alone carry a qualifier."""
    stack, groups = [], []
    for pos, char in enumerate(unit):
        if char == "(":
            stack.append(pos)
        elif char == ")":
            if not stack:
                raise ValueError("unbalanced parentheses make clause boundaries ambiguous")
            groups.append((stack.pop(), pos))
    if stack:
        raise ValueError("unbalanced parentheses make clause boundaries ambiguous")
    spans = []
    for match in CONDITIONAL_RE.finditer(unit):
        pos = match.start()
        inner = [g for g in groups if g[0] < pos < g[1]]
        span = None
        if inner:
            opening, closing = max(inner)
            nested = [g for g in groups if opening < g[0] < closing]
            cuts = [i for i in range(opening + 1, closing) if unit[i] in ",;" and
                    not any(a < i < b for a, b in nested)]
            last = (cuts[-1] + 1 if cuts else opening + 1, closing)
            conjunct = unit[last[0]:last[1]]
            if (cuts and last[0] <= pos and re.match(r"\s*(?:and|or)\s", conjunct) and
                    PART_TOKEN_RE.search(conjunct) and not PREDICATE_RE.search(conjunct) and
                    ":" not in conjunct and ". " not in conjunct):
                span = last
        if span is None:
            raise ValueError(f"conditional qualifier {match.group(0)!r} has a scope that cannot be bounded "
                             "to another part")
        spans.append(span)
    return spans


def _canonical_exemption_refs(sentence):
    """Validate one exact ASCII owner sentence and return its reference identities."""
    match = EXEMPTION_SENTENCE_RE.fullmatch(sentence)
    if match is None:
        raise ValueError("every line must be a complete canonical operating-temperature exemption")
    refs = re.findall(EXEMPTION_REF, match["refs"], re.I | re.ASCII)
    identities = {ref.casefold() for ref in refs}
    if (len(identities) != len(refs)
            or match["verb"].lower() != ("is" if len(refs) == 1 else "are")):
        raise ValueError("the canonical exemption must name each reference once and use is/are consistently")
    return identities


def _require_unconditional(unit, mpn):
    """The identity must be named once in an affirmative statement, outside every conditional clause."""
    if not HAND_FIT_RE.search(unit) or NEGATED_HAND_FIT_RE.search(unit):
        raise ValueError("the cited statement does not affirmatively declare a hand-fit")
    pattern = r"(?<![A-Za-z0-9_/-])" + re.escape(mpn) + r"(?![A-Za-z0-9_/-])"
    found = [m.start() for m in re.finditer(pattern, unit)]
    if len(found) != 1:
        raise ValueError(f"{mpn!r} is named {len(found)} times in the cited statement; expected exactly once")
    if any(a <= found[0] < b for a, b in _confined_spans(unit)):
        raise ValueError(f"{mpn!r} is named only conditionally; a conditional hand-fit must use the "
                         "stock-unavailable deviation class")


def _owner_hand_fit(citation, cfg, catalog, identity, matches):
    """Admit a declared owner hand-fit regardless of stock, only for an identity the cited decision names."""
    if citation is None:
        raise ValueError('owner_hand_fit deviation requires the scope hand_fit_policy citation; '
                         'the deviations file cannot assert it')
    if identity not in citation['identities']:
        raise ValueError(f'identity {identity} is not named by the owner hand-fit policy')
    if 'selection' in citation and sorted({c['reference'] for c in matches}) != [citation['selection']['reference']]:
        raise ValueError(f"design references {sorted({c['reference'] for c in matches})} differ from the hand-fit "
                         f"selection decision's {citation['selection']['reference']!r}")
    for pinned in citation.get('selection', {}).get('footprints', []):
        if (pinned['mpn'], pinned['lcsc']) == (identity['mpn'], identity['lcsc']):
            placed = sorted({_lookup_single_property(c['properties'], 'Footprint') or '' for c in matches})
            if placed != [pinned['footprint']]:
                raise ValueError(f"design footprint {placed} differs from the hand-fit selection chain's pinned "
                                 f"footprint {pinned['footprint']!r}")
    if identity['lcsc'] is None:
        if any(LCSC_RE.fullmatch(_lookup_single_property(c['properties'], 'LCSC') or '') for c in matches):
            raise ValueError('owner hand-fit identity without LCSC cannot cover a native LCSC id')
        if any(p['mpn'] == identity['mpn'] for p in catalog['parts'].values()):
            raise ValueError('catalog lists this MPN; the owner hand-fit identity must name its LCSC')
        return None
    row = catalog['parts'].get(identity['lcsc'])
    if row is None:
        raise ValueError('owner hand-fit identity has no captured catalog observation')
    if row['mpn'] != identity['mpn']:
        raise ValueError(f"catalog row {identity['lcsc']} MPN {row['mpn']!r} differs from the policy identity")
    if row['stock'] is None:
        raise ValueError('owner hand-fit identity has unknown stock telemetry')
    stale = _stale_reason(cfg, catalog, identity['lcsc'], row['last_seen'])
    if stale is not None:
        raise ValueError(f'owner hand-fit stock observation is stale or unverified: {stale}')
    return {'lcsc': row['lcsc'], 'mpn': row['mpn'], 'stock': row['stock'],
            'last_seen': row['last_seen'], 'tier': row['tier']}


def _assembly_deviations(inputs, cfg, extracted, catalog, captured_files, citation=None):
    from .hardware_interface import exact_keys
    from .part_decisions import evidence_references, validate_deviation

    declared, findings = [], []
    if cfg["deviations"] is None:
        return declared, findings
    path, rel = _resolve_captured_file(inputs, cfg["deviations"], "deviations")
    doc = _load_strict_json(path)
    try:
        exact_keys(doc, 'schema deviations')
        if doc['schema'] != 'agent-ee.part-deviations/1' or not isinstance(doc['deviations'], list):
            raise ValueError('unsupported part deviations')
    except (ValueError, TypeError, KeyError) as exc:
        raise AssemblyReadinessExecutionError('failed', str(exc)) from exc
    files = captured_files if captured_files is not None else {
        p.relative_to(inputs).as_posix() for p in Path(inputs).rglob('*') if p.is_file()}
    seen = set()
    for index, deviation in enumerate(doc['deviations']):
        try:
            if not validate_deviation(deviation):
                continue  # Substitutions remain the part_decisions check's obligation.
            evidence = evidence_references(deviation['evidence_refs'], inputs, files)
            identity = deviation['original']
            matches = [c for c in extracted['populated'] if
                       c['properties'].get('Partnumber') == identity['mpn'] and
                       (identity['lcsc'] is None or c['properties'].get('LCSC') == identity['lcsc'])]
            if not matches:
                raise ValueError('assembly deviation has no populated BOM component with its exact identity')
            if 'policy' in deviation:
                observation = _owner_hand_fit(citation, cfg, catalog, identity, matches)
            else:
                matched_ids = {_lookup_single_property(c['properties'], 'LCSC') for c in matches}
                stocked = [p['lcsc'] for p in catalog['parts'].values() if
                           (p['mpn'] == identity['mpn'] or p['lcsc'] in matched_ids) and
                           p['stock'] is not None and p['stock'] > 0]
                if stocked:
                    raise ValueError(f'deviation for stocked part refused: {stocked}')
                if any(p['stock'] is None for p in catalog['parts'].values() if
                       p['mpn'] == identity['mpn'] or p['lcsc'] in matched_ids):
                    raise ValueError('unknown stock cannot establish an unavailable part')
            for comp in matches:
                if comp['reference'] in seen:
                    raise ValueError('duplicate assembly deviation for component')
                if comp['properties'].get('JLCPCB_Assembly') != 'not_assembled':
                    raise ValueError('hand-fitted part requires JLCPCB_Assembly=not_assembled')
                lcsc = _lookup_single_property(comp['properties'], 'LCSC')
                if lcsc and not LCSC_RE.fullmatch(lcsc) and lcsc.upper() not in NO_LCSC_VALUES:
                    raise ValueError('assembly deviation has malformed LCSC identity')
                if LCSC_RE.fullmatch(lcsc) and catalog.get('capture') is not None:
                    _capture_gate(catalog['capture'], cfg, [lcsc] if lcsc not in catalog['parts'] else [])
            for comp in matches:
                seen.add(comp['reference'])
                entry = {'reference': comp['reference'], 'sheet': comp['sheet'],
                    'sheet_path': comp['sheet_path'], 'uuid': comp['uuid'],
                    'mpn': identity['mpn'], 'lcsc': _lookup_single_property(comp['properties'], 'LCSC') or None,
                    'assembly': deviation['assembly'], 'purchase_source': deviation['purchase_source'],
                    'stock_deviation': deviation['stock_deviation'], 'reason': deviation['reason'],
                    'requirement_id': deviation['requirement_id'], 'evidence': evidence,
                    'declaration_index': index, 'deviations_path': rel, 'deviations_sha256': digest(path),
                    'assembly_rule': 'JLCPCB_Assembly=not_assembled', 'stock_state': 'declared_deviation'}
                if 'policy' in deviation:
                    entry.update(stock_state='owner_hand_fit', policy=deviation['policy'],
                                 policy_citation={k: citation[k] for k in ('source', 'sha256', 'line', 'quote')},
                                 catalog_observation=observation)
                    if 'selection' in citation:
                        entry['policy_selection'] = citation['selection']
                declared.append(entry)
        except AssemblyReadinessExecutionError:
            raise
        except (ValueError, TypeError, KeyError, OSError) as exc:
            findings.append({'severity': 'defect', 'code': 'invalid_assembly_deviation',
                             'declaration_index': index, 'declaration': deviation, 'message': str(exc)})
    return declared, findings


def evaluate_assembly_readiness(inputs_root: Path, netlist_path: Path, params: dict, *, expected_sheets=None,
                                hierarchy_files=None, captured_files=None) -> dict:
    """Join native XML netlist components with the BOM property policy and captured catalog snapshot.

    ``expected_sheets`` is the hierarchy instance count; a netlist covering a
    different number of sheets is incomplete evidence and fails.
    ``hierarchy_files`` (root first) binds every placed symbol occurrence to
    a netlist component and the reverse; any gap fails.
    """
    try:
        cfg = validate_params(params)
    except ValueError as exc:
        raise AssemblyReadinessExecutionError("unsupported", str(exc)) from exc

    catalog = load_catalog_snapshot(
        inputs_root,
        cfg["catalog"],
        expected_sha256=cfg["catalog_sha256"],
    )
    extracted = read_netlist_components(netlist_path)
    if expected_sheets is not None and extracted["sheets"] != expected_sheets:
        raise AssemblyReadinessExecutionError(
            "failed",
            f"native netlist covers {extracted['sheets']} sheet instance(s), hierarchy has {expected_sheets}",
        )
    coverage = verify_netlist_coverage(inputs_root, hierarchy_files, extracted) if hierarchy_files else None

    populated = extracted["populated"]
    excluded_dnp = extracted["excluded_dnp"]
    excluded_from_bom = extracted["excluded_from_bom"]
    ignored_non_assembly = extracted["ignored_non_assembly"]

    citation = (_verify_hand_fit_policy(Path(inputs_root), cfg["hand_fit_policy"])
                if cfg["hand_fit_policy"] is not None else None)
    declared_deviation, findings = _assembly_deviations(Path(inputs_root), cfg, extracted, catalog, captured_files,
                                                        citation)
    declared_refs = {d['reference'] for d in declared_deviation}
    rating_records, rating_records_error = None, None
    if cfg["rating_provenance"] is not None:
        from .passive_ratings import load_provenance
        try:
            rating_records = load_provenance(Path(inputs_root), cfg["rating_provenance"])
        except (ValueError, TypeError, KeyError, OSError) as exc:
            # Unavailable provenance is unknown coverage, never a pass.
            rating_records_error = str(exc)
            findings.append({"severity": "unknown", "code": "rating_provenance_unavailable",
                             "provenance": cfg["rating_provenance"],
                             "message": f"receipted rating provenance is unavailable: {exc}"})
    temperature_report = None
    if cfg["operating_temperature"] is not None:
        temperature_report, temperature_findings = _operating_temperature(
            Path(inputs_root), cfg["operating_temperature"], populated, captured_files)
        findings.extend(temperature_findings)
    missing_properties_by_ref = {}
    missing_rating_refs = []
    missing_lcsc_refs = []
    invalid_lcsc_by_ref = {}
    unknown_lcsc_by_ref = {}

    # 1. Report excluded_from_bom components distinctly from DNP.
    for comp in excluded_from_bom:
        ref = comp["reference"]
        if comp['properties'].get('JLCPCB_Assembly') or _is_no_lcsc(
                _lookup_single_property(comp['properties'], 'LCSC'), comp['properties']):
            findings.append({'severity': 'defect', 'code': 'invalid_assembly_exclusion',
                             'reference': ref, 'message': 'hand-fit parts must stay in the native BOM '
                             'with a recorded deviation and not_assembled marker'})
        findings.append({
            "severity": "info",
            "code": "exclude_from_bom",
            "reference": ref,
            "sheet": comp["sheet"],
            "message": (
                f"{ref} is natively excluded from the BOM (in_bom no) without dnp; "
                "omitted from the BOM but not marked DNP on the assembly drawing"
            ),
        })

    # 2. Evaluate required BOM properties & passive ratings on populated components.
    matched_by_lcsc = {}
    component_reports = []

    for comp in populated:
        ref = comp["reference"]
        props = comp["properties"]
        comp_missing_props = []
        declared = ref in declared_refs
        if props.get('JLCPCB_Assembly') and not declared:
            findings.append({'severity': 'defect', 'code': 'undeclared_assembly_marker',
                             'reference': ref, 'message': 'assembly exclusion requires a valid recorded deviation'})

        markers = _non_native_markers(props)
        if markers:
            findings.append({
                "severity": "defect",
                "code": "non_native_assembly_marker",
                "reference": ref,
                "markers": markers,
                "sheet": comp["sheet"],
                "message": (
                    f"{ref} carries {', '.join(markers)} but no native DNP/exclude attribute; "
                    "KiCad exports and the JLC BOM still place it"
                ),
            })

        for rule in cfg["required_properties"]:
            choices = rule["any_of"]
            label = rule["label"]
            if len(choices) == 1 and choices[0].lower() == "lcsc":
                # LCSC is checked dedicatedly in step 3; still record in comp_missing_props if requested.
                val = _lookup_single_property(props, "LCSC")
                if not val and not declared:
                    comp_missing_props.append(label)
                continue
            satisfied = any(_lookup_single_property(props, candidate) for candidate in choices)
            if not satisfied:
                comp_missing_props.append(label)
                findings.append({
                    "severity": "defect",
                    "code": "missing_property",
                    "reference": ref,
                    "property": label,
                    "candidates": choices,
                    "sheet": comp["sheet"],
                    "message": f"{ref} is missing required BOM property {label!r}",
                })

        non_lcsc_missing = [p for p in comp_missing_props if p.lower() != "lcsc"]
        if non_lcsc_missing:
            missing_properties_by_ref[ref] = non_lcsc_missing

        passive_type = _passive_kind(comp)
        has_rating = True
        rating_provenance = rating_record = None
        if passive_type in ("R", "C"):
            from .passive_ratings import PROPERTY as RATING_PROVENANCE, resolve_record, validate_binding
            problems = validate_binding(props, passive_type)
            if problems:
                rating_record = "unresolved"
            elif cfg["rating_provenance"] is None:
                rating_record = "undeclared" if RATING_PROVENANCE in props else None
            elif rating_records is None:
                rating_record = "unavailable" if RATING_PROVENANCE in props else None
            else:
                problems = resolve_record(props, ref, rating_records, comp["sheet"])
                rating_record = ("unresolved" if problems else
                                 "receipt_bound" if RATING_PROVENANCE in props else None)
            # Only a record resolved against the receipted report is source-backed.
            rating_provenance = ('invalid' if problems else
                                 'source_backed' if rating_record == "receipt_bound" else
                                 'unverified' if RATING_PROVENANCE in props else 'unsourced')
            if problems:
                findings.append({
                    "severity": "defect",
                    "code": "invalid_rating_provenance",
                    "reference": ref,
                    "component_kind": passive_type,
                    "sheet": comp["sheet"],
                    "problems": problems,
                    "message": f"{ref} rating fields disagree with their source provenance: " + "; ".join(problems),
                })
        if cfg["require_passive_ratings"] and passive_type in ("R", "C"):
            has_rating = _has_passive_rating(props, passive_type)
            if not has_rating:
                missing_rating_refs.append(ref)
                findings.append({
                    "severity": "defect",
                    "code": "missing_rating_property",
                    "reference": ref,
                    "component_kind": passive_type,
                    "sheet": comp["sheet"],
                    "message": (
                        f"populated passive {ref} ({passive_type}) is missing a required "
                        f"{'power/voltage' if passive_type == 'R' else 'voltage'} rating property"
                    ),
                })

        lcsc_val = _lookup_single_property(props, "LCSC")
        lcsc_status = "ok"
        if declared:
            lcsc_status = 'declared_deviation'
        elif not lcsc_val:
            lcsc_status = "missing_lcsc"
            missing_lcsc_refs.append(ref)
            findings.append({
                "severity": "defect",
                "code": "missing_lcsc",
                "reference": ref,
                "property": "LCSC",
                "sheet": comp["sheet"],
                "message": f"populated component {ref} is missing required LCSC part number",
            })
        elif not LCSC_RE.fullmatch(lcsc_val):
            lcsc_status = "invalid_lcsc"
            invalid_lcsc_by_ref[ref] = lcsc_val
            findings.append({
                "severity": "defect",
                "code": "invalid_lcsc",
                "reference": ref,
                "lcsc": lcsc_val,
                "sheet": comp["sheet"],
                "message": f"populated component {ref} has malformed LCSC ID {lcsc_val!r}",
            })
        elif lcsc_val not in catalog["parts"]:
            lcsc_status = "unknown_lcsc_in_catalog"
            unknown_lcsc_by_ref[ref] = lcsc_val
            findings.append({
                "severity": "defect",
                "code": "unknown_lcsc_in_catalog",
                "reference": ref,
                "lcsc": lcsc_val,
                "sheet": comp["sheet"],
                "message": (
                    f"populated component {ref} references LCSC ID {lcsc_val} "
                    f"which is absent from catalog snapshot ({catalog['snapshot_date']})"
                ),
            })
        else:
            matched_by_lcsc.setdefault(lcsc_val, []).append(ref)

        component_reports.append({
            "reference": ref,
            "lib_id": comp["lib_id"],
            "sheet": comp["sheet"],
            "value": props.get("Value", ""),
            "footprint": _lookup_single_property(props, "Footprint"),
            "lcsc": lcsc_val or None,
            "lcsc_status": lcsc_status,
            "missing_properties": comp_missing_props,
            "passive_kind": passive_type,
            "has_rating_property": has_rating if passive_type else None,
            "rating_provenance": rating_provenance,
            "rating_record": rating_record,
        })

    capture = catalog.get("capture")
    if capture is not None:
        try:
            _capture_gate(capture, cfg, unknown_lcsc_by_ref.values())
        except AssemblyReadinessExecutionError as exc:
            exc.response_verdicts = capture["response_verdicts"]
            raise

    # 3. Evaluate matched LCSC parts: 3-state stock, freshness, quantity * boards, and Basic/Preferred/Extended tiers.
    as_of_raw = cfg["as_of"] or catalog["as_of"] or catalog["snapshot_date"]
    _parse_iso_date_or_datetime(as_of_raw, "as_of")
    effective_stale_before_str = cfg["stale_before"] or catalog["stale_before"]
    if effective_stale_before_str is not None:
        _parse_iso_date_or_datetime(effective_stale_before_str, "stale_before")

    boards = cfg["boards"]
    stock_floor = cfg["stock_floor"]
    max_stock_age_days = cfg["max_stock_age_days"]
    max_age_hours = cfg["max_age_hours"]

    tier_counts = {
        "basic_unique": 0,
        "preferred_unique": 0,
        "extended_unique": 0,
        "basic_placements": 0,
        "preferred_placements": 0,
        "extended_placements": 0,
        "extended_lcsc": [],
    }
    part_summaries = {}

    for lcsc in sorted(matched_by_lcsc):
        refs = sorted(matched_by_lcsc[lcsc])
        qty_per_board = len(refs)
        required_stock = qty_per_board * boards
        threshold = max(required_stock, stock_floor)
        part = catalog["parts"][lcsc]
        tier = part["tier"]

        if tier == "Basic":
            tier_counts["basic_unique"] += 1
            tier_counts["basic_placements"] += qty_per_board
        elif tier == "Preferred":
            tier_counts["preferred_unique"] += 1
            tier_counts["preferred_placements"] += qty_per_board
        else:
            tier_counts["extended_unique"] += 1
            tier_counts["extended_placements"] += qty_per_board
            tier_counts["extended_lcsc"].append(lcsc)

        stock = part["stock"]
        last_seen = part["last_seen"]
        stock_state = "ok"

        if stock == 0:
            stock_state = "defect"
            findings.append({
                "severity": "defect",
                "code": "out_of_stock",
                "lcsc": lcsc,
                "references": refs,
                "available": 0,
                "required": required_stock,
                "snapshot_date": catalog["snapshot_date"],
                "last_seen": last_seen,
                "message": (
                    f"LCSC {lcsc} ({', '.join(refs)}) has 0 stock in snapshot "
                    f"{catalog['snapshot_date']} (required {required_stock} for {boards} board(s))"
                ),
            })
        elif stock is None:
            stock_state = "unknown"
            findings.append({
                "severity": "unknown",
                "code": "missing_stock_telemetry",
                "lcsc": lcsc,
                "references": refs,
                "required": required_stock,
                "snapshot_date": catalog["snapshot_date"],
                "message": (
                    f"LCSC {lcsc} ({', '.join(refs)}) has missing/null stock telemetry "
                    f"in snapshot {catalog['snapshot_date']}"
                ),
            })
        else:
            stale_reason = _stale_reason(cfg, catalog, lcsc, last_seen)
            if stale_reason is not None:
                stock_state = "unknown"
                findings.append({
                    "severity": "unknown",
                    "code": "stale_stock_telemetry",
                    "lcsc": lcsc,
                    "references": refs,
                    "available": stock,
                    "required": required_stock,
                    "last_seen": last_seen,
                    "as_of": as_of_raw,
                    "snapshot_date": catalog["snapshot_date"],
                    "message": (
                        f"LCSC {lcsc} ({', '.join(refs)}) stock telemetry is stale or unverified: {stale_reason}"
                    ),
                })
            elif stock < threshold:
                stock_state = "defect"
                findings.append({
                    "severity": "defect",
                    "code": "insufficient_stock",
                    "lcsc": lcsc,
                    "references": refs,
                    "available": stock,
                    "required": required_stock,
                    "threshold": threshold,
                    "boards": boards,
                    "quantity_per_board": qty_per_board,
                    "snapshot_date": catalog["snapshot_date"],
                    "last_seen": last_seen,
                    "message": (
                        f"LCSC {lcsc} ({', '.join(refs)}) has insufficient stock: "
                        f"available {stock} < required {threshold} "
                        f"({qty_per_board}/board * {boards} board(s)) as of {catalog['snapshot_date']}"
                    ),
                })

        part_summaries[lcsc] = {
            "lcsc": lcsc,
            "references": refs,
            "quantity_per_board": qty_per_board,
            "boards": boards,
            "required_stock": required_stock,
            "available_stock": stock,
            "stock_state": stock_state,
            "tier": tier,
            "is_basic": part["is_basic"],
            "is_preferred": part["is_preferred"],
            "mfr": part["mfr"],
            "mpn": part["mpn"],
            "package": part["package"],
            "last_seen": last_seen,
            "snapshot_date": catalog["snapshot_date"],
        }

    # 4. Check Extended part count policy if configured.
    max_ext = cfg["max_extended_parts"]
    if max_ext is not None and tier_counts["extended_unique"] > max_ext:
        findings.append({
            "severity": "defect",
            "code": "extended_parts_limit_exceeded",
            "extended_unique": tier_counts["extended_unique"],
            "max_extended_parts": max_ext,
            "extended_lcsc": list(tier_counts["extended_lcsc"]),
            "message": (
                f"design uses {tier_counts['extended_unique']} unique Extended LCSC part(s) "
                f"({', '.join(tier_counts['extended_lcsc'])}), exceeding max_extended_parts={max_ext}"
            ),
        })

    undeclared = [c["reference"] for c in component_reports if c["rating_record"] == "undeclared"]
    if undeclared:
        # A record_sha256 nothing resolves is an unverified provenance claim: fail closed.
        findings.append({"severity": "unknown", "code": "undeclared_rating_provenance", "references": undeclared,
                         "message": "AGENT_EE_Ratings record_sha256 cannot be resolved; no receipted "
                                    "rating_provenance is declared"})

    defect_findings = [f for f in findings if f["severity"] == "defect"]
    unknown_findings = [f for f in findings if f["severity"] == "unknown"]

    if defect_findings:
        outcome = "defect"
        summary = (
            f"assembly_readiness reported {len(defect_findings)} defect(s) and "
            f"{len(unknown_findings)} unknown(s) across {len(populated)} populated component(s)"
        )
    elif rating_records_error is not None:
        outcome = "unknown"
        summary = "assembly_readiness cannot certify pass: receipted rating provenance is unavailable"
    elif unknown_findings:
        outcome = "unknown"
        summary = (
            f"assembly_readiness cannot certify pass: {len(unknown_findings)} "
            f"unknown(s) across {len(populated)} populated component(s)"
        )
    elif not populated:
        outcome = "info"
        summary = "0 populated assembly components; nothing to assemble"
    else:
        outcome = "pass"
        summary = (
            f"{len(populated) - len(declared_refs)} JLC assembly component(s) ({len(matched_by_lcsc)} unique LCSC part(s)) "
            f"have required BOM properties and fresh stock for {boards} board(s) "
            f"(Basic: {tier_counts['basic_unique']}, Preferred: {tier_counts['preferred_unique']}, "
            f"Extended: {tier_counts['extended_unique']}); {len(declared_refs)} declared hand-fit deviation(s)"
        )

    return {
        "schema": "agent-ee.assembly-readiness/1",
        "execution": "ok",
        "outcome": outcome,
        "summary": summary,
        "catalog": {
            "schema": catalog["schema"],
            "path": catalog["catalog_rel"],
            "sha256": catalog["catalog_sha256"],
            "database_path": catalog["database_rel"],
            "database_sha256": catalog["database_sha256"],
            "snapshot_date": catalog["snapshot_date"],
            "as_of": as_of_raw,
            "stale_before": effective_stale_before_str,
            "capture": None if capture is None else {
                "as_of": capture["as_of"],
                "completed_at": capture["completed_at"],
                "requested": capture["requested"],
                "not_found": capture["not_found"],
                "failed": sorted(capture["failed"]),
                "response_verdicts": capture["response_verdicts"],
            },
        },
        "policy": {
            "boards": boards,
            "required_properties": [r["label"] for r in cfg["required_properties"]],
            "require_passive_ratings": cfg["require_passive_ratings"],
            "max_extended_parts": max_ext,
            "max_stock_age_days": max_stock_age_days,
            "max_age_hours": max_age_hours,
            "stale_before": effective_stale_before_str,
            "stock_floor": stock_floor,
        },
        "counts": {
            "populated": len(populated),
            "excluded_dnp": len(excluded_dnp),
            "excluded_from_bom": len(excluded_from_bom),
            "ignored_non_assembly": len(ignored_non_assembly),
            "unique_lcsc": len(matched_by_lcsc),
            "declared_deviation": len(declared_refs),
            "defects": len(defect_findings),
            "unknowns": len(unknown_findings),
        },
        "tier_counts": tier_counts,
        "populated_refs": [c["reference"] for c in populated],
        "excluded_dnp": [c["reference"] for c in excluded_dnp],
        "excluded_from_bom": [c["reference"] for c in excluded_from_bom],
        "declared_deviation": declared_deviation,
        "deviations_sha256": digest(Path(inputs_root) / cfg['deviations']) if cfg['deviations'] else None,
        "hand_fit_policy": citation,
        "rating_provenance": None if cfg["rating_provenance"] is None else {
            **cfg["rating_provenance"],
            "report_sha256": (digest(Path(inputs_root) / cfg["rating_provenance"]["report"])
                              if rating_records is not None else None),
            "records": None if rating_records is None else len(rating_records),
            "error": rating_records_error,
        },
        "operating_temperature": temperature_report,
        "ignored_non_assembly": [
            {"reference": c["reference"], "lib_id": c["lib_id"], "reason": c["ignore_reason"]}
            for c in ignored_non_assembly
        ],
        "missing_properties": missing_properties_by_ref,
        "missing_rating_refs": missing_rating_refs,
        "missing_lcsc_refs": missing_lcsc_refs,
        "invalid_lcsc": invalid_lcsc_by_ref,
        "unknown_lcsc_in_catalog": unknown_lcsc_by_ref,
        "parts": part_summaries,
        "components": component_reports,
        "findings": findings,
        "netlist": {
            "sha256": digest(netlist_path),
            "components": extracted["components"],
            "sheets": extracted["sheets"],
            "hierarchy_coverage": coverage,
        },
    }


def campaign_stock_context(root, state, config, scope):
    """Generation receipt selected only from the coordinator's anchored records."""
    from .stage_done import _receipts
    from .stage_receipts import validate_pre_receipt
    if not any(c['kind'] == 'assembly_readiness' for c in scope.get('checks', [])):
        return None
    context = {"receipt": None}
    schematic = scope.get('schematic')

    def select(path, sha, receipt):
        if (receipt['spec']['stage'] in ('scaffold', 'schematic') and
                schematic in receipt['outputs']):
            context['receipt'] = {'path': str(path), 'sha256': sha, 'binding': receipt['binding']}

    for link in config.get('pre_receipts', []):
        receipt = validate_pre_receipt(root, link)
        select(Path(root) / link['path'], link['sha256'], receipt)
    for number in range(1, len(state['history']) + 1):
        work = Path(root) / 'implementations' / str(number)
        for k, sha, receipt in _receipts(root, state, str(number)):
            link = read_json(work / 'stage-receipts.json')['receipts'][k - 1]
            select(work / link['path'], sha, receipt)
    return context


def _generation_time(link, manifest):
    from .stage_receipts import ADOPTABLE, validate_receipt
    if link is None:
        raise ValueError('campaign JLC stock evidence requires a generation stage receipt')
    path = Path(link['path'])
    if path.is_symlink() or digest(path) != link['sha256']:
        raise ValueError('generation stage receipt changed')
    receipt = validate_receipt(path.parent, binding=link['binding'])
    schematic = manifest['scope']['schematic']
    if (receipt['status'] not in ADOPTABLE or receipt['spec']['stage'] not in ('scaffold', 'schematic') or
            receipt['outputs'].get(schematic) != manifest['files'][schematic]):
        raise ValueError('generation stage receipt is not adoptable or does not bind the captured schematic')
    return datetime.fromtimestamp(receipt['started_unix'] + receipt['duration_s'], timezone.utc).isoformat()


def stock_evaluation_params(ctx):
    """Bind live-stock age to coordinator time, never to a caller's snapshot date."""
    from .checks import CheckError
    params = dict(ctx.params)
    path, _ = _resolve_captured_file(ctx.inputs, params['catalog'], 'catalog')
    doc = _load_strict_json(path)
    if not is_capture(doc):
        return params
    context = getattr(ctx, 'stock_context', None)
    witness = {'context': context}
    try:
        if context is None:
            result = ctx.run_dir / 'result.json'
            stamp = (read_json(result)['started_utc'] if result.is_file()
                     else datetime.now(timezone.utc).isoformat())
            witness.update(source='run_started_utc', as_of=stamp)
        else:
            stamp = _generation_time(context['receipt'], ctx.manifest)
            witness.update(source='generation_receipt_completed', as_of=stamp)
        params['as_of'] = stamp
        # The campaign's day-of-generation policy cannot be relaxed by scope params.
        params['max_age_hours'] = min(24 if params.get('max_age_hours') is None else params['max_age_hours'], 24)
        witness['max_age_hours'] = params['max_age_hours']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        witness['error'] = str(exc)
        raise CheckError('failed', f'JLC stock evaluation time refused: {exc}') from exc
    finally:
        write_json(ctx.work / 'stock-evaluation.json', witness)
        ctx.evidence('stock-evaluation.json')
        ctx.result['details']['stock_evaluation'] = witness
    return params


STAGE_GATE_REASONS = ('stage_not_reached', 'stage_blocked')


def _verify_stage_gated(run, check, manifest, baseline):
    """A coordinator stage gate stopped this check before its handler, so no stock was evaluated.

    Established from sealed run evidence, never from the result's own fields: the
    stage verdict is reconstructed from the immutable stage declaration and the
    native presence probe (or earlier check results), the details and gate findings
    must be exactly what the gate writes, and no stock-evaluation artifact may exist.
    """
    from .baseline import load_baseline
    from .stages import validate_gate_findings, verify_gate
    run = Path(run)
    reason = check['details']['reason']
    if baseline is None:
        baseline = load_baseline(run)
    if baseline is None:
        raise ValueError('stage-gated stock check requires an immutable baseline')
    verify_gate(run, check, manifest, baseline)
    validate_gate_findings(check)
    stage = next(c for c in manifest['scope']['checks'] if c['id'] == check['id'])['stage']
    work = f"work/{check['id']}"
    expected = {'reason': reason, 'stage': stage}
    if reason == 'stage_not_reached':
        subject = manifest['scope']['stages']['subjects'][stage]
        missing = read_json(run / work / 'stage-subject.json')['missing']
        entities = [subject['path']] if subject['kind'] == 'tracks' else missing
        expected['gate_findings'] = [{'check_id': check['id'], 'entity': ref, 'kind': 'stage_not_reached'}
                                     for ref in entities]
        summary, witnesses = f'{stage}: expected native content absent', {work + '/stage-subject.json'}
    else:
        # A presence probe may precede the blocked gate; its files are the only other evidence.
        summary, witnesses = f'{stage} gate blocked by earlier checks', {work + '/stage-subject.json',
                                                                          work + '/stage-gate.json'}
    if check['details'] != expected or check['summary'] != summary:
        raise ValueError('stage-gated stock check differs from its recomputed stage gate')
    evidence = [e['path'] if isinstance(e, dict) else e for e in check['evidence']]
    if any(rel not in witnesses and not rel.startswith(work + '/stage-subject/') for rel in evidence):
        raise ValueError('stage-gated stock check cites evidence beyond its stage gate')
    if any((run / work / name).exists() for name in ('stock-evaluation.json', 'assembly-readiness.json', 'design.xml')):
        raise ValueError('stage-gated stock check retains stock evaluation artifacts')


def verify_stock_context(run, result, expected, baseline=None):
    """Campaign replay checks the sealed stock witness against anchored receipt selection.

    ``baseline`` is the caller's already-verified immutable baseline; when absent it
    is recompiled from the run, and is only needed to reconstruct a stage gate.
    """
    manifest = read_json(Path(run) / 'candidate.json')
    specs = {s['id']: s for s in manifest['scope']['checks']}
    for check in result['checks']:
        if check['kind'] != 'assembly_readiness':
            continue
        witness = check.get('details', {}).get('stock_evaluation')
        gated = check.get('details', {}).get('reason') in STAGE_GATE_REASONS
        if gated and witness is not None:
            raise ValueError('stage-gated stock check cannot carry a stock evaluation witness')
        if witness is None:
            if check['execution'] == 'ok':
                catalog = specs[check['id']]['params']['catalog']
                if is_capture(_load_strict_json(Path(run) / 'inputs' / catalog)):
                    if not gated:
                        raise ValueError('JLC stock check lacks its evaluation witness')
                    _verify_stage_gated(run, check, manifest, baseline)
            continue
        if witness['context'] != expected:
            raise ValueError('stock evaluation context differs from the anchored generation receipt')
        rel = f"work/{check['id']}/stock-evaluation.json"
        if rel not in check['evidence'] or read_json(Path(run) / rel) != witness:
            raise ValueError('stock check omits its sealed evaluation witness')
        if 'error' in witness and (check['execution'] == 'ok' or check['outcome'] != 'unknown'):
            raise ValueError('stock evaluation error must remain failed/unknown coverage')
        if 'error' not in witness:
            if expected is None or witness['as_of'] != _generation_time(expected['receipt'], manifest):
                raise ValueError('stock evaluation time differs from the generation receipt')
            limit = specs[check['id']]['params'].get('max_age_hours')
            limit = min(24 if limit is None else limit, 24)
            if witness['source'] != 'generation_receipt_completed' or witness['max_age_hours'] != limit:
                raise ValueError('stock evaluation policy differs from the campaign policy')


def check_assembly_readiness(ctx):
    """Export the native XML netlist in the sandbox, then join it with the captured catalog."""
    from .checks import CheckError, verified_hierarchy

    sch_rel = ctx.scope.get("schematic")
    if not isinstance(sch_rel, str) or not sch_rel.strip():
        raise CheckError("unsupported", "assembly_readiness requires scope.schematic")

    try:
        validate_params(ctx.params)
    except (ValueError, TypeError, KeyError) as exc:
        raise CheckError("unsupported", str(exc)) from exc

    sch = ctx.candidate_path(sch_rel)
    files, instances = verified_hierarchy(ctx)
    try:
        schematic_hashes = verify_schematic_files(ctx.inputs, files)
    except AssemblyReadinessExecutionError as exc:
        raise CheckError(exc.execution, str(exc)) from exc
    ctx.seed_kicad_config()
    ctx.execute(["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
                 "-o", "/work/design.xml", sch], timeout=NETLIST_TIMEOUT)
    ctx.evidence("design.xml")
    netlist = ctx.work / "design.xml"
    if not netlist.is_file() or netlist.stat().st_size == 0:
        raise CheckError("failed", "native XML netlist was not written")

    try:
        params = stock_evaluation_params(ctx)
        report = evaluate_assembly_readiness(ctx.inputs, netlist, params, expected_sheets=instances,
                                             hierarchy_files=files, captured_files=ctx.manifest['files'])
    except AssemblyReadinessExecutionError as exc:
        if hasattr(exc, "response_verdicts"):
            failure = {"schema": "agent-ee.assembly-readiness/1", "candidate_id": ctx.manifest["candidate_id"],
                       "execution": exc.execution, "outcome": "unknown", "summary": str(exc),
                       "response_verdicts": exc.response_verdicts}
            write_json(ctx.work / "assembly-readiness.json", failure)
            ctx.evidence("assembly-readiness.json")
            ctx.result["details"].update(failure)
        raise CheckError(exc.execution, str(exc)) from exc

    report["candidate_id"] = ctx.manifest["candidate_id"]
    report["schematic"] = {
        "root": sch_rel,
        "sheet_instances": instances,
        "files": schematic_hashes,
    }
    report["netlist"]["path"] = f"work/{ctx.cid}/design.xml"
    write_json(ctx.work / "assembly-readiness.json", report)
    ctx.evidence("assembly-readiness.json")
    ctx.result["details"].update(report)
    ctx.result["limitations"].append(
        "Offline catalog snapshot check only; live distributor pricing, reel minimums, "
        "attrition overage, and physical pad-to-package geometry are outside this check."
    )
    ctx.result["execution"] = report["execution"]
    ctx.result["outcome"] = report["outcome"]
    ctx.result["summary"] = report["summary"]

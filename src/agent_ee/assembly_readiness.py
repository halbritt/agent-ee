"""JLC assembly-readiness check: LCSC binding, 3-state stock freshness, Basic/Extended and BOM properties.

Components come from the native ``kicad-cli sch export netlist --format kicadxml``
of the captured schematic hierarchy, the same source the JLC BOM in
``fab_export`` is built from; the catalog is a captured offline snapshot.
"""

from datetime import date, datetime, timezone
import json
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
))

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


def _validate_hand_fit_policy(raw, deviations):
    """Scope-held citation of a captured owner decision naming the exact hand-fit identities."""
    from .hardware_interface import exact_keys, text
    from .part_decisions import names_identity, part
    exact_keys(raw, "source sha256 line quote identities")
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
    for identity in identities:
        part(identity)
        if identity["mpn"] is None:
            raise ValueError("hand_fit_policy identity requires an exact MPN")
        if not names_identity(identity["mpn"], raw["quote"]):
            raise ValueError(f"hand_fit_policy.quote does not name {identity['mpn']!r}")
    if len({(i["mpn"], i["lcsc"]) for i in identities}) != len(identities):
        raise ValueError("duplicate hand_fit_policy identity")
    return {key: raw[key] for key in ("source", "sha256", "line", "quote")} | {
        "identities": [dict(i) for i in identities]}


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
    if cfg["rating_provenance"] is not None:
        for key in ("receipt", "report"):
            if cfg["rating_provenance"][key] not in files:
                raise ValueError(f"rating_provenance.{key} must be a captured input")
            immutable_inputs.append(cfg["rating_provenance"][key])
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
    try:
        unit = _policy_unit(lines, policy["line"] - 1)
        for identity in policy["identities"]:
            _require_unconditional(unit, identity["mpn"])
    except ValueError as exc:
        raise AssemblyReadinessExecutionError(
            "failed", f"hand_fit_policy {rel!r} line {policy['line']}: {exc}") from exc
    return policy | {"class": "owner_hand_fit"}


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


def _require_unconditional(unit, mpn):
    """The identity must be named once, by a non-negated hand-fit statement, outside every conditional clause."""
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
                problems = resolve_record(props, ref, rating_records)
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


def verify_stock_context(run, result, expected):
    """Campaign replay checks the sealed stock witness against anchored receipt selection."""
    manifest = read_json(Path(run) / 'candidate.json')
    specs = {s['id']: s for s in manifest['scope']['checks']}
    for check in result['checks']:
        if check['kind'] != 'assembly_readiness':
            continue
        witness = check.get('details', {}).get('stock_evaluation')
        if witness is None:
            if check['execution'] == 'ok':
                catalog = specs[check['id']]['params']['catalog']
                if is_capture(_load_strict_json(Path(run) / 'inputs' / catalog)):
                    raise ValueError('JLC stock check lacks its evaluation witness')
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

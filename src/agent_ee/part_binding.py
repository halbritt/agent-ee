"""Part-binding properties embedded in native KiCad symbols and footprints, with validator."""

from decimal import Decimal, DecimalException
import json
import posixpath
import re
from pathlib import Path

from .candidate import safe_relative
from .storage import digest, write_json
from .symbol_library import LibraryError, children, parse_sexpr


LCSC_RE = re.compile(r"^C[1-9][0-9]*$")
MAX_SHEET_OCCURRENCES = 4096
PARAM_NAME_RE = re.compile(r"^PARAM_[A-Za-z0-9_]+$")
DECIMAL_LITERAL_RE = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")
MAX_DECIMAL_SIGNIFICAND_DIGITS = 28
MAX_DECIMAL_EXPONENT = 1000
PARAM_UNITS = frozenset((
    *(
        f"{prefix}{base}"
        for prefix in ("", "f", "p", "n", "u", "m", "k", "M", "G")
        for base in ("ohm", "Ohm", "F", "H", "V", "A", "W", "Hz", "s", "m", "g")
    ),
    "degC",
    "K",
    "C/W",
    "K/W",
    "%",
    "ppm",
    "ppm/K",
    "ppm/degC",
    "dB",
    "bps",
    "kbps",
    "Mbps",
    "Gbps",
    "rad",
    "deg",
    "lm",
    "cd",
    "mcd",
    "T",
    "mT",
    "uT",
))
TRUTHY_MARKERS = frozenset(("true", "yes", "1"))
NO_LCSC_VALUES = frozenset(("DNP", "NO_LCSC", "NO-LCSC"))
BINDING_FIELDS = frozenset(("LCSC", "Manufacturer", "Partnumber", "Datasheet"))
ALLOWED_CHECK_PARAMS = frozenset((
    "schematic",
    "board",
    "require_datasheet",
    "require_params",
    "calibration_inputs",
))


class PartBindingError(ValueError):
    """Base error for part-binding validation and contract failures."""


class PartBindingMalformedError(PartBindingError):
    """Raised when S-expressions or PARAM_* JSON literals are malformed."""


def _unique_json_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise PartBindingMalformedError(f"duplicate JSON key: {key!r}")
        obj[key] = value
    return obj


def _reject_json_constant(token):
    raise PartBindingMalformedError(f"non-finite JSON constant: {token!r}")


def _parse_json_number(token: str) -> Decimal:
    if not isinstance(token, str) or not DECIMAL_LITERAL_RE.match(token):
        raise PartBindingMalformedError(f"invalid JSON numeric literal: {token!r}")
    try:
        num = Decimal(token)
    except (DecimalException, ArithmeticError, ValueError) as exc:
        raise PartBindingMalformedError(f"invalid JSON numeric literal {token!r}: {exc}") from exc
    return num


def _decimal_text(value: Decimal, prop_name: str = "PARAM_?", field_name: str = "value") -> str:
    try:
        if not value.is_finite():
            raise PartBindingMalformedError(f"{prop_name}: {field_name} must be finite")
        if value == 0:
            return "0"
        digits = len(value.as_tuple().digits)
        adj = abs(value.adjusted())
        if digits > MAX_DECIMAL_SIGNIFICAND_DIGITS or adj > MAX_DECIMAL_EXPONENT:
            raise PartBindingMalformedError(
                f"{prop_name}: {field_name} decimal exponent or precision out of bounds (adjusted={value.adjusted()}, digits={digits})"
            )
        norm = value.normalize()
        return format(norm, "f")
    except PartBindingMalformedError:
        raise
    except (DecimalException, ArithmeticError, ValueError, MemoryError) as exc:
        raise PartBindingMalformedError(
            f"{prop_name}: {field_name} decimal overflow or invalid operation ({exc})"
        ) from exc


def _parse_decimal(raw, prop_name: str, field_name: str) -> Decimal:
    if isinstance(raw, bool) or raw is None:
        raise PartBindingMalformedError(
            f"{prop_name}: {field_name} must be a finite number or numeric string"
        )
    if isinstance(raw, Decimal):
        num = raw
    elif isinstance(raw, (int, float)):
        raw_str = str(raw)
        if not DECIMAL_LITERAL_RE.match(raw_str):
            raise PartBindingMalformedError(
                f"{prop_name}: invalid numeric literal in {field_name}: {raw!r}"
            )
        try:
            num = Decimal(raw_str)
        except (DecimalException, ArithmeticError, ValueError) as exc:
            raise PartBindingMalformedError(
                f"{prop_name}: invalid numeric literal in {field_name}: {raw!r}"
            ) from exc
    elif isinstance(raw, str):
        stripped = raw.strip()
        if not stripped or "_" in stripped or not DECIMAL_LITERAL_RE.match(stripped):
            raise PartBindingMalformedError(
                f"{prop_name}: invalid numeric string in {field_name}: {raw!r}"
            )
        try:
            num = Decimal(stripped)
        except (DecimalException, ArithmeticError, ValueError) as exc:
            raise PartBindingMalformedError(
                f"{prop_name}: invalid numeric string in {field_name}: {raw!r}"
            ) from exc
    else:
        raise PartBindingMalformedError(
            f"{prop_name}: {field_name} has unsupported type {type(raw).__name__}"
        )
    try:
        is_fin = num.is_finite()
    except (DecimalException, ArithmeticError) as exc:
        raise PartBindingMalformedError(
            f"{prop_name}: {field_name} decimal error: {exc}"
        ) from exc
    if not is_fin:
        raise PartBindingMalformedError(
            f"{prop_name}: {field_name} must be finite, got {raw!r}"
        )
    digits = len(num.as_tuple().digits)
    adj = abs(num.adjusted())
    if digits > MAX_DECIMAL_SIGNIFICAND_DIGITS or adj > MAX_DECIMAL_EXPONENT:
        raise PartBindingMalformedError(
            f"{prop_name}: {field_name} decimal exponent or precision out of bounds: {raw!r}"
        )
    return num


def parse_param_value(raw_text: str, prop_name: str = "PARAM_?") -> dict:
    """Parse and normalize a PARAM_<name> JSON interval or discrete set literal."""
    if not isinstance(prop_name, str) or not prop_name.startswith("PARAM_") or len(prop_name) <= 6:
        raise PartBindingMalformedError(f"invalid PARAM property name: {prop_name!r}")
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise PartBindingMalformedError(f"{prop_name}: parameter value must be non-empty JSON text")
    try:
        parsed = json.loads(
            raw_text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
            parse_float=_parse_json_number,
            parse_int=_parse_json_number,
        )
    except PartBindingMalformedError:
        raise
    except (json.JSONDecodeError, DecimalException, ArithmeticError, ValueError, TypeError, RecursionError) as exc:
        raise PartBindingMalformedError(f"{prop_name}: invalid JSON ({exc})") from exc

    if not isinstance(parsed, dict):
        raise PartBindingMalformedError(f"{prop_name}: JSON root must be an object")

    if prop_name == "PARAM_Datasheet":
        from .datasheet_capture import validate_url
        if (set(parsed) != {"schema", "url", "sha256"} or
                parsed["schema"] != "agent-ee.datasheet-binding/1" or
                not isinstance(parsed["sha256"], str) or
                not re.fullmatch("[0-9a-f]{64}", parsed["sha256"])):
            raise PartBindingMalformedError("PARAM_Datasheet: malformed captured identity")
        try:
            validate_url(parsed["url"])
        except (TypeError, ValueError) as exc:
            raise PartBindingMalformedError("PARAM_Datasheet: invalid captured URL") from exc
        return parsed

    raw_unit = parsed.get("unit")
    if not isinstance(raw_unit, str) or not raw_unit.strip():
        raise PartBindingMalformedError(f"{prop_name}: requires non-empty string 'unit'")
    unit = raw_unit.strip()
    if unit not in PARAM_UNITS:
        raise PartBindingMalformedError(
            f"{prop_name}: unsupported unit {unit!r}; must be in declared unit vocabulary"
        )

    has_values = "values" in parsed
    has_set = "set" in parsed
    has_bounds = "min" in parsed or "max" in parsed or "nominal" in parsed

    if (has_values or has_set) and has_bounds:
        raise PartBindingMalformedError(
            f"{prop_name}: cannot mix discrete set ('values'/'set') with interval bounds ('min'/'max'/'nominal')"
        )
    if has_values and has_set:
        raise PartBindingMalformedError(
            f"{prop_name}: cannot specify both 'values' and 'set'"
        )

    if has_values or has_set:
        set_key = "values" if has_values else "set"
        extra = set(parsed) - {"unit", set_key}
        if extra:
            raise PartBindingMalformedError(
                f"{prop_name}: unexpected keys in discrete set parameter: {sorted(extra)}"
            )
        raw_items = parsed[set_key]
        if not isinstance(raw_items, list) or not raw_items:
            raise PartBindingMalformedError(
                f"{prop_name}: '{set_key}' must be a non-empty list"
            )
        decimals = [
            _parse_decimal(item, prop_name, f"{set_key}[{idx}]")
            for idx, item in enumerate(raw_items)
        ]
        by_numeric = {}
        for idx, dec in enumerate(decimals):
            text_val = _decimal_text(dec, prop_name, f"{set_key}[{idx}]")
            by_numeric[Decimal(text_val)] = text_val
        sorted_keys = sorted(by_numeric)
        sorted_values = [by_numeric[k] for k in sorted_keys]
        return {
            "min": sorted_values[0],
            "max": sorted_values[-1],
            "values": sorted_values,
            "unit": unit,
        }

    if "min" not in parsed or "max" not in parsed:
        raise PartBindingMalformedError(
            f"{prop_name}: interval parameter requires both 'min' and 'max' (or 'values'/'set')"
        )
    extra = set(parsed) - {"min", "max", "nominal", "unit"}
    if extra:
        raise PartBindingMalformedError(
            f"{prop_name}: unexpected keys in interval parameter: {sorted(extra)}"
        )
    low = _parse_decimal(parsed["min"], prop_name, "min")
    high = _parse_decimal(parsed["max"], prop_name, "max")
    low_text = _decimal_text(low, prop_name, "min")
    high_text = _decimal_text(high, prop_name, "max")
    if low > high:
        raise PartBindingMalformedError(
            f"{prop_name}: min ({low_text}) exceeds max ({high_text})"
        )
    normalized = {
        "min": low_text,
        "max": high_text,
        "unit": unit,
    }
    if "nominal" in parsed:
        nom = _parse_decimal(parsed["nominal"], prop_name, "nominal")
        nom_text = _decimal_text(nom, prop_name, "nominal")
        if nom < low or nom > high:
            raise PartBindingMalformedError(
                f"{prop_name}: nominal ({nom_text}) outside [{low_text}, {high_text}]"
            )
        normalized["nominal"] = nom_text
    return normalized


def _normalize_datasheet(raw: str) -> str:
    val = (raw or "").strip()
    return "" if val == "~" else val


def _extract_yes_no(node, tag: str, default: bool) -> bool:
    found = children(node, tag)
    if not found:
        return default
    if len(found) > 1 or len(found[0]) < 2 or not isinstance(found[0][1], str):
        raise PartBindingMalformedError(f"malformed ({tag} ...) node")
    token = found[0][1].strip().lower()
    if token == "yes":
        return True
    if token == "no":
        return False
    raise PartBindingMalformedError(f"invalid ({tag} {found[0][1]!r}) token; expected yes or no")


def _extract_single_str(node, tag: str, *, required: bool = False, default: str = "") -> str:
    found = children(node, tag)
    if not found:
        if required:
            raise PartBindingMalformedError(f"missing required ({tag} ...) node")
        return default
    if len(found) > 1 or len(found[0]) < 2 or not isinstance(found[0][1], str):
        raise PartBindingMalformedError(f"malformed ({tag} ...) node")
    val = found[0][1]
    if required and not val.strip():
        raise PartBindingMalformedError(f"empty required ({tag} ...) node")
    return val


def _extract_properties(
    node, context_label: str, *, defer_param_errors: bool = False
) -> tuple[dict[str, str], dict[str, dict], list[dict]]:
    props = {}
    params = {}
    param_errors = []
    for prop in children(node, "property"):
        if len(prop) < 3 or not isinstance(prop[1], str) or not isinstance(prop[2], str):
            raise PartBindingMalformedError(f"{context_label}: malformed (property ...) node")
        name = prop[1]
        val = prop[2]
        if not name:
            raise PartBindingMalformedError(f"{context_label}: empty property name")
        if name in props:
            raise PartBindingMalformedError(f"{context_label}: duplicate property {name!r}")
        props[name] = val
        if name.startswith("PARAM_"):
            try:
                if not PARAM_NAME_RE.match(name):
                    raise PartBindingMalformedError(
                        f"{context_label}: invalid parameter property name {name!r}"
                    )
                params[name] = parse_param_value(val, name)
            except PartBindingMalformedError as exc:
                if not defer_param_errors:
                    raise
                param_errors.append({
                    "property": name,
                    "value": val,
                    "reason": str(exc),
                })
    return props, params, param_errors


def _identify_power_lib_symbols(root) -> set[str]:
    power_libs = set()
    extends_map = {}
    lib_symbols_nodes = children(root, "lib_symbols")
    for lib_syms in lib_symbols_nodes:
        for sym in children(lib_syms, "symbol"):
            if len(sym) < 2 or not isinstance(sym[1], str):
                raise PartBindingMalformedError("malformed symbol in (lib_symbols ...)")
            sym_name = sym[1]
            if children(sym, "power"):
                power_libs.add(sym_name)
            ext = children(sym, "extends")
            if ext and len(ext[0]) >= 2 and isinstance(ext[0][1], str):
                extends_map[sym_name] = ext[0][1]

    # Propagate power flag across extends chains
    for sym_name in list(extends_map):
        curr = sym_name
        visited = set()
        while curr in extends_map and curr not in visited:
            visited.add(curr)
            parent = extends_map[curr]
            if parent in power_libs or f"{curr.split(':')[0]}:{parent}" in power_libs:
                power_libs.add(sym_name)
                break
            curr = parent
    return power_libs


def _instance_entries(symbol_node) -> list[tuple[str, str, int | None]]:
    """Return (instance path, reference, unit) for every (instances ...) entry."""
    entries = []
    for inst in children(symbol_node, "instances"):
        for proj in children(inst, "project"):
            for path_node in children(proj, "path"):
                if len(path_node) < 2 or not isinstance(path_node[1], str):
                    raise PartBindingMalformedError("malformed instance (path ...)")
                ref_nodes = children(path_node, "reference")
                if not ref_nodes:
                    continue
                if len(ref_nodes[0]) < 2 or not isinstance(ref_nodes[0][1], str):
                    raise PartBindingMalformedError("malformed instance (reference ...)")
                unit_val = None
                unit_nodes = children(path_node, "unit")
                if unit_nodes and len(unit_nodes[0]) >= 2 and isinstance(unit_nodes[0][1], str):
                    try:
                        unit_val = int(unit_nodes[0][1])
                    except ValueError as exc:
                        raise PartBindingMalformedError("malformed instance (unit ...)") from exc
                ref_val = ref_nodes[0][1].strip()
                if ref_val:
                    entries.append((path_node[1], ref_val, unit_val))
    return entries


def _sheet_children(root, sheet_rel: str) -> list[tuple[str, str]]:
    """Return (sheet uuid, resolved child path) for each (sheet ...) of a schematic."""
    found = []
    for sheet in children(root, "sheet"):
        files = [
            prop[2] for prop in children(sheet, "property")
            if len(prop) >= 3 and prop[1] in ("Sheetfile", "Sheet file")
        ]
        if len(files) != 1 or not isinstance(files[0], str) or not files[0].strip():
            raise PartBindingMalformedError(f"sheet in {sheet_rel!r} needs exactly one Sheetfile")
        sheet_uuid = _extract_single_str(sheet, "uuid", required=True)
        child = posixpath.normpath(posixpath.join(posixpath.dirname(sheet_rel), files[0].strip()))
        if child.startswith("../") or child == ".." or posixpath.isabs(child):
            raise PartBindingMalformedError(
                f"sheet {sheet_rel!r} references {files[0]!r} outside the captured inputs"
            )
        found.append((sheet_uuid, child))
    return found


def _parse_schematic_file(raw: bytes, rel: str):
    try:
        root = parse_sexpr(raw)
    except LibraryError as exc:
        raise PartBindingMalformedError(f"invalid schematic S-expression in {rel!r}: {exc}") from exc
    if not isinstance(root, list) or not root or root[0] != "kicad_sch":
        raise PartBindingMalformedError(f"expected S-expression with root 'kicad_sch' in {rel!r}")
    return root


def _sheet_occurrences(sch_bytes: bytes, root_rel: str, sheets: dict[str, bytes]):
    """Walk the captured sheet hierarchy; yield (rel, parsed root, instance path, flat)."""
    parsed = {root_rel: _parse_schematic_file(sch_bytes, root_rel)}
    root_uuid = _extract_single_str(parsed[root_rel], "uuid", required=False, default="")
    flat = not children(parsed[root_rel], "sheet")
    occurrences = []
    pending = [(root_rel, f"/{root_uuid}" if root_uuid else "/", ())]
    while pending:
        rel, instance, trail = pending.pop()
        if rel in trail:
            raise PartBindingMalformedError(f"schematic sheet hierarchy cycle at {rel!r}")
        if len(occurrences) >= MAX_SHEET_OCCURRENCES:
            raise PartBindingMalformedError("schematic sheet occurrence limit exceeded")
        if rel not in parsed:
            if rel not in sheets:
                raise PartBindingMalformedError(
                    f"sheet {rel!r} is not captured; schematic binding coverage is incomplete"
                )
            parsed[rel] = _parse_schematic_file(sheets[rel], rel)
        occurrences.append((rel, parsed[rel], instance))
        for sheet_uuid, child in reversed(_sheet_children(parsed[rel], rel)):
            if not root_uuid:
                raise PartBindingMalformedError("hierarchical root schematic is missing its (uuid ...)")
            pending.append((child, f"{instance.rstrip('/')}/{sheet_uuid}", trail + (rel,)))
    return occurrences, flat, sorted(parsed)


def _occurrence_reference(symbol_node, props, instance: str, flat: bool, label: str):
    """Reference and unit of one symbol occurrence; never guessed in a hierarchy."""
    entries = _instance_entries(symbol_node)
    own = [(ref, unit) for path, ref, unit in entries if path == instance]
    if own:
        refs = {ref for ref, _ in own}
        if len(refs) > 1:
            raise PartBindingMalformedError(
                f"{label} has conflicting references {sorted(refs)} for instance {instance!r}"
            )
        return own[0]
    if not flat:
        raise PartBindingMalformedError(
            f"{label} has no (instances ...) reference for sheet occurrence {instance!r}"
        )
    # Flat schematic without a matching path: first annotated entry, else the Reference field.
    prop_ref = props.get("Reference", "").strip()
    annotated = [(ref, unit) for _, ref, unit in entries if not ref.endswith("?")]
    if annotated:
        return annotated[0]
    if entries:
        return (prop_ref or entries[0][1]), entries[0][2]
    return prop_ref, None


def _resolve_partnumber(props: dict[str, str]) -> tuple[str, str | None]:
    has_pn = "Partnumber" in props and bool(props["Partnumber"].strip())
    has_camel = "PartNumber" in props and bool(props["PartNumber"].strip())
    pn = props.get("Partnumber", "").strip()
    camel = props.get("PartNumber", "").strip()
    if has_pn and has_camel and pn != camel:
        return pn, f"conflicting Partnumber ({pn!r}) and PartNumber ({camel!r}) on same component"
    return (pn if has_pn else camel), None


def _is_no_lcsc(lcsc: str, props: dict[str, str]) -> bool:
    if lcsc.upper() in NO_LCSC_VALUES:
        return True
    for key in ("No_LCSC", "no_lcsc", "NO_LCSC"):
        if props.get(key, "").strip().lower() in TRUTHY_MARKERS:
            return True
    return False


def _is_dnp(dnp_flag: bool, lcsc: str, props: dict[str, str]) -> bool:
    if dnp_flag or lcsc.upper() == "DNP":
        return True
    for key in ("dnp", "DNP"):
        if props.get(key, "").strip().lower() in TRUTHY_MARKERS:
            return True
    return False


def schematic_hierarchy_bindings(
    sch_bytes: bytes,
    *,
    path: str = "",
    sheets: dict[str, bytes] | None = None,
    defer_param_errors: bool = False,
) -> tuple[dict[str, dict], list[str]]:
    """Parse placed symbol bindings across the captured sheet hierarchy.

    ``path`` is the root schematic's captured path; child ``Sheetfile`` paths
    resolve against their parent sheet and must be keys of ``sheets``. Every
    sheet occurrence contributes its own instance references, so a reused
    sheet yields one binding per occurrence. Returns (bindings, sheet files).
    """
    occurrences, flat, files = _sheet_occurrences(sch_bytes, path, sheets or {})
    power_by_file = {}
    bindings: dict[str, dict] = {}

    for rel, root, instance in occurrences:
        if rel not in power_by_file:
            power_by_file[rel] = _identify_power_lib_symbols(root)
        power_libs = power_by_file[rel]
        for sym in children(root, "symbol"):
            lib_id = _extract_single_str(sym, "lib_id", required=True)
            uuid = _extract_single_str(sym, "uuid", required=False, default="")
            unit_str = _extract_single_str(sym, "unit", required=False, default="1")
            try:
                unit = int(unit_str)
            except ValueError as exc:
                raise PartBindingMalformedError(f"invalid symbol unit {unit_str!r}") from exc

            dnp_node = _extract_yes_no(sym, "dnp", False)
            in_bom = _extract_yes_no(sym, "in_bom", True)
            on_board = _extract_yes_no(sym, "on_board", True)

            label = f"symbol({lib_id}) uuid {uuid!r} in {rel!r}"
            props, params, param_errors = _extract_properties(
                sym, label, defer_param_errors=defer_param_errors
            )
            ref, inst_unit = _occurrence_reference(sym, props, instance, flat, label)
            if inst_unit is not None:
                unit = inst_unit

            if not ref:
                raise PartBindingMalformedError(f"{label} is missing Reference")

            lcsc = props.get("LCSC", "").strip()
            manufacturer = props.get("Manufacturer", "").strip()
            partnumber, pn_conflict = _resolve_partnumber(props)
            datasheet = _normalize_datasheet(props.get("Datasheet", ""))
            footprint = props.get("Footprint", "").strip()
            value = props.get("Value", "").strip()

            dnp = _is_dnp(dnp_node, lcsc, props)
            no_lcsc = _is_no_lcsc(lcsc, props)
            is_power = (
                ref.startswith("#")
                or ref == "PWR_FLAG"
                or value == "PWR_FLAG"
                or lib_id in power_libs
                or lib_id.startswith("power:")
            )
            virtual = is_power or not on_board

            unit_entry = {
                "reference": ref,
                "lib_id": lib_id,
                "uuid": uuid,
                "sheet": rel,
                "instance_path": instance,
                "unit": unit,
                "units": [unit],
                "uuids": [uuid] if uuid else [],
                "LCSC": lcsc,
                "lcsc": lcsc,
                "Manufacturer": manufacturer,
                "manufacturer": manufacturer,
                "Partnumber": partnumber,
                "partnumber": partnumber,
                "PartNumber": props.get("PartNumber", "").strip(),
                "Datasheet": datasheet,
                "datasheet": datasheet,
                "Footprint": footprint,
                "footprint": footprint,
                "Value": value,
                "value": value,
                "dnp": dnp,
                "native_dnp": dnp_node,
                "in_bom": in_bom,
                "on_board": on_board,
                "virtual": virtual,
                "unannotated": ref.endswith("?"),
                "no_lcsc": no_lcsc,
                "exempt": dnp or no_lcsc,
                "params": params,
                "param_errors": [
                    {"reference": ref, "source": "schematic", **err}
                    for err in param_errors
                ],
                "properties": props,
                "unit_disagreements": [],
            }
            if pn_conflict:
                unit_entry["unit_disagreements"].append({
                    "reference": ref,
                    "field": "Partnumber",
                    "property": "Partnumber",
                    "schematic": props.get("Partnumber", ""),
                    "board": props.get("PartNumber", ""),
                    "reason": pn_conflict,
                })

            if ref not in bindings:
                bindings[ref] = unit_entry
            else:
                existing = bindings[ref]
                if unit in existing["units"] and not existing["unannotated"]:
                    existing["unit_disagreements"].append({
                        "reference": ref,
                        "field": "reference",
                        "property": "Reference",
                        "schematic": f"{existing['sheet']} unit {unit}",
                        "board": f"{rel} unit {unit}",
                        "reason": f"duplicate schematic reference {ref!r} unit {unit}",
                    })
                existing["units"].append(unit)
                if uuid:
                    existing["uuids"].append(uuid)
                existing["unit_disagreements"].extend(unit_entry["unit_disagreements"])
                existing["param_errors"].extend(unit_entry["param_errors"])

                # Check boolean flags across units
                for flag_key in ("dnp", "native_dnp", "in_bom", "on_board", "virtual"):
                    if existing[flag_key] != unit_entry[flag_key]:
                        existing["unit_disagreements"].append({
                            "reference": ref,
                            "field": flag_key,
                            "property": flag_key,
                            "schematic": existing[flag_key],
                            "board": unit_entry[flag_key],
                            "reason": f"multi-unit schematic symbol {ref} disagrees on {flag_key} across units",
                        })

                # Merge or record disagreement on string binding properties
                for prop_key, lower_key in (
                    ("LCSC", "lcsc"),
                    ("Manufacturer", "manufacturer"),
                    ("Partnumber", "partnumber"),
                    ("Datasheet", "datasheet"),
                    ("Footprint", "footprint"),
                    ("Value", "value"),
                ):
                    old_val = existing[prop_key]
                    new_val = unit_entry[prop_key]
                    if old_val and new_val and old_val != new_val:
                        existing["unit_disagreements"].append({
                            "reference": ref,
                            "field": prop_key,
                            "property": prop_key,
                            "schematic": old_val,
                            "board": new_val,
                            "reason": f"multi-unit schematic symbol {ref} disagrees on {prop_key}: {old_val!r} vs {new_val!r}",
                        })
                    elif not old_val and new_val:
                        existing[prop_key] = new_val
                        existing[lower_key] = new_val

                existing["no_lcsc"] = existing["no_lcsc"] or unit_entry["no_lcsc"]
                existing["exempt"] = existing["dnp"] or existing["no_lcsc"]

                # Merge or record disagreement on PARAM_* properties
                for p_name, p_val in params.items():
                    if p_name in existing["params"] and existing["params"][p_name] != p_val:
                        existing["unit_disagreements"].append({
                            "reference": ref,
                            "field": p_name,
                            "property": p_name,
                            "schematic": existing["params"][p_name],
                            "board": p_val,
                            "reason": f"multi-unit schematic symbol {ref} disagrees on {p_name}",
                        })
                    else:
                        existing["params"][p_name] = p_val

                for k, v in props.items():
                    if k not in existing["properties"] or not existing["properties"][k]:
                        existing["properties"][k] = v

    return bindings, files


def parse_schematic_bindings(
    sch_bytes: bytes,
    *,
    path: str = "",
    sheets: dict[str, bytes] | None = None,
    defer_param_errors: bool = False,
) -> dict[str, dict]:
    """Parse placed symbol part bindings from captured .kicad_sch bytes."""
    return schematic_hierarchy_bindings(
        sch_bytes, path=path, sheets=sheets, defer_param_errors=defer_param_errors
    )[0]


def _extract_fp_text(fp_node) -> dict[str, str]:
    texts = {}
    for item in children(fp_node, "fp_text"):
        if len(item) >= 3 and isinstance(item[1], str) and isinstance(item[2], str):
            kind = item[1].strip().lower()
            if kind in ("reference", "value") and kind not in texts:
                texts[kind] = item[2]
    return texts


def _extract_attr_tokens(fp_node) -> list[str]:
    tokens = []
    for attr in children(fp_node, "attr"):
        for item in attr[1:]:
            if not isinstance(item, str):
                raise PartBindingMalformedError("malformed non-atom in footprint (attr ...)")
            tokens.append(item.strip().lower())
    return tokens


def _has_copper_pads(fp_node) -> bool:
    has_copper = False
    for pad in children(fp_node, "pad"):
        layers = children(pad, "layers")
        if (len(layers) != 1 or len(layers[0]) < 2
                or any(not isinstance(layer, str) or not layer for layer in layers[0][1:])):
            raise PartBindingMalformedError("footprint pad layers must be explicit atoms")
        pad_has_copper = any(layer.endswith(".Cu") for layer in layers[0][1:])
        if pad_has_copper and len(pad) > 2 and pad[2] == "np_thru_hole":
            from .land_pattern import PatternError, extract_footprint

            # The shared qualifier proves bare holes have no annulus. An
            # unqualified hole keeps its copper claim and must carry bindings.
            try:
                extract_footprint(["footprint", fp_node[1], pad], side="F.Cu",
                                  frame_angle=0, label="part-binding NPTH")
            except PatternError as exc:
                if exc.mode == "empty_copper":
                    pad_has_copper = False
                elif exc.execution == "malformed":
                    raise PartBindingMalformedError(str(exc)) from exc
        has_copper |= pad_has_copper
    return has_copper


def parse_board_bindings(
    pcb_bytes: bytes, *, defer_param_errors: bool = False
) -> dict[str, dict]:
    """Parse footprint part bindings from captured .kicad_pcb bytes."""
    try:
        root = parse_sexpr(pcb_bytes)
    except LibraryError as exc:
        raise PartBindingMalformedError(f"invalid board S-expression: {exc}") from exc

    if not isinstance(root, list) or not root or root[0] != "kicad_pcb":
        raise PartBindingMalformedError("expected S-expression with root 'kicad_pcb'")

    footprint_nodes = children(root, "footprint") + children(root, "module")
    bindings: dict[str, dict] = {}

    for fp in footprint_nodes:
        if len(fp) < 2 or not isinstance(fp[1], str) or not fp[1].strip():
            raise PartBindingMalformedError("footprint is missing library identifier")
        lib_id = fp[1].strip()
        uuid = _extract_single_str(fp, "uuid", required=False, default="")
        if not uuid:
            uuid = _extract_single_str(fp, "tstamp", required=False, default="")

        attrs = _extract_attr_tokens(fp)
        dnp_node = _extract_yes_no(fp, "dnp", False)
        in_bom_node = _extract_yes_no(fp, "in_bom", True)
        on_board_node = _extract_yes_no(fp, "on_board", True)

        props, params, param_errors = _extract_properties(
            fp, f"footprint({lib_id})", defer_param_errors=defer_param_errors
        )
        fp_texts = _extract_fp_text(fp)

        ref = props.get("Reference", "").strip() or fp_texts.get("reference", "").strip()
        if not ref:
            raise PartBindingMalformedError(f"footprint {lib_id!r} is missing Reference")

        value = props.get("Value", "").strip() or fp_texts.get("value", "").strip()
        lcsc = props.get("LCSC", "").strip()
        manufacturer = props.get("Manufacturer", "").strip()
        partnumber, pn_conflict = _resolve_partnumber(props)
        datasheet = _normalize_datasheet(props.get("Datasheet", ""))
        prop_footprint = props.get("Footprint", "").strip()
        footprint = prop_footprint or lib_id

        dnp = ("dnp" in attrs) or _is_dnp(dnp_node, lcsc, props)
        in_bom = ("exclude_from_bom" not in attrs) and in_bom_node
        board_only = "board_only" in attrs
        has_copper_pads = _has_copper_pads(fp)
        virtual = not has_copper_pads and (
            ("virtual" in attrs)
            or board_only
            or ref.startswith("#")
            or ref == "REF**"
            or not on_board_node
        )
        no_lcsc = _is_no_lcsc(lcsc, props)

        entry = {
            "reference": ref,
            "lib_id": lib_id,
            "uuid": uuid,
            "attrs": attrs,
            "LCSC": lcsc,
            "lcsc": lcsc,
            "Manufacturer": manufacturer,
            "manufacturer": manufacturer,
            "Partnumber": partnumber,
            "partnumber": partnumber,
            "PartNumber": props.get("PartNumber", "").strip(),
            "Datasheet": datasheet,
            "datasheet": datasheet,
            "Footprint": footprint,
            "footprint": footprint,
            "Value": value,
            "value": value,
            "dnp": dnp,
            "native_dnp": ("dnp" in attrs) or dnp_node,
            "in_bom": in_bom,
            "on_board": on_board_node,
            "board_only": board_only,
            "has_copper_pads": has_copper_pads,
            "virtual": virtual,
            "unannotated": ref.endswith("?"),
            "no_lcsc": no_lcsc,
            "exempt": dnp or no_lcsc,
            "params": params,
            "param_errors": [
                {"reference": ref, "source": "board", **err}
                for err in param_errors
            ],
            "properties": props,
            "duplicate_disagreements": [],
        }
        if pn_conflict:
            entry["duplicate_disagreements"].append({
                "reference": ref,
                "field": "Partnumber",
                "property": "Partnumber",
                "schematic": props.get("Partnumber", ""),
                "board": props.get("PartNumber", ""),
                "reason": pn_conflict,
            })
        if prop_footprint and prop_footprint != lib_id:
            entry["duplicate_disagreements"].append({
                "reference": ref,
                "field": "Footprint",
                "property": "Footprint",
                "schematic": prop_footprint,
                "board": lib_id,
                "reason": f"board footprint {ref} property Footprint ({prop_footprint!r}) disagrees with footprint lib_id ({lib_id!r})",
            })

        if ref in bindings:
            prev = bindings[ref]
            prev["param_errors"].extend(entry["param_errors"])
            if not prev["virtual"] or not entry["virtual"]:
                prev["duplicate_disagreements"].append({
                    "reference": ref,
                    "field": "reference",
                    "property": "Reference",
                    "schematic": prev["lib_id"],
                    "board": entry["lib_id"],
                    "reason": f"duplicate board footprint reference {ref!r}",
                })
        else:
            bindings[ref] = entry

    return bindings


def _check_component_bindings(
    comp: dict,
    source_name: str,
    *,
    require_datasheet: bool,
    require_params: bool,
    missing_bindings: list[dict],
    invalid_bindings: list[dict],
):
    ref = comp["reference"]
    lcsc = comp["LCSC"]
    manufacturer = comp["Manufacturer"]
    partnumber = comp["Partnumber"]
    datasheet = comp["Datasheet"]
    params = comp["params"]
    dnp = comp["dnp"]
    no_lcsc = comp["no_lcsc"]

    # Validate LCSC format whenever a non-marker LCSC string is provided
    if lcsc and lcsc.upper() not in NO_LCSC_VALUES:
        if not LCSC_RE.match(lcsc):
            invalid_bindings.append({
                "reference": ref,
                "source": source_name,
                "property": "LCSC",
                "field": "LCSC",
                "value": lcsc,
                "reason": f"{source_name} {ref} has invalid LCSC code {lcsc!r} (expected ^C[1-9][0-9]*$)",
            })

    # A DNP or no-LCSC marker stands in for the LCSC code only; Manufacturer and
    # Partnumber stay required on every non-virtual component.
    missing_props = []
    if comp.get("unannotated"):
        missing_props.append("Reference")
    if not lcsc and not (dnp or no_lcsc):
        missing_props.append("LCSC")
    if not manufacturer:
        missing_props.append("Manufacturer")
    if not partnumber:
        missing_props.append("Partnumber")
    if not (dnp or no_lcsc):
        if require_datasheet and not datasheet:
            missing_props.append("Datasheet")
        if require_params and not (set(params) - {"PARAM_Datasheet"}) and not comp.get("param_errors"):
            missing_props.append("PARAM_*")

    if "PARAM_Datasheet" in params and params["PARAM_Datasheet"]["url"] != datasheet:
        invalid_bindings.append({"reference": ref, "source": source_name,
            "property": "PARAM_Datasheet", "field": "PARAM_Datasheet",
            "value": params["PARAM_Datasheet"], "reason": "captured URL differs from Datasheet"})

    for prop in missing_props:
        missing_bindings.append({
            "reference": ref,
            "source": source_name,
            "property": prop,
            "field": prop,
            "missing": missing_props,
            "reason": f"{source_name} {ref} is missing required property {prop}",
        })


def _reference_outcomes(
    evaluated, missing_bindings, invalid_bindings, disagreements, param_errors=None
) -> dict[str, str]:
    """Per-reference outcome: malformed dominates defect, which dominates unknown; pass only with no record at all."""
    outcomes = dict.fromkeys(evaluated, "pass")
    for item in missing_bindings:
        if outcomes.get(item["reference"]) not in ("defect", "malformed"):
            outcomes[item["reference"]] = "unknown"
    for item in [*invalid_bindings, *disagreements]:
        if outcomes.get(item["reference"]) != "malformed":
            outcomes[item["reference"]] = "defect"
    for item in param_errors or ():
        outcomes[item["reference"]] = "malformed"
    return dict(sorted(outcomes.items()))


def _one_sided_missing(missing_bindings: list[dict], ref: str, side: str, prop: str):
    if any(m["reference"] == ref and m["source"] == side and m["property"] == prop for m in missing_bindings):
        return
    other = "schematic" if side == "board" else "board"
    missing_bindings.append({
        "reference": ref,
        "source": side,
        "property": prop,
        "field": prop,
        "missing": [prop],
        "reason": f"{side} {ref} lacks {prop} that the {other} carries",
    })


def validate_part_bindings(
    sch_bytes: bytes | None = None,
    pcb_bytes: bytes | None = None,
    *,
    require_datasheet: bool = False,
    require_params: bool = False,
    schematic_path: str = "",
    sheets: dict[str, bytes] | None = None,
) -> dict:
    """Validate schematic and/or board part bindings and cross-check parity.

    ``schematic_path`` and ``sheets`` supply the captured sheet hierarchy below
    the root schematic; a child sheet that is not supplied is malformed.
    """
    if sch_bytes is None and pcb_bytes is None:
        raise PartBindingError("at least one of sch_bytes or pcb_bytes is required")

    try:
        sch_bindings, sch_files = (
            schematic_hierarchy_bindings(
                sch_bytes, path=schematic_path, sheets=sheets, defer_param_errors=True
            )
            if sch_bytes is not None else ({}, [])
        )
        pcb_bindings = (
            parse_board_bindings(pcb_bytes, defer_param_errors=True)
            if pcb_bytes is not None else {}
        )
    except PartBindingMalformedError as exc:
        return {
            "schema": "agent-ee.part-binding/1",
            "execution": "malformed",
            "outcome": "unknown",
            "summary": f"malformed part binding input: {exc}",
            "schematic_components": {},
            "board_components": {},
            "schematic_files": [],
            "reference_outcomes": {},
            "evaluated_references": [],
            "exempt_references": [],
            "virtual_references": [],
            "missing_bindings": [],
            "invalid_bindings": [],
            "disagreements": [],
            "param_errors": [],
            "errors": [str(exc)],
        }

    missing_bindings: list[dict] = []
    invalid_bindings: list[dict] = []
    disagreements: list[dict] = []
    param_errors: list[dict] = []
    evaluated_refs: set[str] = set()
    exempt_refs: set[str] = set()
    virtual_refs: set[str] = set()

    if sch_bytes is not None:
        for ref in sorted(sch_bindings):
            comp = sch_bindings[ref]
            disagreements.extend(comp.get("unit_disagreements", []))
            param_errors.extend(comp.get("param_errors", []))
            if comp["virtual"]:
                virtual_refs.add(ref)
                continue
            evaluated_refs.add(ref)
            if comp["exempt"]:
                exempt_refs.add(ref)
            _check_component_bindings(
                comp,
                "schematic",
                require_datasheet=require_datasheet,
                require_params=require_params,
                missing_bindings=missing_bindings,
                invalid_bindings=invalid_bindings,
            )

    if pcb_bytes is not None:
        for ref in sorted(pcb_bindings):
            comp = pcb_bindings[ref]
            disagreements.extend(comp.get("duplicate_disagreements", []))
            if comp["virtual"]:
                virtual_refs.add(ref)
                continue
            evaluated_refs.add(ref)
            if comp["exempt"]:
                exempt_refs.add(ref)
            _check_component_bindings(
                comp,
                "board",
                require_datasheet=require_datasheet,
                require_params=require_params,
                missing_bindings=missing_bindings,
                invalid_bindings=invalid_bindings,
            )

    if pcb_bytes is not None:
        for ref in sorted(pcb_bindings):
            param_errors.extend(pcb_bindings[ref].get("param_errors", []))

    if sch_bytes is not None and pcb_bytes is not None:
        for ref in sorted(set(sch_bindings) | set(pcb_bindings)):
            sch_comp = sch_bindings.get(ref)
            pcb_comp = pcb_bindings.get(ref)

            if sch_comp is not None and pcb_comp is None:
                if not sch_comp["virtual"] and sch_comp["on_board"] and not sch_comp["dnp"]:
                    disagreements.append({
                        "reference": ref,
                        "field": "presence",
                        "property": "presence",
                        "schematic": "present",
                        "board": "missing",
                        "reason": f"non-virtual component {ref} is present in schematic but missing from board",
                    })
                continue

            if pcb_comp is not None and sch_comp is None:
                if not pcb_comp["virtual"]:
                    disagreements.append({
                        "reference": ref,
                        "field": "presence",
                        "property": "presence",
                        "schematic": "missing",
                        "board": "present",
                        "reason": f"non-virtual footprint {ref} is present on board but missing from schematic",
                    })
                continue

            # Both exist
            if sch_comp["virtual"] and pcb_comp["virtual"]:
                continue
            if sch_comp["virtual"] != pcb_comp["virtual"]:
                if pcb_comp["has_copper_pads"] or not pcb_comp.get("board_only", False):
                    disagreements.append({
                        "reference": ref,
                        "field": "virtual",
                        "property": "virtual",
                        "schematic": sch_comp["virtual"],
                        "board": pcb_comp["virtual"],
                        "reason": f"{ref} virtual status disagrees between schematic ({sch_comp['virtual']}) and board ({pcb_comp['virtual']})",
                    })
                continue

            # Compare properties for non-virtual paired components. A binding
            # property present on one side only is missing coverage there
            # (unknown); two different non-empty values are a disagreement.
            for field_name in (
                "LCSC",
                "Manufacturer",
                "Partnumber",
                "Datasheet",
                "Footprint",
                "Value",
                "dnp",
                "native_dnp",
                "in_bom",
            ):
                s_val = sch_comp[field_name]
                b_val = pcb_comp[field_name]
                if s_val == b_val:
                    continue
                if field_name in BINDING_FIELDS and not (s_val and b_val):
                    side = "board" if s_val else "schematic"
                    _one_sided_missing(missing_bindings, ref, side, field_name)
                    continue
                disagreements.append({
                    "reference": ref,
                    "field": field_name,
                    "property": field_name,
                    "schematic": s_val,
                    "board": b_val,
                    "reason": f"{ref} disagrees on {field_name}: schematic={s_val!r} vs board={b_val!r}",
                })

            # If both explicitly define PartNumber as well as Partnumber, verify exact match
            if (
                "Partnumber" in sch_comp["properties"]
                and "PartNumber" in sch_comp["properties"]
                and "Partnumber" in pcb_comp["properties"]
                and "PartNumber" in pcb_comp["properties"]
                and sch_comp["PartNumber"] != pcb_comp["PartNumber"]
            ):
                disagreements.append({
                    "reference": ref,
                    "field": "PartNumber",
                    "property": "PartNumber",
                    "schematic": sch_comp["PartNumber"],
                    "board": pcb_comp["PartNumber"],
                    "reason": f"{ref} disagrees on PartNumber: schematic={sch_comp['PartNumber']!r} vs board={pcb_comp['PartNumber']!r}",
                })

            malformed_param_names = {
                err["property"]
                for err in (sch_comp.get("param_errors", []) + pcb_comp.get("param_errors", []))
            }
            all_params = sorted(set(sch_comp["params"]) | set(pcb_comp["params"]))
            for p_name in all_params:
                if p_name in malformed_param_names:
                    continue
                s_param = sch_comp["params"].get(p_name)
                b_param = pcb_comp["params"].get(p_name)
                if s_param is None or b_param is None:
                    _one_sided_missing(missing_bindings, ref, "board" if b_param is None else "schematic", p_name)
                elif s_param != b_param:
                    disagreements.append({
                        "reference": ref,
                        "field": p_name,
                        "property": p_name,
                        "schematic": s_param,
                        "board": b_param,
                        "reason": f"{ref} disagrees on {p_name}: schematic={s_param!r} vs board={b_param!r}",
                    })

    if param_errors:
        execution = "malformed"
        outcome = "unknown"
        summary = f"malformed part binding parameter(s): {param_errors[0]['reason']}"
    elif disagreements or invalid_bindings:
        execution = "ok"
        outcome = "defect"
        parts = []
        if disagreements:
            parts.append(f"{len(disagreements)} disagreement(s)")
        if invalid_bindings:
            parts.append(f"{len(invalid_bindings)} invalid binding(s)")
        summary = "part-binding validation found " + " and ".join(parts)
    elif missing_bindings:
        execution = "ok"
        outcome = "unknown"
        summary = f"part-binding incomplete: {len(missing_bindings)} missing binding property(ies)"
    elif not evaluated_refs:
        execution = "ok"
        outcome = "info"
        summary = "no non-virtual components to evaluate for part bindings"
    else:
        execution = "ok"
        outcome = "pass"
        summary = f"all {len(evaluated_refs)} evaluated component(s) have valid, consistent part bindings"

    return {
        "schema": "agent-ee.part-binding/1",
        "execution": execution,
        "outcome": outcome,
        "summary": summary,
        "schematic_components": sch_bindings,
        "board_components": pcb_bindings,
        "schematic_files": sch_files,
        "reference_outcomes": _reference_outcomes(
            evaluated_refs, missing_bindings, invalid_bindings, disagreements, param_errors
        ),
        "evaluated_references": sorted(evaluated_refs),
        "exempt_references": sorted(exempt_refs),
        "virtual_references": sorted(virtual_refs),
        "missing_bindings": missing_bindings,
        "invalid_bindings": invalid_bindings,
        "disagreements": disagreements,
        "param_errors": param_errors,
        "errors": [
            f"{err['source']} {err['reference']}: {err['reason']}"
            for err in param_errors
        ],
    }


def capability(params, scope, files, inputs=None) -> dict:
    """Validate check parameters and return capability descriptor for scope/3 audits."""
    if not isinstance(params, dict):
        raise ValueError("part_binding params must be an object")
    unknown = set(params) - ALLOWED_CHECK_PARAMS
    if unknown:
        raise ValueError(f"unknown part_binding parameters: {sorted(unknown)}")

    for flag in ("require_datasheet", "require_params"):
        if flag in params and type(params[flag]) is not bool:
            raise ValueError(f"{flag} must be a boolean")

    cal_inputs = params.get("calibration_inputs", [])
    if not isinstance(cal_inputs, list):
        raise ValueError("calibration_inputs must be a list")
    immutable = []
    for item in cal_inputs:
        if not isinstance(item, str):
            raise ValueError("calibration_inputs entries must be strings")
        rel = str(safe_relative(item))
        if rel != item or rel not in files:
            raise ValueError(f"calibration input not captured: {item!r}")
        immutable.append(rel)

    scope_dict = scope if isinstance(scope, dict) else {}
    sch_path = params.get("schematic", scope_dict.get("schematic"))
    pcb_path = params.get("board", scope_dict.get("board"))
    if sch_path is None and pcb_path is None:
        raise ValueError("part_binding requires at least one of schematic or board")

    for label, path in (("schematic", sch_path), ("board", pcb_path)):
        if path is not None:
            if not isinstance(path, str) or not path.strip():
                raise ValueError(f"{label} path must be non-empty text")
            rel = str(safe_relative(path))
            if rel != path or rel not in files:
                raise ValueError(f"{label} input not captured: {path!r}")

    return {
        "immutable_inputs": sorted(set(immutable)),
        "predicates": ["part-binding:schema/1", "part-binding:parity/1"],
    }


def check_part_binding(ctx):
    """Run part-binding check inside a check execution context."""
    from .checks import CheckError

    try:
        capability(ctx.params, ctx.scope, ctx.manifest["files"], ctx.inputs)
    except (ValueError, KeyError, TypeError) as exc:
        raise CheckError("unsupported", str(exc)) from exc

    sch_rel = ctx.params.get("schematic", ctx.scope.get("schematic"))
    pcb_rel = ctx.params.get("board", ctx.scope.get("board"))
    require_datasheet = bool(ctx.params.get("require_datasheet", False))
    require_params = bool(ctx.params.get("require_params", False))

    sch_bytes = None
    pcb_bytes = None
    sheets = {}
    input_digests = {}

    try:
        if sch_rel is not None:
            sch_file = Path(ctx.inputs) / sch_rel
            sch_bytes = sch_file.read_bytes()
            input_digests["schematic"] = {"path": sch_rel, "sha256": digest(sch_file)}
            # Child sheets resolve only among captured schematic files.
            for rel in ctx.manifest["files"]:
                if rel != sch_rel and rel.endswith(".kicad_sch"):
                    sheets[rel] = (Path(ctx.inputs) / rel).read_bytes()
        if pcb_rel is not None:
            pcb_file = Path(ctx.inputs) / pcb_rel
            pcb_bytes = pcb_file.read_bytes()
            input_digests["board"] = {"path": pcb_rel, "sha256": digest(pcb_file)}
    except OSError as exc:
        raise CheckError("malformed", f"unable to read captured design files: {exc}") from exc

    report = validate_part_bindings(
        sch_bytes,
        pcb_bytes,
        require_datasheet=require_datasheet,
        require_params=require_params,
        schematic_path=sch_rel or "",
        sheets=sheets,
    )
    if len(report["schematic_files"]) > 1:
        input_digests["sheets"] = [
            {"path": rel, "sha256": digest(Path(ctx.inputs) / rel)}
            for rel in report["schematic_files"] if rel != sch_rel
        ]
    report["candidate_id"] = ctx.manifest["candidate_id"]
    report["inputs"] = input_digests

    write_json(ctx.work / "part_binding.json", report)
    ctx.evidence("part_binding.json")

    ctx.result["execution"] = report["execution"]
    ctx.result["outcome"] = report["outcome"]
    ctx.result["summary"] = report["summary"]
    ctx.result["details"].update(report)
    ctx.result["limitations"].append(
        "Static property and parameter schema/parity validation over captured KiCad S-expressions; "
        "does not verify live vendor stock or electrical suitability against circuit operating points."
    )

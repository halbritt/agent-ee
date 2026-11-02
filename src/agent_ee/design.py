"""Compile explicit design decisions into the existing typed build contracts."""

import math
import re
from pathlib import Path

from .catalog import strict_json
from .storage import digest
from .symbol_library import children, parse_library, parse_sexpr, resolve_symbol

SCHEMA = "agent-ee.design/1"
CLASS_SYMBOLS = dict(
    resistor="Device:R",
    capacitor="Device:C",
    inductor="Device:L",
    ferrite="Device:FerriteBead",
    fuse="Device:Fuse",
    diode="Device:D",
    led="Device:LED",
    polarized_capacitor="Device:C_Polarized",
)


class DesignError(ValueError):
    """All independently actionable input problems, with design field paths."""

    def __init__(self, problems):
        self.problems = problems
        super().__init__("Design refused:\n" + "\n".join("- " + p for p in problems))


def load(path):
    return strict_json(Path(path).read_bytes())


def virtual_parts(design):
    group = design["parts"][next(iter(sorted(design["parts"])))]["group"]
    result = {
        "PWR01": dict(
            reference="#PWR01", lib_id="power:GND", value=design["ground"], group=group
        )
    }
    for index, net in enumerate(sorted(design["power_flags"]), 1):
        result[f"FLG{index:02}"] = dict(
            reference=f"#FLG{index:02}",
            lib_id="power:PWR_FLAG",
            value="PWR_FLAG",
            group=group,
        )
    return result


def footprint_path(part):
    lib, name = part["footprint"].split(":")
    return f"{lib}.pretty/{name}.kicad_mod"


def number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def device_class(lib_id):
    """Known Device two-terminal families; unfamiliar symbols remain declarations."""
    if not lib_id or not lib_id.startswith("Device:"):
        return None
    name = lib_id.split(":", 1)[1]
    for family, kind in (("C_Polarized", "polarized_capacitor"), ("FerriteBead", "ferrite"),
                         ("Fuse", "fuse"), ("Polyfuse", "fuse"), ("LED", "led"), ("R", "resistor"),
                         ("C", "capacitor"), ("L", "inductor"), ("D", "diode")):
        if name == family or name.startswith(family + "_"):
            return kind
    return None


def device_polarized(lib_id):
    return device_class(lib_id) in ("diode", "led", "polarized_capacitor") or (
        device_class(lib_id) is not None and "Polarized" in lib_id.split(":", 1)[1].split("_")
    )


def validate(design, captures=None, *, cad_only=False):
    """Validate decisions and captured pin inventories without writing anything."""
    from .design_shape import problems

    shape_errors = problems(design)
    if shape_errors:
        raise DesignError(shape_errors)
    if captures is not None:
        from .requirement_map import input_inventory

        try:
            root = Path(captures)
            if not root.is_dir() or root.is_symlink():
                raise ValueError("capture root must be a real directory")
            input_inventory(root)
        except (OSError, ValueError) as exc:
            raise DesignError(
                [
                    f"captures: supply regular captured files without links or special files: {exc}"
                ]
            ) from exc
    errors = []
    if captures is not None and not cad_only:
        from .design_capture import validate_capture_manifest

        try:
            validate_capture_manifest(Path(captures))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise DesignError(
                [f"captures.capture.json: run design capture again; {exc}"]
            ) from exc

    def fail(path, why):
        errors.append(f"{path}: {why}")

    def fields(obj, required, optional, path):
        if not isinstance(obj, dict):
            fail(path, "supply an object")
            return False
        for k in sorted(set(required) - obj.keys()):
            fail(path + "." + k, "supply this required field")
        for k in sorted(obj.keys() - set(required) - set(optional)):
            fail(
                path + "." + k, "unknown field; remove it or use the documented format"
            )
        return not (set(required) - obj.keys())

    if not fields(
        design,
        (
            "schema",
            "name",
            "requirement_prefix",
            "intent",
            "board",
            "parts",
            "nets",
            "no_connects",
            "power_flags",
            "ground",
            "connectors",
            "decoupling",
            "groups",
            "plane",
            "functional",
            "bench",
        ),
        ("placement", "mechanics"),
        "design",
    ):
        raise DesignError(errors)
    if design["schema"] != SCHEMA:
        fail("schema", "use agent-ee.design/1")
    for key in ("name", "requirement_prefix"):
        if not isinstance(design[key], str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,47}", design[key]
        ):
            fail(key, "use a short identifier beginning with a letter")

    from .spec_record import PREFIX

    if not PREFIX.fullmatch(design["requirement_prefix"]):
        fail(
            "requirement_prefix",
            "use 1 to 16 uppercase ASCII letters/digits, beginning with a letter",
        )

    def prose(text, path):
        if (
            not isinstance(text, str)
            or not text.strip()
            or "\n" in text
            or "\r" in text
        ):
            fail(path, "supply one nonempty paragraph on one line")

    prose(design["intent"], "intent")
    from .spec_record import Parser, SpecRefused

    try:
        parsed = Parser(("# Design intent\n\n" + design["intent"] + "\n").encode()).parse()
        if (len(parsed.headings) != 1 or parsed.items or len(parsed.prose) != 1
                or parsed.text(parsed.prose[0]["byte_start"], parsed.prose[0]["byte_end"]) != design["intent"].strip()):
            fail("intent", "supply plain paragraph text; Markdown structure changes the generated spec")
    except SpecRefused as exc:
        fail("intent", f"supply plain paragraph text; {exc}")
    board = design["board"]
    if fields(
        board,
        (
            "width_mm",
            "height_mm",
            "thickness_mm",
            "copper_layers",
            "max_component_height_mm",
        ),
        (),
        "board",
    ):
        for k in ("width_mm", "height_mm", "thickness_mm", "max_component_height_mm"):
            if not number(board[k]) or board[k] <= 0:
                fail("board." + k, "supply a positive finite dimension in mm")
        for k, maximum in (
            ("width_mm", 670),
            ("height_mm", 600),
            ("max_component_height_mm", 1000),
        ):
            if number(board[k]) and board[k] > maximum:
                fail(
                    "board." + k,
                    f"supported maximum is {maximum} mm; reduce the dimension",
                )
        from decimal import Decimal

        for k in ("width_mm", "height_mm"):
            if number(board[k]) and abs(board[k]) <= 1000:
                nm = Decimal(str(board[k])) * 1000000
                if nm != nm.to_integral_value():
                    fail("board." + k, "use whole nanometres")
        if board["copper_layers"] != 2 or type(board["copper_layers"]) is not int:
            fail("board.copper_layers", "only two copper layers are supported; set 2")
        if board["thickness_mm"] != 1.2:
            fail(
                "board.thickness_mm",
                "the packaged sourced profile currently supports nominal 1.2 mm only",
            )
    parts = design["parts"]
    if not isinstance(parts, dict) or not parts:
        fail("parts", "supply parts keyed by physical reference")
        parts = {}
    groups = design["groups"]
    if not isinstance(groups, list) or not groups:
        fail("groups", "supply at least one group")
        groups = []
    group_names = set()
    for i, g in enumerate(groups):
        if fields(g, ("name", "anchor", "title"), (), f"groups[{i}]"):
            if not isinstance(g["name"], str) or not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_-]*", g["name"]
            ):
                fail(f"groups[{i}].name", "use an identifier")
            elif g["name"] in group_names:
                fail(f"groups[{i}].name", "group names must be unique")
            else:
                group_names.add(g["name"])
            if (
                g["anchor"] not in parts
                or parts.get(g["anchor"], {}).get("group") != g["name"]
            ):
                fail(f"groups[{i}].anchor", "select a part in this group")
            prose(g["title"], f"groups[{i}].title")
    pins, pads, assemblies, footprints = {}, {}, {}, {}
    cad = Path(captures) / "cad" if captures is not None else None
    for ref, p in parts.items():
        path = "parts." + ref
        if ref == "PWR01" or re.fullmatch(r"FLG[0-9]+", ref):
            fail(
                path,
                "reference is reserved for generated power symbols; select another physical reference",
            )
        if not re.fullmatch(r"[A-Z]+[1-9][0-9]*", ref):
            fail(path, "use a physical KiCad reference such as U1")
        if not fields(
            p, ("lcsc", "value", "lib_id", "footprint", "group"), ("connector",), path
        ):
            continue
        if not isinstance(p["lcsc"], str) or not re.fullmatch(
            r"C[1-9][0-9]*", p["lcsc"]
        ):
            fail(path + ".lcsc", "supply an exact LCSC number such as C25804")
        prose(p["value"], path + ".value")
        if p["group"] not in group_names:
            fail(path + ".group", "select a declared group")
        for key in ("lib_id", "footprint"):
            if (
                not isinstance(p[key], str)
                or not re.fullmatch(r"[^:/\\\s]+:[^:/\\\s]+", p[key])
                or ".." in p[key]
            ):
                fail(path + "." + key, "use library:member without path separators")
        if "connector" in p and p["connector"] is not None:
            c = p["connector"]
            if fields(c, ("rows", "columns"), (), path + ".connector"):
                if any(type(c[k]) is not int or c[k] < 1 for k in c):
                    fail(
                        path + ".connector",
                        "rows and columns must be positive integers",
                    )
        if cad is not None:
            try:
                lib, symbol = p["lib_id"].split(":")
                raw = (cad / (lib + ".kicad_sym")).read_bytes()
                inventory, _, _ = resolve_symbol(
                    parse_library(raw), symbol, lib, digest(cad / (lib + ".kicad_sym"))
                )
                pins[ref] = {x["number"] for x in inventory}
                if any(x["unit"] > 1 for x in inventory):
                    fail(
                        path + ".lib_id",
                        "multi-unit symbols are not supported by this compiler yet; choose a single-unit symbol",
                    )
            except (OSError, ValueError) as exc:
                fail(
                    path + ".lib_id",
                    f"capture the selected symbol and its parents: {exc}",
                )
            try:
                node = parse_sexpr((cad / footprint_path(p)).read_bytes())
                footprints[ref] = node
                pads[ref] = [n[1] for n in children(node, "pad") if n[1]]
                if any(n[3] == "custom" for n in children(node, "pad")):
                    fail(
                        path + ".footprint",
                        "custom pads need native qualification and are not supported by offline design compile; select ordinary pads",
                    )
                attrs = {v for n in children(node, "attr") for v in n[1:]} & {
                    "smd",
                    "through_hole",
                }
                if len(attrs) != 1:
                    fail(
                        path + ".footprint",
                        "select a footprint with exactly one native smd/through_hole attribute",
                    )
                else:
                    assemblies[ref] = next(iter(attrs))
                if ref in pins and set(pads[ref]) != pins[ref]:
                    fail(
                        path + ".footprint",
                        f"symbol pins {sorted(pins[ref])} and footprint pads {sorted(set(pads[ref]))} must agree; select matching CAD",
                    )
            except (OSError, ValueError, IndexError, TypeError) as exc:
                fail(path + ".footprint", f"capture the selected footprint: {exc}")

    def terminal(term, path):
        if not isinstance(term, str) or "." not in term:
            fail(path, "use REF.PIN")
            return None
        ref, pin = term.split(".", 1)
        if ref not in parts:
            fail(path, f"unknown reference {ref}; declare it in parts")
        elif pins and ref in pins and pin not in pins[ref]:
            fail(
                path,
                f"pin {pin} absent from captured symbol; choose one of {sorted(pins[ref])}",
            )
        elif pads and ref in pads and pin not in pads[ref]:
            fail(path, f"pin {pin} absent from captured footprint; select matching CAD")
        return ref, pin

    nets = design["nets"]
    if not isinstance(nets, dict) or not nets:
        fail("nets", "supply named nets")
        nets = {}
    used = set()
    for net, terms in nets.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_+.-]*", net):
            fail("nets." + net, "use a plain net identifier without native / prefix")
        if not isinstance(terms, list) or len(terms) < 2:
            fail("nets." + net, "connect at least two physical terminals")
            continue
        for i, term in enumerate(terms):
            terminal(term, f"nets.{net}[{i}]")
            if isinstance(term, str):
                if term in used:
                    fail(
                        f"nets.{net}[{i}]",
                        f"{term} is already assigned; give each terminal exactly one net or no-connect",
                    )
                used.add(term)
    if not isinstance(design["no_connects"], list):
        fail("no_connects", "supply a list")
        nc = []
    else:
        nc = design["no_connects"]
    for i, term in enumerate(nc):
        terminal(term, f"no_connects[{i}]")
        if isinstance(term, str):
            if term in used:
                fail(
                    f"no_connects[{i}]",
                    "terminal already connected or declared; remove the duplicate",
                )
            used.add(term)
    for ref, inventory in pins.items():
        for pin in sorted(inventory):
            if ref + "." + pin not in used:
                fail(
                    "parts." + ref,
                    f"pin {pin} needs a net or explicit no_connects entry",
                )
    if design["ground"] != "GND" or design["ground"] not in nets:
        fail(
            "ground",
            "use a declared GND net; other ground power symbols are not yet supported",
        )
    if (
        not isinstance(design["power_flags"], list)
        or any(n not in nets for n in design["power_flags"])
        or len(set(design["power_flags"])) != len(design["power_flags"])
    ):
        fail("power_flags", "list unique declared net names that carry power flags")
    if fields(design["plane"], ("net", "layer"), (), "plane"):
        if design["plane"]["net"] not in nets or design["plane"]["layer"] not in (
            "F.Cu",
            "B.Cu",
        ):
            fail("plane", "select a declared net and F.Cu or B.Cu")
    connectors = design["connectors"]
    if not isinstance(connectors, list):
        fail("connectors", "supply a list")
        connectors = []
    locked = set()
    for i, c in enumerate(connectors):
        path = f"connectors[{i}]"
        if not fields(
            c,
            (
                "reference",
                "edge",
                "position_mm",
                "inset_mm",
                "rotation_deg",
                "insertion",
            ),
            ("local_mating_axis",),
            path,
        ):
            continue
        ref = c["reference"]
        if ref not in parts or ref in locked:
            fail(path + ".reference", "select a unique declared part")
        locked.add(ref)
        if c["edge"] not in ("top", "right", "bottom", "left"):
            fail(path + ".edge", "choose top, right, bottom or left")
        else:
            extent = (
                board.get("width_mm")
                if c["edge"] in ("top", "bottom")
                else board.get("height_mm")
            )
            if (
                not number(c["position_mm"])
                or not number(extent)
                or not 0 <= c["position_mm"] <= extent
            ):
                fail(
                    path + ".position_mm",
                    "place the connector along the selected edge within the board dimensions",
                )
        cross_extent = (
            board.get("width_mm")
            if c["edge"] in ("left", "right")
            else board.get("height_mm")
        )
        if (
            not number(c["inset_mm"])
            or c["inset_mm"] < 0
            or (number(cross_extent) and c["inset_mm"] > cross_extent)
        ):
            fail(path + ".inset_mm", "supply a nonnegative inward distance")
        if not number(c["rotation_deg"]) or c["rotation_deg"] % 90:
            fail(path + ".rotation_deg", "use a multiple of 90 degrees")
        if (
            ref in footprints
            and c["edge"] in ("left", "right", "top", "bottom")
            and number(c["rotation_deg"])
            and not c["rotation_deg"] % 90
            and number(c["inset_mm"])
        ):
            from .design_geometry import connector_edge_gap

            try:
                gap = connector_edge_gap(footprints[ref], c)
                if gap > 1:
                    fail(
                        path + ".inset_mm",
                        f"rotated courtyard is {gap:g} mm from the edge; bring it within 1 mm",
                    )
            except ValueError as exc:
                fail(path + ".reference", str(exc))
        from .placement_predicates import AXES, board_axis

        axis = c.get("local_mating_axis")
        if c["insertion"] == "through_edge" and axis is None:
            fail(path + ".local_mating_axis", f"{ref}: declare the footprint-local mating axis")
        if axis is not None and axis not in AXES:
            fail(path + ".local_mating_axis", f"{ref}: choose x+, x-, y+ or y-")
        elif axis is not None and c["insertion"] == "through_edge" and number(c["rotation_deg"]):
            outward = {"left": "x-", "right": "x+", "top": "y-", "bottom": "y+"}.get(c["edge"])
            faces = board_axis(axis, c["rotation_deg"], "F")
            if faces != outward:
                fail(path + ".rotation_deg", f"{ref}: mating axis faces {faces}, not declared edge {c['edge']} ({outward})")
        if c["insertion"] not in ("from_top", "through_edge"):
            fail(path + ".insertion", "choose from_top or through_edge")
    if not isinstance(design["decoupling"], list):
        fail("decoupling", "supply a list")
        decoupling = []
    else:
        decoupling = design["decoupling"]
    for i, d in enumerate(decoupling):
        path = f"decoupling[{i}]"
        if not fields(
            d, ("capacitor", "device", "supply", "return", "max_distance_mm"), (), path
        ):
            continue
        for key in ("capacitor", "device"):
            if d[key] not in parts:
                fail(path + "." + key, "select a declared part")
        if d["capacitor"] == d["device"]:
            fail(path + ".capacitor", "capacitor and device must be different parts")
        if d["supply"] == d["return"] or set(d["supply"]) & set(d["return"]):
            fail(path + ".return", "supply and return must use different terminals")
        if not number(d["max_distance_mm"]) or d["max_distance_mm"] <= 0:
            fail(path + ".max_distance_mm", "supply a positive finite distance")
        else:
            from decimal import Decimal

            if (
                number(board["width_mm"])
                and number(board["height_mm"])
                and 0 < board["width_mm"] <= 670
                and 0 < board["height_mm"] <= 600
                and d["max_distance_mm"]
                >= math.hypot(board["width_mm"], board["height_mm"])
            ):
                fail(
                    path + ".max_distance_mm",
                    "distance reaches or exceeds the board diagonal; supply a meaningful locality limit",
                )
            nm = Decimal(str(d["max_distance_mm"])) * 1000000
            if nm != nm.to_integral_value():
                fail(
                    path + ".max_distance_mm",
                    "supply a distance representable in whole nanometres",
                )
        leg_nets = [{n for n, ts in nets.items() if set(d[key]) & set(ts)}
                    for key in ("supply", "return")]
        if leg_nets[0] & leg_nets[1]:
            fail(path + ".return", "supply and return must use different nets")
        for key in ("supply", "return"):
            pair = d[key]
            if not isinstance(pair, list) or len(pair) != 2:
                fail(path + "." + key, "supply [DEVICE.PIN, CAPACITOR.PIN]")
                continue
            parsed = [terminal(t, path + "." + key) for t in pair]
            if all(parsed) and [t[0] for t in parsed] != [d["device"], d["capacitor"]]:
                fail(
                    path + "." + key,
                    "supply the device terminal followed by the capacitor terminal",
                )
            ns = [next((n for n, ts in nets.items() if t in ts), None) for t in pair]
            if ns[0] is None or ns[0] != ns[1]:
                fail(
                    path + "." + key, "both endpoints must be on the same declared net"
                )
            for t in parsed:
                if t and pads.get(t[0], []).count(t[1]) > 1:
                    fail(
                        path + "." + key,
                        f"{t[0]}.{t[1]} has repeated lands; this compiler cannot yet derive the native land UUID; use an unambiguous package",
                    )
    functional = design["functional"]
    if not isinstance(functional, list) or not functional:
        fail("functional", "supply at least one witnessed functional requirement")
        functional = []
    witness_ids = set()
    for i, f in enumerate(functional):
        path = f"functional[{i}]"
        if not fields(f, ("text", "witnesses"), (), path):
            continue
        prose(f["text"], path + ".text")
        if not isinstance(f["witnesses"], list) or not f["witnesses"]:
            fail(
                path + ".witnesses",
                "add independent pin_net or element_between assertions",
            )
            continue
        for j, w in enumerate(f["witnesses"]):
            wp = path + f".witnesses[{j}]"
            if not isinstance(w, dict):
                fail(wp, "supply a witness object")
                continue
            if w.get("type") == "pin_net":
                if fields(w, ("id", "type", "terminal", "net"), (), wp):
                    terminal(w["terminal"], wp + ".terminal")
                    if w["net"] not in nets:
                        fail(wp + ".net", "select a declared net")
            elif w.get("type") == "element_between":
                if fields(
                    w, ("id", "type", "class", "nets", "value"), ("lib_id", "pins"), wp
                ):
                    if w["class"] in ("diode", "led", "polarized_capacitor"):
                        fail(
                            wp + ".class",
                            "element_between is undirected; use separate pin_net witnesses for both polarized pins",
                        )
                    if w["class"] not in (
                        "resistor",
                        "capacitor",
                        "inductor",
                        "ferrite",
                        "fuse",
                        "diode",
                        "led",
                        "polarized_capacitor",
                    ):
                        fail(
                            wp + ".class",
                            "choose a supported two-terminal topology class",
                        )
                    if (
                        not isinstance(w["nets"], list)
                        or len(w["nets"]) != 2
                        or len(set(w["nets"])) != 2
                        or any(n not in nets for n in w["nets"])
                    ):
                        fail(wp + ".nets", "supply two declared nets")
                    prose(w["value"], wp + ".value")
                    from .topology_rules import quantity

                    if quantity(w["value"]) is None:
                        fail(
                            wp + ".value", "supply a plain quantity such as 10k or 100n"
                        )
                    class_pins = w.get("pins", ["1", "2"])
                    if len(class_pins) != 2 or len(set(class_pins)) != 2:
                        fail(wp + ".pins", "supply two distinct pin numbers")
                    class_symbol = w.get("lib_id", CLASS_SYMBOLS.get(w["class"]))
                    canonical_class = device_class(class_symbol)
                    if device_polarized(class_symbol):
                        fail(wp + ".lib_id", f"{class_symbol} is polarized; use separate pin_net witnesses for both pins")
                    if canonical_class is not None and canonical_class != w["class"]:
                        fail(
                            wp + ".lib_id",
                            f"{class_symbol} is class {canonical_class}, not {w['class']}",
                        )
                    matches = [
                        r for r, p in parts.items() if p["lib_id"] == class_symbol
                    ]
                    if not matches:
                        fail(
                            wp + ".lib_id",
                            "select a symbol present in parts for this element class",
                        )
                    elif pins and any(
                        r in pins and set(class_pins) != pins[r] for r in matches
                    ):
                        fail(
                            wp + ".pins",
                            "the two class pins must match the captured symbol pin set",
                        )
            else:
                fail(wp + ".type", "use pin_net or element_between")
            wid = w.get("id")
            if (
                not isinstance(wid, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", wid)
                or wid in witness_ids
            ):
                fail(wp + ".id", "use a unique short rule identifier")
            else:
                witness_ids.add(wid)
    if not isinstance(design["bench"], list) or not design["bench"]:
        fail("bench", "list physical follow-ups that software cannot establish")
    else:
        for i, text in enumerate(design["bench"]):
            prose(text, f"bench[{i}]")
    placement = design.get("placement", {})
    if fields(placement, (), ("budget_s", "seeds", "steps_per_seed"), "placement"):
        for k, v in placement.items():
            if type(v) is not int or v < 1 or (k == "seeds" and v > 64):
                fail("placement." + k, "supply a positive integer (at most 64 seeds)")
    mechanics = design.get("mechanics")
    if mechanics is not None and fields(
        mechanics, ("access_owners",), ("packages",), "mechanics"
    ):
        if not isinstance(mechanics["access_owners"], list) or any(
            r not in locked for r in mechanics["access_owners"]
        ):
            fail("mechanics.access_owners", "list only declared fixed connectors")
        for ref, package in mechanics.get("packages", {}).items():
            if ref not in parts:
                fail("mechanics.packages." + ref, "select a declared part")
            if fields(
                package, ("size_mm", "offset_mm"), (), "mechanics.packages." + ref
            ):
                for key, length in (("size_mm", 3), ("offset_mm", 2)):
                    v = package[key]
                    if (
                        not isinstance(v, list)
                        or len(v) != length
                        or any(
                            not number(n) or (key == "size_mm" and n <= 0) for n in v
                        )
                    ):
                        fail(
                            "mechanics.packages." + ref + "." + key,
                            "supply finite dimensions in the footprint local frame",
                        )
    if captures is not None and not cad_only:
        from .part_record import prepare_record

        for ref, p in parts.items():
            try:
                root = Path(captures)
                ds = load(root / "datasheets/manifest.json")
                ids = {r["id"] for r in ds["parts"]}
                dsid = (
                    p["lcsc"]
                    if p["lcsc"] in ids
                    else next(
                        (
                            r
                            for r, candidate in sorted(design["parts"].items())
                            if candidate["lcsc"] == p["lcsc"] and r in ids
                        ),
                        ref,
                    )
                )
                prepare_record(
                    root,
                    p["lcsc"],
                    blob(root, "stock/catalog.json"),
                    blob(root, "datasheets/manifest.json"),
                    dsid,
                )
                from .design_capture import validate_supplier_capture

                validate_supplier_capture(root, p["lcsc"])
            except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                fail(
                    "parts." + ref + ".lcsc", f"run design capture for this part: {exc}"
                )
    if captures is not None and not cad_only:
        try:
            from .models3d import parse_stock_receipt
            from .design_capture import model_references

            root = Path(captures)
            receipt = parse_stock_receipt(
                (root / "lib3d/.agent-ee-models.json").read_bytes(), "design captures"
            )
            for rel in model_references(root / "cad"):
                entry = receipt["files"].get(rel)
                if (
                    not entry
                    or not entry.get("commit")
                    or digest(root / "lib3d" / rel) != entry["sha256"]
                ):
                    fail(
                        "parts.footprint",
                        f"{rel}: run design capture for a hash-verified model at a pinned upstream revision",
                    )
        except (OSError, ValueError) as exc:
            fail(
                "parts.footprint",
                f"run design capture to retain the stock-model receipt: {exc}",
            )
    if captures is not None and not cad_only:
        from .design_capture import stock_freshness

        try:
            stock_freshness(Path(captures))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            fail("captures.stock.catalog", str(exc))
    if errors:
        raise DesignError(errors)
    return assemblies


def blob(root, rel):
    return dict(path=rel, sha256=digest(Path(root) / rel))


def configure_parser(parser):
    sub = parser.add_subparsers(dest="design_command", required=True)
    from .design_generate import configure_parser as configure_generate

    configure_generate(sub)
    for command in ("compile", "capture"):
        p = sub.add_parser(command)
        p.add_argument("design")
        p.add_argument("--out", required=True)
        if command == "compile":
            p.add_argument("--captures", required=True)
        else:
            p.add_argument(
                "--kicad-root",
                default="/usr/share/kicad",
                help="local CAD libraries to retain with the capture",
            )


def execute(args):
    if args.design_command in ("generate", "benchmark"):
        from .design_generate import execute as generate_execute

        return generate_execute(args)
    design = load(args.design)
    if args.design_command == "compile":
        from .design_compile import compile_design

        compile_design(design, Path(args.captures), Path(args.out))
    else:
        from .design_capture import capture_design

        capture_design(design, Path(args.out), kicad_root=Path(args.kicad_root))
    print(args.out)
    return 0

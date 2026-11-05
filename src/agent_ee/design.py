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


# The packaged fabrication profile: its stackup gives the copper for current-derived widths.
FAB_PROFILE = Path(__file__).parent / "data/jlc-2layer-fab-profile.json"
# The allowed temperature rise for widths derived from declared currents (AGENTEE-582). The tool
# had no default; this value is agent-selected and recorded with every derived width.
CURRENT_RISE = dict(
    temperature_rise_c=10,
    selection="agent-selected",
    selected_by="agent-ee builder, AGENTEE-582, 2026-11-03",
    basis="A conservative rise for continuous-current traces; the value of the tool's existing "
    "net-constraint example (fixtures/net-constraints/rc.constraints.json, docs/net-constraints.md).",
    authority="Not an owner, datasheet or fabricator ruling.",
)


def current_minimum_mm(current_ma, copper):
    """IPC-2152 minimum width, in mm, for a continuous current on the thinnest of the copper layers.

    copper is a fab profile's stackup layer list. None outside the fit's range."""
    from .net_constraints import governing_copper, ipc2152_minimum

    governing, _ = governing_copper({"stackup": {"layers": copper}})
    _, width = ipc2152_minimum(current_ma, CURRENT_RISE["temperature_rise_c"], governing)
    return None if width is None else width / 1000


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


def native_net(design, net):
    return net if net == design["ground"] else "/" + net


def router_pair(name, names):
    """The (positive, negative) pair kicad-route forms from net ``name``, or None.

    With ``--diff-pairs auto`` the router pairs nets by name only (its
    src/kicad_route/diff_pairs.py ``detect``): a name ending in ``_P``/``_N`` or
    ``+``/``-`` is always a pair member, and the router refuses it when the mate
    is missing; a name ending in a bare ``P``/``N`` is a member when its mate is
    in ``names``. The native ``/`` prefix is the same on both members, so the
    rule gives the same answer on statement names.
    """
    if name.endswith(("_P", "_N")):
        return name[:-2] + "_P", name[:-2] + "_N"
    if name.endswith(("+", "-")):
        return name[:-1] + "+", name[:-1] + "-"
    if name.endswith(("P", "N")) and name[:-1] + ("N" if name[-1] == "P" else "P") in names:
        return name[:-1] + "P", name[:-1] + "N"
    return None


def pair_routing(design):
    """The board-router options that the declared differential pairs compile to.

    Without declared pairs this is empty: the board routes with no pair handling.
    The router applies one matching and one impedance setting to every pair it
    detects, so the first pair's intent stands for all (validate checks they agree).
    Impedance is nominal microstrip on the outer layer opposite ``plane``,
    referenced to the plane net.
    """
    pairs = design.get("diff_pairs") or []
    if not pairs:
        return {}
    router = {"diff_pairs": "auto"}
    first = pairs[0]
    if "intra_pair_match" in first:
        router["intra_pair_match"] = dict(first["intra_pair_match"])
    if "impedance" in first:
        plane = design["plane"]
        router["pair_layers"] = ["F.Cu" if plane["layer"] == "B.Cu" else "B.Cu"]
        router["pair_impedance"] = dict(
            target_ohm=first["impedance"]["target_ohm"],
            tolerance_percent=first["impedance"]["tolerance_percent"],
            reference_net=native_net(design, plane["net"]),
            max_uncoupled_mm=first["impedance"]["max_uncoupled_mm"],
        )
    return router


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


def datasheet_source(parts, ref, path, fail):
    """Check a declared datasheet source ``{url, sha256?}`` (its shape is already checked).

    Datasheets are captured per LCSC id, so parts that share one declare the same source.
    """
    from .datasheet_capture import validate_url

    source = parts[ref]["datasheet"]
    try:
        validate_url(source["url"])
    except ValueError:
        fail(path + ".url", "supply an http or https URL without credentials or a fragment")
    if "sha256" in source and not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
        fail(path + ".sha256", "supply the PDF's SHA-256 as 64 lowercase hexadecimal digits, or omit it")
    others = sorted(
        r for r, q in parts.items()
        if r != ref and isinstance(q, dict) and q.get("lcsc") == parts[ref]["lcsc"] and "datasheet" in q
    )
    if any(parts[r]["datasheet"] != source for r in others):
        fail(
            path,
            f"parts with lcsc {parts[ref]['lcsc']} share one datasheet; declare the same source as parts.{others[0]}.datasheet",
        )


def validate_thickness_fit(fit, path, fail):
    if not isinstance(fit, dict):
        fail(path, "supply thickness_fit as an object")
        return
    from decimal import Decimal
    from . import tht_thickness_fit
    from . import board_envelope as envelope

    def _to_decimal(val):
        if isinstance(val, float):
            return Decimal(str(val))
        if isinstance(val, dict):
            return {k: _to_decimal(v) for k, v in val.items()}
        return val

    try:
        norm = _to_decimal(fit)
        envelope.fields(norm, path, ['recommended_mm', 'recommended_source'],
                        ['hard_limits', 'fit_range', 'tail_length_mm', 'fillet_margin_mm'])
        recommended = envelope.length(norm['recommended_mm'], f'{path}.recommended_mm', positive=True)
        tht_thickness_fit.source(norm['recommended_source'], f'{path}.recommended_source')
        for key, both in (('hard_limits', False), ('fit_range', True)):
            if key in norm:
                bounds = tht_thickness_fit.limits(norm[key], f'{path}.{key}', both=both)
                if not bounds.get('min_mm', recommended) <= recommended <= bounds.get('max_mm', recommended):
                    fail(f'{path}.{key}', 'excludes the recommended nominal')
        if 'tail_length_mm' in norm:
            tail = envelope.length(norm['tail_length_mm'], f'{path}.tail_length_mm', positive=True)
            fillet = envelope.length(norm.get('fillet_margin_mm', Decimal('0.5')), f'{path}.fillet_margin_mm', positive=True)
            if tail <= fillet:
                fail(f'{path}.tail_length_mm', 'tail_length_mm must exceed fillet_margin_mm')
        elif 'fillet_margin_mm' in norm:
            fail(f'{path}.fillet_margin_mm', 'fillet_margin_mm requires tail_length_mm')
    except (ValueError, KeyError) as exc:
        msg = str(exc)
        if msg.startswith(path + ": "):
            msg = msg[len(path) + 2:]
        elif msg.startswith(path + "."):
            msg = msg[len(path) + 1:]
        fail(path, msg)


def validate_kelvin_senses(design, nets, parts, footprints, fail):
    senses = design.get("kelvin_senses", [])
    if not isinstance(senses, list):
        fail("kelvin_senses", "supply a list of Kelvin sense connection declarations")
        return
    plane_net = design.get("plane", {}).get("net")
    declared_names = set()
    term_to_net = {}
    for net_name, terms in nets.items():
        if isinstance(terms, list):
            for t in terms:
                if isinstance(t, str):
                    term_to_net[t] = net_name

    for i, sense in enumerate(senses):
        path = f"kelvin_senses[{i}]"
        if not isinstance(sense, dict):
            continue
        if "name" in sense:
            sname = sense["name"]
            if not isinstance(sname, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,47}", sname):
                fail(f"{path}.name", "use a short identifier beginning with a letter")
            elif sname in declared_names:
                fail(f"{path}.name", f"duplicate Kelvin sense name '{sname}'")
            else:
                declared_names.add(sname)

        shunt = sense.get("shunt")
        device = sense.get("device")
        if not isinstance(shunt, str) or shunt not in parts:
            fail(f"{path}.shunt", "select a declared part")
        if not isinstance(device, str) or device not in parts:
            fail(f"{path}.device", "select a declared part")
        if shunt and device and shunt == device:
            fail(path, "shunt and device must be distinct parts")

        pos = sense.get("positive")
        neg = sense.get("negative")
        pos_net = None
        neg_net = None

        if isinstance(pos, list) and len(pos) == 2 and all(isinstance(t, str) for t in pos):
            pos_nets = [term_to_net.get(t) for t in pos]
            if None in pos_nets:
                fail(f"{path}.positive", "both terminals must be assigned to declared nets")
            elif pos_nets[0] != pos_nets[1]:
                fail(f"{path}.positive", f"both terminals must be on the same net, got '{pos_nets[0]}' and '{pos_nets[1]}'")
            else:
                pos_net = pos_nets[0]

        if isinstance(neg, list) and len(neg) == 2 and all(isinstance(t, str) for t in neg):
            neg_nets = [term_to_net.get(t) for t in neg]
            if None in neg_nets:
                fail(f"{path}.negative", "both terminals must be assigned to declared nets")
            elif neg_nets[0] != neg_nets[1]:
                fail(f"{path}.negative", f"both terminals must be on the same net, got '{neg_nets[0]}' and '{neg_nets[1]}'")
            else:
                neg_net = neg_nets[0]

        if pos_net and neg_net and pos_net == neg_net:
            fail(path, f"positive and negative sense connections must be on different nets, got '{pos_net}'")

        for net_val, label in ((pos_net, "positive"), (neg_net, "negative")):
            if net_val and plane_net and net_val == plane_net:
                fail(f"{path}.{label}", f"sensed net '{net_val}' cannot be assigned to plane.net; copper plane fills bypass the sensed shunt")

        if "max_distance_mm" in sense:
            dist = sense["max_distance_mm"]
            if isinstance(dist, bool) or not isinstance(dist, (int, float)) or dist <= 0:
                fail(f"{path}.max_distance_mm", "must be a positive number")

        if "max_loop_mm" in sense:
            loop = sense["max_loop_mm"]
            if isinstance(loop, bool) or not isinstance(loop, (int, float)) or loop <= 0:
                fail(f"{path}.max_loop_mm", "must be a positive number")

        if "coupled" in sense and not isinstance(sense["coupled"], bool):
            fail(f"{path}.coupled", "must be Boolean")


PAIR_FIELDS = ("intra_pair_match", "impedance")


def validate_diff_pairs(design, nets, fail):
    """Declared differential pairs, and the router's name convention when any are declared.

    kicad-route understands pairs only through ``--diff-pairs auto``, which pairs
    nets by name (router_pair). Declared intent and router behaviour agree only
    when every declared pair is named STEM_P/STEM_N and no other net has a name
    the router pairs. Names are never changed: a mismatch is a refusal. Without
    declared pairs the board routes with no pair handling and names are free.
    """
    pairs = design.get("diff_pairs", [])
    if not isinstance(pairs, list):
        fail("diff_pairs", "supply a list of {positive, negative} pairs")
        return
    declared, valid = {}, []
    for i, pair in enumerate(pairs):
        path = f"diff_pairs[{i}]"
        if not isinstance(pair, dict) or not {"positive", "negative"} <= pair.keys():
            continue  # The shape check names the missing field.
        refused = False
        for key in ("positive", "negative"):
            net = pair[key]
            if net not in nets:
                fail(f"{path}.{key}", "select a declared net")
                refused = True
            elif net in declared:
                fail(f"{path}.{key}", f"{net} is already in diff_pairs[{declared[net]}]; a net belongs to one pair")
                refused = True
            else:
                declared[net] = i
        positive, negative = pair["positive"], pair["negative"]
        if not refused and not (positive.endswith("_P") and negative == positive[:-2] + "_N"):
            from os.path import commonprefix

            stem = positive[:-2] if positive.endswith("_P") else (
                commonprefix([positive, negative]).rstrip("_") or positive)
            fail(path, f"the router pairs nets only by name, as STEM_P and STEM_N: name this pair "
                 f"{stem}_P (positive) and {stem}_N (negative) in nets, diff_pairs and every witness; "
                 "names are not changed automatically")
            refused = True
        if not refused:
            valid.append((i, pair))
    if not pairs:
        return
    for net in sorted(nets):
        mate = router_pair(net, nets)
        if net in declared or mate is None:
            continue
        fail("nets." + net, f"with diff_pairs declared, the router treats {net} as a differential-pair member "
             f"({mate[0]} with {mate[1]}) by its name; declare that pair in diff_pairs, or rename {net} so it "
             "does not end in _P, _N, + or -, nor in P or N beside a net with the other letter")
    for key in PAIR_FIELDS:
        values = [pair.get(key) for _, pair in valid]
        for (i, pair), value in zip(valid, values):
            if value != values[0]:
                fail(f"diff_pairs[{i}].{key}", f"the router applies one {key} setting to every pair: give every "
                     f"declared pair the same {key}, or omit it on all of them; different settings per pair "
                     "are not supported yet [tool_gap]")
                break
    if len(valid) != len(pairs) or any(pair.get(key) != valid[0][1].get(key) for _, pair in valid for key in PAIR_FIELDS):
        return
    from .routing_adapter import validate_router_options

    router = pair_routing(design)
    for key, options in (("intra_pair_match", ("intra_pair_match",)),
                         ("impedance", ("pair_layers", "pair_impedance"))):
        if key in valid[0][1]:
            try:
                validate_router_options({"diff_pairs": "auto", **{k: router[k] for k in options}})
            except (ValueError, TypeError) as exc:
                fail(f"diff_pairs[0].{key}", str(exc))


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
        ("placement", "mechanics", "markings", "diff_pairs", "currents", "kelvin_senses"),
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
                or parsed.text(parsed.prose[0]["byte_start"], parsed.prose[0]["byte_end"]).strip() != design["intent"].strip()):
            fail("intent", "supply plain paragraph text; Markdown structure changes the generated spec")
    except SpecRefused as exc:
        fail("intent", "supply plain paragraph text; " + "; ".join(f["message"] for f in exc.findings))
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
    from .pad_correspondence import validate_declaration
    for ref, part in parts.items():
        if 'cpl_registration' in part:
            try:
                validate_declaration(part['cpl_registration'])
            except ValueError as exc:
                fail('parts.' + ref + '.cpl_registration', str(exc))
        if 'thickness_fit' in part:
            validate_thickness_fit(part['thickness_fit'], 'parts.' + ref + '.thickness_fit', fail)
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
            p, ("lcsc", "value", "lib_id", "footprint", "group"), ("connector", "datasheet", "cpl_registration", "thickness_fit"), path
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
        if "datasheet" in p:
            datasheet_source(parts, ref, path + ".datasheet", fail)
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

    from .board_text import validate_items, default_limits
    try:
        markings = validate_items(design.get("markings", []), parts, pins or None)
        floors = default_limits()
        for i, marking in enumerate(markings):
            for key, floor in (("height_mm", "min_silk_text_height_mm"), ("stroke_mm", "min_silk_line_width_mm")):
                if marking.get(key, floors[floor]) < floors[floor]:
                    fail(f"markings[{i}].{key}", f"must be at least {floors[floor]} mm from the fabrication profile")
            if "at_mm" in marking["anchor"]:
                x, y = marking["anchor"]["at_mm"]
                if not (0 <= x <= design["board"]["width_mm"] and 0 <= y <= design["board"]["height_mm"]):
                    fail(f"markings[{i}].anchor.at_mm", "must be within the declared board")
            elif captures is not None and "pin" in marking["anchor"]:
                ref, pin = marking["anchor"]["reference"], marking["anchor"]["pin"]
                if ref in pads and pads[ref].count(pin) != 1:
                    fail(f"markings[{i}].anchor.pin", "must select exactly one physical pad")
    except (ValueError, TypeError) as exc:
        fail("markings", str(exc))

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
            from decimal import Decimal

            from .design_geometry import EDGE_RUNS, connector_edge_gap, connector_edge_span

            try:
                gap = connector_edge_gap(footprints[ref], c)
                if gap > 1:
                    fail(
                        path + ".inset_mm",
                        f"rotated courtyard is {gap:g} mm from the edge; bring it within 1 mm",
                    )
                start, end = connector_edge_span(footprints[ref], c)
                length = board.get("width_mm" if c["edge"] in ("top", "bottom") else "height_mm")
                if number(c["position_mm"]) and number(length):
                    position, length = Decimal(str(c["position_mm"])), Decimal(str(length))
                    first, last, direction, _ = EDGE_RUNS[c["edge"]]

                    def mm(value):
                        return f"{float(value) + 0.0:g}"

                    # A position off the edge is refused above; this names the courtyard's overrun.
                    if 0 <= position <= length and (position + start < 0 or position + end > length):
                        past = (
                            f"{mm(-(position + start))} mm past the {first} corner"
                            if position + start < 0
                            else f"{mm(position + end - length)} mm past the {last} corner"
                        )
                        fits = (
                            f"use position_mm from {mm(-start)} to {mm(length - end)}"
                            if -start <= length - end
                            else f"its {mm(end - start)} mm along the edge do not fit the {mm(length)} mm edge"
                        )
                        fail(
                            path + ".position_mm",
                            f"{ref}: rotated courtyard runs {past}. The {c['edge']} edge starts at the "
                            f"{first} corner and runs {direction}, and position_mm is measured from that "
                            f"corner; {fits}",
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
        refused_before = len(errors)
        if not fields(
            d, ("capacitor", "device", "supply", "return", "max_distance_mm"), ("max_loop_mm",), path
        ):
            continue
        for key in ("capacitor", "device"):
            if d[key] not in parts:
                fail(path + "." + key, "select a declared part")
        if d["capacitor"] == d["device"]:
            fail(path + ".capacitor", "capacitor and device must be different parts")
        if "max_loop_mm" in d and (not number(d["max_loop_mm"]) or d["max_loop_mm"] <= 0):
            fail(path + ".max_loop_mm", "supply a positive finite copper loop length")
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
        legs = [d[key] for key in ("supply", "return")]
        # The two-part bound assumes what the placement engine enforces between these parts:
        # their courtyard bounding boxes may not overlap. The engine scores a pair only when at
        # least one part is movable (crates/placement-engine/src/job.rs:1088-1091 `interacts`,
        # used at 1552, 1578 and 1732-1745), and then any body overlap is hard (job.rs:1104-1110).
        # A body is the occupancy rectangle (job.rs:1001-1003), which for a footprint with a
        # courtyard is the bounding box of its courtyard polygons (placement_job.classify_footprint).
        # Two declared connectors are both fixed, so nothing keeps their courtyard boxes apart and
        # the bound does not apply to them. A relation already refused above gets no bound either.
        if (
            len(errors) == refused_before
            and not (d["device"] in locked and d["capacitor"] in locked)
            and d["device"] in footprints
            and d["capacitor"] in footprints
            and all(
                isinstance(leg, list)
                and len(leg) == 2
                and all(isinstance(t, str) and "." in t for t in leg)
                and [t.split(".", 1)[0] for t in leg] == [d["device"], d["capacitor"]]
                for leg in legs
            )
        ):
            from .design_geometry import decoupling_reach

            reach = decoupling_reach(
                footprints[d["device"]],
                footprints[d["capacitor"]],
                *[tuple(t.split(".", 1)[1] for t in leg) for leg in legs],
            )
            # 1 nm absorbs floating-point error; the bound itself is never rounded down.
            if reach is not None and reach > d["max_distance_mm"] + 1e-6:
                fail(
                    path + ".max_distance_mm",
                    f"{'-'.join(legs[0])} and {'-'.join(legs[1])} cannot both be within "
                    f"{d['max_distance_mm']:g} mm: with the {d['device']} and {d['capacitor']} "
                    f"courtyards apart, in every quarter-turn rotation, the longer leg is at least "
                    f"{reach:.3f} mm. Raise max_distance_mm to {math.ceil(reach * 100 - 1e-6) / 100:g} mm "
                    "or more, or choose pads or packages that let the capacitor sit closer",
                )
    validate_diff_pairs(design, nets, fail)
    validate_kelvin_senses(design, nets, parts, footprints, fail)
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
    current_problems(design, nets, footprints, decoupling, fields, fail)
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
                record = prepare_record(
                    root,
                    p["lcsc"],
                    blob(root, "stock/catalog.json"),
                    blob(root, "datasheets/manifest.json"),
                    dsid,
                )
                source = p.get("datasheet")
                if source is not None and (
                    record["datasheet_url"] != source["url"]
                    or source.get("sha256") not in (None, record["artifact"]["sha256"])
                ):
                    fail(
                        "parts." + ref + ".datasheet",
                        f"the captured datasheet ({record['datasheet_url']}, sha256 {record['artifact']['sha256']}) "
                        "is not this declared source; run design capture again into a new directory",
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
                    or not (entry.get("commit") or entry.get('source') == 'declared-url')
                    or digest(root / "lib3d" / rel) != entry["sha256"]
                ):
                    fail(
                        "parts.footprint",
                        f"{rel}: run design capture for a model bound to a source URL and verified content digest",
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


def current_problems(design, nets, footprints, decoupling, fields, fail):
    """Declared continuous currents (AGENTEE-582): each names a declared net and a current in mA.

    With captured footprints, a derived width must fit the generated local copper of a
    decoupling leg on its net, which is never wider than its narrower land; otherwise the design
    is refused here, before placement. Whether a routed track reaches each land is not decided
    here: current_reach_advisories only advises, and the final copper-width/1 check decides."""
    currents = design.get("currents", {})
    if not isinstance(currents, dict):
        fail("currents", "supply an object mapping declared net names to {current_ma}")
        return
    profile = load(FAB_PROFILE)
    copper = profile["stackup"]["layers"]
    pairs = design.get("diff_pairs") if isinstance(design.get("diff_pairs"), list) else []
    members = {pair[key]: i for i, pair in enumerate(pairs) if isinstance(pair, dict)
               for key in ("positive", "negative") if isinstance(pair.get(key), str)}
    widths = {}
    for net, spec in currents.items():
        path = "currents." + net
        if net not in nets:
            fail(path, "declare a current only for a net in nets")
            continue
        if net in members:
            # The router draws a declared pair at its class diff-pair width (design_compile.envelope).
            fail(path, f"{net} is in diff_pairs[{members[net]}], which the router draws at the pair's "
                 "diff-pair width; a current-derived width is not applied to a pair yet, so declare the "
                 "current on a net outside the pair or remove it [tool_gap]")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,55}", net):
            fail(path, "name a net with a declared current with at most 56 letters, digits and underscores; "
                 "the router matches netclass patterns as globs and cannot bind an escaped +, . or - [tool_gap]")
        if fields(spec, ("current_ma",), (), path):
            value = spec["current_ma"]
            if not number(value) or not 0 < value <= 30000:
                fail(path + ".current_ma", "supply the continuous current in mA as a positive number "
                     "of at most 30000, the range of the IPC-2152 fit")
            else:
                widths[net] = current_minimum_mm(value, copper)
    basis = current_basis(profile)
    from .design_geometry import land_width

    for i, d in enumerate(decoupling):
        for key in ("supply", "return"):
            leg = d.get(key) if isinstance(d, dict) else None
            if not (isinstance(leg, list) and len(leg) == 2
                    and all(isinstance(t, str) and "." in t and t.split(".", 1)[0] in footprints for t in leg)):
                continue
            net = next((n for n, terms in nets.items() if leg[0] in terms), None)
            lands = [land_width(footprints[t.split(".", 1)[0]], t.split(".", 1)[1]) for t in leg]
            if net in widths and None not in lands and min(lands) + 1e-6 < widths[net]:
                # local_decoupling._leg draws each leg no wider than its narrower land.
                fail(f"decoupling[{i}].{key}",
                     f"generated local copper on {net} is at most {min(lands):g} mm wide (the narrower land of "
                     f"{leg[0]} and {leg[1]}), below the {widths[net]:g} mm its declared "
                     f"{currents[net]['current_ma']:g} mA needs ({basis}); select parts whose lands are at "
                     "least that wide, or lower the declared current if the requirement allows it")


def current_basis(profile):
    return (f"IPC-2152 generic-chart fit, {CURRENT_RISE['temperature_rise_c']} C agent-selected temperature rise, "
            f"thinnest copper of fab profile {profile['name']}")


def current_reach_advisories(design, footprints):
    """Advisories, never refusals: lands that no straight track of a net's derived width reaches.

    A straight track from the land centre along each of the router's eight directions must keep
    the fab profile's minimum clearance from the part's lands on other nets. A route with bends
    can still reach a land that every straight track misses, so this names a likely difficulty
    only; the final copper-width/1 check on the routed board decides. footprints maps a
    reference to its parsed footprint; design is a validated statement."""
    from .design_geometry import track_access_width

    profile = load(FAB_PROFILE)
    clearance = next(limit["value"] for limit in profile["limits"]
                     if limit.get("key") == "min_clearance_mm" and limit.get("tier") == "minimum")
    advisories = []
    for net, spec in sorted(design.get("currents", {}).items()):
        width = current_minimum_mm(spec["current_ma"], profile["stackup"]["layers"])
        on_net = {}
        for term in design["nets"][net]:
            ref, pin = term.split(".", 1)
            on_net.setdefault(ref, set()).add(pin)
        for ref in sorted(set(on_net) & set(footprints)):
            for pin in sorted(on_net[ref]):
                access = track_access_width(footprints[ref], pin, on_net[ref], clearance)
                # 1 nm absorbs floating-point error; the reported width is rounded down to 1 um.
                if access is not None and width > access + 1e-6:
                    advisories.append(
                        f"currents.{net}: advisory: no straight {width:g} mm track ({current_basis(profile)}) "
                        f"along the router's eight directions reaches {ref} pad {pin} "
                        f"({design['parts'][ref]['footprint']}) and keeps the {clearance:g} mm fabrication "
                        f"clearance from the part's lands on other nets (widest straight approach "
                        f"{math.floor(access * 1000 + 1e-6) / 1000:g} mm); a route with bends may still fit, and "
                        "the final copper-width/1 check decides")
    return advisories


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
            p.add_argument('--model-sources', help='JSON map of model paths to explicit HTTPS URLs and SHA-256 digests')
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

        capture_design(design, Path(args.out), kicad_root=Path(args.kicad_root),
                       model_sources=load(args.model_sources) if args.model_sources else None)
    print(args.out)
    return 0

"""Deterministic bundle emission; existing producers own their record semantics."""

import hashlib
import json
import shutil
import tempfile
import uuid
from pathlib import Path

from .design import (
    CLASS_SYMBOLS,
    DesignError,
    blob,
    footprint_path,
    load,
    validate,
    virtual_parts,
)
from .storage import digest, identity, write_json
from .symbol_library import children, parse_sexpr

DATA = Path(__file__).parent / "data"
CODES = {
    "Design intent": "INTENT",
    "Native verification": "NATIVE",
    "Electrical function": "FUNC",
    "Placement and mechanics": "MECH",
    "Manufacturing": "DELIV",
    "Physical follow-up": "BENCH",
}
NATIVE = [
    "Native ERC shall report no electrical violations after schematic emission.",
    "Final native DRC shall report no errors and no unconnected items after zone refill.",
    "The emitted native netlist shall match the selected circuit topology exactly.",
    "Every physical component shall match the selected part, symbol, footprint and population declaration.",
    "Every populated component shall carry purchasing properties and current JLCPCB stock evidence.",
]


def native_net(design, net):
    return net if net == design["ground"] else "/" + net


def spec(design):
    sections = {
        "Design intent": [design["intent"]],
        "Native verification": NATIVE,
        "Electrical function": [f["text"] for f in design["functional"]],
        "Placement and mechanics": [
            f"The board shall be {design['board']['width_mm']} by {design['board']['height_mm']} mm, two copper layers and nominal {design['board']['thickness_mm']} mm thick.",
            "All declared connectors shall occupy their fixed edge positions.",
            "Declared decoupling supply and return pads shall meet their maximum distance requirements.",
            f"Courtyards shall not overlap and components shall fit the outline and {design['board']['max_component_height_mm']} mm height envelope.",
        ],
        "Manufacturing": [
            "The final native design shall produce consistent copper, mask, silk, outline, drill, BOM and placement files; all declared checks must pass."
        ],
        "Physical follow-up": design["bench"],
    }
    # Empty optional connector/locality clauses are omitted rather than given empty evidence.
    if not design["decoupling"]:
        sections["Placement and mechanics"][2] = (
            "The declared plane shall be present in the final board and refill without unconnected items."
        )
    if not design["connectors"]:
        sections["Placement and mechanics"][1] = (
            "The board shall retain the declared nominal stackup."
        )
    return "".join(
        "# "
        + title
        + "\n\n"
        + (
            "\n\n".join(rows)
            if title == "Design intent"
            else "\n".join("- " + r for r in rows)
        )
        + "\n\n"
        for title, rows in sections.items()
    )


def cad_records(design, captures, root, assemblies):
    from .symbol_capture import capture_libraries

    selected = [
        p["lib_id"]
        for p in [*design["parts"].values(), *virtual_parts(design).values()]
    ]
    sources = {
        lib: (captures / "cad" / (lib + ".kicad_sym")).read_bytes()
        for lib in {s.split(":")[0] for s in selected}
    }
    libraries, captured, _ = capture_libraries(selected, sources)
    for rel, raw in captured.items():
        (root / rel).write_bytes(raw)
    members = {}
    for p in design["parts"].values():
        rel = footprint_path(p)
        target = root / rel
        target.parent.mkdir(exist_ok=True)
        target.write_bytes((captures / "cad" / rel).read_bytes())
        members.setdefault(p["footprint"].split(":")[0], {})[rel] = digest(target)
    cad = dict(
        schema="agent-ee.footprint-libraries/1",
        complete=True,
        libraries=[
            dict(
                name=lib,
                members=[dict(path=p, sha256=h) for p, h in sorted(rows.items())],
            )
            for lib, rows in sorted(members.items())
        ],
    )
    write_json(root / "cad.json", cad)
    write_json(
        root / "assignments.json",
        dict(
            schema="agent-ee.footprint-assignments-input/1",
            libraries=libraries,
            components=[
                dict(
                    reference=r,
                    lib_id=p["lib_id"],
                    footprint=p["footprint"],
                    assembly=assemblies[r],
                    connector=p.get("connector"),
                )
                for r, p in sorted(design["parts"].items())
            ],
        ),
    )
    closure = [
        dict(path=p, sha256=h) for rows in members.values() for p, h in rows.items()
    ] + [dict(path=l["path"], sha256=l["sha256"]) for l in libraries]
    return libraries, identity(sorted(closure, key=lambda m: m["path"]))


def selected_parts(design, root, record, members_sha):
    from .part_record import prepare_record

    parts, decisions = [], {}
    for ref, p in sorted(design["parts"].items()):
        ids = {r["id"] for r in load(root / "datasheets/manifest.json")["parts"]}
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
        rec = prepare_record(
            root,
            p["lcsc"],
            blob(root, "stock/catalog.json"),
            blob(root, "datasheets/manifest.json"),
            dsid,
        )
        (root / "named").mkdir(exist_ok=True)
        write_json(root / f"named/{ref}.json", rec)
        decisions[ref] = (
            f"Design-declared selection of {p['lcsc']}; captured identity does not establish suitability."
        )
        parts.append(
            dict(
                id=ref,
                role=p["group"] + "/" + ref,
                required=True,
                instances=[dict(reference=ref, sheet="/")],
                selection_id="selected-" + ref,
                lib_id=p["lib_id"],
                footprint=p["footprint"],
                **{
                    k: rec[k]
                    for k in ("lcsc", "mpn", "manufacturer", "stock", "package")
                },
                datasheet=dict(
                    status="captured",
                    required=True,
                    artifact=rec["artifact"],
                    provenance=dict(
                        rec["datasheets"],
                        locator="parts[id=" + rec["datasheet_id"] + "]",
                    ),
                ),
                selection=dict(
                    kind="named",
                    lcsc=rec["lcsc"],
                    part_record=f"named/{ref}.json",
                    part_record_sha256=digest(root / f"named/{ref}.json"),
                    decision_ref=dict(
                        path="decisions.json",
                        sha256="",
                        locator="parts[id=" + ref + "]",
                    ),
                ),
            )
        )
    write_json(root / "decisions.json", decisions)
    for p in parts:
        p["selection"]["decision_ref"]["sha256"] = digest(root / "decisions.json")
    declaration = next(
        r
        for r in record["requirements"]
        if r["id"] == design["requirement_prefix"] + "-NATIVE-4"
    )
    write_json(
        root / "parts.json",
        dict(
            schema="agent-ee.parts/1",
            catalog=None,
            selection=None,
            population="native-dnp-optional-only/1",
            property_fields=dict(lcsc="LCSC", mpn="MPN", manufacturer="Manufacturer"),
            declaration_source=dict(
                locator=json.dumps(declaration["locator"], sort_keys=True),
                path="requirements-map/spec.md",
                sha256=digest(root / "spec.md"),
            ),
            cad=dict(
                manifest=blob(root, "cad.json"),
                assignments=blob(root, "assignments.json"),
                members_sha256=members_sha,
            ),
            parts=parts,
        ),
    )


def circuit(design, root, libraries):
    parts, virtual = design["parts"], virtual_parts(design)
    components = [
        dict(
            id=r,
            reference=r,
            lib_id=p["lib_id"],
            value=p["value"],
            footprint="",
            units=[1],
        )
        for r, p in sorted(parts.items())
    ]
    components += [
        dict(
            id=r,
            reference=p["reference"],
            lib_id=p["lib_id"],
            value=p["value"],
            footprint="",
            units=[1],
        )
        for r, p in sorted(virtual.items())
    ]

    def term(t):
        r, pin = t.split(".", 1)
        return dict(component=r, pin=dict(number=pin))

    nets = {
        name: [term(t) for t in sorted(ts)]
        for name, ts in sorted(design["nets"].items())
    }
    nets[design["ground"]].append(term("PWR01.1"))
    for index, net in enumerate(sorted(design["power_flags"]), 1):
        nets[net].append(term(f"FLG{index:02}.1"))
    write_json(
        root / "circuit.json",
        dict(
            schema="agent-ee.connectivity-intent/1",
            libraries=libraries,
            components=components,
            nets=[dict(id=n, name=n, terminals=ts) for n, ts in nets.items()],
            no_connects=[term(t) for t in sorted(design["no_connects"])],
        ),
    )
    write_json(
        root / "layout.json",
        dict(
            schema="agent-ee.schematic-layout/1",
            project=design["name"],
            presentation="functional",
            namespace=str(
                uuid.uuid5(uuid.NAMESPACE_URL, "agent-ee-design/" + design["name"])
            ),
            groups=[
                dict(
                    id=g["name"],
                    title=g["title"],
                    components=[
                        r
                        for r, p in sorted({**parts, **virtual}.items())
                        if p["group"] == g["name"]
                    ],
                )
                for g in design["groups"]
            ],
        ),
    )
    write_json(
        root / "ratings.json",
        dict(
            schema="agent-ee.passive-rating-sources/1",
            catalog=True,
            extractor=None,
            facts=None,
        ),
    )


def envelope(design, root):
    profile = load(DATA / "jlc-2layer-fab-profile.json")
    facts = (DATA / "jlc-2layer-selected-facts.txt").read_bytes()
    (root / "fab-sources").mkdir()
    (root / "fab-sources/selected-facts.txt").write_bytes(facts)
    for item in [
        profile["stackup"],
        profile["finished_thickness_tolerance"],
        *profile["limits"],
    ]:
        item["source"]["sha256"] = digest(root / "fab-sources/selected-facts.txt")
    for limit in profile["limits"]:
        limit["requirement"] = f"R-envelope-{design['name']}-fab-profile"
    write_json(root / "fab-profile.json", profile)
    b = design["board"]
    x, y = 100, 100
    w, h = b["width_mm"], b["height_mm"]
    write_json(
        root / "envelope.json",
        dict(
            schema="agent-ee.envelope/1",
            id=design["name"],
            layer_count=2,
            fab_profile=dict(
                name=profile["name"], sha256=digest(root / "fab-profile.json")
            ),
            keepouts=[],
            mounting_holes=[],
            max_component_height_mm=b["max_component_height_mm"],
            outline=dict(
                edges=[
                    dict(name=n, start=a, end=z)
                    for n, a, z in [
                        ("top", [x, y], [x + w, y]),
                        ("right", [x + w, y], [x + w, y + h]),
                        ("bottom", [x + w, y + h], [x, y + h]),
                        ("left", [x, y + h], [x, y]),
                    ]
                ]
            ),
            stackup=dict(
                thickness_mm=b["thickness_mm"],
                thickness_basis="nominal-design",
                layers=[
                    dict(
                        kind="copper",
                        name="F.Cu",
                        thickness_mm=0.035,
                        copper_weight_oz=1,
                    ),
                    dict(
                        kind="dielectric",
                        name="core",
                        thickness_mm=round(b["thickness_mm"] - 0.07, 6),
                    ),
                    dict(
                        kind="copper",
                        name="B.Cu",
                        thickness_mm=0.035,
                        copper_weight_oz=1,
                    ),
                ],
            ),
            fixed_footprints=[
                dict(c, side="front", tolerance_mm=0) for c in design["connectors"]
            ],
        ),
    )
    from .scaffold import produced_rules

    files = {
        str(p.relative_to(root)): digest(p) for p in root.rglob("*") if p.is_file()
    }
    _, _, rules, netclass, _ = produced_rules(
        dict(fab_profile="fab-profile.json"), files, root
    )
    from . import native_geometry_defaults

    # Keep the sourced fab minima, and route at least as conservatively as the
    # pinned native defaults. A minimum pad/drill pair alone can violate KiCad's
    # unchanged annular-width default.
    routing, routing_sources = {}, {}
    for item in netclass:
        field = item["setting"].rsplit(".", 1)[1]
        native_value, citation = native_geometry_defaults.DEFAULTS[item["setting"]]
        routing[field] = max(item["value"], native_value)
        routing_sources[field] = dict(
            fab=item,
            native_default=native_value,
            native_citation=citation,
            selected=routing[field],
        )
    annular, annular_citation = native_geometry_defaults.DEFAULTS[
        "board.design_settings.rules.min_via_annular_width"
    ]
    routing["via_diameter"] = max(
        routing["via_diameter"], round(routing["via_drill"] + 2 * annular, 6)
    )
    write_json(
        root / "project-rule-derivation.json",
        dict(
            schema="agent-ee.design-project-rules/1",
            fab_profile=blob(root, "fab-profile.json"),
            enforced=rules,
            routing=routing_sources,
            native_defaults=dict(
                version=native_geometry_defaults.VERSION,
                module_sha256=digest(native_geometry_defaults.__file__),
                annular_width=annular,
                citation=annular_citation,
            ),
            selected_routing=routing,
        ),
    )
    project = dict(
        board=dict(
            design_settings=dict(
                rules={
                    **{i["setting"].rsplit(".", 1)[1]: i["value"] for i in rules},
                    "min_via_annular_width": annular,
                }
            )
        ),
        meta=dict(filename=design["name"] + ".kicad_pro", version=1),
        net_settings=dict(
            classes=[
                dict(
                    name="Default",
                    **routing,
                )
            ],
            meta=dict(version=3),
        ),
    )
    (root / "project").mkdir()
    write_json(root / f"project/{design['name']}.kicad_pro", project)
    return project


def placement(design, root):
    write_json(
        root / "planes.json",
        dict(
            schema="agent-ee.plane-plan/1",
            zones=[
                dict(
                    net=native_net(design, design["plane"]["net"]),
                    layer=design["plane"]["layer"],
                    outline="board",
                    clearance_mm=0.2,
                    min_width_mm=0.2,
                    thermal=dict(
                        connection="thermal", gap_mm=0.25, spoke_width_mm=0.25
                    ),
                    priority=0,
                    island_removal="always",
                )
            ],
        ),
    )
    write_json(
        root / "groups.json",
        dict(
            groups=[
                dict(
                    name=g["name"],
                    anchor=g["anchor"],
                    refs=[
                        r
                        for r, p in sorted(design["parts"].items())
                        if p["group"] == g["name"]
                    ],
                )
                for g in design["groups"]
            ]
        ),
    )
    obligations, proximity = [], []
    text = (root / "spec.md").read_text()
    for i, d in enumerate(design["decoupling"]):

        def endpoint(t):
            ref, pin = t.split(".", 1)
            net = next(n for n, terms in design["nets"].items() if t in terms)
            return dict(reference=ref, pad=pin, net=native_net(design, net))

        quote = "Declared decoupling supply and return pads shall meet their maximum distance requirements."
        row = dict(id="decoupling-" + str(i + 1), metric="pad-centre-to-centre")
        for key in ("supply", "return"):
            row[key] = dict(
                citation=quote,
                max_distance_nm=round(d["max_distance_mm"] * 1000000),
                provenance=dict(
                    basis="Design-declared measurement requirement",
                    **{"class": "engineering"},
                    citation=dict(
                        kind="captured_source",
                        locator="Placement and mechanics / item 3",
                        path="spec.md",
                        quote=quote,
                        sha256=digest(root / "spec.md"),
                    ),
                ),
                **{"from": endpoint(d[key][0]), "to": endpoint(d[key][1])},
            )
        obligations.append(row)
        proximity.append(
            dict(
                ref=d["capacitor"],
                near=d["device"],
                max_mm=d["max_distance_mm"],
                pads={
                    d["device"]: [d["supply"][0].split(".", 1)[1]],
                    d["capacitor"]: [d["supply"][1].split(".", 1)[1]],
                },
                basis="Design-declared supply pad distance; final acceptance required",
            )
        )
    locality = dict(
        schema="agent-ee.electrical-locality/1",
        source_records={"spec.md": text},
        obligations=obligations,
    )
    if obligations:
        write_json(root / "electrical-locality.json", locality)
    write_json(
        root / "placement.json",
        dict(
            edge_connectors=[
                dict(
                    ref=c["reference"],
                    edge={
                        "left": "west",
                        "right": "east",
                        "top": "north",
                        "bottom": "south",
                    }[c["edge"]],
                )
                for c in design["connectors"]
            ],
            fixed_poses=[],
            proximity=proximity,
            **({"electrical_locality": locality} if obligations else {}),
        ),
    )


def cpl_policy(design, root):
    from .land_pattern import library_footprint
    from .easyeda_footprint import parse_package
    from . import cpl_rotation as c

    cad = load(root / "cad.json")
    captures = {
        m["path"]: (root / m["path"]).read_bytes()
        for lib in cad["libraries"]
        for m in lib["members"]
    }
    rules, suppliers = {}, []
    (root / "cpl").mkdir()
    shutil.copytree(root / "supplier", root / "cpl/supplier")
    shutil.rmtree(root / "supplier")
    for ref, part in sorted(design["parts"].items()):
        lcsc = part["lcsc"]
        pr = root / f"cpl/supplier/{lcsc}.provenance.json"
        provenance = load(pr)
        provenance["path"] = f"cpl/supplier/{lcsc}.raweasy.json"
        if "response" in provenance:
            provenance["response"]["path"] = f"cpl/supplier/{lcsc}.full.json"
        write_json(pr, provenance)
        suppliers.append(f"cpl/supplier/{lcsc}.provenance.json")
        native = library_footprint(cad, captures, part["footprint"])
        supplier = parse_package((root / provenance["path"]).read_bytes(), lcsc, 200)
        board_side, _, why = c.classify_pin1(c._top_view(native["pattern"]))
        supplier_side, _, swhy = c.classify_pin1(c._top_view(supplier["pattern"]))
        if not board_side or not supplier_side:
            raise DesignError(
                [
                    f"parts.{ref}.footprint: cannot derive CPL pin-1 orientation ({why}; {swhy}); choose CAD with an unambiguous pin-1 side"
                ]
            )
        offset = c.pin1_offset(supplier_side, board_side)
        name = part["footprint"].split(":")[1]
        if name in rules and rules[name] != offset:
            raise DesignError(
                [
                    f"parts.{ref}.footprint: supplier records disagree on CPL rotation for {name}; use distinct footprint names"
                ]
            )
        rules[name] = offset
    policy = dict(
        schema_version=1,
        policy_id="design-jlc-cpl-v1",
        provenance="Footprint-local pin-1 comparison against captured supplier component bytes; supplier preview remains required.",
        footprint_rules=[
            dict(id=f"rule-{i}", footprint_prefix=name, rotation_degrees=offset)
            for i, (name, offset) in enumerate(
                sorted(rules.items(), key=lambda row: (-len(row[0]), row[0]))
            )
        ],
        component_overrides=[],
    )
    write_json(root / "cpl/jlc-cpl-policy.json", policy)
    write_json(
        root / "cpl/assembly-profile.json",
        dict(
            schema="agent-ee.assembly-profile/1",
            profile_id="jlcpcb-cpl-software/1",
            supplier="JLCPCB",
            service="Software CPL orientation assessment; through-hole assembly availability not confirmed",
            applicability=dict(mount=["smd", "through_hole"], sides=["Top"]),
            position_convention=dict(
                anchor="pad_box_centroid",
                angle="native_plus_policy_delta_bottom_mirrored",
                x="board_x_mm",
                y="negated_board_y_mm",
            ),
            rotation_check="cpl",
            policy_sha256=digest(root / "cpl/jlc-cpl-policy.json"),
            rationale="Compiled from captured footprint-local geometry; no supplier confirmation.",
            sources=["cpl/jlc-cpl-policy.json"],
        ),
    )
    return sorted(set(suppliers))


def topology_rules(design, root):
    rules, classes = [], {}
    for i, f in enumerate(design["functional"], 1):
        for w in f["witnesses"]:
            rule = dict(w, requirement=f"{design['requirement_prefix']}-FUNC-{i}")
            if w["type"] == "pin_net":
                ref, pin = rule["terminal"].split(".", 1)
                rule["terminal"] = dict(reference=ref, pin=pin)
                rule["net"] = native_net(design, rule["net"])
            else:
                lib_id = rule.pop("lib_id", CLASS_SYMBOLS[w["class"]])
                pins = rule.pop("pins", ["1", "2"])
                classification = dict(
                    **{"class": w["class"]}, terminals=dict(a=pins[0], b=pins[1])
                )
                if lib_id in classes and classes[lib_id] != classification:
                    raise DesignError(
                        [
                            f"functional[{i - 1}].witnesses: conflicting classes for {lib_id}; use one consistent classification"
                        ]
                    )
                classes[lib_id] = classification
                rule["nets"] = [native_net(design, n) for n in rule["nets"]]
            rules.append(rule)
    document = dict(schema="agent-ee.topology-rules/1", classes=classes, rules=rules)
    from .topology_rules import validate_rules

    validate_rules(document)
    write_json(root / "topology-rules.json", document)


def requirements(design, root, record, suppliers):
    prefix = design["requirement_prefix"]
    name = design["name"]

    def check(cid, kind, **params):
        return dict(id=cid, kind=kind, params=params, stage="verification")

    def req(suffix, checks):
        return dict(id=prefix + "-" + suffix, checks=checks)

    envelope_check = lambda cid, criterion: check(
        cid, "board_envelope", criterion=criterion, envelope="envelope.json"
    )
    anchor = lambda ref, cid: check(
        cid,
        "placement",
        criterion="connector-edge/1",
        drc_check="drc",
        envelope="envelope.json",
        reference=ref,
    )
    model_params = dict(
        model_libraries=dict(KICAD10_3DMODEL_DIR="lib3d/.agent-ee-models.json")
    )
    height = lambda cid: check(
        cid,
        "board_envelope",
        criterion="component-height/1",
        envelope="envelope.json",
        **model_params,
    )
    rows = [
        req("NATIVE-1", [check("erc", "erc")]),
        req("NATIVE-2", [check("drc", "drc", plane_plans=[blob(root, "planes.json")])]),
        req(
            "NATIVE-3",
            [
                check("netlist", "netlist"),
                check(
                    "topology",
                    "netlist_parity",
                    multiset="exactly",
                    netlist_check="netlist",
                    predicate="topology",
                    source=dict(
                        kind="intent", path="build-generated/connectivity-intent.json"
                    ),
                ),
            ],
        ),
        req(
            "NATIVE-4",
            [
                check(
                    "parts",
                    "parts_subset",
                    manifest="parts.json",
                    netlist_check="netlist",
                    requirement_id=prefix + "-NATIVE-4",
                )
            ],
        ),
        req(
            "NATIVE-5",
            [
                check(
                    "assembly",
                    "assembly_readiness",
                    boards=1,
                    catalog="stock/catalog.json",
                    catalog_sha256=digest(root / "stock/catalog.json"),
                    max_age_hours=24,
                )
            ],
        ),
    ]
    rule_check = check(
        "functional-rules",
        "topology_rules",
        rules="topology-rules.json",
        netlist_check="netlist",
    )
    rows += [
        req(f"FUNC-{i}", [rule_check]) for i in range(1, len(design["functional"]) + 1)
    ]
    rows += [
        req(
            "MECH-1",
            [
                envelope_check("mech-outline", "outline/1"),
                envelope_check("mech-layers", "layers/1"),
                envelope_check("mech-stackup", "stackup/1"),
            ],
        ),
        req(
            "MECH-2",
            [
                anchor(c["reference"], "mech-anchor-" + c["reference"].lower())
                for c in design["connectors"]
            ]
            or [envelope_check("mech-nominal-stack", "stackup/1")],
        ),
        req(
            "MECH-3",
            [
                check(
                    "locality",
                    "placement",
                    criterion="electrical-locality/1",
                    drc_check="drc",
                    locality="electrical-locality.json",
                )
            ]
            if design["decoupling"]
            else [check("plane-drc", "drc", plane_plans=[blob(root, "planes.json")])],
        ),
        req(
            "MECH-4",
            [
                check(
                    "courtyard",
                    "placement",
                    criterion="courtyard-overlap/1",
                    drc_check="drc",
                ),
                check(
                    "containment",
                    "placement",
                    criterion="outline-containment/1",
                    drc_check="drc",
                ),
                height("mech-model-height"),
            ],
        ),
    ]
    rows += [
        dict(
            id=f"{prefix}-BENCH-{i}",
            bench=dict(
                criterion=text,
                procedure="Physical inspection and controlled bench test; not performed.",
            ),
        )
        for i, text in enumerate(design["bench"], 1)
    ]
    for suffix, checks in [
        ("outline", [envelope_check("outline", "outline/1")]),
        ("layer-count", [envelope_check("layers", "layers/1")]),
        ("component-height", [height("model-height")]),
        ("stackup", [envelope_check("stackup", "stackup/1")]),
        (
            "fab-profile",
            [check("profile", "fab_profile", fab_profile="fab-profile.json")],
        ),
    ]:
        rows.append(dict(id=f"R-envelope-{name}-{suffix}", checks=checks))
    rows += [
        dict(
            id=f"R-envelope-{name}-fixed-{c['reference']}",
            checks=[anchor(c["reference"], "anchor-" + c["reference"].lower())],
        )
        for c in design["connectors"]
    ]
    rows.append(
        req(
            "DELIV-1",
            [
                check(
                    "geometry",
                    "geometry",
                    covered_primitives=["PCB_TRACK", "PCB_ARC"],
                    criterion="copper-width/1",
                    exclude_nets=[],
                    min_track_width_mm=load(root / f"project/{name}.kicad_pro")[
                        "board"
                    ]["design_settings"]["rules"]["min_track_width"],
                ),
                check("fab-export", "fab_export", netlist_check="netlist"),
                check(
                    "cpl",
                    "cpl_rotation",
                    export_check="fab-export",
                    geometry_check="geometry",
                    policy="cpl/jlc-cpl-policy.json",
                    source_tolerance_nm="200",
                    supplier_footprints=suppliers,
                ),
                check(
                    "fab",
                    "fab_package",
                    netlist_check="netlist",
                    assembly_profile="cpl/assembly-profile.json",
                    **model_params,
                ),
            ],
        )
    )
    scope = dict(
        schema="agent-ee.scope/3",
        name=name,
        scope=design["intent"],
        board=f"project/{name}.kicad_pcb",
        schematic=f"project/{name}.kicad_sch",
        mutable_inputs=[f"project/{name}.kicad_sch", f"project/{name}.kicad_pcb"],
        envelope=dict(path="envelope.json", profile="fab-profile.json", dialog=None),
        generation=dict(schema="agent-ee.generation/1", done_check="fab"),
        requirements=["spec.md"],
        limitations=[
            "Software file claim only. No physical validation, independent engineering review or supplier preview."
        ],
        inputs=[dict(path=p.name, at=p.name) for p in sorted(root.iterdir())],
        stages=dict(
            schema="agent-ee.stages/1",
            finding_retry_limit=3,
            order=["verification"],
            subjects=dict(
                verification=dict(
                    kind="symbols",
                    path=f"project/{name}.kicad_sch",
                    references=sorted(design["parts"]),
                )
            ),
        ),
    )
    write_json(
        root / "requirement-map.json",
        dict(
            schema="agent-ee.requirement-map/2",
            spec_record_sha256=digest(root / "spec-record.json"),
            scope=scope,
            requirements=rows,
            prose=[
                dict(id=r["id"], context=dict(reason="Design intent context."))
                for r in record["prose"]
            ],
        ),
    )
    raw = (root / "spec.md").read_bytes()
    clauses = []
    for r in record["requirements"] + record["prose"]:
        loc = r["locator"]
        kind = (
            "context"
            if r in record["prose"]
            else "physical"
            if r["section"] == "Physical follow-up"
            else "executable"
        )
        clauses.append(
            dict(
                id=r["id"],
                spans=[
                    dict(
                        byte_start=loc["byte_start"],
                        byte_end=loc["byte_end"],
                        text_sha256=hashlib.sha256(
                            raw[loc["byte_start"] : loc["byte_end"]]
                        ).hexdigest(),
                        obligation=kind,
                    )
                ],
            )
        )
    write_json(
        root / "obligations.json",
        dict(
            schema="agent-ee.obligations/1",
            spec_sha256=digest(root / "spec.md"),
            clauses=clauses,
        ),
    )


def mechanics(design, root):
    if "mechanics" not in design:
        return
    declared = design["mechanics"]
    packages = {}
    for ref, part in sorted(design["parts"].items()):
        if ref in declared.get("packages", {}):
            packages[ref] = declared["packages"][ref]
            continue
        node = parse_sexpr((root / footprint_path(part)).read_bytes())
        points = []
        for graphic in node:
            if not isinstance(graphic, list) or not graphic:
                continue
            if not any(n[1:] == ["F.CrtYd"] for n in children(graphic, "layer")):
                continue
            if graphic[0] not in ("fp_line", "fp_rect"):
                raise DesignError(
                    [
                        f"mechanics.packages.{ref}: curved or unsupported courtyard; supply explicit size_mm and offset_mm"
                    ]
                )
            for key in ("start", "end"):
                points.extend(
                    [[float(n[1]), float(n[2])] for n in children(graphic, key)]
                )
        if not points:
            raise DesignError(
                [
                    f"mechanics.packages.{ref}: no linear front courtyard; supply size_mm and offset_mm in the footprint local frame"
                ]
            )
        xs, ys = zip(*points)
        packages[ref] = dict(
            size_mm=[
                max(xs) - min(xs),
                max(ys) - min(ys),
                design["board"]["max_component_height_mm"],
            ],
            offset_mm=[(max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2],
        )
    (root / "analysis").mkdir()
    (root / "analysis/body-basis.txt").write_text(
        "Design-declared measurement stand-in: unspecified package bodies use the bounding box of front courtyard line endpoints in each footprint local frame. Height uses the design maximum. Supplied sizes are design declarations. These dimensions are not measured physical bodies.\n"
    )
    write_json(
        root / "analysis/geometry-model.json",
        dict(
            custody="design-declared",
            access_owners=declared["access_owners"],
            source_files=["analysis/body-basis.txt"],
            packages=packages,
        ),
    )
    from .acquisition_mechanics import declaration

    write_json(root / "placement-mechanics.json", declaration(root))


def qualify_bundle(root, work):
    """Replay existing offline producers against an isolated, provisional scaffold."""
    from .board_envelope import validate as validate_envelope
    from .parts_stage import compile_parts
    from .requirement_map import compile_map, input_inventory
    from .scaffold import native_files
    from .schematic_emitter import emit_schematic

    shutil.copytree(root, work)
    (work / "requirements-map").mkdir()
    shutil.copyfile(work / "spec.md", work / "requirements-map/spec.md")
    try:
        _, intent = compile_parts(
            work, "parts.json", "circuit.json", "layout.json", ratings="ratings.json"
        )
    except ValueError as exc:
        raise DesignError(
            [f"parts/nets: correct the selected values, CAD or connectivity: {exc}"]
        ) from exc
    (work / "build-generated").mkdir()
    write_json(work / "build-generated/connectivity-intent.json", intent)
    libraries = {
        lib["path"]: (work / lib["path"]).read_bytes() for lib in intent["libraries"]
    }
    emission, emitted = emit_schematic(
        intent, libraries, (work / "layout.json").read_bytes()
    )
    if emitted is None:
        raise DesignError(
            [
                f"groups/nets: schematic layout refused; correct grouping or connectivity: {emission['findings']}"
            ]
        )
    envelope_record = validate_envelope((work / "envelope.json").read_bytes())
    project = next((work / "project").glob("*.kicad_pro"))
    sch = "project/" + project.with_suffix(".kicad_sch").name
    for rel, raw in native_files(
        envelope_record, sch, load(project), emission["root_uuid"], "design-preflight"
    ).items():
        (work / rel).write_bytes(raw)
    files = {
        rel: sha
        for rel, sha in input_inventory(work).items()
        if not rel.startswith("requirements-map/")
    }
    compile_map(
        (work / "requirement-map.json").read_bytes(),
        (work / "spec-record.json").read_bytes(),
        (work / "spec.md").read_bytes(),
        files=files,
        inputs=work,
        obligations_raw=(work / "obligations.json").read_bytes(),
    )


def compile_design(design, captures, out):
    """Refuse before output publication; identical decisions/captures yield identical bytes."""
    assemblies = validate(design, captures)
    if out.exists() or out.is_symlink():
        raise FileExistsError(f"{out}: choose a new bundle directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".design-compile-", dir=out.parent
    ) as temporary:
        bundle = Path(temporary) / "bundle"
        root = bundle / "inputs"
        root.mkdir(parents=True)
        # Retain the design itself as a readable source of generated decisions.
        write_json(root / "design.json", design)
        for rel in ("stock", "datasheets", "supplier", "lib3d"):
            shutil.copytree(captures / rel, root / rel)
        (root / "spec.md").write_text(spec(design), encoding="utf-8")
        from .spec_record import capture

        record = capture(
            (root / "spec.md").read_bytes(), design["requirement_prefix"], CODES
        )
        write_json(root / "spec-record.json", record)
        libraries, members_sha = cad_records(design, captures, root, assemblies)
        selected_parts(design, root, record, members_sha)
        circuit(design, root, libraries)
        envelope(design, root)
        placement(design, root)
        mechanics(design, root)
        suppliers = cpl_policy(design, root)
        topology_rules(design, root)
        requirements(design, root, record, suppliers)
        p = design.get("placement", {})
        seeds = p.get("seeds", 4)
        plan = dict(
            schema="agent-ee.build-plan/2",
            source="inputs",
            envelope="envelope.json",
            requirements=dict(
                map="requirement-map.json",
                record="spec-record.json",
                spec="spec.md",
                obligations="obligations.json",
            ),
            parts=dict(
                manifest="parts.json",
                circuit="circuit.json",
                layout="layout.json",
                ratings="ratings.json",
            ),
            placement=dict(
                intent="placement.json",
                groups="groups.json",
                planes="planes.json",
                budget_s=p.get("budget_s", 600),
                search=dict(
                    seeds=[dict(seed=i, start="clustered") for i in range(seeds)],
                    steps_per_seed=p.get("steps_per_seed", 100000),
                    shortlist=seeds,
                ),
                router=dict(checkout="/opt/kicad-route"),
            ),
            verification_timeout_s=1800,
        )
        if "mechanics" in design:
            plan["placement"]["mechanics"] = "placement-mechanics.json"
        write_json(bundle / "build-plan.json", plan)
        from .build_plan import validate_plan

        validate_plan(plan)
        qualify_bundle(root, Path(temporary) / "preflight")
        bundle.rename(out)
    return out / "build-plan.json"

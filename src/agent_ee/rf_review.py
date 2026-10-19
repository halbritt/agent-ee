"""Dedicated RF review: applicability, exact source binding, assessment contract and gates.

The RF reviewer is an ordinary subscription reviewer role (``rf``) with a fixed
brief and a structured ``rf_assessment``. This module decides explicitly whether
a captured candidate requires it, binds the review to the exact captured RF
sources, validates the reviewer's topic coverage and external qualification
statements, and supplies the completion and correction-closure gates. A 50 ohm
declaration, calculator output, clearance pass or plane coverage is input to the
review, never RF qualification; this module records no measurement.
"""
import copy
import json
import re
from pathlib import Path, PurePosixPath

from .storage import digest, identity, read_json, write_json

ROLE = "rf"
SCHEMA = "agent-ee.rf-review-decision/1"
ASSESSMENT_KEY = "rf_assessment"

TOPICS = (
    "gnss_receiver_to_sma_path",
    "ble_module_antenna_integration",
    "controlled_impedance_assumptions_tolerances",
    "width_gap_layer_transitions_launches",
    "ground_reference_continuity_return_paths",
    "stitching_and_decoupling",
    "antenna_keepout_nearby_copper_enclosure",
    "rf_supply_noise_and_coupling",
)
TOPIC_STATUSES = ("supported", "concern", "missing_evidence", "not_applicable")
QUALIFICATION_METHODS = (
    "field_solver", "matching_network_measurement", "vna", "tdr", "receiver_sensitivity_test",
    "installed_gnss_measurement", "installed_ble_measurement", "antenna_efficiency_measurement",
    "emissions_precompliance")
# Registered check kinds an RF reviewer consumes when present. Absence is recorded
# as missing, never as a pass. Layer policy and RF reference continuity are not
# check kinds: they come from the routing adapter's verified AGENTEE-231 grade.
RF_EVIDENCE_KINDS = ("drc", "geometry", "routing_rules", "net_class_assignment", "netlist", "netlist_parity",
                     "rf_geometry")
RF_EXPECTED_KINDS = ("net_class_assignment", "routing_rules", "geometry")
# Under work/ so reviewers see it at /evidence/work/rf-routing and may cite it; the id is reserved.
ROUTING_DIR = "work/rf-routing"
ROUTING_SCHEMA = "agent-ee.rf-routing-evidence/1"
ROUTING_STATUSES = ("verified", "missing_evidence", "invalid", "stale")
CATEGORIES = ("schematic", "board", "stackup", "rf_constraints", "rf_captures", "planes",
              "antenna_and_module_sources", "datasheets")

# Coaxial connectors, antennas and RF libraries; package names such as D_SMA are not RF parts.
RF_PART = re.compile(r"(?i)(^rf_|^rf:|^connector_coaxial:|:conn_coaxial|antenna|u\.fl|ipex)")
RF_NET = re.compile(r"(?i)(^|[/_.\-])(rf|ant|antenna|gnss|gps|lna)(\d*)([_/.\-]|$)")
GNSS_PART = re.compile(r"(?i)(rf_gps|gnss|neo-m|max-m|\bgps)")
COAX_PART = re.compile(r"(?i)(coaxial|u\.fl|ipex)")
GROUND_NET = re.compile(r"(?i)(^|/)(gnd|ground|vss|agnd|dgnd|pgnd)\w*$")
BLE_PART = re.compile(r"(?i)(rf_module|wroom|esp32-[a-z0-9]*-mini|nrf52|\bble\b|bluetooth)")

BRIEF = (
    "You are the dedicated RF reviewer. Review the RF signal chain of this exact candidate from its captured native "
    "sources and the packet rf_review binding: the GNSS receiver RF input to the SMA launch (series parts, DC "
    "blocking, ESD, bias/LNA supply, connector footprint and edge launch), and the BLE/Wi-Fi module antenna "
    "integration (module placement versus board edge, antenna keepout on every layer, nearby copper, mounting "
    "hardware, connectors, enclosure and cable). For controlled impedance, state the assumed structure (microstrip, "
    "coplanar with lower ground), the stackup layers, dielectric constant and thickness actually bound, the copper "
    "thickness, solder-mask treatment and fabrication tolerances, and how they differ from the declaration. Inspect "
    "the actual routed width, gap to coplanar ground, layer transitions, vias, stubs, pad and connector launch "
    "geometry and neck-downs on the exact RF nets; if copper is absent, say so. Check ground-reference continuity "
    "under the whole RF path and antenna feed: reference layer, plane splits/slots, zone fill state, return path at "
    "transitions and launches, and ground stitching via pitch along coplanar ground. Check decoupling and filtering "
    "of RF supply rails, switching-regulator and digital noise sources near RF parts and coupling into the RF path. "
    "Rules: a 50 ohm declaration, a calculator or solver output for idealized geometry, a minimum clearance or DRC "
    "pass, net-class assignment, or 2D plane coverage is NEVER RF qualification and must not be reported as such. "
    "Separate conclusions supported by cited captured evidence from missing evidence. State explicitly where field "
    "solving of the as-built geometry, matching-network measurement, VNA or TDR on fabricated coupons/boards, "
    "receiver sensitivity tests, or installed BLE/GNSS measurements in the enclosure are still required. Never invent "
    "measurements, datasheet values or bench results, and never present reviewer judgment as bench evidence. Every "
    "finding must name a concrete location (reference designator/pad, net, layer, coordinates or file lines) and "
    "cite the exact captured source file it relies on. ")


def _rel_kind(rel):
    name = PurePosixPath(rel).name.lower()
    return name, PurePosixPath(rel).suffix.lower()


def _json(read, rel):
    try:
        value = json.loads(read(rel))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _text(read, rel):
    try:
        return read(rel).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _top_level_items(text):
    """Line ranges of top-level KiCad items; KiCad saves each at one tab of indentation."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("\t(") and not line.startswith("\t\t")]
    items = []
    for n, start in enumerate(starts):
        end = (starts[n + 1] if n + 1 < len(starts) else len(lines)) - 1
        while end > start and not lines[end].strip():
            end -= 1
        items.append((start + 1, end + 1, "\n".join(lines[start:end + 1])))
    return items


def _atoms(node):
    return [x for x in node[1:] if isinstance(x, str)]


def _first(node, key):
    from .symbol_library import children
    found = children(node, key)
    return found[0] if found else None


def _board_summary(raw, rf_nets):
    """Parse the captured board with the shared S-expression parser; report RF structure and locations."""
    from .symbol_library import LibraryError, children, parse_sexpr
    try:
        root = parse_sexpr(raw)
        text = raw.decode("utf-8")
    except (LibraryError, UnicodeDecodeError) as exc:
        return {"parsed": False, "reason": f"board not parseable as KiCad S-expression: {exc}"}, [], set()
    if root[0] != "kicad_pcb":
        return {"parsed": False, "reason": "board root is not kicad_pcb"}, [], set()
    nets = set()
    for item in root[1:]:
        if isinstance(item, list) and item and item[0] == "net":
            nets.update(a for a in _atoms(item) if not re.fullmatch(r"\d+", a))
    setup = _first(root, "setup")
    stackup = []
    if setup is not None and _first(setup, "stackup") is not None:
        for layer in children(_first(setup, "stackup"), "layer"):
            entry = {"name": _atoms(layer)[0] if _atoms(layer) else ""}
            for key in ("type", "thickness", "material", "epsilon_r", "loss_tangent"):
                node = _first(layer, key)
                if node is not None and _atoms(node):
                    entry[key] = _atoms(node)[0]
            stackup.append(entry)
    footprints, rf_parts, zones = [], [], []
    tracks = {}

    def net_of(node):
        n = _first(node, "net")
        return None if n is None else next((a for a in reversed(_atoms(n))), None)

    for node in root[1:]:
        if not isinstance(node, list) or not node:
            continue
        if node[0] == "footprint":
            name = _atoms(node)[0] if _atoms(node) else ""
            ref = next((_atoms(p)[1] for p in children(node, "property")
                        if len(_atoms(p)) > 1 and _atoms(p)[0] == "Reference"), "")
            at = _first(node, "at")
            pads = [(p_atoms[0] if (p_atoms := _atoms(p)) else "", net_of(p)) for p in children(node, "pad")]
            nets.update(n for _, n in pads if n)
            touched = sorted({n for _, n in pads if n in rf_nets})
            entry = {"reference": ref, "footprint": name, "at": _atoms(at) if at is not None else [],
                     "layer": (_atoms(_first(node, "layer")) or [""])[0] if _first(node, "layer") is not None else ""}
            if RF_PART.search(name) or touched:
                rf_parts.append({**entry, "rf_nets": touched, "pad_nets": sorted({n for _, n in pads if n}),
                                 "rf_pads": sorted(num for num, n in pads if n in rf_nets)})
            footprints.append(entry)
        elif node[0] in ("segment", "arc", "via"):
            net = net_of(node)
            if net:
                nets.add(net)
            if net in rf_nets:
                record = tracks.setdefault(net, {"segments": 0, "arcs": 0, "vias": 0, "widths_mm": [], "layers": []})
                record[{"segment": "segments", "arc": "arcs", "via": "vias"}[node[0]]] += 1
                width = _first(node, "width") or _first(node, "size")
                if width is not None and _atoms(width) and node[0] != "via":
                    record["widths_mm"] = sorted(set(record["widths_mm"]) | {_atoms(width)[0]})
                layers = _first(node, "layer") or _first(node, "layers")
                if layers is not None:
                    record["layers"] = sorted(set(record["layers"]) | set(_atoms(layers)))
        elif node[0] == "zone":
            layers = _first(node, "layers") or _first(node, "layer")
            zones.append({"net": net_of(node), "layers": _atoms(layers) if layers is not None else [],
                          "keepout": _first(node, "keepout") is not None,
                          "filled": bool(children(node, "filled_polygon"))})
    locations = []
    for start, end, chunk in _top_level_items(text):
        head = chunk.strip().split(None, 1)[0].lstrip("(")
        if head == "setup" and "(stackup" in chunk:
            locations.append({"item": "setup/stackup", "lines": [start, end]})
        elif head == "zone":
            locations.append({"item": "zone", "lines": [start, end]})
        elif head == "footprint" and (RF_PART.search((re.match(r'\s*\(footprint "([^"]*)"', chunk) or [None, ""])[1])
                                      or any(f'(net "{n}")' in chunk for n in rf_nets)):
            ref = re.search(r'\(property "Reference" "([^"]*)"', chunk)
            locations.append({"item": "footprint " + (ref.group(1) if ref else "?"), "lines": [start, end]})
        elif head in ("segment", "arc", "via") and any(f'(net "{n}")' in chunk for n in rf_nets):
            locations.append({"item": head, "lines": [start, end]})
    summary = {"parsed": True, "stackup": stackup, "rf_parts": rf_parts, "zones": zones,
               "rf_copper": {net: tracks.get(net, {"segments": 0, "arcs": 0, "vias": 0, "widths_mm": [],
                                                   "layers": []}) for net in sorted(rf_nets & nets)},
               "rf_nets_absent_from_board": sorted(rf_nets - nets), "locations": locations,
               "footprint_count": len(footprints)}
    return summary, [p["footprint"] for p in rf_parts], nets


def _schematic_parts(raw):
    text = raw.decode("utf-8", "replace")
    return sorted(set(re.findall(r'\(lib_id "([^"]+)"\)', text))), sorted(set(
        re.findall(r'\((?:global_label|label|hierarchical_label) "([^"]+)"', text)))


def _declaration(scope):
    declared = scope.get("rf_review")
    if declared is None:
        return None
    if (not isinstance(declared, dict) or set(declared) - {"applicable", "basis", "sources"} or
            not isinstance(declared.get("applicable"), bool) or
            not isinstance(declared.get("basis"), str) or not declared["basis"].strip()):
        raise ValueError("scope rf_review requires boolean applicable, a nonempty basis and optional sources")
    sources = declared.get("sources", {})
    if (not isinstance(sources, dict) or set(sources) - set(CATEGORIES) or
            any(not isinstance(v, list) or any(not isinstance(p, str) for p in v) for v in sources.values())):
        raise ValueError("scope rf_review sources map known categories to captured paths")
    return declared


def decide(scope, files, read, routing=None):
    """Explicit applicability and exact source binding for one captured candidate.

    ``files`` maps captured relative paths to SHA-256; ``read(rel)`` returns their
    bytes. Detected RF indicators always make the review required: a declaration
    cannot waive them. Without indicators the not-applicable basis is recorded.
    ``routing`` is the run's retained routing adapter evidence (``retained_routing``);
    None is missing evidence."""
    declared = _declaration(scope)
    indicators, sources = [], {c: [] for c in CATEGORIES}

    def bind(category, rel):
        entry = {"path": "inputs/" + rel, "sha256": files[rel]}
        if entry not in sources[category]:
            sources[category].append(entry)

    rf_nets, impedance = set(), []
    for rel in sorted(files):
        name, suffix = _rel_kind(rel)
        if suffix == ".json":
            doc = _json(read, rel)
            schema = doc.get("schema") if doc else None
            if schema == "agent-ee.net-constraints/1":
                from .net_constraints import parse_document
                try:
                    _, classes = parse_document(read(rel))
                except ValueError as exc:
                    indicators.append({"kind": "unparseable_net_constraints", "source": "inputs/" + rel,
                                       "detail": str(exc)})
                    bind("rf_constraints", rel)
                    continue
                for spec in classes:
                    if "impedance_ohm" in spec or RF_NET.search(spec["name"]):
                        rf_nets.update(spec["nets"])
                        impedance.append({"class": spec["name"], "nets": spec["nets"],
                                          **{k: spec[k] for k in ("impedance_ohm", "track_width_mm",
                                                                 "clearance_mm") if k in spec},
                                          "status": "declared_input_not_qualification"})
                        indicators.append({"kind": "rf_constraint_class", "source": "inputs/" + rel,
                                           "detail": spec["name"]})
                        bind("rf_constraints", rel)
            elif schema == "agent-ee.plane-plan/1":
                bind("planes", rel)
            elif schema == "agent-ee.datasheet-facts/1" and "antenna" in json.dumps(doc).lower():
                bind("antenna_and_module_sources", rel)
            elif "rf" in re.split(r"[-_.]", name) and "capture" in name:
                bind("rf_captures", rel)
            elif "stackup" in name or name == "fab-profile.json":
                bind("stackup", rel)
        elif suffix == ".pdf":
            bind("datasheets", rel)
        elif "stackup" in name:
            bind("stackup", rel)
        if PurePosixPath(rel).parts[0] == "antenna":
            bind("antenna_and_module_sources", rel)
    schematic_parts, labels = [], []
    for rel in [scope.get("schematic"), *[r for r in sorted(files) if r.endswith(".kicad_sch")]]:
        if rel in files:
            bind("schematic", rel)
            parts, names = _schematic_parts(read(rel))
            schematic_parts += parts
            labels += names
    for lib_id in sorted(set(schematic_parts)):
        if RF_PART.search(lib_id) or GNSS_PART.search(lib_id) or BLE_PART.search(lib_id):
            indicators.append({"kind": "schematic_rf_part", "source": "inputs/" + scope["schematic"],
                               "detail": lib_id})
    related = set()
    for label in sorted(set(labels)):
        if RF_NET.search(label):
            related.add(label)
            indicators.append({"kind": "schematic_rf_related_label", "source": "inputs/" + scope["schematic"],
                               "detail": label})
    board = {"parsed": False, "reason": "board not captured"}
    board_rel = scope.get("board")
    if board_rel in files:
        bind("board", board_rel)
        bind("stackup", board_rel)
        board, rf_footprints, board_nets = _board_summary(read(board_rel), rf_nets)
        if board.get("parsed"):
            # RF signal nets: declared RF classes plus coaxial-connector signal pads. Name matches
            # (bias, enable, fault, UART) are RF-related indicators, not RF geometry.
            coax = {n for part in board["rf_parts"] if COAX_PART.search(part["footprint"])
                    for n in part["pad_nets"] if not GROUND_NET.search(n)}
            if coax - rf_nets:
                rf_nets |= coax
                board, rf_footprints, board_nets = _board_summary(read(board_rel), rf_nets)
            named = {n for n in board_nets if RF_NET.search(n) and not n.startswith("unconnected-")}
            related |= named
            for net in sorted(named):
                indicators.append({"kind": "board_rf_related_net", "source": "inputs/" + board_rel, "detail": net})
            for name in sorted(set(rf_footprints)):
                if RF_PART.search(name):
                    indicators.append({"kind": "board_rf_footprint", "source": "inputs/" + board_rel,
                                       "detail": name})
            if any(z["filled"] for z in board["zones"]) or board["zones"]:
                bind("planes", board_rel)
    for category, paths in (declared or {}).get("sources", {}).items():
        for rel in paths:
            if rel not in files:
                raise ValueError(f"scope rf_review source was not captured: {rel}")
            bind(category, rel)
    applicable = bool(indicators) or bool(declared and declared["applicable"])
    basis = []
    if indicators:
        basis.append(f"{len(indicators)} RF indicator(s) detected in captured sources")
    if declared:
        basis.append(f"scope declares applicable={declared['applicable']}: {declared['basis']}")
        if not declared["applicable"] and indicators:
            basis.append("declared not-applicable is contradicted by detected RF indicators; review required")
    if not applicable:
        basis.append(f"no RF indicator in {len(set(schematic_parts))} schematic lib_ids, {len(set(labels))} "
                     f"schematic labels, board ({'parsed' if board.get('parsed') else board.get('reason')}) "
                     "or captured net-constraint classes; RF review not applicable")
    expects = {
        "gnss": any(GNSS_PART.search(i["detail"]) for i in indicators) or
                any("gnss" in n.lower() or "gps" in n.lower() for n in rf_nets),
        "ble": any(BLE_PART.search(i["detail"]) for i in indicators),
        "controlled_impedance": bool(impedance)}
    missing = [f"{c}: no captured source bound" for c in CATEGORIES if not sources[c]]
    if board.get("parsed") and applicable and not any(z["filled"] for z in board.get("zones", [])):
        missing.append("planes: no filled copper zone in the captured board; plane continuity is unknown")
    routing = _bound_routing(routing, scope, files)
    if applicable:
        missing += routing_missing(routing)
    layer_requirements = _layer_requirements(sources["rf_captures"], read, impedance, rf_nets, board, routing)
    missing += [f"RF layer requirement {r['signal_layer']} over {r['reference_layer']} for "
                f"{', '.join(r['nets']) or 'undeclared RF nets'} ({r['source']}): {r['status']}; {r['reason']}"
                for r in layer_requirements]
    decision = {"schema": SCHEMA, "role": ROLE, "applicable": applicable, "required": applicable,
                "basis": basis, "declared": copy.deepcopy(declared), "indicators": indicators,
                "rf_nets": sorted(rf_nets), "rf_related_nets": sorted(related), "impedance_declarations": impedance, "expects": expects,
                "sources": sources, "board": board, "routing_adapter": routing,
                "rf_layer_requirements": layer_requirements, "missing": missing,
                "category_sha256": {**{c: identity(sources[c]) for c in CATEGORIES},
                                    "routing_adapter": identity(routing)}}
    decision["binding_sha256"] = identity(decision)
    return decision


def decide_run(run_dir, manifest):
    run_dir = Path(run_dir)
    return decide(manifest["scope"], manifest["files"], lambda rel: (run_dir / "inputs" / rel).read_bytes(),
                  retained_routing(run_dir))


def native_evidence(checks, decision=None):
    """RF-relevant native evidence for the packet; absent kinds stay missing (unknown)."""
    present = [{"check": c["id"], "kind": c.get("kind"), "execution": c.get("execution"),
                "outcome": c.get("outcome"), "path": f"checks/{c['id']}.json"}
               for c in checks if c.get("kind") in RF_EVIDENCE_KINDS]
    kinds = {c["kind"] for c in present}
    absent = [k for k in RF_EXPECTED_KINDS if k not in kinds]
    routing = (decision or {}).get("routing_adapter") or _missing_routing("no RF decision bound to this packet")
    return {"present": present, "absent_kinds": absent,
            "routing_adapter": {k: routing.get(k) for k in ("status", "reason", "record_sha256", "adapter_report",
                                                            "reference_continuity", "layer_policy")},
            "rule": "absent evidence is unknown, never pass; present native passes and routing adapter grades are "
                    "inputs, not RF qualification"}


def _missing_routing(reason):
    return {"schema": ROUTING_SCHEMA, "status": "missing_evidence", "reason": reason, "record_sha256": None}


def _copy_bound(source, target):
    """Copy a retained evidence file and return the SHA-256 of the bytes copied."""
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"routing evidence file missing: {source.name}")
    data = source.read_bytes()
    target.write_bytes(data)
    return digest(target)


def capture_routing(run, manifest, context):
    """Verify and retain the candidate-bound routing adapter evidence for the RF review.

    ``context`` is None (no routing step supplied) or ``{"receipt": None | {"path",
    "sha256"}}`` naming an anchored AGENTEE-95 routing receipt. The receipt is
    re-verified with the routing adapter's own verifier; the adapter report and
    reference-continuity measurement are copied into the run and bound by SHA-256.
    Anything absent, unverifiable or of another board is recorded as such, never
    as a pass."""
    from .kicad_route_attempt import POLICY_FILE
    if any(c.get("id") == PurePosixPath(ROUTING_DIR).name for c in manifest["scope"].get("checks", [])):
        raise ValueError(f"check id {PurePosixPath(ROUTING_DIR).name} is reserved for RF routing evidence")
    out = Path(run) / ROUTING_DIR
    out.mkdir(parents=True)
    board_rel = manifest["scope"]["board"]
    record = {"schema": ROUTING_SCHEMA,
              "board": {"path": "inputs/" + board_rel, "sha256": manifest["files"].get(board_rel)},
              "receipt": None}

    def finish(status, reason, **extra):
        record.update(status=status, reason=reason, **extra)
        write_json(out / "evidence.json", record)
        return record

    link = (context or {}).get("receipt")
    if link is None:
        return finish("missing_evidence", "no routing adapter receipt was supplied for this candidate"
                      if context is None else "the declared routing step has no adopted receipt")
    record["receipt"] = {"path": str(link["path"]), "sha256": link["sha256"]}
    path = Path(link["path"])
    try:
        if path.is_symlink() or not path.is_file() or digest(path) != link["sha256"]:
            raise ValueError("routing receipt changed since it was anchored")
        from . import routing_adapter
        summary = routing_adapter.verify_campaign_receipt(path.parent)
        if summary is None:
            return finish("missing_evidence", "the routing attempt left no adapter report")
        receipt = read_json(path)
        routing = path.parent / "run" / "routing"
        report_sha = _copy_bound(routing / "adapter-report.json", out / "adapter-report.json")
        if report_sha != receipt["artifacts"].get("routing/adapter-report.json"):
            raise ValueError("adapter report differs from the receipt artifact")
        report = read_json(out / "adapter-report.json")
        continuity, retained = None, report["retained"].get("attempt/" + POLICY_FILE)
        if retained is not None:
            sha = _copy_bound(routing / "attempt" / POLICY_FILE, out / "reference-continuity.json")
            if sha != retained:
                raise ValueError("reference continuity measurement differs from the adapter report")
            continuity = {"path": f"{ROUTING_DIR}/reference-continuity.json", "sha256": sha}
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        return finish("invalid", f"routing adapter evidence failed verification: {exc}")
    extra = {"candidate_sha256": summary["candidate_sha256"], "classification": summary["classification"],
             "backend": summary["backend"],
             "adapter_report": {"path": f"{ROUTING_DIR}/adapter-report.json", "sha256": report_sha},
             "reference_continuity": continuity,
             "declared_layer_policy": report["request"].get("layer_policy"),
             "layer_policy": summary.get("layer_policy")}
    if summary["candidate_sha256"] != record["board"]["sha256"]:
        return finish("stale", f"routing adapter evidence is of board {summary['candidate_sha256']}, "
                               "not the reviewed board", **extra)
    return finish("verified", "verified routing receipt whose routed board is the reviewed board", **extra)


def retained_routing(run_dir):
    """The run's retained routing evidence record, re-checked against its retained copies."""
    run_dir = Path(run_dir)
    path = run_dir / ROUTING_DIR / "evidence.json"
    if path.is_symlink() or not path.is_file():
        return _missing_routing("no routing adapter evidence record is retained in this run")
    sha = digest(path)
    try:
        record = read_json(path)
        if not isinstance(record, dict) or record.get("schema") != ROUTING_SCHEMA or \
                record.get("status") not in ROUTING_STATUSES:
            raise ValueError("malformed routing evidence record")
        for key in ("adapter_report", "reference_continuity"):
            entry = record.get(key)
            if entry is not None:
                copied = run_dir / entry["path"]
                if (PurePosixPath(entry["path"]).parent != PurePosixPath(ROUTING_DIR) or copied.is_symlink() or
                        not copied.is_file() or digest(copied) != entry["sha256"]):
                    raise ValueError(f"retained {key} changed after capture")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return {"schema": ROUTING_SCHEMA, "status": "invalid", "reason": f"retained routing evidence: {exc}",
                "record_sha256": sha}
    return {**record, "record_sha256": sha}


def _bound_routing(routing, scope, files):
    """The routing evidence as bound to this candidate's captured board."""
    if routing is None:
        return _missing_routing("no routing adapter evidence was bound")
    routing = copy.deepcopy(routing)
    board = (routing.get("board") or {})
    if routing["status"] in ("verified", "stale") and (
            board.get("path") != "inputs/" + str(scope.get("board")) or board.get("sha256") != files.get(scope.get("board"))):
        routing.update(status="invalid", reason="routing evidence record is bound to another captured board")
    return routing


def routing_missing(routing):
    if routing["status"] != "verified":
        return [f"routing adapter evidence {routing['status']}: {routing['reason']}; layer policy and RF reference "
                "continuity are unknown"]
    policy = routing.get("layer_policy")
    if policy is None:
        return ["routing adapter evidence: no layer policy was declared to the router; layer policy and RF "
                "reference continuity are unknown"]
    out = [f"routing adapter layer policy item {name}: {item['status']}"
           for name, item in sorted(policy.get("items", {}).items()) if item.get("status") != "pass"]
    if "reference_continuity" not in policy.get("items", {}):
        out.append("routing adapter layer policy declares no RF reference; RF reference continuity is unknown")
    return out


def _stackup_statements(value, pointer=""):
    if isinstance(value, dict):
        if isinstance(value.get("signal_layer"), str) and isinstance(value.get("reference_layer"), str):
            yield pointer or "/", value["signal_layer"], value["reference_layer"]
        for key, item in value.items():
            yield from _stackup_statements(item, f"{pointer}/{key}")
    elif isinstance(value, list):
        for n, item in enumerate(value):
            yield from _stackup_statements(item, f"{pointer}/{n}")


def _layer_requirements(captures, read, impedance, rf_nets, board, routing):
    """Per-net RF signal/reference layer requirements stated in captured RF sources.

    No registered check and no adapter grade enforces a per-net signal layer, and
    a global signal-layer allowlist is not per-net enforcement, so a requirement is
    never satisfied here: unknown, or defect when the verified reference continuity
    grade for its reference layer reports one."""
    nets = sorted({n for d in impedance if "impedance_ohm" in d for n in d["nets"]} or rf_nets)
    policy = routing.get("layer_policy") if routing["status"] == "verified" else None
    declared = routing.get("declared_layer_policy") if routing["status"] == "verified" else None
    reference = (declared or {}).get("reference") or {}
    found = {}
    for entry in captures:
        doc = _json(read, entry["path"][len("inputs/"):])
        for pointer, signal, ref in _stackup_statements(doc or {}):
            layer = ref.split()[0] if ref.split() else ref
            found.setdefault((entry["path"], signal, layer), {"declared": ref, "locators": []})["locators"].append(pointer)
    out = []
    for (source, signal, layer), seen in sorted(found.items()):
        continuity = None
        if (policy is not None and layer in (reference.get("layers") or []) and nets and
                set(nets) <= set(reference.get("rf_nets") or [])):
            continuity = (policy.get("items") or {}).get("reference_continuity")
        status = "defect" if (continuity or {}).get("status") == "defect" else "unknown"
        out.append({"source": source, "locators": seen["locators"], "signal_layer": signal, "reference_layer": layer,
                    "reference_declared": seen["declared"], "nets": nets, "status": status,
                    "reason": ("the verified routing reference continuity grade reports a defect on this reference"
                               if status == "defect" else
                               "no registered check or routing adapter grade enforces this per-net signal layer; a "
                               "global signal-layer allowlist is not per-net enforcement"),
                    "related_evidence": {
                        "global_signal_layers": (declared or {}).get("signal_layers"),
                        "reference_continuity": None if continuity is None else continuity.get("status"),
                        "board_rf_copper_layers": {n: (board.get("rf_copper") or {}).get(n, {}).get("layers")
                                                   for n in nets}}})
    return out


def campaign_routing_context(root, state):
    """The latest anchored receipt of the campaign's owned routing step; None without one."""
    from .stage_done import _receipts
    binding = state.get("routing_binding")
    if binding is None:
        return None
    context = {"receipt": None}
    for number in range(1, len(state["history"]) + 1):
        work = Path(root) / "implementations" / str(number)
        for k, sha, receipt in _receipts(root, state, str(number)):
            if receipt["spec"] == binding["spec"]:
                link = read_json(work / "stage-receipts.json")["receipts"][k - 1]
                context["receipt"] = {"path": str(work / link["path"]), "sha256": sha}
    return context


def assessment_schema(binding_sha256):
    from .reviewers import EVIDENCE_SCHEMA
    return {"type": "object", "additionalProperties": False,
            "required": ["binding_sha256", "topics", "external_qualification", "qualification_claimed"],
            "properties": {
                "binding_sha256": {"type": "string", "enum": [binding_sha256]},
                "qualification_claimed": {"type": "boolean", "enum": [False]},
                "topics": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                    "required": ["topic", "status", "summary", "evidence", "missing_evidence"],
                    "properties": {"topic": {"type": "string", "enum": list(TOPICS)},
                                   "status": {"type": "string", "enum": list(TOPIC_STATUSES)},
                                   "summary": {"type": "string"}, "evidence": EVIDENCE_SCHEMA,
                                   "missing_evidence": {"type": "array", "items": {"type": "string"}}}}},
                "external_qualification": {"type": "array", "items": {"type": "object",
                    "additionalProperties": False, "required": ["method", "reason", "scope"],
                    "properties": {"method": {"type": "string", "enum": list(QUALIFICATION_METHODS)},
                                   "reason": {"type": "string"}, "scope": {"type": "string"}}}}}}


def prompt(decision, evidence):
    return (BRIEF + "Return the additional top-level rf_assessment object: binding_sha256 exactly "
            f"{decision['binding_sha256']} (packet rf_review); qualification_claimed false; one topics entry for "
            f"each of {', '.join(TOPICS)} with status supported (only with structured evidence citations), concern, "
            "missing_evidence (list exactly what is missing) or not_applicable (summary states the basis); and "
            "external_qualification naming every remaining field-solver, matching, VNA/TDR, receiver or installed "
            "BLE/GNSS measurement still required. Every finding must cite at least one captured inputs/ source or "
            "check/experiment evidence. rf_review.routing_adapter is the routing adapter evidence bound to this "
            "candidate by SHA-256 (status verified, missing_evidence, invalid or stale; only verified grades are "
            "evidence, and a layer policy pass is never RF qualification). rf_review.rf_layer_requirements are "
            "per-net signal/reference layer requirements from captured RF sources; none is enforced by a check, so "
            "treat each as unknown (or defect where so graded), never satisfied. RF binding: " + json.dumps({"rf_review": decision, "native_evidence": evidence}))


def validate_assessment(value, review_findings, run_dir, *, require_structured=True):
    """Mechanically validate the RF reviewer's assessment against the run's bound decision."""
    from .findings import validate_citation
    from .storage import read_json
    packet = read_json(run_dir / "packet.json") if (run_dir / "packet.json").is_file() else {}
    decision = packet.get("rf_review")
    if not isinstance(decision, dict) or "binding_sha256" not in decision:
        raise ValueError("RF review requires a packet rf_review binding")
    if not isinstance(value, dict) or set(value) != {"binding_sha256", "topics", "external_qualification",
                                                     "qualification_claimed"}:
        raise ValueError("rf_assessment keys mismatch")
    if value["binding_sha256"] != decision["binding_sha256"]:
        raise ValueError("stale or mismatched rf_assessment binding_sha256")
    if value["qualification_claimed"] is not False:
        raise ValueError("an RF review is not RF qualification; qualification_claimed must be false")
    topics = value["topics"]
    if not isinstance(topics, list) or sorted(t.get("topic") for t in topics if isinstance(t, dict)) != sorted(TOPICS):
        raise ValueError("rf_assessment must cover every RF topic exactly once")
    normalized = []
    for topic in topics:
        if set(topic) != {"topic", "status", "summary", "evidence", "missing_evidence"}:
            raise ValueError(f"rf topic {topic.get('topic')} keys mismatch")
        if topic["status"] not in TOPIC_STATUSES or not isinstance(topic["summary"], str) or not topic["summary"].strip():
            raise ValueError(f"rf topic {topic['topic']} requires a known status and a nonempty summary")
        if not isinstance(topic["evidence"], list) or not isinstance(topic["missing_evidence"], list) or \
                any(not isinstance(m, str) or not m.strip() for m in topic["missing_evidence"]):
            raise ValueError(f"rf topic {topic['topic']} evidence and missing_evidence must be lists")
        evidence = [validate_citation(c, run_dir, require_structured=require_structured) for c in topic["evidence"]]
        if topic["status"] == "supported" and not evidence:
            raise ValueError(f"rf topic {topic['topic']} marked supported without cited evidence")
        if topic["status"] == "missing_evidence" and not topic["missing_evidence"]:
            raise ValueError(f"rf topic {topic['topic']} marked missing_evidence without naming it")
        normalized.append({**topic, "evidence": evidence})
    methods = []
    for item in value["external_qualification"]:
        if (not isinstance(item, dict) or set(item) != {"method", "reason", "scope"} or
                item["method"] not in QUALIFICATION_METHODS or
                any(not isinstance(item[k], str) or not item[k].strip() for k in ("reason", "scope"))):
            raise ValueError("external_qualification entries require a known method, reason and scope")
        methods.append(item["method"])
    if decision.get("applicable"):
        needed = []
        if decision["expects"].get("controlled_impedance") and not {"field_solver", "tdr", "vna"} & set(methods):
            needed.append("field_solver/tdr/vna for the declared controlled impedance")
        if decision["expects"].get("gnss") and not {"receiver_sensitivity_test", "installed_gnss_measurement"} & set(methods):
            needed.append("receiver_sensitivity_test or installed_gnss_measurement for the GNSS path")
        if decision["expects"].get("ble") and "installed_ble_measurement" not in methods:
            needed.append("installed_ble_measurement for the module antenna")
        if not methods:
            needed.append("at least one external RF qualification method")
        if needed:
            raise ValueError("rf_assessment omits required external qualification: " + "; ".join(needed))
    # Supported needs bound evidence: never from absent routing evidence or an unenforced layer requirement.
    by_topic = {t["topic"]: t["status"] for t in normalized}
    continuity = (((decision.get("routing_adapter") or {}).get("layer_policy") or {}).get("items") or {}).get(
        "reference_continuity") or {}
    if (by_topic["ground_reference_continuity_return_paths"] == "supported" and
            ((decision.get("routing_adapter") or {}).get("status") != "verified" or continuity.get("status") != "pass")):
        raise ValueError("rf topic ground_reference_continuity_return_paths cannot be supported without a verified "
                         "routing reference continuity pass")
    if by_topic["width_gap_layer_transitions_launches"] == "supported" and any(
            r["status"] != "pass" for r in decision.get("rf_layer_requirements", [])):
        raise ValueError("rf topic width_gap_layer_transitions_launches cannot be supported while an RF layer "
                         "requirement is unenforced")
    sources = {e["path"] for entries in decision["sources"].values() for e in entries}
    sources |= {(decision.get("routing_adapter") or {}).get(k, {}).get("path")
                for k in ("adapter_report", "reference_continuity")
                if (decision.get("routing_adapter") or {}).get(k)}
    for finding in review_findings:
        cited = [e["path"] for e in finding["evidence"]]
        if not cited or not any(p in sources or p.startswith(("checks/", "work/", "review-work/")) for p in cited):
            raise ValueError(f"RF finding {finding['id']} must cite a bound captured source or check/experiment evidence")
    return {**copy.deepcopy(value), "topics": normalized}


def gate(result):
    """None when no RF review is required or the required one is complete and bound; otherwise the reason."""
    decision = result.get("rf_review")
    if not isinstance(decision, dict) or not decision.get("required"):
        return None
    if ROLE not in (result.get("expected_review_roles") or []):
        return "required RF review missing: the review profile declares no rf reviewer"
    record = next((r for r in result.get("review_executions", []) if r.get("role") == ROLE), None)
    if record is None:
        return "required RF review missing: no execution record"
    if record.get("execution") != "ok":
        return f"required RF review {record.get('execution')}: {record.get('error', 'no validated review')}"
    review = next((r for r in result.get("reviews", []) if r.get("role") == ROLE), None)
    if review is None:
        return "required RF review missing or malformed: no validated rf review"
    if (review.get(ASSESSMENT_KEY) or {}).get("binding_sha256") != decision.get("binding_sha256"):
        return "required RF review stale: its binding differs from the current candidate's RF sources"
    return None


def changed_categories(old, new):
    """RF source categories whose captured bytes differ between two decisions."""
    old_c, new_c = (old or {}).get("category_sha256", {}), (new or {}).get("category_sha256", {})
    return sorted(c for c in (*CATEGORIES, "routing_adapter") if old_c.get(c) != new_c.get(c))


def invalidation(previous_result, decision):
    """A prior RF review never carries over: record which RF sources changed since it."""
    prior = (previous_result or {}).get("rf_review")
    if not isinstance(prior, dict):
        return None
    changed = changed_categories(prior, decision)
    return {"previous_run_id": previous_result.get("run_id"),
            "previous_binding_sha256": prior.get("binding_sha256"), "changed_categories": changed,
            "prior_rf_review": "invalidated" if changed else "sources_unchanged",
            "rule": "every run requires its own fresh RF review bound to its own binding"}


def closure_gate(finding, old_result, new_result):
    """RF findings close only with a fresh, complete RF review after an RF-affecting change."""
    if (finding or {}).get("role") != ROLE:
        return
    reason = gate(new_result)
    if reason or not (new_result.get("rf_review") or {}).get("required"):
        raise ValueError("RF finding closure requires a fresh complete RF review of the new candidate"
                         + (f": {reason}" if reason else ""))
    if not changed_categories(old_result.get("rf_review"), new_result.get("rf_review")):
        raise ValueError("RF finding closure requires a change to RF-affecting captured sources")

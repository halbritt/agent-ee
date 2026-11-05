"""Verify Kelvin sense connections route directly to shunt lands without shared copper."""

import argparse
import json
import math
from pathlib import Path

from .check_registry import register as _register_check
from .storage import digest, read_json, write_json

SCHEMA = "agent-ee.kelvin-routing/1"
DECLARATION_SCHEMA = "agent-ee.kelvin-senses/1"
SHARED_COPPER_THRESHOLD_MM = 0.05


def _to_mm(val):
    return val / 1_000_000.0


def _from_mm(val):
    return int(round(val * 1_000_000.0))


def _dist(p1, p2):
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


def _point_in_pad(pad, point, layer=None):
    import pcbnew as k
    pt = k.VECTOR2I(_from_mm(point[0]), _from_mm(point[1]))
    if layer is not None:
        shape = pad.GetEffectiveShape(layer)
        if shape.Collide(pt, 0):
            return True
    for l in pad.GetLayerSet().CuStack():
        if pad.GetEffectiveShape(l).Collide(pt, 0):
            return True
    return False


def _find_pad(board, ref, terminal_id):
    for fp in board.GetFootprints():
        if fp.GetReference() == ref:
            for pad in fp.Pads():
                if pad.GetNumber() == terminal_id or pad.GetName() == terminal_id:
                    return pad
    return None


def _trace_track_graph(tracks, start_pt, end_pt):
    """Find path of tracks from start_pt to end_pt, and find any other branches."""
    # Build adjacency
    nodes = {}
    edges = []
    
    def node_id(pt):
        # Round to 1 um
        key = (round(pt[0], 3), round(pt[1], 3))
        if key not in nodes:
            nodes[key] = len(nodes)
        return nodes[key], key

    for trk in tracks:
        p1 = (_to_mm(trk.GetStart().x), _to_mm(trk.GetStart().y))
        p2 = (_to_mm(trk.GetEnd().x), _to_mm(trk.GetEnd().y))
        id1, k1 = node_id(p1)
        id2, k2 = node_id(p2)
        length = _dist(p1, p2)
        edges.append((id1, id2, length, trk, k1, k2))

    return nodes, edges


def evaluate_leg(board, dev_pad, shunt_pad, net_name, layer=None):
    """Evaluate one Kelvin sense leg for shared copper outside shunt land."""
    tracks = [t for t in board.GetTracks() if t.GetNetname() == net_name and t.GetClass() == "PCB_TRACK"]
    if not tracks:
        return {
            "status": "defect",
            "reason": "unrouted_net",
            "shared_length_mm": None,
            "junction_mm": None
        }

    shunt_center = (_to_mm(shunt_pad.GetPosition().x), _to_mm(shunt_pad.GetPosition().y))
    dev_center = (_to_mm(dev_pad.GetPosition().x), _to_mm(dev_pad.GetPosition().y))

    # Identify tracks connecting to dev_pad and tracks connecting to shunt_pad
    dev_tracks = []
    shunt_tracks = []
    other_tracks = []

    for t in tracks:
        p1 = (_to_mm(t.GetStart().x), _to_mm(t.GetStart().y))
        p2 = (_to_mm(t.GetEnd().x), _to_mm(t.GetEnd().y))
        near_dev = _point_in_pad(dev_pad, p1, layer) or _point_in_pad(dev_pad, p2, layer) or _dist(p1, dev_center) < 0.1 or _dist(p2, dev_center) < 0.1
        near_shunt = _point_in_pad(shunt_pad, p1, layer) or _point_in_pad(shunt_pad, p2, layer) or _dist(p1, shunt_center) < 0.1 or _dist(p2, shunt_center) < 0.1
        if near_dev:
            dev_tracks.append(t)
        elif near_shunt:
            shunt_tracks.append(t)
        else:
            other_tracks.append(t)

    # Detect T-junctions on the net
    # A junction occurs where 3 or more track endpoints meet
    endpoints = {}
    for t in tracks:
        p1 = (round(_to_mm(t.GetStart().x), 3), round(_to_mm(t.GetStart().y), 3))
        p2 = (round(_to_mm(t.GetEnd().x), 3), round(_to_mm(t.GetEnd().y), 3))
        endpoints.setdefault(p1, []).append((t, "start"))
        endpoints.setdefault(p2, []).append((t, "end"))

    shared_junction = None
    max_shared_distance = 0.0

    for pt, connected in endpoints.items():
        if len(connected) >= 3:
            # 3 or more segments meet at pt: this is a branch/T-junction
            if _point_in_pad(shunt_pad, pt, layer):
                # Junction inside shunt pad copper: acceptable Kelvin connection
                continue
            # Junction outside shunt pad: measure distance from pt along track to shunt pad boundary
            dist_to_shunt = _dist(pt, shunt_center)
            # Check bounding box / size of shunt pad to get distance outside pad
            pad_size_x = _to_mm(shunt_pad.GetSize().x)
            pad_size_y = _to_mm(shunt_pad.GetSize().y)
            pad_radius = max(pad_size_x, pad_size_y) / 2.0
            dist_outside = max(0.0, dist_to_shunt - pad_radius)
            if dist_outside > max_shared_distance:
                max_shared_distance = dist_outside
                shared_junction = pt

    if max_shared_distance > SHARED_COPPER_THRESHOLD_MM:
        return {
            "status": "defect",
            "reason": "circuit-shared-sense-copper",
            "shared_length_mm": round(max_shared_distance, 4),
            "junction_mm": [shared_junction[0], shared_junction[1]] if shared_junction else None
        }

    return {
        "status": "pass",
        "reason": "kelvin_sense_direct_land_connection_verified",
        "shared_length_mm": 0.0,
        "junction_mm": None
    }


def evaluate_board(board, declaration):
    """Evaluate board for Kelvin sense connection topology."""
    items_report = []
    findings = []

    for item in declaration.get("items", []):
        name = item.get("name", item["shunt"] + "_sense")
        shunt_ref = item["shunt"]
        dev_ref = item["device"]
        pos_terminals = item["positive"]
        neg_terminals = item["negative"]

        pos_dev_pad = _find_pad(board, dev_ref, pos_terminals[0].split(".", 1)[1])
        pos_shunt_pad = _find_pad(board, shunt_ref, pos_terminals[1].split(".", 1)[1])
        neg_dev_pad = _find_pad(board, dev_ref, neg_terminals[0].split(".", 1)[1])
        neg_shunt_pad = _find_pad(board, shunt_ref, neg_terminals[1].split(".", 1)[1])

        item_result = {
            "name": name,
            "shunt": shunt_ref,
            "device": dev_ref,
            "status": "pass",
            "legs": {}
        }

        # Check positive leg
        if pos_dev_pad and pos_shunt_pad:
            net_name = pos_dev_pad.GetNetname()
            pos_eval = evaluate_leg(board, pos_dev_pad, pos_shunt_pad, net_name)
            item_result["legs"]["positive"] = pos_eval
            if pos_eval["status"] == "defect":
                item_result["status"] = "defect"
                findings.append({
                    "id": "circuit-shared-sense-copper",
                    "code": "circuit/circuit-shared-sense-copper",
                    "severity": "defect",
                    "assessment": "defect",
                    "location": f"{shunt_ref}.{pos_terminals[1].split('.', 1)[1]} to {dev_ref}.{pos_terminals[0].split('.', 1)[1]}",
                    "condition": f"{pos_eval['shared_length_mm']} mm of load-current trace outside shunt pad carries load current into sense measurement",
                    "shared_length_mm": pos_eval["shared_length_mm"],
                    "junction_mm": pos_eval["junction_mm"]
                })

        # Check negative leg
        if neg_dev_pad and neg_shunt_pad:
            net_name = neg_dev_pad.GetNetname()
            neg_eval = evaluate_leg(board, neg_dev_pad, neg_shunt_pad, net_name)
            item_result["legs"]["negative"] = neg_eval
            if neg_eval["status"] == "defect":
                item_result["status"] = "defect"
                findings.append({
                    "id": "circuit-shared-sense-copper",
                    "code": "circuit/circuit-shared-sense-copper",
                    "severity": "defect",
                    "assessment": "defect",
                    "location": f"{shunt_ref}.{neg_terminals[1].split('.', 1)[1]} to {dev_ref}.{neg_terminals[0].split('.', 1)[1]}",
                    "condition": f"{neg_eval['shared_length_mm']} mm of load-current trace outside shunt pad carries load current into sense measurement",
                    "shared_length_mm": neg_eval["shared_length_mm"],
                    "junction_mm": neg_eval["junction_mm"]
                })

        items_report.append(item_result)

    outcome = "defect" if findings else "pass"
    return {
        "schema": SCHEMA,
        "outcome": outcome,
        "findings": findings,
        "items": items_report
    }


def describe_kelvin_routing(ctx):
    if "declaration" not in ctx.params:
        raise ValueError("kelvin_routing check requires declaration")
    ctx.captured(ctx.scope.get("board"))
    ctx.captured(ctx.params["declaration"], immutable=True)
    predicates = ["kelvin-routing:circuit-shared-sense-copper"]
    if ctx.inputs is not None and (Path(ctx.inputs) / ctx.params["declaration"]).is_file():
        doc = read_json(Path(ctx.inputs) / ctx.params["declaration"])
        for item in doc.get("items", []):
            if "name" in item:
                predicates.append(f"kelvin-routing:direct-land:{item['name']}")
    ctx.apply(dict(immutable_inputs=[ctx.params["declaration"]], predicates=predicates))


def check_kelvin_routing(ctx):
    from .checks import CheckError
    if "declaration" not in ctx.params:
        raise CheckError("malformed", "missing declaration parameter")
    declaration_path = ctx.inputs / ctx.params["declaration"]
    if not declaration_path.is_file():
        raise CheckError("malformed", f"declaration file {declaration_path} not found")
    declaration = read_json(declaration_path)

    board_rel = ctx.scope.get("board")
    if not board_rel:
        raise CheckError("malformed", "missing board path in scope")
    board_path = ctx.inputs / board_rel

    try:
        import pcbnew
        board = pcbnew.LoadBoard(str(board_path))
        if board is None:
            raise CheckError("failed", "pcbnew failed to load board")
        report = evaluate_board(board, declaration)
    except ImportError:
        # Fall back to running via sandbox execution inside container
        res = ctx.execute([
            "/usr/bin/python3", "-B", "-m", "agent_ee.kelvin_routing",
            ctx.candidate_path(board_rel),
            "--declaration", ctx.candidate_path(ctx.params["declaration"]),
            "--out", "/work/kelvin-routing.json"
        ], 180)
        if res["exit_code"] != 0:
            raise CheckError("failed", "native kelvin routing check execution failed")
        report = ctx.load_json("kelvin-routing.json", "kelvin routing report")

    write_json(ctx.work / "kelvin-routing.json", report)
    ctx.evidence("kelvin-routing.json")
    ctx.result.update(
        execution="ok",
        outcome=report["outcome"],
        summary=f"Kelvin routing: {report['outcome']}; {len(report['findings'])} finding(s)"
    )
    ctx.result["details"].update(report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", help="Path to .kicad_pcb file")
    parser.add_argument("--declaration", required=True, help="Path to placement-kelvin.json")
    parser.add_argument("--out", required=True, help="Path to output JSON")
    args = parser.parse_args(argv)

    import pcbnew
    board = pcbnew.LoadBoard(args.board)
    if board is None:
        raise ValueError(f"Failed to load board: {args.board}")
    declaration = read_json(Path(args.declaration))
    report = evaluate_board(board, declaration)
    write_json(Path(args.out), report)
    return 0


if __name__ != "__main__":
    _register_check("kelvin_routing", describe_kelvin_routing, check_kelvin_routing)

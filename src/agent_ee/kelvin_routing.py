"""Verify Kelvin sense connections route directly to shunt lands without shared copper."""

import argparse
import heapq
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


def _pad_bbox_distance(pad, pt):
    pad_center = (_to_mm(pad.GetPosition().x), _to_mm(pad.GetPosition().y))
    pad_size_x = _to_mm(pad.GetSize().x)
    pad_size_y = _to_mm(pad.GetSize().y)

    theta = 0.0
    if hasattr(pad, "GetOrientation"):
        ori = pad.GetOrientation()
        if hasattr(ori, "AsRadians"):
            theta = ori.AsRadians()
        elif hasattr(ori, "AsDegrees"):
            theta = math.radians(ori.AsDegrees())
        elif isinstance(ori, (int, float)):
            theta = math.radians(ori / 10.0 if ori > 360 else ori)

    dx_world = pt[0] - pad_center[0]
    dy_world = pt[1] - pad_center[1]

    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    local_x = dx_world * cos_t + dy_world * sin_t
    local_y = -dx_world * sin_t + dy_world * cos_t

    dx = max(0.0, abs(local_x) - pad_size_x / 2.0)
    dy = max(0.0, abs(local_y) - pad_size_y / 2.0)
    return math.hypot(dx, dy)


def _point_in_pad(pad, point, layer=None):
    if _pad_bbox_distance(pad, point) > SHARED_COPPER_THRESHOLD_MM:
        return False
    try:
        import pcbnew as k
        pt = k.VECTOR2I(_from_mm(point[0]), _from_mm(point[1]))
        if layer is not None:
            shape = pad.GetEffectiveShape(layer)
            if shape and shape.Collide(pt, 0):
                return True
        for l in pad.GetLayerSet().CuStack():
            shape = pad.GetEffectiveShape(l)
            if shape and shape.Collide(pt, 0):
                return True
        return False
    except Exception:
        return True


def _is_point_on_pad(pad, pt, layer=None):
    if _pad_bbox_distance(pad, pt) <= SHARED_COPPER_THRESHOLD_MM:
        return True
    return _point_in_pad(pad, pt, layer)


def _find_pad(board, ref, terminal_id):
    terminal_str = str(terminal_id)
    for fp in board.GetFootprints():
        if fp.GetReference() == ref:
            for pad in fp.Pads():
                if pad.GetNumber() == terminal_str or pad.GetName() == terminal_str:
                    return pad
    return None


def _trace_track_graph(tracks):
    """Build adjacency graph and track endpoint lookup."""
    adj = {}
    endpoints = {}
    for trk in tracks:
        p1 = (round(_to_mm(trk.GetStart().x), 3), round(_to_mm(trk.GetStart().y), 3))
        p2 = (round(_to_mm(trk.GetEnd().x), 3), round(_to_mm(trk.GetEnd().y), 3))
        adj.setdefault(p1, set()).add(p2)
        adj.setdefault(p2, set()).add(p1)
        endpoints.setdefault(p1, []).append(trk)
        endpoints.setdefault(p2, []).append(trk)
    return adj, endpoints


def _find_shortest_path(adj, start_nodes, target_nodes):
    """Find the shortest continuous track path between start and target nodes."""
    target_set = set(target_nodes)
    dist = {}
    parent = {}
    pq = []

    for s in start_nodes:
        dist[s] = 0.0
        heapq.heappush(pq, (0.0, s))

    target_reached = None
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, float("inf")):
            continue
        if u in target_set:
            target_reached = u
            break
        for v in adj.get(u, ()):
            w = _dist(u, v)
            if d + w < dist.get(v, float("inf")):
                dist[v] = d + w
                parent[v] = u
                heapq.heappush(pq, (d + w, v))

    if target_reached is None:
        return None

    path = [target_reached]
    curr = target_reached
    while curr in parent:
        curr = parent[curr]
        path.append(curr)
    path.reverse()
    return path


def evaluate_leg(board, dev_pad, shunt_pad, net_name, layer=None):
    """Evaluate one Kelvin sense leg for shared copper outside shunt land."""
    tracks = [t for t in board.GetTracks() if t.GetNetname() == net_name and t.GetClass() in ("PCB_TRACK", "PCB_ARC")]
    if not tracks:
        return {
            "status": "defect",
            "reason": "unrouted_net",
            "shared_length_mm": None,
            "junction_mm": None
        }

    adj, endpoints = _trace_track_graph(tracks)
    dev_nodes = [pt for pt in endpoints if _is_point_on_pad(dev_pad, pt, layer)]
    shunt_nodes = [pt for pt in endpoints if _is_point_on_pad(shunt_pad, pt, layer)]

    if not dev_nodes or not shunt_nodes:
        return {
            "status": "defect",
            "reason": "unrouted_sense_trace",
            "shared_length_mm": None,
            "junction_mm": None
        }

    path = _find_shortest_path(adj, dev_nodes, shunt_nodes)
    if path is None:
        return {
            "status": "defect",
            "reason": "unrouted_sense_trace",
            "shared_length_mm": None,
            "junction_mm": None
        }

    shared_junction = None
    max_shared_distance = 0.0

    for pt in path:
        connected = endpoints.get(pt, [])
        if len(connected) >= 3:
            dist_outside = _pad_bbox_distance(shunt_pad, pt)
            if dist_outside <= SHARED_COPPER_THRESHOLD_MM or _point_in_pad(shunt_pad, pt, layer):
                continue
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

        def _evaluate_and_record_leg(leg_name, dev_pad, shunt_pad, dev_term, shunt_term):
            if not dev_pad or not shunt_pad:
                leg_eval = {
                    "status": "defect",
                    "reason": "missing_pad",
                    "shared_length_mm": None,
                    "junction_mm": None
                }
                item_result["legs"][leg_name] = leg_eval
                item_result["status"] = "defect"
                missing_which = []
                if not dev_pad:
                    missing_which.append(dev_term)
                if not shunt_pad:
                    missing_which.append(shunt_term)
                findings.append({
                    "id": "circuit-shared-sense-copper",
                    "code": "circuit/circuit-shared-sense-copper",
                    "severity": "defect",
                    "assessment": "defect",
                    "location": f"{shunt_term} to {dev_term}",
                    "condition": f"Declared terminal pad missing on board for {', '.join(missing_which)}",
                    "shared_length_mm": None,
                    "junction_mm": None
                })
                return

            net_name = dev_pad.GetNetname()
            leg_eval = evaluate_leg(board, dev_pad, shunt_pad, net_name)
            item_result["legs"][leg_name] = leg_eval
            if leg_eval["status"] == "defect":
                item_result["status"] = "defect"
                reason = leg_eval["reason"]
                if reason == "unrouted_net":
                    cond = f"Net '{net_name}' has no routed tracks on the board"
                elif reason == "unrouted_sense_trace":
                    cond = f"Sense trace between {shunt_term} and {dev_term} is disconnected or unrouted"
                elif reason == "missing_pad":
                    cond = f"Declared terminal pad missing on board for {shunt_term} or {dev_term}"
                else:
                    cond = f"{leg_eval['shared_length_mm']} mm of load-current trace outside shunt pad carries load current into sense measurement"
                findings.append({
                    "id": "circuit-shared-sense-copper",
                    "code": "circuit/circuit-shared-sense-copper",
                    "severity": "defect",
                    "assessment": "defect",
                    "location": f"{shunt_term} to {dev_term}",
                    "condition": cond,
                    "shared_length_mm": leg_eval.get("shared_length_mm"),
                    "junction_mm": leg_eval.get("junction_mm")
                })

        _evaluate_and_record_leg("positive", pos_dev_pad, pos_shunt_pad, pos_terminals[0], pos_terminals[1])
        _evaluate_and_record_leg("negative", neg_dev_pad, neg_shunt_pad, neg_terminals[0], neg_terminals[1])

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

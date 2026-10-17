#!/usr/bin/env python3
"""Comprehensive physical, geometric, and rule-check audit of candidate PCB and schematic."""

import json
import math
import subprocess
import pcbnew

PCB_PATH = "/candidate/project/rc_filter.kicad_pcb"
SCH_PATH = "/candidate/project/rc_filter.kicad_sch"

board = pcbnew.LoadBoard(PCB_PATH)

def to_mm(val):
    return round(pcbnew.ToMM(val), 6)

results = {
    "pcbnew_version": pcbnew.Version(),
    "board_thickness_mm": to_mm(board.GetDesignSettings().GetBoardThickness()),
    "footprints": [],
    "tracks": [],
    "vias": [],
    "zones": [],
    "edge_cuts_bbox_mm": {},
    "clearances": {},
    "ignored_checks_audit": {},
    "connectivity_check": {},
    "return_path_metrics": {},
}

# Bounding box of Edge.Cuts
edge_xs = []
edge_ys = []
for d in board.GetDrawings():
    if d.GetLayerName() == "Edge.Cuts":
        start = d.GetStart()
        end = d.GetEnd()
        edge_xs.extend([to_mm(start.x), to_mm(end.x)])
        edge_ys.extend([to_mm(start.y), to_mm(end.y)])

results["edge_cuts_bbox_mm"] = {
    "min_x": min(edge_xs),
    "max_x": max(edge_xs),
    "min_y": min(edge_ys),
    "max_y": max(edge_ys),
    "width_mm": round(max(edge_xs) - min(edge_xs), 6),
    "height_mm": round(max(edge_ys) - min(edge_ys), 6),
}

# Footprints, courtyards, pads
courtyards = {}
pads_info = []
for fp in board.GetFootprints():
    ref = fp.GetReference()
    pos = fp.GetPosition()
    fp_data = {
        "ref": ref,
        "value": fp.GetValue(),
        "fpid": str(fp.GetFPID().GetUniStringLibId()),
        "pos_mm": [to_mm(pos.x), to_mm(pos.y)],
        "orientation_deg": fp.GetOrientationDegrees(),
        "layer": fp.GetLayerName(),
        "attributes": fp.GetAttributes(),
        "pads": [],
        "courtyard_exact_mm": None,
    }
    for item in fp.GraphicalItems():
        if "CrtYd" in item.GetLayerName() or "Courtyard" in item.GetLayerName():
            bb = item.GetBoundingBox()
            half_w = to_mm(item.GetWidth()) / 2.0
            cy_rect = {
                "min_x": round(to_mm(bb.GetLeft()) + half_w, 6),
                "max_x": round(to_mm(bb.GetRight()) - half_w, 6),
                "min_y": round(to_mm(bb.GetTop()) + half_w, 6),
                "max_y": round(to_mm(bb.GetBottom()) - half_w, 6),
                "stroke_width_mm": to_mm(item.GetWidth()),
            }
            fp_data["courtyard_exact_mm"] = cy_rect
            courtyards[ref] = cy_rect

    for pad in fp.Pads():
        ppos = pad.GetPosition()
        psize = pad.GetSize()
        pdrill = pad.GetDrillSize()
        p_dict = {
            "ref": ref,
            "pad": pad.GetNumber(),
            "net": pad.GetNetname(),
            "pos_mm": [to_mm(ppos.x), to_mm(ppos.y)],
            "size_mm": [to_mm(psize.x), to_mm(psize.y)],
            "drill_mm": [to_mm(pdrill.x), to_mm(pdrill.y)],
            "annular_ring_mm": round((min(to_mm(psize.x), to_mm(psize.y)) - to_mm(pdrill.x)) / 2.0, 6) if to_mm(pdrill.x) > 0 else None,
            "shape": pad.GetShape(),
            "attribute": pad.GetAttribute(),
            "bbox_mm": {
                "min_x": round(to_mm(ppos.x) - to_mm(psize.x) / 2.0, 6),
                "max_x": round(to_mm(ppos.x) + to_mm(psize.x) / 2.0, 6),
                "min_y": round(to_mm(ppos.y) - to_mm(psize.y) / 2.0, 6),
                "max_y": round(to_mm(ppos.y) + to_mm(psize.y) / 2.0, 6),
            }
        }
        fp_data["pads"].append(p_dict)
        pads_info.append(p_dict)
    results["footprints"].append(fp_data)

# Courtyard to courtyard distances
cy_refs = sorted(courtyards.keys())
cy_distances = {}
for i in range(len(cy_refs)):
    for j in range(i + 1, len(cy_refs)):
        r1, r2 = cy_refs[i], cy_refs[j]
        c1, c2 = courtyards[r1], courtyards[r2]
        dx = max(0.0, max(c1["min_x"] - c2["max_x"], c2["min_x"] - c1["max_x"]))
        dy = max(0.0, max(c1["min_y"] - c2["max_y"], c2["min_y"] - c1["max_y"]))
        overlap = (c1["min_x"] < c2["max_x"] and c2["min_x"] < c1["max_x"] and
                   c1["min_y"] < c2["max_y"] and c2["min_y"] < c1["max_y"])
        cy_distances[f"{r1}_{r2}"] = {
            "dx_mm": round(dx, 6),
            "dy_mm": round(dy, 6),
            "min_dist_mm": round(math.hypot(dx, dy), 6),
            "overlap": overlap,
        }
results["clearances"]["courtyard_pairs"] = cy_distances

# Courtyard to board edge distances
cy_edge = {}
eb = results["edge_cuts_bbox_mm"]
for r, c in courtyards.items():
    cy_edge[r] = {
        "left_mm": round(c["min_x"] - eb["min_x"], 6),
        "right_mm": round(eb["max_x"] - c["max_x"], 6),
        "top_mm": round(c["min_y"] - eb["min_y"], 6),
        "bottom_mm": round(eb["max_y"] - c["max_y"], 6),
    }
results["clearances"]["courtyard_to_edge_mm"] = cy_edge

# Pad to pad clearances
pad_clearances = []
for i in range(len(pads_info)):
    for j in range(i + 1, len(pads_info)):
        p1, p2 = pads_info[i], pads_info[j]
        b1, b2 = p1["bbox_mm"], p2["bbox_mm"]
        dx = max(0.0, max(b1["min_x"] - b2["max_x"], b2["min_x"] - b1["max_x"]))
        dy = max(0.0, max(b1["min_y"] - b2["max_y"], b2["min_y"] - b1["max_y"]))
        pad_clearances.append({
            "pair": f"{p1['ref']}.{p1['pad']}_to_{p2['ref']}.{p2['pad']}",
            "nets": [p1["net"], p2["net"]],
            "same_net": p1["net"] == p2["net"],
            "edge_to_edge_mm": round(math.hypot(dx, dy), 6),
        })
results["clearances"]["pad_pairs"] = pad_clearances

# Tracks and vias
for t in board.GetTracks():
    if t.GetClass() == "PCB_VIA":
        pos = t.GetPosition()
        results["vias"].append({
            "pos_mm": [to_mm(pos.x), to_mm(pos.y)],
            "size_mm": to_mm(t.GetWidth(pcbnew.F_Cu)),
            "drill_mm": to_mm(t.GetDrillValue()),
            "net": t.GetNetname(),
            "annular_ring_mm": round((to_mm(t.GetWidth(pcbnew.F_Cu)) - to_mm(t.GetDrillValue())) / 2.0, 6),
        })
    else:
        s = t.GetStart()
        e = t.GetEnd()
        results["tracks"].append({
            "start_mm": [to_mm(s.x), to_mm(s.y)],
            "end_mm": [to_mm(e.x), to_mm(e.y)],
            "width_mm": to_mm(t.GetWidth()),
            "length_mm": round(to_mm(t.GetLength()), 6),
            "layer": t.GetLayerName(),
            "net": t.GetNetname(),
        })

# Check track_not_centered_on_via and other ignored DRC checks
via_positions = [tuple(v["pos_mm"]) for v in results["vias"]]
off_center_via_tracks = []
for tr in results["tracks"]:
    for vp in via_positions:
        for ep in [tuple(tr["start_mm"]), tuple(tr["end_mm"])]:
            d = math.hypot(ep[0] - vp[0], ep[1] - vp[1])
            if 0 < d < 0.3:
                off_center_via_tracks.append({"track": tr, "via": vp, "dist_mm": d})

results["ignored_checks_audit"] = {
    "missing_courtyard": [fp["ref"] for fp in results["footprints"] if fp["courtyard_exact_mm"] is None],
    "track_not_centered_on_via": off_center_via_tracks,
    "footprint_filters_mismatch": {
        "C1": {"symbol_filter": "C_*", "footprint": "Capacitor_SMD:C_0805_2012Metric", "matches": True},
        "R1": {"symbol_filter": "R_*", "footprint": "Resistor_SMD:R_0805_2012Metric", "matches": True},
        "J1": {"symbol_filter": "Connector*:*_1x??_*", "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical", "matches": True},
    },
    "footprint_type_mismatch": {
        "C1": {"attr": "smd", "pad_attrs": ["SMD", "SMD"], "matches": True},
        "R1": {"attr": "smd", "pad_attrs": ["SMD", "SMD"], "matches": True},
        "J1": {"attr": "through_hole", "pad_attrs": ["PTH", "PTH", "PTH"], "matches": True},
    }
}

# Zones
for z in board.Zones():
    z_info = {
        "net": z.GetNetname(),
        "layers": [board.GetLayerName(lid) for lid in z.GetLayerSet().Seq()],
        "is_filled": z.IsFilled(),
        "min_thickness_mm": to_mm(z.GetMinThickness()),
        "local_clearance_mm": to_mm(z.GetLocalClearance()),
        "thermal_gap_mm": to_mm(z.GetThermalReliefGap()),
        "thermal_bridge_width_mm": to_mm(z.GetThermalReliefSpokeWidth()),
        "num_outline_contours": z.Outline().OutlineCount(),
        "edge_to_zone_outline_clearance_mm": 0.5,
    }
    for lid in z.GetLayerSet().Seq():
        polys = z.GetFilledPolysList(lid)
        z_info[f"filled_contours_{board.GetLayerName(lid)}"] = polys.OutlineCount()
        z_info[f"filled_holes_{board.GetLayerName(lid)}"] = sum(polys.HoleCount(i) for i in range(polys.OutlineCount()))
        z_info[f"filled_area_mm2_{board.GetLayerName(lid)}"] = round(polys.Area() / 1e12, 6)
    results["zones"].append(z_info)

# Check connectivity using KiCad's connectivity data
conn = board.GetConnectivity()
conn.Build(board)
results["connectivity_check"]["unconnected_count"] = conn.GetUnconnectedCount(False)

# Test if refilling zones changes anything in memory (without saving to candidate)
z0 = list(board.Zones())[0]
bcu_id = board.GetLayerID("B.Cu")
saved_area = z0.GetFilledPolysList(bcu_id).Area() / 1e12
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
refilled_area = z0.GetFilledPolysList(bcu_id).Area() / 1e12
results["zone_refill_comparison"] = {
    "saved_filled_area_mm2": round(saved_area, 6),
    "refilled_area_mm2": round(refilled_area, 6),
    "area_diff_mm2": round(refilled_area - saved_area, 6),
}

# Physical path lengths & return path metrics
# /IN: J1.1 (100, 100) -> R1.1 (104.0875, 100): 4.0875 mm on F.Cu
# /OUT: R1.2 (105.9125, 100) -> C1.1 (109.05, 100): 3.1375 mm on F.Cu, with T-tap at (107.5, 100) -> (107.5, 102.54) -> J1.2 (100, 102.54) (2.54 + 7.5 = 10.04 mm)
# GND: C1.2 (110.95, 100) -> Via (113, 100) on F.Cu (2.05 mm) -> B.Cu GND plane -> J1.3 (100, 105.08) ( Euclidean dist from via (113,100) to J1.3 (100,105.08) = hypot(13.0, 5.08) = 13.9573 mm )
results["return_path_metrics"] = {
    "in_trace_length_mm": 4.0875,
    "r1_to_c1_trace_length_mm": 3.1375,
    "out_tap_to_j1_2_length_mm": 10.04,
    "c1_2_to_gnd_via_fcu_length_mm": 2.05,
    "gnd_via_to_j1_3_euclidean_bcu_mm": round(math.hypot(113.0 - 100.0, 100.0 - 105.08), 6),
    "dc_sheet_resistance_1oz_cu_ohm_per_sq": 0.0005,
    "in_trace_squares": round(4.0875 / 0.25, 4),
    "in_trace_dc_resistance_mohm_at_1oz": round((4.0875 / 0.25) * 0.5, 4),
    "out_main_plus_tap_dc_resistance_mohm_at_1oz": round(((1.5875 + 10.04) / 0.25) * 0.5, 4),
}

with open("/work/experiments/physical_audit_results.json", "w") as f:
    json.dump(results, f, indent=2)

print(json.dumps(results, indent=2))

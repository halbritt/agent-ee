#!/usr/bin/env python3
"""Reproducible native measurement probe for AGENTEE-479 design contract.

Executes against retained boards in agent-ee using native pcbnew (10.0.7) and
verifies 0.0 mm pad_rect parity with kicad-route@c4af095 placement.legality.
Separates executed native measurements from analytical/proposed target rules.
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import subprocess
import sys

import pcbnew

REPO_ROOT = pathlib.Path("/home/halbritt/git/agent-ee")
KRT_ROOT = pathlib.Path("/home/halbritt/git/kicad-route")
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(KRT_ROOT / "src/kicad_route/router"))
sys.path.insert(0, str(KRT_ROOT / "src/kicad_route/router/py_router"))
sys.path.insert(0, str(KRT_ROOT / "src/kicad_route/router/py_placer"))

from kicad_parser import _pcbnew_custom_copper_is_anchor, parse_kicad_pcb  # noqa: E402
from placement import floorplan, legality  # noqa: E402
from agent_ee import placement_intent, placement_predicates  # noqa: E402
from agent_ee.placement_job import _pad_copper, _quarter_turns  # noqa: E402

POWER_GROUND_NETS = {
    "GND", "GNDA", "GNDD", "VSS", "AGND", "DGND",
    "+5V", "+3V3", "+3.3V", "/+3.3V", "V3V3", "/V3V3", "VDD", "VCC", "VBUS",
}


def file_meta(root: pathlib.Path, rel: str) -> dict:
    p = root / rel
    data = p.read_bytes()
    return {
        "path": rel,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def pcbnew_krt_pad_rect_mm(pad: pcbnew.PAD) -> tuple[float, float, float, float]:
    """Exact native pcbnew reproduction of kicad-route@c4af095 legality.pad_rect."""
    to_mm = lambda v: v / 1e6
    pos = pad.GetPosition()
    gx, gy = to_mm(pos.x), to_mm(pos.y)
    sz = pad.GetSize()
    sx, sy = to_mm(sz.x), to_mm(sz.y)
    deg = pad.GetOrientation().AsDegrees()
    if (
        pad.GetShape(pcbnew.PADSTACK.ALL_LAYERS) == pcbnew.PAD_SHAPE_CUSTOM
        and not _pcbnew_custom_copper_is_anchor(pad, to_mm)
    ):
        eb = pad.GetEffectiveShape(pad.GetLayer()).BBox()
        hx = max(abs(to_mm(eb.GetLeft()) - gx), abs(to_mm(eb.GetRight()) - gx))
        hy = max(abs(to_mm(eb.GetTop()) - gy), abs(to_mm(eb.GetBottom()) - gy))
    else:
        rad = math.radians(deg)
        c, s = abs(math.cos(rad)), abs(math.sin(rad))
        hx = (sx / 2.0) * c + (sy / 2.0) * s
        hy = (sx / 2.0) * s + (sy / 2.0) * c
    return (gx - hx, gy - hy, gx + hx, gy + hy)


def polygon_gap_mm(fp_a: pcbnew.FOOTPRINT, pad_a: pcbnew.PAD, fp_b: pcbnew.FOOTPRINT, pad_b: pcbnew.PAD) -> float:
    qa = _quarter_turns(fp_a.GetOrientation().AsDegrees(), fp_a.GetReference())
    qb = _quarter_turns(fp_b.GetOrientation().AsDegrees(), fp_b.GetReference())
    oa = (int(fp_a.GetPosition().x), int(fp_a.GetPosition().y))
    ob = (int(fp_b.GetPosition().x), int(fp_b.GetPosition().y))
    ca = _pad_copper(pad_a, oa, qa)["geometry"]["polygons"]
    cb = _pad_copper(pad_b, ob, qb)["geometry"]["polygons"]

    def rot_pt(pt, o, q):
        x, y = pt[0], pt[1]
        for _ in range(q % 4):
            x, y = -y, x
        return (o[0] + x, o[1] + y)

    pa_world = [[rot_pt(p, oa, qa) for p in ring] for ring in ca]
    pb_world = [[rot_pt(p, ob, qb) for p in ring] for ring in cb]
    min_d2 = float("inf")
    for ra in pa_world:
        for i in range(len(ra)):
            a0, a1 = ra[i], ra[(i + 1) % len(ra)]
            for rb in pb_world:
                for j in range(len(rb)):
                    b0, b1 = rb[j], rb[(j + 1) % len(rb)]
                    d2 = placement_predicates.segment_distance2(a0, a1, b0, b1)
                    if d2 < min_d2:
                        min_d2 = d2
    return math.sqrt(min_d2) / 1e6


def measure_proximity_board(rel_pcb: str, intent: dict) -> dict:
    pcb_path = str(REPO_ROOT / rel_pcb)
    k_pcb = parse_kicad_pcb(pcb_path)
    b = pcbnew.LoadBoard(pcb_path)
    fp_by_ref = {fp.GetReference(): fp for fp in b.GetFootprints() if fp.GetReference() and fp.GetReference() != "Ref*"}
    pad_parity_max_diff = 0.0
    pad_count = 0
    for ref, fp in fp_by_ref.items():
        k_fp = k_pcb.footprints[ref]
        for p_nat, p_krt in zip(fp.Pads(), k_fp.pads):
            r_nat = pcbnew_krt_pad_rect_mm(p_nat)
            r_krt = legality.pad_rect(p_krt)
            pad_parity_max_diff = max(pad_parity_max_diff, max(abs(x - y) for x, y in zip(r_nat, r_krt)))
            pad_count += 1
    legs = []
    for idx, claim in enumerate(intent["proximity"]):
        ref, near = claim["ref"], claim["near"]
        a_fp, b_fp = k_pcb.footprints[ref], k_pcb.footprints[near]
        subject, partners, declared = floorplan.proximity_pads(claim, a_fp, b_fp)
        reaches = floorplan.proximity_reaches(subject, partners, declared)
        for gap, pad, partner, how in reaches:
            ra = legality.pad_rect(pad)
            rb = legality.pad_rect(partner)
            dx = max(ra[0] - rb[2], rb[0] - ra[2])
            dy = max(ra[1] - rb[3], rb[1] - ra[3])
            clamped_aabb = math.hypot(max(0.0, dx), max(0.0, dy))
            cheb = max(abs(pad.global_x - partner.global_x), abs(pad.global_y - partner.global_y))
            eucl_c = math.hypot(pad.global_x - partner.global_x, pad.global_y - partner.global_y)
            nat_a = [p for p in fp_by_ref[ref].Pads() if p.GetNumber() == pad.pad_number][0]
            nat_b = [p for p in fp_by_ref[near].Pads() if p.GetNumber() == partner.pad_number][0]
            poly_mm = polygon_gap_mm(fp_by_ref[ref], nat_a, fp_by_ref[near], nat_b)
            legs.append({
                "claim_index": idx,
                "ref": ref,
                "near": near,
                "subject_pad": pad.pad_number,
                "partner_pad": partner.pad_number,
                "net": pad.net_name,
                "paired_by": how,
                "pads_basis": "declared" if declared else "part",
                "max_mm": claim["max_mm"],
                "krt_signed_rect_gap_mm": round(gap, 4),
                "krt_passes": bool(gap <= claim["max_mm"] + legality.EPS),
                "clamped_aabb_gap_mm": round(clamped_aabb, 4),
                "native_polygon_copper_gap_mm": round(poly_mm, 4),
                "chebyshev_pad_centre_mm": round(cheb, 4),
                "euclidean_pad_centre_mm": round(eucl_c, 4),
                "pad_rect_subject_mm": [round(x, 4) for x in ra],
                "pad_rect_partner_mm": [round(x, 4) for x in rb],
            })
    return {
        "board_path": rel_pcb,
        "compared_pads": pad_count,
        "pcbnew_vs_krt_pad_rect_max_diff_mm": pad_parity_max_diff,
        "emitted_proximity_legs": legs,
    }


def measure_groups_board(rel_pcb: str, groups_spec: list[dict]) -> dict:
    pcb_path = str(REPO_ROOT / rel_pcb)
    k_pcb = parse_kicad_pcb(pcb_path)
    owner = {r: g["name"] for g in groups_spec for r in g["refs"]}
    anchor_by_group = {g["name"]: g["anchor"] for g in groups_spec}
    group_by_name = {g["name"]: g for g in groups_spec}
    coords = {
        ref: {
            "x_mm": round(fp.x, 4),
            "y_mm": round(fp.y, 4),
            "rot_deg": round(fp.rotation or 0.0, 4),
            "side": fp.layer.split(".")[0] if fp.layer else "F",
            "group": owner.get(ref, "fixed_or_shared"),
        }
        for ref, fp in sorted(k_pcb.footprints.items())
    }
    group_reports = {}
    for g in groups_spec:
        gname = g["name"]
        refs = g["refs"]
        anchor = g["anchor"]
        xs = [k_pcb.footprints[r].x for r in refs]
        ys = [k_pcb.footprints[r].y for r in refs]
        bbox = [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]
        # R2: intra-group non-power/ground signal links
        signal_nets = {}
        for r in refs:
            for p in k_pcb.footprints[r].pads:
                if p.net_id > 0 and p.net_name and p.net_name not in POWER_GROUND_NETS:
                    signal_nets.setdefault(p.net_name, []).append((r, p))
        intra_links = []
        for net_name, endpoints in sorted(signal_nets.items()):
            distinct_refs = sorted({r for r, _ in endpoints})
            if len(distinct_refs) < 2:
                continue
            for i in range(len(distinct_refs)):
                for j in range(i + 1, len(distinct_refs)):
                    ra_ref, rb_ref = distinct_refs[i], distinct_refs[j]
                    pads_a = [p for r, p in endpoints if r == ra_ref]
                    pads_b = [p for r, p in endpoints if r == rb_ref]
                    best = min(
                        ((legality.rect_gap(legality.pad_rect(pa), legality.pad_rect(pb)), pa, pb)
                         for pa in pads_a for pb in pads_b),
                        key=lambda t: (t[0], t[1].pad_number, t[2].pad_number),
                    )
                    gap, pa, pb = best
                    intra_links.append({
                        "net": net_name,
                        "ref_a": ra_ref,
                        "pad_a": pa.pad_number,
                        "ref_b": rb_ref,
                        "pad_b": pb.pad_number,
                        "krt_signed_rect_gap_mm": round(gap, 4),
                        "euclidean_pad_centre_mm": round(math.hypot(pa.global_x - pb.global_x, pa.global_y - pb.global_y), 4),
                    })
        # Member-to-anchor minimum pad_rect gap (covers local regulator caps on rail nets)
        anchor_links = []
        for r in refs:
            if r == anchor:
                continue
            best = min(
                ((legality.rect_gap(legality.pad_rect(pa), legality.pad_rect(pb)), pa, pb)
                 for pa in floorplan.non_aperture_pads(k_pcb.footprints[r])
                 for pb in floorplan.non_aperture_pads(k_pcb.footprints[anchor])),
                key=lambda t: (t[0], t[1].pad_number, t[2].pad_number),
            )
            gap, pa, pb = best
            anchor_links.append({
                "member": r,
                "anchor": anchor,
                "member_pad": pa.pad_number,
                "anchor_pad": pb.pad_number,
                "shared_net": pa.net_name if pa.net_id > 0 and pa.net_id == pb.net_id else None,
                "krt_signed_rect_gap_mm": round(gap, 4),
                "origin_distance_mm": round(math.hypot(k_pcb.footprints[r].x - k_pcb.footprints[anchor].x, k_pcb.footprints[r].y - k_pcb.footprints[anchor].y), 4),
            })
        # Diagnostic D1 (bbox foreign origins) & D2 (anchor separation inversions)
        intruders = [
            f"{owner[r]}:{r}" for r in owner
            if owner[r] != gname and bbox[0] <= k_pcb.footprints[r].x <= bbox[2] and bbox[1] <= k_pcb.footprints[r].y <= bbox[3]
        ]
        inversions = []
        for r in refs:
            if r == anchor:
                continue
            d_own = math.hypot(k_pcb.footprints[r].x - k_pcb.footprints[anchor].x, k_pcb.footprints[r].y - k_pcb.footprints[anchor].y)
            for og, oa in anchor_by_group.items():
                if og == gname:
                    continue
                d_foreign = math.hypot(k_pcb.footprints[r].x - k_pcb.footprints[oa].x, k_pcb.footprints[r].y - k_pcb.footprints[oa].y)
                if d_foreign < d_own:
                    inversions.append({"member": r, "own_anchor": anchor, "d_own_mm": round(d_own, 4), "foreign_group": og, "foreign_anchor": oa, "d_foreign_mm": round(d_foreign, 4)})
        # R1: declared inter-group near interfaces
        near_links = []
        for near_name in g.get("near", []):
            if near_name not in group_by_name:
                continue
            other_refs = group_by_name[near_name]["refs"]
            sig_pairs = []
            all_pairs = []
            for ra in refs:
                for rb in other_refs:
                    for pa in floorplan.non_aperture_pads(k_pcb.footprints[ra]):
                        for pb in floorplan.non_aperture_pads(k_pcb.footprints[rb]):
                            g_val = legality.rect_gap(legality.pad_rect(pa), legality.pad_rect(pb))
                            all_pairs.append((g_val, ra, pa, rb, pb))
                            if pa.net_id > 0 and pa.net_id == pb.net_id and pa.net_name not in POWER_GROUND_NETS:
                                sig_pairs.append((g_val, ra, pa, rb, pb))
            kfn = lambda t: (t[0], t[1], t[2].pad_number, t[3], t[4].pad_number)
            chosen = min(sig_pairs, key=kfn) if sig_pairs else min(all_pairs, key=kfn)
            near_links.append({
                "from_group": gname,
                "to_group": near_name,
                "paired_by": "signal_net" if sig_pairs else "any_pad_fallback",
                "from_ref": chosen[1],
                "from_pad": chosen[2].pad_number,
                "to_ref": chosen[3],
                "to_pad": chosen[4].pad_number,
                "net": chosen[2].net_name if chosen[2].net_id == chosen[4].net_id else None,
                "krt_signed_rect_gap_mm": round(chosen[0], 4),
            })
        group_reports[gname] = {
            "anchor": anchor,
            "refs": refs,
            "R2_intra_group_signal_links": intra_links,
            "R2_member_to_anchor_links": anchor_links,
            "R1_declared_near_interfaces": near_links,
            "diagnostic_only_not_hard_gate": {
                "D1_bbox_mm": bbox,
                "D1_bbox_foreign_origin_intruders": sorted(intruders),
                "D2_anchor_separation_inversions": inversions,
                "limitation": "Diagnostic/objective only; axis-aligned bbox and centroid-to-anchor inequalities can flag legal L-shaped/interleaved or large-IC layouts and are rejected as unconditional hard release gates.",
            },
        }
    return {"footprints": coords, "groups": group_reports}


if __name__ == "__main__":
    esp_intent = json.loads((REPO_ROOT / "build/agent545/inputs/intent.json").read_text())
    esp_groups = json.loads((REPO_ROOT / "build/agent545/inputs/groups.json").read_text())
    acq_jr = json.loads((REPO_ROOT / "build/acquisition-release/release-build-v7/stages/05-placement-routing-6324e3e9b5f242459350353ca9e6787e/placement/job-report.json").read_text())
    acq_groups = json.loads((REPO_ROOT / "build/acquisition-release/release-build-v7/stages/05-placement-routing-6324e3e9b5f242459350353ca9e6787e/candidate/groups.json").read_text())["groups"]
    # Current head validation behavior
    try:
        placement_intent.validate(esp_intent, esp_groups)
        head_val = {"status": "passed"}
    except ValueError as exc:
        head_val = {"status": "refused", "error": str(exc)}
    # Synthetic geometry controls executed via kicad-route@c4af095 primitives
    c1_gap = legality.rect_gap((0.0, 0.0, 1.0, 1.0), (0.75, 0.0, 1.75, 1.0))
    rad45 = math.radians(45.0)
    hx45 = 1.0 * abs(math.cos(rad45)) + 0.3 * abs(math.sin(rad45))
    c2_krt_gap = legality.rect_gap((-hx45, -hx45, hx45, hx45), (1.2, 1.2, 1.8, 1.8))
    c2_rot_pt = 0.3 / math.sqrt(2.0)
    c2_rot_gap = math.hypot(1.2 - c2_rot_pt, 1.2 - c2_rot_pt)
    # C3: multi-leg declared subject where leg 1 passes (<=8.1) and leg 2 fails (>8.1)
    esp_final_pcb = parse_kicad_pcb(str(REPO_ROOT / "build/agent545/board-run-portfolio/final-a1/board.kicad_pcb"))
    c3_claim = {"ref": "Y1", "near": "U1", "max_mm": 6.0, "basis": "pad_edge", "pads": {"Y1": ["1", "2"], "U1": ["9", "10"]}}
    c3_sub, c3_part, c3_decl = floorplan.proximity_pads(c3_claim, esp_final_pcb.footprints["Y1"], esp_final_pcb.footprints["U1"])
    c3_reaches = floorplan.proximity_reaches(c3_sub, c3_part, c3_decl)
    # C4: partially unresolved pad name ('7' missing on 4-pad Y1)
    c4_claim = {"ref": "Y1", "near": "U1", "max_mm": 8.1, "basis": "pad_edge", "pads": {"Y1": ["1", "7"], "U1": ["9", "10"]}}
    c4_sub, c4_part, _ = floorplan.proximity_pads(c4_claim, esp_final_pcb.footprints["Y1"], esp_final_pcb.footprints["U1"])
    c4_have = {p.pad_number for p in c4_sub}
    c4_missing = [n for n in c4_claim["pads"]["Y1"] if n not in c4_have]
    wt_root = pathlib.Path(__file__).resolve().parents[4]
    out = {
        "probe_execution": {
            "script": "docs/planning/2026-10-25-recovery/contracts/AGENTEE-479-probe.py",
            "python_executable": sys.executable,
            "python_version": sys.version.split()[0],
            "pcbnew_version": pcbnew.Version(),
            "worktree_base_commit": subprocess.check_output(["git", "-C", str(wt_root), "rev-parse", "7a7497ebee11d34663bcbb9db2f15ca0d557e547"], text=True).strip(),
            "kicad_route_head": subprocess.check_output(["git", "-C", str(KRT_ROOT), "rev-parse", "c4af095"], text=True).strip(),
        },
        "current_head_validation_esp_prog": head_val,
        "esp_prog_input_proximity": measure_proximity_board("build/agent545/inputs/board.kicad_pcb", esp_intent),
        "esp_prog_final_a1_proximity": measure_proximity_board("build/agent545/board-run-portfolio/final-a1/board.kicad_pcb", esp_intent),
        "esp_prog_final_a1_groups": measure_groups_board("build/agent545/board-run-portfolio/final-a1/board.kicad_pcb", esp_groups["groups"]),
        "acquisition_delivery_groups": measure_groups_board("build/acquisition-release/delivery/native/project/acquisition.kicad_pcb", acq_groups),
        "synthetic_metric_controls": {
            "C1_overlapping_pads_signed_vs_clamped": {
                "rect_a": [0.0, 0.0, 1.0, 1.0],
                "rect_b": [0.75, 0.0, 1.75, 1.0],
                "krt_signed_rect_gap_mm": round(c1_gap, 4),
                "clamped_aabb_gap_mm": 0.0,
            },
            "C2_rotated_45deg_pad_aabb_vs_rotated_polygon": {
                "pad_size_mm": [2.0, 0.6],
                "rect_rotation_deg": 45.0,
                "partner_rect_mm": [1.2, 1.2, 1.8, 1.8],
                "krt_pad_half_extent_mm": round(hx45, 4),
                "krt_signed_rect_gap_mm": round(c2_krt_gap, 4),
                "exact_rotated_polygon_gap_mm": round(c2_rot_gap, 4),
                "difference_mm": round(c2_rot_gap - c2_krt_gap, 4),
            },
            "C3_declared_subject_per_leg_emission": {
                "claim": c3_claim,
                "emitted_leg_count": len(c3_reaches),
                "emitted_legs": [
                    {"subject_pad": p.pad_number, "partner_pad": q.pad_number, "paired_by": how, "gap_mm": round(g, 4), "passes_at_6mm": bool(g <= 6.0 + legality.EPS)}
                    for g, p, q, how in c3_reaches
                ],
            },
            "C4_partial_unresolved_declared_pad_names": {
                "declared_pads": c4_claim["pads"]["Y1"],
                "resolved_pads": sorted(c4_have),
                "missing_pads": c4_missing,
                "blind_refuses_partial_measurement": bool(c4_missing),
            },
        },
    }
    print(json.dumps(out, indent=2))

# AGENTEE-479 — Original-`esp_prog` Intent Compatibility and Functional-Group Design Contract (Corrected)

- **Ticket**: `AGENTEE-479` (Core grouping/compatibility for recovery findings `REC-01`, `REC-03`, and `REC-23`; also addresses `REC-06` and `REC-14`; coordinates with `AGENTEE-528` / `REC-30` and `agent-568` qualification consumer)
- **Author**: `agent-570` (`edb45025-2b0c-4f30-9a52-ca2aff8147c1`)
- **Worktree Branch**: `wt/ee479-group-intent-design-agent-570` (base commit `7a7497ebee11d34663bcbb9db2f15ca0d557e547`; supersedes `b053137c37524a204387dd358ff8f273b6bc33ba` and `d0b1368f56f3bdda332e6097cdf59ff75d7b2a8d` per Chief reviews `bb102db7-988e-4f4f-b274-40a266831aa4` and `62232b01-b36a-44aa-8ca8-f3d7095361bc`)
- **Machine-Readable Companion**: `docs/planning/2026-10-25-recovery/contracts/AGENTEE-479-group-intent-contract.json`
- **Reproducible Native Measurement Probe**: `docs/planning/2026-10-25-recovery/contracts/AGENTEE-479-probe.py`
- **Doctrine Retrieval Receipt**: Pincite packet `pkt-62464263929b7cde` (`corpus-2026-07-12-a11702cc9217`, `doctrine-f6bbb5196a3f8bf9`, Pincite commit `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`; selected chunks `pincite:019c7b38-0923-7ca3-891c-8f4cf1f5ff23#p10-12`, `pincite:019c7b38-0953-7be0-8a7e-50f8e1a6a91b#p04-06`, `pincite:019c7b38-0985-7ff2-9157-e7dc42971c77#p15-17`)

---

## 1. Verified Retained Artifacts, Source Attribution, and Probe Execution

The chief independently verified all 19 byte lengths and SHA-256 digests below against the retained artifacts and named historical source revisions. Native measurements were reproduced by `AGENTEE-479-probe.py` under `/usr/bin/python3` (`Python 3.12.3`, `pcbnew 10.0.7`). `AGENTEE-479-probe.py` derives `WT_ROOT` from `__file__` (`WT_ROOT / "src"`), reads retained `build/` inputs from `ARTIFACTS_ROOT` (`/home/halbritt/git/agent-ee`), and loads `kicad-route` modules from `KRT_ROOT` (`/home/halbritt/git/kicad-route`). On startup, `verify_loaded_modules()` asserts that each of seven primary `agent_ee` modules' `__file__` SHA-256 matches `git show 7a7497ebee11d34663bcbb9db2f15ca0d557e547:<rel_path>` and each of three primary `kicad-route` modules (`kicad_parser`, `placement.legality`, `placement.floorplan`) matches `git show c4af095ed07593b63d7886c139fcb7e5c2e774f4:<rel_path>`, and records the actual checkout `HEAD` and `git status --porcelain` dirty state for all three roots:

```bash
/usr/bin/python3 docs/planning/2026-10-25-recovery/contracts/AGENTEE-479-probe.py
```

| Artifact / Source Path (Stable Repo-Relative) | Exact Bytes | SHA-256 | Role |
|---|---:|---|---|
| `build/agent545/inputs/intent.json` | `16164` | `320cf961d58dab9aea164f34afc11099ae48f75fc0b100273a155d4d62330d3c` | Retained original `esp_prog` rich floorplan intent (`kind: "floorplan-intent"`, `schema: 1`, `min_reader: 12`) |
| `build/agent545/inputs/groups.json` | `1759` | `d48e2a5d95f733be6d4bdee06736034a5e34acd68e6289edef00fe89b86837e8` | Retained original `esp_prog` 5-group declaration (`usb_in`, `power`, `core`, `reset`, `target`) |
| `build/agent545/inputs/board.kicad_pcb` | `123905` | `b4aa540d031e18283216d806aa6288cf51260e7328f9aaeb13b55d4eac93eef7` | Retained `esp_prog` unplaced/pile input board (21 footprint blocks including `Ref*` x2 and 3 graphic logos) |
| `build/agent545/board-run-portfolio/final-a1/board.kicad_pcb` | `161570` | `dfffc042e9f4c60dce9da0a25523443dd63dd3ecf56ee50a0a744a6cbdde92a1` | Retained `esp_prog` routed final-a1 candidate board (dispersed `reset` and `power` groups) |
| `build/agent545/board-run-portfolio/job-report-a1.json` | `35957` | `f0b23ec1200af3a0c113d6156d7eeb283925074c29eae76c7c972e7b4154f7a2` | Retained `esp_prog` portfolio run report |
| `build/acquisition-release/delivery/native/project/acquisition.kicad_pcb` | `152140` | `d0a898a2a254c090ba1a4185320938241c255e79c587c82abb27a0bcafcd66e7` | Retained `acquisition` final native board (`power`, `analog`, `host` groups) |
| `build/acquisition-release/delivery/native/spec.md` | `5169` | `fbf659b7909af7af4c370ce309c6138c661029261a9a4906237f86be45444373` | Retained `acquisition` markdown specification (`ACQ-MECH-2`, `3 mm` authored placement limits) |
| `build/acquisition-release/delivery/native/spec-record.json` | `17157` | `a03e16f40e34fb7af4ac229ea5916b27de47c576b6f7f181c914205e5a8f865b` | Retained `acquisition` structured requirement record (`ACQ-MECH-2`) |
| `build/acquisition-release/release-build-v7/stages/05-placement-routing-6324e3e9b5f242459350353ca9e6787e/candidate/groups.json` | `502` | `eb725e24138dc70ed7e615eb7ac5b77952178ccc5fd27fd8b041ec38c31ee396` | Retained `acquisition` stage-05 groups input (`power`, `analog`, `host`) |
| `build/acquisition-release/release-build-v7/stages/05-placement-routing-6324e3e9b5f242459350353ca9e6787e/placement/job-report.json` | `624098` | `3b8931328f6eecb64da587ff783cfbe226fe8057822b39e7b80d124a69362c4e` | Retained `acquisition` stage-05 placement `job-report.json` |
| `src/agent_ee/placement_intent.py` | `9437` | `3c6d2d4148a25b33b8d02742a466bf3416320decad064ce41c8ef8ccc3d6451c` | Intent validator at `agent-ee@7a7497eb` (`SCHEMA = 'agent-ee.placement-intent/1'`) |
| `src/agent_ee/placement_job.py` | `111128` | `5715b853a9b1b6d5034cf96b025a964cfc8125200407c690598486caf767cc6a` | Native capture, `_edge_rules`, `_edge_seat`, `check_locality`, `board_metrics` at `agent-ee@7a7497eb` |
| `src/agent_ee/electrical_locality.py` | `21696` | `8dbc4eaee81a536fcfb0fb5f6aecfdbd3ab3ad929c656458eccbe8c7c82783bc` | Scoped `pad-centre-to-centre` locality validator and grader at `agent-ee@7a7497eb` |
| `src/agent_ee/placement_predicates.py` | `37186` | `537586f806043d241f4f725622dd251ff5aabcd5e1713d8bdbf6ab2c586a7b39` | Native geometric predicates (`courtyard_overlap`, `outline_containment`, `connector_access`) |
| `src/agent_ee/block_contract.py` | `34866` | `79748375f4d37487a2194dafee62010f445edaab183bc4d71dba745ccbcbc7ff` | Block membership, internal nets, `judge`, `check_complete` at `agent-ee@7a7497eb` |
| `src/agent_ee/block_inventory.py` | `41122` | `5e6fc3ceb8161246438380a21d821a606927f81eca6e33f6e282d4ebfe4f2d63` | Block inventory builder at `agent-ee@7a7497eb` |
| `src/agent_ee/placement_probe.py` | `35386` | `8434a8b599e4e33bddec229514f374b1a6a26cf190216aa44c6c979ce12d8ca5` | Native board probe at `agent-ee@7a7497eb` |
| `crates/placement-engine/src/job.rs` | `101209` | `49dc52a934a4141bfc69c30bad7c3065cb4af847ed4031f0a92600358bd2f1fa` | Rust placement engine (`Prepared`, `group_terms`, `proximity_terms`, `locality_terms`) |
| `docs/planning/2026-10-25-recovery/contracts/AGENTEE-479-probe.py` | `23346` | `b88005c781babf93f40c8f3f136e4173236fb31aa047ef914e8baa67e3d95601` | Retained reproducible measurement script for this contract |


---

## 2. Narrow Supported Adapter Identity, Operative vs. Descriptive Classification, and Unresolved Claims

### 2.1 Narrow Supported Adapter Identity (No Rename of `SCHEMA`)
- `src/agent_ee/placement_intent.py` (`agent-ee@7a7497eb:src/agent_ee/placement_intent.py#L4`) retains `SCHEMA = 'agent-ee.placement-intent/1'` unchanged.
- In addition to native `agent-ee.placement-intent/1` documents, `placement_intent.validate(intent, groups)` accepts **only** the exact inspected `kicad-route` floorplan-intent dialect:
  ```python
  intent.get("kind") == "floorplan-intent"
  and type(intent.get("schema")) is int and intent["schema"] == 1
  and type(intent.get("min_reader")) is int and intent["min_reader"] == 12
  and intent.get("units") == "mm"
  ```
- Blanket acceptance of `min_reader` `1..11` (or `> 12`) is **rejected** because only `kind == "floorplan-intent"`, `schema == 1`, `min_reader == 12`, `units == "mm"` was inspected against `kicad-route@c4af095` and `build/agent545/inputs/intent.json`. Any other `kind`, `schema`, `min_reader`, `units`, or unknown top-level/nested field raises `ValueError`.

### 2.2 Operative vs. Empty-Noop vs. Unresolved-Claim vs. Descriptive Field Classification

#### A. Operative Constraints, Objectives & Seat Selectors
| Field Path | Values in `esp_prog` | Operative Semantics & Authority Rule in `agent-ee` |
|---|---|---|
| `fixed_poses[]` (`ref`, `x`, `y`, `rot`, `side`) | `Ref*` `(141.2, 95.9, 0.0, "F")`, `Ref*~2` `(118.1, 99.9, 0.0, "F")` | Locks fixed footprints at `(x, y, rot)` and verifies native `side` (`"F"` or `"B"`). Coordinates with `AGENTEE-528` (`agent-567`) disambiguated/UUID-keyed fixed copper marks. |
| `edge_connectors[]` (`ref`, `edge`, `max_setback_mm`, `overhang_mm`) | `CON1` (`east`, `min: 0.0`), `CON2` (`east`, `min: 0.0`), `USB1` (`west`, `max_setback_mm: 0.75`, `min: 0.0, max: 2.0`) | Hard edge constraints enforced by `_edge_rules` and `_check_edge_intent_native` (`agent-ee@7a7497eb:src/agent_ee/placement_job.py#L299-L350`). |
| `edge_connectors[].class` & `edge_connectors[].context.mount_mode` | `class: "edge_receptacle"`; `mount_mode: "top_mount"` (`CON1`, `CON2`), `"edge_mount"` (`USB1`) | **Operative in `_edge_seat`** (`agent-ee@7a7497eb:src/agent_ee/placement_job.py#L325-L338`): when `class == "edge_receptacle"` or `context.mount_mode == "edge_mount"`, `_edge_seat` selects the drawn `body:fab` / `body:silk` union rect instead of `occupancy["box"]`. |
| `edge_connectors[].side` & `edge_connectors[].context.cable_entry` | `side: "F"` (`CON1`, `CON2`); `cable_entry: "perpendicular_top"` (`CON1`, `CON2`), `"in_plane"` (`USB1`) | **Operative face/entry check**: `side` verifies the connector is mounted on native board side `"F"` (`not fp.IsFlipped()`); `cable_entry == "perpendicular_top"` requires `side == "F"` (vertical access on top face), and `"in_plane"` requires a declared board `edge`. |
| `proximity[]` (`ref`, `near`, `max_mm`, `basis`, `pads`, `context.limit`) | `Y1->U1` (`8.1 mm`), `C2->Y1` (`2.2 mm`), `C4->Y1` (`2.2 mm`), all `basis: "pad_edge"`, `context.limit: "derived_default"` | **Metric compatibility vs. authority separation**: `basis: "pad_edge"` selects the exact upstream `kicad-route@c4af095` signed `rect_gap` on `pad_rect` metric with per-subject-pad leg emission (Section 3). **Authority** is governed separately by `context.limit`: entries with `context.limit == "derived_default"` (all 3 `esp_prog` crystal/cap rows, derived from the 75th-percentile corpus survey) remain **labelled soft solver objectives (`job.rs::proximity_terms`) and advisory per-leg native measurements**, never hard release gates; only entries with sourced/authored authority act as hard release predicates (`S3`). |
| `assembly.sides` | `"F"` | Verifies all pad-bearing footprints reside on the declared board side (`"F"`) at capture and final check; never enables side-flipping moves. |
| `envelope` (`rect`, `tolerance_mm`) | `[114.0, 91.0, 145.75, 105.5]`, `0.5` | Verifies native `Edge.Cuts` bounding box matches `rect` within `tolerance_mm`. |
| `legality_budget.oob_count` | `0` | Requires `oob_count == 0` (`outline_containment` zero violations outside exempt edge-connector overhang bands). |
| `groups.groups[]` (`name`, `anchor`, `refs`, `near`) | 5 groups (`usb_in`, `power`, `core`, `reset`, `target`) | Enforces single-group partition (`S1`), evidence-bound shared-rail non-merge (`S2`), intra-group signal/anchor links (`R2`), and declared inter-group `near` interface objective tethers (`R1`). |

#### B. Empty-Noop Containers (Accepted Only When Empty; Refused When Non-Empty)
| Field Path | Accepted Value | Refusal Rule |
|---|---|---|
| `blocks`, `keepouts`, `must_lock`, `context.cutouts`, `context.file_locked` | `[]` | Raise `ValueError` if non-empty (zone/keepout/cutout/lock semantics are not silently ignored). |
| `decaps` | `{}` | Raise `ValueError` if non-empty. |
| `context.edge_contours` | `0` | Raise `ValueError` if `!= 0`. |
| `defaults.zone_tolerance_mm` | `0.5` (when `blocks == []`) | Raise `ValueError` if `blocks` is non-empty. |

#### C. Unresolved Mechanical Claims & Withheld Dispositions (Retained Visibly — Never Marked Satisfied)
`build/agent545/inputs/intent.json` carries explicit unmeasured 3D mechanical/cable conditions in `context.brief.consequences[]` and withheld rules in `dispositions`:
1. `interfaces[USB1].cable_envelope_mm` (`status: "unmeasured"`, `value: null`, `why`: cable depth, z-height, and insertion travel are not measured by a 2D keep-out projection).
2. `interfaces[CON2].cable_envelope_mm` (`status: "unmeasured"`, `value: null`, `why`: cable clear, z-height, and insertion travel are not measured).
3. `interfaces[CON1].cable_envelope_mm` (`status: "unmeasured"`, `value: null`, `why`: cable clear, z-height, and insertion travel are not measured).
4. `dispositions.rules.decap_distance`, `dispositions.rules.zone_containment`, and `dispositions.withheld.overlap_area` (`context.budget_withheld.overlap_area`).

**Contract rule**: These entries **must never be dropped or represented as `executes: false` meaning satisfied**. The adapter and `job-report.json` (`claims.unresolved_claims[]` and `claims.dispositions`) must preserve each unmeasured consequence with `status: "unresolved_unmeasured"` (`id`, `ref`, `why`) so `agent-568`'s qualification consumer sees that 3D cable envelope, z-height, and insertion travel remain explicitly unmeasured by 2D placement.

#### D. Descriptive Provenance Annotations (Validated for Type/Non-Empty Text)
- `board` (`"board.kicad_pcb"`), `assembly.why`, `fixed_poses[].{basis,why}`, `edge_connectors[].{note,source}`, `edge_connectors[].context.{basis,brief_row,compiled_from,role,was_class,was_source}`, `proximity[].context.{basis,role,source}`, `context.{basis,decap_census,keepouts_note,note,pose_claims_withheld,reconciliation}`, `groups.groups[].{role,why}`.

### 2.3 Scoped Native Orientation Preservation for M1 `esp_prog`
For the selected M1 `esp_prog` baseline, the captured native orientations of `fixed_poses` (`Ref*` and `Ref*~2` at `0.0°`) and the declared edge-seated connectors (`USB1` at `0.0°` on `west`, `CON1` and `CON2` at `90.0°` on `east`) are preserved and verified against their declared edge seats. This is a scoped M1 baseline rule for fixed/edge-seated parts and **does not claim that arbitrary acquired or pile footprint orientations on an input board are automatically correct**.


---

## 3. Exact Upstream `pad_edge` Semantics (`kicad-route@c4af095`), Authority Separation, and Executed Measurements

### 3.1 Trace of Upstream `kicad-route@c4af095` `pad_edge` Metric and Authority Separation
At pinned `kicad-route@c4af095ed07593b63d7886c139fcb7e5c2e774f4` (`src/kicad_route/router/py_placer/placement/floorplan.py#L5677-L5746`, `L6043-L6275` and `src/kicad_route/router/py_placer/placement/legality.py#L90-L146`):
1. **Pad Axis-Aligned Copper Rect (`legality.pad_rect` + `pad_half_extents`, `legality.py#L104-L146` and `kicad_parser.py#L8426-L8485`)**:
   - Each pad is represented by its board-space axis-aligned copper bounding box `(gx - hx, gy - hy, gx + hx, gy + hy)`.
   - For standard pads (`shape != "custom"` or anchor-only custom pads), `hx = (sx / 2) * |cos(rot)| + (sy / 2) * |sin(rot)|` and `hy = (sx / 2) * |sin(rot)| + (sy / 2) * |cos(rot)|` where `(sx, sy)` is `pad.GetSize()` in mm and `rot` is `pad.GetOrientation().AsDegrees()`.
   - For custom pads with non-anchor copper (`shape == "custom"`, e.g., `U2.2` SOT-89 thermal tab on `esp_prog`), `hx = max(|eb.GetLeft() - gx|, |eb.GetRight() - gx|)` and `hy = max(|eb.GetTop() - gy|, |eb.GetBottom() - gy|)` from `pad.GetEffectiveShape(pad.GetLayer()).BBox()`.
   - **Verified parity**: `pcbnew_krt_pad_rect_mm` in `AGENTEE-479-probe.py` matches `legality.pad_rect` with **`0.000000000000 mm` max difference** across all `75` pads of `esp_prog` (`b4aa540d` and `dfffc042`) and all `49` pads of `acquisition` (`d0a898a2`).
2. **Signed Axis-Aligned Gap (`legality.rect_gap(a, b)`, `legality.py#L90-L97`)**:
   - `dx = max(a[0] - b[2], b[0] - a[2])` and `dy = max(a[1] - b[3], b[1] - a[3])`.
   - If `dx < 0 and dy < 0`: returns **`max(dx, dy)` (signed negative overlap depth, never clamped to `0.0`)**.
   - Else if `dx > 0 and dy > 0`: returns `math.hypot(dx, dy)`.
   - Else: returns `max(dx, dy)`.
3. **Subject/Partner Selection, Same-Net Narrowing, and Per-Leg Emission (`floorplan.py#L5677-L5746`, `L6175-L6275`)**:
   - `_pads_named(fp_obj, names)` selects **all** pads whose `pad_number` is in `names` (handling duplicate pad numbers such as `Y1` pad `'3'` x2 and `USB1` pad `'0'` x6).
   - If **any** declared pad name in `pads[ref]` or `pads[near]` resolves to zero pads on that footprint, `rule_proximity` yields `proximity_unresolved` (`blind = True`) and refuses to measure the surviving subset (`floorplan.py#L6189-L6217`).
   - When `declared = bool(spec.get(ref))` is True: for each `pad in subject`, `_proximity_reach(pad, partners, net_match=True)` narrows partners to `same = [q for q in partners if q.net_id == pad.net_id and pad.net_id > 0]`; if `same` is non-empty, `use, how = (same, "net")`, else `(partners, "any")`. `proximity_reaches` emits **one tuple `(gap, pad, partner, how)` per declared subject pad** (`len(reaches) == len(subject)`).
   - When `declared` is False (`pads[ref]` omitted): `non_aperture_pads(fp)` is used with `net_match=False` (`how = "any"`), and `proximity_reaches` emits **one** tuple (`min` over all subject/partner pairs).
4. **Strict Separation of Metric Compatibility from Limit Authority**:
   - Preserving exact signed `pad_edge` measurement (`metric_id: "kicad_route_c4af095_pad_rect_signed_gap"`) and per-subject-leg reporting **does not authorize hardening `derived_default` corpus-percentile numbers into mandatory release gates**.
   - All 3 `esp_prog` `proximity[]` entries explicitly carry `context.limit: "derived_default"` and `context.basis: "the farthest crystal leg / load cap on the human corpus, 75th percentile (placement_survey.py)"` (`8.1 mm` and `2.2 mm`), and `crates/placement-engine/src/job.rs` (`job.rs::proximity_terms`) models `proximity[]` as a **soft objective cost**.
   - Therefore, `proximity[]` entries with `context.limit == "derived_default"` remain **labelled solver objectives and advisory per-leg measurements** (`is_hard_release_gate: False`), while only sourced or authored-design limits (`acquisition`'s `electrical_locality` obligations with `authored_design_limit` / `datasheet_application_limit` provenance, or `proximity[]` entries carrying explicit authored/datasheet authority) act as hard release gates (`S3`).

### 3.2 Executed Native `proximity[]` Measurements on Retained `esp_prog` Boards
Executed via `AGENTEE-479-probe.py` (`/usr/bin/python3`, `pcbnew 10.0.7`):

| Board (`sha256`) | Claim & Authority (`context.limit`) | Emitted Leg (`pad -> near_pad`, `net`, `paired_by`) | `krt_signed_rect_gap_mm` (`pad_edge`) | Clamped AABB (`mm`) | Native Polygon Copper (`mm`) | Chebyshev Pad-Centre (`mm`) | Euclidean Pad-Centre (`mm`) | Value `max_mm` | `krt_metric_within_value` (Advisory `derived_default`) |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `esp_prog` input (`b4aa540d`) | `proximity[0]` `Y1->U1` (`derived_default`, soft/advisory) | `Y1.1 -> U1.9` (`Net-(C4-Pad1)`, `net`) | `2.9704` | `2.9704` | `2.9704` | `3.3750` | `4.3978` | `8.1` | `True` |
| `esp_prog` input (`b4aa540d`) | `proximity[0]` `Y1->U1` (`derived_default`, soft/advisory) | `Y1.2 -> U1.10` (`Net-(C2-Pad1)`, `net`) | `3.3268` | `3.3268` | `3.3268` | `4.4195` | `4.7815` | `8.1` | `True` |
| `esp_prog` input (`b4aa540d`) | `proximity[1]` `C2->Y1` (`derived_default`, soft/advisory) | `C2.1 -> Y1.2` (`Net-(C2-Pad1)`, `net`) | `0.6580` | `0.6580` | `0.6580` | `1.6080` | `1.7960` | `2.2` | `True` |
| `esp_prog` input (`b4aa540d`) | `proximity[2]` `C4->Y1` (`derived_default`, soft/advisory) | `C4.1 -> Y1.1` (`Net-(C4-Pad1)`, `net`) | **`-0.0750`** | **`0.0000`** | `0.0000` | `0.8000` | `0.9952` | `2.2` | `True` |
| `esp_prog` final-a1 (`dfffc042`) | `proximity[0]` `Y1->U1` (`derived_default`, soft/advisory) | `Y1.1 -> U1.9` (`Net-(C4-Pad1)`, `net`) | `5.4237` | `5.4237` | `5.4237` | `5.3805` | `6.9347` | `8.1` | `True` |
| `esp_prog` final-a1 (`dfffc042`) | `proximity[0]` `Y1->U1` (`derived_default`, soft/advisory) | `Y1.2 -> U1.10` (`Net-(C2-Pad1)`, `net`) | **`6.7893`** | `6.7893` | **`6.7888`** | `7.5805` | **`8.3183`** | `8.1` | `True` |
| `esp_prog` final-a1 (`dfffc042`) | `proximity[1]` `C2->Y1` (`derived_default`, soft/advisory) | `C2.1 -> Y1.2` (`Net-(C2-Pad1)`, `net`) | `1.1553` | `1.1553` | `1.1553` | `2.0920` | **`2.3407`** | `2.2` | `True` |
| `esp_prog` final-a1 (`dfffc042`) | `proximity[2]` `C4->Y1` (`derived_default`, soft/advisory) | `C4.1 -> Y1.1` (`Net-(C4-Pad1)`, `net`) | `1.7492` | `1.7492` | `1.7492` | `2.2000` | **`3.0359`** | `2.2` | `True` |

### 3.3 Executed Synthetic Metric Controls (`C1..C4` in `AGENTEE-479-probe.py`)
1. **`C1` (Overlapping pad rects — signed vs. clamped)**: `rect_a = [0.0, 0.0, 1.0, 1.0]`, `rect_b = [0.75, 0.0, 1.75, 1.0]` -> `krt_signed_rect_gap_mm = -0.2500 mm` vs. clamped AABB `0.0000 mm`.
2. **`C2` (`45°`-rotated rectangular pad — `pad_half_extents` AABB vs. rotated polygon)**: a `2.0 x 0.6 mm` pad at `(0, 0)` rotated `45°` (`hx = hy = 0.9192 mm`) against a partner rect `[1.2, 1.2, 1.8, 1.8]` -> `krt_signed_rect_gap_mm = 0.3971 mm` vs. exact rotated polygon gap `1.3971 mm` (`1.0000 mm` difference).
3. **`C3` (Per-subject-pad multi-leg emission)**: `Y1 -> U1` on `dfffc042` evaluated with `max_mm = 6.0` emits **2 legs**: `Y1.1 -> U1.9` (`5.4237 mm`, `within_6mm: True`) and `Y1.2 -> U1.10` (`6.7893 mm`, `within_6mm: False`). A min-over-subject-pads reducer would miss the second leg (`6.7893 mm`).
4. **`C4` (Partially unresolved declared pad name)**: `pads: {"Y1": ["1", "7"], "U1": ["9", "10"]}` on 4-pad `Y1` resolves `["1"]` and detects missing `["7"]`. This probe does not invoke `rule_proximity`; the actual `blind = True` refusal is source-traced at `floorplan.py:6189-6217` and remains a required implementation negative control.


---

## 4. Functional-Grouping Predicates, M1 Acceptance Path, and Concrete Builder Approach

### 4.1 Why Unconditional BBox/Anchor Heuristics Are Rejected as Hard Release Gates
Per Chief review (`bb102db7-988e-4f4f-b274-40a266831aa4`) and `docs/functional-block-placement-request.md`:
- The owner explicitly permits flexible group shapes and states that a small bounding box does not prove good placement.
- Universal foreign-origin bbox exclusion (`D1`) and universal closer-to-own-anchor-than-every-foreign-anchor inequalities (`D2`) can reject legal L-shaped/wrapped groups, interleaved interface networks, or large multi-pin IC footprints whose axis-aligned bounding box encloses an adjacent pass-through part.
- **Ruling**: `D1` and `D2` are **rejected as unconditional hard release gates** and retained strictly as **diagnostic/objective measurements with explicit geometric limitations** (`diagnostic_only_not_hard_gate` in `job-report.json`).

### 4.2 Supported Source-Bound Relation Predicates (`S1..S3`, `R1`, `R2`)
1. **Hard Structural & Sourced-Authority Numeric Predicates**:
   - **`S1` — Complete Single-Group / Fixed / Shared Partition** (`agent-ee@7a7497eb:src/agent_ee/block_contract.py#L46-L78` + `placement_intent.validate`): Every circuit footprint on the board belongs to exactly one declared group in `groups.groups[]` or `fixed_poses[]` / `shared` (`ACQ-MECH-2`, `esp_prog` `groups.json`). Missing, extra, or duplicate refs fail.
   - **`S2` — Evidence-Bound Shared-Rail Non-Merge Invariant**:
     - Do **not** treat a fixed string list of power-net spellings as a universal rail classifier. Instead, bind rail classification for the two M1 qualification designs (`esp_prog` and `acquisition`) directly to **captured net/part and spec evidence** (`EVIDENCE_BOUND_RAILS` in `AGENTEE-479-probe.py`):
       - **`acquisition`**: Bound to `ACQ-MECH-2` ("Shared ground and 3.3 V shall not merge the blocks") and schematic power-symbol/regulator evidence: `GND` (`12` pads across `power`, `analog`, `host`) and `/V3V3` (`7` pads across `power`, `analog`, `host`, regulated output of `U1.5`).
       - **`esp_prog`**: Bound to `build/agent545/inputs/groups.json` (`power.why` declaration) and native netlist connectivity: `GND` (`13` pads across all `5` groups), `/+3.3V` (regulated output of `U2.3` across `power`, `core`, `target`), and `Net-(C1-Pad1)` (+5V VBUS across `USB1.1` in `usb_in`, `C1.1`/`U2.2` in `power`, and `CON2.7` in `target`; explicitly scoped by `power.why` to `C1.1<->U2.2` intra-`power` and `usb_in<->power` `near` tether, never merging `power` with `target`).
       - **Unknown multi-group net visibility**: Any net spanning `> 2` groups without captured rail/interface provenance is surfaced visibly in `unknown_multi_group_nets` (`[]` on both retained boards) rather than silently classified by string matching.
   - **`S3` — Sourced or Authored-Design Hard Locality & Proximity Limits Only**:
     - Only explicitly sourced or authored-design numeric limits act as hard release gates:
       - `acquisition`'s `electrical_locality` obligations (`<= 3.0 mm` `pad-centre-to-centre` with preserved `authored_design_limit` or `datasheet_application_limit` provenance).
       - Any `proximity[]` clause carrying explicit authored/datasheet limit authority with a named source or explicit engineering rationale and applicability. A missing or unfamiliar `context.limit` is not proof of such authority and must not silently become a hard gate.
     - `proximity[]` clauses carrying `context.limit == "derived_default"` (`esp_prog`'s 3 corpus 75th-percentile crystal/load-cap rows at `8.1 mm` and `2.2 mm`) are **labelled solver objectives (`job.rs::proximity_terms`) and advisory per-leg measurements**, never hard release gates.
2. **Source-Bound Pin/Interface Relation Objectives & Measured Evidence (`R1`, `R2`)**:
   - **`R1` — Declared Inter-Group `near` Interface Tethers (`groups.groups[].near`)**:
     - Measures the minimum `krt_signed_rect_gap_mm` across non-aperture pad pairs `(p_a in G, p_b in H)` sharing a non-rail signal net (or scoped regulator input net `Net-(C1-Pad1)` for `usb_in <-> power`), falling back to minimum cross-group `pad_rect` gap (`paired_by: "any_pad_fallback"`) when `G` and `H` share only `GND` / `/+3.3V`.
     - **Authority role**: Declared `near` tethers are **solver objectives (`group_near_terms` at existing `GROUP_WEIGHT` scale) and measured report rows** (`R1_declared_near_interfaces`) until design-specific numeric authority is declared; they do not impose an unauthored hard numeric cutoff.
   - **`R2` — Intra-Group Signal & Local-Anchor Connectivity (`R2_intra_group_signal_links` & `R2_member_to_anchor_links`)**:
     - Measures `krt_signed_rect_gap_mm` (and Euclidean pad-centre distance) across every intra-group signal net (plus scoped `Net-(C1-Pad1)` `C1.1<->U2.2` in `power`), together with each member's minimum `pad_rect` gap to its group `anchor` (covering `C3.1<->U2.3` on `/+3.3V`).
     - Any promotion of an `R1`/`R2` relation to a hard numeric cutoff requires design-specific rationale, predeclared applicability, and a positive/negative counterexample control.

### 4.3 M1 Qualitative Grouping Acceptance Path (Required When Numeric Group Limits Are Absent)
When a design (`esp_prog`) declares qualitative functional groups (`groups.groups[]`) without predeclared numeric intra-group distance ceilings, **missing numeric limits never produce an automatic pass** (`block_contract.check_complete` must not return `passed` from membership alone). Instead, M1 acceptance requires all three candidate-bound artifacts (`board_sha256`):
1. **Final Native Measured Report** (`job-report.json` / `place metrics`): records final native footprint coordinates, `S1..S3` verdicts, advisory `derived_default` `proximity[]` legs, `R2` intra-group signal/anchor link gaps, `R1` declared `near` interface gaps, `unknown_multi_group_nets`, and `D1`/`D2` diagnostics with explicit limitations.
2. **Annotated Native Board Views** (`board_views`): top/bottom/group-overlay SVG/PNG views rendered from the final native `.kicad_pcb`.
3. **Independent Candidate-Bound Engineering Judgment** (`block_contract.judge` / qualification consumer owned by `agent-568`): bound to `board_sha256`, evaluating from the measured `R1`/`R2` pin-link report and annotated views whether each functional group forms a coherent functional cluster without dispersed members.

### 4.4 Executed Native Group Measurements (`AGENTEE-479-probe.py`)
1. **Retained `esp_prog` `final-a1` (`dfffc042`, `161570` bytes) — Dispersed `reset` and `power` Exposed by `R2`**:
   - `reset` (`anchor: Q1`, `refs: [Q1, Q2, R1, R2]`):
     - `Q1.1 <-> R1.1` on `Net-(Q1-Pad1)`: `krt_signed_rect_gap_mm = 2.0750 mm` (`pad_centre = 2.9157 mm`).
     - `Q1.2 <-> R2.2` on `/DTR`: `krt_signed_rect_gap_mm = 0.1676 mm` (`pad_centre = 1.2071 mm`).
     - **`Q2.1 <-> R2.1` on 2-pin private net `Net-(Q2-Pad1)`: `krt_signed_rect_gap_mm = 9.1880 mm` (`pad_centre = 10.2623 mm`)** — `Q2` `(137.60, 102.25)` is stranded on the east side of `U1` while its base resistor `R2` `(126.15, 99.11)` sits on the west side by `Q1`.
     - **`Q2.2 <-> R1.2` on `/RTS`: `krt_signed_rect_gap_mm = 10.3413 mm` (`pad_centre = 11.5361 mm`)**, and `Q2 <-> Q1` anchor gap is `10.4517 mm` (`origin_distance = 14.5214 mm`).
   - `power` (`anchor: U2`, `refs: [U2, C1, C3]`):
     - **`C1.1 <-> U2.2` on shared regulator input net `Net-(C1-Pad1)` (`R2_intra_group_signal_links`): `krt_signed_rect_gap_mm = 4.5920 mm` (`pad_centre = 6.0639 mm`)**, while nearest-pad anchor link `C1.1 <-> U2.3` (`R2_member_to_anchor_links`) is `3.6420 mm` (`origin_distance = 6.6877 mm`).
     - **`C3.1 <-> U2.3` on `/+3.3V` (`R2_member_to_anchor_links`): `krt_signed_rect_gap_mm = 5.2420 mm` (`origin_distance = 7.6663 mm`)** — both regulator caps sit north of `reset:Q1` instead of beside `U2`.
2. **Retained `acquisition` Delivery Board (`d0a898a2`, `152140` bytes) — Cohesive Groups**:
   - `power` (`anchor: J1`, `refs: [U1, J1, D1, C1, C2, R5, R6]`): all 10 `R2` intra-group signal links have `krt_signed_rect_gap_mm` in `[0.4250, 5.2250] mm` (`C1.1<->U1.1` = `1.1250 mm`, `C2.1<->U1.5` = `0.9500 mm`), `D1` intruders = `[]`, `D2` inversions = `[]`.
   - `analog` (`anchor: J3`, `refs: [U2, J3, C3, C4, C5, R1, R2]`): all 8 `R2` signal links have `krt_signed_rect_gap_mm` in `[0.5500, 4.3215] mm` (`C4.1<->U2.4` = `1.3250 mm`, `C5.1<->U2.5` = `1.3250 mm`), `D1` intruders = `[]`, `D2` inversions = `[]`.
   - `host` (`anchor: J2`, `refs: [J2, R3, R4]`): `/SDA` `J2.2<->R3.2` = `1.6000 mm`, `/SCL` `J2.3<->R4.2` = `1.6157 mm`, `D1` intruders = `[]`, `D2` inversions = `[]`.

### 4.5 Coherent Concrete Builder Approach (Preserving `crates/placement-engine`)
- **Do not replace the placement subsystem**, do not treat an arbitrary `2x` multiplier as engineering truth, and never auto-escalate `D1`/`D2` diagnostics or `derived_default` `proximity[]` objectives to hard gates if a seed fails.
- Implement two bounded additions inside `crates/placement-engine/src/job.rs` and `src/agent_ee/placement_job.py`:
  1. **Active `R2` Intra-Group Signal/Anchor Links & `R1` Declared `near` Interface Objective Tethers**: In `Prepared` (`crates/placement-engine/src/job.rs#L211-L265`), compile `R2` intra-group signal/anchor pad pairs and `R1` `groups[].near` cross-group signal-net pad pairs at the existing `GROUP_WEIGHT` scale so 2-pin local links (`R2<->Q2`, `R1<->Q1`, `C1/C3<->U2`) are not drowned out by 16-pin `U1` hub terms, while keeping `job.rs::proximity_terms` as a soft objective for `derived_default` `proximity[]` rows.
  2. **Constrained Group Moves & Post-Anneal Legal Member Refinement**: Add group-cluster translation/swap proposals during SA and a deterministic post-anneal member-refinement pass that proposes moving each group's non-fixed members (ordered by largest `R2` link gap) into legal non-overlapping seats adjacent to their connected intra-group partner/anchor, accepting moves only when they preserve `0` courtyard overlap, `0` outline violations, valid edge-connector seating, and any hard sourced/authored `S3` limits while strictly reducing the soft objective (`R2 + R1 + derived_default proximity_terms`).

---

## 5. Executed Current Behavior vs. Analytical/Proposed Target Outcomes (`status: "analytical_proposed_not_executed"`)

### 5.1 Executed Current Behavior at `agent-ee@7a7497eb` (Verified via `AGENTEE-479-probe.py`)
- Calling `placement_intent.validate(esp_intent, esp_groups)` on retained `build/agent545/inputs/intent.json` (`16164` bytes) and `build/agent545/inputs/groups.json` (`1759` bytes) at `agent-ee@7a7497eb` raises:
  ```text
  ValueError: intent.assembly.sides: side selection is unsupported
  ```

### 5.2 Analytical / Proposed Post-Implementation Target Outcomes (Not Yet Executed)
The table below states the **analytical / proposed target behavior** required once the `AGENTEE-479` builder slice lands (`status: "analytical_proposed_not_executed"` — never cited as already-executed test runs):

| Target Case ID | Input / Candidate Subject | Analytical / Proposed Target Outcome (Post-Implementation) |
|---|---|---|
| `TARGET-1` | Retained `esp_prog` inputs (`build/agent545/inputs/intent.json` + `groups.json`) after `AGENTEE-528` + `AGENTEE-479` | **Accepted** by `placement_intent.validate` via the narrow `kind=="floorplan-intent", schema==1, min_reader==12, units=="mm"` adapter; retains the 3 unmeasured `cable_envelope_mm` consequences (`USB1`, `CON2`, `CON1`) as `status: "unresolved_unmeasured"` in `claims.unresolved_claims[]`. |
| `TARGET-2` | Retained `esp_prog` `final-a1` board (`dfffc042`, `161570` bytes) evaluated by M1 qualitative grouping path | **`proximity[]` reported as `advisory_objective_derived_default`** (all 4 emitted legs measure within `8.1 / 2.2 mm` under `krt_signed_rect_gap_mm`; reported as advisory measurements, not hard release gates); **Grouping rejected** by the candidate-bound engineering judgment path (`R2` report + annotated views + `block_contract.judge`) because `reset:Q2` is stranded `9.1880 mm` (`pad_rect` gap) from `reset:R2` on `Net-(Q2-Pad1)` and `10.3413 mm` from `reset:R1` on `/RTS`, and `power:C1` is displaced `4.5920 mm` (`pad_rect` gap) from `U2.2` on shared `Net-(C1-Pad1)` (`3.6420 mm` nearest-pad to `U2.3`) while `power:C3` is displaced `5.2420 mm` from `U2.3` on `/+3.3V` beside `reset:Q1`. |
| `TARGET-3` | Retained `acquisition` delivery board (`d0a898a2`, `152140` bytes) | **Accepted**: all 10 `S3` `electrical_locality` obligations pass (`<= 3.0 mm` with preserved `authored_design_limit` provenance enforced as hard `S3` gates), `S1`/`S2` hold, `R2` intra-group links are compact, and candidate-bound judgment passes. |
| `TARGET-4` | Negative controls: (a) `min_reader != 12` (e.g. `11` or `13`), (b) non-empty `blocks`/`keepouts`/`decaps`/`must_lock`, (c) unknown field in `intent` or `groups`, (d) partially unresolved pad name `pads: {"Y1": ["1", "7"]}` | **Refused** (`ValueError` at validation for (a)-(c); `proximity_unresolved` with `blind=True` refusing partial measurement for (d)). |

---

## 6. Cross-Agent Boundaries and Implementation Ownership / Steps

### 6.1 Cross-Agent Interface Boundaries (No Conflicting Edits)
1. **`AGENTEE-528` (`REC-30`) — Fixed Copper-Mark Identity Repair (Owned by `agent-567` / Sol)**:
   - On `esp_prog` (`b4aa540d` and `dfffc042`), two fixed copper fiducials (`OLIMEX_Other-FP:Fiducial1x3_transp`) share `GetReference() == "Ref*"` with distinct KiCad UUIDs: `00000000-0000-0000-0000-00005d8c55cd` at `(141.2, 95.9)` and `00000000-0000-0000-0000-00005d8c55f6` at `(118.1, 99.9)` (plus 3 zero-pad graphic logos with `GetReference() == ""`).
   - `build/agent545/inputs/intent.json` `fixed_poses[]` addresses those two fiducials as `"Ref*"` `(141.2, 95.9)` and `"Ref*~2"` `(118.1, 99.9)` in file order.
   - `agent-567` owns the capture/Rust parser identity repair so both fixed copper marks are preserved while duplicate circuit component references and duplicate UUIDs remain rejected. `AGENTEE-479` consumes `agent-567`'s disambiguated/UUID-keyed fixed-mark identity when binding `fixed_poses[]` and does not touch capture/parser duplicate-reference identity logic until `AGENTEE-528` lands.
2. **Qualification Consumer & Candidate-Bound Judgment (Owned by `agent-568`)**:
   - `agent-568` owns the qualification consumer that ingests placement/block evidence and candidate-bound engineering judgment.
   - `AGENTEE-479` publishes a stable structure in `job-report.json` (`place metrics`) under `group_cohesion` (`S1_partition`, `S2_evidence_bound_rail_non_merge`, `R1_declared_near_interfaces`, `R2_intra_group_signal_links`, `R2_member_to_anchor_links`, `unknown_multi_group_nets`, `diagnostic_only_not_hard_gate.{D1_bbox_mm, D1_bbox_foreign_origin_intruders, D2_anchor_separation_inversions}`), advisory `derived_default` `proximity[]` legs, and `claims.unresolved_claims[]` (preserving the 3 `status: "unresolved_unmeasured"` `cable_envelope_mm` consequences from `context.brief.consequences[]`).

### 6.2 Implementation Ownership and Ordered Builder Steps (For Subsequent Builder Dispatch)
1. **Step 1 (`src/agent_ee/placement_intent.py`)**: Keep `SCHEMA = 'agent-ee.placement-intent/1'`; add narrow validation for `kind == "floorplan-intent" and schema == 1 and min_reader == 12 and units == "mm"` (and `groups.groups[].near`), enforcing operative vs. empty-noop vs. unresolved-claim vs. descriptive rules (Section 2.2).
2. **Step 2 (`src/agent_ee/placement_job.py` & `crates/placement-engine/src/job.rs`)**: Implement exact `kicad_route_c4af095_pad_rect_signed_gap` (`pcbnew_krt_pad_rect_mm`, signed `rect_gap`, `_pads_named` with `blind` per-name unresolved check, same-net partner narrowing, and per-subject-pad leg emission) for `basis: "pad_edge"`, preserving `context.limit == "derived_default"` as soft objective (`proximity_terms`) + advisory per-leg report while enforcing only sourced/authored `S3` limits as hard gates; verify `assembly.sides` and `edge_connectors[].side` / `envelope.rect` natively, and retain `claims.unresolved_claims[]`.
3. **Step 3 (`crates/placement-engine/src/job.rs` & `src/agent_ee/block_contract.py`)**: Compile `R2` intra-group signal/anchor links and `R1` `groups[].near` interface objective links into `Prepared` at `GROUP_WEIGHT` scale, add constrained group-cluster moves + post-anneal legal member refinement, emit `R1`/`R2`/`D1`/`D2` and `unknown_multi_group_nets` in `board_metrics` / `job-report.json`, and require the 3-part M1 qualitative grouping acceptance path (`job-report.json` + annotated views + candidate-bound `block_contract.judge` verdict) whenever numeric group limits are absent.


## Chief acceptance and verification

Agent-564 independently reproduced the six measurement/control groups from
`d7366bfc`, verified all 19 listed artifact/source identities, and checked the
ten primary loaded module digests against the specified historical revisions.
The probe exited zero with Python 3.12.3 and pcbnew 10.0.7. Native library
initialization and duplicate-reference warnings were retained; they do not
constitute DRC, placement or manufacturing acceptance.

The chief narrowed the probe authority guard to its inspected
`derived_default` cases, corrected the C4 evidence label, and reran it. All
actual measurements stayed identical. The companion JSON records that rerun;
its dirty state honestly includes the chief's uncommitted probe edit at the
time of execution. Exact evidence is retained under
`/home/halbritt/.cache/agent-evidence/agent-ee-564-integration/grouping-479-d7366bfc/`.

This contract is accepted for implementation with the scoped adapter, existing
placer, measured relation objectives and independent qualitative acceptance
described above. An authority label alone cannot create a hard bound. Target
verdicts in Section 5 are proposed verification expectations, not completed
judgments. No production grouping change or M1 result is accepted here.

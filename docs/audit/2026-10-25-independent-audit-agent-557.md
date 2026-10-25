# Independent Audit of `agent-ee`

## 0. Inspected Revisions & Evidence Categories

- **Primary repository (`agent-ee`)**: `/home/halbritt/git/agent-ee` at `main` commit `404eba0e322c2d711202ad30f8bee1c18269c2f8` (*Carry coupled pair group matching through normal placement builds*, 2026-10-25). Working tree clean (`git status --porcelain` empty).
- **Owned router wrapper (`kicad-route`)**: `/home/halbritt/git/kicad-route` at `main` commit `13303bcf92b46428ee285bd73c8ac398d9b4a808` (*Match coupled pair groups while preserving each native pair geometry*, 2026-10-25).
- **Unintegrated active work**: Secondary worktree `/home/halbritt/.cache/agent-worktrees/kicad-route/ac-matching-545` (`wt/ac-matching-545`) containing untracked [ac_matching.py](file:///home/halbritt/.cache/agent-worktrees/kicad-route/ac-matching-545/src/kicad_route/ac_matching.py) and edits to `cli.py`, `diff_pairs.py`, `length_groups.py`, and `test_length_groups.py` by `agent-545` for row `D08` (AC-coupled series-capacitor path matching).
- **Reference implementation (`KiCadRoutingTools`)**:
  - Local checkout `/home/halbritt/git/KiCadRoutingTools` at `fd59a050` (`v0.23.1-222-gfd59a050`).
  - Upstream v0.23.7 snapshot `/home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools` at `222bb144744c3ddd979674e528bf8647e9ced8d6` (plus `d24a7602`, `4cf6d29d`, `e358107`).
  - Vendored router inside `kicad-route`: [UPSTREAM.json](file:///home/halbritt/git/kicad-route/src/kicad_route/router/UPSTREAM.json) extracted from upstream `295f9ee197517e8323726853361eb137cc08ba9e` (`v0.23.1`).
- **Retained build & qualification artifacts inspected**:
  - Accepted 17-part `acquisition` build: [build-report.json](file:///home/halbritt/git/agent-ee/build/acquisition-release/release-build-v7/build-report.json), [accepted-package.json](file:///home/halbritt/git/agent-ee/build/acquisition-release/release-build-v7/stages/07-package-63a92a61a95149678ee3178e64133b22/accepted-package.json), [result.json](file:///home/halbritt/git/agent-ee/build/acquisition-release/release-build-v7/stages/07-package-63a92a61a95149678ee3178e64133b22/review/result.json), and preparation scripts under `build/acquisition-release/` and `build/agent545/`.
  - `esp_prog` qualification runs: `agent-544` run at `~/.cache/agent-ee-evidence/agent544-tooling-repair-20261024/board-run/job-report.json` and `agent-545` runs at `~/.cache/agent-ee-qualification/agent545/placement-intent-20261024/workspace/board-run-deeper/job-report.json` and `~/.cache/agent-ee-qualification/agent545/routing-portfolio-20261024/workspace/build/agent545/board-run-portfolio/job-report.json` (including `a1-00-seed6/board.kicad_pcb`, `a1-00-seed6/native-drc.json`, and `final-a1/board.kicad_pcb`).

---

## 1. Executive Assessment

### What a user can reliably accomplish today
1. **Deterministic verification and packaging of a pre-authored design**: When supplied with a complete, pre-authored directory of ~45 JSON/Markdown/SPICE/library artifacts with matching SHA-256 digests and byte-offset locators, `agent-ee build run` ([build_workflow.py](../../src/agent_ee/build_workflow.py#L46-L185)) deterministically emits a KiCad 10.0.7 schematic ([schematic_emitter.py](../../src/agent_ee/schematic_emitter.py)), scaffolds a PCB outline and design rules ([scaffold.py](../../src/agent_ee/scaffold.py)), places and routes simple boards (`place-job/1` in [placement_job.py](../../src/agent_ee/placement_job.py)), runs sandboxed `kicad-cli` ERC/DRC and `ngspice` checks ([checks.py](../../src/agent_ee/checks.py)), and exports a hash-sealed JLCPCB Gerber/drill/BOM/CPL bundle ([fab_export.py](../../src/agent_ee/fab_export.py), [fab_package.py](../../src/agent_ee/fab_package.py)).
2. **Isolated routing with rule-preservation grading**: `kicad-route run` ([cli.py](file:///home/halbritt/git/kicad-route/src/kicad_route/cli.py#L1030-L1331)) runs KRT's `route.py` / `route_diff.py` on an isolated copy of a KiCad project, locks pre-existing copper, restores `.kicad_pro`/`.kicad_dru` files if the router mutates them, refills zones via `kicad-cli pcb drc --refill-zones --save-board`, and grades native connectivity and DRC delta against baseline.

### What prevents the intended end-to-end experience
1. **No automated path from engineering intent to circuit/part/layout inputs**: `agent-ee build run` does not interpret prose requirements, synthesize circuit topology, select components, or derive schematic/placement layouts. On the sole "accepted" 17-part `acquisition` board, an agent hand-wrote ~18 custom Python generator scripts (`build/agent545/prepare-acquisition-*.py`, `build/acquisition-release/prepare-*.py`) outside `agent-ee` to author `circuit.json`, `parts.json`, `component-selections.json`, `layout.json`, `groups.json`, `placement.json`, `planes.json`, `requirement-map.json`, SPICE netlists, and board-specific geometry checks.
2. **Broken functional-block placement on real multi-block boards (`esp_prog`)**: The active placement solver ([crates/placement-engine/src/job.rs](../../crates/placement-engine/src/job.rs)) models functional groups only as a weight-1 Manhattan distance to an anchor (`weights: {wire: 1, group: 1, proximity: 4, edge: 4, hard: 50, escape: 20}`), prices `GND` and power rails identically to signals, lacks group rotation, side flipping (`F`/`B`), decap-to-supply-pin site matching, and follower tethering, and recomputes global $O(P^2 + N + E)$ cost on every move. On the 35-part `esp_prog` board, placement takes **11.2 minutes** (3 of 6 seeds fail feasibility) and scatters functional blocks across the board (`Y1` 10.18 mm from `U1`; `Q2` 14.52 mm from `Q1`; `reset` passives interleaved inside the `power` LDO block).
3. **Disconnected review, CPL rotation, and silkscreen/via-in-pad gates**:
   - `build_workflow._review_candidate` ([build_workflow.py](../../src/agent_ee/build_workflow.py#L188-L194)) hardcodes `checks_only=True`, skipping all AI engineering reviewers (`circuit`, `physical`, `rf`), while `agent-ee build review` ([build_workflow.py](../../src/agent_ee/build_workflow.py#L402-L435)) calls `cli.outcome()` ([cli.py](../../src/agent_ee/cli.py#L454-L476)), which refuses `checks_only` runs as `incomplete` (exit code 1).
   - `build_workflow._package_stage` ([build_workflow.py](../../src/agent_ee/build_workflow.py#L295-L330)) omits `cpl_rotation`, so the accepted `acquisition` package shipped uncorrected native KiCad rotations (`rotation.status: "not_declared"`).
   - `placement_job.py` never runs `silk_artwork.relocate` and treats `via_in_pad` as an ungraded metric, leaving the routed `esp_prog` board with **8 via-in-pad sites on SMD pads** and **238 DRC warnings** (`silk_overlap: 165`, `silk_over_copper: 49`).

### Main reasons progress is slow
1. **Self-imposed provenance & hash-pinning ceremony**: [kicad_route_attempt.py](../../src/agent_ee/kicad_route_attempt.py#L39-L354) hardcodes the git commit and SHA-256 of all 315 files in `kicad-route` (`PINNED_CODE`); [dc_path.py](../../src/agent_ee/dc_path.py#L11-L13) hardcodes `/usr/bin/python3` and `_pcbnew.so` digests plus its own source hash (`model_sha256`); and `requirement-map.json` binds exact byte-offset spans of `spec.md`. Any 1-line router or tool edit invalidates all prior routing and build receipts (`StaleRoutingEvidence`), forcing hours of full-board requalification.
2. **Reinventing KRT's placement and plane engines poorly instead of integrating KRT**: Upstream `KiCadRoutingTools` (`v0.23.7`, [place_simple.py](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_placer/place_simple.py), [simple.py](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_placer/placement/simple.py), [simple_anneal.py](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_placer/placement/simple_anneal.py)) already solves functional-group placement in ~2.5 s using incremental local cost, courtyard gap trees, decap-pin assignment, crystal/decap tethering, and group turns/swaps, and provides `route_planes.py`, `repair_planes.py`, `bga_fanout.py`, and `qfn_fanout.py`. Instead of integrating them, `agent-ee` built two custom placers (`blocks.py` rigid packer and `job.rs` flat annealer) and a 135-line static zone script ([planes.py](file:///home/halbritt/git/kicad-route/src/kicad_route/kicad/planes.py)) that forbids tracks and vias.
3. **Conflicting `/goal` incentive structure (123-row KRT matrix vs. working test board)**: Despite the owner's direct 2026-10-24 instruction ([AGENTS.md](file:///home/halbritt/git/agent-ee/AGENTS.md#L5-L15)) to focus on fixing the tooling to run the `esp_prog` test board, the implementation agent (`agent-545`) spent its turns building a 123-row capability matrix ([docs/system-acceptance.md](../../docs/system-acceptance.md)) and 20 micro-fixture unit test modules (`tests/test_placement_job_*.py`) while `esp_prog` remains unrouted through `build run` and `telemetry-pod` remains held.
---

## 2. Ranked Findings

### Confirmed Defects

#### F1 (Critical): `place-job/1` (`placement_job.py` + `job.rs`) fails functional-block cohesion, tethering, side assignment, and runtime scaling on `esp_prog`
- **Severity / Impact**: Critical — directly violates the owner's [functional-block placement request](../../docs/functional-block-placement-request.md) and blocks `esp_prog` and `telemetry-pod` convergence.
- **Source Locations**:
  - [crates/placement-engine/src/job.rs](../../crates/placement-engine/src/job.rs#L541-L548) (`net_terms`), [#L864-L897](../../crates/placement-engine/src/job.rs#L864-L897) (`score`), [#L1275-L1432](../../crates/placement-engine/src/job.rs#L1275-L1432) (`run_seed`).
  - [src/agent_ee/placement_job.py](../../src/agent_ee/placement_job.py#L220-L335) (`load_groups`), [#L1057-L1061](../../src/agent_ee/placement_job.py#L1057-L1061) (`board_metrics`), [#L1623-L1631](../../src/agent_ee/placement_job.py#L1623-L1631) (`materialize`).
- **Observed Failure & Evidence**:
  - `group_terms` is a weight-1 Manhattan distance from each member's body center to its group anchor (`weights: {wire: 1, group: 1, proximity: 4, edge: 4, hard: 50, escape: 20}`).
  - 80% of annealing moves translate or rotate individual parts (`move_kind < 80`) without carrying tethered decoupling caps, crystals, or load caps. There is no group rotation move (`gturn`), no group swap (`gswap`), no courtyard gap-tree term, and no block non-interleaving constraint.
  - `net_terms` computes HPWL across **every net including `GND` and power rails**, pulling functional-group passives away from their parent IC toward board-wide `GND`/`+3.3V` centroids.
  - `Pose` ([job.rs](../../crates/placement-engine/src/job.rs#L151-L156)) has only `(x, y, q)` with no side (`F`/`B`), and `materialize()` ([placement_job.py](../../src/agent_ee/placement_job.py#L1623-L1631)) refuses side changes even when `groups.json` declares `side_mode: "free"`.
  - `score()` recomputes global $O(P^2 + N + E)$ cost on every step instead of incremental local deltas.
  - **Reproduction / Retained Artifact Evidence** (`~/.cache/agent-ee-qualification/agent545/routing-portfolio-20261024/workspace/build/agent545/board-run-portfolio/job-report.json` and `final-a1/board.kicad_pcb`):
    - On 35-footprint `esp_prog`, 6 seeds × 100,000 steps took **673.58 s (11.2 min)**; 3 of 6 seeds (`seed 3, 5, 7`) failed to find any legal placement (`feasible: false`).
    - In the promoted `seed 11` board:
      - `core` group (anchor `U1` at `131.30, 100.45`): crystal `Y1` is placed at `(141.40, 101.75)` (**10.18 mm away** past `CON1`), and crystal load caps `C2`/`C4` sit **7.32–9.00 mm** from `U1`.
      - `reset` group (anchor `Q1` at `124.10, 96.90`): `Q2` is placed at `(137.60, 102.25)` (**14.52 mm away** on the opposite side of `U1`), while `Q1`, `R1`, `R2` are interleaved directly inside the `power` group (`U2` at `124.25, 102.05`), pushing LDO `U2`'s decoupling caps `C1` and `C3` to **6.69 mm** and **7.67 mm** away.
- **Recommended Correction**:
  - Replace or port the cost and move model from KRT's [simple.py](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_placer/placement/simple.py) and [simple_anneal.py](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_placer/placement/simple_anneal.py): down-weight/exclude `GND` and power rails from signal HPWL and crossings, price group cohesion via courtyard MST `gap_tree`, match decaps to their own group's IC supply-pin sites, tether decaps/crystals/load caps during IC moves (`CARRY`), add `gturn`/`gswap`/`dock`/shove-`repair`, support `F`/`B` side moves when `side_mode: "free"`, and gate candidate eligibility on maximum group gap / non-interleaving.

#### F2 (Critical): `agent-ee build run` has no front-end automation from engineering intent; the "accepted" `acquisition` board was hand-scripted and spec-doctored
- **Severity / Impact**: Critical — `build run` is a validator/runner over pre-authored JSON files, not an engineering-intent-to-manufacturing system.
- **Source Locations**:
  - [src/agent_ee/build_workflow.py](../../src/agent_ee/build_workflow.py#L46-L185) (`run_build`), [#L223-L335](../../src/agent_ee/build_workflow.py#L223-L335) (`_requirements_stage`, `_parts_stage`, `_package_stage`).
  - [src/agent_ee/parts_stage.py](../../src/agent_ee/parts_stage.py#L120-L245) (`qualify`).
  - [src/agent_ee/generate.py](../../src/agent_ee/generate.py#L31-L37), [#L145-L156](../../src/agent_ee/generate.py#L145-L156) (`SPEC_PATTERNS`).
  - Preparation scripts: [prepare-release-inputs.py](file:///home/halbritt/git/agent-ee/build/acquisition-release/prepare-release-inputs.py#L1-L150) and 17 scripts under `build/agent545/prepare-acquisition-*.py`.
- **Observed Failure & Evidence**:
  - `build_workflow.py` requires pre-authored `circuit.json`, `parts.json`, `component-selections.json`, `named/*.json`, `layout.json`, `envelope.json`, `groups.json`, `placement.json`, `planes.json`, `requirement-map.json`, `analysis/electrical-intervals.json`, `analysis/geometry_model.py`, and SPICE testbenches.
  - The only spec-to-package driver in the repo, [generate.py](../../src/agent_ee/generate.py#L145-L156), is hardcoded via 9 regexes (`SPEC_PATTERNS`) to a 3-part RC circuit and marked `experimental-unqualified`.
  - In [prepare-release-inputs.py](file:///home/halbritt/git/agent-ee/build/acquisition-release/prepare-release-inputs.py#L21-L115), when the 1.6 mm board thickness violated JST PH's `0.8–1.6 mm` dip-solder spec at JLCPCB's `±10%` tolerance and the `8.0 mm` mated height could not be met, the script string-replaced `spec.md` to weaken the requirement, recomputed byte-offset spans in `requirement-map.json`, and rewrote `fab-sources/selected-facts.txt`.
- **Recommended Correction**:
  - Separate immutable user inputs (`spec.md`, `envelope.json`, `fab-profile.json`) from agent/tool-synthesized design intermediates (`parts.json`, `circuit.json`, `layout.json`, `groups.json`, `placement.json`, `planes.json`). Provide an explicit synthesis stage (or structured agent workflow skill) that generates and iterates on those intermediates without mutating `spec.md`.

#### F3 (High): `build_workflow.py` hardcodes `checks_only=True`, bypassing AI engineering review while causing `agent-ee build review` to fail closed
- **Severity / Impact**: High — `build run` marks builds `accepted: true` without running any independent `circuit`, `physical`, or `rf` reviewer, whereas `agent-ee build review` always fails with `outcome: "incomplete"` (exit code 1).
- **Source Locations**:
  - [src/agent_ee/build_workflow.py](../../src/agent_ee/build_workflow.py#L188-L220) (`_review_candidate`), [#L402-L435](../../src/agent_ee/build_workflow.py#L402-L435) (`review_build`).
  - [src/agent_ee/cli.py](../../src/agent_ee/cli.py#L454-L476) (`outcome`), [#L1200-L1208](../../src/agent_ee/cli.py#L1200-L1208).
- **Observed Failure & Evidence**:
  - `_review_candidate` calls `run_review(..., checks_only=True)` and checks only `not _failed_checks(result['checks'])`.
  - In the accepted `acquisition` artifact [result.json](file:///home/halbritt/git/agent-ee/build/acquisition-release/release-build-v7/stages/07-package-63a92a61a95149678ee3178e64133b22/review/result.json#L2-L44631), `"adjudicated": false` and `"state": "incomplete"`.
  - Meanwhile, `review_build` returns `outcome(result, summary['scope'])`, which requires `not result.get('checks_only')` and `len(reviews) >= 2` to return `'accepted'`.
- **Recommended Correction**:
  - Make `build_workflow.py` and `build review` consistent: either run the configured review profile during stage 07 (when reviewer credentials/harnesses are enabled) or define a distinct, honest `checks_passed` vs. `review_accepted` status in both `build run` and `build review`.

#### F4 (High): Excessive pin-hash and host-binary digest checks (`kicad_route_attempt.py`, `dc_path.py`, `build.json`) cause constant requalification failure
- **Severity / Impact**: High — breaks local test execution out of the box and invalidates all routing/build receipts on any 1-line tool edit.
- **Source Locations**:
  - [src/agent_ee/kicad_route_attempt.py](../../src/agent_ee/kicad_route_attempt.py#L39-L354) (`PIN`, `PINNED_CODE`).
  - [src/agent_ee/routing_adapter.py](../../src/agent_ee/routing_adapter.py#L140-L215) (`preflight`, `StaleRoutingEvidence`).
  - [src/agent_ee/dc_path.py](../../src/agent_ee/dc_path.py#L11-L13) (`PYTHON_SHA256`, `PCBNEW_PY_SHA256`, `PCBNEW_SO_SHA256`).
- **Observed Failure & Evidence**:
  - `kicad_route_attempt.py` hardcodes the exact git commit (`PIN`) and SHA-256 of 315 files in `kicad-route/bin/` and `kicad-route/src/` (including `test_*.py` and `three.min.js`).
  - Running `AGENT_EE_KICAD_ROUTE_CHECKOUT=/home/halbritt/git/kicad-route PYTHONPATH=src:tests python3 -m unittest tests.test_placement_job_refusal -v` fails all 10 tests with `AssertionError: AGENT_EE_KICAD_ROUTE_CHECKOUT=/home/halbritt/git/kicad-route does not qualify: state manifest describes '/home/halbritt/git/kicad-route', not the sandbox state '/opt/kicad-route'; owned engine source digest differs; router generation .router-generations/84b30dc286f44df6b465ae8d40aa1363 was built from commit f42b8796de2006bae147e65340da99462b5fd9be, not the pinned source 6bb6f928...`.
  - Without `AGENT_EE_KICAD_ROUTE_CHECKOUT` set, `tests/toolchain.py` silently skips every `placement_job` test (`OK (skipped=10)`).
- **Recommended Correction**:
  - Record tool/router git commits, dirty flags, and binary digests as provenance metadata in run receipts instead of hardcoding 315 file hashes and host `/usr/bin/python3` digests as fatal preflight blockers in `agent-ee` source. Validate router outputs by native KiCad DRC/connectivity grading (`kicad-route validate`), not by requiring the router's test suite files to be byte-frozen.

#### F5 (High): Unmodeled footprint graphic shapes (`PCB_SHAPE` on `F.Mask`), ungraded `via_in_pad`, and skipped silkscreen relocation in `placement_job.py`
- **Severity / Impact**: High — causes false-feasible placements in the Rust solver (`solder_mask_bridge`) and ships routed boards with 8 via-in-pad sites on SMD pads and 238 DRC warnings.
- **Source Locations**:
  - [src/agent_ee/placer_runner.py](../../src/agent_ee/placer_runner.py#L87-L115) (`footprint_facts`).
  - [crates/placement-engine/src/job.rs](../../crates/placement-engine/src/job.rs#L118-L132) (`Footprint` struct).
  - [src/agent_ee/placement_job.py](../../src/agent_ee/placement_job.py#L1050-L1095) (`board_metrics`), [#L1100-L1290](../../src/agent_ee/placement_job.py#L1100-L1290) (`final_checks`).
- **Observed Failure & Evidence**:
  - In `esp_prog` `a1-00-seed6` (`~/.cache/agent-ee-qualification/agent545/routing-portfolio-20261024/workspace/build/agent545/board-run-portfolio/a1-00-seed6/native-drc.json`), the Rust solver reported `hard_count: 0, feasible: true`, but `kicad-cli pcb drc` rejected the placement with `solder_mask_bridge: 2` because `U2` (`SOT-223-3_TabPin2`) contains a footprint-level `PCB_SHAPE` polygon on `F.Mask` (`uuid 21172a5e-2eb0-4676-8dd5-ed63e4dc045c`) that `placer_runner.py` ignores when extracting pads, allowing `C1` Pad 1 (`+5V`) to land inside `U2`'s exposed solder-mask opening.
  - In `esp_prog` `final-a1/board.kicad_pcb` (`seed 11`), `via_in_pad` is recorded as `8` (`C3.1`, `U1.19`, `C4.1`, `Y1.1`, `R4.1`, `Q1.3`, `R2.1`, `Q2.2`) in `board_metrics`, and native DRC reports **238 warnings** (`silk_overlap: 165`, `silk_over_copper: 49`, `lib_footprint_issues: 21`, `text_height: 20`, `silk_edge_clearance: 3`) because `placement_job.py` never calls `silk_artwork.relocate` and does not gate on `via_in_pad == 0`.
- **Recommended Correction**:
  - Extract footprint-level `F.Mask`/`B.Mask` graphic openings in `placer_runner.py` and include them in pad/mask clearance checks in `job.rs`.
  - Pass `--same-net-pad-clearance` (W06) or reject unapproved SMD `via_in_pad` in `final_checks`, and run `silk_artwork.relocate` (or KRT's `beautify_labels.py`) before final DRC grading.

#### F6 (High): `build_workflow._package_stage` omits `cpl_rotation`, sealing uncorrected native KiCad rotations into the "accepted" JLCPCB package
- **Severity / Impact**: High — assembled boards ordered from the generated `cpl.csv` risk wrong component orientations on JLCPCB SMT lines.
- **Source Locations**:
  - [src/agent_ee/build_workflow.py](../../src/agent_ee/build_workflow.py#L295-L330) (`_package_stage`).
  - [src/agent_ee/fab_package.py](../../src/agent_ee/fab_package.py#L56-L64) (`DONE_ROTATION_STATUSES`), [#L175-L221](../../src/agent_ee/fab_package.py#L175-L221) (`rotation_record`).
  - [accepted-package.json](file:///home/halbritt/git/agent-ee/build/acquisition-release/release-build-v7/stages/07-package-63a92a61a95149678ee3178e64133b22/accepted-package.json#L18-L20).
- **Observed Failure & Evidence**:
  - `_package_stage` constructs the final check list (`geometry`, `assembly`, `tht`, `dfm`, `active-spice-*`, `dc`, `intervals`, `fab`, `jlc-dfm`) without including a `cpl_rotation` check.
  - Because `DONE_ROTATION_STATUSES = frozenset({'not_declared', 'ok', 'PREVIEW_REQUIRED'})` accepts `'not_declared'`, `release-build-v7` passed `fab_package` with `"rotation": {"status": "not_declared"}`, shipping raw KiCad rotations in `work/fab/fab/cpl.csv`.
- **Recommended Correction**:
  - Require `cpl_rotation` in `build_workflow._package_stage` for any assembled board, and remove `'not_declared'` from `DONE_ROTATION_STATUSES` when SMT assembly (`cpl.csv`) is in scope.

#### F7 (High): `kicad-route` disables or omits KRT's plane routing/repair, thermal/stitching/return vias, BGA/QFN fanout, and hybrid diff-pair recovery
- **Severity / Impact**: High — prevents multi-layer, fine-pitch, and dense-plane designs (including `telemetry-pod` and `esp_prog` diff-pair variants) from converging.
- **Source Locations**:
  - [kicad-route/src/kicad_route/kicad/planes.py](file:///home/halbritt/git/kicad-route/src/kicad_route/kicad/planes.py#L1-L135) and [planes.py](file:///home/halbritt/git/kicad-route/src/kicad_route/planes.py#L141-L145).
  - [kicad-route/src/kicad_route/cli.py](file:///home/halbritt/git/kicad-route/src/kicad_route/cli.py#L1462-L1478) (`KRT_FLAGS`, `KRT_ENV`).
  - [kicad-route/src/kicad_route/diff_pairs.py](file:///home/halbritt/git/kicad-route/src/kicad_route/diff_pairs.py#L199-L260) (`route`).
- **Observed Failure & Evidence**:
  - `kicad-route planes` does not call KRT's `route_planes.py` or `repair_planes.py`; it runs a 135-line `pcbnew` script that creates static `k.ZONE` polygons and fails if any track or via is added (`plane-plan/1 permits no new or altered tracks/vias`), omitting split-plane strap repair (W05), thermal via arrays (W06), plane stitching/edge fences (W07), and ground return vias (W08).
  - `diff_pairs.py` passes `--no-gnd-vias` and `--no-stub-layer-swap` to `route_diff.py` and rejects `member.get("escape") == "hybrid"` (`D03`), discarding the candidate board when a pair requires short single-ended terminal escape legs.
  - Neither `kicad-route` nor `agent-ee` exposes KRT's `bga_fanout.py` (F01–F03) or `qfn_fanout.py` (F04).
- **Recommended Correction**:
  - Integrate KRT's `bga_fanout.py`, `qfn_fanout.py`, `route_planes.py`, and `repair_planes.py` into `kicad-route` as explicit, native-DRC-graded stages, allow bounded hybrid terminal escape (`D03`) graded by native uncoupled-length rules, and enable return vias (`--add-gnd-vias`) when plane layers are present.

#### F8 (Medium): Three competing workflow drivers (`build_workflow.py`, `pod_driver.py`, `generate.py`) and two incompatible placement stacks fragment development
- **Severity / Impact**: Medium — features built in one stack are unreachable in the others.
- **Source Locations**:
  - [src/agent_ee/build_workflow.py](../../src/agent_ee/build_workflow.py), [src/agent_ee/pod_driver.py](../../src/agent_ee/pod_driver.py#L230-L235), [src/agent_ee/generate.py](../../src/agent_ee/generate.py).
  - Placement Stack A ([blocks.py](../../src/agent_ee/blocks.py), [block_arrangement.py](../../src/agent_ee/block_arrangement.py), [placer.py](../../src/agent_ee/placer.py)) vs. Stack B ([placement_job.py](../../src/agent_ee/placement_job.py), [job.rs](../../crates/placement-engine/src/job.rs)).
- **Observed Failure & Evidence**:
  - `pod_driver.py` uses Stack A (`blocks.py` rigid macro packing) and AI reviewers, but explicitly forbids the `fab` stage (`'fab in stages.order requires AGENTEE-146-compatible pod step wiring; this driver excludes fab'`).
  - `build_workflow.py` uses Stack B (`placement_job.py`) and runs `fab`, but hardcodes `checks_only=True` (skipping AI reviewers) and drops Stack A's block disjointness and side flipping.
- **Recommended Correction**:
  - Retire `generate.py` (experimental 3-part RC script) and unify `pod_driver.py` and `build_workflow.py` into a single staged pipeline (`build_workflow.py`) backed by one functional-block-aware placement engine.

### Plausible Concerns

- **PC1: Unbounded memory/file growth in `build-report.json` and `review/result.json`**: Stage 07 of `acquisition` hashes 1,596 files (including fontconfig cache files `work/*/home/.cache/fontconfig/*.cache-9` inside sandboxed `HOME` directories, [result.json](file:///home/halbritt/git/agent-ee/build/acquisition-release/release-build-v7/stages/07-package-63a92a61a95149678ee3178e64133b22/review/result.json#L254-L258)), producing a 44,632-line JSON result for a 17-part board. Excluding ephemeral `$HOME/.cache` from sealed evidence would reduce artifact bloat and false hash churn.
- **PC2: `dc_path.py` and `active_spice.py` require hand-authored per-board models**: While the validators themselves are sound, requiring a human or external script to author `dc-model.json`, `electrical-intervals.json`, and SPICE `.lib` corner wrappers for every new board without a generator template will bottleneck onboarding of `esp_prog` and `telemetry-pod`.
---

## 3. Workflow and KRT Coverage Summary

### 3.1 End-to-End User Workflow Trace (`agent-ee build run`)

| Stage | Normal Entrypoint Execution | Required Inputs & Source | Actor (Agent / Tool / Human) | Outputs Consumed Next | Surviving Constraints & Real-Design Status |
|---|---|---|---|---|---|
| **1. Engineering Intent → Requirements & Circuit** | `01-requirements`: [build_workflow._requirements_stage](../../src/agent_ee/build_workflow.py#L223-L250) calls `requirement_map.verify_record`. | Pre-authored `spec.md`, `spec-record.json`, `requirement-map.json`, `circuit.json` (hand-scripted outside `agent-ee`). | **External / Manual**: Agent hand-scripts JSON & byte offsets; tool only validates hashes. | `requirement-map.json`check bindings used in stage 07 (`_package_stage`). | Byte-offset spans survive; **not automated** from prose on any real board (`acquisition` used 18 custom scripts). |
| **2. Component Selection** | `02-parts`: [parts_stage.qualify](../../src/agent_ee/parts_stage.py#L120-L245) + `binding_fill.run`. | Pre-authored `parts.json`, `component-selections.json`, `named/*.json`, `ratings.json`, `datasheet-bindings*.json`, PDFs. | **External / Manual** selection; **Deterministic** schema/hash/rating verification. | `build-generated/connectivity-intent.json` and `parts-ratings/receipt.json`. | LCSC/MPN/footprint/rating bindings survive; **no automated catalog search** in `build run`. |
| **3. Board Definition (Scaffold)** | `03-scaffold`: [scaffold.produce_candidate](../../src/agent_ee/scaffold.py#L850-L950). | `envelope.json`, `fab-profile.json`, `project/*.kicad_pro` template, `layout.json`. | **Deterministic** (`scaffold.py`). | Empty `.kicad_sch`, `.kicad_pcb` (outline + stackup + rules), `product/scope.json`. | Outline, layer stackup, fixed footprints, keepouts, and min fab rules survive. Demonstrated on `acquisition`. |
| **4. Schematic & Forward Annotation** | `04-schematic`: [schematic_stage.run](../../src/agent_ee/schematic_stage.py) → [schematic_emitter.py](../../src/agent_ee/schematic_emitter.py) → [forward_annotation.py](../../src/agent_ee/forward_annotation.py). | `build-generated/connectivity-intent.json`, `layout.json`, `.kicad_sym`/`.kicad_mod` captures. | **Deterministic** emission, native ERC, XML netlist export, and `pcbnew` staging. | Populated `.kicad_sch` and unplaced staged `.kicad_pcb`. | UUIDs, pin-pad parity, and net names survive. Demonstrated on `acquisition`. |
| **5. Placement & Routing** | `05-placement-routing`: [placement_job.run_job](../../src/agent_ee/placement_job.py#L1680-L1850) → `agent-ee-placement` ([job.rs](../../crates/placement-engine/src/job.rs)) → `kicad-route` ([cli.py](file:///home/halbritt/git/kicad-route/src/kicad_route/cli.py)). | `placement.json`, `groups.json`, `planes.json` (optional). | **Deterministic** Rust SA placer + `kicad-route` (`route_diff.py` / `route.py` / `planes.py`). | Placed, routed, zone-filled `.kicad_pcb`. | **Breaks functional-block cohesion on `esp_prog`** (`Y1` 10.2 mm from `U1`, `reset` interleaved in `power`); no side flip; 8 via-in-pad sites. |
| **6. Artwork** | `06-artwork`: [routed_artwork.apply](../../src/agent_ee/routed_artwork.py) → [silk_artwork.relocate](../../src/agent_ee/silk_artwork.py) → [final_planes.apply](../../src/agent_ee/final_planes.py). | Routed `.kicad_pcb`, `planes.json`. | **Deterministic** `pcbnew` label relocation + zone refill + native DRC. | Refilled `.kicad_pcb` with relocated silkscreen (only in `build run`, **not** in `place run`). | Demonstrated on `acquisition`; **skipped in standalone `place run`**, leaving 214 silk DRC warnings on `esp_prog`. |
| **7. Engineering Verification & Fab Package** | `07-package`: [build_workflow._package_stage](../../src/agent_ee/build_workflow.py#L295-L330) → [checks.py](../../src/agent_ee/checks.py) → [fab_package.py](../../src/agent_ee/fab_package.py). | All stage outputs + `analysis/*` SPICE/DC/interval/geometry models. | **Deterministic** checks only (`checks_only=True`); **AI reviewers bypassed**. | `accepted-package.json`, `gerbers.zip`, `bom.csv`, `cpl.csv`. | **Omits `cpl_rotation`** (`rotation.status: "not_declared"` on `acquisition`); `build review` CLI fails with `incomplete`. |

### 3.2 Meaningful KRT Coverage Comparison

| Area | Upstream `KiCadRoutingTools` (`v0.23.7`) | `agent-ee` + `kicad-route` Integrated Behavior | Classification |
|---|---|---|---|
| **Functional Placement & Grouping** (`P01–P14`) | [place_simple.py](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_placer/place_simple.py), [simple.py](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_placer/placement/simple.py), [simple_anneal.py](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_placer/placement/simple_anneal.py): courtyard MST `gap_tree`, decap-to-supply-pin site matching, crystal/decap tethering (`CARRY`), `gshift`/`gturn`/`gswap`/`dock`/shove-`repair`, incremental local cost (~2.5 s for 22 parts). | [job.rs](../../crates/placement-engine/src/job.rs): weight-1 anchor distance, global $O(P^2+N+E)$ cost per step (~673 s on `esp_prog`), `GND`/power priced as signals, no tethering, no `gturn`/`gswap`/`dock`, no side flip, ungraded group scatter. | **Defective custom replacement** (much slower and fails functional grouping on `esp_prog`). |
| **Seed Portfolio & Fast Route Judging** (`P12`) | [judge_seeds.py](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_placer/judge_seeds.py) + [converge.py](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_placer/converge.py): fast `--quick 2` or scoped-net tier-3 route probes in seconds. | [placement_job.py](../../src/agent_ee/placement_job.py#L1400-L1560): runs full budgeted `kicad-route` (`--refill-zones` + full native DRC) sequentially per seed; feedback adds max +6 weight. | **Demonstrated but ~10–50x slower** than KRT's tiered judging. |
| **Single-Ended Routing & Recovery** (`R01–R12`) | `route.py`: Rust grid A*, rip-up/reroute, stub layer swap (`R05`), terminal clearance escalation (`R08`), net rescue (`R09`), passive move (`F05`). | [cli.py](file:///home/halbritt/git/kicad-route/src/kicad_route/cli.py#L1462-L1478) wraps `route.py` with native DRC/rule restoration, **but hardcodes** `--escalation off`, `--no-stub-layer-swap`, `KICAD_TERMINAL_ESCALATION=0`, `KICAD_RESCUE_CAP_MOVE=0`. | **Core routing demonstrated; recovery ladders disabled by wrapper**. |
| **Power Widths, Planes & Return Vias** (`W01–W10`) | `route_planes.py`, `repair_planes.py`, `sense_branch.py`: Voronoi split planes (`W04`), MST strap/island repair (`W05`), thermal via arrays (`W06`), area stitching/edge fence (`W07`), return vias (`W08`), Kelvin sense (`W09`). | [planes.py](file:///home/halbritt/git/kicad-route/src/kicad_route/kicad/planes.py) creates static `pcbnew.ZONE` fills only and **forbids any tracks or vias**; `diff_pairs.py` passes `--no-gnd-vias`; `--no-power-tap-neckdown` hardcoded. | **Upstream-available but unintegrated** (`W04–W08`, `W10` missing from `kicad-route planes`). |
| **Differential Pairs, Impedance & Timing** (`D01–D10`) | `route_diff.py`, `impedance.py`, `length_matching.py`: coupled pose A*, multipoint/hybrid recovery (`D03`), stackup Z0 solver (`D04`), intra-pair & group length/delay matching (`D06–D08`). | `diff_pairs.py`, `pair_impedance.py`, `length_groups.py` integrated and verified on micro-fixtures; `D08` AC-coupled in unmerged worktree `ac-matching-545`; **hybrid escape (`D03`) explicitly rejected** ([diff_pairs.py](file:///home/halbritt/git/kicad-route/src/kicad_route/diff_pairs.py#L252)). | **Demonstrated on fixtures; `D03` hybrid recovery blocked; `D08` unmerged**. |
| **Buses** (`D09`, `D11`) | `route.py --bus` (`bus_detection.py`, `bus_corridor.py`) and experimental `awx/route_bus.py`. | `kicad-route run --bus` integrated into `placement_job.py` (`bus_routing`) and verified on synthetic fixture. | **Demonstrated equivalent** for `D09`; `D11` (`awx`) justifiably out of scope. |
| **BGA & QFN Fanout** (`F01–F04`) | [bga_fanout](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_router/bga_fanout/__init__.py) (channel, dogbone, underpad, plane drops) and [qfn_fanout](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_router/qfn_fanout/__init__.py) (surface stub, underpad via). | **Not integrated** in `kicad-route` or `agent-ee`. (`placement_escape.py` only checks 2D bounding corridors during placement). | **Missing capability** (available upstream in vendored router, unexposed). |
| **Verification & Manufacturing** (`V01–V10`, `A06`) | `check_drc.py`, `check_floorplan.py`, `check_assembly.py`, `beautify_labels.py`, `fill_for_delivery.py`. No native schematic/SPICE/Gerber/BOM/CPL pipeline. | Native `kicad-cli` ERC/DRC + schematic parity, `ngspice`, `dc_path`, `fab_export`, `fab_consistency`, `cpl_rotation`, `jlc_dfm`. | **Better in `agent-ee` for native DRC/ERC/SPICE/fab** (except `cpl_rotation` omitted in `build_workflow.py` and floorplan cohesion ungraded in `placement_job.py`). |
---

## 4. Diagnosis of the Lack of Convergence

### 4.1 Root Causes Ranked by Impact
1. **Self-imposed provenance & hash-pinning ceremony (`kicad_route_attempt.py`, `dc_path.py`, `requirement-map.json`)**:
   - **Source of obligation**: **Agent-selected assumption**, not an owner requirement.
   - Pinning all 315 files of `kicad-route` (`PINNED_CODE`), `/usr/bin/python3`, `_pcbnew.so`, `dc_path.py`'s own SHA-256 (`model_sha256`), and exact byte-offset spans in `spec.md` means any 1-line fix to a router or check module invalidates every prior run receipt (`StaleRoutingEvidence`) and breaks the unit test suite (`require_kicad_route` failed all 10 tests in `test_placement_job_refusal.py` on the live checkout).
2. **Algorithmic defects in the custom Rust placement solver (`job.rs`)**:
   - **Source of obligation**: **Owner requirement** ([docs/functional-block-placement-request.md](../../docs/functional-block-placement-request.md) and 2026-10-24 `esp_prog` directive in [AGENTS.md](file:///home/halbritt/git/agent-ee/AGENTS.md)).
   - After rigid macro packing (`blocks.py` + `block_arrangement.py`) failed to pack boards when rectangular block bounding boxes competed for space, the implementation agent built `job.rs` (`place-job/1`) from scratch. Because `job.rs` omitted KRT's courtyard gap-tree objective, decap-to-supply-pin site assignment, crystal/decap tethering, rail down-weighting, group turns/swaps, side flipping, footprint `F.Mask` shapes, and incremental local cost evaluation, `esp_prog` takes 11.2 minutes to produce scattered, interleaved groups and `solder_mask_bridge` DRC errors.
3. **Conflicting incentives from the 123-row KRT capability matrix vs. the owner's `esp_prog` directive**:
   - **Source of obligation**: **Earlier broad `/goal` vs. latest 2026-10-24 owner ruling**.
   - In [docs/system-acceptance.md](../../docs/system-acceptance.md), the agent defined completion as proving all 123 rows of KRT (`P01–A06`) plus `Q1–Q6` qualification boards. To make progress against 123 rows while `esp_prog` took 11+ minutes per placement attempt, `agent-545` pivoted to building synthetic 2-part micro-fixtures (`test_placement_job_impedance.py`, `test_placement_job_stripline.py`, `test_placement_job_delay_groups.py`, `ac_matching.py`) for features (`D04`, `D07`, `D08`) that `esp_prog` does not even use, while leaving `esp_prog` broken.
4. **Overly restrictive wrapper flags in `kicad-route`**:
   - **Source of obligation**: **Agent-selected over-restriction** (`--no-stub-layer-swap`, `--no-gnd-vias`, rejecting `escape == "hybrid"` in `diff_pairs.py`, and forbidding vias/tracks in `planes.py`). While restoring `.kicad_pro` before native DRC grading is a **justified check**, disabling KRT's internal stub-swap and hybrid differential escape prevents valid boards from routing even when the resulting copper would pass native KiCad DRC.

### 4.2 Unresolved Engineering Obligations & Appropriate Evidence
- **JST PH 1.6 mm board thickness vs. `0.8–1.6 mm` datasheet nominal (`acquisition`)**:
  - *Source*: Component datasheet (`B2B-PH-K-S` / `B4B-PH-K-S` 3.4 mm solder tail) vs. JLCPCB standard 2-layer `1.6 mm ±10%` stackup.
  - *Appropriate evidence*: Sourced calculation of minimum lead protrusion ($3.4\text{ mm} - 1.76\text{ mm} = 1.64\text{ mm}$ protrusion beyond the bottom copper surface, exceeding IPC-A-610 Class 2 minimum clinched/vertical solder-fillet requirements) OR selecting a `1.2 mm` stackup—**never** silently rewriting `spec.md` via a preparation script.
- **Functional-block placement quality (`esp_prog` and `telemetry-pod`)**:
  - *Source*: Direct owner requirement ([docs/functional-block-placement-request.md](../../docs/functional-block-placement-request.md)).
  - *Appropriate evidence*: Direct geometric inspection of final component coordinates against block membership (courtyard gap tree = 0 mm or bounded block gap, zero foreign-group interleaving inside a block's convex hull/bounding box, crystal/decap pad-to-pin distance $\le$ datasheet limit), enforced by an existing floorplan check (`check_floorplan.py` or `placement_job.py` eligibility gate).
- **DC rail drop and active startup/shutdown behavior (`acquisition`, `telemetry-pod`)**:
  - *Source*: Derived electrical condition.
  - *Appropriate evidence*: Sourced analytical calculation (`engineering-calculations.json` / KRT's `plane_resistance.py` + `analyze_power_paths.py`) and standard `ngspice` simulation (`active_spice.py`) are sufficient; building a separate host-binary-pinned `dc_path.py` check that refuses to run without pre-hashed `pcbnew.so` digests was unnecessary infrastructure.

---

## 5. Corrective Sequence

### Phase 1: Smallest Coherent Set of Changes for a Dependable First Complete Workflow (Target: `esp_prog` + `acquisition`)
1. **Remove hardcoded code/binary hash blockers (`Simplify / Remove`)**:
   - In [kicad_route_attempt.py](../../src/agent_ee/kicad_route_attempt.py) and [routing_adapter.py](../../src/agent_ee/routing_adapter.py), replace the fatal 315-file `PINNED_CODE` check with git-commit/dirty provenance recording in the run receipt; rely on `kicad-route`'s own native `kicad-cli pcb drc --schematic-parity` validation.
   - In [dc_path.py](../../src/agent_ee/dc_path.py), remove hardcoded `PYTHON_SHA256`, `PCBNEW_PY_SHA256`, `PCBNEW_SO_SHA256`, and self-source `model_sha256` blockers.
   - In `build_workflow.py` and `requirement_map.py`, index requirements by stable section/requirement IDs rather than fragile byte-offset spans in `spec.md`.
2. **Fix functional-block placement and runtime in `placement_job.py` + `job.rs` (`Fix / Reuse from KRT`)**:
   - Port or delegate to KRT's [simple.py](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_placer/placement/simple.py) / [simple_anneal.py](file:///home/halbritt/.cache/agent-ee-qualification/upstream/KiCadRoutingTools/py_placer/placement/simple_anneal.py) mechanics:
     - Exclude `GND` and down-weight power rails in `net_terms`; price group cohesion by courtyard MST `gap_tree` and penalize foreign-group courtyard intrusion into a group's bounding box.
     - Match decoupling caps to their own group's IC supply-pin sites and tether decaps/crystals/load caps (`CARRY`) when moving or rotating their parent IC.
     - Add `gturn`, `gswap`, `dock`, and post-anneal shove `repair`, plus `F`/`B` side flipping when `side_mode: "free"`.
     - Extract footprint-level `F.Mask`/`B.Mask` graphic polygons in `placer_runner.py` so `U2` (`SOT-223`) cannot bridge adjacent pads.
     - Gate candidate eligibility in `placement_job.py` on functional-block cohesion, `via_in_pad == 0` (unless explicitly permitted), and run `silk_artwork.relocate` before final DRC.
3. **Unify `build_workflow.py` verification & fab packaging (`Fix`)**:
   - Add `cpl_rotation` to `build_workflow._package_stage` and remove `'not_declared'` from `fab_package.DONE_ROTATION_STATUSES` when `cpl.csv` is exported.
   - Align `build_workflow._review_candidate` and `build review` so `checks_only` mode is explicit and consistent.
   - Run `esp_prog` end-to-end through `agent-ee build run` to a clean, functionally grouped, DRC-clean, rotation-corrected JLCPCB package.

### Phase 2: Next Capability Milestones (Target: `telemetry-pod` Manufacturing Readiness)
1. **Integrate KRT Fanout & Plane Repair (`Reuse`)**:
   - Expose KRT's `qfn_fanout.py` (`F04`), `bga_fanout.py` (`F01–F03`), `route_planes.py` (`W04, W06–W08`), and `repair_planes.py` (`W05`) inside `kicad-route` and `placement_job.py`, graded by native KiCad zone refill and DRC.
   - Allow KRT's hybrid differential escape (`D03`) and stub layer swap (`R05`) in `kicad-route/src/kicad_route/diff_pairs.py`, grading the resulting copper against native `.kicad_dru` uncoupled-length and skew limits.
2. **Automate Intent-to-Inputs Synthesis (`Build / Consolidate`)**:
   - Replace external `prepare-*.py` scripts and retire `generate.py` / `pod_driver.py` in favor of a first-class `agent-ee prepare` stage (backed by an agent skill + deterministic validators) that synthesizes `parts.json`, `circuit.json`, `layout.json`, `groups.json`, and `placement.json` from `spec.md` + `envelope.json`.
   - Qualify the held `telemetry-pod` board through the unified `agent-ee build run` pipeline.

---

## 6. Acceptance Recommendation & Remaining Uncertainty

### Practical Definition of Done
A board build (`esp_prog`, `acquisition`, or `telemetry-pod`) is **Accepted** when a single `agent-ee build run` invocation produces a sealed output directory where:
1. **Traceability & Integrity**: Immutable user inputs (`spec.md`, `envelope.json`, `fab-profile.json`) are unmodified; all component selections have verified LCSC/MPN/footprint/pin/rating bindings.
2. **Native Electrical & Schematic Correctness**: `kicad-cli sch erc --severity-all` passes with 0 errors; exported XML netlist and board schematic parity match 100%; required SPICE/analytical interval checks pass.
3. **Functional-Block Placement & Mechanical Legality**: Every functional group's members satisfy courtyard gap-tree cohesion ($\le 1.5\text{ mm}$ intra-group gap), zero foreign-group interleaving, critical passive-to-pin distances ($\le$ datasheet limit, e.g., crystal/decap $\le 3\text{ mm}$), edge connector overhang/keepout compliance, and 3D height limits.
4. **Routing & Plane Completeness**: After `kicad-cli pcb drc --refill-zones --save-board`, `kicad-cli pcb drc --severity-all --schematic-parity` reports **0 errors**, **0 unconnected items**, **0 unapproved via-in-pad sites**, and **0 silk-over-copper / silk-overlap errors** on fab layers.
5. **Manufacturing Package Consistency**: `gerbers.zip`, `drill/`, `bom.csv`, and **rotation-corrected** `cpl.csv` (`rotation.status` in `{'ok', 'PREVIEW_REQUIRED'}`) match the final native `.kicad_pcb` SHA-256 and pass `fab_consistency` and `jlc_dfm`.

### Remaining Uncertainty (Bounded Investigations)
1. **`esp_prog` 2-layer routing completion once functional blocks are cohesive**: In `routing-portfolio-20261024`, `esp_prog` routed with 0 opens on `seed 11` only when groups were scattered and 8 vias landed in SMD pads. A bounded test (after porting KRT's group/tether cost and enabling `qfn_fanout` / hybrid diff-pair escape) is needed to confirm whether 2-layer `esp_prog` routes 100% DRC-clean without via-in-pad or requires 0.127 mm / 0.127 mm preferred JLCPCB 2-layer rules.
2. **Reviewer harness availability in headless builds**: `build_workflow.py` currently sets `checks_only=True` because external LLM reviewer binaries (`claude`, `codex`, `agy`) are `null` inside the `bwrap` sandbox ([result.json](file:///home/halbritt/git/agent-ee/build/acquisition-release/release-build-v7/stages/07-package-63a92a61a95149678ee3178e64133b22/review/result.json#L4594-L4613)). Clarifying whether AI review is an optional host-side gate or required in `build run` will settle F3.

---

## 7. Top Three Changes to Improve Likelihood of Success

1. **Replace `job.rs`'s naive group/net objective with KRT's proven functional-group placement model (`simple.py` + `simple_anneal.py`) and gate on block cohesion**:
   - *Why*: Placement is the primary technical bottleneck blocking `esp_prog` and `telemetry-pod`. KRT already solved fast (~2.5 s), cohesive functional-group annealing using incremental local cost, courtyard MST gap trees, supply-pin decap assignment, crystal/decap tethering (`CARRY`), and group turns/swaps. Adopting this model eliminates the 11-minute placement bottleneck and satisfies the owner's core functional-block placement requirement.
2. **Strip hardcoded source-tree and host-binary SHA-256 gates (`PINNED_CODE` in `kicad_route_attempt.py`, binary/self hashes in `dc_path.py`, byte-offset spans in `requirement-map.json`) in favor of output-based native KiCad verification**:
   - *Why*: Self-imposed hash-pinning ceremony is the #1 process bottleneck. It causes every 1-line router or tool improvement to invalidate all prior evidence (`StaleRoutingEvidence`) and currently breaks `agent-ee`'s own test suite against `/home/halbritt/git/kicad-route`. Grading outputs with native `kicad-cli` ERC/DRC and schematic parity preserves full engineering rigor while restoring fast iteration.
3. **Refocus development from the 123-row synthetic KRT matrix to closing `esp_prog` (and then `telemetry-pod`) end-to-end through `agent-ee build run` with KRT's real fanout/plane tools (`qfn_fanout.py`, `route_planes.py`, `repair_planes.py`) and `cpl_rotation` wired in**:
   - *Why*: Building micro-fixtures for 123 KRT rows (`D08` AC-coupling, DDR4 auto-grouping, etc.) created a false sense of progress while leaving the owner's immediate qualification target (`esp_prog`) unable to place its 4 functional blocks or pass `build run`. Driving `esp_prog` and `telemetry-pod` through the single `build run` entrypoint forces real integration of fanout, plane repair, silkscreen relocation, and CPL rotation correction.

# Workflow trace: the actual path through `agent-ee build run` (HEAD 404eba0e)

Supporting investigation for the [independent audit of 2026-10-25](2026-10-25-independent-audit.md).
Read-only; produced by an investigation sub-agent and spot-verified by the auditor.
Line numbers refer to revision `404eba0e`.

## 1. Stage-by-stage table (normal entrypoint)

Entry: `cli.py:881-882` registers `build`; `cli.py:992-993` calls `build_workflow.execute`, then `build_workflow.run` (`build_workflow.py:438-512`). Fixed stage order `STAGES` at `build_workflow.py:22`; each stage runs in `_execute` (`:257-340`). Steps that spawn tools go through `stage_receipts.run_step` (`stage_receipts.py:1135`), a bubblewrap sandbox with the network off (`executor.py:64-85`).

| Stage | What executes (function) | Inputs it needs (schema) | Who produces inputs today | Output consumed next |
|---|---|---|---|---|
| Intent to requirements | `_requirements` `:112-127` then `requirement_map.map_declaration` `:37`, `spec_record.verify`, `account_requirements` `:233-290`, plus envelope-derived obligations `envelope_sources` `:213-232` | `spec.md` (prose), `spec-record.json` (`agent-ee.spec-record/1`, byte-locators), `requirement-map.json` (`agent-ee.requirement-map/1`: per-requirement hand-written check declarations `{kind, params, stage}` or `bench`/`limitation`) | Human or agent hand-authors all three. `spec_record.capture` only tokenises prose into locators. No code maps prose to checks; the map is the mapping. 8 of 29 accounted requirements are tool-derived from `envelope.json` (outline, layers, height, stackup, profile, fixed J1/J2/J3). | `scope.json` (checks list plus baseline), `accounting.json` |
| Parts and circuit | `parts_stage.configuration` `:156-180` runs `python -m agent_ee.parts_stage` in the sandbox, which calls `compile_parts` `:65-153` | `parts.json` (`agent-ee.parts/1` with cad, assignments, selection and datasheet provenance), `circuit.json` (`agent-ee.connectivity-intent/1`: components, nets as explicit pin lists), `layout.json` (`schematic-layout/1`), optional `ratings.json`, captured libraries and catalogs | Circuit is hand-authored (docstring `:68-71`: "Nets and pin selectors remain authored"). Parts manifest built outside the tool with helper APIs (`part_record.prepare_record/qualify_record`, `datasheet_capture`, `symbol_capture`) driven by ad-hoc scripts. | `build-generated/connectivity-intent.json` (footprints bound into intent), ratings receipt |
| Board definition and scaffold | `scaffold.scaffold` `:861-1137` (in-process); empty board via `board()` `:130-174` (layers, stackup, Edge.Cuts only); emitter identity via `emission_plan` `:1052`; `_compile_scaffold_requirements` `:130-159` re-compiles the map and asserts baseline equality | `envelope.json` (`agent-ee.envelope/1`: outline edges, stackup, `fixed_footprints` with edge, side and position), `fab-profile.json`, `parts.json`, generated intent, layout, `planning.scope.json` | `envelope.json` and `fab-profile.json` hand-authored (`build/agent545/prepare-acquisition-build.py:99`). | `product/inputs/` candidate with `project/*.kicad_pro/.kicad_pcb/.kicad_sch` skeleton, `scaffold-receipt.json`, `product/scope.json` |
| Schematic | `scaffold.content_step` `:492` (places envelope fixed footprints `:317-332`), then `schematic_stage.configuration` `:33-92`: sandboxed `emit` (`schematic_emitter.emit_schematic`, `schematic_stage.py:112`) and `annotate` (`kicad-cli sch export netlist` `:132`, `forward_annotation.plan/apply` `:137-143`) | Generated intent, layout, symbol captures, footprint manifest | Deterministic tooling | `project/acquisition.kicad_sch`, board with footprints (unplaced) |
| Placement and routing | `placement_job.run_job` `:1346` then `_execute_job` `:1381`: `capture` `:582`, `solve` `:706` (Rust engine `placement_rust_runtime.resolve_executable` `:20`, installed at `/usr/local/libexec/agent-ee/agent-ee-placement` `:17`), shortlist native DRC, `route` `:1187` (pinned `kicad-route`, backend `builtin` `:1238`), `final_checks` `:1712` (refill DRC `:1767`, `final_planes validate` `:1771`, `check_edge_intent` `:1812`, `check_locality` `:1813`, `board_metrics` `:1814`) | `placement.json` (`edge_connectors`, `fixed_poses`, `proximity`, inline `electrical_locality`), `groups.json` (`{name, anchor, refs}`), `electrical-locality.json`, `planes.json` (`plane-plan/1`), board, project, `.kicad_dru`; plan `search`, `router`, `budget_s` | All hand-authored (`prepare-acquisition-build.py:140-143`, `prepare-acquisition-locality.py`). The script that authored the 10 `proximity` entries is not retained. | Routed `board.kicad_pcb` copied over `scope['board']` (`build_workflow.py:338`) |
| Artwork (silk) | `_artwork` `:223-255` then `routed_artwork.step` `:410` (sandboxed field mover), `qualify_receipt` `:434` | Routed board | Deterministic | Board with moved Reference fields (hash changes) |
| Engineering verification and fab export | `_package` `:197-221`: `_manufacturing_coverage` `:162-185` (MANDATORY kinds `:27-28`), `_review_candidate` `:188-194` = `cli.run_review(..., checks_only=True)` then `checks.run_checks` `:1164` over 55 registered kinds `:1120-1163`; fab via check kinds `fab_package` (`fab_package.py:280`), `fab_export`, `fab_consistency` | `scope.json` checks (hand-mapped), analysis adapters, catalogs | Checks are deterministic; which checks run is hand-mapped; no LLM reviewers (checks-only) | `accepted-package.json` with Gerber, drill, BOM and CPL hashes bound to the board SHA |

## 2. Retained real-design evidence: `build/acquisition-release/release-build-v7`

Verified (confirmed):

- `build-report.json`: `status: accepted`, `accepted: true`, 7 completed stages in order 01-requirements to 07-package; runtime KiCad 10.0.7, pcbnew 10.0.7, Python SHA `e50d468e...`.
- Final board `stages/07-package-.../candidate/project/acquisition.kicad_pcb` SHA-256 `d0a898a2a254c090ba1a4185320938241c255e79c587c82abb27a0bcafcd66e7` (recomputed with `sha256sum`; matches `accepted-package.json.artifacts.board_sha256` and `review/inputs/...`).
- `review/result.json`: 38 checks, 37 `ok/pass` plus `netlist` `ok/info`. Review `outcome` is `incomplete` (limitations: no physical validation, bench obligation ACQ-BENCH-1, "Checks-only run: independent agent reviews and adjudication not performed"); `_package` only requires ok/pass, so build `accepted` coexists with review `incomplete`.
- Package artifacts: gerbers.zip, bom.csv, cpl.csv, 2 drills plus maps, manifest.json; `rotation.status = not_declared` (accepted because `not_declared` is in `DONE_ROTATION_STATUSES`, `fab_package.py:62`); `physical_validation_performed=false`, `supplier_preview_performed=false`.
- Stage receipts: parts `argv -m agent_ee.parts_stage`; schematic 3 receipts (`agent_ee.scaffold` content, `schematic_stage emit`, `annotate`); artwork 1 receipt (`/artwork-tool/artwork.py`), `artwork.json status=repaired`, `source_sha256 bf49c9bf...` to `board_sha256 d0a898a2...` (silk field moves changed the hash after routing; package DRC re-ran on the final hash).
- Placement job report: Rust engine SHA `6d497aa5...`, 6 seeds by 100k steps, 3 feasible, seed 6 chosen; router commit `80e14443...`, `elapsed 286.7 s`; final `eligibility: eligible`, `claims.accepted=false, clean=false`.
- Sealed copy exists: `~/.cache/agent-ee-qualification/agent545/acquisition-release-20261024` (20,046 files; SHA256SUMS lists the PCB hash and `build-report.json` `6451b3f3...`, recomputed and matched). Seal revision `ef2609e2` (2026-10-24), 13 commits behind HEAD.

Inputs bundle (198 files), hand-authored by agent-545 via ad-hoc scripts (all gitignored under `build/`):

- `spec.md` is a literal string in `build/agent545/prepare-acquisition-build.py:14`; corrected later by `build/acquisition-release/prepare-release-inputs.py` (1.6 to 1.2 mm, height clause rewrite).
- `circuit.json` nets are a hand-typed dict (`build/agent545/prepare-acquisition-circuit.py:11-19`).
- `requirement-map.json`, `groups.json`, `placement.json`, `envelope.json` written by `prepare-acquisition-build.py:136,140,143,99`; `electrical-locality.json` by `prepare-acquisition-locality.py`; check bindings, `analysis/electrical-intervals.json`, `engineering-geometry` limits, SPICE checks and `build.json` by `prepare-acceptance-checks.py` (`:27,64`); resistor swaps by `select-release-resistors.py:13` (hand `choices` dict).
- Fixture-specific: `inputs/analysis/geometry_model.py` (121 lines) hardcodes `project/acquisition.kicad_pcb` (line 12) and is the `measurements` adapter for `engineering-geometry`; `annotate_delivery.py` hardcodes the board rectangle `(100,100),45,35`. The requirement map declares 51 check entries (38 unique ids; 7 duplicated across requirements).

Not reproducible on this host: job report paths are `/opt/agent-ee/...` and `/opt/kicad-route` (container); neither `/opt/kicad-route` nor `/usr/local/libexec/agent-ee/` exists on proximal.

## 3. Constraint survival

| Declared intent | Placement job | Final package scope (`07-package/scope.json`) |
|---|---|---|
| `groups.json` membership and anchor | Rust soft cost term `group_nm` (`crates/placement-engine/src/job.rs:1020,1071`); `board_metrics` reports spread only, no fail (`placement_job.py:1034-1085`) | No tool check of group membership. Only the hand-written `engineering-geometry` adapter reads `groups.json`. |
| `placement.json.proximity` (max_mm) | Soft cost `proximity_excess_nm`; accepted seed 6 retained `proximity_excess_nm=1,025,000` (about 1.0 mm aggregate excess) | Not checked at package (only the 5 locality obligations are). |
| `electrical-locality.json` | Hard engine constraint (seed 8 rejected on `electrical_locality`), `check_locality` `:1813` | `locality` check `criterion electrical-locality/1` bound to the same file; survives. |
| Edge and fixed connectors | `check_edge_intent` `:1812`; envelope fixed footprints placed by scaffold `:317-332` | `anchor-j1/2/3` (`connector-edge/1`), derived envelope requirements; survives. |
| Side | No side field in intent except `fixed_poses` (`:497`); side read from native (`:854`) | Only via envelope fixed footprints `side: front`. |
| `planes.json` | `_produce_planes` plus `final_planes validate` `:1771` | Bound into `drc.params.plane_plans` SHA; enforced by `_manufacturing_coverage` `:177-185`. |
| Final DRC versus plan | | `_package` `:217-218` requires `fab_package.artifacts.board_sha256 == digest(candidate board)`; package review ran on d0a898a2 (confirmed). |

## 4. Competing and legacy paths (reachability from `build_workflow.py`)

- Import-graph reachability is misleading: `cli` and `checks` are hubs (`checks.py` imports all 55 check modules; `routed_artwork` imports `placer_runner` which imports `placer`). Excluding the `cli` hub: 199 of 238 modules reachable by import; 39 modules and 21,803 lines unreachable even by import (`placement_access` 1808, `benchmark_run` 1788, `render_gallery` 1318, `block_library` 1267, `rung1_qualified` 1112, `campaign` 1010, `rung1_campaign` 969, `physical_intent` 896, `pod_driver` 650, `rung1_stages` 557, `physical_architect` 446, `block_first` 254, and others).
- Not executed by the build path (imported only transitively or by other CLIs): legacy Python placer cluster (`placer`, `placer_runner`, `placement_adapter`, `placement`, `placement_plans/groups/access/trace/runtime/...`) about 16.8k lines; `block_*` and `blocks` about 9.1k; `generate`, `rung1_*`, `campaign`, `board_build`, `pod_driver`, benchmarks about 11.9k; `physical_intent`, `design_intent`, `requirement_scope` about 2.1k; `happy_adapters`, `kicad_happy*` about 0.9k. `placement_job.py` imports none of `placer*` or `placement_adapter` (its imports: `kicad_route_attempt`, `routing_adapter`, `placement_rust_runtime`, `placement_probe/edges/escape`, `routing_native`, `electrical_locality`).
- `agent_ee.generate` (`generate.py:1-9`, `docs/generate.md:1-9` "private experimental entry point"): the only prose-to-design code, but `parse_spec` is regex bound to the rung-1 RC fixture (`:353-377`, raises unless the topology is R in to out, C out to GND). The `campaign` CLI (`cli.py:1089-1091`) is the LLM-review loop, not used by `build run` (`checks_only=True`). The `place`, `parts` and `scaffold` CLIs (`cli.py:988-997`) are the same producers exposed standalone. `docs/placement-job.md:10-16` states the job is "not an accepted production default" and all legacy callers are unchanged.

## 5. Findings

1. No intent-to-circuit, parts or placement synthesis exists in the normal path (confirmed). `docs/build-workflow.md:20-22` says so; `parts_stage.py:68-71`; the circuit was a hand-typed dict.
2. Requirement-to-check mapping is hand-authored JSON; the requirements stage only validates locators and copies declared checks (`requirement_map.py:258-260,268-276`) (confirmed).
3. Functional-group and proximity intent do not survive to final acceptance: soft engine costs only; the final scope has no group check; the accepted candidate carried 1.0 mm proximity excess (confirmed from `engine.seeds[seed 6].best_terms`; the mapping of `proximity_excess_nm` to `max_mm` is inferred).
4. No automated test runs `build run` through `package` to `accepted=true`: no `tests/test_build_workflow*.py`; only `tests/test_parts_stage.py:383` (diagnostic `--through` up to placement-routing, asserts not accepted), `test_build_artwork.py:34` (`_execute('artwork')` directly), `test_build_review.py` (review only) (confirmed). The sole through-package evidence is the local, gitignored (`.gitignore:4`) v7 run plus its `~/.cache` seal.
5. Docs contradict each other: `docs/build-workflow.md:7-9,189-191` says a complete manufacturing run "remains an open requirement" and is "not claimed as execution evidence", while `docs/system-acceptance.md:45,50-54` records v7 as accepted. `docs/cli-reference.md` has no `build` entry (confirmed).
6. Runtime drift since v7: `build_workflow.py`, `placement_job.py`, `kicad_route_attempt.py` hashes differ from the run's recorded source; the router pin moved from `80e14443` (v7) to `13303bcf` (`kicad_route_attempt.py:39`, `docker/kicad-route/install.sh:11`; a mismatch is reported as a problem at `:2872-2873`). HEAD has not re-run the acquisition package (confirmed).
7. Container-only execution: the engine `/usr/local/libexec/agent-ee/agent-ee-placement` and `/opt/kicad-route` are absent on the host; the bwrap sandbox is required (`executor.py:64`) (confirmed).
8. Engineering verification is whatever the author mapped: `interval_assertions` (hand-entered parameters), `active_spice` (reused corner models), `measurements` with a 121-line design-specific adapter; `_manufacturing_coverage` only enforces the 10 MANDATORY kinds (`:27-28`) (confirmed).
9. Acceptance with `outcome: incomplete`: the build accepts on ok/pass checks while the review verdict is `incomplete` and CPL rotation is `not_declared` (confirmed; `_package :207-213`, `fab_package.py:62`).
10. Manual preparation required: at least 10 one-off scripts (`build/agent545/prepare-acquisition-*.py`, `build/acquisition-release/prepare-*.py`, `select-release-resistors.py`) to capture catalogs, datasheets and symbols, author spec, map, circuit, groups, placement, locality, envelope and profile, bind checks, and write `build.json` (confirmed).

## 6. What was run

Read-only: `git log`, `git log -S`, `git merge-base`, `sha256sum`, Python JSON summaries of `build-report.json`, `job-report.json`, `result.json`, `scope.json`, `accepted-package.json` and inputs; import-graph reachability scripts in the auditor's scratchpad; `PYTHONPATH=src python3 -m agent_ee.cli build --help`; listing of `~/.cache/agent-ee-qualification/agent545/...`; grep of SHA256SUMS. No repository state changed.

## 7. Could not verify

- The script that authored `placement.json.proximity` entries (not retained; only `prepare-acquisition-build.py:143` writing empty lists).
- The v7 run itself (container image, engine binary, `/opt/kicad-route` checkout) is not present on the host; only its hashes are.
- Exact semantics of engine `proximity_excess_nm` versus declared `max_mm` (Rust source not traced in depth).
- Whether the sealed `~/.cache` bundle's full SHA256SUMS verifies end to end (two hashes spot-checked).
- Doc test-count claims (for example "93 tests" in `docs/build-workflow.md:193`) were not re-run.

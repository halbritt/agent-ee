# KiCadRoutingTools coverage versus agent-ee (HEAD 404eba0e)

Supporting investigation for the [independent audit of 2026-10-25](2026-10-25-independent-audit.md).
Read-only; produced by an investigation sub-agent and spot-verified by the auditor.

## Ground truth on the three trees

| Tree | State | Note |
|---|---|---|
| `~/git/KiCadRoutingTools` | `fd59a050`, v0.23.1-222, 2026-10-19 | Not the stock used for comparisons. It is an ancestor of, and 356 commits behind, the stock checkouts in `~/.cache/agent-ee-qualification/upstream/` (`222bb144` v0.23.7 and `d24a760`); later stock (`4cf6d29`, `e358107`) lives under `build/impedance/stock-current` and `build/delay-matching/stock-source/`. |
| `~/git/kicad-route` | `13303bcf`, 195 commits | Vendors KRT v0.23.1 (`vendor/krt` at `295f9ee`) and carries an owned copy at `src/kicad_route/router/` (fork `0494a05` = v0.23.1 plus 2 fixes plus 6 selective cherry-picks, `router/UPSTREAM.json`). 28 commits touch the owned engine. Versus stock `d24a760` `py_router`: 82 files differ, 13 modules exist only upstream (`qfn_fanout*`, `run_plan.py`, `make_plan.py`, `headless_plan.py`, `sense_branch.py`, `net_keepouts.py`, `place_fanout_clearance.py`, `add_rule_area.py`, others), 19 only in the owned tree (`native_delay.py`, `sexpr.py`, movie and render tooling). `py_tools`: 9 of upstream's 27. `py_placer`: 5 of upstream's 12 (no `place_simple.py` or `judge_seeds.py`). |
| `~/git/agent-ee` | `404eba0e` | Pins kicad-route `13303bcf` by SHA-256 of every `bin/` and `src/` blob (`src/agent_ee/kicad_route_attempt.py:36-523`). |

## Does agent-ee route itself?

No. Pure delegation. No A* or grid router exists in `src/agent_ee` (the only `_dijkstra` is `rf_geometry.py:1160`, a CPW branch-length measurer). Two routing call paths, both invoking `bin/kicad-route run` once per attempt:

- Normal path (`agent-ee build run`, `build_workflow.py:296-329`, `placement_job.run_job`, `placement_job.route` at `placement_job.py:1187-1300`): builds argv with `--backend builtin --diff-pairs auto --protect existing|locked` plus optional `--grid-step`, `--pair-layers`, `--pair-impedance` and `--impedance-*`, `--diff-pair-intra-match`, `--bus*`, `--length-match-group-json` and `--group-*`. Recovery (`_route_feedback`, lines 1141-1185) has exactly two bounded repairs: `starved_thermal` runs `kicad-route spokes` and retries with `--reserve-spoke-pad`; `track_dangling` on a pair-free run retries `--nets ... --backend-arg=--force-reroute`. It never uses `campaign`, `blocked`, `barrier-from`, `--max-ripup` or `--ordering`.
- Sandbox campaign path (`routing_adapter.py` plus `kicad_route_attempt.py`, used by `campaign.py`): `tool_argv` (`kicad_route_attempt.py:1793-1813`) passes `run --backend --dir --board --schematic --protect existing --budget [--diff-pairs auto] [--pour-area-loss] [--backend-arg=--layers/--layer-costs] [--experiment-policy]`. Of the 3,061 lines, 463 are the `PINNED_CODE` hash dict, about 230 are JSON shape validators, and the rest is custody (tree hashing, link refusal, staged-copy identity), result-contract re-derivation (`semantics`, `_requalify`, `_run_mismatch`), pair-coverage, layer-policy and area-loss grading, and retention. Zero routing algorithm.

## Option-surface count

| Surface | Count | Reached by kicad-route | Reached by agent-ee |
|---|---|---|---|
| KRT `route.py` long options (owned engine; stock d24a760 adds only `--quick`) | 97 | 23 named literals plus `--backend-arg` passthrough (refused with `--diff-pairs`, and for `--bus*` and `--grid-step`) | 1 via `--backend-arg` (`--layers`/`--layer-costs`, campaign path), `--force-reroute` (repair) |
| KRT `route_diff.py` options | 82 | 25 | only via kicad-route's `--diff-pairs auto`, `--pair-layers`, `--pair-impedance`, intra-match, groups |
| KRT `route_planes.py` / `bga_fanout` / `qfn_fanout` | 41 / 24 / 16 | 0 / 0 / 0 (never invoked; `qfn_fanout` absent from the owned tree) | 0 |
| KRT `KICAD_*` env knobs (owned 234, stock 209 distinct) | | 7 forced (`cli.py:1470-1478`), all to off; `--backend-env` exists | `--backend-env` never passed (docstring, `kicad_route_attempt.py:13`) |
| kicad-route `run` CLI options | 59 | | 14 (sandbox path), about 24 (placement_job path) |

kicad-route always forces `--escalation off --fab-tier standard --strict-sizes --no-power-tap-neckdown --no-fix-drc-settings` and `KICAD_TERMINAL_ESCALATION=0 KICAD_RESCUE_CAP_MOVE=0 KICAD_DYNAMIC_ITERATIONS=0 KICAD_IMPEDANCE_NECKDOWN=0` (`cli.py:1462-1478`, rationale `docs/routing.md:70-106` in kicad-route). KRT's R07 rescue and escalation, F05 cap-move rescue and W02 neckdown are therefore deliberately disabled policy, not missing code.

## Coverage table

| Area | Classification | Evidence |
|---|---|---|
| (a) Functional and block placement | Justified scope difference, own engine; implemented and verified on fixtures; no matched placement comparison versus KRT | Rust `crates/placement-engine` 22,512 lines, 103 `#[test]`, is the only runtime on the normal path (`placer_runner.py:921-925`, `placement_rust_runtime.py:48`); the Python `_plan_local` is kept as a differential oracle (`placement_runtime.py:33-37`, `tests/test_placement_engine_parity.py`, 15 cases). KRT `place_simple.py` is not in the owned tree and is not invoked anywhere. The explicit reuse decision is deferred in `docs/placement-architecture.md:158-170`. The AGENTEE-513 "stock placement baseline" cited in `docs/placement-routing-plan.md:15` has no artifact on disk. |
| (b) Routing core, rip-up and recovery | Available upstream, incompletely integrated (core demonstrated on small articles and esp_prog) | Core via kicad-route run; internal new-copper rip-up at KRT default `MAX_RIPUP=3` (`routing_defaults.py:132`), pre-existing rip and finalize-rip forced off. Recovery limited to 2 repair kinds (`placement_job.py:1141-1185`); kicad-route `campaign`, `blocked` and `barrier` exist (`src/kicad_route/{campaign,blocked,barrier}.py`) but agent-ee never calls them. `--max-ripup`, `--ordering mps`, `--rip-existing-nets`, guide corridors and keep-away: 0 uses in either wrapper. |
| (c) Power, planes, thermal and stitching | Own implementation of zones; KRT W03 to W08 missing | agent-ee `add_planes.py` and `final_planes.py` drive `kicad-route planes`, which creates native pcbnew zones from `agent-ee.plane-plan/1` (`kicad_route/planes.py:1`, `kicad/planes.py`), thermal relief fields only (`plane_plan.py:42-61`). KRT `route_planes.py` (41 options: thermal via arrays, stitch vias and edge fence, ground return vias, region repair) and `repair_planes.py` are never invoked. The own thermal-spoke reservation (`spokes.py`) is the only plane-feedback mechanism; verified in `build/plane-feedback` and `docs/qualification/plane-feedback-2026-10-24.md`. |
| (d) Differential pairs, impedance, length and time matching | Demonstrated equivalent on 4 to 5 footprint articles; owned fork better on native-profile delay | Matched runs retained with argv, env and input hashes (see spot-checks). The owned fork adds `native_delay.py` (KiCad timing profiles); stock fails the same native rule (`build/delay-matching/comparison-final.json`: stock `timing_exit 2`, FAIL). Coverage open per the docs themselves: AC-coupled composition, single-ended, coplanar and multilayer impedance. |
| (e) Buses | Demonstrated equivalent (one 8-lane, 5-footprint article) | `build/bus/matched-results.json`: identical 502.1307 mm, 35 segments, 4 vias owned versus stock `4cf6d29`. Only geometric `--bus` (D09, R03); the upstream OR-Tools bus path is not integrated (research-only per `delta.md`). |
| (f) Fanout, BGA and QFN escape | Missing | `bga_fanout` present in the owned tree with 0 invocations; `qfn_fanout` not in the owned tree; `grep -i fanout src/agent_ee` hits only the hash dict and unrelated modules. `placement_escape.py` is a placement space hint, not an escape router. Docs agree ("fanout ... remain required subsequent work", `system-acceptance.md:305-308`). |
| (g) Verification, DRC and checks | Justified scope difference (own, native-first); KRT V01 to V05 tools not reused | agent-ee `checks.py` CHECK_FUNCS (drc, erc, netlist, topology_rules, routing_equivalence, jlc_dfm, fab_package and others) on `kicad-cli pcb drc --refill-zones` plus `placement_check.py`, `assembly_readiness.py`, `routing_rules.py`. kicad-route grades with native DRC plus preservation checks. KRT `check_drc`, `check_floorplan`, `check_assembly`, `board_score`, `check_impedance`, `fill_for_delivery`: 0 uses in either wrapper (the owned tree keeps `check_floorplan.py` and `check_impedance.py` but nothing calls them). Reasonable given native KiCad is the arbiter, but KRT's V02 per-intent floorplan roster has no matched comparison. |
| (h) Automation, CLI and agent workflow | Justified scope difference | KRT: 10 Claude and opencode skills plus a plugin AI tab (`docs/claude-skills.md`, `kicad_routing_plugin/ai_backend.py`), no MCP anywhere in KRT. KRT A01 to A03 headless plan and replay (`run_plan.py`, `make_plan.py`) are not in the owned tree. agent-ee has its own CLI (`place`, `parts`, `build`, `scaffold`, `campaign`, `review`, `resume`, `adjudicate`), one skill (`skills/agent-ee/SKILL.md`), JSON contracts and sandbox custody: a different, broader automation model (schematic to fab) rather than a port. |

## Spot-checks of matched stock comparison claims

| Case | Same input? | Same settings? | Verdict |
|---|---|---|---|
| Bus (`build/bus/matched-{owned,stock}/request.json`) | yes (hashed, `same input: True`) | identical argv and env except engine path | Genuine matched engine experiment. Caveat: "stock" is stock source under kicad-route's conservative flags and env, run from the owned fork's venv. |
| Delay (`build/delay-matching/matched-*`) | yes (`7fbb95cc...` four times) | identical | Genuine; the owned advantage is the fork's native-profile feature, stock measured by the native rule. |
| Pair impedance (`build/impedance/{owned-integrated,stock-4cf6d29}`) | yes (`714e7afb...`) | no: owned `--track-width 0.325537` (pre-solved outside the engine), stock `--track-width 0.25 --impedance 100`; owned also `--no-short-pair-deferral` | Matched inputs, different pipelines; documented as such (`pair-impedance-2026-10-25.md:120-126`). Same for stripline. |
| Pair transition (`build/pair-transition/matched-comparison.json`) | yes | normalized argv equal | Genuine for the a2 pair, but 25 `owned-*` exploration dirs versus 4 `stock-*`: owned got far more tuning before the reported pair. |

All comparison articles have 4 to 5 footprints; none is a real board. Every comparison is engine versus engine (`route.py` and `route_diff.py` direct), never agent-ee workflow versus KRT workflow.

## esp_prog: what is actually verified

- `build/agent545/board-run-portfolio/final-a1/board.kicad_pcb` SHA `dfffc042...` and `native-drc.json` SHA `bffb4f4a...` match `docs/benchmarks/routing-portfolio-2026-10-24.md`. Recomputed from the JSON: 258 violations, all warnings (165 silk_overlap, 49 silk_over_copper, 21 lib_footprint_issues, 20 text_height, 3 silk_edge_clearance), 0 errors, 0 unconnected, 0 parity. kicad-route `result.json`: `routing.status COMPLETE`, 211 tracks, 30 vias, 389.433 mm, validation PASS, backend commit `be1f3a83`, budget 260 s, `--diff-pairs auto`, no backend args. The claim is verified for that run; the 8 via-in-pad count is a doc figure not recomputed.
- "44 opens in both tools" is not a full-board comparison: `differential-escape-2026-10-24.md:33` shows both engines ran only `route_diff` on the USB pair with 44 other-net opens left untouched; both produced an uncoupled-terminal pair the guard rejects. Matched failure, pair-only.
- The esp_prog placement itself came from agent-ee's Rust planner (6 seeds, agent-chosen 100k-step budget); no stock-KRT placement run of esp_prog exists on disk, so "route complete" is owned-pipeline evidence only. KRT's own `tests/test_esp_prog_diff.py` guards just the pair.

## Findings

1. Routing is a thin, heavily receipted wrapper over a v0.23.1-era fork (high confidence). The owned engine baseline is `295f9ee` plus 8 selective commits; stock is about 578 commits ahead (0.23.7). agent-ee is pinned to that fork by blob hashes, so every upstream fix needs a kicad-route adoption plus re-pin cycle. kicad-route's `routing-engine-migration.md` makes this explicit; the maintenance cost is real and growing (82 of about 150 `py_router` files already diverge).
2. About 75 percent of KRT's `route.py` surface and 100 percent of its env knobs are unreachable from the normal agent-ee path (high). Only `--backend-arg` on the campaign path opens it, restricted to `--layers` and `--layer-costs`. This is a policy choice (conservative, rule-preserving) but it means R05, R06 tuning, R08 and R09 behaviour is "available upstream, not integrated", not "evaluated and rejected".
3. Fanout is absent and QFN fanout is not even in the owned source (high). This is the largest capability gap relative to KRT's F01 to F06 and is acknowledged in the docs.
4. Plane features beyond zone creation are missing (high): no thermal-via arrays, stitching or fence, return vias, or plane-region repair. agent-ee's plane-plan model is explicit and native, which is good for custody, but W05 to W08 would need either KRT `route_planes.py` adoption or new native code.
5. Parity claims rest on 4 to 5 footprint synthetic articles, single runs, and owned-side exploration asymmetry (high). The docs do state "no superiority"; the audit confirms they also do not establish equivalence on any real board.
6. The Rust placement engine is real and on the normal path (high): 22.5k lines, 103 unit tests run by `scripts/build_placement_engine.py:92` (`cargo test` before build), a 15-case Python parity suite. On this host the binary is not installed (`/usr/local/libexec/agent-ee` absent, not on PATH; only `crates/placement-engine/target/release/agent-ee-placement`), so a host run depends on `AGENT_EE_RUST_PLANNER`; the CI image installs it.
7. No matched placement comparison exists (high). The architecture doc rates KRT's grouped placer a "serious replacement candidate" but defers; `placement-routing-plan.md` references a stock baseline (AGENTEE-513) that is not retained.
8. Recovery is narrower than kicad-route already offers (medium). `campaign` (hard-nets-first chains), `blocked` and `barrier-from` exist in the pinned tool and are not called from agent-ee; the only automated repair is spokes plus force-reroute.
9. esp_prog "route complete" is verified on the retained artifacts (high); the 258 warnings are silkscreen, library and text-height, with no copper findings.

## Reuse versus custom: recommendations with cited basis

- Reuse (adopt from pinned kicad-route, no new code): `campaign`, `blocked`, `barrier-from` for incomplete routes. Concrete limitation: `placement_job._route_feedback` returns `None` for every finding class except two, so any other DRC class ends the job.
- Adapt from KRT (the owned tree already has the code): `bga_fanout` for dense escapes; port `qfn_fanout` and `route_planes` stitching and return-via options. Concrete defect: zero fanout capability today, and plane plans cannot express stitching.
- Evaluate before porting: KRT `place_simple.py` and `judge_seeds.py` multistart plus quick-route selection versus the Rust planner. This is the docs' own deferred decision; it needs the missing matched esp_prog or pod placement run before any rewrite.
- Keep custom (justified): plane-plan custody, native-DRC-first grading, the hash-pinned adapter, the Rust planner. Each exists because KRT relaxes rules, writes `.kicad_pro` or moves passives by default (kicad-route `docs/routing.md:70-90`), which the agent-ee contracts forbid.
- Do not rewrite routing: no defect in KRT's router is cited anywhere that a rewrite would fix; the cost is upstream drift, which a refresh (not a rewrite) addresses.

## What was run and what could not be verified

Ran: git, diff, wc and grep inventories across the three trees and stock checkouts; SHA-256 and JSON recomputation of the esp_prog final DRC and kicad-route result; argv, env and input-hash diffs for the bus, impedance, stripline, delay and pair-transition comparisons. No router, placer, test suite or `agent-ee doctor` was run by this investigation.

Not verified: the 8 via-in-pad count; the inter-pair "33 stock findings"; native-rules "same geometry"; that kicad-route's `--backend-arg` passthrough reaches the engine for arbitrary flags (only `--layers`, `--layer-costs` and `--force-reroute` observed); whether the CI image's installed planner matches the committed build receipt.

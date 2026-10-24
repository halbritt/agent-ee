# Whole-board placement job (AGENTEE-515)

Status: first vertical slice of the architecture adopted by chief 531
(decision `b4a5857a`, see [placement-architecture.md](placement-architecture.md),
"Chief decision, 2026-10-24"). It is a working workflow, not an accepted
production default: the existing `placer_runner` planning boundary, the `/1` and
`/2` candidate-set schemas and every live caller (`placer.pack`,
`blocks.pack_block`, `block_variants`, `block_variant_stage`, `reserved_area`,
`pod_driver`, `placement_adapter`) are unchanged. There is no automatic Python
fallback and no superiority or manufacturing claim.

The AGENTEE-515q2 correction slice carries declared connector overhang and
independent drill reservations. The previous prototype ignored USB1's existing
0–2 mm west overhang and dropped NPTH reservations. This slice is correctness
input for review; complete target execution and matched stock qualification
remain outside its scope. AGENTEE-515 remains incomplete.

## One entry point

```sh
python3 -m agent_ee.placement_job run job-config.json --out JOB_DIR [--resume]
python3 -m agent_ee.placement_job metrics BOARD.kicad_pcb --groups groups.json --intent intent.json
```

The config (`agent-ee.placement-job-config/1`) names every external input by
path **and SHA-256**; a mismatch refuses the job before any work. Fixture bytes
stay outside the public repository.

```json
{"schema": "agent-ee.placement-job-config/1", "budget_s": 600,
 "inputs": {"board": {"path": "prepared/board.kicad_pcb", "sha256": "<64 hex>"},
            "project": {"path": "prepared/board.kicad_pro", "sha256": "<64 hex>"},
            "intent": {"path": "prepared/intent.json", "sha256": "<64 hex>"},
            "groups": {"path": "prepared/groups.json", "sha256": "<64 hex>"}},
 "search": {"seeds": [{"seed": 0, "start": "scattered"}, {"seed": 6, "start": "clustered"}],
            "steps_per_seed": 1000000, "shortlist": 3},
 "router": {"checkout": "/opt/kicad-route"}}
```

`budget_s` is ONE overall budget from job start: preparation, search, judging,
export (routing) and final validation all draw on it; there is no phase cap.
The legacy `router.budget_s` field is accepted but ignored: routing receives
the remaining overall time, with no minimum timeout extension. If less than one
second remains, the integer-second router CLI is not started.
`job-report.json` records `setup`, `import`, `prepare`, `search`, `judge`,
`export` and `final_validation` separately. Queue time (admission) is outside
the process and must be recorded by the caller.

## Order of work

1. **Runtime preflight**: pcbnew import, `kicad-cli version`, the Rust engine
   (`placement_rust_runtime.resolve_executable`, same discovery as the planner)
   and `routing_adapter.preflight` on the router checkout (`problems == []`).
2. **Native capture, once** (`placement_probe.extract` plus pcbnew geometry).
   Identity is the full native footprint UUID; `native_ref` is carried
   unchanged. Each footprint gets a role:
   - `movable`: packed and searched.
   - `fixed_body`: locked footprints and fixed poses that draw a body.
   - `fixed_copper_mark`: a fixed pose whose pads are unnetted and which draws no
     fab, silk or courtyard (a copper fiducial). It is a copper obstacle with its
     native pad clearance, never a body.
   - `silk_only`: padless, zone-free, every graphic on a silkscreen layer. Only
     these are excluded from packing.
   **Zone-free supported domain:** every board-level zone is refused before a
   prepared problem can reach the solver. This includes keepouts, rule areas
   and ordinary copper pours, filled or unfilled. Footprint zones and rule areas
   remain refused. Native checks after routing do not make an ignored placement
   obstacle safe; this job has no placement zone engine.
   Custom `.kicad_dru` files and non-default netclasses/assignments are refused
   before a problem is returned. The supported clearance domain is the board
   minimum, the Default netclass and native per-pad overrides. A larger Default
   clearance contributes to the global copper bound. Named non-default classes
   require separate native rule-resolution qualification, even when a global
   maximum might conservatively cover a particular board.
   Capture runs once in a fresh native subprocess, using the controller's
   existing bounded child runner. Every consumed board, Default netclass,
   hole, hole-pair, edge and mask bound therefore comes from an independent
   native load of the saved inputs, even after earlier loads at the same path.
   Omitted fields and absent projects retain KiCad's native defaults; Python
   supplies no replacement constants. Declared bounds are still validated with
   the shared strict JSON reader. Board, project and custom-rule bytes are
   compared before and after capture; a change refuses the result. No project
   file or global native setting is rewritten. The frozen esp_prog project
   remains supported with its omitted `solder_mask_min_width`.
3. **Occupancy.** A proven courtyard is used as such. A present but unproven
   (malformed) courtyard is a hard refusal. A missing courtyard is a metadata gap:
   the footprint gets `derived_occupancy` with its provenance rung, in order
   `fab+pads`, `silk+pads`, `pads`, and the source drawing UUIDs. `User.Drawings`
   is classified as documentation, never as a package outline. Separation of
   derived envelopes is **not** proof of package body separation.
4. **Prepared Rust model and incremental search** (`crates/placement-engine/src/job.rs`,
   schema `agent-ee.placement-job-input/1`, one process for the whole seed
   portfolio). `prepare` validates once: full-UUID identity with deterministic,
   collision-checked aliases (`fp000`... in UUID order, never a UUID prefix),
   roles, occupancy labels, rules (largest net-class clearance, per-pad native
   clearance, copper-to-edge clearance, solder-mask minimum web), groups,
   proximity rules and edge rules. `SearchState::evaluate` computes the delta of a
   move (single part, quarter turn, rigid group translation) from a bucketed
   spatial index queried at the **new** pose plus the old contributors, with
   movers paired among themselves; `commit` is generation-checked and atomic,
   `reject` leaves the committed state unchanged, and a stale trial is refused.
   `full_evaluate` recomputes everything independently; every retained state is
   checked against it, and `verify_every` enables a per-commit differential check.
   All hard verdicts and objective terms are integer nanometres (areas i128).
   Seeds are independent named `splitmix64/agent-ee-515-v1` streams; scattered and
   clustered starts are explicit. The engine returns a shortlist of hard-feasible
   states. When none exists it returns a **diagnostic shortlist**: least-infeasible
   states with their exact named violations, labelled `feasible: false`.
   An `evaluate` request re-evaluates explicit pose vectors (predicted native pad
   positions, terms, violations) without searching.
   Pad copper uses KiCad's native `TransformShapeToPolygon`, `ERROR_OUTSIDE`,
   with a 1 µm curve tolerance. Circle, rectangle, oval/obround, roundrect and
   custom pads carry labelled `conservative_native_polygon` contours in the
   existing `copper_local_nm` entries. Rust uses the shared exact polygon
   distance implementation, comparing separation against the larger native
   per-pad override and the captured board/netclass bound. Bounds still serve
   the spatial index and copper-edge gate. Curves are outer approximations,
   holes are filled, and copper layers are projected together; these labelled
   reservations can reject legal seats. Copper graphics and legacy input
   entries retain their conservative box behavior. Unsupported native shapes,
   per-layer padstacks, missing/invalid contours or contours outside indexed
   bounds refuse preparation. Body/courtyard and mask reservations are unchanged.
   These search bounds do not replace native DRC or excuse U2 artwork errors.
   `holes_local_nm` independently retains every supported round or slot drill,
   including NPTH pads with no copper and fixed marks with no body. Native
   `TransformHoleToPolygon(ERROR_OUTSIDE, 1000 nm)` provides outward contours;
   the same exact polygon separation and quarter-turn transform are used for
   drills. Hole-to-copper uses native board `m_HoleClearance`, without a copper
   pad's local clearance override; hole-to-hole uses `m_HoleToHoleMin`. Drill
   reservations participate in the spatial index and incremental evaluation.
   Invalid drill dimensions and secondary/tertiary drills are refused.
   KiCad 10.0.7's sampled native `hole_to_hole` reports cover round drilled
   holes; the slot-pair controls emit no such item. Slot pairs retain the board
   minimum as a conservative search reservation, with separated positive
   controls. Their engine refusals are not reported as native violations or
   exact native verdict agreement.
   Missing hole entries on legacy prepared inputs keep their earlier contract;
   new native capture always supplies the independent drill inventory.

   An edge connector carries its declared `overhang_mm` interval in nanometres,
   bound to its native identity and the named west/east/north/south edge. Only
   that footprint's occupancy containment uses the permitted outward strip;
   containment on the other three edges, copper-to-edge clearance and the
   existing maximum setback remain in force. Overhang is the nonnegative
   outward excursion of the existing occupancy, so a zero minimum permits an
   inboard seat within the setback. A positive minimum requires that excursion.
   Malformed intervals, ambiguous references and unknown edges refuse capture;
   Rust also validates intervals. Source geometry and derived occupancy are
   unchanged. Fab stroke bounds remain metadata-derived reservations, never
   physical proof of the connector body.
   A zero minimum without a declared maximum is carried as an open interval
   and grants no outward containment exception. A positive minimum without a
   finite maximum is explicitly unsupported. The existing CON1/CON2 zero-only
   declarations therefore keep strict containment.
5. **Shortlist native checks before promotion.** Each candidate is applied to a
   disposable native board by UUID (pcbnew), saved, the frozen project bytes are
   restored next to it, and it is reloaded. Integrity requires the same UUID set,
   refs, sides, locks, FPIDs, pad UUIDs/numbers/nets and graphics; every saved pose
   equal to the engine pose; every native pad exactly where the engine transform
   put it; unchanged fixed parts; and unchanged non-footprint board content (the
   only normalization: SaveBoard's standard layer user names). Native DRC
   (`kicad-cli pcb drc --severity-all`) errors are split into **pre-existing**
   (all items in one footprint and the same finding on the unchanged input) and
   **placement-introduced**.
   Before writing, `materialize(..., intent=...)` derives protected UUIDs from
   source-board native locks and the captured fixed intent. Any protected x, y,
   rotation or side difference, or a protected `moved: true` row, refuses the
   candidate independently of its predicted pads.
6. **Incumbent custody.** `incumbent.json` is replaced atomically (`os.replace`)
   only by a strictly better candidate with preserved integrity, engine-feasible
   poses and zero placement-introduced errors. A failed trial, crash or worse
   candidate never touches it, and candidate directories are never overwritten.
   Without a valid incumbent the best diagnostic candidate is kept separately in
   `best-diagnostic.json` and may be routed **only as an exploratory run**; the
   report then says `NO valid incumbent`.
7. **Pinned router process** on a fresh copy (`kicad-route run`, the adapter's
   argv shape, `--protect existing`, `--diff-pairs auto`, the checkout's own
   state). The sandboxed `routing_adapter.step` refuses a candidate without a
   schematic, so a schematic-less board is routed by this direct process call;
   the router's preflight identity is recorded either way.
8. **Final checks, independent of the search**: reload the routed board; footprint
   UUIDs, refs, poses, sides, locks, FPIDs and pads must be unchanged by routing.
   The shared S-expression reader compares complete saved native content,
   including every pad's size, drill, shape, layers and local transform,
   footprint graphics/fields, board setup/rules, outline and other drawings.
   Tracks, arcs and vias are routing-owned; zones are protected unless their full
   UUIDs are explicitly supplied by the controller as owned. This job currently
   owns no zones. An integrity mismatch yields `eligibility: INELIGIBLE`,
   `physical_checks: FAILED` and job status `integrity_failed`, even if DRC is
   otherwise clean. Router-output existence alone cannot yield `completed`.
   Native
   DRC with `--refill-zones`; opens; errors split into pre-existing,
   placement-introduced and routing-introduced; warnings by type; measured route
   length (segments and arcs, by layer), vias, geometric via-in-pad (via centre
   inside native pad copper), grouping and declared-edge setbacks. Pre-existing
   errors stay FAILED physical checks: the report claims `accepted: false,
   clean: false`.

## Cancellation, deadline and resume

After the job owns its output directory, a `JobRefusal` produces a terminal
`job-report.json` with status `refused`, the exact reason, elapsed time, completed
phase timings and available runtime identities. Acceptance and clean claims stay
false. When an attempt has been allocated, its `job-report-aN.json` is also
written; before then, the attempt is null. The CLI prints the refused status and
exits nonzero. Refusal never replaces the incumbent or diagnostic board.

A nonempty directory without `--resume` is refused without writes. Resume captures
the input in memory and checks the checkpoint identity before writing capture,
problem or report files. A failed identity check, or cancellation/deadline before
that check, leaves existing evidence untouched.

A `CANCEL` file in the job directory stops the engine and controller. The same
monotonic deadline is checked before judging, routing and final DRC. Nonblocking
pipe I/O checks cancellation and remaining time at most every 50 ms during owned
capture, engine, router and DRC subprocesses, including stalled stdout or stdin. On
interruption the owned process group is killed and its leader reaped; no next
phase starts. The report says `cancelled` or `deadline` and the incumbent stays
intact. An interrupted child is recorded without claiming solver completion or
native DRC evidence. The engine also checks its deadline internally.
Finished seeds are checkpointed atomically (`checkpoint.json`, bound to the job
identity: problem, config and engine hashes). `--resume` refuses a checkpoint of
another identity, skips completed seeds and restarts unfinished ones from their
start: it is restart at a seed boundary, not continuation of an interrupted PRNG
state. Diagnostic candidates of earlier seeds are not carried across a resume.
Each attempt writes its own `candidates/aN-*`, `input-drc-aN`, `route-aN`,
`final-aN` and `job-report-aN.json`; `incumbent.json` carries across attempts
and is still replaced only by a strictly better checked candidate.

## Tests

### Native contour complexity

The whole-board job admits at most **2,048 raw vertices per contour**, **16
contours per copper/drill item**, and **16,384 contour vertices per problem**.
`job.rs` declares these bounds; native Python capture mirrors them, with a parity
test. Capture refuses excess geometry with the reference, footprint UUID, pad
UUID, contour index/count and applicable bound. Rust independently validates the
same limits before preparing geometry. The older general planner's 512-vertex
contract is unchanged.

These limits admit the retained esp_prog input's 4,328 vertices in 111 contours,
including U2's 1,118-vertex contour, with headroom. They do not simplify contours
or substitute bounding boxes. Existing exact normalization and invalid-polygon
checks remain in force. Preparation triangulates nonconvex copper once and
records convex-piece and original-boundary edge bounds. Moves transform this
prepared geometry. Exact overlap uses the convex pieces; exact clearance uses
only original boundary edges, never triangulation diagonals. Bounding boxes only
reject impossible pairs.

On the retained input, the limit-only repair took 53.817 seconds to evaluate its
saved poses. The prepared-geometry repair measured 0.042 seconds preparation and
0.022 seconds evaluation in the release Rust test on proximal (2026-10-24).
This measures geometry evaluation, not placement quality or routing throughput.
An original synthetic case at the 16,384-vertex total limit (eight 2,048-vertex
concave stars across two overlapping items) measured 1.238 seconds preparation
and 0.031 seconds evaluation. These are measured cases, not worst-case latency
guarantees; the controller deadline still bounds preparation and evaluation.
Regression coverage compares transformed prepared results with the previous exact
calculation, checks touching and exact-clearance boundaries, preserves the native
U2 region, and kills limit-scale preparation at the controller deadline.

`tests/test_placement_job.py`, `tests/test_placement_job_guards.py` (original
synthetic boards built through pcbnew) and the `job::tests` Rust module. The
esp_prog capture test reads the external AGENTEE-513 bundle by SHA-256 when
`AGENT_EE_ESP_PROG_PREPARED` names it. Its absence fails under
`AGENT_EE_REQUIRE_TOOLCHAIN=1`; only optional mode may skip it. The guard tests
exercise both modes, coherent protected moves, native geometry/rule mutations,
phase-boundary cancellation and live stalled-child termination.

`tests/test_placement_job_clearance.py` checks each supported native pad shape,
clearance overrides and refusal boundaries. `test_placement_job_reservations`
adds all-edge overhang controls, the reviewer R1–H1 regression, movable/fixed
round and slot holes under rotation, hole-to-copper versus hole-to-hole rules,
bodyless marks, supported Default clearance and a larger non-default netclass
negative that native DRC rejects and capture explicitly refuses. The separate external esp_prog
gate, `python3 -m unittest -fv tests.placement_clearance_agreement`, requires
`AGENT_EE_ESP_PROG_PREPARED`, `AGENT_EE_USB1_SCAN`,
`AGENT_EE_USB1_SCAN_PROBLEM` and `AGENT_EE_CLEARANCE_EVIDENCE`. They retain the
as-built full-feasibility gate, the original 50 deterministic seats from the
sealed scan enumeration, 10 deliberate pad intrusions, and 10 additional
positive/boundary controls across x = 117.575–117.775 mm, each checked by native
DRC. A separate control isolates USB1 drill-to-fiducial clearance. Complete
native reports and separate copper-pair and full-engine matrices are retained.
The comparison is scoped to USB1 and the specified fiducial; it establishes
neither whole-board placement legality nor AGENTEE-515 completion. The matrix
separately scopes copper-pair feasibility (excluding mask/drill and unary
terms) and full engine feasibility (all encoded terms, compared with the
native pair's electrical/hole/mask violations at every severity). Neither matrix measures whole-board
legality or establishes that unchecked scan seats are native-clear. A false
feasible native-clearance result or failed as-built agreement stops the lane.

`tests/test_placement_job_capture.py` compares capture with a separate native
process that only calls `pcbnew.LoadBoard` and reads the consumed settings.
Weak and strong explicit values followed by omission, absent projects and
absent Default clearance exercise same-path cache history. The keepout control
has native `items_not_allowed` findings and was feasible under the held parent;
capture now refuses it before solving. Copper-zone, rule-area, footprint-zone
and zero-zone controls define the supported domain. Capture cancellation,
deadline, child failure, missing output and input mutation fail closed.

## Origins

The transaction, spatial-query and seeded-restart semantics follow KRT v0.23.7
mechanisms selected in AGENTEE-514. They were reimplemented after source
inspection (not clean-room); `job.rs` carries the upstream MIT notice and
`docs/file-origins.json` origin `KRT-515` records the attribution.

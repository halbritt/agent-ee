# Whole-board placement job (AGENTEE-515)

On 2026-10-24, a retained esp_prog run finished in 139 seconds with zero native
DRC errors and zero unconnected items, but violated two declared connector edges.
It was not an accepted design. See the
[run report](benchmarks/placement-job-2026-10-24.md) for inputs, reproduction,
regressions and remaining intent/assembly limitations.

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

Functional groups may include fixed connectors and other fixed physical
members. A fixed group anchor supplies the initial cluster position; group
translations move only movable members. Fixed members retain their original
poses and still contribute to group distances. This repairs the sourced
acquisition board's `unknown movable member J1` refusal without unlocking its
connectors. Native materialization and final verification still check locks
and exact saved coordinates.

## One entry point

```sh
agent-ee place run job-config.json --out JOB_DIR [--resume]
agent-ee place metrics BOARD.kicad_pcb --groups groups.json --intent intent.json
```

`python3 -m agent_ee.cli place` invokes the same public command when running
from source. The earlier `python3 -m agent_ee.placement_job` interface remains
compatible. This is the placement-to-route entrypoint; the complete engineering
workflow remains unfinished in the [system acceptance map](system-acceptance.md).

The config (`agent-ee.placement-job-config/1`) names every external input by
path **and SHA-256**; a mismatch refuses the job before any work. Fixture bytes
stay outside the public repository.

## Supported intent boundary (AGENTEE-520)

New intent documents use `"schema": "agent-ee.placement-intent/1"`. Existing
unversioned documents containing only the supported fields remain valid without
rewriting their bytes. Validation runs at public `place run` intake, public
`build run` intake, and both capture boundaries. Unknown fields and unsupported
declarations refuse with a path such as `intent.assembly.sides` or
`intent.edge_connectors[0].side` before native capture or search. Duplicate JSON
keys, nonfinite values and malformed declarations refuse as well.

| Intent field | Supported meaning |
| --- | --- |
| `fixed_poses[]` | `ref`, `x`, `y` identify an existing native pose in mm; optional `rot` (quarter-turn degrees) and `side` (`F`/`B`) must match that pose. The part stays fixed. `basis` and `why` are text evidence, not additional constraints. |
| `edge_connectors[]` | Required `ref`, `edge` (`west`/`east`/`north`/`south`); optional `max_setback_mm` and `overhang_mm` (`min`/`max`) retain existing native and solver enforcement. `class` accepts `edge_receptacle` or `connector_affinity`; `context.mount_mode` accepts `edge_mount` or `top_mount` and selects the existing seat calculation. `note`/`source` are text evidence. |
| `proximity[]` | Required `ref`, `near`, nonnegative finite `max_mm`, and `pads` mapping exactly those two refs to nonempty lists of distinct pad numbers. Missing native pads refuse. This scores minimum Chebyshev distance between pad centres; it is a preference. `basis: "pad_edge"` refuses because the engine does not measure pad edges. Optional `basis` prose and `context` text fields (`limit`, `basis`, `role`, `source`) retain descriptive evidence; they cannot change this metric or establish a hard limit. |
| `electrical_locality` | The existing `agent-ee.electrical-locality/1` validator, provenance classification, Rust feasibility and final native checks remain authoritative. Unknown nested fields refuse; a label alone does not establish a hard limit. |

The separate groups input requires `groups[]` with `name`, `anchor`, `refs`;
names and membership must be unique, and the anchor belongs to its refs.
Optional group `role`/`why` are descriptive text. Optional top-level `schema: 1`
and `board` retain the legacy group envelope; `board` is a descriptive label,
while the actual native input is bound by `inputs.board` and its SHA-256.
Group anchor distance remains a preference. Group `near`, side, zone, region
or hard-cohesion declarations are unsupported and refuse.

Jobs record `intent_contract` with the supported version, input form, validator
hash, constraint/preference distinction and unsupported capabilities. The
validator hash participates in checkpoint identity. Adding the supported schema
to an otherwise identical document leaves captured geometry and constraints
unchanged, but changes input bytes and therefore requires a fresh job.

Rich KRT `floorplan-intent` documents (`schema: 1`, `min_reader`, `assembly`,
`blocks`, `keepouts`, `envelope`, `defaults`, `legality_budget`, `must_lock`,
`decaps`, etc.) are not this contract, even if a collection is empty. The raw
retained esp_prog input now refuses at `intent.assembly.sides`; its connector
side and group-nearness declarations also need explicit treatment. Keep the
original input and its hash. Migration requires a separate source-bound mapping
of every declaration to an implemented constraint, a named unsupported
requirement, or clearly descriptive evidence. Deleting unsupported fields does
not establish equivalent intent or qualify that board. AGENTEE-479 owns this
representative's compatibility mapping. Acquisition's minimal unversioned
intent remains supported. No automatic filtering or KRT-reader fallback runs.

Native non-default netclasses and custom rules are carried through the job.
`inputs.project` must name the board's same-stem `.kicad_pro`. If a same-stem
`.kicad_dru` exists, `inputs.rules` must name it with its SHA-256; omission or a
different companion path refuses the job. The full build derives this binding
from the preceding native project automatically. Materialization, routing and
final grading preserve the exact authored bytes. Changed or missing routed
project/rule files make the result ineligible, even if copying the original
rules back for final diagnostics yields a clean DRC result. Rule changes also
change the resumable job identity. Before search, a disposable native rule-loading
witness rejects malformed rules that KiCad can silently omit from a successful
CLI report. Its added track and severity override never enter the actual design.
See the [native-rule qualification](qualification/native-rules-2026-10-24.md).

An optional `inputs.planes` supplies a path and SHA-256 for a captured
[`agent-ee.plane-plan/1`](add-planes.md). Each routing attempt creates these zones
on its own copy of the selected placement. The final board is refilled, saved
and independently checked against the same plan before it can be eligible.
Only these controller-owned zone UUIDs may differ from the zone-free placement
candidate. The final result records native plane evidence and the producer's
hash-bound receipt; existing footprints, placements and other protected native
content still have to match. Unknown input names are refused.

For a plane job whose staged native input contains no tracks or vias, the router
may rip up its own generated copper (`--protect locked`). Authored copper keeps
the default `--protect existing` policy. A fully connected result with a newly
starved thermal may receive one retry reserving the exact native pad/layer
corridors. A remaining dangling track may receive one scoped reroute, only when
the saved result establishes that it has no differential pairs and every other
preservation check passes. Both repairs share the original candidate's remaining
budget, retain all attempts and undergo unchanged native and final placement
checks. A rejected router result cannot complete the job. See the
[qualification and limits](qualification/plane-feedback-2026-10-24.md).

An optional `inputs.locality` supplies a path and SHA-256 for the existing
`agent-ee.electrical-locality/1` document. The public build derives this input
from its required `electrical-locality/1` check, so search and final acceptance
consume the same declaration. An inline `intent.electrical_locality`, if also
present, must equal it. Missing endpoints, wrong nets, unavailable native copper
centroids and corrupt citations refuse preparation.

Sourced hard limits participate in Rust feasibility using exact squared
Euclidean distances between native copper-land centroids. Rational centroids
and quarter-turn rotations retain exact boundary comparisons. The incremental
cache updates when either endpoint moves. Only the search penalty is capped for
extreme distances; the exact feasibility decision is unchanged. Independent
native checks measure every declared leg after materialization and again after
routing. An exceeded hard limit makes a candidate ineligible. Unsourced limits
remain advisory and never acquire owner authority.

A captured engineering citation can establish a declared engineering constraint
under the shared source-quantity checks. It retains class `engineering`;
an engineering label alone remains advisory. The older `intent.proximity`
minimum Chebyshev pad-centre score remains a preference and supplies no
hard-limit acceptance.

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

Optional `router.grid_step_mm` selects the builtin router's search resolution
in millimetres, including its coupled differential-pair and remaining-net
passes. For example, `0.025` requests a 25 micrometre search grid. It does not
change native widths or clearance requirements. Omission uses the router
default; the pinned CLI rejects non-finite, sub-nanometre or incompatible
controls. The chosen value is retained in the configuration and routing record.

Optional `router.intra_pair_match` requests length matching of newly routed
pairs. Supply both `tolerance_mm` and `amplitude_mm`, for example
`{"tolerance_mm": 0.1, "amplitude_mm": 2.0}`. The amplitude bounds the meander
search; it does not change a native clearance, gap, length or skew requirement.
Both values must be finite and at least one nanometre. Saved pair lengths must
meet the requested tolerance, and all native rules still apply. Omission leaves
matching disabled. Existing protected copper is preserved, so an already
connected pair outside tolerance is rejected rather than silently altered.
These controls are part of the job identity and cannot change on resume.

Optional `router.pair_layers` supplies the enabled native copper layers for new
pair tracks, for example `["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]`. Omission
keeps the router's outer-layer default. The nonempty list must contain unique
layer names; native routing rejects disabled or non-copper layers. The final
router grade checks saved track layers independently. Existing valid copper
and through-via spans remain subject to native rules. Layer selection is bound
to resume identity. Matching measures summed planar copper; native electrical
length/skew rules are separate requirements and must also pass.

Optional `router.bus` enables geometric bus grouping, middle-out ordering and
shared routing corridors in the existing single-ended pass. For example,
`{"bus": {"detection_radius_mm": 20, "min_nets": 3}}` expands endpoint clustering
for a wide header. Automatic differential pairs remain protected and outside
the bus scope. Omission keeps bus grouping disabled; an empty object enables
the backend defaults.

The accepted fields are `detection_radius_mm` and `attraction_radius_mm`
(finite and at least 0.000001 mm, default 5), `attraction_bonus` (nonnegative
integer, default 5000), and `min_nets` (integer at least two, default two).
The existing geometric filter may require more members than the raw clustering
minimum. Unknown fields and invalid bounds refuse before routing, and these
controls are bound to the job's resume identity. Actual groups and their
diagnostics appear in the retained router result's `process.bus_routing`.
These are search preferences, not electrical bus identities or requirements
for length, delay, impedance or coupling. Native final verification still
decides connectivity, clearances and preservation.

Optional `router.ac_match` matches complete P/N copper paths through explicitly
declared populated series parts, for example
`{"chains":[["C1","C2"]],"tolerance_mm":0.1,"amplitude_mm":3}`.
The router verifies native bridge topology before routing. Final saved-board
checks independently measure every two-terminal segment and add its contribution
to P or N. Skew plus the printed measurement rounding bound must meet the requested
tolerance. `final.ac_matching` contains paths, distances, requirement and native
source hashes. DNP/missing/crossed/ambiguous bridges and incomplete or unmatched
paths reject. Existing connected sections remain protected and contribute to the
target. The normal build placement producer carries the same request unchanged;
changed requests cannot resume an old job. Component internal delay and physical
signal integrity are separate obligations. Combined AC, coupled-group and
intra-pair controls must agree because their backend tuning settings are shared.

Optional `router.length_match` requests geometric matching of explicit
single-ended two-terminal net groups. Supply `groups`, `tolerance_mm` and
`amplitude_mm`, for example
`{"groups": [["DQ0", "DQ1", "DQ2", "DQ3"]], "tolerance_mm": 0.1, "amplitude_mm": 3}`.
Optional `spacing_widths` defaults to 2 and must be at least 1. Numeric controls
must be finite and positive. Names are exact; missing, duplicated, out-of-scope,
coupled-pair, multipoint or zone/graphical-copper members refuse. These controls
are bound to resume identity and are separate from intra-pair matching.
The job serializes each group as a JSON array for the public router, preserving
names beginning with a hyphen and literal glob characters. Direct router users
can use `--length-match-group-json '["-DQ0", "--help"]'`; saved router commands
use the same form to preserve group boundaries during replay.

Both the public router and `final_checks` measure the saved/refilled board with
native KiCad terminal-to-terminal `fromTo` paths. `final.length_matching` binds
the board hash and request, all resolved terminals, measurement coverage and
native skew findings. A connected loop cannot inflate the useful path length.
Each group gets its own verification-only native copy and an explicit skew rule;
a zero-length limit deliberately emits one measurement finding per path to
prove coverage. Those measurement findings are labelled evidence, not defects
in the original design. Original native rules remain unchanged and are graded
separately. Missing measurements or ignored required checks fail. This operation
does not establish propagation time, AC/inter-pair matching or signal integrity.

For nominal propagation-delay matching, supply `tolerance_ps` in place of
`tolerance_mm`, for example
`{"groups": [["DQ0", "DQ1"]], "tolerance_ps": 1, "amplitude_mm": 3}`.
Exactly one tolerance domain is allowed. Every member needs an effective,
enabled single-ended KiCad 10 tuning profile with explicit per-layer widths and
delays and a positive via delay. The native board must declare the complete
stackup, including every dielectric sheet's thickness and permittivity, and
keep via-height calculation enabled. Preflight checks original input bytes
before native saves can insert material defaults. Changed timing requirements
invalidate resumption just as changed geometric requirements do.

The router uses authored profile rates and the signal's used via span to plan
meanders; existing connected members can set the target while retaining their
geometry. Final native `fromTo` time-domain measurements must cover every member
and satisfy the requested skew after all saves/refills. Unmodelled layers,
profile-width mismatches, missing measurements, ignored checks and failed skew
reject. `final.length_matching` also binds the native project hash. Native
profile units are attoseconds/mm; the public tolerance is picoseconds.

This checks nominal authored timing, not physical model calibration or signal
integrity. Profile values need engineering substantiation and explicit review
when stackup/geometry changes; KiCad does not refresh them automatically. The
layered qualification uses a stated Er=4 assumption and calculations, not a
supplier-qualified stackup. Native input pours remain outside the placement
model; declare planes in `inputs.planes` for the normal plane producer.

Optional `router.pair_impedance` requests nominal differential impedance
synthesis with an explicit target and verification limits:

```json
{"pair_layers": ["F.Cu"],
 "pair_impedance": {"target_ohm": 100, "tolerance_percent": 10,
                    "reference_net": "GND", "max_uncoupled_mm": 3}}
```

All four fields are required; the numbers must be finite and positive, with
tolerance below 100 percent. The capability requires one explicit signal layer
and native stackup copper thickness, adjacent dielectric thickness and Er. An
outer layer uses the uncoated microstrip model. An inner layer uses symmetric
stripline: the two immediately adjacent dielectrics must have the same Er and
heights equal within one native nanometre. The router uses its existing analytic
model to select width above the native class floor. It preserves the authored project and rules. The declared
plane plan must produce actual filled reference copper on the adjacent layer
for microstrip, or both adjacent layers for stripline. Use `"pair_layers":
["In1.Cu"]` for the inner-layer case, with matching native stackup and planes.
Missing stackup data, an unreachable target or missing reference cannot pass.

After final zone refill, the job independently measures saved widths, parallel
pair gaps, coupled length and actual native reference fill, and binds its report
to the final board hash. Terminal escapes are reported separately and limited
by `max_uncoupled_mm`; they acquire no impedance claim. Targets and limits are
part of resume identity. Both stripline references must cover the traces and
the complete coupled corridor between them. Inner through-hole terminal
openings are limited to the same-net pad, the smallest explicit zone clearance,
and the reported native polygon approximation allowance. Larger openings fail.
This model excludes via/arc routes, asymmetric or multilayer impedance and
coplanar pours. These are nominal analytical calculations; supplier tolerances,
field simulation and physical measurement remain separate obligations.

The captured native project can also declare the router's existing assembly
policy under `kicad_routing_tools.same_net_pad_clearance`. A positive value in
millimetres keeps newly routed vias away from same-net SMD pads and paste
openings in both single-ended and coupled-pair routing. For example, `0.15`
selects an engineering routing margin. This project setting survives the normal
snapshot and routing chain; the final project remains byte-identical to its
captured input. It does not establish final assembly acceptance: inspect saved
geometry and the selected manufacturing profile. The acquisition qualification
exercised this setting on the same native placement, retaining a complete route
and eliminating its three measured via-center-in-pad sites.

`job-report.json` records `setup`, `import`, `prepare`, `search`, `judge`,
`export` and `final_validation` separately, plus `rule_validation` when custom
rules are present. Queue time (admission) is outside
the process and must be recorded by the caller.

### Coupled pair groups

The existing `router.length_match` operation also accepts complete coupled
pairs, detected from native names and netclasses. Include both members of at
least two pairs, for example `[["A_P", "A_N", "B_P", "B_N"]]`. Use
`tolerance_mm`; coupled native delay-profile matching remains unqualified.
Every named terminal-to-terminal path must satisfy the requested native skew.

The router tunes the group before locking pair copper for subsequent passes,
and preserves each pair's own native width/gap. A failure in one pair discards
the batch output. Existing connected copper sets the target and is preserved.
Single-ended members and coupled members must be in separate groups. If both
`router.intra_pair_match` and a coupled group are requested, their tolerance and
amplitude must agree because the backend shares those controls. Original native
rules, including stricter coupling limits, always remain in force.

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
   **Native routing keepouts:** board-level rule areas that allow footprints
   and pads and have no placement-area restriction are preserved through the
   job. Their routing restrictions are enforced by the router and native DRC.
   Placement-restricting areas and ordinary copper pours, filled or unfilled,
   are refused before solving. Footprint zones and rule areas remain refused.
   Native checks after routing do not make an ignored placement obstacle safe;
   this job has no placement zone engine. Declared `inputs.planes`
   are produced after placement and may be outer or enabled inner copper layers.
   Final verification requires their declared `island_removal` to be `always`.
   Search conservatively uses the largest declared netclass clearance, the
   board minimum and native per-pad overrides. Conditional custom rules are
   evaluated by native KiCad on each saved shortlist candidate and the final
   routed board; the search model does not implement a second rule evaluator.
   A geometrically feasible search result cannot bypass those native checks.
   Fully fixed boards retain their one unchanged placement and receive the
   same native judging and routing, with zero search moves.
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
   schema `agent-ee.placement-job-input/2`; a `/1` record replays only under the
   labelled `legacy_pad_centre/1` contract, see [intent-dialect.md](intent-dialect.md#capture-contract);
   one process for the whole seed
   portfolio). `prepare` validates once: full-UUID identity with deterministic,
   collision-checked aliases (`fp000`... in UUID order, never a UUID prefix),
   roles, occupancy labels, rules (largest net-class clearance, per-pad native
   clearance, copper-to-edge clearance, solder-mask minimum web), groups,
   proximity rules and edge rules.

   **Fixed mark identity (AGENTEE-528):** multiple `fixed_copper_mark` objects
   may share a display `native_ref`; their full UUIDs and solver aliases remain
   distinct. Duplicate references involving a `movable` or `fixed_body`
   component still refuse. Group, proximity and electrical-locality selectors
   require one physical footprint for each native reference; a repeated mark
   label produces an explicit ambiguity error. No new alias or UUID selector
   syntax is introduced. Capture's existing fixed-pose locator `Ref*~2` resolves
   the base label and exact declared pose to a UUID; it does not rename the
   native object. Both marks retain their protected pose and copper/mask
   geometry through native save/reload. Parser evaluation of a retained problem
   does not qualify unsupported public intent or establish placement feasibility.

   `SearchState::evaluate` computes the delta of a
   move (single part, quarter turn, group translation) from a bucketed
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

   Declaring an edge also makes **edge identity a hard constraint**, whether or
   not a numeric maximum setback is supplied. Fixed connectors are checked too;
   a conflicting fixed pose cannot become a feasible placement. A connector
   declared `class: edge_receptacle` or `context.mount_mode: edge_mount` uses its
   side's Fab body shapes, then silk shapes, as its seat. Other declarations
   use the occupancy envelope. The seat and packing reservation remain distinct:
   a courtyard can reserve access space without changing which edge the body
   faces. At corners, a unique overhanging edge takes priority; otherwise the
   longer body span along the touched edges, then setback, determines the edge.
   Ties use west, north, east, south order. The native copper-edge margin defines
   which edges are touched. Mating-face inference is not implemented in this
   path; declared connectors retain their input rotation. This is not complete
   access-direction or KRT floorplan parity.
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
   poses, passing saved-board edge checks and zero placement-introduced errors. A failed trial, crash or worse
   candidate never touches it, and candidate directories are never overwritten.
   Without a valid incumbent the best diagnostic candidate is kept separately in
   `best-diagnostic.json` and may be routed **only as an exploratory run**; the
   report then says `NO valid incumbent`.
7. **Pinned router process** on a fresh copy (`kicad-route run`, the adapter's
   argv shape, copper protection as described above, `--diff-pairs auto`, the checkout's own
   state). The sandboxed `routing_adapter.step` refuses a candidate without a
   schematic, so a schematic-less board is routed by this direct process call;
   the router's preflight identity is recorded either way.
   The input board's stem supplies the routing project name. The placement
   incumbent routes first; on rejection, the job tries the remaining distinct,
   eligible shortlist placements in score order. Failed edge/integrity/native
   placement checks never enter this queue. Each attempt receives an equal
   share of the remaining job time, recorded as an agent scheduling default.
   Every routed result receives fresh final checks. The first passing result
   stops the queue; this establishes a routable choice, not globally optimal
   copper length or via count. `routing_trials` retains failures as well as the
   selected result. `routed-incumbent.json` binds that result to its receipt and
   placement; `incumbent.json` continues to identify the best placement score.
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
   Edge identity is checked again from saved native geometry in a fresh bounded
   process, with board/project/rule bytes checked for changes. A violation makes
   the final board ineligible and the job `intent_failed`, even with clean DRC.
   The report lists every declared connector's UUID, body/occupancy basis,
   observed edge and distances. `coverage: ["edge_identity"]` states this check's
   actual scope; it does not establish all functional-block, pin, side, access,
   height, overhang or setback requirements. The metrics command exits nonzero
   when this check fails.
   Job status `verification_failed` also rejects native errors or opens;
   `placement_failed` rejects an otherwise eligible exploratory route with no
   feasible incumbent. `completed` requires an eligible final board and a valid
   incumbent. It remains a placement/routing milestone, not manufacturing
   acceptance or complete required-check coverage.

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
identity: problem, config, engine and controller hashes, router identity and
native tool versions). `--resume` refuses a checkpoint of
another identity, skips completed seeds and restarts unfinished ones from their
start: it is restart at a seed boundary, not continuation of an interrupted PRNG
state. Diagnostic candidates of earlier seeds are not carried across a resume.
Each attempt writes its own `candidates/aN-*`, `input-drc-aN`, `route-aN`,
`final-aN` and `job-report-aN.json`; later routing trials use `-r2`, `-r3`, etc.
Completed routing-trial receipts are hash-bound in the checkpoint. Resume
retains these receipts but runs fresh routing and final checks; it does not
adopt old copper or reuse old verification as a passing result.
`incumbent.json` carries across attempts
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
bodyless marks, Default clearance and a larger non-default netclass negative
that both the search model and native DRC reject. `test_placement_job_rules`
exercises native rule custody, a four-layer paired routing article, the build's
actual placement-stage producer, rule violations and changed-rule resumption.
The separate external esp_prog
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
capture now refuses it before solving. Copper-zone, placement-restricting
rule-area, footprint-zone and zero-zone controls define the placement boundary.
`test_placement_job_transition` additionally preserves routing-only rule areas
through the public job and actual build producer, while rejecting pad,
footprint and placement-area restrictions. Capture cancellation,
deadline, child failure, missing output and input mutation fail closed.

## Origins

The transaction, spatial-query and seeded-restart semantics follow KRT v0.23.7
mechanisms selected in AGENTEE-514. They were reimplemented after source
inspection (not clean-room); `job.rs` carries the upstream MIT notice and
`docs/file-origins.json` origin `KRT-515` records the attribution.

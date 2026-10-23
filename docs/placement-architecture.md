# Block placement in the complete PCB creation system

Status: proposed architecture; ADC completed and AFE interrupted with retained phase measurements at 23:16Z. Owner requested wall-time analysis and a holistic architecture on 2026-10-23. This document does not authorize implementation or release the preserved P run. Chief531 owns the proposal;530 integrates;532 relays.

Integration record: integrated by agent-530 on 2026-10-23 from chief agent-531's draft (SHA256 `51e126df98cf4cab…`), with host-local artifact paths replaced by evidence identities. It is a proposal: it authorizes no implementation, router refresh or change to the owner-paused P run (AGENTEE-507). Diagnostic evidence is AGENTEE-508; tracking is AGENTEE-485/507/508.

## Problem and evidence

The shipping Rust executable owns the pure planning boundary in `placer_runner`, but that boundary begins after block candidates have been produced. `crates/placement-engine/README.md` states that Stage A remains Python. `block_variants.produce` calls `blocks.pack_block` directly; `blocks.pack_block` calls Python `placer.pack`, including recovery and compaction. `COMPACT_TRIALS=6` applies separately to two compaction families, allowing twelve additional packing attempts per eligible face. These are call-path observations, not measured attribution.

The original producer re-extracts the full source for each block. Each native face alternative loads the full board, transforms members, saves/copies it, extracts a full capture, reloads to verify pad identities, packs, hashes and serializes the capture. The writer later reproduces supplied variants before planning. These checks protect real native semantics, so eliminating them without replacing their guarantees is unacceptable. Their present scheduling and representation amplify work.

Existing 92–270-second side totals and 599.5 seconds for one of 26 blocks are partial file-timestamp observations, not profiles or a complete-run estimate. The historical Rust benchmark reports a 56.45-second mean versus 95.89 seconds for CPython on a different frozen workload. It measures capture, downstream planning, writing and validation; it does not prove current Stage-A speed or an end-to-end speedup after moving Stage A. Preserve that distinction.

There is also operational wall time: the paused P job holds three of four admission units while another test queues; its frozen timeout processes use real time and cannot be cleanly resumed by merely thawing them. Queueing, active computation, validation, publication and deliberate pause must be reported separately.

## Measured wall time: ADC, 2026-10-23

AGENTEE-508 ran the unchanged producer from `8ab3b0b9` with actual run-3 emitted inputs in an admitted container. The completed ADC block took **648.733 s wall / 649.127 s process CPU** under cProfile and phase wrappers. This is an instrumented diagnosis, not a production latency benchmark. AFE was interrupted as described below; do not extrapolate ADC to all 26 blocks.

| Observed phase | Wall seconds | Interpretation |
| --- | ---: | --- |
| Entire ADC production | 648.733 | Includes both F/B candidates and validation |
| Python packing, 16 calls | 629.094 | 97.0% of total, including compaction |
| Compaction, both faces | 626.717 | 96.6% of total; nested in packing, not additive |
| Native F/B preparation plus original extraction | 7.791 | About 1.2%; individual loads/extracts nest inside it |
| Candidate-set validation | 5.970 | Includes subordinate admission/candidate checks |

Compaction tried seven alternatives per face. Each face used 30,916 search steps and reduced the box from 261.215 to 197.626 square millimetres (about 24.3%). Several tighter trials exhausted 10,000 steps or proved infeasible. It did useful work, but most search cost was spent attempting further compression. This motivates an explicit quality budget and best-legal checkpoint; simply deleting compaction changes behavior and may degrade placement.

The cProfile file contains impossible caller edges (`Path.read_text` calling placement functions and `time.sleep` calling `place_unit`) and cumulative times greater than elapsed time. Treat **per-function timing and counts as unreliable**, rather than publishing a mechanical/deepcopy percentage. A sampling-thread interaction is a hypothesis, not an established root cause. The explicit phase wrappers use independent monotonic timers; their packing/compaction attribution remains usable for this instrumented run because the sampler does not invoke those wrapped operations. No uninstrumented matched control exists, so neither profiler overhead nor an achievable speedup is established. Process CPU includes the RSS sampler thread. Sampled process-tree peak was 410,296 KiB through ADC; it is not an instantaneous exact maximum or an AFE measurement.

Source independently explains the avoidable work: Python `mechanical_reservations.grade` calls `materialize`, which invokes `_binding` on every evaluation. `_binding` revalidates and hashes the immutable document and visits all bound native owners; `_native_owner` validates and deep-copies their pad inventories. Rust `mechanical::grade` likewise invokes `binding` for every grade, validating the document, digest, owners and selection. Rust avoids the Python deep copy there by retaining owner references, but still repeats immutable binding validation. A literal language port preserves that architectural repetition. The diagnostic also identifies an interface mismatch: `lib.rs` rejects a block mechanical selection in a global policy, whereas Stage-A isolation creates such selections. Calling the public Rust planner for each inner trial is therefore not a demonstrated drop-in substitution.

The first design priority is **prepare immutable evidence once, evaluate pose-dependent constraints incrementally, and retain an independent full final grade**. This is supported by repeated work in source and the measured parent-phase cost; the exact benefit requires a clean matched measurement. Moving native file I/O out of the search is still sensible system design, but the measured ADC run does not support calling KiCad startup the dominant bottleneck.

Evidence: AGENTEE-508 diagnostic packet `agentee-508-diagnostic` (SHA256SUMS `974ec2e6f1691944bb1e0b4ce9059574767b2d21d2f99892dabf934a54d2f7f1`), retained in the integration host's private evidence store and not in this repository: `RESULT.md`, `METHOD.md`, `PHASES.md`, `SOURCE.md`, `out/{adc-run.json,adc-result-summary.json,afe-run.json,phases.json,adc.pstats,afe.pstats,rss.json,done.json}`, input digests in `inputs.json`, and the observation code `scripts/profile_blocks.py`. Full campaign timing and a matched control remain unavailable. The terminal packet was sealed at 23:17:34Z; chief531 read RESULT.md, METHOD.md, PHASES.md and SOURCE.md and verified its complete SHA256SUMS successfully at 23:18Z.

### AFE terminal observation

The diagnostic container was absent by 23:16:45Z and `done.json` records completion of the diagnostic harness at 23:16:14Z. `afe-run.json` records `KeyboardInterrupt()` after **735.825 s wall / 735.828 s process CPU**. This is an intentionally interrupted, censored observation, not a completed block-production result. Its phase wrappers report **730.082 s packing**, including **729.409 s compaction**, for the first/front face. Seven packing calls were entered, including the interrupted call. No complete AFE result summary was emitted, and no back-face native variant was entered.

Initial extraction plus front-face preparation was 4.559 s. The initial extraction row is labelled `B` because the observation script carries the previous ADC face label into the next block's setup; it does not mean AFE's back face was prepared. This instrumentation limitation does not change the block-level parent-phase conclusion. The raw status string is `failed`; the retained KeyboardInterrupt and bounded diagnostic shutdown explain why the report labels it interrupted rather than a solver failure.

Both measured blocks concentrate active instrumented wall time in repeated packing under compaction. The evidence supports prioritizing search-state preparation/reuse and an explicit compaction quality budget. It does not establish uninstrumented latency, completion time for AFE, total campaign duration, a subfunction cost breakdown, or any speedup from the proposed changes.

## Outcome and ownership

One Rust placement engine should own the hierarchy from internal block layouts through board arrangement and final refinement. Python retains campaign orchestration, adapters, evidence retention and native tool invocation. KiCad remains the authority for native file semantics. The existing Rust router remains a separate solver behind its established adapter; a common process or a shared implementation language is not sufficient reason to merge the two solvers.

The engine's unit of work is a complete placement problem, not a script-generated bag of opaque block files. It consumes authoritative native facts, electrical intent and constraints, and returns a candidate placement with explicit provenance, failures and metrics. The campaign—not the solver—decides acceptance using independently recaptured results and required engineering checks.

## Data flow

Captured source + netlist + intent + native geometry
  -> validated PlacementProblem
  -> Rust block layout alternatives
  -> Rust board arrangement + residual placement
  -> Rust boundary/member refinement
  -> exact placement delta
  -> KiCad apply/save/reload + independent checks
  -> pinned router attempt
  -> candidate-bound routing feedback
  -> bounded local placement repair or explicit failure

The agent selects meaningful engineering constraints, priorities and experiments. It does not orchestrate individual search moves or invent geometry through prose.

## Interfaces and responsibilities

### Native adapter

Capture an immutable board snapshot and companion files once per attempt. Preserve native footprint/pad UUIDs, connectivity, layer information, exact geometry, rule areas, locks, board outline, physical thickness and source identities. Normalize into an explicit, versioned interchange format; retain the raw source and tool fingerprints.

For front/back alternatives, initially retain the current native-qualified generation as a compatibility adapter. Then qualify reusable local footprint transforms against actual KiCad save/reload behavior. Cache only qualified representations keyed by all relevant geometry, transform semantics and KiCad version. Unsupported custom/per-layer pads remain explicit refusals. Do not relabel an in-memory transform as a native recapture under the old candidate-set schema.

Only the selected candidate is applied to a disposable native board for publication. Its complete saved coordinates, pad inventories, fields, side transformations and protected contents are checked. Native DRC and final block contracts remain separate from search acceptance.

### Rust placement engine

Own block membership interpretation, local layouts, face/orientation alternatives, packing, recovery, compaction, global arrangement, residual placement and refinement in one prepared in-memory problem. Reuse the existing exact geometry, locality, mechanical and provenance machinery; do not create another overlapping solver.

Introduce internal types for immutable board facts, footprint geometry, block membership, candidate poses, constraint dependencies, prepared geometry and search state. Keep external JSON at the process boundary; avoid rebuilding or parsing complete board records for every trial. Typed internal state is an implementation proposal, not a claim that current Rust JSON processing is the measured bottleneck.

A block candidate contains member transforms, anchor identity, envelope, interface pads/nets, internal objective values, relevant constraint dependencies and evidence references. Shared board geometry is stored once. The board arrangement sees the geometry and interface costs necessary to judge candidates; a rigid rectangle is not the entire block contract.

Generate bounded alternatives relevant to electrical intent: eligible faces, orientations, internal compactness/routability tradeoffs, and explicitly declared split-face support. Do not exhaustively enumerate arbitrary per-member side assignments. Preserve anchors and protected layouts, allow separately authorized flexible boundary members, and retain the best legal candidate while refining. A smaller block is not automatically a better routed board.

### Constraint and evidence model

Compile hard constraints and advisory objectives once, retaining their provenance and scope. Structural invariants, locks and authorized numeric constraints remain enforced. Engineering estimates stay advisory. Do not introduce interference-fit or worst-case-stack gates, inferred enclosure dimensions, or unsourced distance limits during optimization.

Build an explicit dependency map: changing an internal member invalidates affected geometry, local electrical relationships, interface positions and cross-block/routing costs; a rigid block translation preserves qualified internal relationships but invalidates board-relative and inter-block facts. Whole-board constraints still receive their required final check. Cache invalidation is conservative until its dependency coverage is demonstrated.

Cache computational results separately from acceptance evidence. A cache hit can supply a candidate but cannot skip required final-native checks. Cache keys bind semantic inputs, engine version, geometry qualification and relevant policy; missing or uncertain dependencies cause a miss. Partial output is diagnostic, not an adoptable complete candidate set.

### Prepared problem and move transaction

Proposed internal interfaces below are pseudocode, not existing callable APIs. Use an explicit construction boundary:

- `prepare(native_snapshot, intent, constraints) -> PreparedProblem | Refusal`: validate hashes, schema, identities, complete coverage and native geometry once; compile selected reservations, electrical pairs, immutable local polygons and pad geometry into indexed records. A prepared object is immutable and bound to the complete semantic input identity.
- `evaluate(state, MoveDelta) -> Trial`: compute changed footprint/block poses and the affected constraint closure, query the spatial index for newly relevant neighbours, and return hard violations plus objective deltas. Never reuse the old neighbour list as the complete new search region. No full JSON capture, document hash or native-owner deep copy belongs in this loop.
- `commit(trial)` / `reject(trial)`: update state, spatial indices and cached contributions atomically, or leave them unchanged. Trials carry the state generation they evaluated; stale trials are refused.
- `finish(best_state) -> PlacementSolution`: emit member poses, source/problem identity, remaining violations, metrics and structured reasons. Independent full grading and native publication decide whether it is adoptable.

Make geometry and identity impossible to mutate through a pose-only move. If an operation changes source geometry, net connectivity, side eligibility or policy, construct a new problem rather than smuggling it through a move. Initially recompute any constraint whose dependency coverage is uncertain. Compare incremental results with a full reference evaluator across translations, rotations, face changes, group moves, new-neighbour collisions, rollback and stale-trial cases. This is the critical preservation test for removing repeated validation.

### Placement–routing feedback

Use a versioned contract between existing solvers, with source/placement digest, UUID/pad/net identities, layer stack, actual width/clearance rules, and router/runtime identity. Returned evidence should identify unrouted connections, escape failures, congestion regions, layer bottlenecks and actual measured route costs, with explicit unavailable fields.

Placement may use pin-aware escape and corridor estimates as objectives. Their presence does not prove routability. Do not hard-code the numerical gaps or layer borrowing assumptions in the historical research blueprint as project requirements.

A routed attempt can propose a local repair region, implicated blocks and a measurable objective. Repack only that region plus the dependency closure, keeping accepted unaffected state fixed. Route-derived hints are invalid when source identities or relevant placement change. Every iteration retains the previous best validated board; routing completion cannot mask new electrical, mechanical, placement or fabrication defects. The current `routing_feedback: unavailable` must stay truthful until this contract is implemented and tested.

### Execution lifecycle

Use one admitted job with an overall deadline, explicit child budgets, cancellation ownership and persisted checkpoints. Bound internal worker concurrency by admitted CPU and memory; do not combine independent process pools and engine threads without accounting for both. Deterministic ordering/tie-breaking preserves reproducibility when independent blocks run concurrently.

Record queue wait separately from active stage time. A deliberate pause must store active elapsed time and remaining budget; freezing a process with wall-clock timeout alarms is not a supported resumable checkpoint. The preserved existing P container remains inspection-only until a reconciled owner release. Future engine checkpoints should contain prepared-input identity, completed alternatives, remaining search work, random/deterministic state and a resumable budget ledger, or explicitly state that only restart-from-checkpoint is supported.

## Performance measurement contract

Report end-to-end elapsed time and critical-path stages: queue/admission; source capture; native variant qualification; local packing; compaction/recovery; arrangement/residual/refinement; serialization/hashing; native output and independent verification; routing; publication. Nested timings must not be summed twice. Parallel task CPU seconds are not elapsed seconds.

Per block and face, retain call counts, attempted and accepted moves, compaction trials, rejection categories, candidates examined, cache hits/misses, bytes serialized, native loads/saves/extractions, subprocess launches and peak process-tree RSS. Record source/binary/image/inputs, admission limits, host contention and instrumentation overhead. Use profiler output to select the first optimization; preserve uninstrumented controls on matching inputs when feasible.

## Alternatives and migration

Keeping the current path plus process parallelism is the least invasive alternative, but retains Python Stage A, duplicated orchestration and native round trips. Its benefit and contention must be measured; it does not satisfy the requested coherent ownership on its own.

Routing each Python compaction call through a Rust subprocess could be a diagnostic bridge, but is not the target architecture: it preserves repeated setup, transport and search lifecycle fragmentation. A wholesale rewrite of KiCad I/O, router and campaign orchestration is unnecessary and risks established semantics.

Recommended checkpoints:
1. Preserve baseline and measure representative actual Stage-A work; name missing evidence.
2. Extend the existing Rust engine with Stage-A packing/recovery/compaction over prepared inputs, preserving the existing candidate contract through a narrow adapter. Compare against Python reference on exact candidates/refusals and real pod inputs. No simultaneous search-policy change.
3. Move the complete hierarchy into one request/session. Version the native geometry/variant contract; qualify cached transforms and dependency-aware reuse. Retain old readers for historical evidence; reject ambiguous mixtures.
4. Improve search and block alternatives based on profiles and routed outcomes. Distinguish quality changes from semantic-preserving migration; evaluate legality, quality, runtime and memory together.
5. Add candidate-bound routing feedback and local repair, proving stale-feedback rejection and best-board preservation.

Each checkpoint must be reversible through the previous recorded engine/runtime identity. No silent Python fallback in production. Keep the Python implementation as an explicit differential oracle until its preservation role has a justified replacement.

## Upstream KRT v0.23.7: explicit reuse decision

The owner-relayed release request was checked against the primary [v0.23.7 release](https://github.com/drandyhaas/KiCadRoutingTools/releases/tag/v0.23.7) and selected tagged source (`py_placer/place_simple.py`, `placement/simple.py`, `placement/simple_anneal.py`). The local checkout is older than this tag and has no local v0.23.7 object; conclusions here use files fetched read-only at the tag, not the local README's obsolete flow. Read-only copies fetched at the tag are retained with the chief's review packet on the integration host (not in this repository); the primary upstream links above are authoritative.

KRT supplies functional groups, datasheet templates, multiple seeded starts, and a short routing comparison of shortlisted placements. Its model builds spatial indices and per-part dependency maps; `Model.local` updates affected contributions and `commit`/`restore` manages trial state. It retains `total` as a full reference. Those are directly relevant answers to the repeated-work problem observed here. The release's routing corpus shows improvements and regressions; it is not evidence of pod performance.

Source qualification matters: the annealing loop is Python, with NumPy and `grid_router` Rust kernels for geometry/cost work. It is not an existing all-Rust hierarchical replacement. Groups are flexible collections in a weighted objective, templates bind local support relationships, and overlap penalties can permit intermediate overlap. Declared constraints may also be priced as penalties and graded afterwards; `judge_order` may send the least-defective seed to route when no clean seed exists. That is a diagnostic search policy, not our acceptance policy. Upstream floating-point search representations must not replace our exact hard-constraint authority by assumption.

| Alternative | Benefit | Compatibility work | Recommendation |
| --- | --- | --- | --- |
| Adopt KRT grouped placer behind the existing campaign adapter | Reuses integrated group/template/search/route workflow | Map every required pod constraint and native identity contract; preserve exact final checks and reject unsupported constraints; measure actual pod latency and quality | Serious replacement candidate; not a drop-in production change |
| Port reusable KRT mechanisms into the existing engine | Reuses incremental scoring, group moves, templates and route selection while preserving current contracts | Translate representations and test dependency/invariant parity; retain upstream attribution and license requirements | Preferred architectural direction with current evidence |
| Extend current Stage A without examining upstream | Smallest immediate integration surface | Still must remove repeated validation and build hierarchy/feedback | Reject as the default development plan; duplicates available mechanisms |
| Replace the whole campaign/router/native I/O stack | Superficial consolidation | Large unrelated semantic migration | Not justified by this profile |

The architecture therefore keeps one placement ownership boundary and stable problem/solution contracts, while treating the solver implementation as replaceable. Before substantial new search code, perform the focused compatibility comparison on the actual pod: use an explicit mapping for all required constraints, source identities, side changes, mechanical/RF accesses, electrical relationships and final-coordinate grouping; name unsupported items rather than dropping them. If KRT can satisfy those contracts through a narrow adapter and gives better end-to-end results, adopt it instead of maintaining a duplicate search implementation. Otherwise port the measured-useful mechanisms into the existing Rust engine. The evidence currently favors the latter because our exact constraint machinery exists and upstream equivalence has not been demonstrated; it does not prove adoption is inferior.

This does not require a speculative abstraction framework or two production solvers. Use one explicit experimental adapter for comparison, then select one production path. No automatic backend fallback. The earlier migration checkpoint of a literal Stage-A port is amended: first remove invariant reconstruction through a prepared problem, and make the reuse decision before writing a new hierarchical search. Algorithm changes then receive quality validation separately from representation-preserving work.

A router refresh is a separate bounded change: assign an owner, compare the current fork with v0.23.7, identify missing relevant fixes, and replay the pod plus affected regression fixtures before adoption. No unreviewed bulk merge or production routing run is implied by reading release notes. The placement architecture consumes a versioned router result, so this refresh need not block preparation/model work.

## Acceptance and limits

Architecture acceptance requires a complete problem/solution contract, invariant ownership, observed cost attribution, migration scope and explicit open questions. Implementation acceptance additionally requires exact/admissible behavior comparison, native save/reload verification, resource/cancellation tests, cache mutation controls and matched full-pipeline measurements. Preserve functional grouping, real selected underside use, source-bound physical relationships, mechanical/RF access, and final manufacturing checks. Do not optimize the benchmark by omitting difficult blocks or weakening constraints.

Targets must be end-to-end and paired with output quality. The existing 1200-second C1 threshold belongs to the earlier task; it is not a measured attainable target for this redesign. No speedup factor is claimed before matched measurement. Neither this architecture nor a clean DRC report establishes a manufacturing-ready PCB.

Open before implementation: broader representative phase attribution and a matched control; supported native transform coverage; exact scope of reusable internal geometry; routing feedback fields actually available from the pinned router; cache dependency completeness; and resource costs of concurrent prepared problems. These are explicit engineering questions, not reasons to return to unmeasured parallelism.

## Integration review corrections

Agent530 reviewed the source claims; relay532 distinguished historical engineering rulings from owner authority. The owner requires Rust as default with no silent Python fallback. Legacy-first search order is an existing engineering decision: preserve it during the structural migration, then revisit it explicitly as an algorithm/quality change. The existing `agent-ee.side-changes/1` record remains the native mutation contract until an explicit compatible replacement is qualified. Record runtime identities rather than imposing a new owner pin requirement.

The router-feedback checkpoint needs a named router-side maintainer before implementation; no router owner is currently assigned. This proposal creates no new agent. Critical reviews use Astra or Fable, and implementation/review must use different model classes. Relay532 corrected its earlier provenance statement in 9f695975@1, citing the recorded owner instruction. Thus Opus implementation requires an eligible reviewer from another model class; do not assign Fable to that work. Legacy-first remains a separate engineering choice.

Operational evidence from relay b452197e@1: the writer's checksum test waited 714 seconds for admission. The corrected integration gate (merge b1f4f4d2) exhausted its 840-second window with exit 124 and no unittest summary, after about 115 seconds of admission queueing. Corrected accounting (chief 053c7a78@1; integrator evidence ARGV-AND-COUNT.md): the 19 modules contain 491 tests in loader order; 433 completed with success markers (an earlier count of 439 included six dots belonging to `./kicad/...` assertion paths, and the earlier 474 total was depressed by two setUpClass errors in the invalid first run); tests 434-491 (58) are unfinished, and test 434 was presumably executing at the cap. These are provisional observations, not a gate pass. These observations establish orchestration delay separately from producer CPU cost. Raw evidence must be retained before treating either as a benchmark. A future job contract should distinguish admission deadline, active execution budget and absolute cleanup deadline; it must not reset already-dispatched clocks.

## Review evidence and proposal limits

The draft uses repository observations and the validated Pincite architecture-assessment packet recorded in the chief's review packet (`doctrine-provenance.json`, host-local). Its authority ceiling is recommendation. ADC phase attribution is available above. Material outstanding evidence is broader attribution, instrumentation calibration and matched native behavior/performance verification of any future implementation. Diagnostic coverage is retained in AGENTEE-508 (sealed, accepted as limited/censored evidence); the second is a future implementation acceptance requirement. Therefore this draft makes no measured improvement claim and authorizes no code change. Generic metric/trend, personnel-scoring and whole-repository churn obligations are not material to this bounded ownership proposal: no such argument is used. Co-change data is not being offered as causality. The retained packet lists the remaining detailed obligations; explicit migration, preservation and rollback checks in this document are proposed checks, not completed verification.

Validation surface inspected (not rerun for this design): `test_placement_engine_parity` covers downstream planning/refusal equality, including candidate sets, final contracts and refinement; `test_block_variants` covers source/eligibility/identity mutations and forged native captures; `test_block_contract` covers final-coordinate authority, deliberate cross-face relationships, source provenance and unknown coverage. These are starting points for the migration matrix, not evidence that an unimplemented Stage A port passes.

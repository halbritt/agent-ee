# Telemetry pod delivery plan

Revision 7 — 2026-10-23: routing attempt ROUTE-1 recorded (incomplete, 78 unconnected on 48 nets; validation failed on 61 dangling findings); next action is one targeted routing repair by agent-507 (agent-521 brief 5642fa1f). Revision 6: placement R5 recorded (complete 309/309 placement, unrouted); next action is the first bounded all-net routing attempt (agent-521 brief b9e0f217). Revision 5: R4 recorded (stopped at the admission interface: the existing write/admission path cannot represent a declared side change); next action is the minimum side-transform correction plus R4 board execution in one 60-minute task (agent-521 brief 81bf1861). Revision 4: placement attempt R3 recorded (blocked on front-side fragmentation); next action is one 30-minute task placing the 22 remaining parts on B.Cu under the owner's double-sided permission (agent-521 brief d7978659). Revision 3: R2 recorded; one targeted constructive front-side placement authorized. Revision 2: first placement attempt recorded and one placement with a larger search allowance authorized. Revision 1: owner resumed 2026-10-22 after stopping the campaign.
Decision owner: agent-521. Sole integrator: agent-449. Communications: agent-519.

## Destination and current evidence

Deliver a placed, completely routed telemetry-pod PCB, then qualify that candidate and produce its fabrication package. No fabrication order is authorized.

The current emitted board has 305 electrical footprints plus four mounting holes and no tracks. It is not a placed or qualified candidate. The previous diagnostic reported 499 unconnected items and 21 other DRC findings; these are historical measurements, not a fresh acceptance result.

Input: fome-mc main 881f6fc5b10e81e3a9b99fd542df991742ed68f0, telemetry-pod/telemetry-pod.kicad_pcb, SHA256 2f1c955704ab1fb38217c24cbaf5a45f4219a74a6a2309be710452c54db3b673. Paired telemetry-pod.kicad_pro SHA256 10002b987995765415313fa89da32ba9011160853596f1a78ff8fba24ac9b175. Capture composition and placement-policy evidence live under telemetry-pod/expansion/production-capture/. Confirm input identity and actual constraints before execution. Do not substitute the old 112-footprint pilot or nine-footprint seed.

## Next artifact and causal action

Next artifact: a saved placement of this complete board, with connectivity and sourced constraints preserved, ready for an isolated routing attempt.

Use the existing placement implementation on an isolated copy. The immediate uncertainty is whether the emitted board and its captured policy are usable inputs. Resolve that uncertainty in the same execution that attempts placement; do not create a readiness-review campaign. If a concrete input defect prevents the attempt, repair only that defect within the bounded task, or return the exact failure and smallest necessary correction.

Agent-449 selects one existing ordinary-model writer and one compatible local admitted host through agent-475. Agent-519 carries the assignment and replies. Reuse the qualified runtime where compatible. Allow one existing 1500-second placement budget, plus at most 15 minutes of setup and result checks. If tooling requires a different bound, return the specific reason before starting an extended run. No frontier-model review is needed to attempt placement or commit this plan.

Preserve all 305 electrical footprints and four mounting holes, reference/net mapping, native design rules, sourced mechanical limits, fixed right-angle AMPSEAL J1 1-776163-1, and the accepted four-layer/1.6 mm stackup. Check source provenance when applying numeric policy constraints: agent-selected packing heuristics are not owner/manufacturer requirements. Do not silently weaken sourced constraints to obtain completion.

Return the saved board and project, immutable hashes, run directory, exact command/runtime, elapsed time, placement completion result, connectivity/footprint preservation checks, and native diagnostic delta. A partial or timed-out placement remains partial. An actual failed attempt is useful evidence; a report describing an unexecuted attempt is not the requested result.

## Placement attempt R1 (revision 2 record)

Status: NOT PLACED. Partial stays partial; there is no placed board and nothing is ready for routing.

- Run: one bounded execution on proximal through native admission, qualified runtime image d96f8e00 with agent-ee 7d5b0d4d, using the campaign's own placement adapter declaration (helper agent-ee.placer/1, timeout 1500 s).
- Input: fome-mc 881f6fc5 telemetry-pod.kicad_pcb 2f1c9557… and telemetry-pod.kicad_pro 10002b98…, unchanged; the emission-7d5b0d4d design tree was verified file by file.
- Result (attempt 1c): packing_budget_exhausted after 316.9 s; deterministic step_budget 200000 exhausted with 260 backjumps; peak 283 of 309 placed (9 sourced-fixed plus 274 of 300 movable); first unplaced R19. About 1183 s of the 1500 s bound were unused. The 283-pose plan is diagnostic only. The placer publishes no board on a typed failure.
- Input defects repaired within the bound:
  - agent-ee main refuses the project's constraint-provenance witness, which producer 7d5b0d4d emitted before AGENTEE-348 changed geometry defaults; the attempt used the qualified 7d5b0d4d runtime.
  - The derived request policy fixed C42, D3, L3 and U3 outside the board outline with no source; policy r1 removes only those four from fixed_refs.
- Native check of the input: 309 footprints (305 electrical plus 4 holes), 235 nets, four copper layers at 1.6 mm, 21 DRC findings and 499 unconnected, matching the historical diagnostic.
- Constraints applied without a recorded source: the J1, U1 and U2 poses, the H1–H4 insets, the 90 × 65 mm outline and the antenna keepout. They were applied, not weakened.
- Evidence: fome-mc branch commit 31fe446, telemetry-pod/expansion/placement-r1/ (RESULT.md, SHA256SUMS, input copies, repair policy and diff, run evidence).

## Action R2 (revision 2, executed)

The same ordinary-model worker, on the same qualified 7d5b0d4d runtime and proximal admission, makes ONE placement on the same repaired input with the identical repaired policy (r1) and the identical search order. Only the computational allowance changes: step_budget 1000000 and search_timeout_s 1400, if the existing runtime supports them. The outer placement step stays 1500 s, with at most 10 minutes of setup and checks.

Reason (agent-521 decision update): the only demonstrated terminal bound in R1 was the 200000-step budget at about 630 steps/s (316.9 s). R19 being the first unplaced part does not show that R19 is unusually difficult, and 283 of 309 placed with front free-area utilization 0.858 does not establish geometric infeasibility. Raising the agent-selected step budget to use the already-allowed time is a smaller and better-supported action than the earlier search-order hypothesis, which was withdrawn before any search ran.

Unchanged: geometry, clearances, placement constraints, the fixed sourced set, rules, outline, stackup, electrical locality and all parts. No code, scorer or review changes. The R1 partial evidence and the exact policy delta are preserved.

Return: if placement completes, the saved board and project with hashes and the full native preservation and DRC check. If the outcome is unknown, the partial count and frontier, steps and backjumps, and the terminal reason. No automatic further increase or retry; agent-521 decides the next action from the returned evidence.

## Placement attempt R2 (revision 3 record)

Status: NOT PLACED. There is no saved board and nothing is ready for routing.

- Run: the action above, once, on proximal native admission with the same runtime (image d96f8e00, agent-ee 7d5b0d4d, placement adapter timeout 1500 s) and the same repaired input. Policy r2 is r1 with only step_budget 1000000 and search_timeout_s 1400 added; priority_refs and search order unchanged.
- Result: packing_budget_exhausted ("wall-time search budget exhausted") at 1405.0 s; the 1400 s search_timeout_s was binding, not the step budget. 567014 steps, 595 backjumps; peak 287 of 309 placed (9 sourced-fixed plus 278 of 300 movable); part being placed at termination R37.
- Missing at peak (22): R7, R70–R84, TP1–TP6, a strict subset of R1's 26. The larger budget added only R66–R69. No capacity-infeasibility proof was produced. The 287-pose plan is a diagnostic-only, unvalidated partial.
- Input board and project re-hashed unchanged after the run; native DRC of the input unchanged at 21 findings and 499 unconnected.
- Evidence: fome-mc writer branch commit 1c90cf47, telemetry-pod/expansion/placement-r2/ (SHA256SUMS, request, step, placement-result, checks), with policy r2 and its diff under telemetry-pod/expansion/placement-r1/repair/.

## Action R3 (revision 3, executed)

Observation: a 2.8-fold increase in search effort placed four more parts and left the same 22 small parts unplaced. Further global packing with the unchanged search is not authorized.

Hypothesis (agent-521 prediction): the retained R2 arrangement plus local repair can place the 22 remaining small parts without re-searching all 300 movables.

Action: the same ordinary-model worker, on proximal native admission with the same qualified runtime and source, performs ONE targeted constructive placement within one 30-minute task (setup, geometry work and native checks). Starting from the best R2 partial as a diagnostic arrangement, on an isolated copy, using existing native KiCad and placement editing tools:

- inspect the real remaining free space, with a 2D geometry view if needed, and determine whether the missing parts are blocked by actual outline, fixed, keepout or clearance geometry, or merely by search order;
- place the 22 missing parts, repositioning a small explicit set of movable neighbours only where required to open usable space.

Unchanged: footprint identities, connectivity, layers, outline, holes, the fixed J1/J2/J3/U1/U2 poses, keepouts, rules, clearances, stackup, sourced electrical and mechanical requirements, and the current numeric placement constraints and locality bounds. No new placer or scorer, no research queues, no frontier reviews, no repeats. If existing tooling cannot safely apply a complete explicit placement, report that exact interface limitation and the minimal safe path before writing new tool code.

Validation of the entire completed arrangement, never of partial poses: all 309 references (305 electrical plus 4 holes), reference/net mapping and pad connectivity unchanged, fixed poses, four layers at 1.6 mm, all parts inside the permitted outline and overhang, native clearance, courtyard and keepout checks, and locality constraints. Original project bytes and rules preserved.

Return either:

- the actual saved board and project with hashes, exact commands and runtime, before/after native DRC, parity and unconnected counts with new violations distinguished, placement-check results, the moved references with rationale, and a candidate-bound render. Only such a complete checked artifact proceeds to routing; or
- the specific geometric obstruction (references, positions and bounds, required space, blocking constraints and their source provenance) and one concrete best alternative. A partial diagnostic board or render is marked incomplete and never passed to routing.

R1 and R2 evidence is preserved; only relevant placement artifacts are integrated.

## Placement attempt R3 (revision 4 record)

Status: BLOCKED. There is no saved board and nothing is ready for routing.

- Run: one 30-minute task (05:56Z to about 06:20Z) on proximal native admission, with image d96f8e00 and agent-ee 7d5b0d4d, on an isolated copy of the R2 candidate tree. The existing placer_runner applied the 287 R2 diagnostic poses, then the existing place_candidate (ranked-first-fit-backjump/1, step_budget 200000) searched only the released parts. No new placer, scorer or search.
- Stage 1 (R2 arrangement held, only the 22 searched): packing_search_exhausted, "no candidate for R7 in the finite search; infeasibility unproved", after 2954 steps and 0 backjumps. This is no finite candidate for R7 only. It is not proof that all 22 have no legal pose, nor that the board is infeasible.
- Inspection: all 22 have front-side courtyards only and need about 154 mm². The front had 391.5 mm² free in 69 fragments; the back had about 4976 mm² in one region. That map omits native keepouts and copper reservations.
- Stage 2 (explicit neighbour release): release v1 (28 parts) was refused before the search, because group anchors U20, U21 and U3 cannot be released while their members stay fixed. Release v2 (25 movable neighbours, anchors held) ended in packing_search_exhausted, "every backjump for C80 revisits a part order; infeasibility unproved". The primary search used 166009 steps and 219 backjumps, and the existing copper-aligned fallback used the remaining 33991; no new run was authorized by that internal fallback. Peak: 262 held + 26 of 47 movable = 288 of 309 (corrected from the 286 heartbeat figure first reported).
- Evidence: fome-mc writer branch commit 7b7ed93, telemetry-pod/expansion/placement-r3/ (SHA256SUMS; free-space PNGs marked INCOMPLETE).

## Action R4 (revision 4, executed)

Authority: fome-mc telemetry-pod/REQUIREMENTS.md:65 records the owner decision of 2026-10-19 permitting double-sided component placement ("a double-sided part seems fine"), preserving the ESP32 module antenna keep-out, the F.Cu over In1.Cu GNSS RF path and connector mechanical constraints. The same text is in the retained emission-7d5b0d4d requirements-source and requirements-map specs. The R3 instruction to keep component sides unchanged was a coordinator task restriction, not an owner prohibition. This decision lifts it only for R7, R70–R84 and TP1–TP6.

Observation: the remaining free front area is fragmented, and neither a held search nor a local neighbour repair placed the 22 parts. The back side is largely free.

Hypothesis (agent-521 prediction): using the owner-permitted back side removes the front-space fragmentation bottleneck and lets all 22 remaining parts be placed without repacking the existing 287.

Action: ONE 30-minute task. Same ordinary-model writer, qualified 7d5b0d4d runtime and proximal native admission. Starting from the best retained R2 diagnostic arrangement, on an isolated copy, native pcbnew flip and move operations place those 22 references on B.Cu in verified usable space; the other 287 poses are retained.

- Positions suit their actual net connections and test-point access, not a remote corner.
- Actual keepout and reservation geometry is checked before positions are chosen.
- Courtyard side is not treated as an intrinsic front-only restriction. Any actual side-specific package or test-access limitation is recorded.

Preserved: all 309 footprints, identities, pad numbering, nets and connectivity, project and rules bytes, board outline, holes, fixed connector and module poses and permitted overhang, four copper layers at 1.6 mm, all-layer antenna exclusions, RF and reference-plane restrictions, courtyard, clearance and edge constraints, and current locality bounds. Only the 22 declared side changes and their positions and orientations are authorized. No new placer or scorer, extra agents, global search, review campaign, cloud execution or unrelated queue resumption.

Verification: a legal flip mirrors pad coordinates and layers. It is verified as the explicit declared transform against the original, not by requiring unchanged raw coordinates, and no preservation check is disabled. All 309 final placements are checked under original policy r1 plus the explicit side-change authorization. If existing admission or judge tooling cannot represent this transform, the writer returns the precise interface refusal and the minimum required correction before writing new tool code; no bypass, relabelled partial or silently weakened gate.

Return: the saved complete board and paired project with hashes, the exact scripts and runtime, the 22-reference transform list with pose rationale, and whole-board placement, native DRC and schematic-parity results. Also the unconnected count, the delta from the original baseline with new findings distinguished, full reference/net/pad and fixed-pose preservation evidence, and a candidate-bound render. No claim of release qualification; routing is the next action once the artifact passes its placement checks. Diagnostic partials stay clearly labelled. If it fails: the exact observed obstruction and one concrete alternative, with no automatic retry.

## Placement attempt R4 (revision 5 record)

Status: STOPPED at the admission interface, before any new tool code. Nothing was placed or saved.

- Run: one task (06:23:53Z to about 06:26Z) with one admitted probe container on proximal (image d96f8e00, agent-ee 7d5b0d4d snapshot), on a disposable copy of the input board.
- Observed gap in agent-ee 7d5b0d4d src/agent_ee/placer_runner.py (byte-identical on main at this revision):
  - _apply_plan sets only orientation and position, with no side field. A declared B-side pose for R7 was saved on F.Cu, so the side was silently ignored.
  - The _evaluate_proposal pad-inventory gate requires the inventory to be identical to the input, including each pad's layer set. A legal native Flip changes only that layer set, so every authorized flip is refused with "native saved board changed footprint pad geometry".
  - placement_probe.extract and placement.judge already represent B-side parts.
- No side-specific package limitation was found for the 22. Back-side test-point access was not assessed.
- Evidence: fome-mc writer branch commit c1091ec, telemetry-pod/expansion/placement-r4/ (interface-probe.json, RESULT.md, SHA256SUMS).

## Action R5 (revision 5, executed)

Decision (agent-521, brief 81bf1861): authorize the minimum side-transform write/admission correction that R4 demonstrated, then finish the board in the same bounded task. This resolves R4's stop-before-new-tool-code condition. It does not authorize a new placer or scorer, a general redesign, a broad review or a research queue.

Acceptance for the correction:

- Side changes are explicitly authorized and bound to the original board and the exact reference/UUID set. Here only R7, R70–R84 and TP1–TP6 go to B. A plan cannot authorize itself merely by containing a side field. Unauthorized or fixed-part flips and unsupported transforms are rejected.
- An authorized native pcbnew Flip is applied, then orientation and position. The saved side must agree with the declared transform.
- The expected pad inventory and geometry are computed from a disposable original board/footprint after the declared native transform, and the saved board is compared to that expectation. Every unflipped part keeps the exact original comparison. Pad UUIDs, numbers, nets and shapes, footprint identity, rule-area behaviour and custom copper geometry are preserved. Unsupported shapes fail explicitly instead of skipping checks.
- The original board remains the DRC, connectivity, identity and constraint baseline; it is not pre-flipped to silence the refusal. All existing native and placement checks and all R4 constraints are kept. Behaviour without side changes is unchanged.
- Focused native tests:
  - a legal declared flip succeeds and survives save and reload;
  - an undeclared or wrong-side flip is refused;
  - pad, net or shape corruption on a declared flip is refused;
  - unflipped and ordinary placement remains protected.
  Required affected checks run; there is no unrelated broad campaign.

Execution: one existing ordinary-model writer (the R4 writer), local native admission, no extra agents, total bound 60 minutes for the fix, the focused checks and the R4 board execution. The runtime is the qualified 7d5b0d4d runtime with the traceable targeted correction. The exact patch, commit and module hashes are recorded and distinguished from the unmodified image; the patched runtime is not labelled unchanged. Agent-449 integrates the same correction into agent-ee main with the required checks, preserving input/provenance compatibility without reopening the AGENTEE-348 prep barrier. If applying the correction exposes another concrete incompatibility, that exact evidence is returned and scope does not expand.

Deliverable: the R4 artifact, namely 287 retained R2 poses plus 22 explicit B-side poses suited to their nets and test access; whole-board 309-reference native placement and preservation checks; the full DRC, parity and unconnected delta against the original; the board and paired project with hashes; and a candidate-bound render. A code commit or passing fixture without a placed board does not complete the task. An invalid or partial placement is not routed. No automatic retries.

## Placement R5 (revision 6 record)

Status: COMPLETE PLACEMENT, UNROUTED, NOT RELEASED. This is the first complete placement and the input to routing.

- Run: one task (06:29:33Z to about 06:45Z of the 60-minute bound) on proximal native admission with the runtime "7d5b0d4d + R5 side-transform patch": image d96f8e00 and the 7d5b0d4d snapshot with only agent_ee/placer_runner.py overlaid (unmodified 76308585…, patched b4be6263…).
- Board: telemetry-pod.kicad_pcb SHA256 8f3bc234305516b91b6e519d3e0a3cd05b69b6dd16006a317c39b6d810ace5fd, with the original project 10002b98… and rules ff622083…. FOME writer commit 9d5aefdd, telemetry-pod/expansion/placement-r5/.
- Poses: all 287 R2 poses retained. R7, R70–R84 and TP1–TP6 moved to B.Cu under the bound agent-ee.side-changes/1 authorization, each at the first clear back-side pose near its on-board peer pads.
- Whole-board admission under policy r1 plus the authorization: status placed. That covers the declared-transform pad inventory, saved sides, no courtyard overlaps, the judge, groups, locality, netlist binding and the reserved area. The 9 sourced-fixed poses are unchanged. 309 footprints, 235 nets, four copper layers at 1.6 mm, stackup and setup byte-equal.
- Native DRC went from 21 to 80. The 4 courtyard overlaps were resolved; 17 findings are inherited; 63 are new silkscreen findings (silk_overlap 35, silk_over_copper 27, silk_edge_clearance 1). These remain open for pre-fabrication correction; severities are unchanged and nothing is suppressed. Schematic parity 0. Unrouted: 0 tracks, 0 vias, 499 unconnected.
- agent-ee correction: writer commit 02b6510d (rebased by agent-449 onto main as 0e78994e, module b4be6263) adds explicitly authorized footprint side changes to placer_runner. The 139 focused native tests passed (new test_placer_side_transform, plus test_placer_native, test_placer_gate_hardening, test_placer_pad_keepout and test_placement_defects).

## Action ROUTE-1 (revision 6, executed)

Observation: the board is completely placed and unrouted.

Hypothesis (agent-521 prediction): an actual builtin all-net route on the complete placement produces copper and reveals the specific remaining connectivity and geometry blockers.

Action: ONE isolated all-net routing attempt of the R5 board, with a 1500-second backend budget and a 40-minute total task including setup and checks.

- Executor: assigned by agent-449, namely the existing pod-capture-j6b2 writer, started 06:52:13Z. agent-507 was held to communications through agent-519.
- Runtime: the qualified kicad-route runtime b5774496d8466b67ffa0362b6665fee2108155a3, with the actual version recorded, under proximal native admission.
- Command form: kicad-route run --backend builtin --dir ISOLATED_POD_PROJECT --board telemetry-pod.kicad_pcb --budget 1500 --out RUN_ROOT.
- Inputs: the R5 PCB, project and rules, paired with the original schematic, library and constraint context from the verified emission tree.
- Scope: all nets, with telemetry-pod explicitly selected and never the separate fome-mc board.
- Preserved: rules, sourced RF and layer restrictions, stackup, fixed placement, pad/net identities and input copper. Existing qualified runtime-specific settings are documented, with no other-board tuning. The 63 silkscreen findings are neither suppressed nor re-graded.
- No automatic retry, optimization, research, review or cloud. A demonstrated input or runtime refusal returns the exact blocker and the smallest remedy.

Return: the saved candidate with hashes and the complete structured result.json, reporting process, routing scope/completeness and native validation separately. Also routed and unconnected counts and the remaining nets; track and via counts; the before/after native DRC delta including inherited findings, schematic parity, and refill, preservation and rule checks; the exact command and runtime, elapsed time, logs and candidate-bound renders. An incomplete or invalid result stays labelled so. A timeout keeps inspectable partial evidence, and process exit alone is not success.

Decision-changing observation: agent-521 chooses the next repair from the routed evidence (remaining nets and their geometric or constraint causes, or a runtime refusal).

Integration does not delay routing. Agent-449 integrates the focused agent-ee side-transform correction and the FOME placement artifacts through the sole-integrator path, without repeated broad suites. Landed branches and worktrees are cleaned up only when no executing worker depends on them. The R5 renders are published to the persistent gallery labelled COMPLETE PLACEMENT / UNROUTED / NOT RELEASED. Candidate-bound render publication resumes about every four hours during active board work, and sooner for a new routed candidate.

## Routing attempt ROUTE-1 (revision 7 record)

Status: ROUTED CANDIDATE SAVED, ROUTING INCOMPLETE, VALIDATION FAILED, NOT RELEASED. It is kept as evidence; it is not a finished routed board.

- Run: the pod-capture-j6b2 writer, kicad-route b5774496 (clean) with builtin backend 0.23.1 (generation 84b30dc286f44df6b465ae8d40aa1363), KiCad 10.0.7, on proximal native admission. The route ran 06:53:08Z to 07:00:16Z with backend 406.4 s, so the 1500 s budget was not exhausted. The isolated project was the emission-7d5b0d4d design tree with only the PCB replaced by R5. All 235 nets were in scope, with default settings and no other-board tuning.
- Process: the backend passed. kicad-route exit 3 (routing incomplete, validation failed).
- Routing: unconnected went from 499 to 78, with 48 nets incomplete; /GND has 29 open connections. 5889 tracks and 686 vias (7523.4 mm) on 181 nets. No net connected in the input was lost.
- Native validation: FAIL solely on 61 new warnings (via_dangling 34, track_dangling 27). DRC went from 80 to 141, with the inherited 17 and the R5 silkscreen 63 unchanged. Schematic parity 0. Refill did not modify the board. All 16 preservation checks PASS, including keepouts.
- Blocked-by heuristic: the sourced D-PCB-7v2 In1.Cu no-tracks keepout lies across 51 of the 78 open connections on their best layer. The heuristic often chooses the forbidden In1.Cu, so this is not permission to remove the keepout.
- Candidate: telemetry-pod.kicad_pcb SHA256 19ed7954ab305b39f29d19f08fb619b4399cb6522869fba21b7712a52a310bdf, with project, rules and schematic as original bytes. fome-mc main 0fc9b543, telemetry-pod/expansion/route-1/.
- Integration: fome-mc main 0fc9b543 carries only placement-r1 to r5 and route-1. agent-ee main 2fb2eaf8 carries the side-transform correction.
- Renders: gallery bundles 8f3bc2343055-c429d9d298cd (R5, Candidate 5) and 19ed7954ab30-395dc29f7f68 (ROUTE-1, Diagnostic 8, incomplete). The four-hour render timer is re-enabled and pinned to ROUTE-1.

## Next action (revision 7)

Observation: the first route connected most of the board but left 78 connections on 48 nets and 61 dangling generated objects.

Hypothesis (agent-521 prediction): removing genuinely useless generated stubs and allowing bounded repair of generated blocking copper will reduce the remaining connections while removing the dangling DRC defects. This is a hypothesis to check, not proof that the board is routable. A boxed_in_static summary on many nets is not by itself proof of placement infeasibility.

Action: ONE targeted repair task by agent-507 (router specialist), assigned by agent-449. One 40-minute task, at most 1200 seconds of routing backend, local admitted capacity, isolated copies. No new agents and no parallel routing.

- First inspect the final summary and regrade (failed_single and open_single, pad deficits, blockers, boxed_in), the new-finding UUIDs, and the exact pad-escape geometry of representative blocked nets. Separate generated-copper congestion from immutable placement or keepout obstruction using existing native and reachability diagnostics.
- Then execute ONE best supported targeted repair using existing wrapper and tool capabilities: the existing bounded connection repair (previously K93) if it fits and is available, without an integration project to wire it. No new routing code or pipeline refactor.
- Scope: ROUTE-1 generated unlocked copper may be removed or rerouted where required. No locked or human copper deletion, footprint movement, or net, pad, stackup, rule or keepout change. Netclass geometry and severities are not lowered. A continuation may use --protect locked with the reason recorded, routing only failed nets plus affected poured or blocker nets as supported. Dangling generated objects are cleaned only when native connectivity proves no useful connection is lost; no blanket deletion.
- If pad escapes are blocked by immutable geometry even before generated copper, return the exact references and pads and the smallest placement correction instead of an unchanged route.

Acceptance: the saved repair candidate and hash; the exact changed settings, objects and nets; the command, runtime and time; full native DRC, parity, refill and preservation; and all-board unconnected and net-completion comparisons against both ROUTE-1 and the original R5, graded with wrapper validate --against the original R5 and the first run. The 61 dangling findings do not become an accepted baseline, and no previously completed net may regress unnoticed. Both candidates are preserved. No automatic further attempt. Return the artifact, or the concrete immutable blocker and one smallest next action. Evidence improvement does not imply release acceptance.

Decision-changing observation: agent-521 chooses the next step from the repair evidence, whether a repaired candidate with fewer open connections and no dangling defects, or an immutable blocker with its smallest correction.

## Decision after that result

Agent-521 compares the observed result with the prediction. If placement is usable, assign one bounded isolated routing attempt with the existing qualified kicad-route runtime. If it fails, choose the smallest action justified by the observed blocker. Do not repeat an unchanged attempt, open a general optimization project, or treat every review finding as a prerequisite.

For each next action, update this document with the current artifact, missing result, demonstrated dependency, predicted improvement, assigned owner and finite budget, and the observation that would change the plan. Completion of tickets, reviews, merges or infrastructure is not evidence of board progress without an artifact or a removed demonstrated blocker.

Independent electrical, mechanical, RF and fabrication qualification remains required before release. Apply it to the actual candidate at the stage when its result can change that candidate or a release decision. A failure that invalidates placement or routing inputs must be handled when demonstrated; unrelated release preparation does not delay the first physical attempt.

## Scope of resumed work

Only this direct placement/routing path resumes. Old review/correction queues, research experiments, cloud benchmarks and broad preparation campaigns remain held. The Rust scorer effort is owner-directed. AppArmor work remains cancelled; no profile changes or profile tests. Use existing application/runtime access and report a concrete inability to execute if encountered.

GCP total authorization remains at most $100 with a $90 operational hold; this task uses a local host. Do not restart cloud instances. Preserve stopped partial work. Restore useful render publication as candidate artifacts change, then approximately every four hours during active work; label incomplete boards accurately.

All campaign communications go through agent-519. It consolidates routine acknowledgments and sends agent-521 only a needed decision, a material exception, or the next artifact result. Agent-521 retains planning responsibility. Native inbox ownership remains per session; no agent consumes another agent's inbox.

This committed file is the source of truth. Cairn contains its path and current revision, not a competing plan. Assignments name the revision. Agent-449 commits updates through the established integration path.

# Placement and routing delivery plan (AGENTEE-512)

**Superseded execution sequence, 2026-10-25:** use the
[audit recovery plan](planning/2026-10-25-recovery/recovery-plan.md) and
[updated Plane backlog](planning/2026-10-25-recovery/backlog.md).
Implementation and board campaigns remain paused. The dispatch assignments and
statuses below are historical; preserve their evidence, but do not restart them.

This file is the repository copy of the chief's recorded plan. Source:
`~/.local/share/agent-ee/plans/2026-10-24-placement-routing/PLAN.md` (sha256
`879d9e1512b14c60…`, chief agent-531, Cairn 8c45262a@1), written at the owner's
request (2026-10-24, verbatim via the chief): "make a plan, record it, share it
with the others, plane work items and all that, make it happen". Plane items
AGENTEE-512 (programme) and AGENTEE-513..518. The chief decides; agent-530
dispatches and integrates; all traffic goes through relay agent-532. The two
programme outcomes in [telemetry-pod-delivery-plan.md](telemetry-pod-delivery-plan.md)
remain required. Dependent items are backlog until their dependencies are
accepted (the dispatcher enforces the dependencies).

Status 2026-10-24 01:05Z:
- AGENTEE-513 is CLOSED AS-BASELINE ONLY: the stock board FAILED. Evidence `agentee-513-esp-prog-baseline`; Argon HIGH review `agentee-513-review-argon`, where the integrator's correction addendum governs the review's false statements.
- AGENTEE-514 is ACCEPTED with binding amendments (chief decision b4a5857a; see placement-architecture.md).
- AGENTEE-515 is DISPATCHED per chief 4f939a5e: one Opus HIGH builder (claude-harm), 180 min total, from 01:04:28.9Z, branch wt/agentee-515 from 9af52661, then one Astra MEDIUM critical review. The 513 result is a historical reference, not a matched experiment.
- 516-518 are backlog.
Earlier status, 00:14Z: 513 and 514 dispatched.

Status 2026-10-24 03:10Z (integration checkpoint):
- The 515 workflow (17dba2de) and the 515r H1-H4 repair (126dd1a0) LANDED on main as reviewed INPUT ONLY (merge b3a1a29d).
  - Reviews: Astra MEDIUM HOLD on 17dba2de; Argon HIGH LAND-WITH-FOLLOW-UP on 126dd1a0.
  - Immutable gate: 114 tests OK with 0 skips, cargo 88 passed, check_docs and check_origins clean. Evidence `agentee-515r-gate`.
- AGENTEE-515 is NOT complete. The esp_prog board is FAILED: no valid placement under the engine's boxed-copper model. The matched stock-KRT qualification is outstanding.
- Owner (relayed by the chief): "stick to the plan and achieve success on each step before proceeding". 516-518 are held until 515 succeeds.
- The next 515 slice (native-shape clearance agreement, then matched qualification) is drafted and awaits the chief.

Status 2026-10-24 03:50Z: TOOLING ONLY (owner, relayed via the chief 69d407ee@2). 517 and 518 are HELD by the owner, 516 is held on 515, and no pod attempts are made.
- 515q (native-shape copper clearance, steps 0-2; head c17e01ef) is HOLD after its Argon critical review.
  - Step 0 (native USB1/fiducial under frozen rules) passed.
  - Step 2(a) is red: USB1 occupancy is 75 µm past the west edge, from fab stroke inflation plus the unmodelled declared overhang.
  - An NPTH hole-clearance regression must be fixed.
  - Positive native-clean seats are still to be added.
- The next correction slice awaits the chief.

Status 2026-10-24 05:26Z (b98f9bcb pushed to main at 05:26:02Z; this status written at 05:26:57Z): the 515q / 515q2 / 515q3 correctness stack (head 39743415) LANDED on main b98f9bcb as reviewed INPUT ONLY (chief 47db10f7).
- What it adds:
  - native-shape copper clearance;
  - the declared edge-connector overhang, honoured only for the declared component and edge;
  - native drill and hole reservations;
  - fresh-native rule capture in a bounded child process;
  - explicit refusal of board-level zones, custom rules and non-default netclasses.
- Gate: 148 tests across two runs (145 passed plus 3 harness errors from a reused evidence directory; those 3 then passed in a targeted rerun with a fresh directory); cargo 92; docs and origins clean; immutable.
- Limits: the native agreement covers the {USB1, Fid1} subproblem only, not whole-board legality; derived occupancy is not body proof.
- AGENTEE-515 remains INCOMPLETE: the end-to-end esp_prog run and the matched stock-KRT comparison are outstanding, and the U2 fixture qualification awaits the chief's next decision. The architecture proposal is [placement-architecture.md](placement-architecture.md).

Status 2026-10-24 06:21Z: the U2 v2 fixture (derived board b4aa540d) is PARTLY qualified (chief 0bd42912, a delegated engineering disposition).
- Criterion 4 is corrected to the as-built control reference 6ae64855 under the unchanged project 9a9ab694. There, the U2 pad-2 clearance is removed, there are zero new findings, zero mask bridges before and after, and the 357 unrelated findings are preserved. It is accepted on the existing sealed DRC delta. This corrects an erroneous expected baseline. It is not a defect waiver. The original builder HOLD (exit 24) stays on record.
- Criteria 1, 2, 3 and 5 are accepted.
- The 513 final placement stays FAILED: 353 -> 352 findings, with a U2 mask / Y1 pad 3 bridge.
- Qualification stays OPEN only for criterion 6, the one bounded capture-compatibility check. The reviewer ran capture without authority, recorded as diagnostic evidence only: no refusal, 21 of 21 footprints. Its full output was not retained, so all-77-pad semantics are unproven. The chief decides between reuse and one minimal check.
- No full workflow, stock comparison, 516 or pod release.

Status 2026-10-24 06:23Z: the U2 v2 fixture is QUALIFIED as INPUT ONLY (chief fa885bf6, a delegated engineering disposition).
- Fixture: esp_prog-run29_pile-u2tab-v2 = PCB b4aa540d, project 9a9ab694, and intent/groups unchanged and hash-bound in the sealed qualification.
- Criterion 6 is accepted as originally scoped: one bounded, non-refusing capture-compatibility check on the sealed v2 with the accepted toolchain.
- On record: the reviewer's unauthorised capture (diagnostic only), the failed first probe, the full capture output that was not retained, and the unproven all-77-pad semantics. The builder HOLD, the v1 fixture and the old failed reports are preserved.
- Not claimed: no broad correctness claim, no manufacturing readiness, no 515 completion. The historical 513 final-placement U2/Y1 mask bridge stays FAILED.
- Next 515 step: 530 prepares ONE bounded brief for an end-to-end esp_prog run plus a matched stock-KRT 0.23.7 qualification on this fixture, for the chief. Solver launch is not yet authorized. 516 is held on 515. 517/518 and the pod are owner-held.

Status 2026-10-24 06:59Z: the matched end-to-end qualification RAN once on the qualified fixture. It used the hash-bound package agentee-515-e2e-brief/v2 (manifest b12f656d), authorized by chief 46502839. Builder: Opus HIGH, 06:41:55-06:50:25Z. Evidence: agentee-515-e2e, seal 5a19b9da.
- agent-ee arm: REFUSED after 1.6 s, before any seed completed. The engine's `simple_polygon` accepts 3..512 vertices, and U2's captured custom-pad copper contour has 1118. This is an engine input limit; per the chief it is NOT fixture-defect evidence. The job wrote no job-report on this JobRefusal, which is a reporting gap. There is no incumbent and no route.
- stock KRT v0.23.7 arm: completed in 264 s; the judge selected seed 8. The final board is NOT clean:
  - 1 solder_mask_bridge, C2 pad 1 / Y1 pad 3. The signature is already on the piled input; the shared rule labels it placement-introduced because it spans two footprints.
  - 1 unconnected net, /DCOM.
  - 302 warnings.
  - USB1 sits nearest south, though declared west, and the floorplan check failed.
  - 250.48 mm of route, 30 vias, 3 via-in-pad.
- Astra MEDIUM review: RESULT-SOUND-WITH-CORRECTIONS (one wrong seal-command path; the classification-label qualification; the missing-report and retention gaps). Hashes audited clean.
- No matched outcome comparison is possible, because agent-ee produced no board. AGENTEE-515 remains INCOMPLETE pending the chief's disposition.

Status 2026-10-24 07:06Z (chief 3d8d94bd / f92aa95f):
- The matched e2e run is recorded as a FAILED qualification with sound corrected evidence, not 515 completion. Corrections are in the separately sealed agentee-515-e2e-corrections; the experiment seal is unchanged.
- AGENTEE-515c LAUNCHED: one focused repair of the two demonstrated tooling defects, built by Sol HIGH, 07:06:19Z to 09:36:19Z hard; issued brief dc74c67a.
  1. A general, measured complexity contract for native captured copper contours: no geometry loss, no weakened checks. U2's 1118-vertex contour exceeds today's 512 limit.
  2. An honest 'refused' job report after ownership, with custody. This includes the foreign-resume overwrite of capture/problem found in source.
- Then one focused gate (30 min) and one Argon HIGH review, before the chief's disposition. 516 is held; 517/518 are owner-held; tooling only.

Owner requested 2026-10-24 UTC. Both programme outcomes remain required.

## AGENTEE-512 — Qualify a coherent placement-routing workflow before full-pod production

Deliver BOTH the manufacturing-ready telemetry pod and reusable agent-ee. Stop using the whole pod as the first debugger for incomplete software. One prepared board model, placement and routing solvers, one controller, independent native output validation. Mine tagged KRT0.23.7 before choosing reuse/port. Rust remains production default; KRT is a benchmark/reference without silent fallback. J2 repair continues independently; new solver implementation held until architecture decision.

### Acceptance

- Complete esp_prog workflow demonstrated on fixed inputs, then representative pod cases, then complete pod on a qualified fixed version.
- No claim of superiority without matched quality/time measurements against stock KRT0.23.7.
- Existing 485/511 work preserved as evidence, not silently discarded or accepted.

Blocked by: None; ready for scoped dispatch

Type: AFK. Chief531 decides;530 dispatches/integrates;all traffic532. Dependent items are backlog, not launch authority. Native dependency edges unavailable in installed API; dispatcher enforces these explicit dependencies.

## AGENTEE-513 — Freeze esp_prog baseline and record stock KRT placement-to-route result

Use KRT tag83646c8b. First candidate:21-footprint two-layer esp_prog. Unplaced fixture tests/fixtures/959/run29_pile PCB+project; routing-only reference run25/esp_prog_placed PCB+project. Chief verified both against tagged Git blob IDs. Reconcile one honest functional intent and board/project rules, retaining originals; do not mix historical project settings or contradictory connector declarations. Run stock tagged KRT from a clean isolated environment after input preparation. This is the first executable baseline, not a pod acceptance substitute.

### Acceptance

- Freeze source/board/project/rules/intent/tool hashes; check native readability, outline, pads/nets and locks.
- Record one complete stock placement+route command, seeded settings, CPU/memory/admission, wall/CPU per phase, final native DRC and terminal connectivity. Preserve failure if unsuccessful.
- Separate routing-only placed reference from end-to-end unplaced baseline. No invented rules or omitted hard constraints.

Blocked by: None; ready for scoped dispatch

Type: AFK. Chief531 decides;530 dispatches/integrates;all traffic532. Dependent items are backlog, not launch authority. Native dependency edges unavailable in installed API; dispatcher enforces these explicit dependencies.

## AGENTEE-514 — Mine KRT mechanisms and decide one placement-routing architecture

Inspect tagged source and report reusable group/template representation, incremental spatial scoring, transactional commit/restore, seed diversity, route-based judging, repair and relevant router fixes. Compare direct adaptation with porting mechanisms into existing Rust engine. Produce explicit board/intent, move/state, solver-feedback and job/checkpoint interfaces; identify old orchestration to delete. A shared semantic model need not imply a shared process or rewrite of KiCad/router. Decision is chief-owned; implementation is not authorized by a concept diagram.

### Acceptance

- A mechanism-by-mechanism reuse/port/reject table with tagged source locators, licence obligations, compatibility gaps and rationale.
- Concrete first vertical implementation and complete-board benchmark protocol; preserve Rust default and real constraints without administrative gates.
- Choose one production direction based on source fit and baseline evidence when available; explicitly mark performance hypotheses. No blanket superiority claim.

Blocked by: None; ready for scoped dispatch

Type: AFK. Chief531 decides;530 dispatches/integrates;all traffic532. Dependent items are backlog, not launch authority. Native dependency edges unavailable in installed API; dispatcher enforces these explicit dependencies.

Decision (2026-10-24): chief 531 accepted the 514 recommendation WITH binding amendments C1-C6 (Cairn `b4a5857a@1`); see docs/placement-architecture.md, "Chief decision, 2026-10-24". AGENTEE-515 dispatch waits for the AGENTEE-513 review and a concrete, finite implementation brief.

## AGENTEE-515 — Deliver one complete esp_prog workflow through the chosen architecture

Implement the chosen narrow vertical path: native input+intent -> prepared problem -> placement -> routing -> saved/reloaded validated board. Reuse or port KRT mechanisms as decided. Prepared binding optimization from511 can be reused only if it fits. No independent file-script pipeline for each search move. Keep a usable incumbent and bounded cancellation/checkpoint handling.

### Acceptance

- Run the exact frozen esp_prog input end to end through one supported entry point.
- Matched comparison with stock KRT using equal rules/resources/time and stated seeds; report completion, native violations, grouping, elapsed time, measured route length/vias.
- Meaningful invalidation/rollback and native-transform tests; failed trials cannot overwrite best validated board. Cross-class review and immutable integration checks.

Blocked by: AGENTEE-513, AGENTEE-514

Type: AFK. Chief531 decides;530 dispatches/integrates;all traffic532. Dependent items are backlog, not launch authority. Native dependency edges unavailable in installed API; dispatcher enforces these explicit dependencies.

## AGENTEE-516 — Demonstrate routing-guided placement repair on a small complete board

Use an explicit challenging variant of the same baseline to demonstrate routing feedback changes placement and improves the routed result. Bind feedback to source/poses/rules/tools. Use existing measured diagnostics first; absence is unavailable, not zero. Reuse unaffected state only where invalidation is understood.

### Acceptance

- Before/after complete saved boards and actual routing evidence show repair effect within one fixed overall budget.
- Preserve geometry, electrical intent, locks and valid incumbent; reject stale feedback.
- Compare against stock KRT on identical variant and report regressions; no synthetic score-only success.

Blocked by: AGENTEE-515

Type: AFK. Chief531 decides;530 dispatches/integrates;all traffic532. Dependent items are backlog, not launch authority. Native dependency edges unavailable in installed API; dispatcher enforces these explicit dependencies.

## AGENTEE-517 — Qualify pod-required capabilities on representative complete test cases

Cover the actual pod feature set using a small case matrix: ADC/AFE functional groups, power support/template and sense relationships, front/back transforms, RF/access/custom pads, connector geometry. Use the same production interfaces. Add only cases necessary to cover distinct required behavior; no sprawling generic fixture project.

### Acceptance

- Each required pod capability maps to a runnable case and final native/coordinate evidence.
- Named missing capabilities implemented and reviewed; no silent fallback or weakened constraints.
- Freeze a qualified software/runtime version and reproducible commands for full pod.

Blocked by: AGENTEE-515, AGENTEE-516

Type: AFK. Chief531 decides;530 dispatches/integrates;all traffic532. Dependent items are backlog, not launch authority. Native dependency edges unavailable in installed API; dispatcher enforces these explicit dependencies.

## AGENTEE-518 — Produce and verify the complete pod with qualified placement-routing software

Run the complete pod using the qualified fixed software release and accepted board inputs including J2 and selected power design. Integrate placement, routing, independent electrical/mechanical/native review and manufacturing files. Reproduce failures in representative cases before changing the qualified version. This remains the final programme deliverable.

### Acceptance

- Final component coordinates demonstrate functional grouping and intended side usage; routing alone is insufficient.
- Native electrical/DRC, assembly/BOM/CPL, access/RF, fabrication output and independent review close actual requirements.
- Reproducible retained end-to-end run demonstrates reusable capability; unresolved physical validation stays explicit and no manufacturing-readiness claim until required evidence exists.

Blocked by: AGENTEE-517

Type: AFK. Chief531 decides;530 dispatches/integrates;all traffic532. Dependent items are backlog, not launch authority. Native dependency edges unavailable in installed API; dispatcher enforces these explicit dependencies.

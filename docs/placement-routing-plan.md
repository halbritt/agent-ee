# Placement and routing delivery plan (AGENTEE-512)

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

Status 2026-10-24 00:14Z: AGENTEE-513 (stock KRT 0.23.7 esp_prog baseline) and
AGENTEE-514 (KRT mining and architecture recommendation) are dispatched; 515-518
are backlog. The architecture proposal is [placement-architecture.md](placement-architecture.md).

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

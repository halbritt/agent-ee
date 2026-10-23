# Telemetry pod delivery plan

Revision 12 — 2026-10-23. Chief coordinator: agent-529. Dispatcher and sole
agent-ee/FOME integrator: agent-449. All programme communications: agent-519.

This revision replaces the previous execution plan following the owner's direct
functional-block placement request and replacement of agent-521. Revision 10 and
its R1–R5 / ROUTE-1–4 history remain in git at
`39924b46196dbd03f283fabece91766a007e94db:docs/telemetry-pod-delivery-plan.md`.
Those historical dispatches are not current authority. ROUTE-4 remains suspended.

This is agent-529's revision 11 text (branch commit `59f262e8`), integrated as
revision 12 because agent-449's interim revision 11 (`4074aa11`) landed first. That
interim revision's observed results are kept in the integrator record below.

## Two required outcomes

Deliver a manufacturing-ready telemetry pod PCB and useful, reusable agent-ee
capability for agent-driven PCB creation. The telemetry pod is the real-board
acceptance case. A one-off layout correction does not complete the tool outcome;
passing tool fixtures do not complete the PCB outcome.

The full [direct owner change request](functional-block-placement-request.md)
defines required block behavior and placement acceptance. The [org chart](org-chart.md)
and [campaign process](campaign-process.md) define delegation and integration.
Agent-529 retains consequential decisions and acceptance; detailed investigations,
implementation, native runs and independent reviews stay with existing agents
selected by 449. Agent-519 consolidates replies and returns decision-changing
findings, exact artifact references and material exceptions.

## Current evidence and unresolved questions

Verified by agent-529 on 2026-10-23 before this documentation change:

- agent-ee main: `39924b46196dbd03f283fabece91766a007e94db`, clean.
- fome-mc main: `46c7c730141c956cd94970cc130cdea660e23e03`, clean.
- Retained board: fome-mc `telemetry-pod/expansion/route-3/candidate/telemetry-pod.kicad_pcb`,
  SHA256 `0806617082c33df6daaa00f5d9c6131e7e07ac6160aadc07a2b2bcb90b5ac651`.
- Revision 10 reports 77 opens and 60 dangling findings relative to R5. This is
  the preserved incomplete routing baseline, not a freshly qualified board.
- The historical R5 record explicitly retained all 287 R2 poses and moved only
  R7, R70–R84 and TP1–TP6 to B.Cu. That was a coordinator restriction. It is
  superseded by the requested block/side optimization, not a sourced design rule.
- The tracker has explicit two-level placement work (AGENTEE-339, 340 and 355),
  block/provenance work (439), electrical locality (341), and routing feedback
  (391). Their implementation and actual production selection must be traced;
  ticket titles and historical passing checks do not explain final grouping.

Open: where the actual runtime ignores or loses membership/relationships, whether
optimization can choose sides, coverage of the block definitions, the provenance
of fixed/side/edge constraints, and whether final-coordinate validation covers the
owner's intent. A prior 0.5 mm courtyard-edge policy is not evidence of adequate
screw, assembly or enclosure clearance. Do not invent a replacement margin.

The dispatcher must reconcile agent-507's actual ROUTE-4 state and retain any
already-running result at its safe stop. No further routing starts on this
placement while the block repair and regeneration are unresolved. Preserve all
R1–R5 and ROUTE-1–3 evidence as baselines.

## Work and decision sequence

Tracking parent: **AGENTEE-472 — Preserve functional blocks through reusable
placement and regenerate telemetry pod**. This covers the placement repair;
fully routed, independently qualified manufacturing output remains part of the
programme goal after that work is accepted.

| Step | Work item or artifact | Acceptance and next decision |
| --- | --- | --- |
| 1 | **AGENTEE-473 — Trace two-level block placement and production side locking** | Source-backed stage table, actual R1–R5 execution path, causal loss points, side eligibility versus initial pose, focused reproduction and proposed repair boundary. Select implementation from this evidence. |
| 2 | **AGENTEE-474 — Establish sourced mechanical and side constraints for block placement** | Per-face constraint register with sources, geometry and explicit unknowns. Distinguish placement blockers from final-release evidence. Select valid constraints before packing. |
| 3 | Repair reusable placement logic using the existing block model | Cover membership, hierarchy, block/vertical side moves, pin relationships, legalization, feedback and final-board verification. A meaningful congestion regression fails before and passes after the repair. Review the actual consumer path. |
| 4 | Regenerate the full telemetry-pod board | Candidate, source/runtime/input identities, annotated top/bottom views, final-coordinate block/adjacency report, mechanical checks and underside-choice evidence satisfy the placement acceptance matrix below. |
| 5 | Route the accepted placement with qualified current tooling | Complete actual connectivity and native checks. Any component adjustment reopens affected block/mechanical validation. Report blockers without dropping constraints. |
| 6 | Qualify and package the final candidate | Independent electrical, RF, mechanical/assembly and fabrication reviews close applicable findings; complete verified manufacturing package and reproducible tool evidence. No order is authorized. |

Steps 1 and 2 may proceed independently with existing capacity. They do not
restart old broad review/research queues. Reuse relevant prior evidence and
existing lanes; 449 reconciles ownership before dispatching. Each subsequent
implementation/run gets a bounded work item based on the actual dependency,
with one holder, selected subscription model, admitted host, immutable inputs,
acceptance evidence, independent verifier where needed, budget and stop condition.
These are operational decisions within the owner's authorization, not requests
for repeated owner permission.

### First bounded assignments

Agent-449 selects the existing holders and records valid Plane assignees and
session/model/host identities. The initial request is Cairn
`65ab387e-22c8-4612-b72d-23ea75442c96@1`, sent by 529 through 519. Sending does not
prove dispatch or handling; the exact receipt and result must be recorded.

- **Source trace:** at most 45 minutes. Read current source plus archived production
  requests, policies, executed versions and evidence. Trace block creation,
  initial placement, side assignment, macro/global search, residual packing,
  legalization, write/admission and routing feedback through final-coordinate
  checks. Explain whether the intended two passes were executed and distinguish
  absent capability, bypassed path, missing input and coordinator restrictions.
  Return one compact evidence table, reproduction, correction boundary and
  decision-changing unknowns. No new board mutation, routing or broad suite.
- **Mechanical/side register:** at most 30 minutes. Reuse current requirements,
  manufacturer and existing review evidence; targeted primary-source verification
  where needed. Cover fixed anchors; screw heads, washers and standoffs; connector
  bodies and access; antenna/RF/reference-plane limits; specified enclosure and
  thermal limits; per-face courtyards, height and test access. Record sources and
  unknowns rather than inventing geometry or treating heuristics as requirements.
  No board mutation, orders, project upload or broad research campaign.

At the time limit, return selected findings and the exact remaining uncertainty.
The coordinator chooses the next action from those results; no automatic repeat
of an unchanged experiment. Preserve a live bounded job through observation
failures and record its actual terminal state before considering another run.

### Integrator record: dispatch and observed results (agent-449)

- ROUTE-4 terminal state (reconciled by agent-449 on 2026-10-23): agent-507's
  finite batch had run before the suspension arrived. It stopped by its own rule
  after 2 consecutive target failures, and no routing is running.
  - Best retained candidate `5690da57e291dff46f80b239c1c81f164d0622c6d4c0d03b65437e653f025825`:
    one GND junction via. Against ROUTE-3: 77→75 open, DRC 140→138, no new finding
    or split. Against R5: 58 dangling.
  - Two later targets were rejected and reverted.
  - Evidence is preserved and NOT adopted, on fome-mc main `fd9b3c40`,
    `telemetry-pod/expansion/route-4/`. The ROUTE-3 baseline above is unchanged.
  - Gallery: Diagnostic 10, bundle `5690da57e291-216cf677a645`, labelled
    INCOMPLETE / NOT RELEASED / not adopted.
- AGENTEE-473 holder: the existing pod-capture-j6b2 writer (Claude Opus, session
  `266a6f9b-6921-458f-b927-86b58473050f`, proximal), started 08:34:38Z.
- AGENTEE-474 holder: agent-507 (codex gpt-6.1-sol, `0573c9f0`, proximal), Cairn
  request event `767c8dc6`. It starts after the ROUTE-4 packaging.
- Plane assignee for both is the only workspace member (halbritt). The accountable
  agent, model, session and host are recorded in item comments. Both items are
  In Progress.
- Rust planner (owner "bring in the rust planner"): agent-524's branch lands
  through agent-449 before any AGENTEE-472 placement code change, because both
  touch `placer.py` and `placer_runner.py`.

## Placement acceptance matrix

Every result is bound to the final saved board hash after all component changes.
These are required evidence, not statements that the current tool already meets
them. All rows are currently open.

| Requirement | Evidence required for acceptance |
| --- | --- |
| Explicit membership | Every footprint appears in a declared block or has an explicit shared/fixed role; no omission, accidental duplication or unrelated-block merger through shared power/ground nets. Preserve membership at every stage. |
| Hierarchical placement | Sourced fixed anchors, block placement across board area/permitted sides, then member refinement. Flexible block shape retains physical locality and the declared electrical relationships. |
| Optimized sides | Eligible whole blocks can change face; deliberate cross-face arrangements support vertical adjacency. Record restrictions and candidate choices. A constrained congested case demonstrates useful underside selection; no equal-count target. |
| Critical relationships | Device/support obligations for decoupling, feedback, filtering, protection and other declared relationships are evaluated at relevant pins/interfaces where appropriate. Report metric, bound, source and final-coordinate result; small bounding boxes alone are insufficient. |
| Cohesion through adjustments | Global placement, legalization, residual packing and routing feedback preserve constraints, move/reshape/re-side blocks, or explicitly report an unsatisfied constraint. Any relaxation is visible and justified within authority; no silent acceptance. |
| Mechanical clearance | Apply sourced per-face mechanical/access/antenna/height/courtyard restrictions before packing and check them against final saved geometry. Unknown required geometry cannot be reported as a pass. |
| Per-block report | Membership and roles, side assignment, physical spread, estimated internal connection lengths with defined metrics/units/endpoints, critical relationships, and every violation or exception. Derive from final coordinates. |
| Visual evidence | Top and bottom views identify blocks by color or outline with a readable legend and candidate identity. The geometry report establishes compliance; images supplement it. |
| Regression and real board | Congested fixture with usable underside exercises global placement and legalization and catches lost grouping/side eligibility. Reviewed reusable implementation and the regenerated full telemetry pod both meet the contract. |

A native DRC pass, preserved connectivity or completed route is additional
engineering evidence, never a substitute for this matrix. After routing, validate
these constraints again against the final board; any changed footprint needs
fresh relevant checks. Exceptions remain explicit and can block acceptance.

## Preservation, resources and release

Preserve footprint/reference/pad/net identities, schematic correspondence, accepted
four-layer 1.6 mm stackup, sourced keepouts and In1.Cu no-track restriction, and
confirmed mechanical/interface requirements. Establish provenance for restrictions
before applying them; an old initial pose or courtyard side is not by itself a
requirement. Preserve the existing block model and repair its enforcement rather
than creating an unrelated grouping system.

Use subscription routes only; quota/authentication failure stops the affected
operation, with no OpenRouter fallback. Use existing agents and admitted local
capacity under the campaign process. No new cloud startup. Existing GCP limits
remain a $100 cap and $90 operational hold. The Rust scorer remains owner-directed;
no duplicate port or independent scorer assignment. AppArmor work stays cancelled.
No component purchase, fabrication order or new project upload is authorized.

The current private gallery remains a labelled historical/incomplete view until a
new consistent candidate is available. Update candidate-bound views as artifacts
change; 449 owns the existing render schedule. Preserve findings and failed
candidates; do not relabel partial placement, incomplete routing or unknown
mechanical evidence as success.

Completion requires both full-goal outcomes. Inspect the actual final board,
independent review evidence, fabrication outputs and reproducible tool/regression
results against every requirement before declaring manufacturing readiness.
This plan is the current execution record; Cairn handoffs point here rather than
creating a second competing plan. Agent-449 integrates reviewed updates.

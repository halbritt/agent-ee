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

ROUTE-4 terminal facts, verified by agent-449 on 2026-10-23 and retained as
HISTORICAL diagnostic evidence (agent-529 decision `21eebef9`):
- It actually started before the withdrawal and terminated at its two-consecutive-failure
  guard. Per agent-507's report, it is no longer running.
- Retained board `5690da57e291dff46f80b239c1c81f164d0622c6d4c0d03b65437e653f025825`
  and `RESULT.md` `c638c458…` are identical between agent-507 commit `4d022e38` and
  fome-mc main `fd9b3c40`, `telemetry-pod/expansion/route-4/`.
- Native result against R5, recounted from `checks/against-r5/candidate-drc.json`:
  138 violations, 75 unconnected, 58 dangling (the rest inherited), parity 0.
  Against ROUTE-3: 77→75 open, with no new finding or split.
- Gallery bundle `5690da57e291-216cf677a645` carries status
  `216cf677a645-e57435415c8b` (routed with completeness not verified, not adopted,
  not qualified, DRC 138 / 75 unconnected).
- This is evidence retention, not acceptance of functional grouping or release.
  Placement grouping is unverified.

The old routing dispatch stays closed. No further routing starts on this placement
while the block repair and regeneration are unresolved. AGENTEE-473/474 keep their
immutable inputs (ROUTE-3 `08066170`, fome-mc `46c7c730`) for comparison. Preserve
all R1–R5 and ROUTE-1–4 evidence as baselines.

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

### Current state and next actions (agent-449, 2026-10-23 11:06Z)

This section is replaced, not appended, on each observed result. Earlier states are in git history.

Owner directions (relayed by agent-519, verbatim quotes kept separate from interpretation):
- "bring in the rust planner"; "make the new rust planner the default".
- "Pogo clip on bench": TP1–6 are hand-probed on the bare board before enclosure, with BLE OTA after. Back-side test points are fine. Reserve probe approach only; no fixture.
- "Once, on the bench": JP1–5 get soldering-iron access before enclosure.
- "90mm height, the length and width are quite a bit more than that": about 90 mm is a provisional HEIGHT envelope for the full stack. It is an estimate, not a cap; the outline is not cowl-constrained.
- "Agents pick M3 hardware".
- "leave the files in local"; "fuck trakcing the licenses": vendor models stay local, with no licence tracking.
- "try not to block on anything waiting for me.": use engineering defaults within authority, recorded as assumptions; hard limits still apply.
- These answers settle usage and selection only. They never establish a clearance PASS.
- The inductor variants are exact: L1 SRP7050TA-150M, L2 SRP7050WA-6R8M (`47f9ef50`).

Reference boards:
- Diagnostic reference and gallery pin: `b1bb009b`, the ROUTE-4 board plus 9 KiCad stock models. Not adopted, no poses frozen.
  - Model coverage is 292/309. 17 are unresolved: 15 intended-none, and L1/L2 missing pending the owner fetch AGENTEE-481. U3 is visualization only.
- Retained for comparison: `57a7968d` (ROUTE-3 plus models), ROUTE-3 `08066170`, ROUTE-4 `5690da57` (historical) and R5 `8f3bc234`.

| Item | State | Holder / handle | Budget | Blocker and next actor |
| --- | --- | --- | --- | --- |
| AGENTEE-479 implementation (engine) | running | agent-524 (Astra), Cairn `91c7aab7`, from 09:39Z, `wt/agentee-479`; agent-524 also finalizes `block-candidate-set/1` | 120-minute checkpoint | Then the critical code review: claude-fable-5-1 HIGH (owner rule), with agent-510 on contract and evidence. Phase rule `39bfdc72`: pre-packing claims only; post-routing items never pass early. |
| AGENTEE-484 decoupling source repair | **LANDED** fome-mc `07ed909` (writer `890e8c6`; includes 478b, the v2 cowl HEIGHT and 478c) | — | — | Verified: ERC 0, C130–C133 only, validator PASS (313 footprints / 26 blocks), sums and markers clean. agent-510 ACCEPTED the netlist and rebinds (`c44a3749`); the open dispositions moved to AGENTEE-488. |
| AGENTEE-484r RF_50 binding in source plus the production_run.sh AppArmor fix | **LANDED** fome-mc `697b2b32` | — | — | Verified: the rule condition now also selects `/POD_GNSS_RF` (project copy byte-identical to source); native DRC flags a 0.20 mm `/POD_GNSS_RF` track with the binding and not without it; production_run.sh runs `apparmor=unconfined`. |
| AGENTEE-478c/478d anchor roles and U2 orientation | 478c landed with 484; **478d LANDED** fome-mc `d46183ef` | — | — | 478d changes only the U2 entries in side-eligibility.json and fixed-shared-rules.json; SHA256SUMS and validate_coverage.py PASS. U2 per agent-529 `c7adf503`: board F-side faces UP (engineering assumption); face and rotation DERIVED from the package axes plus the board-to-bike transform; agent-507 verifies the package axes in 486. |
| AGENTEE-486 mechanical closure | running | agent-507 (Sol), Cairn `ddbea5b2`, from 10:14Z | 60-minute checkpoint, then a 20-minute Astra/Fable-HIGH verification | Covers heights rebound to the 484 parts, the J1 mate/harness/tool sweep, iron/card/SMA access, antenna case and metal, the L1/L2 variants, and the J1–H3/H4 and antenna/hardware anchor conflicts. The current poses, hardware and outline are NOT owner-frozen. |
| AGENTEE-480 GNSS binding and mechanical inputs | landed fome-mc `b389b47a` | agent-507 | — | ROUTE-3 fails F-over-In1 (expected before regeneration). 19 H1–H4 reservation conflicts. |
| AGENTEE-487 missing copper planes plus the runtime-receipt rebinding | RESULT verified by 449; critical review running (Astra HIGH, `review-ee487`, from 11:04Z, 15 minutes); NOT landed | j6b2 writer (fome-mc `wt/agentee-487` 8204a29; agent-ee `wt/agentee-487-planes` 2c2d99f7) | checkpoint 10:41–10:59Z | Root cause: add-planes is declared for the campaign routing turn, and before routing it refused the wholly unconnected In2 /+3V3 always zone, so no saved board ever had planes. Fix (529 `76150ac4` option a): add_planes defers a wholly unconnected always zone as `deferred_connectivity`; the plan stays always/always (the never-policy change e780472 is reverted). 449 checks: tests.test_add_planes 15/15 in the CI image; receipt test 11/11 (accepts any matching binary/receipt pair, rejects a mismatch, no 5261cb13); a wrong net still refuses; sums OK; no 479 overlap. OPEN: nothing consumes deferred_connectivity after routing yet; production regeneration is blocked by the JLC stock-freshness gate (capture older than 24 h; a refresh needs a decision); the 484r retention is missing 2 files that only regeneration can restore; agent-ee main's producer refuses this project differently (constraint provenance geometry inventory). |
| AGENTEE-488 electrical dispositions (C133 VIO topology for U100 and U21, C18 derating, U5 6.3 V bank) | early READ-ONLY packet RETURNED by agent-522 (findings sha256 313cf891…, sums OK); Fable 5.1 HIGH verification `review-ee488r` running from 11:09:40Z (20 minutes) | research: agent-522; source edits: j6b2 writer after 487 lands and 529 adopts | phase 10:46:34–11:31:34Z | Proposed and NOT yet adopted: C133 terminal 2 /GND to /+5V_CAN (AH1014 Fig17 VIO-to-VCC), a NEW C134 for U21, and C18 0603 1 uF to 1210 22 uF CGA6P3X7R1E226MT000N (2.8 mm height: affects 486 and 479). U5 is PARTIAL: static 6.169–6.432 V, OVP recovery margin 70.6 mV, L1 3.5 A Irms / 6 A Isat, C6/C7 about 25.4 uF effective; the load-step envelope is unresolved. These changes must settle before 485 freezes its inputs. |
| AGENTEE-186 RF qualification (design prediction only; routed geometry and the connected reference are verified post-routing) | early checkpoint RETURNED (agent-510, Cairn `526e9dc2`): CONDITIONAL geometry, not qualification; Fable 5.1 HIGH review `review-ee186rf` running from 11:06Z (20 minutes) | agent-510; review: Fable | — | Grounded coplanar on F.Cu over In1 /GND: 0.335 mm track, 0.2032 mm gap on both sides; tolerances UNKNOWN. The uncoated 49.66-ohm point does not transfer to the masked board. PRE-FREEZE: the corridor gap, the CPW ground strips plus a via fence outside 480's keepout, and the L3/D3 shunt taps were relayed to agent-507 for 486 (Cairn `88677e01`); 485 consumes 507's answer. 529 `8900b0a9`: ONE retained coated JLC public-calculator request dispatched to agent-510 (Cairn `9dd12a01`, 20 minutes, public parameters plus the generic geometry only; on failure, no uncoated substitute). The stackup-metadata correction is queued for the j6b2 writer after 487 (15 minutes, re-emitted, 510 verifies correspondence). |
| AGENTEE-483 model assets plus the local vendor store | queued after 488 (optional) | j6b2 writer | 45 minutes | Owner: local only, no licence work |
| AGENTEE-485 regeneration plus placement acceptance | next stage, not started | — | 60 minutes plus agent-510 (20 minutes) | Needs reviewed 479, 484 (landed; agent-510 accepted the netlist), 484r (landed), 487, 488 (any part changes), 480, **486**, 478b, 478c and 478d (landed) |

AGENTEE-485 runtime pin (agent-529 `b2422ab3` v2): Rust `5261cb13` (476) and `7d5b0d4d` are HISTORICAL baselines. 485 builds, uses and hashes the binary from reviewed 479 plus the actual integration source, with its build receipt. Source, mounted Python, binary, contracts and native candidate describe ONE production run. 449 checks that 487's runtime receipt binds the binary actually used, not a hard-coded hash.

AGENTEE-485 acceptance (agent-529 `2dc4d4a5`):
- The real production path explicitly consumes the allocation, eligibility, relationships and templates.
- It delivers the final native board and project, plus same-candidate annotated front and back views.
- It reports complete per-block and critical-pin metrics, and side-choice evidence.
- Mechanical checks keep unknowns separate.
- Source and runtime hashes are recorded, including the mounted Python source and the Rust binary and receipt.
- Native correspondence and checks run.
- Hand placement as a substitute for the tool repair is NOT allowed, and neither is a frozen 309 count.
- Passing 485 admits the candidate to ROUTING, not to manufacturing release. The same saved-coordinate checks re-run on the final routed board.

Settled results:
- The 473/473b traces and the 474 register (fome-mc `4ff731c9`, `d8f6ec09`, `7be86549`). agent-510 put them on HOLD; the correction is AGENTEE-482.
- AGENTEE-475, AppArmor profile loading removed (agent-ee `061c8049`).
- AGENTEE-476, Rust planner as the default (agent-ee `b2607131`). agent-522 found no mismatch (the timing figures are historical only).
- AGENTEE-482, HOLD correction (fome-mc `f65d7903`); agent-510 ACCEPTED the investigation package.
- AGENTEE-478, D1–D5 prep (fome-mc `57810f11`).
- AGENTEE-477, 3D models (fome-mc `fa41e4b9`).
- ROUTE-4 historical evidence (fome-mc `fd9b3c40`).

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

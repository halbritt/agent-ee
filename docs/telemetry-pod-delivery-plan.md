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

### Current state and next actions (agent-449, 2026-10-23 13:51Z)

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
| AGENTEE-479 implementation (engine) | **LANDED** agent-ee main `c4724465` (479 at 09c0076b) plus `a28e80a0` (524's 488 return-leg proof 1e1d2367, tests/docs only) | — | — | Gate 349 OK; merged-result check_docs/check_origins/63 tests OK; runtime receipt and binary proven against 09c0076b; archive 1040/1040; Fable LAND-WITH-FOLLOW-UP (G1–G7 queued, proposed for 524 after 489); 510 PASS-WITH-NOTES (fome-mc pipeline.py:498 accepts /2 only, so 485 adds the selected-variant path). Rust remains the default planner. |
| AGENTEE-484 decoupling source repair | **LANDED** fome-mc `07ed909` (writer `890e8c6`; includes 478b, the v2 cowl HEIGHT and 478c) | — | — | Verified: ERC 0, C130–C133 only, validator PASS (313 footprints / 26 blocks), sums and markers clean. agent-510 ACCEPTED the netlist and rebinds (`c44a3749`); the open dispositions moved to AGENTEE-488. |
| AGENTEE-484r RF_50 binding in source plus the production_run.sh AppArmor fix | **LANDED** fome-mc `697b2b32` | — | — | Verified: the rule condition now also selects `/POD_GNSS_RF` (project copy byte-identical to source); native DRC flags a 0.20 mm `/POD_GNSS_RF` track with the binding and not without it; production_run.sh runs `apparmor=unconfined`. |
| AGENTEE-478c/478d anchor roles and U2 orientation | 478c landed with 484; **478d LANDED** fome-mc `d46183ef` | — | — | 478d changes only the U2 entries in side-eligibility.json and fixed-shared-rules.json; SHA256SUMS and validate_coverage.py PASS. U2 per agent-529 `c7adf503`: board F-side faces UP (engineering assumption); face and rotation DERIVED from the package axes plus the board-to-bike transform; agent-507 verifies the package axes in 486. |
| AGENTEE-486 mechanical closure | **ACCEPTED (agent-529 `49106e9b`) and LANDED as the reviewed PARTIAL increment** on fome-mc main `33c5ba8`; nothing implied for packing admission, J2 tolerance, J1 mating or the J3 notch. The 51658d32 annotation delta does not change geometry. Review limits stand: envelope areas, 35-disc containment and helper runs were not independently rerun | — | — | Fable: review-ee486 LAND-WITH-FOLLOW-UP; delta BLOCK narrow H1 corrected; Delta 2 LAND (partial). H1 closed by 449 diff/hash check (no active C134; C84 net-only; C18 2.8 mm). CPW reservations at 0.293/0.2032 (half-width 0.3497). Heights 296 sourced / 15 bare / 2 UNKNOWN (J1, J3). Sums verified from a fresh checkout. Follow-ups D2-1..3 (L3 relief or +0.021 mm conservative; U2 installed axis NOT_RUN, not a pre-fab gate; 0.335 text superseded) plus the review-fix-map H1_2 attribution fix (0.293 = chief direction 4cf9d43d@2; 37bc38d9@1 superseded the early-rebase order) are in the writer's 489 source handoff; open J1/J2/J3/U2/post-186 rebind items move to AGENTEE-490. |
| AGENTEE-480 GNSS binding and mechanical inputs | landed fome-mc `b389b47a` | agent-507 | — | ROUTE-3 fails F-over-In1 (expected before regeneration). 19 H1–H4 reservation conflicts. |
| AGENTEE-487 missing copper planes plus the runtime-receipt rebinding | agent-ee LANDED main `2c7162c2` (producer eea55a00 + 487c 4f63e270 + 522 H2c fafc12c4). Fable HIGH review-ee487h2c: H2 FIXED, LAND-WITH-FOLLOW-UP (71 tests; Astra's 4 reproductions refuse; 9/9 bypass probes refused). Landing gate: check_docs/origins 0, 282 tests OK. fome-mc side NOT landed separately: on main f6c7ec2 the H4-fixed receipt checker rejects the existing historical receipt ('initial candidate differs from the retained pipeline'), and the reissued stock-484r2 retention fails retain --check against main's REQUIREMENTS.md (486 POD-ME-1 change). Stock refresh b683054 already on main | fome-mc 2ab758b + 60f6a78d land WITH the 488c stack: the j6b2 writer merges 2ab758b into 488c (receipt code already byte-identical; conflicts only design-rules README/SHA256SUMS, regenerate) | -- | Follow-ups: measure O(n^2) polygon-chain validation on the actual pod In1/In2 fills before 485 relies on deferral (inspect timeout 180 s); consumer does not reload the saved board (forgery out of threat model, AGENTEE-348); mm tolerance comment. |
| AGENTEE-488 electrical dispositions (C133/C84 VIO topology, C18, U5 6.3 V bank) | ADOPTED (`061a1b20` v2); source edits DONE by the writer (fome-mc wt/agentee-488 c98e1135: board delta exactly C133.2/C84.2 -> /+5V_CAN and C18 -> C694521 1210; ERC 0; retention fresh-verified 415 files; named-return 'unsatisfied' fix 9/9); Astra review `review-ee488fome` running; NOT landed (based on held stock-484r2). U5 continues in AGENTEE-491 | edits: j6b2 writer; generic return-leg consumer: agent-524; U5 research: agent-522 (slice ended 11:31:34Z) | — | Adopted: C133 pin 2 and EXISTING C84 pin 2 move /GND to /+5V_CAN (U100.5/U100.3 and U21.5/U21.3 named endpoints; a missing, wrong-net or unmeasurable return FAILS); NO C134; C100/C83/C19 unchanged. C18 = CGA6P3X7R1E226MT000N (C694521) 22 uF/25 V 1210 as an ENGINEERING MARGIN choice inside REF50 §9.3 (1–50 uF, REF50xxI/AI), deliberately above the §9.4.1.1 1–10 uF layout example; NOT a stability repair. The REF50 0.1 uF stable-input figure is the EI grade (§6.6) and does NOT apply to the fitted REF5050AIDR (AI grade), so no stability margin is claimed (Fable correction accepted by 529). Height 2.80 mm max. U5: the original slice was accepted as PARTIAL (11:09 packet); ONE new 30-min follow-up dispatched to agent-522 at 11:46:48Z (Cairn `87b7daad`): conservative envelope and a C18/U5/L1/C6/C7 source-change decision before 485, then a Fable delta. Open, decision-changing: normal/simultaneous PRE_6V3 load envelope; inrush/soft-start with the 22 uF C18 and distributed capacitance; fault/retry; aging/thermal; loop response. This remains a manufacturing-release gate. |
| AGENTEE-186 RF qualification | 0.293 mm / 0.2032 mm two-flank ADOPTED (agent-529 `4cf9d43d` v2) as the AGENT conditional CAD nominal; no tolerance band and no 'impedance verified' | agent-ee RF update LANDED main `8c8a6d1c` (6678bb12; check_docs/origins 0; test_gnss_rf_path, routing_policy_contract, rf_geometry, hygiene, origins: 112 tests OK). Stackup repair LANDED main `5b3fc2d7` (524 1d61e314; Fable READY-WITH-GAPS = LAND-WITH-FOLLOW-UP; landing gate 263 tests OK, docs/origins 0). fome-mc RF rules (writer 324b37ee) ride in the 488c stack | -- | Follow-ups: stackup G1 the fome-mc writer must declare prepreg 0.2104/Er 4.4, core 1.065/Er with evidence (4.6 unsourced), prepreg, loss_tangent null (writer 489 slice; 510 checks correspondence); G2 compare_saved status defect over loss-unknown and G3 require dielectric_type with any material field (524, after the 26-failure slice); G4 0.02 label is not provenance. Post-route RF checks remain (492). |
| AGENTEE-483 model assets plus the local vendor store | queued after 488 (optional) | j6b2 writer | 45 minutes | Owner: local only, no licence work |
| AGENTEE-489 enforce source-bound mechanical reservations throughout placement (child of 472; agent-529 `9dee4ab7`) | DISPATCHED | consumer: agent-524 (Cairn `465ec3e4`, a 45-min NEW slice after its scaffold fix; then the return-leg repair); source declaration: j6b2 writer (notes/agentee-489-source.txt, 30 min, after its queue AND after AGENTEE-490's reviewed decision table and reservations, plus the reviewed 486 packet). Consumer slice 1 RETURNED (agent-524 11383b09; Fable 5.1 HIGH review `review-ee489` from 12:28:34Z) | 45 + 30 min, then a Fable 5.1 HIGH 20-min delta | Must reject a mechanically illegal flip in selection AND in the final judge, accept a legal underside alternative, catch stale bindings after native moves/rotations/flips, and keep fixed obstacles fixed while attached ones follow their owner. Unknown height is never zero. 485 requires it. |
| AGENTEE-490 resolve mechanical inputs that can change packing or fabrication (child of 472; links 486, 489; agent-529 `151803ac`) | RETURNED 13:08:10Z (b503bf71); Fable LAND-WITH-FOLLOW-UP; ACCEPTED as PARTIAL (529 `82f08e7a`) and LANDED on fome-mc main `f6c7ec25`. J2 option A selected (vertical SMA, AGENTEE-494); J1 external mating and J3 plain-edge B (9.1,40,90) directions selected, no notch. Follow-ups H1 materialisation and H2 citations sit in the writer's 489 slice | agent-507 (Sol) | 40 + 20 min | Immutable inputs: 486 packet 51658d32 (sums 48300baa…), 480 packet b389b47a (RESULT 1fec5b5b…), fome-mc main b6830548 (rules 2769902a…, BOM parts.json 2e9afa70…, intent c7c1eb52…). Priorities: J2 slot vs finished 1.6 mm board/solder (the 0.0002 mm nominal is not a pass; give a concrete alternative if unqualified); J1/J3 bounds and mating/card/tool; bare-board and installed-hardware reservations. Output: one decision table plus bound reservations for the writer/489; unresolved items need the missing evidence AND a finite alternative. |
| AGENTEE-491 close PRE_6V3 recovery and supported sensor-output limits (child of 488) | 522 slice DONE (packet SHA256SUMS 58040048); Fable ADOPT-WITH-CORRECTIONS; agent-529 CONFIRMED (`74db43f0`); probes/ee491 recompute complete (calculations IDENTICAL) | value/BOM binding: j6b2 writer, queued 20 min (notes/agentee-491-bom.txt) | 20 min | R74/R78/R82 -> 75 k RT0603BRD0775KL (C723587); R75/R79/R83 -> 208 k RT0603BRD07208KL (C861219); R73/R77/R81 stay 1.05 M. Value-only (no footprint/height/membership). Post-stress recovery +119.72 mV; max OV trip 8.216 V; UV headroom +61.89 mV. The >= 8.3 V sensor screen is agent-derived and STATIC only (transients unqualified). No sensor or CAN2 proposal adopted; 650/210 mA and 1.487 A stay conditional. The 5 V ±1% aux stays OPEN. |
| AGENTEE-492 exact-CAD RF CPW measurement contract (child of 186) | Obstacle CONFIRMED; ruling 8683ae8e (0.2032 = physical emitted gap). Emitter feasibility 9fd76c03: Fable FEASIBLE FOR AXIS-ALIGNED ORDINARY SECTIONS CONFIRMED (nominal DRC 0; gap 203200 nm exact; GND component incl. 4 vias + In1 after refill; tamper probes fail the verifier). Gaps D1 vias checked on reloaded board, D2 non-axis = UNKNOWN (Pythagorean directions exist), D3 pod geometry (F.Cu pour) not shown, D4 bind to geometry digest | agent-510 implementation slice (Cairn `14404398`; RELEASED by agent-529 `3bda7fb7`, which accepts 9fd76c03 as fixture feasibility, NOT pod/RF acceptance; amendment `da204277`). Frozen integrated base: agent-ee main 5b3fc2d7 (contains 6678bb12 via 8c8a6d1c) + 9fd76c03 (docs only, clean merge; 510 merges it first and reports the hash) | 120 min (14:01:51Z-16:01:51Z) + same Fable 30 | Engineering routing constraint (529): ordinary RF sections are AXIS-ALIGNED; non-axis/curves located UNKNOWN with residuals. Explicit connected GND flank conductors on both sides even with the F.Cu pour; D1 vias on the reloaded refilled board; D4 reports bound to the exact saved board hash (geometry digest supplementary). D3 pod application needs a valid 485 placement: if not ready by the cutoff, 510 returns fixture evidence + the named unexecuted pod-application step (a later dependent slice, not an extension). Writer RF declaration (10 min) follows the reviewed consumer. |
| AGENTEE-493 close 489 review gaps (child of 489; agent-529 `8958d1be`, `d66d0495`) | DISPATCHED to agent-524 (Cairn `417a2433`), 13:04:41Z–13:34:41Z (30 min), then the same Fable reviewer 15 min | agent-524 | 30 + 15 min | G5 Stage-A intra-block attached reservations (REQUIRED before production 489/485), G2 saved-check original binding, G3 Python/Rust blocker order, G1/G6 docs, G4 test fix. Frozen base agent-ee 93258726. NEXT on 524: the stackup repair (agent-529 `3ddc945d` item 2; 20 min + Fable 15; serialized after 493 because both touch the envelope docs; the loss tangent stays unknown and a KiCad 0.02 default is labelled a tool default). |
| AGENTEE-494 J2 vertical SMA (Amphenol 132134): joint budget, footprint, reservations, sealed penetration (child of 490; agent-529 `82f08e7a` selected option A) | 58eac58f HOLD as published (agent-529 `02cdc35b`): useful PARTIAL input, not an approved canonical footprint. 507 fix fe89e701 DONE: de2cc15b (H1 relabelled; H2 figures UNRESOLVED; H3 frames declared), 449-verified (SHA256SUMS cbb76912 OK, no 'Owner529'); same-Fable delta on de2cc15b: LAND-WITH-FOLLOW-UP as PARTIAL writer input (H1/H2/H3 fixed truthfully; 24/24 sums; N1 nit: bulkhead 095-902-570-006 is a candidate pointer only). It had completed before 529 `243fb1bb` asked to coalesce, so the 494b result gets ONE final same-Fable 15 delta on the final packet | agent-507 494b slice (Cairn `afd61829`), started FROM de2cc15b per 519's reconciliation (fe89e701 had returned; 529's 58eac58f freeze satisfied by descent) | 30 min (14:10:32Z-14:40:32Z) + same Fable HIGH 15 | (a) state exactly what the (+20,+12) mapping covers; F(103,40,0) is proposed-frame, not saved-pose proof; the writer rebinds geometry + both RF area UUIDs after adoption. (b) 341.25 mm = engineering proposal; external 457 mm does not violate the owner's 500 mm (REQUIREMENTS:70). (c) a placement-admissible J2 interface with finite reservations + assembly instructions. (d) if 132134's joint stays unbounded, ONE exact SMT alternative (105 C rule, DC path, sealed SMA penetration), ranked. Adoption by the chief after review; then 449 lands the package on fome-mc as partial input. |
| agent-ee main integration 13:40–13:47Z | LANDED `1177cbc2` (479 follow-ups 259fb123 + 489 slice-1 + 449 stub fix 32b36d6c) and `87025844` (AGENTEE-493 697820be; G5 CLOSED at implementation level, agent-529 `38f1ac7e`) | — | — | Merged gates: 493 261 tests OK, docs/origins OK. The 489 landing found 26 PRE-EXISTING placer failures (copper_extent x24, packing_scope, pad_keepout), identical on pre-merge 254d259d; the 1 new error (capacity stub) is fixed. 493 follow-ups F1–F3 stay named. |
| Pre-existing placer failures (26) | LANDED main `51a6db8b` (524 08c800cd, tests only). Fable HIGH review-placer26: READY = LAND (cause (a) reproduced by tripwire; (c) ruled out: Rust constraints::blockers calls copper_escapes with board pad keepouts and the native publication gate catches independently; gate-disabled probe fails 20/20). Landing gate: 141 tests OK, docs/origins 0 | -- | -- | Closed. (The reviewer's scratch probe logs were deleted with its worktree by 449; the review file and verdict are intact.) |
| AGENTEE-496 5 V sensor/auxiliary accuracy and placement-changing decisions (child of 488; agent-529 `73047144`) | DISPATCHED to agent-522 (Cairn `130f7ec7`) after its 487 H2c disposition; frozen fome-mc f6c7ec25 + reviewed 491 packet + 061a1b20@2/74db43f0 | agent-522 | 30 min (14:34:11Z-15:04:11Z) + ONE Fable HIGH 15 | Source-only decision packet: REF5050AI -> U6 -> connector and U30 paths, sourced error/headroom budget per 5 V output; retained topology or ONE ranked repair. OWNER CONSTRAINT 5.00 V +/-1% (REQUIREMENTS:36): if no finite repair meets it, the finding goes to 519, never a silent relaxation. A topology/footprint/relationship change needs a chief decision before final placement. |
| AGENTEE-495 stackup follow-up G2/G3 (agent-529 `1ed2358b`; stackup 1d61e314 ACCEPTED, landed 5b3fc2d7) | 524 slice DONE in-slice: wt/agentee-495 f13a3944 (committed 14:15:46Z; G2 material mismatch dominates unknown loss, scoped so legacy authority-unknown is unchanged; G3 epsilon_r/loss_tangent key presence requires dielectric_type; G4 unchanged). Worker: Ran 145 OK, red/over-broad runs retained; archive sha256 2f3a3a4f re-hashed by 449 | same Fable delta (review-eestackup), launched 14:33Z | 10 min | G2 proven type/Er/material defect dominates unknown loss (evidence kept); G3 any material declaration names dielectric_type (refuse, no silent core); legacy no-material envelopes unchanged; G4 0.02 stays unqualified/not-compared. Must close before production 485 consumes the writer's material declarations (G1, in the writer's 489 slice with the JLC 1f09c567 capture bytes). |
| AGENTEE-488c H2 (requirements revision delta) + 491 coherent production | H2 guard ACCEPTED by agent-529 `243fb1bb` at 9cd8f11 + 92e6637 (Astra: FIXED, LAND-AFTER-PRODUCTION). Revision delta DONE (writer 9cd8f11/92e6637: requirement_revision.py admits only POD-CD-STORAGE + exact POD-ME-1 d865a76d->09c89ce3; revision-delta.json 1 added/1 changed/77 unchanged; 449 re-ran test_requirement_revision 9/9 PASS). Production BLOCKED at build-out: 'binding differs from the canonical record: R74' -- no canonical records for C723587/C861219 | j6b2 writer (491 records + coherent production, launched ~13:56Z) | 40 min counted from process start 13:54:45.964Z, CUTOFF 14:34:45.964Z (529 `243fb1bb`), + same Astra 15 delta | Astra guard delta (13:58Z): H2-guard FIXED, LAND-AFTER-PRODUCTION (9/9 controls + 14/14 independent bypass probes; revision-delta.json reproduced byte for byte); nonblocking: pipeline.py:35-38 module prose still says only POD-CD-STORAGE (writer's next slice). Canonical datasheets + prepare_record for both resistors, build.py declarations, rating source, generation-day stock, production, new retention, composed retain --check, fresh git-archive proof. Board geometry moved (287 footprints) so no prior placement/routing acceptance carries over. |
| AGENTEE-485 regeneration plus placement acceptance | NOT STARTED; gated | j6b2 writer: (a) 489 source declaration (30 min), (b) a production-integration slice (20 min, `b712b9fc` item 3: consume block-candidate-set/1 with its selected variant, eligibility, refinement witness and final rechecks; pipeline.py:498 historical /2 validator untouched; underside and stale-binding controls reviewed by agent-510), (c) regeneration (60 min) plus agent-510 review (20 min) | 20 + 60 + 20 min | Needs: 479 (LANDED), 489 consumer (524) + source (writer), 487 (H2/H4 re-fix, 522 consumer, 524 scaffold fix), 488 edits (writer, running), 186 RF source (writer, running), 486 corrected delta reviewed, 490 decisions reviewed, the stock refresh (LANDED b683054), and a correctly bound regenerated project. No hand placement; no frozen 313 count. |

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

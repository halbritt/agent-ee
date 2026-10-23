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
| AGENTEE-487 missing copper planes plus the runtime-receipt rebinding | OPEN. Stock refresh LANDED on fome-mc main `b683054` (81/81 found; hashes re-verified). Astra delta of the writer fixes: BLOCK (H1-producer and H3 FIXED; H2 NOT FIXED: islands.json is a heuristic and malformed flags are accepted; H4 NOT FIXED: selected identities are not compared). 484r2 retention HELD (its receipt came from the H4-unfixed script). 522 consumer closure: terminal return 4f63e270 (agent-ee wt/agentee-487c; merges producer 259b3eec; 21 tests on peecee-wsl); the same Fable reviewer's 15-min closure delta (review-ee487c '## Delta (f14bb6cf..4f63e270)') gives LAND-WITH-FOLLOW-UP with all 529 criteria MET. NOT accepted for landing yet: it carries 259b3eec, whose H2 is under re-fix, so it lands with the writer's H2/H4 re-fix after the same Astra re-check; the 485 gate stays until then; 524 source-inventory repair a8fece00 ACCEPTED and LANDED on agent-ee main `2bcd3d1f` (Fable review-ee487s READY/LAND; the old-base fail reproduction is complete; it closes the six-missing-rules defect ONLY) | writer: H2/H4 re-fix queued (notes/agentee-487-h2h4.txt, 25 min) after its 488 + 186 slices; 522: consumer; 524: producer scaffold | — | 485 needs: H2/H4 fixed and re-reviewed, the 522 consumer accepted, the 524 scaffold fix landed, and a correctly bound regenerated project. |
| AGENTEE-488 electrical dispositions (C133/C84 VIO topology, C18, U5 6.3 V bank) | ADOPTED (`061a1b20` v2); source edits DONE by the writer (fome-mc wt/agentee-488 c98e1135: board delta exactly C133.2/C84.2 -> /+5V_CAN and C18 -> C694521 1210; ERC 0; retention fresh-verified 415 files; named-return 'unsatisfied' fix 9/9); Astra review `review-ee488fome` running; NOT landed (based on held stock-484r2). U5 continues in AGENTEE-491 | edits: j6b2 writer; generic return-leg consumer: agent-524; U5 research: agent-522 (slice ended 11:31:34Z) | — | Adopted: C133 pin 2 and EXISTING C84 pin 2 move /GND to /+5V_CAN (U100.5/U100.3 and U21.5/U21.3 named endpoints; a missing, wrong-net or unmeasurable return FAILS); NO C134; C100/C83/C19 unchanged. C18 = CGA6P3X7R1E226MT000N (C694521) 22 uF/25 V 1210 as an ENGINEERING MARGIN choice inside REF50 §9.3 (1–50 uF, REF50xxI/AI), deliberately above the §9.4.1.1 1–10 uF layout example; NOT a stability repair. The REF50 0.1 uF stable-input figure is the EI grade (§6.6) and does NOT apply to the fitted REF5050AIDR (AI grade), so no stability margin is claimed (Fable correction accepted by 529). Height 2.80 mm max. U5: the original slice was accepted as PARTIAL (11:09 packet); ONE new 30-min follow-up dispatched to agent-522 at 11:46:48Z (Cairn `87b7daad`): conservative envelope and a C18/U5/L1/C6/C7 source-change decision before 485, then a Fable delta. Open, decision-changing: normal/simultaneous PRE_6V3 load envelope; inrush/soft-start with the 22 uF C18 and distributed capacitance; fault/retry; aging/thermal; loop response. This remains a manufacturing-release gate. |
| AGENTEE-186 RF qualification | 0.293 mm / 0.2032 mm two-flank ADOPTED (agent-529 `4cf9d43d` v2) as the AGENT conditional CAD nominal: min = max width on ordinary segments; the 484r exact-net selector kept; no tolerance band and no 'impedance verified' | writer DONE: fome-mc wt/agentee-186 324b37ee (dru width min=opt=max 0.293, F.Cu CPW clearance min 0.2032, U3/J2 launch REVIEW scope, 484r selector kept; dru d4ce8f95; retention 408 fresh-verified) and agent-ee wt/agentee-186-rf 6678bb12 (fixtures/inverse/docs/tests); Astra reviews running; NOT landed. Stackup HOLD (agent-ee scaffold.py:149 writes 'core'/no Er) and the rf-geometry gap/In1 consumer needs a tolerance ruling: both with 529. Earlier: source change: j6b2 writer slice 2 (agent-ee + fome-mc, 30 min TOTAL approved `b712b9fc`), running in the writer turn launched 12:00:15Z; then a Fable 5.1 HIGH delta | 30 min | Basis: inverse W1 0.29287 mm, Z 50.009 ohm at T1 0.04064 mm (calculator 1.6 mil; guide taper 0.7 vs client 0.5 recorded, not merged); the forward 47.394 ohm @ 0.335 / T1 0.035 kept separately. Two-flank gap and In1 reference must be consumed and measured; a missing consumer is returned explicitly. 507 rebases the corridor to 0.293 (correction slice). Post-route RF checks remain. |
| AGENTEE-483 model assets plus the local vendor store | queued after 488 (optional) | j6b2 writer | 45 minutes | Owner: local only, no licence work |
| AGENTEE-489 enforce source-bound mechanical reservations throughout placement (child of 472; agent-529 `9dee4ab7`) | DISPATCHED | consumer: agent-524 (Cairn `465ec3e4`, a 45-min NEW slice after its scaffold fix; then the return-leg repair); source declaration: j6b2 writer (notes/agentee-489-source.txt, 30 min, after its queue AND after AGENTEE-490's reviewed decision table and reservations, plus the reviewed 486 packet). Consumer slice 1 RETURNED (agent-524 11383b09; Fable 5.1 HIGH review `review-ee489` from 12:28:34Z) | 45 + 30 min, then a Fable 5.1 HIGH 20-min delta | Must reject a mechanically illegal flip in selection AND in the final judge, accept a legal underside alternative, catch stale bindings after native moves/rotations/flips, and keep fixed obstacles fixed while attached ones follow their owner. Unknown height is never zero. 485 requires it. |
| AGENTEE-490 resolve mechanical inputs that can change packing or fabrication (child of 472; links 486, 489; agent-529 `151803ac`) | RETURNED 13:08:10Z (b503bf71); Fable LAND-WITH-FOLLOW-UP; ACCEPTED as PARTIAL (529 `82f08e7a`) and LANDED on fome-mc main `f6c7ec25`. J2 option A selected (vertical SMA, AGENTEE-494); J1 external mating and J3 plain-edge B (9.1,40,90) directions selected, no notch. Follow-ups H1 materialisation and H2 citations sit in the writer's 489 slice | agent-507 (Sol) | 40 + 20 min | Immutable inputs: 486 packet 51658d32 (sums 48300baa…), 480 packet b389b47a (RESULT 1fec5b5b…), fome-mc main b6830548 (rules 2769902a…, BOM parts.json 2e9afa70…, intent c7c1eb52…). Priorities: J2 slot vs finished 1.6 mm board/solder (the 0.0002 mm nominal is not a pass; give a concrete alternative if unqualified); J1/J3 bounds and mating/card/tool; bare-board and installed-hardware reservations. Output: one decision table plus bound reservations for the writer/489; unresolved items need the missing evidence AND a finite alternative. |
| AGENTEE-491 close PRE_6V3 recovery and supported sensor-output limits (child of 488) | 522 slice DONE (packet SHA256SUMS 58040048); Fable ADOPT-WITH-CORRECTIONS; agent-529 CONFIRMED (`74db43f0`); probes/ee491 recompute complete (calculations IDENTICAL) | value/BOM binding: j6b2 writer, queued 20 min (notes/agentee-491-bom.txt) | 20 min | R74/R78/R82 -> 75 k RT0603BRD0775KL (C723587); R75/R79/R83 -> 208 k RT0603BRD07208KL (C861219); R73/R77/R81 stay 1.05 M. Value-only (no footprint/height/membership). Post-stress recovery +119.72 mV; max OV trip 8.216 V; UV headroom +61.89 mV. The >= 8.3 V sensor screen is agent-derived and STATIC only (transients unqualified). No sensor or CAN2 proposal adopted; 650/210 mA and 1.487 A stay conditional. The 5 V ±1% aux stays OPEN. |
| AGENTEE-492 exact-CAD RF CPW measurement contract (child of 186) | obstacle slice DONE (510 58350277, docs/probe only; Fable OBSTACLE CONFIRMED). RULING (agent-529 `8683ae8e`): 0.2032 = the PHYSICAL emitted gap, so KiCad zone fill's 0.2037 is NOT exact. Emitter feasibility slice DISPATCHED to agent-510 (25 min + Fable 15) | agent-510 | 25 + 15, then queued 120 + Fable 30 | Feasibility: native saved explicit coplanar GND primitives at exactly 0.2032 on both flanks with the 0.2032 rule kept; DRC; controls; diagonal if feasible. Queued 120-min implementation after review: nonnegative tolerance parsing, request/board identity binding, connected-component reference evidence (a floating In1 must fail), per-interval boundary certificates, regressions. The writer's 10-min declaration slice stays queued until the consumer contract is reviewed. Placement may proceed without the post-route RF checker only if RF obligations stay explicit in a valid captured stage contract; no pre-routing RF PASS. |
| AGENTEE-493 close 489 review gaps (child of 489; agent-529 `8958d1be`, `d66d0495`) | DISPATCHED to agent-524 (Cairn `417a2433`), 13:04:41Z–13:34:41Z (30 min), then the same Fable reviewer 15 min | agent-524 | 30 + 15 min | G5 Stage-A intra-block attached reservations (REQUIRED before production 489/485), G2 saved-check original binding, G3 Python/Rust blocker order, G1/G6 docs, G4 test fix. Frozen base agent-ee 93258726. NEXT on 524: the stackup repair (agent-529 `3ddc945d` item 2; 20 min + Fable 15; serialized after 493 because both touch the envelope docs; the loss tangent stays unknown and a KiCad 0.02 default is labelled a tool default). |
| AGENTEE-494 J2 vertical SMA (Amphenol 132134): joint budget, footprint, reservations, sealed penetration (child of 490; agent-529 `82f08e7a` selected option A) | DISPATCHED to agent-507 (Cairn `90bf1bd3`), 13:26:21Z–13:46:21Z (20 min), then Fable 15 | agent-507 | 20 + 15 min | Frozen input: 490 b503bf71 (on fome-mc main f6c7ec25) plus REQUIREMENTS dd6555f7. Standard 1.6 mm board kept (option B rejected). F (103,40,0) is a candidate anchor. IP67 penetration (REQUIREMENTS 66) must be concrete. The J2 change invalidates the RF endpoint/launch/cutouts. The writer may prepare A's source integration now; the canonical footprint waits for the reviewed 494. |
| agent-ee main integration 13:40–13:47Z | LANDED `1177cbc2` (479 follow-ups 259fb123 + 489 slice-1 + 449 stub fix 32b36d6c) and `87025844` (AGENTEE-493 697820be; G5 CLOSED at implementation level, agent-529 `38f1ac7e`) | — | — | Merged gates: 493 261 tests OK, docs/origins OK. The 489 landing found 26 PRE-EXISTING placer failures (copper_extent x24, packing_scope, pad_keepout), identical on pre-merge 254d259d; the 1 new error (capacity stub) is fixed. 493 follow-ups F1–F3 stay named. |
| Pre-existing placer failures (26) | DISPATCHED to agent-524 (Cairn `67efe4cc`), 13:49:46Z–14:09:46Z (20 min) + Fable 15; frozen main 87025844 | agent-524 | 20 + 15 min | Distinguish a stale monkeypatch seam, a changed refusal path, or a REAL unsafe publication; restore real coverage on the default production path; never weaken production. REQUIRED before 485. |
| AGENTEE-488c H2 (requirements revision delta) | writer slice DISPATCHED 13:49:30Z (20 min) + same Astra 15 (agent-529 `38f1ac7e` item 1) | j6b2 writer | 20 + 15 min | An explicit accepted revision delta admitting only POD-CD-STORAGE and the exact POD-ME-1 height-estimate correction (d865a76d -> 09c89ce3); regenerate revision-delta.json; positive and negative controls; then coherent production / retention / composed retain --check, with the 491 resistor change included. |
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

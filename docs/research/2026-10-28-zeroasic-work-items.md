# Local work items from the Zero ASIC research

Drafted 2026-10-28 against agent-ee
`09bd4e74bdb5383d4de1da3cfb329783d43dc30a`.
Context and pinned code examples: [research report](2026-10-28-zeroasic-autonomy.md).

These are local proposals, not published Plane tickets or implementation
dispatches. `ZA-01` through `ZA-11` are document identifiers. Existing item IDs
come from the [recovery backlog](../planning/2026-10-25-recovery/backlog.md)
and [generator roadmap](../generator-roadmap.md); their live status was not
checked because Plane is down. When it returns, reconcile with completed and
active work before amending an item or creating a child. The recovery
[execution record](../planning/2026-10-25-recovery/execution.md) supersedes the
backlog snapshot's old blanket pause language. The telemetry-pod hold remains.

P0 means a correctness or first-autonomy prerequisite; P1 means the next bounded
extension; P2 means an experiment whose benefit remains unproved. Dependencies
below supplement the existing recovery dependencies; they do not cancel them.
Each slice ends in observable behavior through a supported entrypoint.

| Local ID | Deliverable | Priority | Existing work to extend | Local prerequisites |
| --- | --- | --- | --- | --- |
| ZA-01 | Portable current build with attributable negative cases | P0 | AGENTEE-519/515/524; generation benchmark AGENTEE-69 | Existing runtime and typed qualification |
| ZA-02 | Acquisition brief to typed inputs and a generated package | P0 | AGENTEE-525 | ZA-01 reference |
| ZA-03 | One automatically composed regulator/ADC/interface design | P1 | AGENTEE-525; block work AGENTEE-89/90 | ZA-02 |
| ZA-04 | One validated part alternative after a catalog change | P1 | AGENTEE-525; parts stage AGENTEE-79 | ZA-03 |
| ZA-05 | Final functional-group gate on the ordinary build path | P0 | AGENTEE-479 | Existing supported intent and runtime |
| ZA-06 | One congested board solved with permitted underside placement | P1 | AGENTEE-489 | ZA-05 |
| ZA-07 | A failed route produces one constrained placement repair | P1 | AGENTEE-516 | ZA-05; ZA-06 only for side-changing repair |
| ZA-08 | Matched experiment for a bounded routing tournament | P2 | AGENTEE-513/527/516 | ZA-01, ZA-05; qualified route options |
| ZA-09 | A DC-drop failure drives one verified copper repair | P1 | Existing DC-path work AGENTEE-13; AGENTEE-517 | ZA-01; qualified DC-path model |
| ZA-10 | Generated candidate enters independent review and repair | P0 for engineering-complete claim | Existing campaign/review; AGENTEE-83/525 | ZA-02; existing qualification contracts |
| ZA-11 | Frozen brief-to-board evaluation with intervention counts | P1 | AGENTEE-69/515/517 | ZA-02/03/04/05/10 |

## ZA-01 — Make the current complete build a portable reference

Extend [recovery_runtime.py](../../scripts/recovery_runtime.py), the captured
[input manifest](../../fixtures/recovery/inputs.json) and existing M1 checks.
A full build harness already exists. The gap is a documented, wired native
regression that a fresh operator can run with permitted input access.
Use the existing acquisition reference; an original redistributable mini-board
may supplement it if some inputs cannot be published. A substitute must be
named separately and cannot inherit acquisition acceptance.

Use UMI's valid/injected-fault pairing to audit current acceptance coverage.
Reuse the wrong ADC tie, reversed diode, modified CPL and resume-drift cases;
add only missing public-path protection. Bind each expected defect to a
requirement, checker, candidate and diagnostic.

Acceptance:

- [ ] A clean supported runtime executes all seven public build stages with
  pinned source/router inputs and produces the expected qualification record.
- [ ] The independent evaluator catches the selected functional mutation even
  when ERC/parity pass; changing exported CPL bytes fails the package gate.
- [ ] Required input drift refuses resume; an interrupted valid run resumes
  through the existing journal. Missing native tools are unavailable/incomplete,
  never a skipped green release.
- [ ] The manifest, commands and result are discoverable from the normal test
  procedure. The strict native job actually invokes the complete workflow.

First cap: one current control, one existing electrical negative, one package
negative and the existing resume probe. Retain failures and follow host
admission. Completion establishes a reference workflow, not prose synthesis.

## ZA-02 — Generate one acquisition variant from an engineering brief

Extend AGENTEE-525 with a supported preparation workflow. Its inputs are a
brief, the captured acquisition/reference library, a declared manufacturing
profile and finite agent/tool budgets. The first brief changes one load or
interface requirement within the qualified family. It must not require the
caller to hand-author circuit, layout, groups or requirement/check JSON.

Reuse the dialog, spec capture, requirement-map compiler, parts helpers and
build schemas. Produce the typed build plan and all required engineering
inputs, with source locators, chosen values and rationales. The generator may
propose a missing decision; it cannot silently turn an unanswered question
into a user constraint. Requests outside supported source/model applicability
return the precise unresolved condition.

Acceptance:

- [ ] A fresh agent/operator can start with the brief and captured context,
  generate the bundle and pass all required automated file obligations through
  the public build, without a private preparation script or hand-editing the
  PCB. Remaining judgment obligations are explicitly listed; failed checks
  cannot satisfy this criterion.
- [ ] Evaluator-owned assertions verify the changed behavior against the
  original brief, including bounds and relevant pin/interface compatibility.
  Every required clause retains its proper evidence method.
- [ ] A missing required supply/interface decision prevents dependent
  generation; a contradictory requirement produces a bounded refusal.
- [ ] Changed prose changes derived input identity and invalidates affected
  evidence. Report every human intervention and unresolved judgment.

First cap: one control brief and one changed brief, at most three preparation
attempts per brief. Use the same declared downstream build budget. A pending
judgment remains pending; ZA-10 supplies actual review integration. Combined
completion requires `file_release: pass` and a passing requested engineering
review, with no unresolved required findings.

## ZA-03 — Compose one design from qualified block instances

Use EBRICK's shared simulation/implementation definition as the pattern.
Capture and qualify the acquisition regulator, ADC and connector/interface as
entries using the existing block library/adaptation machinery, then instantiate
them. The reusable machinery exists; this research has not established that
these three roles already have qualified library entries. Bind each instance to its
parts, pin mapping, rail/load limits, model applicability, decoupling/locality
and verification requirements. Let the changed brief select a supported
parameterization. Qualify the composition at its electrical boundaries.

This slice completes an actual generated variant. A new library schema,
retrieval index or collection of schematic fragments alone is insufficient.
Keep the existing block-first design policy and explicit unsupported hierarchy
limits; require fresh checks after adaptation.

Acceptance:

- [ ] One compatible composition produces a native schematic and board and
  passes all required automated file obligations through ZA-02. Remaining
  judgments are listed explicitly. Combined completion with ZA-10 requires
  `file_release: pass` and passing requested engineering review.
- [ ] Incompatible rail/pull-up voltage or insufficient regulator current
  refuses before placement, naming the conflicting contracts.
- [ ] Implementation and simulation bind the same chosen instance, while
  independent topology/interface assertions remain evaluator-owned.
- [ ] A changed block revision, missing physical pin or stale applicability
  receipt cannot reuse the previous result.

First cap: the three existing acquisition roles, one valid composition and two
incompatibility cases. Broader block discovery follows demonstrated coverage.

## ZA-04 — Regenerate with one compatible part alternative

Extend the existing part selector and captured catalog path. Give the
acquisition preparation two frozen catalog snapshots: the initial choice is
available in one and unavailable in the other. For one role, select an
explicitly qualified alternative and regenerate all affected inputs. Consider
ratings, package/pin compatibility, model applicability and assembly binding;
price or stock alone cannot establish suitability.

Acceptance:

- [ ] The alternative reaches fresh electrical/native/package checks with
  corresponding symbol, footprint, BOM, CPL and source evidence.
- [ ] A stocked but electrically incompatible alternative is rejected with
  a specific reason; no available qualified choice yields an unresolved part.
- [ ] Changed pins or package geometry invalidate the relevant circuit,
  placement, routing and package evidence. Unaffected evidence is reused only
  through the existing applicability rules.
- [ ] The run records catalog identity and the reason for selection; it makes
  no purchase and does not label snapshot stock as current availability.

First cap: one part role, two qualified choices, two catalog snapshots and one
incompatible control. General allocation/backtracking is outside this slice.

## ZA-05 — Enforce functional groups after the final native save

Complete the normal-path portion of AGENTEE-479. Adapt the existing
[block contract](../block-contract.md) and final native predicates into the
prepared placement job. Retain complete block/fixed/shared allocation and
declared pin relationships through search, legalization and routing feedback.
Keep sourced hard bounds distinct from engineering advisories. Do not derive
a universal radius or a passing threshold from the candidate under test.

Acceptance:

- [ ] A routed, DRC-clean board with one violated required support relationship
  fails final acceptance using reloaded native coordinates.
- [ ] Supply and return legs are both measured where required. Missing
  geometry or incomplete membership cannot produce a pass.
- [ ] A legal control passes, and final reports identify membership, face,
  spread, pin distances and unresolved exceptions.
- [ ] A late component move invalidates the affected result; checking the
  planned coordinates or a colored rendering cannot satisfy this gate.

First cap: one complete group control and one deliberate final-coordinate
mutation through the public path. Reuse the accepted 479 contract before
designing another predicate. Keep broader side support in ZA-06.

## ZA-06 — Use permitted underside area on one congested design

Extend AGENTEE-489 with a complete side-aware placement case. Carry captured
face eligibility, through-hole occupancy, component bodies and mechanical
reservations through prepared search and final-native grading. Move only
eligible components/blocks; maintain the group and pin constraints from ZA-05.

Acceptance:

- [ ] The same frozen congested board gains a valid routed placement by using
  eligible underside space, with final group/locality checks passing.
- [ ] Forbidden face, through-hole collision and sourced keepout controls fail
  even if their 2D top-view bounding boxes appear acceptable.
- [ ] Flipped footprint pin identity, pad geometry and manufacturing orientation
  survive through the final package assessment.
- [ ] An impossible mechanical condition remains an explicit failure without
  weakening its source rule or changing the board envelope.

First cap: one congested board, its fixed-side control and the three named
negative cases. Compare under the same rules and total native budget. Equal
top/bottom component counts are not an objective.

## ZA-07 — Repair placement using one observed routing bottleneck

Extend the existing route shortlist and bounded copper-repair controller in
AGENTEE-516. Use a failed route to propose one concrete movement of an eligible
functional group that opens a channel. Construct a new candidate with the
existing writer, then rerun routing and every affected final predicate.
Side-changing repair additionally depends on ZA-06.

Acceptance:

- [ ] A frozen case that exhausts its original shortlist completes after the
  bounded movement, with the same UUIDs, netlist, rules and required intent.
- [ ] A blocked fixed interface terminates with the observed unmet constraint;
  it cannot be solved by deleting a rule or moving a locked component.
- [ ] The original candidate and all unsuccessful attempts remain inspectable;
  new checks bind the saved repaired board and its exported package.
- [ ] Timeout/cancellation covers descendants and leaves an honest incumbent
  status. Total elapsed time includes refill and final verification.

First cap: one hypothesis, at most two new placement candidates and one routing
trial per candidate within the declared shared budget. This is a closure
demonstration, not evidence of general router superiority.

## ZA-08 — Test a small routing tournament against the current policy

Use DeepRoute's strategy search and SiliconCompiler's eligibility-before-score
selector as inspiration. First determine which strategy parameters the pinned
kicad-route adapter already supports. Compare a bounded deterministic strategy
tree with the existing shortlist policy; do not add an unsupported router
option merely to populate the experiment.

Acceptance:

- [ ] Candidate keys bind input board, effective constraints, runtime and all
  strategy parameters. Equivalent configurations run once; failed attempts
  and scores are retained. Tie breaking and stopping are explicit.
- [ ] A shorter route with a required violation loses to a valid route.
  Missing metrics or no eligible candidates cannot produce a winner.
- [ ] The selector uses stable physical units/bounds for progress. Relative
  per-batch normalized scores do not establish convergence across batches.
- [ ] Compare identical inputs, constraints, hardware allocation, total work
  caps and final grading. Report accepted outcomes, opens, violations, time
  and CPU use separately; preserve the current policy if benefit is absent.

First cap: two policies, two frozen boards, two declared seed replicates per
board (eight jobs total), at most six route trials per job under the same
configured total budget. Reserve final grading time, following AGENTEE-527.
Choose strategy settings on one board and assess transfer on the other.
Stop on non-comparable constraints or a preservation regression.

## ZA-09 — Close one DC-drop feedback loop

Use the existing qualified DC-path implementation on a declared load path with
captured material, temperature, current and endpoint assumptions. Start with a
routed board that violates its specified drop bound. Permit a copper-width or
routing-geometry repair within captured rules, regenerate, and rerun native and
electrical checks. A changed routing recipe requires explicitly versioned build
inputs while preserving the original requirements and sourced limits;
verification rules remain immutable within the repair campaign. This tests
whether analysis can drive an actual correction.

Acceptance:

- [ ] The initial failure and repaired margin are measured from each saved
  board, with supply and return coverage appropriate to the requirement.
- [ ] An independent analytical/simple native control checks the expected
  direction and numerical tolerance of the model.
- [ ] A disconnected conductor, missing material/model prerequisite or stale
  geometry yields defect/unknown as appropriate, never an invented voltage.
- [ ] The repaired candidate still passes required clearance, topology,
  placement and package gates. The report retains the model's limitations;
  nominal copper analysis does not become full thermal or SI qualification.

First cap: one path family, one failing control and at most two repair attempts.
Stop if the needed physical effect is outside the existing model. Field solvers,
generic return-path synthesis and co-simulation need separate justified cases.

## ZA-10 — Join generated output to independent review and repair

Compose the generated candidate and scope with the existing review/campaign
engine. A requested engineering-complete outcome must run the applicable
independent reviewers and adjudication, retain source/candidate-bound findings,
and send accepted defects through the existing bounded writer loop. File-only
builds keep their narrower existing behavior and qualification dimensions.

Acceptance:

- [ ] An independently detected functional defect that passes ERC/DRC leads
  to a bounded repair, fresh affected checks and renewed independent evidence.
- [ ] Initial reviewers remain isolated from each other's findings. The
  implementation agent cannot author its own concurrence or acceptance result.
- [ ] Reviewer unavailability or exhausted budget produces an incomplete
  engineering-review outcome, while retaining separately valid file evidence.
- [ ] A repair cannot weaken requirements or sever their source mappings.
  A changed candidate reopens applicable judgments and package verification.
- [ ] The final report explains the engineering-review outcome without
  relabeling it as supplier confirmation or physical validation.

First cap: one supported defect/control pair, at most two repair iterations
and declared model/native budgets. Scripted tests qualify orchestration only;
a live independent review is required for the live-review claim.

## ZA-11 — Measure autonomy on a frozen requirements series

Extend the existing frozen generation benchmark, drawing on LogikBench's
parameterized designs and explicit functional verdicts. Freeze the evaluator
before running the generator. Keep its expected requirements, bounds and
negative controls out of the writer's mutable scope. Start with acquisition
control, a changed load/interface brief, a qualified part-alternative case and
an intentionally unsupported or contradictory request.

Acceptance:

- [ ] Supported cases start from brief plus captured catalog/block/fab context
  and reach the expected file and engineering-review states with no hidden
  edits. The unsupported case returns its specific unmet condition.
- [ ] Record per-case completion, unresolved requirements, manual interventions,
  electrical margins, native findings, model/tool identities, wall time and
  final artifact hashes. A missing result stays missing in the denominator.
- [ ] Include an unseen supported parameter variant; do not use the evaluator
  to tune the recipe during the measured run.
- [ ] A held-out mutation that weakens a generated check cannot fool the
  evaluator. Existing evidence seals and source bindings remain enforced.
- [ ] Report the limited supported domain and sample size. Do not extrapolate
  a few successful runs to arbitrary board design or physical success.

First cap: four frozen briefs, at most two attempts each with common declared
budgets. Reuse equivalent existing benchmark machinery. Expansion follows a
specific uncovered requirement, not a target number of examples.

## Reconciliation when Plane returns

Attach these proposals to the existing work where they describe the same
observable outcome. Check current code and integration evidence before adding
acceptance criteria: typed claims, input drift refusal, block contracts,
ordinary routing retries and fabrication exports already exist. A new child
item is warranted only for a distinct remaining deliverable.

The first useful implementation is ZA-02 on the validated reference from
ZA-01, while ZA-05 completes its existing correctness work. Tournament and
mixed-signal research do not block that path. Broader pod execution, supplier
uploads, orders and physical qualification keep their existing authority and
evidence requirements.

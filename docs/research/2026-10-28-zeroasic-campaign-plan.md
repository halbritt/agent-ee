# Requirements-to-board implementation campaign

Proposed 2026-10-28 against `2bd25070819113240148ef0196e73ba08e196a8a`.
[Owner request](2026-10-28-zeroasic-prompt.md) ·
[Research](2026-10-28-zeroasic-autonomy.md) ·
[Detailed work items](2026-10-28-zeroasic-work-items.md)

This plan turns the research into an ordered campaign with acceptance gates.
Plane reconciliation and implementation are future execution steps. No Plane
items were created or live statuses checked while preparing this document.

## Owner scope amendment — 2026-10-29

Direct owner instruction: **"remove the ESP-Prog requirements"**.

ESP-Prog is excluded from this campaign's deliverables, dispatches and acceptance
gates. G4 requires acquisition-applicable qualification under AGENTEE-515, not
closure of that broader M1 item. Historical ESP-Prog results remain retained;
their exclusion is not a passing qualification. This amendment supersedes older
campaign requirements, dependencies and goal wording that include ESP-Prog.
Acquisition engineering, negative/resume checks and the four-case acceptance
series remain required. The owner subsequently instructed **"do that and then
resume work"** on 2026-10-29, authorizing execution under this amended scope.

## Destination and success criteria

The first campaign delivers autonomous preparation and engineering acceptance
within the captured acquisition-board family. The caller supplies requirements,
qualified reference/catalog context and a manufacturing profile. Agent-ee
produces the circuit, native KiCad project and verified manufacturing files
through its supported interfaces. The caller does not author intermediate
circuit, layout, grouping or requirement/check JSON.

A supported result counts as complete only when:

- All seven public build stages complete, with source and candidate identities
  preserved and `file_release: pass`.
- Independent electrical assertions establish the requested behavior. Required
  ERC/DRC, connectivity, final-coordinate group/mechanical and package checks
  pass; there are zero opens and no unresolved required engineering findings.
- Required candidate-specific judgments have independent concurrence. The
  requested full engineering review passes for the same final candidate.
- Editable native files, Gerbers, drills, BOM and CPL are retained with hashes,
  evidence, limitations and commands another operator can run.
- The run stays within its declared budgets and contains zero manual design
  edits or undocumented preparation steps. Agent-driven repair is allowed
  within the declared limits and is recorded.

Supplier confirmation and physical validation retain their separate statuses.
This campaign establishes a software file-release and engineering-review
result. Manufacturing and bench success require later evidence. The existing
telemetry-pod hold remains in force.

The finite acceptance series is:

| Case | Required outcome |
| --- | --- |
| Acquisition control | Complete accepted package from its brief |
| Unseen supported load/interface variant | Complete accepted package satisfying independently fixed changed bounds |
| Qualified alternative part | Complete accepted package after a captured catalog makes the initial choice unavailable |
| Unsupported or contradictory request | Specific unresolved requirement, no fabricated assumption or release claim |

Allow at most **two complete generation attempts per case**. All three supported
cases must pass within that cap, and the unsupported case must refuse correctly.
Also retain the acquisition-applicable M1 qualification, attributable negative
and resume gates. Full closure of broader M1 work is not a campaign dependency.
This is a small domain demonstration; report the sample size and supported envelope.

## 1. Reconcile Plane, then create the campaign map

When execution starts, read current repository and tracker evidence before
dispatching work. Follow the [recovery choices](../planning/2026-10-25-recovery/recovery-plan.md)
and [campaign process](../campaign-process.md). An old unassigned/pause snapshot
or a historical green run does not establish current readiness. The accepted
architecture in [decisions](../decisions.md) remains the implementation base:
`build_workflow`, prepared Rust placement, the owned kicad-route adapter and the
existing review/campaign engine.

1. Reconcile AGENTEE-512/515/519/479/525 and the remaining mappings below with
   current code, integrated commits and qualifying evidence. Record the exact
   remaining behavior for each item. Reuse completed work when its evidence
   applies; reopen only a demonstrated gap.
2. Create or reuse outcome epics and their child work items. Preserve existing
   ownership and parentage where appropriate; use links or module membership
   for shared prerequisites. Avoid duplicating an existing deliverable under a
   new Zero ASIC title.
3. Give every new item a stable local key, observable result, prerequisites,
   supported entrypoint, positive and negative acceptance cases, immutable
   inputs, evidence destination and finite budget. Copy the applicable criteria
   from the detailed work list and add the gate below that consumes its result.
4. Reconcile/create the nodes first, then wire prerequisites. Read back IDs,
   parent/group membership, dependencies and state. Save the mapping and
   execution evidence under a dated directory in `docs/planning/`.

| Outcome epic | Local work | Existing items to reconcile | Epic exit evidence |
| --- | --- | --- | --- |
| Dependable reference and final geometry | ZA-01, ZA-05 | 512, 519, 520, 521, 479, 523, 524, 515, 526 | Current acquisition qualification and release evidence, including attributable negatives and resume |
| First brief produces an independently accepted board | ZA-03a, ZA-02, ZA-10 | 525, 89/90, 83 | Brief-only preparation followed by file release and full independent review |
| Adaptation works without manual design edits | ZA-03b, ZA-04, ZA-11 | 525, 79, 69, 515/517 | Frozen four-case series meets the success criteria above |
| Physical and electrical feedback repair a named failure | ZA-06, ZA-07, ZA-09 | 489, 516, 13, 517 | Each selected extension closes its own frozen failure with all affected gates passing |
| Routing strategy experiment, optional | ZA-08 | 513, 527, 516 | Comparable results and an evidence-backed adopt/retain decision |

Numbers in this table use the `AGENTEE-` prefix. Epic names are proposed outcome
groups, not claims that those native epic objects already exist.

Use the installed Plane CLI conventions for ordinary items, including stable
`external_source`/`external_id` identities when creating them. Check the
available client capabilities for native epic types and dependency relations.
At drafting time this host has no `plane` binary or Plane connector; the known
CLI/API surface does not supply every epic/relation operation. Do not invent
commands. If native wiring is unavailable, retain the local dependency table
and explicitly record pending tracker wiring; ordinary parent items or modules
can provide the visible grouping. Do not report native edges as created until
readback confirms them. The local plan remains usable while Plane is down.

## 2. Implement in this order

`ZA-03` has two slices to remove a dependency cycle: **03a qualifies the three
reference roles before preparation depends on them; 03b automates selection
and composition after the preparation interface exists.** ZA-10 can be developed
against a fixed candidate before ZA-02 finishes.

| Gate | Work and permitted parallelism | Acceptance to advance |
| --- | --- | --- |
| G0 — Freeze the first delivery contract | Reconcile tracker/code; inventory inputs, runtime, current failures and supported acquisition envelope. Name coordinator and independent verifier. | A versioned campaign record fixes cases, source/rule authority, evaluation custody, numeric budgets and evidence locations. Credentials/tools/inputs pass preflight. Remaining gaps have owners and dependencies. |
| G1 — Establish dependable inputs and checks | ZA-01 current reference; ZA-05 normal-path final group gate; ZA-03a capture and qualify regulator, ADC and interface roles. Separate owners may work in parallel after shared contracts are fixed. | Acquisition reference runs through the public path; known electrical/package negatives and resume behave correctly; final native group mutation fails; qualified block entries state applicability and reject the selected incompatible boundaries. |
| G2 — Complete the first autonomous loop | ZA-02 consumes G1's reference blocks. ZA-10 integrates actual review/repair into existing qualification, initially using a fixed candidate. Integrate both for the first brief. | A fresh run starts from the control brief, prepares every required input, completes all seven stages and meets the full supported-result contract. A functional defect that survives ERC/DRC is independently detected and repaired within budget. |
| G3 — Prove controlled adaptation | ZA-03b parameterized composition, then ZA-04 alternative selection. Prepare ZA-11's evaluator independently. | A supported development variant and the catalog-change case regenerate affected inputs/evidence and meet the same contract. Incompatible rail/current/part cases refuse. No manual candidate edits are needed. |
| G4 — Accept the core campaign | ZA-11 frozen four-case evaluation; close acquisition-applicable qualification obligations under AGENTEE-515 on the release revision. | Three supported successes, one correct refusal, no manual design edits, complete accounting, current acquisition qualification, independent verification and a reproducible release record. |
| G5 — Extend one capability at a time | ZA-06 side/mechanics, then side-changing ZA-07 repair; same-side ZA-07 needs ZA-05. ZA-09 needs a qualified DC model. Select a concrete blocked case for each. | Each extension passes its detailed positive/negative cases and preserves the core campaign's affected guarantees. Completion of G4 does not depend on these extensions. |
| G6 — Decide whether tournament search helps | ZA-08 matched experiment after correctness and route options are qualified. | Required gates remain intact; report accepted outcomes and total cost. Keep the current policy if benefit is absent or comparison is invalid. |

Preparation development may proceed once its runtime/input/acceptance contracts
are stable. **G4 requires acquisition-applicable qualification; full M1 closure
is outside this campaign.** Shared implementation defects that affect acquisition
still require correction. Record the affected acquisition consumer precisely.

The first implementation dispatch should reconcile the reference harness and
missing normal-path protection in ZA-01/05, while a separate builder qualifies
ZA-03a's existing acquisition blocks. Then deliver the G2 public path before
starting broader discovery, new solver integrations or tournament search.

## 3. Bind judgments, review and package evidence correctly

The current package path runs checks-only review. ZA-10 must provide a supported
way to bind actual independent review into the existing qualification result;
writing a judgment witness alone does not populate `engineering_review`.
Use the existing writer, isolated reviewers, adjudication and check machinery.
A manually set pass field or a second success boolean cannot discharge this work.

For candidate-specific judgment authoring, the current
[build contract](../build-workflow.md#authored-judgments-and-package-records)
supports this sequence:

1. Capture the brief, datasheets, extracts, extraction provenance and all
   applicability sources before the run. Declare the top-level `judgments`
   directory in `agent-ee.build-plan/2`, and map the required record paths in
   `agent-ee.requirement-map/2` relative to it. The directory may initially be
   absent.
2. Build through `artwork`. Obtain judgments and independent verifier witnesses
   for that captured source and candidate in the declared directory.
3. Resume into the **first** package attempt, which freezes the judgments.
   Changed or added judgments after any package attempt, including a failed
   attempt, require a new run. Do not edit sealed evidence.
4. Bind full review and adjudication to the final candidate, scope and artifacts
   through ZA-10. Any accepted repair creates fresh candidate-bound checks,
   judgments and package evidence as required by the existing invalidation
   rules. Accept only the final matching evidence set.

A review repair must re-enter the supported production path through updated
generation inputs or an explicitly receipted writer stage. Fresh checks on an
externally edited PCB alone cannot satisfy the seven-stage generation claim.
ZA-10 must preserve that stage chain as well as the review evidence binding.

The verifier's independence requires a separate trusted execution. Distinct
identity strings in a JSON witness are insufficient. Reviewer unavailability
or budget exhaustion leaves engineering review incomplete; it does not erase
separately valid file evidence or establish campaign success.

## 4. Protect the evaluation and count every outcome

Reuse the [generation benchmark](../generation-benchmark.md), its input pins,
private evaluator and authenticated series contracts. G0 fixes the evaluation
protocol and case families. Before the measured G4 run, the independent
verifier fixes exact task inputs, expected outcomes and electrical bounds,
retaining task-pin digests outside the writer's mutable store. The generator
receives only the public brief and permitted context. Audit that context for
answer leakage; exact-copy detection alone cannot detect paraphrased answers.

Freeze the implementation/runtime, catalog, manufacturing profile, model/tool
configuration and repair protocol for the series. Run credential/tool admission
before entering a counted attempt. Once an attempt starts, timeouts, provider
failures, missing outputs and exhausted budgets remain in the denominator.
Never replace them silently with a later successful run.

The existing benchmark executes local scripts in an isolated, networkless
environment. Any adapter needed for live model execution must preserve its
protected evaluator and retained execution evidence; the current harness is
not evidence that live inference is already integrated. Direct diagnostic
evaluation of a supplied board cannot substitute for a real generation run.

ZA-11 must also qualify an independent refusal evaluator. Current board tasks
treat missing deliverables as failure/unknown and ignore generator `result.json`
claims. Require a structured refusal bound to the exact unsupported clause and
captured context, verify its reason independently, and reject an accompanying
release claim. Keep this outcome separate from board completion; the existing
board verdict alone cannot supply the four-case campaign result.

Configure the benchmark with `--attempts 2` explicitly; its default is three.
Its current `--timeout` applies separately to generation and evaluation within
each attempt. ZA-11 must enforce one enclosing case deadline and work counter
across preparation, native build, review, evaluation and retries, with reserved
grading time. Acceptance includes a forced exhaustion case proving that neither
a retry nor a child process resets that budget.

After trusted trials, the benchmark's `freeze` command seals selected reports
into an authenticated series using independently retained task pins. This is
the **result seal**, distinct from fixing tasks before generation. Retain its
manifest digest externally, then aggregate the exact selected report set using
the manifest and expected digest. Keep both attempts and the per-case selection
policy in the campaign ledger; a passing selected report must not hide retries.

Report:

- Supported completion **n/3**, correct refusal **n/1**, and total expected
  outcomes **n/4**. A refusal is never a generated board.
- First-attempt completion separately from completion within two attempts.
- Manual interventions, unresolved obligations, repair count, electrical
  margins, native findings, model/provider calls, elapsed time and native work.
- Every attempted run and its final status, candidate and artifact identities.

Use development variants to tune the implementation. If the unseen case's
details are used to repair the generator, retain its failed result and create a
new independently frozen evaluation version with a replacement unseen case.
Developer changes during a measured series likewise require a new version.
Do not claim success against a holdout that became a development fixture.

## 5. Dispatch, integration and stopping rules

Use one coordinator/integrator, at most two implementation builders and one
independent verifier. Start with one builder on the critical path; add the
second for an independent deliverable such as qualified block capture. Assign
one writer to each shared contract/module until its interface is settled.
Each builder uses an isolated branch/worktree. Integrate serially and verify
the integrated revision before dependent work consumes it.

Every dispatch includes:

- One holder, model and admitted host; immutable input and base revision.
- A bounded deliverable, dependencies, allowed changes and public acceptance
  path, with positive and attributable negative cases.
- Numeric wall-time, model-call/token and native-work limits, plus a reserved
  final-grading allowance. Calibrate these once from the ZA-01 reference before
  dependent native dispatch; store actual numbers in the campaign record.
- Evidence destination and conditions for success, incomplete, defect and stop.

The work list's experiment caps are ceilings. The G4 cap of two complete
attempts per case overrides ZA-02's development allowance of three preparation
attempts. Nested preparation, placement, route and review retries consume the
same enclosing budget; they cannot each reset the clock. Retain failures when
the budget expires. Extensions need a new recorded dispatch and reason.

A builder's completion means ready for verification. The coordinator marks the
work accepted only after independent evidence applies to the integrated commit.
Map these concepts to the project's actual Plane states rather than assuming
new state names. Keep implementation completion, accepted generated artifact
and epic completion distinct in the item description and evidence ledger.

Run focused checks for each changed path. Reuse one authoritative full gate for
the same source/runtime/input/test contract; workers should not independently
repeat the same expensive suite. Re-run affected gates after material changes,
and satisfy the existing strict/native release requirements at G4. Missing
native coverage is an incomplete gate, not a skip that passes.

Classify a failure as implementation defect, missing input, unsupported domain,
unavailable infrastructure or exhausted budget. Fix within the current slice
when possible. Two investigations of the same boundary with no new evidence
trigger a focused blocker and replanning of its consumers. Do not respond by
weakening the brief, expanding the architecture or repeatedly increasing caps.
Routine engineering choices remain delegated under the existing decisions;
only genuinely owner-reserved facts/actions need the owner's answer.

## 6. Close with a reproducible release

The coordinator publishes one committed release record containing the source
and runtime pins, Plane/local mapping, accepted evidence, all counted outcomes,
commands, artifact locations, known limits and remaining supplier/physical
obligations. A fresh verifier who did not author the generator or evaluator must
reproduce the documented path from a clean checkout and permitted inputs,
without private scripts or coaching from the builder.

Close the core campaign only after G4 passes. If it does not, retain a bounded
failure report naming the exact unfinished gate and next work item. Close an
extension separately when its own evidence passes. Preserve the broader
requirements-to-board objective for the next campaign, chosen from a specific
unsupported requirement or measured failure in this one.

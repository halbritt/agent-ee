# Acquisition case adapter: required parent boundary

Source diagnosis at `eb005bee29e53900eb45a7d024fef966c63b6117`.
This is a proposed interface, not an implemented generator or execution grant.
The private four-case tasks and expected outcomes remain evaluator-owned.

The existing `generation_benchmark.run_task` cannot execute the acquisition
acceptance contract unchanged. A through-artwork checkpoint followed by review
after `run_task` returns does not qualify. Neither does a generator-authored
`accepted` flag, seven directories, or a copied qualification summary.

## One case, existing accounting

The existing case start, absolute productive cutoff, final deadline,
cancellation handle, attempt cap, phase-admission credits and final grading
reserve remain authoritative. Every attempt, launch failure, seat refusal,
partial record, timeout and failed evaluation remains in `trial.json` and the
attempt tree. No authoring, review, package run or retry occurs after the case.

The smallest useful integration is a fixed, coordinator-owned acquisition
completion call between generation and grading **inside** `run_task`. It is not
a caller-provided shell command or writer-selected adapter. Its public inputs
are the attempt's exact brief, an independently frozen public preparation
context, and that attempt's owned paths. It receives the existing absolute
productive cutoff and cancellation handle. It receives no task root, evaluator
path, oracle, expected intent, task pins, rule weights or expected refusal.
Its code/context/profile hashes must be part of the authenticated trial binding
and series freeze, and must be checked again during report replay.

The existing `admit` function must treat this completion as productive work,
reserve a grading credit, debit its work credit before launch, and never refund
it. With one generation admission, one completion admission and one grading
admission, two complete attempts require six phase credits; this is a phase
counter, not a native-process or token counter. The phase must not receive a new
wall-clock allocation. Failure becomes an attempted failure, not a replacement
invocation outside the ledger. Defaults for unrelated generation tasks should
retain their current meaning.

## Concrete production sequence

1. The untrusted generation sandbox emits the public acquisition request. It
   remains networkless, with no credentials, reference oracle or peer workspace.
   Existing `previous/` and `feedback.json` remain its only repair inputs.
2. The trusted completion phase calls `acquisition_prepare.prepare` using the
   frozen public reference plan, source additions, catalog closure and explicit
   review profile. Preparation is charged to the same case. A real
   `PreparationRefused.report` becomes the ordinary `refusal.json`; the evaluator
   independently compares it with its private contract. Runtime/authentication,
   source-custody or tool failure must not be relabelled as a correct refusal.
3. The trusted phase calls the ordinary public build through artwork, retaining
   its complete run. This is an intermediate checkpoint only. Running native
   build steps in this trusted parent composition avoids putting all stage
   sandboxes inside the untrusted generator's additional bwrap boundary.
   Each production stage still owns its existing isolation and read-only inputs.
4. A declared author seat produces actual `agent-ee.engineering-judgment/1`
   records from the generated candidate, public sources and judgment briefs.
   An independent declared verifier produces actual
   `agent-ee.judgment-verification/1` witnesses. Both calls use the existing
   reviewer launch/preflight/subscription machinery, the same deadline and
   cancellation. The evaluator cannot be either seat. No conversion of review
   findings or check statuses into authored testimony is allowed.
5. Place only these genuinely authored records in the prepared plan's declared
   late-bound judgment directory. Resume the **same** build with the ordinary
   default `through='package'`. The existing package stage freezes the judgment
   inventory and runs its captured-profile full independent review through
   `retained_run.review_candidate`. It already supplies the package engineering
   review; an additional detached `build review` is unnecessary and cannot
   retroactively repair a failed package review.
6. Consume the original build's production qualification/custody verifiers.
   Require all seven ordered `retained_run.STAGES`, a completed package, matching
   source/candidate/package identities, genuine required judgments and the
   requested full review for that package. Existing qualification owns the
   release decision. There is no adapter-owned acceptance boolean.
7. Copy only the private task's declared deliverables from that exact package
   candidate into ordinary evaluator inputs; pass their actual hashes and the
   retained package identity/evidence binding to `generation_benchmark.evaluate`
   **before** the case deadline. Existing candidate capture, baseline checks,
   evidence seal, report replay, series freeze and aggregation remain the
   independent oracle. Bind the complete producer/package evidence as well as
   native deliverables; deleting that evidence must invalidate replay.

Do not require supplier/physical dimensions to pass. The owner keeps those
obligations separate. A software file-release/full-engineering result must keep
supplier and physical limitations visible. An operational/resume obligation is
required where the actual typed map declares it; do not manufacture a failed
native run just to obtain operational credit.

Candidate IDs from different scope captures need not be equal: adding evaluator
inputs or `.generation/` changes the candidate identity. Establish continuity
with exact native file hashes, sealed stage trees and their actual scope and
candidate bindings. Do not equate every review/check candidate ID by fiat.

## Exact current blockers

* `run_task` admits only `generation` and `grading`. It passes no completion
  callback, deadline or profile to a trusted acquisition phase. Its sandbox
  intent sees only public generator/prompt, `/work`, `/app` and system files.
  `executor.sandbox_argv` clears the environment, hides host home and `/run`,
  supplies a private `/tmp`, and disables network. Switching that entire intent
  to Host, mounting the evaluator or sharing credentials with the writer would
  weaken the boundary; none is an implementation of this contract.
* `acquisition_prepare` reads repository assets via `ROOT`, including
  `fixtures/recovery/inputs.json`, the typed overlay and supplement/power pins.
  The ordinary generator view's `/app` is only `src`; embedding context alone
  does not make those hard-coded paths available. Trusted parent preparation
  from the pinned production checkout uses the existing API without inventing
  global configuration or relocating captured native receipts.
* `qualification.freeze_judgments` and `admit_judgment` freeze/validate authored
  testimony; they do not author it. `campaign.run_campaign` runs independent
  candidate reviews and optional implementation turns, but does not produce
  these per-clause testimony/witness records. An explicit judgment-seat adapter
  is still missing. Historical R5 agent-authored records cannot fill it.
* Existing `evaluate` grades declared native outputs against its private
  baseline. It has no production-package provenance input or replay rule.
  Adding a loose post-trial summary would leave its authenticated series unable
  to establish the required package/review continuity.
* A build produced under `/work` inside the generator cannot simply be moved
  and resumed on the parent: placement configuration and retained native
  receipts contain concrete absolute paths. Keep the native build in one owned
  parent path domain from its original launch through package and grading.

These are source-established blockers, not measured runtime failures. No
truthful complete adapter can be assembled solely by supplying a new
`generator.py` to the unchanged runner. No production implementation is claimed
by this document. The necessary change is the narrow trusted completion seam,
its genuine judgment author/verifier integration, and package-binding replay in
this existing runner, rather than another controller or acceptance framework.

## One native admission experiment, after source review

Run one disposable **non-G4 public acquisition control**, one attempt and one
original case clock, through the proposed existing-runner integration above.
It must include preparation, the actual native build, both subscription judgment
seats, the package's captured-profile full review, file-release qualification,
and existing independent evaluator capture/grading. Use the supported
`native-admit`/`ae-ci-exec` route and an independently verified immutable cached
image digest, source/public-context/profile pins and owned persistent output.
Do not build/pull an image or use a tag as identity. Root chooses a finite case
and reserve from evidence; no measured fit for this composition is known.

The supported runner command shape is:

```sh
python3 -B -m agent_ee.generation_benchmark run \
  --task PUBLIC_NON_G4_TASK --generator FROZEN_PUBLIC_GENERATOR \
  --scratch FRESH_OWNED_SCRATCH --store OWNED_EVALUATION_STORE \
  --attempts 1 --timeout ATTEMPT_CAP --case-timeout TOTAL_CASE_CAP \
  --grading-reserve FINAL_GRADING_RESERVE --max-work 3 \
  --expect-pins EXTERNALLY_RETAINED_PUBLIC_TASK_PINS
```

This is deliberately not labelled runnable today: the completion seam and its
package provenance replay are absent. Do not invent a CLI switch. Freeze their
exact reviewed entry before admission. The single experiment must retain actual
executor/seat exits, time spent at each boundary, raw failure output, cancellation
and owned cleanup evidence, sealed package/native hashes and the evaluator's
captured hashes. Any missing/incomplete stage is a retained failure. It is not
one of the four private cases and establishes no G4 acceptance. Nested-stage
namespace compatibility and full seat/package timing remain runtime unknowns;
this one real composition, rather than an unchanged through-artwork run, is the
useful admission experiment.

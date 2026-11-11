---
name: agent-ee
description: Generates KiCad boards from engineering requirements (parts, schematic, placement, routing, checks, fabrication package) and reviews existing KiCad designs with native ERC, DRC and simulation checks. Use when building a board from requirements or a captured design, or when checking a KiCad design for circuit or board defects.
---

# agent-ee

Use the installed `agent-ee` CLI. Source, scope examples and the
[README](../../README.md) are at `~/git/agent-ee`; the README says what each
stage supports today and what it does not. The [full manual](../../docs/README.md)
has the exact contracts, operator workflows and troubleshooting. Run
`agent-ee doctor` first to see which tools this host has.

## Generate a board

- `agent-ee design generate REQUIREMENTS` turns a requirements document into a
  design statement; `agent-ee design compile DESIGN` validates it and writes
  the build bundle ([design capture and compile](../../docs/design-compile.md)).
- `agent-ee build run PLAN --out RUN` runs the stages from requirements to the
  fabrication package ([build workflow](../../docs/build-workflow.md)). A
  build resumes after an edit to agent-ee and records the change; it refuses a
  changed plan, input source or native tool.
- `agent-ee place run CONFIG --out JOB_DIR` places and routes an existing
  board, and `agent-ee place metrics BOARD` measures a placement
  ([placement job](../../docs/placement-job.md)).

Judge the result on the saved board, and say which claim you are making. No
open connection and no new DRC error under the board's own rules is the
benchmark measure; it is not a fabrication release. A release lists every
remaining violation, including ones the source design already had, every
track or via below its net class's size, the electrical and placement checks
that ran, and the supplier and physical checks that did not. Do not relax a
rule, limit or requirement to get a pass. When a run fails, look for a tool
defect first; report an invalid or unsupported input as that.

## Check an existing design

`agent-ee review SCOPE --checks-only --out /fresh/path/review` runs the
declared native and engineering checks on a captured copy of the design with
no model calls. This is the ordinary way to check a board. The command exits
1 even when every check passes, because it records the model review as not
done; read the check results in `REPORT.md` and `result.json`.

The steps below are the older review workflow, which adds model reviewers and
adjudication on top of the checks. It stays supported and is optional: use it
when the owner or the task asks for it, not as a step every design or change
must pass. It needs an explicit [review profile](../../docs/reviewer-profiles.md)
for Claude, Codex or Agy subscription calls; there is no implicit model
default. Keep the source design read-only and experiments separate. Do not add
an OpenRouter fallback on auth/quota failure.

1. Inspect the design requirements and native artifacts. Declare the actual
   review scope in an `agent-ee.scope/2` JSON file, including external libraries,
   applicable requirements, models, and tool configuration as captured inputs.
   Bind every requirement to executable checks and capture target specifications
   and reference architectures. Legacy scope/1 reviews remain supported without
   these stronger guarantees. See [closed-loop contracts](../../docs/closed-loop.md).
   Existing examples are under `~/git/agent-ee/scopes/` and `fixtures/`.
2. Run `agent-ee doctor`, then
   `agent-ee review /absolute/path/scope.json --profile /absolute/path/profile.json --out /fresh/path/review --max-seconds 1200`.
3. Read `REPORT.md`, `result.json`, raw native evidence and reviewer limitations.
   `agent-ee inspect /path/review` reports progress and freshness.
   For `review`, exit 0 means declared criteria satisfied. Exit 1 includes defects, unresolved
   questions or incomplete coverage; exit 2 is invocation/freshness refusal.
4. Use `agent-ee verify /path/review` before using a result as current acceptance.
   Its exit 0 establishes freshness only; also require `criteria_satisfied`.
   Source, requirement, rule or evidence changes require a new run.
   `agent-ee resume /path/review --out /fresh/path/review` recaptures and reruns;
   it preserves old evidence and never silently reuses acceptance.
5. Investigate disputed claims against native source and appropriate primary
   references. `agent-ee adjudicate RUN decisions.json` records explained
   dispositions. Citation existence validates traceability, not electrical truth.
   In scope/2, only executed coordinator check findings can become accepted
   defects. Reviewer hypotheses remain concerns until reproduced by a check;
   an unrelated assertion cannot borrow a failed check's evidence.
6. Repair only with authorization. The review command does not modify source
   designs. On an authorized disposable copy, change the saved native files,
   review the new candidate, then use `agent-ee close-correction OLD NEW
   --finding ID --checks CHECK_IDS --reason 'evidence-based closure explanation'`.
   The old defect remains visible in its original candidate.

For authorized automatic repair in that workflow, `agent-ee campaign CONFIG --out NEW`
takes an explicit subscription implementation definition and review profile. One agent
writes a disposable candidate; reviewers and adjudication evaluate it. Declare mutable design inputs, iteration and time
budgets. Do not bypass regression, repeated-design or trade-off stops by resetting
history. `agent-ee verify-campaign CAMPAIGN` validates retained history and state.

Capture physical discrepancies with `agent-ee campaign-feedback CAMPAIGN REPORT
--out NEW` (or `bench-review RUN REPORT --out NEW` for standalone reviews).
Reports must bind the candidate, instrument provenance, raw sample hashes and
operating conditions. Feedback invalidates prior current acceptance, retains
historical judgments, and runs calibrated checks and independent reviews before
any further repair. Never label synthetic samples as physical bench evidence.

`criteria_satisfied` applies only to declared checks and this captured candidate.
ERC/DRC, ideal passive simulation and agent agreement do not establish automotive,
EMC, thermal, manufacturing or bench qualification. Missing/failed reviewers,
unsupported analysis and exhausted budgets stay incomplete. Do not change limits,
models, exclusions or requirements to make a candidate pass without an explicit
engineering decision and new evidence.

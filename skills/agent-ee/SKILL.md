---
name: agent-ee
description: Generates KiCad boards from engineering requirements (parts, schematic, placement, routing, checks, fabrication package) and reviews existing KiCad designs with native ERC, DRC and simulation checks. Use when building a board from requirements or a captured design, or when checking a KiCad design for circuit or board defects.
---

# agent-ee

To generate a board, use `agent-ee design generate REQUIREMENTS` (requirements
document to design statement), `agent-ee design compile` and
`agent-ee build run PLAN --out RUN`; see the [README](../../README.md) for what
these support today. The rest of this skill covers reviewing an existing
design, which is one stage of the generator and the older of the two uses.

Use the installed `agent-ee` CLI. Source and scope examples live at
`~/git/agent-ee`; read its [README](../../README.md) for setup and scope authoring.
The CLI coordinates checks and fresh initial reviewers; it does not need manual
reviewer-prompt coordination. Select an explicit [review profile](../../docs/reviewer-profiles.md) for Claude, Codex
or Agy subscription calls. There is no implicit model default. Tools are enabled
for investigation; keep source design read-only and experiments separate. Do not add an OpenRouter fallback on auth/quota failure.
The [full manual](../../docs/README.md) includes exact scope/check/result
contracts, operator workflows, troubleshooting and extension instructions.

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

For authorized automatic repair, use `agent-ee campaign CONFIG --out NEW` with
an explicit subscription implementation definition and review profile. One agent
writes a disposable candidate; fresh independent reviewers and centralized
adjudication evaluate it. Declare mutable design inputs, iteration and time
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

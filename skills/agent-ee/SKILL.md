---
name: agent-ee
description: Runs candidate-bound adversarial PCB and electrical engineering reviews with native KiCad checks, independent agent judgments, explicit evidence and correction closure. Use when reviewing a KiCad design, evaluating circuit or board defects, or verifying an authorized electrical repair.
---

# agent-ee

Use the installed `agent-ee` CLI. Source and scope examples live at
`~/git/agent-ee`; read its [README](../../README.md) for setup and scope authoring.
The CLI coordinates checks and fresh initial reviewers; it does not need manual
reviewer-prompt coordination. Current default reviewers use Claude subscription
authentication. Do not add an OpenRouter fallback on auth/quota failure.

1. Inspect the design requirements and native artifacts. Declare the actual
   review scope in an `agent-ee.scope/1` JSON file, including external libraries,
   applicable requirements, models, and tool configuration as captured inputs.
   Existing examples are under `~/git/agent-ee/scopes/` and `fixtures/`.
2. Run `agent-ee doctor`, then
   `agent-ee review /absolute/path/scope.json --out /fresh/path/review --max-seconds 1200`.
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
6. Repair only with authorization. The review command does not modify source
   designs. On an authorized disposable copy, change the saved native files,
   review the new candidate, then use `agent-ee close-correction OLD NEW
   --finding ID --checks CHECK_IDS --reason 'evidence-based closure explanation'`.
   The old defect remains visible in its original candidate.

`criteria_satisfied` applies only to declared checks and this captured candidate.
ERC/DRC, ideal passive simulation and agent agreement do not establish automotive,
EMC, thermal, manufacturing or bench qualification. Missing/failed reviewers,
unsupported analysis and exhausted budgets stay incomplete. Do not change limits,
models, exclusions or requirements to make a candidate pass without an explicit
engineering decision and new evidence.

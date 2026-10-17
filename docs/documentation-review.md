# Documentation coverage and review record

This audit describes the current worktree on 2026-10-17. It updates the operator
and maintainer manual for the scope/2 implementation without changing runtime
code. The earlier [implementation review](closed-loop-review.md) and historical
acceptance records remain separate evidence.

## Coverage

| Surface | Authoritative documentation |
| --- | --- |
| Installation, prerequisites and routing | [Installation](installation.md), [reviewer profiles](reviewer-profiles.md) |
| All eleven CLI commands, flags, exit codes and budgets | [CLI reference](cli-reference.md) |
| Ordinary review, correction closure and continuation choice | [Operator guide](operator-guide.md) |
| Scope fields, immutable baseline, packet ordering and migration | [Scope reference](scope-reference.md) |
| Native checks, parameter validation and engineering limits | [Check reference](check-reference.md) |
| Pinned producer, parser request/report, coupled metrics and bounds | [Measurement adapters](measurement-adapters.md) |
| Results, witnesses, evidence seal and durable state schemas | [Result reference](result-reference.md) |
| Single-writer configuration, convergence order, leases and history | [Campaign guide](campaign-guide.md) |
| Bench samples, residual arithmetic, epochs and failure recovery | [Bench feedback](bench-feedback.md) |
| Modules, orchestration, sandbox reads/writes and trust boundaries | [Architecture](architecture.md), [security](security.md) |
| Refusals, partial writes and diagnostic recovery | [Troubleshooting](troubleshooting.md) |
| Extension interfaces, tests and reproduction | [Maintainer guide](maintainer-guide.md), [evidence reproduction](reproducing-evidence.md) |
| Invariant mapping and demonstrated limits | [Closed-loop overview](closed-loop.md), [coverage](coverage.md) |

Nine embedded Mermaid diagrams cover orchestration, sandbox information flow,
baseline identity, campaign sequence/state, producer/parser evidence, witness
adjudication, bench re-entry and calibration epochs. Historical agreements and
assessments are explicitly marked as historical where their old topology differs
from the current implementation.

## Independent reviews and dispositions

Fable agent-413 and Argon agent-414 reviewed the shared worktree read-only. The
coordinator checked findings against current source before editing the manual.
Neither reviewer wrote the documentation during this task.

| Review | Selected findings and disposition |
| --- | --- |
| Fable initial, Cairn `6fcc63a0-401f-4683-b617-6f66161f02a4` v1 | Corrected result-tree omissions, empirical outcome projection, scope/2 witness refusal and accepted-check restrictions, packet baseline placement, check ceilings/calibration, diagnostics, navigation and budget ranges. |
| Argon initial, Cairn `4f4896fb-65d5-4ffb-8c35-03a9baefb3ee` v1 | Updated module/test inventories and interface signatures, packet ordering, result contracts, sandbox role matrix, optional tool inputs, exhausted feedback behavior, retry collisions and current reproduction routes. Suggested diagrams were checked against source rather than copied as authority. |
| Fable final, Cairn `343c49ad-9e8d-49ca-b271-92f561513d76` v1 | Confirmed prior corrections and new guide contracts. Reported a profile-propagation error and prompted corresponding guide edits. Subsequent source inspection and a focused execution disproved that report: capture embeds external profile content in the scope, and bench re-entry preserves it. The guide edits have been corrected. Also made producer artifact location wording exact. Fable independently reported 98 passing tests. |
| Argon final, Cairn `eb56c299-5bb3-4b77-804a-f64c893d0287` v1 | Found no remaining material error in diagram semantics, state/epoch transitions, convergence, leases or provenance. Its cosmetic heading overlap was already fixed and rerendered; added optional external-profile fields to the candidate table as suggested. |

## Verification

- The local checker passed for 33 documents, 282 local links, six JSON examples,
  five Python examples, 25 shell examples and nine Mermaid blocks. A negative
  smoke test confirmed it rejects a missing link.
- All eleven command help pages were captured from the actual CLI and compared
  with the command reference. Local outputs are under `build/docs-verification/`.
- The documented parser template executed in a real Bubblewrap sandbox against
  synthetic producer output. The resulting 110 degC metric with a 125 degC
  maximum produced a margin of 15 degC through the real bounds evaluator.
- The scope/2 fixture captured and passed snapshot verification. Campaign profile
  definitions and all six provided reviewer profiles passed their real validator.
- All nine Mermaid blocks rendered with Mermaid 11.12.0 and Chromium; rendered
  SVG/PNG files and a version manifest are local outputs in `build/docs-diagrams/`.
  Visual inspection prompted a shorter trust-boundary heading and a vertical
  measurement flow so labels remain readable.
- The retained final native control still passed `verify` after documentation
  edits. Its `incomplete` outcome is expected for checks-only execution.

The checker is [scripts/check_docs.py](../scripts/check_docs.py). It does not
fetch external links, run the examples, render Mermaid or establish prose truth.
Rendered build outputs are ignored local artifacts; the embedded Mermaid sources
are the maintained diagrams.

## Implementation limits made explicit

This audit documented the following execution observations without changing
implementation behavior. Both observations were subsequently corrected:

1. RC deck validation defaults an omitted `step_v`, but metric-condition creation
   reads the original parameter directly. With measurements requested, specify
   `step_v` explicitly to avoid a post-simulation `KeyError`. A real native run
   reproduced the error in `build/docs-verification/omitted-step-run/`.
   **AGENTEE-2 correction:** deck and metric generation now share validated
   parameters, and omitted `step_v` completes with the 1.0 V default. Native
   regression tests compare the measured response against the analytic RC
   response and preserve historical explicit condition spellings. See
   [coverage](coverage.md) for identity and observation compatibility.
2. **Correction:** standalone `bench-review` preserves external profiles too.
   `capture` embeds their parsed content in `candidate.json` under
   `scope.review_profile`; `bench_review` copies that scope into the new review.
   A focused execution exercised real capture and bench scope assembly, with
   ingestion, verification, result persistence and new review execution stubbed.
   It confirmed profile continuity, not live provider or physical validation.
   The earlier missing-profile claim was a source-reading error.

An entirely unbounded native RC check does not invoke empirical correction.
Parser/model qualification, instrument authenticity and physical coverage remain
operator obligations. The earlier live subscription campaign stopped at an
invalid adjudicator citation; complete repair loops are established by scripted
integration tests, not a claimed successful live-model repair or physical test.

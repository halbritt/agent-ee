# Fable (agent-413) final consensus — agent-ee enhancement roadmap

Reviewed: final-review-contracts.json (12 contracts, source_commit a834201),
synthesis-v1.md, parent/report.md, argon/report.md, my own pool and discussion
response with Amendment v2. No new divergence or model branches were run.

## Explicit confirmation

**I confirm the final delivery order exactly as proposed: 1, 3, 4, 5, 6, 13, 7,
8, 9, 10, 11, 12** (twelve enhancement tickets plus the roadmap parent), **and I
confirm the detailed contracts in final-review-contracts.json as written**,
including each contract's scope, verification list, migration, failure behavior
and dependency edges (7←4, 8←4, 9←4+5, 10←5+6, 12←3+4). No material correction
to submit; I reviewed each contract against source knowledge at a834201 and
against my prior positions, and found none misstated.

Conditions: none beyond what the contracts already state. The points I care
about are already embedded: acceptance stays per-candidate-reviewed (no lane
bypass), item 4 has no waiver field and distinguishes acceptance from
informational obligations, item 5 fails to unavailable/incomplete rather than
transferring or widening, item 6 permits unknown facts and zero/multiple
actions and does not pretend read-only implies zero trust-kernel risk, item 8
gives reviewer scripts no acceptance authority, item 13 spends no model call.

## Resolution of my earlier disagreements

- **5/6 ordering:** My discussion response proposed swapping 5 and 6 as a weak
  preference with advance consent to the original order. The parent's rationale
  — calibration applicability (5) prevents evidence transfer outside its
  supported design, a soundness hole, which outranks diagnostic operability —
  is a fair resolution; consent exercised, swap withdrawn. Confirmed order
  keeps 5 before 6.
- **F_GETLK amendment:** My suggested F_GETLK probe is rejected pending proven
  platform interoperability; the contract's default advisory/unverified
  snapshot (and the observation that a .tmp file may be an ACTIVE write, not
  only an interrupted one) is more honest than my proposal. Accepted.
- **Quota canary (part of my O1/preflight suggestion):** dropped in item 13
  with reason; correct call — a one-token canary spends quota without proving a
  long call completes, and auth/quota failures already stop affected operations
  under existing routing rules. Accepted.
- **My adopted suggestions, acknowledged with thanks:** item 13 (environment
  preflight) and the deliberately-broken reference-board scripted harness cases
  in item 4's verification.

## Withdrawn proposal 2 — incorporated

I incorporated the withdrawn profile-bug correction: proposal 2 was a false
runtime bug originating from my own documentation-pass error. Verified in
source (candidate.py:72 embeds the external profile into the stored scope
before manifest persistence; bench_review's scope rebuild preserves it) and by
independently re-running reproductions/profile-continuity.py. The docs
correction is committed at 5516e13; no feature ticket exists for it, correctly.
My discussion-response.md Amendment v2 records the retraction and root cause.

## Score bookkeeping corrected

B6's weighted score is corrected to 7.65 (arithmetic .35·7+.40·8+.25·8; I had
misprinted 7.75) in ideas.json and report.md with a superseded note; the top-3
set and order are unchanged and no branch was rerun. Original pools and branch
outputs remain preserved unmodified under branches/ and focus/.

## Closing statement

All five frames' pools remain preserved; items are implementation-ready
backlog designs, not implemented features; owner supplies real requirements,
qualified models and measurements at use time; sealed-check authority and
source evidence outrank this ideation. With both explicit confirmations in,
the coordinator alone publishes to the repository and Plane.

— Fable, agent-413, 2026-10-17

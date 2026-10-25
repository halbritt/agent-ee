# AGENTEE-521 contract — evidence methods and qualification states

Design for chief (agent-564) and builder review. Author agent-565 (Fable),
2026-10-25. Input: agent-ee `dab756e56a6a5358c00983074565af0e8e09862c`,
Plane AGENTEE-521 (description sha256 `3d61678b…971a1`), RD-03/04/05 in
[recovery-plan.md](../recovery-plan.md), findings REC-06/07/12/19/20/21/26.
No production code changes in this dispatch. Implementation depends on
AGENTEE-519 (runtime) and AGENTEE-520 (intent contract) and on chief approval
of this contract; it is not an owner permission gate.

## 1. Current behaviour (source-inspected, not re-audited)

| Surface | Field / code today | Where | Meaning today |
| --- | --- | --- | --- |
| `build run` report `agent-ee.build-report/1` | `status` ∈ running/accepted/checkpoint/failed/interrupted; `accepted` bool; `completed`, `attempts`, `candidate`, `identity`, `inputs.runtime` | `build_workflow.run` | `accepted` = all seven stages completed and `_package` did not raise |
| `build run` exit | 0 for accepted **and** checkpoint; 1 otherwise | `build_workflow.execute` | checkpoint (`--through`) is "diagnostic, never acceptance" but shares exit 0 |
| `_package` | requires every acceptance-mapped check `ok/pass`, every executed check ∈ pass/info, exactly one passing `fab_package` bound to the board hash; writes `accepted-package.json` with `physical_obligations`, `physical_validation_performed:false`, `supplier_preview_performed:false` | `build_workflow._package` | software file-release gate; already refuses missing/stale/failed required evidence |
| `_review_candidate` | `run_review(..., checks_only=True)` then `verify` | `build_workflow._review_candidate` | deterministic checks only; no reviewer, no adjudication, by design |
| `build review` report `agent-ee.build-review/1` | `accepted:false` always; `outcome` copied from review result; exit 0 only if `criteria_satisfied` | `build_workflow.review_build/execute` | checks-only can never reach `criteria_satisfied` (`cli.outcome` needs ≥2 reviews + adjudication), so exit is always 1; documented but not tested |
| Review result `agent-ee.result/1` | `outcome` ∈ criteria_satisfied/incomplete/defects_or_unresolved; `state` ∈ running/complete/incomplete/interrupted; limitation "Checks-only run: independent agent reviews and adjudication not performed." | `cli._run_review`, `cli.outcome` | sound; reused unchanged |
| Requirement map `agent-ee.requirement-map/1` | per requirement exactly one of `checks` / `limitation{reason}` / `bench{criterion,procedure}` | `requirement_map.disposition` | no operational or judgment mode; a `checks` entry can bind any check kind to any clause |
| Coverage | `baseline.coverage.requirements[rid].acceptance_checks`; receipt `requirements[].checks` | `requirement_map.compile_map`, `baseline.compile_baseline` | counts check ids per requirement; nothing reports unique executed methods |
| DRC/ERC | `details.constraint_classes`, `advisory_findings_count`, `graded_unwaived`, `ignored_checks`, `by_severity`; class ∈ owner/manufacturer/datasheet/engineering/default | `checks.check_drc/check_erc/grade_drc`, `design_rule_provenance` | advisories and ignored categories are already captured per check, not surfaced at release level |
| Retained acquisition map | `build/acquisition-release/inputs/requirement-map.json` sha256 `df11c81d221f410ed016f3583ae14860570d117bbc81fcc40f9f6121367dc863` (git-ignored retained input) | — | 26 requirements: 23 `checks`, 1 `bench`, 0 `limitation`; `ACQ-DELIV-2` → one `netlist_parity` check; `netlist_parity` bound to 6 requirements; `engineering-intervals` reused by 5, `engineering-geometry` by 3 |
| Retained decision file | `build/acquisition-release/inputs/manufacturing-decision.json` sha256 `7db99d7476a74b4e6f546da44ddcf866412cdeb409dcc834edbd0bc01a3a9be6` | — | still states nominal 1.6 mm and "8 mm including mated connectors" while the closure record corrected to 1.2 mm and a scoped height (REC-19/20) |
| Docs | `docs/cli-reference.md` exit table omits `build run` / `build review`; `docs/build-workflow.md` says "accepted native files" without naming which qualification is claimed | — | REC-26 drift |

Existing consumers of `accepted` outside `build_workflow.py`: none in `src/`,
`tests/`, `integration/`, `scripts/` (grep 2026-10-25). The only `build-report`
consumer is `build review` itself. `tests/test_build_review.py` covers refusals
only; no test pins `build run` exit codes or `accepted` semantics.

## 2. Qualification states

Five ordered states, each bound to one candidate identity (final native board
sha256 plus build `identity`). A record may hold any prefix of the order. No
state is inferred from a higher one being absent; no higher state can be
recorded without the lower states for the same candidate hash.

| State | Produced by | Requires | Never claims |
| --- | --- | --- | --- |
| `checks` | `build review`; `review --checks-only`; `--through` checkpoints | deterministic native/measurement checks executed on the exact candidate | acceptance, review, assembly, hardware |
| `file_release` | `build run` reaching `package` | `checks` + all acceptance-mapped checks `ok/pass` + package bound to board hash + every `operational` clause proved (§4) + every `judgment` clause bound and unresolved-defect-free (§5) + advisory/ignored register written (§6) + current release decision consistent with corrected inputs (§7) | independent review, supplier confirmation, tested hardware |
| `engineering_review` | ordinary `review` on the released candidate's scope via the subscription route, `outcome == criteria_satisfied`, ≥2 reviews, adjudicated | `file_release` for the same board hash; review `candidate_id` equal to the released candidate | supplier, hardware |
| `supplier` | an authored supplier evidence record (preview/DFM result, supplier identity, date, artifact hashes) | `file_release`; artifact hashes equal to `accepted-package.json.artifacts` | hardware |
| `physical` | bench evidence per `bench` obligations (`criterion`, `procedure`, result, operator, date) | `file_release`; every `bench` receipt addressed or explicitly deferred with reason | nothing further; M1 does not reach it |

Record: `release-record.json`, schema `agent-ee.release-record/1`, written by
the package stage beside `accepted-package.json` and extendable later only by
appending higher states with their evidence (append-only; earlier entries are
never rewritten). Fields: `candidate` {board_sha256, build_identity,
candidate_id}, `states` [{state, at_utc, evidence[] (paths + sha256), by
(tool or author identity)}], `highest_state`, `methods` (§4), `judgments`
(§5), `rules` (§6), `decision` (§7), `superseded` (§7).

## 3. Compatibility contract (accepted fields and exit codes)

Nothing existing changes meaning or type; everything new is additive.

| Field / code | After this item | Test to add |
| --- | --- | --- |
| build-report `accepted:true` | means exactly `highest_state == file_release`; the report gains `qualification: {schema, highest_state, record}` | report with `accepted:true` always has `qualification.highest_state == 'file_release'`; checkpoint/failed reports have `highest_state == 'checks'` or `null` |
| build-report `status` | unchanged vocabulary | existing values round-trip |
| `build run` exit | unchanged: 0 accepted or checkpoint, 1 otherwise. Rationale: `--through` is an operator-requested checkpoint; automation must gate on `accepted` + `qualification`, which the docs will state | exit/`status`/`accepted` matrix for accepted, checkpoint, failed |
| `build review` report | `accepted:false` retained; gains `qualification_state: 'checks'`; exit stays 1 (checks-only cannot be `criteria_satisfied`) | passing checks-only review still returns `accepted:false`, exit 1, limitation string present |
| review result `outcome`/`state` | unchanged | existing suites |
| `accepted-package.json` | unchanged keys retained; gains `release_record` path + sha256 | key set is a superset; the two `*_performed:false` flags remain false after `build run` |
| requirement-map/1 | accepted unchanged for retained history; compiles with a receipt `limitations` note "map/1: no method binding; operational and judgment clauses unverified" | retained acquisition map/1 still compiles byte-identically to its receipt |
| CLI exit table | `build run` and `build review` rows added; automation sentence names `accepted` + `qualification.highest_state` | doc check: table lists both commands |

A CLI consumer that today treats `accepted:true` as "manufacturing package
verified by software checks" keeps working. One that treated it as "reviewed /
assembled / tested" was reading a claim the tool never made; the record now
says so explicitly.

## 4. Evidence-method binding

`agent-ee.requirement-map/2` keeps the `/1` modes and adds two. Exactly one
mode per requirement stays the rule (`disposition` extends `modes`).

| Mode | Payload | Verified by |
| --- | --- | --- |
| `checks` (existing) | check list; **new optional `method_class`** per check: `native_check` \| `measurement` \| `model` (SPICE/interval) \| `topology` (parity/rules/interface) | unchanged compile; class is recorded in the receipt |
| `limitation` / `bench` (existing) | unchanged | unchanged |
| `operational` (new) | `{criterion, proof: {kind: 'execution_and_resume'}}` | package stage: the build journal of **this** run, or a referenced retained run with identical `identity`, contains an attempt whose `status` is `interrupted` followed by completion of the same stage and a later `accepted` report; receipts of the resumed stages verify (`tree_identity`); evidence paths recorded in `release-record.methods` |
| `judgment` (new) | `{record: 'path/to/judgment.json'}` | §5 |

Rule for `ACQ-DELIV-2` and any clause of the same shape: `operational` is the
only admissible mode. Netlist parity may remain bound to `ACQ-ELEC-*`
topology clauses; it cannot be the sole evidence of a reproducibility clause.
This is enforced by the authored map (the acquisition map is re-authored under
`/2` as a new input revision), by a compiler refusal when an `operational`
requirement lists checks, and by the negative test in §8 — not by parsing
prose. No universal prose validator is added (exclusion honoured).

Reporting: the release record's `methods` section lists **unique executed
methods** (check id + kind + method_class + evidence sha256) separately from
**requirement coverage** (requirement id → bound method ids + mode). The
acquisition figure that this surfaces today: 23 checked requirements over 15
kinds, with `netlist_parity` serving six of them. Reuse is allowed; it is
visible.

## 5. Captured engineering judgments

`agent-ee.engineering-judgment/1`, one file per judgment, an authored input
(hash-verified like every other input):

```
{ "schema": "agent-ee.engineering-judgment/1", "requirement": "ACQ-MECH-4",
  "clause": "<exact spec span or spec-record locator>",
  "conclusion": "...", "assumptions": ["..."],
  "sources": [{"kind": "datasheet|manufacturer|owner|engineering", "citation": {...}}],
  "limits": "<applicability and what is not covered>",
  "candidate": {"board_sha256": "...", "inputs_identity": "..."} | null,
  "author": {"agent": "agent-NNN", "model": "...", "date_utc": "..."},
  "unresolved_defects": [] }
```

Package-stage rules: every `judgment`-mode requirement names a record; a
record bound to a candidate whose `board_sha256` differs from the final board
is **stale** and refuses release (same strictness as stale check evidence);
`candidate: null` is allowed only when `limits` says the judgment is
candidate-independent (e.g. source applicability); a non-empty
`unresolved_defects` refuses release; the record is copied into
`release-record.judgments` with its sha256. The existing closure prose for
ACQ-MECH-1/ACQ-MECH-4 (`docs/qualification/acquisition-closure-2026-10-24.md`)
is the first content to migrate into such records, with the 1.2 mm correction
and the mated-height scope correction as explicit `assumptions` changes.
Original spec text and the correction history stay untouched.

## 6. Rules, advisories and ignored categories (RD-04)

Release record `rules` section, assembled from existing check details without
re-grading: for ERC and DRC, `ignored_checks` verbatim; `by_severity`;
`constraint_classes`; every `default`-class (unsourced) advisory finding with
locator and measured value (`drc-constraints.json` already classifies them);
`waived` count; and the list of applicable sourced rules (`owner`,
`manufacturer`, `datasheet`, cited `engineering`) that were evaluated. An
applicable sourced rule missing from the evaluated set refuses release (this is
the existing `require_evaluated_limits` path, now reported). Nothing promotes a
`default` limit to a gate and nothing demotes a sourced one; the decisions.md
AGENTEE-331/338/348 policy is restated by reference, not reimplemented.

## 7. Current release decision versus history (REC-19/20)

The package stage writes `release-decision.json` (`agent-ee.release-decision/1`)
from current inputs: board nominal thickness and tolerance source, height
scope, profile identity, envelope hash, the judgments of §5, and
`supersedes: [{path, sha256, reason}]`. The retained
`manufacturing-decision.json` (sha256 `7db99d74…9be6`) is listed under
`supersedes` with reason "1.6 mm nominal and absolute 8 mm mated height
superseded by the 2026-10-24 corrections"; its bytes are not edited. A
decision whose thickness disagrees with the envelope's `stackup/1` evidence
refuses release. Historical runs under `build/` keep their own files and are
inspectable, not current.

## 8. Negatives without a prose validator

| Negative | Existing fixture | Required outcome | Mechanism |
| --- | --- | --- | --- |
| Wrong ADC address | `build/requirement-clauses/bad-address-*` | the `hardware_interface` / `topology_rules` check bound to the addressing clause returns `defect`; `build run` refuses at the stage that executes it; `highest_state` never reaches `file_release` | existing discriminating checks; the contract only requires the clause to bind to that check, recorded with `method_class: topology` |
| Reversed diode | `build/agent545/acquisition-reversed-diode-build.json` (`docs/qualification/acquisition-electrical-2026-10-24.md`) | ERC/parity clean; the `active_spice` / `dc_path` / interval check bound to the polarity clause returns `defect`; release refused | same |
| Wrong evidence-method mapping | re-authored acquisition map with `ACQ-DELIV-2` left as `checks: [netlist_parity]` | compiler refusal `operational_required` naming `ACQ-DELIV-2`… **only** if the clause is declared `operational` in the spec record's obligation kind; otherwise the release record shows `ACQ-DELIV-2` covered by `topology` with no `operational` proof, and the M1 acceptance case (agent-569) rejects it | the spec-record obligation kind is the sourced declaration; no prose parsing |
| Missing required judgment | map/2 `judgment` entry without a record, or record with `unresolved_defects` | package refusal naming the requirement | §5 |
| Changed candidate inputs on resume | existing identity check | existing refusal | unchanged |
| Checks-only presented as review | `build review` on an accepted run | `accepted:false`, `qualification_state:'checks'`, exit 1 | §3 test |

The `operational_required` refusal needs one sourced hook: the spec record
(`spec_record.verify`) must carry an obligation kind per requirement
(`executable`, `operational`, `physical`, `judgment`). That is an authored
field in the captured spec record, not an inference. If chief prefers not to
touch the spec record in M1, the fallback is the M1 acceptance case alone (row
3, second clause) and the map author's responsibility; this is the one
judgment the chief must make (§11).

## 9. Entry points and implementation slices

Ordered; each slice is independently landable and has its own tests. Slices
1–3 are the minimum for the five acceptance criteria.

| # | Slice | Files | Tests | Depends on |
| --- | --- | --- | --- | --- |
| 1 | Qualification record + compatibility pins: `release-record.json`, `qualification` on build-report, `qualification_state` on build-review, `accepted-package.json` link; exit matrix | `src/agent_ee/build_workflow.py`; new `src/agent_ee/release_record.py` | new `tests/test_build_qualification.py`: exit/status/accepted matrix; checks-only never `file_release`; record append-only | 519 (native harness to run a full build fixture) |
| 2 | requirement-map/2: `operational`, `judgment`, `method_class`; `/1` compat; receipt `methods` vs coverage | `src/agent_ee/requirement_map.py`; `docs/requirement-map.md` (if present) | extend `tests/test_requirement_map.py`: `/1` byte-stable; `/2` refusals (`operational` with checks, missing record); methods/coverage counts | none |
| 3 | Package-stage enforcement: operational proof from journal, judgment binding/staleness, rules register, release decision with `supersedes` | `src/agent_ee/build_workflow.py` (`_package`, `_review_source` reuse), `src/agent_ee/checks.py` (expose, not re-grade, advisory/ignored details) | `tests/test_build_qualification.py` negatives of §8 rows 4–5; stale judgment refusal; decision/stackup mismatch refusal | 1, 2, 519 |
| 4 | Spec-record obligation kind + `operational_required` compiler refusal (§8 row 3) | `src/agent_ee/spec_record.py`, `requirement_map.py` | `/2` map with mis-bound ACQ-DELIV-2 refuses | chief decision §11 |
| 5 | Re-author acquisition inputs as a new revision under `/2` with judgments and the current decision; rerun in the supported runtime | `build/…` retained inputs (new revision directory; originals untouched); `docs/qualification/` new dated record | native run evidence; agent-569 acceptance case | 519, 520, 3 |
| 6 | Docs: `docs/build-workflow.md` qualification section; `docs/cli-reference.md` exit rows + automation gate sentence; link recovery plan | docs only | doc consistency test if the repo has one; otherwise review | 1 |

Not touched: `cli.outcome`, review result schema, `verify`, native severities,
`grade_drc`, policy files, reviewer routes, any `build/` historical bytes.

## 10. Acceptance coverage

| AGENTEE-521 acceptance | Covered by |
| --- | --- |
| ACQ-DELIV-2 needs execution + resume proof, not parity | §4 `operational`, slices 2–5, §8 row 3 |
| Checks-only never claims review/supplier/hardware; compat documented and tested | §2–3, slice 1 and 6 |
| Judgments name assumptions/sources/candidate/limits; unresolved defect blocks | §5, slice 3 |
| Advisories and ignored categories visible; sourced rules stay gates; no promotion | §6, slice 3 |
| Current decision consistent with 1.2 mm and corrected height; history immutable | §7, slices 3 and 5 |

## 11. Judgments required from chief before implementation

1. Spec-record obligation kind (slice 4) in M1, or defer to the acceptance case.
2. `build run --through` keeps exit 0 (compat, this contract) or moves to a new
   exit code; the contract recommends keeping it and gating automation on
   `accepted` + `qualification.highest_state`.
3. Whether `release-record.json` is written only at `package` (contract) or
   also at checkpoints with `highest_state:'checks'` (useful for agent-569's
   harness; small extra scope).

## 12. Exclusions kept

No model invocation inside every package stage; no signatures; no prose
validator; no owner-waiver generation; no rewrite of historical acceptance; no
auto-success from an unavailable reviewer (checks-only stays `incomplete`).

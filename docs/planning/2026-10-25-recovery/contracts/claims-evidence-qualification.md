# AGENTEE-521 contract — evidence methods and qualification dimensions

Design for chief (agent-564) and builder review. Author agent-565 (Fable).
Revision 3 (frozen for integration), 2026-10-25, after chief reviews of 26e975a8 and 2a17119e; §12 settles the remaining boundaries and overrides earlier sections where they differ. Input: agent-ee
`dab756e56a6a5358c00983074565af0e8e09862c`, Plane AGENTEE-521, RD-03/04/05 in
[recovery-plan.md](../recovery-plan.md), REC-06/07/12/19/20/21/26,
[intent-compatibility.md](../intent-compatibility.md). No production code in
this dispatch. Implementation depends on AGENTEE-519/520 and chief approval.

Chief decisions applied: `build run` checkpoint keeps exit 0; obligation kinds
are source-bound and authored now; checkpoints publish per-dimension status
but never assert checks passed because a checkpoint exists; supplier and
physical are record-only pending dimensions in M1; no external upload or
bench workflow.

## 1. Current behaviour (source-inspected)

| Surface | Today | Where |
| --- | --- | --- |
| build-report/1 | `status` ∈ running/accepted/checkpoint/failed/interrupted; `accepted` bool = seven stages completed and `_package` raised nothing; `completed[]` (stage, directory, candidate, tree), `attempts[]` (stage, directory, status, reason), `identity`, `inputs` {plan_sha256, source inventory, runtime} | `build_workflow.run` |
| `build run` exit | 0 for accepted and checkpoint, 1 otherwise | `build_workflow.execute` |
| `_package` | every acceptance-mapped check `ok/pass`, every executed check ∈ pass/info, one passing `fab_package` bound to the board hash; writes `accepted-package.json` (`physical_obligations`, `physical_validation_performed:false`, `supplier_preview_performed:false`) | `build_workflow._package` |
| `_review_candidate` | `run_review(checks_only=True)` + `verify` | `build_workflow._review_candidate` |
| build-review/1 | `accepted:false` always; exit 0 only on `criteria_satisfied`, unreachable for checks-only (`cli.outcome` needs ≥2 reviews and adjudication) → always exit 1; documented, untested | `review_build`, `execute` |
| result/1 check rows | carry `id`, `kind` (from the scope definition, set by the executor), `execution`, `outcome`, `details`, `limitations` | `checks.run_checks` (record at line 1174) |
| requirement-map/1 | one of `checks` / `limitation` / `bench` per requirement, or `spans` (byte-complete partition with sub-mappings); prose `context` | `requirement_map.disposition`, `source_mappings` |
| spec-record/1 | byte-identical capture of `spec.md`; clause entries {id, kind (bullet/table_row/acceptance), section, locator, text, text_sha256}; `verify` recomputes the capture, so no authored field can live inside it | `spec_record.capture/verify` |
| DRC/ERC details | `ignored_checks`, `by_severity`, `constraint_classes`, `advisory_findings_count`, `graded_unwaived`, `drc-constraints.json`; classes owner/manufacturer/datasheet/engineering/default | `checks.check_erc/check_drc/grade_drc`, `design_rule_provenance` |

Retained acquisition inputs (git-ignored, hashed): `requirement-map.json`
`df11c81d…7dc863`: 26 entries = 23 `checks` + 1 `bench` (ACQ-BENCH-1) + 2
`spans` partitions (ACQ-ELEC-6, ACQ-MECH-3); the spec record captures 18
clauses + 1 prose, the rest are derived envelope obligations. `ACQ-DELIV-2` →
one `netlist_parity` check; `netlist_parity` serves 6 requirements.
`manufacturing-decision.json` `7db99d74…9be6` still states 1.6 mm nominal and
"8 mm including mated connectors". No consumer of `accepted` exists outside
`build_workflow.py`; `docs/cli-reference.md` exit table omits `build`.

Negatives already on disk, exact entry points: wrong ADC address —
`build/requirement-clauses/bad-address-mutation.json` (U2 pin 1 GND→V3V3),
rejected by check `adc-ties` kind `topology_rules`, violation `U2-1-6`
(`bad-address-review/result.json`); reversed diode —
`build/agent545/acquisition-polarity-reversed-review-02/result.json`, check
`electrical-topology` kind `topology_rules`, violations `D1-2-1`, `D1-1-1`.
Both run through `checks.check_topology_rules` (`docs/topology-rules.md`).
ERC and parity pass in both; only the bound topology rule discriminates.

## 2. Qualification dimensions (replaces the state ladder)

One module `src/agent_ee/qualification.py` writes one record
`qualification.json` (`agent-ee.qualification/1`) in every completed stage
directory and the package stage; build-report gains
`qualification: {path, sha256, dimensions: {name: status}}`. Dimensions are
independent; each has `status` ∈ `not_assessed` / `pending` / `pass` /
`failed`, `evidence[]` (paths + sha256 from existing receipts and results)
and `binding` {board_sha256 or null, scope_sha256, stage}. No dimension
derives from another's status.

| Dimension | pass iff | failed iff | otherwise |
| --- | --- | --- | --- |
| `checks` | this stage executed a review and every executed row is `ok` with outcome pass/info; rows listed with id, kind, outcome | any executed row is not ok, or outcome defect/unknown | `not_assessed` when the stage ran no review (e.g. parts, scaffold); a checkpoint never implies pass |
| `file_release` | package gate passed (§1 `_package`) **and** §4 operational clauses are not `failed` **and** §5 judgment clauses are `recorded` **and** §6 register has no missing applicable sourced rule **and** §7 decision agrees with stackup evidence | package gate refused (reason recorded) | `not_assessed` at any checkpoint |
| `operational` | journal of **this run** shows all seven stages in `completed[]` with verified tree identities and at least one attempt with status interrupted/failed followed by a completed attempt of the same stage under unchanged `identity` | completed stage identity or identity check failed during resume | `pending` when no interruption was observed; `not_assessed` before package |
| `engineering_review` | an ordinary full `review` run (subscription route, ≥2 reviews, adjudicated, `criteria_satisfied`, `verify` ok) is referenced and its candidate board sha256 equals this candidate | referenced review is `defects_or_unresolved` for this board | `not_assessed` (default; a review may be run before or after package, it binds by board hash) |
| `judgments` | every `judgment`-mode requirement has a record admitted per §5 with empty `unresolved_defects` (`recorded`) | any record stale, mismatched or with unresolved defects | `pending` when a required record is missing at a checkpoint |
| `supplier` | not reachable in M1 | — | `pending` (record-only; `supplier_preview_performed:false` retained) |
| `physical` | not reachable in M1; later only from bench result records per `bench` obligation | — | `pending` for every bench obligation; deferred obligations stay `pending` with reason, never pass |

`accepted` is the explicit projection `file_release.status == 'pass'`,
nothing else. A checkpoint report shows `file_release: not_assessed` and the
real `checks` rows of that stage.

## 3. Compatibility (fields and exit codes unchanged)

| Field / code | After | Test |
| --- | --- | --- |
| build-report `status`, `accepted` | unchanged; `qualification` added | `accepted:true ⇔ file_release pass`; checkpoint → `file_release not_assessed`, `accepted:false`; failed → `file_release failed` |
| `build run` exit | 0 accepted/checkpoint, 1 otherwise (chief decision); docs state automation gates on `accepted` plus `qualification.dimensions` | exit × status matrix |
| build-review/1 | `accepted:false` kept; `qualification` added with `checks` only, every other dimension `not_assessed`; exit stays 1 | passing checks-only review → `accepted:false`, exit 1, checks-only limitation present |
| result/1, `verify`, `cli.outcome` | unchanged | existing suites |
| `accepted-package.json` | keys retained; `qualification_sha256` added | superset check; both `*_performed` remain false |
| CLI reference | `build run` / `build review` rows added | doc check |

## 4. Source-bound obligations and evidence methods

**Obligations file** `obligations.json` (`agent-ee.obligations/1`), authored
at the requirements stage next to `spec.md`, one entry per captured clause:
`{id, text_sha256, obligation}` with `obligation` ∈ `executable` /
`operational` / `judgment` / `physical` / `context`. The compiler refuses a
missing entry, an unknown id, or a `text_sha256` that differs from the spec
record clause (so an edited clause invalidates its obligation). Derived
envelope obligations are `executable` by construction. No clause id is
special-cased in code; `ACQ-DELIV-2` is `operational` because the acquisition
obligations file says so, and the acceptance case checks that file.

**requirement-map/2** keeps `/1` modes and adds `operational {criterion}` and
`judgment {record}`. Compatibility rule enforced by `compile_map`:
executable→`checks`; operational→`operational`; judgment→`judgment`;
physical→`bench`; context→`context`; `spans` partitions must each satisfy the
rule for their clause. Any other pairing refuses with
`incompatible_evidence_method` naming id and clause locator. `limitation`
remains allowed for any kind and keeps producing the NOT CHECKED limitation.

**Method class** is derived in code from the executed check `kind` (result
row, §1), never from a caller label: `native` (erc, drc, geometry, placement,
board_envelope, fab_profile, fab_package, fab_export, fab_roundtrip,
assembly_readiness, parts_subset, cpl_rotation), `topology` (netlist,
netlist_parity, topology_rules, hardware_interface, net_names), `model`
(interval_assertions, active_spice, rc_transient, dc_path), `measurement`
(measurements, dfm_geometry). Unknown kinds are `unclassified` and count as
not covering any executable clause. `qualification.json.methods` lists unique
executed methods (id, kind, class, evidence sha256) separately from
`coverage` (requirement → mode, bound ids, class set, status).

**Legacy maps.** Superseded by §12.2: `/1` plans keep generating through
every public stage; their method coverage is recorded as legacy/unknown and
current qualification claims are withheld. A plan that supplies
`obligations.json` must use `/2`.

## 5. Engineering judgments

`agent-ee.engineering-judgment/1`, authored, hash-verified:
`requirement`, `clause_text_sha256`, `conclusion`, `assumptions[]`,
`sources[{kind: datasheet|manufacturer|owner|engineering, citation}]`,
`applicability` (structured conditions, e.g. `{"board_thickness_mm": 1.2,
"profile_sha256": "…"}`), `inputs[{path, sha256}]`, `candidate`
(`null` or `{board_sha256, scope_sha256}`), `author {agent, model, date_utc}`,
`unresolved_defects[]`.

Admission at package time (`qualification.py`): every `inputs` entry must
match the captured source inventory or the stage's captured datasheet
records; every `applicability` key must be checkable against captured
inputs (envelope, profile) and must hold; a candidate-independent record has
`candidate: null`, non-empty `inputs` and `applicability`; a
candidate-specific record binds `board_sha256` of the final board and the
scope hash — mismatch is `stale`. Status is `recorded`, never `pass`: the
record is evidence that a named author reached a conclusion under stated
assumptions; its correctness is what `engineering_review` evaluates.

Where candidate-specific judgments come from: run `build run --through
artwork` (checkpoint) or `build review`; the board sha256 is in that
candidate; author the record against it; place it in the plan's declared
`judgments` directory. That directory is excluded from the build `identity`
(no stage before package reads it, so it cannot influence generation) and is
hashed into the package receipt and `qualification.json`. `--resume` to
package then admits it. If artwork→package changes the board bytes the record
is `stale` and `file_release` is `failed`, truthfully.

## 6. Rules register (RD-04)

`qualification.json.rules`: per ERC/DRC check, `ignored_checks` verbatim,
`by_severity`, `constraint_classes`, every default-class advisory with locator
and measured value (from `drc-constraints.json`), waived count, and the
applicable sourced rules evaluated. A missing applicable sourced limit (the
existing `require_evaluated_limits` refusal) marks `file_release failed`. No
regrading; AGENTEE-331/338/348 policy by reference.

## 7. Current decision and history (REC-19/20)

`qualification.json.decision`: board nominal thickness and tolerance source,
height scope, profile identity, envelope hash, judgments by sha256, and
`supersedes[{path, sha256, reason}]` listing `manufacturing-decision.json`
`7db99d74…9be6` as superseded by the 2026-10-24 corrections. Thickness must
equal the `stackup/1` evidence or `file_release` is `failed`. No retained
bytes are edited; no separate decision file is created.

## 8. Bootstrap, run and resume sequence (non-circular)

No step needs a prior accepted result; every gate reads only the run's own
receipts, candidate files and checks-only review.

1. `build run PLAN --out A`: seven public stages; package gate from A's
   receipts; `accepted:true`; `operational: pending` (no interruption
   observed) so `ACQ-DELIV-2` shows pending in coverage. A is complete but not
   the release run.
2. `build run PLAN --out B`, create `B/CANCEL` during placement-routing →
   `status: interrupted`, exit 1; remove `CANCEL`; `build run --resume --out
   B` → completes; B's journal has interrupted→completed under one identity →
   `operational: pass`; `file_release: pass`. B is the M1 release run.
3. Changed-input refusal: alter one source byte, `build run --resume --out
   B2` on a copy of interrupted B → refusal "build resume identity differs";
   the acceptance harness (agent-569) records it as a negative.
4. `build review A --out R`: `accepted:false`, exit 1, `checks` only.
5. Negatives of §1 under the `/2` map: `adc-ties` and `electrical-topology`
   return `defect`, `checks: failed`, `file_release` not reached.

All seven stages remain visible in `completed[]`; only native and
checks-only evidence is used; no prose assertion stands in for a method.

## 9. Implementation slices

| # | Slice | Files | Tests | Depends |
| --- | --- | --- | --- | --- |
| 1 | `qualification.py` + record + build-report/build-review fields + compat matrix | new `src/agent_ee/qualification.py`; `build_workflow.py` | new `tests/test_qualification.py` | 519 fixture |
| 2 | `obligations/1`, map/2 modes, compatibility refusal, kind→class table, legacy/archive rule | `requirement_map.py`, `spec_record.py` (read-side only), `docs/requirement-map.md` | `tests/test_requirement_map.py` additions | none |
| 3 | package integration: operational from journal, judgment admission, rules and decision sections, `judgments` dir outside identity | `build_workflow.py` (`run`, `_package`), `checks.py` expose-only | `tests/test_qualification.py` negatives §8 | 1, 2, 519 |
| 4 | acquisition inputs revision under `/2` with obligations and judgments; runs A/B/B2/R in the supported runtime; dated record under `docs/qualification/` | retained inputs (new revision dir) | native evidence; 569 case | 519, 520, 3 |
| 5 | docs: build-workflow qualification section, CLI exit rows, automation sentence | docs | doc check | 1 |

Untouched: `cli.outcome`, result/1, `verify`, severities, `grade_drc`,
policy files, reviewer routes, retained `build/` bytes.

## 10. Acceptance mapping

ACQ-DELIV-2 by execution+resume → §4 obligations + §8 step 2. Checks-only
never claims review/supplier/hardware, compat tested → §2–3. Judgments with
assumptions/sources/candidate/limits, unresolved defect blocks → §5.
Advisories and ignored categories visible, sourced gates kept → §6. Current
decision at 1.2 mm with history immutable → §7.

## 11. Unresolved question for the chief

Does M1 closure of acquisition require `engineering_review: pass`, or is
`not_assessed` with recorded judgments sufficient for RD-03's "supported
external review when the claim requires it"? The record supports either; the
acceptance case must state which.

## 12. Boundaries settled for integration (revision 3)

### 12.1 Assessment hook, crash behaviour, and the ACQ-DELIV-2 proof

The assessment needs the package receipt, so it does not run inside
`_package`. Hook: in `build_workflow.run`, inside the existing exclusive
lock, immediately after the stage loop's final `write_json(build-report.json)`
(success, checkpoint, failed and interrupted paths alike), call
`qualification.assess(out)`. It reads only committed files: `build-report.json`,
every `completed[]` receipt (`tree_identity` re-verified), `attempts[]`, the
package stage's `review/result.json`, `accepted-package.json`, and the frozen
judgment inventory (§12.3). It writes `qualification.json` at the run root
and then rewrites `build-report.json` with the `qualification` summary.

`accepted` is set only after `qualification.json` is written: the stage loop
sets `status='assessing'`; `assess` returns the file-release verdict; the
report then becomes `accepted/checkpoint` and is written once more. If
`assess` raises (I/O, malformed receipt, inventory mismatch), the report is
written as `status: failed`, `accepted: false`,
`reason: QualificationError: …` with all seven `completed[]` rows intact.
`run --resume` on a report whose `completed[]` already holds all seven stages
re-runs only `assess` (no stage re-executes); the existing `accepted` branch
also re-runs `assess` after `verify`, so the record is always regenerated
from receipts and never trusted from disk. A crash between the two report
writes leaves `status: assessing`, which resume treats like `failed`.

Per-obligation state: `qualification.json.coverage[rid]` = `{obligation,
mode, status, evidence[]}` for every captured clause and derived obligation;
nothing is summarised away. `summary.pending_required = [rid…]` lists every
required obligation not at pass/recorded.

| Run | What the record says | Evidence written |
| --- | --- | --- |
| A, first clean `build run` to package | `file_release: pass`, `claim: "software file obligations only; delivery, judgment and physical obligations are listed under coverage with their own status"`, `operational: pending`, `pending_required` includes `ACQ-DELIV-2`; `accepted: true` | seven `completed[]` receipts; package review result |
| B, `build run`, `CANCEL` during placement-routing, `run --resume` | `operational: pass` with `evidence: {clean_execution: [sha256 of each completed receipt, stages 1–7], resume: {interrupted_attempt: index, resumed_attempt: index, stage, identity}}`; `coverage['ACQ-DELIV-2'].status = pass`; `related_runs: [{run: A, build_report_sha256, identity}]` optional link, never a prerequisite | B's own journal and receipts |
| B2, one changed source byte, `run --resume` on a copy of interrupted B | refusal `build resume identity differs…`; recorded by the acceptance harness as `negatives.changed_input: pass` with the refusal text and the altered path | harness record (agent-569) |

M1 acceptance requires B's `operational: pass` and the B2 negative. A alone
is a file-only claim and its text says so.

### 12.2 Legacy map/1 plans keep generating

`build run` admits a `/1` map and executes every supported public stage as
today. `compile_map` records `method_binding: 'legacy'` for each requirement
(no obligations file exists to bind against). `assess` then writes
`coverage[rid].status = 'unknown'` with
`reason: 'legacy map/1: method binding unqualified'`, `operational` and
`judgments` as `not_assessed (legacy)`, `qualification.mode = 'legacy'`, and
`claims_withheld: true`. `file_release` is still assessed from the existing
`_package` gate, because that gate is unchanged. Historical `verify`,
`inspect` and `build review` are unaffected. Mixing is refused: a plan with
`obligations.json` and a `/1` map, or a `/2` map without `obligations.json`,
refuses before stage one. The M1 input revision is typed and cannot bypass
binding.

| Report fields | Legacy `/1` plan | Typed `/2` plan |
| --- | --- | --- |
| `status: accepted`, `accepted: true` | seven stages completed and the existing package gate passed; `qualification.mode: legacy`, method coverage unknown, current claims withheld | same gate passed **and** `qualification.json` written with `file_release: pass`; coverage per obligation |
| `status: checkpoint`, `accepted: false` | unchanged | `file_release: not_assessed`, checks rows of the stage |
| `status: failed/interrupted` | unchanged | `file_release: failed` or `not_assessed`, reason retained |
| exit code | unchanged (0 accepted/checkpoint, 1 otherwise) | unchanged |

Wording in `docs/build-workflow.md` changes from "accepted native files" to
"software file release under the recorded qualification mode"; prior reports
are reinterpreted by this table, not asserted unchanged.

### 12.3 Judgment side inputs: freeze, mutation and resume

The plan declares `judgments: "judgments/"`. Before `_package` runs, the
package stage copies that directory into `stages/07-package-*/judgments/`
and writes `judgment-inventory.json` {path → sha256} plus the applicability
inventory {envelope sha256, profile sha256, datasheet record sha256s used by
any record}. Both are part of the stage `tree` identity, so they are frozen
by the package receipt. `accepted-package.json` and `qualification.json` bind
`judgment_inventory_sha256`.

Same-run mutation: after `_package`, the existing post-stage check
(`_inventory(source) != source_files`) is extended with the live judgments
directory versus `judgment-inventory.json`; a difference fails the stage like
any other input change. Later change: `run --resume` (and the `accepted`
re-verify branch) recomputes the live inventory and refuses with
`judgment inputs changed after package` — no re-evaluation, no re-acceptance.
A changed or added judgment requires a new run (B′), whose package receipt
freezes the new inventory. The directory is excluded from the pre-package
`identity` only because no stage before package reads it; `assess` asserts
that by refusing if any pre-package receipt lists a `judgments/` path.

### 12.4 Judgments versus engineering review; acceptance witness

Two different things, both recorded:

- `judgments` dimension: every required engineering clause (obligation
  `judgment`) has a source- and candidate-bound record (§5) with empty
  `unresolved_defects`, status `recorded`. Structural validity and
  deterministic checks are never called review.
- `engineering_review` dimension: `pass` only when a full ordinary `review`
  run (≥2 reviews, adjudicated, `criteria_satisfied`, `verify` ok) exists for
  this board hash; otherwise `not_assessed`. M1 does not require it for every
  build.

Acceptance witness for M1 (no prose checker): the verification dispatch
(agent-569) writes one `judgment-verification.json` per record, schema
`agent-ee.judgment-verification/1`: `{judgment_sha256, verifier {agent, model,
date_utc}, checked: [source citations resolved against captured records,
applicability conditions recomputed from captured inputs, conclusion
consistent with the bound checks' measured values], result: concur | dissent,
defects[]}`. `assess` admits witnesses from the package stage's
`judgments/verification/` snapshot (frozen with §12.3) and reports
`coverage[rid].witness = concur | dissent | missing`. M1 acceptance requires
`concur` for every required engineering clause; `dissent` or a non-empty
`defects` is an unresolved required engineering defect and
`file_release` is `failed`. Which clauses are required engineering clauses is
read from `obligations.json`, not inferred.

### 12.5 AGENTEE-524 handoff, single writer

524 owns the check implementations that produce the exact roundtrip and
orientation records (`fab_roundtrip`, `cpl_rotation` kinds in the checks-only
review). 521 owns `_package` wiring and `qualification.py`, which consume
those result rows only. `assess` reports `package_records: {roundtrip,
orientation}` from the rows: `pass`/`failed` when present, `pending (524)`
when absent; an executable obligation bound to either kind blocks
`file_release` while pending. No second writer touches `_package`; 524 lands
its checks and record formats, then 521 flips nothing but the obligations of
the M1 input revision.

### 12.6 Frozen

Revision 3 is frozen for chief integration. Open item from §11 stands;
everything else is settled by the chief's stated decisions.

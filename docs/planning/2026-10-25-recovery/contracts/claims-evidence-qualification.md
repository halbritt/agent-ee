# AGENTEE-521 contract — evidence methods and qualification dimensions

Implementation contract for AGENTEE-521, accepted by the chief integrator
(agent-564) on 2026-10-25. Author agent-565. Input commit
`dab756e56a6a5358c00983074565af0e8e09862c`; decisions RD-03/04/05 in
[recovery-plan.md](../recovery-plan.md); findings REC-06/07/12/19/20/21/26;
constraint preservation per [intent-compatibility.md](../intent-compatibility.md).
Implementation depends on AGENTEE-519 (runtime fixture) and AGENTEE-520 and
is assigned separately by the chief. This document contains no code.

Settled decisions: `build run` checkpoint keeps exit 0; obligation kinds are
authored and source-bound; checkpoints publish per-dimension status and
never assert checks passed because a checkpoint exists; supplier and physical
are record-only pending dimensions in M1; no external upload or bench
workflow; M1 does not mandate a full two-review invocation per deterministic
build, but every required engineering judgment needs an independent
source- and candidate-bound concur witness; the formal `engineering_review`
dimension stays `not_assessed` without actual full-review evidence.

## 1. Current behaviour (source-inspected)

| Surface | Today | Where |
| --- | --- | --- |
| build-report/1 | `status` ∈ running/accepted/checkpoint/failed/interrupted; `accepted` = seven stages completed and `_package` raised nothing; `completed[]` {stage, directory, candidate, tree}, `attempts[]` {stage, directory, status, reason}, `identity`, `inputs` {plan_sha256, source inventory, runtime} | `build_workflow.run` |
| `build run` exit | 0 accepted or checkpoint, 1 otherwise | `build_workflow.execute` |
| `_package` | acceptance-mapped checks `ok/pass`, executed checks ∈ pass/info, one passing `fab_package` bound to the board hash; writes `accepted-package.json` inside the package stage directory | `build_workflow._package` |
| `_review_candidate` | `run_review(checks_only=True)` + `verify` | `build_workflow._review_candidate` |
| build-review/1 | `accepted:false` always; exit 1 always for checks-only (`cli.outcome` needs ≥2 reviews + adjudication); documented, untested | `review_build`, `execute` |
| result/1 check rows | `id`, `kind` (set by the executor from the scope), `execution`, `outcome`, `details`, `limitations` | `checks.run_checks` |
| requirement-map/1 | per requirement one of `checks` / `limitation{reason}` / `bench{criterion,procedure}`, or `spans[]` {byte_start, byte_end, mapping{id, mode…}} forming a byte-complete partition of the clause; prose `context` | `requirement_map.disposition`, `source_mappings` |
| spec-record/1 | byte-identical capture of `spec.md`; clause entries {id, kind, section, locator{byte_start, byte_end,…}, text, text_sha256}; `verify` recomputes the capture, so it cannot carry authored fields | `spec_record.capture/verify` |
| stage receipts | each completed stage's `tree` is `tree_identity(directory)`; resume re-verifies every completed tree | `stage_receipts`, `build_workflow.run` |
| DRC/ERC details | `ignored_checks`, `by_severity`, `constraint_classes`, `advisory_findings_count`, `graded_unwaived`, `drc-constraints.json`; classes owner/manufacturer/datasheet/engineering/default | `checks.check_erc/check_drc/grade_drc`, `design_rule_provenance` |

Retained acquisition inputs (git-ignored, hashed): `requirement-map.json`
`df11c81d…7dc863` has 26 entries = 23 `checks` + 1 `bench` (ACQ-BENCH-1) +
2 `spans` partitions (ACQ-ELEC-6, ACQ-MECH-3); the spec record captures 18
clauses + 1 prose, the remainder are derived envelope obligations.
`ACQ-DELIV-2` → one `netlist_parity` check; `netlist_parity` serves six
requirements. `manufacturing-decision.json` `7db99d74…9be6` still states
1.6 mm nominal and "8 mm including mated connectors". No consumer of
`accepted` exists outside `build_workflow.py`; `docs/cli-reference.md` omits
`build` from its exit table.

Negatives on disk, exact entry points: wrong ADC address,
`build/requirement-clauses/bad-address-mutation.json` (U2 pin 1 GND→V3V3),
rejected by check `adc-ties` kind `topology_rules`, violation `U2-1-6`;
reversed diode, `build/agent545/acquisition-polarity-reversed-review-02`,
check `electrical-topology` kind `topology_rules`, violations `D1-2-1`,
`D1-1-1`. Both execute `checks.check_topology_rules`
(`docs/topology-rules.md`). ERC and parity pass in both.

## 2. Qualification dimensions and record

One module `src/agent_ee/qualification.py`; one record `qualification.json`
(`agent-ee.qualification/1`) at the **run root**, never inside a stage
directory. Dimensions are independent; each has `status` ∈ `not_assessed` /
`pending` / `pass` / `failed`, `evidence[]` (path + sha256 of sealed files)
and `binding` {board_sha256 or null, scope_sha256, stage}. No dimension's
status derives from another's.

| Dimension | pass iff | failed iff | otherwise |
| --- | --- | --- | --- |
| `checks` | the assessed stage executed a review and every executed row is `ok` with outcome pass/info; rows listed with id, kind, outcome | any executed row not ok, or outcome defect/unknown | `not_assessed` for a stage without a review; a checkpoint never implies pass |
| `file_release` | the `_package` gate passed **and** no `operational` clause is `failed` **and** every `judgment` clause is `recorded` with a `concur` witness where required (§5) **and** the rules register has no missing applicable sourced rule (§6) **and** the decision agrees with stackup evidence (§7) **and** required package records are not pending (§8) | gate refused or any condition failed, reason recorded | `not_assessed` at checkpoints |
| `operational` | this run's journal shows all seven stages in `completed[]` with re-verified tree identities and at least one attempt with status interrupted/failed followed by a completed attempt of the same stage under unchanged `identity` | a completed tree or identity check failed during resume | `pending` when no interruption was observed; `not_assessed` before package |
| `engineering_review` | a full ordinary `review` run (≥2 reviews, adjudicated, `criteria_satisfied`, `verify` ok) is referenced and its candidate board sha256 equals this board | that review is `defects_or_unresolved` for this board | `not_assessed` |
| `judgments` | every `judgment` clause has an admitted record (§5) with empty `unresolved_defects` and a `concur` witness | any record stale, mismatched, dissented, or with defects | `pending` when a record or witness is missing |
| `supplier` | not reachable in M1 | — | `pending`; `supplier_preview_performed:false` retained |
| `physical` | not reachable in M1; later only from bench result records per `bench` obligation | — | `pending` per obligation; deferred obligations stay pending with reason |

`accepted` is exactly `file_release.status == 'pass'`. Record sections:
`dimensions`, `coverage` (§4), `methods` (§4), `judgments` (§5), `rules`
(§6), `decision` (§7), `package_records` (§8), `summary.pending_required`
(every required obligation not at pass/recorded), `claim` (fixed text:
"software file obligations only; delivery, judgment, supplier and physical
obligations are listed under coverage with their own status"), `mode`
(`typed` or `legacy`, §4), `related_runs[]` (optional links by build-report
sha256 and identity, never prerequisites).

### 2.1 One-way evidence flow and the assessment hook

Flow is strictly one-way: frozen package evidence → sealed package stage
receipt → run-root `qualification.json` → `build-report.json` summary.
`accepted-package.json` lives inside the package stage tree and therefore
carries **no** reference to `qualification.json`. `qualification.json` hashes
sealed files and is itself referenced only from the build report. Nothing is
written into a completed stage tree after sealing.

Hook: in `build_workflow.run`, inside the existing exclusive lock, after the
stage loop's final build-report write on every path (complete, checkpoint,
failed, interrupted), call `qualification.assess(out)`. It reads only sealed
files: `build-report.json`, every `completed[]` receipt (trees re-verified),
`attempts[]`, the package stage `review/result.json`,
`accepted-package.json`, the frozen judgment inventory and witnesses (§5).
Sequence on a full run: stage loop ends → `status='assessing'` written →
`assess` writes `qualification.json` → report written as
`accepted/checkpoint` with `qualification: {path, sha256, mode, dimensions:
{name: status}}`. `accepted` is never set before `qualification.json` exists.

Failure: if `assess` raises, the report is written `status: failed`,
`accepted: false`, `reason: QualificationError: …`, with `completed[]`
intact. `run --resume` on a report whose `completed[]` holds all seven
stages re-runs only `assess`; the existing `accepted` branch re-runs `verify`
then `assess`, so the record is always regenerated from receipts and never
trusted from disk. A crash leaving `status: assessing` resumes like `failed`.

### 2.2 Reference runs for M1

| Run | Record | Evidence |
| --- | --- | --- |
| A — first clean `build run` to package | `file_release: pass` with the fixed `claim` text; `operational: pending`; `pending_required` includes `ACQ-DELIV-2`; `accepted: true` means file obligations only | seven sealed receipts; package review result |
| B — `build run`, `CANCEL` during placement-routing, `run --resume` | `operational: pass`, `evidence: {clean_execution: [receipt sha256 for stages 1–7], resume: {interrupted_attempt, resumed_attempt, stage, identity}}`; `coverage['ACQ-DELIV-2'].status: pass`; `related_runs` may link A | B's own journal and receipts; no accepted flag is a prerequisite |
| B2 — one changed source byte, `run --resume` on a copy of interrupted B | refusal `build resume identity differs…`; the verification harness records `negatives.changed_input: pass` with the refusal text and altered path | harness record |
| R — `build review A` | `accepted:false`, exit 1, `qualification` with `checks` only | review evidence |
| N — the two topology negatives under the typed map | `adc-ties` / `electrical-topology` return `defect`; `checks: failed`; `file_release` not reached | result rows |

M1 acceptance requires B's `operational: pass` and the B2 negative. B is the
release run; A is a file-only claim and its text says so.

## 3. Compatibility of existing fields and exit codes

| Field / code | Legacy `/1` plan | Typed `/2` plan | Test |
| --- | --- | --- | --- |
| `status: accepted`, `accepted: true` | seven stages completed and the unchanged package gate passed; `qualification.mode: legacy`, coverage `unknown`, current claims withheld | same gate **and** `qualification.json` with `file_release: pass`; coverage per obligation | `accepted ⇔ file_release pass` (typed); legacy record carries `claims_withheld: true` |
| `status: checkpoint`, `accepted: false` | unchanged | `file_release: not_assessed`; real `checks` rows of the stage | checkpoint matrix |
| `status: failed/interrupted` | unchanged | `file_release: failed` or `not_assessed`, reason retained | failure matrix |
| exit code | unchanged: 0 accepted/checkpoint, 1 otherwise | unchanged | exit matrix |
| build-review/1 | `accepted:false`, exit 1; `qualification` added with `checks` only, all else `not_assessed` | same | passing checks-only review stays `accepted:false`, exit 1, checks-only limitation present |
| `accepted-package.json` | unchanged keys; no qualification reference (one-way flow) | same, plus `judgment_inventory_sha256` (§5) | superset check; both `*_performed` flags remain false |
| result/1, `verify`, `cli.outcome` | unchanged | unchanged | existing suites |
| `docs/build-workflow.md` | wording becomes "software file release under the recorded qualification mode"; prior reports are read through this table, not asserted unchanged | | doc check |
| `docs/cli-reference.md` | rows for `build run` and `build review`; automation gates on `accepted` plus `qualification.dimensions` | | doc check |

## 4. Source-bound obligations and evidence methods

### 4.1 Obligations file

`obligations.json` (`agent-ee.obligations/1`) is authored at the requirements
stage beside `spec.md`. It binds **source spans**, not clause ids alone:

```
{ "schema": "agent-ee.obligations/1",
  "spec_sha256": "fbf659b7909af7af4c370ce309c6138c661029261a9a4906237f86be45444373",
  "clauses": [
    { "id": "ACQ-DELIV-2", "spans": [
        { "byte_start": 4612, "byte_end": 4850,
          "text_sha256": "d32419ea258dba5aa8533c575d552655a51d707defefc6aae12868cbf0b5c210",
          "obligation": "operational" } ] },
    { "id": "ACQ-MECH-3", "spans": [
        { "byte_start": 3536, "byte_end": 3703,
          "text_sha256": "3e80d9362048f09ad0db132c375ad001ed74073a08c8e5055a66c8761a559fb7",
          "obligation": "executable" },
        { "byte_start": 3703, "byte_end": 3852,
          "text_sha256": "336f9fb3cb48ae592da9f4c6ff5a53436193a61b068c44a3109905f36467d53c",
          "obligation": "judgment" } ] } ] }
```

`obligation` ∈ `executable` / `operational` / `judgment` / `physical` /
`context`. For each captured clause the spans must form a byte-complete,
ordered, non-overlapping partition of the clause's locator range that does
not split a code point (the existing `source_mappings` span rules); each
span's `text_sha256` must equal sha256 of those spec bytes. A clause with a
single span covering its whole range is the common case. Refusals:
`obligation_missing` (clause without entry), `obligation_unknown_id`,
`obligation_text_drift`, `obligation_partition`. Derived envelope obligations
are `executable` and cannot be partitioned. Prose entries are `context`. No
clause id is special-cased in code.

### 4.2 requirement-map/2 and the compiler contract

`/2` keeps the `/1` modes and adds `operational {criterion}` and
`judgment {record}`. Binding rule, enforced in `requirement_map.compile_map`
and exposed to its consumer:

The first supported operational criterion is the explicit identifier
`public_build_and_resume`, assessed by the journal-and-receipt predicate in
§2. Unknown criterion identifiers refuse as unsupported. Free-form operational
text is source context; a generic successful resume does not prove an arbitrary
operational requirement. `coverage_view` leaves operational and judgment spans
pending until the qualification consumer supplies their bound assessment.

- Each map entry (whole clause or each `spans[].mapping`) is matched to the
  obligation span with the identical `byte_start`/`byte_end`. A map span that
  does not coincide with an obligation span refuses `span_alignment`; a map
  may not merge two obligation spans of different kinds or split one.
- Mode must match the span's obligation: executable→`checks`;
  operational→`operational`; judgment→`judgment`; physical→`bench`;
  context→`context`. Otherwise refuse `incompatible_evidence_method` naming
  id, span bytes and both kinds. `limitation` is allowed for any kind and
  keeps producing the NOT CHECKED limitation (status `failed` for a required
  clause under §2).
- Receipt (`agent-ee.requirement-map-receipt/2`) gains per span:
  `obligation`, `mode`, `bound_ids[]`, `obligation_text_sha256`. The scope
  passed to checks is unchanged in shape.

Consumer API for `qualification.assess`:
`requirement_map.coverage_view(receipt, result_rows) ->
{rid: {spans: [{byte_start, byte_end, obligation, mode, bound_ids,
executed: [{id, kind, class, execution, outcome}], status}], status}}`.
Clause status is the worst span status; span status is `pass` only when
every bound id executed `ok` with pass/info; `operational` and `judgment`
spans take their status from §2 and §5. The `class` is derived in code from
the executed `kind` (never from a caller label): `native` (erc, drc,
geometry, placement, board_envelope, fab_profile, fab_package, fab_export,
fab_roundtrip, assembly_readiness, parts_subset, cpl_rotation), `topology`
(netlist, netlist_parity, topology_rules, hardware_interface, net_names),
`model` (interval_assertions, active_spice, rc_transient, dc_path),
`measurement` (measurements, dfm_geometry); unknown kinds are
`unclassified` and cover nothing. `qualification.json.methods` lists unique
executed methods (id, kind, class, evidence sha256) separately from
`coverage`.

### 4.3 Legacy `/1` plans

A `/1` plan keeps generating through every supported public stage. Without
an obligations file, `compile_map` records `method_binding: 'legacy'`;
`assess` writes `mode: 'legacy'`, every `coverage[rid].status: 'unknown'`
with reason `legacy map/1: method binding unqualified`, `operational` and
`judgments` as `not_assessed (legacy)`, and `claims_withheld: true`.
`file_release` is still assessed from the unchanged package gate. Historical
`verify`, `inspect` and `build review` are unaffected. Mixing refuses before
stage one: `obligations.json` with a `/1` map, or a `/2` map without
`obligations.json`. The M1 input revision is typed and cannot bypass binding.

## 5. Engineering judgments, admission and witnesses

Record `agent-ee.engineering-judgment/1`, authored, hash-verified:
`requirement`, `span` {byte_start, byte_end, text_sha256}, `conclusion`,
`assumptions[]`, `sources[{kind: datasheet|manufacturer|owner|engineering,
citation}]`, `applicability` (structured conditions such as
`{"board_thickness_mm": 1.2, "profile_sha256": "…"}`), `inputs[{path,
sha256}]`, `candidate` (`null` or `{board_sha256, scope_sha256}`),
`author {agent, model, date_utc}`, `unresolved_defects[]`.

Admission (`assess`): every `inputs` entry must match the captured source
inventory or the stage's captured datasheet records; every `applicability`
key must be checkable against captured inputs and hold; a
candidate-independent record has `candidate: null` with non-empty `inputs`
and `applicability`; a candidate-specific record binds the final board and
scope hashes, otherwise it is `stale`. Status is `recorded`, never `pass`: a
typed record proves only that a named author reached a conclusion under
stated assumptions.

Freeze: the plan declares `judgments: "judgments/"`. Before `_package` runs,
the package stage copies that directory to `stages/07-package-*/judgments/`
and writes `judgment-inventory.json` (path → sha256) and the applicability
inventory (envelope, profile and datasheet record hashes used by any
record). All of this is inside the package stage tree, so the stage receipt
seals it; `accepted-package.json` records `judgment_inventory_sha256`. The
directory is excluded from the pre-package `identity` only because no stage
before package reads it; `assess` refuses if any pre-package receipt lists a
`judgments/` path. Same-run mutation after `_package` is caught by the
existing post-stage inventory check extended to the judgments directory and
fails the stage. A later change makes `run --resume` and the `accepted`
re-verify refuse with `judgment inputs changed after package`; there is no
re-evaluation and no re-acceptance; a changed or added judgment requires a
new run whose package receipt freezes the new inventory. Candidate-specific
records are authored after a `--through artwork` checkpoint or a `build
review` exposes the board hash; if artwork→package changes the board bytes
the record is `stale` and `file_release` is `failed`.

Witness: `agent-ee.judgment-verification/1`, one per record:
`{judgment_sha256, verifier {agent, model, date_utc}, checked: [source
citations resolved against captured records, applicability recomputed from
captured inputs, conclusion consistent with the bound checks' measured
values], result: concur | dissent, defects[]}`. The verifier is metadata; the
schema names no agent, and independent acceptance is performed by whichever
agent the chief assigns for M1 verification. Witnesses live in
`judgments/verification/` and are frozen with the inventory. `assess`
reports `coverage[rid].witness = concur | dissent | missing`. For every
required `judgment` span, `recorded` + `concur` is needed for `file_release`;
`dissent`, non-empty `defects` or non-empty `unresolved_defects` is an
unresolved required engineering defect and `file_release` is `failed`.
Judgments and witnesses are never labelled review; `engineering_review` is
the separate dimension of §2.

## 6. Rules register (RD-04)

`qualification.json.rules`: per ERC/DRC check, `ignored_checks` verbatim,
`by_severity`, `constraint_classes`, every default-class advisory with
locator and measured value (from `drc-constraints.json`), waived count, and
the applicable sourced rules evaluated. A missing applicable sourced limit
(the existing `require_evaluated_limits` refusal) marks `file_release
failed`. No regrading; AGENTEE-331/338/348 policy applies by reference.

## 7. Current decision and history (REC-19/20)

`qualification.json.decision`: board nominal thickness and tolerance
source, height scope, profile identity, envelope hash, judgments by sha256,
and `supersedes[{path, sha256, reason}]` naming `manufacturing-decision.json`
`7db99d74…9be6` as superseded by the 2026-10-24 corrections. Thickness must
equal the `stackup/1` evidence or `file_release` is `failed`. No retained
bytes are edited and no separate decision file is created.

## 8. AGENTEE-524 handoff, single writer

524 owns the check implementations producing the exact roundtrip and
orientation records (`fab_roundtrip`, `cpl_rotation` kinds in the
checks-only review). 521 owns `_package` wiring and `qualification.py`,
which consume result rows only. `assess` reports `package_records:
{roundtrip, orientation}` as `pass`/`failed` from the rows or `pending (524)`
when absent; an executable obligation bound to either kind blocks
`file_release` while pending. No second writer touches `_package`.

## 9. Implementation slices

| # | Slice | Files | Tests | Depends |
| --- | --- | --- | --- | --- |
| 1 | `qualification.py`, run-root record, `assess` hook with `assessing` status and failure/resume paths, build-report and build-review fields, compatibility matrix | new `src/agent_ee/qualification.py`; `build_workflow.py` | new `tests/test_qualification.py` | 519 fixture |
| 2 | `obligations/1` with span partitions, map/2 modes, span alignment and kind matching, receipt/2, `coverage_view`, kind→class table, legacy path and mixing refusal | `requirement_map.py`, `spec_record.py` (read side), `docs/requirement-map.md` | `tests/test_requirement_map.py` | none |
| 3 | package integration: operational from journal, judgment freeze/admission/witness, rules and decision sections, package records | `build_workflow.py` (`run`, `_package`), `checks.py` expose-only | `tests/test_qualification.py` negatives of §2.2 | 1, 2, 519 |
| 4 | acquisition input revision under `/2` with obligations, judgments and witnesses; runs A/B/B2/R/N in the supported runtime; dated record under `docs/qualification/` | retained inputs, new revision directory | native evidence; M1 verification | 519, 520, 3, 524 for package records |
| 5 | docs: build-workflow qualification section, CLI exit rows, automation sentence | docs | doc check | 1 |

Untouched: `cli.outcome`, result/1, `verify`, severities, `grade_drc`,
policy files, reviewer routes, retained `build/` bytes.

## 10. Acceptance mapping

ACQ-DELIV-2 by execution and resume → §4.1 obligations, §2.2 run B.
Checks-only never claims review, supplier or hardware; compatibility
documented and tested → §2–3. Judgments with assumptions, sources,
candidate and limits; unresolved defect blocks → §5. Advisories and ignored
categories visible, sourced gates kept, no promotion → §6. Current decision
at 1.2 mm with history immutable → §7.

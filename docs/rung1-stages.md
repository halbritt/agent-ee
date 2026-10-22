# Rung-1 stage-ordered driver

`agent_ee.rung1_stages` (AGENTEE-104) runs one RC candidate through the fixed
rung-1 stage order and records which stages it reached. It spends no inference
and takes no review profile. Each stage either runs its declared
`agent-ee.stage-step/1` steps and is graded by sealed checks, or is recorded as
`not_reached` with the item that owns it. A stage is never recorded as passed
on the strength of a later stage. The record is the precursor run that the
[rung-1 campaign record](generator-roadmap.md#benchmark-ladder) (AGENTEE-96)
later replaces.

The driver composes existing modules: [rung1_qualified](rung1-qualified.md)
preparation and step declarations, [stage receipts](stage-receipts.md),
the [requirement compiler](requirement-scope.md), checks-only reviews and the
[fab_package done gate](fab-package.md). It adds no check kind, step, campaign
code or parser. Its record is `experimental-unqualified`.

## Commands

```sh
python3 scripts/rung1_stages.py run --out RUNDIR --reference fixtures/rc/control.scope.json
python3 scripts/rung1_stages.py run --out RUNDIR --spec fixtures/rung1/spec.md \
  --catalog-checkout CATALOG_GIT_CHECKOUT
python3 scripts/rung1_stages.py run --out RUNDIR --requirement-record
python3 scripts/rung1_stages.py verify RUNDIR
```

`--out` must not exist. Name exactly one source. `--omit-step STAGE`
(repeatable) removes that stage's declarations from the plan, so the stage is
`absent`. `--spec` also takes `--kicad-route` (default
`$AGENT_EE_KICAD_ROUTE_CHECKOUT`, as for `rung1_qualified`) and `--parts-manifest`.
`run` exits 0 only when the record says `done: true`. `verify` exits 0 only when
the record verifies. Either command exits 2 on refused input.

## Stages

| # | Stage | Receipt stage | Owner | Producer in this driver |
| --- | --- | --- | --- | --- |
| 1 | requirements | `scaffold` | AGENTEE-100 | step `compile-requirements`: the requirement compiler on the captured record and spec, artifact-only |
| 2 | parts | `parts` | AGENTEE-101 | none declared (see [Gaps](#gaps)) |
| 3 | schematic | `schematic` | AGENTEE-102 | `rung1_qualified` steps `emit-schematic`, `forward-annotate` |
| 4 | placement | `placement` | AGENTEE-91 | the placement adapter's `agent-ee-placer` helper step |
| 5 | routing | `routing` | AGENTEE-93 | `routing_adapter.step` `kicad-route-builtin`, checked with `routing_adapter.verify` |
| 6 | verification | `verification` | AGENTEE-104 | checks only |
| 7 | fab | `fab` | AGENTEE-94 | checks only; `fab_package` seals exports, followed by any bound `jlc_dfm` checks |

The receipt-stage column is the `stage` field that `stage_receipts` accepts. The
two orders correspond one to one.

## Entry and stop rule

A run starts at an entry stage. `--reference` enters at verification, `--spec`
at schematic and `--requirement-record` at requirements. The content of every
stage before the entry was supplied to the run rather than produced by that
stage's step, so the driver records those stages as `not_reached` with reason
`supplied: ...`.

From the entry on, each stage does the following:

1. Steps. If the stage declares steps, it runs each through
   `stage_receipts.run_step` on the run's design and adopts the result. A
   step that is not adoptable, a refused placement receipt, a failed routing
   verification or a refused adoption makes the stage `failed`. Nothing is
   adopted or substituted.
2. Checks. If the stage has checks, the driver runs `agent-ee review --checks-only`
   on a retained copy of the current design in `reviews/LABEL-source/`. Its
   scope names that copy, so later placement or routing adoptions in `design/`
   cannot change an earlier review's declared inputs. A review that follows no new adoption is reused, so
   verification and fab are graded from the routing-stage review. The run must
   pass `agent-ee verify`. The stage's checks are read from the sealed
   `checks/ID.json` files:

| Sealed results | Stage status |
| --- | --- |
| every check `ok` with outcome `pass` or `info` | `pass` |
| any `ok`/`defect` | `failed` |
| any execution other than `ok`, outcome `unknown`, a missing result or no check | `missing_coverage` |
| review does not verify | `missing_coverage` |

3. Production binding. A `pass` also requires the reviewed candidate to be
   exactly the tree the latest adoptable receipt produced
   (`stage_receipts.applicability` is `applicable`). Otherwise the stage is
   `failed`. This is the decision-1 amendment, applied per stage. It
   [composes](decisions.md#done-requires-each-stage-through-its-declared-step-amends-decision-1)
   the receipt helper rather than the campaign done rule.

A stage with steps and no checks is `completed`, never `pass`. A stage with
neither is `absent`. The first `failed`, `missing_coverage` or `absent` stage
stops the driver, and every later stage is `not_reached` with reason
`stopped at stage N`. No later stage is graded or reported. A later stage's
check may still appear in a shared review: the reference review also seals the
fab check. If verification stops, that fab result is not graded and fab stays
`not_reached`.

Check stages come from the scope. A check's `stage` field places it. In an
unstaged scope, the `fab_package` check is the fab stage and every other check
is verification.

## Done gate

`done_gate` grades the fab stage with `fab_package`, followed only by bound
[`jlc_dfm`](jlc-dfm.md) checks when declared. The driver preserves that order and
groups both kinds in the fab stage unless their stage is explicit. A scope
that already contains this package and its bound DFM consumers is used unchanged. Otherwise its
`fab_export` and `fab_consistency` checks become one `fab_package` check with
the export check's ID, and requirements that named them name it instead. A
scope/2 or scope/3 also gains the generation marker, which baseline compilation
then enforces. `done` is true only when every stage is `pass` or `completed`,
fab is `pass`, and its `fab_package` result is `pass`.

## Record

`RUNDIR/rung1-stage-record.json` uses `agent-ee.rung1-stage-record/0-experimental`
and holds the following:

- For each stage: number, name, receipt stage, owner, status and reason; the receipt
  summaries (path, receipt identity, status, exit, violations, outputs,
  artifacts, tool identity and reported version); the review run and its
  command; each graded check's execution, outcome, summary, path and SHA-256;
  and the production binding.
- `fab_manifest`: the done check's execution and outcome, its sealed
  `details.artifacts` record and the `manifest.json` hash, when a package exists.
- `agent_ee`: the commit and tracked changes, `tools` (Python, `kicad-cli`,
  ngspice and Bubblewrap versions) and `supplied` (reference scope, spec, preparation
  receipts, scaffold, scope validation, kicad-route identity and preflight, and
  omitted stages).
- `files`: SHA-256 of every bound file. These are the receipts, the review
  `result.json`, `candidate.json`, evidence seal and graded check results, and
  `scope.json` and `scaffold.json`.
- `record_id`: the identity of the record without it.

`verify` recomputes everything:

- the record identity and every bound hash, and that the bound file set
  follows from the stages;
- that the current agent-ee commit and tracked changes equal the recorded ones;
- each receipt, through `stage_receipts.validate_receipt`;
- every review recorded as sealed, through `agent-ee verify`, including reviews
  whose checks failed or left missing coverage; this recomputes both retained
  source and captured input hashes. Passing stages are regraded from sealed results;
- the stop rule and the `done` flag.

A forged `pass` without a review, a stage reached after a stop, an edited
sealed check, a changed receipt or another commit is reported as a problem.
Edited or missing files in either `reviews/LABEL-source/` or the review's
`inputs/` invalidate that review. Changes to the working `design/` alone do
not invalidate earlier stage evidence. Keep the run at its recorded absolute
path: the retained scopes use absolute source paths.

## Recorded runs (2026-10-18, base b3a6eb5)

Run from this branch with KiCad 10.0.7, ngspice and Bubblewrap on the host.

**Reference board** (`--reference fixtures/rc/control.scope.json`, about 35 s).
Stages 1-5 are `not_reached` (supplied) with owners AGENTEE-100, -101, -102,
-91 and -93. Verification is `pass`: `erc`, `drc`, `geometry` and `rc-step` pass,
and `netlist` is `info`. Fab is `missing_coverage`: `fab_package` is
`unsupported/unknown` with `BOM has parts without a complete
LCSC/manufacturer/part-number binding: C1, J1, R1`. The 2026-10-17
checks-only evidence called this outcome a defect. Since AGENTEE-23, `fab_export`
classifies an unbound BOM line as unsupported, and the ticket's own rule makes
an execution other than `ok` missing coverage. `verify` exits 0.

**Requirement record** (`--requirement-record`). Stage 1 `compile-requirements`
completes. Its `compiled/scope.json` artifact is byte-identical to
[fixtures/rung1/compiled.scope.json](../fixtures/rung1/compiled.scope.json).
Parts is `absent`, and stages 3-7 are `not_reached`.

**Generation** (`--spec fixtures/rung1/spec.md` with the test-authored catalog
from `tests/test_generate.py`). See [Gaps](#gaps) for the live fome-mc BOM.
Preparation, the scaffold and the staged scope are `rung1_qualified`'s, and the
scope's fab stage is the `fab_package` done gate. Stages 1 and 2 are
`not_reached` (supplied). Schematic is `pass`: `emit-schematic` and
`forward-annotate` completed, `erc` and `parity` pass, and `netlist` is `info`.
The graded candidate is exactly the forward-annotate receipt's output. Placement is
`failed`. The `agent-ee-placer` step completed with exit 0, but
`placement_adapter.qualify_receipt` refused its proposal: `placement helper
proposal outside placement scope: footprint ... (J1): property ... changed beyond the
footprint transform`. The refused properties are hidden binding fields such as
`JLCPCB_Assembly` and `datasheet_obligation`. Their `(at 0 0 0)` stays at the
board origin while the footprint moves. Routing, verification and fab are
`not_reached`. `rung1_qualified --dry-run` at the same commit stops on the same
refusal, so the defect is on main, not in this driver. It belongs to the
placement adapter, forward annotation or the placer (AGENTEE-91, AGENTEE-52,
AGENTEE-54). `verify` exits 0.

## Gaps

- **Parts has no declared step.** `agent_ee.generate` stage 2, or `rung1_parts`
  with `--parts-manifest`, selects parts in-process during `rung1_qualified`
  preparation. So a run from requirements stops at parts, and a generation run
  enters at schematic with stages 1 and 2 supplied. The stage-1 compiled scope
  is recorded, but the generation run grades with the `rung1_qualified` staged
  scope. Moving the drivers onto a declared producer is AGENTEE-134.
- **Live catalog.** On 2026-10-18 the live fome-mc BOM refused at preparation
  (`catalog columns differ from Comment,Designator,Footprint,LCSC Part #,Quantity`).
  The driver then records every stage `not_reached` with the setup error.
- **No reviewers.** Reviews are checks-only. The record carries no independent
  review or adjudication, and nothing in it is campaign acceptance.

Each checks-only review declares a fixed copy of that stage's source under
`review-sources/LABEL/`. Later placement or routing writes to `design/` leave
those earlier sources intact. Historical verification uses the ordinary source
and snapshot checks, including rejection of edits to a retained review source.

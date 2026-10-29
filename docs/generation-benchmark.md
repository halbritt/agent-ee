# Frozen generation benchmark

`python3 -m agent_ee.generation_benchmark` runs offline generator trials and
scores native outputs through the existing candidate, baseline and check runner.
It adds no check kind or model judge. This measures declared generation
requirements; it establishes no fabrication or bench validity.

AGENTEE-69's ten unlicensed upstream prompts are **kept private and not
committed**. The relayed decision (owner-relayed, agent-drafted record, recorded
as a Plane comment on AGENTEE-69) reads verbatim:

> "Owner decision (2026-10-22, owner via agent-519 decision quiz): KEEP PRIVATE: the ten unlicensed tscircuit prompts are not committed. Accept the committed original prompt series as the substitute and amend criterion 2 accordingly."

No upstream prompts, rule files, code, netlists or non-MIT fixture set were
imported. The committed original prompt series under
`fixtures/generation-benchmark/` (currently the `original-series` task) is
original agent-ee material, made with the owned topology fixture generator. It
is the accepted substitute for the ten prompts. Golden-netlist equivalence
scoring is not implemented.

AGENTEE-69 criterion 2 therefore reads, as amended under that decision:
*importing the committed original prompt series as scope/2 or scope/3 baselines
gives every acceptance criterion at least one check ID, or scope/3 refuses it.*
`test_committed_original_series_maps_every_acceptance_criterion` validates every
committed task against it, and
`test_scope3_refuses_unmapped_acceptance_and_scoring_gaps` covers the refusals.

## Frozen tasks

A task directory contains `task.json`, `pins.json`, a public prompt, a reference
schematic or board used only for design grouping, and an `evaluator/` directory.
`pins.json` (`agent-ee.generation-pins/1`) maps every other file to its SHA-256.
Changed, missing or additional files refuse the run before generator execution.
Links, multiply linked files and special files are refused. Prompt and reference
paths must be canonical relative paths. The prompt must be exactly `prompt.md`
and separate from the reference. A top-level copy of evaluator rules cannot
be selected under another prompt name. The existing file hashes also refuse a
`prompt.md` whose bytes equal any evaluator input (including nested files) or
the reference, before generator execution or evaluation. This detects exact
copies only; task authors must still review paraphrases, excerpts and other
public prompt contents.

Pass `run --expect-pins SHA256` to compare `pins.json` against a digest retained
outside the task directory before any generator execution. The Python
`pinned_task`, `evaluate` and `run_task` entry points accept `expect_pins` too.
With this execution anchor, coordinated task edits and repinning are refused.
Obtain the digest at the freeze boundary and retain it independently; computing
it from an untrusted task immediately before execution supplies no authenticity. Without this
option, pins remain local task-author inputs. The authenticated series freeze
below requires external task pins even when a diagnostic run omitted them. Every repair attempt also
checks the pins digest observed before the first generation.

`task.json` (`agent-ee.generation-task/1`) declares:

- `id`, `domain`, `difficulty`, `prompt`, `reference` and `deliverables`;
- an ordinary scope/2 or scope/3 `scope` with empty `inputs`; the harness supplies
  only the evaluator files and declared generated deliverables at evaluation;
- `rules`, each with `id`, `check`, `category`, boolean `critical`, positive
  integer `weight`, `source_criteria` and required `deliverables`.

Each scoring rule names a configured check and criterion IDs already mapped to
that check by the baseline. Every criterion and deliverable needs a scoring
rule. Prerequisite checks such as informational `netlist` remain baseline-bound
without being independently scored as acceptance passes. Baseline compilation
is the existing implementation; scope/3 retains its executable coverage audit.
Task authors must review the substantive adequacy of criterion mappings.

The original task runs native ERC first. Its verification stage uses the
existing native presence gate for R1/R2 and the endpoint connectors before netlist and
topology checks.
An empty schematic can therefore pass ERC and still fail component conformance.
An unsupported native reader or tool failure leaves unknown coverage. This does
not change the unstaged netlist check's refusal of an empty export.

## Generator and evaluation boundary

The generator is a local Python script invoked as:

```sh
/usr/bin/python3 /candidate/generator.py /candidate/prompt.md /work/output
```

The existing executor exposes only that script and prompt under `/candidate`,
with writable `/work` and no network. Evaluator rules, the reference schematic,
other attempts and candidate baselines are not mounted into that sandbox.
On a repair attempt, `/work/previous` holds the previous outputs, and
`/work/feedback.json` gives only check status and missing deliverable names.
The maximum is one initial attempt and two repairs. A successful generation
whose declared rules all pass stops early. Every attempted generator execution,
output and evaluation is retained in its own directory. `trial.json` records
the generator hash and all evaluation report paths.

The coordinator copies evaluator inputs and declared outputs into an ordinary
candidate, captures its immutable baseline, runs its checks, and seals and
verifies the evidence. `run_task` copies the executor's `execution.json`, stdout,
stderr and console logs into `evaluation-N/generation/`, verifies their digests,
and derives generation execution from the executor record. These bytes also
enter the captured candidate under the reserved `.generation/` prefix, binding
the executor record through the candidate identity into sealed check evidence.
Task evaluator inputs and deliverables cannot use that prefix. Missing or unsafe
outputs retain the executor records without claiming a captured candidate.
Detailed native results remain under the attempt's
`evaluation-N/run/`. Generator `result.json` claims are never evidence.

Generator `execution=failed` is separate from each check's execution and
outcome. A failed generator can still have usable outputs with known check
results. The report's `result` pairs `generation_execution` with a combined
`overall_pass`: failed generation makes this false, otherwise it preserves the
check score's true/false/null. `scoring.overall_pass` keeps its check-only meaning.
Missing deliverables fail every rule requiring them; remaining rules
stay unknown when no complete candidate can be captured. Malformed generated
inputs that cannot be captured produce unknown scoring and a capture diagnostic.
Links, hardlinks, special files and non-directory output roots instead record
`generation.execution=failed` with `output_error=non_ordinary_output`, no native
checks, and unknown scores. Those unsafe outputs remain in their attempt
directory but are never copied into repair inputs. Unsafe entries are not read
or hashed; the report binds the rejection class. Unexpected harness exceptions
propagate; they cannot become passes.

## Source-bound expected refusals

An opt-in variant of the existing `agent-ee.generation-task/1` uses the same
common `id`, `domain`, `difficulty`, `prompt`, `reference` and `deliverables`.
It replaces `scope` and `rules` with:

```json
{
  "deliverables": ["refusal.json"],
  "expected_refusal": {"contract": "evaluator/refusal-contract.json"}
}
```

The contract is private, canonical task-relative, under `evaluator/`, and pinned
by the ordinary task pins. Native reference grouping stays mandatory; this
variant does not invent a reference board or infer a design pass. Old readers
reject this additional task variant; ordinary native tasks/reports retain their
existing fields and grading behavior.

The independently authored private JSON contract has exactly these fields:

- `outcome`: exactly `unresolved`;
- `code`: exactly `unsupported_clause`;
- `brief_sha256`: SHA-256 of the exact public `prompt.md` bytes;
- `byte_start`, `byte_end`: strict integer UTF-8 byte offsets selecting one
  complete, nonblank, trimmed clause line, excluding indentation/trailing space;
- `clause_sha256`: SHA-256 of those exact selected bytes;
- `context_files`: a nonempty map of canonical `evaluator/` file paths to their
  pinned SHA-256 values. Each file must be nonempty and distinct from the contract.

Task authors freeze the unsupported clause and applicable capability/source
context **independently before execution**. The evaluator validates these
bindings and never calls the producer's `parse_brief` to choose an expectation.
The contract does not prove its curator chose a substantively unsupported
request: independent task review and external pin custody remain necessary.
No private case, task library or answer corpus is added by this interface.

The sole permissible ordinary output tree is the one file `refusal.json`.
Its exact JSON fields are `outcome`, `brief_sha256`, and one-element `findings`.
That finding has exactly `code`, `message`, `byte_start`, `byte_end`; its message
must equal `unsupported or ambiguous brief clause: ` followed by the frozen
UTF-8 clause. Offset booleans/floats, generic reasons, invented clauses, unknown
fields, extra findings, wrong hashes/codes/outcomes and preparation/release
claims fail. JSON whitespace/key order are immaterial. This matches the existing
`acquisition_prepare.PreparationRefused` report, including the brief digest
added by `prepare`. Unsupported clause is **unresolved**, not an assertion of
physical impossibility.

A producer adapter may catch that specific `PreparationRefused`, publish the
response and intentionally exit0 to complete its requested transaction. This
feature adds no adapter or transport. The existing preparation CLI exits1 for
this outcome; that nonzero execution still fails overall even with a correct
response. Arbitrary crash, timeout, unavailable execution or missing output
cannot become successful refusal. Missing outputs still count, every begun
attempt remains in its case ledger, and the campaign still explicitly selects
`--attempts 2`. Correct source refusal is never board completion.

Refusal evaluation uses local evaluator source/Python identities and no native
runtime probes or native candidate/check seal. One fixed critical source-refusal
criterion uses the existing exact score machinery. Reports explicitly carry
`kind: source_refusal`; they provide no board verdict. Source-only or mixed
aggregate rows carry `kind` and separate `source_refusal` from `native_design`
before domain/difficulty grouping. Native-only summaries keep their old row shape.

For this branch, `evaluation-N/outputs/` retains **every ordinary file and
directory**, including empty `release/` or `prepared/` directories and extra
files. Any such extra entry fails; it is retained as failure evidence. The
report binding's `output_tree` contains all file hashes and the complete
relative entry/type census. Capture checks the copied snapshot and rechecks
the original output; replay rechecks the entire retained tree and recomputes
the grade from raw bytes and the pinned private contract. Unsafe links,
hardlinks or special entries retain the existing rejection class and original
attempt output; they are never followed/copied or accepted as evidence.

The existing evaluation receipts, exclusive report publication, externally
pinned `freeze` and authenticated series replay apply. `evidence_sha256` is
null for source refusal because it has no native seal; this is accepted only
through the explicit refusal replay branch. Direct caller-only generation
claims remain diagnostic and cannot freeze. Case verification also replays
every retained evaluated refusal attempt against its receipt, including earlier
failed/missing-output attempts, so deleting extra output or empty directories
from an earlier refusal snapshot is detected. Cached passes, coordinated local
report rewrites contradicting raw evidence, and mutations after external freeze
are refused. Without independent task/series anchors, coordinated local
re-authoring remains outside the diagnostic consistency guarantee.

Pure public-fixture success is neither native/G4 qualification nor campaign,
board, manufacturing or release acceptance. Live generation, private task
selection, native case/descendant containment proof and independent engineering
review remain separately authorized work.

## Enclosing case budget

`run` and `run_task` use one monotonic case clock across setup, generation,
checks, publication and retries. `--timeout` remains a per-generation and
per-evaluation ceiling; it never extends the enclosing deadline. Evaluation's
ceiling now starts before its input/runtime capture, rather than after capture.
The additional public options (Python keywords use underscores) are:

| CLI option | Default | Meaning |
| --- | --- | --- |
| `--case-timeout` | `2 * attempts * timeout` | Positive integer seconds for the entire case, at least 2. |
| `--grading-reserve` | `timeout` | Integer seconds unavailable to generation; at least 1 and less than case timeout. |
| `--max-work` | `2 * attempts` | Integer phase-admission credits, at least 2. |

The original attempts range/default, 1..3/default3, remains. The ZeroASIC
campaign explicitly supplies `--attempts 2`; it is not silently inferred.
Choose calibrated case/reserve values explicitly for qualification. Very small
budgets can refuse before launch: native runs retain their existing one-second
launch floor and checks retain their five-second minimum admission window.

A generation credit covers the **whole sandboxed generation subtree**, including
its preparation, native build, review and internal repairs. A grading credit
covers one evaluation phase, including its runtime probes and checks. This is
a trusted coordinator phase counter, not a count of internal native calls,
provider calls or tokens. Those measurements remain unknown without a separately
qualified live adapter; this feature adds no provider integration. Credits are
charged before phase work and are never refunded after failures/timeouts.
Generation needs two remaining credits, preserving the last credit for grading.
An odd work cap can therefore leave an unused final grading credit.

Every generation is clamped to original `start + case_timeout - grading_reserve`.
Grading can use the remaining original case deadline, subject to its own timeout
ceiling. An intermediate grade can consume that final reserve, but then no repair
is admitted. A child/retry cannot obtain a fresh allowance. Native setup keeps
the absolute cutoff, including a late sandbox/host launch; nested native calls
on the same/copied context cannot widen its deadline or replace its cancellation
handle. The supported generation sandbox's PID namespace contains subprocesses;
this is not a claim to contain arbitrary detached host workloads.

The API additionally accepts `cancel=executor.Cancellation()` in `run_task`,
`evaluate` and `checks.run_checks`. `evaluate(..., deadline=ABSOLUTE_MONOTONIC)`
clamps its whole operation to the earlier of that deadline and its timeout.
`native_run.using(deadline=..., cancel=...)` scopes these existing native limits
without changing current adapter routing; explicitly supplied adapters still
work as before. Delegated checks retain the same cancellation handle. Neither
context variables nor this credit counter are a cross-process token meter.

`trial.json` retains its `agent-ee.generation-trial/1` schema and existing
`generator_sha256`/`evaluations` fields. It additionally records task pins,
limits, original deadlines, elapsed time, work/attempt admissions and an ordered
`attempt_records` list. Each attempt is persisted **before preparation**; its
actual generation status, error and nullable evaluation report remain recorded.
Final status is `completed`, `attempts_exhausted`, `budget_exhausted`, `cancelled`,
`interrupted` or `error`. Count started attempts from `attempts_started`, not the
number of reports: exhaustion can return an empty report list or leave an
incomplete final attempt. Unexpected exceptions/interrupts still propagate after
retaining that ledger. Scratch is never reused to restart the clock.

File capture/hashing and terminal metadata use cooperative deadline checkpoints;
a filesystem call already in progress and existing process cleanup/output-drain
grace can finish past the cutoff. They grant no further productive work. Late
grading/publication never supplies case success. A separate externally bounded
native qualification must demonstrate actual elapsed/descendant cleanup bounds;
this source feature does not claim hard real-time preemption of Python/file I/O.

New run-task reports bind their case/attempt in the existing evaluation binding.
The existing series/1 entry optionally adds `case_files`: the ledger plus records
for **all** started attempts, not only the selected one; unavailable records are
explicit nulls. Frozen series verification detects edits/deletions to earlier
failed attempts and the ledger. A selected passing report is refused while its
case is incomplete/exhausted/late. Historical reports without case bindings keep
the previous entry shape and verification; no prior evidence is rewritten.
Aggregation still counts one selected report per task, while the case ledger
retains the attempt denominator and selection history. It does not turn missing
reports into success. Expected-refusal evaluation below uses the same enclosing budget.

For example, a root-authorized invocation can add these options to the ordinary
`run` command, retaining its task/generator/source/runtime pins:

```sh
--attempts 2 --timeout 120 --case-timeout 400 --grading-reserve 60 --max-work 4
```

These example numbers are configuration, not measured native qualification.

## Scores and immutable reports

A check contributes pass/fail only for `ok/pass` or `ok/defect`. Informational,
unsupported, malformed, missing and failed checks score unknown. Any unknown
rule keeps its category score and overall numeric score null. Lower bounds
count known passes; upper bounds also count unknown weights. Fractions use
exact integer numerator/denominator pairs. A known critical failure forces
`overall_pass=false` even when another rule is unknown.

Evaluation directories use a SHA-256 identity of task files, selected output
hashes, evaluator source files, observed native executable identities, Python
version, timeout, generation execution and retained executor-record hashes. The runtime observation uses the
existing fingerprint and pcbnew-extension helpers. External shared libraries
and undeclared adapter installations are not a complete runtime closure;
qualification across different hosts needs a pinned runtime image or additional
captured dependencies. The benchmark does not provision an image.

`report.json` contains only stable check projections and exact scores; native
logs, durations and attempt paths stay in retained evidence. Direct `evaluate`
calls without executor records retain the deterministic output-only identity.
For `run_task`, the binding also hashes executor records, which contain duration
and sandbox paths; separate executions can therefore have different report
identities even for identical native outputs. An existing
report with different bytes is an error and is never overwritten. Each retained
`evaluation-N/evaluation.json` binds its report path and digest. A separate
`trials/` index beside the report locates these records without adding volatile
paths to the deterministic report. Each evaluation also retains a frozen `task/`
copy. Aggregation requires the identity directory and a retained evaluation
record with the matching report digest. It verifies the task file binding,
evaluation inputs, candidate snapshot, immutable baseline and sealed check
artifacts, then recomputes rule scores and the combined result. Isolated report
edits are refused. Coordinated scoring rewrites of the report, `trials/` receipt
and `evaluation.json` are also refused when they contradict the retained sealed
check evidence. Aggregation launches no native tools. Missing or damaged evidence
cannot establish a pass. Older trials without a retained task must be reevaluated.

## Authenticated series freeze

Local consistency checks alone cannot authenticate a trial-store writer. Freeze
trusted trials **before** handing the store to an untrusted writer. The `freeze`
command requires `--expect-pins`, a JSON map of task IDs to independently
retained `pins.json` digests, and one selected report per task. Missing, wrong or
extra task pins are refused. Direct `evaluate` calls with only a caller-supplied
`generation_execution` are diagnostic records and cannot be frozen; an actual
retained executor record is required. `run_task` supplies it automatically.

The resulting `agent-ee.generation-series/1` manifest has an `evaluations` list.
Each entry binds the report identity and SHA-256, task pins digest, evidence-seal
digest (null for capture failure) and all retained executor-record hashes.
`freeze` writes a new manifest file exclusively and prints its SHA-256. Retain
that digest independently of the trial store. The manifest can travel with the
store because its independently retained digest authenticates it. The task pins
map and series digest must come from trusted custody; reading them from the
same untrusted store immediately before verification gives no authenticity.

Authenticated aggregation requires both `--manifest` and `--expect-pins`, where
aggregate's `--expect-pins` is the **series manifest digest**. Either flag alone
is refused. Summaries carry `authentication`: `{series_sha256}` when anchored,
otherwise `diagnostic-unauthenticated`. The selected reports must match the complete frozen series exactly;
subsets, duplicates, changed identities and extra reports are refused. Aggregate
checks the manifest digest, report digest, evidence-seal digest and executor
records, then performs the ordinary candidate/baseline/evidence replay.

This closes the review's three attacks under the external-custody boundary:

- **P1:** rewritten checks resealed with `baseline.freeze_evidence` change the
  externally anchored seal or report digest and are refused.
- **P2:** flipping a report's generation claim contradicts the retained executor
  record. Editing that record as well changes its candidate/report binding and
  the externally anchored series, so authenticated aggregate refuses it.
- **P3d:** editing retained domain or difficulty, repinning and rebinding changes
  the report identity/digest and is refused before re-stratification.

External digests were chosen over rerunning native checks to authenticate the
observed trial, including generation execution and grouping metadata, without
repeating tool execution or requiring the original runtime at aggregate time.
No new dependencies, network access or owner evidence store are introduced.
The freeze boundary assumes trusted evaluation and task-pin custody; it cannot
detect forgery committed before the first trusted freeze. An attacker who can
replace the independently retained digest is outside this threat model.

Legacy `aggregate` without either flag remains a diagnostic local consistency
check with its existing verdicts. It has no authenticity claim and still permits
coordinated local resealing and re-stratification. A self-consistent local attack
must also rewrite the mutable case ledger's status, task pins and selected
report paths where those claims change; partial forgeries can be caught by the
case-ledger guard before the external-anchor check. The adversarial native
fixtures retain both that early refusal and the fully rebound diagnostic attack
before proving the independently retained series still rejects it.
Authentication is mandatory
at the explicit `freeze` boundary and when consuming a frozen series, rather
than silently changing existing diagnostic pass/fail/unknown results. Unknown
scores remain unknown even when their evidence is authenticated. Older trials
without retained executor records must be rerun before freezing.

```sh
PYTHONPATH=src python3 -m agent_ee.generation_benchmark run \
  --task fixtures/generation-benchmark/original-series \
  --expect-pins EXTERNALLY_RETAINED_PINS_SHA256 \
  --generator /absolute/path/to/generator.py \
  --scratch build/generation-trial --store build/generation-evaluations
```

Scratch must be a new directory. The evaluation store is a caller-selected
local directory, not an owner evidence store created by this feature.

## Design splits and summaries

`split` uses the shared design-diff structural schematic or board fingerprint, which
ignores placement and UUIDs. All aliases with an identical fingerprint go to
the same seeded split (approximately 20% test). This deliberately conservative
projection can group distinct designs with identical component/net-name
summaries; it does not establish electrical equivalence or identify every
near-duplicate. It never distributes an identical fingerprint across splits.

An exclusion list (`agent-ee.generation-exclusions/1`) carries a nonempty
`version` and a `tasks` map of task ID to reason. Excluding one design excludes
all fingerprint aliases, and the report lists each excluded task and reason.
Unknown exclusion IDs are errors. No third-party exclusion list was copied.

```sh
PYTHONPATH=src python3 -m agent_ee.generation_benchmark split \
  --exclusions fixtures/generation-benchmark/exclusions.json --seed 17 \
  fixtures/generation-benchmark/original-series
# external-task-pins.json: {"original-series": "TRUSTED_TASK_PINS_SHA256"}
PYTHONPATH=src python3 -m agent_ee.generation_benchmark freeze \
  --expect-pins /trusted/external-task-pins.json --manifest build/series.json \
  build/generation-evaluations/IDENTITY/report.json
# Retain the printed manifest digest independently before exposing the store.
PYTHONPATH=src python3 -m agent_ee.generation_benchmark aggregate \
  --manifest build/series.json --expect-pins TRUSTED_SERIES_SHA256 \
  build/generation-evaluations/IDENTITY/report.json
```

The aggregate JSON table stratifies by domain and difficulty, counts known
passes, failures, unknowns and generation failures, averages exact score bounds
and category scores (remaining null on unknown), and refuses duplicate task
IDs. Select one attempt per task explicitly; never cherry-pick repeated attempts
as independent tasks. Pass/fail/unknown counts use the combined result, so a
recorded failed generation is never counted as passed. Authenticated series
verification binds that status to the frozen executor evidence. Numeric bounds
continue to describe check grading separately from generation execution.

## Validation

`tests.test_generation_benchmark` exercises pinned-file changes, criterion
coverage refusal, exact unknown bounds, saved-record conflicts, split aliases
and exclusions, real native conformance, empty ERC-clean output, malformed
outputs, unsafe output retention and repair isolation, failed generation,
aggregate trial bindings, all six private-prompt bypass spellings, and the
networkless generator visibility boundary with exact public-prompt bytes.
AGENTEE-278 regressions cover external pin mismatches after repinning, the
`prompt.md` allow-list and exact private-input copies, board-only pinning and
splits, coordinated scoring rewrites against retained sealed evidence, damaged
sealed evidence and failed generation with passing outputs. AGENTEE-285 adds
failing-then-passing P1/P2/P3d attack regressions, real executor retention in the
candidate, mandatory external task pins at freeze, exact series selection, CLI
freeze/aggregate, changed report/seal/manifest refusal and authenticated unknown
results that never pass.
Use `AGENT_EE_REQUIRE_TOOLCHAIN=1` to require those native tests without skips.

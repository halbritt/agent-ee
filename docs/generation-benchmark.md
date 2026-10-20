# Frozen generation benchmark

`python3 -m agent_ee.generation_benchmark` runs offline generator trials and
scores native outputs through the existing candidate, baseline and check runner.
It adds no check kind or model judge. This measures declared generation
requirements; it establishes no fabrication or bench validity.

AGENTEE-69's ten unlicensed upstream prompts are **blocked on owner permission**.
No upstream prompts, rule files, code, netlists or non-MIT fixture set were
imported. The committed `fixtures/generation-benchmark/original-series` task is
original agent-ee material, made with the owned topology fixture generator.
It is a qualification example, not a substitute claim that the requested ten
prompts were imported. Golden-netlist equivalence scoring is not implemented.

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
coordinated local resealing and re-stratification. Authentication is mandatory
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

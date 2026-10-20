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
schematic used only for design grouping, and an `evaluator/` directory.
`pins.json` (`agent-ee.generation-pins/1`) maps every other file to its SHA-256.
Changed, missing or additional files refuse the run before generator execution.
Links, multiply linked files and special files are refused. Prompt and reference
paths must be canonical relative paths. The prompt cannot name `task.json`,
`pins.json`, the reference, or any file under `evaluator/`, including alternate
spellings such as `./evaluator/rules.json`. Pins are local task-author inputs,
not an external authenticity anchor: repinning defines a different task binding.

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
verifies the evidence. Detailed native results remain under the attempt's
`evaluation-N/run/`. Generator `result.json` claims are never evidence.

Generator `execution=failed` is separate from each check's execution and
outcome. A failed generator can still have usable outputs with known check
results. Missing deliverables fail every rule requiring them; remaining rules
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
version, timeout and generation execution. The runtime observation uses the
existing fingerprint and pcbnew-extension helpers. External shared libraries
and undeclared adapter installations are not a complete runtime closure;
qualification across different hosts needs a pinned runtime image or additional
captured dependencies. The benchmark does not provision an image.

`report.json` contains only stable check projections and exact scores; native
logs, durations and attempt paths stay in retained evidence. Identical outputs
on an unchanged task and runtime produce byte-identical reports. An existing
report with different bytes is an error and is never overwritten. Each retained
`evaluation-N/evaluation.json` binds its report path and digest. A separate
`trials/` index beside the report locates these records without adding volatile
paths to the deterministic report. Aggregation requires the identity directory
and at least one retained evaluation record with the matching report digest.
Handwritten reports, unrecorded reports and changed report bytes are refused.
These local records detect accidental or isolated report edits; they do not
authenticate an author who can rewrite both reports and retained trial records.

```sh
PYTHONPATH=src python3 -m agent_ee.generation_benchmark run \
  --task fixtures/generation-benchmark/original-series \
  --generator /absolute/path/to/generator.py \
  --scratch build/generation-trial --store build/generation-evaluations
```

Scratch must be a new directory. The evaluation store is a caller-selected
local directory, not an owner evidence store created by this feature.

## Design splits and summaries

`split` uses the shared design-diff structural schematic fingerprint, which
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
PYTHONPATH=src python3 -m agent_ee.generation_benchmark aggregate \
  build/generation-evaluations/IDENTITY/report.json
```

The aggregate JSON table stratifies by domain and difficulty, counts known
passes, failures, unknowns and generation failures, averages exact score bounds
and category scores (remaining null on unknown), and refuses duplicate task
IDs. Select one attempt per task explicitly; never cherry-pick repeated attempts
as independent tasks. Generation execution and check grading are separate
columns, so a check pass is not a claim that the generator completed successfully.

## Validation

`tests.test_generation_benchmark` exercises pinned-file changes, criterion
coverage refusal, exact unknown bounds, saved-record conflicts, split aliases
and exclusions, real native conformance, empty ERC-clean output, malformed
outputs, unsafe output retention and repair isolation, failed generation,
aggregate trial bindings, all six private-prompt bypass spellings, and the
networkless generator visibility boundary with exact public-prompt bytes.
Use `AGENT_EE_REQUIRE_TOOLCHAIN=1` to require those native tests without skips.

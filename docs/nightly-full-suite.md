# Local nightly suite and GitHub completion

## In brief

The full test suite runs once a night on the owner's host, inside the
container image, on the exact commit of `origin/main`. It takes about two
hours. It is the one place every test runs; an ordinary change runs only the
tests for what it touched ([AGENTS.md](../AGENTS.md)).

- A run is **complete** when its log holds one full strict verdict that agrees
  with the exit status. A complete run with problems is a failure, not a pass.
- The result is compared with the previous complete run. A new problem, or an
  old one of a changed kind, is a regression.
- Reporting is optional and separate from the tests. By default a regression
  files one Plane issue per commit and the commit gets a GitHub status.
  `--skip-plane` runs the suite without filing in Plane, and
  `--skip-gh-status` without the GitHub status. Neither changes what the suite
  runs or how its verdict is read.
- Hosted GitHub CI runs no tests. It only checks that a complete local verdict
  exists for the commit.
- Run one full native suite on a host at a time. Do not start a second one
  beside the nightly.

The sections below are the reference for the controller's existing behaviour:
what it records, when it refuses, and how an incomplete attempt is handled.

AGENTEE-619 keeps actual tests on the admitted local runner. GitHub runs
`scripts/check_full_suite_recency.py`. The latest `local-full-suite` status must
carry `strict-complete <exact SHA> <original finish> problems=N`. Completed
unchanged commits do not expire. Pending/error, unmarked, malformed or newer
incomplete records cannot borrow an older completion. Success requires zero
problems; failure requires at least one. Conflicting records at the latest
identical timestamp refuse in both API orderings; genuine duplicate
state/description records pass. Host finish/publication/current time permit at
most five seconds of clock skew. Timestamps are never rewritten.

Completion does not assert quality or acceptance. A completed suite with problems
keeps its GitHub failure status, including an unchanged baseline. Public status
descriptions contain counts and timestamps without private paths or logs.

## Nightly execution

`scripts/nightly_full_suite.py run` fetches `origin/main` and selects its exact
commit/tree. Fetch failure cannot fall back to a local HEAD. Foreign or dirty
worktrees, including ignored files, refuse without deletion. The prepared
service uses `--fresh-worktree`: each attempt keeps its own worktree, so prior
build outputs cannot affect the next candidate.

The controller delegates to the reviewed `ae-ci-exec`, with two workers and
weight 2 by default. That runtime owns native admission, input selection, image
selection and mounts. The controller constructs no Docker command. The installed
runner offers an ordinary contract: it prints its image identity and kept-scratch
path to the log and returns the command's exit status; there is no companion
program and no private outcome journal. The controller records the source, exact
command, unique attempt identity, runner hash, controller hashes, image
identities, kept scratch, exit, start/finish, log hash and a `completion` of
`complete` or `incomplete` with its reason (AGENTEE-619).

State defaults to `~/.local/share/agent-ee-full-suite`. Each attempt has a
`runs/<timestamp>-<commit>/` directory containing execution/attempt receipts and
`suite.log`. A completed comparison adds `compare.json`, `compare.txt` and an
`output-manifest.json` inventory with file hashes and link metadata. A regression
or unknown prior problem also produces integrator failure records. These local
artifacts are not files to commit or upload.

The suite has a 10,800-second aggregate deadline, including admission waiting;
the per-module deadline stays 3,600 seconds. The installed service has a
210-minute outer limit for stock preparation, checkout, reporting and cleanup.
A measured two-worker run exhausted the former 7,200-second limit after
393 of 451 modules, without a per-module timeout. The increased aggregate
budget preserves two workers, weight 2 and strict verdict requirements. That
incomplete attempt remains retained; it cannot establish a passing baseline.

On timeout or Ctrl-C, the controller sends SIGTERM to its process group, waits
60 seconds, then uses SIGKILL if needed, and records the attempt as incomplete
with `termination` set. Killing the controller itself can leave a child running
and an unresolved marker. The prepared systemd service terminates its control
group. Never replay an unresolved marker without proving cleanup and
reconciling its evidence.

## Verdicts, reporting and incomplete attempts

The parser accepts one complete full strict-suite verdict and exactly its N
preceding problem lines. Raw unittest duplicates and trailing annotations are
ignored. Full IDs preserve subtest parameters, including bracketed/quoted colons.
Identical repeated IDs receive deterministic `[occurrence N]` suffixes. Missing,
ambiguous, partial and render-omitting verdicts cannot establish completion.
Exit must agree: 0 for no problems, 1 for a completed suite with problems.

Completed tests and external reporting are separate state. Unchanged completed
main skips native work. Failed Plane/GitHub reporting returns nonzero and resumes
from retained comparison before any later native attempt. Plane uses installed
`--name`, escaped `--description-html` and a stable commit external identity.
Creation must return a successful JSON envelope with the issue ID and matching
external source/ID. A 409 must name the existing issue ID; lookup by the external
identity must confirm that same ID. New IDs and changed problem kinds are regressions.

`run --plane-assignee USER_UUID` explicitly selects a Plane accountability user.
For the authorized campaign, halbritt is `a3f369ea-d8dd-42ed-bea3-db653abd632b`.
The option also works before `run`; omission leaves assignment unspecified.
Invalid or nil UUIDs refuse before state creation or execution. The option does
not apply to `qualify` or `import-baseline`. Both creation and conflict lookup
must confirm the requested assignee in the returned issue; an ignored flag or
unassigned existing issue keeps reporting incomplete. This path does not change
an existing issue's assignment. Reporting retries preserve the commit external
identity and original suite finish time without rerunning the suite. Real filing
and deduplication evidence is recorded separately from the controller fixtures.

When module setup prevents execution, its previous problem IDs/kinds remain
`unexecuted_problems` with status unknown. Summary, Plane and integrator records
cannot count them as fixed. The setup diagnostic remains a separate observed
problem. Unknown test counts are never invented; a later executed pass may close
those old problems.

Completion is established only by the retained log's single complete strict
verdict agreeing with the observed exit status. A missing, truncated, duplicate
or disagreeing verdict, a timeout or an interruption leaves the attempt
`incomplete`: state does not advance, nothing publishes as complete, and GitHub
receives `error`. When the controller observed a normal runner exit,
the attempt is retained as incomplete, `reconciliation.json` records
`exit_observed_incomplete`, and `active-run.json` is cleared so a later call may
try that commit again. A missing exit or a runner killed by signal leaves the
marker in place and blocks even `--force`: native cleanup is uncertain. A
timeout permits retry only if the runner's TERM cleanup returned normally.
Inspect an unresolved attempt before retrying. A generic exit code never
establishes completion.

## Explicit baseline initialization and one full gate

Missing state refuses normal execution. The operator may select
`run --initialize-baseline` for a reviewed candidate-bound native run. Existing
or corrupt state cannot be overwritten. Only a complete verdict records the
baseline, preserving inherited problems without filing them as new regressions.
Comparison counts are null because there was no prior run. A failing baseline
still publishes failure. Unversioned legacy state requires reconciliation.

To preserve the single composed gate rule before landing, the operator may instead
select `qualify` on an exact clean candidate. It uses the normal runtime entrypoint,
records the complete source/runtime/input evidence and problem inventory, and
publishes the actual completion once. It does not initialize nightly state.

```sh
python3 -B scripts/nightly_full_suite.py qualify \
  --root /absolute/clean/candidate --commit EXACT_COMMIT --tree EXACT_TREE \
  --runner /absolute/reviewed/ae-ci-exec --evidence-dir /absolute/new/gate \
  --weight 2 --jobs 2
```

A full native run still has to be admitted on the host like any other. The controller chooses a
fresh evidence directory; existing evidence directories refuse. `-B` avoids
controller bytecode in the candidate before its clean-tree check.

After review and landing of that exact commit/tree, `import-baseline` consumes
an explicit `agent-ee.baseline-import/1` JSON selection with `gate_directory`,
`commit`, `tree`, `output_manifest_sha256`, `input_manifest_sha256` and
`review_reference`. The output manifest binds every retained file, including the
execution receipt and complete log. Controller bytes must match the selected Git
commit; the receipt must be `complete`, full-suite, and its exit must agree with
the log's verdict. The installed runner writes no native input receipt, so
`input_manifest_sha256` is the reviewer's recorded identity, not a cross-check.

```sh
python3 -B scripts/nightly_full_suite.py import-baseline --root . \
  --state-dir "$HOME/.local/share/agent-ee-full-suite" \
  --selection /absolute/lead-selected/baseline-import.json
```

Import requires matching intended `origin/main`, uninitialized state, no active
attempt and consistent complete verdict/count/exit. It records original start/
finish lineage, invokes no native command and publishes no status or synthetic
freshness. The original gate owns completion publication. Missing, changed,
incomplete or mismatched evidence refuses. Preparing the operation selects no
baseline and executes no gate.

## Selected inputs, the runner's receipt and fresh stock (U4 B/C)

The controller passes one selected `agent-ee.native-inputs/1` manifest
(`--input-manifest`) to the installed runner as `AE_CI_INPUT_MANIFEST`, with a
unique external `AE_CI_EVIDENCE_DIR` under the attempt's `runs/<id>/native/`. The
runner's own helper prepares read-only snapshots, binds the resolved image into
one receipt and announces `ae-ci-exec: native input receipt /absolute/path` in
the log. After completion validation the controller binds that single receipt to
the attempt: status `prepared`, the manifest digest selected before launch,
unchanged current manifest bytes, source commit/tree, runner hash
and announced image must all match, every input must be read-only with a file or
Git identity, and the receipt must lie inside this attempt's evidence directory.
Any mismatch leaves the attempt `incomplete` ("native input receipt not bound").
An ambient `AE_CI_INPUT_MANIFEST` never leaks into an attempt that did not select
one. `qualify` requires `--input-manifest`; `import-baseline` requires the gate's
bound receipt and cross-checks the selection's `input_manifest_sha256` against
it, replacing reviewer attestation. Mount policy and confinement belong to the
runner-invoked helper and to `strict_inputs`; the controller validates neither.

`--stock-config` (schema `agent-ee.nightly-stock-config/1`: `context_path`,
`source_root`, `per_request_timeout_seconds`, `invocation_timeout_seconds`,
`min_interval_seconds`, `max_bytes`, optional `endpoint`) refreshes the
acquisition stock context before an actually selected native attempt, after the
lock, unchanged-main, reporting, unresolved-attempt and source checks. The config
is exact and finite: non-finite numbers, non-integer `max_bytes`, an invocation
timeout above 420 s, a per-request timeout not below the invocation timeout, or
values outside the producer's own limits refuse; values are preserved, never
coerced. Preparation runs in `scripts/nightly_stock_prepare.py`, a fresh
interpreter whose only `agent_ee` is the selected checkout's `src` (clean import
custody); the controller never imports the producer. Requested
IDs come from the current context's two retained parent catalogs; the existing
`agent_ee.jlc_stock_capture` producer runs once per parent with bounded time, no
retry and no backdating. The configured request interval also applies between
the two parent populations. Part identities must match the previous catalog; the
existing `alternative_context` union/admission algebra builds and checks the new
closure; raw bytes and provenance are retained under `runs/<id>/stock/`. The
per-attempt manifest `runs/<id>/input-manifest.json` replaces exactly the stock
source root and context entries. Unchanged-main skips and reporting retries never
capture. Any failed preparation is retained with its reason and starts no native
work and advances no baseline; there is no cached stale fallback. The 24 h
acceptance rule is evaluated by the tests at real run time and is unchanged.
Preparation attempts are indexed before capture, so a failed preparation remains
counted and a later explicit retry can proceed. The active-native marker is
created only after preparation succeeds. The existing preparation child owns
one process group containing its producer and fetch workers; bounded termination
and reaping on return, failure, timeout or interruption prevent a fetch from
outliving a failed preparation. Partial logs and records remain retained.

## Module input setup and preserved coverage

`strict_suite.py` validates the entire inventory with runtime `strict_inputs.py`
before precompilation or any child, including cheap-check mode. Each child applies
only its own bindings and rechecks its paths. Global schema, allowlist and
confinement faults stop the suite. Module-local unavailability, including an
explicit producer declaration, refuses that module while independent modules
execute. Verdicts state that its unexecuted test count is unknown. The helper
and adjacent `native-input-policy.json` remain byte-bound to the runtime pair.

Real Python-child fixtures retain their sources, manifests, logs and summaries
under `AGENT_EE_EVIDENCE_DIR/ci-consumer-fixtures`. Nightly Git/process fixtures
are retained under `nightly-fixtures`. These pure checks do not prove native
mounts. Executable FOME/J1 acquisition and read-only mount custody checks live in
the infra runtime suite.

All eight `test_strict_suite.WorkflowInputTests` identities remain, with their
former GitHub-native assumptions reconciled to completion-only GitHub and local
full-suite delegation. Runtime-consumed FOME/J1 values remain unchanged.
`test_baseline2_and_20261104_exact_counts` uses committed portable inventories
with exact problem identities/kinds and source-log hashes, preserving its
144/182/4/42/140 comparison. Private captures and raw historical logs stay out of
Git. Inventories do not claim native execution.

`--max-hours` remains accepted for compatibility without expiring a completed
exact commit. The historical `test_stale_status_fails` name remains with the
operator-approved changed expectation. Legacy unmarked statuses cannot establish
completion because the old runner could publish them without a complete verdict.

## Retention and operational gates

`--max-retained-runs` and `--max-retained-bytes` are optional; when selected they
refuse further native work at that retained-evidence backlog. No threshold is
applied by default (the owner required no hard halt), but history accounting
still runs and is recorded in `retention-check.json`. Completed unchanged-main
skips and reporting retries still work. The byte check includes run outputs and
indexed kept scratch. One admitted run can cross a threshold; the next is held.
Nothing is automatically deleted. Worktrees, inputs, outputs and scratch remain selected until the operator
reconciles, exports and explicitly releases them. Missing selected scratch blocks
new work; there is no age-based purge.

The infra service must explicitly select the runtime input manifest and fresh
stock configuration, retain native scratch and use a fresh worktree per attempt.
Its installation record binds the selected configuration and installed file
hashes. The campaign selects no retained-run or byte threshold. Installation,
activation, moved-main execution and unchanged-main skip evidence are separate
from source-level verification of this controller.

History accounting validates `retention-index.json` against all retained attempts,
including failed/incomplete states. Multiple lineage entries for the
same attempt UUID count once. Byte totals count regular-file sizes once per
device/inode across run outputs, retained worktrees and indexed scratch, without
following links or charging other agents' histories. Index/path/receipt failures
hold admission with actionable `retention-check.json`; unknown accounting is
reported as unknown, never zero. No new last-tested SHA or completion status is
fabricated. Existing unindexed history requires reconciliation by the operator.

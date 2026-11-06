# Local nightly suite and GitHub recency

AGENTEE-619 keeps actual tests on the admitted local runner. GitHub's scheduled
and manual workflow executes only `scripts/check_full_suite_recency.py` for its
commit. The latest `local-full-suite` status must carry a `strict-complete` marker
binding the exact commit, actual finish time and problem count. Completed
unchanged commits do not expire. Pending/error, unmarked, malformed or
future-dated evidence cannot borrow an older completion. Completion does not
assert that every test passed; a completed failed suite keeps its failure status. The status description contains counts
and a timestamp, without local paths or logs.

`scripts/nightly_full_suite.py run` fetches `origin/main` and checks out its exact
commit in the configured isolated worktree. Fetch failure cannot fall back to a
local HEAD. Existing foreign or dirty worktrees are refused and preserved.
The runner delegates the full suite to `ae-ci-exec`, with two workers and
admission weight 2 by default. `ae-ci-exec` owns native admission, runtime/input
selection and mounts; this script does not construct a competing Docker command.
Native scratch is retained with `AE_CI_KEEP_TMP=1`. Its child has a two-hour bound.

State defaults to `~/.local/share/agent-ee-full-suite`. Each attempt gets a
`runs/<timestamp>-<commit>/` directory with an execution receipt and `suite.log`.
The receipt records source commit/tree, command, runner digest, exit status when
available, log digest and image identities reported by `ae-ci-exec`. Successful
parsing adds `compare.json` and `compare.txt`. A new regression also gets
`failures.json` and `latest-failures.json`. These are local artifacts, not files
to commit or upload.

The receipt also hashes the nightly Python controller, which can differ from
the tested worktree. On timeout the controller sends SIGTERM to its runner
process group and waits up to 60 seconds for cleanup before using SIGKILL.

The parser accepts one full strict-suite verdict and the exact number of
problem lines immediately before it. It ignores duplicate raw unittest output
and trailing input annotations. Full test IDs retain subtest parameters. A
repeated identical ID gets a deterministic `[occurrence N]` suffix so counts
still match the verdict. Missing, incomplete, ambiguous and render-omitting
reports cannot establish a completed full suite. Colons inside bracketed or
quoted subtest parameters remain part of the identity. The process exit must agree
with the verdict: 0 for a pass, 1 for a completed suite with problems.

Completed tests and external reporting are separate state. An unchanged
completed main skips native execution. Failed Plane or GitHub reporting returns
nonzero and resumes from the retained comparison on the next call, before a
later native run can start. Plane uses `--description-html`, escaped text and
the `agent-ee-nightly-suite`/commit external identity; a conflict is accepted
only after looking up that exact identity. GitHub marks every completed suite with remaining problems as failure;
regression filing separately covers additions and changed problem kinds.

Missing state refuses normal execution. The lead explicitly selects
`run --initialize-baseline` for the first reviewed candidate-bound native run;
existing or corrupted state cannot be overwritten by this flag. Only its
complete verdict records the baseline, without filing inherited problems as
new regressions. Its comparison is labelled `baseline`; comparison
counts in state are null because there is no previous run. A failing baseline
still publishes a GitHub failure status. Subsequent changes of problem kind,
including missing-input/skip to error/failure, are reported as regressions even
when the full test ID remains present. Diagnostic-only changes within a kind
remain kept problems.

## Per-module input setup

`strict_suite.py` imports the runtime-owned `strict_inputs.py` helper. It calls
`validate(os.environ, modules())` once before precompilation and child dispatch,
using the entire module inventory. Each child calls `for_module` with that
selection. Global schema, confinement and protected-variable violations stop
the suite. A `ModuleInputUnavailable` produces a retained module refusal with
its variable; independent modules still execute. The verdict and summary
distinguish these refused setups from known test-level missing inputs and state
that the blocked module's unexecuted test count is unknown.

The pure consumer fixtures exercise real Python children and preserve their
manifests, probe sources, logs and summaries under
`AGENT_EE_EVIDENCE_DIR/ci-consumer-fixtures` when that evidence directory is set.
They use the helper's public test policy roots and keep its source bytes
unchanged. These checks do not demonstrate the native producer or container
mounts; those remain part of the runtime owner's composed review.

An unresolved native attempt leaves `active-run.json` and blocks another
attempt, including `--force`, until its receipts/reservation/evidence are
reconciled. `--force` only bypasses the unchanged-completed-commit check.
Unversioned state is preserved and refused because its old parser identities
cannot safely be used as a baseline. Do not remove a marker or state file merely
to obtain a green schedule.

The host-specific service, timer, install mapping and activation record belong
to infra at `hosts/proximal/config/agent-ee-full-suite/`. The remediation lead
installs and activates them after review and landing, then proves one moved-main
run and an unchanged-main skip. Preparing unit files is not that proof.

## Coverage preserved by AGENTEE-619

The eight `test_strict_suite.WorkflowInputTests` identities remain. Their old
GitHub native-job assumptions are reconciled with recency-only CI: command
extraction still rejects missing, ambiguous and unsupported executable blocks;
the actual extracted shell command is checked with fresh/stale/missing status
responses; the local lock and delegation/full-suite arguments are exercised.
The runtime-consumed FOME commit and J1 decision hash values remain unchanged.
The J1 documented HTTPS/user-agent/size/time/hash guards remain checked. Runtime
review must additionally close anonymous FOME acquisition and read-only input
mount custody in the shared `ae-ci-exec` owner lane.

`test_baseline2_and_20261104_exact_counts` executes on committed problem
inventories in `tests/fixtures/full-suite-problem-inventories.json`, without a
former agent's local files. They preserve the source verdicts, problem kinds and
full test identities, with source-log SHA-256 values. Skip diagnostics are
replaced by a fixed historical-unavailability label; private paths and raw logs
are excluded. The exact 144/182/4/42/140 comparison remains asserted. These
inventories exercise parsing/comparison; they do not claim native execution.

## Monitor compatibility and pending operational gates

`--max-hours` remains accepted for CLI compatibility; it no longer expires a
completed exact commit. The historical `test_stale_status_fails` identity is
retained, with its former age-expiry expectation explicitly replaced by the
lead's 2026-11-06 exact-commit completion decision. Old unmarked statuses cannot
establish completion because the former runner could publish them without a
complete verdict. A new main SHA needs its own marked completion.

Native attempts publish pending before execution and error on incomplete
execution, without a completion marker. Local `latest-attempt-failure.json`
and the run's `attempt-failure.json` retain the failure. Distinguishing a
retryable definite no-start from an uncertain started attempt still requires
the runtime owner's reviewed protocol; unresolved attempts remain held.
Executable acquisition/mount custody coverage, stable owned input/evidence
configuration, and retention/ignored-input handling remain composed runtime
review gates. Preparing this consumer does not close those gates or activate
the timer.

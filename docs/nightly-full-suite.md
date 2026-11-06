# Local nightly suite and GitHub recency

AGENTEE-619 keeps actual tests on the admitted local runner. GitHub's scheduled
and manual workflow executes only `scripts/check_full_suite_recency.py` for its
commit. A recent `local-full-suite` status in `success` or `failure` establishes
a completed run; `pending`, `error`, absent or stale statuses do not. Recency
does not assert that every test passed. The status description contains counts
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

The parser accepts one full strict-suite verdict and the exact number of
problem lines immediately before it. It ignores duplicate raw unittest output
and trailing input annotations. Full test IDs retain subtest parameters. A
repeated identical ID gets a deterministic `[occurrence N]` suffix so counts
still match the verdict. Missing, incomplete, ambiguous and render-omitting
reports cannot establish a completed full suite. The process exit must agree
with the verdict: 0 for a pass, 1 for a completed suite with problems.

Completed tests and external reporting are separate state. An unchanged
completed main skips native execution. Failed Plane or GitHub reporting returns
nonzero and resumes from the retained comparison on the next call, before a
later native run can start. Plane uses `--description-html`, escaped text and
the `agent-ee-nightly-suite`/commit external identity; a conflict is accepted
only after looking up that exact identity. GitHub marks added problems as
failure and an unchanged or reduced problem inventory as success.

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

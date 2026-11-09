# Local nightly suite and GitHub completion

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

## Nightly execution and custody

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
and deduplication proof still require a separate operational authorization.

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
The lead inspects unresolved attempts before retrying. A generic exit code never
establishes completion.

## Explicit baseline initialization and one full gate

Missing state refuses normal execution. The lead may select
`run --initialize-baseline` for a reviewed candidate-bound native run. Existing
or corrupt state cannot be overwritten. Only a complete verdict records the
baseline, preserving inherited problems without filing them as new regressions.
Comparison counts are null because there was no prior run. A failing baseline
still publishes failure. Unversioned legacy state requires reconciliation.

To preserve the single composed gate rule before landing, the lead may instead
select `qualify` on an exact clean candidate. It uses the normal runtime entrypoint,
records the complete source/runtime/input evidence and problem inventory, and
publishes the actual completion once. It does not initialize nightly state.

```sh
python3 -B scripts/nightly_full_suite.py qualify \
  --root /absolute/clean/candidate --commit EXACT_COMMIT --tree EXACT_TREE \
  --runner /absolute/reviewed/ae-ci-exec --evidence-dir /absolute/new/gate \
  --weight 2 --jobs 2
```

Native capacity still needs a separate lead grant. The controller chooses a
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
lead-approved changed expectation. Legacy unmarked statuses cannot establish
completion because the old runner could publish them without a complete verdict.

## Retention and operational gates

`--max-retained-runs` and `--max-retained-bytes` are optional; when selected they
refuse further native work at that retained-evidence backlog. No threshold is
applied by default (the owner required no hard halt), but history accounting
still runs and is recorded in `retention-check.json`. Completed unchanged-main
skips and reporting retries still work. The byte check includes run outputs and
indexed kept scratch. One admitted run can cross a threshold; the next is held.
Nothing is automatically deleted. Worktrees, inputs, outputs and scratch remain selected until the lead
reconciles, exports and explicitly releases them. Missing selected scratch blocks
new work; there is no age-based purge.

The prepared infra unit explicitly selects the owned runtime manifest and a
fresh per-run evidence policy. The separate scoped nightly manifest remains runtime-owned and pending; its
exact reviewed hash must be recorded before activation. Lead retention thresholds
are selected, with implementation/review still pending.
The lead owns installation, activation, moved-main execution and unchanged-main
skip proof after review/landing. No native grant, baseline import or timer
activation follows merely from preparing this code.

History accounting validates `retention-index.json` against all retained attempts,
including failed/incomplete states. Multiple lineage entries for the
same attempt UUID count once. Byte totals count regular-file sizes once per
device/inode across run outputs, retained worktrees and indexed scratch, without
following links or charging other agents' histories. Index/path/receipt failures
hold admission with actionable `retention-check.json`; unknown accounting is
reported as unknown, never zero. No new last-tested SHA or completion status is
fabricated. Existing unindexed history requires lead reconciliation.

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
selection and mounts. The controller constructs no Docker command. It records
the source, exact command, unique attempt identity, runner and companion-file
hashes, controller hashes, image identities, exit, start/finish and log hash.
A private host `outcome.json` sits beside each attempt's `native/` directory,
outside the writable checkout and native output mount. Input snapshots, sealed
receipts and writable outputs are retained under `native/`.

State defaults to `~/.local/share/agent-ee-full-suite`. Each attempt has a
`runs/<timestamp>-<commit>/` directory containing execution/attempt receipts and
`suite.log`. A completed comparison adds `compare.json`, `compare.txt` and an
`output-manifest.json` inventory with file hashes and link metadata. A regression
or unknown prior problem also produces integrator failure records. These local
artifacts are not files to commit or upload.

On timeout or Ctrl-C, the controller sends SIGTERM to its process group, waits
60 seconds, then uses SIGKILL if needed. Manual SIGTERM can leave a child running
and an unresolved marker. The prepared systemd service terminates its control
group. Never replay an unresolved manual interruption without proving cleanup
and reconciling its evidence.

## Verdicts, reporting and no-start recovery

The parser accepts one complete full strict-suite verdict and exactly its N
preceding problem lines. Raw unittest duplicates and trailing annotations are
ignored. Full IDs preserve subtest parameters, including bracketed/quoted colons.
Identical repeated IDs receive deterministic `[occurrence N]` suffixes. Missing,
ambiguous, partial and render-omitting verdicts cannot establish completion.
Exit must agree: 0 for no problems, 1 for a completed suite with problems.

Completed tests and external reporting are separate state. Unchanged completed
main skips native work. Failed Plane/GitHub reporting returns nonzero and resumes
from retained comparison before any later native attempt. Plane uses escaped
`--description-html` and a commit external identity; a conflict requires looking
up that exact identity. New IDs and changed problem kinds are regressions.

When module setup prevents execution, its previous problem IDs/kinds remain
`unexecuted_problems` with status unknown. Summary, Plane and integrator records
cannot count them as fixed. The setup diagnostic remains a separate observed
problem. Unknown test counts are never invented; a later executed pass may close
those old problems.

An unresolved attempt leaves `active-run.json`, blocking even `--force`.
Automatic clearance requires a private, complete `not_started` journal bound to
the exact command (including attempt identity), runner/supervisor, exit and
start/finish interval. The journal bytes and supervisor attempt ID are sealed in
the execution receipt before clearance. The failed attempt and reconciliation
remain retained; a later call may retry. Missing, stale, altered, malformed,
interrupted or started/uncertain records never clear the marker. A generic exit
code cannot establish no-start. `--force` bypasses only the unchanged-commit check.

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

Native capacity still needs a separate lead grant. Select an explicit
`AE_CI_INPUT_MANIFEST`; the controller chooses fresh evidence and journal paths.
Existing evidence directories refuse. `-B` avoids controller bytecode in the
candidate before its clean-tree check.

After review and landing of that exact commit/tree, `import-baseline` consumes
an explicit `agent-ee.baseline-import/1` JSON selection with `gate_directory`,
`commit`, `tree`, `output_manifest_sha256`, `input_manifest_sha256` and
`review_reference`. The output manifest binds every retained file, including the
execution receipt, complete log, supervisor journal and native input receipts.
Controller bytes must match the selected Git commit. Input receipts must match
source, runner, image and manifest identity.

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

`--max-retained-runs` (7) and `--max-retained-bytes` (20 GiB) refuse further native
work at the retained-evidence backlog threshold. Completed unchanged-main skips
and reporting retries still work. The byte check includes run outputs and indexed
kept scratch. One admitted run can cross the threshold; the next is held. The lead selected these
admission thresholds on 2026-11-06. Nothing is automatically
deleted. Worktrees, inputs, outputs and scratch remain selected until the lead
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
including failed/incomplete/no-start states. Multiple lineage entries for the
same attempt UUID count once. Byte totals count regular-file sizes once per
device/inode across run outputs, retained worktrees and indexed scratch, without
following links or charging other agents' histories. Index/path/receipt failures
hold admission with actionable `retention-check.json`; unknown accounting is
reported as unknown, never zero. No new last-tested SHA or completion status is
fabricated. Existing unindexed history requires lead reconciliation.

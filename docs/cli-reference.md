# CLI reference

Invoke `agent-ee --help` or `agent-ee COMMAND --help` for parser help. Commands
below are implemented by [cli.py](../src/agent_ee/cli.py). JSON is written to
stdout for `doctor`, `inspect` and successful correction linkage; diagnostics
from handled invocation/refusal errors go to stderr with an `agent-ee:` prefix.

## Commands

| Command | Arguments | Effect |
| --- | --- | --- |
| `doctor` | None | Probe prerequisite versions/imports; no design or inference run |
| `review` | `SCOPE --out RUN [--profile FILE] [--max-seconds N] [--checks-only]` | Capture inputs and run checks, initial reviews and adjudication |
| `inspect` | `RUN` | Show recorded outcome/state, findings, checks, freshness and correction links |
| `verify` | `RUN [--snapshot-only]` | Check input, policy, runtime and retained evidence consistency |
| `resume` | `RUN --out NEW_RUN [--max-seconds N]` | Read the old manifest's scope path, recapture and rerun all work |
| `adjudicate` | `RUN DECISIONS_JSON` | Validate and append operator-supplied dispositions |
| `close-correction` | `OLD_RUN NEW_RUN --finding ID --checks IDS --reason TEXT` | Append a verified correction link to the old run |

Paths are shell arguments, so quote paths containing spaces. `SCOPE` is a JSON
file. All output directories for `review` and `resume` must be new; parents are
created. There is no overwrite, force, in-place resume, single-role retry,
automatic repair or provider-fallback switch.

## Exit codes

| Command | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `doctor` | Every recorded probe exited 0 | A prerequisite probe failed or timed out | Parser/handled invocation error |
| `review`, `resume` | `criteria_satisfied` | Any other recorded review outcome, including execution/capture failure and interruption | Refusal before the run is handled, such as an existing output directory or invalid budget; parser error |
| `verify` | Integrity/freshness checks succeeded, whatever the design outcome | Not a normal handled result | Stale/inconsistent/unavailable evidence or handled invocation/parser error |
| `inspect` | Inspection printed, even when its `freshness` field reports a refusal | Not a normal handled result | Cannot load the required inspection record or another handled invocation/parser error |
| `adjudicate` | Decisions applied; does not imply the resulting review passes | Not a normal handled result | Decisions refused or evidence unavailable/stale |
| `close-correction` | Correction invariants verified and event appended | Not a normal handled result | Closure refused or evidence unavailable/stale |

Unexpected unhandled Python failures can have interpreter exit behavior outside
this table. Do not infer acceptance from an arbitrary successful shell command.
For automation, require a successful `verify` plus `result.json`'s
`outcome == "criteria_satisfied"`. `inspect` and `verify` success alone are not
design acceptance. The [operator guide](operator-guide.md) includes that gate.

## Work budgets and interruption

`--max-seconds` defaults to 1200 and accepts a finite number from 0 through
86400 inclusive. A zero budget is useful for exercising an incomplete run.
The work deadline is monotonic, begins before capture, and is checked between
steps and during subprocess execution. Capture, fingerprinting, finalization
and file I/O are not all hard-preempted; this is not an exact process lifetime
limit or a CPU/token quota.

Checks run in declared order. A check with less than five seconds remaining
does not begin. Native command timeouts are capped by the remaining deadline.
Every reviewer and the adjudicator has an explicit profile timeout; supplied
small evaluation profiles use 300 seconds and donor profiles use 900 seconds.
Calls run sequentially. No automatic
retry occurs. See the [check reference](check-reference.md) for native ceilings.

SIGINT attempts to preserve validated checks/reviews, unknown placeholders for
unfinished work, and useful findings. Invoked subprocess groups are killed on
timeout or caught interrupt. A shell-level force kill or machine crash can
leave a record with state `running`; inspection can project its durable records,
but it does not prove the process is still alive or finalize acceptance.
An interrupt caught inside the subprocess executor records the interrupted
invocation and propagates to the outer boundary, which finalizes an interrupted
run. Check process status and saved records after a force kill.

## `doctor`

The JSON object has `prerequisites` and `note`. Each normal probe records `path`,
`exit_code`, `version_output` and `diagnostics`; a launch failure or 15-second
probe timeout records `error` instead. The probes are kicad-cli, ngspice, bwrap,
pcbnew through system Python, and Claude. No design check or inference occurs.
Authentication is not probed, so doctor can succeed before a review discovers
missing subscription access.

## `review` and `resume`

`--checks-only` runs capture and checks without agent reviews. It always leaves
the adversarial review incomplete and exits 1, including when every check
passes. Its useful native evidence remains available. `resume` has no
`--checks-only` flag and starts a full fresh review.

`resume` uses the original scope file path stored in `candidate.json`, not a
scope reconstructed from old captured bytes. If that path has moved, create
and inspect a new scope and use `review`. It does not import old acceptance,
copy dispositions onto a changed design, or close prior findings automatically.
The previous run is linked for inspection; closure is a separate operation.

## `inspect` and `verify`

Inspection prints `outcome`, `state`, `candidate_id`, `freshness`, `checks`,
`findings` and `corrections`. Linked corrections are checked against their saved
target result digest and the target's current freshness. A moved or changed
target is reported; the original event is retained.

Normal verification checks the original scope file and currently declared
source bytes as well as the copied snapshot. `--snapshot-only` skips the live
scope/source comparison. It still checks the captured candidate, Python policy,
runtime binaries and retained artifacts. It does not mean "ignore all stale
evidence" and does not bypass changed tool versions. Historical restoration is
covered in [evidence reproduction](reproducing-evidence.md).

## `adjudicate`

The decisions document must carry the exact candidate/run identities and valid
evidence citations. Unmentioned findings remain unchanged. A manual event is
appended and the result/report projection is regenerated. Complete applicable
execution and at least two initial reviews remain necessary. A disposition
cannot turn an unknown or failing declared check into a passing review.

See the [result reference](result-reference.md) for decision fields and
dispositions. The automated adjudicator must decide every finding; manual
updates may target a subset. Manual repair closure through ordinary same-candidate
adjudication is refused; use the dedicated correction command.

## `close-correction`

`--checks` is a comma-separated list of check IDs without spaces, for example
`erc,drc,rc-step`. Every named check in the new run must have executed and
passed; informational checks cannot be substituted for affected passing checks.
The reason should state the saved change and why the fresh evidence closes
that particular mechanism. The CLI cannot establish that semantic relationship
from a generic string alone.

The old candidate is verified as a historical snapshot; its live source may
have changed. The new candidate must verify against its current source and
satisfy the entire review. Criteria and declared requirement bytes must match
the old review. The command records changed input hashes, candidate/run IDs,
affected checks and the new result digest. It does not edit the design or mark
the old candidate as passing. See the [operator guide](operator-guide.md) for
the bounded repair workflow.

`--profile FILE` captures an explicit reviewer/adjudicator configuration. An
embedded scope `review_profile` is also accepted. Without either, native checks
are preserved but full review is incomplete. See [profiles](reviewer-profiles.md).

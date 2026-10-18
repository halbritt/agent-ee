# CLI reference

For version-2 requirements, measurement/coupling interfaces, campaigns and bench
feedback, see the [closed-loop contract and command reference](closed-loop.md).

Invoke `agent-ee --help` or `agent-ee COMMAND --help` for parser help. Commands
below are implemented by [cli.py](../src/agent_ee/cli.py). JSON is written to
stdout for `doctor`, `inspect` and successful correction linkage; diagnostics
from handled invocation/refusal errors go to stderr with an `agent-ee:` prefix.

## Commands

| Command | Arguments | Effect |
| --- | --- | --- |
| `capture-catalog` | `MANIFEST --source-root LOCAL --out NEW` | Prepare a pinned local catalog and print its manifest hash; no source authentication |
| `evidence-request` | `RUN --finding ID --spec FILE [--script FILE] --out NEW` | Export a standalone nonauthoritative concern request |
| `evidence-replay` | `REQUEST --out NEW` | Replay the supported bounded networkless Python contract; no acceptance authority |
| `evidence-graduation` | `REQUEST --proposal FILE [--replay REPLAY] --out NEW` | Export an inert proposed check bundle |
| `verify-evidence-operation` | `OPERATION` | Verify standalone operation file identities; no acceptance or source freshness claim |
| `campaign` | `CONFIG --out NEW` | Review and repair disposable candidates with one writer and bounded convergence |
| `verify-campaign` | `CAMPAIGN` | Recompute historical convergence and verify result/calibration links |
| `bench-review` | `RUN REPORT --out NEW [--max-seconds N]` | Capture physical samples and run calibrated checks plus fresh review |
| `campaign-feedback` | `CAMPAIGN REPORT --out NEW [--max-seconds N]` | Start a calibrated epoch and continue within the remaining repair budget |
| `doctor` | `[--checks-only]` | Probe prerequisite versions/imports and hash the probed binaries; no design or inference run |
| `review` | `SCOPE --out RUN [--profile FILE] [--max-seconds N] [--checks-only]` | Capture inputs and run checks, initial reviews and adjudication |
| `view3d` | `SOURCE --out NEW [--port N] [--no-browser] [--export-only]` | Export a saved board or captured candidate into a local 3D browser viewer |
| `render3d` | `SOURCE --out NEW [--models DIR] [--fetch-models] [--width N --height N] [--quality basic\|high] [--jobs N] [--timeout SECONDS]` | Ray trace top, bottom and isometric PNGs with native KiCad |
| `diagnose` | `RUN_OR_CAMPAIGN [--json] [--snapshot-only]` | Report retained facts, unavailable evidence and ambiguous transitions without writes |
| `inspect` | `RUN` | Show recorded outcome/state, findings, checks, freshness and correction links |
| `verify` | `RUN [--snapshot-only]` | Check input, policy, runtime and retained evidence consistency |
| `resume` | `RUN --out NEW_RUN [--max-seconds N]` | Read the old manifest's scope path, recapture and rerun all work |
| `adjudicate` | `RUN DECISIONS_JSON` | Validate and append operator-supplied dispositions |
| `close-correction` | `OLD_RUN NEW_RUN --finding ID --checks IDS --reason TEXT` | Append a verified correction link to the old run |

Paths are shell arguments, so quote paths containing spaces. `SCOPE` is a JSON
file. All output directories for `review` and `resume` must be new; parents are
created. There is no overwrite, force, in-place resume, single-role retry,
or provider-fallback switch. Automatic repair is a separate explicit `campaign`
operation, with configuration and limits described in the closed-loop reference.

## Exit codes

| Command | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `doctor` | Every recorded probe exited 0 and its binary was hashed | A prerequisite probe failed or timed out, or a probed binary could not be hashed | Parser/handled invocation error |
| `review`, `resume` | `criteria_satisfied` | Any other recorded review outcome, including execution/capture failure and interruption | Refusal before the run is handled, such as an existing output directory or invalid budget; parser error |
| `view3d` | Export completed; normal viewer shutdown | Not a normal handled result | Invocation/native-export error |
| `render3d` | Three validated PNGs and a completed manifest | Not a normal handled result | Invocation/native-render error; interruption returns 130 |
| `campaign`, `campaign-feedback`, `bench-review` | Criteria satisfied | Retained nonpassing/incomplete outcome | Invocation refusal |
| `verify-campaign` | Integrity checks succeeded; read printed campaign state | Not a normal handled result | Inconsistent/unavailable evidence |
| `verify` | Integrity/freshness checks succeeded, whatever the design outcome | Not a normal handled result | Stale/inconsistent/unavailable evidence or handled invocation/parser error |
| `diagnose` | Advisory report printed, including failed/unavailable verification | Not a normal handled result | Invocation/parser error |
| `inspect` | Inspection printed, even when its `freshness` field reports a refusal | Not a normal handled result | Cannot load the required inspection record or another handled invocation/parser error |
| `adjudicate` | Decisions applied; does not imply the resulting review passes | Not a normal handled result | Decisions refused or evidence unavailable/stale |
| `close-correction` | Correction invariants verified and event appended | Not a normal handled result | Closure refused or evidence unavailable/stale |

Unexpected unhandled Python failures can have interpreter exit behavior outside
this table. Do not infer acceptance from an arbitrary successful shell command.
For automation, require a successful `verify` plus `result.json`'s
`outcome == "criteria_satisfied"`. `inspect` and `verify` success alone are not
design acceptance. The [operator guide](operator-guide.md) includes that gate.

## Work budgets and interruption

For `review` and `resume`, `--max-seconds` defaults to 1200 and accepts a
finite number from 0 through 86400 inclusive. A zero budget is useful for exercising an incomplete run.
The review work deadline is monotonic, begins before capture, and is checked between
steps and during subprocess execution. Capture, fingerprinting, finalization
and file I/O are not all hard-preempted; this is not an exact process lifetime
limit or a CPU/token quota.

Checks run in declared order. A check with less than five seconds remaining
does not begin. Native command timeouts are capped by the remaining deadline.
Every reviewer and the adjudicator has an explicit profile timeout; supplied
small evaluation profiles use 300 seconds and donor profiles use 900 seconds.
Initial review calls run concurrently, at most two at once; adjudication waits
for all initial roles to settle. Each call's timeout is capped by the remaining
shared deadline at admission. No automatic retry occurs. See the
[check reference](check-reference.md) for native ceilings.

SIGINT attempts to preserve validated checks/reviews, unknown placeholders for
unfinished work, and useful findings. Invoked subprocess groups are killed on
timeout or caught interrupt. A shell-level force kill or machine crash can
leave a record with state `running`; inspection can project its durable records,
but it does not prove the process is still alive or finalize acceptance.
An interrupt caught inside the subprocess executor records the interrupted
invocation and propagates to the outer boundary, which finalizes an interrupted
run. Check process status and saved records after a force kill.

## `view3d`

`SOURCE` is a saved `.kicad_pcb` file or a retained review directory. `--out`
must be new. A retained review uses the board in its verified captured inputs;
viewing does not verify the review result or establish design acceptance.

The command exports native GLB geometry, serves the output on `127.0.0.1` and
opens the default browser. `--port` accepts 0 through 65535; its default 0 chooses
an available port. `--no-browser` keeps the server and prints its URL without
opening a browser. `--export-only` builds the bundle and exits without starting
a server. Ctrl+C stops an active server. Native export has a 300-second timeout.
See [3D PCB viewer](pcb-viewer.md) for controls, model-library limits and remote
use.

## `render3d`

`SOURCE` is a saved `.kicad_pcb` file or retained review directory. `--out` must
be new and outside a retained review. Defaults request 4096 × 3072 pixels,
`--quality high`, `--jobs 3` and a 1200-second timeout per view. `--jobs` accepts
1, 2 or 3. Actual output dimensions are recorded from the PNGs.

`--models DIR` supplies a stock-model cache. `--fetch-models` requires that
directory and explicitly enables downloads of referenced stock models from
pinned official sources. Missing models remain visible in coverage metadata.
See [native 3D renders](pcb-renders.md) for output records, examples and failures.

## `doctor`

The JSON object has `prerequisites` and `note`. Each normal probe records `path`,
`exit_code`, `version_output`, `diagnostics`, `binary` and `sha256`; a launch
failure or 15-second probe timeout records `error` instead. The probes are
kicad-cli, ngspice, bwrap, system Python (`/usr/bin/python3 --version`), pcbnew
through system Python, and Claude. No design check or inference occurs.

`binary` is the file whose SHA-256 is reported. For executables it is the
`path` found on `PATH`. For pcbnew, `path` remains `/usr/bin/python3` and `binary` is
KiCad's `_pcbnew` native extension. These are the same files and digests that
`runtime_files` records in every result. `binary` and `sha256` are `null` when
the extension cannot be located; a probed file that cannot be read records
`hash_error`. Either makes doctor exit 1. A hash identifies that one file, not
every shared library it loads; stock libraries are identified per run by the
scope's captured input hashes.

`--checks-only` omits the reviewer harness probe (Claude) and names it under
`skipped`. Use it where only native checks run, such as the
[container image](container.md). It does not make a full review possible without
the harness. Codex and Agy are not version-probed by doctor even when a profile
selects them. Authentication is not probed, so doctor can succeed before a
review discovers missing subscription access.

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

## `campaign` and `verify-campaign`

`campaign CONFIG --out NEW` requires an `agent-ee.campaign/1` JSON object.
There are no `--profile`, `--checks-only`, `--max-iterations` or `--max-seconds`
flags on this command: review allocation, implementation definition and budgets
are configuration fields. `scope` resolves relative to CONFIG and must use
scope/2. See the [complete field reference](campaign-guide.md#configure-and-start).

The campaign runs synchronously and retains `state.json`, ordinary runs, original
result snapshots and implementation workspaces. Individual review completions
print their report/outcome lines; the campaign command's exit code and final
state determine the campaign result. Do not parse the last review's printed line
as the whole campaign outcome.

`verify-campaign CAMPAIGN` takes no snapshot-only flag. It checks historical run
snapshots without requiring their original live sources, but still requires the
recorded Python/runtime identities and retained evidence. It prints the verified
campaign state and exits 0 on integrity success, including for honestly incomplete
or escalated campaigns. For a satisfied generation campaign it recomputes the final
run's sealed fab artifact hashes, refuses a state whose `fab_artifacts` differ, and
prints them after the state ([done gate](fab-package.md#campaign-state)). It uses the root writer lease and can refuse while a
campaign is active. See [history verification](campaign-guide.md#retained-files-and-verification).

## `bench-review` and `campaign-feedback`

Both require an existing target, an `agent-ee.bench/1` report, a fresh `--out`
directory, and optional `--max-seconds` (default 1200). Neither accepts a new
review-profile flag. Campaign continuation passes its captured campaign profile.
Standalone `bench-review` preserves the captured scope `review_profile`, including
a profile originally supplied through `--profile FILE`. Capture embeds the parsed
external profile in that scope; bench re-entry does not reload its original path.
Physical data must identify that candidate and match executed metric contracts.

`bench-review RUN REPORT --out NEW` captures the bundle, registers feedback on
RUN and starts a fresh review at NEW/review. Its later review validates a finite
0–86400-second budget. A bad budget can therefore be refused after feedback
registration; the old acceptance is not rolled back.

`campaign-feedback CAMPAIGN REPORT --out NEW` validates its finite 1–86400-second
budget before ingestion and requires an accepted or empirically superseded last
candidate. The fresh review goes into the next campaign run directory. Calibrated
repair continues only within the remaining original iteration allowance.

These commands can mutate the source run's empirical event/result even if the
subsequent review fails. They do not edit its native design inputs. Consult
[bench admission and recovery](bench-feedback.md) before retrying a failed attempt.

## Read-only failure diagnosis

`agent-ee diagnose PATH` prints an evidence timeline. `--json` emits
`agent-ee.diagnosis/1`, including identities, recorded state, verification,
verified facts, failed invariants, unavailable facts, ambiguous transitions and
operator actions with preconditions. Campaign reports also contain per-run reports.
Timeline entries describe observed records in lifecycle order; they do not prove
write chronology or completed transitions. Record fields remain untrusted until
ordinary integrity verification succeeds under coherent reads.

Diagnosis opens existing adjacent writer lock files read-only and attempts shared
nonblocking `flock` leases, including marked campaign ancestors. It never creates
a missing lock. `coherence.status` is `coherent`, `busy` or `unverified`.
Verification uses the same decisions as `verify` and `verify-campaign`, retaining
exception types and messages; `verification.coherent` says whether those decisions
were observed under leases. A passing check on an unverified snapshot is advisory.
Missing or malformed records produce partial reports. A `.tmp` file may belong to
an active writer; it establishes neither the interrupted phase nor a safe retry.

Diagnosis does not repair records, recover work, invoke models or rerun checks.
It may probe installed runtime identity through the ordinary verifier. Default
run verification checks live sources; `--snapshot-only` selects historical inputs.
Campaign history always uses historical inputs. Exit zero means the report was
printed, even for busy, unknown or failed evidence; it does not establish design
acceptance or recovery safety. Byte hashes and directory inventory stay unchanged;
filesystem access times may change from reads. Leases coordinate supported writers,
not arbitrary host processes.

See [evidence requests](evidence-requests.md) for schemas, resource limits and replay exit codes.

## Bench transaction recovery

`bench-review --operation-id ID` and `campaign-feedback --operation-id ID` opt
new operations into the [recoverable feedback contract](bench-feedback.md#recoverable-feedback-transactions).
The ID accepts 1–80 ASCII letters, digits, underscores or hyphens.

| Command | Effect |
| --- | --- |
| `bench-status PATH` | Read-only structured phase, identity and ambiguity diagnosis under existing leases. |
| `bench-recover PATH` | Reconcile exact deterministic publication and adopt verified terminal review evidence; never call a provider. |
| `bench-launch PATH` | Explicitly launch the first review only if no start intent exists. |
| `bench-new-attempt PATH --attempt-id ID --out DIR` | Explicitly launch a fresh review after an uncertain attempt, retaining all old evidence. |

Commands print JSON. Status/recovery/launch return 0 for completed or ready for
explicit review, 1 for unresolved execution, and 2 on refusal. The initial
transaction returns 0 only when the reviewed outcome satisfies criteria.
A completed transaction with an incomplete review remains an incomplete design
outcome. No command acquires measurements or authorizes a new design writer.

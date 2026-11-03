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
| `build run` | `PLAN --out RUN [--resume] [--through STAGE]` | Execute 7-stage generative build pipeline (`requirements` through `package`) |
| `build review` | `RUN --out NEW [--max-seconds N] [--profile FILE]` | Run fresh native checks and optional isolated reviewers on last completed candidate |
| `place run` | `CONFIG --out NEW [--resume]` | Run placement search and routing optimization using the Rust placement engine |
| `place metrics` | `BOARD [--groups FILE] [--intent FILE]` | Extract read-only placement quality metrics, wirelengths, and locality |
| `parts compile` | `--bundle DIR --manifest FILE --circuit FILE --layout FILE --out DIR [--deviations FILE] [--ratings FILE] [--adopt-as PATH]` | Bind selected parts and land patterns into circuit intent |
| `design capture` | `DESIGN --out DIR [--kicad-root DIR]` | Capture CAD symbols, footprints, 3D models, datasheets, and stock quotes |
| `design compile` | `DESIGN --captures DIR --out DIR` | Deterministically compile design statement and captured assets into relocatable build bundle |
| `design generate` | `REQUIREMENTS --out DIR [--author SPEC] [--max-attempts N] [--max-seconds N] ...` | Bounded LLM synthesis loop from requirements text to validated design statement |
| `design benchmark` | `REQUIREMENTS --out DIR [...]` | Benchmark requirements-to-board generation loop across design examples |
| `scaffold` | `ENVELOPE PARTS (--scope FILE \| --plan FILE) [--intent FILE] [--layout FILE] --out DIR` | Create initial empty native project, outline, stackup, rules, and fixed parts |
| `capture-catalog` | `MANIFEST --source-root LOCAL --out NEW` | Prepare a pinned local catalog and print its manifest hash; no source authentication |
| `evidence-request` | `RUN --finding ID --spec FILE [--script FILE] --out NEW` | Export a standalone nonauthoritative concern request |
| `evidence-replay` | `REQUEST --out NEW` | Replay the supported bounded networkless Python contract; no acceptance authority |
| `evidence-graduation` | `REQUEST --proposal FILE [--replay REPLAY] --out NEW` | Export an inert proposed check bundle |
| `verify-evidence-operation` | `OPERATION` | Verify standalone operation file identities; no acceptance or source freshness claim |
| `campaign` | `CONFIG --out NEW` | Review and repair disposable candidates with one writer and bounded convergence |
| `verify-campaign` | `CAMPAIGN` | Recompute historical convergence and verify result/calibration links |
| `bench-review` | `RUN REPORT --out NEW [--max-seconds N] [--operation-id ID]` | Capture physical samples and run calibrated checks plus fresh review |
| `campaign-feedback` | `CAMPAIGN REPORT --out NEW [--max-seconds N] [--operation-id ID]` | Start a calibrated epoch and continue within the remaining repair budget |
| `bench-status` | `PATH` | Read-only structured phase, identity, and ambiguity diagnosis under existing leases |
| `bench-recover` | `PATH` | Reconcile deterministic publication and adopt verified terminal review evidence |
| `bench-launch` | `PATH` | Explicitly launch the first review only if no start intent exists |
| `bench-new-attempt` | `PATH --attempt-id ID --out DIR` | Explicitly launch a fresh review after an uncertain attempt, retaining old evidence |
| `doctor` | `[--checks-only] [--router-checkout DIR]` | Probe prerequisite versions/imports and hash the probed binaries; no design or inference run |
| `review` | `SCOPE --out RUN [--profile FILE] [--max-seconds N] [--checks-only] [--roles R,...] [--routing-receipt RECEIPT]` | Capture inputs and run checks, initial reviews and adjudication |
| `view3d` | `SOURCE --out NEW [--port N] [--no-browser] [--export-only]` | Export a saved board or captured candidate into a local 3D browser viewer |
| `render3d` | `SOURCE --out NEW [--models DIR] [--fetch-models] [--width N --height N] [--quality basic\|high] [--jobs N] [--timeout SECONDS]` | Ray trace top, bottom and isometric PNGs with native KiCad |
| `diagnose` | `RUN_OR_CAMPAIGN [--json] [--snapshot-only]` | Report retained facts, unavailable evidence and ambiguous transitions without writes |
| `inspect` | `RUN` | Show recorded outcome/state, findings, checks, freshness and correction links |
| `verify` | `RUN [--snapshot-only] [--current-host]` | Check input, policy, recorded runtime and retained evidence consistency |
| `resume` | `RUN --out NEW_RUN [--max-seconds N]` | Read the old manifest's scope path, recapture and rerun all work |
| `adjudicate` | `RUN DECISIONS_JSON` | Validate and append operator-supplied dispositions |
| `close-correction` | `OLD_RUN NEW_RUN --finding ID --checks IDS --reason TEXT` | Append a verified correction link to the old run |
| `pcbschemagen_gate1`* | `--design FILE [--netlist FILE] --task-id ID [--out FILE] ...` | Deterministic Gate 1 topological & ERC verifier against PCBSchemaGen Knowledge Graph |
| `pcbschemagen_benchmark`* | `[--manifest FILE] [--task-id ID] [--candidate FILE] [--candidates-dir DIR] [--out FILE] [--run-build] ...` | PCBSchemaGen Dual-Gate benchmark runner evaluating Gate 1 topology and Gate 2 physical build |

Paths are shell arguments, so quote paths containing spaces. `SCOPE` is a JSON
file. All output directories for `review` and `resume` must be new; parents are
created. There is no overwrite, force, in-place resume, single-role retry,
or provider-fallback switch. Automatic repair is a separate explicit `campaign`
operation, with configuration and limits described in the closed-loop reference.

## Exit codes

| Command | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `place run` | Status `completed` (all placements and routing resolved) | Status `incomplete` or defect outcome | Input, solver, or invocation refusal |
| `place metrics` | Metrics calculated and output printed | Handled measurement failure | Argument, path, or parser error |
| `parts compile` | Part bindings and connectivity intent emitted | Parts compilation refused | Argument, path, or parser error |
| `design compile` | Validated relocatable build bundle emitted | `DesignError` or validation failure | Argument, path, or parser error |
| `design generate`, `design benchmark` | Validated design statement emitted | Max attempts exhausted without valid design | Invocation or parameter refusal |
| `scaffold` | Initial native candidate project created | Scaffold creation refused | Argument, path, or parser error |
| `doctor` | Every recorded probe exited 0 and its binary was hashed | A prerequisite probe failed or timed out, or a probed binary could not be hashed | Parser/handled invocation error |
| `review`, `resume` | `criteria_satisfied` | Any other recorded review outcome, including execution/capture failure and interruption | Refusal before the run is handled, such as an existing output directory or invalid budget; parser error |
| `build run` | Recorded `accepted` or diagnostic `checkpoint`; inspect `accepted` and `qualification.dimensions` | Recorded pending/failed/interrupted build or failed qualification | Input, identity or invocation refusal; inspect a retained run's separate `resume-refusals` receipt |
| `build review` | Full review criteria satisfied, if such evidence exists; the current checks-only path returns 1 | Checks-only diagnostic remains incomplete even if checks pass; `accepted:false` | Source custody or invocation refusal |
| `view3d` | Export completed; normal viewer shutdown | Not a normal handled result | Invocation/native-export error |
| `render3d` | Three validated PNGs and a completed manifest | Not a normal handled result | Invocation/native-render error; interruption returns 130 |
| `campaign`, `campaign-feedback`, `bench-review` | Criteria satisfied | Retained nonpassing/incomplete outcome | Invocation refusal |
| `verify-campaign` | Integrity checks succeeded; read printed campaign state | Not a normal handled result | Inconsistent/unavailable evidence |
| `verify` | Integrity/freshness checks succeeded, whatever the design outcome | Not a normal handled result | Stale/inconsistent/unavailable evidence or handled invocation/parser error |
| `diagnose` | Advisory report printed, including failed/unavailable verification | Not a normal handled result | Invocation/parser error |
| `inspect` | Inspection printed, even when its `freshness` field reports a refusal | Not a normal handled result | Cannot load the required inspection record or another handled invocation/parser error |
| `adjudicate` | Decisions applied; does not imply the resulting review passes | Not a normal handled result | Decisions refused or evidence unavailable/stale |
| `close-correction` | Correction invariants verified and event appended | Not a normal handled result | Closure refused or evidence unavailable/stale |
| `pcbschemagen_gate1` | Gate 1 passed (`passed == True`) | Gate 1 failed | Invocation or runtime error |
| `pcbschemagen_benchmark` | All evaluated tasks passed dual-gate evaluation | Any task failed either gate | Invocation or verifier input error |

Unexpected unhandled Python failures can have interpreter exit behavior outside
this table. Do not infer acceptance from an arbitrary successful shell command.
For automation, require a successful `verify` plus `result.json`'s
`outcome == "criteria_satisfied"`. `inspect` and `verify` success alone are not
design acceptance. The [operator guide](operator-guide.md) includes that gate.
For build automation, gate the file claim on `accepted` and the recorded
`qualification.dimensions.file_release.status`, then inspect the separately
required operational, judgment, supplier and physical statuses. Checkpoint exit
0 alone grants no release claim; legacy mode withholds current method claims.

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

## `build run` and `build review`

`build run PLAN --out OUT [--resume] [--through STAGE]` executes the full 7-stage generative build pipeline:
1. `requirements` -> validates authored spec, requirements record, and derives mechanical obligations.
2. `parts` -> binds authored parts, datasheets, symbols, and footprints to circuit intent.
3. `scaffold` -> creates empty native KiCad project, board outline, stackup, rules, and fixed parts.
4. `schematic` -> emits native `.kicad_sch`, runs ERC, and forward-annotates footprints onto the PCB.
5. `placement-routing` -> executes Rust placement search and invokes `kicad-route` routing engine.
6. `artwork` -> relocates silkscreen reference fields off conflicting pads and edges.
7. `package` -> runs checks-only review and exports Gerber, drill, BOM, and CPL fabrication artifacts.

- `PLAN`: Path to `build-plan.json` (`agent-ee.build-plan/1` or `agent-ee.build-plan/2`).
- `--out OUT`: Run directory. Must be new unless `--resume` is specified.
- `--resume`: Resumes a previously interrupted or checkpointed build in `--out`.
- `--through STAGE`: Stops execution after completing `STAGE` (`requirements`, `parts`, `scaffold`, `schematic`, `placement-routing`, `artwork`, or `package`). Checkpoint runs exit 0 with status `checkpoint` but grant no acceptance claim.

`build review RUN --out OUT [--max-seconds N] [--profile FILE]` executes fresh native checks and optional isolated reviewers on the last completed candidate of an existing build without modifying the candidate:
- `RUN`: Path to an existing build directory.
- `--out OUT`: Output directory for review report and diagnostics.
- `--max-seconds N`: Maximum execution time in seconds (default: 900).
- `--profile FILE`: Explicit `agent-ee.profile/1` JSON profile specifying reviewer harnesses and adjudicator.

```sh
agent-ee build run bundles/i2c/build-plan.json --out runs/i2c-run
agent-ee build run bundles/i2c/build-plan.json --out runs/i2c-run --through schematic
agent-ee build run bundles/i2c/build-plan.json --out runs/i2c-run --resume
agent-ee build review runs/i2c-run --out runs/i2c-review --profile profiles/standard.json
```

## `place run` and `place metrics`

`place run CONFIG --out OUT [--resume]` executes standalone placement optimization and routing for a board using the external Rust placement engine (`agent-ee-placement`) and `kicad-route`:
- `CONFIG`: Path to placement job configuration JSON (`agent-ee.placement-job-config/1`).
- `--out OUT`: Output directory for placed board, manifests, and placement plans.
- `--resume`: Resumes an in-progress placement search.

`place metrics BOARD [--groups GROUPS] [--intent INTENT]` inspects a placed `.kicad_pcb` board file read-only, measuring bounding box extents, connector edge alignments, electrical locality scores, wirelengths, and group positions:
- `BOARD`: Path to a native `.kicad_pcb` board file.
- `--groups GROUPS`: Optional groups JSON file defining functional groups.
- `--intent INTENT`: Optional placement intent JSON file defining component locality rules.

```sh
agent-ee place run configs/placement-pod.json --out runs/placement-out
agent-ee place metrics runs/placement-out/board.kicad_pcb --groups groups.json --intent intent.json
```

## `parts compile`

`parts compile --bundle DIR --manifest FILE --circuit FILE --layout FILE --out DIR [--deviations FILE] [--ratings FILE] [--adopt-as PATH]` compiles selected components into bound circuit intent, validating footprints, land patterns, and passive component ratings:
- `--bundle`: Path to bundle directory containing captured assets.
- `--manifest`: Path to parts manifest JSON.
- `--circuit`: Path to circuit declaration JSON.
- `--layout`: Path to layout declaration JSON.
- `--out`: Target output directory.
- `--deviations`: Optional explicit assembly deviations JSON.
- `--ratings`: Optional passive rating sources JSON for power and voltage validation.
- `--adopt-as`: Receipted output path; refuses if it overwrites an immutable dependency.

```sh
agent-ee parts compile --bundle bundles/i2c/inputs \
  --manifest parts.json --circuit circuit.json --layout layout.json --out runs/parts-out
```

## `design capture`, `design compile`, `design generate`, and `design benchmark`

The `agent-ee design` family manages the transformation from high-level circuit declarations or requirements text into typed build bundles:

`design capture DESIGN --out OUT [--kicad-root DIR]` downloads, extracts, and caches local CAD symbols, footprints, 3D models, component datasheets, and catalog stock quotes into a pinned capture directory:
- `DESIGN`: Path to `agent-ee.design/1` JSON statement.
- `--out OUT`: Output directory for captured assets.
- `--kicad-root DIR`: Path to local KiCad symbol and footprint libraries (default: `/usr/share/kicad`).

`design compile DESIGN --captures DIR --out OUT` deterministically compiles an `agent-ee.design/1` statement and its captured assets into a relocatable build bundle (`inputs/` and `build-plan.json` under `agent-ee.build-plan/2`):
- `DESIGN`: Path to `agent-ee.design/1` JSON statement.
- `--captures DIR`: Path to directory generated by `design capture`.
- `--out OUT`: Target bundle directory (must be new).

`design generate REQUIREMENTS --out OUT [OPTIONS]` runs a bounded multi-attempt synthesis loop using an LLM author (`codex` or `agy`) to translate natural-language requirements into a validated `agent-ee.design/1` statement:
- `REQUIREMENTS`: Path to requirements Markdown file.
- `--out OUT`: Target output directory.
- `--author SPEC`: Author format `codex:MODEL:EFFORT` or `agy:MODEL:EFFORT` (default: `codex:gpt-6.1-sol:high`).
- `--max-attempts N`: Maximum generation attempts (default: 4).
- `--max-seconds N`: Maximum wall-clock budget for all phases and retries (default: 2400).
- `--author-seconds N`: Timeout per author turn (default: 600).
- `--search-retries N`: Extra placement seed attempts per turn (default: 1).
- `--review-profile FILE`: Optional engineering review profile.
- `--context-root DIR`: Path to repository checkout with reference examples.
- `--kicad-root DIR`: Local KiCad library root (default: `/usr/share/kicad`).

`design benchmark REQUIREMENTS --out OUT [OPTIONS]` executes the automated requirements-to-board loop across benchmark cases, taking identical arguments to `design generate`.

```sh
agent-ee design capture fixtures/design-examples/i2c-sensor.json --out captures/i2c
agent-ee design compile fixtures/design-examples/i2c-sensor.json --captures captures/i2c --out bundles/i2c
agent-ee design generate fixtures/design-requirements/led-flasher.md --out runs/led-flasher-gen --max-attempts 2
```

## `scaffold`

`scaffold ENVELOPE PARTS (--scope SCOPE | --plan PLAN) [--intent INTENT] [--layout LAYOUT] --out OUT` creates an initial empty native `.kicad_pcb` board and project from captured planning inputs:
- `ENVELOPE`: Path to board envelope JSON (`agent-ee.envelope/1`).
- `PARTS`: Path to parts JSON.
- `--scope SCOPE`: Path to stage-one scope/2 or scope/3 with captured project rules.
- `--plan PLAN`: Path to `agent-ee.scaffold-plan/1` deriving checks and project rules (mutually exclusive with `--scope`).
- `--intent INTENT`: Optional captured bound connectivity intent.
- `--layout LAYOUT`: Captured emitter layout (required if `--intent` is given).
- `--out OUT`: Target output directory.

```sh
agent-ee scaffold envelope.json parts.json --plan scaffold-plan.json --out candidates/c1
```

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
directory and explicitly enables downloads of referenced stock models from the
official library at the installed KiCad release's tag, with the 6.0.11 tag as a
legacy fallback; the receipt records each file's tag and commit. Missing models remain visible in coverage metadata.
See [native 3D renders](pcb-renders.md) for output records, examples and failures.

## `doctor`

The JSON object has `prerequisites`, `runtime` and `note`. `runtime` names the
actual interpreter, prefixes, launcher, CLI source and whether it is the system
interpreter outside a venv. Each normal probe records `path`,
`exit_code`, `version_output`, `diagnostics`, `binary` and `sha256`; a launch
failure or 15-second probe timeout records `error` instead. The probes are
kicad-cli, ngspice, bwrap, the running Python interpreter, pcbnew through that
interpreter, the Rust planner, and Claude. No design check or inference occurs.
The kicad-cli record adds `erc_json_coordinates`: the ERC JSON coordinate regime
(`scale_x100`, `native` or `withheld` with `reason`) the `erc` check applies to
that version ([quirk guards](kicad-quirks.md)).

`binary` is the file whose SHA-256 is reported. For executables it is the
`path` found on `PATH`. For pcbnew, `path` names the running interpreter and
`binary` is KiCad's `_pcbnew` native extension imported by that interpreter.
The source launcher uses `/usr/bin/python3`, which is also the interpreter
used by the result runtime fingerprint. `binary` and `sha256` are `null` when
the extension cannot be located; a probed file that cannot be read records
`hash_error`. Either makes doctor exit 1. A hash identifies that one file, not
every shared library it loads; stock libraries are identified per run by the
scope's captured input hashes.

`--router-checkout DIR` adds a required `kicad-route` record with the checkout, its
commit, clean-tree state and code digest, backend identity and `problems`. A dirty
tree, an unreadable commit or an installed-backend or source mismatch makes this probe
fail, as does the image's installed checkout holding another commit than its install
record `/opt/kicad-route.install.json`. Without this option doctor does not check the router.

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

`--roles` names profile reviewer roles for a targeted run, for example `--roles rf`
for a fresh [RF review](rf-review.md). Only those roles are dispatched; the
others keep `not_run` records and adjudication is skipped. The result is always
incomplete. Every run records the [RF applicability decision](rf-review.md#applicability-is-always-recorded).

`--routing-receipt` names a routing adapter `receipt.json`. The run anchors its
digest, re-verifies it, and binds it to the RF review when its routed board is
the reviewed board. Without the option, the RF
[routing evidence](rf-review.md#routing-adapter-evidence) is
`missing_evidence`. Campaigns supply the owned routing receipt themselves.

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
scope/source comparison. It still checks the captured candidate, the recorded
policy and runtime, the retained KiCad install record and retained artifacts. It
does not mean "ignore all stale evidence".

A finished run is verified against its own records. It used the tools it
recorded in `result.json` `runtime_files` (KiCad, ngspice, Bubblewrap, Python,
harnesses) and the tools its scope declares (measurement tools, the datasheet
extractor). A different tool on the verifying host, for example KiCad 10.0.8 for
a run made with 10.0.7, does not fail verification: each one is printed as an
`observation: NAME differs on this host` line with the recorded and current
path and SHA-256. A changed harness that the run's profile never invokes is also
an `observation:` line ([used and unused harnesses](result-reference.md#used-and-unused-harnesses)).
So is an agent-ee implementation (Python policy files) other than the one the
run recorded: the observation lists the files that differ. The records must
still agree: `runtime_files` must equal the launch expectation's, which
verification recomputes from the run, and a retained `kicad-install.json` must
have its recorded SHA-256, be a valid install record and describe the run's
recorded installed `kicad-cli` and pcbnew extension. `--current-host` is for
work that continues on the run on this host. It also requires the run's tools,
the tools its launch expectation declared (such as the DC tools and
`pcbnew.py`), its implementation and the host's KiCad install record to be the
ones the run recorded, and refuses a difference with `tool runtime changed;
rerun under the current tools`, `declared tool changed since the run`,
`review implementation changed; rerun under the current rules` or `KiCad
install record on this host differs from the one the run retained`. Without it,
a declared tool that differs is an `observation:` line too. `adjudicate` and `close-correction`
write into runs and always apply that requirement. Historical restoration is covered in
[evidence reproduction](reproducing-evidence.md).

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
recorded Python identity, consistent runtime records and retained evidence. Tool
differences on this host are printed as `observation:` lines, once each. It prints the verified
campaign state and exits 0 on integrity success, including for honestly incomplete
or escalated campaigns. For a satisfied generation campaign it recomputes the final
run's sealed fab artifact hashes, refuses a state whose `fab_artifacts` differ, and
prints them after the state ([done gate](fab-package.md#campaign-state)). With declared stage steps
it also recomputes the [stage-step done binding](stage-receipts.md#done-through-declared-steps) and
refuses a satisfied state whose `stage_done` differs. It uses the root writer lease and can refuse while a
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

```sh
agent-ee bench-status runs/bench-tx-01
agent-ee bench-recover runs/bench-tx-01
agent-ee bench-launch runs/bench-tx-01
agent-ee bench-new-attempt runs/bench-tx-01 --attempt-id attempt-2 --out runs/bench-tx-01-attempt-2
```

## PCBSchemaGen benchmark CLI modules

In addition to `agent-ee` subcommands, the repository provides standalone benchmark execution and verification CLI modules for PCBSchemaGen dual-gate evaluation:

### `python3 -m agent_ee.pcbschemagen_gate1`

Deterministic Gate 1 topological and ERC verifier adapter. It translates candidates from `agent-ee.design/1` or native KiCad XML netlists into PCBSchemaGen topological snapshots, filters testing wrappers, and checks pin roles and net topologies against the Knowledge Graph (`Gate1KGStore`):

- `--design, -d DESIGN`: Path to `agent-ee.design/1` JSON statement.
- `--netlist, -n NETLIST`: Path to native KiCad XML netlist.
- `--task-id, -t TASK_ID`: Task identifier (e.g. `104` or `BUCK_AUX_LOGIC`).
- `--task-name TASK_NAME`: Optional human-readable task name.
- `--pcbschemagen-root DIR`: Clean PCBSchemaGen Git checkout at any commit; the report records the commit as `verifier.commit` (default: `$PCBSCHEMAGEN_ROOT` or `~/git/PCBSchemaGen_v2`). See [benchmark prerequisites](benchmarking-guide.md#executing-pcbschemagen-benchmarks).
- `--captures CAPTURES`: Path to captured KiCad symbols directory.
- `--std-snapshot FILE`: Reference design snapshot JSON.
- `--rules RULES`: Topological rules JSON file.
- `--out, -o OUT`: Output path for Gate 1 report JSON (`agent-ee.pcbschemagen-gate1/1`).
- `--quiet, -q`: Suppress informational output.

Exit codes: 0 if Gate 1 passes (`passed == True`); 1 if Gate 1 fails; 2 on invocation error.

```sh
PYTHONPATH=src python3 -m agent_ee.pcbschemagen_gate1 \
  --design candidate_design.json --task-id 104 --out reports/gate1_report.json
```

### `python3 -m agent_ee.pcbschemagen_benchmark`

Dual-Gate Benchmark Suite Runner for PCBSchemaGen Tier-1 tasks. Evaluates candidates through two non-overlapping validation gates:
- **Gate 1**: Zero-LLM pin/topology correctness against the PCBSchemaGen Knowledge Graph.
- **Gate 2**: Physical board compilation and routing in the `agent-ee` native pipeline (ERC, stackup, connector edge placement, DRC).

Emits a structured score `(Gate 1: PASS/FAIL, Gate 2: PASS/FAIL)` adhering to `agent-ee.dual-gate-benchmark/1`:

- `--manifest, -m MANIFEST`: Path to Tier-1 manifest JSON (`agent-ee.pcbschemagen-tier1-manifest/1`, default: `fixtures/pcbschemagen-tier1/manifest.json`).
- `--task-id, -t TASK_ID`: Single task ID or comma-separated list of IDs (e.g. `104,105,106`).
- `--candidate, -c CANDIDATE`: Path to candidate design JSON or build run directory (requires `--task-id`).
- `--candidates-dir DIR`: Directory of candidates named `<id>.json` or `<id>/design.json`.
- `--pcbschemagen-root DIR`: Clean PCBSchemaGen Git checkout at any commit; each task's `gate1.verifier` records the commit.
- `--captures-dir DIR`: Directory of captured stock/symbol libraries.
- `--out, -o OUT`: Output path for structured benchmark report JSON.
- `--run-build`: Execute physical board compilation and routing in Gate 2.
- `--summary, -s`: Print text summary table.
- `--quiet, -q`: Suppress progress logging.

Exit codes: 0 if all evaluated tasks pass dual-gate evaluation; 1 if any task fails either gate; 2 on invocation error.

```sh
PYTHONPATH=src python3 -m agent_ee.pcbschemagen_benchmark \
  --task-id 104 --candidate evaluation_candidates/104.json --summary
```

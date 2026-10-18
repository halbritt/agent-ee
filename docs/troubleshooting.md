# Troubleshooting and recovery

Start with `agent-ee inspect RUN`, the top-level `error.log` if present, and the
failing check/reviewer execution record. Preserve the run. A later successful
attempt does not retroactively complete an interrupted candidate's evidence.

## Common symptoms

| Symptom / diagnostic | Meaning | Next action |
| --- | --- | --- |
| Installer says `Refusing to replace existing path` | A discovery destination belongs to something else | Inspect the file/link; resolve the conflict intentionally; rerun installer |
| `agent-ee` not found | CLI link missing or `~/.local/bin` absent from PATH | Use `./bin/agent-ee`; inspect installation and shell PATH |
| Doctor's pcbnew probe fails | System Python cannot import installed KiCad bindings | Check `/usr/bin/python3`, not an unrelated virtualenv; repair the environment |
| Bubblewrap fails before a tool runs | Namespace/mount permissions or binary/environment problem | Read `sandbox_argv` and console; repair permissions/setup, then create a fresh run |
| In the container: `No permissions to create new namespace`, `Failed to make / slave` or `Can't mount proc` | Docker's seccomp, AppArmor or `/proc` masking blocks Bubblewrap; container root fails with `Creating new namespace failed` | Run as non-root with the three options in [container privileges](container.md#bubblewrap-inside-the-container); do not substitute `--privileged` |
| Action verdict `result_invalid` or `coverage_incomplete` | The gate found no trustworthy result or an unexecuted check, independent of the review's exit 1 | Read the job summary and the uploaded run directory; fix the cause and rerun into a new directory |
| Output path already exists | Runs cannot overwrite old evidence | Select a new output path or use `resume` with a new path |
| `max-seconds must be finite and between 0 and 86400` | Invalid work budget | Supply a finite value in the supported range |
| `input selects no files` | Missing/overrestrictive source selection | Compare source location, case and fnmatch patterns |
| `declare symlink target as a separate input` | An undeclared live link was selected | Explicitly map its real source at the effective captured destination |
| `overlapping input destinations` | Two selections populate the same target | Narrow includes/excludes or choose nonoverlapping destinations |
| `hierarchy references missing sheet files` | Native sheet coverage is incomplete | Capture the missing original child or correct an authorized disposable candidate; rerun |
| `sheet hierarchy cycle` / escaped reference | Unusable hierarchy or a reference outside capture | Inspect original child paths; do not ignore missing coverage |
| `schematic parity was not performed` | Native console reports a skipped parity check despite possible exit 0 | Restore matching board/schematic layout and captured dependencies; rerun |
| ERC/DRC `execution=ok`, `outcome=defect` | Tool executed and criterion failed | Inspect the actual native issue; distinguish rule metadata from physical mechanism |
| `unknown check kind` | Check handler absent | Correct the kind or implement/test a new check explicitly |
| Transient says populated component unsupported | Generator lacks an applicable model interpretation | Use a suitable independently validated adapter/model; do not idealize it silently |
| Measurement missing/nonfinite or raw trace invalid | Simulation did not establish the requested result | Inspect deck, control, solver log and raw trace; correct execution/test setup and rerun |
| `packet_artifacts must name evidence produced` | A required generated packet artifact is absent | Fix the failed prerequisite or scope artifact declaration; create a fresh run |
| Text/binary packet omission notice | Reviewer context lacks part/all of that artifact | Narrow the scope or provide appropriate traced extraction and rerun |
| `Claude subscription credential file unavailable` | Fixed credential lookup failed | Authenticate the installed harness through the existing subscription route |
| Auth/quota/provider failure | That invocation cannot complete its review | Stop affected inference; resolve access/quota; no API-key/OpenRouter fallback |
| Reviewer `malformed` | JSON, schema, role, identity, citations or finding validation failed | Inspect raw stdout plus role-level error; rerun a fresh bounded review after addressing the cause |
| `adjudicator did not disposition every finding` | Automated reconciliation incomplete | Inspect all claims; prepare valid evidence-based manual decisions or rerun |
| Criteria pass but overall result incomplete | Some required review/adjudication/execution is absent | Inspect expected roles/checks and failure records; checks-only is always incomplete |
| Result is `defects_or_unresolved` | Complete work found defects or material open questions | Inspect accepted/deferred findings; do not treat inspection exit 0 as acceptance |

## Stale evidence

A normal freshness refusal looks like this actual mutation experiment:

```text
agent-ee: declared source inputs changed; evidence applies only to the captured candidate
```

Verification exits 2. Inspect what changed; review the changed saved candidate
in a new run. Do not update hashes by hand. Exact byte restoration can restore
the original candidate's identity, but never establishes acceptance of the
temporarily changed design.

| Refusal | Cause / response |
| --- | --- |
| `review scope changed; evidence is stale` | Exact scope file bytes changed; inspect the change and recapture |
| `candidate snapshot changed; evidence is stale` | Copied inputs were edited, added, removed or linked; restore only from trusted original evidence or rerun |
| `review implementation changed` | Python policy differs; use matching historical implementation for inspection or rerun current policy |
| `tool runtime changed` | A fingerprinted native tool, or a harness the run's profile invokes, differs; historical binary portability is not promised. An unused harness prints an `observation:` line instead |
| `execution/review evidence changed` | Raw or interpreted retained artifacts differ; preserve and investigate the discrepancy |
| `stored result contradicts its execution and finding records` | Outcome projection does not match its contents; do not accept it |
| `check projection differs` / `review projection differs` | Final result and retained check/review records disagree; investigate rather than patching acceptance |

`--snapshot-only` skips current source availability, not policy/runtime/evidence
consistency. It is appropriate for a preserved historical candidate, not a way
to approve a changed live design. A matching source commit does not reinstall
the historical OS or executable set.

## Interrupted or exhausted runs

With SIGINT or a depleted work budget, completed evidence remains useful and
unfinished work remains unknown. If a force kill left state `running`, inspect
saved per-check/reviewer files and confirm process status separately. Do not
edit state to `complete`. Start again with:

```sh
agent-ee resume build/interrupted-review --out build/recovered-review --max-seconds 1200
```

This requires the old candidate manifest and its original scope path. If capture
never produced a manifest or the scope moved, use `review` with a valid explicit
scope. The new run does not reuse old acceptance or automatically transfer
finding closures. All declared work runs again and may spend subscription quota.

## Correction and archive refusals

Closure requires an old accepted finding and a distinct new candidate whose
entire review satisfies criteria. Changed check definitions or declared
requirements, missing affected checks, informational-only checks and stale
new evidence are refusal conditions. Resolve the actual requirement/repair
relationship; do not weaken criteria to make closure succeed.

Archive restore checks the full archive before creating missing files. A
checksum mismatch, unsafe member path/link, or changed existing evidence is a
hard refusal. Preserve the conflicting copy and use a fresh checkout for
inspection instead of overwriting it. Archive packing also refuses a running
review, an existing archive name, or an implementation ref that does not
reproduce recorded policy hashes. Use a new evidence-group name for a new round.

## Closed-loop refusals

Start with the failing run's execution records and, for a campaign, `state.json`.
Use `verify-campaign` only after the active writer releases its lease. Error text
below is an exact implementation diagnostic or a distinctive substring; other
context may be included in the stored exception.

| Diagnostic or state | Meaning | Supported response |
| --- | --- | --- |
| `KeyError: 'step_v'` after RC simulation | Historical runtime before the AGENTEE-2 default-path fix | Preserve the historical run and capture a new candidate with the current implementation; omitted `step_v` now defaults to 1.0 V through measured-result generation ([reference](check-reference.md#ideal-passive-transient)) |
| Fresh standalone bench review has no reviewer profile | External source-run profiles are not currently forwarded | Prepare the source scope with an embedded profile; preserve already registered feedback and diagnose the incomplete follow-up |
| `scope/2 requires baseline requirements, targets and reference_architectures` | Required baseline shape missing or wrong | Supply the exact baseline fields and capture their source files |
| `every check must trace to a baseline requirement` | Requirement mappings do not cover all checks | Correct the engineering mapping, including informational prerequisites |
| `immutable baseline changed` / `candidate changed the campaign baseline` | Captured normative/model/criteria identity changed | Preserve old evidence; deliberately create a new baseline/campaign instead of weakening history |
| `witness must establish a freshly failed criterion` | Claimed defect cites a passing, unknown or nonexecuted check | Reproduce an applicable criterion violation; retain an unproven claim as a concern |
| `only executable check findings may be accepted` | Scope/2 adjudication attempted to accept reviewer prose | Accept the mapped check finding or retain/duplicate the reviewer analysis appropriately |
| `evidence seal contradicts retained check artifacts` | Saved seal does not reproduce from the retained checks/raw files | Preserve and investigate; do not regenerate a seal to hide changed evidence |
| `declared measurement tool changed since review` | Pinned producer executable no longer matches | Use the matching historical tool or create a newly declared run |
| `measurement parser altered raw tool evidence` / `measurement parser altered coordinator request` | Parser crossed its evidence boundary | Inspect invocation and mount records; reject that attempt |
| `campaign terminal state contradicts convergence history` | Stored state and recomputed history disagree | Preserve records and investigate the state mutation |
| `another writer owns` | Supported writer lease is held | Inspect the actual process; wait for its completion or resolve the running operation; do not unlink the lock |
| `implementation changed the captured file inventory` | Proposal added or deleted a file | Keep the authorized inventory or deliberately start a new scope for a broader change |
| `implementation changed immutable verification input` | Proposal edited outside the allowlist | Inspect the proposed diff and accepted defects; do not broaden state by hand |
| `campaign mutable_inputs differ from captured write authority` | Stored allowlist differs from the first scope | Treat state as inconsistent; preserve and investigate |
| `campaign history result link changed` | Stored history snapshot/hash/path or current result changed incompatibly | Preserve original evidence; later manual adjudication is not a campaign history rewrite mechanism |
| `campaign epoch lacks a bound physical feedback transition` | Epoch change is not backed by the preceding bench event/bundle/review | Inspect event and captured calibration identity; do not reset epoch numbers |
| `escalated` | Convergence rule stopped repair | Read `tradeoff.json`, its reason and margins; see [stop rules](campaign-guide.md#states-and-stop-rules) |
| `empirical_superseded` | Last retained candidate has newer registered bench evidence | Inspect the feedback output/new review; old acceptance is no longer current |
| `measurement check lacks a pinned tool declaration` | Generic check has no usable tool identity | Supply explicit argv/hash/domain/program inputs; rerun under the corrected scope |
| `measurement tool executable differs from pinned baseline` | Installed executable does not match the declared digest | Investigate the installation/change; approve and declare a new tool identity intentionally |
| `parser cited evidence not produced by the declared tool` | Parser used its own replacement or an unrecorded path | Fix parser provenance; cite actual recorded producer outputs |
| `parser domain differs from baseline tool declaration` | Parser and declared producer disagree | Correct the tool/adapter contract; domain labels are not inferred |
| `coupled measurements have different operating conditions` | Joined values are not at the same exact condition | Produce compatible measurements; do not relabel unrelated samples |
| `bench report must identify the measured candidate` | Report does not match the source candidate hash/schema | Select the actual prototype's captured run and correct the report provenance |
| `bench observation differs from captured instrument sample` | Report fields disagree with raw sample | Investigate source data/conversion; never edit raw evidence to fit a conclusion |
| `adapter must declare immutable calibration_inputs` | No model applicability boundary was declared | Create a properly scoped new baseline before claiming reusable calibration |
| `calibration model applicability changed` | Current captured model hashes differ | Obtain a new justified model/bench basis; old residuals are not silently reused |

## Feedback failure stages

| Last durable stage | What remains true | Recovery implication |
| --- | --- | --- |
| Report rejected before bundle capture | No completed new empirical registration is established | Output directories may still exist; inspect them and choose a fresh path for a corrected attempt |
| Bundle captured and event registered | Old run's current outcome is incomplete | Keep the bundle and source event even if later review fails |
| New review directory exists | Useful new checks/review records may be partial | There is no general in-place campaign resume; a second feedback call can collide with the reserved next run path |
| New epoch retained | Original historical result snapshot remains unchanged | Verify the campaign and diagnose the new epoch's actual state |

Do not assume a failed command had no side effects. The [bench guide](bench-feedback.md)
defines registration order, budget validation timing and the absence of a
multi-file rollback. An invalid adjudicator citation must be corrected through
valid evidence in a fresh supported workflow, not by silently rewriting its log
or manufacturing the referenced file after the call.

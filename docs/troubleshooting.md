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
| `tool runtime changed` | A fingerprinted executable/path/extension differs; historical binary portability is not promised |
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

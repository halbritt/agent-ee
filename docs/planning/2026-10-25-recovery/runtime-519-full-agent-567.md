# AGENTEE-519 full runtime baseline — agent-567

One authorized full attempt ran at fixed source
`61c6cd49052487fa18fda7a55cec07618d10b3e6`, before AGENTEE-520. The managed
worktree was clean at launch. No runtime code, captured input, budget or
constraint changed, and neither workflow was retried. Chief owns integration
of this evidence note and subsequent repair decisions.

The public harness used `scripts/recovery-container.sh --full`, the original
local retained-input root, and image
`sha256:6533216670ed0bbfabf5dc9d535ce25d59a35bb6c352dda2f99937484b958e7e`.
`native-admit run` provided host admission; an outer `timeout` capped the run at
2,700 seconds. The captured acquisition placement budget stayed 900 seconds and
esp_prog stayed 1,200 seconds. The attempt ran from 2026-10-25 12:55:41 to
13:01:38 PDT and exited 1, within the first 15-minute checkpoint.

All raw evidence remains local at
`/home/halbritt/.cache/agent-evidence/agent-ee-519-full-agent-567/`.
`selected-result.json` indexes the native outputs and hash-binds the reports;
`attempt-1/image-inspect.json` and source identity files bind the runtime.
`originals-integrity.json` records all 204 original files matching the frozen
manifest both before and after the run. No source files or evidence originals
were overwritten.

| Public path | Recorded result |
| --- | --- |
| Doctor and planner receipt | Both exited 0; current Rust source/receipt and router pin passed. |
| Deliberately cancelled esp_prog preflight | Exit 1/status `cancelled` after native capture/runtime identity, 21 native objects, no seeds or evaluation counters. This did not exercise the Rust input parser. |
| Acquisition `build run --through package` | Exit 0 in 352.133 seconds, all seven stages complete. Legacy build report says `accepted: true`. Placement job completed in 280.971 seconds; its six seeds performed 600,000 evaluations. |
| Acquisition native routing | `process: PASS`, `routing: COMPLETE`, `validation: PASS`; zero total opens and zero new DRC violations. The router recorded 18 pre-existing violations; the later finalized package DRC recorded zero violations, zero opens and zero unwaived issues under its captured configuration. |
| Acquisition package checks | 37 `pass`, one `info`; 17 components, 12 nets, 0 DNP. Eight sealed artifact files and all three delivered native file hashes matched the accepted-package record. |
| Full imported esp_prog `place run` | Exit 1 in 1.416 seconds; job report `refused`, reason `placement engine failed (exit 1): duplicate physical reference Ref*`. No seed completed and no routed output exists. |

## Native outputs and esp_prog failure boundary

Acquisition's delivered native files are under the `candidate/project/`
directory named by `attempt-1/run/acquisition/build-report.json`. Their hashes:

- `acquisition.kicad_pcb`: `7faecc6d9edfe9f5fa0532741a2665bd161449d6e2a8ad2cdeca4ddbc3e60c5e`
- `acquisition.kicad_pro`: `b1eec1b496b4dba780aeee2aee56d38908a877abb46d7e3726cee161156c07b0`
- `acquisition.kicad_sch`: `fc552de4568240bb7e4093817c060c11bc00d832459e5c5ea004e5575f5cf294`

`attempt-1/run/esp-prog/capture.json` contains two `fixed_copper_mark` objects
with native reference `Ref*` and distinct UUIDs ending `5d8c55cd` and `5d8c55f6`.
The captured fixed-pose declarations distinguish them as `Ref*` and `Ref*~2`.
At this exact source, `crates/placement-engine/src/job.rs:589` requires unique
native physical references for every role except `SilkOnly`, and the real Rust
process rejects the second mark before returning a planning result. The native
capture/preflight therefore passes while the full public path fails at the Rust
input boundary. Input board hash remains
`b4aa540d031e18283216d806aa6288cf51260e7328f9aaeb13b55d4eac93eef7`.
This observation does not authorize deleting or renaming the marks in the
original board, stripping intent, changing identity rules or retrying.

## Qualification limits and next step

The harness's overall result is `failed`, `acceptance: false`. Acquisition's
legacy `accepted` state is reproduced, while its checks-only review outcome is
`incomplete`. Independent agent reviews/adjudication, supplier preview and
physical validation were not performed. CPL rotation is `not_declared`; exact
sealed export geometry/assembly-profile qualification remains later work.
The final DRC retains the captured ignored-check categories and sourced-rule
policy; zero reported issues is not a universal manufacturing claim.
The router's measurement flag is false because its state manifest lacks a
passing determinism record; this attempt supplies no comparative performance
or reproducibility claim.

This records the requested current full-run result; chief owns issue closure.
AGENTEE-515 and M1 acceptance remain open. No new tests or runtime fixes were added. Chief
should retain the esp_prog duplicate-reference failure as a separate, bounded
input/identity regression when selecting its repair. AGENTEE-520 must still
truthfully refuse unsupported rich declarations in the original inputs; this
older baseline is not permission to bypass that boundary.

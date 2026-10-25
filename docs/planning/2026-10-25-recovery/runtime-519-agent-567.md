# AGENTEE-519 runtime delivery — agent-567

Implementation: `27e9d01b53611a839b31935d3b1dda715023f89e`, branch
`wt/runtime-519-agent-567`, based on dispatched `dab756e5`. Delivery is pending
chief integration and independent verification. Agent-564 owns acceptance,
main integration and worktree cleanup.

The implementation adds actual-interpreter doctor probes, optional required
router verification, explicit backup-preserving uv launcher migration, a
204-file retained-input inventory and a pinned-container runtime harness.
The source launcher now propagates its source directory to native Python
subprocesses. A first isolated container run reproduced `ModuleNotFoundError:
No module named 'agent_ee'` in fresh native capture before that launcher fix.
No build workflow, placement controller, router pin, original intent or
acceptance semantics changed.

## Verification

The final run started from clean implementation commit `27e9d01b`, using image
`sha256:6533216670ed0bbfabf5dc9d535ce25d59a35bb6c352dda2f99937484b958e7e`.
The existing cached image's planner receipt matched current Rust sources;
router preflight matched `c4af095ed07593b63d7886c139fcb7e5c2e774f4`.

Retained evidence is local, outside this disposable worktree:
`/home/halbritt/.cache/agent-evidence/agent-ee-519-agent-567/`.

| Evidence | Observed result |
| --- | --- |
| `qualification/run/runtime-report.json` | Harness `execution_completed`; `acceptance: false`. Public acquisition `build run --through schematic` completed requirements, parts, scaffold and schematic, status `checkpoint`, `accepted: false`, in 10.179 seconds. |
| `qualification/run/esp-preflight/job-report.json` | Exact original esp_prog inputs, with only copied config path relocation. Existing public `place run` with pre-created `CANCEL` returned exit 1/status `cancelled`, after runtime/native import identity. No Rust result, seeds or evaluation counters. 21 captured native footprint objects. This is readiness evidence, not a placed board. |
| `qualification/image-inspect.json`, `source-revision.txt`, `source-status.txt` | Exact local image inspection, implementation revision and empty source dirty status at launch. |
| `controls/native-generated.json`, `acquisition-native.net` | Emitted native acquisition PCB reparsed with 17 footprints; emitted schematic exported a KiCad XML netlist with exit 0. PCB/schematic/project bytes unchanged by inspection. Schematic SHA-256 `fc552de4568240bb7e4093817c060c11bc00d832459e5c5ea004e5575f5cf294`. |
| `controls/missing-planner.json` | Explicit unavailable Rust override caused doctor exit 1 with the missing executable; no fallback. |
| `controls/isolated-python.json` | Real isolated venv caused doctor exit 1 on its `pcbnew` import, despite working system bindings; actual venv interpreter and `uses_system_python: false` retained. |

`AE_CI_MAX_WAIT=60 ae-ci-exec . -- python3 -m unittest tests.test_doctor tests.test_install tests.test_recovery_runtime -q`
passed 20 tests with no skips. Installer cases ran against isolated homes and
preserved unrelated executables and existing backups. Input tests rejected
missing, changed and symlinked captures. Harness grading rejected refusals,
timeouts and jobs with search evidence. Shell syntax, diff whitespace and
`scripts/check_docs.py` passed (248 documents, no link/example errors before
this evidence note). These are focused checks; the strict suite was not run.

## Remaining boundaries

The image build procedure is documented; this slice reused a cached pinned
image rather than performing another complete native toolchain build. Captured
inputs still require local evidence access; no publication or redistribution
clearance is inferred from this inventory. Acquisition has 17 declared populated
parts; imported esp_prog has no population manifest and its native object count
is not used as a population count.

Full mode is implemented as an explicit harness option but was not exercised.
Current full placement/routing, package semantics and M1 release acceptance
remain later work. In particular, AGENTEE-520 may refuse rich declarations that
this base currently ignores. Such a refusal must remain visible against the
original inputs; removing their intent is not a runtime fix. The harness keeps
acquisition's result independent of esp_prog refusal and blocks a full esp_prog
search without demonstrated preflight readiness.

Proposed next bounded step: chief verifies/lands 519, then qualifies 520 against
these same retained inputs and records its expected refusals before selecting
any explicit field mapping under the grouping/representative recovery work.

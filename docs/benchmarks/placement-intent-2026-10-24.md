# Connector intent and honest final status — 2026-10-24

This iteration repairs reusable placement and acceptance behavior. It does not
qualify the full system, establish KRT parity or accept esp_prog for manufacture.
The [system acceptance map](../system-acceptance.md) remains unfinished.

## Observed defect and correction

At baseline `6122503daf82180ceae07b598593830a96614587`, a connector edge without a
numeric maximum setback affected a soft objective but did not require the
connector to remain nearest that edge. The retained predecessor esp_prog board
had CON1/CON2 declared east and observed nearest south despite zero native DRC
errors/opens. A new original native fixture reproduced the same defect before
the correction. The retained red log is `edge-red-native.log`.

The Rust engine now enforces edge identity for movable and fixed connectors.
Capture distinguishes drawn connector body/seat geometry from the courtyard or
derived packing reservation. Candidate promotion and final acceptance independently
reload the saved native board and its settings in a bounded fresh process, check
every declared edge, and report UUID, geometry basis, measured distances and
violations. No reference names, esp_prog coordinates or numeric setback defaults
appear in this correction. Missing edge-check results cannot pass promotion.

The first fresh esp_prog run also exposed a controller defect: it returned
`completed` when final native checks reported opens. The controller now requires
preserved integrity, passing edge checks, final eligibility and a feasible
incumbent. Router success or an exploratory route cannot override those gates.
The original failed report remains unchanged as evidence.

## Native execution

The ordinary entrypoint is `agent-ee place run` (or
`python3 -m agent_ee.cli place run` from source). Inputs are frozen copies of the
predecessor U2-v2 regression, not another live telemetry-pod attempt:

- Board SHA-256: `b4aa540d031e18283216d806aa6288cf51260e7328f9aaeb13b55d4eac93eef7`.
- Project: `9a9ab694fc8a9a456a6791060d998c01a171e30308a170e1b4f97d864b2b6395`.
- Intent: `320cf961d58dab9aea164f34afc11099ae48f75fc0b100273a155d4d62330d3c`.
- Groups: `d48e2a5d95f733be6d4bdee06736034a5e34acd68e6289edef00fe89b86837e8`.

Native environment: KiCad/pcbnew 10.0.7, Python 3.12.3;
`agent-ee:exec-6c7cb78b8f650c16`; placement executable SHA-256
`5763f66e2cddeb8aba604ab25b8895a473e5a80ff8406aaa9366aa3a7f0b5e51`;
kicad-route `b5774496d8466b67ffa0362b6665fee2108155a3`, owned router source
`owned-9cfc5d5e9369` (0.23.1 with selective fixes). This is not stock KRT 0.23.7.
Both attempts had a 600-second total process budget. Admission queue time is
outside these timings; other host workloads were present, so these are observed
run times, not performance or repeatability claims.

| Attempt | Search | Placement | Final native result | Status |
| --- | --- | --- | --- | --- |
| `board-run` | Seeds 0–11, 10,000 steps each; 194.992 s total | Edge checks pass; diagnostic seed 10 retains one CON1/U1 reservation conflict | Zero DRC violations of error severity, two unconnected items | Legacy `completed` defect, explicitly rejected by final eligibility; not accepted |
| `board-run-deeper` | Seeds 0,3,6,9,10,11; 30,000 steps each; 257.193 s total | Feasible seed 10, no engine violations; all three edges pass | Zero DRC violations of error severity, two unconnected items; 270 warnings | `verification_failed`; not accepted |
| `board-run-rejection` | Same inputs/configuration as `board-run`; 143.549 s total, after controller correction | Diagnostic placement; no feasible incumbent | Preserved integrity, two open connections, final ineligible | `verification_failed`, public CLI exit 1 |

The rejection rerun demonstrates the public failure result with actual native
outputs. The time difference is not a performance claim.

The deeper board is
`6ee94e896973269c23e01b58e2388cf0e0ba980e9644242bbb165a8ba25badf4`.
Its two open nets are `/D_P` and `/D_N`, between USB1 and U1. The differential
router reports a coupled middle with hybrid, uncoupled terminal escapes. The
existing kicad-route pair contract rejects that result, discards those pair
tracks and routes the other nets. Final native connectivity correctly detects
the pair remains open. Relaxing that contract or routing the pair as ordinary
signals is not a demonstrated repair. Next routing work must preserve the
requirements and feed this rejection into permitted placement/routing choices.

The deeper board has 21 vias and 261.2065 mm of measured track length. Five
via centres lie inside pad copper; this geometric observation is not an assembly
disposition. Group distances are reported but full functional-block acceptance
is not established. No manufacturing package is accepted from these runs.

## Regression evidence and commands

The rebuilt image passed all 98 Rust tests. The strict native placement suite
passed 77 tests with no skips, including the original frozen esp_prog capture,
retained 1,118-vertex problem, cancellation/resumption and input-custody checks.
All-four-edge controls exercise 16 cases: four correct-edge positives and twelve
wrong-edge negatives, each with clean native DRC, solver feasibility and final
saved-board eligibility. A distinct-body/courtyard case and a fixed-connector
negative also pass. The native incomplete-route negative proves a reported
router success cannot complete a board whose saved connectivity is incomplete.
CLI and source-origin regressions passed 46 tests.

One initial strict invocation omitted `AGENT_EE_515_E2E_PROBLEM`: 75 tests passed
and that required test failed. It was not counted as a passing suite. The complete
rerun supplied both required frozen external inputs and passed all 77 tests.

After restoring the retained bundle's `workspace/` contents to `build/agent545/`:

```sh
ae-ci-exec . -- python3 -m agent_ee.cli place run build/agent545/board-config-deeper.json --out build/agent545/reproduce-deeper
ae-ci-exec . -- env PYTHONPATH=src:tests AGENT_EE_ESP_PROG_PREPARED=/opt/agent-ee/build/agent545/baseline AGENT_EE_515_E2E_PROBLEM=/opt/agent-ee/build/agent545/retained-problem.json python3 -m unittest -v tests.test_placement_job tests.test_placement_job_guards tests.test_placement_job_capture tests.test_placement_job_reservations tests.test_placement_job_clearance tests.test_placement_job_complexity tests.test_placement_job_refusal
```

Evidence is retained under
`~/.cache/agent-ee-qualification/agent545/placement-intent-20261024/`:
`workspace/` contains exact inputs, configurations, logs, stage/native outputs
and individual reports; source patch and integration records identify the code.
`SHA256SUMS` binds the bundle. External design inputs remain outside the public
repository. Clean-setup reproduction by another operator still requires access
to that retained bundle; this iteration does not close whole-system reproduction.

## Explicit limits

The edge check's coverage is only `edge_identity`. Mating-face inference,
connector rotation search, complete final overhang/setback/access checks,
functional grouping and side/height constraints are not claimed by it. The
placement job still refuses zones, custom rules, non-default netclasses and
nonrectangular outlines; these remain required capability gaps in the system
map. The public placement command is not the complete engineering entrypoint.
Latest stock KRT was source-mapped but has not been run on matched inputs here;
no superiority claim exists. Electrical and manufacturing qualification, fresh
multiblock generation, package validation and physical/supplier obligations
remain open.

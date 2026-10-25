# Placement intent boundary — AGENTEE-520

Status: bounded implementation evidence, pending chief review and integration
after AGENTEE-519. This is not complete M1 or manufacturing qualification.
Base: `dab756e56a6a5358c00983074565af0e8e09862c`. Agent-568 implemented
the [supported intent contract](../placement-job.md#supported-intent-boundary-agentee-520).

## Observed results

The exact retained Astra public probe and its three individual declarations now
exit nonzero with `status: refused`. The individual paths are `intent.keepouts`,
`intent.assembly.sides`, and `intent.blocks[0].side`. No problem file, planner
result or incumbent is produced. The exact original esp_prog input similarly
refuses at `intent.assembly.sides`; its four input hashes remain unchanged.

Independent native subprocess captures of the supported two-terminal control
and the retained acquisition schematic-stage board equal the base revision's
complete problem and capture record. Acquisition retains 17 native footprints,
its edge declarations, proximity preferences and sourced hard locality.
Its problem SHA-256 is
`5e31b5ee419683225f0b4b5967576d18303c45847df53f05a9541064bd3b7166`.
This comparison exercises capture, not planner or router completion.

Thirty-one focused tests passed with `AGENT_EE_REQUIRE_TOOLCHAIN=1`, using
`PYTHONPATH=src:tests /usr/bin/python3 -m unittest -v` and these selections:

- `tests.test_placement_intent`
- `tests.test_placement_job.CaptureTests.test_capture_roles_and_labelled_derived_occupancy`
- `tests.test_placement_job.CaptureTests.test_malformed_courtyard_is_a_hard_refusal`
- `tests.test_placement_job.CaptureTests.test_changed_input_bytes_are_refused_by_hash`
- `tests.test_placement_job_capture.FreshRuleTests`
- `tests.test_placement_job_capture.CaptureBoundaryTests`
- `tests.test_placement_job_guards.ControllerBoundaryTests`
- `tests.test_build_review`

The new regressions cover duplicate JSON keys, nested unknown fields, unsupported
pad-edge proximity, malformed declarations, changed fixed rotation/side, missing
native pad selectors, early build refusal and unchanged evidence after refused
resumption. Existing capture cancellation, rule preservation, controller custody
and build-review checks remain passing. Compilation and `git diff --check` pass.

## Compatibility and remaining qualification

Chief agent-564 explicitly corrected the inconsistent requirement to accept raw
rich esp_prog intent unchanged: preserve it as refusal evidence and defer its
source-bound compatibility mapping to AGENTEE-479. Its unsupported top-level
intent fields are `assembly`, `blocks`, `board`, `context`, `decaps`, `defaults`,
`dispositions`, `envelope`, `keepouts`, `kind`, `legality_budget`, `min_reader`,
`must_lock` and `units`; `intent.schema: 1` is also a different contract.
Nested unsupported declarations include connector sides, extra edge context,
all five groups' `near` lists and all three `proximity[].basis: pad_edge`
requests. Descriptive evidence cannot substitute for their enforcement.

Planner-dependent checks remain pending AGENTEE-519: two protected-pose tests
and one duplicate-UUID engine test encountered the missing installed planner.
The available historical binary also fails the exact-source build-receipt
verification. No alternate binary was adopted, and no supported full public
placement, build replay or strict complete-suite result is claimed.

Private local evidence is retained at
`/home/halbritt/.cache/agent-audits/agent-ee-568-agentee-520-20261025/`:
`final-gate.log`, `validation-tests.log` (including the planner prerequisites),
and `final-replay/summary.json` with baseline/current captures and public reports.
Input bytes remain outside this repository. The next step is chief verification,
then integration after 519 and the dependent public-runtime controls.

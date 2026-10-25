# AGENTEE-529 mask capture repair after AGENTEE-522 — 2026-10-25

Agent-569 implemented the scoped repair from
`82133d6d2032791cc85240257dc0573bdf49302e`, following the
[accepted diagnosis](AGENTEE-522-mask-expansion-investigation.md).
Chief independent verification is recorded below; the scoped repair is accepted.

## Change and native API boundary

`placement_job._mask_graphic_bbox` includes a `PCB_SHAPE` graphic's resolved
positive `GetSolderMaskExpansion()` in its mask bounds before local transformation.
Zero or negative values retain the original conservative bounds. No planner,
intent, grouping, rule, schema, native output or DRC semantics are changed.

The helper checks the actual native item type. Native mask DRC adds this margin
for `PCB_SHAPE_T`; `PCB_TEXT` has no such API, and `PCB_TEXTBOX` has a distinct
type despite inheriting shape methods. Non-shape graphics retain their complete
native bounding boxes. Nothing is discarded or defaulted after an API failure.
Native probes establish that the tested shape's box already includes stroke but
does not include its mask margin; the repair adds that margin once. Text and
textbox boxes, including native knockout/border effects, are kept unchanged.
The native API/source captures and unfiltered diagnostics are retained privately.

## Verification

* **Red:** a public cold capture of an owned rectangle inherited 100,000 nm
  expansion, but returned its unexpanded box. The exact assertion failed before
  the repair and passed afterwards.
* **Native strict run:** `test_placement_job_mask` (3 tests) and the full existing
  `test_placement_job_capture` (14 tests) pass: **17 tests, no skips**.
  Command: `python3 scripts/strict_suite.py --jobs 1 --module-timeout 600
  test_placement_job_mask test_placement_job_capture` under `ae-ci-exec`, actual
  admission weight 1, outer bound 900 seconds. This is not the full repository suite.
* The new tests cover 24 cold-file variants: inherited positive/zero/negative
  margins on authored front/back footprints at all four quarter turns. Six live
  native variants cover positive local override/zero/negative on both sides;
  four more retain mask text/textbox bounds. All use owned geometry and real
  capture/classification, without mocks or a copy of the margin helper as oracle.
* A fresh-instance native API probe covers 16 shape/textbox combinations of
  inherited/local margins and mask flags. It confirms the raw box is not already
  enlarged by the resolved margin.

**Native persistence limit:** KiCad 10.0.7 keeps a shape's local override in memory
but omits it on saving an explicit mask-layer graphic. The initial 24 failing
override-file variants and the before/after-save probe are retained. Local
override coverage therefore uses live native objects and the real classifier;
it is not claimed as cold file-roundtrip support. Cold public capture independently
tests inherited signed margins. The repair does not change native serialization.
An initial direct unittest invocation also failed because an existing imported
fixture expects discovery's test path; the standard strict discovery runner was
used thereafter. Both setup failures remain in the evidence.

## Exact retained cases from repaired capture

Only the approved original inputs, failing seed6 and legal seed11 were copied.
No new control layout, placement search or router search was constructed.
The repaired classifier now captures U2's actual graphic rectangle as
`[-1050800, -2550800, 1050800, 2450800]` nm; its other two apertures and C1's
captured masks are unchanged. This value comes from repaired capture, not a
manually enlarged replay model.

The explicit C1/U2 diagnostic projection preserves both complete footprint rows,
outline/rules and the two exact pose vectors, with other population/group/proximity
context removed only for localization. It is not substituted for whole-job acceptance.

| Fixed case | Old captured projection | Repaired-capture projection | Fresh native DRC |
| --- | --- | --- | --- |
| seed6 | feasible, zero hard conflicts | infeasible, two hard conflicts | two mask errors |
| seed11 | feasible, zero hard conflicts | feasible, zero hard conflicts | zero errors |

Both native cases retain 46 opens and all original board/project bytes. Board
hashes remain `f9bae0799371fd6cf08c9db9d5c302f4d444c8009fafc39fa5141f6fde451fbe`
and `8b3e27fc1729a4901c5fc492a847a1764a1b48f517bc71d60c2ae2c52f796715`;
project hash `9a9ab694fc8a9a456a6791060d998c01a171e30308a170e1b4f97d864b2b6395`.
The original intent still refuses at `intent.assembly.sides`, and the full frozen
problem still refuses duplicate physical `Ref*`. Those blockers are not bypassed
or relabeled as M1 success. Native DRC remains mandatory and rejects seed6.

## Runtime and artifacts

Supported image `agent-ee:exec-b446111f323d3d93`, ID
`sha256:6533216670ed0bbfabf5dc9d535ce25d59a35bb6c352dda2f99937484b958e7e`;
its build-input label was recomputed and matched. Python 3.12.3, KiCad/pcbnew
10.0.7. The installed planner's complete current-source receipt verifies;
binary SHA-256 `aaf5a67344b8b09859ec62de78dc79027e02c3723efcaeb03c17838cc331a768`.
No alternate binary/model route was used. Tested source/test hashes match the
delivered files and are recorded in `runtime-binding.json`.

Retained outside the worktree:
`/home/halbritt/.cache/agent-verification/agent-ee-569-mask522-repair-20261025/`.
`work/article/` has exactly the two approved cases and original input manifest;
`work/replay/summary.json` records successful repaired-capture and native replay.
`red.log`, `green-tracer.log`, `native-focused-modules.log`, API/bbox/persistence
probe logs and the failed setup logs are retained. `artifact-sha256.json` gives
the exact artifact manifest. No original inputs, failed evidence or native rules
were changed; no purchase, upload, telemetry-pod action or issue closure occurred.

Artifact manifest SHA-256:
`15ebccacade3a746d61d4dc4590cfd08d0145b65b321e5687c078645ca6f7e77`.

## Chief independent verification and acceptance

Agent-570 independently inspected the repair, native API/source boundary and
all 49 indexed worker artifacts, accepting the change with the documented
native-persistence and diagnostic-projection limits. Agent-564 also verified
all 49 artifact hashes, inspected the diff, and passed the 17 strict native
tests with no skips on the delivered source. After rebasing onto `02067921`,
the chief repeated those same 17 tests against the current runtime and replayed
the exact retained seed6/seed11 inputs. No search or new layout was constructed.

The repaired classifier's real mask geometry again changes seed6 from zero
to two hard conflicts while retaining seed11 with zero. Independent native DRC
reports two mask errors for seed6, zero for seed11, and 46 opens on both. All
input board/project and manifest hashes remain unchanged. The current installed
planner receipt verifies binary `c073d457c89be2a608abf7bc10ab4a7972466381f022c005d77849ea73877d61`,
which includes the already integrated AGENTEE-528 identity repair.

The first chief replay controller incorrectly expected the full historical
unrepaired model to be infeasible after identity admission. That assertion
failed: it reports feasible for seed6 because it still contains the old mask
capture. Both the failed controller and corrected observation are retained.
The corrected controller changes only that historical expectation; all repaired
mask projection and native DRC assertions are unchanged and pass. This is not a
whole-job acceptance claim. Rich public intent remains truthfully refused until
AGENTEE-479; the old duplicate-reference refusal is superseded by AGENTEE-528.

Chief evidence, copied outside the disposable worktree, is at
`/home/halbritt/.cache/agent-evidence/agent-ee-564-integration/mask-529/`.
It includes both native-test logs, exact replay inputs/results, both controller
logs, current source/runtime binding and 33 tested source/test hashes checked
again during integration. No native rule, input or manufacturing threshold was
relaxed. Acceptance closes this capture repair; it does not qualify either
46-open board, a complete placement run or M1.

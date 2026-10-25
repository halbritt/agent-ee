# AGENTEE-522 — missing mask-graphic expansion, 2026-10-25

**First divergent boundary: `classify_footprint` captures mask graphics with
`_bbox(g)` but omits their resolved native solder-mask expansion.** The U2
graphic has a 50,800 nm expansion in KiCad. Pads already receive their resolved
expansion; graphics do not. The graphic is present, and its saved/transformed
coordinates are correct. This is not evidence of missing graphic extraction or
a rotation/save bug. No production implementation is included in this report.

Investigation source: `7bb195de14f6874202df196830272d0e17700770`.
Scope and stopping conditions were read from AGENTEE-522 and the current
recovery instructions. Native rejection remains mandatory. The historical
candidate was rejected; this investigation establishes no escaped package defect,
current whole-job eligibility, board acceptance or issue closure.

## Exact article, native witnesses and preservation

Historical failing article:
`build/agent545/board-run-portfolio/candidates/a1-00-seed6/`.
The complete frozen problem is `board-run-portfolio/problem.json`, SHA-256
`5f60dc24606fdd9eb097ee08e5ab6614087f501321ea960f664fe35bfef58e26`.
Retained `engine-output.json` reports seed6 `hard_count=0` and feasibility.

Current `materialize` applied its complete historical pose vector to an isolated
copy of the original input, saved and reloaded it. Its integrity report has no
problems. The saved board is **byte-identical** to the historical failing board:
SHA-256 `f9bae0799371fd6cf08c9db9d5c302f4d444c8009fafc39fa5141f6fde451fbe`.
All 21 footprint UUIDs and their posed coordinates are preserved. Original and
isolated input bytes were rehashed and match; the unchanged project SHA-256 is
`9a9ab694fc8a9a456a6791060d998c01a171e30308a170e1b4f97d864b2b6395`.
No custom-rule companion exists in these captured inputs.

Fresh KiCad 10.0.7 DRC reproduces exactly the two mask errors against polygon
`ea372e2b-574f-4f26-bd71-4293918f1ee8` of U2:

| Other native item | UUID |
| --- | --- |
| C1 pad 1, `Net-(C1-Pad1)` | `c9fe2374-c58b-407e-90b5-0df5c3cd3078` |
| U2 pad 2, `Net-(C1-Pad1)` | `6e34de5f-e239-44f9-9f76-e35b6d5e57f0` |

The legal mask control is the retained **seed11** pose vector from the same
problem, materialized from the same original inputs/project. Fresh DRC reports
zero errors, including zero mask errors. Its saved board hash is
`8b3e27fc1729a4901c5fc492a847a1764a1b48f517bc71d60c2ae2c52f796715`.
Both cases retain **46 opens**: these are unrouted placement articles, not
complete electrical or manufacturing controls. Other warning categories remain
in the raw reports.

## Boundary trace and decisive comparison

| Boundary | Observation |
| --- | --- |
| Native input | U2's polygon has an eight-vertex contour, 200,000 nm stroke and native `GetSolderMaskExpansion() == 50800`. Minimum web is also 50,800 nm; mask-to-copper clearance is zero. |
| Capture | `placement_job.py::classify_footprint` grows pad bounds by `_mask_expansion(p)` but appends mask-graphic `_bbox(g)` unchanged. U2's three mask rectangles, including the graphic, are in the frozen problem. |
| Local graphic rectangle | Captured `[-1000000, -2500000, 1000000, 2400000]` nm includes stroke but lacks the native expansion. Applying that expansion conservatively gives `[-1050800, -2550800, 1050800, 2450800]`. |
| Transform/save/reload | The seed6 U2 pose is `(141550000, 98500000, q=1)`. Native local bounds and the exact polygon vertices match the frozen model and historical candidate after current materialization/reload. |
| Evaluator | `job.rs::placed` transforms captured mask rectangles; `pair_terms` grows one by the captured web and tests overlap. It cannot recover an omitted per-graphic expansion. |
| Native DRC | It uses the graphic's resolved expansion as well as the minimum web. The two original errors persist under fresh native checking. |

At seed6, the captured graphic's left world bound is 139,050,000 nm; C1's
pad-mask right bound is 138,958,800 nm. The captured gap is **91,200 nm**, above
the 50,800 nm web. Native graphic expansion reduces that gap to **40,400 nm**,
below the same web. No new threshold is needed to explain the divergence.

The primary [KiCad 10.0.7 mask DRC source](https://github.com/KiCad/kicad-source-mirror/blob/10.0.7/pcbnew/drc/drc_test_provider_solder_mask.cpp)
confirms that mask shapes contribute their resolved expansion during aperture
construction and collision checks (`addItemToRTrees`, lines 147–160, and the
collision visitor, lines 567–590). Its aperture-net handling can report several
exposed copper items for one opening; solver pair counts are not asserted to be
a one-to-one translation of native net-aware findings. The source capture URL,
time and hash are retained privately.

### Current source versus historical solver eligibility

The source-bound current planner refuses the **unchanged full frozen problem**
with `duplicate physical reference Ref*`. Those are two fixed copper marks.
The original intent also truthfully refuses at `intent.assembly.sides` under
the current public contract. These results are retained; neither the records
nor operative declarations were filtered to claim current whole-job success.

To isolate the mask calculation, the deterministic diagnostic projection keeps
only the complete, unchanged C1 and U2 footprint rows, original outline/rules
and their exact seed6/seed11 poses. Groups and proximity preferences involving
the omitted population are removed explicitly. This is a mask investigation
input, **not** a substitute representative or accepted placement job.

| Two-footprint diagnostic model | Seed6 | Seed11 control |
| --- | --- | --- |
| Original captured mask bounds | feasible, zero hard conflicts | feasible, zero hard conflicts |
| Only U2 graphic bound enlarged by its native 50,800 nm expansion | infeasible, two hard pair conflicts | feasible, zero hard conflicts |
| Full native placement article | two mask errors | zero native errors |

The current binary evaluates these fixed vectors only. No seed portfolio or
placement search runs. This source-bound calculation, the measured gap and the
byte-identical native reconstruction identify capture as the first divergence.

## Runtime, reproducer and limits

Supported `ae-ci-exec` runtime, actual admission weight 1, native command bounds
of 180–600 seconds. Image `agent-ee:exec-b446111f323d3d93`, ID
`sha256:6533216670ed0bbfabf5dc9d535ce25d59a35bb6c352dda2f99937484b958e7e`;
build-input label recomputed and matched. Python 3.12.3, pcbnew/KiCad 10.0.7.
Current planner binary SHA-256
`aaf5a67344b8b09859ec62de78dc79027e02c3723efcaeb03c17838cc331a768`, receipt
SHA-256 `2a96fbea9cc1fcb764838fdc225471913ed951b02de067bb4d12e4a01cfd7531`.
`scripts/build_placement_engine.py::verify` matched all current source hashes,
binary, lockfile and pinned toolchain receipt before evaluation. The historical
job records a different binary (`01a0a470…`); its feasibility is historical data.
No alternate or unverified planner was used.

Private retained evidence:
`/home/halbritt/.cache/agent-verification/agent-ee-569-mask522-20261025/`.
`article/` retains inputs, manifests, native geometry/expansion, full planner
refusal, projected requests/results, materialization integrity records and all
native reports. `reproducer-check/summary.json` records a successful fresh replay.
`artifact-sha256.json` indexes the retained files; its SHA-256 is
`f4e657a9863c168e1819e3fcc1080be2405a9d42689d3aac704cc95d3d9b4b54`.
Original inputs remain untouched.

The minimal replay is `article/reproduce.py`: copy the retained article bundle
into the managed worktree's ignored `build/agent569-mask522/`, then run inside
the supported runtime:

```sh
python3 build/agent569-mask522/reproduce.py build/agent569-mask522 NEW_OUTPUT
```

It verifies the source-bound binary, evaluates the two explicit projected models
and reruns DRC on the two fixed native boards. It asserts the table above and
that native checking leaves board/project bytes unchanged. This replay completed
successfully. No broad suite or board qualification is claimed.

**Scope deviation:** two attempted C1-only separation controls were constructed
before selecting retained seed11. The lateral attempt collided with C3; the
vertical attempt collided with CON2. They exceeded the intended two-case
discipline and are retained under `control-setup-invalid*`, excluded from legal
control evidence. No sweep/search followed. Two diagnostic API assumptions and
an incorrect initial 35-footprint assertion also failed; their logs are retained.
The actual article has 21 footprints. Native import diagnostics are unfiltered.

## Smallest justified follow-up

Recommend a focused capture repair that conservatively includes each F.Mask/
B.Mask graphic's **resolved native expansion**, before converting its bounds to
the local frame. Keep native rules and rejection unchanged; preserve existing
zero/negative-expansion conservatism and refuse unqualified geometry rather than
silently dropping it. No implementation is authorized by this report itself.

Acceptance should use an owned graphic fixture with inherited and overridden
expansion, zero/negative cases and quarter-turn transforms. Positive graphic
expansion must appear in capture and prepared feasibility; the fixed seed6
diagnostic must reject and seed11 must remain mask-legal. Repeat native
materialization/reload/DRC on these exact articles and preserve both project/rule
bytes and failed artifacts. Keep the unrelated full-problem reference/intent
compatibility blockers explicit rather than changing them in this repair.

Planning provenance: Pincite packet `pkt-91cd13023c07e6f9`, content hash
`91cd13023c07e6f9ba5e793903e201496ea46581b91dd0058f15acf4f8cb1b1e`, using
`universal-evidence-before-intervention`. The private decision receipt records
the material limits and deferred repair acceptance; unrelated monitoring,
concurrency and bulk-ingest obligations do not establish this diagnosis.

## Chief review

Agent-564 inspected the current capture branch and KiCad 10.0.7 mask DRC source,
recomputed the evidence-index digest and all 94 indexed file hashes, and read
the native expansion measurement and successful fixed-case replay summary.
They agree with the stated first divergence. This review accepts the bounded
diagnosis and a narrow capture repair; it does not claim an independent native
rerun, whole-board feasibility, production repair or M1 completion. The repair
must receive its own native positive/negative verification before integration.

# Exact-gap straight conductor emission: native feasibility result

2026-10-23, agent-510, AGENTEE-492 emitter slice. **Straight-section feasibility
PASS. The production checker is unchanged and its exact-mode acceptance remains
open.** Agent-449 requested this bounded experiment under relayed ruling
`8683ae8e@1`, request `db3e0a68-1695-4dd9-b606-0e1857875cf2@1`.

The nominal saved and refilled KiCad board has a 293000 nm RF trace, exactly
203200 nm physical copper gaps on **both** flanks, connected explicit ground
conductors, and connected filled In1 ground. Native KiCad DRC reports **zero
violations and zero unconnected items**. The minimum clearance remains .2032 mm;
the custom RF width rule has minimum and maximum .293 mm. No clearance reduction,
500 nm equality allowance, solver query or launch exception was used.

This is a generated feasibility fixture, not the telemetry pod or a production
routing result. It does not qualify launches, curved routing, impedance,
fabrication tolerance or the full board. Schematic parity was not requested.

## Inputs and execution

Frozen source: `583502771b40a287d984a45fd98b3fb53a6ef319`, unchanged checker SHA256
`46975c4fe3cfbac14bd3906c3bfad4cf8e3f399d671e8416634367a95675c7e1` and fixture SHA256
`3225c6c85ab7112292cade1aef33dc51678c88ac8ada1c3cd55ef62fee8e58cb`.
The new standalone emitter does not alter or depend on the older fixture generator.

Execution used `AE_CI_WEIGHT=2 AE_CI_MAX_WAIT=120 ae-ci-exec` on **peecee-wsl**,
including its host admission, container lease label and resource restrictions.
No direct Docker execution bypass was used. Native version: **KiCad 10.0.7**.
Runtime image tag `agent-ee:exec-149276250b0861ec`, peecee image ID
`sha256:a73690f0bfca48731b314ab12b81119e0c0ba93bd57fa3e6df342288894ca530`.
This differs from the prior proximal image ID; the runtime is recorded separately.
Python source was mounted from the frozen worktree plus the diagnostic scripts.

## Construction and exact measurements

The RF segment runs from (10000000, 20000000) to (30000000, 20000000) nm on F.Cu.
Its width is 293000 nm. Two 1000000 nm wide GND segments extend from x=8000000
to x=32000000 at y=19150300 and y=20849700 nm. Their centerline offset is 849700 nm.
Therefore each ordinary-section copper gap is exactly:

```text
849700 - 293000/2 - 1000000/2 = 203200 nm
```

The reported equality decision uses integer/rational squared distances, not the
rounded display value: `cross_product² / signal_length² = 721990090000 nm²`,
which equals `849700²`. Native saved endpoints and widths are retained in
`run4/nominal/measurement.json`. The conductor segments extend past the RF endpoints,
so their endcaps do not replace the parallel boundaries within the ordinary span.
RF endpoint pads are circular and exactly the trace diameter; this fixture does
not introduce an enlarged production launch or a launch exception.

Both GND conductors join through a crossbar outside the RF span. Four GND through
vias connect to a filled In1 zone. A physical F.Cu GND terminal anchors the native
connected component. After load, `BuildConnectivity` and `GetConnectedItems`
include both flanks, all four vias and the In1 zone in that terminal's component.
A second native fill/save/reload preserves those connections and the exact signal
and flank endpoints/widths. Island removal is ALWAYS for the reference zone.
The separate verification script reloads the final board again, recomputes both gap
certificates and connected membership, and reads the actual DRC JSON and rules.

There is **no F.Cu ground zone** in this witness. The flanks are connected GND
track primitives, so KiCad's zone filler does not move their facing boundaries.
There is an In1 ground zone. This demonstrates a legal available emitter strategy;
it does not establish that a production router currently creates or preserves it.

## Final controls (run4)

All rules remain identical across the six controls, with explicit `mm` units.
The ordinary RF target remains .293 mm; altered widths are intentional negatives.

| Control | Native RF width (nm) | Both physical gaps (nm) | Native DRC | Exact physical policy |
| --- | ---: | ---: | --- | --- |
| Nominal | 293000 | 203200 | 0 violations, 0 unconnected | Straight witness passes |
| Gap below | 293000 | 202200 | 3 clearance errors | Fails gap |
| Gap above | 293000 | 204200 | 0 violations, 0 unconnected | Fails exact gap despite DRC pass |
| Width narrow | 283000 | 203200 | 1 track-width error | Fails width |
| Width wide | 303000 | 203200 | 1 track-width error | Fails width |
| 45-degree rounded construction | 293000 | approximately 203199.813360283 | 0 violations, 0 unconnected | Not exact; not accepted |

The clearance negative explicitly names rule **RF physical clearance**, required
.2032 mm and actual .2022 mm. The wide-width negative names **RF exact ordinary
width**, maximum .293 mm and actual .303 mm; the narrow control exercises its
minimum. These prove the custom rules are actually consumed. DRC exit code alone
is not the result: `kicad-cli pcb drc` returns process success even with violations;
the verifier checks their categories and counts in JSON.

### Diagonal boundary

The emitted 45-degree integer-coordinate centerlines are parallel. Their exact
squared center distance is `1443979545649/2 nm²`, not the required `721990090000 nm²`.
Both gaps are approximately 0.186639717 nm below nominal. This is a grid-rounding
observation, not a numerical acceptance allowance. Native DRC passing this tiny
difference is not an exact-equality certificate.

For parallel 45-degree lines through integer-nanometer points, their separation
is `|integer| / sqrt(2)` nm. A nonzero rational required center separation such as
849700 nm cannot equal that expression. Thus this particular parallel-track
representation cannot realize the exact continuous physical gap at 45 degrees
with integer coordinates and integer widths. A separately reviewed serialization
contract may address this representation limit; this experiment does not introduce
one. Axis-aligned straight feasibility is demonstrated; diagonal exact acceptance
and curves remain outside the demonstrated result. No route-policy change follows.

## Retained attempts and corrections

- `run1.log`: stopped before executing the probe because the remote main object
  store lacked the frozen commit. The exact commit was then fetched from the
  existing reviewer clone and checked out; no substitution of another source.
- `run2`: exact straight geometry and connectivity obtained, but default footprint
  text at the origin produced six silk violations. Initial custom rule literals
  lacked units, and the altered-width controls did not demonstrate enforcement.
- `run3`: fixture reference/value text moved to F.Fab, removing the unrelated silk
  violations; width controls still did not validate the unitless custom rule.
- `run4`: explicit `mm` suffixes on the required custom width/clearance constraints;
  the intended rules demonstrably active through negatives. This is the accepted
  feasibility evidence. Global project minimum clearance was .2032 throughout.

Every attempt and its output is retained. No failed trial was overwritten. Earlier
runs are diagnostic history, not passing custom-rule evidence. No production source,
checker or rule file was changed.

## Reproduction and retained evidence

From the frozen source plus these diagnostic scripts, inside an isolated worktree:

```sh
AE_CI_WEIGHT=2 AE_CI_MAX_WAIT=120 ae-ci-exec . -- \
  python3 docs/agentee-492/emitter_probe.py build/492-emitter/run4
AE_CI_WEIGHT=2 AE_CI_MAX_WAIT=120 ae-ci-exec . -- \
  python3 docs/agentee-492/emitter_verify.py build/492-emitter/run4
```

Use a new output directory for another run; the producer refuses to overwrite
existing case directories. The verifier checks all six final boards, two-sided
integer/rational geometry, native ground connectivity, unchanged refill geometry,
unchanged rule targets, and the DRC control outcomes. Both commands exit 0.

Evidence root:
`/home/halbritt/.local/share/agent-ee/reviews/2026-10-23-agentee-492-emitter-agent510/`.
`run4` contains six input/final native boards, project and rule files, native DRC
JSON/stdout/stderr, per-case measurements and an aggregate result. `verify.log`,
execution/source identities, script copies and `SHA256SUMS` bind the evidence.
There is no production algorithm change to benchmark or roll back. The next
implementation slice remains responsible for request/board binding, connected
reference evidence, interval provenance, nonnegative tolerances and regressions.

The earlier report is also corrected for Fable G1: freshly filled floating In1
copper reproduces the CPW-only gap, while the composed pod-shaped stitching check
already fails closed when all vias are absent. Fable G2 is addressed by this
slice's admitted wrapper execution, not by retroactively relabeling earlier runs.

The admitted documentation and origin checks also passed (`errors: []`); no broad
regression suite was run for this diagnostic-only change. The remote scratch
worktree is removed after retaining the results. The local `wt/agentee-492` branch
is handed to agent-449 unmerged, as dispatched.

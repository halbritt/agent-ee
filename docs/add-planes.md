# Captured plane production

`agent_ee.add_planes` implements AGENTEE-183 as the declared `add-planes`
routing-stage producer. It runs after placement and before the dedicated
[routing adapter](routing-adapter.md). It does not invoke a model or select nets,
layers, geometry, clearances, island policy, or thermal settings.

## Plane plan

Capture the plan as an immutable scope input and baseline target. Paths passed
to the producer are relative to the captured input root. A plan has exactly
`schema` and `zones`; every zone declares all the fields shown here:

```json
{
  "schema": "agent-ee.plane-plan/1",
  "zones": [{
    "net": "GND",
    "layer": "In1.Cu",
    "outline": "board",
    "clearance_mm": 0.25,
    "min_width_mm": 0.2,
    "thermal": {
      "connection": "thermal",
      "gap_mm": 0.3,
      "spoke_width_mm": 0.3
    },
    "priority": 0,
    "island_removal": "always"
  }]
}
```

This is a format example, not the telemetry pod's engineering plan. Supported
outlines are `"board"` (one explicit closed native outline, including its holes)
or `{"polygon_mm": [[x, y], ...]}` in absolute board coordinates. Polygon closure
is implicit; repeated vertices, self-intersections and zero area are refused.
Every net must already exist and every selected copper layer must be enabled.
`thermal.connection` is `solid`, `thermal`, or `tht_thermal`; `priority` is an
integer from 0 through 100. Plan size is 1 through 64 zones. Dimensions must be
finite; clearances, widths and thermal dimensions are 0.001 through 100 mm.

Island removal is explicitly `always` or `never`. This matters before routing:
an isolated inner-layer region may have no connection yet. The producer never
changes the policy to make a fill pass. A selected policy that leaves any planned
zone without filled copper is a refusal. Engineering must choose the intended
policy in the captured plan. In KiCad 10, `always` removes disconnected secondary
islands when the zone has a connected main pour. A wholly unconnected zone can
retain its fill and emit native DRC `isolated_copper` warnings instead. The
producer refuses any `isolated_copper` violation attributed to a planned
`always` zone, including warnings and exclusions, and reports the exact zone
UUIDs. `never` retains disconnected copper; its isolated-copper warnings remain
in the native report without causing this policy refusal.

Zone UUIDs are deterministic from the complete plan and list index. The same
plan can refill its own zones on later routing turns. A changed plan does not
authorize deleting zones from an older plan. Existing board copper zones must
have the exact planned identity, name, net and layer; other copper zones,
including footprint-owned copper zones, are refused. Native rule areas are
preserved and honored by KiCad fill.

## Coordinator use

```python
from agent_ee import add_planes

plane_step = add_planes.step(
    "/absolute/agent-ee/src",
    board="project/pod.kicad_pcb",
    plan="requirements/planes.json",
)
```

Put this declaration in the campaign's routing `steps`, followed by the
dedicated `routing_adapter` declaration. The step adopts only exit 0 and writes
only the board. Do not put the plane plan or native board in `writer_editable`.
AGENTEE-197 verifies the captured-source → plane receipt → owned-router receipt
chain and refuses writer edits before the first routing producer. The router
retains its own semantic verification and `--protect existing` contract.

`generate(captured_root, board, plan, new_output)` supports an isolated local
probe. The output must be new and outside the captured root. The CLI runs inside
the existing step sandbox, reading `/candidate` and writing `/work/out`.

## Native custody and failure behavior

The existing networkless executor launches `pcbnew` in child processes. The
producer copies the board and project/settings/rules siblings into private
scratch space, creates only planned zones, fills them natively, and saves. Any
`.kicad_pro`, `.kicad_prl` or `.kicad_dru` sibling created or rewritten by SaveBoard
is removed or restored in `finally`.

Only the planned top-level zone expressions from that saved board are spliced
into the original UTF-8 bytes. All bytes outside those expressions survive,
including footprints, pads, tracks, vias, rule areas, outlines and metadata.
A second native child opens the delivered form and checks zone inventory,
settings, outlines and nonempty actual fill. Native DRC then checks that form
without refilling or saving it. Execution failure, malformed DRC output, or an
error citing a planned zone or `isolated_copper` citing a planned `always` zone
prevents publication of `out/board.kicad_pcb`.
The report must include all native severities; each violation must have a known
severity, nonempty type, and well-formed item UUIDs. An error without item
attribution is unknown and refuses publication. An `isolated_copper` violation
without attribution also refuses when any planned zone uses `always`. Such
records cannot be classified as
unrelated to the planes.
The report retains the whole native DRC and counts other findings; existing
unconnected items and unrelated defects remain subject to later campaign checks.

`out/planes.json` records the plan hash, input/output board hashes, planned UUIDs,
native KiCad version, filled areas and DRC evidence hash. Failed production has
`outcome: unknown`, no published board, and retained diagnostics. The enclosing
stage receipt binds all captured inputs, tool source, output bytes and artifacts.

## Qualification boundary

Native tests use an original four-layer probe with In1 GND and two In2 power
regions. They exercise byte preservation, repeated fills, native footprint
keepouts, unplanned-zone/missing-net refusal, saved fill validation, native DRC
defects, and board-only receipt adoption. Native island-policy regressions check
wholly unconnected zone refusal under `always`, its retained fill under `never`,
and a split zone whose secondary island is removed only under `always`, with
saved fills inspected in fresh processes. The optional routing test uses the
qualified pinned KRT backend and checks `pour_integrity` after actual signal
routing. Its explicit empty schematic retains baseline parity findings: this
test establishes routing/plane preservation, not schematic correctness.

Actual pod use still requires AGENTEE-177's captured board envelope and native
antenna/mechanical rule areas, the accepted pod plane plan, and integration with
AGENTEE-202's router pin. The producer does not infer antenna keepouts from
reference names or invent a mechanical envelope. Native fill only respects the
keepouts actually present in the captured board. Full fresh DRC, schematic
parity, routing checks and pour integrity are still required after routing;
owner and bench obligations remain open. No fabrication is authorized here.

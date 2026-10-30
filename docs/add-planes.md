# Captured plane production

`agent_ee.add_planes` implements AGENTEE-183 as the declared `add-planes`
routing-stage producer. It runs after placement and before routing, through the
[normal build](build-workflow.md), placement job or dedicated
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
producer refuses an `isolated_copper` violation attributed to a planned
`always` zone, including warnings and exclusions, and reports the exact zone
UUIDs. Exception (AGENTEE-487): only when the caller declares the pre-routing
phase (`step(..., phase='pre-routing')`, argv `--phase pre-routing`, as the
routing-turn step does) may a wholly unconnected planned `always` zone be
deferred. Wholly unconnected is proven by the native polygon evidence of the VERIFIED
SAVED fill: the `inspect` child reloads the saved, spliced board and records for every
filled polygon its index, full identity (outline and holes, digest of the exact nm
vertices) and isolation by KiCad's native shape collision
(`SHAPE_POLY_SET.Collide(effective shape, 0)` against every same-net pad, track, arc
and via on the zone layer, so edge contact counts as connected), bound to the saved
board's sha256 (`verify/facts.json`, `polygon_evidence`, `board_sha256`). Every saved
polygon must be covered exactly once (indexes 0..n-1, n the saved polygon count,
unique identities) and marked isolated; flags must be exact booleans and vertices
finite. The writer and consumer share `plane_polygon_identity.polygon_digest`:
SHA256 of `json.dumps([outline_nm, *holes_nm]).encode()`, retaining native vertex
and hole order and the default JSON separators. The writer retains authoritative `outline_nm` and `holes_nm` integer arrays from
the reloaded native fill. The consumer rejects Boolean, float and nonfinite nm
coordinates without coercion. Millimetre display arrays must reproduce those
exact nm arrays (allowing only binary-float round-trip error); contradictions
refuse. The consumer validates
nonempty chains with nonzero area (including KiCad's opposite-direction bridges
for fractured holes), recomputes the digest and requires exact
lowercase hexadecimal equality. Coverage uniqueness uses these recomputed identities,
not labels supplied by the evidence. Empty holes, self-intersections and off-grid
coordinates refuse. Missing, duplicate, stale, malformed or contradictory evidence refuses;
partial isolation is not deferred. KiCad 10 DRC places every `isolated_copper` marker
of a zone at the zone anchor, so the markers are ZONE-LEVEL corroboration only (the
zone must have at least one, each with a finite position); they are never polygon
witnesses and never fill missing coverage. The raw DRC findings are kept. Flags must be
exact booleans and positions/vertices finite numbers; duplicate, unmappable,
ambiguous or non-finite evidence and a malformed or missing native record fail
closed; partial isolation is not deferred. A deferred zone
keeps its definition and `always` policy and is listed in
`deferred_connectivity` as `{zone, net, layer, island_removal: "always",
obligation}`; the report also records `phase`. Without the phase input (any
other caller and every post-routing use) the zone is refused as before. The
post-routing consumer that discharges the obligation is a separate component. `never` retains disconnected copper; its isolated-copper warnings remain
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
the board and its producer report. By default the report is named
`<plan-stem>-producer.json` beside the plan; `step(..., report=...)` can name
an explicit candidate path. Keep the plan, board and report outside
`writer_editable`. Capture an empty report output slot before preparation;
this reserves the inventory without inventing a report or its eventual hash.
The campaign must list the report in `mutable_inputs`. With `pod_driver`, keep
the initial scaffold scope's mutable list limited to native files: the existing
`_final_scope` adds declared step outputs, including this report, after scaffolding.
AGENTEE-197 verifies the captured-source → plane receipt → owned-router receipt
chain and refuses writer edits before the first routing producer. The router
retains its own semantic verification and `--protect existing` contract.

`generate(captured_root, board, plan, new_output)` supports an isolated local
probe. The output must be new and outside the captured root. The CLI defaults
to the existing step sandbox, reading `/candidate` and writing `/work/out`;
`--inputs DIRECTORY --out NEW_DIRECTORY` selects explicit locations. All native
children still use the existing networkless executor.

In `agent-ee place run`, optional `inputs.planes` has the same `{path, sha256}`
shape as the other captured inputs. Unknown input names are refused. The job
validates the plan before search, gives the router the produced native zones,
and retains each trial's production report. After routing it runs the producer
again in strict post-routing mode to save a fresh fill, followed by the
independent `final_planes` consumer. Only UUIDs derived from the controller's
captured plan are exempted from the existing protected-native-content comparison;
the final consumer checks their exact definitions and connectivity. The source
policy identity for these tools is included in checkpoint identity.

A final DRC check can declare `plane_plans: [{path, sha256}]` for direct native
verification against captured source intent. Missing, stale, duplicate or
otherwise unbound plans refuse. This does not manufacture legacy campaign
producer/composition evidence: those reports, when captured, still require
their existing complete `plane_obligations` bindings.

An explicitly planned current board may also capture source plane plans inside
reference blocks. A source plan is separate only when a standard `block.json`
validates the complete payload, every payload hash matches the captured inventory,
and `source.paths` lists the plan. A block containing the current board or any
selected plan gets no such exemption. This distinction applies only to plane-plan
documents: embedded producer reports and compositions still need their bindings.
Payload validation establishes custody, not source qualification.

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
defects, and board/report receipt adoption. Native island-policy regressions check
wholly unconnected zone refusal under `always`, its retained fill under `never`,
and a split zone whose secondary island is removed only under `always`, with
saved fills inspected in fresh processes. The optional routing test uses the
qualified pinned KRT backend and checks `pour_integrity` after actual signal
routing. Its explicit empty schematic retains baseline parity findings: this
test establishes routing/plane preservation, not schematic correctness.
To keep the router off the planes, declare a routing
[layer policy](routing-adapter.md#declared-layer-policy-agentee-231): allowed signal
layers, an area-loss bound and, optionally, the reference plane under RF nets.

Actual pod use still requires AGENTEE-177's captured board envelope and native
antenna/mechanical rule areas, the accepted pod plane plan, and integration with
AGENTEE-202's router pin. The producer does not infer antenna keepouts from
reference names or invent a mechanical envelope. Native fill only respects the
keepouts actually present in the captured board. Full fresh DRC, schematic
parity, routing checks and pour integrity are still required after routing;
owner and bench obligations remain open. No fabrication is authorized here.

### Final DRC obligation binding (AGENTEE-487c)

A final full `drc` check declares `params.plane_obligations`, a list of objects
with `plan`, `producer`, and `composition` links. The plan and composition are
captured immutable inputs with `{"path": "relative/path", "sha256": "digest"}`.
The composition's `planes` list contains the exact plan path/hash link.

For an in-campaign producer, declare the report without a preparation-time hash:

```json
{
  "producer": {"path": "composition/planes-producer.json", "stage": "add-planes"}
}
```

The producing stage records the actual report SHA-256 in its receipt and adopts
`out/planes.json` into that candidate path. The final check resolves the hash
after production, through the verified owned-router receipt and its immediately
preceding add-planes receipt. The producer's complete output tree must equal the
router's input tree, and the report must bind the producing board before/after
hashes and the immutable plan. Missing routing/producer evidence, a missing or
stale report, or a different plan/board refuses the binding. Resolved links appear
in the final check's `details.plane_bindings`. The native saved-board plane
inspection and DRC still run; provenance is not final-board acceptance.

An already captured historical report may still use a fixed `path`/`sha256`
producer link; that mode freezes all three files as before. The stage-owned mode
freezes only the plan and composition; its empty output slot and later report
remain coordinator-owned candidate data. The scope does not change after
preparation, and no report hash is precomputed.

Plan filenames are unrestricted. Captured plane plans and producer reports
without these bindings fail final DRC, including renamed files. Missing files,
stale hashes and declared-but-absent obligations fail. A scope with no plane
artifacts or declarations keeps its ordinary DRC behavior. Placement DRC
projections defer this gate to the full DRC check.

The pod composition producer must supply these links before this consumer can
accept its candidate. Existing composition records without the plane binding
are deliberately not grandfathered into acceptance. The hardcoded pod reference
net list and corridor discovery in `plane_reference.py` remain a separate
follow-up. The empty-fill `UnFill` fixture proves refusal of empty saved fill;
it does not prove that island removal caused that empty fill.

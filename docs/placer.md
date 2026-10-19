# Disposable deterministic placer (AGENTEE-54)

`placer.pack(facts, policy)` proposes a complete placement from the saved-board
[placement probe](placement.md). The policy is captured input, using schema
`agent-ee.packing-policy/1` and explicit `step_budget`, `gap_nm`, `fixed_refs`,
`priority_refs`, `ground_nets`, and `obstacles`, plus optional
`min_courtyard_edge_mm` (default `0.0`). Obstacles declare `side` and
closed straight `polygons` in integer nanometres. Locked footprints are fixed
even when absent from `fixed_refs`; interface-owned fixed references must be
supplied by the coordinator from captured owner intent. When
`min_courtyard_edge_mm` is set (in the packing policy, on the `placer_runner`
request, or derived by `placement_adapter` from the scope's `edge-clearance/1`
check params or `fab_profile` `min_edge_clearance_mm`), movable footprints are
kept at least that margin inside every board outline edge; fixed edge-mounted
footprints (`fixed_refs` and `locked`) are exempt by reference. The margin used
is recorded as `min_courtyard_edge_mm` in the plan and in
`placement-result.json`.

The margin measures courtyard polygons, not text or copper. A driver must
declare enough space for its footprints' other features and grade the saved
board with native DRC. The [rung-1 policy](rung1-qualified.md#placement-policy)
uses 1.5 mm to preserve space for J1's reference text outside its courtyard;
using the copper-edge rule as the courtyard margin alone clipped that text.

The placer sorts explicit priorities first, then descending pad count, then
reference. It tries four quarter-turn rotations at deterministic positions
aligned to board vertices (shifted inward by `min_courtyard_edge_mm`) and
inflated reserved courtyard boxes. It chooses the
smallest integer Manhattan same-net pad distance to already placed parts,
excluding explicitly declared ground nets, with position and rotation tie
breaks. Boxes conservatively reserve a minimum gap around parts the search
places; fixed courtyards and declared obstacles are exact polygons (below). The
full polygon judge checks the complete proposal. No random numbers, wall-clock stopping rule, new
runtime dependency or third-party implementation is used.

The plan records budget and actual steps. Only a complete plan contains
placements. A board too small for its parts yields `packing_infeasible`,
`outcome=defect`, `steps_used=0` and the policy's `step_budget`, with an exact
`proof` of one kind:

| `proof.kind` | Proved | Recorded values |
| --- | --- | --- |
| `courtyard_area` | total same-side courtyard area exceeds board area | `side`, `board_area_nm2`, `required_courtyard_area_nm2` |
| `min_width` | a part's courtyard convex hull is wider, in its narrowest direction, than the outline's hull | `ref`, `board_min_width_squared_nm2`, `footprint_min_width_squared_nm2` |
| `diameter` | a part's courtyard is longer than the outline's longest vertex-to-vertex span | `ref`, `board_diameter_squared_nm2`, `footprint_diameter_squared_nm2` |

Values are exact `[numerator, denominator]` rationals in square nanometres.
The width and diameter proofs hold for every rotation, not only quarter turns,
and cover all courtyards of a part on both sides. A part that could fit only
at an angle the search does not try is not proved infeasible.
Budget exhaustion, finite greedy-search
exhaustion and a conflicting fixed placement yield typed unknown results:
these do not prove geometric infeasibility. Missing/unsupported geometry is
unknown. Initial rotations and pad rotations must be quarter turns. The placer
does not optimize thermal/EMC behavior, decoupling loops, routing or arbitrary
rotations.

### Footprint-owned keepouts (AGENTEE-178)

The existing native `rule_areas` inventory supplies footprint-forbidding rule
areas. Their straight, qualified polygons exclude other footprints' courtyards
on the declared copper sides. The owner is exempt from its own area. When the
owner moves or rotates, its exclusion moves rigidly with it. The search checks
both a candidate courtyard against placed owners' keepouts and the candidate
owner's keepout against already placed peers. Concave exclusions use their
polygons; their bounding boxes only supply search anchors. Keepouts may extend
outside the board; courtyard containment still uses the board outline.
Board-owned footprint keepouts are stationary polygon exclusions too.
Unsupported outlines, malformed footprint flags or layers, and unknown owners
return `unsupported_geometry`, `outcome=unknown`, with no placements.

The native writer rechecks the complete saved board with the placement adapter's
write scope and retains `placement-write-scope.json`; the result includes that
proof as `write_scope`. An out-of-scope save retains diagnostics and publishes
no destination. Native DRC runs as before, with its full raw report retained;
this does not change DRC severity or the registered `footprint-keepout/1`
predicate's current unknown boundary for owned areas. Native tests include a
positive peer-in-keepout DRC control and fixed/moving-owner placement runs in
the networkless sandbox. Committed fixtures are original; the adapter also tests
the installed ESP32-S3-WROOM-1 at runtime without retaining its design files.

### Envelope fixed footprints and holes (AGENTEE-179)

A fixed footprint (in `fixed_refs` or locked) reserves its courtyard polygons on
each side it declares, never their bounding box; declared `obstacles` are the
same kind of exact obstacle. A placed courtyard must not overlap one and must
stay at least `gap_nm` away from it, by exact Euclidean edge distance. Boxes only
prefilter the comparison and supply search anchors, so the notch of a concave
fixed courtyard stays usable. Fixed parts must fit the outline and stay clear
of each other, the obstacles and keepouts by the same rule. Otherwise the plan
is `fixed_conflict`, `outcome=unknown`, naming the later part of the pair.

The coordinator derives the request from the envelope. The placement adapter
adds every `fixed_footprints[].reference` and `mounting_holes[].id` of a captured
envelope to `fixed_refs` (`placement_adapter.envelope_references`). The scaffold
writes the AMPSEAL, SMA, IMU and radio as locked footprints at their declared
transforms and each hole as a locked board-only footprint with a square
courtyard on both sides (see [scaffold](scaffold.md)). A writer move of any
of them is refused by name in the adapter's write scope.

The native writer accepts that scaffold board. A board-only footprint (native
`IsBoardOnly`) is not in the XML export; it is left out of the inventory
comparison only when none of its pads has a net and the export does not name
it. Board-level rule areas are admitted like footprint-owned ones: board
footprint keepouts are already stationary obstacles. Board copper zones are
still refused. `tests/test_pod_placement.py` runs the scaffold, forward
annotation, the sandboxed writer and the registered checks on the pod probe
board. It uses authored stand-in courtyards. The fixed parts and holes keep
their saved geometry, and `courtyard-overlap/1`, `outline-containment/1`,
`connector-edge/1` and `mounting-holes/1` pass. Every rule area survives the
writer unchanged, and a fresh-process reload of the placed board gives the
same exact pad layer sets (copper and `F.Mask`/`B.Mask`) as the annotated
source. The generic run uses the fixture's ordinary explicit board keepout.
A second run takes the radio's antenna zone only from the explicit
[captured-CAD retention input](scaffold.md#explicit-captured-antenna-cad-retention)
(AGENTEE-211). The placer treats that module-owned zone as an obstacle, and
`keepouts/1` stays `unknown` for the antenna. Minimum edge clearance
(`min_courtyard_edge_mm`, AGENTEE-180) still applies to movable parts only:
fixed parts and holes are exempt by reference, but they remain exact
obstacles. No fastener head, standoff or IMU isolation distance is declared,
so none is reserved.

### Pad shapes (AGENTEE-109)

Pad outlines never enter the search or the judge: the packer uses pad centres
for wire cost and courtyard polygons for space. Pads must still have a
reviewed native shape, so that a plan states what it covers. The accepted
native `PAD_SHAPE` values, read from the KiCad 10.0.7 pcbnew bindings, are:

| Value | Shape | Stock examples |
| --- | --- | --- |
| 0 | circle | `PinHeader_1x03_P2.54mm_Vertical` pads 2 and 3 |
| 1 | rect | header pad 1 |
| 2 | oval | |
| 4 | roundrect | `R_0805_2012Metric`, `C_0805_2012Metric` |
| 5 | chamfered rect | |

TRAPEZOID (3), CUSTOM (6), any other value, a non-integer shape, a pad angle
that is not a quarter turn, and a capture with pads whose recorded
`pcbnew_version` is not major version 10 give `unsupported_geometry`,
`outcome=unknown`. The enum is read only for the probe version it came from.
Every complete plan records the domain as `pad_shapes`, the value of
`placer.pad_shape_qualification()`:

```json
{"pcbnew_major": "10", "accepted": {"0": "circle", "1": "rect", "2": "oval",
 "4": "roundrect", "5": "chamfered_rect"}, "pad_rotation": "quarter-turn",
 "pad_outline_in_geometry": false}
```

The placement probe records one shape per pad. The native writer therefore
also refuses, as typed `unsupported_geometry`, any pad whose padstack is not
KiCad's normal single-shape mode: a front/inner/back or custom padstack could
carry an unqualified shape on another layer. After saving, the writer reloads
the board and compares every pad's footprint-local facts with the source:
UUID, number, net, attribute, padstack mode, shape, size, offset, drill size
and shape, corner and chamfer ratios, chamfer corners, layer set, relative
position and relative angle, plus each footprint's library identity. Any
difference fails the proposal; it is retained as a `NAME-failure-*`
diagnostic and nothing is published. A placement therefore moves stock
footprints; it never substitutes rectangles for their pads.

`placer_runner.place_candidate(source, netlist, destination, request)` is the
native writer. `netlist` is a captured native XML export: reference inventories
and every assigned physical terminal net must agree with the saved board.
The request contains `policy` plus explicit `authorization` with schema
`agent-ee.placement-write/1`, `single_design_writer: true`, exact
`source_sha256`, and an existing resolved `writable_root`. Destination must be
a fresh direct child of that isolated scratch directory, and the source must
be outside it. The writer refuses existing board tracks/vias/copper zones,
footprint-owned copper zones and footprint-owned board edges. Board and
footprint-owned rule areas are admitted. It acquires the repository's nonblocking writer lease, stages a new board, applies only movable footprint positions/rotations,
then reloads the saved bytes. Static positions, rotations and native identities
are checked against the plan; netlist terminal assignments are checked again.

The actual saved board must pass the polygon judge and an actual native DRC
courtyard evaluation before atomic publication of the complete directory.
Unrelated native DRC findings remain in the raw report and require their own
checks; `placed` does not claim a clean routed design. Failures propagate or
return a typed incomplete plan and publish no partial destination. Native
execution/validation failures retain a separate `NAME-failure-*` directory with
`status=not_placed`, `execution=failed`, `outcome=unknown`, the failed board
snapshot and available plan, saved probe/judgment and raw native DRC diagnostics.
The requested destination remains absent. The source
and netlist hashes are rechecked before publication. Generated
`placement-result.json`, `placement-probe.json`, native DRC JSON and native DRC
console/execution records describe the disposable proposal. Transformed pure
plan judgments are marked `transformed-proposal-not-native`; the final judgment
binds to the actual saved candidate hash. Standalone outputs remain unsealed.

The CLI entry point is `python3 -m agent_ee.placer_runner BOARD NETLIST
--request REQUEST --destination DESTINATION --result RESULT`. Run it under the
existing networkless tool sandbox: read-only `/candidate` inputs and writable
`/work`. The explicit authorization names `/work`; the result must also be a
fresh file directly inside it. The coordinator must capture the request and
XML dependencies immutably, enforce the design writer allowlist and run its
normal fresh checks/independent reviews on the new candidate. This module does
not register a new check kind, edit the baseline or give a proposal acceptance
authority. Integrator-owned CLI/campaign registration remains separate.

Tests use independent saved boards and actual KiCad 10 bindings/DRC. Two
identical inputs yield byte-identical saved board files. A separate path uses
actual native XML exported from the existing owned RC schematic with
independent rectangular mechanical placeholders; it does not qualify those
placeholders as production footprint models. Authorization, net mismatch,
unsupported geometry, insufficient area, deterministic budget and tool failure
boundaries are exercised without fabricating successful tool evidence. Owned
saved boards too small by area and by width give `packing_infeasible`,
`outcome=defect` from both `place_candidate` and the CLI (exit 1). The result
records the step budget. No proposal, staging directory or failure board is
left behind.

The pad-shape tests build owned boards with every accepted shape at quarter
turns, including plated circle and oval pads with drills, and check that the
saved pads match the source. Trapezoid, custom, 45-degree and per-layer
padstack pads, a missing courtyard and a 45-degree footprint refuse as typed
unknown with no destination. A locked footprint stays unchanged. Rewriting
pads to rectangles inside the writer fails and is not published. The stock RC
test exports native XML from the owned RC schematic, loads the unchanged
installed KiCad 10.0.7 `R_0805_2012Metric`, `C_0805_2012Metric` and
`PinHeader_1x03_P2.54mm_Vertical` footprints, fixes and locks J1 on a 50 x 30 mm
outline and places R1 and C1. The saved board passes the judge and native
courtyard DRC, keeps the netlist's pad nets, and its pads match the library
footprints. Unconnected copper is expected before routing; this is not a
routed or schematic-parity result. Schematic parity of the stock RC board
belongs to the rung 1 board build (AGENTEE-96), which consumes this placer.

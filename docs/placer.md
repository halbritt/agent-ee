# Disposable deterministic placer (AGENTEE-54)

`placer.pack(facts, policy)` proposes a complete placement from the saved-board
[placement probe](placement.md). The policy is captured input, using schema
`agent-ee.packing-policy/1` and explicit `step_budget`, `gap_nm`, `fixed_refs`,
`priority_refs`, `ground_nets`, and `obstacles`. Obstacles declare `side` and
closed straight `polygons` in integer nanometres. Locked footprints are fixed
even when absent from `fixed_refs`; interface-owned fixed references must be
supplied by the coordinator from captured owner intent.

The placer sorts explicit priorities first, then descending pad count, then
reference. It tries four quarter-turn rotations at deterministic positions
aligned to board vertices and inflated reserved courtyard boxes. It chooses the
smallest integer Manhattan same-net pad distance to already placed parts,
excluding explicitly declared ground nets, with position and rotation tie
breaks. Boxes conservatively reserve a minimum gap; the full polygon judge
checks the complete proposal. No random numbers, wall-clock stopping rule, new
runtime dependency or third-party implementation is used.

The plan records budget and actual steps. Only a complete plan contains
placements. Proven total same-side courtyard area exceeding board area yields
`packing_infeasible`, `outcome=defect`. Budget exhaustion, finite greedy-search
exhaustion and a conflicting fixed placement yield typed unknown results:
these do not prove geometric infeasibility. Missing/unsupported geometry is
unknown. Initial rotations and pad rotations must be quarter turns and pads
must be native rectangles. The placer does not optimize thermal/EMC behavior,
decoupling loops, routing, arbitrary pad shapes or arbitrary rotations.

`placer_runner.place_candidate(source, netlist, destination, request)` is the
native writer. `netlist` is a captured native XML export: reference inventories
and every assigned physical terminal net must agree with the saved board.
The request contains `policy` plus explicit `authorization` with schema
`agent-ee.placement-write/1`, `single_design_writer: true`, exact
`source_sha256`, and an existing resolved `writable_root`. Destination must be
a fresh direct child of that isolated scratch directory, and the source must
be outside it. The writer refuses existing tracks/vias/zones and
footprint-owned board edges. It acquires the repository's nonblocking writer
lease, stages a new board, applies only movable footprint positions/rotations,
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
boundaries are exercised without fabricating successful tool evidence.

# Placement modules (AGENTEE-53, AGENTEE-92)

The standalone placement modules read actual saved `.kicad_pcb` bytes using
KiCad's `pcbnew` bindings. They add no dependencies and do not modify the board.
`placement_probe.extract(path)` returns `agent-ee.placement-probe/1`, including
board SHA-256, tool version, native UUIDs, pad nets and positions, footprint
positions/rotations, raw courtyard primitives by side, and raw Edge.Cuts from
both board and footprint graphics/fields. Coordinates are integer nanometres.
Native errors propagate; no fabricated fallback geometry is supplied. The probe
CLI refuses an output path that resolves to its input board.

`placement.judge(facts, policy=None)` is an offline pure function. It checks
full polygons, including concave boundaries, using exact rational intersections.
Positive-area same-layer courtyard intersection and positive-area courtyard
outside a proven closed outline produce `verdict=hard_fail`, `outcome=defect`.
Opposite-side courtyards and boundaries that only touch do not overlap. Findings
name references, side and intersection/outside area in square millimetres;
`off_board` lists the references with positive area outside the outline.

A proven contour requires straight, closed, non-self-intersecting geometry.
Segment endpoints must match exactly and have degree two. Rectangles and simple
closed native polygons qualify. Curves, open/branching/duplicate edges, holes,
multiple board contours, nested or touching courtyard contours are unsupported.
Missing Edge.Cuts is `outline_missing`; unsupported Edge.Cuts is
`outline_unproven`. Missing or unproved courtyard coverage gives unknown for
that part, including absence on the footprint's mounting side. A proved defect
remains a defect when another part has unknown coverage. Malformed capture
identities and missing board hashes raise `ValueError`.

The optional captured `agent-ee.placement-policy/1` declaration supplies
`ground_nets`, `ic_refs`, `connector_refs`, `max_shared_refs` (1 through 8), and
`capacitors`. Each capacitor declares `capacitance_nf`, `max_distance_mm`, and
`deduction`. No IC/capacitor role, ground alias, distance requirement or scoring
weight is inferred from a reference or value string. Shared non-ground nets
within the declared reference-count limit associate each capacitor to its
nearest declared IC. A qualifying connector net instead records an interface
filter waiver with its nearest connector distance, but only when that distance
is within the capacitor's own `max_distance_mm`. A capacitor farther from every
connector than its limit falls through to the IC decoupling check, so a rail
that also reaches a distant connector keeps its deduction. Distances are Euclidean
footprint-centre distances, not routed loop lengths. Optional `connector_edge`
contains `max_distance_mm` and `deduction` for an advisory nearest-outline-edge
centre distance. Advisories reduce the initial score of 100 down to a minimum
of zero; they never produce a defect or turn unknown geometry into a pass.
Without a policy the score is `null`: no advisory was evaluated.

The owned native SOIC-8 board with two 0402 capacitors
(`NativeDecouplingScoreTest` in `tests/test_placement_native.py`) declares
100 nF at 2.5 mm and 1 µF at 5 mm with a deduction of 15 each. Saved by pcbnew
and probed in the tool sandbox, it scores 70 with only the two decoupling
advisories, naming C1 and C2, and no hard failure. Moving C1 onto U1's
courtyard is `hard_fail` whether the score is 70 or, under a lenient policy,
100. A VBUS capacitor 2.25 mm from its connector is an `interface_filter` with
no deduction although it also shares VBUS with U1. With a connector that
carries VCC and GND, as the upstream fixture's does, placed far from both caps,
the score is still 70 with both decoupling deductions; moved 3 mm from C2
(within its 5 mm limit) it waives C2 only and the score is 85. The footprint
geometry is written in the test; the 0402 pad offset and size are the generic
values KiCad's stock 0402 footprint also uses. It is **not** the upstream Konnect fixture: that AGPL design
file was not copied, so its exact numbers have not been reproduced.

## Placement check kind

`placement` (AGENTEE-92) is a registered coordinator check. The coordinator
witness names a requirement and a check, not a predicate inside it, so each
check evaluates exactly one `criterion`. Declare one check per requirement.
Every check names an earlier `drc` check in `drc_check`:

```json
{"id": "courtyard", "kind": "placement",
 "params": {"criterion": "courtyard-overlap/1", "drc_check": "drc-placement"}}
```

| `criterion` | Extra parameters | Defect | Unknown |
| --- | --- | --- | --- |
| `courtyard-overlap/1` | none | a native `courtyards_overlap` item of any severity, cited with its footprint UUIDs and positions | footprint without a courtyard on its mounting side; native `malformed_courtyard`; the native check ignored by project settings; helper geometry finds an overlap the native report lacks |
| `outline-containment/1` | none | positive courtyard area outside the single proven outline (the judge above) | missing, open, curved or multiple outlines; missing or unproved courtyard |
| `edge-clearance/1` | `min_courtyard_edge_mm` | exact courtyard-to-Edge.Cuts distance below the minimum, or courtyard crossing/outside the outline | unproved outline or courtyard |
| `footprint-keepout/1` | none | a native `items_not_allowed` item citing a footprint UUID | footprint-forbidding rule area with holes, arcs or several contours, or owned by a footprint; footprint without a qualified courtyard where such an area applies to its side; helper geometry intersects but native DRC is silent; native check ignored |
| `connector-edge/1` | `envelope` | wrong side or rotation; origin farther than `tolerance_mm` from the declared anchor (names edge, measured inset and distance); facing contradicts a declared local mating axis | reference missing or duplicated; outline unproved or declared edge absent from it; facing undeterminable |
| `ratsnest-mst/1` | optional `max_manhattan_mm`, `max_euclidean_mm` | proved total above a declared maximum | Euclidean interval straddles a maximum; no netted pads |

The prerequisite must be `execution=ok` with outcome `pass` or `defect` for the
same candidate and board hash. A DRC defect is consumable evidence. It must
retain `work/<drc>/drc.json` with all three raw lists and all severities, name
the board file, and report the probe's KiCad version. A missing or failed
prerequisite is `not_run`; a corrupt, foreign or version-drifted one is
`malformed`. Both are unknown coverage. The placement check's only tool
execution is the read-only probe; it cites the prerequisite's raw report rather
than running DRC again. Use the DRC `placement-stage/1` projection at placement
and a later full DRC for final acceptance ([DRC](check-reference.md#drc-and-schematic-parity)).

Native semantics established on KiCad 10.0.7 fixtures: footprint keepouts test
the courtyard on the footprint's side layer; boundary contact is not a
violation; a copper-only rule area does not forbid footprints; a footprint
without a courtyard is silently untested by DRC, so the predicate reports it
unknown. `missing_courtyard` is ignored by default and its absence proves
nothing, which is why courtyard presence comes from the probe. A proved defect
remains a defect beside unrelated unknown coverage; pass needs complete coverage.

`edge-clearance/1` takes an explicit scope minimum, bound into the baseline with
the check criteria. The AGENTEE-75 profile has only a copper-to-edge key
(`min_edge_clearance_mm`); it is not reinterpreted as a courtyard limit.

`connector-edge/1` reads a captured AGENTEE-85 `agent-ee.envelope/1` file through
`board_envelope.validate`. Capture requires that envelope to be declared in the
scope with every derived obligation covered. One `connector-edge/1` check
covers the fixed-footprint obligation of an envelope with one fixed footprint
([baseline integration](board-envelope.md#baseline-integration)). `placement_predicates.connector_access` is the
declared-edge predicate. A declaration names reference,
side, edge segment and outward axis, anchor, tolerance, optional rotation,
insertion (`through_edge`, `from_top`, `from_bottom`) and an optional
footprint-local mating axis. That axis is mapped to board axes by mirroring y on
the back and rotating by the saved orientation, qualified against pcbnew pad
transforms. No nearest-edge heuristic replaces a declaration. A connector with a
declared insertion direction but no envelope edge is checked by the AGENTEE-64
[`dfa_dft` connector-orientation/1](dfa-dft.md#connector-orientation), which uses
the same axis mapping against the nearest saved outline edge.

`ratsnest-mst/1` reports, per sorted net, every netted pad (duplicate pad
numbers kept by pad UUID) and two minimum spanning trees over pad centres:
Manhattan with exact integer nanometres, and Euclidean with exact squared-length
ordering and an integer-nanometre interval per edge. Ties break by sorted pad
identity, so the same saved bytes give byte-identical `ratsnest.json`. It is a
proxy, not routed length or routability. Unnetted pads are listed and not
measured; no net is excluded by name. Without a declared maximum the outcome is
`info` and the check has no acceptance predicate. Declared maxima appear as
`details.margins` for the convergence reducer.

Optional advisory parameters never decide pass or defect. `policy` keeps the
decoupling score described above. `routing_feedback` names a captured
kicad-route `result.json` and the captured `reference_files` that kicad-route
fingerprinted (08179cd `input_fingerprint`). It is applicable only when those
files reproduce `input.fingerprint` and the entry for `input.board` has this
candidate's board hash; otherwise it is `stale` or `unavailable`.
Footprint blockers map to a board footprint UUID only through a unique
reference.

Each check retains `placement-probe.json`, `placement-judgment.json` (schema
`agent-ee.placement-predicate/1`, with the prerequisite result and raw report
hashes), and `ratsnest.json` for the MST proxy. Capability predicates are
`placement:<criterion>`, or one `placement:ratsnest-mst/1:<metric>:max` per
declared maximum. Envelope and routing paths are immutable inputs.

Native tests generate owned boards, save/reload them, exercise the real probe
inside Bubblewrap, and compare native DRC courtyard overlap with the judge.
These are software geometry qualifications, not fabrication approval. Copper,
clearance, thermal, EMC and decoupling-loop physics remain separate checks.

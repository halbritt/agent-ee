# Placement modules (AGENTEE-53)

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
name references, side and intersection/outside area in square millimetres.

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
filter waiver with its nearest connector distance. Distances are Euclidean
footprint-centre distances, not routed loop lengths. Optional `connector_edge`
contains `max_distance_mm` and `deduction` for an advisory nearest-outline-edge
centre distance. Advisories reduce the initial score of 100 down to a minimum
of zero; they never produce a defect or turn unknown geometry into a pass.

The independent owned two-cap example declares 100 nF at 2.5 mm and 1 µF at
5 mm, with a deduction of 15 per distant cap, giving score 70. This is **not**
the upstream Konnect fixture or evidence of its exact numerical acceptance.
No upstream code or design files were copied. That ticket criterion remains
unmet pending qualified fixture provenance and publication authorization.

`placement_check.check_placement(ctx)` uses the existing networkless executor,
compares the probe hash to the candidate manifest, and retains
`placement-probe.json` and `placement-judgment.json`. The integrator owns
registration, scope/3 capability qualification and immutable policy dependency
capture. The adapter alone grants no acceptance authority: existing coordinator
sealing, requirement mapping and adjudication remain necessary. Standalone
judgments explicitly identify themselves as unsealed.

Native tests generate owned boards, save/reload them, exercise the real probe
inside Bubblewrap, and compare native DRC courtyard overlap with the judge.
These are software geometry qualifications, not fabrication approval. Copper,
clearance, thermal, EMC and decoupling-loop physics remain separate checks.

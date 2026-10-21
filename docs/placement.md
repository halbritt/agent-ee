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
closed native polygons qualify. Curves (except courtyard circles and arcs, below),
open/branching/duplicate edges, holes, multiple board contours, nested or
touching courtyard contours are unsupported.
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

## Circular and arc courtyards (AGENTEE-212)

A courtyard circle (`fp_circle`, saved as centre and one point on it) or arc
(start, mid, end) is never asserted to be a polygon. `courtyard_curves` replaces
it by two exact straight bounds, and the probe records them on a `proven`
courtyard:

- `polygons` is the outer bound. Every segment stays at or beyond the radius,
  checked with exact rationals, so it contains the true courtyard.
- `inner_polygons` is the inner bound. Every vertex lies on or inside the
  circle, so it lies within the true courtyard.
- `approximation.tolerance_nm` is the declared maximum distance between either
  bound and the curve: `courtyard_curves.ARC_TOLERANCE_NM` = 5000 nm, the
  0.005 mm arc error pcbnew itself allows (its `ARC_HIGH_DEF`). Each vertex is
  checked against it. A record that declares more than 50000 nm is refused.

Floating point only proposes vertices; the exact checks decide. A full circle
uses a ring of at least 8 vertices, rounded up to a multiple of four; the 1 mm
radius probe pads of the pod seed use 48. In a contour that mixes segments and
arcs, the arc's midpoint shows which side is outside. For a convex arc the outer
bound takes the outside chain; for a concave arc (a notch) it takes the inside
chain. The inner bound takes the other chain, and it is checked to lie inside
the outer bound. Collinear arcs, an arc whose start equals its end, a radius
not larger than twice the tolerance (checked exactly before any step count, so
a 1 nm circle is refused rather than raising), and bounds that are not simple
polygons stay `courtyard_unproven`, with a `reason`. The packer and the judge
then refuse that footprint by name. The same applies to unsupported primitives, which are named by native
shape. Rounded rectangles (`corner_radius`), and curves in Edge.Cuts or in rule
areas, remain unsupported.

Every consumer decides a pass on the outer bound and proves a defect only on
the inner bound. The judge's overlap and containment, the predicates
`edge-clearance/1` and `placement-groups/1`, and the packer's infeasibility
proofs all follow this rule. A result that lies only between the two bounds
(within 5 um) is `unknown` with kind `courtyard_approximation`; it is never a
defect. `courtyard-overlap/1` and `footprint-keepout/1` keep native DRC as the
defect authority. They cross-check the inner bound: an inner-bound intersection
that native DRC lacks is a `helper_disagreement`. A pair or keepout that meets
only the outer bound is `courtyard_approximation`, also `unknown`. Native DRC
silence never turns that band into a pass: two 1 mm circles 1.998 mm apart
overlap by 2 um, and native DRC reports nothing for them. Outer bounds that do
not meet prove the pair clear. Straight courtyards are their own bounds,
and their records and results are unchanged. Two convex polygons are clipped
directly, and a convex polygon is fanned rather than ear-clipped, so the 48-gon
bounds stay fast; the arithmetic is exact either way.

## Declared fixed-courtyard overhang (AGENTEE-212)

`placement.judge(facts, policy=None, overhangs=None)` takes envelope-bound
declarations. Each names a `reference` and the `source` envelope id, hash and
pointer. It also lists `edges`: each is a proven outline edge (`start_nm`,
`end_nm`), its `outward` axis and `max_nm`. `placement.overhang_bands` sweeps
each declared edge outward by its distance. Where two declared edges meet, it
also admits the corner rectangle spanned by both distances. The real seed's U1
needs that: it leaves the board across the top and the right edge.
These regions must share no area with the board or with each other. Courtyard
area inside them is listed under `declared_overhangs` and is not a failure. Area
beyond them, or outside the board for an undeclared reference, is still
`outside_outline`. An edge that is not on the proven outline, or an axis that
points into the board, raises `ValueError`. Without `overhangs` the judgment is
unchanged and has no `declared_overhangs` key.

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
| `courtyard-overlap/1` | none | a native `courtyards_overlap` item of any severity, cited with its footprint UUIDs and positions | footprint without a courtyard on its mounting side; native `malformed_courtyard`; the native check ignored by project settings; helper geometry finds an overlap the native report lacks; a pair the native report lacks overlaps only between curve bounds (`courtyard_approximation`) |
| `outline-containment/1` | optional `envelope` | positive courtyard area outside the single proven outline (the judge above); with `envelope`, only area beyond that envelope's declared `courtyard_overhangs` | missing, open, curved or multiple outlines; missing or unproved courtyard; outside area only between curve bounds |
| `edge-clearance/1` | `min_courtyard_edge_mm` | exact courtyard-to-Edge.Cuts distance below the minimum, or courtyard crossing/outside the outline | unproved outline or courtyard |
| `footprint-keepout/1` | none | a native `items_not_allowed` item citing a footprint UUID | footprint-forbidding rule area with holes, arcs or several contours, or owned by a footprint; footprint without a qualified courtyard where such an area applies to its side; helper geometry intersects but native DRC is silent; intersection only between curve bounds (`courtyard_approximation`); native check ignored |
| `connector-edge/1` | `envelope`, `reference` (required for several fixed footprints) | wrong side or rotation; origin farther than `tolerance_mm` from the declared anchor (names edge, measured inset and distance); facing contradicts a declared local mating axis | reference missing or duplicated; outline unproved or declared edge absent from it; facing undeterminable |
| `imu-axes/1` | `envelope`, `imu_ref`, `graphic_id` or `marker_ref`, `forward_axis`, `side`, `max_distance_mm`, `marker_rotation_deg`, `facts_check`, `axis_fact` | IMU side/rotation or mapped axes disagree; marker missing, wrong layer, rotation or distance | missing/ambiguous IMU, unverified/inapplicable axis fact, incomplete marker inventory, ambiguous or unreadable marking |
| `keep-apart/1` | `policy` with nonempty declared `keep_apart` rules | projected courtyard separation proved below `min_distance_mm` on the inner bounds | missing/ambiguous reference, unqualified courtyard or minimum between curve bounds |
| `ratsnest-mst/1` | optional `max_manhattan_mm`, `max_euclidean_mm` | proved total above a declared maximum | Euclidean interval straddles a maximum; no netted pads |
| `placement-groups/1` | `groups` (captured `agent-ee.placement-groups/1`, or a packing policy with `groups`) | a member courtyard (inner bound) outside its group region (`outside_group_region`); a member origin farther than `max_distance_nm` from its anchor (`group_anchor_distance`) | member or anchor reference missing or duplicated; member without a qualified courtyard where a region applies; region boundary only between curve bounds |

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
the check criteria. Under AGENTEE-361 it may declare
`constraint_provenance.min_courtyard_edge_mm` with `class` (`fixture_author`
or `manufacturer`), `enforcement: hard`, nonempty `citation` and integer
`value_nm` exactly equal to the minimum in nanometres. Without this source,
each measured courtyard is an advisory: an `advisories` entry with
`kind: unsourced_limit`, the source class, the measured distance and
`exceeded`. Advisories never create gate findings. When an exceeded advisory is
all that remains, the status is `info` (check `execution: ok`, `outcome: info`).
Satisfied advisories (`exceeded: false`) are reported and keep `pass`, so that
existing pass verdicts are preserved. A courtyard proved outside the
board still establishes a structural defect. Missing or uncertain geometry
retains its unknown verdict. The AGENTEE-75 profile has only a copper-to-edge key
(`min_edge_clearance_mm`); it is not reinterpreted as a courtyard limit.

### Keep tall and heavy parts apart

`placement-policy/1` accepts `keep_apart: [{ref, others, min_distance_mm}]`.
The registered `keep-apart/1` criterion requires a nonempty declaration and an
earlier native DRC check. For example:

```json
{"id": "imu-separation", "kind": "placement", "params": {
  "criterion": "keep-apart/1", "drc_check": "drc",
  "policy": {"schema": "agent-ee.placement-policy/1", "keep_apart": [
    {"ref": "IMU", "others": ["SMA", "J1", "C8"], "min_distance_mm": 15}
  ]}
}}
```

The 15 mm minimum above is an example input. The telemetry pod spec requires
tall/heavy components away from the IMU but gives no distance; the pod minimum
must be declared, never invented by the check. References and distances are
captured criteria. Empty rules, repeated pairs, self-pairs and nonpositive or
sub-nanometre distances are refused. This criterion can use a policy containing
only `schema` and `keep_apart`; advisory scoring still needs its full declaration.

Separation is the minimum Euclidean distance between all qualified projected
courtyard polygons, including opposite board sides. Overlap or containment has
zero separation; exact straight-polygon equality with the minimum passes. Squared distances are
compared as exact rationals; rounded display distances never decide a verdict.
For curved courtyards, outer-bound separation at least the minimum proves pass;
inner-bound separation below the minimum proves defect. Otherwise the pair is
unknown with a `courtyard_approximation` entry naming both references. The
existing `distance_mm` and `distance_squared_nm` fields measure the outer bounds
(a lower bound on true separation); `inner_distance_mm` and
`inner_distance_squared_nm` measure the inner bounds (an upper bound).
Missing/ambiguous footprints and missing or unsupported courtyards are unknown.
Each measured pair retains references, the declared minimum, distance and
verdict in `placement-judgment.json`; proved violations also carry gate findings.
`KeepApartTests.test_keep_apart` runs native DRC and the sandboxed probe on an
original SMA/IMU fixture with a 5 mm gap and proves failure against 15 mm.
`KeepApartTests.test_keep_apart_native_circle_bounds` exercises saved `fp_circle`
courtyards of radius 1 mm against a 10 mm minimum: 12 mm center spacing is
unknown, 12.02 mm proves pass, and 11.99 mm proves defect.

The standalone advisory score does not grade `keep_apart`; declare the registered
criterion to obtain acceptance evidence. Component height is a separate
[envelope criterion](board-envelope.md#component-height).

`connector-edge/1` reads a captured AGENTEE-85 envelope through
`board_envelope.validate`. Capture requires that envelope to be declared in the
scope with every derived obligation covered. Each check selects one fixed
footprint using `reference` (for example `J1`) and witnesses only its obligation,
with predicate `placement:connector-edge/1:J1`. A single fixed footprint may omit
the selector and retain `placement:connector-edge/1`. Missing or duplicated saved
references are unknown only for the selected check; an undeclared selector or
several declarations without a selector is refused
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

`outline-containment/1` with `envelope` reads the same captured envelope bytes
and grades each fixed footprint's declared `courtyard_overhangs`
([declared overhang](#declared-fixed-courtyard-overhang-agentee-212)). The result
lists `declared_overhangs` and binds the envelope id and hash. Like any consumed
envelope, it must be the scope's declared envelope. Without `envelope` the
criterion is unchanged: no overhang is allowed.

`placement-groups/1` grades declared placement groups on the saved board
([placer groups](placer.md#group-and-region-input-agentee-212)). `groups` names a
captured `agent-ee.placement-groups/1` document (`schema` and `groups` only), or
the captured packing policy whose `groups` the packer enforced. For each group
member, the predicate checks every courtyard side against the group `region`;
membership alone grades nothing. It also checks the origin distance from the
`anchor` (exact squared integers; `measured` reports millimetres). A malformed
or unconstrained declaration is `malformed`. Groups may carry the
[constraint source records](placer.md#obstacle-and-group-sources-agentee-361).
Unsourced region and origin-distance limits are advisory: every member
measured against one is an `advisories` entry (`kind: unsourced_limit`, the
original `advisory_kind`, the source class, the measurement and `exceeded`).
When only exceeded advisories remain the status is `info`, never blocking at
the stage gate; satisfied advisories keep `pass`, and unknown remains for real
coverage gaps. Sourced violations remain defects
beside unrelated unknowns. The packer, the saved-board placer runner and block
arrangement still enforce the group, so they accept `pass` or an `info` whose
advisories are all `exceeded: false`. Proved defects become gate
findings with the member as entity.

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

The advisory [`placement_congestion`](placement-congestion.md) check (AGENTEE-57)
reads the same probe without a DRC prerequisite. It reports pad overlaps,
courtyard issues, congested regions and suggested moves for the repair writer.
It never passes a board and does not change these predicates.

Native tests generate owned boards, save/reload them, exercise the real probe
inside Bubblewrap, and compare native DRC courtyard overlap with the judge.
These are software geometry qualifications, not fabrication approval. Copper,
clearance, thermal, EMC and decoupling-loop physics remain separate checks.

### IMU axes

`imu-axes/1` selects one fixed footprint in the declared envelope using `imu_ref`.
`side` must match that declaration (`front` or `back`); `forward_axis` is `x+`.
The native side and rotation must equal the envelope values. A prior
`datasheet_facts` check, selected by `facts_check`, must verify `axis_fact`.
That fact uses the existing facts/1 or facts/2 record with `kind: axis_orientation`
and these additional fields:

```json
{
  "footprint_id": "Owned:R2",
  "axes": {"X": "x-", "Y": "y+", "Z": "z+"}
}
```

This example describes the original test stand-in at 180 degrees. It is not an
LSM6DSO32 mapping. The ordinary fact fields (`id`, `part_number`, `value`, `unit`,
`conditions`, `source`) remain required. `part_number` must equal the native IMU
value and `footprint_id` its library ID. `axes` declares signed sensor directions
in that footprint's front, zero-rotation frame. X and Y are orthogonal planar
axes (`x+`, `x-`, `y+`, `y-`), Z is `z+` or `z-`, and the frame must be right-handed.
KiCad board y increases downward. The existing qualified front/back quarter-turn
transform must produce X=`x+`, Y=`y-`, Z=`z+` on the board. Unsupported transforms
remain unknown. Fact interpretation and footprint applicability are engineering
declarations: PDF text occurrence does not prove the interpretation of a drawing.

`graphic_id` is the exact native UUID of a board or footprint graphic.
Alternatively, `marker_ref` names a dedicated marker footprint containing one
non-field graphic. The qualified marking is the scaffold's stroke text
`IMU_AXES X forward Y left Z up` (whitespace is collapsed), on F.SilkS.
It must be visible, unmirrored, without knockout or a custom font, at least
0.8 mm in both text dimensions, with stroke at least 0.12 mm and at most one
quarter of the smaller text dimension. Unsupported graphic/content/rendering
is unknown. This check does not establish visual clearance from pads, masks or
other graphics; use native DRC for silkscreen clearances.

`marker_rotation_deg` is an explicit board-frame quarter turn, compared modulo
360. `max_distance_mm` is a positive distance from IMU origin to marker origin;
the exact squared-nanometre comparison includes the boundary. No inferred marker
name, nearest text or rounded distance can stand in for the declared selection.
The capability predicate is `placement:imu-axes/1:<imu_ref>`. The retained judgment
names the IMU and marker and includes the measured transform and verified fact.
The existing envelope fixed-footprint obligation still needs its separate
`connector-edge/1` witness, which also checks position.

`tests.test_placement_check` runs the registered scope/3 check over the scaffold
and original PDF/footprint stand-ins. It proves rotated-IMU and missing-marker
defects, unreadable-marker unknown coverage, layer/rotation/distance boundaries,
part applicability and sealed witness binding. Production LSM6DSO32 axis capture
and its exact footprint datum remain inputs to supply after AGENTEE-209 settles
the IMU choice; these software tests do not qualify a production sensor mapping.

## Native qualification

The following tests qualify the AGENTEE-92 predicates. The connector criterion
has standalone native coverage; its normal coordinator path remains blocked by
the envelope capture gate described below.

| Criterion | Test and observation |
| --- | --- |
| Overlapping R/C courtyards cite native DRC | `test_placement_gates_native.NativePlacementGates.test_overlapping_rc_courtyards_defect_cites_the_native_item` checks a defect with C1/R1 UUIDs and the retained native report hash, then verifies the sealed witness |
| Missing courtyard or unproved outline stays unknown | `test_placement_gates_native.NativePlacementGates.test_missing_courtyard_is_unknown_for_geometric_predicates` rejects a missing C1 courtyard as unknown; `test_placement_native.NativePlacementTest.test_saved_native_control_mutations_and_hash_binding` saves open, missing and curved outline variants and checks unknown results |
| Moving the header names edge and distance | `test_placement_gates_native.NativePlacementGates.test_connector_edge_on_rc_header` saves a moved J1 and checks the declared `left` edge and 3 mm displacement |
| Circular courtyards: overlap defect, clear pass | `test_placer_residuals.NativeResidualsTest.test_circular_courtyard_overlap_native_defect_and_clear_pass` saves two 1 mm `fp_circle` probe courtyards 1.5 mm apart (native `courtyards_overlap`, judge overlap, `courtyard-overlap/1` defect) and 2.05 mm apart (none of them) |
| Circular courtyards: native silence in the tolerance band | `test_placer_residuals.NativeResidualsTest.test_circle_tolerance_band_stays_unknown_under_native_drc_silence` saves 1 mm circles 1.998 mm apart along the axis (native silent, judge overlap, predicate `helper_disagreement`) and at the inner bound's midchord (native silent, judge and predicate `courtyard_approximation`), 2.002 mm apart (`courtyard_approximation`) and 2.01 mm apart (pass) |
| Declared overhang containment | `test_pod_placement.OverhangPodPlacementTests.test_declared_overhang_native` runs `outline-containment/1` with the scope envelope on a scaffolded board whose SMA stand-in overhangs the right edge as declared, and passes |
| Group regions and anchors | `test_placer_residuals.NativeGroupCheckTest.test_registered_group_check_passes_and_names_defects` runs the registered `placement-groups/1` check on owned RC variants: pass, then a moved C1 gives `outside_group_region` and `group_anchor_distance` |
| Same saved board gives identical ratsnest totals | `test_placement_gates_native.NativePlacementGates.test_ratsnest_byte_identical_oracle_and_declared_maxima` compares complete `ratsnest.json` bytes across captures and checks an independent Manhattan MST oracle and exact maximum boundaries |

Run these modules on the native host, with the test helper directory on the
import path:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest \
  tests.test_placement_predicates tests.test_placement_gates_native \
  tests.test_placement_native -v
```

The connector predicate qualification does not establish a complete envelope
campaign. `test_board_envelope.EnvelopeNativeBaselineTests` exercises native
connector checks and witness binding with test stand-ins for unrelated
envelope obligations. Production envelope capture still refuses unresolved
stackup manufacturing authority; that gate must remain closed. Likewise,
`min_courtyard_edge_mm` is a captured scope declaration, not a fabricator-sourced
courtyard limit. The optional routing test uses an owned result record to
qualify parsing and advisory behavior, not to claim a routing attempt.

The RC fixture's scope/3 declaration mapping outline, layer count and stackup to
their `board_envelope` criteria, the fixed header to `connector-edge/1`, and the
profile to `fab_profile` is refused before candidate creation:
`R-envelope-rc-filter-stackup` lacks executable coverage because
`copper_weight_oz` needs qualified captured manufacturing authority. A profile
with source-verified stackup metadata can now supply that authority
(AGENTEE-161); a passing standalone connector judgment cannot supply it.

# Disposable deterministic placer (AGENTEE-54)

For caller-named capacitor rows with strict before/after score gating, see
[targeted placement plans](placement-plans.md) (AGENTEE-55). That opt-in writer
request also exposes net-cluster seeds and BGA escape plans for inspection.

`placer.pack(facts, policy)` proposes a complete placement from the saved-board
[placement probe](placement.md). The policy is captured input, using schema
`agent-ee.packing-policy/1` and explicit `step_budget`, `gap_nm`, `fixed_refs`,
`priority_refs`, `ground_nets`, and `obstacles`, plus optional
`min_courtyard_edge_mm` (default `0.0`), envelope-bound `fixed_overhangs`,
declared `groups` ([AGENTEE-212](#real-pod-residuals-agentee-212)) and the boolean
`capacity_proof` (default `false`, [AGENTEE-249](#board-capacity-agentee-249)). Obstacles declare `side` and
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

The initial per-part search sorts explicit priorities first, then descending pad count, then
reference (parts bound to a declared anchor point, then declared group anchors
and their members, are ordered first; see [search](#bounded-search-agentee-212)
and [crowded clusters](#crowded-clusters-agentee-212)). It tries four quarter-turn rotations at deterministic positions
aligned to board vertices (shifted inward by `min_courtyard_edge_mm`),
inflated reserved courtyard boxes and the slabs of fixed courtyards. It chooses the
smallest integer Manhattan same-net pad distance to already placed parts,
excluding explicitly declared ground nets, with position and rotation tie
breaks. Boxes conservatively reserve a minimum gap around parts the search
places; fixed courtyards and declared obstacles are exact polygons (below). The
full polygon judge checks the complete proposal. No random numbers, new runtime dependency or third-party implementation is used.
An optional [search wall-time budget](#incremental-locality-search-and-diagnostics-agentee-332)
retains a typed unknown partial diagnostic on expiry.

The plan records budget and actual steps. Only a complete plan contains
placements. A board too small for its parts yields `packing_infeasible`,
`outcome=defect`, `steps_used=0` and the policy's `step_budget`, with an exact
`proof` of one kind:

| `proof.kind` | Proved | Recorded values |
| --- | --- | --- |
| `courtyard_area` | total same-side courtyard area exceeds board area | `side`, `board_area_nm2`, `required_courtyard_area_nm2` |
| `min_width` | a part's courtyard convex hull is wider, in its narrowest direction, than the outline's hull | `ref`, `board_min_width_squared_nm2`, `footprint_min_width_squared_nm2` |
| `diameter` | a part's courtyard is longer than the outline's longest vertex-to-vertex span | `ref`, `board_diameter_squared_nm2`, `footprint_diameter_squared_nm2` |
| `region_capacity` | a declared group region cannot hold the courtyards that must lie in it ([below](#region-capacity-proofs-agentee-212)) | `group`, `side`, `groups`, `references`, `region_board_area_nm2`, `blocked_area_nm2`, `blocked_by`, `available_area_nm2`, `required_courtyard_area_nm2` |
| `region_capacity_gap` | the same, with every courtyard and the region grown by half the packing gap | `group`, `side`, `groups`, `references`, `gap_nm`, `grown_region_area_upper_nm2`, `blocked_area_nm2`, `blocked_by`, `available_area_nm2`, `required_grown_courtyard_area_nm2` |
| `board_capacity_gap` | with `capacity_proof`: the movable courtyards on one side, grown by half the gap, exceed the board's free area ([below](#board-capacity-agentee-249)) | `side`, `gap_nm`, `blocked_by`, `square_nm` (0), `references`, `required_grown_courtyard_area_nm2`, `available_area_upper_nm2`, `ratio` |
| `board_capacity_fragmented` | with `capacity_proof`: the parts at least `square_nm` thick exceed the free area such parts can reach | the same, `square_nm` > 0 |

Values are exact `[numerator, denominator]` rationals in square nanometres.
The width and diameter proofs hold for every rotation, not only quarter turns,
and cover all courtyards of a part on both sides. A part that could fit only
at an angle the search does not try is not proved infeasible.
Budget exhaustion, finite search
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
no destination. Native DRC retains full baseline and candidate reports;
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

### Declared obstacle unions and reserved-gap provenance (AGENTEE-338)

Earlier policy validation refused touching or overlapping polygons within one
obstacle (`nested or touching polygon contours unsupported`); splitting them
into separate obstacle entries avoided that refusal. Declared obstacle polygons
now reserve their exact union on each declared side, whether grouped in one
entry or split across entries. Each polygon must still be simple, nondegenerate,
straight and expressed in integer nanometres; each entry permits 1–64 polygons.
Self-intersections, repeated vertices and zero-area members remain invalid policy
and raise `ValueError`. Courtyard and outline contour qualification is unchanged.

The union is represented by its member polygons, with no rounding, hull or
bounding-box replacement. Shared edges and overlaps open no gap; concave notches
and uncovered holes remain available. Nested members and duplicates add no
reserved space. Board-capacity calculations union their blocker boxes rather
than adding overlapping areas. Region-capacity proofs retain only disjoint
blockers as a conservative lower bound, so overlap may weaken a proof but never
inflate blocked area.

`gap_nm` applies between fixed courtyards and declared obstacles too. In
particular, `gap_nm: 250000` requires a 0.25 mm Euclidean edge distance; exactly
0.25 mm is admitted, while one nanometre less returns `fixed_conflict`,
`outcome=unknown`, with no placements. Positive-area structural overlap remains
blocked even when `gap_nm` is zero.

The pod's 0.25 mm value came from agent-491's packing policy (`gap_nm: 250000`),
as recorded by agent-499 in the AGENTEE-338 comment and AGENTEE-331/337 provenance
follow-up. It is an **agent-chosen advisory default**, with no captured JLCPCB SMT
assembly figure establishing it as a manufacturing requirement. The generic
packing API requires an explicit gap and supplies no 0.25 mm default. This
value remains a search reservation and a fixed-placement admission guard. A
capacity proof that depends on it reports `packing_infeasible`, `outcome=unknown`,
empty placements and `owner_confirmation: ["gap_nm"]`, naming the unsourced
constraint in `reason` and `binding_constraints`. The diagnostic keeps the
existing `unknown`/`hard` gap entry: `hard` describes the enforced search
reservation, not authority to establish a design defect. If the same capacity
bound proves infeasibility at zero gap, that structural proof retains its defect
verdict and does not name the gap as binding.

The low-level packing API accepts a qualified gap source in the existing
`constraint_provenance.gap_nm` entry: `class` (`fixture_author` or
`manufacturer`), `enforcement: "hard"`, `value_nm` (equal to `gap_nm`) and a
nonempty `citation`. The source entry is retained in binding constraints.
Original numeric test fixtures cite their authored gap requirement and retain
all previous verdict expectations. Manufacturer class is for a gap validated
against a captured manufacturer requirement, including a captured JLCPCB SMT
assembly record; those requirements remain hard. A caller must qualify that
source before calling this geometry API: a citation string is not independently
authenticated by the placer. The coordinator placement adapter continues to
refuse policy-supplied `constraint_provenance`, per-obstacle `provenance` and
per-group `constraint_provenance`; envelope provenance still comes only from
captured envelopes. This change adds no manufacturer capture pipeline
or automatic promotion of an agent-chosen number.
Neither a successful packing plan nor this spacing default establishes native
DRC or assembly clearance acceptance.

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

### Real-pod residuals (AGENTEE-212)

The released native-v2 pod seed exposed five gaps. Each is closed without a
global exemption, a clipped zone or an invented courtyard. The seed has 112
footprints, a 108-component XML export, six circular probe courtyards, outboard
U1/J2 courtyards, and J1 and H1..H4 without courtyards.

**Missing courtyards are refused by name.** A fixed or movable part without a
mounting-side courtyard gives `unsupported_geometry`, `outcome=unknown`, with
every such reference listed. On the seed the reason is
`mounting-side courtyard missing: H1, H2, H3, H4, J1`. An unqualified
courtyard is named with its side, status and native reason, for example
`TP1 F courtyard not qualified: courtyard_unproven (...)`. Nothing is derived
from pads, silkscreen or a bounding box. J1's nominal flange
projection (60.8 x 32.1 mm, parenthesized reference dimensions) is not a
qualified courtyard. It must arrive as reviewed CAD, never from the placer.

**Circular courtyards.** The probe bounds circles and arcs exactly within a
declared 5 um tolerance ([placement](placement.md#circular-and-arc-courtyards-agentee-212)).
The packer reserves and checks the outer bound, which contains the circle. Its
area, width and diameter proofs use the inner bound, so an approximation never
proves infeasibility. The seed's six 1 mm-radius TP courtyards are now `proven`
48-gon bounds.

**Declared fixed-courtyard overhang.** `fixed_overhangs` lists envelope-bound
declarations. Each names a `reference` and its `source` (envelope id, sha256 and
pointer). It also lists `edges`, each with name, `start_nm`, `end_nm`,
`outward` and `max_nm`. The placement adapter derives them only from a captured
envelope's `fixed_footprints[].courtyard_overhangs`
([board envelope](board-envelope.md)) through
`placement_adapter.envelope_overhangs`. A captured packing policy that carries
its own `fixed_overhangs` is refused, and two envelopes declaring the same
reference are refused. A declared fixed courtyard may leave the outline only
inside the swept band of each declared edge, plus the corner between two
adjacent declared edges, and only up to each `max_nm`. Anything beyond is
still `fixed_conflict`, naming the reference. An undeclared reference keeps
full containment. A declaration for a movable part raises `ValueError`. An edge
that is not a proven outline edge is `unsupported_geometry`. A declared
reference counts only its on-board area in the area proof and takes no part in
the width or diameter proofs. Every undeclared courtyard, fixed or movable,
must lie wholly inside the outline. An undeclared outboard courtyard is
therefore refused in one of two ways. If the area proof holds, the plan is
`packing_infeasible` with a `courtyard_area` proof (a `defect`). Otherwise it
is a `fixed_conflict` that names the reference.
The plan judgment and the writer's saved-board judgment use the same
declarations and list `declared_overhangs`. On the seed, U1 needs top and right
allowances (and their corner), J2 the right edge and J1's nominal flange the
bottom edge. These are geometry facts; the allowance values are owner/engineering
inputs this item does not choose.

**Board-only exclusion at scale.** The writer leaves a footprint out of the
netlist inventory comparison only when native `IsBoardOnly` is set, none of its
pads has a net and the export does not name it (AGENTEE-179). On the seed this
admits 112 saved footprints against 108 XML components; without the rule the
same inputs are refused. A hole whose board-only attribute is cleared, that has
a net, or that the export names is refused.

### Group and region input (AGENTEE-212)

`groups` is a list of declared placement groups (`placement_groups`). Each has
an `id` and `references`, and a reference belongs to at most one group. A group
also has at least one constraint:

- `region: {"polygons": [...]}`: straight, proven polygons in nanometres that
  must contain every courtyard of each member;
- `anchor: {"reference": REF}` or `anchor: {"point_nm": [x, y]}`, with
  `max_distance_nm`: the largest Euclidean distance from the anchor to each
  member's footprint origin. A point anchor expresses a target coordinate.

Membership alone constrains nothing, so a group without a constraint is
refused. An anchor reference must be a known non-member, and it must not be
itself an anchored member, so there are no chains. The packer places movable
anchors first, each directly followed by its anchored members. It adds region
vertices and anchor points to its candidate alignments and admits only
candidates whose outer courtyard bound satisfies the group; the anchor distance
binds whichever of anchor and member is placed second, and a member placed
before its anchor must lie within the sum of both distances of each placed
member of the same anchor. A fixed member must
already satisfy its group, and its anchor must be fixed; otherwise the result
is `fixed_conflict`. A region whose capacity is provably too small for its
members is `packing_infeasible` with a `region_capacity` or
`region_capacity_gap` proof; a region the search cannot fill without such a
proof is a bounded `packing_search_exhausted`, never a defect. A complete
plan carries `group_grading`, from the same `placement_groups.grade` as the
registered [`placement-groups/1`](placement.md#placement-check-kind) check, and
must pass. The writer grades the saved board again and refuses to publish a
proposal that fails. The pod proposal's 15 groups list membership only, with no
coordinates or distances, so they do not validate as given. Regions and
distances are physical design choices that a captured declaration must supply.

### Bounded search (AGENTEE-212)

The real pod placement stopped at the first part that had no admissible
candidate: the greedy pass never revisited an earlier choice, and it submitted
every candidate of every part to the predicates. The search keeps the greedy
choice and adds deterministic backjumping.

- **Ranked first fit.** Each part's candidates are ranked by the same key as
  before, (wire cost, x, y, rotation), and submitted to the predicates in that
  order; the first admissible one is taken. That is the candidate the greedy
  pass chose (the minimum key among admissible candidates), so a layout the
  greedy pass completed is unchanged. One step is one candidate submitted to the
  exact predicates (outline, exact obstacles, keepouts, group);
  `step_budget` bounds them and exhaustion is `packing_budget_exhausted`.
  Before AGENTEE-212 every generated candidate was a step, including those the
  reservation-box test rejects; on the pod those were nearly all of them.
- **Window and reservations.** Candidate generation keeps only positions whose
  courtyard box lies inside the outline's box shrunk by `min_courtyard_edge_mm`,
  inside the group region's box, and whose origin lies inside the distance box
  of every placed anchor partner. It then applies the reservation-box test
  itself as a sweep over each x (a reserved box blocks an open interval of y),
  recording the owners of blocking boxes. A rectilinear courtyard or fixed
  obstacle is exactly the union of its vertical slabs (boxes), so a
  positive-area overlap between a slab of the candidate's courtyard and a slab
  of a rectilinear fixed courtyard or declared obstacle on the same side is a
  polygon overlap, which the exact obstacle test rejects; the sweep drops those
  too. A position whose slab pair is nearer than the gap still counts as a step
  but is rejected without the polygon test ([below](#crowded-clusters-agentee-212)). Each is a necessary condition of a predicate, so no
  admissible candidate is dropped, and the predicates still recheck every
  candidate the generation keeps.
- **Backjumping.** A rejected candidate records the placed movable parts that
  blocked it (reservation box or keepout owner, or the anchor partner). When a
  part has no admissible candidate, the search undoes placements back to the
  latest such part and places the failed part before it. A member blocked
  through its anchor instead leads the anchor's unit: the member is tried in
  rank order and the anchor (with the rest of its unit) placed first-fit beside
  it. The n-th failure of the same part starts at its 2^(n-1)-th latest blocker,
  so a part that keeps failing moves earlier quickly. An order already tried
  jumps further back; every part order is tried at most once and backjumps are
  bounded by `step_budget` too. A part with no
  movable blocker, or whose every backjump repeats an order, ends the search
  as `packing_search_exhausted` naming it.

No random numbers or wall-clock limits are used: identical inputs give
identical plans. Every accepted candidate passes the same exact predicates,
the complete proposal the same polygon judge and group grading, and fixed parts
never move. Every result the search returns records
`search: {strategy: "ranked-first-fit-backjump/1", backjumps, unplaced?}`; a
search failure that names a part also records `placed_peak`.

### Crowded clusters (AGENTEE-212)

The actual v2 pod input spent its 200 000-step budget after 160 backjumps. At
its deepest point it had placed 72 of 104 movable parts. The fixed connector and
RF module courtyards cover most of the board. At the 0.25 mm gap, the movable
courtyards grown by half the gap need about 1549 mm² of the at most 1620 mm²
left (about 96 %). That is not a proof, so the result stays unknown. Three
changes, tested on owned fixtures (`tests/test_placer_crowded.py`):

- **Slab alignment.** Candidate positions align to every slab of a rectilinear
  fixed courtyard or obstacle as well as to its box. A concave courtyard's box
  can lie far from its edges: the RF module's box spans 48 mm, while its board
  edges at x = 52.75 mm and 72.25 mm were never alignment targets. A part
  anchored within 5 mm of a point on one of those edges therefore had no
  candidate at all.
- **Anchor points first.** Parts bound to a declared anchor point are placed
  before every cluster, tightest `max_distance_nm` first. Each has only a small
  disc of positions, which a cluster placed earlier could take.
- **Exact gap band at generation.** For a rectilinear candidate, two closed
  boxes are nearer than the gap exactly when dx² + dy² < gap², with dx and dy
  their nonnegative separations, all integers. A position with such a pair
  against a fixed or obstacle slab still counts as one step and is rejected,
  but without running the polygon test. Step accounting, conflicts and plans
  are unchanged; only CPU time drops (about half on the pod).

Search failures also record `search.placed_peak`: the most movable parts placed
at once. It is a diagnostic only. A plan is complete or empty, and the outcome
stays unknown. The capacity figure above is now computed by the engine and
recorded in every result as [`capacity`](#board-capacity-agentee-249); the v2
input gives 1548.703575 / 1619.765150 mm² (0.956128), and no fragmentation row
exceeds 0.944, so it stays unknown.

The rank key is unchanged. A touching-perimeter (compaction) term raised the
pod's deepest partial placement further, but it moved parts against each other
and the board edge, which changed native silkscreen and copper-to-edge DRC on
existing boards. Courtyards do not model either, so that term was not adopted.

On the actual pod inputs (policy step budget 200 000) neither v1 nor v2
completes. An anchor-free rectangle packer that ignores nets reached at most 86
of 104 parts. The remaining lever is the input (board area, fixed courtyards,
the gap or mounting side), which is the pod lane's decision.

### Region capacity proofs (AGENTEE-212)

After the fixed reservations, each group with a `region` is checked against
the movable members of every group whose region equals it or (for a
one-polygon region) lies inside it. Their courtyards, by inner bound, must lie
in the region and the outline, clear of fixed courtyards, declared obstacles
and footprint keepouts. Blockers are subtracted by inner bound and only when
pairwise disjoint.

- `region_capacity` compares courtyard area alone with the region's on-board
  area less blockers wholly inside the outline.
- `region_capacity_gap` uses `gap_nm`: courtyards grown by `gap_nm/2` are
  disjoint and lie in the region grown by `gap_nm/2`. A convex courtyard's grown
  area is bounded below by Steiner's formula with integer-floor edge lengths
  and pi taken as 3; another polygon gains at least `gap_nm` times its larger
  extent; a part with several courtyards on one side counts them ungrown. The
  grown region is bounded above by an edge strip and a sector of angle below pi
  per vertex, with integer-ceiling lengths and pi taken as 22/7. Only fixed
  courtyards and declared obstacles, which keep the gap, are subtracted.

Both use exact rationals and return `packing_infeasible`, with `steps_used=0`.
A proof bound by a soft limit or an unsourced nonzero gap has `outcome=unknown`;
a hard sourced gap or a proof independent of the gap retains `outcome=defect`. Edge margin and the width of a region are not used, so a
region too narrow but large enough by area is still only a search failure.

### Unsourced policy limits (AGENTEE-358)

A nonzero `min_courtyard_edge_mm` without a qualified source is advisory.
Search still reserves that margin, as it reserves an unsourced packing gap;
search exhaustion remains unknown. A board-capacity proof depending on the
margin reports `packing_infeasible`, `outcome: unknown`, empty placements and
`owner_confirmation` naming `min_courtyard_edge_mm`. Its binding entry is
`class: unknown`, `enforcement: soft`, with an explicit advisory reason.
The diagnostic retains the requested margin and gap.

The low-level geometry API accepts `constraint_provenance.min_courtyard_edge_mm`
with the same source fields as `gap_nm`: `class` (`fixture_author` or
`manufacturer`), `enforcement: hard`, nonempty `citation` and integer `value_nm`
equal to the policy margin converted exactly to nanometres. Original authored
fixtures declare their margin requirements explicitly. As with gap sources,
the caller must qualify the source; citation authentication and owner-stated
gap admission belong to AGENTEE-348. Coordinator adapters still refuse
caller-supplied provenance.

Before reporting a capacity proof, the placer retries without unsourced limits:
first both margin and gap, then each individually if needed. A proof that
survives omits the removed limits from its binding constraints and retains its
existing verdict under the remaining constraints. Region proofs do not use the
edge margin. Their zero-gap retry can find a later structural region defect
after an earlier gap-dependent region proof. The regression test fails when
that retry is removed (AGENTEE-338 review mutant M3).

The policy/capacity audit found these numeric constraints:

| Constraint | Class and authority | Capacity/defect effect |
| --- | --- | --- |
| `min_courtyard_edge_mm` | Unknown/soft without source; fixture-author or manufacturer/hard with caller-qualified source | Advisory-dependent board proofs are unknown. |
| `gap_nm` (including pod 0.25 mm) | Unknown/advisory without source; fixture-author or manufacturer/hard with caller-qualified source | Advisory-dependent board and region proofs are unknown (AGENTEE-338). |
| Native rule areas and footprint keepouts | Captured board geometry, board/hard; no numeric padding is added | Structural polygon exclusion. |
| `groups[].max_distance_nm` | Unknown/soft unless its group declares a sourced hard value | Search predicate; saved-board violations without a source are advisory `info` (AGENTEE-361). Unused by capacity proofs. |
| Group region polygons and policy obstacles | Unknown/soft without a source; sourced geometry is hard | Advisory-dependent proofs are unknown and name the geometry in binding constraints (AGENTEE-361). |
| Outline and fixed geometry | Envelope classes retained, otherwise unknown legacy hard geometry | Existing structural capacity verdicts remain. |
| Step budget, search timeout and geometry work guards | Computational bounds, no engineering authority | Exhaustion/refusal is unknown, never a capacity defect. |

The placer has no other numeric edge-margin, reserved-gap, keepout-padding or
group-radius default that can establish a capacity defect. Electrical locality
uses its existing per-pair sourced/advisory contract, separately from capacity.

### Obstacle and group sources (AGENTEE-361)

Each policy obstacle may declare `provenance: {class, enforcement, citation}`.
The accepted geometry classes are `fixture_author`, `board`, `datasheet`,
`manufacturer` and `arrangement_rule`; enforcement must be `hard` and the
citation nonempty. Without that record an obstacle is `unknown`/`soft`. Native
captured rule areas and footprint keepouts remain `board`/`hard` exclusions.

Only a direct caller of this geometry API supplies these records (AGENTEE-365).
The coordinator placement adapter and the pod driver refuse a packing policy
whose obstacles carry `provenance` or whose groups carry `constraint_provenance`,
exactly as they refuse a policy-level `constraint_provenance`: the packing
policy is caller-authored, so it may state geometry but not qualify it. A
sourced keep-out reaches the board from a captured envelope as a native rule
area, graded `board`/`hard`; caller-declared obstacles, regions and distances
stay advisory. Block arrangement passes the cited source of each hard keep-out
and `no_stacking` shadow to the residual pack as an `arrangement_rule`/`hard`
record, so a residual capacity proof resting on one is a defect that names it
(see [blocks](blocks.md#residual-packing-and-the-complete-plan)).

Each group may declare `constraint_provenance` keyed by `region` and/or
`max_distance_nm`, only for constraints that group actually has. A region uses
the geometry source record above; a distance uses the existing numeric record
(`fixture_author` or `manufacturer`, `hard`, nonempty `citation`, integer
`value_nm` equal to `max_distance_nm`). For example:

```json
{"id": "filter", "references": ["C1"],
 "anchor": {"reference": "U1"}, "max_distance_nm": 1000000,
 "constraint_provenance": {"max_distance_nm": {
   "class": "fixture_author", "enforcement": "hard", "value_nm": 1000000,
   "citation": "Original fixture: C1 origin within 1 mm of U1"}}}
```

A region capacity proof records every participating region, including nested
regions, as `group <id> region` in `binding_constraints`. Policy obstacles keep
their original `obstacle <index>` names. A proof that needs an unsourced region
or obstacle reports `packing_infeasible`/`unknown`, empty placements and
`owner_confirmation` naming it. Proofs retry without advisory geometry, then
without unsourced numeric limits; an independent sourced region or board
capacity defect takes precedence over an advisory proof. The capacity diagnostic
retains the original requested geometry and limits.

Search still reserves the declared geometry and group radii; search failure
remains unknown. Saved-board grading reports unsourced region and radius
limits as `unsourced_limit` advisories with the original `advisory_kind`,
measured values, source class and `exceeded`. The status is `info` when only
exceeded advisories remain; satisfied ones keep `pass`. Sourced defects survive alongside them. Because search enforces the
groups, a plan or saved board is accepted only when no advisory is exceeded
(`placement_groups.satisfied`). These source records follow the existing caller qualification contract;
authenticating declared citations remains AGENTEE-348.

### Board capacity (AGENTEE-249)

After the fixed reservations and region proofs, the engine bounds board-wide
capacity at the packing gap for each side that has movable courtyards. With
r = `gap_nm`/2:

- **Container.** A movable courtyard lies in the outline at least
  `min_courtyard_edge_mm` (m) from its edges, so grown by r it lies in the
  outline's box offset inward by m − r.
- **Blocked.** Fixed courtyards (inner bounds) and declared obstacles keep the
  gap, so grown by r they share no area with grown movable courtyards. Only
  boxes inside those grown sets are counted: each slab of a rectilinear polygon
  grown by r along x and, separately, along y (the corners stay free). A
  non-rectilinear blocker counts nothing. When m ≥ r and the outline is
  rectilinear, the outline's box less the outline is blocked too, grown by
  m − r. Footprint keepouts are not counted. So `available_area_upper_nm2` is an
  upper bound.
- **Required.** Each movable part's courtyards on that side, by inner bound,
  grown by r, bounded below as in the region proofs (Steiner with pi taken as 3
  for one convex polygon, `gap_nm` times the larger extent for another, ungrown
  area for several).

`capacity.sides.<side>.free_area` compares the two. It is the scratch
calculation of the [crowded-cluster](#crowded-clusters-agentee-212) analysis,
now in the engine.

**Fragmentation.** A part has square size a when every point of its
courtyards lies in an a × a square inside them: the smaller side of a
rectangle, the thinnest arm of a rectilinear polygon (checked exactly against
its own square cover), and 0 for any other shape. A rigid motion carries the
squares along, so at any angle each point of the grown courtyard lies in a
square grown by r that fits in the free space. Such a set holds a disc of
radius a/2 + r and lies within a/√2 + r of its centre. Its centre therefore
lies where the axis cross of half-length a/2 + r is free, and it reaches only
free points within an axis box of half-side ⌈a/√2⌉ + r (rounded up to the half
nanometre) of such a centre. For each distinct square size, the parts at least
that thick must fit in that reachable free area. These are the
`capacity.sides.<side>.fragmentation` rows. Narrow strips, slots and margins
then count only for parts that can reach them. The bound holds for every
rotation, not only quarter turns.

Every row is a necessary condition for every placement, so exceeding one is a
proof. The `capacity` record (`basis: diagnostic-only`, method
`gapped-free-area/1`, exact `[numerator, denominator]` areas and ratios, and a
six-decimal `ratio_display` rounded down) is attached to the plan, to search and
budget failures and to judge rejections. It never passes or fails a plan. Results
that stop before the reservations (unsupported geometry, fixed conflicts, the
earlier proofs) do not carry it; the earlier proofs still name their
[binding constraints](#constraint-provenance-agentee-266).

A row that is exceeded becomes a `packing_infeasible` result
(`board_capacity_gap` for the free-area row, `board_capacity_fragmented` for a
square size) only when the policy sets `capacity_proof: true`. By default the
result stays the search's unknown. With it enabled, a proof establishes a defect
only when its binding limits permit that verdict: an unsourced gap remains unknown.
Examples retained as search failures without the opt-in include the 4 mm board
with a 1.5 mm margin (`test_placer.PlacerTest.test_edge_margin`), the 3 mm
notch at a 1.5 mm gap
(`test_placer.EnvelopeFixedTest.test_fixed_courtyard_is_a_polygon_not_its_bounding_box`)
and two 1 mm circles in a 2.506 mm square at the 0.5 mm gap
(`test_placer_residuals.CurvedCourtyardTest.test_circle_infeasibility_is_proved_only_on_the_inner_bound`).
Making it the default is an owner decision. No placement constraint is relaxed
either way. The bound is not tight: a feasible input never exceeds it, but an
infeasible one may not either. Owned fixtures are in
`tests/test_placer_capacity.py`. On a 20 × 12 mm board with a 12 × 9 mm fixed
block, six 3.2 mm squares are placed. Seven are proved infeasible against the
fixture's authored 0.25 mm requirement by the fragmentation row, although they
need only about 81 % of the free area. Removing that gap's provenance gives the
same geometric bound with an unknown outcome.

### Constraint provenance (AGENTEE-266)

`placement_adapter.binding` reads the outline and fixed-reference
[envelope provenance](board-envelope.md#constraint-provenance) from the captured
placement envelopes, validated against the capture inventory: an owner quote
whose source is not captured, has changed, does not contain the quote, or is a
mutable input refuses the envelope (AGENTEE-271). It writes them into the policy as `constraint_provenance:
{outline, fixed: {reference: entry}}`. A packing policy file that declares it
is refused by the adapter and the pod driver. If any consumed envelope lacks
provenance, nothing is written. With several envelopes, any hard statement of a
limit wins over a soft one.

Each `capacity.sides.<side>` names its `binding_constraints`: the outline, the
fixed parts and obstacles in `blocked_by`, and the edge margin and gap. Each
has its `class` and `enforcement`. The margin and gap come from the policy or a
fab profile; without a qualified source their provenance is `unknown`. The unsourced margin
is soft under AGENTEE-358. Without declared provenance the
outline is `unknown` too.

Every `packing_infeasible` proof also carries `binding_constraints`. When one
of them is soft (an assumed or owner-weakened limit), the proof stays in the
record, but the outcome is `unknown` instead of `defect`. The result then has
`owner_confirmation` (the soft constraints), and the reason names them. A proof
bound only by hard or undeclared limits keeps `defect`, except that an unsourced
gap is advisory under AGENTEE-338, an unsourced margin is soft under
AGENTEE-358, and unsourced policy obstacles and group regions are soft under
AGENTEE-361. A zero-gap proof does not bind that gap; a zero-margin proof does
not bind the margin. Owned tests are in
`tests/test_constraint_provenance.py`.

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

TRAPEZOID (3), unqualified CUSTOM (6), any other value, a non-integer shape, a pad angle
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
XML net names are compared in their [board form](forward-annotation.md#native-net-name-encoding-agentee-220)
(`{slash}` for a literal slash, AGENTEE-220); an ambiguous local slash is refused.
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
console/execution records describe the disposable proposal. A `blocks` request
also publishes `block-provenance.json`, the reviewers' record of each placed
block's provenance ([blocks.md](blocks.md#review-input-block-provenance-agentee-355)). Transformed pure
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
saved pads match the source. Trapezoid, unqualified custom, 45-degree and per-layer
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


## Block candidates (AGENTEE-339)

[Blocks](blocks.md) derive placement blocks from the sealed schematic hierarchy
or an engineer allocation file. Each block is packed alone with this search,
around a fixed anchor on a synthetic plane, into a rigid
`agent-ee.block-candidate/2`, then re-packed on shrinking outlines to the least
box area ([compaction](blocks.md#compaction-agentee-355), AGENTEE-355). A first plan that
strands a plane-only member is retried with a temporary anchor-proximity group
([recovery](blocks.md#recovery-agentee-359), AGENTEE-359). Its own pad obligations and groups apply, and
boundary constraints are deferred to arrangement (AGENTEE-340). Search logic is
unchanged. [Arrangement](blocks.md#stage-b-arrangement-agentee-340) admits a
candidate only after `blocks.admit` (or a mapped match and re-grade) checks it
against the board. Arrangement places the blocks as rigid macros with this
module's predicates, then packs the residual parts with `pack`, with block
members fixed. A writer request with a `blocks` section takes that path
through the same save, reload, grade and native DRC gate.

## Pod-scale qualification (AGENTEE-181)

Run the original fixture qualification on the host, using a fresh destination:

```sh
PYTHONPATH=src python3 scripts/qualify_pod_placement.py --out build/pod-scale-placement-qualified
```

The record is `build/pod-scale-placement-qualified/qualification.json`. Each case retains
its intent, footprint manifest, emitted schematic, actual sandboxed KiCad XML
export, forward-annotation plan and apply evidence, placement request, both
sandbox executions, saved boards, native DRC reports and per-criterion verdicts.
The record gives elapsed seconds for each placer invocation (including sandbox
startup, saved-board checks and native DRC), placed count, side counts, transforms,
computed board hashes, input preservation and repeatability.

| Outline | Footprints placed | Side assignment | Footprint keepout | Runtime, repeats 1 / 2 |
| --- | --- | --- | --- | --- |
| 80 × 50 mm | 90 / 90 | 45 front / 45 back | 48 × 21 mm, both sides | 28.228 / 32.811 s |
| 85 × 55 mm | 112 / 112 | 56 front / 56 back | 48 × 21 mm, both sides | 64.245 / 58.715 s |
| 90 × 65 mm | 112 / 112 | 56 front / 56 back | 48 × 21 mm, both sides | 54.088 / 51.630 s |

The 2026-10-20 host run passed all four placement predicates in both attempts
for every row. Saved bytes and transforms repeated identically; source inputs
remained unchanged. Timings include concurrent host load and are observations,
not performance limits. The oversized C1 returned `packing_infeasible`,
`outcome=defect`, exit 1 with a diameter proof and no proposal.

The 80 × 50 mm envelope is an assumed exploration point, not an owner-stated
size requirement. The range includes the 90 × 65 mm double-sided pod scale
reported by the coordinator. These fixtures use only authored rectangular
mechanical stand-ins: a 25 × 10 mm connector, two 18 × 14 mm modules, eight
7 × 5 mm parts, twenty 3.5 × 2.5 mm parts and remaining 2 × 1.5 mm parts.
All use the repository's owned two-pin CAP symbol with synthetic VCC/GND nets.
They qualify placement mechanics and scale; they do not establish that the actual
pod's package mix, pin escape, electrical grouping or routing fits these outlines.
No actual pod design or external library is copied.

The packer receives a 0.25 mm courtyard gap, a 0.5 mm courtyard edge margin and
a 200000-step budget. Every side is assigned explicitly before placement; the
probe does not imply automatic side selection. The four existing check evaluators
(`courtyard-overlap/1`, `outline-containment/1`, `edge-clearance/1`,
`footprint-keepout/1`) grade the saved proposal and all-severity native DRC.
These standalone records are unsealed fixture evidence, not campaign acceptance.
Unconnected copper remains expected before routing.

An oversized control replaces C1's courtyard with an authored 100 × 2 mm
rectangle. Its diameter exceeds the 80 × 50 mm outline's diameter while its
area stays below the board's: the existing infeasibility proof must name C1,
return a typed failure and publish no proposal. This distinguishes the named
part failure from the aggregate area proof. Missing XML is separately exercised
as failed execution without a result or a passing verdict.

`tests.test_placer_pod_scale` runs the whole matrix with real native tools and
requires all parts placed, all four verdicts passing, identical transforms and
saved bytes on repetition, unchanged inputs and the named oversized failure.
There is no timing threshold and no change to packing constraints, verdicts,
check registration, campaign policy or the evidence model.

## Native custom pads (AGENTEE-307)

CUSTOM (6) is admitted per pad when a fresh native placement probe supplies
`custom_geometry.status=proven`. The probe reuses the bounded S-expression
reader and [AGENTEE-300 qualifier](footprint-libraries.md#native-custom-pads-agentee-300):
explicit solid-connected SMD pads on one front/back copper layer, outline
clearance, circle/rectangle anchors and bounded `gr_poly`, `gr_rect`, `gr_circle`
or `gr_line` primitives. Unsupported syntax stays `unsupported_geometry`,
`outcome=unknown`; an old probe with only the shape number is insufficient.
Native merged copper must be nonempty and have no holes. Multi-island geometry
is retained in full. No copper-clearance claim follows from this admission.

The probe retains the full qualified primitive list, native anchor, native
pad-local merged polygons and native copper bounding box, including the anchor.
The search rotates that full extent along with the pad. A complete plan lists
`qualified_custom_pads` by reference, UUID and electrical number; `pad_shapes`
continues to list the unconditional stock shape domain. The native writer
compares primitives, merged geometry, pad identities, nets, layers and the
transformed copper extent after save/reload. A dropped redundant primitive is
refused even when its removal leaves the merged extent unchanged. Rectangle
corner ordering is canonicalized because KiCad normalizes corners after a flip;
all other primitive tokens and their order stay exact in the write-scope gate.

Owned fixtures exercise all four primitive kinds together, every source/target
quarter turn, both mounting sides, both native flip directions and flip-back
identity. The packing search keeps the supplied side assignments. These tests
qualify native custom-pad preservation on both sides. The campaign write-scope
checker also admits writer-requested flips of native-qualified custom-pad
footprints, including their footprint arcs, through an exact sandboxed native
reflection comparison (see [write-scope qualification](placement-adapter.md#custom-pad-side-flips-agentee-307)).

On the real expanded pod at fome-mc `845284fbbf907197a7476d23ce59490afb5e26f1`, a fresh extraction of the imported
board admits both pads of JP1–JP5. Scratch native write/save/reload probes preserve
all ten pads through every quarter turn, both native flip directions and exact
flip-back; these probes supply geometry evidence only. With the lane's unchanged diagnostic policy,
the next result is `packing_infeasible`, `outcome=defect`, `steps_used=0`,
`placements=[]`, reason **`F courtyard area exceeds board area`**. The exact
courtyard-area proof records board area `[5850000000000000, 1]` and required
front courtyard area `[7898547501909592, 1]` nm². All 331 footprints are staged
on the front; this says nothing about fit with intended double-sided assignments.
The external board and its probe are read-only inputs, not repository fixtures.

Focused native regression:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest tests.test_placer_custom_pads tests.test_placer_native -v
```

The expanded pod side seed at fome-mc `f9305366a810c884b9c8f5e9c0ac9e5b324d2e17` now passes write scope with zero
refusals (previously 16 footprint arcs and eight custom pads on JP2–JP5).
With that seed's explicit side assignments and the retained diagnostic packing
policy, the next result remains `packing_infeasible`, `outcome=defect`,
`steps_used=0`, `placements=[]`, reason **`F courtyard area exceeds board area`**.
The exact front-side courtyard-area proof records board area
`[5850000000000000, 1]` and required area `[6549088000000000, 1]` nm².
Admission of the candidate transform does not establish packing feasibility.

### Copper extent gate (AGENTEE-366)

Before AGENTEE-366 the search packed and gated candidates on the declared
courtyard only. A part whose copper reached past its courtyard (an offset custom
pad, or a stock land whose shape centre sits off its anchor) could be planned
with that copper off the board. On an original native fixture the courtyard-only
search published such a board as `placed`, `outcome=info`, with no open defect,
so no later stage caught it.

Candidates are now gated on courtyard and copper extent together. Each pad's
copper is bounded by an integer box, rounded outward and derived from the
capture only:

- a proven custom pad uses its native `custom_geometry.bbox_nm` (the anchor
  and every primitive);
- rectangle, oval and rounded-rectangle lands use `size_nm`, quarter-turned
  by the pad rotation, about the native shape centre (`position_nm` plus
  `copper_centroid_offset_nm`, which is the shape centre for these symmetric
  shapes);
- a circle is native: its X diameter (`size_nm[0]`) on both axes at every
  rotation, so a record with unequal sizes is not under-bounded;
- a chamfered rectangle contains its area centroid, so the box twice its size
  about that centroid contains it (conservative);
- a chamfered rectangle with rounded corners has no qualified area centroid
  (`copper_centroid_reason`); native extraction then records
  `copper_land_offset_nm`, the integer offset from `position_nm` to the native
  shape position (`ShapePos`), and the land is its quarter-turned size box about
  that point. The land is its size rectangle with corners cut or rounded
  inward, so the box contains it.

A copper box that lies in a courtyard polygon of the part at the setback S
(the box grown by S on every side fits the courtyard) adds nothing: that
courtyard's own outline test already covers it. Each remaining box must lie in
the board outline at least S from every outline edge (`min_courtyard_edge_mm`
stays a courtyard margin and does not apply to copper).

The setback S is structural, not an engineering clearance (coordinator ruling
366e). The AGENTEE-331 publication gate polygonises pads with `ERROR_OUTSIDE`
at the board's `m_MaxError` (`copper_outside_outline`), and reports edge
clearance to 4 decimals, so a gap under 50 nm cannot be told from a crossing
(`copper_edge_clearance`, structural). Flush copper is therefore refused there,
and S = `max_error_nm` + 50 nm. `placement_probe` reads `max_error_nm` from the
saved board's design settings into the placement facts. A copper-aligned plan
records `search.copper_aligned.copper_setback` (`setback_nm`, `max_error_nm`,
`report_window_nm` and the source). A capture without `max_error_nm` has no
default. When a movable part's copper leaves its courtyards (and, in block
arrangement, any non-fixed member's copper), the search returns
`unsupported_geometry`, `outcome=unknown` ("copper setback unknown"). A
targeted plan reports `missing coverage: <ref>: copper setback unknown`. A
malformed value (negative, or not an integer) is also `unsupported_geometry`.
Without the fact, the in-courtyard exemption uses closed containment. That only
matters for hand-built records, because native extraction always records the
value. Edge-clearance limits keep their AGENTEE-331 provenance grading,
unchanged. On each outer copper side (`F.Cu`, `B.Cu`, `*.Cu`), boxes
outside that side's courtyards must avoid footprint-forbidding keep-outs on
that side. Board keep-outs, placed owners' keep-outs and a moving owner's
keep-out against placed peers' copper are all checked; an owner stays exempt
from its own areas. A pad record without `layers` is taken to reach both
sides.

Legacy first. Every pass gates copper, but the first search (and the joint
cluster pass) keeps the courtyard-derived candidate positions unchanged. Only
when those fail and some movable part has copper outside all its courtyards
does `pack` run a copper-aligned pass. That pass uses the remaining step budget
and the same absolute deadline, and aligns escaping copper boxes to the outline
as courtyard boxes are, held off it by S, so the fit can sit exactly S from the
board edge. A plan from
it records `search.legacy_attempt` (the first pass alone, as for joint
completions) and `search.copper_aligned.prior_steps_used` (legacy plus joint
steps); its `steps_used` is the total. A failed aligned pass returns the earlier
result unchanged, so a failure record is byte-identical to the legacy-first
result (the `tests.test_placer_clusters` differential). An earlier admissible
plan is never replaced. A plan changes only where the courtyard-only
search would have put copper off the board or into a keep-out.

The gate applies to the packing search, to Stage-B block arrangement (each
member's copper, so a member at the block edge cannot overhang; a block whose
members have copper outside their courtyards also gets outline-aligned axes at
S for that copper, other blocks keep their candidate positions) and to targeted plans (`target copper outside outline
or in keep-out`). A part whose copper lies in its courtyards caches nothing, and
its candidates, steps and plan are those of the courtyard-only search.
Rejections count against the existing step budget only; no wall-clock limit is
added. Fixed parts are not regated, because their pose is the designer's.

The pinned seeded differential `tests.test_placer_geometry_cost` (ac662ff plans)
changes for seeds 0, 3, 23 and 38 only: the pinned plan puts 0.2 mm lands up to
0.073 mm off the board (3, 23, 38) or into a board keep-out (0). The gated
results remain `planned`. Likewise, in `tests.test_placer_clusters`
`test_seeded_legacy_plans_preserve_verdict_placements_and_steps`, the frozen
plans for seed 1 case 1 and seed 2 case 1 put C3's copper 0.05 mm off the board.
Gated, both stay `planned`/`info`, with other placements and step counts. By
coordinator ruling these six pins were re-pinned to the gated results, and only
these six. Each re-pinned plan is also checked by `copper_violations` in
`tests/test_placer_copper_extent.py`. That check is independent of the placer's
gate: it boxes every moved land from its own record and compares it with the
outline and the board copper keep-outs.
`IndependentCopperCheckTest` reproduces each superseded pin with the
courtyard-only search and shows the check flags it.

A failed copper-aligned pass is not reported in the result. Any added field,
including one outside `search`, breaks the whole-result legacy-first identity
pinned by `test_cluster_guard_is_unknown_and_cleans_up_partial_state` and
`test_seeded_legacy_first_differential_preserves_whole_results` (cases 1:3 and
1:5). The steps it spends are therefore not visible in the failure record.

A fixed part's copper is taken as given. In seed 4, fixed `P0` has a land
0.055 mm past the board edge in the input itself, and the plan reports it
unchanged.

Boundaries. Missing copper extent is never "no constraint". Malformed
`size_nm`, `copper_centroid_offset_nm`, `copper_land_offset_nm` or `layers`,
a centroid or land offset without `size_nm`, and a copper land whose
`copper_centroid_reason` comes without a proven land offset (per-layer
padstacks, for example) return `unsupported_geometry`, `outcome=unknown`.
Native extraction records, for every pad, either a centroid or the reason it
has none, so a native land always has a box or refuses. Only a record with
none of `copper_centroid_offset_nm`, `copper_centroid_reason` and
`copper_land_offset_nm` (a hand-built capture, possibly with `size_nm` alone,
which places nothing) keeps the courtyard-only bound; existing hand-built
fixtures plan exactly as before. This is an accepted residual (coordinator
ruling, AGENTEE-366): a native record edited down to `size_nm` alone is
indistinguishable from such a capture and is also bounded by its courtyard
only. Native extraction never emits that shape, and the native tests assert
exactly one of centroid or reason per pad. A native record that keeps its
`copper_centroid_reason` but is relabelled as a stock shape stays unknown
(`test_placer_custom_pads.CustomPadPlacementTest.test_stock_pad_without_custom_evidence_still_plans`);
the same record with the reason removed plans
(`test_legacy_stock_pad_without_custom_evidence_still_plans`). Inner-layer-only keep-outs, rule areas
forbidding only pads, and copper against peer courtyards are not packing
constraints. The native DRC publication gate remains the authority for copper
clearance.

Focused regression, including native writer runs at every quarter turn on both
sides (offset custom pads, rounded chamfered lands and unequal-size circles,
measured from the saved board's own copper polygons) with mutant controls for
the superseded bounds, a per-layer padstack refusal, and the box against every
native land vertex for each stock shape:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest tests.test_placer_copper_extent -v
```

### Through-hole reservations and native publication gate (AGENTEE-318)

On a board with PTH or NPTH pads, the native probe records
`packing_pad_geometry` for every footprint. pcbnew's
`TransformShapeToPolygon` reports copper on each enabled layer the pad spans;
`TransformHoleToPolygon` reports the drill, including slots. Both use outward
bounds with a 1 µm error. Through-hole copper and drills grow by the maximum
native board copper and hole minimum; copper also includes the applicable pad clearance. Peer copper
adds any excess of its native effective clearance over that minimum, including
a larger peer netclass, local clearance or single-item custom rule
(AGENTEE-325). This can refuse a
placement that a same-net rule would admit. KiCad 10 SWIG exposes per-pad
`GetOwnClearance`, but no pairwise custom-rule evaluator: `.kicad_dru` rules
that depend on both `A` and `B` are enforced by the native publication gate, not resolved
by these reservations. No custom-rule parser or substitute evaluator is used.
Each drill contour carries its `pad_uuid` and `reservation_kind: drill`;
qualification requires exactly one drill entry for every drilled pad, including
multiple drilled pads in one footprint. Legacy incomplete captures must be reprobed.
Unsupported contours or padstacks yield unknown coverage.

The packer checks these reservations against peer courtyards on both sides
and peer copper, including copper outside a courtyard. The check is symmetric:
it applies when the drilled footprint moves and when a peer moves toward it.
Fixed conflicts name both footprints. Targeted plans use the same predicate.
These reservations never create or modify a courtyard, and do not participate
in courtyard area proofs. An exhausted search remains unknown.

Before publication, the writer runs native DRC on the baseline and saved
candidate using byte-identical copies of the source `.kicad_pro` and
`.kicad_dru`, when present. With no project file both runs use native defaults.
The full reports and executions are retained in `baseline/` and the proposal
root. `native-drc-comparison.json` compares category plus sorted native item
UUIDs as a multiset. For collision and mask findings, it also compares saved
native pad and footprint-graphic geometry and footprint transforms
(AGENTEE-325). A cross-footprint violation involving a moved footprint is new,
even when both items collided in the baseline. A rigid intra-footprint finding
with unchanged local geometry remains a baseline finding. Missing native item
geometry refuses comparison. A native item KIID shared across different
footprints also refuses comparison, including case aliases normalized by KiCad;
it cannot establish an intra-footprint baseline. Non-collision numeric categories also bind to absolute saved geometry; other categories retain UUID-based reporting. Enforcement changes and a new
outline crossing count as new findings on otherwise identical item UUIDs.
Any added `shorting_items`, `pth_inside_courtyard` or `holes_co_located` refuses
publication with an engineering citation to AGENTEE-318/325/331. Printed copper-edge actuals below 0.00005 mm (50 nm) are also structural,
regardless of the numeric edge limit. KiCad reports touching/crossing copper as
zero. A separate native child subtracts the board material from every saved
effective copper polygon; nonempty differences are engineering/hard
`copper_outside_outline` witnesses and always refuse publication, even when
native DRC omits wholly outside copper. See the
[measurement boundary](scaffold.md#declared-design-rule-sources-agentee-331). Numeric findings, including `clearance`,
resolve against the [declared sources](scaffold.md#declared-design-rule-sources-agentee-331):
owner, manufacturer, datasheet and engineering limits block; default or unresolved
limits remain visible advisories. Captured owner rules cite the immutable file,
SHA-256 and rule name. The comparison retains classified baseline and candidate
findings, including silk and text findings, and retains failures with the candidate. An introduced
`solder_mask_bridge` between different footprints also blocks publication;
baseline and intra-footprint mask bridges remain non-blocking and reported.
Ignoring a required collision or mask category, a failed tool, missing output,
incomplete severity coverage or changed evaluation settings also refuses publication. Existing
courtyard and write-scope gates remain in force. Rule-level `(severity ignore)`
in `.kicad_dru` never appears in native `ignored_checks`, so the gate refuses
any such token, including case, whitespace and quoted variants. This conservative
refusal also applies to ignores scoped to unrelated categories; it does not
evaluate custom-rule conditions. Added `silk_*` findings are
reported separately in `silkscreen_findings`; they are not shorts. The
[post-placement artwork pass](silk-artwork.md) relocates permitted fields and
carries remaining `silk_overlap` or `silk_over_copper` as open defects in a
published proposal (AGENTEE-333). Fab legibility remains a separate obligation;
the [downstream carried-defect gate](silk-artwork.md#placement-feasibility-and-fab-legibility-agentee-333)
refuses while any carried defect remains open.

`tests.test_placer_through_holes` supplies original native PTH/NPTH, slot,
shape, rotation, opposite-side and settings fixtures, plus DRC regression,
identity/multiplicity, ignored-category and silk-separation controls. This
proposal gate does not replace captured coordinator checks or authorize adoption.

AGENTEE-317 refuses ambiguous custom-pad evidence: every pad sharing a custom
pad's native UUID is marked unsupported, including case, brace and dash aliases
and collisions across footprints. Unique pads with noncanonical saved UUID
spellings retain their previous unknown coverage. Non-object `custom_geometry`
and unqualified `proven` evidence on a stock or custom pad also return
`unsupported_geometry`, `outcome=unknown`, with no placements. Direct
`transform` calls raise `ValueError` for non-object evidence; callers must qualify
`proven` dictionaries before transforming them, as `pack` does. The native
regressions in `tests.test_placer_custom_pads` cover both refusal paths.

## Pad-level electrical locality (AGENTEE-322)

`policy.electrical_locality` opts the packer into pad-level constraints. It is
an `agent-ee.electrical-locality/1` document. The separate coordinator `placement`
check selects `criterion: electrical-locality/1` and a captured `locality` JSON
path containing the same document. The check is acceptance evidence; the
standalone plan and writer output remain unsealed proposals.

For coordinator-run packing (AGENTEE-328), the adapter reads the one captured
locality file named by the scope's locality checks, verifies its immutable raw
SHA-256, and derives `policy.electrical_locality` from those bytes. An existing
inline document must equal the captured document; a mismatch refuses before the
helper runs. The binding record retains `electrical_locality: {path, sha256}`;
the effective helper policy carries the same pair as
`electrical_locality_binding`. A supplied binding must match. Missing, changed,
linked or mutable locality files refuse, as do several different locality paths
or inline locality without a coordinator locality check. Repeated checks of the
same file are allowed. The check judgment independently retains
`locality: {path, sha256}`. Standalone `pack()` accepts inline declarations for
diagnostics but cannot establish a coordinator capture binding.

```json
{
  "schema": "agent-ee.electrical-locality/1",
  "obligations": [{
    "id": "owned-example-C1",
    "metric": "pad-centre-to-centre",
    "supply": {
      "from": {"reference": "U1", "pad": "8", "net": "VCC"},
      "to": {"reference": "C1", "pad": "1", "net": "VCC"},
      "max_distance_nm": 5000000,
      "citation": "Owned example requirement E1: supply pad centres within 5 mm."
    },
    "return": {
      "from": {"reference": "U1", "pad": "4", "net": "GND"},
      "to": {"reference": "C1", "pad": "2", "net": "GND"},
      "max_distance_nm": 5000000,
      "citation": "Owned example requirement E2: return pad centres within 5 mm."
    }
  }]
}
```

These example limits have no recorded provenance and are advisory (AGENTEE-337),
with no manufacturer or pod acceptance claim. Each obligation requires explicit supply and return pad
identities ending at the same capacitor, with distinct physical pads on each
shared footprint. The supply source may differ from the return source
(AGENTEE-330): a buck output loop declares inductor pad 2 → capacitor positive
as `supply` and IC PGND → capacitor negative as `return`. Pad numbers may be equal
on different source references; reference, pad number, net and any captured UUID
still identify each endpoint exactly. For distinct source references, the supply
and return endpoint net sets must be disjoint, even without a `kind`
(AGENTEE-341). Each pair must name the same net at both ends. A buck may additionally declare `switch`
with the identical pair structure: `from` selects the return-source IC switch
pad and `to` selects its inductor pad. Shared rail membership never assigns a capacitor to
an IC. Select the explicit `buck-output-loop` kind below to bind component and
return-net roles. Multiple selected supply/return pins require separate obligations.

An endpoint may add `uuid` with the canonical native pad UUID from the captured
`placement-probe.json`. The UUID must occur exactly once across the board and
belong to the declared reference, pad number and net. This selects one physical
land of a multi-pad pin, including exposed-pad thermal lands with repeated pad
numbers. A missing, duplicated or mismatched UUID remains unknown. Without a
UUID, duplicate pad numbers still refuse; the checker never picks the first or
nearest land. There is no implicit pad-set aggregation. Declare separate
UUID-selected obligations when every land must meet a limit.

AGENTEE-328 measures `pad-centre-to-centre` between native **copper-land
centroids**, before drill subtraction, in the board plane. Symmetric stock
circle, rectangle, oval and roundrect pads use pcbnew `PAD.ShapePos()`, which
includes the copper offset from the electrical anchor. Straight trapezoids,
straight chamfered rectangles and qualified custom pads use exact signed-area
moments of the native copper polygons. Custom centroids require a rectangular
anchor and filled, zero-width `gr_poly` or `gr_rect` primitives. Curved or stroked
custom copper, rounded asymmetric chamfers, per-layer padstacks and missing
centroid evidence remain unknown; there is no anchor fallback. Footprint
rotations and native side flips move the copper with the pad. The native probe
adds per-pad `copper_centroid_offset_nm`: two `[numerator, denominator]` pairs
in board axes, relative to `position_nm`. Missing evidence has a
`copper_centroid_reason`; a rounded chamfered rectangle also gets
`copper_land_offset_nm`, a bound for the copper extent gate only, never a
centroid. Older captures need a fresh native probe before they
can establish copper locality.

Squared distances are exact rational nanometres, including equality. Integral
`distance_squared_nm` stays an integer; a fractional value is encoded as
`{numerator, denominator}`. Floating `distance_mm` is reporting only. This metric
does not measure copper-edge separation, routed length, a 3D return path or
loop inductance. Other metric names remain unknown. A missing/duplicated
reference or pad, wrong native net, unavailable centroid, absent limit or absent
citation remains unknown. Limits must be explicit nonnegative integer
nanometres. The citation is the declaring engineer's source/locator text,
retained verbatim in the immutable obligation document. A citation alone grants
no hard numeric authority. Supply, return and switch are all measured; a proved
sourced violation remains a defect even when another pair is unknown. Missing limits never inherit an origin-group radius.

The **placement constraint** is distinct from this **check**. For each rotation,
the packer adds candidate origins from the selected native pad offsets, peer
pad centres and declared radius extrema. It ranks candidates by the exact sum
of squared distance/limit squared across all resolved supply, return and switch
pairs, ahead of the existing net cost. Ground pairs participate even when the
net is in `ground_nets`. Only pairs with verified numeric provenance impose a
hard distance constraint, checked symmetrically whichever endpoint is placed second.
Advisory pairs affect the objective only; they generate no extra target origins
and cannot exhaust the hard target-origin cap. An unplaced peer is deferred;
all sourced pairs must pass before the complete plan is returned and again after
native save/reload. An advisory-only `info` judgment permits publication. Fixed violations or finite search exhaustion publish no placements
and remain unknown feasibility. Missing inputs refuse before search. Targeted
plans also refuse a completed proposal violating these obligations; their
existing origin-based objectives are unchanged.

`groups` keeps its existing footprint-origin and courtyard meaning. A passing
origin group is not electrical acceptance. Locality neither weakens courtyard,
through-hole reservation or native DRC gates nor establishes routing acceptance.

`tests.test_electrical_locality` exercises exact boundaries, missing evidence,
coupled return/switch regressions, both placement orders, deterministic guidance,
fixed conflicts, a saved native proposal and the registered captured check.
`tests.test_locality_copper` adds native offset and custom copper on both sides
at all quarter turns, exact fractional distances, distinct-UUID near/far
selection, and coordinator policy/hash binding with refusal boundaries.


### Locality provenance (AGENTEE-337)

Every pair may add `provenance`, an object with `class` and a nonempty `basis`.
Classes use the AGENTEE-331 vocabulary: `datasheet`, `manufacturer`, `owner`,
`engineering`, `default`. Missing, unknown, prose-only or invalid derivations
produce `advisories` with `kind: unsourced`, `severity: defect`, and `exceeded`
and `below_floor` flags. Advisory-only results are `info`; they never become coordinator gate
findings or hard pack constraints. Geometry/identity failures and absent limits retain unknown coverage.
A citation is required only for hard enforcement. Hard defects retain priority
when another pair is advisory or unknown.

The document may include `source_records`, mapping relative source paths to
captured UTF-8 source text. Records are part of the same immutable locality
capture; they are not files to open, a live fetch, or an owner evidence store.
Each source reference contains `path`, the SHA-256 of that exact UTF-8 text,
`locator`, and an exact `quote` present in that text. Hash, missing-text or quote
failures leave the bound advisory. Source identity and applicability remain
review obligations: a matching digest is not proof of vendor authenticity or
owner authorship. Do not publish third-party source text without rights approval;
original numeric transcription records can retain source identities instead.

For `datasheet`, `derivation.kind: numeric` requires `value_nm` equal to
`max_distance_nm` and an equal explicit nm or mm number in the source quote.
`derivation.source` carries the source reference above. A prose citation such as
“as close as possible” cannot qualify. A measured recommended/reference layout
uses `kind: layout`, integer `distance_units`, integer `scale_nm_per_unit`, a
nonempty `scale_reference`, and the same source reference. The check recomputes
the product exactly. This is a declared measurement with a recorded scale;
it does not automatically interpret a PDF image.

For `owner`, `decision_reference` carries the same verified source reference
plus a nonempty `owner` identity. An agent identity or a case-insensitive
`design-owner`, `design_owner`, `design owner` or `designowner` label is
not admitted. An owner label without the recorded decision stays advisory.
This checks the retained decision reference, not authenticated authorship.
Original tests use explicit fixture-author decisions to retain their numerical
contracts; those decisions establish no real hardware requirements.

For `manufacturer`, the pinned original numeric records in
`src/agent_ee/data/jlcpcb-locality.json` retain host capture URLs, timestamps,
response hashes and package-pair spacing values. Raw vendor bytes are excluded.
The numeric-record digest is pinned in policy code. A missing or changed record
leaves an explicit manufacturer check unknown; it cannot earn an advisory-only
result from unavailable manufacturing evidence.
The check computes a **necessary copper floor** from both footprints' native
lands and the captured 0.15 mm different-net PCB clearance. Each qualified SMD
circle, rectangle, oval or roundrect supplies an inscribed disk. At coincident
selected centroids, each different-net cross-footprint pad pair excludes a disk
of relative translations. The maximum of `r1 + r2 + clearance - offset1 - offset2`
(over zero), rounded down, is a necessary distance to escape those exclusions.
Each offset is a pad centroid distance from its selected locality centroid,
rounded up. The triangle inequality makes this lower bound valid at every
relative footprint rotation.
Only pads sharing a copper layer participate. This disk subset can prove a
lower bound even when other copper is unsupported; it never proves completeness.
Pad-pair products over 4096 and unsupported selected lands have unknown floors.
Disk identities are resolved once per footprint in each independent grade.

For hard sourced or explicit `manufacturer` provenance, a bound below this known PCB floor
returns a typed `locality_infeasible` defect naming the obligation, role and
references; packing refuses it before search. Unsourced bounds remain advisory
even below that floor, with `below_floor: true`. An unavailable geometric floor
also stays advisory for manufacturer labels; captured-record integrity failures
remain unknown. Hard bounds below the necessary floor are typed infeasibility
defects (coordinator ruling G3).
A manufacturer minimum supplies no electrical maximum, so exceeding a larger
agent-selected maximum remains advisory.

Assembly spacing remains an **unknown full manufacturing floor**. The captured
matrix measures body/lead extents, while native pads describe solder lands.
The report includes an advisory `assembly.pad_envelope_estimate_nm` for explicitly
named package families in the matrix; it never rejects against that estimate.
Unlisted packages and opposite-side placement never inherit another package's
value. Other leads, bodies and different-net copper can raise the true minimum.
The exact whole-footprint minimum and qualified physical extents are not
implemented. AGENTEE-337 therefore retains this acceptance gap; a known copper
floor, including zero, is not an assembly or whole-footprint pass.

`tests.test_locality_provenance` covers legacy/agent advisories, source derivation,
source/decision refusals, soft index constraints and computed infeasibility.
Joint-cluster axis pruning and conflict sets use hard pairs only; advisory pairs
may influence ordering but never exclude candidates. The registered check
summary reports both the advisory count and the exceeded-advisory count.

The [block candidate path](blocks.md) preserves captured `source_records` and
pair provenance. Advisory-only and mixed blocks can pack and admit with locality
`info`; only hard pairs prune the search. Candidate validation checks truthful
advisory records, and admission re-grades every pair from the source board.

### Read-only pod diagnostic

Fresh native extraction of the three supplied lane candidates measured the
following pad-centre distances (mm). These are diagnostic observations; the
lane files provide origin limits but no cited numeric pad-level obligations.

| Pair | Original locality lane | Storage/power lane | Main-buck lane |
| --- | ---: | ---: | ---: |
| U26.8 → C97.1 supply | 5.589331 | 2.510383 | 2.206836 |
| U26.4 → C97.2 return | 3.641844 | 8.276450 | 7.531037 |
| U26.8 → C98.1 supply | 6.791381 | 1.963173 | 7.417186 |
| U26.4 → C98.2 return | 2.069118 | 8.578406 | 2.869190 |
| U7.6 → C11.1 supply | 2.013982 | 1.982278 | 7.580872 |
| U7.1 → C11.2 return | 8.369231 | 9.258668 | 2.093161 |
| U5.8 → L1.1 switch | 12.285643 | 12.023220 | 6.859360 |
| U7.8 → L2.1 switch | 31.485048 | 15.754277 | 5.315473 |

Using those explicit identities with absent pad limits, each check is `unknown`.
Each guided pack request returns, verbatim:
`status=electrical_locality_unavailable`, `outcome=unknown`, `steps_used=0`,
`placements=[]`, reason `pad locality needs unambiguous native pads, limits and citations`.
No pod design was changed or copied into this repository. Numeric limits are
required for declared supply, return and switch pairs before a bounded guided
pod run can proceed. Unsourced limits can guide it as advisory terms; hard
enforcement additionally requires the validated provenance and citation above.
The owned fixture does demonstrate a changed packed transform: an unguided
0.500 mm supply / 4.924 mm return becomes 1.803 mm / 1.803 mm under its explicitly
declared 4 mm limits. That fixture limit supplies no pod acceptance threshold.

`tests.test_placer_gate_hardening` covers moved baseline collisions, rigid
intra-footprint preservation, peer netclass reservations, per-pad hole
completeness, native inter-footprint mask bridges and ignored mask checks,
including shared cross-footprint KIIDs and rule-level ignores. Rigid intra-footprint
fixtures cover both shared and distinct pad KIIDs.
Netted original fixtures exercise a 4 mm peer netclass and a 4 mm single-item
custom rule. A 4 mm custom rule conditional on both pad nets proves the native
gate refuses a gap that satisfies the board minimum.


### Buck output-capacitor loops (AGENTEE-330)

AGENTEE-334 adds an explicit role binding to the existing
`agent-ee.electrical-locality/1` document. Add these fields to an obligation:

```json
{"kind": "buck-output-loop", "ic": "U1", "inductor": "L1", "return_net": "GND"}
```

The existing `supply`, `return`, and optional `switch` pairs still select exact
native pads and carry their own limits and citations. For this kind,
`supply.from.reference` must equal `inductor`, `return.from.reference` must equal
`ic`, and these two references must differ. When present, `switch.from.reference`
must equal `ic` and `switch.to.reference` must equal `inductor`. Both return
endpoints must use `return_net`; neither supply endpoint may use that net.
Neither switch endpoint may use `return_net` or either supply endpoint net
(AGENTEE-341). A switch leg on the output rail therefore refuses even when its
native pads and distance would otherwise pass.
Net names and reference prefixes never infer these roles. A declaration of IC
and inductor identity is supplied by the engineer; this check does not establish
the component's device type from a library or datasheet.

Role metadata requires the complete four-field binding above. Unknown kinds,
missing role identities, swapped supply/return nets and unrelated rail members
refuse validation before grading or packing. Per-pair native identity, centroid,
limit and citation failures retain their existing unknown/defect precedence.
Omit the switch pair when no numeric switch limit has been declared. An unsourced
switch limit is advisory under the same provenance rules as supply and return.

Untyped same-source obligations retain their existing verdicts, including
input decoupling with a switch leg. Untyped distinct-source obligations require
disjoint supply/return net sets but do not assert the loop-role contract.
Disjoint nets alone cannot identify an unrelated source on the same rail or
detect a supply/return swap: explicit role binding is required for those cases.
New output-loop declarations must select the kind explicitly. Check, pack
constraints, candidate targets and saved-proposal grading then use all declared
pairs. Distances bound individual legs, not a summed
perimeter, routed path or inductance.

The retained original fixture is `bound_output_loop()` in
`tests/test_electrical_locality.py`. Its U1/L1/C1 pad identities, citations and
limits are authored test requirements; it contains no third-party design data.
`BoundOutputLoopTests.test_owned_evidence_and_optional_switch` reproduces exact
squared distances of 89, 162 and 16 mm² for supply, return and switch, retains
each citation and the full declaration in the result, and exercises omission of
the switch leg. Other tests refuse R9 on VCC, independently exercise both switch
bindings with distinct endpoints, and guide all six placement orders. Existing
generic-pair regressions retain their verdict expectations.
A captured native check also exercises explicitly bound roles on the owned RC
stand-in; its R1/J1 roles are fixture declarations, not a claim about RC hardware.

The read-only pod D-PL-1 packet is committed in fome-mc at
`fc73b20f2dd91f26b3eb3a7319e5b0895d0a8be9`, under
`telemetry-pod/expansion/electrical-locality/pad-obligations/`.
`electrical-locality.json` retains 15 generic obligations / 33 pairs;
`frozen-79cc1310-measurements.json` retains their diagnostic measurements and
`README.md` gives the declarations and source locators. These existing untyped
obligations remain valid. The packet does not declare the seven additional
inductor-to-output-capacitor loops previously reported here from disposable
scratch. Those measurements are withdrawn as retained evidence; the owned fixture
above is the reproducible output-loop evidence. No pod design is copied,
modified or adopted, and pod output-loop bounds still require an engineer's
captured declaration.

## Incremental locality search and diagnostics (AGENTEE-332)

After the independent initial locality grade qualifies identities and copper
centroids, packing caches selected pad positions per footprint. Direct
`PlacementIndex(document)` deep-copies the declaration, then builds a fresh
exact-type JSON snapshot owned by the index. Before building caches it validates
role bindings, disjoint nets and canonical UUIDs in that snapshot
(AGENTEE-341/352/356).
Validation covers values produced by copying; subclasses left by copying and
cyclic containers refuse with `locality document must contain only JSON values`.
References retained by a copy hook cannot change the owned snapshot after validation.
Direct `validate()` and `grade()` apply the same exact-type check, including
object keys, before schema and net-disjointness checks. Both check a fresh
snapshot; `grade()` measures and reports only its validated snapshot, so
caller edits after validation cannot change its verdict or `declaration`.
Nesting deeper than 64 levels refuses with `locality document nesting exceeds 64
levels` at all three entry points instead of `RecursionError` (AGENTEE-363), including when the index's
copy recurses on a value inserted after its pre-walk or on a deep non-JSON tuple. Binding a deeply nested
captured locality file refuses with the same error. Later caller edits cannot
change indexed values. Its public `pairs` and `endpoints` properties expose
mapping proxies, tuple pair sequences and read-only endpoint fields; callers cannot mutate the indexed declarations.
Copying does not promise an atomic view of concurrent edits: an invalid
snapshot refuses, and only the validated snapshot supplies the index.
It still requires independently qualified native geometry. Search push/pop
updates this index, including backjumps. Each rotation resolves only its incident
roles with placed peers once; candidate costs translate those exact centroids
without copying footprint geometry or scanning other footprints. Footprints with
no declared roles skip locality evaluation. The independent final and saved-board
graders still check every obligation.

Locality targets are deduplicated and capped at 256 distinct origins per rotation.
Exceeding that cap returns `packing_budget_exhausted`, `outcome=unknown`; it never
truncates constraints or searches a silently reduced target set. Below the cap,
candidate axes, exact rational costs, tie breaks and deterministic placements are
preserved. This is a fail-closed refusal, with reason `locality target budget exhausted`
and empty `placements`, rather than a geometric infeasibility proof. Raising or
tiling the cap requires qualification against a real hub-IC case.

Optional `policy.search_timeout_s` is a positive wall-time search budget of at
most 86,400 seconds.
It starts after geometry qualification and capacity proofs. Cooperative checks
run during candidate generation, empty reservation sweeps and candidate testing;
individual geometry operations and sorting are not interrupted. Set this budget
below the enclosing executor deadline, leaving time for qualification and result
serialization. An external process kill cannot guarantee a retained partial result.
Omitting it keeps deterministic step-only stopping, including joint clusters.

`pack(..., heartbeat=callback)` emits qualification/search progress, including
steps and placed counts; search heartbeats are emitted at five-second intervals
when checkpoints execute. The standalone runner prints these as flushed JSON
`placement_heartbeat` events on stderr; `board_build` stdout remains one final
JSON summary that can be parsed as a single document. Its ordinary result file
retains a normal typed failure after a cooperative timeout.

Search, step, target and wall-time exhaustion retain `search.termination` as
`search_exhausted` or `budget_exhausted`. The normal `placements` field remains
empty and the outcome remains unknown. `search.partial` retains the deepest
visited placement under authority
`diagnostic-only-unvalidated-partial-placement`; it cannot be adopted or passed
to the board writer as a complete plan. Already established infeasibility proofs
and unknown geometry verdicts retain their existing meaning.

`tests.test_electrical_locality.LocalityIndexTests` compares indexed and original
costs/targets with rotations, fractional copper centroids, UUID-selected repeated
numbers and backtracking. A committed seed-9332 differential exercises 300 generated
cases with random LIFO push/pop states, rotations, fractional copper centroids and
output loops with distinct supply and return sources, comparing exact constraints
and target sets against the legacy functions. Its 331-footprint, 15-obligation index
benchmark limits setup to 34 pad resolutions, checks 5,000 two-pair candidate
evaluations and forbids per-candidate pad resolution. A second full packing benchmark
has 331 footprints, 15 obligations and one constrained movable peer among 330 placed
parts, with fewer than 400 pad resolutions, at most 3,000 locality evaluations and
16 exact candidate tests across qualification, search and final grading. Generous
120-second and 180-second safety ceilings supplement these deterministic bounds.
These isolate locality scaling; courtyard qualification,
capacity proofs, broad backtracking and native saved-board gates still have
independent costs.

## Joint locality clusters (AGENTEE-335)

Packing first runs the unchanged per-part search. Every previously planned
input keeps exactly its placements and counted steps. Only a failure with a
multi-member locality component can trigger joint search. Existing determinate
geometry/capacity proofs retain their verdicts and do not trigger it.

In that second search, connected locality components with at least three
footprints are packed as a unit. The highest-degree footprint is the hub
(ties use the existing queue order and reference); a movable hub goes first. Other movable endpoints follow
in ascending order of their tightest incident pad-distance limit, with the
existing queue order and reference breaking ties. Fixed endpoints stay fixed.
Supply, return and switch roles all contribute, so a shared inductor stays with
its IC and capacitors. Two-footprint components and unguided placements retain
their existing ordering, candidates and verdicts.

Each unit searches joint transforms around its placed members. If a later
member cannot fit, search revisits every earlier member's alternatives,
including nonlead members. Necessary axis projections prune impossible sourced
pad distances before candidate ranking; advisory bounds remain soft. The exact
predicates and independent complete-plan grade enforce hard locality limits. Origin
group distances, fixed geometry, gaps and other placement constraints remain
enforced. A backjump moves the whole cluster without splitting its membership.

Each joint cluster has a cumulative guard of 4,096 tested candidates across
backjumps and a maximum of 64 movable members to bound recursion. There is no
default wall-clock cluster guard. These limits describe search coverage, not
physical infeasibility. Finite joint exhaustion also does not establish that a
continuous placement is impossible.

Both attempts share `policy.step_budget`: joint search receives only the
candidates remaining after the legacy attempt. If none remain, it is recorded
as `not_attempted`. A joint completion reports the sum of both attempts in
`steps_used` and records `search.legacy_attempt`. The original policy remains
bound to the plan. The joint attempt uses the legacy search's absolute opt-in
`search_timeout_s` deadline, including time spent requalifying the second
attempt; the deadline is never restarted. Qualification precedes the initial
search deadline. Individual geometry calls and sorts are cooperative and are
not interrupted. Heartbeats may read the clock for progress only; they never
change a step-only verdict.

Only an independently regraded joint completion replaces the legacy result.
Otherwise the original typed failure, steps, partial diagnostics, and empty
`placements` remain authoritative. `search.joint_cluster_attempt` records the
second attempt's status, steps, termination and any `blocking_cluster` naming
references and obligations. A cluster guard or finite refusal appears there as
`locality_cluster_exhausted`, never as a feasibility proof. Cluster membership
and tightest-first order are available in `search.locality_clusters` even when
legacy search succeeds; this describes the optional strategy, not a claim that
it ran. Partials have diagnostic-only authority and cannot enter a board writer.

`tests.test_placer_clusters` covers the joint engine's tightest-first order,
nonlead backtracking, switch peers, movable hubs, exact regrading, cumulative
step guards and cleanup. Public contract tests cover unchanged legacy plans,
shared remaining steps and absolute deadlines. A seeded differential freezes
16 original multi-member cases from the pre-cluster placer, including movable
hubs, and checks every legacy plan's verdict, placements and steps. AGENTEE-347
adds a 22-case deterministic differential against the retained per-part engine:
legacy plans and failures keep whole-result equality (apart from
`search.joint_cluster_attempt` on failures; legacy `locality_clusters` stays equal),
repeated runs are identical, total steps stay within budget, and seed 2 case 55
completes jointly and passes an independent locality regrade. Three redundant
slow refusal cases are omitted. The cluster-guard regression compares the whole
legacy result, including steps and
search records. Fault injection into the search predicate makes complete proposals
fail the real locality grade on both internal paths and public `pack()`,
requiring `judge_rejected` with empty placements. The public joint-attempt
diagnostic must also report `judge_rejected`. Refusal bounds use candidate counts,
not wall time. The locality-index suite adds exact limit and placed-state scan
bounds for both paths; a joint evaluation bound catches removal of projection
pruning.

The real pod remains diagnostic. Fail-fast completion of that run is outside
AGENTEE-335 after the legacy-first ruling; block-level placement (AGENTEE-339/340)
is its path. No refusal publishes a board or establishes physical impossibility.

## Qualification and candidate cost (AGENTEE-332, AGENTEE-353)

The real pod's guided search spent most of its wall time outside locality:
courtyard qualification self-intersection tests over 1,900 native pad copper
polygons (up to 96 vertices each), a deep copy of the whole footprint record for
every tested candidate, and an exact wire cost for every generated candidate
position (about 33,000 per scan) while only a handful per scan are ever tested.
Each change below keeps every answer; none relaxes a predicate or changes a rank.

- `simple_polygon` tests edge pairs through a sweep over edge bounding boxes
  sorted by least x. Two segments cross or touch only where their closed boxes
  overlap, so the exact `intersects` test runs on exactly those pairs.
- `transform` copies only the containers it rewrites (courtyards, keepouts,
  packing geometry entries, pads, a proven custom geometry) and shares the
  leaves it leaves unchanged; the search, judge, graders and writer only read
  placed footprints. The search keeps one at-origin transform per part and
  rotation and translates it per candidate (`_translate`), which equals the full
  transform exactly: a rotated point plus the position, and a translated box is
  the box of the translated corners.
- `_fits` runs the edge-margin distance only against outline edges whose box is
  nearer than the margin to the courtyard box; every other edge is at least the
  margin away from every courtyard point.
- Without an overhang band, `_fits` decides containment in a convex
  counter-clockwise outline by the vertex test (every courtyard vertex on or
  left of every outline edge), which equals the area test for closed polygons;
  concave outlines and bands keep the exact clipping.
- Hole/copper reservations carry union boxes, so `_hole_conflict` skips a pair
  of parts whose inventories cannot meet, and keepout tests run only against
  placed parts that own a keepout (an empty keepout list never blocks).
- `_overlaps` answers "positive shared area" exactly: boxes that do not
  overlap share none; two convex counter-clockwise polygons share positive area
  exactly when neither has an edge with every vertex of the other on or right
  of it (integer cross products, no clipping); every other pair keeps the exact
  rational clipping. Hole reservations and keepouts use it.
- Candidates enter the ranking with a lower bound on their wire cost (for each
  signal pad, the distance to the nearest peer x coordinate plus the distance
  to the nearest peer y coordinate on its net, which separates by axis and is
  evaluated once per axis value by bisection). `_ranked` resolves the exact
  wire cost only when an entry may be next: a popped exact key precedes every
  remaining entry, whose exact key is at least its own bound, and candidate
  x, y and rotation make keys unique. The sequence equals sorting every exact
  key, so steps, tie breaks and the chosen candidate are unchanged. Every exact
  resolution first calls the search checkpoint, so the deadline and heartbeat
  are checked before each exact wire cost, as when generation computed them.
- Hole exclusion skips a peer whose target union box misses the hole union box.
  Qualification therefore requires a native target inventory on every
  footprint whenever any footprint declares a nonempty hole inventory, and
  refuses otherwise as `unsupported_geometry` / `unknown` ("native packing
  target reservations missing"), drilled pads or not. The reservation caches
  are rebuilt from qualified inventories; input-supplied `_packing_*` keys are
  discarded, never trusted.

`tests.test_placer_geometry_cost` also holds a pod-scale qualification benchmark
(331 footprints, 1,986 native-style 96-gons) bounded by pair-test counts, with a
safety ceiling; `AGENT_EE_SKIP_BENCHMARKS=1` skips it only outside strict runs
(`AGENT_EE_REQUIRE_TOOLCHAIN=1` always runs it). It holds the proof: seeded differentials of the
transform, translation, sweep and margin prefilter against the previous
formulations, the lazy order against full sorting, and 40 seeded owned boards
whose plan digests (status, steps, search record and placements) are pinned
from the ac662ff packer. Their authored numeric locality requirements carry
fixture-author source records so provenance enforcement preserves the pinned
plans. An advisory-only joint-cluster differential also compares lazy ranking
with full exact sorting, including exceeded bounds without hard pruning.
A direct scan regression isolates advisory axis pruning and movable-peer
conflicts with opposite-side courtyards.
On the pod inputs, a 20,000-step guided budget, the
full 200,000-step guided run, the 10-obligation run and the unguided run
produce identical results before and after (see the AGENTEE-332 performance
report for the measured times).

# Pre-route placement and congestion report (AGENTEE-57)

`placement_congestion` is an advisory coordinator check. It runs before
routing. It reports typed placement issues per component and congested board
regions with an estimated routing-failure probability. Each item names nearby
parts, their free space in each direction, and a concrete suggested move such as
`move C1 1.9 mm right` or `rotate R1 180°`. The report goes into the sealed
check result. A placement-stage result reaches the placement writer through the
[placement campaign adapter](placement-adapter.md) context.

The check never passes a board. With no proved defect its outcome is `info`.
`info` has no acceptance predicate and does not show the board is routable.
Routing acceptance stays with the router and native DRC.

## Declaration

```json
{"id": "congestion", "kind": "placement_congestion", "stage": "placement",
 "params": {"clearance_mm": 0.2, "track_width_mm": 0.25, "cell_mm": 2.54,
            "routing_layers": ["F.Cu", "B.Cu"],
            "max_failure_probability": 0.05, "min_rotation_gain_mm": 1}}
```

| Parameter | Meaning |
| --- | --- |
| `clearance_mm` | Required. Copper clearance between pads of different nets. Moves clear by at least this much. |
| `track_width_mm` | Required. With `clearance_mm` this gives the track pitch used for cell capacity. |
| `cell_mm` | Required. Congestion grid cell size. It must hold at least four track pitches; below that, pad blockage is all-or-nothing per cell. A cell of several pitches, such as 2.54 mm for 0.45 mm pitch, is a reasonable start. |
| `routing_layers` | Required. Distinct copper layer names (`F.Cu`, `B.Cu`, `In<n>.Cu`) available for routing. |
| `max_failure_probability` | Optional, strictly between 0 and 1. A region with a higher probability becomes a `routing_congestion` defect, which blocks a later routing stage. Without it, congestion is reported only. |
| `min_rotation_gain_mm` | Optional. Enables `ratsnest_rotation` suggestions whose Manhattan MST gain reaches this value. |

These are declarations of the analysis method. No value is inferred from part
references or values. A requirement that maps this check must be
`informational`. The capability has no predicate, so an `acceptance` mapping is
refused with `no executable bound`.

## Defects in a full review

In a full `review`, a congestion defect becomes the finding `check/<id>`. The
adjudicator accepts it by citing `checks/<id>.json` (AGENTEE-217). It needs no
requirement witness because none exists. The coordinator checks the baseline
coverage: the check must have no acceptance predicate and no acceptance mapping.
The sealed result must be unchanged, executed and a defect, and its tool
evidence must be unchanged. The accepted finding and its decision event are
marked `"authority": "advisory"`.

The finding is never acceptance evidence. `validate_witnesses` still refuses it
for every requirement. A defect check outcome keeps the review at
`defects_or_unresolved`. Like any check defect, it cannot be noted, deferred or
dismissed. An acceptance check defect cannot be marked a duplicate of an
advisory finding, because the advisory finding would then stand in for a
requirement failure. The reverse duplicate is allowed. The accepted finding
reaches the repair writer as context, like other accepted check findings.

## Execution

The only tool execution is the read-only saved-board
[placement probe](placement.md) in the networkless sandbox:
`/usr/bin/python3 -B -m agent_ee.placement_probe BOARD --out /work/placement-probe.json`
(600 s). The coordinator then runs `placement_congestion.analyze` on the captured
probe JSON. That is a pure, offline computation with no new dependency. The
netlist is the saved board's pad-net assignment, which is the forward-annotated
netlist. No schematic export runs. Evidence is the probe and
`work/<id>/congestion.json` (schema `agent-ee.placement-congestion/1`).
`details` carries the ranked issues, the unknown items and the regions without
their cell lists.

A probe that exits non-zero, writes nothing, writes invalid JSON or does not bind
the captured board hash is unknown coverage (`failed` or `malformed`, outcome
`unknown`). It is never an empty clean report. Offline,
`python3 -m agent_ee.placement_congestion PROBE --params JSON --out OUT` writes a
`status: unknown` record and exits 2 for a missing or unusable probe.

## Issues

Issues are ordered deterministically. The order is issue kind (as listed below),
then magnitude, largest first, then references. Each issue has a 1-based `rank`.
Every issue also lists `parts`: the involved references, each with `locked` and
`free_space_mm`. It lists `nearby`: other parts within one cell of the involved
parts, ordered by gap and then reference. It also carries `suggested_move`, or
`suggestion_unavailable` with the reason.

| Kind | Class | Meaning |
| --- | --- | --- |
| `pad_overlap` | defect | Copper pads of two footprints on a shared copper layer have proved negative clearance. `clearance_mm` is the worst proved clearance. `pad_pairs` lists pads, nets, layers and `clearance_bounds_mm`. |
| `courtyard_overlap` | defect | Positive-area same-side courtyard intersection from the exact [AGENTEE-53 judge](placement.md). |
| `outside_outline` | defect | Positive courtyard area outside the proven outline (same judge). |
| `routing_congestion` | defect | Region probability above a declared `max_failure_probability`. |
| `pad_clearance` | advisory | Proved clearance between different nets below `clearance_mm` but not negative. Same-net contact is not a clearance issue. |
| `ratsnest_rotation` | advisory | A verified quarter turn of an unlocked part shortens the pad-centre Manhattan MST of its nets by at least `min_rotation_gain_mm`. |

A defect outcome carries `details.gate_findings`. Each finding is keyed by the
`+`-joined references (or the region id) and the kind. Its `suggested_fix` is
`{"op": "move", "params": {"reference", "x_mm", "y_mm", "dx_mm", "dy_mm"}}`
or `{"op": "rotate", "params": {"reference", "rotation_deg"}}`. Fixes are
advisory data only and are never applied by the coordinator.

The taxonomy uses agent-ee's own placement probe and predicate vocabulary.
`pad_overlap` and `pad_clearance` follow the `courtyard_overlap` naming.

### Pad clearance

Pad copper is modelled exactly for circles, rectangles and ovals, using the
saved position, size and absolute rotation. Clearance is the signed distance
between convex cores widened by a radius, computed from their Minkowski
difference; a negative value is the penetration depth. The probe does not
capture the corner ratio of a rounded rectangle, so the pad is bounded on both
sides: it lies inside its rectangle and contains the stadium of the largest
corner radius. A pair is reported only when the bound decides it. When the
bounds straddle the requirement, the pair is listed in `unknown` as
`pad_clearance_unresolved`. Chamfered rectangles have only the outer bound.
Trapezoid and custom pads are `pad_shape_unsupported`. NPTH pads carry no
copper here; hole clearances remain native DRC's.

### Moves

A suggested move is the smallest axis translation (`up`, `down`, `left`,
`right`; KiCad board y grows downward). It must clear, at the same time:

- every modelled copper pad pair, to `clearance_mm` between different nets, or
  to contact on the same net
- every same-side courtyard
- the outline

It is checked on the convex hulls of the courtyards. After rounding up to
whole micrometres, it is verified against the exact rational courtyard and
outline geometry and the modelled pads. The cheapest movable involved part
moves. Ties go to fewer pads, then reference order. Locked parts are never
moved. Any unproved courtyard, unproved outline or unmodelled pad leaves the
move unavailable, with the reason. A rotation suggestion is checked the same
way after an exact quarter turn about the footprint origin. Its pad transform
matches pcbnew for 90, 180 and 270 degrees.

### Free space

`free_space_mm` is how far the part's courtyard can move in each direction
before it touches another same-side courtyard or leaves the outline. Convex
hulls make it conservative for concave courtyards. Courtyards that already
overlap are excluded, because they are reported as issues. It is
`{"status": "unknown", "reason": ...}` when the part's courtyard, the outline,
or any same-side courtyard is unproved.

## Congestion regions

The outline's bounding box is gridded into `cell_mm` cells. Each cell is
weighted by its exact area inside the outline:

- **Capacity.** Each routing layer contributes its free area divided by the
  track pitch. Pad outer boxes on that layer block area. The total, in tracks
  through the cell, is rounded down to an integer.
- **Demand.** Demand comes from a rectangular uniform wire density (RUDY)
  spread of each net's pad-centre Manhattan MST edge (the
  [`ratsnest-mst/1`](placement.md#placement-check-kind) proxy). A box narrower
  than one pitch is widened to one pitch. Its length spreads over the unblocked
  area of the box, because routes pass around pads.
- **Failure probability.** A cell's probability is P(Poisson(demand) >
  capacity). A region is a 4-connected set of cells at or above 1 %, and its
  probability is 1 − ∏(1 − p) over its cells.

Regions are ordered by probability, highest first, then by position. A region
lists its cells, bounding box, demand and capacity. Its nearby parts are those
touching the box widened by one cell, ordered by distance from the region
centre and then by reference. Each nearby part has its free space. A region
also has a heuristic relief move: the nearest movable part moves away from the
centre, within its free space. The move is verified against courtyards and pads
and gives `region_probability_after`.

Severity bands are not stored in any record. `severity(probability)` derives
them on every read: `high` at 5 % or more, `medium` at 2 % or more, `low` at
1 % or more. `method.severity_bands` records the thresholds. Demand with no
unblocked area in its box is reported as `unplaced_demand`. With no netted pads,
demand is unknown (`no_net_terminals`). An unproved outline leaves congestion
unevaluated (`congestion: null`).

The probability is an uncalibrated estimate. It has not been compared with
routed outcomes, and the router remains the authority.

## Validation

`tests.test_placement_congestion` covers:

- signed-distance oracles (circles, rotated squares, ovals) and the
  rounded-rectangle bounds
- a randomized comparison of the analytic blocked interval with direct
  evaluation
- pad overlap with negative clearance and a minimal verified move
- byte-identical output for repeated and shuffled input
- regions ordered by probability, with nearby parts in distance order and
  computed bands
- the declared maximum, rotations, locked parts, parameter refusal and the
  advisory-only capability
- unknown results for missing, invalid and unproved inputs

`tests.test_placement_congestion_native` uses pcbnew-saved RC variants through
`checks.run_checks`:

- C1 moved over R1 gives a `pad_overlap` of −0.65 mm alongside native
  `courtyards_overlap`. Applying the suggested move, saved and reloaded in a
  fresh process with pad layers unchanged, clears native DRC's courtyard item.
- Two captures of the same board give byte-identical `congestion.json`.
- The clean board is `info`, not `pass`.
- A corrupt board makes the probe fail, and the outcome is unknown.
- Quarter-turn pad transforms match pcbnew, and the suggested `rotate R1 180°`
  restores the control pads.
- A full `run_record.run_review` with passing ERC as the only acceptance check, the
  overlap as the only defect and scripted agent transport completes. The
  adjudicated advisory finding fails the review (`defects_or_unresolved`) and
  is refused as a requirement witness.

`tests.test_baseline.AdvisoryDefectTests` covers the adjudication refusals: an
uncited, changed or non-failed result, deferral, noting, dismissal, and an
acceptance defect marked a duplicate of an advisory finding.

## Sources

The routing-difficulty mechanism is used as a reference only: per-region
probability of failure, 5/2/1 % severity bands, deterministic tie-break and free
space per direction near components. It comes from tscircuit
circuit-json-routing-analysis (`lib/analyzeRoutingNext.ts`, MIT), and no code
was ported. That project relies on the capacity-autorouter node solver, while
this check uses the RUDY and Poisson model above. tscircuit
circuit-json-placement-analysis has no licence. None of its code, type names or
text was used, and the issue kinds here come from agent-ee's own placement
vocabulary. RUDY is the published rectangular uniform wire density estimate
(Spindler and Johannes, 2007).

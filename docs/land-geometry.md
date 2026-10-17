# Land-pattern geometry kernel

`agent_ee.land_geometry` computes geometry from already registered physical-pad
records using only the Python standard library. It does not parse supplier or
KiCad files, select registration, authenticate a binding, run native tools or
create check evidence. The check adapter owns those steps and their coverage.
The [security boundary](security.md#evidence-trust) remains unchanged.

## Input contract

A pattern has exactly `pads` and `holes`, both lists. Every physical pad is
retained, including pads sharing a logical number or centre. Each pad has exactly
`number` (nonempty literal string), `layers` (nonempty list of distinct copper
layer strings) and `primitive`. Holes are primitive records, including NPTH.
The adapter must qualify layer names and through-aperture applicability. Every
hole is subtracted from land copper on every represented layer; blind/buried or
layer-dependent apertures require another contract and must be refused upstream.
Pads describe gross land copper, excluding traces, zones and thermal reliefs.

```json
{
  "pads": [
    {
      "number": "1",
      "layers": ["F.Cu", "B.Cu"],
      "primitive": {
        "shape": "roundrect",
        "center": ["0", "0"],
        "size": ["2000000", "1000000"],
        "angle": "90",
        "radius": "200000"
      }
    }
  ],
  "holes": [
    {"shape": "oval", "center": ["100000", "0"], "size": ["600000", "400000"]}
  ]
}
```

Coordinates and dimensions use one common nanometre coordinate frame. Values
are integers or finite decimal strings; floats, booleans, scientific notation,
NaN and infinity are refused. Dimensions must be positive. `shape` is `rect`,
`circle`, `oval` (capsule), or `roundrect`. Circles need equal dimensions.
Roundrect requires `radius` in `[0, min(size)/2]`; other shapes forbid that field.
`angle` defaults to zero and is in degrees. Only exact multiples of 90 qualify.
The adapter must supply the already registered centre and dimensions; the kernel
never rotates about an inferred datum, searches mirror/rotation choices or scales
a pattern to fit. General decimal angles return `unsupported_rotation` without
rounding. A registration with unresolved orientation remains an adapter-level
`pin_mapping_unavailable`, regardless of the supplied geometry's IoU.

Unexpected fields are malformed rather than silently ignored. Source locators,
UUIDs, plating, provenance and native/supplier hashes belong in the adapter's
parallel records, with physical list indices preserved. No input is mutated.

## Public functions

- `polygon_area(polygons, subtract=(), limits=None)` returns the exact `Fraction`
  area of the polygon union minus the subtracting polygon union.
- `polygon_intersection_area(left, right, limits=None)` returns the exact
  `Fraction` area of the intersection of two polygon unions.
- `area_bounds(pattern, refinement=4, limits=None)` returns certified bounds on
  net copper area per layer and the union of all drilled openings.
- `match_pins(left, right, tolerance="0", aliases=None, limits=None)` computes
  pin agreement under fixed registration and a Euclidean centre-distance bound.
- `compare_patterns(left, right, threshold="0.98", pin_tolerance="0",
  levels=(2,4,8,16), limits=None)` returns per-layer copper IoU, hole IoU, pin
  agreement and a diagnostic mode. Its pin comparison uses literal numbers;
  adapters with captured aliases call `match_pins` explicitly as well.

All parameters after the pattern/polygon operands are keyword-only. Polygon
inputs are sequences of simple polygons, each with at least three distinct
vertices, without a repeated closing vertex. They may be concave and use exact
`Fraction` coordinates in addition to the numeric format above. Self-crossings,
zero area and reversed collinear edges are malformed. Touching boundaries and
collinear edges between different polygons are supported. Union/difference
naturally represent disconnected regions and holes without adding bridge edges.

`area_bounds` returns `algorithm`, `refinement`, `copper` (layer-to-bound mapping)
and `holes`. Every numeric endpoint is an exact JSON-compatible rational record:
`{"numerator": integer, "denominator": positive_integer}`. A bound has `lower`
and `upper`. IoU metrics additionally have `decision`: `pass` only when
`lower > threshold`, `defect` only when `upper <= threshold`, otherwise `unknown`.
Both empty hole sets have `decision: not_applicable` with null endpoints. One
certifiably empty and one certifiably nonempty set have IoU zero. Both empty
copper sets on a represented layer, or no conductive pads, are refused. If
emptiness cannot be certified, uncertainty remains unknown.

The comparison's top-level mode is `pin_conflict` first, then `low_accuracy` for
any proved area failure, then `numeric_uncertainty` for unresolved numeric
bounds, otherwise the pin mode. Consumers must retain all metrics and the pin
result: a proved local failure does not erase another predicate's unknown
coverage. These diagnostics do not assign evidence execution/outcome authority.

## Exact polygon areas and bounded curves

The vertical sweep splits x at every vertex and exact segment intersection.
In each open slab, segment ordering is constant. Crossing a polygon boundary
toggles its interior; the Boolean predicate selects y bands between boundaries.
Their endpoints are linear, so the trapezoid integral is an exact rational area.
Coincident boundaries toggle together. Pads and holes are unioned before area
measurement, preventing duplicate or overlapping pads from double counting.

For curved primitives the kernel samples the rational unit-circle parameter
`((1-t*t)/(1+t*t), 2*t/(1+t*t))` at `t=j/n` in each quarter. Chords form an inner
polygon. Adjacent exact tangents intersect to form an outer polygon. These
vertices are rational; neither trigonometric floats nor a bounding box replace
the curved boundary. A rounded rectangle is the Minkowski sum of its rectangular
core and a disk; an oval is the same construction with a line-segment core.
Convex hulls of translated circle-envelope vertices bound those sums.

Every curved result is an enclosure of the original curved set. A finite polygon
is never reported as the exact area of a circle. Rectangle geometry is exact
under supported transforms. For land bounds `Li <= L <= Lo` and hole bounds
`Hi <= H <= Ho`, net copper is bounded by `Li minus Ho` and `Lo minus Hi`.
A union of these bounds encloses the complete copper set per layer. Intersection
and union bounds for operands A and B give IoU lower `I_low/U_high` and upper
`min(1,I_high/U_low)`; zero denominators retain uncertainty unless emptiness is
proved. Structurally identical set representations can establish IoU one when
positive inner area proves they are nonempty, including identical curves.

Refinement levels must strictly increase by integer multiples, so every chord
set includes the previous one and tangent halfplanes only accumulate. Inner
bounds increase and outer bounds decrease. An unresolved threshold after the
last level is `numeric_uncertainty`; exhausting work raises an explicit refusal
rather than silently decreasing resolution.

## Pin correspondence

Layer sets and registered centres determine a bipartite admissibility graph,
independently of pin labels. Exact squared distance is compared to squared
`tolerance`. The kernel tests perfect matchings and checks which edges can
participate in any perfect matching; there is no nearest-neighbour tie break.
No correspondence or unequal physical pad counts yield
`pin_mapping_unavailable`. If no admissible bijection preserves pin labels, the
result is `pin_conflict`, even at copper IoU one. Mixed agreeing and disagreeing
bijections are unknown. Ambiguity is acceptable only when every admissible
bijection agrees on pin identity. `pairs` contains index pairs only when the
correspondence is unique; it is empty for ambiguous correspondence, including a
proved conflict. Repeated logical numbers retain all physical geometry.

An optional explicit alias dictionary maps left numbers to right numbers.
Noninjective aliases, including collisions with implicit identity of present
left numbers, return `alias_conflict`. No numeric coercion, prefix stripping or
package-name heuristic is applied. An incomplete alias map uses literal identity
for its remaining numbers.

## Refusal and resource contract

`GeometryError(ValueError)` exposes `execution` and `mode`. Invalid records use
`execution: malformed`, `mode: malformed_geometry`. Unsupported records or
computations use `execution: unsupported` and `unsupported_shape`,
`unsupported_rotation`, `empty_copper`, or `work_limit`. Exceptions propagate;
there is no fallback geometry or successful partial result.

`Limits` is a frozen dataclass with positive integer fields:

| Field | Default | Scope |
| --- | --- | --- |
| `max_segments` | 2048 | Each polygon collection/arrangement, refinement vertex count and per-pad layer inventory |
| `max_pair_checks` | 5000000 | Accumulated segment-pair and sweep segment visits |
| `max_slabs` | 20000 | Accumulated sweep slabs |
| `max_match_states` | 100000 | Accumulated graph search/edge visits |
| `max_pads` | 128 | Physical pads plus holes in each pattern |
| `max_numeric_bits` | 4096 | Input and retained rational numerator/denominator sizes |

Counters persist across all operations and refinements of one public call.
`levels` has at most 32 entries. Limits and refinement parameters are part of the
adapter's captured algorithm policy. Computational limits do not certify wall
clock or whole-process memory limits; the caller still uses the ordinary
sandbox and deadline. Unexpected implementation errors propagate to its normal
failed-execution handling.

## Qualification and remaining work

The owned [mathematical controls](../tests/test_land_geometry.py) exercise exact
slanted/concave polygon areas, shared edges, overlapping pads/apertures,
independent circle/capsule/roundrect/annulus areas and a circle lens against a
50-digit analytic enclosure. They check monotonic refinement, strict thresholds,
quarter turns, deterministic outputs, pin swaps at unit IoU, ambiguous and
non-greedy correspondence, malformed inputs, empty copper and work exhaustion.

This kernel does not complete AGENTEE-31 alone. Arbitrary rotations remain
unsupported. Native saved/reloaded front/back, radius, drill/slot/offset controls,
EasyEDA identity/shape coverage, private C11702 versus pinned 0402/0603 observations,
public witness verification and coordinator registration are separate adapter and
integration obligations. No third-party design data is included here. Passing
these mathematical controls is neither owner acceptance nor native design
qualification.

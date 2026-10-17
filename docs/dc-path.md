# PCB DC path resistance

The optional `dc_path` check extracts saved KiCad 10.0.7 copper geometry and
solves a bounded, ideal one-dimensional resistor network. It reports two-terminal
DC voltage drop and total copper loss at explicitly declared fixed currents.
There are no material, thickness, temperature, current or tolerance defaults.
The [owned analytic expectations](../fixtures/dc-path/EXPECTATIONS.md) and
[native tests](../tests/test_dc_path.py) define the qualified domain.

## Captured inputs

A check has exactly these parameters:

```json
{
  "id": "dc",
  "kind": "dc_path",
  "params": {
    "contract": "dc-contract.json",
    "qualification": "dc-qualification.json"
  }
}
```

Both files must be captured inputs. Scope/2 and scope/3 preserve them as immutable
baseline dependencies. The board stays a mutable design input; geometry changes
require a new extraction and new candidate evidence. Scope/3 reads the captured
contract before inference and validates the actual predicates and dependencies.
That audit cannot establish runtime geometry support.

The shipped scopes capture an empty native schematic because the scope contract
requires that field; the DC check makes no schematic electrical claim.

The [example contract](../fixtures/dc-path/contract.json) explicitly captures:

- The net, F.Cu layer and copper thickness in meters.
- Resistivity in ohm meters, reference temperature, linear temperature coefficient
  and the valid external temperature interval. The initial range is 20..80 C.
- Unique native footprint reference/pad identities and the ideal contact model.
- Width and thickness factor intervals containing 1, within [.5, 1.5]. Length
  factors must be explicitly [1, 1]; uncertainty moving junctions is unsupported.
- One to eight positive fixed-current loads, each with voltage-drop and total-loss
  upper limits. Conditions include the complete contract identity and load.

Run an owned fixture with `agent-ee review fixtures/dc-path/straight.scope.json
--out build/dc-control --checks-only`. As with other checks-only reviews, the
review command exits 1 because agent review is incomplete; inspect the check
outcome and run `agent-ee verify build/dc-control` for evidence integrity.
The other five scopes use the same immutable declared material and limits.

Qualification pins the native Python interpreter, pcbnew wrapper and native
extension, extraction script, model policy and numerical thresholds. Tool hashes
are checked before/after extraction and included in pre-inference expectations.
The native probe runs in the existing networkless tool sandbox. Changing a pin
requires semantic review and native analytic requalification, not a hash refresh
alone. The shipped qualification targets the repository's pinned native image.

## Geometry and physics limits

All board tracks must be on the declared net and F.Cu layer. All conductive
branches are retained. An accepted network must reduce completely through
series and parallel operations between the two declared terminals. The handler
refuses dangling branches, disconnected islands and non-series-parallel graphs.
It never selects a shortest path or treats a component as a copper short.

Straight segments use integer native endpoints and unrounded native lengths.
Circular arcs retain their defining points, native center, radius and signed
sweep. Their lengths use radius times absolute sweep and must agree with the
unrounded native length within 2 nm. Supported sweeps are at most pi radians;
arcs must have radius at least ten times the largest envelope width. Arc
junctions are unsupported. Possible contacts between disjoint arc bounding boxes
are conservatively refused when the expanded boxes overlap.

Straight endpoint junctions must meet at an angle of at least 90 degrees.
Non-endpoint intersections, T contacts and width overlaps are refused, including
contacts created at the widest tolerance endpoint. Every conductor must be at
least ten times its largest envelope width. Exactly two round, centered SMD
pads on F.Cu are allowed; each diameter must fit inside the narrowest attached
trace. Their native centers define ideal terminal anchors. Other pads, vias,
all zones (even on unrelated nets), other net/layer tracks and copper graphics
are unsupported. This deliberately conservative isolation policy does not claim
arbitrary-board extraction. Pad/junction spreading and connector/component
resistance are excluded idealizations, not measured copper truth.

For every edge, `R = rho(T) L / (width thickness)`. The external temperature is
an input, with positive linear corrected resistivity throughout the declared
range. No self-heating or thermal equilibrium is inferred. A standard-library
conductance solver uses exact rational elimination on the derived edge resistances
and converts its solution to floating point for reporting. It uses every edge and retains its matrix, node voltages, edge
currents, KCL and energy residuals. Graphs are capped at 128 edges and 32 nodes;
resistance ratios above 1e6, small pivots, nonfinite results or relative residuals
above 1e-9 refuse coverage.

Nominal, lower-resistance and upper-resistance cases are retained separately.
For a fixed topology of positive resistances, two-terminal effective resistance
is monotone in each edge resistance. Width/thickness endpoints therefore bound
fixed-current drop and total loss; a 1e-9 relative numerical allowance widens the
reported intervals. This does not bound arbitrary branch power or multi-source
loads. Exact zero dimensional tolerances reproduce the nominal solution, with
the same small numerical allowance. Empirical residual transfer is unsupported.

## Evidence and qualification

`geometry.json` retains the native inventory. `network.json` records all edge
geometry, terminal resolution and coverage counters. `analysis.json` records the
captured contract and qualification, board identity, all dimensional cases,
resistances, matrices, solutions, residuals and measurement extrema witnesses.
Each completed load solution is written before the next load or case starts.
They use the existing coordinator evidence seal and requirement witness path.
Any unsupported extraction or incomplete case leaves outcome unknown; only a
complete supported limit violation creates the normal coordinator defect.

The original straight control is 10 mm by .25 mm by 35 um, with declared
rho=1.724e-8 ohm m at 20 C. Its analytic resistance is .019702857142857143 ohm.
At 1 A it meets the .03 V limit; the owned .125 mm narrowed and 20 mm lengthened
boards each violate it. Series, parallel and radius/sweep arc expectations are
independent of the solver and extracted lengths. Scripted initial reviewers and
adjudication test coordinator authority and sealing; they do not establish
live-model reliability or physical validation. Owner review remains separate.

Adding a handler changes `checks.py`, which is pinned by the shipped
`passive_corners` qualification. The integrator must review that change,
requalify those fixture pins, and rerun their native analytical oracle. This
branch intentionally does not silently update those existing hashes.

# PCB DC path resistance

The optional `dc_path` check extracts saved KiCad 10.0.7 copper geometry. Contract
version 1 solves an isolated, ideal one-dimensional resistor network; version 2
bounds voltage drop on complete multilayer nets with distributed loads. Both
models require explicitly declared physical parameters and ideal contacts.
There are no material, thickness, temperature, current or tolerance defaults.
The [owned analytic expectations](../fixtures/dc-path/EXPECTATIONS.md) and
[native tests](../tests/test_dc_path.py) define the qualified domain.

## Multilayer net bounds

Use [`agent-ee.dc-path/2`](../fixtures/dc-rail/contract.json) for straight/arc
tracks, through vias, SMD pads and circular plated holes on a native board with
other nets. The public check kind and parameters remain `dc_path`, `contract`
and `qualification`. The contract explicitly names an earlier **full** `drc`
check, source/sink reference and pad identities, net names, and a maximum **total
positive current injection into each net**, including all loads and sources.
Using only one load's current when other loads share that net is invalid.

Every enabled copper layer needs a minimum thickness. Declare a minimum track
width factor, maximum board height, minimum barrel plating, minimum hole diameter
factor, resistivity upper bound at the stated temperature, and drop limits.
No default material, dimension, load or tolerance is inferred from the board.
Each bound sums every relevant track and barrel resistance, including branches
that the selected load may not use. The circular barrel uses the hole lower
bound as its outer diameter and full board height, deliberately overestimating
its resistance. A `1e-12` relative arithmetic allowance is added.

In the connected passive resistor model, a branch cannot carry more than the
total positive injected current. Summing the nonnegative branch resistances
therefore bounds any terminal voltage difference. This avoids an optimistic
shortest-path estimate and works with distributed loads. It can be loose; a
failed upper bound means the declared model has not established the budget,
not that a measured board necessarily exceeds it. Supply and return paths must
both be included in a rail budget.

The native prerequisite must belong to the same captured board and candidate,
retain the raw report, pass full schematic parity/connectivity, and have zero
raw findings. Waived findings or advisory numeric violations cannot establish
this prerequisite. Ignored conductive checks refuse. KiCad's five default
nonconductive omissions are retained explicitly: missing courtyard, track/via
centering, tuning profiles, footprint filters and footprint component type.
Contract compilation rejects missing, forward and projected DRC prerequisites.

The report accounts for every native conductor as a modeled rail conductor,
an ideal rail pad contact, or an explicitly excluded other-net object. Ambiguous
terminals, zones, copper graphics, non-through vias, nonplated/slotted rail holes,
missing layers, malformed geometry or stale qualifications refuse. Zone support
remains a whole-system implementation gap. Version 1's stricter endpoint model
and solver are unchanged.

**These are ideal-model bounds.** Pad spreading, solder/package/contact
resistance, self-heating and AC effects are not modeled. Declared supplier
minima and current/temperature envelopes require separate substantiation before
physical acceptance. Native DRC and a passing copper bound do not supply it.

Replay the [native analytic case](../fixtures/dc-rail/requirements.md):

```sh
ae-ci-exec . -- bin/agent-ee review fixtures/dc-rail/control.scope.json \
  --out build/dc-rail-control --checks-only --max-seconds 180
ae-ci-exec . -- bin/agent-ee verify build/dc-rail-control
```

The checks-only review exits 1 because independent engineering review remains
incomplete. Its `dc` check must be `ok/pass`, at approximately 11.6373224084 mV;
`verify` must exit zero. The [native tests](../tests/test_dc_rail.py) also prove
rejection of a disconnected route, an exceeded drop budget, altered sealed
geometry, stale DRC and an ignored connectivity check. Qualification pins the
model, probe, dispatcher and native tool identities. Regenerating that file
without rerunning the native oracles does not establish qualification.

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

Policy `endpoint-planar-dc/2` requires an explicit substrate inventory. Board
and footprint Edge.Cuts together must form one closed axis-aligned outer
rectangle and zero or more strictly internal, mutually disjoint rectangular
cutouts. Each rectangle must consist of exactly four native straight segments.
Missing, open, curved, rotated, touching, overlapping, nested or ambiguous
boundaries refuse coverage; native rectangle primitives also remain unsupported.
At most 128 boundary segments are allowed. Conductor native bounding boxes,
expanded by the maximum dimensional width radius plus 2 nm, and terminal pad
envelopes must lie strictly within retained substrate and clear every cutout.
This box test deliberately refuses some physically clear paths near cutouts,
particularly arcs; it cannot grant coverage across missing substrate.
The native inventory retains every boundary UUID and supported shape geometry;
the network retains qualified rectangles and tested copper envelopes.

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
are unsupported, including footprint Reference, Value and user PCB_FIELD objects
from the native field collection (even hidden copper fields conservatively
refuse). Board drawings and footprint graphical items retain all native classes
on copper, and board/footprint zones remain globally unsupported.
This deliberately conservative isolation policy does not claim
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

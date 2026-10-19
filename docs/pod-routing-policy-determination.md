# Pod routing: initial layer policy determination (AGENTEE-257)

This page answers the pod lane's question (agent-469, 2026-10-19): is there a
source-backed initial [AGENTEE-231 `layer_policy`](routing-adapter.md#declared-layer-policy-agentee-231)
for the telemetry pod, in particular a defensible `pour_area_loss_pct`?

**Answer:** not yet. `signal_layers` and the RF `reference` are source-backed.
No numeric `pour_area_loss_pct` can be source-backed today, for two reasons:

- the bound's denominator does not exist yet, because no placed pod board exists;
- the quantity the bound has to encode, a signal-via budget, is not in any captured
  source.

This page gives the evidence-gathering recipe and its decision rule, the owned tool
that performs it ([`agent_ee.pour_budget`](../src/agent_ee/pour_budget.py)), and the
owner questions. The geometric routing guard is not an RF qualification. The
section [What stays unqualified](#what-stays-unqualified) lists what remains open
and how each item would be closed.

This is a specialist determination for routing preparation. It does not change an
owner requirement, a physical limit or a sealed input.

## Sources read

Hashes were recomputed with `sha256sum` on 2026-10-19:

| Source | sha256 |
| --- | --- |
| `pod-campaign-v1/constraint-application-adoption-draft/routing-policy-intake.json` | `a12a1a00a3c54d0a691bb0909d64da4b519e26b88f1d3aefcdbf2d7f418fb67b` |
| `pod-campaign-v1/reviewed-planes/plane-plan.json` | `391602f6cb02785f69e7e06c7992d5c0fc08a7a97a1110cc2f143dfab61b8676` |
| `pod-campaign-v1/reviewed-planes/review-225.md` | `f5d89194c5272360ab7da0b6e945b88445ff201228c118e85af3893dfd126df1` |
| `pod-generation-inputs-v3/rf-source-capture.json` | `e881b4f1466524bafa45698ed8cb9c59be946009c2388ca69cd34257f82b4822` |
| native-v5 `telemetry-pod.kicad_pcb` (the staged board the plane plan was reviewed on) | `4fe774b5751af2b7cbd7169a81356fe5d57b741dd25c12a63c1d75639a484087` |
| native-v5 `telemetry-pod.kicad_pro` | `84ec00618f295150ad6c27e9e2c84d2d713cd10794e8d58d2e07107ccc802800` |

All paths are under `/tmp/agent469-coordination/`, except native-v5, which is under
`/tmp/agent463-pod-preparation-20261018/native-v5/scaffold/inputs/project/`.
The kicad-route source was read in the pinned checkout `a132899a26ed9d7bf95c9978f0c1c992ee066f63`
(`kicad_route_attempt.PIN`), read-only.

## What kicad-route's `area_loss` measures

The descriptions below cite files in the pinned checkout. Line numbers are at
`a132899`.

- **Board states compared.** The **reference** is the routing input exactly as
  delivered to the router: a copy of it, refilled by `kicad-cli pcb drc
  --refill-zones --save-board` (`cli.py` `baseline_drc`, lines 342–360, through
  `kicad_drc`, lines 268–281). The **candidate** is the routed board, refilled and
  saved the same way before grading (`cli.py` line 795). The check is therefore a
  before-routing versus after-routing difference. For a pod routing stage that runs
  after placement and `add_planes`, the reference is the placed, planed, unrouted
  board.
- **Which pours count.** Every copper zone that is not a rule area and has a net
  counts (`kicad/pour.py` line 150). This covers every net and every layer, not
  only the planes. On each layer the zone fill is split into its polygons. Their
  areas are summed per `net@layer`, rounded to 0.0001 mm², over every zone of
  that net on that layer (`kicad/pour.py` lines 184–203 and 374–381). Islands
  cut off from the main pour still count while KiCad keeps them. An island KiCad
  removes (island removal `always`, no pad) is lost area.
- **Denominator and verdict.** `area_delta_pct = round(100 × (candidate − reference) / reference, 3)`
  per `net@layer`. It is `None` when the reference area is zero (`pour.py` line 90).
  With `--pour-area-loss PCT`, a `net@layer` fails when `−area_delta_pct > PCT`
  (`pour.py` line 96). A gain never fails. One bound applies to every `net@layer`.
  agent-ee grades `area_loss` only when the router's recorded
  `area_loss_limit_pct` equals the declared bound. It treats a `None` delta as
  unknown, or as a defect on a declared reference plane
  (`kicad_route_attempt.area_loss`).
- **Placement-time voids do not count.** Antipads and clearance cuts around the
  placed pads, thermal reliefs, footprint and board rule-area (keepout) voids, and
  the board-edge clearance are already in the reference refill. They cancel out
  of the difference: they are neither in the denominator nor counted as loss. What
  counts is what routing changes:
  - the clearance cut around new tracks and vias of other nets in the pour's
    layer;
  - fragments the refill then removes (island removal, slivers below the zone's
    minimum width);
  - changes in how new same-net copper joins the pour.
- **With `signal_layers` limited to F.Cu/B.Cu**, KRT places no track on In1/In2
  (`disallowed_tracks` grades that). KRT still uses through-vias, which pass
  through In1/In2. On the pod's inner `/GND` planes, the only router-induced loss
  is then:
  - the antipad of each through-via of a non-`/GND` net;
  - any fragment those antipads cut off and the refill removes.

  A `/GND` via (fanout or stitching) joins the plane and removes nothing. The
  owned probe measures this rather than assuming it; see below.

The `0.5` in the routing-adapter example is only an example. No captured source
states a plane-area or via-count limit for the pod.

## Why no numeric bound is source-backed today

1. **No post-placement baseline exists.** On native-v5, 104 of 112 footprints
   are staged outside the 80 × 50 mm outline (review-225 §1.3, reproduced below).
   The latest placement attempt the scratch audit found,
   `pod-placement-priority-v2/manifest.json`, is `packing_budget_exhausted` with
   0 placements. The plane fill measured on native-v5 (3741.6545 mm² per inner
   layer) is a staged-board fill. It lacks the antipads and thermal cuts of the
   104 unplaced footprints, so it is not the reference the router will divide by.
2. **The bound encodes a via count, and no source gives one.** With an outer-only
   allowlist, the loss is about the number of signal through-vias times one
   antipad. `spec.md`, the envelope, the fab profile, the RF capture and the plane
   review bound neither the via count nor the plane area. Choosing that count is an
   engineering decision ([owner question Q1](#owner-questions)).

## The decision rule

For a declared signal-via budget `N`, on the placed, planed, unrouted routing input:

```
pour_area_loss_pct = ceil_0.001( 100 × N × max over pours p and routed net classes c of a(p, c) / A(p) )
```

The terms are:

- `A(p)` is pour `p`'s area on the refilled routing input, computed exactly as
  kicad-route computes its reference.
- `a(p, c)` is the fill that one through-via of net class `c`, at that class's via
  size and clearance, removes from `p` after a kicad-cli refill. It is measured on
  the same board, not computed from a formula.

The bound is rounded up to 0.001 % because kicad-route rounds each delta to
0.001 % and fails only when the loss strictly exceeds the bound.

Why the rule admits legitimate routes and still rejects bad ones:

- **It is not zero.** Each routed signal via costs one antipad. A zero bound
  rejects any route that needs a single via. The native test demonstrates this
  below.
- **It is not chosen to pass.** It is fixed by `N` and the board before any
  routing result exists. A route within budget passes: its loss is at most `N`
  whole antipads, because overlapping antipads or antipads at the plane edge cost
  less. A route fails when it uses more signal vias than the budget, or when its
  vias cut the plane into fragments that the refill removes beyond the budget's
  area.
- **A failure is evidence for a decision.** A routed attempt whose loss exceeds
  the bound is evidence for either re-placement or an owner decision to raise
  `N`. It is never a reason to recompute the bound from the attempt.
- **Diagnostic runs are not adoptable.** A diagnostic attempt with a permissive
  bound (for example 100) only shows how many vias the router wants. It feeds the
  owner's choice of `N` and is never adopted.

The rule bounds area only. Plane fragmentation that keeps the area within budget,
continuity under signals other than the RF net, and the RF structure itself are
outside it; see [What stays unqualified](#what-stays-unqualified). The pin's own
`pour_integrity` failures (`lost_fill_contact`, `isolated_island`,
`spokes_below_minimum`) still apply on top.

## Recipe

1. **Board state.** Use the routing input as the routing stage will receive it:
   placed, with no footprint anchor outside the outline, `add_planes` applied with
   the reviewed plan (`391602f6…` above), and net classes recaptured after
   AGENTEE-230 binds `RF_50` to `/POD_GNSS_RF`. That recapture adds a probed net
   class with its own clearance. Do not use native-v5 or any staged board.
2. **Measure.**

   ```sh
   PYTHONPATH=src python3 -m agent_ee.pour_budget measure \
       --candidate <campaign candidate root> --board <relative board path> --out <new directory>
   ```

   This runs in the executor sandbox with no network. On a copy of the board, it
   refills with `kicad-cli pcb drc --refill-zones --save-board` and records:

   - `pour_area_mm2`: every pour's area, summed as kicad-route sums it;
   - `outside_outline`: footprints whose anchor is outside the outline;
   - `probes`: for every pour, and for every net class with a routed net (a net
     of two or more pads off the pour nets), one through-via of that class's size
     with a free net. It is placed at an interior point whose whole antipad lies
     inside the pour. The board is refilled and the loss is measured on every
     pour;
   - `own_net_probes`: one via on the pour's own net.

   Fail-closed rules:

   - a board `.kicad_dru` with `via_diameter` or `hole_size` constraints is refused,
     because pcbnew's Python cannot evaluate which via size KRT would pick;
   - a probe whose antipad is not wholly inside the pour, or whose via loses its
     net in the refill, is `unknown`.
3. **Declare `N`** (owner question Q1). Record its basis. The measurement does not
   choose `N`.
4. **Derive.**

   ```sh
   PYTHONPATH=src python3 -m agent_ee.pour_budget derive --report <out>/pour-budget.json --via-budget N
   ```

   This prints `agent-ee.pour-budget-derivation/1`: the bound, the governing pour,
   per-pour areas and per-class via losses, and the board hash. It refuses, with
   exit 1, when:

   - the board is not placed;
   - a probe is unknown or missing;
   - a probe via removed no fill;
   - an own-net via removes fill (a via budget would not bound that loss);
   - the budget is not an integer from 1 to 100000;
   - the result exceeds 100 %.
5. **Bind.** The derivation's `board_sha256` must equal the routing input's hash
   in the routing attempt's `snapshot.json`. Any change to placement, planes or net
   classes changes that hash and requires a new measurement. Declare:

   ```json
   {"signal_layers": ["F.Cu", "B.Cu"], "pour_area_loss_pct": <derived>,
    "reference": {"net": "/GND", "layers": ["In1.Cu"], "rf_nets": ["/POD_GNSS_RF"]}}
   ```

   `layer_costs` is omitted. With only the two outer layers allowed, a cost ratio
   between F.Cu and B.Cu is a routing preference, not a guard (owner question Q2).
   Adopting a policy in the pod campaign remains the pod lane's step; see
   [routing-adapter.md](routing-adapter.md#declared-layer-policy-agentee-231).

### Source-backed fields

| Field | Value | Basis |
| --- | --- | --- |
| `signal_layers` | `["F.Cu", "B.Cu"]` | intake `reviewed_constraints.signal_layers`. In1/In2 are `/GND` planes in the reviewed plane plan |
| `reference.net` / `layers` / `rf_nets` | `/GND`, `["In1.Cu"]`, `["/POD_GNSS_RF"]` | RF capture `source_stackup`: signal F.Cu over In1.Cu, 0.2104 mm prepreg. Native net names per review-225 §1.2 and finding 3 |
| `pour_area_loss_pct` | none yet | needs the post-placement measurement and `N` (above) |

### Evidence on the staged pod board (not a bound)

The probe was run in scratch on the reviewed plane plan applied to native-v5.
`add_planes` output `6d8b46bd908942974cdffcee514c839fef20b4608d7b50b2e1adb93de711934a`
reproduces the board hash in review-225's table. The results:

- **Pour area:** `/GND@In1.Cu` and `/GND@In2.Cu` are 3741.6545 mm² each, matching
  review-225.
- **Routed net class:** `Default` only, with a 0.25 mm via and 0.15 mm drill.
  `RF_50` is not bound on native-v5 (AGENTEE-230).
- **Signal via:** one `Default` through-via removes 0.3348 mm² from each plane.
  That is a 0.2 mm zone clearance plus KiCad's 0.0005 mm filler margin around the
  0.125 mm via radius.
- **`/GND` via:** removes 0.0 mm².
- **`derive`:** refuses with `not a placed board: footprints outside the outline: C1, …`
  (104 references).

For orientation only: on this staged fill, one signal via costs 0.00895 % of a
plane. The 0.5 example would therefore admit about 56 signal vias. The
post-placement fill will be smaller, so each via's share will be larger.

## Native evidence for the rule

[`tests/test_pour_budget.py`](../tests/test_pour_budget.py) runs on original probe
boards:

- **Antipad size.** A planed four-layer probe has a `Default` class (0.25 mm via)
  and a `WIDE` class (0.6 mm via). Each probe removes one whole circular antipad
  from each plane, within 0.01 mm² of π(r + 0.2005)². A `/GND` via removes
  nothing, and the bound follows the worst class.
- **Denominator.** The measured pour areas equal kicad-route's own
  `kicad/pour.py` facts on the same refilled board.
- **Refusals.** An unplaced footprint, a missing, unknown or zero probe, an
  own-net loss, a bad budget, reused output and custom via-size rules are all
  refused.
- **End to end with the pinned router.** On the layer-policy probe, the only
  signal runs from an F.Cu pad to a B.Cu pad, so it needs one via. With
  `signal_layers` F.Cu/B.Cu and the bound derived for `N = 1`:
  - the routed board has exactly one via (the signal's) and the grade passes;
  - the router's reference area equals the measured baseline;
  - each plane's loss is within 0.02 mm² of the measured per-via loss.

  The same route with a zero bound has the same single via and gives an
  `area_loss` defect.

  In a scratch replay of this scenario (fixture default 0.6 mm via, 0.25 mm plane
  clearance):

  - each plane measured 1466.8348 mm², and one signal via removed 0.9575 mm², so
    the derived bound was 0.066 %;
  - the routed board lost exactly 0.9575 mm² per plane (−0.065 %), which passed;
  - with a 0 bound, the same loss was `validation_failed` with an `area_loss`
    defect.

## What stays unqualified

The layer policy is a geometric routing guard: allowed layers, a pour-area bound
and 2D fill coverage under RF tracks. None of the items below is qualified by it:

| Item | What the guard does today | What stays open | Evidence or check that would close it |
| --- | --- | --- | --- |
| Routed impedance of `/POD_GNSS_RF` | Nothing until AGENTEE-230 binds `RF_50` (min width 0.335 mm, clearance 0.2032 mm) to `/POD_GNSS_RF`. Then KRT routes at the class width | The calculation is `SurfaceCoplanarWaveguideWithLowerGnd1B`: grounded coplanar waveguide with ground copper on F.Cu at the D1 gap of 0.2032 mm. The reviewed plane plan puts no copper on F.Cu, and the class clearance is a minimum, not an exact gap. The routed line is therefore not the calculated structure, and the RF capture records impedance as `recorded_not_enforced` with the RF maximum length unbound | An owner decision on the line structure (Q3). Then either an F.Cu `/GND` coplanar zone in the plane plan with a post-route check of the actual gap and width along every RF segment, or a recalculation for the structure actually routed. Fabricated-board TDR/VNA measurement for qualification |
| Reference and return-path continuity under the RF trace after placement | `reference_continuity`: 2D coverage of the In1.Cu `/GND` fill under each RF track footprint on the routed candidate. An RF via through In1 is a defect | Ground beyond the track footprint (the coplanar gaps and the field region around the trace), stitching vias along a coplanar ground and at the J2 and U3 launches, and return paths of signals other than the RF net. The area bound does not cover fragmentation within budget | A declared guard width (Q5) checked as fill coverage of the track grown by that width. A stitching-via spacing check if a coplanar ground is adopted. The RF field and bench qualification remains separate |
| Antenna keepouts | U1's footprint-owned rule area (no copper or pours on all four layers) lies outside the outline (review-225 §1.3). Router copper inside a keepout rule area is a new native DRC violation, which fails validation (`cli.py` lines 833–839) | Whether the captured keepout matches the module documentation for the placed position. The conflict between `spec.md` ("solid ground under the module antenna") and the keepout (review-225 finding 4). The GNSS antenna is external through J2, and its feed region has no captured keepout | A placement-stage check that U1's overhang and keepout match the captured source at the final position. An owner answer to Q6 |
| Per-net F.Cu-only routing of `/POD_GNSS_RF` | `signal_layers` is global: B.Cu is allowed for every net. On native-v5 every `/POD_GNSS_RF` pad is an F.Cu SMD pad (J2.1, U3.11, L3.2, D3.1). Any B.Cu RF segment would therefore need a via through In1, which `reference_continuity` grades a defect | That consequence holds only while every RF pad stays on F.Cu. A placement side flip of J2, U3, L3 or D3, or a through-hole RF pad, would allow a B.Cu RF track that the 2D check measures against In1 and can pass | Re-check the RF pad layers on the placed board before routing, as a precondition of the recipe. Or add a per-net layer item to `layer_policy` (for example `reference.signal_layers`) that grades any RF track off F.Cu as a defect. That changes `kicad_route_attempt.py` and its frozen adapter hash, so it needs its own ticket |

## Owner questions

None of these is assumed anywhere on this page:

- **Q1.** The signal through-via budget `N` for the pod: the largest acceptable
  number of non-`/GND` through-vias, or equivalently the largest acceptable
  inner-plane area loss.
- **Q2.** Should the router prefer F.Cu over B.Cu (`layer_costs`), or are the
  outer layers equal?
- **Q3.** Is the RF line meant to be the calculated grounded coplanar waveguide,
  which needs F.Cu `/GND` copper beside the trace and therefore a plane-plan
  change, or a microstrip over In1, which needs a different width?
- **Q4.** What is the maximum RF routed length? The RF capture's `blocked_need`
  says it is unbound.
- **Q5.** What width of continuous In1 `/GND` must surround the RF trace, and is
  stitching required?
- **Q6.** "Solid ground under the module antenna" (`spec.md`) or the captured
  module keepout (review-225 finding 4)?
- **Q7.** Must In2.Cu also be a declared reference layer for the RF net?
- **Q8.** Must `/POD_GNSS_RF` be F.Cu-only end to end as a stated requirement? The
  RF calculation implies it, but no source states it.

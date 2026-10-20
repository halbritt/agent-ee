# Pod routing: initial layer policy determination (AGENTEE-257)

This page answers the pod lane's question (agent-469, 2026-10-19): is there a
source-backed initial [AGENTEE-231 `layer_policy`](routing-adapter.md#declared-layer-policy-agentee-231)
for the telemetry pod, in particular a defensible `pour_area_loss_pct`?

**Answer:** not yet. `signal_layers` and the RF `reference` are source-backed.
No numeric `pour_area_loss_pct` can be source-backed today, for two reasons:

- the bound's denominator does not exist yet, because no placed pod board exists;
- the quantity the bound has to encode, a signal-via budget, is not in any captured
  source. It is an engineering choice, and this page gives the method that makes it
  ([via budget](#via-budget-n)).

This page gives the evidence-gathering recipe and its decision rule, the owned tool
that performs it ([`agent_ee.pour_budget`](../src/agent_ee/pour_budget.py)), a
[classification](#classification-of-the-open-decisions) of every open decision
(captured source, engineering choice or owner fact), and the
[engineering paths](#engineering-paths) for the via budget, the RF length bound,
the return corridor and the stitching pitch. The geometric routing guard is not an
RF qualification. The section [What stays unqualified](#what-stays-unqualified)
lists what remains open and how each item would be closed.

Two tracker items carry work this page cannot do:

- **AGENTEE-260** owns the pre-routing, coordinator-owned derivation and its
  sequencing. The numeric `pour_area_loss_pct` cannot exist when the campaign is
  configured: it is derived from the placed, planed, unrouted board, after
  placement and before routing. Campaign configuration can only declare the
  method and the inputs. The campaign field `layer_policy_derivation` now does
  this: the coordinator measures, derives and binds the bound on each routing turn
  ([derived at the pre-routing boundary](routing-adapter.md#derived-at-the-pre-routing-boundary-agentee-260)).
- **AGENTEE-261** owns the RF geometry checks this page leaves unenforced: the exact
  grounded-CPW gap and coplanar ground, the stitching-via pitch along the RF
  corridor, and branched RF net length. AGENTEE-258/259 (per-net signal layers
  and their wiring into the RF assessment) do not close those items.

This is a specialist determination for routing preparation. It does not change an
owner requirement, a physical limit or a sealed input. It adopts no constraint
value: adoption stays with the pod lane.

## Sources read

Hashes were recomputed with `sha256sum` on 2026-10-19:

| Source | sha256 |
| --- | --- |
| `pod-campaign-v1/constraint-application-adoption-draft/routing-policy-intake-at-assignment.json` (alias **intake-at-assignment**) | `a12a1a00a3c54d0a691bb0909d64da4b519e26b88f1d3aefcdbf2d7f418fb67b` |
| `pod-campaign-v1/reviewed-planes/plane-plan.json` | `391602f6cb02785f69e7e06c7992d5c0fc08a7a97a1110cc2f143dfab61b8676` |
| `pod-campaign-v1/reviewed-planes/review-225.md` | `f5d89194c5272360ab7da0b6e945b88445ff201228c118e85af3893dfd126df1` |
| `pod-generation-inputs-v3/rf-source-capture.json` (the RF calculation capture) | `e881b4f1466524bafa45698ed8cb9c59be946009c2388ca69cd34257f82b4822` |
| `pod-generation-inputs-v3/core/datasheets-core/77a91ad2….pdf`: u-blox NEO-M9N-00B data sheet UBX-19014285 R08 (the manifest records the same hash) | `e21b35c1a4fd04f353e298a3c3c411c08e683a318d87ad3af9f570da40a40150` |
| `pod-generation-inputs-v3/sources/jlcpcb-stackup.txt` (JLC04161H-7628) | `02e61b5b1b54092a5930a3ddf601186bc9ea29adb6595c94afb3595aa89a75a4` |
| `pod-generation-inputs-v3/fab-profile.json` | `02e484728bbcf695422ab2aa70ff3923ddd82b871fc28715aa1e9e9104822632` |
| `pod-generation-inputs-v3/spec.md` | `09e02c8d9471aabfa5d25935c87f436370cf1be367a96425b35305a53ce93493` |
| `pod-generation-inputs-v3/complete-decisions.md` ("engineering selections under the owner's delegated authority") | `f09a0b56852332738ce3d2f342f0bc815732cc8b59e751e6b2b5cb14e4b08dcb` |
| `calc-4-result.json`: the raw JLC impedance-solver response behind the RF capture (`dErEff` 2.94025199985, 49.6637289254 Ω) | `d05e15b93f02b3cc12b9b967c093503c60a3c72338a2f35ddccd96d4998b89af` |
| `pod-generation-inputs-v3/antenna/policy.json` (ESP32 antenna `outside_board`) | `62bd9a2f35edcdadbf8c5ca3bf863d3824d0c82be684dbf426e687a99ce1d163` |
| `pod-generation-inputs-v3/sources/esp32-guidelines.pdf` (cited by that policy, §1.4.7) | `057f6df63e10a2dfc155cf19d942adf5780127d839626e2beb4c1a61efeaf3a5` |
| native-v5 `telemetry-pod.kicad_pcb` (the staged board the plane plan was reviewed on) | `4fe774b5751af2b7cbd7169a81356fe5d57b741dd25c12a63c1d75639a484087` |
| native-v5 `telemetry-pod.kicad_pro` | `84ec00618f295150ad6c27e9e2c84d2d713cd10794e8d58d2e07107ccc802800` |

All paths are under `/tmp/agent469-coordination/`, except native-v5, which is under
`/tmp/agent463-pod-preparation-20261018/native-v5/scaffold/inputs/project/`, and
`calc-4-result.json`, which is under
`/tmp/agent-ee-burndown-20261017/workers/ee186e-gnss-rf-w2/scratch/`.
The live `routing-policy-intake.json` beside the snapshot now hashes
`487abc8fc0f032494186858990f0d302576d9f3e835d24118c79285579b261c5`. It differs from
intake-at-assignment only in `specialist_resolution_request` (status
`accepted_worker_started`, plus `work_item` and `response_record`); its sources,
`reviewed_constraints` and `unresolved` entries are identical. This page answers
intake-at-assignment.
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
   0 placements, and the AGENTEE-212 continuation intake
   (`pod-campaign-v1/placement212t-intake.json`) still reports 0 placements. The
   plane fill measured on native-v5 (3741.6545 mm² per inner layer) is a
   staged-board fill. It lacks the antipads and thermal cuts of the 104 unplaced
   footprints, so it is not the reference the router will divide by.
2. **The bound is derived from a via budget, and no source gives one.** With an
   outer-only allowlist, the router-induced loss is about the number of signal
   through-vias times one antipad, so the bound is derived as an area from a
   chosen via count. `spec.md`, the envelope, the fab profile, the RF capture and
   the plane review bound neither the via count nor the plane area. Choosing that
   count is an engineering decision with a recorded basis: see
   [via budget N](#via-budget-n). The check then enforces the area, not the count
   ([what is enforced](#what-the-check-enforces-area-not-via-count)).

Because of reason 1, the number can only be derived after placement and before
routing. That sequencing, and who derives the number, is AGENTEE-260.

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
  adoptable routing result exists. A route with at most `N` signal through-vias
  passes the area item: its loss is at most `N` whole worst-class antipads,
  because overlapping antipads, antipads at the plane edge and smaller-class
  antipads cost less. A route fails when its router-induced loss on some
  `net@layer` exceeds the bound, whether from too many antipads or from fragments
  the refill removes. That is an area outcome, not a via count; see below.
- **A failure is evidence for a decision.** A routed attempt whose loss exceeds
  the bound is evidence for re-placement or for re-running the
  [via-budget method](#via-budget-n) on a new board. It is never a reason to
  recompute the bound from the failing attempt.
- **Diagnostic runs are not adoptable.** A diagnostic attempt with a permissive
  bound (for example 100) only shows how many vias the router wants. It is the
  measured input to `N` and is never adopted.

The rule bounds area only. Plane fragmentation that keeps the area within budget,
continuity under signals other than the RF net, and the RF structure itself are
outside it; see [What stays unqualified](#what-stays-unqualified). The pin's own
`pour_integrity` failures (`lost_fill_contact`, `isolated_island`,
`spokes_below_minimum`) still apply on top.

### What the check enforces: area, not via count

`N` is the engineering choice from which the bound is derived. It is not what any
check grades. Both graders compare plane **area**:

- kicad-route's `pour_integrity` `area_loss` fails a `net@layer` whose area loss
  exceeds the bound;
- agent-ee's layer-policy item `area_loss` (`kicad_route_attempt.area_loss`)
  grades that same recorded comparison against the declared bound.

Neither counts vias. A route can therefore satisfy the area bound with a via count
different from `N`:

- **more than `N` signal vias** when their antipads overlap, sit at the plane edge
  or belong to a net class with a smaller via or clearance than the worst class
  the bound was derived from;
- **any number of `/GND` vias**, which join the inner `/GND` planes and remove no
  fill from them ([measured](#evidence-on-the-staged-pod-board-not-a-bound));
- a different via mix on a pour whose area `A(p)` is larger than the governing
  pour's, since the bound is one percentage for every `net@layer`.

A route can also fail with fewer than `N` vias, when a fragment the refill removes
pushes the loss over the bound. **No check enforces the via count itself.**
`routing_rules` `via_count` counts vias on named nets with qualified two-pad
routes; it is not a board-wide signal-via total. A board-wide count, if one is
wanted, would be a new check. The retained `V_i` and `N` are a record of the
derivation, not a grade.

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
3. **Choose `N`** by the [via-budget method](#via-budget-n) on the same board hash
   and record its basis. The pour measurement does not choose `N`.
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
   classes changes that hash and requires a new measurement and a new `N`. Declare:

   ```json
   {"signal_layers": ["F.Cu", "B.Cu"], "pour_area_loss_pct": <derived>,
    "reference": {"net": "/GND", "layers": ["In1.Cu"], "rf_nets": ["/POD_GNSS_RF"]}}
   ```

   `layer_costs` is omitted; see [layer costs](#layer-costs). Adopting a policy in
   the pod campaign remains the pod lane's step; see
   [routing-adapter.md](routing-adapter.md#declared-layer-policy-agentee-231).
   In a campaign, steps 2, 4 and 5 are the coordinator's
   (AGENTEE-260): with `layer_policy_derivation` it measures and derives on every
   routing turn and records the result. It binds the record to the adopted routing
   input, and runs the router with exactly that bound. Step 3 stays an engineering
   input: the `via_budget` record with its pilot counts and board hash. See
   [derived at the pre-routing boundary](routing-adapter.md#derived-at-the-pre-routing-boundary-agentee-260).

### Source-backed fields

| Field | Value | Basis |
| --- | --- | --- |
| `signal_layers` | `["F.Cu", "B.Cu"]` | intake-at-assignment `reviewed_constraints.signal_layers`. In1/In2 are `/GND` planes in the reviewed plane plan |
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

## Classification of the open decisions

The first determination listed eight owner questions (Q1–Q8) and one owner fact
(Q9, the antenna and cable). Each is classified here as:

- **(a)** decided by a captured source, which is cited;
- **(b)** an engineering choice, with a method and a recorded basis in
  [Engineering paths](#engineering-paths);
- **(c)** a missing owner fact that changes product behaviour, which no captured
  source or engineering method can settle.

| # | Decision | Class | Decision and basis |
| --- | --- | --- | --- |
| Q1 | Signal through-via budget `N` | (b) | Measured routed demand on the placed board plus a stated margin. `N` is converted into the derived `pour_area_loss_pct`; checks enforce that plane-area bound, not the via count ([area, not via count](#what-the-check-enforces-area-not-via-count)). See [via budget N](#via-budget-n) |
| Q2 | `layer_costs` (F.Cu vs B.Cu preference) | (b) | Omitted (equal costs). The captured stackup is symmetric and both inner layers are `/GND`, so neither outer layer has a better reference for non-RF nets. The RF layer is a per-net requirement, not a cost. See [layer costs](#layer-costs) |
| Q3 | RF line structure | (a) | The RF calculation capture: `W2_SurfaceCoplanarWaveguideWithLowerGnd1B`, a grounded coplanar waveguide (GCPW) on F.Cu over In1.Cu, with W1 0.334416796875 mm, D1 0.2032 mm and 49.6637289254 Ω. It is kept as captured, and microstrip is never substituted silently. Implementing it needs F.Cu `/GND` coplanar copper, which the reviewed plane plan does not have. See [RF structure](#rf-structure-gcpw-on-fcu-over-in1) |
| Q4 | Maximum RF routed length | (b), **unknown today** | Method: a loss-budget derivation from the antenna gain, the cable loss, the NEO-M9N data sheet and the loss per length. The captured sources lack an antenna and cable data sheet and the dielectric loss tangent, so the bound stays unknown. The antenna and cable are themselves a (b) selection that still needs a sourced candidate (below). See [RF length bound](#rf-length-bound) |
| Q5 | Return corridor width under the RF trace, and stitching | (b) | Guard 1.19 mm beyond each track edge on In1 and on the F.Cu coplanar ground: an unsourced geometric guard choice, not RF evidence. Stitching pitch at most 4.45 mm (λg/20 at 1605.375 MHz, conservative εeff). See [return corridor, guard width and stitching](#return-corridor-guard-width-and-stitching) |
| Q6 | Ground under the module antenna or the captured keepout | (a) | Review-225 finding 4: the captured ESP32-S3-WROOM-1 keepout (`antenna/policy.json`, `outside_board`, esp32-guidelines §1.4.7) governs the antenna region, which has no copper on any layer. Solid `/GND` lies under the module body. `spec.md`'s "solid ground under the module antenna" is read as shorthand for the module and RF path, as review-225 already reviewed. Not reopened |
| Q7 | In2.Cu as a second RF reference | (a) | The RF calculation's model has one lower ground, In1.Cu, under F.Cu. In2.Cu is not part of the calculated structure, so no second reference is declared. The pod lane gave the same direction |
| Q8 | `/POD_GNSS_RF` F.Cu-only end to end | (a) | The RF calculation defines the candidate structure on F.Cu over In1. Any other layer is not the calculated line. Enforcement is a check gap owned by AGENTEE-258/259, not an open decision |
| Q9 | Which external active antenna and cable the product uses | (b), **needs a sourced candidate** | `spec.md` fixes the antenna type and installation; `complete-decisions.md` delegates component selection to engineering. See [antenna and cable selection](#antenna-and-cable-engineering-selection-under-delegation) |

No item is class (c) after this classification. The installation facts that
`spec.md` does not state are listed separately
[below](#installation-facts-not-stated-in-specmd); none of them blocks the
selection or the length method.

### Antenna and cable: engineering selection under delegation

`spec.md` fixes the antenna arrangement:

- "NEO-M9N with an external active antenna on an SMA connector" (component
  decisions);
- the enclosure has "the SMA antenna connector" as one of only two penetrations;
- "GNSS antenna mounted inside the cowl, as high as possible and facing up. The
  cowl is fiberglass and therefore transparent to GNSS and BLE", with an "antenna
  cable no longer than 500 mm to the SMA connector" (mechanical).

It names neither the antenna model nor the cable type. `complete-decisions.md`
states that the candidate's component identities "are engineering selections
under the owner's delegated authority". The antenna and cable are therefore an
engineering selection under that delegation, class (b), not an owner fact. The
earlier determination's request for an owner answer is withdrawn.

The selection is still open: **no candidate antenna or cable data sheet is
captured.** A candidate needs, from captured manufacturer sources:

- an active GNSS antenna with an SMA plug or a pigtail of at most 500 mm ending
  in one, covering GPS L1 and GLONASS L1OF up to 1605.375 MHz (the highest NEO-M9N
  signal, Table 3), with a stated LNA gain range `G_a`, noise figure `F_a`,
  supply voltage range and current;
- if the cable is separate, its type and loss per metre at about 1.6 GHz, at a
  length of at most 500 mm;
- a check that the antenna's supply range matches the board's antenna supply,
  and that `G_a − L_c − L_b` stays at or below the NEO-M9N `Ext_gain` 30 dB
  (Table 11) at the shortest board trace.

Those parts set the gain and noise figure the receiver sees at RF_IN, so the
[RF length bound](#rf-length-bound) stays **unknown** until the candidate's
captured data sheets exist. No check may claim the bound before then. The
selection also remains a candidate: the antenna's in-cowl performance stays
unqualified (`complete-decisions.md` keeps "EMC/RF/antenna/enclosure performance"
open).

### Installation facts not stated in `spec.md`

These are genuine installation facts that the specification leaves open. They
refine the analysis; none is needed to select a candidate or to run the length
method, because a worst case can be taken:

- **Installed cable length.** `spec.md` gives only the upper bound, 500 mm. Until
  a measured route length in the cowl exists, the length method takes each rule's
  own worst case: 500 mm (most cable loss) for the noise-figure rule, and the
  shortest plausible cable (least loss) for the `Ext_gain` ceiling.
- **Antenna mounting surface.** `spec.md` says inside the cowl, high and facing
  up, but not whether the antenna sits on a metal ground plane. Many active patch
  antennas specify gain on a stated ground plane. Until the mounting surface is
  known, the selection uses the data sheet's gain for the no-ground-plane or
  smallest stated ground-plane case, or prefers an antenna specified without one.

## Engineering paths

Every input below comes from a captured source. The thresholds applied to those
inputs (the 90 % containment of the guard width, the λg/20 stitching fraction,
the 10 % via margin and the 0.1 dB noise-figure allowance) are engineering
choices, stated as such; they are not sourced and are not RF qualification. Each
path names its pass/fail rule and the check that enforces it. "Unenforced" means
no existing check kind grades the rule. It is then an open gap, never a pass.

The derivations below reproduce with this standard-library snippet. The inputs
are the RF capture `w2_continuation` (H 0.2104 mm, εr 4.4, emitted width 0.335 mm,
D1 0.2032 mm), the solver's `dErEff` from `calc-4-result.json`, GPS L1
1575.42 MHz, and NEO-M9N Table 3 (GLONASS L1OF up to 1602 MHz + 6 × 562.5 kHz):

```python
import math

C, ER, H, W, D1 = 299_792_458.0, 4.4, 0.2104, 0.335, 0.2032
ER_EFF_SOLVER = 2.94025199985                 # calc-4-result.json impedance_calc_result.dErEff
F_L1 = 1575.42e6                              # GPS L1
F_MAX = 1602e6 + 6 * 562.5e3                  # 1605.375 MHz, the highest NEO-M9N signal
lambda_g_min = C / F_MAX * 1e3 / math.sqrt(ER)  # 89.026 mm: eps_eff < eps_r on any line partly in air
pitch = math.floor(lambda_g_min / 20 * 100) / 100  # 4.45 mm (governing)

for f in (F_L1, F_MAX):                       # comparison only, not governing
    lam_g = C / f * 1e3 / math.sqrt(ER_EFF_SOLVER)
    print(f / 1e6, round(lam_g, 3), math.floor(lam_g / 20 * 100) / 100, math.floor(lam_g / 10 * 100) / 100)
# 1575.42 110.977 5.54 11.09
# 1605.375 108.906 5.44 10.89


def outside(g):
    # Worst-case In1 return current outside a corridor g beyond each track edge:
    # a filament at the track edge over an infinite plane, J ~ h / (h^2 + x^2).
    return ((math.pi / 2 - math.atan(g / H)) + (math.pi / 2 - math.atan((W + g) / H))) / math.pi


lo, hi = 0.0, 100.0
for _ in range(200):
    mid = (lo + hi) / 2
    lo, hi = (mid, hi) if outside(mid) > 0.10 else (lo, mid)
guard = math.ceil(hi * 100) / 100              # 1.19 mm (exact root 1.1812 mm); 0.10 is a chosen heuristic
alpha_d_coefficient = 27.3 * math.sqrt(ER) / (C / F_MAX * 1e3)  # 0.30665 dB/mm per unit tan(delta)
q = (ER_EFF_SOLVER - 1) / (ER - 1)            # quasi-TEM filling factor, comparison only
alpha_d_solver = 27.3 * ER / math.sqrt(ER_EFF_SOLVER) * q / (C / F_MAX * 1e3)  # 0.21407
```

### Via budget N

**Basis.** No source bounds the via count. A useful budget does two things. It
admits the route the placed board actually needs. It also catches a route that
spends far more inner-plane area than that need, for example a regression or a
different router order. Only routed demand on the same board measures that need.

**Method**, on the exact routing input whose hash the derivation binds:

1. Run `K = 3` diagnostic routing attempts with `signal_layers` F.Cu/B.Cu, the
   same net classes and a permissive, non-adoptable `pour_area_loss_pct` of 100.
   Only attempts classified `complete` count. If any attempt is not `complete`, the
   demand is unknown and `N` cannot be chosen. That outcome is evidence for
   placement, not a budget.
2. In each attempt, count `V_i`: the through-vias of nets other than `/GND` in
   `validate/candidate-facts.json` that are not matched by a via of the same net,
   position, diameter and drill in `facts/reference-facts.json` (both files are
   written by the pinned router):

   ```python
   import collections
   import json
   from pathlib import Path


   def signal_vias(attempt_dir):
       attempt = Path(attempt_dir)
       cand = json.loads((attempt / 'validate/candidate-facts.json').read_text())
       ref = json.loads((attempt / 'facts/reference-facts.json').read_text())

       def key(via):
           return via['net'], tuple(via['position']), via['diameter'], via['drill']

       existing = collections.Counter(key(v) for v in ref['vias'])
       count = 0
       for via in cand['vias']:
           if existing[key(via)] > 0:
               existing[key(via)] -= 1
           elif via['net'] != '/GND' and via['via_type'] == 'through':
               count += 1
       return count
   ```

3. `N = V_max + max(1, ceil(0.1 × V_max))`, where `V_max = max_i V_i`. The stated
   margin is 10 %, with at least one via. It absorbs small differences between
   attempts and a rip-up without a new derivation. A route that needs materially
   more plane area than the demonstrated need still fails. If the attempts differ
   by more than the margin (`V_max − V_min > max(1, ceil(0.1 × V_max))`), the
   router is not repeatable enough on this board for a budget. Record that and do
   not choose `N`.
4. Run `pour_budget measure` and `derive --via-budget N` on the same board. The
   measured antipad loss `a(p, c)` turns `N` into the bound.

**Pass/fail.** The adoptable attempt passes when every `net@layer` loss is within
the derived bound. This is enforced by the existing layer-policy item
`area_loss` (`kicad_route_attempt.area_loss`), plus the pin's `pour_integrity`.
Both grade plane area. Neither grades the adoptable route's via count, which may
differ from `N` in either direction while the area passes or fails
([area, not via count](#what-the-check-enforces-area-not-via-count)). The
diagnostic attempts, `V_i`, `N` and the derivation are retained together with
the board hash as the derivation record. `routing_rules` `via_count` is not
used: it counts vias on named nets with qualified two-pad routes, not a
board-wide total.

**Not decided by this budget.** Whether the demand is acceptable for plane
function. That is graded by the RF corridor below and by `pour_integrity`,
not by the area total.

### Layer costs

**Choice: omit `layer_costs`.** The basis is the captured stackup
(`jlcpcb-stackup.txt`): F.Cu / 0.2104 mm 7628 prepreg / In1.Cu and
In2.Cu / 0.2104 mm 7628 prepreg / B.Cu. Both inner layers are `/GND` in the
reviewed plane plan. A non-RF net therefore has the same reference geometry on
either outer layer. The RF layer is a per-net requirement ([Q8](#classification-of-the-open-decisions))
that a cost cannot enforce. A cost is a routing preference, not a guard, so it has
no pass/fail rule. If the via-budget diagnostics show a needlessly high via count,
a cost experiment (the same `K` attempts with costs) may be compared. It changes
the route only, never a grade.

### RF structure: GCPW on F.Cu over In1

The RF calculation capture fixes the structure: `SurfaceCoplanarWaveguideWithLowerGnd1B`
(uncoated), signal on F.Cu, lower ground In1.Cu at 0.2104 mm, εr 4.4, F.Cu copper
0.035 mm. The geometry is W1 0.334416796875 mm (emitted minimum 0.335 mm), W2
0.321716796875 mm (taper metadata) and coplanar gap D1 0.2032 mm. The structure
needs F.Cu `/GND` copper on both sides of the trace at D1. The reviewed plane plan
has none. Routing a plain trace over In1 would give a different line, which is not
the calculated one.

**Path.** After placement, propose an F.Cu `/GND` coplanar zone along the routed
or reserved RF path. Its geometry is derived fresh from the placed J2.1/U3.11/L3.2/D3.1
pads:

- gap D1 to the RF copper;
- strip width at least `guard − D1` = 0.99 mm (below);
- stitched as below.

That proposal is a plane-plan change. It needs its own review after placement,
like review-225. Nothing here pre-approves it.

**Pass/fail.** For every RF segment, the measured edge-to-ground gap on F.Cu
equals D1 within the fabrication tolerance, the width is at least 0.335 mm, and the
In1 reference is present (below).

**Enforcement:**

- the width minimum is enforced (`geometry` `copper-width/1`, router `RF_50`);
- the clearance minimum is enforced (`RF_50` clearance 0.2032 mm, DRC);
- the exact gap, its upper side included, and the presence of the coplanar ground
  are **unenforced**: no check kind measures them. AGENTEE-261 owns that
  post-route measurement; AGENTEE-258/259 do not cover it.

### Return corridor, guard width and stitching

**Guard width.** In1 carries the return current under the trace. Model the worst
case as a current filament at the track edge over an infinite plane, ignoring the
F.Cu coplanar ground, which only pulls return current closer. By image theory, the
plane current density is ∝ h / (h² + x²). The share outside a corridor that
extends `g` beyond each edge is
`[(π/2 − atan(g/h)) + (π/2 − atan((W + g)/h))] / π`. The threshold chosen here is
**at least 90 % containment**.

> [!IMPORTANT]
> The 90 % image-current containment threshold, and so g = 1.19 mm, is an
> **unsourced engineering heuristic**: a geometric guard choice for routing, not
> RF evidence and not RF qualification. No captured source states a containment
> fraction or a corridor width for this line. The model is a quasi-static
> filament over an infinite plane; it does not show that the line meets its
> impedance, loss or coupling targets with this corridor, or fails them without
> it. Evidence that would replace the threshold: a 2D/3D field-solver run of the
> calculated GCPW cross-section on the captured stackup, giving impedance and
> return-current distribution versus In1 and coplanar-ground width; or a
> fabricated coupon of the same stackup with the corridor narrowed in steps,
> measured by TDR/VNA. Either would set the corridor from evidence; until then
> 1.19 mm is a recorded choice.

Sensitivity of the choice:

| Containment | g (mm) | corridor W + 2g (mm) |
| --- | --- | --- |
| 80 % | 0.52 | 1.375 |
| **90 %** | **1.19** (root 1.1812) | **2.715** |
| 95 % | 2.52 | 5.375 |

The common 3H rule (D1 + 3H = 0.8344 mm), also a heuristic, is smaller than the
90 % value, so 1.19 mm governs. The guard applies to:

- In1.Cu: continuous `/GND` fill under the RF track grown by 1.19 mm per side, with
  no antipad of another net's via inside it;
- F.Cu: the coplanar ground strip, from D1 out to at least 1.19 mm from the track
  edge (width at least 0.99 mm).

**Stitching pitch.** The highest NEO-M9N receive frequency (Table 3) is GLONASS
L1OF at 1602 MHz + 6 × 562.5 kHz = 1605.375 MHz, so λ0 = 186.743 mm. On any line
partly in air, εeff < εr = 4.4, which bounds the guided wavelength from below:
λg ≥ λ0 / √4.4 = 89.026 mm. Stitching at λg/20 is chosen over the looser λg/10,
which would be 8.903 mm. The λg/20 fraction is itself a common design rule of
thumb, not a captured source. The pitch is therefore **at most 4.45 mm** (4.451 mm,
rounded down).

For comparison, the captured solver result (`calc-4-result.json`,
`impedance_calc_result.dErEff` 2.94025199985) gives the εeff of the calculated
cross-section. The guided wavelengths below use `λg = c / (f √εeff)`; pitches are
rounded down to 0.01 mm:

| Frequency | εeff | λ0 (mm) | λg (mm) | λg/20 (mm) | λg/10 (mm) |
| --- | --- | --- | --- | --- | --- |
| 1575.42 MHz (GPS L1) | 2.94025199985 (solver) | 190.294 | 110.977 | 5.54 | 11.09 |
| 1605.375 MHz (GLONASS L1OF k = 6) | 2.94025199985 (solver) | 186.743 | 108.906 | 5.44 | 10.89 |
| 1575.42 MHz | 4.4 (bound) | 190.294 | 90.719 | 4.53 | 9.07 |
| **1605.375 MHz** | **4.4 (bound)** | **186.743** | **89.026** | **4.45** | **8.90** |

The governing value stays the conservative 4.45 mm (bound row, highest
frequency). The solver value is a single quasi-static result for the calculated
cross-section (W1 0.334416796875 mm, D1 0.2032 mm), not for the routed geometry,
the taper, the launches or fabrication spread. Using the εr bound makes the pitch
independent of that value and about 18 % tighter than the solver-based 5.44 mm
at the same frequency. Field-solver or coupon evidence on the routed structure
could justify the looser pitch; nothing here does.

This applies:

- along both coplanar ground strips, `/GND` through-vias joining F.Cu ground to
  In1 (and In2), at most 4.45 mm apart, each via pad wholly inside the strip;
- at each launch, at least one `/GND` via within 4.45 mm of J2.1 and of U3.11. On
  U3, the data sheet's GND pins 10 and 12 flank RF_IN (pin 11);
- at each end of a coplanar strip.

These are frequency-derived at the GNSS band only. They do not qualify the
2.4 GHz BLE radio's coupling.

**Pass/fail and enforcement:**

| Rule | Enforcement |
| --- | --- |
| In1 fill covers the RF track footprint, and no RF via passes through In1 | Existing `reference_continuity` (layer-policy item) |
| In1 fill covers the track grown by 1.19 mm per side; no foreign antipad inside | **Unenforced.** `reference_continuity` measures the track footprint only. AGENTEE-258 asks for the declared reference under the routed length (reusing the 231 continuity geometry), not a guard width; AGENTEE-261 names the guard-width proxy only to say it is not qualification. A guard-width parameter is in neither ticket's stated scope; coordinator to assign |
| F.Cu coplanar strip present to 1.19 mm, stitched at ≤ 4.45 mm, launch vias present | **Unenforced.** No check kind measures the coplanar ground or stitching pitch. AGENTEE-261 owns both; AGENTEE-258/259 do not cover them |
| RF track only on F.Cu, end to end | **Unenforced** per net. AGENTEE-258/259. Global `signal_layers` does not enforce it |

### RF length bound

**Status: unknown.** The bound stays unknown until a sourced antenna and cable
candidate ([Q9](#antenna-and-cable-engineering-selection-under-delegation)) and one
material fact are captured. `rf-source-capture.json` records it as `unbound` (its
15 mm `native_control_max_mm` is test-only), and nothing here changes that.

**Method.** The signal chain is: antenna LNA (gain `G_a`, noise figure `F_a`) →
cable (loss `L_c`) → board trace (loss `L_b = α × ℓ`) → RF_IN (`NFtot` 3.5 dB,
GPS-only, Table 11). The bound `ℓ_max` is the largest `ℓ` that satisfies both:

1. **Gain ceiling:** `G_a − L_c − L_b ≤ 30 dB` (`Ext_gain`, Table 11). Trace loss
   only lowers the gain, so this rule can never bound length from above. It flags
   an over-gain antenna and short cable.
2. **Noise-figure degradation:** by Friis,
   `F_sys = F_a + (L_c × L_b × F_rx − 1) / G_a` (linear). The engineering choice
   is that board loss may raise `F_sys` by at most 0.1 dB over `L_b = 1`. Solve
   for `L_b`, then `ℓ_max = L_b(dB) / α`.

**Loss per length `α`.** The conductor loss follows from the captured geometry and
copper. The dielectric loss needs the 7628 prepreg loss tangent `tan δ` at about
1.6 GHz, which no captured source states. With εeff ≤ εr, the dielectric term is
bounded by `α_d ≤ 27.3 √εr tan δ / λ0` = 0.30665 × tan δ dB/mm at 1605.375 MHz
(0.30093 × tan δ at 1575.42 MHz). This is the all-dielectric bound. The usual
quasi-TEM filling-factor form, `α_d = 27.3 × (εr / √εeff) × ((εeff − 1) / (εr − 1)) × tan δ / λ0`,
with the solver's εeff 2.94025199985 gives 0.21407 × tan δ dB/mm at 1605.375 MHz
and 0.21008 × tan δ at 1575.42 MHz. The εr bound is kept as the governing value:
it is the larger loss, so it gives the shorter, conservative `ℓ_max`, and it
does not depend on the quasi-TEM approximation or on a solver value computed for
the calculated cross-section only. A measured loss per length on a test coupon of the same
stackup would replace both.

**Evidence needed to close:**

- the candidate antenna data sheet (`G_a` minimum and maximum, `F_a`);
- the candidate cable data sheet (loss per metre at 1.6 GHz) and its length, at
  most 500 mm;
- the JLC 7628 prepreg `tan δ`, or a coupon loss measurement.

**Pass/fail once bound.** The routed `/POD_GNSS_RF` length from J2.1 to U3.11,
plus the L3.2 and D3.1 stubs, must not exceed `ℓ_max`. `routing_rules` `length` is
the existing kind. It qualifies only two-pad, unbranched SMD routes, but
`/POD_GNSS_RF` has four pads (J2.1, U3.11, L3.2, D3.1). It would therefore be
unknown on this net, never a pass. The branched-net length check is AGENTEE-261,
not AGENTEE-258/259.

## What stays unqualified

The layer policy is a geometric routing guard: allowed layers, a pour-area bound
(derived from a via budget, but grading area, not via count) and 2D fill coverage
under RF tracks. The engineering paths above add rules for corridor, stitching,
structure and length. Those rules are not RF qualification either: the corridor
width rests on an unsourced containment heuristic, and the stitching pitch on a
λg/20 rule of thumb with a conservative εeff. Passing any of them is a geometric
result only. Field-solver and bench work stay unclaimed. None of the items below
is qualified by the guard:

| Item | What the guard does today | What stays open | Evidence or check that would close it |
| --- | --- | --- | --- |
| Routed impedance of `/POD_GNSS_RF` | Nothing until AGENTEE-230 binds `RF_50` (min width 0.335 mm, clearance 0.2032 mm) to `/POD_GNSS_RF`. Then KRT routes at the class width | The calculated structure is a GCPW with F.Cu ground at D1 0.2032 mm ([Q3](#classification-of-the-open-decisions)). The reviewed plane plan has no F.Cu copper, and the class clearance is a minimum, not an exact gap. The RF capture records impedance as `recorded_not_enforced` | The post-placement F.Cu coplanar-zone proposal and its review ([RF structure](#rf-structure-gcpw-on-fcu-over-in1)). A post-route exact-gap check (AGENTEE-261). Field-solver verification of the as-routed geometry and fabricated-board TDR/VNA measurement for qualification (unclaimed) |
| Reference and return-path continuity under the RF trace after placement | `reference_continuity`: 2D coverage of the In1.Cu `/GND` fill under each RF track footprint on the routed candidate. An RF via through In1 is a defect | The 1.19 mm corridor (an unsourced geometric guard), coplanar stitching at ≤ 4.45 mm and launch vias ([corridor](#return-corridor-guard-width-and-stitching)). Return paths of signals other than the RF net. The area bound does not cover fragmentation within budget | Guard-width coverage (in no ticket's stated scope; coordinator to assign). A stitching-pitch check (AGENTEE-261). A field-solver or coupon basis for the guard width. RF field and bench qualification remain separate and unclaimed |
| Antenna keepouts | U1's footprint-owned rule area (no copper or pours on all four layers) lies outside the outline (review-225 §1.3). Router copper inside a keepout rule area is a new native DRC violation, which fails validation (`cli.py` lines 833–839) | Whether the captured keepout matches the module documentation at the final placed position. The keepout-versus-`spec.md` wording is settled ([Q6](#classification-of-the-open-decisions)). The GNSS antenna is external through J2, so it has no board keepout | A placement-stage check that U1's overhang and keepout match the captured source at the final position |
| Per-net F.Cu-only routing of `/POD_GNSS_RF` | `signal_layers` is global: B.Cu is allowed for every net. On native-v5 every `/POD_GNSS_RF` pad is an F.Cu SMD pad (J2.1, U3.11, L3.2, D3.1). Any B.Cu RF segment would therefore need a via through In1, which `reference_continuity` grades a defect | That consequence holds only while every RF pad stays on F.Cu. A placement side flip of J2, U3, L3 or D3, or a through-hole RF pad, would allow a B.Cu RF track that the 2D check measures against In1 and can pass | Re-check the RF pad layers on the placed board before routing, as a precondition of the recipe. The per-net layer check is AGENTEE-258/259 |
| RF length | Nothing | Unknown bound ([RF length](#rf-length-bound)). A branched-net length measurement | A sourced antenna and cable candidate ([Q9](#antenna-and-cable-engineering-selection-under-delegation)), the loss-tangent capture or a coupon, then the branched-net length check (AGENTEE-261) |

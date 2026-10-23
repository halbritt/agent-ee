# GNSS RF path (AGENTEE-186)

The pod targets a 50 ohm coplanar path from NEO-M9N RF_IN to SMA on F.Cu over
the In1.Cu ground plane of JLC04161H-7628. A successful host-side JLCPCB
calculation now supplies the declared dimensions in
[RF_50 net constraints](../fixtures/telemetry-pod/rf-net-constraints.json).
**Actual impedance remains unverified.** The existing emitter records
`impedance_ohm: 50` as `recorded_not_enforced`.

The [requirement map](../fixtures/telemetry-pod/requirement-map.json) preserves
this target and its remaining evidence gaps under `POD-IF-GNSS-ANTENNA`.
Compilation puts it in scope limitations, outside executable acceptance
requirements. `tests.test_requirement_map` verifies that distinction and the
propagation of limitations into native run results and sealed evidence.

The [dedicated RF review](rf-review.md) consumes this record, the captured
calculation and native evidence as review inputs. Neither this page's controls
nor that review are RF qualification.

## Exact native net binding (AGENTEE-230)

`RF_IN` is the receiver's pin name, not the pod board's net name. The
[pin allocation](../fixtures/telemetry-pod/electrical/pin-allocation.json)
connects NEO-M9N U3 pin 11 (`RF_IN`) to `POD_GNSS_RF`.
`electrical/prepare_intent.py` preserves that conductor and assigns it to SMA
J2 pin 1. KiCad exports the root-sheet label as `/POD_GNSS_RF`.
RF_50 now declares that exact native name, including the leading slash.
The emitted exact pattern leaves `/` literal so both KiCad and the pinned
router bind RF_50; the previous `\/` spelling made KRT use Default width.
The capture's `dimension_mapping.net_adaptation` records the source hash,
terminal mapping and reason using the explicit source/native adaptation
pattern. Its updated constraint hash binds the renamed declaration. Calculator
geometry, unit conversion and raw request/result hashes are unchanged.

Emission with a supplied board, standalone CLI emission, DRC setup, copper-width
binding and router setup refuse absent exact constraint nets with their missing
names. The width capability also refuses an absent binding before capture.
A net need not have routed copper to exist: pad nets count. Generated scaffold
settings are planning output until populated-board checks establish membership.
They do not qualify a net by declaring a class alone.

## Captured calculations

### Historical uncoated inverse (calculation 4)

The host fetched the [JLCPCB calculator page](https://jlcpcb.com/pcb-impedance-calculator/)
and public client on 2026-10-19 UTC (2026-10-18 local). Decision a2
(agent-463 under the owner's delegated authority, 2026-10-18 19:38) authorized
three anonymous calculations with public recipe/supporting endpoints and
UUID-bound WebSocket results. All three selected inverse W1 and returned
solver status 6 with null geometry after their HTTP submissions were accepted.

Offline inspection then established that the captured public client's automatic
inverse path selects W2 (upper width), using bounds 2–150 mil. The earlier
requests selected W1 (lower width), using bounds 1–200 mil. Agent-463 authorized
exactly one further calculation on 2026-10-18 at 20:00: preserve attempt 3's
uncoated model and physical inputs, change only the inverse target/bounds to
W2/2–150, and use a fresh request identity. That request returned **solver
status 0, `dResultValid: 1`** and calculated widths. No further calculation,
hidden field, gap/model sweep, login, payment, upload or captcha bypass occurred
within that historical continuation.
The successful request needs no new recipe/support call.

| Submission | Inverse target | Input gap | Model and variation | Solver result |
| --- | --- | --- | --- | --- |
| 1 | W1, 1–200 mil | 5 mil | Coated coplanar with lower ground; visible fields | Status 6, null geometry |
| 2 | W1, 1–200 mil | 6 mil | Same recipe plus fetched hidden G1/HZ0 fields | Status 6, null geometry |
| 3 | W1, 1–200 mil | 8 mil | Uncoated coplanar with lower ground; visible fields | Status 6, null geometry |
| 4 | W2, 2–150 mil | 8 mil | Attempt 3 model and physical inputs preserved | Status 0, valid geometry |

The source template and fixture bind 0.2104 mm prepreg with dielectric constant
4.4, 0.035 mm F.Cu and 0.0152 mm In1.Cu reference copper. The request uses
H1=8.2835 mil and T1=1.3780 mil at the public client's four-decimal conversion
precision. T1 models the fixture's copper rather than the client's 1.6 mil
plated-copper default. The model is
`SurfaceCoplanarWaveguideWithLowerGnd1B`; it does not model solder mask.

The historical uncoated result reports 49.6637289254 ohm, W1
13.166015625 mil (0.334416796875 mm), W2 12.666015625 mil
(0.321716796875 mm) and D1 8 mil (0.2032 mm). Its requested
50 ±0.5 ohm solver search tolerance is historical calculator input, not an
acceptance band or fabrication tolerance. These widths are superseded by the
adopted coated inverse below.

### Historical coated forward point

The `historical_forward_point` in
[capture metadata](../fixtures/telemetry-pod/rf-source-capture.json), under
`w2_continuation.dimension_mapping.adopted_inverse`, retains the coated forward
point: 0.335 mm width, T1 0.035 mm and 47.394 ohm. It has its own inputs and
is distinct from the uncoated inverse above and the adopted coated inverse
below. No width sensitivity is inferred from comparing these calculations.

### Adopted coated inverse (agent-529 4cf9d43d@2)

Chief engineering direction `4cf9d43d@2` adopts the **0.293 mm CAD nominal
lower/drawn width with a two-flank 0.2032 mm gap**, AGENT-selected CONDITIONAL.
The [adopted decoded result](../fixtures/telemetry-pod/rf-inverse-50ohm-result.json)
explicitly names model `W2_CoatedCoplanarWaveguideWithLowerGnd1B` and reports
50.0087997251 ohm and effective Er 3.1696914377. Its basis is T1 0.04064 mm
(JLC 1.6 mil calculator parameter, not a fabricated-copper specification),
H1 0.2104 mm, Er1 4.4, mask C1 1.2 / C2 0.6 mil, CEr 3.8 and taper
0.5 mil. The JLC guide's 0.7 mil taper is a recorded difference, not merged
into this basis.

| Source field | Result in mil | Converted mm | Declaration |
| --- | --- | --- | --- |
| W1, lower width | 11.5301269531 | 0.29286522460874 | RF_50 `track_width_mm` |
| W2, upper width | 11.0301269531 | 0.28016522460874 | Retained taper metadata; no separate KiCad width |
| D1, gap on both flanks | 8 | 0.2032 | RF_50 `clearance_mm` |

The unit conversion is exactly 0.0254 mm/mil. KiCad represents planar copper;
the declaration uses lower W1 and retains W2 explicitly. The existing emitter
rounds the minimum upward to 0.293 mm. A minimum clearance does not enforce
an exact coplanar gap or establish return-plane continuity. No acceptance or
fabrication tolerance band is established, and neither the calculated geometry
nor the rounded KiCad route verifies actual pod impedance.

### Retained decoded results

[Capture metadata](../fixtures/telemetry-pod/rf-source-capture.json) records
URLs, UTC timestamps, tools, raw hashes, request identities and transport
observations for the historical workflow. Its `w2_continuation` retains the
historical calculation, while `dimension_mapping.adopted_inverse` identifies
the adopted result and basis; the mapping binds the current constraint hash.
Raw vendor pages, scripts and transport frames stay in worker scratch.
Two decoded result files are committed:

- [rf-w2-calc-4-result.json](../fixtures/telemetry-pod/rf-w2-calc-4-result.json)
  is the 513-byte historical uncoated W2 result, listed under
  `committed_decoded_results` (AGENTEE-264).
- [rf-inverse-50ohm-result.json](../fixtures/telemetry-pod/rf-inverse-50ohm-result.json)
  is the adopted coated inverse, bound by `adopted_inverse.result_path` and
  `result_sha256`; its effective Er is at `/impedance_calc_result/dErEff`.

These files hold calculator numbers and identifiers for agent-ee's inputs.
Hashes establish byte identity, not vendor authentication, impedance
qualification or committed offline replay. Checks use no network.

The RF maximum length remains a scope limitation until a design bound is
captured. The controls' 15 mm maximum is test-only. The spec's 500 mm antenna
cable limit applies to the cable to SMA, not the PCB trace. No length requirement
is inferred from that cable limit or from the impedance result.

## Native mechanism controls

`tests.test_gnss_rf_path` uses original four-layer two-terminal boards from
`tests.routing_fixture`, the existing emitter, routing adapter, stage receipts,
candidate capture, executor, `geometry` `copper-width/1`, `routing_rules` and
sealed evidence. U_GNSS and J_SMA are pad labels, not actual device models.

The source-bound probe starts without tracks. The pinned router receives the
emitted RF_50 project/rules for `/POD_GNSS_RF` in the networkless sandbox;
the test verifies its stage receipt and independently checks its unchanged output. Widths at the
emitted 0.293 mm width pass; narrowing one segment to 0.292999 mm produces a
width defect. The recorded 50 ohm target remains `recorded_not_enforced`.

The earlier synthetic controls remain separate:

- A 10 mm authored RF_IN path passes 0.3 mm width and test-only 15 mm length.
- A 0.299999 mm segment fails the synthetic width minimum.
- Moving the SMA pad to 40 mm and extending connected copper fails native length.
- Missing copper leaves length coverage unknown.
- Renaming a recorded native net refuses DRC and width checks, and router setup
  refuses the missing exact name before invoking the routing tool.
- Lowering the project width below the recorded minimum refuses capture.

These software probes have no ground plane or actual device footprints. They
establish dimensional check behavior, not exact gap, return geometry, impedance
or complete pod acceptance.

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 \
AGENT_EE_KICAD_ROUTE_CHECKOUT=/home/halbritt/git/agent-ee/build/kicad-route-pin-b577449 \
PYTHONPATH=src:tests python3 -m unittest tests.test_gnss_rf_path tests.test_requirement_map -v
```

Set `AGENT_EE_RF_EVIDENCE` to a new scratch directory to retain the source-bound
router receipt and positive/narrower check runs instead of removing them after
the test. Re-running these offline tests submits no calculator requests.

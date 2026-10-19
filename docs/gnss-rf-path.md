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

## Captured calculation

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
hidden field, gap/model sweep, login, payment, upload or captcha bypass occurred.
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

| Source field | Result in mil | Converted mm | Declaration |
| --- | --- | --- | --- |
| W1, lower width | 13.166015625 | 0.334416796875 | RF_50 `track_width_mm` |
| W2, upper width | 12.666015625 | 0.321716796875 | Retained taper metadata; no separate KiCad width |
| D1, selected gap preserved in result | 8 | 0.2032 | RF_50 `clearance_mm` |

The unit conversion is exactly 0.0254 mm/mil. KiCad represents planar copper;
the declaration uses the lower W1 width and retains W2 explicitly. The existing
emitter rounds the minimum upward to **0.335 mm**, a small difference from the
solver geometry. The server reports 49.6637289254 ohm for its modeled geometry
within the requested 50 ±0.5 ohm target. This numeric result does not verify the
rounded KiCad route or actual pod impedance. A minimum clearance also does not
hold the coplanar gap to an exact value or establish return-plane continuity.

[Capture metadata](../fixtures/telemetry-pod/rf-source-capture.json) records
URLs, UTC timestamps, tools, raw hashes, fresh request identities, all received
socket frames/payloads and sent control frames. Its `w2_continuation` binds the
single new result, upper/lower/gap mapping and constraint-file hash. Earlier
records remain historical. Raw vendor bytes stay in worker scratch; no owner
evidence store or vendor code/text is committed. Hashes establish byte identity,
not vendor authentication or committed offline replay. Checks use no network.

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
emitted 0.335 mm minimum pass; narrowing one segment to 0.334999 mm produces a
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
AGENT_EE_KICAD_ROUTE_CHECKOUT=/home/halbritt/git/agent-ee/build/kicad-route-pin-a132899 \
PYTHONPATH=src:tests python3 -m unittest tests.test_gnss_rf_path tests.test_requirement_map -v
```

Set `AGENT_EE_RF_EVIDENCE` to a new scratch directory to retain the source-bound
router receipt and positive/narrower check runs instead of removing them after
the test. Re-running these offline tests submits no calculator requests.

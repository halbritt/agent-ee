# GNSS RF path (AGENTEE-186)

The pod targets a 50 ohm coplanar path from NEO-M9N RF_IN to SMA on F.Cu over
the In1.Cu ground plane of JLC04161H-7628. **The impedance and source-derived
routing dimensions remain unverified.** No RF_50 production constraint document
has been emitted from calculator data.

The [requirement map](../fixtures/telemetry-pod/requirement-map.json) preserves
this target and the missing evidence under `POD-IF-GNSS-ANTENNA`. Compilation
puts it in scope limitations, outside executable acceptance requirements.
`tests.test_requirement_map` checks that distinction; its native checks-only
test also verifies that compiled limitations survive into the run result and
evidence index.

## Captured calculator failures

The host fetched the [JLCPCB calculator page](https://jlcpcb.com/pcb-impedance-calculator/)
and public client on 2026-10-19 UTC (2026-10-18 local). Earlier workers stopped
before calculation submission under narrower authorization. Decision a2
(agent-463 under the owner's delegated authority, 2026-10-18 19:38) authorized
the bounded anonymous recipe lookup, supporting endpoints, calculation POST
and UUID-bound WebSocket transport, including ordinary anonymous initialization.

The authorized workflow fetched the model recipe through
`POST /api/jlcTools/impedance/selectPageImpedancePicture`, the standard
JLC04161H-7628 template, coating and copper configuration, and parameter ranges.
It submitted exactly three calculations through
`POST /api/jlcTools/impedance/calc`, receiving results through
`wss://tools.jlc.com/jlcTools/webSocket/{uuid}`. Each POST returned HTTP 200
and submission success. **Each bound solver result returned status 6 and a
null result.** The error suggests missing or invalid parameters; its precise
cause is unknown. No login, payment, credential, upload or captcha was needed.
The three-calculation budget is exhausted; no fourth calculation was submitted.

The fetched standard template and fixture agree on 0.2104 mm prepreg,
dielectric constant 4.4 and 0.035 mm F.Cu over the 0.0152 mm In1.Cu reference
copper. The requests
use H1=8.2835 mil and T1=1.3780 mil at the public client's four-decimal conversion
precision. T1 explicitly models the fixture's 0.035 mm copper rather than
silently using the client's 1.6 mil plated-copper default. The input gaps below
were selected for the bounded calculation; they are not calculated outputs.

| Submission | Target | Input gap | Recipe and variation | Observed result |
| --- | --- | --- | --- | --- |
| 1 | 50 ohm | 5 mil / 0.127 mm | Coated coplanar with lower ground; public client visible fields | Solver status 6, null geometry |
| 2 | 50 ohm | 6 mil / 0.1524 mm | Same recipe with its exact non-null hidden G1=20 and HZ0=53 fields | Solver status 6, null geometry |
| 3 | 50 ohm | 8 mil / 0.2032 mm | Public uncoated coplanar with lower ground recipe; visible fields | Solver status 6, null geometry |

Each request asks for inverse W1 calculation. Coating and taper inputs come
from the fetched recipes/configuration; neither alternative claims actual
mask or impedance qualification. Plans and the two bounded diagnostic amendments
were recorded in the worker result before the corresponding submissions.

[Capture metadata](../fixtures/telemetry-pod/rf-source-capture.json) retains
URL, UTC timestamp, tool, request and response identities, and every received
socket message and sent control frame hash. Raw vendor bytes stay in worker
scratch; they are not committed or an owner evidence store. The historical
`raw_captures` and `continuation` describe the earlier workers;
`anonymous_workflow` records the three actual calculations. Hashes establish
byte identity, not vendor authentication or committed offline replay. HTTP
submission success cannot supply calculated width or gap.

Completion requires a successful source-bound result with its selected coplanar
gap, calculated width, target, stackup and signal/reference copper identities.
Further host calculations need a new bounded plan. Only a successful result can
populate `RF_50` through the existing [net-constraint emitter](net-constraints.md).
Its `impedance_ohm: 50` must retain `recorded_not_enforced`; no check may claim
verified impedance. The pod RF maximum length also needs a captured design
bound. It remains a scope limitation; the fixture's 15 mm test bound does not
supply one. The spec's 500 mm antenna cable limit applies to the cable to the
SMA, not the PCB RF trace.

## Native mechanism controls

`tests.test_gnss_rf_path` generates original four-layer two-terminal boards
using `tests.routing_fixture`. U_GNSS and J_SMA are pad labels, not models of
the actual parts. It reuses the existing emitter, candidate capture, executor,
`geometry` `copper-width/1`, `routing_rules`, and evidence sealing. No second
parser, schema, solver or pipeline is added.

The test choices are **0.3 mm width, 0.2 mm clearance and 15 mm maximum length**.
They are synthetic bounds, not JLCPCB calculation values or pod requirements.
The controls establish:

- A 10 mm saved RF_IN path at the declared width passes width and length.
- A 0.299999 mm segment fails the recorded 0.3 mm width minimum.
- Moving the SMA pad to 40 mm and extending connected copper fails native length.
- Missing copper leaves length coverage unknown.
- Lowering the project width below the recorded minimum refuses scope/3 capture.
- The 50 ohm target remains `recorded_not_enforced` even when dimensions pass.

These boards contain no ground plane and are authored routed controls, not
router-produced proposals. They do not meet the source-bound routed-probe
deliverable. Native measurement, a width minimum and clearance defaults cannot
establish coplanar impedance, exact width/gap, return continuity, device matching
or actual pod acceptance.

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests \
  python3 -m unittest tests.test_gnss_rf_path tests.test_requirement_map -v
```

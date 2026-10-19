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

## Source capture preflight blocker

The host fetched the [JLCPCB calculator page](https://jlcpcb.com/pcb-impedance-calculator/)
on 2026-10-19 UTC (2026-10-18 local). The HTML contains no calculated width/gap
for JLC04161H-7628. The first worker stopped at the GET-only capture boundary;
the old cart calculator URL returned HTTP 404. Agent-463's delegated amendment
then authorized a bounded host-side POST to `/jlcTools/impedance/calc`, using
public dimensions, without login, payment, uploads or a cookie/session/token.

The continuation captured the public pages and client JavaScript again. The
client obtains `impedanceType` and `parameterData` through a separate POST to
`/jlcTools/impedance/selectPageImpedancePicture`; the static calculator HTML
does not supply that model recipe. Its calculation request carries a UUID,
and its result handler receives output over
`wss://tools.jlc.com/jlcTools/webSocket/{uuid}`. Those additional protocol
operations exceed the calc-only amendment. No model-catalog POST, WebSocket
session or incomplete calculation POST was attempted. This is a **request
preflight blocker**, not an observed server refusal: authentication requirements
and the availability of a synchronous alternative have not been tested.

[Capture metadata](../fixtures/telemetry-pod/rf-source-capture.json) records the
URLs, UTC times, tool and raw response hashes for both workers, plus the known
stackup dimensions and the missing model inputs. The public stackup page records
7628's dielectric constant as 4.4; that fact supplies no calculated width or gap.
No candidate geometry was submitted and no calculation response exists.
Raw HTML/JavaScript stays in worker
scratch; it is not committed or an owner evidence store. These identities
record the unsuccessful capture, not a calculator result or an offline replay.

Completion requires a source-bound model recipe and an authorized result
transport, then a genuine result with the selected coplanar gap, width, target, stackup,
signal/reference layers and copper identities bound to hashed raw bytes.
Only then can those dimensions populate `RF_50` using the existing
[net-constraint emitter](net-constraints.md). Its `impedance_ohm: 50` must retain
`recorded_not_enforced`; no check may claim verified impedance. The pod RF
maximum length also needs a captured design bound. It remains a scope limitation;
the fixture's 15 mm test bound does not supply one. The spec's 500 mm antenna
cable limit is for the cable to the SMA, not the PCB RF trace.

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

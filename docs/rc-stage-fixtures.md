# Original RC stage fixtures (AGENTEE-103)

The [stage fixtures](../fixtures/rc/stages/) provide original three-component RC
inputs for placement and routing, using owned symbols and land patterns. They
contain no stock library capture or FOME-MC design content. The ticket's exact
derivation of `fixtures/rc/control` remains blocked by that control's unresolved
license and publication rights ([origin inventory](file-origins.json)). This
alternative does not close the owner gate or qualify a complete rung-1 campaign.

| Variant | Saved boundary | Expected initial checks |
| --- | --- | --- |
| `unplaced` | Three footprints on a 10 mm staging row outside the 50 x 30 mm outline; no tracks, vias or zones | ERC and courtyard pass; placement DRC, containment and full DRC fail |
| `placed-unrouted` | Authored reference anchors; no tracks or vias; B.Cu `/GND` zone retained unfilled | ERC, placement DRC, courtyard and containment pass; full DRC fails on unconnected items |
| `seeded-placement` | C1 moved into R1's courtyard; J1 moved across the left edge; otherwise the placed-unrouted design | Native courtyard overlap, containment and board-edge defects; full DRC fails |

The network is J1.1 -- R1 10k -- OUT (J1.2) -- C1 100n -- GND (J1.3).
R1 uses the owned generic two-pad land pattern also used by C1. No manufacturer
package match, simulation result or fabrication suitability is claimed.

The two placed variants have identical zone definitions with independently
assigned zone UUIDs. Use each as a stage input, rather than as an exact
before/after pair for the placement write-scope check. Footprint identities,
values and connectivity are shared; each captured candidate has its own
immutable baseline and fresh checks.

Each variant has a scope/2 declaration with captured fixture requirements.
The independently authored [expectations](../fixtures/rc/stages/expectations.json)
describe initial outcomes; a failed fixture remains a production defect.
Placement DRC leaves unconnected items ungraded, and every scope also declares
full DRC. Native exit 0 does not establish a pass.

## Reproduce and qualify

From the repository root, with the standard native toolchain (KiCad 10.0.7,
ngspice and Bubblewrap), and `AGENT_EE_KICAD_ROUTE_CHECKOUT` set to a
[qualified pinned checkout](routing-adapter.md#pinned-compatibility):

```sh
PYTHONPATH=src python3 fixtures/rc/stages/generate.py --out build/rc-stages
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest tests.test_rc_stages -v
```

`--out` must be fresh. The existing networkless executor mounts the generator,
agent-ee sources and owned libraries read-only. Generation reuses
`schematic_emitter`, `board_build.read_netlist` and `board_build.construct`;
pcbnew applies the authored placements and unfilled zone. The output retains
executor and emitter records, source/tool identities, logs and every fixture
file's SHA-256. The committed [generation receipt](../fixtures/rc/stages/receipts/generation/)
identifies the run producing these boards. The native test regenerates every
project byte and checks geometry, native network, DRC and evidence seals.

To retain checks-only results per variant:

```sh
for variant in unplaced placed-unrouted seeded-placement; do
  PYTHONPATH=src python3 -m agent_ee.cli review \
    "fixtures/rc/stages/$variant.scope.json" --checks-only \
    --out "build/rc-stage-checks/$variant" --max-seconds 300
done
```

Each command exits 1, as the checks-only contract requires. Inspect `checks/`
and the raw reports under `work/`. Compact retained results are under
[receipts/checks](../fixtures/rc/stages/receipts/checks/); missing output and
failed execution are never expected defects.

The routing test uses AGENTEE-93 `routing_adapter.step` and the existing stage
receipt implementation. Its command invokes `kicad-route run --backend krt`
on a read-only capture of placed-unrouted. Set `AGENT_EE_KICAD_ROUTE_CHECKOUT`
to the adapter's qualified pin before running it. The test requires an observed
exit and actual `result.json`, backend, routing and validation records, without
asserting exit 0. The retained qualification is under
[receipts/routing](../fixtures/rc/stages/receipts/routing/).

Native tests use the standard `require_toolchain(native=True)` and, for
routing, `require_kicad_route()`. Missing tools use `toolchain prerequisite: ...`
skip reasons and fail under `AGENT_EE_REQUIRE_TOOLCHAIN=1`. Subprocess tests
exercise both behaviors with an empty PATH. Real check failures are never
caught as skips.

# RC stage fixtures (AGENTEE-103)

AGENTEE-103 has two fixture sets for the placement and routing stage
boundaries. Both are kept:

- [Control-derived variants](#control-derived-variants) in
  [`fixtures/rc/derived/`](../fixtures/rc/derived/) are the ticket's literal
  deliverable: pcbnew derivatives of `fixtures/rc/control` (origin T1, with
  embedded stock footprint data, T6). They became possible after the MIT
  decision for T1 cited in [PROVENANCE](../fixtures/rc/PROVENANCE.md#control-derived-stage-variants-agentee-103).
  Use them where a criterion names "the placed RC scaffold" of the reference.
- The original alternative in [`fixtures/rc/stages/`](../fixtures/rc/stages/)
  was built while the control's rights were unresolved. It uses owned symbols
  and land patterns, no stock library or FOME-MC content, independently
  authored geometry and the same topology and values. It remains the input
  for tests that must run on agent-ee-owned material only (for example
  `tests/test_placement_defects.py`).

The two sets share the circuit and the expected check outcomes per variant, but
not files, UUIDs, footprints or coordinates. Neither qualifies a complete
rung-1 campaign.

## Original alternative

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

## Reproduce and qualify the original alternative

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
receipt implementation. Its command invokes `kicad-route run --backend builtin`
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

## Control-derived variants

[`fixtures/rc/derived/generate.py`](../fixtures/rc/derived/generate.py) loads
the routed control board in pcbnew inside the networkless executor and saves one
board per variant; schematic, project and library tables are byte copies of the
control. Footprint, pad and zone identities, nets and the 20 x 12 mm outline are
the control's.

| Variant | Saved boundary | Expected initial checks |
| --- | --- | --- |
| `unplaced` | R1, C1, J1 on a staging row at x = 125/135/145 mm, outside the 96..116 x 96..108 mm outline; tracks, via and zone removed | ERC and courtyard pass; placement DRC, containment and full DRC fail |
| `placed-unrouted` | Control positions; tracks and the GND via removed; B.Cu GND zone kept, unfilled | ERC, placement DRC, courtyard and containment pass; full DRC fails on unconnected items |
| `seeded-placement` | C1 at (108,100) mm overlapping R1's courtyard; J1 at (95.5,100) mm across the left edge | Native `courtyards_overlap` (C1/R1), J1 copper and silk edge clearance, containment defect; full DRC fails |

Each variant has a scope/2 declaration (`fixtures/rc/derived/<variant>.scope.json`)
whose requirements in [`requirements/`](../fixtures/rc/derived/requirements/) and
[expectations](../fixtures/rc/derived/expectations.json) were authored before any
check ran. The scopes capture the stock symbol and footprint libraries the
control uses, as `fixtures/rc/control.scope.json` does.

```sh
PYTHONPATH=src python3 fixtures/rc/derived/generate.py --out build/rc-derived
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest tests.test_rc_derived -v
for variant in unplaced placed-unrouted seeded-placement; do
  PYTHONPATH=src python3 -m agent_ee.cli review \
    "fixtures/rc/derived/$variant.scope.json" --checks-only \
    --out "build/rc-derived-checks/$variant" --max-seconds 300
done
```

The test regenerates every fixture byte and the generation receipt's sources
and edits, checks the identity and geometry against the control, runs the six
checks per variant with sealed evidence, and runs `kicad-route run --backend
krt` on a read-only capture of placed-unrouted through `routing_adapter.step`.
Router exit and `result.json` are required to exist; exit 0 is not asserted.
Retained receipts: [generation](../fixtures/rc/derived/receipts/generation/),
[checks-only](../fixtures/rc/derived/receipts/checks/) (each command exited 1, as
checks-only requires) and [routing](../fixtures/rc/derived/receipts/routing/)
(router exit 0, routing `COMPLETE`, validation `PASS` on the 5f1c072 pin; the
routed board is not retained and routing grades no acceptance). Missing tools
skip with `toolchain prerequisite: ...`, or fail under
`AGENT_EE_REQUIRE_TOOLCHAIN=1`.

# Native CAN pair routing probe (AGENTEE-185)

The owned [probe](../fixtures/telemetry-pod/can-pair/probe.py) composes the
existing net-constraints emitter, dedicated routing adapter and routing_rules
check. It routes CAN+/CAN- through a captured CAN netclass using the reviewed
kicad-route pin a132899 and `diff_pairs: "auto"`. It does not generate or qualify
an actual pod.

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
  AGENT_EE_KICAD_ROUTE_CHECKOUT=/path/to/qualified/kicad-route \
  python3 -m unittest tests.test_routing_adapter tests.test_can_pair -v

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 \
  fixtures/telemetry-pod/can-pair/probe.py \
  --checkout /path/to/qualified/kicad-route --out /tmp/new-can-pair-probe
```

Every output directory must be new. Native generation, routing, checks and the
negative control write only there. The router checkout/state are read-only
sandbox mounts. No model or network call is involved.

## Declared evidence

- [Net constraints](../fixtures/telemetry-pod/can-pair/net-constraints.json):
  CAN+/CAN-, 0.25 mm track width, 0.2 mm clearance, 0.35 mm pair gap.
- [Native gap rule](../fixtures/telemetry-pod/can-pair/gap.kicad_dru):
  0.34–0.36 mm, explicitly enforced. Native netclass defaults alone would not
  establish a gap-range check.
- [Routing rules](../fixtures/telemetry-pod/can-pair/routing-rules.json):
  explicit member length bound and 0.15 mm skew, evaluated independently through
  native DRC with the existing rule-loading witness.
- `route/receipt.json` retains the captured input hashes and dedicated adapter
  identity; `route/run/routing/attempt/result.json` carries the native `/route/2`
  pair report. Adapter replay verifies the retained attempt before adoption.
- `positive/` grades the exact delivered board, without extending copper to pad
  centres. `negative/` captures the same rules and a connected leg exactly 5 mm
  longer. Its skew must fail; this negative is not a routable candidate proposal.

The two-terminal probe measures 9.8125 mm of copper on each member despite 10 mm
between pad centres. The checker retains KiCad's native length meaning and the
captured project settings, including via-height configuration. Its topology
proof now admits a unique endpoint inside a native SMD pad shape, with existing
branch/collision guards. No fictitious pad-centre extension is included.

## Missing or uncoupled coverage

Adapter reports add `semantics.pair_coverage`; replay rederives it from retained
request, attempt and result bytes. Existing reports without the diagnostic remain
readable, and replay derives the same conservative result.

`reported_coupled` requires an eligible native result, COUPLED/ROUTED status,
routed members, finite consistent reported lengths/skew and zero gap findings.
It is provenance, **not an acceptance pass**: `acceptance_ready` stays false and
fresh routing_rules checks remain necessary. ALREADY_CONNECTED reports preserve
input connectivity but do not prove this invocation built coupled copper.
Rejected pairs, empty plans, missing tools/results or unsupported routing remain
unknown. A single-ended CAN run that completes routing explicitly records
**CAN pair routed single-ended** and cannot claim qualified pair geometry.
The native single-ended control completes both nets but fails width/gap validation;
its report retains that limitation and refuses a proposal.
There is no automatic fallback from a failed pair request to single-ended routing.

## Limits

The fixture bounds are software-test choices, not owner-approved pod dimensions,
impedance or delay requirements. Two original pad rows and an empty schematic
exercise routing behavior; no transceiver, real connector, source qualification
or schematic/PCB parity acceptance is asserted. Other native DRC findings remain
visible. Zero gap findings does not establish coupling percentage or impedance.

The existing routing_rules topology excludes zones anywhere on the board,
branched nets, arcs and multilayer paths. Thus an actual pod containing pours or
branched CAN protection/termination cannot inherit this fixture's pass; it needs
separately supported topology/checks or an explicit unresolved limitation.
Source review, full pod ERC/DRC, bench obligations and fabrication remain open.

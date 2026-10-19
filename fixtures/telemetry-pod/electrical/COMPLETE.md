# Complete candidate circuit inputs

AGENTEE-207 extends the retained eight-component core to102 purchased physical
components plus six manufactured programming/sense pads. The original core
recipe and its historical outputs remain intact. This is a candidate circuit;
all canonical requirements and physical acceptance conditions remain in force.

## Reproduce

Use the exact source locks and reviewed producer revision. Existing output
paths are refused. No network request, stock inference or fabricated capture
record is performed by these recipes.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 fixtures/telemetry-pod/electrical/build_complete.py \
  --out /tmp/pod-complete-new \
  --root core=/tmp/agent463-pod-electrical-inputs \
  --root repo=. \
  --root flat=/tmp/agent463-ee176-flat/evidence \
  --root kicad=/usr/share/kicad \
  --root power=/tmp/agent463-pod-power-research \
  --root active=/tmp/agent463-pod-active-cad \
  --root antenna=/tmp/agent463-pod-antenna-inputs \
  --root capacitor=/tmp/agent463-pod-capacitor-closure
```

Roots can point to relocated retained captures; every source byte is hash
checked. Keep whole acquisition/catalog batches and their recorded failures.
Vendor PDFs, stock libraries and both CAN source instances remain external.
The recipes and numerical metadata do not grant new redistribution rights.

`parts-electrical.json` is the existing parts/1 manifest. Its qualification
replays each real part record, stock response, captured datasheet and CAD
assignment, including exposed-pad and ground-stitch declarations. Both actual
connectors have positive historical stock; neither receives a fabricated
NO_LCSC or unavailable-part hand-fit declaration. Assembly capability is separate.

The manifest also includes six `bare_copper_probe` entries. Their exact captured
symbol/footprint hashes and declared native nets are in `probe-selections.json`.
Its historical native observation is a provenance pointer, not a replacement for
checking the newly emitted native netlist. All six declared native names have a
leading slash, including `/GND` and `/+3V3`. No alias inference is performed.
The existing binding producer checks all 108 physical component refs and writes
`intent.json` and `binding-fill.json`; five virtual power markers have no part ref.
`part-deviations.json` is explicitly empty. Source-property adaptations remain
visible separately and do not fabricate unavailable-part assembly exceptions.

`intent.unbound.json` uses existing intent/1. The flat CAN compiler binds both
source instances, selects only U100/C100/D100/R100 and records the source-to-
delivered mapping. `can-library-regeneration.json` makes the actual regenerated
library hashes explicit while preserving historical core captures. The reviewed
legacy property correction changes native '~' semantics without suppressing
ERC. `part-property-adaptations.json` retains original source purchasing fields
before the canonical binding producer replaces their representation.

`pin-allocation.actual.json` projects every resolved physical/virtual pin and
its declared net or NC from the existing resolver. It is informational, not an
independent electrical proof. `block-instances.json` retains immutable source
part identities, distinct from actual part_ref selections.

`requirements/io-map.actual.csv` derives only CAN native P/N names and the four
sensor-return GND Net cells from the pinned188 input. Exact originals and hashes
remain under core/; logical signal names remain unchanged. The source CSV's TBD
MCU fields are historical unresolved declarations. Actual indirect CAN/AFE
paths are in the complete intent and pin inventory;188c must not reinterpret
these as direct conductors or as proof of transfer function.

## Native evidence

Emit the bound circuit:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 fixtures/telemetry-pod/electrical/emit_complete.py \
  --bundle /tmp/pod-complete-new --out /tmp/pod-native-new
```

This calls the existing emitter, exports native XML, runs ERC with all severities,
and compares exact topology using existing native/resolved readers with every
physical reference anchored. Reports and process logs are retained. Any ERC
finding or nonpassing graph comparison makes this command nonzero. No project
severity, exclusion or DRC limit is relaxed. Native-clean graph evidence does
not establish component suitability or hardware results. An explicitly requested
`--unbound-draft` remains an intermediate topology diagnostic without bindings.

The six pads are native in_bom=no/on_board=yes exposed copper with no paste.
They have no supplier identities. The reviewed manufactured-feature contract
binds their CAD and exact pin/net selectors. A later campaign must run the native
netlist check and a passing `parts_subset` on this same manifest/netlist before
`part_binding` or `fab_export` admit probes through `probe_manifest`. A direct
membership diagnostic or binding-fill report cannot substitute for those sealed
prerequisites. Board geometry, probe access and BOM/CPL omission remain subject
to their respective native checks; do not disguise probes as power/DNP parts.

## Decisions and remaining gates

Read [complete-decisions.md](complete-decisions.md). It records the adopted
power topology, separate sensor/CAN rails, source-bound IMU Mode1 restriction,
GPIO46 resistor strap, OPA reference-driver model, exact connector evidence,
source applicability gaps and physical obligations.

Two topology-review distinctions remain explicit: C52 supplies the ADC's local
1uF recommended by MCP3204 section6.4; the power proposal prose's additional
dedicated ADC100nF is not separately allocated in this circuit. Do not claim
that local pair was preserved or that another rail capacitor has proven local
placement. The GNSS limiter's FAULT output is pulled up but not monitored by
firmware; no fault-reporting behavior is implemented by that connection.

The canonical166 identity compiler cannot invent regulator MPNs in the Power
row, which deliberately delegates choices. Actual selections remain explicit in
parts/1 and the engineering decisions; the old core decision projection remains
historical/unresolved. Off-board harness plug/contacts/cavity plugs, inline fuse,
external antenna and programming equipment remain separate delivery obligations.

Core105°C conflicts, vertical/tall AMPSEAL versus the canonical mechanical
wording, RF land-pattern qualification, effective capacitance, reference
settling, thermal and fault performance remain open. Source149/204 qualification,
full requirement/check composition, owner PREVIEW_REQUIRED, placement/routing,
assembly/fabrication and physical bench8/9 gates are not completed by this input
recipe or an ERC pass. `done:false` is retained throughout.

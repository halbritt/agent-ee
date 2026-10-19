# Bare copper probe features

AGENTEE-210 adds `kind: "bare_copper_probe"` to the existing
`agent-ee.parts/1` union. A probe is required manufactured copper with a declared
pin and net. It has no purchased-part identity. Arbitrary BOM exclusions and
`TP` references do not select this path.

A feature entry has exactly these fields:

```json
{
  "kind": "bare_copper_probe",
  "id": "uart-pogo",
  "role": "UART programming and rail sense",
  "required": true,
  "instances": [
    {"sheet": "/", "reference": "TP1", "net": "POD_UART_TX", "native_net": "/POD_UART_TX"}
  ],
  "lib_id": "PodFeatures:PogoPad",
  "footprint": "PodFeatures:PogoPad_D1.5mm",
  "probe": {
    "symbol": {"path": "PodFeatures.kicad_sym", "sha256": "<captured SHA-256>"},
    "footprint": {"path": "PodFeatures.pretty/PogoPad_D1.5mm.kicad_mod", "sha256": "<captured SHA-256>"},
    "pin": {"number": "1", "name": "PROBE", "electrical_type": "passive"}
  }
}
```

The example hashes are placeholders. `net` is the exact intent label;
`native_net` is the exact expected KiCad export name, including any sheet prefix.
The qualifier never matches basenames or infers global/local aliases. Symbols
and footprints must also be exact members of the manifest's existing CAD
assignments and dependency closure. Each selector is unique and required.

The captured symbol must have one passive pin and native `in_bom=no`,
`on_board=yes`, without inheritance or power-symbol semantics. The footprint
must have one circular SMD pad at its origin on `F.Cu` and `F.Mask`, a nonnegative
mask margin, and exact `smd exclude_from_bom exclude_from_pos_files` attributes.
Extra pads, drill, paste, zones, copper graphics, off-board/DNP flags, supplier
fields and unsupported geometry are refused. This version supports front pads;
other shapes and sides require an explicit future contract.

`binding_fill` checks component allocation and intent connectivity, then writes
only `AGENT_EE_Probe` for feature bindings. The reserved property records exact
CAD hashes, symbol body, pin, geometry, reference and nets. Ordinary parts keep
their supplier, datasheet and parameter rules. The emitter admits the bounded
structured property and preserves the captured symbol's native flags.

The property is identity evidence, not source approval. `parts_subset` replays
the captured manifest and compares required native membership, embedded symbol,
flags, pin and exact net. To admit probes, `part_binding` requires
`probe_manifest` and an earlier `netlist_check`; `fab_export` requires
`probe_manifest` alongside its existing `netlist_check`. Their capabilities
protect the manifest and complete dependency closure as immutable inputs.
A marker without this source custody fails closed.

Board construction and forward annotation verify the captured footprint bytes
and the saved native pad geometry, flags and net. Part-binding independently
checks schematic and board flags and required presence. Fab export validates
both sides before omitting probes from BOM/CPL. The physical board inventory
still includes them, and native copper/mask exports retain them. Fab manifests
with probes add `probe_features`, containing the qualified declaration records;
`fab_consistency` rederives those records and verifies saved geometry. The
ordinary non-DNP exclusion policy remains in force for all purchased components.

Scaffolding retains probe selectors. The RC-only rung-1 parts producer explicitly
refuses this kind. Pin-contract checks cannot use probes as manufacturer or
part-number evidence; interval/catalog assertions cannot select them as catalog
parts. Existing `dfa_dft` declarations can name their pads for access and coverage
without treating that result as purchased-part qualification.

`tests.test_probe_features` uses the frozen, original six-pad tracer with three
synthetic purchased components. Its retained freeze binds TP1/POD_UART_TX,
TP2/POD_UART_RX, TP3/GND, TP4/MCU_EN, TP5/MCU_BOOT and TP6/+3V3. These are
pipeline contract fixtures; they establish no telemetry-pod design acceptance,
pogo-fixture suitability, finish, spacing, physical programming or bench result.

Probe admission also requires an earlier, passing `parts_subset` check using the same manifest and native netlist. Capabilities enforce that ordering; runtime consumers verify its sealed report and exact source hashes.

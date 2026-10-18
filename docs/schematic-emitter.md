# Schematic emitter

`agent_ee.schematic_emitter` (AGENTEE-40) writes wire-free KiCad 10
schematics and a project file from captured connectivity intent: one file for
a flat design, or one file per sheet for a hierarchical design (see
[Hierarchy](#hierarchy)). It is a helper of the sole implementation writer, not
a check: its receipt is diagnostic, and native ERC, netlist export and parity
checks on the captured output decide whether the output is correct.

```sh
PYTHONPATH=src python3 -m agent_ee.schematic_emitter intent.json \
  --layout layout.json --captures captured-inputs \
  --out build/emitted --receipt build/emitted-receipt.json
```

Exit 0 means the project was written; exit 1 means it was refused and no
project directory exists. The receipt is always written to a new file. An
existing output directory is refused. The emitter reads only the declared
`.kicad_sym` captures and never searches installed libraries or tables.

## Inputs

- Raw `agent-ee.connectivity-intent/1` or `/2` ([connectivity intent](connectivity-ir.md)).
  The emitter resolves it with `connectivity_ir.resolve_intent` in the same call.
  Anything other than a complete, passing resolution is refused, with the
  resolver findings prefixed `resolution_`. An authored resolved record is
  never a source of pin geometry. The Python API accepts an optional
  `expected_resolved`; it must equal the fresh resolution exactly.
- The captured symbol library bytes the intent declares, by relative path.
- An `agent-ee.schematic-layout/1` record:

```json
{
  "schema": "agent-ee.schematic-layout/1",
  "namespace": "6f0f7f0e-5c43-5b8e-9a51-2a3f2c1d0e40",
  "project": "regulator",
  "transforms": {"reg": {"rotation": 90, "mirror": "x"}}
}
```

`namespace` is a canonical lowercase UUID chosen once per project. `project`
is the file stem. `transforms` is optional and keyed by logical component ID
(for a hierarchical intent, the resolver's scoped ID, such as
`["filter","c_in"]`);
rotation is 0, 90, 180 or 270 degrees counter-clockwise, and mirror is `null`,
`"x"` or `"y"`, applied on the sheet after rotation. Equivalent spellings are
written the way KiCad writes them (for example mirror `x` at 180 becomes mirror
`y` at 0).

The Python API can also write project `sym-lib-table` and `fp-lib-table` rows
that point at `${KIPRJMOD}` paths. The emitter does not write board files.

### Library subsets and emission cost

AGENTEE-172 shares one content-addressed library parse across fresh intent
resolution, symbol contexts and all sheet plans in an emission. The cache is
local to that call and keyed by the SHA-256 of the actual bytes. Every declared
path and hash is still checked, including unused libraries. The existing
quote-preserving scan compares against that shared tree before serialization;
selected definitions are copied before editing. No tree survives into the next
emission. `build_context` also parses each library only once in a standalone call,
including inherited properties.

Use [symbol capture](symbol-capture.md) to keep captured inputs small. The
generator and both rung-1 scaffold builders now capture subsets by default.
Intent and emission receipts name the subset SHA-256; `symbol-captures.json`
retains each existing capture receipt, including the full source SHA-256 and
selected symbol/ancestor hashes. Whole and subset libraries produce identical
project bytes for the same selection and layout; their provenance receipts
differ because they identify different inputs.

The original 130-component benchmark uses seven ICs, 40-pin headers and
capacitors. Stock library bytes stay in the local run directory. Each run
records library hashes, three complete emission timings without warmup, output
hashes, host/tool identities and a sandboxed native export comparison of every
connected and no-connect terminal. Capture and workload construction are timed
separately. The 60-second target is recorded, never asserted in CI:

```sh
PYTHONPATH=src python3 scripts/benchmark_emitter.py --out build/emitter-benchmark
```

This synthetic workload measures emission; it is not a telemetry-pod design or
an electrical qualification. `tests/test_schematic_emitter.py` separately checks
byte-identical whole/subset emission for 20 parts, refusal of a missing symbol
by name, one parse per content hash across hierarchical sheets, and hash drift
on a subsequent emission.

## Output

`<project>.kicad_pro` and `<project>.kicad_sch` (format version 20260306,
KiCad 10.0):

- Each selected unit is one symbol instance. Exact captured symbol
  definitions are embedded under `lib_symbols` as `Library:Symbol`.
  For `extends` symbols (AGENTEE-135), the emitter consumes the shared resolver's
  validated ancestor-to-child source chain. Parent graphics and pins are embedded
  under the selected symbol's unit names; child properties and explicit flags
  override inherited values. The embedded definition has no `extends` dependency.
  Missing parents, cycles and replacement graphics/pins fail resolution before
  any output is built. Captured library bytes and source locators stay unchanged.
- Every connected pin endpoint gets a net label at the exact transformed pin
  coordinate. Nets driven by a global `power_in` power symbol use global labels;
  other nets use local labels. Explicit no-connect pins get no-connect markers.
  No wires are drawn.
- Stacked pins (distinct pins of one unit on one connection point, as stock
  MCU symbols stack their VSS pins) are emitted only when the intent puts them
  all on one net. KiCad joins them natively, so one label serves the stack.
  A stack spanning two nets, or holding a no-connect pin, is refused.
- Power and flag symbols appear only where the intent declares them as
  components. The emitter never adds a power symbol or flag.
- Power symbol and flag instances are written `in_bom no` and `on_board no`,
  as eeschema places them, even though the stock library definitions say
  `yes`. The embedded `lib_symbols` copy keeps the captured flags, and
  `exclude_from_sim` and `in_pos_files` come from the library. The staged
  presence probe ([staged campaign](staged-campaign.md)) therefore counts the
  same physical references as the native netlist (AGENTEE-123).
- Pin offsets must be exactly representable on the native 100 nm coordinate
  lattice. Coordinates are never snapped. The editor's customary 1.27 mm grid
  is not an electrical-connectivity restriction; native ERC may still report
  off-grid endpoints.
- Symbol anchors sit on a 2.54 mm grid, in rows from left to right with
  wrapping. The smallest landscape sheet from A4 to A0 that holds the content
  is chosen. Space is reserved for labels and the reference and value text, but
  the packing does not prove the absence of visual overlap.
- Element order and formatting follow KiCad's own writer. In the native tests,
  `kicad-cli sch upgrade --force` rewrites the file to identical bytes except for
  the generator name, and `kicad-cli sch upgrade` reports that nothing needed
  upgrading.

### Stable identity

All UUIDs are `uuid5(namespace, JSON array)`, with the array written in compact
form:

| Object | Name array |
| --- | --- |
| Root sheet | `["sheet","/"]` |
| Symbol instance | `["symbol", id, unit]` |
| Instance pin | `["pin", id, unit, number]` |
| Label, global label, no-connect | `[kind, id, unit, pin]` |

Only the layout namespace and the logical component ID enter these names.
References, positions, input order and design hashes do not, so adding a
component leaves every existing symbol, pin and root UUID unchanged. Instance
paths are `/<root uuid>`. Repeating an emission, or emitting from reordered
input, gives identical bytes.

## Hierarchy

A `connectivity-intent/2` design resolves to `resolved-connectivity/2`
(AGENTEE-39). The emitter reads its `hierarchy`, `ports`, per-sheet components
and `local_nets` segments with their resolved labels. It does not join, rename
or infer anything from names, and it never flattens. Each sheet occurrence
becomes its own file, named by the occurrence's `file`:

- Symbols, labels and no-connect markers of a sheet are placed in that sheet
  as in a flat design. A segment's label is its `local_nets` label.
- Each declared port becomes one pin on the child's sheet symbol in the parent
  file, with the port's name and direction. Pins sit on the left edge of the
  sheet symbol in name order. The parent segment's label is placed on the pin's
  connection point. A segment with no pins of its own (a pass-through) is
  labelled only at its children's sheet pins.
- The child segment a port binds is labelled with hierarchical labels, named
  after the port, on every connection point of that segment. All other
  segments use local labels, so internal nets with the same name on different
  sheets stay separate.
- The root file is `<project>.kicad_sch` and holds the `sheet_instances`
  record. Pages are numbered depth first, children in local ID order.

Identity follows the frozen AGENTEE-39 contract:

| Object | Name array |
| --- | --- |
| Sheet document (root UUID for the root file) | `["document", file]` |
| Child sheet symbol | `["sheet", parent file, child local_id]` |
| Sheet pin, and the parent label on it | `["sheet_pin", parent file, child local_id, port id]` and `[kind, "sheet_pin", parent file, child local_id, port id]` |
| Symbol, pin, label, marker | As above, with `file, local_id` in place of `id` |

A symbol's instance path is the root UUID followed by the sheet-symbol UUIDs of
its ancestors, so the native netlist sheet path of `filter` above is
`/<sheet uuid>/`.

Hierarchical refusals, in addition to the resolver's own (repeated sheet
files, hierarchical power symbols, undeclared or duplicate ports, singleton or
port-only nets, and port names that alias another class's label):

| Code | Condition |
| --- | --- |
| `port_label_alias` | A port name differs from its child segment's label. One segment would carry two names, so it is refused rather than renamed |
| `hierarchy_file_unsupported` | A sheet file is not a flat, safe `name.kicad_sch` in the project directory |
| `layout_schema` | The root sheet file is not `<project>.kicad_sch` |
| `malformed_sheet_name` | A child local ID cannot be a sheet name |
| `unanchored_segment` | A segment has no pin and no child sheet pin to carry its label |

## Refusals

Generation either succeeds completely or writes nothing:

| Code | Condition |
| --- | --- |
| `resolution_*` | The resolver rejected the intent: unknown symbol or library, duplicate component or reference, unknown or unaccounted pin, missing or changed captured library (execution `failed`), or a version 1 intent with hierarchy (execution `unsupported`) |
| `resolved_mismatch` | A supplied resolved record differs from the fresh resolution |
| `layout_schema` | Malformed layout or table rows, or a transform for an unknown component |
| `unsupported_symbol_node`, `unsupported_graphic` | An unknown top-level symbol node, or unit bounds the [symbol context](symbol-context.md) cannot compute (such as degenerate arcs or `bezier`) |
| `unlocatable_pin` | Pin geometry differs from the symbol context, or a pin endpoint is not exactly representable on the native 100 nm lattice |
| `impossible_pin_endpoint` | Two pins would share one connection point, other than stacked pins of one unit on one net |
| `malformed_net_name`, `duplicate_net_name` | Net names must be unique and match `[A-Za-z0-9_+.-]{1,64}`, so bus syntax, markup and text variables cannot change connectivity |
| `malformed_reference`, `malformed_value`, `malformed_footprint` | References need a number and a `#` prefix exactly for power symbols; values refuse control characters, backslashes and `${`; footprints are empty or `Library:Name` |
| `power_net_mismatch`, `unsupported_power_symbol` | A `power_in` power symbol's value must equal its net's name; power symbols have one connected pin |
| `paper_overflow` | The content does not fit on A0 |

## Evidence

Tests: `tests/test_schematic_emitter.py`, using original fixtures in
`fixtures/generation` ([provenance](../fixtures/generation/PROVENANCE.md)).
Native tests run KiCad 10.0.7 `kicad-cli` through the production Bubblewrap
executor with networking unshared:

- The three-component regulator (connector, regulator with an explicit
  no-connect pin, capacitor, plus the ground symbol and power flags that its
  intent declares) gives zero ERC violations of any severity. Its native XML
  netlist equals independently written expectations.
- Sixteen instances of an asymmetric symbol cover all eight transforms, along
  with a two-unit symbol with common pins and a repeated pin name. Every pin
  connects natively as specified, and label coordinates match a hand-derived
  endpoint table.
- Swapped or shorted terminals change the native netlist.
- A regulator variant whose spare pin is stacked on its ground pin gives zero
  ERC violations, and both stacked pins appear on the native `GND` net. The
  same stack refuses when the spare pin is on another net or a no-connect.
  `tests/test_symbol_capture.py` qualifies the captured stock `STM32H723ZGTx`
  with its nine stacked VSS pins the same way (see
  [large-symbol capture](symbol-capture.md#qualification)).
- `tests/test_emitter_parity.py` passes the emitted output through the AGENTEE-41
  parity predicates: topology, components and instance anchors computed from
  the identity contract. Omitted, shorted and swapped terminals, an extra
  identical part and changed UUIDs each fail the matching predicate. The
  AGENTEE-41 modules are imported directly, so their absence is an error,
  never a skip.
- An owned two-level derived capacitor and multi-unit symbol inherit all physical
  pins and common units, retain parent properties and override the leaf Value.
  Native ERC has zero violations, native nets equal the base-symbol controls,
  and forced native saves reproduce the emitted bytes apart from the generator.
  The runtime-captured stock `STM32H742ZITx`, derived from
  `STM32H742Z_G-I_Tx`, resolves all 144 physical pins, passes native ERC with zero
  violations, and exports the expected supply, ground, VCAP and no-connect pins.
  Removing its captured parent refuses even when installed libraries are present.
- Runtime-captured stock `Device:L` and `Connector:Conn_Coaxial` (whose body
  graphics use circular arcs) emit, pass native ERC with zero violations, and
  reproduce byte-identically under a forced native re-save; all `Device:L*`
  inductor symbols compute complete bounds in `tests/test_symbol_context.py`
  (AGENTEE-171).
- The full regulator intent, including its declared ground symbol and two
  power flags, passes both native ERC and all three physical parity predicates.
  The shared parity adapter records the virtual `#` symbols that native XML
  omits; moving a capacitor terminal onto the wrong rail still fails topology.
  A scope/3 coordinator run retains the normalization in its sealed report.
- The same regulator, as the schematic subject of a staged scope/3
  (AGENTEE-48/88), passes the native presence probe: the bounded reader and
  KiCad's XML both give `C1, J1, U1`, and ERC, netlist and parity pass with
  sealed evidence. With the ordinary C1 instance rewritten to `in_bom no`/
  `on_board no`, the bounded reader leaves C1 out while KiCad's XML still lists
  it, so every staged check is `unknown` because the probe refuses (`bounded
  inventory disagrees with native reader`).
- `tests/test_schematic_hierarchy.py` emits an original two-level design
  (`two_level.*`): a root connector and capacitor, and a `filter` child with
  three capacitors. It has two shared nets, an internal `LOCAL_SAME` net on each
  sheet and one explicit no-connect. Its expectations were written before any
  hierarchical output existed. Native ERC reports no violations of any
  severity on either sheet, so there is no `hier_label_mismatch`. The native
  netlist equals the expected physical nets, and the two `LOCAL_SAME` nets stay
  separate. The sheet symbol has exactly one pin per shared net. Every connected
  pin and sheet pin carries exactly one label, on independently computed
  coordinates, and hierarchical labels appear only on port segments. Native
  sheet paths and symbol UUIDs follow the identity contract. Both files need no
  upgrade, and a forced native re-save reproduces them apart from the generator
  name. Deleting a sheet pin or adding an undeclared one produces a native
  `hier_label_mismatch`. A three-level design (`three_level.*`) with a
  pass-through middle sheet that has no parts, plus a sibling, has no ERC
  violations on any of its four sheets.
- The two-level export passes AGENTEE-41 topology, components and instance
  parity against a fresh resolution, with anchors computed from the identity
  contract. Swapped anchors fail the instance predicate. Shorting the child's
  internal label onto its hierarchical net, or deleting a sheet pin, fails
  topology. The three-level export passes topology and components.

## Limits

- Repeated sheet files (one file instantiated more than once) and power
  symbols in multi-sheet designs are refused by the resolver until their native
  semantics are qualified. Sheet files must sit in the project directory.
- A port's name must equal its child segment's label.
- Derived replacement graphics/pins, alternate body styles with pins, degenerate
  arcs, `bezier` curves and other graphics without qualified bounds are refused.
- KiCad's native XML omits virtual `#` power and flag symbols. The
  [parity adapter](netlist-parity.md) projects qualified single-pin markers out
  of the physical graph and records each omission. Nets must retain at least
  two physical terminals. Parity cannot prove the presence of a power marker
  or flag; native ERC remains a separate check.
- The staged presence probe refuses power symbols counted as physical
  (`in_bom yes` or `on_board yes`). Exclusion requires an embedded library power
  flag and explicit no/no instance flags. An ordinary part renamed with a `#`
  prefix is also refused, even when marked no/no, because KiCad would silently
  omit it from the native netlist. See [stage gate evidence](staged-campaign.md#gate-evidence).
- Label packing is conservative, not a visual-quality claim. The emitter does
  not draw wires, route, or choose parts or units.
- Writing the project file is a scaffold concern (AGENTEE-87). A campaign
  repair cannot add files or change immutable project files.

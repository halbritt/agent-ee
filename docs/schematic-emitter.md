# Schematic emitter

`agent_ee.schematic_emitter` (AGENTEE-40) writes wire-free KiCad 10
schematics and a project file from captured connectivity intent: one file for
a flat design, or one file per sheet for a hierarchical design (see
[Hierarchy](#hierarchy)). A flat design can instead request the
[functional presentation](#functional-presentation) (AGENTEE-222): titled
groups with short local wires. It is a helper of the sole implementation writer, not
a check: its receipt is diagnostic, and native ERC, netlist export and parity
checks on the captured output decide whether the output is correct.

The [owned telemetry-pod symbols](../fixtures/telemetry-pod/symbols/README.md)
qualify PESD1CAN-UX and LSM6DSO32 emission with native KiCad 10.0.7 ERC,
complete exported pin inventories and exact net terminals. Both drawings use
rectangles without arcs or inheritance. This pin exercise does not qualify the
pod's functional circuit.

```sh
PYTHONPATH=src python3 -m agent_ee.schematic_emitter intent.json \
  --layout layout.json --captures captured-inputs \
  --out build/emitted --receipt build/emitted-receipt.json
```

Exit 0 means the project was written; exit 1 means it was refused and no
project directory exists. The receipt is always written to a new file. An
existing output directory is refused. The emitter reads only the declared
`.kicad_sym` captures and never searches installed libraries or tables.

`part_binding.PARAM_Datasheet` retains the original zero-to-256-character
free-text validation, including empty strings and local filenames. Values that
begin with `{` after leading whitespace must instead be canonical structured
bindings; malformed object-like values never fall back to free text.
The structured path accepts at most 2048 UTF-8 bytes of canonical
`agent-ee.datasheet-binding/1` JSON (AGENTEE-293). This fixed bound gives captured
manufacturer URLs room beyond the 256-character free-text limit, while bounding
parsing and native property storage. The existing `part_binding.parse_param_value`
validates the schema, URL and PDF identity; `storage.canonical` defines the exact
JSON spelling. The emitter preserves the accepted bytes, including the full URL
and identity. Duplicate keys, noncanonical JSON, controls, backslashes and `${`
text variables are refused. A plain `Datasheet` longer than 256 characters is
allowed only when it equals the URL in a validated canonical `PARAM_Datasheet`
on the same component, and is itself safe ASCII of at most 2048 bytes
(AGENTEE-296). The complete companion JSON must also fit its 2048-byte bound;
this makes the usable URL bound smaller than 2048 bytes. No normalization or
prefix matching is applied. Other plain datasheets and `PARAM_*` properties keep
their existing 256-character limit. The probe identity property
retains its separate existing contract.

Legacy `Datasheet` and `PARAM_Datasheet` text whose first visible character is
`[` or `{` after whitespace and Unicode format (`Cf`) prefixes receives a typed
`suspicious_datasheet_text` receipt finding. This diagnostic preserves legacy
emission and its `info` outcome, including `[]`; it grants no structured-binding
status. Object-like `PARAM_Datasheet` text after ordinary whitespace still must
pass canonical structured validation. The binding validator independently
refuses arrays and invisible-prefixed JSON as malformed parameters.

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
  KiCad 10 `(property private "name" "value" ...)` metadata is keyed by
  its actual name, retained with its private modifier in the embedded definition,
  and omitted from instance fields. Public and private fields with the same name
  in one source are refused; inherited child fields override parents by name.
  Only the bare `private` token is a modifier; both the name and value must
  be quoted strings. A public field named `private` remains a public field.
  Malformed properties and private mandatory fields (`Reference`, `Value`,
  `Footprint`, `Datasheet`, `Description`), including child overrides, are
  refused with `unsupported_symbol_node`.
  Instance fields follow the library's property order. A standard field the
  library symbol lacks (`Footprint`, or a bound `Datasheet`) is written hidden
  in its KiCad slot, after the library's `Reference`, `Value` and `Footprint`
  that precede it; other new fields follow in name order. Ordinal readers such
  as KiBot take the third field as the footprint and the fourth as the
  datasheet whatever their names, so a custom binding that sorts first (such as
  `AGENT_EE_Probe`) must not take either slot (AGENTEE-444).
  Missing parents, cycles and replacement graphics/pins fail resolution before
  any output is built. Captured library bytes and source locators stay unchanged.
- Every connected pin endpoint gets a net label at the exact transformed pin
  coordinate. Nets driven by a global `power_in` power symbol use global labels;
  other nets use local labels. Explicit no-connect pins get no-connect markers.
  No wires are drawn in the default `rows` presentation; the
  [functional presentation](#functional-presentation) replaces some labels
  with short wires.
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
- Optional component `in_bom: false` in connectivity intent excludes that
  instance from the native BOM while retaining its library `on_board` state
  and leaving DNP unchanged. `true` explicitly includes a physical component;
  omitted values retain the captured library policy. Power symbols cannot
  request inclusion. These are boolean values, not KiCad `yes`/`no` strings.
  The older `agent_ee.schematic_emit` entry point refuses explicit component
  BOM policies instead of ignoring them.
  This emission policy does not qualify a fabrication package: current fab
  export still refuses non-DNP native exclusions, including marked assembly
  exclusions. Board propagation and fab BOM/CPL agreement remain unqualified
  for this policy (AGENTEE-289).
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

For repair candidates, [stable regeneration](schematic-regeneration.md) retains
actual prior placement and native identities while updating intent. A plain
emission still packs the complete sheet from scratch.

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

## Functional presentation

`presentation: "functional"` selects titled groups and short local wires for a
flat design (AGENTEE-222). It changes placement and drawing geometry while
preserving intent connectivity:

```json
{
  "schema": "agent-ee.schematic-layout/1",
  "namespace": "3b1f6c52-8a7e-5d2b-9c41-7e0d2a6b5f18",
  "project": "presentation",
  "presentation": "functional",
  "groups": [
    {"id": "input", "title": "Input connector", "components": ["j_in", "flag_vin"]},
    {"id": "regulator", "title": "Regulator and output filter", "components": ["reg"]}
  ]
}
```

`presentation` is `rows` (the default; bytes are unchanged from before
AGENTEE-222) or `functional`. `groups` is optional and needs `functional`. Each
group has a unique safe `id`, a 1-64 character `title` and a nonempty list of
component IDs, and a component belongs to at most one group. Implementation:
`agent_ee.schematic_presentation`.

- **Roles.** A *satellite* is a single-unit, non-power part with exactly two
  connected pins on two different nets and a reference prefix `R`, `C`, `L`,
  `D`, `FB`, `F` or `Y`, without an explicit transform. A *marker* is a power
  symbol, flag or other single-pin part, such as a test point, or a two-pin part
  with such a prefix that cannot be a satellite. A marker is never re-oriented. A non-power single-pin marker on
  a vertical pin gets an outward label lead and a horizontal label (AGENTEE-301),
  beyond the pin-name column. Every other unit is an *anchor*. A net is a *rail* when it has at least six terminals,
  carries a power symbol or flag, or is a global power net.
- **Groups.** Listed groups come first, in layout order, and then one group per
  remaining anchor, titled with its reference and value. Unlisted satellites
  and markers join the group that reaches them first, breadth-first over
  non-rail nets through other satellites. Parts that touch only rails (bulk
  and decoupling capacitors, flags, test points) join the anchor with the most
  pins on their lowest-fanout net. A part that no anchor reaches is placed in
  an `Unassigned parts` group; it is never silently attached elsewhere.
- **Local wires.** Each satellite is drawn beside the pin it serves and joined
  to it by one straight wire. A shunt part (one pin on a rail) hangs
  perpendicular to that wire, so several decoupling parts form a comb with a
  junction at each tap. A series part continues the wire in line when that fits
  nearest, so its far pin can carry a further chain. Satellites are oriented
  automatically; their Reference and Value are placed beside the body,
  horizontal and centred, never on the wire. A part hanging from a vertical
  wire may stack both texts on the side away from the wire when the default
  above/below split would cross it (AGENTEE-241). Shunt parts are placed lowest
  pin first, so downward combs form a staircase rather than blocking the next
  pin. Within a group, satellites are tried in hops from the group's own
  anchors over non-rail nets, so a divider leg follows the series part whose
  far pin it hangs from even when the layout lists both.
- **Labels.** The target pin keeps its label, so every net keeps a label with
  its intent name. Only a wired satellite's attach pin drops its label. The
  satellite's far pin, and every pin that is not wired, keeps its label as in
  `rows`. Pins on global power nets are never extended by a satellite wire,
  because the global label's flag sits on the connection line.
- **Pin-label fan (AGENTEE-241, AGENTEE-255).** On an anchor's top or bottom edge, a run of
  labelled pins one grid (2.54 mm) apart would read as one crowded column of
  vertical text against the pin numbers. Each pin of such a run that has not
  already been wired in the primary sweep gets a short straight wire out of the
  edge and a horizontal label at its end: the left half of the run points left,
  the right half right, staggered one grid per pin with the outermost pins
  nearest the body. Fanning runs before the in-line shunt fallback lets a
  fallback shunt continue from the fanned endpoint rather than past a vertical
  label, and each fanned run drops its original vertical label boxes by object
  identity so multiple runs on one anchor keep exact overlap reservations. A
  pin already wired to a satellite in the primary sweep keeps its place and
  label. A run is fanned whole or not at all, under the same collision rules as
  a satellite. The label keeps its UUID and net name; only its position and
  angle change.
- **Fail closed.** A placement is tried at up to 24 grid steps (2.54 mm) from the
  pin. It is refused if its wire would pass through any connection point,
  overlap another wire, or land on an existing point, or if its body, text or
  label would overlap anything already drawn in that cluster. Satellites that
  do not fit are retried in further sweeps while any sweep places one, since a
  later part may create their target. After dense edge runs fan out, a shunt
  part that still fits nowhere may continue an anchor pin's wire in line, which
  closes that pin to further parts. If any satellites in the group still remain
  unplaced (for example, when an earlier shunt hung across a neighbour pin's
  ray), the group sweep is retried with those unplaced satellites promoted
  ahead while the unplaced count strictly decreases (AGENTEE-255). A satellite
  that fits nowhere stays a labelled loose part in its group. Before any bytes are
  produced, `verify` rebuilds connectivity from the drawn pin endpoints, wires
  and labels and refuses (`presentation_conflict`) unless it equals the intent
  partition exactly. No-connect pins must touch nothing.
- **Frames and page.** Each group gets a dashed frame and a bold title. Clusters
  and loose parts inside a group, and the groups on the page, are packed
  bottom-left on a skyline in order, so the flow runs left to right in layout
  order. Each group is first packed at the width (one of several aspect ratios)
  that gives its frame the least area, which removes most whitespace inside
  frames (AGENTEE-241); if that set does not fit, one shared aspect ratio is
  tried at a time, as before. The smallest landscape paper from A4 to A0 that
  fits any of them is used. Text reservations are conservative estimates;
  only an untransformed symbol's left- or right-justified horizontal field is
  reserved on its library side alone, since no transform can flip it.
- **Identity.** Symbol, pin, label and no-connect UUIDs, references, values,
  footprints and embedded symbols are exactly those of the `rows`
  presentation. Wires are `["wire", id, unit, attach pin]`, fan wires
  `["wire", "fan", id, unit, pin]`, junctions
  `["junction", id, unit, attach pin]`, frames `["group_frame", kind, group]` and
  titles `["group_title", kind, group]`, where `kind` distinguishes `explicit`
  from automatic `component` groups. Fallback frames use
  `["group_frame", "unassigned"]` and titles use `["group_title", "unassigned"]`. Output is deterministic and independent of
  input order. A KiCad 10.0.7 forced re-save reproduces the bytes apart from the
  generator name.

Hierarchical intents and [stable regeneration](schematic-regeneration.md)
refuse the functional presentation (`layout_schema`).

### Real pod check

The real telemetry-pod intent (113 components, 62 nets) was re-emitted
read-only from the prepared inputs, without adoption. The `rows` emission is
byte-identical to the reviewed candidate, so it renders to the same PNG.
After AGENTEE-241 and AGENTEE-255, both the authored 12-group layout and
automatic grouping wire all 85 satellites (including R10, R13 and R26 in
automatic grouping). Six dense edge pins are fanned (U3 pins 23, 22 and 7; U4
pins 14, 13 and 12), with C40 continuing U4 pin 13 in line from its fanned
horizontal `SENSOR_REF_2V5` label. Total frame area drops from 1323 to 1148 cm²
(authored) and from 1234 to 1033 cm² (automatic). Both fit A2 instead of A1.
Native ERC gives the same 221 warnings as before, all of them library-table
warnings from the sandbox. The exported netlist is identical (116 native nets,
including the 54 no-connect singletons), and AGENTEE-41 topology and components
parity pass. After AGENTEE-301, detached fields and marker labels use the placement
rules below. Remaining visual limits: a pin wired to a perpendicular comb in the primary sweep keeps its
label at the pin (U4 pin 7), and group membership comes from structure or the
layout, not from datasheet roles.

### Detached text and native measurement (AGENTEE-301)

Functional placement checks an anchor or detached part's visible Reference and
Value reservations against its own labels. A colliding pair is placed
horizontally beside the body, searching at most 24 grid steps in each cardinal
direction. Both fields and explicit NC reservations participate in packing.
A non-power single-pin part with a vertical connection gets a straight outward
lead and a horizontal label beyond its pin-name column. The label keeps its UUID
and name; the endpoint remains connected by the new wire. Symbol orientation,
physical pins, instance identities, bindings and field visibility are retained.
The `rows` presentation keeps its existing byte contract.

`agent_ee.schematic_text` measures the actual ink in a native SVG export, including
stroke width. It associates visible fields and labels with native source
identities, and checks every label against every non-label text box on the
sheet, including pin names, pin numbers and group titles:

```sh
kicad-cli sch export svg --exclude-drawing-sheet -o build/svg/ build/emitted/design.kicad_sch
PYTHONPATH=src python3 -m agent_ee.schematic_text \
  build/emitted/design.kicad_sch build/svg/design.svg > build/text-overlaps.json
```

Exit 0 means no measured label/text overlap; 1 means overlaps; 2 means the SVG
cannot establish coverage. Missing or ambiguous visible text, unsupported stroke
paths and transformed stroke groups are errors. Empty native groups and exact
repeated drawing passes are handled explicitly. The supported geometry is native
KiCad stroke text with absolute M/L paths in millimetres; substitute system fonts
and other SVG producers are not qualified. Native multi-unit reference suffixes
and hidden library effects are included in matching. The diagnostic does not
check label/label or field/field overlap and grants no electrical acceptance.

The default pure planner uses conservative text reservations. To require native
bounds before publishing a flat emission, pass a new scratch directory:

```sh
PYTHONPATH=src python3 -m agent_ee.schematic_emitter intent.json \
  --layout layout.json --captures captured-inputs \
  --native-text-work build/native-text \
  --out build/emitted --receipt build/emitted-receipt.json
```

`emit_schematic(..., native_text_work=path)` provides the same option in Python.
Each draft is immutable and SVG export runs through the existing networkless
executor sandbox. Colliding Reference/Value fields move using their measured
ink bounds, with at most 24 grid steps per cardinal direction. Candidates avoid
all other measured text, symbol bodies and wires, stay inside their group frame
and page, and preserve font, angle, visibility and identity. A second export
must establish zero remaining overlaps. Failed exports, incomplete measurement,
unplaceable fields and remaining pin-name overlaps refuse the emission with
`presentation_text`; no project is published. The workspace retains both native
execution receipts and `text-placement.json`. Ordinary emission remains pure
when this option is absent. The native option also supports flat `rows` drafts,
while multi-sheet output is refused. Library tables may still be published
with the final files; measurement uses embedded symbols and exports only SVG. The bounded owned regression covers four rotations,
two mirrored orientations and short-pin test points using original symbol text.
The expanded pod captured at fome-mc `e4f710f` measured 17 label/text overlaps
before this repair and zero afterward across the whole sheet, including U31.
The regenerated baseline was byte-identical to the captured schematic. Native
XML graph and metadata compare equal, the native BOM is byte-identical, and ERC
issues compare equal after removing position metadata. These are presentation
observations, not electrical or manufacturing qualification. Real pod CAD and
render bytes remain in worker scratch, outside this repository.

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
| `layout_schema` | Malformed layout or table rows, or a transform for an unknown component; an unknown `presentation`, `groups` without `functional`, a malformed or duplicate group, an unknown or twice-grouped member, or `functional` for a hierarchical intent |
| `unsupported_symbol_node`, `unsupported_graphic` | An unknown top-level symbol node, or unit bounds the [symbol context](symbol-context.md) cannot compute (such as degenerate arcs or `bezier`) |
| `unlocatable_pin` | Pin geometry differs from the symbol context, or a pin endpoint is not exactly representable on the native 100 nm lattice |
| `impossible_pin_endpoint` | Two pins would share one connection point, other than stacked pins of one unit on one net |
| `malformed_net_name`, `duplicate_net_name` | Net names must be unique and match `[A-Za-z0-9_+.-]{1,64}`, so bus syntax, markup and text variables cannot change connectivity |
| `malformed_reference`, `malformed_value`, `malformed_footprint` | References need a number and a `#` prefix exactly for power symbols; values refuse control characters, backslashes and `${`; footprints are empty or `Library:Name` |
| `malformed_part_binding` | A binding overrides native identity or flags, violates the ordinary text limit, or has an unsafe, malformed, noncanonical or over-2048-byte `PARAM_Datasheet` value |
| `power_net_mismatch`, `unsupported_power_symbol` | A `power_in` power symbol's value must equal its net's name; power symbols have one connected pin |
| `paper_overflow` | The content does not fit on A0 |
| `presentation_conflict` | The functional arrangement's drawn pins, wires and labels do not reproduce the intent partition exactly, a wire passes through a connection point, or a no-connect pin touches a drawn connection (an internal guard; no output is written) |

## Evidence

Tests: `tests/test_schematic_emitter.py`, using original fixtures in
`fixtures/generation` ([provenance](../fixtures/generation/PROVENANCE.md)).
The long datasheet-binding regression uses a representative 152-character
Microchip URL with an original synthetic PDF identity. It tests the 278-byte
binding and exact 2048-byte boundary, paired plain URLs longer than 256 characters,
mismatched or invalid companions, invisible-prefix and array diagnostics, and native
forced-save byte preservation followed by XML netlist export.
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
- `tests/test_schematic_presentation.py` (AGENTEE-222) uses the owned
  `presentation.*` fixture. `rows` bytes for the regulator, transforms and dual
  fixtures equal hashes taken from main 9cb4f12, and an explicit `rows` equals
  the default. Functional output is byte-identical when repeated and when
  components, nets and terminals are shuffled. Symbol and pin UUIDs, embedded
  symbols and no-connect identities equal the `rows` emission. Exact
  label counts per net, axis-aligned wires that end only on pins, junctions or
  other wires, a junction only at a comb tap, an in-line series capacitor and a
  hanging filter capacitor are asserted, along with three non-overlapping titled
  frames that contain their members. An island with no anchor lands in
  `Unassigned parts`. Ten malformed layouts, a hierarchical intent and stable
  regeneration are refused. AGENTEE-241 and AGENTEE-255 add: a divider leg listed before its
  series part is still wired; a rotated connector's free edge pins fan out to
  staggered horizontal labels beside a wired pin, with 0 native ERC
  violations, a netlist equal to `rows` and a byte-identical forced re-save; two
  fanned edge runs on one anchor remove their own vertical label boxes and
  preserve every fanned horizontal label reservation; an edge run fans before an
  in-line fallback shunt capacitor attaches; a group sweep retries with unplaced
  series satellites promoted ahead of blocking shunts; the
  in-line shunt placement happens only as the fallback; stacked fields keep
  Reference above Value; and the chosen frames are never larger than any
  single shared aspect gives. The self-check refuses a drawn short, a split, a
  wire through a pin and a wire onto a no-connect pin. A `python -m` CLI run
  writes the API bytes. Native: zero ERC violations, the same XML netlist as
  `rows` and the hand-written nets, AGENTEE-41 topology, components and instance
  parity, a byte-identical forced re-save, and a changed native netlist when one
  wire end is moved onto ground.
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
- Label packing is conservative, not a visual-quality claim. In `rows` the emitter does
  not draw wires, route, or choose parts or units.
- The functional presentation is flat-only and opt-in; `rows` stays the default,
  so existing callers, hashes and stable regeneration are unchanged. Its text
  extents are estimates (about 0.9 em per glyph), not measured font metrics.
  Loose parts and markers keep `rows` label placement. Adjacent labels on an
  anchor's left or right side (already on separate horizontal rows), and a pin
  wired to a perpendicular comb in the primary sweep, keep `rows` placement;
  top and bottom edge runs fan before the in-line fallback. Roles come from reference
  prefixes and net structure, and group membership from the layout or from
  structure, never from datasheet semantics. It wires only satellites to
  anchor pins or satellite pins; it never routes nets between anchors.
- Writing the project file is a scaffold concern (AGENTEE-87). A campaign
  repair cannot add files or change immutable project files.

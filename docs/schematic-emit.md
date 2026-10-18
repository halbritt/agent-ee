# Rung-1 schematic emitter (experimental)

`agent_ee.schematic_emit` writes a KiCad 10 schematic project from a resolved
connectivity record without drawing any wires. It is a private experimental
stand-in for the rung-1 generator driver. Its parts-manifest and receipt schemas
carry `0-experimental` names and its receipt says
`"qualification": "experimental-unqualified"`. The
[native emitter](schematic-emitter.md) (AGENTEE-40) is now implemented alongside
it; the experimental rung-1 driver still calls this module. Do not register
this module as a check, and do not cite its receipt as qualification evidence.

The emitter does not run KiCad. Native ERC and netlist export of its output are
the caller's checks. Rung 1 runs them in `agent_ee.generate`.

## Command

```sh
PYTHONPATH=src python3 -m agent_ee.schematic_emit resolved.json \
  --libraries captured-libs --parts parts.json \
  --project-template fixtures/rc/control/rc_filter.kicad_pro \
  --name rc_gen --out build/rc_gen
```

Exit 0 writes these files into `--out`, which must be new or empty:

- `NAME.kicad_sch`
- `NAME.kicad_pro`
- `sym-lib-table`
- `fp-lib-table`
- `emit-receipt.json`

Exit 1 writes nothing. It prints `{"status": "refused", "reason": ...}` on
stderr. Argument errors exit 2.

## Inputs

- `resolved.json` is an `agent-ee.resolved-connectivity/1` record from
  [connectivity_ir](connectivity-ir.md). The emitter calls `validate_resolved`
  on it. A defect record is refused, and the refusal repeats the resolver's
  findings, including the real pin inventory when a pin is unknown.
- `--libraries` is a directory. Each library `path` in the record is read
  beneath it. Symlinks and paths that escape the directory are ignored, and the
  library is then reported as missing. Each file's sha256 must equal the
  record's value. For every component, the record's pins and source chain must
  equal a fresh `resolve_symbol` of the captured bytes. A mismatch is refused
  and the refusal lists the real pins.
- `--parts` is an `agent-ee.rung1-parts-manifest/0-experimental` object.
  `parts` is either an object keyed by reference or a list of objects that each
  have a `reference`. An entry's fields are either at the top level or under a
  `fields` object. The emitter uses `Footprint` (required, `LIBRARY:NAME`),
  `LCSC`, `MPN`, `Manufacturer` and `JLCPCB_Assembly`. The only value accepted
  for `JLCPCB_Assembly` is `"not_assembled"`. Other keys are not emitted. They
  are listed in the receipt as `ignored_manifest_keys`. Every component in the
  record needs an entry, and an entry for an unknown reference is refused. The
  manifest footprint is always the one emitted. The receipt records it with
  the intent's footprint.
- `--project-template` is a `.kicad_pro` file. The emitter copies it and
  changes `meta.filename`, the root `sheets` entry and
  `schematic.top_level_sheets` to match `NAME` and the root sheet UUID. A
  template that describes more than one sheet is refused.

```json
{
  "schema": "agent-ee.rung1-parts-manifest/0-experimental",
  "parts": {
    "R1": {"Footprint": "Resistor_SMD:R_0805_2012Metric", "LCSC": "C0000", "MPN": "example", "Manufacturer": "example"},
    "J1": {"Footprint": "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical", "JLCPCB_Assembly": "not_assembled"}
  }
}
```

The LCSC and MPN values above are placeholders, not catalog selections.

## Method

Components are placed left to right in natural reference order (C1, J1, R1,
and so on). Symbol origins are on the 2.54 mm grid, and rows wrap at the page
width. The page is the smallest of A4 to A0 that the layout fits on. All
symbols have rotation 0 and unit 1. For each pin, the emitter puts one item
exactly on the pin's connection point:

- A net named `/X` gets a local label `X`. The label angle points away from the
  symbol body.
- A net with a bare name such as `GND` gets a power symbol. The symbol must be
  in a declared library, carry a global `power` declaration, have that exact
  symbol name and Value, and have one pin at its origin. Each pin gets its own
  power symbol, rotated so that its body points away from the pin.
- A record no-connect gets a `no_connect` marker.

Before writing, the emitter checks that each connection point belongs to
exactly one net. A collision is refused.

If no intent terminal on a power-symbol net is `power_out`, the emitter adds
one free-standing `power:GND` + `PWR_FLAG` pair (or the equivalent pair for
that net). The pair asserts that the net is driven from outside the sheet, for
example through a connector. Without it, KiCad reports `power_pin_not_driven`.
The receipt lists each pair under `power_flags` with this reason. Nets named
`/X` never get a flag. On those nets, an undriven `power_in` pin remains a
genuine ERC finding.

Each used library symbol is embedded in `lib_symbols` and renamed
`Library:Symbol`. Derived (`extends`) symbols are flattened. The child's
properties and flags replace the parent's, and the unit sub-symbols take the
child's name. The S-expression writer keeps the quote marks found by a
quote-preserving parse, and the shared `symbol_library.parse_sexpr` must
produce the same tree. The writer uses KiCad's layout: tab indentation,
atom-only lists on one line, and consecutive `xy` points wrapped at column 99.
It reproduces `fixtures/rc/control/rc_filter.kicad_sch` and 221 of the 223
KiCad 10.0.7 stock symbol libraries byte for byte. The other two,
`FPGA_Xilinx_Virtex7` and `MCU_ST_STM32H7`, exceed the shared parser's
two-million-node limit. The parser refuses them as whole libraries; a
[large-symbol capture](symbol-capture.md) of the needed symbol is what the
emitters and `connectivity_ir` then use. `kicad-cli sch upgrade
--force` re-saves an emitted schematic with only these differences: the
generator lines, and the project name (only when the file is renamed).

UUIDs are `uuid5` over the record's `canonical_sha256` and an identity string.
Examples are `symbol:R1`, `pin:R1:2`, `label:R1:1` and `sheet`. Power-symbol
references are numbered `#PWR01`, `#FLG01` and so on in placement order.
Identical inputs therefore produce byte-identical outputs, including the
receipt. `Footprint`, `Datasheet`, `Description` and the manifest fields are
hidden symbol properties. Power symbols are written with `in_bom no` and
`on_board no`.

`sym-lib-table` lists the used symbol library nicknames as
`${KICAD10_SYMBOL_DIR}/<name>.kicad_sym`. `fp-lib-table` lists the footprint
libraries named by the manifest as `${KICAD10_FOOTPRINT_DIR}/<name>.pretty`.
For the rung-1 RC design, both tables are byte-identical to the fixture's
tables. The record's library names must therefore be stock KiCad nicknames.

## Receipt

`emit-receipt.json` has the schema
`agent-ee.rung1-schematic-emit-receipt/0-experimental` and
`qualification: experimental-unqualified`. It contains:

- Input sha256 values: the resolved file bytes, its `canonical_sha256` and
  `intent_sha256`, the parts manifest, the template and every library.
- Output paths and sha256 values.
- The embedded symbols, each with its library source chain and hashes.
- Placements, with intent and emitted footprints.
- `power_flags` and `ignored_manifest_keys`.
- The tool identity: this module's source sha256, the sha256 of
  `connectivity_ir.py` and `symbol_library.py`, and the Python version.
  `kicad_cli` is `null`, because the emitter does not invoke KiCad.

## Refusals and limits

The emitter refuses these inputs and never approximates them:

- Records that fail resolution or validation.
- Missing or changed captured libraries.
- Record pins that disagree with the captured symbol.
- Components that are absent from the manifest, and manifest entries for
  unknown references.
- Malformed footprints.
- Bare net names with no single matching power symbol.
- Unnamed nets, and nets whose name contains a hierarchical path.
- Power-flagged components in the record. Declare the power net by name
  instead.
- Multi-unit symbols, and any unit other than 1.
- Non-power symbols with hidden `power_in` pins, which KiCad connects
  implicitly.
- Derived symbols that replace graphics or pins.
- Power symbols that are not global.
- Layouts that do not fit on A0.

Global labels, hierarchical sheets, buses and wires are not produced.

## Validation and tests

```sh
PYTHONPATH=src /usr/bin/python3 -m unittest tests.test_schematic_emit -v
```

The tests use an owned minimal library for the refusal cases and for
determinism. They also emit the shared rung-1 RC intent from captured copies of
the stock `Device`, `power` and `Connector_Generic` libraries. These checks
pass:

- `kicad-cli sch erc --severity-all` reports zero violations.
- `kicad-cli sch export netlist --format kicadxml` gives exactly the node sets
  `/IN = {J1.1, R1.1}`, `/OUT = {R1.2, C1.1, J1.2}` and `GND = {C1.2, J1.3}`.
- A second emission is byte-identical, and the library tables equal the
  fixture's tables.

KiCad runs on a temporary copy, never in the repository. Without `kicad-cli`
or the stock libraries, the native tests skip with the standard
`toolchain prerequisite:` reason. They fail instead when
`AGENT_EE_REQUIRE_TOOLCHAIN=1` is set.

# JLCPCB fab package export

`fab_export` (AGENTEE-23, stage 7 of the [generator roadmap](generator-roadmap.md))
writes the files JLCPCB receives from the captured candidate. The pinned
`kicad-cli` runs inside the executor sandbox with no network. The check uses the
captured `.kicad_pcb`, plus the native XML netlist and part-binding fields that
an earlier `netlist` check retained. It does not run DFM, panelisation, stock or
price queries, and it does not place an order.
[fab_export.py](../src/agent_ee/fab_export.py) implements it.

## Parameters and coverage

Parameters are exactly `{"netlist_check": "<id>"}`. The named check must be an
earlier `netlist` check in the same scope. Its result must be `execution=ok` for
the same candidate, and its retained `design.xml` must match its
`connectivity.json`. Otherwise the check is `not_run`. v1 refuses any other key,
for example a layer or output override, as `unsupported`. In scope/3 the check
declares the predicate `fab-export:jlcpcb-package/1`, so it must be mapped to an
acceptance requirement. The board stays mutable candidate input. A captured
`.kicad_pro` next to the board becomes an immutable dependency.

## Native steps

Each step runs in its own `/work`, `work/<id>/steps/<step>/`, which keeps that
step's `execution.json`, console logs and raw native outputs (`out/`, before
timestamp normalisation) as evidence:

| Step | Arguments after `kicad-cli` |
| --- | --- |
| `version` | `version` |
| `stats` | `pcb export stats --format json --units mm -o /work/out/stats.json BOARD` |
| `gerbers` | `pcb export gerbers -o /work/out/ --layers <copper>,F.Mask,B.Mask,F.Paste,B.Paste,F.SilkS,B.SilkS,Edge.Cuts BOARD` |
| `drill` | `pcb export drill -o /work/out/ --format excellon --excellon-units mm --excellon-separate-th --generate-map --map-format gerberx2 BOARD` |
| `pos` | `pcb export pos -o /work/out/pos.csv --side both --format csv --units mm --exclude-dnp BOARD` |

The copper list follows the board's layer table: `F.Cu`, `In1.Cu`…`In(n-2).Cu`,
`B.Cu`. The count comes from the layer table, and the native Gerber job file
must report the same count. The flags were checked against KiCad 10.0.7
`--help`. KiCad exits 0 when a requested layer is not on the board, so the check
requires exactly one plot per expected layer.

## Staging layout

The layout follows the fab lane interface contract v1, under `work/<id>/fab/`:

| Path | Content |
| --- | --- |
| `gerbers/` | One plot per enabled fab layer, with native Protel extensions (`.gtl`, `.g1`…, `.gbl`, `.gts`, `.gbs`, `.gtp`, `.gbp`, `.gto`, `.gbo`, `.gm1`). Each plot must have the expected `%TF.FileFunction`, a coordinate format and a final `M02*`. The native `.gbrjob` is kept only in the step evidence. |
| `drill/` | Exactly one `*-PTH.drl` and one `*-NPTH.drl`, with `Plated,1,n,PTH` / `NonPlated,1,n,NPTH` file functions, and their Gerber X2 drill maps. Any other drill file is refused. KiCad writes a header-only NPTH file when the board has no unplated holes. |
| `bom.csv` | Header `Designator,Footprint,Quantity,Value,Manufacturer,Partnumber,"LCSC Part #"` |
| `cpl.csv` | Header `Designator,Mid X,Mid Y,Layer,Rotation` |
| `gerbers.zip` | Flat archive of `gerbers/` (sorted) then `drill/` (sorted). Each member has the date 1980-01-01, mode 0644, Unix create system and deflate level 9. |
| `pcb_summary.json` | Native `pcb export stats` JSON, with only `metadata.date` normalised |
| `manifest.json` | `agent-ee.fab-manifest/1`, written last |

Native timestamps are normalised so that two runs on the same captured board
give byte-identical Gerbers, drills, CSVs, summary and zip. These are the
Gerber `%TF.CreationDate` and `G04 ... date` lines, the Excellon `DRILL file ...
date` and `TF.CreationDate` comments, and the stats date. Each pattern must
occur exactly once per file. Any other remaining date text makes the output
malformed. The zip is byte-identical for the same zlib implementation.

### BOM

Part bindings come from the native XML netlist of the prerequisite, never from
the board. Power symbols (`#` references) are excluded. The symbol fields that bind a part follow AGENTEE-32: `LCSC` (`^C[1-9][0-9]*$`),
`Manufacturer`, and `Partnumber` (or `PartNumber` when `Partnumber` is unset;
two different values are refused). `Value` and `Footprint` (the full library id)
are also required.

#### DNP authority

The board's footprint `dnp` attribute decides which parts the package
assembles. The same flags drive the BOM, `--exclude-dnp` in the position file
and `designators.board_non_dnp`, so the three agree by construction:

- A netlist component with a board footprint is in the BOM exactly when that
  footprint is not DNP. Its schematic DNP flag does not matter here; a
  schematic-DNP part whose footprint is populated is bound from its netlist
  fields and listed.
- A netlist component with no board footprint keeps its schematic state. A
  schematic-DNP part off the board is left out. A populated part off the board
  stays in the BOM and then fails designator agreement (`malformed`).
- A board footprint with no netlist component has no binding and is never in
  the BOM, so it also fails designator agreement.
- `exclude_from_bom` or `exclude_from_board` on a component that is populated
  in either the schematic or the board is refused as `unsupported`.

The export never resolves a schematic/board DNP disagreement silently. Each
disagreeing designator is listed in `details.dnp_disagreements` as
`{"designator", "schematic_dnp", "board_dnp"}`, and a limitation names it.
`details.schematic_dnp` keeps the schematic DNP references. The package still
completes, because the board flags fully define it. The
[consistency gate](fab-consistency.md) reads the sealed netlist and geometry
itself and reports every such designator as a `designator_parity` defect. The
manifest is unchanged: `designators` holds only `board_non_dnp`. The board is
the authority because it is the artefact that the Gerbers, drill and position
file come from. Building the BOM from the schematic would let BOM and CPL
disagree whenever the two sources did.

Rows are grouped by LCSC number. Designators within a row are in natural order
(`C1,C2,C10`), and `Quantity` is the designator count. Rows are ordered by their
first designator. Members of one LCSC number whose footprint, value,
manufacturer or part number differ stay in separate rows that share the number,
so that the consistency gate (AGENTEE-73) can see the conflict.

Text is changed to ASCII with fixed rules: `µ`/`μ`→`u`, `Ω`/`Ω`→`ohm`,
`±`→`+/-` and `°`→`deg`. NFKD decomposition then removes combining marks.
Control characters, or characters that have no ASCII form after these steps,
are refused as `unsupported`. A part with a missing or malformed binding is
written with an empty `LCSC Part #` cell and listed in `details.flagged_parts`.
Any flagged part stops the package from completing.

### CPL

The native position file is rewritten: `Ref`→`Designator`, `PosX`→`Mid X`,
`PosY`→`Mid Y`, `Side` `top|bottom`→`Layer` `Top|Bottom`, and `Rot`→`Rotation`.
`Val` and `Package` are dropped. Coordinates are the native millimetre values in
absolute board X and negated board Y, on both sides, with no auxiliary/drill-origin
override. The anchor is the native footprint position. The consistency gate
compares these coordinates with the exact native geometry probe within 0.01 mm.
Rows are in natural designator order. The check applies **no rotation, offset or pin-1 policy**:
`Rotation` is the native KiCad angle. JLC rotation correction belongs to
AGENTEE-72. The BOM and position-file designators must both equal the board's
non-DNP footprints before a manifest is written. The DNP authority rule makes
them agree when the netlist and board name the same parts. A remaining
mismatch, such as a renamed footprint or a part missing from one side, makes
the output malformed and retains the files only in `fab.incomplete/`.

### Manifest

```json
{"schema": "agent-ee.fab-manifest/1", "candidate_id": "...",
 "board_path": "design/fab.kicad_pcb", "board_sha256": "<captured digest>",
 "tool": {"path": "/usr/bin/kicad-cli", "version": "10.0.7", "sha256": "..."},
 "files": {"bom.csv": "<sha256>", "gerbers/fab-F_Cu.gtl": "<sha256>", "...": "..."},
 "copper_layers": 2, "enabled_fab_layers": ["F.Cu", "B.Cu", "F.Mask", "..."],
 "drill": {"pth": "fab-PTH.drl", "npth": "fab-NPTH.drl"},
 "designators": {"board_non_dnp": ["C1", "C2", "C10", "..."]},
 "generated_utc": "2026-10-17T19:04:46Z"}
```

`files` holds the sha256 of every regular file in `fab/` except
`manifest.json`, keyed by staging-relative path. `board_sha256` is the
candidate-manifest digest of the captured board. The check also confirms that
the captured bytes still have that digest. `drill` gives file names inside
`drill/`. `designators.board_non_dnp` comes from the board's footprint inventory,
not from the position file. `tool.path` and `tool.sha256` identify the
`kicad-cli` on the sandbox `PATH`, the same file `doctor` and `runtime_files`
fingerprint. The check reads the manifest back and verifies it against the
staging before it reports `pass`. `verify_manifest` is the same comparison for
consumers.

## Outcomes

| Condition | Execution | Outcome |
| --- | --- | --- |
| Every step succeeded, every output validated, no flagged part, manifest verified | `ok` | `pass` |
| Missing or unparseable native output; BOM or position designators that disagree with the board; unnormalisable timestamps; board bytes that differ from the captured digest | `malformed` | `unknown` |
| Unsupported board feature, missing part binding, non-ASCII text that cannot be mapped | `unsupported` | `unknown` |
| `kicad-cli` nonzero exit or an empty produced native file | `failed` | `unknown` |
| Netlist prerequisite absent, later, failed or from another candidate | `not_run` | `unknown` |

Pre-existing `fab/` staging or a native step's `out/` directory is refused as
`malformed` before reuse; stale bytes cannot enter a new manifest.

A failure after staging started renames `fab/` to `fab.incomplete/` without a
manifest, so no partial package looks complete. The incomplete files stay as
evidence, for example the BOM with flagged rows.

Unsupported in v1: copper counts other than an even 2–32 with contiguous inner
layers; a missing mask, paste, silkscreen or `Edge.Cuts` layer; blind, buried or
micro vias; drill spans other than `F.Cu..B.Cu`; castellated or press-fit pads;
a missing board outline; assembly variants on the board or schematic; duplicate
or non-plain references; and populated footprints or symbols marked `board_only`,
`exclude_from_bom`, `exclude_from_pos_files` or `exclude_from_board`. A board
with no populated footprints is also refused, because the package would be
empty.

`pass` means that the exports exist, parsed, agree on designators and were
hashed and bound to the board. `details.designators` records the board, CPL
and BOM sets even on a mismatch. LCSC-to-footprint uniqueness, CPL
side/coordinate checks, export-log diagnostics and 3D-model checks belong to
`fab_consistency` (AGENTEE-73), which reads this staging and manifest and
never regenerates the exports.

## Fixture and tests

[fixtures/fab-export](../fixtures/fab-export/generate.py) is an owned synthetic
schematic with a 2-layer and a 4-layer board. It has an SMD part on the bottom,
a through-hole header with an unplated peg, a DNP capacitor, an LCSC group of
three, and `µ`/`Ω` values. The LCSC-style numbers and manufacturer strings are
placeholders. Nothing is copied from FOME-MC, a donor or a supplier library.

[tests/test_fab_export.py](../tests/test_fab_export.py) covers the following:

- the 2-layer and 4-layer packages, sealed;
- byte-identical repeat runs whose raw native Gerber timestamps differ;
- mismatched BOM/board references refused before completion;
- schematic/board DNP disagreement in both directions: the package follows the
  board, completes, and records `details.dnp_disagreements`;
- the DNP authority and exclusion rules as unit cases;
- real netlist → geometry → fab export → consistency checks with sealed evidence;
- DNP exclusion, a missing part binding, and empty, removed or truncated native
  outputs;
- a real `kicad-cli` nonzero exit;
- a blind via and a non-DNP exclusion;
- prerequisite and parameter boundaries, and manifest tampering.

The native tests need Bubblewrap and `kicad-cli` in the sandbox. They skip when
these are absent, and fail under `AGENT_EE_REQUIRE_TOOLCHAIN=1`.

The separate [fab round-trip check](fab-roundtrip.md) consumes this manifest
and exact zip bytes alongside sealed native geometry to check layer, drill,
flash-count and outline conservation. Export success alone does not establish
those comparisons.

# JLCPCB fab package export

`fab_export` (AGENTEE-23, stage 7 of the [generator roadmap](generator-roadmap.md))
writes the files JLCPCB receives from the captured candidate. The pinned
`kicad-cli` runs inside the executor sandbox with no network. The check uses the
captured `.kicad_pcb`, plus the native XML netlist and part-binding fields that
an earlier `netlist` check retained. It does not run DFM, panelisation, stock or
price queries, and it does not place an order.
[fab_export.py](../src/agent_ee/fab_export.py) implements it.

## Parameters and coverage

Parameters are `{"netlist_check": "<id>"}` plus an optional
`"assembly_exclusion": "JLCPCB_Assembly=not_assembled"` (see
[assembly exclusions](#assembly-exclusions)) and an optional
`"bom_variant": "<name>"` (see [BOM variant](#bom-variant)).
`"copper_features": ["JP1"]` declares copper-only footprint designators
(see [copper features](#copper-features)). Optional
`probe_manifest` names captured immutable probe declarations and their source
closure; admission requires a matching earlier passing `parts_subset` check. The named check must be an
earlier `netlist` check in the same scope. Its result must be `execution=ok` for
the same candidate, and its retained `design.xml` must match its
`connectivity.json`. Otherwise the check is `not_run`. v1 refuses any other key,
for example a layer or output override, any other exclusion rule text and an
empty or padded variant name, as `unsupported`. In scope/3 the check
declares the predicate `fab-export:jlcpcb-package/1`, so it must be mapped to an
acceptance requirement. The board stays mutable candidate input. A captured
`.kicad_pro` next to the board becomes an immutable dependency.

Board-only mechanical footprints carrying all three native attributes
`board_only`, `exclude_from_bom` and `exclude_from_pos_files` remain in
`board_non_dnp` and the native geometry inventory. They need no schematic part,
BOM binding or placement row. Their sorted references appear in the 
`designators.mechanical` manifest set (always emitted by `agent-ee.fab-manifest/2`, `[]` when the board has none), and their holes
remain in the native drill output. Mechanical items must have no netlist path,
no matching native netlist component and no assigned pad net. Netted or
unqualified pad-net declarations are refused even when the exclusions are
present. `fab_consistency` independently re-derives admission and verifies the
mechanical set; a `/2` manifest without it is refused, while legacy `/1` manifests
without the field are accepted with an advisory and retain admission replay. Original 3.2 mm hole fixtures exercise this path without copying stock
library files. Partial exclusion combinations on non-DNP
footprints still fail closed; this exception does not admit arbitrary excluded
schematic parts. Qualified probe features retain their separate source checks.

Netlisted UART test pads may carry footprint BOM/position exclusions when both
native symbol and footprint carry `JLCPCB_Assembly=not_assembled` and the export
check declares the matching `assembly_exclusion` rule. They need no supplier
binding, are omitted from BOM/CPL and appear in `designators.excluded`. Missing
rules or disagreeing markers remain refused. This does not admit native
schematic exclusion flags; the symbol remains netlisted and in the schematic
BOM. The original single-pad `TestPoint_Pad_D1.5mm` fixture verifies native
connectivity and a passing consistency gate.

## Copper features

A solder jumper has copper to fabricate, but no component to buy or place.
Declare it explicitly in the captured export check:

```json
{"netlist_check": "netlist", "copper_features": ["JP1"]}
```

The list must contain unique plain designators and must be nonempty when
present. No feature is inferred from its name or exclusions. Each declaration
must identify exactly one footprint and a native on-board schematic component.
The footprint must carry both `exclude_from_bom` and
`exclude_from_pos_files`, contain at least one surface copper pad, and have
no model. Copper declarations use a field allow-list on footprint properties,
captured schematic properties (including hidden fields), variant fields and
retained native fields: `Reference`, `Value`, `Footprint`, `Datasheet`,
`Description` and the exact KiCad internal names `ki_keywords`,
`ki_description`, `ki_fp_filters` and `ki_locked`. Other `ki_*` names receive
no exemption. Datasheet and Description may
contain any text, including stock solder-jumper descriptions. Every other field
must be empty, whitespace-only or `~` (surrounding whitespace is ignored).
Nonempty manufacturer, distributor, supplier, assembly-policy and arbitrary
metadata fields refuse the declaration; field-name token matching does not
qualify copper. Every native field occurrence is retained for this check, so a
later empty duplicate cannot hide an earlier nonempty field. The general
orderable-field detector and part-binding policy are unchanged.
Missing or non-string footprint property values are unqualified footprint
content. Unreadable property names or references produce a typed board-read
error. Export refusal summaries list each distinct problem once. Captured
schematic properties and variant field clauses are checked too, including fields
omitted from the default native netlist or hidden by `in_bom=no`.

Qualified pad shapes are rect, roundrect, circle, oval and trapezoid SMD pads
on copper, mask and paste layers. Custom SMD pads also qualify when their
line, arc, circle, rectangle, polygon or curve primitives inherit these layers
without overrides. Drilled pads and padstacks are refused.
Displayed properties remain restricted to silk/fab layers. Footprint graphics
also admit courtyard shapes on either F.CrtYd or B.CrtYd; coordinate-free or
malformed courtyard objects remain refused. Courtyards never enlarge copper
extent. Own-side F.Mask/B.Mask lines, rectangles, polygons and circles with an
axis-aligned radius qualify, filled or outlined, only when their entire envelope
(including half the stroke width) fits inside the footprint-local bounding box of
its own-side pad copper plus **0.05 mm** on each edge. This box includes gaps
between pads, allowing a solder bridge opening. It is not a contour-distance
or mask-manufacturability check. Board/footprint mask expansion settings do not
increase this admission margin.

This exact rational envelope includes nominal pad size and custom line, rectangle,
polygon and axis-radius circle primitives. Pad rotations relative to the footprint
must be multiples of 90 degrees for mask qualification. Custom pads need an
explicit rect anchor, or a circle anchor with equal size axes. Mask text, arcs, curves,
non-axis circle radii, trapezoid deltas and otherwise unproved geometry are
refused by this bounded route. These restrictions apply when proving a new mask
graphic; pad-only declarations retain their previous qualification. Opposite-side
or distant mask openings, copper/paste/adhesive graphics, zones, models and
embedded files remain refused.
The feature remains in the native board inventory and Gerbers, and appears in
neither BOM nor CPL. KiCad's native position export must also omit it.

Graphics and properties carrying `(layers ...)` are refused even alongside
an admitted `(layer ...)`, in either token order: KiCad can apply the extra
layer set as copper. This applies to silk, fab, courtyard and mask graphics.

`details.copper_features` lists the captured declaration. The manifest schema
and `designators.excluded` assembly-rule records are unchanged.

`fab_consistency` independently repeats qualification from captured board bytes
and the sealed native netlist, using the captured export check's declaration.
It refuses an invalid declared feature even if the producer reported success.
A copper designator inserted into either CSV is a `designator_parity` defect.
Undeclared exclusions retain their existing verdicts. This policy does not
supply a component binding or exempt other checks of the board.

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
| `bom` (only with `bom_variant`) | `sch export bom --fields Reference,Value,Footprint,LCSC,Manufacturer,Partnumber,PartNumber --labels <same> --ref-range-delimiter '' --exclude-dnp -o /work/out/bom.csv --variant NAME SCHEMATIC` |

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
| `bom.csv` | Header `Designator,Footprint,Quantity,Comment,Manufacturer,Partnumber,"LCSC Part #"` (verified against `fixtures/fab-export/jlc-bom-template.csv`) |
| `cpl.csv` | Header `Designator,Mid X,Mid Y,Layer,Rotation` (verified against `fixtures/fab-export/jlc-cpl-template.csv`) |
| `gerbers.zip` | Flat archive of `gerbers/` (sorted) then `drill/` (sorted). Each member has the date 1980-01-01, mode 0644, Unix create system and deflate level 9. |
| `pcb_summary.json` | Native `pcb export stats` JSON, with only `metadata.date` normalised |
| `manifest.json` | `agent-ee.fab-manifest/2`, written last |

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
  in either the schematic or the board is refused as `unsupported`, also when
  the component is excluded by the declared rule below.

The export never resolves a schematic/board DNP disagreement silently. Each
disagreeing designator is listed in `details.dnp_disagreements` as
`{"designator", "schematic_dnp", "board_dnp"}`, and a limitation names it.
`details.schematic_dnp` keeps the schematic DNP references. The package still
completes, because the board flags fully define it. The
[consistency gate](fab-consistency.md) reads the sealed netlist and geometry
itself and reports every such designator as a `designator_parity` defect. The
manifest's `designators.board_non_dnp` follows the board flags alone. The board is
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

#### Assembly exclusions

A populated part can stay on the board but out of JLC assembly, for example
the rung-1 through-hole header J1 that is hand-soldered. The rung-1 generator
marks such a symbol with the field `JLCPCB_Assembly=not_assembled`, and
`board_build` copies the field to the footprint as a property. `fab_export`
honours the marker only when the scope declares the same rule as a check
parameter, `"assembly_exclusion": "JLCPCB_Assembly=not_assembled"`. The
parameter is part of the captured scope, so it is hashed into the
candidate identity (`candidate_id`, `scope_sha256`). v1 knows only this rule.

A part is excluded only when both of these hold:

- its board footprint is not DNP (DNP stays the board's decision, and a DNP
  part is omitted as DNP, never listed as excluded);
- the native netlist field and the board footprint property are both
  `not_assembled`.

The excluded part needs no `LCSC`, `Manufacturer` or `Partnumber` binding. It
is left out of `bom.csv`. The native position file still lists it, because it
is populated. The native designators must equal `board_non_dnp` before the
excluded rows are dropped from `cpl.csv`. The BOM and CPL designators must then
equal the assembled set, `board_non_dnp` less the excluded designators. The
manifest lists each excluded designator with its rule, and a limitation names
it: the part is still on the board, in the Gerbers and drill files, and must be
fitted outside JLC assembly.

Nothing is excluded silently. These cases are refused as `unsupported` before
any native export:

- a populated part carries the field and the scope declares no matching rule;
- the netlist field and the board property differ, or only one of them is
  present;
- the field has a value other than `not_assembled`;
- a marked netlist component has no board footprint;
- a marked board footprint has no netlist component.

A declared rule that matches no part is recorded in the manifest
(`exclusion_rule`) with an empty `excluded` list, and the package is otherwise
unchanged.

#### BOM variant

A declared `"bom_variant": "NAME"` (the generator's `--bom-variant`,
AGENTEE-66) exports the BOM of one design variant. Before any kicad-cli command
the check runs `kicad_quirks.validate_bom_variants` on the captured project file
(the scope schematic with `.kicad_pro`) and every netlist schematic. An
unregistered name, an uncaptured or unreadable project file, or an unloadable
variant clause is `unsupported` ("refused before kicad-cli ran"), because
kicad-cli would silently export the default
([quirk guards](kicad-quirks.md#bom-variant-names)). With the parameter,
schematic variant clauses are allowed; without it they are refused.

The name reaches only the `bom` step. `bom.csv` is built from that ungrouped
native export with the same grouping, ASCII and binding rules as the default
BOM, so variant field overrides (for example a part number) are honoured. Rule
exclusions are still decided on the default netlist and board. Positions stay
the default variant, so `cpl.csv` is the default CPL. A variant whose assembled
designators differ from the default assembled set is refused as `unsupported`
by name: `BOM variant 'NAME' changes the populated set (adds [...], removes
[...])`, with `details.bom_variant.populated_set_change`. A schematic/board DNP
disagreement also reads as a populated-set change, because the native BOM
follows the schematic flags. The manifest gains `bom_variant` (name, project
path and sha256, registered names, native BOM sha256 and the designators whose
binding the variant changed). The raw native BOM stays step evidence. A run
without the parameter writes no `bom_variant` key.

### CPL

The native position file is rewritten: `Ref`→`Designator`, `PosX`→`Mid X`,
`PosY`→`Mid Y`, `Side` `top|bottom`→`Layer` `Top|Bottom`, and `Rot`→`Rotation`.
`Val` and `Package` are dropped. Coordinates are the native millimetre values in
absolute board X and negated board Y, on both sides, with no auxiliary/drill-origin
override. The anchor is the native footprint position. The consistency gate
compares these coordinates with the exact native geometry probe within 0.01 mm.
Rows are in natural designator order. The check applies **no rotation, offset or pin-1 policy**:
`Rotation` is the native KiCad angle. JLC rotation correction is the separate
[`cpl_rotation`](cpl-rotation.md) check (AGENTEE-72), which reads this staging.
The native position-file designators must equal the board's non-DNP
footprints, and the BOM and CPL designators must equal the board's assembled
footprints (non-DNP less rule-excluded parts), before a manifest is written.
The DNP authority and exclusion rules make them agree when the netlist and board name the same parts. A remaining
mismatch, such as a renamed footprint or a part missing from one side, makes
the output malformed and retains the files only in `fab.incomplete/`.

### Manifest

```json
{"schema": "agent-ee.fab-manifest/2", "candidate_id": "...",
 "board_path": "design/fab.kicad_pcb", "board_sha256": "<captured digest>",
 "tool": {"path": "/usr/bin/kicad-cli", "version": "10.0.7", "sha256": "..."},
 "files": {"bom.csv": "<sha256>", "gerbers/fab-F_Cu.gtl": "<sha256>", "...": "..."},
 "headers": {"bom": {"header": "Designator,Footprint,Quantity,Comment,Manufacturer,Partnumber,\"LCSC Part #\"",
                     "columns": ["Designator", "Footprint", "Quantity", "Comment", "Manufacturer", "Partnumber", "LCSC Part #"],
                     "sha256": "<header sha256>", "template_sha256": "<template sha256>"},
             "cpl": {"header": "Designator,Mid X,Mid Y,Layer,Rotation",
                     "columns": ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"],
                     "sha256": "<header sha256>", "template_sha256": "<template sha256>"}},
 "copper_layers": 2, "enabled_fab_layers": ["F.Cu", "B.Cu", "F.Mask", "..."],
 "drill": {"pth": "fab-PTH.drl", "npth": "fab-NPTH.drl"},
 "designators": {"board_non_dnp": ["C1", "C2", "C10", "J1", "..."],
                 "excluded": [{"designator": "J1", "rule": "JLCPCB_Assembly=not_assembled"}],
                 "exclusion_rule": "JLCPCB_Assembly=not_assembled",
                 "mechanical": []},
 "generated_utc": "2026-10-17T19:04:46Z"}
```

`files` holds the sha256 of every regular file in `fab/` except
`manifest.json`, keyed by staging-relative path. `headers` records the verified
BOM and CPL header strings, column lists, header-line SHA-256 digests and
captured template SHA-256 digests (`fixtures/fab-export/jlc-bom-template.csv`,
`fixtures/fab-export/jlc-cpl-template.csv`, and
`fixtures/fab-export/jlc-templates.json`, AGENTEE-196). `board_sha256` is the
candidate-manifest digest of the captured board. The check also confirms that
the captured bytes still have that digest. `drill` gives file names inside
`drill/`. `designators.board_non_dnp` comes from the board's footprint inventory,
not from the position file. `designators.excluded` lists the rule-excluded
designators, a subset of `board_non_dnp`, and `designators.exclusion_rule` is
the declared rule or `null`. Both keys are always present. `tool.path` and `tool.sha256` identify the
`kicad-cli` on the sandbox `PATH`, the same file `doctor` and `runtime_files`
fingerprint. The check reads the manifest back and verifies it against the
staging before it reports `pass`. `verify_manifest` is the same comparison for
consumers.

## Outcomes

| Condition | Execution | Outcome |
| --- | --- | --- |
| Every step succeeded, every output validated, no flagged part, manifest verified | `ok` | `pass` |
| Missing or unparseable native output; BOM or position designators that disagree with the board; unnormalisable timestamps; board bytes that differ from the captured digest | `malformed` | `unknown` |
| Unsupported board feature, missing part binding, non-ASCII text that cannot be mapped, an assembly marker without a matching declared rule or with sources that disagree | `unsupported` | `unknown` |
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
a missing board outline; assembly variants on the board, or on the schematic
without a declared `bom_variant`; duplicate
or non-plain references; and populated footprints or symbols marked `board_only`,
`exclude_from_bom`, `exclude_from_pos_files` or `exclude_from_board` (the
declared `JLCPCB_Assembly` rule, qualified board-only mechanical footprints,
qualified probes and explicitly declared copper features are the supported exceptions). A board
with no populated footprints is also refused, because the package would be
empty.

`pass` means that the exports exist, parsed, agree on designators and were
hashed and bound to the board. `details.designators` records the board
non-DNP, excluded, assembled, native position-file, CPL and BOM sets even on a
mismatch. `details.assembly_exclusion` records the rule, the scope check that
declared it, the captured `scope_sha256` and the excluded designators. LCSC-to-footprint uniqueness, CPL
side/coordinate checks, export-log diagnostics and 3D-model checks belong to
`fab_consistency` (AGENTEE-73), which reads this staging and manifest and
never regenerates the exports.

## Fixture and tests

[fixtures/fab-export](../fixtures/fab-export/generate.py) is an owned synthetic
schematic with a 2-layer and a 4-layer board. It has an SMD part on the bottom,
a through-hole header with an unplated peg, a DNP capacitor, an LCSC group of
three, and `µ`/`Ω` values. The LCSC-style numbers and manufacturer strings are
placeholders. Nothing is copied from FOME-MC, a donor or a supplier library.

Both owned generators (this one and
[fixtures/cpl-rotation](../fixtures/cpl-rotation/generate.py)) are
deterministic. Schematic and supplier UUIDs are UUIDv5 values from a fixed
namespace. Before each board is built, the generator builds a warm-up board
and then seeds pcbnew's KIID generator with a fixed per-board value. Without
the warm-up, a board's KIIDs depended on whether another board had been built
earlier in the process. Boards are pinned to pcbnew 10.0.7: both generators
refuse any other or unreadable `pcbnew.Version()` before writing anything
(the cpl `--supplier-only` mode needs no pcbnew and does not check).
[tests/test_fixture_generators.py](../tests/test_fixture_generators.py)
(AGENTEE-367) fails unless the sandbox's pcbnew reports 10.0.7, and requires:

- two full runs of each generator in the native sandbox to be byte-identical;
- every output (schematics, supplier records and the four boards) to equal the
  committed file byte for byte;
- each board, generated alone in a fresh process and in one shared process in
  reordered and repeated sequence across both generators, to equal its
  committed file;
- both generators, with `pcbnew.Version()` reporting another version, an empty
  value, `None` or an error, to exit non-zero without writing output, from
  the command line and from `make_board`.

The four boards (`fab`, `fab4`, `pod4` and `cpl.kicad_pcb`) were re-pinned to
the seeded output under the coordinator ruling on AGENTEE-367, and again after
the warm-up was added, composed with AGENTEE-351g's Reference positions.
Before seeding they carried random KIIDs, and KiCad orders board items by KIID.
Content was unchanged by either re-pin: with KIIDs removed and top-level items
unordered, the old and new boards compare equal. No test, receipt or doc pinned their digests or KIIDs.
To change a board, edit its generator, rerun it with the pinned KiCad and
commit the output.

[tests/test_fab_export.py](../tests/test_fab_export.py) covers the following:

- the 2-layer and 4-layer packages, sealed;
- `test_board_only_holes`: four original 3.2 mm holes retained in NPTH drills,
  absent from BOM/CPL, listed in the mechanical set, with consistency passing;
- `test_board_only_with_net_refused` and netlist-path refusal;
- `test_testpad_not_assembled`: an original 1.5 mm netlisted pad excluded and
  listed under the declared rule, consistency passing, no-rule refusal;
- byte-identical repeat runs whose raw native Gerber timestamps differ;
- mismatched BOM/board references refused before completion;
- schematic/board DNP disagreement in both directions: the package follows the
  board, completes, and records `details.dnp_disagreements`;
- the DNP authority and exclusion rules as unit cases;
- an owned solder-jumper fixture passing export and consistency with native
  netlist/geometry evidence, missing declarations and partial flags refused,
  and model/MPN refusals replayed independently by consistency;
- the declared `JLCPCB_Assembly=not_assembled` rule: an unbound, marked J1 is
  left out of the BOM and CPL, listed in the manifest, and the package passes
  `fab_consistency` with sealed evidence; DNP C3 stays a DNP omission;
- a marked part with no declared rule, or marked in only the schematic or only
  the board, refused before export; a declared rule that matches nothing;
- real netlist → geometry → fab export → consistency checks with sealed evidence;
- DNP exclusion, a missing part binding, and empty, removed or truncated native
  outputs;
- a real `kicad-cli` nonzero exit;
- a blind via and a non-DNP exclusion;
- a declared `bom_variant`: the name reaches only the native BOM export, a field
  override reaches `bom.csv`, the CPL equals the unvaried export, the manifest
  records the variant; a variant that drops a part is refused by name; an
  unregistered name or an uncaptured project file is refused before kicad-cli;
- prerequisite and parameter boundaries, and manifest tampering.

The native tests need Bubblewrap and `kicad-cli` in the sandbox. They skip when
these are absent, and fail under `AGENT_EE_REQUIRE_TOOLCHAIN=1`.

The separate [fab round-trip check](fab-roundtrip.md) consumes this manifest
and exact zip bytes alongside sealed native geometry to check layer, drill,
flash-count and outline conservation. Export success alone does not establish
those comparisons.

## Declared fab steps

`tests.test_fab_stage` runs the native declared-step checks with a private copy
of the tool source, excluding `__pycache__`. Shared source mounts let concurrent
host imports change the tree during execution and correctly refuse the receipt
as `mount_changed` / `source_changed` (AGENTEE-299). The fixture isolates those
writes; production identity checks still hash and compare every mounted file.

When a campaign includes `fab` in `stages.order` and declares a fab step,
`fab_export`, `cpl_rotation` and `fab_package` consume that step's sealed
`out/fab` output. The shared [fab-stage producer](../src/agent_ee/fab_stage.py)
retains `out/fab-manifest.json`, the corrected package, and the native check
run under `out/checks`. All must be declared receipt artifacts. More than one
fab producer is refused because the checks cannot select an authoritative package.

The coordinator supplies the declaration and latest anchored receipt to each
review. Each fab check seals `work/ID/fab-step.json` and records the same
context in `details.fab_step`. The receipt must validate, match the frozen step,
be adoptable, and have an after-tree identical to the captured candidate.
Missing or changed artifacts, malformed package or native manifests, a stale
candidate or an invalid receipt leave coverage unknown. Campaign verification reconstructs this context from its
frozen configuration and preceding receipt indexes.

Before the step runs, fab checks report `not_run`/`unknown` with
`details.reason: fab_step_pending`. If all other checks and the independent
review are complete and satisfied, the coordinator schedules the fab turn.
The review remains incomplete. A crash, malformed export or other unknown
check cannot enter this scheduling path. The turn consumes an iteration;
without remaining budget the campaign stays incomplete.

Afterwards, fresh sandbox exports still run. Every normalized Gerber, drill,
ZIP, BOM, board summary and native CPL must be byte-identical to the step's
sealed exports. The corrected CPL must also match the fresh correction; the
policy, per-part results and preview status must agree. The checks grade copies
of the matching step output. Native manifests and correction reports refer to the nested run's candidate
identity. Manifest timestamps and report references to that run differ; these
context fields are bound by the receipt. The remaining manifest and correction
values must match the fresh run, including the native CPL digest. Payload files receive no additional normalization for comparison.
A difference is a refusal with unknown coverage, never a passing substitute.

Scopes without a declared fab step, or with `fab` outside the stage order,
keep the existing self-export path. The declared-stage done binding and every
rotation-status done term remain in force. `PREVIEW_REQUIRED` is preserved.
`DeclaredFabStepTests` and `DeclaredFabCampaignTests` exercise native output,
refusals and the D/F1/G1 fab-turn reachability shapes.

Historical campaigns with a counted fab producer but no sealed step witness do
not inherit this acceptance evidence. Start a fresh campaign to obtain it.
Historical scopes without a counted fab producer keep their earlier behavior.

## Required copper probes

With `probe_manifest`, [bare copper probe features](probe-features.md) qualify against an immutable captured parts/1 declaration and the preceding native netlist. Saved board geometry, native flags and required presence are checked before BOM/CPL exclusion. `board_non_dnp` still includes probes; the native position inventory excludes these exact feature references. Copper and mask Gerbers retain their pads, and the fab manifest adds qualified `probe_features` records. Undeclared markers and ordinary non-DNP exclusions remain refused.

Probe admission also requires an earlier, passing `parts_subset` check using the same manifest and native netlist. Capabilities enforce that ordering; runtime consumers verify its sealed report and exact source hashes.

## Copper membership agreement

When a scope also declares parts membership (`parts_subset`) against the same
`netlist_check`, the parts manifest's copper references must equal this check's
explicit `copper_features` set. This applies to `fab_package` through the shared
export path. Copper manifests must be captured and immutable; their earlier
membership checks must pass with complete coverage and evidence bound to this
candidate, the exact manifest and the native XML. A missing or extra declaration,
a forward prerequisite, or failed or stale membership evidence refuses export.
Standalone fab scopes continue to qualify their explicit declarations directly.

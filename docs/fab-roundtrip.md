# Fab-package round-trip conservation

`fab_roundtrip` (AGENTEE-74) re-parses a captured candidate's native fabrication
outputs and compares their counts and outline bounds with its sealed pcbnew
geometry. It runs offline in the coordinator, launches no tool and never
regenerates an export. It complements [fab_consistency](fab-consistency.md),
which checks assembly inventory, models and native export diagnostics.

Parameters are exactly `{"export_check": "fab"}`. The named check must be an
earlier successful `fab_export` for the same candidate, with its earlier
netlist prerequisite. At least one earlier successful `geometry` check must
supply sealed `geometry.json` bound to the captured board digest. All earlier
geometry checks must agree on fabrication facts. Scope/3 declares the acceptance
predicate `fab-package:roundtrip/1` and records those ordered prerequisites.
No new scope or manifest schema is introduced.

For example, after the existing `netlist`, `geometry` and `fab` export checks,
append this check and map it to a fabrication acceptance requirement:

```json
{"id": "roundtrip", "kind": "fab_roundtrip", "params": {"export_check": "fab"}}
```

## Comparisons

The check reuses the export manifest reader, pinned-tool verification, captured
board S-expression reader, file inventory and exact zip comparison. It verifies
that all declared files are regular, nonempty and hash-bound, and that every
intact package's zip contains the exact parsed Gerber and drill bytes.

- There must be one plot for every enabled fabrication layer: all copper layers,
  `F.Mask`, `B.Mask`, `Edge.Cuts`, and the export's optional paste/silkscreen
  layers. A four-layer board requires `In1.Cu` and `In2.Cu`. X2 `FileFunction`
  identifies each layer; filename extensions alone cannot establish identity.
- Drill histograms compare integer-nanometre diameters within 1000 nm and exact hit counts,
  separately for PTH and NPTH. PTH includes through vias. Tool numbering and
  hit order may differ. KiCad prints tool diameters to three decimals; sorted matching permits that
  rounding and multiple native diameters sharing one output tool. Positions
  are parsed but are outside the comparison. Slots and non-through vias are
  unsupported.
- `Edge.Cuts` drawn centreline bounds must agree with the proven straight board
  outline within **1000 nm (1 µm), inclusive**, on each of the four bounds.
  Gerber coordinates use absolute board X and negative board Y. Aperture width
  is excluded from both bounds. A missing, open or unsupported native outline
  leaves coverage unknown.
- Each copper layer reports aperture definitions/count, total flashes and
  separate pad/via flash counts. `SMDPad`/`ComponentPad` and `ViaPad` aperture
  functions identify those categories. Counts must equal pcbnew's native
  `FlashLayer` inventories. A shared aperture may flash many pads; aperture
  count is reported rather than incorrectly equated with pad count.

The geometry probe adds `copper_layers` in stack order, a proven `outline`
using the existing placement contour helpers, per-pad `drill_nm` and
`flash_layers`, and per-via `drill_nm` and `flash_layers`. These are additive
facts; existing millimetre values and geometry criteria retain their behavior.
A legacy report without the exact new facts must be rerun.

## Parser boundary and results

The fresh stdlib parsers accept KiCad's absolute 4.6 mm Gerber subset, linear
moves/draws, flashes, positive polarity and circular/rectangular/oval apertures.
They accept the explicitly listed KiCad X2 file/aperture/object attributes.
Excellon support is metric decimal, absolute round drilling with declared tools
and separated plating. Empty NPTH drill output with a complete native header
and trailer is valid when the board has no NPTH holes.

| Condition | Execution / outcome |
| --- | --- |
| Layer, file, drill count/diameter, pad/via count or outline bound differs | `ok` / `defect`, naming the layer or file |
| Inch units, unknown Gerber command, missing trailer/format, invalid number or undefined aperture/tool | `malformed` / `unknown` with source line |
| Known unsupported construct (arc, region, macro, repeat, transform, clear polarity, drill slot or incremental mode) | `unsupported` / `unknown` with source line |
| X2 attribute outside the small allow-list | `unsupported` / `unknown` with source line |
| Missing prerequisite, unsupported board features, legacy geometry or unclassified copper flash | `unsupported` / `unknown` |
| Stale manifest, changed hashes, invalid zip or contradictory native evidence | `malformed` / `unknown` |

Any parser error ends the check without a pass or a defect witness, even if an
earlier comparison found a difference. Parser errors retain file, 1-based line
and message in `details.parser_error` and `work/CHECK/fab-roundtrip.json`.
Successful comparisons and established defects retain layer/aperture records,
drill histograms and outline bounds there. Defects qualify through the existing
sealed requirement/check witness path; this check changes no adjudication rules.

## Qualification and limits

[Tests](../tests/test_fab_roundtrip.py) run actual sandboxed KiCad exports and
native geometry on the original owned two- and four-layer
[fab fixtures](../fixtures/fab-export/generate.py). Fault tests alter retained
output before sealing and rebuild its hashes/zip to isolate the conservation
comparison. They cover dropped mask, inner layer and drill files, dropped hits,
wrong diameter/plating, dropped pad flashes, the exact 1 µm outline boundary,
inch units and unsupported syntax. A roundrect-pad mutation of the owned board verifies that real KiCad
multi-line aperture macros are unsupported at their opening line.
Pure parser controls cover line numbers,
modal state, trailers, undefined tools and non-finite aperture input.
`AGENT_EE_REQUIRE_TOOLCHAIN=1` makes missing KiCad/pcbnew/bubblewrap a failure.

This is not a general Gerber renderer or a proof of fabrication readiness.
Copper artwork, aperture-to-pad shape equality, routing geometry, hole position,
DFM, silk/mask artwork and drill-map artwork are not compared. Unsupported
constructs remain unknown; the parser does not approximate them. No vendor
corpus or third-party design is included.

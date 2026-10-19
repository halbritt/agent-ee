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
  separate pad/via flash counts. The `ViaPad` aperture function identifies vias;
  `ComponentPad`, `SMDPad`, `BGAPad`, `ConnectorPad`, `HeatsinkPad`,
  `CastellatedPad`, `TestPad`, `FiducialPad` and `WasherPad` identify pads.
  Counts must equal pcbnew's native `FlashLayer` inventories. A shared aperture
  may flash many pads; aperture count is reported rather than incorrectly
  equated with pad count.
- **Pad extents.** Every flash becomes circles and polygons in board
  coordinates. Aperture macros are evaluated with exact parameter arithmetic
  (below). When the pad and via counts agree, the bounding boxes of the pad
  flashes on a layer are paired with pcbnew's per-layer pad bounding boxes
  (`PAD::GetBoundingBox(layer)`, Y negated). Exact pairs are taken first and
  the rest pair within **1000 nm**. Any unpaired pad or flash is a
  `pad_extent` defect that names up to five pads.
- **Tracks, arcs and zones.** `Conductor` linear draws must equal the native
  track segments on the layer, and `Conductor` G75 arc draws must equal the
  native `PCB_ARC` tracks; a mismatch is a `track_count` defect. Each closed
  contour of a `Conductor` G36/G37 region is one region. Their number must equal
  the outlines of the saved zone fills on the layer (board and footprint zones,
  `GetFilledPolysList(layer).OutlineCount()`), else it is a `region_count`
  defect. KiCad 10 plots each fractured fill outline as one region.
  `NonConductor`, `EtchedComponent` and unattributed draws and regions
  (footprint copper graphics, text) are counted as `other_draws`/`other_regions`
  and are not conserved.
  A netted board or footprint shape on a copper layer plots as `Conductor`
  artwork. Such a shape cannot be attributed to a track or zone, so that
  layer's track/region comparison becomes a coverage gap and the check is
  unknown. A pad plotted as a region rather than a flash is also a gap.
  KiCad plots a degenerate arc track as a segment; such a board would show a
  `track_count` difference.

The geometry probe adds `copper_layers` in stack order, a proven `outline`
using the existing placement contour helpers, per-pad `drill_nm`,
`flash_layers` and `flash_bbox_nm` (exact native bounding box per flashed
copper layer), per-via `drill_nm` and `flash_layers`, `zone_fill_outlines` and
`copper_conductor_shapes` (counts per copper layer). These are additive
facts; existing millimetre values and geometry criteria retain their behavior.
A legacy report without the exact new facts is `unsupported` and must be rerun.
A zero-length track is unsupported.

## Parser boundary and results

The owned stdlib parsers accept the absolute 4.6 mm Gerber subset that KiCad
10.0.7 emits:

- linear moves and draws, flashes and positive polarity;
- circular, rectangular and oval apertures, including the zero-diameter
  circle that KiCad selects before regions (flashing it is malformed);
- `G02`/`G03` circular draws in `G75` multi-quadrant mode, with a start equal to
  the end meaning a full circle. Start and end radii must agree within 1 µm.
  Arc extremes count in the plot bounds;
- `G36`/`G37` regions with linear or circular contour segments. Each contour
  must close. A region takes the `AperFunction` attribute that is current when
  it opens (`%TD.AperFunction*%` clears it);
- `%AM` aperture macros with the primitives KiCad emits: comment 0, circle 1,
  outline 4, polygon 5, vector line 20 and center line 21. This covers
  `RoundRect`, `RotRect`, `HorizOval`, `Outline4P` and `FreePolyN`. Macro
  parameters from `%ADD` and expressions with `+ - x /`, parentheses and
  unary signs are evaluated exactly with rational arithmetic; only rotations
  use floating point. Rotations turn about the macro origin. An outline must
  close, an undefined `$n` or a division by zero is malformed, and a macro whose
  primitives have no image is malformed;
- the explicitly listed KiCad X2 file, aperture and object attributes.

Anything outside that list stays `unsupported` at its source line. This
includes the thermal (7), moiré (6) and deprecated (2, 22) primitives, macro
variable definitions, clear exposure, single-quadrant `G74`, arcs without
`G75`, standard polygon `P` apertures, aperture holes, step-and-repeat,
transforms and clear polarity. No external Gerber library is used.
Excellon support is metric decimal, absolute round drilling with declared tools
and separated plating. Empty NPTH drill output with a complete native header
and trailer is valid when the board has no NPTH holes.

| Condition | Execution / outcome |
| --- | --- |
| Layer, file, drill count/diameter, pad/via count or outline bound differs | `ok` / `defect`, naming the layer or file |
| Inch units, unknown Gerber command, missing trailer/format, invalid number or undefined aperture/tool | `malformed` / `unknown` with source line |
| Pad extent, track/arc draw or zone region count differs | `ok` / `defect` (`pad_extent`, `track_count`, `region_count`) naming the layer |
| Unclosed region contour, inconsistent arc radii, flash or aperture selection inside a region, invalid macro arithmetic or empty macro image | `malformed` / `unknown` with source line |
| Known unsupported construct (macro primitive outside 0/1/4/5/20/21, macro variable definition, clear exposure, `G74` or arc without `G75`, repeat, transform, clear polarity, drill slot or incremental mode) | `unsupported` / `unknown` with source line |
| X2 attribute outside the small allow-list | `unsupported` / `unknown` with source line |
| Missing prerequisite, unsupported board features, legacy geometry, unclassified copper flash, pad region or netted copper shape | `unsupported` / `unknown` |
| Stale manifest, changed hashes, invalid zip or contradictory native evidence | `malformed` / `unknown` |

Any parser error ends the check without a pass or a defect witness, even if an
earlier comparison found a difference. Parser errors retain file, 1-based line
and message in `details.parser_error` and `work/CHECK/fab-roundtrip.json`.
Successful comparisons and established defects retain layer/aperture records,
drill histograms and outline bounds there. Each copper record lists observed and
expected `pad_flashes`, `via_flashes`, `track_segments`, `track_arcs` and
`conductor_regions`, along with `macro_pad_flashes`, `other_draws`, `other_regions`
and `pad_extent_mismatches`. Each aperture lists its shape, its macro name and
parameters, and its `bbox_nm`. Defects qualify through the existing
sealed requirement/check witness path; this check changes no adjudication rules.

## Qualification and limits

[Tests](../tests/test_fab_roundtrip.py) run actual sandboxed KiCad exports and
native geometry on the original owned two- and four-layer
[fab fixtures](../fixtures/fab-export/generate.py). Fault tests alter retained
output before sealing and rebuild its hashes/zip to isolate the conservation
comparison. They cover dropped mask, inner layer and drill files, dropped hits,
wrong diameter/plating, dropped pad flashes, the exact 1 µm outline boundary,
inch units and unsupported syntax.

The original 4-layer pod probe `pod4.kicad_pcb` (AGENTEE-195) has the same
parts and nets. Its pads are RoundRect (R1, R2 on the bottom side), RotRect
(C1), custom FreePoly (C2), chamfered and rotated FreePoly (C10), trapezoid
Outline4P (C3) and a rotated HorizOval (J1 pin 2). It also has an arc track on
each outer layer and saved zone fills on In1, In2 and B.Cu. The sandboxed
export of this board passes with these conserved counts (pad/via flashes,
segments, arcs, regions):

| Layer | Pads | Vias | Segments | Arcs | Regions |
| --- | --- | --- | --- | --- | --- |
| F.Cu | 13 (11 macro) | 1 | 3 | 1 | 0 |
| In1.Cu | 3 | 1 | 0 | 0 | 1 |
| In2.Cu | 3 | 1 | 0 | 0 | 2 |
| B.Cu | 5 | 1 | 1 | 1 | 1 |

Every flashed pad extent equals pcbnew's at 0 nm. Each of these faults is a
named defect that witnesses: a widened RoundRect corner (R1) or a moved
FreePoly vertex (C2) is a `pad_extent` defect, a dropped arc draw is a
`track_count` defect and a dropped In2 region is a `region_count` defect. An
inconsistent arc radius or an unclosed region is malformed. A missing `G75` or
a thermal primitive in place of a RoundRect circle is unsupported at its line;
none of these faults can witness.

The fome-mc probe reads `fome-mc.kicad_pcb` at commit `df304f3`, by blob id,
from the local fome-mc object store (`AGENT_EE_FOME_MC_CHECKOUT`, default
`~/git/fome-mc`). It exports the eleven fab layers and runs the geometry probe in
the sandbox, then re-parses every plot. There are no unknowns. The comparison
finds no defects and no gaps, including 1318 macro pad flashes, 1799/1674 F.Cu
pad/via flashes, 4276 F.Cu segments and 375 copper regions. The board is never
committed. Without that object store the test skips, or fails under
`AGENT_EE_REQUIRE_TOOLCHAIN=1` as the named
[`fome-mc-store` required input](container.md#required-external-inputs).

Pure parser controls cover line numbers, modal state, trailers, undefined tools,
non-finite aperture input, exact macro arithmetic and extents, arc direction
and bounds, region closure and each unsupported macro feature.
`AGENT_EE_REQUIRE_TOOLCHAIN=1` makes missing KiCad/pcbnew/bubblewrap a failure.

This is not a general Gerber renderer or a proof of fabrication readiness.
Pad extents are compared as bounding boxes, not as shapes. Copper artwork
rendering, track and zone geometry, via extents, hole position, DFM,
silk/mask/paste artwork and drill-map artwork are not compared. A board outline
with arcs remains unknown, because the probe proves only straight outlines. Unsupported
constructs remain unknown; the parser does not approximate them. No vendor
corpus or third-party design is included.

# AGENTEE-524: D1 polarity evidence and the smallest source-bound remedy

Agent-566 (Fable 5.1), 2026-10-25, bounded evidence note for chief agent-564.
No production code, profile, limit, footprint or part changed. No supplier
preview or upload. Every file below is a retained original, read in place.

## Sources (exact paths, hashes)

| Source | Path | sha256 |
| --- | --- | --- |
| Sealed native XML netlist (v7 package candidate, kicad-cli `kicadxml`) | `build/acquisition-release/release-build-v7/stages/07-package-63a92a61a95149678ee3178e64133b22/review/work/review-acq-elec-6-remaining-topology/stage-subject/native-netlist.xml` | `f89b2d0f…8acda` |
| Captured board | `…/07-package-…/candidate/project/acquisition.kicad_pcb` | `d0a898a2…66e7` |
| Sealed geometry probe | `…/07-package-…/review/work/geometry/geometry.json` | (retained; D1 pads read from it) |
| Supplier CAD, LCSC C83152 (EasyEDA `result` object, byte-exact) | `~/.cache/agent-audits/agent-ee-566-20261025/cpl-inputs-rev2/supplier/C83152.raweasy.json` (+ `.provenance.json`) | `9939a5a6…f52c` |
| Manufacturer datasheet, Diodes Inc BAT46W (already captured; bound by the netlist `PARAM_Datasheet` field) | `…/07-package-…/candidate/datasheets-power-refined/ff2e780dd8716f9ddf9cdb60a5b9b804b65917a304adaaf7e4e3c8dfa85bd8de.pdf` | `3bc31f4a…be56b` (5 pages, title "BAT46W") |

## Observed facts (no inference)

**Board side, sealed netlist.** `comp ref="D1"`: value `BAT46W`, footprint
`Diode_SMD:D_SOD-123`, libsource `Device:D_Schottky`, fields `LCSC C83152`,
`MPN BAT46W-7-F`, `Manufacturer Diodes Incorporated`, `PARAM_Datasheet
{sha256 3bc31f4a…, url https://www.diodes.com/datasheet/download/BAT46W.pdf}`.
`libpart Device:D_Schottky` pins: `1` name `K`, `2` name `A` (both passive).
Nets: `/VIN_PROTECTED` node D1 pin `1`, `pinfunction="K_1"`; `/VIN` node D1
pin `2`, `pinfunction="A_2"`.

**Board side, captured board and probe.** Footprint D1 `(at 105.2 131.45 180)`,
pad `1` at (−1.65, 0) and pad `2` at (+1.65, 0) in the footprint frame; probe
pad `1` net `/VIN_PROTECTED` at board (106.85, 131.45), pad `2` net `/VIN` at
(103.55, 131.45). The pad nets equal the netlist nets of pins 1 and 2, so the
pad↔pin map is consistent. The board carries **no `pinfunction` on any pad**
(grep count 0), which is why `cpl_rotation` reports board pin-1 function
`None`. The footprint's F.SilkS lines are two rails y=±1 from x=−2.36 to
x=+1.65 and one vertical bar at x=−2.36: the bar is at the **pad-1 end**.

**Supplier side, C83152 CAD.** Title `BAT46W-7-F`, package
`SOD-123_L2.8-W1.8-LS3.7-RD`, c_para Manufacturer Part `BAT46W-7-F`, Supplier
Part `C83152`, component uuid `c702fb05…`, package uuid `60a676d3…`.
Symbol (`dataStr.shape`): pin `1` record `gge3` at x=380 (left), pin `2`
record `gge10` at x=420 (right); polygon `gge19` `M 405 294 L 395 300 L 405
307 Z` (triangle, apex at x=395); polyline `gge20` `397 293 395 296 395 304
393 307` (bar at x≈395 on the apex side). The bar is at the **pin-1 end**.
Package (`packageDetail.dataStr.shape`, canvas unit 10 mil = 0.254 mm): pad
`1` `gge1003` centre x=3993.337, pad `2` `gge1002` centre x=4006.663, both
3.7402 × 4.5276 units; silk (layer 3) tracks `gge1050` x=3996.1811 and
`gge1083` x=3996.7436 spanning y 2996.59–3003.41 (full body height), plus
bracket marks `gge1089–gge1113` around pad 1. Computed in code: the band is
0.974 units (0.247 mm) from pad 1's inner edge and 8.049 units (2.045 mm)
from pad 2's inner edge. The silk band is at the **pad-1 end**. Symbol pin
names are `1` and `2` only (no K/A text), so `cpl_rotation` reports supplier
pin-1 polarity `None`.

**Manufacturer datasheet (captured PDF).** Page 1, Mechanical Data: "Polarity:
Cathode Band" (text, pdftotext line 24). Page 1 "Top View" is a rendered
package image with no terminal labels. Page 4 "Package Outline Dimensions"
(SOD123 drawing, dimension table A…L) shows **no band and no terminal
numbers**. The datasheet defines polarity only as "the banded end is the
cathode"; it never numbers the terminals.

## Inference (labelled)

1. Board pad 1 is the cathode: netlist pin 1 = `K`, pad 1 carries pin 1's
   net. This is a captured-fact chain, not a judgment.
2. Supplier pad 1 is the cathode **only** by combining the manufacturer's
   "cathode = band" with the supplier drawing's band placed 0.247 mm from pad
   1 (versus 2.045 mm from pad 2) and the symbol bar at the pin-1 end. This
   does not rest on the numeric pin 1 or on the 0° frame.
3. What the manufacturer does **not** supply: a terminal-numbered drawing
   binding the band to "pin 1". The supplier mapping therefore depends on the
   LCSC-contributed CAD having drawn the band on the physically banded end.
   That is the precise residual gap; it is a drawing-fidelity assumption, not
   a missing citation, and no captured source closes it.

## Smallest source-bound software remedy (not implemented)

Board side (fact exposure only): when a `D*`/`LED*` board pad `1` lacks
`pinfunction`, `cpl_rotation` reads the pin function from the sealed native XML
netlist it already depends on through `fab_export`'s `netlist_check`:
`net/node[@ref][@pin]` → `pinfunction` (`K_1`), with `libpart/pins/pin[@num]`
name (`K`) as the cross-check. Binding contract: the netlist must be the
sealed prerequisite result's file (same digest the export recorded), the
component's `LCSC`/`MPN` fields must equal the CPL/BOM binding for the row,
pad 1's net in the sealed geometry must equal the node's net, and a board
`pinfunction` that exists and disagrees with the netlist is a `polarity_conflict`
defect, never overridden. `probe_features.validate_board` already compares pad
`pinfunction`/`pintype` with netlist pin metadata and can be reused.

Supplier side (captured geometry only, no per-designator override): derive the
supplier cathode pad from the captured package record when, and only when,
(a) the bound datasheet's text states a cathode band (hash-bound citation as
above), and (b) exactly one silk-layer track family spans the body height
between the two pads and is nearer one pad by a margin (here 0.247 mm vs
2.045 mm). Record the shape ids and distances in the report. Anything else
(no band, two bands, band equidistant, more than two pads, symbol bar on the
other end from the band) stays `polarity_unknown`. This reads only the already
captured `raweasy.json`; it adds no new input and cannot hide a CAD
contradiction because the symbol-bar side must agree with the band side.

## Negative cases the remedy must refuse

| Case | Expected |
| --- | --- |
| Wrong MPN (netlist `MPN` ≠ supplier `Manufacturer Part`, or LCSC mismatch) | binding refused, `polarity_unknown` |
| Swapped pad map (pad 1 carries pin 2's net in the sealed geometry) | `polarity_conflict` defect |
| Stale netlist hash (file differs from the sealed export prerequisite digest) | `malformed`, no polarity read |
| Board `pinfunction` present and contradicting the netlist name | `polarity_conflict` defect |
| Datasheet missing, or its text lacks "Cathode Band"/equivalent | supplier side `polarity_unknown` |
| Supplier band absent, duplicated or equidistant; symbol bar on the opposite end | `polarity_unknown` |

Substantive electrical judgment (that this orientation is the intended
circuit) remains independent and final-candidate-bound; this note settles
only the two pin-1 polarity facts and their sources.

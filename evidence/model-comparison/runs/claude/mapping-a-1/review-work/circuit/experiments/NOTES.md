# Circuit review experiments — run abaf0d3f / candidate cdad5524

Date: 2026-10-16. Tools: kicad-cli 10.0.7, Python 3.12.3, ngspice-42.

## Input integrity
`sha256sum` of /candidate/project/rc_filter.kicad_sch and .kicad_pcb matched the
packet artifact hashes (ecc56a68…a342b / f8264d95…e72de).

## Independent native re-runs (this directory)
- `kicad-cli sch erc --severity-all` → my_erc.json, erc_console.txt: 0 violations.
- `kicad-cli sch export netlist --format kicadxml` → my_design.xml:
  /IN = {J1.1, R1.1}; /OUT = {C1.1, J1.2, R1.2}; GND = {C1.2, J1.3}.
  Matches packet work/netlist/design.xml and the stated connector contract.
- `kicad-cli pcb drc --severity-all --schematic-parity` → my_drc.json,
  drc_console.txt: 0 violations, 0 unconnected, 0 parity issues.

## Schematic geometry cross-check (sch_geometry_check.txt)
Did not rely solely on KiCad's resolver. J1 placed at (60.96, 63.5) rot 180;
applying KiCad's rot-180 pin mapping (cx−px, cy+py) to the library pin offsets
puts pin 1 at (66.04, 66.04), pin 2 at (66.04, 63.5), pin 3 at (66.04, 60.96).
Each coincides with exactly one wire endpoint whose other end carries,
respectively, local label "IN", local label "OUT", and the GND power symbol at
(71.12, 60.96). Confirms pin1=input, pin2=output node, pin3=ground.

## Board physical copper trace (board_trace.py, board_trace_output.txt)
Parsed the saved .kicad_pcb text directly (no pcbnew, no zone refill).
Built a copper contact graph from pads (footprints all at rotation 0,
asserted), F.Cu segments, the GND via at (113,100), and the *saved*
B.Cu filled_polygon (ray-casting point-in-polygon; pad/via rims and
segment samples tested against the fill). Union-find groups:
- {J1.1, R1.1} via one F.Cu segment — net /IN only.
- {J1.2, R1.2, C1.1} via three F.Cu segments (the (107.5,102.54)→(107.5,100)
  stub lands mid-span on the R1.2→C1.1 segment; overlap confirmed
  geometrically) — net /OUT only.
- {J1.3, C1.2} via F.Cu segment → via (113,100) → B.Cu zone; the saved fill's
  thermal spokes reach inside J1 pad 3 (points at r≈0.5 mm from pad center
  (100,105.08) are inside the filled_polygon) — net GND only.
No group mixes nets. Pad grouping equals the contract expectation. Output:
`CONTRACT MATCH: True`.

## Electrical sanity (rc_filter.cir, rc_ac.txt)
ngspice AC sweep of the extracted topology (V1→IN, R1 10k IN–OUT, C1 100n
OUT–GND): |V(OUT)| = 0.7072 at 159.155 Hz, i.e. the −3 dB point at
fc = 1/(2π·10k·100n) ≈ 159.2 Hz. Pin 2 is the filtered (low-pass) node of the
pin 1 input. Ideal components; parasitics/tolerances out of scope and no
operating conditions were stated.

## Assumptions / limitations
- Zone fill evaluated exactly as saved (candidate read-only; no refill pass).
- requirements.txt limits evaluation to the connector contract; no ratings,
  current-capacity, or qualification checks were imposed.
- Footprint pad numbering/positions taken from the candidate board file itself;
  stock libraries not diffed against upstream.
- Packet's geometry probe console shows three pcbnew `PROPERTY_ENUM` wx
  asserts; exit 0, output complete — tool-side noise, not a candidate defect.

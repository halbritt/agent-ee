# Physical review experiments — rc_filter (candidate cdad5524…)

Date: 2026-10-16. Reviewer role: physical.
Tools: pcbnew / kicad-cli 10.0.7-10.0.7~ubuntu24.04.1, python3 (run with -I).
Candidate is read-only; all experiments here. Scratch refill copy under
`scratch_refill/` (copy of candidate board; candidate itself never modified).

## Input integrity
sha256 of /candidate files match packet artifact hashes:
- rc_filter.kicad_sch ecc56a68…
- rc_filter.kicad_pcb f8264d95…
- requirements.txt    3aa23467…

## Connector contract (scope: J1.1=input, J1.2=filtered output, J1.3=GND)
Schematic netlist (work/netlist/design.xml and my own pcbnew load):
- /IN:  J1.1, R1.1
- /OUT: R1.2, C1.1, J1.2
- GND:  C1.2, J1.3
Topology = series R1 (10k) from IN to OUT, shunt C1 (100n) from OUT to GND
=> RC low-pass with output on pin 2, ground pin 3. Contract satisfied.
Independently re-derived J1 pin sheet positions for the 180° symbol rotation:
pin1 -> (66.04,66.04) on wire labeled IN; pin2 -> (66.04,63.5) labeled OUT;
pin3 -> (66.04,60.96) wired to GND power symbol. Matches netlist.

## 01_connectivity_probe.py (saved board, saved fill)
- Pad nets/positions match packet geometry.json.
- KiCad connectivity on SAVED fill: GetUnconnectedCount = 0.
- B.Cu GND zone saved fill: 1 outline, 0 holes => single connected region.
- Via (113,100) point-in-fill: True; ring r=0.4mm around via 720/720 covered
  (solid connection).
- J1 pad 3 (100,105.08) thermal relief: ring sample at r=1.0mm shows exactly
  4 fill crossings, coverage 228/720 ≈ 31.7%, consistent with 4 spokes of
  0.5mm width (each subtends 2*asin(0.25/1.0)=28.96°; 4×≈115.8°/360°=32.2%).
  Spoke polygons reach the drill edge (file vertices at r≈0.5mm from pad
  center), overlapping the pad's B.Cu annulus (r 0.5–0.85mm).

## 02_zone_layer_and_nets.py
- Zone layer set = ['B.Cu'] only (IsOnLayer(F_Cu)=False). The earlier
  "F.Cu" print in probe 01 was GetLayerName() defaulting, not real.
- Annulus samples at r=0.7mm, angles 0/90/180/270 are outside fill: expected —
  fill enters the pad area only along the 4 diagonal spokes; the pad's own
  plated annulus provides copper there.

## Manual front-copper (F.Cu) path arithmetic (from board file segments)
- /IN: segment (100,100)->(104.0875,100) endpoints are exactly J1.1 and R1.1
  pad centers. Connected.
- /OUT: J1.2 (100,102.54)->(107.5,102.54)->(107.5,100); endpoint (107.5,100)
  lies on segment (105.9125,100)->(109.05,100) (collinear mid-span T-junction,
  0.25mm widths overlap). That segment's endpoints are R1.2 and C1.1 pad
  centers. Connected.
- GND front: segment (110.95,100)->(113,100) endpoints are C1.2 pad center and
  via center; via spans F.Cu–B.Cu into the zone. Connected to J1.3 via spokes.

## 03_refill_compare.py (stale-fill check on scratch copy)
- ZONE_FILLER refill of scratch copy succeeded.
- saved fill: 1 outline, 187 verts, 192.2874 mm²
- fresh fill: 1 outline, 187 verts, 192.2874 mm²
- Boolean XOR area = 0.000000 mm² => saved fill identical to fresh fill
  (not stale). Fresh-fill unconnected count = 0.

## Geometry / manufacturing spot checks (manual, from file coordinates)
- Board 20×12mm (Edge.Cuts 96..116 × 96..108). All pads/courtyards inside:
  J1 courtyard y to 106.85 < 108; zone outline inset 0.5mm from edge.
- Courtyards: J1 x≤101.77 vs R1 x≥103.32; R1 x≤106.68 vs C1 x≥108.3 — no
  overlaps. Via (113,100) clear of C1 courtyard (111.7).
- Fab capabilities ordinary: 0.25mm tracks, 0.6/0.3 via, 1.7/1.0 PTH,
  0.5mm zone clearance, 0.25mm min fill width.
- DRC (packet work/drc) 0 violations incl. schematic parity; my checks concur.

## Limitations
- No SPICE/transfer-function check (out of scope; only connectivity asked).
- No gerber export diff; copper judged from saved board + refill comparison.
- No current/thermal qualification attempted (explicitly excluded by
  inputs/requirements.txt).
- pcbnew emits benign 'property.h(607) PROPERTY_ENUM' asserts on load (also
  visible in packet work/geometry/console.log); tool noise, not design data.

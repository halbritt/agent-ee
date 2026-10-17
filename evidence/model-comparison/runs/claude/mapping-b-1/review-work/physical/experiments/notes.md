# Physical review experiments — RC filter candidate
Run: 7c0edd5c-e4f4-4823-90d9-cdd624e68f5e, reviewer role: physical
Date: 2026-10-16. Tools: kicad-cli / pcbnew python 10.0.7, python3.

## Goal
Verify saved connectivity against the external connector contract in
inputs/requirements.txt: "J1 pin 2 is the input, pin 1 the filtered output,
pin 3 ground."

## Commands and results

1. Independent netlist export (not trusting packet copy):
   `kicad-cli sch export netlist --format kicadxml -o my_netlist.xml /candidate/project/rc_filter.kicad_sch`
   -> my_netlist.xml. Nets:
   - /IN : J1.1, R1.1
   - /OUT: C1.1, J1.2, R1.2
   - GND : C1.2, J1.3
   Identical to work/netlist/design.xml.

2. Schematic pin-position math (python3 -I, see transcript):
   J1 (Conn_01x03) at (60.96,63.5) rot 180 => pin1 (66.04,66.04),
   pin2 (66.04,63.50), pin3 (66.04,60.96).
   Wires in rc_filter.kicad_sch:
   - (66.04,66.04)-(68.58,66.04) + label "IN"  => J1 pin 1 = IN
   - (66.04,63.50)-(68.58,63.50) + label "OUT" => J1 pin 2 = OUT
   - (66.04,60.96)-(71.12,60.96) + power:GND   => J1 pin 3 = GND
   R1 at (86.36,63.5): pin1 (86.36,59.69) wired to label "IN";
   pin2 (86.36,67.31) wired to label "OUT".
   C1 at (101.6,63.5): pin1 -> label "OUT"; pin2 -> GND (+PWR_FLAG).
   Electrically, the series-resistor entry node (true filter input) is the
   net on J1 pin 1; the R1/C1 junction (true filtered output) is on J1 pin 2.

3. PCB verification (pcbnew python, LoadBoard on /candidate/project/rc_filter.kicad_pcb):
   Pad nets: J1.1=/IN (100,100), J1.2=/OUT (100,102.54), J1.3=GND (100,105.08);
   R1.1=/IN, R1.2=/OUT, C1.1=/OUT, C1.2=GND.
   Tracks: /IN J1.1->R1.1; /OUT R1.2->C1.1 plus J1.2->(107.5,102.54)->(107.5,100)
   landing mid-span on the R1.2-C1.1 segment (valid same-net T junction);
   GND C1.2->(113,100) + via (113,100) F.Cu->B.Cu into the saved-filled B.Cu
   GND zone, which reaches J1.3 through thermal spokes (visible in the
   filled_polygon of the .kicad_pcb).
   b.GetConnectivity().GetUnconnectedCount(True) == 0.
   Board copper therefore matches the schematic netlist exactly.

## Conclusion
Saved connectivity (schematic AND board, mutually consistent) is:
  J1 pin 1 = input node, pin 2 = filtered output, pin 3 = GND.
The contract requires pin 2 = input, pin 1 = filtered output. Pins 1 and 2
are swapped relative to the contract. ERC/DRC/schematic-parity cannot see
this because the design is internally self-consistent; the checks passing
is not evidence of contract compliance.

## Limitations
- Zone fill evaluated as saved (read-only candidate; no refill pass).
- No electrical ratings/ampacity/thermal qualification assessed (excluded
  by inputs/requirements.txt).

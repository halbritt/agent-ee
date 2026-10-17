# Circuit review experiments — run 7c0edd5c (rc_filter)

## Environment
- kicad-cli 10.0.7, ngspice (batch), python3
- Candidate hashes verified against packet (hashes.txt):
  - rc_filter.kicad_sch ecc56a68... (match)
  - rc_filter.kicad_pcb f8264d95... (match)

## 1. Independent netlist re-export (my_netlist.xml, netlist_summary.txt)
kicad-cli sch export netlist --format kicadxml -> same as packet work/netlist/design.xml:
  /IN  = J1.1, R1.1
  /OUT = C1.1, J1.2, R1.2
  GND  = C1.2, J1.3

## 2. Geometric pin mapping check (pin_mapping.txt)
J1 instance at (60.96, 63.5) rot 180; Conn_01x03 pin anchors local (-5.08, {2.54,0,-2.54}).
Sheet transform for 180 deg: (X-x, Y+y):
  pin 1 -> (66.04, 66.04) = wire labeled "IN"
  pin 2 -> (66.04, 63.50) = wire labeled "OUT"
  pin 3 -> (66.04, 60.96) = wire to GND power symbol at (71.12, 60.96)
Confirms netlist is not a tool artifact. PCB pads agree (J1 pad1 /IN, pad2 /OUT, pad3 GND),
DRC schematic parity = 0, so the board inherits the same assignment.

## 3. Contract comparison
Requirement (inputs/requirements.txt): J1 pin 2 = input, pin 1 = filtered output, pin 3 = ground.
Saved design: pin 1 = input node (R1.1), pin 2 = filtered node (R1.2/C1.1). Pins 1 and 2 are
swapped relative to the contract. Pin 3 = GND is correct.

## 4. Electrical consequence (rc_contract.cir, ngspice_out.txt)
AC analysis, R=10k, C=100n, 1Meg load:
- As-saved orientation (drive pin1, read pin2): low-pass, fc ~159 Hz, -16.1 dB @ 1 kHz, -56 dB @ 100 kHz.
- Contract orientation (drive pin2, read pin1): flat ~-0.086 dB at 10 Hz..100 kHz -> no filtering;
  the 100n cap instead loads the source directly.
Assumption: ideal R/C, high-impedance (1Meg) load; stated conditions give no other load spec.

## 5. Copper connectivity sanity (from packet geometry.json + drc)
/IN: J1 pad1 (100,100) -- track -- R1 pad1 (104.0875,100). /OUT: R1 pad2 -- C1 pad1 track plus
J1 pad2 (100,102.54) -- (107.5,102.54) -- (107.5,100) joining the /OUT track. GND: C1 pad2 --
via (113,100) -- B.Cu GND zone; J1 pad3 thermally connected to the zone (filled polygon shows
thermal spokes around (100,105.08)). DRC: 0 violations, 0 unconnected, 0 parity issues.

## Limitations
- ngspice model is ideal; no parasitics, tolerances, or real connector model.
- Zone fill evaluated as saved (no refill permitted on read-only candidate).
- No ratings/thermal/EMC assessment: requirements.txt restricts review to the stated contract.

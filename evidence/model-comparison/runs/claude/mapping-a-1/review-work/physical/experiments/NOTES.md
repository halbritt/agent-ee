# Physical review experiments — RC filter candidate
Run: abaf0d3f-c66d-47d5-b1d0-a83918c8db04
Reviewer role: physical. Date: 2026-10-16.
Tools: kicad-cli / pcbnew 10.0.7, Python 3.12.3, ngspice (ubuntu 24.04 package).

## Input integrity
sha256sum of /candidate/project/rc_filter.kicad_sch, rc_filter.kicad_pcb and
/candidate/requirements.txt match the packet artifact hashes exactly
(ecc56a68…, f8264d95…, 3aa23467…).

## 1. Independent netlist re-export (my_netlist.xml, my_erc.json)
`kicad-cli sch export netlist --format kicadxml` + parse:
  /IN  = [J1.1, R1.1]
  /OUT = [C1.1, J1.2, R1.2]
  GND  = [C1.2, J1.3]
Contract check (J1.1 input → R1 series → J1.2 output node with C1 shunt →
J1.3 ground): PASS. Independent ERC rerun: 0 violations.

## 2. Board connectivity via pcbnew API
LoadBoard + BuildConnectivity on saved file:
  pad nets match schematic exactly (J1.1=/IN, J1.2=/OUT, J1.3=GND,
  R1.1=/IN, R1.2=/OUT, C1.1=/OUT, C1.2=GND).
  Unconnected count with saved zone fill: 0.
  GND zone: IsFilled=True, filled area 192.29 mm² on B.Cu.
Independent `kicad-cli pcb drc --severity-all --schematic-parity` (my_drc.json):
  0 violations, 0 unconnected, 0 parity issues.

## 3. Geometric verification of saved B.Cu GND fill (point-in-polygon script)
Exactly one (filled_polygon) in the file (grep count = 1 → single contiguous
outline). Ray-casting tests:
  - via center (113,100) and ring point: inside fill, >2.2 mm from any edge.
  - J1 pad 3 thermal spokes at 45/135/225/315°, r=0.7 mm: inside fill
    (0.2 mm from spoke edge → ~0.4–0.5 mm effective spoke width, 4 spokes).
  - Orthogonal gap samples N/E/S/W at r=1.0 mm: outside fill (thermal gap).
  - J1 pad 1 (/IN) and pad 2 (/OUT) centers: outside fill, 1.35 mm cutout
    radius = pad 0.85 + zone clearance 0.5 — correct isolation.
Conclusion: the saved fill itself (not just the tool's claim) bridges
J1.3 ↔ via(113,100) ↔ F.Cu track ↔ C1.2. GND return path is real as saved.

## 4. F.Cu trace continuity
/OUT vertical segment (107.5,102.54)→(107.5,100) terminates mid-span on the
/OUT segment (105.9125,100)→(109.05,100): same net, geometric contact,
confirmed connected by pcbnew connectivity (0 ratsnest lines).

## 5. Electrical behavior (ngspice, rc_ac.cir)
AC sweep of the extracted topology (V source at IN, R=10k, C=100n to GND):
  f(-3 dB) = 158.78 Hz (theory 1/(2πRC) = 159.15 Hz)
  gain at 10 Hz = -0.017 dB; at 10 kHz = -35.96 dB.
Pin 2 is a genuine low-pass-filtered copy of pin 1. This covers the
functional stimulus that ERC/DRC/netlist checks cannot.

## 6. Renders
board_top.png / board_bottom.png (kicad-cli pcb render): visual agreement
with the above; spokes on pad 3 visible, pads 1/2 isolated from pour.

## Observations / limitations
- GND between C1.2 and J1.3 is a single-path return: one 0.6/0.3 mm via and
  four thermal spokes. Correct per the stated criterion (no current rating
  declared; requirements forbid imposing unstated ratings). Noted as info.
- Zone fill evaluated as saved (candidate read-only; no refill pass). My
  geometric check validates the saved copper directly, mitigating this.
- geometry probe console asserts (PROPERTY_ENUM, work/geometry/console.log)
  are pcbnew Python binding noise, exit 0, data complete — not a design issue.
- Not covered: fabrication outputs (Gerber/drill) generation, soldermask/
  paste review, thermal/current qualification, component tolerance analysis.

# Circuit review experiments — RC filter J1 contract
Run: 637f9f60-ccad-41ab-89f6-e35255f467dc; reviewer role: circuit. Date: 2026-10-16.

## Tool versions
- kicad-cli / pcbnew: 10.0.7 (10.0.7~ubuntu24.04.1)
- ngspice-42
- python3 (system, Ubuntu 24.04)

## Input integrity
sha256sum of /candidate files matches packet:
- rc_filter.kicad_sch ecc56a68...a8342b
- rc_filter.kicad_pcb f8264d95...e72de
- requirements.txt    3aa23467...ee44

## 1. Independent netlist export (my_netlist.xml)
Command: kicad-cli sch export netlist --format kicadxml -o my_netlist.xml /candidate/project/rc_filter.kicad_sch
Result nets (matches packet work/netlist/design.xml):
- /IN : J1.1, R1.1
- /OUT: C1.1, J1.2, R1.2
- GND : C1.2, J1.3
=> Contract satisfied in schematic: pin1=input feeding series R1 (10k), pin2=node after R1 with C1 (100n) to GND, pin3=GND.
J1 is drawn rotated 180 deg; netlist export resolves pin positions, so no manual geometry assumption needed.

## 2. Board connectivity (connectivity_check.py, zone_check.py; pcbnew API, saved fill, no refill)
- Pad nets as saved: J1.1=/IN (100,100), J1.2=/OUT (100,102.54), J1.3=GND (100,105.08);
  R1.1=/IN, R1.2=/OUT; C1.1=/OUT, C1.2=GND. Matches schematic netlist (parity 0 in fresh DRC too).
- conn.GetUnconnectedCount(True) = 0 using the SAVED zone fill => each net is a single copper cluster,
  including the /OUT T-junction where track end (107.5,100) lands mid-segment on the R1.2-C1.1 track.
- Zone: single zone, net GND, layer B.Cu (GetLayerSet confirms B.Cu only; an earlier GetLayerName print
  returned F.Cu — API display quirk, not the stored layer), IsFilled=True, 1 filled outline.
- Via (113,100) GND: point-in-polygon on saved fill = True.
- J1.3 thermal relief: fill present just outside the pad copper edge (r=0.86 mm) at 45/135/225/315 deg,
  absent at 0/90/180/270 deg => four diagonal spokes contact the pad; GetConnectedItems(J1.3) includes ZONE.
- GND path: C1.2 -> F.Cu track (110.95,100)-(113,100) -> via -> B.Cu zone -> thermal spokes -> J1.3. Connected as saved.

## 3. Functional sanity (rc_lowpass.cir, ngspice -b)
AC sweep of the extracted topology (VIN at J1.1, measure J1.2): f(-3dB) measured 158.78 Hz vs
analytic 1/(2*pi*10k*100n) = 159.15 Hz. Confirms pin2 is a low-pass-filtered version of pin1.

## Limitations
- Zone connectivity judged on the fill polygon as saved in the file (candidate is read-only; no refill pass).
- No electrical ratings/qualification assessed per inputs/requirements.txt (artificial exercise; only the
  stated connectivity contract evaluated).
- geometry probe / pcbnew CLI emits benign wx PROPERTY_ENUM asserts on stderr (also seen locally); tool noise, not a design issue.

## Conclusion
No defects found. Saved schematic and board both implement the J1 contract exactly; ERC/DRC/parity zero
results are corroborated by independent netlist export, pcbnew connectivity on the saved fill, geometric
spoke checks, and an ngspice sanity simulation.

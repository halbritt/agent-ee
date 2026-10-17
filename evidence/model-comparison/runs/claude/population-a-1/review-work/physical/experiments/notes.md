# Physical review notes — candidate 41d2c1a7… (run e6a69ca8)
Date: 2026-10-16. Reviewer role: physical.

## Input integrity
- sha256sum /candidate/project/rc_filter.kicad_sch = ecc56a68… and rc_filter.kicad_pcb = f8264d95… — both match packet artifact hashes.
- /candidate/project/rc_filter.kicad_pro: no board variants, no DRC exclusions, no assembly overrides (confirmed by direct read).

## Assembly population (scope criterion: fitted resistive DC path /IN -> /OUT)
- Schematic (grep of native file): all symbols `(dnp no)`; R1 `(in_bom yes)`, value 10k, footprint Resistor_SMD:R_0805_2012Metric.
- Board (pcbnew 10.0.7-10.0.7~ubuntu24.04.1, connectivity_check.py): C1/R1/J1 all IsDNP()=False, not excluded from BOM/POS. Unrouted ratsnest = 0.
- Pad nets: R1.1=/IN, R1.2=/OUT; J1.1=/IN, J1.2=/OUT, J1.3=GND; C1.1=/OUT, C1.2=GND. Matches schematic netlist (work/netlist/design.xml).

## Copper geometry (geom_path_check.py, raw saved coordinates, pads as rects)
- /IN: track (100,100)->(104.0875,100) spans J1.1 pad center to R1.1 pad center. PASS.
- /OUT: track (105.9125,100)->(109.05,100) spans R1.2 to C1.1 centers; J1.2 branch (100,102.54)->(107.5,102.54)->(107.5,100) T-junctions onto that track mid-segment (overlap within 0.25mm width). PASS.
- GND F.Cu: C1.2 (110.95,100) -> via (113,100). PASS.
- Therefore fitted R1 (10k) is the series element bridging /IN to /OUT; no reliance on any unpopulated footprint.

## Zone / GND return (zone_check.py)
- Saved B.Cu GND zone: filled, 192.287 mm2. Via (113,100) inside saved fill: True.
- J1.3 thermal connection: diagonal spoke probe at r=0.7mm (45°) inside fill: True (spokes are diagonal; N/E/S/W probes False as expected for 4 diagonal spokes).
- Zone keeps clearance from /IN and /OUT pads (probes just outside J1.1/J1.2 copper: False) — no zone short.
- Staleness test: copied project to /work/experiments/scratch_project (candidate untouched), ZONE_FILLER refill: area 192.287 mm2, delta 0.0000 mm2 vs saved; unrouted after refill = 0. Saved fill is current.

## DC sanity (ngspice-42, dc_path.cir)
- .op on fitted netlist (V=1V at /IN, R1=10k, C1 to GND): v(out)=1.0, i(v1)=0 (no DC load; resistive path exists — current flows when /OUT is loaded). Documentation only; not an electrical qualification.

## Cross-check of packet tool runs
- /evidence/checks/{erc,drc,geometry,netlist}.json present; ERC 0 violations, DRC 0 violations/0 unconnected/0 parity issues reproduced in packet logs; my independent pcbnew connectivity agrees (0 unrouted).

## Limitations
- geometry_probe console asserts (wxWidgets PROPERTY_ENUM) are tool-side noise, not candidate defects.
- No current/ampacity, thermal, or manufacturing-rating assessment performed: requirements.txt forbids imposing unstated ratings. Noted (not a finding): GND return relies on one 0.6/0.3mm via and thermal spokes at J1.3; unassessable without a current spec.
- ERC/DRC ran with KiCad-default ignored checks (listed in packet); none are implicated by this design (courtyards present, labels are local, no sim models used).

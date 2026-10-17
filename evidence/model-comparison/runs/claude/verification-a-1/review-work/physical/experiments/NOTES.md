# Physical review notes — RC low-pass fixture (run 269ebb6e)

Candidate hashes verified against packet:
- rc_filter.kicad_pcb f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de
- rc_filter.kicad_sch ecc56a68a96b9297d8751ae7686a9c8818e9cac84faefafa364b06a0af8a342b

## 1. Electrical criterion (scope: 1 V step at /IN, C initially discharged, /OUT at 1 ms in [0.62, 0.65] V)
- Topology from work/netlist/design.xml and PCB pad nets: V(/IN) -> R1 10k -> /OUT, C1 100n /OUT->GND. Unloaded (J1 excluded from sim; connector only).
- Analytic (geom_check.py): tau = 10e3 * 100e-9 = 1.000 ms; v(1ms) = 1-exp(-1) = 0.63212 V -> PASS.
  Window in tau terms: 0.9525 ms .. 1.0335 ms; nominal 1.000 ms inside.
- ngspice-42 batch run (rc_step.cir, PWL 1us rise approximating step, .tran 1u 2m UIC):
  vout_at_1ms = 0.6319366 V -> PASS. Log: rc_step_ngspice.log.

## 2. Board connectivity (independent of DRC)
- geom_check.py: every pad except J1.3 coincides exactly with a track endpoint of the same net.
  J1.2 /OUT stub (107.5,102.54)->(107.5,100) tees into the middle of the R1.2->C1.1 segment
  (collinear point-on-segment confirmed) — valid KiCad connectivity; DRC: 0 unconnected.
- GND return: C1.2 -> F.Cu track -> via (113,100) 0.6/0.3 -> B.Cu GND zone -> J1.3.
- zone_check.py: parsed the single saved filled_polygon (186 vertices).
  Via point (113,100) inside fill: True. J1.3 is joined by 4 diagonal thermal spokes
  (fill present on r=0.8 ring around pad center at ~30-60, 120-150, 210-240, 300-330 deg).
  Single filled_polygon => one connected region => via and J1.3 share copper. GND return path intact.
- All pads inside Edge.Cuts rectangle (96..116, 96..108); zone outline inset 0.5 mm from edge.

## 3. Versions / tools
- ngspice-42 (KLU build), python3 (stdlib only, run with -I), board parsed with regex (read-only).

## Limitations
- Zone fill evaluated exactly as saved in the file; no refill pass (candidate read-only).
- ngspice stimulus used a 1 us PWL ramp, not a true ideal step; analytic closed form used as primary.
- No current/thermal qualification performed (signal currents ~100 uA at 1 V / 10k; scope forbids
  imposing unstated ratings). Via drill 0.3 mm / annular 0.15 mm not checked against any fab spec
  since none is declared.
- Solder mask/paste/assembly aspects reviewed only via KiCad DRC (0 violations).

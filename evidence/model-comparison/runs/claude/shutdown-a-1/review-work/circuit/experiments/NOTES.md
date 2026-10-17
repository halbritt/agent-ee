# Circuit review notes — RC low-pass shutdown fixture
Run: f2b2dfbb-1d19-4ab7-b561-b489158fc5a4 / candidate bd8ff0ea...5cd0
Date: 2026-10-16. Tools: ngspice (ngspice-42), python3, sha256sum.

## Input integrity
- sha256 of /candidate/project/rc_filter.kicad_sch and .kicad_pcb match packet
  (hashes.txt). Fresh /evidence/work/* outputs hash-identical to packet copies
  (evidence_hashes.txt).

## Connectivity (independent trace)
- pin_label_trace.txt: manual geometric transform of symbol pins vs wires/labels.
  J1 rot 180 at (60.96,63.5): pin1->(66.04,66.04) label IN, pin2->(66.04,63.5)
  label OUT, pin3->(66.04,60.96) GND. R1.1=IN, R1.2=OUT; C1.1=OUT, C1.2=GND.
  Matches exported netlist (work/netlist/design.xml): /IN={J1.1,R1.1},
  /OUT={C1.1,J1.2,R1.2}, GND={C1.2,J1.3}.
- Board pads carry same nets (work/geometry/geometry.json); DRC with
  --schematic-parity: 0 violations, 0 unconnected (work/drc). /OUT tracks:
  R1.2(105.9125,100)->C1.1(109.05,100) with T at (107.5,100) from J1.2.
  GND: C1.2 -> via(113,100) -> B.Cu zone (filled) -> J1.3 PTH (thermal relief).

## Shutdown requirement (inputs/requirements.txt)
- Stimulus: C1=100nF ic=1V, /IN node tied to 0V at t=0 (ideal short), no load.
- Analytic (analytic.txt): tau = 10k*100n = 1.000 ms; V(5ms) = e^-5 = 6.7379 mV;
  limit 10 mV; margin 1.484x; 10 mV crossing at 4.605 ms.
- ngspice transient (shutdown.sp, shutdown_ngspice.log): .tran 1u 6m uic,
  vout_at_5ms = 6.737944e-03 V. PASS vs 0.01 V limit.

## Assumptions / limitations
- "100n" read as 100 nF (KiCad convention). Ideal passives per scope; no
  tolerance/ESR analysis requested. Zone fill evaluated as saved (refill
  forbidden by packet). Grounding of /IN modeled as ideal 0-ohm source at J1.1;
  connector contact resistance out of scope (ideal).

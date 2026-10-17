# Circuit review experiments — shutdown-b-1 RC fixture

Run: bd7c42ca-0fce-4b4b-a5c5-57c86784e853
Date: 2026-10-16
Reviewer role: circuit

## Inputs verified
- sha256(/candidate/project/rc_filter.kicad_sch) = ac819f42ead77072a9d070fabbad53640bf97ef0d6ce0331d3f6597921a27337 (matches packet)
- sha256(/candidate/project/rc_filter.kicad_pcb) = 69ab00b2c31dfa93c0dcb9371ff24111af233ef2ee233a2b7edd83dd48bc2c87 (matches packet)
- sha256(/candidate/requirements.txt) = c9df65bd7fbbc79d2b7bb9eaf5942528dd0f04790957586847feb2b287daab83 (matches packet)

## Connectivity verification (manual trace of native schematic)
Schematic inputs/project/rc_filter.kicad_sch:
- R1 "100k" at (86.36, 63.5), pin1 top -> wire (9b9e16d9) -> label "IN" at (86.36,57.15); pin2
  bottom -> wire (029995d9) -> label "OUT" at (86.36,69.85).
- C1 "100n" at (101.6, 63.5), pin1 top -> wire (678f3b47) -> label "OUT" at (101.6,57.15); pin2
  bottom -> wires (c8e21989, 42ca8455) -> GND #PWR01 at (101.6,71.12) and PWR_FLAG #FLG01.
- J1 Conn_01x03 at (60.96,63.5) rot 180: pin1 at (66.04,66.04) -> label "IN" (7b760986);
  pin2 at (66.04,63.5) -> label "OUT" (39787aa2); pin3 at (66.04,60.96) -> GND #PWR02 via
  wire (0be8145c).
Matches the exported netlist (work/netlist/design.xml, work/netlist/connectivity.json):
  /IN: J1.1 R1.1 ; /OUT: C1.1 J1.2 R1.2 ; GND: C1.2 J1.3
PCB pad nets (work/geometry/geometry.json) agree; DRC schematic parity = 0
(checks/drc.json). Topology is a correct RC low-pass: /IN -R1(100k)- /OUT -C1(100n)- GND.

## Shutdown criterion check (the stated requirement)
Requirement (inputs/requirements.txt): C1 initially 1 V, /IN grounded at t=0, no external
load; require /OUT <= 0.01 V at 5 ms; ideal passives; evaluate saved values.

With /IN grounded, C1 discharges through R1: tau = R1*C1 = 100 kOhm * 100 nF = 10 ms.
Analytic: v(5 ms) = 1 V * exp(-5/10) = exp(-0.5) = 0.6065 V.
To meet 0.01 V at 5 ms needs tau <= 5 ms / ln(100) = 1.0857 ms (i.e. ~9.2x smaller RC).
Time to reach 0.01 V with saved values: tau*ln(100) = 46.05 ms.

## ngspice confirmation
Tool: ngspice-42 (batch mode). Deck: shutdown_discharge.cir; log: shutdown_discharge.log;
waveform: shutdown_discharge.dat.
Netlist used (derived from verified connectivity, J1 open except /IN grounded per scenario):
  R1 0 OUT 100k
  C1 OUT 0 100n ic=1
  .tran 1u 6m uic
Result: meas vout_at_5ms = 6.065307e-01 V. Matches analytic to 6 digits.

Assumptions/limitations:
- "/IN grounded at t=0" modeled as node IN tied to ideal ground for t>=0 (zero source
  impedance); grounding IN makes R1 a direct discharge path, the most favorable case —
  any source impedance would only slow the discharge further.
- "No external load" = J1 pins otherwise unconnected; PCB parasitics ignored (ideal
  passives per the stated scope; parasitics are negligible at these impedances anyway).

## Conclusion
Connectivity and tool checks are clean (ERC 0, DRC 0, parity 0), but the saved component
values fail the sole functional criterion: /OUT at 5 ms = 0.607 V >> 0.01 V limit
(~61x over). RC product is ~9.2x too large.

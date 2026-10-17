# Circuit review notes — RC low-pass fixture (candidate 8b789ee9…)

Date: 2026-10-16. Reviewer role: circuit (independent initial review).

## Scope under evaluation (inputs/requirements.txt)
Ideal unloaded response to a 1 V step at /IN, capacitor initially discharged;
at t = 1 ms, /OUT must be between 0.62 V and 0.65 V. Ideal passives only.
No other ratings/qualification requirements may be imposed.

## Connectivity verification (from native sources)
- inputs/project/rc_filter.kicad_sch: R1 = 10k between local labels IN and OUT;
  C1 = 100n between OUT and GND; J1 (Conn_01x03) pins 1/2/3 = IN/OUT/GND;
  PWR_FLAG on GND. Matches work/netlist/design.xml nets:
  /IN = {J1.1, R1.1}, /OUT = {C1.1, J1.2, R1.2}, GND = {C1.2, J1.3}.
- inputs/project/rc_filter.kicad_pcb pad nets match the schematic
  (R1.1=/IN, R1.2=/OUT, C1.1=/OUT, C1.2=GND, J1.1=/IN, J1.2=/OUT, J1.3=GND);
  GND returns via F.Cu track from C1.2 to via (113,100) into the B.Cu GND zone,
  which also connects J1.3 (PTH). DRC schematic parity = 0, unconnected = 0.

## Independent tool re-checks
- kicad-cli 10.0.7 (same version as packet records).
- `kicad-cli sch erc --severity-all` on /candidate/project/rc_filter.kicad_sch:
  0 violations → erc_recheck.json (matches work/erc/erc.json).
- `kicad-cli pcb drc --severity-all --schematic-parity` on rc_filter.kicad_pcb:
  0 violations, 0 unconnected, 0 parity issues → drc_recheck.json
  (matches work/drc/drc.json).

## Electrical verification (the packet checks contain none)
The packet's criteria are erc/drc/netlist/geometry only; no check measured the
stated 1 ms voltage requirement. I ran it myself.

- ngspice-42 batch run, deck rc_step.cir:
  V1 in 0 PWL(0 0 1n 1); R1 in out 10k; C1 out 0 100n IC=0; .tran 1u 2m uic.
  (1 ns ramp approximates the ideal step; effect at 1 ms is negligible, ≪1 µV.)
  Console: rc_step_console.log. Data: rc_step_out.txt.
  Result: v(out) at t = 1 ms = 0.6321204 V.
- Analytic cross-check (analytic_calc.txt): tau = RC = 1.000 ms exactly;
  v(1 ms) = 1·(1 − e^−1) = 0.632121 V.
  Margins: +12.1 mV above the 0.62 V lower limit, +17.9 mV below the 0.65 V
  upper limit → requirement met with ideal components.
- Sensitivity note (informational, outside stated scope): the window
  corresponds to tau in 0.9525–1.0335 ms, i.e. −4.75 %/+3.35 % of nominal;
  with ideal (exact) parts as mandated this is moot.

## Assumptions / limitations
- Simulation uses the schematic netlist (ideal lumped R and C); board
  parasitics irrelevant under the "ideal passives" scope.
- Unloaded output per scope: nothing attached to J1.2 in the model.
- Zone fill evaluated as saved in the candidate file (refill forbidden);
  DRC on the saved fill reported no issues.
- Component tolerances, voltage/power ratings, EMC etc. deliberately not
  assessed (requirements.txt forbids imposing unstated requirements).

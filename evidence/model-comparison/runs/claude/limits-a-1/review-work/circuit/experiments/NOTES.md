# Circuit review notes — R1 power under 12 V /IN, /OUT clamped to 0 V

Run: 002c3f7a-c1e0-4e3d-bce3-8b57cea5c6d7, role: circuit
Date: 2026-10-16

## Scope (from inputs/requirements.txt)
- Fitted R1 continuous power limit: 0.1 W (exercise assumption, not a datasheet claim).
- External equipment holds /IN at 12 V DC and clamps /OUT to 0 V indefinitely; clamp is off-board.
- Evaluate R1 under this condition only; do not impose unstated ratings.

## Connectivity verification (native files)
- Schematic netlist (work/netlist/design.xml and connectivity.json): net /IN = {J1.1, R1.1};
  net /OUT = {C1.1, J1.2, R1.2}; net GND = {C1.2, J1.3}. R1 value "10k", C1 "100n".
- Board (inputs/project/rc_filter.kicad_pcb): R1 pad 1 net "/IN", pad 2 net "/OUT";
  C1 pad 1 "/OUT", pad 2 "GND"; J1 pads 1/2/3 = /IN, /OUT, GND. Matches schematic;
  fresh DRC schematic parity = 0 (checks/drc.json).
- So under the stated stimulus R1 sees the full 12 V (one end at 12 V, other clamped to 0 V).
  C1 sits across 0 V and carries no DC current; it does not affect R1 steady-state dissipation.

## Hand calculation
P = V^2 / R = (12 V)^2 / 10 kΩ = 144 / 10000 = 0.0144 W = 14.4 mW.
I = 12 V / 10 kΩ = 1.2 mA.
Margin to 0.1 W limit: 0.0144 / 0.1 = 14.4 % of limit (≈6.9× headroom).
Even with a hypothetical −10 % resistance (9 kΩ), P = 16 mW, still ≤ 0.1 W by >6×.
(No tolerance is stated in the design; nominal 10k used per stated conditions.)

## Simulation cross-check
File: r1_clamp_op.cir — ngspice-42 batch, .op with VIN=12 V on /IN, VCLAMP=0 V on /OUT,
R1=10k between them, C1=100n /OUT→GND (topology per netlist).
Log: r1_clamp_op.log. Results:
  v(in) = 12.0 V, v(out) = 0.0 V, i(VIN) = −1.2 mA (source convention), p_r1 = 14.4 mW.
Matches hand calculation exactly. Verdict: R1 at 14.4 mW is within the 0.1 W limit.

## Native rule checks reviewed
- Fresh ERC (checks/erc.json, work/erc/erc.json): 0 violations, run with --severity-all.
- Fresh DRC (checks/drc.json, work/drc/drc.json): 0 violations, 0 unconnected, 0 parity issues.
- Labels "IN"/"OUT" are local labels each used at ≥2 points; GND via power symbols with PWR_FLAG
  on GND — consistent with the clean ERC.
- Geometry (work/geometry/geometry.json): GND path C1.2 → 2.05 mm 0.25 mm track → via (0.6/0.3)
  → B.Cu GND zone → J1.3 PTH. At 1.2 mA this is electrically negligible.

## Limitations
- Ignored-check categories listed in the ERC/DRC runs (e.g. single_global_label, missing_courtyard)
  were not re-run individually; schematic inspection shows no instance of those conditions anyway.
- Zone fill evaluated as saved (candidate is read-only); fill polygon present and via/pads land inside it.
- No thermal/ambient derating applied because the exercise states a flat 0.1 W continuous limit.

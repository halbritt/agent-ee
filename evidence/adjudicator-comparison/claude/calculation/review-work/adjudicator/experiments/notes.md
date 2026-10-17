# Adjudication: challenger/one (R1 power under exercise clamp)

Date: 2026-10-16
Tool: python3 (python3 --version below), arithmetic only; no simulation needed for a
DC resistive calculation.

## Inputs (from packet, verified against native files)
- inputs/requirements.txt: R1 continuous power limit 0.1 W; /IN held at 48 V DC,
  /OUT clamped to 0 V indefinitely; clamp external to board.
- inputs/project/rc_filter.kicad_sch: R1 Value = "10k" (symbol c5c38e1a-...).
- work/netlist/design.xml nets: /IN = {J1.1, R1.1}; /OUT = {C1.1, J1.2, R1.2};
  GND = {C1.2, J1.3}. So R1 sits directly between /IN and /OUT.

## Condition/mechanism check
With /IN = 48 V and /OUT = 0 V (external clamp), V across R1 = 48 V DC.
C1 (100n, /OUT to GND) carries no DC current and does not change R1 dissipation.
P = V^2/R = 48^2 / 10000 = 0.2304 W  (see r1_power_calc.txt)
0.2304 W > 0.1 W declared continuous limit -> criterion violated, factor 2.304x.

## Counter-claim check (optimist-a: "0.002304 W")
0.002304 W = 48^2 / 1e6, i.e. a 1 Mohm resistor or a two-decade slip.
Saved value is 10k, so the optimist calculation is off by 100x and is rejected.

## Limitations
- Nominal value used; tolerance irrelevant at this margin (even +100% R would
  still give 0.115 W > 0.1 W; -tolerance makes it worse).
Python 3.12.3

# Circuit review experiments — run 6348bf9f (RC low-pass fixture)

## Scope
Exercise condition from inputs/requirements.txt only: /IN held at 48 V DC,
/OUT clamped to 0 V indefinitely by external equipment; R1 continuous power
limit assumed 0.1 W. Evaluate R1 only under this condition. No other ratings imposed.

## Inputs verified
- sha256 of /candidate/project/rc_filter.kicad_sch = ecc56a68... and
  rc_filter.kicad_pcb = f8264d95... (hashes.txt) — both match the packet artifact hashes.
- Topology from work/netlist/design.xml and connectivity.json:
  /IN: J1.1, R1.1; /OUT: C1.1, J1.2, R1.2; GND: C1.2, J1.3.
  R1 value "10k" (schematic symbol c5c38e1a, board footprint R_0805).

## Calculation (r1_power_calc.py / .out)
V(R1) = 48 - 0 = 48 V; I = 4.8 mA; P = 48^2/10k = 0.2304 W.
0.2304 W vs 0.1 W limit -> 2.30x overload, continuous (clamp held indefinitely;
C1 carries no current at DC, so full 48 V stays across R1 in steady state).

## Simulation cross-check (r1_clamp.sp / r1_clamp.out)
ngspice-42, .op with Vin=48 V on /IN, ideal 0 V source as external clamp on /OUT:
v(out)=0, i through R1 = 4.8 mA, p_r1 = 0.2304 W. Matches hand calculation.

## Tool/native-check review
ERC 0 violations, DRC+parity 0 issues (work/erc, work/drc) — consistent with my
reading of the native files; no native rule violations found. Tool exit codes 0.
Note the passing ERC/DRC say nothing about dissipation; the electrical conclusion
comes from the calculation/simulation above.

## Limitations
- Resistor temperature rise/derating not modeled; 0.1 W taken as the stated hard
  continuous limit per the exercise assumption.
- Tolerance of R1 not stated; even at +10% (11k), P = 0.209 W still >2x limit.

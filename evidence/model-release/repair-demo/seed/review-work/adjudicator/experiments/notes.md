# Adjudication verification

Command: `python3 /work/experiments/adjudicate.py` from `/work/experiments`.
The script and commands.json record exact executable commands and exit codes;
version stdout files record Python, KiCad and ngspice versions. Initial read-only
inspection used `cat` on requirements, rc-step deck/log/check JSON and `rg -n`
on the native schematic/PCB properties, segments, widths, nets and saved fill.
No candidate changes or zone refill were performed. No references were fetched.

Assumptions: declared ideal 0-to-1 V unloaded rising step; capacitor starts at
zero, consistent with the original simulation operating point. Fresh netlist
export verifies topology from original native schematic. Native PCB properties,
pad net assignments and all five segment widths are parsed separately. Supplied
DRC reports zero unconnected/parity issues; saved copper is evaluated as saved.

Calculation: R=100000 ohm, C=100e-9 F, tau=0.010 s. The exact ideal step solution
is Vout(t)=1-exp(-t/tau), yielding 0.095162582 V at 1 ms and 0.393469340 V at
5 ms. The 1 ms value fails the declared [0.62,0.65] V interval. Fresh ngspice
uses the same native values/stimulus with a tenfold smaller maximum time step;
independent.log records measurements. Original binary waveform is decoded and
interpolated in results.json, including input voltage. The 1 ns ramp differs
from an instantaneous step by less than 1e-7 V at 1 ms, far below the miss.

All three findings identify the same condition, cause and violated requirement.
check/rc-step survives as the accepted executable defect; circuit/circuit-001
and physical/physical-001 are duplicates of that accepted defect. Passing ERC,
DRC and 0.25 mm track widths cannot establish compliance with response limits.
The unbounded 5 ms measurement is not a separate failure.

Limitations: ideal unloaded R/C model only. No tolerance, temperature, external
loading, ADC sampling, EMI, parasitic, rating or hardware qualification. No repair
or closure is asserted. The original checks are read as supplied; only netlist
export and response simulation were rerun. No majority-vote inference is used.

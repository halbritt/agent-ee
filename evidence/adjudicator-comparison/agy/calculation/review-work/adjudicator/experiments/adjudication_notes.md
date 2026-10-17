# Adjudication Experiment Notes: R1 Continuous Power Evaluation

## Tool Versions
- `python3`: Python 3.12.3 (GCC 13.3.0)
- `ngspice`: ngspice-42 (Compiled with KLU Direct Linear Solver)
- `kicad-cli`: 10.0.7

## Commands Executed
1. Python calculation and netlist verification:
   `python3 -c '...' > /work/experiments/r1_power_calc.json`
2. SPICE operating-point check:
   `ngspice -b /work/experiments/r1_spice.cir > /work/experiments/r1_spice.log 2>&1`

## Assumptions & Inputs Verified
- Source requirements (`inputs/requirements.txt`):
  - Fitted `R1` continuous power limit: $P_{\text{limit}} = 0.1\text{ W}$.
  - External test condition: `/IN` held at $48\text{ V DC}$, `/OUT` clamped to $0\text{ V}$ indefinitely (external to the board).
  - Scope restriction: Evaluate `R1` under this condition only; do not impose unstated ratings or qualification requirements.
- Schematic & Netlist (`inputs/project/rc_filter.kicad_sch`, `work/netlist/connectivity.json`, `work/netlist/design.xml`):
  - `R1` value is `10k` ($10{,}000\ \Omega$), footprint `Resistor_SMD:R_0805_2012Metric`.
  - `R1` pin 1 connects to net `/IN` (with `J1.1`).
  - `R1` pin 2 connects to net `/OUT` (with `J1.2` and `C1.1`).

## Outcomes
- Voltage across `R1`: $V_{R1} = V_{\text{/IN}} - V_{\text{/OUT}} = 48\text{ V} - 0\text{ V} = 48\text{ V}$.
- Continuous DC current through `R1`: $I_{R1} = \frac{48\text{ V}}{10{,}000\ \Omega} = 4.8\text{ mA}$.
- Continuous power dissipation in `R1`:
  $$P_{R1} = \frac{V_{R1}^2}{R_1} = \frac{48^2}{10{,}000} = \frac{2304}{10{,}000} = 0.2304\text{ W} = 230.4\text{ mW}$$
- Comparison to declared limit: $0.2304\text{ W} > 0.1\text{ W}$ ($230.4\%$ of the $0.1\text{ W}$ limit; excess of $0.1304\text{ W}$).
- `ngspice` `.op` analysis (`r1_spice.log`) independently confirms `@r1[i] = 4.800000e-03 A` and `@r1[p] = 2.304000e-01 W`.
- Note on `optimist-a` error: `optimist-a` claimed `0.002304 W`, which is off by a factor of $100$ (equivalent to dividing $2304$ by $10^6$ instead of $10^4$).
- Conclusion: Singleton finding `challenger/one` is a verified defect (`accepted`).

## Limitations
- Evaluation is strictly bounded by the nominal $10\text{ k}\Omega$ value and ideal voltage source/clamp conditions per `inputs/requirements.txt`. Component resistance tolerance, temperature coefficient, and ambient thermal derating are not modeled because `inputs/requirements.txt` forbids imposing unstated ratings or qualification requirements.

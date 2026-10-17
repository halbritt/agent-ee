# Circuit Review Experiments & Verification Notes

## Tools & Versions
- `python3`: Python 3.12.3
- `ngspice`: ngspice-42
- `kicad-cli`: 10.0.7

## Commands Executed
1. `python3 /work/experiments/verify_connectivity.py > /work/experiments/verify_connectivity.json`
2. `ngspice -b /work/experiments/rc_filter_sim.cir -o /work/experiments/rc_filter_sim.log`

## Assumptions
- Ideal linear passive components (`R1 = 10 kOhm`, `C1 = 100 nF`) at nominal values and 27 °C, as no operating voltage, frequency target, source/load impedance, or part tolerances/ratings are specified in `inputs/requirements.txt`.
- External stimulus applied at `J1.1` (`/IN`), output observed at `J1.2` (`/OUT`), reference ground at `J1.3` (`GND`).

## Outcomes
- **Schematic Connectivity (`inputs/project/rc_filter.kicad_sch`, `work/netlist/design.xml`, `work/netlist/connectivity.json`)**:
  - `/IN`: `J1.1` connected to `R1.1`
  - `/OUT`: `J1.2` connected to `R1.2` and `C1.1`
  - `GND`: `J1.3` connected to `C1.2` (with `PWR_FLAG` `#FLG01` on `GND`)
  - Matches stated external connector contract: J1 pin 1 is the input, pin 2 the filtered output, pin 3 ground.
- **Saved PCB Connectivity (`inputs/project/rc_filter.kicad_pcb`, `work/geometry/geometry.json`, `work/drc/drc.json`)**:
  - `J1.1` `(100.0, 100.0)` routed on `F.Cu` to `R1.1` `(104.0875, 100.0)` on net `/IN`.
  - `R1.2` `(105.9125, 100.0)` routed on `F.Cu` to `C1.1` `(109.05, 100.0)` and T-branched to `J1.2` `(100.0, 102.54)` on net `/OUT`.
  - `C1.2` `(110.95, 100.0)` routed on `F.Cu` to via `(113.0, 100.0)` and connected through the saved `B.Cu` `GND` copper pour (`38a83012-bcf9-4bb9-b9f4-50d49cd91d90`) to `J1.3` `(100.0, 105.08)`.
  - Native ERC and DRC (`--schematic-parity`) pass with 0 violations, 0 unconnected items, and 0 schematic parity errors.
- **Electrical Behavior (`review-work/circuit/experiments/rc_filter_sim.log`)**:
  - First-order RC low-pass filter with $\tau = R_1 C_1 = 1.0\text{ ms}$, $f_{-3\text{dB}} = 159.155\text{ Hz}$, phase at $f_c = -0.7854\text{ rad}$ ($-45^\circ$), step response reaching $0.6319\text{ V}$ at $1\tau$ ($1\text{ ms}$) and $0.9933\text{ V}$ at $5\tau$ ($5\text{ ms}$).

## Limitations
- `inputs/requirements.txt` specifies only the external connector pinout and saved connectivity contract ("External connector contract: J1 pin 1 is the input, pin 2 the filtered output, pin 3 ground. Verify saved connectivity. Only the stated exercise criterion is being evaluated; do not impose unstated ratings or qualification requirements.").
- Component ratings (voltage, power, dielectric, temperature), cutoff frequency target, source/load impedances, and transient/ESD/startup/brownout conditions are not specified and therefore not evaluated against numerical limits.

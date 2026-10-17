# Circuit review experiments — RC low-pass fixture (run 59e69da1)

## Environment
- ngspice-42 (KLU), python3, kicad files read-only at /candidate
- Candidate hashes verified against packet:
  - rc_filter.kicad_sch ecc56a68... (match)
  - rc_filter.kicad_pcb f8264d95... (match)

## Topology (from work/netlist/design.xml, independently re-read from schematic)
- /IN: J1.1, R1.1 ; /OUT: C1.1, J1.2, R1.2 ; GND: C1.2, J1.3
- Series R1=10k from IN to OUT, shunt C1=100n OUT->GND. Correct low-pass.
- PCB (work/geometry/geometry.json): /OUT continuous via tracks
  (105.9125,100)-(109.05,100), (100,102.54)-(107.5,102.54)-(107.5,100);
  junction (107.5,100) lies on the first segment. DRC: 0 unconnected.

## Simulation: rc_step.cir (ngspice -b)
- Vin: PWL 0->1 V in 1 ns at t=0, V(out) initial 0 (uic), .tran 1u 2m
- Results: vout_at_1ms = 0.6321204 V (in window 0.62..0.65), vout_at_2ms = 0.8646647 V
- Raw waveform: rc_step_data.txt

## Analytic sensitivity: window_sensitivity.txt
- tau = 1.0000 ms exactly; V(1ms) ideal = 0.632121 V
- Margin: -12.12 mV to 0.62 lower bound, +17.88 mV to 0.65 upper bound
- RC product must lie in [0.9525, 1.0335] ms => -4.75% .. +3.35% allowed deviation
- DC load at OUT: RL=320k gives 0.6239 V (fail); equivalent DC sink ~1.21 uA at OUT fails low bound
- Charge-share with discharged sampling cap connected at 1 ms: Cs~1.9-2 nF erodes full low margin; 10-100 pF negligible
- dV/dt at 1 ms = e^-1/tau = 368 V/s = 0.368 mV/us; time window for in-band value: 0.9676..1.0498 ms (-32 us/+50 us around 1 ms)

## Limitations
- No ADC model simulated (parameters unspecified per requirements; scope forbids inventing ADC behavior)
- No tolerance/temperature/C-DC-bias data stated for R1/C1; only nominal evaluated
- PCB parasitics not extracted; ~15 mm of 0.25 mm trace (~ tens of mOhm, fF-pF) is negligible vs 10k/100n

# Physical review notes — RC filter fixture (run 002c3f7a)

Date: 2026-10-16. Tools: KiCad/pcbnew 10.0.7-10.0.7~ubuntu24.04.1, ngspice (batch), python3.

## Inputs verified
- sha256 of /candidate/project/rc_filter.kicad_sch = ecc56a68... and rc_filter.kicad_pcb = f8264d95...
  match the packet artifact hashes (command: sha256sum).

## Exercise condition evaluated
/IN = 12 V DC, /OUT clamped to 0 V by external equipment (clamp off-board),
R1 = 10k with stated continuous limit 0.1 W.

## Connectivity (independent)
- board_connectivity.py (pcbnew): R1.1 = /IN, R1.2 = /OUT, C1.1 = /OUT, C1.2 = GND,
  J1.1/2/3 = /IN//OUT/GND; BuildConnectivity() unconnected count = 0 on the saved
  zone fill. Matches work/netlist/design.xml.

## R1 stress
- r1_power_calc.py: I = 12/10k = 1.2 mA; P = V^2/R = 14.4 mW; 6.94x below 0.1 W. PASS.
- clamp_op.cir (ngspice .op): v(in)=12, v(out)=0, i=1.2 mA, p_r1 = 14.4 mW. Confirms.
- C1 sees 0 V DC (OUT clamped to GND potential); no stress in this condition.
- Return current flows through the external clamp, not the board GND net; GND
  track/via/zone carry ~0 A in this scenario.

## Trace heating sanity check
- trace_heating.py: 0.25 mm tracks, assumed 1 oz (35 um) copper (stackup copper
  weight not stated in the board file); J = 0.137 A/mm^2; IPC-2221 external-trace
  estimated rise at 1.2 mA ~3e-6 degC. Negligible regardless of copper weight.

## Limitations / assumptions
- Copper thickness assumed 1 oz for the heating sanity check only; conclusion is
  insensitive to this at 1.2 mA.
- R1 tolerance not specified; even at -10% (9k), P = 16 mW, still ~6x margin.
- No physical measurement possible; evaluation is analytical + saved-file geometry.
- Zone fill evaluated as saved (read-only candidate; no refill pass).

## Conclusion
No defect under the stated exercise condition. ERC/DRC/parity re-confirmed clean by
packet evidence (work/erc/erc.json, work/drc/drc.json) and my own pcbnew connectivity run.

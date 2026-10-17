# Physical review experiments — rc_filter (run 59e69da1)

Tools: KiCad/pcbnew 10.0.7-ubuntu24.04.1, ngspice-42, python3.
Candidate read-only at /candidate; nothing modified.

## 1. connectivity_probe.py / connectivity_probe.out.json
Loaded the saved board (no zone refill) with pcbnew. Results:
- Pad->net map matches schematic netlist (J1.1=/IN, J1.2=/OUT, J1.3=GND,
  R1.1=/IN, R1.2=/OUT, C1.1=/OUT, C1.2=GND).
- B.Cu GND zone is saved filled, single filled polygon; fill contains the
  via center (113,100).
- J1 pad 3 annulus (r=0.86 mm ring around (100,105.08)) is covered at
  12/24 sampled ring points = 4 thermal spokes; pad is connected to the fill.
- KiCad BuildConnectivity on the loaded board: 0 unconnected items.
Conclusion: GND return path C1.2 -> F.Cu track -> single 0.6/0.3 via ->
B.Cu zone -> thermal spokes -> J1.3 is continuous as saved. It is the only
GND continuity between C1 and the connector (no F.Cu path).

## 2. rc_step.cir / rc_step.log / rc_step.dat
ngspice transient, netlist-derived R1=10k IN->OUT, C1=100n OUT->GND,
ideal 1 V step (1 ns rise), /OUT unloaded (ADC unspecified per packet).
- V(out) at 1 ms = 0.63212 V -> inside 0.62..0.65 band (nominal).
- V(out) at 2 ms = 0.86466 V (sanity: matches 1-exp(-2)).

## 3. tolerance_and_loading.py / tolerance_and_loading.out
- Band 0.62..0.65 V at t=1 ms requires tau in [0.9525, 1.0335] ms,
  i.e. combined R*C deviation within -4.75%..+3.35% of nominal.
  Symmetric +/-3.4% combined RC is the first failure; +/-5% and +/-10% fail.
  No tolerance is specified on R1 ("10k") or C1 ("100n") in the schematic.
- ADC charge-sharing bound (no ADC model invented): a zero-charged sampling
  cap must exceed ~1.95 nF to pull 0.632 V below 0.620 V at the sampling
  instant; a continuous resistive load below ~16.3 kOhm drops even the
  final value below 0.62 V.

## Limitations
- Copper thickness/stackup not specified in the board; trace parasitics
  estimated qualitatively only (series copper ~tens of mOhm vs 10 kOhm
  source impedance; negligible to the criterion). No ampacity/thermal
  qualification inferred or needed (no current requirement stated).
- Zone evaluated exactly as saved (read-only candidate; no refill pass).
- ADC sampling capacitance, switch resistance, initial charge and timing
  are unspecified by the packet; only bounding analysis performed.

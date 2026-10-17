# Physical review notes — RC low-pass shutdown fixture
Run: f2b2dfbb-1d19-4ab7-b561-b489158fc5a4; candidate bd8ff0ea...5cd0
Date: 2026-10-16. Tools: ngspice 42 (apt), KiCad/pcbnew 10.0.7-10.0.7~ubuntu24.04.1, python3.

## Inputs verified
input_hashes.txt: sha256 of /candidate/project/rc_filter.kicad_sch, .kicad_pcb and
requirements.txt match the packet artifact hashes exactly.

## Electrical criterion (requirements: /OUT <= 0.01 V at 5 ms, C1 pre-charged 1 V, /IN grounded, no load)
- Topology from work/netlist/design.xml: R1 10k between /IN and /OUT; C1 100n /OUT-GND;
  J1.1=/IN, J1.2=/OUT, J1.3=GND. With /IN grounded, discharge tau = 10k*100n = 1.000 ms.
- analytical_calc.txt: V(5 ms) = 1*exp(-5) = 6.7379 mV <= 10 mV -> PASS.
  Time to reach 10 mV = 4.6052 ms; margin to the 5 ms deadline = 0.395 ms (7.9% of window).
- shutdown_discharge.cir / shutdown_discharge_output.txt: ngspice .tran 1u 6m uic,
  meas vout5ms = 6.737944e-03 V. Matches analytical.

## Board connectivity (saved fill, no refill of the candidate)
- board_connectivity_probe.py/.json: pad nets match schematic; single B.Cu GND zone,
  is_filled=true, one outline, 192.29 mm2; via (113,100) center and surroundings inside fill;
  native connectivity GetUnconnectedCount=0 on the saved board.
- spoke_probe.py/_output.txt: around J1 pad 3 (100,105.08) the saved fill contains points at
  r=0.7 mm (inside the pad annulus, hole r=0.5, pad r=0.85) near the four 45-degree diagonals,
  and bridges the clearance gap at r=1.1 mm at 45/135/225/315 deg -> four thermal spokes
  physically connect J1.3 to the GND plane. Pad 2 (/OUT) ring at r=1.1 is entirely outside
  the fill -> correctly isolated.
- GND return path for the discharge: C1 pad2 -> 0.25 mm F.Cu track -> via (0.6/0.3) ->
  B.Cu GND plane -> thermal spokes -> J1 pad 3. Peak discharge current 1 V/10k = 100 uA;
  no ampacity or thermal qualification inferred or needed for the stated exercise.
- refill_compare.py/_output.txt: scratch copy refilled with ZONE_FILLER; area (192.287427 mm2)
  and vertex count (187) identical to saved fill -> saved fill not stale. Candidate untouched.

## F.Cu routing
/IN: J1.1 (100,100) -> R1.1 (104.0875,100). /OUT: J1.2 (100,102.54) -> (107.5,102.54) ->
(107.5,100) T-ing into R1.2 (105.9125,100) -> C1.1 (109.05,100) segment; collinear touch at
(107.5,100) confirmed connected by native connectivity (0 unconnected). All pads/tracks inside
the 20x12 mm Edge.Cuts outline; J1 courtyard max y=106.85 < 108.

## Limitations
- Spoke/fill continuity shown by point-in-polygon sampling of the saved single-outline polygon
  plus native connectivity count; no gerber-level extraction performed.
- No tolerance/parasitic (ESR, leakage) analysis: requirements mandate ideal passives.
- Margin note: criterion needs t >= 4.605*tau; design provides 5*tau. Any combined +8.6%
  error in R*C would breach 10 mV, but tolerances are out of scope per requirements.

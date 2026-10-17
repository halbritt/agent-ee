# Physical review notes — run 1ec07f8d-42d8-4c61-aed6-11957642f2bd

## Inputs verified
- sha256sum of /candidate/project files matches packet (`hashes.txt`):
  - rc_filter.kicad_sch a058e910... (matches packet)
  - rc_filter.kicad_pcb b2c9655c... (matches packet and both check records)

## Population state (native files)
- Schematic: Device:R symbol R1 has `(dnp yes)` (inputs/project/rc_filter.kicad_sch:1261);
  all other symbols `(dnp no)`.
- Board: R1 footprint has `(attr smd dnp)` (inputs/project/rc_filter.kicad_pcb:304);
  C1 `(attr smd)` and J1 `(attr through_hole)` are fitted.
- Consistent with work/netlist/connectivity.json (R1 dnp=true) and
  work/geometry/geometry.json (R1 footprint dnp=true).

## Independent fitted-assembly DC connectivity check
- Tool: pcbnew Python 10.0.7-10.0.7~ubuntu24.04.1 (stderr emits three benign
  PROPERTY_ENUM asserts on import — same noise visible in work/geometry/console.log;
  tool noise, not a design signal).
- Script: fitted_connectivity.py. Union-find over board conductors:
  pads, 5 track segments, 1 via, B.Cu GND zone. Track-track contact includes
  mid-segment T-junctions (track a6dab0b4 ends at (107.5,100) on the span of
  track 69a2edc1 — endpoint-only matching would miss it; v1 of the script did,
  v2 fixed). Component bodies modeled at DC: fitted R* bridge their pads;
  fitted capacitors are DC-open; DNP footprints conduct nothing (per the
  exercise criterion).
- Result (fitted_connectivity.json): fitted_path_IN_to_OUT = false.
  Three DC islands, each net-pure:
  1. /OUT: C1.1, R1.pad2, J1.2, tracks 69a2edc1/8b528360/a6dab0b4
  2. GND: C1.2, J1.3, track 7deb1fb0, via b71a70d7, B.Cu zone
  3. /IN: J1.1, R1.pad1, track ae12bf6f
- The only element spanning /IN to /OUT is the R1 body. Copper gap between
  R1 pad1 inner edge (x=104.6) and pad2 inner edge (x=105.4): 0.8 mm.
  With R1 unpopulated there is no fitted resistive DC path /IN -> /OUT:
  open circuit at the connector between J1 pin 1 and J1 pin 2.
- Counterfactual sanity check: bridging R1's pads joins islands 1 and 3, so
  the copper routing itself is complete; only the population state fails.

## Why the packet checks still pass
- ERC has no rule about DNP breaking a net (0 violations, work/erc/erc.json).
- DRC connectivity treats the DNP footprint's pads as netlist members, so
  0 unconnected items and 0 parity issues (work/drc/drc.json). KiCad parity
  also sees dnp set identically in sch and pcb, so no mismatch.
- None of the four packet checks (erc/drc/netlist/geometry) evaluates the
  stated fitted-path criterion; netlist/geometry outputs expose dnp flags as
  info only. Tool exit 0 is not evidence of electrical behavior here.

## Not done / limitations
- No ngspice run: the failure is topological (open circuit), simulation adds
  nothing; building a SPICE netlist would require inventing stimuli.
- No ampacity/thermal assessment: no current or thermal requirement stated;
  did not infer ratings from the 0.25 mm track width.
- Zone fill accepted as saved (refill forbidden on read-only candidate);
  saved filled_polygon is consistent with GND-only connectivity and passed DRC.

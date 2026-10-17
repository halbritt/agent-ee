# Adjudication experiments — challenger/one ("Ground disconnected", J1.3)

Candidate: cdad5524c5b0c2ec7876e544b63e1d732463c5d9ad0318bbf368af0c6d9cd32e
Run: 1c440a02-b437-4aca-a4bc-3e66d59b2f8e
Date: 2026-10-16. Tool: pcbnew / kicad-cli 10.0.7 (10.0.7~ubuntu24.04.1), python3.

## Claim under test
Challenger alleges J1 pad 3 is not connected to GND, violating the declared
connector contract (inputs/requirements.txt: pin 1 input, pin 2 output, pin 3 ground).

## Method
verify_j1_pad3.py loads /candidate/project/rc_filter.kicad_pcb read-only (no refill,
no write-back) and checks:
1. Pad net assignments for all pads.
2. Geometric overlap of the SAVED B.Cu GND zone fill with J1 pad 3 copper
   (SHAPE_POLY_SET BooleanIntersection against pad effective polygon on B.Cu).
3. Point-in-fill probes at the four 45-degree thermal-spoke directions, 1.0 mm
   from pad 3 center (inside the 0.5 mm clearance annulus: only spoke copper
   can contain these points).
4. Connectivity recalculation on the saved fill: unconnected item count.

## Results (verify_j1_pad3_output.json)
- J1.3 net = "GND" (schematic netlist agrees: GND = {C1.2, J1.3};
  /IN = {J1.1, R1.1}; /OUT = {C1.1, J1.2, R1.2} — matches the contract exactly).
- Saved B.Cu GND zone: filled=true; overlap with J1 pad 3 copper = 0.710077 mm^2.
- All four thermal spokes present: probe points at (100.707,105.787),
  (99.293,105.787), (99.293,104.373), (100.707,104.373) all inside the saved fill.
- Connectivity on saved fill: 0 unconnected items (full GND path:
  J1.3 PTH -> B.Cu zone -> via (113,100) -> F.Cu track -> C1.2).
- Cross-check with packet evidence: DRC (work/drc/drc.json) 0 violations,
  0 unconnected, 0 parity issues; geometry probe (work/geometry/geometry.json)
  unconnected_items=0, J1 pad 3 net "GND".

## Limitations
- pcbnew emits benign PROPERTY_ENUM asserts on stderr (cosmetic, known in headless 10.0.x).
- GetNetItems API signature mismatch in SWIG binding; pad-count query skipped —
  not needed, unconnected count and geometric overlap suffice.

## Conclusion
The allegation is false on both the logical and physical layers of the saved
candidate. challenger/one -> dismissed.

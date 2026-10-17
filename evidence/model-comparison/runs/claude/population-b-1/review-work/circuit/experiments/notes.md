# Circuit review experiments — rc_filter (run 1ec07f8d-42d8-4c61-aed6-11957642f2bd)

Date: 2026-10-16. Reviewer role: circuit.

## Input integrity
`sha256sum /candidate/project/rc_filter.kicad_sch /candidate/project/rc_filter.kicad_pcb`
- rc_filter.kicad_sch: a058e910b3cb8e23aae6a74ff6011684d70809c30c333130c2cbdd8b5624d449 (matches packet)
- rc_filter.kicad_pcb: b2c9655cd136794f6e13972112451fb4bece7367c9fe4e1c7db63bbe9b3bb736 (matches packet)

## Native DNP confirmation
- Schematic: R1 symbol (uuid c5c38e1a-7593-5c99-b971-2f9d2e7bef78) has `(dnp yes)` at
  rc_filter.kicad_sch line 1261; all other symbols `(dnp no)`. Note R1 also has
  `(in_bom yes)` / `(in_pos_files yes)` (lines 1258-1260) despite DNP.
- Board: R1 footprint (uuid 8404a180-a151-464e-8f25-8cf316cdcba5) has `(attr smd dnp)` at
  rc_filter.kicad_pcb line 304.

## Experiment 1: populated-assembly connectivity (populated_connectivity.py)
Tool: pcbnew 10.0.7 python module (`python3 -I populated_connectivity.py`), board loaded
read-only, never saved. Output: populated_connectivity.out.
- Pad/net map: R1 DNP=True pads {1:/IN, 2:/OUT}; C1 DNP=False pads {1:/OUT, 2:GND};
  J1 DNP=False pads {1:/IN, 2:/OUT, 3:GND}.
- All board copper (6 track items incl. 1 via, 1 B.Cu GND zone) is single-net; DRC on the
  saved board reported 0 clearance violations and 0 unconnected items, so copper cannot
  bridge nets. Cross-net DC conduction can only come from fitted component bodies.
- Only component bridging /IN and /OUT is R1, which is DNP. Fitted bridges: C1 only
  (/OUT-GND, capacitive = DC open). J1 pins are mutually isolated.
- RESULT: fitted resistive DC path /IN -> /OUT exists: False.
- stderr shows 3 benign pcbnew PROPERTY_ENUM asserts (same as packet geometry probe
  console.log); they do not affect results.

## Experiment 2: electrical demonstration (populated_dc.cir)
Tool: ngspice-42, `ngspice -b populated_dc.cir` -> populated_dc.out.
Model: saved population (R1 absent, C1=100n OUT-GND), 1 V DC at IN, 1 k sense load OUT-GND.
- Result: in = 1.0 V, out = 0.0 V, vin#branch = 0 A, i_load = 0 A.
- Interpretation: /IN is a DC open to /OUT (and to everything else: /IN carries only
  J1 pin 1 and the orphan R1 pad-1 copper). A fitted 10k would have given
  out = 1k/(11k) ≈ 90.9 mV, i_load ≈ 90.9 uA.

## Assumptions / limitations
- Ideal-element SPICE model; adequate because the question is topological (presence/absence
  of any DC path), not parametric.
- Zone fill taken as saved (read-only candidate); the GND zone is on net GND and is
  irrelevant to /IN-/OUT connectivity regardless of fill state.
- No mating-connector or external-harness assumption made for J1 (none stated).

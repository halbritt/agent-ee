# Adjudication experiments — Universal 112 donor (run 8534a2a6, candidate 46e5a5c3…)

Role: adjudicator. Date: 2026-10-16. Tools: python3 (stdlib, -I), curl, sha256sum, grep against the
read-only candidate at /candidate and native evidence at /evidence/work. Prior reviewer experiments
consulted read-only at /experiments/circuit and /experiments/physical.

## 1. Native check counts re-verified from full evidence files
- /evidence/work/erc/erc.json: 353 total violations; errors = {power_pin_not_driven: 3,
  pin_not_connected: 1, pin_to_pin: 1} — matches packet check/erc summary exactly.
- /evidence/work/drc/drc.json: 142 violations (errors = {padstack: 5, annular_width: 5,
  starved_thermal: 3}), 114 schematic-parity issues, 0 unconnected items — matches check/drc.
  Starved thermals confirmed on Pad 2 of C15, Pad 4 of U7, Pad 2 of C14 (GND zone, F.Cu).

## 2. CAN2 termination (circ-01 vs PHY-03) — independent re-verification
- Base board: R36/R37 = 120 Ω both (dnp yes) with "n.p." schematic annotation (packet artifact
  inputs/project/wbo-can-trans.kicad_sch; connectivity.json dnp_refs include R36, R37). Confirmed:
  no other resistive element on /CAN2+ //CAN2-.
- Brain-module side: re-fetched https://raw.githubusercontent.com/FOME-Tech/Polygonus/master/psu.kicad_sch
  (sha256 8bc90db0f4ccb2d9040b8e5cea1a6ea074a2ca7417d4081fea7d6803e5b04c52) — byte-identical to the
  circuit reviewer's saved copy at /experiments/circuit/polygonus/psu.kicad_sch.
- Parsed that file with python3 -I: R10 is Device:R_Small, Value "120", (dnp no)(in_bom yes), at
  (207.645,125.095). Independent wire-graph BFS from R10 pin endpoints (207.645,121.285)/(207.645,128.905)
  reaches hierarchical labels CANH2 (232.41,114.935) and CANL2 (232.41,135.255) respectively; R9 likewise
  bridges CANH/CANL (CAN1). So a Polygonus module carries one populated 120 Ω terminator on CAN2.
- Conclusion: in the documented primary configuration the internal wideband CAN2 bus is single-terminated
  on the module (τ ≈ 10 ns per circuit reviewer's RC estimate — adequate for the short internal bus).
  The major-severity mechanism in PHY-03 (advertised internal wideband link fails for lack of any
  termination) is resolved by this evidence. Residual: Atlas module (claimed compatible, repo 404)
  and builder population choices remain unverified → configuration-dependence concern only (circ-01).

## 3. J1 mechanical padstacks (circ-03 / PHY-01)
Parsed footprint "Simon:Molex_112_643330100" (J1) from /candidate/project/Polygonus-Universal-Base.kicad_pcb:
117 thru_hole pads; exactly 5 unnumbered pads with pad size == drill: (4,4)/drill 4 at rel (-61.25,-7.85),
(-13.2,-14.85), (70.05,-7.85); (2.2,2.2)/drill 2.2 at rel (-57.65,-4), (66.45,-4). With J1 at (133.2,43.34)
these are the absolute positions in the DRC errors (71.95,35.49), (120.0,28.49), (203.25,35.49) etc.
Defect confirmed: PTH pegs with zero annular ring (should be NPTH).

## 4. Sheet alias (circ-05)
sha256("/candidate/project/VR Conditioner.kicad_sch") == sha256(".../VR conditioner.kicad_sch") ==
377a2691ec878301977100851c40e302b2b7864d88cc27c6c305c03bbf14e15b. Root schematic line 21553 references
"VR Conditioner.kicad_sch". Matches the packet's declared limitation (disposable byte-copy alias).

## 5. Library tables (PHY-06)
/candidate/project/fp-lib-table registers only wideband_module, header_placement, fome_modules — no
"Simon" footprint entry although /candidate/project/fome-kicad-libs/Simon.pretty exists. sym-lib-table
registers Simon and fome_modules symbols only; `find /candidate -iname '*hellen*'` returns nothing, so the
hellen-one-common symbol library used by R1–R4, C1, J4, P1–P6 is absent from the project. Consistent with
the native lib_symbol_issues / footprint_link_issues / lib_footprint_issues warnings and the J2 parity
footprint substitution (Simon:Polygonus-With-USB-SWD on board vs fome_modules:polygonus on symbol).
Defect (minor) confirmed from the candidate's own files.

## 6. Copper-marginality concerns (PHY-04 / PHY-05)
Spot-checked the physical reviewer's arithmetic: 0.6 mm × 35 µm × 112 mm → R ≈ 1.7e-8·0.112/2.1e-8 ≈ 91 mΩ
(reviewer: 92 mΩ); IPC-2221 external 0.6 mm/1 oz at 10 °C → ≈1.66 A (reviewer: 1.65 A); ΔT at 2.5 A ≈ 25–26 °C.
Numbers reproduce. These are chart-fit estimates against assumed currents, with no declared current/thermal
criterion in the packet → supported concerns, not established defects.

## 7. MAX9924 polarity (circ-06)
Wiring facts confirmed from work/netlist/connectivity.json in the packet: /CRANK + → R25 → R27 → U10.2 (IN−),
/CRANK − → R26 → R28 → U10.1 (IN+). No declared requirement fixes the intended edge polarity; firmware edge
selection can absorb a static inversion. Observation supported; defect not established.

## Limitations
- Polygonus evidence is upstream master, not necessarily the exact revision paired with this donor.
- Atlas module design unreachable (HTTP 404); its CAN2 termination remains unverified.
- No SPICE simulation; CAN settling figures are the circuit reviewer's analytic RC estimates (methodology
  reviewed and found sound; not re-simulated).
- Zone fills and board evaluated as saved per packet constraints (no refill).

# Physical review notes — Universal 112 donor (candidate 46e5a5c3…, run 8534a2a6)

Tools: kicad-cli / pcbnew 10.0.7 (per work/*/execution.json), python3 3.x with pcbnew bindings.
Board: /candidate/project/Polygonus-Universal-Base.kicad_pcb (sha256 4e059d57… per packet), 4 copper layers
(F.Cu / In.Power.Cu / In2.Gnd.Cu / B.Cu), stackup copper thickness 0.035 mm per layer (read from board stackup).
All board measurements extracted read-only via pcbnew API; no candidate files modified.

## 1. DRC error extraction (work/drc/drc.json, severity=error, 13 items)
- starved_thermal ×3 (F.Cu GND zone, min spoke 2, actual 1): C15 pad2 (107.85,98.76), U7 pad4 (101.01,102.68),
  C14 pad2 (101.59,104.29).
- padstack "PTH pad hole leaves no copper" ×5 + annular_width 0.0mm ×5: J1 no-net pads at
  (71.95,35.49) (75.55,39.34) (120.0,28.49) (199.65,39.34) (203.25,35.49).
  pcbnew confirms: PTH attribute, pad size == drill (4.0/4.0 mm ×3, 2.2/2.2 mm ×2) → zero annular ring,
  i.e. mechanical peg holes of Simon:Molex_112_643330100 modelled as plated with no copper land.

## 2. Sheet alias check
sha256("VR Conditioner.kicad_sch") == sha256("VR conditioner.kicad_sch") = 377a2691ec87…; both 61944 bytes.
Byte-copy alias confirmed; both files coexist in /candidate/project.

## 3. CAN2 (internal wideband bus) physical topology
Nodes (pad abs positions, mm): U21.6/7 (78.0,140) + R37 (80.2–83.1,140.6) + D11 + C33/C34 (left end);
J2.73/74 (142.4–144.9,131.0) mid; U20.6/7 (177.8,140–141) + R36 (175.0,139.2–142.1) + D10 + C29/C30 (right end);
J1.11/15 external pins at (94.2–96.7,33.3) → ~107 mm stub off the main y≈131–140 run.
Routed 0.2/0.25 mm, ~250 mm total per line. Termination resistors R36 and R37 are both 120R and both DNP
(connectivity.json: dnp=true; wbo-can-trans.kicad_sch symbol d69380f9… has `(dnp yes)` and in_bom no in both
instance paths). Line loading: 2×47 pF per line (C29/C33 on CAN2+, C30/C34 on CAN2-) + NUP2105L ×2.
As saved, zero ohmic termination is populated anywhere on the net.

## 4. Copper ampacity estimates (IPC-2221 external, I = 0.048·ΔT^0.44·A[mil²]^0.725, 35 µm Cu)
ASSUMPTIONS: 1 oz outer copper per stackup; IPC-2221 chart fit (conservative vs IPC-2152 for isolated traces,
optimistic near other heat sources); still-air. Estimates only — not a thermal qualification.
- 0.6 mm track: 1.65 A @10°C, 2.24 A @20°C, 2.68 A @30°C rise. ΔT(2.5 A)≈26°C, ΔT(3.0 A)≈39°C.
- 0.8 mm track: 2.03 A @10°C; ΔT(6 A)≈117°C (extrapolated), ΔT(2 A)≈10°C.
- 1.0 mm track: 2.39 A @10°C.
- 0.4 mm drill via, 25 µm plating: barrel ≈0.033 mm² ≈ 2.3 A @10°C each.

### Heater returns (measured via pcbnew, section above tool log)
/HeaterNeg: J1.108 (189.5,30.8) → U1.11/12 (195–198,116.3); single 0.6 mm B.Cu path ≈112 mm, no parallel copper,
no vias; pad-to-pad bridge U1.11↔U1.12 is 0.25 mm × 2.5 mm (parallel-pin tie only, not series).
R≈92 mΩ → at 2.5 A: 0.23 V, 0.57 W dissipated in trace.
/HeaterNeg2: J1.76 (130.7,30.8) → U2.11/12 (66.7–69.2,109.6); single 0.6 mm B.Cu path ≈158 mm.
R≈129 mΩ → at 2.5 A: 0.32 V, 0.81 W.
Context: Bosch LSU heater warm-up current is of order 2–2.5 A (PWM-limited by the wideband module), steady-state
lower. ΔT estimate 20–40°C over local ambient during warm-up.

### ETB H-bridge supply (/12V_MR_FUSE)
Net is distributed by a large In.Power.Cu zone; net has only 3 vias total (0.4 mm drill, all at (109–111.2,52.5)).
- U15.4 (TLE9201SG #1 supply, SMD): sole path = 0.8 mm F.Cu × 4.56 mm → C26.2 → 1.5 mm F.Cu ≈8.7 mm →
  J1.73/74 PTH barrels → plane. Full bridge current crosses the 0.8 mm segment.
- U3.4 (TLE9201SG #2 supply, SMD): 0.8 mm × 1.6 mm neck → 1.5 mm track → the 3×0.4 mm via group → plane
  (≈3×2.3 A @10°C combined).
TLE9201SG is a 6 A-class H-bridge with internal current limit above 6 A (per Infineon datasheet family data;
not re-fetched — treated as assumption). Nominal throttle currents 1–2 A are fine (ΔT≈10°C on 0.8 mm);
stall/current-limit events are marginal on the U15 path.

### ETB outputs
/ETB1_+ 0.8–1.0 mm ×≈11 mm; /ETB1_- 1.0 mm ×20 mm; /ETB2_- 1.0 mm ×37.5 mm → ~2.4 A @10°C class.

## 5. Zones / fills
GND zone spans F.Cu+In2.Gnd.Cu+B.Cu, filled. 12V_MR zones on F/B/In.Power around F1 and J1.105/106.
8× /IGBTn zones on B.Cu (coil current pours). Two unfilled no-net entries are rule areas (keepouts), not
forgotten fills. Saved-fill DRC: 0 unconnected items; 3 starved thermals as above.

## 6. Library resolution
Project fp-lib-table registers only wideband_module, header_placement, fome_modules. The board/schematic reference
footprint lib "Simon" (J1, J4, P1–P6, J2 variant) and symbol lib "hellen-one-common". fome-kicad-libs/Simon.pretty
EXISTS in-repo but is NOT registered; no hellen-one-common library file found anywhere in the project. Native
checks: 8 footprint_link_issues, 14 lib_symbol_issues, lib_footprint_issues ×9. Design relies on embedded caches;
"Simon"/"hellen-one-common" may exist in the author's global tables (cannot verify) — but a clean checkout cannot
re-resolve them. Also parity warning: J2 on PCB uses Simon:Polygonus-With-USB-SWD while the symbol specifies
fome_modules:polygonus (net mapping consistent; attribute/BOM-exclusion mismatch also flagged).

## 7. ERC errors (work/erc/erc.json, 5 errors)
- power_pin_not_driven: #PWR01 GND pin (root), A1.8 VMOT (on /12V_MR_FUSE), J2.90 12V_IGN (on /12V_KEY).
  Rails are harness-fed via J1; no PWR_FLAG placed.
- pin_not_connected: A1.2 ~FLT output (no no-connect marker; PCB nets it as unconnected-(A1-~FLT-Pad2)).
- pin_to_pin: J2.40/41 both "Power output" on 5V_SENSOR_1 — symbol pin-type modelling (two outputs of the same
  module paralleled intentionally).

## Limitations of this work
- No zone refill / no board modification (read-only candidate); fills evaluated as saved.
- IPC-2221 fit used as estimate; no IPC-2152 chart lookup, no FEA, no measured thermal data.
- Connector terminal (Molex 64333-0100) and fuse-clip current ratings not verified against manufacturer data.
- TLE9201SG and LSU heater current figures from reviewer knowledge, not re-downloaded datasheets.
- No EMC/transient/ignition-energy analysis (declared out of scope by packet).

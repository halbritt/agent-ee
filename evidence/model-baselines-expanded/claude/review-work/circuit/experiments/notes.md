# Circuit review experiments — Universal 112 donor (run 8534a2a6)

Reviewer role: circuit. Date: 2026-10-16. Tools: kicad-cli 10.0.7, python3 (stdlib, -I), curl, WebFetch/WebSearch.

## 1. Reproduction of native checks
- ERC rerun (`rerun/erc_rerun2.json`): `kicad-cli sch erc --format json --severity-all --units mm` on
  /candidate/project/Polygonus-Universal-Base.kicad_sch with KICAD10_* env pointed at /candidate/stock.
  Result: 5 errors (identical set to packet: 3× power_pin_not_driven, 1× pin_not_connected, 1× pin_to_pin),
  229 endpoint_off_grid, 2 multiple_net_names, 5 isolated_pin_label, 1 same_local_global_label — all match
  work/erc/erc.json. Library-availability warnings (lib_symbol_issues / footprint_link_issues /
  lib_symbol_mismatch) differ in count (603 warnings total vs packet 348) because my ad-hoc
  KICAD_CONFIG_HOME lacks the harness's generated global library tables; all electrically meaningful
  categories reproduce exactly.
- DRC rerun (`rerun/drc_rerun.json`): 13 errors (5 padstack + 5 annular_width + 3 starved_thermal),
  0 unconnected, 114 parity issues — matches work/drc/drc.json exactly for errors/unconnected/parity.

## 2. ERC error triage (all 5 errors)
- power_pin_not_driven ×3: #PWR01 GND; A1 pin 8 VMOT on /12V_MR_FUSE; J2 pin 90 12V_IGN on /12V_KEY.
  Supplies enter via passive connector pins (J1.105/106 → F1 → 12V_MR_FUSE; J1.45 → 12V_KEY); no PWR_FLAG
  on those nets. Battery/key power genuinely present through the harness; ERC modeling only.
- pin_not_connected: A1 pin 2 ~FLT (DRV8825 carrier open-drain fault output) left unused — safe.
- pin_to_pin: J2 pins 40+41 both modeled "Power output" named 5V_SENSOR_1 and strapped together —
  paralleled pins of the same module regulator; benign modeling artifact.
- same_local_global_label and local "+5V" labels (root sheet lines 12563/13475/14687/14707/15087/15127 of
  Polygonus-Universal-Base.kicad_sch): connectivity.json shows a single merged +5V net containing all
  expected consumers (R1.1, R2.2, U1.1, U2.1/U2.4, J2.81, U20.3, U21.3 …) → no floating sub-net.

## 3. DRC error triage
- padstack/annular ×5: J1 footprint "Simon:Molex_112_643330100" contains five unnumbered thru-hole pads
  with pad size == drill (4/4 mm and 2.2/2.2 mm) at e.g. (71.95,35.49), (120.0,28.49) — connector mounting
  peg holes modeled as PTH with zero annular ring instead of NPTH. No net; mechanical only.
- starved_thermal ×3: GND pads C15.2 (107.85,98.76), U7.4 (101.01,102.68), C14.2 (101.59,104.29) get 1 of
  2 required zone spokes on F.Cu. Still connected (0 unconnected items); solderability/robustness only.

## 4. CAN2 internal wideband bus termination (scoped question)
Saved state (verified in Polygonus-Universal-Base.kicad_pcb and wbo-can-trans.kicad_sch):
- R36 (175.01,140.67) and R37 (81.62,140.55), both value 120, both `(dnp yes)`; schematic carries an
  explicit "n.p." annotation next to the terminator cell. Pads land on /CAN2+ and /CAN2-.
- Bus members (connectivity.json): U20.7/6 and U21.7/6 (TJA1051T/3 transceivers for wideband modules U1,
  U2), J2.73/74 (Polygonus brain module CAN2), J1.15/11 (harness), D10/D11 NUP2105L ESD, C29/C30/C33/C34
  47 pF line-to-GND (two per line because the sheet is instantiated twice → ≈94 pF/line + ESD + traces).
- Brain-module side: fetched FOME-Tech/Polygonus (master). psu.kicad_sch contains U4/U5 MCP2551-I-SN and
  R9/R10 = 120 Ω, not DNP. Wire trace confirms R10 (207.645,125.095, R_Small pins ±2.54) bridges
  CANH2 (via 211.455,114.935 → hier label CANH2) to CANL2 (via 211.455,135.255 → CANL2). So with a
  Polygonus installed, internal CAN2 is single-terminated with 120 Ω.
- RC estimates: with the module's single 120 Ω: τ_diff ≈ 120 Ω × ~80 pF (two 94 pF line-GND caps in
  series + transceiver/trace) ≈ 10 ns — fine at 1 Mbit/s.
  With NO termination anywhere (brain module without on-module terminator, nothing populated on base,
  no external bus): recessive-state differential resistance is only the paralleled transceiver input
  bias resistances, TJA1051 R_i(cm) = 15–37 kΩ/pin → R_diff ≈ 2×(15–37k)/3 ≈ 10–25 kΩ; τ ≈ 0.7–1.6 µs
  against a 2 µs bit at 500 kbit/s (1 µs at 1 Mbit/s) → dominant→recessive edges do not settle in time;
  communication marginal-to-failing.
- Atlas compatibility claim (README): https://github.com/mck1117/atlas returns HTTP 404 — Atlas CAN2
  termination could not be verified.

## 5. Interface/pin-identity spot checks (all pass)
- TJA1051T-3 symbol pin map 1 TXD / 2 GND / 3 VCC / 4 RXD / 5 VIO / 6 CANL / 7 CANH / 8 S matches NXP
  device; S tied to GND (normal mode); VCC=+5V, VIO=+3.3V (J2.82 module rail) matching the 3.3 V logic of
  the f042 wideband modules; 100 nF decoupling on both rails per transceiver.
- Wideband module symbol: pin 5 CAN_RX (input) ← U20.4/U21.4 RXD (output); pin 6 CAN_TX (output) →
  U20.1/U21.1 TXD (input). Correct pairing, both channels. SEL straps differ between U1 (SEL_1=GND,
  SEL_2 float) and U2 (SEL_1 float, SEL_2=+5V) → distinct module IDs.
- TLE9201SG: symbol pin 2 "VSO" tied to +3.3V. Infineon datasheet (via DigiKey HTML datasheet) confirms
  pin 2 = VSO, supply for the SO output, "tied to 5V or 3.3V depending on logic level" — correct. DIS
  pulled up 10 k to +3.3V (R35/R5) → bridge disabled until MCU drives: safe default. OUT1/OUT2 → J1
  ETB pins with 10 nF snubbing caps; VS on fused 12 V with 10 µF.
- DRV8825 carrier A1: VMOT=/12V_MR_FUSE + C2 56 µF bulk; nRESET/nSLEEP (pins 13/14) = +5V; mode pins +
  nENABLE (pins 9–12) = GND (full step, enabled); STEP/DIR = /LS9,/LS10 with 1 k pullups (R1/R2) and 1 k
  pulldowns (RN1). Polygonus mcu.kicad_sch shows LS9–LS12 are hierarchical outputs of the MCU sheet
  (3.3 V push-pull GPIO), so drive levels are 0/3.3 V (> DRV8825 VIH 2.2 V), not the 2.5 V divider that
  open-drain outputs would produce. VNL5090 adder inputs via RN2/RN4 series 1 k: OK.
- VR module (vr_module_poly): 74HC02 U5 wired as cross-coupled NOR SR latch (pin4→pin3, pin1→pin5) fed
  by comparator U7 thresh_low/thresh_high paths; unused gate inputs grounded. Output net drives J2.95,
  LED D1+R6, J5. Consistent window-comparator trigger.
- VR Conditioner (MAX9924 U10): BIAS stiffened with populated 1 k/1 k divider to VCC/2 + 1 µF (matches
  internal VCC/2 bias, no conflict); COUT (pin 7) → /VR1+ → J2.96 with 4.7 k pullup R34. Observed:
  /CRANK + (J1.59) → R25 → R27 → IN− (U10.2) and /CRANK − → R26 → R28 → IN+ (U10.1), i.e. connector
  polarity is swapped relative to the MAX9924 typical application; output polarity inverts, normally
  absorbed by firmware edge selection. No stated requirement to judge against — recorded as observation.

## 6. Sheet alias verification
sha256("VR Conditioner.kicad_sch") == sha256("VR conditioner.kicad_sch") ==
377a2691ec878301977100851c40e302b2b7864d88cc27c6c305c03bbf14e15b (byte-identical). Root sheet
(Polygonus-Universal-Base.kicad_sch:21553) references "VR Conditioner.kicad_sch"; netlist sheet 4
resolves to it and all VR-conditioner components (U10, JP1, C23–C25, R25–R34) appear in the netlist.
Residual hazard: two byte-identical files differing only in case will collide on case-insensitive
filesystems and can silently diverge when edited.

## 7. Limitations of these experiments
- Atlas brain module design unavailable (404) — CAN2 termination with Atlas unverified.
- No SPICE simulation performed (no models for TJA1051/MAX9924); CAN analysis is analytic RC estimate.
- Connector current ratings, fuse sizing, thermal and transient qualification not assessed (also
  excluded by packet scope).
- Downloaded Polygonus sources are upstream master branch, not necessarily the exact revision the donor
  targets.

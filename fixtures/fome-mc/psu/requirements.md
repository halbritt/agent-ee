# fome-mc PSU block: rung-2 review requirements (AGENTEE-106)

## Subject

The subject is the fome-mc power supply block: `sheets/psu.kicad_sch` at fome-mc
commit `df304f3321296f7e517e0d2e2aa52e1380d52993`, without the CAN transceivers
(U4, U5, D4, D5, R7, R8, R9, R10). The block was captured through the offline Git
path. Its 31 parts are listed in `parts.json`, and `interface.json` is the derived
boundary contract. The candidate places the captured files at their block paths
(`sheets/`, `libs/`, `sources/`), so every requirement source below is a captured
fome-mc file.

## Standalone board

`fome-mc-psu.kicad_sch` is an original root sheet. It includes the captured sheet
unchanged as the `psu` instance and reuses the fome-mc root and `/psu/` sheet UUIDs,
so the captured symbols keep their native references.

- J1 brings in `/12V_RAW` and GND. On the full board, `/12V_RAW` comes from
  `Keep_Alive.kicad_sch`.
- J2 exposes `/12V_PROT`, `/VBOOST`, `/5V_SENSOR_1`, `/5V_SENSOR_2` and GND.
- J3 exposes both CAN buses.
- The `mcu` stub (`mcu-stub.kicad_sch`, J4) holds the MCU side of the power-good and
  CAN logic nets. Because the stub is named `mcu`, those nets keep their native names
  (`/mcu/5V_SENSOR_1_PG`).

ERC uses the rule severities that the pinned fome-mc project declares. The saved
board is empty: no layout is captured, and no layout claim is made.

## Requirements

Requirement IDs match the block entry (`block.json`).

| ID | Claim | Source | Check |
| --- | --- | --- | --- |
| `psu-native` | The sheet loads natively, passes ERC and exports a netlist that contains every reference-set part | `sources/docs/BLOCK_LIBRARY.md`, What "verified block" means | `erc`, `netlist` |
| `psu-interface` | Every boundary terminal stays on its named native net, and every part keeps its populated state and value | `sources/docs/BLOCK_LIBRARY.md`, Block catalogue / Power supply | `interface` |
| `psu-tvs-clamp` | The D1 SMCJ24A clamp is below every downstream absolute maximum (FOMEMC-7) | `sources/docs/DESIGN.md` 6.3 item 1 | `tvs-clamp` |
| `psu-reverse-block` | The Q1/D19/R124 reverse-battery block works without a series diode drop | `sources/docs/DESIGN.md` 6.3 item 1 | `reverse-block` |
| `psu-c12-part` | C12 is GCM188R71C106KA64D, LCSC C3092605 (FOMEMC-15) | `sources/docs/DESIGN.md` 6.3 item 2 | `c12-part` |
| `psu-c4-rating` | C4 is the 50 V X7R 1210 C417122, rated above the clamp (FOMEMC-17) | `sources/docs/DESIGN.md` 6.3 item 2 | `c4-rating` |
| `psu-brownout-floor` | The guaranteed regulation floor is about 4.80 V: the LMR16020 4.30 V minimum VIN plus the series drop (FOMEMC-17) | `sources/docs/DESIGN.md` 6.3 item 3 | `brownout-floor` |
| `psu-rails` | +5V is within 2 %, the sensor rails within 1 % and +3V3 within 1 % | `sources/docs/BLOCK_LIBRARY.md`, Power supply requirements | `rail-regulation` |
| `psu-capacitor-ratings` | Every capacitor is rated above its rail clamp | `sources/docs/DESIGN.md` 6.3 item 2 | `capacitor-ratings` |
| `psu-input-filter` | `/12V_PROT` reaches `/VBOOST` through the L1/C6 LC filter | `sources/docs/DESIGN.md` 6.3 item 2 | `input-filter` |

## Unknown coverage

No agent-ee check can yet establish these claims. Each one is bound to a declared
check that reports unknown coverage. That check result is never a pass.

| Check | Kind | Why it is unknown | Needed |
| --- | --- | --- | --- |
| `tvs-clamp` | `datasheet_limits` (not registered) | No SMCJ24A, TLE9201SG, VNLD5090 or DRV8825 datasheet is captured | AGENTEE-18, AGENTEE-61 |
| `reverse-block`, `brownout-floor`, `rail-regulation` | `active_model` (not registered) | No active model exists for the AO4485, LMR16020, TLS115D0E or TLV757P | AGENTEE-19 |
| `c12-part` | `catalog_part_identity` (not registered) | No catalog snapshot holds C3092605 or the captured C12 part | AGENTEE-27, AGENTEE-137 |
| `c4-rating`, `capacitor-ratings` | `catalog_rating` (not registered) | No catalog voltage ratings are captured | AGENTEE-27, AGENTEE-29, AGENTEE-61 |
| `input-filter` | `rc_transient` | It accepts passive-only networks, and the PSU sheet has active parts | AGENTEE-19 for a sourced and loaded filter claim |

Reviewers judge the captured design against these sources directly. `parts.json`
records each part's captured LCSC number.

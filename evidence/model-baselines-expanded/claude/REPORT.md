# Universal 112 disposable hierarchy alias review review

Outcome: **defects_or_unresolved**; process state: `complete`.

Candidate: `46e5a5c34e7b9715155c9e90277cd3639b3a19ebb9590053a081bbb6436ba860`
Run: `8534a2a6-e237-4d1c-a002-90ce37ec09e4`

Review the saved Universal 112 donor with an explicit disposable sheet-path alias, native rule coverage, saved-board implementation, and internal dual-wideband CAN termination. Original as-received snapshot is reported separately. No source circuit redesign or manufacturing approval.

## Checks

| Check | Execution | Design result | Summary |
| --- | --- | --- | --- |
| erc | ok | defect | ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5} |
| drc | ok | defect | DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s) |
| netlist | ok | info | native XML netlist exported from a complete 10-instance hierarchy: 139 component(s), 199 net(s), 10 DNP |
| geometry | ok | info | saved-board geometry extracted (no declared geometric requirement applied) |

## Initial reviews

- circuit: ok; requested model claude-fable-5.
- physical: ok; requested model claude-fable-5.

circuit coverage: Reproduced native ERC and DRC with kicad-cli 10.0.7 against the read-only candidate; all 5 ERC errors, all 13 DRC errors, 0 unconnected items and 114 parity issues match the packet (library-availability warning counts differ only with environment).; Triaged every error-severity ERC violation (3x power_pin_not_driven, pin_not_connected on A1 ~FLT, pin_to_pin on J2 pins 40/41) against the netlist and harness power entry paths.; Triaged every error-severity DRC violation (5x padstack + 5x annular_width on J1 mechanical peg holes, 3x starved_thermal on GND pads C14/C15/U7).; Scoped CAN2 termination review: confirmed R36/R37 (120R) both DNP in schematic and saved board with 'n.p.' annotation; enumerated all CAN2 bus members; fetched FOME-Tech/Polygonus sources and wire-traced the module's populated 120R (R10) across CANH2/CANL2; computed recessive-settling RC for terminated (~10 ns) and hypothetical unterminated (~0.7-1.6 us vs 2 us bit) cases.; Verified TJA1051T/3 pin identity (TXD/GND/VCC/RXD/VIO/CANL/CANH/S), S=GND normal mode, VCC=5V, VIO=3.3V matching the 3.3V-logic f042 wideband modules, and correct CAN_RX/CAN_TX pairing to both wideband modules; verified distinct SEL ID straps on U1 vs U2.; Verified TLE9201SG pin 2 = VSO tied to +3.3V against the Infineon datasheet; DIS default-disable pull-ups; VS/outputs wiring.; Verified DRV8825 carrier strapping (nRESET/nSLEEP high, mode/nENABLE low, VMOT bulk cap) and confirmed from Polygonus mcu.kicad_sch that LS9-12 are 3.3V push-pull MCU outputs, so STEP/DIR and VNL5090 adder drive levels exceed input thresholds (no 2.5V divider marginality).; Verified VR module 74HC02 cross-coupled NOR latch topology, grounded unused gates, and MAX9924 VR-conditioner bias/output network; checked local '+5V' label merge into the single global +5V net in connectivity.json.; Verified the disposable sheet alias: 'VR Conditioner.kicad_sch' is byte-identical (sha256 377a2691...) to 'VR conditioner.kicad_sch', root references the alias, and the netlist resolves the full 10-sheet hierarchy including all VR Conditioner components.
- Atlas brain-module compatibility (claimed in inputs/project/README.md) could not be assessed: github.com/mck1117/atlas returns 404, so CAN2 termination with an Atlas module installed is unverified.
- Polygonus module evidence is upstream master-branch source, not necessarily the exact revision matching this donor.
- No SPICE simulation was run (no vendor models for TJA1051/MAX9924/TLE9201 at hand); CAN timing is an analytic RC estimate recorded in review-work/circuit/experiments/notes.md.
- Power budget (fuse F1 sizing, wideband heater and ETB currents through J1 pins and F1), connector pin current ratings, ignition energy, EMC, thermal and automotive transient behavior were not evaluated (also outside packet scope).
- Zone fill evaluated as saved; the read-only candidate forbids a refill pass.
- MAX9924 CRANK+/CRANK- polarity swap relative to the typical application circuit was observed but not adjudicated: no stated requirement fixes the intended edge polarity, and firmware edge selection can absorb it.

physical coverage: Re-extracted all 13 error-severity items from the full native DRC report (work/drc/drc.json) and localized them on the saved board via the pcbnew API (padstacks, annular width, starved thermals).; Inspected J1 mechanical padstacks directly in the saved .kicad_pcb (pad attribute, size, drill).; Verified the disposable sheet alias: byte-identical sha256 for 'VR Conditioner.kicad_sch' and 'VR conditioner.kicad_sch'.; Mapped the complete CAN2 physical topology (all pads/nodes, termination resistor placement and DNP state, line-loading capacitors, external-pin stub length).; Measured copper geometry of high-current paths: wideband heater returns (/HeaterNeg, /HeaterNeg2), fused 12V distribution (/12V_MR_FUSE zone, via count, ETB supply necks), ETB motor outputs, unfused /12V_MR feed; computed IPC-2221 ampacity estimates and trace resistances with stated assumptions (review-work/physical/experiments/physical-review-notes.md).; Enumerated all copper zones and confirmed the two unfilled no-net entries are rule areas, not forgotten fills; confirmed GND plane coverage (F.Cu/In2.Gnd.Cu/B.Cu) and 0 unconnected items at saved fill state.; Checked project library resolution: fp-lib-table/sym-lib-table contents versus libraries actually referenced and library files present in the repo; correlated with the native lib_*_issues warnings and the J2 footprint-substitution parity warning.; Reviewed all 5 native ERC errors from work/erc/erc.json and attributed root causes.; Cross-checked netlist connectivity (work/netlist/connectivity.json) against board pad nets for the nets examined.
- Zone fills evaluated as saved; the read-only candidate forbids a refill pass, so fill-dependent results (starved thermals, connectivity) reflect the saved state only.
- Ampacity figures are IPC-2221 chart-fit estimates at assumed 35 µm copper and still air; no IPC-2152 lookup, FEA, or measured thermal data — they bound marginality but do not qualify or disqualify the design thermally.
- Molex 64333-0100 terminal and Keystone 3568 fuse-clip current ratings were not verified against manufacturer data; connector-pin ampacity is not assessed.
- TLE9201SG current-limit and LSU heater current figures are from reviewer knowledge, not freshly retrieved datasheets, and are stated as assumptions in the experiments notes.
- Internal construction of the Polygonus brain module and wideband modules (including any on-module CAN termination) is outside the donor and could not be verified.
- No EMC, automotive transient, ignition-energy, or wideband control-stability analysis (declared out of scope by the packet).
- Whether 'Simon' / 'hellen-one-common' libraries resolve via the original author's global library tables cannot be determined from the candidate.

## Findings and dispositions

### check/erc: ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

Disposition: **accepted**.

Location: erc. Condition: check erc (erc).

ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Executed ERC check fails its criterion: 353 violations including 5 errors (3x power_pin_not_driven, 1x pin_not_connected, 1x pin_to_pin). Counts re-verified from the full evidence file and independently reproduced by the circuit reviewer's rerun. Failing check criterion must remain accepted; root-cause triage of the 5 errors is carried by circuit/circ-02.
- [work/erc/execution.json](<work/erc/execution.json>): check:erc
- [work/erc/console.log](<work/erc/console.log>): check:erc
- [work/erc/erc.json](<work/erc/erc.json>): check:erc

### check/drc: DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition: **accepted**.

Location: drc. Condition: check drc (drc).

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Executed DRC check fails its criterion: 142 violations including 13 errors (5 padstack, 5 annular_width, 3 starved_thermal), 114 schematic-parity issues, 0 unconnected items. Re-verified from the full evidence file and reproduced by both reviewers. The error-level items are detailed in accepted findings physical/PHY-01 and physical/PHY-02.
- [work/drc/execution.json](<work/drc/execution.json>): check:drc
- [work/drc/console.log](<work/drc/console.log>): check:drc
- [work/drc/drc.json](<work/drc/drc.json>): check:drc

### circuit/circ-01-can2-termination-dnp: Internal wideband CAN2 bus has no termination populated on the base board; function depends entirely on the plug-in brain module

Disposition: **deferred**.

Location: project/wbo-can-trans.kicad_sch (R36/R37, 'n.p.' text at 113.03,76.2); board R36 at (175.01,140.67), R37 at (81.62,140.55). Condition: Both 120R terminators R36 and R37 are DNP ((dnp yes), in_bom no) in both instances of the WBO-CAN-TRANS sheet, and no other termination exists anywhere on the base board's /CAN2+ //CAN2- nets, which carry three transceiver nodes (U20, U21, brain-module CAN2 via J2.73/74), ~94 pF per line of filter capacitance (duplicated 47 pF C29/C33 and C30/C34), ESD clamps D10/D11, and harness pins J1.15/J1.11..

A CAN bus with zero resistive termination relies on transceiver input bias (TJA1051 R_i(cm) 15-37 kOhm/pin; ~10-25 kOhm differential for three nodes) to return to recessive; with ~80 pF effective differential capacitance the settling constant is ~0.7-1.6 us against a 2 us bit at 500 kbit/s, so dominant-to-recessive edges do not settle and the advertised internal dual-wideband link fails or is marginal. I verified the primary configuration is saved from this: the Polygonus brain module itself carries one populated 120R (R10 wired across CANH2/CANL2 next to MCP2551 U5 in Polygonus psu.kicad_sch), giving a single-terminated short bus (tau ~10 ns, functional). However the README also claims Atlas module compatibility, and the Atlas design is unreachable (404), so an installed module without on-module termination would leave the internal bus completely unterminated with no base-board default protecting against it.

The scoped question resolves as: termination exists but only on the removable brain module, verified by independent retrieval and wire-tracing of the Polygonus source, not on the reviewed board. Single 120R termination on a short internal bus is electrically adequate (calculation in experiments), so this is not a defect of the saved design in its documented primary configuration; it is a configuration-dependence concern because the base board as built provides zero termination and the second claimed-compatible module family could not be verified.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Root engineering reconciliation: saved R36/R37 are DNP. Retrieved upstream module R10 supports a possible termination configuration, but is not evidence of the actual mated module revision, harness or operating waveform. No definite CAN failure is established. Preserve assembled-bus termination and behavior as unresolved; do not infer functional operation from a resistor or an assumed RC calculation.
- [inputs/project/wbo-can-trans.kicad_sch](<inputs/project/wbo-can-trans.kicad_sch>): symbol R36 (dnp yes)/(in_bom no), value 120, pins on CAN2+/CAN2- wires at y=83.82/88.9; text "n.p." at (113.03,76.2)
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets /CAN2+ and /CAN2- listing U20.7/6, U21.7/6, J2.73/74, J1.15/11, D10/D11, C29/C30/C33/C34, R36/R37; dnp_refs include R36,R37
- [review-work/circuit/experiments/notes.md](<review-work/circuit/experiments/notes.md>): section 4: Polygonus R10 120R wire trace in psu.kicad_sch and RC settling calculations
- [inputs/project/README.md](<inputs/project/README.md>): 'Dual wideband O2 sensor controllers ... connected internally to CAN bus #2'; compatibility claim for Polygonus and Atlas

### circuit/circ-02-erc-errors: Schematic fails native ERC with 5 errors (power modeling and unused pins); no electrical malfunction identified

Disposition: **accepted**.

Location: project/Polygonus-Universal-Base.kicad_sch root sheet (A1, J2, #PWR01). Condition: ERC reports: Input Power pin not driven for #PWR01 GND, A1 pin 8 VMOT (/12V_MR_FUSE), and J2 pin 90 12V_IGN (/12V_KEY); pin_not_connected for A1 pin 2 ~FLT; pin_to_pin error for J2 pins 40/41 (two 'Power output' pins tied together). Reproduced exactly in an independent ERC run..

The supply nets are fed only through passive connector pins (J1.105/106 through fuse F1; J1.45) with no PWR_FLAG, so KiCad cannot prove the power-input pins are driven; A1's open-drain fault output is deliberately unused; J2 pins 40/41 are paralleled outputs of the same module regulator modeled as two power-output pins. None of these indicates a wiring fault - battery and key power genuinely arrive via the harness - but the design does not pass its own native ruleset as saved, and standing errors can mask future real regressions.

A native violation is a reportable defect even when benign; I verified each error's electrical context from the netlist and found power sourcing and intentional no-connects rather than faults, hence minor severity rather than functional.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Canonical detailed defect for the 5 ERC errors: 3x power_pin_not_driven (no PWR_FLAG on harness-fed /12V_MR_FUSE and /12V_KEY, plus a GND power symbol), 1x pin_not_connected (A1 ~FLT without NC marker), 1x pin_to_pin (J2 pins 40/41 both typed Power output on 5V_SENSOR_1). Verified against the full ERC report and netlist; root causes are schematic-modeling hygiene rather than copper faults, hence minor severity, but the saved design fails its native ruleset. physical/PHY-07 is the same finding from the other reviewer.
- [work/erc/erc.json](<work/erc/erc.json>): error entries: power_pin_not_driven at (36.83,78.74), (48.26,219.71), (293.37,173.99); pin_not_connected at (38.1,224.79); pin_to_pin at (262.89,128.27/130.81)
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): /12V_MR_FUSE containing F1.2, J1.73/74, A1.8; /12V_KEY = J1.45, J2.90; /5V_SENSOR_1 = J1.16/19/20, J2.40, J2.41
- [review-work/circuit/experiments/notes.md](<review-work/circuit/experiments/notes.md>): sections 1-2: ERC reproduction (identical 5 errors) and triage

### circuit/circ-03-j1-mechanical-pth-padstack: J1 connector mounting peg holes modeled as plated pads with zero annular ring (5 padstack + 5 annular-width DRC errors)

Disposition: **duplicate**.

Location: project/Polygonus-Universal-Base.kicad_pcb, footprint Simon:Molex_112_643330100 (J1 at 133.2,43.34). Condition: Five unnumbered thru-hole pads in the J1 footprint have pad size equal to drill (4.0/4.0 mm and 2.2/2.2 mm, e.g. at (71.95,35.49), (120.0,28.49), (203.25,35.49)), producing DRC errors 'PTH pad hole leaves no copper' and 'annular width 0.0000 mm' against the 0.1 mm board constraint..

Connector alignment/retention pegs defined as PTH with no copper ring should be NPTH; as saved they request plated holes with no annulus, which fabricators commonly flag or silently convert, and plating slivers in a ringless hole can flake. No net is attached, so there is no connectivity or electrical impact.

Verified directly in the saved board that pad diameter equals drill on unconnected mechanical holes; this is a footprint-modeling/fabrication-interface defect, not an electrical one, hence minor.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Same defect as physical/PHY-01: the five unnumbered J1 peg holes saved as PTH pads with pad size equal to drill (zero annular ring). Both reviewers verified the identical pads; I re-parsed the J1 footprint in the saved board and confirmed exactly 5 thru-hole pads with size==drill (4/4 x3, 2.2/2.2 x2) at the DRC-reported positions. Consolidated under PHY-01 (defect-to-defect duplication).
- [work/drc/drc.json](<work/drc/drc.json>): error-severity 'padstack' and 'annular_width' entries, items 'PTH pad [<no net>] of J1' at the five listed coordinates
- [inputs/project/wbo-can-trans.kicad_sch](<inputs/project/wbo-can-trans.kicad_sch>): (not applicable to this finding; see board file)
- [review-work/circuit/experiments/notes.md](<review-work/circuit/experiments/notes.md>): section 3: extraction of the five unnumbered pads with size==drill from the saved board file

### circuit/circ-04-starved-thermals: Three GND pads receive only one of two required thermal spokes from the GND zone

Disposition: **duplicate**.

Location: project/Polygonus-Universal-Base.kicad_pcb, F.Cu GND zone; pads C15.2 (107.85,98.76), U7.4 (101.01,102.68), C14.2 (101.59,104.29). Condition: DRC error 'Thermal relief connection to zone incomplete (zone min spoke count 2; actual 1)' on the GND pads of decoupling caps C15/C14 and comparator U7 in the VR-module area, in the saved (unrefilled) zone fill..

A single-spoke thermal connection remains electrically connected (0 unconnected items) but halves the pad's copper connection, raising connection impedance for a comparator ground/decoupling path and reducing solder-joint and rework robustness; a refill or local keepout fix would be needed, which the read-only review cannot apply.

Reproduced independently; connectivity is intact so impact is manufacturing robustness and marginally higher ground-connection impedance on small-signal parts, consistent with minor severity.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Same defect as physical/PHY-02: the three error-severity starved_thermal violations on GND pads C15.2, U7.4, C14.2 in the saved F.Cu GND fill. Identical pads, mechanism, and severity in both reviews; verified in the full DRC report. Consolidated under PHY-02 (defect-to-defect duplication).
- [work/drc/drc.json](<work/drc/drc.json>): three error-severity 'starved_thermal' entries listing Zone [GND] on F.Cu/B.Cu/In2.Gnd.Cu and pads C15.2, U7.4, C14.2
- [review-work/circuit/experiments/notes.md](<review-work/circuit/experiments/notes.md>): sections 1,3: independent DRC reproduction (same 3 starved_thermal errors) and triage

### circuit/circ-05-sheet-alias-duplicate: Hierarchy resolved via byte-identical case-variant duplicate sheet file; divergence and case-collision hazard remains

Disposition: **noted**.

Location: project/VR Conditioner.kicad_sch vs project/VR conditioner.kicad_sch; root reference at Polygonus-Universal-Base.kicad_sch line 21553. Condition: The candidate contains two sheet files whose names differ only by case, verified byte-identical (sha256 377a2691ec878301977100851c40e302b2b7864d88cc27c6c305c03bbf14e15b for both); the root schematic references 'VR Conditioner.kicad_sch' and the netlist resolves all VR-conditioner components through it..

The alias makes the 10-sheet hierarchy complete for this review (confirmed: U10/JP1/C23-C25/R25-R34 present in the netlist), but future edits through KiCad update only the referenced file, letting the lowercase copy silently go stale, and checkouts on case-insensitive filesystems (Windows/macOS defaults) will collide the two names. The packet itself declares this is a disposable alias and the original discrepancy remains open.

Verified the alias is a faithful byte copy and that connectivity is currently correct, so no electrical defect; recorded as info-level configuration-management concern consistent with the packet's declared limitation.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Supported info-level configuration-management concern. Verified myself: both 'VR Conditioner.kicad_sch' and 'VR conditioner.kicad_sch' hash to 377a2691ec87..., the root sheet (line 21553) references the capitalized name, and the netlist resolves the full 10-sheet hierarchy including all VR-conditioner components. No electrical defect in the saved state; the divergence/case-collision hazard matches the packet's own declared limitation that the alias is disposable and the original filename discrepancy remains open.
- [review-work/circuit/experiments/notes.md](<review-work/circuit/experiments/notes.md>): section 6: matching sha256 of both files and root Sheetfile reference at line 21553
- [work/netlist/design.xml](<work/netlist/design.xml>): sheet number 4 '/VR Conditioner/' with source 'VR Conditioner.kicad_sch'
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): components C23-C25, R25-R34, U10, JP1 with sheet '/VR Conditioner/'

### circuit/circ-06-max9924-input-polarity: VR1 (MAX9924) differential inputs wired with CRANK+ to IN- and CRANK- to IN+ (inverted vs. typical application)

Disposition: **noted**.

Location: project/VR Conditioner.kicad_sch (U10 MAX9924, R25/R27 and R26/R28 input chains). Condition: Netlist shows /CRANK + (J1.59) -> R25 4.7k -> R27 4.7k -> U10.2 (IN-) and /CRANK - (J1.55) -> R26 4.7k -> R28 4.7k -> U10.1 (IN+), the opposite of the MAX9924 typical application polarity..

Swapped differential polarity inverts the comparator output edge relative to the VR sensor's true tooth-edge zero crossing. ECU firmware edge selection can absorb a static inversion, and adaptive-threshold zero-cross detection is symmetric, so this does not necessarily impair function; but if firmware assumes the conventional polarity, trigger edges land on the wrong tooth flank, degrading timing accuracy on asymmetric VR waveforms.

The cross-wiring is evidenced directly in the exported netlist, but no stated requirement fixes the intended polarity and the donor is an established design whose firmware pairing is out of scope, so this is recorded as an info-level concern for human confirmation rather than a defect.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Supported info-level observation: the netlist confirms /CRANK + reaches U10.2 (IN-) via R25/R27 and /CRANK - reaches U10.1 (IN+) via R26/R28, inverted relative to the MAX9924 typical application. No declared requirement fixes the intended edge polarity, zero-cross detection is symmetric, and firmware edge selection can absorb a static inversion, so a defect is not established; preserved as a concern for human confirmation of the intended firmware pairing.
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets '/CRANK +': [J1.59, R25.2]; 'Net-(R25-Pad1)': [R25.1, R27.2]; 'Net-(U10-IN-)': [C23.1, R27.1, R29.2, U10.2]; '/CRANK -': [J1.55, R26.2]; 'Net-(U10-IN+)': [C23.2, R28.1, R29.1, U10.1]
- [review-work/circuit/experiments/notes.md](<review-work/circuit/experiments/notes.md>): section 5: MAX9924 wiring observation and rationale

### physical/PHY-01: J1 mechanical peg holes are plated through-holes with zero annular ring (5 native DRC errors)

Disposition: **accepted**.

Location: Polygonus-Universal-Base.kicad_pcb, footprint J1 (Simon:Molex_112_643330100), no-net pads at (71.95,35.49), (75.55,39.34), (120.0,28.49), (199.65,39.34), (203.25,35.49) mm. Condition: Five no-net pads of the 112-pin header are saved as PTH pads whose drill equals the pad size (4.0/4.0 mm ×3, 2.2/2.2 mm ×2), giving 0.000 mm annular width against the board's 0.100 mm minimum; native DRC reports 5 'padstack' and 5 'annular_width' errors..

A plated hole with no copper land either gets rejected/queried at fabrication or is plated anyway, reducing the finished hole diameter by roughly the plating thickness and leaving an unanchored barrel; for connector alignment pegs this risks peg interference fit changes and plating tear-out during connector press-in. The holes should be NPTH.

Confirmed directly in the saved board via pcbnew (PTH attribute, size==drill). This is a mechanical/manufacturing issue only (no net attached), so severity is minor, but it is a definite native-rule violation in the saved candidate.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Saved board and direct native DRC establish five PTH pads with pad diameter equal to drill, zero annulus against the saved 0.1 mm rule. Accept the saved-rule violation only. Fabrication rejection, plating damage and connector fit are not demonstrated; exclusions remain explicit.
- [work/drc/drc.json](<work/drc/drc.json>): violations[] type=padstack and type=annular_width, severity=error, items 'PTH pad [<no net>] of J1'
- [review-work/physical/experiments/physical-review-notes.md](<review-work/physical/experiments/physical-review-notes.md>): section 1 — pcbnew pad attribute/size/drill extraction
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): footprint J1, no-net thru_hole pads with size==drill

### physical/PHY-02: Saved F.Cu ground fill leaves three pads with starved thermal reliefs (1 spoke vs required 2)

Disposition: **accepted**.

Location: Polygonus-Universal-Base.kicad_pcb, GND zone on F.Cu; pads C15.2 (107.85,98.76), U7.4 (101.01,102.68), C14.2 (101.59,104.29) mm (VR module area). Condition: Native DRC reports 3 error-severity 'starved_thermal' violations: the saved F.Cu GND fill connects these pads with a single thermal spoke where the zone's own constraint requires a minimum of two..

A single-spoke thermal connection halves the intended copper cross-section at the joint: higher joint resistance/inductance on decoupling and comparator ground pins, and a mechanically/thermally weaker solder connection that is more vulnerable to spoke necking during fill changes or fabrication etch variation.

The pads remain electrically connected (DRC shows 0 unconnected items), so the impact is marginal rather than functional; but it violates the board's own fill constraints in the saved state, which the read-only review must take as-is. Minor defect.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Native DRC on saved copper establishes one spoke versus the configured minimum of two on C15.2, U7.4 and C14.2. Accept the saved-rule violation only; zero unconnected items does not qualify impedance, soldering or thermal performance.
- [work/drc/drc.json](<work/drc/drc.json>): violations[] type=starved_thermal, severity=error (3 items)
- [review-work/physical/experiments/physical-review-notes.md](<review-work/physical/experiments/physical-review-notes.md>): section 1 and section 5 (saved-fill state, 0 unconnected items)

### physical/PHY-03: Internal dual-wideband CAN2 bus has no populated termination: both 120 Ω terminators (R36, R37) are DNP

Disposition: **duplicate**.

Location: wbo-can-trans.kicad_sch (both instances /WBO-CAN-TRANS/ and /WBO-CAN-TRANS1/), R36/R37; board nets /CAN2+ and /CAN2-, R36 at (175.0,140.7), R37 at (81.6,140.6) mm. Condition: The CAN2 bus internally links the ECU module (J2.73/74 at x≈144), both TJA1051T-3 wideband transceivers (U21 at x≈78, U20 at x≈178), and external connector pins J1.11/15 via a ~107 mm stub. The only termination resistors on the net — 120 Ω R36 at the U20 end and 120 Ω R37 at the U21 end — are both marked DNP ((dnp yes), in_bom no) in the saved design, so as-built the bus has zero ohmic termination; line loading is 2×47 pF per line (C29/C33, C30/C34) plus two NUP2105L clamps..

An unterminated high-speed CAN bus has no defined recessive differential load: reflections and slow recessive transitions cause bit errors, and the README-advertised internal wideband function (wideband controllers 'connected internally to CAN bus #2') then depends entirely on termination existing inside the plug-in brain module or in the external harness, neither of which is part of this donor. Conversely, if a builder populates both R36 and R37 and also attaches an externally terminated CAN2 harness through J1.11/15, the bus load falls to ~40 Ω, below the transceiver's normal 50–65 Ω operating load.

Placement of the two terminators at the physical bus ends is correct and the DNP pattern may be a deliberate configuration option, so this is not provable as a defect from the donor alone. But the packet scope explicitly claims 'internal dual-wideband CAN termination', and the saved evidence shows no termination is actually populated: correct operation of an advertised core feature rests on an unverified assumption about the module or harness. Major concern requiring documentation or module-side verification.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Same native DNP carrier termination and unresolved actual module/harness configuration as the surviving deferred circuit finding. No majority vote or presumed functioning upstream module resolves this concern.
- [inputs/project/wbo-can-trans.kicad_sch](<inputs/project/wbo-can-trans.kicad_sch>): symbol uuid d69380f9-5487-4782-9ce9-0690df62e543 (R36/R37, Value 120, dnp yes, in_bom no)
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): components R36/R37 dnp:true; nets /CAN2+ and /CAN2- node lists
- [work/geometry/geometry.json](<work/geometry/geometry.json>): footprint R36 dnp:true, pads on /CAN2+ and /CAN2-
- [review-work/physical/experiments/physical-review-notes.md](<review-work/physical/experiments/physical-review-notes.md>): section 3 — CAN2 node map, stub length, line loading
- [inputs/project/README.md](<inputs/project/README.md>): 'Dual wideband O2 sensor controllers … connected internally to CAN bus #2'

### physical/PHY-04: Wideband heater return currents carried on single 0.6 mm tracks up to 158 mm long

Disposition: **deferred**.

Location: Polygonus-Universal-Base.kicad_pcb, nets /HeaterNeg (J1.108 → U1.11/12, ≈112 mm, B.Cu) and /HeaterNeg2 (J1.76 → U2.11/12, ≈158 mm, B.Cu). Condition: Each wideband sensor heater return is a single 0.6 mm wide, 35 µm thick B.Cu track with no parallel copper and no plane relief: measured R ≈ 92 mΩ (/HeaterNeg) and ≈ 129 mΩ (/HeaterNeg2). IPC-2221 external estimate for 0.6 mm/1 oz is ≈1.65 A at 10 °C rise; at the ~2.5 A LSU heater warm-up current the estimated rise is ≈26 °C (≈39 °C at 3 A) with 0.57–0.81 W dissipated along the trace and 0.23–0.32 V dropped in the heater low side..

During every cold start the heater PWM ramp drives ~2–2.5 A through these traces for tens of seconds to minutes; in an engine-bay ambient the added 25–40 °C self-heating pushes local trace temperature well above the surrounding board, stressing laminate and solder joints over life, and the series drop slightly reduces heater voltage authority available to the module's closed-loop heater control.

Width alone does not establish a failure — heater current is duty-limited and the estimates are chart fits, so this is a concern, not a defect. It is the thinnest sustained-current copper on the board relative to its expected load and merits either a width increase, paralleled layers, or a documented thermal justification.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Actual geometry supports a current-path investigation. Current, duty cycle, copper thickness, ambient and permissible rise are not sufficiently established for a thermal verdict. IPC-2221 estimates with assumed currents do not prove overheating or adequate transient protection. Preserve for load and thermal evidence; no redesign is authorized by this disposition.
- [review-work/physical/experiments/physical-review-notes.md](<review-work/physical/experiments/physical-review-notes.md>): section 4 'Heater returns' — pcbnew segment listing, resistance and IPC-2221 calculations with assumptions
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets /HeaterNeg (J1.108, U1.11, U1.12) and /HeaterNeg2 (J1.76, U2.11, U2.12)
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): 0.6 mm B.Cu segments on /HeaterNeg and /HeaterNeg2

### physical/PHY-05: ETB H-bridge supply pads reach the 12V_MR_FUSE plane through narrow necks and only three 0.4 mm vias net-wide

Disposition: **deferred**.

Location: Polygonus-Universal-Base.kicad_pcb, net /12V_MR_FUSE: U15 pad 4 (120.54,44.91) via 0.8 mm×4.6 mm F.Cu neck to J1.73/74 barrels; U3 pad 4 (103.68,45.1) via 0.8 mm×1.6 mm neck and the net's only 3 vias (0.4 mm drill) at (109–111.2,52.5) mm. Condition: The fused 12 V rail is distributed on the In.Power.Cu plane, but the entire net has just three 0.4 mm vias. The ETB1 driver (U15, TLE9201SG, 6 A-class with internal current limit above 6 A) is fed exclusively through a 0.8 mm wide, 4.6 mm long 1 oz F.Cu segment into the J1.73/74 pin barrels; the ETB2 driver (U3) is fed through the three-via group (≈2.3 A each at 10 °C rise estimate, ≈7 A combined)..

At nominal throttle currents (1–2 A) the 0.8 mm neck sits near 10 °C rise — acceptable. During motor stall or driver current-limit events (>6 A), IPC-2221 extrapolation puts the 0.8 mm segment above 100 °C rise if sustained; repeated limit/thermal-shutdown cycling of the TLE9201 can hold average current high while the trace, unlike the IC, has no protection. The three-via group for ETB2 is adequate only if current shares evenly and events stay brief.

Short necks tolerate brief overloads and throttle stall events are transient, so no defect is provable; but a 6 A-class bridge fed through a single 0.8 mm neck and a net served by three small vias is marginal by estimate and worth widening or paralleling. Reported as a calculated concern, not a thermal disqualification.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Actual geometry supports a current-path investigation. Current, duty cycle, copper thickness, ambient and permissible rise are not sufficiently established for a thermal verdict. IPC-2221 estimates with assumed currents do not prove overheating or adequate transient protection. Preserve for load and thermal evidence; no redesign is authorized by this disposition.
- [review-work/physical/experiments/physical-review-notes.md](<review-work/physical/experiments/physical-review-notes.md>): section 4 'ETB H-bridge supply' — full track/via/pad extraction and IPC-2221 calculations
- [work/geometry/geometry.json](<work/geometry/geometry.json>): U15 pad 4 /12V_MR_FUSE SMD at (120.54,44.91); U3 pad 4 at (103.68,45.1)
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): /12V_MR_FUSE F.Cu tracks (120.56,44.94)→(125.12,44.94) w=0.8; vias at (109.02/110.06/111.17, 52.5)

### physical/PHY-06: Project library tables do not resolve the libraries the design actually uses (Simon footprints unregistered, hellen-one-common absent)

Disposition: **accepted**.

Location: Polygonus-Universal-Base project: fp-lib-table (3 entries), sym-lib-table (2 entries) vs references Simon:Molex_112_643330100, Simon:PAD-TH, Simon:BMM-USB, Simon:Polygonus-With-USB-SWD (J2), hellen-one-common symbols (R1–R4, C1, J4, P1–P6). Condition: fp-lib-table registers only wideband_module, header_placement and fome_modules even though fome-kicad-libs/Simon.pretty exists in the repo; no hellen-one-common library file exists anywhere in the project. Native checks correspondingly report 8 footprint_link_issues and 14 lib_symbol_issues (ERC) plus 9 lib_footprint_issues (DRC), and schematic parity flags that board J2 uses Simon:Polygonus-With-USB-SWD while the symbol specifies fome_modules:polygonus..

The design builds only from the caches embedded in the schematic and board files; a clean checkout cannot re-resolve, diff, or update these footprints/symbols against sources, so library drift (such as the already-present J2 footprint substitution) cannot be audited, and any footprint refresh in KiCad would fail or silently diverge.

The native violations are reproduced from the project's own saved tables, and the unregistered in-repo Simon.pretty shows this is a project-configuration gap rather than purely a sandbox artifact (the possibility of an author-side global table is noted as a limitation). No electrical impact in the saved state since caches are intact — minor defect in manufacturing-data/reproducibility hygiene.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Established minor defect. Verified from the candidate's own files: fp-lib-table registers only wideband_module, header_placement and fome_modules while fome-kicad-libs/Simon.pretty exists unregistered in the project tree, and no hellen-one-common library file exists anywhere in the candidate (find returned nothing). This matches the native lib_symbol_issues/footprint_link_issues/lib_footprint_issues warnings, and the DRC parity report shows an actual consequence already present: board J2 carries Simon:Polygonus-With-USB-SWD while the symbol specifies fome_modules:polygonus. A clean checkout cannot re-resolve or audit these footprints/symbols. No electrical impact in the saved state (caches intact), hence minor.
- [inputs/project/fp-lib-table](<inputs/project/fp-lib-table>): full file — 3 libraries, no 'Simon' entry despite fome-kicad-libs/Simon.pretty in repo
- [inputs/project/sym-lib-table](<inputs/project/sym-lib-table>): full file — no 'hellen-one-common' entry; no such file found in project tree
- [work/erc/erc.json](<work/erc/erc.json>): lib_symbol_issues / footprint_link_issues entries citing 'Simon' and 'hellen-one-common'
- [work/drc/drc.json](<work/drc/drc.json>): schematic_parity: 'Simon:Polygonus-With-USB-SWD doesn't match footprint given by symbol (fome_modules:polygonus)' (J2); lib_footprint_issues for library 'Simon'

### physical/PHY-07: Five native ERC errors remain: undriven power-input pins, unmarked NC pin, and paralleled power-output pins

Disposition: **duplicate**.

Location: Polygonus-Universal-Base.kicad_sch root sheet: #PWR01 GND pin (36.8,78.7); A1 pin 8 VMOT (48.3,219.7); J2 pin 90 12V_IGN (293.4,174.0); A1 pin 2 ~FLT (38.1,224.8); J2 pins 40/41 5V_SENSOR_1 (262.9,128.3/130.8). Condition: ERC reports 5 errors: three power_pin_not_driven (a GND power symbol pin, A1 VMOT on /12V_MR_FUSE, J2.90 12V_IGN on /12V_KEY), one pin_not_connected (A1 ~FLT output with no no-connect marker), and one pin_to_pin (J2 pins 40 and 41 both typed Power output and tied together on 5V_SENSOR_1)..

These rails are genuinely fed from the vehicle harness through J1 (12V_KEY: J1.45↔J2.90; 12V_MR_FUSE carries F1 output), so the undriven-pin errors reflect missing PWR_FLAG markers rather than missing supplies; the pin_to_pin error reflects the J2 module symbol modelling its paralleled 5V output pins both as Power output; A1 ~FLT is an open fault output (netlist: unconnected-(A1-~FLT-Pad2)) lacking an NC flag. None indicates a physical copper fault, but unresolved errors mask any future real ERC regressions.

Evidence-supported native errors in the saved schematic; root-cause analysis shows schematic-modelling hygiene (missing PWR_FLAG/NC markers, symbol pin typing) rather than electrical faults, hence info severity. Left at defect because the native rule set is violated as saved and the errors should be resolved or waived explicitly.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Same defect as circuit/circ-02: the identical five native ERC errors with the same root-cause attribution (missing PWR_FLAG on harness-fed rails, unmarked NC on A1 ~FLT, paralleled Power-output pins J2.40/41). Both reviewers independently confirmed the same items; consolidated under the circuit finding as canonical (defect-to-defect duplication, severity carried as minor by the canonical).
- [work/erc/erc.json](<work/erc/erc.json>): severity=error entries: power_pin_not_driven ×3, pin_not_connected (A1 ~FLT), pin_to_pin (J2 40/41)
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets /12V_KEY (J1.45,J2.90), /5V_SENSOR_1 (J2.40,J2.41), unconnected-(A1-~FLT-Pad2)

## Limitations

- Donor carrier remains a modular design; integrated FOME-MC coverage is not established by this donor review.
- Scope omits full automotive transients, ignition energy, wideband control stability, EMC and thermal qualification.
- Saved copper is checked without refilling or changing the board.
- This disposable candidate adds VR Conditioner.kicad_sch as a byte copy of the existing lowercase VR conditioner.kicad_sch. The original checkout remains unchanged, and its filename discrepancy remains open there.
- text artifact partially supplied: work/geometry/geometry.json (100000/1657568 bytes)
- text artifact partially supplied: work/drc/drc.json (100000/142607 bytes)
- text artifact partially supplied: work/erc/erc.json (100000/226344 bytes)
- text artifact partially supplied: work/netlist/design.xml (84810/252017 bytes)
- text artifact partially supplied: work/geometry/execution.json (0/2154 bytes)
- text artifact partially supplied: work/geometry/console.log (0/446 bytes)

This result applies only to the captured candidate and declared criteria. Prototype and bench
validation, including operating conditions omitted from this review, remain necessary.

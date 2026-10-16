# Universal 112 disposable hierarchy alias review review

Outcome: **defects_or_unresolved**; process state: `complete`.

Candidate: `2767e5a30de2c3bcdee814eda2dbc63cc460f744b8029c74079b8feea3e0ae9b`
Run: `127a9e8e-5b06-4239-87ce-8ad95a4c01af`

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

circuit coverage: Native ERC results (work/erc/erc.json, 353 violations incl. 5 errors) reviewed against the exported connectivity to classify each error-severity item; Native DRC/schematic-parity results (work/drc/drc.json) reviewed; error-type counts (padstack, annular_width, starved_thermal) and parity warnings assessed; Dual-wideband CAN2 chain traced in native source inputs/project/wbo-can-trans.kicad_sch and work/netlist/connectivity.json: TJA1051T-3 pin identity (TXD1/GND2/VCC3/RXD4/VIO5/CANL6/CANH7/S8), CANH->/CAN2+ and CANL->/CAN2- polarity, TXD/RXD crossover to f042 wideband modules U1/U2, S-pin grounding, VCC=+5V and VIO=+3.3V supply assignment, NUP2105L ESD clamps and 47p filters, and 120R termination population state (R36/R37 DNP); Board placement of CAN2 parts cross-checked in work/geometry/geometry.json (U21, C29/C30/C33/C34, R36 pads and nets consistent with schematic); VR conditioner front end checked: MAX9924 input network (R25-R28 4.7k pairs, C23 1n, DNP R29/R32/R33), BIAS divider (R30/R31 1k with C24 1u), open-drain output pull-up R34 to +3.3V, JP1 mode strap bridged to +5V; ETB TLE9201SG safe-state check: DIS pins pulled up via R35/R5 10k to +3.3V (drivers disabled by default); VS on fused 12V rail; Power rail distribution sampled across +5V, +3.3V, 12V_MR/12V_MR_FUSE nets; wideband module SEL strap difference (U1 SEL_1=GND vs U2 SEL_2=+5V) noted as plausible address selection; Netlist/geometry consistency: 139 components, 199 nets, 10 DNP refs, 0 unconnected items in both netlist and geometry probes
- Several artifacts were truncated in the packet (drc.json, erc.json, geometry.json, design.xml supplied as prefixes), so error-severity DRC item locations and part of the ERC/netlist detail could not be inspected directly
- Custom-library parts (Simon:MAX9924, Simon:TLE9201SG, fome_modules:polygonus, f042_wideband_module) could not be verified against vendor datasheets from the packet; pin-identity checks rely on the symbol/netlist labels
- Zone fill evaluated as saved per check limitation; starved_thermal errors may be fill-state artifacts
- Polygonus module internals (CAN1 protection, 5V-tolerance of module pins, 3.3V/5V rail sourcing) are outside this donor packet and were not verifiable
- No electrical measurements, simulations, or operating-condition data were supplied; review is static/native-rule and connectivity based
- The as-received lowercase 'VR conditioner.kicad_sch' filename discrepancy is declared out of scope for this disposable alias candidate

physical coverage: Reviewed ERC results (353 violations, 5 errors) against the supplied native sheet wbo-can-trans.kicad_sch and the erc.json excerpt, classifying errors versus library/grid hygiene warnings.; Reviewed DRC results (142 violations, 13 errors, 114 parity issues) including error-type counts (padstack, annular_width, starved_thermal) and the schematic-parity entries in the drc.json excerpt.; Cross-checked exported connectivity (work/netlist/connectivity.json, 139 components / 199 nets) for the dual-wideband CAN2 bus: transceiver pin nets, VCC(+5V)/VIO(+3.3V) rails, NUP2105L protector nets, 47 pF filter caps, and DNP status of termination resistors R36/R37.; Checked saved-board placement of CAN2 components (U20/U21 transceiver positions, R36/R37 terminator pad nets and positions, C29/C30/C33/C34 filter caps) in the geometry.json excerpt.; Verified wideband module strapping (U1 SEL pin to GND, U2 SEL pin to +5V) and heater/LSU net fan-out between J1 and U1/U2 in connectivity.json.; Cross-checked README feature claims (dual CAN, internal CAN2 widebands, lowside adder, IGBT outputs, stepper) against netlist nets and components.; Reviewed the disposable VR Conditioner.kicad_sch sheet-path alias usage in the 10-instance hierarchy (netlist schematic_files, design.xml sheet sources).
- Several artifacts were truncated in the packet: drc.json (100000/142607 bytes, the 13 error-severity entries for padstack/annular_width/starved_thermal are not visible), erc.json (100000/226344), geometry.json (100000/1657568, tracks/vias/zones not included), design.xml (84913/252017); geometry execution.json and console.log were omitted entirely. Error locations for the DRC errors are therefore known only by count and type.
- No trace width, copper ampacity, thermal, or return-path evaluation was possible: the geometry excerpt contains only footprint/pad data, no track or zone geometry, and no current requirements were declared. Per instructions, no ampacity or thermal qualification is inferred.
- Zone fill was evaluated as saved; the read-only candidate forbids a refill, so the 3 starved_thermal errors reflect the saved fill state and could change after a refill.
- The verification environment lacks the project libraries Simon, hellen-one-common, fome_modules and header_placement, so ~200 lib_symbol/lib_footprint mismatch or issue warnings could not be resolved against library sources; cached symbols/footprints were accepted as-is.
- External harness behavior (CAN bus length, external termination, ignition transients, EMC) is out of scope per the declared limitations and was not assessed.
- The Polygonus brain module (J2) internals are not part of this donor packet, so protection/termination possibly located on the module (e.g., for CAN1) could not be verified.

## Findings and dispositions

### check/erc: ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

Disposition: **accepted**.

Location: erc. Condition: check erc (erc).

ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

Disposition reason: Executable ERC check ran to completion (exit 0) and reports 353 violations including 5 error-severity items confirmed in the report body: power_pin_not_driven on #PWR01 GND (36.83,78.74), A1 pin 8 VMOT (48.26,219.71), J2 pin 90 12V_IGN (293.37,173.99); pin_not_connected on A1 pin 2 ~FLT (38.1,224.79); pin_to_pin on J2 pins 40/41 (both Power output, 5V_SENSOR_1). A failing native check criterion with tool-confirmed error entries is an established defect and cannot be dismissed; canonical record for the ERC error set.
- [work/erc/execution.json](<work/erc/execution.json>): check:erc
- [work/erc/console.log](<work/erc/console.log>): check:erc
- [work/erc/erc.json](<work/erc/erc.json>): check:erc

### check/drc: DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition: **accepted**.

Location: drc. Condition: check drc (drc).

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition reason: Executable DRC check ran to completion (exit 0) and reports 142 violations with by_severity error=13, decomposed by the tool as padstack:5, annular_width:5, starved_thermal:3, plus 114 schematic parity issues. Error-severity DRC results are native evidence of a failing check criterion (fabrication-relevant annular/padstack geometry independent of zone-fill state), so the defect stands; canonical record for the board-level DRC error set. Individual error coordinates lie beyond the 100000-byte excerpt of drc.json but the tool summary and console confirm the counts.
- [work/drc/execution.json](<work/drc/execution.json>): check:drc
- [work/drc/console.log](<work/drc/console.log>): check:drc
- [work/drc/drc.json](<work/drc/drc.json>): check:drc

### circuit/f1-erc-errors: ERC reports 5 native error-severity violations (undriven power inputs, unconnected ~FLT, paralleled power outputs)

Disposition: **duplicate**.

Location: project/Polygonus-Universal-Base.kicad_sch (root sheet). Condition: Native ERC found 5 errors: power_pin_not_driven on hidden GND pin of #PWR01 (36.83,78.74), on A1 pin 8 VMOT (48.26,219.71), and on J2 pin 90 12V_IGN (293.37,173.99); pin_not_connected on A1 pin 2 ~{FLT} (38.1,224.79); pin_to_pin between J2 pins 40 and 41, both 'Power output' type on 5V_SENSOR_1..

The GND, 12V_MR_FUSE and 12V_KEY rails are sourced only through passive connector/fuse pins with no PWR_FLAG or power-output pin, so KiCad cannot confirm the rails are driven; the DRV8825 fault output is left floating with no no-connect marker; J2 defines two paralleled pins as power outputs which conflict when tied. Electrically the rails are externally sourced via J1 and the ~FLT output is open-drain, so these are predominantly formal violations, but they mask any future genuinely-undriven rail.

Confirmed native rule violations in the candidate schematic. Connectivity shows the flagged rails are fed from harness connector pins through F1, so no actual floating supply exists under stated use; the ~FLT output is a status output whose omission loses stepper-fault visibility but does not break bias or startup. Classified defect (tool-confirmed) at minor severity; remediable with PWR_FLAGs, a no-connect marker, and pin-type cleanup on the J2 module symbol. The 348 warnings (229 endpoint_off_grid, lib mismatches, 1 same_local_global_label, 2 multiple_net_names) are hygiene items consistent with the imported hellen-one/Simon libraries.

Disposition reason: Restates and triages the same 5 error-severity ERC violations already established by the accepted check/erc finding (same uuids, locations, and types). The circuit-level mechanism analysis (harness-fed rails lacking PWR_FLAG, open-drain ~FLT, paralleled module power-output pins) is sound and verified against connectivity.json, but the underlying defect is identical; defect-to-defect duplication preserves it under the accepted canonical check.
- [work/erc/erc.json](<work/erc/erc.json>): sheets[path='/'].violations: types power_pin_not_driven (#PWR01, A1 pin 8 VMOT, J2 pin 90 12V_IGN), pin_not_connected (A1 pin 2 ~{FLT}), pin_to_pin (J2 pins 40/41)
- [work/erc/console.log](<work/erc/console.log>): line 1: 'Found 353 violations'
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets '/12V_KEY' [J1.45, J2.90], '/12V_MR_FUSE' (sourced only via F1.2 from passive J1 pins), 'unconnected-(A1-~{FLT}-Pad2)'

### circuit/f2-drc-errors: DRC reports 13 error-severity board violations: 5 padstack, 5 annular_width, 3 starved_thermal

Disposition: **duplicate**.

Location: project/Polygonus-Universal-Base.kicad_pcb. Condition: The DRC check summary reports 142 violations with by_severity error=13, decomposed by type as padstack:5, annular_width:5, starved_thermal:3 (remaining types are warnings)..

Annular-width and padstack errors indicate drilled pads/vias whose copper ring or stack geometry falls below the configured minimum, risking breakout or unreliable plated connections at fabrication; starved thermal spokes risk poor solder joints or marginal zone connection on thermal-relief pads.

Error-severity counts are native tool evidence, so this is reported as a defect even though the individual item coordinates lie past the truncated portion of drc.json and could not be inspected. The starved_thermal subset may be an artifact of the as-saved zone fill (the check explicitly forbade a refill pass), but annular-width/padstack errors are fill-independent and fabrication-relevant. Exact nets/locations need the full drc.json to disposition; severity major because annular/padstack errors can gate manufacturing.

Disposition reason: Identical content to the accepted check/drc defect: the same 13 error-severity violations (padstack 5, annular_width 5, starved_thermal 3) reported by the same tool run, with no additional independent evidence beyond the tool summary. Linked as duplicate so the defect remains attached to the accepted canonical check rather than double-counted.
- [work/drc/drc.json](<work/drc/drc.json>): header/summary region: 'included_severities' and check details by_type {padstack:5, annular_width:5, starved_thermal:3}; error items fall beyond the 100000-byte included prefix
- [work/drc/console.log](<work/drc/console.log>): 'Found 142 violations / 0 unconnected items / 114 schematic parity issues'

### circuit/f3-can2-termination-dnp: Both CAN2 120R termination resistors (R36, R37) are DNP; internal wideband bus unterminated as saved

Disposition: **duplicate**.

Location: project/wbo-can-trans.kicad_sch (both instances /WBO-CAN-TRANS/ and /WBO-CAN-TRANS1/), refs R36/R37. Condition: The single sheet's 120R termination resistor is marked (dnp yes)/(in_bom no), so both hierarchy instances (R36 and R37) are DNP. The /CAN2+ and /CAN2- bus carries both wideband controllers (U1/U2 via U20/U21) and is also brought out on J1 pins 15/11 and J2 pins 73/74..

With both terminations unpopulated, the internal CAN2 segment between the two TJA1051T-3 transceivers and the Polygonus module has no differential termination unless the builder populates R36/R37 or supplies external termination on the harness. An unterminated or singly-terminated high-speed CAN bus suffers reflections and bit errors, degrading or breaking wideband communication the README describes as internal to CAN bus #2.

Placement of the two 120R resistors near the two transceivers (x~81.6 and x~175.0) is a correct two-end termination scheme when both are populated (60R differential). Defaulting both to DNP is plausibly intentional because the bus is exposed on J1 for external extension, so termination choice is deferred to the builder; but nothing in the packet documents that, and the stated scope highlights 'internal dual-wideband CAN termination'. Labeled a concern, not a defect, since the DNP state may be a deliberate build option rather than an error.

Disposition reason: Same observation as physical/PHY-002: the single 120R terminator in wbo-can-trans.kicad_sch is (dnp yes)/(in_bom no), so both hierarchy instances R36/R37 are unpopulated and the internal CAN2 bus ships unterminated. PHY-002 carries the fuller evidence set (board placement of both terminators, README functional claim) and the higher severity, so it is the surviving canonical record; this concern-to-concern duplication loses no defect status.
- [inputs/project/wbo-can-trans.kicad_sch](<inputs/project/wbo-can-trans.kicad_sch>): symbol Device:R uuid d69380f9-5487-4782-9ce9-0690df62e543, Value '120', '(in_bom no)…(dnp yes)', instances R36 (/2889b04e…) and R37 (/10870bd0…)
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): components R36/R37 dnp:true; nets '/CAN2+' [C29.2,C33.2,D10.1,D11.1,J1.15,J2.73,R36.1,R37.1,U20.7,U21.7] and '/CAN2-'
- [work/geometry/geometry.json](<work/geometry/geometry.json>): footprint R36 dnp:true, pads on /CAN2+ and /CAN2- near (175.0,142.1)

### circuit/f4-vr-input-polarity: CRANK+ / CRANK- connector signals arrive inverted at the MAX9924 differential inputs

Disposition: **noted**.

Location: VR Conditioner sheet (/76f66c7e-94ea-4f9a-aa2e-a01725934e38/), U10 with R25-R28. Condition: Per the netlist, '/CRANK +' (J1.59) feeds R25 then R27 into U10 pin 2 (IN-), while '/CRANK -' (J1.55) feeds R26 then R28 into U10 pin 1 (IN+). The connector-positive signal lands on the inverting input and vice versa..

For a VR zero-cross conditioner, swapping the differential inputs inverts the output edge relative to the sensor's true-zero crossing direction, which shifts which tooth edge the ECU triggers on. If firmware or documentation assumes the conventional VR+ -> IN+ mapping, crank timing edge selection would be inverted.

The input network itself (series 4.7k pairs, 1n differential C23, DNP attenuator/hysteresis options R29/R32/R33, BIAS at mid-rail via R30/R31/C24, pull-up R34 on the output) matches a standard MAX9924 differential VR application; only the +/- assignment relative to the connector labels is reversed. This is frequently an intentional edge-polarity choice (or the harness labels are arbitrary), and firmware can select the trigger edge, so it is a concern to confirm against FOME conventions rather than an evidence-proven defect. The symbol is from the custom 'Simon' library and the MAX9924 pin names come from the netlist labels; no datasheet was available in the packet to independently confirm pin 1/2 identity.

Disposition reason: The connectivity observation is verified: '/CRANK +' (J1.59) reaches U10 pin 2 (Net-(U10-IN-)) via R25/R27, and '/CRANK -' (J1.55) reaches U10 pin 1 (Net-(U10-IN+)) via R26/R28, so connector-positive lands on the inverting input. However, no declared criterion or documented FOME polarity convention is violated: VR zero-cross edge polarity is commonly an intentional design choice and is firmware-selectable, and the MAX9924 symbol comes from an unverifiable custom library. Supported minor concern with no established failure mechanism; preserved as noted for confirmation against firmware conventions.
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets '/CRANK +':[J1.59,R25.2]; 'Net-(R25-Pad1)':[R25.1,R27.2]; 'Net-(U10-IN-)':[C23.1,R27.1,R29.2,U10.2]; '/CRANK -':[J1.55,R26.2]; 'Net-(R26-Pad1)':[R26.1,R28.2]; 'Net-(U10-IN+)':[C23.2,R28.1,R29.1,U10.1]
- [work/geometry/geometry.json](<work/geometry/geometry.json>): U10 pads 1/2 on Net-(U10-IN+)/Net-(U10-IN-); R27/R28 pad nets consistent

### circuit/f5-j2-footprint-substitution: J2 module footprint on the board differs from the schematic symbol's footprint

Disposition: **duplicate**.

Location: project/Polygonus-Universal-Base.kicad_pcb, footprint J2 at (136.57, 99.21). Condition: Schematic parity reports: 'Simon:Polygonus-With-USB-SWD doesn't match footprint given by symbol (fome_modules:polygonus)' for J2, plus a BOM-attribute mismatch on the same footprint..

All 107 Polygonus module pins (power, CAN, all I/O) route through J2. If the substituted footprint's pad numbering or geometry differed from the symbol's intended footprint, every module connection would be at risk. The parity check only compares footprint names/fields, not pad-to-pin compatibility.

The board netlist shows J2 pads carrying the expected nets and DRC reports 0 unconnected items, which indicates the substituted footprint's pad numbers do map to the schematic pins, so this is most likely a deliberate variant footprint (adding USB/SWD features). Marked a concern rather than a defect: pad-number mapping is validated by connectivity, but mechanical/geometric compatibility with the actual Polygonus module could not be verified from the packet.

Disposition reason: Same parity observation as physical/PHY-004: footprint J2 on the board is Simon:Polygonus-With-USB-SWD while the symbol assigns fome_modules:polygonus (same drc.json parity entries, same uuid f447e585). PHY-004 is the surviving canonical record; concern-to-concern duplication is consistent since net-level parity holds (0 unconnected items) and no electrical defect is established.
- [work/drc/drc.json](<work/drc/drc.json>): schematic_parity items for 'Footprint J2' uuid f447e585-df78-4239-b8cb-4653b3837bb1: footprint_symbol_mismatch (name) and attributes mismatch
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): component J2 footprint 'fome_modules:polygonus'; J2 pin nets populated throughout (e.g. /CAN2+ J2.73, +5V J2.81, +3.3V J2.82)

### circuit/f6-can1-no-protection: CAN1 lines pass from module to harness with no on-board ESD protection, filtering or termination

Disposition: **noted**.

Location: Root sheet, nets /CAN+ and /CAN-. Condition: Nets '/CAN+' [J1.47, J2.93] and '/CAN-' [J1.46, J2.94] contain only the harness connector and the module header: no NUP2105L clamp, no 47p filter caps, and no termination option, in contrast to the CAN2 bus which has all three..

If the Polygonus brain module does not carry its own CAN1 ESD protection, the primary vehicle CAN bus would be exposed to automotive transients directly at the module transceiver; absence of any base-board termination option also forces termination entirely into the harness.

This is consistent with the declared modular architecture: the CAN1 transceiver lives on the Polygonus module, which may include its own protection and termination, and the donor review explicitly excludes integrated module coverage and automotive transient qualification. Reported as an informational concern so the asymmetry versus CAN2 is dispositioned deliberately rather than by omission.

Disposition reason: Verified in connectivity: '/CAN+' [J1.47,J2.93] and '/CAN-' [J1.46,J2.94] contain only the harness and module connector pins, with no TVS, filter, or termination footprints, in contrast to CAN2 which carries D10/D11 (NUP2105L), 47p filters, and terminator footprints. No declared criterion requires base-board CAN1 protection, the CAN1 transceiver lives on the Polygonus module whose internals are explicitly out of this donor review's scope, and automotive transient qualification is a declared limitation. Supported info-level observation of the asymmetric protection scheme; canonical record for this topic.
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets '/CAN+':[J1.47,J2.93] and '/CAN-':[J1.46,J2.94] vs '/CAN2+'/'/CAN2-' containing D10/D11 (NUP2105L) and C29/C30/C33/C34
- [inputs/project/README.md](<inputs/project/README.md>): Features/Connectivity: 'Dual CAN'

### physical/PHY-001: Saved board has 13 DRC error-severity violations: padstack, annular width, and starved thermal

Disposition: **duplicate**.

Location: Polygonus-Universal-Base.kicad_pcb (specific items truncated out of supplied drc.json excerpt). Condition: DRC with --severity-all reports 13 error-severity violations on the saved board: padstack x5, annular_width x5, starved_thermal x3 (by_type/by_severity in the drc check record; console confirms 142 total violations)..

Annular ring below minimum risks drill breakout and open or intermittent plated-through connections after fabrication; padstack errors risk fab rejection or unreliable via construction; starved thermal reliefs mean pads join their copper zones through insufficient spokes, constricting the electrical connection and degrading solderability on what are typically ground/power pads.

These are native tool errors on the saved copper, not tool failures. The exact coordinates are not in the supplied excerpt, so the count and classification come from the tool's own summary; the physical failure modes they encode (drill breakout, starved zone connection) are manufacturing and connectivity risks independent of location. Zone-fill-as-saved is a caveat for the starved_thermal subset only.

Disposition reason: Identical to the accepted check/drc defect: same 13 error-severity DRC violations (padstack x5, annular_width x5, starved_thermal x3) from the same tool run, known only through the tool's own summary because the individual entries fall beyond the supplied drc.json excerpt. Defect-to-defect duplication keeps it attached to the accepted canonical check finding.
- [work/drc/drc.json](<work/drc/drc.json>): by_type: padstack=5, annular_width=5, starved_thermal=3; by_severity: error=13 (summarized in drc check details; individual entries beyond byte 100000 of the excerpt)
- [work/drc/console.log](<work/drc/console.log>): line 1: 'Found 142 violations'

### physical/PHY-002: Internal dual-wideband CAN2 bus has both 120 ohm terminators (R36, R37) unpopulated

Disposition: **deferred**.

Location: wbo-can-trans.kicad_sch (R36 at 115.57,86.36, two sheet instances R36/R37); board: R36 at (175.01,140.67), R37 at (81.62,140.55). Condition: The README states the two wideband controllers are connected internally to CAN bus #2. The CAN2+/CAN2- nets join U20.7/6, U21.7/6, J2.73/74, J1.15/11, protectors D10/D11 and filter caps, plus terminators R36.1/2 and R37.1/2. Both R36 and R37 are marked (dnp yes)(in_bom no) in the native sheet and dnp:true in the netlist, so the saved board ships with zero on-board CAN2 termination..

U20 and U21 sit at roughly opposite board edges (R36 near x=175 mm, R37/U21 near x=75-82 mm), so the populated pair would form correct split end-termination. With both DNP, the internal multi-node bus between the two wideband MCUs, the ECU module, and the harness pins is unterminated unless the external harness supplies both 120 ohm ends; reflections on an unterminated or singly-terminated bus can corrupt wideband CAN traffic at typical 500 kbit rates.

Marked as a concern rather than a defect because the DNP may be a deliberate builder option (CAN2 is also exposed on J1 pins 11/15 for external nodes, where double internal termination would conflict with harness terminators). However, no documentation in the packet instructs the builder to terminate externally, and the advertised internal wideband function depends on a properly terminated bus, so the as-saved population is a real signal-integrity risk that the scope explicitly asks to be reviewed.

Disposition reason: Major-severity concern that evidence confirms but does not resolve. The native sheet proves R36/R37 are DNP (symbol uuid d69380f9: value 120, in_bom no, dnp yes applied to both instances), connectivity and geometry confirm both terminators sit correctly at the two transceiver ends of /CAN2+-/CAN2-, and the README advertises internal wideband operation on CAN bus #2. Whether the all-DNP population is a deliberate builder option (the bus is also exposed on J1 pins 11/15, where internal termination would conflict with harness terminators) or an omission cannot be determined from the packet; no population/termination requirement is declared. Per adjudication rules a major concern cannot be merely noted, and inventing a termination requirement is not permitted, so it is deferred pending design-intent documentation or build instructions.
- [inputs/project/wbo-can-trans.kicad_sch](<inputs/project/wbo-can-trans.kicad_sch>): symbol R36 (uuid d69380f9-5487-4782-9ce9-0690df62e543): value 120, (in_bom no)(dnp yes), instances R36/R37
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets /CAN2+ and /CAN2- include R36.1/R37.1 and R36.2/R37.2; components R36, R37 dnp:true
- [work/geometry/geometry.json](<work/geometry/geometry.json>): R36 dnp:true pads on /CAN2+ and /CAN2- at (175.01,142.13); U21 at (75.48,140.27); C33/C34 near x=81-82
- [inputs/project/README.md](<inputs/project/README.md>): 'Dual wideband O2 sensor controllers ... connected internally to CAN bus #2'

### physical/PHY-003: Five ERC error-severity violations: undriven power inputs, power-output conflict, unconnected fault pin

Disposition: **duplicate**.

Location: Polygonus-Universal-Base.kicad_sch root sheet. Condition: ERC reports 5 errors: GND power input #PWR01 not driven (36.83,78.74); A1 pin 8 VMOT power input not driven (48.26,219.71); J2 pin 90 12V_IGN power input not driven (293.37,173.99); J2 pins 40/41 both Power-output type tied together on 5V_SENSOR_1 (262.89,128.27/130.81); A1 pin 2 ~FLT output unconnected with no no-connect marker (38.1,224.79)..

The undriven power-input errors indicate battery/harness-fed nets (12V_KEY, VMOT via 12V_MR_FUSE) and GND lack PWR_FLAG markers, so the tool cannot confirm a power source; the pin_to_pin error is two power-output pins of the same module symbol paralleled; the unconnected DRV8825 ~FLT pin means stepper-driver fault reporting is physically unmonitored and the omission is unmarked, so it cannot be distinguished from an accidental open.

These are confirmed native errors. Electrically, the undriven-power and paralleled-output cases are explainable (harness-fed rails, duplicated module pins) and therefore minor, but they remain unresolved errors that mask any future genuine power-sourcing fault; the unmarked ~FLT open is a small functional gap (no stepper fault detection) rather than a hazard.

Disposition reason: Restates the same 5 error-severity ERC violations established by the accepted check/erc finding (same uuids ad746bdd/acdd6b6d/3d2dd476/a25f94e0/2f453565-b5fc1cf2, same locations and types). Defect-to-defect duplication preserves the defect under the accepted canonical check record.
- [work/erc/erc.json](<work/erc/erc.json>): root sheet '/': power_pin_not_driven x3 (uuids ad746bdd, acdd6b6d, 3d2dd476), pin_to_pin (2f453565/b5fc1cf2), pin_not_connected A1 pin 2 (a25f94e0)
- [work/erc/console.log](<work/erc/console.log>): 'Found 353 violations'

### physical/PHY-004: Main module footprint on board differs from schematic assignment (J2)

Disposition: **noted**.

Location: Polygonus-Universal-Base.kicad_pcb, footprint J2 at (136.57,99.21). Condition: Schematic parity reports: 'Simon:Polygonus-With-USB-SWD doesn't match footprint given by symbol (fome_modules:polygonus)' plus an exclude-from-BOM attribute mismatch for footprint J2..

The board carries a different footprint variant for the central ECU brain-module connector than the schematic assigns. Net-level parity passed (0 unconnected items), so pad-to-net mapping currently agrees, but the divergent footprint identity means mechanical outline, extra pads (USB/SWD variant), or future re-sync from schematic could silently alter the board, and the Simon library is absent from the verification environment so the placed footprint could not be validated against any source.

Concern rather than defect: connectivity parity holds and the substitution is plausibly an intentional variant with USB/SWD breakout, but the schematic/board divergence on the single most connected component (107 pins) is a configuration-management risk, especially with the source library unavailable for comparison.

Disposition reason: Verified parity observation: the board carries Simon:Polygonus-With-USB-SWD for J2 while the schematic symbol assigns fome_modules:polygonus, plus a BOM-attribute mismatch (both visible in the drc.json parity section). No electrical defect is established: DRC reports 0 unconnected items and the J2 pads carry the expected nets throughout, indicating the variant footprint's pad numbering maps correctly; the substitution is plausibly an intentional USB/SWD variant. Supported minor configuration-management concern that violates no declared criterion; the mechanical compatibility caveat stands but cannot be evaluated with the Simon library absent. Canonical record for this topic.
- [work/drc/drc.json](<work/drc/drc.json>): schematic_parity entries for 'Footprint J2' (uuid f447e585-df78-4239-b8cb-4653b3837bb1), types footprint_symbol_mismatch
- [work/drc/execution.json](<work/drc/execution.json>): argv includes --schematic-parity; exit_code 0

### physical/PHY-005: Library environment gaps leave ~200 symbol/footprint integrity checks unresolved

Disposition: **noted**.

Location: ERC/DRC runs (sandboxed environment, project libraries absent). Condition: ERC: lib_symbol_mismatch x89, lib_symbol_issues x14, footprint_link_issues x8 ('configuration does not include the footprint library Simon' / symbol library hellen-one-common, Connector, Device:CP etc.). DRC: lib_footprint_mismatch x106, lib_footprint_issues x9..

With the Simon, hellen-one-common, fome_modules and related libraries missing from the verification sandbox, every cached symbol and footprint from those libraries is unverifiable against its source, and stock-library parts additionally mismatch their current library copies (KiCad 10 vs design-era KiCad 9 libraries). A stale or edited cached footprint (e.g., the 112-pin Molex J1 or the wideband module footprints) would pass this review undetected as long as internal pad/net mapping is self-consistent.

This is a verification-coverage challenge rather than a board fault: the checks ran, but a large class of library-integrity assertions is vacuous in this environment, so the 'native rule coverage' claim in the scope is weaker than the raw run counts suggest.

Disposition reason: Verified coverage limitation, not a board defect: ERC reports lib_symbol_mismatch=89, lib_symbol_issues=14, footprint_link_issues=8 and DRC reports lib_footprint_mismatch=106, lib_footprint_issues=9 because the Simon, hellen-one-common, fome_modules and related project libraries are absent from the sandbox, so cached symbols/footprints cannot be validated against sources. This weakens the effective breadth of the native rule coverage claimed in scope but violates no design criterion; the checks themselves executed. Supported minor concern, noted.
- [work/erc/erc.json](<work/erc/erc.json>): by_type lib_symbol_mismatch=89, lib_symbol_issues=14, footprint_link_issues=8; e.g. 'The current configuration does not include the footprint library Simon' for J1 (uuid 6971898b)
- [work/drc/drc.json](<work/drc/drc.json>): by_type lib_footprint_mismatch=106, lib_footprint_issues=9; e.g. 'The current configuration does not include the footprint library Simon' for J1 (uuid 93ba26da)

### physical/PHY-006: 229 off-grid wire/pin endpoints in the schematic

Disposition: **noted**.

Location: Multiple sheets, concentrated in root and VR Conditioner areas (e.g. wires at 286.385,107.315; 107.315,80.01; 165.735,75.565). Condition: ERC reports 229 endpoint_off_grid warnings: wire ends and symbol pins (including R25/R26/R31/R34 pins, JP1, PWR symbols, C24/C25 pins) sit off the connection grid..

Off-grid endpoints currently resolve to connected nets (netlist is complete, 0 unconnected board items), but they are fragile: any future edit or symbol move can silently break a junction that visually appears connected, and several off-grid points sit on the VR conditioner analog front end where a silent open would disable crank-signal processing.

No present-day connectivity fault is evidenced (DRC shows 0 unconnected items), so this is a latent-risk concern about schematic hygiene, not a defect in the saved netlist.

Disposition reason: Verified: ERC reports 229 endpoint_off_grid warnings with sampled entries confirmed in the report (e.g. uuid 15d9afbe at 286.385,107.315; R25 pin 1 at 107.315,80.01; R34 pin 1 at 165.735,75.565). No present-day connectivity fault exists: the netlist resolves all affected nets with expected members and DRC reports 0 unconnected items, so no criterion is currently violated. This is a supported latent-risk hygiene concern (fragility under future edits, concentrated in the VR conditioner analog front end), correctly preserved as a non-defect observation.
- [work/erc/erc.json](<work/erc/erc.json>): by_type endpoint_off_grid=229; e.g. uuids 15d9afbe (286.385,107.315), 34654827 (R25 pin 1 at 107.315,80.01), c2f74bd3 (R34 pin 1 at 165.735,75.565)
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): all affected nets (Net-(R25-Pad1), Net-(U10-IN-) etc.) presently resolve with expected members

### physical/PHY-007: Hierarchy depends on disposable 'VR Conditioner.kicad_sch' byte-copy alias of the lowercase original

Disposition: **noted**.

Location: project/VR Conditioner.kicad_sch (alias of VR conditioner.kicad_sch). Condition: The candidate adds VR Conditioner.kicad_sch as a byte copy of the existing lowercase file so the 10-instance hierarchy resolves; ERC/netlist runs list project/VR Conditioner.kicad_sch and design.xml records sheet 4 source 'VR Conditioner.kicad_sch'. The declared limitations confirm the original checkout's filename discrepancy remains open..

Two byte-identical files with case-differing names coexist in the project. On case-insensitive filesystems they collide; on case-sensitive systems future edits can land in whichever copy a given tool resolves, silently forking the VR conditioner sheet (MAX9924 crank input chain, C23-C25, R25-R34, JP1). All connectivity verified here is against the alias, not the original file path.

The alias is explicitly declared as a disposable workaround, so this is not a new defect, but the physical design content of the VR conditioner now has two potential sources of truth; the verified netlist applies only to the alias as supplied.

Disposition reason: Verified and explicitly declared: the packet's declared_limitations state the candidate adds VR Conditioner.kicad_sch as a byte copy of the lowercase original as a disposable alias, and the netlist export confirms the hierarchy resolves through it (sheet 4 source 'VR Conditioner.kicad_sch', schematic_files list). Two case-differing byte-identical files are a real fork/collision risk for future edits, but this is a declared workaround of the review scaffolding, not a defect in the design under review; all verified connectivity applies to the alias as supplied. Supported minor concern, noted.
- [work/netlist/design.xml](<work/netlist/design.xml>): sheet number 4 '/VR Conditioner/' source 'VR Conditioner.kicad_sch'
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): components C23-C25, R25-R34, U10, JP1 on sheet /VR Conditioner/ (uuid 76f66c7e-94ea-4f9a-aa2e-a01725934e38)

### physical/PHY-008: Main CAN bus (CAN1) has no board-level ESD protection or filtering, unlike CAN2

Disposition: **duplicate**.

Location: Root sheet nets /CAN+ and /CAN-. Condition: Nets /CAN+ (J1.47, J2.93) and /CAN- (J1.46, J2.94) run directly from the harness connector to the brain-module connector with no TVS, filter capacitors, or termination footprints on the base board, whereas CAN2 carries NUP2105L protectors (D10/D11), 47 pF filters, and terminator footprints..

If the Polygonus module does not include transceiver-side protection, the primary vehicle CAN lines enter the module unprotected against harness ESD/transients; the asymmetry versus CAN2 suggests protection was assumed to live on the module, which cannot be confirmed from this packet.

No requirement for base-board CAN1 protection is declared, and the module (out of scope) plausibly provides it, so this is recorded only as an informational observation about the asymmetric protection scheme and the coverage boundary, not as a fault.

Disposition reason: Same observation as circuit/f6: CAN1 nets /CAN+ and /CAN- run connector-to-module with no on-board protection, filtering, or termination footprints, in contrast to CAN2. circuit/f6 is the surviving canonical noted record (it additionally cites the README's modular-architecture context); concern-to-concern duplication, no defect status lost.
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): '/CAN+': [J1.47, J2.93]; '/CAN-': [J1.46, J2.94]; contrast '/CAN2+'/'/CAN2-' with D10/D11, C29/C30/C33/C34, R36/R37

## Limitations

- Donor carrier remains a modular design; integrated FOME-MC coverage is not established by this donor review.
- Scope omits full automotive transients, ignition energy, wideband control stability, EMC and thermal qualification.
- Saved copper is checked without refilling or changing the board.
- This disposable candidate adds VR Conditioner.kicad_sch as a byte copy of the existing lowercase VR conditioner.kicad_sch. The original checkout remains unchanged, and its filename discrepancy remains open there.
- text artifact partially supplied: work/geometry/geometry.json (100000/1657568 bytes)
- text artifact partially supplied: work/drc/drc.json (100000/142607 bytes)
- text artifact partially supplied: work/erc/erc.json (100000/226344 bytes)
- text artifact partially supplied: work/netlist/design.xml (84913/252017 bytes)
- text artifact partially supplied: work/geometry/execution.json (0/2120 bytes)
- text artifact partially supplied: work/geometry/console.log (0/446 bytes)

This result applies only to the captured candidate and declared criteria. Prototype and bench
validation, including operating conditions omitted from this review, remain necessary.

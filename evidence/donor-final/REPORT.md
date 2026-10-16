# Universal 112 saved donor baseline review

Outcome: **incomplete**; process state: `complete`.

Candidate: `693f8163ce687e039a86a686decde90440b8c14b5ae3b38c294486b2f1e4fb44`
Run: `c6cb3775-cb6c-446f-acaa-f3eb7229b643`

Read-only donor baseline: native rule coverage, saved board implementation, and internal dual-wideband CAN termination. No ECU redesign or manufacturing approval.

## Checks

| Check | Execution | Design result | Summary |
| --- | --- | --- | --- |
| erc | malformed | unknown | hierarchy references missing sheet files: ['VR Conditioner.kicad_sch']; coverage is incomplete |
| drc | ok | defect | DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s) |
| netlist | malformed | unknown | hierarchy references missing sheet files: ['VR Conditioner.kicad_sch']; coverage is incomplete |
| geometry | ok | info | saved-board geometry extracted (no declared geometric requirement applied) |

## Initial reviews

- circuit: ok; requested model claude-fable-5.
- physical: ok; requested model claude-fable-5.

circuit coverage: Traced inputs/project/wbo-can-trans.kicad_sch net-by-net: TJA1051T-3 pinout (TXD pin1->CAN_TX, RXD pin4->CAN_RX, VCC pin3->+5V with C31 100n, VIO pin5->+3.3V with C32 100n, S pin8->GND normal mode, GND pin2->GND, CANH pin7->CAN2+, CANL pin6->CAN2-); no CANH/CANL swap found under y-mirror transform.; Verified NUP2105L dual ESD clamp (D10/D11) cathodes to CAN2+ and CAN2-, anode pin3 to GND; verified 47p filter caps C29/C30 from CAN2+/CAN2- to GND in both sheet instances.; Checked internal dual-wideband CAN2 termination: sheet resistor R36 (120R, 1206) is dnp/in_bom-no with 'n.p.' annotation; confirmed on saved board that both instance refs R36 and R37 bridge /CAN2+ to /CAN2- and appear in geometry dnp_refs.; Cross-checked board geometry pad nets for U21 (TJA1051T-3: /CAN_TX2, /CAN_RX2, /CAN2+, /CAN2-, +5V, +3.3V, GND) and U1 (f042_wideband_module: /CAN_RX, /CAN_TX, /LSU_* pins) within the included geometry prefix.; Reviewed DRC execution record (kicad-cli 10.0.7, exit 0, severity-all with schematic parity), console log counts (142 violations, 0 unconnected, 114 parity issues), and the included prefix of work/drc/drc.json (parity warnings, silk_over_copper, lib_footprint_mismatch/issues, extra_footprint list).; Correlated the 15 extra_footprint parity items (U10 MAX9924, R25-R34, C23-C25, JP1) with the missing 'VR Conditioner.kicad_sch' sheet file that caused ERC and netlist checks to be malformed.; Confirmed DRC reports zero unconnected items on the saved board (fill state as saved).; Sanity-checked README feature claims against visible board content (dual CAN transceivers, dual wideband module footprints, VNL5090 lowside drivers, TLE9201SG H-bridge, DRV8825 stepper header).
- work/drc/drc.json supplied only as a 100000-byte prefix: the 13 error-severity violations (padstack, annular_width, starved_thermal per the check's by_type summary) are not individually visible, so affected refs/locations could not be verified.
- work/geometry/geometry.json supplied only as a 100000-byte prefix: the second wideband module and second transceiver instance (sheet instance refs U20/C29-C32/R36 vs board region near U1) could not be fully inspected; full net list for CAN2 node count is unverified.
- ERC and netlist checks were malformed (missing sheet file), so no machine electrical-rules coverage exists for any sheet; my connectivity review covers only the supplied wbo-can-trans sheet.
- Root schematic and all other sheets (including VR conditioner) were not supplied; parent-side wiring of the CAN2+/CAN2- hierarchical pins and whether CAN2 is exposed on the external harness could not be confirmed.
- No CAN bit rate, bus length, or termination requirement is stated in the packet, so the DNP-termination finding is bounded to a concern.
- No component datasheet data beyond symbol fields was available; part ratings (e.g., NUP2105L 24Vrwm vs automotive transients) were not assessed per declared scope.
- Saved copper evaluated as-is; zone refill forbidden per candidate constraints.

physical coverage: Full read of inputs/project/wbo-can-trans.kicad_sch: transceiver supplies (VCC=+5V, VIO=+3.3V, S=GND), ESD protection (NUP2105L), filter caps, DNP 120R termination, hierarchical label shapes, wiring topology; inputs/project/README.md feature claims cross-checked against CAN2 implementation (internal dual-wideband on CAN bus #2); work/drc/drc.json (supplied 100000-byte prefix): all schematic_parity items (114) including 15 extra_footprint and the J2 footprint substitution; silk_over_copper and lib_footprint_mismatch/lib_footprint_issues violations as included; DRC check record details: violation counts by severity (error: 13) and by type (padstack 5, annular_width 5, starved_thermal 3), 0 unconnected items; work/geometry/geometry.json (supplied 100000-byte prefix): pad-level net assignments for U21 (TJA1051T-3), U1 (f042_wideband_module), CAN2 filter caps C29/C30/C33/C34, DNP termination R36, DNP refs list, and other visible footprints; work/drc/execution.json, work/drc/console.log, work/geometry/execution.json, work/geometry/console.log: tool invocation integrity, exit codes, hash consistency with board_sha256
- ERC and netlist checks never executed (missing sheet file 'VR Conditioner.kicad_sch'); electrical-rule and full connectivity-vs-schematic coverage is absent for the whole hierarchy
- work/drc/drc.json truncated at 100000/141827 bytes: the 13 error-severity violation items (padstack, annular_width, starved_thermal) fall outside the supplied prefix, so their board locations could not be inspected
- work/geometry/geometry.json truncated at 100000/1657568 bytes: track segments (2207), vias (1224), and zones (18) were not supplied; no clearance, return-path, or current-path geometry review was possible
- No stackup, copper weight, or thermal data supplied; ampacity and thermal qualification of high-current paths (IGBT ignition drivers, H-bridges, lowside outputs) cannot be assessed and must not be inferred from widths
- Only one schematic sheet (wbo-can-trans) supplied; the root sheet, VR conditioner sheet, and all other hierarchy content were not reviewable
- Zone fill evaluated strictly as saved (read-only candidate); starved_thermal findings reflect the saved fill state and could not be re-verified by refilling
- Termination state of the plug-in f042 wideband modules and of the external harness is outside the packet; the CAN2 termination finding is conditional on those unknowns
- Declared scope exclusions apply: no automotive transient, ignition energy, wideband control stability, EMC, or thermal qualification was performed

## Findings and dispositions

### check/drc: DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition: **accepted**.

Location: drc. Condition: check drc (drc).

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition reason: Native DRC on the saved board (kicad-cli 10.0.7, exit 0, --severity-all --schematic-parity) reported 142 violations including 13 error-severity items (by_severity error=13; by_type padstack=5, annular_width=5, starved_thermal=3) plus 114 parity issues. This is a failing check criterion against the exact board sha256 4e059d57...; it cannot be dismissed and no repair exists in the packet. Remains the canonical executable DRC defect; circuit/F2, physical/F2, and physical/F7 are subsets of this same tool run.
- [work/drc/execution.json](work/drc/execution.json): check:drc
- [work/drc/console.log](work/drc/console.log): check:drc
- [work/drc/drc.json](work/drc/drc.json): check:drc

### circuit/F1-sheet-file-case-mismatch: Hierarchy references 'VR Conditioner.kicad_sch' but checkout supplies 'VR conditioner.kicad_sch'; ERC and netlist coverage lost

Disposition: **accepted**.

Location: project root sheet reference -> VR Conditioner.kicad_sch (checkout: VR conditioner.kicad_sch). Condition: Any case-sensitive filesystem (as used by the native toolchain here) loading the project hierarchy..

The sheet file reference fails to resolve, so eeschema cannot build the full hierarchy: ERC and netlist generation abort (both checks executed 'malformed'), and DRC schematic parity flags all 15 VR-conditioner board components (U10 MAX9924, R25-R34, C23-C25, JP1) as 'Extra footprint' because they exist on the board but not in the loaded schematic. All electrical rule verification for the entire project is therefore unestablished.

The ERC and netlist checks both report missing_sheet_files=['VR Conditioner.kicad_sch'] and the declared limitation confirms the checkout filename is lower-case 'conditioner'. The parity extra_footprint set is exactly the VR-conditioner analog front end (MAX9924 plus its bias/filter network), which proves the board implements a sheet the schematic hierarchy cannot load. This is a native violation of project integrity, not a tool failure, and it blocks all machine electrical verification of the design as supplied.

Disposition reason: Verified against native input and artifacts: ERC and netlist checks both aborted with missing_sheet_files=['VR Conditioner.kicad_sch'] while the declared limitation confirms the checkout supplies 'VR conditioner.kicad_sch' (case mismatch). Independently, DRC parity flags exactly the VR-conditioner board section (15 extra_footprint items: U10 MAX9924, R25-R34, C23-C25, JP1) as having no schematic counterpart, and geometry shows U10 populated with live nets (/VR1+, +5V, GND). All machine ERC/netlist coverage for the hierarchy is lost; this is the canonical project-integrity defect. Major severity is appropriate.
- [work/drc/drc.json](work/drc/drc.json): schematic_parity entries type=extra_footprint for C25 (uuid 0070999b), R28 (40773cef), U10 (4ccdf03c), R27 (4f184bcb), R29 (667488c6), R32, C24, R33, R30, R25, JP1, R26, C23, R31, R34
- [work/geometry/geometry.json](work/geometry/geometry.json): footprint ref U10 value MAX9924 with live nets (/VR1+, Net-(U10-IN+), +5V) and footprints R27/R28/R29 on Net-(U10-IN+/-): components wired on the board yet absent from the loaded schematic
- [work/drc/console.log](work/drc/console.log): line 3: 'Found 114 schematic parity issues'

### circuit/F2-drc-error-violations: Native DRC reports 13 error-severity violations: 5 padstack, 5 annular_width, 3 starved_thermal

Disposition: **duplicate**.

Location: inputs/project/Polygonus-Universal-Base.kicad_pcb (saved board, sha256 4e059d57...). Condition: Board fabricated/assembled as saved; zones evaluated as saved per read-only constraint..

Annular-width and padstack errors indicate pad/via geometry below the board's own constraints, risking drill breakout and unreliable plated connections; starved thermal errors indicate zone thermal-relief spokes insufficient for the connected pads, risking poor solder joints or an effectively open zone connection.

The DRC run executed cleanly against the exact board sha and natively reported 13 violations at error severity whose types (padstack, annular width, starved thermal) are manufacturing/reliability defects rather than cosmetic warnings. A zero exit code does not clear these; they are real rule violations in the saved copper. The specific items are truncated from the supplied artifact prefix, so affected references should be confirmed from the full drc.json, but the counts and types are tool-reported facts.

Disposition reason: Restates the same native DRC run that check/drc reports: the 13 error-severity violations (5 padstack, 5 annular_width, 3 starved_thermal) are a subset of the 142 violations counted by the accepted check finding, from the identical tool invocation and board sha. The analysis is correct and supported (console count plus check by_severity/by_type breakdown), so it is linked to the accepted canonical check rather than dismissed. The individual error items lie beyond the 100000-byte drc.json prefix and should be localized from the full report during repair.
- [work/drc/console.log](work/drc/console.log): line 1: 'Found 142 violations' (kicad-cli pcb drc --severity-all, exit 0)
- [work/drc/drc.json](work/drc/drc.json): violations[] (error-severity items lie beyond the included 100000-byte prefix; check summary by_severity error=13, by_type padstack=5, annular_width=5, starved_thermal=3)
- [work/drc/execution.json](work/drc/execution.json): argv kicad-cli pcb drc --format json --severity-all --schematic-parity; exit_code 0

### circuit/F3-can2-termination-dnp: Both internal CAN2 (wideband bus) 120R terminations are DNP; bus has no populated termination

Disposition: **accepted**.

Location: inputs/project/wbo-can-trans.kicad_sch R36 (instances R36/R37) across /CAN2+ and /CAN2-. Condition: Operating the internal wideband CAN bus (ECU brain CAN2 transceiver U21 plus wideband module transceivers) without user-added external termination..

With both 120R resistors unpopulated, differential termination is only the transceivers' internal bias (tens of kilohms). Recessive-state transitions must discharge the bus capacitance (including the four populated 47p filter caps C29/C30/C33/C34 plus trace and transceiver capacitance) through that high impedance, giving microsecond-scale edges that corrupt bits at typical CAN rates; reflections are also undamped. Internal wideband communication would be unreliable or nonfunctional unless termination exists elsewhere.

The DNP is clearly intentional (schematic 'n.p.' note), which suggests the designers expect termination elsewhere, possibly external on the harness since /CAN2+ also appears on module-connector pads (J2 pad 73 per DRC items). But the packet contains no evidence of any populated termination anywhere on CAN2, and the README presents the wideband link as internal. If the bus is used as documented with no external nodes, it is unterminated and likely nonfunctional. Because the intended system-level termination strategy is unstated and the root schematic is unavailable, this is reported as a concern rather than a defect, but it directly challenges the packet's stated scope of internal dual-wideband CAN termination.

Disposition reason: Condition verified in the native schematic: R36 (value 120, dnp yes, in_bom no, 'n.p.' text annotation) wires through (119.38,86.36)->(121.92,88.9) to the CAN2- rail and through (111.76,86.36)->(109.22,83.82) to the CAN2+ rail, so the only differential termination on the internal wideband bus is unpopulated in both sheet instances (refs R36 and R37, confirmed dnp in geometry dnp_refs with pad nets /CAN2+ and /CAN2-). The 47p caps C29/C30/C33/C34 are line-to-ground filters, not termination. Internal dual-wideband CAN termination is explicitly in the review scope and no populated termination is evidenced anywhere in the packet; correctly held at concern (not defect) because module- or harness-side termination cannot be excluded from supplied data. Canonical for this issue.
- [inputs/project/wbo-can-trans.kicad_sch](inputs/project/wbo-can-trans.kicad_sch): symbol uuid d69380f9-5487-4782-9ce9-0690df62e543: R36 value 120, (dnp yes), (in_bom no); text 'n.p.' uuid 98ed307b at (113.03 76.2); wiring ties R36 pins to CAN2+ (y=83.82) and CAN2- (y=88.9)
- [work/geometry/geometry.json](work/geometry/geometry.json): dnp_refs includes R36 and R37; footprint R36 (uuid starting ed50429c pad entries) dnp=true with pad1 net /CAN2+ and pad2 net /CAN2- at ~(175.0, 142.1)
- [inputs/project/README.md](inputs/project/README.md): 'Dual wideband O2 sensor controllers ... (connected internally to CAN bus #2)'

### circuit/F4-footprint-library-hygiene: J2 brain-module footprint differs from symbol-specified footprint and 'Simon' footprint library is absent from configuration

Disposition: **accepted**.

Location: Polygonus-Universal-Base.kicad_pcb footprints J2, P1, P3, P5, P6, J1, J4 (library 'Simon'). Condition: Rebuilding, re-annotating, or re-importing netlist changes on any machine without the private 'Simon' library..

J2 uses Simon:Polygonus-With-USB-SWD while the symbol specifies fome_modules:polygonus; the 'Simon' library is not in the configuration, so footprint updates from library would fail or silently substitute, and 106 lib_footprint_mismatch warnings show board copies have diverged from their libraries. Risk is to interface geometry fidelity (the brain-module connector pin field) if footprints are ever refreshed.

As saved, the board geometry is self-contained and DRC shows 0 unconnected items, so there is no demonstrated electrical fault. However, a deliberate footprint substitution on the central 112-pad module interface that contradicts the symbol field, combined with an unavailable source library, is a maintainability and reproducibility risk worth tracking; it also inflates parity noise (96 Datasheet-field mismatches, 106 library mismatches) that can mask real parity defects.

Disposition reason: Verified verbatim in the included drc.json prefix: J2 footprint_symbol_mismatch ('Simon:Polygonus-With-USB-SWD doesn't match footprint given by symbol (fome_modules:polygonus)', uuid f447e585) plus lib_footprint_issues 'configuration does not include the footprint library Simon' on P1/P3/P5/P6/J1/J4, against a backdrop of 106 lib_footprint_mismatch warnings. No demonstrated electrical fault (DRC 0 unconnected items), so minor concern is the right calibration; retained as the canonical footprint/library hygiene finding covering both the J2 substitution and the library-drift population. Subsumes physical/F4 and physical/F5.
- [work/drc/drc.json](work/drc/drc.json): schematic_parity item: 'Simon:Polygonus-With-USB-SWD doesn't match footprint given by symbol (fome_modules:polygonus)' for Footprint J2 uuid f447e585
- [work/drc/drc.json](work/drc/drc.json): violations type=lib_footprint_issues: 'The current configuration does not include the footprint library Simon' (P1 uuid 1646febd, P3, P5, P6, J1, J4)

### circuit/F5-canrx-label-direction: Hierarchical label CAN_RX declared shape 'input' but is driven by transceiver RXD output

Disposition: **accepted**.

Location: inputs/project/wbo-can-trans.kicad_sch hierarchical_label CAN_RX at (204.47, 83.82). Condition: ERC review of the sheet (currently impossible because ERC is malformed per F1)..

The label connects to U20/U21 pin 4 (RXD, declared output). Declaring the sheet port as input misstates signal direction; electrically the net still forms, but ERC pin-conflict checking at the parent sheet pin is weakened and future edits may wire a driver against RXD.

No functional impact in the current wiring (RXD correctly drives the port toward the parent), and CAN_TX ('input' driving TXD) is correct. This is a documentation/ERC-hygiene nit; it matters mainly because ERC cannot currently run (F1) to police such direction mismatches project-wide.

Disposition reason: Verified by geometric trace of the native schematic: U20 (TJA1051T-3) is placed mirror-y at (166.37, 86.36), which puts RXD pin 4 (declared pin type output, symbol offset -12.7, +2.54) at (179.07, 83.82); wire uuid 327ec337 runs (179.07,83.82)-(204.47,83.82) to hierarchical label 'CAN_RX' declared (shape input). An output-driven sheet port declared as input is a real direction mismatch; CAN_TX->TXD (input pin) is correctly shaped, confirming only CAN_RX is wrong. No netlist impact, so info-level concern is correct; relevant because ERC (which would police this) cannot currently run per circuit/F1. Canonical; subsumes physical/F6.
- [inputs/project/wbo-can-trans.kicad_sch](inputs/project/wbo-can-trans.kicad_sch): hierarchical_label 'CAN_RX' (shape input) uuid 0cfcbd19; wire uuid 327ec337 (179.07,83.82)-(204.47,83.82) to U20 RXD pin 4 (output, at mirrored position 179.07,83.82)

### physical/F1: Hierarchy references missing sheet file 'VR Conditioner.kicad_sch' (filename case mismatch), breaking ERC/netlist and corrupting parity coverage

Disposition: **duplicate**.

Location: Project hierarchy (root references 'VR Conditioner.kicad_sch'; checkout supplies 'VR conditioner.kicad_sch'); VR conditioner section on board near (133.8, 56.1) mm. Condition: The root schematic references a sheet filename whose case does not match the file present in the checkout. Both the ERC and netlist checks aborted as malformed ('hierarchy references missing sheet files'), and the native DRC parity pass reported exactly the VR-conditioner board components (U10 MAX9924, R25-R34, C23-C25, JP1 - 15 items) as 'Extra footprint' with no schematic counterpart..

On case-sensitive filesystems the sheet cannot be loaded, so the VR conditioner circuit is absent from the compiled netlist. Schematic-to-board equivalence for the VR input section (a README-advertised feature: dual VR crank/cam inputs) is unverifiable, and any netlist-driven update or verification run would treat those footprints as orphans, risking their removal or mis-netting.

Two independent native tools fail on the same missing-file reference (ERC/netlist abort; DRC parity misclassifies the entire VR section), so this is a real project-data integrity defect, not a tool artifact. The affected components carry real nets in the saved board (geometry shows U10 wired to /VR1+, +5V, GND), proving the section exists physically but is invisible to native verification. Major rather than blocking because the saved copper itself reports 0 unconnected items and the defect is a one-character rename fix, but it blocks all hierarchy-level verification until corrected.

Disposition reason: Same defect as circuit/F1-sheet-file-case-mismatch: identical condition (sheet filename case mismatch breaking ERC/netlist), identical mechanism (VR-conditioner section invisible to native verification), and identical evidence set (15 extra_footprint parity items, U10 MAX9924 geometry, console parity count). Content fully subsumed by the accepted canonical finding.
- [work/drc/drc.json](work/drc/drc.json): schematic_parity: 15x type=extra_footprint (C25 uuid 0070999b, U10 uuid 4ccdf03c, R27 uuid 4f184bcb, R28 uuid 40773cef, R29 uuid 667488c6, R25, R26, R30-R34, C23, C24, JP1)
- [work/geometry/geometry.json](work/geometry/geometry.json): footprint U10 value MAX9924 at (133.811, 56.081) with live nets (/VR1+, +5V, GND) - populated on board yet 'extra' per parity
- [work/drc/console.log](work/drc/console.log): line 3: 'Found 114 schematic parity issues'

### physical/F2: Saved board fails native DRC with 13 error-severity violations (5 padstack, 5 annular_width, 3 starved_thermal)

Disposition: **duplicate**.

Location: Polygonus-Universal-Base.kicad_pcb, saved copper/padstacks (specific items in truncated portion of drc.json). Condition: The native DRC run (kicad-cli 10.0.7, --severity-all, exit 0) reports 142 violations of which 13 are error severity. The by_type breakdown in the check record (padstack 5, annular_width 5, starved_thermal 3) sums exactly to the 13 errors..

Annular-width errors mean drilled holes whose remaining copper ring is below the board minimum, risking drill breakout and open/intermittent connections after fabrication. Padstack errors indicate invalid pad/via stack definitions that fabricators may interpret inconsistently. Starved thermals mean pads whose saved zone fill provides fewer thermal spokes than required, risking a high-resistance or mechanically weak pad-to-plane connection on what are typically power/ground pads.

A successful tool exit does not mean the board passed: the console and report counts show 13 error-class rule violations in the saved artifact. These categories are manufacturing-reliability defects rather than cosmetic warnings. I cannot localize the items because the drc.json excerpt was truncated before the violations of error severity, so the exact nets/positions remain a coverage gap, but the existence and classification of the errors is directly evidenced by the native run.

Disposition reason: Identical to circuit/F2-drc-error-violations and a subset of the accepted check/drc: same 13 error-severity violations (padstack 5, annular_width 5, starved_thermal 3) from the same kicad-cli run on the same saved board. Mechanism description (drill breakout, inconsistent padstacks, starved thermal reliefs) is sound but adds no separate defect beyond the failing DRC check it restates.
- [work/drc/console.log](work/drc/console.log): line 1: 'Found 142 violations'
- [work/drc/drc.json](work/drc/drc.json): check details: by_severity error=13; by_type padstack=5, annular_width=5, starved_thermal=3 (individual items beyond supplied 100000-byte prefix)
- [work/drc/execution.json](work/drc/execution.json): argv '--severity-all', exit_code 0, board Polygonus-Universal-Base.kicad_pcb

### physical/F3: Internal dual-wideband CAN2 bus has zero populated 120R termination on the base board (both termination resistors DNP)

Disposition: **duplicate**.

Location: wbo-can-trans sheet instances (R36/R37, 120R 1206 across CAN2+/CAN2-); board R36 at (175.01, 142.13) mm and R37 at (81.62, 140.55) mm. Condition: Both 120R termination resistors across /CAN2+ and /CAN2- are not populated: the schematic marks R36 '(dnp yes)', '(in_bom no)' with an 'n.p.' annotation (instance references R36 and R37 across the two sheet instances), and the board geometry lists R36 and R37 in dnp_refs with footprint R36 confirmed dnp:true spanning /CAN2+ to /CAN2-. The 47pF caps (C29/C30/C33/C34) on the bus are line-to-ground filters, not termination. README states both wideband O2 controllers are connected internally to CAN bus #2..

A high-speed CAN bus without ~60R differential loading suffers reflections and reduced noise margin; depending on bit rate and stub lengths this ranges from elevated error frames to total arbitration failure. The internal CAN2 segment carries the two onboard f042 wideband modules (U1/U2) plus transceiver U21, so wideband lambda data - a core advertised feature - depends on this bus. As saved, correct operation relies entirely on termination located on the plug-in wideband modules or in the external harness, neither of which is evidenced anywhere in the packet.

The DNP state is clearly deliberate ('n.p.' annotation), which usually indicates termination is expected elsewhere - plausibly CAN2 is also exposed externally ('Dual CAN') and terminated in the harness, or the mck1117 wideband modules carry their own termination option. But the review scope explicitly targets internal dual-wideband CAN termination, and nothing in the packet verifies any populated termination on this bus. Classified as a concern rather than a defect because module/harness-side termination cannot be ruled out from the supplied data; major because an unterminated bus would disable both wideband controllers, a headline feature.

Disposition reason: Same finding as circuit/F3-can2-termination-dnp: both DNP 120R terminations (R36/R37) across /CAN2+ and /CAN2- with intentional 'n.p.' annotation, same mechanism (unterminated bus reflections risking the internal wideband link), same concern-level framing conditional on unverifiable module/harness termination. Fully subsumed by the accepted canonical concern.
- [inputs/project/wbo-can-trans.kicad_sch](inputs/project/wbo-can-trans.kicad_sch): symbol R36 uuid d69380f9-5487-4782-9ce9-0690df62e543: value 120, (dnp yes), (in_bom no), instances R36/R37; text 'n.p.' uuid 98ed307b at (113.03, 76.2); wires tie its pins to the CAN2+ (y=83.82) and CAN2- (y=88.9) rails
- [work/geometry/geometry.json](work/geometry/geometry.json): dnp_refs includes R36, R37; footprint R36 dnp:true, pad 1 net /CAN2+, pad 2 net /CAN2- at (175.014, 142.129)
- [inputs/project/README.md](inputs/project/README.md): Inputs: 'Dual wideband O2 sensor controllers ... connected internally to CAN bus #2'

### physical/F4: Brain-module connector J2 footprint on board differs from footprint declared by its schematic symbol

Disposition: **duplicate**.

Location: Footprint J2 at (136.566, 99.212) mm. Condition: DRC parity reports J2 uses 'Simon:Polygonus-With-USB-SWD' while the symbol specifies 'fome_modules:polygonus', plus a BOM-exclusion attribute mismatch on the same footprint..

If the substitution is not a verified pin-compatible variant, any pad numbering or position difference between the two footprints would mis-route the 100+ module signals (the board's central interface). The 'Simon' library is also absent from the tool configuration, so the substituted footprint could not be checked against any library copy.

The name suggests a deliberate variant adding USB/SWD access, and the saved board reports 0 unconnected items, so the nets as saved are self-consistent. However, pin-map equivalence to the symbol-declared footprint is unverified and unverifiable from the packet (library missing from configuration). Minor concern: likely intentional, but it should be reconciled in the schematic to restore parity integrity.

Disposition reason: The J2 footprint substitution (Simon:Polygonus-With-USB-SWD vs symbol-declared fome_modules:polygonus, uuid f447e585) is the same parity item already carried by the accepted circuit/F4-footprint-library-hygiene, with the same minor-concern assessment and the same observation that the 'Simon' library is absent from configuration. No additional condition or mechanism beyond the canonical finding.
- [work/drc/drc.json](work/drc/drc.json): schematic_parity: footprint_symbol_mismatch, 'Simon:Polygonus-With-USB-SWD doesn't match footprint given by symbol (fome_modules:polygonus)', Footprint J2 uuid f447e585-df78-4239-b8cb-4653b3837bb1

### physical/F5: Widespread library drift: 106 footprints mismatch library copies and board-local 'Simon' library absent from configuration (9 items)

Disposition: **duplicate**.

Location: Board-wide (C/R/LED/connector footprints; P1, P3, P5, P6, J1, J4 and others reference missing 'Simon' library). Condition: DRC reports 106 lib_footprint_mismatch violations (footprints differ from their library copies) and 9 lib_footprint_issues ('configuration does not include the footprint library Simon')..

Saved copper cannot be faithfully regenerated or audited against libraries; a future footprint update-from-library would silently change pad geometry across the board, and the 'Simon' footprints (including the main module connector and power pads) have no library reference at all for verification.

These are warning-class native findings with no direct electrical consequence in the saved artifact, but they undermine reproducibility and future-maintenance safety of the board data. Concern (not defect) because the saved geometry itself is checked as-is and mismatches may reflect intentional local edits.

Disposition reason: The 106 lib_footprint_mismatch violations and 9 lib_footprint_issues ('Simon' library missing from configuration) are the same library-drift/hygiene population already cited and assessed in the accepted circuit/F4-footprint-library-hygiene (which explicitly covers the 106 mismatches, the missing Simon library, and the reproducibility/maintenance risk). Same evidence, same minor-concern calibration.
- [work/drc/drc.json](work/drc/drc.json): violations: 106x type=lib_footprint_mismatch (e.g. C25 uuid 0070999b, R35 uuid 1905dec1); 9x type=lib_footprint_issues (e.g. P1 uuid 1646febd, J1 uuid 93ba26da, J4 uuid a7443703)

### physical/F6: Hierarchical label CAN_RX shaped 'input' while driven by transceiver RXD output on the CAN2 sheet

Disposition: **duplicate**.

Location: inputs/project/wbo-can-trans.kicad_sch, hierarchical label CAN_RX at (204.47, 83.82). Condition: The CAN_RX hierarchical label has shape 'input' (signal entering the sheet) but is wired to U20/U21 pin 4 (RXD, an output pin) inside the sheet; CAN_TX is also shape 'input'..

Direction-shape mismatch on hierarchical labels is exactly the class of issue ERC flags (output pin driving an input-shaped sheet port); with ERC unable to run (F1), this latent warning is unverified. It does not alter the netlist, only connectivity-intent documentation and ERC cleanliness.

Verified by tracing the sheet wiring: the RXD output reaches the input-shaped port directly. Netlist connectivity is unaffected (labels connect by name regardless of shape), so this is hygiene/ERC debt rather than an electrical fault; flagged because ERC coverage is currently zero and this would otherwise be caught there.

Disposition reason: Same hierarchical-label direction mismatch as circuit/F5-canrx-label-direction: CAN_RX (shape input, uuid 0cfcbd19) driven by U20/U21 RXD output pin 4 via wire (179.07,83.82)-(204.47,83.82). Same mechanism (ERC hygiene, no netlist impact) and same contextual relevance (ERC currently cannot run). Severity difference (minor vs info) does not alter the identical underlying issue; subsumed by the accepted canonical finding.
- [inputs/project/wbo-can-trans.kicad_sch](inputs/project/wbo-can-trans.kicad_sch): hierarchical_label 'CAN_RX' uuid 0cfcbd19 (shape input) at (204.47, 83.82); wire (179.07, 83.82)-(204.47, 83.82) to U20 RXD pin 4 (pin type output, symbol TJA1051T-3 mirrored at 166.37, 86.36)

### physical/F7: 14 silkscreen-clipped-by-solder-mask violations around connector legends

Disposition: **duplicate**.

Location: F.Silkscreen circles/rectangles of J6-J12, U2 overlapping PTH/SMD pads of J2 and C33 (e.g. (112.075, 56.315), (80.868, 137.564)). Condition: Native DRC reports 14 silk_over_copper warnings where silkscreen graphics fall on mask-opened pad areas..

Silkscreen over exposed pads is clipped at fabrication, degrading legend legibility; no electrical effect.

Confirmed native violations in the saved artifact, hence a defect, but purely cosmetic: fabricators auto-clip silk from mask openings. Recorded at info severity for completeness of the native-rule coverage claim.

Disposition reason: The 14 silk_over_copper warnings are individually visible in the included drc.json prefix (e.g. Circle of J12 vs PTH pad 104 [/EXT_SPI_SCK] of J2 uuid cb75ea42; Rectangle of U2 vs Pad 2 [/CAN2+] of C33 uuid 2995fbe4) and are a warning-severity subset of the 142 violations reported by the accepted check/drc from the same tool run. The cosmetic, info-level characterization is accurate, but the finding adds no defect beyond the failing DRC check it itemizes, so it is linked to that accepted canonical finding.
- [work/drc/drc.json](work/drc/drc.json): violations: 14x type=silk_over_copper (e.g. Circle of J12 on F.Silkscreen vs PTH pad 104 [/EXT_SPI_SCK] of J2, uuid cb75ea42; Rectangle of U2 vs Pad 2 [/CAN2+] of C33, uuid 2995fbe4)

## Limitations

- Donor carrier remains a modular design; integrated FOME-MC coverage is not established by this donor review.
- Scope omits full automotive transients, ignition energy, wideband control stability, EMC and thermal qualification.
- Saved copper is checked without refilling or changing the board.
- The donor root references VR Conditioner.kicad_sch but the checkout supplies VR conditioner.kicad_sch. ERC/netlist hierarchy coverage is incomplete; native DRC parity counts cannot establish full hierarchy equivalence.
- text artifact partially supplied: work/geometry/geometry.json (100000/1657568 bytes)
- text artifact partially supplied: work/drc/drc.json (100000/141827 bytes)

This result applies only to the captured candidate and declared criteria. Prototype and bench
validation, including operating conditions omitted from this review, remain necessary.

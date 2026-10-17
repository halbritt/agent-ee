# Universal 112 disposable hierarchy alias review review

Outcome: **defects_or_unresolved**; process state: `complete`.

Candidate: `69119fab9d25319b3bf376eca634b13104ba76b7417416b85a2a64a4e78a6ab4`
Run: `bafd7961-39b9-4a34-9b39-b7f108e82f07`

Review the saved Universal 112 donor with an explicit disposable sheet-path alias, native rule coverage, saved-board implementation, and internal dual-wideband CAN termination. Original as-received snapshot is reported separately. No source circuit redesign or manufacturing approval.

## Checks

| Check | Execution | Design result | Summary |
| --- | --- | --- | --- |
| erc | ok | defect | ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5} |
| drc | ok | defect | DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s) |
| netlist | ok | info | native XML netlist exported from a complete 10-instance hierarchy: 139 component(s), 199 net(s), 10 DNP |
| geometry | ok | info | saved-board geometry extracted (no declared geometric requirement applied) |

## Initial reviews

- circuit: ok; requested model gpt-6.1-sol.
- physical: ok; requested model gpt-6.1-sol.

circuit coverage: Read complete local ERC, DRC, netlist and geometry records and relevant native schematics and saved PCB.; Independently exported all 10 sheet instances: 139 components and 199 nets. Connectivity matches the supplied netlist; native PCB pad net assignments match.; Verified the disposable VR sheet alias is byte-identical to the lowercase source.; Traced CAN2, added lowsides, stepper controls, VR interfaces, ignition connections and ETB disable bias; checked selected component identities and manufacturer limits.; Reviewed native rule violations without treating successful execution or zero unconnected items as electrical validation. Commands, references, calculations and limitations are recorded in review-work/circuit/experiments/review-notes.md.
- No bench measurements or verified dynamic device simulations; no startup, shutdown, reset or brownout waveforms were validated.
- Exact Polygonus/Atlas and wideband module revisions, firmware configuration and harness termination were not established.
- No CAN timing/signal-integrity tests, sensor amplitude/frequency sweeps, exhaustive component-rating checks or load qualification.
- Automotive transients, ignition energy, wideband-loop stability, EMC, thermal qualification and integrated FOME-MC coverage remain omitted.
- Saved copper was not refilled or modified. Original as-received checkout and manufacturing approval are outside this review.

physical coverage: Read full native PCB, relevant schematic sources, project rules, and supplied DRC/ERC/netlist/geometry records.; Reproduced configured DRC: 142 violations, 114 parity issues, zero unconnected items; all 13 error-level results are excluded.; Independently compared 659 schematic pin-to-net assignments against PCB pads: no missing pins or net-name mismatches.; Measured connector hole annuli and exported separate plated/unplated drill files.; Inspected saved ground copper and routed connections at all three starved thermals.; Checked CAN2 termination population, connectivity, and ideal resistance calculations; sampled power, heater, and ground routing geometry.
- Saved copper only; no refill or candidate modification. Commands, versions, configuration differences, assumptions, and outcomes are recorded in review-work/physical/experiments/review-notes.md.
- No hardware resistance, CAN traffic, waveform, cable, rise-time, bus-rate, or host-module population testing.
- No component pinout audit, 3D fit, module standoff clearance, fabrication-tolerance qualification, or manufacturing approval.
- No ampacity, simultaneous-load, fuse-coordination, thermal, automotive-transient, ignition-energy, wideband-stability, or EMC qualification; widths and via counts do not establish these.
- Ignored courtyard checks and the configured zero copper-edge minimum limit mechanical and clearance coverage. Integrated FOME-MC and the separate original as-received checkout were not reviewed.

## Findings and dispositions

### check/erc: ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

Disposition: **accepted**.

Location: erc. Condition: check erc (erc).

ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): The declared ERC criterion fails. Independent execution reproduces all 353 violations, including five errors. These establish native rule/model-intent defects, without independently proving missing physical supplies or shorted regulators. Successful execution is not a pass.
- [work/erc/execution.json](<work/erc/execution.json>): check:erc
- [work/erc/console.log](<work/erc/console.log>): check:erc
- [work/erc/erc.json](<work/erc/erc.json>): check:erc

### check/drc: DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition: **accepted**.

Location: drc. Condition: check drc (drc).

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): The declared DRC criterion fails. Independent execution reproduces 142 violations and 114 parity issues, including 13 excluded error-level violations. Exclusions and zero unconnected items do not resolve the failing geometric rules.
- [work/drc/execution.json](<work/drc/execution.json>): check:drc
- [work/drc/console.log](<work/drc/console.log>): check:drc
- [work/drc/drc.json](<work/drc/drc.json>): check:drc

### circuit/circuit-001: Eight lowside switches have conflicting component identities

Disposition: **accepted**.

Location: U11–U14/U16–U19 component identification. Condition: Selecting components for populated U11–U14 and U16–U19 from their native fields..

The circuit value identifies VNL5090 while the procurement code identifies VNL5050N3TR-E.

The conflicting identities exist in both schematic and saved PCB. The [SKU owner identifies C2680415 as VNL5050N3TR-E](https://www.lcsc.com/product-detail/C2680415.html). No alternate-part declaration was located. This is a definite identification defect; electrical incompatibility between those parts is not established here.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Root engineering reconciliation: component identity is explicitly within the review scope. Native schematic and PCB value VNL5090 conflict with the purchasing code C2680415, which the retained SKU-owner page identifies as VNL5050N3TR-E. No documented alternate-part decision resolves which part the saved design specifies. Accept the identification inconsistency; do not claim electrical incompatibility or physical failure from it.
- [inputs/project/Lowside Adder.kicad_sch](<inputs/project/Lowside Adder.kicad_sch>): U11 Value VNL5090 and LCSC C2680415, lines 2541–2583; repeated on U12–U14 and the second sheet instance
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): U11 footprint UUID 5160b79e-ac90-4a32-befc-c61fcdad3f06, Value and LCSC properties; corresponding U12–U19 footprints
- [review-work/circuit/experiments/vnl-reference-web.txt](<review-work/circuit/experiments/vnl-reference-web.txt>): LCSC product identification: MPN VNL5050N3TR-E, stock code C2680415

### circuit/circuit-002: Added lowside drive lacks demonstrated margin for 3.3 V GPIOs

Disposition: **deferred**.

Location: Added lowside input drive, U11–U14/U16–U19. Condition: Using J2's MCU passthrough controls with 3.3 V GPIO high levels; the exact mated module revision is unspecified..

There is no level conversion between the GPIO controls and the SOT-223 switches. A 3.3 V source provides at most 3.3 V before series-resistor drop, below the N3 device's 3.5 V minimum control supply.

Both possible N3 identities have this limitation; the [manufacturer specifications](https://www.st.com/resource/en/datasheet/vnl5050s5-e.pdf) do not establish guaranteed operation at 3.3 V. R1/R2 also produce an unloaded 2.5 V LS9/10 bias when GPIOs float, leaving reset behavior unverified. Exact module GPIO levels and switch waveforms are absent, so this remains a compatibility concern rather than demonstrated output failure.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): The conditional mechanism is supported: native series drive from a 3.3 V GPIO cannot guarantee the N3 input/supply's 3.5 V minimum, supported by both [VNL5090](https://www.st.com/resource/en/datasheet/vnl5090n3-e.pdf) and [VNL5050](https://www.st.com/resource/en/datasheet/vnl5050s5-e.pdf) specifications. Actual mated-module GPIO levels and reset behavior remain unspecified. The unloaded 2.5 V bias does not predict switch state. Preserve this major compatibility uncertainty pending actual module-drive evidence.
- [inputs/project/Lowside Adder.kicad_sch](<inputs/project/Lowside Adder.kicad_sch>): RN1/RN2 and VNL5090 input wiring; RN3/RN4 in the second sheet instance
- [review-work/circuit/experiments/audit.json](<review-work/circuit/experiments/audit.json>): selected_nets and selected_board_footprints: GPIO controls connect through 1 kohm branches to the eight switch inputs
- [review-work/circuit/experiments/polygonus-readme.md](<review-work/circuit/experiments/polygonus-readme.md>): Outputs section and J2-equivalent pins 12–15, 24–27: MCU passthrough controls
- [review-work/circuit/experiments/vnl5050-primary-web.txt](<review-work/circuit/experiments/vnl5050-primary-web.txt>): Table 8: N3 input doubles as supply; input threshold maximum 3.5 V
- [review-work/circuit/experiments/calculations.json](<review-work/circuit/experiments/calculations.json>): lowside_GPIO_3V3_case and LS9_LS10_GPIO_highZ

### circuit/circuit-003: Internal CAN2 termination depends on an undocumented external configuration

Disposition: **deferred**.

Location: Internal CAN2 termination, R36/R37. Condition: Operating the internally connected dual-wideband CAN2 bus with the saved population settings..

Both local termination resistors are omitted. The transceiver input resistances do not provide equivalent termination.

The saved carrier supplies no populated resistor directly across CAN2. Termination could exist in the mated brain module or external harness, but neither configuration is established. Consequently, adequate termination for internal wideband communication is unresolved; no CAN waveform or communication failure is claimed.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Both native carrier shunts are DNP, leaving no populated resistive carrier termination. The [TJA1051 input resistance](https://www.nxp.com/docs/en/data-sheet/TJA1051.pdf) is not equivalent termination. However, module/harness loading remains unspecified, and neither a standalone-carrier termination requirement nor assembled-bus failure is established. Preserve the major uncertainty pending the actual termination configuration and operating evidence.
- [inputs/project/wbo-can-trans.kicad_sch](<inputs/project/wbo-can-trans.kicad_sch>): Resistor UUID d69380f9-5487-4782-9ce9-0690df62e543: 120 ohm, DNP yes, instances R36/R37
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): R36 UUID 6924b9c0-bd5f-481d-9f8d-7fc20978c5ab and R37 UUID 7f8c8af6-f455-43aa-9bef-892e252a308e: DNP attributes
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets /CAN2+ and /CAN2-: U20/U21, R36/R37, J2.73/74 and J1.15/11
- [review-work/circuit/experiments/audit.json](<review-work/circuit/experiments/audit.json>): can2_direct_resistors and selected_board_footprints.R36/R37
- [review-work/circuit/experiments/tja1051.txt](<review-work/circuit/experiments/tja1051.txt>): Table 7, Ri(dif): differential input resistance 19–52 kohm

### physical/physical-001: Five plated J1 holes have zero annular copper

Disposition: **accepted**.

Location: J1 unnumbered holes at (71.95,35.49), (75.55,39.34), (120,28.49), (199.65,39.34), and (203.25,35.49) mm. Condition: Five unnumbered J1 holes are declared plated through holes with copper diameter equal to drill diameter: three at 4.0 mm and two at 2.2 mm..

Annulus = (pad diameter − drill diameter)/2 = 0 mm, below the configured 0.1 mm minimum. Drill export preserves their plated classification.

This is a reproducible native padstack/rule violation, despite its exclusion. Whether these were intended as unplated locating holes remains unresolved; the evidence does not establish connector fit failure or fabrication rejection.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Established native padstack/annular-rule defect: all five plated J1 holes have equal pad and drill diameters, giving zero annulus against the configured 0.1 mm minimum. Independent DRC and plated drill export confirm the condition. Exclusions do not resolve it; connector-fit failure and fabrication rejection remain unproven.
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): Lines 21686–21725; five unnumbered thru_hole pads
- [inputs/project/Polygonus-Universal-Base.kicad_pro](<inputs/project/Polygonus-Universal-Base.kicad_pro>): board.design_settings.rules.min_via_annular_width = 0.1; drc_exclusions contains these holes
- [work/drc/drc.json](<work/drc/drc.json>): violations[132:142]: padstack and annular_width, excluded=true
- [review-work/physical/experiments/physical-measurements.json](<review-work/physical/experiments/physical-measurements.json>): j1_mechanical_holes; radial_annulus_mm = 0 for all five
- [review-work/physical/experiments/drills/Polygonus-Universal-Base-PTH.drl](<review-work/physical/experiments/drills/Polygonus-Universal-Base-PTH.drl>): Lines 1564–1573; T10 and T12 emit all five holes as plated

### physical/physical-002: Three saved ground reliefs violate the spoke minimum

Disposition: **accepted**.

Location: C15.2 (107.851547,98.755769), U7.4 (101.008268,102.683572), C14.2 (101.591948,104.291543) mm. Condition: Saved F.Cu ground reliefs at C15.2, U7.4, and C14.2 each resolve one spoke against a configured minimum of two..

The saved zone connections do not satisfy the native minimum spoke count; exclusions suppress disposition without changing copper.

Independent DRC reproduces all three violations. Each pad also has a routed ground connection toward a nearby via, so these findings establish a geometric rule violation, not an open ground or demonstrated thermal failure.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Established saved-copper rule defect: each specified pad resolves one thermal spoke against the configured minimum of two. Independent DRC reproduces all three. Exclusions do not alter copper or resolve the criterion. Routed ground connections limit the conclusion to the spoke-rule violation; no open ground or thermal failure is established.
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): GND zone UUID bc540b8b-3ffa-4b00-b211-3a91191a1f6d, line 69477 onward; saved filled polygons
- [inputs/project/Polygonus-Universal-Base.kicad_pro](<inputs/project/Polygonus-Universal-Base.kicad_pro>): Lines 263–273: exclusions; board.design_settings.rules.min_resolved_spokes = 2
- [work/drc/drc.json](<work/drc/drc.json>): violations[94:97]: starved_thermal, actual 1, minimum 2, excluded=true
- [review-work/physical/experiments/physical-measurements.json](<review-work/physical/experiments/physical-measurements.json>): thermal_pads: pad coordinates, connected 0.25 mm traces, and nearby ground vias
- [review-work/physical/experiments/saved-thermal-copper.png](<review-work/physical/experiments/saved-thermal-copper.png>): Three saved F.Cu detail panels

### physical/physical-003: Internal CAN2 termination remains unestablished

Disposition: **duplicate**.

Location: Internal dual-wideband CAN2 network, R36/R37 and J2.73/J2.74. Condition: Both carrier CAN2 termination options, R36 and R37, are 120 ohms but DNP and excluded from assembly outputs..

The saved carrier population contributes no resistive termination. Host-module or harness termination therefore determines the assembled bus loading.

The native population is clear, but host/harness termination and electrical behavior are unverified. Ideal carrier-only alternatives are 120 ohms with one resistor and 60 ohms with both. Continuity checks cannot establish appropriate loading or waveform quality, so a communication failure is not asserted.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Same DNP termination population and unknown module/harness loading as circuit/circuit-003. Its supported observation and unresolved assembled-bus implications survive in that canonical deferred concern.
- [inputs/project/wbo-can-trans.kicad_sch](<inputs/project/wbo-can-trans.kicad_sch>): Lines 2665–2675; resistor UUID d69380f9-5487-4782-9ce9-0690df62e543; R36/R37 instances
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): R36 at lines 14742–14810; R37 at lines 18665–18733; DNP attributes
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets /CAN2+ and /CAN2-; components R36 and R37
- [review-work/physical/experiments/termination-calculation.json](<review-work/physical/experiments/termination-calculation.json>): CAN2_carrier_termination_cases and stated exclusions

## Limitations

- Donor carrier remains a modular design; integrated FOME-MC coverage is not established by this donor review.
- Scope omits full automotive transients, ignition energy, wideband control stability, EMC and thermal qualification.
- Saved copper is checked without refilling or changing the board.
- This disposable candidate adds VR Conditioner.kicad_sch as a byte copy of the existing lowercase VR conditioner.kicad_sch. The original checkout remains unchanged, and its filename discrepancy remains open there.
- text artifact partially supplied: work/geometry/geometry.json (100000/1657568 bytes)
- text artifact partially supplied: work/drc/drc.json (100000/142607 bytes)
- text artifact partially supplied: work/erc/erc.json (100000/226344 bytes)
- text artifact partially supplied: work/netlist/design.xml (84840/252017 bytes)
- text artifact partially supplied: work/geometry/execution.json (0/2144 bytes)
- text artifact partially supplied: work/geometry/console.log (0/446 bytes)

This result applies only to the captured candidate and declared criteria. Prototype and bench
validation, including operating conditions omitted from this review, remain necessary.

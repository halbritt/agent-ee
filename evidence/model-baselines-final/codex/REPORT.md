# Universal 112 disposable hierarchy alias review review

Outcome: **defects_or_unresolved**; process state: `complete`.

Candidate: `69119fab9d25319b3bf376eca634b13104ba76b7417416b85a2a64a4e78a6ab4`
Run: `f3c237c2-3a08-4617-aaba-f04c47bbe98f`

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

circuit coverage: Inspected native circuit symbols, exported connectivity, full ERC/DRC records and saved-board pad assignments. Compared 694 numbered pads against the schematic netlist: no net-name mismatches.; Checked CAN transceiver pin identities and nominal supplies, MAX9924 default mode, MAX9142 comparator pin compatibility, added lowside interfaces and TLE9201 input defaults.; Confirmed R36/R37 are both 120 ohm, DNP and excluded from the BOM in the native schematic and saved board.; Verified the disposable VR sheet alias is byte-identical to the lowercase source; saved-board hash matches the packet.; Performed an analytical and ngspice resistor-only DC check of undriven LS9/LS10 bias. Commands, references, assumptions and limitations are recorded in review-work/circuit/experiments/review_log.md and driver_analysis.md.
- Actual ECU module revision, fitted parts and firmware were not supplied. Retrieved upstream module sources are pinned references, not proof of the assembled module.
- Internal CAN2 end-to-end termination and communication remain unverified: both base terminators are DNP; a reference Polygonus implementation contains one 120-ohm terminator. No mandatory short-bus topology, harness termination or waveform measurements were supplied, so no deterministic CAN failure is alleged.
- No validated active-device simulation or hardware measurements covered startup, shutdown, brownout, VR response, ignition switching or actuator behavior. The added simulation proves passive DC bias only.
- Omitted full rail-current budgets, automotive transients, ignition energy, wideband stability, EMC, thermal qualification, Atlas internals and integrated FOME-MC coverage.
- Physical manufacturing/layout adjudication and individual disposition of every native warning were omitted. Saved copper was not refilled or modified; successful check exits and matching net names do not establish electrical behavior.

physical coverage: Read complete native DRC, ERC, netlist and geometry records; inspected saved PCB and project constraints/exclusions.; Independently reran DRC without refilling copper: reproduced the 13 excluded error entries and zero unconnected items.; Compared 694 numbered PCB pad instances against native XML net assignments; found no mismatches.; Measured J1 mounting holes and generated separate plated/unplated drill files.; Inspected three flagged ground reliefs and their additional trace connections; checked CAN2 connectivity, DNP termination population and selected power/return geometry. Commands, versions and results are recorded in review-work/physical/experiments/README.md.
- Saved fills only; no refill comparison, fabricated-board continuity test, powered measurement or behavioral simulation.
- R36 and R37 are both 120-ohm DNP. Module/harness termination, assembled CAN2 resistance and dynamic CAN operation were not established.
- No ampacity, load simultaneity, fault coordination, temperature-rise, junction-temperature or cooling qualification; width alone was not treated as qualification.
- Connector drawings, fabrication tolerances, finished-hole fit, complete land-pattern validation and 3D module/enclosure clearances were not verified.
- The independent DRC environment had different library resolution; geometric error entries reproduced, while library-warning counts differed.
- Not every ERC warning received component-specific adjudication. Automotive transients, ignition energy, wideband stability, EMC and integrated FOME-MC coverage remain omitted.

## Findings and dispositions

### check/erc: ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

Disposition: **accepted**.

Location: erc. Condition: check erc (erc).

ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): The declared ERC criterion fails: 353 violations across ten sheet instances, including five unexcluded errors. Exit zero establishes execution, not compliance. Accept as a native-check defect; the power-pin annotations do not independently establish absent supplies or regulator contention.
- [work/erc/execution.json](<work/erc/execution.json>): check:erc
- [work/erc/console.log](<work/erc/console.log>): check:erc
- [work/erc/erc.json](<work/erc/erc.json>): check:erc

### check/drc: DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition: **accepted**.

Location: drc. Condition: check drc (drc).

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): The declared DRC criterion fails: 142 board violations and 114 parity issues remain. All 13 severity-error entries have saved exclusions; 94 board warnings remain unexcluded. Preserve those exceptions without claiming unwaived manufacturing failures. Zero unconnected items and matching numbered-pad nets do not establish rule/parity compliance. Saved copper was not refilled.
- [work/drc/execution.json](<work/drc/execution.json>): check:drc
- [work/drc/console.log](<work/drc/console.log>): check:drc
- [work/drc/drc.json](<work/drc/drc.json>): check:drc

### circuit/circuit-001: Added lowside drivers receive less than their minimum operating drive

Disposition: **deferred**.

Location: Both Lowside Adder instances: U11–U14/U16–U19 and RN2/RN4. Condition: Added lowside outputs commanded high through the documented Polygonus 3.3 V MCU interface..

The SOT-223 N3 devices internally connect INPUT to their control supply. Their specified operating minimum is 3.5 V, while these channels receive at most nominal 3.3 V through series resistors.

Even ideal GPIO drive has 3.3−3.5 = −0.2 V operating-range margin before output and resistor drops. Both the named [VNL5090N3](https://www.st.com/resource/en/datasheet/vnl5090n3-e.pdf) and purchasing-selected [VNL5050N3](https://www.st.com/resource/en/datasheet/vnl5050n3-e.pdf) have this limitation. Turn-on and specified on-resistance are therefore unsupported by the interface. Actual loaded switching was not measured.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): The native series drive and ST N3 input/supply minimum support the conditional calculation: an ideal 3.3 V GPIO is 0.2 V below 3.5 V before resistor drop. The retrieved upstream module is reference evidence, not proof of the exact mated assembly. Preserve the claimed defect and defer applicability pending module revision and actual GPIO levels; no actual switch failure or damage is established.
- [inputs/project/Lowside Adder.kicad_sch](<inputs/project/Lowside Adder.kicad_sch>): U11–U14 INPUT pins; RN2 1k series branches; repeated as U16–U19/RN4
- [inputs/project/Polygonus-Universal-Base.kicad_sch](<inputs/project/Polygonus-Universal-Base.kicad_sch>): J2 pins 12–15 and 24–27
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): U11–U14 and U16–U19 SOT-223 footprints, pad 1 connections
- [review-work/circuit/experiments/Polygonus_mcu_interface.json](<review-work/circuit/experiments/Polygonus_mcu_interface.json>): LS9–LS12 and IGN9–IGN12 MCU nets; U26 VDD connected to +3V3
- [review-work/circuit/experiments/references/VNL5050_supplier.pdf](<review-work/circuit/experiments/references/VNL5050_supplier.pdf>): ST DocID15917 Rev6, Tables 2, 6 and 8
- [review-work/circuit/experiments/references/web_driver_sources.json](<review-work/circuit/experiments/references/web_driver_sources.json>): ST DS8791 Rev10, Tables 2, 6 and 8
- [review-work/circuit/experiments/driver_analysis.md](<review-work/circuit/experiments/driver_analysis.md>): Section 1: drive-voltage bound and operating-range calculation

### circuit/circuit-002: Undriven LS9/LS10 have an uncertain switch state

Disposition: **deferred**.

Location: LS9/LS10: R1/R2, RN1/RN2 and U11/U12. Condition: +5V is present while J2.24 and J2.25 are undriven, such as before MCU output initialization..

Each populated 1k pullup opposes a 1k pulldown, producing a nominal unloaded 2.5 V input bias instead of a defined low state.

The passive calculation and restricted simulation agree: 5×1k/(1k+1k)=2.5 V, with 1.5k Thevenin resistance at the driver input. This lies within the N3 devices’ specified 1–3.5 V threshold interval, so unwanted conduction cannot be excluded. Driver loading, initialization timing and actuator response remain unmeasured; an actual startup actuation is not established.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Native +5 V R1/R2 pullups and RN1 pulldowns are each 1 kohm; unloaded bias is 2.5 V with 1.5 kohm driver Thevenin resistance including RN2. The ngspice deck proves that passive calculation only. Actual switch conduction/reset actuation depends on input loading and module sequencing and remains unresolved.
- [inputs/project/Polygonus-Universal-Base.kicad_sch](<inputs/project/Polygonus-Universal-Base.kicad_sch>): R2 at line 20109 and R1 at line 20669, both 1k pullups
- [inputs/project/Lowside Adder.kicad_sch](<inputs/project/Lowside Adder.kicad_sch>): RN1 1k pulldowns, RN2 1k series branches, U11/U12 INPUT pins
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): Nets /LS9, /LS10 and Net-(RN2-R4.2)/Net-(RN2-R3.2)
- [review-work/circuit/experiments/reset_bias.cir](<review-work/circuit/experiments/reset_bias.cir>): Explicit ideal-resistor model and unloaded-input assumptions
- [review-work/circuit/experiments/reset_bias.log](<review-work/circuit/experiments/reset_bias.log>): Operating-point results: control and driver both 2.500000 V
- [review-work/circuit/experiments/driver_analysis.md](<review-work/circuit/experiments/driver_analysis.md>): Section 2: Thevenin calculation and uncertainty

### circuit/circuit-003: Lowside driver values conflict with their purchasing code

Disposition: **accepted**.

Location: U11–U14 and U16–U19 component identity fields. Condition: Driver identity is obtained from the saved value and purchasing fields..

The value identifies VNL5090, while the purchasing code selects VNL5050N3TR-E.

The [supplier’s C2680415 record](https://www.lcsc.com/product-detail/C2680415.html) identifies a different device from the schematic value. These devices have different published resistance and protection characteristics, leaving the intended fitted identity ambiguous. No harmful substitution outcome is asserted.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): The saved VNL5090 value and LCSC C2680415 purchasing field identify different manufacturer parts. The retained original supplier product page maps that SKU to VNL5050N3TR-E. Accept this component-definition ambiguity within the declared component-identity review scope; a harmful electrical substitution is not established.
- [inputs/project/Lowside Adder.kicad_sch](<inputs/project/Lowside Adder.kicad_sch>): U11 value VNL5090 and LCSC C2680415, lines 2541–2583; repeated on the other driver symbols
- [review-work/circuit/experiments/references/LCSC_C2680415_product.json](<review-work/circuit/experiments/references/LCSC_C2680415_product.json>): SKU C2680415 maps to MPN VNL5050N3TR-E
- [review-work/circuit/experiments/references/LCSC_C2680415_cn.html](<review-work/circuit/experiments/references/LCSC_C2680415_cn.html>): Original supplier product record

### physical/physical-001: J1 mounting holes retain zero-annulus PTH geometry

Disposition: **noted**.

Location: J1 mounting holes at (71.95,35.49), (75.55,39.34), (120.00,28.49), (199.65,39.34), (203.25,35.49) mm. Condition: Direct drill export of the saved PCB places five anonymous J1 mounting holes in the plated-through-hole file..

Each circular pad diameter equals its drill diameter: 4.0/4.0 mm or 2.2/2.2 mm. Nominal annulus (pad minus drill diameter)/2 is therefore zero, despite the plated drill classification.

Native geometry, DRC and independent drill export agree on the zero-annulus PTH representation. The saved exclusions acknowledge these violations but provide no rationale. Plating intent and finished-hole fit remain unverified. These are no-net mounting holes; electrical failure or fabrication rejection is not established.

Disposition reason: Root agent evidence reconciliation (separate from the rejected automatic adjudication): Confirmed five no-net mounting holes with pad diameter equal to drill diameter, exported as PTH. Preserve the original minor concern: the native 0.1 mm annular limit has explicit saved per-hole exclusions. No separate unwaived plating/finished-fit requirement or demonstrated electrical/fabrication failure establishes an additional defect. Raw rule findings remain under accepted check/drc; exclusions are not repairs or manufacturing approval.
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): Lines 21687–21726: five anonymous thru_hole pads; UUIDs e186b75f-4739-4f2e-a723-906e090de7da, d33d29a3-4924-49dd-a841-0d17124fc788, e5984f98-dc19-4bdf-ab93-d568639f9b10, 233888a2-e331-456b-963a-5ee5605edf83, 0b0fdd3c-c35f-4f2f-9520-66ae58d48bc0
- [inputs/project/Polygonus-Universal-Base.kicad_pro](<inputs/project/Polygonus-Universal-Base.kicad_pro>): board.design_settings.rules.min_via_annular_width = 0.1; corresponding annular_width and padstack exclusions
- [work/drc/drc.json](<work/drc/drc.json>): /violations/132 through /violations/141: five padstack and five annular_width entries, all excluded=true
- [review-work/physical/experiments/board-measurements.json](<review-work/physical/experiments/board-measurements.json>): mechanical_holes: positions, diameters and nominal_annulus_mm calculations
- [review-work/physical/experiments/drill/Polygonus-Universal-Base-PTH.drl](<review-work/physical/experiments/drill/Polygonus-Universal-Base-PTH.drl>): T10 and T12 drill hits: two 2.2 mm holes and three 4.0 mm holes
- [review-work/physical/experiments/drill-report.txt](<review-work/physical/experiments/drill-report.txt>): Plated hole tool counts; total unplated holes count 0

## Limitations

- Donor carrier remains a modular design; integrated FOME-MC coverage is not established by this donor review.
- Scope omits full automotive transients, ignition energy, wideband control stability, EMC and thermal qualification.
- Saved copper is checked without refilling or changing the board.
- This disposable candidate adds VR Conditioner.kicad_sch as a byte copy of the existing lowercase VR conditioner.kicad_sch. The original checkout remains unchanged, and its filename discrepancy remains open there.
- text artifact partially supplied: work/geometry/geometry.json (100000/1657568 bytes)
- text artifact partially supplied: work/drc/drc.json (100000/142607 bytes)
- text artifact partially supplied: work/erc/erc.json (100000/226344 bytes)
- text artifact partially supplied: work/netlist/design.xml (84808/252017 bytes)
- text artifact partially supplied: work/geometry/execution.json (0/2154 bytes)
- text artifact partially supplied: work/geometry/console.log (0/446 bytes)

This result applies only to the captured candidate and declared criteria. Prototype and bench
validation, including operating conditions omitted from this review, remain necessary.

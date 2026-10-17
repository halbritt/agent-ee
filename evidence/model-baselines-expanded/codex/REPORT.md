# Universal 112 disposable hierarchy alias review review

Outcome: **defects_or_unresolved**; process state: `complete`.

Candidate: `69119fab9d25319b3bf376eca634b13104ba76b7417416b85a2a64a4e78a6ab4`
Run: `c91cb6bf-3f83-487a-885c-c8f087d4b1af`

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

circuit coverage: Read native schematics and full ERC, DRC, netlist and geometry records.; Independently exported the complete 10-instance hierarchy; all 199 nets match the supplied connectivity.; Confirmed the VR sheet alias is byte-identical and reproduced all 353 ERC violations using the supplied library configuration.; Traced CAN transceiver supplies, signals and termination population; examined ETB disable bias, stepper controls, VR circuitry and shared ignition connections against saved-board pad assignments.
- No bench measurements or validated functional simulations; supplied checks contain no electrical stimulus or waveform measurements.
- Manufacturer-reference retrieval failed because DNS was unavailable; independent device pinout, threshold and rating validation remains incomplete.
- Brain-module internals, harness termination, firmware reset states, startup, shutdown and brownout behavior were not established. LS9/LS10 divider calculations alone did not establish a threshold violation.
- Automotive transients, ignition energy, motor loads, wideband stability, EMC and thermal qualification remain omitted. Copper was evaluated as saved without refill; DRC records were inspected rather than rerun.
- Integrated FOME-MC coverage, manufacturing approval and the original as-received filename discrepancy are outside this review. Commands, versions, assumptions and outcomes are recorded in review-work/circuit/experiments/review-notes.md.

physical coverage: Read complete native check records and independently inspected saved PCB geometry, stackup, zones, selected power/return paths and CAN2 routing.; Independent DRC reproduced all 142 supplied violations, zero unconnected items and 114 parity issues without refilling.; Compared all 659 schematic pin assignments with saved PCB assignments; no discrepancies found.; Generated separate plated/unplated drill outputs and calculated connector-hole annuli.; Inspected three excluded ground-thermal violations; direct routed ground connections prevent interpreting these as demonstrated opens.
- Saved copper only; no refill comparison.
- No powered CAN testing, module/harness inspection, bitrate or edge-rate stimulus, impedance extraction or waveform measurement.
- No load/duty/ambient thermal analysis, ampacity qualification, fuse coordination or ignition-energy testing; widths were not treated as ratings.
- No complete 3D clearance, mating-tolerance, stencil or fabrication-capability assessment; native courtyard coverage is partly disabled.
- No automotive transient, EMC, wideband-stability or integrated FOME-MC qualification.
- ERC records inspected but not independently rerun or fully adjudicated electrically.
- Manufacturer-reference retrieval failed DNS; no external tolerance or device model was assumed. Commands, outcomes and assumptions are recorded in review-work/physical/experiments/review-notes.md.

## Findings and dispositions

### check/erc: ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

Disposition: **accepted**.

Location: erc. Condition: check erc (erc).

ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

Disposition reason: The declared ERC criterion fails: independent execution reproduces all 353 records, including five native declaration errors. Exit code zero does not constitute a passing check. These errors do not independently establish missing physical power or regulator contention.
- [work/erc/execution.json](<work/erc/execution.json>): check:erc
- [work/erc/console.log](<work/erc/console.log>): check:erc
- [work/erc/erc.json](<work/erc/erc.json>): check:erc

### check/drc: DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition: **accepted**.

Location: drc. Condition: check drc (drc).

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition reason: Independent DRC reproduces 142 violations and 114 parity issues. Native annular geometry violates the saved 0.10 mm constraint despite explicit exclusions. Zero unconnected items does not satisfy the separate DRC criterion; excluded thermal errors alone do not prove opens.
- [work/drc/execution.json](<work/drc/execution.json>): check:drc
- [work/drc/console.log](<work/drc/console.log>): check:drc
- [work/drc/drc.json](<work/drc/drc.json>): check:drc

### circuit/circuit-can2-termination: CAN2 termination depends on unspecified module or harness circuitry

Disposition: **deferred**.

Location: CAN2: R36/R37, U20/U21, J2.73/.74 and J1.15/.11. Condition: Internal dual-wideband CAN2 operation without termination supplied by the brain module or harness..

Both carrier termination branches are unpopulated, leaving zero populated resistive termination conductance on the carrier. Complete-bus termination therefore depends on circuitry outside the reviewed carrier.

The schematic, exported connectivity and saved board agree on this omission. Termination adequacy and resulting communication behavior cannot be established because brain-module termination, harness topology and bit rate are unspecified. Static ERC and zero unconnected PCB items do not resolve that uncertainty.

Disposition reason: The carrier has no populated direct resistive termination, but attached module/harness termination and operating topology are unestablished. Communication failure is therefore unproven. Preserve this major uncertainty pending intended external circuitry and assembled-bus termination/topology evidence; no carrier-local termination requirement is declared.
- [inputs/project/wbo-can-trans.kicad_sch](<inputs/project/wbo-can-trans.kicad_sch>): Lines 2666–2684: resistor UUID d69380f9-5487-4782-9ce9-0690df62e543, value 120, dnp yes; instantiated as R36 and R37
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): R36 and R37 population attributes at lines 14810 and 18733; both marked dnp
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets /CAN2+, /CAN2-; components R36, R37
- [review-work/circuit/experiments/circuit-calculations.json](<review-work/circuit/experiments/circuit-calculations.json>): direct_resistors and declared_populated_carrier_termination_conductance_S

### circuit/circuit-erc-declarations: Five native ERC declaration errors remain unresolved

Disposition: **duplicate**.

Location: Root schematic power and pin declarations. Condition: Running native ERC on the saved aliased hierarchy with the supplied library configuration..

ERC reports three undriven power-input declarations, an unhandled A1 fault-output connection, and two interconnected J2 sensor-supply pins both typed as power outputs.

The five native errors reproduce independently despite exit status zero. This is a schematic declaration defect; the evidence does not establish absent physical power, a regulator collision, or a requirement to monitor the unused fault output.

Disposition reason: These are the same five established declaration errors retained under the accepted ERC finding.
- [inputs/project/Polygonus-Universal-Base.kicad_sch](<inputs/project/Polygonus-Universal-Base.kicad_sch>): #PWR01 at (36.83,78.74); A1.2/.8; J2.40/.41/.90
- [work/erc/erc.json](<work/erc/erc.json>): sheets[path='/'].violations: five error-level records
- [review-work/circuit/experiments/reproduced-erc.json](<review-work/circuit/experiments/reproduced-erc.json>): Root-sheet power_pin_not_driven, pin_not_connected and pin_to_pin records

### physical/physical-001: Five plated connector holes have zero nominal annular copper

Disposition: **accepted**.

Location: J1 mechanical holes at (71.95,35.49), (75.55,39.34), (120,28.49), (199.65,39.34), (203.25,35.49) mm. Condition: The saved J1 footprint defines five unnumbered holes as plated through holes, with copper diameter equal to drill diameter..

The nominal drill removes the entire annular land. Drill generation nevertheless classifies these holes as plated.

This is a confirmed padstack/manufacturing-data violation against the native 0.10 mm annular constraint. All ten corresponding errors are explicitly excluded with blank comments; exclusion does not change the exported geometry. Fabrication impossibility or incorrect connector fit is not established.

Disposition reason: Native geometry and independent drill export establish five plated holes with zero nominal annular land, violating the saved 0.10 mm constraint. Exclusions do not alter the geometry. This establishes a manufacturing-data defect without proving connector misfit or fabrication impossibility.
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): Lines 21687–21725; five thru_hole pad definitions
- [inputs/project/Polygonus-Universal-Base.kicad_pro](<inputs/project/Polygonus-Universal-Base.kicad_pro>): board.design_settings.rules.min_via_annular_width and drc_exclusions
- [work/drc/drc.json](<work/drc/drc.json>): violations[132]–[141]: excluded padstack and annular_width errors
- [review-work/physical/experiments/calculations.json](<review-work/physical/experiments/calculations.json>): hole_annuli: all five calculated annuli equal 0 mm
- [review-work/physical/experiments/drill-report.txt](<review-work/physical/experiments/drill-report.txt>): Plated tool T10: two 2.2 mm holes; T12: three 4 mm holes; zero unplated holes

### physical/physical-002: Default CAN2 termination depends on unverified external hardware

Disposition: **duplicate**.

Location: CAN2 network, R36/R37, U20/U21 and J2.73/74. Condition: Both wideband channels use CAN2 with the default assembly, and adequate termination is absent from the attached brain module and harness..

Both carrier termination resistors are omitted, leaving termination dependent on external hardware. Without suitable termination, reflections may reduce communication margins.

Native schematic and PCB assembly flags agree. However, CAN2 also reaches J1 and J2, whose connected hardware could supply termination. No resistance measurement or CAN waveform test establishes the assembled condition, so communication failure is not proven.

Disposition reason: Same omitted carrier termination and unresolved external-hardware condition as the canonical deferred CAN2 concern; no separate failure mechanism is established.
- [inputs/project/wbo-can-trans.kicad_sch](<inputs/project/wbo-can-trans.kicad_sch>): Resistor symbol d69380f9-5487-4782-9ce9-0690df62e543: 120 ohms, DNP; instances R36 and R37
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): Footprint UUIDs 6924b9c0-bd5f-481d-9f8d-7fc20978c5ab and 7f8c8af6-f455-43aa-9bef-892e252a308e: DNP and excluded from BOM
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets /CAN2+ and /CAN2-; components R36 and R37
- [review-work/physical/experiments/calculations.json](<review-work/physical/experiments/calculations.json>): carrier_only_can2_termination

## Limitations

- Donor carrier remains a modular design; integrated FOME-MC coverage is not established by this donor review.
- Scope omits full automotive transients, ignition energy, wideband control stability, EMC and thermal qualification.
- Saved copper is checked without refilling or changing the board.
- This disposable candidate adds VR Conditioner.kicad_sch as a byte copy of the existing lowercase VR conditioner.kicad_sch. The original checkout remains unchanged, and its filename discrepancy remains open there.
- text artifact partially supplied: work/geometry/geometry.json (100000/1657568 bytes)
- text artifact partially supplied: work/drc/drc.json (100000/142607 bytes)
- text artifact partially supplied: work/erc/erc.json (100000/226344 bytes)
- text artifact partially supplied: work/netlist/design.xml (84815/252017 bytes)
- text artifact partially supplied: work/geometry/execution.json (0/2152 bytes)
- text artifact partially supplied: work/geometry/console.log (0/446 bytes)

This result applies only to the captured candidate and declared criteria. Prototype and bench
validation, including operating conditions omitted from this review, remain necessary.

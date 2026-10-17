# Universal 112 disposable hierarchy alias review review

Outcome: **defects_or_unresolved**; process state: `complete`.

Candidate: `d5b8384d1b2948621e1c1b2dc675affa4fc13959286eea50af3e9df6c5dcd0d8`
Run: `8bf132e9-a698-44e0-878f-52cc0c3c103b`

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

circuit coverage: Inspected native schematics, embedded pin definitions, saved board and complete check records.; Independent KiCad export reproduced all 199 nets and 139 components; every exported pin matched its saved-board pad net.; Verified identical uppercase/lowercase VR Conditioner sheet copies.; Traced CAN interfaces, termination population, VR bias networks, stepper controls, ETB disable connections, lowside networks and IGBT connections.; Recorded nominal calculations, commands, versions and interpretations in review-work/circuit/experiments/review-log.md.
- Manufacturer documentation retrieval failed DNS resolution; part ratings, supplier identities and all physical pin functions were not independently qualified.
- Polygonus, Atlas and wideband module internals, firmware and integrated FOME-MC behavior were not reviewed.
- Startup, shutdown, reset, brownout, power sequencing, tolerance corners and loaded operating limits were not simulated or measured.
- No validated device-level models or dynamic electrical tests were supplied; native check success does not establish electrical behavior.
- CAN signal integrity, sensor waveforms, ignition energy, automotive transients, EMC and thermal qualification remain unverified.
- No zone refill, complete mating/mechanical review, manufacturing approval or original as-received checkout review was performed.

physical coverage: Read complete supplied check records and relevant native PCB, schematic, footprint and project-rule definitions.; Independently reproduced DRC: 142 violations, 114 parity entries and zero unconnected items; all 13 error entries are saved exclusions.; Independently exported the complete 10-instance schematic hierarchy. All 659 unique schematic pin-to-net assignments match saved PCB pads.; Inspected saved copper, connector drill classification, selected power/return routing and CAN2 resistor population. Recorded commands, versions, calculations and outcomes in review-work/physical/experiments/REVIEW_NOTES.md.
- Saved copper only; no refill pass or comparison against newly generated zone fill.
- No physical electrical, CAN waveform or temperature measurements. No CAN bit-rate, cable or edge-rate model was evaluated.
- No ampacity or thermal qualification. Conditional heater-trace resistance calculations exclude contacts, barrels, actual operating temperature and full supply/return-loop impedance.
- No complete module-internal review, including brain CAN termination or integrated FOME-MC coverage; the separate original as-received checkout was not reviewed.
- No exhaustive 3D assembly-clearance, fabrication-tolerance, solder-process or manufacturer-drawing validation. Primary connector drawing retrieval failed DNS resolution.
- Disabled courtyard checks and the saved zero copper-edge minimum do not establish manufacturing clearance acceptance.
- Not every ERC, library or parity warning was individually adjudicated against component datasheets.
- Automotive transients, ignition energy, wideband stability, EMC and manufacturing approval remain outside this review.

## Findings and dispositions

### check/erc: ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

Disposition: **accepted**.

Location: erc. Condition: check erc (erc).

ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

ERC ran on 10 sheet instance(s) and reported 353 violation(s): {'warning': 348, 'error': 5}

Disposition reason: Established native ERC criterion failure: independent execution exactly reproduces 353 violations, including five errors concerning power-drive declarations, A1.2 connectivity and J2.40/.41 pin types. Successful command execution does not mean ERC passed. These findings establish rule defects without proving absent hardware power or regulator contention.
- [work/erc/execution.json](<work/erc/execution.json>): check:erc
- [work/erc/console.log](<work/erc/console.log>): check:erc
- [work/erc/erc.json](<work/erc/erc.json>): check:erc

### check/drc: DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition: **accepted**.

Location: drc. Condition: check drc (drc).

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

DRC with schematic parity reported 142 violation(s), 0 unconnected item(s), 114 parity issue(s)

Disposition reason: Established native DRC criterion failure: independent execution exactly reproduces 142 violations and 114 parity issues, with zero unconnected items. All 13 error entries are saved exclusions with empty comments; the underlying geometry violations remain. Saved copper was evaluated without refilling. Manufacturing rejection and functional electrical failure are not established.
- [work/drc/execution.json](<work/drc/execution.json>): check:drc
- [work/drc/console.log](<work/drc/console.log>): check:drc
- [work/drc/drc.json](<work/drc/drc.json>): check:drc

### circuit/circuit-can2-termination: Internal CAN2 termination depends on an unverified external implementation

Disposition: **noted**.

Location: Internal CAN2 bus, R36/R37 and module interface J2. Condition: Default saved assembly with both wideband interfaces populated..

Both carrier termination resistors are omitted, leaving no populated resistive termination across CAN2 on the carrier.

Native schematic and board agree that the only two cross-bus resistors are 120-ohm DNP parts. Their assembled termination conductance is therefore zero. Termination inside the attached module or harness is not established by this packet. Without module internals, bitrate or waveform measurements, this remains an unresolved termination concern rather than demonstrated CAN failure.

Disposition reason: Supported minor configuration concern: schematic and saved PCB both omit R36/R37, contributing zero populated carrier termination conductance across CAN2. Module and harness termination remain unverified. No declared termination requirement, bitrate or measured signaling failure establishes a defect, so the concern retains its non-defect classification.
- [inputs/project/wbo-can-trans.kicad_sch](<inputs/project/wbo-can-trans.kicad_sch>): R36 symbol near line 2676; dnp yes; instance paths assign R36 and R37
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): R36 near line 14746 and R37 near line 18669; both DNP
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets /CAN2+, /CAN2-; components R36, R37
- [review-work/circuit/experiments/can-termination-calculation.json](<review-work/circuit/experiments/can-termination-calculation.json>): Carrier termination conductance calculation and stated exclusions

### physical/PHY001: Five plated connector holes have zero annular copper

Disposition: **accepted**.

Location: J1 mechanical holes at (71.95,35.49), (75.55,39.34), (120,28.49), (199.65,39.34), (203.25,35.49) mm. Condition: Five unnumbered J1 holes are defined as plated through holes with copper diameter equal to drill diameter..

The nominal annular ring is (pad diameter − drill diameter)/2 = 0 mm, below the saved 0.1 mm constraint. Native drill export classifies these holes as plated.

This is a reproducible manufacturing-geometry violation, explicitly excluded in the project. Exclusion does not change the copper or drill classification. These holes carry no net; the evidence does not establish an electrical open, mechanical failure or fabricator rejection.

Disposition reason: Established saved geometry defect: all five holes are plated, and their nominal annular ring is zero, below the saved 0.1 mm constraint. Native extraction, independent DRC and drill export agree. Saved exclusions do not change the geometry or limit. Electrical opens, mechanical failure and fabricator rejection remain unproven.
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): Lines 21686–21725: five unnumbered thru_hole pads, including UUID e186b75f-4739-4f2e-a723-906e090de7da; diameter/drill pairs are 4/4 mm or 2.2/2.2 mm.
- [inputs/project/Polygonus-Universal-Base.kicad_pro](<inputs/project/Polygonus-Universal-Base.kicad_pro>): board.design_settings.rules.min_via_annular_width = 0.1; corresponding padstack and annular_width exclusions have empty comments.
- [work/drc/drc.json](<work/drc/drc.json>): violations[132] through violations[141]: five padstack and five annular-width errors, all excluded:true.
- [review-work/physical/experiments/drill/Polygonus-Universal-Base-PTH.drl](<review-work/physical/experiments/drill/Polygonus-Universal-Base-PTH.drl>): Plated file header; T10C2.200 and T12C4.000; corresponding hits at X75.55, X199.65, X71.95, X120.0 and X203.25.

### physical/PHY002: Three ground thermals fall below the saved spoke-count rule

Disposition: **accepted**.

Location: C15.2 (107.851547,98.755769), U7.4 (101.008268,102.683572), C14.2 (101.591948,104.291543) mm. Condition: The saved F.Cu fill connects C15.2, U7.4 and C14.2 to the ground zone with one resolved thermal spoke each..

Saved copper falls short of the configured two-spoke thermal-relief rule.

Both native DRC runs identify the same saved-fill violations. They are explicit saved exclusions with empty comments. Zero unconnected items does not establish compliance with the spoke-count rule. No open circuit, excessive temperature or inadequate ampacity is demonstrated.

Disposition reason: Established saved thermal-rule defect: independent DRC confirms one resolved F.Cu spoke at each cited ground pad against the configured minimum of two. The saved exclusions and zero unconnected count do not establish compliance. No refill, open-circuit, ampacity or temperature conclusion is implied.
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): Ground zone UUID bc540b8b-3ffa-4b00-b211-3a91191a1f6d, line 69477; affected pad UUIDs at lines 13481, 20557 and 28773.
- [inputs/project/Polygonus-Universal-Base.kicad_pro](<inputs/project/Polygonus-Universal-Base.kicad_pro>): board.design_settings.rules.min_resolved_spokes = 2; corresponding starved_thermal exclusions at lines 264–272.
- [work/drc/drc.json](<work/drc/drc.json>): violations[94], [95], [96]: minimum spoke count 2, actual 1; all excluded:true.
- [review-work/physical/experiments/stock_drc.json](<review-work/physical/experiments/stock_drc.json>): Three independently reproduced starved_thermal entries for C15.2, U7.4 and C14.2.

### physical/PHY003: CAN2 termination depends on unverified module or harness configuration

Disposition: **duplicate**.

Location: CAN2 bus; R36, R37, U20, U21 and J2. Condition: R36 and R37 are both DNP in the schematic and saved PCB while the two wideband transceivers share CAN2 with the brain-module interface and harness connector..

Neither carrier termination resistor contributes a resistive load with the saved population. Any completed-bus termination must therefore come from another assembly element.

The carrier population is established, but brain-module termination and external harness configuration were not established. ERC, connectivity and geometry checks do not measure assembled-bus termination or signaling. This is a configuration concern, not proof that the short internal CAN bus fails.

Disposition reason: Same minor CAN2 population and external-termination dependency as the surviving circuit finding. Both describe the same DNP resistors, shared bus and unverified module/harness configuration.
- [inputs/project/wbo-can-trans.kicad_sch](<inputs/project/wbo-can-trans.kicad_sch>): Symbol UUID d69380f9-5487-4782-9ce9-0690df62e543, line 2662 onward: 120-ohm resistor, dnp yes, in_bom no; instance references R36 and R37.
- [inputs/project/Polygonus-Universal-Base.kicad_pcb](<inputs/project/Polygonus-Universal-Base.kicad_pcb>): R36 and R37 footprints near lines 14746 and 18669: DNP population and pads on /CAN2+ and /CAN2-.
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): components.R36, components.R37 and nets./CAN2+, nets./CAN2-: U20/U21, J2.73/.74 and J1.15/.11 share the bus.
- [review-work/physical/experiments/check_audit.json](<review-work/physical/experiments/check_audit.json>): can_termination: default carrier resistor conductance 0 S; hypothetical single/dual resistor populations give 120/60 ohms.

## Limitations

- Donor carrier remains a modular design; integrated FOME-MC coverage is not established by this donor review.
- Scope omits full automotive transients, ignition energy, wideband control stability, EMC and thermal qualification.
- Saved copper is checked without refilling or changing the board.
- This disposable candidate adds VR Conditioner.kicad_sch as a byte copy of the existing lowercase VR conditioner.kicad_sch. The original checkout remains unchanged, and its filename discrepancy remains open there.
- text artifact partially supplied: work/geometry/geometry.json (100000/1657568 bytes)
- text artifact partially supplied: work/drc/drc.json (100000/142607 bytes)
- text artifact partially supplied: work/erc/erc.json (100000/226344 bytes)
- text artifact partially supplied: work/netlist/design.xml (84869/252017 bytes)
- text artifact partially supplied: work/geometry/execution.json (0/2134 bytes)
- text artifact partially supplied: work/geometry/console.log (0/446 bytes)

This result applies only to the captured candidate and declared criteria. Prototype and bench
validation, including operating conditions omitted from this review, remain necessary.

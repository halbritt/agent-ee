# Engineering comparison mapping-b review

Outcome: **incomplete**; process state: `incomplete`.

Candidate: `21b0c14a5c2860d4ccecc4fe1758409cf93d368265316a1c5f42d3577cedea7e`
Run: `7c0edd5c-e4f4-4823-90d9-cdd624e68f5e`

External connector contract: J1 pin 2 is the input, pin 1 the filtered output, pin 3 ground. Verify saved connectivity.

## Checks

| Check | Execution | Design result | Summary |
| --- | --- | --- | --- |
| erc | ok | pass | ERC ran on 1 sheet instance(s) with zero violations |
| drc | ok | pass | DRC with schematic parity reported zero issues on the saved board |
| netlist | ok | info | native XML netlist exported from a complete 1-instance hierarchy: 3 component(s), 3 net(s), 0 DNP |
| geometry | ok | info | saved-board geometry extracted (no declared geometric requirement applied) |

## Initial reviews

- circuit: ok; requested model claude-fable-5.
- physical: ok; requested model claude-fable-5.

circuit coverage: Verified candidate file hashes against packet values (sch ecc56a68..., pcb f8264d95...); Independently re-exported the schematic netlist with kicad-cli 10.0.7 and compared to packet work/netlist/design.xml (identical); Re-derived J1 pin-to-wire mapping geometrically from the symbol instance (at 60.96,63.5 rot 180) and the wire/label coordinates to rule out a netlist tool artifact; Compared saved J1 pin assignment (pin1=/IN, pin2=/OUT, pin3=GND) against the stated contract (pin2=input, pin1=filtered output, pin3=ground); Confirmed PCB pad nets match schematic (J1 pad1 /IN, pad2 /OUT, pad3 GND; DRC schematic parity 0), so the board inherits the schematic assignment; Traced board copper connectivity from packet geometry.json: /IN, /OUT tracks, GND via at (113,100), B.Cu GND zone with thermal spokes at J1 pad 3; DRC reports 0 violations and 0 unconnected items; Quantified electrical consequence with ngspice AC analysis in both connector orientations (ideal R=10k, C=100n, 1Meg load); Reviewed packet ERC/DRC/netlist/geometry check records for execution validity (exit codes, ignored-check lists, consistent hashes)
- ngspice demonstration uses ideal R/C with a 1 Megohm load; no load impedance is stated in the operating conditions, so the contract-orientation result (no filtering into a high-impedance load) assumes a high-impedance measurement point
- No ratings, tolerance, thermal, or EMC assessment performed: inputs/requirements.txt restricts evaluation to the stated connector contract and forbids imposing unstated ratings
- Zone fill evaluated as saved; the read-only candidate forbids a refill pass, so zone connectivity relies on the saved filled_polygon plus DRC/geometry probe results
- Stock symbol/footprint library files referenced by the project (KICAD10_* paths) were not independently audited beyond the lib_symbols embedded in the schematic and the footprints embedded in the board
- Physical mating-connector fit and real connector part verification are out of scope; J1 is a generic 2.54 mm pin header

physical coverage: Re-derived J1/R1/C1 pin sheet coordinates from native schematic placement (J1 at 180 deg) and matched them to wires, local labels and power symbols to establish pin-to-net mapping independently of net names; Independently re-exported the KiCad XML netlist (kicad-cli 10.0.7) and compared against work/netlist/design.xml and connectivity.json — identical; Loaded the saved board in pcbnew python: dumped all pad net assignments, all track segments/via, verified schematic/board parity of nets and confirmed GetUnconnectedCount == 0 on the saved file; Traced copper paths: /IN J1.1->R1.1; /OUT R1.2->C1.1 with J1.2 stub landing mid-span on the same-net segment (valid T junction); GND C1.2 -> F.Cu track -> via (113,100) -> saved B.Cu zone fill -> J1.3 thermal spokes (spokes visible in the filled_polygon of the native .kicad_pcb); Checked the saved connectivity against the external connector contract in inputs/requirements.txt (the sole stated criterion); Sanity-checked placement vs the 20x12 mm Edge.Cuts outline (all pads, courtyards, via and zone inside); reviewed fresh ERC/DRC/parity outputs and their ignored-check lists
- Zone fill evaluated as saved; the read-only candidate forbids a refill pass, so fill correctness is taken from the stored filled_polygon plus DRC/pcbnew connectivity on that stored fill
- No ampacity, thermal, ratings or qualification assessment performed — explicitly excluded by inputs/requirements.txt; no conclusions drawn from track widths
- No SPICE/transfer-function simulation run; the criterion is connectivity only and J1 is exclude_from_sim in the schematic
- Manufacturing review limited to the native DRC rule set as configured (several checks are in the ignored list, e.g. missing_courtyard, footprint_type_mismatch); no fab-specific rule deck applied

## Findings and dispositions

### circuit/CIRC-01: J1 pins 1 and 2 are swapped relative to the stated connector contract (input and filtered output inverted)

Disposition: **unresolved**.

Location: inputs/project/rc_filter.kicad_sch (symbol J1 uuid 465541a7-d06f-5282-b75f-15b79e4182a2 at 60.96,63.5 rot 180; labels IN at 68.58,66.04 and OUT at 68.58,63.5) and inputs/project/rc_filter.kicad_pcb (J1 pads: pad 1 net /IN, pad 2 net /OUT). Condition: The contract (inputs/requirements.txt) requires J1 pin 2 = input and pin 1 = filtered output. In the saved schematic and board, J1 pin 1 connects to the raw input node /IN (with R1 pin 1) and J1 pin 2 connects to the filtered R1/C1 node /OUT (with R1 pin 2 and C1 pin 1). Pin 3 = GND is correct. Confirmed by independent kicad-cli netlist re-export and by geometric pin-position derivation: with the 180-degree instance transform, J1 pin 1 lands at (66.04, 66.04) on the wire labeled IN and pin 2 at (66.04, 63.50) on the wire labeled OUT; PCB pad nets and DRC parity (0 issues) show the board inherits the same assignment..

A system honoring the contract drives pin 2 and takes the filtered output from pin 1. On this board that drives the C1 node directly and reads through the 10k resistor: ngspice AC analysis shows a flat ~-0.086 dB response from 10 Hz to 100 kHz into a 1 Megohm load (no low-pass behavior), while the 100 nF capacitor loads the source directly. The intended ~159 Hz low-pass (-16 dB at 1 kHz) only exists in the reverse, non-contract orientation. All toolchain checks (ERC, DRC, parity) pass because the schematic's internal IN/OUT labels are self-consistent with the wrong pin choice; the successful exits do not demonstrate contract conformance.

The scope's single criterion is conformance of saved connectivity to the external connector contract, and the saved design fails it: the input and filtered-output pins are interchanged in both the schematic and the board. Two independent derivations (netlist export and raw-coordinate pin mapping) agree, so this is not a tool artifact, and DRC parity of 0 confirms the board matches the swapped schematic. The clean ERC/DRC/netlist results are expected despite the defect because the error is in which physical pin each net reaches, not in internal consistency. Electrically the swap is not benign: used per the contract the circuit provides no filtering and presents the capacitor directly to the external driver. Because the design violates its only stated functional requirement at the external interface, severity is blocking and the assessment is defect.
- [inputs/project/rc_filter.kicad_sch](<inputs/project/rc_filter.kicad_sch>): symbol J1 (uuid 465541a7-d06f-5282-b75f-15b79e4182a2) at (60.96 63.5 180); wires at (66.04,66.04)->(68.58,66.04) with label IN (uuid 7b760986) and (66.04,63.5)->(68.58,63.5) with label OUT (uuid 39787aa2)
- [work/netlist/design.xml](<work/netlist/design.xml>): nets: /IN = J1 pin 1 + R1 pin 1; /OUT = C1 pin 1 + J1 pin 2 + R1 pin 2; GND = C1 pin 2 + J1 pin 3
- [inputs/project/rc_filter.kicad_pcb](<inputs/project/rc_filter.kicad_pcb>): footprint J1 (uuid d529384f-9eec-4053-a911-4a9c09283aa0): pad 1 net /IN, pad 2 net /OUT, pad 3 net GND
- [work/geometry/geometry.json](<work/geometry/geometry.json>): pads: J1.1 (100,100) net /IN, J1.2 (100,102.54) net /OUT, J1.3 (100,105.08) net GND
- [inputs/requirements.txt](<inputs/requirements.txt>): line 1: J1 pin 2 is the input, pin 1 the filtered output, pin 3 ground
- [review-work/circuit/experiments/netlist_summary.txt](<review-work/circuit/experiments/netlist_summary.txt>): independent kicad-cli 10.0.7 netlist re-export, identical net membership
- [review-work/circuit/experiments/pin_mapping.txt](<review-work/circuit/experiments/pin_mapping.txt>): geometric derivation: pin 1 -> (66.04,66.04)=IN wire, pin 2 -> (66.04,63.50)=OUT wire, pin 3 -> (66.04,60.96)=GND wire
- [review-work/circuit/experiments/ngspice_out.txt](<review-work/circuit/experiments/ngspice_out.txt>): AC sweep: as-saved orientation vdb(outA) -16.07 dB at 1 kHz (low-pass); contract orientation vdb(outB) -0.086 dB flat 10 Hz..100 kHz (no filtering)
- [review-work/circuit/experiments/NOTES.md](<review-work/circuit/experiments/NOTES.md>): sections 1-4: commands, versions, transform derivation, simulation assumptions

### physical/PHY-001: J1 input/output pins swapped relative to the external connector contract (pin 1 = input, pin 2 = filtered output as saved)

Disposition: **unresolved**.

Location: inputs/project/rc_filter.kicad_sch (J1 at (60.96,63.5) rot 180; wires/labels at (66.04,66.04)-(68.58,66.04) 'IN' and (66.04,63.5)-(68.58,63.5) 'OUT') and inputs/project/rc_filter.kicad_pcb (J1 pads 1/2). Condition: The contract in inputs/requirements.txt states J1 pin 2 is the input, pin 1 the filtered output, pin 3 ground. In the saved schematic, J1 pin 1 (sheet position (66.04,66.04) after 180-degree placement) is wired to label 'IN' — the node entering series resistor R1 pin 1 — and J1 pin 2 ((66.04,63.50)) is wired to label 'OUT' — the R1/C1 filtered junction. The saved board matches: J1 pad 1 (100,100) is on net /IN and pad 2 (100,102.54) on net /OUT. Pin 3 = GND is the only pin meeting the contract..

Pins 1 and 2 are transposed at the external interface. A harness built per the contract drives the source into the R1/C1 junction (directly loading the source with C1, no defined series element) and takes 'output' from the far end of R1, so the specified input-at-pin-2, filtered-output-at-pin-1 behavior is not delivered; the sole stated acceptance criterion fails. The error is invisible to ERC/DRC/schematic parity because schematic and board are mutually consistent — net names simply attach the roles to the wrong pins.

Three independent derivations agree: (1) geometric reconstruction of J1's pin positions under its saved 180-degree rotation maps pin 1 to the wire labeled 'IN' and pin 2 to 'OUT'; (2) a fresh kicad-cli netlist export reproduces /IN=[J1.1,R1.1] and /OUT=[C1.1,J1.2,R1.2]; (3) pcbnew reads the same pad-net assignments from the board, and the copper implements them. Electrically the net on J1.1 is unambiguously the filter input (it reaches only the free end of series R1) and the net on J1.2 is the filtered R-C node, so this is a true role swap at the connector, not a naming quibble. It directly violates the only evaluated criterion, which supports blocking severity; schematic and board agree with each other, so the fix is a design change (reassign J1 pins 1/2), not a layout parity repair.
- [inputs/requirements.txt](<inputs/requirements.txt>): line 1: 'J1 pin 2 is the input, pin 1 the filtered output, pin 3 ground'
- [inputs/project/rc_filter.kicad_sch](<inputs/project/rc_filter.kicad_sch>): J1 symbol (uuid 465541a7) at (60.96 63.5 180); wire uuid 6f0b101e (66.04,66.04)->(68.58,66.04) with label 'IN' uuid 7b760986; wire uuid af803d4e (66.04,63.5)->(68.58,63.5) with label 'OUT' uuid 39787aa2; R1 pin1 wire uuid 9b9e16d9 to label 'IN' uuid 4728ac71
- [inputs/project/rc_filter.kicad_pcb](<inputs/project/rc_filter.kicad_pcb>): J1 footprint uuid d529384f: pad 1 (net "/IN") uuid 7e93f4b3, pad 2 (net "/OUT") uuid d6746591, pad 3 (net "GND") uuid ddcb81c6
- [work/netlist/design.xml](<work/netlist/design.xml>): <nets>: net '/IN' nodes J1 pin 1 + R1 pin 1; net '/OUT' nodes C1 pin 1, J1 pin 2, R1 pin 2; net 'GND' nodes C1 pin 2, J1 pin 3
- [work/netlist/connectivity.json](<work/netlist/connectivity.json>): nets: /IN=[J1.1,R1.1], /OUT=[C1.1,J1.2,R1.2], GND=[C1.2,J1.3]
- [work/geometry/geometry.json](<work/geometry/geometry.json>): J1 pads: pad 1 net /IN at (100,100), pad 2 net /OUT at (100,102.54), pad 3 net GND at (100,105.08)
- [review-work/physical/experiments/notes.md](<review-work/physical/experiments/notes.md>): sections 1-3: independent netlist re-export, pin-position derivation, pcbnew pad/track dump (unconnected=0)
- [review-work/physical/experiments/my_netlist.xml](<review-work/physical/experiments/my_netlist.xml>): <nets> block — independently regenerated, identical to packet netlist

### physical/PHY-002: Criteria set cannot detect the connector-contract violation: all native checks pass on an internally consistent but contract-swapped design

Disposition: **unresolved**.

Location: packet checks 'erc', 'drc', 'netlist', 'geometry'; work/erc/erc.json, work/drc/drc.json. Condition: ERC reports 0 violations, DRC with --schematic-parity reports 0 violations/0 unconnected/0 parity issues, and the netlist/geometry probes are info-only. None of these checks encodes the external requirement that net roles map to specific J1 pin numbers, so the suite returns clean results on a design whose sole acceptance criterion fails (PHY-001)..

ERC/DRC/parity verify internal self-consistency (schematic vs board, copper rules), not requirement-level pin assignments. A pin-role swap propagated consistently through schematic and layout produces zero findings; a successful tool exit is then mistaken for contract compliance unless a dedicated assertion (e.g. expected net name per J1 pad number) is run against the netlist or geometry output.

This is a verification-coverage observation, not a tool failure: the tools ran correctly and their outputs are reproducible (I re-ran the netlist export with identical results). The gap is that the declared criteria (erc, drc, netlist, geometry) contain no assertion binding net /IN to J1 pad 2 and /OUT to J1 pad 1 per the contract, so green results provide no evidence for the one requirement under review. Flagged as a concern so the harness adds a pin-role assertion; severity minor because the data needed for such a check is already produced by the existing netlist/geometry evidence.
- [work/erc/console.log](<work/erc/console.log>): 'Found 0 violations'
- [work/drc/console.log](<work/drc/console.log>): 'Found 0 violations / 0 unconnected items / 0 schematic parity issues'
- [work/netlist/design.xml](<work/netlist/design.xml>): <nets> block shows J1.1=/IN, J1.2=/OUT — data sufficient to detect the swap, but no check in the packet asserts on it
- [review-work/physical/experiments/notes.md](<review-work/physical/experiments/notes.md>): Conclusion section: self-consistency masks contract violation from ERC/DRC/parity

## Limitations

- Artificial exercise requirements; no ECU qualification.
- Checks-only run: independent agent reviews and adjudication not performed.
- Comparative initial judgments only: no adjudication; not an acceptance run.

This result applies only to the captured candidate and declared criteria. Prototype and bench
validation, including operating conditions omitted from this review, remain necessary.

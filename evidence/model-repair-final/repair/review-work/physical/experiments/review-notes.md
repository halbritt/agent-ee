# Physical initial review experiment record

Candidate: 300958ecd16853020cb8349fb72b85adce6c1e892d58b990815c099df44094ec
Run: 7c496fac-ab12-43f8-8d50-e9de4d77ab86
Packet SHA256: 8db30139ed51a572b2ba794dce8bd29ff31e58b7321e20d25b23a5f6d55f2cb2
Role: physical

## Scope and inputs
Read inputs/requirements/fixture-requirements.md, the native schematic, PCB, project settings, library tables, all five checks/*.json records, work/drc/drc.json, work/erc/erc.json, work/netlist/design.xml and connectivity.json, and the transient deck/control/log/raw artifacts. Only the declared ideal unloaded 1 V response and 0.20 mm track geometry are acceptance requirements. No ECU requirements or selected component ratings assumed. No prior reviewer experiments or judgments were read.

Initial discovery commands: pwd; rg --files -g AGENTS.md -g !home/** /work /candidate /evidence; ls -la /candidate/project /evidence/work /evidence/checks /work/experiments. No AGENTS.md was found by that search. Read-only cat/rg/nl inspections located native pads, segments, zone fill, values and edge outline. Project file has empty drc_exclusions and no additional design-rule values. Candidate project hashes are recorded in native_execution.json.

## Reproduction commands and versions
Run `python3 /work/experiments/run_native_checks.py` to run the three exact argv arrays recorded in native_execution.json: ERC, DRC with schematic parity and XML netlist export. Outputs stay in this directory. No refill option or candidate write was used. Before/after hashes confirm all candidate project files remained unchanged.

Run `python3 /work/experiments/inspect_native.py` to independently parse native S-expressions, compare board pad assignments with freshly exported schematic nets, examine track widths and saved fill samples, calculate the ideal RC response, and read/interpolate supplied binary raw vectors. Results and assumptions are in native_inspection.json.

versions.json records KiCad CLI 10.0.7, ngspice 42 and Python 3.12.3. A dependency availability probe imported pcbnew 10.0.7 with the same PROPERTY_ENUM diagnostics seen in work/geometry/console.log, then failed to import optional shapely (not installed). This is an experiment dependency limitation, not a design defect. The completed native-inspection experiment uses only Python standard library and does not depend on pcbnew or shapely. No package installation or network reference retrieval was necessary.

## Outcomes and native locators
- Fresh ERC: no violations; fresh DRC: no violations, unconnected items or parity issues. Compare drc.json/erc.json with work/drc/drc.json and work/erc/erc.json; read the arrays, not exit code alone. Both retain the packet's ignored-check lists.
- inputs/project/rc_filter.kicad_pcb lines 210, 218, 359, 367, 587, 596, 605: all seven pad nets match fresh schematic export. IN is J1.1--R1.1; OUT is R1.2/C1.1/J1.2; GND is C1.2/J1.3.
- PCB lines 667, 675, 683, 691, 699: exactly five segments, each 0.25 mm. All exceed the declared 0.20 mm minimum. OUT branch reaches the interior of the R1--C1 segment at (107.5,100); physical connection does not require a saved endpoint at that junction.
- PCB lines 699, 707, 715, 734: C1.2 at (110.95,100) reaches a GND via at (113,100) and the B.Cu saved fill. J1.3 at (100,105.08) connects through diagonal thermal spokes. An explicit sampled polygon path reaches from the via center to the J1.3 annulus; 3003/3003 sampled points lie within the saved fill. Four diagonal polar sample sets corroborate thermal bridges. Center of via itself is drilled; connection is through its plated annulus, whose surrounding area is in the plane. This sampling supports saved geometry and supplements DRC; it is not a fabrication or clearance guarantee.
- PCB lines 627--666: closed 20 x 12 mm rectangular outline. Footprint bodies/courtyards are within it, and saved copper lies inside the outline. No special fabrication capability assumed. Saved via pad/drill is 0.6/0.3 mm; J1 pad/drill is 1.7/1 mm. Neither implies fabrication qualification without a process specification.
- inputs/project/rc_filter.kicad_sch lines 955 and 1274; PCB lines 107 and 256: C1=100n, R1=10k. tau=0.001 s, ideal V(1ms)=0.6321205588 V, ideal V(5ms)=0.9932620530 V.
- work/rc-step/deck.cir lines 2--7 and work/rc-step/ngspice.log measurements: stimulus ramps 0--1 V in 1 ns and starts discharged. Independently parsed work/rc-step/result.raw has 1025 points and four vectors; interpolated Vout is 0.6321201340 V at 1 ms and 0.9932621197 V at 5 ms. Errors against ideal instantaneous-step equation are approximately -0.425 microvolt and +0.067 microvolt, immaterial to 0.62--0.65 V criterion. Vout at 5 ms has no acceptance bounds and is informational.
- Initial ideal current is 100 microampere; peak ideal resistor dissipation is 100 microwatt and final capacitor energy is 50 nJ. These stimulus-specific calculations are not component-rating or copper ampacity qualifications.

## Coverage omissions and limitations
No board refill, manufactured continuity test, Gerber/drill fabrication audit, assembly process validation, physical thermal/ampacity qualification, or hardware response measurement. No copper weight, materials, component part numbers, tolerances, voltage ratings or thermal models were specified/assumed. No tolerance, temperature, external loading, ADC sampling, EMI, interconnect parasitic or source-impedance analysis. These are outside the fixture's ideal scope, not demonstrated failures.

ERC ignores single_global_label, four_way_junction, simulation_model_issue and footprint_filter. DRC ignores missing_courtyard, track_not_centered_on_via, tuning_profile_track_geometries, footprint_filters_mismatch and footprint_type_mismatch. Manual native inspection found courtyards on all three footprints, the via centered on its trace endpoint and expected SMD/PTH attributes, but this review does not claim a complete independent implementation of ignored checks. No model-validation inference is taken from ERC.

No evidence-supported physical defect identified within the captured requirements. Clean native checks and geometric observations do not establish manufactured electrical behavior.

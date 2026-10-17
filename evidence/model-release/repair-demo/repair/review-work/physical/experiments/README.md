# Physical initial review

Candidate: a3056a0f779d71d1360af66b5419e5d3a8cc024bd5f95a292d1888f36bafff5d
Run: c98786fd-4bbd-4910-afa1-826a0d5f3525
Packet: 8eddbf3895208ee2cd021bd03bd9c9a1db5b3a179691ddfb61ee3bd584ffa991

## Scope and records

Read the native schematic, PCB, project settings and declared fixture requirements; examined fresh ERC, DRC, geometry, netlist and transient records. No prior review experiments or judgments were used. No network references were needed for these native geometric and elementary RC calculations. Candidate files were never edited or refilled. Commands and outputs for substantive independent checks are in commands.jsonl and matching .stdout/.stderr files; scripts are retained here. Output SVG was generated without --check-zones and uses the saved fill.

Initial discovery commands: `pwd`; `rg --files /candidate /evidence /work` filtered for native and check extensions; `cat /candidate/project/rc_filter.kicad_pro`; reads of `/evidence/checks/{geometry,drc,rc-step}.json`; `kicad-cli pcb export svg --help`. The project has no DRC exclusions or custom rules. Python dependency probe found pcbnew 10.0.7 and no Shapely; no dependency was installed. Geometry checks therefore use direct native parsing and an independent even-odd polygon point test. pcbnew emitted three PROPERTY_ENUM assertion messages on import; commands completed and native geometry results were independently corroborated by text parsing. These messages are not classified as native design violations.

## Assumptions, method and outcomes

`physical-audit.json` enumerates every saved track and pad, compares native pad-net sets with the schematic netlist, lists pcbnew connectivity and tests actual saved B.Cu fill. All five segments are 0.25 mm, exceeding the declared 0.20 mm minimum. Seven pad-net assignments match the exported schematic. The return runs C1.2 -> 2.05 mm F.Cu trace -> GND via at (113,100) -> saved B.Cu polygon -> J1.3. Four diagonal thermal spokes were sampled from the J1.3 annulus through the relief to the plane; all tested points lie in saved copper. A sampled path from the annulus to the via also remains in saved copper. Finite samples corroborate this route but are not a general proof of all polygon geometry; independent native DRC complements them. No refill was performed.

Native DRC with schematic parity independently reports zero violations, zero unconnected items and zero parity issues (drc.json). Independent ERC also reports zero violations (erc.json). Their ignored checks are retained in those original result JSON files rather than treated as covered. API enumeration and version probes are retained in api_probe.stdout and versions.stdout.

`response-audit.json` independently reads all 1025 binary transient samples, verifies time range and stimulus level, and compares them with Vout=1-exp(-t/RC), using the native 10k/100n values. At 1 ms it obtains 0.632120134 V, within 0.62..0.65 V; at 5 ms, 0.993262120 V. The 5 ms sample is informative because no limit was specified. The deck's 1 ns input ramp is 1e-6 of tau; its finite rise and numerical error do not challenge the declared margin. Unloaded ideal zero initial capacitor voltage matches the stated test. Simulation is from schematic topology; saved physical connectivity was checked separately.

Placement/manufacturing geometry was inspected from native footprints and outline: closed 20 x 12 mm rectangle, separated courtyards inside it, 0805 SMD pads, 1 mm header drills with nominal 0.35 mm annuli, and 0.3 mm via drill with nominal 0.15 mm annulus. Native DRC found no spacing, edge, drill or courtyard violations under the saved/default settings. No specific fabricator acceptance or part fit is inferred from these dimensions. The 1 V ideal RC step has peak source current about 100 uA, peak resistor power 0.1 mW and final capacitor energy 50 nJ. These calculations do not qualify unspecified components or copper thermally.

No evidence-supported defect was identified against the captured scope.

## Coverage omissions

- No zone refill or refill-stability comparison; only saved fill is evaluated.
- No fabricated board, assembly, continuity, step-response or thermal measurements.
- No component part numbers, ratings, tolerances, temperature effects, loading, ADC sampling, EMI, parasitic extraction or hardware qualification; these are outside the fixture's stated coverage.
- No fabrication stackup/copper thickness/process capability, assembly-process or 3D mechanical qualification. Track width is only checked geometrically; no ampacity inference.
- Ignored ERC/DRC categories listed in result JSON were not independently rerun enabled. Finite return-path samples do not exhaustively establish polygon clearance; native DRC provides complementary saved-board checks.

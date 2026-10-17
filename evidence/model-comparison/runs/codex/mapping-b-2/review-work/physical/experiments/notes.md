# Physical initial review: commands, assumptions, outcomes, limitations

Candidate 38e2fe8dec46839b67a77ae9ecb488b30d9b2ecdf7cddd293c295bfecaf50650; run e5c3df45-50fe-491e-b996-faba2b28f975.
All candidate access was read-only. No candidate save, refill, repair, or modification was performed. No credentials or prior reviewer experiments were read.

## Commands and versions

- Discovery: `pwd`, `rg --files` for candidate project/check artifacts and AGENTS.md; `ls -la /work /candidate /evidence /evidence/checks /evidence/work`. No AGENTS.md was found in the searched workspace/candidate/evidence trees.
- `kicad-cli version`: 10.0.7. `/usr/bin/python3 --version`: 3.12.3. `pcbnew.GetBuildVersion()`: 10.0.7-10.0.7~ubuntu24.04.1.
- Dependency probe: `/usr/bin/python3 -c 'import pcbnew; print(pcbnew.GetBuildVersion()); import shapely; print("shapely",shapely.__version__)'` returned 1: shapely absent. Shapely was not needed or used. pcbnew emitted three PROPERTY_ENUM assertions during import, also seen in supplied geometry console. These are tool diagnostics, not design violations.
- Inspected `cat`/`nl -ba`/`rg -n` output from original requirements, PCB, schematic, project rules; supplied `checks/{drc,erc,geometry,netlist}.json`, native `work/{erc,drc}/*.json`, netlist XML/connectivity and geometry console. Project design settings have empty custom rules and no DRC exclusions. Native ERC and DRC report zero violations; DRC also zero unconnected items and zero parity issues. Ignored checks are explicitly listed in those native reports; this review does not claim they ran.
- API discovery: printed CONNECTIVITY_DATA methods and GetConnectedPads documentation. These methods report adjacent objects, so the final probe traverses the adjacency graph, including zones, rather than mistaking directly adjacent pads for an entire net.
- `/usr/bin/python3 /work/experiments/trace_saved.py > /work/experiments/trace_saved.json 2> /work/experiments/trace_saved.stderr.log`: exit 0. Loads saved native PCB, builds connectivity only, records pads/tracks/zones and traverses their copper adjacency graph. Final JSON contract outcome is FAIL; process exit 0 means the inspection ran. Original hashes in trace_saved.json match the packet. An initial probe revision used zone.GetLayerName(), which misleadingly returned F.Cu for the zone; final code uses zone.GetLayerSet().Seq() and correctly records B.Cu. Initial no-layer via.GetWidth() produced an API warning; final code passes F.Cu for via width. Final stderr retains only the three import PROPERTY_ENUM assertions.
- `kicad-cli sch export netlist --format kicadxml -o /work/experiments/fresh_netlist.xml /candidate/project/rc_filter.kicad_sch > /work/experiments/fresh_netlist.stdout.log 2> /work/experiments/fresh_netlist.stderr.log`: exit 0; stdout/stderr empty.
- Python stdlib XML comparison: parsed fresh_netlist.xml and /evidence/work/netlist/design.xml with ElementTree, normalized net membership by ref.pin, compared equality, and combined results with trace_saved.json in crosscheck.json. Memberships match exactly. This comparison and the graph are saved evidence, not hardware measurements.

## Findings supported by original geometry

External contract (inputs/requirements.txt line 1) requires J1.2 input, J1.1 filtered output, J1.3 ground. Native PCB lines 587-603 instead assign J1.1 /IN and J1.2 /OUT. Copper confirms the assignments: segment ae12bf6f-d4ed-46d3-9916-8738850152cc connects (100,100) J1.1 to (104.0875,100) R1.1. J1.2 at (100,102.54) reaches (107.5,102.54), then (107.5,100), meeting the segment between R1.2 (105.9125,100) and C1.1 (109.05,100). This is not merely mislabeled net names.

Schematic J1 is at (60.96,63.5), rotation 180 degrees (line 1093). Embedded symbol pin 1 is local (-5.08,+2.54), pin 2 (-5.08,0), pin 3 (-5.08,-2.54). Transforming to schematic coordinates puts pin 1 at (66.04,66.04), pin 2 at (66.04,63.5), pin 3 at (66.04,60.96). Native wires/labels put IN on pin 1, OUT on pin 2, GND on pin 3. Fresh XML and supplied XML confirm this mapping. Consequently schematic parity shares the connector mistake and cannot validate the external contract. ERC checks passive pin rules, not intended signal direction; netlist and geometry check summaries only inventory the design.

Ground continuity is supported: C1.2 -> F.Cu segment 7deb1fb0-3a34-4321-a0ef-399bdd8439d1 -> via b71a70d7-bbc1-43d1-ae55-5e4d992412a4 -> saved B.Cu zone 38a83012-bcf9-4bb9-b9f4-50d49cd91d90 -> J1.3. All five tracks, seven pads, the via and the zone were traversed. The rectangular board outline spans x=96..116 mm and y=96..108 mm. No additional geometry defect was established. Widths and pad dimensions were inventoried, not used as ampacity or fabrication qualification.

Consequence is conditional electrical reasoning, not a measured waveform: with ideal drive at the contracted input J1.2 and an unloaded output at J1.1, no current flows through R1, so V(J1.1)=V(J1.2). C1 shunts the driven input instead of forming the intended output pole. Finite source impedance and load can change behavior; no such operating conditions are specified or assumed for acceptance. The explicit pin contract already fails independent of those conditions.

## Coverage omissions

- No physical continuity, impedance, AC/transient or thermal measurements; no SPICE/parasitic model validated.
- No source/load/current/voltage/environment limits specified; no ampacity, thermal, EMC, tolerance, transient or ECU qualification inferred.
- No zone refill; connectivity uses the candidate's saved fill only. No alternate fabrication exports, assembly, mating connector, 3D clearance or production DFM qualification reviewed.
- ERC/DRC reports reviewed as supplied, not rerun; their ignored checks remain omitted. No unsupported failure is inferred from those omissions or from tool warnings.

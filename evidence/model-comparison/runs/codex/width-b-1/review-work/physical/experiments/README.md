# Physical review experiment

Scope: every saved copper track segment must have width >= 0.30 mm. This is a geometric comparison only.

Read-only inputs inspected: requirements.txt; project/rc_filter.kicad_pcb, rc_filter.kicad_sch (packet), rc_filter.kicad_pro; supplied work/geometry/geometry.json and execution/console records (packet); work/drc/drc.json, work/erc/erc.json, work/netlist/connectivity.json and native XML netlist (packet); checks/geometry.json. No credentials or prior-review experiments read.

Commands used to locate inputs: `pwd`; `rg --files -g AGENTS.md -g '!home/**' /work /candidate /evidence` (no AGENTS.md found); `ls -la /candidate/project /evidence/work /evidence/checks /work/experiments`. Native locator command: `rg -n -A 9 '^\s*\(segment' /candidate/project/rc_filter.kicad_pcb`. Relevant files were read using `cat`. `kicad-cli version` returned 10.0.7.

Measurement command: `/usr/bin/python3 -B /work/experiments/measure_widths.py`. The script measures every saved segment/arc block directly using decimal widths, independently loads the board via pcbnew, compares by UUID to the supplied geometry, and runs native DRC with schematic parity. See width-measurements.json for versions, exact DRC argv/environment, hash checks, every width, coordinates, UUID and source line. See drc.json and drc-console.log for native results. The candidate is never saved or refilled.

The initial run exited 0 and emitted three KiCad PROPERTY_ENUM assertions and two API warnings from requesting via width without a layer. Via extraction was changed to explicitly request F.Cu diameter, and the script rerun as `/usr/bin/python3 -B /work/experiments/measure_widths.py > /work/experiments/measurement-console.log 2>&1`. Final console captures remaining library diagnostics; segment widths are independently supported by the native text and supplied geometry and do not use the via width API.

Outcome: all five F.Cu segments are 0.25 mm, a shortfall of 0.05 mm (16.67% of the 0.30 mm requirement); direct source, KiCad API and supplied geometry agree. Native DRC reports zero violations, unconnected items and schematic parity issues; that does not enforce the external geometric requirement. The supplied geometry check explicitly says no declared geometric requirement was applied. Project rules are empty. A 0.6 mm via diameter and 0.25 mm zone minimum thickness are distinct object properties and were not counted as track widths.

Connectivity review: /IN connects J1.1 to R1.1; /OUT connects R1.2, C1.1 and J1.2 with a T-junction at (107.5,100); GND goes C1.2 via the F.Cu segment to (113,100), through the via and saved B.Cu fill to J1.3. Saved fill and footprint/edge geometry were inspected; native DRC supplies connectivity/clearance/parity checks. No separate physical fabrication evidence is present.

Limitations: native DRC uses saved zone fill without a refill, and its report lists ignored checks. ERC and netlist were inspected from supplied records rather than rerun. No circuit stimuli, simulation, bench, current/ampacity, thermal, EMI, fabrication tolerance, assembly, 3D/mechanical or environmental qualification was performed. No such ratings or qualification requirements are stated; width alone establishes none of them.

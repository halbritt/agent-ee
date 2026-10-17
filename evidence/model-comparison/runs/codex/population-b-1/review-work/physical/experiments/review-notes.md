# Physical review experiment record

Scope: saved assembly must provide a fitted resistive DC path from /IN to /OUT; no alternate assembly override. Native files under /candidate were read only. All new commands, code, reports and configuration are under /work/experiments, cited as review-work/physical/experiments/.

## Commands and inputs

Initial read-only inspection used `rg --files /candidate /evidence`, `cat` on requirements, the supplied check JSON, execution JSON, native project settings and netlist, and `rg -n` / `nl -ba` / `sed -n` on the native schematic and board. No credentials or previous reviewer experiments were read. The native board and schematic contents were independently parsed by audit.py. Useful original locators: schematic lines 1252–1274; PCB lines 240–244, 301–305, 359–373, 587–613 and 667–738. Project settings were inspected for configured rule severities, minimums and exclusions.

Reproduction: `/usr/bin/python3 /work/experiments/audit.py`. Exact subprocess arguments, exit statuses, environment overrides and before/after SHA-256 values are in commands.json. KiCad CLI is 10.0.7; Python is 3.12.3. Fresh ERC, schematic-parity DRC and native XML export were run directly against /candidate, with reports written here. No save or zone refill was invoked. All commands returned 0; parsed reports, rather than exit statuses alone, show zero violations, unconnected items and parity issues. Candidate schematic, board and project hashes were unchanged.

## Results and calculations

Both native schematic `(dnp yes)` and native PCB `(attr smd dnp)` designate R1 (10k) unpopulated. C1 and J1 are populated. The fresh XML net membership matches every native board pad net. R1 is the only resistor, with pad 1 on /IN and pad 2 on /OUT. Native segments route J1.1 (100,100) to R1.1 (104.0875,100); R1.2 (105.9125,100) to C1.1 (109.05,100); and J1.2 via (107.5,102.54) and (107.5,100) to that /OUT segment. The T-junction lies on the horizontal /OUT segment. No segment bridges the R1 pads. The sole zone is saved B.Cu GND. C1.2 routes to the GND via at (113,100); its return to J1.3 uses the saved GND fill. Native saved-fill connectivity is also covered by the zero-unconnected DRC result; no return impedance qualification is implied.

R1 has aligned, unrotated 1.025 mm wide roundrect pads, center separation 1.825 mm. Facing copper edges at the common centerline are separated by `(105.9125 - 104.0875) - (1.025 + 1.025)/2 = 0.8000 mm`. This confirms that the empty land pattern does not supply a copper bridge. This is a geometry calculation from the saved file, not a manufactured measurement.

Native-topology.json records the population, pads, segments, zones and DC graph result. Each routed net is one graph node; only a populated resistor would add a resistive inter-net edge. A capacitor supplies no specified resistive DC path, and distinct header contacts are not internally bridged. There are zero fitted resistor edges, and /OUT is unreachable from /IN. The result directly fails the stated criterion without assuming a stimulus voltage, load, component rating or parasitic leakage model.

## Verification coverage and limitations

The supplied ERC/DRC results and independent repeats are consistent: both nets are properly routed to their own pads and the DNP state agrees across schematic and PCB. Such checks do not require a populated resistor between different nets. Native XML includes the DNP resistor as a component with a dnp property; counting it as a conducting device would invalidate an assembly continuity test. The supplied netlist and geometry checks report DNP correctly but apply no fitted-path assertion or electrical stimuli. No separate verification defect is alleged beyond this coverage omission.

The supplied geometry console contains PROPERTY_ENUM assertion diagnostics despite successful extraction. Its DNP and coordinate claims were cross-checked using independent native parsing and a fresh netlist; no finding relies on treating exit success as electrical evidence.

Omissions: no built-board resistance measurement, SPICE transient/AC analysis, parasitic extraction, zone refill, Gerber/drill fabrication audit, assembler BOM/position-output audit, part-specific mechanical fit, solder process qualification, ampacity, thermal, environmental or ECU qualification. No operating current, voltage, copper weight, thermal environment or manufacturer part ratings are specified. Track width alone is not used to infer any of those properties. ERC and DRC retain the candidate's documented ignored checks; those checks have not been independently enabled. The graph audit is limited to this three-component design and unrotated footprints; it is not a general copper-geometry solver. Native segments, pad geometry, saved GND fill and DRC were separately inspected to support its routed-net premise. No external wiring or accidental solder bridge is an allowed assembly override.

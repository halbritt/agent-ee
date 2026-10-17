# Physical initial review record

Candidate: 69119fab9d25319b3bf376eca634b13104ba76b7417416b85a2a64a4e78a6ab4
Run: bafd7961-39b9-4a34-9b39-b7f108e82f07
Packet SHA256: 7443dc72bd7dc0c25527bd0f1550b7d74a74454d412e30e18634608d420b4731
Role: physical

## Commands and versions

- Reconnaissance: `rg --files` under /candidate/project libraries; read project fp-lib-table, rule_severities, rules, and drc_exclusions in Polygonus-Universal-Base.kicad_pro. Read full /evidence/work/drc/drc.json, erc/erc.json, geometry/geometry.json and netlist/design.xml and connectivity.json using Python JSON/XML parsers. No credential files or other reviewers' experiments read.
- `python3 /work/experiments/run_checks.py`: commands.json records full argument lists, environment overrides, exit codes and log files. KiCad CLI 10.0.7; Python version is in command-1.log. All exports read the candidate directly and write only experiment artifacts.
- First independent DRC used a fresh KiCad configuration without a global footprint table. It reproduced the geometric errors, but generated 129 missing-library warnings instead of the supplied report's mix of library mismatch/issues warnings. This is a configuration coverage difference; it is not evidence of a changed board or electrical defect.
- Repeated DRC after copying only fp-lib-table and sym-lib-table from /evidence/work/drc/kicad-config/10.0 to the experiment configuration. Full command and result are in configured-drc-command.json and configured-drc.log. Result matches supplied report: 142 violations, 114 parity issues, zero unconnected items. All 13 error-level results are excluded. The 13 results are 5 padstack + 5 annular width + 3 starved thermal. KiCad's zero exit code does not mean these checks passed.
- `/usr/bin/python3 /work/experiments/physical_probe.py > /work/experiments/physical-probe.log 2>&1`: pcbnew 10.0.7 reads saved PCB without refill or save. Source, calculations and assumptions in physical_probe.py; measurements in physical-measurements.json; saved ground copper details in saved-thermal-copper.png. pcbnew startup emits PROPERTY_ENUM assertions; it loads the board and returns measurements. Geometric DRC errors are reproduced by CLI independently of this Python probe.
- `kicad-cli pcb export drill --excellon-separate-th --generate-report ...` via run_checks.py: drill report and separate PTH/NPTH Excellon outputs are in drill-report.txt and drills/. No copper refill performed.
- Byte comparison confirmed uppercase sheet alias and lowercase original VR conditioner files are identical in this candidate. This does not adjudicate a separate original checkout.

## Measurements / bounded conclusions

- Board SHA256 4e059d571e0f46911d9101b86c8178298cefb866e2f19d5295a30c35dadedd53 matches supplied checks.
- Independently exported schematic contains 659 distinct (reference,pin) net assignments. All exist on PCB with identical net names; no extra numbered PCB pins. Repeated copper pads with the same pin number are not separate schematic pins. This check shares the native pin numbering and does not prove a part's pinout is correct.
- J1's five unnumbered holes are PTH, with pad diameter equal to drill diameter: 3 x 4.0 mm and 2 x 2.2 mm. Radial annulus = (pad diameter - drill diameter)/2 = 0 mm. Native minimum annular width is 0.1 mm. Drill export places all five holes into PTH file, tools T12 and T10, respectively; NPTH file has zero holes. No conclusion made about intended post tolerances or manufacturer acceptance.
- Ground zone bc540b8b-3ffa-4b00-b211-3a91191a1f6d has saved F.Cu reliefs with one resolved spoke at C15.2, U7.4 and C14.2 where native minimum is two. Each also has a 0.25 mm routed ground connection leading toward a nearby ground via. The defects are native geometric rule violations; no open ground, excess temperature or field failure is established.
- Both 120 ohm CAN2 shunts R36/R37 are DNP and excluded from BOM and placement on saved PCB and schematic. Both span the same /CAN2+ and /CAN2- nets. They contribute 0 S conductance when absent. Ideal optional population arithmetic: one 120 ohm shunt = 120 ohms; both populated = (1/120 + 1/120)^-1 = 60 ohms. This arithmetic excludes transceiver leakage, protection devices, host-module termination and external harness loads. It is not a waveform simulation.
- CAN2 routes are about 252.43 mm aggregate track length for CAN2+ and 252.40 mm for CAN2-, each with four vias. Aggregate length includes branches and is not an end-to-end transmission-line length or proof of controlled impedance.
- Native PCB contains power plane /12V_MR_FUSE and GND zone, and heater-negative routes. Route widths/lengths and via counts recorded to challenge geometry-only conclusions. They do not establish ampacity, ground impedance, allowable heater current, transient behavior or temperature.

## Coverage omissions and limitations

No zone refill; no hardware resistance or waveform measurement; no CAN traffic, termination sensitivity, bus rate, rise-time, cable or host-module population sweep; no assembled host/wideband module internals established; no 3D connector fit, module standoff clearance, manufacturer drill tolerances or fabrication approval. No component datasheet pinout audit, ignition energy, wideband stability, EMC, automotive transients, thermal simulation, simultaneous output-current/duty-cycle qualification or fuse coordination. No integrated FOME-MC verification. The donor check is not original as-received checkout validation.

ERC errors were read but not all adjudicated electrically: external supply flags, a floating fault output, and two power-output-typed sensor supply pins cannot independently establish faulty behavior from ERC alone. Library drift, unavailable custom libraries and largely field-level schematic parity warnings were inspected as coverage limitations, not automatically converted to functional defects. Ignored native checks include missing courtyards and PTH/NPTH inside courtyards; copper-edge minimum is configured as 0 mm. Therefore the check set does not establish mechanical keepouts or an application-specific edge-clearance requirement. No new such requirement was imposed.

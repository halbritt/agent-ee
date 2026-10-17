# Circuit review experiment record

Scope: the fixed ideal unloaded 1 V step and saved-board width requirements in
inputs/requirements/fixture-requirements.md, items 1 and 2 (lines 7–18).
No ECU, active-device, external-interface or manufacturer specifications assumed.

## Commands and tools

Executed `python3 /work/experiments/review.py` from /work. The script, independent
deck and all results are retained in this directory. commands.json records exact
subprocess argv, working directory, environment overrides, return codes and
stdout/stderr artifacts. Versions: Python 3.12.3, KiCad CLI 10.0.7, ngspice-42
(kicad-version.stdout and ngspice-version.stdout). No network references needed.

Initial read-only inspection used `rg --files -g AGENTS.md -g '!home/**' /work
/candidate /evidence` (no applicable instruction files found), directory listings,
`cat` of the native requirements/project configuration and fresh check reports,
`rg -n` of native component/pad/segment/label declarations, and `sed -n` of
schematic lines 725–855 and PCB lines 667–740. These reads were followed by the
complete native s-expression parse in review.py. Fresh check execution records,
ERC/DRC reports, XML netlist, connectivity JSON, geometry JSON, stimulus/control
decks and ngspice log were read directly under /evidence/work and /evidence/checks.
No credential files or prior-reviewer experiment directory was read.

## Connectivity and native checks

Original schematic: inputs/project/rc_filter.kicad_sch, C1 UUID
1dcaf7b4-dc79-5865-a3a4-db0c3eaad5d8 (lines 934–1011), R1 UUID
c5c38e1a-7593-5c99-b971-2f9d2e7bef78 (lines 1253–1330), J1 UUID
465541a7-d06f-5282-b75f-15b79e4182a2 (lines 1092–1172), wires/labels
725–853. Fresh XML export native.xml agrees semantically with
work/netlist/design.xml (/export/components and /export/nets).
R1.1=IN, R1.2=OUT, C1.1=OUT, C1.2=GND, J1.1=IN, J1.2=OUT, J1.3=GND.
PCB pad mappings agree. No DNP passive element exists. J1 is correctly omitted
as a simulation element for the specified ideal external source/unloaded output.

Rerun erc.json and drc.json reproduce zero native violations; DRC also has no
unconnected items or schematic-parity issues. Saved ground return is via C1.2,
F.Cu segment 7deb1fb0-3a34-4321-a0ef-399bdd8439d1, via
b71a70d7-bbc1-43d1-ae55-5e4d992412a4, B.Cu zone
38a83012-bcf9-4bb9-b9f4-50d49cd91d90 and J1.3. Zone not refilled.
This is connectivity evidence, not a hardware measurement.

Native PCB inputs/project/rc_filter.kicad_pcb lines 667–706 has five segments,
each width 0.25 mm. Independent full-file parse confirms no additional segment
or arc below the 0.20 mm requirement. analysis.json records UUIDs and widths.
Geometry-probe's original console contains enum assertion messages, but native
widths and fresh DRC independently support the declared geometry result.

## Transient verification

work/rc-step/deck.cir lines 2–7 matches native C1=100n, R1=10k and topology.
VSTEP is PWL(0 0 1n 1), not a DC-biased operating point; original initial
solution and first raw row both have IN=OUT=0. No load or omitted active part.
The 1 ns ramp is an adequate numerical approximation here: exact first-order
ramp formula gives 0.6321203748887758 V at 1 ms, only 0.18394 microvolt below
an instantaneous 1 V step. Formula for t>=tr:
Vout = 1 - exp(-t/tau)*expm1(tr/tau)/(tr/tau), tau=R*C=0.001 second.
Ideal step gives 0.6321205588285577 V at 1 ms and 0.9932620530009145 V at 5 ms.

Read work/rc-step/result.raw directly as real binary doubles, checking the header,
1025 rows, byte length, monotonic times and 5 ms final time. Header retained in
supplied-raw-header.txt. Linear interpolation reproduces the actual measurement:
0.6321201340170995 V at 1 ms; final output 0.993262119728839 V at 5 ms.
The maximum sampled error versus exact 1 ns ramp after the edge is under
0.763 microvolt. These values support the result independently of process exit.

Independent numerical experiment independent.cir derives R/C values from fresh
native XML, uses a 1 ps rise and 0.1 us maximum timestep, no load, and a zero
initial operating point. independent.log reports 50029 rows and
vout_1ms=0.6321206 V, vout_5ms=0.9932621 V. This independently generated model
checks the original simulation; it is not a modified candidate or a proposed fix.
1 ms measurement passes [0.62,0.65] V. 5 ms has no stated acceptance threshold.

## Outcomes and coverage limits

No evidence-supported defect or unresolved concern under the stated requirements.
analysis.json records unchanged SHA256 for native schematic, PCB, project
configuration and requirements before/after all experiment checks.

Only the ideal unloaded response from initially discharged C was evaluated.
Tolerance, temperature dependence, parasitics, loading, ADC sampling, EMI and
hardware behavior are not covered. No manufacturer part/rating was supplied, so
real component voltage/power/derating suitability cannot be established. No
shutdown, reset, brownout or arbitrary initial-charge test requirement is stated;
these are not added acceptance criteria. No hardware measurements made.

Saved zone fill only; no refill. ERC ignored checks: single_global_label,
four_way_junction, simulation_model_issue, footprint_filter. DRC ignored checks:
missing_courtyard, track_not_centered_on_via, tuning_profile_track_geometries,
footprint_filters_mismatch, footprint_type_mismatch. These exclusions are present
in original and rerun reports; zero issues is limited to enabled checks.

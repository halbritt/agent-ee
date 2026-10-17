# Physical initial review

Scope: only the stated ideal shutdown (C1 initially 1 V, IN grounded at t=0 relative to C1 ground, OUT unloaded, OUT <= 10 mV at 5 ms). Candidate files were read only; no board save or zone refill. No external references fetched and no credential files read.

## Commands and records

- Discovery/read commands: `pwd`, `rg --files -g AGENTS.md` restricted to /work, /candidate and /evidence; `ls -la /candidate/project /evidence/work /evidence/checks /work/experiments`; `cat` requirements, project settings, native check JSONs, netlist connectivity, geometry console; `rg -n` native component values, pads, nets, tracks, via, zone, and outline. No AGENTS.md was found.
- Versions: Python 3.12.3, KiCad CLI and pcbnew 10.0.7, ngspice 42. Full version command results: execution.json.
- Primary reproducible experiment: `/usr/bin/python3 /work/experiments/physical_review.py > /work/experiments/physical_review.stdout.log 2> /work/experiments/physical_review.stderr.log`. Reads native schematic values using S-expressions; compares board values and pad assignments with original XML export; probes saved connectivity without refill; calculates exponential decay; generates and runs two ngspice decks. Commands, exit codes and hashes: execution.json. Simulators only receive scratch decks, not a modified candidate.
- First driver attempt exited 1 because the Python binding requires `GetUnconnectedCount(False)`; preserved in probe_attempt1.stderr.log. Driver corrected to the installed API, and via width read with explicit layer. Second attempt exited 0. This scripting error is not a native design defect.
- Connectivity API exploratory command loaded the board, called `GetConnectivity().Build(b)` and printed `GetConnectedItems(i)` for all pads, tracks and zones. Output: connectivity_api_probe.log. `GetConnectedPads` reports direct touching pads (hence empty for separated pads); `GetConnectedItems` gives copper-connected groups, including saved fill. No disconnection inferred from the former.
- Explicit copper group assertions and geometry observations: `/usr/bin/python3 /work/experiments/copper_groups.py > /work/experiments/copper_groups.stdout.log 2> /work/experiments/copper_groups.stderr.log`. Results in copper_groups.json. These checks use KiCad's connectivity engine and are not independent manufactured-board measurements.
- KiCad Python emits PROPERTY_ENUM assertions at import, also present in supplied geometry console. Probes completed and agree with native text and fresh DRC; these warnings are recorded, not silently reclassified as design violations.

## Outcomes and verification coverage

Saved PCB has three distinct connected copper groups matching the schematic. C1 ground returns through F.Cu track, through via, B.Cu saved fill and J1.3. R1 is the only resistor and discharge path with IN clamped to ground. Geometry inspection found no additional evidence-supported defect. Fresh ERC and saved-fill DRC records contain no violations, no unconnected items and no parity differences; ignored rule lists were read. These records do not include shutdown stimuli, initial charge, transient analysis or a voltage/time assertion. Netlist and geometry outputs are descriptive, not functional tests.

Analytical result: tau = 100 kOhm * 100 nF = 10 ms; V(5 ms) = exp(-0.5) V = 0.60653066 V; crossing 0.01 V at 46.0517 ms. Maximum tau compatible with the deadline is 1.085736 ms. Initial resistor current and power are 10 uA and 10 uW, capacitor energy 50 nJ; these are ideal exercise calculations, not component/trace thermal qualification.

Ngspice uses an explicit zero-volt source at IN, C1 IC=1 and `.tran ... UIC`, with no OUT load. This prevents a DC operating point from removing initial charge and tests grounded IN rather than open-circuit shutdown. Both 1 us and 0.1 us maximum steps give Vout(5 ms)=0.6065307 V and crossing=46.0517 ms (see shutdown.log and shutdown_fine.log). Analytical calculation and simulator support a major failure of the stated shutdown criterion despite successful native checks.

## Omissions and limits

No hardware, Gerber/drill fabrication audit, assembly trial, real part models, component tolerance/voltage/temperature dependence, leakage, ESR/ESL, trace parasitics, thermal or ampacity qualification, enclosure/connector mating review, EMI/EMC, or reliability testing. None is required to establish this ideal exercise failure; part models, ratings and fabricator rules were not invented. No zone refill performed; only saved copper evaluated. Ignored ERC/DRC categories remain omitted as listed in work/erc/erc.json and work/drc/drc.json. No unstated geometric criterion applied. Native checks were inspected rather than redundantly rerun. Ideal analytical and hand-mapped SPICE models share saved values/connectivity, cross-checked against native schematic/PCB and exported XML; they do not validate physical components.

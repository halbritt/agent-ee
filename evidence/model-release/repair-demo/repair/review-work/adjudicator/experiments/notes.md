# Adjudication evidence audit

Command: python3 standard-library script executed from /work; Python version recorded in audit.json. The script reads only the named requirements, schematic, PCB and fresh check files; no credential files are read. Native inputs were not changed.

Method: SHA-256 native requirements and KiCad files; enumerate all top-level native PCB segment widths; inspect populated 10k/100n properties in schematic and PCB; compare exported pin connectivity to the series-R/shunt-C topology; inspect ERC and saved-board DRC arrays; extract ngspice logged measurements; calculate 1-exp(-1). Assertions all passed. Exact commands and script are retained in commands.txt and audit.py.

Sources and locators: inputs/requirements/fixture-requirements.md requirements 1 and 2; inputs/project/rc_filter.kicad_sch populated R1/C1 properties; inputs/project/rc_filter.kicad_pcb five segment blocks; work/netlist/connectivity.json components and nets; work/erc/erc.json sheets[0].violations; work/drc/drc.json violations, unconnected_items and schematic_parity; work/rc-step/ngspice.log Measurements for Transient Analysis.

Assumptions: ideal 1 V input step, initially uncharged capacitor, unloaded output. R=10000 ohms, C=100e-9 F, tau=1 ms. The 1 ns rise in work/rc-step/deck.cir is negligible against the declared 1 ms sample; the measured/analytical difference is under 1 microvolt. The 5 ms measurement has no bound.

Outcome: all five native tracks measure 0.25 mm >= 0.20 mm. Logged Vout(1 ms)=0.6321201 V is within 0.62-0.65 V and agrees with the analytical response. Fresh ERC/DRC reports have empty issue arrays. Both initial reviews and the packet findings list contain zero findings, so the decision array is empty.

Limitations: fresh checks were inspected rather than rerun. This short audit does not independently extract schematic geometry connectivity or prove saved zone polygon connectivity; the native netlist export and saved-board parity/DRC provide those checks. Zone fill remains as saved. No tolerance, loading, temperature, ADC, EMI, ratings, hardware or manufacturing qualification is claimed. Reviewer agreement is not used as proof.

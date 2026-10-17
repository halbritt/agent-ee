# Adjudication record

Candidate: 1b342471005bae6c5d2029e39c033963daf85c8e69e3436446f63daf5b962f05
Run: 1c24510a-0c7d-4b4b-82e5-6d7aba87f11c

No findings were submitted in either the top-level findings array or either initial review. Consequently there are no finding IDs requiring a disposition. The decision array is empty. This conclusion uses native and executable evidence, not the number of clean reviews.

## Commands and results

Initial read-only discovery used `pwd`, `rg --files -g 'AGENTS.md' -g '!home/**' /work /candidate /evidence /experiments`, `ls -la /candidate /evidence /experiments /work`, `ls -la /work/experiments`, and `rg --files /evidence/checks /experiments/physical /experiments/circuit /candidate/project`. No AGENTS.md was found. Only directory names were enumerated under /work; no credentials were read. Read the requirements, supplied deck, ngspice log, ERC report and DRC report with `cat`. All subsequent calculations, native parsing, native netlist export, version probes and their outcomes are reproducible with `python3 /work/experiments/adjudicate.py`. See review-work/adjudicator/experiments/commands.json for exact executable arguments, versions, exit codes and tool output; review-work/adjudicator/experiments/audit.json for results; review-work/adjudicator/experiments/independent.xml for the new native netlist export. Audit script exited 0. Native schematic and board hashes match the packet.

## Evidence and conclusions

- inputs/requirements/fixture-requirements.md, requirements 1 and 2: the actual limits are 0.62–0.65 V at 1 ms unloaded and 0.20 mm minimum track width. No 5 ms limit or hardware qualification criterion is declared.
- inputs/project/rc_filter.kicad_sch, populated symbols C1 (UUID 1dcaf7b4-dc79-5865-a3a4-db0c3eaad5d8) and R1 (UUID c5c38e1a-7593-5c99-b971-2f9d2e7bef78): 100n and 10k, both dnp=no. Fresh native export confirms R1 between /IN and /OUT, C1 between /OUT and GND. All seven board pad net assignments match the native export and work/netlist/connectivity.json.
- inputs/project/rc_filter.kicad_pcb, all five segment blocks (UUIDs recorded in audit.json): each width is 0.25 mm. This independently satisfies geometry. work/geometry/geometry.json agrees. Assertions printed in work/geometry/console.log do not establish an incorrect width result; the widths were independently read directly from the native board.
- work/rc-step/deck.cir, lines 2–7: nominal low-pass elements, zero-to-one-volt input with 1 ns rise, no external output load. work/rc-step/ngspice.log, Initial Transient Solution and Measurements for Transient Analysis: initial OUT=0, measured Vout(1ms)=0.6321201 V. Native values give tau=1ms and ideal-step Vout(1ms)=0.6321205588 V. The small finite-rise/numerical difference does not affect compliance.
- work/erc/erc.json, sheets[0].violations: empty. work/drc/drc.json, violations, unconnected_items and schematic_parity: all empty. checks/erc.json, checks/drc.json, checks/geometry.json and checks/rc-step.json all record execution=ok and outcome=pass; checks/netlist.json records execution=ok and outcome=info. No failing executable criterion was found.

## Assumptions and limitations

No candidate was edited and no zone refill was performed. Native ERC/DRC results are used as supplied, including their explicit ignored categories; these checks were not rerun by the adjudicator. Saved ground-zone connectivity relies on the supplied DRC; no independent copper-connectivity reconstruction is claimed here. No new transient run or binary waveform decode was needed; the supplied log and native topology were checked analytically. Tolerance, temperature, source impedance, external loading, ADC sampling, EMI, component ratings, fabrication and hardware performance remain outside the declared ideal unloaded fixture scope. None of these limitations supplies a violated declared criterion.

# Adjudication evidence record

Candidate: 0b0265f2838c37cacd80169482dacd56a05559c05ccf88cb9aba1dd719bb31a7
Run: be95470d-1098-4730-ba36-4968f4a89eee

The packet and both initial reviews supply no finding IDs. The required decision list is therefore empty. This conclusion follows direct checks below, not a vote of reviewers. No scratch change was made to any native candidate file.

## Commands and versions

Initial discovery used `pwd` and `rg --files /candidate /evidence/work /evidence/checks /work/experiments`; output was truncated because of stock libraries. Targeted reads then used `cat /candidate/requirements/fixture-requirements.md`, `cat /evidence/checks/rc-step.json`, and `cat /evidence/checks/geometry.json`. `mkdir -p /work/experiments` created the output directory. `python3 --version`, `kicad-cli --version`, and `ngspice --version` returned Python 3.12.3, KiCad 10.0.7, and ngspice 42. All these commands completed with exit 0.

`python3 /work/experiments/audit.py` completed with exit 0. The script, all subprocess argv, return codes, tool versions and outputs are saved in review-work/adjudicator/experiments/audit.py and review-work/adjudicator/experiments/audit.json. A verbatim copy of the provided simulation deck was run with `ngspice -n -b -o /work/experiments/ngspice.log /work/experiments/deck.cir`; deck and result log are retained in this directory.

## Evidence and outcomes

- inputs/requirements/fixture-requirements.md, requirements 1 and 2, defines only the ideal unloaded 1 V step window at 1 ms (0.62..0.65 V) and every copper track segment's 0.20 mm minimum width. Its scope limits do not establish additional component rating, hardware, manufacturing or ECU requirements.
- inputs/project/rc_filter.kicad_sch, C1/R1 native symbol instances, confirms populated 100n/10k. Native input hashes match the packet. work/netlist/connectivity.json, /nets, establishes R1 between /IN and /OUT, C1 between /OUT and GND, with J1 exposing those nets. Direct native PCB pad assignments exactly match the export.
- inputs/project/rc_filter.kicad_pcb, all five top-level segment records, each declares width 0.25 mm. The independent parser agrees with work/geometry/geometry.json, /tracks. Geometry console's nonfatal enum assertions do not invalidate the independently parsed width result.
- work/erc/erc.json, /sheets/0/violations, is empty. work/drc/drc.json, /violations, /unconnected_items and /schematic_parity, are empty. Execution records and checks all indicate successful execution. These reports were inspected, not rerun during adjudication.
- work/rc-step/deck.cir, C1/R1/VSTEP/.tran/.meas records, has the expected topology, initially zero source, 1 ns ramp to 1 V, and measurement at 1 ms. Independent nominal calculation gives tau=0.001 s and Vout(1 ms)=0.6321205588 V. Repeated ngspice gives 0.6321201 V at 1 ms, within the unchanged declared window; it gives 0.9932621 V at 5 ms, where no bound is declared.
- work/rc-step/result.raw was independently decoded as 1025 four-variable samples. The finite-ramp analytical response agrees with every recorded output sample within 10 microvolts; the exact maximum error is recorded in audit.json /retained_raw.

## Assumptions and limitations

Analytical and SPICE response assume ideal nominal components, an unloaded output, an ideal voltage source and initially settled zero volts. Tolerance, temperature, loading, ADC sampling, parasitics, EMI, hardware, purchased-part ratings, fabrication and assembly remain unqualified. The saved zone fill is the evaluated state; no refill was performed. ERC/DRC ignored categories remain omissions and are retained in audit.json /reports. Nominal geometric width is not an ampacity guarantee. No new limit or pass was inferred for omitted tests. No external references were necessary for these native-input and numerical checks. Prior reviewer experiments were not used as proof.

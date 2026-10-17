# Adjudication record

Candidate: 7a48855eff011bbbb0e59bd670ca885cfd820dde585082a2f1a4afd688b35174
Run: d460fe18-d4d9-49fc-8947-a43926a68ad4

Executed `python3 /work/experiments/adjudicate.py` from /work. Exit code 0.
The script records exact subprocess commands, exit codes and output filenames in
commands.json. Versions: Python 3.12.3, KiCad CLI 10.0.7, ngspice 42.
All created files are under /work/experiments. Native hashes before and after match.

Read-only inspection commands used `rg --files` to locate candidate, evidence and
prior experiment files; `cat` to inspect requirements, connectivity, fresh check
reports and prior calculation/simulation artifacts; and
`rg -n -A 10 -B 5 'property "Reference" "(R1|C1|J1)"|property "Value" "(10k|100n)"|net "/OUT"' /candidate/project/rc_filter.kicad_sch /candidate/project/rc_filter.kicad_pcb`.
This confirmed the cited schematic value and board routing locators. Prior results
read were /experiments/circuit/{calculation.json,measurement.json,nominal.cir} and
/experiments/physical/{response_calculation.json,ngspice_baseline.log,nominal_baseline.cir}.
These were corroboration only; adjudication calculations and native export were
executed independently. No credential files were read.

## Evidence and interpretation

Requirements line 1 explicitly requests 0.62 through 0.65 V at 1 ms with a switched
ADC and explicitly leaves its sampling capacitance, switch resistance, initial
charge and sampling timing unspecified. Line 2 prohibits unstated requirements.
The criterion is voltage at the stated instant; no persistent band or final-value
settling criterion is inferred from the word "settles".

Schematic lines 944/955 identify C1=100n; lines 1263/1274 identify R1=10k.
The independent export native.xml matches the fresh exported connectivity:
R1 is between IN and OUT, C1 between OUT and ground, and J1.2 is OUT.
Board pads C1.1, R1.2, J1.2 and OUT segments at lines 675 through 698 agree.
No ADC model or ADC timing specification is supplied by this three-component circuit.

Fresh ERC has no violations. Fresh DRC has no violations, unconnected items or
parity issues; enabled/ignored checks remain as recorded. Geometry was extracted
as saved with no numerical geometric requirement. The geometry console assertions
do not supply ADC behavior and did not prevent extraction. No native failing
criterion is present in these reports. ERC/DRC were inspected without rerunning;
saved zone fill was not changed. Passing these checks does not execute the
ADC-loaded transient criterion.

Independent ideal unloaded calculation: tau=10k*100n=1 ms,
Vout(1 ms)=1-exp(-1)=0.6321205588 V. Independent ngspice gives 0.6321206 V.
The simulator uses UIC and a discharged C1; the 1 V source is applied at transient
start. This is an unloaded reference only. calculation.json records symbolic
charge-sharing and finite-resistance differential equations to establish why
the unspecified ADC parameters affect the response, without assigning values.
Thus the evidence establishes neither compliance nor a band violation under the
required connected condition. A numerical ADC simulation would require inventing
missing behavior, contrary to the requirement.

## Decisions

physical/physical_adc_coverage: deferred. It describes material uncertainty about
the actual stated operating condition, not an established defect. Its major
concern remains open until the ADC parameters and switching history are supplied
and the connected transient is evaluated against the unchanged band/time.

circuit/circuit-001: duplicate of physical/physical_adc_coverage. It describes the
same OUT node, missing parameters, charge-sharing mechanism and missing loaded
coverage. The canonical finding retains the material uncertainty as deferred;
neither finding demonstrates an executable defect or native rule violation.
Canonical selection consolidates identical scope, not reviewer votes.

Limitations: no actual switched-ADC transient or bench measurement; no assigned
tolerances, leakage, parasitic, source-impedance or qualification limits. No
candidate modification, repair closure, or claim of loaded compliance is made.

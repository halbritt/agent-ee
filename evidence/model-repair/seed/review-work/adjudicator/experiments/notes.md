# Adjudication record

Candidate: 329f916010187406f161061477ccaa609a7cfa22231e8b51f90b321f35ab1028
Run: 2d02a128-482d-459a-b6ca-047678a963ef

## Commands and tools

Working directory: /work. Initial read-only discovery used `pwd`,
`rg --files /candidate /evidence/work /evidence/checks /work` with filters
for AGENTS.md, requirements, rc_filter, ngspice, deck, connectivity,
design.xml and geometry.json, and `ls -la /work /evidence/checks`.
No applicable AGENTS.md was found. No credential files were read.
`cat` read fixture-requirements.md, the supplied deck.cir, ngspice.log and
connectivity.json. `rg -n` checked Reference, Value, net and width fields
in the original schematic and PCB. Confirmed native locators:
schematic R1 value line 1274, C1 value line 955;
PCB R1 value line 256, C1 value line 107, segment widths lines
670, 678, 686, 694 and 702.

Reproduce the independent audit with:

    python3 /work/experiments/adjudicate.py

The script and audit-results.json preserve the native fields, source hashes,
exported connectivity, calculation, and fresh check summaries. commands.json
records exact subprocess arguments, versions, return codes, stdout and stderr.
Tools: Python 3.12.3, KiCad CLI 10.0.7, ngspice 42. Script exit code: 0.
Final evidence inspection used `cat` on /evidence/checks/rc-step.json,
/evidence/checks/geometry.json, /evidence/work/erc/erc.json and
/evidence/work/drc/drc.json.

## Assumptions, outcomes and classification

Requirement 1 explicitly bounds the ideal unloaded 1 V step response at 1 ms
to [0.62, 0.65] V. The initial discharged state matches the supplied simulation
operating point. Original schematic library pin positions, component positions,
wires and labels agree with the native XML export: R1 connects /IN to /OUT,
and C1 connects /OUT to GND. Original PCB pad assignments agree. Both saved
native files and the XML export specify populated R1=100k, C1=100n.

Thus tau=0.01 s and Vout(1 ms)=1-exp(-0.1)=0.095162581964 V.
This is 0.524837418 V below the declared minimum. The supplied ngspice
measurement is 0.09516251 V. Independent simulation uses the same native
component values with a delayed edge and 0.1 us maximum timestep. It returns
Vout=0.09516254 V and Vin=1 V at 1 ms after the edge starts. Its 1 ns rise
is negligible relative to the failure margin. This resolves the major/blocking
concern as an established defect, not merely an observation or uncertainty.

Accept check/rc-step as the canonical executable failure. circuit/circuit-001
and physical/physical_rc_step have identical applicable condition, failing
criterion and physical cause; duplicate both into that accepted defect.
No votes or assumptions about reviewer reliability are needed. The reported
allowed tau interval (0.95254 to 1.03350 ms) also agrees with calculation.

All five original native track segments are 0.25 mm, satisfying the declared
0.20 mm minimum. Fresh ERC and DRC reports contain no violations; DRC also
has no unconnected items or schematic-parity issues. These results do not
test or override the transient requirement. No acceptance bound exists for
the 5 ms measurement; its 0.3934693 V result is informational.

## Limitations

No candidate file or requirement was modified. The independent deck is an
experiment, not a repair. No refill was performed. ERC/DRC were inspected,
not rerun; their listed ignored checks remain untested. The supplied binary
waveform was not decoded in this adjudication; native topology, textual
measurement log, analytical solution and independent simulation establish
the failure. No reliance on prior reviewer experiment files was necessary.
Tolerance, temperature, loading, ADC sampling, EMI, parasitics, manufacturing
qualification and hardware behavior are outside this fixture's declared test.

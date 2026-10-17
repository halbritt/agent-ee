# Circuit review record

Candidate: 329f916010187406f161061477ccaa609a7cfa22231e8b51f90b321f35ab1028
Run: 09798e99-2197-4e50-8731-974e12ae3215
Packet SHA256: 5dadb5c27917b9a529664a00924e33faa3caebd1f8ef286fa111a0239b7dd631

## Method and reproducibility

Run `python3 /work/experiments/review.py` from /work. The script, all substantive
subprocess argv, working directories, exit codes and stdout/stderr filenames are
recorded in review.py and commands.json. Versions: Python 3.12.3, KiCad CLI
10.0.7, ngspice 42 (version outputs retained). KiCad library variables point to
the candidate's stock libraries; configuration outputs go to experiments.
No network references or other reviewers' experiments were used.

Initial read-only shell commands were `pwd`, `rg --files -g 'AGENTS.md' -g
'!home/**' /work /candidate /evidence` (no matching AGENTS.md), `ls -la /work
/candidate /evidence`, and `rg --files /candidate/project
/candidate/requirements /evidence/work /evidence/checks`. Subsequent `cat`
reads covered requirements, supplied deck/control/log, ERC/DRC native reports,
rc-step check JSON, project configuration and library tables. `rg -n` located
R1/C1 reference/value properties and board segment declarations. These were
file discovery and inspection commands, not electrical verification.

## Outcomes

- Entire native schematic and board parsed directly; values, population flags,
  footprint pin/net identities and track segments recorded in
  native-inspection.json. Candidate hashes match the packet and were unchanged
  after checks. R1 schematic Value is at line 1274 (100k); C1 at line 955
  (100n). Board values agree at lines 256 and 107 respectively.
- Fresh native XML export (native.xml) matches supplied component values and
  nets. Board pad assignments match exported nets: J1.1/R1.1 = /IN;
  R1.2/C1.1/J1.2 = /OUT; C1.2/J1.3 = GND. Generic nonpolar R and C need no
  semiconductor pin/model assumptions. Connector and power flags are correctly
  omitted as circuit elements in this ideal unloaded test.
- Fresh ERC and DRC with schematic parity give empty violation lists, empty
  parity and unconnected lists; see erc.json/drc.json. Their ignored-check
  lists agree with supplied reports; no full coverage of disabled rules is
  claimed. Native board segments at lines 667 through 706 all have width
  0.25 mm, above the stated 0.20 mm minimum. Saved ground fill was used,
  with no refill. Supplied geometry console assertions do not invalidate the
  widths directly confirmed from the native board.
- Verification deck has exactly the native R1/C1 connectivity and values,
  a zero-initialized capacitor via the zero-input operating point, a 0-to-1 V
  ramp over 1 ns, and no load. Its measurements are at absolute 1 ms and 5 ms
  from ramp start. The ramp approximation changes the 1 ms result by only
  about 45 nV compared to an instantaneous step.
- calculation.json records tau = 0.01 s, analytic V(1 ms) =
  0.095162581964 V and V(5 ms) = 0.393469340287 V. Supplied binary raw data
  was decoded as 1025 real four-double rows, with verified variable order;
  independent interpolation gives 0.095162513912 V and 0.393469316277 V.
  Largest sampled deviation from the instantaneous-step formula is below
  50 nV; interpolation introduces a further small error between samples.
- Independent native-derived deck independent.cir, using tighter tolerances
  and 1 us maximum time step, produces V(1 ms) = 0.09516254 V and
  V(5 ms) = 0.3934693 V; see independent.log. Agreement with both analysis
  and supplied raw data establishes a component-value response violation,
  independent of simulator exit status. Requirement 1 is 0.62 to 0.65 V at
  1 ms; no acceptance limit is supplied at 5 ms.

## Assumptions and omissions

Only the stated ideal 1 V step, initially discharged capacitor and unloaded
output were evaluated. No tolerance, temperature, loading, ADC sampling,
EMI, nonideal parasitics, physical component ratings or hardware behavior
were tested. No external interface or part-number ratings are specified.
No additional falling-step, shutdown or precharged startup scenarios were
introduced. Active reset/brownout behavior is inapplicable to this passive
fixture. Board zone refill, disabled ERC/DRC rules, manufacturing process
capability and ampacity were not verified. Candidate was never edited;
scratch simulations reproduce the saved values and do not constitute a fix.

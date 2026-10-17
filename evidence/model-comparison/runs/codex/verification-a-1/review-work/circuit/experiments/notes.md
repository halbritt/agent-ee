# Circuit review experiment record

Scope: inputs/requirements.txt, line 1: ideal unloaded response to a 1 V
step at /IN, C1 initially discharged; /OUT must be 0.62–0.65 V at 1 ms.
Candidate files were only read. No scratch candidate changes were made.

## Commands and versions

Reproduction command: `python3 /work/experiments/review.py`.
Full subprocess argv, working directory and exit status: commands.json;
stdout/stderr files named there. Versions: Python 3.12.3, KiCad 10.0.7,
ngspice 42 (full version output in versions.json and *-version.stdout).

Initial read-only inspection used `pwd`, `ls -la /candidate /evidence
/work/experiments`, `rg --files /candidate/project /evidence/work
/evidence/checks`, `cat` on requirements, check JSON, native ERC/DRC JSON
and connectivity JSON, and `rg -n`/`sed -n` on the original schematic
and PCB. No AGENTS.md was found by the scoped filename search. No
credential files or other reviewers' experiments were read.

## Native identity and connectivity

Original inputs/project/rc_filter.kicad_sch:
- C1 instance at line 934; value 100n at line 955.
- R1 instance at line 1253; value 10k at line 1274.
- Wires/labels at lines 724–854 join R1 pin 1 to IN, R1 pin 2 to OUT,
  C1 pin 1 to OUT, and C1 pin 2 to GND. J1 at 180 degrees maps pins
  1/2/3 to IN/OUT/GND respectively.

A fresh native XML export (native-export.xml, components/comp and nets/net)
matches work/netlist/design.xml and work/netlist/connectivity.json for
all three components and all three nets. Original PCB pads at lines
210, 218, 359, 367, 587, 596 and 605 have the same net assignments.
native-and-check-audit.json records parsed native values, pin assignments,
input hashes and check-record details. Input hashes match the packet.

## Electrical calculation and simulation

The only energy-storage element is C1. KCL gives
`C*dVout/dt=(1-Vout)/R`, with Vout(0)=0. Thus
`Vout(t)=1-exp(-t/(R*C))`; R*C=0.001 s and Vout(1 ms)=
0.6321205588285577 V. This satisfies the sole voltage criterion.

The decks rc-1u.cir and rc-100n.cir use native-exported R/C values and
topology. J1 is an external access connector, with no load added. GND
is node 0. An ideal 1 V DC source for t>=0 plus C1 IC=0 and UIC solves
the exact post-step initial-value problem; the DC charged operating point
is deliberately skipped. No pulse rise time or unspecified source
impedance is introduced. Both runs measure input=1 V and output=
0.6321206 V at 1 ms. The 1 us output (~0.0009995 V) also agrees with
the initially discharged response. A tenfold maximum-step reduction
leaves the 1 ms result unchanged at the printed precision. Full results,
assumptions and analytic comparison: response-results.json.

The first optional probe requested 1 ns, earlier than the simulator's
retained data interval. ngspice exited zero but reported that measurement
as failed on stderr; the driver caught the absent measurement. That
attempt's deck, stdout, stderr and commands are preserved under
initial-probe-attempt/. The valid 1 ms measurement was already present.
The probe was moved to 1 us, and both final runs contain all requested
measurements without measurement errors. This was a reviewer experiment
limitation, not a candidate defect.

## Check interpretation and omissions

Read work/erc/{execution.json,console.log,erc.json} and
work/drc/{execution.json,console.log,drc.json}: zero native reported
violations; DRC also reports zero unconnected items and zero parity
issues. These static checks alone do not establish transient behavior.
They were not rerun; the fresh netlist export and independent electrical
calculation/simulation supply the functional check.

ERC disabled checks: single_global_label, four_way_junction,
simulation_model_issue, footprint_filter. DRC disabled checks:
missing_courtyard, track_not_centered_on_via,
tuning_profile_track_geometries, footprint_filters_mismatch,
footprint_type_mismatch. No claim of coverage for these disabled checks.

work/geometry/geometry.json describes the saved board; its console reports
KiCad PROPERTY_ENUM assertions. Its extracted pad assignments agree with
the original PCB and netlist; the assertions do not establish a native
design violation. Geometry was not independently regenerated, and no
additional geometric requirement was stated. Board zones were not refilled.

No physical measurements, manufacturing qualification, component tolerance,
parasitics, voltage/power ratings, finite output load or source impedance
were evaluated: the requirement explicitly specifies ideal passive
components and unloaded behavior. Shutdown, reset, brownout and additional
input waveforms are unspecified and were not assessed. No active part
models or external references are necessary for this scoped RC analysis.

Outcome: no evidence-supported circuit defect under the stated conditions.

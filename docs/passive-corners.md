# Qualified passive tolerance corners

`passive_corners` is an optional built-in check for one unloaded first-order RC
low-pass. It consumes an earlier native netlist check and refuses every populated
network except one resistor from input to output and one capacitor from output
to ground. Existing recognized connectors, virtual power flags and DNP parts are
recorded exclusions. Native component references, terminals, values and UUIDs
remain in the retained plan. Captured design bytes are never substituted or edited.

The owned [control scope](../fixtures/passive-corners/control.scope.json) passes
its complete envelope. The [defect scope](../fixtures/passive-corners/defect.scope.json)
uses the same nominally passing design with tighter bounds that fail at tolerance
corners. Both use scope/3, immutable requirements and captured qualification inputs.
Run them through the ordinary review command with a new output directory.
Checks-only still leaves independent reviews incomplete.

Declare `netlist_check`, three distinct `input_net`, `output_net`, `ground_net`
names, explicit `step_v`, `stop_ms`, named `measurements`, `qualification`,
`tolerances` and `max_corners`. Every modeled reference needs an independent
relative interval `[lower, upper]` containing zero, with lower greater than -1
and upper at most 1. Explicit `[0, 0]` is required for zero tolerance. Unknown
parameters, extra/missing references and alternate grids refuse coverage.
The qualification JSON must be captured and match the bundled domain and policy
contract, apart from the optional historical source hashes described below.
It becomes immutable under scope/2 as well as scope/3.

The qualified domain is step amplitude 0.1–5 V, stop 5 ms, observation times
0.1–5 ms, zero initial capacitor voltage and endpoint RC products 0.1–10 ms.
The solver uses a 1 ns linear ramp, maximum 1 us step, reltol 1e-6, abstol 1e-12
and vntol 1e-9. At most four unique endpoint corners and one nominal analysis
run sequentially, with eight observations, 30 seconds per analysis, 150 seconds
per ensemble and the enclosing review deadline. There are no retries.

For positive V and t, `V*(1-exp(-t/(R*C)))` decreases with either positive R or C.
Thus independent rectangular endpoint combinations bound this response.
The [owned reference](../fixtures/passive-corners/requirements/reference.md)
derives the conservative finite-ramp allowance. Each simulated corner and nominal
analysis must agree with the analytic solution within `V*(0.0001 + 1e-9/(R*C))`
volts. This check supplies numerical qualification evidence for every analysis;
matching hashes alone supplies no qualification.

`ensemble.json` records the complete expected and completed corner ID sets,
nominal analysis, component provenance and policy. Separate corner directories
retain decks, controls, execution records, logs, binary traces and measured
metrics with oracle/error comparisons. Failure at any corner or nominal run
leaves `complete: false`, unknown execution coverage and no aggregate margins.
Seals and ordinary requirement/check witnesses protect this evidence before
centralized adjudication; reviewer agreement adds no defect authority.

The published V metric retains the measured nominal value and endpoint envelope,
expanded by the largest qualified numerical/ramp allowance. It names extrema
witnesses and preserves a canonical condition containing topology policy, step,
ramp, initial voltage, time, tolerance identity and qualification-input path.
Coupled margins require exact condition and unit identity and distinct domains.
An unbounded extra observation remains diagnostic; at least one bounded
observation is required. Zero tolerance deduplicates to one corner and agrees
with nominal analysis within the same numerical allowance.

Captured qualification inputs describe the supported RC domain and numerical
policy. New records omit `parser_sha256` and `model_sha256`; existing
`agent-ee.passive-corners/1` records may include them as historical provenance.
Those two fields do not affect acceptance. Other unknown fields, changed policy
or changed numerical limits still require explicit requalification. Captured
input files must remain unchanged during a run.

Each ensemble records the installed ngspice executable SHA-256 and version before
starting, and refuses a binary change before or after any analysis. A new run
can use an updated simulator; every analysis still has to satisfy the independent
analytic oracle. Retained evidence identifies the simulator actually used, and
continuing a run requires its recorded runtime. Changes to RC calculation or
parser behavior need the relevant regression checks; unrelated source edits do
not require refreshing a qualification checksum. A policy or domain change must
update the contract and its native acceptance tests.

### 2026-11-06 qualification review (AGENTEE-618)

Historical record: the source-checksum procedure below was replaced on
2026-11-11 by the domain and policy comparison described above.

The stale parser checksum in the shipped fixture was traced to commit
`b383dadbdf89befdec29c56c182e1b26ecb1952d`. Its entire change to `checks.py`
adds `from .kelvin_routing import check_kelvin_routing`. The prior bytes have
SHA-256 `a69d97986da5bd625089aac3046f85a84db7c01db97496a0da44495a34ca5773`;
the reviewed bytes have SHA-256
`23aa47f2acb9a915b019861813f1e8ae36f11ceaa7fa806d788a9e16179120e3`.
All 36 existing top-level function and class definitions are unchanged. The
import registers the Kelvin check; it neither runs a native tool at import time
nor changes the passive-corner handler. The passive parser, netlist interpretation,
stimulus construction and analytic comparison are unchanged by that parser
checksum transition.

AGENTEE-618 separately changes the passive-corner model to observe the installed
simulator, retain its version and digest, and refuse drift. The model checksum
in the qualification fixture identifies that implementation. The native
`test_passive_corners` corpus passed on 2026-11-06: 13 tests in the execution
image `sha256:f3fd32c44e23295b2c28e23babbae25240c734e3d3e124f96699ea420c425bec`.
The accompanying strict gate passed ten modules and 138 tests with no skips.
The tested model SHA-256 is `1e0c65127f378f84be30e4ded4812dd54ac8e1a9b2fcf57cc6602853215fbfa0`;
the retained gate log SHA-256 is `e8b09385d036dcef71aecf6000bfb047d6d69579a79da31cac9d3c5942605fc5`.
A full-suite gate and independent review of the final candidate are still required.
The corpus exercises the control, defect and zero-tolerance outcomes, analytic bounds,
complete ensemble coverage, source-identity mismatch, corrupt traces, empirical
transfer refusal and sealed witnesses. Recorded-adapter tests also cover failed
or malformed version probes, deadline truncation, and binary changes during
identity capture and analysis. Hash equality does not replace these checks.

If the scope carries empirical calibration, numerical corner evidence can remain
as diagnostics but the check returns unsupported/unknown without acceptance
margins. Calibration/2 exact nominal-design identity does not establish that a
bench residual applies across hypothetical component realizations. No interval
composition or physical correction is silently inferred. Bench ingestion does
not support this new observable. Loading, correlated tolerances, temperature,
aging, nonlinear/RLC networks, overshoot and physical validation remain outside
this policy.

Native tests use scripted initial reviewers and adjudication to exercise the
coordinator contract. They establish neither live-model review quality nor
physical validation. Run the focused corpus with:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests /usr/bin/python3 -m unittest test_passive_corners -v
```

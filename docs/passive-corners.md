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
The qualification JSON must be captured and exactly match the bundled contract;
it becomes immutable under scope/2 as well as scope/3.

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

Captured qualification inputs bind the exact corrected ngspice 47 executable SHA-256,
parser/model source hashes and fixed policy. Changing any identity invalidates
old qualification inputs. Requalification requires reviewing the changed semantics,
updating the captured contract, and rerunning the owned native acceptance corpus;
do not merely edit hashes to imply qualification. The policy intentionally pins
one installed binary rather than claiming compatibility with arbitrary ngspice
builds. Other versions/builds need a policy change and demonstrated qualification.

`parser_sha256` covers the entire `agent_ee/checks.py` module, so changes to
native-netlist prerequisites also require this process. If integration changes
that module again, refresh and requalify against the final integrated bytes.

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

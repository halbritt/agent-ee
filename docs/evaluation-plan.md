# Evaluation plan, recorded before implementation

The owner authorizes a reusable CLI review system and disposable fixture repairs.
The real FOME-MC and Universal 112 designs must remain unchanged. Manufacturing
approval, ECU architecture selection, and general redesign are outside this task.

## Independent expectations

Use a first-order passive RC low-pass, grounded capacitor, negligible output load,
ideal 1 V step, and source resistance negligible relative to the series resistor.
With R = 10 kohm and C = 100 nF, tau = RC = 1 ms;
Vout(1 ms) = 1 - exp(-1) = 0.6321205588 V, t50 = ln(2) ms,
and Vout(5 ms) = 0.993262053 V. A declared fixture requirement is
0.62 <= Vout(1 ms) <= 0.65 V. This limit is fixed across the demonstration.
It is an artificial review requirement, not an ECU electrical requirement.

Changing the saved series resistor to 100 kohm, in both schematic and board,
keeps passive connectivity and native rule compliance while reducing Vout(1 ms)
to 1 - exp(-0.1) = 0.0951625820 V. This must be detected as a functional defect.
Restoring 10 kohm must produce a different candidate and fresh evidence that
satisfies the original requirement. The generated simulation must derive its
values and connectivity from KiCad's exported netlist, not from fixture names.
Ideal R/C simulation establishes only this unloaded response; tolerance,
temperature, loading, ADC sampling, EMI and hardware behavior remain untested.

The saved board must also be inspected for actual track geometry, pad/net
identity, and native DRC results. A fixture minimum signal-track width of 0.20 mm
is a declared geometric requirement, not an ampacity or manufacturing guarantee.

## Process challenges

1. Change a requirement, library, model, native file or relevant configuration:
   previous evidence cannot accept the changed candidate.
2. Supply stale reviewer output: reject its candidate/run identity.
3. Missing executable, timeout, invalid JSON, absent/non-finite measurement,
   solver error and incomplete reviewer output must yield incomplete coverage,
   distinct from a successful check which found a design violation.
4. A supported singleton stays visible despite other reviewers finding no issue.
   An unsupported allegation requires an explained rejection or explicit uncertainty.
5. Exhaust work or interrupt execution: retain completed evidence, findings and
   an incomplete result; rerun in a fresh run rather than reusing acceptance.
6. Attempt a write from the review executor to protected inputs: the operating
   system must refuse it. Document limits of the isolation boundary separately.

## Real baseline

Review a captured Universal 112 donor snapshot (clean checkout observed at task
start), with the actual supplied shared libraries and FOME-MC design requirements
identified separately. Missing hierarchy files and module internals are coverage
gaps, not silent pass results. Select a bounded electrical/physical investigation
from the actual native files and retain source locators and uncertainty.
No seeded expected findings are imposed on the real baseline.

## Completion evidence

Record native inputs and versions, exact commands, outputs, source-to-extraction
links, initial agent judgments, adjudication, changes and fresh closure. Summarize
seed detection/misses and valid-control concerns without treating this small
exercise as a hardware correctness benchmark.

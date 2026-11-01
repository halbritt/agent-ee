# Independent capacitor removal evidence

The nominal 25 °C film-capacitor reference now has eight completed removal
sensitivities: all four independent low/high C1/C2 combinations at regulator
inputs of 4.355 V and 5.25 V. All ten existing model predicates pass. This
addresses the full circuit review's concern that the earlier transient varied
both capacitors together. Fresh candidate checks and engineering review remain
required; these observations do not establish physical validation.

Each capacitor scales its five internal vendor capacitances by 0.8 or 1.2 and
the existing 1.0026 nominal temperature transfer. Vendor R/L values remain
unchanged, with explicit zero inductor series resistance. The source limit,
bleeders, C3 sensitivity, loads and host-release sequence remain unchanged.
Each trace covers 80 ms with a maximum 2 µs step. These are model sensitivities,
not guaranteed joint RLC, lot, aging or temperature corners.

| Observation across all eight cases | Result |
| --- | --- |
| Steady regulator output | 3.299676–3.299694 V |
| Peak output | 3.300690 V |
| Minimum input | −0.017950 V |
| Source current | 0–20 mA |
| Source current after disconnection | 0 A |
| Host and reserve currents after release | 0 A |

The first high-input/low-output case reached 73.22 ms before its 400-second
execution limit. Its trace still used normal 2 µs steps. LTspice reported twelve
worker threads while the process had affinity to one CPU. Selecting
`.options threads=1` completed the same case in 3.629 seconds, with the same
trapezoidal solver, tolerances and time limits. The 37,205 overlapping samples
matched the incomplete trace within 8.5e-22 V. Five previously unexecuted cases
then completed in 3.18–3.28 seconds each. Total native time, including the
failed attempt, was 960.064 seconds against the original 3,300-second limit.
The [ADI support response](https://ez.analog.com/design-tools-and-calculators/ltspice/f/q-a/592257/option-to-limit-the-maximum-number-of-threads)
documents this worker setting. The original failure remains in the evidence.

`acquisition_film.prepare(..., removal_evidence=...)` attaches the fixed
manifest to its exact circuit and parts. It preserves the original shutdown
check and adds `film-independent-removal` to the same power-evaluation mappings.
The new check verifies the complete case inventory, native commands, input
pins, outputs and source identities before recomputing each trace. It applies
the original thirteen scalar limits to the extrema across all cases. Failed
values cannot disappear through case selection; changing an input or dropping
a case refuses the evidence.

`analysis/film-removal/summary.json` retains each case and identifies the case
responsible for every reported extremum. `matrix-execution.json` preserves the
initial incomplete run; `matrix-completion.json` records the completed set and
the runtime amendment. The incomplete original case and its completed successor
have separate paths. Run `analysis/film-removal/replay.py` with the generated
inputs root and an output JSON path to reproduce the arithmetic without a new
native simulation. The captured removal assessment describes native replay
and its limits.

The previous package passed checks, judgments and its file-release dimension,
but its full engineering review remained incomplete. The circuit review
identified this removal gap, and the physical review lost its Claude login
after a host credential refresh left the container holding the old file inode.
Neither that package nor the new model results are final campaign acceptance.
Generated repair, part adaptation, the strict gate and frozen G4 remain open.

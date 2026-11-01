# Nominal power applicability decision

2026-11-01; ADP development reference only. This is an engineering assessment,
not file release, physical validation, or a replacement of the frozen G4 cases.

The generated film candidate replaces C1 and C2 with Rubycon
25MU225MB23225 / C3778570, 2.2 uF, 25 V, nonpolar PMLCAP film. Retain the
ADP7104, B0520LW, bleeders, C3, 17 components and 18 placement relationships.
The original ADP preparer retains the Samsung selection. The separate film
preparer generates this replacement from the exact supplier CAD and captured
part record. Its fresh native suite has 37 passing checks and one informational
result. The last independent review admitted 12 of 14 judgments. New
[loop-response evidence](acquisition-loop-applicability.md) addresses its ESR
applicability concern: twelve film cases have 52.2–60.5 degrees of modeled phase
margin after explicitly removing LTspice's added inductor resistance. The
generator can bind this retained investigation to the exact circuit and parts.
It also corrects the ADC supplement binding identified by that review. The new
evidence and operating document still require fresh candidate-bound acceptance.

## Why this change

The previous Samsung evidence does not establish the joint capacitance/ESR
assumptions used in the retained sensitivity. That is an evidence limitation,
not an observed hardware failure. Rubycon explicitly describes capacitance and
impedance as unchanged by DC bias. Its selected part has a tolerance minimum
of 1.76 uF, above the ADP's 0.7 uF minimum. This removes the particular need to
combine separate Class II bias and small-signal curves. It does not turn a
reference model into a product guarantee. See the [MU data sheet](https://www.rubycon.co.jp/wp-content/uploads/catalog-pmlcap/MU.pdf)
and [PMLCAP technical note](https://www.rubycon.co.jp/wp-content/uploads/products-pmlcap/pml-technical-note_en.pdf),
sections 6 and the MU dimension table.

The exact manufacturer's S-parameter data is at 20 C and zero DC bias. A
series-network conversion gives approximately 0.550 ohm at 1.001 kHz,
0.0859 ohm at 10.017 kHz and 0.0224 ohm at 100.254 kHz. The ADP Rev. I Table 2
ESR recommendation is 0.001 to 0.2 ohm without a frequency. Neither selecting
100 kHz nor the MU dissipation-factor specification proves that recommendation
at every frequency. Application acceptance must consider the frequency-dependent
network and regulator behavior. The manufacturer's simulation cautions explicitly
exclude product guarantees and substitution for board evaluation.

The selected MU maximum body is 3.5 by 2.8 by 2.0 mm. The much larger KEMET
2220 C0G option was screened out: its documented land pattern and maximum body
cannot satisfy both existing 3 mm capacitor relationships and non-overlap with
this SOIC in any permitted orthogonal orientation. That exact geometry result
does not reject all C0G capacitors. A polarized polymer option was screened out
for C1 because the declared reverse-input condition can apply continuous reverse
voltage. Its transient reverse allowance cannot authorize that condition.

## Current, rail and thermal assessment

The corrected diode source statement is 0.385 V maximum at **0.5 A**, 25 C;
0.1 A was an attribution error. The conditional monotonic-current inference,
0.385 V allowance and 4.355 V protected-source floor are unchanged. Eight
actual-source tests pass. Preparation changes only the engineering-basis text
and its dependent hash, with all 237 source members retained.

Using the existing 1.015162-ohm interconnect model and the entire 20 mA source
limit gives 20.303 mV drop. Subtracting this from 3.25875 V leaves 3.238447 V,
4.447 mV above the required 3.234 V. This calculation removes the ground-current
allowance from that particular drop estimate; it does not guarantee regulation
or establish a manufactured resistance bound. The production calculation's
16.5 mV allowance is unchanged.

The conservative current budget leaves 8.761 mA for regulator ground current,
including the retained 1 mA engineering reserve. The source-derived 2 mA
allowance remains conditional. The reserve grants no external-load permission.

At 25 C ambient, the continuous input-power ceiling is 0.105 W. The ADP's
167.8 C/W typical small-copper reference predicts 42.619 C junction temperature;
reaching 85 C at that power would require 571.429 C/W. Native KiCad geometry
finds a continuous 1465.659 mm2 bottom ground region contacting all four exposed-
pad via annuli. The front region touching U1 ground and its exposed pad is
14.799 mm2. Drill holes were subtracted conservatively. This supports a nominal
thermal assessment with substantial margin, without asserting that the actual
board has the reference thermal resistance. Stored-capacitor transient energy
is outside the continuous input-power bound.

## Supply removal

The ADP reverse-blocking topology removes the old TPS output-minus-input
restriction. Its independent absolute ratings are VIN -0.3 to 22 V and VOUT
-0.3 to 20 V; the 55 mV switching threshold is typical, not a guaranteed clamp.
For the declared non-sinking source, no independent external drive and passive
loads, capacitor size affects discharge timing. There is no required discharge
deadline. A quasistatic argument keeping both nodes between ground and the
5.25 V source ceiling therefore does not require the old input/output capacitor
ratio. This is a topology-level engineering inference, not proof of dynamic
stability, hot-swap behavior, surge tolerance or the encrypted model's internals.

## Exact experiment and remaining gates

The first local film experiment replaces C1/C2 with the exact Rubycon RLC topology,
scaling all its capacitors by 0.8 while retaining its resistors and inductors.
The ADP model, source, host schedule, C3 and 80 ms observation interval stay
unchanged. This is a declared tolerance sensitivity. A 25 C simulator setting
does not convert Rubycon's 20 C fitted constants into guaranteed 25 C bounds.
The zero-target source can clamp negative VIN and is not physical disconnection.

The single run completed in 265.641 s without timeout or changed inputs. All
eight declared model predicates passed across 40,619 samples. Steady VOUT was
3.299676 V; its full-trace maximum was 3.300806 V. The startup rail first reached
3.234 V at 1.522 ms. These observations support developing the film replacement;
they do not establish a guaranteed ESR corner, physical stability or acceptance.

The dated JLC capture reported 2,094 units at 2026-11-01 09:18:42 UTC. No part
was purchased or reserved. Initial supplier component, footprint-SVG and Pro
catalog requests returned HTTP 403, including a previously working control part.
Subsequent requests carrying the official viewer's Origin/Referer metadata
returned the SVG and full component record. The exact C3778570 source now passes
the existing complete-footprint parser and supplier-provenance reader: ten
records, two rectangular pads and no holes. The failed captures remain retained;
this success does not isolate which request difference resolved the access error.

Supplier CAD admission and generated C1/C2 source preparation are complete. The
film source producer retains the specification, connectivity, other purchasing
selections and all 18 functional relationships. Native assembly, body clearance
and placement checks have evaluated its larger capacitor bodies. The remaining
geometry measurement issues below precede independent candidate-bound electrical
judgments, full review and file-release evidence.
Do not refresh reusable roles or run frozen G4 against this unaccepted candidate.
The package still requires editable native files, views, fabrication outputs,
reproduction and all the original acceptance gates.

The retained engineering record is
`~/.local/share/agent-ee/zeroasic-20261029-agent707/nominal-applicability-r1/`:
`margin-calculations.json`, `copper-observation-r2/output/copper.json`,
`c0g-geometry-screen.json`, `rubycon-source/impedance-observation.json`,
`film-jlc-stock/catalog.json` and `film-model-r1/`. Native copper observations
bind board SHA-256
`2eabd5bc1bd73974359192cc5118478a41773516aac4fe89fc9cb58aa472240b`.
Manufacturer models remain in the local capture and are not redistributed here.

The later supplier admission is retained under
`~/.local/share/agent-ee/zeroasic-20261029-agent707/film-cad-admission-r1/`.
`cad-admission.json` pins all eight prepared source files, including the exact
supplier component and the part record derived from the unchanged stock capture
and historically imported manufacturer PDF. The latter keeps unknown redirect
provenance. These source checks do not establish native placement, CPL rotation
or electrical acceptance.

## Generated film source and disconnected-source experiment

`acquisition_film.prepare` derives `acquisition-control-film-r1` from the exact
237-file ADP source. It selects C3778570 for C1/C2, uses both complete supplier
pads, and binds maximum 3.5 × 2.8 × 2.0 mm bodies in the Python and Rust mechanics
consumers. The generic KiCad 1210 STEP is an illustrative view model; the MU
maximum dimensions govern clearance and height. Raw supplier CAD, PDFs and
manufacturer models remain external captured inputs, not redistributed fixtures.

The second experiment used the generated circuit and full parts hashes recorded
before execution. Its changed hypothesis was a source with zero current after
disconnection, including at negative VIN, and host release completed 499 µs before
source removal began. This replaces the first experiment's zero-target clamp and
concurrent host release. Capacitor models, tolerances and their applicability
limitations otherwise remain as above.

That run completed in 268.044 s with unchanged inputs. All ten model predicates
passed across 40,575 samples: steady VOUT 3.299676 V, maximum VOUT 3.300225 V,
minimum VIN −17.925 mV, and exactly zero sampled source current after removal.
Host current was zero during the declared interval before source removal. The
negative input prediction is a model observation, not a hardware measurement.

The first parser replay rejected the log's failed Newton iteration even though
Gmin stepping explicitly recovered and the transient completed. The parser now
recognizes that exact recovery sequence before completion while retaining fatal,
incomplete-trace and wrong-temperature refusals. No simulation was repeated for
this parser repair. ADI documents these successive operating-point methods in its
[LTspice reference](https://github.com/analogdevicesinc/ltspice-reference/blob/main/ai_ref/SIMULATION-COMMANDS-REFERENCE.md#op--dc-operating-point).

Evidence is retained in
`~/.local/share/agent-ee/zeroasic-20261029-agent707/film-build-integration-r1/`:
`model-r1/` holds the pre-run bindings, execution, unchanged waveform and failed
parser diagnosis; `bundle-r2/` holds 263 admitted source files. These results do
not establish nominal-25 C capacitor applicability, final-board acceptance or
permission to replace the frozen G4 reference.

## Native film build and remaining geometry work

The first build found an exact-feasible placement, then stopped because the
routing caller selected the old TPS pin map for the new film model. The caller
now selects the ADP pin map explicitly. A regression checks those endpoints
against the generated circuit. No placement limit, footprint or net guard was
relaxed. The second build completed through artwork in 469.860 s.

The saved board is SHA-256
`2040f20bd2eda776d37eb39a296bbc2c7429f61ff268aa0cbfe9b7df0cac38b1`.
Its check suite completed in 136.378 s: 36 checks passed, the netlist check was
informational, and the engineering-geometry tool failed. ERC, DRC, schematic
parity and opens were all zero; native placement retained 17 components and
18 functional relationships. The result remains incomplete, with no accepted
file release or fresh independent judgments. All 123 Rust tests and 21 focused
Python tests passed, including the actual captured source and model guards.

The geometry tool stopped at C1.1 to U1.8. A read-only native diagnostic found
that the 0.25 mm trace makes positive-area copper contact with C1, while its
center line lies outside the pad. The existing graph only admits a trace whose
center line enters the pad. A separate diagnostic that admits positive native
copper intersections completed the measurements; it is not a replacement check
or acceptance record.

That diagnostic also found 0.326931 mm2 of the two declared J1-to-J3 ground
strips outside saved bottom ground copper. The J1-to-J2 strip passed. This is a
separate unresolved return-path witness, not proof that the whole ground plane
is disconnected. The measured body gap was 0.494 mm and the estimated rail drop
was 14.301 mV; all five local-loop lengths were within their existing limits.
Next repair the pad/trace measurement and establish an unobstructed return
path with the same width and resistance requirements. Preserve these failed
checks and rerun the corrected candidate through the ordinary gates.

`native-r1-diagnosis.json`, `native-r2/`, `checks-r1/`, and
`geometry-diagnosis-r1/` and `geometry-diagnosis-r2/` retain the observations in
the film-build integration directory above. Manufacturer applicability remains
a separate electrical judgment; these geometric results do not resolve it.

## Native copper contact and ground-corridor repair

The film geometry checker now measures positive-area pad/trace contact even
when the trace center line lies outside the pad. It requires matching native
net and layer identities. For supported convex SMD pads, the measured path
runs from the pad center through a point inside both copper shapes and then
to the trace center line. Gaps, touching edges and wrong nets or layers cannot
create a connection. Unsupported positive contacts stop the check. The prior
film model retains its original checker; the new model binds the new checker,
contact helper and path-basis record by exact hashes.

The two J1-to-J3 ground corridors crossed thermal-relief cuts around U1's
exposed-pad holes. They did not cross the drill holes themselves. Both generated
corridors now detour 0.5 mm left between y=118.8 and y=116.3 mm. Each gains 1 mm
of path length at the original 0.2 mm width. Routing reserves these corridors
before the final native copper check. Connector endpoints, component geometry,
electrical inputs, resistance calculation and numeric limits are unchanged.

On the unchanged saved board above, the corrected checker and corridors give
zero ground-strip area outside copper and an estimated rail drop of 14.432 mV,
below the existing 16.5 mV limit. All other original geometry limits pass.
This is a diagnostic result, not acceptance of a regenerated board. Thirty
focused Python/native tests passed with no skips, including that exact saved
board and source-tamper refusals; all 123 Rust tests passed.

These records are retained under
`~/.local/share/agent-ee/zeroasic-20261029-agent707/film-geometry-closure-r1/`:
`ground-diagnosis-r1/`, `ground-diagnosis-r2/`, `target-measurement-r1/`,
`regressions-r3/` and `planner-build-execution.json`. The failed attempts remain
available alongside the successful tests.

The regenerated candidate completed through artwork in 484.592 s. Its saved
board SHA-256 is
`c38405ad95afe463d4f8baeef38f21ecd3f6f242467ef305a71a48b2ae72fe07`.
The fresh suite completed in 163.572 s: 37 checks passed and one netlist check
was informational. ERC, DRC, schematic parity and opens were zero. All 17
components and 18 final-coordinate relationships passed the placement contract.
The geometry check measured zero ground-strip area outside copper and a
14.432 mV rail drop; all original margins passed. `native-r1/`, `checks-r1/`
and `native-result.json` bind these results to the new board.

The two geometry failures are closed for this generated candidate. Overall
review status remains incomplete while fresh independent judgments and the
full release checks are outstanding. These results do not grant nominal power
applicability or permission to replace the frozen G4 reference.

The first fresh author identified a stale parent-document binding in the ADC
timing supplement. The film generator now makes the current `OPERATING.md`
explicitly adopt that supplement by path and exact hash. The historical
supplement stays unchanged. Its power-up and conversion-readiness corrections
apply to the current host instructions; no new timing requirement or physical
host acceptance is introduced. This documentation repair does not alter the
candidate already under independent review.

The independent verifier subsequently concurred with all 14 author records.
Twelve judgments were admitted; the two records with unresolved findings were
refused. The author completed in 810.301 s and the verifier in 937.316 s, with
no transport retry. The preserved result is `criteria_failed`, not acceptance.

The electrical finding concerns the ADP7104 Rev I recommendations: Table 2
specifies 0.001–0.2 ohm capacitor ESR, while the output-capacitor discussion on
page 17 recommends up to 1 ohm. Neither passage states a frequency. The exact
MU reference model has about 0.55 ohm ESR at 1 kHz. Its favorable retained
transient does not reconcile those recommendations with the regulator's loop
response. This is an unresolved compatibility analysis, not an observed
oscillation or a demand for a universal lifetime or temperature guarantee.

The generated ADC adoption repair passed the 30-test suite with no skips. An
exact input comparison found only `OPERATING.md` changed; the historical
supplement, circuit, selected parts and model evidence are unchanged. The new
operating-document hash is
`57598ff3b4dc3d76d7f69bdd1aa3a2c65f16a380ff184f580c15fa45c94ec12f`.
`bundle-r2/`, `regressions-r4/` and `adc-adoption-verification.json` retain that
verification. The reviewed candidate and its negative judgments remain intact
under `judgments/`.

The follow-up now establishes model suitability with an analytic control and
compares the actual frequency-dependent film network against the data-sheet
references. See the [nominal loop assessment](acquisition-loop-applicability.md)
for all cases, the simulator-default correction, retained failures and limits.
This provides new evidence for a fresh acceptance round; it does not replace
the earlier negative judgment. Full review, file release and frozen G4 credit
remain outstanding.

The subsequent package passed checks, all fourteen independent judgments and
its file-release dimension, while the full engineering review stayed incomplete.
Its circuit reviewer identified an unexecuted independent C1/C2 removal
combination. The [independent removal investigation](acquisition-removal-bounds.md)
now covers all eight low/high capacitor and source-limit cases with the original
electrical limits. All model predicates pass; a new required replay check and
fresh candidate-bound acceptance remain necessary.

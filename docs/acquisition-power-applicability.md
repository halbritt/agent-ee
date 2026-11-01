# Nominal power applicability decision

2026-11-01; ADP development reference only. This is an engineering assessment,
not file release, physical validation, or a replacement of the frozen G4 cases.

The next power candidate is a specific change to C1 and C2: Rubycon
25MU225MB23225 / C3778570, 2.2 uF, 25 V, nonpolar PMLCAP film. Retain the
ADP7104, B0520LW, bleeders, C3, 17 components and 18 placement relationships.
The production preparer still selects the original Samsung capacitors. Supplier
CAD admission and a new generated native build are prerequisites to selecting
the film parts there; their stock listing is not footprint evidence.

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

The local film experiment replaces C1/C2 with the exact Rubycon RLC topology,
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

Supplier CAD admission is complete. Continue with a source-bound generated C1/C2
replacement and fresh geometry/assembly checks. Then obtain independent
candidate-bound electrical judgments, full review and file-release evidence.
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

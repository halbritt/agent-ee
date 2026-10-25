# Differential stripline qualification — 2026-10-25

Status: the bounded nominal stripline capability passed its native, public-workflow
and integration gates. The implementation is integrated with router pin
`95c3baf2dcbc3d38759cf08839f76d71901d28a5` (312 exact runtime files). The acquisition manufacturing release
at `ef2609e2` remains accepted and unchanged.

## Behavior and preservation boundary

The opt-in `router.pair_impedance` operation supports one symmetric,
homogeneous inner signal layer. Both immediately adjacent copper layers must
contain the named reference net and actual saved fill. Explicit original copper
and dielectric thickness and Er are checked before KiCad can insert defaults.
The existing maintained nominal model solves width; native class width remains
a floor and authored rules are preserved.

The final checker measures actual widths, gaps and lengths. Both reference fills
must cover each trace and the complete coupled corridor, including the gap
between pair members. Inner through-hole launches may use only the signal's own
pad plus the smallest explicit zone clearance and a reported numerical polygon
bound. The article's native 0.005 mm polygon error plus 0.001 mm yields 0.006 mm.
An oversized terminal opening or a split elsewhere still fails. The outer-layer
reference invariant has no terminal exception and remains a regression.

The new native article exposed a separate production defect: PCB-only routing
snapshots copied library tables but dropped library directories. The correction
uses the existing project snapshot operation with zero schematic sheets. Native
library references remain intact; no unlinking or DRC severity changes are used.

## Article and engineering evidence

`kicad-route/fixtures/pair_stripline` contains the captured native input, project,
custom rules and real two-pin 2.54 mm header library. Four copper layers are each
0.035 mm, with 0.486667/0.486666/0.486667 mm Er=4.2 dielectrics and 1.6 mm total
thickness. The pair is on In1.Cu, between GND fills on F.Cu and In2.Cu. Two fixed
GND anchors connect both reference planes. Two locked headers provide PTH signal
launches. This is a software article, not an electrical product or fabrication
release.

Agent-selected assumptions are 100 ohms +/-10% nominal differential impedance
and at most 4 mm total uncoupled terminal escape per member. Native 0.25 mm
minimum width, 0.35 mm nominal gap, 0.34–0.36 mm gap rule, 0.15 mm skew and 6 mm
uncoupled rule are unchanged. No owner product requirement is inferred.

The model selects 0.315869 mm width, giving 100.1537 ohms at the measured
0.350001 mm gap. Independent calculations from [TI SNLA187, p39](https://www.ti.com/lit/pdf/snla187)
and [TI SLVA531A, p16](https://www.ti.com/lit/pdf/slva531) give 96.94 and 106.23
ohms for this geometry. Their coupling diagrams use conflicting definitions of
height; both interpretations are retained in `build/stripline/independent-calculation.json`.
Both fall inside the article's nominal tolerance. This agreement does not prove
fabricated impedance, material tolerances or the behavior of pad launches.

## Matched stock comparison

Stock main remains `4cf6d29ddbe7d19b511352d0abbb26842f609be6`, verified by a fresh
`git ls-remote` on October 25. The prior captured stock source and compiled module
were reused after all 14 Rust source hashes and the module hash were verified.
The two routes consume identical native bytes, constraints, 0.025 mm grid and
180-second backend budget, on KiCad 10.0.7 and Python 3.12.3. Stock performs its
own impedance solve; owned synthesis uses the production native pair operation.

| Observed result | Owned router | Stock KRT |
|---|---:|---:|
| Native opens / findings | 0 / 0 | 0 / 0 |
| Independent saved impedance/reference grade | PASS | PASS |
| Copper length | 19.2232 mm | 19.4023 mm |
| Vias | 0 | 0 |
| Coupled length per member | 7.35 mm | 7.6 mm |
| Terminal escape per member | 2.26159 mm | 2.10116 mm |
| Backend time | 3.3795 s | 3.1633 s |

Owned timing excludes preflight synthesis, so these are not comparative speed
measurements. One run does not establish reliability or overall superiority.
Both satisfy this bounded article. Native board hashes are respectively
`aa80eb0207d56ec04bb57cdea8c5d70d231a619d92229643d80dc154cffd17f2` and
`b35469c99fd2d793b628cd08ee2d8fa0e933da3da3b2f8743cfc9cd756d2a574`.
Requests, logs, source checks and native grades are in
`build/stripline/matched-{owned,stock}` and `matched-results.json`.

## Public workflow and integration gates

The four fresh public job/build-producer cases pass (174.521 seconds, no skips).
The saved build candidate has zero opens and native findings and preserves the
captured library dependencies byte for byte. Both missing-reference requests
and missing original lower-dielectric Er reject. The standalone job reports
two baseline missing-library warnings because its temporary board directory
does not include the project libraries; the build producer retains that captured
context and its final native grade is clean. Neither intermediate eligibility
nor this diagnostic stage exercise establishes a circuit manufacturing release.
All 987 required consumer cases across 50 modules have passing evidence, with
no missing inputs or skips. The original 49-module invocation retained three
failures caused by an old expected pin and stale contract documentation; exact
reruns close only those assertions after correction. The original exit status
is retained in `pin-gate.log`, and `consumer-gate-closure.json` accounts for every
required case. All 1,064 router cases also have passing evidence: the complete
smoke invocation passed 1,063 and skipped the native wheel operation because its
runtime environment variable was absent. That exact operation passed in a fresh
70.02-second native rerun. `router-gate-closure.json` retains and reconciles both
results; the original skip is not treated as a pass. Native negative cases cover
either missing reference,
a split beneath the pair, an oversized terminal opening and a split only between
the traces. Original missing dielectric data and unequal/asymmetric dielectrics
refuse before routing. Existing outer-layer cases also pass.

This does not qualify asymmetric, multilayer, single-ended or coplanar impedance,
barrel/via transitions, field-solver accuracy, supplier tolerances or physical
measurements. These remain larger-goal obligations; they have not been excluded
to finish this milestone. No unchanged acquisition build is repeated.

Native signal-layer and In2 reference-layer plots were also inspected. Their
source-board hash, SVG/PNG hashes and bounded observations are retained in
`build/stripline/views/review.json`; plotting did not change the native board.

## Reproduction and artifacts

The supported image was freshly built from the pinned installer as
`agent-ee:exec-83e3434ef151750e`, image ID
`sha256:237d11e64468cba832bbd053d44742316c33a23968ddc48e3652c50d5bb1bb65`.
It contains KiCad 10.0.7, Python 3.12.3 and the owned router 0.23.1. All 103
placement-engine Rust tests passed during its build. `native-image.json` and
`native-image-full.log` retain the observed installation and verification.

From the agent-ee repository, reproduce the public native qualification in a
fresh output directory:

```sh
ae-ci-exec . -- env AGENT_EE_STRIPLINE_EVIDENCE=/opt/agent-ee/build/stripline-reproduction \
  python3 scripts/strict_suite.py --jobs 1 test_placement_job_stripline
```

The test constructs the captured plane/placement request from the installed
native fixture and calls `agent-ee place run`; it also exercises the actual
placement-routing build producer. Retained inputs can be replayed directly:

```sh
ae-ci-exec . -- bin/agent-ee place run \
  build/stripline/public/inputs/job.json --out build/stripline-replay
```

The complete build-producer native candidate is
`build/stripline/public/build-stage/placement-routing/candidate/board.kicad_pcb`,
SHA-256 `3b9ab2f0cbeab853b526dd736875445b207286bbade1e38a3748b825a0caebbf`.
Its sibling project, rules, table and footprint module are retained together.
`native-final.json` independently reports zero findings and opens. The public
job board has SHA-256
`bed21b68b4decab2a5d602295af4a500cdf50d3b00bceebea1d07d5525350fa5`.
No fabrication package is accepted for this interconnect-only article.

Evidence is retained under `build/stripline` and sealed under
`~/.cache/agent-ee-qualification/agent545/pair-stripline-20261025`. The root
`integration.json` records final source revisions and gate identities; the
separate seal manifest binds every retained file and symbolic-link target.
The routing adapter's default request/result schemas, native facts, pour
calculation, refill and mirrored reader semantics remain unchanged. The
PCB-only snapshot correction changes dependency retention, not these contracts.

Next named capability: grouped single-ended bus routing through the normal
placement job and build producer. Its representative article will carry an
eight-bit bus, an unrelated parallel net and a multi-terminal lane. Completion
requires native connectivity/clearance and preservation checks, real failure
rejection, captured stock comparison and the applicable consumer regressions.
Timing matching remains a separate requirement and gains no claim from grouping.

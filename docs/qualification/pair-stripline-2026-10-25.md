# Differential stripline qualification — 2026-10-25

Status: focused native and public-workflow qualification passed; broader integration gates in progress. This document does
not yet declare the capability integrated. The acquisition manufacturing release
at `ef2609e2` remains accepted and unchanged.

## Behavior and preservation boundary

The opt-in `router.pair_impedance` operation is being extended to one symmetric,
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

## Public workflow and remaining gates

The four fresh public job/build-producer cases pass (174.521 seconds, no skips).
The saved build candidate has zero opens and native findings and preserves the
captured library dependencies byte for byte. Both missing-reference requests
and missing original lower-dielectric Er reject. The standalone job reports
two baseline missing-library warnings because its temporary board directory
does not include the project libraries; the build producer retains that captured
context and its final native grade is clean. Neither intermediate eligibility
nor this diagnostic stage exercise establishes a circuit manufacturing release.
The complete router smoke gate, applicable agent-ee pin/integration checks,
trunk integration and final evidence sealing remain required. Native negative cases cover either missing reference,
a split beneath the pair, an oversized terminal opening and a split only between
the traces. Original missing dielectric data and unequal/asymmetric dielectrics
must refuse before routing. Existing outer-layer cases remain required.

This does not qualify asymmetric, multilayer, single-ended or coplanar impedance,
barrel/via transitions, field-solver accuracy, supplier tolerances or physical
measurements. These remain larger-goal obligations; they have not been excluded
to finish this milestone. No unchanged acquisition build is repeated.

Native signal-layer and In2 reference-layer plots were also inspected. Their
source-board hash, SVG/PNG hashes and bounded observations are retained in
`build/stripline/views/review.json`; plotting did not change the native board.

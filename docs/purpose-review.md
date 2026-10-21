# Placement purpose review (AGENTEE-381)

Every sealed review packet includes a `purpose_review` dimension. Every initial
reviewer receives its mandatory brief, including reviewers with custom lenses.
It asks whether the design uses its area well, alongside correctness review:

1. Explain every obstacle, keep-out, lane, fixed pose and advisory rule in the
   captured policy and arrangement. Compare edge and inboard positions, sides and
   orientations to find the least costly way to serve the purpose.
2. Explain every empty field above the review threshold on both F and B: what
   reserves it, and whether that reserve is justified.
3. Examine access directions and external interfaces: cable exit, card insertion,
   antenna use, probe access, LED visibility, mounting and enclosure fit.
4. Name the single change with the best area or routability return, with an
   estimated saving and uncertainty, or explain why no improvement is supported.

The current 100 mm² empty-field threshold is an **agent-chosen advisory review
focus**. It has no sourced acceptance authority. It neither rejects a design nor
prunes placement search, and it is not a minimum useful area. This feature does
not change the placer, its search order or its budgets.

## Evidence and output

The dimension indexes captured inputs by path and SHA-256, check executions and
their evidence, and retained image paths. These indexes survive a zero text
excerpt budget. Reviewers must inspect the complete policy, arrangement, native
geometry measurements and renders through the existing read-only evidence mounts.
Only files admitted to the packet's citation inventory are indexed; peer review
work is excluded. The index does not generate missing measurements or renders.
An image filename alone does not establish board-side coverage or model fidelity.

Reviewers explain justified constraints and reserves in `coverage`. Supported
waste observations use the existing `findings` array beside correctness findings,
with `assessment: concern` and `severity: info` or `minor`. The `reasoning` states
the change, saving in mm² or a named routability measure, calculation or basis,
and uncertainty. An unquantifiable saving stays unknown with the missing
measurement named. Renders supplement structured citations to captured text
measurements; binary images cannot supply fabricated text locators. Missing
renders, measurements, explanations or inaccessible visual evidence belong in
`limitations`, not a clean purpose conclusion. Overlapping reserves must not be
double-counted, and a bounding box alone cannot prove recoverable area.

The central adjudicator retains supported advisories through the existing `noted`
disposition. Missing evidence for a purpose saving is a coverage limitation.
Unsupported claims remain subject to the ordinary evidence rules. Estimated savings
do not become blocking requirements. Existing check defects remain defects, and
the baseline witness and evidence-seal contracts are unchanged. Initial reviewer
independence and the single design writer are unchanged. There is no new review
schema or check kind. This is a review instruction, not a deterministic detector
or a validator that guarantees every reviewer follows the brief.

## Qualification boundary

`tests.test_purpose_review` exercises complete navigation with no excerpts,
independent prompt delivery with custom lenses, missing/link/peer image handling,
and retention of an advisory saving beside an accepted correctness defect.
Its original illustrative card-reserve declarations compare an inboard 600 mm²
reserve with an edge alternative. They are not the pod iteration-7/8k designs and
do not establish that a live model catches that real example. A sealed review of
those two captured candidates, including their renders and measurements, remains
necessary for that qualification. No synthetic model response is evidence of it.

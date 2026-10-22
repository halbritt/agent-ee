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

`board_renders` keys visual coverage to admitted board renders per side.
`purpose_review.render_sides` reads the coordinator visuals record
(`agent-ee.candidate-visuals/1`, AGENTEE-382): view `top` or `front` covers F,
`bottom` or `back` covers B, and inner-layer plots cover neither side. A side is
covered only when the visuals record has `status: available` and the image path
is in the packet's citation inventory. Each uncovered side adds its own
`visual purpose coverage is missing` limitation. Other images, such as a
schematic SVG or a drawn illustration, stay in `images` for navigation and cover
no side. Without a coordinator visuals record, both sides are missing.

Reviewers explain justified constraints and reserves in `coverage`, with each
purpose entry beginning `purpose_review:`. Supported waste observations use the
existing `findings` array beside correctness findings, with `assessment: concern`,
`severity: info` or `minor`, and the optional `dimension: purpose_review`. A
finding with that dimension and any other assessment or severity is refused, as
is one carrying any downgrade marker the validator emits: `downgraded_from`
(any value), `validation_note`, the standard downgrade note in `reasoning`, or
the review's `<role>/<id>: downgraded from defect` limitation. Each marker alone
keeps the history, so a defect the validator downgraded for empty evidence stays
correctness uncertainty even if it is relabelled, stripped of one marker and
revalidated. Review validation, finding collection, adjudication, the run
outcome and `verify` apply the same rule. The `reasoning` states
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
do not become blocking requirements.

Every decision on a `purpose_review` finding records `purpose_status: advisory`
on the finding and its decision event, and the report shows it. A deferred
purpose finding stays visible but does not hold the run outcome at
`defects_or_unresolved`. An undecided one still holds it. Other findings are
adjudicated exactly as before. A deferred correctness concern still holds the
outcome, and a check defect still fails the run. No finding without the
dimension may be marked a duplicate of a purpose finding, so a correctness
concern cannot become advisory that way. The outcome sets aside only a deferred
finding that has the dimension and the advisory status, is a reviewer concern of
severity `info` or `minor`, and has no downgrade marker.

`verify` does not trust these fields in `result.json`.
`purpose_review.verify_records` re-derives them from the retained initial reviews
(`reviews/<role>.json`) and adjudication event files (`events/*adjudication*.json`).
A finding is a purpose finding only when its retained review marked it and that
mark passes the rule above. Verification then replays the writer: it collects
the retained reviews with `collect_findings` and applies each retained
adjudication record with `adjudicate`, in record order. Each purpose finding in
`result.json` must equal the replayed finding as a complete object: every
decision event field (evidence, `candidate_id`, `run_id`, `seq`, disposition,
reason, duplicate target, status), the latest `decision`, `decision_evidence`,
`purpose_status`, disposition, severity, assessment and markers. Any difference,
a record that no longer replays, or a repeated finding id is refused. A
`dimension`, `purpose_status` or event status on any other finding is refused.

Each initial review records purpose coverage in `result.json`
`purpose_coverage`. A role is `addressed` when it has a `purpose_review:`
coverage or limitation entry or a purpose finding. Otherwise it is `unknown`,
and the run adds a `purpose coverage unknown` limitation for it. That limitation
records an unassessed dimension; it is not a pass, and it does not block
acceptance. `verify` recomputes these records from the retained initial reviews
and refuses a result whose `purpose_coverage` is changed or marked `addressed`
without a retained basis, or that omits a required unknown limitation. A
complete run without the record is refused. An incomplete run cannot be
accepted. Before `adjudicate` can complete one, it records the coverage again
from the retained reviews. Captured scope limitations remain a verified prefix of
`limitations`. A run recovered after an interruption records the same coverage
from its retained reviews. The record shows that the reviewer marked the dimension, not that
its purpose reasoning is sound. Existing check defects remain defects, and
the baseline witness and evidence-seal contracts are unchanged. Initial reviewer
independence and the single design writer are unchanged. There is no new review
schema or check kind. This is a review instruction, not a deterministic detector
or a validator that guarantees every reviewer follows the brief.

## Qualification boundary

`tests.test_purpose_review` exercises complete navigation with no excerpts,
independent prompt delivery with custom lenses, missing/link/peer image handling,
retention of an advisory saving beside an accepted correctness defect, per-side
render coverage, advisory status for deferred purpose findings, and recorded
purpose coverage. `PurposeDowngradeTests` reproduces the two-validation
downgrade relabelling and shows it refused. `PurposeIntegrityRunTests` runs
two full scripted reviews with native netlist execution and real adjudication
records. It then shows `verify` refusing forged status, dimension, severity,
assessment, downgrade and event edits, and coverage deletion or forged `addressed`
status.
Its original illustrative card-reserve declarations compare an inboard 600 mm²
reserve with an edge alternative. They are not the pod iteration-7/8k designs and
do not establish that a live model catches that real example. A sealed review of
those two captured candidates, including their renders and measurements, remains
necessary for that qualification. No synthetic model response is evidence of it.

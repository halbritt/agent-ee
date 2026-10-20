# Actual telemetry pod campaign composition (AGENTEE-197)

[`fixtures/telemetry-pod/composition-recipe.json`](../fixtures/telemetry-pod/composition-recipe.json)
records how far the captured telemetry pod preparation gets toward a
[pod driver](pod-driver.md) plan. It was produced by running the existing
validators and owned producer declaration APIs over the read-only agent463
preparation tree. It contains source paths, SHA-256 identities, requirement IDs,
validator outcomes and declarations only, with no design or library bytes.

Its status is `incomplete`. Nothing ran: no scaffold, campaign, placement,
router, check or model. It is not acceptance evidence and does not adopt the pod.

## What composes

| Input | Existing API | Outcome |
|---|---|---|
| Planning and native-v5 scope | `cli.validate_scope`, `stages.declaration`, `compile_baseline` | accepted |
| Spec and capture | `spec_record.verify` | accepted |
| Binding | `binding_fill.configuration(intent='intent.unbound.json', output='intent.json', captures='.')` | accepted; dependency closure from the captured report, all 278 captured |
| Emit and annotate | `schematic_stage.configuration` | accepted |
| Placement | `placement_adapter.validate_declaration` (`agent-ee.placer/1`, captured packing policy) | accepted, declared only |
| Routing | `routing_adapter.preflight`, `validate_declaration` (pin 5f1c072) | accepted, declared only |
| Criterion map | `evidence_index.criteria_map` against the compiled baseline (pre-capture) | accepted |

The captured binding report matches the separate-output form: its input is
`intent.unbound.json` and its output is `intent.json`. That intent is therefore
the writer input, and the bound intent is a coordinator output.

## Exact refusal

`pod_driver.validate_configuration` refuses the actual scope with
`pod stages must be schematic, placement, routing, verification`. The recipe
also lists every other gate independently, so fixing one does not hide the next:

- stage order is `[schematic, verification]`; placement and routing subjects are absent;
- no `generation` marker or `fab_package` done check;
- `spec.md` and `spec-record.json` are not baseline targets;
- no captured `agent-ee.plane-plan/1`, so no `add-planes` step;
- the driver recorded only the write-scaffold pre-receipt, while the actual
  preparation also needed the `envelope-content` producer. AGENTEE-224 adds the
  optional `envelope_content` plan field, which runs that producer as the second
  pre-receipt; the recipe's record of this gap predates it.

Because the configuration is refused, no checks-only dry run was attempted, and
no substitute scope or plan was used. `next_steps` in the recipe gives one
action and owner for each missing input.

## Criteria and retained obligations

Only `POD-AC-3` (ERC and DRC) is a baseline requirement. The evidence-index map
therefore lists it under criterion 3 and leaves criteria 1, 2 and 4 to 9 empty,
which the index reports as not met. A separate accounting map assigns all 48
requirement and prose rows to criteria for planning. It is not coverage.

Every limitation and bench row stays a `NOT CHECKED` or `BENCH REQUIRED` scope
limitation. This includes component suitability (`POD-CD-*`), temperature
(`POD-ME-3`, AGENTEE-209), connector, and antenna and keep-out rows
(AGENTEE-211). The recipe's `open_obligations_all_retained` is `true` only
because none of those rows has a check.

## Product changes found while composing

Composing the actual inputs exposed three contract conflicts, fixed in separate
commits:

- `pod_driver` accepts an optional `placement_adapter` in place of the generic
  `place-board` step, because the campaign forbids combining the two;
- `binding_fill.configuration` accepts a distinct `output`, so the writer intent
  is not also a step output;
- `binding_fill.configuration` accepts `captures='.'` for root-relative libraries.

## Reproduce

The probe scripts are scratch tooling and are not part of this repository. To
recompute the identities, hash the listed files under `sources.root`, and run the
named validators from a checkout at this commit. The inputs must still match
`freeze_sha256` and the tool record.

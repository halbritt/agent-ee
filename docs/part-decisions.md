# Component decisions and deviations

`part_decisions` checks that each captured part choice is present in a populated,
on-board component of an earlier coordinator-produced native netlist. A recorded
substitution must name a replacement that is actually present. It checks exact
`Partnumber` and, when specified, `LCSC` fields; `Value` and the symbol library
name cannot substitute for those fields. `Manufacturer` is retained in the
report. Parts excluded only from the BOM still count; DNP and off-board symbols
do not.

This is presence evidence. It does not establish component suitability, pin
compatibility, footprint correctness, procurement availability, or electrical
and bench acceptance. Use the corresponding checks and independent review for
those obligations.

## Compile the captured table

First capture the owner's source with [spec-record](spec-record.md). Prepare a
reviewed projection with `section`, `decisions`, and `exclusions`. Each decision
has the following shape:

```json
{
  "requirement_id": "POD-CD-GNSS",
  "function": "GNSS",
  "mpn": "NEO-M9N",
  "lcsc": null,
  "symbol_hint": null,
  "footprint_hint": null
}
```

The function must equal the source row's `Function` cell. The MPN and LCSC must
occur in its `Decision` cell. One identity may be null, but not both. Every
source row in the named section must appear in the projection or in the explicit
exclusions array. Multiple choices from one source row are separate entries:
for example, the CAN transceiver, protection device, and termination each need
their own identity. Symbol and footprint hints are information for the writer,
not evidence of native presence.

```sh
PYTHONPATH=src python3 -m agent_ee.part_decisions \
  --spec requirements/spec.md \
  --spec-record requirements/spec-record.json \
  --declaration requirements/part-decisions.declaration.json \
  --out requirements/part-decisions.json
```

The output is `agent-ee.part-decisions/1`, containing the declaration, exact
source rows, locators and source hashes. The compiler refuses an existing output
path. It verifies the source record against the exact source bytes. Each check
recompiles this binding before evaluating the native components.

This is an explicit projection of prose, not an automatic component selector.
Review must establish that every chosen part within a prose row is represented.
An optional decision `normalization` object, with `source_mpn` and `reason`,
records a deliberate normalization such as the source's ESP32-S3-WROOM-1 module
with 8 MB flash to an exact ordering MPN. The source identity must occur in the
Decision cell, and the reason remains visible in every result. The checker does
not establish that the normalized ordering code is correct.

Each source-row exclusion has `requirement_id`, `classification`, `reason`,
and `evidence_refs`. The classifications are:

- `non_part_constraint`: an explicit captured classification that a row is
  outside this identity predicate. It requires at least one captured evidence
  reference (`path` and `locator`). Those files become immutable baseline
  inputs; their hashes and the reason are reported as `outside_predicate`.
- `unresolved_part_decision`: an unfulfilled component choice. It remains a
  `not_evaluated` obligation and makes the result `unsupported/unknown`, even
  when the other projected parts are present. Its evidence array can be empty.

Classification is a reviewed declaration, not a source-text heuristic. A
required part cannot be omitted as an unresolved decision and yield pass.
Review must reject a false non-part classification. The canonical pod source
mixes power architecture, board construction, and parts. A chosen regulator
still needs its selected identity or deviation. Harness accessories require an
explicit scope decision because they are not populated PCB symbols. A pass
covers the declared part identities, never all criterion 2 obligations.

## Check parameters and baseline

```json
{
  "id": "component-decisions",
  "kind": "part_decisions",
  "params": {
    "netlist_check": "native",
    "decisions": "requirements/part-decisions.json",
    "spec_record": "requirements/spec-record.json",
    "spec": "requirements/spec.md",
    "deviations": "requirements/part-deviations.json"
  }
}
```

All paths must be captured. `decisions`, `spec_record`, `spec`, and classification evidence become
immutable baseline inputs. Each projected requirement ID must map to this check
and to the captured spec path. Scope/3 advertises one
`part-decisions:<requirement_id>` predicate per projected requirement.
`netlist_check` must name an earlier `netlist` check. No additional tool runs.
`deviations` may be null; otherwise it names a mutable captured writer input.

## Captured deviations

```json
{
  "schema": "agent-ee.part-deviations/1",
  "deviations": [
    {
      "requirement_id": "POD-CD-GNSS",
      "replacement": {"mpn": "NEO-M8N", "lcsc": null},
      "reason": "Example substitution requiring suitability review, not approval.",
      "evidence_refs": [
        {"path": "requirements/comparison.md", "locator": "GNSS comparison"}
      ]
    }
  ]
}
```

For a source row with multiple selected parts, include `original`, an exact
`{mpn, lcsc}` pair selecting one decision. Ambiguous, duplicate, or unknown
originals are defects. A blank or missing reason, absent evidence reference,
uncaptured or empty evidence file, or absent replacement is also a defect.
Evidence references have captured relative `path` and nonempty `locator` fields;
the report hashes the captured bytes. A locator and a hash make the declaration
inspectable; they do not prove the referenced text supports the substitution.

A declaration applies even if the original part is also present: its named
replacement must exist. Reviewers see all declarations, invalid declarations,
reasons, evidence hashes and matched native component locators.

## Results and limits

`work/<check-id>/part_decisions.json` is normal coordinator evidence, sealed by
the existing evidence mechanism. It records exact netlist, connectivity,
decision-table and deviation-input hashes. Malformed, missing, failed or
unreadable prerequisite evidence stays unknown. Contradictory native field and
property representations are refused by the shared XML reader.

The focused tests use original synthetic native designs with the telemetry-pod
MPN strings, real KiCad exports and the normal capture/seal contracts. They do
not demonstrate actual pod generation or criterion 2 acceptance. This checker
can consume ordinary native fields independently of the named-part acquisition
lane; the actual pod still needs those identities propagated into its generated
design and its source projection reviewed.

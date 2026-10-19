# Captured part bindings

`agent_ee.binding_fill` projects captured part identities into connectivity
intent before schematic emission and forward annotation. It performs no
acquisition or component selection. AGENTEE-174 adds this producer; it does not
establish electrical suitability or actual telemetry-pod acceptance.

## Explicit component references

The input remains strict `agent-ee.connectivity-intent/1`. An immutable
`agent-ee.part-refs/1` sidecar names each non-power component's manifest entry:

```json
{
  "schema": "agent-ee.part-refs/1",
  "components": {
    "can/R9": {"part_ref": "can-termination"},
    "gnss/U1": {"part_ref": "gnss-module"}
  }
}
```

Keys are exact intent component IDs, not reference prefixes, values or inferred
supplier codes. The map must cover every non-power component exactly once and
contain no other entries. Power status comes from resolved captured symbols.
Unknown symbols, mismatched symbol-capture hashes, CAD identities or root-sheet
instance allocations refuse. Pin-accounting errors remain the emitter's
responsibility, so the existing writer repair loop can receive its diagnostics.

A component of a child sheet, including one reused by several sheet symbols,
declares its hierarchical instances explicitly (AGENTEE-252):

```json
{"amp/R1": {"part_ref": "bias-resistor",
            "instances": [{"sheet": "/A/", "reference": "R101"},
                          {"sheet": "/B/", "reference": "R201"}]}}
```

Each instance must be a non-root `{sheet, reference}` allocation of that part
in the manifest, and no instance may be claimed twice. The declared instances
replace the root-sheet rule for that component; without them the root-sheet
rule applies unchanged. Probe features take root-sheet allocations only. The
component's report record then lists its `instances`. The intent is unchanged:
the instances only name where the one bound symbol is placed.

AGENTEE-176 flat composition uses IDs such as `can/R9` and separately preserves
immutable `source_parts` and `references` in each block-instance record. The
caller explicitly maps those IDs to final manifest entries. Source references
do not imply supplier identity. The final manifest CAD declarations must pin the
actual composed library and allocated board references. This producer changes
neither the intent schema nor the block-instance provenance schema.

Missing `part_binding` fields are filled. Every supplied field must equal the
derived value exactly; unknown fields and conflicting values refuse. An explicit
empty `Datasheet` conflicts with a captured nonempty URL. This matters for flat
composition, which preserves empty native source properties: source metadata
must be reconciled explicitly, never dropped to make filling succeed. Existing
native identity/population flags are not writable through bindings.

## Derived fields

The complete central `parts/1` manifest is qualified before projection, including
canonical generic selection or named/external records, captured CAD and all
source hashes. Native properties are:

- `LCSC`, `Manufacturer`, and `Partnumber`, plus the manifest's supported MPN alias;
- `Datasheet`, the captured acquisition URL;
- `PARAM_Datasheet`, deterministic JSON with exactly `schema`, `url`, `sha256`;
- generic catalog `PARAM_*` ratings from the canonical selected row;
- `JLCPCB_Assembly=not_assembled` for a matching declared hand-fit deviation.

`PARAM_Datasheet` uses `agent-ee.datasheet-binding/1`. It requires HTTP(S) URL
syntax without credentials/fragments and a lowercase SHA-256 of the captured
artifact. The producer replays acquisition and binds the exact PDF. The native
part-binding parser validates this reserved field and checks its URL against
`Datasheet`; normal schematic/board parity compares the full record. This
provenance record is not a rating and does not satisfy `require_params` alone.
All other `PARAM_*` numeric interval/set rules remain unchanged.

Manifest property names must be `LCSC`, `Manufacturer`, and one of `MPN`,
`Partnumber`, `PartNumber`. Assembly declarations reuse the existing
[part-deviations contract](part-decisions.md). Multiple matching declarations
refuse. Substitutions remain explicit selections of a different manifest entry
and the separate `part_decisions` obligation; this producer does not choose one.

## External parts without an LCSC number

The central `parts/1` selection union additionally accepts `kind: external`.
Its selection keys remain `kind`, `lcsc`, `part_record`, `part_record_sha256`,
`decision_ref`. The entry's `lcsc` and selection `lcsc` must be `NO_LCSC` and
`stock` must be null. Existing generic and named LCSC validation is unchanged.
No synthetic JLCPCB row or catalog quantity is created.

`part_record.prepare_external_record(root, identity_source, datasheets,
datasheet_id, deviation_ref)` constructs a strict
`agent-ee.external-part-record/1`. It retains:

- exact `mpn`, `manufacturer`, `package`, derived `lcsc: NO_LCSC`, `stock: null`;
- `identity_source: {path, sha256, locator}`, with locator `/`;
- `datasheets: {path, sha256}`, `datasheet_id`, captured `datasheet_url`, and
  `artifact: {path, sha256}`;
- `deviation_ref: {path, sha256, index}`, identifying the entire captured
  deviations file and one zero-based entry.

The identity source is a captured `agent-ee.external-part-identity/1` declaration
with exactly `schema`, `mpn`, `manufacturer`, `package`, and nonempty
`evidence_refs`. Each evidence reference has `path`, `sha256`, `locator` and must
name nonempty captured bytes. The record's identity is rederived from this
pinned declaration. This is a reviewed identity projection, not authenticated
supplier data or automatic interpretation of the referenced prose/PDF.

The selected deviation must pass the existing AGENTEE-167 validator: consigned
or hand-soldered after delivery, exact original MPN, null original LCSC,
purchase source, reason, stock-deviation reason and captured evidence. Datasheet
acquisition must be complete and hash-valid. External bindings always include
`NO_LCSC` and `not_assembled`; omitting or changing the matching deviation
refuses. A valid record does not prove that stock is zero or available.

The whole deviations file hash is part of the immutable manifest closure. A
change to any entry requires deliberate record/manifest requalification and a
new captured baseline. A mutable writer deviations file may be a separate
captured copy, but it must equal the pinned declaration for external binding;
it cannot silently inherit the earlier qualification. Do not mark a path in
this immutable closure writer-editable.

Keep `assembly_readiness` and `part_decisions` configured. Existing assembly
checks still refuse conflicting positive/unknown stock, undeclared hand-fit
markers and contradictory native identities. External selections never bypass
those checks, and their quantities do not enter JLCPCB stock aggregation.

## Receipted stage

`binding_fill.configuration(source_root, intent=..., manifest=...,
part_refs=..., deviations=..., captures=..., dependencies=...)` returns one
ordinary schematic `stage-step/1`. Supply the qualified manifest dependency
closure and captured evidence paths. Keep the manifest, sidecar, libraries and
source closure immutable. Only the intent is a declared output. Its original
bytes are still captured in the standard receipt's complete input snapshot.

By default the intent is filled in place. Pass a distinct captured `output=PATH`
to keep the read intent writer-owned: the step then reads `intent` as a declared
input and writes the bound projection only to `output`. Point the schematic
stage's `intent` at that output. The [pod driver](pod-driver.md) requires this
form, because a writer-editable file cannot also be a step output. `captures`
may be `.` when the intent's library paths are relative to the candidate root.

Place this step before `schematic_stage.configuration`'s emit and annotate
steps. `intent.json` is published in the step work directory only after complete
qualification; failure retains `binding-fill.json` and publishes no adoptable
intent. The standard receipt binds command/source, input snapshot, artifacts
and adopted output. No special receipt schema or weakened adoption rule is used.

Successful `agent-ee.binding-fill/1` reports record normalized input/output,
manifest, sidecar and deviation identities, the exact dependency map and every
component's manifest reference/derived fields/deviation index. These are
producer evidence, not accepted check findings. A failed report records
`execution: failed` and the refusal reason.

## Qualification limits

The focused native benchmark uses 130 original synthetic components and
captured synthetic identity/PDF envelopes. It exercises real binding-fill,
KiCad emission/export, forward annotation, native field parity and placement
write-scope verification. It is not the telemetry pod and proves no real part's
pin map, footprint, circuitry, stock availability or assembly suitability.
Actual pod captures, reviewed projections and all remaining stage checks are
still required. No network, model, purchase or upload operation is performed.

## Required copper probes

A declared [bare copper probe feature](probe-features.md) receives only the source-derived `AGENT_EE_Probe` identity property. Binding fill requires its exact reference allocation and intent net and refuses supplier, DNP and assembly claims. Missing declared probes are refused by reference.

## Captured datasheet source selection

AGENTEE-237 adds an explicit source-decision input to the part-record producer.
A captured datasheet may include `source_selection: {path, sha256, part_key}`.
The named `agent-ee.part-record/1` may carry the same reference; its manifest
entry must retain it. Generic and external manifest entries can also declare a
selection. The referenced file has this strict shape (hash placeholders must be
replaced with actual SHA-256 values):

```json
{
  "schema": "agent-ee.datasheet-source-selection/1",
  "parts": {
    "C123": {
      "selected": [{
        "id": "general-family",
        "sha256": "<selected PDF hash>",
        "provenance": {
          "path": "datasheets/manifest.json",
          "sha256": "<acquisition manifest hash>",
          "locator": "parts[id=catalog-alias]"
        }
      }],
      "rejected": [{
        "id": "supplier-wrong-family",
        "sha256": "<rejected PDF hash>",
        "reason": "Wrong family for the purchased part"
      }]
    }
  }
}
```

Each entry has exactly `selected` and `rejected` lists. Every source has a
nonempty ID and PDF hash; selected sources have acquisition provenance and
rejected sources have a reason. Exactly one selected source is required for the
declared `part_key`. Missing keys, zero or multiple selections, duplicate IDs,
and selected/rejected conflicts refuse. Rejection takes precedence by PDF hash,
including when a source is acquired under another ID or URL. The manifest
qualifier reports the logical part, purchase ID, all allocated sheet/reference
instances and the rejected source IDs/hashes. It replays the selected acquisition
using the same helper as binding fill and freezes its dependencies and the
selection file in the immutable closure.

`prepare_record(..., source_selection=ref)` uses the selected provenance in
place of the supplier-default datasheet arguments. The CLI accepts
`--source-selection sources.json --source-part-key C123` (the key defaults to
`--lcsc`). Put the resulting record's `artifact`, `datasheets`, `datasheet_id`
and `source_selection` into the manifest's datasheet declaration. Existing
qualification rejects a stale manifest instead of silently rebinding it.
Binding fill then projects the selected acquisition URL/hash into `Datasheet`
and `PARAM_Datasheet`.

The acquisition ID is a source alias, never a replacement purchase ID. Stock,
MPN, manufacturer and supplier package still come from the purchased LCSC row;
CAD and instance allocation still come from the manifest's qualified assignments.
For example, purchasing C694521 can use a catalog acquired as C76706 without
changing the purchased part. Selecting a family catalog does not prove exact
ordering-suffix correspondence or authorize ratings: later exact-ordering
evidence needs its own captured source binding and qualification.

Legacy captures without a declared selection retain their custody-only behavior.
Callers migrating a reviewed source decision must supply this reference; the
producer does not infer selection from prose or arbitrary supplier filenames.

## Source-derived passive ratings

AGENTEE-233 adds an opt-in rating projection for populated resistors and
capacitors, so `assembly:passive-ratings` can evaluate captured designs without
handwritten values. `fill(..., ratings=declaration)`, the CLI's `--ratings` and
`configuration(..., ratings=path)` take a captured declaration:

```json
{
  "schema": "agent-ee.passive-rating-sources/1",
  "catalog": true,
  "facts": {"path": "rating-facts.json", "sha256": "<facts file hash>"},
  "extractor": {"path": "/usr/bin/pdftotext", "sha256": "<binary hash>",
                "version": "pdftotext version 24.02.0"}
}
```

`facts` and `extractor` are both null or both present. Without `ratings`, output
is unchanged. A component is rated when its resolved reference or symbol makes
it R or C under the same rule the assembly checker uses (`_passive_kind`).
Ratings come only from two kinds of source:

- **Catalog attribute.** For a named part, the producer reopens the part
  record's captured JLCPCB response by its pinned hash and takes the single row
  whose `componentCode` is the purchased LCSC ID and whose `componentModelEn`
  is the purchased MPN. It maps exactly `Power(Watts)` to `Power` (R) and
  `Voltage Rating` to `Voltage` (C). The resistor attribute
  `Voltage-Supply(Max)` is not mapped, because its name does not establish
  working or overload voltage. Generic and external parts have no captured
  supplier row here. Their generic `PARAM_*` ratings remain unchanged.
- **Datasheet fact.** The declared facts file is strict
  `agent-ee.datasheet-facts/1` and contains only `power_rating` (R) or
  `voltage_rating` (R or C) facts. Each fact's `part_number` must equal a bound
  part's MPN. Its cited PDF must be that part's bound artifact, and that
  artifact must be the [selected source](#captured-datasheet-source-selection).
  A fact on a part without a declared `source_selection`, on a rejected hash
  or on any other PDF refuses, naming the fact, the source ID and the hash. The
  pinned pdftotext verifies every citation in the existing networkless sandbox,
  using the [datasheet facts](datasheet-facts.md) page and token rules. An
  unverified, unsupported or unextractable citation refuses when used and is
  never treated as support. Facts naming no bound R/C part refuse.

Values are compared exactly by SI magnitude (`100mW`, `0.1W` and `1/10 W` are
equal). Unparseable spellings refuse. If any two sources for one field
disagree, filling refuses and lists every value and source. A part with no
source keeps no rating field, and the report records the exact reason under
`ratings.missing`. The assembly check then still reports
`missing_rating_property`.

A rated component receives `Power` and/or `Voltage`, spelled as in the
catalog (or as the fact value plus unit), and `AGENT_EE_Ratings`. Native fields
hold at most 256 characters, so this field is compact canonical JSON:
`{"ratings": {...}, "record_sha256": ..., "schema": "agent-ee.passive-rating-binding/1"}`.
The full record is in `binding-fill.json` under `ratings.derived[key]`. The key
is the reference for a root-sheet component. For a component with declared
child-sheet instances, each instance gets its own entry keyed by its
hierarchical path, the sheet names plus the reference (`/A/R101`, `/B/R201`).
All of these entries hold the same part record (`ratings.missing` uses the same
keys). The record holds
the part ID, LCSC and MPN; per field the value, exact SI value, unit and every
source (`catalog`: LCSC ID, response path, SHA-256, attribute name;
`datasheet`: selected source ID, fact ID, PDF path, SHA-256, page); and the
limitation. `record_sha256` is the canonical identity of that record.
`assembly_readiness` resolves it against the receipted report when its
[`rating_provenance`](assembly-readiness.md#receipt-bound-rating-provenance)
is declared (AGENTEE-242); undeclared, the check is `unknown`. Rating
fields follow the ordinary writer-binding rule: a supplied different value
refuses. Facts files and cited PDFs are added to the report dependencies. Pass
them in `configuration(..., dependencies=...)`.

These are rated values only. They establish no derating, applied stress,
effective capacitance under DC bias, temperature suitability (for example
105 °C operation) or ordering-suffix correspondence. For example, the pod's
C694521 row rates `CGA6P3X7R1E226MT000N` at 25 V. That does not establish the
MT000N/M250AB correspondence, which needs its own cited evidence.

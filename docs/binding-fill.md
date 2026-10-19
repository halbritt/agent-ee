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

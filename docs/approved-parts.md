# Approved-parts manifest and native membership

`agent-ee.parts/1` declares allowed physical instances and their captured
catalog/CAD identities. The `parts_subset` adapter evaluates exactly
`parts:declared-membership-multiplicity/1`. It does not authenticate owner
approval. A selection result, an author's declaration, and refreshed hashes
cannot create that authority. Real generation still needs owner-supplied
allowlist data and the producer migration described below.

## Public interfaces

[parts_manifest.py](../src/agent_ee/parts_manifest.py) exports:

- `validate_manifest(document)`: strict structural validation, no I/O. Returns
  the supplied document without changing it; rejects unknown fields and versions.
- `load_manifest(root, path)`: strict JSON and captured-path validation.
- `dependency_closure(document, root)`: validates pinned bytes and returns the
  sorted path-to-SHA256 map of every dependency. The caller also freezes the
  manifest file itself.
- `qualify_manifest(document, root)`: rederives canonical selection and CAD
  assignment checks; returns `dependencies`, `selection`, `cad`, and explicit
  `missing_provenance`. Missing required datasheet data remains missing coverage.
- `prepare_manifest(proposal, root)`: copies a supplied proposal, fills its
  historical catalog row fields using canonical selection, then qualifies it.
  The caller supplies and captures the canonical selection result, declaration
  source, CAD records and datasheet evidence first. The function creates no
  approval receipt and writes no files.

[parts_subset.py](../src/agent_ee/parts_subset.py) exports:

- `compare_inventory(document, graph, virtual, missing_provenance=())`: pure
  diagnostic comparison. Inputs must already be validated/qualified. `graph`
  comes from the shared strict native reader; `virtual` comes from
  `native_binding.virtual_symbols` over that exact graph and captured hierarchy.
- `evaluate(root, manifest_path, native_xml, schematic_paths)`: complete offline
  replay, including canonical catalog/selection, CAD and native hierarchy checks.
  The first schematic path is the root. Returns a report, including failures.
- `capability(params, files, inputs, scope, check_id)`: validates the ordered
  native producer, single requirement mapping and immutable closure; returns
  `immutable_inputs` and the one predicate.
- `check_parts_subset(ctx)`: coordinator adapter using the existing networkless
  executor, candidate identity check and retained native XML prerequisite.

## Exact schema

All objects reject extra keys. Paths are canonical relative capture paths, with
no links, backslashes or parent traversal. Hashes are lowercase 64-digit SHA256.
A **blob** is exactly `{path, sha256}`; a **located blob** adds `locator`.

| Top-level field | Exact content |
| --- | --- |
| `schema` | `agent-ee.parts/1` |
| `declaration_source` | Located blob identifying the captured allowlist/requirement source; no invented approving identity |
| `catalog` | `snapshot` directory, `manifest_sha256`, `database_sha256`, `snapshot_date`; null for named-only manifests |
| `selection` | `requirements` blob and `result` blob for canonical `agent-ee.part-requirements/1` and its complete result; null with a null catalog for named-only manifests |
| `cad` | `manifest` blob, `assignments` blob, `members_sha256`; optional `declarations` blob |
| `property_fields` | Distinct exact native property names under `lcsc`, `mpn`, `manufacturer`; no inferred aliases |
| `population` | `native-dnp-optional-only/1` |
| `parts` | Nonempty list of at most 4096 logical parts |

Each part has these required fields and the optional `pin_contract` field:

| Field | Meaning |
| --- | --- |
| `id`, `role` | Nonempty logical identifier and descriptive role |
| `required` | Boolean applying to every selector of this logical part |
| `instances` | Nonempty list of exact `{sheet, reference}` selectors; each selector is unique across the entire manifest |
| `selection_id` | Unique allocation ID; generic entries match a canonical selection requirement; named entries retain it as a logical allocation label |
| `lib_id`, `footprint` | Exact `library:member` identities, matching captured CAD assignments |
| `lcsc`, `mpn`, `manufacturer` | Exact nonempty catalog identities; LCSC uses `C` followed by a positive decimal integer |
| `stock` | Historical integer quantity, or null for unknown; rederived from the captured row |
| `datasheet` | One of the two explicit forms below |
| `pin_contract` (optional) | Captured blob for [page-cited electrical obligations](pin-contracts.md); included in the immutable dependency closure |

A captured datasheet has exactly `status: captured`, `required` boolean,
`artifact` blob and `provenance` located blob. An unavailable datasheet has
exactly `status: unavailable`, `required` boolean and nonempty `reason`.
Hashes establish byte identity, not datasheet applicability or verified facts.
Required unavailable provenance makes the check unknown.

`sheet` is the native exporter instance path (`/` for root, or slash-delimited
sheet UUIDs ending in `/`); repeated child sheets have separate selectors.
No authored logical-ID property can substitute for the sheet/reference pair.
The number of selectors must equal canonical `quantity_per_board`, independently
of selection's build quantity and stock allocation. Each physical instance can
satisfy only one selector. Optional selectors reserve capacity but may be absent.

The CAD manifest is the existing complete
`agent-ee.footprint-libraries/1` record. Assignments use
`agent-ee.footprint-assignments-input/1`, with each logical `id` used as the
assignment's `reference`. Symbol/footprint qualification reuses the existing
assignment checker; it does not create another CAD importer. `members_sha256`
is `storage.identity(sorted(member_blobs, key=lambda m: m['path']))`, over all
footprint members and captured symbol-library blobs, without library `name`
fields. Duplicate paths are rejected. Both CAD records and all their members
are immutable dependencies.

An optional `cad.declarations` pins the existing
`agent-ee.footprint-declarations/1` input to the assignment checker. It is a
separate immutable dependency, with its bytes and hash checked again on replay;
it is not part of the CAD member digest. For example, the captured ESP32 pad 41
thermal-via declaration permits the existing checker to distinguish thermal
vias from mixed assembly. It does not exempt arbitrary copper or waive pin/pad
parity. Missing, changed or malformed declaration bytes refuse qualification.
Omitting the field preserves the previous checker behavior, including refusal
of undeclared thermal vias. Declaration contents remain subject to review.

Canonical selection is recomputed from captured requirements and the pinned
catalog snapshot, then compared with the entire captured selection result.
The catalog loader rederives normalized rows from stored raw attributes. An
arbitrary rehashed selection record cannot replace this replay. Raw catalog
volume provenance is retained by that canonical contract; this module does not
fetch upstream sources or authenticate them.

## Named ICs and modules

An entry with a `selection` object uses exact captured identity replay. It keeps
all ordinary part fields, adds a nonempty supplier `package` string, and declares:

```json
{
  "kind": "named",
  "lcsc": "C38695",
  "part_record": "named/C38695/part-record.json",
  "part_record_sha256": "<64 lowercase hex digits>",
  "decision_ref": {
    "path": "decisions/can-transceiver.txt",
    "sha256": "<64 lowercase hex digits>",
    "locator": "line 1"
  }
}
```

`decision_ref` is a captured located blob recording why this exact part was
chosen. It conveys no approval authority. Unknown selection kinds, absent
records and unavailable datasheets are refused. A mixed manifest's top-level
catalog/selection contains exactly its generic allocations. A named-only
manifest sets both fields to null. Generic ranking and category restrictions
are unchanged. Named instance capacity comes from its explicit selectors;
there is no invented parametric requirement or stock allocation.

The [part-record producer](../src/agent_ee/part_record.py) consumes existing
captures offline. It does not contact suppliers:

```sh
PYTHONPATH=src python3 -m agent_ee.part_record --root build/parts \
  --lcsc C38695 --catalog stock/catalog.json \
  --datasheets datasheets/manifest.json --datasheet-id U1 \
  --out named/C38695/part-record.json
```

The root and output parent must exist; output must be new. The producer prints
the output SHA-256. `prepare_record(root, lcsc, catalog_blob, datasheets_blob,
datasheet_id)` returns the same document without writing.
`qualify_record(document, root)` replays it and returns its immutable dependency
map. Both use the existing JLC capture validator and shared datasheet acquisition
reader; there is no second catalog or CAD parser.

`agent-ee.part-record/1` has exactly `schema`, `lcsc`, `mpn`, `manufacturer`,
`package`, `category`, `library_tier`, `stock`, `fetched_at`, `response_sha256`,
`datasheet_url`, `catalog`, `datasheets`, `datasheet_id`, `artifact`.
`catalog` and `datasheets` are blobs; `artifact` is the acquired PDF blob.
The JLC producer's `componentModelEn`, `componentBrandEn`,
`componentSpecificationEn`, `firstSortName`, `componentLibraryType` and
`preferredComponentFlag` supply MPN, manufacturer, package, category and tier.
`stockCount` supplies historical stock. Request metadata supplies the fetch time
and raw response SHA-256. Every summary is rederived from the retained response.
Empty supplier identity, package or category refuses admission. Zero historical
stock may identify a part; it does not satisfy assembly readiness.

AGENTEE-155 does not retain a datasheet URL. `datasheet_url` therefore comes from
the selected AGENTEE-33 acquisition record's original `url`. The full acquisition
binding digest, PDF hash and envelope are replayed by the same reader used for
facts/2. The part's `datasheet.artifact` must equal the record artifact; its
provenance must name the acquisition manifest with locator
`parts[id=DATASHEET_ID]`. This captures a source claim; it does not prove that
the document applies to the part or that its contents are correct.

Qualification checks exact LCSC, MPN, manufacturer, package and stock against
the record, then runs ordinary captured CAD assignment qualification. The
manifest closure freezes the record, decision, JLC catalog and retained
responses, acquisition manifest and acquired PDFs. `parts_subset` and both
scaffold modes consume this same closure. A changed record, response, PDF or
decision cannot escape baseline immutability. The existing native predicate
still compares the declared supplier and CAD identities, including rejecting an
unlisted populated IC. No suitability inference is claimed.

Named tests use original synthetic response/PDF/CAD captures, with real native
KiCad execution and sealed networkless subset checks. They do not qualify the
public endpoint's live response shape. That still requires an owner-run
AGENTEE-155 capture. The RC-specific rung-1 property adapter remains limited to
its existing generic R/C/header route.

## Outcomes and evidence

Every exported component is accounted for before population filtering. BOM
exclusion, `JLCPCB_Assembly=not_assembled`, connector names and authored `DNP`
properties grant no exemption. Native DNP can exclude an optional/unlisted
instance; a required native-DNP or off-board instance is a defect. Only
exporter-omitted power occurrences qualified by the shared hierarchy audit
receive the documented virtual-power exclusion.

Complete identity mismatch, absent required selectors, or unexpected populated
instances produce defects. Each match records sheet, reference, UUID, native and
declaration locators and exact differing values. Missing identity properties,
mandatory provenance, malformed inventories or unresolved exporter omissions
produce unknown coverage, retaining known mismatches diagnostically. An empty
or entirely optional-absent/DNP population never passes vacuously.

The report retains the native inventory projection and its hash, raw XML hash,
manifest hash, canonical selection/CAD observations, immutable dependency map,
match table and exclusions. The adapter records candidate ID and prerequisite
check ID. Existing coordinator sealing and public verification protect the
retained report and XML; the report's own hash is not an approval mechanism.
Only a complete execution exposes the single evaluated predicate. Unknown
coverage cannot establish a defect witness.

## Scope and campaign integration

`parts_subset` is registered in the production check runner and capability
registry. Its single predicate can cover a scope/3 acceptance requirement.
Declare an earlier `netlist` check and capture the manifest and all its
provenance dependencies. Both scope/2 and scope/3 freeze that closure in
`baseline.json.files`, including the manifest SHA256. The params object is
exactly:

```json
{
  "manifest": "parts/manifest.json",
  "netlist_check": "netlist",
  "requirement_id": "parts-membership"
}
```

Map the check to one baseline requirement whose source path and locator equal
`declaration_source`. The capability rejects mutable closure members. Changing any manifest byte
changes the baseline ID, even if its dependencies stay identical. The campaign
rejects a writer changing a baseline source and refuses a fresh capture whose
baseline differs from the campaign's baseline before verification starts.

Focused tests use production registration, actual native tools, sandboxing,
coordinator seals and public `verify`. They cover exact footprint diagnostics,
missing required selectors, failed/missing native prerequisites, both baseline
versions, rejected campaign replacements and retained-evidence tampering.

The experimental `generate.py` entry point still needs migration from
`agent-ee.rung1-parts-manifest/0-experimental`. This validator rejects that
experimental schema. The default BOM-based rung path still lacks complete
supplier identities. The [optional qualified-driver path](rung1-parts.md)
now consumes an existing `parts/1` bundle, replays selection and binds its
R/C/header fields; it requires an explicitly assembled-header spec and retains
the experimental manifest only as a property projection. A real catalog-bound
reference run is recorded on that page; complete empty-candidate campaign and
header package/geometry qualification remain outstanding. Synthetic owned fixtures establish
software behavior only. Canonical parts membership does not establish assembly
readiness, live stock, owner review, or hardware release.

The existing reference-block parts payload checks bytes/schema only. Root and
the AGENTEE-89 owner must bind a relevant sealed parts predicate to the block's
actual native subject before upgrading that authority. A passing check for
another subject is insufficient. AGENTEE-29/32 repairs and AGENTEE-33/40 metadata
integration remain separately owned.

## External hand-fitted identities

AGENTEE-174 adds the explicit `external` selection variant to the same central
parts manifest. It requires `NO_LCSC`, unknown stock, a rederived captured
identity declaration, acquired datasheet and a pinned AGENTEE-167 hand-fit
deviation. It does not create a JLCPCB catalog row. See
[binding fill](binding-fill.md#external-parts-without-an-lcsc-number) for exact
record fields, immutable dependency closure and requalification rules.

## Manufactured probe features

The parts/1 union also admits one required [bare copper probe feature](probe-features.md) kind with exact captured symbol, footprint, pin and native net identity. This variant has no catalog or purchased identity; the ordinary part schema is unchanged.

## Explicit datasheet source decisions

A captured `datasheet` optionally adds `source_selection: {path, sha256,
part_key}` referencing the strict
[`agent-ee.datasheet-source-selection/1` contract](binding-fill.md#captured-datasheet-source-selection).
The named part record may also declare this field, in which case the manifest
must retain the identical reference. Qualification rejects a bound rejected
source by hash, requires exactly one selected source for the declared key, and
replays its acquisition and immutable dependencies. Purchase identity and CAD
remain independent of the acquisition alias. Legacy declarations without this
field establish custody only; source selection does not establish electrical
ratings or exact ordering-code correspondence.

## Declared on-board copper features

AGENTEE-305 adds `kind: "copper_feature"` beside `bare_copper_probe` in
`agent-ee.parts/1`. A copper feature is required, has 2–64 distinct passive
pins and has no catalog allocation, supplier identity, stock or datasheet record.
Its exact fields are `kind`, `id`, `role`, `required`, `instances`, `lib_id`,
`footprint` and `copper`:

```json
{
  "kind": "copper_feature",
  "id": "termination-jumper",
  "role": "on-board solder bridge",
  "required": true,
  "lib_id": "Owned:Jumper",
  "footprint": "Owned:Jumper2",
  "instances": [
    {"sheet": "/", "reference": "JP1", "nets": {"1": "CAN_L", "2": "TERM"}}
  ],
  "copper": {
    "symbol": {"path": "Owned.kicad_sym", "sha256": "<captured SHA-256>"},
    "footprint": {"path": "Owned.pretty/Jumper2.kicad_mod", "sha256": "<captured SHA-256>"},
    "pins": [
      {"number": "1", "name": "A", "electrical_type": "passive"},
      {"number": "2", "name": "B", "electrical_type": "passive"}
    ],
    "native_flags": {"in_bom": false, "dnp": false, "exclude_from_board": false}
  }
}
```

Replace the illustrative hash placeholders with actual captured digests. Source
blobs must match the manifest's captured CAD assignments and immutable closure.
Qualification checks exact symbol pins, pad count and pad numbers, then reuses
[fab copper rules](fab-export.md#copper-features) for surface geometry, graphics
and fields. Library format headers are omitted from the geometry view; an absent
library placement is evaluated at its native origin. Source bytes remain intact.
Custom-pad or assembly declarations still use the existing captured CAD
`declarations` contract. Inherited and power symbols are unsupported for this kind.

`parts_subset` compares native sheet/reference, library and footprint identities,
pin metadata, every declared native net and all three native population flags.
It also binds the embedded symbol body to the captured source. Missing or changed
source evidence refuses coverage; an observed membership mismatch is a defect.
An excluded BOM flag alone never admits an undeclared populated instance. DNP
cannot suppress a required copper declaration. Existing purchased-part and probe
rules are unchanged.

This admission path covers manifest qualification and native parts membership.
It does not add copper generation to binding fill or the Rung-1 RC producer.
Fab steps still require their explicit `copper_features` reference list and fresh
board qualification; parts membership alone does not establish fab acceptance.
Owned native tests exercise sandboxed export, coordinator evidence and public
verification. A pinned pod replay can qualify JP1–JP5 while still refusing the
complete candidate for another undeclared feature; that is incomplete candidate
coverage, not a passing full-pod result.

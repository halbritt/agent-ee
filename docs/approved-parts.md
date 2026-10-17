# Captured parts membership proposals

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
| `catalog` | `snapshot` directory, `manifest_sha256`, `database_sha256`, `snapshot_date` |
| `selection` | `requirements` blob and `result` blob for canonical `agent-ee.part-requirements/1` and the complete canonical selection-check result |
| `cad` | `manifest` blob, `assignments` blob, `members_sha256` |
| `property_fields` | Distinct exact native property names under `lcsc`, `mpn`, `manufacturer`; no inferred aliases |
| `population` | `native-dnp-optional-only/1` |
| `parts` | Nonempty list of at most 4096 logical parts |

Each part has exactly these fields:

| Field | Meaning |
| --- | --- |
| `id`, `role` | Nonempty logical identifier and descriptive role |
| `required` | Boolean applying to every selector of this logical part |
| `instances` | Nonempty list of exact `{sheet, reference}` selectors; each selector is unique across the entire manifest |
| `selection_id` | Unique canonical selection allocation ID |
| `lib_id`, `footprint` | Exact `library:member` identities, matching captured CAD assignments |
| `lcsc`, `mpn`, `manufacturer` | Exact nonempty catalog identities; LCSC uses `C` followed by a positive decimal integer |
| `stock` | Historical integer quantity, or null for unknown; rederived from the captured row |
| `datasheet` | One of the two explicit forms below |

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

Canonical selection is recomputed from captured requirements and the pinned
catalog snapshot, then compared with the entire captured selection result.
The catalog loader rederives normalized rows from stored raw attributes. An
arbitrary rehashed selection record cannot replace this replay. Raw catalog
volume provenance is retained by that canonical contract; this module does not
fetch upstream sources or authenticate them.

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

## Root integration and remaining acceptance

Production registry, baseline, stage and reference-block changes are deliberately
owned by root. Register `check_parts_subset` in `checks.CHECK_FUNCS`; use
`capability` in `capabilities.describe_check` with an ordered `netlist`
prerequisite and `can_evaluate_bound=True`. Freeze its returned closure for both
scope/2 and scope/3. The params object is exactly:

```json
{
  "manifest": "parts/manifest.json",
  "netlist_check": "netlist",
  "requirement_id": "parts-membership"
}
```

Map the check to one baseline requirement whose source path and locator equal
`declaration_source`. The capability rejects mutable closure members. Root must
also preserve the existing campaign baseline comparison so changed manifest
bytes cannot be adopted mid-run. The focused tests install the pending registry
and descriptor hooks only in their own process, then use actual native tools,
sandboxing, coordinator seals and public `verify`. This demonstrates the seam;
it does not claim production registration has landed.

Coordinator 423 owns `generate.py` and migration from
`agent-ee.rung1-parts-manifest/0-experimental`. This validator rejects that
experimental schema. Rung 1's real J1 lacks qualified LCSC/MPN/provenance, so the
actual R/C/header stage remains unknown. Synthetic owned fixtures establish
software behavior only. Canonical parts membership does not establish assembly
readiness, live stock, owner review, or hardware release.

The existing reference-block parts payload checks bytes/schema only. Root and
the AGENTEE-89 owner must bind a relevant sealed parts predicate to the block's
actual native subject before upgrading that authority. A passing check for
another subject is insufficient. AGENTEE-29/32 repairs and AGENTEE-33/40 metadata
integration remain separately owned.

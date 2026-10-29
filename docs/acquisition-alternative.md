# Captured acquisition ADC carrier selection

`acquisition_alternative.revise(owned_revision, CapturedContext(...))` is a
source producer for the existing acquisition family. It supports only primary
C37593 / TI ADS1115IDGSR and carrier alternative C468683 / TI ADS1115IDGST,
using the existing ADS1115IDGS symbol and TSSOP-10_3x3mm_P0.5mm footprint.
It does not run preparation, native tools, a build, acquisition or a controller.
No public preparer option is wired by this module.

The caller provides a complete immutable inventory of its owned **unsealed**
revision, one captured catalog blob, one captured TI IDGST carrier-page blob,
one captured TI package-ordering text blob, and the actual preparation
`as_of` evaluation time. Blobs use existing `{path, sha256}` contracts under
that revision; there is no caller `qualified` flag or caller-selected part.
The catalog may be the reviewed offline union of existing observations. Its
external parent/derivation provenance must accompany it; this function neither
merges catalogs nor changes their dates. A context inventory is a source pin,
not proof of external authority; the normal acquisition recipe admission and
review of captured manufacturer evidence remain required.

Selection replays the exact JLC raw responses through the existing catalog
validator and run/freshness gate. The assembly check's sourced boards, stock
floor and freshness settings are retained. Stock must meet
`max(instance quantity * boards, stock_floor)` using the existing selection
stock rule; full selected-board quantities are also checked. A successful
observation for each selected board ID and both family IDs is required;
missing/failed/not-found evidence refuses rather than proving availability.
The primary is retained whenever eligible. Otherwise the sole supported TI
carrier alternative is selected deterministically. No candidate refuses.
Captured identity/package mismatches exclude a candidate even when stocked.
Optional operating-temperature, hand-fit and Extended-count policies are
outside this bounded adapter and explicitly refuse if enabled.

The captured TI page must identify IDGST and explicitly link IDGSR as the same
product in another carrier. The captured ordering rows must identify both
orderables as active production VSSOP(DGS),10 pins, NiPdAu, MSL2,
-40..125 C, BOGI, with their distinct 2500/250 reel quantities. This narrow
correspondence preserves the authored device, physical pin/pad identity and
operating limits. It supplies no new ratings or operating guarantees. ADS1015,
X2QFN, another grade or a guessed package is unsupported. Undocumented supplier
flags are neither eligibility nor ineligibility predicates here.

Existing `part_record.prepare_record` regenerates every selected named record
from the explicit catalog and each existing captured datasheet binding.
`parts_manifest.prepare_manifest` replays the complete declared CAD/dependency
closure. Historical records and raw captures are preserved. New records and
an attributable decision are under `preparation/alternative/`. The producer
revises exactly these four operative source files:

- `parts.json`: same logical allocations/CAD/datasheets, replayed purchasing
  fields and observed stock, new decision and named-record bindings.
- `circuit.json`: only the selected ADC purchasing/display value.
- `component-selections.json`: only that ADC's LCSC and purchasing value.
- `requirement-map.json`: only the assembly catalog path/hash, retaining policy.

A full `part-refs/1` purchasing binding is produced separately at
`preparation/alternative/part-refs.json`. Other topology, NCs, source limits,
ratings, mechanical policy, layout and all native CAD bytes remain untouched.
The report provides selected identity, decision/input pins, complete before
inventory, changed/added hashes and resulting inventory. `audit_selection`
rereads catalog/decision/source pins, requalifies the manifest and compares the
whole resulting source inventory. Changed stock or source bytes cannot reuse
that decision. Call it before subsequent legitimate preparation edits; its
inventory intentionally stops matching once those edits occur. Future build
stock checks still use their actual run anchor; this producer never resets
freshness or authorizes replay against a historical sealed run.

## Serial wiring in acquisition_prepare (coordinator owned)

Keep `_admit`, `_load_overlay`, the original plan/inventory and fixed brief
recipe unchanged. Add one optional captured-context argument/CLI path to the
existing preparer, with its exact bytes and producer/helper hashes included in
preparation identity. Do not admit the offline draft as the recipe itself.
A missing context retains current preparer behavior.

After `_write_revision` has produced the owned revision and `preparation/`
exists, copy the admitted catalog, exact raw responses, TI page, ordering text
and external parent/derivation provenance into a fresh directory **under
preparation/**. Verify original immutable pins, bytes and inventory before and
after copying. For the reviewed union retain earliest `as_of`, latest
`completed_at`, each request/fetched_at/raw response and both original catalogs;
never describe it as a fresh capture or one historical invocation. Copy ordering
text under preparation too: pre-populating `manufacturer-text/` would collide
with typed_overlay's separately admitted added-input tree.

Build CapturedContext from the complete revised inventory and the captured
blob pins, then call `revise`. Archive these four file changes against the
original recipe, alongside capacitance changes, and retain the selector report
under preparation. Existing `_archive_revision` has a closed REVISION_BASIS
and will raise KeyError for new circuit/component-selection changes; root must
add precise bases for those two files or explicitly append their original
bytes and combined before/after hashes to the existing ledger. This module
does not modify that owner module or manufacture an archive receipt.

Update `overlay['base']['requirement_map_sha256']` after the assembly-path edit
and before writing overlay.json, mechanics/operative-map derivation and
`typed_overlay.prepare`. The original spec/spec-record pins and typed
partitions do not change for purchasing selection. The existing preparation
scope input captures the new source/decision/record files. Ensure preparation
is declared exactly once in the map; otherwise explicitly add it before seal.
Generate mechanics and typed accounting from this revised map, then seal using
the existing producer. No inputs may be appended after seal. Retain every
source change in the final preparation input inventory and identity.

The normal parts stage still consumes `parts.json` and `circuit.json` from the
plan. `parts_stage.compile_parts` and its ordinary binding-fill/native consumers
must generate new purchasing properties, rating provenance, baseline/stage
receipts and final assembly checks. Do not substitute old draft build receipts
or edit native properties. Exact-MPN applicability, electrical/native/package
review, fresh run-bound stock, supplier preview and physical validation remain
pending. No G2, ZA04, G4, board adoption or assembly acceptance follows from
source tests.

Focused offline tests require `AGENT_EE_ALTERNATIVE_DRAFT` naming the retained
reviewed draft root. Without private inputs the tests skip explicitly. They
copy only its hash-pinned original inputs and raw catalog/source evidence;
none of its generated alternative named/parts/build receipts are transplanted.
Synthetic boundary mutations are labeled CPU substitutions. Subprocess/network
operations and native imports are denied throughout the tests:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
  AGENT_EE_ALTERNATIVE_DRAFT=/absolute/private/reviewed-draft \
  timeout 180 python3 -m unittest test_acquisition_alternative -v
```

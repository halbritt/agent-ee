# Captured acquisition ADC carrier selection

`acquisition_alternative.revise(owned_revision, CapturedContext(...))` is a
source producer for the existing acquisition family. It supports only primary
C37593 / TI ADS1115IDGSR and carrier alternative C468683 / TI ADS1115IDGST,
using the existing ADS1115IDGS symbol and TSSOP-10_3x3mm_P0.5mm footprint.
It does not run preparation, native tools, a build, acquisition or a controller.
The public preparer optional context hook is documented in acquisition-prepare.md.

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

## Public preparation wiring

The existing public `acquisition_prepare.prepare` now accepts
`alternative_context`, `alternative_source` and `alternative_context_sha256`;
the CLI uses the corresponding hyphenated flags. All three are required together.
`acquisition_alternative_context` validates/copies the finite reviewed closure
and verifies the exact conservative two-parent catalog union. It performs no
fetch or catalog discovery and copies no old generated alternative receipts.

Original `_admit` and `_load_overlay` remain unchanged. After `_write_revision`,
context bytes and all referenced raw/source/parent provenance are copied under
preparation, and the selector runs against the complete owned revision. Exact
context/source/helper pins and the actual evaluation anchor enter identity.
The parts citation is rebound to its identical actual operative spec if its
historical requirements-stage namespace does not yet exist. Archive handling
includes circuit/component-selection changes and explicit selected catalog/source
hashes; original files and old catalog choices remain preserved. The overlay's
base map hash is rebound before mechanics and typed derivation/sealing. Context
and source bytes are reread before publication, and no input is appended after
the existing typed seal. Root alone admits any actual target execution.

The normal parts stage still consumes `parts.json` and `circuit.json` from the
plan. `parts_stage.compile_parts` and its ordinary binding-fill/native consumers
must generate new purchasing properties, rating provenance, baseline/stage
receipts and final assembly checks. Do not substitute old draft build receipts
or edit native properties. Exact-MPN applicability, electrical/native/package
review, fresh run-bound stock, supplier preview and physical validation remain
pending. No G2, ZA04, G4, board adoption or assembly acceptance follows from
source tests.

Standing tests use original authored CAD, supplier and PDF envelope fixtures;
these are explicit CPU substitutions, never vendor observations or native proof.
They require no host-private paths and do not skip without private inputs.
Guards are scoped per test and restore subprocess/network functions, native
module entries and import-finder state; no permanent audit hooks are installed.
Real retained-capture checks remain separate private evidence outside normal
test discovery. Neither fixture class provides campaign acceptance.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:.:tests \
  timeout 180 python3 -m unittest tests.test_acquisition_alternative -v
```

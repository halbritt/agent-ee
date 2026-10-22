# Catalog identity index and Stage 2 MPN resolution

`agent_ee.catalog_identity` (AGENTEE-247) prepares Stage 2 part identities for
non-generic components: MCUs, transceivers, sensors, crystals, GNSS/RF modules
and specialty connectors. It maps declared MPNs to candidate LCSC ids and
catalog datasheet URLs offline. It never downloads, scrapes or guesses.

It has two parts:

- **Capture** derives a pinned, hash-sealed `agent-ee.catalog-identity/1`
  index from local jlcparts split-archive volumes.
- **Resolution** maps `agent-ee.part-decisions/1` MPN declarations to
  candidates for [`jlc_stock_capture`](jlc-stock-capture.md) and
  [`datasheet_capture`](datasheet-capture.md).

The index is separate from the [`agent-ee.catalog/1` snapshot](catalog.md). The
eight generic tables, SI-interval normalization, `rows_sha256` and existing
snapshot pins are unchanged. [Part selection](part-selection.md) still selects
only from those verified generic tables. The identity index takes no part in
parametric selection.

## Source and profile

The source manifest is `agent-ee.catalog-identity-source/1`. It has exactly
`schema`, `snapshot_date`, `source_url`, `source_commit`, `stale_before` and
`volumes`. Each volume has `path`, `sha256`, `size_bytes` and the format
`jlcparts-v2-identity-split-zip`. The committed
[identity source manifest](../catalog-sources/jlcparts-identity-2026-09-14.json)
pins the same thirteen jlcparts pieces as the
[stocked catalog source](../catalog-sources/jlcparts-2026-09-14.json): same
commit, byte counts and SHA-256 values, under a different profile name. A
volume list cannot be used with the wrong importer. The stocked importer refuses
identity volumes, and the identity importer refuses stocked volumes.

Acquisition and archive handling reuse the [pinned jlcparts path](catalog.md#pinned-jlcparts-acquisition).
Every volume is size- and SHA-256-checked before any ZIP metadata is read. The
same ZIP64 split layout, member limits, `source-db-v2` marker and exact
`jlc_components` column check apply. The upstream producer software
(yaqwsx/jlcparts) is MIT-licensed. No upstream data is vendored. Pinning the
public source does not grant permission to redistribute its data, so captured
indexes belong in private scratch.

The profile keeps every row except:

- **Generic passives.** These are rows whose upstream category or subcategory
  maps to `resistor`, `capacitor` or `inductor` in the stocked importer's
  category map. They are resolved parametrically from `agent-ee.catalog/1` and
  are counted in `pruned_rows.generic`. A row is pruned only when its
  subcategory is positively qualified in `QUALIFIED_SUBCATEGORIES` (identity is
  nominal value, tolerance and package). Every other subcategory, whether
  unknown, empty or unlisted, is kept, as is any listing that
  `NON_GENERIC_SUBCATEGORY_WORDS` vetoes. Capture and load use the same rule. See [the passive boundary](#passive-boundary-agentee-253).
- **Stale rows.** These are rows whose UTC `fetched_at` date is before
  `stale_before`. They are counted in `pruned_rows.stale`.

Zero-stock and absent (`present = 0`) rows are kept. The index records
historical identity, not current availability.

## Index rows

`identity.sqlite3` has one `identity` table, keyed by LCSC id, plus an index on
`(mpn_key, lcsc)`. Each row records:

| Column | Meaning |
|---|---|
| `lcsc` | `C` followed by the native integer id. |
| `mpn`, `mpn_key` | Trimmed upstream `mfr` text. The key is that text upper-cased, and lookups use the key. An empty MPN is never matched. |
| `mfr`, `package` | Trimmed manufacturer and package text. Empty means unknown. |
| `category`, `subcategory` | The upstream claims, verbatim. |
| `library_tier` | `Basic` (`base`), `Preferred` (`expand` with `preferred = 1`) or `Extended`. An unknown library type fails capture. |
| `stock`, `present` | Historical stock (an integer ≥ 0) and the 0/1 presence flag at fetch time. |
| `last_seen` | The row's UTC `fetched_at` date, never after `snapshot_date`. |
| `datasheet_url` | The raw upstream `datasheet` text, or NULL when empty. It is never fetched or validated at capture. |
| `provenance` | Canonical JSON with `volume` (`cache.zip`), the one-based LCSC-ordered source `row` and `raw_sha256` (a hash of the native record). |

`manifest.json` (`agent-ee.catalog-identity/1`) retains the following:

- the source pins
- `generic_families_excluded`
- the database path, SHA-256 and size
- `row_count`
- the canonical streamed `rows_sha256`
- `pruned_rows`
- the SQLite version

Capture builds in a private staging directory and publishes the directory once,
with no replacement. Failure publishes nothing. Repeat captures from the same
volumes are byte-identical within one SQLite version.

`load_identity_index(directory, manifest_sha256)` performs these checks before
any query:

- verifies the manifest pin and the database size and hash
- runs `integrity_check`, which also proves the MPN index agrees with its table
- checks the exact schema SQL
- revalidates every row: types, LCSC syntax, the MPN key, the tier vocabulary,
  that no generic category is present, the snapshot and stale window, and
  canonical provenance
- checks unique ids and source rows, then the row count and `rows_sha256`

Queries then run on the same verified in-memory bytes. A tampered or repinned
forged row fails closed.

## Resolution

`resolve_decisions(index, decisions, narrowing=None)` reads the
`declaration.decisions` list of an `agent-ee.part-decisions/1` document and
uses the existing `part()` identity rule. It returns an
`agent-ee.catalog-identity-resolution/1` document. Each entry has one of these
statuses:

| Status | Meaning |
|---|---|
| `resolved` | Exactly one exact (case-insensitive) MPN match, possibly after an explicit package narrowing. |
| `selected` | An explicit, reasoned narrowing named an LCSC id that is among the candidates. |
| `verified` | The declared LCSC id is in the index with exactly the declared MPN. |
| `declared_lcsc` | An LCSC-only declaration whose id is in the index. |
| `ambiguous` | More than one exact candidate, a prefix-only match, or more than 50 prefix candidates. |
| `conflict` | The declared LCSC id's indexed MPN differs, or an explicit selection is not a candidate. |
| `unknown` | No match, the declared LCSC id is absent from the index, or no candidate survives package narrowing. |

Exact matches take precedence. Prefix candidates are rows whose MPN key extends
the declared key. They are listed only when there is no exact match, and they
never resolve without an explicit LCSC selection. A package filter alone does
not resolve them. `unknown` is never proof that a part does not exist:
generic passives, stale rows and parts outside the pinned feed are simply not
in the index.

A narrowing document (`agent-ee.catalog-identity-narrowing/1`) has `entries`.
Each entry has `requirement_id`, `mpn`, `package`, `lcsc` and a nonempty
`reason`, and at least one of `package` or `lcsc` must be set. Narrowing is
refused when an entry:

- names no decision
- repeats a decision
- targets a decision that already declares an LCSC id (that id is verified,
  not narrowed)

The document records:

- the index pins (manifest, database and rows SHA-256, and the snapshot date)
- the canonical identity of the decisions and narrowing inputs
- every candidate's identity facts
- `stock_capture_ids`: a sorted, unique list of LCSC ids from
  `resolved`/`selected`/`verified`/`declared_lcsc` entries. It is valid input
  for `jlc_stock_capture --ids-file`.
- `datasheet_bindings`: `{id, datasheet_url}` records for those ids whose
  indexed URL passes the datasheet acquisition URL rule. It is valid input for
  `datasheet_capture`.

Each entry reports its URL status:

- `candidate`: the URL passed the rule
- `absent`: the index has no URL
- `invalid`: for example, a non-HTTP(S) URL; it gets no binding

Catalog URLs are **candidates only**. Acquisition still verifies the PDF
envelope and hash. Which captured source governs a part stays with the
[datasheet source-selection contract](approved-parts.md#explicit-datasheet-source-decisions)
(`agent-ee.datasheet-source-selection/1`). Neither a resolution nor a catalog
URL establishes live stock, datasheet correctness, suitability or owner
approval.

## Commands

Run capture on the host with private destinations. Resolution is networkless and
runs in the ordinary sandbox, which the tests exercise in an isolated network
namespace:

```sh
PYTHONPATH=src python3 -m agent_ee.catalog_identity capture \
  catalog-sources/jlcparts-identity-2026-09-14.json \
  --source-root /private/scratch/jlcparts-inputs --out /private/scratch/jlcparts-identity
PYTHONPATH=src python3 -m agent_ee.catalog_identity resolve \
  /private/scratch/jlcparts-identity MANIFEST_SHA256 decisions.json \
  --narrowing narrowing.json --out /private/scratch/resolution.json
```

Each command prints the SHA-256 of the manifest or resolution it wrote, and
exits nonzero on any failure. A resolution output is never replaced. The volumes
are the ones fetched by `scripts/acquire_catalog.py`.

## Captured JLCPCB datasheet URLs

`jlc_stock_capture.response_datasheet_url(raw, lcsc)` reads `dataManualUrl` from
the exact `componentCode` row of a retained response, after the shared parser
accepts that response. An absent, null or blank value is None. A non-string
value fails. The `agent-ee.jlc-catalog/1` part keys and parser output are
unchanged, because the URL is read from the hash-verified response bytes that
are already retained.

`part_record.datasheet_url_crosscheck(record, root, identity_url=None)` first
replays an `agent-ee.part-record/1` record. It then compares the record's
acquisition URL with the JLCPCB response claim and, optionally, the identity
index URL for the same LCSC id. The result is one of:

- `agree`: every available claim equals the acquisition URL
- `differ`: at least one claim differs
- `unknown`: no catalog claim is available

The verdict is informational. It does not change the record and it grants no
source authority.

## Offline bouni split inspection and LCSC `pdfUrl`

`catalog_bouni.inspect_split(manifest, root)` verifies an
`agent-ee.catalog-bouni-split/1` manifest (`archive`, `member`, and `parts` with
`path`, `size_bytes`, `sha256`) against numbered parts `NAME.zip.001 ... NNN`.
It checks name pattern, contiguous numbering from 001, listed order, duplicates,
unlisted parts on disk, regular-file status, size and SHA-256, then streams the
parts into a private temporary file and requires a readable ZIP whose only
member is the declared one. It then verifies that member from its raw
compressed span, never from `zipfile`'s reader. The local header must agree with
the central directory on flags, method, name, CRC-32 and both sizes, ZIP64
included (`archive_member_header_mismatch`). Only STORED and DEFLATE are
accepted. Other methods, encryption and data descriptors are refused
(`archive_member_unsupported`). The member must tile the archive body exactly
with no gap or overlap (`archive_member_bounds`). Exactly the compressed span is
read. A STORED span must equal its declared size. A DEFLATE span is decoded
incrementally and must reach its end-of-stream marker with no data left over;
decoding stops at that marker and any remaining span bytes are refused, and a
decoder step that neither consumes input nor yields output is refused, so
malformed input cannot loop (`archive_member_corrupt`).
The size limit (`max_member_bytes`) and the CRC-32 apply to the actual output,
not the declared sizes (`archive_member_oversized`, `archive_member_corrupt`).
A valid manifest hash alone does not establish archive integrity. It reports counts and sizes, and never reads the
member as a catalog. Every refusal is a `BouniRefusal` with a typed `reason`
(`missing_part`, `reordered_parts`, `duplicate_part`, `checksum_mismatch`,
`size_mismatch`, `archive_truncated`, `unlisted_part`, `manifest_malformed`, ...).
The layout is an owner-authored description, not qualified against the real
feed, which is not fetched.

`catalog_bouni.lcsc_pdf_url(raw, lcsc)` reads `result.pdfUrl` from retained LCSC
response bytes for the exact `result.productCode`. It returns the stripped
string, or None when absent, null, blank or for another part; a non-string value
or a malformed response is refused. The URL is recorded and never fetched.

## Passive boundary (AGENTEE-253)

`catalog_bouni.passive_boundary(families)` validates the family set treated as
passive. It has no default; a missing value is `boundary_not_supplied`, and a
string, empty, duplicated or out-of-set value is `boundary_invalid`. The allowed
set `KNOWN_FAMILIES` is resistor, capacitor and inductor, exactly the families
`part_selection` resolves by nominal value with tolerance (`VALUE_ATTRIBUTES`).
A test keeps the three in agreement.

`catalog_identity` is the production caller. `capture_identity` validates
`GENERIC_FAMILIES` before reading any volume, and `load_identity_index`
validates the manifest's `generic_families_excluded` before any row is read.
A widened boundary (for example adding `diode`, `led` or `ferrite_bead`) is
refused at both points, and a narrowed one is refused at load as an unsupported
profile. Diodes, LEDs, connectors, regulators and every unmapped family
(crystals, fuses, ferrite beads in their own category, ICs) stay in the index
and resolve by MPN. Within R/C/L categories, only subcategories listed in
`QUALIFIED_SUBCATEGORIES` are pruned; every unknown, empty or unlisted
subcategory keeps its row (fail closed, no default pruning). The reasoning is in
[decisions](decisions.md#catalog-identity-passive-boundary-agentee-253).

## Limits

- The `dataManualUrl` field name is unqualified against live JLCPCB responses
  until an owner-run capture shows it. The LCSC `pdfUrl` is read offline only
  (see below) and its response shape is likewise unqualified.
- The bouni/kicad-jlcpcb-tools format is inspected offline against an
  owner-authored layout and synthetic archives only (see below). No importer
  exists for it; only the pinned jlcparts `source-db-v2` layout is accepted.
- The subcategory words are matched against owned synthetic labels only. Real
  jlcparts category and subcategory strings are unqualified until a full-feed
  capture exists; an unlisted non-value-interchangeable subcategory inside an
  R/C/L category is still pruned.
- A full-feed capture and its size, runtime and row counts have not been
  qualified. The 1 GiB derived-database limit of the catalog importer applies.
  Committed tests use owned synthetic multi-category archives only.

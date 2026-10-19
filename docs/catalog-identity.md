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
  are counted in `pruned_rows.generic`.
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

## Limits

- The `dataManualUrl` field name is unqualified against live JLCPCB responses
  until an owner-run capture shows it. The `pdfUrl` field of LCSC responses is
  not read.
- The bouni/kicad-jlcpcb-tools split-archive format is not supported. Its layout
  has not been inspected or qualified offline. Only the pinned jlcparts
  `source-db-v2` layout is accepted.
- A full-feed capture and its size, runtime and row counts have not been
  qualified. The 1 GiB derived-database limit of the catalog importer applies.
  Committed tests use owned synthetic multi-category archives only.

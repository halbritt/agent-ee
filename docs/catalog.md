# Offline catalog snapshots

`agent_ee.catalog` captures explicitly pinned **local** CSV, SQLite, or ordinary
ZIP files containing CSVs. It never downloads data, reads provider credentials,
or supplies invented availability. Source catalog bytes and derived snapshots
belong in private scratch until the owner approves publication. The committed
tests generate owned synthetic inputs; their stock and prices are test values.

Capture is separate from checks. The caller supplies an `agent-ee.catalog-source/1`
manifest with exactly these fields:

```json
{
  "schema": "agent-ee.catalog-source/1",
  "snapshot_date": "2026-10-17",
  "source_url": "https://example.invalid/owned-catalog",
  "source_commit": "owned-fixture-v1",
  "currency": "USD",
  "stale_before": null,
  "volumes": [
    {"path": "owned.csv", "sha256": "<trusted SHA-256>", "size_bytes": 1234, "format": "csv"}
  ]
}
```

The hash and size must describe the actual input; the example placeholders do
not form an importable manifest. Each volume is verified before derivation.
`stale_before` is an explicit ISO date cutoff for `last_seen`, or null to retain
all rows. Capture refuses duplicate LCSC identities even when a duplicate would
be pruned. Row counts and the number pruned are retained.

CSV headers contain exactly `lcsc,mfr,mpn,package,category,stock,price1,is_basic,`
`is_preferred,last_seen,attributes` in any order. SQLite sources have an ordinary
`components` table with those columns in that order and text cells; views and
numeric coercion are unsupported. Categories are resistor, capacitor, inductor,
diode, led, connector and regulator. `stock` is a nonnegative integer;
`price1` is decimal USD per unit. An empty stock or price cell means unknown.
Booleans are explicitly `0` or `1`. `last_seen` must not exceed `snapshot_date`.
`attributes` is a JSON object mapping attribute names to original text.
Missing columns, extra columns, duplicate JSON keys, malformed row widths,
negative prices and invalid dates fail capture. Duplicate ZIP members, unsafe
member paths, symlinks, encryption and non-CSV members are refused.

Typed attribute names and SI units are:

| Attribute | Unit |
| --- | --- |
| resistance | ohm |
| capacitance | F |
| inductance | H |
| voltage, input_voltage, output_voltage, dropout | V |
| power | W |
| quiescent_current | A |
| temperature | degC |

Values require explicit units: `10 kohm +/-1%`, `100 nF ±10%`, `50 V`, or
`-40 degC .. 85 degC`. Prefix case matters (`M` is mega, `m` is milli).
Unparseable and absent attributes remain unknown, with raw text preserved;
they never acquire a guessed nominal value or tolerance. Normalization is a
catalog claim, not verification against a datasheet.

`capture_catalog(source_manifest, source_root, destination)` builds a new
private staging directory, validates SQLite integrity, and atomically publishes
`catalog.sqlite3` and `manifest.json` together using Linux `renameat2` with
no replacement. Failure publishes nothing; an existing destination is refused.
This is a Linux capture interface. The stdlib importer supports ordinary ZIPs,
not split 7z archives or vendor-specific upstream schemas. Those require a
separately qualified outside-capture conversion and new hash pins. A source
volume is limited to 512 MiB and total compressed/expanded catalog bytes to
1 GiB; this implementation is not a streaming multi-GB upstream downloader.

The DB has `component_base` and one derived table per category. Base rows contain
the source columns plus canonical JSON `normalized` SI intervals and `provenance`.
Category tables add `<attribute>_min` and `<attribute>_max` decimal-text columns
for the applicable attributes. Decimal text retains exact values without binary
float rounding. NULL interval endpoints mean unknown. Provenance records the
volume path, one-based parsed record number (after headers, cumulative across
sorted ZIP CSV members), and canonical raw-row SHA-256. The volume SHA pins the
original bytes. Source URL/commit are recorded declarations, not authenticated
publisher identities.

The output manifest (`agent-ee.catalog/1`) retains source pins, snapshot date,
SQLite version, database SHA/size, canonical rows SHA, per-table row counts and
pruned count. Sorted insertion and fixed page size make repeated captures from
the same inputs byte-identical in the tested SQLite version. Canonical row
identity is the cross-version comparison; cross-version SQLite byte identity
is not claimed.

`load_catalog(snapshot_directory, expected_manifest_sha256)` verifies the pinned
manifest, DB size/hash, quick_check and exact table schema SQL (so key
constraints cannot be dropped). It then revalidates every base row as captured
data rather than trusting the hashes: canonical JSON text, integer or NULL stock,
decimal-text or NULL price, 0/1 flags, unique LCSC identity and unique source
record, and provenance naming a pinned volume with a positive record number and
SHA-256. Normalized SI intervals and every other derived field are recomputed from
the retained raw `attributes` and source cells with this module's normalization
and must match exactly. Rows last seen after `snapshot_date` or before `stale_before`
are refused. Category tables must equal the revalidated base rows. Counts and
canonical row identity are checked last; it returns `manifest` and the rederived
`rows`.

A content hash only proves the bytes are the pinned bytes. Without rederivation,
a newly pinned snapshot could carry intervals that contradict its own raw claims,
for example a `10 kohm +/-5%` row labelled with a +/-1% interval. Such snapshots,
including ones produced by a different normalization implementation, fail closed.
Because raw attribute text and price are canonicalized at capture, the stored
`raw_sha256` cannot be recomputed from the snapshot alone; it can be checked only
against the pinned source volume, which the snapshot does not include. The
manifest records no capture-execution receipt or producer attestation.

`catalog_check(...)` maps missing, corrupt or semantically invalid data to
`execution: failed`, `outcome: unknown`; its pass establishes snapshot integrity
and derivation consistency only. Successful results retain snapshot manifest/DB
hashes and date. Consumers must capture both
files into their immutable candidate and invoke this reader inside the existing
networkless sandbox. The `catalog` check registers this reader as informational
scope/3 coverage.
It cannot satisfy an acceptance requirement; it does not expand reviewer or
implementation permissions.

All availability and prices are **as of `snapshot_date`**. No result establishes
live inventory, order-time price, datasheet correctness, or assembly acceptance.

## CLI and captured check integration

`agent-ee capture-catalog SOURCE_JSON --source-root LOCAL_DIRECTORY --out NEW`
prepares a local snapshot and prints its manifest SHA-256. It performs no
network acquisition or upstream authentication. Bad source pins publish nothing.

A check with `kind: "catalog"` takes exactly `snapshot` (a candidate-relative
directory) and `manifest_sha256`. Capture both `manifest.json` and
`catalog.sqlite3` from that directory. Scope/2 and scope/3 baselines bind both
files, and the check invokes the existing reader in the networkless sandbox.
Retained `work/CHECK_ID/catalog.json` and execution records use the ordinary
coordinator evidence seal. Corrupt or unavailable bytes produce failed/unknown.
The application policy identity hashes `catalog.py`, `part_selection.py` and
the adapter along with the rest of the Python package; changing normalization
code invalidates public verification of earlier policy identity.

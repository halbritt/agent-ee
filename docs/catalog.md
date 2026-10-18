# Offline catalog snapshots

`agent_ee.catalog` captures explicitly pinned **local** CSV, SQLite, ordinary
ZIP/CSV files, or the jlcparts split ZIP64 feed described below. Capture never
downloads data or reads provider credentials. A separate host acquisition script
downloads pinned volumes before capture. Source catalog bytes and derived snapshots
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
An empty manufacturer name means unknown; it is retained without a guessed
name. LCSC identity, MPN, package and category remain required nonempty text.
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
This is a Linux capture interface. Each compressed source volume is limited to
512 MiB; all compressed volumes and the derived DB are each limited to 1 GiB.
Ordinary ZIP/CSV expansion is limited to 1 GiB. Native split ZIP64 extraction
has a separate 8 GiB limit and streams to private temporary files.

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
and derivation consistency only. Results retain the requested manifest pin;
successful results also retain the verified DB hash and snapshot date. Those
two fields are explicitly null when the snapshot cannot be verified. Selection
retains the verified date and DB hash even when its captured requirements file
is missing, unreadable or malformed. Consumers must capture both
files into their immutable candidate and invoke this reader inside the existing
networkless sandbox. The `catalog` check registers this reader as informational
scope/3 coverage.
It cannot satisfy an acceptance requirement; it does not expand reviewer or
implementation permissions.

Original synthetic tests cover both normalized inputs and the native
`source-db-v2` layout. They recapture pinned volumes byte for byte, reject
mismatched volumes before publication, exercise real HTTP byte/deadline failures,
and query 1 kΩ 0603 resistors in an isolated network namespace. Supplier data is
kept private; it is never used as a committed test fixture.

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


## Pinned jlcparts acquisition

The committed [source manifest](../catalog-sources/jlcparts-2026-09-14.json)
pins the exact raw URLs at jlcparts commit
`75daa67a78f3acf4ce18bab8dd68a86ac28c2818`: twelve 50 MiB pieces and the final
ZIP piece, 680,612,272 compressed bytes in total. Every piece has a SHA-256 and
size. Pins were established against that commit's Git tree blob IDs. The raw
member is a 5,940,703,232-byte SQLite `source-db-v2` database. Source schema and
values were inspected directly; the recovery documentation's older
`components` schema does not describe these bytes. The upstream software is
[MIT licensed](https://github.com/yaqwsx/jlcparts/blob/master/LICENSE).
Pinning a public source does not grant permission to redistribute its data.

Run acquisition **on the host**, with destinations in private scratch:

```sh
PYTHONPATH=src python3 scripts/acquire_catalog.py \
  --manifest catalog-sources/jlcparts-2026-09-14.json \
  --out /private/scratch/jlcparts-inputs --timeout 900
agent-ee capture-catalog catalog-sources/jlcparts-2026-09-14.json \
  --source-root /private/scratch/jlcparts-inputs \
  --out /private/scratch/jlcparts-snapshot
```

The download runs in an owned child with a total wall deadline (1–3600 seconds,
900 by default), not just a socket timeout. Each response is capped at its
pinned byte count and verified by SHA-256. HTTP failures, wrong lengths, hashes,
deadlines and cancellation leave no published inputs. Successful downloads and
their source manifest publish together through the same no-replacement atomic
directory publication used by catalog capture. Ambient proxy credentials are
ignored. The script never runs inside a check sandbox. No retries or live API
updates can change the pinned inputs.

`jlcparts-v2-stocked-split-zip` is an explicit compact import profile, not the
entire upstream universe. It retains present rows with positive captured stock
in supported categories: resistors, capacitors, inductors, diodes, connectors,
discrete LED indicators and linear/LDO regulators. Other families, absent parts
and zero-stock rows are excluded. Missing parts in this profile do not prove
unavailability in the complete upstream catalog. The pinned one-year
`stale_before` cutoff applies to the retained row's UTC `fetched_at` date.
`last_seen` records that date, so the publication date is not confused with an
individual part's fetch date. No stock or price is made current by capture.

All volumes are streamed into a private staging file and verified before any
archive metadata or source rows are interpreted. The adapter accepts only
contiguous `cache.z01` … `cache.zip` pieces, the pinned producer's fixed ZIP64
trailer, and a single regular `cache.sqlite3` member beginning on disk zero.
It translates split disk offsets, then delegates directory/decompression/CRC
validation to Python's standard `zipfile` reader. No 7-Zip dependency, shell
extraction or archive repair is used. Alternate layouts, encrypted members,
unsafe names, oversized expansion and CRC errors fail capture. Extraction files
are removed on success and failure.

The native adapter checks SQLite `quick_check`, the `source-db-v2` marker and
exact required table columns before conversion. It maps `mfr` to MPN,
`manufacturer` to manufacturer name, `base`/`expand` to Basic/Extended, and
`preferred` and `stock` directly. Empty manufacturer names stay unknown;
structural text is trimmed and its original spelling retained in attributes.
`price1` is the per-unit USD price at the smallest recorded MOQ, not a promise
that one piece can be ordered. Invalid prices or flags fail the build.
Original attribute strings remain under `upstream:` names; source category,
package, MPN, manufacturer and datasheet URL are retained. Passive intervals
require an explicit source tolerance. Recognized ratings feed the existing SI
normalizer, including the captured `Voltage Rating` (capacitors) and
`Voltage Rating (Max)` (headers) spellings; conflicting aliases remain unknown.
Existing snapshots keep their captured attributes: the importer does not add a
newly recognized rating to an old snapshot silently. Re-capture source bytes or
record an explicit derived capture with its source hashes and importer identity.
Absent, ambiguous or unparseable attributes stay unknown. This
initial regulator mapping requires both input/output range endpoints; it does
not infer omitted endpoints from a maximum or fixed nominal rating. Unknown limits cannot support selection constraints.

Output uses the existing `agent-ee.catalog/1` schema, normalization, typed
tables and atomic publication path. Its manifest retains every archive pin;
row provenance names the complete archive's final piece and the row's one-based
LCSC-ordered source position, with a hash of the native record. It does not claim
that a record lives entirely in that piece. The format name pins the import
profile. Repeat capture needs the same importer policy, manifest and volumes;
byte identity is qualified within the same SQLite version. Offline reads and checks retain
the existing manifest/DB/date identity and evidence-authority rules.


## Full-feed runtime qualification

The pinned stocked profile produced 334,455 rows in a 749,039,616-byte DB on
SQLite 3.45.1. A real networkless `select_parts` query for 1 kΩ ±1%, 0603
returned 179 eligible candidates and selected C21190, retaining Basic=true,
stock=8,013,731 and snapshot date 2026-09-14. That private qualification used a
900-second process limit and completed in 327 seconds on the busy host.
These are captured facts, not current supplier availability.

The shared reader still revalidates every row before selection. It sorts narrow
row references after decoding, checks category base cells through a SQLite join,
and streams canonical row hashing without building another full JSON catalog.
Repeated SI ratings share a bounded cache of immutable endpoints; returned row
intervals remain independent dictionaries. The cache key includes the caller's
Decimal rounding, exponent limits and trap policy. Canonical row hashes, snapshot schema,
raw claims, row counts, exact JSON types and unknown semantics are unchanged.
No row is dropped and no validation receipt substitutes for rederivation.

AGENTEE-128 takes the ticket's explicit option to declare a larger per-check
ceiling. Both helpers now allow **600 seconds**, still shortened by the enclosing
run deadline. The original 120-second bound timed out for both helpers on the
loaded qualification host, including the first optimized integrity probe. The
600-second bound allows full rederivation and the selection report's complete
exclusion evidence; it matches the existing catalog-consuming package and
assembly checks. It does not guarantee completion on every host or for every
requirements list. Timeout remains interrupted/unknown coverage when the run
deadline expires, or timeout/unknown when the helper reaches its own ceiling.

Repeat the fixed 1 kΩ ±1%, 0603 probe against an existing captured snapshot:

```sh
PYTHONPATH=src python3 scripts/qualify_catalog_runtime.py \
  /private/scratch/jlcparts-snapshot \
  6f5a2767e6a7f54840e9bd17cccd1ebe54585d0c1b7d18049cb141013eda2d09 \
  --out /private/scratch/new-runtime-qualification
```

Use the actual pin if re-capture changes the manifest. The script creates a
scope/3 baseline, captures both full catalog files and owned requirements, runs
the registered networkless helpers, and verifies their ordinary coordinator
seal. `summary.json` binds the baseline, seal, helper reports and snapshot pins,
and records each invocation's timeout and elapsed wall time. Exit zero requires
both checks to pass within their full declared helper budgets. A shorter run
budget, missing output or failed check cannot qualify. The run remains an
incomplete checks-only review: runtime qualification grants no independent review
or design acceptance. All output, including captured rows and selection evidence,
belongs in private scratch. Only hashes, provenance and measured summaries may
be committed.

On 2026-10-18 the registered helpers completed a sealed private full-feed run
on Linux x86-64, Python 3.12.3 and SQLite 3.45.1:

| Helper | Elapsed wall time | Declared ceiling | Result |
| --- | ---: | ---: | --- |
| `catalog` | 104.996 s | 600 s | ok / pass |
| `part_selection` | 128.744 s | 600 s | ok / pass |

The selection retained all 334,455 rows in its evaluated universe, returned the
same 179 eligible candidates and selected C21190. The manifest SHA-256 remains
`6f5a2767e6a7f54840e9bd17cccd1ebe54585d0c1b7d18049cb141013eda2d09`, DB
SHA-256 `a32e101342e43e2358d74a0a19d0e63231102de802461a97a0582f025f461a11`,
and canonical rows SHA-256
`73f49d9577451fa396a2b794cf9829d9477c0d49c69e401cbcc3da9560c6471f`.
The coordinator evidence seal SHA-256 is
`bbd78ba037d4b6f170da38139892a79d807fa7828a3af041c9e80cb09a000109`;
the baseline file SHA-256 is
`528a23248980270551420624303f2149ca410548fa84dfc7b9c27a2ee6fb4549`.
These observations include process startup, complete semantic validation and
report writing, but exclude host capture and public verification overhead.
The selection exceeded 120 seconds. Earlier 120-second probes timed out too;
this is qualification under the declared 600-second ceiling, not a universal
120-second guarantee or a controlled speedup factor.
The host had other fleet work running; no cache flush, CPU reservation, latency
percentile or larger multi-part workload was qualified.

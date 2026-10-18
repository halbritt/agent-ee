# Deterministic generic-part selection

`agent_ee.part_selection.select_parts(snapshot_directory, manifest_sha256,
requirements)` reads a [pinned offline catalog](catalog.md) and returns an
`agent-ee.selection/1` record. It performs no writes, network calls, inference,
or clock reads. Both catalog files must already be captured immutable inputs.
Invoke selection in the existing networkless sandbox; there is no live supplier
lookup or fallback parts list.

Requirements use `agent-ee.part-requirements/1`:

```json
{
  "schema": "agent-ee.part-requirements/1",
  "build_quantity": 10,
  "stock_floor": 100,
  "parts": [
    {
      "id": "R1",
      "category": "resistor",
      "package": "0402",
      "quantity_per_board": 1,
      "constraints": {
        "resistance": {"kind": "within", "nominal": "10 kohm", "tolerance_percent": "1"},
        "voltage": {"kind": "at_least", "min": "50 V"},
        "temperature": {"kind": "covers", "min": "-40 degC", "max": "85 degC"}
      }
    }
  ]
}
```

All fields are required, unknown fields are refused, and part IDs must be unique.
Quantities must be positive integers; the stock floor is a nonnegative integer.
Booleans are not quantities. Packages match exactly; the selector does not guess
KiCad package aliases. Attribute names and units follow the catalog contract,
and constraints must be applicable to the declared generic category. Selection
does not infer a device function, pin contract, footprint mapping or IC suitability.

Each constraint explicitly declares its interval relation:

| Kind | Candidate must satisfy |
| --- | --- |
| `within` | Entire candidate interval is contained in the required interval. |
| `covers` | Candidate supported range contains the required operating range. |
| `at_least` | Candidate interval minimum is at least `min`. |
| `at_most` | Candidate interval maximum is at most `max`. |

`within` accepts either `nominal` plus positive `tolerance_percent` (below 100),
or distinct explicit `min` and `max` quantities. `covers` requires distinct
`min` and `max`. Singletons, reversed intervals, bare nominal requirements and
wrong units are user errors. Resistors, capacitors and inductors require a
`within` constraint on their value. Their catalog value also needs an explicit
nonzero tolerance/range: a bare nominal catalog value is unknown, not a
zero-tolerance part. Other absent or unparseable catalog attributes exclude the
row as unknown. A minimum-voltage requirement uses the catalog rating's **lower**
endpoint, so `25 V .. 100 V` cannot establish a 50 V minimum rating.

The minimum available stock is `max(build_quantity * quantity_per_board,
stock_floor)`. Unknown stock excludes a row; known low stock is a definite
exclusion. After choosing a part, its required quantity is reserved from the
snapshot stock for later requirements. The stock floor applies to stock still
available before that selection. This is deterministic allocation within this
record, not a supplier reservation or order-time availability guarantee.

The selector repeatedly handles the requirement with the fewest eligible
candidates, then the most constraints, then lexical part ID. It re-evaluates
after each allocation. This is a deterministic greedy algorithm; it does not
claim to find every globally feasible allocation. A later empty set stays
unknown rather than backtracking into an undocumented choice.

Eligible candidates rank by Basic, Preferred, Extended; known price before
unknown price; ascending decimal unit price; descending available stock; then
numeric LCSC ID. Basic wins even if a Preferred part is cheaper. Missing price
is explicitly retained as unknown, never fabricated. All rows already satisfy
the stock floor before ranking. The record exposes rank fields, ordered keys,
and the deciding key against the runner-up (or the sole-candidate reason).

Results bind the exact requirements hash, snapshot manifest hash, DB hash and
snapshot date. Each part retains normalized constraints, chosen full row with
raw provenance, every ranked candidate and its matched intervals, every rejected
row with reasons/unknown flags, and exclusion counts. Counts are reason counts;
one excluded row can contribute more than one reason. `excluded_rows` is the
separate row count. Repeating the same inputs produces canonical byte-identical
JSON via `agent_ee.storage.canonical`.

The acceptance tests compare these bytes with output from separate networkless
Bubblewrap processes using different Python hash seeds. Stock-boundary tests
exercise both build quantity and stock floor: exactly sufficient Basic stock
wins over a cheaper Preferred part; one unit short excludes the Basic row and
records `low_stock`.

`execution: ok, outcome: pass` means all parts matched the declared catalog
constraints. It does not grant engineering acceptance. An empty candidate set
has `outcome: unknown`, reason `no candidate`, and null `chosen`. The
`selection_check` adapter maps invalid requirements and missing, corrupt or
semantically invalid snapshots to `execution: failed, outcome: unknown` with an
error and no fallback choice. Selection uses only intervals that `load_catalog`
has rederived from each row's retained raw attributes. A repinned snapshot whose
stored intervals disagree with its raw claims is never used to select a part.
The direct `select_parts` API raises the underlying input error. The integration
below supplies scope and baseline registration without new
reviewer access or a separate acceptance path.

`selection_check` accepts either a requirements object or a `pathlib.Path` to
the captured JSON file. The sandbox CLI uses the path form so that snapshot
verification happens before requirements loading. Missing files, invalid UTF-8,
invalid JSON and duplicate JSON keys retain the verified snapshot date and
hashes in their failed/unknown report. If the snapshot itself cannot be verified,
its date and DB hash are null; an unverified declaration cannot supply provenance.

Prices, stock, and classifications are explicitly **as of the snapshot date**.
Datasheet facts, package/pin binding and later assembly-readiness verification
remain separate checks. The tests use original synthetic catalog rows, including
a 33-row Basic/stock ranking case; they do not reproduce or qualify the named
third-party Konnect fixture.

The [owner decision of 2026-10-18](decisions.md#konnect-fixtures-are-replaced-by-owned-equivalents)
amended the named-fixture acceptance criterion to the owned controls. The
33-row synthetic case qualifies that ranking behavior without importing Konnect
material. Its AGPL-3.0 fixture remains outside this repository's MIT-only boundary.

## Requirement-bound check

Register `kind: "part_selection"` with exactly these params:

```json
{
  "snapshot": "data/catalog",
  "manifest_sha256": "<trusted manifest SHA-256>",
  "requirements": "data/parts.json",
  "requirement_id": "parts"
}
```

Use the actual manifest SHA-256 in place of the placeholder. The check must map
to exactly one baseline requirement with ID `parts` and source path
`data/parts.json`. That file uses the existing part-requirements schema above;
no separate selection schema is introduced. Multiple parts form one conjunctive
requirement. Split checks explicitly when separate baseline requirements are
needed. Capture the requirements file and both snapshot files. All three are
immutable baseline inputs in scope/2 and scope/3.

Scope/3 describes the bounded predicate `catalog:declared-part-constraints/1`.
A pass covers only those declared constraints against the approved snapshot's
claims. An empty candidate set remains unknown, so it cannot become a sealed
defect witness. Invalid inputs return failed/unknown. The retained sandbox
report enters the existing coordinator seal, public verify and witness gates;
selection has no private acceptance or adjudication route. The helper has a
600-second ceiling, shortened by the enclosing run deadline. The
[full-feed runtime qualification](catalog.md#full-feed-runtime-qualification)
records its workload and measured bound; larger requirement sets are not covered
by that one-query measurement. A checks-only run
still has incomplete independent review, even if its selection check passes.

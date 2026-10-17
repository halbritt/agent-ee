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

`execution: ok, outcome: pass` means all parts matched the declared catalog
constraints. It does not grant engineering acceptance. An empty candidate set
has `outcome: unknown`, reason `no candidate`, and null `chosen`. The
`selection_check` adapter maps invalid requirements and missing/corrupt snapshots
to `execution: failed, outcome: unknown` with an error and no fallback choice.
The direct `select_parts` API raises the underlying input error. Shared scope,
CLI, baseline and check registration belong to the integrator; this module
adds no acceptance authority or new reviewer access.

Prices, stock, and classifications are explicitly **as of the snapshot date**.
Datasheet facts, package/pin binding and later assembly-readiness verification
remain separate checks. The tests use original synthetic catalog rows, including
a 33-row Basic/stock ranking case; they do not reproduce or qualify the named
third-party Konnect fixture.

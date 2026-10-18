# Interval assertion fixtures

Owned synthetic inputs for the `interval_assertions` check (AGENTEE-61).

- `catalog-source.csv` holds seven invented catalog claims (LCSC numbers
  C900001-C900007, manufacturer "Owned fixture"). Tests capture it into a pinned
  `agent-ee.catalog/1` snapshot with `catalog.capture_catalog`; no snapshot
  database is stored here.
- `requirements/divider.assertions.json` declares a 10 V +/-1 % rail, a
  22 kohm/10 kohm divider and a C1 voltage-rating relation against
  `requirements/system.md`.

Seeded variants (5 % or 20 % R2, an untoleranced R2, a 6.3 V C1, a malformed
expression and a zero-crossing denominator) are derived by the tests from these
files. The hand-computed divider reference is in `docs/interval-assertions.md`.

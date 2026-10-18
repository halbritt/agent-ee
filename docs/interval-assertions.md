# Interval assertions

`interval_assertions` checks owner-declared algebraic relations, such as
divider outputs, rating margins, current limits and RC products, across part
tolerances. It evaluates them forward, in the worst case. It reports a verdict
per assertion and per requirement ID. Pinned catalog claims and declared
requirement intervals are the inputs. The check never back-solves, never
derives the equation from the schematic, and never uses binary floating point
in a bound.

## Parameters

```json
{"id": "intervals", "kind": "interval_assertions", "params": {
  "assertions": "requirements/divider.assertions.json",
  "snapshot": "catalog", "manifest_sha256": "<64 hex>"}}
```

`assertions` is required. Add `snapshot` and `manifest_sha256` together when any
parameter cites the catalog. They name an [`agent-ee.catalog/1` snapshot](catalog.md),
which is verified and rederived exactly as the catalog check does. An optional
`parts_manifest` names an [`agent-ee.parts/1` manifest](approved-parts.md) that
pins the same snapshot. Catalog parameters can then cite a logical `part` ID
instead of an LCSC number. All of these files are captured as immutable baseline inputs.

The capability refuses any requirement ID cited by the document unless the
baseline declares it, sources it from the assertion document, and binds it to
this check. Each assertion publishes one predicate,
`interval-assertion:<assertion id>`.

## Document

```json
{
  "schema": "agent-ee.interval-assertions/1",
  "correlation": "shared-symbol/independent-parameters",
  "parameters": {
    "vin": {"source": "declared", "requirement": "R-VIN", "unit": "V", "nominal": "10 V", "tolerance_percent": "1"},
    "r1": {"source": "catalog", "lcsc": "C900001", "attribute": "resistance", "role": "toleranced", "reference": "R1"},
    "r2": {"source": "catalog", "lcsc": "C900002", "attribute": "resistance", "role": "toleranced", "reference": "R2"},
    "c1_rating": {"source": "catalog", "lcsc": "C900006", "attribute": "voltage", "role": "rating", "reference": "C1"}
  },
  "assertions": [
    {"id": "vout-band", "requirement": "R-VOUT", "expression": "vin * r2 / (r1 + r2)", "unit": "V",
     "target": {"nominal": "3.3 V", "tolerance_percent": "10"}},
    {"id": "c1-voltage-rating", "requirement": "R-C1-RATING", "lhs": "vin", "op": "<=", "rhs": "c1_rating", "unit": "V"}
  ]
}
```

- Declared parameters cite a requirement ID and use `nominal` +
  `tolerance_percent`, `min` + `max`, or a single side. A bare `nominal` or a
  single side is an unbounded operand.
- Catalog parameters cite `lcsc` (or `part`), a catalog `attribute` with its
  catalog unit, and a `role`. For a `toleranced` value, a catalog claim without
  a tolerance is unbounded. A `rating` accepts the exact claimed rating.
  Temperature is refused.
- Expressions use `+ - * / ( )`, lower-case parameter names and dimensionless
  decimal literals. Units are checked dimensionally over V, A and s: `ohm`, `F`,
  `H`, `W`, `Hz` and `1`. Adding unlike units, or a result unit that differs
  from the declared `unit`, is malformed.
- Targets use the declared-interval forms, and both bounds are closed.
  A relation `lhs <= rhs` or `lhs >= rhs` is evaluated as one difference, so a
  parameter shared by both sides stays correlated.
- An assertion may use at most 12 parameters.

## Semantics

The declared correlation rule is atopile's. A parameter name denotes one value
wherever it occurs. Distinct parameters, including separate declared intervals,
vary independently over their whole intervals. No other rule is accepted.

Every bound is an exact `fractions.Fraction`. The report keeps the rational text
and a decimal display. A non-terminating decimal is rounded outward to 15
significant digits, and `rounded_outward` is set.

1. Any unknown operand (unbounded, missing catalog row or claim) makes the
   assertion `unknown`. It never passes.
2. Natural interval arithmetic with forward-mode interval derivatives gives a
   sound enclosure. A division by an interval that contains zero is unbounded,
   and the assertion is `unknown`.
3. A parameter whose derivative enclosure is sign-definite over the box is
   pinned to its extreme endpoints. This removes the overestimate from repeated
   occurrences. `monotonicity` records each parameter as nondecreasing,
   nonincreasing, fixed or unproven.
4. Every endpoint combination of the non-degenerate parameters is evaluated
   exactly. `exact` is true when the enclosure equals this attained corner hull,
   and `bounds.min`/`bounds.max` then name the attaining endpoint of every operand.
5. The verdict is `pass` when the enclosure lies inside the target. It is
   `defect` only when an attained corner lies outside the target. That corner is
   the `witness`, and `contributions` rank each parameter by the output swing
   from flipping it. Otherwise the verdict is `unknown`; an over-wide enclosure
   from unproven monotonicity never becomes a defect.

The check outcome is `defect` when any assertion is a defect, otherwise
`unknown` when any is unknown, otherwise `pass`. `details.requirements` lists the
verdicts for each requirement ID. The summary names each defective assertion,
its requirement, the computed and target intervals, and the largest-swing part.

## Origin trace and evidence

The coordinator first validates the document and its requirement binding. A
malformed document refuses the scope/3 audit. A malformed document that reaches
the check gives `execution: malformed`. The coordinator then runs:

```sh
/usr/bin/python3 -m agent_ee.interval_assertions /candidate DOCUMENT \
  --snapshot SNAPSHOT --manifest-sha256 MANIFEST_SHA256 --out /work/intervals.json
```

This runs in the sandbox with a 300 s ceiling. `intervals.json`
(`agent-ee.interval-assertion-result/1`) records:

- the input hashes: the assertion document, the catalog manifest pin, the
  database hash and date, and the parts manifest;
- every resolved parameter interval;
- per assertion, the target, computed interval, corner hull, monotonicity,
  verdict, reasons and witness.

Each operand origin carries one of two traces:

- **Declared**: the requirement ID, document path and document hash.
- **Catalog**: the LCSC number, attribute, role, raw claim text, catalog
  manifest/database hashes, and the row's raw-source provenance. It also carries
  the reference or part ID, and the parts manifest hash when one is used.

The coordinator accepts a report only if it is bound to the captured document
hash and its verdict list is complete and consistent. A failed catalog
verification is `execution: failed`, outcome unknown.

## Hand-computed reference

The owned [fixtures](../fixtures/interval-assertions/README.md) declare a
10 V ±1 % rail, R1 = 22 kΩ and R2 = 10 kΩ. With 1 % resistors:

- v_min = 9.9 · 9900 / (22220 + 9900) = 891/292 ≈ 3.05137 V
- v_max = 10.1 · 10100 / (21780 + 10100) = 10201/3188 ≈ 3.19981 V

Both lie inside 3.3 V ±10 % = [2.97, 3.63] V, so the result is pass. Seeding a
5 % R2 gives v_min = 9405/3172 ≈ 2.96501 V. A 20 % R2 gives 3960/1511 ≈ 2.62078 V.
Both are defects, and R2 is the largest contributor. A 6.3 V C1 rating against
the [9.9, 10.1] V rail gives a margin of [−3.8, −3.6] V, a defect. An
untoleranced R2 is unknown.

## Overlap with other checks

[`passive_corners`](passive-corners.md) already covers the time-domain response
of one unloaded RC low-pass by simulation, and `coupled_margins` joins measured
tool results across domains. Do not restate either relation here. This check is
for static algebraic relations that neither one simulates.

## Limits

The owner declares the equation. Nothing proves that it describes the netlist.
Catalog claims are snapshot claims, not datasheet facts. There is no temperature,
aging, loading or distribution model. The worst-case box assumes independent
parameters; a correlated-tolerance declaration (for example, a matched resistor
network) is not supported. A non-monotone repeated parameter can leave a
too-wide enclosure, which is reported as unknown. The check does not bisect
the box.

The mechanism follows atopile's solver ideas: superset assertions on picked
parameters, contradictions traced to their sources, and same-symbol
correlation. atopile is MIT-licensed; no atopile code or design files are used.

Run the focused tests with:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest tests.test_interval_assertions -v
```

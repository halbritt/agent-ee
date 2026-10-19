# JLC Assembly-Readiness Check (`assembly_readiness`)

`assembly_readiness` is the orderable-BOM gate for pipeline stages 2 (parts)
and 7 (fab). It checks every populated, on-board, in-BOM component of the
captured KiCad schematic. Each must carry the required sourcing and rating
properties and resolve by exact LCSC ID to a captured offline catalog snapshot.
That snapshot must show fresh stock for the declared board quantity, and the
design must stay within any declared Extended-part budget.
Captured hand-fit deviations are reported separately under the rules below.

The check does not pick replacements, query live stock or price the board.
Live JLCPCB stock reaches it only through an owner-run
[stock capture](jlc-stock-capture.md) made before candidate capture.

## Inputs and evidence

- **Components**: the check runs the native
  `kicad-cli sch export netlist --format kicadxml -o /work/design.xml SCHEMATIC`
  inside the executor sandbox (600 s ceiling) and reads only that XML. KiCad
  resolves the sheet hierarchy, per-instance references of reused sheets,
  multi-unit symbols, user fields and the native DNP / in-BOM / on-board
  attributes. This is the same source `fab_export` builds the JLC BOM from.
- **Completeness gate**: before the export, every file of the
  [verified hierarchy](check-reference.md#hierarchy-validation) must parse as
  one complete S-expression with a `kicad_sch` root, using the shared
  `symbol_library.parse_sexpr`. KiCad 10 exports a truncated child sheet with
  exit 0 and silently drops its components, so a netlist alone is not trusted.
  The netlist's `<design><sheet>` count must also equal the hierarchy instance
  count.
- **Coverage gate**: parsing and sheet counts are not enough. KiCad 10 also
  drops every component of a complete child sheet it cannot load, such as one
  with `(in_bom maybe)` or an unknown token. It still exits 0 and still
  reports the sheet. After the export, every placed symbol occurrence (sheet
  instance path plus symbol UUID) in the captured hierarchy must therefore
  bind to one netlist `<comp>` (`sheetpath`/`tstamps`), and every netlist
  component must bind back to an occurrence. The audit is the shared
  `native_binding.virtual_symbols` occurrence walk. Only `#`-referenced
  occurrences, such as power symbols and `PWR_FLAG`, may be missing from the
  export. Any other gap fails, and the message names the missing references,
  the problem kinds and the affected sheet files. A sheet set that differs
  from the hierarchy also fails. The report records
  `netlist.hierarchy_coverage` (`bound_components`, `exporter_omitted`).
- **Catalog**: `params["catalog"]`, either an `agent-ee.catalog/1` manifest
  (whose SQLite `database.path` is size- and SHA-256-pinned) or a JSON snapshot
  (`agent-ee.catalog-snapshot/1` or `agent-ee.jlc-catalog/1`). Canonical SQLite
  decimal-text prices are decoded with the catalog decimal reader. A null
  `stale_before` is accepted as an absent policy; stock remains unknown unless
  the check supplies an explicit maximum age or cutoff.
- **JLCPCB stock capture**: an [`agent-ee.jlc-catalog/1`](jlc-catalog.md) file with its required
  `requests` list comes from the owner-run
  [JLCPCB stock producer](jlc-stock-capture.md). The check re-derives every
  part from the retained raw responses, which are immutable baseline inputs.
  The coordinator binds evaluation to the generation receipt in campaigns
  (missing or inapplicable receipts fail) and to review time in standalone checks,
  with `max_age_hours: 24`. Direct evaluation calls require an explicit
  `as_of` and staleness (`unsupported` otherwise). It fails a capture older
  than that staleness, dated after the run, or lacking an observation for a
  populated LCSC id (never requested, or a failed request). Only an observed
  not-found gives `unknown_lcsc_in_catalog`. The report carries
  `catalog.capture`, including per-part response hash verdicts.
  Missing requests, unknown keys and non-JLCPCB sources are refused.
- **Retained evidence**: `work/<id>/design.xml`, the executor record and
  `work/<id>/assembly-readiness.json`. The report carries the hashes of each
  schematic file (`schematic.files`), of the netlist (`netlist.sha256`) and
  of the catalog (`catalog.sha256`, `catalog.database_sha256`).

## Check kind and capability contract

- **Check kind:** `assembly_readiness`
- **Native tool:** `kicad-cli` (declared in the launch expectation).
- **Predicates (`agent-ee.scope/3`):**
  - `assembly:bom-properties`
  - `assembly:lcsc-binding`
  - `assembly:stock-coverage`
  - `assembly:passive-ratings` (when `require_passive_ratings` is `true`, the default)
  - `assembly:extended-parts-limit` (when `max_extended_parts` is set)
  - `assembly:rating-provenance` (when `rating_provenance` is set)
  - `assembly:operating-temperature` (when `operating_temperature` is set)
- **Captured inputs:** `scope.schematic`.
- **Immutable baseline inputs:** `params["catalog"]`, plus `database.path` for
  an `agent-ee.catalog/1` manifest, or every retained `responses/*.json` file
  for a JLCPCB stock capture, plus `rating_provenance.receipt` and
  `rating_provenance.report` when declared, plus the DigiKey capture manifest
  and every retained response it names when `operating_temperature` is set.

## Parameters (`params`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `catalog` | `str` (required) | — | Captured relative path to an `agent-ee.catalog/1` manifest or a JSON catalog snapshot. |
| `catalog_sha256` | `str \| None` | `None` | Optional lowercase hex SHA-256 pinning the catalog file. |
| `boards` | `int >= 1` | `1` | Boards to assemble; required stock per LCSC ID is `quantity_per_board * boards`. |
| `required_properties` | `list[str \| {"any_of": list[str]}]` | `["Footprint", "LCSC", "Manufacturer", {"any_of": ["Partnumber", "PartNumber", "MPN", "CompanyPN"]}]` | Properties every populated component must carry. An entry is one name (`"PartNumber"`), a pipe-delimited any-of group (`"PartNumber\|MPN\|CompanyPN"`) or `{"any_of": [...]}`. `LCSC` is always enforced, whether or not it is listed. |
| `require_passive_ratings` | `bool` | `true` | Populated resistors need a power/voltage rating (`Power`, `PowerRating`, `Power_Rating`, `Rating`, `Voltage`, `VoltageRating`, `Voltage_Rating`, `PARAM_power`, `PARAM_voltage`); populated capacitors need a voltage rating (`Voltage`, `VoltageRating`, `Voltage_Rating`, `Rating`, `WVDC`, `PARAM_voltage`). The derating adapters read these. |
| `max_extended_parts` | `int >= 0 \| None` | `None` | Maximum unique Extended-tier LCSC IDs. Basic, Preferred and Extended counts are always reported. |
| `max_stock_age_days` | `number >= 0 \| None` | `None` | Maximum age in days of a part's `last_seen` relative to `as_of`. |
| `max_age_hours` | `number >= 0 \| None` | `None` | Maximum age in hours of a part's `last_seen` relative to `as_of`. |
| `stale_before` | `str \| None` | catalog `stale_before` | ISO date; earlier `last_seen` observations are stale. |
| `as_of` | `str \| None` | catalog `snapshot_date` for legacy snapshots | Evaluation date or ISO-8601 timestamp. For JLCPCB captures the coordinator overrides this with the generation receipt completion time (campaign) or review time (standalone); direct evaluation API calls require it. |
| `stock_floor` | `int >= 0` | `0` | Minimum stock per LCSC ID: `threshold = max(quantity_per_board * boards, stock_floor)`. |
| `deviations` | `str \| None` | `None` | Captured mutable `agent-ee.part-deviations/1` file shared with `part_decisions`. |
| `hand_fit_policy` | `object \| None` | `None` | Citation of a captured owner decision: `source`, `sha256`, `line`, `quote` and exact `identities`. See [owner hand-fit policy](#owner-hand-fit-policy). |
| `rating_provenance` | `object \| None` | `None` | Receipted binding-fill provenance: `receipt` (captured stage receipt of the `binding-fill` step), `receipt_sha256` (coordinator pin) and `report` (captured `binding-fill.json`). See [receipt-bound rating provenance](#receipt-bound-rating-provenance). |
| `operating_temperature` | `object \| None` | `None` | Declared ambient `min_c`/`max_c` (finite degC) and a captured DigiKey record per reference: `capture`, `capture_sha256` (pin) and `parts` (`{reference: key}`). See [DigiKey operating temperature](#digikey-operating-temperature). |

`property` lookups for `Footprint`, `LCSC` and `Manufacturer` also accept the
common aliases (`footprint`, `LCSC Part`, `jlcpcb_part`, `Mfr`, ...); other
names are exact. Empty and `~` values count as missing.

## Declared hand-fit deviations

An assembly entry
in the [shared deviations format](part-decisions.md#captured-deviations) must
name its exact `original.mpn`, `assembly` (`consigned` or `hand_soldered`),
`purchase_source`, `reason`, `stock_deviation.reason`, and captured
`evidence_refs`. Both modes mean fitting after delivery.

Every matched native component must be populated, on board and in BOM, with
the exact `Partnumber` and, when specified, `LCSC`. It must carry
`JLCPCB_Assembly=not_assembled`. Missing, duplicate, unmatched or invalid
declarations are defects. A marker without a valid declaration is a defect.
A NO_LCSC marker alone is also a defect. Part-binding's marker spellings
remain valid on declared exceptions; an absent LCSC is allowed too.
Manufacturer, footprint, part number and applicable rating policies still run.

The check refuses an exception if any captured catalog row for the exact MPN
or native LCSC ID has positive stock, including stock insufficient for the
requested build. A matching row with null stock cannot establish unavailability
and is refused. JLC capture freshness and failed/unrequested-ID checks still
apply to declared parts with an LCSC ID. For parts without a catalog identity,
the purchase source and cited declaration are reviewed evidence of the
exception, not an automated proof of distributor absence. No network is used.

`declared_deviation` lists each valid exception with its native reference,
sheet and UUID, MPN, LCSC, assembly mode, purchase source, reasons, evidence
hashes and deviation-file hash. Its `stock_state` is `declared_deviation`.
These parts remain visible in `populated_refs` and `components`, with
`lcsc_status=declared_deviation`; they do not enter `parts`, stock quantities
or tier counts. `counts.declared_deviation` and the summary distinguish them
from JLC assembly parts. Every remaining component grades normally. Substitution
records remain the `part_decisions` check's presence obligation.

## Owner hand-fit policy

A declaration of the stock-unavailable class above cannot express a part the
owner wants hand-fitted whatever JLCPCB stocks. An assembly entry with
`"policy": "owner_hand_fit"` is a distinct class. The deviations file only
names the class; its authority is the check's `hand_fit_policy` parameter:

```json
{
  "hand_fit_policy": {
    "source": "requirements/spec.md",
    "sha256": "<sha256 of the captured source>",
    "line": 76,
    "quote": "<exact text from that line>",
    "identities": [{"mpn": "776228-1", "lcsc": "C498958"}]
  }
}
```

`source` must be a captured input other than the deviations file. It becomes an
immutable input. The check refuses the run (`failed`) if the source bytes
differ from `sha256` or `quote` is not a substring of line `line`. A
malformed citation, or an identity whose MPN is not a whole token in `quote`,
is `unsupported`. A paraphrase is not accepted. The LCSC is bound by the
scope, not by the quote.

The quote only locates the statement. The checker reads the whole Markdown
unit holding line `line`, not the quote, so a quote cut off before a condition
gets the same result. The unit is a list item plus its continuation lines, a
table row, or a paragraph. The run is refused (`failed`) unless every listed
identity is named unconditionally:

- the unit affirmatively declares a hand-fit (`hand-fit…`, `hand-solder…`,
  `fitted by hand` or `soldered by hand`, not negated);
- the MPN occurs exactly once in the unit;
- the unit has balanced parentheses. It is not in a code block, a nested list
  item, or a list item after a lead-in that ends in `:`. Its nearest heading
  has no conditional qualifier;
- every conditional qualifier in the unit (`if`, `unless`, `when`, `where`,
  `provided`, `only`, `except`, `should`, `assuming`, `until`, `once`,
  `while`, `otherwise`, `in case`, `as long as`, `subject to`, `unstocked`,
  `unavailable`, `out of stock` and similar) must be confined. A qualifier is
  confined only when it sits in the last comma-separated conjunct of a
  parenthesized enumeration, that conjunct starts with `and` or `or`, names a
  part-like token and has no predicate (`is`, `are`, `be`, `fitted`,
  `hand-fit…`). Any other qualifier has an unbounded scope, which refuses every
  identity in the unit. An identity inside a confined conjunct is named only
  conditionally and is refused.

A conditionally named part can only use the stock-unavailable class.

A policy entry is admitted regardless of positive or zero stock only when:

- its `original` equals one listed identity exactly (MPN and LCSC, null
  included);
- with an LCSC, a catalog row for it exists, has that exact MPN, known stock
  and a fresh `last_seen` (same staleness rules as BOM parts; a JLC capture
  must also pass its run binding and age gate);
- without an LCSC, no catalog row has the MPN and no matched native component
  carries a real LCSC ID;
- every other rule above holds: populated in-BOM match, the
  `JLCPCB_Assembly=not_assembled` marker, purchase source, reasons, captured
  evidence and no duplicates.

An entry without `policy` keeps the stock-unavailable class unchanged: positive
or null stock is still refused, even when a citation is configured. A `policy`
entry without a configured citation, for an unlisted identity, or with an
unsupported value is an `invalid_assembly_deviation` defect. Accepted policy
entries report `stock_state: owner_hand_fit`, `policy`, `policy_citation` and
`catalog_observation`; the report's `hand_fit_policy` echoes the verified
citation. They stay outside `parts`, stock quantities and tier totals.

For the telemetry pod, the canonical owner text is
[fixtures/telemetry-pod/spec.md](../fixtures/telemetry-pod/spec.md) line 76
(acceptance criterion 5): "(the 776228-1 header, and the NEO-M9N or SMA
connector if unstocked)". The header is admitted as a policy identity. Listing
NEO-M9N or SMA in `hand_fit_policy.identities`, with the full quote or a
shortened one, refuses the run. They stay stock-unavailable declarations.
Pod adoption belongs to the pod lane.

## Fabrication exclusion

For fabrication, set the `fab_export` parameter `assembly_exclusion` to
`JLCPCB_Assembly=not_assembled` and put the marker on both symbol and footprint.
The [fab exporter](fab-export.md) omits the part from BOM and CPL and lists
its designator and rule in the manifest. Run both checks: the exporter does
not validate sourcing declarations. Reports use ordinary coordinator sealing.

## Component classification

The native netlist omits power symbols, `PWR_FLAG` included. The remaining
components fall into four disjoint buckets, in this order:

1. **`ignored_non_assembly`**: native `exclude_from_board` (`(on_board no)`),
   for example a mounting hole with no footprint on the board, or a `#`
   reference. Never flagged.
2. **`excluded_dnp`**: native `(dnp yes)` only. Not checked for properties,
   LCSC or stock.
3. **`excluded_from_bom`**: native `(in_bom no)` without DNP, for example an
   on-board mounting hole or test point. Each is reported as an `info`
   finding (`code="exclude_from_bom"`), distinct from DNP, because it is
   omitted from the BOM without a DNP mark on the assembly drawing.
4. **`populated`**: everything else. Checked against all policies.

Native flags are the valueless netlist properties `dnp`, `exclude_from_bom`
and `exclude_from_board`. A *user field* that merely uses a DNP-style name
(`DNP`, `dnp`, `DNF`, `exclude_from_bom`, `exclude_from_board`) with a truthy
value, or a `Value` of `DNP`/`DNF`/`DNI`, does not exclude the component.
KiCad's exports and the JLC BOM still place it, so it stays populated and
gets a `non_native_assembly_marker` defect naming the marker.

## Findings and three-state stock

Each unique LCSC ID on populated components is aggregated across the design
(`quantity_per_board` = number of references, counting each instance of a
reused sheet).

- **`defect`**
  - `missing_property`: a required property or any-of group is absent.
  - `missing_rating_property`: populated R or C without a rating property.
  - `invalid_rating_provenance`: a populated R or C carries an
    `AGENT_EE_Ratings` field ([source-derived ratings](binding-fill.md#source-derived-passive-ratings))
    that is malformed, names a field that is not a rating for its kind, or
    whose value differs from the native `Power`/`Voltage` property. The check
    validates such a field whenever it is present, whatever the value of
    `require_passive_ratings`. Each component report records
    `rating_provenance`: `source_backed` (resolved to its receipted record),
    `unverified` (field present but not resolved), `unsourced`, `invalid`, or
    null for non-passives. Unsourced hand-entered ratings still satisfy the property
    rule, as before. With `rating_provenance` declared the field must also
    resolve to its receipted record (see below).
  - `non_native_assembly_marker`: see above.
  - `missing_lcsc`: no `LCSC` value; names the reference.
  - `invalid_lcsc`: value does not match `^C[1-9][0-9]*$`.
  - `unknown_lcsc_in_catalog`: exact ID absent from the snapshot; cites the ID
    and snapshot date.
  - `out_of_stock`: snapshot records stock `0`. An explicit zero is a defect
    even when the observation is stale.
  - `insufficient_stock`: fresh positive stock below the threshold. The
    finding carries `available`, `required`, `boards`, `quantity_per_board`,
    `snapshot_date` and `last_seen`.
  - `extended_parts_limit_exceeded`: unique Extended IDs exceed
    `max_extended_parts`.
  - `operating_temperature_out_of_range`: the declared ambient range is not
    inside the bound part's DigiKey ambient operating range.
- **`unknown`** (never `pass`)
  - `operating_temperature_unavailable`: the declared DigiKey capture is
    unreadable, differs from `capture_sha256` or names uncaptured files.
  - `operating_temperature_unknown`: a declared reference is not exactly one
    populated component, its part number or manufacturer does not match the
    record, the request was not found or failed, or the operating temperature
    is absent, unparsed or junction/case only.
  - `undeclared_rating_provenance`: a populated R or C carries a well-formed
    `AGENT_EE_Ratings` field but the scope declares no `rating_provenance` to
    resolve its `record_sha256`; names the `references`.
  - `rating_provenance_unavailable`: `rating_provenance` is declared but the
    receipt or report is missing, the receipt differs from its pin, is not an
    adoptable `binding-fill` receipt with `--ratings`, the report is not its
    recorded `out/binding-fill.json` artifact, or a report record does not
    match its identity.
  - `missing_stock_telemetry`: stock is `null`.
  - `stale_stock_telemetry`: `last_seen` is missing, is after the evaluation
    date, is earlier than `stale_before`, or is older than
    `max_stock_age_days` / `max_age_hours`. It is also stale when no maximum
    age is declared anywhere (no catalog or parameter `stale_before`, no
    `max_stock_age_days`, no `max_age_hours`).
- **`ok`**: fresh positive stock covering the threshold.

## DigiKey operating temperature

`operating_temperature` (AGENTEE-254) compares a declared ambient range with
the `Operating Temperature` text of a captured
[DigiKey record](digikey-capture.md#check-evidence) bound to each listed
reference. The check replays the capture offline. The pinned manifest and every
response it reads must be captured inputs with unchanged hashes. The component's
`Partnumber`, `PartNumber` and `MPN` values must name exactly the DigiKey MPN,
and a `Manufacturer` value, when present, must match it case-insensitively.

DigiKey text such as `-40°C ~ 85°C (TA)` or `-55°C ~ 155°C` is parsed exactly;
comma-separated ranges keep their `TA`, `TJ` or `TC` qualifiers. Only a single
unqualified or `TA` range is compared, with inclusive endpoints. It passes when
it contains `min_c..max_c` and is otherwise a defect. Junction or case limits
depend on dissipation and stay unknown, as do absent, unparsed or ambiguous
values. Only the listed references are judged. This does not establish derating,
self-heating, storage limits or engineering acceptance.

DigiKey `quantity_available` and price never enter this check's evidence. The
stock predicates still read only the JLCPCB catalog, so a part out of JLCPCB
stock remains `out_of_stock` whatever DigiKey lists. Each judged reference is
reported under `operating_temperature.components` with its verdict, reason and
capture/response provenance.

## Receipt-bound rating provenance

`AGENT_EE_Ratings` carries only a compact `record_sha256`
([binding fill](binding-fill.md#source-derived-passive-ratings)). Without
`rating_provenance` the check still validates its format and its agreement
with the native `Power`/`Voltage` properties, and a disagreement stays an
`invalid_rating_provenance` defect. A well-formed field it cannot resolve is an
unknown `undeclared_rating_provenance` finding, so the outcome is `unknown`,
never `pass` (AGENTEE-242). A design whose passives carry no `AGENT_EE_Ratings`
is unaffected. Pod and campaign scopes that run `binding-fill --ratings` must
therefore declare `rating_provenance` on their `assembly_readiness` check and
capture the adopted `binding-fill` stage `receipt.json` and its
`run/out/binding-fill.json` as candidate inputs, with the coordinator-pinned
`receipt_sha256`. The check does not discover campaign stage receipts itself.
Declaring the provenance binds each field to the full record:

```json
{
  "rating_provenance": {
    "receipt": "provenance/binding-fill.receipt.json",
    "receipt_sha256": "<sha256 of the receipt.json the coordinator adopted>",
    "report": "provenance/binding-fill.json"
  }
}
```

The receipt's own identity is recomputed, and its recorded
`out/binding-fill.json` artifact hash must be the captured report's. For each
populated R or C, the native field's `record_sha256` must equal
`ratings.derived[reference].record_sha256`. Its ratings must equal the record's
values, and the native `LCSC` and every present `MPN`/`Partnumber`/`PartNumber`
must equal the record's part. A component the report derived but that carries
no `AGENT_EE_Ratings` is refused. Any mismatch, a forged 64-hex hash or a
self-consistent forged record included, is an `invalid_rating_provenance`
defect. Unavailable provenance is `unknown`, never `pass`. A determinate defect
elsewhere still gives `defect`. Each component report records `rating_record`:
`receipt_bound`, `unresolved` (malformed, drifted or mismatched),
`unavailable`, `undeclared`, or null. The report
echoes `rating_provenance` with `report_sha256`, `records` and `error`. The
check reads the captured receipt JSON. It does not replay the retained
receipt directory, so the coordinator pin is the authority for that receipt.

A component outside the root sheet resolves first by its hierarchical path,
the native sheet names plus the reference (for example `/A/R101`), and then by
its bare reference. Binding fill gives each declared child-sheet instance such
a key ([declared instances](binding-fill.md#explicit-component-references)),
so every instance of a reused child sheet resolves to its own entry
(AGENTEE-252). An instance the report does not represent still fails closed as
`invalid_rating_provenance` (`names no receipted rating record`).

## Execution and outcome

- Invalid parameters: `execution="unsupported"`.
- Missing or unparseable root or child sheet, a hierarchy cycle or escape,
  kicad-cli failure, a missing or unparseable netlist, a repeated reference
  or component identity, a sheet-count mismatch, a placed symbol occurrence
  missing from the netlist (or a netlist component that is no occurrence of
  the hierarchy), or a missing, corrupt or hash-mismatched catalog:
  `execution="failed"` (`"malformed"` for missing sheet files reported by the
  shared hierarchy walk), `outcome="unknown"`. These never produce an empty
  finding list.
- With `execution="ok"`: any defect gives `outcome="defect"`. Otherwise any
  unknown gives `outcome="unknown"`. Otherwise zero populated components (an
  empty schematic, or only power, DNP or excluded symbols) gives
  `outcome="info"` with `counts.populated == 0`. Otherwise `outcome="pass"`.

## Sources

Semantics ported from MIT-licensed projects; no code or fixtures copied:
tscircuit's parts engine and jlcsearch live-stock policy (unknown stock is
`null`, explicit zero is out of stock, missing or expired observations are
unknown), circuit-synth's BOM property audit (required and any-of property
groups, DNP exclusion; a swallowed load failure there returns a silent pass,
which this check refuses), and Konnect's BOM health check. The tests use
original `clean_boost`-like and `di_box`-like schematics exported by the real
kicad-cli.

The ticket's acceptance criterion says a PartNumber-only policy reports
"4 of 9" components on `clean_boost`. That number comes from circuit-synth's
`TEST_FIXTURES_BOM_AUDIT.md`. The document says "Missing PartNumber: 4" but
lists only three components (U1, R2 and C1). Its own table, and its real
fixture exported through this check, also give 3 of 9. The original
`clean_boost`-like fixture here has the same shape: 9 populated parts, with
C1 lacking any part number and R2 and U1 carrying only `MPN`. It reports
**3 of 9** (C1, R2, U1) under a PartNumber-only policy and only C1 under the
any-of policy. The fixture is not built to reach 4.

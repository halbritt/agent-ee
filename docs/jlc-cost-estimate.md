# JLCPCB cost estimate (informational)

`jlc_cost_estimate` (AGENTEE-77, stage 7 of the
[generator roadmap](generator-roadmap.md)) adds an offline cost estimate and a
Basic/Preferred/Extended breakdown to a sealed [`fab_export`](fab-export.md)
package. It is **not a quote** and never grades a design: the outcome is `info`,
or `unknown` when the board summary is missing. It is never `pass` or `defect`.
[jlc_cost.py](../src/agent_ee/jlc_cost.py) implements it. The check runs in the
coordinator, launches no tool and makes no network request.

## Parameters

| Parameter | Type | Description |
| --- | --- | --- |
| `export_check` | `str` (required) | An earlier successful `fab_export` check in the same scope. |
| `catalog` | `str` (required) | Captured catalog: an `agent-ee.catalog/1` manifest or a JSON snapshot, loaded by the [assembly-readiness](assembly-readiness.md#inputs-and-evidence) catalog loader. |
| `catalog_sha256` | `str \| null` | Optional lowercase SHA-256 pin of the catalog file. |
| `pricing_table` | `str` (required) | Captured `agent-ee.jlc-pricing/1` table (see below). |
| `assembly_tier` | `"economic" \| "standard"` (required) | Assembly service the table entries are selected for. |
| `boards` | `int >= 1` (required) | Boards ordered and assembled. |
| `as_of` | `str \| null` | Evaluation date `YYYY-MM-DD`; default is the catalog `snapshot_date`. |
| `pricing_max_age_days` | `int >= 0 \| null` | Maximum age of a table entry's `observed_on` at `as_of`. |

Any other key, or a missing required key, is `unsupported`. In scope/3 the
check declares **no predicate**, so `can_evaluate_bound` is false. It can be
mapped only to an `informational` requirement; mapping it alone to an
acceptance requirement fails coverage compilation. The pricing table, the
catalog and a SQLite catalog's database are immutable baseline inputs in
scope/2 and scope/3. Launch preflight requires no native tool for it.

## Inputs

All inputs are captured or sealed; nothing is fetched. The estimate consumes a
`fab_export` check, not `fab_package`; because `fab_package` must be declared
last, a scope that wants both the done gate and the estimate declares a
separate `fab_export` check for the estimate to consume.

- **Fab staging**: the earlier export's `fab/manifest.json` is verified against
  its file inventory (`verify_manifest`) and must be bound to the captured board
  digest. `bom.csv` and `cpl.csv` are read with the same strict readers as
  [`cpl_rotation`](cpl-rotation.md); there is no second BOM parser. The BOM and
  CPL designator sets must be equal. The copper layer count comes from the
  manifest's `copper_layers`.
- **Board summary**: the export's `pcb_summary.json` (native
  `kicad-cli pcb export stats`). Width and height (` mm`) give the bounding
  rectangle used as the billing area; the native `area` (` mm²`) is reported
  only. `has_outline` must be true.
- **Board pads**: each BOM designator's footprint in the captured board, read
  with the shared fab-export S-expression scanner. Joints are the pads with a
  non-empty number and type `smd` (SMT joint) or `thru_hole` (THT joint).
  `np_thru_hole`, `connect` and unnumbered pads are not joints. Designators that
  the export excluded by its assembly rule are not in the BOM and so add no
  joints.
- **Vias in SMD pads** (AGENTEE-228): a `thru_hole` pad is excluded from the
  THT joints when it carries exactly one native `(property pad_prop_heatsink)`
  or `(property pad_prop_mechanical)` and its number also names an `smd` pad of
  the same footprint. These are the thermal and stitching vias of the
  [AGENTEE-221 contract](footprint-libraries.md), for example the 12 heatsink
  vias in pad 41 of `ESP32-S3-WROOM-1` or the 2 mechanical vias in pad 2 of an
  edge-mount SMA. The property is read by
  `footprint_library.thermal_via_property`, the same reader the footprint
  index uses for thermal-via declarations. The exclusion is recorded per
  designator in `tht_excluded` (pad, count, property, reason). Every other `thru_hole` pad stays a THT joint, which is the
  conservative choice. It is listed in `tht_unclassified` with a reason when it
  might still be a via: an unmarked hole in a footprint with `smd` pads, or a
  heatsink/mechanical pad with no same-numbered `smd` pad, such as the
  `pad_prop_mechanical` shield anchors `SH` of a USB-C receptacle, which are
  soldered. Other properties, or more than one property, do not match the
  contract.
- **Sides**: the CPL `Layer` column of the BOM designators.
- **Part class**: for each unique LCSC ID in the BOM, the snapshot's class
  flags. `library_tier`, or `is_basic = 1`, decides the class. Otherwise both
  `is_basic` and `is_preferred` must be present. An ID absent from the snapshot,
  or a row with too few flags, is class **`unknown`**; it is never counted as
  Basic or Extended. (`assembly_readiness` keeps its own Extended fallback; its
  loader now also records `class_known`.)

## Pricing table

An owner captures the table as a versioned, dated file. The keys are exact;
unknown keys, duplicate JSON keys, duplicate entry ids and two entries with the
same selector are refused (`malformed`).

```json
{
  "schema": "agent-ee.jlc-pricing/1",
  "table_id": "agent-ee-owned-jlc-pricing",
  "version": "1",
  "currency": "USD",
  "captured_on": "2026-10-17",
  "stale_before": "2026-07-01",
  "provenance": {"source_url": "https://jlcpcb.com/quote",
                 "method": "how the values were captured",
                 "values": "owned-test-values"},
  "entries": [
    {"id": "pcb-2l-base", "kind": "pcb_base", "layers": 2, "max_width_mm": "100",
     "max_height_mm": "100", "included_area_mm2": "50000", "value_usd": "4.00",
     "observed_on": "2026-10-17"}
  ]
}
```

`provenance.values` is `observed` for values read from the fabricator or
`owned-test-values` for test tables. Test values add a limitation. Every money,
size and area value is decimal text, and arithmetic is exact `Decimal`.

| Entry kind | Selector | Values | Use |
| --- | --- | --- | --- |
| `pcb_base` | `layers` | `max_width_mm`, `max_height_mm`, `included_area_mm2`, `value_usd` | One per order when the board fits the box in either orientation |
| `pcb_area_surcharge` | `layers` | `step_area_mm2`, `value_usd` | Per started step of total ordered area (`width × height × boards`) above `included_area_mm2` |
| `assembly_tier` | `tier` | `max_sides` (1 or 2) | Boards with parts on more sides cannot be assembled at this tier |
| `assembly_setup` | `tier` | `value_usd` | Per assembled side |
| `assembly_stencil` | `tier` | `value_usd` | Per assembled side |
| `assembly_joint` | `tier`, `technology` (`smt`, `tht`) | `value_usd` | Per joint, times `boards` |
| `part_loading_fee` | `tier`, `part_class` (`basic`, `preferred`, `extended`) | `value_usd` | Per unique LCSC ID of that class |

Component cost is catalog `price1 × quantity_per_board × boards` per unique
LCSC ID. Quantity price breaks are not modelled.

### Staleness

Each entry carries `observed_on`, which must not be after `captured_on`. Like a
catalog `stale_before`, the table's `stale_before` (null, or not after
`captured_on`) marks entries observed earlier as stale. An entry is also stale
when it is older than `pricing_max_age_days` at `as_of`, or observed after
`as_of`. When neither `stale_before` nor `pricing_max_age_days` is declared,
every entry is stale. Stale entries are still used. The report flags them per
line item (`entry_stale`) and in `pricing_table.stale_entries`, and the summary
counts them. Staleness never changes the outcome, because the estimate grades
nothing.

## Report and outcome

`work/<id>/jlc-cost-estimate.json` (`agent-ee.jlc-cost-estimate/1`) is sealed
evidence with these fields:

- `not_a_quote: true`, `assembly_tier`, `boards`.
- `pricing_table`: path, SHA-256, `table_id`, `version`, `captured_on`,
  `stale_before`, provenance, evaluation date, `cited_entries` and
  `stale_entries`.
- `inputs`: SHA-256 of the board, the export manifest, `bom.csv`, `cpl.csv`
  and `pcb_summary.json` (null when absent), and the catalog and database
  SHA-256 with the snapshot date.
- `parts`: per unique LCSC, the designators, quantity, `class` and
  `class_source`, `price1_usd` and `stock`. `class_counts` gives unique-LCSC
  counts for `basic`, `preferred`, `extended` and `unknown`. A BOM that splits
  one LCSC ID across rows counts it once.
- `selection_objective`: `unique_extended_parts` and
  `unique_unknown_class_parts`, the quantities part selection should minimise.
- `pcb`, `sides` and `joints` (totals, per board and per designator).
  `joints` also gives `tht_excluded_per_board` and
  `tht_unclassified_per_board`. Each `per_designator` entry has `smt`, `tht`,
  `tht_excluded` and `tht_unclassified`. When either count is non-zero, the
  result's limitations say so.
- `line_items`: each has `group` (`pcb`, `assembly` or `components`), `item`,
  `quantity`, `unit_usd` and `amount_usd`. Each also cites its source: the
  table `entry` with `entry_stale`, or the `catalog_row`.
- `unresolved`: gaps that are never filled in, each with `min_usd`, `max_usd`
  (null when unbounded) and the cited entries. Gaps are a missing table entry,
  a board outside every base box, too many sides for the tier, a missing
  summary, an unknown part class (bounded by the lowest and highest class fee)
  and a missing catalog price.
- `totals` per group and overall: `min_usd`, `max_usd` (null when a gap is
  unbounded) and `complete`.

| Condition | Execution / outcome |
| --- | --- |
| Estimate computed (gaps allowed) | `ok` / `info` |
| `pcb_summary.json` not in the export manifest, or unusable (bad JSON, units or no outline) | `ok` / `unknown`; the class breakdown and assembly lines are still reported |
| Invalid parameters, or a missing or wrong-kind prerequisite | `unsupported` / `unknown` |
| Table refused, uncaptured or hash mismatch; staging inventory mismatch; board digest mismatch; BOM/CPL disagreement; designator without a footprint | `malformed` / `unknown` |
| Catalog missing, corrupt or hash-mismatched | `failed` / `unknown` |

## Sources and limits

The mechanism follows atopile's MIT-licensed
`src/atopile/server/domains/cost_estimation.py` (pricing dataclasses, pick-and-place
parsing, BOM part categorisation, solder-joint estimate, PCB and assembly cost)
and `src/faebryk/exporters/bom/json_bom.py` (class flags and unit cost captured at
pick time). No code, constant or text is copied. Konnect's heuristic
`estimate_cost` and AI-PCB-Generator's invented estimates are counter-examples:
here every number cites a captured table entry or catalog row, and missing data
stays unresolved.

The committed table [fixtures/jlc-cost/jlc-pricing-owned.json](../fixtures/jlc-cost/jlc-pricing-owned.json)
holds owned round test values, not JLCPCB prices; see its
[provenance](../fixtures/jlc-cost/PROVENANCE.md). Shipping, tax, panelisation,
surface finish, colour, reel/attrition fees and promotions are outside the table.
[Tests](../tests/test_jlc_cost.py) cover the strict table, freshness, pure
arithmetic against hand-computed totals, and a real kicad-cli netlist and fab
export of the owned fab-export board, including a dropped or unusable summary
and tampered staging.

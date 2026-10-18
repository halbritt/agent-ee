# JLC Assembly-Readiness Check (`assembly_readiness`)

`assembly_readiness` is the orderable-BOM gate for pipeline stages 2 (parts)
and 7 (fab). It checks every populated, on-board, in-BOM component of the
captured KiCad schematic. Each must carry the required sourcing and rating
properties and resolve by exact LCSC ID to a captured offline catalog snapshot.
That snapshot must show fresh stock for the declared board quantity, and the
design must stay within any declared Extended-part budget.

The check does not pick replacements, query live stock or price the board.

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
  (`agent-ee.catalog-snapshot/1` or `agent-ee.jlc-catalog/1`).
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
- **Captured inputs:** `scope.schematic`.
- **Immutable baseline inputs:** `params["catalog"]`, plus `database.path` for
  an `agent-ee.catalog/1` manifest.

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
| `as_of` | `str \| None` | catalog `snapshot_date` | Evaluation date or ISO-8601 timestamp for stock age. |
| `stock_floor` | `int >= 0` | `0` | Minimum stock per LCSC ID: `threshold = max(quantity_per_board * boards, stock_floor)`. |

`property` lookups for `Footprint`, `LCSC` and `Manufacturer` also accept the
common aliases (`footprint`, `LCSC Part`, `jlcpcb_part`, `Mfr`, ...); other
names are exact. Empty and `~` values count as missing.

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
- **`unknown`** (never `pass`)
  - `missing_stock_telemetry`: stock is `null`.
  - `stale_stock_telemetry`: `last_seen` is missing, is after the evaluation
    date, is earlier than `stale_before`, or is older than
    `max_stock_age_days` / `max_age_hours`. It is also stale when no maximum
    age is declared anywhere (no catalog or parameter `stale_before`, no
    `max_stock_age_days`, no `max_age_hours`).
- **`ok`**: fresh positive stock covering the threshold.

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

# JLC Assembly-Readiness Check (`assembly_readiness`)

`assembly_readiness` verifies that every populated, on-board, in-BOM schematic component in a captured KiCad design carries required sourcing and rating properties and resolves to a captured offline LCSC catalog entry with fresh, sufficient stock for the declared board quantity and within any declared Extended-part budget.

## Check Kind and Capability Contract

- **Check kind:** `assembly_readiness`
- **Capability version:** `assembly_readiness/1`
- **Predicates (`agent-ee.scope/3`):**
  - `assembly:bom-properties`
  - `assembly:lcsc-binding`
  - `assembly:stock-coverage`
  - `assembly:passive-ratings` (when `require_passive_ratings` is `true`, the default)
  - `assembly:extended-parts-limit` (when `max_extended_parts` is set)
- **Immutable baseline inputs:** `params["catalog"]` (plus `database.path` when the catalog manifest uses schema `agent-ee.catalog/1`).

## Parameters (`params`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `catalog` | `str` (required) | — | Captured relative path to an `agent-ee.catalog/1` manifest or a standalone JSON catalog snapshot (`agent-ee.catalog-snapshot/1` or `agent-ee.jlc-catalog/1`). |
| `catalog_sha256` | `str \| None` | `None` | Optional 64-character lowercase hex SHA-256 pinning the catalog file. |
| `boards` | `int >= 1` | `1` | Number of boards to assemble; required stock for each unique LCSC ID is `quantity_per_board * boards`. |
| `required_properties` | `list[str \| {"any_of": list[str]}]` | `["Footprint", "LCSC", "Manufacturer", {"any_of": ["Partnumber", "PartNumber", "MPN", "CompanyPN"]}]` | Required BOM properties on every populated symbol. Entries may be a single property name (`"PartNumber"`), a pipe-delimited alias group (`"PartNumber\|MPN\|CompanyPN"`), or `{"any_of": [...]}`. |
| `require_passive_ratings` | `bool` | `true` | Require populated resistors (`R`) to carry a power/voltage rating (`Power`, `PowerRating`, `Rating`, `Voltage`, `VoltageRating`, `PARAM_power`, `PARAM_voltage`) and populated capacitors (`C`) to carry a voltage rating (`Voltage`, `VoltageRating`, `Rating`, `WVDC`, `PARAM_voltage`). |
| `max_extended_parts` | `int >= 0 \| None` | `None` | Optional maximum number of unique `Extended`-tier LCSC parts allowed across populated components. |
| `max_stock_age_days` | `int \| float >= 0 \| None` | `None` | Maximum allowed age (in days relative to `as_of` / `snapshot_date`) for part stock telemetry (`last_seen`). |
| `max_age_hours` | `int \| float >= 0 \| None` | `None` | Maximum allowed age (in hours relative to `as_of` / `snapshot_date`) for part stock telemetry (`last_seen`). |
| `stale_before` | `str \| None` | catalog `stale_before` | ISO date (`YYYY-MM-DD`) before which any `last_seen` timestamp is treated as stale (`unknown`). |
| `as_of` | `str \| None` | catalog `snapshot_date` | Reference ISO date or ISO-8601 timestamp for evaluating stock telemetry age. |
| `stock_floor` | `int >= 0` | `0` | Optional minimum stock floor per unique LCSC ID (`threshold = max(quantity_per_board * boards, stock_floor)`). |

## Component Classification (`DNP` vs `exclude_from_bom` vs Non-Assembly)

Schematic symbols across the root `.kicad_sch` and all child `(sheet ...)` files are parsed via `agent_ee.symbol_library.parse_sexpr` and partitioned into four disjoint buckets:

1. **`ignored_non_assembly`**:
   - Power symbols (`Reference` starting with `#` such as `#PWR01`, `Reference == "PWR_FLAG"`, `lib_id` starting with `power:`, or `lib_id` whose `(lib_symbols ...)` definition contains `(power)`).
   - Off-board / virtual / mechanical symbols with `(on_board no)`.
2. **`excluded_dnp`**:
   - On-board assembly symbols marked `(dnp yes)` (or property `dnp`/`DNP` set to `yes`/`true`/`1`). Excluded from assembly and stock checks.
3. **`excluded_from_bom`**:
   - On-board symbols that are **not** marked `(dnp yes)` (`dnp=False`) but have `(in_bom no)` (or property `exclude_from_bom=yes`).
   - Kept strictly distinct from `excluded_dnp` and reported in `details["excluded_from_bom"]` and `findings` (`code="exclude_from_bom"`, `severity="info"`) so a component silently omitted from the BOM without a DNP mark on the assembly drawing is always surfaced.
4. **`populated`**:
   - On-board (`on_board=True`), in-BOM (`in_bom=True`), non-DNP (`dnp=False`) assembly components evaluated against property, rating, LCSC binding, stock, and tier policies.

## 3-State Stock & Freshness Model

Each unique `LCSC` ID referenced by one or more populated components is aggregated across the design (`quantity_per_board = len(references)`, `required_stock = quantity_per_board * boards`) and evaluated against the captured catalog snapshot:

- **`defect`**:
  - `missing_lcsc`: populated symbol has no `LCSC` property.
  - `invalid_lcsc`: `LCSC` value does not match `^C[1-9][0-9]*$`.
  - `unknown_lcsc_in_catalog`: `LCSC` ID is absent from the captured snapshot.
  - `out_of_stock`: snapshot explicitly records `stock == 0`.
  - `insufficient_stock`: snapshot records fresh positive stock, but `stock < max(quantity_per_board * boards, stock_floor)` (reports required vs available quantity and snapshot date).
  - `extended_parts_limit_exceeded`: `tier_counts["extended_unique"] > max_extended_parts`.
- **`unknown` (never `pass`)**:
  - `missing_stock_telemetry`: snapshot records `stock: null` / `None`.
  - `stale_stock_telemetry`: part `last_seen` / `checked_at` is missing, earlier than `stale_before`, or older than `max_stock_age_days` / `max_age_hours`.
- **`ok`**:
  - Part has fresh positive stock covering `max(quantity_per_board * boards, stock_floor)`.

## Execution and Outcome Semantics

- Corrupt, truncated, or missing schematic/sheet files or catalog snapshots fail closed with `execution="failed"` and `outcome="unknown"`.
- Invalid check parameters fail closed with `execution="unsupported"` and `outcome="unknown"`.
- When `execution="ok"`:
  - Any `defect` finding produces `outcome="defect"`.
  - Otherwise, any `unknown` finding (missing or stale stock telemetry) produces `outcome="unknown"` (never `pass`).
  - Otherwise, if `len(populated) == 0`, produces `outcome="info"` (`"0 populated assembly components; nothing to assemble"`).
  - Otherwise, produces `outcome="pass"`.

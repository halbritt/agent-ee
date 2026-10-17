# Part-Binding Properties and Validator (`part_binding`)

## Purpose

Every placed physical component in a KiCad schematic (`.kicad_sch`) and board layout (`.kicad_pcb`) carries explicit supply-chain and electrical parameter binding properties directly inside the native KiCad S-expression files. The `part_binding` check (`src/agent_ee/part_binding.py`) parses captured `.kicad_sch` and `.kicad_pcb` bytes using the shared S-expression parser (`src/agent_ee/symbol_library.py`), validates property and parameter schemas, and enforces schematic-to-board parity.

---

## Embedded Property Schema

### Required Part-Binding Properties

Every non-virtual, non-exempt placed symbol in `.kicad_sch` and footprint in `.kicad_pcb` must carry:

| Property | Format / Rule | Description |
|---|---|---|
| `LCSC` | `^C[1-9][0-9]*$` (e.g. `C12345`) | LCSC catalog part number |
| `Manufacturer` | Non-empty string | Component manufacturer name |
| `Partnumber` | Non-empty string (`PartNumber` accepted as fallback if `Partnumber` is unset) | Manufacturer part number (MPN) |
| `Datasheet` | String (`""` and `"~"` normalized as empty; required when `require_datasheet=True`) | Datasheet URL or captured path |

### Typed Parameter Properties (`PARAM_<name>`)

Any property whose name matches `^PARAM_[A-Za-z0-9_]+$` (for example `PARAM_resistance`, `PARAM_voltage`, `PARAM_capacitance`, `PARAM_tolerance`) holds a strict JSON object representing either a **closed interval** or a **discrete set** with an explicit physical unit:

1. **Closed Interval Form**:
   ```json
   {"min": "9900", "max": "10100", "nominal": "10000", "unit": "ohm"}
   ```
   - `min` and `max` are required finite numbers or numeric strings parsed with exact `Decimal`.
   - `nominal` is optional; when present, it must satisfy `min <= nominal <= max`.
   - Must satisfy `min <= max`.
   - `unit` is a required non-empty trimmed string.

2. **Discrete Set Form**:
   ```json
   {"values": ["3.3", "5.0"], "unit": "V"}
   ```
   *(The key `"set"` is also accepted as an alias for `"values"`.)*
   - `values` (or `set`) must be a non-empty list of finite numbers or numeric strings.
   - Normalized to sorted unique canonical decimal strings, with `min` and `max` derived automatically from the minimum and maximum elements of the set.

3. **Strict JSON Rules**:
   - Duplicate JSON keys are rejected.
   - `NaN`, `Infinity`, `-Infinity`, booleans, and `null` bounds are rejected.
   - Any violation raises `PartBindingMalformedError`.

---

## Exemptions and Virtual Components

### Virtual Components (Excluded from Evaluation)

- **Schematic (`.kicad_sch`)**:
  - Reference starts with `#` (e.g. `#PWR01`, `#FLG01`).
  - Reference or Value is `PWR_FLAG`.
  - Symbol's library definition in `(lib_symbols ...)` carries `(power)` (including via `(extends ...)`), or `lib_id` starts with `power:`.
  - Symbol has `(on_board no)`.
- **Board (`.kicad_pcb`)**:
  - Footprint `(attr ...)` contains `virtual` or `board_only`.
  - Reference starts with `#` or equals `REF**`.
  - Footprint has `(on_board no)`.

### DNP and Explicit `no-LCSC` Exemptions

A non-virtual component is exempt from requiring a catalog `^C[1-9][0-9]*$` LCSC code and part-binding triples when:
- Marked DNP via `(dnp yes)`, footprint `(attr ... dnp)`, property `dnp`/`DNP` in `("true", "yes", "1")`, or `LCSC` set to `"DNP"`.
- Explicitly marked `no-LCSC` via `LCSC` in `("DNP", "NO_LCSC", "NO-LCSC")` or property `No_LCSC` / `no_lcsc` in `("true", "yes", "1")`.

*(Note: Even when exempt from catalog completeness, a component present in both schematic and board must still agree across schematic and board on its properties.)*

---

## Schematic / Board Parity Rules

When both `sch_bytes` and `pcb_bytes` are supplied to `validate_part_bindings`:
1. **Presence Parity**:
   - Every non-virtual schematic symbol with `on_board=True` and `dnp=False` must have a corresponding footprint on the board.
   - Every non-virtual, non-`board_only` footprint on the board must have a corresponding non-virtual `on_board` symbol in the schematic.
2. **Property & Parameter Parity**:
   - For every reference present in both schematic and board, `LCSC`, `Manufacturer`, `Partnumber`, `Datasheet` (with `"~"` and `""` normalized as empty), `Footprint` (schematic `Footprint` property vs board footprint `lib_id`), `Value`, `dnp`, `in_bom`, and normalized `PARAM_*` dictionaries must match.
3. **Internal Multi-Unit Consistency**:
   - Multi-unit symbols sharing a reference in `.kicad_sch` are merged; any conflict in binding properties, flags, or `PARAM_*` values across units of the same reference is recorded in `disagreements`.

---

## Execution and Outcome Semantics

| Condition | `execution` | `outcome` | Reason |
|---|---|---|---|
| Unparseable S-expression, wrong root tag, invalid `PARAM_*` JSON, duplicate keys, missing `unit`, or `min > max` | `malformed` | `unknown` | Structural or parameter literal syntax failure |
| Non-empty `disagreements` (schematic/board mismatch, missing parity, multi-unit conflict) or `invalid_bindings` (e.g. `LCSC` = `"12345"` or `"C0"`) | `ok` | `defect` | Binding violation or schematic/board contradiction |
| Non-empty `missing_bindings` (missing `LCSC`, `Manufacturer`, or `Partnumber` on a non-virtual, non-exempt component) and no defects | `ok` | `unknown` | Incomplete binding coverage is never reported as `pass` |
| Zero non-virtual components evaluated and no defects/missing bindings | `ok` | `info` | Nothing eligible to qualify |
| At least one non-virtual component evaluated with zero disagreements, invalid bindings, or missing bindings | `ok` | `pass` | All evaluated bindings are valid and consistent |

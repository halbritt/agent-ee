# Part-Binding Properties and Validator (`part_binding`)

## Purpose

Every placed physical component in a KiCad schematic (`.kicad_sch`) and board layout (`.kicad_pcb`) carries explicit supply-chain and electrical parameter binding properties directly inside the native KiCad S-expression files. The `part_binding` check (`src/agent_ee/part_binding.py`) parses captured `.kicad_sch` and `.kicad_pcb` bytes using the shared S-expression parser (`src/agent_ee/symbol_library.py`), validates property and parameter schemas, and enforces schematic-to-board parity. It defines and checks the record; it does not pick parts.

## Check parameters

```json
{"id": "bindings", "kind": "part_binding",
 "params": {"require_datasheet": true, "require_params": true}}
```

| Parameter | Default | Meaning |
|---|---|---|
| `schematic` | `scope.schematic` | Captured root `.kicad_sch` path |
| `board` | `scope.board` | Captured `.kicad_pcb` path |
| `require_datasheet` | `false` | Also require a non-empty `Datasheet` |
| `require_params` | `false` | Also require at least one `PARAM_*` property |
| `calibration_inputs` | `[]` | Optional captured paths recorded as immutable baseline inputs |

At least one of `schematic` and `board` must resolve to a captured input; any
other parameter is refused. The schematic and board stay mutable candidate
inputs. Scope/3 exposes the predicates `part-binding:schema/1` and
`part-binding:parity/1`. The check runs in the coordinator with no tool and no
network, writes `work/<check id>/part_binding.json` and records the SHA-256 of
the root schematic, every visited child sheet and the board under `inputs`.

## Schematic sheet hierarchy

The root schematic's `(sheet ...)` children are followed through their
`Sheetfile` property, resolved against the parent sheet's directory. A child
sheet must be a captured `.kicad_sch` input; an uncaptured sheet, a path that
leaves the captured inputs, a missing sheet UUID or a sheet cycle is
`malformed`, because coverage would be incomplete. Each sheet occurrence takes
its references from the symbol's `(instances ...)` entry whose path equals the
occurrence path (`/<root uuid>/<sheet uuid>...`), so a sheet file used twice
yields one binding per occurrence (for example `R10` and `R20`). In a
hierarchical design, a symbol with no entry for its occurrence is malformed; a
flat schematic falls back to the first annotated entry, then to the
`Reference` field. The test suite cross-checks these references against the
`kicad-cli sch export netlist` component list.

---

## Embedded Property Schema

### Required Part-Binding Properties

Every non-virtual placed symbol in `.kicad_sch` and footprint in `.kicad_pcb` must carry the following. A [DNP or no-LCSC marker](#dnp-and-explicit-no-lcsc-markers) replaces `LCSC` only.

| Property | Format / Rule | Description |
|---|---|---|
| `LCSC` | `^C[1-9][0-9]*$` (e.g. `C12345`); any other non-marker value is an invalid binding (`defect`) | LCSC catalog part number |
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
- **Board (`.kicad_pcb`)**: these markers exclude a footprint only when it has
  no copper pads:
  - Footprint `(attr ...)` contains `virtual` or `board_only`.
  - Reference starts with `#` or equals `REF**`.
  - Footprint has `(on_board no)`.

A pad declaring a layer ending in `.Cu` (including `*.Cu` and inner layers)
makes the footprint bindable regardless of those markers, except for qualified
bare NPTH holes described below. Binding validation and schematic/board parity
still apply. Marked padless mechanical footprints
and those with only non-copper pads retain their virtual exemption.
The board component record retains `attrs`, `board_only`, `reference` and
`on_board`, and adds `has_copper_pads`; its `virtual` field records the effective
exemption. A bare, unnumbered NPTH hole is also non-copper when the existing
`land_pattern` native qualifier proves that its pad and drill shapes and sizes
coincide with no offset, even if it declares `*.Cu`. An NPTH with an annulus or
unqualified geometry keeps its copper classification and requires bindings.
Malformed NPTH geometry encountered during that qualification is `malformed`.
Missing, empty, repeated or non-atom pad layer lists are also `malformed`, so
ambiguous coverage cannot qualify a footprint as virtual.

### DNP and Explicit `no-LCSC` Markers

A DNP or no-LCSC marker replaces the `LCSC` code only. `Manufacturer` and
`Partnumber` stay required on every non-virtual component. The markers are:
- DNP: `(dnp yes)`, footprint `(attr ... dnp)`, property `dnp`/`DNP` in `("true", "yes", "1")`, or `LCSC` set to `"DNP"`.
- no-LCSC: `LCSC` in `("DNP", "NO_LCSC", "NO-LCSC")` or property `No_LCSC` / `no_lcsc` in `("true", "yes", "1")`.

A marked component is listed in `exempt_references`. The optional
`require_datasheet` and `require_params` requirements do not apply to it. It
must still agree across schematic and board.

### References

An unannotated reference ending in `?` on a non-virtual component is recorded
as a missing `Reference` binding (`unknown`). Two symbols with the same
reference and unit, and two board footprints with the same reference, are
disagreements.

---

## Schematic / Board Parity Rules

When both `sch_bytes` and `pcb_bytes` are supplied to `validate_part_bindings`:
1. **Presence Parity**:
   - Every non-virtual schematic symbol with `on_board=True` and `dnp=False` must have a corresponding footprint on the board.
   - Every non-virtual footprint on the board must have a corresponding non-virtual `on_board` symbol in the schematic.
2. **Property & Parameter Parity**:
   - For every reference present in both schematic and board, `LCSC`, `Manufacturer`, `Partnumber`, `Datasheet` (with `"~"` and `""` normalized as empty), `Footprint` (schematic `Footprint` property vs board footprint `lib_id`), `Value`, effective `dnp`, native DNP (`(dnp yes)` or footprint `dnp` attribute; an authored `DNP` property is not native), `in_bom`, and normalized `PARAM_*` dictionaries must match.
   - Two different non-empty values are a disagreement (`defect`). A binding property (`LCSC`, `Manufacturer`, `Partnumber`, `Datasheet` or a `PARAM_*`) present on one side only is a missing binding on the other side (`unknown`), never a pass. A footprint that lacks the properties is therefore `unknown` for that reference.
3. **Internal Multi-Unit Consistency**:
   - Multi-unit symbols sharing a reference in `.kicad_sch` are merged; any conflict in binding properties, flags, or `PARAM_*` values across units of the same reference is recorded in `disagreements`.

---

## Execution and Outcome Semantics

| Condition | `execution` | `outcome` | Reason |
|---|---|---|---|
| Unparseable S-expression, wrong root tag, invalid `PARAM_*` JSON, duplicate keys, missing `unit`, or `min > max` | `malformed` | `unknown` | Structural or parameter literal syntax failure |
| Uncaptured child sheet, unsafe `Sheetfile`, sheet cycle or unmatched occurrence | `malformed` | `unknown` | Hierarchy coverage is incomplete |
| Non-empty `disagreements` (schematic/board mismatch, missing parity, multi-unit conflict) or `invalid_bindings` (e.g. `LCSC` = `"12345"` or `"C0"`) | `ok` | `defect` | Binding violation or schematic/board contradiction |
| Non-empty `missing_bindings` (missing `LCSC` without a marker, `Manufacturer` or `Partnumber` on a non-virtual component, a one-sided binding property, or an unannotated reference) and no defects | `ok` | `unknown` | Incomplete binding coverage is never reported as `pass` |
| Zero non-virtual components evaluated and no defects/missing bindings | `ok` | `info` | Nothing eligible to qualify |
| At least one non-virtual component evaluated with zero disagreements, invalid bindings, or missing bindings | `ok` | `pass` | All evaluated bindings are valid and consistent |

`reference_outcomes` maps each evaluated reference to `pass`, `unknown` or
`defect`. A reference with any invalid binding or disagreement is `defect`;
otherwise one with any missing binding is `unknown`; otherwise it is `pass`.
The run outcome follows the table above, so a defect elsewhere never hides
an `unknown` reference.

## Related checks

[`assembly_readiness`](assembly-readiness.md) and
[`package_consistency`](package-consistency.md) read the same `LCSC` field with
the same `^C[1-9][0-9]*$` rule but answer different questions.
`part_binding` asks whether the binding record is complete and consistent, so a
missing value is `unknown` and an explicit no-LCSC marker is accepted.
`assembly_readiness` asks whether JLC can assemble the populated parts, so a
missing or non-catalog LCSC value there is a defect. `package_consistency`
treats an authored `DNP` field as data and excludes only native flags. A
no-LCSC part can therefore pass `part_binding` and fail `assembly_readiness`.

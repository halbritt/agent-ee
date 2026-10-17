# Footprint and catalog package consistency

`package_consistency` compares each schematic component's assigned KiCad
footprint with the package of its LCSC part in a pinned
[catalog snapshot](catalog.md). The comparison is name-level and goes only
through a captured, hashed mapping table. A name the table does not list is
unknown, never guessed. Land-pattern geometry, pitch and exposed pads are not
compared; LCSC package strings do not carry them.

```json
{
  "id": "packages",
  "kind": "package_consistency",
  "params": {
    "netlist_check": "netlist",
    "catalog": "catalog",
    "catalog_manifest_sha256": "<SHA-256 of catalog/manifest.json>",
    "mapping": "package-map.json",
    "lcsc_field": "LCSC",
    "class_mismatch": "info"
  }
}
```

All six parameters are required and no others are accepted:

- `netlist_check` names an earlier `netlist` check. Its `design.xml` is read by
  the shared strict native XML reader, which supplies the reference, sheet/UUID,
  footprint, component fields and native `dnp`, `exclude_from_board` and
  `exclude_from_bom` flags. Its `connectivity.json` must equal the projection of
  those bytes. Duplicate or valued native flags, duplicate fields or properties,
  a contradicted footprint field and duplicate identities are malformed.
- `catalog` is the captured snapshot directory holding `manifest.json` and
  `catalog.sqlite3`. `catalog_manifest_sha256` must equal the captured manifest
  hash. The snapshot is read by `catalog.load_catalog`, which re-derives every row
  from its raw claims, inside the networkless check sandbox.
- `mapping` is the captured `agent-ee.package-map/1` table described below.
- `lcsc_field` is the exact native field name holding the LCSC part number.
  Its value must be exactly `C` and a number without leading zeros. No other field is consulted.
- `class_mismatch` is `defect` or `info`. It sets how a footprint class that
  differs from the catalog category is reported when the package names agree.

The check must map to exactly one baseline requirement. The catalog files and
the mapping table become immutable baseline dependencies in scope/2 and scope/3,
so changing any of them changes the baseline identity. The schematic stays a
mutable candidate input. Scope/3 exposes the predicate
`package-consistency:footprint-package`, plus
`package-consistency:component-class` when `class_mismatch` is `defect`.

## Per-reference outcome

Each native component gets one record naming its reference, sheet, UUID,
footprint, LCSC value, native flags, the rule that fired on each side and a
locator. Repeated sheet instances are separate records.

| Status | Condition |
| --- | --- |
| `excluded` | Native `dnp`, `exclude_from_board` or `exclude_from_bom` flag. An authored field such as `DNP=yes` is data and excludes nothing. |
| `unknown` | No or malformed LCSC value, empty footprint, part absent from the snapshot, or footprint or package name outside the table. |
| `mismatch` | Both names are mapped and family, pin count or body differ. Pin counts are never crossed. |
| `class_mismatch` | Same package, but the footprint's class list (for example `resistor` for `Resistor_SMD:R_0603_1608Metric`) does not contain the catalog category. Severity follows `class_mismatch`. |
| `match` | Same package descriptor and, for chip footprints, the catalog category is a footprint class. |

The aggregate is `unknown` with execution `unsupported` if any non-excluded
record is unknown or no component is eligible. A proven mismatch elsewhere does
not turn missing coverage into a defect. Otherwise it is `defect` when any
mismatch, or a class mismatch under the `defect` policy, exists, and `pass`
otherwise. Class mismatches under the `info` policy are listed in `advisories`.
A missing, failed or changed netlist prerequisite, a malformed native XML
inventory, or a catalog that fails loading or re-derivation gives execution
`failed` (or `malformed` for an inconsistent lookup) and outcome `unknown`.

`work/<id>/packages.json` records every record, the table path, SHA-256 and
version, the catalog manifest and database hashes and snapshot date, and the
native XML, connectivity and catalog-lookup hashes. `request.json` lists the
LCSC IDs sent to the sandboxed catalog reader and `catalog-rows.json` its
answer. The ordinary coordinator seal covers these and the netlist artifacts.

## Mapping table

The committed table is
[`references/package-map/kicad-10.0.7-lcsc-1.json`](../references/package-map/kicad-10.0.7-lcsc-1.json),
produced by [`generate.py`](../references/package-map/generate.py) from the file
names in the pinned KiCad 10.0.7 footprint library. It has exactly `schema`,
`version`, `derivation`, `rules`, `footprints` and `packages`. Every footprint
entry is an exact `library:name` ID with a rule, a class list (catalog
categories, or `null` for class-free outlines) and a `{family, pins, body}`
package descriptor; every package entry is an exact LCSC string with a rule and
descriptor. The rules are:

| Rule | Footprints | Package names |
| --- | --- | --- |
| `chip-imperial` | `R_`, `C_`, `L_`, `LED_` and `D_` chips whose imperial and metric codes agree, including hand-solder pads; the library fixes the class | `0201` to `2512` imperial codes |
| `named-sot23` | `SOT-23`, `SOT-23-3`, `SOT-23-5`, `SOT-23-6` and hand-solder variants; not `SOT-23W` | `SOT-23`, `SOT-23-3`, `SOT-23-3L`, `TO-236-3(SOT-23-3)`, `SOT-23-5`, `SOT-23-5L`, `SOT-25-5`, `SOT-23-6`, `SOT-23-6L` |
| `gull-wing` | SOIC (JEDEC MS-012, 3.9 mm, 1.27 mm) and TSSOP (MO-153, 4.4 mm, 0.65 mm) with the standard body length | `SOIC-8/14/16` and `TSSOP-8` to `TSSOP-28` |
| `body-size` | `QFN-N-1EP_WxL` and `LQFP-N_WxL` | `QFN-`, `VQFN-`, `UFQFPN-N(WxL)` and `LQFP-N(WxL)` for outlines in the footprint table |

Names whose body is not fixed are absent on purpose, so they stay unknown: bare
`SOP-8`, other pitches of a SOIC/TSSOP name, `TQFP`, and any QFN lead count and
body that the library offers in more than one pitch. The table was derived
independently from library names and public standards; it is not a copy of
another project's alias list. `derivation.observations` records LCSC strings
read from public product pages on 2026-10-17 (`C1525` `0402`, `C14663` `0603`,
`C9055` `SOT-23-5`, `C51118` `SOT-25-5`, `C20917` `SOT-23`, `C7593` `SOIC-8`,
`C1340704` `UFQFPN-48(7x7)`). The table has not been measured against a full
catalog feed: a real string the table lacks is unknown, and a feed row whose
string misstates its body would be compared as stated. Regenerate with
`python3 -I references/package-map/generate.py /usr/share/kicad/footprints <out>`
and give a changed table a new version.

## Evidence and limits

The [original fixture generator](../fixtures/package-consistency/generate.py)
creates a root sheet and a child sheet instantiated twice, with native DNP and
BOM-excluded components, an authored `DNP` field, and synthetic catalog rows.
Tests run the actual networkless native exporter for the ticket examples
(`Capacitor_SMD:C_0402_1005Metric` with `C1525`, QFN-48 7x7 with
`UFQFPN-48(7x7)`, a SOT-23 footprint on a SOT-23-5 part, and
`Resistor_SMD:R_0603_1608Metric` on a 0603 capacitor under both policies), the
unknown cases, mapping and baseline rebinding, requirement isolation, missing
prerequisites, invalid tables, duplicate native properties, sealing and
tampering, and a re-pinned snapshot whose stored intervals contradict its raw
claims.

Catalog/1 categories are `resistor`, `capacitor`, `inductor`, `diode`, `led`,
`connector` and `regulator`. A microcontroller such as the real `C1340704` cannot be a catalog
row, so the QFN-48 example uses a synthetic regulator row in that package. All
stock, prices and attributes in the fixture are test values. Synthetic examples
do not qualify a full catalog feed. The check does not compare the board
footprint (DRC schematic parity covers that), the symbol class, land-pattern
geometry or whether the part suits the circuit.

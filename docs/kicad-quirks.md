# KiCad report-format quirk guards

Two kicad-cli 10.0.x output faults would otherwise give wrong evidence while the
command still exits 0. `agent_ee.kicad_quirks` guards both (AGENTEE-66). The
guards are keyed on the sandboxed kicad-cli identity and run offline. They add no
check kind and no runtime dependency.

## ERC JSON coordinate scale

KiCad 10.0.6 and earlier 10.0.x releases write every ERC JSON item `pos` at 1/100
of its true value, whatever `--units` says (KiCad issue 25582). The text report
(`--format report`) is correct. 10.0.7 writes true values.

Before ERC runs, the `erc` check runs `kicad-cli version` in the sandbox as its
own sealed execution (`work/<id>/kicad-version/`). The version line selects the
regime:

| `kicad-cli version` | `regime` | Locations |
| --- | --- | --- |
| `10.0.0` to `10.0.6` | `scale_x100` | `pos` × 100, rounded to the 0.0001 mm schematic unit |
| `10.0.7` and later `10.0.N` | `native` | `pos` as written |
| Anything else | `withheld` | No positions; `reason` says why |

"Anything else" covers other series, release candidates, branch and nightly
builds (`10.0.6-45-g…`) and a version run that fails or prints more than one line.
Positions are also withheld when the report's own `kicad_version` differs from
the sealed version line. They are withheld too when `coordinate_units` is not `mm`
or an item lacks a finite numeric position. Withheld coordinates are information
only. Violation counts and the `pass`/`defect` outcome do not depend on them,
and a limitation names the reason. Coordinates are never trusted without a
classified version.

The check result records the regime alongside the counts:

- `details.kicad_cli`: `{path, sha256, version}` of the pinned sandbox binary.
- `details.erc_coordinates`: `version`, `regime`, `scale`, `status` (`trusted` or
  `withheld`), `report_kicad_version`, `coordinate_units`, and `reason` when
  withheld.
- `details.locations`: one entry per violation with `sheet`, `type`, `severity`
  and `items` (`uuid`, `description`, plus `pos_mm` only when trusted).

Under `scale_x100`, item descriptions stay verbatim. Lengths quoted inside them
(`length 0.2540 mm`) are at the same 1/100 scale, so
`erc_coordinates.limitation` and a result limitation say so. `agent-ee doctor`
adds the same regime record for the host kicad-cli as
`prerequisites.kicad-cli.erc_json_coordinates`.

Fixtures and the 10.0.6 build used for capture are recorded in
[fixtures/kicad-quirks/README.md](../fixtures/kicad-quirks/README.md). On
both captures the corrected positions match the text report for all 12 items.
The pinned image ships 10.0.7, so the native `erc` test exercises `native`. The
`scale_x100` path is tested against the captured 10.0.6 JSON and report.
Releases 10.0.0 to 10.0.4 fall under `scale_x100` on the issue's evidence, but no
build was captured. Launchpad no longer serves them.

## BOM variant names

`kicad-cli sch export bom --variant NAME` exits 0 and writes the default variant
when `NAME` is undefined. KiCad registers variants in the project file as
`schematic.variants[].name`. Symbol and sheet instances carry
`(variant (name "…") …)` clauses. A clause holding `exclude_from_bom` stops
kicad-cli loading the schematic; the native token is `in_bom`.

`validate_bom_variants(project, schematics, names)` returns a record or raises
`VariantError` before any export runs. It refuses:

- a name absent from the `.kicad_pro` registry, including one used only in
  schematic clauses or differing in case;
- a missing, non-JSON or malformed project file, or a registry entry with an
  empty or duplicate name;
- an empty, duplicated or non-string request;
- a variant clause with `exclude_from_bom`, an unrecognised token or no single
  name, and an unreadable schematic.

The generator's stage 7 is the only agent-ee path that runs
`kicad-cli sch export bom`. It applies the guard when given `--bom-variant NAME`
([generation driver](generate.md)). A refusal fails the stage before its first
kicad-cli command and records no commands. An accepted name is passed as
`--variant NAME` to the BOM export only. The receipt records the validation and
the project file as an input. CPL stays the default variant, so the existing
BOM/CPL designator agreement fails a variant that changes the populated set. The
`fab_export` check takes no variant parameter and refuses any schematic or board
that declares variants ([fab export](fab-export.md)).

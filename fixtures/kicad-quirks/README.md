# KiCad report-format quirk fixtures (AGENTEE-66)

Hand-authored for agent-ee. Nothing here is copied from Konnect (AGPL-3.0-only)
or any other project; the behaviour was re-derived from KiCad issue 25582, the
KiCad 10.0.7 source as a statement of file-format facts, and the captures below.
See [KiCad report-format quirk guards](../../docs/kicad-quirks.md).

## `erc-scale/`

`erc_scale.kicad_sch` holds labels and wires only (no symbols). Native ERC reports
11 violations with 12 located items: dangling labels, a dangling wire with its
endpoints, and two labels on one wire. `../erc-scale.scope.json` captures it for
the real `erc` check.

The four reports were written by the released kicad-cli builds over that file:

```sh
kicad-cli sch erc --format json   --severity-all --units mm -o erc-10.0.7.json erc_scale.kicad_sch
kicad-cli sch erc --format report --severity-all --units mm -o erc-10.0.7.rpt  erc_scale.kicad_sch
```

- 10.0.7: the pinned host package `10.0.7~ubuntu24.04.1`.
- 10.0.6: `kicad_10.0.6~ubuntu24.04.1_amd64.deb` from the KiCad 10.0 releases PPA
  (SHA-256 `a4920d3fc7b5719b8d5efa4ee24603a4313b50a53409bb5417f50770ce73926d`),
  unpacked with `dpkg-deb -x` into a scratch directory and run with that
  directory's `usr/lib/x86_64-linux-gnu` first on `LD_LIBRARY_PATH`. It printed
  `kicad-cli version` = `10.0.6`. It was not installed, retained or added to the image.

The two `.rpt` files are identical apart from their timestamp line and are the
location oracle. `erc-10.0.6.json` writes every position at 1/100 of the report
value (127.00 mm reads 1.27). It also writes lengths inside item descriptions
(`length 0.2540 mm`) at that scale. `erc-10.0.7.json` matches the report.

## `variants/`

`var.kicad_sch` has two synthetic resistors; R1 is DNP in variant `LITE` through
the native `(variant (name "LITE") (dnp yes))` instance clause. `var.kicad_pro`
registers `LITE` under `schematic.variants`. With kicad-cli 10.0.7 and
`--exclude-dnp`, `--variant LITE` drops R1. `--variant NOPE` exits 0 and writes
the default BOM. Replacing the clause's `dnp` with `exclude_from_bom` makes
kicad-cli fail to load the schematic (exit 3).

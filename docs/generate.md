# Rung-1 generation driver (experimental)

`agent_ee.generate` is a **private experimental entry point** that takes the
one-paragraph rung-1 spec ([fixtures/rung1/spec.md](../fixtures/rung1/spec.md))
to a JLCPCB fabrication package. It is not a second pipeline. It runs the
existing pieces in order and hands verification to the existing
[campaign](campaign-guide.md): design writer steps first, then the scope/2
check set and independent review, then export. Every record it writes carries
`"qualification": "experimental-unqualified"` and an experimental schema name.

At integration, owned implementations supersede the stand-ins this driver
calls or defines:

| Driver piece | Superseded by |
| --- | --- |
| Stage receipts (`agent-ee.rung1-stage-receipt/0-experimental`) | `impl/stage-receipts` (AGENTEE-95) |
| Schematic emitter ([schematic-emit.md](schematic-emit.md)) | `impl/native-emitter` (AGENTEE-40, AGENTEE-52) |
| Board envelope (`agent-ee.rung1-envelope/0-experimental`) | `impl/envelope-boundary-repair` (AGENTEE-85) |
| Board build and row placement ([board-build.md](board-build.md)) | placement writer and placer adapter (AGENTEE-91, AGENTEE-92); stock pad shapes AGENTEE-109 |
| Routing call to `kicad-route` | `impl/external-routing-adapter` (AGENTEE-93); sandboxed state KICADROUTE-6 |
| Fab export | `fab_export` then `cpl_rotation`, through the shared receipted fab step (AGENTEE-23, AGENTEE-72, AGENTEE-95, AGENTEE-126) |
| Part selection from a design BOM | catalog snapshot and selector (AGENTEE-27, AGENTEE-28) |

## Command

```sh
PYTHONPATH=src /usr/bin/python3 -m agent_ee.generate fixtures/rung1/spec.md \
  --out build/rung1-run --profile profiles/claude-baseline.json \
  --cpl-inputs /path/to/your-cpl-inputs
PYTHONPATH=src /usr/bin/python3 -m agent_ee.generate fixtures/rung1/spec.md \
  --out build/rung1-skip --skip-review --cpl-inputs /path/to/your-cpl-inputs
```

`--out` must not exist. Without `--skip-review`, `--profile` is required and
stage 6 runs `agent-ee campaign`, which spends subscription inference.
`--max-seconds` sets the campaign work budget (default 2400). Exit status is 0
only when the record says `done: true`, 1 otherwise, and 2 when the invocation
is refused. `--stand-in-fixture` uses `fixtures/rc/control` when the schematic
emitter or board builder is missing; those stages are then recorded as
`not_reached:dependency_missing`, so such a run can never be done.
`--bom-variant NAME` exports the stage 7 BOM for a design variant. `NAME` must be
registered in the reviewed project's `.kicad_pro`; otherwise stage 7 fails before
any kicad-cli command, because kicad-cli would silently export the default
([quirk guards](kicad-quirks.md#bom-variant-names)). An accepted name is passed
to the receipted `export-fab` step (`--bom-variant NAME` in its argv) and from
there to the `fab_export` check's `bom_variant` parameter, which guards it again
and gives it only to `kicad-cli sch export bom --variant NAME`
([fab export](fab-export.md#bom-variant)). The fab manifest and the stage
receipt (`details.bom_variant`, `details.bom_variant_export` and the native BOM
command) record it. CPL stays the default variant: a variant that changes the
populated set fails stage 7 by name. Stage 7 still requires `--cpl-inputs`.

## Stages

Each stage writes `RUNDIR/stages/N-NAME/receipt.json` with the stage, status,
hashed inputs and outputs, tool identities (path, binary sha256, version),
started and finished timestamps, and the exact argv of every command with its
exit code and logs. A failed stage records its error verbatim. A later stage
never starts after an earlier failure; it is recorded as `not_reached`.

| Stage | What it does | Fails when |
| --- | --- | --- |
| 1 requirements | Extracts the RC parameters from the spec with fixed patterns (each must match exactly once), imports an intent record with `design_intent.import_record`, captures an empty-question dialog with `design_dialog.capture_dialog`, writes `requirements/{spec,system,targets,reference}.md` and compiles `scope/rung1.scope.json` from `fixtures/closed-loop/control.scope.json` | A parameter is missing or ambiguous, the topology is not the RC template, or the lint/dialog does not pass |
| 2 parts | Reads the owner's fome-mc BOM `export/BOM-fome-mc.csv` as committed at `~/git/fome-mc` HEAD (working-tree edits by other sessions are recorded, not read), copies it into the run, writes `parts-manifest.json` and captures each part's Datasheet into `datasheets/` | The BOM header is not one of the two declared schemas (`Comment,Designator,Footprint,LCSC Part #,Quantity` or `Comment,Designator,Footprint,LCSC`), a library-less footprint has no entry in the captured footprint map, no exact-value row of the symbol's chip family has an LCSC number, a footprint is missing from the stock library, or pads differ from pins |
| 3 schematic | Writes the connectivity intent over captured `Device`, `power` and `Connector_Generic` libraries, resolves it with `connectivity_ir`, runs `agent_ee.schematic_emit` in the sandbox, then native ERC and XML netlist export | The intent does not resolve, the emitter refuses, ERC reports any violation, or the netlist nets or components differ from the intent |
| 4 placement | Writes the envelope (50 x 30 mm outline at 100,100 mm; J1 fixed 2.54 mm inside the left edge) and packing policy, then runs `agent_ee.board_build` in the sandbox | `board_build` exits non-zero (its exit meanings are kept in the error) |
| 5 routing | Runs `kicad-route --out stages/5-routing/out run --backend builtin --budget 300` in a copy of the placed project and copies the candidate to `RUNDIR/design/` | kicad-route exits non-zero (0 is complete and valid) |
| 6 review | Runs `agent-ee campaign` on the compiled scope (review profile from `--profile`, implementation = the adjudicator's harness/model, 3 iterations), then `verify-campaign` | The campaign state is not `criteria_satisfied`, or verification fails |
| 7 fab | Runs native netlist, geometry, `fab_export` (with `bom_variant` when `--bom-variant` is given) and `cpl_rotation` through an AGENTEE-95 stage receipt; derives a corrected package and hashes its files and correction report | Policy or supplier capture is missing, an export/check fails, CPL coverage is incomplete, or a `--bom-variant` is unregistered or changes the populated set; successful correction retains `PREVIEW_REQUIRED` |

With `--skip-review`, stage 6 runs `agent-ee review --checks-only` instead.
Its status is `not_reached:review_skipped` when every declared check executed
and passed, and `failed` otherwise. Stage 7 still runs, but the record cannot
be done: there is no review verdict.

## Parts rule

`read_catalog` accepts only the two declared BOM header schemas, selected by
exact header match and recorded in `details.catalog` (`header_schema` and
`columns`) of the stage-2 receipt: the 5-column pre-`11f03c8` header
`Comment,Designator,Footprint,LCSC Part #,Quantity` (`five_column`) and the
4-column `11f03c8` header `Comment,Designator,Footprint,LCSC` (`four_column`).
Any other header is refused naming the header. For the 4-column form,
`Quantity` is derived from the comma-separated designator count in `Designator`
and library-less footprint names (`R_0402_1005Metric`) are resolved only through
the captured footprint map; an unmapped library-less footprint is refused,
never guessed.

Selection is deterministic: the catalog `Comment` must equal the intent value
exactly, the footprint must be a two-terminal chip of the symbol's family
(`Resistor_SMD:R_*Metric` or `Capacitor_SMD:C_*Metric`) and the row must have
an LCSC number. Candidates rank by distance from the fixture's 0805 package,
then larger package, then catalog quantity (parts fome-mc uses most), then LCSC
number. The manifest keeps the chosen row, every rejected row with its reason
and rank key, and the footprint file hash. MPN and manufacturer are absent from
the BOM, so they are omitted, never invented. Every part also has an explicit
`Datasheet` field: the raw catalog text when the catalog has a datasheet
column, otherwise `null` with `datasheet_obligation:
"missing:catalog_has_no_datasheet_column"`, so the obligation is never dropped.
Stage 2 is also the capture phase: it passes every part's `Datasheet` to
[`capture_datasheets`](datasheet-capture.md) and writes
`stages/2-parts/datasheets/` (PDFs named by URL hash plus `manifest.json` with
URL, fetch time and sha256 per part). The receipt lists the captured and missing
parts; a missing datasheet is a stage limitation, not a stage failure, and later
`datasheet_facts`/`pin_map` coverage for that part is unknown. Only this stage
may touch the network, and only when a URL exists; the fome-mc BOM has no
datasheet column, so the current run fetches nothing. Stage 3 copies a non-null
`Datasheet` onto the emitted symbol.
Each selection has a `selection_id`, the sha256 of the canonical JSON of its
catalog sha256, catalog line, symbol, value and reference.

A part the spec names hand-soldered (the header) gets
`JLCPCB_Assembly: "not_assembled"` with its reason, and is refused if the
catalog has a row for its value, so the rule cannot hide a sourced part. Stage 7
now uses the owned `fab_export` rules. The non-DNP header has no LCSC binding,
so that exporter refuses it. It is not silently omitted or changed to DNP.

## Record and done gate

`RUNDIR/rung1-record.json` (`agent-ee.rung1-record/0-experimental`) lists every
stage with its status, error and receipt hash, the campaign verdict, the fab
manifest hash and the placer used. `done` is true only when the campaign
verdict is `criteria_satisfied` **and** the fab exports exist, are nonempty and
match their manifest hashes **and** the fab status is in the allow-list
`fab_package.DONE_ROTATION_STATUSES` (`not_declared`, `ok`, `PREVIEW_REQUIRED`).
The corrected package retains `PREVIEW_REQUIRED`, which is done only as an
operator obligation (AGENTEE-151, owner decision 2026-10-18): stage 7
recomputes `preview_obligations` (every corrected CPL designator with its side
and the policy sha256) from the package's rotation report and corrected CPL,
refuses the stage when the fab manifest's copy differs, and records it in the
stage receipt and the record. The operator confirms each designator in the
JLCPCB placement preview before ordering
([preview obligations](fab-package.md#preview-obligations-agentee-151)).
Without obligations the record's `fab_rotation_blocker` names the reason,
`fab: cpl rotation PREVIEW_REQUIRED: no preview obligations recorded`; the
stage status stays `PREVIEW_REQUIRED`.
Supply `--cpl-inputs DIRECTORY` with the
[captured policy and supplier tree](cpl-rotation.md#generator-fab-integration).
Stage 7 refuses a package whose consumed policy digest differs from the one
stage 1 captured. The driver never relaxes, overrides or
reinterprets a check, reviewer, adjudicator or convergence result.

## Limitations

- The spec parser extracts parameters of the RC template only. It is not a
  general requirements compiler.
- The catalog is a design BOM: no stock, price, tolerance or rating data.
- kicad-route runs as an ordinary process; its state directory is not yet
  mounted read-only (KICADROUTE-6).
- Fab export uses the owned native exporter and captured CPL policy. The
  bottom-side transform still needs a real JLCPCB preview. No DFM is claimed.
- The envelope is an exact-key input of `board_build`, so it carries its
  experimental status in the schema name only.

Tests: `PYTHONPATH=src /usr/bin/python3 -m unittest tests.test_generate -v`.
The integration test runs stages 1 to 5 and 7 with `--skip-review` over the
tests' owned synthetic catalog, committed to a throwaway git checkout, never the
owner's live fome-mc BOM. Stage 5 runs the pinned kicad-route named by
`AGENT_EE_KICAD_ROUTE_CHECKOUT`, put first on `PATH`. Without the toolchain or
that variable the test skips, and under `AGENT_EE_REQUIRE_TOOLCHAIN=1` it fails.
Tests never run stage 6 with inference.

# Rung 1 on the owned pipeline (experimental driver)

`agent_ee.rung1_qualified` runs rung 1 as a staged repair campaign that starts
from an **empty** candidate (decision 1), like
[agent_ee.rung1_campaign](rung1-campaign.md). The difference is that every
generation step is now an owned module from main. The driver adds no campaign
code and patches no owned module. It prepares immutable inputs, writes the
empty native project, compiles a [staged scope](staged-campaign.md), and
declares the steps. The ordinary `agent-ee campaign` command does the work, and
`agent-ee verify-campaign` checks it.

The driver's own records are `experimental-unqualified` and use schema names
ending in `0-experimental`. The checks, stage receipts, placement records and
routing reports in a run are the owned, qualified ones.

## Command

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /usr/bin/python3 -m agent_ee.rung1_qualified \
  fixtures/rung1/spec.md --out RUNDIR --dry-run --cpl-inputs /path/to/your-cpl-inputs
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /usr/bin/python3 -m agent_ee.rung1_qualified \
  fixtures/rung1/spec.md --out RUNDIR --cpl-inputs /path/to/your-cpl-inputs
```

For catalog-bound R1/C1/J1, add `--parts-manifest PRIVATE_BUNDLE/parts.json`
and use `fixtures/rung1/spec-assembled.md`. The [captured-parts route](rung1-parts.md)
replays the owned `parts/1` bundle, captures its closure and adds membership,
assembly-readiness and package-consistency checks. It requires the explicit
assembled-header decision; the original hand-soldered spec is refused on this
route.

`--out` must not exist. `--profile` defaults to `profiles/claude-baseline.json`.
`--kicad-route` must name a kicad-route checkout at the routing adapter's pin
(`e497916`) with `vendor/krt` and `.venv-krt` installed; the default is
`$AGENT_EE_KICAD_ROUTE_CHECKOUT`, else the standard image path `/opt/kicad-route`
(see [Routing pin](#routing-pin)). The test module gates on the same variable through
`tests/toolchain.py`'s `require_kicad_route()`: unset is a skip, or a failure under
`AGENT_EE_REQUIRE_TOOLCHAIN=1`; a configured checkout that does not qualify always fails.
`--max-iterations` defaults to 4 and `--max-seconds` to 3600 (see
[Iteration budget](#iteration-budget)). The exit status is 0 only when the
record says `done: true`.

`--dry-run` spends no inference. It builds and validates everything, runs the
registration probes, and runs a checks-only review of the empty candidate. It
then rehearses each stage on a copy of the captured design: the schematic steps,
a checks-only review, the placement adapter's helper step exactly as the
campaign declares it, a checks-only review, the routing step with
`routing_adapter.verify`, and a last checks-only review. The rehearsal is
outside any campaign and is not acceptance evidence.

Without `--dry-run` the command runs `agent-ee campaign RUNDIR/campaign.json
--out RUNDIR/campaign` once, then `agent-ee verify-campaign`. A failed
reviewer or writer call is recorded as it happened and never retried.

## Stages and owned modules

| Stage | Owned module (Plane item) | How the run uses it |
| --- | --- | --- |
| requirements | `design_dialog`, `design_intent` (AGENTEE-25, AGENTEE-26) | through `agent_ee.generate` stage 1 |
| envelope | `board_envelope` (AGENTEE-85) | `derive`, `stage_one_requirements` and `round_trip`; the empty board is built from the captured envelope |
| scaffold | `scaffold.native_files` (AGENTEE-87, AGENTEE-134) | the shared `write-scaffold` step, run in place before the campaign and imported as its pre-receipt |
| parts | `parts_manifest`, `catalog`, `part_selection` (AGENTEE-86/27/28) | `--parts-manifest` replays and freezes the canonical bundle; without it the experimental BOM route remains (see [Gaps](#gaps)) |
| schematic | `schematic_emitter` (AGENTEE-40) | step `emit-schematic`: intent, layout and captured symbols |
| board population | `forward_annotation` (AGENTEE-52) | step `forward-annotate`: native XML netlist, plan, apply to a new board |
| placement | `placement_adapter` with helper `agent-ee.placer/1` (AGENTEE-91, AGENTEE-54) | campaign `placement_adapter` declaration; no row fallback |
| routing | `routing_adapter.step` and `kicad_route_attempt` (AGENTEE-93) | step `kicad-route-krt`, adoptable exits `[0, 2]` as the adapter declares |
| verification | `rc_transient` check | unchanged from the closed-loop control |
| fab | `fab_export` (AGENTEE-23), `fab_consistency` (AGENTEE-73), `part_binding` (AGENTEE-32) checks; with `--cpl-inputs`, then `cpl_rotation` (AGENTEE-126) | stage `fab` in the scope; with `--cpl-inputs`, the receipted `export-fab` step, whose corrected package remains `PREVIEW_REQUIRED` |

`board_build.py` is not used at all: forward annotation populates the board.

### Connectivity intent

`generation/intent.json` is the `agent_ee.generate` intent with `/` stripped
from net names: KiCad prefixes a root-sheet local label with the sheet path, so
the labels `IN` and `OUT` export as `/IN` and `/OUT`. The ground net carries a
declared `power:GND` (`#PWR01`) and a `power:PWR_FLAG` (`#FLG01`); every other
pin is passive, so the flag drives the ground symbol's power input for ERC.
`GND` is a global power net and exports as `GND`, the name the placement policy
and the `rc-step` check use. A spec ground other than `GND` has no stock power
symbol and is refused. The emitter writes both symbols `in_bom no` and
`on_board no`, so the schematic presence probe and the native netlist agree on
`C1`, `J1` and `R1` (AGENTEE-123).

### Staged scope

The scope is `agent-ee.scope/2` with stage order schematic, placement,
routing, verification, fab. Checks, in order:

| Check | Kind | Stage | Requirement |
| --- | --- | --- | --- |
| `erc`, `netlist` | `erc`, `netlist` | schematic | R-native |
| `parity` | `netlist_parity` (`intent`, `topology`, `exactly`) | schematic | R-parity |
| `drc-placement` | `drc`, projection `placement-stage/1` | placement | R-native |
| `courtyard` | `placement` `courtyard-overlap/1` | placement | R-courtyard |
| `outline` | `placement` `outline-containment/1` | placement | R-outline |
| `drc` | `drc` (full, schematic parity) | routing | R-native |
| `geometry` | `geometry`, 0.2 mm | routing | R-width |
| `rc-step` | `rc_transient` | verification | R-response |
| `binding` | `part_binding` | fab | R-binding |
| `fab` | `fab_export` | fab | R-fab |
| `fab-consistency` | `fab_consistency` | fab | R-fab-consistency |
| `cpl` | `cpl_rotation` (only with `--cpl-inputs`) | fab | R-cpl |

The parity source is the captured rung-1 IR, `generation/intent.json`, which the
check resolves against the captured `stock/symbols` libraries in coordinator code.
The IR and those libraries are immutable baseline inputs. The golden netlist is a
sandboxed `kicad-cli` XML export of a copy of `fixtures/rc/control`, captured under
`generation/golden/`. It is a reference, not an oracle: no check reads it, and the
driver records a descriptive comparison with it (see
[Schematic turn](#schematic-turn-agentee-102)). The subjects are
the symbols and footprints R1, C1 and J1 and at least one track; the retry
limit is 3. `mutable_inputs` is exactly the schematic and the board.
`requirements/qualified.md` states the added requirements; it adds no limit
that the spec does not state. The scope is validated by the loaders capture
uses, with the inputs copied, so the parity capability reads its source.

### Schematic turn (AGENTEE-102)

The rung-1 IR is `qualified_intent`: the `agent_ee.generate` connectivity intent,
built from the stage-1 record (nets, header pins, values) and the stage-2
selections (footprints and binding fields). J1.1 is on `IN`, R1 is between `IN`
and `OUT`, C1 is between `OUT` and `GND`, J1.2 is on `OUT`, J1.3 is on `GND`, and a
`power:GND` (`#PWR01`) and a `power:PWR_FLAG` (`#FLG01`), both without a
footprint, sit on `GND` (see [Connectivity intent](#connectivity-intent)). Library paths name the
scope's captured `stock/symbols/*.kicad_sym`. The scaffold refuses an IR that does
not resolve completely.

On the schematic turn the coordinator runs two steps in its networkless sandbox
on the delivered design. `emit-schematic` runs the pinned emitter
(`python3 -m agent_ee.schematic_emitter`) on `/candidate/generation/intent.json`
with captures read from `/candidate`. `forward-annotate` exports the native XML
netlist of the emitted schematic and applies `forward_annotation` to the
scaffold board. The three footprints land on its staging column, and placement
is left to the placement stage. The steps write only the existing schematic and
board, so the captured inventory gains no file.

The emitter receipt `out/emit-receipt.json` is a receipt artifact. It records
`intent_sha256`, `resolved_sha256`, the emitter module and its SHA-256, the
captured library hashes and the hash of each emitted file. The stage receipt
binds the inputs, the output hashes and the tool identity. The next review
seals `erc`, `netlist`, `parity` (`normalized_power_symbols` lists `#FLG01` and `#PWR01`) and
`drc-placement` (DRC with schematic parity).

If an IR pin does not resolve in the captured libraries, the emitter refuses
with `resolution_rejected` and the resolver's finding, writes no file, and exits
1. The step is then `failed`, `stage_receipts.adopt` refuses it (`no output
adopted or substituted`), forward annotation does not run, and the campaign
records `incomplete`. No symbol is guessed.

`reference_parity` compares a sealed native netlist with the control export.
It uses the owned inventory reader and the exact topology comparison. The
record is `agent-ee.rung1-reference-parity/0-experimental` with role
`reference_not_oracle`. The dry run records it after the schematic turn as
`reference_after_schematic`, and the campaign record stores it for each
iteration as `reference_parity`. A read failure is recorded as `failed` and
`unknown`, never as agreement.

`SchematicTurnTest` in `tests/test_rung1_qualified.py` runs this turn natively,
without inference. It checks the following:

- Both steps complete, and the design's file set is unchanged.
- The emitter receipt binds the IR, the emitter and the adopted schematic.
- The board has the footprints C1, J1 and R1.
- `erc` and intent `parity` pass.
- `drc-placement` reports 0 schematic parity issues, and the full `drc` is
  `stage_not_reached`.
- The control reference shows the same components (C1, J1, R1) and three
  matched nets. The control and the IR both name their ground `GND`, the
  global net of a `power:GND` symbol, so the net names match as well.

`UnresolvedPinTest` gives R1 a pin 3 and checks the refusal through
`run_step`, `adopt` and `campaign.run_implementation_steps`.

### Steps and the step plan

On each implementation turn the campaign runs only the steps of the earliest
failing stage (`campaign.step_plan`, `agent-ee.step-plan/1`):

| Turn | Steps |
| --- | --- |
| schematic | `emit-schematic` (`steps/emit.sh`), then `forward-annotate` (`steps/annotate.sh`) |
| placement | the placement adapter: writer transforms, then one `agent-ee-placer` helper receipt |
| routing | `kicad-route-krt` from `routing_adapter.step` |
| verification | none |
| fab | with `--cpl-inputs` only: the shared `export-fab` step (`fab_export` followed by `cpl_rotation`), with an AGENTEE-95 receipt; otherwise none |

The wrappers in [fixtures/rung1/qualified/steps](../fixtures/rung1/qualified/steps/)
only call the owned CLIs. Forward annotation is a schematic-turn step because
the placement adapter refuses any other placement-stage step, and the placer
needs footprints on the board.

### Placement policy

`generation/placement-policy.json` is a captured `agent-ee.packing-policy/1`.
It fixes no reference (see [Gaps](#gaps)) and names `GND` as the ground net.
Its obstacles are four 0.5 mm bands along the inside of the outline, on each
side, one obstacle per band. The placer grades courtyards against the outline
only. In a dry run without the bands it put C1's pad 0.255 mm from the edge, and
native DRC reported `copper_edge_clearance` against the template board's 0.5 mm
rule, so the placement stage could not pass. The bands, with the policy's 1 mm
gap, keep every courtyard 1.5 mm inside the edge. The placer reserves one
bounding box per obstacle, so a single obstacle holding all four bands
reserves the whole board (`packing_search_exhausted`).

### Iteration budget

Because of the step plan, one stage advances per implementation turn. The
empty candidate needs four reviews to reach the fab checks: empty, after the
schematic turn, after the placement turn, after the routing turn. Two
iterations would stop after the schematic turn, so the default is 4. The
campaign budget stays at 3600 s, as the integrator set it.

### Routing pin

Current `routing_adapter.preflight` accepts only
`e4979160a19b89837f9a9e9e25db620f3449f40a`, with exact `bin/` and `src/`
blob hashes and installed KRT state. Set `AGENT_EE_KICAD_ROUTE_CHECKOUT` to
that detached checkout; the standard image provides `/opt/kicad-route`.
[AGENTEE-187 qualification](routing-adapter.md#pin-qualification-record-agentee-187-2026-10-18)
records the migration. Historical rung-1 evidence used
`08179cdc97d08864da127f14d53ac46cf5a27851` at
`~/git/agent-ee/build/kicad-route-pin-08179cd`; its captured identities remain
historical and do not qualify as runs of the current adapter.

### Empty scaffold

Selected symbol subsets live under `scaffold/generation/symbols/`; the scope
also maps that directory to `stock/symbols`. Stock footprints and templates
are captured separately. This keeps the scaffold pre-receipt's after-tree
bound to its own inputs without claiming the separately captured stock tree.
With `--parts-manifest`, the driver copies `rung1-package-map.json` before
the scaffold step runs, so both the pre-receipt and `scaffold.json` bind it.
Compiling the scope only reads the scaffold; it does not add files to it.

The driver writes `requirements/` and the generation inputs. The empty
schematic, board and project come from `agent_ee.scaffold.native_files`, the one
empty-scaffold builder on main, through the same sandboxed `write-scaffold` step
as the [RC campaign driver](rung1-campaign.md#empty-scaffold) (AGENTEE-134). The
step runs in place before the campaign exists. It reads the captured
`generation/envelope/envelope.json` (`agent-ee.envelope/1`) and
`generation/layout.json` at their candidate paths. The board takes its outline,
copper layers and stackup from the envelope. The schematic and project name the
root-sheet UUID that `schematic_emitter` derives from the layout. The fixture
project supplies only the `.kicad_pro` settings and the stock library tables. The
step's receipt is kept in `RUNDIR/scaffold-receipt/`. The campaign configuration
declares it under `pre_receipts` and never as an implementation step or stage.
Campaign start imports it, and `verify-campaign` refuses a first capture that
differs from the receipt's after-tree.

## Records

| Path | Content |
| --- | --- |
| `prep/` | `agent_ee.generate` stage-1 and stage-2 receipts |
| `parts-catalog/` | the catalog source manifest and volume built from the BOM through `generate.read_catalog_with_schema` and `generate.catalog_lcsc`, and the capture result (`header_schema` and `columns` recorded under `/parts/catalog_capture`) |
| `scaffold/` | empty project, requirements, generation inputs (intent, layout, parts manifest, footprint manifest, placement policy, envelope records, golden netlist) |
| `scaffold-receipt/` | the `write-scaffold` [stage receipt](stage-receipts.md) that wrote the empty project |
| `campaign/pre-receipts/1/` | the imported scaffold receipt that `verify-campaign` revalidates against the first capture |
| `scaffold-checks/` | kicad-cli loads of the empty files |
| `probes/`, `probes.json` | scope/3 variants that consume the envelope, with the loaders' exact refusals |
| `scope.json`, `campaign.json` | staged scope and campaign configuration |
| `dry-run/` | checks-only reviews and the rehearsal receipts |
| `campaign/` | the campaign root |
| `rung1-qualified-record.json` | `agent-ee.rung1-qualified-record/0-experimental` |

`done` is the campaign verdict `criteria_satisfied` with `verify-campaign`
exit 0 (which includes the scaffold pre-receipt's first-capture binding), and the final state lists the `agent-ee.stage-done/1` binding that
verify-campaign recomputed (`stage_receipts_done.bound`). The schematic must be
`emit-schematic`'s output. The board must come from `forward-annotate`, then the
placement adapter's footprint moves, then the routing step, with no other edit
([done through declared steps](stage-receipts.md#done-through-declared-steps)).
The fab package is graded by checks inside the scope.

`done` needs the campaign verdict `criteria_satisfied`, `verify-campaign`
exit 0 and a rotation status in `fab_package.DONE_ROTATION_STATUSES`
(`not_declared`, `ok`, `PREVIEW_REQUIRED`). Without `--cpl-inputs` the scope declares no
`cpl_rotation`, the fab package is graded by checks inside the scope as before,
and the status is `not_declared`. With `--cpl-inputs DIRECTORY` (the immutable
[policy and supplier tree](cpl-rotation.md#generator-fab-integration)) the scope
declares `cpl`, the record also needs the derived corrected package, and the
current CPL report's `PREVIEW_REQUIRED` is done only as an operator obligation
(AGENTEE-151, owner decision 2026-10-18): `fab_outcome` recomputes
`preview_obligations` from the final run's sealed report and corrected CPL,
refuses a derived package whose copy differs, and the record lists them as
`preview_obligations` for the operator to confirm in the JLCPCB placement
preview before ordering
([preview obligations](fab-package.md#preview-obligations-agentee-151)).
Without them `done` stays false with `fab_rotation_blocker: "fab: cpl rotation
PREVIEW_REQUIRED: no preview obligations recorded"`. A declared correction whose captured policy is excluded from the
scope is refused at capture. The qualified record points to the derived
corrected package; the sealed native export and check evidence stay unchanged.

## Recorded run (2026-10-17)

One real run used commit 512262a on `impl/rung1-qualified` (branched from main
at f442ba0), before AGENTEE-123: its intent declared no power symbol, and its
ground was the local label `/GND`. It ran from a clean worktree, with the
kicad-route pin clone and `profiles/claude-baseline.json`, `max_iterations` 4
and `max_seconds` 3600.
The run directory is copied to `build/rung1-qualified-2026-10-17/`, with the
final dry run under `dry-run-rehearsal/`. The run also predates the shared
scaffold builder and its pre-receipt (AGENTEE-134). Its empty board kept the
fixture board's setup and had no stackup, so the current driver does not
reproduce its candidate bytes. The campaign stopped `incomplete`,
so `done` is false. `verify-campaign` exits 0 and prints `incomplete`.

- Run 1 reviewed the empty candidate. The schematic, placement and routing
  checks were `stage_not_reached`, and the verification and fab checks were
  `stage_blocked`. Both reviewers and the adjudicator ran. Implementation 1
  (schematic turn): the writer ran for 254 s, then `emit-schematic` and
  `forward-annotate` completed with exit 0.
- Run 2: `erc` pass, `netlist` info, `parity` pass (that run's parity source was
  the control's golden XML; it is now the IR), `drc-placement` pass,
  `courtyard` pass, `outline` defect (3 footprints on the staging column).
  Implementation 2 (placement turn): the writer ran for 110 s, then the
  `agent-ee-placer` helper completed, and the placement record is `proposed`.
- Run 3: every schematic and placement check passed, and routing was
  `stage_not_reached`. Implementation 3 (routing turn): the writer routed the
  board itself with pcbnew. `SaveBoard` rewrote the immutable
  `project/rung1_rc.kicad_pro`, and the campaign stopped with `ValueError:
  implementation changed immutable verification input:
  project/rung1_rc.kicad_pro` before the `kicad-route-krt` step ran. No
  owned module refused anything. The driver did not retry.

The final dry run, at the same code, shows the stages the campaign did not
reach. After the rehearsed KRT route (exit 0, `complete`, verified by
`routing_adapter.verify`), `drc`, `geometry` and `rc-step` pass. `binding` is
`ok/unknown` (18 missing binding properties). `fab` is `unsupported/unknown`
(`BOM has parts without a complete LCSC/manufacturer/part-number binding: C1,
J1, R1`), so `fab-consistency` is `unsupported/unknown`.

## Gaps

The recorded default run has the following limits. Supplying a captured-parts
bundle closes its sourcing/binding gaps as described below; the other limits
remain.

- **Writer on generation turns.** On the routing turn the writer routed the
  board with pcbnew instead of leaving the board to the routing step. pcbnew's
  save rewrote the immutable project file, and the existing proposal check
  ended the campaign. The writer prompt does not say that a coordinator step
  will route, and nothing stops the writer from saving through pcbnew.

- **Envelope binding (AGENTEE-85).** The envelope is captured under
  `generation/envelope/` but no check consumes it. A scope/3 that declares it
  and binds every obligation is refused at capture with
  `envelope obligations lack executable coverage and are refused, not dropped:
  R-envelope-rung1-rc-stackup (unknown: envelope-stackup: copper_weight_oz needs
  qualified captured manufacturing authority; no inferred conversion)`. The
  `board_envelope` kind is also absent from `capabilities.KINDS` and
  `checks.CHECK_FUNCS`. So J1 is not fixed on the left edge: the placer may put
  it anywhere on the board, and connector-on-edge is not graded.
- **Part binding.** The owned emitter now writes `part_binding` string fields. The
  default fome-mc BOM path still supplies no manufacturer or MPN, so its
  `part_binding` and `fab_export` checks remain incomplete. The optional
  [captured-parts route](rung1-parts.md) supplies catalog identities and ratings;
  a real supplier-bound reference run is recorded there. Complete staged
  campaign qualification remains outstanding.
- **J1 not assembled.** `forward_annotation` refuses DNP (`dnp_unsupported`)
  and the parity source compares population state, so J1 stays populated.
  `fab_export` can leave a populated part out of the BOM and CPL under a
  declared `JLCPCB_Assembly=not_assembled` rule
  ([assembly exclusions](fab-export.md#assembly-exclusions), AGENTEE-122). The
  qualified scope does not declare that rule yet, so J1 is still flagged on the
  original hand-soldered route. The optional captured-parts route requires an
  explicitly assembled J1 with its own catalog selection.
- **Parts catalog (AGENTEE-27/28).** The owned capture needs `mfr`, `mpn` and
  0/1 `is_basic`/`is_preferred` values that the BOM does not have, and the
  selector needs stock and tolerance. The capture refusal is recorded; parts
  stay with the experimental `generate` selection unless a complete captured
  `parts/1` bundle is supplied. Without that bundle, membership, assembly and
  package checks are listed as missing coverage.
- **3D models.** Stock footprints use `${KICAD10_3DMODEL_DIR}` model paths,
  which `fab_consistency` reports as unknown until library roots map to
  captured inputs.

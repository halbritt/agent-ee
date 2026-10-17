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
  fixtures/rung1/spec.md --out RUNDIR --dry-run
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /usr/bin/python3 -m agent_ee.rung1_qualified \
  fixtures/rung1/spec.md --out RUNDIR
```

`--out` must not exist. `--profile` defaults to `profiles/claude-baseline.json`.
`--kicad-route` must name a kicad-route checkout at the routing adapter's pin
(`08179cd`) with `vendor/krt` and `.venv-krt` installed; the default is
`~/git/agent-ee/build/kicad-route-pin-08179cd` (see [Routing pin](#routing-pin)).
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
| envelope | `board_envelope` (AGENTEE-85) | `derive`, `stage_one_requirements` and `round_trip`; the scaffold outline is read back from the validation record |
| parts | `catalog` (AGENTEE-27), `part_selection` (AGENTEE-28) | capture attempted on the fome-mc BOM; selection stays `agent_ee.generate` stage 2 (see [Gaps](#gaps)) |
| schematic | `schematic_emitter` (AGENTEE-40) | step `emit-schematic`: intent, layout and captured symbols |
| board population | `forward_annotation` (AGENTEE-52) | step `forward-annotate`: native XML netlist, plan, apply to a new board |
| placement | `placement_adapter` with helper `agent-ee.placer/1` (AGENTEE-91, AGENTEE-54) | campaign `placement_adapter` declaration; no row fallback |
| routing | `routing_adapter.step` and `kicad_route_attempt` (AGENTEE-93) | step `kicad-route-krt`, adoptable exits `[0, 2]` as the adapter declares |
| verification | `rc_transient` check | unchanged from the closed-loop control |
| fab | `fab_export` (AGENTEE-23), `fab_consistency` (AGENTEE-73), `part_binding` (AGENTEE-32) checks | stage `fab` in the scope; no driver export |

`board_build.py` is not used at all: forward annotation populates the board.

### Staged scope

The scope is `agent-ee.scope/2` with stage order schematic, placement,
routing, verification, fab. Checks, in order:

| Check | Kind | Stage | Requirement |
| --- | --- | --- | --- |
| `erc`, `netlist` | `erc`, `netlist` | schematic | R-native |
| `parity` | `netlist_parity` (`golden_xml`, `topology`, `exactly`) | schematic | R-parity |
| `drc-placement` | `drc`, projection `placement-stage/1` | placement | R-native |
| `courtyard` | `placement` `courtyard-overlap/1` | placement | R-courtyard |
| `outline` | `placement` `outline-containment/1` | placement | R-outline |
| `drc` | `drc` (full, schematic parity) | routing | R-native |
| `geometry` | `geometry`, 0.2 mm | routing | R-width |
| `rc-step` | `rc_transient` | verification | R-response |
| `binding` | `part_binding` | fab | R-binding |
| `fab` | `fab_export` | fab | R-fab |
| `fab-consistency` | `fab_consistency` | fab | R-fab-consistency |

The golden netlist is a sandboxed `kicad-cli` XML export of a copy of
`fixtures/rc/control`, captured under `generation/golden/`. The subjects are
the symbols and footprints R1, C1 and J1 and at least one track; the retry
limit is 3. `mutable_inputs` is exactly the schematic and the board.
`requirements/qualified.md` states the added requirements; it adds no limit
that the spec does not state. The scope is validated by the loaders capture
uses, with the inputs copied, so the parity capability reads its source.

### Steps and the step plan

On each implementation turn the campaign runs only the steps of the earliest
failing stage (`campaign.step_plan`, `agent-ee.step-plan/1`):

| Turn | Steps |
| --- | --- |
| schematic | `emit-schematic` (`steps/emit.sh`), then `forward-annotate` (`steps/annotate.sh`) |
| placement | the placement adapter: writer transforms, then one `agent-ee-placer` helper receipt |
| routing | `kicad-route-krt` from `routing_adapter.step` |
| verification, fab | none: these stages have no generation step |

The wrappers in [fixtures/rung1/qualified/steps](../fixtures/rung1/qualified/steps/)
only call the owned CLIs. Forward annotation is a schematic-turn step because
the placement adapter refuses any other placement-stage step, and the placer
needs footprints on the board.

### Placement policy

`generation/placement-policy.json` is a captured `agent-ee.packing-policy/1`.
It fixes no reference (see [Gaps](#gaps)) and names `/GND` as the ground net.
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

`routing_adapter.preflight` accepts only a checkout whose HEAD is
`08179cdc97d08864da127f14d53ac46cf5a27851` and whose `bin/` and `src/` match
the pinned blobs. `~/git/kicad-route` has moved past the pin, and
`build/kicad-route-snapshot` has no `.git`. The run therefore uses a clone of
`~/git/kicad-route` checked out at the pin, with `vendor/` and `.venv-krt`
copied from the snapshot. Nothing in either source tree was changed.

## Records

| Path | Content |
| --- | --- |
| `prep/` | `agent_ee.generate` stage-1 and stage-2 receipts |
| `parts-catalog/` | the catalog source manifest and volume built from the BOM, and the capture result |
| `scaffold/` | empty project, requirements, generation inputs (intent, layout, parts manifest, footprint manifest, placement policy, envelope records, golden netlist) |
| `scaffold-checks/` | kicad-cli loads of the empty files |
| `probes/`, `probes.json` | scope/3 variants that consume the envelope, with the loaders' exact refusals |
| `scope.json`, `campaign.json` | staged scope and campaign configuration |
| `dry-run/` | checks-only reviews and the rehearsal receipts |
| `campaign/` | the campaign root |
| `rung1-qualified-record.json` | `agent-ee.rung1-qualified-record/0-experimental` |

`done` is the campaign verdict `criteria_satisfied` with `verify-campaign`
exit 0. The fab package is graded by checks inside the scope.

## Gaps

These are recorded in every run with the owned module's exact refusal:

- **Envelope binding (AGENTEE-85).** The envelope is captured under
  `generation/envelope/` but no check consumes it. A scope/3 that declares it
  and binds every obligation is refused at capture with
  `envelope obligations lack executable coverage and are refused, not dropped:
  R-envelope-rung1-rc-stackup (unknown: envelope-stackup: copper_weight_oz needs
  qualified captured manufacturing authority; no inferred conversion)`. The
  `board_envelope` kind is also absent from `capabilities.KINDS` and
  `checks.CHECK_FUNCS`. So J1 is not fixed on the left edge: the placer may put
  it anywhere on the board, and connector-on-edge is not graded.
- **Part binding.** The emitter writes only library properties (Reference,
  Value, Footprint, Datasheet, Description); the intent's `part_binding` is
  preserved by the resolver but not emitted. No owned module writes `LCSC`,
  `Manufacturer` or `Partnumber`, and the fome-mc BOM has no manufacturer or
  MPN. `part_binding` and `fab_export` therefore cannot pass on the generated
  design.
- **J1 not assembled.** `fab_export` excludes only DNP parts.
  `forward_annotation` refuses DNP (`dnp_unsupported`) and the parity source
  compares population state, so J1 stays populated and is flagged.
- **Parts catalog (AGENTEE-27/28).** The owned capture needs `mfr`, `mpn` and
  0/1 `is_basic`/`is_preferred` values that the BOM does not have, and the
  selector needs stock and tolerance. The capture refusal is recorded; parts
  stay with the experimental `generate` selection.
- **Power symbols (AGENTEE-40 with AGENTEE-48/88).** The emitter copies
  `in_bom`/`on_board` from the library definition, so a declared `power:GND` or
  `power:PWR_FLAG` is written with `in_bom yes` and `on_board yes`. The staged
  presence probe's bounded reader then counts `#PWR01` and `#FLG01`, while the
  native netlist omits them. A dry run on 2026-10-17 with those symbols in the
  intent stopped every schematic check at `stage subject probe did not
  complete`, from `python3 -m agent_ee.stages
  /candidate/project/rung1_rc.kicad_sch symbols /work/references.json`:
  `ValueError: bounded inventory disagrees with native reader; coverage
  unsupported` (bounded `#FLG01, #PWR01, C1, J1, R1`, native `C1, J1, R1`).
  The intent therefore declares no power symbol. Its labels `IN`, `OUT` and
  `GND` are local and export as `/IN`, `/OUT` and `/GND`, and the `rc-step`
  check names the ground node `/GND`. The topology parity check ignores net
  names.
- **3D models.** Stock footprints use `${KICAD10_3DMODEL_DIR}` model paths,
  which `fab_consistency` reports as unknown until library roots map to
  captured inputs.

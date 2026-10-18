# Rung 1 as a staged repair campaign (experimental)

`agent_ee.rung1_campaign` runs rung 1 the way decision 1 states it: generation is a
repair campaign that starts from an **empty** candidate, on the existing
writer/reviewer/adjudicator/check loop. It adds no campaign code. It prepares
inputs, writes an empty native project, compiles a
[staged scope](staged-campaign.md), and declares the generation tools as
[implementation steps](stage-receipts.md). The ordinary `agent-ee campaign`
command then does the work, and `agent-ee verify-campaign` checks the result.

Every record that this driver writes is `experimental-unqualified`. The schema
names end in `0-experimental`. The driver does not replace
[agent_ee.generate](generate.md): that module runs the same tools as a straight
pipeline and then reviews the finished design once.

## Command

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /usr/bin/python3 -m agent_ee.rung1_campaign \
  fixtures/rung1/spec.md --out RUNDIR --dry-run --kicad-route ~/git/kicad-route
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /usr/bin/python3 -m agent_ee.rung1_campaign \
  fixtures/rung1/spec.md --out RUNDIR --kicad-route ~/git/kicad-route
```

`--out` must not exist. `--profile` defaults to `profiles/claude-baseline.json`.
`--kicad-route` names a kicad-route checkout that has `vendor/krt` and
`.venv-krt` installed. The step mounts it read-only, so a snapshot of the
checkout at a fixed commit is better than a checkout that other sessions edit.
`--max-seconds` is the campaign budget (default 3600). The exit status is 0
only when the record says `done: true`.

`--dry-run` spends no inference. It runs every preparation and validation
step. Then it runs a checks-only `agent-ee review` of the empty candidate and
a rehearsal of the four steps through `stage_receipts.run_step` on a copy of
the captured empty design. Last, it runs a checks-only review of the
rehearsed design. The rehearsal is outside any campaign and is not acceptance
evidence.

Without `--dry-run`, the command runs `agent-ee campaign RUNDIR/campaign.json
--out RUNDIR/campaign` once, and then `agent-ee verify-campaign`. A reviewer
or writer failure is recorded as it happened. The driver never retries it.

`--rebuild-record` recomputes `rung1-campaign-record.json` for a finished
campaign in `--out` and keeps the earlier record as
`rung1-campaign-record.previous.json`. It runs `verify-campaign` again, from
the package that ran the campaign, and spends no inference.

## What the driver writes

| Path | Content |
| --- | --- |
| `prep/stages/1-requirements`, `prep/stages/2-parts` | `agent_ee.generate` stage-1 and stage-2 receipts, the compiled requirements and the parts manifest |
| `scaffold/project/` | `rung1_rc.kicad_pro`, an empty `rung1_rc.kicad_sch`, an empty `rung1_rc.kicad_pcb`, and the stock library tables |
| `scaffold/requirements/` | `spec.md`, `system.md`, `targets.md`, `reference.md` from stage 1 |
| `scaffold/generation/` | Connectivity intent, resolved record, parts manifest and catalog copy, envelope, placement request, and an immutable template board |
| `scaffold-checks/` | kicad-cli ERC, XML netlist export and parity DRC of the empty files, on sandboxed copies |
| `scope.json`, `campaign.json` | The staged scope/2 and the `agent-ee.campaign/1` configuration |
| `campaign/` | The campaign root: runs, implementations, stage receipts, state |
| `rung1-campaign-record.json` | `agent-ee.rung1-campaign-record/0-experimental` |

### Empty scaffold

The schematic has version 20260306, no symbols, one sheet instance, and the
root-sheet UUID that the schematic emitter derives from the resolved record. The
project file therefore names the same root sheet before and after emission.
The board keeps the layers, setup and stackup of the fixture board
`fixtures/rc/control/rc_filter.kicad_pcb` (version 20260206). It has no
footprints, copper, zones or nets. Its Edge.Cuts rectangle is the envelope
outline (50 x 30 mm at 100,100 mm). Both versions are in
`stages.PRESENCE_VERSIONS`. Before the scope is written, kicad-cli must load
both files in the sandbox.

### Staged scope

The checks and their stages are `erc` and `netlist` (schematic), `drc` with
schematic parity (placement), `geometry` with a 0.2 mm minimum width
(routing), and `rc-step` (verification). The `rc-step` parameters are the same
as in the closed-loop control: a 1 V step at `/IN`, a 5 ms stop, and `/OUT` at
1 ms in [0.62, 0.65] V. R-native, R-width and R-response map to these checks
as in `fixtures/closed-loop/control.scope.json`. The stage subjects are the
symbols R1, C1 and J1, the footprints R1, C1 and J1, and at least one track.
The retry limit is 3.

`mutable_inputs` is exactly the schematic and the board. Everything under
`generation/` and `requirements/` is immutable. The driver validates the
scope with the capture selection, `baseline.compile_baseline`,
`stages.declaration` and `cli.validate_scope` before it writes the
configuration.

### Implementation steps

The steps run in this order after each implementation session, on the
writer's delivered design. Each step runs in the coordinator's networkless
sandbox, and only exit 0 is adoptable.

| Step | Stage | Program | Writes |
| --- | --- | --- | --- |
| `emit-schematic` | schematic | `steps/emit.py`: `agent_ee.schematic_emit` | the schematic, from `out/sch/` |
| `build-board` | placement | `steps/placement.sh`: XML netlist from `/candidate`, then `agent_ee.board_build --placement-fallback row` | the board, from `out/board/placed/` |
| `route-krt` | routing | `steps/route.sh`: `kicad-route run --backend krt --budget 300` on a copy of the project | the board, copied from the run's candidate to `out/candidate/` |
| `export-fab` | fab | `steps/fab.py`: Gerbers, drill, positions, BOM, JLCPCB rewrite, `fab-manifest.json` | the board, in place and unchanged |

The step programs are in
[fixtures/rung1/staged/steps](../fixtures/rung1/staged/steps/). They are mounted
at `/tool/steps`. The agent-ee source tree is mounted at `/tool/agent-ee` and is
recorded as the receipt's Python source tree. The kicad-route checkout is
mounted at `/tool/kicad-route` and is also its state directory
(`KICAD_ROUTE_STATE`). The routing step reports kicad-route's `result.json`
`backend` key. It never lists exit 2 (valid but incomplete), exit 3 (validation
failed), or exits 1 and 4 as adoptable. The configuration builder refuses those
exits, any exit other than 0, and any write outside `mutable_inputs`.

`stage_receipts.validate_step` requires at least one write. The fab step
therefore declares the board as an in-place write and leaves it unchanged. Its
exports are retained as receipt artifacts under
`implementations/N/receipts/4-export-fab/run/out/`.

The board builder reads its template from `generation/template/`, not from
the mutable board. A writer edit to the board cannot change the rules or the
stackup that the builder copies.

## Record and done gate

The record lists the preparation receipts, the scaffold and its load checks,
the scope and configuration hashes, and the kicad-route identity. For each
iteration, it lists the check outcomes with their stage reasons, the findings,
and the convergence projection. For each implementation, it lists the writer
execution, every step receipt (status, exit code, violations, tool identity,
reported version, backend, and input and output hashes), and the receipt index
applicability. It also records the `verify-campaign` exit code and the fab
manifest.

`done` is true only when all of these are true:

- The campaign verdict is `criteria_satisfied`, and `verify-campaign` exits 0.
- The fab exports exist, are not empty, and match their manifest.
- The fab step consumed the board and the schematic of the candidate that the
  final run accepted.

## Recorded run (2026-10-17)

One real run used commit 02d74cf from a clean clone, and a kicad-route
snapshot at 08179cd (KRT v0.23.1). The run stopped early with the state
`incomplete`, so `done` is false.

- Iteration 1 reviewed the empty candidate. `erc`, `netlist`, `drc` and
  `geometry` were `stage_not_reached`, and `rc-step` was `stage_blocked`. Both
  reviewers and the adjudicator ran. The adjudicator accepted the five check
  defects, and the campaign continued to `repair`.
- The writer ran for 190 s (exit 0). All four steps then completed with exit 0
  and no violations, and the coordinator adopted their outputs. The index
  marks `emit-schematic` and `build-board` as superseded, and `route-krt` and
  `export-fab` as applicable.
- Iteration 2 captured the generated design. All five checks passed (`netlist`
  is `info`), and both reviewers ran. Then the host binary
  `~/.local/bin/agy`, an unused harness that `runtime_files` hashes, was
  replaced at 20:47:51Z. The adjudicator was not run. The review stopped with
  `implementation or tool runtime changed during review; rerun required`, and
  the campaign stopped with `tool runtime changed; rerun under the current
  tools`. `verify-campaign` exits 2 for the same reason. AGENTEE-112 now
  records an unused harness change as an observation, and the review
  continues. The retained run was made under the earlier policy, so it still
  fails verification with `review implementation changed`.
- The fab step consumed the board and schematic of the iteration-2 candidate.
  It made 15 files, and the BOM and CPL list C1 and R1, with J1 excluded as
  `not_assembled`. That candidate was not accepted.

## Limits

- Placement uses the unqualified row stand-in of `board_build` (AGENTEE-109).
- The fab export is the driver's export. The qualified check kind is AGENTEE-23.
- Mount identities are observed when the step runs. Tool trees are not copied
  into the campaign.
- The writer runs before the steps. Its edits to the schematic or board are
  overwritten by the steps that follow. An edit to an immutable input stops
  the repair, as the campaign requires.
- The parts catalog is the fome-mc design BOM. It has no stock, price or
  tolerance data.

## Tests

```sh
PYTHONPATH=src /usr/bin/python3 -m unittest tests.test_rung1_campaign -v
```

The tests check that the scaffold is valid and deterministic, that its project
file and tables equal the emitter's, and that the scope validates with the
real loaders. They check that widened write authority, an adoptable exit 3 and
reserved mount points are refused. With the native toolchain, they check that
kicad-cli loads the empty files and that a checks-only review of the empty
candidate gives `stage_not_reached` for `erc`, `netlist`, `drc` and `geometry`
and `stage_blocked` for `rc-step`. No test runs a campaign or inference.

## Review conventions file

The scaffold adds `requirements/review-conventions.md` and lists it in the scope's
`requirements`. It restates, for the independent reviewers and the adjudicator, how the
coordinator validates citations and witnesses (allowed path prefixes; `baseline.json` is
sealed context, not a citable path; witnesses cite unchanged check results). It changes no
check, limit or acceptance rule. It was added after two campaign attempts on 2026-10-17 were
recorded incomplete because an otherwise valid adjudication cited `baseline.json` and a
reviewer witness was malformed (AGENTEE-111, AGENTEE-115). The default `max_iterations` for
this runner is 2: the empty candidate and one generated candidate.

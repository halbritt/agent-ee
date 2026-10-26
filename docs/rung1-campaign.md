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
  fixtures/rung1/spec.md --out RUNDIR --dry-run --kicad-route ~/git/kicad-route \
  --cpl-inputs /path/to/your-cpl-inputs
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /usr/bin/python3 -m agent_ee.rung1_campaign \
  fixtures/rung1/spec.md --out RUNDIR --kicad-route ~/git/kicad-route \
  --cpl-inputs /path/to/your-cpl-inputs
```

`--out` must not exist. `--profile` defaults to `profiles/claude-baseline.json`.
`--kicad-route` names a kicad-route checkout that has the owned engine and
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
| `scaffold/generation/` | Connectivity intent, resolved record, parts manifest and catalog copy, the board builder's envelope, the scaffold's `agent-ee.envelope/1` (`board-envelope.json`), placement request, and an immutable template board |
| `scaffold-receipt/` | The `write-scaffold` [stage receipt](stage-receipts.md) with its retained inputs, run and version records |
| `campaign/pre-receipts/1/` | The imported scaffold receipt that `verify-campaign` revalidates against the first capture |
| `scaffold-checks/` | kicad-cli ERC, XML netlist export and parity DRC of the empty files, on sandboxed copies |
| `scope.json`, `campaign.json` | The staged scope/2 and the `agent-ee.campaign/1` configuration |
| `campaign/` | The campaign root: runs, implementations, stage receipts, state |
| `rung1-campaign-record.json` | `agent-ee.rung1-campaign-record/0-experimental` |

### Empty scaffold

The empty schematic, board and project come from `agent_ee.scaffold.native_files`,
the one empty-scaffold builder on main, shared with the
[`scaffold` command](scaffold.md) and [rung1_qualified](rung1-qualified.md)
(AGENTEE-134). The schematic has version 20260306, no symbols, one sheet instance,
and the root-sheet UUID that the schematic emitter derives from the resolved record.
The project file therefore names the same root sheet before and after emission. The
board (version 20260206) is built from the captured `agent-ee.envelope/1`
`generation/board-envelope.json`: its Edge.Cuts outline (50 x 30 mm at 100,100 mm),
copper layer count, thickness and stackup, with the layer table in physical stack order.
It has no footprints, copper, zones or nets. Both versions are in
`stages.PRESENCE_VERSIONS`. Before the scope is written, kicad-cli must load both
files in the sandbox.

The driver writes only `requirements/` and the generation inputs itself. The project
files, the stock library tables and `generation/template/` are written by the
`write-scaffold` step (stage `scaffold`, program `steps/scaffold.py`). The step runs through
`stage_receipts.run_step` in the networkless sandbox on that directory, in place: it reads
`/candidate/generation/board-envelope.json` and `/candidate/generation/resolved.json` at
their candidate paths, so the receipt's after-tree is the first capture. It mounts the step
programs, the agent-ee source tree (recorded as the Python source tree) and the fixture
project `fixtures/rc/control` read-only at `/tool/template`; from the fixture it takes
only the `.kicad_pro` settings and the stock library tables. Only exit 0 is adopted, through
`stage_receipts.adopt`. A table that uses `${KIPRJMOD}`, or an envelope the builder refuses
(for example an odd copper layer count), exits 3. Any other status stops the
preparation with `PrepareFailure`, keeps the receipt and leaves no project.

### Staged scope

The checks and their stages are `erc` and `netlist` (schematic), `drc` with
schematic parity (placement), `geometry` with a 0.2 mm minimum width
(routing), `rc-step` (verification) and, last, the done gate `fab`
([`fab_package`](fab-package.md), verification). With `--cpl-inputs DIRECTORY`
the scaffold captures the [policy and supplier tree](cpl-rotation.md#generator-fab-integration)
under `generation/cpl/`, and the scope adds a final stage `fab` holding
`fab-export` (`fab_export`), `cpl` (`cpl_rotation`, export check `fab-export`) and
the done gate, which then seals the corrected CPL (R-cpl, sourced from
`requirements/fab.md`). Without the capture no CPL check is declared, the stage
order ends at verification, and the done gate seals the native CPL with rotation
status `not_declared`. The `rc-step` parameters are the same
as in the closed-loop control: a 1 V step at `/IN`, a 5 ms stop, and `/OUT` at
1 ms in [0.62, 0.65] V. R-native, R-width and R-response map to these checks
as in `fixtures/closed-loop/control.scope.json`; R-fab maps to `fab` and is
sourced from the scaffold's `requirements/fab-package.md`. The scope carries the
`generation` marker `{schema: agent-ee.generation/1, done_check: fab}`, so capture
refuses it if the done gate or R-fab is removed. The stage subjects are the
symbols R1, C1 and J1, the footprints R1, C1 and J1, and at least one track.
The retry limit is 3. The generated campaign also declares
`max_schema_resubmissions: 1` for separately recorded model output resubmissions;
this is independent of the stage retry limit. See the
[campaign output rule](campaign-guide.md#bounded-output-resubmission).

`mutable_inputs` is exactly the schematic and the board. Everything under
`generation/` and `requirements/` is immutable. The driver validates the
scope with the capture selection, `baseline.compile_baseline`,
`stages.declaration` and `run_record.validate_scope` before it writes the
configuration.

### Implementation steps

The coordinator selects the steps of the earliest failing stage after each
implementation session and runs them on the delivered design. Each step runs
in the networkless sandbox, and only exit 0 is adoptable.

| Step | Stage | Program | Writes |
| --- | --- | --- | --- |
| `emit-schematic` | schematic | `steps/emit.py`: `agent_ee.schematic_emit` | the schematic, from `out/sch/` |
| `build-board` | placement | `steps/placement.sh`: XML netlist from `/candidate`, then `agent_ee.board_build --placement-fallback row` | the board, from `out/board/placed/` |
| `route-krt` | routing | `steps/route.sh`: `kicad-route run --backend builtin --budget 300` on a copy of the project | the board, copied from the run's candidate to `out/candidate/` |
| `export-fab` | fab | `agent_ee.fab_stage`: native export, sealed CPL correction, derived `fab-manifest.json` | the board, in place and unchanged |

The schematic, placement and routing programs are in
[fixtures/rung1/staged/steps](../fixtures/rung1/staged/steps/) and are mounted
at `/tool/steps`. The fab step runs the shared source module; the older
`steps/fab.py` entry point delegates to that module. The agent-ee source tree is mounted at `/tool/agent-ee` and is
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

The record lists the preparation receipts, the scaffold with its stage receipt and load checks,
the scope and configuration hashes, and the kicad-route identity (commit, `describe` with a
`-dirty` suffix for tracked changes, read without writing the checkout). For each
iteration, it lists the check outcomes with their stage reasons, the findings,
and the convergence projection. For each implementation, it lists the writer
execution, every step receipt (status, exit code, violations, tool identity,
reported version, backend, and input and output hashes), and the receipt index
applicability. It also records the `verify-campaign` exit code, the
`export-fab` step manifest (provenance only), `fab_artifacts` from the campaign
state, and `fab_artifacts_recomputed_by_verify_campaign`, the hashes that
verify-campaign printed after recomputing them from the final run.

`done` is true only when all of these are true:

- The campaign verdict is `criteria_satisfied`, and `verify-campaign` exits 0.
- The final state lists the fab artifact hashes of the passing `fab` done check
  (`fab_artifacts`).
- verify-campaign recomputed the same `artifacts_id`, candidate and file hashes
  (`fab_artifacts_verified`).
- The sealed rotation status (`fab_artifacts.rotation.status`, recorded as
  `fab_rotation`) is in `fab_package.DONE_ROTATION_STATUSES`,
  `{not_declared, ok, PREVIEW_REQUIRED}`. A declared `cpl_rotation` always
  reports `PREVIEW_REQUIRED`, which is done only as an operator obligation
  (AGENTEE-151, owner decision 2026-10-18): verify-campaign prints the
  `preview_obligations` it recomputed from the final run (each corrected CPL
  designator with its side, and the policy sha256), the record requires them to
  equal the state's, and lists them as `preview_obligations` for the operator to
  confirm in the JLCPCB placement preview before ordering
  ([preview obligations](fab-package.md#preview-obligations-agentee-151)).
  Missing or disagreeing obligations keep `done` false (`fab_rotation_blocker`,
  `fab_artifacts_verified`). A missing or unknown status is never done.
- `scaffold_receipt_binding.bound`: the scaffold receipt revalidates with its recorded
  sha256, and its after-tree (inputs plus outputs) equals the first captured candidate
  (`campaign/runs/1/candidate.json`, or the empty checks-only run on `--dry-run`) on
  `project/`, `generation/` and `requirements/`, in both directions. A file that differs, is
  missing from the capture, or is in the capture but not in the receipt (for example a
  planted `project/rung1_rc.kicad_dru`) leaves it unbound, as do a missing receipt and a
  missing capture. A record of a campaign from before the scaffold receipt existed is
  therefore never `done` when it is rebuilt. New configurations declare this receipt under
  `pre_receipts` with the generic binding `{"pre_receipt": 1}` and the relative source path
  `scaffold-receipt/receipt.json`. Campaign start copies it into `campaign/pre-receipts/1/`;
  `verify-campaign` checks that copy and its exact first-capture binding. The rung 1 record
  uses the same copy, without falling back to the original if it is missing or invalid.
  Dry runs check the original receipt. Older campaigns without a pre-receipt declaration
  retain the record-only check.
- The final state lists the `agent-ee.stage-done/1` binding that verify-campaign
  recomputed (`stage_receipts_done.bound`). The schematic must be `emit-schematic`'s
  output, and the board must have passed through `build-board` and then `route-krt`
  with no writer edit after either
  ([done through declared steps](stage-receipts.md#done-through-declared-steps)).

The `export-fab` step's stage `fab` is not a declared scope stage, so a staged
campaign's step plan never selects it; the done gate is the `fab` check. The
stage-step binding lists it under `excluded`. The
step remains in the configuration for `--dry-run` rehearsals.

The `export-fab` step runs the shared receipted fab stage (`agent_ee.fab_stage`):
`fab_export` then `cpl_rotation` in the networkless sandbox, with a derived
corrected package. Without `--cpl-inputs` it refuses (exit 3, policy not
captured) and its stage `fab` is not a declared scope stage, so a staged
campaign's step plan never selects it; with the capture the `fab` stage is
declared and the step may be selected for it. In both cases the done gate is the
`fab` check, and the step remains in the configuration for `--dry-run` rehearsals.

## Recorded run (2026-10-17)

This run predates the `fab` done gate.

One real run used commit 02d74cf from a clean clone, and a kicad-route
snapshot at 08179cd (KRT v0.23.1). The run stopped early with the state
`incomplete`, so `done` is false.
That run predates the `write-scaffold` step: its scaffold was written by the driver
itself and has no stage receipt, so a rebuilt record of it stays unbound and not `done`.
It also predates the shared builder (AGENTEE-134): its empty board kept the fixture
board's setup and had no stackup, so its candidate bytes are not reproduced by the
current driver. The empty-candidate outcome of its iteration 1 (`stage_not_reached`
for `erc`, `netlist`, `drc` and `geometry`, `stage_blocked` for `rc-step`) is
reproduced natively on the current scaffold by
`test_checks_only_review_of_the_empty_candidate_witnesses_stage_absence`.

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
- The `export-fab` step is provenance only; acceptance uses the `fab_package`
  done gate (AGENTEE-94), which runs the AGENTEE-23 export and AGENTEE-73 gate
  and, with `--cpl-inputs`, seals the AGENTEE-126 corrected CPL. The owned
  exporter still refuses the generated hand-soldered non-DNP header without an
  LCSC binding unless it carries the declared assembly exclusion (AGENTEE-122).
- The recorded run below predates the done gate; its scope had no `fab` check.
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

The tests check that the scaffold is valid, deterministic and byte-equal to
`scaffold.native_files` on the captured envelope, that an unbuildable envelope exits 3, that its files are the adopted
outputs of a valid `write-scaffold` receipt, that a failing scaffold step keeps its receipt and
writes no project, and that the scaffold binding detects a changed, missing or planted extra file. They check that its tables
equal the emitter's and its project file is the same JSON as the emitter's, and that the scope validates with the
real loaders. They check that widened write authority, an adoptable exit 3 and
reserved mount points are refused. With the native toolchain, they check that
kicad-cli loads the empty files and that a checks-only review of the empty
candidate gives `stage_not_reached` for `erc`, `netlist`, `drc` and `geometry`
and `stage_blocked` for `rc-step`. No test runs a campaign or inference.

## Review conventions file

The scaffold adds `requirements/review-conventions.md` and lists it in the scope's
`requirements`. It restates, for the independent reviewers and the adjudicator, how the
coordinator validates citations and witnesses (`baseline.json` is citable after
immutable-baseline verification; other allowed paths remain explicit; witnesses cite unchanged check results). It changes no
check, limit or acceptance rule. It was added after two campaign attempts on 2026-10-17 were
recorded incomplete because an otherwise valid adjudication cited `baseline.json` and a
reviewer witness was malformed (AGENTEE-111, AGENTEE-115). The default `max_iterations` for
this runner is 2: the empty candidate and one generated candidate.

Receipted Python steps mount a [content-addressed source snapshot](stage-receipts.md#python-tool-source-snapshots), separate from host import caches. A refused fab step reports its violations and retained receipt path; mounted-file mutation still refuses adoption.

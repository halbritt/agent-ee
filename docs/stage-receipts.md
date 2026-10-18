# Stage receipts

A stage receipt records one writer-side generation step that the coordinator ran
itself: the exact argv, the tool identity, the input and output file hashes, the exit
status, the timing and the sandbox profile. Receipts are provenance (decisions 5 and 6 in
[implementation decisions](decisions.md#generator-direction-2026-10-17)).
A receipt never makes a check pass. Acceptance still comes only from fresh
sealed checks of the captured proposal. The module is
[stage_receipts.py](../src/agent_ee/stage_receipts.py) (AGENTEE-95).

## Authority

Only the coordinator creates a receipt. It copies the design, runs the
declared step through the ordinary executor sandbox (Bubblewrap with
`--unshare-net`, a read-only `/candidate` and one writable `/work`), and hashes the result
itself. Campaign receipts live in `implementations/N/receipts/`. That directory is outside
the implementation writer's writable mount, `review-work/implementation`. Writer prose
and writer-made JSON are never read as receipts, wherever the writer puts them.
Neither is a tool report the writer copied. The validator recomputes every derivable
field from the retained coordinator records. Changing a claimed output hash, filling in a
version, removing network isolation or relabelling a failed exit is refused,
even when the forger also recomputes the receipt identity.

## Step declaration

```json
{"schema": "agent-ee.stage-step/1", "stage": "placement", "step": "pack-helper",
 "argv": ["/usr/bin/python3", "/tool/pack.py"],
 "mounts": [{"source": "/opt/pack-helper", "at": "/tool"}], "source": "/tool",
 "version_argv": ["/usr/bin/python3", "/tool/pack.py", "--version"],
 "writes": {"project/board.kicad_pcb": "design/project/board.kicad_pcb"},
 "artifacts": [], "adoptable_exits": [0], "timeout_s": 120}
```

| Field | Rule |
| --- | --- |
| `stage` | `scaffold`, `parts`, `schematic`, `placement`, `routing`, `verification` or `fab` |
| `argv` | The executable must be under a declared mount or a read-only system path (`/usr`, `/bin`, `/sbin`, `/lib`, `/lib64`). Never `/work` or `/candidate` |
| `mounts` | Read-only binds; `at` cannot overlap another mount or any sandbox system path |
| `source` | Optional mount path whose file tree identifies a Python tool |
| `version_argv` | Optional version probe, run in the same sandbox |
| `backend_report` | Optional `{artifact, key}`: copy that key of a declared JSON artifact verbatim. For kicad-route, `result.json` key `backend` |
| `writes` | Candidate path → work path that becomes it. In-place writes use `design/<same path>`. Out-of-place writes name a produced file such as `out/candidate/...` |
| `artifacts` | Work paths retained and hashed, for example `out/result.json`, `out/validate` |
| `adoptable_exits` | Must include 0. kicad-route's partial-but-valid exit 2 would be listed here. A failed validation (3) never is |

Native `pcbnew.SaveBoard` can also rewrite sibling `.kicad_pro`/`.kicad_prl`
files. The runner refuses undeclared changes in `run/design`; it does not
restore project siblings automatically. A board-saving wrapper must restore
their captured bytes and inventory before exiting, or write to a staging copy
outside `run/design` and declare only the staged board in `writes`. Follow the
[campaign writer rule](campaign-guide.md#saving-boards-without-changing-project-siblings)
without broadening the campaign's mutable inputs. Receipts remain provenance.

## Receipt fields and status

Schema `agent-ee.stage-receipt/1`, `receipts/<k>-<step>/receipt.json`, retained with
`inputs/` (the pre-step design, mounted at `/candidate`), `run/` (executor records, the
writable `design/` copy and declared outputs) and `version/`.

- `tool.executable`: sandbox path, resolved host path and sha256, observed before the step.
  `tool_after` repeats the observation after the step. A change, or a missing executable, adds the violation `tool_changed`.
  Mount trees and the Python source tree are compared the same way.
- `tool.reported_version`: `reported` with the probe's first output line, or `unknown` with
  `value: null` and a reason. The reasons are: no probe declared, a failing probe, empty output, output
  that is not UTF-8 or larger than 1 MB, or output that is not a regular file. An unknown identity is never filled in.
- `tool.backend`: the declared report key copied verbatim. A `null` backend version stays `null`.
  The report is parsed as strict JSON. Any of the following records the backend as `unknown` with a
  specific reason: a missing, linked or special file, more than 1 MB, invalid UTF-8, a parse error,
  duplicate keys, `NaN` or `Infinity` (including float overflow such as `1e400`), nesting deeper than
  64, or an integer beyond the interpreter limit. No backend is guessed, and the process receipt
  is still written. A malformed report alone does not change the step status: the backend is provenance.
- `inputs` and `outputs`: sha256 by candidate path. `changed` lists outputs whose bytes differ.
- `violations`: `inventory_changed`, `write_outside_scope`, `missing_output`, `unsafe_output`
  (the write source is behind a link or special entry), `executor_record_unsafe:<dir>/<file>` (an
  executor record in `run/` or `version/` is a link or special file), `executor_record_missing:<dir>`
  (the executor was entered but no trusted record exists), `tool_changed`, `mount_changed` and `source_changed`.
- `status`, recomputed from the recorded execution and violations:

| Status | Meaning | Adoptable |
| --- | --- | --- |
| `completed` | exit 0, no violation | yes |
| `partial` | exit in `adoptable_exits` other than 0, no violation | yes |
| `failed` | another exit, including a crash or a missing executable | no |
| `timeout`, `unavailable` | executor result | no |
| `cancelled` | interrupted; partial outputs are hashed and the receipt is written before the interrupt propagates | no |
| `refused` | an adoptable exit with a violation, or no trusted executor record together with an `executor_record_*` violation | no |

## Links and special files planted by the tool

The tool can create links, FIFOs and other entries anywhere in its writable `/work`. A link
target string is resolved differently in the coordinator's namespace: `/work/link -> /home/...` dangles inside the
sandbox but would name a real host directory outside it. The coordinator therefore never resolves
a path below the run directory by name.

- Every lookup of a write source, artifact, backend report or executor record opens one component
  at a time, relative to its parent, without following links. The leaf must be a regular file.
  A link or special entry at any component is reported, and its target is never read or hashed.
- A write source behind a link is `unsafe_output`, so the step is `refused`. A declared artifact, or an entry
  inside a declared artifact directory, behind a link or special entry is recorded as `null`. Artifact
  links are allowed, for example kicad-route's `latest` link, so they are not violations.
- The design copy, the retained inputs and artifact directories are walked through directory
  descriptors, iteratively. Directories deeper than 128 levels are recorded as special entries rather
  than entered. A link or special entry in the design copy is an `inventory_changed` violation.
- `adopt` first revalidates the retained receipt and refuses any receipt object that differs from it.
  A writer-supplied or edited copy cannot authorize adoption. It then reads each output without following
  links, checks the sha256 of the bytes it read, and writes those same bytes beneath the design, again
  without following links. A replaced output or ancestor is refused even when its bytes match.
- The executor writes `stdout.log`, `stderr.log`, `console.log` and `execution.json` into `/work`
  after the tool exits, without following or writing through any planted entry
  ([security](security.md#candidate-permissions)). An entry the tool created or changed at one of those
  names is moved aside and listed in the record's `displaced_records`. That, or a record that is later a
  link or special file, is `executor_record_unsafe` and is never read through.
  If the executor cannot write its records (for example the tool left `/work` unwritable), or an interrupt
  arrives before it does, no record on disk is trusted. The receipt is kept with `execution: null`,
  `executor_record_missing` and status `refused`. A non-interrupt executor error is then re-raised. A
  record left on disk after an interrupt is used only if it is a well-formed `interrupted` record, so at most it gives
  `cancelled`.

Tool descendants do not outlive the step. Bubblewrap's PID namespace is torn down when the step
exits or is killed on timeout or cancellation, so nothing in the sandbox can swap a path while the
coordinator reads it. The descriptor-relative reads and the hash-then-write adoption do not depend on that.

## Exact applicability

A receipt's after-tree is its `inputs` with its `outputs` substituted. Against the
delivered design, each receipt is classified as follows:

- `applicable`: the delivered tree equals the after-tree exactly, every file included, not only the outputs.
- `superseded`: a later receipt in the same index consumed exactly this after-tree and is itself
  applicable or superseded, so no writer edit came between the two steps.
- `not_applicable`: any other relation. The index lists the differing paths. A later writer edit to the board
  therefore makes a routing receipt inapplicable, as AGENTEE-93 requires. An unadoptable status
  is never applicable.

## Campaign integration

`agent-ee.campaign/1` accepts an optional `implementation_steps` list of step
declarations. Every declaration is validated before the campaign directory is created, and the list is part of the
hashed `config.json`. Each implementation runs its own step plan (`agent-ee.step-plan/1`),
which the coordinator derives from the frozen configuration and the verified source run.
In a staged campaign the plan holds only the steps of the earliest failing stage, followed by the
[placement adapter](placement-adapter.md) on placement turns. Unstaged campaigns, and campaigns
created before the plan existed, run every declared step as before. Receipt indexes follow the
plan's order. After the writer session, the coordinator first applies the existing
proposal checks to the writer's design. It then runs each planned step on that design, and checks that every write target
is in `mutable_inputs` before launch. An adoptable step's exact outputs are copied back only
while the design still equals the receipt's inputs. An unadoptable step stops the repair as
`incomplete`. No default output is substituted and no proposal is captured. Afterwards the
existing inventory, immutable-input, baseline and adapter checks run again.

Every implementation in a new campaign writes `implementations/N/stage-receipts.json`
(`agent-ee.stage-receipt-index/1`), even when it holds no receipts. The index lists every receipt with its
sha256 and applicability, together with the delivered design tree. Its digest is anchored in
`state.json["stage_receipts"]`. `verify-campaign` refuses the campaign in these cases:

- a missing, changed or unanchored index, or a receipt directory that is not indexed;
- a receipt that fails validation, is bound to a different implementation or source candidate, or
  does not match the step that the re-derived plan puts at its index;
- a delivered proposal without a receipt for every planned step;
- recorded applicability that differs from the recomputed applicability;
- a delivered tree that differs from the next run's captured proposal. A receipt that claims to apply
  while its outputs differ from the captured proposal fails with that message.

Campaigns created before this change have no anchors and no index, and they verify as before.

## Limits

- Steps run after the writer session, on its delivered design. A tool the writer runs inside its
  own sandbox produces no receipt. Its effect counts only after capture, as before.
- Mount and source tree identities are observations at execution time. A later host
  change is not rechecked against retained bytes, because tool trees are not copied.
- The [routing adapter](routing-adapter.md) runs declared kicad-route steps through
  the campaign writer, with routing-specific verification before adoption and on replay.
  Exit 2 is a partial proposal requiring fresh checks; exits 1, 3 and 4 stop the
  implementation without a proposal. The AGENTEE-54 placer runs through the
  [placement adapter](placement-adapter.md). Generic step tests use an original test program.
- A completed receipt with all writes present but missing declared artifacts or an unknown backend is still
  adoptable. A tool adapter such as AGENTEE-93 must require its own result and validation artifacts, and
  the qualified statuses, before it adopts a proposal. `adoptable_exits` does not encode a tool's exit semantics.

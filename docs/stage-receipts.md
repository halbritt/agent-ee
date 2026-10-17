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

## Receipt fields and status

Schema `agent-ee.stage-receipt/1`, `receipts/<k>-<step>/receipt.json`, retained with
`inputs/` (the pre-step design, mounted at `/candidate`), `run/` (executor records, the
writable `design/` copy and declared outputs) and `version/`.

- `tool.executable`: sandbox path, resolved host path and sha256, observed before the step.
  `tool_after` repeats the observation after the step. A change, or a missing executable, adds the violation `tool_changed`.
  Mount trees and the Python source tree are compared the same way.
- `tool.reported_version`: `reported` with the probe's first output line, or `unknown` with
  `value: null` and a reason (no probe declared, a failing probe, or empty output). An unknown
  identity is never filled in.
- `tool.backend`: the declared report key copied verbatim. A `null` backend version stays `null`.
- `inputs` and `outputs`: sha256 by candidate path. `changed` lists outputs whose bytes differ.
- `violations`: `inventory_changed`, `write_outside_scope`, `missing_output`, `tool_changed`,
  `mount_changed` and `source_changed`.
- `status`, recomputed from the recorded execution and violations:

| Status | Meaning | Adoptable |
| --- | --- | --- |
| `completed` | exit 0, no violation | yes |
| `partial` | exit in `adoptable_exits` other than 0, no violation | yes |
| `failed` | another exit, including a crash or a missing executable | no |
| `timeout`, `unavailable` | executor result | no |
| `cancelled` | interrupted; partial outputs are hashed and the receipt is written before the interrupt propagates | no |
| `refused` | an adoptable exit with a violation | no |

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
hashed `config.json`. After the writer session, the coordinator first applies the existing
proposal checks to the writer's design. It then runs each step on that design, and checks that every write target
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
  does not match the campaign's declared step;
- recorded applicability that differs from the recomputed applicability;
- a delivered tree that differs from the next run's captured proposal. A receipt that claims to apply
  while its outputs differ from the captured proposal fails with that message.

Campaigns created before this change have no anchors and no index, and they verify as before.

## Limits

- Steps run after the writer session, on its delivered design. A tool the writer runs inside its
  own sandbox produces no receipt. Its effect counts only after capture, as before.
- Mount and source tree identities are observations at execution time. A later host
  change is not rechecked against retained bytes, because tool trees are not copied.
- No production scaffold (AGENTEE-87), placement helper (AGENTEE-54/55) or kicad-route
  attempt (AGENTEE-93) is wired yet. The tests use an original test step program. The
  rung 1 record (AGENTEE-96) still needs real receipts for those three steps.
- Whether kicad-route exits 1 and 4 should leave the writer's unrouted board as the proposal, rather than
  stopping the repair, is a decision for AGENTEE-93 and AGENTEE-88.

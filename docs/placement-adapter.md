# Placement campaign adapter (AGENTEE-91)

`placement_adapter` is the campaign-side part of the placement stage. It runs the
existing [initial placer](placer.md) as a coordinator step and limits what a
placement iteration may change on the board. It adds no second placer, campaign,
writer or acceptance path. The campaign wiring belongs to the integrator and is
not yet in `campaign.py`. Until then, this module is a tested library seam.

## Declaration and dispatch

The campaign configuration key `placement_adapter` holds an
`agent-ee.placement-adapter/1` declaration. It is part of the hashed `config.json`:

```json
{"schema": "agent-ee.placement-adapter/1",
 "helper": "agent-ee.placer/1",
 "policy": "project/placement-policy.json",
 "fixed_footprints": ["22222222-2222-4222-8222-222222222222"],
 "timeout_s": 600}
```

- `helper` is either `agent-ee.placer/1` or `null`. With `null`, only the writer
  edits the board.
- `policy` must be a captured `agent-ee.packing-policy/1` file outside
  `mutable_inputs`.
- `fixed_footprints` lists native footprint UUIDs that the envelope fixes. The
  envelope record should supply them once it is frozen. The adapter infers no
  connector role.

The protected set is the union of three groups: the declared UUIDs, the policy's
`fixed_refs` resolved on the source board, and footprints locked on the source
board.

`is_placement_iteration` is true only when two conditions hold. The scope
declares `stages`, and the earliest failing stage recorded in the history is
`placement`. Other iterations and unstaged scopes are unaffected.
`writer_context` gives the writer the sealed placement-stage check results and
their accepted findings. It also includes the retained `placement-probe.json`,
but only when that file's hash matches `result.json` `artifact_hashes`. Routing
feedback is reported as `unavailable` until a captured routed-attempt receipt
exists. This context is prompt data, not evidence.

## Helper step

`run_placement_iteration` runs after the writer session and the existing
inventory checks. It runs inside the campaign's existing writer lease and takes
no other campaign lease. When `helper` is set, it runs exactly one
stage-receipt step (AGENTEE-95, `stage_receipts.run_step`). That module is not yet
on this branch. The step's sandbox view is:

- `/candidate`: the receipt's read-only copy of the delivered design. The
  source board is therefore outside the writable root.
- `/placement-tool`: a coordinator copy of the `agent_ee` sources plus a constant
  launcher. Python runs with `-I`, so this hashed tree, rather than `/app`,
  supplies the helper code. The receipt records the tree as the step's source
  identity.
- `/placement-request/request.json`: a request derived by the coordinator. It
  contains `agent-ee.placement-write/1` with `single_design_writer: true`, the
  exact delivered board SHA-256 and `writable_root: /work/placement`. Its policy is
  the captured policy with every protected reference added to `fixed_refs`.

Inside the sandbox, the driver exports native KiCad XML from
`/candidate/<schematic>`. It then calls the unchanged `placer_runner.main`, with
the fresh destination `/work/placement/placed`. The step declares the following:

- writes: `{<board>: "placement/placed/candidate.kicad_pcb"}`
- artifacts: `placement`
- adoptable exits: `[0]`
- version probe: `kicad-cli version`
- network: off

A proposal is adopted only if all of these hold:

- the receipt is `completed`;
- `placement-result.json` reports `placed`, with `authority:
  disposable-unsealed-proposal`;
- its source and board hashes equal the receipt's input and output board hashes;
- the write-scope proof below passes for that exact output.

Any other outcome raises an error and leaves the iteration incomplete. That
includes a helper exit 1 (typed packing failure or crash), a netlist export
failure (driver exit 3), a timeout, an exhausted deadline, a cancellation, a
missing output or a scope refusal. The receipt and receipt index remain
anchored. Neither the original board nor any default placement is substituted.

A successful native netlist export does not prove that the netlist is
populated. KiCad exports an empty netlist from a parseable stub schematic. The
placer's inventory binding then refuses it.

## Placement write scope

`write_scope(before, after, protected)` compares two complete saved
`.kicad_pcb` parse trees, using the shared bounded S-expression parser. It allows
only the following changes:

- a footprint `at` translation;
- a footprint rotation delta of 0, 90, 180 or 270 degrees, modulo 360;
- the same delta, modulo 360, added to the `at` angle of that footprint's `pad`,
  `property` and `fp_text` children. Their local x/y and every other token must
  stay equal. KiCad 10.0.7 stores those angles as footprint plus local angle, and
  the native tests check actual pcbnew output;
- a reordering of a footprint's UUID-keyed children, which KiCad re-sorts
  graphics into on save.

Every other change is refused with a specific violation:

- a change, addition, removal or reordering of a board item outside footprints.
  This covers the header, setup and rules, the outline, zones, tracks, vias,
  nets and groups;
- a footprint added, removed or given a duplicate UUID;
- a changed library identity, layer or side. Side flips are refused as
  unsupported until a mirrored-child rule is qualified;
- a rotation delta that is not a quarter turn;
- any `at` token change on a protected footprint;
- a pad position, size, net, number or UUID change;
- a field, model, schematic path or graphic change;
- a lock-state change;
- a footprint-owned zone that would move with its footprint.

`proposal_scope` additionally requires every non-board file to keep its exact
bytes and inventory. In a placement iteration, it compares the delivered design
with the source candidate's captured inputs. It therefore covers the writer's own
edits as well as the helper's output.

The shared parser does not keep the difference between quoted and unquoted
atoms. Native reload and the fresh checks remain the authority on how KiCad
parses the bytes.

## Records and authority

`implementations/N/placement-adapter.json` (`agent-ee.placement-adapter-record/1`)
records the following:

- the declaration identity and the protected UUIDs;
- the request identity, receipt path, ID, status and digest;
- the helper result digest, the step's scope proof and the proposal scope proof;
- `authority: provenance-only`.

`verify_record` recomputes a proposed record from three sources: the retained
receipt (through `stage_receipts.validate_receipt`), the request, and the
captured proposal inputs. Any difference is refused.

The helper's local judgment, its native courtyard DRC and its `placed` status
describe a disposable proposal only. Grades come only from the next run's fresh
captured checks of the captured candidate, which include native DRC and the
placement check. Reviewers and adjudication follow as before.

## Qualification and open obligations

The tests use owned boards and actual tools:

- KiCad 10.0.7 pcbnew edits;
- `kicad-cli` netlist export of the owned RC schematic;
- the production networkless executor;
- the AGENTEE-95 receipt module.

They check that quarter turns are accepted, and that edits outside the scope,
fixed-connector moves, helper failures, cancellation and an exhausted deadline
are refused. The rectangular placeholder footprints are not production models.

These obligations remain open:

- The real RC fixture footprints use roundrect and circle pads. The unchanged
  initial packer reports `unsupported_geometry` for them, so the real RC ladder
  needs pad-shape qualification in AGENTEE-54 or AGENTEE-55. The structural
  scope proof itself accepts transforms of those real footprints.
- AGENTEE-91 closure needs an actual RC campaign. It must run with forward
  annotation (AGENTEE-52), the registered placement check (AGENTEE-92), staged
  gates (AGENTEE-48/88), merged stage receipts (AGENTEE-95) and integrator-owned
  `campaign.py` wiring. Standalone helper tests are not that proof.
- Side changes, writer-selected helper invocation and routed-attempt feedback
  are not supported yet.

# Placement campaign adapter (AGENTEE-91)

`placement_adapter` is the placement stage of the existing repair campaign. When
the earliest failing stage is placement, the one implementation writer proposes
footprint transforms. The coordinator can then run the pinned
[initial placer](placer.md) once as a receipted step. Before the next capture, it
limits what the iteration may change on the board. The adapter adds no second
placer, campaign, writer or acceptance path. Fresh captured checks grade every
proposal.

## Declaration and admission

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
  edits the board. This is a writer-only placement mode, not a skipped helper.
- `policy` must be a captured `agent-ee.packing-policy/1` file outside
  `mutable_inputs`. It is required with a helper and refused without one.
- `fixed_footprints` lists native footprint UUIDs that must not move. The list
  may be incomplete: envelope-fixed parts are protected anyway (see below).

`run_campaign` validates the declaration before it creates the campaign
directory, so before any provider call. It refuses these cases:

- a malformed declaration;
- a scope without an `agent-ee.stages/1` declaration that includes `placement`;
- the adapter combined with `implementation_steps` of stage `placement`, so that
  one placement helper never runs twice in an iteration.

When the first candidate has been captured, the coordinator binds the declaration
to that candidate and stores the binding in `state.json["placement_binding"]`
(`agent-ee.placement-binding/1`). The binding holds the declaration identity, the
board and schematic paths, the policy path and digest, and every envelope that
a `placement` check consumes, with its digest and fixed references. The binding
is refused if the policy or an envelope is a mutable input, if a captured digest
does not match, or if any declared UUID or fixed reference does not resolve to
exactly one source footprint. Every later iteration re-derives the binding from
its own source candidate and refuses any drift. The reviews of the first run
happen before the binding, but no writer runs before it.

### Protected footprints

The protected set is the union of four groups, all resolved on the captured
source board:

- the declared `fixed_footprints` UUIDs;
- the policy's `fixed_refs`;
- the `fixed_footprints[].reference` of every captured envelope named by a
  `placement` check's `envelope` parameter, read from the envelope bytes with the
  [AGENTEE-85 validator](board-envelope.md);
- footprints locked on the source board.

A missing or ambiguous reference refuses the binding. It never shrinks the set.
A writer cannot unlock a footprint, edit the policy or envelope, or drop a fixed
reference before the set is computed, because all three come from the source
candidate, which is immutable. The envelope is consumed here under the existing
placement-check semantics. This is not the full AGENTEE-85 normative baseline
integration, which remains separate.

## Step plan and dispatch

The coordinator derives one ordered step plan for each implementation
(`agent-ee.step-plan/1`). Its only inputs are the frozen configuration, the
source candidate and the verified history record of the source run. The writer
cannot change it.

| Campaign | Plan |
| --- | --- |
| Staged scope | `implementation_steps` whose stage equals the earliest failing stage, then the placement adapter when that stage is `placement` and the adapter is declared |
| Unstaged scope | Every `implementation_steps` entry, as before; the adapter is refused |
| Created before the plan (no `state["step_plan"]`) | Every `implementation_steps` entry, as before; the adapter is refused |

Receipt indexes come from the same plan, in order. A routing-stage step therefore
never runs on a placement turn, and the placer never runs on a schematic or
routing turn. Older campaigns keep their historical meaning and verify as
before. To use the adapter, start a fresh campaign.

## Placement iteration sequence

On a placement turn, `campaign.implement` does the following, under the
existing campaign and source-run writer leases:

1. The previous run and the campaign have already been verified; the earliest
   failing stage comes from that verified history. The writer prompt gains
   `writer_context`: the sealed placement-stage check results, their accepted
   findings, the retained `placement-probe.json` when its hash matches
   `result.json`, the protected references and the supported transform family.
   A finding is included only when its `id` is `check/<local_id>` for a
   placement-stage check. Routing feedback is reported as `unavailable` until a
   captured AGENTEE-93 routed-attempt receipt exists. The context is prompt
   data, not evidence.
2. The single writer runs under the existing preflight and operation deadline.
   A failed call leaves the campaign incomplete, with no retry or fallback. The
   existing inventory and immutable-input checks run on its result.
3. `run_placement_iteration` holds the writer's delivered design to the
   placement write scope against the source candidate (`writer_scope`), before
   any helper runs. A helper therefore cannot overwrite an illegal writer edit
   and conceal it.
4. With a helper, exactly one AGENTEE-95 step runs (described below), and only a
   qualified result is adopted.
5. The final design is compared with the original source inputs again
   (`proposal_scope`). The existing proposal checks run once more, and only then
   is `proposal.scope.json` written.

The next run captures the proposal and runs the ordinary checks: the
placement-stage DRC projection, the placement checks, later eligible checks,
independent initial reviews and adjudication. The adapter's `proposed` status
never proves a placement pass. The existing stage convergence decides whether to
repair again or stop.

Any refusal, helper failure, timeout, cancellation or invalid result leaves the
iteration incomplete. The retained record keeps the original exception. If the
record itself cannot be written, the original exception is still reported, and
the missing record is anchored as `null`.

## Helper step

When `helper` is set, the coordinator runs one stage-receipt step
(`stage_receipts.run_step`) as receipt `receipts/<index>-agent-ee-placer`, where
the index comes from the step plan. The step's sandbox view is:

- `/candidate`: the receipt's read-only copy of the delivered design. The
  source board is therefore outside the writable root.
- `/placement-tool`: a coordinator copy of the `agent_ee` sources plus a constant
  launcher. Python runs with `-I`, so this hashed tree, rather than `/app`,
  supplies the helper code. The receipt records the tree as the step's source
  identity. The snapshot must equal the campaign's pinned `agent_ee` sources
  (the launch policy files) before the step runs, and verification checks the
  retained snapshot against both the pin and the receipt.
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
`.kicad_pcb` parse trees, using the shared bounded S-expression parser. The
report is `agent-ee.placement-write-scope/2`, and its transform family is
`agent-ee.placement-transforms/2`. Each transform records `rotation_deg`,
`flipped` and the resulting `side`. Only the following footprint changes are
allowed:

- a footprint `at` translation;
- a footprint rotation delta of 0, 90, 180 or 270 degrees, modulo 360;
- the same delta, modulo 360, added to the `at` angle of that footprint's `pad`,
  `property` and `fp_text` children. Their local x/y and every other token must
  stay equal. KiCad 10.0.7 stores those angles as footprint plus local angle, and
  the native tests check actual pcbnew output;
- a reordering of a footprint's UUID-keyed children, which KiCad re-sorts
  graphics into on save;
- a front/back side flip, as described in the next section.

Every other change is refused with a specific violation:

- a change, addition, removal or reordering of a board item outside footprints.
  This covers the header, setup and rules, the outline, zones, tracks, vias,
  nets and groups;
- a footprint added, removed or given a duplicate UUID;
- a changed library identity, or a layer change other than a front/back flip;
- a rotation delta that is not a quarter turn;
- any `at` token change or side flip of a protected footprint;
- a pad position, size, net, number or UUID change;
- a field, model, schematic path or graphic change;
- a lock-state change;
- a footprint-owned zone that would move with its footprint.

`proposal_scope` additionally requires every non-board file to keep its exact
bytes and inventory. File-level `mutable_inputs` is therefore not enough: a
schematic listed as mutable is still refused in a placement iteration.

### Side flips

A side flip is accepted only in the exact form that KiCad 10.0.7
`FOOTPRINT::Flip` saves. The native tests check both flip directions, from every
quarter turn, followed by a move, a further quarter turn and a flip back:

- the footprint `layer` swaps between `F.Cu` and `B.Cu`;
- the saved footprint angle is negated, and what remains must be a quarter turn.
  A top-bottom flip leaves 0 degrees, a left-right flip 180 degrees;
- every footprint-local point (`start`, `end`, `center`, polygon `xy`, and the
  local x/y of `at`) keeps x and negates y exactly;
- every `F.*`/`B.*` layer of `Cu`, `Adhes`, `Paste`, `SilkS`, `Mask`, `CrtYd` and
  `Fab` swaps. `*.Cu` and `*.Mask` stay. Any other layer refuses the flip;
- a pad angle `a` becomes `θ' - (a - θ)`, where θ and θ' are the footprint angles
  before and after: the local pad angle is negated;
- a text angle `a` becomes `180 - a + turn`, and `mirror` toggles in its
  justification. KiCad may also keep a flipped text upright: a further half turn
  with `left` and `right` swapped is accepted, and nothing else;
- 3D models and every other footprint token are unchanged.

A flip of a footprint outside this qualified family is refused as unsupported:

- `fp_arc`: pcbnew re-derives arcs on a flip, swapping their endpoints and
  recomputing the midpoint;
- custom (`primitives`/`options`), chamfered, trapezoid (`rect_delta`) or
  offset-drill pads;
- text with `top` or `bottom` justification;
- any other keyed child kind.

Arbitrary orientations are not supported. A footprint saved at a non-quarter
angle may be translated, turned by a quarter turn or flipped, but the change
relative to its source must always be a qualified transform.

### Exact angles

Angles are compared exactly. Each saved angle token is scaled to an integer
number of 10^-32 degree units, without any Decimal context, and turns are
compared with integer arithmetic modulo 360. An angle that needs more than 32
digits before or after the decimal point is refused as not exactly
representable, and is never rounded. Only plain decimal and exponent forms,
such as `-90`, `12.5` or `1e-29`, are read as numbers. Any other token, for
example `nan`, `9_0` or a quoted ` 90`, is kept as an exact extra position
token, so adding or changing one is refused. Python's `Decimal` alone would read
`9_0` as 90, which KiCad does not. This applies to the footprint angle and to the child angles of a
turned footprint. An unchanged footprint whose saved angle cannot be held
exactly is refused too, so a source board with such an angle refuses every
placement iteration. pcbnew 10.0.7 saves tiny turns in exponent form, for
example `(at 15.5 15 1e-29)`. The native tests check that such a turn, and a
`1e-40` turn, are refused on both board sides.

The shared parser does not keep the difference between quoted and unquoted
atoms. Native reload and the fresh checks remain the authority on how KiCad
parses the bytes.

## Records, anchors and verification

`implementations/N/placement-adapter.json` (`agent-ee.placement-adapter-record/2`)
records the following:

- the declaration and binding identities, and the protected UUIDs and references;
- the request identity, receipt path, ID, status and digest;
- the helper result digest and the step's scope proof;
- the writer's pre-helper scope proof (`writer_scope`) and the final proposal
  scope proof;
- `status` (`proposed`, `refused`, `incomplete` or `cancelled`), the error, and
  `authority: provenance-only`.

Its digest is anchored in `state.json["placement_records"]`, keyed by
implementation, including for failed iterations. `verify-campaign` re-derives the
step plan of every implementation from the history it has just verified against
evidence, then does the following:

- It passes each implementation's expected receipt declarations to the stage
  receipt verifier. A delivered proposal must hold a receipt for every planned
  step; an incomplete one may hold a prefix.
- It refuses a placement record or anchor on an implementation whose plan has no
  placement step, a missing or changed record, and a binding that differs from the
  first captured candidate or drifts later.
- For a `proposed` record, `verify_record` rebuilds every field from the retained
  receipt, the re-derived request, the source inputs and the captured next
  candidate. It copies no field from the stored record. Both scope proofs must be
  `allowed`. With a helper, the captured proposal must be exactly the adopted
  helper output over the writer's delivered design. When no next run exists, the
  coordinator's scratch design stands in for it.
- For an incomplete last iteration, `verify_attempt` checks the record without
  asserting adoption or completion. It recomputes the binding, the protected set,
  any helper receipt and request, and each scope proof from the retained records
  and the unchanged scratch design. A refused status needs a refused scope proof;
  a cancelled status needs the `KeyboardInterrupt` error. An attempt whose final
  scope is allowed is not accepted as incomplete.

The helper's local judgment, its native courtyard DRC and its `placed` status
describe a disposable proposal only. Grades come only from the next run's fresh
captured checks of the captured candidate.

## Qualification and open obligations

The AGENTEE-91 orchestration scopes consume no board envelope. A scope that
does must declare it under the AGENTEE-85 contract and bind every derived
obligation. The envelope-binding scope maps `connector-edge/1` to the
fixed-footprint obligation and native `board_envelope` `outline/1` and
`layers/1` checks to the outline and layer-count obligations; those bind. Its
`stackup/1` check cannot bind until the AGENTEE-94 fab-profile seam supplies a
qualified captured `copper_weight_oz` authority, so capture refuses with
`R-envelope-rc-filter-stackup` as the only unbound obligation and no candidate is
published. The envelope-fixed connector, the envelope reference missing from the
board and the envelope declared mutable assert exactly that refusal; their
binding behavior is unreachable through a campaign until AGENTEE-94. The envelope
authority guard is neither bypassed nor replaced by test bindings.

A campaign whose run captured no candidate records that run's own `result.json`
error (`run N captured no candidate: ...`), not a missing-manifest error.

The adapter tests use owned boards and actual tools: KiCad 10.0.7 pcbnew edits
and flips, `kicad-cli` netlist export, the production networkless executor and
the AGENTEE-95 receipt module.

`tests/test_placement_campaign.py` runs the campaign with real capture,
sandboxed checks, stage gates, receipts and the pinned placer. Only the agent
transport is scripted, so these tests do not show live-model repair quality.
Project-local footprint libraries are rebuilt from the stock KiCad footprints at
test time and are not committed. The graded checks are the placement-stage DRC
projection, `courtyard-overlap/1`, `outline-containment/1` and a routing-stage
DRC. J1 is protected by its declared UUID.

**Forward-annotated scaffold.** The starting board is an actual AGENTEE-52
forward annotation. The RC schematic's native netlist is annotated onto the RC
outline after every footprint, track and zone has been removed. R1, C1 and J1
arrive with stock pads on the staging column outside the outline. The tests
show the following:

- The first run grades the scaffold as an `outline` defect, with all three parts
  outside the outline. The writer prompt receives that sealed finding and the
  retained probe of that exact board.
- With a captured policy that keeps a 1.5 mm band free inside every edge, the
  pinned placer's proposal is adopted. The next run's fresh placement-stage DRC
  (no violations, no parity items), courtyard and outline checks pass on exactly
  the receipted helper output. The routing turn that follows runs no helper and
  gets no placement context.
- Without that band, the placer packs pads 0.25 to 0.30 mm from the edge,
  against the board's 0.5 mm copper edge clearance. The adapter still records
  `proposed`, but fresh DRC grades `copper_edge_clearance` as a defect. The
  campaign escalates on the regression and never accepts it. The placer does not
  apply edge clearance or silkscreen edge rules; that is an open AGENTEE-54 gap.
- A writer-only placement of the scaffold is captured and graded the same way.

**Placed RC variants.** The other scenarios start from owned variants of the RC
control board, with tracks and zones removed and every pad made rectangular. The
tests show the following:

- The pinned placer repairs an R1/C1 courtyard overlap.
- Two writer-only repairs are each graded by fresh native checks, and the
  second writer turn receives the second run's findings.
- A writer side flip is captured and graded.
- These proposals are refused, and none of them creates a next candidate: an
  outline move, added copper, a field change, a move of the declared fixed
  connector, a lock removed before a move, a schematic change, a tiny angle and
  a flip with a non-quarter turn.
- A helper typed failure, a crash, a timeout, a real coordinator interrupt, a
  writer failure and a failed record write each leave the campaign incomplete
  and publicly verifiable. No default placement is substituted.
- The pinned placer packs the unchanged stock RC roundrect and circle pads
  (AGENTEE-109). The fresh courtyard check passes on the adopted board, whose
  native pad shapes and sizes equal the stock board's. Its only placement-stage
  DRC violations are the three `lib_footprint_mismatch` reports against the
  fixture's rectangular-pad library copies, so the campaign escalates on a
  repeated design rather than substituting pads.
- Public verification refuses tampered records, requests, outputs, receipts,
  configuration, bindings, anchors and relabelled history.

**Envelope-fixed connectors.** A placement check that consumes a board envelope
requires the scope to declare that envelope (AGENTEE-85). Capture then requires
every envelope obligation to be bound to an executable check. The RC envelope's
outline and layer-count obligations bind to the native `board_envelope`
`outline/1` and `layers/1` checks; its stackup obligation cannot bind until a
qualified captured `copper_weight_oz` authority exists (AGENTEE-94), and the
envelope selects no fabricator profile. Capture therefore refuses such a
campaign before any writer or helper runs, naming `R-envelope-rc-filter-stackup`
as the only unbound obligation. The campaign reports the run's retained refusal, labelled
unverified, because no candidate exists to verify it against. The campaign test
asserts this refusal; it does not bypass the authority gate with test bindings.
The adapter tests exercise envelope-fixed protection directly on the real RC
envelope:

- With no declared UUID, a writer move of J1 is refused.
- The helper request adds J1 to `fixed_refs`, and the placer leaves J1 in place.
- An envelope reference absent from the board, a mutable envelope and changed
  envelope bytes each refuse the binding.

These obligations remain open:

- **Envelope-consuming campaigns (AGENTEE-85, AGENTEE-94/75).** The
  campaign-level refusal of an envelope-fixed connector move needs the outline
  and layer-count bindings, the copper-weight authority for the stackup and a
  selected fabricator profile. Until then, such a campaign stops at capture.
- **AGENTEE-93 routing feedback.** The writer context reports it as unavailable.
  No stale result or model prose substitutes for a captured routed-attempt
  receipt.
- **Convergence.** A second writer-only placement turn on the scaffold that
  leaves some part off the board is escalated as `no_monotonic_progress`. The
  outline check reports no margin, so partial progress is not progress, and an
  intermediate proposal that fails a check that passed before is a `regression`.
- **Deadline exhaustion** is exercised directly on `run_placement_iteration`,
  not through a full campaign.
- Writer-selected helper invocation is not supported. The declaration selects the
  helper.

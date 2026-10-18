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
- `sublayout` is optional. It names a captured
  [sub-layout request](sublayout-reuse.md#request) and the captured root of its
  sub-boards, `{"request": "layout/reuse.json", "sub_boards": "layout/sub-boards"}`.
  Only the group-local copper of that request is then admitted besides footprint
  transforms (see [group-local copper](#group-local-copper-agentee-129)). A
  declaration without the key normalizes, hashes and binds exactly as before.
  A `sublayout` together with a `helper` is refused by name: the pinned
  `agent-ee.placer/1` refuses any board that holds tracks, vias or zones, so
  every helper run after a reuse would fail.

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

The only exception is the copper of a declared sub-layout, described next.

`proposal_scope` additionally requires every non-board file to keep its exact
bytes and inventory. File-level `mutable_inputs` is therefore not enough: a
schematic listed as mutable is still refused in a placement iteration.

`pcbnew.SaveBoard` can rewrite same-stem `.kicad_pro` and `.kicad_prl`
siblings as well as the board. A native save is no exception to the exact-byte
non-board scope above. Follow the
[campaign writer rule](campaign-guide.md#saving-boards-without-changing-project-siblings):
restore or exclude generated siblings in the sandboxed step, or save to a
staging copy and copy back only the board. The adapter's records and receipts
remain provenance only; they cannot authorize sibling changes or make a check
pass.

### Group-local copper (AGENTEE-129)

With a declared `sublayout`, a placement iteration may also hold the output of
[sub-layout reuse](sublayout-reuse.md), for example the writer running
`agent_ee.sublayout` on its disposable copy. Nothing else is widened.

**Binding.** The request file and every sub-board it names
(`<sub_boards>/<sub_board>`) must be captured inputs outside `mutable_inputs`,
with the captured digests; a group's declared `sub_board_sha256` must equal the
captured digest. Any missing, mutable, linked, changed or invalid file refuses the
binding before any writer runs. The binding records
`sublayout: {request: {path, sha256}, sub_boards, groups: [{id, sub_board, sha256}]}`,
so a later iteration whose request or sub-boards differ is refused as drift.

**Admission.** For each scope proof, `group_admission` plans the request with the
unchanged AGENTEE-56 planner from fresh sandboxed pcbnew facts of the captured
source board and sub-boards. A group admits copper only when the planner applies
it: every member is new (unlocked and outside the source outline), land patterns
and footprints match, and every UUID is fresh. A group the planner refuses, for
example because its members were placed by an earlier iteration, admits nothing,
and copper already on the source board stays immutable as before.

**Proof.** `write_scope(before, after, protected, groups)` lifts a new board-level
node out of the comparison only when it carries the UUID of a planned copy or of
the planned group node, and its kind matches (`segment`, `arc`, `via`, `zone`,
`gr_*` graphics, `group`). Each such UUID is lifted once; a second node with an
admitted UUID is refused, in either order, because pcbnew keeps both nodes while
a read keyed by UUID would see only one. A copy UUID is
`uuid5(namespace, [group id, sub-board sha256, source uuid])`, so the admitted
copper is bound to the captured sub-board bytes. Each lifted copy must also keep
every saved token other than its geometry and net (`start`, `end`, `mid`,
`center`, `at`, `pts`, `polygon`, `filled_polygon`, `net`, `net_name`, `uuid`)
equal to its source node in the captured sub-board, read through the shared
S-expression parser: zone fill and clearance settings, stroke, layers and lock
state included. Every other added, removed or changed copper node is refused as
before, including copper inside the group's region. The members of a group with such a node may take the sub-board's
Reference and Value position, angle, layer and visibility. Then, for every group
with any admitted node, a fresh sandboxed pcbnew read of the delivered board must
show, exactly:

- every planned copy, so a group is admitted whole or not at all;
- each copy's kind, net, layers, width, drill and points equal to the sub-board
  transform onto the voted net (copper on a withheld net is never admitted);
- each copy's net among the group's `mapped` nets, and its bounding box inside the
  group region: the sub-board module's extent (footprints and copied items, not
  its outline) moved and rotated like the copies;
- each member's pose, pad positions, unchanged pad nets and planned fields;
- the KiCad group `sublayout:<id>` with exactly the members and copies;
- each copied zone's saved fill, on every layer, exactly equal to a coordinator
  refill (see below);
- every board item UUID exactly once, and every item that is not on the source
  board a planned copy of a reused group. The token comparison already refuses
  both cases; the fresh read must agree with it.

Both pcbnew reads are bound to the board bytes that the token comparison hashed
(`after_sha256`).

**Zone fills.** Fill geometry is not part of the plan. The token comparison
skips `filled_polygon` and the facts carry only `IsFilled()`. Fresh DRC also
grades saved fills without a refill pass (see [check reference](check-reference.md)).
So a saved fill is admitted only when it equals a refill made by the coordinator.
`sublayout.zone_fills` copies the delivered board into the networkless sandbox,
and the child `agent_ee.sublayout_probe fills` reads every saved fill polygon of
the copied zones on any layer, outline and holes in integer nanometres. It then
unfills those zones and refills them with pcbnew's `ZONE_FILLER`, exactly as
`sublayout_probe apply` refills them, and reads the fill again. The copy is never
saved, and the candidate is never filled or written. Any difference refuses the
group: a forged, extra, off-layer or missing fill, and also a stale fill. The
writer must therefore refill the copied zones after the last change near them;
`sublayout.reuse` already leaves an exact refill.

This refill-and-compare was chosen over checking that each fill lies inside the
zone's own outline. Containment would still admit a forged same-net fill inside
the outline, for example one that ignores thermal relief, island removal or
clearance to a higher-priority zone. Equality to the refill admits only the fill
that pcbnew itself computes for the admitted copy.

Any difference is a violation with the group named, and the iteration is refused.
Protected footprints keep their rule: a protected member refuses its group.

**Record.** With a declared sub-layout, every scope proof (`writer_scope`,
`proposal_scope`) carries a `copper` section, `agent-ee.group-copper-scope/1`:
the request digest and, per group, `status` (`reused`, `refused`, `absent` or
`not_admissible` with the planner's findings), the sub-board path and sha256, and
for applied groups the group UUID, rotation, anchor, members, region, mapped nets,
copied counts by kind, item UUIDs, withheld copper and `zone_fills`. `zone_fills`
maps each copied zone UUID to the sha256 identity of its coordinator refill. It is part of the anchored
`placement-adapter.json`, and `verify_record` and `verify_attempt` recompute it,
with fresh pcbnew facts, from the source inputs and the captured proposal or
scratch design.

The copper is graded like any proposal: the next run's fresh native DRC with
schematic parity, placement checks and the routing-stage DRC decide. Neither
the reuse result nor the scope record is evidence of a pass.

Limits:

- **No placement helper.** A declaration with both a `sublayout` and a `helper`
  is refused at validation, before the campaign directory exists. The pinned
  placer (`placer_runner`) refuses any board with tracks, vias or zones, even
  when every reused member is a fixed reference, so a helper after a reuse could
  only fail. Fixing the members in the policy would not help in any case: policy
  `fixed_refs` are protected footprints, and a protected member refuses its
  group. The writer places the remaining footprints itself, under the ordinary
  footprint-transform scope.
- **Stale fills are refused.** A footprint move near a copied zone, after its fill,
  refuses the group until the zone is refilled.
- **Later moves are not tied to the copper.** Moving members of a group reused in
  an earlier iteration is still an ordinary footprint transform, and fresh DRC
  grades the copper they leave behind.

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

**Sub-layout reuse (AGENTEE-129).** `tests/test_group_copper_scope.py` starts
from an actual AGENTEE-52 forward annotation of the emitted regulator schematic
onto the owned module's outline, with the owned routed module captured as the
sub-board and a captured request. The scripted writer runs the unchanged
`sublayout.reuse`. The adapter records the group as `reused`, and the next run's
fresh placement-stage DRC, courtyard, outline and full routing-stage DRC (no
violations, unconnected items or parity issues) pass, so the campaign ends
`criteria_satisfied` and verifies publicly. The same reuse plus one stray track
beside the group, and the same reuse without a declared `sublayout`, each refuse
the iteration with no next candidate. Scope tests on the rotated instance of
`fixtures/sublayout/top.kicad_pcb` refuse, from real pcbnew saves, a moved
copy, a copy locked after the reuse, a missing copy, a member moved off its copper, a member field off the
plan, copper outside the group region, extra copper inside the region, edits of
a group's copper in a later iteration and a protected member. They also refuse,
from saved-board text edits, a second node that reuses a copy UUID in either order,
and four kinds of saved fill on the copied zone: one forged far outside the
region, one forged inside its outline, one forged on a layer the zone is not on,
and a fill removed. They admit a sub-board whose Reference text was moved. A
helper declared beside a sub-layout refuses the campaign before any writer runs.
The real placer step, given the reused board with every member fixed, fails with
`initial placer refuses existing tracks, vias or zones`.

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

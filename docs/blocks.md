# Two-level placement: blocks (AGENTEE-339) and arrangement (AGENTEE-340)

`agent_ee.blocks` derives placement blocks from the sealed schematic hierarchy
and packs each block in isolation into a rigid block candidate (stage A).
[Stage B](#stage-b-arrangement-agentee-340) (`agent_ee.block_arrangement`)
arranges the candidates as rigid macros and packs the residual parts. The
[block library](block-library.md) (AGENTEE-342) catalogues and imports cited candidates. Neither module
writes a board; the native writer does. The complete board plan must still pass native save/reload,
retain usable [locality evidence](placer.md#pad-level-electrical-locality-agentee-322) and pass the
[native publication gate](placer.md#through-hole-reservations-and-native-publication-gate-agentee-318).

```sh
PYTHONPATH=src python3 -m agent_ee.blocks derive --probe build/placement-probe.json \
  --netlist build/design.xml --allocation design/blocks.json --out build/blocks.json
PYTHONPATH=src python3 -m agent_ee.blocks pack --probe build/placement-probe.json \
  --netlist build/design.xml --allocation design/blocks.json --policy design/packing.json \
  --block buck-5v --out build/buck-5v.candidate.json
PYTHONPATH=src python3 -m agent_ee.blocks admit --probe build/placement-probe.json \
  --netlist build/design.xml --allocation design/blocks.json --policy design/packing.json \
  --candidate build/buck-5v.candidate.json --out build/buck-5v.admission.json
PYTHONPATH=src python3 -m agent_ee.blocks match --probe build/placement-probe.json \
  --netlist build/design.xml --candidate build/buck-5v.candidate.json \
  --mapping design/buck-12v.mapping.json --out build/buck-12v.match.json
```

Each command writes a new file (an existing `--out` is refused). It prints one
JSON status line on stdout; `pack` heartbeats go to stderr. The exit code is 0
for `derived`, `packed`, `admitted` or `match` and 1 otherwise. A refused input prints
`{"status": "refused", "reason": ...}` and writes nothing.

## Inputs

- `--netlist` is the captured native KiCad XML export of the sealed design, for
  example the `netlist` check output of a [block-first](block-first.md)
  integration run. It is read with the strict native inventory reader, and its
  SHA-256 is recorded. Each component's sheet occurrence path comes from that export.
- `--probe` is the native [placement probe](placement.md) of the board. Its
  `board_sha256` binds every output.
- `--allocation` is optional: the engineer's `agent-ee.block-allocation/1`
  file. Its raw bytes are hashed.

## Derivation

Without an allocation file, each top-level child sheet occurrence is one block,
including its nested sheets. The block id is the sheet name without slashes.
Blocks are ordered by name. Root-sheet components form the residual set.
Repeated occurrences of one child file are separate blocks.

An allocation file replaces that default:

```json
{
  "schema": "agent-ee.block-allocation/1",
  "blocks": [
    {"id": "buck-5v", "references": ["U5", "C1", "C2", "C3", "L1"], "anchor": "U5"},
    {"id": "sensor", "sheets": ["/Sensor/"], "references": ["C9"]}
  ],
  "residual": ["J1"]
}
```

| Field | Rule |
| --- | --- |
| `id` | Unique nonempty text |
| `sheets` | Exported sheet names (`/Sensor/`) or occurrence paths. A sheet claims its own components and those of its descendants. The root sheet is not a block |
| `references` | On-board native components. This is how a sheet is split, root parts are grouped or a part is moved between blocks |
| `anchor` | Optional member that defines the block frame |
| `residual` | Board footprints that stay outside every block |

Fields other than these are refused, and every block needs `sheets` or
`references`. The following refuse, naming the parts or sheets involved:

- a footprint claimed twice (by two blocks, twice within one block, or by a
  block and `residual`). Within one block, a nested sheet listed beside its
  parent sheet, or a reference inside a listed sheet, counts as a second claim;
- a child-sheet component that is neither allocated nor declared residual;
- an unknown reference, sheet or residual entry, or a listed sheet with no
  on-board footprints;
- duplicate ids, an anchor that is not a member, or duplicate JSON keys.

Unclaimed root-sheet components are residual.

In both modes, every on-board XML component must have one board footprint
with the same footprint identifier. A board footprint absent from the export is
residual (`board-only`) only when none of its pads has a net. Locked footprints
cannot be rigid-block members, so they must be allocated to the residual set.
Fixed parts belong to arrangement. Run `derive` on the pre-placement seed. A
board whose block members are already locked at cluster poses (for example a
hand-anchored placement candidate) refuses.

`derive` returns `agent-ee.block-derivation/1`:

```json
{"schema": "agent-ee.block-derivation/1", "board_sha256": "<probe hash>",
 "inventory_sha256": "<XML hash>", "allocation_sha256": null,
 "blocks": [{"id": "Power", "origin": "sheet", "sheets": ["/<uuid>/"],
             "references": ["C1", "L1", "U1"]}],
 "residual": [{"reference": "J1", "reason": "root-sheet"},
              {"reference": "H1", "reason": "board-only"}]}
```

Residual reasons are `root-sheet`, `board-only` and `allocated-residual`.

## Intra-block packing

`pack_block` reuses the unchanged [placer](placer.md) search
(`ranked-first-fit-backjump/1`). It does not modify search logic. The block's
members are copied into an isolated capture.

- **Anchor.** The declared anchor, or the member with the most numbered pads
  (ties: natural reference order), stays fixed at its captured pose.
- **Synthetic plane.** The outline is a square centred on the anchor. Its
  half-side is the anchor's extent plus, for every other member, twice its
  extent (courtyards and pad reservations) and the gap. Any chain of the members
  fits inside. A first plan in which a member touches the square's edge goes to
  [recovery](#recovery-agentee-359). If recovery does not obtain a plan, the
  result is `synthetic_plane_reached`, `outcome=unknown`, with no candidate.
  This happens, for example, with a member whose only nets are ground nets: the
  placer's wire cost ignores ground nets.
- **Order.** Declared priorities come first. Then members are ordered
  breadth-first over shared non-ground nets from the anchor, so each member
  is placed after a same-net neighbour.
- **Policy.** The caller supplies an ordinary `agent-ee.packing-policy/1` for
  the board. The block keeps `step_budget`, `gap_nm`, `ground_nets`,
  `search_timeout_s` and in-block `priority_refs`. It also keeps
  footprint-owned rule areas of its members. It keeps groups whose members and
  anchor reference are all in the block, without a region, and
  `electrical_locality` obligations whose endpoints all lie in the block. The
  anchor is fixed in the block frame, so a board priority on it is dropped. Board
  regions, point anchors, obstacles, board rule areas, the edge margin, and
  groups or obligations that cross the block boundary are deferred to
  arrangement. Their ids and counts are recorded in `deferred`. A block
  member in `fixed_refs` or `fixed_overhangs` refuses.

### Recovery (AGENTEE-359)

The placer's wire cost ignores ground nets and parts outside the block. A member
whose only tie to the rest of the block is a plane net, or whose other net goes
to a fixed or out-of-block part, therefore ranks by position alone. On the pod,
C18 (`PRE_6V3`, `GND`) and R33 (`MCU_BOOT46`, `GND`) went to the synthetic
plane's corner, although a same-member block exists.

The first run is unchanged. Only when its plan reaches the synthetic plane,
`pack_block` retries with a temporary anchor-proximity group:

- The group (`stage-a-anchor-proximity`) holds the members that reached the
  plane within `max_distance_nm` of the anchor's origin. Every other part of
  the isolated policy, and the synthetic plane itself, is unchanged.
- The first trial uses half the plane's half-side. If a trial's plan still
  reaches the plane, the members that reached it join the group. If no new
  member reached it, the distance is halved instead.
- At most six trials run (`RECOVERY_TRIALS`), each under the block step budget.
  There is no wall clock beyond the policy's own `search_timeout_s`. Any other
  placer outcome (budget, infeasible or refused) ends the retry.
- A member is eligible only if it is in no declared group and its numbered
  pads carry at least two distinct nets. A part shorted to one net has no
  connection that places it near the anchor. If the anchor itself is an
  anchored group member, recovery does not apply.

The group never reaches the candidate. The recovered poses are graded and
witnessed against the unchanged block policy, as for any first run, and
`block_policy` is the unchanged isolated policy. A synthetic-plane boundary is
never a feasibility result: when the retry ends without a plan inside the
plane, the result stays `synthetic_plane_reached`, `outcome=unknown`. It then
carries a `recovery` record with `status` `not_applicable` or `exhausted`, a
`reason` and the trials. The distances are search effort, not design limits.

A recovered candidate's `search` gains `recovery`:
`{method: "anchor-proximity-retry/1", status: "recovered", stranded,
references, max_distance_nm, trials: [{references, max_distance_nm, status,
steps_used}], steps_total}`. `validate_candidate` checks every trial, not
just the last. `stranded`, `references` and each trial's `references` are
distinct non-anchor members in natural order, none in a declared group; each
`max_distance_nm` is a positive integer. There are at most six trials, each
within the block budget. Every trial but the last is `synthetic_plane_reached`
and the last is `recovered`, naming the record's references and distance. The
first trial groups exactly `stranded`. Each later trial either grows the group
(a strict superset) at the same distance, or keeps the group at half the
previous distance (integer halving); anything else refuses. `steps_total` is
the trials' sum. No `stage-a-anchor-proximity` group may appear in
`block_policy`. `admit` also binds the ladder to the board: the first
distance must be half the synthetic plane's half-side recomputed from the
board, and every grouped member must be eligible there (no declared group, at
least two nets), else `recovery_mismatch`. Compaction then runs on the recovered
candidate as usual. The record stays on whichever run compaction selects.

### Compaction (AGENTEE-355)

The first run's poses follow the placer's wire cost, so a block can come out
sparse or strung out (on the pod, wheel-input was 53 × 14 mm). `pack_block`
therefore re-packs a packed block on shrinking rectangular outlines and keeps
the candidate with the least total outline box area (summed over both sides).
Two families are bisected in turn, each for at most six trials:

- `anchor-square`: a square centred on the anchor, by half-side. The lower bound
  is the anchor's own courtyard reach; the upper bound is the best candidate's.
- `box-scale`: the best box so far, scaled about its centre in per mille. The
  lower bound keeps the anchor's courtyard inside.

A trial runs the unchanged placer with the same isolated block policy, so every
internal obligation, group, gap and rule area applies unchanged. A trial counts
as packed only when its candidate passes the same re-grade and courtyard witness
as the first run. A packed trial lowers the upper bound to its own extent; any
other result raises the lower bound. Bisection stops when the bounds are within
the larger of the gap and 0.1 mm. Ties keep the earlier candidate, so the result
is deterministic. Each trial may use at most the larger of 10 000 steps and ten
times the first run's steps, and never more than the block budget. This cap is
search effort, not a design limit.

Compaction runs only after the first run, or [recovery](#recovery-agentee-359),
has packed without reaching the synthetic plane, so `synthetic_plane_reached`
and every refusal are unchanged.
The candidate's `search` gains `compaction`:
`{objective: "outline-bisection-min-box-area/1", first_box_area_nm2, box_area_nm2,
step_cap, trials: [{family, parameter, status, steps_used, box_area_nm2?}], steps_total}`.
`validate_candidate` checks that record's structure: the area is at most the first
run's, and every trial is within the cap. `admit` binds the area to the board's
own outline (`stale_evidence` with evidence `compaction`). The
candidate's `steps_used` is the selected run's. `pack --no-compact` (and
`compact=False`) keeps the first run.

A member pad number that carries several nets refuses, because its pin role
would be ambiguous. The placer's own refusals propagate unchanged, with `status`, `outcome`, `reason`
and the full `plan`. For example, obligations without limits or citations give
`electrical_locality_unavailable`, and an exhausted search gives
`packing_search_exhausted` (`unknown`). No search-derived pose is ever returned
as a pass.

## Block candidate schema

`agent-ee.block-candidate/2` is the contract for AGENTEE-340 and AGENTEE-342.
Version 2 replaces `/1`, which accepted missing or failed evidence. It adds each
member's `courtyard` record, and every field below is required. Unknown fields
refuse, and a `/1` document is refused. Only `status: packed` is usable.

| Field | Content |
| --- | --- |
| `block_id`, `status` | Block id; `packed`, or `refused` when a re-grade or witness fails |
| `provenance` | `class`, `enforcement`, `citation`, optional `method`; reference classes add `source: {locator, sha256}` |
| `frame` | `{convention: "anchor-origin-quarter-turn/1", anchor}` |
| `members[]` | `reference`, `fpid`, `side`, `value`, `pose`, `pad_geometry_sha256`, `courtyard_sha256`, `courtyard` (the footprint-local courtyard record, in canonical rings, that `courtyard_sha256` hashes), `pins[]` |
| `outline` | Per side: `box_nm` `[x0, y0, x1, y1]` and the framed outer `courtyards` polygons |
| `grade` | Independent re-grade in the frame: `electrical_locality` and `groups` (each `null` when nothing is internal) |
| `witness` | `exact-courtyard-separation/1`: `gap_nm`, `pairs_checked`, per-side `minimum` exact squared distance `[numerator, denominator]`, `below_gap`, `drilled_pad_conflicts`, `status` |
| `placer_judgment` | Verdict and basis of the placer's full-polygon judge |
| `deferred` | Boundary groups and obligations by id; counts of obstacles and board rule areas |
| `search` | `strategy`, `backjumps`, `steps_used`, `step_budget`; optional `compaction` ([AGENTEE-355](#compaction-agentee-355)) and `recovery` ([AGENTEE-359](#recovery-agentee-359)) |
| `block_policy` | The exact isolated packing policy |
| `sources` | `board_sha256`, `inventory_sha256`, `allocation_sha256` (`null` without an allocation file), `derivation_sha256`, `policy_sha256` |

### Validation and admission

`validate_candidate(document)` needs no board. It raises `ValueError` unless:

- every documented field and nested record is present and well typed, with no
  unknown fields; a malformed nested value is a typed refusal, not a crash;
- each member has one pin record per pad; one role names one net with one
  `boundary` flag, and one net has one role;
- `grade.electrical_locality` and `grade.groups` are `null` exactly when the
  block policy has no internal obligations or groups. Otherwise they have no
  defects or unknowns, declare the block policy and measure every internal pair
  or group member once. Groups must be `pass` with no exceeded advisory: a
  satisfied unsourced group limit grades `pass`, so a stored group grade of
  `info` is refused whatever its advisories say. Locality may be `pass` or `info`, with
  every hard squared distance within its limit and every advisory reported;
- the witness is `exact-courtyard-separation/1` at the block policy gap, with
  status `pass`, no pair below the gap, no drilled-pad conflict, and exact
  rational minima (`[numerator, denominator]`, positive denominator) at least
  the gap squared;
- the placer judgment passes, the search stayed within the block budget, and
  the block policy fixes only the anchor and holds only internal obligations
  and anchored groups;
- `deferred` lists unique group and obligation ids disjoint from the internal
  ones, with nonnegative counts;
- every source is a SHA-256; `allocation_sha256` may be `null`.

It also recomputes pose-dependent courtyard evidence. Each member's `courtyard`
record must hash to its `courtyard_sha256`; placed at the member's pose, the
records must reproduce the stored `outline` and witness exactly. A moved pose,
an empty box or an edited polygon therefore refuses. A record whose rings are not
canonical comes from before AGENTEE-370 and refuses with `identity_version`; it is
never re-hashed.

Validation is structural, not board admission (AGENTEE-401). Every measured
locality row requires `constraint_provenance` matching the declaration and an
explicit `manufacturing_floor`. A known floor needs a nonnegative integer
`floor_nm`, the captured PCB source and clearance, and the necessary-copper-floor
kind; an unknown floor needs a nonempty reason. Missing or malformed records
refuse instead of implying `below_floor: false`. Explicit unknown floors remain
unknown records; geometry-unqualified floors can still accompany advisory grades.
The floor value and its claimed geometry cannot be verified without the board.
Consumers must use `admit` before trusting stored locality evidence; mapped
arrangement inputs independently regrade the target board's own footprints and
policy. `match` establishes member identity, not evidence admission.

### Pose-independent identity (AGENTEE-370)

`pad_geometry_sha256` and `courtyard_sha256` hash the footprint at the origin,
rotation 0, on its captured side. pcbnew starts a captured contour at a different
vertex, and may wind it the other way, at each board rotation. So every courtyard
ring (`polygons`, `inner_polygons`) and every custom-pad `merged_polygons_nm`
ring is made canonical before hashing (`blocks.canonical_ring`). The ring runs
with a nonnegative shoelace sum and starts at its lexicographically smallest
rotation; a zero-area ring takes the smaller of both directions. Each polygon list
is then sorted. Only vertex order changes: a moved vertex, another size or a mirror
image still hashes differently. Framed members (`blocks.frame_member`) carry these
rings, so a candidate outline and its `admit` and arrangement re-derivations do
not depend on the captured order either.

`NativeCustomPadIdentityTests` (AGENTEE-373) saves an original asymmetric custom
pad through pcbnew on both sides at all four quarter turns, then extracts its
geometry. Every cyclic start and winding of the extracted copper contour keeps
its pad identity; moving a copper vertex by 1 nm changes it. Opposite sides
retain distinct identities.

AGENTEE-429 asserts the side directly rather than through chirality: a native
mirror-symmetric custom pad (a cross) on F and B has the same merged copper, and
once its layers, courtyard key and mirrored incidental drawing are matched the side
alone still separates both identities. A synthetic part with only `side` changed
does the same, `canonical_ring` is tested for empty, single, repeated, collinear,
pinched and reflected rings, and removing `side` from the hashed documents fails
those tests.

`admit(candidate, probe, inventory, derivation, policy)` admits a candidate onto
the board it was packed from. Stage B must call it before using a candidate. It
returns `{"status": "admitted" | "refused", "problems": [...]}`. Problem kinds:

| Kind | Rule |
| --- | --- |
| `source_mismatch` | Every `sources` hash equals the supplied board, inventory, allocation, derivation and policy |
| `block_unknown`, `block_members`, `frame_anchor` | The derivation has the block, with the same members and anchor |
| `block_policy_mismatch` | The isolated policy recomputed from the board policy equals `block_policy` |
| `deferred_mismatch` | The recomputed deferred record equals `deferred`, so a boundary obligation or group cannot be dropped |
| `identity` | `match` with the identity mapping succeeds, which binds each `pad_geometry_sha256` to the board |
| `stale_evidence` | The board's own footprints at the frame poses reproduce `outline`, `grade` and `witness` exactly, including drilled-pad reservations; a `compaction` record's area equals that outline's box area |
| `recovery_mismatch` | A `recovery` record's first trial distance is half the synthetic plane's half-side recomputed from the board, and every grouped member is eligible there (in no declared group, on at least two nets) |
| `evidence_failed` | Locality is `pass` or advisory `info`; groups and witness pass |

Identity matching and grading stay separate. `match` compares identity only,
and the graders are the registered checks' own. A candidate from another board
or policy is refused by `admit`. Reusing it there takes `match` with a mapping
plus a re-grade on the target board. The
[block library](block-library.md#materialization) does that for catalogued
entries; arranging them is AGENTEE-340.

**Frame.** The anchor's origin is `(0, 0)` and its rotation is 0. A member pose
is its footprint origin and its rotation relative to the anchor, in integer
nanometres and quarter turns, in KiCad's board sense. It has the same form as
the hand artefact's `[dx, dy, rotation]`. A member keeps its captured side; a
block is not mirrored in stage A. To place the block at board pose `(P, r)`,
each member goes to `P + rotate(position, r)` with rotation
`(rotation + r) mod 360`, which is `placer.transform`'s convention.

**Pins.** Each numbered pad gives `pad`, `function` (the native libpart pin
`name` and `electrical_type`, or `null`), and `role`. Pads on one net share a
role, `n1`, `n2`, ..., numbered in anchor-first member order. `boundary` is true
when the net also reaches a footprint outside the block. The net name is
informative only. Unconnected pads have `role: null`.

**Values.** `{"text": ..., "rule": "exact"}` requires the same value text. A
declared tolerance is
`{"text": "22uF", "rule": "tolerance", "relative_tolerance": [1, 10], "citation": ...}`.
Both values must be written quantities with the same unit (`topology_rules.quantity`),
and the target must lie within the nominal times one plus or minus the tolerance,
inclusive. Search-derived candidates always use `exact`.

**Grade and witness.** The candidate re-places every member at its frame pose,
then re-grades locality and groups with the same graders as the registered
checks. It also measures every same-side courtyard pair exactly (overlap, then
edge distance) and re-checks drilled-pad reservations. A pair below the gap or
any failed grade makes the candidate `refused`. Courtyards do not model copper
clearance. Native DRC remains the board's gate.

### Provenance

Layout provenance uses AGENTEE-331's class / citation / enforcement record:

| Class | Enforcement | Requires |
| --- | --- | --- |
| `manufacturer-reference-layout` | `hard` | citation, captured source locator and sha256 |
| `manufacturer-evm` | `hard` | citation, captured source locator and sha256 |
| `qualified-owner-board` | `hard` | citation, captured source locator and sha256 |
| `search-derived` | `advisory` | citation of the search |

`pack_block` emits only `search-derived` candidates. Their poses are a proposal:
arrangement may re-pack or replace them, and they never become a reference
layout. A `hard` class means arrangement must keep the block rigid. The
[block library](block-library.md#import-paths) imports such layouts and binds
their source hashes.
`validate_candidate` only checks that the record is present and well formed; a
`search-derived` record has no `source`. It does not verify the cited document.

Unsourced numeric limits remain advisory, following AGENTEE-331/337. The
isolated policy preserves `source_records` and each pair's provenance. Only hard
pairs prune the search. Candidate production, validation and admission accept
locality `info` with no defects or unknowns and retain every advisory, including
`exceeded` and `below_floor`. Validation checks the stored classification and
advisory status; admission independently re-grades native lands against the
source-bound policy. Sourced distance violations, infeasibility and unknown
coverage still refuse. Group grades and courtyard witnesses must pass.

AGENTEE-402 ruling 402a: locality references to present source records with
mismatched SHA-256 digests produce `source_record_integrity` unknown coverage.
Packing and candidate validation refuse them, including forged advisory grades.
Absent source records and malformed hashes retain advisory/unsourced behavior.

## Matching

`match(candidate, probe, inventory, mapping)` decides whether a target instance
reproduces a candidate. `mapping` pairs every candidate member with a distinct
target reference (identity by default). Each mismatch is listed, and any
mismatch gives `status: refused`:

| Kind | Rule |
| --- | --- |
| `fpid` | Same footprint library identifier |
| `side` | Same mounting side |
| `pad_geometry` | Same hash of footprint-local pads at rotation 0: number, attribute, shape, size, local position and rotation, layers, drill, custom geometry and copper-centroid offset; UUIDs and nets excluded |
| `courtyard` | Same hash of footprint-local courtyard polygons |
| `pins`, `pin_function` | Same numbered pads, and the same native pin name and electrical type for each |
| `net_role` | A connected pad maps to a connected pad with the same `boundary` flag |
| `net_role_split`, `net_role_merged` | The candidate's net partition maps one-to-one onto the target's |
| `value` | The member's value rule holds |
| `target_missing` | Target reference absent from the board or the export |

Net names never identify a role. Two instances of one buck on different rails
match when every pin lands on the corresponding net.

## Stage B: arrangement (AGENTEE-340)

`block_arrangement.arrange(capture, inventory, derivation, policy, arrangement, entries)`
places every derived block as one rigid macro. It then packs the residual parts
with the unchanged placer, with every block member fixed, and grades the
complete plan as one board. It writes no board. Through `placer_runner`, the
plan is saved and reloaded, judged, graded and gated by native DRC, as every
placer plan is.

```sh
PYTHONPATH=src python3 -m agent_ee.block_arrangement --probe build/placement-probe.json \
  --netlist build/design.xml --allocation design/blocks.json --policy design/packing.json \
  --arrangement design/arrangement.json --candidate build/buck-5v.candidate.json \
  --candidate build/sensor.candidate.json --out build/arranged-plan.json
```

The CLI prints one JSON status line, and heartbeats go to stderr. The exit code
is 0 only for `planned`. An existing `--out` is refused. The CLI takes
same-board candidates. Mapped (library) entries are passed through the API or
the [runner request](#runner-request).

### Inputs and admission

`entries` holds one record per derived block:
`{"candidate": <agent-ee.block-candidate/2>, "mapping": {...}, "boundary_nets": {...}}`.
`mapping` and `boundary_nets` are optional. A missing block, a duplicate block or
an unadmitted candidate gives `arrangement_refused`, `outcome=unknown`, with each
problem listed. Library candidates and search-derived candidates are both rigid
macros. The plan records each block's provenance class and citation.

- **Same board, no mapping.** `blocks.admit` must admit the candidate.
- **Mapped (a library or reused layout).** `blocks.match` with the mapping must
  match. The mapped references must be exactly one derived block, with the same
  anchor. Their frame poses are then re-graded with the target board's own
  footprints and isolated policy (locality, groups and the courtyard witness).
  The block's deferred obligations and groups are the target board's, from its
  own isolated record (`blocks._isolate`). The source candidate's `deferred`
  record names the source board's obligations and is never graded on the target.
- **Boundary nets.** Matching compares net roles, but not which outside net a
  boundary role lands on (the 339 review gap). Arrangement binds every role to
  the board net its pins land on. A boundary role must reach a footprint outside
  the block, and an internal role must not. On the candidate's own board, the
  recorded net name must be the board net. A mapped candidate must declare the
  target net of every boundary role in `boundary_nets`. The problem kinds are
  `boundary_net_mismatch`, `boundary_undeclared`, `boundary_unknown_role`,
  `boundary_flag` and `net_role_split`. The plan records each block's binding
  with the outside references.

`policy` is the captured packing policy that the candidates were packed and are
admitted against. `effective_policy` may only add board obstacles, for example
the writer's `IMU_AXES` reservations. Arrangement enforces those obstacles.
It refuses any other difference with `EffectivePolicyError`, a `ValueError`
subclass whose `problems` name each key that differs. Every key except
`obstacles` must be equal, and the captured obstacles must stay unchanged and in
order as the prefix of the effective list. So the effective policy cannot drop or
relax a captured constraint, such as an obstacle, the gap, fixed parts, groups,
locality or the edge margin. Nothing is published.
A group that ties a block member to a residual movable anchor refuses
(`group_anchor_residual`): the member is fixed before that anchor is placed.

### Arrangement policy

`agent-ee.block-arrangement/1`:

```json
{"schema": "agent-ee.block-arrangement/1", "step_budget": 200000,
 "priority_blocks": ["buck-5v"],
 "objective": {"terms": ["pad-locality", "inter-block-wire", "thermal-spread"],
               "provenance": {"enforcement": "advisory", "reason": "default order"}},
 "thermal": {"sources": ["U5", "U7"], "min_distance_nm": 15000000,
             "provenance": {"enforcement": "advisory", "reason": "no sourced limit"}},
 "keepouts": [{"id": "imu", "side": "F", "polygons": [[[0, 0], [4000000, 0], [4000000, 4000000], [0, 4000000]]],
               "provenance": {"enforcement": "hard", "citation": "IMU datasheet section 9"}}],
 "no_stacking": {"references": ["L1", "L2"], "margin_nm": 500000,
                 "provenance": {"enforcement": "hard", "citation": "owner rule D-1"}},
 "no_block_over_fixed": {"exempt_blocks": ["storage"],
                         "provenance": {"enforcement": "hard", "citation": "owner rule D-2"}}}
```

Only `schema` and `step_budget` (1 to 1 000 000) are required. Unknown fields
refuse. `search_timeout_s` is opt-in (0 to 86 400 s). Without it there is no
wall clock: the step budget is the only limit.

Every rule carries a provenance record. `hard` needs a `citation`. An unsourced
rule or number is `advisory` (AGENTEE-331/337): the search never uses it to
prune, and the complete plan grades and reports it. A hard rule that has no
citation is refused.

| Rule | Hard effect | Graded on the complete plan |
| --- | --- | --- |
| `keepouts` | A stationary obstacle for blocks and residual parts, kept clear by the packing gap | Footprints whose courtyard overlaps the area |
| `no_stacking` | Each listed part's mounting-side courtyard box, grown by `margin_nm`, is a shadow on the opposite side. Nothing there may come within the gap of it | `[part, intruder]` pairs |
| `no_block_over_fixed` | A non-exempt block member's courtyard may not overlap a fixed part's courtyard on the other side | `[block, member, fixed]` |
| `thermal` | With `min_distance_nm`, sources (footprint origins) in different blocks keep that distance | Pairwise source distances and the minimum |

A packing policy's `advisory_regions` with `shadow_of` report the same shadow geometry, never searched, on flat plans and saved boards too (see [placer](placer.md#advisory-regions-and-pose-bound-shadows-agentee-436)). Its `proximity_nm` reports nearby parts as well as intruders.

A block whose own members lie in its own shadow refuses
(`block_internal_stacking`). The writer re-grades the rules on the native saved
board (`judgment.block_rules`). A failed hard rule there refuses publication.

### Search

Each block becomes one footprint-like macro: the members' courtyards on each
side, their pads, footprint-owned keep-outs and drilled-pad reservations, at
their frame poses. The block keeps its captured side; a stage-A candidate is
side-preserving, so it is not mirrored. The placer's own predicates apply
unchanged: outline containment with `min_courtyard_edge_mm`; exact polygon
clearance by the gap from fixed parts, obstacles and keep-outs; rule areas;
symmetric through-hole reservations; group regions and anchor distances; and
cross-block pad obligations whose peer is already placed. Fixed parts keep
their envelope overhang bands. Each member of a placed block reserves its own
courtyard box, inflated by the gap, as the placer reserves a movable part. A
block's bounding box is not reserved, so sparse blocks can interleave. On the
pod, stage-A block boxes total 3590 mm² (F) and 3212 mm² (B) of a 5850 mm²
side, so whole-block boxes did not fit.

Blocks are ordered by `priority_blocks`; an entry that is not a derived block id is refused, and the refusal names every unknown id and lists the derived ids (as does `no_block_over_fixed.exempt_blocks`, AGENTEE-372). Then come blocks that constrain both
board sides: those with a drilled pad (a through-board reservation) or a
hard-shadowed part. The rest follow. Within each group, the next block is the
one that shares the most non-ground nets with the fixed parts and the blocks
already ordered (ties: larger courtyard area, then id). For each block,
candidate origins align to the outline, placed boxes, exact obstacles, group
points and obligation targets, in four quarter turns.

Member-aligned axes (AGENTEE-355) add two sets of origins. In x, a frontier
member box touches a placed box or obstacle at the gap: a frontier box has no
other member box of its side wholly to its left (or right) within its y span.
In y, at each x, the ends of the blocked runs are added: the origins where some
member box touches a reserved box, shadow, obstacle or drilled pad in y. Both
stay within the outline window. The macro-aligned origins remain, so the
candidate set only grows. The objective terms rank
the candidates lexicographically, in the declared order, then by position and
rotation; there are no weights:

- `pad-locality`: the exact cross-block obligation cost (squared distance over
  squared limit), as the placer ranks it;
- `inter-block-wire`: for each non-ground net the block shares with placed
  parts, the integer Manhattan distance from the centroid of the block's pads on
  that net (floored to 1 nm) to the nearest placed pad on it, summed. Each shared
  net is one term, so a block pair sharing more nets weighs more;
- `thermal-spread`: the negated smallest squared distance to a placed source.

A hard thermal limit and the obligation limits are exact, so a violating
position is dropped while ranking and its blocking blocks are recorded. A
position where a rectilinear member courtyard overlaps a fixed courtyard,
obstacle or footprint-forbidding rule area is dropped the same way. The
first admissible candidate is taken. Every candidate given to the exact
predicates costs one step.

The prefilter is a sweep over blocked rectangles: for each pair of a member box
and a reserved box, the open set of origins where the two come within the gap.
A placed block's drilled-pad reservation (an inscribed square) blocks any
member courtyard that would overlap it on either side. Its owner is recorded as
a blocker. Only parts with pad geometry count, as in `placer._hole_conflict`.
On the pod with the default objective, drilled-pad rejections at the exact
predicate fell from 4341 (340 search, 300 s) to 48 (240 s). The rectangles are cached as the incremental
ranking cache: a block's rotated geometry and its rectangles against fixed parts
are computed once, and its rectangles against each placed block are kept per
(block, rotation, placed block, pose). After a backjump, only the blocks that
moved are recomputed. The cache is bounded (`OWNED_CACHE` entries, least
recently used first) and changes no result. A block with no admissible candidate backjumps to
the latest placed block among the recorded blockers, which then tries its next
candidate (conflict-directed backjumping). Results:

| `status` | `outcome` | Meaning |
| --- | --- | --- |
| `planned` | `info` | Complete plan; see below |
| `arrangement_search_exhausted` | `unknown` | A block has no candidate, and no placed block blocked it. Infeasibility is unproved: the candidate set is finite |
| `arrangement_budget_exhausted` | `unknown` | Step budget (candidates and, as in the placer, backjumps), opt-in wall time, or the locality target budget |
| `arrangement_refused`, `electrical_locality_unavailable`, `unsupported_geometry`, `fixed_conflict` | `unknown` | Inputs |
| `judge_rejected` | `unknown` | The complete plan failed a hard rule or a deferred obligation |

Failures carry `search.partial` with the arranged blocks. Its authority is
`diagnostic-only-unvalidated-partial-arrangement`. Placements are empty.

### Residual packing and the complete plan

The residual parts are packed by `placer.pack` (`ranked-first-fit-backjump/1`,
legacy-first). Block members are fixed at their arranged poses, and the hard
keep-outs and shadows are obstacles. Each carries its rule's citation as
`provenance: {class: arrangement_rule, enforcement: hard, citation}`, so a
residual capacity proof that rests on a hard rule is a `defect` naming that
obstacle, not an unknown about unsourced geometry (AGENTEE-365). Advisory rules
are never obstacles. A residual failure is the placer's own
typed result, with the arrangement record attached. The placer judges the
complete proposal and grades every group and pad obligation of the board policy.
That includes each block's deferred cross-block obligations.

The plan is an ordinary `agent-ee.packing-plan/1`. Only the board's fixed parts
are marked `fixed`, so the writer moves block members. `policy` is the
effective board policy. `steps_used` adds the arrangement steps to the residual
steps. `block_arrangement` records:

- the validated arrangement policy, `derivation_sha256` and `policy_sha256`;
- per block: provenance, `candidate_sha256`, anchor, references, boundary binding
  and deferred record;
- `order` and `inter_block_nets`, which counts shared non-ground nets per pair of
  blocks or fixed parts;
- `search` (`steps_used`, `step_budget`, `backjumps`, `rejected` per predicate,
  `elapsed_s`) and `arranged` block poses. Heartbeats carry the current block,
  backjumps and rejections;
- `residual`: references, the residual policy hash, the added obstacles and
  steps;
- `rules`: every block rule graded with its enforcement;
- `deferred_grading`: every deferred obligation and group of every block, with
  its status on the complete plan. A deferred obligation that is not measured is
  `unknown`, which fails the plan. Exceeded locality advisories produce `info` rows
  with the original `advisories` (including `exceeded`); they do not reject the
  plan. Unexceeded advisory rows retain `pass` and include their advisories.
  Defects or unavailable measurements still reject it (AGENTEE-402).

### Runner request

A `placer_runner` request with a `blocks` section uses arrangement instead of
`pack`:

```json
{"policy": {"schema": "agent-ee.packing-policy/1"}, "authorization": {},
 "blocks": {"allocation_text": null, "arrangement": {"schema": "agent-ee.block-arrangement/1", "step_budget": 200000},
            "candidates": [{"candidate": {}}]}}
```

The runner derives the blocks from the captured netlist it already binds.
`allocation_text` is the allocation file's UTF-8 text, so its hash is the file
hash. Combining `blocks` with `targeted` or `targeted_plans` is refused.
`block_arrangement.request_section(allocation, arrangement, candidates)` builds
the section from files. Every candidate file must be a bare
`agent-ee.block-candidate/2` packed (`blocks pack --allocation`) or materialized
(`block_library materialize --allocation --block`) against the same allocation
file; any other document, such as a materialize result record, is refused.
`block_arrangement.request_section_from_bytes` builds the same section from
bytes the caller has already bound by digest. A campaign placement iteration
uses it for a captured `blocks` declaration
([placement adapter](placement-adapter.md#captured-blocks-requests-agentee-439)).

### Review input: block provenance (AGENTEE-355)

Every placed arranged plan also publishes `block-provenance.json`
(`agent-ee.block-provenance/1`) beside `placement-result.json`. It is built by
`block_arrangement.provenance_artifact` and lists every placed block:

| Field | Meaning |
| --- | --- |
| `block`, `references`, `anchor`, `candidate_sha256` | Identity of the block and its admitted candidate |
| `class`, `enforcement`, `citation`, optional `method`, `source` | The candidate's provenance record, unchanged |
| `reuse` | `own-board`, or `mapped` for a library or reused layout; a mapped row carries its source-to-board `mapping` |
| `position_nm`, `rotation_deg` | The arranged anchor pose |

The top level binds the artifact: `source_sha256` (seed board), `board_sha256`
(the saved candidate board), the required `plan_sha256` (SHA-256 of the
written `placement-plan.json` bytes),
`derivation_sha256`, `policy_sha256`, and `rules` (each block rule's
enforcement and status on the saved board). `authority` is a fixed statement: the
artifact is review input only, and an advisory block confers no acceptance.
A scope that declares `block_provenance` carries the artifact whole in every
initial reviewer's packet as `block_provenance.record`, so each reviewer sees
every block's provenance `class` and `citation` (AGENTEE-421;
`tests/test_block_provenance.py`).
`placement-result.json` records the file's hash as `block_provenance_sha256`.
The placement plan records the search's wall `elapsed_s`, so `plan_sha256`, and
with it the artifact's bytes, differ between otherwise identical runs; every
other field is deterministic.
The `arrange` CLI writes the same artifact for a planned result with
`--provenance FILE`; its `board_sha256` is the plan's source board and its
`plan_sha256` the hash of the `--out` file. Its `reserved_area` is a typed
unknown (`limitation: offline_unsaved_plan`): no board is saved offline.
`--runner-section FILE` writes the runner's `blocks` request section from the
same files, so `placer_runner` saves, judges and measures the board
(AGENTEE-384).

The producer refuses an incomplete roster: the plan's provenance records
(`block_arrangement.blocks`) and arranged poses (`block_arrangement.arranged`)
must name the same nonempty, distinct block ids, and the records' references
must be exactly the placed members (placements neither fixed nor residual).

`validate_provenance` fails closed. It refuses a class that is unknown or whose
enforcement is not its class's (`search-derived` must be `advisory`), a mapped
row without its mapping or an own-board row with one, a mapping that does not
cover exactly the placed references, shared references, a blank block id, a
pose that is not an integer `[x, y]` with a rotation of 0, 90, 180 or 270, an
empty block list, a missing `plan_sha256`, an edited authority statement and
any unknown key. A consistent hard pair (`qualified-owner-board`/`hard`) is a
valid row; whether a block actually carries it is decided against its plan by
`check_roster`.

**Reaching the reviewers.** The coordinator captures the saved board,
`block-provenance.json` and the complete `placement-plan.json` from the same
placement as candidate inputs, and names the artifact and its plan in the scope
(the two keys are declared together):

```json
{"board": "pod/candidate.kicad_pcb", "block_provenance": "pod/block-provenance.json",
 "block_plan": "pod/placement-plan.json"}
```

The captured bytes are part of candidate identity. `reviewers.build_packet`
reads them through `block_arrangement.review_input`, which checks both captured
digests, validates the artifact, and requires its `board_sha256` to be the
captured `board` and its `plan_sha256` to be the captured plan's bytes. It then
runs `check_roster`: the plan must pass the producer's completeness checks, the
artifact's `source_sha256`, `derivation_sha256` and `policy_sha256` must be the
plan's, every row (block, references, anchor, candidate hash, class,
enforcement, citation, method, source, reuse, mapping and pose) must equal the
row derived from the plan, no block may be added or omitted, and each rule keeps
the plan's enforcement. The packet then carries `block_provenance: {path,
sha256, record, plan: {path, sha256}}` outside the text budget, so no row is
clipped. Every initial reviewer receives the same packet; no reviewer sees
another's judgment. A declared artifact or plan that is not captured, was
changed, is malformed, omits or relabels a block, or describes another board or
plan raises, so no packet is built and the review stays incomplete. Without the
declaration the packet has no `block_provenance`.

### Real-pod diagnostic (stage B)

This is a read-only scratch diagnostic; nothing was adopted or written to fome-mc.
The inputs are fome-mc `768df2d`
`telemetry-pod/expansion/blocks/allocation.json` (checked against
`blocks/SHA256SUMS`), the pre-placement seed board
`placement-inputs/rejected-seed.kicad_pcb.gz` (uncompressed SHA-256
`6f458cea…`) and `released-source-migration/native.xml`. The packing policy is
iteration 3's (0.25 mm gap, 0.5 mm edge margin) with the rev-2 pad obligations.
Only the allocation's residual anchors (H1–H4, J1, J2, U1, U2) stay fixed. Of
the obstacles, only the five IMU-isolation strips stay. The other four were
derived from the hand cluster poses and move with the blocks. The arrangement
policy cites iteration 2's inductor shadows (L1, L2, L4, +0.5 mm) and its
no-cluster-over-a-fixed-part rule (for the regulator blocks pre-6v3, buck-3v3,
storage-power and wheel-input) as hard. Thermal spread (U5, U7, U15; 15 mm) is
advisory: no sourced limit. Each run used the full writer path through
`placer_runner`. The host's load average was about 100 on 12 cores, so wall
times are not comparable with earlier runs; CPU time is given too.

`derive` reproduces agent-499's read-only derivation exactly: 21 blocks, 313
members and 14 residual. Stage A packs 19 of the 21 blocks (123 s in total). In
buck-3v3, C18 (a `/PRE_6V3` decoupler) and in mcu, R33 (a pull-down to the fixed
module U1) have no signal peer inside their block, so both blocks reach the
synthetic plane. Stage A also refused the gnss block, whose anchor U3 is a board
priority. That is fixed here: `_isolate` drops a priority on the anchor.

| Run | Outcome | Wall (CPU) | Steps, backjumps | Blocks placed (peak) |
| --- | --- | --- | --- | --- |
| Allocation as given | `arrangement_refused`: `block_candidate_missing` buck-3v3, mcu | 140 s | – | – |
| Variant: C18, R33 moved to residual; default order; `search_timeout_s` 1800 | `arrangement_budget_exhausted` (wall time), unplaced wheel-input | 2042 s (641 s) | 604, 550 | 6 |
| Same variant, `priority_blocks` wheel-input, diagnostic | `arrangement_budget_exhausted` (wall time), unplaced gnss | 2029 s (644 s) | 4245, 650 | 14 |

In the variant, all 21 blocks pack (104 s). No arrangement completed in 30
minutes, so the pod has no complete plan, locality grade or DRC result from
stage B. For comparison, the flat AGENTEE-322 guided search exhausted its budget
after 53 minutes with 41 of 331 parts placed. Agent-499's hand-anchored
iterations 2 and 3 placed all 331 parts in 1429 s and 866 s, with clearance,
courtyard, hole and copper-edge DRC at 0 on the cited project. The partial
arrangements are recorded as `diagnostic-only-unvalidated-partial-arrangement`.

The gaps are in the search. Stage-A candidates are sparse: diagnostic is
32 × 34 mm, and wheel-input is 53 × 14 mm for 26 parts. Several regulator blocks
have through-board thermal vias, and the inductor shadows and the over-fixed
rule bind the opposite side. Positions are aligned only to the macro's bounding
box, so interleaving two sparse blocks is found only by chance. Conflict-directed
backjumping recovers from local dead ends, but each backjump re-ranks the next
block, which takes seconds per block on the pod. Compact intra-block packing (a
stage-A objective), member-aligned candidate axes, or a cited library layout
for the regulator blocks (AGENTEE-342) are the next levers. AGENTEE-355 added the
first two; see [the microSD diagnostic](#real-pod-diagnostic-on-the-microsd-source-agentee-355).

### Real-pod diagnostic on the microSD source (AGENTEE-355)

A read-only scratch diagnostic; nothing was adopted or written to fome-mc. Inputs
at fome-mc `3d7ff5f`, each checked with `sha256sum -c` against the fome-mc
SHA256SUMS files:

- `placement-seed-microsd/base-seed.kicad_pcb.gz` (uncompressed `05da3eb6…`,
  309 footprints: 162 F, 147 B);
- `storage-microsd/native.xml` (`7791d443…`);
- `blocks/allocation.json` (`1fb4ecde…`): 20 blocks, 293 members, 16 residual.

The pod lane's allocation makes C18 and R33 residual for electrical reasons: C18
is on `PRE_6V3`, U5's output node, and R33 is a strap whose only non-plane net
goes to the fixed U1. The stage-A limitation that stranded a part reached only
through plane nets is fixed by [recovery](#recovery-agentee-359) (AGENTEE-359);
see [the rerun with C18 and R33 kept](#c18-and-r33-kept-in-their-blocks-agentee-359).

The packing policy is iteration 3's (0.25 mm gap, 0.5 mm edge margin) with the
fixed refs H1–H4, J1, J2, U1 and U2, only the five IMU-isolation obstacles, no
groups or overhangs that name removed parts, and the 12 pad obligations. Every
locality bound in those obligations is unsourced, so it is advisory
(AGENTEE-337): it ranks and is reported, and never prunes. The arrangement
policy cites iteration 2's inductor shadows (L1, L2, +0.5 mm) and its
no-cluster-over-a-fixed-part rule (pre-6v3, buck-3v3, wheel-input) as hard.
Thermal spread (U5, U7; 15 mm) is advisory. The objective is `["pad-locality"]`
(advisory), which ranks by obligation cost and then by position.

**Stage A.** All 20 blocks pack with compaction: 2607 mm² of F and 1701 mm² of
B outline boxes, in 80 s wall on 10 processes (298 s CPU; slowest block
diagnostic, 79 s).

**Complete pod through the writer.** `placer_runner.place_candidate` with a
`blocks` request and the iteration-3 `candidate.kicad_pro` beside the seed. Both
orders are `placed`:

| Order | Wall time | Steps (arrangement + residual) | Saved board SHA-256 |
| --- | --- | --- | --- |
| Default | 41.4 s | 164 + 1671 | `38b2e6b7…58d1` |
| Largest first | 41.0 s | 86 + 119 | `ced19a8d…25ea` |

A repeated default run took 37.9 s and saved a byte-identical board. Both boards
have:

- judgment `pass`, no native courtyard overlaps, no blocking DRC findings;
- groups `pass`; hard `no_stacking` and `no_block_over_fixed` `pass`, advisory
  thermal spread `pass`;
- locality `info`: 26 pairs measured, 0 defects, 0 unknown, 26 advisories. Six
  pairs exceed their unsourced bound: U5/C3 and U7/C10 supply (1.09×), return
  (1.24×) and switch (1.56× and 1.62×). Before AGENTEE-337 these bounds pruned
  stage A and every pair was within its bound;
- a `block-provenance.json` naming all 20 blocks as `search-derived`,
  `advisory`, `own-board`, bound to the saved board.

The default board adds no finding to the runner's own DRC comparison; the
largest-first board adds eight silk findings, so its `outcome` is `defect` (the
D-PCB-8 silk policy was not applied).

Native DRC with the cited `design-rules/board-setup.kicad_pro` (`7c7933bf…`):

| Item | Seed | Default board | Largest-first board |
| --- | --- | --- | --- |
| Courtyard | 5 | 0 | 0 |
| Hole (`npth_inside_courtyard`) | 1 | 0 | 0 |
| Copper-edge | 0 | 0 | 0 |
| Clearance | 18 | 18 | 18 |
| Drill | 44 | 44 | 44 |
| Library | 199 | 199 | 199 |
| Silk overlap / over copper / edge | 0 / 0 / 3 | 6 / 0 / 1 | 7 / 7 / 0 |

The 18 clearance items are identical to the seed's: pad pairs inside U2
(LGA-14) and U16, U18 and U19 (DSE0006A). They are land-pattern spacing, not
placement.

For comparison, agent-499's hand-anchored iterations 2 and 3 placed the older
eMMC source in 1429 s and 866 s. No hand placement of this microSD source
exists to compare against directly.

The first run of this diagnostic on the composed tree (main `6e7c2ea`) crashed
with `KeyError: '_packing_holes_box'`: `_shift` moved the drilled-pad inventories
but not the union boxes that `placer._hole_conflict` reads (AGENTEE-353). Main
crashes the same way. `_shift` now moves the boxes too
(`MemberAxesTests.test_shift_keeps_the_placer_reservation_caches`).

An earlier run at fome-mc `8c42f2e` used scratch allocations, because that
allocation still named ten parts the microSD source removed. With C18 and R33 in
their blocks, stage A returned `synthetic_plane_reached` for buck-3v3 and mcu.
With both residual (the same 20 blocks as `3d7ff5f`), the 30-minute ablation
showed:

- only compaction together with the position-first objective completed the pod;
- the 355 stage-B changes reduced work, not reach: arrangement steps fell from
  1502 to 131 and drilled-pad rejections from 1363 to 18.

The stage-B plan is search-derived and advisory, like every block in it. No pod
acceptance is claimed. Routing, the silk policy and review remain the pod
lane's.

#### C18 and R33 kept in their blocks (AGENTEE-359)

A read-only rerun with recovery, on the same inputs and policies, checked with
`sha256sum -c`. On the published `3d7ff5f` allocation no first plan reaches the
synthetic plane, so recovery never runs. All 20 stage-A candidates and both
saved boards are byte-identical to the AGENTEE-355 run above. C18 and R33 are
placed as residual parts.

The previous allocation, fome-mc `f2b5dd9`, keeps C18 in buck-3v3 and R33 in
mcu (20 blocks, 295 members, 14 residual). That is the allocation the
AGENTEE-355 review found stranded. Now all 20 blocks pack. Each stranded member
is recovered in one trial: C18 within 36.9 mm of U7 in 17 steps, R33 within
22.1 mm of C45 in 14 steps. Both blocks are then compacted (buck-3v3 to 487 mm²
F, mcu to 99 mm² F). The locality grade is `info`, the witness passes and the
candidate is admitted. Two independent runs gave identical candidates. Both
orders complete through `placer_runner.place_candidate`. The host was heavily
loaded, so wall times are pessimistic:

| Order | Wall time | Steps (arrangement + residual) |
| --- | --- | --- |
| Default | 173 s | 1406 + 6 |
| Largest first | 140 s | 103 + 30 |

Both boards have judgment `pass`, groups `pass`, hard `no_stacking` and
`no_block_over_fixed` `pass`, and no blocking DRC finding or native courtyard
overlap. Locality is `info` with 26 pairs and the same six advisory exceedances.
With the cited rules, courtyard, hole and copper-edge DRC are 0, and the 18
clearance items are the seed's. The only additions are silk (default: 16
overlap, 15 over copper; largest first: 7 and 5), so both runner outcomes are
`defect` until the silk policy (D-PCB-8) is applied.

## Real-pod diagnostic (stage A)

This is a read-only scratch diagnostic. No pod file was changed or copied into
the repository. The inputs are fome-mc `7a8baa137bb5072016c49b5b57c1e38bf0634cff`
`telemetry-pod/expansion/placement-candidate/iteration-3/inputs/` `seed.kicad_pcb.gz`
and `native.xml`, plus the D-PL-1 `electrical-locality.json`. A fresh native
probe was taken of the seed.

The pod schematic is flat: 327 components on `/`. Default derivation therefore
yields no blocks, and all 331 footprints are residual (four as `board-only`). An
allocation file gives the four regulator blocks: U5 and U7 with the members of
agent-491's `component-allocation.json` groups, and U15 and U26 with the members
of `cluster-poses.json`. The seed locks those 33 parts at agent-499's hand poses,
and iteration 3's policy fixes them, so `derive` and `pack` refused as specified.
The diagnostic unlocked them in a scratch probe copy and dropped them from
`fixed_refs`. Otherwise it used iteration 3's policy (0.25 mm gap) with D-PL-1's
obligations. Pair distances are native copper-centroid distances. The hand poses
were graded with the same grader in the same frame.

| Block | Members | Internal pairs | Max distance/limit, search / hand | Sum of pair distances (mm), search / hand | Box area (mm²), search / hand | Steps, backjumps | `pack_block` wall time |
| --- | ---: | ---: | --- | --- | --- | --- | ---: |
| U5 | 12 | 11 | 0.945 / 0.956 | 41.75 / 48.85 | 358.44 / 388.78 | 1056, 1 | 7.38 s |
| U7 | 13 | 11 | 0.945 / 0.956 | 41.85 / 48.87 | 416.02 / 401.22 | 1057, 1 | 6.98 s |
| U15 | 5 | 7 | 0.917 / 0.964 | 21.28 / 23.55 | 119.14 / 147.96 | 1422, 1 | 4.86 s |
| U26 | 3 | 4 | 0.760 / 0.762 | 18.18 / 18.46 | 68.33 / 69.50 | 7, 0 | 0.82 s |

In this historical diagnostic, all four candidates and the hand clusters passed
the internal locality grade and the 0.25 mm courtyard witness. Under AGENTEE-337,
these unsourced locality bounds now grade as advisory `info`. Repeated packing
gave identical candidates. The search pose is shorter than the hand pose on 21 of the 33 pairs. It is longer on 12: the
U5/U7 boot supply and high-frequency input supply, return and switch, U15's input
supply and switch and C68 supply, and U26's C97 supply. The hand-pose time was
not recorded. The wall times are single observations on a loaded host, not limits.
The flat guided search had exhausted 200 000 steps with 41 of 331 parts placed.
Matching the U5 candidate onto U7 refuses. L1 and L2 differ in footprint, land
pattern, courtyard and value, and the feedback resistors R2 and R27 differ in
value from R4 and R28.

These limits remain the pod lane's advisory bounds. No pod acceptance is claimed.

## Validation

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest tests.test_blocks tests.test_block_arrangement tests.test_block_provenance -v
```

`tests.test_blocks` uses authored hierarchical XML, boards, pads and limits.
It covers:

- default and allocated derivation, with every ambiguity refusal;
- board/netlist disagreement and locked members;
- packing with internal and deferred obligations and groups;
- advisory-only and mixed hard/advisory packing, truthful advisory records,
  and source-record preservation through JSON round trips and admission;
- required floor/provenance records (AGENTEE-401), malformed and stale floor
  provenance refusals, retained geometry-unknown advisory floors, manufacturer
  record integrity refusal, and board admission refusing a structurally valid
  but false unknown-floor claim;
- frame rigidity under anchor rotation and determinism;
- fail-closed locality, fixed-member and synthetic-plane cases;
- every provenance class and the structural refusals;
- each missing, unknown, failed, malformed or pose-stale evidence field
  (review H1), through both `validate_candidate` and `match`;
- admission: source, policy and deferred binding, a dropped boundary
  obligation, a moved member with a self-consistent outline and witness, and
  a changed land pattern;
- matching of a second instance, with each mismatch kind and the inclusive
  tolerance boundaries;
- compaction (AGENTEE-355): an authored resistor chain shrinks from 81.375 to
  56.25 mm² with the same block policy, witness and admission, deterministically;
  an internal group still passes; each malformed compaction record refuses, and
  admission refuses a recorded area that differs from the board outline's;
- recovery (AGENTEE-359): a member tied to the block only through the ground
  plane is recovered, graded on the unchanged policy, admitted and
  deterministic, and compaction keeps its record. A first plan inside the plane
  runs no recovery. A declared-group member and a single-net member stay
  `synthetic_plane_reached`. Exhaustion is bounded at six trials with halving
  distances and stays unknown. Each malformed recovery record refuses,
  through `validate_candidate` and `admit`, including a malformed intermediate
  trial (status, references, distance, steps) and any history off the
  grow-or-halve ladder; an honest three-trial grow/halve history admits, and a
  self-consistent ladder from the wrong start distance is `recovery_mismatch`;
- the CLI, with and without `--no-compact`.

A native test derives the two blocks of the [block-first](block-first.md)
fixture from its actual sandboxed `kicad-cli` XML export.

`tests.test_block_arrangement` (stage B) uses the same authored fixture. It
covers:

- rigid arranged poses, residual packing, deferred grading, boundary binding,
  provenance and determinism;
- admission refusals: missing, duplicate and wrong-policy candidates, and a
  recorded net that differs from the board net;
- a mapped library candidate, with undeclared, wrong and correct boundary nets,
  and graded on the target board's deferred obligations, not the source's;
- the native runner publishes `block-provenance.json` bound to the saved board,
  its plan and each admitted candidate's class and enforcement;
- an effective policy that only appends obstacles is accepted. One that drops,
  replaces or reorders a captured obstacle, or changes any other key, refuses
  with `EffectivePolicyError`;
- hard and advisory keep-outs, thermal limits and objective order, shadows,
  no block over a fixed part, internal stacking and stranded groups;
- typed search and budget exhaustion, including the backjump bound, and policy
  validation;
- a seeded differential of the multi-box sweep against `placer._unreserved`, and
  the inscribed drilled-pad squares;
- member-aligned origins (AGENTEE-355): the contact origins of a seeded scan are
  free of every blocked rectangle, lie on a run end inside the window, and add to
  the plain scan without removing anything; frontier members; a placed drilled
  pad blocks only positions that `placer._hole_conflict` rejects; and the ranking
  cache changes no result (cache size 0 gives identical plans and failures);
- the CLI;
- a native pcbnew board through `placer_runner`, with save, reload, judge,
  locality grade, native DRC and a rigidity check on the saved board.

`tests.test_block_provenance` covers the review input. A library entry
(`project-cluster/1`, search-derived, advisory) is materialized and mapped onto a
second power stage beside two own-board blocks. The artifact names all three
blocks with their candidate hashes, classes, enforcement, method and mapping; each
fail-closed refusal is exercised; the CLI writes it only for a planned result.
The artifact is then captured with a scope, `build_packet` carries it, and the
prompt that each profile reviewer receives (the transport is replaced in the test,
so no model is called) contains every row and no peer's review. A packet is not
built for an artifact bound to another board, an undeclared or uncaptured path, a
changed capture or a relabelled enforcement.

## Native face candidate sets (AGENTEE-479)

`agent_ee.block_variants.produce(source, capture, inventory, derivation,
block_id, policy, eligibility, output_directory, *, heartbeat=None,
compact=True, member_sides=None)` produces an
`agent-ee.block-candidate-set/1`. The output directory must be absent. Stage A
still runs the existing Python block packer; the wrapper contains unchanged
`agent-ee.block-candidate/2` documents.

The set has exactly these fields:

- `schema`, `block_id`, and `sources`: the original board, native inventory,
  allocation, derivation and policy bindings used by `/2`.
- `eligibility`: `record` is the complete side-eligibility document;
  `record_sha256` hashes its canonical JSON representation, and `eligible_faces`
  lists the block's permitted anchor faces in F/B order. The record must bind
  `sources.board_sha256` to the original board. It is also supplied unchanged as
  `blocks.side_eligibility` in the planning request.
- `captured_members`: ordered `{ref, uuid, side}` original identities. This
  retains mixed-side captures without inventing one original face for the block.
- `variants`: exactly one result for every eligible face, including failed
  faces. Each result has `status`, `capture`, `capture_sha256`, `board_sha256`,
  `derivation`, `members`, `candidate`, and `failure`.

For a native-qualified face, `capture` contains the complete native recapture,
`capture_sha256` hashes its canonical JSON, and `board_sha256` hashes the saved
board bytes. Its `derivation` differs from the original derivation only in the
board hash. Each member records `{ref, uuid, source_side, side, flipped,
pad_inventory_sha256}`; the last hash covers the same native footprint-local
pad inventory used by the R5 writer. A packed result has its unchanged `/2`
`candidate` and a null `failure`. A packing failure retains the entire typed
result in `failure`, with a null `candidate`. A refused native flip has status
`native_flip_refused`, a reason, and null capture, hashes, derivation and
candidate. It is never presented as a usable alternative.

The default variant places every member on its face key. Optional
`member_sides` maps face keys to per-reference overrides for deliberate
cross-face inputs. The key must remain the anchor's actual face. An explicit
`eligibility.members[ref].eligible_faces` overrides the block's member face
permission; otherwise the block's eligible faces apply. This permission alone
does not establish a deliberate-cross-face relationship or mechanical clearance.
Those declarations and final saved-coordinate checks remain separate.

The producer flips each required footprint through native
`pcbnew.Flip(position, FLIP_DIRECTION_LEFT_RIGHT)`, saves a disposable board,
and extracts its full geometry. It preserves original board bytes when no flip
is needed. Custom pads, per-layer padstacks and locked-footprint flips retain an
explicit refusal. Project and custom-rule companions follow each disposable
copy. Completed face results remain in `candidate-set.json` if later work fails.

Before production planning, the writer recreates every supplied variant from
the original board and compares the complete native recapture and pad inventory.
Changing both a supplied geometry record and its declared hash cannot pass this
check. After planning, `block_arrangement.arranged[].selected_face` and the
block's selected inner-candidate and whole-set hashes identify the chosen
alternative. The writer derives the exact existing `agent-ee.side-changes/1`
record from that selection and eligibility. Explicit side-change authorization,
if supplied too, must describe exactly the same transitions. The R5 custom-pad,
UUID, movable-member and saved-pad checks still run. Plain `/2` candidates retain
their captured sides and existing admission path.

`blocks.contract` supplies the independent final-coordinate contract and is
required for candidate sets; it remains optional for plain `/2` requests.
Its eligibility record must equal `blocks.side_eligibility`. After native saving
and the existing placement/DRC checks, the writer extracts the saved board again
and writes `block-contract.json`. A required missing measurement or violation
refuses publication; the complete report and planning execution receipt remain
in the failure directory. Successful placement results link the report and its
SHA-256. This check does not establish routing acceptance.

For campaign placement declarations, `blocks.candidates` accepts captured bare
`/2` or `candidate-set/1` files. Candidate sets also require captured `allocation`,
`side_eligibility`, and `contract` JSON paths in that declaration. The adapter
requires every path to be an immutable captured input, records its byte hash,
and carries the full eligibility and contract records into the runner request.
It checks each set against the original captured board, native netlist,
allocation and effective request policy. These package checks do not replace
the writer's native reproduction or final saved-board judgment.

The offline arrangement CLI accepts `--side-eligibility` and `--contract` paths;
`--runner-section` retains both full records. Programmatic `request_section`
accepts the same optional keyword paths; `request_section_from_bytes` accepts
their already-bound bytes. Missing records for a candidate set are refused.

When a selected block undergoes bounded member refinement, its `/2` candidate
hash continues to identify the starting layout. The provenance artifact marks
this explicitly with `candidate_role: "starting-layout"`, records every final
member position, rotation and side, and retains the complete `member_refinement`
witness with its canonical JSON hash. The selected native face and candidate-set
hash are retained even when refinement leaves the layout unchanged. Move records
bind their starting candidate, before/after capture hashes and guarded metrics;
the hash chain must end at the witness's final capture and member poses. Review
input validation compares the complete witness and roster against the published
plan. These identity checks do not substitute for native save/reload or the
independent saved-board contract judgment.

Candidate sets enable a post-global member pass. Optional
`arrangement.refinement: {"step_budget": 64}` sets its separate finite budget
(default 64; zero disables trial moves). Only search-derived, unlocked,
non-anchor members move, within their original selected block window and with
unchanged face and rotation. Contact targets include exact non-ground native
pad-centroid alignments, with integer neighbors for fractional centers. An
accepted move must reduce internal HPWL without increasing whole-board HPWL or
any declared 3D pin distance; all applicable hard placement rules still hold.
Reference layouts stay unchanged. The plan records refinement steps separately
from macro and residual search, plus an explicit unavailable routing-feedback
record. This finite local pass does not establish an optimal placement.

After saving, `block-refinement-verification.json` reconstructs the selected
starting layout from native coordinates and replays each accepted translation.
It independently checks the actual request, candidate identity, final poses,
metrics and hard constraints. Its saved-native capture hashes are distinct from
the planner witness hashes. Failed replay refuses publication and retains the
verification failure beside the saved block-contract report.

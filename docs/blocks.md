# Two-level placement: blocks (AGENTEE-339) and arrangement (AGENTEE-340)

`agent_ee.blocks` derives placement blocks from the sealed schematic hierarchy
and packs each block in isolation into a rigid block candidate (stage A).
[Stage B](#stage-b-arrangement-agentee-340) (`agent_ee.block_arrangement`)
arranges the candidates as rigid macros and packs the residual parts. The block
library (AGENTEE-342) catalogues and imports cited candidates. Neither module
writes a board; the native writer does. The complete board plan must still pass native save/reload,
the [locality grade](placer.md#pad-level-electrical-locality-agentee-322) and the
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
  fits inside. A member that touches the square's edge gives
  `synthetic_plane_reached`, `outcome=unknown`, with no candidate. This happens,
  for example, with a member whose only nets are ground nets: the placer's wire
  cost ignores ground nets.
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
| `members[]` | `reference`, `fpid`, `side`, `value`, `pose`, `pad_geometry_sha256`, `courtyard_sha256`, `courtyard` (the footprint-local courtyard record that `courtyard_sha256` hashes), `pins[]` |
| `outline` | Per side: `box_nm` `[x0, y0, x1, y1]` and the framed outer `courtyards` polygons |
| `grade` | Independent re-grade in the frame: `electrical_locality` and `groups` (each `null` when nothing is internal) |
| `witness` | `exact-courtyard-separation/1`: `gap_nm`, `pairs_checked`, per-side `minimum` exact squared distance `[numerator, denominator]`, `below_gap`, `drilled_pad_conflicts`, `status` |
| `placer_judgment` | Verdict and basis of the placer's full-polygon judge |
| `deferred` | Boundary groups and obligations by id; counts of obstacles and board rule areas |
| `search` | `strategy`, `backjumps`, `steps_used`, `step_budget` |
| `block_policy` | The exact isolated packing policy |
| `sources` | `board_sha256`, `inventory_sha256`, `allocation_sha256` (`null` without an allocation file), `derivation_sha256`, `policy_sha256` |

### Validation and admission

`validate_candidate(document)` needs no board. It raises `ValueError` unless:

- every documented field and nested record is present and well typed, with no
  unknown fields; a malformed nested value is a typed refusal, not a crash;
- each member has one pin record per pad; one role names one net with one
  `boundary` flag, and one net has one role;
- `grade.electrical_locality` and `grade.groups` are `null` exactly when the
  block policy has no internal obligations or groups. Otherwise they pass with
  no defects or unknowns, declare the block policy, measure every internal pair
  or group member once, and each exact squared distance is within its limit;
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
an empty box or an edited polygon therefore refuses.

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
| `stale_evidence` | The board's own footprints at the frame poses reproduce `outline`, `grade` and `witness` exactly, including drilled-pad reservations |
| `evidence_failed` | That re-grade and witness pass |

Identity matching and grading stay separate. `match` compares identity only,
and the graders are the registered checks' own. A candidate from another board
or policy is refused by `admit`. Reusing it there takes `match` with a mapping
plus a re-grade on the target board, which is arrangement (AGENTEE-340) and
library (AGENTEE-342) work.

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
layout. A `hard` class means arrangement must keep the block rigid. Importing
such a layout, and verifying its citation and source, is AGENTEE-342.
`validate_candidate` only checks that the record is present and well formed; a
`search-derived` record has no `source`. It does not verify the cited document.

Unsourced numeric limits are meant to stay advisory, following AGENTEE-331/337.
That is not implemented yet: the intra-block search still enforces every
locality limit in the block policy as hard, so an unsourced bound that is too
tight gives `packing_search_exhausted` rather than a candidate. Carrying each
pair's class and enforcement into `blocks._locality` waits for AGENTEE-337's
bound-provenance contract.

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

Blocks are ordered by `priority_blocks`. Then come blocks that constrain both
board sides: those with a drilled pad (a through-board reservation) or a
hard-shadowed part. The rest follow. Within each group, the next block is the
one that shares the most non-ground nets with the fixed parts and the blocks
already ordered (ties: larger courtyard area, then id). For each block,
candidate origins align to the outline, placed boxes, exact obstacles, group
points and obligation targets, in four quarter turns. The objective terms rank
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
predicates costs one step. A block with no admissible candidate backjumps to
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
keep-outs and shadows are obstacles. A residual failure is the placer's own
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
  `unknown`, which fails the plan.

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
the section from files.

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
for the regulator blocks (AGENTEE-342) are the next levers.

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

All four candidates and the hand clusters pass the internal locality grade and the
0.25 mm courtyard witness. Repeated packing gave identical candidates. The search
pose is shorter than the hand pose on 21 of the 33 pairs. It is longer on 12: the
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
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest tests.test_blocks tests.test_block_arrangement -v
```

`tests.test_blocks` uses authored hierarchical XML, boards, pads and limits.
It covers:

- default and allocated derivation, with every ambiguity refusal;
- board/netlist disagreement and locked members;
- packing with internal and deferred obligations and groups;
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
- the CLI.

A native test derives the two blocks of the [block-first](block-first.md)
fixture from its actual sandboxed `kicad-cli` XML export.

`tests.test_block_arrangement` (stage B) uses the same authored fixture. It
covers:

- rigid arranged poses, residual packing, deferred grading, boundary binding,
  provenance and determinism;
- admission refusals: missing, duplicate and wrong-policy candidates, and a
  recorded net that differs from the board net;
- a mapped library candidate, with undeclared, wrong and correct boundary nets;
- hard and advisory keep-outs, thermal limits and objective order, shadows,
  no block over a fixed part, internal stacking and stranded groups;
- typed search and budget exhaustion, including the backjump bound, and policy
  validation;
- a seeded differential of the multi-box sweep against `placer._unreserved`, and
  the inscribed drilled-pad squares;
- the CLI;
- a native pcbnew board through `placer_runner`, with save, reload, judge,
  locality grade, native DRC and a rigidity check on the saved board.

# Two-level placement, stage A: blocks (AGENTEE-339)

`agent_ee.blocks` derives placement blocks from the sealed schematic hierarchy
and packs each block in isolation into a rigid block candidate. Stage B
(AGENTEE-340) arranges candidates and packs the residual parts. The block
library (AGENTEE-342) catalogues and imports cited candidates. This module
writes no board. The complete board plan must still pass native save/reload,
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
  `electrical_locality` obligations whose endpoints all lie in the block. Board
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
  block policy has no internal obligations or groups. Otherwise they have no
  defects or unknowns, declare the block policy and measure every internal pair
  or group member once. Groups must pass. Locality may be `pass` or `info`, with
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
| `evidence_failed` | Locality is `pass` or advisory `info`; groups and witness pass |

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

Unsourced numeric limits remain advisory, following AGENTEE-331/337. The
isolated policy preserves `source_records` and each pair's provenance. Only hard
pairs prune the search. Candidate production, validation and admission accept
locality `info` with no defects or unknowns and retain every advisory, including
`exceeded` and `below_floor`. Validation checks the stored classification and
advisory status; admission independently re-grades native lands against the
source-bound policy. Sourced distance violations, infeasibility and unknown
coverage still refuse. Group grades and courtyard witnesses must pass.

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

## Real-pod diagnostic

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
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest tests.test_blocks -v
```

`tests.test_blocks` uses authored hierarchical XML, boards, pads and limits.
It covers:

- default and allocated derivation, with every ambiguity refusal;
- board/netlist disagreement and locked members;
- packing with internal and deferred obligations and groups;
- advisory-only and mixed hard/advisory packing, truthful advisory records,
  and source-record preservation through JSON round trips and admission;
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

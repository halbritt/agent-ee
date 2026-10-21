# Sub-layout reuse

`agent_ee.sublayout` (AGENTEE-56) copies the placement and routing of a proven
module layout into new instances of that module on a top board. A sub-board is
a captured `.kicad_pcb` that holds the module's own footprints and copper,
such as a regulator, a USB-C input or MCU decoupling. Reuse is a helper of the
sole implementation writer for stages 4 and 5. It is group-local:
integration routing between groups and the rest of the board remains
kicad-route's job. Its result record is diagnostic. The existing `drc`,
`placement` and connectivity checks judge the board it writes.

```sh
PYTHONPATH=src python3 -m agent_ee.sublayout project/board.kicad_pcb \
  --request build/reuse.json --sub-boards captured/sub-boards \
  --work build/reuse-work --out build/board.kicad_pcb --record build/reuse-result.json
```

The command exits 0 when the reuse ran (whatever its outcome) and 1 when it
failed. `--work` must name a new directory. `--out` and `--record` must be new
files. The input board is never modified.

The mechanism follows the description in AGENTEE-56 of atopile's
`layout_sync`: an anchor footprint with the most pads, pad-vote net mapping,
and copies with new UUIDs. No atopile code was copied. Rotation is added
(upstream lacks it). Copper on nets that cannot be mapped is reported, not
created on net 0.

## Request

Schema `agent-ee.sublayout-request/1`. The document has exactly two keys,
`schema` and `groups`:

```json
{
  "schema": "agent-ee.sublayout-request/1",
  "groups": [
    {"id": "reg-b", "sub_board": "regulator.kicad_pcb",
     "members": {"J1": "J2", "U1": "U2", "C1": "C2"},
     "anchor_at_nm": [160000000, 120000000], "rotation_deg": 90,
     "sub_board_sha256": "optional lowercase hex"}
  ]
}
```

| Field | Rule |
| --- | --- |
| `id` | Unique short identifier. It names the KiCad group `sublayout:<id>` and seeds the new UUIDs |
| `sub_board` | Normal relative `.kicad_pcb` path under `--sub-boards`. No absolute path, `..` or symbolic link |
| `members` | Maps every sub-board footprint reference to one top-board reference. Coverage must be exact, and a top reference may belong to only one member in the whole request |
| `anchor_at_nm` | Integer nanometre target of the anchor footprint's origin |
| `rotation_deg` | Optional, one of 0, 90, 180, 270 (KiCad's positive, counter-clockwise sense) |
| `sub_board_sha256` | Optional. When given, the captured bytes must match it |

## Mechanism

pcbnew runs only in `agent_ee.sublayout_probe`, a child process started
through the production Bubblewrap executor. Networking is unshared and every
board is copied into a read-only input directory. The planner itself is
standard-library Python over the child's facts. The child's `fills` command
reads the saved fill of named zones, refills them on its private copy and
reports both, without saving. The placement write scope uses it
([zone fills](placement-adapter.md#group-local-copper-agentee-129)).

1. **Anchor.** The sub-board footprint with the most pads is the anchor (ties:
   natural reference order). Each member is placed at
   `anchor_at_nm + R(sub position − sub anchor position)`. Its orientation is
   the sub-board orientation plus the rotation, and its side is the sub-board
   footprint's side. Reference and Value fields take the sub-board's
   transformed positions, angles, layers and visibility.
2. **New footprints only.** A layout is pulled only when every top-board
   member is new: unlocked, and with its bounding box wholly outside the top
   board's Edge.Cuts bounding box. That is the off-board staging row that
   [board building](board-build.md) and
   [forward annotation](forward-annotation.md) use. A top board without an
   outline, or any placed or locked member, refuses the group.
3. **Land patterns.** Each pair must have the same FPID and the same pads, by
   number and by local position (B-side local y is mirrored back). Sub-board
   footprints must sit at quarter-turn orientations, so every transformed
   coordinate is exact integer nanometres.
4. **Net vote.** Every pad of every pair votes for the top net of its paired
   top pad. A top pad without a net abstains. A sub net is `mapped` when every
   voting pad agrees, `contested` when votes split, and `unmapped` when no pad
   votes. Two sub nets may map to the same top net (the top design merged
   them). A split vote is never resolved by majority, because copying its
   copper would short the dissenting top nets.
5. **Copy.** Tracks, arcs, vias, zones (copper and rule areas) and graphics
   other than Edge.Cuts and Margin are duplicated. Each copy is moved and
   rotated about the anchor target, and receives the UUID
   `uuid5(namespace, [id, sub-board sha256, source uuid])`. Copper on a mapped
   net takes the voted top net, and unnetted sub-board items stay unnetted.
   Copper on an `unmapped` or `contested` net is **withheld** and listed with
   its source UUID and reason. The copied zones, and only those, are refilled.
   The members and copies form the KiCad group `sublayout:<id>`.
6. **Verification.** A fresh child reads the saved board. Footprints outside
   every group, existing items and existing groups must have unchanged facts. Group footprints, every pad position, the copied fields and every
   copied item's kind, net, layers, width, drill and points must equal the
   planned transform exactly. Any difference fails the run, and no board is
   published.
7. **DRC delta.** Native `kicad-cli pcb drc --severity-all` runs in the same
   sandbox on the input board and on the result. A violation of the result is
   *new* when it names an item of the group or lies inside the group's
   bounding box, and no input violation has the same type and item UUIDs.
   Pre-existing violations that only moved, such as a footprint's
   `lib_footprint_issues`, are therefore not new.

Refusals and unknowns are per group. Other groups in the same request still
apply.

## Result

Schema `agent-ee.sublayout-result/1`, sealed with `canonical_sha256`. It
records the top-board sha256, the request identity, the output path and
sha256, the DRC report hashes and counts, and one entry per group:

| Group `status` | Meaning | Group `outcome` |
| --- | --- | --- |
| `populated` | Every item copied | `pass` with no new DRC violation, `defect` otherwise |
| `partial` | Some copper withheld (`withheld`, `net_map`) | `unknown`, or `defect` with a new DRC violation |
| `refused` | A precondition failed (`not_new`, `member_coverage`, `footprint_mismatch`, `land_pattern_mismatch`, `orientation_unqualified`, `layer_stack`, `unsupported_item`, `no_outline`, `top_reference`, `uuid_collision`) | `unknown` |
| `unknown` | The sub-board is missing (`sub_board_missing`), differs from the declared sha256 (`sub_board_changed`) or cannot be read (`sub_board_unreadable`) | `unknown` |

`sub_board` always records the path, the observed sha256 (`null` when the file
is absent) and any declared sha256. Populated groups also report `anchor`,
`net_map` (with vote counts and abstentions), `copied` counts by kind,
`unnetted_copper` (copper the sub-board itself left without a net),
`sub_offsets_nm` and `bbox_nm`. When DRC cannot run, every applied group is
`drc: not_evaluated` and `unknown`. The overall `outcome` is `defect` if any
group is a defect, `unknown` if any group is not `pass`, and `pass` only when
every group is populated with no new violation inside its box. When no group
applies, no board is written.

## Evidence-model fit

Sub-boards are captured inputs, bound by sha256 in the plan, in the result
and in the child. Inside a campaign, the placement binding records the request
and sub-board digests, and the write scope admits only copies whose UUIDs derive
from those bytes. The child refuses bytes that differ from the plan. The
operation runs offline in the networkless sandbox. Reuse grants no
acceptance: the coordinator's fresh `drc` and `placement` checks, and
kicad-route for integration routing, decide whether the candidate is done.

## Limits

- Only quarter-turn rotations and quarter-turn sub-board footprint
  orientations are qualified. Mirroring a whole group is not supported.
- Board-level item classes other than tracks, arcs, vias, zones, shapes and
  text (for example dimensions, tables and images) refuse the group.
  Sub-board KiCad groups are ignored.
- Binding is by explicit reference mapping. Hierarchical-sheet (path) binding
  is not implemented.
- In a staged campaign, a placement iteration admits the copper that reuse
  writes only when the placement declaration names the captured request and
  sub-boards (`sublayout`, AGENTEE-129). The adapter then re-plans each group
  from the captured bytes and admits exactly whole groups whose members were
  new, as described under
  [group-local copper](placement-adapter.md#group-local-copper-agentee-129). Without
  that declaration the copper is refused as before.
- DRC runs without a schematic, so schematic parity is not part of the delta.
  Unconnected items between groups are integration work. The DRC report
  retains them, but they are not graded here.

Tests: `tests/test_sublayout.py`, over the owned routed module
`fixtures/generation/regulator.kicad_pcb` and the owned top board
`fixtures/sublayout/top.kicad_pcb` ([provenance](../fixtures/sublayout/PROVENANCE.md)).

AGENTEE-331 carries the top project's native `.kicad_pro`, `.kicad_prl` and
`.kicad_dru` into both sandbox DRC inputs. The provenance classifier retains
classes and citations in `drc/constraints.json` and each new finding. Numeric
defaults are advisory, reported in `new_violations` and `advisory_findings`, and
do not alone make a group defective. Sourced numeric and structural findings
still grade. The saved-board copper containment child contributes engineering
witnesses for effective copper beyond the outline, including cutouts.
Numeric delta identities include measured descriptions and absolute item geometry;
a moved item cannot reuse its old UUID to hide a worsened edge or hole gap.

# Local copper access (diagnostic)

AGENTEE-386 implements the first slice (M3a) of
[routability plan M3](research/2026-10-21-routability-design.md#m3--qualify-local-access-before-block-freeze-59-days-split-capture-and-selection-prs).
It captures raw routing geometry from a complete candidate board and reports, for
each conductive terminal, source-bound access **witnesses** or an explicit
**unknown**. It is diagnostic only. It does not rank, select, reject or change a
placement, a block candidate or its admission. Ranking is M3b (AGENTEE-387). M3b
is gated on a qualified placement ablation.

## Capture: `agent-ee.routing-geometry/1`

`placement_probe.routing_geometry(board)` is an opt-in capture, separate from
`extract`. Packing reservations are enlarged, so they are never read as raw
copper. The capture loads the saved board with its same-stem project and
records:

- **Pad copper.** Each pad's copper on every copper layer, as native outer
  (`ERROR_OUTSIDE`) and inner (`ERROR_INSIDE`) polygon bounds at the board's max
  error.
- **Pad facts.** Drill or slot outline and plated/NPTH status, native own
  clearance per layer, net, number and UUID.
- **Unsupported pads.** A pad with a custom shape or a per-layer padstack is
  marked `unsupported` with a reason. Its native outer copper still obstructs
  other terminals.
- **Board items.** Existing tracks (an arc is `unsupported`), vias with their
  spanned layers, copper zones, the Edge.Cuts outline, and board and footprint
  rule areas.
- **Rules.** The native own clearance of a trial track on each net and layer, and
  the board rule floor: minimum clearance, hole clearance, hole-to-hole,
  copper-edge clearance, minimum track width, via diameter, annular ring and
  drill.
- **Custom rules.** A native `.kicad_dru` is recorded by hash and rule names.
  Its rules are not evaluated.

The capture refuses if the saved board changes while it is being read.

```sh
PYTHONPATH=src python3 -m agent_ee.placement_probe board.kicad_pcb --routing-geometry --out geometry.json
```

## Analysis: `agent-ee.placement-access/1`

`placement_access.analyze_access(geometry, poses, routing_policy, limits)` is pure.

**Routing policy.** The policy declares `track_width_nm`, `routing_layers` and
approved through `vias` (`diameter_nm`, `drill_nm`). A value below the captured
rule floor refuses.

**Terminal components.** Same-net pads whose native inner copper bounds
(`ERROR_INSIDE`, a subset of the real copper) touch on a shared layer form one
terminal component, so a join is physical contact. Outer bounds
(`ERROR_OUTSIDE`) that touch never join on their own. Two pads within the
max-error rim can still have a real gap that native KiCad reports as a missing
connection. Such pads stay separate components, each listing the other under
`unresolved_contact`. The extra obligation is conservative. Pads that share a
number keep their own UUIDs. An `unconnected-*` net and a pad without copper
(NPTH) are `not_required`, and so is the only component of a net that has no
same-net track, via or zone.

**Candidates.** The finite, deterministic candidate set is tested in this order:

1. Direct straight or one-bend links to a same-net component within
   `direct_max_nm`.
2. Straight exits in four normal and four diagonal directions to a local shell
   around the footprint's copper. The shell is grown by clearance plus track
   width unless `shell_nm` is set.
3. One-bend exits. The first-leg lengths come from the pad boundary, the
   footprint copper and the straight blocker, each plus the required clearance
   and snapped to the grid.
4. Short stubs to an approved through via that reaches another routing layer.

**Search limits.** The limits (`max_witnesses` 8, `max_candidates` 96,
`direct_max_nm` 5 mm, `grid_nm` 0.01 mm) are a search budget, not physical limits.

**Clearance model.** The model is conservative:

- Different-net copper is grown by half the track width plus the largest of the
  board minimum, the track's native own clearance and the obstacle's native own
  clearance.
- Foreign drills and slots (NPTH included) use the hole clearance.
- A via also keeps the hole-to-hole distance, and its copper is checked on every
  copper layer it spans. A layer that is not a routing layer still obstructs a
  through via.
- The outline uses the copper-edge clearance.
- Rule areas block only the item kind they forbid.
- Courtyards are not copper.

**Results.** Each terminal gets one of:

- `witness`: alternative clear paths, each with its path, layer, via, minimum
  margin and limiting obstacle. Witnesses are alternatives, not compatible
  reservations. `truncated` reports a reached witness cap.
- `unknown`: no witness in the finite search (`no_witness_in_finite_search`), an
  exhausted budget (`search_budget_exhausted`), unsupported geometry, or global
  coverage limits. The attempted count and blockers are retained.
- `not_required`.

There is no `proven_no_access`, and a failed search is never infeasibility.

**Unknown coverage.** Some board facts are reported as unknown, never guessed
around:

- Native custom rules, an unproven outline, an unsupported rule-area outline, an
  arc track or a blind or buried via make every terminal unknown (`coverage`).
- In a block sidecar without the saved board, the rules themselves are unbound,
  and every terminal is unknown (see Rule authority below). `analyze_access` is
  pure and non-authoritative: it searches with whatever rules it is given.
- A different-net copper zone makes its layers unknown for the terminal, and no
  via is proposed through it.

**Poses.** `poses` maps a reference to a quarter-turn pose change. `AccessIndex`
applies the change with `placer.rotate`. Its `push` and `pop` restore the index
exactly, and the result of `push` equals a full recomputation.

```sh
PYTHONPATH=src python3 -m agent_ee.placement_access analyze --geometry geometry.json \
    --policy policy.json --out access.json
```

## Native authority: `qualify`

`placement_access.qualify(board, groups, out)` checks each group of witnesses on
its own isolated copy of the board:

1. Check each witness's claim against a fresh native capture of the board,
   before any copper is written. Native DRC proves only copper legality and that
   no opens were added. A shortened or retargeted witness is legal copper too.
   The checks are:
   - Every witness starts inside the source pad's inner copper on its layer, on
     the source pad's net, at no less than the minimum track width.
   - A `direct` witness ends inside the inner copper of a pad of its named
     `target`, a different same-net terminal component.
   - An `exit` witness ends outside the owning footprint's copper box grown by
     clearance plus half the track width.
   - A `via` witness ends at a through via that meets the via rule minima and
     reaches another copper layer.

   Before these checks, `kind` must be one of the three names (a list or
   object kind is refused, not raised), and each witness record must match its
   kind exactly:
   `target` only on `direct`, `via` only on `via`, `direction` never on
   `direct`, no unknown key, and a `via` record with exactly `diameter_nm`,
   `drill_nm`, an integer `position_nm`, `layers` and `reaches`. An empty group
   (or one that is not a list) is refused: zero witnesses are zero evidence.

   Any failure refuses the group with `semantic_findings`. A malformed witness
   or empty group is never materialized. A writable refused group still runs
   the native steps below, so its native findings are reported too.
2. Copy the board and keep its rule files byte-identical. pcbnew rewrites the
   project file on save, so the source bytes are restored before DRC.
3. Add the witness tracks and vias on the source net. Native pcbnew connectivity
   must show each witness's first track touching its source pad. For a `direct`
   witness, its last track must touch a target pad.
4. Run `kicad-cli pcb drc --refill-zones` in the existing sandbox.
5. Compare the findings with the DRC of the unmodified copy.

A group is `qualified` only when three conditions hold:

- Every witness passes the semantic and native connectivity checks.
- The DRC adds no rule finding other than the dangling-end warnings that an open
  stub carries.
- The DRC does not add an unconnected item.

Execution failure raises. It is never a pass. Local witnesses alone do not
establish simultaneous routability.

## Block sidecar: `agent-ee.block-access/1`

`placement_access.block_access(candidate, geometry, routing_policy, capture=capture, board=None)`
writes the optional sidecar for an `agent-ee.block-candidate/2` placed on a
complete board. `capture` is the `agent-ee.placement-probe/1` capture
(`placement_probe` without `--routing-geometry`) of the same saved board. It
runs in diagnostic mode only:

- **Reads, never writes.** It reads the strict candidate/2 and binds its
  canonical digest. It adds no field to that schema.
- **Contour authority.** With `board` (the saved board file), the board's
  SHA-256 must be the captures' and both captures must equal fresh native
  captures of it (`routing_geometry`, `extract`). The board, `.kicad_pro` and
  `.kicad_dru` bytes are read once into a private snapshot directory, and a
  fresh interpreter (`python -m agent_ee.placement_probe`) captures that
  snapshot. A failed child capture refuses. Every native contour, drill
  and rule is then authoritative, and `contour_authority` is `native board`.
  Without it, `contour_authority` is `placement capture` and only what the
  placement capture specifies is bound, as below.
- **Rule authority** (review-ee386d H1). The placement capture records no board
  rules. The rule floor, native own clearances and custom-rule presence beside
  it are editable inputs. Lowering a 10 mm floor to 0, or erasing a present
  custom rule, used to turn ten unknowns into ten witnesses at an unchanged
  capture and candidate. Rules are therefore authoritative only from the saved
  board. With `board`, `rule_authority` is `{status: bound, board_sha256,
  project_sha256, custom_rules_sha256}`: the SHA-256 of the board,
  `.kicad_pro` and `.kicad_dru` of that snapshot (`null` when absent), which
  are exactly the bytes the fresh interpreter loaded. The custom-rule hash must
  be the one captured in the routing geometry. pcbnew can cache a loaded
  project within one process (review-ee386e H1): a project edited after a
  first load in the caller's process still reads the earlier rules there, for
  example a 0 nm floor under a 10 mm project. Those captures differ from the
  fresh snapshot capture and refuse, so cached rules are never bound. A
  project or custom rule file that changes after the sidecar also refuses. Without `board`, `rule_authority` is `{status:
  unbound, reason}`. That reason is a coverage reason, so every terminal is
  unknown, never witnessed. A sidecar cannot relabel its rules as bound or drop
  that reason.
- **Capture binding** (`bind_capture`). The two captures must name the same
  board SHA-256 and max error, the same footprints at the same poses and the
  same pads by UUID, with equal number, net, position, orientation, shape,
  attribute and drill. Any difference refuses.
  - Circle, rectangle and oval lands are fully specified by the capture's size,
    copper offset and orientation, so their contours are bound shape by
    shape. On each copper layer the inner bound must lie inside the land and
    contain it shrunk by the max error. The outer bound must contain the land
    and lie inside it grown by the max error. Both hold within 2 nm of
    rounding. A notch (even 8 % of the area at unchanged extents), cut corners
    or a squared circle refuses. Drill contours are bound the same way to the
    captured drill.
  - The capture does not record a rounded rectangle's radius or a chamfer, and
    has no box for a trapezoid or custom land. Those pads, and pads whose
    copper-layer set differs from the land's, are listed in `unbound_pads`.
    They are analysed as unsupported, so their terminals stay unknown. They
    prove no contact (no inner copper), so they never join a component. A
    direct witness into such a pad is dropped. As obstacles, they use their
    conservative size box grown by the max error on every layer where their
    copper is not bound, including a copper layer the routing geometry left
    out. Copper with no captured box (trapezoid, custom) makes coverage
    unknown. Removing a pad's copper from the routing geometry therefore never
    drops its obligation.
  - An NPTH carries copper only when its land exceeds its drill.
  - A drill without a native contour obstructs as its drill box.
- **Member evidence.** Each member's land-pattern and courtyard hashes are
  recomputed from the capture (`blocks.pad_geometry_sha256`,
  `blocks.courtyard_sha256`) and must equal the candidate's. Its footprint,
  side and per-pad nets must equal the candidate member and its pins.
  Missing, changed or foreign member evidence refuses. `member_evidence`
  records each member's pads by UUID, number, net and copper.
- **Pose check.** Each member must sit on the board at its frame pose relative to
  the anchor. Otherwise the sidecar refuses.
- **Contents.** The candidate's pose-independent pad and courtyard hashes, the
  capture digest, member evidence, unbound pads, internal and boundary net
  roles, member terminals with their witnesses and unknowns, coverage, policy
  and limits.
- **Recheck.** A witness that uses space outside the block must be rechecked
  after arrangement.

`access_status(candidate, sidecar=None, *, geometry, capture, board=None)`
reports `unknown` when there is no sidecar. A sidecar without the routing
geometry and placement capture it binds refuses. With them, the status comes
from `validate_sidecar`, never from the supplied counts or terminal list.
Validation has two steps, and any omission or inconsistency raises:

1. Structure:
   - The exact field set and the candidate binding: digest, block, status,
     frame and member hashes.
   - Member evidence against the candidate pins.
   - Policy, limits and search model.
   - Coverage: `unknown` exactly when it has reasons, and then no witness
     terminal.
   - Rule authority: `bound` to the sidecar's board for `native board`. For
     `placement capture` it must be `unbound`, with that coverage reason. A
     witness terminal needs bound rules.
   - Terminals: they must cover exactly the member copper pads with their
     nets. A witness terminal needs witnesses whose forms are valid and whose
     source, net, layer, width and via match its terminal and policy.
   - The summary: it is recounted from the terminals and must equal the
     supplied one.
2. Re-derivation. The sidecar's records are editable, so they cannot establish
   their own obligations. The sidecar is re-derived from the geometry and
   capture it binds, using its policy and limits, and must equal it exactly.
   The digests must match first. A `native board` sidecar also needs its
   board. Pad identity, copper participation, the terminal partition,
   `not_required` exemptions and witnesses therefore come from the bound
   evidence. Clearing copper flags and deleting terminals refuses. So does
   deleting a real unknown, or inventing an exemption, even with a recount.

Admission (`blocks.admit`) is unchanged: it does not read the sidecar.
With the feature off, candidate identity, bytes and member order are exactly the
legacy ones.

```sh
PYTHONPATH=src python3 -m agent_ee.placement_access block --candidate block.candidate.json \
    --geometry geometry.json --capture capture.json --board board.kicad_pcb --policy policy.json \
    --out block-access.json
```

## Tests

`tests/test_placement_access.py` covers the following:

- An authored native board, saved and reloaded in pcbnew. It has rotated and
  mirrored footprints, slots, NPTH, a custom pad, same-number pads, keepouts, an
  existing track and via, a narrow escape and an enclosed terminal.
- Quarter-turn poses compared against saved pcbnew geometry.
- Mirroring compared against an independent reflection.
- Every witness on the fixture passes native DRC, and a forged witness is refused.
- Terminal contact. Two 1 mm same-net lands with an 8.63 µm gap overlap only in
  their outer bounds. They keep the connection obligation that native DRC
  reports. Truly overlapping lands and a four-piece exposed pad are one
  component, with no native opening.
- Witness semantics. A full direct link qualifies and removes one native
  opening. A direct link shortened short of its target, a retargeted direct link,
  an exit that stays on its own land and a witness on a foreign source are all
  refused. Native DRC alone passes the shortened link. A malformed witness is
  never written.
- Witness forms. Direct, exit and via witnesses qualify. The same witnesses with
  a key of another kind are refused before materialization: a direct witness
  with `via: {}` (formerly a `KeyError`), with a full via, or with a direction;
  an exit with a target; and a via record that is incomplete, extended or
  textual. Empty groups are refused too.
- Capture binding. The authored board's rotated, mirrored and drilled circle,
  rectangle and oval lands bind to its native placement capture by contour. The
  rounded rectangles, the trapezoid and the custom land stay unbound; the
  rounded ones obstruct as their size box, and the other two make coverage
  unknown. A changed copper extent, drill or custom-land support refuses. So
  do these contour forgeries: J1/J2 inner circles squared to their bounding
  boxes, which would join them and erase the 8.63 µm native gap; an 8 % notch
  at unchanged extents; and cut corners.
- Sidecar evidence. Removed, renetted or re-identified member pads, changed
  copper, a changed max error, another board or a missing capture all refuse.
  This includes U23 on the pod candidate. Summary-only sidecars, missing
  fields, unknown coverage with witnesses, dropped terminals, promoted unknowns
  and inconsistent counts refuse too. Self-consistent forgeries refuse on
  re-derivation: suppressed copper flags, a deleted real unknown and an
  invented exemption. Copper stripped from the routing geometry stays an
  unknown obligation.
- Rule authority (review-ee386d H1, agent-510's probe). At the fixed capture
  and candidate, a 10 mm floor, a floor lowered to 0, present custom rules,
  erased custom rules and a missing custom-rule record all give ten unknowns
  and status `unknown` through `block_access` and `access_status`. Relabelled
  or dropped unbound-rule statements refuse. On the native pod board, the
  sidecar binds the board and project hashes. A substituted rule floor or
  custom-rule record refuses against the native capture. A forged project or
  custom-rule hash refuses on re-derivation. A native `.kicad_dru` makes every
  terminal unknown, and deleting it afterwards refuses. Changing `.kicad_pro`
  after the sidecar also refuses. Witness-bearing forgeries (unknown coverage,
  missing witnesses, foreign sources, cross-kind keys, invented exemptions) run
  on the native pod sidecar, which witnesses.
- Witness kinds. A list or object `kind` is refused before materialization. It
  formerly raised `TypeError`.
- The exact U23/C87 replay from the pinned fome-mc store. It includes the actual
  sensor-power-front candidate sidecar under native board authority, and those
  witnesses also pass native DRC. A routing geometry that the board does not
  reproduce refuses. Under placement-capture authority alone, no witness uses
  an unbound land.

Native tests require the toolchain and may not skip under
`AGENT_EE_REQUIRE_TOOLCHAIN=1`.

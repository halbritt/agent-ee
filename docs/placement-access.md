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

**Terminal components.** Same-net pads whose copper already touches form one
terminal component. Pads that share a number keep their own UUIDs. A net with
only one terminal, an `unconnected-*` net and a pad without copper (NPTH) are
`not_required`.

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

1. Copy the board and keep its rule files byte-identical. pcbnew rewrites the
   project file on save, so the source bytes are restored before DRC.
2. Add the witness tracks and vias on the source net.
3. Run `kicad-cli pcb drc --refill-zones` in the existing sandbox.
4. Compare the findings with the DRC of the unmodified copy.

A group is `qualified` only when two conditions hold:

- The DRC adds no rule finding other than the dangling-end warnings that an open
  stub carries.
- The DRC does not add an unconnected item.

Execution failure raises. It is never a pass. Local witnesses alone do not
establish simultaneous routability.

## Block sidecar: `agent-ee.block-access/1`

`placement_access.block_access(candidate, geometry, routing_policy)` writes the
optional sidecar for an `agent-ee.block-candidate/2` placed on a complete board.
It runs in diagnostic mode only:

- **Reads, never writes.** It reads the strict candidate/2 and binds its
  canonical digest. It adds no field to that schema.
- **Pose check.** Each member must sit on the board at its frame pose relative to
  the anchor. Otherwise the sidecar refuses.
- **Contents.** The candidate's pose-independent pad and courtyard hashes,
  internal and boundary net roles, member terminals with their witnesses and
  unknowns, coverage, policy and limits.
- **Recheck.** A witness that uses space outside the block must be rechecked
  after arrangement.

`access_status(candidate, sidecar=None)` reports `unknown` when there is no
sidecar. Admission (`blocks.admit`) is unchanged: it does not read the sidecar.
With the feature off, candidate identity, bytes and member order are exactly the
legacy ones.

```sh
PYTHONPATH=src python3 -m agent_ee.placement_access block --candidate block.candidate.json \
    --geometry geometry.json --policy policy.json --out block-access.json
```

## Tests

`tests/test_placement_access.py` covers the following:

- An authored native board, saved and reloaded in pcbnew. It has rotated and
  mirrored footprints, slots, NPTH, a custom pad, same-number pads, keepouts, an
  existing track and via, a narrow escape and an enclosed terminal.
- Quarter-turn poses compared against saved pcbnew geometry.
- Mirroring compared against an independent reflection.
- Every witness on the fixture passes native DRC, and a forged witness is refused.
- The exact U23/C87 replay from the pinned fome-mc store. It includes the actual
  sensor-power-front candidate sidecar, and those witnesses also pass native DRC.

Native tests require the toolchain and may not skip under
`AGENT_EE_REQUIRE_TOOLCHAIN=1`.

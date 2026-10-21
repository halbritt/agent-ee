# Footprint access direction (AGENTEE-379)

A socket's card travel, a connector's cable exit and an antenna's outward lane
are physical requirements beyond a courtyard. Put a JSON declaration in the
native footprint field `agent_ee_access_direction`. The placement probe retains
its text under `access_direction`; standalone integer-geometry probes may use
the equivalent object. The property is preserved by the placement writer.

This original example declares a test socket. Its dimensions and citation are
fixture requirements, not Hirose, TE or Espressif specifications:

```json
{
  "schema": "agent-ee.access-direction/1",
  "mouth_nm": [2000000, 1000000],
  "outward": "x+",
  "lane_polygons_nm": [
    [[2000000, 0], [32000000, 0], [32000000, 2000000], [2000000, 2000000]]
  ],
  "provenance": {
    "class": "fixture_author",
    "enforcement": "hard",
    "citation": "Original EE379 test requirement: outward lane, 30 mm travel."
  }
}
```

`mouth_nm` and every polygon vertex are integer nanometres relative to the
footprint origin at zero native rotation on its current mounting side. `outward`
is `x+`, `x-`, `y+` or `y-`. Positive native quarter turns rotate `(x, y)` to
`(y, -x)`. Back-side declarations use these same board axes at zero rotation;
there is no extra inferred mirror. Authors must transform a declaration when
flipping a footprint to the other mounting side. Only quarter-turn poses and
straight perpendicular outline edges qualify. The lane must touch its mouth
and lie wholly forward of it. Polygon validation and bounds reuse the existing
placement geometry qualification. Native property text is bounded to 8192 bytes.

The mouth must lie on a proven outline edge, face outward perpendicular to that
edge, and have its entire lane outside the board. The lane must fit in that
edge's outward swept band, using the same exact geometry as
[fixed courtyard overhangs](placer.md#real-pod-residuals-agentee-212).
Its depth comes from the declared lane, without an agent-chosen numeric limit.
The complete outline is checked, so another arm of a concave board cannot
occupy the lane. Footprint-owned packing exclusions also reserve sourced lanes
against same-side peer courtyards, including external overhanging bodies.
Curved peers prove obstruction only through their inner bound; an outer-only
intersection remains unknown. A remote edge along a ray is insufficient: it would leave an
inboard access lane. A cited lane also authorizes courtyard area outside the outline, solely inside
that lane, for fixed, movable and block-member parts. This admits a slot mouth
inside the footprint's courtyard margin without clipping or changing its native
courtyard. An explicit captured envelope overhang maximum retains precedence;
the access declaration cannot enlarge it. Only the exit edge is exempt from a
packing edge margin; other edges retain that margin. Uncited lanes authorize no
courtyard overhang. Existing fixed-overhang policy declarations remain captured,
envelope-bound inputs; a caller cannot supply them as packing-policy authority.
Native rule areas and courtyards are preserved, including a courtyard that
already includes the lane.

Citations use the existing [constraint source qualification](placer.md#obstacle-and-group-sources-agentee-361).
Manufacturer/datasheet sources must identify the drawing, revision and location
that establishes both direction and clearance dimensions. An owner-recorded
board decision may use the existing `board` class with its decision locator.
`fixture_author` is for original test requirements. The other existing geometry
source classes retain their existing semantics. A citation locates declared
evidence; this feature does not authenticate its authority (AGENTEE-348).
An omitted provenance record or missing/blank citation is advisory, even when
the declaration requests hard enforcement. It changes no candidates, pruning,
score or pass verdict, and the measured violation remains visible. A malformed
record, invalid source or unqualified sourced pose is unknown coverage, never a
pass. A lane outside the board with no qualified exterior overhang band also
remains unknown; lack of a sweep proof is not a proved inboard defect. An unsupported uncited rotation remains advisory.

The flat placer adds mouth/lane edge-alignment coordinates only for sourced
parts, then checks each candidate. Block stage A defers this board-bound
obligation while packing its artificial local rectangle. Stage B recovers the
declaration from the hash-bound captured board, constrains each rotated and
translated member, and residual packing enforces it again for arranged members
and movable residual parts. The declaration belongs to the target board even
when importing a block library candidate. Search keeps existing ranking and
step guards; it introduces no wall-clock default. Area proofs subtract only the court/lane
intersection from a cited part's required onboard area. Whole-part width and
diameter proofs cannot reject that part from its overhanging extent. Grown-area
and fragmentation diagnostics omit movable overhang parts (listed explicitly),
and region proofs omit them only where a region reaches outside the board.
These are conservative lower bounds, not claims that the omitted parts fit;
exact candidate and saved-board predicates still decide the complete proposal. Finite search exhaustion is
unknown infeasibility, with an access reason or stage-B rejection count.

The independent polygon judge appends sourced inboard poses as
`access_direction` hard failures. Existing geometric defects survive unknown
access coverage. It records `access_directions` with transformed mouth, vector,
lane, inboard area, provenance and the admitted overhang band. Uncited records
also appear in `advisories`. The saved-board writer repeats judgment after
native save, retaining `placement-judgment.json`. The existing
`outline-containment/1` check includes access failures, measurements and
advisories in its sealed evidence; normal check artifacts are available to the
review packet. No new check kind or acceptance authority is registered.

Executable coverage is in `tests/test_access_direction.py`: a 600 mm² original
lane example, both mounting sides and all four edges, sourced/advisory search
parity, impossible lanes, concave obstruction, malformed input, stage-A/block
stage-B/residual behavior and native placement publication with saved-board
replay. These fixtures do not reproduce the historical fome-mc iteration-7 or
8k run. That design's actual declaration and repack remain a pod qualification
obligation. Internal owner-declared openings, arbitrary-angle access vectors,
curved edges and arbitrary native rotations are not qualified here.

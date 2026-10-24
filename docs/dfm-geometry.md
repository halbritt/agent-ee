# DFM geometry rules

`dfm_geometry` runs eight deterministic manufacturing rules on the saved board.
The thresholds come from a captured profile. Each rule reports coded findings
with coordinates and references. There is no aggregate score.

The rule list and the example thresholds are ported from AI-PCB-Generator
`src/pcb/dfm.py` (MIT). Its geometry is not ported: the source estimates
features from pad centroids and body radii. agent-ee measures exact pcbnew facts:
pad copper polygons, track and arc endpoints, via drill and size, courtyards,
the saved board thickness, and united per-layer copper area. The additional
`smd_via_clearance` rule is an owned implementation for assembly clearance.

General electrical copper width and clearance are not in this suite. Use
[copper-width/1](check-reference.md#saved-board-geometry) and
[DRC](check-reference.md#drc-and-schematic-parity) for them. Board-edge copper
clearance comes from the [fab profile](fab-profile.md). The suite has no etch
compensation, thermal relief, paste or panel rules.

## Check declaration

```json
{"id": "dfm", "kind": "dfm_geometry", "params": {"dfm_profile": "project/dfm-profile.json"}}
```

`params` must contain only `dfm_profile`, the candidate-relative path to a
captured profile. The profile is an immutable input. Each rule contributes one
coverage predicate, `dfm:RULE_ID`. Launch preflight requires `pcbnew`.

The handler runs this command in the sandbox (900 s ceiling):

```
/usr/bin/python3 -B -m agent_ee.geometry_probe BOARD -o /work/geometry.json --dfm
```

`--dfm` adds a `dfm` section to the normal probe report and leaves the rest of
the report unchanged. The coordinator evaluates that section with
[dfm_geometry.py](../src/agent_ee/dfm_geometry.py), a pure stdlib function of
the probe output and the profile. The check retains `geometry.json` and the
candidate-bound `dfm-report.json`.

## Profile

```json
{"schema": "agent-ee.dfm-profile/1", "name": "owned-dfm",
 "basis": "thresholds ported from AI-PCB-Generator src/pcb/dfm.py (MIT)",
 "rules": [
  {"id": "annular-ring", "rule": "annular_ring", "min_ring_mm": 0.15},
  {"id": "via-aspect", "rule": "via_aspect_ratio", "max_ratio": 8},
  {"id": "acid-trap", "rule": "acid_trap", "min_angle_deg": 45},
  {"id": "smd-bridge", "rule": "smd_bridge_gap", "min_gap_mm": 0.2},
  {"id": "component-spacing", "rule": "component_spacing", "min_gap_mm": 1.0},
  {"id": "diff-skew", "rule": "diff_pair_skew", "max_skew_mm": 2.0, "pairs": "name-suffix/1"},
  {"id": "copper-balance", "rule": "copper_balance", "max_layer_share": 0.85},
  {"id": "smd-vias", "rule": "smd_via_clearance", "min_gap_mm": 0.1}]}
```

The first seven values above are the source's thresholds; the SMD/via value is
an example engineering margin. agent-ee has no built-in values,
so every rule must state its own threshold fields and nothing else. `basis` is
optional free text, allowed on the profile and on each rule. Rule ids must be
unique, and a rule kind may appear more than once with different thresholds.
Thresholds must be finite and positive. `min_angle_deg` must be below 180 and
`max_layer_share` must lie between 0.5 and 1. Any other profile shape is
`unsupported` at capability time.

## Rules

| Rule | Code | Measurement | Finding when |
| --- | --- | --- | --- |
| `annular_ring` | DFM-002 | Via ring `(size − drill)/2` in integer nm. PTH pad ring is exact for rectangle pads (drill offset included), and for circle, oval and roundrect pads with no offset | ring < `min_ring_mm` |
| `via_aspect_ratio` | DFM-008 | Saved board thickness ÷ through-via drill | ratio > `max_ratio` |
| `acid_trap` | DFM-003 | Angle between adjacent copper arms that share an exact endpoint on one layer. Arcs use their endpoint tangent | 0 < wedge < `min_angle_deg` |
| `smd_bridge_gap` | DFM-010 | Exact distance between native pad copper polygons. Only SMD pads on different footprints and different nets are compared. A pad with no net counts as its own net | gap < `min_gap_mm` (overlap is gap 0) |
| `component_spacing` | DFM-006 | Exact distance between native courtyard polygons of footprints on the same side | gap < `min_gap_mm` |
| `diff_pair_skew` | DFM-012 | Routed length per net: tracks plus true arc length (radius × sweep). Via barrels are excluded | difference > `max_skew_mm` |
| `copper_balance` | DFM-009 | Area of the united copper (pcbnew polygon union of tracks, vias, pads, copper graphics and filled zones) on each mirrored layer pair | heavier layer's share > `max_layer_share` |
| `smd_via_clearance` | DFM-013 | Gap from the complete via copper disk to native SMD pad copper on each shared outer layer, including same-net pairs | gap < `min_gap_mm` |

Pad polygons use pcbnew's `ERROR_OUTSIDE` approximation, so curved pads are
never drawn smaller than they are. The probe records the error as
`max_error_nm`. Rectangles are exact.

SMD/via comparisons use exact rational squared distances for the threshold;
rounded millimetres appear only in reports. This detects annular-ring overlap
even when the via center lies outside the pad. Custom SMD shapes, missing pad
polygons/layer inventories and per-layer padstack sizes refuse coverage. The
rule does not compare PTH pads, mask or paste openings. Apply additional checks
when those manufacturing features impose separate constraints.

The acquisition qualification exercises the rule through ordinary native review
on two actual routed candidates: the first fails with five copper overlaps over
351 comparisons; the routing-policy repair passes all 390 comparisons at a
0.1 mm declared gap. Native controls also reject a same-net via in a solder pad,
and exact boundary tests distinguish a one-nanometre shortfall. Retained evidence:
`build/agent545/smd-via-qualification/`, with 40 native DFM tests passing in
`build/agent545/smd-via-clearance-native-final.log`. This does not by itself qualify
the full design or manufacturing package.

Differential pairs are found by name: `X+`/`X-` and `X_P`/`X_N` (or
`_p`/`_n`). Name-based detection applies only when the profile says
`"pairs": "name-suffix/1"`. A profile can list explicit pairs instead,
`"pairs": [["USB_D+", "USB_D-"]]`. Net classes are not read.

Acid-trap junctions inside pad copper, or inside a via disk on the same layer,
have no open wedge at the vertex. They are counted in
`pad_or_via_covered_junctions` and do not produce findings.

Component spacing applies only to machine-placed footprints: those with pads,
not board-only, and not excluded from position files.

## Results and unknowns

Each rule record has `execution`, `status`, `checked`, `false_negative_classes`,
`findings` and `unsupported` items. Status is one of these:

- `pass`: the rule checked at least one item and found nothing.
- `defect`: the rule produced at least one finding.
- `not_applicable`: the rule had no applicable items, for example no vias or no
  detected pairs.
- `not_evaluated`: the rule could not be evaluated, and its `execution` is
  `unsupported`.

A rule becomes unsupported, and never passes, in these cases:

- The probe output has no saved `(general (thickness ...))`. pcbnew's load
  default is recorded as `native_thickness_default_nm`, but it is not used.
  This makes the aspect-ratio rule unsupported even when the board has no vias.
- A via is blind, buried or a microvia. Its depth needs per-layer stackup facts.
- A pad or via uses a per-layer padstack mode.
- A PTH pad has a shape or drill combination that the exact ring formula does
  not cover, such as a custom, trapezoid or chamfered pad, or an offset drill in
  a non-rectangular pad.
- A footprint that is placed by machine has no courtyard.
- A pad has no native copper polygon.
- A track or arc is malformed or degenerate.
- A pair net has no routed length, or a declared pair net is absent.
- A zone is unfilled.
- The probe facts are malformed.

The check result is `ok/defect` when every rule executed and at least one
rule has a finding, and `ok/pass` when every rule executed with no findings. If
any rule is unsupported, the result is `unsupported/unknown`, but the findings
from the other rules are still recorded in `details.findings`. Put rules in
separate checks when a partial unknown should not hold back the others.

Each finding has `code`, `rule_id`, `entity`, `at_mm`, `message`, the native
`items` (UUIDs), `refs` and nets, and the measured value. The result also
carries `details.gate_findings`, with entity and kind `dfm/RULE_ID`. These
are the stable identities that adjudication and convergence use. A DFM
finding becomes an accepted defect only through the existing check and
adjudication flow.

## Limits

Each rule definition carries its fixed `false_negative_classes`. They come from
the rule definitions, never from the board, and are recorded on each rule in
`dfm-report.json` and `details.rules` as well as in `dfm-report.json` and
`checks/<id>.json` `limitations`, so a passing result states what each rule
cannot see:

- The rules read saved geometry only. Zone fill is used as saved.
- The probe reads the saved thickness with the shared S-expression parser. It
  parses only the root prefix that ends at the first root-level `general`
  node, so a later duplicate `general` node is not seen.
- Via annular ring uses the all-layers padstack size.
- The acid-trap rule looks only at exact shared endpoints. A track that ends
  partway along another track is not detected.
- Copper balance compares area only. It does not model copper distribution or
  predict warp.
- The skew rule uses lengths from each net as a whole. It does not take
  per-segment pairing or via barrel lengths into account.

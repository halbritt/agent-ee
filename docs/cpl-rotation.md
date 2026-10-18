# JLCPCB CPL rotation correction

`cpl_rotation` (AGENTEE-72, stage 7 of the [generator roadmap](generator-roadmap.md))
turns the native CPL of a sealed [`fab_export`](fab-export.md) package into a
JLCPCB CPL. It replaces each anchor with a pad-box midpoint and applies a
captured, versioned rotation policy. It then checks every rotation against the
pin-1 frame of the part's captured supplier footprint. Two independent
mechanisms have to agree: the policy and the geometry.
[cpl_rotation.py](../src/agent_ee/cpl_rotation.py) implements it.

The check does not run `kicad-cli` again or fetch anything. It runs in the
coordinator over sealed and captured bytes. The result status is always
`PREVIEW_REQUIRED`, with `physical_validation: false`. A JLCPCB Component
Placements preview is still the only physical check of the selected
placement models.

## Parameters

Parameters are exactly:

```json
{"export_check": "fab", "geometry_check": "geometry",
 "policy": "design/jlc-cpl-policy.json",
 "supplier_footprints": ["design/supplier/C910008.provenance.json"],
 "source_tolerance_nm": "200"}
```

- `export_check` names an earlier `fab_export` check. Its result must be `ok`,
  and its sealed `fab/manifest.json` must still verify against the staging
  tree. The manifest must be bound to the captured board digest. Designators in
  the manifest's `designators.excluded` (parts excluded by the declared
  [assembly rule](fab-export.md#assembly-exclusions), which `fab_consistency`
  re-derives) are not counted as populated geometry missing from the CPL. If
  one of them is still a CPL row, the staging is `malformed`. The report lists
  them in `assembly_excluded`.
- `geometry_check` names an earlier `geometry` check of the same board digest.
  Its probe now records each footprint's lib id, orientation and exact nm
  anchor. It also records each pad's native board-space bounding box in integer
  nm (`bbox_nm`, from `PAD::GetBoundingBox`).
- `policy` is a captured rotation policy (below).
- `supplier_footprints` lists captured `agent-ee.supplier-footprint/1`
  provenance records, the same records [`land_pattern`](land-pattern.md)
  uses. Each record pins the raw EasyEDA component bytes by size and sha256.
  At most one record is allowed per LCSC number.
- `source_tolerance_nm` is passed to the shared EasyEDA package reader.

The policy, the provenance records and the raw supplier bytes are immutable
inputs. Capture and scope audit already refuse a policy or supplier record that
does not validate. The scope/3 predicates are
`cpl-rotation:pad-centroid-midpoint`, `cpl-rotation:policy` and
`cpl-rotation:pin1-frame`.

## Generator fab integration

`generate`, `rung1_campaign` and `rung1_qualified` accept `--cpl-inputs DIRECTORY`.
The directory must contain `jlc-cpl-policy.json` in the policy format above and
`supplier/*.provenance.json` with their raw supplier files. Capture copies it to
`generation/cpl/`; each provenance `path` must therefore name its captured raw
file, for example `generation/cpl/supplier/C123.raweasy.json`. The existing
supplier provenance schema, size and digest checks apply. No policy or supplier
geometry is inferred from the catalog or the KiCad footprint. These inputs join
the immutable baseline; they are outside the writer's design paths.

Stage 7 and the rung-1 `export-fab` step use
[`fab_stage.py`](../src/agent_ee/fab_stage.py). Its declared
`agent-ee.stage-step/1` runs in the networkless executor and retains the
AGENTEE-95 receipt, captured candidate, baseline, native netlist, geometry,
`fab_export` package and `cpl_rotation` report. The older fixture entry point
[`fab.py`](../fixtures/rung1/staged/steps/fab.py) delegates to this same module.
With a capture, both rung scopes also declare `fab_export` followed by
`cpl_rotation` for fresh coordinator checks; without one they declare no
`cpl_rotation` and keep their native behaviour (decision C of the AGENTEE-126
integration). The qualified rung retains its native consistency check and adds the
`export-fab` step only when the correction is declared. In the rung-1 campaign
the `fab_package` done gate consumes the declared correction and seals the
corrected CPL ([CPL rotation in the done gate](fab-package.md#cpl-rotation)).

The exporter leaves the native package untouched. Only complete `ok/pass` CPL
coverage can produce a derived package. It copies the corrected `cpl.csv` and
rotation report alongside the other exports. `fab-manifest.json` hashes the
final files and records the original export manifest digest, policy digest,
`applied_corrections`, `unmatched_footprints`, `status: PREVIEW_REQUIRED` and
`physical_validation: false`. The qualified rung derives its package from the
final review's checked outputs outside the sealed review run. Paths inside the
copied rotation report retain their check-workspace meaning; final package paths
and hashes are listed in the derived fab manifest.

Missing policy, an empty supplier set, missing per-part supplier coverage,
invalid provenance, native tool failure, geometry conflict or a policy/frame
defect cannot publish a completed package. Receipts retain the failed exit and
available check evidence. A successfully corrected package still needs the
JLCPCB preview: the generator's stage status is `PREVIEW_REQUIRED`. All three
generation records decide `done` with the same allow-list,
`fab_package.DONE_ROTATION_STATUSES` (`not_declared`, `ok`), so they keep
`done: false` and record `fab_rotation_blocker:
"fab: cpl rotation PREVIEW_REQUIRED (bottom-side transform unvalidated)"`; a
missing or unknown status is never done. Admitting `PREVIEW_REQUIRED` is an
owner decision made in that one constant. The generator also refuses a fab
stage whose consumed policy digest differs from the stage-1 capture.
No owner preview evidence store is created by this integration.

The existing `fab_export` limitations still apply. In particular, the generated
rung's hand-soldered non-DNP header has no LCSC binding and is unsupported by
that exporter; this integration does not convert it to DNP or silently omit it.
An explicit input directory enables the rotation stage, but does not resolve
that independent export gap.

## Midpoints

Mid X/Mid Y are the centre of the union of all the footprint's native pad boxes
in board space. The centre is taken as `left + (right - left) // 2` in integer
nm, which is KiCad's `BOX2I::GetCenter` rounding. It is not the anchor, and it
is not a rotated local box. The CPL axes are those of the native position file:
X is kept and Y is inverted once, on both sides.

The native CPL anchor must equal the probe anchor exactly in nm, and the
layers must agree. Otherwise that designator is `malformed`. A designator gets
`unsupported` and no CPL row in these cases:

- it has no pads or no probe record;
- the probe predates `bbox_nm`;
- it is duplicated among CPL rows, populated probe footprints or board
  footprints.

The anchor is never used as a fallback. If any row lacks a midpoint, no CPL
is written.

The owned [fixture](../fixtures/cpl-rotation/generate.py) reproduces the
midpoint oracle through real pcbnew. It uses a 1x02 2.54 mm header with a
1.7 mm square pin 1 and a 1.7 mm round pin 2:

| Rotation | Anchor (board mm) | Pad-box union centre | CPL Mid X, Mid Y |
| --- | --- | --- | --- |
| 0° | (20, 20) | (20, 21.27) | `20.000000,-21.270000` |
| 90° | (30, 20) | (31.27, 20) | `31.270000,-20.000000` |
| 180° | (40, 20) | (40, 18.73) | `40.000000,-18.730000` |
| 270° | (50, 20) | (48.73, 20) | `48.730000,-20.000000` |
| 45° | (60, 20) | (60.721984, 20.721984) | `60.721984,-20.721984` |

At 45° the union of a rotated square box and a round box differs from a
rotated local centre. The centre-anchored 0805 control `R1` keeps its native
position text.

## Policy

```json
{
  "schema_version": 1,
  "policy_id": "agent-ee-jlc-cpl-v1",
  "provenance": "why each rule is believed",
  "footprint_rules": [
    {"id": "soic-8-3.9x4.9-p1.27", "footprint_prefix": "SOIC-8_3.9x4.9mm_P1.27mm",
     "rotation_degrees": 270}
  ],
  "component_overrides": [
    {"id": "u7-model", "designator": "U7", "rotation_degrees": 90,
     "offset_x_mm": 0.15, "offset_y_mm": -0.1}
  ]
}
```

These cases refuse the whole policy:

- unknown keys, or a missing key, at any level;
- schema versions other than 1;
- duplicate JSON keys;
- duplicate rule ids, across both lists;
- duplicate designators;
- empty ids;
- `NaN`/`Infinity`, booleans, or values with more than six decimals;
- rotations outside ±360°, or offsets above 100 mm.

The check is then `malformed`, and no CPL or report is produced.

Precedence is deterministic. An exact `designator` override wins. Otherwise
the first `footprint_rules` entry whose `footprint_prefix` starts the board
footprint name wins (the library nickname is stripped and matching is
case-sensitive). Otherwise there is no correction. With rule delta `d` and
native angle `a`, the result is `(a + d) mod 360` for top-side parts and
`(180 - (a - d)) mod 360` for bottom-side parts. That bottom-side form is the
ticket's transform. It has not been checked against a JLCPCB bottom-side
preview. Offsets are added in CPL axes after the midpoint. Results are exact
decimals printed with six places.

Every CPL row appears in exactly one of `applied_corrections` (a rule
matched) or `unmatched_footprints` (no rule). This holds even for rows whose
geometry failed.

The [fixture policy](../fixtures/cpl-rotation/jlc-cpl-policy.json) is owned and
has two rules:

- `SOIC-8_3.9x4.9mm_P1.27mm` +270. JLCPCB rendered a KiCad 10 SOIC-8 at
  native 90° a quarter turn off (Konnect issue #518, recorded as a fact). The
  pin-1 frames below derive the same 270.
- HRO `TYPE-C-31-M-12` +180, from the same record. It is not re-derived here.

No JLCKicadTools (GPL) data is used. The pure test `test_issue518_analogue_*`
uses owned rows that mirror that export: `U1` SOIC-8 at 90° becomes 0°.

## Pin-1 frames

Pin 1 of the board footprint and pin 1 of the supplier footprint are each
classified into one of eight positions in a +Y-up top view:

- The board footprint's pads come from the captured board bytes, in the
  library frame. The `land_pattern` extractor recovers that frame and undoes
  the back-side mirror.
- The supplier footprint's pads come from `easyeda_footprint.parse_package`.

The positions are `leftside_top`, `leftside_bottom`, `rightside_*`,
`topside_left|right` and `bottomside_left|right`. They form two cycles of
counter-clockwise quarter turns:

| Cycle | 0° | 90° | 180° | 270° |
| --- | --- | --- | --- | --- |
| A | `leftside_top` | `bottomside_left` | `rightside_bottom` | `topside_right` |
| B | `leftside_bottom` | `bottomside_right` | `rightside_top` | `topside_left` |

The classifier works on exact pad centres with a 10 µm tolerance:

- **Sided.** Pin 1 lies on the bounding-box side with the most pads. Its end
  (top/bottom or left/right) is taken relative to the box centre.
- **Corner.** Pin 1 lies on a corner shared by two equally populated sides,
  for example a 2x2 crystal.
- **Linear.** The pads form one row or column, and pin 1 is at an end.

Corner and linear frames always land in one cycle. That keeps them consistent
under rotation, but a mirrored footprint cannot be seen in them, so
`reflection_detectable` is false.

The supplier-to-board offset is `(index(board) - index(supplier)) mod 4 × 90`.
A matching frame gives 0. A board footprint turned 90° counter-clockwise from
the supplier's gives 90. The offset must equal the applied policy delta mod
360; an unmatched row has a delta of 0. Any mismatch is a `rotation_conflict`
defect. Positions in different cycles are a `reflection` defect.

For `D*` and `LED*` designators, polarity is also checked. The board's pad-1
`pinfunction` and the supplier symbol's pin-1 name are each classified:

- anode: `A`, `anode`, `+`, `pos`, `positive`;
- cathode: `K`, `C`, `cathode`, `-`, `neg`, `negative`.

A disagreement is a `polarity_conflict` defect. An unresolved side is
`polarity_unknown`. The supplier pin record must also show the same displayed
number. `easyeda_footprint.symbol_pins` reads the symbol `P` records verbatim.

The classifier was also run locally on captured EasyEDA components and stock
KiCad 10.0.7 footprints, without committing those bytes. SOIC-8 supplier
footprints classify as `bottomside_left` and KiCad's SOIC-8 as `leftside_top`,
which gives 270. A KiCad 1x02 vertical header (`topside_right`) against a
horizontal supplier header (`leftside_top`) gives 90.

## Report and outcomes

`work/<id>/cpl-rotation.json` (`agent-ee.cpl-rotation/1`) holds:

- the status;
- the policy id, schema version, provenance, precedence and sha256;
- the export manifest and CPL digests, and the geometry digest;
- each designator's native row, midpoint evidence, policy match, corrected row,
  pin-1 classification, offset, polarity and modes;
- `applied_corrections` and `unmatched_footprints`.

The CPL goes to `work/<id>/cpl/cpl.csv` only when the outcome is `pass`. If
every row has a midpoint but the outcome is not `pass`, the CPL goes to
`cpl.incomplete/cpl.csv` as evidence.

| Mode | Per-designator execution | Meaning |
| --- | --- | --- |
| `reflection`, `rotation_conflict`, `polarity_conflict` | ok (outcome defect) | The policy and the supplier geometry disagree |
| `duplicate_designator`, `geometry_missing`, `binding_missing`, `supplier_missing`, `pin1_unclassified`, `pin1_frame_mismatch`, `pad_set_mismatch`, `polarity_unknown` | unsupported | Coverage is unavailable for that designator |
| `anchor_mismatch`, `side_mismatch`, `footprint_mismatch`, `geometry_malformed` | malformed | The sealed sources disagree |

The aggregate follows `land_pattern`:

- Any defect gives `ok`/`defect`, with `coverage: incomplete` when other rows
  lack coverage.
- Otherwise, any non-ok row gives the worst execution and `unknown`.
- `pass` needs every row to pass.

A populated probe footprint that is missing from the CPL makes the result
`malformed`. Before any output, these are refused:

- a refused policy or supplier capture (`malformed`);
- an unverifiable export (`malformed`);
- a missing or forward prerequisite (`unsupported`).

## Limitations

- The check never validates physical rotation. Upload Gerbers, BOM and CPL
  together and inspect every part in JLCPCB's preview. ICs, connectors, diodes
  and LEDs need the most care.
- The bottom-side transform is unqualified. Pin-1 frames compare pad centres
  only, and the board and supplier pad numbers must match as multisets. A
  connector numbered `A1…B12` has no pad `1`, so it is `pin1_unclassified`.
- Polarity uses names, not datasheets. Only `D*`/`LED*` designators are
  checked.
- The fixture's supplier records are owned EasyEDA-format text. The real-format
  reading of symbol `P` records was checked only locally.

## Tests

[tests/test_fab_stage.py](../tests/test_fab_stage.py) exercises the real nested
sandbox and receipt path, corrected SOIC rotation and header midpoint, immutable
policy and raw supplier hashes, missing policy/supplier refusals, and preview
status propagation through generation records.
[tests/test_generate.py](../tests/test_generate.py) `FabStageEndToEndTest` runs
stage 7 through `generate()` on the owned fixture (complete corrected package,
named blocker, policy-capture mismatch refused).
[tests/test_fab_package.py](../tests/test_fab_package.py) covers the done-gate
composition and the campaign record.

[tests/test_cpl_rotation.py](../tests/test_cpl_rotation.py) covers:

- policy refusals (unknown key, duplicate id/designator, NaN, Infinity,
  overflow, precision) and precedence;
- the issue-518 analogue and the bottom-side transform;
- all eight positions in both cycles, with reflections and linear/corner
  frames;
- `BOX2I` rounding and no-anchor fallback;
- per-designator duplicate, missing geometry, missing supplier and anchor/side
  boundaries;
- a pass, a quarter turn, a rotation conflict, a reflection and the diode
  polarity cases.

The native class runs the real `netlist` → `fab_export` → `geometry` →
`cpl_rotation` pipeline on the owned fixture. It checks the oracle, the
control, `U1` 90° → 0°, the partition, the evidence seal, NaN refusal at
capture and at run time, a missing supplier footprint, and the capability
descriptor. A staged copy whose manifest excludes `R1` and whose CPL lacks it
passes with `assembly_excluded: ["R1"]`. The same CPL without the claim leaves
`R1` unlisted (`malformed`), and a claim for a designator still in the CPL is
refused.

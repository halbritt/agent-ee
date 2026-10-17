# Supplier land-pattern cross-check

`agent_ee.land_pattern` compares the land pattern a supplier publishes for a
bound LCSC part with the KiCad footprint that will be fabricated. It reports
copper IoU per outer copper layer, drilled-aperture IoU, and per-pad pin
agreement. Wrong-variant footprints, pin-1/rotation mismatches and swapped pin
numbering are defects even when the copper geometry matches. Geometry uses the
[land-geometry kernel](land-geometry.md): exact rational polygons, with
certified inner/outer bounds for curved primitives. A curved area is never
reported as an exact finite-polygon area.

Status: the supplier reader, native extraction, comparison, CLI and coordinator
adapter are implemented and tested with owned fixtures. The `land_pattern`
kind is registered in the shared check registry, with captured dependencies,
scope/3 coverage and coordinator-sealed evidence. Two acceptance rules exist:
an absolute IoU threshold, and declared
[variant discrimination](#variant-discrimination). The actual C11702 pattern
fails the absolute 0.98 threshold against `R_0402_1005Metric`. Under variant
discrimination it passes against that footprint and is a defect against
`R_0603_1608Metric`. See [C11702 qualification](#c11702-qualification).

## Captured inputs

The check reads no network and fetches nothing. Supplier bytes are captured
once, outside checks, as the raw EasyEDA standard component object (the
`result` member of the component API response, not the envelope). An
`agent-ee.supplier-footprint/1` provenance record pins those bytes:

```json
{
  "schema": "agent-ee.supplier-footprint/1",
  "lcsc": "C11702",
  "format": "easyeda-std-component/1",
  "path": "supplier/C11702.raweasy.json",
  "sha256": "<sha256 of the raw bytes>",
  "size_bytes": 12345,
  "source_url": "<where the bytes came from>",
  "retrieved_at": "2026-10-17T00:00:00Z",
  "retrieval": "<how they were captured>"
}
```

`source_url`, `retrieved_at` and `retrieval` are recorded declarations, not
authenticated publisher identity. The hash proves only that these are the pinned
bytes. LCSC association is checked inside the raw schema itself. `lcsc.number`,
`szlcsc.number` and the symbol `Supplier Part` must equal the bound id. The
component `docType` must be 2. The symbol `puuid` must name the single
`packageDetail` (docType 4), whose head uuid must match.

Immutable `agent-ee.land-pattern-criteria/1` criteria name every reference to
compare. All fields are required and unknown fields are refused:

```json
{
  "schema": "agent-ee.land-pattern-criteria/1",
  "threshold": "0.98",
  "pin_tolerance_nm": "250000",
  "source_tolerance_nm": "200",
  "refinement_levels": [2, 4, 8, 16],
  "library_manifest": "lib/manifest.json",
  "requirements": [
    {
      "reference": "R1",
      "lcsc": "C11702",
      "supplier": {"provenance": "supplier/C11702.provenance.json", "sha256": "<provenance sha256>"},
      "native": {"source": "library", "lib_id": "Resistor_SMD:R_0402_1005Metric"},
      "registration": {"translation": "declared_origin", "rotation": "0", "mirror": false},
      "pin_aliases": null
    },
    {
      "reference": "U1",
      "lcsc": "C20917",
      "supplier": null,
      "native": {"source": "board"},
      "registration": {"translation": "copper_bbox_center", "rotation": "90", "mirror": false},
      "pin_aliases": null
    }
  ]
}
```

- `threshold`, tolerances and coordinates are decimal text; binary floats are
  refused. The threshold is strict: pass needs certified lower bound
  `> threshold`, defect needs upper bound `<= threshold`. A straddle after the
  last refinement level is `numeric_uncertainty`. Thresholds must be justified
  for the supplier/library pair, not tuned until a part passes.
- `pin_tolerance_nm` bounds pad-centre distance for geometric correspondence.
- `source_tolerance_nm` bounds disagreement between redundant supplier fields
  (a RECT's declared size against its outline, slot length against its
  endpoints). It never moves geometry. Declared centre/size/radius values are
  used verbatim; larger disagreement is `source_inconsistent`.
- `supplier: null` keeps the reference in the result as
  `execution: unsupported`, mode `supplier_missing`. It never passes.
- `native.source` is either an exact member of the captured complete
  [footprint manifest](footprint-libraries.md), or the board instance with the
  same reference on the candidate board. A modified board instance never
  inherits library geometry.
- `pin_aliases` is null or an explicit injective native-number→supplier-number
  map. It is never inferred from package names; package naming belongs to the
  separate package-consistency work.
- Bindings are consumed as declared. The check does not select parts or
  footprints.

`agent-ee.land-pattern-criteria/2` has the same fields plus a required
`acceptance` object on every requirement. A /1 criteria file means
`{"rule": "threshold"}` for every requirement and produces the same report
bytes as before /2 existed. `acceptance` is either:

```json
{"rule": "threshold"}
```

```json
{"rule": "nearest_variant",
 "alternatives": [{"source": "library", "lib_id": "Resistor_SMD:R_0201_0603Metric"},
                  {"source": "library", "lib_id": "Resistor_SMD:R_0603_1608Metric"}]}
```

Alternatives are 1–16 unique members of the captured library manifest, distinct
from the bound footprint. `acceptance` in a /1 file, a missing `acceptance` in a
/2 file, unknown rules and extra keys are all refused.

## Supplier reader

`agent_ee.easyeda_footprint.parse_package` treats one canvas unit as
10 mil = 254000 nm, exactly, with +Y down. The package-head origin is the
footprint origin. Every package record is accounted for:

| Record | Treatment |
| --- | --- |
| `PAD` RECT | Rectangle; outline corners must agree with centre/size/rotation within `source_tolerance_nm` |
| `PAD` OVAL | Stadium (circle when sides are equal); centreline must agree |
| `PAD` ELLIPSE | Circle only when width equals height; an unequal ellipse is `unsupported_shape` |
| `PAD` POLYGON | `unsupported_shape` |
| Drilled `PAD` | Must be multilayer (11) and plated `Y`. The round hole or axis-aligned slot is centred on the pad; length, endpoints and midpoint are consistency-checked |
| `HOLE` | Non-plated circular aperture |
| `VIA` | `unsupported_shape` (conductive, not a pad) |
| `TRACK`/`SOLIDREGION`/`CIRCLE`/`ARC`/`RECT`/`TEXT` | Ignored only on proven non-copper layers (silk, paste, mask, outline, document, assembly, mechanical, 3D, component/lead shape, marking); on copper or inner layers `unsupported_shape`; unknown layers `unsupported_layer` |
| `SVGNODE` | Ignored only on layer 19 (3D model) |
| Anything else, e.g. `PT~M …` | `unsupported_shape` |

Pads have exactly 20 fields. Missing, empty, `NaN` or exponent numbers are
`malformed` and are never defaulted to 0. Unnumbered copper pads, non-quarter
rotations (the EasyEDA direction convention is not qualified for general
angles), and inner/unknown pad layers are unsupported. Duplicate pad numbers
and positions are kept as separate physical pads. Ignored records are counted
by type and layer in `inventory.ignored_noncopper`.

## Native extraction

Library members and board instances share one strict extractor over the shared
S-expression reader. Pads may be `smd`, `thru_hole` or `np_thru_hole` with
shape `rect`, `circle`, `oval` or `roundrect`. Any pad field that could change
copper or drill geometry and is not modelled refuses. That covers custom
`options`/`primitives`, `padstack`, `chamfer`, `rect_delta`, unknown fields and
inner-layer copper. The rest:

- Roundrect radius is `roundrect_rratio × min(w, h)`, with the ratio in
  [0, 0.5].
- Copper graphics, zones, net-tie and jumper groups refuse.
- An NPTH must have no copper annulus.
- Paste/mask-only pads are counted and ignored.

Semantics are fixed by a pcbnew 10.0.7 save/reload control
([native test](../tests/test_land_pattern_native.py)):

- A drill `(offset …)` moves the copper in the rotated pad frame, while the
  hole stays at the pad position.
- Board pads store footprint-local positions and absolute angles. The library
  angle is the pad angle minus the footprint angle.
- A back-side instance stores local Y, pad angle and offset Y mirrored, with
  layers swapped. The extractor undoes this for both flip directions, so both
  sides compare in the top-view library frame.

Over the KiCad 10.0.7 stock library, 13,985 footprints extract. All 1,479
refusals are geometry-affecting or unqualified: custom/`connect` pads, zones,
net ties, chamfers, trapezoids, copper graphics, unnumbered copper,
NPTH-with-annulus, offset drills at 45°, or no copper at all.

Through-hole pads with `*.Cu` contribute to both `F.Cu` and `B.Cu`. Inner
layers are not compared. Holes are the union of all physical apertures,
including NPTH, and are subtracted from copper on every compared layer.

## Registration and comparison

Registration is declared per reference and applied exactly. The supplier
pattern is rotated by the declared quarter turn (KiCad convention), then
translated:

- `declared_origin` maps the EasyEDA package origin to the KiCad footprint
  origin.
- `copper_bbox_center` translates both patterns so that their pad-copper
  bounding-box centres coincide.

There is no scaling, mirroring, rotation search or alias search. A wrong
declared rotation is a geometric defect, not a reason to try another rotation.

Under the `threshold` rule each compared copper layer and the hole set is
decided against the criteria `threshold`.

### Variant discrimination

Supplier land patterns are generally not the IPC-nominal pads of a KiCad stock
footprint, so a fixed absolute overlap cannot tell a correct variant from a
neighbouring one across supplier and library generators. The `nearest_variant`
rule asks a relative question instead: is the bound footprint certified closer
to the supplier pattern than every declared alternative?

- Every alternative is registered by the requirement's declared rule and
  compared with the same supplier pattern. The kernel is the same; nothing is
  searched or selected.
- The score of one comparison is its worst metric: the interval
  [min lower, min upper] over compared copper layers and, when either side has
  apertures, the hole set.
- An alternative is `farther` when the bound lower score exceeds its upper
  score. It is `nearer` when its lower score exceeds the bound upper score.
  Otherwise it is `unresolved`.
- All comparisons are refined together through `refinement_levels` until none
  is unresolved or the levels are spent.
- Pass needs every alternative `farther` and pin agreement on the bound
  footprint. Any `nearer` alternative is a defect (`variant_mismatch`). Pin and
  alias conflicts remain defects. A remaining overlap is `variant_unresolved`.
  Identical alternatives therefore can never pass.
- The absolute `threshold` is not applied, and per-metric `decision` fields are
  omitted for these requirements. The pin-correspondence tolerance still has
  to hold for pass. The rule is only as strong as the declared alternatives,
  which should include the neighbouring size variants.
- Each record carries `score` and `variants`, one per alternative, with its
  path, hash, copper, holes, score and relation. Alternative footprints are
  witness inputs.
- An alternative that is missing or cannot be extracted makes the whole
  reference unsupported, with the alternative named in the message.

| Mode | Outcome | Meaning |
| --- | --- | --- |
| `match` | pass | Threshold rule: every compared layer and the hole set pass. Nearest-variant rule: every alternative is certified farther. Either way, pins agree under the unique (or all-agreeing) geometric correspondence |
| `low_accuracy` | defect | Threshold rule: some copper layer or the hole set has certified IoU upper bound ≤ threshold |
| `variant_mismatch` | defect | Nearest-variant rule: a declared alternative is certified nearer to the supplier pattern |
| `pin_conflict` / `alias_conflict` | defect | No admissible correspondence preserves numbering, or the alias map collides; holds even at IoU 1 |
| `pin_mapping_unavailable` | unknown | Pads cannot be uniquely corresponded within tolerance, or admissible correspondences disagree |
| `numeric_uncertainty` | unknown | A threshold straddle after the last refinement level |
| `variant_unresolved` | unknown | Nearest-variant rule: bound and alternative scores still overlap after the last refinement level |
| `supplier_missing`, `unsupported_shape`, `unsupported_layer`, `unsupported_rotation`, `native_missing` | unsupported | Coverage is not available for that reference |
| `supplier_malformed`, `supplier_identity`, `source_inconsistent`, `native_malformed` | malformed | Captured bytes are invalid or disagree with their binding |
| `input_missing`, `kernel_unavailable`, `adapter_error`, `kernel_malformed` | failed | An expected input or implementation was unavailable |

Several modes may be listed. The first explains the outcome. Execution remains
the coordinator vocabulary (`ok/unsupported/malformed/failed`). The aggregate
works like this:

- If any reference has a proven defect, the result is `ok`/`defect`, with
  `coverage: incomplete` when other references lack coverage.
- Otherwise any non-ok reference makes the aggregate non-ok and `unknown`.
- Pass requires every listed reference to pass.
- Empty requirement lists are refused.

The report (`agent-ee.land-pattern/1`) carries canonical-JSON seals:

- Per reference: rational IoU bounds plus 6-decimal outward-rounded
  presentation, the kernel refinement and algorithm, and the registered
  patterns.
- Pin pairs with both numbers and indices.
- Supplier and native locators, inventories and hashes.
- A witness for each defect, naming the exact inputs and the criteria hash.

Reruns are byte-identical. `replay()` recomputes a report from the same
captured bytes and refuses forged or tampered inputs, criteria or reports.

## Coordinator adapter

`capability(params, scope, files, inputs, cid)` takes exactly
`{"criteria": PATH}`. It validates the criteria and returns, as immutable
inputs:

- the criteria file;
- each provenance record and its raw supplier JSON;
- the library manifest and its full member closure.

The board stays mutable candidate geometry; board-derived geometry is never
frozen as truth. Exactly one baseline requirement must map the check, sourced
from the criteria file. The predicate is
`land_pattern:copper-hole-iou-pin-agreement/1`.

`check_land_pattern(ctx)` runs the stdlib module in the existing networkless
sandbox:

```sh
/usr/bin/python3 -m agent_ee.land_pattern /candidate/CRITERIA --captures /candidate --board BOARD --out /work/land-pattern.json
```

`land-pattern.json` is retained as evidence. `land_pattern` is registered in
`CHECK_FUNCS`, the baseline dependency loop and the capability
`KINDS`/descriptor dispatch. The manifest closure already contains every
nearest-variant alternative, so alternatives are immutable inputs too.

## C11702 qualification

This private gate uses the actual EasyEDA component for C11702 (raw sha256
`1874700…67155f2`), a local copy of an upstream converter test asset. It is
compared with the pinned KiCad 10.0.7 stock `R_0201_0603Metric`,
`R_0402_1005Metric`, `R_0603_1608Metric` and `R_0805_2012Metric` footprints.
Settings: declared origin, rotation 0, pin tolerance 0.25 mm, refinement
levels 2/4/8/16. Under the nearest-variant rule, the alternatives for each
bound footprint are the other three sizes.

| Bound footprint | Certified copper IoU | Threshold rule (0.98) | Nearest-variant rule |
| --- | --- | --- | --- |
| `R_0402_1005Metric` | [0.662971, 0.682132] | defect (`low_accuracy`; pins agree) | **pass** (`match`; 0201 [0.470166, 0.483326], 0603 and 0805 all farther; pins 1↔1, 2↔2) |
| `R_0603_1608Metric` | [0.177979, 0.182392] | defect (`low_accuracy`; pin mapping unavailable) | **defect** (`variant_mismatch`: 0402 and 0201 nearer; pin mapping unavailable) |

An independent float integration gave 0.6713 and 0.1795, inside both intervals.
Reruns are byte-identical and `replay` verifies both reports.

The supplier's rectangular 0.566 × 0.540 mm pads at ±0.433 mm differ
substantially from KiCad's IPC-nominal 0.54 × 0.64 mm roundrect pads at
±0.51 mm. No absolute IoU above 0.98 is reachable for this pair, and the
threshold was not tuned. Even the centred copper-bounding-box IoU is about
0.77, below the 0.8 used upstream for supplier-mismatch warnings. The ticket's
acceptance example (0402 passes, 0603 is a defect with the IoU reported) is met
by the declared nearest-variant rule. It is not met by an absolute threshold.
Which rule a campaign uses is part of its immutable criteria.

The raw/derived supplier data remain unpublished. The EasyEDA data licence is
unclear, so committed tests use only owned EasyEDA-format records. One of these
reproduces the published C11702 pad dimensions as facts, against owned
roundrect footprints with the stock R_0201/R_0402/R_0603 pad dimensions. Those
tests check the same pass/defect split and the float control.

## Limitations

- Outer-layer pad copper and drilled apertures only: no mask, paste, courtyard,
  silkscreen, inner-layer, thermal-relief, zone or trace comparison.
- Quarter-turn primitives only (kernel limitation). Board instances at any
  angle are supported when their pads are quarter turns in the footprint frame.
- No datasheet conformance, solderability or assembly approval.
- No capture/fetch service. Supplier JSON must already be captured with
  provenance.

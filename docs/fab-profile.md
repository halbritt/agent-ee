# Captured fabricator profiles (AGENTEE-75)

`fab_profile` is a selectable coordinator check in scope/2 and scope/3. It
evaluates an explicitly captured `agent-ee.fab-profile/1` or
`agent-ee.fab-profile/2` document through the
[routing check's](routing-rules.md) staged native runner:

```json
{"id": "fab", "kind": "fab_profile", "params": {"fab_profile": "requirements/fab-profile.json"}}
```

Parameters contain only `fab_profile`, the complete candidate-relative JSON path.
The profile, every cited source capture and the board's captured `.kicad_pro` and
`.kicad_dru` siblings are frozen as immutable baseline criteria. Each check binds
exactly one mapped fabrication requirement, matching the routing witness contract.
Scope/3 descriptors report one `fab:<limit-id>` predicate per sourced minimum
limit; preferred limits only classify extra charges. Preflight requires both
`kicad-cli` and `pcbnew`. No preset, network lookup, pricing service or default
fabricator is selected implicitly. The [telemetry-pod fixture](telemetry-pod-fab-profile.md)
records a dated subset of JLCPCB JLC04161H-7628 limits; it does not provide
complete supplier DFM or resolved pod mechanics.

For example, an **owned synthetic assumption**, not a real fabricator's limit:

```json
{
  "schema": "agent-ee.fab-profile/1",
  "name": "owned_standard_2026-10",
  "as_of": "2026-10-17",
  "limits": [{
    "id": "trace", "requirement": "fabrication", "key": "min_trace_mm",
    "value": 0.1, "unit": "mm", "tier": "minimum",
    "source": {
      "path": "sources/owned-capabilities.txt",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "source": "Owned synthetic assumptions",
      "as_of": "2026-10-17", "page": 1, "quote": "Minimum trace: 0.1 mm"
    }
  }]
}
```

Replace the placeholder hash with the captured source's SHA-256. Each limit needs
its own source identity, capture path/hash, date, page and exact quote. Dates must
match the profile's pinned date. Duplicate JSON keys are refused. The v1 source
dialect is UTF-8 text with form-feed page boundaries; PDF citations need a
separately qualified extraction adapter and are unsupported here.

Citation verification establishes only that the exact quote occurs on the cited
page of the captured bytes. It does not parse the typed `value` or `unit` from the
quote and does not check that they agree with it. It does not establish source
authenticity, applicability or correct interpretation of a table either. The
engineer owns the typed value and its reading of the cited text. No live
fabricator fact is inferred from a name, example or website.

Supported minimum/preferred keys:

| Key | Unit/value | Evaluation |
| --- | --- | --- |
| `min_trace_mm` | mm | Native track/arc width |
| `min_clearance_mm` | mm | Native copper clearance |
| `min_via_drill_mm` | mm | Native via hole size |
| `min_via_pad_mm` | mm | Native via diameter |
| `min_edge_clearance_mm` | mm | Native board edge clearance, only against a qualified outline |
| `min_hole_to_hole_mm` | mm | Native drilled-hole clearance |
| `max_layers` | integer count | Saved board copper layer count |
| `max_board_mm` | `[x,y]` mm | Saved-axis extents of one simple closed straight-segment outline, x to x and y to y |
| `supports_smt` | boolean | Presence of saved SMD pads |

`agent-ee.fab-profile/2` accepts every `/1` limit unchanged and adds
[conjunctive surcharge rules](#conjunctive-surcharge-rules-fab-profile2),
[silkscreen minima and advisories](#silkscreen-limits-fab-profile2). A `/1`
document keeps its exact `/1` contract and refuses a conjunctive rule.

Assembly coverage is limited to the declared presence of SMD pads. It does not
establish assembly process compatibility, component sourcing or factory approval.

A [board envelope](board-envelope.md#fabricator-profile) that references a
profile is read through this same compiler before generation. Its sourced
minimum `max_layers` and `max_board_mm` limits are evaluated against the declared
layer count and outline on the same fixed axes. Its profile obligation is covered
only by a `fab_profile` check over the referenced bytes.

## Finished-thickness tolerance

A `/2` profile may also contain top-level `finished_thickness_tolerance` with
exactly `percent`, `min_nominal_thickness_mm` and `source`. Percent must be
finite and strictly between 0 and 100; minimum nominal thickness must be
finite and positive. The source uses the same citation contract as every
limit. The compiler retains its `execution` and `expected` fields and adds the
source to `immutable_inputs`. Missing or changed evidence is unsupported,
and the fab-check descriptor refuses it before native execution.
A `/1` profile refuses this field.

This bound is consumed by [envelope/2](board-envelope.md) when comparing the
published physical layer sum to the ordered nominal thickness. It is not a
native DRC rule, extra-charge rule or scope/3 predicate. The `fab_profile`
check still grades only entries in `limits`; it makes no board-thickness
acceptance claim. The envelope's independent stackup obligation remains
unknown without qualified manufacturing authority and source coverage.

## Stackup authority

Fab-profile/2 may declare `stackup` with exactly `name`, `layer_count`,
`nominal_thickness_mm`, `thickness_mm`, `layers`,
`layer_thickness_tolerance_mm` and `source`. Layers reuse envelope/1's alternating
copper/dielectric shape, including explicit copper weights; their exact sum is
`thickness_mm` (physical thickness). The separate nominal thickness is the
board order. The name identifies the cited stack; the source uses the existing
path/hash/identity/date/page/exact-quote verifier. Fab-profile/1 refuses this metadata.

The explicit nonnegative layer tolerance bounds the difference between each
saved or envelope layer thickness and its declared profile thickness, using
exact rational arithmetic and an inclusive boundary. It is a **saved-design
matching policy**, not a claim about manufactured per-layer tolerances. The pod
profile declares zero: its saved layers must retain the published values exactly.
The cited ±10% finished-board tolerance remains a separate nominal-versus-physical
bound and is never applied to individual layers. No copper weight is computed
from thickness. A declared 2 oz layer against the profile's 1 oz is a defect even
when the saved thickness is unchanged.

The compiler retains `stackup.execution` and normalized `expected` metadata,
with cited source paths in `immutable_inputs`. Missing or invalid citations
leave it unsupported; the fab descriptor refuses that metadata. The independent
`board_envelope` `stackup/1` criterion consumes the authority and retains it in
`envelope-judgment.json`. Native fab limit predicates remain unchanged. Citation
verification proves occurrence in captured text, not supplier authenticity or
semantic interpretation of an arbitrary quotation.

## Board outline qualification

Board size and edge clearance need one simple polygon built from exact saved
board-level `Edge.Cuts` segment endpoints (integer nanometres, no chaining
tolerance). Every vertex must join exactly two segments, and the segments must
form one ring. No two edges may cross or touch, except adjacent edges at their
shared vertex, and the area must be nonzero. Concave and non-rectangular
polygons qualify, and a side may be split into collinear segments.

Coverage is unknown, with the reason and saved `locator_ids`, for:

- a missing outline
- zero-length, coincident or reversed duplicate segments
- open, branched or vertex-touching rings
- crossing (for example a bowtie), overlapping or folded collinear edges
- zero area (for example a flat ring)
- arcs, circles, rectangles, polygons, curves and text
- more than one closed ring (cutouts or separate boards)
- any footprint item on `Edge.Cuts`

Nothing is repaired or guessed.

`max_board_mm` compares the saved-axis bounding box on fixed axes: saved x with
the first value and saved y with the second. A 20×15 mm board meets `[20,15]`
but violates `[15,20]`. `fab-profile/1` grants no rotation permission. A
captured quote does not establish that a fabricator accepts either orientation,
so permitting rotation would need a new, explicit and versioned contract. A
board drawn at a non-orthogonal angle is measured on the saved axes, and no
minimum-area rotation is searched.

Native DRC has nothing to measure edge clearance against without an outline, so
it reports a board with no `Edge.Cuts` as satisfying any edge minimum. The native
runner passes its saved-board inventory to the classifier. A satisfied
`min_edge_clearance_mm` becomes `execution=unsupported` and `not_evaluated`
unless the outline qualifies, so a missing, flat or crossing outline cannot pass.
A native edge-clearance violation remains a reported violation. A preferred edge
limit then also stays unknown, because its minimum is no longer evaluated.

Geometry violations cite saved identities in `actual`/`offender_ids`: edge or
pad UUIDs, and for layer count the canonical copper layer names (`F.Cu`,
`In1.Cu`, ...). Renamed layers keep their canonical names. KiCad saves no board
UUID and invents one on each load, so none is cited. These computed comparisons
are distinct from the raw DRC `native_findings` records. Via and track
violations retain the native offender UUIDs.

## Native limits, defects and extra charges

Sourced physical minima generate custom DRC rules and staged `.kicad_pro`
`board.design_settings.rules` limits. Board-wide limits emit no rule condition.
Every limit runs separately, which preserves the captured candidate and avoids
rule precedence between profile limits. Routing constraints stage no project
rules, so captured settings such as via-height length calculation stay as captured.

Minimum violations are defects. A preferred violation becomes an `extra_charge`
record only for offender IDs not already shown illegal under the evaluated
minimum for the same key. Those already-illegal IDs stay in
`minimum_violation_ids`. If every preferred offender is already a minimum
violation, the preferred record says `satisfied`. That means no additional
surcharge, not that the geometry meets the minimum, and the minimum record stays
the defect. A preferred limit without an evaluated minimum for the same key
remains unknown. Extra charges never become defects. They are listed in the
check's `details.extra_charges` and do not change a passing outcome.

The ticket's numbers are exercised with owned synthetic limits: 0.09 mm and
0.15 mm tracks against a 0.1 mm minimum and 0.2 mm preferred limit, and a 0.25 mm
drill against a 0.2 mm minimum and 0.3 mm preferred limit, which reports both via
IDs as extra charges. These numbers are test assumptions. The separately captured
[JLCPCB preset](#dated-jlcpcb-standard-via-preset) uses a 0.45 mm diameter bound
for the joint surcharge.

## Discrete drill options (fab-profile/2)

A minimum-tier `min_via_drill_mm` limit may also declare `drill_options`:

```json
{"drill_options": {"values_mm": [0.15, 0.2, 0.25], "continuous_from_mm": 0.3}}
```

These are the dated preset's sourced options. The limit's source citation must
cover the options and the continuous range. Every value must be a positive,
finite whole number of nanometres. The nonempty discrete list must contain
unique values below `continuous_from_mm`, and no value may be below the limit's
minimum. Other keys, preferred-tier limits and `/1` profiles refuse this field.
Profiles without the field retain their continuous grading.

The existing native inventory records every saved via UUID and integer
`drill_nm`. Before surcharge classification, drills must exactly match a listed
option or be at least `continuous_from_mm`. There is no rounding tolerance.
A drill strictly below the limit's own sourced minimum (integer nanometres) is
a manufacturing defect, not an unlisted option: the minimum record keeps its
native `execution=ok`, `status=violation` and lists the saved vias in
`below_minimum_vias`. That defect needs no new source claim, only the cited
minimum, and stands only when native DRC cites every such via UUID; a
below-minimum via the native finding does not cite makes the record unsupported.
Such a via is a minimum violation, so it is never a surcharge.
An unlisted drill at or above the minimum makes the minimum record
`execution=unsupported` and `status=not_evaluated`, with the exact
`unsupported_vias`. Missing inventory is also unsupported; an observed empty
inventory has no unsupported vias.
Each such refusal is typed by `code`: `via_drill_off_table`,
`via_inventory_missing` or `below_minimum_via_uncited`. Each refusal also
carries `drill_table`, the exact table it was checked against. That table holds
`values_mm` and `continuous_from_mm`, plus the cited capture's `source_path`,
`source_sha256` and `as_of`. Sizes are never rounded to a nearby option. A
tampered or changed capture hash leaves the limit unsupported before any
table is consulted, so on-table drills cannot be graded either.

A captured table lists via drills only, so it covers no drilled pad. The native
inventory also records every drilled pad as `holes`: saved pad UUID,
`hole_class` (`PTH`, `NPTH`, or `other` for any other drilled attribute) and the
requested `drill_x_nm`/`drill_y_nm`. A board with any such hole cannot be graded
by the table, whatever its size. The table record keeps its via grading as
`via_status`, but becomes `execution=unsupported`, `status=not_evaluated` with
`code=hole_class_without_drill_table`, the checked `drill_table`, and
`unsupported_hole_classes`: one entry per observed class with its holes. The
check is then unknown, never pass. A via refusal above keeps its own code and
lists the uncovered classes beside it. Via surcharge records are classified
exactly as on a via-only board, so proved via surcharges are still reported.
Profiles without `drill_options` have no table and keep their continuous grading.
Both scalar and conjunctive drill surcharge records then remain unknown because
their minimum is unevaluated. Raw native reports remain retained evidence, but
cannot establish a drill charge outside the declared options. The
whole check is unknown even if another limit has a proved violation.

When one board has both a below-minimum drill and an unlisted drill, unknown takes
precedence. The record is unsupported with both `unsupported_vias` and
`below_minimum_vias`, and its native findings still cite the defective via, so the
defect is visible in details but the check outcome is `unknown`, never `pass`.
This is the existing whole-check rule above: any unevaluated record makes the
check unknown even beside a proved violation. A determinate `defect` would also
need the surcharge records classified, which the unlisted drill prevents.

## Conjunctive surcharge rules (fab-profile/2)

Some fabricators charge extra for a via only when its drill and its finished
diameter are both small. A `/2` profile expresses that as one rule whose `all`
terms must all hold. The rule has its own captured source, exactly like a scalar
limit. These values are an **owned synthetic assumption**:

```json
{
  "id": "small-via", "requirement": "fabrication", "tier": "preferred",
  "all": [{"key": "min_via_drill_mm", "value": 0.3, "unit": "mm"},
          {"key": "min_via_pad_mm", "value": 0.5, "unit": "mm"}],
  "source": {
    "path": "sources/owned-capabilities.txt",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "source": "Owned synthetic assumptions",
    "as_of": "2026-10-17", "page": 1,
    "quote": "Small via: drill under 0.3 mm and diameter under 0.5 mm"
  }
}
```

A rule needs at least two distinct terms. Each term has exactly `key`, `value`
and `unit`. The only term keys are `min_via_drill_mm` and `min_via_pad_mm`,
because both native findings cite only the via. Values are positive millimetres
in the native finite range. The tier must be `preferred`: a conjunctive rule
classifies extra charges and never defines a defect or a scope/3 predicate. Any
other shape invalidates the profile. A missing or mismatched source leaves the
rule `execution=unsupported`, so the aggregate is unknown.

Each term compiles to the same isolated native rule as the scalar key, under the
marker `agent_ee_<id>+<key>`. `+` is outside the limit ID alphabet. Each term runs
its own staged KiCad DRC under `native-rules/<id>/<key>/`. A term covers a via
only when that via is strictly under the term's bound, as KiCad's native `min`
constraint decides it. A via exactly at a bound does not meet that term. Engineers
encode the cited text's boundary in the typed value. The rule's joint offenders
are the via UUIDs that every term's native findings cite. The per-term records,
with their native findings and hashes, stay under the rule's `terms`.

If a term's DRC fails, is malformed or is unsupported, the rule is not evaluated
and carries that term's execution and reason. Classification needs an evaluated
minimum for every term key. A joint via that is illegal under any term's minimum
is listed in `minimum_violation_ids` and stays a defect of that minimum. The
remaining joint vias are the rule's `offender_ids` in its `extra_charge` record.

## Dated JLCPCB standard via preset

[profile.json](../fixtures/fab-profiles/jlcpcb-standard-2026-10-19/profile.json)
is an explicitly selected `jlcpcb_standard_2026-10-19` profile. It records a
**via-only subset** for ordinary FR-4 two-layer and multilayer fabrication,
excluding the special process for boards at most 1 mm thick. It is independent
of the telemetry-pod profile and changes no captured pod criteria or hashes. The
pod profile records the same two surcharge rules from its own 2026-10-18 capture
(AGENTEE-272), and the [scaffold](scaffold.md#default-via-and-joint-surcharge-rules)
derives a Default via outside the joint band from them.

| Limit | Value | Role |
| --- | --- | --- |
| Via drill / diameter | 0.15 / 0.25 mm | Manufacturing minima |
| Preferred drill | 0.2 mm | Separate charge for the published 0.15 mm drill option at any diameter |
| Joint small-via bounds | Drill <0.3 mm AND diameter <0.45 mm | Charge for the published 0.2 and 0.25 mm drill options with small diameters |

The [capabilities page](https://jlcpcb.com/capabilities/pcb-capabilities) names
0.2 and 0.25 mm drills with diameter below 0.45 mm as surcharged, and separately
names 0.15 mm drills at any diameter. The
[extra-charge article](https://jlcpcb.com/help/article/in-what-cases-will-there-be-charged-extra)
(section 4, Small Size Holes) identifies 0.3 mm as the standard-hole boundary.
The joint rule expresses those published small-drill options below that bound;
**intermediate drill sizes are not qualified by the discrete capabilities text**.
The 0.2 mm preferred floor similarly models the published 0.15 mm option.
The declared `drill_options` gate enforces those options and the continuous
range from 0.3 mm. Drills of 0.18, 0.22 and 0.28 mm are unsupported, regardless
of via diameter. Supplier process applicability and pricing remain unqualified.

For example, a legal 0.25/0.4 mm drill/diameter via yields a `small-via`
`extra_charge` record listing its saved UUID. At 0.25/0.45 mm, the via is outside
the joint band. A 0.15/0.6 mm via retains its separate `small-drill` charge.
A 0.1 mm drill is below the published 0.15 mm minimum, so the check is a
`via-drill` defect and the via appears only in the surcharge records'
`minimum_violation_ids`; 0.149999 mm is a defect and 0.15 mm is the
surcharged option. A supported drill with an undersized diameter still
establishes a diameter defect.
An empty charge list covers the declared rules only; it does not establish an
order price, annular-ring compliance or complete supplier DFM approval.

[source-capture.json](../fixtures/fab-profiles/jlcpcb-standard-2026-10-19/source-capture.json)
records the two host-captured URLs, UTC timestamps, raw response SHA-256 values,
byte counts, table/section locators and the interpretation above. As in the
[telemetry-pod capture](telemetry-pod-fab-profile.md#sources-and-replay), the
committed [numeric record](../fixtures/fab-profiles/jlcpcb-standard-2026-10-19/jlcpcb-vias.txt)
contains original numbers/names only. Each profile citation binds exact text
and its SHA-256; citation occurrence proves neither vendor authenticity nor
semantic interpretation. Raw vendor HTML remains in worker scratch, outside
Git and any owner evidence store. Refreshes require a new host capture and
reviewed date/values; checks and tests never fetch sources.

`tests.test_standard_fab_profile` compiles the committed preset, refuses missing,
changed, misquoted and unsourced facts, and uses real sandboxed KiCad DRC for
the examples and both joint boundaries. It checks saved via IDs and reloads
pad layers in a fresh process. The existing seal helper rejects changed results
and profile bytes. It also covers unlisted drills, below-minimum defects, a
below-minimum plus unlisted mix, exact nanometre boundaries,
invalid option declarations and missing inventory. Saved and reloaded PTH,
NPTH (including an oblong slot) and mixed via/pad boards are unknown, with
their pad UUIDs and sizes, while via grading and surcharges stay as on via-only
boards. The compiler accepts the
optional `/2` drill field; check kinds and predicate names are unchanged.

## Silkscreen limits (fab-profile/2)

A `/2` profile may state four silkscreen (legend) minima. Each key keeps its
own meaning and source, and none is derived from another:

| Key | Native measurement | Gate before grading |
| --- | --- | --- |
| `min_pad_to_silk_mm` | `silk_clearance` with condition `B.Type == 'Pad'`: KiCad reports `silk_over_copper` | A tented pad (no mask opening on that side) reports `silk_overlap` and is unknown |
| `min_silk_text_height_mm` | `text_height` on `?.Silkscreen` | Visible outline-font or knockout text is unknown |
| `min_silk_line_width_mm` | `text_thickness` on `?.Silkscreen`, plus every saved silk stroke width | Filled or zero-width shapes are unknown |
| `min_silk_to_mask_mm` | None: always `code=silk_to_mask_unmeasured`, unknown | Not graded |

KiCad 10 measures silk to pad copper, not to the mask aperture. A host probe
measured the same `actual 0.1400 mm` with a 0.05 mm and a 0.3 mm pad mask
margin. That reading supports pad-to-silk only. No qualified measurement exists
for silkscreen-to-solder-mask clearance, so that limit stays unknown and the
check is never pass while the profile states it. A board silk line exactly at
the bound is legal: KiCad's `min` reports only distances strictly below it.
Silk-to-silk spacing has no sourced limit and none is accepted:
`min_silk_to_silk_mm` is an unsupported key.

Silk keys are minimum tier only. Their rules stage no `.kicad_pro` board
minimum, and the keys are kept out of the `NATIVE` table, so scaffold template
guards are unchanged. A `/1` profile refuses them.

A silk citation binds the meaning of its fact, not only its occurrence. The
quote must be one whole recorded line `<subject> / <role>: <value> mm`. Its
subject must state the key's quantity, and its value must equal the limit's
value exactly. A limit must cite a `minimum` or `absolute minimum` role. A
recommendation cited as a limit, with its source and value unchanged, is
unknown (`code=source_role_mismatch`) and stages no rule, at any value. A
separately sourced minimum with the same number still grades. Other mismatches
are `source_subject_mismatch`, `source_value_mismatch` and
`source_fact_unparsed`. Advisories are bound the same way: a `recommended`
advisory must cite a recommendation, and a `guide_minimum` must cite a minimum.

Silk minima also need supplier applicability. Profile-level `applicability`
entries (fab-profile/2) each hold exactly `id`, `base_materials` and a
`source`. The quote must be one whole recorded line
`scope / <subject> / base material: <names>` (or `supported base materials`)
naming exactly `base_materials`. Each entry covers the silk minima that cite its
source page. An unverified entry refuses the descriptor. The inventory adds
`dielectrics`: the saved `core`/`prepreg` stackup layers of the board and their
`material` names, read with the shared S-expression parser, or `null` when the
board saves no stackup. Applicability is checked first, and every outcome but
the last is unknown:

- no verified scope for the cited page: `silk_applicability_unsourced`
- no saved stackup, a dielectric with no material, or a material outside the
  qualified mapping (only KiCad `FR4` maps, to `FR-4`): `silk_material_unverified`
- a mapped material outside the page's scope: `silk_applicability_mismatch`
- every saved dielectric maps into the scope: grading continues

KiCad writes `FR4` into a new stackup by default, so the saved material is the
designer's declaration, not proof of the order. Order-form options are not
captured.

The native inventory also adds `silk`: every saved item on `F.SilkS`/`B.SilkS`, as
`texts` (class, visibility, stroke font or font name, knockout, height,
thickness, enabled textbox border width), `graphics` (shape, integer-nanometre
stroke width, fill) and `other` (any other class, such as dimensions, tables,
zones or pads on silk). After applicability, and before a silk record is graded:

- any `other` item makes every silk record unknown (`silk_item_unqualified`)
- a native finding that cites hidden text is unknown (`hidden_silk_text`).
  KiCad 10 skips hidden footprint fields, and board text has no saved visibility.
- the text keys need KiCad's stroke font. On it, the nominal height is the
  centreline cap height; the printed extent adds the stroke. Outline fonts and
  knockout text are `silk_text_unqualified`.
- line width compares every stroked shape and enabled textbox border with the
  bound in exact nanometres. Offenders are in `offender_ids` and
  `silk_graphic_violations`; `filled` (solid or hatched) or zero-width shapes are
  `silk_graphic_unmeasured`.

A refused record keeps its native result as `native_status`, its findings, the
first `code`, `unqualified_silk` and every refusal in `silk_refusals`. As with
drill tables, unknown takes precedence over a proved violation.

Profile-level `advisories` (fab-profile/2) hold sourced values that never grade:
each has exactly `id`, `key`, `value` (positive mm), `unit`, `role`
(`recommended` or `guide_minimum`) and a `source` checked by the citation
verifier. They compile to `advisories` metadata only. They produce no native rule,
predicate, defect or extra charge. An unverified advisory refuses the descriptor.

## Dated JLCPCB silkscreen profile

[profile.json](../fixtures/fab-profiles/jlcpcb-silkscreen-2026-10-22/profile.json)
is an explicitly selected `jlcpcb_silkscreen_2026-10-22` profile. It is separate
from the via preset and the pod profile, so their bytes and hashes are unchanged.
A candidate that needs both runs two `fab_profile` checks.

| Limit | Value | Source |
| --- | --- | --- |
| `pad-to-silk` | 0.15 mm | [Capabilities](https://jlcpcb.com/capabilities/pcb-capabilities/), Legend, Pad To Silkscreen |
| `silk-text-height` | 1.0 mm | Capabilities, Legend, Minimum text height |
| `silk-line-width` | 0.15 mm | [Silkscreen guide](https://jlcpcb.com/blog/pcb-silkscreen), line width / text thickness, absolute minimum |
| `silk-to-mask` | 0.15 mm | Silkscreen guide, silkscreen-to-solder-mask clearance, absolute minimum (unmeasured) |

| Advisory | Value | Role |
| --- | --- | --- |
| `guide-text-height` | 0.8 mm | Guide's absolute character-height minimum. The capability table's 1.0 mm minimum grades |
| `recommended-line-width` | 0.2 mm | Recommended only |
| `recommended-silk-to-mask` | 0.25 mm | Recommended only |

| Applicability | Base materials | Source |
| --- | --- | --- |
| `capability-scope` (page 1) | FR-4 | Capabilities page, Rigid PCB Manufacturing Capabilities, Material row |
| `guide-scope` (page 2) | FR-4, Aluminum, Copper Core, Flex (FPC) | Silkscreen guide table, Supported Base Materials |

So `pad-to-silk` and `silk-text-height` grade only a saved FR4 stackup, and
`silk-line-width` grades it under the guide's wider scope.

The capabilities table also says characters narrower than 0.15 mm are
unidentifiable. That value is recorded on page 1. The graded line-width limit
cites the guide, which names lines and text thickness. The guide's 0.15 mm
pad-edge clipping buffer is recorded but not graded.

[source-capture.json](../fixtures/fab-profiles/jlcpcb-silkscreen-2026-10-22/source-capture.json)
records both host-captured URLs with UTC timestamps, HTTP status, raw response
SHA-256 values, byte counts, locators and interpretation limits. The
[numeric record](../fixtures/fab-profiles/jlcpcb-silkscreen-2026-10-22/jlcpcb-silkscreen.txt)
holds numbers and names only. Page 1 is the capabilities table and page 2,
after a form feed, is the guide. Raw vendor HTML remains in worker scratch,
as for the via preset. Raw bytes were rechecked against the recorded lengths
and SHA-256 values during review; the committed tests verify the numeric record
only. Base-material applicability is checked as above. Order options are not
checked, and this is not complete DFM or supplier approval.

`tests.test_silk_fab_profile` covers the preset contract: distinct page/quote
bindings, advisory refusal and `/1` refusal. It also covers fact role, subject
and value binding (recommendations cited as minima), and applicability scope
lines. It refuses missing, changed,
misquoted and wrong-page captures, and runs the qualification gates on unit
records. Real sandboxed KiCad DRC covers: a compliant board (pass, or unknown
with `silk-to-mask`), the exact 0.15 mm boundary, and a 0.14 mm pad gap with
0.8 mm text, 0.1 mm thickness and a 0.1 mm line as a defect citing saved silk
and pad UUIDs. It also covers mask-margin independence, back-side grading
with opposite-side silk ignored, tented pads and outline fonts as unknown,
hidden footprint fields and the saved silk inventory. It also covers the 0.2 mm
and 0.25 mm recommendations promoted to minima (unknown, not a defect). A
missing scope, a missing stackup or material, Polyimide and an owned out-of-scope
record are all unknown, with `native_status` kept for compliant and violating
boards.

## Evidence and failure

The profile hash is in `rules-evaluation.json` and the check's `details.source`.
Source citations are retained in each expected limit. Effective/generated rules,
staged project hashes, native JSON, the fabrication inventory in
`routing-probe.json` and geometry facts remain sealed coordinator evidence under
the check's workspace.

A missing, unreadable or invalid profile is unsupported and never selects
defaults. Numeric bounds outside the native finite numeric range are invalid,
including JSON integers too large to convert to a native number; descriptor
generation and check execution both report unsupported coverage.
A limit missing a valid source fact stays `execution=unsupported`, and
the aggregate outcome is unknown. Other established violations remain visible in
the per-limit records, but partial coverage cannot witness a defect. A changed
profile or source capture cannot reuse the captured candidate's acceptance.

## Tests and remaining boundaries

Owned native fixtures cover:

- 0.09 mm trace failure and 0.15 mm legal/preferred warning
- undersized drills and 0.25 mm drill extra charges
- conjunctive drill-and-diameter surcharges. Six saved vias cover joint,
  drill-only, diameter-only, at-drill-bound, at-diameter-bound and control
  sizes. Only the joint via's saved UUID is charged, a joint via under the
  drill minimum stays a defect, and missing term minima are unknown
- excessive layer counts, including renamed copper layers
- fixed-axis outline extents
- the remaining native rule mappings
- invalid sources and open outlines

Saved-board fixtures add rectangle and concave controls, plus:

- bowtie, flat and missing outlines for size and edge clearance
- reversed and hand-edited coincident duplicates
- arcs, cutout rings and footprint `Edge.Cuts`
- stable saved locators

Tests seal actual native reports using production coordinator evidence logic,
validate defect witnesses, and reject changed sealed artifacts and inputs. A
public review/verify pipeline covers pass, extra-charge, defect and no-outline
cases with scripted reviewer transport.

The [telemetry-pod fixture](telemetry-pod-fab-profile.md) records dated JLCPCB
manufacturing minima from a host capture as original numeric/name records.
It supplies no surcharge rule. The separate
[dated standard via preset](#dated-jlcpcb-standard-via-preset) exercises the
published 0.25 mm drill surcharge with the conjunctive `/2` policy and its
own captured sources. Raw vendor pages remain outside the repository. The
synthetic scalar preferred-drill example remains a test assumption.
Arbitrary-angle minimum bounding dimensions, curved outlines and
cutouts remain unsupported. Owner review and peer review remain separate from
these checks.

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
[conjunctive surcharge rules](#conjunctive-surcharge-rules-fab-profile2). A `/1`
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
IDs as extra charges. These numbers are test assumptions, not a bundled standard
preset or a quote from any fabricator.

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
It supplies no surcharge rule. The standard-preset surcharge criterion remains
unmet for a real fabricator: the synthetic 0.25 mm example proves classification
only. JLCPCB's small-via surcharge depends on drill and diameter together;
`/2` can express a joint condition, but this fixture does not qualify that
conditional policy. Raw vendor pages remain outside the repository. The scalar
preferred-drill example must not be labelled a qualified JLCPCB surcharge preset.
Arbitrary-angle minimum bounding dimensions, curved outlines and
cutouts remain unsupported. Owner review and peer review remain separate from
these checks.

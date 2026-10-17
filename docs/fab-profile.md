# Captured fabricator profiles (AGENTEE-75)

`fab_profile` is a selectable coordinator check in scope/2 and scope/3. It
evaluates an explicitly captured `agent-ee.fab-profile/1` document through the
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
fabricator is supplied.

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

Assembly coverage is limited to the declared presence of SMD pads. It does not
establish assembly process compatibility, component sourcing or factory approval.

A [board envelope](board-envelope.md#fabricator-profile) that references a
profile is read through this same compiler before generation. Its sourced
minimum `max_layers` and `max_board_mm` limits are evaluated against the declared
layer count and outline on the same fixed axes. Its profile obligation is covered
only by a `fab_profile` check over the referenced bytes.

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

## Evidence and failure

The profile hash is in `rules-evaluation.json` and the check's `details.source`.
Source citations are retained in each expected limit. Effective/generated rules,
staged project hashes, native JSON, the fabrication inventory in
`routing-probe.json` and geometry facts remain sealed coordinator evidence under
the check's workspace.

A missing, unreadable or invalid profile is unsupported and never selects
defaults. A limit missing a valid source fact stays `execution=unsupported`, and
the aggregate outcome is unknown. Other established violations remain visible in
the per-limit records, but partial coverage cannot witness a defect. A changed
profile or source capture cannot reuse the captured candidate's acceptance.

## Tests and remaining boundaries

Owned native fixtures cover:

- 0.09 mm trace failure and 0.15 mm legal/preferred warning
- undersized drills and 0.25 mm drill extra charges
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

No real fabricator's capability document has been captured, so no real preset is
qualified. Arbitrary-angle minimum bounding dimensions, curved outlines and
cutouts remain unsupported. Owner review and peer review remain separate from
these checks.

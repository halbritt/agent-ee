# Original floorplan-intent dialect, pad-edge metric and group links (AGENTEE-479)

This is the first implementation slice of the accepted
[AGENTEE-479 contract](planning/2026-10-25-recovery/contracts/AGENTEE-479-group-intent-contract.md).
It adds the exact original-dialect adapter, the upstream pad-edge metric with
per-leg emission, and the measured group-relation report. It does not yet add
the solver objective or the post-anneal member refinement (§Remaining).

## Adapter (`placement_intent.adapt`, `adapt_bytes`)

`placement_intent.is_dialect` recognises exactly one original dialect:
`kind == "floorplan-intent"`, `schema == 1`, `min_reader == 12`,
`units == "mm"`. Any other `min_reader`, schema, kind, units or top-level key
refuses with the exact path. A native `agent-ee.placement-intent/1` document
passes through unchanged and is runnable.

`adapt(intent, groups)` returns an `agent-ee.intent-compatibility/1` record.
It never drops, rewrites or weakens a field; every field receives a
disposition with its exact path:

| Disposition | Fields | Effect |
| --- | --- | --- |
| `operative_supported` | `fixed_poses[]`, `edge_connectors[]` (`ref`, `edge`, `max_setback_mm`, `overhang_mm`, `class`, `context.mount_mode`), `legality_budget.oob_count == 0`, `groups.groups[]` (`name`, `anchor`, `refs`) | carried in `native_intent` / `native_groups`, which the existing validator accepts |
| `operative_pending_capture` | `assembly.sides`, `envelope`, `edge_connectors[].side`, `edge_connectors[].context.cable_entry` | verification against native geometry is still to be wired into capture; listed in `pending` |
| `objective_pending_solver` | `proximity[]` (`basis: pad_edge`), `groups.groups[].near` | measured by `pad_edge` / `group_links` here; solver objective and refinement are the remaining step; listed in `pending` |
| `noop_empty` | `blocks`, `keepouts`, `must_lock`, `decaps`, `context.cutouts`, `context.edge_contours`, `context.file_locked`, `defaults.zone_tolerance_mm` | accepted only at the empty value; any content refuses |
| `unresolved_unmeasured` | `context.brief.consequences[status == "unmeasured"]` | retained in `unresolved_claims` as `unresolved_unmeasured`, never satisfied |
| `withheld_disposition` | `dispositions.rules.*`, `dispositions.withheld.*`, `context.budget_withheld.*` | retained verbatim in `withheld` |
| `descriptive` | `board`, `assembly.why`, `fixed_poses[].{basis,why}`, `edge_connectors[].{note,source,context.*}`, `proximity[].context.*`, `context.*`, `groups.groups[].{role,why}` | text validated; canonical sha256 recorded |

`proximity[].context.limit` is required and classified: `derived_default` is
an `advisory_objective`; any other label is `unassessed_authority`. A label is
never promoted to a hard gate. `cable_entry: perpendicular_top` requires
`side: "F"`; `in_plane` relies on the declared edge.

`runnable` is `False` while `pending` is non-empty. `placement_job.validate_intent`
classifies a dialect intent and refuses to run it with the pending paths in
the refusal, so the original `esp_prog` intent is still refused, as
AGENTEE-520 requires, but now with its complete classification.
`adapt_bytes` records the sha256 and length of the exact captured bytes.

## Pad-edge metric (`pad_edge`)

`pad_rect` is the upstream `kicad-route@c4af095` axis-aligned copper rect
(`hx = sx/2·|cos| + sy/2·|sin|`, custom pads from the effective bounding
box) and `rect_gap` the signed gap (negative overlap depth never clamped,
hypotenuse when diagonal). `claim_legs` reproduces the upstream selection:
every declared pad name must resolve or the claim is `unresolved` with no
partial legs; partners narrow to the subject pad's net when possible; one leg
per declared subject pad; without declared subject pads one minimum leg.
`pcbnew_pads` reads pad rects through pcbnew. The replay on the retained
`esp_prog` input board (`b4aa540d`) reproduces the contract's four legs
(2.9704, 3.3268, 0.6580, −0.0750 mm).

## Group links (`group_links`)

`measure(groups, footprints, rails)` reports S1 (complete partition), S2
(shared rails are an explicit evidence-bound input; nets spanning more than
two groups outside them are listed in `unknown_multi_group_nets`), R2
intra-group signal and member-to-anchor pad-edge links, R1 declared-near
interface links over shared non-rail signal nets (scoped rails only for their
declared pairs), and D1/D2 diagnostics with their limitation text.
`objective` sums the non-negative R1/R2 gaps: the quantity the solver step
will minimise. The replay on the retained final-a1 board (`dfffc042`)
reproduces the contract's power links (4.592, 3.642, 5.242 mm) and the
`usb_in` interfaces (2.1621, 12.8095 mm). A synthetic control shows the
objective strictly decreasing when a dispersed member moves next to its
partner.

## Capture, solver and reporting (second slice)

`placement_job.validate_intent` adapts a dialect intent with the supported
set `DIALECT_SUPPORTED`, so `assembly.sides`, `envelope`, connector `side`
and `cable_entry`, `pad_edge` proximity and `groups[].near` are no longer
pending; the unmeasured cable-envelope claims and withheld dispositions stay
in the problem's `compatibility` record, never satisfied. `_capture_native`
verifies the native side of every pad-bearing footprint, the Edge.Cuts
bounding box against the declared envelope and the native face of each
seated connector, refusing on mismatch (`compatibility.native_checks`).

Captured pads now carry `half_nm`, the axis-aligned half extents of their
copper rect. The solver (`crates/placement-engine/src/job.rs`) scores a
`basis: pad_edge` proximity row with the upstream signed rect gap per
declared subject pad with net narrowing (`pad_rect`, `rect_gap`,
`pad_edge_legs`), at the existing proximity weight; the excess over `max_nm`
is a soft objective for `derived_default` rows. R2 intra-group signal and
member-to-anchor links and R1 declared-near interface links are compiled
from the problem's `groups[].near` and the explicit `rails` input
(`compile_links`), scored as non-negative signed gaps at the group-scale
`link` weight (`Terms.link`), and cached incrementally like proximity. A
net spanning more than two groups outside the declared rails is never
linked, so rails cannot merge blocks. After each seed's anneal,
`refine_members` visits members by largest link gap and offers seats
adjacent to the linked partner pad in every eligible rotation, accepting a
seat only when the full evaluation stays hard-legal (zero overlap, outline,
hole and edge violations) and the cost strictly decreases; anchors and
fixed footprints never move. Seed rows report `refinement` moves and the
measured `links`. `board_metrics` includes the `group_links` report and
`pad_edge_legs` for the saved board. Rust controls: links compile from nets
with a declared rail excluded, pad-edge terms equal the signed gap, and a
legal dispersed member is moved next to its anchor with legality preserved,
the anchor and fixed marks unmoved.

## Remaining

1. Emit the per-candidate `refinement` and `links` rows into the final
   `job-report.json` claims section and bind annotated top/bottom group
   views to the candidate sha256 (reporting only; measurements exist).
2. Declare evidence-bound `rails` for the retained `esp_prog` groups (the
   captured `groups.json` carries none, so its multi-group nets are surfaced
   and not linked) and run the retained esp_prog and acquisition replays
   through the full job as the real-design qualification procedure.
3. The adversarial control on a real candidate and the independent
   candidate-bound judgments remain later acceptance steps.

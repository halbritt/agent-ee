# Board envelope record

An `agent-ee.envelope/1` record states a board's mechanics before "go": the
outline, layer count and stackup, a reference to a captured fabricator
capability profile, mounting holes, keepouts and fixed footprints such as edge
connectors (AGENTEE-85). The intent record states electrical intent; the
envelope adds what an empty candidate needs before any part is placed
(decisions 1 and 4 in the [generator direction](decisions.md#generator-direction-2026-10-17)).

```sh
PYTHONPATH=src python3 -m agent_ee.board_envelope ENVELOPE.json \
  --source-path requirements/envelope.json --out NEW_DIRECTORY \
  [--profile CAPTURED_PROFILE.json] [--board SAVED.kicad_pcb]
```

The command is offline, standard library only and deterministic. Exit 0 means
the structure passed and nothing is unresolved. Exit 1 means the structure
passed but a profile predicate is unresolved, or a saved board does not match.
Exit 2 means the record was refused, or input/output failed. The output
directory must not already exist. An I/O failure can leave a partial
directory; discard it and use a new one.

No check kind is registered. A structural pass is not placement, fabrication or
acceptance evidence. Every output records `acceptance_ready: false`.

## Record

All lengths are millimetres, exact to 1 nm (KiCad's internal unit), in KiCad
board axes with y growing downward. The [RC envelope](../fixtures/board-envelope/rc.envelope.json)
is a complete example; its [provenance](../fixtures/board-envelope/PROVENANCE.md)
separates facts read from the saved board from owned assumptions.

| Field | Content and refusals |
| --- | --- |
| `schema`, `id` | `agent-ee.envelope/1`; ID of letters, digits, `_` or `-` (at most 48) |
| `outline.edges` | Ordered named straight edges `{name, start, end}`. Each edge must end where the next starts, and the last where the first starts. Open, zero-length, overlapping or self-intersecting outlines, zero area and duplicate edge names are refused. Arcs cannot be represented |
| `layer_count` | Integer 2 to 32, equal to the stackup copper count |
| `stackup` | `thickness_mm` and `layers` from top to bottom, alternating `copper` (`name`, `thickness_mm`, `copper_weight_oz`) and `dielectric` (`name`, `thickness_mm`). Copper names are exactly `F.Cu`, `In1.Cu` … `B.Cu`. Layer thicknesses must sum exactly to `thickness_mm` |
| `fab_profile` | `null` (none selected yet) or `{name, sha256}` naming a captured profile |
| `mounting_holes` | `{id, at, drill_mm, plated}`; the whole hole must lie inside the outline |
| `keepouts` | `{id, polygon, layers, forbid}`: a simple polygon contained in the closed outline, stackup copper layer names, and a unique subset of `footprints`, `tracks`, `vias`, `pads`, `copper_pours` |
| `fixed_footprints` | `{reference, side, edge, position_mm, inset_mm, rotation_deg, insertion, tolerance_mm}` |

A fixed footprint's anchor is its KiCad footprint origin. The anchor is
`position_mm` along the named edge, measured from the edge's `start`, then
`inset_mm` toward the board interior. The position must lie within the edge
(0 to its length, inclusive), and the anchor must lie inside the outline.
`side` is `front` (`F.Cu`) or `back` (`B.Cu`).

`insertion` names how the mating part arrives:

- `through_edge`: across the edge from outside the board. The record gives the
  edge's outward axis (`x-`, `x+`, `y-` or `y+`).
- `from_top`: from above the board. Refused for a back-side footprint.
- `from_bottom`: from below the board. Refused for a front-side footprint.

In version 1 a fixed footprint may name only an axis-aligned edge. The outline
itself may be any simple straight-edged polygon, and the record reports whether
it is an `axis_aligned_rectangle`.

References, hole IDs and keepout IDs share one namespace, because KiCad
mounting holes are footprints. Duplicate names are refused. So are unknown or
missing fields at any level, duplicate JSON keys, `NaN` and `Infinity`, booleans
or strings used as numbers, values finer than 1 nm, and non-UTF-8 bytes. A
refusal never produces a partial validation record.

## Fabricator profile

Profile support stays unresolved: the validator never supplies a default
fabricator, layer limit or rule. The AGENTEE-75 `agent-ee.fab-profile/1`
contract is not on main, so the validator does not interpret profile contents.

- With `fab_profile: null`, the record reports `fab_profile_selected` as
  unresolved.
- With a reference, profile bytes passed through `--profile` must match its
  `sha256`, or the envelope is refused. Bytes passed without a reference are
  also refused.
- Whether the profile supports the layer count, and whether its name matches,
  is always reported as unresolved (`fab_profile_supports_layer_count`).

While anything is unresolved, `outcome` is `unknown`. Once the profile contract
is committed, the layer-count predicate belongs to that integration. It is not
reimplemented here.

## Derived obligations

`--source-path` is the capture-relative path at which the envelope will be
selected as a scope input. The outputs are:

| File | Content |
| --- | --- |
| `envelope.json` | The exact input bytes |
| `envelope-validation.json` | `agent-ee.envelope-validation/1`: envelope SHA-256, normalized geometry, edge outward axes, fixed-footprint anchors and mating axes, unresolved predicates |
| `envelope-requirements.json` | `agent-ee.envelope-requirements/1`: one obligation per outline, layer count, stackup, profile reference, hole, keepout and fixed footprint |
| `requirements.md` | The same obligations and unresolved predicates as text |
| `dialog-requirements.json` | The obligations as original requirements in the stage-one dialog format `agent-ee.requirements/1` (`id`, `text`, empty `source_question_ids`), from branch `impl/intent-record` (AGENTEE-26/25, not on main) |
| `round-trip.json` | With `--board` only, see below |

Each obligation has a stable ID, `R-envelope-<id>-<field>[-<name>]`. It also
has:

- a `statement` that starts with the cited field;
- `source` `{path, locator}`, where the locator is the JSON Pointer of that
  field (for example `/fixed_footprints/0`);
- `intent: "acceptance"`;
- the SHA-256 of the canonical normalized field value;
- the `check_kinds` that may evaluate it.

The same envelope bytes always produce byte-identical outputs. Reformatting the
JSON changes the envelope hash but no obligation. Changing one field changes
only the obligation that cites it.

Proposed check kinds, taken from the ticket graph and still to be confirmed by
their owners:

| Obligation | Check kind |
| --- | --- |
| Outline, holes, keepouts, fixed footprints | `placement` (AGENTEE-92) |
| Layer count, profile reference | `fab_profile` (AGENTEE-75) |
| Stackup | `fab_package` (AGENTEE-94) |

None of these kinds is registered on main. A scope/3 baseline that maps them is
therefore refused by capture today.

`agent_ee.board_envelope.require_obligations(scope, requirements)` refuses an
`agent-ee.scope/3` baseline when any obligation:

- is missing or has no mapped check;
- has a changed statement or source, or a non-acceptance intent;
- maps a check whose kind is outside its `check_kinds`.

An obligation is refused rather than dropped. The function also refuses
requirements derived while predicates remained unresolved. Calling it from
baseline compilation is an integration step that has not been done.

## Board round trip

`--board` (or `round_trip(envelope, board)`) compares the fields a saved
`.kicad_pcb` can show, parsed from its bytes with the shared S-expression
reader. It checks:

- the Edge.Cuts segments (`gr_line`, or `gr_rect` as four segments), with any
  other Edge.Cuts item a mismatch;
- the copper layer names;
- `(general (thickness))`, and per-layer thicknesses when the board saves a
  stackup;
- each fixed footprint's layer, origin within `tolerance_mm`, and exact
  rotation.

Copper weight is always `not_in_board`, because KiCad saves only copper
thickness. Holes and keepouts are `not_compared` in version 1. An independent
test loads the RC board with `pcbnew` and checks that its outline, copper
count, thickness and J1 transform agree with the static reading.

The RC envelope matches its saved board on outline, layers, thickness and J1.
The board saves no stackup section, so the per-layer split is not compared.
Moving J1 by 0.5 mm, changing one outline corner or the board thickness gives a
mismatch.

## Limits

This record states obligations; it does not check placement, courtyards,
clearances or fabrication. Arcs, cutouts, slots, multiple outlines and oblique
connector edges are not supported. The proposed check kinds, the profile
contract and baseline integration are pending other items, and are reported
here rather than assumed.

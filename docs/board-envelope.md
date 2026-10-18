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
  [--profile PROFILE.json [--sources SOURCE_DIRECTORY]] \
  [--original-requirements REQUIREMENTS.json] [--board SAVED.kicad_pcb]
```

The command is offline, standard library only and deterministic. Exit 0 means
the structure passed and nothing is unresolved. Exit 1 means the structure
passed but a profile predicate is unresolved, or a saved board does not match.
Exit 2 means the record was refused, or input/output failed. The output
directory must not already exist. An I/O failure can leave a partial
directory; discard it and use a new one.

No check kind is registered. A structural pass is not placement, fabrication or
acceptance evidence. Every output records `acceptance_ready: false`. Acceptance
needs the [baseline integration](#baseline-integration) and mapped checks.

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

`rotation_deg` is an exact decimal in [0, 360). `copper_weight_oz` is an exact
positive decimal. Each may have at most 32 digits before and after the point;
trailing zeros don't count. Anything finer or larger is refused, never rounded,
so `1E-29` stays `0.00000000000000000000000000001`. Results don't depend on
Python's ambient `decimal` context.

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
or strings used as numbers, values finer than 1 nm, and non-UTF-8 bytes.
Records over 1 MiB, polygons over 1024 vertices and lists over 1024 entries are
refused before the exact quadratic polygon checks. A refusal never produces a
partial validation record.

## Fabricator profile

`fab_profile` is `null` (none selected yet) or `{name, sha256}` naming a
captured [AGENTEE-75 profile](fab-profile.md). The validator never supplies a
default fabricator, layer limit or rule. Profile contents are read only by
`fab_profile.compile_profile` and `fab_profile.evaluate_geometry`, with
`fab_profile.simple_polygon` qualifying the declared outline. There is no second
profile interpreter.

- With `fab_profile: null`, `fab_profile_selected` is unresolved.
- With a reference, the profile bytes must match its `sha256`, and the profile's
  `name` must equal the referenced name. Otherwise the envelope is refused. Bytes
  passed without a reference are also refused. A profile the AGENTEE-75 compiler
  refuses (missing fields, duplicate JSON keys, bad limits) refuses the envelope.
- Without profile bytes, `fab_profile_bytes` is unresolved.

Support is decided only by a **ready** (source-verified) **minimum-tier** limit.
Preferred limits only classify surcharges and never decide support.

| Predicate | Limit | Result |
| --- | --- | --- |
| `fab_profile_supports_layer_count` | `max_layers` | `layer_count` above the limit is refused. Without a minimum-tier `max_layers` limit the predicate is unresolved |
| `fab_profile_supports_board_size` | `max_board_mm` | Outline extents are compared on fixed saved axes, x to x and y to y. No rotation is granted, so a 20 × 12 mm outline is refused against `[12, 20]`. Without the limit there is no predicate |

A limit is ready only when its cited source is found unchanged in a capture
inventory. Offline (`--profile` alone) no source is verified, so both predicates
stay unresolved with the compiler's reason. `--sources DIR` hashes the regular
files under `DIR` now, as capture would, and resolves the cited paths there.
Symlinks are not followed. That offline run is a preview. The baseline repeats
the same check against the actual capture.

The validation record lists each satisfied predicate with its limit ID, value and
the declared value, plus `sources_verified` and the verified `source_paths`.
While anything is unresolved, `outcome` is `unknown`.

## Derived obligations

`--source-path` is the capture-relative path at which the envelope will be
selected as a scope input. The outputs are:

| File | Content |
| --- | --- |
| `envelope.json` | The exact input bytes |
| `envelope-validation.json` | `agent-ee.envelope-validation/1`: envelope SHA-256, normalized geometry, edge outward axes, fixed-footprint anchors and mating axes, unresolved predicates |
| `envelope-requirements.json` | `agent-ee.envelope-requirements/1`: one obligation per outline, layer count, stackup, profile reference, hole, keepout and fixed footprint |
| `requirements.md` | The same obligations and unresolved predicates as text |
| `dialog-requirements.json` | The obligations as original requirements in the [dialog's](design-dialog.md) `agent-ee.requirements/1` format (`id`, `text` with source locator and envelope SHA-256, empty `source_question_ids`) |
| `stage-one-requirements.json` | With `--original-requirements` only, see [stage-one requirements](#stage-one-requirements) |
| `round-trip.json` | With `--board` only, see below |

Each obligation has a stable ID, `R-envelope-<id>-<field>[-<name>]`. It also
has:

- a `statement` that starts with the cited field;
- `source` `{path, locator}`, where the locator is the JSON Pointer of that
  field (for example `/fixed_footprints/0`);
- `intent: "acceptance"`;
- the SHA-256 of the canonical normalized field value;
- the `check_kinds` that may evaluate it.

The same envelope, profile and source bytes always produce byte-identical outputs. Reformatting the
JSON changes the envelope hash but no obligation. Changing one field changes
only the obligation that cites it.

Each obligation's `check_kinds` names the check kinds that may evaluate it. A
kind name is not coverage by itself. An obligation is covered only through a
**binding**, a validator for one `(field, kind)` pair that proves the mapped
check evaluates that exact field:

| Obligation | Check kinds | Binding today |
| --- | --- | --- |
| Profile reference | `fab_profile` (AGENTEE-75) | Bound when the check's `fab_profile` parameter names captured bytes with the referenced SHA-256 |
| Fixed footprint | `placement` (AGENTEE-92) | Bound to a `connector-edge/1` check whose `envelope` parameter names these exact captured envelope bytes, when no other requirement maps that check. The criterion grades side, anchor within tolerance, exact rotation, insertion side and the declared edge on the saved outline |
| Outline | `placement` | None: `outline-containment/1` grades courtyards against the saved outline, not the outline against the envelope |
| Keepouts, mounting holes | `placement` | None: `footprint-keepout/1` grades rule areas already saved, and no predicate checks declared holes |
| Layer count, stackup | `fab_package` (AGENTEE-94; registered as the [done gate](fab-package.md), no envelope binding yet) | None |

`connector-edge/1` returns one verdict for every fixed footprint in its
envelope. With two or more fixed footprints, a defect cannot be attributed to
one obligation, so each has unknown coverage. A check mapped by several
requirements is refused.

A `fab_profile` check's `max_layers` limit bounds only the maximum, so it cannot
establish "exactly N copper layers". The layer-count obligation is therefore
proposed for `fab_package`, not `fab_profile`. AGENTEE-75 also binds exactly one
requirement per `fab_profile` check, so the profile's limits must name the
`R-envelope-<id>-fab-profile` requirement.

Adding coverage for another field means its owning item adds a binding in
`board_envelope.BINDINGS`. Registering a kind or criterion alone covers nothing.

`obligation_coverage(scope, requirements, record, files)` reports each
obligation as `bound`, `unmapped` or `unknown`, with its locator and why it is
unbound. It refuses:

- a scope that is not `agent-ee.scope/3`;
- a changed statement, source path or locator, or a non-acceptance intent;
- a mapped check whose kind is outside the field's `check_kinds`, even an
  executable one, and a binding whose validator fails. A `fab_profile` check over
  other bytes cannot cover the referenced profile;
- a baseline `R-envelope-` requirement that the captured envelope does not
  derive;
- a validation record and requirements document for different envelope bytes.

`require_obligations(scope, requirements, record, files)` then refuses anything
not `bound`, listing every such obligation. It also refuses requirements derived
while predicates were unresolved. An obligation is refused rather than dropped.

## Stage-one requirements

`stage_one_requirements(original, dialog_requirements)` appends the envelope's
`dialog-requirements.json` to an `agent-ee.requirements/1` original set, for
example an [intent import's](design-intent.md) `requirements.json`. Both inputs
and the result pass the [dialog's](design-dialog.md) requirement validator. The
envelope entries must all be `R-envelope-` obligations without question sources.
An original set that already holds envelope obligations is refused, so nothing
is silently merged. `--original-requirements FILE` writes the result as
`stage-one-requirements.json`.

The result is the dialog's `--requirements` input. The dialog keeps original IDs
and text unchanged, and refuses a question whose `requirement_id` collides with
an obligation. So the captured decision record carries every obligation, its
envelope field and the envelope SHA-256.

## Baseline integration

A scope/3 declares its envelope in a top-level `envelope` field:

```json
{"envelope": {"path": "requirements/envelope.json",
              "profile": "requirements/fab-profile.json",
              "dialog": "requirements/dialog-requirements.json"}}
```

All three keys are required. `profile` is `null` only when the envelope
references no profile. `dialog` is the dialog's derived `requirements.json`, or
an explicit `null` to bind no decision record. While compiling the baseline,
before the coverage audit, capture:

1. reads the declared envelope and profile from captured bytes with their hashes
   checked. It refuses a reference without a declared profile path;
2. revalidates the envelope and resolves profile support against the capture
   inventory, then re-derives the obligations with `source.path` equal to the
   declared path. No caller-supplied requirements document or `complete` flag is
   trusted;
3. if `dialog` is set, refuses a decision record that lacks an obligation, holds
   different text (for example from other envelope bytes) or carries an extra
   `R-envelope-` ID. Every other consumed stage-one set gets the same test (see
   [Consumed envelopes](#consumed-envelopes));
4. applies `require_obligations` to the scope's baseline;
5. freezes the envelope, profile, verified profile sources and dialog as
   immutable baseline files. A campaign's `mutable_inputs` cannot include them;
6. records `envelope` in `agent-ee.baseline/2`: the declaration, the validation
   record, the derived requirements and the per-obligation coverage. These are
   part of `baseline_id`, so verification recompiles and compares them.

Today no envelope scope can be captured. Its outline, layer count and stackup
have no binding, nor do any holes or keepouts. Capture refuses and names those
obligations with the reason, and writes no baseline or candidate record. A scope
that uses an envelope only as `connector-edge/1` data is refused the same way.
It must declare the envelope, and then every obligation must be covered.

Tests stand in for the missing outline, layer-count and stackup bindings only
inside a test block. The fixed footprint and the profile keep their real
bindings. A native test then runs the declared RC envelope through capture, a
placement-stage DRC, the real `connector-edge/1` and `fab_profile` checks, and a
full DRC:

- J1 at its anchor passes, and so does the profile;
- J1 moved 3 mm is a connector defect, naming the left edge and the distance;
- the sealed result witnesses `R-envelope-rc-filter-fixed-J1`, and only that
  obligation;
- once the stand-in is gone, the same retained run fails baseline verification.

### Consumed envelopes

Envelope authority comes only from what a scope declares or consumes, never from
whatever happens to be captured. For scope/2 and scope/3, capture reads the
paths the scope consumes:

- any check's `envelope` parameter;
- each baseline requirement's `source.path`;
- each baseline target;
- each packet `requirements` document.

A consumed path, whatever its name or size, carries envelope authority when it
starts with `{` and parses as one of these JSON objects:

- an `agent-ee.envelope/1` envelope;
- an `agent-ee.envelope-validation/1` or `agent-ee.envelope-requirements/1`
  record derived from one;
- a stage-one `agent-ee.requirements/1` set that holds `R-envelope-`
  obligations, such as the dialog's `requirements.json`.

A check `envelope` parameter is envelope use whatever the file holds. Capture
refuses:

- an envelope field on scope/1 or scope/2;
- a consumed envelope, envelope record or stage-one envelope obligation, or an
  `R-envelope-` requirement, without a declaration. So a scope can't take its
  requirements from a stage-one set while dropping its envelope obligations;
- a consumed envelope at any path other than the declared one, including a
  byte-identical copy;
- a consumed envelope record whose `envelope_sha256` is not the declared
  envelope's;
- a consumed stage-one set that doesn't carry exactly the declared envelope's
  obligations (the same test as `dialog`);
- a requirement that cites the declared envelope as its source but isn't one of
  its derived obligations, whatever its ID.

Other captured files are not read for envelope authority. These include
`reference_architectures`, inputs no requirement or check cites, and extra
copies of the envelope. Such a file is an informative reference example, and
its content never becomes an obligation. An uncited file leaves the baseline
exactly as if it were absent. A reference architecture is hashed into the
baseline like any reference, but adds no envelope record or obligation.

## Board round trip

`--board` (or `round_trip(envelope, board)`) compares the fields a saved
`.kicad_pcb` can show, parsed from its bytes with the shared S-expression
reader. It checks:

- the Edge.Cuts segments: a board-level `gr_line`, or a `gr_rect` as four
  segments, whose only layer is `Edge.Cuts`;
- every other saved item that has `Edge.Cuts` in its own `layer` or `layers`,
  at any depth. Each is listed under `other` and makes the outline a mismatch.
  That includes footprint fields (`property`, native `PCB_FIELD`), footprint
  text and shapes, pads, dimensions and their text, targets and zones. No item
  is skipped by tag. Such an item need not be a valid contour; it is
  unsupported, so it can't match;
- the copper layer names;
- `(general (thickness))`, and per-layer thicknesses when the board saves a
  stackup;
- each fixed footprint's layer, origin within `tolerance_mm`, and exact
  rotation modulo 360 degrees, with no angular tolerance. The reference must
  match exactly one board footprint; a missing or duplicated fixed reference is
  a mismatch. A saved rotation that is not a number, or needs more than 32
  decimal places, refuses the comparison.

Copper weight is always `not_in_board`, because KiCad saves only copper
thickness. Holes and keepouts are `not_compared` in version 1. An independent
test loads the RC board with `pcbnew` and checks that its outline, copper
count, thickness and J1 transform agree with the static reading.

A second native test saves and reloads RC boards with `pcbnew`:

- J1's Value field moved to Edge.Cuts gives a mismatch with
  `footprint J1 property Value` listed;
- a footprint text and a board dimension on Edge.Cuts give a mismatch with
  each listed;
- an unmodified re-save, and the field moved to `Dwgs.User`, still match.

In every case the reloaded board confirms what is on Edge.Cuts, and the outline
segments are identical to the fixture's.

`match` means that no compared field differs. It does not mean the envelope is
satisfied: holes, keepouts, copper weight and unsaved stackup layers are not
compared.

The RC envelope matches its saved board on outline, layers, thickness and J1.
The board saves no stackup section, so the per-layer split is not compared.
Moving J1 by 0.5 mm, changing one outline corner or the board thickness gives a
mismatch.

## Limits

This record states obligations; it does not check placement, courtyards,
clearances or fabrication. Arcs, cutouts, slots, multiple outlines and oblique
connector edges are not supported. Outline, keepout, hole, layer-count and
stackup bindings do not exist yet (layer count and stackup need a `fab_package` binding), so
an envelope scope/3 is refused at capture until they land. Capture reads only
consumed paths for envelope authority, so an envelope a scope never cites stays
unenforced. To make an envelope binding, declare it. Profile support means only that the typed, source-quoted minimum
limits admit the declared values, not that a fabricator accepts the board.

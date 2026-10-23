# Board envelope record

An `agent-ee.envelope/1` or `/2` record states a board's mechanics before "go": the
outline, layer count and stackup, a reference to a captured fabricator
capability profile, mounting holes, keepouts and fixed footprints such as edge
connectors (AGENTEE-85). The intent record states electrical intent; the
envelope adds what an empty candidate needs before any part is placed
(decisions 1 and 4 in the [generator direction](decisions.md#generator-direction-2026-10-17)).

The [telemetry-pod fabrication inputs](telemetry-pod-fab-profile.md) include a
source-verified fab profile and a structurally valid envelope/2. It separates
nominal and physical thickness with a cited tolerance; mechanical obligations
remain unbound until AGENTEE-177/179. Acceptance remains unknown.

```sh
PYTHONPATH=src python3 -m agent_ee.board_envelope ENVELOPE.json \
  --source-path requirements/envelope.json --out NEW_DIRECTORY \
  [--profile PROFILE.json [--sources SOURCE_DIRECTORY]] \
  [--original-requirements REQUIREMENTS.json] [--board SAVED.kicad_pcb]
```

The command is offline, standard library only and deterministic. Exit 0 means
the structure passed and nothing is unresolved. Exit 1 means the structure
passed but a profile/source predicate or mechanical obligation is unresolved, or a saved
board does not match.
Exit 2 means the record was refused, or input/output failed. The output
directory must not already exist. An I/O failure can leave a partial
directory; discard it and use a new one.

A structural pass is not placement, fabrication or acceptance evidence. The
validation record always carries `acceptance_ready: false`. The registered
[`board_envelope` check](#native-envelope-check) compares outline, copper layers
and source-bound stackup, declared holes and saved keepouts. Acceptance still needs the
[baseline integration](#baseline-integration) and mapped checks for every obligation.

## Record

Envelope lengths are millimetres, exact to 1 nm (KiCad's internal unit), in KiCad
board axes with y growing downward. The separate mechanical-reservations document
uses integer nanometres. The [RC envelope](../fixtures/board-envelope/rc.envelope.json)
is a complete example; its [provenance](../fixtures/board-envelope/PROVENANCE.md)
separates facts read from the saved board from owned assumptions.

| Field | Content and refusals |
| --- | --- |
| `schema`, `id` | `agent-ee.envelope/1`; ID of letters, digits, `_` or `-` (at most 48) |
| `outline.edges` | Ordered named straight edges `{name, start, end}`. Each edge must end where the next starts, and the last where the first starts. Open, zero-length, overlapping or self-intersecting outlines, zero area and duplicate edge names are refused. Arcs cannot be represented |
| `layer_count` | Integer 2 to 32, equal to the stackup copper count |
| `stackup` | `thickness_mm` and `layers` from top to bottom, alternating `copper` (`name`, `thickness_mm`, `copper_weight_oz`) and `dielectric` (`name`, `thickness_mm`). Copper names are exactly `F.Cu`, `In1.Cu` … `B.Cu`. Each copper entry may add `role: "plane"` or `role: "signal"`; other values and dielectric roles are refused. Omitted roles preserve legacy scaffold typing (signal outer layers, power inner layers); explicit roles are compared with saved layer types by round-trip and native envelope checks. This applies to envelope/2 as well. Layer thicknesses must sum exactly to `thickness_mm` |
| `fab_profile` | `null` (none selected yet) or `{name, sha256}` naming a captured profile |
| `mounting_holes` | `{id, at, drill_mm, plated}` plus optional nonnegative `tolerance_mm` (default 0); `id` selects the board reference and the whole hole must lie inside the outline |
| `keepouts` | `{id, polygon, layers, forbid}`: a simple polygon contained in the closed outline; alternatively replace `polygon` with `antenna: {reference, facts_check}` for the legacy ESP32-S3-WROOM-1 derivation, now explicitly unresolved. Stackup copper layer names and a unique subset of `footprints`, `tracks`, `vias`, `pads`, `copper_pours` |
| `fixed_footprints` | `{reference, side, edge, position_mm, inset_mm, rotation_deg, insertion, tolerance_mm}` plus optional `courtyard_overhangs`: a nonempty list of `{edge, max_mm}`, one per distinct axis-aligned outline edge, with positive `max_mm`. It is the only way a fixed courtyard may leave the outline: by at most `max_mm` across that edge, and into the corner between two adjacent declared edges ([AGENTEE-212](placer.md#real-pod-residuals-agentee-212)). The derived fixed-footprint obligation states it, and `outline-containment/1` with `envelope` grades it |
| `max_component_height_mm` | Optional in both versions; positive numeric installed height limit, exact to 1 nm. Derives its own acceptance obligation and binds only `board_envelope` `component-height/1` |
| `constraint_provenance` | Optional in both versions; who fixed each constraint, keyed by the field pointer its obligation cites. See [constraint provenance](#constraint-provenance) |
| `mechanical_reservations` | Optional in both versions; exactly `{path, sha256}` referencing a captured `agent-ee.mechanical-reservations/1` document. Its engineering source and separate original-board snapshot must also be captured, hash-matched and immutable. Required unresolved geometry or coverage cannot pass. See [mechanical reservations](mechanical-reservations.md) for fields, coordinate transforms and supported geometry |

The mechanical reference derives its own acceptance obligation, bound to a
`placement` check with exactly `criterion: "mechanical-access/1"`, `drc_check`
and `envelope`. This check compares fresh saved native coordinates against the
separate original snapshot. `original_board_path` cannot name the current
candidate board, even when its hash matches. Omitting the optional field adds
no mechanical restriction or judgment to legacy packing.

Version 2 changes `stackup` to `{nominal_thickness_mm,
physical_thickness_mm, layers}` and requires `source` on each layer, in the
fab profile's existing path/hash/source/as_of/page/quote format. Layers must
sum exactly to the physical thickness. The referenced fab-profile/2 may supply
`finished_thickness_tolerance: {percent, min_nominal_thickness_mm, source}`.
The compiler verifies its citation; the envelope compares physical and nominal
thickness using exact rational arithmetic, including the tolerance endpoints.
An out-of-range sum or nominal below the captured applicability is refused.
Missing source evidence stays unknown. The validator's `stackup.thickness_mm`
is the nominal value for existing native/fab consumers; its additional fields
retain the physical total, citations and source paths.
The finished-thickness tolerance is the fabricator's tolerance on the board.
It is not a component-side fit tolerance. The
[through-hole thickness fit](#through-hole-thickness-fit) reads only the
nominal value. Version 1 keeps the
exact shape and sum in the table above and refuses version 2 fields.

Each **dielectric** layer in envelope/2 may also declare these material fields
(AGENTEE-186). They use that layer's existing `source` citation; adding a value does not supply
new manufacturing authority. Copper layers and envelope/1 refuse them.

| Field | Meaning |
| --- | --- |
| `dielectric_type` | `core` or `prepreg`; emitted as the native stackup layer type |
| `epsilon_r` | Positive, finite relative permittivity; emitted as native `epsilon_r` |
| `loss_tangent` | A finite nonnegative value when explicitly known, or `null` when unknown. Zero is an explicit value, not a synonym for unknown |

Declaring any material field requires both `dielectric_type` and a positive,
finite `epsilon_r`, including when loss is `null` or zero. Type-only and
type-plus-loss declarations refuse before native serialization can supply Er.
Omitting all material fields preserves legacy
envelopes; it does not establish a material value. Boolean values, numeric strings,
nonfinite numbers and negative loss are refused. The producer must cite evidence
supporting the declared material and its applicability; a thickness-only citation
does not substantiate permittivity or loss. Source verification checks captured
bytes, hashes and quote presence; it does not interpret the material claim.
Numeric material values use the same
exact 32-place decimal limits as copper weights; excess precision is refused.

When any material field is declared and loss is omitted or `null`, the scaffold
explicitly writes `loss_tangent: 0.02`, the serialization default verified by
native load/save with KiCad 10.0.7. This is a **tool default**; the envelope
retains unknown loss. Legacy layers without material fields keep their original
emission behavior. A different saved value is still unqualified when the source
declares unknown loss; it is not necessarily a KiCad default.

`stackup/1` compares supplied type, permittivity and numeric loss exactly with
saved values. A proven material difference produces a defect even when loss is unknown.
The public board-envelope check and retained `envelope-judgment.json` preserve
that defect outcome unless the native probe adds a disagreement, which takes
`unsupported`/`unknown` precedence while retaining the defect evidence.
Unavailable saved stackup or material values remain unknown;
missing evidence is not a proven material mismatch.
A malformed saved Er that pcbnew refuses ends as a FAILED check (`execution: failed`)
with no `envelope-judgment.json`, distinct from unavailable or missing material
data that produces an unknown judgment.
Without a proven difference, declared unknown loss produces an unknown result.
Both results retain the unknown-loss reasons and
`material_evidence` with the source value, serialized value and unqualified
status. Round-trip evidence marks that loss field `not_compared` and retains
`serialized_value`; it never substitutes the saved value into the envelope.
The existing `compare_stackup` profile thickness and copper-weight comparisons
are unchanged. Metadata agreement cannot qualify impedance:
`impedance_qualified` remains false when material fields are declared. Unknown
loss cannot qualify a material model either.

This extension does not change the selected JLC04161H-7628 architecture:
four copper layers, nominal 1.6 mm and physical sum 1.5862 mm. The retained
[fabrication record](../fixtures/telemetry-pod/sources/jlcpcb-stackup.txt)
specifies 0.035 mm outer copper, 0.0152 mm inner copper, two 0.2104 mm 7628
prepreg layers and a 1.065 mm core. It contains no loss value. The separate
[RF capture](../fixtures/telemetry-pod/rf-source-capture.json) records 7628
prepreg permittivity 4.4; it does not supply a core permittivity or loss.
The [later RF calculation](telemetry-pod-delivery-plan.md)'s
`T1 = 0.04064 mm` (1.6 mil) is a calculation input,
not authority to replace the captured 0.035 mm fabrication copper.

Version 2 also requires `obligations`, a list of `{id, requirement_ids,
statement, owner_items}`. Each ID list is nonempty and unique. These carry
requirements whose geometry is a generator decision. Every entry derives an
acceptance requirement with its envelope field hash and remains unresolved;
there is no caller-supplied pass/bound flag. Baseline capture recognizes both
versions, freezes verified stackup sources and refuses missing check coverage.
Existing native outline and layer criteria do not cover these obligations.
An entry may add a `coverage` record that stages it for candidate generation
(see [staged obligations](#staged-obligations)); without one it stays unresolved.

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

## Constraint provenance

An owner must be able to tell the limits they stated from the defaults an agent
proposed (AGENTEE-266). In the telemetry pod, an 80 mm x 50 mm outline the
owner never specified was compiled into a hard ceiling. Its source text also
said "or smaller" and "the envelope is not tight". Placement was blocked at
95.6 % single-sided capacity for days.

`constraint_provenance` maps a field pointer to one entry. The pointers are
`/outline`, `/layer_count`, `/stackup`, `/fab_profile`,
`/max_component_height_mm` (when present), and `/mounting_holes/<i>`,
`/keepouts/<i>`, `/fixed_footprints/<i>` and `/obligations/<i>`. Each class
has exact fields:

| `class` | Fields | Enforcement |
| --- | --- | --- |
| `owner_stated` | `quote`; `source: {path, sha256}` | Hard, unless the quote weakens the limit or does not state the number in context (below) |
| `derived` | `derivation` text; `from`, a list of other pointers, and/or `facts`, a list of `{quote, source}` owner facts; at least one citation | Hard, unless a cited field is soft, the derivation or a fact weakens the limit, or the derivation does not state the derived number (below) |
| `assumed` | `rationale` text | Always soft |

Mechanical reservations have a separate source record whose `classification`
must be `engineering`. This is not another `constraint_provenance.class` and
does not assert owner or manufacturer authority. Captured attribution, numeric
derivations and uncertainty remain visible; the selected reservation's required
check cannot turn an unsupported engineering assumption into an owner-stated
limit. Its document, cited source and original-board snapshot join the immutable
baseline dependencies.

The [owned fixture](../fixtures/board-envelope/provenance/envelope.json) has
all three classes. It is stated against an owned synthetic
[owner statement](../fixtures/board-envelope/provenance/owner-statement.md).

- **Sources.** With a capture inventory (`--sources`, baseline capture, or
  the placement adapter), a cited source must be in the inventory and have the
  cited SHA-256. The quote must appear in its UTF-8 text, comparing runs of
  whitespace as one space. Otherwise the envelope is refused (AGENTEE-271). Only
  a validation with no inventory at all (the CLI without `--sources`) leaves a
  quote `verification: not_captured`. Baseline capture freezes verified sources
  as inputs.
- **Weakening.** An owner quote, derivation text or derivation fact that
  weakens a limit, or that sets a minimum rather than a maximum, is never
  compiled into a hard limit. The wording list (`WEAKENING` in
  `board_envelope.py`) includes "not tight", "not fixed", "soft limit",
  "room in/for/to" (not "no room"), "there is room", "plenty of room/space",
  "ideally", "preferably", "if possible", "flexible", "or so", "target",
  "aim for", "goal", "preliminary", "tentative", "provisional", "placeholder",
  "TBD", "draft", "initial", "for now", "currently", "subject to change",
  "estimate", "guess", "rough(ly)", "approximate(ly)", "maybe", "probably",
  "could", "might", "at least", "minimum", "or larger/bigger/more", "20ish",
  and "about", "around", "approx", "circa", "~" or "≈" before a number.
- **Stated numbers.** For `/outline`, `/layer_count` and
  `/max_component_height_mm`, an owner-stated quote must state the field's
  value in its dimensional context, and only one candidate value. The outline
  (the bounding box width and height) is one `W x H mm` or `W mm by H mm` pair
  in either order, or `W mm square` when the outline is square. A layer count is
  `N layers`, `N copper layers` or `N-layer`. A height is `N mm` in a quote that
  says tall, height or high. "20 mm by 12 mm or smaller" is a hard maximum.
  "Small, 20 mm wide or smaller" is soft, because the pair is not stated. A
  matching number elsewhere ("20 parts, 12 mm from the wall"), a second
  outline ("the enclosure is 40 x 30 mm and the board 20 x 12 mm"), or one
  side of a square outline ("20 mm wide") is soft.
- **Derivations.** A derivation that cites an `assumed` field is refused,
  because a limit derived from an assumption is an assumption. Cycles, unknown
  pointers and repeated citations are refused too. A derivation of `/outline`,
  `/layer_count` or `/max_component_height_mm` is soft unless its `derivation`
  text states the derived value in the same form; it may also state its inputs
  ("24 x 16 mm less 2 mm each side gives 20 x 12 mm").

The validation record's `constraint_provenance` lists every constrained field
with `class`, `enforcement` (`hard` or `soft`) and, for a soft field,
`soft_reason`. It also has `owner_confirmation` (the soft pointers),
`source_paths` (verified sources) and `status`. Status is `declared` when every
field has provenance, else `unknown`.

A field without provenance has class `unknown` and is enforced exactly as
before. It is never treated as owner-stated. Records without
`constraint_provenance` therefore keep every verdict. Provenance never changes
`structural` or `outcome`.

A soft field's obligation is `informational`, not acceptance. Its statement
starts with `ASSUMED, not a hard limit until the owner confirms it`, or with
`SOFT owner statement` or `SOFT derivation`, followed by the reason. The text
reaches `requirements.md` and the dialog requirements, so the owner sees it as
an assumption to confirm. `envelope-requirements.json` carries `provenance` by
obligation ID. Obligation coverage reports a soft obligation as `soft` and
requires no check for it. A baseline that maps it as acceptance is refused.
Placement reads the outline and fixed-reference provenance from the captured
envelope ([placer](placer.md#constraint-provenance-agentee-266)).

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
| Outline | `board_envelope`, `placement` | Bound to `board_envelope` with `criterion: "outline/1"` over these exact captured envelope bytes. Placement's `outline-containment/1` does not bind this field |
| Keepouts, mounting holes | `board_envelope`, `placement` | `keepouts/1` or `mounting-holes/1`, selecting the declared `id`; placement alone does not bind either field |
| Layer count | `board_envelope`, `fab_package` (AGENTEE-94; registered as the [done gate](fab-package.md), no envelope binding yet) | Bound to `board_envelope` with `criterion: "layers/1"` over these exact captured envelope bytes |
| Stackup | `board_envelope`, `fab_package` (AGENTEE-94; registered as the done gate, no envelope binding yet) | Bound to `stackup/1` when the referenced source-verified profile declares the ordered stackup, copper weights and explicit layer-thickness matching tolerance |

`connector-edge/1` selects one fixed footprint with its `reference` parameter.
Each reference needs its own check and derived obligation; a missing SMA cannot
make an AMPSEAL witness unknown. With exactly one fixed footprint, the selector
may be omitted for compatibility. Multiple footprints without a selector stay
unknown at binding and are refused as executable capability. A selector for
another reference, or a check mapped by several requirements, is refused.

A `fab_profile` check's `max_layers` limit bounds only the maximum, so it cannot
establish "exactly N copper layers". The layer-count obligation is therefore
owned by `board_envelope`. AGENTEE-75 also binds exactly one requirement per
`fab_profile` check, so the profile's limits must name the
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

`require_obligations(scope, requirements, record, files, inputs)` then refuses anything
not `bound`, `soft` or `staged`, listing every such obligation. It also refuses requirements derived
while predicates were unresolved. An obligation is refused rather than dropped.

## Staged obligations

A version 2 `/obligations/<n>` entry states a generator decision, so no check
binds it. Its optional `coverage` record separates candidate-generation
admission from final physical qualification:

```json
{"coverage": {
  "generation": [{"locator": "/fixed_footprints/0"},
                 {"locator": "/fixed_footprints/0", "criterion": "outline-containment/1"}],
  "open": [{"id": "panel-cutout", "stage": "physical_qualification",
            "statement": "Panel cutout fit is not yet qualified."}]
}}
```

- `generation` lists the witnesses, which may be none. A witness without a
  criterion is a declared `/mounting_holes/<i>`, `/keepouts/<i>` or
  `/fixed_footprints/<i>` of the same envelope. Its own derived obligation
  must be `bound`; a soft (informational) or unmapped one does not witness.
  `criterion: "outline-containment/1"` is allowed only for a fixed footprint
  with `courtyard_overhangs`. It needs a `placement` check with that criterion
  over these exact captured envelope bytes. `criterion: "part-identity/1"` on a
  fixed footprint needs the contracted part identity (see
  [source contracts](#source-contracts)). No other field witnesses, so
  `/max_component_height_mm` and `component-height/1` cannot cover a staged
  obligation.
- `open` lists the qualification evidence still required, one or more
  `{id, stage, statement}` items. `stage` is `candidate_assessment` (needs a
  generated candidate, for example a placement assessment) or
  `physical_qualification` (enclosure, fabrication or bench evidence). An empty
  list, a status field or any other key is refused, so the envelope never
  claims qualification.

The record holds locators and text only, so it cannot add a numeric limit. A
bound, drill or position keeps the provenance of the concrete field it points
at. Validation moves a staged entry's predicates from `unresolved` to
`qualification_open`, one per open item. The outcome stays `unknown`, and the
derived requirement's statement names the witnesses and open items.
`obligation_coverage` reports the entry as `staged`, with each witness's
requirement, checks and status, and the open items. A missing or unbound witness
makes it `unknown`. Soft provenance on the generic obligation itself does not
change this: it stays `staged` with its open items. A baseline requirement that maps a staged obligation to a
check, or changes its statement, is refused: no check qualifies it.

`require_obligations` admits `staged` entries, so capture, scaffold and
candidate generation can proceed. `require_qualified(coverage)` is the final
physical-qualification gate. It refuses while any staged obligation has open
items, naming each one, and refuses any obligation that is not bound. Nothing in
the envelope or scope can close an open item. Closing one requires
AGENTEE-177/179 evidence that adds a real binding.

### Final acceptance guard

`final_qualification(run, candidate_id)` is the one shared final guard. It
reads the coverage in the run's captured, re-verified `baseline.json`
(`load_baseline`), never the current envelope file, a scope field or a stored
result. It returns `agent-ee.final-qualification/1` with the `baseline_id` and
a `blocking` list, or `None` for a verified capture without an envelope.
`candidate_id` is the identity the consumer's own retained evidence was
produced for: the review result, or the done check's result. The guard first
checks `candidate.json` against its `candidate_id`, then requires that identity
to equal the consumer's, and only then verifies the baseline and reads the
scope's envelope declaration. It never infers "no envelope" from a missing
record. A removed or edited declaration, a missing, empty, null or
envelope-free `baseline.json`, and a re-identified scope therefore refuse: the
identity differs; or it belongs to another candidate than the consumer's
evidence; or the recompiled baseline differs from the retained one, or refuses
the envelope the scope still consumes. `None` means either a verified scope/2
or scope/3 baseline without envelope coverage, or a scope/1 candidate, which
compiles no baseline and must retain no `baseline.json`. `verify` passes in the
baseline it has just verified, so the guard compiles it no second time
(AGENTEE-250).
Each blocker is
`{id, status, open}`: a `staged` obligation with its open item IDs, an
obligation that is not `bound` or `soft`, a derived obligation missing from the
coverage, or unresolved requirements. An empty `unresolved` list is not
readiness: a staged obligation has none and still blocks. `require_final(run, candidate_id)`
raises while anything blocks. Its consumers:

- the review outcome. `review` records the guard in `result.json` as
  `final_qualification` and adds a limitation naming every blocker. A result
  that checks and reviewers would otherwise accept is `incomplete`, not a design
  defect; `defects_or_unresolved` and other verdicts do not change. `verify`
  recomputes the record from the baseline and refuses a removed, emptied or
  stale one.
- the `fab_package` done gate. The fab export and consistency gate still run,
  so the exports stay available as diagnostics. A package that would pass is
  `unsupported`/`unknown` (`fab package done gate held: ...`), and
  `artifact_record` refuses to seal the release. `artifact_record` applies
  the guard last, after the placement release lineage and the package checks,
  bound to the done check's `candidate_id`, which the retained fab export
  manifest must also carry.
- campaign acceptance and replay. A campaign that reaches `criteria_satisfied`
  re-applies the guard before it publishes `fab_artifacts` or `stage_done`;
  while anything blocks it ends `incomplete` with the blockers in `error`.
  `verify-campaign` applies the guard to the final run of an accepted state, so
  a stale or manually altered acceptance is refused. A declared fab step is
  generation, so open qualification does not stop it being scheduled; the done
  gate then holds its release.

Staged generation keeps `require_obligations`: capture, scaffold and candidate
generation proceed. The guard adds no physical closure: today's open items stay
blocking for final acceptance.

### Source contracts

A coverage list is written by whoever composes the envelope, so it cannot set
its own minimum. `board_envelope.OBLIGATION_CONTRACTS` holds the reviewed,
source-bound minimum (AGENTEE-197 review H1/H2). Only an obligation whose `id`,
`requirement_ids`, `owner_items` and `statement` equal a contract entry verbatim
may be staged. A coverage record on any other obligation is refused. A changed
source statement refuses staging until a reviewed commit updates the entry.
While anything is staged, every contracted obligation must be present, so one
cannot be dropped. The contract's `envelope_ids` name source envelopes that must
carry every contracted obligation verbatim even before staging.

For each staged entry the contract, not the coverage list, fixes the generation
witnesses and the open items. Coverage may add witnesses or items and may use
any order, but it cannot omit or restage what the contract lists. The
validation record keeps the resolved minimum as `obligation_contract`, and
`staged_coverage` checks it again. The contract carries roles, references and
identities only, so it adds no numeric limit.

`telemetry-pod-expanded/1` is reviewed from the production capture
`composition/envelope.json` (envelope `expanded-pod-microsd-production`):

| Obligation | Required generation witnesses | Open items that must stay listed |
|---|---|---|
| `corner-holes` | the source holes `H1`, `H2`, `H3` and `H4`, each uniquely nearest a distinct outline corner | `hole-authority`, `fabricated-holes` |
| `height` | none | `candidate-heights` (candidate), `cowl-fit`, `underside-fit`, `connector-fit`, `mating-plug-fit`, `harness-fit` |
| `harness-edge` | J1 fixed (`connector-edge/1`), J1 `outline-containment/1`, J1 `part-identity/1` | `panel-cutout`, `mating-plug`, `retention`, `harness-clearance`; `thickness-tolerance` only when the J1 thickness screen does not pass |
| `antenna` | U1 fixed, U1 `outline-containment/1`, the U1 antenna keepout | `feed-point-side`, `housing-pocket`, `nearby-metal`, `support`, `rf-performance` |
| `imu-separation` | none (assessment only) | `placement-assessment` (candidate), `vibration-coupling` |

Items are `physical_qualification` unless marked candidate. The corner witnesses
are bound by source identity: the envelope must declare exactly holes `H1`–`H4`,
matched by `id` in any order. Each is a required witness at its own locator. A
missing, renamed or extra hole is refused, including an extra hole at the same
coordinates as a source hole or tied between corners. Adding a hole role needs a
reviewed contract change. Corner assignment is a one-to-one nearest-vertex
comparison with ties refused. It is not a hole position limit.
Drill, inset and coordinates stay generator-selected and keep the provenance of
the hole fields. `hole-authority` stays open for them.

J1 board thickness is screened, not left open (AGENTEE-446). The `harness-edge`
contract carries a J1 component-side declaration: TE's recommended 1.57 mm,
cited to customer drawing 776163 rev K1, sheet 2 ("RECOMMENDED P.C. BOARD
LAYOUT FOR 1.57 THICK P.C. BOARD"). Part number and footprint come from the
contracted J1 identity. When `harness-edge` is staged, the record grades it with
the [through-hole thickness fit](#through-hole-thickness-fit) `judge` against
the envelope nominal thickness. The fab finished-thickness tolerance is not
read. Before judging, the contract declaration passes the same
`tht-thickness-fit/1` declaration validator as a captured
`tht-thickness-fit.json`. If that validator refuses it, the screen is `unknown`
with reason `thickness fit declaration refused: ...`, never `pass`. Causes
include a missing or blank citation, a missing or unsupported source class, or
an unknown field. The result is kept as
`obligation_contract.thickness_fit.harness-edge` and on the staged coverage
entry. At the pod's 1.6 mm nominal it is `pass`,
`screened (default +/-10% component fit)`, band 1.413-1.727 mm. If the screen
does not pass (refused, or a nominal outside the band, which is advisory
`unknown`), coverage must list `thickness-tolerance` as a
`physical_qualification` open item, or staging is refused.

The source statement still says "board thickness tolerance ... remain
unqualified", because the contract must match the hash-bound source envelope
verbatim. A source change needs a reviewed contract update. The record therefore
keeps two statements under `obligation_contract.statements.harness-edge` and on
the staged coverage entry (`statement`). `source` is that verbatim text, and
`current` is the accurate current statement. While the screen passes, `current`
is the contract's `screened_statement`: J1 component board-thickness fit is
screened, and panel cutout, mating plug, retention and harness clearance remain
unqualified. Otherwise `current` equals `source`. `requirements.md` prints it as
`Current statement (obligation harness-edge): ...`. Derived requirement
statements and baseline mappings keep the source text. This screen is not a TE
compliance claim and not physical retention evidence.

J1 identity is not deferred into physical fit. It uses the existing parts
enforcement, not a second identity checker. A `part-identity/1` witness is bound
only when every `parts_subset` check in the scope (the scaffold's native
membership check) reads a captured `agent-ee.parts/1` manifest. That manifest
must declare one required purchased root-sheet part for J1 with manufacturer
`TE Connectivity`, MPN `1-776163-1` and footprint
`PodConnector:AMPSEAL_35_1-776163-1`. The footprint must be an exact member of
the manifest's captured CAD closure, and that member is the one the scaffold
loads. The complete manifest must then pass the authoritative
`parts_manifest.qualify_manifest`: the canonical part record or catalog row is
replayed, the native CAD assignment coverage is complete, and J1's datasheet
provenance is captured. Declared strings alone do not bind. A missing check, an
uncaptured manifest or member, a missing or optional part, or another MPN,
manufacturer or footprint leaves the witness `absent`, so capture refuses. The
same applies to a self-consistent substitution behind the contracted strings,
such as another part's record or a re-sealed footprint with changed pads. At run time the `parts_subset` check compares the native J1
with that declaration. DRC schematic parity and `connector-edge/1` bind the
board footprint of the same reference. Pose alone is not identity: a
`connector-edge/1` pass proves neither identity nor mating-plug or enclosure fit.
At preparation time the same part is bound by `binding_fill.fill` and
`parts_manifest.qualify_manifest`.

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

A scope containing outline, layers, a source-bound stackup, the referenced
profile and separately selected fixed connectors can now capture with all
obligations listed as bound in `baseline.json`. Declared holes and keepouts also
bind through their id-selected checks. Unresolved generator mechanics still
have no binding. Capture refuses those obligations by name and writes no baseline or candidate record,
unless a [coverage record](#staged-obligations) stages them. Using an envelope only as
`connector-edge/1` data still requires declaring it and covering every obligation.
The complete pod fixture retains its AGENTEE-177/179 mechanical obligations;
a scoped contract fixture does not establish acceptance of the complete pod.

Some integration tests on owned synthetic profiles stand in for outline,
layer-count and stackup bindings only inside a test block. They do not establish
production acceptance. The recorded JLCPCB profile path below uses no stand-in. A separate
test uses real bindings and confirms that only stackup blocks the sourced RC
scope. The fixed footprint and the profile keep their real
bindings. A native test then runs the declared RC envelope through capture, a
placement-stage DRC, the real `connector-edge/1` and `fab_profile` checks, and a
full DRC:

- J1 at its anchor passes, and so does the profile;
- J1 moved 3 mm is a connector defect, naming the left edge and the distance;
- the sealed result witnesses `R-envelope-rc-filter-fixed-J1`, and only that
  obligation;
- once the stand-in is gone, the same retained run fails baseline verification.

### RC envelope on the recorded JLCPCB 2-layer profile

[rc-jlc-2layer.envelope.json](../fixtures/board-envelope/rc-jlc-2layer.envelope.json)
is the RC envelope with one change: `fab_profile` references the recorded
[JLCPCB 2-layer profile](../fixtures/board-envelope/jlc-2layer/fab-profile.json)
(`jlc-2layer-fr4-1oz`, AGENTEE-85). The profile follows the
[telemetry-pod capture pattern](telemetry-pod-fab-profile.md#sources-and-replay).
It cites one original numbers-only record and eight 2-layer minima: trace and
spacing 0.10 mm, via drill and diameter 0.15/0.25 mm, routed copper-to-edge
0.2 mm, hole-to-hole 0.45 mm, two layers and 670 × 600 mm. It also cites the ±10%
finished-thickness tolerance. Its [capture manifest](../fixtures/board-envelope/jlc-2layer/source-capture.json)
records the URLs, UTC times and response hashes of the host capture on
2026-10-19. The raw responses stayed in the worker's scratch directory.

With the captured sources, the variant validates with `outcome: pass`: both
layer count and board size are supported. JLCPCB publishes the 1.6 mm nominal
thickness, 1 oz (35 µm) outer copper and a single FR-4 dielectric for 2-layer
boards, but no dielectric thickness. Its stackup page and impedance calculator
hold 4- and 6-layer templates only. The profile therefore declares a partial
`stackup` (`jlc-2layer-fr4-1oz-1.6mm`), citing one recorded quote. It gives the
1.6 mm nominal and two 1 oz, 0.035 mm copper layers. The `core` thickness and the
physical total are `null`, meaning unpublished; see
[profile stackup authority](fab-profile.md#stackup-authority). Nothing derives
the core as nominal minus copper, because that would treat the physical thickness
as the nominal one, which the fabricator does not state. The RC's 1.53 mm core
stays an owned assumption ([decision](decisions.md#rc-2-layer-stackup-authority-agentee-85-2026-10-22)).

The copper-weight authority is enough to bind `stackup/1`, so the RC scope
captures with every obligation bound. Running the check:

- compares the nominal thickness, layer names and kinds, copper weights and
  copper thicknesses against the profile; a mismatch is a defect;
- reports the envelope's and the saved board's core thickness as unknown,
  because no published value bounds them. Without a proven defect, the verdict
  is unknown, never pass;
- reports the RC board itself as unknown, because it saves no stackup section.

`tests.test_board_envelope` covers this:

- `RcJlcProfileTests` compiles the profile and its partial stackup, and checks
  the recorded source hashes. It refuses a four-layer variant, and refuses a null
  thickness on copper, on an envelope layer or beside a numeric physical total.
  It also derives byte-identical, field-citing requirements;
- with the profile minus its `stackup` (its bytes before this change), outline,
  layer count, J1 and the profile bind, and capture is refused naming only
  `R-envelope-rc-filter-stackup`. With the recorded profile, every obligation
  binds and capture freezes the profile and its source;
- the native coordinator-path test uses real `outline/1`, `layers/1`,
  `stackup/1`, `connector-edge/1` and `fab_profile` checks, with no stand-in
  (AGENTEE-92 criterion 3). The saved, routed RC board meets all eight minima.
  Moving J1 3 mm on an unrouted candidate is a defect naming `left` and 3.0 mm.
  Its DRC-backed minima stay unknown without a rule-loading witness. `stackup/1` is
  unknown with `missing or duplicate saved stackup`, and the retained run stays a
  valid baseline.

`tests.test_envelope_check.PublishedCopperWeightAuthorityTests` grades the
partial authority on an owned board that saves the RC stackup. The unpublished
core is unknown. A 2 oz envelope layer, a saved 0.036 mm copper layer and a
1.0 mm nominal are each defects.

The shared `rc.envelope.json` and the placement fixture copy keep `fab_profile:
null`. They are the unselected-profile case, and these existing tests assert it:

- `test_rc_envelope_validates_structurally_with_profile_unresolved`;
- `test_profile_reference_is_hash_bound_and_malformed_profiles_are_refused`;
- `test_every_requirement_cites_a_resolving_envelope_field`;
- `test_unresolved_profile_is_refused_even_when_every_obligation_binds`;
- `tests.test_scaffold` `test_uncovered_binding_is_refused_instead_of_weakening_scope`.

Pointing them at the profile changes those tests' expected refusals and makes
every scope that copies the file declare and capture the profile and its source.
Production RC scopes use `rc-jlc-2layer.envelope.json` instead.

## Native envelope check

`board_envelope` takes `criterion` and `envelope`. The hole and keepout criteria
also require an `id` selecting exactly one declared item:

```json
{"id": "envelope-outline", "kind": "board_envelope",
 "params": {"criterion": "outline/1", "envelope": "requirements/envelope.json"}}
```

Use one check per field (or per hole/keepout) and one requirement per check. The envelope path must
name captured immutable bytes; the scope's board remains a candidate design
input. Baseline compilation records the envelope dependency and refuses a
criterion mapped to another field, different envelope bytes, or a check shared
by other requirements. Generation acceptance requires the scope/3 declaration
above and complete obligation coverage; missing manufacturing authority stays unknown.

| Criterion | Capability predicate | Native comparison |
| --- | --- | --- |
| `outline/1` | `envelope:outline/1` | Exact Edge.Cuts segment inventory against the envelope, corroborated by a native load proving one closed straight contour |
| `layers/1` | `envelope:layers/1` | Exact ordered copper layer names, corroborated by native enabled layer IDs and names |
| `stackup/1` | `envelope:stackup/1` with captured authority | Saved nominal thickness and ordered layer names/kinds/thicknesses match the profile within its explicit exact tolerance; declared copper weights match its sourced weights |
| `mounting-holes/1` | `envelope:mounting-holes/1:<id>` | Exactly one footprint with reference `id`, origin within the declared Euclidean tolerance, one round drilled pad whose centre is within the same tolerance, of the declared diameter and PTH/NPTH attribute, and its whole circle inside the native outline |
| `keepouts/1` | `envelope:keepouts/1:<id>` | At least one saved rule area covers every edge of the declared polygon, all declared copper layers and at least the declared forbid set |
| `component-height/1` | `envelope:component-height/1` | Every native footprint has a verified applicable installed maximum height or qualified resolved STEP bounds at most `max_component_height_mm` |
| `tht-thickness-fit/1` | `envelope:tht-thickness-fit/1` | Every declared through-hole part, bound to one installed PTH footprint of the declared part, fits the envelope nominal thickness under its explicit hard limits, documented fit range or the default +/-10% screening band. It binds no envelope obligation |

### Component height

Declare `max_component_height_mm: 12` in the envelope and map its derived
`/max_component_height_mm` requirement to one check:

```json
{"id": "height", "kind": "board_envelope", "params": {
  "criterion": "component-height/1", "envelope": "envelope.json",
  "facts_check": "facts", "height_facts": {"C8": "c8-installed-height"}
}}
```

`facts_check` names an earlier `datasheet_facts` check. `height_facts` explicitly
maps reference to fact ID; both parameters are optional but must occur together.
A selected verified fact must have `kind: "component_height"`, `unit: "mm"`,
`measurement: "installed_maximum_above_board"`, a positive finite `value`, and
`part_number` and `footprint_id` matching the captured part binding and native FPID.
The shared part-binding reader resolves `Partnumber`/`PartNumber`; a missing MPN
property may use the footprint value as its part identifier. A declared MPN
overrides a nominal display value such as `100uF`. Conflicting properties or
saved/native UUID, FPID or value disagreement stay unknown.
The shared facts schema retains the captured PDF hash, page, locator, quote and
conditions. PDF verification checks the citation text; installed orientation and
maximum applicability remain engineer-interpreted assertions. An unverified,
wrong-part, wrong-footprint, nominal or axial-reference fact stays unknown.
An explicitly selected unusable fact does not fall through to another source.

`digikey_heights` (AGENTEE-254) takes a reference's height from a captured
[DigiKey capture](digikey-capture.md#check-evidence) instead:

```json
{"digikey_heights": {"capture": "parts/digikey/digikey-capture.json",
                     "capture_sha256": "<sha256 of the manifest>", "parts": {"U3": "TPS62840"}}}
```

The manifest and every retained response it names become immutable capability
inputs; the check replays them offline and refuses a capture whose hash differs
from `capture_sha256` or whose files were not captured. The record's
`Height - Seated (Max)` text is read as `0.069" (1.75mm)` or `1.75mm`. Each
printed figure counts as rounded to its last digit, and an inch figure narrows
the mm interval or, if it disagrees, refuses it. The upper bound must be at most
the maximum for a pass. A defect needs the lower bound above it. An interval
straddling the maximum is unknown. The saved `Partnumber`/`PartNumber` must
equal the DigiKey MPN exactly, and a saved `Manufacturer`, when present, must
match case-insensitively. A missing part number, mismatched part, absent or
unparsed parameter, or a not-found or failed request is unknown. A reference
listed in both `height_facts` and `digikey_heights` is a refused declaration.
With an optional `package_map` (AGENTEE-274) the DigiKey package must also map
to the native footprint ID. A
[package mismatch](digikey-capture.md#check-evidence) is a defect for that
reference. An unmapped name or a record without a package is unknown.
DigiKey stock and price are not carried into the verdict. Seated height is
DigiKey's listed maximum above the seating plane, as the source states it;
the engineer still owns its applicability to the installed orientation.

For references without a selected fact, the check reuses the fabrication model
resolver. `model_libraries` has the same stock-variable-to-captured-receipt map
as [fab consistency](fab-consistency.md). Stock members require their pinned
receipt hashes; local relative and `${KIPRJMOD}` model paths must resolve inside
captured inputs. All captured STEP assets and declared receipts become immutable
capability inputs, including before an empty scaffold gains footprints. Selecting
these dependencies from mutable board contents would change the baseline during
generation. A separate sandboxed native probe loads each STEP with KiCad 10.0.7
`UTILS_STEP_MODEL.LoadSTEP` and records its bounding box. Positive model scale
and Z offset are applied to the model's maximum outward Z. Upright models are
qualified: X/Y model rotation must be a multiple of 360 degrees; footprint
rotation and front/back placement preserve this local height. Multiple models
must all resolve and load; the largest installed top is graded. Native bounding
box tolerances are retained, with no inferred acceptance tolerance.

No model, missing model, hidden model, VRML, absolute/embedded/alias model, tilted or
nonpositive-scale model, invalid STEP or nonpositive/invalid bounds is unknown.
A native crash, timeout or missing output remains a failed/unknown execution.
Every footprint is checked, including DNP and hand-fitted parts. A missing source
never exempts TE 776228-1; AGENTEE-209's unresolved installed-height question
remains open. No 34.25 mm axial-reference sum is accepted as installed height.

The inclusive limit uses exact decimal source values. A 13 mm capacitor fails
a 12 mm envelope; exactly 12 mm passes for a verified fact. Per-reference
verdicts and source provenance are retained in `envelope-judgment.json`, with
`height-model-request.json` and `height-model-bounds.json` for native models.
An unknown reference keeps aggregate coverage unknown while preserving any
other proved defects. This criterion measures outward height above each mounting
surface; it does not add PCB thickness or prove a two-sided enclosure clearance.
The pod fixture's generator obligations still need their own bindings; adding
this field does not silently discharge its legacy `/obligations` entries.

For example, a hole witness uses:

```json
{"id": "hole-h1", "kind": "board_envelope",
 "params": {"criterion": "mounting-holes/1", "envelope": "requirements/envelope.json", "id": "H1"}}
```

Hole judgments retain `id`, native footprint/pad observations and mismatch reasons.
Missing or duplicate mounting references are defects. Duplicate native footprint
or pad UUIDs make the probe fail with unknown coverage; they cannot overwrite
observations or alias rule-area ownership. Position tolerance includes
its boundary for both the footprint origin and the drilled-pad centre; drill
diameter is exact. KiCad copper-shape offsets do not move the drill and do not
fail this criterion. Slotted or multiple drilled pads do not satisfy the declared
round hole.

Generic keepouts may match board-level or footprint-owned rule areas. Native
KiCad loading supplies board coordinates for both, including a moved/rotated
footprint's saved owned polygon. Polygon coverage tests complete edges, so a
concave notch cannot pass merely because the vertices lie inside. Missing areas,
insufficient layers, a missing forbidden item or a shrunk polygon are defects;
unsupported relevant contours or placement-area semantics leave coverage unknown. No DRC result substitutes
for this declaration comparison. This check grades saved rule-area fidelity;
placement/routing checks still grade objects that violate the area.

For an ESP32-S3-WROOM-1 antenna declaration, use `antenna` instead of `polygon`:

```json
{"id": "antenna", "antenna": {"reference": "U1", "facts_check": "antenna-facts"},
 "layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
 "forbid": ["footprints", "tracks", "vias", "pads", "copper_pours"]}
```

`facts_check` still selects a prior `datasheet_facts` check, but this legacy
ESP32 dynamic declaration currently returns unknown. The former AGENTEE-170
clearance derivation misread the pin-15 label in Figure 11-1 as 15 mm clearance.
That derivation is withdrawn even for previously verified text records. The
three authentic module dimensions do not define a base-board minimum.

The [source context](footprint-libraries.md#footprint-owned-keepout-rule-areas-and-esp32-s3-wroom-1)
separates outside-board placement, conditional asymmetric cutouts and housing
clearance, with captured guideline provenance. These contexts deliberately
withhold geometric qualification. The retained stock footprint rectangle is CAD
geometry only. It cannot make this dynamic predicate pass. Explicit polygon
checks still grade fidelity to an authored declaration; they do not qualify a
manufacturer's antenna placement policy. Scaffold callers receive the same
explicit unresolved error rather than an invented replacement polygon.
`keepouts/1` stays unknown for this declaration. The outside-board case itself
is graded by the separate [antenna placement check](antenna-placement.md)
(AGENTEE-453): saved pose, crossed edge, feed-point corner (an agent-derived
class default, not manufacturer authority) and retained module keepout, as a
bare-board gate. The envelope's `antenna` contract and its open
obligations are unchanged by that check.

Launch preflight requires the recorded `pcbnew` runtime. The coordinator runs
`agent_ee.envelope_probe` in the networkless tool sandbox,
records the board hash and pcbnew version, and retains `envelope-probe.json`,
`envelope-judgment.json`, the execution record and console. A proven mismatch
is `execution: ok`, `outcome: defect`. Unsupported Edge.Cuts geometry, malformed
or contradictory facts, tool failures and missing output cannot pass. Direct
execution of `stackup/1` stays unknown without a source-verified `stackup`
declaration in the envelope's referenced fab-profile/2. The owned
[explicit-stackup fixture](../tests/fixtures/envelope/rc-explicit-stackup.kicad_pcb)
alone therefore remains unknown. Authority is resolved from `scope.envelope.profile`,
whose bytes must match the envelope reference, never from an unrelated check or
uncited file. The profile, citations and envelope are immutable capability inputs;
the judgment retains profile identity and compiled authority. No conversion from
thickness to ounces is inferred. See [profile stackup authority](fab-profile.md#stackup-authority).

`tests.test_envelope_check.PodEnvelopeAcceptanceTests` exercises the scoped pod
contract with an original scaffold and two fixed connectors. It runs the native
checks for matching stackup, a 2 oz declaration against a 1 oz profile, a saved
layer outside the profile tolerance, and an unverified source. It also checks
separate AMPSEAL/SMA verdicts, the bound-obligation capture receipt, and named
hole/keepout refusals. Run it on the host with the strict toolchain gate:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest tests.test_envelope_check -v
```

### Through-hole thickness fit

A soldered through-hole part fits a board within a range around the PCB
thickness its manufacturer recommends (AGENTEE-448). This component-side
tolerance is separate from the fabricator's finished-thickness tolerance
(`finished_thickness_tolerance`, for example the JLCPCB +/-10% source). The
check never reads that tolerance, the physical stackup total or any other fab
tolerance as component fit. The board side is the envelope's nominal
`stackup.thickness_mm` (`nominal_thickness_mm` in envelope/2), and the native
board thickness must equal it.

The check selects the captured immutable declaration with `thickness_fit`:

```json
{"id": "j1-fit", "kind": "board_envelope", "params": {
  "criterion": "tht-thickness-fit/1", "envelope": "envelope.json",
  "thickness_fit": "tht-thickness-fit.json"}}
```

```json
{"schema": "agent-ee.tht-thickness-fit/1", "parts": [
  {"reference": "J1", "part_number": "1-776163-1",
   "footprint_id": "PodConnector:AMPSEAL_35_1-776163-1",
   "recommended_mm": 1.57,
   "recommended_source": {"class": "manufacturer",
     "citation": "TE Connectivity 1-776163-1 customer drawing: recommended PCB thickness 1.57 mm"}}]}
```

Each part may also declare `hard_limits: {min_mm?, max_mm?, source}`, the
manufacturer's explicit limits with at least one bound, and
`fit_range: {min_mm, max_mm, source}`, a documented fit range. Every
`source` is `{class, citation}`. The class is `manufacturer` or `datasheet`,
and the citation is a nonempty string. A missing or blank citation, an unknown
field, a duplicate reference, a bound that excludes the recommended nominal, or
a value that is not exact to 1 nm is refused at capture, and the check reports
it as malformed. The citation names its source. Authenticating that source is
outside this check.

Each side of the band (minimum and maximum) is graded on its own, with exact
rational arithmetic and inclusive bounds:

| Governing bound for that side | Board nominal inside | Board nominal outside |
| --- | --- | --- |
| Explicit `hard_limits` bound | pass | **defect** |
| Otherwise the `fit_range` bound, where it is wider than the default band | pass | unknown: needs feature geometry or evidence |
| Otherwise the default band, recommended x 0.9 or x 1.1 | pass, `screened (default +/-10% component fit)` | unknown: needs feature geometry or evidence |

A documented fit range only widens the default band, one side at a time: the
minimum is the lower of `fit_range.min_mm` and recommended x 0.9, and the
maximum is the higher of `fit_range.max_mm` and recommended x 1.1. A range
inside the band, such as 1.55-1.60 mm for J1, leaves the band at
1.413-1.727 mm, so a 1.65 mm board is still screened. Only an explicit
`hard_limits` bound can narrow the pass region. When one part is a defect and
another is unknown, the check reports unknown and keeps the defect in
`defects`.

The +/-10% default implements an owner assumption, owner-relayed (agent-509,
Cairn 9561cd0c, 2026-10-22) and quoted verbatim from the AGENTEE-448 ticket: "a
soldered THROUGH-HOLE component fits boards within +/-10% (or more) of its
recommended nominal PCB thickness". The rest of this section is agent wording.
Agent reading: the band is a screening default, not a universal hard ceiling,
so a board outside it is never rejected. Only an explicit manufacturer limit
makes a defect. Each declared part must bind to exactly one native footprint with that reference and the declared
`footprint_id`. That footprint needs at least one plated through-hole pad, and
its saved part number (or value) must equal `part_number`. Anything else is
unknown, never a pass. Parts without a declaration are not graded, so every
existing check keeps its verdict. The criterion covers no envelope field.
Baseline binding refuses it for any envelope obligation, so it is mapped only
by a requirement that names component thickness fit.

For J1, TE 1-776163-1 recommends 1.57 mm. The default band is 1.413-1.727 mm,
and the 1.6 mm pod board is about 1.9% above the recommendation, so it is
screened. The J1 declaration is part data for the consuming design. In
fome-mc it is a captured `tht-thickness-fit.json` holding the J1 entry above,
plus one `board_envelope` `tht-thickness-fit/1` check over the pod envelope.
agent-ee carries only the contract and original test fixtures.

`tests.test_tht_thickness_fit` covers J1 at 1.6 mm, the exact 1.413 and
1.727 mm boundaries and 1 nm outside them, 1.40 mm as advisory, explicit-limit
pass and fail, documented ranges that are wider, narrower or one-sided wider
than the band, hard limits narrowing a documented range, mixed defect and
unknown parts (the summary names the defect), refused citations, unbound parts, and
proof that the fab tolerance does not change the verdict. Its native tests run
the registered check over original pcbnew boards:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest tests.test_tht_thickness_fit -v
```

## Consumed envelopes

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

The record states obligations; acceptance requires their registered checks.
Arcs, cutouts, slots, multiple outlines and oblique connector edges are not
supported by this envelope format. The diagnostic round trip does not replace
the native hole, keepout or component-height predicates.
Stackup acceptance needs captured manufacturing authority. JLCPCB publishes
2-layer copper weight and nominal thickness but no dielectric thickness, so the
RC stackup binds yet stays unknown on its core. Several fixed
footprints need separate reference selectors. Capture reads only
consumed paths for envelope authority, so an envelope a scope never cites stays
unenforced. To make an envelope binding, declare it. Profile support means only that the typed, source-quoted minimum
limits admit the declared values, not that a fabricator accepts the board.

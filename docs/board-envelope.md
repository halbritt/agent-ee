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
| `mounting_holes` | `{id, at, drill_mm, plated}` plus optional nonnegative `tolerance_mm` (default 0); `id` selects the board reference and the whole hole must lie inside the outline |
| `keepouts` | `{id, polygon, layers, forbid}`: a simple polygon contained in the closed outline; alternatively replace `polygon` with `antenna: {reference, facts_check}` for the legacy ESP32-S3-WROOM-1 derivation, now explicitly unresolved. Stackup copper layer names and a unique subset of `footprints`, `tracks`, `vias`, `pads`, `copper_pours` |
| `fixed_footprints` | `{reference, side, edge, position_mm, inset_mm, rotation_deg, insertion, tolerance_mm}` plus optional `courtyard_overhangs`: a nonempty list of `{edge, max_mm}`, one per distinct axis-aligned outline edge, with positive `max_mm`. It is the only way a fixed courtyard may leave the outline: by at most `max_mm` across that edge, and into the corner between two adjacent declared edges ([AGENTEE-212](placer.md#real-pod-residuals-agentee-212)). The derived fixed-footprint obligation states it, and `outline-containment/1` with `envelope` grades it |
| `max_component_height_mm` | Optional in both versions; positive numeric installed height limit, exact to 1 nm. Derives its own acceptance obligation and binds only `board_envelope` `component-height/1` |
| `constraint_provenance` | Optional in both versions; who fixed each constraint, keyed by the field pointer its obligation cites. See [constraint provenance](#constraint-provenance) |

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
retain the physical total, citations and source paths. Version 1 keeps the
exact shape and sum in the table above and refuses version 2 fields.

Version 2 also requires `obligations`, a list of `{id, requirement_ids,
statement, owner_items}`. Each ID list is nonempty and unique. These carry
requirements whose geometry is a generator decision. Every entry derives an
acceptance requirement with its envelope field hash and remains unresolved;
there is no caller-supplied pass/bound flag. Baseline capture recognizes both
versions, freezes verified stackup sources and refuses missing check coverage.
Existing native outline and layer criteria do not cover these obligations.

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

A scope containing outline, layers, a source-bound stackup, the referenced
profile and separately selected fixed connectors can now capture with all
obligations listed as bound in `baseline.json`. Declared holes and keepouts also
bind through their id-selected checks. Unresolved generator mechanics still
have no binding. Capture refuses those obligations by name and writes no baseline or candidate record. Using an envelope only as
`connector-edge/1` data still requires declaring it and covering every obligation.
The complete pod fixture retains its AGENTEE-177/179 mechanical obligations;
a scoped contract fixture does not establish acceptance of the complete pod.

Some integration tests stand in for outline, layer-count and stackup bindings
only inside a test block. They do not establish production acceptance. A separate
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
layer count and board size are supported. JLCPCB publishes 1 oz (35 µm) outer
copper and a single FR-4 dielectric for 2-layer boards, but no dielectric
thickness. Its stackup page and impedance calculator hold 4- and 6-layer
templates only. The profile therefore declares **no** `stackup`. Deriving the
core as nominal minus copper would treat the physical thickness as equal to the
nominal one, which the fabricator does not state. The RC's 0.035/1.53/0.035 mm
split stays an owned assumption, and stackup authority stays unknown.

`tests.test_board_envelope` covers this:

- `RcJlcProfileTests` compiles the profile, checks the recorded source hashes,
  refuses a four-layer variant and derives byte-identical, field-citing
  requirements;
- with real bindings only, outline, layer count, J1 and the profile bind, and
  capture is refused naming only `R-envelope-rc-filter-stackup`;
- the native coordinator-path test uses real `outline/1`, `layers/1`,
  `connector-edge/1` and `fab_profile` checks, with a stand-in for stackup
  alone. The saved, routed RC board meets all eight minima. Moving J1 3 mm on an
  unrouted candidate is a defect naming `left` and 3.0 mm, and its DRC-backed
  minima stay unknown without a rule-loading witness. Once the stand-in is
  removed, the retained run fails verification on stackup.

Production capture of the RC envelope needs a fabricator-stated 2-layer
dielectric thickness. That needs a new source or an owner decision.

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
Stackup acceptance needs captured manufacturing authority; JLCPCB publishes
no 2-layer dielectric thickness, so the RC stackup has none yet. Several fixed
footprints need separate reference selectors. Capture reads only
consumed paths for envelope authority, so an envelope a scope never cites stays
unenforced. To make an envelope binding, declare it. Profile support means only that the typed, source-quoted minimum
limits admit the declared values, not that a fabricator accepts the board.

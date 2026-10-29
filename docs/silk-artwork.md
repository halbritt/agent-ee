# Post-placement silkscreen artwork (AGENTEE-320)

The disposable placer relocates visible Reference and Value fields when native
DRC shows that placement added `silk_overlap` or `silk_over_copper`. Additional
custom **fields** require the footprint property
`agent_ee_movable_silk_fields`, containing a JSON array of field names, for example
`["Assembly label"]`. Footprint graphic text (`fp_text`), segments, polygons,
polarity and pin-1 graphics are never edited. Board drawings remain unchanged.

Only field positions may change. Text, UUID, size, thickness, visibility, angle,
justification, layer and mirroring remain intact. A field below the native
project's minimum silk text height or effective native stroke width, or mirrored inconsistently with
its silk side, is left in place with a diagnostic. The pass never deletes,
hides, shrinks, rotates or moves a required marking to another layer.

The search uses pcbnew's native text bounding boxes, including the current font
metrics, rather than estimating widths from character counts. This follows the
measured-text principle of [AGENTEE-301](schematic-emitter.md#detached-text-and-native-measurement-agentee-301);
the schematic SVG parser is specific to schematic plotting and is not reused
for PCB field geometry. Candidate offsets use a 0.25 mm grid within 10 mm of the
field's post-placement anchor, ordered by squared displacement then coordinates.
The existing qualified outline parser requires the entire text box, grown by
at least 0.2 mm or the native silk clearance, to remain inside one qualified
board contour, outside all qualified internal slots and cutouts (curved
contours use the conservative bounds below). Same-side native silk and mask geometry, visible fields, and
surface pad copper/mask bounds reserve space. Other footprints' same-side
courtyard bounding boxes also reserve their entire interiors, so moved labels
cannot end up beneath another assembled component. The owning courtyard is
exempt; the conservative bounding boxes can reject free space in concave
courtyards. KiCad 10 sometimes omits a custom field UUID from its report; native bounds then nominate declared fields near
the cited item, and the final DRC verifies the change. No bounding box establishes a
passing clearance verdict. Conservative reservations and finite search can
leave a resolvable conflict for a later placement attempt.

Fields on footprints whose side changed from the source are left in place,
with typed `placement_silkscreen_conflict` diagnostics carrying reason
`footprint_side_flipped`. The write scope does not authorize their relocation.

The writer saves the board, restores the source `.kicad_pro` and `.kicad_dru`
bytes (or their absence), and reruns native DRC with those unchanged source
project settings. It compares category and native item UUID multisets against
the baseline using the existing placement DRC comparison. Collision and mask
comparisons include saved geometry and footprint poses (AGENTEE-325), both
against the baseline and when checking the artwork pass for regressions. A moved
cross-footprint collision remains introduced after field relocation. A proposal
with only remaining `silk_overlap` or `silk_over_copper` conflicts may publish
with open defects (AGENTEE-333). Baseline findings
retain their ordinary grading, as do other check categories; no severity,
exclusion or project rule is changed. Relocation also refuses any newly
introduced native finding outside those two conflict categories, while retaining
other findings that were already present before the artwork pass. Explicitly
ignored `silk_*` categories (including `silk_edge_clearance`), tool errors and
missing native outputs refuse publication.

`pre-artwork-drc.json` retains the candidate before relocation.
`placement-silkscreen.json` records moved fields, search diagnostics, before/after
added counts and remaining `placement_silkscreen_conflict` findings. Native
conflicts carry the complete native finding; unsupported side-flip diagnostics
carry the field UUID, reference and reason. They remain open defects even if a
second field can move clear of the flipped field. An unresolved graphic conflict
is also carried. It requires a different footprint placement or human resolution.
Search exhaustion reports the bounded search limitation and does not
prove physical infeasibility. Failed candidates retain diagnostics in the writer's
failure directory; published proposals retain theirs in the proposal directory.
Fresh saved-board probes and write-scope proofs
refer to the final board bytes. Coordinator checks remain authoritative for
adoption.

`tests.test_silk_artwork` qualifies original front/back, required-text, value,
custom-field authority, graphics, minimum-size, mirroring, unavailable-coverage,
baseline-defect, exhausted-search, courtyard-interior, flipped-footprint and
rule-byte preservation cases with native DRC. Real pod probes are
private worker evidence, not repository fixtures or a claim that those designs
have no remaining silk conflicts.

With the courtyard reservation, a native probe of the archived main-buck pod
reduces 45 added `silk_over_copper` and 27 added `silk_overlap` findings to
10 and 2 respectively. Its proposal gate accepts with 12 typed open defects and
retains one placement-added `silk_edge_clearance` finding. The pth-fixed pod
reduces 38 copper and 25 overlap findings to 9 and 7, accepting the proposal gate
with 16 typed open defects. These bounded searches can leave resolvable conflicts. Placement-added `silk_edge_clearance`
remains reported in the DRC comparison and retains its existing grading. The
artwork pass refuses any additional edge-clearance regression it introduces.

## Placement feasibility and fab legibility (AGENTEE-333)

Publication preserves unresolved findings in `native_drc_comparison.open_defects`
and the top-level placement receipt `open_defects`. Each record has
`type: placement_silkscreen_conflict`, `status: open`, `verdict: defect` and the
complete native finding, or the unsupported side-flip field diagnostic. A receipt
with any open defects has `outcome: defect` and `verdict: defect`. A receipt
without these defects retains informational outcome; it proves no fab acceptance.
Collision, clearance, PTH courtyard, inter-footprint mask, courtyard, write-scope
and artwork regression refusals remain unchanged. No baseline defect is waived.

The coordinator captures adopted placement helper receipts and their sealed result,
saved board, native DRC/execution and optional artwork report in the reserved
`placement-evidence` directory. The derived successor scope carries ordered
`placement_provenance` links; `placement-evidence/index.json` retains the same
order for nested fab-stage exports. Neither directory nor declaration can be
supplied in a campaign's initial sealed scope. Every successor inherits the
bytes unchanged; campaign verification recomputes the owed capture from anchored
stage receipts and the qualified adapter record. The writer cannot remove or
edit this evidence, and routing or other implementation steps retain it.

Checks in verification/fab/release stages and the fab-family kinds refuse while
carried defects remain open, regardless of declared stage (AGENTEE-344). These
are exactly the coordinator ruling's 18 kinds: `fab_export`, `fab_package`, `fab_roundtrip`, `fab_consistency`,
`cpl_rotation`, `jlc_dfm`, `fab_profile`, `dfm_geometry`, `dfa_dft`,
`assembly_readiness`, `jlc_cost_estimate`, `drc`, `part_binding`,
`package_consistency`, `parts_subset`, `land_pattern`, `catalog` and
`part_selection`. Campaign acceptance also refuses open carry when no fab or
verification check was declared, including acceptance projected by a bench-feedback epoch. Each typed `placement_silkscreen_conflict`
finding names its native field/graphic UUID, available description/reference
and source receipt. The
check retains a candidate-bound `placement-defects.json` witness. Sealing
reconstructs the refusal from captured receipt artifacts; fab package artifact
extraction refuses open carry too. Missing, changed or malformed evidence cannot
produce a pass. Candidates without carry retain their existing fab behavior,
including uncarried baseline silk findings.

The one exception is a `drc` declaring `projection: "placement-stage/1"` with a
placement-stage or unstaged declaration (AGENTEE-346). That check grades
instead of refusing. A `drc` under a schematic, routing, verification, fab or
release declaration is outside the exception and still refuses. A native silk conflict that is exactly an open carried defect is
graded as carried, not as a violation, so the campaign advances to routing.
There the full `drc` and later gates refuse until closure. See the
[check reference](check-reference.md#carried-silk-in-the-placement-projection-agentee-346).

A board edit or a clean DRC delta alone does not close carry. Closure requires a
later receipted placement result for the current board, complete native DRC with
no remaining silk finding on the same identities, and preserved item ownership,
marking content, layer and style. Missing, hidden or relabeled fields do not
close a defect. Every closed field/graphic must also be visible silk wholly
inside the proven board outline and outside every internal slot/cutout. Both producers measure native
bounding boxes from the final saved board in their networkless tool process,
checking every box edge against the qualified outline. The outline includes
board drawings and footprint-owned Edge.Cuts, as in placement qualification.
AGENTEE-362 qualifies one outer ring with disjoint, strictly internal straight
cutout rings. AGENTEE-368 extends this to curved Edge.Cuts: circles (round
mounting holes), arcs (oblong slots, curved board edges) and native rounded
rectangles. Each curved ring is replaced by the exact integer polygons proven
for courtyards ([AGENTEE-212](placement.md#circular-and-arc-courtyards-agentee-212), `courtyard_curves`, within
5 um): a cutout uses its circumscribed superset polygon, the outer ring its
inscribed subset polygon, so the qualified region lies inside the true board.
A rounded-rectangle corner arc is cited by an exact rational point on its
circle; rotated or over-rounded rectangles stay unsupported. Topology is
checked on these bounds: the subset outer ring must still strictly enclose
every superset cutout, so a cutout closer to a curved edge, or to another
cutout, than the bounds can separate leaves the outline unqualified.
Touching/crossing contours, nested cutouts, multiple board islands, open rings,
curves below twice the tolerance and polygons with arcs establish no
containment. Bounding boxes may neither cross nor touch a cutout bound, nor
enclose an entire cutout. The 5 um tolerance bounds how far each polygon
(the circumscribed cutout bound and the inscribed outer-ring bound) may deviate
from its true curve; it is not a clearance guarantee and not a uniform
exclusion band. A mark is judged only against the bound polygon: a mark
touching or crossing the bound refuses even where it does not touch the true
circle (up to 5 um away), while a mark between two bound vertices may qualify
with less than 5 um to the true circle, because it still lies outside the
bound.

Containment models only Edge.Cuts. Non-plated drills (for example an NPTH
mounting-hole footprint) and plated or oval pad slots are not holes in this
outline model: a marking over such a drill is neither excluded by the
containment witness nor relocated around the hole itself. Relocation still
reserves the pad's copper/mask bounds where the pad has them, and native
saved-board DRC remains the authority on silk over those features
(AGENTEE-368 G2). The fome-mc telemetry pod (published allocation, fome-mc
3d7ff5f) uses NPTH mounting-hole footprints on a straight 90 x 65 mm outline,
which qualified before and after AGENTEE-368.
The same qualification gates relocation, including its clearance margin.
Placement outline grading retains its existing single-contour contract.
Entirely off-board, edge-straddling and cutout markings stay typed open defects
even when DRC is silent. The sealed placement result retains `containment`,
with the saved `board_sha256` and the native `markings` UUID list. Closure and
campaign replay consume that receipt-bound witness without launching pcbnew.
Missing, malformed or board-mismatched witnesses close nothing; historical
receipts lacking the witness retain their open defects until a fresh receipt.
Unqualified outlines cannot close carry.
This applies to both placement and routed-artwork receipts. An unsupported
side-flip diagnostic also requires positive
artwork movement of that field, either recorded by the later artwork pass or
visible in its saved position. The original side-flip case stays open even when
another field moves and native `added_after` is empty. A later board may retain
that closure across masked copper track/arc edits only when the shared saved-board
parser validates the structure and every other root-level node has identical raw
source bytes. Decoded atoms cannot establish equivalence: KiCad's string escape
decoding differs from the shared parser's. Comments (`;` outside quoted strings
and KiCad `#` line comments)
refuse the proof. A `#` after only spaces, tabs, carriage returns or NUL bytes
at the start of a line is a comment.
A track span is exempt only when its raw source has balanced parentheses,
bare tokens matching `[A-Za-z0-9_.+-]+`, and double-quoted strings without
backslashes, embedded quotes or control characters. Only spaces, tabs, LF and
CRLF separate tokens. Semicolons and hashes anywhere in the span, mid-token quotes,
other whitespace and unknown track children (including mask exposure) receive
no exemption: their raw bytes remain in the comparison and reopen closure when
changed. This also retains printable hashes and semicolons inside quoted strings.

Masked-track edits on nets whose names contain `#` or `;` (for example `CS#`
or `A;B`) never receive the exemption, even when KiCad writes the net name as a
quoted string. Such edits reopen closure and require a fresh receipt. This is a
conservative liveness bound of the writer grammar (AGENTEE-360).
A segment or arc refuses comparison with `ValueError` when a child is
list-headed (for example `((x))`) or when a value of a `start`, `end`, `mid`,
`width`, `layer`, `net`, `uuid` or `locked` child is a nested list (for example
`(start (1) 2)`), on any layer. Nested lists inside unknown track children are
not refused: those children stay in the exact comparison. The coordinator
retains the carried defect instead of letting an untyped error escape
(AGENTEE-364).

Whitespace inside retained nodes is significant; whitespace between root children is not.
Footprint transforms, marking positions/content/ownership, pads, mask, vias,
zones and setup must remain unchanged. Closure binds the complete captured
`.kicad_pro` and `.kicad_dru` path/digest inventories from the native DRC receipt
and current candidate, rather than guessing a board-stem rule path. This is a
conservative superset of the files native DRC loaded: even an unrelated captured
rule file's addition, removal or change reopens carry. Changes
outside that subset require a fresh receipt; a track comparison supplies no
electrical or routing acceptance (AGENTEE-344). If the equivalence proof cannot
read or parse the later board, the carried defect stays open.

### Board lineage stamp (AGENTEE-351)

Carry in `placement-evidence` follows a campaign's candidate lineage. A delivered
board copied into a new scope without that directory would lose it, so the board
bytes name their own lineage. When a published result has open defects, the
placement writer sets a title block comment to
`agent-ee.placement-lineage/1 <digest>...`. Each digest is `carry_digest` of one
result's open defects: the SHA-256 identity of their sorted native item UUID
sets. A later result with open defects appends its digest; a result without open
defects keeps the source stamp. The writer then reruns native DRC on the stamped
bytes and refuses unless the comparison is unchanged, so `native-drc.json` and
`board_sha256` describe the stamped board. The rerun classifies findings under the
same captured rule provenance (`finding_sources` of the source project) as the
recorded comparison, and compares each comparison list as a finding multiset,
because `kicad-cli` does not keep violation order stable between runs of the same
board (AGENTEE-383). Among findings of one comparison identity, added findings are
chosen in canonical order, so the comparison depends only on the findings. The
rerun compares with native saved states on both sides, so collision identities bind
to saved geometry and the stateless unmeasured-collision refusal (AGENTEE-369) does
not apply to it; moved copper is still a new collision. Any
difference still refuses: the error names each changed comparison key with its
added/removed counts and the changed finding types and item UUIDs, and
`native-drc-restamp.json` (retained with a failed proposal) holds the full
difference. The stamp goes in comment 9; when comment 9 holds user text, it
goes in the highest free comment, and an existing stamp is extended in its own
slot (AGENTEE-412). User text is never replaced. The writer refuses only when
all nine comments hold user text, and it rereads the saved stamp before the
DRC rerun. Placement write scope admits only a stamp that adds or extends this
comment. Dropping or rewriting a digest, or editing any other title block field,
is a violation. Campaign verification also refuses a successor candidate whose
board drops or rewrites its predecessor's stamp, whichever step wrote it.

A gated check (verification, fab and release stages, and the 18 fab-family
kinds in `GATED_KINDS`, see the [check reference](check-reference.md)) on a stamped board refuses as
unknown, with execution `malformed`, when the candidate captured no
`placement-evidence` or that evidence binds no stamp digest.

Captured evidence closes carry only when native DRC reproduces it. Imported
bundles have self-recomputed hashes and receipt identities, so self-consistency
proves nothing. When a bundle on the captured board's hash would close a carried
defect, the gated check first recomputes native DRC on the captured board bytes
in the tool sandbox (`kicad-cli pcb drc --severity-all`). The run is kept under
`work/<check>/placement-lineage/`, and the witness
`work/<check>/placement-lineage.json` binds the board hash, the argv, the report
digest and the report's `silk_*` findings. The check refuses as unknown when
those findings differ from the bundle's claimed `silk_*` findings, or when the
verification cannot run or cannot be trusted. That covers a missing or changed
board, a tool failure, a `.kicad_dru` rule that sets `severity ignore`, and a
report that ignores a `silk_*` check; an exhausted deadline is `interrupted`.
An honest closure that native DRC reproduces still closes carry. Open carry with
no closure keeps its determinate `defect` verdict and needs no native run, so an
expired budget still reports the known carried defects. The evidence seal and
release artifact extraction re-verify closures from the check's sealed witness.
They recheck its board hash, argv, execution record and report digest, and
recompute the findings from the report. A closure with a missing or changed
witness is refused.

To release a re-scoped design, include the delivered `placement-evidence`
directory as an input at `placement-evidence`. Its `index.json` then supplies
the ordered links, and open carry gates the release exactly as it does in the
campaign. This happens even after carry closes, because the stamp stays on the
board. The stamp is a lineage record in the design file, not an owner evidence
store. Owner-authenticated imported evidence awaits AGENTEE-348.

The stamp is read only with the shared S-expression parser, so an escaped marker
is still the stamp. Only a title block comment (1 to 9) whose text begins
with the marker claims a stamp. The marker in the title, company, revision or
date, later in a comment, or in board text is user text and leaves every verdict
as it is. A claim must be well formed and unique: two claims, a claim whose
comment number repeats, or malformed digests are unknown at the gated checks.
Any comment holding a string that begins with the marker is a claim whatever its
shape, so a missing or invalid comment number, an extra element or an extra list
is a malformed claim and is unknown at admission and at seal verification
(AGENTEE-412), never an unstamped board.
KiCad 10.0.7 keeps board-level properties in the project's text variables:
`pcbnew.SaveBoard` drops a `(property ...)` node from the saved `.kicad_pcb`.
The board file therefore has no dedicated non-user field, so the stamp shares
the title block comments with user text as described above.

Release admission also covers boards whose stamp was stripped outside agent-ee
and boards published before the stamp contract. `fab_package` captures a native
DRC witness on the captured board bytes before extracting release artifacts.
Release requires no open carry and no `silk_*` finding at any severity, for
every board. A stamp and an imported index are both self-asserted until
AGENTEE-348 authenticates them, so neither waives the witness: a fabricated
stamp with a self-hashed imported closure still refuses a natively
silk-conflicted board. Open carry refuses first with its own typed message.
This holds out of campaign after an honest imported closure, too: once native
DRC reproduces the closure and no carry stays open, a residual uncarried
`silk_*` finding, even at warning or exclusion severity, refuses release
(AGENTEE-412). An empty imported index does not establish ancestry. Missing, changed or incomplete native evidence refuses as typed
unknown (`malformed`, or `interrupted` on deadline exhaustion). The package's
sealed witness is rechecked by `verify_gate`, artifact extraction and campaign
completion; a forged passing package cannot bypass it. The stamp therefore is
not an indicator of which boards need release verification: all of them do.
In-campaign releases of boards with uncarried baseline `silk_*` findings are
refused too.

This clean-provenance rule applies only at the release artifact boundary.
Standalone `fab_export`, `cpl_rotation`, `fab_consistency` and other checks keep
their verdicts, including determinate defects and unknown coverage. The check
scheduler has no post-check verdict rewrite. The owned successful-release
controls have their reference text relocated away from pads; their asserted
outcomes are unchanged. Native evidence is produced by the existing networkless
executor; this creates no owner evidence store.

Two limits remain. Stamp digests name the defects' native item UUID sets, not board bytes, so
replayed evidence on an unrelated board is accepted as open carry rather than
refused as foreign. That outcome is conservative: replay cannot close
carry, because closure requires the bundle's board hash to equal the captured
board and native DRC to agree. Side-flip diagnostics have no native DRC finding.
Native recomputation therefore checks only the `silk_*` part of a claimed
closure. A side-flip closure still relies on the bundle board's field positions
and on its artwork record.

`tests.test_placement_defects` exercises actual native helper publication,
adoption, coordinator capture, successor binding and downstream refusal, plus
missing/stale/tampered carry, side-flip closure boundaries and verdict isolation.
The existing BOM-variant fab regression retains its original pass expectation.
Its stamp tests cover native stamping on the DRC-checked bytes, the write-scope
extension/drop boundary, escaped and misplaced marker text, a stamp relocated
around user text in comment 9 (unit and native writer), a full title block, and re-scoped release
with and without lineage. They also cover an unbound stamp and a campaign
successor that drops the stamp. Further tests cover forged and honest imported
closures at the check, seal and artifact boundaries, a missing or changed
witness, and verification that is out of time or ignored by a custom rule.
Release regressions cover stripped and legacy unstamped boards, clean native
provenance, empty imported indexes, sealed-witness tampering and standalone
fab-export verdict preservation, and an honestly closed out-of-campaign carry
whose board keeps a warning-severity `silk_edge_clearance` finding.


## Post-routing artwork producer (AGENTEE-350)

`agent_ee.routed_artwork.step(tool_dir, board)` creates a read-only source
snapshot and an ordinary `implementation_steps` declaration, defaulting to the
verification stage. Declare it alongside the routing step when verification
must repair carried silk. The coordinator executes it through the existing
networkless stage sandbox. It accepts routed boards, including tracks, vias and
zones, while the initial placer retains its unrouted-only contract.

The producer reuses the native field search and can also translate conflicting
silk lines, arcs, rectangles, circles, polygons and graphic text owned by the
board or a footprint. It preserves UUID, ownership, shape, content, style,
visibility, side and angle. Only Reference/Value and declared custom fields
may move. The same finite search and obstacle bounds apply; a graphic that
cannot move remains an open defect. Translating a required polarity or pin-1
mark can affect its meaning relative to its component. This producer establishes
geometric clearance only; a circuit/physical reviewer must assess the marking's
meaning before release.

Every changed saved node must be either a permitted silk field position or a
supported silk graphic translated rigidly. The shared S-expression parser
compares all other tokens exactly, including copper, mask, courtyards, pads,
footprint poses, zones, tracks, vias, setup and unknown attributes. Numeric
coordinates are normalized only within permitted graphic translations. Any
other token change refuses publication. Changed text or fields carrying a
`render_cache` refuse because this producer does not regenerate and compare the
cache. A changed list-headed node,
a property whose name is a nested list, a nameless property of a changed
footprint, an empty `()` child of a changed silk graphic, a nested-list `uuid`
value or a
malformed movable-field declaration refuses with `ValueError`, never an untyped
error or a silent skip (AGENTEE-364).

Escapes are compared exactly (AGENTEE-434). Every unchanged node must keep its
raw saved spelling, compared through the shared parser's `raw=True` mode, so a
decoded-equal respelling (`"x\ny"` against a literal line break, `"F.Fab"`
against bare `F.Fab`, `"0"` against `0`) is a token change and refuses. A moved
field or graphic must also keep the raw spelling of everything except its
translation. A backslash is accepted only inside a quoted string as one of the
escapes KiCad's writer emits: `\"`, `\\`, `\n` and `\r`. The shared parser
decodes exactly these as KiCad's lexer does. Any other backslash refuses in
either board, including unchanged strings: octal (`\1`, `\01`), hex (`\x41`),
`\t`, `\a` and other escapes, a backslash in a bare token, and a backslash the
parser skipped in a comment. Pod boards, whose `PARAM_Datasheet` and
`AGENT_EE_Ratings` properties carry escaped JSON, therefore attest unchanged and
can receive a receipted repair. The ambiguity refusal is pinned by
`test_scope_refuses_backslashes_even_when_parser_erases_them` and
`test_backslash_and_spelling_ambiguity_refuses`, the pod-like native board by
`test_pod_board_with_escaped_json_properties_attests_unchanged`, all in
`tests/test_routed_artwork.py`.

`RealPodArtworkTest` in the same module repeats these checks on a real pod
board. It reads the retained electrical-locality baseline
(`telemetry-pod/expansion/electrical-locality/baseline.kicad_pcb`) from the
fome-mc object store at pinned commit `154cb59e`. The bytes are never copied
into this repository. Before use, the test checks them against that directory's
`SHA256SUMS`. The board's footprints carry escaped `PARAM_Datasheet` and
`AGENT_EE_Ratings` JSON. The test proves four things:

- `write_scope(board, board)` attests unchanged.
- A targeted native repair of the conflicting R73, R175 and U19 `Reference`
  fields moves exactly those three fields and replays from its receipt. Every
  other raw node is unchanged, and the remaining conflicts stay open defects.
- Changing escaped property content, respelling an escape outside the writer
  set, or moving an unselected field refuses.
- A hidden property target refuses.

The test runs on the board alone because the pod project ignores DRC classes
that artwork DRC requires. An absent store is the `fome-mc-store` gap, which
fails under `AGENT_EE_REQUIRE_TOOLCHAIN=1`.

### Targeted repair (AGENTEE-434)

`step(tool_dir, board, targets=[('C128', 'Reference'), ('R73', 'Reference')])`
restricts the repair to the named footprint fields. The targets are receipted as
`--target REFERENCE FIELD` producer arguments in sorted, duplicate-free order.
Each target must name exactly one footprint (by its saved `Reference`) and one
of that footprint's fields that is movable, visible and on a silk layer. An
unknown, duplicated or unmovable target, an empty target set, and a name
starting with `-` refuse. With targets, only conflicting target fields move.
Conflicting fields outside the target and all silk graphics stay where they
are and remain open defects. The scope proof records `targets` and refuses
any other silk move, including a graphic or another field of a targeted
footprint, so everything outside the target is proven unchanged.
`qualify_receipt` and carry replay recover the targets from the receipted argv,
refuse noncanonical or malformed target arguments, and recompute the proof with
them. Untargeted proofs keep their historical shape with no `targets` key.
Targeting changes only which fields may move. It adds no size, angle or
legal-position search policy. Whether a relocation is feasible remains a
separate coordinator decision. Project and custom-rule bytes
are copied unchanged for native DRC. Missing reports, ignored silk coverage and
new non-silk native findings refuse the producer; remaining silk conflicts stay
open. The native DRC comparison has no saved geometry states; the scope proof,
whose digests must equal both DRC'd boards, is its witness that copper is
unchanged, so unmeasured pre-existing collisions keep baseline treatment
(AGENTEE-369). Without that proof such a comparison refuses. Uncarried baseline findings retain their existing check verdicts.

### Exact field-pose request (AGENTEE-434)

`step(tool_dir, board, field_poses=request)` replaces the search with an exact,
candidate-bound request. Its schema is `agent-ee.artwork-field-pose/1`, and it carries:

- `source_sha256`: the source board's digest;
- `fields`: one entry per field, each with
  - `footprint_uuid` and `field_uuid`;
  - `from`: the field's board pose in that source, as `{x_nm, y_nm, angle_deg}`;
  - `to`: the exact board pose to write, in the same form.

Coordinates are integer nanometres. `angle_deg` is a canonical decimal string
in [0, 360). The whole request is receipted as one canonical JSON argument,
`--field-pose JSON`. Fields are sorted by uuid and keys are sorted, with no
whitespace. `qualify_receipt` and carry replay refuse a noncanonical or
malformed request and recompute the proof with it.

An absent request is spelled only by omitting `--field-pose`. An explicitly
supplied JSON `null` is not an absent request: the producer CLI, receipt
recovery and canonicalization all refuse it (`field-pose request is null`)
before any search runs or any proof is granted, so it can never select the
default search and its graphic moves.

The producer works natively:

1. It checks the source digest and each native source pose.
2. It writes each pose with `SetPosition` and `SetTextAngle`, and saves.
3. It reloads the saved board and requires exactly the requested poses.

The independent byte-level qualifier (`write_scope(..., field_poses=request)`)
derives every board pose from the saved tokens. The board position is the
footprint `at` plus the footprint-relative field `at`, rotated by the footprint
orientation. The saved field angle is the board angle. Only quarter-turn
footprints have an exact transform; any other orientation refuses.
`tests.test_routed_artwork` checks this transform against native poses for
every field of the real pod board.

The qualifier requires each of these, and refuses otherwise:

- the before bytes have the named digest;
- every token of the requested field's `at` node and its footprint's `at`
  node, in both boards, is an unquoted plain decimal (`-?digits[.digits]`).
  KiCad refuses to load a quoted pose number such as `(at "2.425" -0.575 0)`,
  and the decoded tree would lose the quotes, so the raw spelling is checked
  first. Quoted, list, exponent, `+`-signed and bare-point tokens refuse
  (`field-pose position token is not a native number`);
- each field is unique, in its named footprint, movable, visible and on a silk layer;
- each source pose equals `from`, and each saved pose equals `to`;
- exactly the requested fields change, and only in their `at` node;
- style, text, visibility, mirroring, layer, uuid, footprint pose, other
  fields, graphics, copper, pads and rules stay byte-exact.

An angle changes only where the request names a different `to` angle. The
translation-only default (no request) and target sets still refuse any rotation.
There is no blanket angle allowance and no graphic rotation. A request that
moves nothing, names a field twice, or is combined with a target set refuses.
Native DRC then applies as for every artwork run: new non-silk findings refuse,
and remaining silk conflicts stay open defects. The proof records the
normalized request as `field_poses`.

The refusal tests cover a wrong board hash, a wrong footprint or field uuid, a
wrong source pose or angle, a wrong destination, an unrequested or unapplied
rotation, an unselected field or graphic, style, text, layer, visibility and
property edits, and footprint and copper edits. They also cover quoted x, y
and angle tokens, list, exponent, `+`-signed and bare-point tokens alongside
an otherwise valid requested edit, a quoted source pose and footprint angle,
and an explicit `null` request: in the receipt argv, and as a freshly executed
receipt whose producer refuses before writing any board.

The first pod use of a field-pose request (the iter8u C128/U31 derivative of
iter8t, 2026-10-22) is **experimental and not adopted**; it is retained only
as diagnostic evidence of the mechanism. It qualified its write scope and
natively closed eight studied silk findings with no new findings, but
agent-509 rejected it for adoption: the C128 label reads as belonging to the
neighbouring R14/R16 passives. Its measured legend-to-mask clearances
(C128 0.1400 mm, U31 0.1438 mm) are also below the sourced JLCPCB minimum
silkscreen-to-solder-mask clearance of 0.15 mm, so the derivative is not
fab-acceptable. The captured fab profile does not yet carry that limit; a
qualified correction needs the sourced limit captured and bound first.

The receipt uses the existing placement evidence envelope and adds
`producer: agent-ee-routed-artwork`, the scope proof and the original routed
board artifact. Carry adoption reads artwork receipts only through the
coordinator's anchored stage-receipt index (`stage_receipts` in campaign state):
an unindexed receipt is ignored, and an unanchored, re-anchored or extended index
or a receipt substituted after indexing refuses (AGENTEE-408). It authenticates the entire tool snapshot against
the campaign package pin, verifies the exact launcher invocation and recomputes
the proof from retained receipt inputs and output. The coordinator captures this
bundle under `placement-evidence`, appending its ordered provenance link.
Closure requires the same native field/graphic identities, retained ownership
and marking style/content, current board and rule bytes, and complete native DRC
without remaining silk findings on those identities. Graphics may differ only
by the authenticated translation. A raw edit or unreceipted DRC cannot close carry.

`tests.test_routed_artwork` exercises front/back fields, footprint/board graphics,
escaped pod-like properties, a real pinned pod board, escape and spelling
ambiguity, targeted relocation and its receipt binding, exact field-pose
requests and their receipt binding,
tracks/vias/zones preservation, non-silk and marking-damage refusals, source-pin
and output binding, missing output, exhausted searches, ignored silk coverage
and a nonroot read-only host sandbox.
`tests.test_placement_defects` runs placement, the pinned native router,
verification/fab refusal, receipted graphic repair under a
[writer-authored policy file](#writer-authored-artwork-policy-file-agentee-551),
closure, passing verification
geometry and native fab export (Gerbers, drill, BOM and CPL), followed by campaign
replay. A passing export does not authorize fabrication.
Its closure-currency fixture uses a closed Edge.Cuts rectangle and a canonical
native field UUID: closure survives a masked-track edit, while the same receipt
chain on a board without an outline keeps the defect open, with or without that
track edit. AGENTEE-344d's closure escape tests use that same outlined fixture
and canonical field UUID, keeping positive controls live alongside escape and
writer-grammar refusals. Native regressions cover footprint-owned cutouts as
well as board-owned cutouts and an in-board positive control.

The producer restores the source `.kicad_dru` bytes but does not authenticate a
writer-editable custom rule file against an earlier placement receipt. A writer
can relax a numeric silk constraint before this step without using severity
`ignore`; this remains a recorded limitation (AGENTEE-350 review G5). The
containment requirement is independent of custom silk constraints.

`tests.test_silk_artwork` also qualifies board-owned and footprint-owned slots
and cutouts with native saved-board identities: relocation permits receipt-bound
closure after repair, while in-cutout, slot-straddling and off-board marks refuse.
Topology probes preserve the default placement parser contract and reject
ambiguous contour relationships and boxes surrounding entire holes (AGENTEE-362).
AGENTEE-368 probes round holes, arc-ended slots, rounded rectangles and a
circular board with tangent, straddling, in-hole, within-tolerance and cleared
boxes, plus native board- and footprint-owned round holes. `tests.test_routed_artwork`
moves both carried labels of a routed board around a straight slot and a round
hole placed over their hole-free destinations, then closes the carry.

### Writer-authored artwork policy file (AGENTEE-551)

Graphics stay anchored unless an `agent-ee.artwork-policy/1` request, bound by
`source_sha256` to the exact board being relocated, classifies them as free
rigid groups (AGENTEE-523). A campaign artwork step receives its board only
when the campaign runs, so a policy fixed in the step's argv cannot name that
board. `step(tool_dir, board, artwork_policy_file=PATH)` instead names a design
file that the implementation writer authors for the board it delivers:

- `PATH` is a normalized candidate path, other than the board. The campaign
  must capture it and list it in `mutable_inputs`, because a writer can edit
  captured mutable files but cannot add files.
- The file holds exactly the request's canonical JSON, as
  `canonical_artwork_policy` writes it: sorted keys, no whitespace, no
  trailing newline. Other bytes refuse (`artwork policy file is not canonical`),
  so the file has one meaning.
- The producer argv is `--artwork-policy-file PATH`. The step declares `PATH`
  as its only stage-step input, so a missing file is a receipt violation. The
  receipt's input tree hashes it like every delivered file.
- The producer reads `/candidate/PATH` and applies it as an argv policy. A
  policy whose `source_sha256` differs from the received board refuses
  (`artwork policy names a different source board`). The producer keeps a
  sealed copy at `placement/placed/baseline/artwork-policy.json`.
- `qualify_receipt` requires the argv path to be the declared input. It
  requires the retained input file and the sealed copy to match the receipt's
  input digest. It then decodes the policy from those bytes and recomputes
  `write_scope` against the receipt's exact source and output. Carry replay
  does the same from the sealed copy in the carried bundle.
- An argv policy, a policy file, targets and field poses are mutually
  exclusive.

The classification must be explicit: member UUIDs the author recorded, never a
geometric inference at relocation time. The mode adds no new move authority.
Membership, owner, side, visibility and common-translation checks are the
argv-policy checks. Receipts without a policy file keep their historical
shape: no step inputs and no sealed policy copy.

`tests.test_artwork_policy` covers canonical decoding, argv and input
exclusivity, the hashed input, tampered input bytes or digests, a dropped
declaration and a policy bound to another board.
`test_writer_authored_artwork_policy_closes_carry_and_exports_fab` in
`tests.test_placement_defects` runs the full campaign. The fixture records the
UUIDs of the board silk it authors. On the verification turn the scripted
writer binds the policy to its board. The receipted step then closes the carry,
and the native fab export follows. Without a policy the same carry stays anchored
and escalates
(`test_unclassified_routed_artwork_stays_anchored_and_the_carry_escalates`).

### Pinned silk text-size growth

`routed_artwork.step(..., text_sizes=request)` and the producer's
`--text-size JSON` select a separate **size-only** operation. This authority
cannot be combined with targets, field poses, policy files or free groups.
There is no relocation search. A later pose operation needs its own request
bound to the resulting saved board. Unlisted fields and graphics stay anchored;
a listed graphic receives dimension authority without becoming a free group.

Create the request through the public pure source helper:

```python
from agent_ee.routed_artwork import text_size_request, step

request = text_size_request(board_bytes, project_bytes, explicit_text_uuids)
spec = step(tool_directory, 'project/board.kicad_pcb', text_sizes=request)
```

The helper reads source bytes and returns authority; it performs no native
execution and changes no board. `agent-ee.artwork-text-size/1` binds the complete
board SHA256 and sibling project SHA256. Every text entry binds its native UUID,
owner footprint UUID (or null for board text), silk layer, exact text, current
width/height/stroke, exact desired width/height/stroke, and a raw-token style/pose
hash. Entries are sorted by UUID and the CLI argument is canonical JSON. An
explicit null, duplicate/missing identity or noncanonical receipt argument
refuses. The request never derives graphics groups or grants blanket authority.

The finite supported recipe is visible square text in the default stroke font,
with an explicit thickness, no custom face/bold/italic/cache, and no reflected
justification. Both deficient square dimensions grow exactly to the captured
project's `board.design_settings.rules.min_text_height`; thickness stays exact.
Already-compliant text, shrinking, oversizing, rectangular text and unsupported
styles refuse. Source text, visibility, justification, angle, position, owner
and all non-size tokens remain exact. The native writer calls only `SetTextSize`
on listed identities; saved-board replay independently checks the result.

When that project setting is absent, this mode supports the pinned KiCad
10.0.7 default of **0.8 mm**. This is an explicit native default, not an authored
or supplier constraint. The actual original ESP project
`9a9ab694fc8a9a456a6791060d998c01a171e30308a170e1b4f97d864b2b6395`
omits the setting; retained native DRC on original board
`875d0bd2526e05d367bed3c1ed819d611c6c63b14b0d9482d0de92ca45511071`
reports that minimum for its 15 references at 0.7 mm and five USB texts at
0.508 mm. Production refuses a different native runtime. No project/rule value
is lowered or written. Native DRC and all other checks still grade the actual
saved result; this operation does not guarantee that growth fits the available
space or resolves every rule, carry, or release obligation.

The ordinary step declares the sibling `.kicad_pro` as an immutable input.
The baseline project is retained with the original board. Receipt qualification
reads those exact input bytes and recomputes `write_scope(..., text_sizes=request,
source_project=project_bytes)` across every board token. Carried receipt replay
also retains and hashes that project. Copper, pads, native footprint poses,
nets, outline and lineage/source-carry tokens are checked independently of the
producer's report; a resealed report cannot authorize a physical change.
Missing/changed project custody or a request not applied exactly refuses.

The CPU regressions use synthetic boards and explicit native setter doubles.
They establish source/receipt behavior only. The original ESP board is not
repaired by those tests; independent native feasibility and release assessment
remain required.

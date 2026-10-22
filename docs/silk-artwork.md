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
placement writer sets title block comment 9 to
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
difference. It refuses as well when comment 9 holds other text. Placement write scope admits only a stamp that adds or extends this
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
is still the stamp. Marker text outside title block comment 9 is user text.
KiCad 10.0.7 keeps board-level properties in the project's text variables:
`pcbnew.SaveBoard` drops a `(property ...)` node from the saved `.kicad_pcb`.
The board file therefore has no dedicated non-user field. The stamp stays in
comment 9, and a placement result with open defects refuses publication when
comment 9 holds other text.

Release admission also covers boards whose stamp was stripped outside agent-ee
and boards published before the stamp contract. `fab_package` captures a native
DRC witness on the captured board bytes before extracting release artifacts.
Release requires no open carry and no `silk_*` finding at any severity, for
every board. A stamp and an imported index are both self-asserted until
AGENTEE-348 authenticates them, so neither waives the witness: a fabricated
stamp with a self-hashed imported closure still refuses a natively
silk-conflicted board. Open carry refuses first with its own typed message.
An empty imported index does not establish ancestry. Missing, changed or incomplete native evidence refuses as typed
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
extension/drop boundary, escaped and misplaced marker text, and re-scoped release
with and without lineage. They also cover an unbound stamp and a campaign
successor that drops the stamp. Further tests cover forged and honest imported
closures at the check, seal and artifact boundaries, a missing or changed
witness, and verification that is out of time or ignored by a custom rule.
Release regressions cover stripped and legacy unstamped boards, clean native
provenance, empty imported indexes, sealed-witness tampering and standalone
fab-export verdict preservation.


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
cache. A backslash anywhere in either board also refuses the scope proof,
including unchanged strings and non-silk content. A changed list-headed node,
a property whose name is a nested list, a nameless property of a changed
footprint, an empty `()` child of a changed silk graphic, a nested-list `uuid`
value or a
malformed movable-field declaration refuses with `ValueError`, never an untyped
error or a silent skip (AGENTEE-364). This conservative scope limit
can refuse legitimate boards: the shared parser decodes escapes, so decoded
token equality cannot establish the saved spelling KiCad renders. The refusal is pinned by `test_scope_refuses_backslashes_even_when_parser_erases_them` in `tests/test_routed_artwork.py`. Project and custom-rule bytes
are copied unchanged for native DRC. Missing reports, ignored silk coverage and
new non-silk native findings refuse the producer; remaining silk conflicts stay
open. The native DRC comparison has no saved geometry states; the scope proof,
whose digests must equal both DRC'd boards, is its witness that copper is
unchanged, so unmeasured pre-existing collisions keep baseline treatment
(AGENTEE-369). Without that proof such a comparison refuses. Uncarried baseline findings retain their existing check verdicts.

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
tracks/vias/zones preservation, non-silk and marking-damage refusals, source-pin
and output binding, missing output, exhausted searches, ignored silk coverage
and a nonroot read-only host sandbox.
`tests.test_placement_defects` runs placement, the pinned native router,
verification/fab refusal, receipted graphic repair, closure, passing verification
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

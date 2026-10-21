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
at least 0.2 mm or the native silk clearance, to remain inside one straight
board contour. Same-side native silk and mask geometry, visible fields, and
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

A board edit or a clean DRC delta alone does not close carry. Closure requires a
later receipted placement result for the current board, complete native DRC with
no remaining silk finding on the same identities, and preserved item ownership,
marking content, layer and style. Missing, hidden or relabeled fields do not
close a defect. Every closed field/graphic must also be visible silk wholly
inside the single proven straight board outline. Both producers measure native
bounding boxes from the final saved board in their networkless tool process,
checking every box edge against the qualified outline. The outline includes
board drawings and footprint-owned Edge.Cuts, as in placement qualification.
Slots, cutouts and multiple contours conservatively establish no containment.
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

`tests.test_placement_defects` exercises actual native helper publication,
adoption, coordinator capture, successor binding and downstream refusal, plus
missing/stale/tampered carry, side-flip closure boundaries and verdict isolation.
The existing BOM-variant fab regression retains its original pass expectation.

The out-of-campaign lineage gap remains open: a design re-scoped without both
`placement_provenance` and `placement-evidence/` cannot be distinguished from an
uncarried design. Exporters must retain the lineage. This implementation does
not add a persistent board marker or an owner evidence store. The post-routing producer below closes the routed-board producer gap.


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
a property whose name is a nested list, a nested-list `uuid` value or a
malformed movable-field declaration refuses with `ValueError`, never an untyped
error (AGENTEE-364). This conservative scope limit
can refuse legitimate boards: the shared parser decodes escapes, so decoded
token equality cannot establish the saved spelling KiCad renders. Project and custom-rule bytes
are copied unchanged for native DRC. Missing reports, ignored silk coverage and
new non-silk native findings refuse the producer; remaining silk conflicts stay
open. Uncarried baseline findings retain their existing check verdicts.

The receipt uses the existing placement evidence envelope and adds
`producer: agent-ee-routed-artwork`, the scope proof and the original routed
board artifact. Carry adoption authenticates the entire tool snapshot against
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

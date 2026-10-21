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
close a defect. An unsupported side-flip diagnostic also requires positive
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
not add a persistent board marker or an owner evidence store. A receipted
post-routing artwork producer and a campaign test that closes the defect after
routing also remain outstanding.

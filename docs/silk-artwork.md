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

Checks in verification/fab stages and `fab_export`, `fab_package`,
`fab_consistency`, `cpl_rotation` and `jlc_dfm` refuse while carried defects remain
open. Each typed `placement_silkscreen_conflict` finding names its native
field/graphic UUID, available description/reference and source receipt. The
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
another field moves and native `added_after` is empty. A new current native
result remains necessary after further board changes; this guard supplies no
waiver or electrical acceptance.

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
`board_sha256` describe the stamped board. It refuses as well when comment 9 holds
other text. Placement write scope admits only a stamp that adds or extends this
comment. Dropping or rewriting a digest, or editing any other title block field,
is a violation. Campaign verification also refuses a successor candidate whose
board drops or rewrites its predecessor's stamp, whichever step wrote it.

A gated check (verification, fab and release stages, and the fab export,
package, consistency, CPL and JLC DFM kinds) on a stamped board refuses as
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
Without stamped, verified ancestry, release requires no open carry and no
`silk_*` finding at any severity. An empty imported index does not establish
ancestry. Missing, changed or incomplete native evidence refuses as typed
unknown (`malformed`, or `interrupted` on deadline exhaustion). The package's
sealed witness is rechecked by `verify_gate`, artifact extraction and campaign
completion; a forged passing package cannot bypass it. The stamp therefore is
not the sole indicator of a board that needs release verification.

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

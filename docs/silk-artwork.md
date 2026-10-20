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
the baseline using the existing placement DRC comparison. Only a proposal with
no added `silk_overlap` or `silk_over_copper` may publish. Baseline findings
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
carry the field UUID, reference and reason. They refuse publication even if a
second field can move clear of the flipped field. An unresolved graphic conflict is reported this way
and prevents publication. It requires a different footprint placement or human
resolution. Search exhaustion reports the bounded search limitation and does not
prove physical infeasibility. Failed candidates and these diagnostics remain in
the writer's failure directory. Fresh saved-board probes and write-scope proofs
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
10 and 2 respectively. It refuses publication with 12 typed conflicts and
retains one placement-added `silk_edge_clearance` finding. The pth-fixed pod
reduces 38 copper and 25 overlap findings to 9 and 7, refusing publication
with 16 typed conflicts. These bounded searches can leave resolvable conflicts. Placement-added `silk_edge_clearance`
remains reported in the DRC comparison and retains its existing grading. The
artwork pass refuses any additional edge-clearance regression it introduces.

# Native functional-group views

`agent_ee.group_views.produce(board_path, groups_document, out, *, input_board_path=None)`
writes `top.svg`, `bottom.svg` and `manifest.json` in a new owned directory.
It supplements the final-coordinate group measurements and independent judgment
in the [group-intent contract](planning/2026-10-25-recovery/contracts/AGENTEE-479-group-intent-contract.md).
The caller supplies its validated groups document and the **saved final native
board**, rather than proposed placement coordinates. No placement or routing
operation runs.

```python
from agent_ee.group_views import produce

views = produce(final_board, validated_groups, output_directory,
                input_board_path=original_input_board)
top = output_directory / views['views']['top']['path']
```

Here the four path/document variables belong to the caller. `out` must not
already exist. Exceptions propagate: a caller must retain a render refusal
explicitly instead of treating loose files in an incomplete output as views.
The helper does not modify either source board or the groups document.

The final board's exact hash is `board_sha256`. The optional original input is
hashed independently as `input_board_sha256`; an omitted original stays null.
These fields have different roles even if both files happen to be identical.
`snapshots` identifies exact copies of the final board, optional adjacent
`.kicad_pro`, groups and optional original board. `project_sha256` binds the
adjacent project consumed from that snapshot. Source and snapshot bytes are
checked again after native plotting.

`groups_sha256` hashes the frozen `source/groups.json` bytes written from the
supplied document; it is **not** a claim about the caller's original JSON
whitespace. `canonical_groups_sha256` is the repository's `identity(document)`.
The caller retains its original raw declaration identity separately.

Rendering reuses the unchanged `candidate_visuals._plot` native
`kicad-cli pcb export svg` path. Layers are the existing inspection set: copper,
silk, courtyard and Edge.Cuts on each face. Raw SVGs, command records and console
logs remain beside the annotations, with their hashes. Native PCB geometry is
not reconstructed from footprint centres. The runtime record binds the native
CLI and pcbnew extension, Python, exporter source and annotation helper source.

This frame is qualified on the supported CI KiCad 10.0.7 runtime for one
axis-aligned rectangular outline made of four Edge.Cuts lines. Other contours,
footprint-owned Edge.Cuts and inconsistent SVG viewports refuse. The native
fit-board SVG page size is quantized to mils; annotation preserves its native
children without clipping the last outline edge to that rounded page height.
The annotated drawing window is the outline plus 2 mm; the full native SVG
remains available separately. This is a bounded applicability limit, not a
replacement general renderer.

Both views use coordinates read from the exact saved board. Top is native X
right/Y down. Bottom mirrors X about the same outline frame for **both** native
plot geometry and member positions; annotation text stays readable. The manifest
records crop origin, size, mirror, display scale and offset. A top-mounted part
projected onto bottom remains labelled `[F]` and `opposite face`, with dashed
markers. Double rings identify anchors. The manifest's `members` table records
full native UUID, reference, group, anchor role, coordinates, native rotation
and actual F/B side. The SVG legend shows millimetres to three decimal places;
ungrouped items show a UUID suffix, with the full UUID retained in the manifest.
Rotation and side are observations, not an orientation assessment.

Unknown or ambiguous declared references, duplicate memberships or group names,
and anchors absent from their own membership refuse before output creation.
Native items outside the declared groups remain in `ungrouped`/`unresolved`
with their actual UUID, reference, position and side; they may require fixed or
graphic dispositions from the caller's structural assessment. A member outside
the drawing window is labelled `OUTSIDE VIEW` and retained as unresolved.
Neither an extra item nor a picture establishes a structural pass or defect.

Every successful result has `acceptance_assessed: false`. Images cannot
establish group coherence, sourced locality, electrical orientation,
manufacturing readiness, supplier confirmation or M1 qualification. Native SVG
timestamps are retained, so image byte determinism is not claimed.

Focused native verification:

```sh
ae-ci-exec . -- /usr/bin/python3 -m unittest -v tests.test_group_views
```

The tests compare actual plotted pad geometry against asymmetric front/back
poses, repeat after translating the saved board (including negative Y), check
the mirror on native geometry and markers, and exercise membership, crop,
unresolved visibility and independent input/final hash controls.

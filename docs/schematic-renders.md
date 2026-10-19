# Schematic visual evidence

`schematic_render` (AGENTEE-71) is an optional informational check. It renders
captured schematic sheet instances for independent reviewers and compares their
pixels against an explicitly captured visual baseline. It produces no verdict,
acceptance predicate, defect finding or automatic repair instruction.

```json
{"id": "visual", "kind": "schematic_render", "params": {}}
```

The scope's `schematic` selects the captured root. The check reuses the shared
schematic hierarchy reader and S-expression parser. Each sheet's explicit page
number selects its instance in the native export; filenames and comparison keys
use the occurrence UUID path. Reused child files therefore retain separate
instance images. Missing or duplicate page numbers, unsupported page selectors,
missing children and hierarchy cycles are missing visual coverage.

## Renderer and coordinates

The existing pinned toolchain already supplies KiCad 10.0.7 and Poppler 24.02.0
(`poppler-utils` in the Dockerfile). Both run inside the existing networkless
executor sandbox. KiCad exports each selected page to PDF, without the drawing
sheet, PDF metadata, popups or hierarchical links. `pdftoppm` produces RGB pixels
at 127 DPI, with antialiasing disabled. No new Python or system dependency is
required. The check refuses other tool versions.

The owned overlay adds a 20 mm grid and numeric tick labels. Labels, including
the `x y mm` legend, use owned bitmap glyphs rather than installed fonts. PNG
encoding uses Python's standard library, fixed RGB scanlines and compression,
with no timestamps or other variable metadata. Retained PPM files contain the
same gridded pixels as their PNGs. The renderer identity binds tool versions,
binary SHA-256 hashes, export/raster options, the implementation hash, Python
and zlib versions, grid spacing and pixel scale.

Image pixel `(0, 0)` maps to schematic `(0 mm, 0 mm)`. X increases rightward,
Y downward. Divide either pixel coordinate by **5** to obtain schematic mm;
there is no crop or rotation. For example the tick at `(100, 100)` pixels is
`(20, 20)` mm. Integer pixel rounding contributes at most 0.2 mm, below one
grid unit. Blue grid lines and labels are visual aids; they can obscure small
features along those lines. Consult the captured native source for repairs.
The native PDF remains available without the overlay. Paper sizes resulting in
more than 16 million pixels are refused.

## Retained evidence and baseline

Every declared output, native log and executor receipt belongs to the existing
check evidence inventory. `work/visual/render/manifest.json` records candidate
identity, source hashes, renderer identity, coordinates and per-instance PNG/PPM
hashes. `work/visual/drift.json` records the comparison. The reviewer packet's
check details include both records; its artifact inventory lists the images,
which are available through the mounted evidence path. Images are not embedded
as text. Baseline-bound runs seal these files through the existing evidence
helpers, with the same independence and adjudication boundaries as other checks.

To compare a repair, copy the previous check's complete `render/` output into
an explicit scope input, for example at `visual-baseline`, then declare:

```json
{"id": "visual", "kind": "schematic_render", "params": {"baseline": "visual-baseline"}}
```

The baseline directory, including its manifest and pixels, must be captured.
All captured files under that directory become immutable verification inputs in
scope/2 and scope/3. Baseline creation and selection are explicit; rendering does
not overwrite an earlier candidate or baseline. This directory is ordinary
run evidence, not a new owner evidence store. Map the check to an informational
requirement in scope/3: it cannot satisfy an acceptance requirement.

A successful comparison has status `compared` with per-sheet `changed_pixels`,
`drift_percent` and `changed_bbox_px`. Bounding boxes are half-open
`[left, top, right, bottom]`; unchanged images have a null box. Drift measures
changed RGB pixels including the grid, without a pass/fail threshold. Source
hashes on each side identify the compared sheet bytes; candidate identities
bind the complete captured dependency set.

No baseline yields `unknown` / `no_baseline`. A different renderer yields
`unknown` / `baseline_stale_renderer`. Changed instance inventories, dimensions,
missing files, altered hashes, or disagreement between retained PNG and PPM
pixels also yield unknown. A zero drift measurement is not an engineering pass.

## Failure and review boundary

Render errors retain diagnostics and mark visual evidence missing. The check's
outcome is unknown; a successful render is information only. Other checks and
independent review can continue. Optional KiCad/Poppler rendering tools are
qualified by this check, rather than being additional reviewer preflight gates;
its Python wrapper uses the existing preflight runtime contract.

Reviewers must report omitted visual coverage. Their coordinate-based findings
remain concerns until reproduced and centrally adjudicated under the existing
rules. They use the existing structured review schema and validator. Malformed
critique is an unusable review, including when rendering has failed; empty or
malformed ad-hoc overlap lists cannot become a passing review.

## Qualification and provenance

`tests.test_schematic_render` runs owned hierarchical fixture generation through
real sandboxed native exports and rasterisation. It checks identical PNG bytes,
coordinate round trips and changed-pixel bounds, a child-only edit, captured
baseline immutability, sealed packet artifacts, renderer mismatch and corrupt
artifacts, missing visual coverage, and the existing malformed-review boundary.
Run the feature tests on the qualified native host:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests \
  python3 -m unittest tests.test_schematic_render -v
```

The mechanism references are SchGen (`modules/utils/kicad_sch_export.py`) and
Konnect (`crates/konnect-core/src/tools/sch_export.rs`), as named in AGENTEE-71. They
informed only the ticket's concept: gridded schematic images and renderer-bound
visual comparison. No reference-project source, parser, rasteriser, design,
library, documentation text or critique schema was copied or adapted. The
implementation uses PDF and the already pinned Poppler tool instead of adding
an SVG rasteriser. See the [origin inventory](file-origins.json).

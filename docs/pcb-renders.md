# Native 3D PCB renders

`agent-ee render3d` produces top, bottom and isometric PNGs using KiCad's native
ray tracer. It uses the same views and settings as the FOME-MC board images:
orthographic projection, high quality, an opaque background, floor shadows and
native board materials. It makes no model calls and needs no browser.

```sh
./bin/agent-ee render3d fixtures/rc/control/rc_filter.kicad_pcb \
  --out build/rc-renders
```

The source may also be a retained review directory. In that case the command
verifies and renders its captured board, rather than the current source checkout.
The output directory must be new and outside the retained review.

The command requires `kicad-cli pcb render` with the options above; development
uses KiCad 10.0.7. Python uses only the standard library. Rendering copies the
board and adjacent project file into isolated snapshots. It preserves the saved
copper fill and does not save the source board or GUI preferences.

## Images and run records

For `rc_filter.kicad_pcb`, the output includes:

- `rc_filter-3d-top.png`, `rc_filter-3d-bottom.png` and
  `rc_filter-3d-isometric.png`.
- `render-manifest.json`, with source hashes, native KiCad version, camera and
  quality settings, model coverage, and each valid image's dimensions and hash.
- Snapshots and per-view native commands, stdout and stderr logs.

The default requests 4096 × 3072 pixels and renders up to three views concurrently.
KiCad 10.0.7 produces 4064 × 3056 pixels for that request; the manifest records the
actual PNG dimensions. High-quality rendering of a populated board can take
several minutes. Lower the size or quality for a quick preview:

```sh
./bin/agent-ee render3d fixtures/rc/control/rc_filter.kicad_pcb \
  --out build/rc-renders-preview --width 800 --height 600 --quality basic --jobs 1
```

`--timeout` limits each view to 1200 seconds by default. A failed or timed-out
render returns exit code 2 and retains diagnostics in the new output directory.
Ctrl+C stops the workers, records an interrupted run and returns 130. Only a
manifest with `status: completed` represents a complete three-view bundle.
Existing outputs are refused; choose another directory when retrying.

## Component models

Without `--models`, component references use inherited environment variables and
KiCad's native defaults. Each view uses a fresh KiCad configuration, so paths
configured only in GUI preferences are not loaded.
To use a separate stock-model cache, supply its root directory:

```sh
./bin/agent-ee render3d path/to/board.kicad_pcb \
  --out build/board-renders --models "$HOME/.cache/agent-ee/models"
```

For a new cache, explicitly request downloads:

```sh
./bin/agent-ee render3d path/to/board.kicad_pcb \
  --out build/board-renders-fetched --models "$HOME/.cache/agent-ee/models" \
  --fetch-models
```

Fetching requests only referenced stock-model paths from pinned official KiCad
sources. It does not upload the PCB. The cache retains download provenance and
file hashes. Local project models continue to resolve from the source project.
Ordinary renders make no model downloads. A captured copy of such a cache can
also resolve stock model paths in the
[fab-package consistency gate](fab-consistency.md#stock-model-libraries).

Missing model files and footprints without model references are reported in the
manifest. They do not prevent KiCad from rendering the remaining board. The tool
does not create replacement component bodies. Review coverage before using an
image to communicate assembly or connector details.

For interactive inspection, use [the browser viewer](pcb-viewer.md). These PNGs
are visual artifacts; rendering does not run or approve ERC, DRC or electrical
review.

## Candidate inspection images (AGENTEE-382)

Every successful `placer_runner.place_candidate` proposal now includes `visuals/`
beside `placement-result.json`. The coordinator review lifecycle independently
renders the admitted, captured board into `work/candidate.visuals/`. The placement result
and candidate manifest retain a `visuals` record with source-board SHA-256,
KiCad version and executable SHA-256, image paths and SHA-256 hashes, model
coverage, elapsed time and an explicit reproducibility limitation. Paths are
relative to the proposal directory or captured run, respectively. Generic
`candidate.capture` remains a pure input capture; the coordinator attaches images
through `candidate_visuals.capture_visuals` after validating the scope and inputs.

The networkless executor produces high-quality, opaque 1600 × 900 top and bottom
PNGs, front and back SVG composites (copper, silk, courtyard and board outline),
and one SVG for every enabled inner copper layer plus the outline. A four-layer
board therefore has six images. Two PNG renders run concurrently with separate
KiCad configuration directories. The 180-second executor guard bounds imaging
work; it is a resource guard, not an engineering acceptance limit. Coordinator
imaging elapsed time is excluded from `max_seconds`, leaving the same budget
for checks and reviewers whether imaging is enabled or skipped. Small-board
performance does not qualify pod-scale performance.

`reviewers.build_packet` includes `candidate_visuals` outside the text excerpt
budget, verifies each image hash and board binding, and exposes the images
through `/evidence/work/candidate.visuals/`. The reserved directory name contains
a dot, which declared check IDs cannot contain, so check tools cannot overwrite
coordinator images through their writable work directory. Both initial reviewers receive the
same coordinator-produced images. A modified, missing or linked image refuses
packet assembly. Images remain inspection aids; native geometry, DRC and sealed
coordinator findings retain their authority. Missing models are reported rather
than replaced with invented component bodies.

For fixture runs, set `skip_visuals: true` in a placement request or review
scope, pass `--skip-visuals` to `agent_ee.placer_runner`, or explicitly set
`AGENT_EE_SKIP_VISUALS=1` for a test process. The environment flag accepts only
`0` or `1`; JSON flags accept only booleans. Skips retain `status: skipped` and
a reason. Native failures, timeouts and missing manifests retain
`status: unavailable` with executor diagnostics; they never become visual
success and never change a placement/check verdict. Old retained captures report
that images are unavailable rather than implicitly generating new evidence.

The shared test harness defaults `AGENT_EE_SKIP_VISUALS` to `1` when the
variable is unset. This covers saved placement proposals and coordinator captures
made in the test process, and the resulting reviewer packets. The production executor clears
inherited environment variables: placement helpers launched inside that sandbox
still need an explicit request or CLI skip flag. The `test_candidate_visuals`,
`test_render3d`, `test_schematic_render`, `test_datasheet_footprint`,
`test_schematic_text` and `test_purpose_review` modules force `0` during execution
and restore the previous environment afterward. Their existing visual evidence
assertions still run, including native images and hashes. Explicit `0` opts a
test process into imaging. Invalid flag values remain invalid. Production
processes do not import the test harness and continue to render by default.
Schematic rendering and other checks that create their own visual evidence do
not consult this candidate-imaging flag.

Native image bytes are hashed, but byte determinism is not certified. The result
records this as `reproducibility.status: not_verified` with a reason: native ray
tracing has no bound seed in this invocation. The AGENTEE-382 reviewer repeat on
KiCad 10.0.7 measured PNG ray-trace noise in about 0.67% of raw pixel bytes
and changing SVG `<title>` creation timestamps. These are prior measured causes,
not a repeat comparison of each capture. Native SVG metadata is retained without
normalization; PNG determinism is not claimed. Rendering
is offline; project-local models can be used, but no stock models are downloaded.
An image with unresolved model coverage is incomplete visual coverage.

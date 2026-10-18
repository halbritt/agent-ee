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

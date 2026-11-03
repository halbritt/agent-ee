# 3D PCB viewer

`agent-ee view3d` exports a saved KiCad board and opens an interactive browser
viewer. Use it to inspect board shape, copper, component placement and the
available component models alongside the review workflow. It makes no model
calls.

For high-quality top, bottom and isometric still images, use
[`agent-ee render3d`](pcb-renders.md).

From the checkout:

```sh
./bin/agent-ee view3d fixtures/rc/control/rc_filter.kicad_pcb \
  --out build/rc-viewer
```

The command requires a new output directory. It exports the board, starts a
server on `127.0.0.1`, prints its URL and opens the default browser. Keep the
command running while using the viewer; press Ctrl+C to stop the server. Use
`--no-browser` to open the printed URL yourself, or `--port 8765` to choose a
port. The default `--port 0` asks the operating system for an available port.

To build the viewer without starting a server:

```sh
./bin/agent-ee view3d fixtures/rc/control/rc_filter.kicad_pcb \
  --out build/rc-viewer-export --export-only
```

Serve the generated directory over HTTP when viewing it; browser restrictions
on `file://` can prevent JavaScript modules and model loading. The viewer bundles
its JavaScript dependencies and needs no CDN connection.

For example, serve an exported bundle with Python, then open
`http://127.0.0.1:8765/`:

```sh
python3 -m http.server 8765 --bind 127.0.0.1 --directory build/rc-viewer-export
```

## Inspect the board

Drag to orbit, right-drag to pan and scroll to zoom. **Isometric**, **Top** and
**Bottom** set the camera; **Fit** frames the whole model. Press F while the
viewport has focus to fit the board. **PNG** downloads the current view.

Click geometry or an object name to see its native export name and material.
Use the object checkboxes to hide geometry, the filter to find names, and
**Show all** to restore visibility. The reference grid, selection highlight and
lighting controls change the display. Object names and the available geometry
come from KiCad's export; they do not supply net or requirement navigation.

## Captured candidates

The source can also be a retained review directory:

```sh
./bin/agent-ee view3d build/first-checks --out build/captured-viewer
```

This selects the board declared in the retained candidate and verifies the
captured input bytes. It does not select the current board in the source
checkout. The output must be outside the retained run. A changed or incomplete
captured snapshot is refused. Viewing a snapshot does not verify the review
result, tool evidence or current source
freshness; use [the ordinary verification commands](cli-reference.md#inspect-and-verify)
for those claims.

## Geometry and prerequisites

Export requires KiCad's `kicad-cli pcb export glb` command with its copper,
silkscreen and solder-mask export options. The development environment uses
KiCad 10.0.7. Viewing requires a browser with WebGL support. The Python command
uses the standard library and the included Three.js browser assets.

KiCad exports the saved board geometry and resolves footprint model references
using the installed KiCad environment. Component bodies depend on those models
being available. A board with missing models can still export successfully;
inspect the export log when expected components are absent. Install the matching
KiCad model libraries or repair the footprint model paths, then export to a new
directory. Export does not synthesize replacement component bodies or fill
unsaved copper zones.

Each bundle retains `board.glb`, `viewer.json`, `export.json`,
`export.stdout.log` and `export.stderr.log` alongside the browser assets.
The metadata identifies the source board hash and, for captured inputs, its
candidate ID. Model libraries are resolved locally at export time; the source
board hash does not identify every component model used in the preview.

The preview is a visual aid. Component models, colors and meshes can omit details
needed for mechanical clearance or manufacturing decisions. It does not run
ERC, DRC, simulation or independent review, and a successful export does not
approve the design. The native KiCad board remains the design authority.

## Remote use

The server listens only on loopback. To view it from a workstation while the
command runs on another host, choose a port and forward it through SSH:

```sh
# On the host with the checkout:
./bin/agent-ee view3d fixtures/rc/control/rc_filter.kicad_pcb \
  --out build/remote-viewer --port 8765 --no-browser

# On your workstation, in another terminal:
ssh -L 8765:127.0.0.1:8765 your-host
```

Open `http://127.0.0.1:8765/` on the workstation. The command does not upload
the board or publish the viewer. Exported geometry can still disclose the design;
choose who receives the output directory.

## Refusals and export failures

An existing output directory is refused. Choose a new path instead of deleting
a retained review or viewer export. Missing native export support, a rejected
board or a timed-out export produces a command error; inspect the diagnostic and
any retained export logs before trying again. Browser or graphics failures can
prevent display even after a successful native export.

## Three.js version

The browser assets include three.js files and the upstream `LICENSE` under
`src/agent_ee/viewer_assets/vendor/`. Its `manifest.json` records the release
(`version`, `upstream`) and, per file, the source URL, `upstream_sha256`,
vendored `sha256` and transforms. To move the files to the newest upstream
release:

```sh
python3 scripts/refresh_viewer_vendor.py --check   # fetch and verify; write nothing
python3 scripts/refresh_viewer_vendor.py           # write the files and the manifest
```

The script takes the highest `rNNN` tag (or `--release rNNN`), fetches every
file from the manifest's source URLs at that tag and applies the recorded
transforms. It refuses (exit 1) and writes nothing in these cases:

- a source path does not exist at the release;
- the files are not those the manifest records;
- a vendored module would import a file that is not vendored, or a name that
  `three.module.js` does not export;
- a `THREE` name, loader or controls member, controls event or constructor
  parameter list that `viewer.js` uses differs from the current files.

After a refresh, run the viewer tests and commit the files and manifest
together.

On 2026-11-03 the refresh refused the newest release, r187. From r186 the
release tags do not contain `build/three.module.min.js` or
`build/three.core.min.js`, and in r185 and r187 `GLTFLoader.js` imports
`../utils/SkeletonUtils.js`. The files stay at r180 until the manifest names
the new sources and the added module.

## Development checks

The viewer tests exercise source preservation, captured candidate selection,
native export failures, HTTP boundaries and the packaged module dependencies:

```sh
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_viewer*.py' -v
```

With UIPass and its browser installed, the local fixture exports the included RC
board to a temporary directory. It checks desktop and phone layouts, bottom view,
object visibility, and missing metadata/model states:

```sh
uipass run --manifest "$PWD/.uipass.yaml" --output /tmp/agent-ee-viewer-ui-check
```

Use a fresh report directory for each run. Component bodies appear in these
checks when the matching KiCad 3D model library is installed; the fixture does
not download models.

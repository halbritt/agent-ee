# Persistent private PCB gallery

The gallery retains native three-view render bundles outside Git and `/tmp`,
reuses the existing interactive `view3d` UI, and serves a stable private URL.
Code and the systemd unit live in this repository. The HTTP service is read-only;
publication is a local command, with no upload endpoint or directory browsing.

## Install and operate

From a verified checkout, run `scripts/install_render_gallery.sh TAILSCALE_IPV4`.
It installs a release copy under `~/.local/share/agent-ee-render-gallery`, enables
`agent-ee-render-gallery.service` in the user manager, and listens only on that
Tailscale address at port 18510. The URL is `http://TAILSCALE_IPV4:18510/`.
Tailnet access controls determine who can read the gallery. Do not expose this
port publicly. User-manager startup across logout/reboot requires user lingering;
check `loginctl show-user "$USER" -p Linger` on the host.

Use `systemctl --user status|restart|stop agent-ee-render-gallery.service` and
`journalctl --user -u agent-ee-render-gallery.service` to operate it. Re-running
the installer upgrades code while retaining artifacts. Old app-release directories
are retained for rollback; select the previous `app` symlink and restart to revert.

## Publish a milestone

Generate native artifacts with the existing commands, using a new durable output
directory for each candidate:

```sh
agent-ee render3d /path/candidate.kicad_pcb --out /durable/milestone/renders
agent-ee view3d /path/candidate.kicad_pcb --out /durable/milestone/viewer --export-only
PYTHONPATH="$HOME/.local/share/agent-ee-render-gallery/app" python3 -m agent_ee.render_gallery \
  --root "$HOME/.local/share/agent-ee-render-gallery/artifacts" publish \
  --renders /durable/milestone/renders --viewer /durable/milestone/viewer \
  --label 'Telemetry pod iteration 8' --stage 'Unrouted diagnostic placement'
```

`--viewer` is optional. Three completed native still views are required. Import
checks the render manifest against its own records (no recorded errors, source
unchanged, each image named for its view, matching the per-view record, hash,
valid PNG and recorded dimensions, three distinct images, snapshot board present
as a contained regular file and hashed). Each image must also match the receipt
its own native process wrote (`top.json`, `bottom.json`, `isometric.json`): a
completed status, the same file, hash and dimensions, and a `kicad-cli pcb render`
command with that view's side and rotation, the requested size, that output
file and the staged snapshot board. Swapping images between views and updating
only the manifest is refused. Then the viewer's board/model/candidate identity
is checked.
It copies only declared images and bundled viewer assets, then atomically
publishes the new index.

For a render taken from a retained run directory add `--candidate RUN_DIR`.
Publication then verifies the retained snapshot and requires the manifest's
`candidate_id`, `captured_board` and board hash to match it; a manifest that
names a candidate is refused without `--candidate`, and an unbound manifest is
refused with it. Publication also re-stages the verified retained board exactly
as `render3d` does (only relative model filenames become absolute) and requires
the render snapshot board, and its project file if any, to be those bytes, so an
unrelated or edited snapshot is refused even when the manifest's hashes were
updated to match. Moving the retained run directory after rendering changes the
rewritten model paths and is therefore refused too; render again from the new
location. A viewer must carry the same candidate identity.

Limitations: these checks catch an edited or mismatched manifest but cannot
prove the producer ran KiCad honestly. A producer that rewrites the per-view
receipts together with the manifest, or forges pixels, is not detected; the
receipts live in the same output directory. Without `--candidate` (a render of a
loose `.kicad_pcb`), there is no retained board to re-stage, so the snapshot is
only checked against the manifest's own hashes.

Absolute host paths in model coverage, warnings and `viewer.json` (every string
and key) are replaced by `<redacted>/basename` at publication: POSIX paths,
Windows drive paths with either slash, UNC paths and `file://` URLs, including
directory names with spaces (a path runs to the end of the text or a quote).
Other URLs and KiCad variables such as `${KIPRJMOD}/m.step` are kept. The
server applies the same redaction to `manifest.json`, `viewer.json` and page
warnings of older bundles on the way out.

Each card shows model coverage outside any collapsed element: a badge ("Incomplete
models" when any model is missing, unresolved or disabled or a footprint has no
model; "Model coverage unknown" when any count is absent, negative, not an
integer, or the coverage record is malformed) and the counts of resolved,
missing, unresolved, disabled and model-less items, with "unknown" for an
invalid count. Only the individual
warnings are collapsed.
Failed imports leave the previous latest item visible. Concurrent publishers are
serialized with a file lock. Each publication gets an immutable URL; history is
retained until an operator explicitly archives it. Artifacts may consume substantial
disk space; there is no automatic deletion. Publication manifests retain candidate
hash, render time, declared stage, model coverage and warnings. They are evidence
of import identity, not independent verification of a producer's claims.

Do not label diagnostic or unrouted output as a release. Missing models remain
visible limitations. Rendering does not substitute for native DRC or engineering
review. This first gallery publishes 3D images and the interactive viewer; layer
plots remain in their native review packets.

## Annotated diagnostic supplements

Use `supplement` for an annotated 2D PNG, rather than manufacturing a native
three-view render manifest. Supplements have a separate listing and never change
`Latest` or any existing render bundle. Keep historical/unrouted status and
unadopted proposals explicit in the label and stage.

```sh
PYTHONPATH="$HOME/.local/share/agent-ee-render-gallery/app" python3 -m agent_ee.render_gallery \
  --root "$HOME/.local/share/agent-ee-render-gallery/artifacts" supplement \
  --image /durable/analysis/annotated.png --packet /durable/analysis/INDEX.md \
  --board /durable/candidate.kicad_pcb --board-sha256 ACTUAL_BOARD_SHA256 \
  --label 'Historical bottom-view free-space analysis' \
  --stage 'Unrouted diagnostic; proposed ADC move NOT adopted'
```

The command verifies the supplied board digest, validates the PNG, and binds the
copied image and source packet hashes in a publication manifest. This establishes
byte identity, not correctness of the analysis or adoption of a proposal. Only
the PNG and manifest are served; the source board and packet stay private. SVG
is not accepted. Open `/diagnostics/RETURNED_ID/index.html` for a permanent page
or `/diagnostics/RETURNED_ID/image.png` for the exact full-size image. The main
gallery links its separate diagnostic section. Publications are serialized by
the same lock as render imports, and the diagnostic listing is replaced atomically.

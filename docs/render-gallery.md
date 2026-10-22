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
  --label 'Iteration 8' --stage placement
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

### What is published: an allowlist, not redaction

Every publication surface (the stored `manifest.json` and listing entry, the
published `viewer/viewer.json`, both JSON endpoints and the HTML pages) carries
only allowlisted fields, rebuilt from structured parts at publication and again
when served. Free text is never scrubbed; anything that cannot be rebuilt is
replaced whole by the fixed token `<redacted>`, and every unlisted field is
dropped.

- **Paths.** A path-bearing field (a model path in a warning, `board.path`,
  `board.name` and `captured_board` of the viewer and supplement) is published
  as the final component of an unambiguous local path or as `<redacted>`. A
  value an earlier redactor already scrubbed (`<redacted>/name`) is
  `<redacted>` whole: what followed its marker is not known to have been a
  local file name. Local
  means a POSIX path without a backslash, a drive path with either slash
  (`C:\Users\A\m.step`, `C:/x/m.step`), or either after a KiCad
  `${VARIABLE}`; the component must be a plain file name (letters, digits,
  `_ . + -`, not starting with a dot). Any URI or other `name:` (`file:` in any
  case, with or without an authority), a UNC or `//` host, a doubled separator,
  a backslash outside a drive path, a quote, `< > | * ?`, a control character
  or a trailing separator gives `<redacted>`. So `/home/a/m.step` gives
  `m.step`, and `\\host\share\m.step`, `file:///home/a/m.step` and
  `\\host "user name" \share\m.step` give `<redacted>`.
- **Source file names** of a supplement (`source_packet.name`,
  `source_image.name`) are published only when they already are a plain file
  name; a name that looks like a path is `<redacted>`.
- **Warnings** are rebuilt from a recognised code with allowlisted parameters:
  `REF: no 3D model is declared`, `REF: missing|unresolved 3D model NAME` (the
  path becomes a basename as above), `VIEW: KiCad produced WxH for requested
  WxH`, and the fixed model/viewer notices. A native log line keeps only its
  view (`top: <redacted>`); any other warning is `<redacted>`.
- **Labels and stages** carry no publisher free text. A stage is one code:
  `placement`, `routing`, `routed`, `review`, `diagnostic` or `fixture`
  (shown as Placement, Routing in progress, Routed, Design review, Diagnostic,
  Test fixture; all are the publisher's statement). A label is `KIND NUMBER`,
  `KIND` one of `Iteration`, `Revision`, `Candidate`, `Milestone`,
  `Diagnostic`, `Fixture` and `NUMBER` a decimal from 0 to 999999, rebuilt
  from those two parts. Publication refuses anything else; an earlier record's
  label or stage outside this vocabulary (such as `Telemetry pod iteration 8`)
  is served as `<redacted>` whole. Describe the change in the review packet,
  which is not published.
- **Identities** (board hash, candidate id, model hash, image hashes), times,
  counts, views and statuses must match their grammar or vocabulary, else they
  are published as `null` (shown as unknown).

Published shapes are versioned. A render bundle record is
`agent-ee.gallery-bundle/3`: `label`, `stage`, `board_sha256`, `candidate_id`,
`published_at`, `rendered_at`, `model_coverage` (only `summary` with the counts
`footprints`, `models`, `footprints_fully_resolved`, `resolved`, `missing`,
`unresolved`, `disabled` and `no_model`; the per-footprint inventory stays in
the local render manifest), `warnings`, `images` and `viewer`. The published
`viewer.json` keeps `"schema": "agent-ee.viewer/1"`, which the viewer page
checks, and adds `"publication": "agent-ee.gallery-viewer/2"`; it holds only
`board` (`name`, `path`, `sha256`), `candidate_id`, `captured_board`, `model`,
`warnings`, `coordinates` and `model_sha256` (the export command and logs are
dropped). Supplements are `agent-ee.gallery-diagnostic/4` (below). Records
published earlier (bundle records without a schema or of schema `/2`,
supplements of schema `/2` or `/3`) stay byte-for-byte as stored and are projected into the current
shape when served; projection is idempotent.

Tests keep the composed generator of every review round (433c-433k) as the
regression oracle: file URIs in every casing with and without an authority
(empty and root paths, bracketed IP literals, user info), UNC hosts and paths,
drives with either slash, POSIX paths and hosts interrupted by quotes, groups and
whitespace, wrapped, punctuated and joined as first, second and later operands,
together with each reported example and the partly scrubbed text earlier
redactors published. Every text is put into every field and key of a bundle,
viewer and supplement record, and the stored and served JSON and both pages
must carry no host, user or share text. The oracle is shown to fail on
deliberately leaky publication: pass-through, a regex scrubber, an unlisted
field, a basename without the local-path checks, trusting text that starts with
the marker, a label check that refuses only `/`, a label kept when it is plain
text without path syntax, and a `<redacted>/NAME` value published as `NAME`.

### Cards and history

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
`Latest` or any existing render bundle. Every supplement is bound to a retained
candidate run and records its routing and adoption status explicitly.

```sh
PYTHONPATH="$HOME/.local/share/agent-ee-render-gallery/app" python3 -m agent_ee.render_gallery \
  --root "$HOME/.local/share/agent-ee-render-gallery/artifacts" supplement \
  --image /durable/analysis/annotated.png --packet /durable/analysis/INDEX.md \
  --candidate /durable/runs/CANDIDATE_RUN --board-sha256 ACTUAL_BOARD_SHA256 \
  --routing unrouted --adoption not-adopted \
  --label 'Diagnostic 3' --stage diagnostic
```

The command verifies the retained candidate snapshot (as `--candidate` does for
renders), re-reads its captured board and requires `--board-sha256` to equal
those bytes' hash; a missing, stale or different candidate, or a board that is
not a parseable KiCad board, is refused. The manifest records the candidate id,
captured board name and board hash. `--routing` is one of `unrouted`,
`partially-routed` or `routed`, and `--adoption` one of `not-adopted`, `adopted`
or `no-proposal`; any other value is refused. `unrouted` is checked: it is
refused when the retained board has tracks, arcs or vias, and the other two are
refused when it has none. Whether a board with tracks is completely routed, and
the adoption status, are the publisher's statement and are labelled as such.
The page labels a supplement historical unless its board hash equals the
current Latest render's; this is computed when served, not stored. A served
supplement without a recorded candidate or with an unknown status (including
supplements published before candidate binding) shows "Status unknown".

The PNG is fully validated and the copied image and source packet hashes are
bound in the publication manifest. This establishes candidate and byte
identity, not correctness of the analysis or adoption of a proposal. Only the
PNG and manifest are served; the source board and packet stay private. SVG is
not accepted. The record is the allowlisted `agent-ee.gallery-diagnostic/4`
shape above: label, stage, candidate id, `captured_board` (its basename),
board hash, routing and adoption status, publication time, the source packet
and image names (plain file names or `<redacted>`) and hashes, the image
dimensions and hash and the fixed limitation. Listings, diagnostic pages and
manifests are projected again when served; a record without candidate binding
(no schema `/2`, `/3` or `/4`) shows "Status unknown". Open `/diagnostics/RETURNED_ID/index.html` for a permanent
page or `/diagnostics/RETURNED_ID/image.png` for the exact full-size image. The
main gallery links its separate diagnostic section.

Publication, for supplements and render bundles alike, opens the gallery root
and every parent directory, `bundles/`, `diagnostics/`, the lock and the listing
without following symlinks and writes through directory descriptors: a link at
any of those places refuses publication instead of redirecting writes. Files are
created exclusively, a publication whose identifier already exists is refused
rather than replaced, and the listing is replaced atomically through an
exclusive, randomly named staging file. Publications are serialized by one lock.

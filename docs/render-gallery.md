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
- **Status records** carry only fixed codes, integers, hashes, plain evidence
  file names and model references such as `U3`
  ([Recorded status and limitations](#recorded-status-and-limitations)).
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
viewer, supplement and status record, and the stored and served JSON, the
gallery, supplement and bundle pages and the served viewer page
must carry no host, user or share text. The oracle is shown to fail on
deliberately leaky publication: pass-through, a regex scrubber, an unlisted
field, a basename without the local-path checks, trusting text that starts with
the marker, a label check that refuses only `/`, a label kept when it is plain
text without path syntax, a `<redacted>/NAME` value published as `NAME`, and a
status record that keeps a free-text model reference.

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
visible limitations; record the candidate's status as described in
[Recorded status and limitations](#recorded-status-and-limitations). Rendering does not substitute for native DRC or engineering
review. This first gallery publishes 3D images and the interactive viewer; layer
plots remain in their native review packets.

## Recorded status and limitations

A candidate's limitations (unrouted, not adopted, not qualified, a models-only
derivative, open defects, how a component model was obtained) are not free
text and are never shown as model warnings. They are carried by a separate,
immutable **status record** (`agent-ee.gallery-status/1`) attached to a
published bundle or supplement. Attaching one never changes the publication's
bytes, its listing entry or which bundle is Latest: the record is its own
publication under `statuses/ID/`, listed in `statuses.json`, and bound to the
SHA-256 of the subject's stored `manifest.json` and to its board hash. So the
same mechanism records the status of a bundle published before these fields
existed.

```sh
PYTHONPATH="$HOME/.local/share/agent-ee-render-gallery/app" python3 -m agent_ee.render_gallery \
  --root "$HOME/.local/share/agent-ee-render-gallery/artifacts" status \
  --subject bundles/BUNDLE_ID --record status.json \
  --evidence board=/durable/run/candidate.kicad_pcb --evidence index=/durable/run/INDEX.md \
  --evidence receipt=/durable/run/RECEIPT.json --evidence design-diff=/durable/run/design-diff.json \
  --evidence drc=/durable/run/drc.json --evidence model=/durable/run/models/u3.step \
  --evidence association=/durable/review/association.json
```

`status.json` holds only these fields, each a fixed code or `null` (unknown),
never free text; an omitted field is unknown:

| Field | Codes | Shown as |
|---|---|---|
| `routing` | `routed`, `unrouted` | Routed (completeness not verified) / Unrouted |
| `adoption` | `adopted`, `not-adopted` | recorded disposition |
| `qualification` | `qualified`, `unqualified` | recorded disposition, not machine qualification |
| `scope` | `full`, `models-only` | Full design candidate / Models-only derivative |
| `open_defects` | `count` (integer) with `class` `silk`, `drc` or `review` | Open defects: N ... |
| `drc` | `violations`, `raw_unconnected_items` (integers) | raw unconnected items, not an incomplete-net count |
| `models` | `reference` (`U3`), `provenance` `manufacturer`, `mechanical` or `datasheet-derived`, `transparency` `verified`, `sha256` | per model; every other model's provenance is shown unknown |
| `label`, `stage` | the label and stage vocabulary above | only for an earlier publication whose own label or stage is served as `<redacted>` |

The first four are `{"value": CODE, "evidence": [ROLES]}`; `open_defects`,
`drc` and each model also carry `evidence`. Evidence roles are `board`,
`association`, `receipt`, `design-diff`, `drc`, `index`, `model` and `review`; each `--evidence`
file is read once and its plain file name and SHA-256 are recorded. Values come
from retained evidence and are never inferred from checks; publication refuses
a value that cites no evidence or evidence that was not supplied, and checks
what the evidence can check:

- `board` must be the bytes of the subject's board hash, and `routing` must cite
  it: `unrouted` is refused when the board has tracks, arcs or vias, `routed`
  when it has none.
- Each known value must cite the retained report it was taken from. `unrouted`
  is the only claim the bound board supports alone. `routed`, `adopted`,
  `not-adopted`, `qualified`, `unqualified` and `full` must cite a disposition
  (`receipt`, `review` or `index`; `routed` also the board); `models-only` the
  `design-diff`; `drc` counts and `silk` or `drc` defect counts the `drc` report;
  `review` defect counts the `review` report; a model claim the `model` file.
  Citing the board or the association instead is refused: the association is a
  mapping of files, not a disposition.
- Every other evidence file must be covered by one reviewed **association**
  (`agent-ee.gallery-association/1`), supplied as `association` evidence. A
  hash alone, or a DRC report's `source` file name, does not show that a report,
  receipt, diff or model belongs to this candidate; the association is the
  reviewer's mapping of the subject board to the files retained with it in the
  same packet. Publication refuses evidence it does not list, and an association
  for any other board:

  ```json
  {"schema": "agent-ee.gallery-association/1", "board_sha256": "SUBJECT BOARD SHA-256",
   "evidence": [{"role": "drc", "sha256": "..."}, {"role": "receipt", "sha256": "..."}],
   "models": [{"reference": "U3", "sha256": "...", "provenance": "datasheet-derived", "transparency": null}]}
  ```

  `evidence` lists each associated file by role (not `board` or `association`)
  and SHA-256; `models` lists each reviewed model once. Nothing else is
  accepted. The gallery checks supplied evidence against the association; it
  cannot check who reviewed it, so produce it only from a reviewed packet (its
  receipt and checksums).
- `drc` counts must equal the `violations` and `unconnected_items` of the cited
  KiCad DRC JSON report; a `silk` or `drc` open-defect count must equal its
  `silk_*` or total violations.
- `models-only` must cite an `agent-ee.design-diff/1` board diff of the
  subject board (`raw.subject_sha256`) whose every entry is a
  `footprint[REF]/model` change with unchanged placement and structural hashes.
- A model claim needs `sha256`, the hash of a cited `model` evidence file; its
  reference, hash, provenance and transparency must be the association's entry
  for that reference, and the supplied board must have a footprint with that
  reference whose model file name is the model evidence file name.

`qualified`, `adopted`, `full`, `review` defect counts and model provenance are
recorded dispositions, shown as such; the gallery does not validate them.

Every card, the bundle page `/bundles/ID/index.html`, and the served
`viewer/index.html` (the stored page with the status inserted when served) show
all of these lines outside any collapsed element, with a badge listing every
recorded limitation and unknown. A publication without a record shows "No
status record attached" and every field unknown; a record whose subject hash no
longer matches the stored manifest shows "does not match" and every field
unknown; a stored or served value outside the vocabulary, or one whose cited
evidence is not hash-bound in the record, is unknown, and so is a value citing
anything but the subject's board in a record that binds no association (records
attached before associations existed show only board-checked routing). The same
citation rule is applied when serving, so a record sealed before it (for
example `qualified` citing only the board or only the association) shows that
value unknown on every page and JSON surface.

`statuses.json` is mutable and only names and orders the immutable records. Each
listed record is read from `statuses/ID/status.json` without following a link
and served only when its bytes are the canonical encoding of its projection,
`ID` is its subject manifest hash prefix and its own SHA-256 prefix, the listing
entry equals it, and its subject still matches the stored manifest and board. If
any listed record of a publication is missing, corrupt, rewritten, differs from
its listing entry or names another subject, the publication shows "does not
match" and every field unknown. The newest record is shown; older records are
retained. `/bundles/ID/status.json` and
`/diagnostics/ID/status.json` report `matched`, `mismatched` or `none` with the
verified record and `shown`, the claims the pages show from it (`null` where
unknown); in `record`, a value its citations do not support is `null` too.
`/statuses/ID/status.json` serves a record itself, read the same way: only when
its bytes are its identity and its subject still matches the stored manifest
and board (an unsupported value `null`, as above), else HTTP 404 with
`{"id": ID, "attached": "mismatched", "record": null}`. The
served `manifest.json` of a bundle or supplement and the served
`viewer/viewer.json` carry the same report as `recorded_status` beside the
projected stored fields; the stored files never contain it.

The iter8t migration attaches `routing` unrouted (board, INDEX), `qualification`
unqualified (INDEX's recorded diagnostic/unsealed/not-qualified disposition),
`scope` models-only (RECEIPT, design-diff), 9 open `silk` defects and 226 DRC
violations with 499 raw unconnected items (DRC report), U3 `datasheet-derived`
with its STEP hash (RECEIPT, model) and transparency unknown; adoption is not
recorded for it and stays unknown. Every one of these but routing needs the
reviewed iter8t association: its board hash, the RECEIPT, design-diff, DRC
report, INDEX and U3 STEP hashes of the same retained packet, and U3's STEP hash
with `datasheet-derived` provenance and unknown transparency. The historical supplement attaches
`Diagnostic 1`, `diagnostic`, `unrouted` and `not-adopted`.

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

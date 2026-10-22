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
and hashed), then image hashes and the viewer's board/model/candidate identity.
It copies only declared images and bundled viewer assets, then atomically
publishes the new index.

For a render taken from a retained run directory add `--candidate RUN_DIR`.
Publication then verifies the retained snapshot and requires the manifest's
`candidate_id`, `captured_board` and board hash to match it; a manifest that
names a candidate is refused without `--candidate`, and an unbound manifest is
refused with it. A viewer must carry the same candidate identity. These checks
catch an edited or mismatched manifest but cannot prove the producer ran
KiCad honestly.

Absolute host paths in model coverage, warnings and `viewer.json` are replaced
by `<redacted>/basename` at publication, and the server redacts
`manifest.json` and `viewer.json` of older bundles on the way out.

Each card shows model coverage outside any collapsed element: a badge ("Incomplete
models" when any model is missing, unresolved or disabled or a footprint has no
model; "Model coverage unknown" when counts are absent) and the counts of
resolved, missing, unresolved, disabled and model-less items. Only the individual
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

# Installation and environment

## Tested environment

The recorded demonstrations used Linux, Python 3.12, KiCad 10.0.7, ngspice 42,
Bubblewrap 0.9.0 and Claude Code 2.1.302. These are observed versions, not a
compatibility matrix. Windows, macOS, other KiCad major versions and other
Claude Code versions have not been demonstrated. In particular, the native
checks configure KiCad's `10.0` settings directory and `KICAD10_*` paths.

The Python package declares version 0.1.0 and Python `>=3.12` in
[pyproject.toml](../pyproject.toml). Orchestration uses the standard library.
The system interpreter `/usr/bin/python3` must also import KiCad's `pcbnew`
bindings. A virtual environment that hides system bindings is insufficient.

| Prerequisite | Use |
| --- | --- |
| `git` | Source revision provenance, checkout and evidence archives |
| `/usr/bin/python3` | Source launcher, geometry probe, orchestration |
| `kicad-cli` | JSON ERC/DRC, schematic parity, native XML netlist |
| `pcbnew` / `_pcbnew` | Saved-board geometry and runtime identity |
| `ngspice` | Passive transient check; also included in runtime fingerprints |
| `bwrap` | Filesystem/process/network isolation for invoked checks and reviewers |
| `claude`, `codex`, `agy` | Selected subscription reviewer/adjudicator harnesses; install those used by your profile |
| Scope-declared measurement producer | Additional installed executable under `/usr`, matching the pinned digest; captured parser/program/model inputs are supplied by the scope |
| `bash` | Link installer |

The optional [3D PCB viewer](pcb-viewer.md) also needs
`kicad-cli pcb export glb` with copper, silkscreen and solder-mask export options,
plus a browser with WebGL support. Install the matching KiCad 3D model libraries
for footprint model references to resolve. The viewer bundles its browser
dependencies and does not require a reviewer harness or subscription inference.

[Native 3D still renders](pcb-renders.md) require `kicad-cli pcb render` with
the high-quality, floor and board-stackup-color options (tested with KiCad
10.0.7). They use the standard Python library and require no browser. Stock
component models may come from the installed libraries, a local `--models`
cache, or explicitly requested `--fetch-models` downloads.

Bubblewrap must actually be allowed to create the required namespaces and mount
view. An installed binary alone does not establish that. Native execution in
the [control demonstration](../evidence/rc-verified/control/REPORT.md) exercised
the boundary; the tests also attempt a forbidden candidate write.

KiBot, kicad-happy, document conversion tools, Herdr, Cairn and Gemini were
available to the builders. The normal review command does not require those
systems. Herdr is not the runtime scheduler: the CLI starts its own bounded
review subprocesses. Explicit profiles select Claude, Codex or Agy; see [profiles](reviewer-profiles.md).

## Checkout and links

The GitHub repository is public. These commands require the prerequisites above:

```sh
git clone https://github.com/halbritt/agent-ee.git
cd agent-ee
./scripts/install.sh
agent-ee doctor
```

The installer creates these symlinks to the current checkout:

| Destination | Target |
| --- | --- |
| `~/.local/bin/agent-ee` | `bin/agent-ee` |
| `~/.agents/skills/agent-ee` | `skills/agent-ee` |
| `${CODEX_HOME:-$HOME/.codex}/skills/agent-ee` | `skills/agent-ee` |
| `~/.claude/skills/agent-ee` | `skills/agent-ee` |

Ensure `~/.local/bin` is on your shell's `PATH`. The source launcher uses the
system Python and locates source relative to its resolved checkout. You can
always invoke `./bin/agent-ee` from the checkout without installing links.

Running the installer again is harmless when the existing symlinks point to
this checkout. It refuses any unrelated existing file or link and exits 1.
An earlier link can have been created before a later conflict is encountered;
the installer is not a transactional rollback operation. Inspect a conflicting
destination before moving or removing it, then rerun the installer.

The skill links follow the checkout, but the skill text currently points users
to `~/git/agent-ee` for source examples. If installing elsewhere, use the actual
checkout path and the CLI reference rather than assuming that convenience path.
The package's console entry point also exists in `pyproject.toml`; the documented
installation method is the source launcher, which avoids a required package
installation or extra interpreter environment.

## Container image

The [Dockerfile](../Dockerfile) pins the demonstrated toolchain: KiCad
10.0.7 with stock symbols, footprints, templates and `pcbnew` bindings, ngspice
42, Bubblewrap 0.9.0 and Python 3.12, the installed kicad-happy analyzer and
the pinned kicad-route with KRT for the routing stage, with this checkout
installed through the source launcher. It contains no reviewer harness or credential, so it supports
checks-only reviews and CI, not agent reviews. The
[container reference](container.md) explains how each pin is enforced.

```sh
docker build -t agent-ee:local .
profile=$(bash scripts/container_apparmor.sh)
docker run --rm --network none \
  --security-opt seccomp=unconfined --security-opt "apparmor=$profile" \
  --security-opt systempaths=unconfined \
  agent-ee:local agent-ee doctor --checks-only
```

### Host privileges for Bubblewrap

Bubblewrap still isolates every native check inside the container. To create its
namespaces, the container needs three relaxations of Docker's default
confinement and, on an AppArmor host, a named host profile. The tool sandbox
gets no network:

| Option | Grants |
| --- | --- |
| `--security-opt seccomp=unconfined` | Removes Docker's syscall filter so the non-root user can call `unshare` for a user namespace; kernel permission checks still apply |
| `--security-opt apparmor=<profile>` | Replaces Docker's `docker-default` AppArmor profile, which denies the mounts Bubblewrap makes, with the project's permissive `agent-ee-container` profile that also allows user namespaces (`unconfined` when host AppArmor is disabled) |
| `--security-opt systempaths=unconfined` | Removes Docker's `/proc` masks so a fresh `proc` can be mounted in the new PID namespace; also exposes masked `/proc` entries to the container |

`bash scripts/container_apparmor.sh` loads that profile with
`sudo -n apparmor_parser -r` and prints the name to pass. It changes no other
profile or global sysctl. Plain `apparmor=unconfined` is not enough on a host
that restricts unprivileged user namespaces: the first hosted CI run failed that
way with `Failed RTM_NEWADDR: Operation not permitted`.

Run as a non-root user: the image's `agentee` user, or `--user "$(id -u):$(id -g)"
-e HOME=/tmp` when a mounted workspace must stay owned by you. `--privileged`,
`--cap-add SYS_ADMIN` and container root are not needed and grant more. The
demonstrated Ubuntu 24.04 host kept `kernel.apparmor_restrict_unprivileged_userns=1`.
Loading the profile needs that `sudo` permission, which is an explicit
administrator decision on a self-hosted machine. If the relaxations are missing, the native checks fail
and the run is incomplete; agent-ee never falls back to unsandboxed execution. See
[Bubblewrap inside the container](container.md#bubblewrap-inside-the-container)
for each failure message and what each option exposes.

### Sample consumer workflow

The reusable [action](../action.yml) runs a checks-only review in the image,
uploads the run directory as an artifact and writes the report to the job
summary. It judges the outcome from `result.json`: it fails only when a declared
check reports a defect, coverage is incomplete, or the result is missing,
malformed or refused by `agent-ee verify`. The expected exit 1 of a checks-only
review is not a failure. No model review runs.

This workflow runs the action on the RC fixture with the seeded resistor defect
and is expected to fail. The job summary shows the `check_failed` verdict, the
`rc-step` row (`vout_1ms=0.0951625 outside [0.62, 0.65]`) and the run report.
The run directory is uploaded as `rc-seeded-run`.

```yaml
name: agent-ee checks
on:
  pull_request:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  checks:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: halbritt/agent-ee@REF  # replace REF with a reviewed commit SHA
        with:
          scope: fixtures/rc/seeded.scope.json
          output-directory: build/agent-ee/rc-seeded
          max-seconds: "900"
          artifact-name: rc-seeded-run
```

Inside an agent-ee checkout, `uses: ./` runs the local copy. For a real board,
use your own scope and requirements; the fixture's limits apply only to that
filter. Consumer pull requests are a choice of the consumer project. agent-ee's
own CI runs on pushes to `main` and manual dispatch. It runs the RC fixtures
directly in the image and judges them with the same gate, but it does not call
the action and uploads no run directory: the fixture inputs include third-party
design files whose publication the owner has not yet approved. A hosted run of
this sample waits for that decision. Inputs, verdicts and the
local evidence for this behavior are listed in the [container reference](container.md#reusable-action).

## Subscription authentication

Authenticate each selected harness through its subscription workflow. Credential
paths, native executable discovery and overrides are documented in
[reviewer profiles](reviewer-profiles.md). No API key is required.

The credential file is mounted read-only during inference and is not copied
into retained evidence. The child receives a cleared environment; parent API
keys and alternate provider configuration are not passed through. An unavailable
credential, auth failure or exhausted quota leaves that review incomplete. There
is no API-key or OpenRouter fallback. Do not copy credential contents into a
scope, report, issue or archive.

`doctor` probes executable versions and the Python binding import, and prints
the SHA-256 of each probed binary. It does not
read the credential file, validate subscription access, run a real sandboxed
check, or perform inference. A passing doctor report is prerequisite evidence
only. Run the fixture described in the [operator guide](operator-guide.md) for
actual integration.

## Native libraries and models

Review does not expose the host's live user library configuration. Declare the
effective project tables, external libraries and stock libraries in the scope.
The provided fixture expects stock resources under `/usr/share/kicad`; adjust
the scope's source paths if your installation differs. Candidate destinations
are described in the [scope reference](scope-reference.md).

No general SPICE model bundle is installed by agent-ee. The existing generator
uses ideal populated passive values. Capturing an active-device model alone
does not make that generator understand or simulate it.

## Upgrade and removal

Commit or preserve local work before updating the checkout. Updating Python
implementation files changes the review policy fingerprints; prior results
must be rerun under the new implementation or inspected with their matching
historical source version. Updating a README alone does not change those Python
fingerprints. Native tool upgrades also invalidate runtime freshness.

To uninstall discovery, inspect each destination above with `readlink`, then
unlink only the links targeting this checkout. This does not require deleting
designs, captured evidence or the repository. Do not remove another installation's
files merely because their names match. After moving a checkout, recreate the
links from the new location.

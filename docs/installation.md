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
| `claude` | Tool-free subscription review and adjudication |
| `bash` | Link installer |

Bubblewrap must actually be allowed to create the required namespaces and mount
view. An installed binary alone does not establish that. Native execution in
the [control demonstration](../evidence/rc-verified/control/REPORT.md) exercised
the boundary; the tests also attempt a forbidden candidate write.

KiBot, kicad-happy, document conversion tools, Herdr, Cairn and Gemini were
available to the builders. The normal review command does not require those
systems. Herdr is not the runtime scheduler: the CLI starts its own bounded
review subprocesses. The reusable adapter currently invokes Claude Code only.

## Checkout and links

The GitHub repository is private. These commands require access to it and the
prerequisites above:

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

## Subscription authentication

The adapter expects the installed Claude Code executable on `PATH` and an
existing subscription credential at `~/.claude/.credentials.json`. Authenticate
with the installed harness's normal subscription workflow before running a
review. A login stored only in an alternative config directory does not satisfy
this adapter's fixed credential-file lookup.

The credential file is mounted read-only during inference and is not copied
into retained evidence. The child receives a cleared environment; parent API
keys and alternate provider configuration are not passed through. An unavailable
credential, auth failure or exhausted quota leaves that review incomplete. There
is no API-key or OpenRouter fallback. Do not copy credential contents into a
scope, report, issue or archive.

`doctor` probes executable versions and the Python binding import. It does not
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

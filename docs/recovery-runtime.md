# Captured workflow runtime

AGENTEE-519 qualifies runtime access for the captured acquisition and imported
esp_prog articles. The default run stops acquisition after schematic emission
and deliberately cancels esp_prog after native capture and runtime preflight.
Neither result is file-release acceptance. Full M1 acceptance remains AGENTEE-515.

## Inputs and access

[The input manifest](../fixtures/recovery/inputs.json) pins 204 files: the
acquisition build plan and all 198 files in its revised input directory, the
original esp_prog portfolio config, and its four declared native inputs.
Acquisition declares 17 populated parts. The exact esp_prog source board has 21
native footprint objects, including graphics. It has no assembly population
manifest; its populated-part count remains unknown. The historical routed output
has 35 native objects and is not substituted for this source.

The source files are owner-retained local evidence, outside Git. On the current
host, their root is `/home/halbritt/git/agent-ee`. A clean checkout requires a
local copy of the manifest's exact relative paths from the evidence custodian.
Supplier captures and stock KiCad files retain their origin metadata inside the
bundle. Redistribution clearance has not been established; this repository
contains the inventory, not a republished input bundle. No input is downloaded
or reconstructed when it is missing. An unavailable file or wrong hash exits 1
and lists the affected paths.

Set `RETAINED_ROOT` to that local evidence root. Verification needs only Python
3.12 or later and does not import KiCad:

```sh
python3 scripts/recovery_runtime.py verify --inputs-root "$RETAINED_ROOT"
```

`prepare --inputs-root DIR --out NEW_DIR` copies the pinned bytes and verifies
them again. It retains both original configs; a separate `esp-prog.json` changes
only absolute input locators. `prepared.json` records the original root, manifest
hash and derived config hash. Initial 1.6 mm acquisition inputs remain under
`history/initial-qualification`; the revised build inputs remain unchanged.
Diagnostic historical files such as `manufacturing-decision.json` do not become
current acceptance evidence through copying.

## Pinned container procedure

Build from the checkout being qualified. The Dockerfile pins the native tools,
router and Rust toolchain; the resulting image ID pins the actual execution.
A previously built matching image can be selected by its full local image ID.
The harness verifies its installed Rust planner against this checkout's Rust
sources and validates the router against the current recorded pin.

```sh
mkdir -p build
docker build --iidfile build/recovery-image.id -t agent-ee:recovery .
scripts/recovery-container.sh \
  --image "$(cat build/recovery-image.id)" \
  --inputs-root "$RETAINED_ROOT" \
  --out build/recovery-checkpoint
```

The output directory must be new. Docker and Git are host prerequisites. The
script records the image inspection, source revision and dirty status. It mounts
the checkout and retained evidence read-only and the new output directory
writable, runs as the caller, and disables networking. It applies the existing
[Bubblewrap container options](installation.md#host-privileges-for-bubblewrap),
without changing host sysctls, tool installations or subscription settings.
Missing Docker images are refused; `--pull never` prevents an implicit pull.
The image build itself needs its pinned package/source acquisition.

`run/runtime-report.json` records command arguments, exit codes, elapsed times,
source hashes, logs and the acquisition build checkpoint. Readiness probes have
a 60-second limit each; workflow commands have a 3,600-second outer limit plus
their own captured budgets. The public commands are:

- `agent-ee doctor --checks-only --router-checkout /opt/kicad-route`
- `python3 scripts/build_placement_engine.py verify --output /usr/local/libexec/agent-ee/agent-ee-placement`
- `agent-ee place run CONFIG --out JOB`, with `JOB/CANCEL` present before launch
- `agent-ee build run PLAN --out BUILD --through schematic`

esp_prog's public command exits 1 with `status: cancelled`. Its retained report
must show completed runtime identity/native capture, cancellation before a Rust
process result, no seed results and no evaluation counters for the harness to
record `runtime_ready_before_cancellation: true`. It remains a cancelled job.
An input refusal, missing tool or incomplete capture is recorded as a failure;
no intent is stripped to make readiness pass. AGENTEE-520 may expose previously
ignored declarations in these exact inputs; such a refusal remains a recovery
gap rather than permission to edit the originals. Acquisition runs independently
of that esp_prog refusal.

`--full` explicitly spends the captured placement/routing budgets and runs both
complete workflows. It records the current results, including failures, while
leaving historical success and the harness's `acceptance: false` intact. A failed
esp_prog preflight prevents its full search. Full mode was not executed for
AGENTEE-519; it belongs to later release qualification. The default checkpoint
does not establish routing, artwork, sealed-package or engineering acceptance.

## Host launcher diagnosis

Use `./bin/agent-ee doctor --checks-only` to inspect this checkout without
installing links. Doctor reports its actual interpreter, prefix, launcher and
CLI source. The Python version and `pcbnew` import probes use that interpreter;
a working system import cannot conceal a failed virtual-environment import.
`runtime.uses_system_python` identifies a different interpreter/venv, and
`--router-checkout DIR` adds a required pinned-router/backend probe. Doctor does
not test sandbox execution or subscription authentication.

The source launcher propagates its source directory to fresh Python subprocesses.
An existing uv package launcher can point at a different checkout/interpreter.
The normal installer refuses it. After inspecting that link, explicitly migrate:

```sh
./scripts/install.sh --migrate-uv-launcher
./bin/agent-ee doctor --checks-only
```

Only a symlink to `~/.local/share/uv/tools/agent-ee/bin/agent-ee` containing the
agent-ee console import is recognized. Migration preserves it as
`~/.local/bin/agent-ee.uv-backup`; an existing backup or unrelated executable
is refused. It does not uninstall uv, replace a regular executable, install
Rust, or change machine-wide tool choices. Repeating supported setup is safe.

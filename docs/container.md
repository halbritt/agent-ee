# Container image and CI action

The repository's [Dockerfile](../Dockerfile) builds the demonstrated native
toolchain with this checkout's source launcher. It is for checks-only reviews,
project CI and the reusable [GitHub Action](../action.yml). It does not contain
the Claude, Codex or Agy harnesses or any subscription credential, so the image
cannot run initial reviews, adjudication or campaigns. `agent-ee doctor` without
`--checks-only` therefore exits 1 inside the image because `claude` is absent.

The image has been built and exercised on one Linux x86-64 host with Docker
29.8 (Ubuntu 24.04 host, kernel 6.8, AppArmor enabled). Other architectures,
container engines have not been demonstrated. The first hosted Ubuntu runner
exposed an additional AppArmor user-namespace requirement, handled below.

## Pinned toolchain

| Component | Pin | How the pin is enforced |
| --- | --- | --- |
| Base | `ubuntu:24.04@sha256:68ee2200…` | Image index digest in `UBUNTU_DIGEST` |
| Package indexes | Snapshot `20261015T000000Z` | `snapshot.ubuntu.com` and `snapshot.ppa.launchpadcontent.net` URLs, so later archive uploads do not change resolution |
| KiCad 10.0.7, stock symbols, footprints and templates | `10.0.7~ubuntu24.04.1` | Exact `apt-get install pkg=version` from the KiCad 10.0 releases PPA, signed by key `FDA854F61C4D0D9572BB95E5245D5502FAD7A805` stored in [docker/](../docker/kicad-10.0-releases.asc) |
| pcbnew Python bindings | Shipped by the `kicad` package | Build fails unless `/usr/bin/python3` imports `pcbnew` and reports build version 10.0.7 |
| ngspice 42 | `42+ds-3build1` | Exact apt version; build fails unless `ngspice --version` reports `ngspice-42` |
| Bubblewrap 0.9.0 | `0.9.0-1ubuntu0.3` | Exact apt version; build fails unless `bwrap --version` is `bubblewrap 0.9.0` and the binary is not setuid |
| Python 3.12 | `python3=3.12.3-0ubuntu2.1` | Exact apt version; build fails unless the interpreter reports 3.12 |
| PDF text extraction | `poppler-utils=24.02.0-1ubuntu9.10` | Exact apt version for page-cited datasheet checks |
| TLS bootstrap | `ca-certificates=20240203` | Installed from the immutable noble release pocket before the snapshot sources are enabled |

Pinned packages are also held with `apt-mark hold`. Every pin fails closed: an
unavailable version stops `apt-get install`, and the verification layer refuses
an image whose tools report other versions or whose stock library paths used by
the RC fixtures are missing. Unpinned dependencies (shared libraries, `git`) are
fixed by the snapshot timestamp rather than individual version strings.

Updating a pin means changing the build argument, rebuilding and rerunning the
suite and fixtures. A new tool binary changes `runtime_files`, so earlier results
stop verifying under the new image, as on a native host.

Build from a clean checkout:

```sh
docker build -t agent-ee:local .
```

## Bubblewrap inside the container

agent-ee keeps its own sandbox inside the container: every native check still
runs under Bubblewrap with a read-only candidate, separate PID/IPC/UTS/network
namespaces and a minimal filesystem view, exactly as described in
[security](security.md#candidate-permissions). Docker's default confinement
prevents Bubblewrap from creating those namespaces. Running the image requires
three explicit changes to Docker's confinement plus a host AppArmor profile
when AppArmor restricts unprivileged user namespaces:

| `docker run` option | Why Bubblewrap needs it | What it grants the container |
| --- | --- | --- |
| `--security-opt seccomp=unconfined` | Docker's default seccomp filter rejects `unshare`/`clone` with namespace flags for processes without `CAP_SYS_ADMIN`; without it Bubblewrap reports "No permissions to create new namespace" | Removes Docker's syscall filter. Kernel permission and capability checks still apply; a non-root container user gains no capabilities in the host namespace |
| `--security-opt apparmor=agent-ee-container` | Docker's default profile denies mounts; Ubuntu also restricts capabilities after unprivileged user-namespace creation without an explicit allowance | Selects the named permissive outer profile with `userns,`; inner Bubblewrap restrictions remain in force |
| `--security-opt systempaths=unconfined` | Docker overmounts parts of `/proc`, and the kernel refuses a fresh `proc` mount in a user namespace while those overmounts exist ("Can't mount proc") | Unmasks paths such as `/proc/kcore`, `/proc/keys`, `/proc/timer_list` and makes `/proc/sys` writable to processes privileged enough to write it. The non-root container user still lacks that privilege; root-only files stay root-only |

Run as the image's non-root user (default `agentee`, UID 10001) or as your own
UID with `--user "$(id -u):$(id -g)" -e HOME=/tmp`. Bubblewrap is not setuid, so
it creates an unprivileged user namespace. Do not use these instead:

- `--privileged` grants every capability and host device access; agent-ee does
  not need it.
- Running as container root without `--privileged` fails, because a non-setuid
  Bubblewrap started as UID 0 needs `CAP_SYS_ADMIN` rather than a user namespace.
  Adding `--cap-add SYS_ADMIN` would hand the container that capability.
- Disabling the host's `kernel.apparmor_restrict_unprivileged_userns` sysctl
  changes every unconfined application. The image and action do not do this.

Before using the image on an AppArmor host, run
`bash scripts/container_apparmor.sh`. It uses `sudo -n apparmor_parser -r` to
load or replace the project-owned [named profile](../docker/agent-ee.apparmor) and prints the name to pass
to Docker. It requires AppArmor 4 syntax support and passwordless permission for that
command; otherwise it
fails. The profile remains installed for later runs and applies only to
containers that select it. It does not modify other profiles or global sysctls.
When host AppArmor is disabled, the helper prints `unconfined` instead. The
helper assumes a local Linux Docker daemon, not a remote daemon.

Project CI and the reusable action perform this preparation explicitly. A
self-hosted runner administrator must authorize it. Ubuntu can otherwise deny
loopback setup with `Failed RTM_NEWADDR: Operation not permitted`, even when
Docker uses `apparmor=unconfined`. That was the first hosted CI failure; strict
mode correctly failed rather than skipping those tests.

Checks-only work needs no network, so the documented commands also pass
`--network none`. Bubblewrap still unshares the network for native tools; the
container-level setting removes the outer network as well. Tool sandboxes are
never given network access by this image or action.

Without the three relaxations, agent-ee does not silently run unsandboxed. The
native checks record `execution: failed` with the Bubblewrap diagnostic, the run
is incomplete, and the action's gate reports `coverage_incomplete`.

A typical run, with the review directory created under the mounted workspace:

```sh
profile=$(bash scripts/container_apparmor.sh)
docker run --rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
  --security-opt seccomp=unconfined --security-opt "apparmor=$profile" \
  --security-opt systempaths=unconfined \
  -v "$PWD:$PWD" -w "$PWD" agent-ee:local \
  agent-ee review fixtures/rc/seeded.scope.json --out build/seeded --checks-only
```

Mount the workspace at the same absolute path. Captured manifests record absolute
source paths, and `verify` compares them with the live source. Scope inputs must
be inside the mounted workspace or inside the image (the fixture's stock KiCad
libraries under `/usr/share/kicad` come from the image).

## Doctor in the image

`agent-ee doctor --checks-only` probes kicad-cli, ngspice, Bubblewrap, system
Python and pcbnew, and prints each version output together with the SHA-256 of
the binary it relies on. The pcbnew hash is the `_pcbnew` native extension that
`runtime_files` also records in every result. See the
[CLI reference](cli-reference.md#doctor) for fields and exit codes.

## Project CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on pushes to
`main` and on manual dispatch only; there is no pull-request trigger in this
repository. One job:

1. Builds the image from the checkout.
2. Runs `agent-ee doctor --checks-only` and exercises the production networkless
   executor before the suite. Failed setup also records host AppArmor diagnostics.
3. Runs the full test suite in the image with `AGENT_EE_REQUIRE_TOOLCHAIN=1`
   and fails if any test fails, errors or is skipped.
4. Runs `scripts/check_docs.py` in the image.
5. Runs checks-only reviews of the RC control and seeded fixtures directly in
   the image, with `--network none` and the runner's non-root UID, then
   `agent-ee verify` on each run. The same gate script the action uses judges
   each `result.json`, receives the verify exit status and appends its report to
   the job summary. The step asserts that every control check passes and that
   the seeded run has a fully executed check set whose `rc-step` transient check
   reports `defect` while ERC, DRC and geometry pass.

Project CI does not call the reusable action and uploads no run directory. A run
directory holds copies of the candidate inputs, and the RC fixture inputs include
third-party design material: the FOME-MC-origin fixture and captured stock KiCad
library files. This repository is public, and the owner has not yet resolved
whether that material may be republished, so run outputs stay on the ephemeral
runner and only the gate summary reaches the job page.

`AGENT_EE_REQUIRE_TOOLCHAIN=1` is the strict-toolchain contract: toolchain tests
must fail rather than skip when KiCad, ngspice, Bubblewrap or a usable user
namespace is missing. CI also refuses any skip on its own, so a skip cannot pass
unnoticed whatever the test suite's skip behavior.

The workflow file has been checked locally for YAML structure, and its image,
test, docs and fixture commands have been run locally in the built image. The
fixture step was replayed locally with GitHub's `bash -eo pipefail` semantics:
the control gave `checks_passed`, the seeded run gave `check_failed` with
`rc-step` `defect` and ERC, DRC and geometry `pass`, `agent-ee verify` exited 0
for both, and the step failed when a run directory already existed or an
expectation did not match. The first hosted run built the image but failed at the Bubblewrap host-policy
boundary described above. The named-profile correction still needs a passing
hosted run; local success is not reported as hosted CI acceptance.

## Reusable action

Consumer KiCad projects can call [action.yml](../action.yml) from pull requests
or any other trigger. agent-ee's own development does not use pull requests.

| Input | Default | Meaning |
| --- | --- | --- |
| `scope` | required | Scope JSON inside the workspace |
| `output-directory` | `agent-ee-run` | New run directory inside the workspace; an existing directory is refused |
| `max-seconds` | `1200` | Review work budget, as for `review --max-seconds` |
| `artifact-name` | `agent-ee-run` | Name of the uploaded run-directory artifact |
| `image` | empty | Use an existing local image instead of building this action's Dockerfile |

Outputs are `verdict` and `run-directory`. The action:

1. Builds the pinned image from the action's own checkout unless `image` is set.
2. Runs `agent-ee review SCOPE --checks-only` in the container, then
   `agent-ee verify` on the new run. Their exit statuses are recorded, not judged:
   checks-only always exits 1.
3. Runs [scripts/action_gate.py](../scripts/action_gate.py) on the run. It writes
   the verdict, a check table and the run's `REPORT.md` to the job summary.
4. Uploads the whole run directory, including hidden scratch files, as an
   artifact whatever the verdict.
5. Fails the step unless the verdict is `checks_passed`.

| Verdict | Condition | Action result |
| --- | --- | --- |
| `checks_passed` | Every declared check executed with `pass` or `info` | Success |
| `check_failed` | Every declared check executed; at least one reports `defect` | Failure |
| `coverage_incomplete` | A declared check is missing, `unknown` or not executed; or the run errored, was interrupted or never finalized | Failure |
| `result_invalid` | `result.json` missing, unreadable or malformed; no new run directory; or `agent-ee verify` refused the run | Failure |

No model review or adjudication runs, and `checks_passed` is not design
acceptance. It says only that the declared native checks executed and passed
for the captured candidate. The gate's tests are in
[test_action_gate.py](../tests/test_action_gate.py).

A consumer repository uses the action by reference or from a copy. Each run builds the image unless an `image` is
supplied, which takes a few minutes and downloads the KiCad packages. Pull
requests from forks run the consumer's scope and design files with the relaxed
Docker confinement described above. Bubblewrap still isolates the native tools,
but decide whether that is acceptable for untrusted contributions.

### Sample workflow and local evidence

The [installation guide](installation.md#sample-consumer-workflow) has a sample
consumer workflow that runs the action on the seeded RC fixture and fails with the
`rc-step` finding in the job summary. Like every action run, it uploads the run
directory as an artifact, and the seeded run directory contains the third-party
fixture inputs. A hosted run of the sample therefore waits for the owner's
decision on publishing that material.

The local evidence for this behavior is limited. A scratch harness, not part of
the repository, ran the action's shell steps with GitHub's `bash -eo pipefail`
semantics against the image built on the demonstrated host. It replaced
`upload-artifact` with a directory copy. Results:

- The seeded fixture produced `check_failed`, with the `rc-step` defect in the
  summary and a failed final step.
- The control produced `checks_passed`.
- A zero budget gave `coverage_incomplete`.
- A missing scope, an existing output directory and an output outside the
  workspace each gave `result_invalid`.
- The copied run directory, hidden files included, matched the original.

The action has not yet run on a hosted runner.

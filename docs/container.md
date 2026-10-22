# Container image and CI action

The repository's [Dockerfile](../Dockerfile) builds the demonstrated native
toolchain with this checkout's source launcher. It is for checks-only reviews,
project CI and the reusable [GitHub Action](../action.yml). It does not contain
the Claude, Codex or Agy harnesses or any subscription credential, so the image
cannot run initial reviews, adjudication or campaigns. `agent-ee doctor` without
`--checks-only` therefore exits 1 inside the image because `claude` is absent.

The image has been built and exercised on one Linux x86-64 host with Docker
29.8 (Ubuntu 24.04 host, kernel 6.8, AppArmor enabled). The project CI also passed on a hosted Ubuntu runner at commit `c0ddba2`: 230
strict tests, no skips, documentation checks and both native RC gates. See the
[retained CI result](https://github.com/halbritt/agent-ee/actions/runs/39050160054).
Hosted CI then failed on every push from 2026-10-18 (last green `18c2d97`); see
[Hosted CI restore](#hosted-ci-restore-2026-10-18) for the causes and the
corrections, which have been replayed locally in the image but not yet observed
on a hosted runner. The workflow now provisions the
[required external inputs](#required-external-inputs) on the runner; a hosted run
with them has not yet been observed. Other architectures and container engines have not been demonstrated. The first
hosted run exposed an AppArmor user-namespace requirement, handled below.

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
| kicad-happy 2.3.1 | Commit `06840467ad0f5d76af45f64449b1f5ecb10f052f` | Installed at `/opt/kicad-happy`, outside the package; build validates commit and plugin metadata |
| zstd CLI (tests only) | `zstd=1.5.5+dfsg2-2build1.1` | Exact apt version, the image's `libzstd1` version; reference frames for the fab-consistency decoder test |
| kicad-route with KRT 0.23.1 | kicad-route `b5774496d8466b67ffa0362b6665fee2108155a3`, owned engine (fork lineage `0494a05e`) | Installed at `/opt/kicad-route` by [docker/kicad-route/install.sh](../docker/kicad-route/install.sh); see [Pinned routing tool](#pinned-routing-tool) |
| TLS bootstrap | `ca-certificates=20240203` | Installed from the immutable noble release pocket before the snapshot sources are enabled |

Pinned packages are also held with `apt-mark hold`. Every pin fails closed: an
unavailable version stops `apt-get install`, and the verification layer refuses
an image whose tools report other versions or whose stock library paths used by
the RC fixtures are missing. Unpinned dependencies (shared libraries, `git`) are
fixed by the snapshot timestamp rather than individual version strings.

All eight image-build `apt-get update` and `apt-get install` invocations use
[the shared outer retry wrapper](../docker/apt-retry.sh), including the TLS
bootstrap, native toolchain, routing build stage and final zstd installation.
Each command gets at most four attempts, with waits of 60, 120 and 240 seconds
between failed attempts: **420 seconds (seven minutes) of total outer backoff
per command**, plus at most four executions of that apt command. Across all
eight calls the maximum added outer backoff is 3,360 seconds (56 minutes).
This bounds the outer attempts and waits, not total build wall time: package
counts, transfer times and apt's own per-file retries also contribute.
Each attempt and wait is logged; a fourth failure returns that command's last
nonzero exit status and fails the build. Before retrying, the wrapper clears
partial index and package downloads while preserving complete indexes needed
by install retries.

An invocation without a command exits 2 with a diagnostic on stderr before
attempting any work. The helper remains available to the routing build stage;
the final stage removes `/usr/local/bin/apt-retry` after its last apt call
(the zstd installation).

The existing `Acquire::Retries=5` options remain, with exponential retry delays
enabled and each delay capped at eight seconds. The outer loop also covers
body-less HTTP 5xx responses that apt may not retry internally. Updates use
`APT::Update::Error-Mode=any` so a failed index fetch cannot return success with
stale or missing indexes. Snapshot URLs, package version pins and signature
verification remain unchanged.

`doctor` reports the installed PDF extractor binary and, when
`AGENT_EE_KICAD_HAPPY` names an installation, the analyzer version and its
source-file hashes. Optional tools absent from a basic installation do not
become required merely by running doctor. An explicitly configured broken
analyzer does fail the probe.

## Hosted build cache

Project CI uses `docker/setup-buildx-action` with the `docker-container` driver
and `docker/build-push-action` to load `agent-ee:ci` into the runner's Docker
engine. It imports and exports a GitHub Actions cache with
`type=gha,version=2,scope=agent-ee-native-amd64-v1`. Export uses `mode=max` so
intermediate stages, including the routing build stage's apt install, survive
between runs. The cache export targets `toolchain`, which ends before the
application's `COPY .`. A second build on the same builder loads the final
image without exporting its source layers. Retained design fixtures therefore
do not enter the remote cache. The workflow does not push an image or upload a
build record.
The reusable action's build is unchanged.

The Buildx input is pinned to `v0.37.1` (also reported by the host's
`docker buildx version` during this update). The `docker-container` daemon uses
`image=moby/buildkit@sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea`.
This is the OCI image index digest returned on **2026-10-20** by
`docker buildx imagetools inspect moby/buildkit:buildx-stable-1` from Docker Hub
(`docker.io/moby/buildkit`); the workflow uses the digest directly, so later tag
movement does not change the daemon. Update both pins deliberately.

Only `cache-to` has `ignore-error=true`: a cache export service or quota failure
may leave the cache cold but does not fail a successful toolchain build.
Import misses still run the strict apt path below. Neither the toolchain cache
step nor the final image build may use `continue-on-error`; compilation, apt
and verification failures must still fail the job. The workflow contract in
[test_apt_retry.py](../tests/test_apt_retry.py) rejects mutations that remove
export tolerance, float either builder pin or suppress either step's failures.

The scope names the cache object, not the package identity. BuildKit's layer
keys include the Dockerfile instructions, consumed build arguments and copied
inputs. Changing `APT_SNAPSHOT`, a package pin, the Ubuntu digest, the signing
key or the retry helper invalidates the affected layer and its descendants.
Changing application source at the final `COPY .` preserves earlier native
layers. Bump the scope's `v1` suffix to discard the entire hosted cache. There
is no fallback to a different snapshot or package version.

A warm build reuses the completed apt-install layers, whose original build
used apt's signed metadata and package hash checks. It does not rerun apt or
reverify metadata on each cache hit. A cache miss executes the unchanged
strict apt updates, signature verification and pinned installs; a persistent
mirror outage still fails closed. Cache eviction, GitHub cache unavailability,
a first build or an invalidated apt layer can therefore expose a mirror outage.
The cache is an optimization, not an offline guarantee. The
[Docker cache backend documentation](https://docs.docker.com/build/cache/backends/gha/)
describes its authentication, branch visibility and storage limits.

Run the bounded local simulation from the repository root:

```sh
python3 scripts/check_ci_cache.py build/ci-cache-probe
```

It builds an original, small native stage using the production Ubuntu digest,
signed noble release metadata and exact TLS bootstrap package pin. A warm build
exports inline layer metadata. A second build changes the source payload and
sets an unreachable local HTTP proxy: the apt step must be `CACHED` and the
build must succeed. A third build changes the fixture package pin with the
same unreachable proxy and must fail fetching metadata, proving that a pin
change invalidates the apt layer. Logs are written under the supplied directory; the inspection image is
`agent-ee:ee319-cache-probe`. This exercises real apt and BuildKit layer reuse,
not the complete native image or GitHub's remote cache transport.

After integration, observe the next main-branch CI run without changing pins:
look for successful cache import/export in the image step, then a subsequent
run's apt-install steps marked `CACHED`, with the image loaded for Doctor and
the strict gate. The initial run must populate the cache. Hosted cache restore
and export require that follow-up run; local evidence does not establish them.
No hosted run was triggered for this change.

## Pinned routing tool

The routing stage's external tool is installed in a separate build stage and
only its final tree is copied into the image. The pins live in
[docker/kicad-route/install.sh](../docker/kicad-route/install.sh) and the
hash-locked [wheel list](../docker/kicad-route/krt-requirements.txt):

| Input | Pin | Enforcement |
| --- | --- | --- |
| kicad-route | Commit `b5774496d8466b67ffa0362b6665fee2108155a3` from `github.com/halbritt/kicad-route` | Exact commit fetch and detached checkout; HEAD and clean source checked offline. The adapter also checks every `bin/` and `src/` blob |
| Owned routing engine | `src/kicad_route/router/`, including `rust_router/Cargo.lock` and `UPSTREAM.json` | Ships inside the pin; no external KRT acquisition or prebuilt routing module |
| Python packages | numpy 2.5.4, scipy 1.18.1, shapely 2.2.0, pillow 13.0.0 | Existing CPython 3.12 manylinux x86-64 wheel lock, installed with `--require-hashes --only-binary=:all: --no-deps`; exact four distributions verified |
| Rust toolchain | `rust:1.99.1-slim-bookworm` at the digest in Dockerfile | Digest-pinned image copied into the build stage; exact `rustc` and `cargo` version banners required |
| Native extension | Included Rust source | Owned `build_router.py` invokes `cargo build --release --locked`; the built module SHA-256, engine source digest and toolchain versions are recorded |
| Build-only tools | pip, C compiler, Python headers, Rust | Stay in the build stage; the final image receives the checkout, venv and receipts |

Only linux/amd64 is qualified. Other architectures fail closed. The image does
not install Freerouting. Builds require registry access for the locked Cargo
crates and wheel files; routing and final verification run offline.

The engine descends from the owner's MIT fork at `0494a05e`, carrying the tap-grid
and forbidden-layer weld fixes tracked in
[upstream #1291](https://github.com/drandyhaas/KiCadRoutingTools/issues/1291) and
[upstream #1292](https://github.com/drandyhaas/KiCadRoutingTools/issues/1292).
`UPSTREAM.json` retains the exact lineage and MIT notices. kicad-route now owns
this source; agent-ee passes the canonical `--backend builtin` (AGENTEE-450).

The final build runs `install.sh verify` without network. It checks the source
commit and clean tree, existing kicad-route state-manifest schema and drift,
source digest, installed extension digest, exact Python distributions, shared
library resolution, imports and the owned startup checks. The receipt at
`/opt/kicad-route.install.json` records `source: owned source build`, `rustc`,
`cargo`, the engine identity and extension SHA-256, and wheel provenance. There
is no runtime build or repair fallback. These records identify software inputs;
they do not establish physical board qualification.

`AGENT_EE_KICAD_ROUTE_CHECKOUT=/opt/kicad-route` is set in the image, and the
checkout is its own `KICAD_ROUTE_STATE`. The tree is owned by the runtime user
(UID 10001), as a native checkout is owned by its user. That lets git inside the
tool sandbox read the repository, which it refuses for another owner. The
routing adapter mounts the tree read-only in the networkless tool sandbox, and
the native tests check that writes there fail with `EROFS` and that the tree's
hashes are unchanged. See [routing adapter](routing-adapter.md).

Licences: kicad-route and KRT are MIT and their `LICENSE` files stay in the
tree. The wheels keep their licence files in their `dist-info` directories,
including notices for bundled native libraries. KRT's checkout also contains
third-party resources with their own notices, for example
`py_router/stage3d/vendor/three/LICENSE` and the example boards under `awx/`.
Building the image locally is distinct from publishing it; the image is not
published.

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
fails. The profile is managed by this helper, remains installed for later runs, and applies only to
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
3. Provisions the [required external inputs](#required-external-inputs) on the
   runner: an anonymous clone of the public fome-mc repository at the
   pinned commit, verified before use, and the J1 manufacturer drawing, checked
   against its pinned SHA-256. A mismatch or failed download fails the job.
4. Runs every test module in the image with `AGENT_EE_REQUIRE_TOOLCHAIN=1`
   through [scripts/strict_suite.py](../scripts/strict_suite.py), four modules at
   a time, with both inputs mounted read-only. Native 3D board render tests run
   when renderer-related files or the pinned KiCad image inputs changed.
   GitHub's preceding push commit is the
   comparison base; manual dispatch, an unknown base, or a dirty checkout runs
   them. Every selected test must pass without a skip, and every module must
   produce a valid result. The job summary gives the module and test counts,
   names render tests omitted by change selection, and names tests that did not
   execute because a [required external input](#required-external-inputs) is absent.
5. Runs `scripts/check_docs.py` in the image. This step, the conflict-marker
   check and step 6 also run after a failed suite step, so that their own results
   are reported. Any failed step still fails the job.
6. Runs checks-only reviews of the RC control and seeded fixtures directly in
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

The shared test harness defaults incidental candidate imaging off with
`AGENT_EE_SKIP_VISUALS=1`. The candidate-visual and 3D-render modules explicitly
restore imaging for their tests, including native image/hash checks. Native 3D
render tests have explicit markers; the strict runner excludes them before
execution only when the change selector returns `omit`. This test setting does
not change production defaults or turn any selected unittest into a skip. See
[candidate inspection images](pcb-renders.md#candidate-inspection-images-agentee-382).

For an explicit full render regression run, use
`scripts/strict_suite.py --render-mode run` with the same strict toolchain and external inputs. A local
change-selected run can use `--render-change-base origin/main`; a missing base
runs the render tests. Candidate-bound milestone gallery rendering remains a
separate workflow and is not selected by this regression-test gate.

`AGENT_EE_REQUIRE_TOOLCHAIN=1` is the strict-toolchain contract: toolchain tests
must fail rather than skip when KiCad, ngspice, Bubblewrap, a usable user
namespace, the installed kicad-happy or the pinned kicad-route is missing. CI also refuses any skip on its own, so a skip cannot pass
unnoticed whatever the test suite's skip behavior.

### Required external inputs

A few tests read pinned objects from the owner's fome-mc Git object store
(`AGENT_EE_FOME_MC_CHECKOUT`, default `~/git/fome-mc`). They are recapture and
reparse regressions over fome-mc design files that this repository cites but
does not republish, and they use `fome_mc_store` in
[tests/toolchain.py](../tests/toolchain.py). This is the `fome-mc-store`
partition. Each test names the commit it reads (`df304f3`, `e7f5ae3`,
`c582b29`). The two catalog tests that read the store as a checkout
(`test_generate`, `test_rung1_qualified`) used to read whatever the store's HEAD
was. They now read `FOME_MC_COMMIT` (`1289f31`) through `fome_mc_checkout`, a
scratch clone that shares the store's objects. The store itself is only read.

[test_pod_j1_courtyard.py](../tests/test_pod_j1_courtyard.py) uses committed
fixtures and needs only the public TE drawing of the AMPSEAL connector, which
this repository does not republish. Point `AGENT_EE_J1_DRAWING` at the exact
PDF, as described in the
[J1 fixture README](../fixtures/telemetry-pod/j1/README.md). A missing or wrong
drawing fails these tests in every mode. `AGENT_EE_POD_INPUTS` is an optional
complete override for a host with the captured inputs; CI leaves it unset.

These tests are required. Without the fome-mc store, a test skips by default.
Under `AGENT_EE_REQUIRE_TOOLCHAIN=1` it always fails, with a message naming the
partition; no environment setting turns it into a skip or a pass. The strict
runner reports each such failure as a required test not executed, by partition,
in an error annotation and in the job summary. The run still fails. A present
store whose pinned file does not verify also fails. The J1 tests fail directly,
with their loader's message, and the strict runner reports them as ordinary
errors. `fome-mc-store` is the only partition; the former `pod-retained-inputs`
partition was removed once the J1 tests read `AGENT_EE_J1_DRAWING` directly,
and `unavailable` refuses it as unknown. The strict runner also refuses an
expected failure (`unittest.expectedFailure`), which unittest itself counts as
a success.

Project CI provisions both inputs on the runner, outside the image build, the
test processes and every tool sandbox. The owner's delegate authorized this
on 2026-10-19.

- **fome-mc store.** A plain `git clone --no-checkout` fetches the public
  repository `https://github.com/halbritt/fome-mc` anonymously, with full
  history and every blob, and `git checkout --detach` then checks out
  `1289f3149d2bcaa83a3b490a0bfbd11ed8a17589` (`FOME_MC_COMMIT`, equal to the
  constant in `tests/toolchain.py`). The step sends no Authorization header: it
  uses no token, clears the credential helper and `http.extraheader`, and reads
  no global or system Git configuration (`GIT_CONFIG_GLOBAL=/dev/null`,
  `GIT_CONFIG_NOSYSTEM=1`). `GIT_TERMINAL_PROMPT=0` makes a missing or private
  repository fail rather than prompt. `actions/checkout` is not used here
  because it cannot run with an empty token. The clone is not partial: the
  suite containers have no network, so a lazily fetched blob would fail. No
  submodules or LFS objects are fetched. The checkout lives in
  `build/external/fome-mc`, which `.gitignore` and `.dockerignore` exclude. A
  following step fails the job unless `HEAD` is exactly the pinned commit and
  no credential is configured in the checkout. Every commit the tests pin
  (`df304f3`, `e7f5ae3`, `c582b29`) is an ancestor of `1289f31`.
- **J1 drawing.** A step downloads `https://www.farnell.com/cad/2916153.pdf`
  with `curl --fail --proto '=https' --retry 2 --max-time 90 --max-filesize
  10000000` into the runner's temporary directory, outside the checkout, and checks SHA-256
  `14bd5c3bb2ca16bfcf01918b678eab1aa19df10001c17735dd1f11356a6e4fe8` (the
  drawing hash in `j1-placement-exclusion.json`) with `sha256sum --check
  --strict`. A transport error, an oversized body or any other hash fails the
  job. The distributor stalls requests that carry curl's default User-Agent
  (0 bytes until the time limit, reproduced 2026-10-19), so the step sends a
  browser-style User-Agent and no other header or credential. The PDF is never
  committed or uploaded.

The strict suite container receives the store at `/opt/fome-mc` and the drawing
at `/opt/j1/te-776228-1.pdf`, both read-only, with `AGENT_EE_FOME_MC_CHECKOUT`
and `AGENT_EE_J1_DRAWING` set. It still runs `--network none`. After the
`HEAD` check, a step changes the store's owner to the image user's UID and GID,
read from the image. Git refuses a repository owned by another user, including
inside the tool sandbox, whose environment is reset. A native host has the same
arrangement: the user who reads the store owns it. No Git trust setting is
overridden.
Nothing from fome-mc or the drawing is copied into the image, the repository or
an artifact. [test_strict_suite.py](../tests/test_strict_suite.py) checks that
the workflow's pins equal the tests' pins and that the suite step mounts both
inputs.

Without the provisioning steps, the same image fails the strict gate, naming the
fome-mc tests as not executed and reporting the J1 tests as errors. Hosts that
run the full gate set `AGENT_EE_FOME_MC_CHECKOUT` and `AGENT_EE_J1_DRAWING`
themselves. A run with any required test not executed does not satisfy the
required full gate.

### Hosted CI restore (2026-10-18)

The hosted runs failed for four reasons, each reproduced in the locally built
image with `AGENT_EE_FOME_MC_CHECKOUT` unset:

- Tests that needed the fome-mc store failed or errored (`test_pod_ampseal`,
  `test_pod_can`, `test_generate`, `test_rung1_qualified`) or skipped in strict
  mode (`test_block_inventory`, `test_fab_roundtrip`, `test_rule_severities`).
  They now share the fail-closed `fome-mc-store` partition above. They fail
  under strict mode, never skip.
- `test_scaffold`'s campaign test needed a host Codex installation for the
  real launch preflight, although its inference is scripted and the executable
  never runs. The test now supplies its own native stand-in through
  `AGENT_EE_CODEX_BINARY`; fingerprinting and preflight remain real.
- `test_pod_j1_courtyard` (added at `485ed56`, after the last analysed hosted
  run) read its retained captures from fixed host `/tmp` directories and failed
  everywhere else. agent-463's portability fix (`25a9737`) replaced them with
  committed fixtures and the `AGENT_EE_J1_DRAWING` prerequisite above; 16c's
  interim `pod-retained-inputs` guard for these tests was dropped in the merge,
  and the partition itself in 16e.
- `test_tree_hygiene` ran `git ls-files` in `/opt/agent-ee`, which has no `.git`
  because the build context excludes it. Without `.git` it now scans every file
  of the copied tree; a checkout whose Git fails still fails. The host-side
  `check_conflict_markers.sh` step still checks the Git tree.
  The module also refuses merge leftovers by file name (`.orig`, `.rej`, and
  git mergetool's `BASE`, `LOCAL`, `REMOTE` and `BACKUP` copies) after a
  composition merge committed stray `.orig` files (AGENTEE-377).

The serial suite also took 53 of the job's 60 minutes. The runner now runs
modules in parallel, each in its own process and temporary directory, and the
job limit is 90 minutes.

The workflow file has been checked locally for YAML structure, and its image,
test, docs and fixture commands have been run locally in the built image. The
fixture step was replayed locally with GitHub's `bash -eo pipefail` semantics:
the control gave `checks_passed`, the seeded run gave `check_failed` with
`rc-step` `defect` and ERC, DRC and geometry `pass`, `agent-ee verify` exited 0
for both, and the step failed when a run directory already existed or an
expectation did not match. The first hosted run built the image but failed at the Bubblewrap host-policy
boundary described above. The named-profile correction then passed on the
hosted runner at `c0ddba2`, as recorded at the top of this page.

## Reusable action

Consumer KiCad projects can call [action.yml](../action.yml) from pull requests
or any other trigger. agent-ee's own development does not use pull requests.

| Input | Default | Meaning |
| --- | --- | --- |
| `scope` | required | Scope JSON inside the workspace |
| `output-directory` | `agent-ee-run` | New run directory inside the workspace; an existing directory, control characters, `*`, `?`, `[`, `\` and trailing whitespace are refused because they would change which directory is judged or uploaded |
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
but decide whether that is acceptable for untrusted contributions. A pull
request can also edit the scope and requirements it is judged against, so the
gate checks a contribution only against criteria the consumer controls when the
scope comes from a trusted revision.

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

## Stock 3D models

The KiCad apt install includes `kicad-packages3d=${KICAD_VERSION}` alongside
symbols, footprints and templates. The exact package pin uses the existing
signed package source and fails the build if unavailable. The library adds
approximately 280 MB of downloads and 3.3 GB unpacked (package estimates).
[Candidate model coverage](pcb-renders.md#proposal-project-model-closure-agentee-394)
records the actual referenced model file hashes; runtime rendering is offline.

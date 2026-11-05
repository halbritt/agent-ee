# agent-ee native-check image: the newest KiCad 10.0 release and its libraries when
# the image is built, corrected ngspice, Bubblewrap, Python 3.12, the
# kicad-route/KRT router at the head of its main branch when the image is built, and
# this checkout's source launcher.
# Versions, the KiCad update path and required runtime privileges: docs/container.md.
#
# KiCad and base tools are not held at one fixed version: install records (/opt/*.install.json)
# capture the releases the build installed, and every run with the image uses and records
# those releases. Base tools are resolved from Ubuntu and live PPAs; failure stops `apt-get install`,
# and verification steps test meaning, shape, and platform floors.

ARG UBUNTU_BASE=ubuntu:24.04
FROM ${UBUNTU_BASE} AS native-base

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1

# Launchpad PPA for KiCad, fingerprint FDA854F61C4D0D9572BB95E5245D5502FAD7A805
# (matches the PPA's published signing_key_fingerprint).
COPY --chmod=755 docker/apt-retry.sh /usr/local/bin/apt-retry
COPY docker/kicad-10.0-releases.asc /usr/share/keyrings/kicad-10.0-releases.asc
# KiCad and its symbol, footprint, 3D-model and template libraries are the newest
# 10.0 release in the live KiCad PPA. The PPA's release file is this stage's cache
# key: a rebuild after a KiCad release installs it, and an unchanged PPA leaves the
# later layers cached. Updating KiCad means rebuilding the image.
# docker/kicad/install.py records the release, package versions, PPA release file,
# library tag commits and binary digests in /opt/kicad.install.json, and fails the
# build unless kicad-cli and pcbnew name one 10.x release, pcbnew loads an owned board
# and kicad-cli DRC reports on it.
ADD https://ppa.launchpadcontent.net/kicad/kicad-10.0-releases/ubuntu/dists/noble/InRelease /usr/local/share/agent-ee/kicad/ppa-InRelease
COPY docker/kicad/install.py src/agent_ee/kicad_install.py /usr/local/share/agent-ee/kicad/
COPY fixtures/dc-path/straight.kicad_pcb /usr/local/share/agent-ee/kicad/probe/board.kicad_pcb
COPY fixtures/datasheet-pins/cid-font-probe.pdf /usr/local/share/agent-ee/probes/cid-font-probe.pdf

RUN set -eu; \
    rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*; \
    printf '%s\n' 'Types: deb' 'URIs: http://archive.ubuntu.com/ubuntu' 'Suites: noble' \
        'Components: main' 'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \
        > /etc/apt/sources.list.d/bootstrap.sources; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 -o APT::Update::Error-Mode=any update; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 install -y --no-install-recommends ca-certificates; \
    rm /etc/apt/sources.list.d/bootstrap.sources; \
    printf '%s\n' \
        'Types: deb' 'URIs: http://archive.ubuntu.com/ubuntu' \
        'Suites: noble noble-updates noble-security' 'Components: main universe' \
        'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' '' \
        'Types: deb' \
        'URIs: https://ppa.launchpadcontent.net/kicad/kicad-10.0-releases/ubuntu' \
        'Suites: noble' 'Components: main' \
        'Signed-By: /usr/share/keyrings/kicad-10.0-releases.asc' \
        > /etc/apt/sources.list.d/agent-ee-snapshot.sources; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 -o APT::Update::Error-Mode=any update; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 install -y --no-install-recommends \
        python3 \
        kicad kicad-symbols kicad-footprints kicad-packages3d kicad-templates \
        libfftw3-double3 libgomp1 \
        bubblewrap \
        poppler-utils \
        poppler-data \
        ripgrep \
        git; \
    /usr/bin/python3 /usr/local/share/agent-ee/kicad/install.py /opt/kicad.install.json; \
    rm -rf /var/lib/apt/lists/*

# Refuse an image whose base tools do not meet platform floors, or whose
# stock libraries / pcbnew bindings are missing from the system interpreter.
RUN set -eu; \
    bwrap --version | grep -q -E '^bubblewrap [0-9]+\.[0-9]+'; \
    /usr/bin/python3 -c 'import sys; assert sys.version_info[:2] >= (3, 12), sys.version'; \
    /usr/bin/python3 -c 'import pcbnew' 2>/dev/null; \
    rg --version | grep -q '^ripgrep '; \
    ! pdftotext /usr/local/share/agent-ee/probes/cid-font-probe.pdf - 2>&1 | grep -q 'Missing language pack'; \
    pdftotext /usr/local/share/agent-ee/probes/cid-font-probe.pdf - | grep -q 'B2B-XH-A'; \
    pdftoppm -png -r 72 /usr/local/share/agent-ee/probes/cid-font-probe.pdf /tmp/probe-page; \
    test -f /tmp/probe-page-1.png && rm -f /tmp/probe-page*; \
    test -s /opt/kicad.install.json; \
    test ! -u /usr/bin/bwrap; \
    for f in symbols/Device.kicad_sym symbols/power.kicad_sym symbols/Connector_Generic.kicad_sym \
             footprints/Resistor_SMD.pretty footprints/Capacitor_SMD.pretty \
             footprints/Connector_PinHeader_2.54mm.pretty template/sym-lib-table template/fp-lib-table; do \
        test -e "/usr/share/kicad/$f"; \
    done

# ngspice 47 plus the generic inverse-pswitch correction. Build tools and the
# fetched source stay out of the runtime. Every image runs the numeric oracle.
FROM native-base AS ngspice-build
RUN set -eu; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 -o APT::Update::Error-Mode=any update; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 install -y --no-install-recommends \
        build-essential autoconf automake libtool bison flex pkg-config libfftw3-dev; \
    rm -rf /var/lib/apt/lists/*
COPY docker/ngspice /usr/local/share/agent-ee/ngspice
RUN /usr/local/share/agent-ee/ngspice/build.sh

FROM native-base AS native
COPY --from=ngspice-build /out/usr/ /usr/
COPY docker/ngspice /usr/local/share/agent-ee/ngspice
RUN --network=none set -eu; \
    ngspice --version | grep -q -E '^\*\* ngspice-[0-9]+ '; \
    python3 /usr/local/share/agent-ee/ngspice/verify.py

# The Rust toolchain is acquired from the slim bookworm image, only in the
# build stages. The final image receives native artifacts, never Cargo.
FROM rust:slim-bookworm AS rust-toolchain

FROM native AS kicad-route-build
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup
COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH
RUN set -eu; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 -o APT::Update::Error-Mode=any update; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 install -y --no-install-recommends python3-pip build-essential python3-dev; \
    rm -rf /var/lib/apt/lists/*
# kicad-route: the head of its main branch at build time, or the commit in this ARG.
# docker/kicad-route/install.sh resolves the commit, takes the newest wheels that meet
# the router's own requirements file, builds the owned engine and records the commit,
# its clean-tree state, the engine version, the Rust toolchain banners and every
# wheel's version and SHA-256 in /opt/kicad-route.install.json. The upstream ref
# listing is this stage's cache key: a rebuild after a kicad-route commit installs it.
# Every routing attempt also records the checkout it runs (commit, clean tree, code).
ARG KICAD_ROUTE_COMMIT=
ADD https://github.com/halbritt/kicad-route.git/info/refs?service=git-upload-pack /usr/local/share/agent-ee/kicad-route-upstream-refs
COPY docker/kicad-route /usr/local/share/agent-ee/kicad-route
RUN /usr/local/share/agent-ee/kicad-route/install.sh install

# kicad-happy analyzer: the newest release of the 2.x line at build time, with the
# AGENTEE-219 correction applied (docker/kicad-happy/install.py). The build fails
# if the correction no longer applies, or if a report on the owned probe projects
# fails the checks agent-ee applies when it reads one (kicad_happy_format.py) or
# loses the meaning those projects' fixture expectations require.
# /opt/kicad-happy.install.json records the tag, commit, output format and
# installed-file identity; every run records them with its analyzer evidence.
# Updating means rebuilding the image. The upstream ref listing is this stage's
# cache key: a rebuild after an upstream release installs it, and an unchanged
# release leaves the later layers cached.
FROM native AS kicad-happy
ADD https://github.com/aklofas/kicad-happy.git/info/refs?service=git-upload-pack /usr/local/share/agent-ee/kicad-happy/upstream-refs
COPY docker/kicad-happy /usr/local/share/agent-ee/kicad-happy
COPY src/agent_ee/kicad_happy_format.py src/agent_ee/data/kicad-happy-contract.json fixtures/kicad-happy/ac4-false-positives/correction.patch /usr/local/share/agent-ee/kicad-happy/
COPY fixtures/kicad-happy/severity-gate/warning /usr/local/share/agent-ee/kicad-happy/probe
COPY fixtures/kicad-happy/ac4-false-positives/reproducer.kicad_sch fixtures/kicad-happy/ac4-false-positives/reproducer.kicad_pro /usr/local/share/agent-ee/kicad-happy/probe/
COPY fixtures/kicad-happy/ac4-false-positives/expected.json fixtures/kicad-happy/severity-gate/dispositions.json /usr/local/share/agent-ee/kicad-happy/probe/
RUN /usr/bin/python3 /usr/local/share/agent-ee/kicad-happy/install.py /opt/kicad-happy

FROM native AS toolchain

# The installed analyzer stays outside the agent-ee package and candidate.
# Runtime analysis is networkless.
ENV AGENT_EE_KICAD_HAPPY=/opt/kicad-happy
COPY --from=kicad-happy /opt/kicad-happy /opt/kicad-happy
COPY --from=kicad-happy /opt/kicad-happy.install.json /opt/kicad-happy.install.json

# Test reference tool only: the fab-consistency zstd decoder is compared with
# frames from the zstd CLI, from the same release snapshot as the image's libzstd1.
# PID 1 of every container run from this image: reaps orphaned descendants
# (zombie bwrap under long strict_suite parents) and forwards signals and the
# exit status, without `docker run --init`, whose /usr/sbin/docker-init bind
# mount is a /usr submount that the stage-receipt refusal rightly rejects.
RUN set -eu; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 -o APT::Update::Error-Mode=any update; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 install -y --no-install-recommends zstd tini; \
    rm -f /usr/local/bin/apt-retry; \
    rm -rf /var/lib/apt/lists/*; \
    zstd --version | grep -q -E '(\*\*\* zstd|v[0-9]+\.[0-9]+)'; \
    test -x /usr/bin/tini; \
    /usr/bin/python3 -c 'import hashlib, json, subprocess; \
packages = dict(line.split("\t") for line in subprocess.check_output(["dpkg-query", "-W", "-f=${Package}\t${Version}\n", "bubblewrap", "python3", "poppler-utils", "poppler-data", "ripgrep", "zstd", "tini"]).decode().splitlines() if line); \
digests = {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in ["/usr/bin/bwrap", "/usr/bin/python3", "/usr/bin/pdftoppm", "/usr/bin/rg", "/usr/bin/zstd", "/usr/bin/tini"]}; \
record = {"schema": "agent-ee.base-install/1", "packages": packages, "binaries": digests}; \
open("/opt/base.install.json", "w").write(json.dumps(record, indent=2) + "\n")'

# kicad-route with its owned routing engine, as install.sh installed and recorded it.
# The checkout is its own KICAD_ROUTE_STATE and belongs to the runtime user, as on a native host;
# routing steps mount it read-only in the tool sandbox. The verification runs
# without network, so KRT's startup checks cannot fetch or rebuild anything.
ENV AGENT_EE_KICAD_ROUTE_CHECKOUT=/opt/kicad-route
COPY --from=kicad-route-build --chown=10001:10001 /opt/kicad-route /opt/kicad-route
COPY --from=kicad-route-build /opt/kicad-route.install.json /opt/kicad-route.install.json
COPY docker/kicad-route /usr/local/share/agent-ee/kicad-route
RUN --network=none /usr/local/share/agent-ee/kicad-route/install.sh verify

# Image context record: with Docker's /.dockerenv and the recorded runtime hashes,
# the seeded-benchmark closure verifier's positive evidence that it runs inside
# the image, where the outer-host docker CLI is absent (docs/seeded-benchmark.md).
COPY docker/image-context.json /usr/local/share/agent-ee/image-context.json

# The whole planner is built from this checkout, with the same fixed compiler
# as routing. Its source stage is outside the exported toolchain cache.
FROM kicad-route-build AS placement-engine-build
WORKDIR /build/agent-ee
COPY crates/placement-engine/Cargo.toml crates/placement-engine/Cargo.lock ./crates/placement-engine/
COPY crates/placement-engine/src ./crates/placement-engine/src
COPY src/agent_ee/data/jlcpcb-locality.json ./src/agent_ee/data/jlcpcb-locality.json
COPY scripts/build_placement_engine.py ./scripts/build_placement_engine.py
RUN python3 scripts/build_placement_engine.py fetch
RUN --network=none python3 scripts/build_placement_engine.py build \
    --output /usr/local/libexec/agent-ee/agent-ee-placement

# Source and retained fixtures are deliberately outside the exported CI cache.
FROM toolchain

# Source-launcher installation, as in docs/installation.md: the launcher uses
# /usr/bin/python3 (which imports pcbnew) and locates src/ from its checkout.
COPY . /opt/agent-ee
COPY --from=placement-engine-build /usr/local/libexec/agent-ee/ /usr/local/libexec/agent-ee/
ENV AGENT_EE_RUST_PLANNER=/usr/local/libexec/agent-ee/agent-ee-placement
RUN --network=none set -eu; \
    python3 /opt/agent-ee/scripts/build_placement_engine.py verify \
      --output /usr/local/libexec/agent-ee/agent-ee-placement; \
    ldd /usr/local/libexec/agent-ee/agent-ee-placement > /tmp/placement-engine-libraries.txt; \
    ! grep -q 'not found' /tmp/placement-engine-libraries.txt; \
    rm /tmp/placement-engine-libraries.txt
RUN set -eu; \
    ln -s /opt/agent-ee/bin/agent-ee /usr/local/bin/agent-ee; \
    useradd --create-home --uid 10001 --shell /bin/bash agentee
ENV AE_CONTAINER=1
RUN touch /etc/ae-ci-container

LABEL org.opencontainers.image.title="agent-ee" \
      org.opencontainers.image.description="agent-ee with the newest KiCad 10.0 release at build time (recorded in /opt/kicad.install.json), corrected ngspice 47, Bubblewrap 0.9.0, Python 3.12 and kicad-route main at build time (recorded in /opt/kicad-route.install.json)"

# Bubblewrap needs an unprivileged user namespace; run as a non-root user.
USER agentee
WORKDIR /home/agentee
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["agent-ee", "doctor", "--checks-only"]

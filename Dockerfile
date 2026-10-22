# agent-ee native-check image: the demonstrated KiCad 10.0.7 / ngspice 42 /
# Bubblewrap 0.9.0 / Python 3.12 toolchain, the pinned kicad-route/KRT router
# and this checkout's source launcher.
# Pin enforcement and required runtime privileges: docs/container.md.
#
# Every pin fails closed: an unavailable exact version stops `apt-get install`,
# and the verification step below refuses an image whose installed tools do not
# report the expected versions.

# Ubuntu 24.04 (noble) multi-architecture index digest, pulled 2026-10-17.
ARG UBUNTU_DIGEST=sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac
FROM ubuntu:24.04@${UBUNTU_DIGEST} AS native

# One snapshot timestamp freezes both the Ubuntu archive and the KiCad PPA, so
# the exact package versions below stay installable after newer uploads.
ARG APT_SNAPSHOT=20261015T000000Z
# Bootstrap TLS trust only from the immutable noble release pocket.
ARG CA_CERTIFICATES_VERSION=20240203
ARG KICAD_VERSION=10.0.7~ubuntu24.04.1
ARG NGSPICE_VERSION=42+ds-3build1
ARG BUBBLEWRAP_VERSION=0.9.0-1ubuntu0.3
ARG PYTHON3_VERSION=3.12.3-0ubuntu2.1
ARG POPPLER_VERSION=24.02.0-1ubuntu9.10

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1

# Launchpad PPA for KiCad, fingerprint FDA854F61C4D0D9572BB95E5245D5502FAD7A805
# (matches the PPA's published signing_key_fingerprint).
COPY --chmod=755 docker/apt-retry.sh /usr/local/bin/apt-retry
COPY docker/kicad-10.0-releases.asc /usr/share/keyrings/kicad-10.0-releases.asc

RUN set -eu; \
    rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*; \
    printf '%s\n' 'Types: deb' 'URIs: http://archive.ubuntu.com/ubuntu' 'Suites: noble' \
        'Components: main' 'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \
        > /etc/apt/sources.list.d/bootstrap.sources; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 -o APT::Update::Error-Mode=any update; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 install -y --no-install-recommends "ca-certificates=${CA_CERTIFICATES_VERSION}"; \
    rm /etc/apt/sources.list.d/bootstrap.sources; \
    printf '%s\n' \
        'Types: deb' "URIs: https://snapshot.ubuntu.com/ubuntu/${APT_SNAPSHOT}" \
        'Suites: noble noble-updates noble-security' 'Components: main universe' \
        'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' '' \
        'Types: deb' \
        "URIs: https://snapshot.ppa.launchpadcontent.net/kicad/kicad-10.0-releases/ubuntu/${APT_SNAPSHOT}" \
        'Suites: noble' 'Components: main' \
        'Signed-By: /usr/share/keyrings/kicad-10.0-releases.asc' \
        > /etc/apt/sources.list.d/agent-ee-snapshot.sources; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 -o APT::Update::Error-Mode=any update; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 install -y --no-install-recommends \
        "python3=${PYTHON3_VERSION}" \
        "kicad=${KICAD_VERSION}" \
        "kicad-symbols=${KICAD_VERSION}" \
        "kicad-footprints=${KICAD_VERSION}" \
        "kicad-packages3d=${KICAD_VERSION}" \
        "kicad-templates=${KICAD_VERSION}" \
        "ngspice=${NGSPICE_VERSION}" \
        "bubblewrap=${BUBBLEWRAP_VERSION}" \
        "poppler-utils=${POPPLER_VERSION}" \
        git; \
    apt-mark hold python3 kicad kicad-symbols kicad-footprints kicad-templates ngspice bubblewrap poppler-utils; \
    rm -rf /var/lib/apt/lists/*

# Refuse an image whose tools do not report the demonstrated versions, or whose
# stock libraries / pcbnew bindings are missing from the system interpreter.
RUN set -eu; \
    test "$(kicad-cli version)" = "10.0.7"; \
    ngspice --version | grep -q '^\*\* ngspice-42 '; \
    test "$(bwrap --version)" = "bubblewrap 0.9.0"; \
    /usr/bin/python3 -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version'; \
    /usr/bin/python3 -c 'import pcbnew; v = pcbnew.GetBuildVersion(); assert v.startswith("10.0.7"), v' 2>/dev/null; \
    test ! -u /usr/bin/bwrap; \
    for f in symbols/Device.kicad_sym symbols/power.kicad_sym symbols/Connector_Generic.kicad_sym \
             footprints/Resistor_SMD.pretty footprints/Capacitor_SMD.pretty \
             footprints/Connector_PinHeader_2.54mm.pretty template/sym-lib-table template/fp-lib-table; do \
        test -e "/usr/share/kicad/$f"; \
    done

# The exact Rust toolchain is acquired from a digest-pinned image, only in the
# build stage. The final image receives the installed extension, never Cargo.
FROM rust:1.99.1-slim-bookworm@sha256:a24e292b4af7bdee351a31d3a6379972a5652f0ea22a1b6d6883324ac4f5097c AS rust-toolchain

FROM native AS kicad-route-build
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup
COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH RUSTUP_TOOLCHAIN=1.99.1
ARG PYTHON3_PIP_VERSION=24.0+dfsg-1ubuntu1.3
RUN set -eu; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 -o APT::Update::Error-Mode=any update; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 install -y --no-install-recommends "python3-pip=${PYTHON3_PIP_VERSION}" build-essential python3-dev; \
    rm -rf /var/lib/apt/lists/*
COPY docker/kicad-route /usr/local/share/agent-ee/kicad-route
RUN /usr/local/share/agent-ee/kicad-route/install.sh install

FROM native AS toolchain

# Installed analyzer tool, kept outside the agent-ee package and candidate.
# Runtime analysis remains networkless; only the image build fetches this pin.
ARG KICAD_HAPPY_COMMIT=06840467ad0f5d76af45f64449b1f5ecb10f052f
ENV AGENT_EE_KICAD_HAPPY=/opt/kicad-happy
RUN set -eu; \
    git clone https://github.com/aklofas/kicad-happy.git /opt/kicad-happy; \
    git -C /opt/kicad-happy checkout --detach "${KICAD_HAPPY_COMMIT}"; \
    test "$(git -C /opt/kicad-happy rev-parse HEAD)" = "${KICAD_HAPPY_COMMIT}"; \
    /usr/bin/python3 -c 'import json; assert json.load(open("/opt/kicad-happy/.claude-plugin/plugin.json"))["version"] == "2.3.1"'; \
    rm -rf /opt/kicad-happy/.git

# Test reference tool only: the fab-consistency zstd decoder is compared with
# frames from the zstd CLI, from the same snapshot as the image's libzstd1.
ARG ZSTD_VERSION=1.5.5+dfsg2-2build1.1
RUN set -eu; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 -o APT::Update::Error-Mode=any update; \
    apt-retry apt-get -o Acquire::Retries=5 -o Acquire::Retries::Delay=true -o Acquire::Retries::Delay::Maximum=8 install -y --no-install-recommends "zstd=${ZSTD_VERSION}"; \
    apt-mark hold zstd; \
    rm -f /usr/local/bin/apt-retry; \
    rm -rf /var/lib/apt/lists/*; \
    zstd --version | grep -q 'v1\.5\.5'

# kicad-route b577449 with its owned routing engine for the routing stage. The checkout is
# its own KICAD_ROUTE_STATE and belongs to the runtime user, as on a native host;
# routing steps mount it read-only in the tool sandbox. The verification runs
# without network, so KRT's startup checks cannot fetch or rebuild anything.
ENV AGENT_EE_KICAD_ROUTE_CHECKOUT=/opt/kicad-route
COPY --from=kicad-route-build --chown=10001:10001 /opt/kicad-route /opt/kicad-route
COPY --from=kicad-route-build /opt/kicad-route.install.json /opt/kicad-route.install.json
COPY docker/kicad-route /usr/local/share/agent-ee/kicad-route
RUN --network=none /usr/local/share/agent-ee/kicad-route/install.sh verify

# Image context record: with Docker's /.dockerenv and the pinned runtime hashes,
# the seeded-benchmark closure verifier's positive evidence that it runs inside
# the image, where the outer-host docker CLI is absent (docs/seeded-benchmark.md).
COPY docker/image-context.json /usr/local/share/agent-ee/image-context.json

# Source and retained fixtures are deliberately outside the exported CI cache.
FROM toolchain

# Source-launcher installation, as in docs/installation.md: the launcher uses
# /usr/bin/python3 (which imports pcbnew) and locates src/ from its checkout.
COPY . /opt/agent-ee
RUN set -eu; \
    ln -s /opt/agent-ee/bin/agent-ee /usr/local/bin/agent-ee; \
    useradd --create-home --uid 10001 --shell /bin/bash agentee

LABEL org.opencontainers.image.title="agent-ee" \
      org.opencontainers.image.description="agent-ee with KiCad 10.0.7, ngspice 42, Bubblewrap 0.9.0, Python 3.12 and kicad-route owned engine 0.23.1"

# Bubblewrap needs an unprivileged user namespace; run as a non-root user.
USER agentee
WORKDIR /home/agentee
CMD ["agent-ee", "doctor", "--checks-only"]

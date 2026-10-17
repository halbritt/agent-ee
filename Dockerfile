# agent-ee native-check image: the demonstrated KiCad 10.0.7 / ngspice 42 /
# Bubblewrap 0.9.0 / Python 3.12 toolchain plus this checkout's source launcher.
# Pin enforcement and required runtime privileges: docs/container.md.
#
# Every pin fails closed: an unavailable exact version stops `apt-get install`,
# and the verification step below refuses an image whose installed tools do not
# report the expected versions.

# Ubuntu 24.04 (noble) multi-architecture index digest, pulled 2026-10-17.
ARG UBUNTU_DIGEST=sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac
FROM ubuntu:24.04@${UBUNTU_DIGEST}

# One snapshot timestamp freezes both the Ubuntu archive and the KiCad PPA, so
# the exact package versions below stay installable after newer uploads.
ARG APT_SNAPSHOT=20261015T000000Z
# Bootstrap TLS trust only from the immutable noble release pocket.
ARG CA_CERTIFICATES_VERSION=20240203
ARG KICAD_VERSION=10.0.7~ubuntu24.04.1
ARG NGSPICE_VERSION=42+ds-3build1
ARG BUBBLEWRAP_VERSION=0.9.0-1ubuntu0.3
ARG PYTHON3_VERSION=3.12.3-0ubuntu2.1

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1

# Launchpad PPA for KiCad, fingerprint FDA854F61C4D0D9572BB95E5245D5502FAD7A805
# (matches the PPA's published signing_key_fingerprint).
COPY docker/kicad-10.0-releases.asc /usr/share/keyrings/kicad-10.0-releases.asc

RUN set -eu; \
    rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*; \
    printf '%s\n' 'Types: deb' 'URIs: http://archive.ubuntu.com/ubuntu' 'Suites: noble' \
        'Components: main' 'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \
        > /etc/apt/sources.list.d/bootstrap.sources; \
    apt-get update; \
    apt-get install -y --no-install-recommends "ca-certificates=${CA_CERTIFICATES_VERSION}"; \
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
    apt-get update; \
    apt-get install -y --no-install-recommends \
        "python3=${PYTHON3_VERSION}" \
        "kicad=${KICAD_VERSION}" \
        "kicad-symbols=${KICAD_VERSION}" \
        "kicad-footprints=${KICAD_VERSION}" \
        "kicad-templates=${KICAD_VERSION}" \
        "ngspice=${NGSPICE_VERSION}" \
        "bubblewrap=${BUBBLEWRAP_VERSION}" \
        git; \
    apt-mark hold python3 kicad kicad-symbols kicad-footprints kicad-templates ngspice bubblewrap; \
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

# Source-launcher installation, as in docs/installation.md: the launcher uses
# /usr/bin/python3 (which imports pcbnew) and locates src/ from its checkout.
COPY . /opt/agent-ee
RUN set -eu; \
    ln -s /opt/agent-ee/bin/agent-ee /usr/local/bin/agent-ee; \
    useradd --create-home --uid 10001 --shell /bin/bash agentee

LABEL org.opencontainers.image.title="agent-ee" \
      org.opencontainers.image.description="agent-ee with KiCad 10.0.7, ngspice 42, Bubblewrap 0.9.0 and Python 3.12"

# Bubblewrap needs an unprivileged user namespace; run as a non-root user.
USER agentee
WORKDIR /home/agentee
CMD ["agent-ee", "doctor", "--checks-only"]

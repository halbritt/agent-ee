#!/usr/bin/env bash
# Pinned kicad-route + KRT installation for the agent-ee image (docs/container.md).
#
#   install.sh install   build stage: fetch the pinned sources, the hash-checked
#                        prebuilt Rust router and the hash-locked wheels, then
#                        write the installation manifest. Needs network.
#   install.sh verify    final image: re-check every pin offline and run KRT's
#                        own startup checks against the installed bytes.
#
# Every pin fails closed. Nothing resolves "latest": kicad-route's own `update`
# and KRT's build_router.py installer are never called, because both query the
# newest release and the latter can fall back to a cargo build.
set -euo pipefail

ROOT=/opt/kicad-route
MANIFEST=/opt/kicad-route.install.json
LOCK=$(dirname -- "$(readlink -f -- "$0")")/krt-requirements.txt

KICAD_ROUTE_URL=https://github.com/halbritt/kicad-route.git
KICAD_ROUTE_COMMIT=08179cdc97d08864da127f14d53ac46cf5a27851
KRT_URL=https://github.com/drandyhaas/KiCadRoutingTools.git
KRT_TAG=v0.23.1
KRT_COMMIT=295f9ee197517e8323726853361eb137cc08ba9e
KRT_REQUIREMENTS_SHA256=139aba6f659752d19d8d7c467866bc78a6ced432b7d14b3e45bbfe24a7753e43
# The release asset KRT's installer selects on linux x86-64. Only that
# architecture has been qualified; others need their own hash and native run.
ROUTER_ARCH=amd64
ROUTER_URL=https://github.com/drandyhaas/KiCadRoutingTools/releases/download/$KRT_TAG/grid_router-linux-x86_64.so
ROUTER_SHA256=d42aa6c682e6ab42f82d2241cf5b2a333effa4ad0b58549b1305b4503ce6bf79
PYTHON=/usr/bin/python3
VENV=$ROOT/.venv-krt
KRT=$ROOT/vendor/krt
# name==version pairs the venv must hold, nothing more.
WHEELS='numpy==2.5.4 pillow==13.0.0 scipy==1.18.1 shapely==2.2.0'

fail() { echo "kicad-route install: $*" >&2; exit 1; }

check_arch() {
    local arch
    arch=$(dpkg --print-architecture)
    [ "$arch" = "$ROUTER_ARCH" ] ||
        fail "unsupported architecture $arch: only $ROUTER_ARCH has a qualified grid_router hash"
}

# A detached checkout of one exact commit, keeping real git metadata: the agent-ee
# preflight reads HEAD and kicad-route reports KRT's commit and describe output.
fetch_commit() {
    local url=$1 dir=$2 commit=$3 tag=${4:-}
    git init -q "$dir"
    git -C "$dir" remote add origin "$url"
    if [ -n "$tag" ]; then
        git -C "$dir" fetch -q --depth 1 origin "refs/tags/$tag:refs/tags/$tag"
        [ "$(git -C "$dir" rev-parse "refs/tags/$tag^{commit}")" = "$commit" ] ||
            fail "$url tag $tag is not $commit"
    else
        git -C "$dir" fetch -q --depth 1 origin "$commit"
    fi
    git -C "$dir" -c advice.detachedHead=false checkout -q --detach "$commit"
    [ "$(git -C "$dir" rev-parse HEAD)" = "$commit" ] || fail "$dir HEAD is not $commit"
}

sha256_of() { sha256sum "$1" | cut -d' ' -f1; }

venv_versions() {
    "$VENV/bin/python" -I -B -c '
import importlib.metadata as m
print(" ".join(sorted(d.metadata["Name"].lower() + "==" + d.version for d in m.distributions())))'
}

install_tool() {
    check_arch
    [ ! -e "$ROOT" ] || fail "$ROOT already exists"
    fetch_commit "$KICAD_ROUTE_URL" "$ROOT" "$KICAD_ROUTE_COMMIT"
    mkdir -p "$ROOT/vendor"
    fetch_commit "$KRT_URL" "$KRT" "$KRT_COMMIT" "$KRT_TAG"
    [ "$(sha256_of "$KRT/requirements.txt")" = "$KRT_REQUIREMENTS_SHA256" ] ||
        fail "KRT requirements.txt changed; review the wheel lock before updating its hash"

    local tmp
    tmp=$(mktemp -d)
    curl -fsSL --proto '=https' -o "$tmp/grid_router.so" "$ROUTER_URL"
    [ "$(sha256_of "$tmp/grid_router.so")" = "$ROUTER_SHA256" ] || fail "grid_router asset hash mismatch"
    install -m 0644 "$tmp/grid_router.so" "$KRT/rust_router/grid_router.so"

    # The venv uses the image's own interpreter; its wheels come only from the lock.
    "$PYTHON" -m venv --without-pip "$VENV"
    "$PYTHON" -m pip download --isolated --no-cache-dir --disable-pip-version-check \
        --index-url https://pypi.org/simple --only-binary=:all: --no-deps --require-hashes \
        -r "$LOCK" -d "$tmp/wheels"
    "$PYTHON" -m pip --python "$VENV/bin/python" install --isolated --no-cache-dir \
        --disable-pip-version-check --no-index --find-links "$tmp/wheels" --only-binary=:all: \
        --no-deps --require-hashes --no-compile -r "$LOCK"
    [ "$(venv_versions)" = "$WHEELS" ] || fail "venv holds $(venv_versions), expected $WHEELS"

    "$PYTHON" -I -B - "$tmp/wheels" "$LOCK" > "$MANIFEST" <<PY
import hashlib, importlib.metadata, json, platform, subprocess, sys
from pathlib import Path
wheels, lock = Path(sys.argv[1]), Path(sys.argv[2])
venv = '$VENV'
meta = json.loads(subprocess.run([venv + '/bin/python', '-I', '-B', '-c', (
    'import importlib.metadata as m, json; print(json.dumps({d.metadata["Name"].lower(): '
    '{"version": d.version, "license": d.metadata.get("License-Expression") '
    'or ((d.metadata.get("License") or "").strip().splitlines() or [""])[0][:200] or None, '
    '"license_files": sorted(d.metadata.get_all("License-File") or [])} '
    'for d in m.distributions()}))')], check=True, capture_output=True, text=True).stdout)
files = []
for path in sorted(wheels.iterdir()):
    name = path.name.split('-')[0].lower()
    files.append({**meta[name], 'name': name, 'file': path.name,
                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
json.dump({
    'schema': 'agent-ee.kicad-route-install/1',
    'kicad_route': {'url': '$KICAD_ROUTE_URL', 'commit': '$KICAD_ROUTE_COMMIT', 'path': '$ROOT',
                    'license': 'MIT'},
    'krt': {'url': '$KRT_URL', 'tag': '$KRT_TAG', 'commit': '$KRT_COMMIT', 'path': '$KRT',
            'license': 'MIT', 'requirements_sha256': '$KRT_REQUIREMENTS_SHA256'},
    'grid_router': {'url': '$ROUTER_URL', 'sha256': '$ROUTER_SHA256', 'source': 'release prebuilt'},
    'python': {'executable': '$PYTHON', 'version': platform.python_version(), 'venv': venv},
    'wheel_lock_sha256': hashlib.sha256(lock.read_bytes()).hexdigest(),
    'wheels': files,
    'architecture': '$ROUTER_ARCH',
}, sys.stdout, indent=2, sort_keys=True)
print()
PY
    rm -rf "$tmp"
}

verify_tool() {
    check_arch
    # The tree belongs to the runtime user; root may read it during the build.
    local git=(git -c safe.directory='*')
    [ "$("${git[@]}" -C "$ROOT" rev-parse HEAD)" = "$KICAD_ROUTE_COMMIT" ] || fail "kicad-route HEAD moved"
    [ "$("${git[@]}" -C "$KRT" rev-parse HEAD)" = "$KRT_COMMIT" ] || fail "KRT HEAD moved"
    [ -z "$(GIT_OPTIONAL_LOCKS=0 "${git[@]}" -C "$ROOT" status --porcelain)" ] || fail "kicad-route checkout is dirty"
    [ -z "$(GIT_OPTIONAL_LOCKS=0 "${git[@]}" -C "$KRT" status --porcelain)" ] || fail "KRT checkout is dirty"
    [ "$(sha256_of "$KRT/rust_router/grid_router.so")" = "$ROUTER_SHA256" ] || fail "grid_router hash changed"
    [ "$(venv_versions)" = "$WHEELS" ] || fail "venv holds $(venv_versions), expected $WHEELS"
    # Every native library resolves inside this image, not only on the build host.
    local so libs
    while IFS= read -r -d '' so; do
        libs=$(ldd "$so") || fail "ldd cannot load $so"
        ! grep 'not found' <<<"$libs" || fail "unresolved shared library in $so"
    done < <(find "$VENV" "$KRT/rust_router" -name '*.so' -type f -print0)
    (cd "$KRT" && "$VENV/bin/python" -I -B -c '
import sys
sys.path.insert(0, "rust_router")
import numpy, scipy, shapely, PIL, grid_router
from scipy.optimize import linear_sum_assignment
from shapely.geometry import Polygon
from PIL import Image
assert grid_router.__version__ == "0.23.1", grid_router.__version__
print("grid_router", grid_router.__version__, "numpy", numpy.__version__, "scipy", scipy.__version__,
      "shapely", shapely.__version__, "pillow", PIL.__version__)')
    (cd "$KRT" && "$VENV/bin/python" -B py_router/startup_checks.py)
    [ "$(sha256_of "$KRT/rust_router/grid_router.so")" = "$ROUTER_SHA256" ] || fail "startup checks replaced grid_router"
    [ -z "$(GIT_OPTIONAL_LOCKS=0 "${git[@]}" -C "$KRT" status --porcelain)" ] || fail "startup checks dirtied KRT"
    "$PYTHON" -I -B -c 'import json, sys; m = json.load(open(sys.argv[1])); assert m["krt"]["commit"] == sys.argv[2]' \
        "$MANIFEST" "$KRT_COMMIT"
}

case ${1:-} in
    install) install_tool ;;
    verify) verify_tool ;;
    *) fail "usage: install.sh install|verify" ;;
esac

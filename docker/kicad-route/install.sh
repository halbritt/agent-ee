#!/usr/bin/env bash
# Pinned owned routing engine, built with Cargo --locked in the build stage.
# verify uses only installed bytes and the state manifest, without network.
set -euo pipefail

ROOT=/opt/kicad-route
MANIFEST=/opt/kicad-route.install.json
LOCK=$(dirname -- "$(readlink -f -- "$0")")/krt-requirements.txt

KICAD_ROUTE_URL=https://github.com/halbritt/kicad-route.git
KICAD_ROUTE_COMMIT=1c895b67dd6ca36620c0b5f757d6402e9692bf78
RUSTC_VERSION='rustc 1.99.1 (58cf8f9c3 2026-10-13)'
CARGO_VERSION='cargo 1.99.1 (5f94df478 2026-08-27)'
ROUTER_ARCH=amd64
PYTHON=/usr/bin/python3
VENV=$ROOT/.venv-krt
KRT=$ROOT/src/kicad_route/router
# name==version pairs the venv must hold, nothing more.
WHEELS='numpy==2.5.4 pillow==13.0.0 scipy==1.18.1 shapely==2.2.0'

fail() { echo "kicad-route install: $*" >&2; exit 1; }

check_arch() {
    local arch
    arch=$(dpkg --print-architecture)
    [ "$arch" = "$ROUTER_ARCH" ] ||
        fail "unsupported architecture $arch: only $ROUTER_ARCH has a qualified source build"
}

# A detached checkout of one exact commit, keeping real git metadata: the agent-ee
# preflight reads HEAD and verifies the included engine blobs.
fetch_commit() {
    local url=$1 dir=$2 commit=$3
    git init -q "$dir"
    git -C "$dir" remote add origin "$url"
    git -C "$dir" fetch -q --depth 1 origin "$commit"
    git -C "$dir" -c advice.detachedHead=false checkout -q --detach "$commit"
    [ "$(git -C "$dir" rev-parse HEAD)" = "$commit" ] || fail "$dir HEAD is not $commit"
}

venv_versions() {
    "$VENV/bin/python" -I -B -X pycache_prefix=/dev/null -c '
import importlib.metadata as m
print(" ".join(sorted(d.metadata["Name"].lower() + "==" + d.version for d in m.distributions())))'
}

install_tool() {
    check_arch
    [ ! -e "$ROOT" ] || fail "$ROOT already exists"
    fetch_commit "$KICAD_ROUTE_URL" "$ROOT" "$KICAD_ROUTE_COMMIT"
    [ "$(rustc --version)" = "$RUSTC_VERSION" ] || fail "wrong rustc version"
    [ "$(cargo --version)" = "$CARGO_VERSION" ] || fail "wrong cargo version"
    local tmp
    tmp=$(mktemp -d)
    # The venv uses the image's own interpreter; its wheels come only from the lock.
    "$PYTHON" -m venv --without-pip "$VENV"
    "$PYTHON" -m pip download --isolated --no-cache-dir --disable-pip-version-check \
        --index-url https://pypi.org/simple --only-binary=:all: --no-deps --require-hashes \
        -r "$LOCK" -d "$tmp/wheels"
    "$PYTHON" -m pip --python "$VENV/bin/python" install --isolated --no-cache-dir \
        --disable-pip-version-check --no-index --find-links "$tmp/wheels" --only-binary=:all: \
        --no-deps --require-hashes --no-compile -r "$LOCK"
    [ "$(venv_versions)" = "$WHEELS" ] || fail "venv holds $(venv_versions), expected $WHEELS"

    # The upstream installer builds exactly the included locked crate. There is
    # no external KRT checkout, release lookup or prebuilt binary fallback.
    "$VENV/bin/python" -I -B -X pycache_prefix=/dev/null "$KRT/build_router.py" --target-dir "$tmp/router-native"
    KICAD_ROUTE_STATE="$ROOT" "$ROOT/bin/kicad-route" state-manifest
    "$PYTHON" -I -B -X pycache_prefix=/dev/null - "$tmp/wheels" "$LOCK" > "$MANIFEST" <<PY
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
    'krt': json.load(open('$ROOT/manifest.json'))['krt'],
    'grid_router': {'sha256': json.load(open('$ROOT/manifest.json'))['krt']['extension_module']['sha256'],
                    'source': 'owned source build'},
    'rustc': '$RUSTC_VERSION', 'cargo': '$CARGO_VERSION',
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
    [ -z "$(GIT_OPTIONAL_LOCKS=0 "${git[@]}" -C "$ROOT" status --porcelain)" ] || fail "kicad-route checkout is dirty"
    "$PYTHON" -I -B -X pycache_prefix=/dev/null - "$ROOT" "$MANIFEST" "$LOCK" <<'PYVERIFY'
import hashlib, json, sys
from pathlib import Path
root, receipt = Path(sys.argv[1]), json.load(open(sys.argv[2]))
sys.path.insert(0, str(root / 'src'))
from kicad_route import engine, statemanifest
m = statemanifest.read(root)
assert not statemanifest.schema_problems(m)
assert not statemanifest.verify(m, root)
assert m['krt']['source_sha256'] == engine.source_sha256()
module = statemanifest.extension_module(root / '.venv-krt')
assert module is not None
assert m['krt']['extension_module']['path'] == str(module.relative_to(root))
assert m['krt']['extension_module']['sha256'] == hashlib.sha256(module.read_bytes()).hexdigest()
assert receipt['wheel_lock_sha256'] == hashlib.sha256(Path(sys.argv[3]).read_bytes()).hexdigest()
assert receipt['krt'] == m['krt']
assert receipt['grid_router']['source'] == 'owned source build'
assert receipt['grid_router']['sha256'] == m['krt']['extension_module']['sha256']
assert receipt['rustc'] == 'rustc 1.99.1 (58cf8f9c3 2026-10-13)'
assert receipt['cargo'] == 'cargo 1.99.1 (5f94df478 2026-08-27)'
print('owned engine manifest verified offline')
PYVERIFY
    [ "$(venv_versions)" = "$WHEELS" ] || fail "venv holds $(venv_versions), expected $WHEELS"
    # Every native library resolves inside this image, not only on the build host.
    local so libs
    while IFS= read -r -d '' so; do
        libs=$(ldd "$so") || fail "ldd cannot load $so"
        ! grep 'not found' <<<"$libs" || fail "unresolved shared library in $so"
    done < <(find "$VENV" -name '*.so' -type f -print0)
    (cd "$KRT" && "$VENV/bin/python" -I -B -X pycache_prefix=/dev/null -c '
import sys
sys.path.insert(0, "rust_router")
import numpy, scipy, shapely, PIL, grid_router
from scipy.optimize import linear_sum_assignment
from shapely.geometry import Polygon
from PIL import Image
assert grid_router.__version__ == "0.23.1", grid_router.__version__
print("grid_router", grid_router.__version__, "numpy", numpy.__version__, "scipy", scipy.__version__,
      "shapely", shapely.__version__, "pillow", PIL.__version__)')
    (cd "$KRT" && "$VENV/bin/python" -B -X pycache_prefix=/dev/null py_router/startup_checks.py)
    [ -z "$(GIT_OPTIONAL_LOCKS=0 "${git[@]}" -C "$ROOT" status --porcelain)" ] || fail "startup checks dirtied source"

}

case ${1:-} in
    install) install_tool ;;
    verify) verify_tool ;;
    *) fail "usage: install.sh install|verify" ;;
esac

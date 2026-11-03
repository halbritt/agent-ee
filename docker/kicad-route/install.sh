#!/usr/bin/env bash
# kicad-route and its owned routing engine at the version current when the image is
# built, fixed for every run of the image and recorded in /opt/kicad-route.install.json.
# The engine is built with Cargo --locked in the build stage. verify uses only installed
# bytes and the install record, without network.
#
# KICAD_ROUTE_COMMIT (a build ARG) selects a commit; empty means the head of kicad-route
# main, resolved now. The engine's Python wheels are the newest that satisfy the router's
# own requirements file at that commit, resolved now. Nothing here names an expected
# version: the record holds the commit, its clean-tree state, the engine version, the
# Rust toolchain banners and every wheel's version and SHA-256, and the build fails only
# when one of them is missing or cannot be read.
set -euo pipefail

ROOT=/opt/kicad-route
MANIFEST=/opt/kicad-route.install.json

KICAD_ROUTE_URL=https://github.com/halbritt/kicad-route.git
KICAD_ROUTE_BRANCH=main
KICAD_ROUTE_COMMIT=${KICAD_ROUTE_COMMIT:-}
ROUTER_ARCH=amd64
PYTHON=/usr/bin/python3
VENV=$ROOT/.venv-krt
KRT=$ROOT/src/kicad_route/router
REQUIREMENTS=$KRT/requirements.txt
RUSTC_BANNER='^rustc [0-9]+\.[0-9]+\.[0-9]+ \([0-9a-f]{7,40} [0-9]{4}-[0-9]{2}-[0-9]{2}\)$'
CARGO_BANNER='^cargo [0-9]+\.[0-9]+\.[0-9]+ \([0-9a-f]{7,40} [0-9]{4}-[0-9]{2}-[0-9]{2}\)$'

fail() { echo "kicad-route install: $*" >&2; exit 1; }

check_arch() {
    local arch
    arch=$(dpkg --print-architecture)
    [ "$arch" = "$ROUTER_ARCH" ] ||
        fail "unsupported architecture $arch: only $ROUTER_ARCH has a qualified source build"
}

# The commit to install: the ARG when given, else the head of kicad-route main now.
resolve_commit() {
    if [ -n "$KICAD_ROUTE_COMMIT" ]; then
        [[ $KICAD_ROUTE_COMMIT =~ ^[0-9a-f]{40}$ ]] || fail "KICAD_ROUTE_COMMIT '$KICAD_ROUTE_COMMIT' is not a commit"
        REQUESTED=$KICAD_ROUTE_COMMIT
        return
    fi
    local line
    line=$(git ls-remote "$KICAD_ROUTE_URL" "refs/heads/$KICAD_ROUTE_BRANCH") ||
        fail "cannot list $KICAD_ROUTE_URL"
    KICAD_ROUTE_COMMIT=${line%%[[:space:]]*}
    [[ $KICAD_ROUTE_COMMIT =~ ^[0-9a-f]{40}$ ]] ||
        fail "no commit for refs/heads/$KICAD_ROUTE_BRANCH at $KICAD_ROUTE_URL"
    REQUESTED=$KICAD_ROUTE_BRANCH
}

# A detached checkout of one exact commit, keeping real git metadata: every agent-ee
# attempt reads HEAD and git's clean-tree state and records them.
fetch_commit() {
    local url=$1 dir=$2 commit=$3
    git init -q "$dir"
    git -C "$dir" remote add origin "$url"
    git -C "$dir" fetch -q --depth 1 origin "$commit"
    git -C "$dir" -c advice.detachedHead=false checkout -q --detach "$commit"
    [ "$(git -C "$dir" rev-parse HEAD)" = "$commit" ] || fail "$dir HEAD is not $commit"
}

# The rustc and cargo banners the record holds; an unreadable banner stops the build.
read_toolchain() {
    rustc=$(rustc --version) && [[ $rustc =~ $RUSTC_BANNER ]] || fail "unreadable rustc version '$rustc'"
    cargo=$(cargo --version) && [[ $cargo =~ $CARGO_BANNER ]] || fail "unreadable cargo version '$cargo'"
}

# grid_router.__version__ as the built venv imports it; an unreadable version stops the build.
read_engine_version() {
    engine=$(cd "$KRT" && "$VENV/bin/python" -I -B -X pycache_prefix=/dev/null -c '
import sys
sys.path.insert(0, "rust_router")
import grid_router
print(getattr(grid_router, "__version__", ""))') &&
        [[ $engine =~ ^[0-9]+\.[0-9]+\.[0-9]+([.+-][0-9A-Za-z.+-]+)?$ ]] || fail "unreadable engine version '$engine'"
}

venv_versions() {
    "$VENV/bin/python" -I -B -X pycache_prefix=/dev/null -c '
import importlib.metadata as m
print(" ".join(sorted(d.metadata["Name"].lower() + "==" + d.version for d in m.distributions())))'
}

install_tool() {
    check_arch
    [ ! -e "$ROOT" ] || fail "$ROOT already exists"
    resolve_commit
    fetch_commit "$KICAD_ROUTE_URL" "$ROOT" "$KICAD_ROUTE_COMMIT"
    [ -z "$(git -C "$ROOT" status --porcelain)" ] || fail "kicad-route checkout is dirty after checkout"
    local rustc cargo engine
    read_toolchain
    [ -s "$REQUIREMENTS" ] || fail "kicad-route $KICAD_ROUTE_COMMIT has no $REQUIREMENTS"
    local tmp
    tmp=$(mktemp -d)
    # The venv uses the image's own interpreter. Its wheels are the newest binary releases
    # that satisfy the router's requirements file now; the record keeps each one's digest.
    "$PYTHON" -m venv --without-pip "$VENV"
    "$PYTHON" -m pip download --isolated --no-cache-dir --disable-pip-version-check \
        --index-url https://pypi.org/simple --only-binary=:all: -r "$REQUIREMENTS" -d "$tmp/wheels"
    "$PYTHON" -m pip --python "$VENV/bin/python" install --isolated --no-cache-dir \
        --disable-pip-version-check --no-index --find-links "$tmp/wheels" --only-binary=:all: \
        --no-compile -r "$REQUIREMENTS"
    local expected
    expected=$("$PYTHON" -I -B - "$tmp/wheels" <<'PY'
import sys
from pathlib import Path
names = []
for wheel in sorted(Path(sys.argv[1]).glob('*.whl')):
    name, version = wheel.name.split('-')[:2]
    names.append(name.lower().replace('_', '-') + '==' + version)
print(' '.join(sorted(names)))
PY
)
    [ -n "$expected" ] || fail "no wheels resolved for $REQUIREMENTS"
    [ "$(venv_versions)" = "$expected" ] || fail "venv holds $(venv_versions), resolved $expected"

    # The upstream installer builds exactly the included locked crate. There is
    # no external KRT checkout, release lookup or prebuilt binary fallback.
    "$VENV/bin/python" -I -B -X pycache_prefix=/dev/null "$KRT/build_router.py" --target-dir "$tmp/router-native"
    KICAD_ROUTE_STATE="$ROOT" "$ROOT/bin/kicad-route" state-manifest
    read_engine_version
    [ -z "$(git -C "$ROOT" status --porcelain)" ] || fail "the engine build dirtied the kicad-route checkout"
    "$PYTHON" -I -B -X pycache_prefix=/dev/null - "$tmp/wheels" "$REQUIREMENTS" > "$MANIFEST" <<PY
import hashlib, json, platform, subprocess, sys
from pathlib import Path
wheels, requirements = Path(sys.argv[1]), Path(sys.argv[2])
venv = '$VENV'
meta = json.loads(subprocess.run([venv + '/bin/python', '-I', '-B', '-c', (
    'import importlib.metadata as m, json; print(json.dumps({d.metadata["Name"].lower(): '
    '{"version": d.version, "license": d.metadata.get("License-Expression") '
    'or ((d.metadata.get("License") or "").strip().splitlines() or [""])[0][:200] or None, '
    '"license_files": sorted(d.metadata.get_all("License-File") or [])} '
    'for d in m.distributions()}))')], check=True, capture_output=True, text=True).stdout)
files = []
for path in sorted(wheels.glob('*.whl')):
    name = path.name.split('-')[0].lower().replace('_', '-')
    files.append({**meta[name], 'name': name, 'file': path.name,
                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
state = json.load(open('$ROOT/manifest.json'))
json.dump({
    'schema': 'agent-ee.kicad-route-install/2',
    'kicad_route': {'url': '$KICAD_ROUTE_URL', 'requested': '$REQUESTED', 'commit': '$KICAD_ROUTE_COMMIT',
                    'clean': True, 'path': '$ROOT', 'license': 'MIT'},
    'krt': state['krt'],
    'grid_router': {'version': '$engine', 'sha256': state['krt']['extension_module']['sha256'],
                    'source': 'owned source build'},
    'rustc': '$rustc', 'cargo': '$cargo',
    'python': {'executable': '$PYTHON', 'version': platform.python_version(), 'venv': venv},
    'requirements': {'path': str(requirements.relative_to('$ROOT')),
                     'sha256': hashlib.sha256(requirements.read_bytes()).hexdigest()},
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
    "$PYTHON" -I -B -X pycache_prefix=/dev/null - "$ROOT" "$MANIFEST" \
        "$("${git[@]}" -C "$ROOT" rev-parse HEAD)" "$(venv_versions)" "$RUSTC_BANNER" "$CARGO_BANNER" <<'PYVERIFY'
import hashlib, json, re, sys
from pathlib import Path
root, receipt, head, venv = Path(sys.argv[1]), json.load(open(sys.argv[2])), sys.argv[3], sys.argv[4]
sys.path.insert(0, str(root / 'src'))
from kicad_route import engine, statemanifest
assert receipt['schema'] == 'agent-ee.kicad-route-install/2', receipt['schema']
route = receipt['kicad_route']
assert re.fullmatch(r'[0-9a-f]{40}', route['commit']) and route['clean'] is True, route
assert head == route['commit'], f'kicad-route HEAD {head} is not the recorded {route["commit"]}'
m = statemanifest.read(root)
assert not statemanifest.schema_problems(m)
assert not statemanifest.verify(m, root)
assert m['krt']['source_sha256'] == engine.source_sha256()
module = statemanifest.extension_module(root / '.venv-krt')
assert module is not None
assert m['krt']['extension_module']['path'] == str(module.relative_to(root))
assert m['krt']['extension_module']['sha256'] == hashlib.sha256(module.read_bytes()).hexdigest()
assert receipt['krt'] == m['krt']
grid = receipt['grid_router']
assert grid['source'] == 'owned source build'
assert grid['sha256'] == m['krt']['extension_module']['sha256']
assert re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+([.+-][0-9A-Za-z.+-]+)?', grid['version']), grid
assert re.fullmatch(sys.argv[5], receipt['rustc']), receipt['rustc']
assert re.fullmatch(sys.argv[6], receipt['cargo']), receipt['cargo']
requirements = root / receipt['requirements']['path']
assert receipt['requirements']['sha256'] == hashlib.sha256(requirements.read_bytes()).hexdigest()
wheels = receipt['wheels']
assert wheels and all(re.fullmatch(r'[0-9a-f]{64}', w['sha256']) and w['version'] for w in wheels), wheels
assert venv == ' '.join(sorted(f"{w['name']}=={w['version']}" for w in wheels)), (venv, wheels)
print('kicad-route', route['commit'], 'engine', grid['version'], 'recorded and verified offline')
PYVERIFY
    [ -z "$(GIT_OPTIONAL_LOCKS=0 "${git[@]}" -C "$ROOT" status --porcelain)" ] || fail "kicad-route checkout is dirty"
    # Every native library resolves inside this image, not only on the build host.
    local so libs
    while IFS= read -r -d '' so; do
        libs=$(ldd "$so") || fail "ldd cannot load $so"
        ! grep 'not found' <<<"$libs" || fail "unresolved shared library in $so"
    done < <(find "$VENV" -name '*.so' -type f -print0)
    # The recorded engine version is the one that imports here, with every dependency.
    (cd "$KRT" && "$VENV/bin/python" -I -B -X pycache_prefix=/dev/null -c '
import json, sys
sys.path.insert(0, "rust_router")
import numpy, scipy, shapely, PIL, grid_router
from scipy.optimize import linear_sum_assignment
from shapely.geometry import Polygon
from PIL import Image
recorded = json.load(open(sys.argv[1]))["grid_router"]["version"]
assert grid_router.__version__ == recorded, (grid_router.__version__, recorded)
print("grid_router", grid_router.__version__, "numpy", numpy.__version__, "scipy", scipy.__version__,
      "shapely", shapely.__version__, "pillow", PIL.__version__)' "$MANIFEST")
    (cd "$KRT" && "$VENV/bin/python" -B -X pycache_prefix=/dev/null py_router/startup_checks.py)
    [ -z "$(GIT_OPTIONAL_LOCKS=0 "${git[@]}" -C "$ROOT" status --porcelain)" ] || fail "startup checks dirtied source"

}

# Run as a command (`install.sh install|verify`); sourced, it only defines the functions, so the
# version refusals can be exercised on their own.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    case ${1:-} in
        install) install_tool ;;
        verify) verify_tool ;;
        *) fail "usage: install.sh install|verify" ;;
    esac
fi

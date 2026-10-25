#!/usr/bin/env bash
# Run the captured workflow without installing host tools or fetching design inputs.
set -euo pipefail
usage() {
    printf 'Usage: %s --image sha256:ID --inputs-root DIR --out NEW_DIR [--full]\n' "$0" >&2
    exit 2
}
image= inputs_root= output= full=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --image) [[ $# -ge 2 ]] || usage; image=$2; shift 2 ;;
        --inputs-root) [[ $# -ge 2 ]] || usage; inputs_root=$2; shift 2 ;;
        --out) [[ $# -ge 2 ]] || usage; output=$2; shift 2 ;;
        --full) full=(--full); shift ;;
        *) usage ;;
    esac
done
[[ "$image" =~ ^sha256:[0-9a-f]{64}$ && -n "$inputs_root" && -n "$output" ]] || usage
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
inputs_root=$(realpath -e -- "$inputs_root")
[[ ! -e "$output" && ! -L "$output" ]] || { printf 'Output already exists: %s\n' "$output" >&2; exit 1; }
docker image inspect "$image" >/dev/null
mkdir -p -- "$(dirname -- "$output")"
mkdir -- "$output"
output=$(realpath -e -- "$output")
docker image inspect "$image" > "$output/image-inspect.json"
printf '%s\n' "$image" > "$output/image-id.txt"
git -C "$root" rev-parse HEAD > "$output/source-revision.txt"
git -C "$root" status --porcelain > "$output/source-status.txt"
# This path intentionally does not alter host AppArmor, sysctls, installed tools or subscriptions.
docker run --rm --pull never --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
    --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
    --security-opt systempaths=unconfined \
    --mount "type=bind,src=$root,dst=/opt/agent-ee,readonly" \
    --mount "type=bind,src=$inputs_root,dst=/retained,readonly" \
    --mount "type=bind,src=$output,dst=/output" \
    --workdir /opt/agent-ee "$image" /bin/bash -eu -c '
        /usr/bin/python3 scripts/recovery_runtime.py prepare --inputs-root /retained --out /output/prepared
        /usr/bin/python3 scripts/recovery_runtime.py run --prepared /output/prepared --out /output/run "$@"
    ' recovery-runtime "${full[@]}"

#!/usr/bin/env bash
set -euo pipefail
agent_ee_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
migrate_uv=false
case "${1:-}" in
    '') ;;
    --migrate-uv-launcher) migrate_uv=true; shift ;;
    *) printf 'Usage: %s [--migrate-uv-launcher]\n' "$0" >&2; exit 2 ;;
esac
[[ $# == 0 ]] || { printf 'Unexpected arguments\n' >&2; exit 2; }
install_link() {
    local source=$1 destination=$2
    mkdir -p -- "$(dirname -- "$destination")"
    if [[ -L "$destination" && $(readlink -f -- "$destination") == "$source" ]]; then
        return
    fi
    if [[ -e "$destination" || -L "$destination" ]]; then
        local uv_launcher="$HOME/.local/share/uv/tools/agent-ee/bin/agent-ee"
        if [[ "$destination" == "$HOME/.local/bin/agent-ee" && -L "$destination" \
                && $(readlink -f -- "$destination") == "$uv_launcher" && -f "$uv_launcher" ]] \
                && grep -qx 'from agent_ee.cli import main' "$uv_launcher"; then
            if ! "$migrate_uv"; then
                printf 'Recognized uv agent-ee launcher. Inspect it, then rerun with --migrate-uv-launcher to preserve it as %s.uv-backup and use system Python.\n' "$destination" >&2
                exit 1
            fi
            if [[ -e "$destination.uv-backup" || -L "$destination.uv-backup" ]]; then
                printf 'Refusing to replace backup: %s.uv-backup\n' "$destination" >&2
                exit 1
            fi
            mv -- "$destination" "$destination.uv-backup"
            ln -s -- "$source" "$destination"
            return
        fi
        printf 'Refusing to replace existing path: %s\n' "$destination" >&2
        exit 1
    fi
    ln -s -- "$source" "$destination"
}
install_link "$agent_ee_root/bin/agent-ee" "$HOME/.local/bin/agent-ee"
install_link "$agent_ee_root/skills/agent-ee" "$HOME/.agents/skills/agent-ee"
install_link "$agent_ee_root/skills/agent-ee" "${CODEX_HOME:-$HOME/.codex}/skills/agent-ee"
install_link "$agent_ee_root/skills/agent-ee" "$HOME/.claude/skills/agent-ee"
printf 'Installed agent-ee CLI and discovery links. Run: agent-ee doctor\n'
if [[ ! -x /usr/local/libexec/agent-ee/agent-ee-placement ]]; then
    printf 'Placement requires the Rust planner in /usr/local/libexec/agent-ee/.\n'
    printf 'Build and install it as documented in %s/docs/installation.md#rust-placement-engine\n' "$agent_ee_root"
fi

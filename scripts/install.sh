#!/usr/bin/env bash
set -euo pipefail
agent_ee_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
install_link() {
    local source=$1 destination=$2
    mkdir -p -- "$(dirname -- "$destination")"
    if [[ -L "$destination" && $(readlink -f -- "$destination") == "$source" ]]; then
        return
    fi
    if [[ -e "$destination" || -L "$destination" ]]; then
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

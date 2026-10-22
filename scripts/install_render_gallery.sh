#!/usr/bin/env bash
set -euo pipefail
# Run from a verified checkout. Existing artifacts are retained on upgrade.
repo=$(cd "$(dirname "$0")/.." && pwd)
bind=${1:?Usage: install_render_gallery.sh TAILSCALE_IPV4}
python3 -c 'import ipaddress,sys; a=ipaddress.ip_address(sys.argv[1]); assert a in ipaddress.ip_network("100.64.0.0/10")' "$bind"
base="$HOME/.local/share/agent-ee-render-gallery"
mkdir -p "$base/artifacts" "$HOME/.config/agent-ee-render-gallery" "$HOME/.config/systemd/user"
release=$(mktemp -d "$base/app-release-XXXXXXXX")
cp -a "$repo/src/agent_ee" "$release/"
# Atomic code activation; systemd restart loads the new verified copy.
ln -s "$release" "$base/app.next"
mv -Tf "$base/app.next" "$base/app"
printf 'GALLERY_BIND=%s\n' "$bind" > "$HOME/.config/agent-ee-render-gallery/environment"
cp "$repo/deployment/agent-ee-render-gallery.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now agent-ee-render-gallery.service
systemctl --user restart agent-ee-render-gallery.service

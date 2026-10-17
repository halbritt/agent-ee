#!/usr/bin/env bash
# Print the Docker AppArmor profile after preparing the local Linux host.
set -euo pipefail
if [[ ! -e /sys/module/apparmor/parameters/enabled ]] ||
   [[ $(cat /sys/module/apparmor/parameters/enabled) != Y ]]; then
  echo unconfined
  exit 0
fi
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# This named profile changes neither the global userns sysctl nor other profiles.
sudo -n apparmor_parser -r "$root/docker/agent-ee.apparmor"
echo agent-ee-container
